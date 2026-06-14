"""Simhash core, LSH-banded near-duplicate finder, and cross-split leak counts.

Pure-Python by default; falls through to numpy-vectorised bit unpacking
when numpy is installed (``forgelm.data_audit._optional._HAS_NUMPY``).
The token digest swaps to xxhash's xxh3_64 when available
(``_HAS_XXHASH``); otherwise BLAKE2b is the cross-platform fallback so a
bare install still produces reproducible fingerprints.
"""

from __future__ import annotations

import hashlib
import math
import re
from functools import lru_cache
from typing import Dict, List, Tuple

from . import _optional
from ._types import DEFAULT_NEAR_DUP_HAMMING

# ---------------------------------------------------------------------------
# Simhash + near-duplicate detection
# ---------------------------------------------------------------------------


# Tokenizer for simhash / MinHash dedup. ``\w+`` deliberately matches
# alphanumerics + underscore (Unicode word chars under ``re.UNICODE``)
# rather than the language-aware ``\b[\w']+\b`` pattern from
# ``docs/standards/regex.md`` §1: dedup operates on byte-level token
# overlap, not natural-language words. Underscore-bearing identifiers
# (``__init__``, ``snake_case``) sharing a token *is* the desired
# behaviour for code/text near-duplicate detection — replacing them
# with whitespace as a separator inflates the false-positive rate of
# the simhash/MinHash signature.
_TOKEN_PATTERN = re.compile(r"\w+", re.UNICODE)


def _tokenize(text: str) -> List[str]:
    return [tok.lower() for tok in _TOKEN_PATTERN.findall(text or "")]


# Phase 11.5: per-token digest cache. Token frequency follows Zipf's law —
# the top few thousand tokens (the / and / common stop-words / domain
# vocabulary) dominate hits across an entire corpus. Memoising the digest
# at this granularity collapses millions of hashes into ten thousand on a
# typical run. lru_cache is process-wide and bounded; ``maxsize=10_000``
# trades ~1-2 MB of cache for the throughput win.
#
# ``lru_cache`` keys on the literal call signature, so ``_token_digest('x')``
# and ``_token_digest('x', 64)`` would occupy two distinct entries for the same
# value. Both production callers (``compute_simhash``, ``_compute_simhash_numpy``)
# always pass ``bits`` positionally, so the bits-defaulted shape is never on the
# hot path and the cache does not split in practice — the default is kept only
# for ergonomics of ad-hoc / test callers.
@lru_cache(maxsize=10_000)
def _token_digest(token: str, bits: int = 64) -> int:
    """Hash one tokenised word into a ``bits``-wide integer.

    Backend selection (decided at import time):

    * **xxhash.xxh3_64** when the optional ``xxhash`` dependency is
      installed and ``bits == 64`` — non-cryptographic, marginally
      faster on Python (~1.3x raw, ~1.05x end-to-end after the
      ``lru_cache`` below absorbs Zipfian token repeats). Primary
      reason to keep it is forward-compatibility with other simhash
      implementations, not raw throughput.
    * **hashlib.blake2b** otherwise (and for non-64-bit widths, e.g. tests
      that pin a smaller fingerprint). BLAKE2b is on the modern allowlist
      and supports native ``digest_size`` truncation.

    The choice is deterministic per-process: a corpus audited with xxhash
    will produce different fingerprints than one audited with BLAKE2b, but
    near-duplicate decisions stay stable across runs of the same process.
    Operators that need cross-machine reproducibility should pin the
    presence/absence of ``xxhash`` in their environment.
    """
    encoded = token.encode("utf-8")
    if _optional._HAS_XXHASH and bits == 64:
        return _optional._xxhash.xxh3_64(encoded).intdigest()
    digest_bytes = math.ceil(bits / 8)
    raw = int.from_bytes(
        hashlib.blake2b(encoded, digest_size=digest_bytes).digest(),
        "big",
    )
    # Shift off any extra bits introduced by rounding up to the nearest byte.
    return raw >> (digest_bytes * 8 - bits)


def _compute_simhash_numpy(weights: Dict[str, int], bits: int) -> int:
    """Numpy-vectorised simhash majority vote (dispatched from compute_simhash).

    Builds a (num_unique_tokens x bits) uint8 bit matrix in one shot using
    numpy's right-shift + bitwise-and broadcast, then reduces with a signed
    dot product to get bit_scores without any Python-level loop over bits.
    """
    np = _optional._np
    tokens_list = list(weights.keys())
    w = np.array([weights[t] for t in tokens_list], dtype=np.int64)
    hashes = np.array([_token_digest(t, bits) for t in tokens_list], dtype=np.uint64)

    # Unpack: shift[i] = hash >> i, then & 1 -> (num_tokens, bits) bool matrix
    shifts = np.arange(bits, dtype=np.uint64)
    bits_matrix = ((hashes[:, None] >> shifts) & np.uint64(1)).astype(np.int8)

    # Signed contribution: +weight where bit=1, -weight where bit=0
    # score[j] = sum_i( w[i] * (2*bit[i,j] - 1) )
    contributions = 2 * bits_matrix - 1  # maps {0,1} -> {-1,+1}
    bit_scores = w.astype(np.int64) @ contributions.astype(np.int64)  # (bits,)

    # Pack into integer
    set_bits = np.where(bit_scores > 0)[0]
    if set_bits.size == 0:
        return 0
    return int(sum(1 << int(i) for i in set_bits))


