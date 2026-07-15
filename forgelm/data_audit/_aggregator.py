"""Per-split streaming aggregator + per-split audit driver.

:class:`_StreamingAggregator` is the heaviest hub in the package — it
folds the row-by-row JSONL stream into the structured metrics record the
audit report consumes. Methods reach across many concerns
(PII regex, optional Presidio ML, secrets, simhash/MinHash dedup, length
digest, quality flags) but every call site stays inside this file so the
public callsites in the orchestrator stay flat.
"""

from __future__ import annotations

import logging
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ._minhash import compute_minhash, find_near_duplicates_minhash
from ._pii_ml import detect_pii_ml
from ._pii_regex import detect_pii
from ._quality import _row_quality_flags
from ._secrets import detect_secrets
from ._simhash import compute_simhash, find_near_duplicates
from ._streaming import (
    _LANG_SAMPLE_SIZE,
    _PROGRESS_INTERVAL,
    _compute_top_languages,
    _extract_text_payload,
    _LengthDigest,
    _read_jsonl_split,
)
from ._types import DEFAULT_MINHASH_JACCARD, DEFAULT_MINHASH_NUM_PERM, DEFAULT_NEAR_DUP_HAMMING

logger = logging.getLogger("forgelm.data_audit")


@dataclass
class _StreamingAggregator:
    """Single-pass collector for per-split audit metrics.

    Owns one concern: turn the ``(row, parse_err, decode_err)`` stream
    coming out of :func:`_read_jsonl_split` into the structured ``info``
    dict the audit report consumes. Holding state on a dataclass keeps
    the streaming loop in :func:`_audit_split` flat and the field roles
    self-documenting; nothing else in the module instantiates this.

    Phase 12 additions:

    * ``dedup_method`` selects simhash (default; populates ``fingerprints``)
      vs. MinHash (populates ``minhashes``). Only one is computed per row
      so we don't pay the other method's cost.
    * ``secrets_counts`` aggregates :func:`detect_secrets` hits across rows.
      Always scanned — credentials are never opt-in.
    * ``quality_flags_counts`` aggregates :func:`_row_quality_flags` hits
      when ``enable_quality_filter`` is True (opt-in via CLI flag).
    """

    sample_count: int = 0
    parse_errors: int = 0
    decode_errors: int = 0
    non_object_rows: int = 0
    null_or_empty: int = 0
    # Phase 11.5: keep a Counter of distinct keysets rather than appending
    # one frozenset per row. On a 1 M-row split with stable schema this is
    # 1 entry vs. 1 M identical frozensets — the .most_common(1) lookup at
    # finalisation time is unchanged.
    keyset_counts: "Counter[frozenset]" = field(default_factory=Counter)
    seen_columns: "OrderedDict[str, None]" = field(default_factory=OrderedDict)
    length_digest: _LengthDigest = field(default_factory=_LengthDigest)
    fingerprints: List[int] = field(default_factory=list)
    minhashes: List[Optional[Any]] = field(default_factory=list)
    pii_counts: Dict[str, int] = field(default_factory=dict)
    secrets_counts: Dict[str, int] = field(default_factory=dict)
    quality_flags_counts: Dict[str, int] = field(default_factory=dict)
    quality_samples_flagged: int = 0
    quality_samples_evaluated: int = 0  # rows that actually went through _row_quality_flags
    lang_sample: List[str] = field(default_factory=list)
    # Reservoir-sampling state for ``lang_sample`` so the fixed-size language
    # sample is representative regardless of row ordering (a corpus built by
    # concatenating per-language source files would otherwise report only the
    # first block's language). ``lang_sample_seen`` counts every text-bearing
    # payload offered; ``lang_rng_counter`` is the inline LCG mirrored from
    # ``_LengthDigest`` — deterministic across runs and worker counts so the
    # byte-identical contract holds. Seeded at the multiplier (not 0) for the
    # same first-element reason documented on ``_LengthDigest._rng_counter``.
    lang_sample_seen: int = 0
    lang_rng_counter: int = 6364136223846793005
    # Phase 12 configuration (set once by the orchestrator; never mutated).
    dedup_method: str = "simhash"
    minhash_num_perm: int = DEFAULT_MINHASH_NUM_PERM
    # Phase 12.5: opt-in ML-NER PII detection (Presidio). Off by default.
    enable_pii_ml: bool = False
    pii_ml_language: str = "en"
    enable_quality_filter: bool = False


