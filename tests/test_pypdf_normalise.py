"""Phase 15 Task 3 unit tests — pypdf glyph-normalisation profile."""

from __future__ import annotations

import pytest

from forgelm._pypdf_normalise import (
    DEFAULT_PROFILE,
    PROFILES,
    apply_profile,
    count_substitutions,
    emitted_codepoints,
    profile_summary,
)


class TestProfileVocabulary:
    def test_default_profile_is_none_after_round2_fix(self):
        # Round-2 review (C-2): the module-level default flipped from
        # "turkish" to "none" so a library caller without an explicit
        # language hint does not silently rewrite non-Turkish text.
        # CLI dispatcher (`_resolve_normalise_profile`) + ingest_path
        # auto-derive "turkish" when ``language_hint="tr"`` is set.
        assert DEFAULT_PROFILE == "none"

    def test_profiles_includes_none_for_explicit_opt_out(self):
        assert "none" in PROFILES
        assert "turkish" in PROFILES


class TestApplyProfile:
    def test_turkish_profile_round_trip_canonical_glyphs(self):
        # Audit-measured artefacts: ø Õ ú ÷ ࡟ → İ ı ş ğ •
        assert apply_profile("ø Õ ú ÷ ࡟", "turkish") == "İ ı ş ğ •"

    def test_multichar_substitution_fires_before_single_char(self):
        # ``ö `` (with trailing space) maps to ``Ğ`` — the multi-char
        # pass must run before the single-char str.translate() lookup.
        assert apply_profile("ö abc", "turkish") == "Ğabc"

    def test_none_profile_returns_input_unchanged(self):
        assert apply_profile("ø Õ ú", "none") == "ø Õ ú"

    def test_unknown_profile_silently_no_ops(self):
        # Forward compat: don't crash an ingest run on an unknown profile.
        assert apply_profile("ø Õ ú", "klingon") == "ø Õ ú"

    def test_empty_text_returns_empty(self):
        assert apply_profile("", "turkish") == ""

    def test_preserves_clean_turkish_text(self):
        # The normaliser must never rewrite correct Turkish characters.
        clean = "Türkçe metin doğru karakterler içerir."
        assert apply_profile(clean, "turkish") == clean

    def test_multichar_rule_is_lossy_on_legitimate_o_umlaut(self):
        # F-P6-OPUS-14: the ``"ö " → "Ğ"`` rule is a 2→1, space-consuming
        # transform, so a token that legitimately ends in ``ö`` before a
        # space is merged with the next token. This pins the known,
        # accepted false-positive of the turkish profile so a future
        # widening (or a docstring claim of 1:1 byte-for-byte behaviour)
        # is caught.
        assert apply_profile("foo ö bar", "turkish") == "foo Ğbar"

    def test_docstring_acknowledges_multichar_rule_is_not_1to1(self):
        # F-P6-OPUS-14: the apply_profile docstring previously claimed
        # "1:1 substitutions preserve every other code point byte-for-byte
        # — the function does not strip whitespace", contradicting the
        # shipped 2→1 multi-char rule. The docstring must acknowledge the
        # lossy multi-char transform.
        import inspect

        doc = " ".join((inspect.getdoc(apply_profile) or "").split())
        assert "multi-character" in doc
        assert "2→1" in doc
        # The bare "1:1 ... byte-for-byte / does not strip whitespace"
        # claim must no longer stand unqualified.
        assert "not** 1:1" in doc


class TestProfileSummary:
    def test_turkish_summary_returns_table_sizes(self):
        summary = profile_summary("turkish")
        assert summary["single"] >= 5
        assert summary["multi"] >= 1

    def test_none_profile_summary_is_zero(self):
        assert profile_summary("none") == {"multi": 0, "single": 0}

    def test_unknown_profile_summary_is_zero(self):
        assert profile_summary("klingon") == {"multi": 0, "single": 0}


class TestEmittedCodepoints:
    def test_turkish_emit_set_contains_targets(self):
        emit = emitted_codepoints("turkish")
        for target in ("İ", "ı", "ş", "ğ", "•", "Ğ"):
            assert target in emit

    def test_none_profile_emits_nothing(self):
        assert emitted_codepoints("none") == frozenset()


class TestCountSubstitutions:
    """Round-3 review N-1: pin the exact-count contract.

    ``count_substitutions`` replaced the zip-diff heuristic ingestion.py
    used to log substitution counts. The function must return EXACTLY
    the number of rules that would fire under :func:`apply_profile`.
    """

    def test_empty_text_returns_zero(self):
        assert count_substitutions("", "turkish") == 0

    def test_none_profile_returns_zero(self):
        assert count_substitutions("ø Õ ú ÷", "none") == 0

    def test_unknown_profile_returns_zero(self):
        assert count_substitutions("ø Õ ú ÷", "klingon") == 0

    def test_single_char_substitutions_counted_per_occurrence(self):
        # Five distinct single-char artefacts, one occurrence each → 5.
        assert count_substitutions("ø Õ ú ÷ ࡟", "turkish") == 5

    def test_multi_char_rule_counted_once_per_match(self):
        # ``"ö "`` (with trailing space) → ``"Ğ"``; single occurrence.
        assert count_substitutions("ö abc", "turkish") == 1

    def test_multi_char_rule_consumes_trigger_before_single_pass(self):
        # ``"ö ø"`` → multi-char rule fires on ``"ö "`` (count 1), then
        # ``ø`` single-char rule fires on the trailing ``ø`` (count 1).
        # Total = 2; matches apply_profile result ``"Ğİ"``.
        assert count_substitutions("ö ø", "turkish") == 2

    def test_repeated_single_char_counted_per_occurrence(self):
        # ``"øøøø"`` → four substitutions even though only one rule key.
        assert count_substitutions("øøøø", "turkish") == 4

    def test_clean_turkish_text_returns_zero(self):
        # No artefact characters → no substitutions.
        assert count_substitutions("Türkçe metin doğru karakterler içerir.", "turkish") == 0

    def test_partial_multi_char_match_does_not_count_single_consumed(self):
        # ``"öö abc"`` → only ONE ``"ö "`` match (the SECOND ``ö``
        # followed by space); the first ``ö`` has no trailing space.
        # apply_profile produces ``"öĞabc"`` → 1 substitution.
        assert count_substitutions("öö abc", "turkish") == 1


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
