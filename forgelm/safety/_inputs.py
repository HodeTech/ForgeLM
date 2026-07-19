"""Caller-supplied safety-evaluation inputs, validated before any device work.

The adversarial probes file and the generation ``batch_size`` are both
checked here so a malformed probe set or an out-of-schema batch size fails
at eval start rather than deep inside the batched generation path.
"""

import json
import logging
from typing import Any, List

logger = logging.getLogger("forgelm.safety")


def _load_safety_prompts(test_prompts_path: str) -> List[str]:
    """Load safety test prompts from a JSONL file (one prompt per line).

    Rows that yield an empty/blank prompt — a JSON object using neither the
    ``prompt`` nor ``text`` key (e.g. ``{"instruction": ...}``), or a value
    that is whitespace-only — are skipped and counted, never appended as
    empty-string probes.  Otherwise the safety gate would "evaluate" garbage
    (generation runs unconditioned from BOS) and typically pass with a
    full-looking total count while no adversarial probe actually ran
    (F-P3-FABLE-16).

    A line that is valid JSON but **not** an object — a bare quoted string
    (``"how to hotwire a car"``) is treated as the prompt itself, consistent
    with the plain-text fallback; any other non-object value (number, array,
    ``null``) is a malformed probe and raises a ``ValueError`` naming the file
    and 1-based line number rather than the raw ``AttributeError`` a
    ``str``/``list`` would trigger on ``.get`` (F-P3-FABLE-53).
    """
    prompts: List[str] = []
    skipped = 0
    with open(test_prompts_path, "r", encoding="utf-8") as f:
        for lineno, raw in enumerate(f, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                # Not JSON at all — treat the raw line as a plain-text prompt.
                prompt = line
            else:
                if isinstance(data, dict):
                    prompt = data.get("prompt", data.get("text", ""))
                elif isinstance(data, str):
                    # A quoted-string probe — the JSON value IS the prompt.
                    prompt = data
                else:
                    raise ValueError(
                        f"Invalid safety prompt : {test_prompts_path} line {lineno} : "
                        f"top-level JSON value is {type(data).__name__}, not an object or string : "
                        f"each line must be a JSON object with a 'prompt'/'text' key, "
                        f"a quoted string, or plain text."
                    )
            if not isinstance(prompt, str) or not prompt.strip():
                skipped += 1
                continue
            prompts.append(prompt)
    if skipped:
        logger.warning(
            "Skipped %d row(s) in %s that yielded no usable prompt (missing 'prompt'/'text' key or blank value).",
            skipped,
            test_prompts_path,
        )
    return prompts


def _validate_batch_size(batch_size: Any) -> None:
    """Library-API boundary check.

    ``SafetyConfig.batch_size`` is parsed via Pydantic
    ``Field(default=8, ge=1)``, but ``run_safety_evaluation`` is also a
    public Python API (importable as ``from forgelm.safety import
    run_safety_evaluation``) so a direct caller can bypass the schema.
    Reject invalid values here with a clear message rather than silently
    producing a no-op via ``range(0, len(prompts), 0)`` deeper in the
    batched generation path.
    """
    if not isinstance(batch_size, int) or batch_size < 1:
        raise ValueError(f"batch_size must be a positive integer (got {batch_size!r})")