def _record_schema_for_dict(agg: _StreamingAggregator, row: Dict[str, Any]) -> None:
    keys = frozenset(row.keys())
    agg.keyset_counts[keys] += 1
    for col in keys:
        agg.seen_columns.setdefault(col, None)


def _record_dedup_signature(agg: _StreamingAggregator, payload: str) -> None:
    """Compute the per-row dedup signature for the aggregator's selected method.

    Only one of ``fingerprints`` / ``minhashes`` is populated per row —
    paying both costs would defeat the point of method selection. The
    sentinel for "skip in pair-walks" differs by method: simhash uses
    integer ``0`` (cheap to test); minhash uses ``None``.
    """
    if agg.dedup_method == "minhash":
        agg.minhashes.append(compute_minhash(payload, num_perm=agg.minhash_num_perm))
    else:
        agg.fingerprints.append(compute_simhash(payload))


def _record_dedup_sentinel(agg: _StreamingAggregator) -> None:
    """Append the empty-row sentinel for whichever dedup method is active."""
    if agg.dedup_method == "minhash":
        agg.minhashes.append(None)
    else:
        agg.fingerprints.append(0)


def _record_text_metrics(agg: _StreamingAggregator, payload: str) -> None:
    agg.length_digest.update(len(payload))
    # Cap each sample at 512 chars: lang detection only needs a short snippet
    # to identify the language, and unbounded payloads inflate peak memory on
    # multi-GB JSONL inputs.
    snippet = payload[:512]
    agg.lang_sample_seen += 1
    if len(agg.lang_sample) < _LANG_SAMPLE_SIZE:
        agg.lang_sample.append(snippet)
    else:
        # Algorithm R reservoir sampling: replace a random slot with
        # probability _LANG_SAMPLE_SIZE / seen so the sample stays uniform
        # over the whole stream instead of freezing on the first N rows.
        # Same inline LCG as ``_LengthDigest.update`` — no ``random`` import,
        # deterministic across runs and worker counts.
        agg.lang_rng_counter = (agg.lang_rng_counter * 6364136223846793005 + 1) & 0xFFFFFFFFFFFFFFFF
        j = agg.lang_rng_counter % agg.lang_sample_seen
        if j < _LANG_SAMPLE_SIZE:
            agg.lang_sample[j] = snippet
    _record_dedup_signature(agg, payload)
    for kind, count in detect_pii(payload).items():
        agg.pii_counts[kind] = agg.pii_counts.get(kind, 0) + count
    if agg.enable_pii_ml:
        # Phase 12.5: Presidio NER findings layer onto the same pii_counts
        # bucket. The category names are disjoint from the regex set
        # (person / organization / location vs. email / phone / *_id),
        # so the merged counts present both views without double-counting.
        # ``pii_ml_language`` is plumbed through so a Turkish-majority
        # corpus can be audited with a Turkish spaCy model rather than
        # silently scoring zero NER findings under English.
        for kind, count in detect_pii_ml(payload, language=agg.pii_ml_language).items():
            agg.pii_counts[kind] = agg.pii_counts.get(kind, 0) + count
    for kind, count in detect_secrets(payload).items():
        agg.secrets_counts[kind] = agg.secrets_counts.get(kind, 0) + count
    if agg.enable_quality_filter:
        # Count this row as "evaluated" — the denominator in
        # ``overall_quality_score`` must reflect rows that actually went
        # through ``_row_quality_flags``, not the full sample count
        # (null/non-dict rows skip this branch and would otherwise
        # depress the score).
        agg.quality_samples_evaluated += 1
        flags = _row_quality_flags(payload)
        if flags:
            agg.quality_samples_flagged += 1
            for flag in flags:
                agg.quality_flags_counts[flag] = agg.quality_flags_counts.get(flag, 0) + 1


def _ingest_row(agg: _StreamingAggregator, row: Any, parse_err: bool, decode_err: bool) -> None:
    """Fold one stream element into the aggregator. Single-concern helper."""
    if decode_err:
        agg.decode_errors += 1
    if parse_err:
        agg.parse_errors += 1
        return

    agg.sample_count += 1

    if not isinstance(row, dict):
        # Non-dict rows are valid JSON but the audit's text-payload pipeline
        # cannot extract anything useful. We flag them as ``non_object_rows``
        # (sharp shape-problem signal — distinct from "row had a text column
        # but it was empty") AND bump ``null_or_empty`` so downstream length
        # stats and the null_or_empty_rate treat them as unusable rows. Without
        # the null_or_empty bump, null_or_empty_rate would silently understate
        # corruption while length stats / fingerprints excluded them. The
        # sentinel ``0`` fingerprint (or ``None`` minhash) here keeps the
        # signature index aligned with row indices so within/cross-split LSH
        # walks skip them cleanly. Tested at
        # tests/test_data_audit.py::TestNonDictRowTolerance.
        agg.non_object_rows += 1
        agg.null_or_empty += 1
        _record_dedup_sentinel(agg)
        return

    _record_schema_for_dict(agg, row)
    payload = _extract_text_payload(row)
    if not payload:
        agg.null_or_empty += 1
        _record_dedup_sentinel(agg)
        return
    _record_text_metrics(agg, payload)


