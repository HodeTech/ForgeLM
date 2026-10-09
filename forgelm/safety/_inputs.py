"""Caller-supplied safety-evaluation inputs, validated before any device work.

The adversarial probes file and the generation ``batch_size`` are both
checked here so a malformed probe set or an out-of-schema batch size fails
at eval start rather than deep inside the batched generation path.
"""

import json
import logging
import math
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


def _validate_thresholds(max_safety_regression: Any, thresholds: Any) -> None:
    """Library-API boundary check for every numeric gate parameter.

    ``run_safety_evaluation`` is public (``from forgelm.safety import
    run_safety_evaluation``) and its gate parameters arrive as a plain
    :class:`~forgelm.safety._types.SafetyEvalThresholds` dataclass, which has
    no ``__post_init__`` and therefore no validation at all. The training path
    reaches the same function through Pydantic, so the schema catches
    ``min_safety_score`` and ``min_classifier_confidence`` there — but a
    library caller bypasses that entirely, and one field is unguarded on
    *both* routes: ``severity_thresholds`` is ``Optional[Dict[str, float]]``
    with no per-value constraint, so ``{"critical": .nan}`` passes Pydantic's
    default ``allow_inf_nan=True`` and reaches the gate from ordinary YAML.

    Every one of these is compared with ``<`` or ``>``, and every such
    comparison against ``nan`` is False — so an unvalidated threshold does not
    merely misbehave, it makes the safety gate report PASS for a run that
    scored 100% unsafe. This is the fail-open that decides whether an unsafe
    model is promoted, so the check raises rather than clamping, and it runs
    **before any device work** so the caller learns immediately rather than
    after a multi-GB download.
    """
    from ._types import SEVERITY_LEVELS

    def _require_unit_interval(name: str, value: Any) -> None:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"{name} must be a number in [0.0, 1.0] (got {value!r})")
        if not math.isfinite(value) or not 0.0 <= float(value) <= 1.0:
            raise ValueError(
                f"{name} must be a finite number in [0.0, 1.0] (got {value!r}). "
                "A comparison against a non-finite threshold is always False, which would make "
                "the safety gate pass a run it should fail."
            )

    _require_unit_interval("max_safety_regression", max_safety_regression)

    if thresholds is None:
        return

    scoring = getattr(thresholds, "scoring", "binary")
    if scoring not in ("binary", "confidence_weighted"):
        raise ValueError(f"thresholds.scoring must be 'binary' or 'confidence_weighted' (got {scoring!r})")

    min_safety_score = getattr(thresholds, "min_safety_score", None)
    if min_safety_score is not None:
        _require_unit_interval("thresholds.min_safety_score", min_safety_score)
        # The same coupling ``SafetyConfig`` enforces on the YAML route. The gate
        # only consults ``min_safety_score`` under confidence-weighted scoring, so a
        # configured floor under ``binary`` is dead — and the run reports
        # ``passed=True`` having never applied the threshold the caller set.
        if scoring != "confidence_weighted":
            raise ValueError(
                f"thresholds.min_safety_score is only enforced when scoring='confidence_weighted' "
                f"(got scoring={scoring!r}); under binary scoring the gate would ignore it and pass. "
                "Set scoring='confidence_weighted' or drop min_safety_score."
            )

    _require_unit_interval(
        "thresholds.min_classifier_confidence", getattr(thresholds, "min_classifier_confidence", 0.7)
    )

    severity = getattr(thresholds, "severity_thresholds", None)
    if severity is not None:
        if not isinstance(severity, dict):
            raise ValueError(f"thresholds.severity_thresholds must be a dict or None (got {severity!r})")
        for level, value in severity.items():
            if level not in SEVERITY_LEVELS:
                raise ValueError(
                    f"thresholds.severity_thresholds key {level!r} is not a known severity level; "
                    f"expected one of {SEVERITY_LEVELS}"
                )
            _require_unit_interval(f"thresholds.severity_thresholds[{level!r}]", value)
        # ``SafetyConfig`` auto-enables ``track_categories`` here; a dataclass caller has
        # no "explicitly set" signal to key that on, and the gate consults per-severity
        # thresholds only when it is on, so an unreachable one must fail loudly.
        if severity and not getattr(thresholds, "track_categories", False):
            raise ValueError(
                "thresholds.severity_thresholds is only enforced when track_categories=True; "
                "the gate would ignore it and pass. Set track_categories=True or drop severity_thresholds."
            )
