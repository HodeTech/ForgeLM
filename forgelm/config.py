import logging
import math
import os
import warnings
from typing import Any, Dict, List, Literal, Optional, Tuple

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

logger = logging.getLogger("forgelm.config")


class MoeConfig(BaseModel):
    """MoE-specific fine-tuning configuration."""

    model_config = ConfigDict(extra="forbid")

    quantize_experts: bool = Field(default=False, description="Quantize inactive experts for VRAM savings.")
    experts_to_train: str = Field(default="all", description="`all` or comma-separated expert indices to train.")


class MultimodalConfig(BaseModel):
    """VLM multimodal fine-tuning configuration."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=False, description="Enable VLM multimodal fine-tuning path.")
    image_column: str = Field(default="image", description="Dataset column name for image paths or URLs.")
    text_column: str = Field(default="text", description="Dataset column name for text or captions.")


class MergeConfig(BaseModel):
    """Post-training model merging configuration."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Enable post-training model merging (native TIES / DARE / SLERP / linear on state dicts).",
    )
    method: Literal["ties", "dare", "slerp", "linear"] = Field(
        default="ties",
        description="Merge algorithm: `ties` (TIES-merging), `dare` (DARE), `slerp` (spherical interpolation), `linear` (weighted average).",
    )
    models: List[Dict[str, Any]] = Field(
        default=[],
        description="List of `{path, weight}` dicts naming the source models to merge.",
    )
    output_dir: str = Field(default="./merged_model", description="Directory to write the merged model into.")
    ties_trim_fraction: float = Field(
        default=0.2,
        ge=0.0,
        le=1.0,
        description=(
            "TIES merge: fraction of smallest-magnitude deltas trimmed per task "
            "(default `0.2` keeps the top ~80%; the published TIES default is sparser). "
            "Only consulted when `method` is `ties`."
        ),
    )
    dare_drop_rate: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description=(
            "DARE merge: probability each delta is randomly dropped before rescaling "
            "(default `0.3`; the DARE paper recommends 0.9+ for fine-tuned deltas). "
            "Only consulted when `method` is `dare`."
        ),
    )
    dare_seed: int = Field(
        default=42,
        description="DARE merge: RNG seed for the random drop mask, so a merge is reproducible run-to-run.",
    )

    @model_validator(mode="after")
    def _validate_merge_inputs(self):
        """Reject an enabled merge with an unusable source list at config time.

        Every merge algorithm needs at least two source models, each naming a
        ``path``.  Without this check the failure surfaces only when the
        no-train merge mode runs (`forgelm --merge`), where it lands on
        EXIT_TRAINING_ERROR instead of the EXIT_CONFIG_ERROR a config defect
        warrants — and ``--dry-run`` never catches it at all.
        """
        if not self.enabled:
            return self
        if len(self.models) < 2:
            raise ValueError(
                "merge.enabled is true but fewer than two source models are listed; "
                "every merge algorithm needs at least two `{path, weight}` entries in merge.models."
            )
        for entry in self.models:
            if "path" not in entry:
                raise ValueError("Each merge.models entry must carry a `path` key naming the source model.")
        return self


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name_or_path: str = Field(description="HuggingFace Hub repo ID or local path to the base model.")
    max_length: int = Field(
        default=2048,
        gt=0,
        description="Tokenizer/context max sequence length used during training.",
        json_schema_extra={"wizard": True},
    )
    load_in_4bit: bool = Field(default=True, description="Load the model in 4-bit NF4 quantisation (QLoRA path).")
    backend: Literal["transformers", "unsloth"] = Field(
        default="transformers",
        description="Model backend: `transformers` (HF stock) or `unsloth` (Linux + CUDA only, faster).",
    )
    trust_remote_code: bool = Field(
        default=False,
        description="Allow execution of model-bundled code.  Security: disabled by default for enterprise safety; set true only for models that explicitly require it.",
    )
    offline: bool = Field(
        default=False,
        description="Air-gapped mode: refuse HF Hub network calls.  Models/datasets/extras must be available locally.",
    )
    moe: Optional[MoeConfig] = Field(
        default=None, description="MoE-specific settings (only consulted on MoE checkpoints)."
    )
    multimodal: Optional[MultimodalConfig] = Field(
        default=None, description="VLM fine-tuning settings (only consulted for image-text models)."
    )
    bnb_4bit_use_double_quant: bool = Field(
        default=True,
        description="bitsandbytes: enable double-quantisation for the 4-bit codebook (small VRAM win).",
    )
    bnb_4bit_quant_type: Literal["nf4", "fp4"] = Field(
        default="nf4",
        description="bitsandbytes 4-bit quantisation scheme: `nf4` (recommended) or `fp4`.",
    )
    bnb_4bit_compute_dtype: Literal["auto", "bfloat16", "bf16", "float16", "fp16", "float32", "fp32"] = Field(
        default="auto",
        description="bitsandbytes 4-bit compute dtype: `auto` | `bfloat16` | `float16` | `float32` (each accepts the short `bf16`/`fp16`/`fp32` alias).  `float32` negates most VRAM savings.",
    )

    @model_validator(mode="after")
    def _warn_float32_qlora(self):
        if (
            self.load_in_4bit
            and isinstance(self.bnb_4bit_compute_dtype, str)
            and self.bnb_4bit_compute_dtype.lower() in ("fp32", "float32")
        ):
            logger.warning(
                "bnb_4bit_compute_dtype='float32' with load_in_4bit=True negates most VRAM savings. "
                "Consider 'bfloat16' or 'auto'."
            )
        return self


class LoraConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    r: int = Field(
        default=8,
        ge=1,
        description="LoRA rank: dimension of the low-rank update matrices.",
        json_schema_extra={"wizard": True},
    )
    alpha: int = Field(
        default=16,
        ge=1,
        description="LoRA scaling factor (typically `2 * r`).",
        json_schema_extra={"wizard": True},
    )
    dropout: float = Field(
        default=0.1,
        ge=0.0,
        le=1.0,
        description="Dropout rate applied to the LoRA update.",
        json_schema_extra={"wizard": True},
    )
    bias: Literal["none", "all", "lora_only"] = Field(
        default="none",
        description="Which bias parameters to train: `none` (no biases), `all`, or `lora_only` (LoRA-injected layers only).",
    )
    method: Literal["lora", "dora", "pissa", "rslora"] = Field(
        default="lora",
        description="PEFT method: `lora` (standard), `dora` (weight-decomposed), `pissa` (singular value initialised), `rslora` (rank-stabilised).",
    )
    use_dora: bool = Field(
        default=False,
        description='Deprecated boolean shortcut for `method="dora"`; kept for backward compatibility.',
    )
    use_rslora: bool = Field(
        default=False,
        description='Deprecated boolean shortcut for `method="rslora"`; rank-stabilised LoRA for high ranks (r>64).',
    )
    target_modules: List[str] = Field(
        default=["q_proj", "v_proj"],
        description="Module-name fragments LoRA is injected into (typically attention projections).",
    )
    task_type: str = Field(
        default="CAUSAL_LM", description="PEFT task type label (passed through to the PEFT library)."
    )

    @model_validator(mode="after")
    def _normalize_peft_method(self):
        # Reject contradictory deprecated flags rather than silently picking a
        # winner (F-P1-FAB-20).  Previously use_dora=True + use_rslora=True kept
        # both booleans (model.py re-ORs them into the PEFT config), and a
        # deprecated flag set against a non-matching explicit `method` was a
        # silent no-op.  Both are config-time mistakes → fail fast with exit 1.
        if self.use_dora and self.use_rslora:
            raise ValueError(
                "lora.use_dora and lora.use_rslora are mutually exclusive "
                "(DoRA and rsLoRA select different PEFT methods). Set a single "
                "`method:` ('dora' or 'rslora') instead; both deprecated flags "
                "are removed in v0.9.0."
            )
        if self.use_dora and self.method not in ("lora", "dora"):
            raise ValueError(
                f"lora.use_dora=True contradicts method='{self.method}'. "
                "Drop the deprecated flag and keep the explicit `method:`; "
                "use_dora is removed in v0.9.0."
            )
        if self.use_rslora and self.method not in ("lora", "rslora"):
            raise ValueError(
                f"lora.use_rslora=True contradicts method='{self.method}'. "
                "Drop the deprecated flag and keep the explicit `method:`; "
                "use_rslora is removed in v0.9.0."
            )
        # Emit the deprecation unconditionally whenever the deprecated flag is set —
        # including the compatible-redundant cases (use_dora + method='dora' or
        # use_rslora + method='rslora').  Previously the warning only fired when
        # method was 'lora', so operators who already wrote the correct explicit
        # method alongside the deprecated flag received no nudge to drop it before
        # v0.9.0 removal (F-L-09).
        if self.use_dora:
            message = "lora.use_dora=True is deprecated and removed in v0.9.0. Use method='dora' instead." + (
                " Automatically setting method='dora'." if self.method == "lora" else ""
            )
            logger.warning(message)
            warnings.warn(message, DeprecationWarning, stacklevel=2)
            if self.method == "lora":
                object.__setattr__(self, "method", "dora")
        if self.use_rslora:
            message = "lora.use_rslora=True is deprecated and removed in v0.9.0. Use method='rslora' instead." + (
                " Automatically setting method='rslora'." if self.method == "lora" else ""
            )
            logger.warning(message)
            warnings.warn(message, DeprecationWarning, stacklevel=2)
            if self.method == "lora":
                object.__setattr__(self, "method", "rslora")
        return self


