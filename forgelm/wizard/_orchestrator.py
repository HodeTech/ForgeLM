"""Step machine + public entry point for the wizard.

The orchestrator threads a single :class:`_WizardState` through every
step, persists after each step, and supports back-step / reset.
Each step is registered as a small :class:`_StepDef`.  Steps return
``None`` (success), raise :class:`WizardBack` (operator typed
``back``), or raise :class:`WizardReset` (operator typed ``reset``).
"""

from __future__ import annotations

import copy
import logging
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("forgelm.wizard")

from ._byod import (
    _finalize_quickstart_path,
    _maybe_run_quickstart_template,
    _prompt_dataset_path_with_ingest_offer,
)
from ._collectors import (
    _PREFERENCE_COLUMNS_HINT,
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
    _select_strategy,
    _select_use_case,
)
from ._io import (
    _HF_HUB_ID_RE,
    _PLATFORM,
    WizardBack,
    WizardCancel,
    WizardReset,
    _detect_hardware,
    _print,
    _prompt,
    _prompt_int,
    _prompt_required,
    _prompt_yes_no,
)
from ._state import (
    _MANUAL_USE_CASE,
    DEFAULT_BATCH_SIZE,
    DEFAULT_DROPOUT,
    DEFAULT_EPOCHS,
    DEFAULT_GRAD_ACCUM,
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
    _strip_internal_meta,
    _wizard_state_path,
    _WizardState,
)

# ---------------------------------------------------------------------------
# Strict-tier auto-coercion — Phase 22 / G8.  ``ForgeConfig`` raises
# ``ConfigError`` at load time when ``risk_classification ∈
# {high-risk, unacceptable}`` and ``evaluation.safety.enabled =
# False``; the wizard front-stops the same gate.
# ---------------------------------------------------------------------------


def _apply_strict_tier_coercion(config: Dict[str, Any], compliance: Dict[str, Any]) -> None:
    """Mutate *config* in place to satisfy F-compliance-110 strict-tier requirements.

    Idempotent — called twice in the wizard flow (end of compliance step
    + end of evaluation step) so a Ctrl-C between the two still produces
    a loadable YAML.  The notice line prints only once per wizard run
    (A4 / review-cycle 3): tracked via the ``_wizard_meta`` namespace
    which ``_strip_internal_meta`` removes before save, so the flag
    never reaches the operator's YAML.
    """
    if compliance.get("risk_classification") not in _STRICT_RISK_TIERS:
        return
    meta = config.setdefault("_wizard_meta", {})
    if not meta.get("strict_tier_announced"):
        _print(
            "\n  Risk classification is high-risk / unacceptable — Article 9 "
            "(F-compliance-110) requires safety evaluation enabled and Article 14 "
            "requires the human-approval staging gate.  Auto-enabling both."
        )
        meta["strict_tier_announced"] = True
    evaluation = config.setdefault("evaluation", {})
    evaluation.setdefault("auto_revert", True)
    evaluation["require_human_approval"] = True
    safety = evaluation.get("safety")
    if not isinstance(safety, dict) or not safety.get("enabled"):
        evaluation["safety"] = {
            "enabled": True,
            "test_prompts": _default_safety_probes_path(),
            "scoring": "binary",
            "track_categories": True,
            "severity_thresholds": {"critical": 0, "high": 0.01, "medium": 0.05},
        }


# ---------------------------------------------------------------------------
# Beginner / expert tutorial-paragraph helpers
# ---------------------------------------------------------------------------


def _is_beginner(state: _WizardState) -> bool:
    return state.experience == "beginner"


def _cached_hardware(state: _WizardState) -> Dict[str, Any]:
    """Return ``state.hardware``, populating it on first call.

    C16 / review-cycle 3: ``_detect_hardware`` lazy-imports torch and
    enumerates CUDA devices; both are slow (~50–200 ms).  The welcome
    step + the post-save pre-flight checklist both want the result, so
    cache on the per-run :class:`_WizardState`.  The cache lives only
    for the wizard's lifetime — never persisted (excluded from the YAML
    snapshot in :func:`_persist_state`).

    **Caveat (A3 doc note, review-cycle 3 follow-up):** the cache is
    populated once at welcome-step time and reused for the rest of the
    run.  An operator who hot-plugs a GPU mid-session would still see
    the original (no-GPU) snapshot in the pre-flight checklist.
    Acceptable: hot-plug mid-wizard is an edge case far below the
    50–200 ms/import cost of refreshing on every step, and the
    operator can always exit-and-resume to refresh.
    """
    if state.hardware is None:
        state.hardware = _detect_hardware()
    return state.hardware


def _print_tutorial(state: _WizardState, lines: List[str]) -> None:
    """Print a 2-3-line tutorial paragraph in beginner mode; skip in expert mode."""
    if not _is_beginner(state):
        return
    _print()
    for line in lines:
        _print(f"  · {line}")


# ---------------------------------------------------------------------------
# Step definitions
# ---------------------------------------------------------------------------


@dataclass
class _StepDef:
    """A single step in the full-wizard flow."""

    label: str
    runner: Callable[[_WizardState], None]


@dataclass(frozen=True)
class WizardOutcome:
    """Result of a wizard run.

    Two orthogonal signals:
        - ``config_path``: filesystem path to the saved YAML, or ``None``
          when the wizard exited without writing anything (cancelled,
          Ctrl-C, non-tty refusal).
        - ``start_training``: ``True`` when the operator answered "yes"
          to "Start training now?" — the CLI dispatcher uses this to
          differentiate exit code 0 (saved + start) from exit code 5
          (cancelled, no YAML).  Saved-and-deferred is also exit 0
          because the YAML deliverable was produced.
    """

    config_path: Optional[str] = None
    start_training: bool = False

    @property
    def cancelled(self) -> bool:
        """True when the wizard never produced a YAML."""
        return self.config_path is None


