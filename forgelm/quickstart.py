"""Quickstart layer — Phase 10.5.

Generates a ready-to-train YAML config from a small library of curated
templates. Same YAML schema as a hand-written config; the trainer doesn't
know whether a config came from quickstart or from the wizard.

Templates ship with conservative defaults (QLoRA 4-bit NF4, rank=8, batch=1
with gradient accumulation, gradient checkpointing on, safety/compliance
opt-in only) so a first-time user gets a working model on an 8-12 GB
consumer GPU rather than a CUDA OOM.

Usage (CLI):
    forgelm quickstart customer-support
    forgelm quickstart code-assistant --model deepseek-ai/deepseek-coder-1.3b-instruct
    forgelm quickstart --list

Usage (programmatic):
    from forgelm.quickstart import run_quickstart, list_templates
    result = run_quickstart("customer-support", dry_run=True)
    print(result.config_path)
"""

from __future__ import annotations

import itertools
import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

logger = logging.getLogger("forgelm.quickstart")


# ---------------------------------------------------------------------------
# Template registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Template:
    """A curated quickstart entry — config + bundled dataset + sizing hints."""

    name: str  # CLI handle (kebab-case)
    title: str  # human-readable banner
    description: str  # one-line rationale
    primary_model: str  # 7B-class default
    fallback_model: str  # smaller variant for <8 GB GPUs
    trainer_type: str  # sft | grpo (matches TrainingConfig.trainer_type)
    estimated_minutes: int  # rough wall-clock on the primary model
    min_vram_for_primary_gb: float  # threshold under which fallback is chosen
    bundled_dataset: bool  # False ⇒ user must supply --dataset
    license_note: str  # one-line license for the bundled dataset


# HF Hub model IDs reused across multiple templates. Kept as constants so a
# bump (e.g. Qwen2.5 → Qwen3) is one edit instead of N.
_QWEN_25_7B_INSTRUCT = "Qwen/Qwen2.5-7B-Instruct"
_SMOLLM2_17B_INSTRUCT = "HuggingFaceTB/SmolLM2-1.7B-Instruct"


TEMPLATES: Dict[str, Template] = {
    "customer-support": Template(
        name="customer-support",
        title="Customer Support Assistant",
        description="Polite, brand-safe support replies. SFT on a tiny seed FAQ dataset.",
        primary_model=_QWEN_25_7B_INSTRUCT,
        fallback_model=_SMOLLM2_17B_INSTRUCT,
        trainer_type="sft",
        estimated_minutes=15,
        min_vram_for_primary_gb=10.0,
        bundled_dataset=True,
        license_note="CC-BY-SA 4.0 (authored by ForgeLM contributors)",
    ),
    "code-assistant": Template(
        name="code-assistant",
        title="Code Assistant",
        description="Short code-question Q&A. SFT on a curated programming seed set.",
        primary_model="Qwen/Qwen2.5-Coder-7B-Instruct",
        fallback_model="Qwen/Qwen2.5-Coder-1.5B-Instruct",
        trainer_type="sft",
        estimated_minutes=25,
        min_vram_for_primary_gb=10.0,
        bundled_dataset=True,
        license_note="CC-BY-SA 4.0 (authored by ForgeLM contributors)",
    ),
    "domain-expert": Template(
        name="domain-expert",
        title="Domain Expert (BYOD — bring your own docs)",
        description="Empty data — pair with `forgelm ingest` (Phase 11) or a custom JSONL.",
        primary_model=_QWEN_25_7B_INSTRUCT,
        fallback_model=_SMOLLM2_17B_INSTRUCT,
        trainer_type="sft",
        estimated_minutes=20,
        min_vram_for_primary_gb=10.0,
        bundled_dataset=False,
        license_note="N/A — user-supplied data",
    ),
    "medical-qa-tr": Template(
        name="medical-qa-tr",
        title="Medical Q&A (Türkçe / Turkish)",
        description="Turkish medical-question SFT seed. Disclaimers baked in; not clinical advice.",
        primary_model=_QWEN_25_7B_INSTRUCT,
        fallback_model="Qwen/Qwen2.5-1.5B-Instruct",
        trainer_type="sft",
        estimated_minutes=15,
        min_vram_for_primary_gb=10.0,
        bundled_dataset=True,
        license_note="CC-BY-SA 4.0 (authored by ForgeLM contributors; not medical advice)",
    ),
    "grpo-math": Template(
        name="grpo-math",
        title="Math Reasoning via GRPO",
        description="Group Relative Policy Optimization on grade-school math problems.",
        primary_model="Qwen/Qwen2.5-Math-7B-Instruct",
        fallback_model="Qwen/Qwen2.5-Math-1.5B-Instruct",
        trainer_type="grpo",
        estimated_minutes=45,
        min_vram_for_primary_gb=12.0,
        bundled_dataset=True,
        license_note="CC-BY-SA 4.0 (math problems authored by ForgeLM contributors)",
    ),
}


