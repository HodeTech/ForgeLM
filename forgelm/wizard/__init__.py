"""Interactive configuration wizard for ForgeLM (`forgelm --wizard`).

Phase 22 modernisation (2026-05-08) brought the CLI wizard to parity
with the in-browser ``site/js/wizard.js``: 9-step state machine
(welcome / use-case / model / strategy / trainer / dataset /
training-params / compliance / evaluation), trainer-specific
hyperparameters (``dpo_beta``, ``simpo_beta`` / ``simpo_gamma``,
``kto_beta``, ``orpo_beta``, ``grpo_*``), full PEFT method coverage
(``lora`` / ``dora`` / ``pissa`` / ``rslora``) plus GaLore as a
separate axis, EU AI Act Article 9 / 10 / 11 / 12+17 compliance
accordions, F-compliance-110 strict-tier auto-coercion, ``back`` /
``reset`` navigation, XDG-aware persistence at
``$XDG_CACHE_HOME/forgelm/wizard_state.yaml``, step-diff preview,
beginner / expert toggle, and the Phase 11.5 / 12.5 BYOD inline
ingest + audit helpers.

Phase 22 review-cycle 2 (2026-05-09) layered on operator-quality
guardrails: validate-on-exit (``ForgeConfig.model_validate``),
overwrite confirmation with auto-suffix, non-tty stdin refusal, the
pre-flight checklist, atomic state writes, web-vs-CLI safety field
union (P1 / P18), webhook SSRF preflight (A1), distinct exit-code
``EXIT_WIZARD_CANCELLED = 5`` for genuine cancels, plus best-effort
readline integration on Linux/macOS.

The module was split from a 976-line monolith into a sub-package per
``docs/standards/architecture.md``'s 1000-line ceiling rule: each
submodule owns one concern.

Sub-modules:

- :mod:`._io` — primitive prompts + navigation tokens + hardware detection.
- :mod:`._state` — :class:`_WizardState`, persistence, summary,
  schema-default constants.
- :mod:`._collectors` — every ``_collect_*`` helper (webhook /
  trainer hyperparams / safety / compliance / risk_assessment /
  governance / retention / monitoring / benchmark / judge /
  synthetic / GaLore / RoPE / NEFTune) plus strategy choice +
  use-case preset registry.
- :mod:`._byod` — Phase 11.5 / 12.5 BYOD inline ingest + audit
  helpers + quickstart-template prelude.
- :mod:`._orchestrator` — step definitions, ``_drive_wizard_steps``,
  ``_apply_strict_tier_coercion``, public ``run_wizard``.

This ``__init__`` re-exports the public API + the private symbols
covered by the wizard test-suite (``tests/test_wizard_*.py``); the
re-export discipline keeps ``from forgelm import wizard`` reachable
identically to the v0.5.5 monolith, so existing test fixtures and
external callers do not need to change.
"""

from __future__ import annotations

