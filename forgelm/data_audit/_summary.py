"""Operator-facing report rendering + summary-note builders.

Splits cleanly into:

* PII / secrets / quality / cross-split notes appended to ``report.notes``
  by :func:`audit_dataset`.
* :func:`summarize_report` — multi-line text rendering of an
  :class:`AuditReport`. Default mode collapses zero-finding splits;
  ``verbose=True`` prints every split unconditionally.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ._types import (
    PII_ML_SEVERITY,
    PII_SEVERITY,
    PII_SEVERITY_ORDER,
    AuditReport,
)


def _build_pii_severity(pii_summary: Dict[str, int]) -> Dict[str, Any]:
    """Aggregate PII counts into a severity-tiered breakdown.

    Maps each detected category through :data:`PII_SEVERITY` and emits a
    structured payload that compliance reviewers can parse at a glance:
    a per-tier total, a worst-tier verdict, and a per-type breakdown so
    nothing is lost from the underlying flat counts. Categories absent
    from :data:`PII_SEVERITY` (forward-compat for new types) fall back to
    ``unknown``.

    A snapshot of :data:`PII_SEVERITY` (regex categories) and
    :data:`PII_ML_SEVERITY` (Phase 12.5 ML-NER categories) is taken at
    call time so a test or downstream caller mutating either dict
    cannot corrupt the audit output mid-run; per-call audits see a
    stable merged table for the duration of their work. The two
    tables share the same tier vocabulary (``critical/high/medium/low``)
    on purpose so the verdict surface stays unified.
    """
    severity_table = {**PII_SEVERITY, **PII_ML_SEVERITY}
    if not pii_summary:
        return {
            "total": 0,
            "by_tier": dict.fromkeys(PII_SEVERITY_ORDER, 0),
            "by_type": {},
            "worst_tier": None,
        }

    by_tier: Dict[str, int] = dict.fromkeys(PII_SEVERITY_ORDER, 0)
    by_type: Dict[str, Dict[str, Any]] = {}
    total = 0
    worst_tier_idx: Optional[int] = None

    for kind, count in pii_summary.items():
        if count <= 0:
            continue
        tier = severity_table.get(kind, "unknown")
        by_type[kind] = {"count": count, "tier": tier}
        if tier in by_tier:
            by_tier[tier] += count
        else:  # pragma: no cover — defensive for future PII categories
            by_tier.setdefault(tier, 0)
            by_tier[tier] += count
        total += count
        if tier in PII_SEVERITY_ORDER:
            idx = PII_SEVERITY_ORDER.index(tier)
            if worst_tier_idx is None or idx < worst_tier_idx:
                worst_tier_idx = idx

    worst_tier = PII_SEVERITY_ORDER[worst_tier_idx] if worst_tier_idx is not None else None
    return {
        "total": total,
        "by_tier": by_tier,
        "by_type": by_type,
        "worst_tier": worst_tier,
    }


def _pii_summary_notes(pii_summary: Dict[str, int], pii_severity: Dict[str, Any]) -> List[str]:
    """Operator-actionable note (or "none flagged") for the aggregate PII counts.

    Severity-tier-aware: when a critical-tier category fires (credit card,
    IBAN), the note leads with that verdict so it can't be missed under a
    pile of lower-tier matches. Falls back to the original neutral message
    when nothing is flagged or the severity payload is empty.
    """
    if not pii_summary:
        return ["No PII flagged. (Regex-based detector — false negatives possible.)"]
    flag_total = sum(pii_summary.values())
    breakdown = ", ".join(f"{k}={v}" for k, v in sorted(pii_summary.items()))
    worst_tier = pii_severity.get("worst_tier") if pii_severity else None
    severity_lead = f"WORST tier: {worst_tier.upper()}. " if worst_tier else ""
    return [
        f"{severity_lead}PII flags surfaced ({flag_total} total: {breakdown}). "
        "Review before publishing; mask with `forgelm ingest --pii-mask` "
        "or use `forgelm.data_audit.mask_pii` programmatically."
    ]


def _cross_split_leak_notes(cross: Dict[str, Any]) -> List[str]:
    """Surface the WORST leak direction so an asymmetric figure doesn't bury it."""
    cross_pairs = cross.get("pairs", {}) or {}
    leaking = []
    for name, payload in cross_pairs.items():
        rates = [v for k, v in payload.items() if k.startswith("leak_rate_")]
        if rates and max(rates) > 0:
            leaking.append((name, max(rates)))
    if not leaking:
        return []
    worst = max(leaking, key=lambda kv: kv[1])
    return [
        f"Cross-split leakage detected in {len(leaking)} pair(s): "
        f"{', '.join(name for name, _ in leaking)}. "
        f"Worst leak rate: {worst[1]:.2%} ({worst[0]}). "
        "Re-shuffle splits before benchmarking — leaked rows poison test fidelity."
    ]