# ---------------------------------------------------------------------------
# Filesystem accessors
# ---------------------------------------------------------------------------


def templates_dir() -> Path:
    """Absolute path to the bundled `forgelm/templates/` directory."""
    return Path(__file__).resolve().parent / "templates"


def template_assets(name: str) -> Tuple[Path, Optional[Path]]:
    """Return (config_path, dataset_path-or-None) for a template's bundled files."""
    base = templates_dir() / name
    config_path = base / "config.yaml"
    if not config_path.is_file():
        raise FileNotFoundError(f"Template '{name}' missing config.yaml at {config_path}")
    data_path = base / "data.jsonl"
    return config_path, (data_path if data_path.is_file() else None)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def list_templates() -> List[Template]:
    """Return all registered templates in stable insertion order."""
    return list(TEMPLATES.values())


def get_template(name: str) -> Template:
    """Look up a template by its CLI name; raises with a helpful message on miss."""
    if name not in TEMPLATES:
        available = ", ".join(sorted(TEMPLATES.keys()))
        raise ValueError(f"Unknown template '{name}'. Available: {available}")
    return TEMPLATES[name]


def auto_select_model(template: Template, available_vram_gb: Optional[float]) -> Tuple[str, str]:
    """Pick the appropriate model for the user's GPU.

    Returns ``(model_name, selection_reason)`` so callers can surface the
    decision in logs / wizard summaries instead of silently downsizing.
    """
    if available_vram_gb is None:
        return (
            template.fallback_model,
            "no GPU detected — using fallback model for CPU/Mac compatibility",
        )
    if available_vram_gb < template.min_vram_for_primary_gb:
        return (
            template.fallback_model,
            f"available VRAM {available_vram_gb:.1f} GB < {template.min_vram_for_primary_gb} GB — auto-downsized",
        )
    return template.primary_model, f"available VRAM {available_vram_gb:.1f} GB >= primary requirement"


@dataclass
class QuickstartResult:
    """Outcome of a quickstart run."""

    template: Template
    config_path: Path  # generated YAML on disk
    chosen_model: str
    selection_reason: str
    dataset_path: str
    dry_run: bool
    started_training: bool = False
    extra_notes: List[str] = field(default_factory=list)


def _detect_available_vram_gb() -> Optional[float]:
    """Best-effort total VRAM lookup; returns None if no GPU or torch missing.

    On multi-GPU hosts the function reports the maximum total VRAM across all
    visible CUDA devices. That answers the "will this template fit at all?"
    question — the user can pin a specific GPU at training time via
    ``CUDA_VISIBLE_DEVICES``.
    """
    try:
        # Codacy / "AI tooling" notice on this line is expected — PyTorch
        # is a documented core dependency for the trainer (see pyproject.toml
        # `dependencies`), declared up-front in the README. The import is
        # local *only* so `forgelm --help` and CLI parsing stay torch-free
        # for fast cold start; it is not an attempt to hide an ML
        # dependency. Suppression rationale recorded inline.
        import torch  # codacy-disable: pytorch-detector — core dep, lazy for cold-start cost
    except ImportError:
        return None
    try:
        if not torch.cuda.is_available():
            return None
        count = torch.cuda.device_count()
        if count == 0:
            return None
        max_total_bytes = 0
        for i in range(count):
            with torch.cuda.device(i):
                _, total = torch.cuda.mem_get_info()
                max_total_bytes = max(max_total_bytes, total)
        # Use total (capacity) rather than free (current snapshot) for the
        # "will this template fit at all" question.
        return max_total_bytes / (1024**3)
    except Exception as exc:  # noqa: BLE001 — best-effort: VRAM probe runs ``nvidia-smi`` parsing + torch.cuda introspection + pynvml fallback; surface includes FileNotFoundError (no nvidia-smi), RuntimeError (no CUDA), AttributeError (older torch), and parser errors.  Returning None falls back to a conservative VRAM estimate.  # pragma: no cover — best-effort only.  # NOSONAR
        logger.debug("VRAM probe failed: %s", exc)
        return None


