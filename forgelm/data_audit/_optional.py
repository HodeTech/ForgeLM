"""Optional-dependency sentinels for the data_audit package.

Each block tries the optional import once at module load time and exposes
a ``_HAS_*`` boolean plus the imported handle (or ``None`` when missing).
Keeping these in one file gives every other sub-module a single place to
read from — and tests one stable dotted path
(``forgelm.data_audit._optional._HAS_*``) to patch when simulating a
missing extra.
"""

from __future__ import annotations

# Phase 11.5: optional xxhash backend for the simhash digest. xxh3_64 is a
# non-cryptographic 64-bit hash. The Python-level speedup is modest — local
# microbenchmark (Apple Silicon, Python 3.11.2, xxhash 3.7.0) measured ~1.3x
# on the raw per-digest cost and ~1.05x end-to-end inside compute_simhash
# (the lru_cache below absorbs most repeats anyway). xxhash's well-known
# "4-10x faster than crypto hashes" figure refers to C-level pure-hash
# benchmarks; the Python wrapping (encode -> call -> intdigest) levels the
# playing field. We keep xxhash as the optional backend mostly for forward
# compatibility / parity with other simhash implementations and fall back to
# BLAKE2b cleanly when the dependency is missing so a bare install still
# produces identical fingerprints across releases.
try:  # pragma: no cover — exercised by the dedicated extras-skip tests
    import xxhash as _xxhash

    _HAS_XXHASH = True
except ImportError:  # pragma: no cover
    _xxhash = None
    _HAS_XXHASH = False


# C.3: optional numpy for vectorised bit-unpacking inside compute_simhash.
# The pure-Python path loops `bits` times per token; numpy unpacks the full
# `bits`-wide integer in one matrix operation (tokens x bits) and reduces
# along the token axis with a single dot product.  Speedup is ~4-8x on
# corpora with >1 K unique tokens per record; negligible for short texts
# (lru_cache already handles repeated tokens).
try:  # pragma: no cover — optional accelerator
    import numpy as _np

    _HAS_NUMPY = True
except ImportError:  # pragma: no cover
    _np = None  # type: ignore[assignment]
    _HAS_NUMPY = False


# Phase 12: optional ``datasketch`` backend for MinHash LSH near-duplicate
# detection. Default audit stays on the simhash + LSH band path (Phase 11.5)
# which is exact at ``threshold=3`` and bounded ~50K rows. ``--dedup-method
# minhash`` opts into LSH-banded MinHash, which is the industry standard for
# >50K-row corpora (NeMo Curator, Dolma, RedPajama). MinHash is approximate
# (Jaccard with permutation noise) — that's why simhash stays default.
try:  # pragma: no cover — exercised by the dedicated extras-skip tests
    from datasketch import MinHash as _MinHash
    from datasketch import MinHashLSH as _MinHashLSH

    _HAS_DATASKETCH = True
except ImportError:  # pragma: no cover
    _MinHash = None  # type: ignore[assignment]
    _MinHashLSH = None  # type: ignore[assignment]
    _HAS_DATASKETCH = False


# Phase 12.5: optional Presidio ML-NER PII detection. Layered ON TOP of
# the regex detector so the default audit keeps its zero-extra-deps
# guarantee. Presidio + spaCy are heavyweight (~50 MB model download),
# so we stay strictly opt-in and fail soft when missing — the regex set
# already covers the common structured identifiers (email, phone,
# IBAN, credit card, national IDs) that the audit's compliance contract
# is built around. ML adds the *unstructured* identifiers regex inherently
# misses: person names, organizations, locations.
try:  # pragma: no cover — exercised by the dedicated extras-skip tests
    from presidio_analyzer import AnalyzerEngine as _PresidioAnalyzer

    _HAS_PRESIDIO = True
except ImportError:  # pragma: no cover
    _PresidioAnalyzer = None  # type: ignore[assignment]
    _HAS_PRESIDIO = False


__all__ = [
    "_HAS_XXHASH",
    "_xxhash",
    "_HAS_NUMPY",
    "_np",
    "_HAS_DATASKETCH",
    "_MinHash",
    "_MinHashLSH",
    "_HAS_PRESIDIO",
    "_PresidioAnalyzer",
]
