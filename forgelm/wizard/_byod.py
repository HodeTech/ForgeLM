"""BYOD (Bring Your Own Dataset) helpers — Phase 11.5 / 12.5.

These helpers are CLI's "win" over the web wizard: a typed directory
of raw documents triggers inline ingestion (Phase 11.5), and a typed
JSONL triggers an inline audit (Phase 12.5).  Phase 22 didn't touch
the BYOD logic — the helpers are well-tested in
``tests/test_wizard_byod.py`` and ``tests/test_wizard_phase11.py``.
"""

from __future__ import annotations

import json
import logging
import shlex
from pathlib import Path
from typing import List, Optional

from ._io import _CANCEL_TOKENS, _HF_HUB_ID_RE, WizardCancel, _print, _prompt, _prompt_yes_no

logger = logging.getLogger("forgelm.wizard")

# ---------------------------------------------------------------------------
# Sentinels + thresholds
# ---------------------------------------------------------------------------


_BYOD_LOCAL_NOT_FOUND = object()
"""Sentinel for ``_validate_local_jsonl``: file does not exist on disk.

Distinct from ``None`` (file exists but is not valid JSONL) so the
caller can fall back to an HF Hub-ID interpretation only when the
local path isn't there at all.
"""


_INGEST_SUPPORTED_EXTENSIONS = (".pdf", ".docx", ".epub", ".txt", ".md")
"""Extensions that the wizard's "ingest first" prompt knows how to convert.

Kept in sync with :data:`forgelm.ingestion.SUPPORTED_EXTENSIONS` —
duplicated here so the wizard's directory-scan check doesn't pay the
ingestion module's import cost when the user is bringing a JSONL
straight in.
"""


_AUDIT_LARGE_FILE_THRESHOLD_BYTES: int = 100 * 1024 * 1024  # 100 MB


# ---------------------------------------------------------------------------
# Directory-of-docs ingestion (Phase 11.5)
# ---------------------------------------------------------------------------


def _directory_has_ingestible_files(directory: Path, recursive: bool = True) -> bool:
    """True if ``directory`` contains at least one file the ingester can read."""
    pattern = "**/*" if recursive else "*"
    for entry in directory.glob(pattern):
        if entry.is_file() and entry.suffix.lower() in _INGEST_SUPPORTED_EXTENSIONS:
            return True
    return False


def _offer_ingest_for_directory(directory: Path) -> Optional[str]:
    """Phase 11.5 wizard hook: convert a directory of raw docs into JSONL inline.

    Returns the absolute path to the produced JSONL on success, or
    ``None`` when the operator declines / cancels / ingestion fails
    (the caller treats ``None`` as "re-prompt for a different dataset
    path").
    """
    resolved = directory.expanduser().resolve()
    if not _directory_has_ingestible_files(resolved):
        _print(
            f"  '{resolved}' is a directory, but it doesn't contain any "
            f"{', '.join(_INGEST_SUPPORTED_EXTENSIONS)} files. "
            "Pass a JSONL file or a directory with ingestible documents."
        )
        return None

    if not _prompt_yes_no(
        f"\n  '{resolved}' is a directory of raw documents. Run ingestion now and use the resulting JSONL?",
        default=True,
    ):
        _print(
            "  Skipped — to ingest manually:\n"
            f"      forgelm ingest {shlex.quote(str(resolved))} --recursive --output data/from_docs.jsonl\n"
            "  Then re-run the wizard with the resulting JSONL path."
        )
        return None

    out_dir = (resolved.parent / "data").resolve()
    default_out = out_dir / f"{resolved.name}_ingested.jsonl"
    out_path_raw = _prompt("Output JSONL path", str(default_out))
    out_path = Path(out_path_raw).expanduser().resolve()

    pii_mask = _prompt_yes_no(
        "Mask detected PII (emails, phones, IDs) before writing? Recommended for shared corpora.",
        default=True,
    )

    try:
        from ..ingestion import ingest_path
    except ImportError as exc:  # pragma: no cover — extras-skip path
        _print(f"  ingestion subsystem unavailable: {exc}")
        return None

    _print(f"\n  Running ingest on '{resolved}' (this may take a moment for large corpora)…")
    try:
        result = ingest_path(
            str(resolved),
            output_path=str(out_path),
            recursive=True,
            pii_mask=pii_mask,
        )
    except (FileNotFoundError, ValueError) as exc:
        _print(f"  Ingest failed: {exc}")
        return None
    except OSError as exc:
        # PermissionError / IsADirectoryError both subclass OSError — the
        # bare OSError catch handles every filesystem-level failure mode.
        _print(f"  Ingest failed due to filesystem error: {exc} — check permissions or output path.")
        return None
    except ImportError as exc:
        _print(
            f"  Ingest needs the optional 'ingestion' extra: {exc}\n  Install with: pip install 'forgelm[ingestion]'"
        )
        return None

    if result.chunk_count == 0:
        _print(
            "  Ingestion produced 0 chunks — the directory had no extractable text. "
            "Pass a JSONL file or a directory with text-bearing documents."
        )
        return None

    _print(f"  Ingest complete: {result.chunk_count} chunk(s) from {result.files_processed} file(s) → {out_path}")
    _offer_audit_for_jsonl(out_path)
    return str(out_path)