def _secrets_summary_notes(secrets_summary: Dict[str, int]) -> List[str]:
    """Operator-actionable note when credentials/keys land in the corpus.

    Compliance-critical: a leaked AWS / GitHub / OpenAI key in the
    training data gets memorised at SFT time. The message is loud
    (``CRITICAL``) on purpose — operators must see this above any
    PII / quality noise.
    """
    if not secrets_summary:
        return []
    total = sum(secrets_summary.values())
    breakdown = ", ".join(f"{k}={v}" for k, v in sorted(secrets_summary.items()))
    return [
        f"CRITICAL: {total} credential/secret span(s) detected ({breakdown}). "
        "Scrub before training with `forgelm ingest --secrets-mask` (Phase 12) "
        "or the regex helpers in `forgelm.data_audit.mask_secrets`."
    ]


def _quality_summary_notes(quality_summary: Dict[str, Any]) -> List[str]:
    """Operator-actionable note for the heuristic quality filter."""
    if not quality_summary or not quality_summary.get("samples_flagged"):
        return []
    flagged = quality_summary["samples_flagged"]
    by_check = quality_summary.get("by_check", {})
    breakdown = ", ".join(f"{k}={v}" for k, v in sorted(by_check.items()))
    return [
        f"Quality filter flagged {flagged} sample(s) ({breakdown}). "
        "Heuristics are intentionally conservative; review the offending "
        "rows before deciding to drop them. Pass `--quality-filter` again "
        "after fixes to re-measure."
    ]


def _fold_outcome_into_summary(
    outcome: Any,
    *,
    pii_summary: Dict[str, int],
    secrets_summary: Dict[str, int],
    quality_aggregate: Dict[str, int],
    enable_quality_filter: bool,
) -> Tuple[int, int]:
    """Merge a single split's findings into the cross-split aggregates.

    Returns ``(quality_samples_flagged, quality_samples_evaluated)`` so the
    caller can keep both numerator and denominator running totals without
    re-reading the outcome.
    """
    for kind, count in outcome.pii_split.items():
        pii_summary[kind] = pii_summary.get(kind, 0) + count
    for kind, count in outcome.info.get("secrets_counts", {}).items():
        secrets_summary[kind] = secrets_summary.get(kind, 0) + count
    if not enable_quality_filter:
        return 0, 0
    for kind, count in outcome.info.get("quality_flags_counts", {}).items():
        quality_aggregate[kind] = quality_aggregate.get(kind, 0) + count
    return (
        outcome.info.get("quality_samples_flagged", 0),
        outcome.info.get("quality_samples_evaluated", 0),
    )


def _build_quality_summary(
    *,
    enable_quality_filter: bool,
    samples_flagged_total: int,
    quality_aggregate: Dict[str, int],
    samples_evaluated_total: int,
) -> Dict[str, Any]:
    """Render the ``quality_summary`` block (empty dict when filter is off).

    The denominator is the number of rows that were actually run through
    :func:`_row_quality_flags` — null/empty/non-dict rows are excluded
    because the filter can't evaluate them, and including them would
    silently depress the overall score (a corpus that's 50 % null but
    100 % clean on the rest would otherwise read 0.5 instead of 1.0).
    """
    if not enable_quality_filter:
        return {}
    if samples_evaluated_total:
        overall_score = round(1.0 - (samples_flagged_total / samples_evaluated_total), 4)
    else:
        overall_score = 1.0
    return {
        "samples_flagged": samples_flagged_total,
        "samples_evaluated": samples_evaluated_total,
        "by_check": quality_aggregate,
        "overall_quality_score": overall_score,
    }


def _build_near_duplicate_summary(
    *,
    dedup_method: str,
    near_dup_pairs: Dict[str, int],
    near_dup_threshold: int,
    minhash_jaccard: float,
    minhash_num_perm: int,
) -> Dict[str, Any]:
    """Pack the ``near_duplicate_summary`` block with method-specific params."""
    summary: Dict[str, Any] = {
        "method": dedup_method,
        "pairs_per_split": near_dup_pairs,
    }
    if dedup_method == "minhash":
        summary["jaccard_threshold"] = minhash_jaccard
        summary["num_perm"] = minhash_num_perm
    else:
        summary["hamming_threshold"] = near_dup_threshold
    return summary