class TrainingConfig(BaseModel):
    # populate_by_name lets users keep the legacy `grpo_max_new_tokens` field
    # name in their YAML even though the canonical attribute is now
    # `grpo_max_completion_length` (matches TRL's GRPOConfig field). Without
    # this flag, Pydantic would only accept the alias on input, never the
    # canonical name.
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    output_dir: str = Field(
        default="./checkpoints", description="Directory for intermediate checkpoints + audit log + compliance bundle."
    )
    final_model_dir: str = Field(
        default="final_model", description="Subdirectory of `output_dir` where the final promoted model lands."
    )
    merge_adapters: bool = Field(
        default=False,
        description="When SFT finishes, merge LoRA adapters into the base model (writes a full-weight model).",
    )
    trainer_type: Literal["sft", "orpo", "dpo", "simpo", "kto", "grpo"] = Field(
        default="sft",
        description="Alignment paradigm: `sft` (supervised), `orpo`, `dpo`, `simpo`, `kto`, or `grpo`.",
    )
    max_steps: int = Field(
        default=-1, ge=-1, description="Hard step cap; `-1` = use `num_train_epochs`, positive value overrides epochs."
    )
    num_train_epochs: int = Field(
        default=3,
        ge=1,
        description="Number of training epochs (only consulted when `max_steps == -1`).",
        json_schema_extra={"wizard": True},
    )
    per_device_train_batch_size: int = Field(
        default=4,
        ge=1,
        description="Micro-batch size per GPU.  Multiply by `gradient_accumulation_steps` × world size for effective batch.",
        json_schema_extra={"wizard": True},
    )
    gradient_accumulation_steps: int = Field(
        default=2,
        ge=1,
        description="Number of micro-batches to accumulate before each optimiser step.",
        json_schema_extra={"wizard": True},
    )
    learning_rate: float = Field(
        default=2e-5,
        gt=0,
        description="Peak learning rate.  LoRA / QLoRA usually tolerates 2e-4; full-finetune wants 2e-5.",
        json_schema_extra={"wizard": True},
    )
    warmup_ratio: float = Field(
        default=0.1, ge=0, le=1, description="Fraction of total steps spent warming up the learning rate from 0 → peak."
    )
    weight_decay: float = Field(default=0.01, ge=0, description="L2 weight-decay coefficient applied by the optimiser.")
    eval_steps: int = Field(default=200, ge=1, description="Run validation every N optimiser steps.")
    save_steps: int = Field(default=200, ge=1, description="Write a checkpoint every N optimiser steps.")
    save_total_limit: int = Field(default=3, ge=1, description="Retain at most N checkpoints (oldest evicted first).")
    packing: bool = Field(
        default=False, description="Pack short sequences into one to maximise GPU compute utilisation."
    )
    early_stopping_patience: int = Field(
        default=3, ge=1, description="Stop training after N evals without validation-loss improvement."
    )
    orpo_beta: float = Field(default=0.1, gt=0, description="ORPO odds-ratio weight (alignment paradigm parameter).")
    dpo_beta: float = Field(default=0.1, gt=0, description="DPO temperature parameter.")
    simpo_gamma: float = Field(default=0.5, ge=0, description="SimPO margin term.")
    simpo_beta: float = Field(default=2.0, gt=0, description="SimPO scaling parameter.")
    kto_beta: float = Field(default=0.1, gt=0, description="KTO loss parameter.")
    grpo_num_generations: int = Field(
        default=4, ge=2, description="GRPO: number of responses to generate per prompt during rollout."
    )
    # TRL >=0.12 renamed `max_new_tokens` to `max_completion_length` on GRPOConfig.
    # We mirror the TRL spelling, but accept the legacy name via Pydantic alias
    # so existing YAML configs and templates keep working without edits.
    grpo_max_completion_length: int = Field(
        default=512,
        ge=1,
        alias="grpo_max_new_tokens",
        description="GRPO: max tokens per generated completion (TRL field name).",
    )
    grpo_reward_model: Optional[str] = Field(
        default=None,
        description=(
            "GRPO: HF model path for reward scoring.  When None, the trainer wires "
            "`combined_format_length_reward` as a baseline (always-on, gradient-rich "
            "format + length shaping signal).  If the dataset additionally carries a "
            "`gold_answer` field (see the grpo-math template), a regex correctness "
            "reward is appended for additive scoring — TRL sums multiple reward funcs."
        ),
    )
    galore_enabled: bool = Field(
        default=False, description="GaLore: enable optimizer-level memory optimisation (alternative to LoRA)."
    )
    galore_optim: Literal[
        "galore_adamw",
        "galore_adamw_8bit",
        "galore_adafactor",
        "galore_adamw_layerwise",
        "galore_adamw_8bit_layerwise",
        "galore_adafactor_layerwise",
    ] = Field(
        default="galore_adamw",
        description="GaLore optimiser variant.  `_8bit` halves optimiser-state VRAM; `_layerwise` cuts peak by recomputing per-layer.",
    )
    galore_rank: int = Field(
        default=128, ge=1, description="GaLore: low-rank subspace dimension for gradient projection."
    )
    galore_update_proj_gap: int = Field(
        default=200,
        ge=1,
        description="GaLore: number of steps between SVD re-computations of the projection.",
    )
    galore_scale: float = Field(
        default=0.25, gt=0, description="GaLore: gradient scaling factor (analogous to LoRA alpha)."
    )
    galore_proj_type: Literal["std", "reverse_std", "right", "left", "full"] = Field(
        default="std",
        description="GaLore projection type.  `std` is the documented default; `full` disables projection (debug only).",
    )
    galore_target_modules: Optional[List[str]] = Field(
        default=None,
        description='GaLore target-module regexes.  `None` falls back to `[r".*.attn.*", r".*.mlp.*"]`.',
    )
    rope_scaling: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "RoPE scaling config for long-context fine-tuning, e.g. "
            '`{"type": "linear"|"dynamic"|"yarn"|"longrope", "factor": 4.0}`.'
        ),
    )
    neftune_noise_alpha: Optional[float] = Field(
        default=None,
        description="NEFTune: add Gaussian noise to embeddings during training (5.0 is a common value; improves SFT quality).",
    )
    sliding_window_attention: Optional[int] = Field(
        default=None,
        description="Override the model's sliding-window-attention size (e.g. 4096 for Mistral).  None = use the model default.",
    )
    sample_packing: bool = Field(
        default=False,
        description=(
            "Deprecated alias for `packing`; TRL exposes a single sequence-packing knob. "
            "Setting `sample_packing: true` forwards to `packing: true` with a "
            "`DeprecationWarning`. Removal scheduled for v0.9.0 — use `packing` instead."
        ),
    )
    oom_recovery: bool = Field(
        default=False, description="Auto-halve `per_device_train_batch_size` on CUDA OOM and retry."
    )
    oom_recovery_min_batch_size: int = Field(
        default=1, ge=1, description="Stop OOM retry once batch size reaches this floor; raise instead."
    )
    report_to: Literal["tensorboard", "wandb", "mlflow", "none"] = Field(
        default="tensorboard",
        description="Experiment-tracking backend.  `wandb` / `mlflow` require the `[tracking]` extra.",
    )
    run_name: Optional[str] = Field(default=None, description="W&B / MLflow run name.  Auto-generated when None.")
    gpu_cost_per_hour: Optional[float] = Field(
        default=None,
        ge=0,
        description="USD per hour for the training GPU.  None = auto-detect from known GPUs (used by the cost-estimation report).",
    )

    @model_validator(mode="after")
    def _forward_deprecated_sample_packing(self):
        """Forward the deprecated ``sample_packing`` flag onto ``packing``.

        ``sample_packing`` was historically documented as a functional
        sequence-packing knob but was never consumed by the trainer (TRL's
        ``SFTConfig`` exposes a single ``packing`` parameter), so an operator
        who set it got a silent no-op.  We now alias it to ``packing`` so the
        documented behaviour actually fires during the deprecation window, and
        emit both a ``DeprecationWarning`` (for ``-W error`` / CI deprecation
        sweeps) and a ``logger.warning`` (visible on the CLI path), mirroring
        the ``lora.use_dora`` alias pattern.  Removal target: v0.9.0.
        """
        if self.sample_packing:
            # Always notify: emit when the deprecated flag is set regardless of
            # whether packing is also true.  Previously the guard was
            # ``if self.sample_packing and not self.packing``, which silently
            # swallowed the deprecation when an operator wrote both
            # ``sample_packing: true`` and ``packing: true``, leaving them with
            # no nudge to remove the deprecated key before v0.9.0 removal (F-M-14).
            logger.warning(
                "training.sample_packing is deprecated and forwards to training.packing. "
                "Use `packing: true` instead; sample_packing is removed in v0.9.0."
            )
            warnings.warn(
                "`training.sample_packing` is deprecated and forwards to "
                "`training.packing`. Use `packing: true` instead; the deprecated "
                "field is removed in v0.9.0.",
                DeprecationWarning,
                stacklevel=2,
            )
            if not self.packing:
                object.__setattr__(self, "packing", True)
        return self


class DistributedConfig(BaseModel):
    """Configuration for multi-GPU distributed training via DeepSpeed or FSDP."""

    model_config = ConfigDict(extra="forbid")

    strategy: Optional[Literal["deepspeed", "fsdp"]] = Field(
        default=None,
        description="Distributed strategy: `deepspeed`, `fsdp`, or `None` for single-GPU (no distributed wrapping).",
    )
    deepspeed_config: Optional[str] = Field(
        default=None,
        description="DeepSpeed config: filesystem path to a DS JSON OR preset name (`zero2`, `zero3`, `zero3_offload`).",
    )
    fsdp_strategy: Literal["full_shard", "shard_grad_op", "no_shard", "hybrid_shard"] = Field(
        default="full_shard",
        description="FSDP sharding strategy.  `full_shard` is the production default; `hybrid_shard` for multi-node intra-node sharding.",
    )
    fsdp_auto_wrap: bool = Field(default=True, description="FSDP: auto-wrap transformer layers (recommended).")
    fsdp_offload: bool = Field(
        default=False, description="FSDP: offload parameters to CPU between forward and backward (slower, less VRAM)."
    )
    fsdp_backward_prefetch: Literal["backward_pre", "backward_post"] = Field(
        default="backward_pre",
        description="FSDP backward-prefetch policy.  `backward_pre` overlaps comm + compute; `backward_post` is more memory-conservative.",
    )
    fsdp_state_dict_type: Literal["FULL_STATE_DICT", "SHARDED_STATE_DICT"] = Field(
        default="FULL_STATE_DICT",
        description="FSDP checkpoint format.  `FULL_STATE_DICT` consolidates to rank 0 (HF-compatible); `SHARDED_STATE_DICT` keeps shards separate.",
    )


