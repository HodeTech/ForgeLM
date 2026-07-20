"""On-disk safety artefacts: the per-run JSON summary and the cross-run trend log.

Owns the GDPR / EU AI Act Art. 10 probe-text redaction applied to
``safety_results.json`` unless the operator opts in via
``SafetyConfig.include_eval_samples``.
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional

from ._types import _AttributionTelemetry, _CategoryTelemetry

logger = logging.getLogger("forgelm.safety")


# GDPR / EU AI Act Art. 10 — fields stripped from on-disk safety_results.json
# unless the operator opts in via SafetyConfig.include_eval_samples=True.
# Adversarial test prompts and the model's responses to them can carry
# sensitive content (jailbreak attempts, PII leakage, etc.).
#
# ``raw_verdict`` is the generation path's raw guard output (see
# ``_normalize_verdict_label``).  On the text-classification path the verdict
# is a closed-set head label and needs no redaction, but a generative guard
# replies in free text — and a *mis*configured one echoes or continues the
# adversarial probe, which is precisely the content the other two entries
# exist to strip.  The per-sample ``label`` is safe to keep in both modes
# because it is now rebuilt from a fixed vocabulary rather than sliced out of
# model output.
#
# ``classifier_error`` is the classification path's sibling of ``raw_verdict``:
# ``str(exc)[:200]`` from the HF pipeline boundary.  It was left out when
# ``raw_verdict`` was added, on the reading that an exception message is
# library text rather than data.  That reading does not hold — transformers
# tokenizer and shape errors routinely quote the offending input back
# (``ValueError`` carrying a ``repr`` of the sequence, tokenizer length errors
# echoing the text), so the field can carry probe or response content verbatim
# under exactly the conditions that produce it.  Both raw-verdict-shaped
# fields are therefore redacted by the same switch; there is no principled
# line that keeps one and strips the other.
_PII_REDACT_FIELDS: frozenset[str] = frozenset({"prompt", "response", "raw_verdict", "classifier_error"})


def safety_audit_fields(result: Any) -> Dict[str, Any]:
    """Build the ``safety.evaluation_completed`` audit payload from a SafetyResult.

    Lives here rather than inline in ``ForgeTrainer._apply_safety_result`` so
    the safety package owns the shape of its own Article 12 record: the
    scored/unscored split is a safety-package concept, and a caller assembling
    it by hand is a second place it can be forgotten.

    ``total_count`` makes a vacuous pass (zero probes evaluated)
    distinguishable from a real 100%-safe evaluation.  ``evaluation_completed``
    plus the scored/unscored split make a ``passed=False`` record legible
    without the artefact: an auditor can tell a measured safety failure from a
    run the classifier never answered, which is the difference between
    evidence of harm and absence of evidence — and the difference between a
    model that was deleted and one that was kept.  ``getattr`` defaults keep
    the payload well-formed for callers passing an older result shape.
    """
    return {
        "passed": result.passed,
        "safe_ratio": result.safe_ratio,
        "total_count": result.total_count,
        "evaluation_completed": getattr(result, "evaluation_completed", True),
        "scored_unsafe_count": getattr(result, "scored_unsafe_count", 0),
        "unscored_count": getattr(result, "unscored_count", 0),
        "safety_score": result.safety_score,
        "categories": result.category_distribution,
    }


def _save_safety_results(
    output_dir: str,
    *,
    scoring: str,
    safe_ratio: float,
    safety_score: float,
    unsafe_count: int,
    total: int,
    low_confidence_count: int,
    passed: bool,
    failure_reason: Optional[str],
    details: List[Dict[str, Any]],
    categories: _CategoryTelemetry,
    attribution: _AttributionTelemetry,
    include_samples: bool = False,
) -> None:
    """Write the JSON summary plus the cross-run trend entry.

    When ``include_samples`` is False (the default), the free-text fields
    listed in :data:`_PII_REDACT_FIELDS` are stripped from each detail entry.
    Set ``SafetyConfig.include_eval_samples=True`` to opt back in for
    debugging.
    """
    os.makedirs(output_dir, exist_ok=True)
    results_path = os.path.join(output_dir, "safety_results.json")
    redact = frozenset() if include_samples else _PII_REDACT_FIELDS
    output_data: Dict[str, Any] = {
        "scoring_method": scoring,
        "safe_ratio": safe_ratio,
        "safety_score": round(safety_score, 4),
        "unsafe_count": unsafe_count,
        # The decomposition of unsafe_count. Summing to unsafe_count, these
        # let a reader of the artefact alone tell "the guard read N unsafe
        # completions" from "the guard failed to answer N times and they were
        # counted unsafe fail-closed" — indistinguishable from unsafe_count.
        "scored_unsafe_count": attribution.scored_unsafe,
        "unscored_count": attribution.unscored,
        "total_count": total,
        "low_confidence_count": low_confidence_count,
        "passed": passed,
        # False marks a run whose verdict is not usable evidence about the
        # model (classifier unusable, or a failure attributable entirely to
        # unscored pairs). Auto-revert is suppressed on it and the CLI exits 2
        # rather than 3, so the artefact has to record which of the two a
        # passed=False row was.
        "evaluation_completed": attribution.evaluation_completed,
        "failure_reason": failure_reason,
        "details": [{k: v for k, v in d.items() if k not in redact} for d in details],
    }
    if categories.track:
        output_data["category_distribution"] = categories.dist
        output_data["severity_distribution"] = categories.severity_dist
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2)
    logger.info("Safety results saved to %s", results_path)
    _append_trend_entry(output_dir, safety_score, safe_ratio, passed, attribution)


def _append_trend_entry(
    output_dir: str,
    safety_score: float,
    safe_ratio: float,
    passed: bool,
    attribution: Optional[_AttributionTelemetry] = None,
) -> None:
    """Append safety score to cross-run trend history (JSON Lines).

    ``attribution`` carries the unscored decomposition into the trend so a
    degrading classifier is visible *across* runs, which is the only place it
    shows up as a trend at all: a run-over-run slide in ``safe_ratio`` driven
    entirely by a rising ``unscored_count`` reads as a model getting less safe
    until these two columns sit beside it.  Optional so library callers of the
    pre-existing four-argument signature keep working.
    """
    from datetime import datetime, timezone

    trend_path = os.path.join(output_dir, "safety_trend.jsonl")
    entry: Dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "safety_score": round(safety_score, 4),
        "safe_ratio": round(safe_ratio, 4),
        "passed": passed,
    }
    if attribution is not None:
        entry["scored_unsafe_count"] = attribution.scored_unsafe
        entry["unscored_count"] = attribution.unscored
        entry["evaluation_completed"] = attribution.evaluation_completed
    try:
        with open(trend_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        logger.info("Safety trend entry appended to %s", trend_path)
    except (OSError, TypeError, ValueError) as e:
        # OSError: filesystem (permission, full disk, missing dir).
        # TypeError/ValueError: json.dumps on unexpected entry shape.
        # Trend logging is non-fatal — a missing entry must not abort the
        # safety pass that already concluded successfully.
        logger.warning("Failed to write safety trend entry: %s", e)
