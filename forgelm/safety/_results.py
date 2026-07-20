"""On-disk safety artefacts: the per-run JSON summary and the cross-run trend log.

Owns the GDPR / EU AI Act Art. 10 probe-text redaction applied to
``safety_results.json`` unless the operator opts in via
``SafetyConfig.include_eval_samples``.
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional

from ._types import _CategoryTelemetry

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
_PII_REDACT_FIELDS: frozenset[str] = frozenset({"prompt", "response", "raw_verdict"})


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
    include_samples: bool = False,
) -> None:
    """Write the JSON summary plus the cross-run trend entry.

    When ``include_samples`` is False (the default), raw ``prompt`` /
    ``response`` strings are stripped from each detail entry.  Set
    ``SafetyConfig.include_eval_samples=True`` to opt back in for debugging.
    """
    os.makedirs(output_dir, exist_ok=True)
    results_path = os.path.join(output_dir, "safety_results.json")
    redact = frozenset() if include_samples else _PII_REDACT_FIELDS
    output_data: Dict[str, Any] = {
        "scoring_method": scoring,
        "safe_ratio": safe_ratio,
        "safety_score": round(safety_score, 4),
        "unsafe_count": unsafe_count,
        "total_count": total,
        "low_confidence_count": low_confidence_count,
        "passed": passed,
        "failure_reason": failure_reason,
        "details": [{k: v for k, v in d.items() if k not in redact} for d in details],
    }
    if categories.track:
        output_data["category_distribution"] = categories.dist
        output_data["severity_distribution"] = categories.severity_dist
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2)
    logger.info("Safety results saved to %s", results_path)
    _append_trend_entry(output_dir, safety_score, safe_ratio, passed)


def _append_trend_entry(output_dir: str, safety_score: float, safe_ratio: float, passed: bool) -> None:
    """Append safety score to cross-run trend history (JSON Lines)."""
    from datetime import datetime, timezone

    trend_path = os.path.join(output_dir, "safety_trend.jsonl")
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "safety_score": round(safety_score, 4),
        "safe_ratio": round(safe_ratio, 4),
        "passed": passed,
    }
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