def _step_welcome(state: _WizardState) -> None:
    """Step 1: welcome + experience toggle + hardware detection."""
    _print("\n[1/9] Welcome")
    _print(
        "\n  ForgeLM walks you through producing a validated `config.yaml`.  "
        "Type `back` / `b` at any prompt to return to the previous step, "
        "`reset` / `r` to clear answers, `cancel` / `q` to exit cleanly."
    )
    if _prompt_yes_no(
        "First-time user?  (beginner mode prefixes each step with a short tutorial paragraph)",
        default=False,
    ):
        state.experience = "beginner"
    else:
        state.experience = "expert"
    hw = _cached_hardware(state)
    if hw["gpu_available"]:
        _print(f"  GPU detected: {hw['gpu_name']} ({hw['vram_gb']} GB VRAM, CUDA {hw['cuda_version']})")
    else:
        _print("  No GPU detected. Training will use CPU (very slow for real workloads).")
    suggested_backend = "transformers"
    if hw["gpu_available"] and _PLATFORM == "linux":
        suggested_backend = "unsloth"
        _print("  Recommended backend: unsloth (Linux + GPU detected)")
    elif hw["gpu_available"]:
        _print("  Recommended backend: transformers (Unsloth requires Linux)")
    # PR-D-A4 (PR-E review fix): use a chained ``setdefault`` so an
    # existing ``model.backend`` from ``--wizard-start-from`` survives
    # the hardware-driven suggestion.  Prior code used
    # ``setdefault("model", {})["backend"] = ...`` which only guarded
    # the OUTER dict — the nested ``backend`` key was always
    # overwritten.
    state.config.setdefault("model", {}).setdefault("backend", suggested_backend)


def _step_use_case(state: _WizardState) -> None:
    """Step 2: use-case preselect.

    PR-D-A3 (PR-E review fix): when the wizard was started from an
    existing YAML (``state.use_case == _MANUAL_USE_CASE`` and
    ``state.config`` already carries ``model.name_or_path`` /
    ``training.trainer_type``), the operator's intent is "iterate on
    this config" — we skip the use-case prompt entirely so a bare
    Enter doesn't silently overwrite their model + trainer choices
    with the first template's preset.  The pre-cycle behaviour
    (greenfield wizard run) is preserved for fresh runs.
    """
    _print("\n[2/9] Use-case")
    _print_tutorial(
        state,
        [
            "A use-case picks sensible defaults for trainer + model + dataset.",
            "Pick `custom` to fill every field manually.",
            "Whatever you pick, you can still override every later step.",
        ],
    )
    has_existing_choices = bool(
        state.config.get("model", {}).get("name_or_path") or state.config.get("training", {}).get("trainer_type")
    )
    if state.use_case == _MANUAL_USE_CASE and has_existing_choices:
        _print(
            "  Existing model / trainer choices detected — skipping use-case preset "
            "to avoid clobbering them.  Step 3 (model) + Step 5 (trainer) will use "
            "your loaded values as defaults."
        )
        return
    use_case_key, preset = _select_use_case()
    state.use_case = use_case_key
    # Use ``setdefault`` for both nested keys so existing values
    # survive a use-case re-pick on a partially-loaded YAML.
    if preset.get("model"):
        state.config.setdefault("model", {}).setdefault("name_or_path", preset["model"])
    if preset.get("trainer_type"):
        state.config.setdefault("training", {}).setdefault("trainer_type", preset["trainer_type"])


def _step_model(state: _WizardState) -> None:
    """Step 3: model selection."""
    _print("\n[3/9] Model Selection")
    _print_tutorial(
        state,
        [
            "Pick the base model your fine-tune will adapt.",
            "Smaller models train faster but cap your reasoning ceiling.",
            "QLoRA on a 7B model fits comfortably in 12 GB VRAM.",
        ],
    )
    preset_default = state.config.get("model", {}).get("name_or_path")
    options = list(POPULAR_MODELS)
    options.append("Custom (enter your own)")
    if preset_default and preset_default in POPULAR_MODELS:
        default_idx = POPULAR_MODELS.index(preset_default) + 1
    elif preset_default:
        # Preset from step 2 (use-case) isn't in POPULAR_MODELS — default
        # to the custom slot and pre-fill the prompt with the operator's
        # earlier choice so a bare ``Enter`` keeps it.
        default_idx = len(options)
    else:
        default_idx = 1
    from ._io import _prompt_choice

    chosen = _prompt_choice("Choose a model:", options, default=default_idx)
    if chosen.startswith("Custom"):
        if preset_default and preset_default not in POPULAR_MODELS:
            model_name = _prompt("HuggingFace model name or local path", preset_default)
            if not model_name.strip():
                model_name = preset_default
        else:
            model_name = _prompt_required("HuggingFace model name or local path")
        # P13: nudge the operator when the value looks like a typo.  We
        # can't validate that an HF Hub ID actually resolves without
        # network, but ``<org>/<name>`` is the universal shape — and a
        # local path either points at an existing directory or is
        # operator intent we shouldn't second-guess.
        from pathlib import Path as _P

        looks_like_hub = "/" in model_name and not model_name.startswith((".", "/", "~"))
        if looks_like_hub and not _HF_HUB_ID_RE.match(model_name):
            _print(
                f"  ⚠ '{model_name}' doesn't look like a valid HF Hub ID "
                "(expected '<org>/<name>') and isn't a local path — double-check before training."
            )
        elif not looks_like_hub and not _P(model_name).expanduser().exists():
            _print(
                f"  ⚠ '{model_name}' is not an existing local path and is not in HF Hub format — "
                "the trainer will try to resolve it on HuggingFace at startup."
            )
    else:
        model_name = chosen
    state.config.setdefault("model", {})["name_or_path"] = model_name


