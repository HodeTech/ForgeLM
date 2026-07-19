import logging
import re
from typing import Any, Optional, Tuple

# NOTE: Heavy ML imports (torch, transformers AutoModelForCausalLM/AutoTokenizer/
# BitsAndBytesConfig, peft helpers) are deferred to function bodies so
# `import forgelm.model` is cheap. Eagerly importing torch/peft here costs
# ~3-5s of CLI startup per invocation (peft pulls transformers which pulls
# torch). See closure-plan F-performance-101.

logger = logging.getLogger("forgelm.model")

# MoE expert-count attribute names across architectures: Mixtral / Phi-MoE
# expose ``num_local_experts``; Qwen2/Qwen3-MoE expose ``num_experts``;
# DeepSeek-V3 exposes ``n_routed_experts``. Gating only on ``num_local_experts``
# silently no-ops the entire moe block for Qwen3/DeepSeek (F-P3-FABLE-07).
_MOE_EXPERT_COUNT_ATTRS = ("num_local_experts", "num_experts", "n_routed_experts")


def _resolve_expert_count(model_config: Any) -> Optional[int]:
    """Return the MoE expert count from whichever attribute the architecture
    exposes, or ``None`` when the model is not a recognised MoE checkpoint."""
    for attr in _MOE_EXPERT_COUNT_ATTRS:
        val = getattr(model_config, attr, None)
        if isinstance(val, int) and val > 0:
            return val
    return None


def _resolve_peft_flags(config: Any) -> "Tuple[bool, bool, str]":
    """Derive ``(use_dora, use_rslora, method)`` from the canonical ``lora.method``
    plus the deprecated boolean shortcuts.

    Single source of truth shared by the transformers and unsloth backends so
    identical YAML trains the same algorithm regardless of ``model.backend`` —
    previously the unsloth path read only the boolean shortcuts and silently
    downgraded ``method: dora``/``rslora`` to plain LoRA (F-P3-FABLE-09).
    """
    peft_method = getattr(config.lora, "method", "lora")
    use_dora = config.lora.use_dora or peft_method == "dora"
    use_rslora = getattr(config.lora, "use_rslora", False) or peft_method == "rslora"
    return use_dora, use_rslora, peft_method


def _resolve_bnb_compute_dtype(dtype_str: str):
    import torch

    if not dtype_str or dtype_str == "auto":
        return torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    normalized = str(dtype_str).lower()
    if normalized in ("bf16", "bfloat16"):
        return torch.bfloat16
    if normalized in ("fp16", "float16"):
        return torch.float16
    if normalized in ("fp32", "float32"):
        return torch.float32
    raise ValueError(f"Unsupported bnb_4bit_compute_dtype: {dtype_str!r}")


def _load_unsloth(config: Any) -> Tuple[Any, Any]:
    """Load the model + tokenizer + LoRA via the Unsloth backend."""
    try:
        from unsloth import FastLanguageModel
    except ImportError as e:
        raise ImportError(
            "unsloth backend requires the 'unsloth' extra. Install with: pip install 'forgelm[unsloth]'"
        ) from e

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=config.model.name_or_path,
        max_seq_length=config.model.max_length,
        dtype=None,  # Auto detection
        load_in_4bit=config.model.load_in_4bit,
        # Forward the resolved flag so `model.trust_remote_code: true` is honoured
        # on the unsloth path too; the sibling inference._load_unsloth does the
        # same. Omitting it silently loaded with Unsloth's default (False),
        # failing custom-architecture repos that the operator explicitly trusted.
        trust_remote_code=getattr(config.model, "trust_remote_code", False),
    )

    use_dora, use_rslora, peft_method = _resolve_peft_flags(config)
    if peft_method == "pissa":
        # Unsloth's get_peft_model has no init_lora_weights="pissa" path; dropping
        # it silently would train plain LoRA under a config that asked for PiSSA.
        from .config import ConfigError

        raise ConfigError(
            "lora.method='pissa' is not supported by the unsloth backend. Use "
            "model.backend='transformers' for PiSSA, or choose another lora.method."
        )
    logger.info(
        "Setting up Unsloth LoRA configuration (method=%s, DoRA=%s, rsLoRA=%s)...",
        peft_method,
        use_dora,
        use_rslora,
    )
    model = FastLanguageModel.get_peft_model(
        model,
        r=config.lora.r,
        target_modules=config.lora.target_modules,
        lora_alpha=config.lora.alpha,
        lora_dropout=config.lora.dropout,
        bias=config.lora.bias,
        use_gradient_checkpointing="unsloth",
        use_rslora=use_rslora,
        use_dora=use_dora,
    )
    return model, tokenizer