# ---------------------------------------------------------------------------
# JSONL audit (Phase 12.5)
# ---------------------------------------------------------------------------


def _offer_audit_for_jsonl(jsonl_path: Path) -> bool:
    """Phase 12.5 wizard hook: optionally audit a JSONL the user just selected."""
    try:
        size_bytes = jsonl_path.stat().st_size
    except OSError:
        size_bytes = 0
    if size_bytes > _AUDIT_LARGE_FILE_THRESHOLD_BYTES:
        size_mb = size_bytes / (1024 * 1024)
        prompt = (
            f"\n  Run a quality + governance audit on '{jsonl_path}' "
            f"({size_mb:.0f} MB) before training? Scan is CPU-only and "
            "streams the file; runtime depends on size (≈ 8–15 MB/s of "
            "JSONL on a single CPU)."
        )
    else:
        prompt = (
            f"\n  Run a quality + governance audit on '{jsonl_path}' "
            "before training? (length stats, language, near-duplicates, "
            "PII, secrets — CPU-only, runtime depends on dataset size)"
        )
    if not _prompt_yes_no(prompt, default=True):
        _print("  Skipped — audit can be run later via:")
        _print(f"      forgelm audit {shlex.quote(str(jsonl_path))}")
        return False

    try:
        from ..data_audit import audit_dataset, summarize_report
    except ImportError as exc:  # pragma: no cover — extras-skip path
        _print(f"  audit subsystem unavailable: {exc}")
        return False

    _print(f"\n  Running audit on '{jsonl_path}'…")
    try:
        report = audit_dataset(str(jsonl_path))
    except (OSError, ValueError) as exc:
        # FileNotFoundError subclasses OSError — bare OSError covers it.
        _print(f"  Audit could not run: {exc}")
        return False
    except ImportError as exc:
        _print(f"  Audit could not run (missing optional dep): {exc}")
        return False
    except Exception as exc:  # noqa: BLE001 — best-effort inline audit; audit_dataset crosses the data_audit subsystem which raises a wide tail (streaming/parse/optional-dep errors) and the offer is advisory — a failure must not abort BYOD selection. # NOSONAR python:S5754
        logger.warning("Inline audit on %s could not run: %s", jsonl_path, exc)
        _print(f"  Audit could not run: {exc}")
        return False

    _print("\n  Audit complete. Summary:")
    _print(summarize_report(report, verbose=False))
    return True


# ---------------------------------------------------------------------------
# Dataset-path collection that triggers the BYOD helpers
# ---------------------------------------------------------------------------


def _prompt_dataset_path_with_ingest_offer(question: str) -> str:
    """Prompt for a dataset path; auto-offer ingestion when the user gives a directory.

    Validates that the typed value is one of:
    1. A directory containing ingestible documents (triggers Phase 11.5
       inline ingest).
    2. A local JSONL / JSON file (triggers the Phase 12.5 audit offer).
    3. A HuggingFace Hub dataset ID (``<org>/<name>``).

    Anything else re-prompts.  Without this validation a typo silently
    becomes ``data.dataset_name_or_path`` and surfaces only at training
    time as a confusing HF resolver error.
    """
    while True:
        raw = _prompt(question, "").strip()
        if not raw:
            _print("  A dataset reference is required.")
            continue
        candidate = Path(raw).expanduser()
        if candidate.is_dir():
            ingested = _offer_ingest_for_directory(candidate)
            if ingested is None:
                continue
            return ingested
        if candidate.is_file():
            if candidate.suffix.lower() in (".jsonl", ".json"):
                _offer_audit_for_jsonl(candidate.resolve())
                return str(candidate.resolve())
            _print(
                f"  '{candidate}' is not a JSONL / JSON file. "
                "Provide a JSONL dataset, a directory of raw documents, or an HF Hub ID."
            )
            continue
        if _HF_HUB_ID_RE.match(raw):
            _print(f"  Treating '{raw}' as an HF Hub dataset ID (no local validation).")
            return raw
        _print(f"  '{raw}' is not a local file/directory and is not a valid HF Hub ID (expected '<org>/<name>').")


def _validate_local_jsonl(raw_path: str):
    """Validate a user-supplied JSONL path."""
    resolved = Path(raw_path).expanduser()
    if resolved.is_dir():
        ingested = _offer_ingest_for_directory(resolved)
        if ingested is None:
            return None
        return ingested
    if not resolved.is_file():
        return _BYOD_LOCAL_NOT_FOUND
    try:
        with open(resolved, "r", encoding="utf-8") as fh:
            first_line = next((line for line in fh if line.strip()), "")
        if not first_line:
            raise ValueError("file is empty")
        json.loads(first_line)
    except (OSError, ValueError) as e:
        _print(f"  File is not valid JSONL (first line failed to parse): {e}")
        return None
    _offer_audit_for_jsonl(resolved.resolve())
    return str(resolved.resolve())