def _step_strategy(state: _WizardState) -> None:
    """Step 4: strategy + LoRA params."""
    _print("\n[4/9] Fine-Tuning Strategy")
    _print_tutorial(
        state,
        [
            "QLoRA is the safest default: 4-bit base + low-rank adapter.",
            "DoRA / PiSSA / rsLoRA tweak the adapter for better convergence.",
            "GaLore replaces adapters with full-parameter training via gradient projection.",
        ],
    )
    # PR-D-A1 (PR-E review fix): pass existing strategy hints to
    # ``_select_strategy`` so the prompt default reflects a loaded
    # ``lora.method`` + ``model.load_in_4bit`` + ``training.galore_enabled``
    # triplet.  Prior code always defaulted to QLoRA, silently
    # regressing operators iterating on DoRA / PiSSA / rsLoRA / GaLore.
    _existing_lora_for_strategy = state.config.get("lora", {})
    _existing_model_for_strategy = state.config.get("model", {})
    _existing_training_for_strategy = state.config.get("training", {})
    strategy = _select_strategy(
        existing_method=_existing_lora_for_strategy.get("method"),
        existing_load_in_4bit=_existing_model_for_strategy.get("load_in_4bit"),
        existing_galore=bool(_existing_training_for_strategy.get("galore_enabled", False)),
    )
    from ._io import _prompt_choice

    # Match an existing ``lora.target_modules`` list against the three
    # preset shapes so the operator's prior pick becomes the default.
    target_preset_options = [
        "standard (q_proj, v_proj)",
        "extended (q, k, v, o)",
        "full (all linear layers)",
    ]
    target_default_idx = 1
    existing_modules = _existing_lora_for_strategy.get("target_modules")
    if isinstance(existing_modules, list):
        existing_set = set(existing_modules)
        for i, key in enumerate(("standard", "extended", "full"), 1):
            if existing_set == set(TARGET_MODULE_PRESETS[key]):
                target_default_idx = i
                break
    target_preset = _prompt_choice(
        "Target modules:",
        target_preset_options,
        default=target_default_idx,
    )
    preset_key = target_preset.split(" ")[0]
    target_modules = TARGET_MODULE_PRESETS.get(preset_key, TARGET_MODULE_PRESETS["standard"])
    # E2 (review-cycle 3): inline rationale on the high-impact operator
    # knobs.  Each prompt extends its question text with a one-clause
    # hint operators most often need (without adding a separate full
    # rationale catalogue — see ``docs/design/wizard_mode.md`` for
    # scope rationale).
    # E3 (PR-D): when the wizard was started from an existing YAML the
    # operator's prior LoRA values become the prompt defaults so a
    # bare Enter keeps them.  Falls back to the schema defaults
    # (DEFAULT_LORA_R / DEFAULT_LORA_R*2) when ``state.config`` has
    # no ``lora`` block yet.
    lora_r = _prompt_int(
        "LoRA rank (r) — capacity of the adapter (higher = more expressive, more VRAM; "
        "8 is the schema default, 16 is a common 'a bit stronger' bump, 64+ rarely helps)",
        int(_existing_lora_for_strategy.get("r", DEFAULT_LORA_R)),
        min_val=1,
        max_val=512,
    )
    lora_alpha = _prompt_int(
        "LoRA alpha — adapter scaling (the 'alpha = 2 × r' convention is what most papers use)",
        int(_existing_lora_for_strategy.get("alpha", lora_r * 2)),
        min_val=1,
        max_val=1024,
    )
    state.config.setdefault("model", {})["load_in_4bit"] = strategy.load_in_4bit
    state.config["model"].setdefault("trust_remote_code", False)
    # P16: when QLoRA (load_in_4bit=True) is selected, emit the two
    # bnb-related quant flags the web wizard already writes.  Both have
    # schema defaults but writing them explicitly keeps the YAML self-
    # documenting and matches ``site/js/wizard.js`` (parity).
    if strategy.load_in_4bit:
        state.config["model"].setdefault("bnb_4bit_quant_type", "nf4")
        state.config["model"].setdefault("bnb_4bit_compute_dtype", "auto")
    # PR-D-A1 (PR-E review fix): the prompt-derived values
    # (``lora.r``, ``lora.alpha``, ``lora.method``, ``lora.target_modules``)
    # always reflect the operator's intent for THIS run — assign them
    # directly.  ``dropout``, ``bias``, and ``task_type`` are NOT
    # prompted, so use ``setdefault`` to preserve any existing values
    # the start-from YAML might carry (custom dropout for a heavily-
    # regularised run, ``bias=lora_only``, etc.).
    lora_block = state.config.setdefault("lora", {})
    lora_block["r"] = lora_r
    lora_block["alpha"] = lora_alpha
    lora_block["method"] = strategy.method
    lora_block["target_modules"] = target_modules
    lora_block.setdefault("dropout", DEFAULT_DROPOUT)
    lora_block.setdefault("bias", "none")
    lora_block.setdefault("task_type", "CAUSAL_LM")
    state.config.setdefault("training", {})["galore_enabled"] = strategy.use_galore
    state.config.setdefault("_wizard_meta", {})["use_galore"] = strategy.use_galore


def _step_trainer(state: _WizardState) -> None:
    """Step 5: trainer + per-trainer hyperparameters."""
    _print("\n[5/9] Training Objective")
    _print_tutorial(
        state,
        [
            "SFT is supervised fine-tuning — the standard instruction-tuning recipe.",
            "DPO / SimPO / KTO / ORPO align via preferences (chosen vs rejected).",
            "GRPO is reasoning RL (DeepSeek-R1 style); needs a reward signal.",
        ],
    )
    objectives = [
        "sft — Supervised Fine-Tuning (standard instruction tuning)",
        "dpo — Direct Preference Optimization (chosen/rejected pairs)",
        "simpo — Simple Preference Optimization (no reference model, lower memory)",
        "kto — Binary feedback alignment (thumbs up/down, practical for production)",
        "orpo — Odds Ratio Preference Optimization (SFT + alignment in one stage)",
        "grpo — Group Relative Policy Optimization (reasoning RL, like DeepSeek-R1)",
    ]
    preset = state.config.get("training", {}).get("trainer_type", "sft")
    default_idx = next(
        (i for i, opt in enumerate(objectives, 1) if opt.startswith(f"{preset} —")),
        1,
    )
    from ._io import _prompt_choice

    chosen = _prompt_choice("Choose your training objective:", objectives, default=default_idx)
    trainer_type = chosen.split(" — ")[0].strip().lower()
    dataset_format_hint = {
        "sft": "Columns: System (opt), User/instruction, Assistant/output — or 'messages' list",
        "dpo": _PREFERENCE_COLUMNS_HINT,
        "simpo": _PREFERENCE_COLUMNS_HINT,
        "orpo": _PREFERENCE_COLUMNS_HINT,
        "kto": "Columns: prompt, completion, label (boolean: true=good, false=bad)",
        "grpo": "Columns: prompt (model generates responses during training)",
    }.get(trainer_type, "Standard format")
    _print(f"  Dataset format: {dataset_format_hint}")
    training_block = state.config.setdefault("training", {})
    # Snapshot the existing per-trainer knobs *before* writing the new
    # ``trainer_type`` so the helper sees the loaded YAML's prior values
    # as prompt defaults (rather than the schema literals) on a
    # ``--wizard-start-from`` rerun.
    existing_training = dict(training_block)
    training_block["trainer_type"] = trainer_type
    hyperparams = _collect_trainer_hyperparameters(trainer_type, existing=existing_training)
    training_block.update(hyperparams)