def _load_tokenizer(config: Any, trust_remote_code: bool) -> Any:
    """Load AutoTokenizer (or AutoProcessor for VLMs) and ensure pad_token is set."""
    mm_cfg = getattr(config.model, "multimodal", None)
    if mm_cfg and mm_cfg.enabled:
        logger.info("Multimodal VLM mode enabled — loading with AutoProcessor.")
        from transformers import AutoProcessor

        tokenizer = AutoProcessor.from_pretrained(config.model.name_or_path, trust_remote_code=trust_remote_code)
    else:
        from transformers import AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(config.model.name_or_path, trust_remote_code=trust_remote_code)

    # AutoProcessor (VLM) wraps the text tokenizer under ``.tokenizer`` and does
    # not itself expose pad_token/pad_token_id; operate on the inner tokenizer so
    # the multimodal path doesn't AttributeError (F-P3-FABLE-11).
    pad_target = getattr(tokenizer, "tokenizer", tokenizer)
    if hasattr(pad_target, "pad_token") and pad_target.pad_token is None:
        logger.info("Tokenizer has no pad_token, using eos_token as pad_token.")
        pad_target.pad_token = pad_target.eos_token
    return tokenizer


def _device_map_for(config: Any, is_distributed: bool):
    """Pick a from_pretrained device_map suited to the current environment."""
    import torch

    if is_distributed:
        logger.info("Distributed training detected — skipping device_map.")
        return None
    if config.model.load_in_4bit:
        # 4-bit quantization needs device_map for layer placement
        return "auto"
    if torch.cuda.is_available():
        # Single GPU: place entire model on GPU without device_map="auto"
        # (which can split across CPU/GPU and break gradients)
        return {"": 0}
    return None  # CPU-only


def _build_model_kwargs(config: Any, trust_remote_code: bool) -> dict:
    """Assemble from_pretrained kwargs (device_map, BnB, RoPE, sliding window)."""
    import torch

    dist_cfg = getattr(config, "distributed", None)
    is_distributed = bool(dist_cfg and dist_cfg.strategy)

    kwargs: dict = {"trust_remote_code": trust_remote_code}
    device_map = _device_map_for(config, is_distributed)
    if device_map is not None:
        kwargs["device_map"] = device_map

    if torch.cuda.is_available() and config.model.load_in_4bit:
        from transformers import BitsAndBytesConfig

        logger.info("Using 4-bit QLoRA quantization...")
        compute_dtype = _resolve_bnb_compute_dtype(config.model.bnb_4bit_compute_dtype)
        kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_use_double_quant=config.model.bnb_4bit_use_double_quant,
            bnb_4bit_quant_type=config.model.bnb_4bit_quant_type,
            bnb_4bit_compute_dtype=compute_dtype,
        )
    elif config.model.load_in_4bit:
        # CUDA unavailable: bitsandbytes 4-bit needs a CUDA device, so the model
        # loads in full precision. Make the silent skip loud — otherwise the run
        # is full-precision while compliance artefacts (which read
        # config.load_in_4bit) still claim "4-bit NF4" (F-P3-FABLE-10).
        logger.warning(
            "model.load_in_4bit=true but CUDA is unavailable — loading in full "
            "precision (bitsandbytes 4-bit requires a CUDA device). This run is NOT "
            "quantized; compliance artefacts and the model card that read "
            "load_in_4bit will overstate the quantization."
        )

    rope_scaling = config.training.rope_scaling
    if rope_scaling:
        logger.info("RoPE scaling enabled: %s", rope_scaling)
        kwargs["rope_scaling"] = rope_scaling

    sliding_window = config.training.sliding_window_attention
    if sliding_window:
        logger.info("Sliding window attention override: %d tokens", sliding_window)
        kwargs["sliding_window"] = sliding_window

    return kwargs


