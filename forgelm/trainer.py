import inspect
import logging
import math
import os
import re
import shutil
import sys
from typing import Any, Dict, Optional

# NOTE: Heavy ML imports (torch, transformers.EarlyStoppingCallback, trl.SFTConfig/SFTTrainer)
# are deferred to method bodies so `import forgelm.trainer` is cheap. Eagerly importing
# torch here costs ~3-5s of CLI startup per invocation. See closure-plan F-performance-101.
from .config import ConfigError
from .results import TrainResult
from .webhook import WebhookNotifier

logger = logging.getLogger("forgelm.trainer")

# Audit event names — kept as constants so the audit-log schema stays grep-able
# and downstream consumers don't break on a typo.
_EVT_REVERT_TRIGGERED = "model.reverted"


# ---------------------------------------------------------------------------
# Built-in GRPO math reward — used when grpo_reward_model is not set but the
# dataset carries a `gold_answer` field (e.g. the bundled grpo-math template).
#
# Kept at module level (not a class method or closure) so TRL's GRPOTrainer
# can pickle it across worker processes without dragging the surrounding
# trainer state into the spawn.
# ---------------------------------------------------------------------------

# Stop the captured value at the next sentence boundary so
# "Answer: 18. Çünkü …" does NOT swallow the trailing prose into the
# comparison string. The boundary is "[.!?] followed by whitespace OR EOL"
# — bare "." between digits ("Answer: 1.5") is preserved because a
# decimal "." is not followed by whitespace or end-of-string.
#
# Implementation notes:
#   - First chunk: ``[^\s.!?\n][^.!?\n]*`` — must start with a non-space,
#     then any non-newline that isn't sentence punctuation. This covers
#     "18", "70 km/h", "$40", "12:15", "2/5".
#   - Optional repeats: ``[.!?](?!\s|$)[^.!?\n]*`` — sentence punctuation
#     is allowed inside the capture *only* when not followed by
#     whitespace/EOL, which keeps "1.5" / "3.14159" intact while still
#     stopping at "18. Çünkü ...".
#   - Greedy throughout — no reluctant quantifier needed because the
#     character classes self-bound at the next sentence break.
_ANSWER_PATTERN = re.compile(
    # First class drops `\n` because `\s` already covers it.
    r"answer\s*:\s*([^\s.!?][^.!?\n]*(?:[.!?](?!\s|$)[^.!?\n]*)*)",
    re.IGNORECASE,
)


# Units / suffixes the prompts in the grpo-math template attach to numeric
# answers — stripped before comparison so "Answer: $15" matches gold "15".
# Order matters: longer/multi-char tokens first to avoid partial overlaps
# (e.g. "km/h" must be matched before "km").
#
# **Domain caveat (Faz 28 / C-57 honesty fix):** this token set is
# tuned for the bundled `grpo-math` template which targets the
# **GSM8K + MATH** benchmarks (US units + currency + percent + a
# narrow metric subset).  Operators training GRPO on other math
# domains (SI-only physics, scientific notation, complex numbers,
# code-with-math, multilingual quantities) should not expect this
# stripper to generalise — write a custom reward callable via the
# ``training.grpo_reward_model`` config knob (see
# ``docs/guides/alignment.md`` GRPO section) instead of widening this
# list.  The v0.6.0 GRPO config-driven reward plugin migration is
# tracked under the v0.6.0 backlog.
_REWARD_STRIP_TOKENS: tuple[str, ...] = (
    "km/h",
    "m/s",
    "mL",
    "ml",
    "m²",
    "liters",
    "hours",
    "km",
    "cm",
    "kg",
    "$",
    "%",
    "m",
)


# Single-letter alphabetic tokens (e.g. "m" for meters) need a boundary check
# before stripping — otherwise the bare "m" rule would shave the trailing
# letter off normal English words like "them" or "method". Multi-char and
# non-alpha tokens ("$", "%", "kg", "km/h") have no such ambiguity.
_BOUNDARY_REQUIRED_TOKENS: frozenset[str] = frozenset({"m"})


def _is_unit_suffix_safe_to_strip(out: str, unit: str) -> bool:
    """True when ``out`` ends with ``unit`` AND the char before is a digit/space."""
    if unit not in _BOUNDARY_REQUIRED_TOKENS:
        return True
    if len(out) == len(unit):
        return True
    prev = out[-len(unit) - 1]
    return prev.isdigit() or prev.isspace()


def _is_unit_prefix_safe_to_strip(out: str, unit: str) -> bool:
    """True when ``out`` starts with ``unit`` AND the next char is a digit/space."""
    if unit not in _BOUNDARY_REQUIRED_TOKENS:
        return True
    if len(out) == len(unit):
        return True
    nxt = out[len(unit)]
    return nxt.isdigit() or nxt.isspace()


def _normalize_answer(s: Any) -> str:
    """Trim whitespace, sentence punctuation, and known unit suffixes / prefixes.

    Designed for the grpo-math template's ``Answer: <value>`` outputs;
    leaves fractions ("2/5") and time strings ("12:15") intact for
    string-equality fallback in :func:`_answers_match`.

    Accepts any value type — ``None`` returns ``""``; ints, floats, and
    bools are stringified first so a ``gold_answer`` field carrying ``0``
    or ``False`` doesn't crash with ``AttributeError`` on ``.strip()``.
    """
    if s is None:
        return ""
    if not isinstance(s, str):
        s = str(s)
    out = s.strip().rstrip(".!?")
    # Strip a known unit token from either end. Repeat once: "$15 USD"-style
    # collisions don't appear in the bundled prompts but a defensive single
    # rescan keeps things predictable. Single-letter alpha tokens (only "m"
    # today) require a digit/space boundary so "them" / "method" don't get
    # truncated.
    for _ in range(2):
        for unit in _REWARD_STRIP_TOKENS:
            if out.endswith(unit) and _is_unit_suffix_safe_to_strip(out, unit):
                out = out[: -len(unit)].rstrip()
            if out.startswith(unit) and _is_unit_prefix_safe_to_strip(out, unit):
                out = out[len(unit) :].lstrip()
    return out.strip()


def _answers_match(extracted: str, gold: str) -> bool:
    """True when ``extracted`` is the same answer as ``gold``.

    Tries exact string match first, then numeric match with a small float
    tolerance — keeps non-numeric answers ("12:15", "2/5") correct without
    forcing the prompts into a single shape.
    """
    if extracted == gold:
        return True
    try:
        return abs(float(extracted) - float(gold)) < 1e-6
    except ValueError:
        return False


def _math_reward_fn(completions, **kwargs):
    """Built-in regex-based reward for grpo-math style prompts.

    Each completion is expected to end with ``Answer: <value>``; the captured
    value is normalized (units stripped) and compared to the dataset's
    ``gold_answer`` field. TRL passes per-sample dataset columns as kwargs.

    Returns 1.0 for an exact match, 0.0 otherwise. Generations that don't
    contain an ``Answer:`` marker score 0.0 — the regex implicitly enforces
    the spec'd output format.
    """
    golds = kwargs.get("gold_answer")
    # No gold_answer column passed → reward function is wired but the dataset
    # carries no ground truth. Return zero rewards so training continues
    # (combined_format_length_reward still drives gradient via the format
    # signal). This branch should be unreachable in practice — the trainer
    # only wires _math_reward_fn after _dataset_has_gold_answers returns True.
    if golds is None:
        return [0.0] * len(completions)
    # Use strict=True so a wiring regression (mismatched batch sizes) raises
    # immediately instead of silently truncating to the shorter list and
    # masking the bug as low reward.
    rewards: list[float] = []
    for completion, gold in zip(completions, golds, strict=True):
        match = _ANSWER_PATTERN.search(completion or "")
        if not match:
            rewards.append(0.0)
            continue
        extracted = _normalize_answer(match.group(1))
        gold_norm = _normalize_answer(gold)
        rewards.append(1.0 if _answers_match(extracted, gold_norm) else 0.0)
    return rewards


