"""
Core inference primitives shared across chat, judge, synthetic, and safety modules.

Provides a unified load/generate interface with streaming support, logit statistics,
and adaptive sampling.  Heavy dependencies (torch, transformers, peft) are imported
lazily so that --help and config parsing remain lightweight.
"""

from __future__ import annotations

import logging
from threading import Thread
from typing import Any, Dict, Iterator, List, Optional, Tuple

logger = logging.getLogger("forgelm.inference")


# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------


def _pick_tokenizer_source(path: str, adapter: Optional[str]) -> str:
    """Return the directory the tokenizer should be loaded from.

    Prefer the adapter directory when it carries ``tokenizer_config.json``
    — fine-tuning may have added special tokens or a custom chat template,
    and loading the base tokenizer would silently drop those.  HF saves
    ``tokenizer_config.json`` next to the adapter weights when the trainer
    called ``tokenizer.save_pretrained(adapter_dir)``.
    """
    if adapter:
        import os as _os

        if _os.path.isfile(_os.path.join(adapter, "tokenizer_config.json")):
            logger.info("Using tokenizer from adapter directory: %s", adapter)
            return adapter
    return path


def load_model(
    path: str,
    adapter: Optional[str] = None,
    backend: str = "transformers",
    load_in_4bit: bool = False,
    load_in_8bit: bool = False,
    trust_remote_code: bool = False,
    device_map: Optional[str] = "auto",
) -> Tuple[Any, Any]:
    """Load a causal LM and tokenizer for inference.

    Args:
        path: Local directory or HF Hub model ID.
        adapter: Optional PEFT adapter path.  Adapter weights are merged into
            the base model and unloaded so generation is self-contained.
        backend: ``"transformers"`` (default) or ``"unsloth"``.
        load_in_4bit: Enable bitsandbytes 4-bit NF4 quantisation (CUDA only).
        load_in_8bit: Enable bitsandbytes 8-bit quantisation (CUDA only).
        trust_remote_code: Allow execution of model-bundled code.
        device_map: Passed directly to ``from_pretrained``; ``"auto"`` for
            multi-GPU, ``None`` to skip placement (e.g. CPU-only).

    Returns:
        ``(model, tokenizer)`` tuple ready for generation.
    """
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    if backend.lower() == "unsloth":
        if load_in_4bit and load_in_8bit:
            logger.warning("Both load_in_4bit and load_in_8bit are set; preferring 4-bit for Unsloth backend.")
        return _load_unsloth(
            path,
            adapter,
            trust_remote_code,
            load_in_4bit=load_in_4bit,
            load_in_8bit=load_in_8bit,
        )

    logger.info("Loading model for inference: %s", path)

    tokenizer_source = _pick_tokenizer_source(path, adapter)
    tokenizer = AutoTokenizer.from_pretrained(tokenizer_source, trust_remote_code=trust_remote_code)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model_kwargs: Dict[str, Any] = {"trust_remote_code": trust_remote_code}
    if device_map is not None:
        model_kwargs["device_map"] = device_map

    if torch.cuda.is_available():
        if load_in_4bit:
            from transformers import BitsAndBytesConfig

            model_kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_compute_dtype=torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16,
                bnb_4bit_use_double_quant=True,
                bnb_4bit_quant_type="nf4",
            )
        elif load_in_8bit:
            from transformers import BitsAndBytesConfig

            model_kwargs["quantization_config"] = BitsAndBytesConfig(load_in_8bit=True)

    model = AutoModelForCausalLM.from_pretrained(path, **model_kwargs)
    model.eval()

    if adapter:
        logger.info("Loading PEFT adapter from %s, merging into base model.", adapter)
        from peft import PeftModel

        model = PeftModel.from_pretrained(model, adapter)
        model = model.merge_and_unload()
        logger.info("Adapter merged and unloaded.")

    return model, tokenizer


def _load_unsloth(
    path: str,
    adapter: Optional[str],
    trust_remote_code: bool,
    *,
    load_in_4bit: bool = False,
    load_in_8bit: bool = False,
) -> Tuple[Any, Any]:
    """Load model via the Unsloth backend for faster inference."""
    # Reject the unsupported adapter combination before the expensive import +
    # multi-GB ``from_pretrained`` load, so an operator passing --adapter to the
    # unsloth backend gets an immediate rejection rather than paying the full
    # model-load cost only to be refused at the end.
    if adapter:
        raise ValueError(
            "Unsloth backend does not support loading a separate adapter at inference time. "
            "Merge the adapter into the base model before inference "
            "(forgelm export --adapter ...), or use backend='transformers'."
        )

    try:
        from unsloth import FastLanguageModel
    except ImportError as e:
        raise ImportError(
            "unsloth backend requires the 'unsloth' extra. Install with: pip install 'forgelm[unsloth]'"
        ) from e

    # Default to 4-bit when neither flag is set (Unsloth's primary mode)
    if not load_in_4bit and not load_in_8bit:
        load_in_4bit = True

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=path,
        dtype=None,
        load_in_4bit=load_in_4bit,
        load_in_8bit=load_in_8bit,
        trust_remote_code=trust_remote_code,
    )
    FastLanguageModel.for_inference(model)

    return model, tokenizer


