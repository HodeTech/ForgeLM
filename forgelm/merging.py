"""Model merging support.

Merge multiple LoRA adapters or fine-tuned models using various strategies.
Provides config-driven merging as a post-training step or standalone CLI command.
"""

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger("forgelm.merging")


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
) -> MergeResult:
    """Merge multiple LoRA/PEFT adapters into a single model.

    Args:
        base_model_path: Path or HF ID of the base model.
        adapters: List of dicts with 'path' and 'weight' keys.
        method: Merge strategy — "linear", "ties", "dare", "slerp".
        output_dir: Where to save the merged model.
        trust_remote_code: Allow custom code from model repos.

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
            merged = _advanced_merge(base_model, adapters, method)
        elif method == "slerp":
            merged = _slerp_merge(base_model, adapters)
        else:
            return MergeResult(success=False, error=f"Unknown merge method: {method}")

        # Save merged model
        os.makedirs(output_dir, exist_ok=True)
        merged.save_pretrained(output_dir, safe_serialization=True)
        tokenizer.save_pretrained(output_dir)
        logger.info("Merged model saved to %s", output_dir)

        return MergeResult(
            success=True,
            output_dir=output_dir,
            method=method,
            num_models=len(adapters),
        )

    except Exception as e:  # noqa: BLE001 — best-effort: model merging crosses HF model load (OSError/RuntimeError), PEFT adapter load (KeyError on missing config), torch tensor ops (RuntimeError on dtype/device mismatch), and mergekit-internal errors.  MergeResult(success=False) is the documented public contract.  # NOSONAR
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


def _advanced_merge(base_model, adapters, method):
    """TIES or DARE merge using native PyTorch implementation."""
    logger.info("Using %s merge strategy (native implementation).", method.upper())
    return _ties_dare_merge(base_model, adapters, method)


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
            if omega.abs() < 1e-6:
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


def _ties_dare_merge(base_model, adapters, method):
    """Merge using TIES or DARE algorithm directly on state dicts.

    TIES (TIES-Merging): Trim, Elect Sign, and Merge
    - Trims small delta values (keeps top-k% by magnitude)
    - Resolves sign conflicts by majority vote
    - Merges remaining values

    DARE (Drop And REscale):
    - Randomly drops delta values with probability p
    - Rescales remaining values by 1/(1-p) to preserve expected magnitude
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
    weights = [w / total_w for w in weights]

    # Merge
    merged_delta = {}
    for key in task_vectors[0]:
        deltas = [tv[key].float() for tv in task_vectors if key in tv]
        if not deltas:
            continue

        if method == "ties":
            merged_delta[key] = _ties_merge_tensor(deltas, weights, trim_fraction=0.2)
        elif method == "dare":
            merged_delta[key] = _dare_merge_tensor(deltas, weights, drop_rate=0.3)
        else:
            merged_delta[key] = sum(d * w for d, w in zip(deltas, weights))

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
