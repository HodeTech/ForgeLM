"""End-to-end audit orchestrator + atomic JSON writer + cross-split overlap.

The hub: :func:`audit_dataset` reaches into nine other modules to
stitch together the per-split metrics, the cross-split leakage report,
the severity tiering, the optional Croissant card, and the final
:class:`AuditReport`. All other heavy lifting lives in dedicated
modules; this file is the orchestration glue.
"""

from __future__ import annotations

import logging
import multiprocessing
import os
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .._strict_json import dumps_strict
from ._croissant import _build_croissant_metadata

# ``_require_datasketch`` lives in ``_minhash.py`` but the orchestrator
# pre-flights via the same helper to fail fast before any rows are read.
from ._minhash import _count_leaked_rows_minhash_bidirectional, _require_datasketch
from ._pii_ml import _require_presidio
from ._simhash import _count_leaked_rows
from ._splits import _process_split, _resolve_input
from ._summary import (
    _build_near_duplicate_summary,
    _build_pii_severity,
    _build_quality_summary,
    _cross_split_leak_notes,
    _fold_outcome_into_summary,
    _pii_summary_notes,
    _quality_summary_notes,
    _secrets_summary_notes,
)
from ._types import (
    DEDUP_METHODS,
    DEFAULT_MINHASH_JACCARD,
    DEFAULT_MINHASH_NUM_PERM,
    DEFAULT_NEAR_DUP_HAMMING,
    AuditReport,
)

logger = logging.getLogger("forgelm.data_audit")


def _pair_leak_payload(
    a: str,
    sigs_a: List[Any],
    b: str,
    sigs_b: List[Any],
    *,
    dedup_method: str,
    threshold: int,
    minhash_jaccard: float,
    minhash_num_perm: int,
) -> Dict[str, Any]:
    """Both-directional leak counts + rates for one (a, b) split pair.

    Dispatches on ``dedup_method``: simhash uses Hamming-distance LSH;
    minhash uses Jaccard-similarity LSH (datasketch). Same shape on
    both paths so consumers can read ``leak_rate_<split>`` without
    knowing which method ran.
    """
    if dedup_method == "minhash":
        leaked_in_a, leaked_in_b = _count_leaked_rows_minhash_bidirectional(
            sigs_a, sigs_b, minhash_jaccard, num_perm=minhash_num_perm
        )
    else:
        leaked_in_a = _count_leaked_rows(sigs_a, sigs_b, threshold)
        leaked_in_b = _count_leaked_rows(sigs_b, sigs_a, threshold)
    return {
        f"leaked_rows_in_{a}": leaked_in_a,
        f"leak_rate_{a}": round(leaked_in_a / len(sigs_a), 4) if sigs_a else 0.0,
        f"leaked_rows_in_{b}": leaked_in_b,
        f"leak_rate_{b}": round(leaked_in_b / len(sigs_b), 4) if sigs_b else 0.0,
    }


def _cross_split_overlap(
    signatures_by_split: Dict[str, List[Any]],
    *,
    dedup_method: str,
    threshold: int,
    minhash_jaccard: float = DEFAULT_MINHASH_JACCARD,
    minhash_num_perm: int = DEFAULT_MINHASH_NUM_PERM,
) -> Dict[str, Any]:
    """Pairwise leakage report across train/validation/test splits.

    Reports leak rate **in both directions** — the symmetric ratio
    (shared / smaller-split-size) is the metric that actually destroys
    benchmark fidelity, but the asymmetric one (shared / larger split)
    is informative too. Without both, an operator scanning
    ``train__test = 0.05`` could miss that the same 5 rows leak 50% of
    a small test set.

    ``signatures_by_split`` carries simhash ints (when method is
    ``"simhash"``) or :class:`datasketch.MinHash` instances (method is
    ``"minhash"``). Sentinel filtering matches the per-method scheme:
    ``0`` for simhash, ``None`` for minhash.
    """
    if dedup_method == "minhash":
        nonempty = {name: [m for m in sigs if m is not None] for name, sigs in signatures_by_split.items()}
        report: Dict[str, Any] = {
            "method": "minhash",
            "jaccard_threshold": minhash_jaccard,
            "num_perm": minhash_num_perm,
            "pairs": {},
        }
    else:
        nonempty = {name: [fp for fp in sigs if fp != 0] for name, sigs in signatures_by_split.items()}
        report = {"method": "simhash", "hamming_threshold": threshold, "pairs": {}}

    splits = list(nonempty.keys())
    for i, a in enumerate(splits):
        sigs_a = nonempty[a]
        if not sigs_a:
            continue
        for j in range(i + 1, len(splits)):
            b = splits[j]
            sigs_b = nonempty[b]
            if not sigs_b:
                continue
            report["pairs"][f"{a}__{b}"] = _pair_leak_payload(
                a,
                sigs_a,
                b,
                sigs_b,
                dedup_method=dedup_method,
                threshold=threshold,
                minhash_jaccard=minhash_jaccard,
                minhash_num_perm=minhash_num_perm,
            )
    return report