def _detect_moe_experts(model: Any, config: Any) -> Optional[int]:
    """Resolve the MoE expert count (pre-PEFT), or None for a non-MoE model.

    Detection only — the actual expert selection + quantization run AFTER
    ``get_peft_model`` (see :func:`_apply_moe_post_peft`), because PEFT freezes
    every base parameter during adapter injection: freezing base expert weights
    beforehand is a no-op (F-P3-FABLE-02) and the quantization sweep finds
    nothing frozen yet (F-P3-FABLE-08).
    """
    moe_cfg = getattr(config.model, "moe", None)
    if not moe_cfg:
        return None
    num_experts = _resolve_expert_count(model.config)
    if num_experts is None:
        logger.warning(
            "model.moe is configured but the loaded model exposes no recognised MoE "
            "expert-count attribute (%s) — the moe block (quantize_experts / "
            "experts_to_train) is ignored. Is this actually a MoE checkpoint?",
            ", ".join(_MOE_EXPERT_COUNT_ATTRS),
        )
        return None
    logger.info("MoE model detected: %d experts", num_experts)
    return num_experts


def _apply_moe_post_peft(model: Any, config: Any, num_experts: int) -> None:
    """Apply MoE expert selection + optional quantization AFTER ``get_peft_model``.

    Order matters: PEFT has already frozen every base parameter and injected the
    trainable LoRA adapters, so here we (1) constrain the trainable set that
    actually matters — the adapters of unselected experts — and (2) recast the
    now-frozen base expert weights for VRAM savings.
    """
    moe_cfg = config.model.moe
    if moe_cfg.experts_to_train != "all":
        _freeze_unselected_experts(model, moe_cfg.experts_to_train, num_experts)
    if moe_cfg.quantize_experts:
        _apply_moe_expert_quantization(model)


def _build_lora_config(config: Any) -> "LoraConfig":  # noqa: F821 — peft import is lazy
    """Resolve PEFT method (lora / dora / rslora / pissa) and build LoraConfig."""
    from peft import LoraConfig

    use_dora, use_rslora, peft_method = _resolve_peft_flags(config)

    logger.info(
        "Setting up PEFT configuration (method=%s, DoRA=%s, rsLoRA=%s)...",
        peft_method,
        use_dora,
        use_rslora,
    )

    lora_kwargs = {
        "r": config.lora.r,
        "lora_alpha": config.lora.alpha,
        "lora_dropout": config.lora.dropout,
        "bias": config.lora.bias,
        "task_type": config.lora.task_type,
        "target_modules": config.lora.target_modules,
        "use_dora": use_dora,
        "use_rslora": use_rslora,
    }
    if peft_method == "pissa":
        lora_kwargs["init_lora_weights"] = "pissa"
        logger.info("Using PiSSA initialization (principal component adapter init).")
    return LoraConfig(**lora_kwargs)