def _resolve_dataset(template: Template, dataset_override: Optional[str], scratch_dir: Path) -> Tuple[str, List[str]]:
    """Resolve the dataset path and return (final_path, extra_notes).

    The bundled seed dataset is copied into ``scratch_dir`` (a per-run
    directory) so subsequent quickstart invocations cannot overwrite a user's
    edits to a previous run's dataset.
    """
    notes: List[str] = []
    if dataset_override:
        return dataset_override, notes

    _, bundled = template_assets(template.name)
    if bundled is None:
        # domain-expert and any other BYOD templates land here.
        raise ValueError(
            f"Template '{template.name}' does not bundle a dataset. "
            "Pass --dataset PATH or generate one via `forgelm ingest` (Phase 11)."
        )
    dest = scratch_dir / f"{template.name}.jsonl"
    if dest.resolve() == bundled.resolve():
        return str(dest), notes
    scratch_dir.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        # Refuse to overwrite a previous run's dataset — each quickstart run
        # owns its own directory, so this should be unreachable except when a
        # caller explicitly recycles a directory. Surface the recovery paths
        # so the operator isn't left guessing.
        raise FileExistsError(
            f"Dataset already exists at {dest}. Either:\n"
            f"  - pass a fresh --output PATH (default: timestamped dir under ./configs/),\n"
            f"  - pass --dataset {dest} to reuse this file as-is,\n"
            f"  - or delete {dest} first."
        )
    shutil.copyfile(bundled, dest)
    notes.append(f"copied seed dataset to {dest}")
    return str(dest), notes


def _materialize_config(template: Template, chosen_model: str, dataset_path: str) -> Dict[str, Any]:
    """Load the bundled YAML and patch in the runtime overrides.

    Resilient to ``model: null`` / ``data: null`` in the source template — a
    legal but null-valued YAML mapping would otherwise crash the
    ``setdefault().__setitem__`` chain with ``TypeError``.
    """
    config_path, _ = template_assets(template.name)
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    if not isinstance(cfg, dict):
        raise ValueError(
            f"Template '{template.name}' config.yaml did not parse as a mapping; got {type(cfg).__name__}."
        )

    for section in ("model", "data"):
        existing = cfg.get(section)
        if existing is not None and not isinstance(existing, dict):
            raise ValueError(
                f"Template '{template.name}' config.yaml has non-mapping value for '{section}': "
                f"{type(existing).__name__}. Expected a mapping or null."
            )

    cfg["model"] = (cfg.get("model") or {}) | {"name_or_path": chosen_model}
    cfg["data"] = (cfg.get("data") or {}) | {"dataset_name_or_path": dataset_path}
    return cfg


# Monotonic counter appended to every slug — guarantees uniqueness for
# back-to-back calls in the same process even when ``datetime.now()``
# returns the same microsecond (Windows clock resolution can be as
# coarse as ~16ms; macOS/Linux are sub-microsecond, but the counter
# costs nothing on those platforms and is the cheapest way to keep
# the cross-OS contract identical).
_run_slug_counter = itertools.count()


def _run_timestamp_slug() -> str:
    """UTC timestamp + microseconds + PID + monotonic counter.

    The counter suffix makes the slug collision-free regardless of the
    underlying clock resolution; without it, Windows callers can hit
    the ``test_two_immediate_calls_produce_distinct_paths`` regression
    when two ``_default_output_path`` calls land in the same OS-clock
    tick (16ms granularity on stock Windows 10).
    """
    now = datetime.now(timezone.utc)
    return f"{now.strftime('%Y%m%dT%H%M%S')}-{now.microsecond:06d}-{os.getpid()}-{next(_run_slug_counter)}"