def _atomic_write_json(target: Path, payload: Dict[str, Any]) -> None:
    """Write ``payload`` to ``target`` via tempfile + atomic rename.

    Phase 11.5 hardening: previously the audit report was written with a
    plain ``open()`` + ``json.dump``. A crash mid-write left a half-baked
    file at the canonical path, so the next pipeline step would either
    parse garbage or trip the "missing report" branch silently. Routing
    through :class:`tempfile.NamedTemporaryFile` in the same directory and
    then ``os.replace`` keeps the canonical path either fully present
    (post-crash readers see the previous good report) or atomically
    swapped with a complete one. ``newline="\\n"`` pins LF terminators
    so the file is byte-identical across Windows/Linux/macOS — important
    because the EU AI Act governance bundle inlines this exact JSON.
    """
    tmp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=str(target.parent),
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as fh:
            tmp_path = Path(fh.name)
            fh.write(dumps_strict(payload, indent=2, ensure_ascii=False))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, target)
        # Wave 2b final-followup F-21-04: also fsync the parent
        # directory so the rename's directory entry is on disk on
        # non-journaled FS.  Mirrors the discipline in
        # ``forgelm/cli/subcommands/_purge.py::_atomic_rewrite_dropping_lines``.
        # ``O_DIRECTORY`` not on Windows; trap and continue.
        try:
            dir_fd = os.open(str(target.parent), os.O_DIRECTORY)
        except (AttributeError, OSError):  # pragma: no cover — Windows / unusual FS
            pass
        else:
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        tmp_path = None
        logger.info("Wrote audit report: %s", target)
    finally:
        if tmp_path is not None and tmp_path.exists():
            # Best-effort cleanup so a half-written .tmp doesn't litter the
            # output dir if os.replace itself raised (rare — same FS).
            try:
                tmp_path.unlink()
            except OSError:  # pragma: no cover — defensive
                pass


def _process_split_for_pool(
    args_tuple: Tuple[str, Path, Dict[str, Any]],
):
    """Module-level wrapper: dispatches to :func:`_process_split` from a tuple.

    ``multiprocessing.Pool.map`` requires the worker callable + its arguments
    to be picklable.  Lambdas and ``functools.partial`` over keyword-only
    args don't pickle cleanly across spawn-method workers, so we accept a
    single positional tuple and unpack the kwargs inside the worker.

    **Test injection point** (Wave 2a Round-5 F-R5-03): spawn-method
    workers do not see monkeypatches applied in the parent test process,
    so the only way to exercise the orchestrator's ``pool.map`` re-raise
    branch is via an env var that the worker reads on entry.  Setting
    ``FORGELM_AUDIT_TEST_WORKER_RAISES=<split_name>`` causes this
    wrapper to raise ``RuntimeError`` for the matching split — used by
    ``tests/test_data_audit_workers.py::TestWorkersErrorPropagation``
    to verify that an uncaught worker exception surfaces through
    ``Pool.map`` instead of being silently swallowed.  Production code
    should never set this variable; the check is fail-closed (env var
    must equal the exact split name being processed) to minimise blast
    radius if an operator sets it by mistake.
    """
    split_name, path, kwargs = args_tuple
    test_raise_split = os.environ.get("FORGELM_AUDIT_TEST_WORKER_RAISES")
    if test_raise_split and test_raise_split == split_name:
        raise RuntimeError(f"synthetic test-injected worker failure for split {split_name!r}")
    return _process_split(split_name, path, **kwargs)