def get_model_and_tokenizer(config: Any) -> Tuple[Any, Any]:
    """Loads the base model, tokenizer, and configures LoRA."""
    import torch
    from peft import get_peft_model, prepare_model_for_kbit_training
    from transformers import AutoModelForCausalLM

    logger.info("Loading Base Model: %s with backend: %s", config.model.name_or_path, config.model.backend)

    trust_remote_code = getattr(config.model, "trust_remote_code", False)
    if trust_remote_code:
        logger.warning(
            "trust_remote_code is ENABLED. This allows execution of arbitrary code "
            "from the model repository. Only use this with models you trust."
        )

    if config.model.backend.lower() == "unsloth":
        return _load_unsloth(config)

    tokenizer = _load_tokenizer(config, trust_remote_code)
    model_kwargs = _build_model_kwargs(config, trust_remote_code)

    # Most VLM architectures are registered under AutoModelForImageTextToText,
    # not AutoModelForCausalLM — loading them via the causal-LM class fails with
    # "Unrecognized configuration class" (F-P3-FABLE-11).
    mm_cfg = getattr(config.model, "multimodal", None)
    if mm_cfg and mm_cfg.enabled:
        from transformers import AutoModelForImageTextToText

        model = AutoModelForImageTextToText.from_pretrained(config.model.name_or_path, **model_kwargs)
    else:
        model = AutoModelForCausalLM.from_pretrained(config.model.name_or_path, **model_kwargs)

    # Sync pad_token_id to model config to suppress generation warnings. A VLM
    # AutoProcessor exposes pad_token_id only on its inner ``.tokenizer``.
    pad_source = getattr(tokenizer, "tokenizer", tokenizer)
    pad_token_id = getattr(pad_source, "pad_token_id", None)
    if pad_token_id is not None and model.config.pad_token_id is None:
        model.config.pad_token_id = pad_token_id

    # enable_input_require_grads is needed for gradient checkpointing with LoRA
    if hasattr(model, "enable_input_require_grads"):
        model.enable_input_require_grads()

    if torch.cuda.is_available() and config.model.load_in_4bit:
        model = prepare_model_for_kbit_training(model)

    # MoE detection reads model.config (pre-PEFT); the actual expert selection +
    # quantization must run AFTER get_peft_model so they constrain the injected
    # adapters / operate on the now-frozen base (F-P3-FABLE-02/08).
    moe_num_experts = _detect_moe_experts(model, config)
    model = get_peft_model(model, _build_lora_config(config))
    if moe_num_experts is not None:
        _apply_moe_post_peft(model, config, moe_num_experts)
    model.print_trainable_parameters()

    return model, tokenizer


def _is_frozen_expert_weight(name: str, module) -> bool:
    """True if *module* is a frozen expert weight tensor that's safe to recast."""
    if "expert" not in name.lower() or not hasattr(module, "weight"):
        return False
    return not module.weight.requires_grad


def _recast_expert_weight(name: str, module, target_dtype) -> bool:
    """Recast a single expert weight in-place; return True if a change was made."""
    import torch

    if module.weight.dtype == target_dtype:
        return False
    # bitsandbytes Linear4bit stores packed quantised weights as uint8 (and
    # 8-bit as int8). Casting that packed storage to a float dtype destroys the
    # data↔quant_state invariant — the forward dequantise then reads garbage or
    # crashes. Never recast packed quant storage (F-P3-FABLE-08).
    if module.weight.dtype in (torch.uint8, torch.int8):
        return False
    try:
        module.weight.data = module.weight.data.to(target_dtype)
    except Exception as e:  # noqa: BLE001 — best-effort: per-expert weight recast runs across hundreds of MoE expert tensors; surface includes RuntimeError (dtype unsupported on device), AttributeError (frozen / shared parameter), and torch internal errors on edge architectures.  Returning False keeps the per-expert loop running so a single recast failure cannot abort the whole sweep.  # NOSONAR
        logger.debug("Could not optimize %s: %s", name, e)
        return False
    return True


