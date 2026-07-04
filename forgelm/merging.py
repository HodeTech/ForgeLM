"""Model merging support.

Merge multiple LoRA adapters or fine-tuned models using various strategies.
Provides config-driven merging as a post-training step or standalone CLI command.
"""

import logging
import math
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger("forgelm.merging")

# TIES/DARE merge hyperparameter defaults (F-P3-FABLE-60). These are the
# fallback values used when the caller does not pass an explicit override; the
# config-driven path threads ``MergeConfig.ties_trim_fraction`` /
# ``.dare_drop_rate`` / ``.dare_seed`` in instead. Named here so the defaults
# are a single documented source of truth rather than bare magic numbers at the
# call sites, and so the deliberate departure from the published paper defaults
# is visible. See _ties_dare_merge's docstring for the rationale.
_TIES_TRIM_FRACTION = 0.2  # trim bottom 20% of weights → keep top 80%
_DARE_DROP_RATE = 0.3  # drop 30% of deltas (paper recommends 0.9+ for FT deltas)
_DARE_SEED = 42  # fixed so a merge is reproducible run-to-run


@dataclass
class MergeResult:
    """Result of a model merge operation."""

    success: bool
    output_dir: str = ""
    method: str = ""
    num_models: int = 0
    error: Optional[str] = None


def merge_peft_adapters(
    base_model_path: str,
    adapters: List[Dict[str, Any]],
    method: str = "linear",
    output_dir: str = "./merged_model",
    trust_remote_code: bool = False,
    ties_trim_fraction: float = _TIES_TRIM_FRACTION,
    dare_drop_rate: float = _DARE_DROP_RATE,
    dare_seed: int = _DARE_SEED,
) -> MergeResult:
    """Merge multiple LoRA/PEFT adapters into a single model.

    Args:
        base_model_path: Path or HF ID of the base model.
        adapters: List of dicts with 'path' and 'weight' keys.
        method: Merge strategy — "linear", "ties", "dare", "slerp".
        output_dir: Where to save the merged model.
        trust_remote_code: Allow custom code from model repos.
        ties_trim_fraction: TIES trim fraction (only used when method == "ties").
        dare_drop_rate: DARE drop probability (only used when method == "dare").
        dare_seed: DARE RNG seed (only used when method == "dare").

    Returns:
        MergeResult with status.
    """
    from transformers import AutoModelForCausalLM, AutoTokenizer

    if not adapters:
        return MergeResult(success=False, error="No adapters provided for merging.")

    logger.info("Merging %d adapters with method '%s'...", len(adapters), method)
    logger.info("Base model: %s", base_model_path)

    try:
        # Load base model
        tokenizer = AutoTokenizer.from_pretrained(base_model_path, trust_remote_code=trust_remote_code)
        base_model = AutoModelForCausalLM.from_pretrained(
            base_model_path, trust_remote_code=trust_remote_code, device_map="cpu"
        )

        if method == "linear":
            merged = _linear_merge(base_model, adapters)
        elif method in ("ties", "dare"):
            merged = _advanced_merge(
                base_model,
                adapters,
                method,
                ties_trim_fraction=ties_trim_fraction,
                dare_drop_rate=dare_drop_rate,
                dare_seed=dare_seed,
            )
        elif method == "slerp":
            merged = _slerp_merge(base_model, adapters)
        else:
            return MergeResult(success=False, error=f"Unknown merge method: {method}")

        # Save merged model. transformers 5.x removed the `safe_serialization`
        # kwarg (safetensors is now the enforced default); passing it raises
        # TypeError, so we rely on the default.
        os.makedirs(output_dir, exist_ok=True)
        merged.save_pretrained(output_dir)
        tokenizer.save_pretrained(output_dir)
        logger.info("Merged model saved to %s", output_dir)

        return MergeResult(
            success=True,
            output_dir=output_dir,
            method=method,
            num_models=len(adapters),
        )

    except Exception as e:  # noqa: BLE001 — best-effort: model merging crosses HF model load (OSError/RuntimeError), PEFT adapter load (KeyError on missing config), and torch tensor ops (RuntimeError on dtype/device mismatch).  Merging is native (peft + torch); MergeResult(success=False) is the documented public contract.  # NOSONAR
        logger.exception("Model merging failed")
        return MergeResult(success=False, error=str(e))


