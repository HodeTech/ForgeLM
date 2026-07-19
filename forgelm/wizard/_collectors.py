"""Section-collector helpers — webhook / safety / trainer / compliance / etc.

Every public ``ForgeConfig`` block the operator can populate via the
wizard has a ``_collect_*`` helper here.  The orchestrator
(``forgelm.wizard._orchestrator``) calls them in order; each returns
either a populated dict (which the orchestrator drops into the
running config) or ``None`` (which the orchestrator interprets as
"the operator declined this block; omit it from the YAML").
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

from ._io import (
    _print,
    _prompt,
    _prompt_choice,
    _prompt_float,
    _prompt_int,
    _prompt_optional_list,
    _prompt_required,
    _prompt_yes_no,
)


def _prompt_required_list(question: str) -> list:
    """Prompt for a non-empty comma-separated list, re-asking until provided."""
    while True:
        items = _prompt_optional_list(question)
        if items:
            return items
        _print("    At least one entry is required.")


# ---------------------------------------------------------------------------
# Risk-tier surface (mirrors ``forgelm.config._STRICT_RISK_TIERS`` /
# ``forgelm.config.RiskTier``).
# ---------------------------------------------------------------------------


# Strict-tier risk classifications that trigger ``F-compliance-110``
# auto-coercion (mandatory safety eval + Article 14 staging gate).
_STRICT_RISK_TIERS: Tuple[str, ...] = ("high-risk", "unacceptable")

# Full ``RiskTier`` Literal, in the order the wizard offers them
# (default = ``minimal-risk`` matches the Pydantic field default).
_RISK_TIERS: Tuple[str, ...] = (
    "unknown",
    "minimal-risk",
    "limited-risk",
    "high-risk",
    "unacceptable",
)


# ---------------------------------------------------------------------------
# Safety probe path resolution — Phase 22 / G16.  Bundled probe set
# lives in the package data at
# ``forgelm.safety_prompts/default_probes.jsonl``; the v0.5.5 wizard
# emitted ``configs/safety_prompts/general_safety.jsonl`` which is
# repo-relative and not shipped in the wheel — broken on
# ``pip install``.
# ---------------------------------------------------------------------------


def _default_safety_probes_path() -> str:
    """Return the absolute filesystem path of the bundled default probe set.

    Uses :mod:`importlib.resources` so the path is correct under both
    editable installs and built wheels.  Falls back to the import-time
    package directory when ``files()`` raises (e.g. the safety_prompts
    package isn't installed — which can happen in slim test
    environments).
    """
    try:
        from importlib.resources import files

        probe = files("forgelm.safety_prompts").joinpath("default_probes.jsonl")
        return str(probe)
    except (FileNotFoundError, ImportError):  # ModuleNotFoundError is an ImportError subclass.
        # Fallback: best-effort guess at the package directory.
        return str(Path(__file__).resolve().parent.parent / "safety_prompts" / "default_probes.jsonl")


# ---------------------------------------------------------------------------
# Shared ``env:VAR_NAME`` reference parsing — the webhook URL prompt and
# the monitoring-endpoint prompt both accept this indirection syntax.
# Centralised here so the accepted env-var-name shape (POSIX: uppercase
# letters / digits / underscores, must start with a letter) only needs
# updating in one place.
# ---------------------------------------------------------------------------


_ENV_VAR_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$", flags=re.ASCII)


def _parse_env_ref(raw: str) -> Optional[str]:
    """Parse an ``env:VAR_NAME`` reference; return the variable name.

    Returns ``None`` when *raw* does not start with the ``env:`` prefix
    at all — callers treat that as "not an env reference, handle as a
    literal value instead".  Raises :class:`ValueError` when the prefix
    is present but the variable name is missing or not a valid POSIX
    environment-variable name; callers that want a soft-fail (warn and
    drop the field, e.g. :func:`_collect_monitoring`) should catch it,
    while callers that want a hard re-prompt (e.g.
    :func:`_parse_webhook_value`) let it propagate.
    """
    if not raw.lower().startswith("env:"):
        return None
    var_name = raw[4:].strip()
    if not var_name:
        raise ValueError("`env:` prefix needs a non-empty variable name (e.g. `env:SLACK_WEBHOOK_URL`).")
    if not _ENV_VAR_NAME_RE.match(var_name):
        raise ValueError(
            f"`env:{var_name}` is not a POSIX environment-variable name "
            "(uppercase letters / digits / underscores, must start with a letter)."
        )
    return var_name


# ---------------------------------------------------------------------------
# Webhook URL parsing — Phase 22 / G15.  Single-prompt syntax mirrors
# the web wizard's ``env:VAR_NAME`` prefix sugar.
# ---------------------------------------------------------------------------


def _parse_webhook_value(raw: str) -> Optional[Dict[str, str]]:
    """Parse a webhook URL or ``env:VAR_NAME`` reference.

    Returns:
        - ``None`` when *raw* is empty (caller drops the webhook block).
        - ``{"url_env": "VAR"}`` when *raw* starts with ``env:``.
        - ``{"url": "<raw>"}`` otherwise.

    Validates that bare URLs use ``https`` scheme; ``http`` URLs print
    a warning but are still accepted (matches ``forgelm/webhook.py``'s
    runtime behaviour — ``allow_insecure_http=True`` only logs a
    warning, doesn't reject).  Empty / malformed URLs surface a clear
    re-prompt request via ``ValueError``.
    """
    raw = raw.strip()
    if not raw:
        return None
    if raw.lower().startswith("env:"):
        return {"url_env": _parse_env_ref(raw)}
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"`{raw}` is not a valid URL or env-var reference (use `https://…` or `env:VAR_NAME`).")
    if parsed.scheme not in ("https", "http"):
        raise ValueError(
            f"Webhook URL must use `https://` (or `http://` with explicit operator opt-in), got `{parsed.scheme}://`."
        )
    if parsed.scheme == "http":
        _print(
            "  Warning: webhook URL uses HTTP, not HTTPS.  Data will travel unencrypted.  "
            "Use `env:VAR_NAME` to source the URL from a secret manager."
        )
    # SSRF preflight — reject loopback / RFC1918 / IMDS-style hosts up
    # front so the operator catches typos at config time, not 30 minutes
    # into a training run when ``forgelm/_http.safe_post`` rejects the
    # destination.  Runtime ``allow_private`` overrides remain available
    # for operators who legitimately point at internal hooks.
    #
    # B10 (review-cycle 2 audit): the preflight rejects URLs whose
    # *current* DNS resolution lands on a private IP — DNS rebinding at
    # runtime is the same TOCTOU window ``_http.safe_post`` resolves
    # under tracked at issue #14 and is NOT closed here. The preflight
    # buys "wrong URL caught at config time", not "guaranteed-public
    # destination at training time".
    host = (parsed.hostname or "").strip()
    if host and _webhook_preflight_is_private(host):
        raise ValueError(
            f"Webhook URL `{raw}` resolves to a private / loopback / link-local "
            f"destination (`{host}`).  Use `env:VAR_NAME` for production hooks "
            "or set `webhook.allow_private=true` in the YAML if this is intentional."
        )
    return {"url": raw}


_WEBHOOK_DNS_PREFLIGHT_TIMEOUT_SECONDS = 3.0


def _webhook_preflight_is_private(host: str) -> bool:
    """Bounded-timeout wrapper around ``_http._is_private_destination``.

    ``_is_private_destination`` performs a real ``socket.getaddrinfo`` DNS
    lookup with no timeout of its own, which can hang this interactive
    prompt for the OS resolver's default timeout (tens of seconds) on a
    sandboxed CI runner, an air-gapped machine, or a network with a
    slow/unreachable resolver.  ``socket.setdefaulttimeout`` does NOT bound
    this: CPython's ``getaddrinfo`` is a blocking libc call that only
    consults the socket default timeout for socket-object operations
    (``connect``/``recv``), never for name resolution — so that approach
    was previously a no-op against the exact hang it claimed to bound.

    Instead, run the lookup in a daemon worker thread and join with a
    ``_WEBHOOK_DNS_PREFLIGHT_TIMEOUT_SECONDS`` bound. If the thread has not
    finished by then, the resolver is treated as unbounded-slow and the
    preflight is skipped — ``_http.safe_post`` still enforces the real SSRF
    check at training time — instead of blocking the wizard. The worker
    thread is daemonized so an actually-hung resolver cannot keep the
    process alive; its (possibly late) result is simply discarded. Returns
    ``False`` (don't block) whenever the check itself cannot complete,
    whether because ``forgelm._http`` is unavailable, the resolver raised,
    or the timeout was hit.
    """
    import threading

    try:
        from .._http import _is_private_destination
    except ImportError:  # pragma: no cover — _http always present
        return False

    outcome: Dict[str, Any] = {}

    def _resolve() -> None:
        try:
            outcome["result"] = _is_private_destination(host)
        except OSError as exc:
            # ``_is_private_destination`` already narrows
            # ``socket.gaierror`` internally; anything else surfacing here
            # (e.g. a socket-layer timeout) is "could not determine", not
            # "reject".
            outcome["error"] = exc

    worker = threading.Thread(target=_resolve, daemon=True)
    worker.start()
    worker.join(_WEBHOOK_DNS_PREFLIGHT_TIMEOUT_SECONDS)
    if worker.is_alive() or "error" in outcome:
        return False
    return bool(outcome.get("result", False))


# ---------------------------------------------------------------------------
# Webhook collector
# ---------------------------------------------------------------------------


def _collect_webhook_config() -> Optional[Dict[str, Any]]:
    """Prompt for webhook configuration; returns the webhook section or None."""
    if not _prompt_yes_no("Configure webhook notifications?", default=False):
        return None
    while True:
        raw = _prompt(
            "Webhook URL (or `env:VAR_NAME` to source from environment)",
            "env:FORGELM_WEBHOOK_URL",
        )
        if not raw:
            _print("  A webhook URL or `env:` reference is required to enable webhooks.")
            continue
        try:
            section = _parse_webhook_value(raw)
        except ValueError as exc:
            _print(f"  {exc}")
            continue
        if section is None:
            return None
        # Defaults mirror ``site/js/wizard.js`` and ``WebhookConfig`` —
        # start notifications are noisy and off by default; success +
        # failure are the operationally interesting ones.
        section.setdefault("notify_on_start", False)
        section.setdefault("notify_on_success", True)
        section.setdefault("notify_on_failure", True)
        return section


# ---------------------------------------------------------------------------
# Safety-eval collector — Phase 22 / G16
# ---------------------------------------------------------------------------


def _collect_safety_config(
    *,
    default_enabled: bool = False,
    existing: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Prompt for safety eval; returns the safety section or None.

    *default_enabled* is set to ``True`` by ``_apply_strict_tier_coercion``
    so high-risk operators see "Y/n" instead of "y/N" — the hint is
    the nudge.

    *existing* lets ``--wizard-start-from`` reruns honour the loaded
    YAML's prior choices: prompt defaults are taken from the existing
    block, and ``track_categories`` / ``severity_thresholds`` flow
    through verbatim when the operator declines the re-prompt.  A
    bare Enter at every step preserves the prior config.
    """
    if not _prompt_yes_no("Enable safety evaluation (Llama Guard)?", default=default_enabled):
        return None
    existing = existing or {}
    existing_scoring = existing.get("scoring")
    scoring_default = 2 if existing_scoring == "confidence_weighted" else 1
    scoring_choice = _prompt_choice(
        "Safety scoring mode:",
        [
            "binary (simple safe/unsafe ratio)",
            "confidence_weighted (uses classifier confidence)",
        ],
        default=scoring_default,
    )
    scoring_mode = "confidence_weighted" if "confidence" in scoring_choice else "binary"
    classifier_default = existing.get("classifier") or "meta-llama/Llama-Guard-3-8B"
    safety: Dict[str, Any] = {
        "enabled": True,
        # ``classifier`` and ``max_safety_regression`` were originally
        # web-wizard-only fields.  Surfacing both here keeps CLI- and
        # web-generated YAMLs structurally equivalent; defaults fall
        # through to the loaded value when an existing block is passed.
        "classifier": _prompt(
            "Harm classifier (HF Hub ID — leave empty for Llama-Guard-3-8B default)",
            classifier_default,
        )
        or classifier_default,
        "test_prompts": existing.get("test_prompts") or _default_safety_probes_path(),
        "scoring": scoring_mode,
        "max_safety_regression": _prompt_float(
            "max_safety_regression (0.0-1.0; auto-revert above this unsafe-response ratio)",
            float(existing.get("max_safety_regression", 0.05)),
            min_val=0.0,
            max_val=1.0,
        ),
    }
    if scoring_mode == "confidence_weighted":
        safety["min_safety_score"] = float(existing.get("min_safety_score", 0.85))
        safety["min_classifier_confidence"] = _prompt_float(
            "min_classifier_confidence (0.0-1.0; flag responses below this confidence floor for review)",
            float(existing.get("min_classifier_confidence", 0.7)),
            min_val=0.0,
            max_val=1.0,
        )
    track_default = bool(existing.get("track_categories"))
    if _prompt_yes_no("Track harm categories (S1-S14 + ForgeLM-curated)?", default=track_default):
        safety["track_categories"] = True
        safety["severity_thresholds"] = existing.get("severity_thresholds") or {
            "critical": 0,
            "high": 0.01,
            "medium": 0.05,
        }
    return safety


# ---------------------------------------------------------------------------
# Trainer-specific hyperparameters — Phase 22 / G1
# ---------------------------------------------------------------------------


def _collect_trainer_hyperparameters(
    trainer_type: str,
    *,
    existing: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return trainer-specific hyperparameters as a flat dict.

    SFT has no per-trainer knobs (the schema's generic
    ``num_train_epochs`` / ``learning_rate`` already covers it), so
    the helper short-circuits with an empty dict.  Other trainers
    prompt for the fields ``forgelm.config.TrainingConfig`` exposes;
    defaults mirror the schema so an operator who accepts every
    prompt produces a YAML byte-equivalent to ``ForgeConfig()``.

    *existing* lets ``--wizard-start-from`` reruns honour the loaded
    YAML's prior trainer-specific knobs as prompt defaults so a bare
    Enter through this step keeps ``dpo_beta``, ``simpo_*``,
    ``kto_beta``, ``orpo_beta``, ``grpo_*`` exactly as they were.
    """
    if trainer_type == "sft":
        return {}
    existing = existing or {}
    _print("\n  Trainer-specific hyperparameters")
    if trainer_type == "dpo":
        return {
            "dpo_beta": _prompt_float(
                "dpo_beta — temperature / KL strength (lower → closer to reference model)",
                float(existing.get("dpo_beta", 0.1)),
                min_val=1e-5,
                max_val=10.0,
            ),
        }
    if trainer_type == "orpo":
        return {
            "orpo_beta": _prompt_float(
                "orpo_beta — odds-ratio penalty strength",
                float(existing.get("orpo_beta", 0.1)),
                min_val=1e-5,
                max_val=10.0,
            ),
        }
    if trainer_type == "kto":
        return {
            "kto_beta": _prompt_float(
                "kto_beta — same KL role as dpo_beta for binary feedback",
                float(existing.get("kto_beta", 0.1)),
                min_val=1e-5,
                max_val=10.0,
            ),
        }
    if trainer_type == "simpo":
        return {
            "simpo_beta": _prompt_float(
                "simpo_beta — length-normalised reward scale (NOT same scale as dpo_beta)",
                float(existing.get("simpo_beta", 2.0)),
                min_val=1e-5,
                max_val=10.0,
            ),
            "simpo_gamma": _prompt_float(
                "simpo_gamma — margin between chosen / rejected log-likelihoods",
                float(existing.get("simpo_gamma", 0.5)),
                min_val=0.0,
                max_val=10.0,
            ),
        }
    if trainer_type == "grpo":
        result: Dict[str, Any] = {
            "grpo_num_generations": _prompt_int(
                "grpo_num_generations — responses sampled per prompt (higher = more stable, more compute)",
                int(existing.get("grpo_num_generations", 4)),
                min_val=2,
                max_val=64,
            ),
            "grpo_max_completion_length": _prompt_int(
                "grpo_max_completion_length — cap on generated response length",
                int(existing.get("grpo_max_completion_length", 512)),
                min_val=32,
                max_val=8192,
            ),
        }
        reward = _prompt(
            "grpo_reward_model — dotted-path callable, HF Hub reward model, or empty for the built-in shaper",
            str(existing.get("grpo_reward_model") or ""),
        )
        if reward.strip():
            result["grpo_reward_model"] = reward.strip()
        return result
    return {}


# ---------------------------------------------------------------------------
# Long-context modifiers — RoPE + NEFTune
# ---------------------------------------------------------------------------


_ROPE_SCALING_TYPES: Tuple[str, ...] = ("linear", "dynamic", "yarn", "longrope")


def _rope_scaling_is_valid(candidate: Dict[str, Any]) -> bool:
    """Return True when *candidate* passes ``ForgeConfig``'s rope_scaling validator.

    Runs the block through the real ``TrainingConfig`` field validator so
    the wizard's "keep existing" branch can never honour a shape that
    ``ForgeConfig`` will later reject — and never reject one it accepts.
    This mirrors the validator's per-type branching (scalar ``factor`` for
    ``linear`` / ``dynamic`` / ``yarn``; non-empty ``short_factor`` /
    ``long_factor`` lists for ``longrope``) and the ``rope_type`` alias
    without duplicating that logic here.
    """
    from pydantic import ValidationError

    from ..config import TrainingConfig

    try:
        TrainingConfig(rope_scaling=candidate)
    except ValidationError:
        return False
    return True


def _describe_rope_scaling(block: Dict[str, Any]) -> str:
    """Render a one-line human summary of an existing rope_scaling block."""
    rope_type = block.get("type", block.get("rope_type", "?"))
    if rope_type == "longrope":
        short = block.get("short_factor")
        long = block.get("long_factor")
        short_len = len(short) if isinstance(short, list) else "?"
        long_len = len(long) if isinstance(long, list) else "?"
        return f"type={rope_type}, short_factor[{short_len}], long_factor[{long_len}]"
    return f"type={rope_type}, factor={block.get('factor', '?')}"


def _prompt_factor_list(question: str, default: list) -> list:
    """Prompt for a non-empty comma-separated list of numbers, re-asking until valid.

    Backs the LongRoPE ``short_factor`` / ``long_factor`` prompts, whose
    ``ForgeConfig`` validator requires non-empty numeric lists.  Funnels
    through :func:`_prompt` so navigation tokens (``back`` / ``reset`` /
    ``cancel``) still fire; a bare Enter keeps *default*.
    """
    default_csv = ", ".join(repr(value) for value in default)
    while True:
        raw = _prompt(f"{question} (comma-separated numbers)", default_csv)
        items = [item.strip() for item in raw.split(",") if item.strip()]
        if not items:
            _print("    At least one numeric factor is required.")
            continue
        try:
            return [float(item) for item in items]
        except ValueError:
            _print("    All entries must be numbers (e.g. `1.0, 1.2, 1.5`).")


def _collect_longrope_factors(max_length: int, base_context: int) -> Dict[str, Any]:
    """Collect a LongRoPE ``rope_scaling`` block (short_factor / long_factor).

    LongRoPE is parameterised by per-dimension ``short_factor`` /
    ``long_factor`` lists (one entry per ``head_dim / 2``), not a scalar
    ``factor`` — this is exactly what ``ForgeConfig``'s ``rope_scaling``
    validator requires for ``rope_type='longrope'``.  The values are
    model-specific (copied from the model's ``config.json``; e.g. Phi-3
    ships them), so the wizard cannot compute them from ``max_length``
    alone; it prompts for both lists with non-empty placeholder defaults
    the operator is told to replace, and always returns a block that
    round-trips through ``ForgeConfig``.
    """
    fallback_factor = round(max_length / base_context, 4)
    _print(
        "  Note: LongRoPE uses per-dimension short_factor / long_factor lists "
        "(one entry per head_dim/2), not a scalar factor.  These values are "
        "model-specific — copy them from your model's config.json (e.g. Phi-3 "
        "ships them under `rope_scaling`).  The defaults below are non-empty "
        "placeholders; replace them with your model's factors before training."
    )
    short_factor = _prompt_factor_list(
        "short_factor — per-dimension factors for the short-context regime",
        [1.0],
    )
    long_factor = _prompt_factor_list(
        "long_factor — per-dimension factors for the extended-context regime",
        [fallback_factor],
    )
    return {"type": "longrope", "short_factor": short_factor, "long_factor": long_factor}


def _collect_rope_scaling(
    max_length: int,
    *,
    existing: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Prompt for RoPE scaling parameters when context is long; otherwise None.

    *existing* lets ``--wizard-start-from`` reruns offer to keep a
    previously-tuned ``rope_scaling`` block verbatim.  When the loaded
    YAML carries one, the helper asks "Keep existing RoPE scaling?"
    (default yes) and returns the stored dict on accept; on decline,
    or when there is no prior block, the standard prompt flow runs and
    the scaling parameters are always recomputed fresh from
    ``max_length`` — reusing a stale ``existing`` block after an explicit
    decline would silently ignore the operator's answer.

    The "keep existing" branch validates the whole block through
    ``ForgeConfig``'s own ``rope_scaling`` field validator
    (:func:`_rope_scaling_is_valid`) before returning it verbatim, so a
    valid ``longrope`` block (``short_factor`` / ``long_factor`` lists,
    ``factor`` optional) is honoured while a corrupted / hand-edited one
    degrades to a fresh recompute.  ``ForgeConfig`` normally guarantees
    this shape for anything loaded via ``--wizard-start-from``, but a
    resumed ``wizard_state.yaml`` snapshot (``_load_wizard_state``) is not
    schema-validated, so the check must run here too.
    """
    if max_length <= 4096:
        return None
    base_context = 4096
    if existing:
        _print(f"\n  Existing RoPE scaling: {_describe_rope_scaling(existing)}")
        if _prompt_yes_no("  Keep existing RoPE scaling?", default=True):
            if _rope_scaling_is_valid(existing):
                return dict(existing)
            _print("  ⚠ Existing RoPE scaling is invalid — recomputing instead of keeping it.")
    _print(f"\n  Long context detected ({max_length} tokens).")
    if not _prompt_yes_no("Enable RoPE scaling for extended context?", default=True):
        return None
    existing_type = existing.get("type", existing.get("rope_type")) if existing else None
    type_default = _ROPE_SCALING_TYPES.index(existing_type) + 1 if existing_type in _ROPE_SCALING_TYPES else 1
    rope_type = _prompt_choice(
        "RoPE scaling type:",
        [
            "linear (simple, proven)",
            "dynamic (adaptive)",
            "yarn (best quality, newer)",
            "longrope (newest — 32K+ context, requires LongRoPE-aware model)",
        ],
        default=type_default,
    ).split(" ")[0]
    # Reaching this point means either there was no existing block, or
    # the operator explicitly declined to keep it (or it failed
    # validation above) — always recompute fresh from max_length rather
    # than falling back to a stale/unvalidated existing block, which
    # would silently override the operator's decline.
    if rope_type == "longrope":
        return _collect_longrope_factors(max_length, base_context)
    rope_factor = max_length / base_context
    _print(
        f"  Note: RoPE factor {rope_factor:.1f}x computed assuming base context of "
        f"{base_context} tokens.  Adjust manually if your model has a different "
        f"original context length (e.g., Llama 3.1 = 131072, Mistral v0.3 = 32768)."
    )
    return {"type": rope_type, "factor": rope_factor}


def _collect_neftune_alpha() -> Optional[float]:
    """Prompt for NEFTune noise injection; returns alpha or None."""
    if not _prompt_yes_no("Enable NEFTune noise injection (improves training quality)?", default=False):
        return None
    return _prompt_float("NEFTune noise alpha", 5.0, min_val=0.0, max_val=100.0)


# ---------------------------------------------------------------------------
# GaLore — Phase 22 / G9 surfaces all six schema-allowed optimizer
# variants, including the three ``_layerwise`` siblings that drop peak
# VRAM further.
# ---------------------------------------------------------------------------


_GALORE_OPTIMIZERS: Tuple[str, ...] = (
    "galore_adamw",
    "galore_adamw_8bit",
    "galore_adafactor",
    "galore_adamw_layerwise",
    "galore_adamw_8bit_layerwise",
    "galore_adafactor_layerwise",
)


def _collect_galore_config(
    use_galore: bool,
    *,
    existing: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Prompt for GaLore-specific knobs when GaLore was selected.

    *existing* (any subset of ``galore_optim`` / ``galore_rank`` /
    ``galore_update_proj_gap`` / ``galore_scale`` from the loaded
    config) lets ``--wizard-start-from`` reruns offer to keep the
    operator's prior tuning verbatim.  On decline (or no prior block)
    the helper falls back to schema defaults — same as a greenfield
    run.
    """
    if not use_galore:
        return {}
    existing = existing or {}
    if existing.get("galore_optim") or existing.get("galore_rank"):
        _print(
            f"\n  Existing GaLore: optim={existing.get('galore_optim', '?')}, "
            f"rank={existing.get('galore_rank', '?')}, "
            f"update_proj_gap={existing.get('galore_update_proj_gap', 200)}, "
            f"scale={existing.get('galore_scale', 0.25)}"
        )
        if _prompt_yes_no("  Keep existing GaLore tuning?", default=True):
            return {
                "galore_enabled": True,
                "galore_optim": existing.get("galore_optim", "galore_adamw"),
                "galore_rank": int(existing.get("galore_rank", 128)),
                "galore_update_proj_gap": int(existing.get("galore_update_proj_gap", 200)),
                "galore_scale": float(existing.get("galore_scale", 0.25)),
            }
    _print("\n[Advanced] GaLore Configuration")
    galore_rank = _prompt_int(
        "GaLore rank (lower = less memory)",
        int(existing.get("galore_rank", 128)),
        min_val=1,
        max_val=4096,
    )
    optim_default = (
        _GALORE_OPTIMIZERS.index(existing.get("galore_optim")) + 1
        if existing.get("galore_optim") in _GALORE_OPTIMIZERS
        else 1
    )
    galore_optim = _prompt_choice(
        "GaLore optimizer (variants ending in _8bit halve optimiser VRAM; "
        "_layerwise variants further reduce peak VRAM via per-layer recompute):",
        [f"{name} (recommended)" if name == "galore_adamw" else name for name in _GALORE_OPTIMIZERS],
        default=optim_default,
    )
    return {
        "galore_enabled": True,
        "galore_optim": galore_optim.split(" ")[0],
        "galore_rank": galore_rank,
        "galore_update_proj_gap": int(existing.get("galore_update_proj_gap", 200)),
        "galore_scale": float(existing.get("galore_scale", 0.25)),
    }


# ---------------------------------------------------------------------------
# Compliance + Article 9 / 10 / Retention / Monitoring / Eval-gates /
# Synthetic — Phase 22 / G5.
# ---------------------------------------------------------------------------


def _collect_compliance_metadata() -> Dict[str, Any]:
    """Article 11 + Annex IV §1: provider + system metadata.

    ``risk_classification`` is collected FIRST so the operator sees the
    strict-tier hint up front; downstream Article 9 / 10 collectors
    branch on the chosen tier and the wizard's auto-coercion gate
    (``_apply_strict_tier_coercion``) reads it without an awkward
    forward-reference.
    """
    # E2 (review-cycle 3): inline rationale — risk_classification gates
    # F-compliance-110 auto-coercion (high-risk + unacceptable both force
    # safety eval + Article 14 staging gate); operators picking
    # 'minimal-risk' bypass these mandatory checks.
    risk_classification = _prompt_choice(
        "Risk classification (mirrored at risk_assessment.risk_category) — "
        "high-risk / unacceptable auto-enable safety eval + human-approval staging "
        "(EU AI Act Article 9 + 14 / F-compliance-110):",
        list(_RISK_TIERS),
        default=2,  # ``minimal-risk`` — same default as the Pydantic field.
    )
    if risk_classification in _STRICT_RISK_TIERS:
        _print(
            "  Strict tier selected — Article 11 / Annex IV §1 fields below "
            "(provider name, contact, system name, intended purpose) are "
            "mandatory and re-prompt until provided."
        )
        provider_name = _prompt_required("Organization (legal-entity) name")
        provider_contact = _prompt_required("Provider regulatory contact (email or phone)")
        system_name = _prompt_required("Human-readable system name")
        intended_purpose = _prompt_required("Intended purpose of the system")
    else:
        provider_name = _prompt("Organization (legal-entity) name", "")
        provider_contact = _prompt("Provider regulatory contact (email or phone)", "")
        system_name = _prompt("Human-readable system name", "")
        intended_purpose = _prompt("Intended purpose of the system", "")
    return {
        "provider_name": provider_name,
        "provider_contact": provider_contact,
        "system_name": system_name,
        "intended_purpose": intended_purpose,
        "known_limitations": _prompt("Known limitations operator wants documented (free-text)", ""),
        "system_version": _prompt("System version string", "v0.1.0"),
        "risk_classification": risk_classification,
    }


def _collect_risk_assessment(risk_classification: str) -> Optional[Dict[str, Any]]:
    """Article 9: risk-management evidence.  Mandatory for strict tiers."""
    is_strict = risk_classification in _STRICT_RISK_TIERS
    if not is_strict and not _prompt_yes_no("Configure Article 9 risk_assessment metadata?", default=False):
        return None
    if is_strict:
        _print(
            "  Risk classification is high-risk / unacceptable — Article 9 "
            "risk_assessment evidence is mandatory.  All four fields below "
            "should be populated."
        )
    if is_strict:
        intended_use = _prompt_required("Article 9(2)(a): intended_use")
        foreseeable = _prompt_required_list("Article 9(2)(b): foreseeable_misuse — list at least one realistic misuse")
        mitigation = _prompt_required_list("Article 9(2)(c): mitigation_measures the deployer applies")
    else:
        intended_use = _prompt("intended_use (Article 9(2)(a)) — optional", "")
        foreseeable = _prompt_optional_list("Article 9(2)(b): foreseeable_misuse — list at least one realistic misuse")
        mitigation = _prompt_optional_list("Article 9(2)(c): mitigation_measures the deployer applies")
    vulnerable = _prompt_yes_no(
        "Article 9(2)(b): vulnerable_groups_considered (children / minorities / etc.)?",
        default=is_strict,
    )
    return {
        "intended_use": intended_use,
        "foreseeable_misuse": foreseeable,
        "mitigation_measures": mitigation,
        "vulnerable_groups_considered": vulnerable,
        # Mirror the compliance.risk_classification across into
        # risk_assessment.risk_category so ``ForgeConfig._risk_tiers``
        # sees a consistent pair.
        "risk_category": risk_classification,
    }


_ARTICLE10_COLLECTION_PROMPT = "Article 10(2)(b): how was data collected?"
_ARTICLE10_ANNOTATION_PROMPT = "Article 10(2)(b): annotation methodology"
_ARTICLE10_KNOWN_BIASES_PROMPT = "Article 10(2)(f): known_biases"


def _collect_data_governance(
    *,
    mandatory: bool,
    existing: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Article 10: data governance metadata.  Mandatory under strict tiers.

    When *existing* carries previously populated values (operator
    iterating from ``--wizard-start-from``), each prompt defaults to
    the prior answer so a bare Enter keeps it.  Strict-tier
    ``_prompt_required`` calls still re-prompt until non-empty — the
    existing-value default just spares the operator from re-typing.
    """
    existing = existing or {}
    if not mandatory and not _prompt_yes_no(
        "Configure Article 10 data.governance metadata?",
        default=bool(existing),
    ):
        return existing or None
    if mandatory:
        _print(
            "  Risk classification is high-risk / unacceptable — Article 10 "
            "data.governance evidence is mandatory.  Free-text fields below "
            "re-prompt until provided."
        )
        # ``_prompt_required`` doesn't accept a default; for strict-tier
        # iteration we fall back to ``_prompt`` when an existing
        # non-empty value exists, otherwise enforce the strict re-prompt.
        collection_method = (
            _prompt(_ARTICLE10_COLLECTION_PROMPT, existing["collection_method"])
            if existing.get("collection_method")
            else _prompt_required(_ARTICLE10_COLLECTION_PROMPT)
        )
        annotation_process = (
            _prompt(_ARTICLE10_ANNOTATION_PROMPT, existing["annotation_process"])
            if existing.get("annotation_process")
            else _prompt_required(_ARTICLE10_ANNOTATION_PROMPT)
        )
        known_biases = (
            _prompt(_ARTICLE10_KNOWN_BIASES_PROMPT, existing["known_biases"])
            if existing.get("known_biases")
            else _prompt_required(_ARTICLE10_KNOWN_BIASES_PROMPT)
        )
    else:
        collection_method = _prompt(_ARTICLE10_COLLECTION_PROMPT, existing.get("collection_method", ""))
        annotation_process = _prompt(_ARTICLE10_ANNOTATION_PROMPT, existing.get("annotation_process", ""))
        known_biases = _prompt(_ARTICLE10_KNOWN_BIASES_PROMPT, existing.get("known_biases", ""))
    return {
        "collection_method": collection_method,
        "annotation_process": annotation_process,
        "known_biases": known_biases,
        "personal_data_included": _prompt_yes_no(
            "Article 10(5): personal_data_included?",
            default=bool(existing.get("personal_data_included", False)),
        ),
        "dpia_completed": _prompt_yes_no(
            "Article 35 GDPR: dpia_completed?",
            default=bool(existing.get("dpia_completed", False)),
        ),
    }


def _collect_retention() -> Optional[Dict[str, Any]]:
    """GDPR Article 5(1)(e) + 17: retention horizons."""
    if not _prompt_yes_no(
        "Customise retention horizons (audit log / staging / ephemeral / raw documents)?",
        default=False,
    ):
        return None
    audit = _prompt_int(
        "audit_log_retention_days (0 = retain indefinitely)",
        1825,
        min_val=0,
        max_val=18250,
    )
    staging = _prompt_int("staging_ttl_days (0 = retain indefinitely)", 7, min_val=0, max_val=365)
    ephemeral = _prompt_int(
        "ephemeral_artefact_retention_days (0 = retain indefinitely)",
        90,
        min_val=0,
        max_val=3650,
    )
    raw_docs = _prompt_int("raw_documents_retention_days (0 = retain indefinitely)", 90, min_val=0, max_val=3650)
    enforce = _prompt_choice(
        "enforcement mode:",
        [
            "log_only (record violations only)",
            "warn_on_excess (stderr warning, run continues)",
            "block_on_excess (abort trainer with EXIT_EVAL_FAILURE)",
        ],
        default=1,
    )
    return {
        "audit_log_retention_days": audit,
        "staging_ttl_days": staging,
        "ephemeral_artefact_retention_days": ephemeral,
        "raw_documents_retention_days": raw_docs,
        "enforce": enforce.split(" ")[0],
    }


def _collect_monitoring() -> Optional[Dict[str, Any]]:
    """Article 12+17: post-market monitoring hooks.

    Endpoint accepts either a literal URL or ``env:VAR_NAME`` to source
    the URL from the environment — mirrors the webhook collector's
    convention so the same syntax works in both places.
    """
    if not _prompt_yes_no("Configure post-market monitoring?", default=False):
        return None
    endpoint_raw = _prompt(
        "Monitoring endpoint URL (or `env:VAR_NAME` to source from environment)",
        "",
    )
    metrics_export = _prompt_choice(
        "metrics_export:",
        ["none", "prometheus", "datadog", "custom_webhook"],
        default=1,
    )
    monitoring: Dict[str, Any] = {
        "enabled": True,
        "metrics_export": metrics_export,
        "alert_on_drift": _prompt_yes_no("alert_on_drift?", default=True),
        "check_interval_hours": _prompt_int("check_interval_hours", 24, min_val=1, max_val=720),
    }
    # P9: support env:VAR_NAME indirection for the monitoring endpoint
    # the same way the webhook collector does.  ``MonitoringConfig`` has
    # both ``endpoint`` and ``endpoint_env`` fields; emit whichever the
    # operator typed.
    endpoint_stripped = endpoint_raw.strip()
    if endpoint_stripped.lower().startswith("env:"):
        try:
            monitoring["endpoint_env"] = _parse_env_ref(endpoint_stripped)
        except ValueError:
            _print(f"  '{endpoint_stripped}' is not a valid env-var reference — monitoring endpoint left unset.")
    elif endpoint_stripped:
        monitoring["endpoint"] = endpoint_stripped
    return monitoring


def _collect_benchmark(
    *,
    existing: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """``evaluation.benchmark`` lm-evaluation-harness gate.

    *existing* lets ``--wizard-start-from`` reruns default the gate
    prompt to "yes" when a prior block was loaded (so a bare Enter
    keeps the loaded values) and seed the inner prompts (``tasks``
    list, ``min_score`` floor) from the existing block.  An explicit
    "no" still drops the block — the caller's pop-on-decline contract
    in ``_step_evaluation`` is what disables a previously-enabled
    gate.
    """
    existing = existing or {}
    has_prior = bool(existing.get("tasks"))
    if not _prompt_yes_no(
        "Enable benchmark evaluation (lm-evaluation-harness — needs `forgelm[eval]` extra)?",
        default=has_prior,
    ):
        return None
    default_tasks_csv = ", ".join(existing["tasks"]) if has_prior else "arc_easy, hellaswag"
    tasks = _prompt_optional_list(
        "Benchmark tasks (e.g. arc_easy, hellaswag, mmlu)",
        default_csv=default_tasks_csv,
    )
    if not tasks:
        _print("  No tasks specified; benchmark gate disabled.")
        return None
    existing_min_score = existing.get("min_score")
    min_score_default = "" if existing_min_score is None else str(existing_min_score)
    min_score_raw = _prompt("min_score (auto-revert below this) — leave empty for `null`", min_score_default)
    benchmark: Dict[str, Any] = {"enabled": True, "tasks": tasks}
    if min_score_raw.strip():
        try:
            benchmark["min_score"] = float(min_score_raw)
        except ValueError:
            _print(f"  '{min_score_raw}' is not a number; min_score left unset.")
    return benchmark


def _collect_judge(
    *,
    existing: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """``evaluation.llm_judge`` LLM-as-Judge gate.

    *existing* lets ``--wizard-start-from`` reruns default the gate
    prompt to "yes" when a prior block was loaded and seed the inner
    prompts (``judge_model``, ``judge_api_key_env``, ``min_score``)
    from the loaded values.  Same pop-on-explicit-decline semantics as
    :func:`_collect_benchmark`.
    """
    existing = existing or {}
    has_prior = bool(existing.get("enabled") or existing.get("judge_model"))
    if not _prompt_yes_no("Enable LLM-as-Judge scoring?", default=has_prior):
        return None
    judge_model = _prompt(
        "judge_model (API name or local path)",
        existing.get("judge_model") or "gpt-4o-mini",
    )
    judge_api_key_env = _prompt(
        "judge_api_key_env (env-var name carrying API key; leave empty for a local judge)",
        existing.get("judge_api_key_env") or "OPENAI_API_KEY",
    )
    judge: Dict[str, Any] = {
        "enabled": True,
        "judge_model": judge_model,
        # ``JudgeConfig.min_score`` schema default is 5.0 — keep parity
        # so an operator who accepts every prompt produces a YAML
        # byte-equivalent to ``ForgeConfig()``.
        "min_score": _prompt_float(
            "min_score (mean 1-10 floor; auto-revert below)",
            float(existing.get("min_score", 5.0)),
            min_val=1.0,
            max_val=10.0,
        ),
    }
    if judge_api_key_env.strip():
        judge["judge_api_key_env"] = judge_api_key_env.strip()
    return judge


def _collect_synthetic() -> Optional[Dict[str, Any]]:
    """``synthetic`` teacher → student distillation block."""
    if not _prompt_yes_no("Configure synthetic-data generation (teacher → student)?", default=False):
        return None
    backend = _prompt_choice(
        "teacher_backend:",
        [
            "api (OpenAI/Anthropic-compatible)",
            "local (HF model loaded in-process)",
            "file (read pre-generated JSONL)",
        ],
        default=1,
    )
    backend_token = backend.split(" ")[0]
    section: Dict[str, Any] = {
        "enabled": True,
        "teacher_backend": backend_token,
        "teacher_model": _prompt("teacher_model (HF Hub ID or API model name)", "gpt-4o"),
    }
    # ``"api"`` is a backend-mode discriminator (one of "api"/"local"/"file"),
    # not a credential.  Bandit B105 flags it as "possible hardcoded password"
    # because of the proximity of ``backend_token``; suppress with rationale.
    if backend_token == "api":  # nosec B105 -- enum-style discriminator, not a secret
        api_base = _prompt(
            "api_base (OpenAI-compatible endpoint URL)",
            "https://api.openai.com/v1",
        )
        api_key_env = _prompt("api_key_env", "OPENAI_API_KEY")
        section["api_base"] = api_base
        section["api_key_env"] = api_key_env
    seed_file = _prompt("seed_file (path to seed prompts JSONL or text)", "")
    if seed_file.strip():
        section["seed_file"] = seed_file
    section["output_file"] = _prompt("output_file", "data/synthetic.jsonl")
    return section


# ---------------------------------------------------------------------------
# Strategy → flag derivation.  Phase 22 / G2 expands the menu to cover
# the full ``LoraConfigModel.method`` Literal (lora / dora / pissa /
# rslora) with GaLore as a separate axis.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _StrategyChoice:
    """Decoded form of a strategy menu pick."""

    label: str
    load_in_4bit: bool
    method: str  # lora / dora / pissa / rslora
    use_galore: bool


_STRATEGY_CHOICES: Tuple[_StrategyChoice, ...] = (
    _StrategyChoice(
        label="QLoRA (4-bit quantization, standard LoRA — recommended for most operators)",
        load_in_4bit=True,
        method="lora",
        use_galore=False,
    ),
    _StrategyChoice(
        label="LoRA (full-precision adapters, more memory, slightly better quality)",
        load_in_4bit=False,
        method="lora",
        use_galore=False,
    ),
    _StrategyChoice(
        label="DoRA (Weight-Decomposed LoRA — best adapter quality, slightly more compute)",
        load_in_4bit=True,
        method="dora",
        use_galore=False,
    ),
    _StrategyChoice(
        label="PiSSA (singular-value initialised LoRA — better stability, paper-recommended for large r)",
        load_in_4bit=True,
        method="pissa",
        use_galore=False,
    ),
    _StrategyChoice(
        label="rsLoRA (rank-stabilised LoRA — recommended when r > 64)",
        load_in_4bit=True,
        method="rslora",
        use_galore=False,
    ),
    _StrategyChoice(
        label="GaLore (full-parameter via gradient projection — no adapters, lowest peak VRAM)",
        load_in_4bit=False,
        method="lora",  # dummy, ignored when use_galore=True
        use_galore=True,
    ),
)


def _select_strategy(
    *,
    existing_method: Optional[str] = None,
    existing_load_in_4bit: Optional[bool] = None,
    existing_galore: bool = False,
) -> _StrategyChoice:
    """Prompt for the fine-tuning strategy.

    PR-D-A1 (PR-E review fix): when the operator supplied an existing
    YAML the prompt's default index is derived from the loaded
    ``lora.method`` + ``model.load_in_4bit`` + ``training.galore_enabled``
    triplet so a bare Enter preserves the existing strategy.  Falls
    back to QLoRA (default=1) for greenfield runs, matching the
    pre-cycle behaviour.
    """
    default_idx = 1  # QLoRA
    if existing_galore:
        default_idx = next(
            (i for i, c in enumerate(_STRATEGY_CHOICES, 1) if c.use_galore),
            1,
        )
    elif existing_method is not None:
        # Match by (method, load_in_4bit) pair where possible — DoRA/
        # PiSSA/rsLoRA all live in the QLoRA branch (load_in_4bit=True).
        for i, choice in enumerate(_STRATEGY_CHOICES, 1):
            if choice.use_galore:
                continue
            if choice.method == existing_method and (
                existing_load_in_4bit is None or choice.load_in_4bit == existing_load_in_4bit
            ):
                default_idx = i
                break
    label = _prompt_choice(
        "Choose your fine-tuning strategy:",
        [choice.label for choice in _STRATEGY_CHOICES],
        default=default_idx,
    )
    return next(c for c in _STRATEGY_CHOICES if c.label == label)


# ---------------------------------------------------------------------------
# Use-case preset registry — Phase 22 / G12 + G14 + I4.  Anchored on
# the CLI quickstart TEMPLATES so the web wizard adopting the same
# keys closes the use-case key drift documented in the analysis
# report.
# ---------------------------------------------------------------------------


# Wizard-only "manual" path that doesn't preselect anything.  Mirrored
# in ``_state._MANUAL_USE_CASE`` so callers from either submodule see
# the same value.
_MANUAL_USE_CASE = "custom"


def _wizard_use_case_presets() -> Dict[str, Dict[str, Any]]:
    """Build the use-case → preset map from ``forgelm.quickstart.TEMPLATES``."""
    from ..quickstart import TEMPLATES

    presets: Dict[str, Dict[str, Any]] = {}
    for name, tpl in TEMPLATES.items():
        presets[name] = {
            "title": tpl.title,
            "trainer_type": tpl.trainer_type,
            "model": tpl.primary_model,
            "bundled_dataset": tpl.bundled_dataset,
            "estimated_minutes": tpl.estimated_minutes,
        }
    presets[_MANUAL_USE_CASE] = {
        "title": "Custom (build from scratch — no preselect)",
        "trainer_type": None,
        "model": None,
        "bundled_dataset": False,
        "estimated_minutes": 0,
    }
    return presets


def _select_use_case() -> Tuple[str, Dict[str, Any]]:
    """Prompt for a use-case and return (key, preset_dict)."""
    presets = _wizard_use_case_presets()
    keys = list(presets.keys())
    options = [
        f"{key} — {presets[key]['title']} (~{presets[key]['estimated_minutes']}min)"
        if presets[key]["estimated_minutes"]
        else f"{key} — {presets[key]['title']}"
        for key in keys
    ]
    chosen = _prompt_choice(
        "Use-case (each preselects sensible defaults; you can override every later step):",
        options,
        default=1,
    )
    chosen_key = chosen.split(" — ")[0]
    return chosen_key, presets.get(chosen_key, presets[_MANUAL_USE_CASE])


# Re-export ``_PREFERENCE_COLUMNS_HINT`` from ``_state`` for the
# orchestrator's trainer-selection step — keeps the data-format hints
# next to where they're surfaced.
_PREFERENCE_COLUMNS_HINT = "Columns: prompt, chosen, rejected"
