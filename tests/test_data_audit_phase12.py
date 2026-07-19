"""Phase 12 tests for forgelm.data_audit — MinHash, secrets, quality filter.

Kept in a dedicated file so the Phase 11 / 11.5 surface (``test_data_audit.py``)
stays focused on the simhash / regex / streaming contract — Phase 12 adds
optional methods that only run when explicitly opted into, plus an
always-on secrets scan.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from forgelm.data_audit import (
    DEDUP_METHODS,
    DEFAULT_MINHASH_JACCARD,
    SECRET_TYPES,
    _row_quality_flags,
    audit_dataset,
    detect_secrets,
    mask_secrets,
)


def _has(module_name: str) -> bool:
    try:
        importlib.import_module(module_name)
        return True
    except ImportError:
        return False


def _write_jsonl(path: Path, rows):
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


# Secret-shaped fixtures reconstructed at runtime from inert fragments —
# the regex still has to match real shapes, but no full literal credential
# lives in the source tree (silences gitleaks / trufflehog scans of the repo).
FAKE_AWS_KEY: str = "AKIA" + "IOSFODNN7" + "EXAMPLE"
FAKE_GH_TOKEN: str = "ghp_" + "1234567890abcdefghij" + "ABCDEFGHIJabcdef"
FAKE_OPENAI_KEY: str = "sk-proj-" + "abcDEF1234567890" + "_XYZ-tokens-here"
# JWT pieces — header is the base64url of {"alg":"HS256"}; payload + sig are
# inert lookalikes that satisfy the post-fix regex (alg-prefix anchor).
FAKE_JWT: str = "eyJhbGciOiJIUzI1NiJ9" + "." + "eyJzdWIiOiIxIn0" + "." + "SflKxwRJSMeKKF2QT4fwpMeJ"


# ---------------------------------------------------------------------------
# Secrets detection — always-on; runs without any optional dependency
# ---------------------------------------------------------------------------


class TestSecretsDetection:
    def test_aws_access_key_detected(self):
        # ``AKIA…`` 20-char access key.
        text = f"config: aws_access_key_id={FAKE_AWS_KEY} end"
        result = detect_secrets(text)
        assert result.get("aws_access_key") == 1

    def test_github_token_detected(self):
        text = f"token: {FAKE_GH_TOKEN}"
        result = detect_secrets(text)
        assert result.get("github_token") == 1

    def test_openai_api_key_detected(self):
        text = f"OPENAI_API_KEY={FAKE_OPENAI_KEY}"
        result = detect_secrets(text)
        assert result.get("openai_api_key") == 1

    def test_jwt_detected(self):
        # Real-shape JWT: header.payload.signature, all base64url.
        text = f"Authorization: Bearer {FAKE_JWT}"
        result = detect_secrets(text)
        assert result.get("jwt") == 1

    def test_clean_text_returns_empty(self):
        assert detect_secrets("perfectly innocent prose with no credentials") == {}

    def test_non_string_returns_empty(self):
        assert detect_secrets(None) == {}
        assert detect_secrets(42) == {}

    def test_secret_types_listed(self):
        # Sanity: the public tuple should match what detect_secrets can emit.
        assert "aws_access_key" in SECRET_TYPES
        assert "github_token" in SECRET_TYPES
        assert "jwt" in SECRET_TYPES


class TestSecretsMasking:
    def test_aws_key_redacted(self):
        original = f"config: aws_access_key_id={FAKE_AWS_KEY} end"
        masked = mask_secrets(original)
        assert FAKE_AWS_KEY not in masked
        assert "[REDACTED-SECRET]" in masked

    def test_return_counts_truthful(self):
        original = f"k1={FAKE_AWS_KEY} / k2={FAKE_GH_TOKEN}"
        masked, counts = mask_secrets(original, return_counts=True)
        assert counts.get("aws_access_key") == 1
        assert counts.get("github_token") == 1
        assert "[REDACTED-SECRET]" in masked

    def test_clean_text_passes_through(self):
        original = "no secrets here"
        masked, counts = mask_secrets(original, return_counts=True)
        assert masked == original
        assert counts == {}

    def test_non_string_passes_through(self):
        assert mask_secrets(None) is None


class TestAuditPicksUpSecrets:
    def test_secrets_summary_lands_in_audit_json(self, tmp_path):
        path = tmp_path / "x.jsonl"
        _write_jsonl(
            path,
            [
                {"text": f"key={FAKE_AWS_KEY} here"},
                {"text": "innocent line"},
                {"text": f"token={FAKE_GH_TOKEN}"},
            ],
        )
        report = audit_dataset(str(path))
        # detect_secrets is always-on — no flag needed.
        assert report.secrets_summary.get("aws_access_key") == 1
        assert report.secrets_summary.get("github_token") == 1


# ---------------------------------------------------------------------------
# Quality filter — opt-in; default audit doesn't run it
# ---------------------------------------------------------------------------


class TestQualityFilterPerRow:
    def test_low_alpha_ratio_flagged(self):
        # 90% non-letters → flagged.
        text = "1234567890 !@#$%^&*() {} [] :;<>"
        flags = _row_quality_flags(text)
        assert "low_alpha_ratio" in flags

    def test_short_paragraphs_flagged(self):
        # All paragraphs are < 5 words → flagged.
        text = "hi there.\n\nyo.\n\nok bye."
        flags = _row_quality_flags(text)
        assert "short_paragraphs" in flags

    def test_clean_prose_passes(self):
        text = (
            "The quick brown fox jumps over the lazy dog. The same fox "
            "later jumps back, this time more deliberately. End-of-line "
            "punctuation appears throughout the corpus. Lines are long "
            "enough to satisfy the heuristic checks."
        )
        flags = _row_quality_flags(text)
        assert flags == []

    def test_empty_text_returns_empty(self):
        # ``_row_quality_flags`` is typed ``Optional[str]`` so the streaming
        # aggregator can call it on every row without per-call type checks.
        assert _row_quality_flags("") == []
        assert _row_quality_flags(None) == []


class TestQualityFilterEnabled:
    def test_quality_summary_only_present_when_enabled(self, tmp_path):
        path = tmp_path / "x.jsonl"
        _write_jsonl(path, [{"text": "1234567890 !@#$%^&*()"}, {"text": "fine prose here that survives heuristics."}])

        # Default: quality filter off → no quality_summary fields.
        default_report = audit_dataset(str(path))
        assert default_report.quality_summary == {}

        # Opt-in: quality filter on → quality_summary populated.
        opt_in_report = audit_dataset(str(path), enable_quality_filter=True)
        assert opt_in_report.quality_summary.get("samples_flagged", 0) >= 1
        assert "by_check" in opt_in_report.quality_summary


# ---------------------------------------------------------------------------
# MinHash LSH — needs the optional 'datasketch' extra
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _has("datasketch"), reason="datasketch (ingestion-scale extra) not installed")
class TestMinHashLshDedup:
    def test_dedup_method_choices_listed(self):
        assert "simhash" in DEDUP_METHODS
        assert "minhash" in DEDUP_METHODS

    def test_minhash_finds_near_duplicates(self):
        from forgelm.data_audit import compute_minhash, find_near_duplicates_minhash

        texts = [
            "the quick brown fox jumps over the lazy dog",
            "the quick brown fox jumps over the lazy dog",  # exact dup
            "the quick brown fox leaps over the lazy dog",  # near dup
            "completely unrelated payload with different tokens",
        ]
        minhashes = [compute_minhash(t) for t in texts]
        pairs = find_near_duplicates_minhash(minhashes, jaccard_threshold=0.5)
        pair_idx = {(i, j) for i, j, _ in pairs}
        # exact + near should both surface
        assert (0, 1) in pair_idx
        assert (0, 2) in pair_idx or (1, 2) in pair_idx

    def test_audit_minhash_writes_method_in_report(self, tmp_path):
        path = tmp_path / "x.jsonl"
        _write_jsonl(
            path,
            [
                {"text": "alpha beta gamma delta epsilon zeta"},
                {"text": "alpha beta gamma delta epsilon zeta"},
                {"text": "completely unrelated payload"},
            ],
        )
        report = audit_dataset(
            str(path),
            dedup_method="minhash",
            minhash_jaccard=DEFAULT_MINHASH_JACCARD,
        )
        assert report.near_duplicate_summary.get("method") == "minhash"
        # Within-split near-dup pair must be picked up.
        assert report.splits["train"]["near_duplicate_pairs"] >= 1

    def test_audit_default_uses_simhash(self, tmp_path):
        # Phase 11.5 default behaviour preserved when method is omitted.
        path = tmp_path / "x.jsonl"
        _write_jsonl(path, [{"text": "alpha"}])
        report = audit_dataset(str(path))
        assert report.near_duplicate_summary.get("method") == "simhash"


class TestMinHashMissingExtra:
    def test_helpful_error_when_datasketch_missing(self, monkeypatch):
        from forgelm import data_audit as audit_mod

        # Faz 14: optional-deps sentinels live in ._optional after the
        # data_audit package split; ._minhash reads ``_optional._HAS_DATASKETCH``
        # via attribute lookup, so patches applied to the canonical module
        # propagate to the require-helper.
        monkeypatch.setattr(audit_mod._optional, "_HAS_DATASKETCH", False)
        with pytest.raises(ImportError, match=r"forgelm\[ingestion-scale\]"):
            audit_mod._require_datasketch()


# ---------------------------------------------------------------------------
# Quality score — clean split's rows must count in the score denominator
# ---------------------------------------------------------------------------


# Known-clean prose (matches the fixture in TestQualityFilterPerRow) — passes
# every heuristic quality check, so it lands as evaluated-but-not-flagged.
_CLEAN_PROSE: str = (
    "The quick brown fox jumps over the lazy dog. The same fox later jumps "
    "back, this time more deliberately. End-of-line punctuation appears "
    "throughout the corpus. Lines are long enough to satisfy the heuristics."
)


class TestQualityScoreMultiSplitCleanSplit:
    """A split scanned with zero quality flags (the common clean case) must
    still contribute its evaluated rows to ``overall_quality_score``'s
    denominator. Previously the per-split ``quality_samples_evaluated`` was
    only written when a flag fired, so a clean split's rows vanished from the
    denominator — biasing the EU AI Act Article 10 score low."""

    def test_clean_split_rows_counted_in_denominator(self, tmp_path):
        _write_jsonl(
            tmp_path / "train.jsonl",
            [
                {"text": "1234567890 !@#$%^&*()"},  # low_alpha_ratio -> flagged
                {"text": _CLEAN_PROSE},
            ],
        )
        _write_jsonl(
            tmp_path / "validation.jsonl",
            [{"text": _CLEAN_PROSE} for _ in range(3)],  # all clean, zero flags
        )
        report = audit_dataset(str(tmp_path), enable_quality_filter=True)
        qs = report.quality_summary
        # 5 rows scanned total; every one counts in the denominator.
        assert qs["samples_evaluated"] == 5
        assert qs["samples_flagged"] == 1
        assert qs["overall_quality_score"] == round(1 - 1 / 5, 4)  # 0.8, not 0.5
        # The clean split surfaces its evaluated-row count even with zero flags.
        assert report.splits["validation"].get("quality_samples_evaluated") == 3
        assert report.splits["validation"].get("quality_samples_flagged") == 0


# ---------------------------------------------------------------------------
# Private-key secret patterns — bounded lazy span, no quadratic ReDoS
# ---------------------------------------------------------------------------


class TestSecretsPrivateKeyReDoS:
    """The openssh/pgp private-key patterns used an unbounded ``.*?`` under
    DOTALL — O(n^2) on a row with many unclosed BEGIN markers (a scraped doc,
    a corrupted PEM dump, one crafted row; measured ~18.9s / 500KB). Bounded to
    ``.{0,N}?`` so the scan is linear. Median-of-5 growth-ratio check per
    regex.md's ReDoS-budget methodology. Markers are built from inert fragments
    (no full literal key material) per regex.md fixture hygiene."""

    _TOLERANCE = 3

    def _median_ms(self, fn, payload, samples=5):
        import statistics
        import time

        obs = []
        for _ in range(samples):
            t0 = time.perf_counter()
            fn(payload)
            obs.append((time.perf_counter() - t0) * 1000)
        return statistics.median(obs)

    @pytest.mark.parametrize(
        "kind, begin",
        [
            ("openssh_private_key", "-----" + "BEGIN " + "RSA PRIVATE KEY" + "-----" + "\n"),
            ("pgp_private_key", "-----" + "BEGIN " + "PGP PRIVATE KEY BLOCK" + "-----" + "\n"),
        ],
    )
    def test_unclosed_begin_markers_scale_linearly(self, kind, begin):
        from forgelm.data_audit._secrets import _SECRET_PATTERNS

        pat = _SECRET_PATTERNS[kind]
        sizes = (1_000, 2_000, 4_000)
        timings = {n: self._median_ms(pat.search, begin * n) for n in sizes}
        baseline = max(timings[1_000], 0.001)
        for n in sizes[1:]:
            ratio = timings[n] / baseline
            allowed = (n / 1_000) * self._TOLERANCE
            assert ratio <= allowed, (
                f"{kind} grew {ratio:.1f}x from n=1000 to n={n} "
                f"(allowed {allowed:.1f}x); ReDoS regression. timings_ms={timings}"
            )

    def test_real_closed_key_still_detected_and_masked(self):
        begin = "-----" + "BEGIN " + "RSA PRIVATE KEY" + "-----"
        end = "-----" + "END " + "RSA PRIVATE KEY" + "-----"
        body = "\n".join("QUFB" * 16 for _ in range(20))
        key = f"{begin}\n{body}\n{end}"
        assert detect_secrets(f"pre\n{key}\npost").get("openssh_private_key") == 1
        masked = mask_secrets(f"pre\n{key}\npost")
        assert "QUFB" not in masked
        assert "[REDACTED-SECRET]" in masked

    def test_large_pgp_block_still_detected(self):
        """PGP blocks with multiple subkeys / user IDs / a photo-ID packet
        routinely exceed 8 KB — well past the shared PEM bound the pgp_private_key
        pattern used to reuse. A block just over that old 8192-char body bound
        must still be detected and masked (recall floor for
        _PGP_PRIVATE_KEY_BODY_MAX_CHARS)."""
        from forgelm.data_audit._secrets import _PRIVATE_KEY_BODY_MAX_CHARS

        begin = "-----" + "BEGIN " + "PGP PRIVATE KEY BLOCK" + "-----"
        end = "-----" + "END " + "PGP PRIVATE KEY BLOCK" + "-----"
        # >8192 chars of body — would have been a recall miss under the old
        # shared PEM/PGP bound.
        body = "\n".join("QUFB" * 16 for _ in range(_PRIVATE_KEY_BODY_MAX_CHARS // 64 + 20))
        assert len(body) > _PRIVATE_KEY_BODY_MAX_CHARS
        key = f"{begin}\n{body}\n{end}"

        assert detect_secrets(f"pre\n{key}\npost").get("pgp_private_key") == 1
        masked = mask_secrets(f"pre\n{key}\npost")
        assert "QUFB" not in masked
        assert "[REDACTED-SECRET]" in masked


# ---------------------------------------------------------------------------
# Routed (tests-standalone): MinHash LSH backend had zero real execution.
#
# _minhash.py's LSH wiring sat at ~16% coverage because every test that would
# run it was gated behind a ``datasketch`` importorskip / skipif, and no CI
# leg installs the ``ingestion-scale`` extra — so the candidate generation,
# Jaccard verification, and bidirectional leak counting never ran in CI. A
# faithful pure-Python stub of datasketch's MinHash / MinHashLSH interface lets
# the REAL _minhash.py code run in the default (.[dev], no-extra) environment,
# so a regression there surfaces without the optional dep. When datasketch IS
# installed, TestMinHashLshDedup above still exercises the genuine library.
# ---------------------------------------------------------------------------


class _StubMinHash:
    """Minimal ``datasketch.MinHash`` stand-in: exact set-Jaccard over the
    update tokens. Faithful enough to drive _minhash.py's LSH branches; the
    real library's approximate permutation internals are third-party and not
    this project's coverage target."""

    def __init__(self, num_perm: int = 128) -> None:
        self.num_perm = num_perm
        self._tokens: set = set()

    def update(self, token: bytes) -> None:
        self._tokens.add(token)

    def jaccard(self, other: "_StubMinHash") -> float:
        if not self._tokens and not other._tokens:
            return 1.0
        union = self._tokens | other._tokens
        return len(self._tokens & other._tokens) / len(union) if union else 0.0

    @property
    def hashvalues(self) -> "_StubHashValues":
        return _StubHashValues(self._tokens)