def audit_dataset(  # NOSONAR — cognitive complexity is inherent to the audit orchestration logic; extraction would fragment cohesive pipeline steps
    source: str,
    *,
    output_dir: Optional[str] = None,
    near_dup_threshold: int = DEFAULT_NEAR_DUP_HAMMING,
    dedup_method: str = "simhash",
    minhash_jaccard: float = DEFAULT_MINHASH_JACCARD,
    minhash_num_perm: int = DEFAULT_MINHASH_NUM_PERM,
    enable_quality_filter: bool = False,
    enable_pii_ml: bool = False,
    pii_ml_language: str = "en",
    emit_croissant: bool = False,
    workers: int = 1,
) -> AuditReport:
    """Run the audit pipeline over a JSONL file or split-keyed directory.

    Args:
        source: Path to a ``.jsonl`` file (single split) or a directory
            containing ``train.jsonl`` / ``validation.jsonl`` / ``test.jsonl``.
        output_dir: When set, writes ``data_audit_report.json`` under this
            directory (created if missing). Returned :class:`AuditReport`
            is identical either way.
        near_dup_threshold: Hamming distance cutoff for the simhash-based
            near-duplicate detector. Default 3 (~95% similarity). Ignored
            when ``dedup_method="minhash"``.
        dedup_method: Phase 12 — ``"simhash"`` (default; exact recall via
            LSH banding) or ``"minhash"`` (datasketch MinHash LSH; the
            industry standard above ~50K rows). MinHash requires the
            optional ``[ingestion-scale]`` extra.
        minhash_jaccard: Jaccard-similarity threshold for the MinHash
            method. Default ``0.85`` ~ simhash's ``threshold=3`` in
            similarity terms.
        minhash_num_perm: Number of permutations for ``datasketch.MinHash``.
            Default ``128`` matches datasketch's own default.
        enable_pii_ml: Phase 12.5 opt-in — layer Presidio's ML-NER PII
            detector (``person`` / ``organization`` / ``location``) on
            top of the regex detector. Requires the optional
            ``[ingestion-pii-ml]`` extra AND a spaCy NER model
            (``python -m spacy download en_core_web_lg``); raises
            ``ImportError`` with the install hint when either is
            missing so the failure surfaces before any rows are scanned.
        pii_ml_language: Phase 12.5 — language code passed to Presidio's
            NLP engine. Default ``"en"``. Set to ``"tr"`` (or whichever
            spaCy model the operator has loaded) for non-English
            corpora; Presidio raises a typed exception when no engine
            is registered for the requested language.
        emit_croissant: Phase 12.5 opt-in flag — when ``True``, populate
            the report's ``croissant`` field with a Google Croissant 1.0
            dataset card (``@type: sc:Dataset``) so the same JSON file
            doubles as both the EU AI Act Article 10 governance artifact
            and a Croissant-consumer dataset card. Off by default — older
            consumers see byte-identical output until they opt in.
        enable_quality_filter: Phase 12 opt-in flag — when ``True``, run
            the heuristic quality checks (Gopher / C4 / RefinedWeb-style)
            and surface findings under ``quality_summary``.
        workers: Phase 17 — number of worker processes used for the
            split-level pipeline.  ``1`` (default) keeps the pre-Phase-17
            single-process behaviour exactly.  ``> 1`` distributes the
            per-split processing over ``min(workers, len(splits))``
            ``multiprocessing.Pool`` workers; the merge step that builds
            the final report stays single-threaded so the determinism
            contract (byte-identical JSON across worker counts) is
            preserved.  Speed-up scales with the number of *splits*, not
            row count — single-split corpora ignore values >1.

    Returns:
        :class:`AuditReport`. JSON-serialize via ``asdict(report)``.
    """
    if not isinstance(near_dup_threshold, int) or isinstance(near_dup_threshold, bool) or near_dup_threshold < 0:
        raise ValueError(f"near_dup_threshold must be a non-negative integer; got {near_dup_threshold!r}.")
    if dedup_method not in DEDUP_METHODS:
        raise ValueError(f"dedup_method must be one of {DEDUP_METHODS}; got {dedup_method!r}.")
    if dedup_method == "minhash":
        if not isinstance(minhash_jaccard, (int, float)) or isinstance(minhash_jaccard, bool):
            raise ValueError(f"minhash_jaccard must be a float in [0.0, 1.0]; got {minhash_jaccard!r}.")
        if not 0.0 <= float(minhash_jaccard) <= 1.0:
            raise ValueError(f"minhash_jaccard must be in [0.0, 1.0]; got {minhash_jaccard!r}.")
        if not isinstance(minhash_num_perm, int) or isinstance(minhash_num_perm, bool) or minhash_num_perm <= 0:
            raise ValueError(f"minhash_num_perm must be a positive integer; got {minhash_num_perm!r}.")
        _require_datasketch()
    if enable_pii_ml:
        # Phase 12.5: pre-flight the optional extra AND the requested
        # language so the caller learns the dep / model / language is
        # missing BEFORE we open any files / scan any rows, mirroring
        # the ``_require_datasketch`` shape above. Without the language
        # check, ``--pii-ml-language tr`` against a default Presidio
        # install would silently return ``{}`` per row (analyzer raises
        # ``ValueError`` which ``detect_pii_ml`` deliberately swallows
        # for per-row resilience on pathological strings).
        _require_presidio(language=pii_ml_language)

    if not isinstance(workers, int) or isinstance(workers, bool) or workers < 1:
        raise ValueError(f"workers must be a positive integer; got {workers!r}.")

    splits_paths, resolution_notes = _resolve_input(source)

    splits_info: Dict[str, Dict[str, Any]] = {}
    signatures_by_split: Dict[str, List[Any]] = {}
    pii_summary: Dict[str, int] = {}
    secrets_summary: Dict[str, int] = {}
    quality_aggregate: Dict[str, int] = {}
    quality_samples_flagged_total = 0
    quality_samples_evaluated_total = 0
    total_samples = 0
    near_dup_pairs: Dict[str, int] = {}
    notes: List[str] = list(resolution_notes)

    parse_errors_total = 0
    decode_errors_total = 0

    # Determinism contract: regardless of ``workers``, every aggregate is
    # built up by walking the splits in the exact order ``splits_paths``
    # yields them.  Multiprocessing only changes how the per-split outcomes
    # are *computed*; the merge step below is single-threaded so byte-
    # equivalent output is guaranteed across worker counts.  See the
    # ``test_data_audit_workers.py`` regression suite for the test that
    # pins this invariant.
    split_kwargs: Dict[str, Any] = {
        "near_dup_threshold": near_dup_threshold,
        "dedup_method": dedup_method,
        "minhash_jaccard": minhash_jaccard,
        "minhash_num_perm": minhash_num_perm,
        "enable_quality_filter": enable_quality_filter,
        "enable_pii_ml": enable_pii_ml,
        "pii_ml_language": pii_ml_language,
    }
    effective_workers = min(workers, len(splits_paths)) if splits_paths else 1
    if workers > effective_workers:
        # Operator asked for more parallelism than we can use; surface
        # the clamp so the wall-clock-vs-expected gap doesn't surprise
        # them (Wave 2a Round-2 review F-17-01).
        logger.info(
            "audit: requested workers=%d but only %d split(s) found; running %d worker(s)",
            workers,
            len(splits_paths) if splits_paths else 0,
            effective_workers,
        )
    if effective_workers > 1:
        # Pool.map preserves input order so ``outcomes`` lines up with
        # ``splits_paths.items()`` 1:1.  We bound the pool to
        # ``len(splits_paths)`` — extra workers would idle since the unit
        # of work is one split.
        #
        # Wave 2a Round-1 review (F-26-03): pin the start method to
        # ``"spawn"`` so the determinism contract is platform-independent.
        # ``Pool()`` defaults to fork on Linux + spawn on macOS / Windows;
        # Linux CI never exercises the spawn path otherwise.  Spawn also
        # gives every worker a fresh interpreter (no shared
        # langdetect.DetectorFactory state, no copied open file handles)
        # which is exactly what the byte-identical contract requires.
        logger.info(
            "audit: spawning %d worker process(es) over %d split(s)",
            effective_workers,
            len(splits_paths),
        )
        ctx = multiprocessing.get_context("spawn")
        pool_args = [(name, path, split_kwargs) for name, path in splits_paths.items()]
        with ctx.Pool(effective_workers) as pool:
            try:
                outcomes_list = pool.map(_process_split_for_pool, pool_args)
            except Exception:  # noqa: BLE001 — best-effort: re-raise unchanged so the operator sees the worker traceback through Pool.map's wrapping; we log at ERROR first so the contextual "which split failed" hint lands even when the traceback is opaque.
                logger.error(
                    "audit: parallel worker raised inside the pool; the original "
                    "traceback is wrapped — re-run with --workers 1 to surface it "
                    "with full context."
                )
                raise
        outcomes_iter = zip(splits_paths.keys(), outcomes_list)
    else:
        logger.info("audit: sequential mode (workers=%d, splits=%d)", workers, len(splits_paths))
        outcomes_iter = (
            (split_name, _process_split(split_name, path, **split_kwargs)) for split_name, path in splits_paths.items()
        )

    for split_name, outcome in outcomes_iter:
        splits_info[split_name] = outcome.info
        signatures_by_split[split_name] = outcome.signatures
        total_samples += outcome.row_count
        near_dup_pairs[split_name] = outcome.info.get("near_duplicate_pairs", 0)
        notes.extend(outcome.split_notes)
        parse_errors_total += outcome.parse_errors
        decode_errors_total += outcome.decode_errors
        flagged, evaluated = _fold_outcome_into_summary(
            outcome,
            pii_summary=pii_summary,
            secrets_summary=secrets_summary,
            quality_aggregate=quality_aggregate,
            enable_quality_filter=enable_quality_filter,
        )
        quality_samples_flagged_total += flagged
        quality_samples_evaluated_total += evaluated

    cross = _cross_split_overlap(
        signatures_by_split,
        dedup_method=dedup_method,
        threshold=near_dup_threshold,
        minhash_jaccard=minhash_jaccard,
        minhash_num_perm=minhash_num_perm,
    )
    pii_severity = _build_pii_severity(pii_summary)
    quality_summary = _build_quality_summary(
        enable_quality_filter=enable_quality_filter,
        samples_flagged_total=quality_samples_flagged_total,
        quality_aggregate=quality_aggregate,
        samples_evaluated_total=quality_samples_evaluated_total,
    )

    notes.extend(_pii_summary_notes(pii_summary, pii_severity))
    notes.extend(_secrets_summary_notes(secrets_summary))
    notes.extend(_quality_summary_notes(quality_summary))
    notes.extend(_cross_split_leak_notes(cross))

    near_dup_total = sum(near_dup_pairs.values())
    if near_dup_total > 0:
        notes.append(
            f"{near_dup_total} near-duplicate pair(s) found within splits. "
            "Inspect; identical chunks waste training compute and can overweight specific phrasing."
        )

    if parse_errors_total or decode_errors_total:
        notes.append(
            f"Data integrity: {parse_errors_total} parse error(s) + "
            f"{decode_errors_total} decode error(s) across all splits. "
            "These rows did NOT contribute to per-split metrics — re-emit the "
            "JSONL after fixing or accept the parseable subset as audited."
        )

    near_duplicate_summary = _build_near_duplicate_summary(
        dedup_method=dedup_method,
        near_dup_pairs=near_dup_pairs,
        near_dup_threshold=near_dup_threshold,
        minhash_jaccard=minhash_jaccard,
        minhash_num_perm=minhash_num_perm,
    )

    generated_at = datetime.now(timezone.utc).isoformat()
    source_path_abs = os.fspath(Path(source).expanduser().resolve())
    croissant: Dict[str, Any] = {}
    if emit_croissant:
        croissant = _build_croissant_metadata(
            source_path=source_path_abs,
            source_input=source,
            generated_at=generated_at,
            total_samples=total_samples,
            splits_info=splits_info,
            splits_paths=splits_paths,
        )

    report = AuditReport(
        generated_at=generated_at,
        source_path=source_path_abs,
        source_input=source,
        total_samples=total_samples,
        splits=splits_info,
        cross_split_overlap=cross,
        pii_summary=pii_summary,
        pii_severity=pii_severity,
        near_duplicate_summary=near_duplicate_summary,
        secrets_summary=secrets_summary,
        quality_summary=quality_summary,
        croissant=croissant,
        notes=notes,
    )

    if output_dir:
        out_dir = Path(output_dir).expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(out_dir / "data_audit_report.json", asdict(report))

    return report


__all__ = [
    "_pair_leak_payload",
    "_cross_split_overlap",
    "_atomic_write_json",
    "audit_dataset",
]