# This package's ``__init__`` is intentionally a re-export shim — the
# wizard test-suite (``tests/test_wizard_*.py``) imports private
# helpers (``_print``, ``_collect_*``, ``_save_wizard_state``, etc.)
# directly off ``forgelm.wizard``.  Ruff's F401 "imported but unused"
# rule treats every re-export as dead code; the standard idiom for
# "we know, this is the package surface" is the per-file noqa below.
# ruff: noqa: F401
# Public entry point + navigation exceptions
from ._byod import (
    _AUDIT_LARGE_FILE_THRESHOLD_BYTES,  # noqa: F401  -- re-export for tests (pyflakes/Prospector compat)
    _BYOD_LOCAL_NOT_FOUND,
    _INGEST_SUPPORTED_EXTENSIONS,
    _directory_has_ingestible_files,
    _finalize_quickstart_path,
    _maybe_run_quickstart_template,
    _offer_audit_for_jsonl,
    _offer_ingest_for_directory,
    _prompt_dataset_path_with_ingest_offer,
    _resolve_byod_dataset_path,
    _validate_local_jsonl,
)
from ._collectors import (
    _GALORE_OPTIMIZERS,  # noqa: F401  -- re-export for tests (pyflakes/Prospector compat)
    _MANUAL_USE_CASE,
    _PREFERENCE_COLUMNS_HINT,
    _RISK_TIERS,
    _STRATEGY_CHOICES,
    _STRICT_RISK_TIERS,
    _collect_benchmark,
    _collect_compliance_metadata,
    _collect_data_governance,
    _collect_galore_config,
    _collect_judge,
    _collect_monitoring,
    _collect_neftune_alpha,
    _collect_retention,
    _collect_risk_assessment,
    _collect_rope_scaling,
    _collect_safety_config,
    _collect_synthetic,
    _collect_trainer_hyperparameters,
    _collect_webhook_config,
    _default_safety_probes_path,
    _parse_webhook_value,
    _select_strategy,
    _select_use_case,
    _StrategyChoice,
    _wizard_use_case_presets,
)
from ._io import (
    _BACK_TOKENS,  # noqa: F401  -- re-export for tests (pyflakes/Prospector compat)
    _CANCEL_TOKENS,
    _HF_HUB_ID_RE,
    _PLATFORM,
    _RESET_TOKENS,
    WizardBack,
    WizardCancel,
    WizardReset,
    _check_navigation_token,
    _detect_hardware,
    _print,
    _prompt,
    _prompt_choice,
    _prompt_float,
    _prompt_int,
    _prompt_optional_list,
    _prompt_required,
    _prompt_yes_no,
)
from ._orchestrator import (
    _STEPS,  # noqa: F401  -- re-export for tests (pyflakes/Prospector compat)
    WizardOutcome,
    _apply_strict_tier_coercion,
    _drive_wizard_steps,
    _is_beginner,
    _load_initial_state_from_yaml,
    _maybe_resume_state,
    _next_free_filename,
    _persist_state,
    _print_preflight_checklist,
    _print_tutorial,
    _prompt_unique_filename,
    _run_full_wizard,
    _run_full_wizard_outcome,
    _step_compliance,
    _step_dataset,
    _step_evaluation,
    _step_model,
    _step_strategy,
    _step_trainer,
    _step_training_params,
    _step_use_case,
    _step_welcome,
    _StepDef,
    _validate_generated_config,
    run_wizard,
    run_wizard_full,
)
from ._state import (
    _STATE_VERSION,  # noqa: F401  -- re-export for tests (pyflakes/Prospector compat)
    DEFAULT_BATCH_SIZE,
    DEFAULT_DROPOUT,
    DEFAULT_EPOCHS,
    DEFAULT_LORA_ALPHA,
    DEFAULT_LORA_R,
    DEFAULT_LR,
    DEFAULT_MAX_LENGTH,
    POPULAR_MODELS,
    TARGET_MODULE_PRESETS,
    _clear_wizard_state,
    _flatten_dict,
    _load_wizard_state,
    _print_step_diff,
    _print_wizard_summary,
    _save_config_to_file,
    _save_wizard_state,
    _strategy_label,
    _strip_internal_meta,
    _wizard_state_dir,
    _wizard_state_path,
    _WizardState,
)

# ``__all__`` is a public re-export contract for ``from forgelm.wizard
# import *`` consumers.  Only the truly public names appear here; the
# many ``_private`` symbols above remain importable but aren't
# advertised.
__all__ = [
    "run_wizard",
    "run_wizard_full",
    "WizardOutcome",
    "WizardBack",
    "WizardReset",
    "WizardCancel",
    "POPULAR_MODELS",
    "TARGET_MODULE_PRESETS",
    "DEFAULT_LORA_R",
    "DEFAULT_LORA_ALPHA",
    "DEFAULT_EPOCHS",
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_LR",
    "DEFAULT_MAX_LENGTH",
    "DEFAULT_DROPOUT",
]