def compute_simhash(text: str, *, bits: int = 64) -> int:
    """64-bit simhash over case-folded word tokens.

    Per-bit majority voting weighted by token frequency, where each
    distinct token's hash is computed once via :func:`_token_digest`
    (cached at module scope). Empty input -> ``0``.

    When numpy is available and ``bits`` is a multiple of 8, dispatches to
    :func:`_compute_simhash_numpy` for a ~4-8x speedup on texts with many
    unique tokens.
    """
    tokens = _tokenize(text)
    if not tokens:
        return 0

    weights: Dict[str, int] = {}
    for token in tokens:
        weights[token] = weights.get(token, 0) + 1

    # Numpy fast path uses ``np.uint64`` for the hash dtype, which silently
    # truncates digests wider than 64 bits — fall back to pure Python for
    # those (the hashlib BLAKE2b path scales arbitrarily wide).
    if _optional._HAS_NUMPY and bits % 8 == 0 and bits <= 64:
        return _compute_simhash_numpy(weights, bits)

    bit_scores = [0] * bits
    for token, weight in weights.items():
        token_hash = _token_digest(token, bits)
        for i in range(bits):
            bit = (token_hash >> i) & 1
            bit_scores[i] += weight if bit else -weight

    fingerprint = 0
    for i, score in enumerate(bit_scores):
        if score > 0:
            fingerprint |= 1 << i
    return fingerprint


def hamming_distance(a: int, b: int) -> int:
    return (a ^ b).bit_count()


_LSH_MIN_BAND_BITS: int = 4
"""Minimum useful bits per LSH band. Below this the band's value space is
so narrow (<= 16 buckets) that almost every row collides and the index
degrades to a brute-force scan with extra bookkeeping. We fall back to a
linear pair walk when adaptive banding can't reach this floor."""


def _band_count_for_threshold(threshold: int, bits: int) -> int:
    """Choose ``bands`` so that pigeonhole guarantees recall.

    With ``bands = threshold + 1`` and a ``bits``-wide fingerprint, two
    fingerprints differing in ``<= threshold`` bits MUST agree on at least
    one full band (``threshold`` differing bits spread over ``threshold + 1``
    bands leaves at least one band intact). The caller is responsible for
    verifying the band-bit floor; we return 0 to signal "fall back to brute
    force" when the configured threshold leaves bands too narrow to be
    useful as a bucket key.
    """
    if threshold < 0:
        return 0
    bands = threshold + 1
    if bits // bands < _LSH_MIN_BAND_BITS:
        return 0
    return bands


def _split_into_bands(fingerprint: int, bands: int, bits: int) -> List[int]:
    """Slice ``fingerprint`` into ``bands`` equal-width band values.

    Lower bits land in band 0 to keep the slicing deterministic across
    fingerprint widths; the band index is part of the bucket key, so there
    is no security/avalanche concern.
    """
    band_bits = bits // bands
    mask = (1 << band_bits) - 1
    return [(fingerprint >> (i * band_bits)) & mask for i in range(bands)]


def _find_near_duplicates_brute(
    fingerprints: List[int],
    threshold: int,
) -> List[Tuple[int, int, int]]:
    """Quadratic fallback used when LSH banding can't be configured."""
    pairs: List[Tuple[int, int, int]] = []
    for i, fp_i in enumerate(fingerprints):
        if fp_i == 0:
            continue
        for j in range(i + 1, len(fingerprints)):
            fp_j = fingerprints[j]
            if fp_j == 0:
                continue
            distance = hamming_distance(fp_i, fp_j)
            if distance <= threshold:
                pairs.append((i, j, distance))
    return pairs


def _bucket_pairs_within_threshold(
    indices: List[int],
    fingerprints: List[int],
    threshold: int,
    seen: set,
) -> List[Tuple[int, int, int]]:
    """Verify the candidate pairs in a single LSH bucket against the full
    Hamming check, deduplicating across buckets via the shared ``seen`` set.
    """
    out: List[Tuple[int, int, int]] = []
    for left_pos in range(len(indices)):
        i = indices[left_pos]
        fp_i = fingerprints[i]
        for right_pos in range(left_pos + 1, len(indices)):
            j = indices[right_pos]
            if (i, j) in seen:
                continue
            seen.add((i, j))
            distance = hamming_distance(fp_i, fingerprints[j])
            if distance <= threshold:
                out.append((i, j, distance))
    return out


