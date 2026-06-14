"""Split discovery (canonical names + aliases) and per-split processing.

The audit accepts either a single ``.jsonl`` (treated as ``train``) or a
directory whose canonical / aliased filenames map to the standard
``train`` / ``validation`` / ``test`` partition. When canonical names
aren't found we fall back to a pseudo-split layout (one split per
``*.jsonl`` file) and warn loudly that cross-split leakage is meaningless
in that mode.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple

from ._aggregator import _audit_split
from ._types import DEFAULT_MINHASH_JACCARD, DEFAULT_MINHASH_NUM_PERM

logger = logging.getLogger("forgelm.data_audit")


# Common synonyms for the canonical split names. Folded onto canonical at
# load time so leakage analysis treats e.g. ``dev.jsonl`` and
# ``validation.jsonl`` as the same split semantically. Alias preference is
# intentional: a directory containing both ``validation.jsonl`` and
# ``dev.jsonl`` should warn (loud) rather than silently merge.
_SPLIT_ALIASES: Dict[str, str] = {
    "train": "train",
    "validation": "validation",
    "valid": "validation",
    "val": "validation",
    "dev": "validation",
    "test": "test",
    "eval": "test",
    "holdout": "test",
}


def _scan_canonical_split_files(src: Path) -> Tuple[Dict[str, Path], List[str]]:
    """Discover ``train`` / ``validation`` / ``test`` files via canonical names + aliases."""
    layouts: Dict[str, Path] = {}
    notes: List[str] = []
    for stem, canonical in _SPLIT_ALIASES.items():
        candidate = src / f"{stem}.jsonl"
        if not candidate.is_file():
            continue
        if canonical in layouts:
            msg = (
                f"both '{layouts[canonical].name}' and '{candidate.name}' map to "
                f"the '{canonical}' split; using the first one. Rename to disambiguate."
            )
            notes.append(msg)
            # F-P6-OPUS-12/16: surface the collision LOUDLY (not just in the
            # report JSON), mirroring the pseudo-split branch below — one of
            # the two files is silently dropped from the leakage analysis.
            logger.warning(msg)
            continue
        if stem != canonical:
            notes.append(f"'{candidate.name}' treated as the '{canonical}' split.")
        layouts[canonical] = candidate
    return layouts, notes


def _scan_pseudo_split_files(src: Path) -> Tuple[Dict[str, Path], List[str]]:
    """Last-resort fallback: every ``*.jsonl`` becomes its own pseudo-split.

    Cross-split leakage analysis on pseudo-splits is meaningless (those
    files probably aren't a real train/test partition), so warn loudly.
    """
    layouts: Dict[str, Path] = {}
    notes: List[str] = []
    for jsonl in sorted(src.glob("*.jsonl")):
        layouts[jsonl.stem] = jsonl
    if layouts:
        msg = (
            f"no canonical split files found in '{src}'. "
            "Each .jsonl is being audited as its own pseudo-split — "
            "cross-split leakage analysis is meaningless without a real partition."
        )
        notes.append(msg)
        logger.warning(msg)
    return layouts, notes


def _resolve_directory_splits(src: Path) -> Tuple[Dict[str, Path], List[str]]:
    """Find a usable split layout under ``src`` (canonical first, pseudo as fallback)."""
    layouts, notes = _scan_canonical_split_files(src)
    if layouts:
        return layouts, notes
    return _scan_pseudo_split_files(src)


def _resolve_input(source: str) -> Tuple[Dict[str, Path], List[str]]:
    """Map the user-supplied path to a ``{split_name: path}`` dict + notes.

    Two layouts are supported:
    * Single ``.jsonl`` file -> treated as the ``train`` split.
    * Directory with files matching canonical names (``train.jsonl`` /
      ``validation.jsonl`` / ``test.jsonl``) or common aliases (``dev`` /
      ``val`` / ``valid`` / ``eval`` / ``holdout``).
    """
    src = Path(source).expanduser().resolve()
    if src.is_file():
        return {"train": src}, []
    if src.is_dir():
        layouts, notes = _resolve_directory_splits(src)
        if layouts:
            return layouts, notes
    raise FileNotFoundError(
        f"Audit input not found or empty: '{src}'. "
        f"Pass a .jsonl file or a directory containing train.jsonl / validation.jsonl / test.jsonl."
    )


# ---------------------------------------------------------------------------
# Per-split processing — extracted from audit_dataset for readability and
# testability. Each helper owns one concern; the orchestrator stitches them.
# ---------------------------------------------------------------------------


@dataclass
class _SplitOutcome:
    """Bundle of per-split results assembled by :func:`_process_split`.

    ``signatures`` carries simhash ints for the default method and
    :class:`datasketch.MinHash` instances for the MinHash method —
    field name renamed from ``fingerprints`` in Phase 12 so the
    method-agnostic role is obvious to readers.
    """

    info: Dict[str, Any]
    signatures: List[Any]
    pii_split: Dict[str, int]
    row_count: int
    parse_errors: int
    decode_errors: int
    split_notes: List[str]


def _process_split(
    split_name: str,
    path: Path,
    *,
    near_dup_threshold: int,
    dedup_method: str = "simhash",
    minhash_jaccard: float = DEFAULT_MINHASH_JACCARD,
    minhash_num_perm: int = DEFAULT_MINHASH_NUM_PERM,
    enable_quality_filter: bool = False,
    enable_pii_ml: bool = False,
    pii_ml_language: str = "en",
) -> _SplitOutcome:
    """Stream + audit one split. Tolerates per-split filesystem failures.

    The streaming :func:`_audit_split` opens ``path`` lazily inside
    :func:`_read_jsonl_split`, so an ``OSError`` (permission denied,
    ENOSPC, IsADirectoryError, ...) bubbles up here and is converted into
    a structured per-split error rather than aborting the whole audit.
    Other splits in the same directory continue uninterrupted.
    """
    logger.info("audit: scanning split '%s' (%s)", split_name, path.name)
    try:
        info, signatures, pii_split, parse_errors, decode_errors = _audit_split(
            split_name,
            path,
            near_dup_threshold=near_dup_threshold,
            dedup_method=dedup_method,
            minhash_jaccard=minhash_jaccard,
            minhash_num_perm=minhash_num_perm,
            enable_quality_filter=enable_quality_filter,
            enable_pii_ml=enable_pii_ml,
            pii_ml_language=pii_ml_language,
        )
    except OSError as exc:
        logger.warning("Could not read split '%s' (%s): %s — skipping.", split_name, path, exc)
        return _SplitOutcome(
            info={"error": f"read_failed: {exc}", "path": str(path)},
            signatures=[],
            pii_split={},
            row_count=0,
            parse_errors=0,
            decode_errors=0,
            split_notes=[f"split '{split_name}' skipped (read failure: {exc})"],
        )

    split_notes: List[str] = []
    # Surface JSONL hygiene metrics on the split itself so the report
    # distinguishes "this split has 1240 rows" from "this split had 1330
    # lines but 90 were malformed JSON we silently dropped".
    if parse_errors:
        info["parse_errors"] = parse_errors
        split_notes.append(
            f"split '{split_name}': {parse_errors} malformed JSONL line(s) "
            "skipped — metrics computed over the parseable subset only."
        )
    if decode_errors:
        info["decode_errors"] = decode_errors
        split_notes.append(
            f"split '{split_name}': {decode_errors} line(s) had non-UTF-8 "
            "bytes (replaced with U+FFFD). Re-encode the source file as "
            "UTF-8 if these rows matter."
        )

    return _SplitOutcome(
        info=info,
        signatures=signatures,
        pii_split=pii_split,
        row_count=info.get("sample_count", 0),
        parse_errors=parse_errors,
        decode_errors=decode_errors,
        split_notes=split_notes,
    )


__all__ = [
    "_SPLIT_ALIASES",
    "_scan_canonical_split_files",
    "_scan_pseudo_split_files",
    "_resolve_directory_splits",
    "_resolve_input",
    "_SplitOutcome",
    "_process_split",
]