# ---------------------------------------------------------------------------
# summarize_report — operator-facing text rendering
# ---------------------------------------------------------------------------


def _split_has_findings(info: Dict[str, Any]) -> bool:
    """A split is "interesting" when any non-trivial signal is present.

    Used by :func:`summarize_report` in non-verbose mode to suppress
    splits with zero findings. The criteria stay loose on purpose —
    operators expect to see every split that has *anything* worth
    reviewing (errors, drift, leakage, PII, near-duplicates, decode
    issues), and only the all-clean rows get folded into a tail summary.
    """
    if "error" in info:
        return True
    for key in (
        "null_or_empty_count",
        "near_duplicate_pairs",
        "pii_counts",
        "secrets_counts",
        "quality_flags_counts",
        "schema_drift_columns",
        "non_object_rows",
        "parse_errors",
        "decode_errors",
    ):
        value = info.get(key)
        if isinstance(value, dict) and value:
            return True
        if isinstance(value, list) and value:
            return True
        if isinstance(value, int) and value > 0:
            return True
    return False


def _render_split_block(split_name: str, info: Dict[str, Any]) -> List[str]:
    """Operator-friendly rendering of one split's metrics. One concern per call.

    JSONL hygiene counters (``parse_errors``, ``decode_errors``,
    ``non_object_rows``) are surfaced **before** the body metrics so a
    malformed split is flagged at the top of the block — :func:`_split_has_findings`
    promotes splits that carry any of these, and the operator should see the
    reason ahead of length/PII/etc. lines that follow.
    """
    block: List[str] = [f"  └─ {split_name}: n={info.get('sample_count', 0)}"]
    if "error" in info:
        block.append(f"     ERROR        : {info['error']}")
        return block
    if info.get("parse_errors"):
        block.append(f"     parse errors    : {info['parse_errors']} (malformed JSONL line(s) skipped)")
    if info.get("decode_errors"):
        block.append(f"     decode errors   : {info['decode_errors']} (non-UTF-8 byte(s) replaced with U+FFFD)")
    if info.get("non_object_rows"):
        block.append(f"     non-object rows : {info['non_object_rows']} (valid JSON but not a dict)")
    text_len = info.get("text_length") or {}
    if text_len:
        block.append(
            f"     length  min={text_len['min']} max={text_len['max']} mean={text_len['mean']} p95={text_len['p95']}"
        )
    if info.get("null_or_empty_count"):
        block.append(f"     null/empty: {info['null_or_empty_count']} ({info['null_or_empty_rate'] * 100:.1f}%)")
    if info.get("near_duplicate_pairs"):
        block.append(f"     near-duplicate pairs: {info['near_duplicate_pairs']}")
    if info.get("languages_top3"):
        tops = ", ".join(f"{e['code']}={e['count']}" for e in info["languages_top3"])
        block.append(f"     languages (top-3): {tops}")
    if info.get("pii_counts"):
        pii = ", ".join(f"{k}={v}" for k, v in sorted(info["pii_counts"].items()))
        block.append(f"     PII             : {pii}")
    if info.get("secrets_counts"):
        secrets = ", ".join(f"{k}={v}" for k, v in sorted(info["secrets_counts"].items()))
        block.append(f"     secrets         : {secrets}")
    if info.get("quality_flags_counts"):
        quality = ", ".join(f"{k}={v}" for k, v in sorted(info["quality_flags_counts"].items()))
        block.append(f"     quality flags   : {quality} (samples_flagged={info.get('quality_samples_flagged', 0)})")
    if info.get("schema_drift_columns"):
        block.append(f"     schema drift    : {', '.join(info['schema_drift_columns'])}")
    return block


def _render_pii_severity(severity: Dict[str, Any]) -> List[str]:
    """Render the PII severity summary (or nothing when no flags fired)."""
    if not severity or not severity.get("total"):
        return []
    out = [f"  PII severity   : worst tier = {severity.get('worst_tier') or 'n/a'}"]
    by_tier = severity.get("by_tier", {})
    nonzero = [(tier, count) for tier, count in by_tier.items() if count]
    if nonzero:
        breakdown = ", ".join(f"{tier}={count}" for tier, count in nonzero)
        out.append(f"     by tier      : {breakdown}")
    return out