def _linear_merge(base_model, adapters):
    """Linear interpolation merge: weighted average of adapter parameters."""
    from peft import PeftModel

    # Load and merge each adapter with weighted interpolation
    total_weight = sum(a.get("weight", 1.0) for a in adapters)
    if total_weight == 0:
        raise ValueError("Adapter weights sum to 0. Provide positive weights for merging.")

    # Capture base model state for reset between adapter loads
    base_state = {k: v.clone() for k, v in base_model.state_dict().items()}
    merged_state = None

    for adapter_info in adapters:
        path = adapter_info["path"]
        weight = adapter_info.get("weight", 1.0) / total_weight
        logger.info("  Loading adapter: %s (weight=%.3f)", path, weight)

        # Reset base model to original state before loading each adapter
        base_model.load_state_dict(base_state, strict=True)
        adapter_model = PeftModel.from_pretrained(base_model, path)
        merged_adapter = adapter_model.merge_and_unload()

        if merged_state is None:
            merged_state = {k: v.clone() * weight for k, v in merged_adapter.state_dict().items()}
        else:
            for k, v in merged_adapter.state_dict().items():
                if k in merged_state:
                    merged_state[k] += v * weight

        del adapter_model, merged_adapter

    missing, unexpected = base_model.load_state_dict(merged_state, strict=False)
    # Both directions of mismatch are useful when diagnosing a bad merge:
    # - missing  → adapter didn't supply weights the base model expects
    # - unexpected → adapter supplied weights the base model has no slot for
    if missing:
        logger.warning(
            "Merge left %d base-model parameters without adapter coverage (using base values).",
            len(missing),
        )
    if unexpected:
        logger.warning("Merge produced %d unexpected keys (ignored).", len(unexpected))
    return base_model


def _advanced_merge(
    base_model,
    adapters,
    method,
    ties_trim_fraction=_TIES_TRIM_FRACTION,
    dare_drop_rate=_DARE_DROP_RATE,
    dare_seed=_DARE_SEED,
):
    """TIES or DARE merge using native PyTorch implementation."""
    logger.info("Using %s merge strategy (native implementation).", method.upper())
    return _ties_dare_merge(
        base_model,
        adapters,
        method,
        ties_trim_fraction=ties_trim_fraction,
        dare_drop_rate=dare_drop_rate,
        dare_seed=dare_seed,
    )