class _StubHashValues:
    """Supplies ``.tobytes()`` for ``_aggregator_to_info``'s minhash_distinct
    identity set."""

    def __init__(self, tokens: set) -> None:
        self._tokens = tokens

    def tobytes(self) -> bytes:
        return repr(sorted(self._tokens)).encode("utf-8")


class _StubMinHashLSH:
    """Minimal ``datasketch.MinHashLSH`` stand-in. ``query`` returns exact
    matches — a valid subset of a real LSH's candidate set — which still
    exercises the verification path in ``_emit_minhash_pair`` /
    ``_count_leaks_against_index``."""

    def __init__(self, threshold: float = DEFAULT_MINHASH_JACCARD, num_perm: int = 128) -> None:
        self.threshold = threshold
        self.num_perm = num_perm
        self._store: dict = {}

    def insert(self, key: str, m: _StubMinHash) -> None:
        self._store[key] = m

    def query(self, m: _StubMinHash) -> list:
        return [key for key, stored in self._store.items() if stored.jaccard(m) >= self.threshold]


@pytest.fixture
def _stub_datasketch(monkeypatch):
    from forgelm.data_audit import _optional

    monkeypatch.setattr(_optional, "_HAS_DATASKETCH", True)
    monkeypatch.setattr(_optional, "_MinHash", _StubMinHash)
    monkeypatch.setattr(_optional, "_MinHashLSH", _StubMinHashLSH)
    return _optional