def _step_dataset(state: _WizardState) -> None:
    """Step 6: dataset path + optional Article 10 governance."""
    _print("\n[6/9] Dataset")
    _print_tutorial(
        state,
        [
            "Point the wizard at an HF Hub dataset ID, a local JSONL, or a directory of raw documents.",
            "Directories of raw documents trigger inline ingestion (PDF / DOCX / EPUB / TXT / MD).",
            "JSONL files are auto-audited (length / language / dedup / PII / secrets).",
        ],
    )
    # E3 (PR-D): when state already carries a dataset path (operator
    # started from an existing YAML), offer to keep it instead of
    # forcing a re-prompt that would re-trigger ingestion / audit.
    existing_dataset = state.config.get("data", {}).get("dataset_name_or_path")
    if existing_dataset:
        _print(f"  Existing dataset: {existing_dataset!r}")
    if existing_dataset and _prompt_yes_no("  Keep this dataset?", default=True):
        dataset_path = existing_dataset
    else:
        dataset_path = _prompt_dataset_path_with_ingest_offer(
            "HuggingFace dataset name or local file path (or directory of raw documents)",
        )
    data_block = state.config.setdefault("data", {})
    data_block["dataset_name_or_path"] = dataset_path
    data_block.setdefault("shuffle", True)
    data_block.setdefault("clean_text", True)
    data_block.setdefault("add_eos", True)
    # PR-D-B5 (PR-E review fix): pass existing governance dict so the
    # operator iterating from a YAML doesn't re-type the Article 10
    # free-text fields.
    governance = _collect_data_governance(
        mandatory=False,
        existing=data_block.get("governance") or {},
    )
    if governance:
        data_block["governance"] = governance


def _step_training_params(state: _WizardState) -> None:
    """Step 7: epochs + batch + max_length + RoPE + NEFTune + OOM + GaLore advanced."""
    _print("\n[7/9] Training Parameters")
    _print_tutorial(
        state,
        [
            "Epochs / batch / learning_rate are the three knobs you'll tune most often.",
            "Long-context (>4096) automatically prompts for RoPE scaling.",
            "OOM recovery halves the batch size on CUDA out-of-memory and retries.",
        ],
    )
    # E2: inline rationale on the three most-tuned operator knobs.
    # E3 (PR-D): defaults pull from the existing config when the
    # wizard was started from a YAML so the operator can iterate.
    existing_training = state.config.get("training", {})
    existing_model = state.config.get("model", {})
    epochs = _prompt_int(
        "Number of epochs — full passes over the training set "
        "(SFT typically wants 1-3; DPO/SimPO/ORPO 1-2; 5+ usually overfits on instruction-tuning corpora)",
        int(existing_training.get("num_train_epochs", DEFAULT_EPOCHS)),
        min_val=1,
        max_val=1000,
    )
    batch_size = _prompt_int(
        "Batch size per device — effective tokens/step is "
        "``batch_size × gradient_accumulation_steps × max_length``; "
        "4 fits 7B QLoRA in 12 GB VRAM with the schema-default 2048 max_length",
        int(existing_training.get("per_device_train_batch_size", DEFAULT_BATCH_SIZE)),
        min_val=1,
        max_val=512,
    )
    max_length = _prompt_int(
        "Max sequence length — tokens per training example; "
        "2048 is safe for instruction tuning, raise for long-form RAG / code-assist "
        "(>4096 triggers an automatic RoPE-scaling prompt)",
        int(existing_model.get("max_length", DEFAULT_MAX_LENGTH)),
        min_val=64,
        max_val=131072,
    )
    output_dir = _prompt("Output directory", existing_training.get("output_dir", "./checkpoints"))
    rope_scaling = _collect_rope_scaling(
        max_length,
        existing=existing_training.get("rope_scaling")
        if isinstance(existing_training.get("rope_scaling"), dict)
        else None,
    )
    neftune_alpha = _collect_neftune_alpha()
    use_oom_recovery = _prompt_yes_no(
        "Enable OOM recovery? (auto-halves batch size on CUDA out-of-memory, then retries)",
        default=False,
    )
    use_galore = bool(state.config.get("_wizard_meta", {}).get("use_galore"))
    # ``existing_training`` already carries the loaded ``galore_*`` keys
    # at the top level (TrainingConfig is a flat block); pass it through
    # so the collector can offer to keep the prior tuning.
    galore_config = _collect_galore_config(use_galore, existing=existing_training)
    model_block = state.config.setdefault("model", {})
    model_block["max_length"] = max_length
    training_block = state.config.setdefault("training", {})
    training_block["output_dir"] = output_dir
    training_block.setdefault("final_model_dir", "final_model")
    training_block.setdefault("merge_adapters", False)
    training_block["num_train_epochs"] = epochs
    training_block["per_device_train_batch_size"] = batch_size
    training_block.setdefault("gradient_accumulation_steps", DEFAULT_GRAD_ACCUM)
    training_block.setdefault("learning_rate", DEFAULT_LR)
    training_block.setdefault("warmup_ratio", 0.1)
    training_block.setdefault("weight_decay", 0.01)
    training_block.setdefault("eval_steps", 200)
    training_block.setdefault("save_steps", 200)
    training_block.setdefault("save_total_limit", 3)
    training_block.setdefault("packing", False)
    training_block.update(galore_config)
    if neftune_alpha is not None:
        training_block["neftune_noise_alpha"] = neftune_alpha
    if rope_scaling is not None:
        training_block["rope_scaling"] = rope_scaling
    if use_oom_recovery:
        training_block["oom_recovery"] = True
        training_block["oom_recovery_min_batch_size"] = 1


def _step_compliance(state: _WizardState) -> None:
    """Step 8: compliance / risk_classification / risk_assessment / governance / retention / monitoring."""
    _print("\n[8/9] Compliance + risk")
    _print_tutorial(
        state,
        [
            "Configure EU AI Act compliance metadata for Annex IV documentation.",
            "high-risk / unacceptable risk_classification triggers Article 9 / 14 auto-coercion.",
            "Article 10 data.governance and retention horizons are optional unless you're high-risk.",
        ],
    )
    if not _prompt_yes_no(
        "Configure EU AI Act compliance metadata?",
        default=False,
    ):
        return
    compliance = _collect_compliance_metadata()
    state.config["compliance"] = compliance
    risk = compliance.get("risk_classification", "minimal-risk")
    risk_assessment = _collect_risk_assessment(risk)
    if risk_assessment:
        state.config["risk_assessment"] = risk_assessment
    is_strict = risk in _STRICT_RISK_TIERS
    existing_governance = state.config.get("data", {}).get("governance")
    if existing_governance and not is_strict:
        # Step 6 (dataset) already collected the optional Article 10 block.
        # Re-prompting under non-strict tier would silently overwrite the
        # operator's earlier answers — skip and keep what they typed.
        _print("  Article 10 data.governance already populated (Step 6 — Dataset). Keeping previous answers.")
    else:
        # PR-D-B5: thread existing governance values through so a
        # strict-tier iteration uses prior answers as defaults.
        governance = _collect_data_governance(
            mandatory=is_strict,
            existing=existing_governance or {},
        )
        if governance:
            state.config.setdefault("data", {})["governance"] = governance
    retention = _collect_retention()
    if retention:
        state.config["retention"] = retention
    monitoring = _collect_monitoring()
    if monitoring:
        state.config["monitoring"] = monitoring
    # B5: fire strict-tier coercion at the end of the compliance step too.
    # ``_step_evaluation`` calls it again at flow end (idempotent), but
    # firing here means the operator who Ctrl-C's between steps 8 and 9
    # still gets a loadable YAML when they manually save state.
    _apply_strict_tier_coercion(state.config, compliance)