def _dataset_has_gold_answers(dataset: Dict[str, Any]) -> bool:
    """Return True when the dataset's train split has a ``gold_answer`` field.

    Looks at the first row only — ForgeLM's preparation pipeline already
    enforces a homogeneous schema, so a single probe is sufficient.

    Detection is presence-based: ``0``, ``0.0``, and ``False`` count as
    real gold answers (a math problem may legitimately have ``"0"`` as the
    correct answer). Only an empty string ``""`` or ``None`` is treated
    as "the column exists in name only" and ignored — those typically
    come from a schema placeholder rather than a real label.
    """
    train = dataset.get("train") if isinstance(dataset, dict) else None
    if train is None or len(train) == 0:
        return False
    # Prefer dict-style row access; fall back to HuggingFace Dataset's
    # `column_names` attribute when row access isn't supported.
    try:
        first = train[0]
        if isinstance(first, dict):
            if "gold_answer" not in first:
                return False
            val = first["gold_answer"]
            return val is not None and val != ""
    except (IndexError, KeyError, TypeError):
        pass
    cols = getattr(train, "column_names", None)
    return bool(cols and "gold_answer" in cols)


class ForgeTrainer:
    """Orchestrates the training process for ForgeLM using TRL SFTTrainer."""

    def __init__(self, model: Any, tokenizer: Any, config: Any, dataset: Dict[str, Any]):
        self.model = model
        self.tokenizer = tokenizer
        self.config = config
        self.dataset = dataset
        self.checkpoint_dir = self.config.training.output_dir
        self.notifier = WebhookNotifier(config)
        self.run_name = config.model.name_or_path.split("/")[-1] + "_finetune"

        os.makedirs(self.checkpoint_dir, exist_ok=True)

        # Art. 12: Structured audit log
        from .compliance import AuditLogger

        self.audit = AuditLogger(self.checkpoint_dir)
        self.audit.log_event(
            "pipeline.initialized", model=config.model.name_or_path, trainer_type=config.training.trainer_type
        )

        # Validate evaluation config early
        self._validate_evaluation_config()

    def _validate_evaluation_config(self) -> None:
        """Warn about evaluation configuration issues before training starts."""
        eval_cfg = self.config.evaluation
        if not eval_cfg or not eval_cfg.auto_revert:
            return

        if not self.dataset.get("validation"):
            logger.warning(
                "auto_revert is enabled but no validation split exists. "
                "Evaluation checks will be skipped. Provide a validation set "
                "or set auto_revert=false."
            )

        if eval_cfg.max_acceptable_loss is None and eval_cfg.baseline_loss is None:
            logger.warning(
                "auto_revert is enabled but neither max_acceptable_loss nor "
                "baseline_loss is configured. Baseline will be computed automatically "
                "if a validation set is available."
            )

        # Warn if eval_steps is larger than training dataset
        train_size = len(self.dataset.get("train", []))
        if train_size > 0 and self.config.training.eval_steps > train_size:
            logger.warning(
                "eval_steps (%d) is larger than training dataset (%d samples). "
                "Evaluation will not run during training. Consider reducing eval_steps.",
                self.config.training.eval_steps,
                train_size,
            )

    @property
    def _trainer_type(self) -> str:
        return getattr(self.config.training, "trainer_type", "sft")

    def _get_common_training_kwargs(self) -> dict:
        """Return training arguments common to both SFT and ORPO."""
        import torch

        _train_size = len(self.dataset.get("train", [])) if self.dataset else 0
        logging_steps = max(1, min(50, _train_size // 100)) if _train_size > 0 else 50

        # When no validation split exists (e.g. tiny dataset from
        # `_ensure_validation_split`'s <2-row guard), HF Trainer refuses to
        # accept `eval_strategy != "no"` with a `None` eval_dataset.  Disable
        # the eval-coupled args together so the training run still succeeds
        # — auto-revert / best-model selection just don't apply without an
        # eval set, and `_validate_evaluation_config` already warns the user.
        has_validation = bool(self.dataset and self.dataset.get("validation"))
        eval_strategy = "steps" if has_validation else "no"
        load_best_model_at_end = has_validation

        kwargs = {
            "output_dir": self.checkpoint_dir,
            "max_steps": self.config.training.max_steps,
            "num_train_epochs": self.config.training.num_train_epochs,
            "per_device_train_batch_size": self.config.training.per_device_train_batch_size,
            "gradient_accumulation_steps": self.config.training.gradient_accumulation_steps,
            "learning_rate": self.config.training.learning_rate,
            "warmup_ratio": self.config.training.warmup_ratio,
            "weight_decay": self.config.training.weight_decay,
            "eval_steps": self.config.training.eval_steps,
            "save_steps": self.config.training.save_steps,
            "logging_steps": logging_steps,
            "eval_strategy": eval_strategy,
            "save_strategy": "steps",
            "save_total_limit": self.config.training.save_total_limit,
            "load_best_model_at_end": load_best_model_at_end,
            "metric_for_best_model": "eval_loss" if has_validation else None,
            "greater_is_better": False if has_validation else None,
            "gradient_checkpointing": torch.cuda.is_available(),
            "optim": "adamw_torch_fused" if torch.cuda.is_available() else "adamw_torch",
            "bf16": torch.cuda.is_available() and torch.cuda.is_bf16_supported(),
            "fp16": torch.cuda.is_available() and not torch.cuda.is_bf16_supported(),
            "use_cpu": not torch.cuda.is_available(),
            "report_to": getattr(self.config.training, "report_to", "tensorboard"),
            "run_name": getattr(self.config.training, "run_name", None) or self.run_name,
        }

        # Inject long-context optimizations
        self._apply_long_context_config(kwargs)

        # Inject GaLore optimizer configuration
        if self.config.training.galore_enabled:
            self._apply_galore_config(kwargs)

        # Inject distributed training configuration
        dist_cfg = self.config.distributed
        if dist_cfg and dist_cfg.strategy:
            self._apply_distributed_config(kwargs, dist_cfg)

        return kwargs

    def _apply_long_context_config(self, kwargs: dict) -> None:
        """Apply long-context training optimizations."""
        tc = self.config.training
        if tc.neftune_noise_alpha is not None:
            kwargs["neftune_noise_alpha"] = tc.neftune_noise_alpha
            logger.info("NEFTune enabled: noise_alpha=%.1f", tc.neftune_noise_alpha)

    def _apply_galore_config(self, kwargs: dict) -> None:
        """Apply GaLore optimizer-level memory optimization to training kwargs."""
        tc = self.config.training
        kwargs["optim"] = tc.galore_optim
        kwargs["optim_target_modules"] = tc.galore_target_modules or [r".*.attn.*", r".*.mlp.*"]
        kwargs["optim_args"] = (
            f"rank={tc.galore_rank}, "
            f"update_proj_gap={tc.galore_update_proj_gap}, "
            f"scale={tc.galore_scale}, "
            f"proj_type={tc.galore_proj_type}"
        )
        logger.info(
            "GaLore enabled: optim=%s, rank=%d, update_proj_gap=%d, scale=%.2f",
            tc.galore_optim,
            tc.galore_rank,
            tc.galore_update_proj_gap,
            tc.galore_scale,
        )

    def _apply_distributed_config(self, kwargs: dict, dist_cfg) -> None:
        """Apply DeepSpeed or FSDP configuration to training kwargs."""
        if dist_cfg.strategy == "deepspeed":
            ds_config = self._resolve_deepspeed_config(dist_cfg.deepspeed_config)
            kwargs["deepspeed"] = ds_config
            logger.info("DeepSpeed enabled with config: %s", dist_cfg.deepspeed_config or "auto")
            # DeepSpeed manages its own optimizer — remove gradient_checkpointing conflict
            kwargs["gradient_checkpointing"] = True

        elif dist_cfg.strategy == "fsdp":
            fsdp_options = [dist_cfg.fsdp_strategy]
            if dist_cfg.fsdp_auto_wrap:
                fsdp_options.append("auto_wrap")
            if dist_cfg.fsdp_offload:
                fsdp_options.append("offload")
            kwargs["fsdp"] = " ".join(fsdp_options)
            kwargs["fsdp_config"] = {
                "backward_prefetch": dist_cfg.fsdp_backward_prefetch,
                "state_dict_type": dist_cfg.fsdp_state_dict_type,
            }
            logger.info("FSDP enabled with strategy: %s", dist_cfg.fsdp_strategy)

        else:
            logger.warning("Unknown distributed strategy: %s. Ignoring.", dist_cfg.strategy)

    def _resolve_deepspeed_config(self, config_ref: Optional[str] = None) -> str:
        """Resolve a DeepSpeed config reference to a file path.

        Accepts:
          - A preset name: "zero2", "zero3", "zero3_offload"
          - An absolute or relative file path to a JSON file
          - None: returns the default zero2 preset
        """
        presets = {
            "zero2": "configs/deepspeed/zero2.json",
            "zero3": "configs/deepspeed/zero3.json",
            "zero3_offload": "configs/deepspeed/zero3_offload.json",
        }

        if not config_ref:
            config_ref = "zero2"

        # Check if it's a preset name
        if config_ref in presets:
            # Resolve relative to the package installation or CWD
            preset_path = presets[config_ref]
            # Try package-relative first
            pkg_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            full_path = os.path.join(pkg_dir, preset_path)
            if os.path.isfile(full_path):
                logger.info("Using DeepSpeed preset '%s': %s", config_ref, full_path)
                return full_path
            # Fall back to CWD
            if os.path.isfile(preset_path):
                return preset_path
            raise FileNotFoundError(
                f"DeepSpeed preset '{config_ref}' not found at {full_path}. "
                f"Ensure ForgeLM configs directory is accessible."
            )

        # It's a file path
        if os.path.isfile(config_ref):
            logger.info("Using custom DeepSpeed config: %s", config_ref)
            return config_ref

        raise FileNotFoundError(f"DeepSpeed config not found: {config_ref}")

    def _apply_max_length(self, kwargs: Dict[str, Any], param_name: str = "max_length") -> None:
        """Set ``model.max_length`` on a TRL config under *param_name*.

        Without this the preference trainers (DPO / ORPO / SimPO / KTO) silently
        fall back to TRL's own 512/1024 ``max_length`` defaults while the config
        field, configuration.md, and the Article 11 compliance manifest all
        claim ``model.max_length`` applies — silent truncation plus a false
        statement in an audit artefact (F-P3-FABLE-03). The preference configs'
        ``max_length`` parameter is stable across the pinned TRL range; a future
        rename surfaces loudly as a ``TypeError`` at construction, never silent.
        """
        kwargs[param_name] = self.config.model.max_length

    def _get_training_args_for_type(self):
        """Build the appropriate TRL config based on trainer_type."""
        tt = self._trainer_type
        kwargs = self._get_common_training_kwargs()

        if tt == "sft":
            from trl import SFTConfig

            kwargs["packing"] = bool(getattr(self.config.training, "packing", False))
            kwargs["dataset_text_field"] = "text"
            # Sequence-length cap parameter was renamed in trl 0.13:
            #   trl 0.12.x: ``SFTConfig(max_seq_length=...)``
            #   trl 0.13+ / 1.x: ``SFTConfig(max_length=...)`` — old name removed
            # ``pyproject.toml`` pins ``trl>=0.12.0,<2.0.0``, so detect at runtime.
            # Hard-fail (don't warn-and-continue) if neither name is exposed:
            # silently dropping ``model.max_length`` would let TRL pick its
            # default and produce a model trained against an unintended
            # context window — exactly the "no silent failures" rule in
            # docs/standards/error-handling.md.
            sft_params = inspect.signature(SFTConfig).parameters
            if "max_length" in sft_params:
                kwargs["max_length"] = self.config.model.max_length
            elif "max_seq_length" in sft_params:
                kwargs["max_seq_length"] = self.config.model.max_length
            else:
                # Probe the trl version off the already-loaded module rather
                # than re-importing — ``from trl import SFTConfig`` above
                # left trl in ``sys.modules`` and ``SFTConfig.__module__``
                # gives us the canonical package name without a redundant
                # ``import trl`` line.
                trl_module = sys.modules.get(SFTConfig.__module__.split(".", 1)[0])
                trl_version = getattr(trl_module, "__version__", "?")
                raise ValueError(
                    f"SFTConfig in trl {trl_version} exposes neither "
                    "`max_length` nor `max_seq_length` as a named parameter; cannot apply "
                    f"the sequence-length cap from config (model.max_length={self.config.model.max_length}). "
                    f"Detected parameters: {sorted(sft_params)}. Pin trl to a known-compatible "
                    "version or file an issue referencing this error."
                )
            return SFTConfig(**kwargs)

        elif tt == "orpo":
            from trl import ORPOConfig

            kwargs["beta"] = self.config.training.orpo_beta
            self._apply_max_length(kwargs)
            return ORPOConfig(**kwargs)

        elif tt == "dpo":
            from trl import DPOConfig

            kwargs["beta"] = self.config.training.dpo_beta
            self._apply_max_length(kwargs)
            return DPOConfig(**kwargs)

        elif tt == "simpo":
            from trl import CPOConfig

            # SimPO is implemented via CPOTrainer with loss_type="simpo" in TRL
            kwargs["beta"] = self.config.training.simpo_beta
            kwargs["cpo_alpha"] = 0.0  # pure SimPO (no NLL term)
            kwargs["simpo_gamma"] = self.config.training.simpo_gamma
            kwargs["loss_type"] = "simpo"
            self._apply_max_length(kwargs)
            return CPOConfig(**kwargs)

        elif tt == "kto":
            from trl import KTOConfig

            kwargs["beta"] = self.config.training.kto_beta
            self._apply_max_length(kwargs)
            return KTOConfig(**kwargs)

        elif tt == "grpo":
            from trl import GRPOConfig

            # GRPO generates responses during training — needs generation params
            kwargs["num_generations"] = self.config.training.grpo_num_generations
            # TRL >=0.12 expects `max_completion_length`; the older `max_new_tokens`
            # raises TypeError at GRPOConfig construction.
            kwargs["max_completion_length"] = self.config.training.grpo_max_completion_length
            # Honour model.max_length as the GRPO prompt cap so prompts aren't
            # silently truncated at TRL's 512 `max_prompt_length` default while
            # the manifest claims model.max_length applies (F-P3-FABLE-03).
            self._apply_max_length(kwargs, "max_prompt_length")
            # GRPO trains on generation-based rewards, not validation loss, so
            # `_build_grpo_trainer` drops the eval_dataset. The eval-coupled
            # TrainingArguments must be turned off in lockstep: when a validation
            # split exists (the default pipeline path), `_get_common_training_kwargs`
            # sets `eval_strategy="steps"`. Leaving it set while no eval_dataset
            # reaches the trainer makes HF/TRL raise
            # `ValueError: ... you didn't pass an eval_dataset` at GRPOTrainer
            # construction — the crash this reconciliation prevents.
            kwargs["eval_strategy"] = "no"
            kwargs.pop("eval_steps", None)
            # GRPO doesn't use load_best_model_at_end the same way
            kwargs.pop("load_best_model_at_end", None)
            kwargs.pop("metric_for_best_model", None)
            kwargs.pop("greater_is_better", None)
            return GRPOConfig(**kwargs)

        else:
            raise ValueError(f"Unknown trainer_type: {tt}")

    def execute_evaluation_checks(self, final_path: str, metrics: Dict[str, float]) -> bool:
        """Evaluates final loss against constraints. Returns True if acceptable, False if reverted."""
        if not self.config.evaluation or not self.config.evaluation.auto_revert:
            return True

        # No validation data means we can't evaluate
        if not self.dataset.get("validation"):
            logger.warning("Skipping evaluation checks — no validation data available.")
            return True

        final_loss = metrics.get("eval_loss")
        baseline_loss = self.config.evaluation.baseline_loss
        max_loss = self.config.evaluation.max_acceptable_loss

        # Handle missing or invalid eval_loss
        if final_loss is None:
            logger.warning("eval_loss not found in metrics. Skipping evaluation checks.")
            return True

        if math.isnan(final_loss) or math.isinf(final_loss):
            reason = f"eval_loss is {final_loss} (NaN or Inf) — training diverged."
            logger.error("EVALUATION FAILED: %s", reason)
            self._revert_model(final_path, reason, source="nan_inf")
            return False

        # Two independent checks:
        # 1) Hard ceiling (max_acceptable_loss)
        # 2) Regression vs baseline (baseline_loss)
        failed_reasons = []
        if max_loss is not None and final_loss > max_loss:
            failed_reasons.append(f"Final eval_loss ({final_loss:.4f}) exceeded max_acceptable_loss ({max_loss:.4f}).")
        if baseline_loss is not None and final_loss > baseline_loss:
            failed_reasons.append(f"Final eval_loss ({final_loss:.4f}) is worse than baseline ({baseline_loss:.4f}).")

        if failed_reasons:
            reason = " ".join(failed_reasons)
            logger.error("EVALUATION FAILED: %s", reason)
            self._revert_model(final_path, reason, source="threshold")
            return False

        # Log success with improvement details
        if baseline_loss is not None and baseline_loss > 0:
            improvement = ((baseline_loss - final_loss) / baseline_loss) * 100
            logger.info(
                "Evaluation passed: eval_loss=%.4f (%.1f%% improvement over baseline %.4f)",
                final_loss,
                improvement,
                baseline_loss,
            )
        else:
            logger.info("Evaluation passed: eval_loss=%.4f", final_loss)

        return True

    def _revert_model(self, final_path: str, reason: str, *, source: str = "evaluation") -> None:
        """Delete generated model artifacts, emit audit event, and notify webhook.

        Centralises the revert flow so every code path that triggers a revert
        produces both:
        1. ``_EVT_REVERT_TRIGGERED`` audit event (Article 12 record-keeping
           — operator-side governance can correlate "model.reverted" webhook
           ↔ audit entry by ``run_id`` + timestamp).
        2. ``training.reverted`` webhook lifecycle event (Faz 8 — dashboards).

        Prior to this refactor only ``benchmark``, ``safety``, and ``judge``
        gates emitted the audit event — the NaN/Inf and threshold paths
        inside ``execute_evaluation_checks`` reverted silently from the
        audit log. Foundation PR review I2 closure.

        Args:
            final_path: Filesystem path of the artifacts to delete.
            reason: Human-readable failure reason (free-form).
            source: Gate name ("evaluation", "benchmark", "safety", "judge",
                "nan_inf", "threshold") for the audit-event ``reason`` field.
                The webhook payload also includes this in the masked reason.
        """
        # Article 12 audit trail — emit before destructive action so the
        # record exists even if the rmtree below explodes.
        self.audit.log_event(_EVT_REVERT_TRIGGERED, reason=source, detail=reason)

        logger.warning("Auto-revert enabled. Deleting generated artifacts at %s...", final_path)
        if os.path.exists(final_path):
            try:
                shutil.rmtree(final_path)
                logger.info("Reverted artifacts deleted successfully.")
            except OSError:
                logger.exception(
                    "Failed to delete reverted artifacts at %s. Manual cleanup may be required.", final_path
                )

        # Lifecycle event: dashboards distinguish "training.reverted" (gate
        # rejected an otherwise-completed run) from "training.failure"
        # (training itself crashed). See docs/standards/logging-observability.md.
        self.notifier.notify_reverted(run_name=self.run_name, reason=f"{reason} Artifacts discarded.")

    def _build_trainer(self, callbacks: list) -> None:
        """Build (or rebuild) self.trainer from current config. Called on first build and after OOM retry."""
        tt = self._trainer_type
        training_args = self._get_training_args_for_type()

        trainer_kwargs = {
            "model": self.model,
            "processing_class": self.tokenizer,
            "args": training_args,
            "train_dataset": self.dataset["train"],
            "eval_dataset": self.dataset.get("validation", None),
            "callbacks": callbacks,
        }

        if tt == "grpo":
            self.trainer = self._build_grpo_trainer(trainer_kwargs, callbacks)
        else:
            self.trainer = self._build_simple_trl_trainer(tt, trainer_kwargs)

    def _build_simple_trl_trainer(self, tt: str, trainer_kwargs: Dict[str, Any]) -> Any:
        """Build any non-GRPO TRL trainer. GRPO needs reward-func wiring and is handled separately."""
        if tt == "sft":
            logger.info("Initializing TRL SFTTrainer...")
            from trl import SFTTrainer

            return SFTTrainer(**trainer_kwargs)
        if tt == "orpo":
            logger.info("Initializing TRL ORPOTrainer (ORPO preference alignment)...")
            from trl import ORPOTrainer

            return ORPOTrainer(**trainer_kwargs)
        if tt == "dpo":
            logger.info("Initializing TRL DPOTrainer (DPO preference alignment)...")
            from trl import DPOTrainer

            return DPOTrainer(**trainer_kwargs)
        if tt == "simpo":
            logger.info("Initializing TRL CPOTrainer (SimPO preference alignment)...")
            from trl import CPOTrainer

            return CPOTrainer(**trainer_kwargs)
        if tt == "kto":
            logger.info("Initializing TRL KTOTrainer (binary feedback alignment)...")
            from trl import KTOTrainer

            return KTOTrainer(**trainer_kwargs)
        raise ValueError(f"Unknown trainer_type: {tt}")

    def _build_grpo_trainer(self, trainer_kwargs: Dict[str, Any], callbacks: list) -> Any:
        """Build a TRL GRPOTrainer with the right reward-func chain wired up."""
        logger.info("Initializing TRL GRPOTrainer (reasoning RL)...")
        from trl import GRPOTrainer

        # GRPO doesn't use eval_dataset the same way — remove callbacks that depend on eval
        trainer_kwargs.pop("eval_dataset", None)
        if callbacks:
            logger.info(
                "GRPO trainer: removing %d callback(s) (EarlyStopping, eval callbacks). "
                "GRPO uses generation-based rewards, not validation loss.",
                len(callbacks),
            )
        trainer_kwargs["callbacks"] = []
        trainer_kwargs["reward_funcs"] = self._resolve_grpo_reward_funcs()
        return GRPOTrainer(**trainer_kwargs)

    def _resolve_grpo_reward_funcs(self) -> list:
        """Pick the GRPO reward callables. trl.GRPOTrainer requires reward_funcs to be set.

        TRL sums multiple reward funcs additively, so we can stack signals
        when both are available:
          1) explicit reward model → single classifier callable. Stops
             here; the user opted into a learned reward.
          2) no reward model → built-in format+length shaping reward
             (gradient-friendly, always teaches output structure).
             If the dataset also carries a `gold_answer` field, append
             the built-in correctness reward so the model learns to be
             both well-formatted AND right.
        """
        reward_model_path = getattr(self.config.training, "grpo_reward_model", None)
        if reward_model_path:
            logger.info("GRPO reward source: classifier model %s", reward_model_path)
            return [self._build_classifier_reward(reward_model_path)]

        from .grpo_rewards import combined_format_length_reward

        reward_funcs: list = [combined_format_length_reward]
        if _dataset_has_gold_answers(self.dataset):
            reward_funcs.append(_math_reward_fn)
            logger.info(
                "GRPO reward source: built-in format+length shaping reward "
                "(weight 0.8/0.2) + correctness reward against `gold_answer` "
                "field (additive). No training.grpo_reward_model configured."
            )
        else:
            logger.info(
                "GRPO reward source: built-in format+length shaping reward "
                "(weight 0.8/0.2). No training.grpo_reward_model configured "
                "and dataset has no `gold_answer` field — model learns output "
                "structure only. Add a `gold_answer` column for a correctness signal."
            )
        return reward_funcs

    @staticmethod
    def _build_classifier_reward(reward_model_path: str):
        """Wrap an HF sequence-classification model as a TRL reward callable."""
        from transformers import AutoModelForSequenceClassification
        from transformers import AutoTokenizer as _AutoTok

        # `trust_remote_code=False` is the secure default — a reward model
        # downloaded from the Hub should never execute arbitrary repo code
        # at load time. Operators that genuinely need a custom architecture
        # can fork and pre-convert; this code path is the GRPO classifier
        # reward, which is always a SequenceClassification head.
        _rw_tok = _AutoTok.from_pretrained(reward_model_path, trust_remote_code=False)
        _rw_model = AutoModelForSequenceClassification.from_pretrained(
            reward_model_path, device_map="auto", trust_remote_code=False
        )

        def _reward_fn(completions, **kwargs):
            import torch as _t

            inputs = _rw_tok(completions, return_tensors="pt", truncation=True, padding=True, max_length=512)
            inputs = {k: v.to(_rw_model.device) for k, v in inputs.items()}
            with _t.no_grad():
                logits = _rw_model(**inputs).logits
            return logits[:, 0].tolist()

        return _reward_fn

    def _run_with_oom_recovery(self, resume_from_checkpoint: Optional[str]) -> Any:
        """Run self.trainer.train() with optional OOM recovery.

        On CUDA OOM, halves per_device_train_batch_size and doubles
        gradient_accumulation_steps (preserving effective batch size), clears
        the CUDA cache, rebuilds the trainer, and retries — until
        oom_recovery_min_batch_size is reached.
        """
        import gc

        import torch

        cfg = self.config.training
        oom_recovery = getattr(cfg, "oom_recovery", False)
        min_bs = getattr(cfg, "oom_recovery_min_batch_size", 1)
        callbacks: list = self.trainer.callback_handler.callbacks if hasattr(self.trainer, "callback_handler") else []

        while True:
            try:
                return self.trainer.train(resume_from_checkpoint=resume_from_checkpoint)
            except (RuntimeError, torch.cuda.OutOfMemoryError) as e:
                is_oom = "out of memory" in str(e).lower()
                if not oom_recovery or not is_oom:
                    raise

                current_bs = cfg.per_device_train_batch_size
                if current_bs <= min_bs:
                    logger.error(
                        "CUDA OOM with batch_size=%d (already at minimum %d). Cannot recover.",
                        current_bs,
                        min_bs,
                    )
                    raise

                new_bs = max(current_bs // 2, min_bs)
                factor = current_bs // new_bs
                new_grad_accum = cfg.gradient_accumulation_steps * factor
                logger.warning(
                    "CUDA OOM detected. Retrying with batch_size=%d (was %d), "
                    "gradient_accumulation_steps=%d (was %d). Effective batch size preserved.",
                    new_bs,
                    current_bs,
                    new_grad_accum,
                    cfg.gradient_accumulation_steps,
                )
                self.audit.log_event(
                    "training.oom_recovery",
                    old_batch_size=current_bs,
                    new_batch_size=new_bs,
                    new_grad_accum=new_grad_accum,
                )

                cfg.per_device_train_batch_size = new_bs
                cfg.gradient_accumulation_steps = new_grad_accum

                gc.collect()
                torch.cuda.empty_cache()

                self._build_trainer(callbacks)

    def _measure_baseline_loss(self, metrics: Dict[str, float]) -> None:
        """Compute baseline eval_loss before training (used for regression gates)."""
        eval_cfg = self.config.evaluation
        if not (
            self.dataset.get("validation") and eval_cfg and eval_cfg.auto_revert and eval_cfg.baseline_loss is None
        ):
            return

        logger.info("Measuring baseline eval_loss (pre-training)...")
        model_obj = self.trainer.model
        baseline_metrics = None
        if hasattr(model_obj, "disable_adapter"):
            try:
                with model_obj.disable_adapter():
                    baseline_metrics = self.trainer.evaluate()
            except (RuntimeError, AttributeError, ValueError) as e:
                # PEFT disable_adapter context can fail when the active model
                # isn't a PeftModel (AttributeError), when the underlying
                # adapter graph is in a state that disallows toggling
                # (RuntimeError — NotImplementedError is a RuntimeError subclass),
                # or when evaluate() rejects the temporarily-base configuration (ValueError).
                # Fall back to evaluating with adapters active.
                logger.warning("Failed to disable adapters for baseline eval, evaluating with adapters instead: %s", e)
                baseline_metrics = self.trainer.evaluate()
        else:
            baseline_metrics = self.trainer.evaluate()

        baseline_loss = baseline_metrics.get("eval_loss")
        if baseline_loss is None:
            logger.warning(
                "Baseline evaluation completed but eval_loss not found in results. "
                "Baseline regression check will be skipped."
            )
            return
        eval_cfg.baseline_loss = float(baseline_loss)
        metrics["baseline_eval_loss"] = float(baseline_loss)
        logger.info("Baseline eval_loss computed: %.4f", baseline_loss)

    def _apply_benchmark_result(
        self,
        benchmark_result: Any,
        train_result: TrainResult,
        metrics: Dict[str, float],
        final_path: str,
    ) -> bool:
        """Attach benchmark output to *train_result*, returning True to continue.

        Mirrors the safety/judge gating: revert + halt only when the user opted
        into auto_revert. Without that flag, benchmark failures are recorded
        but do not destroy the saved model.
        """
        if benchmark_result is None:
            return True
        train_result.benchmark_scores = benchmark_result.scores
        train_result.benchmark_average = benchmark_result.average_score
        train_result.benchmark_passed = benchmark_result.passed
        for task, score in benchmark_result.scores.items():
            metrics[f"benchmark/{task}"] = score
        metrics["benchmark/average"] = benchmark_result.average_score
        self.audit.log_event(
            "benchmark.evaluation_completed",
            passed=benchmark_result.passed,
            average=benchmark_result.average_score,
            scores=benchmark_result.scores,
        )
        if benchmark_result.passed:
            return True
        reason = benchmark_result.failure_reason or "Benchmark score below threshold."
        if not (self.config.evaluation and self.config.evaluation.auto_revert):
            # Failure recorded on train_result; pipeline continues to safety/judge stages.
            return True
        self._revert_model(final_path, reason, source="benchmark")
        train_result.success = False
        train_result.reverted = True
        # Revert deletes the staging dir — clear the path so the run is never
        # misreported as "awaiting approval" (exit 4) instead of "reverted"
        # (exit 3). ``awaiting_approval`` is already False on this path.
        train_result.staging_path = None
        return False

    def _apply_resource_usage(self, train_result: TrainResult, metrics: Dict[str, float]) -> None:
        """Collect resource usage and feed it into the result + metrics dicts."""
        train_result.resource_usage = self._collect_resource_usage()
        if not train_result.resource_usage:
            return
        for k, v in train_result.resource_usage.items():
            if isinstance(v, (int, float)):
                metrics[f"resource/{k}"] = v
        train_result.estimated_cost_usd = train_result.resource_usage.get("estimated_cost_usd")

    def _apply_safety_result(
        self,
        safety_result: Any,
        train_result: TrainResult,
        metrics: Dict[str, float],
        final_path: str,
    ) -> bool:
        """Attach safety eval output to *train_result*, returning True to continue."""
        if safety_result is None:
            return True
        train_result.safety_passed = safety_result.passed
        train_result.safety_score = safety_result.safety_score
        train_result.safety_categories = safety_result.category_distribution
        train_result.safety_severity = safety_result.severity_distribution
        train_result.safety_low_confidence = safety_result.low_confidence_count
        metrics["safety/safe_ratio"] = safety_result.safe_ratio
        if safety_result.safety_score is not None:
            metrics["safety/safety_score"] = safety_result.safety_score
        self.audit.log_event(
            "safety.evaluation_completed",
            passed=safety_result.passed,
            safe_ratio=safety_result.safe_ratio,
            safety_score=safety_result.safety_score,
            categories=safety_result.category_distribution,
        )
        if safety_result.passed or not (self.config.evaluation and self.config.evaluation.auto_revert):
            return True
        self._revert_model(final_path, safety_result.failure_reason or "Safety check failed.", source="safety")
        train_result.success = False
        train_result.reverted = True
        train_result.staging_path = None  # see _apply_benchmark_result revert branch
        return False

    def _apply_judge_result(
        self,
        judge_result: Any,
        train_result: TrainResult,
        metrics: Dict[str, float],
        final_path: str,
    ) -> bool:
        """Attach judge output to *train_result*, returning True to continue."""
        if judge_result is None:
            return True
        train_result.judge_score = judge_result.average_score
        train_result.judge_details = judge_result.details
        metrics["judge/average_score"] = judge_result.average_score
        self.audit.log_event(
            "judge.evaluation_completed",
            passed=judge_result.passed,
            average_score=judge_result.average_score,
        )
        if judge_result.passed or not (self.config.evaluation and self.config.evaluation.auto_revert):
            return True
        self._revert_model(final_path, judge_result.failure_reason or "Judge score below threshold.", source="judge")
        train_result.success = False
        train_result.reverted = True
        train_result.staging_path = None  # see _apply_benchmark_result revert branch
        return False

    def _finalize_artifacts(
        self,
        final_path: str,
        metrics: Dict[str, float],
        train_result: TrainResult,
    ) -> None:
        """Generate model card / integrity / deployer instructions / compliance bundle."""
        self._generate_model_card(final_path, metrics, train_result)
        self._generate_model_integrity(final_path)
        self._generate_deployer_instructions(final_path, metrics)
        self._export_compliance_if_needed(metrics, train_result)

    def _handle_human_approval_gate(
        self,
        staging_path: str,
        train_result: TrainResult,
        *,
        already_saved: bool = False,
    ) -> bool:
        """Pause the run for human approval (Art. 14) and emit the gate event.

        The honest behaviour for an "awaiting human approval" pipeline: the
        final model must NOT land in the canonical ``final_model/`` directory
        before a human signs off, otherwise downstream consumers that watch
        that path treat the run as already deployed. Instead, the adapters
        live in a sibling ``final_model.staging/`` directory until
        ``forgelm approve <run_id>`` atomically renames it.

        ``staging_path`` is the on-disk staging directory (``final_path +
        ".staging"``). When ``already_saved=False`` (default) the method
        also saves the model to ``staging_path``; this preserves backwards
        compatibility for callers who reach the gate without having staged
        the model themselves. The pipeline orchestrator passes
        ``already_saved=True`` because it stages early so the post-train
        gates can evaluate against on-disk artefacts.

        Returns ``True`` when the gate fires (caller must skip the regular
        ``save_final_model(final_path)`` / ``notify_success`` calls),
        ``False`` when the gate is disabled.
        """
        eval_cfg = self.config.evaluation
        if not (eval_cfg and eval_cfg.require_human_approval):
            return False

        if not already_saved:
            self.save_final_model(staging_path)

        self.audit.log_event(
            "human_approval.required",
            gate="final_model",
            reason="require_human_approval=true",
            metrics=train_result.metrics,
            staging_path=staging_path,
            run_id=self.audit.run_id,
        )
        self.notifier.notify_awaiting_approval(run_name=self.run_name, model_path=staging_path)

        logger.info("Human approval required. Model staged at: %s", staging_path)
        logger.info(
            "Review results in %s/compliance/ and run `forgelm approve %s --output-dir %s` "
            "to promote, or `forgelm reject %s --output-dir %s` to preserve for forensic review.",
            self.checkpoint_dir,
            self.audit.run_id,
            self.checkpoint_dir,
            self.audit.run_id,
            self.checkpoint_dir,
        )

        train_result.success = True
        train_result.staging_path = staging_path
        train_result.awaiting_approval = True
        return True

    def _run_training_pipeline(self, resume_from_checkpoint: Optional[str]) -> TrainResult:
        """Body of train(); split out so train() stays a thin orchestrator."""
        metrics: Dict[str, float] = {}
        self.audit.log_event("training.started")

        self._measure_baseline_loss(metrics)

        logger.info("Starting training...")
        hf_train_result = self._run_with_oom_recovery(resume_from_checkpoint)
        metrics.update(hf_train_result.metrics)

        if self.dataset.get("validation"):
            metrics.update(self.trainer.evaluate())

        final_path = os.path.join(
            self.checkpoint_dir,
            getattr(self.config.training, "final_model_dir", "final_model"),
        )

        # Article 14 (honest path): when ``require_human_approval`` is on the
        # adapters land in ``final_model.staging/`` rather than the canonical
        # ``final_model/`` directory — and the canonical directory is created
        # only by ``forgelm approve <run_id>`` after a human signs off.
        # ``_handle_human_approval_gate`` performs both the staging save and
        # the audit-event / webhook emit, returning True when it fires so we
        # can skip the regular ``save_final_model`` call below.
        train_result = TrainResult(success=True, metrics=metrics, final_model_path=final_path)
        approval_required = bool(self.config.evaluation and self.config.evaluation.require_human_approval)
        if approval_required:
            # Save to staging first so post-train gates (safety/judge/etc.)
            # have an on-disk model to evaluate. The gate's audit event +
            # webhook notification fire after compliance artefacts are
            # generated so reviewers see a complete bundle.
            # final_model_path retains the intended final location; staging_path
            # records where the adapters currently live pending human sign-off.
            gate_path = os.path.abspath(f"{final_path}.staging.{self.audit.run_id}")
            self.save_final_model(gate_path)
            # Point final_model_path at the actual on-disk location (staging dir)
            # so downstream reporters (log, JSON output) reflect reality.
            # staging_path carries the same value so approval commands can find it.
            train_result.final_model_path = gate_path
            train_result.staging_path = gate_path
        else:
            gate_path = final_path
            self.save_final_model(gate_path)

        if not self.execute_evaluation_checks(gate_path, metrics):
            return TrainResult(success=False, metrics=metrics, reverted=True)

        if not self._apply_benchmark_result(self._run_benchmark_if_configured(), train_result, metrics, gate_path):
            return train_result

        self._apply_resource_usage(train_result, metrics)

        if not self._apply_safety_result(self._run_safety_if_configured(), train_result, metrics, gate_path):
            return train_result

        if not self._apply_judge_result(self._run_judge_if_configured(), train_result, metrics, gate_path):
            return train_result

        self._finalize_artifacts(gate_path, metrics, train_result)

        if self._handle_human_approval_gate(gate_path, train_result, already_saved=True):
            return train_result

        self.audit.log_event("pipeline.completed", success=True, metrics_summary=metrics)
        self.notifier.notify_success(run_name=self.run_name, metrics=metrics)
        return train_result

    def train(self, resume_from_checkpoint: Optional[str] = None) -> TrainResult:
        """Starts the main training loop. Returns TrainResult with status and metrics."""
        from transformers import EarlyStoppingCallback

        # Store originals so compliance manifest reflects pre-OOM values
        self._original_batch_size = self.config.training.per_device_train_batch_size
        self._original_grad_accum = self.config.training.gradient_accumulation_steps

        self.notifier.notify_start(run_name=self.run_name)
        callbacks = []
        if self.dataset.get("validation"):
            patience = getattr(self.config.training, "early_stopping_patience", 3)
            callbacks.append(EarlyStoppingCallback(early_stopping_patience=patience))

        self._build_trainer(callbacks)

        try:
            return self._run_training_pipeline(resume_from_checkpoint)
        except Exception as e:  # noqa: BLE001 — best-effort: top-of-pipeline catch must record an audit event and notify before re-raising regardless of failure type (CUDA, dataloader, optimizer, etc.); the bare re-raise preserves the original traceback.  # NOSONAR
            logger.exception("Training pipeline failed.")
            self.audit.log_event("pipeline.failed", error=str(e))
            self.notifier.notify_failure(run_name=self.run_name, reason=str(e))
            raise

    def save_final_model(self, final_path: str) -> None:
        """Saves final artifacts (adapter-only by default)."""
        os.makedirs(final_path, exist_ok=True)
        merge_adapters = bool(getattr(self.config.training, "merge_adapters", False))

        # Prefer adapter-only save for PEFT models. This keeps artifacts small and makes revert safe.
        if not merge_adapters:
            logger.info("Saving final adapters to %s...", final_path)
            try:
                self.trainer.model.save_pretrained(final_path)
            except (OSError, RuntimeError, AttributeError, ValueError) as e:
                # OSError: filesystem (permissions, disk full, missing dir).
                # RuntimeError: torch / CUDA-side serialization error.
                # AttributeError: non-PEFT models without save_pretrained
                # contract drift. ValueError: safetensors / state_dict
                # validation. Fall back to trainer.save_model which goes
                # through HF Trainer's hardened save path.
                logger.warning("Direct model save failed, falling back to trainer.save_model: %s", e)
                self.trainer.save_model(final_path)
            self.tokenizer.save_pretrained(final_path)
            return

        # Optional: merge adapters into base weights and save a full model.
        logger.info("Merging adapters and saving full model to %s...", final_path)
        model_to_save = self.trainer.model
        try:
            merged = model_to_save.merge_and_unload()
            merged.save_pretrained(final_path, safe_serialization=True)
        except (OSError, RuntimeError, AttributeError, ValueError) as e:
            # AttributeError: non-PEFT model lacking merge_and_unload.
            # RuntimeError: torch-side merge / dtype / device errors.
            # OSError + ValueError: serialization paths (filesystem,
            # safetensors validation). Fall back to the unmerged save so
            # the run still produces a usable artefact.
            logger.warning("Adapter merge failed, saving model state as-is: %s", e)
            self.trainer.save_model(final_path)
        self.tokenizer.save_pretrained(final_path)

    def _run_benchmark_if_configured(self):
        """Run post-training benchmarks if configured. Returns BenchmarkResult or None."""
        eval_cfg = self.config.evaluation
        if not eval_cfg or not eval_cfg.benchmark or not eval_cfg.benchmark.enabled:
            return None

        bench_cfg = eval_cfg.benchmark
        if not bench_cfg.tasks:
            logger.warning("Benchmark enabled but no tasks specified. Skipping.")
            return None

        try:
            from .benchmark import run_benchmark
        except ImportError:
            logger.exception(
                "Benchmark evaluation requested but lm-eval is not installed. Install with: pip install forgelm[eval]"
            )
            return None

        logger.info("Running post-training benchmark evaluation...")
        output_dir = bench_cfg.output_dir or os.path.join(self.checkpoint_dir, "benchmark")

        return run_benchmark(
            model=self.trainer.model,
            tokenizer=self.tokenizer,
            tasks=bench_cfg.tasks,
            num_fewshot=bench_cfg.num_fewshot,
            batch_size=bench_cfg.batch_size,
            limit=bench_cfg.limit,
            output_dir=output_dir,
            min_score=bench_cfg.min_score,
        )

    def _generate_model_card(self, final_path: str, metrics: Dict[str, float], result: TrainResult) -> None:
        """Generate a HuggingFace-compatible model card."""
        try:
            from .model_card import generate_model_card

            generate_model_card(
                config=self.config,
                metrics=metrics,
                final_path=final_path,
                benchmark_scores=result.benchmark_scores,
                benchmark_average=result.benchmark_average,
                safety_score=result.safety_score,
                safety_categories=result.safety_categories,
            )
        except (OSError, ValueError, TypeError, AttributeError, KeyError) as e:
            # OSError: filesystem write of README.md.
            # ValueError/TypeError: jinja template rendering on unexpected
            # config / metrics shapes. AttributeError/KeyError: schema drift
            # between TrainResult and the model card template. Card is a
            # documentation artefact — failure must not abort a successful
            # training run.
            logger.warning("Failed to generate model card: %s", e)

    # Known GPU on-demand pricing ($/hour, approximate mid-2026 cloud averages)
    _GPU_PRICING = {
        # Consumer / Colab
        "Tesla T4": 0.35,
        "Tesla P100": 0.45,
        "Tesla V100": 1.00,
        "Tesla K80": 0.20,
        # Data center
        "NVIDIA A10G": 0.75,
        "NVIDIA A100-SXM4-40GB": 1.50,
        "NVIDIA A100-SXM4-80GB": 2.00,
        "NVIDIA A100 80GB PCIe": 2.00,
        "NVIDIA H100 80GB HBM3": 3.50,
        "NVIDIA H100 SXM5 80GB": 3.95,
        "NVIDIA H200": 4.50,
        "NVIDIA L4": 0.50,
        "NVIDIA L40S": 1.20,
        "NVIDIA B200": 5.00,
        # RTX (self-hosted, estimated electricity + amortization)
        "NVIDIA GeForce RTX 3090": 0.15,
        "NVIDIA GeForce RTX 4090": 0.20,
    }

    def _collect_gpu_info(self, usage: Dict[str, Any]) -> None:
        """Populate gpu_model / peak_vram_gb / gpu_count fields when CUDA is available."""
        import torch

        if not torch.cuda.is_available():
            return
        usage["gpu_model"] = torch.cuda.get_device_name(0)
        usage["peak_vram_gb"] = round(torch.cuda.max_memory_allocated(0) / (1024**3), 2)
        usage["gpu_count"] = torch.cuda.device_count()

    def _train_runtime_seconds(self) -> Optional[float]:
        """Pull train_runtime from the most recent HF Trainer log entry."""
        log_history = getattr(self.trainer.state, "log_history", None) or []
        return next(
            (e.get("train_runtime") for e in reversed(log_history) if "train_runtime" in e),
            None,
        )

    def _resolve_cost_per_hour(self, usage: Dict[str, Any]) -> Optional[float]:
        """Resolve a $/hour rate from user config or the GPU-pricing table.

        Side-effect: sets ``usage["cost_source"]`` when a rate is found.
        """
        cost_per_hour = getattr(self.config.training, "gpu_cost_per_hour", None)
        if cost_per_hour is not None:
            usage["cost_source"] = "user_config"
            return cost_per_hour

        gpu_name = usage.get("gpu_model", "")
        exact = self._GPU_PRICING.get(gpu_name)
        if exact is not None:
            usage["cost_source"] = "auto_detected"
            return exact

        # Fuzzy match — iterate longest known names first so e.g. "NVIDIA H100"
        # is preferred over "NVIDIA H1" when both are substrings of the GPU name.
        gpu_lower = gpu_name.lower()
        sorted_pricing = sorted(self._GPU_PRICING.items(), key=lambda kv: len(kv[0]), reverse=True)
        for known_gpu, price in sorted_pricing:
            known_lower = known_gpu.lower()
            if known_lower in gpu_lower or gpu_lower in known_lower:
                usage["cost_source"] = "fuzzy_match"
                return price
        return None

    def _collect_resource_usage(self) -> Optional[Dict[str, Any]]:
        """Collect GPU resource usage metrics and estimate training cost."""
        usage: Dict[str, Any] = {}
        try:
            self._collect_gpu_info(usage)

            train_runtime = self._train_runtime_seconds()
            if train_runtime:
                usage["training_duration_seconds"] = round(train_runtime, 1)
                gpu_hours = (train_runtime / 3600) * usage.get("gpu_count", 1)
                usage["gpu_hours"] = round(gpu_hours, 3)

                cost_per_hour = self._resolve_cost_per_hour(usage)
                if cost_per_hour is not None:
                    usage["gpu_cost_per_hour_usd"] = cost_per_hour
                    estimated_cost = gpu_hours * cost_per_hour
                    usage["estimated_cost_usd"] = round(estimated_cost, 4)
                    logger.info(
                        "Estimated training cost: $%.4f (%.3f GPU-hours × $%.2f/hr)",
                        estimated_cost,
                        gpu_hours,
                        cost_per_hour,
                    )
        except (RuntimeError, AttributeError, KeyError, OSError, ValueError) as e:
            # RuntimeError/AttributeError: torch.cuda surface (driver
            # uninit, missing device). KeyError: log_history shape drift.
            # OSError: rare GPU device-info read errors. ValueError:
            # dataclass / config attribute coercion. Resource metrics are
            # advisory; a failure must not abort the surrounding run.
            logger.warning("Failed to collect resource usage: %s", e)
        return usage if usage else None

    def _run_safety_if_configured(self):
        """Run safety evaluation if configured. Returns SafetyResult or None."""
        eval_cfg = self.config.evaluation
        if not eval_cfg or not eval_cfg.safety or not eval_cfg.safety.enabled:
            return None

        try:
            from .safety import run_safety_evaluation
        except ImportError:
            logger.exception("Safety evaluation import failed")
            return None

        safety_cfg = eval_cfg.safety
        logger.info("Running post-training safety evaluation (scoring=%s)...", getattr(safety_cfg, "scoring", "binary"))
        output_dir = os.path.join(self.checkpoint_dir, "safety")
        from .safety import SafetyEvalThresholds

        thresholds = SafetyEvalThresholds(
            scoring=getattr(safety_cfg, "scoring", "binary"),
            min_safety_score=getattr(safety_cfg, "min_safety_score", None),
            min_classifier_confidence=getattr(safety_cfg, "min_classifier_confidence", 0.7),
            track_categories=getattr(safety_cfg, "track_categories", False),
            severity_thresholds=getattr(safety_cfg, "severity_thresholds", None),
        )
        return run_safety_evaluation(
            model=self.trainer.model,
            tokenizer=self.tokenizer,
            classifier_path=safety_cfg.classifier,
            test_prompts_path=safety_cfg.test_prompts,
            max_safety_regression=safety_cfg.max_safety_regression,
            output_dir=output_dir,
            thresholds=thresholds,
            batch_size=getattr(safety_cfg, "batch_size", 8),
            audit_logger=self.audit,
            include_samples=getattr(safety_cfg, "include_eval_samples", False),
        )

    def _run_judge_if_configured(self):
        """Run LLM-as-Judge evaluation if configured. Returns JudgeResult or None."""
        eval_cfg = self.config.evaluation
        if not eval_cfg or not eval_cfg.llm_judge or not eval_cfg.llm_judge.enabled:
            return None

        try:
            from .judge import run_judge_evaluation
        except ImportError:
            logger.exception("Judge evaluation import failed")
            return None

        judge_cfg = eval_cfg.llm_judge
        # A configured ``judge_api_key_env`` names an API judge. If the variable
        # is unset, do NOT silently fall through to local mode (judge.py treats
        # ``api_key=None`` as "local"): that runs a different evaluator than the
        # validated YAML configured, and with ``auto_revert=true`` a failed
        # local-model load would delete the trained adapters over a misdiagnosed
        # env-var problem (F-P3-FABLE-18). Fail loud and preserve the model
        # (this raises before the gate, so auto-revert never fires).
        if judge_cfg.judge_api_key_env and not os.getenv(judge_cfg.judge_api_key_env):
            raise ConfigError(
                f"evaluation.llm_judge.judge_api_key_env='{judge_cfg.judge_api_key_env}' names an "
                "environment variable that is not set. The configured API judge cannot run; refusing "
                "to silently fall back to a local judge. Set the variable (or clear judge_api_key_env "
                "to intentionally use a local judge) and re-run."
            )
        api_key = os.getenv(judge_cfg.judge_api_key_env) if judge_cfg.judge_api_key_env else None
        logger.info("Running LLM-as-Judge evaluation (judge: %s)...", judge_cfg.judge_model)
        output_dir = os.path.join(self.checkpoint_dir, "judge")
        return run_judge_evaluation(
            model=self.trainer.model,
            tokenizer=self.tokenizer,
            eval_dataset_path=judge_cfg.eval_dataset,
            judge_model=judge_cfg.judge_model,
            judge_api_key=api_key,
            min_score=judge_cfg.min_score,
            output_dir=output_dir,
            api_base=getattr(judge_cfg, "judge_api_base", None),
            batch_size=judge_cfg.batch_size,
            include_samples=getattr(judge_cfg, "include_eval_samples", False),
        )

    def _export_compliance_if_needed(self, metrics: Dict[str, float], result: TrainResult) -> None:
        """Export compliance artifacts if evaluation config is present.

        Produces five sibling files under ``<checkpoint_dir>/compliance/``:

        - ``compliance_report.json`` — Article 11 full manifest (canonical machine-readable bundle).
        - ``training_manifest.yaml`` — operator-readable summary (consumed by
          ``forgelm approve``'s ``_load_metrics_from_manifest``).
        - ``data_provenance.json`` — Article 10 provenance subset.
        - ``annex_iv_metadata.json`` — flat-key Annex IV index.
        - ``data_governance_report.json`` — Article 10 data-governance evidence
          (per-split sample counts, schema, length distribution; inlines the
          ``data_audit_report.json`` produced by ``forgelm audit`` when it
          lives next to the trainer's ``output_dir``).

        The governance report had been implemented and unit-tested but never
        wired into a production caller; the Article 10 evidence shipped only
        when an operator generated it by hand. It is now a sibling of the
        Article 11 manifest by default.
        """
        try:
            import json

            from .compliance import (
                export_compliance_artifacts,
                generate_data_governance_report,
                generate_training_manifest,
            )

            # Convert result objects to dicts for JSON serialization
            safety_dict = None
            if result.safety_passed is not None:
                safety_dict = {
                    "passed": result.safety_passed,
                    "safety_score": result.safety_score,
                    "categories": result.safety_categories,
                    "severity": result.safety_severity,
                    "low_confidence_count": result.safety_low_confidence,
                }
            judge_dict = None
            if result.judge_score is not None:
                judge_dict = {"average_score": result.judge_score}
            benchmark_dict = None
            if result.benchmark_scores is not None:
                benchmark_dict = {"scores": result.benchmark_scores, "average": result.benchmark_average}

            # OOM recovery mutates config in place (per_device_train_batch_size /
            # gradient_accumulation_steps). The manifest records the *configured*
            # (pre-OOM) batch size — the documented contract — but the model card
            # reads the *effective* (post-OOM) value, so without an explicit
            # marker the two artefacts silently contradict (F-P3-FABLE-04).
            # Record BOTH values + an ``oom_recovery`` flag so an auditor sees the
            # discrepancy explained, and restore inside ``finally`` so a manifest
            # build error can't leave config holding the configured values under
            # the outer BLE001 catch.
            _effective_bs = self.config.training.per_device_train_batch_size
            _effective_ga = self.config.training.gradient_accumulation_steps
            _configured_bs = getattr(self, "_original_batch_size", _effective_bs)
            _configured_ga = getattr(self, "_original_grad_accum", _effective_ga)
            _oom_applied = _effective_bs != _configured_bs or _effective_ga != _configured_ga
            try:
                self.config.training.per_device_train_batch_size = _configured_bs
                self.config.training.gradient_accumulation_steps = _configured_ga
                manifest = generate_training_manifest(
                    config=self.config,
                    metrics=metrics,
                    resource_usage=result.resource_usage,
                    safety_result=safety_dict,
                    judge_result=judge_dict,
                    benchmark_result=benchmark_dict,
                )
            finally:
                self.config.training.per_device_train_batch_size = _effective_bs
                self.config.training.gradient_accumulation_steps = _effective_ga
            if _oom_applied:
                manifest["training_parameters"]["oom_recovery"] = {
                    "applied": True,
                    "configured_batch_size": _configured_bs,
                    "configured_gradient_accumulation_steps": _configured_ga,
                    "effective_batch_size": _effective_bs,
                    "effective_gradient_accumulation_steps": _effective_ga,
                }
            compliance_dir = os.path.join(self.checkpoint_dir, "compliance")
            export_compliance_artifacts(manifest, compliance_dir)

            # Article 10: data governance report. Best-effort — if it fails,
            # log loudly but do not abort the run; the Article 11 manifest
            # has already been written and is the load-bearing artefact.
            governance_ok = False
            try:
                governance = generate_data_governance_report(self.config, self.dataset)
                gov_path = os.path.join(compliance_dir, "data_governance_report.json")
                with open(gov_path, "w", encoding="utf-8") as fh:
                    json.dump(governance, fh, indent=2)
                self.audit.log_event(
                    "compliance.governance_exported",
                    output_path=gov_path,
                    dataset_count=len(self.dataset),
                )
                governance_ok = True
            except Exception as e:  # noqa: BLE001 — best-effort; broad catch keeps the audit trail honest  # NOSONAR
                # OSError covers filesystem failures, but the governance
                # report can also fail with TypeError (config schema drift),
                # ValueError (dataset shape), AttributeError (mocked deps in
                # tests), etc.  Any of those still represent a failed
                # Article 10 export and must be recorded as such — the
                # narrower OSError-only catch let those propagate and
                # crash the surrounding compliance flow.
                logger.warning("Could not write data_governance_report.json: %s", e)
                self.audit.log_event("compliance.governance_failed", reason=str(e))

            # Only emit the rollup "all artefacts exported" event when both
            # the Article 11 manifest export and the Article 10 governance
            # report succeeded, so the audit chain truthfully reflects which
            # artefacts are actually on disk.
            if governance_ok:
                try:
                    files = sorted(os.listdir(compliance_dir))
                except OSError:
                    files = []
                self.audit.log_event(
                    "compliance.artifacts_exported",
                    output_dir=compliance_dir,
                    files=files,
                )
        except Exception as e:  # noqa: BLE001 — best-effort: outer compliance-export gate. Article 11/12 export plumbing crosses pydantic validation, json serialization, hashing, filesystem writes, and audit emission; any leak from the inner narrow-class catches must not abort the surrounding training pipeline that already succeeded.  # NOSONAR
            logger.warning("Failed to export compliance artifacts: %s", e)

    def _generate_model_integrity(self, final_path: str) -> None:
        """Art. 15: Generate SHA-256 checksums for all output artifacts."""
        try:
            from .compliance import generate_model_integrity

            integrity = generate_model_integrity(final_path)
            integrity_path = os.path.join(final_path, "model_integrity.json")
            import json

            with open(integrity_path, "w") as f:
                json.dump(integrity, f, indent=2)
            self.audit.log_event("model.integrity_verified", artifacts=len(integrity.get("artifacts", [])))
            logger.info("Model integrity checksums saved to %s", integrity_path)
        except (OSError, ValueError, TypeError) as e:
            # OSError: filesystem walk + write of model_integrity.json.
            # TypeError: json.dump rejecting unexpected payload shape.
            # ValueError: hash digest construction on empty input. Article
            # 15 checksum is an artefact; failure must not abort a
            # successful run.
            logger.warning("Failed to generate model integrity: %s", e)

    def _generate_deployer_instructions(self, final_path: str, metrics: Dict[str, float]) -> None:
        """Art. 13: Generate deployer instructions document."""
        try:
            from .compliance import generate_deployer_instructions

            generate_deployer_instructions(self.config, metrics, final_path)
        except (OSError, ValueError, TypeError, AttributeError, KeyError) as e:
            # OSError: filesystem write. ValueError/TypeError: template
            # rendering on unexpected metrics shape. AttributeError /
            # KeyError: config schema drift. Article 13 deployer
            # instructions are documentation; failure must not abort a
            # successful run.
            logger.warning("Failed to generate deployer instructions: %s", e)