class TestMinHashLshBackendExecutedWithStub:
    """Exercises the real _minhash.py LSH backend end-to-end via the stub, so
    the code path runs in every CI leg rather than being skipped."""

    def test_find_near_duplicates_minhash_runs(self, _stub_datasketch):
        from forgelm.data_audit import compute_minhash, find_near_duplicates_minhash

        texts = [
            "the quick brown fox jumps over the lazy dog",
            "the quick brown fox jumps over the lazy dog",  # exact dup
            "the quick brown fox leaps over the lazy dog",  # near dup
            "completely unrelated payload with different tokens",
        ]
        minhashes = [compute_minhash(t) for t in texts]
        pairs = find_near_duplicates_minhash(minhashes, jaccard_threshold=0.5)
        pair_idx = {(i, j) for i, j, _ in pairs}
        assert (0, 1) in pair_idx  # exact dup surfaces
        assert (0, 2) in pair_idx or (1, 2) in pair_idx  # near dup surfaces
        assert all(score >= 0.5 for _, _, score in pairs)

    def test_audit_minhash_within_and_cross_split(self, _stub_datasketch, tmp_path):
        _write_jsonl(
            tmp_path / "train.jsonl",
            [
                {"text": "alpha beta gamma delta epsilon zeta"},
                {"text": "alpha beta gamma delta epsilon zeta"},  # within-split dup
                {"text": "unique train content one two three"},
            ],
        )
        _write_jsonl(
            tmp_path / "test.jsonl",
            [
                {"text": "alpha beta gamma delta epsilon zeta"},  # leaks from train
                {"text": "unrelated test content four five six"},
            ],
        )
        report = audit_dataset(str(tmp_path), dedup_method="minhash", minhash_jaccard=0.85)
        assert report.near_duplicate_summary.get("method") == "minhash"
        # within-split near-duplicate pair detected (_build_minhash_lsh / _emit_minhash_pair)
        assert report.splits["train"]["near_duplicate_pairs"] >= 1
        # cross-split leak counted both directions (_count_leaked_rows_minhash_bidirectional)
        payload = report.cross_split_overlap["pairs"]["train__test"]
        assert payload["leaked_rows_in_train"] >= 1
        assert payload["leaked_rows_in_test"] >= 1

    def test_compute_minhash_empty_returns_none(self, _stub_datasketch):
        from forgelm.data_audit import compute_minhash

        assert compute_minhash("") is None