def _build_band_index(
    fingerprints: List[int],
    bands: int,
    bits: int,
) -> Dict[Tuple[int, int], List[int]]:
    """Bucket ``fingerprints`` by (band_index, band_value) for LSH lookups."""
    buckets: Dict[Tuple[int, int], List[int]] = {}
    for idx, fp in enumerate(fingerprints):
        if fp == 0:
            continue
        for band_idx, band_val in enumerate(_split_into_bands(fp, bands, bits)):
            buckets.setdefault((band_idx, band_val), []).append(idx)
    return buckets


def find_near_duplicates(
    fingerprints: List[int],
    *,
    threshold: int = DEFAULT_NEAR_DUP_HAMMING,
    bits: int = 64,
) -> List[Tuple[int, int, int]]:
    """Pair-find rows whose simhash Hamming distance <= ``threshold``.

    Returns ``[(i, j, distance), ...]`` with ``i < j``.

    Uses **LSH banding** to drop the typical case from ``O(n^2)`` to
    roughly ``O(n x k)`` where ``k`` is the average bucket fan-out:

    1. Each fingerprint is sliced into ``threshold + 1`` equal-width bands.
       Pigeonhole guarantees that two fingerprints differing in at most
       ``threshold`` bits agree on at least one full band — so candidate
       pairs are exactly the rows that collide in any single band-bucket.
    2. Candidates are then verified with the full Hamming distance check
       (see :func:`_bucket_pairs_within_threshold`).

    Falls back to a quadratic pair walk when ``threshold`` is high enough
    that bands would shrink below 4 bits (effectively useless as bucket
    keys) — at that point the index degenerates and the linear path is
    no slower in practice while remaining exact.

    Args:
        fingerprints: Per-row simhashes (``0`` is sentinel for empty rows).
        threshold: Hamming-distance cutoff; default 3 ~ 95 % similarity.
        bits: Fingerprint width in bits. Default matches
            :func:`compute_simhash`.

    Pairs are returned in row-index order to keep the output stable across
    runs and across the brute-force / LSH paths.
    """
    bands = _band_count_for_threshold(threshold, bits)
    if bands == 0:
        return _find_near_duplicates_brute(fingerprints, threshold)

    buckets = _build_band_index(fingerprints, bands, bits)
    seen: set = set()
    pairs: List[Tuple[int, int, int]] = []
    for indices in buckets.values():
        if len(indices) < 2:
            continue
        pairs.extend(_bucket_pairs_within_threshold(indices, fingerprints, threshold, seen))

    pairs.sort(key=lambda triple: (triple[0], triple[1]))
    return pairs


def _count_leaked_brute(source: List[int], target: List[int], threshold: int) -> int:
    """Linear-scan fallback used when LSH banding cannot be configured."""
    return sum(
        1
        for fp in source
        if fp != 0 and any(other != 0 and hamming_distance(fp, other) <= threshold for other in target)
    )


def _source_row_leaks(
    fp: int,
    target_index: Dict[Tuple[int, int], List[int]],
    target: List[int],
    threshold: int,
    bands: int,
    bits: int,
) -> bool:
    """Walk one source row's bands; return True on the first target hit.

    The ``seen`` set is per-source-row: we never double-check the same
    target index across the source row's bands, but a fresh source row
    starts with an empty ``seen`` because its hits are independent.
    """
    seen: set = set()
    for band_idx, band_val in enumerate(_split_into_bands(fp, bands, bits)):
        candidates = target_index.get((band_idx, band_val))
        if not candidates:
            continue
        for j in candidates:
            if j in seen:
                continue
            seen.add(j)
            if hamming_distance(fp, target[j]) <= threshold:
                return True
    return False


def _count_leaked_rows(
    source: List[int],
    target: List[int],
    threshold: int,
    *,
    bits: int = 64,
) -> int:
    """Rows in ``source`` whose nearest neighbour in ``target`` is within ``threshold``.

    Uses the same LSH banding shape as :func:`find_near_duplicates` so a
    cross-split overlap audit on a 100 K-row train + 10 K-row test no
    longer needs the full ``100K x 10K`` Hamming sweep. Falls back to
    :func:`_count_leaked_brute` when banding cannot be configured (edge
    thresholds where bands shrink below 4 bits).
    """
    bands = _band_count_for_threshold(threshold, bits)
    if bands == 0:
        return _count_leaked_brute(source, target, threshold)

    target_index = _build_band_index(target, bands, bits)
    if not target_index:
        return 0

    return sum(1 for fp in source if fp != 0 and _source_row_leaks(fp, target_index, target, threshold, bands, bits))


__all__ = [
    "_TOKEN_PATTERN",
    "_tokenize",
    "_token_digest",
    "_compute_simhash_numpy",
    "compute_simhash",
    "hamming_distance",
    "_LSH_MIN_BAND_BITS",
    "_band_count_for_threshold",
    "_split_into_bands",
    "_find_near_duplicates_brute",
    "_bucket_pairs_within_threshold",
    "_build_band_index",
    "find_near_duplicates",
    "_count_leaked_brute",
    "_source_row_leaks",
    "_count_leaked_rows",
]
