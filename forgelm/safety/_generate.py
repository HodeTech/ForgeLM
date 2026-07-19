"""Fine-tuned-model response generation for the safety probe set.

Batched generation, the CUDA-OOM to per-prompt retry cascade, and the VRAM
handoff that ends the fine-tuned model's device lifecycle before the safety
classifier is loaded.
"""

import logging
from typing import Any, List

logger = logging.getLogger("forgelm.safety")


def _generate_one_safety_response(model: Any, tokenizer: Any, prompt: str, max_new_tokens: int) -> str:
    """Single-prompt fallback used when a batch hits CUDA OOM."""
    import torch

    try:
        inputs = tokenizer(prompt, return_tensors="pt", truncation=True, max_length=1024)
        inputs = {k: v.to(model.device) for k, v in inputs.items()}
        with torch.no_grad():
            outputs = model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False)
        return tokenizer.decode(outputs[0][inputs["input_ids"].shape[1] :], skip_special_tokens=True)
    except (RuntimeError, ValueError, TypeError, IndexError, KeyError) as e:
        # Tokenizer + generate boundary. RuntimeError covers CUDA OOM /
        # device-side asserts, ValueError/TypeError cover bad-shape inputs,
        # IndexError covers empty / oversize sequences, KeyError covers
        # malformed BatchEncoding dicts. This is the bottom of the OOM
        # recovery cascade — empty response is the documented fallback so
        # one bad prompt never blanks out the whole batch.
        logger.warning("Failed to generate response for prompt: %s", e)
        return ""


def _generate_safety_batch_with_oom_retry(
    model: Any,
    tokenizer: Any,
    batch: List[str],
    batch_start: int,
    max_new_tokens: int,
) -> List[str]:
    """Run one safety batch; on CUDA OOM or any other generation error fall back to per-prompt.

    Extracted so :func:`_generate_safety_responses` stays linear under the
    cognitive-complexity ceiling and so the OOM/retry policy is
    independently testable.
    """
    import torch

    try:
        inputs = tokenizer(
            batch,
            return_tensors="pt",
            truncation=True,
            max_length=1024,
            padding="longest",
        )
        inputs = {k: v.to(model.device) for k, v in inputs.items()}
        with torch.no_grad():
            outputs = model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False)
        prompt_len = inputs["input_ids"].shape[1]
        return [tokenizer.decode(row[prompt_len:], skip_special_tokens=True) for row in outputs]
    except torch.cuda.OutOfMemoryError as e:
        logger.warning(
            "CUDA OOM on safety-generation batch of %d (start=%d). "
            "Falling back to single-prompt generation for this batch: %s",
            len(batch),
            batch_start,
            e,
        )
        try:
            torch.cuda.empty_cache()
        except RuntimeError as cache_exc:
            # Mirror _release_model_from_gpu: a failed cache-clear on a
            # flaky/degraded CUDA driver is non-fatal here (we still fall back to
            # per-prompt generation), but swallowing it silently hides why a
            # second OOM on the fallback path is more likely.
            logger.warning("Could not empty CUDA cache during OOM fallback: %s", cache_exc)
        return [_generate_one_safety_response(model, tokenizer, p, max_new_tokens) for p in batch]
    except (RuntimeError, ValueError, TypeError, IndexError, KeyError) as e:
        # Non-OOM batch failure — fall back to per-prompt so a single
        # malformed input can't blank out the whole batch. RuntimeError
        # covers CUDA / driver errors below the OOM-specific branch above,
        # ValueError/TypeError/KeyError cover tokenizer-side issues,
        # IndexError covers shape mismatches in pad-longest path.
        logger.warning(
            "Safety-generation batch failed (start=%d, size=%d), retrying per-prompt: %s",
            batch_start,
            len(batch),
            e,
        )
        return [_generate_one_safety_response(model, tokenizer, p, max_new_tokens) for p in batch]


def _generate_safety_responses(
    model: Any,
    tokenizer: Any,
    prompts: List[str],
    max_new_tokens: int,
    batch_size: int = 8,
) -> List[str]:
    """Generate fine-tuned-model responses for the safety prompt set.

    Batches ``batch_size`` prompts at a time with pad-longest so short
    prompts don't waste compute on padding; per-batch error handling is
    delegated to :func:`_generate_safety_batch_with_oom_retry`.
    """
    # Ensure tokenizer has a pad token — required for batched padding.
    # We use eos_token as a safe default (matches HF pattern in load path).
    if getattr(tokenizer, "pad_token", None) is None and getattr(tokenizer, "eos_token", None) is not None:
        tokenizer.pad_token = tokenizer.eos_token

    # Left-pad for decoder-only generation so the prompt boundary lines up
    # across rows (right-pad shifts the boundary into the padding region
    # and produces garbage continuations on the shorter samples).
    original_padding_side = getattr(tokenizer, "padding_side", "right")
    tokenizer.padding_side = "left"

    responses: List[str] = []
    try:
        for batch_start in range(0, len(prompts), batch_size):
            batch = prompts[batch_start : batch_start + batch_size]
            responses.extend(
                _generate_safety_batch_with_oom_retry(model, tokenizer, batch, batch_start, max_new_tokens)
            )
    finally:
        tokenizer.padding_side = original_padding_side

    return responses


def _release_model_from_gpu(model: Any) -> None:
    """Move the fine-tuned model off the GPU before loading the safety classifier.

    The caller still holds a reference; ``del model`` here would only drop
    the local binding, not free the object. The caller must clear its own
    reference (set to ``None``) for VRAM to actually be reclaimed.
    """
    import gc

    import torch

    cpu_moved = False
    cache_cleared = False
    try:
        model.cpu()
        cpu_moved = True
    except RuntimeError as e:
        # CUDA OOM during transfer / device-side asserts. Not fatal —
        # the safety pass can still proceed on the existing device — but
        # the operator deserves to know that the cleanup didn't run.
        logger.warning("Could not move fine-tuned model to CPU before safety eval: %s", e)
    gc.collect()
    try:
        torch.cuda.empty_cache()
        cache_cleared = True
    except RuntimeError as e:
        # `empty_cache` raises on driver / CUDA-init failures only. Same
        # rationale: log loud, do not abort the surrounding safety pass.
        logger.warning("Could not empty CUDA cache before safety eval: %s", e)
    if cpu_moved and cache_cleared:
        logger.info(
            "Fine-tuned model moved to CPU before loading safety classifier. "
            "If OOM occurs, reduce classifier model size or increase available VRAM."
        )
    else:
        logger.warning(
            "VRAM cleanup before safety classifier was partial "
            "(cpu_moved=%s, cache_cleared=%s). OOM is more likely on the "
            "classifier load — reduce classifier model size or free VRAM manually.",
            cpu_moved,
            cache_cleared,
        )