def _step_evaluation(state: _WizardState) -> None:
    """Step 9 (operations): evaluation gates, webhooks, synthetic, save."""
    _print("\n[9/9] Operations + evaluation")
    _print_tutorial(
        state,
        [
            "Evaluation gates (auto_revert + safety + benchmark + judge) protect against regressions.",
            "Webhooks notify Slack / Teams / your CI when training starts, succeeds, or fails.",
            "Synthetic-data lets a stronger teacher model generate extra training examples.",
        ],
    )
    # PR-D-A2 (PR-E review fix): preserve the operator's existing
    # evaluation block when iterating from a YAML.  Three changes
    # vs. the pre-fix code:
    #   1. Seed ``evaluation`` from ``state.config["evaluation"]`` so
    #      sibling fields (``benchmark.tasks``, ``llm_judge.model``,
    #      ``safety.severity_thresholds``) survive a "press Enter" pass.
    #   2. Default each gate's enable-prompt to whatever was loaded so
    #      operators with auto-revert + judge already configured don't
    #      need to re-answer "yes" to keep them.
    #   3. ``_collect_*`` returning ``None`` (operator declined) leaves
    #      the existing block intact rather than wiping it — declining
    #      is "don't reconfigure", not "delete".
    existing_evaluation = state.config.get("evaluation") or {}
    evaluation: Dict[str, Any] = copy.deepcopy(existing_evaluation)
    auto_revert_default = bool(existing_evaluation.get("auto_revert", False))
    if _prompt_yes_no(
        "Enable auto-revert (discard model if quality drops)?",
        default=auto_revert_default,
    ):
        evaluation["auto_revert"] = True
        existing_max_loss = existing_evaluation.get("max_acceptable_loss")
        # When auto-revert is on but no explicit threshold is given,
        # emit the web wizard's safe ``2.0`` default rather than leaving
        # the field unset.  Mirrors ``site/js/wizard.js`` so the two
        # surfaces produce equivalent YAMLs for the same answers.
        prompt_default = str(existing_max_loss) if existing_max_loss is not None else ""
        max_loss = _prompt("Max acceptable loss (leave empty for default 2.0)", prompt_default)
        if max_loss.strip():
            try:
                evaluation["max_acceptable_loss"] = float(max_loss)
            except ValueError:
                _print(f"  '{max_loss}' is not a number; using default 2.0.")
                evaluation["max_acceptable_loss"] = 2.0
        else:
            evaluation["max_acceptable_loss"] = 2.0
    else:
        # Explicit "no" — operator turned auto-revert OFF on this run.
        # The deepcopy of ``existing_evaluation`` would otherwise leave
        # ``auto_revert: true`` (and a stale ``max_acceptable_loss``)
        # in the rebuild, ignoring the operator's choice.  Pin the
        # contract: a "no" answer always disables, regardless of the
        # loaded state.
        evaluation["auto_revert"] = False
        evaluation.pop("max_acceptable_loss", None)
    risk = state.config.get("compliance", {}).get("risk_classification", "minimal-risk")
    existing_safety = existing_evaluation.get("safety")
    safety_default_enabled = (risk in _STRICT_RISK_TIERS) or bool(
        isinstance(existing_safety, dict) and existing_safety.get("enabled")
    )
    safety = _collect_safety_config(
        default_enabled=safety_default_enabled,
        existing=existing_safety if isinstance(existing_safety, dict) else None,
    )
    if safety:
        evaluation["safety"] = safety
    else:
        # Operator declined safety eval at the gate prompt.  Drop any
        # loaded safety block so the rebuild reflects the current
        # answer rather than silently re-enabling on a deepcopy.
        evaluation.pop("safety", None)
    # ``_collect_benchmark`` / ``_collect_judge`` accept the loaded
    # block via *existing* so a rerun with a populated benchmark / judge
    # config defaults the gate prompt to "yes" (bare Enter keeps it).
    # An explicit "no" returns ``None`` and pops the block — the
    # explicit-disable contract that lets a rerun actually turn
    # previously-enabled gates off.
    existing_benchmark = existing_evaluation.get("benchmark")
    benchmark = _collect_benchmark(
        existing=existing_benchmark if isinstance(existing_benchmark, dict) else None,
    )
    if benchmark:
        evaluation["benchmark"] = benchmark
    else:
        evaluation.pop("benchmark", None)
    existing_judge = existing_evaluation.get("llm_judge")
    judge = _collect_judge(
        existing=existing_judge if isinstance(existing_judge, dict) else None,
    )
    if judge:
        evaluation["llm_judge"] = judge
    else:
        evaluation.pop("llm_judge", None)
    if evaluation:
        state.config["evaluation"] = evaluation
    webhook_section = _collect_webhook_config()
    if webhook_section:
        state.config["webhook"] = webhook_section
    synthetic = _collect_synthetic()
    if synthetic:
        state.config["synthetic"] = synthetic
    compliance = state.config.get("compliance")
    if compliance:
        _apply_strict_tier_coercion(state.config, compliance)


# NOTE: ``_STEPS`` lists the CLI wizard's nine canonical step IDs.  The
# in-browser wizard at ``site/js/wizard.js::STEPS`` covers the same nine
# user-visible stages but uses different IDs (welcome / use-case / trainer /
# model / dataset / training / compliance / operations / review) and a
# ``trainer ↔ model`` order swap motivated by the web's preset-driven flow.
# Both surfaces reach the same generated YAML; the divergence is by design.
_STEPS: Tuple[_StepDef, ...] = (
    _StepDef("welcome", _step_welcome),
    _StepDef("use-case", _step_use_case),
    _StepDef("model", _step_model),
    _StepDef("strategy", _step_strategy),
    _StepDef("trainer", _step_trainer),
    _StepDef("dataset", _step_dataset),
    _StepDef("training-params", _step_training_params),
    _StepDef("compliance", _step_compliance),
    _StepDef("evaluation", _step_evaluation),
)


# ---------------------------------------------------------------------------
# Resume + step-machine driver
# ---------------------------------------------------------------------------