class DataGovernanceConfig(BaseModel):
    """Art. 10: Data governance metadata."""

    model_config = ConfigDict(extra="forbid")

    collection_method: str = Field(
        default="", description="Article 10(2)(b): how the training data was collected (free-text)."
    )
    annotation_process: str = Field(
        default="", description="Article 10(2)(b): annotation / labelling methodology (free-text)."
    )
    known_biases: str = Field(
        default="", description="Article 10(2)(f): documented data biases the operator is aware of."
    )
    personal_data_included: bool = Field(
        default=False,
        description="Article 10(5): whether the training data contains personal data of identifiable subjects.",
    )
    dpia_completed: bool = Field(
        default=False, description="Article 35 GDPR: Data Protection Impact Assessment completed for this dataset."
    )


class DataConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_name_or_path: str = Field(
        description="Primary dataset: HuggingFace Hub ID, local JSONL path, or directory of JSONL files."
    )
    extra_datasets: Optional[List[str]] = Field(
        default=None, description="Additional datasets to mix in alongside the primary."
    )
    mix_ratio: Optional[List[float]] = Field(
        default=None,
        description="Per-dataset weight (primary + extras).  Uniform when None; values must be non-negative and not all zero.",
    )
    shuffle: bool = Field(default=True, description="Shuffle the merged corpus before splitting train/validation.")
    clean_text: bool = Field(
        default=True, description="Strip excessive whitespace + control characters before tokenisation."
    )
    add_eos: bool = Field(
        default=True, description="Append the EOS token to every example so generation knows where to stop."
    )
    governance: Optional[DataGovernanceConfig] = Field(
        default=None, description="EU AI Act Article 10 data governance metadata."
    )

    @field_validator("mix_ratio")
    @classmethod
    def _validate_mix_ratio(cls, v):
        if v is not None:
            # Reject NaN / inf before the comparison checks: `nan < 0` and
            # `nan == 0` are both False, so a non-finite weight would otherwise
            # slip through and crash later in `data._apply_mix_ratio` at
            # `int(max_dataset_size * nan)` (a runtime exit-2 instead of a
            # config-time exit-1).
            if any(not math.isfinite(r) for r in v):
                raise ValueError("mix_ratio values must be finite (no NaN or inf).")
            if any(r < 0 for r in v):
                raise ValueError("mix_ratio values must be non-negative.")
            if all(r == 0 for r in v):
                raise ValueError("mix_ratio values cannot all be zero.")
        return v

    @model_validator(mode="after")
    def _validate_mix_ratio_length(self):
        """Require one mix_ratio weight per dataset (primary + extras).

        The field-level validator above cannot see ``extra_datasets``, so the
        cross-field length check lives here.  A mismatch used to validate
        cleanly and silently fall back to uniform mixing at runtime
        (``data._merge_extra_datasets``) — i.e. the operator's declared
        mixture was replaced by a different one with no error.
        """
        if self.mix_ratio is not None:
            expected = 1 + len(self.extra_datasets or [])
            if len(self.mix_ratio) != expected:
                raise ValueError(
                    f"mix_ratio length ({len(self.mix_ratio)}) must equal the dataset count "
                    f"({expected} = 1 primary + {len(self.extra_datasets or [])} extra_datasets). "
                    "List one weight per dataset, primary first."
                )
        return self


class BenchmarkConfig(BaseModel):
    """Configuration for post-training benchmark evaluation via lm-evaluation-harness."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=False, description="Enable lm-evaluation-harness benchmark scoring after training.")
    tasks: List[str] = Field(default=[], description='lm-eval task names (e.g. `["arc_easy", "hellaswag", "mmlu"]`).')
    num_fewshot: Optional[int] = Field(
        default=None, description="Few-shot example count.  None = use the task's documented default."
    )
    batch_size: str = Field(default="auto", description='lm-eval batch size: `"auto"` or an integer string.')
    limit: Optional[int] = Field(default=None, description="Cap samples per task for quick checks.  None = full task.")
    output_dir: Optional[str] = Field(
        default=None, description="Where to save benchmark results JSON.  Defaults to the training output_dir."
    )
    min_score: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Minimum average accuracy (0.0–1.0).  When set + auto_revert=True, falling below triggers an auto-revert to the prior model.",
    )

    @model_validator(mode="after")
    def _enabled_requires_tasks(self):
        """Reject an enabled benchmark gate with no tasks (F-P1-FAB-19).

        ``enabled=True`` + ``tasks=[]`` previously validated cleanly, then
        ``trainer.py`` short-circuited to a skip-as-pass at runtime — the gate
        the operator explicitly enabled (possibly with ``min_score`` +
        ``auto_revert``) never executed and emitted no benchmark audit event.
        That is a silently-disabled decision gate; fail fast at config time
        (exit 1) so the operator lists a task or disables the block.
        """
        if self.enabled and not self.tasks:
            raise ValueError(
                "evaluation.benchmark.enabled is true but tasks is empty; "
                "list at least one lm-eval task (e.g. ['arc_easy']) or set "
                "enabled: false."
            )
        return self


class SafetyConfig(BaseModel):
    """Post-training safety evaluation configuration."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=False, description="Enable post-training safety evaluation.")
    classifier: str = Field(
        default="meta-llama/Llama-Guard-3-8B", description="Harm classifier model (HF Hub ID or local path)."
    )
    test_prompts: str = Field(
        default="safety_prompts.jsonl", description="Path to JSONL file with adversarial test prompts."
    )
    max_safety_regression: float = Field(
        default=0.05,
        ge=0.0,
        le=1.0,
        description="Maximum allowed unsafe-response ratio (0.0–1.0).  Auto-revert triggers when exceeded.",
    )
    scoring: Literal["binary", "confidence_weighted"] = Field(
        default="binary",
        description="Scoring scheme: `binary` (safe/unsafe per response) or `confidence_weighted` (Llama Guard probability).",
    )
    min_safety_score: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description='Weighted score threshold (0.0–1.0); used when `scoring="confidence_weighted"`.',
    )
    min_classifier_confidence: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="Flag responses with classifier confidence below this floor for human review.",
    )
    track_categories: bool = Field(
        default=False, description="Parse Llama Guard S1-S14 harm categories per-response and surface in the report."
    )
    severity_thresholds: Optional[Dict[str, float]] = Field(
        default=None,
        description='Per-severity limits: e.g. `{"critical": 0, "high": 0.01}`.  Auto-revert when exceeded.',
    )
    batch_size: int = Field(
        default=8, ge=1, description="Batched generation size for safety evaluation.  1 disables batching."
    )
    include_eval_samples: bool = Field(
        default=False,
        description=(
            "Persist raw `prompt` / `response` strings to `safety_results.json`.  "
            "Default OFF for GDPR / EU AI Act Art. 10 privacy by default — adversarial "
            "test prompts and model responses may surface sensitive content.  Opt in "
            "for debugging."
        ),
    )

    @model_validator(mode="after")
    def _validate_safety_gates(self):
        """Reject reachable states that silently disable a configured safety gate.

        Each branch closes an auto-revert bypass where the operator configured
        a gate but the runtime evaluator (``safety.py``) would never fire it,
        while ``safety.evaluation_completed`` still records ``passed=True``
        (F-P1-FAB-04/07/08, F-P3-FABLE-15 / XP-06).
        """
        # Guard: skip cross-field consistency checks when safety evaluation is
        # disabled.  An operator who disables the gate may leave threshold fields
        # from a previous enabled run in their YAML; rejecting those at config
        # time is user-hostile and inconsistent with the sister validators
        # ``_validate_merge_inputs`` (line ~85) and ``_validate_synthetic_payload``
        # (line ~943) which both early-return when ``enabled=False`` (F-M-13).
        if not self.enabled:
            return self

        # (1) min_safety_score is only consulted under confidence_weighted
        # scoring (safety.py); set under binary scoring it is a dead threshold.
        if self.min_safety_score is not None and self.scoring != "confidence_weighted":
            raise ValueError(
                "evaluation.safety.min_safety_score is only enforced when "
                'scoring="confidence_weighted" (current scoring='
                f'"{self.scoring}"); under binary scoring the threshold is '
                "silently ignored.  Set scoring to confidence_weighted or "
                "remove min_safety_score."
            )

        # (2) severity_thresholds is only consulted when track_categories is on
        # (safety.py gates on `severity_thresholds and track_categories`).  An
        # operator who set per-severity limits clearly intends enforcement, so
        # auto-enable category tracking rather than silently dropping the gate.
        if self.severity_thresholds and not self.track_categories:
            if "track_categories" in self.model_fields_set:
                # Operator explicitly wrote ``track_categories: false`` alongside
                # ``severity_thresholds``; the two settings directly contradict —
                # auto-enabling would silently override a deliberate choice (F-M-15).
                raise ValueError(
                    "evaluation.safety.severity_thresholds requires track_categories=True "
                    "to be enforced, but track_categories was explicitly set to false. "
                    "Set track_categories to true or remove severity_thresholds."
                )
            # track_categories is the default (not explicitly set) → auto-enable and
            # record the mutation in __pydantic_fields_set__ so it survives a
            # ``model_dump(exclude_unset=True)`` round-trip in pipeline stage merges.
            # Without adding it to model_fields_set, the auto-enabled value is
            # excluded from the dump, re-validation re-runs this branch, and the
            # warning fires N+1 times for an N-stage pipeline (F-M-15).
            logger.warning(
                "evaluation.safety.severity_thresholds requires track_categories=True "
                "to be enforced; auto-enabling track_categories."
            )
            object.__setattr__(self, "track_categories", True)
            object.__setattr__(
                self,
                "__pydantic_fields_set__",
                self.model_fields_set | {"track_categories"},
            )

        # (3) restrict severity_thresholds to the known vocabulary and 0.0–1.0
        # values so a typo'd/wrongly-cased key cannot validate and then never
        # match a distribution bucket (permanently inert), and an out-of-range
        # value cannot make the per-severity gate unfireable (>1.0) or fire
        # unconditionally (<0.0).
        if self.severity_thresholds:
            for key, value in self.severity_thresholds.items():
                if key not in SEVERITY_LEVELS:
                    raise ValueError(
                        f"evaluation.safety.severity_thresholds key {key!r} is not a "
                        f"recognized severity level; allowed: {list(SEVERITY_LEVELS)}."
                    )
                if not 0.0 <= value <= 1.0:
                    raise ValueError(
                        f"evaluation.safety.severity_thresholds[{key!r}] must be in [0.0, 1.0], got {value}."
                    )
        return self


