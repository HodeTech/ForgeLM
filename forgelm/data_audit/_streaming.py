"""Streaming helpers — JSONL reader, length digest, language detection.

Phase 11.5 promoted the JSONL reader from a buffered tuple to a generator
so the audit pipeline can process one line at a time. Memory on a 100 K-row
split drops from O(n) raw rows + O(n) text payloads (~hundreds of MB) to a
handful of metric aggregators plus the ``n``-element fingerprint list (8 B/row).

The :class:`_LengthDigest` reservoir is the bounded-memory replacement for
the previous unbounded length list: exact min/max/mean, approximate
p50/p95 above 100 K rows.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Iterator, List, Tuple

from ._types import _INSTRUCTION_PAIRS, _TEXT_COLUMNS

logger = logging.getLogger("forgelm.data_audit")


def _detect_language(text: str) -> str:
    if not text or len(text) < 20:
        return "unknown"
    try:
        from langdetect import DetectorFactory, detect
        from langdetect.lang_detect_exception import LangDetectException

        DetectorFactory.seed = 0
        try:
            return detect(text)
        except LangDetectException:
            # langdetect raises this when no features can be extracted (e.g.,
            # short non-alphabetic strings, pure-symbol payloads). Treat as
            # "unknown" rather than crashing the audit.
            return "unknown"
    except ImportError:
        return "unknown"


# ---------------------------------------------------------------------------
# Streaming length digest — bounded memory for large corpora (C.2)
# ---------------------------------------------------------------------------

# Reservoir size: below this the digest is exact; above it p50/p95 are
# approximate via Algorithm R random sampling. 100K ints ~ 800 KB — a
# negligible fraction of peak audit memory even on multi-million-row splits.
_LENGTH_RESERVOIR_SIZE = 100_000


class _LengthDigest:
    """Streaming min/max/mean/p50/p95 accumulator with bounded memory.

    Replaces the ``text_lengths: List[int]`` field on ``_StreamingAggregator``,
    which grew O(n) — 80 MB+ per split on 10 M-row corpora.  This keeps
    memory capped at ``_LENGTH_RESERVOIR_SIZE`` integers (~800 KB) regardless
    of dataset size; p50/p95 are exact up to that cap, approximate beyond it.
    """

    __slots__ = ("_n", "_total", "_min", "_max", "_reservoir", "_rng_counter")

    def __init__(self) -> None:
        self._n: int = 0
        self._total: int = 0
        self._min: int = 0
        self._max: int = 0
        self._reservoir: List[int] = []
        # Inline LCG counter for reservoir sampling, seeded at the LCG
        # multiplier — avoids importing random and keeps the digest
        # deterministic across runs and worker counts (the byte-identical
        # contract). The constant seed is intentional; there is no
        # external-seeding path (the slot is only advanced internally in
        # ``update``). Seeding at the multiplier (rather than 0) avoids a
        # first-element deterministic replacement: with seed=0 the first LCG
        # advance yields 1, so j=1%n is always < reservoir size and slot 1 is
        # always overwritten on the (reservoir_size+1)th element.
        self._rng_counter: int = 6364136223846793005

    def update(self, length: int) -> None:
        self._n += 1
        self._total += length
        if self._n == 1:
            self._min = self._max = length
        else:
            if length < self._min:
                self._min = length
            if length > self._max:
                self._max = length
        if len(self._reservoir) < _LENGTH_RESERVOIR_SIZE:
            self._reservoir.append(length)
        else:
            # Algorithm R: replace a random slot with decreasing probability
            # Use a simple LCG for speed and to avoid global random state.
            self._rng_counter = (self._rng_counter * 6364136223846793005 + 1) & 0xFFFFFFFFFFFFFFFF
            j = self._rng_counter % self._n
            if j < _LENGTH_RESERVOIR_SIZE:
                self._reservoir[j] = length

    def stats(self) -> Dict[str, float]:
        if self._n == 0:
            return {}
        s = sorted(self._reservoir)
        k = len(s)
        return {
            "min": self._min,
            "max": self._max,
            "mean": round(self._total / self._n, 1),
            "p50": s[k // 2],
            "p95": s[min(k - 1, int(k * 0.95))],
        }


def _extract_text_payload(row: Dict[str, Any]) -> str:
    """Pick the most plausible text column from a row for stats / dedup.

    Recognises the instruction-tuning / chat shapes the rest of the codebase
    emits so synthetic ``instruction`` / ``chatml`` / ``prompt_response``
    output is scanned for PII/secrets/quality instead of silently extracting
    to ``""`` (F-P6-OPUS-07). A recognised ``(user_half, assistant_half)``
    pair takes priority over a bare single column so the assistant/response
    half — the text most likely to carry memorised PII — is never dropped.
    """
    # Instruction-tuning pairs first: join both halves so the response/output
    # is scanned. Only fires when BOTH keys carry non-empty string content.
    for user_key, asst_key in _INSTRUCTION_PAIRS:
        user_val = row.get(user_key)
        asst_val = row.get(asst_key)
        if isinstance(user_val, str) and isinstance(asst_val, str):
            halves = [v for v in (user_val, asst_val) if v.strip()]
            if halves:
                return "\n".join(halves)
    for col in _TEXT_COLUMNS:
        val = row.get(col)
        if isinstance(val, str) and val.strip():
            return val
    # ``messages`` / chat schemas: concatenate role-tagged content.
    msgs = row.get("messages")
    if isinstance(msgs, list):
        parts = []
        for m in msgs:
            if isinstance(m, dict) and isinstance(m.get("content"), str):
                parts.append(m["content"])
        if parts:
            return "\n".join(parts)
    # Half-present instruction pairs, last resort: a row carrying only ONE
    # half of a recognised pair (partial export, interrupted synthetic
    # generation leaving ``output`` unset, a prompt-only corpus) would
    # otherwise extract to ``""`` — silently counted as ``null_or_empty`` and
    # never scanned for PII/secrets/quality, exactly the shape most likely to
    # carry unreviewed PII. ``instruction`` / ``output`` / ``User`` /
    # ``Assistant`` / ``response`` have no entry in ``_TEXT_COLUMNS``, so scan
    # them in isolation here rather than dropping the row. Kept AFTER the
    # canonical single-column fallback so a row that also has a
    # ``text``/``content``/``completion`` column still prefers that column.
    for user_key, asst_key in _INSTRUCTION_PAIRS:
        for key in (user_key, asst_key):
            val = row.get(key)
            if isinstance(val, str) and val.strip():
                return val
    return ""


def _read_jsonl_split(path: Path) -> Iterator[Tuple[Any, bool, bool]]:
    """Streaming JSONL reader. Yields ``(row, parse_error, decode_error)``.

    Phase 11.5 promoted this from a buffered ``(rows, parse_errors,
    decode_errors)`` tuple to a generator so the audit pipeline can process
    one line at a time. RAM use on a 100 K-row split drops from O(n) raw
    rows + O(n) text payloads (~hundreds of MB) to a handful of metric
    aggregators plus the ``n``-element fingerprint list (8 bytes/row).

    Per-line semantics are unchanged:

    * UTF-8 decode is permissive (``errors="replace"``) — a single mojibake
      line never aborts the whole audit. ``decode_error=True`` is reported
      only when a *strict* decode of the same raw bytes would have raised,
      so a corpus that legitimately contains literal U+FFFD characters
      (valid UTF-8) is **not** falsely flagged.
    * ``json.JSONDecodeError`` is caught per line; the offending line is
      surfaced as ``(None, parse_error=True, decode_error=...)`` so
      downstream aggregators can count it without the row poisoning the
      schema / payload pipelines.
    * Yielded rows may be non-dict JSON (lists, scalars); downstream
      :func:`_extract_text_payload` and :func:`_audit_split` guard
      ``isinstance(row, dict)``.

    ``OSError`` from the initial ``open()`` is propagated to the caller —
    that is the expected signal for "this split is unreachable / unreadable".
    """
    # Read bytes so we can distinguish a genuine non-UTF-8 byte (which a
    # strict decode would reject) from a line that legitimately *contains*
    # the U+FFFD code point (valid UTF-8, must not be flagged). Decode
    # strictly first — success means the text is exactly right and the line
    # carries no decode error; only on ``UnicodeDecodeError`` do we fall back
    # to ``errors="replace"`` for usable text and flag the line. One decode
    # per line in the common (strict-success) hot path.
    with open(path, "rb") as fh:
        for line_number, raw_bytes in enumerate(fh, start=1):
            try:
                line = raw_bytes.decode("utf-8")
                decode_error = False
            except UnicodeDecodeError:
                line = raw_bytes.decode("utf-8", errors="replace")
                decode_error = True
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                logger.warning("Skipping malformed JSONL line %d in %s: %s", line_number, path, exc)
                yield None, True, decode_error
                continue
            yield row, False, decode_error


_PROGRESS_INTERVAL: int = 5000
"""Emit a progress log every N rows when a split is large enough that the
audit's silent stretch is over a few seconds. Threshold picked so smoke
tests / quickstart audits stay quiet but real corpora surface signal."""


_LANG_SAMPLE_SIZE: int = 200
"""How many text-bearing payloads we sample for language detection. Bounded
so a 100 K-row corpus does not pay 100 K langdetect calls. The sample is a
uniform reservoir (Algorithm R, see ``_record_text_metrics``), not a
head-of-stream take, so the top-3 distribution is representative even when
the corpus is a concatenation of per-language source blocks rather than an
i.i.d.-shuffled stream."""


def _compute_top_languages(sample: List[str]) -> List[Dict[str, Any]]:
    """Top-3 languages over the ``sample`` list. Empty ``sample`` -> empty list."""
    counts: Dict[str, int] = {}
    for text in sample:
        lang = _detect_language(text)
        if lang:
            counts[lang] = counts.get(lang, 0) + 1
    if not counts:
        return []
    top3 = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:3]
    return [{"code": code, "count": n} for code, n in top3]


__all__ = [
    "_detect_language",
    "_LENGTH_RESERVOIR_SIZE",
    "_LengthDigest",
    "_extract_text_payload",
    "_read_jsonl_split",
    "_PROGRESS_INTERVAL",
    "_LANG_SAMPLE_SIZE",
    "_compute_top_languages",
]