def _maybe_resume_state() -> _WizardState:
    """Offer to resume an in-progress wizard run."""
    snapshot = _load_wizard_state()
    if snapshot is None:
        return _WizardState()
    completed = snapshot.get("completed_steps", [])
    if not completed:
        return _WizardState()
    if not _prompt_yes_no(
        f"Resume previous wizard run? (completed {len(completed)} step(s): {', '.join(completed)})",
        default=True,
    ):
        _clear_wizard_state()
        return _WizardState()
    return _WizardState(
        experience=snapshot.get("experience", "expert"),
        use_case=snapshot.get("use_case", _MANUAL_USE_CASE),
        current_step=snapshot.get("current_step", 0),
        completed_steps=list(completed),
        config=dict(snapshot.get("config", {})),
    )


def _persist_state(state: _WizardState) -> None:
    """Snapshot the wizard's in-memory state to disk."""
    _save_wizard_state(
        {
            "experience": state.experience,
            "use_case": state.use_case,
            "current_step": state.current_step,
            "completed_steps": state.completed_steps,
            "config": state.config,
        }
    )


def _drive_wizard_steps(state: _WizardState) -> _WizardState:
    """Run the wizard's step machine, honouring back / reset.

    ``WizardBack`` rolls *state.config* back to the snapshot taken before
    the step ran — partial mutations made by a half-completed step would
    otherwise leak into the previous step's prompts.

    ``WizardReset`` re-loops with a fresh state instead of returning;
    returning would let ``_run_full_wizard`` treat the reset as a
    completed run and try to save an empty config.
    """
    while state.current_step < len(_STEPS):
        step = _STEPS[state.current_step]
        prev_config = copy.deepcopy(state.config)
        try:
            step.runner(state)
        except WizardBack:
            state.config = prev_config
            if state.current_step == 0:
                _print("  Already at the first step.")
                continue
            state.current_step -= 1
            if state.completed_steps and state.completed_steps[-1] == _STEPS[state.current_step].label:
                state.completed_steps.pop()
            _persist_state(state)
            continue
        except WizardReset:
            _print("  Resetting wizard state.")
            _clear_wizard_state()
            state = _WizardState()
            continue
        # Strip the ``_wizard_meta`` namespace before showing the operator
        # what changed — those are internal scratch keys (e.g.
        # ``_wizard_meta.use_galore``) the strategy step uses to thread
        # signals to the training-params step.  They never reach the
        # YAML (``_strip_internal_meta`` is applied at save time) so they
        # shouldn't appear in the live diff either.
        _print_step_diff(_strip_internal_meta(prev_config), _strip_internal_meta(state.config), step.label)
        if step.label not in state.completed_steps:
            state.completed_steps.append(step.label)
        state.current_step += 1
        _persist_state(state)
    return state


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def run_wizard(start_from: Optional[str] = None) -> Optional[str]:
    """Run the interactive configuration wizard (back-compat shim).

    Returns the path to the generated config file when the user opts
    to start training immediately, or ``None`` for either "deferred"
    or "cancelled" — callers that need to differentiate the two should
    use :func:`run_wizard_full` (returns a :class:`WizardOutcome`).

    *start_from* (E3 / PR-D): pre-populate the wizard from an existing
    YAML so each step's prompts default to the operator's prior
    answers — see :func:`run_wizard_full` for the full contract.
    """
    outcome = run_wizard_full(start_from=start_from)
    if outcome.start_training and outcome.config_path:
        return outcome.config_path
    return None


def run_wizard_full(start_from: Optional[str] = None) -> WizardOutcome:
    """Run the wizard and return a structured outcome.

    Refuses to launch when stdin isn't a TTY (piped input, CI cron job)
    because silent ``EOFError`` answers produce empty configs that look
    like successful runs.  Operators who want a deterministic scripted
    config should reach for ``forgelm quickstart <template>`` instead.

    When *start_from* is supplied the wizard preloads its state from
    the YAML at that path (E3 / PR-D), validates it against
    ``ForgeConfig``, and skips the quickstart-template prelude — the
    operator's intent is "iterate on this existing config", not "pick
    a fresh template".  The save flow defaults to overwriting
    *start_from*; the existing :func:`_prompt_unique_filename` overwrite
    confirmation still fires.
    """
    import sys as _sys

    if not _sys.stdin.isatty():
        _print(
            "  ⚠ Wizard refused to launch: stdin is not a TTY.\n"
            "    The interactive wizard needs a real terminal — piped input "
            "(`forgelm --wizard < answers.txt`) and CI cron jobs produce "
            "silently-empty configs.  For deterministic scripted config "
            "generation use:\n"
            "      forgelm quickstart <template-name>\n"
            "    Available templates: "
            "customer-support, code-assistant, domain-expert, medical-qa-tr, grpo-math."
        )
        return WizardOutcome(config_path=None, start_training=False)
    if start_from is not None:
        # E3: skip the quickstart prelude — operator's intent is "iterate
        # on this YAML", not "pick a curated template".
        return _run_full_wizard_outcome(start_from=start_from)
    # The quickstart-template prelude runs BEFORE the step machine, so its
    # prompts' navigation tokens have no handler of their own.  Catch them
    # here (F-P7-OPUS-08): ``cancel`` exits cleanly; ``back``/``reset``
    # before any step have nothing to go back to, so fall through to the
    # full wizard rather than crashing with an uncaught traceback.
    try:
        quickstart_path = _maybe_run_quickstart_template()
        if quickstart_path is not None:
            # Quickstart template path: ``_finalize_quickstart_path`` returns
            # the path when the operator wants to train now, else None.
            # Either way the YAML was saved by the quickstart machinery.
            finalized = _finalize_quickstart_path(quickstart_path)
            return WizardOutcome(
                config_path=quickstart_path,
                start_training=finalized is not None,
            )
    except WizardCancel:
        _print("\n  Wizard cancelled.  No config written.")
        return WizardOutcome(config_path=None, start_training=False)
    except (WizardBack, WizardReset):
        _print("\n  Falling back to the full configuration wizard.")
        return _run_full_wizard_outcome()
    _print("\n  Falling back to the full configuration wizard.")
    return _run_full_wizard_outcome()


def _canonical_start_from(path: str) -> str:
    """Return *path* with ``~`` expansion applied, as a string.

    PR-D-B6 (PR-E review fix): pre-fix, the load helper expanded ``~``
    via ``Path.expanduser`` for the existence check, but the save flow
    received the raw operator string.  ``Path("~/x.yaml").exists()``
    in ``_prompt_unique_filename`` returns False (Path doesn't auto-
    expand) so the overwrite confirmation never fires and a literal
    ``~/x.yaml`` directory ends up created.  Canonicalising once at
    the entry point makes load + save use the same string.
    """
    return str(Path(path).expanduser())