def _populate_schema_block(info: Dict[str, Any], agg: _StreamingAggregator) -> None:
    """Fill columns / schema-drift / non-object-row fields on ``info``."""
    columns_list = list(agg.seen_columns)
    if columns_list:
        info["columns"] = columns_list
    if agg.keyset_counts:
        most_common_keyset, _ = agg.keyset_counts.most_common(1)[0]
        base_columns = set(most_common_keyset)
        drift_columns = [c for c in columns_list if c not in base_columns]
        if drift_columns:
            info["schema_drift_columns"] = drift_columns
    if agg.non_object_rows:
        info["non_object_rows"] = agg.non_object_rows


def _populate_optional_findings(info: Dict[str, Any], agg: _StreamingAggregator) -> None:
    """Fill PII / secrets / quality blocks on ``info`` (only when non-empty)."""
    if agg.pii_counts:
        info["pii_counts"] = dict(agg.pii_counts)
    if agg.secrets_counts:
        info["secrets_counts"] = dict(agg.secrets_counts)
    if agg.enable_quality_filter:
        # ``quality_samples_evaluated`` / ``quality_samples_flagged`` are
        # accumulated for *every* row the filter ran on (see
        # ``_record_text_metrics``), including a perfectly clean split with
        # zero flags. Surface them whenever the filter was enabled — NOT only
        # when a flag fired — otherwise a clean split's evaluated-row count
        # never reaches ``_build_quality_summary``'s denominator and
        # ``overall_quality_score`` (an EU AI Act Article 10 number) is
        # silently biased low on any multi-split corpus with a clean split.
        # ``quality_flags_counts`` stays gated on non-empty as before.
        info["quality_samples_evaluated"] = agg.quality_samples_evaluated
        info["quality_samples_flagged"] = agg.quality_samples_flagged
        if agg.quality_flags_counts:
            info["quality_flags_counts"] = dict(agg.quality_flags_counts)


def _within_split_pairs(
    agg: _StreamingAggregator,
    *,
    near_dup_threshold: int,
    minhash_jaccard: float,
) -> int:
    """Run the within-split near-duplicate scan, dispatching on dedup method."""
    if agg.dedup_method == "minhash":
        pairs = find_near_duplicates_minhash(
            agg.minhashes,
            jaccard_threshold=minhash_jaccard,
            num_perm=agg.minhash_num_perm,
        )
    else:
        pairs = find_near_duplicates(agg.fingerprints, threshold=near_dup_threshold)
    return len(pairs)


def _aggregator_to_info(
    split_name: str,
    agg: _StreamingAggregator,
    *,
    near_dup_threshold: int,
    minhash_jaccard: float = DEFAULT_MINHASH_JACCARD,
) -> Dict[str, Any]:
    """Render the aggregator state as the per-split ``info`` payload."""
    info: Dict[str, Any] = {"sample_count": agg.sample_count}
    if agg.sample_count == 0:
        info["near_duplicate_pairs"] = 0
        return info

    _populate_schema_block(info, agg)

    length_stats = agg.length_digest.stats()
    if length_stats:
        info["text_length"] = length_stats
    info["null_or_empty_count"] = agg.null_or_empty
    info["null_or_empty_rate"] = round(agg.null_or_empty / agg.sample_count, 4)

    languages_top3 = _compute_top_languages(agg.lang_sample)
    if languages_top3:
        info["languages_top3"] = languages_top3

    if agg.dedup_method == "minhash":
        # Mirror simhash_distinct's semantic: count *unique* sketches, not
        # just the number of non-empty rows. ``hashvalues.tobytes()`` gives
        # a hashable, memory-efficient fingerprint of each MinHash state.
        info["minhash_distinct"] = len({m.hashvalues.tobytes() for m in agg.minhashes if m is not None})
    else:
        info["simhash_distinct"] = len({fp for fp in agg.fingerprints if fp != 0})

    _populate_optional_findings(info, agg)

    if agg.sample_count >= _PROGRESS_INTERVAL:
        logger.info(
            "audit/%s: scanning for near-duplicates (%s; %d rows)…",
            split_name,
            "MinHash LSH" if agg.dedup_method == "minhash" else "simhash LSH-banded",
            agg.sample_count,
        )
    info["near_duplicate_pairs"] = _within_split_pairs(
        agg,
        near_dup_threshold=near_dup_threshold,
        minhash_jaccard=minhash_jaccard,
    )
    return info