def _resolve_byod_dataset_path() -> Optional[str]:
    """Prompt the user for a BYOD dataset path and validate it."""
    while True:
        # ``cancel`` (and its aliases) now raise ``WizardCancel`` from the
        # primitive prompt; in the BYOD prelude we honour the long-standing
        # "fall back to the full wizard" semantics by catching it here instead
        # of letting it bubble to a clean exit.
        try:
            dataset_path = _prompt(
                "Path to your dataset JSONL (must exist as a JSONL file) or HF Hub ID, "
                "or 'cancel' to fall back to the full wizard",
                "",
            )
        except WizardCancel:
            _print("  Cancelled — falling back to the full wizard.")
            return None
        if dataset_path.strip().lower() in _CANCEL_TOKENS:  # pragma: no cover — defensive; cancel now raises
            _print("  Cancelled — falling back to the full wizard.")
            return None
        if not dataset_path:
            _print("  A dataset path is required for this template. Type 'cancel' to use the full wizard instead.")
            continue

        try:
            result = _validate_local_jsonl(dataset_path)
        except WizardCancel:
            _print("  Cancelled — falling back to the full wizard.")
            return None
        if isinstance(result, str):
            return result
        if result is None:
            continue
        if _HF_HUB_ID_RE.match(dataset_path):
            _print(f"  Treating '{dataset_path}' as an HF Hub dataset ID (no local validation).")
            return dataset_path

        _print(f"  Path not found or not a regular file: {dataset_path}")


# ---------------------------------------------------------------------------
# Quickstart-template prelude.  An operator who picks a template
# bypasses the 9-step full wizard via the curated shortcut path.
# ---------------------------------------------------------------------------


def _maybe_run_quickstart_template() -> Optional[str]:
    """Offer the quickstart template path before the full wizard."""
    from ..quickstart import TEMPLATES, list_templates, run_quickstart

    _print("\n" + "=" * 60)
    _print("  ForgeLM Configuration Wizard")
    _print("=" * 60)

    if not _prompt_yes_no(
        "\nStart from a curated quickstart template? (recommended for first runs)",
        default=True,
    ):
        return None

    _print("\nAvailable templates:")
    names: List[str] = []
    for tpl in list_templates():
        bundled = "[x] data" if tpl.bundled_dataset else "[ ] BYOD"
        names.append(tpl.name)
        _print(f"  {len(names)}) {tpl.name}  —  {tpl.title}  ({bundled}, ~{tpl.estimated_minutes}min)")
    raw = _prompt("Pick a template by number or name", names[0])

    chosen: Optional[str] = None
    if raw.isdigit():
        idx = int(raw)
        if 1 <= idx <= len(names):
            chosen = names[idx - 1]
    elif raw in TEMPLATES:
        chosen = raw
    if chosen is None:
        _print(f"  Could not interpret '{raw}'. Falling back to the full wizard.")
        return None

    template = TEMPLATES[chosen]
    if not template.bundled_dataset:
        _print(f"  '{chosen}' is BYOD — bring your own JSONL dataset.")
        dataset_path = _resolve_byod_dataset_path()
        if dataset_path is None:
            return None
    else:
        dataset_path = ""

    try:
        result = run_quickstart(
            chosen,
            dataset_override=dataset_path or None,
        )
    except (FileNotFoundError, ValueError) as e:
        _print(f"  Quickstart failed: {e}. Falling back to the full wizard.")
        return None
    except OSError as e:
        # ``forgelm.quickstart._resolve_dataset`` raises ``FileExistsError``
        # by design when the per-run scratch dataset path already exists
        # (an ``OSError`` subclass distinct from ``FileNotFoundError``);
        # other filesystem failures (disk full, permissions) during the
        # seed-dataset copy land here too.  Mirrors the catch-tier pattern
        # in ``_offer_ingest_for_directory`` above.
        _print(f"  Quickstart failed due to filesystem error: {e}. Falling back to the full wizard.")
        return None

    _print(f"\n  Quickstart config generated at: {result.config_path}")
    _print(f"  Selected model: {result.chosen_model}  ({result.selection_reason})")
    _print(f"  Dataset       : {result.dataset_path}")
    _print()
    return str(result.config_path)


def _finalize_quickstart_path(quickstart_path: str) -> Optional[str]:
    """Ask whether to start training now with the quickstart-generated config."""
    quoted_path = shlex.quote(quickstart_path)
    if _prompt_yes_no("Start training now with the generated config?", default=False):
        _print(f"\n  Running: forgelm --config {quoted_path}")
        return quickstart_path
    _print("\n  To start training later, run:")
    _print(f"    forgelm --config {quoted_path}")
    return None