def summarize_report(report: AuditReport, *, verbose: bool = False) -> str:
    """Render an :class:`AuditReport` as a multi-line operator-facing summary.

    Default (``verbose=False``): splits with zero findings are folded into
    a single "N split(s) clean" line. This keeps the multi-split summary
    short on a healthy dataset while still surfacing every interesting
    signal — error, drift, leakage, PII, decode issues, near-duplicates.
    Pass ``verbose=True`` to print every split unconditionally.
    """
    lines = [
        "Data audit summary",
        f"  Source        : {report.source_path}",
        f"  Total samples : {report.total_samples}",
        f"  Splits        : {', '.join(report.splits)}",
    ]

    interesting: List[Tuple[str, Dict[str, Any]]] = []
    clean: List[str] = []
    for split_name, info in report.splits.items():
        if verbose or _split_has_findings(info):
            interesting.append((split_name, info))
        else:
            clean.append(split_name)

    for split_name, info in interesting:
        lines.extend(_render_split_block(split_name, info))

    if clean and not verbose:
        lines.append(f"  └─ ({len(clean)} clean split(s): {', '.join(clean)} — pass verbose=True to expand)")

    lines.extend(_render_pii_severity(report.pii_severity))
    if report.secrets_summary:
        total = sum(report.secrets_summary.values())
        breakdown = ", ".join(f"{k}={v}" for k, v in sorted(report.secrets_summary.items()))
        lines.append(f"  Secrets        : CRITICAL — {total} flagged ({breakdown})")
    if report.quality_summary and report.quality_summary.get("samples_flagged"):
        score = report.quality_summary.get("overall_quality_score", 0.0)
        flagged = report.quality_summary["samples_flagged"]
        lines.append(f"  Quality        : {flagged} sample(s) flagged (overall score = {score:.4f})")

    if report.cross_split_overlap.get("pairs"):
        method = report.cross_split_overlap.get("method", "simhash")
        lines.append(f"  Cross-split leakage ({method}):")
        for pair_name, payload in report.cross_split_overlap["pairs"].items():
            lines.append(_render_cross_split_pair(pair_name, payload))
    return "\n".join(lines)


def _render_cross_split_pair(pair_name: str, payload: Dict[str, Any]) -> str:
    """Human-readable one-liner for a cross-split leak pair.

    Renders ``leaked=`` counts + ``rate=`` percentages per direction instead
    of dumping the raw payload dict, matching the hand-formatted style of the
    split / PII / secrets / quality blocks elsewhere in this module.

    Split names are read off the payload's own ``leaked_rows_in_<split>``
    keys — built with the real split names in
    ``_orchestrator._pair_leak_payload`` and preserved in insertion order —
    rather than re-derived via ``pair_name.split("__", 1)``. A split name
    that itself contains ``__`` (e.g. ``train__aug`` paired with ``test``
    yields the composite key ``train__aug__test``) would otherwise split at
    the wrong boundary (``a="train"``, ``b="aug__test"``), miss both payload
    keys, and silently render ``leaked=0/0`` for a genuine leak.
    ``_cross_split_leak_notes`` reads the same payload directly and was never
    affected — only this per-pair render line was.
    """
    leaked_keys = [k for k in payload if k.startswith("leaked_rows_in_")]
    if len(leaked_keys) != 2:  # pragma: no cover — defensive, payload always carries exactly 2
        return f"    {pair_name}: {payload}"
    name_a = leaked_keys[0][len("leaked_rows_in_") :]
    name_b = leaked_keys[1][len("leaked_rows_in_") :]
    leaked_a = payload.get(f"leaked_rows_in_{name_a}", 0)
    leaked_b = payload.get(f"leaked_rows_in_{name_b}", 0)
    rate_a = payload.get(f"leak_rate_{name_a}", 0.0)
    rate_b = payload.get(f"leak_rate_{name_b}", 0.0)
    return f"    {pair_name}: leaked={leaked_a}/{leaked_b} rate={rate_a:.2%}/{rate_b:.2%}"


__all__ = [
    "_build_pii_severity",
    "_pii_summary_notes",
    "_cross_split_leak_notes",
    "_secrets_summary_notes",
    "_quality_summary_notes",
    "_fold_outcome_into_summary",
    "_build_quality_summary",
    "_build_near_duplicate_summary",
    "_split_has_findings",
    "_render_split_block",
    "_render_pii_severity",
    "_render_cross_split_pair",
    "summarize_report",
]