class JudgeConfig(BaseModel):
    """LLM-as-Judge evaluation configuration."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=False, description="Enable LLM-as-Judge scoring after training.")
    judge_model: str = Field(
        default="gpt-4o", description="Judge model: API model name (e.g. `gpt-4o`) or local model path."
    )
    judge_api_key_env: Optional[str] = Field(
        default=None, description="Env var name carrying the judge API key.  None = local judge model."
    )
    judge_api_base: Optional[str] = Field(
        default=None, description="Override the judge API base URL (Azure OpenAI, self-hosted vLLM, etc.)."
    )
    eval_dataset: str = Field(default="eval_prompts.jsonl", description="JSONL file of evaluation prompts to score.")
    min_score: float = Field(
        default=5.0,
        ge=1.0,
        le=10.0,
        description="Minimum average judge score (1–10 scale) to consider the model passing.",
    )
    batch_size: int = Field(
        default=8,
        ge=1,
        description="Batched fine-tuned-model generation size during judge evaluation.  1 disables batching.",
    )
    include_eval_samples: bool = Field(
        default=False,
        description=(
            "Persist raw eval `prompt`, `response`, and judge `reason` strings to "
            "`judge_results.json`.  Default OFF for GDPR / EU AI Act Art. 10 "
            "privacy by default — judge reasoning can quote PII from the eval set.  "
            "Opt in for debugging."
        ),
    )

    @model_validator(mode="after")
    def _warn_extreme_min_score(self):
        """Warn when min_score is so close to the scale edges that the gate is trivial.

        ``min_score <= 2.0`` means 'any response scoring above 1/10 passes' —
        effectively a no-op gate where auto_revert never fires.  ``min_score >= 9.0``
        means 'only near-perfect responses pass' — an always-failing gate in practice.
        Neither extreme raises a ValidationError (both are within the ``ge=1.0,
        le=10.0`` bounds) but operators who write these values are likely configuring
        an unintentional no-op or impossible gate (F-L-10).
        """
        if self.min_score <= 2.0:
            logger.warning(
                "evaluation.llm_judge.min_score=%.1f is near the scale minimum "
                "(1–10); the judge gate passes for almost any response. "
                "Consider raising min_score.",
                self.min_score,
            )
        elif self.min_score >= 9.0:
            logger.warning(
                "evaluation.llm_judge.min_score=%.1f is near the scale maximum "
                "(1–10); the judge gate will rarely pass. "
                "Consider lowering min_score.",
                self.min_score,
            )
        return self


class EvaluationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auto_revert: bool = Field(
        default=False,
        description="Restore the pre-training model on quality regression (loss / benchmark / safety threshold).",
    )
    max_acceptable_loss: Optional[float] = Field(
        default=None,
        description="Hard cap on validation loss.  When exceeded + auto_revert=True, training auto-reverts.",
    )
    baseline_loss: Optional[float] = Field(
        default=None,
        description="Pre-training baseline loss for regression detection.  Auto-computed when validation set exists.",
    )
    benchmark: Optional[BenchmarkConfig] = Field(
        default=None, description="Post-training benchmark via lm-evaluation-harness."
    )
    safety: Optional[SafetyConfig] = Field(default=None, description="Post-training safety evaluation block.")
    llm_judge: Optional[JudgeConfig] = Field(default=None, description="LLM-as-Judge scoring block.")
    require_human_approval: bool = Field(
        default=False,
        description="Article 14: pause the pipeline for human review (stages model under `final_model.staging.<run_id>/` and exits 4).",
    )
    # ``final_model.staging.<run_id>/`` retention horizon for `forgelm reject` paths.
    # Documented now (v0.5.5) so operators can plan their evidence-preservation
    # policy; auto-deletion enforcement is deferred to Phase 21 (GDPR
    # right-to-erasure) where it lands alongside the broader retention
    # framework. Setting the value today has no runtime effect — it is
    # surfaced in the compliance manifest so reviewers can audit the policy.
    staging_ttl_days: int = Field(
        default=7,
        ge=0,
        description=(
            "Article 14: number of days to retain `final_model.staging.<run_id>/` after a "
            "`forgelm reject` decision before scheduled cleanup. Zero means retain "
            "indefinitely. Auto-deletion enforcement is deferred to Phase 21 "
            "(GDPR right-to-erasure)."
        ),
    )


# EU AI Act risk taxonomy — single source of truth shared by
# ``RiskAssessmentConfig.risk_category`` and
# ``ComplianceMetadataConfig.risk_classification`` so the two Pydantic fields
# can never drift.  ``unacceptable`` covers Article 5 prohibited practices;
# ``high-risk`` covers Article 6 systems requiring full Annex IV documentation;
# ``limited-risk`` and ``minimal-risk`` cover the transparency-only and
# unrestricted tiers; ``unknown`` is the explicit placeholder for systems that
# have not yet been classified.  The default for both fields stays
# ``"minimal-risk"`` so existing configs validate unchanged.
RiskTier = Literal["unknown", "minimal-risk", "limited-risk", "high-risk", "unacceptable"]

# Tiers that demand full Annex IV documentation + auto-revert + safety gates
# under the EU AI Act.  Keep this set in lockstep with
# ``ForgeConfig._warn_high_risk_compliance`` and the wizard prompt so the new
# tier is reachable + enforced everywhere the old high-risk-only set was.
_STRICT_RISK_TIERS: frozenset[str] = frozenset({"high-risk", "unacceptable"})

# Canonical safety severity vocabulary shared between config.py (validator)
# and safety.py (runtime).  Defined here so the Config layer does not import
# from the Quality layer (safety.py) — the architecture standard
# (docs/standards/architecture.md) has no CONFIG → SAFETY directed edge.
# safety.py keeps its own copy to avoid a circular import until a shared
# ``forgelm._constants`` module is introduced (tracked separately).
SEVERITY_LEVELS: tuple[str, ...] = ("critical", "high", "medium", "low")


class RiskAssessmentConfig(BaseModel):
    """Art. 9: Risk management — declare risks before training."""

    model_config = ConfigDict(extra="forbid")

    intended_use: str = Field(
        default="", description="Article 9(2)(a): the intended purpose of the system (free-text)."
    )
    foreseeable_misuse: List[str] = Field(
        default=[], description="Article 9(2)(b): reasonably-foreseeable misuse scenarios the deployer must mitigate."
    )
    risk_category: RiskTier = Field(
        default="minimal-risk",
        description="Article 6 risk tier.  `high-risk` and `unacceptable` trigger Annex IV documentation requirements.",
    )
    mitigation_measures: List[str] = Field(
        default=[], description="Article 9(2)(c): operator-supplied mitigation steps (free-text list)."
    )
    vulnerable_groups_considered: bool = Field(
        default=False,
        description="Article 9(2)(b): the operator considered potential impact on vulnerable groups (children, minorities, etc.).",
    )


class MonitoringConfig(BaseModel):
    """Art. 12+17: Post-market monitoring hooks."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=False, description="Enable Article 12 post-market monitoring hooks.")
    endpoint: str = Field(
        default="", description="Monitoring-system webhook URL (Prometheus push gateway / Datadog / custom)."
    )
    endpoint_env: Optional[str] = Field(
        default=None, description="Env var name carrying the endpoint URL (overrides `endpoint` when set)."
    )
    metrics_export: Literal["none", "prometheus", "datadog", "custom_webhook"] = Field(
        default="none",
        description="Metrics exporter: `none`, `prometheus`, `datadog`, or `custom_webhook`.",
    )
    alert_on_drift: bool = Field(
        default=True, description="Emit a webhook alert when drift detector flags a regression."
    )
    check_interval_hours: int = Field(default=24, ge=1, description="Monitoring check cadence in hours.")


class ComplianceMetadataConfig(BaseModel):
    """Art. 11 + Annex IV: Provider and system metadata for technical documentation."""

    model_config = ConfigDict(extra="forbid")

    provider_name: str = Field(default="", description="Annex IV §1: legal-entity name of the system provider.")
    provider_contact: str = Field(
        default="", description="Annex IV §1: provider's regulatory point of contact (email or phone)."
    )
    system_name: str = Field(default="", description="Annex IV §1: human-readable system name (operator-chosen).")
    intended_purpose: str = Field(
        default="", description="Annex IV §1: declared intended purpose of the system (free-text)."
    )
    known_limitations: str = Field(
        default="", description="Annex IV §3: documented system limitations the operator is aware of."
    )
    system_version: str = Field(default="", description="Annex IV §1: operator-supplied system version string.")
    risk_classification: RiskTier = Field(
        default="minimal-risk",
        description="Article 6 risk tier classification (paired with `risk_assessment.risk_category`).",
    )