def _slerp_merge(base_model, adapters):
    """SLERP merge between two models (only supports 2 models)."""
    if len(adapters) != 2:
        logger.warning("SLERP requires exactly 2 models. Got %d. Falling back to linear.", len(adapters))
        return _linear_merge(base_model, adapters)

    import torch
    from peft import PeftModel

    logger.info("Performing SLERP merge between 2 adapters...")
    w1 = adapters[0].get("weight", 1.0)
    w2 = adapters[1].get("weight", 1.0)
    t = w2 / (w1 + w2) if (w1 + w2) > 0 else 0.5  # normalize to [0,1] interpolation factor

    # Save base state to restore between adapter loads
    base_state = {k: v.clone() for k, v in base_model.state_dict().items()}

    model_a = PeftModel.from_pretrained(base_model, adapters[0]["path"])
    state_a = model_a.merge_and_unload().state_dict()
    del model_a

    # Restore base model before loading second adapter
    base_model.load_state_dict(base_state, strict=True)

    model_b = PeftModel.from_pretrained(base_model, adapters[1]["path"])
    state_b = model_b.merge_and_unload().state_dict()
    del model_b

    merged_state = {}
    for key in state_a:
        if key in state_b:
            v0 = state_a[key].float()
            v1 = state_b[key].float()
            # Simplified SLERP for parameter tensors
            # vector_norm flattens the parameter tensor and returns a scalar
            # magnitude — the right semantics for SLERP, regardless of tensor rank.
            dot = torch.sum(v0 * v1) / (torch.linalg.vector_norm(v0) * torch.linalg.vector_norm(v1) + 1e-8)
            dot = torch.clamp(dot, -1.0, 1.0)
            omega = torch.acos(dot)
            # Fall back to linear interpolation for near-parallel (omega ≈ 0)
            # and near-anti-parallel (omega ≈ π) cases.  In both regimes
            # sin(omega) is near zero, which amplifies numerical error
            # catastrophically (F-L-18).
            if omega.abs() < 1e-6 or (omega - math.pi).abs() < 1e-6:
                merged_state[key] = ((1 - t) * v0 + t * v1).to(state_a[key].dtype)
            else:
                so = torch.sin(omega)
                merged_state[key] = ((torch.sin((1 - t) * omega) / so) * v0 + (torch.sin(t * omega) / so) * v1).to(
                    state_a[key].dtype
                )
        else:
            merged_state[key] = state_a[key]

    base_model.load_state_dict(merged_state, strict=False)
    return base_model


def _ties_dare_merge(
    base_model,
    adapters,
    method,
    ties_trim_fraction=_TIES_TRIM_FRACTION,
    dare_drop_rate=_DARE_DROP_RATE,
    dare_seed=_DARE_SEED,
):
    """Merge using TIES or DARE algorithm directly on state dicts.

    TIES (TIES-Merging): Trim, Elect Sign, and Merge
    - Trims the smallest-magnitude delta values per task, keeping the rest
    - Resolves sign conflicts by majority vote
    - Merges remaining values

    DARE (Drop And REscale):
    - Randomly drops delta values with probability ``drop_rate``
    - Rescales remaining values by 1/(1-drop_rate) to preserve expected magnitude

    Hyperparameters (``ties_trim_fraction``, ``dare_drop_rate``, ``dare_seed``)
    are config-driven via :class:`forgelm.config.MergeConfig` and threaded in
    here; the module-level ``_TIES_TRIM_FRACTION`` / ``_DARE_DROP_RATE`` /
    ``_DARE_SEED`` constants are the defaults used when no override is supplied.
    The shipped defaults are deliberately conservative and differ from the
    published papers (F-P3-FABLE-60):

    * TIES ``trim_fraction=0.2`` trims the bottom 20% of weights and **keeps the
      top 80%**. The TIES-Merging paper's headline default keeps the top ~20%
      (a far sparser merge); ForgeLM keeps more signal so a two-adapter merge is
      less destructive out of the box.
    * DARE ``drop_rate=0.3`` is below the 0.9+ regime the DARE paper recommends
      for fine-tuned deltas, again favouring signal retention.

    Operators needing paper-faithful sparsity can either raise these knobs in
    ``merge:`` config or merge with an external tool (e.g. mergekit) — see
    docs/reference/configuration.md (merge section).
    """
    from peft import PeftModel

    logger.info("Running %s merge on %d adapters...", method.upper(), len(adapters))

    # Collect task vectors (deltas from base model)
    base_state = {k: v.clone() for k, v in base_model.state_dict().items()}
    task_vectors = []
    weights = []

    for adapter_info in adapters:
        path = adapter_info["path"]
        weight = adapter_info.get("weight", 1.0)
        weights.append(weight)
        logger.info("  Loading adapter: %s (weight=%.3f)", path, weight)

        base_model.load_state_dict(base_state, strict=True)
        adapter_model = PeftModel.from_pretrained(base_model, path)
        merged = adapter_model.merge_and_unload()
        delta = {k: merged.state_dict()[k] - base_state[k] for k in base_state if k in merged.state_dict()}
        task_vectors.append(delta)
        del adapter_model, merged

    # Normalize weights
    total_w = sum(weights)
    if total_w == 0:
        raise ValueError("Adapter weights sum to 0. Provide positive weights for merging.")
    weights = [w / total_w for w in weights]

    # Merge
    merged_delta = {}
    for key in task_vectors[0]:
        # Filter deltas and weights together so adapters that lack this key do
        # not silently truncate the zip (F-L-17).  Re-normalize so the key's
        # effective weights always sum to 1.0 regardless of which adapters carry it.
        pairs = [(tv[key].float(), w) for tv, w in zip(task_vectors, weights) if key in tv]
        if not pairs:
            continue
        deltas, key_weights = zip(*pairs)
        key_total = sum(key_weights)
        key_weights = [kw / key_total for kw in key_weights]

        if method == "ties":
            merged_delta[key] = _ties_merge_tensor(list(deltas), list(key_weights), trim_fraction=ties_trim_fraction)
        elif method == "dare":
            merged_delta[key] = _dare_merge_tensor(
                list(deltas), list(key_weights), drop_rate=dare_drop_rate, seed=dare_seed ^ (hash(key) & 0xFFFF_FFFF)
            )
        else:
            merged_delta[key] = sum(d * w for d, w in zip(deltas, key_weights))

    # Apply merged delta to base model
    final_state = {k: base_state[k] + merged_delta[k].to(base_state[k].dtype) for k in merged_delta}
    for k in base_state:
        if k not in final_state:
            final_state[k] = base_state[k]

    base_model.load_state_dict(final_state, strict=False)
    logger.info("%s merge complete.", method.upper())
    return base_model


