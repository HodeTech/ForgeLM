"""Phase 15 Task 2 unit tests — Unicode / script-sanity checker."""

from __future__ import annotations

import logging

import pytest

from forgelm._script_sanity import (
    DEFAULT_THRESHOLD,
    SUPPORTED_LANGUAGES,
    ScriptSanityReport,
    check_script_sanity,
    report_to_structured,
)


class TestDefaults:
    def test_threshold_is_calibrated_for_audit_findings(self):
        # Phase 15 calibration: catches 7 % corruption (front matter) and
        # 3 % corruption (bullet chapter) while keeping 1 % mixed-script
        # corpora silent. 1.5 % sits in the middle.
        assert DEFAULT_THRESHOLD == pytest.approx(0.015)

    def test_supported_languages_includes_turkish_and_english(self):
        assert "tr" in SUPPORTED_LANGUAGES
        assert "en" in SUPPORTED_LANGUAGES


class TestCheckScriptSanity:
    def test_fires_on_audit_canonical_glyphs(self, caplog):
        # The audit's two-chunk front-matter corruption shape: ~7 % weird chars.
        # Mix a smaller clean prefix with enough corrupt glyphs to clear
        # the 1.5 % threshold by a clean margin.
        text = "Sadece düzgün bir Türkçe paragraf. " * 10 + "øø÷÷ÕÕúú" * 8
        with caplog.at_level(logging.WARNING):
            report = check_script_sanity(
                text,
                language_hint="tr",
                file_path="probe.pdf",
                threshold=0.02,
                profile="none",
            )
        assert report.triggered
        assert any(g == "ø" for g, _ in report.top_offenders)
        # Warning fired loudly enough that a CI log scrape catches it.
        assert any("Script-sanity warning" in r.message for r in caplog.records)

    def test_silent_on_clean_turkish(self):
        clean = "Türkçe metin doğru karakterler içerir. " * 50
        report = check_script_sanity(clean, language_hint="tr", file_path="clean.txt")
        assert not report.triggered
        assert report.ratio == pytest.approx(0.0)

    def test_silent_on_unknown_language(self):
        # Unknown language hint — check is a no-op (not an error).
        report = check_script_sanity("any text", language_hint="zz", file_path="x.txt")
        assert not report.triggered

    def test_silent_on_empty_text(self):
        report = check_script_sanity("", language_hint="tr", file_path="empty.txt")
        assert not report.triggered

    def test_profile_allowlist_protects_bullets(self):
        # Bullets are normaliser-emitted under the turkish profile, so
        # they should not count as out-of-script.
        text = "Bir satır • Başka satır • Yine başka • Daha fazla. " * 20
        report = check_script_sanity(text, language_hint="tr", file_path="b.txt", profile="turkish")
        assert not report.triggered

    def test_cause_hint_identifies_font_fallback(self):
        text = "Body text " * 100 + "ø" * 50 + "÷" * 50
        report = check_script_sanity(
            text,
            language_hint="tr",
            file_path="font.pdf",
            threshold=0.01,
            profile="none",
        )
        assert report.triggered
        assert "font fallback" in report.cause_hint.lower()

    def test_cause_hint_does_not_mislabel_legitimate_cjk_as_custom_glyph(self):
        # Regression: the old heuristic's `ord(g) >= 0x0800` disjunct was a
        # blanket catch-all covering Devanagari, Thai, Georgian, Hangul,
        # Hiragana/Katakana, and all CJK Unified Ideographs — not just the
        # Mandaic bullet artefact it was added for. Ordinary embedded
        # Chinese text (a common, legitimate case: English docs quoting
        # Chinese) must not be mislabelled "custom glyph / private-use
        # block" — it's a different script, not corrupted data.
        text = "This document quotes a Chinese phrase: " + "中文引用内容 " * 30
        report = check_script_sanity(
            text,
            language_hint="en",
            file_path="cjk.txt",
            threshold=0.01,
            profile="none",
        )
        assert report.triggered  # still correctly flagged as out-of-script
        assert report.cause_hint != "custom glyph / private-use block"

    def test_cause_hint_still_identifies_mandaic_bullet_artefact(self):
        # The narrowed heuristic must still catch the audit's original
        # U+085F Mandaic-block bullet-glyph corruption.
        text = "Body text " * 100 + chr(0x085F) * 50
        report = check_script_sanity(
            text,
            language_hint="tr",
            file_path="mandaic.pdf",
            threshold=0.01,
            profile="none",
        )
        assert report.triggered
        assert report.cause_hint == "custom glyph / private-use block"

    def test_cause_hint_still_identifies_true_pua_block(self):
        text = "Body text " * 100 + chr(0xE000) * 50
        report = check_script_sanity(
            text,
            language_hint="en",
            file_path="pua.txt",
            threshold=0.01,
            profile="none",
        )
        assert report.triggered
        assert report.cause_hint == "custom glyph / private-use block"

    def test_cause_hint_identifies_mojibake(self):
        text = "Body text " * 100 + "�" * 100
        report = check_script_sanity(
            text,
            language_hint="tr",
            file_path="moji.txt",
            threshold=0.01,
            profile="none",
        )
        assert report.triggered
        assert "mojibake" in report.cause_hint.lower()

    def test_logger_override_for_test_isolation(self):
        custom_logger = logging.getLogger("test.script_sanity")
        captured: list = []
        handler = logging.Handler()
        handler.emit = lambda record: captured.append(record.getMessage())
        custom_logger.addHandler(handler)
        custom_logger.setLevel(logging.WARNING)

        try:
            check_script_sanity(
                "Body. " * 50 + "øø" * 30,
                language_hint="tr",
                file_path="t.txt",
                threshold=0.01,
                profile="none",
                logger_override=custom_logger,
            )
            # Captured by the test-local logger, not the module logger.
            assert any("Script-sanity" in m for m in captured)
        finally:
            # Round-2 nit: avoid leaking the test handler into other tests
            # that share the ``test.script_sanity`` logger name.
            custom_logger.removeHandler(handler)


class TestReportToStructured:
    def test_aggregate_with_triggered_reports(self):
        reports = [
            ScriptSanityReport(
                file_path="a.txt",
                language_hint="tr",
                total_chars=100,
                out_of_script_chars=20,
                ratio=0.20,
                threshold=0.015,
                triggered=True,
                top_offenders=(("ø", 10), ("÷", 10)),
                cause_hint="pypdf font fallback (Latin-1 substitutes)",
            ),
            ScriptSanityReport(
                file_path="b.txt",
                language_hint="tr",
                total_chars=100,
                out_of_script_chars=0,
                ratio=0.0,
                threshold=0.015,
                triggered=False,
            ),
        ]
        agg = report_to_structured(reports)
        assert agg["files_checked"] == 2
        assert agg["files_triggered"] == 1
        assert agg["max_ratio"] == pytest.approx(0.20)
        assert "per_file" in agg
        assert agg["per_file"][0]["file_path"] == "a.txt"

    def test_aggregate_with_no_triggered_reports(self):
        reports = [
            ScriptSanityReport(
                file_path="clean.txt",
                language_hint="tr",
                total_chars=100,
                out_of_script_chars=0,
                ratio=0.0,
                threshold=0.015,
                triggered=False,
            )
        ]
        agg = report_to_structured(reports)
        assert agg["files_triggered"] == 0
        # No per_file when nothing triggered — keeps the JSON tight.
        assert "per_file" not in agg


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