class SyntheticConfig(BaseModel):
    """Synthetic data generation via teacher→student distillation."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=False, description="Enable synthetic-data generation.")
    teacher_model: str = Field(
        default="", description="HF Hub ID or API model name (e.g. `gpt-4`, `meta-llama/Llama-3-70B`)."
    )
    teacher_backend: Literal["api", "local", "file"] = Field(
        default="api",
        description="Teacher backend: `api` (OpenAI/Anthropic), `local` (HF), `file` (read pre-generated JSONL).",
    )
    api_base: str = Field(default="", description="API endpoint (e.g. `https://api.openai.com/v1`).")
    api_key: Optional[str] = Field(
        default=None, description="API key.  Prefer `api_key_env` to avoid committing secrets."
    )
    api_key_env: Optional[str] = Field(
        default=None, description="Env var name carrying the API key (e.g. `OPENAI_API_KEY`)."
    )
    api_delay: float = Field(default=0.5, ge=0.0, description="Seconds between API calls (rate limiting).")
    api_timeout: int = Field(
        default=60,
        ge=10,
        description="Per-call API timeout in seconds (floored at 10s by the SSRF-guarded HTTP chokepoint).",
    )
    seed_file: str = Field(
        default="", description="Path to seed prompts file (JSONL or plain text, one prompt per line)."
    )
    seed_prompts: List[str] = Field(default=[], description="Inline seed prompts (alternative to `seed_file`).")
    system_prompt: str = Field(default="", description="System prompt prepended on every teacher call.")
    max_new_tokens: int = Field(default=1024, ge=1, description="Max tokens per teacher response.")
    temperature: float = Field(default=0.7, ge=0.0, description="Sampling temperature passed to the teacher.")
    output_file: str = Field(default="synthetic_data.jsonl", description="Output JSONL file path.")
    output_format: Literal["messages", "instruction", "chatml", "prompt_response"] = Field(
        default="messages",
        description=(
            "Output format: `messages` (chat-style array), `instruction` (Alpaca-style), "
            "`chatml`, or `prompt_response`. NOTE: `chatml` emits ForgeLM's legacy "
            "`{User, Assistant}` key layout (which `data.py` trains on natively), NOT "
            "OpenAI `<|im_start|>` ChatML markup — pick `messages` if you need a "
            "portable chat format for external tools."
        ),
    )
    min_success_rate: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description=(
            "Minimum fraction of seed prompts that must yield a usable example for "
            "`forgelm --generate-data` to report success (exit 0). Default `0.0` keeps "
            "the legacy behaviour (any non-zero yield succeeds); raise it so a CI "
            "pipeline does not train on a near-empty dataset from a mostly-failed run."
        ),
    )
    sanity_failure_rate: float = Field(
        default=0.2,
        ge=0.0,
        le=1.0,
        description=(
            "Failure-rate (0.0–1.0) above which `forgelm --generate-data` logs a WARNING "
            "that the dataset may be small or skewed — independent of `min_success_rate`, "
            "which gates the exit code. Default `0.2` warns when more than 20% of prompts fail."
        ),
    )

    @model_validator(mode="after")
    def _validate_synthetic_payload(self):
        """Reject an enabled synthetic block with an unusable payload at config time.

        An enabled generation needs a teacher to call and seeds to expand; the
        ``file`` backend reads pre-generated JSONL so it only needs a seed
        source.  Without this check the run no-ops or fails at the first
        teacher call (EXIT_TRAINING_ERROR) instead of being rejected at config
        load / ``--dry-run`` with EXIT_CONFIG_ERROR.
        """
        if not self.enabled:
            return self
        if self.teacher_backend != "file" and not self.teacher_model:
            raise ValueError(
                "synthetic.enabled is true but teacher_model is empty; "
                "set synthetic.teacher_model (or use teacher_backend: file with a seed source)."
            )
        if not self.seed_file and not self.seed_prompts:
            raise ValueError(
                "synthetic.enabled is true but no seeds are provided; "
                "set synthetic.seed_file or synthetic.seed_prompts."
            )
        return self

    @model_validator(mode="after")
    def _warn_direct_api_key(self):
        if self.api_key and not self.api_key_env:
            logger.warning(
                "synthetic.api_key is set directly in config. "
                "Prefer api_key_env to avoid accidentally committing secrets to version control."
            )
        return self

    def model_dump(self, **kwargs):
        """Redact api_key from serialized output."""
        d = super().model_dump(**kwargs)
        if d.get("api_key"):
            d["api_key"] = "***REDACTED***"
        return d


class WebhookConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: Optional[str] = Field(
        default=None,
        description="Webhook URL (Slack / Teams / Discord / custom).  Use `url_env` to read from env to avoid committing secrets.",
    )
    url_env: Optional[str] = Field(
        default=None, description="Env var name carrying the webhook URL (overrides `url` when set)."
    )
    notify_on_start: bool = Field(default=True, description="POST a `notify_start` event when training begins.")
    notify_on_success: bool = Field(
        default=True, description="POST a `notify_success` event when training completes successfully."
    )
    notify_on_failure: bool = Field(
        default=True, description="POST a `notify_failure` event when training fails (any non-zero exit)."
    )
    timeout: int = Field(
        default=10,
        ge=1,
        description=(
            "HTTP request timeout in seconds.  Clamped to ≥ 1s by the notifier.  "
            "Default raised to 10s in v0.5.5 (was 5s) — Slack/Teams gateway latency "
            "spikes regularly cross 5s in production, and a webhook timeout silently "
            "degrades the audit chain (webhook failure is best-effort)."
        ),
    )
    allow_private_destinations: bool = Field(
        default=False,
        description="SSRF opt-in.  Webhooks default to public-internet destinations only; in-cluster Slack proxies / on-prem Teams gateways need this set.",
    )
    require_https: bool = Field(
        default=False,
        description=(
            "TLS-only enforcement.  When True, a plaintext `http://` webhook URL is "
            "refused (the SSRF chokepoint raises) instead of warned-and-sent.  Default "
            "False preserves the documented warn-then-send behaviour; set True on a "
            "regulated estate to make cleartext delivery a hard failure."
        ),
    )
    tls_ca_bundle: Optional[str] = Field(
        default=None,
        description="Path to a custom CA bundle forwarded as `requests`'s `verify=` argument (corporate MITM CA on regulated estates).  None = bundled certifi CA store.",
    )


class AuthConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hf_token: Optional[str] = Field(
        default=None,
        description="HuggingFace Hub access token.  Auto-redacted from log output and serialised manifests.",
    )

    def __repr__(self) -> str:
        return "AuthConfig(hf_token='***')" if self.hf_token else "AuthConfig(hf_token=None)"

    def model_dump(self, **kwargs):
        """Override to always exclude token from serialization."""
        data = super().model_dump(**kwargs)
        if "hf_token" in data and data["hf_token"]:
            data["hf_token"] = "***REDACTED***"
        return data


class RetentionConfig(BaseModel):
    """Phase 21 / GDPR Article 5(1)(e) storage limitation + Article 17
    erasure horizons.

    Top-level retention block (per Phase 20 design `gdpr-erasure-design`
    §3 + closure-plan §15.5 v2.5).  All four horizons default to values
    chosen to be compliant out of the box for typical enterprise EU AI
    Act use:  audit logs at 5 years (Article 12 record-keeping
    obligation × statute-of-limitations buffer), staging at 7 days
    (operator gets one work-week to act on a `forgelm reject`),
    ephemeral artefacts at 90 days (compliance bundle + audit reports
    have a quarterly review cadence in most QMS), raw documents at 90
    days (typical ingestion-window before re-running data audit).

    Setting any horizon to ``0`` disables the policy for that artefact
    kind (retain indefinitely).  ``enforce`` controls how the trainer
    pre-flight gate reacts to violations:  ``log_only`` records a
    notice, ``warn_on_excess`` emits a structured warning, and
    ``block_on_excess`` aborts training with EXIT_EVAL_FAILURE so a
    regulated CI cannot accidentally extend the retention horizon by
    re-using a stale workspace.
    """

    model_config = ConfigDict(extra="forbid")

    audit_log_retention_days: int = Field(
        default=1825,
        ge=0,
        description=(
            "Days to retain `audit_log.jsonl` before flagging it as overdue under Article 5(1)(e). "
            "Default 1825 = 5 years.  Set to 0 to retain indefinitely (Article 17(3)(b) defence)."
        ),
    )
    staging_ttl_days: int = Field(
        default=7,
        ge=0,
        description=(
            "Days to retain `final_model.staging.<run_id>/` after a `forgelm reject` decision before scheduled cleanup. "
            "Set to 0 to retain indefinitely.  Replaces (and supersedes) the deprecated "
            "`evaluation.staging_ttl_days`; both fields are accepted with identical values during the deprecation window (legacy field removed in v0.8.0)."
        ),
    )
    ephemeral_artefact_retention_days: int = Field(
        default=90,
        ge=0,
        description=(
            "Days to retain compliance bundles, data audit reports, and other run-scoped derived artefacts. "
            "Set to 0 to retain indefinitely."
        ),
    )
    raw_documents_retention_days: int = Field(
        default=90,
        ge=0,
        description=(
            "Days to retain ingested raw documents (PDF / DOCX / EPUB / TXT / Markdown) under "
            "the operator's ingestion-output directory.  Set to 0 to retain indefinitely. "
            "Closes ghost-features GH-023 (was nested as `ingestion.retention.raw_documents.ttl_days`; now top-level)."
        ),
    )
    enforce: Literal["log_only", "warn_on_excess", "block_on_excess"] = Field(
        default="log_only",
        description=(
            "Policy enforcement mode.  `log_only` records violations in the audit log without operator-visible output; "
            "`warn_on_excess` adds a structured warning to stderr; `block_on_excess` aborts the trainer pre-flight with "
            "EXIT_EVAL_FAILURE (3) so a regulated CI gate does not silently extend the retention horizon."
        ),
    )


# Module-level deduplication set for _warn_tier_disagreement.  Without this,
# ``merge_pipeline_stage_config`` re-instantiates ForgeConfig once per pipeline
# stage, re-runs _validate_consistency, and re-emits the warning N+1 times for
# an N-stage pipeline run (F-L-11).  The set stores frozenset tier-pair tuples;
# a process restart clears it (appropriate — cross-run deduplication would
# suppress legitimate new mismatches).
_tier_disagreement_warned: set[frozenset[str]] = set()


class ForgeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: ModelConfig = Field(description="Base-model + quantisation + backend block (required).")
    lora: LoraConfigModel = Field(description="PEFT / LoRA configuration block (required).")
    training: TrainingConfig = Field(
        description="Trainer hyperparameters + alignment-method parameters block (required)."
    )
    data: DataConfig = Field(description="Dataset + governance configuration block (required).")
    auth: Optional[AuthConfig] = Field(default=None, description="HuggingFace Hub authentication block (optional).")
    evaluation: Optional[EvaluationConfig] = Field(
        default=None,
        description="Post-training evaluation block (loss / benchmark / safety / judge / human-approval gate).",
    )
    webhook: Optional[WebhookConfig] = Field(
        default=None, description="Webhook notification block (Slack / Teams / Discord / custom)."
    )
    distributed: Optional[DistributedConfig] = Field(
        default=None, description="DeepSpeed / FSDP multi-GPU configuration block."
    )
    merge: Optional[MergeConfig] = Field(default=None, description="Post-training model-merging configuration block.")
    compliance: Optional[ComplianceMetadataConfig] = Field(
        default=None,
        description="Annex IV technical-documentation metadata block (provider name, system version, etc.).",
    )
    risk_assessment: Optional[RiskAssessmentConfig] = Field(
        default=None,
        description="EU AI Act Article 9 risk-management block (intended use, foreseeable misuse, risk tier).",
    )
    monitoring: Optional[MonitoringConfig] = Field(
        default=None, description="EU AI Act Article 12 + 17 post-market monitoring block."
    )
    synthetic: Optional[SyntheticConfig] = Field(
        default=None, description="Teacher→student synthetic-data generation block."
    )
    retention: Optional[RetentionConfig] = Field(
        default=None,
        description="Phase 21 / GDPR Article 5(1)(e) storage limitation + Article 17 erasure horizons block.",
    )
    pipeline: Optional["PipelineConfig"] = Field(
        default=None,
        description=(
            "Phase 14 — multi-stage training pipeline chain (e.g. SFT → DPO → "
            "GRPO).  When present, the orchestrator at "
            "``forgelm/cli/_pipeline.py`` runs each stage sequentially with "
            "section-wholesale inheritance from this root config.  When absent, "
            "the run path is byte-identical to v0.6.0."
        ),
    )

    def _warn_general_consistency(self) -> None:
        """Emit warnings for the broad cross-field config inconsistencies."""
        if self.evaluation and self.evaluation.auto_revert and self.training.merge_adapters:
            logger.warning(
                "auto_revert=True with merge_adapters=True: if evaluation fails, "
                "the merged full model will be deleted. Consider using adapter-only saves."
            )
        if self.model.backend == "unsloth" and self.model.trust_remote_code:
            logger.warning(
                "trust_remote_code=True with Unsloth backend: Unsloth internally calls "
                "HuggingFace Transformers which MAY still execute remote code. "
                "Verify the Unsloth version's behavior before production use."
            )
        if self.training.merge_adapters and self.training.trainer_type != "sft":
            logger.warning(
                "merge_adapters=True with trainer_type='%s' may produce unexpected results. "
                "Adapter merging is designed for SFT workflows.",
                self.training.trainer_type,
            )
        if self.lora.r > 64 and not getattr(self.lora, "use_rslora", False) and self.lora.method not in ("rslora",):
            logger.warning(
                "LoRA rank r=%d is high. Consider method='rslora' for training stability.",
                self.lora.r,
            )
        if (
            self.training.eval_steps
            and self.training.save_steps
            and self.training.eval_steps > self.training.save_steps
            and self.evaluation
            and getattr(self.evaluation, "auto_revert", False)
        ):
            logger.warning(
                "eval_steps (%d) > save_steps (%d): load_best_model_at_end may not work correctly. "
                "Set eval_steps <= save_steps.",
                self.training.eval_steps,
                self.training.save_steps,
            )

    def _risk_tiers(self) -> Tuple[Optional[str], Optional[str]]:
        """Return the (risk_assessment.risk_category, compliance.risk_classification) pair.

        Both sibling fields share the same RiskTier Literal but are
        independent — Pydantic does not enforce equality between them,
        so a hand-written YAML can reach an asymmetric state where the
        technical and compliance views disagree.  All strict-gate
        decisions OR across both fields (F-W3FU-S-01 / F-W3FU-01
        regression fix): if EITHER is in the strict tier, the gate
        fires; if EITHER is ``unacceptable``, the Article 5 banner
        fires.  ``_resolve_risk_label`` produces a *display* label for
        log messages; the gate boolean is computed independently to
        avoid the asymmetric-tier silent bypass.
        """
        ra = self.risk_assessment.risk_category if self.risk_assessment else None
        cm = self.compliance.risk_classification if self.compliance else None
        return ra, cm

    def _resolve_risk_label(self) -> Optional[str]:
        """Return the active risk label for log messages.

        Display-only — used to fill the ``%r`` slot in the auto_revert
        warning and the ``ConfigError`` raise.  See ``_risk_tiers`` /
        ``_is_strict_tier`` / ``_is_unacceptable`` for the actual gate
        logic, which OR's across both sibling fields rather than
        picking one.
        """
        ra, cm = self._risk_tiers()
        # When the two siblings disagree, prefer whichever side carries
        # a strict tier so the warning message names the strict label
        # the operator needs to address.
        if ra in _STRICT_RISK_TIERS:
            return ra
        if cm in _STRICT_RISK_TIERS:
            return cm
        return ra or cm

    def _is_strict_tier(self) -> bool:
        """True iff EITHER sibling field is in ``_STRICT_RISK_TIERS``.

        The OR-across-fields semantics matches the pre-Wave-3 behaviour
        and is required so that an asymmetric config (e.g.
        ``risk_assessment.risk_category="limited-risk"`` AND
        ``compliance.risk_classification="high-risk"``) cannot silently
        bypass the F-compliance-110 strict gate.
        """
        ra, cm = self._risk_tiers()
        return ra in _STRICT_RISK_TIERS or cm in _STRICT_RISK_TIERS

    def _is_unacceptable(self) -> bool:
        """True iff EITHER sibling field is ``"unacceptable"``.

        Article 5 prohibited-practice banner fires whenever either view
        marks the deployment unacceptable — disagreement between the
        technical and compliance views must NOT silence the notice.
        """
        ra, cm = self._risk_tiers()
        return ra == "unacceptable" or cm == "unacceptable"

    def _warn_unacceptable_practice(self) -> None:
        """Article 5 — prohibited-practices banner.

        Louder operator notice on top of the auto_revert nudge — the
        deployment itself is unlawful in the EU regardless of how well
        the safety gates are wired up.
        """
        logger.warning(
            "Risk classification 'unacceptable' corresponds to EU AI Act Article 5 prohibited "
            "practices. ForgeLM will not refuse the run, but deploying such a system inside the "
            "EU is unlawful — confirm operator intent before continuing."
        )

    def _enforce_safety_gate_for_strict_tier(self, label: Optional[str]) -> None:
        """Article 9 — risk management evidence requires safety eval enabled.

        Wave 3 / Faz 28 (F-compliance-110): a high-risk / unacceptable
        classification REQUIRES an enabled safety evaluation gate to
        back the EU AI Act Article 9 risk-management claim.  Earlier
        versions only emitted a warning, which let regulated runs
        ship Annex IV bundles whose risk-management section was not
        actually evidenced.  v0.5.5 escalates the warning to a hard
        ``ConfigError``: operators who genuinely want a sandboxed run
        without safety eval must lower the risk_classification (e.g.
        to ``limited-risk``) or enable ``evaluation.safety``.
        """
        safety = self.evaluation.safety if self.evaluation else None
        if not safety or not safety.enabled:
            raise ConfigError(
                f"Risk classification {label!r} requires evaluation.safety.enabled: true "
                "(EU AI Act Article 9 risk-management evidence cannot be derived "
                "from a disabled safety eval).  Either enable safety evaluation "
                "or lower the risk_classification to a non-strict tier."
            )
        if not safety.track_categories:
            logger.warning(
                "High-risk AI: harm category tracking (track_categories: true) is recommended "
                "for detailed EU AI Act compliance documentation."
            )

    def _warn_high_risk_compliance(self) -> None:
        """EU AI Act compliance recommendations for strict risk tiers.

        ``unacceptable`` (Article 5 prohibited practices) is treated at least
        as strictly as ``high-risk`` for ForgeLM's purposes — the gate exists
        to nudge the operator into running with auto-revert + safety eval,
        and ``unacceptable`` should never get *less* gating than ``high-risk``
        because the underlying use case is not allowed at all under the Act.

        Strict-tier detection ORs across both sibling fields
        (``risk_assessment.risk_category`` and
        ``compliance.risk_classification``) — see ``_is_strict_tier``
        for rationale.  The display label is whichever sibling carries
        a strict tier; this is NOT the gate decision (the gate is the
        OR), only the message text.
        """
        if not self._is_strict_tier():
            return
        label = self._resolve_risk_label()
        if not self.evaluation or not self.evaluation.auto_revert:
            logger.warning(
                "Risk classification %r requires evaluation.auto_revert: true "
                "for EU AI Act compliance. Safety gates should be enabled.",
                label,
            )
        if self._is_unacceptable():
            self._warn_unacceptable_practice()
        self._enforce_safety_gate_for_strict_tier(label)

    def _warn_tier_disagreement(self) -> None:
        """Warn when the two risk-tier siblings are explicitly set and disagree.

        ``risk_assessment.risk_category`` and
        ``compliance.risk_classification`` are independent fields that both
        default to ``minimal-risk`` on their sub-models.  The strict gate ORs
        across them (see ``_is_strict_tier``), so a disagreement never silently
        bypasses a safety gate — but compliance.py emits BOTH values into the
        governance / Annex IV bundle, so an explicit disagreement ships a
        technical-documentation set whose declared tiers contradict each other
        with no record that ForgeLM noticed.  Nudge the operator to reconcile
        before the bundle becomes regulatory evidence.  Checks each
        sub-model's ``model_fields_set`` (the keys default to the same value,
        so root-level presence is not enough to prove an explicit choice).

        Deduplication: ``merge_pipeline_stage_config`` re-instantiates
        ``ForgeConfig`` once per pipeline stage, which re-runs
        ``_validate_consistency`` and would re-emit this warning N+1 times for
        an N-stage pipeline.  The module-level ``_tier_disagreement_warned`` set
        suppresses repeat emissions within the same process (F-L-11).
        """
        if not (self.risk_assessment and self.compliance):
            return
        ra_set = "risk_category" in self.risk_assessment.model_fields_set
        cm_set = "risk_classification" in self.compliance.model_fields_set
        if not (ra_set and cm_set):
            return
        ra, cm = self._risk_tiers()
        if ra != cm:
            pair_key: frozenset[str] = frozenset({ra, cm})
            if pair_key in _tier_disagreement_warned:
                return
            _tier_disagreement_warned.add(pair_key)
            logger.warning(
                "Risk tiers disagree: risk_assessment.risk_category=%r vs "
                "compliance.risk_classification=%r. Both values are emitted into "
                "the EU AI Act governance / Annex IV bundle; reconcile them before "
                "the compliance documentation becomes regulatory evidence.",
                ra,
                cm,
            )

    def _validate_galore(self) -> None:
        if not self.training.galore_enabled:
            return
        if self.lora.r > 0:
            logger.info(
                "GaLore (gradient rank=%d) enabled alongside LoRA (adapter rank=%d). "
                "GaLore reduces gradient memory via low-rank projection; "
                "LoRA constrains trainable parameters. Both are active simultaneously.",
                self.training.galore_rank,
                self.lora.r,
            )
        if "layerwise" in self.training.galore_optim and self.distributed and self.distributed.strategy:
            raise ValueError(
                "GaLore layerwise optimizers do not support multi-GPU (DDP). "
                "Use a non-layerwise variant (e.g., galore_adamw) or disable distributed training."
            )

    def _validate_distributed(self) -> None:
        if not (self.distributed and self.distributed.strategy):
            return
        if self.model.backend == "unsloth":
            raise ValueError(
                "Unsloth backend does not support multi-GPU distributed training. "
                "Set backend: 'transformers' for DeepSpeed/FSDP."
            )
        if (
            self.distributed.strategy == "deepspeed"
            and self.distributed.deepspeed_config
            and "zero3" in str(self.distributed.deepspeed_config)
            and self.model.load_in_4bit
        ):
            logger.warning(
                "QLoRA (4-bit) with DeepSpeed ZeRO-3 has known compatibility issues. "
                "Consider using ZeRO-2 or disabling 4-bit quantization for stability."
            )

    @model_validator(mode="after")
    def _validate_consistency(self):
        self._warn_general_consistency()
        self._warn_tier_disagreement()
        self._warn_high_risk_compliance()
        # `trainer_type` validation now lives in TrainingConfig.trainer_type's
        # `Literal[...]` annotation — Pydantic raises ValidationError on
        # construction with the field name and the allowed values, so the
        # bespoke `_validate_trainer_type` runtime check became redundant.
        self._validate_galore()
        self._validate_distributed()
        self._reconcile_staging_ttl_days()
        return self

    def _reconcile_staging_ttl_days(self) -> None:
        """Phase 21 deprecation cadence:  reconcile the legacy
        ``evaluation.staging_ttl_days`` against the canonical
        ``retention.staging_ttl_days``.

        Per Phase 20 design §3.1 v2 (and gdpr-erasure-design L75-81):

        - When **only** ``evaluation.staging_ttl_days`` is set →
          alias-forward to ``retention.staging_ttl_days`` (creating
          ``retention`` block if missing) and emit a single
          ``DeprecationWarning`` naming the new field + the v0.8.0
          removal target.
        - When **only** ``retention.staging_ttl_days`` is set → no
          warning; canonical path.
        - When **both** are set with **identical** values → emit
          ``DeprecationWarning`` for the deprecated field; the canonical
          ``retention.staging_ttl_days`` value wins; operator's intent
          is unambiguous.
        - When **both** are set with **different** values → raise
          ``ConfigError`` at validation time naming both keys, both
          values, and instructing the operator to remove the deprecated
          entry.  Silent winner = wrong winner.

        Wave 2b Round-4 review F-W2B-02 fix: Pydantic v2 exposes
        ``model_fields_set`` exactly to distinguish "operator wrote
        the field in YAML" from "Pydantic filled the default".  We
        consult that set so an operator who follows the documented
        deprecation cadence (delete the deprecated key, add the
        canonical block) is not refused with ``ConfigError`` because
        the deprecated default-7 was re-filled.  The previous
        "value differs from default" heuristic mis-handled the
        explicit-default + canonical-different scenario.
        """
        # Bind the optional sub-models locally so the type narrowing is
        # visible to static analysers (SonarCloud S2259) and the field-
        # explicitness checks below cannot race against another mutator.
        evaluation = self.evaluation
        retention = self.retention

        legacy_was_explicitly_set = bool(evaluation is not None and "staging_ttl_days" in evaluation.model_fields_set)
        legacy = evaluation.staging_ttl_days if (legacy_was_explicitly_set and evaluation is not None) else None
        # Wave 2b Round-5 review F-W2B-RETENTION: applying the same
        # ``model_fields_set`` test to the canonical block.  An operator
        # who writes ``retention: {audit_log_retention_days: 1825}``
        # (no staging key) leaves ``staging_ttl_days`` at its default
        # of 7; treating that 7 as an explicit canonical value would
        # spuriously raise ``ConfigError`` when paired with
        # ``evaluation.staging_ttl_days: 14``.  We only treat
        # ``retention.staging_ttl_days`` as canonical when the operator
        # actually wrote it.
        canonical_was_explicitly_set = bool(retention is not None and "staging_ttl_days" in retention.model_fields_set)
        canonical = retention.staging_ttl_days if (canonical_was_explicitly_set and retention is not None) else None

        # Both unset → nothing to do.
        if legacy is None and canonical is None:
            return
        # Only canonical set (or operator deleted the deprecated key) →
        # canonical path; no warning.
        if legacy is None and canonical is not None:
            return
        # Only legacy set explicitly → alias-forward.
        if legacy is not None and canonical is None:
            self._apply_legacy_alias_forward(legacy, retention)
            return
        # Both set.  Compare.
        if legacy == canonical:
            self._emit_legacy_match_warning()
            return
        # Both set with different values → refuse.
        raise ConfigError(
            "Conflicting staging_ttl_days values: "
            f"`evaluation.staging_ttl_days={legacy}` (deprecated, forwards to "
            f"`retention.staging_ttl_days`) vs `retention.staging_ttl_days={canonical}` "
            "(canonical).  Remove the deprecated entry; the canonical block wins.  "
            "(Tracking issue: removal scheduled for v0.8.0 per "
            "docs/standards/release.md#deprecation-cadence.)"
        )

    def _apply_legacy_alias_forward(self, legacy: int, retention: Optional["RetentionConfig"]) -> None:
        """Mirror ``evaluation.staging_ttl_days`` onto ``retention.staging_ttl_days``.

        ``model_copy(update=...)`` preserves any other ``retention.*`` keys
        the operator already wrote (e.g. ``audit_log_retention_days: 1825``
        paired with ``evaluation.staging_ttl_days: 14``).  The previous
        ``RetentionConfig(staging_ttl_days=legacy)`` constructor call would
        have silently discarded those.

        ``stacklevel=5`` is tuned so the DeprecationWarning surfaces at the
        operator's ``ForgeConfig(...)`` call site rather than inside the
        Pydantic ``@model_validator`` machinery (caller →
        ``_reconcile_staging_ttl_days`` → here).
        """
        if retention is not None:
            self.retention = retention.model_copy(update={"staging_ttl_days": legacy})
        else:
            self.retention = RetentionConfig(staging_ttl_days=legacy)
        # Pair the DeprecationWarning with a logger.warning: CPython's default
        # filters suppress DeprecationWarning emitted outside __main__, so the
        # warnings.warn call alone never reaches a CLI operator (F-P1-FAB-17).
        # The logger line mirrors the lora.use_dora / sample_packing idiom and
        # surfaces on the CLI path; the warnings.warn keeps `-W error` / CI
        # deprecation sweeps working for library consumers.
        message = (
            "`evaluation.staging_ttl_days` is deprecated and forwards to "
            "`retention.staging_ttl_days`. "
            "Move the value under the new top-level `retention:` block; the "
            "deprecated field is removed in v0.8.0."
        )
        logger.warning(message)
        warnings.warn(message, DeprecationWarning, stacklevel=5)

    def _emit_legacy_match_warning(self) -> None:
        """Warn when both fields are set to identical values; canonical wins.

        ``stacklevel=5`` matches :meth:`_apply_legacy_alias_forward` so both
        deprecation paths attribute the warning to the same operator
        call frame.
        """
        # See `_apply_legacy_alias_forward`: pair the logger.warning so the
        # deprecation reaches CLI operators, not just `-W error` consumers.
        message = (
            "`evaluation.staging_ttl_days` is deprecated; the value matches "
            "`retention.staging_ttl_days` so the canonical block wins.  Remove "
            "`evaluation.staging_ttl_days` from your YAML — the deprecated field "
            "is removed in v0.8.0."
        )
        logger.warning(message)
        warnings.warn(message, DeprecationWarning, stacklevel=5)


class ConfigError(Exception):
    """Raised when configuration validation fails."""

    pass


# ---------------------------------------------------------------------------
# Phase 14 — Multi-Stage Training Pipeline Chains
# ---------------------------------------------------------------------------
#
# A ``pipeline`` block at the root level chains 2+ training stages (e.g.
# SFT → DPO → GRPO) into one config-driven run.  See
# ``docs/roadmap/phase-14-pipeline-chains.md`` for the full design spec
# (inheritance matrix, CLI semantics, audit-log events, Annex IV manifest).
#
# Layering rule: the pipeline orchestrator (``forgelm/cli/_pipeline.py``)
# produces one flat :class:`ForgeConfig` per stage via
# :func:`merge_pipeline_stage_config`, then hands that to a fresh
# :class:`ForgeTrainer`.  ``forgelm/trainer.py`` is **not** aware of the
# pipeline layer — single-stage runs remain byte-identical to v0.6.0.


_STAGE_NAME_PATTERN = r"^[a-z0-9_]{1,32}$"
"""Regex enforcing audit-safe pipeline stage names.