def _apply_moe_expert_quantization(model) -> None:
    """Reduce MoE expert memory by freezing and converting to half precision.

    Converts frozen expert weights to float16/bfloat16 for VRAM savings.
    Note: True int8 quantization requires bitsandbytes Linear8bitLt —
    raw dtype casting to int8 destroys weight values and is NOT used here.
    """
    import torch

    # A 4-bit-loaded model's expert weights are already bitsandbytes Params4bit
    # (packed uint8); recasting them to a float dtype corrupts the quant state.
    # The model is already quantised — there is nothing for this half-precision
    # pass to save (F-P3-FABLE-08).
    is_4bit = getattr(model, "is_loaded_in_4bit", False) or getattr(
        getattr(model, "base_model", None), "is_loaded_in_4bit", False
    )
    if is_4bit:
        logger.info(
            "MoE expert quantization skipped: model is already loaded in 4-bit "
            "(recasting packed Params4bit storage would corrupt the weights)."
        )
        return

    target_dtype = torch.float16
    if torch.cuda.is_available() and torch.cuda.is_bf16_supported():
        target_dtype = torch.bfloat16

    optimized_count = 0
    failed_count = 0
    for name, module in model.named_modules():
        if not _is_frozen_expert_weight(name, module):
            continue
        if module.weight.dtype == target_dtype or module.weight.dtype in (torch.uint8, torch.int8):
            continue
        if _recast_expert_weight(name, module, target_dtype):
            optimized_count += 1
        else:
            failed_count += 1

    if failed_count:
        logger.warning(
            "MoE expert optimization: %d expert weight tensor(s) failed to recast to %s "
            "(see DEBUG logs); they remain at their original dtype.",
            failed_count,
            target_dtype,
        )
    if optimized_count > 0:
        logger.info(
            "MoE expert optimization: %d expert weight tensors converted to %s for VRAM savings.",
            optimized_count,
            target_dtype,
        )
    else:
        logger.info("MoE expert optimization: no eligible frozen expert weights found.")


def _parse_selected_experts(experts_to_train: str, num_experts: int) -> Optional[set]:
    """Parse the comma-separated index list and drop out-of-range indices.

    Returns ``None`` when the input is malformed (caller should bail early).
    """
    try:
        selected = {int(idx.strip()) for idx in experts_to_train.split(",")}
    except ValueError:
        logger.warning(
            "Invalid experts_to_train value: '%s'. Expected comma-separated integers. Training all experts.",
            experts_to_train,
        )
        return None
    invalid = selected - set(range(num_experts))
    if invalid:
        logger.warning("Expert indices %s exceed num_experts=%d. Ignoring invalid indices.", invalid, num_experts)
        selected -= invalid
    return selected


# Tracks parameter names already logged as unrecognized so repeated calls on
# the same checkpoint don't produce a log line per-parameter.
# Per-process state; deduplication scope is the running process only — under
# DDP / DeepSpeed each rank holds its own copy and may emit the warning once.
_LOGGED_UNKNOWN_EXPERT_NAMES: set = set()

# Per-architecture regex registry for resolving the expert index inside an
# MoE state-dict parameter name. ASCII-bound \d so we can't match exotic
# Unicode digits, anchored on a literal trailing dot/underscore so we don't
# accidentally match neighbouring fields like ``experts.norm``. Patterns
# verified against the published state-dict listings of:
#   * Mixtral 8x7B / 8x22B  -> ``model.layers.{L}.block_sparse_moe.experts.{E}.w1.weight``
#   * Qwen 3 MoE            -> ``model.layers.{L}.mlp.experts.{E}.up_proj.weight``
#   * DeepSeek-V3           -> ``model.layers.{L}.mlp.experts.{E}.gate_proj.weight``
#   * Phi-MoE / GShard      -> ``model.layers.{L}.mlp.expert_{E}.gate_proj.weight``
#   * Nested / experimental -> ``...experts.expert_{E}.weight``
# Add new architectures by appending one regex; the resolver below short-
# circuits on the first match and returns the captured index.
_EXPERT_NAME_PATTERNS: Tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:^|\.)experts\.(\d+)\.", re.ASCII),  # Mixtral / Qwen 3 / DeepSeek-V3
    re.compile(r"(?:^|\.)experts\.expert_(\d+)\.", re.ASCII),  # nested expert_{i} under experts/
    re.compile(r"(?:^|\.)expert_(\d+)\.", re.ASCII),  # Phi-MoE / GShard-style flat
)