# ---------------------------------------------------------------------------
# Input preparation
# ---------------------------------------------------------------------------


def _build_prompt(
    tokenizer: Any,
    messages: List[Dict[str, str]],
) -> str:
    """Format a message list into a prompt string using the tokenizer's chat template.

    Falls back to a plain ``role: content`` concatenation when no template is
    available (e.g. raw base models).
    """
    if getattr(tokenizer, "chat_template", None) and hasattr(tokenizer, "apply_chat_template"):
        return tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)

    parts: List[str] = []
    for msg in messages:
        parts.append(f"{msg['role']}: {msg['content']}")
    # Mimic add_generation_prompt=True: signal the model to continue as assistant
    # rather than echoing the user role.  Without this, raw base models tend to
    # produce a "user: ..." continuation instead of a response.
    parts.append("assistant: ")
    return "\n".join(parts)


def _to_messages(
    prompt: str,
    system_prompt: Optional[str] = None,
    history: Optional[List[Dict[str, str]]] = None,
) -> List[Dict[str, str]]:
    """Assemble a message list from a user prompt, optional system prompt, and history."""
    messages: List[Dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": prompt})
    return messages


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


def generate(
    model: Any,
    tokenizer: Any,
    prompt: str,
    *,
    messages: Optional[List[Dict[str, str]]] = None,
    system_prompt: Optional[str] = None,
    history: Optional[List[Dict[str, str]]] = None,
    max_new_tokens: int = 512,
    temperature: float = 0.7,
    top_k: int = 50,
    top_p: float = 0.9,
    repetition_penalty: float = 1.1,
    do_sample: bool = True,
) -> str:
    """Generate a response for *prompt*.

    Handles chat template formatting automatically.  Pass *messages* to supply
    a pre-built message list (e.g. for multi-turn chat); otherwise the function
    builds one from *prompt*, *system_prompt*, and *history*.

    Returns the generated response text only (input tokens stripped).
    """
    import torch

    if messages is None:
        messages = _to_messages(prompt, system_prompt, history)

    text = _build_prompt(tokenizer, messages)
    input_ids = tokenizer(text, return_tensors="pt").input_ids.to(model.device)

    gen_kwargs: Dict[str, Any] = {
        "input_ids": input_ids,
        "max_new_tokens": max_new_tokens,
        "do_sample": do_sample,
        "pad_token_id": tokenizer.pad_token_id,
        "eos_token_id": tokenizer.eos_token_id,
    }
    if do_sample:
        gen_kwargs.update(temperature=temperature, top_k=top_k, top_p=top_p)
    gen_kwargs["repetition_penalty"] = repetition_penalty

    with torch.inference_mode():
        output_ids = model.generate(**gen_kwargs)

    new_tokens = output_ids[0][input_ids.shape[-1] :]
    return tokenizer.decode(new_tokens, skip_special_tokens=True)


def generate_stream(
    model: Any,
    tokenizer: Any,
    prompt: str,
    *,
    messages: Optional[List[Dict[str, str]]] = None,
    system_prompt: Optional[str] = None,
    history: Optional[List[Dict[str, str]]] = None,
    max_new_tokens: int = 512,
    temperature: float = 0.7,
    top_k: int = 50,
    top_p: float = 0.9,
    repetition_penalty: float = 1.1,
    do_sample: bool = True,
) -> Iterator[str]:
    """Stream generation token-by-token.

    Yields decoded text fragments as they are produced.  Uses
    ``TextIteratorStreamer`` in a background thread so the caller's loop can
    update the UI without blocking.
    """
    import torch
    from transformers import TextIteratorStreamer

    if messages is None:
        messages = _to_messages(prompt, system_prompt, history)

    text = _build_prompt(tokenizer, messages)
    input_ids = tokenizer(text, return_tensors="pt").input_ids.to(model.device)

    # timeout prevents an indefinite block when model.generate() raises before
    # the streamer's queue is populated (OOM, dtype mismatch, invalid input ids).
    streamer = TextIteratorStreamer(
        tokenizer,
        skip_prompt=True,
        skip_special_tokens=True,
        timeout=60.0,
    )

    gen_kwargs: Dict[str, Any] = {
        "input_ids": input_ids,
        "max_new_tokens": max_new_tokens,
        "do_sample": do_sample,
        "pad_token_id": tokenizer.pad_token_id,
        "eos_token_id": tokenizer.eos_token_id,
        "streamer": streamer,
    }
    if do_sample:
        gen_kwargs.update(temperature=temperature, top_k=top_k, top_p=top_p)
    gen_kwargs["repetition_penalty"] = repetition_penalty

    _exc: List[Exception] = []

    def _gen_thread() -> None:
        try:
            with torch.inference_mode():
                model.generate(**gen_kwargs)
        except Exception as e:  # noqa: BLE001 — best-effort: streaming generate runs in a worker thread; the broad catch funnels every torch error (CUDA OOM RuntimeError, dtype mismatch, tokenizer ValueError) into the shared list so the foreground thread can re-raise after joining. KeyboardInterrupt is a BaseException and is intentionally NOT caught here — it is delivered to the main thread and propagates to the CLI seam.  # NOSONAR
            _exc.append(e)

    thread = Thread(target=_gen_thread, daemon=True)
    thread.start()

    from queue import Empty as _QueueEmpty

    try:
        for token_text in streamer:
            yield token_text
    except _QueueEmpty as timeout_err:
        # Streamer timed out waiting for the next token.  If the worker thread
        # captured an exception, surface that; otherwise raise a TimeoutError
        # so callers can distinguish "stuck" from a normal end of generation.
        if _exc:
            raise _exc[0] from timeout_err
        raise TimeoutError(
            "generate_stream timed out waiting for the next token from model.generate()."
        ) from timeout_err

    thread.join()
    if _exc:
        raise _exc[0]


# ---------------------------------------------------------------------------
# Logit statistics
# ---------------------------------------------------------------------------


def logit_stats(logits: Any) -> Dict[str, float]:
    """Compute generation-quality statistics from a single-position logit tensor.

    Args:
        logits: 1-D tensor of shape ``[vocab_size]``.

    Returns:
        Dict with keys:
          - ``entropy``: Shannon entropy (nats) over the softmax distribution.
          - ``top1_prob``: Probability of the argmax token.
          - ``effective_vocab``: Number of tokens with probability > 1 %.
    """
    import torch

    probs = torch.softmax(logits.float(), dim=-1)
    log_probs = torch.log(probs + 1e-10)
    entropy = float(-torch.sum(probs * log_probs).item())
    top1_prob = float(probs.max().item())
    effective_vocab = int((probs > 0.01).sum().item())
    return {
        "entropy": round(entropy, 4),
        "top1_prob": round(top1_prob, 4),
        "effective_vocab": effective_vocab,
    }


# ---------------------------------------------------------------------------
# Adaptive sampling
# ---------------------------------------------------------------------------


def adaptive_sample(
    logits: Any,
    temperature: float = 1.0,
    top_k: int = 50,
    top_p: float = 0.9,
    entropy_threshold: float = 6.5,
) -> int:
    """Sample the next token with entropy-adaptive strategy.

    When the model is confident (entropy < *entropy_threshold*), uses greedy
    decoding to avoid introducing noise.  For high-entropy distributions,
    applies temperature scaling followed by top-k and nucleus (top-p) filtering
    before sampling.

    Args:
        logits: 1-D tensor of shape ``[vocab_size]``.
        temperature: Scaling factor for logits in sampling mode.
        top_k: Keep only the *top_k* most likely tokens.
        top_p: Nucleus probability mass threshold.
        entropy_threshold: Shannon entropy (nats) below which greedy decoding
            is used.  ``math.log(vocab_size)`` ≈ 10.8 for a 50 k vocab.

    Returns:
        Integer token index.
    """
    import torch

    probs = torch.softmax(logits.float(), dim=-1)
    entropy = float(-torch.sum(probs * torch.log(probs + 1e-10)).item())

    if entropy < entropy_threshold:
        return int(logits.argmax().item())

    # Temperature scaling
    scaled = logits.float() / max(temperature, 1e-6)

    # Top-k filtering
    if top_k > 0:
        k = min(top_k, scaled.size(-1))
        top_vals, _ = torch.topk(scaled, k)
        cutoff = top_vals[..., -1].unsqueeze(-1)
        scaled = scaled.masked_fill(scaled < cutoff, float("-inf"))

    # Nucleus (top-p) filtering
    if 0.0 < top_p < 1.0:
        sorted_logits, sorted_idx = torch.sort(scaled, descending=True)
        cumulative = torch.cumsum(torch.softmax(sorted_logits, dim=-1), dim=-1)
        remove = cumulative - torch.softmax(sorted_logits, dim=-1) > top_p
        sorted_logits[remove] = float("-inf")
        scaled = scaled.scatter(-1, sorted_idx, sorted_logits)

    sample_probs = torch.softmax(scaled, dim=-1)
    return int(torch.multinomial(sample_probs, num_samples=1).item())