The name is used in CLI flags (``--stage <name>``, ``--resume-from <name>``),
audit-log fields, state-file keys, and webhook payloads — pinning it to a
narrow lowercase / digit / underscore alphabet removes every escaping
concern downstream.
"""


class PipelineStage(BaseModel):
    """A single stage in a multi-stage training pipeline.

    Section-wholesale override semantics — if a block is present in the
    stage YAML it **fully replaces** the root config's block for that
    section; if the block is omitted, the stage inherits the root.  No
    field-level deep-merge: "if you want to inherit, omit the block; if
    you want to override, supply the full block."

    Auto-chaining: when the stage does not supply its own ``model:`` block
    the orchestrator sets ``model.name_or_path`` to the *previous*
    stage's ``training.output_dir/final_model`` path.  Stage 0 still reads
    the root's ``model.name_or_path``.  An explicit per-stage ``model:``
    block (with its required ``name_or_path``) disables auto-chaining for
    that stage (operator escape hatch).

    The pipeline-level concerns (``distributed``, ``webhook``,
    ``compliance``, ``risk_assessment``, ``monitoring``, ``retention``,
    ``synthetic``, ``merge``, ``auth``) live at the root only and have no
    per-stage override field — ``extra="forbid"`` causes Pydantic to
    reject any attempt to declare them inside a stage, with the
    section-name preserved in the error message.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        ...,
        pattern=_STAGE_NAME_PATTERN,
        description=(
            "Stage identifier.  Must match ``[a-z0-9_]{1,32}`` so it can serve as "
            "an identifier in CLI flags, audit-log fields, and state-file keys "
            "without escaping.  Must be unique within the pipeline."
        ),
    )

    # Section-wholesale override slots.  ``None`` (the default) means
    # "inherit this section from the root config"; a populated value
    # replaces the root section in full.
    model: Optional[ModelConfig] = Field(
        default=None,
        description=(
            "Per-stage model block override.  When None, the stage inherits the "
            "root ``model`` block AND auto-chains ``model.name_or_path`` to the "
            "previous stage's output path."
        ),
    )
    lora: Optional[LoraConfigModel] = Field(
        default=None,
        description="Per-stage LoRA / PEFT block override.  None → inherit from root.",
    )
    training: Optional[TrainingConfig] = Field(
        default=None,
        description=(
            "Per-stage training block override.  None → inherit from root.  "
            "When present, ``trainer_type`` is required (Pydantic's existing "
            "``TrainingConfig`` validation enforces this)."
        ),
    )
    data: Optional[DataConfig] = Field(
        default=None,
        description=(
            "Per-stage data block override.  None → inherit from root.  Pipelines "
            "rarely reuse the same dataset across stages; supplying an explicit "
            "per-stage data block is the common case."
        ),
    )
    evaluation: Optional[EvaluationConfig] = Field(
        default=None,
        description=(
            "Per-stage evaluation block override.  None → inherit from root.  "
            "Per-stage gates (loss thresholds, auto_revert, safety, judge, human-"
            "approval) live here; each stage may independently configure its gate."
        ),
    )

    @model_validator(mode="after")
    def _training_block_must_set_trainer_type_explicitly(self) -> "PipelineStage":
        """When a stage supplies its own ``training:`` block, the YAML
        MUST explicitly set ``trainer_type`` (each stage states its
        alignment paradigm in the YAML — and therefore in the pipeline
        manifest — for audit clarity).  Without this guard,
        ``TrainingConfig.trainer_type`` would silently default to
        ``"sft"`` and a DPO stage's manifest could carry
        ``trainer_type: sft`` if the operator forgot the field.
        """
        if self.training is None:
            return self
        if "trainer_type" not in self.training.model_fields_set:
            raise ValueError(
                f"Pipeline stage {self.name!r}: when a 'training:' block is "
                f"supplied per stage, 'trainer_type' must be set explicitly "
                f"(each stage states its alignment paradigm for audit clarity)."
            )
        return self