def _load_initial_state_from_yaml(path: str) -> _WizardState:
    """Build a :class:`_WizardState` pre-populated from *path*'s YAML (E3 / PR-D).

    Reads the YAML, validates it against ``ForgeConfig`` (so a typo
    in the source surfaces immediately rather than 30 minutes into a
    failed training run), and seeds :attr:`_WizardState.config` with
    the loaded dict.  The wizard's per-step prompts already read from
    ``state.config`` for their defaults — pre-populating it is the
    full plumbing.

    Raises :class:`FileNotFoundError` when the path doesn't exist and
    :class:`ValueError` when the YAML doesn't parse / fails schema
    validation.  Both surface a clear single-line error to the
    operator before the wizard's interactive flow begins.
    """
    import yaml as _yaml

    src = Path(path).expanduser()
    if not src.is_file():
        raise FileNotFoundError(f"--wizard-start-from path does not exist: {path}")
    try:
        with open(src, "r", encoding="utf-8") as fh:
            data = _yaml.safe_load(fh)
    except _yaml.YAMLError as exc:
        raise ValueError(f"--wizard-start-from YAML failed to parse: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"--wizard-start-from YAML root must be a mapping; got {type(data).__name__}")
    # PR-D-A5 (PR-E review fix): split the try/except so a downstream
    # ``ImportError`` from inside a Pydantic validator doesn't silently
    # bypass schema validation.  Pre-fix the outer ``try`` covered both
    # the import AND the validate call — a future custom validator that
    # lazy-imports an optional dep (e.g. ``bitsandbytes``) would have
    # silently disabled the upfront schema check that the wizard's
    # whole "fail fast" promise depends on.
    try:
        from ..config import ForgeConfig as _ForgeConfig
    except ImportError:  # pragma: no cover — config always present
        # Use a separate identifier so the import failure path doesn't
        # shadow the ``ForgeConfig`` name (static analyzers flag the
        # ``ForgeConfig = None`` rebind as a class shadow).
        _ForgeConfig = None
    if _ForgeConfig is not None:
        try:
            _ForgeConfig.model_validate(data)
        except Exception as exc:  # noqa: BLE001 — pydantic raises ValidationError; surface to operator
            raise ValueError(f"--wizard-start-from YAML failed schema validation: {exc}") from exc
    state = _WizardState(
        experience="expert",  # YAML-supplied operators are typically expert
        use_case=_MANUAL_USE_CASE,  # already-edited config; no preset re-application
        current_step=0,
        completed_steps=[],
        config=copy.deepcopy(data),
    )
    return state


def _run_full_wizard_outcome(start_from: Optional[str] = None) -> WizardOutcome:
    """9-step interactive flow producing a hand-rolled config.yaml."""
    # PR-D-B6 (PR-E review fix): canonicalise ``start_from`` once
    # so subsequent ``Path(...).exists()`` / save-default checks use
    # the same expanded form.  ``~/x.yaml`` is a common shell
    # shortcut; expanding it here means the breadcrumb prints a
    # consistent path AND the save flow's overwrite confirmation
    # fires correctly.
    if start_from is not None:
        start_from = _canonical_start_from(start_from)
        # PR-D-A6 (PR-E review fix): warn the operator when an
        # in-progress resume snapshot exists — the start-from path
        # takes precedence and the snapshot will be cleared at save
        # time.  Pre-fix this happened silently, so a Ctrl-C'd wizard
        # from yesterday quietly disappeared.
        existing_snapshot = _load_wizard_state()
        if existing_snapshot:
            completed = existing_snapshot.get("completed_steps", [])
            _print(
                f"\n  ⚠ In-progress wizard snapshot detected at "
                f"{_wizard_state_path()} ({len(completed)} step(s) completed).\n"
                "    --wizard-start-from takes precedence; the snapshot will be "
                "discarded at save time.  Cancel now (Ctrl-C) and rerun "
                "``forgelm --wizard`` (without --wizard-start-from) to resume it."
            )
        try:
            state = _load_initial_state_from_yaml(start_from)
        except (FileNotFoundError, ValueError) as exc:
            _print(f"\n  ⚠ {exc}")
            return WizardOutcome(config_path=None, start_training=False)
        _print(f"\n  Loaded {start_from} as wizard starting state.")
        # Print a small breadcrumb so operators understand the wizard
        # will use the loaded values as defaults for each step.
        flat = _flatten_dict(state.config)
        _print(f"  {len(flat)} field(s) populated; press Enter at each prompt to keep the existing value.")
    else:
        state = _maybe_resume_state()
    try:
        state = _drive_wizard_steps(state)
    except WizardCancel:
        # Operator typed ``cancel`` / ``q`` at a step prompt — the
        # welcome banner promises this exits cleanly (F-P7-OPUS-05).
        # Preserve state so a rerun can resume, mirroring the Ctrl-C path.
        _persist_state(state)
        _print("\n  Wizard cancelled.  No config written.")
        return WizardOutcome(config_path=None, start_training=False)
    except (KeyboardInterrupt, EOFError):
        _persist_state(state)
        _print(f"\n  Interrupted.  State preserved at {_wizard_state_path()} — rerun `forgelm --wizard` to resume.")
        return WizardOutcome(config_path=None, start_training=False)
    config = _strip_internal_meta(state.config)
    # E3 (PR-D): when the wizard was started from an existing YAML the
    # operator's intent is "edit this file"; default the save filename
    # to ``start_from`` so a bare Enter overwrites it (subject to the
    # existing overwrite confirmation in ``_prompt_unique_filename``).
    save_default = start_from if start_from else "my_config.yaml"
    config_filename = _prompt_unique_filename("Save config as", save_default)
    config_filename = _save_config_to_file(config, config_filename)
    _print_preflight_checklist(config, state)
    _print_wizard_summary(config)
    is_valid = _validate_generated_config(config_filename)
    _clear_wizard_state()
    if is_valid and _prompt_yes_no("Start training now?", default=False):
        _print(f"\n  Running: forgelm --config {shlex.quote(config_filename)}")
        _print()
        return WizardOutcome(config_path=config_filename, start_training=True)
    _print("\n  To start training later, run:")
    _print(f"    forgelm --config {shlex.quote(config_filename)}")
    _print()
    return WizardOutcome(config_path=config_filename, start_training=False)


def _run_full_wizard(start_from: Optional[str] = None) -> Optional[str]:
    """Back-compat wrapper around :func:`_run_full_wizard_outcome`."""
    outcome = _run_full_wizard_outcome(start_from=start_from)
    return outcome.config_path if outcome.start_training else None


def _prompt_unique_filename(question: str, default: str) -> str:
    """Prompt for a config filename, offering a non-clobber suffix if it exists.

    Without this, a second wizard run with the default ``my_config.yaml``
    silently overwrites yesterday's config.  We re-prompt with an
    explicit overwrite confirmation; on decline we suffix with the next
    free integer (``my_config_2.yaml``) so the operator never loses
    work without typing ``y``.
    """
    # Single pass: every branch returns, so an explicit ``while True``
    # was misleading (SonarCloud python:S1751).  The decline path picks
    # an auto-suffix instead of re-prompting — the suffix call is the
    # documented contract that the operator never loses work.
    raw = _prompt(question, default).strip()
    if not raw:
        raw = default
    # Case-insensitive extension check so ``my_config.YAML`` /
    # ``my_config.YML`` don't get a second ``.yaml`` appended, and
    # ``~`` is expanded to the operator's home so the existence check +
    # the eventual ``_atomic_yaml_write`` write don't both miss the
    # tilde (PR #57 Gemini review). Anything else (relative or absolute
    # path) flows through unchanged.
    if not raw.lower().endswith((".yaml", ".yml")):
        raw += ".yaml"
    expanded = str(Path(raw).expanduser())
    if not Path(expanded).exists():
        return expanded
    if _prompt_yes_no(f"  '{expanded}' already exists.  Overwrite?", default=False):
        return expanded
    suffixed = _next_free_filename(expanded)
    _print(f"  Will save as '{suffixed}' instead.")
    return suffixed


def _next_free_filename(path: str) -> str:
    """Append ``_2``, ``_3`` … to *path* (before suffix) until a free name is found."""
    p = Path(path)
    stem, suffix = p.stem, p.suffix
    parent = p.parent
    counter = 2
    while True:
        candidate = parent / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return str(candidate)
        counter += 1


def _validate_generated_config(config_filename: str) -> bool:
    """Validate the wizard's output against ``ForgeConfig`` before exit.

    Catches schema violations (e.g., F-compliance-110 strict-tier
    leftovers, accidentally-emitted incompatible field combinations)
    BEFORE the operator runs ``forgelm --config <path>`` and discovers
    the breakage 30 seconds in.  Returns ``True`` on a clean validation
    and ``False`` on any failure (or when the config / yaml imports
    are missing) so the caller can short-circuit the immediate-train
    branch and never offer "Start training now?" against a YAML the
    schema already rejected.  Emits a structured WARNING log line so
    CI / log pipelines see the failure without scraping stdout.
    """
    try:
        import yaml as _yaml

        from ..config import ForgeConfig
    except ImportError:  # pragma: no cover — config always present
        return False
    try:
        with open(config_filename, "r", encoding="utf-8") as fh:
            data = _yaml.safe_load(fh) or {}
        ForgeConfig.model_validate(data)
    except Exception as exc:  # noqa: BLE001 — pydantic raises ValidationError; yaml raises YAMLError
        # Structured-log visibility per docs/standards/error-handling.md.
        # Operators tail audit / CI logs and miss stdout-only failures;
        # the WARNING line keeps the "wizard wrote a YAML the schema
        # rejects" event in the same channel as every other config-time
        # error.  ``_print`` below still surfaces the human-readable
        # error inline.
        logger.warning("Wizard output failed schema validation (%s): %s", config_filename, exc)
        _print("\n  ⚠ Wizard output failed schema validation:")
        for line in str(exc).splitlines()[:10]:
            _print(f"    {line}")
        _print(
            "  The YAML was saved but will fail to load.  Hand-edit the file or "
            "rerun the wizard.  This is rare — usually a strict-tier coercion gap."
        )
        return False
    _print("  ✓ Schema validation passed.")
    return True


def _print_preflight_checklist(config: Dict[str, Any], state: Optional[_WizardState] = None) -> None:
    """Print a short pre-flight checklist before the summary.

    Three quick checks the operator can act on immediately: GPU VRAM
    against load_in_4bit / model_size assumption; dataset path
    existence (or HF Hub flag); risk-tier ↔ safety-eval consistency.
    Skipped silently when the relevant info is missing.

    Accepts an optional :class:`_WizardState` so the GPU detection
    result can be reused from :func:`_step_welcome` (C16 / review-cycle
    3) — a fresh ``_detect_hardware`` call lazy-imports torch and
    enumerates CUDA devices, which is wasted work when the welcome step
    already paid that cost.  ``None`` falls back to a direct call so
    isolated callers (e.g., tests) keep working.
    """
    _print("\n" + "=" * 60)
    _print("  Pre-flight checklist")
    _print("=" * 60)

    hw = _cached_hardware(state) if state is not None else _detect_hardware()
    if hw["gpu_available"] and hw.get("vram_gb"):
        _print(f"  · GPU      : {hw['gpu_name']} ({hw['vram_gb']} GB VRAM)")
        load_in_4bit = config.get("model", {}).get("load_in_4bit", False)
        if hw["vram_gb"] < 12 and not load_in_4bit:
            _print("    ⚠ <12 GB VRAM with full-precision loading — consider QLoRA (load_in_4bit=True).")
    else:
        _print("  · GPU      : not detected (training will be CPU-only — slow for real workloads)")

    dataset = config.get("data", {}).get("dataset_name_or_path")
    if dataset:
        path = Path(dataset).expanduser()
        if path.is_file():
            _print(f"  · Dataset  : {dataset} (local file ✓)")
        elif path.is_dir():
            _print(f"  · Dataset  : {dataset} (directory — needs ingestion)")
        elif "/" in dataset and not str(dataset).startswith("/"):
            _print(f"  · Dataset  : {dataset} (HuggingFace Hub ID)")
        else:
            _print(f"  · Dataset  : {dataset} (⚠ unrecognised — check before running)")

    risk = config.get("compliance", {}).get("risk_classification")
    if risk:
        safety_enabled = config.get("evaluation", {}).get("safety", {}).get("enabled", False)
        if risk in _STRICT_RISK_TIERS and safety_enabled:
            _print(f"  · Risk     : {risk} → safety eval enabled ✓")
        elif risk in _STRICT_RISK_TIERS:
            _print(f"  · Risk     : {risk} → ⚠ safety eval NOT enabled (will fail F-compliance-110)")
        else:
            _print(f"  · Risk     : {risk}")