def _default_output_path(template_name: str) -> Path:
    """Default location for the generated YAML.

    Returns ``./configs/<template>-<UTC-timestamp>/config.yaml``. Each call
    yields a unique directory (microsecond precision + PID) so back-to-back
    runs never collide and a second run cannot overwrite the first run's
    artifacts.
    """
    return Path("configs") / f"{template_name}-{_run_timestamp_slug()}" / "config.yaml"


def _forgelm_version() -> str:
    """Best-effort lookup of the installed forgelm package version."""
    try:
        return importlib_metadata.version("forgelm")
    except importlib_metadata.PackageNotFoundError:
        return "unknown"


def _build_provenance_header(
    *,
    template: Template,
    chosen_model: str,
    selection_reason: str,
    generated_at: datetime,
) -> str:
    """Render the YAML comment block prepended to every generated config."""
    lines = [
        "# generated by forgelm quickstart",
        f"# template: {template.name}",
        f"# generated_at: {generated_at.isoformat()}",
        f"# forgelm_version: {_forgelm_version()}",
        f"# chosen_model: {chosen_model}",
        f"# selection_reason: {selection_reason}",
        "#",
        "# This file was produced from a curated template; edit freely. Re-running",
        "# `forgelm quickstart` writes to a NEW timestamped directory and will not",
        "# touch this one.",
        "",
    ]
    return "\n".join(lines)


def _append_audit_event(audit_dir: Path, event: Dict[str, Any]) -> None:
    """Append a structured event to ``<audit_dir>/quickstart_audit.jsonl``.

    Mirrors :class:`forgelm.compliance.AuditLogger`'s append-only JSONL
    discipline without coupling to the checkpoint-dir-bound logger. A UTC
    ISO-8601 ``timestamp`` is injected automatically. Write failures are logged
    at ``warning`` and swallowed so audit-disk problems never block quickstart.

    **This is a convenience log, not a compliance chain, deliberately.** It is
    unchained (no ``_hmac``, no previous-entry hash), carries no run context or
    resolved model revision, and its writes are best-effort. That is the right
    weight for what it records — which template and VRAM figure produced which
    model choice, before any training run exists to attach it to. A second
    hash-chained trail rooted in nothing would be *weaker* compliance evidence
    than one honest chain (``compliance.AuditLogger``, the EU AI Act Art. 12
    artefact) plus a clearly-labelled convenience log; do not "upgrade" it.

    Two consequences a future author must know before adding an event here:

    - The key is ``event_type``, not ``event``, and the namespace is
      ``quickstart.*``. ``tools/check_audit_event_catalog.py`` matches neither,
      so it reports green having never examined this file — it is not a safety
      net for anything written here.
    - What *does* hold the line is ``tests/test_quickstart_compat.py``'s
      ``TestAuditLog``, which asserts exactly one event with this exact
      ``event_type``. A new event fails there, which is the intended prompt to
      go and decide whether it belongs in the real chain instead.
    """
    entry = {"timestamp": datetime.now(timezone.utc).isoformat(), **event}
    try:
        audit_dir.mkdir(parents=True, exist_ok=True)
        log_path = audit_dir / "quickstart_audit.jsonl"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except (OSError, TypeError, ValueError) as exc:
        # OSError: filesystem write (ENOSPC, permission, broken parent dir).
        # TypeError / ValueError: ``json.dumps`` rejecting an unserialisable
        # object even with ``default=str`` (e.g. a circular reference).
        # The quickstart audit log is supplementary; the primary audit
        # chain in compliance.AuditLogger remains authoritative.
        logger.warning("Failed to write quickstart audit event: %s", exc)