def _expert_index_in_name(name: str, num_experts: int) -> Optional[int]:
    """Return the expert index appearing in *name*, or None if not an expert param.

    Resolves the index via :data:`_EXPERT_NAME_PATTERNS` so adding support
    for a new MoE architecture is a one-line registry change rather than a
    behaviour edit on the freezing logic. If the name looks like an expert
    param (``"expert"`` substring) but doesn't match any registered
    pattern, log a single INFO line so an unfamiliar checkpoint surfaces
    in operator logs instead of silently making every expert trainable.
    """
    for pattern in _EXPERT_NAME_PATTERNS:
        match = pattern.search(name)
        if match:
            idx = int(match.group(1))
            if 0 <= idx < num_experts:
                return idx
            # Index outside the configured expert range — caller's
            # num_experts is wrong, or the regex caught a non-expert
            # field whose number happens to exceed the count.  Surface a
            # single warning per (num_experts) so an operator with a
            # mis-configured count notices instead of silently ending up
            # with every expert trainable.
            sentinel = f"_OUT_OF_RANGE_{num_experts}_"
            if sentinel not in _LOGGED_UNKNOWN_EXPERT_NAMES:
                _LOGGED_UNKNOWN_EXPERT_NAMES.add(sentinel)
                logger.warning(
                    "Expert index %d in %r exceeds configured num_experts=%d. "
                    "Either the model's expert count was misread or this is a "
                    "non-expert field whose suffix happens to be numeric.",
                    idx,
                    name,
                    num_experts,
                )
            return None
    if "expert" in name.lower() and "_UNKNOWN_EXPERT_LAYOUT_" not in _LOGGED_UNKNOWN_EXPERT_NAMES:
        _LOGGED_UNKNOWN_EXPERT_NAMES.add("_UNKNOWN_EXPERT_LAYOUT_")
        logger.info(
            "Unrecognized MoE expert parameter naming: %r — falling back to "
            "trainable. Add a regex to forgelm.model._EXPERT_NAME_PATTERNS "
            "to teach the resolver about this architecture.",
            name,
        )
    return None


def _freeze_unselected_experts(model, experts_to_train: str, num_experts: int) -> None:
    """Freeze the LoRA adapters of unselected experts. Run AFTER ``get_peft_model``.

    PEFT freezes every base parameter during adapter injection, so the trainable
    set is the injected ``lora_*`` adapters. Freezing those that resolve to an
    unselected expert is what actually constrains training to
    ``experts_to_train`` — the pre-PEFT base-weight freeze this replaces left the
    trainable set identical whether the field was set or not (F-P3-FABLE-02).

    Args:
        model: The PEFT-wrapped model with MoE architecture.
        experts_to_train: Comma-separated expert indices (e.g., "0,1,2").
        num_experts: Total number of experts in the model.
    """
    selected = _parse_selected_experts(experts_to_train, num_experts)
    if selected is None:
        return

    expert_adapter_params = 0
    frozen_count = 0
    for name, param in model.named_parameters():
        if "lora_" not in name:
            continue
        idx = _expert_index_in_name(name, num_experts)
        if idx is None:
            continue
        expert_adapter_params += 1
        if idx not in selected:
            param.requires_grad = False
            frozen_count += 1

    if expert_adapter_params == 0:
        # PEFT keys ``peft_config`` by adapter name (a dict, multi-adapter
        # design), so a plain ``getattr(..., "target_modules")`` returns "?".
        # Resolve the first adapter's config before reading the field.
        peft_config = getattr(model, "peft_config", None)
        if isinstance(peft_config, dict):
            peft_config = next(iter(peft_config.values()), None)
        target_modules = getattr(peft_config, "target_modules", "?")
        logger.warning(
            "experts_to_train=%r selected experts %s, but NO LoRA adapters were injected "
            "into any expert — lora.target_modules (%s) does not cover expert projections "
            "(e.g. gate_proj/up_proj/down_proj), so expert selection has no effect. Widen "
            "target_modules to train specific experts.",
            experts_to_train,
            sorted(selected),
            target_modules,
        )
        return

    logger.info(
        "MoE expert selection: training experts %s, froze %d of %d expert adapter parameter(s) "
        "from unselected experts.",
        sorted(selected),
        frozen_count,
        expert_adapter_params,
    )