class PipelineConfig(BaseModel):
    """Top-level pipeline block.

    Carries the ordered list of stages (≥ 1) that the orchestrator
    executes sequentially.  The orchestrator is responsible for chaining
    each stage's input model to the previous stage's output and emitting
    the ``pipeline.*`` audit events documented in the Phase 14 spec.
    """

    model_config = ConfigDict(extra="forbid")

    output_dir: str = Field(
        default="./pipeline_run",
        description=(
            "Pipeline-level output directory.  Hosts the pipeline-level audit "
            "log, the pipeline state file (``pipeline_state.json``), and the "
            "pipeline manifest (``compliance/pipeline_manifest.json``).  "
            "Distinct from each stage's ``training.output_dir`` — those keep "
            "the per-stage checkpoints + per-stage training manifest."
        ),
    )

    stages: List[PipelineStage] = Field(
        ...,
        min_length=1,
        description=(
            "Ordered list of training stages.  Must contain at least one stage; "
            "operators who want a single-stage run should omit the ``pipeline:`` "
            "block entirely rather than declaring a 1-element pipeline."
        ),
    )

    @field_validator("stages")
    @classmethod
    def _unique_stage_names(cls, v: List[PipelineStage]) -> List[PipelineStage]:
        """Reject duplicate stage names early.

        Duplicate names would make ``--stage <name>`` and
        ``--resume-from <name>`` ambiguous, and would corrupt the pipeline
        state file (which keys per-stage status on the name).
        """
        names = [s.name for s in v]
        seen = set()
        duplicates: List[str] = []
        for n in names:
            if n in seen and n not in duplicates:
                duplicates.append(n)
            seen.add(n)
        if duplicates:
            raise ValueError(
                "Duplicate pipeline stage name(s): "
                + ", ".join(repr(d) for d in duplicates)
                + ".  Stage names must be unique within a pipeline."
            )
        return v