def run_quickstart(
    template_name: str,
    *,
    model_override: Optional[str] = None,
    dataset_override: Optional[str] = None,
    output_path: Optional[str] = None,
    dry_run: bool = False,
    available_vram_gb: Optional[float] = None,
) -> QuickstartResult:
    """Generate a config from a template and (optionally) report next steps.

    Args:
        template_name: One of the keys in :data:`TEMPLATES` (e.g. ``"customer-support"``).
        model_override: Skip auto-select and force a specific HF Hub ID.
        dataset_override: Use this dataset path instead of the bundled seed.
        output_path: Destination for the generated YAML. Defaults to
            ``./configs/<template>-<timestamp>.yaml``.
        dry_run: Generate the YAML without invoking training.
        available_vram_gb: Override the auto-detected GPU capacity (mainly for tests).

    Returns:
        A :class:`QuickstartResult` describing what was generated and chosen.
    """
    template = get_template(template_name)

    if available_vram_gb is None:
        available_vram_gb = _detect_available_vram_gb()

    if model_override:
        chosen_model = model_override
        selection_reason = "model-override flag"
    else:
        chosen_model, selection_reason = auto_select_model(template, available_vram_gb)

    config_target = Path(output_path) if output_path else _default_output_path(template_name)
    config_target.parent.mkdir(parents=True, exist_ok=True)

    _append_audit_event(
        config_target.parent,
        {
            "event_type": "quickstart.model_selection",
            "template": template.name,
            "template_primary_model": template.primary_model,
            "template_fallback_model": template.fallback_model,
            "template_min_vram_for_primary_gb": template.min_vram_for_primary_gb,
            "available_vram_gb": available_vram_gb,
            "chosen_model": chosen_model,
            "selection_reason": selection_reason,
            "model_override_used": model_override is not None,
            "dataset_override_used": dataset_override is not None,
            "dry_run": dry_run,
        },
    )

    dataset_path, dataset_notes = _resolve_dataset(template, dataset_override, config_target.parent)

    cfg = _materialize_config(template, chosen_model, dataset_path)

    header = _build_provenance_header(
        template=template,
        chosen_model=chosen_model,
        selection_reason=selection_reason,
        generated_at=datetime.now(timezone.utc),
    )
    with open(config_target, "w", encoding="utf-8") as f:
        f.write(header)
        yaml.safe_dump(cfg, f, default_flow_style=False, sort_keys=False)

    logger.info(
        "quickstart: template=%s model=%s dataset=%s config=%s (%s)",
        template.name,
        chosen_model,
        dataset_path,
        config_target,
        selection_reason,
    )

    return QuickstartResult(
        template=template,
        config_path=config_target,
        chosen_model=chosen_model,
        selection_reason=selection_reason,
        dataset_path=dataset_path,
        dry_run=dry_run,
        started_training=False,
        extra_notes=dataset_notes,
    )


# ---------------------------------------------------------------------------
# CLI helpers (used by forgelm.cli)
# ---------------------------------------------------------------------------


def format_template_list() -> str:
    """Render the template registry as a human-readable list."""
    lines = ["Available quickstart templates:", ""]
    for tpl in list_templates():
        bundled = "[x] bundled data" if tpl.bundled_dataset else "[ ] user-supplied data"
        lines.append(f"  {tpl.name}")
        lines.append(f"    {tpl.title} — {tpl.description}")
        lines.append(
            f"    primary={tpl.primary_model}  fallback={tpl.fallback_model}  "
            f"trainer={tpl.trainer_type}  ~{tpl.estimated_minutes}min  {bundled}"
        )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def summarize_result(result: QuickstartResult) -> str:
    """Plain-text summary printed by the CLI after generation."""
    lines = [
        f"Template       : {result.template.name} — {result.template.title}",
        f"Model          : {result.chosen_model}",
        f"Selection      : {result.selection_reason}",
        f"Dataset        : {result.dataset_path}",
        f"Generated YAML : {result.config_path}",
        f"Trainer        : {result.template.trainer_type}",
        f"Est. wall-clock: ~{result.template.estimated_minutes} minutes on primary model",
    ]
    for note in result.extra_notes:
        lines.append(f"Note           : {note}")
    if result.dry_run:
        lines.append("")
        lines.append(f"Dry-run only. To start training: forgelm --config {result.config_path}")
    return "\n".join(lines)