def _ties_merge_tensor(deltas, weights, trim_fraction=0.2):
    """TIES-Merging for a single tensor: trim small values, elect sign, merge."""
    import torch

    stacked = torch.stack(deltas)

    # Step 1: Trim — zero out bottom trim_fraction by magnitude per task
    for i in range(len(deltas)):
        flat = stacked[i].abs().flatten()
        if flat.numel() == 0:
            continue
        # ``torch.quantile`` hard-fails above 2^24 elements (PyTorch #64947) —
        # which every real-model weight tensor exceeds (a 7B mlp.gate_proj is
        # ~45M elements), so the DEFAULT TIES merge crashed on any non-toy model
        # (F-P3-FABLE-19). ``kthvalue`` has no size limit and yields the same
        # trim threshold: the k-th smallest magnitude, k = trim_fraction · n.
        flat_f = flat.float()
        k = max(1, int(trim_fraction * flat_f.numel()))
        threshold = flat_f.kthvalue(k).values
        stacked[i][stacked[i].abs() < threshold] = 0.0

    # Step 2: Elect sign — majority vote (ties resolve to +1)
    sign_votes = torch.sign(stacked).sum(dim=0)
    elected_sign = torch.where(
        sign_votes >= 0,
        torch.ones_like(sign_votes),
        torch.full_like(sign_votes, -1.0),
    )

    # Step 3: Merge — weighted average of values that agree with elected sign
    result = torch.zeros_like(deltas[0])
    for i, (_delta, w) in enumerate(zip(deltas, weights)):
        mask = torch.sign(stacked[i]) == elected_sign
        result += (stacked[i] * mask.float()) * w

    return result


def _dare_merge_tensor(deltas, weights, drop_rate=0.3, seed: int = 42):
    """DARE merge for a single tensor: random drop + rescale."""
    import torch

    if drop_rate >= 1.0:
        return torch.zeros_like(deltas[0])

    generator = torch.Generator()
    generator.manual_seed(seed)
    result = torch.zeros_like(deltas[0])
    for delta, w in zip(deltas, weights):
        # Random binary mask (keep with probability 1-drop_rate)
        mask = torch.bernoulli(
            torch.full_like(delta, 1.0 - drop_rate),
            generator=generator,
        )
        # Rescale to preserve expected magnitude
        rescaled = delta * mask / (1.0 - drop_rate)
        result += rescaled * w

    return result