def merge_pipeline_stage_config(
    root_cfg: "ForgeConfig",
    stage: PipelineStage,
    *,
    prev_output_model: Optional[str] = None,
    input_model_override: Optional[str] = None,
) -> "ForgeConfig":
    """Materialise a flat :class:`ForgeConfig` for a single pipeline stage.

    Implements the section-wholesale inheritance rule documented in
    ``docs/roadmap/phase-14-pipeline-chains.md`` Task 2:

    - For each of ``model`` / ``lora`` / ``training`` / ``data`` /
      ``evaluation``: if the stage sets the block, the stage's block
      replaces the root's; otherwise the root's block is preserved.
    - The pipeline-level sections (``distributed``, ``webhook``,
      ``compliance``, ``risk_assessment``, ``monitoring``, ``retention``,
      ``synthetic``, ``merge``, ``auth``) are kept from the root unchanged
      — they cannot be overridden per stage.

    Auto-chain resolution (in priority order):

    1. ``input_model_override`` (CLI ``--input-model`` flag) — operator
       escape hatch.  Set ``model.name_or_path`` to this value, regardless
       of whether the stage declared its own ``model:`` block.  The
       caller is responsible for logging the override.
    2. The stage declared its own ``model:`` block — keep the stage's
       ``name_or_path``.  No auto-chain.
    3. ``prev_output_model`` is not None — auto-chain.  Set
       ``model.name_or_path`` to this value (typically
       ``<prev_stage.output_dir>/final_model``).
    4. Stage 0 with no override — keep the root's ``model.name_or_path``.

    The returned :class:`ForgeConfig` carries no ``pipeline`` block (it
    is stripped during the merge) so a downstream :class:`ForgeTrainer`
    sees an ordinary single-stage config and behaves byte-identically to
    a v0.6.0 single-stage run.
    """
    # ``exclude_unset=True`` so only keys the operator actually wrote
    # round-trip — re-validation re-fills defaults identically.  Without it,
    # ``model_dump`` materialises *unset* defaults (e.g. an ``evaluation``
    # block dumps ``staging_ttl_days=7``); on re-validation every dumped key
    # counts as ``model_fields_set``, so a root with a canonical
    # ``retention.staging_ttl_days != 7`` plus any ``evaluation`` block would
    # falsely raise ``ConfigError`` ("conflicting staging_ttl_days") for a
    # field the operator never wrote (F-P1-FAB-03).  ``exclude_none=True``
    # additionally drops inherited ``Optional[T] = None`` fields so the
    # per-stage manifest is not inflated with no-op None values.
    base = root_cfg.model_dump(exclude_none=True, exclude_unset=True)
    base.pop("pipeline", None)

    # Stage overrides use the same ``exclude_unset=True`` rationale as the root
    # dump above: a stage ``evaluation`` block that omits ``staging_ttl_days``
    # must not materialise the default ``7`` and falsely conflict with a
    # canonical ``retention.staging_ttl_days`` on re-validation (F-P1-FAB-03).
    if stage.model is not None:
        base["model"] = stage.model.model_dump(exclude_none=True, exclude_unset=True)
    if stage.lora is not None:
        base["lora"] = stage.lora.model_dump(exclude_none=True, exclude_unset=True)
    if stage.training is not None:
        base["training"] = stage.training.model_dump(exclude_none=True, exclude_unset=True)
    if stage.data is not None:
        base["data"] = stage.data.model_dump(exclude_none=True, exclude_unset=True)
    if stage.evaluation is not None:
        base["evaluation"] = stage.evaluation.model_dump(exclude_none=True, exclude_unset=True)

    # Auto-chain resolution.  See docstring above for priority order.
    if input_model_override is not None:
        base["model"]["name_or_path"] = input_model_override
    elif stage.model is None and prev_output_model is not None:
        # Stage inherited the model block AND a previous output exists —
        # the canonical auto-chain case.
        base["model"]["name_or_path"] = prev_output_model
    # else: keep whatever is already in ``base["model"]["name_or_path"]``
    # (either the stage's explicit value or the root's).

    return ForgeConfig(**base)


# Resolve the ``pipeline: Optional["PipelineConfig"]`` forward reference in
# :class:`ForgeConfig` now that :class:`PipelineConfig` is in scope.
# Without this, the first ``ForgeConfig(**yaml_data)`` instantiation that
# carries a populated ``pipeline`` block fails at validation time with a
# ``PydanticUndefinedAnnotation`` because the forward reference was never
# late-bound.
ForgeConfig.model_rebuild()


def load_config(config_path: str) -> ForgeConfig:
    """Loads and validates a YAML configuration file."""
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        try:
            yaml_data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ConfigError(f"Invalid YAML syntax in {config_path}: {e}") from e

    if not isinstance(yaml_data, dict):
        raise ConfigError(f"Configuration file must contain a YAML mapping, got {type(yaml_data).__name__}")

    try:
        config = ForgeConfig(**yaml_data)
    except ValidationError as e:
        # Pydantic's ValidationError lists field path + violation per error;
        # preserve the structured detail by passing str(e) — the previous
        # bare-Exception catch lost line/column info from custom validators.
        raise ConfigError(f"Configuration validation failed:\n{e}") from e
    except (TypeError, ValueError) as e:
        # Defensive: a custom @model_validator can raise plain ValueError
        # / TypeError outside Pydantic's wrapper. Same message shape.
        raise ConfigError(f"Configuration validation failed: {e}") from e

    return config