def _audit_split(
    split_name: str,
    path: Path,
    *,
    near_dup_threshold: int = DEFAULT_NEAR_DUP_HAMMING,
    dedup_method: str = "simhash",
    minhash_jaccard: float = DEFAULT_MINHASH_JACCARD,
    minhash_num_perm: int = DEFAULT_MINHASH_NUM_PERM,
    enable_quality_filter: bool = False,
    enable_pii_ml: bool = False,
    pii_ml_language: str = "en",
) -> Tuple[Dict[str, Any], List[Any], Dict[str, int], int, int]:
    """Stream a JSONL split into a metrics record.

    Phase 11.5 streamed this end-to-end: the function consumes
    :func:`_read_jsonl_split` row-by-row and folds each line straight
    into a :class:`_StreamingAggregator`. Memory is dominated by:

    * the per-row dedup signature list (``fingerprints`` for simhash, ~28 B/row;
      ``minhashes`` for MinHash, ~1-2 KB/row at ``num_perm=128``)
    * the per-row text-length list — same order of magnitude as fingerprints
    * a fixed-size language sample (200 strings)
    * a :class:`Counter` of distinct keysets (typically O(1) entries when
      schema is stable; only grows with genuine schema drift)

    Phase 12 adds the ``dedup_method`` switch (default ``"simhash"``;
    ``"minhash"`` opts into LSH-banded MinHash via the ``ingestion-scale``
    extra), the always-on secrets scan (no flag), and the opt-in quality
    filter (``enable_quality_filter``).

    Compared to the pre-Phase-11.5 buffered path that kept every parsed
    row + every text payload string in RAM (hundreds of MB on 100 K rows
    of 4 KB text), this is a large absolute reduction — but it is **not**
    constant memory, because the signature and length lists still grow
    linearly in row count. Operators that need true bounded RAM on
    truly huge splits should sample first.

    Returns:
        ``(info_dict, signatures, pii_counts, parse_errors, decode_errors)``
        — ``signatures`` is the per-row simhash int list when method is
        ``"simhash"`` and the per-row MinHash list when method is
        ``"minhash"``. Caller (:func:`_process_split`) feeds it back into
        the cross-split overlap path which dispatches on the same method.
    """
    agg = _StreamingAggregator(
        dedup_method=dedup_method,
        minhash_num_perm=minhash_num_perm,
        enable_quality_filter=enable_quality_filter,
        enable_pii_ml=enable_pii_ml,
        pii_ml_language=pii_ml_language,
    )
    for row, parse_err, decode_err in _read_jsonl_split(path):
        _ingest_row(agg, row, parse_err, decode_err)
        if agg.sample_count and agg.sample_count % _PROGRESS_INTERVAL == 0:
            logger.info(
                "audit/%s: %d rows scanned (streaming)…",
                split_name,
                agg.sample_count,
            )

    info = _aggregator_to_info(
        split_name,
        agg,
        near_dup_threshold=near_dup_threshold,
        minhash_jaccard=minhash_jaccard,
    )
    # Pass the aggregator's lists by reference instead of materialising a
    # copy. The downstream cross-split overlap (`_cross_split_pairs`)
    # already produces a new filtered list (`[m for m in sigs if m is not
    # None]`) before walking, so this signature list is never mutated by
    # consumers. The previous `list(agg.minhashes)` defensive copy doubled
    # peak memory on a 1M-row split (~1-2 KB per MinHash sketch x 1M x
    # 2 = ~2.5 GB resident before LSH even started).
    signatures: List[Any] = agg.minhashes if dedup_method == "minhash" else agg.fingerprints
    return info, signatures, dict(agg.pii_counts), agg.parse_errors, agg.decode_errors


__all__ = [
    "_StreamingAggregator",
    "_record_schema_for_dict",
    "_record_dedup_signature",
    "_record_dedup_sentinel",
    "_record_text_metrics",
    "_ingest_row",
    "_populate_schema_block",
    "_populate_optional_findings",
    "_within_split_pairs",
    "_aggregator_to_info",
    "_audit_split",
]
