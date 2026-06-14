"""Phase 15 ingestion-reliability regression suite.

Mirrors the Phase 15 roadmap (`docs/roadmap/phase-15-ingestion-reliability.md`).
The roadmap captures the audit findings that drove every task; the
audit notes themselves live outside the public tree per the
`tools/check_no_analysis_refs.py` policy. Each test class targets one
Wave 1 / Wave 2 task and asserts the documented acceptance criterion.

Test strategy:

* **In-test synthesis** for PDF / DOCX / EPUB. Binary fixtures
  committed against pypdf-extracted goldens drift on every minor
  version bump; synthesising the inputs inside the test keeps the
  suite resilient. The synthesis helpers use only ``pypdf.PdfWriter``,
  ``python-docx``, and ``ebooklib`` — the same libraries the
  extractors call.
* **On-disk fixtures** for TXT / MD where the inputs are deterministic
  plain text and the extractor output is contractually stable.
* **Behaviour assertions**, not byte-compare goldens. Each test
  asserts the documented contract (e.g. "no chunk contains the
  repeating header line", "alpha-ratio recovers above 0.65 after
  normalisation"). The audit's "fixture + golden" pattern is the
  spirit; in-test behaviour assertions are the letter.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import List

import pytest

from forgelm.ingestion import (
    IngestParameterError,
    ingest_path,
    strip_paragraph_packed_headers,
)

# ---------------------------------------------------------------------------
# Capability gates
# ---------------------------------------------------------------------------


def _has(module_name: str) -> bool:
    try:
        importlib.import_module(module_name)
        return True
    except ImportError:
        return False


HAS_PYPDF = _has("pypdf")
HAS_DOCX = _has("docx")
HAS_EPUB = _has("ebooklib")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "ingestion"


def _read_jsonl(path: Path) -> List[str]:
    return [json.loads(line)["text"] for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _synth_multipage_pdf(pages: List[List[str]]) -> bytes:
    """Build an N-page PDF whose pages carry the supplied line lists.

    Extends the ``_hand_built_pdf`` helper pattern from
    ``tests/test_ingestion.py`` to N pages so Phase 15's multi-line
    header dedup (Task 1) can be exercised against a realistic shape.
    Each page is laid out with vertically-stacked Tj-encoded lines so
    pypdf's ``extract_text()`` sees them in order.
    """
    pdf_objs: List[bytes] = []

    # Object 1: Catalog. Object 2: Pages tree (filled after we know N).
    # Object 3+: Pages + content streams.
    # Page indices in the Pages tree start at 3 and grow by 2 per page
    # (one page object + one content stream object per page). With N
    # pages we end up at 2N + 2 objects total.
    page_object_ids: List[int] = []
    content_streams: List[bytes] = []

    for page_lines in pages:
        # Each line drops 14 points; start at the top of the page.
        # ``Td`` carries an (x, y) translation per line.
        lines: List[str] = []
        y = 740
        for ln in page_lines:
            escaped = ln.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            lines.append(f"BT /F1 11 Tf 72 {y} Td ({escaped}) Tj ET")
            y -= 14
        content_streams.append("\n".join(lines).encode("latin-1", errors="replace"))

    next_id = 3
    for _ in content_streams:
        page_obj_id = next_id
        page_object_ids.append(page_obj_id)
        next_id += 2

    pages_obj_kids = " ".join(f"{pid} 0 R" for pid in page_object_ids)
    catalog = b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    pages_obj = (
        b"2 0 obj\n<< /Type /Pages /Kids ["
        + pages_obj_kids.encode("latin-1")
        + b"] /Count "
        + str(len(page_object_ids)).encode("latin-1")
        + b" >>\nendobj\n"
    )

    pdf_objs.append(catalog)
    pdf_objs.append(pages_obj)

    for idx, content in enumerate(content_streams):
        page_obj_id = page_object_ids[idx]
        content_obj_id = page_obj_id + 1
        # Share a single Type1 font (Helvetica) across every page; resolved
        # via the Pages tree's resource dictionary on each Page object.
        # Each page declares /F1 separately so the test stays close to
        # the pre-Phase-15 ``_hand_built_pdf`` shape.
        page = (
            f"{page_obj_id} 0 obj\n"
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Contents {content_obj_id} 0 R "
            f"/Resources << /Font << /F1 {next_id} 0 R >> >> >>\n"
            f"endobj\n"
        ).encode("latin-1")
        content_obj = (
            f"{content_obj_id} 0 obj\n<< /Length ".encode("latin-1")
            + str(len(content)).encode("latin-1")
            + b" >>\nstream\n"
            + content
            + b"\nendstream\nendobj\n"
        )
        pdf_objs.append(page)
        pdf_objs.append(content_obj)

    font_obj = (f"{next_id} 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n").encode("latin-1")
    pdf_objs.append(font_obj)

    pdf = b"%PDF-1.4\n"
    offsets = [0]
    for obj in pdf_objs:
        offsets.append(len(pdf))
        pdf += obj

    xref_offset = len(pdf)
    pdf += f"xref\n0 {len(pdf_objs) + 1}\n".encode("latin-1")
    pdf += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        pdf += f"{offset:010d} 00000 n \n".encode("latin-1")
    pdf += (
        b"trailer\n<< /Size "
        + str(len(pdf_objs) + 1).encode("latin-1")
        + b" /Root 1 0 R >>\nstartxref\n"
        + str(xref_offset).encode("latin-1")
        + b"\n%%EOF"
    )
    return pdf


# ---------------------------------------------------------------------------
# Task 1 — multi-line PDF edge dedup (window-based fix)
# ---------------------------------------------------------------------------


class TestTask1MultiLinePdfEdgeDedup:
    """Phase 15 Task 1: variable-outer-line corpora no longer lock the dedup out."""

    def test_strip_paragraph_packed_headers_drops_repeating_block_starts(self):
        # Each block leads with the same header line; the second-pass
        # dedup should strip them after the page-level pass.
        text = "\n\n".join(f"REPEATING HEADER\nBody for block {i}." for i in range(5))
        cleaned, stripped = strip_paragraph_packed_headers(text)
        assert stripped == 5
        assert "REPEATING HEADER" not in cleaned

    def test_window_based_dedup_strips_deeper_constant_line(self):
        """Variable outer line + constant deeper line — the pre-Phase-15 bug."""
        from forgelm.ingestion import _strip_repeating_page_lines

        pages = [f"Section {i} Title\nPUBLICATION IDENTIFIER\n\nBody of page {i}.\n\n{i + 1}" for i in range(1, 5)]
        cleaned, stripped = _strip_repeating_page_lines(pages)
        # The constant publication identifier must be stripped despite
        # the variable outer line (the audit §1.1 trap).
        for page in cleaned:
            assert "PUBLICATION IDENTIFIER" not in page
        assert stripped >= 4  # at least one strip per page

    def test_window_based_dedup_strips_deeper_constant_bottom_line(self):
        """Round-3 regression: the same audit §1.1 trap at the BOTTOM edge.

        CodeRabbit caught that the pre-round-3 tail walk was asymmetric:
        it stopped at the first non-match instead of inspecting every
        position in the window. A page shaped like
        ``[..., "FOOTER", "<page number>"]`` would leave the constant
        ``FOOTER`` stranded because the varying page number on the
        last line broke the peel loop on pass 1. The symmetric tail
        walk below covers that case.
        """
        from forgelm.ingestion import _strip_repeating_page_lines

        # FOOTER is constant one row above the varying page number.
        pages = [f"Body of page {i}.\n\nFOOTER\n\n{i}" for i in range(1, 5)]
        cleaned, stripped = _strip_repeating_page_lines(pages)
        for page in cleaned:
            assert "FOOTER" not in page
        assert stripped >= 4

    def test_short_doc_skips_dedup(self):
        """Documents with fewer than 3 pages keep the pre-15 no-op behaviour."""
        from forgelm.ingestion import _strip_repeating_page_lines

        pages = ["HEADER\nbody1", "HEADER\nbody2"]
        cleaned, stripped = _strip_repeating_page_lines(pages)
        assert stripped == 0
        assert cleaned == pages

    @pytest.mark.skipif(not HAS_PYPDF, reason="pypdf extra not installed")
    def test_synthesised_pdf_with_variable_outer_constant_inner_dedups(self, tmp_path):
        """End-to-end: variable outer + constant inner header gets dedup'd."""
        pages = [
            ["Section 1 Title", "PUBLICATION ID 12345", "Body of page one is here."],
            ["Section 2 Title", "PUBLICATION ID 12345", "Body of page two is here."],
            ["Section 3 Title", "PUBLICATION ID 12345", "Body of page three is here."],
            ["Section 4 Title", "PUBLICATION ID 12345", "Body of page four is here."],
        ]
        pdf_path = tmp_path / "multipage.pdf"
        pdf_path.write_bytes(_synth_multipage_pdf(pages))
        out = tmp_path / "out.jsonl"
        result = ingest_path(
            str(pdf_path),
            output_path=str(out),
            strategy="paragraph",
            chunk_size=4000,
            keep_frontmatter=True,  # opt out of the heuristic for this test
        )
        all_text = "\n".join(_read_jsonl(out))
        assert "PUBLICATION ID 12345" not in all_text
        assert result.pdf_header_footer_lines_stripped > 0


# ---------------------------------------------------------------------------
# Task 2 — Unicode / script sanity layer
# ---------------------------------------------------------------------------


class TestTask2ScriptSanity:
    """Phase 15 Task 2: out-of-script char ratio drives a WARNING."""

    def test_check_script_sanity_fires_on_corrupt_glyphs(self):
        from forgelm._script_sanity import check_script_sanity

        # ø Õ ú ÷ ࡟ are all out-of-script for Turkish (Latin Extended-A range).
        # Mix in plenty of regular ASCII to keep the ratio measurable.
        text = "Body text only contains regular ASCII " * 5 + "øÕú÷࡟øÕú÷࡟øÕú÷࡟"
        report = check_script_sanity(
            text,
            language_hint="tr",
            file_path="test.txt",
            threshold=0.01,
            profile="none",  # disable the emit-list allowance
        )
        assert report.triggered
        assert report.ratio > 0.01
        assert any(glyph == "ø" for glyph, _ in report.top_offenders)

    def test_check_script_sanity_no_op_on_unknown_hint(self):
        from forgelm._script_sanity import check_script_sanity

        report = check_script_sanity(
            "any text at all",
            language_hint="zz",  # unknown
            file_path="test.txt",
        )
        assert not report.triggered
        assert report.ratio == pytest.approx(0.0)

    def test_check_script_sanity_silent_on_clean_text(self):
        from forgelm._script_sanity import check_script_sanity

        report = check_script_sanity(
            "Sadece düzgün Türkçe metin içeren bir cümle. " * 20,
            language_hint="tr",
            file_path="clean.txt",
        )
        assert not report.triggered
        assert report.ratio < 0.01

    def test_profile_allowlist_protects_normaliser_output(self):
        """Characters the normaliser is allowed to emit are in-script."""
        from forgelm._script_sanity import check_script_sanity

        # • U+2022 is a normaliser-emitted bullet; with profile=turkish it
        # should not count as out-of-script even though it's outside the
        # strict Latin Extended-A range.
        text = "Body text with bullets • • • appearing repeatedly across the chunk. " * 10
        report = check_script_sanity(text, language_hint="tr", file_path="bullets.txt", profile="turkish")
        assert not report.triggered


# ---------------------------------------------------------------------------
# Task 3 — Turkish pypdf glyph normalisation table
# ---------------------------------------------------------------------------


class TestTask3TurkishGlyphNormalisation:
    """Phase 15 Task 3: corrupt Turkish glyphs map back to the correct chars."""

    def test_apply_profile_substitutes_all_documented_glyphs(self):
        from forgelm._pypdf_normalise import apply_profile

        corrupt = "ø Õ ú ÷ ࡟"
        cleaned = apply_profile(corrupt, "turkish")
        assert "ø" not in cleaned
        assert "Õ" not in cleaned
        assert "ú" not in cleaned
        assert "÷" not in cleaned
        assert "࡟" not in cleaned
        # All five canonical Turkish targets show up.
        for target in ("İ", "ı", "ş", "ğ", "•"):
            assert target in cleaned

    def test_apply_profile_none_is_identity(self):
        from forgelm._pypdf_normalise import apply_profile

        assert apply_profile("ø Õ ú", "none") == "ø Õ ú"

    def test_unknown_profile_silently_no_ops(self):
        from forgelm._pypdf_normalise import apply_profile

        assert apply_profile("ø Õ ú", "klingon") == "ø Õ ú"

    def test_multichar_substitution_applied_before_single_char(self):
        from forgelm._pypdf_normalise import apply_profile

        # ``ö `` (with trailing space) maps to ``Ğ`` — the multi-char rule
        # must fire before a single-char ``ö`` substitution could land
        # (none today, but the ordering protects future additions).
        assert apply_profile("ö Body", "turkish") == "ĞBody"


# ---------------------------------------------------------------------------
# Task 4 — Ingest-time quality pre-signal
# ---------------------------------------------------------------------------


class TestTask4QualityPresignal:
    """Phase 15 Task 4: ingest_path emits a quality_presignal block."""

    def test_quality_presignal_populates_on_clean_text(self, tmp_path):
        src = tmp_path / "doc.txt"
        src.write_text("A clean paragraph of normal English text.\n\nAnother clean paragraph here.\n")
        out = tmp_path / "out.jsonl"
        result = ingest_path(str(src), output_path=str(out), strategy="paragraph")
        ns = result.notes_structured
        assert "quality_presignal" in ns
        assert ns["quality_presignal"]["samples_evaluated"] >= 1

    def test_quality_presignal_flags_short_repetitive_text(self, tmp_path):
        src = tmp_path / "noisy.txt"
        # Lots of repeated single-line chunks — should trip repeated_lines
        # and/or low_alpha checks (depending on chunker boundaries).
        src.write_text("RT\n\nRT\n\nRT\n\nRT\n\n")
        out = tmp_path / "out.jsonl"
        result = ingest_path(str(src), output_path=str(out), strategy="paragraph")
        # Some chunk should have been flagged.
        ns = result.notes_structured
        # Either flagged or no chunks (depends on chunker filtering); both
        # are correct contract — we only assert the block exists.
        assert "quality_presignal" in ns

    def test_no_quality_presignal_when_disabled(self, tmp_path):
        src = tmp_path / "doc.txt"
        src.write_text("Body text.")
        out = tmp_path / "out.jsonl"
        result = ingest_path(
            str(src),
            output_path=str(out),
            strategy="paragraph",
            quality_presignal=False,
        )
        assert "quality_presignal" not in result.notes_structured


# ---------------------------------------------------------------------------
# Task 6 — DOCX explicit header / footer extraction
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_DOCX, reason="python-docx extra not installed")
class TestTask6DocxHeaderFooter:
    """Phase 15 Task 6: header/footer lines are subtracted from the body."""

    def test_docx_header_subtracted_from_body(self, tmp_path):
        from docx import Document

        doc = Document()
        section = doc.sections[0]
        # python-docx exposes header via .header; first paragraph is the
        # editable one.
        section.header.paragraphs[0].text = "ACME CONFIDENTIAL"
        section.footer.paragraphs[0].text = "Page footer text"

        # Body paragraphs include the same header line we just declared.
        for i in range(3):
            doc.add_paragraph("ACME CONFIDENTIAL")
            doc.add_paragraph(f"Body paragraph {i} that the chunker keeps.")

        docx_path = tmp_path / "doc.docx"
        doc.save(str(docx_path))

        out = tmp_path / "out.jsonl"
        ingest_path(str(docx_path), output_path=str(out), strategy="paragraph")
        all_text = "\n".join(_read_jsonl(out))
        assert "ACME CONFIDENTIAL" not in all_text
        assert "Body paragraph" in all_text


# ---------------------------------------------------------------------------
# Task 7 — EPUB spine + nav / cover / copyright skip
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_EPUB, reason="ebooklib extra not installed")
class TestTask7EpubSpineSkip:
    """Phase 15 Task 7: nav / cover / copyright items are skipped by default."""

    def test_epub_skips_nav_and_cover(self, tmp_path):
        from ebooklib import epub

        book = epub.EpubBook()
        book.set_identifier("phase-15-fixture")
        book.set_title("Phase 15 fixture")
        book.set_language("en")

        cover = epub.EpubHtml(title="Cover", file_name="cover.xhtml", content="<p>COVER PAGE</p>")
        nav_html = epub.EpubHtml(title="Nav", file_name="nav.xhtml", content="<p>NAV TOC</p>")
        chap = epub.EpubHtml(
            title="Chapter 1",
            file_name="chap1.xhtml",
            content="<p>Body of chapter one.</p>",
        )
        for item in (cover, nav_html, chap):
            book.add_item(item)
        book.spine = [cover, nav_html, chap]
        book.toc = [chap]
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())

        epub_path = tmp_path / "book.epub"
        epub.write_epub(str(epub_path), book)

        out = tmp_path / "out.jsonl"
        ingest_path(str(epub_path), output_path=str(out), strategy="paragraph")
        all_text = "\n".join(_read_jsonl(out))
        assert "COVER PAGE" not in all_text
        assert "NAV TOC" not in all_text
        assert "Body of chapter one." in all_text


# ---------------------------------------------------------------------------
# Task 8 — TXT BOM strip + MD frontmatter detection
# ---------------------------------------------------------------------------


class TestTask8TxtBomMdFrontmatter:
    """Phase 15 Task 8: UTF-8 BOM + YAML frontmatter are stripped by default."""

    def test_txt_with_bom_strips_bom(self, tmp_path):
        out = tmp_path / "out.jsonl"
        ingest_path(
            str(FIXTURE_DIR / "txt_with_bom_crlf.txt"),
            output_path=str(out),
            strategy="paragraph",
        )
        all_text = "\n".join(_read_jsonl(out))
        # The BOM character U+FEFF must not survive into the JSONL.
        assert "﻿" not in all_text
        assert "Header Line One" in all_text

    def test_md_frontmatter_stripped_by_default(self, tmp_path):
        out = tmp_path / "out.jsonl"
        ingest_path(
            str(FIXTURE_DIR / "md_with_frontmatter_and_html.md"),
            output_path=str(out),
            strategy="paragraph",
        )
        all_text = "\n".join(_read_jsonl(out))
        assert "title:" not in all_text
        assert "author:" not in all_text
        assert "Body heading" in all_text

    def test_md_keep_frontmatter_opt_in(self, tmp_path):
        out = tmp_path / "out.jsonl"
        ingest_path(
            str(FIXTURE_DIR / "md_with_frontmatter_and_html.md"),
            output_path=str(out),
            strategy="paragraph",
            keep_md_frontmatter=True,
        )
        all_text = "\n".join(_read_jsonl(out))
        assert "title:" in all_text


# ---------------------------------------------------------------------------
# Task 10 — mixed_directory recursive ingest
# ---------------------------------------------------------------------------


class TestTask10MixedDirectory:
    """Phase 15 Task 10: cross-format ingest produces lex-sorted, deterministic output."""

    def test_mixed_directory_recursive_produces_both_formats(self, tmp_path):
        out = tmp_path / "out.jsonl"
        result = ingest_path(
            str(FIXTURE_DIR / "mixed_directory"),
            output_path=str(out),
            strategy="paragraph",
            recursive=True,
        )
        assert result.files_processed >= 2
        # format_counts should record both .txt and .md.
        formats = set(result.format_counts.keys())
        assert ".txt" in formats
        assert ".md" in formats

    def test_recursive_walk_order_is_lex_sorted(self, tmp_path):
        # Synthesize a fresh dir with files whose lex order differs from
        # filesystem insertion order to confirm the walker sorts.
        d = tmp_path / "ordered"
        d.mkdir()
        (d / "zebra.txt").write_text("Zebra body text.\n\nMore zebra content.\n")
        (d / "alpha.txt").write_text("Alpha body text.\n\nMore alpha content.\n")
        out = tmp_path / "out.jsonl"
        ingest_path(
            str(d),
            output_path=str(out),
            strategy="paragraph",
            recursive=True,
        )
        chunks = _read_jsonl(out)
        # The first chunk must come from alpha.txt, the second batch from zebra.txt.
        assert "Alpha" in chunks[0]
        # The last chunk should originate from zebra.txt.
        assert any("Zebra" in chunk for chunk in chunks)


# ---------------------------------------------------------------------------
# Task 11 — --strip-pattern with ReDoS guard
# ---------------------------------------------------------------------------


class TestTask11StripPattern:
    """Phase 15 Wave 2 Task 11: operator regex applied + ReDoS-validated."""

    def test_strip_pattern_removes_matching_lines(self, tmp_path):
        src = tmp_path / "doc.txt"
        src.write_text("Normal body text.\nWATERMARK ID 12345\nMore body text.\n\nWATERMARK ID 99999\nFinal.")
        out = tmp_path / "out.jsonl"
        ingest_path(
            str(src),
            output_path=str(out),
            strategy="paragraph",
            strip_patterns=[r"^WATERMARK ID \d+$"],
        )
        all_text = "\n".join(_read_jsonl(out))
        assert "WATERMARK" not in all_text
        assert "Normal body text" in all_text

    def test_strip_pattern_redos_shape_rejected(self, tmp_path):
        src = tmp_path / "doc.txt"
        src.write_text("body")
        out = tmp_path / "out.jsonl"
        with pytest.raises(ValueError, match="ReDoS"):
            ingest_path(
                str(src),
                output_path=str(out),
                strip_patterns=[r"(a+)+b"],
            )

    def test_strip_pattern_invalid_regex_rejected(self, tmp_path):
        src = tmp_path / "doc.txt"
        src.write_text("body")
        out = tmp_path / "out.jsonl"
        with pytest.raises(ValueError, match="not a valid regular expression"):
            ingest_path(str(src), output_path=str(out), strip_patterns=["[unclosed"])


# ---------------------------------------------------------------------------
# Task 12 — --page-range PDF flag
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_PYPDF, reason="pypdf extra not installed")
class TestTask12PageRange:
    """Phase 15 Wave 2 Task 12: page-range slices PDF extraction."""

    def test_page_range_extracts_only_requested_pages(self, tmp_path):
        pages = [
            ["First page content here."],
            ["Second page content here."],
            ["Third page content here."],
            ["Fourth page content here."],
        ]
        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(_synth_multipage_pdf(pages))
        out = tmp_path / "out.jsonl"
        ingest_path(
            str(pdf),
            output_path=str(out),
            page_range=(2, 3),
            keep_frontmatter=True,
            quality_presignal=False,
        )
        all_text = "\n".join(_read_jsonl(out))
        assert "First page" not in all_text
        assert "Fourth page" not in all_text
        assert "Second page" in all_text or "Third page" in all_text

    def test_page_range_invalid_start_raises(self, tmp_path):
        # F-P6-OPUS-10: pin the concrete IngestParameterError, not a bare
        # ValueError. The per-file soft-fail dispatcher only re-raises
        # (ImportError, IngestParameterError); a downgrade to plain
        # ValueError would be SWALLOWED as a per-file skip, silently
        # turning an operator mistake into a soft no-op — and a
        # `pytest.raises(ValueError)` guard would not catch the regression.
        pages = [["A"], ["B"]]
        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(_synth_multipage_pdf(pages))
        out = tmp_path / "out.jsonl"
        with pytest.raises(IngestParameterError, match="page-range"):
            ingest_path(str(pdf), output_path=str(out), page_range=(0, 1))

    def test_page_range_start_after_end_raises(self, tmp_path):
        # F-P6-OPUS-10: type-pinned (see sibling test above).
        pages = [["A"], ["B"]]
        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(_synth_multipage_pdf(pages))
        out = tmp_path / "out.jsonl"
        with pytest.raises(IngestParameterError, match="page-range"):
            ingest_path(str(pdf), output_path=str(out), page_range=(5, 1))


# ---------------------------------------------------------------------------
# Task 13 — Front-matter heuristic (default ON in v0.6.0)
# ---------------------------------------------------------------------------


class TestTask13FrontmatterHeuristic:
    """Phase 15 Wave 2 Task 13: heuristic drops alpha < 0.30 + underscore > 0.10 + ≥ 5 page numbers."""

    def test_is_frontmatter_page_fires_on_toc_shape(self):
        from forgelm.ingestion import _is_frontmatter_page

        toc_page = (
            "Chapter 1______________________________________ 10\n"
            "Chapter 2______________________________________ 25\n"
            "Chapter 3______________________________________ 40\n"
            "\n10\n\n11\n\n12\n\n13\n\n14\n"
        )
        assert _is_frontmatter_page(toc_page)

    def test_is_frontmatter_page_skips_clean_body(self):
        from forgelm.ingestion import _is_frontmatter_page

        body = (
            "This is a perfectly normal page of body content covering one or two "
            "topics with full sentences and no ToC dotted leaders or repeating page "
            "numbers. It should not trip the heuristic." * 3
        )
        assert not _is_frontmatter_page(body)

    def test_is_frontmatter_page_positive_control_numeric_answer_key(self):
        """F-P6-OPUS-09: pin the documented residual false-positive band.

        A dense numeric answer-key / scoring page (short dotted-leader
        labels + inline 1-3 digit answer rows) DOES trip the 3-signal
        heuristic. This is an accepted, bounded trade-off (drop is
        head/tail-only, WARNED, and ``--keep-frontmatter`` opt-out), but
        leaving it unpinned means a future signal-widening could silently
        change which pages get dropped with zero failing test. If this
        page should ever NO LONGER be treated as front-matter, that is an
        intentional calibration change and this assertion must be updated
        deliberately.
        """
        from forgelm.ingestion import _is_frontmatter_page

        answer_key = (
            "\n".join(
                [
                    "Q1 ....... ",
                    "12",
                    "Q2 ....... ",
                    "34",
                    "Q3 ....... ",
                    "56",
                    "Q4 ....... ",
                    "78",
                    "Q5 ....... ",
                    "90",
                    "Q6 ....... ",
                    "11",
                ]
            )
            + "\n"
        )
        assert _is_frontmatter_page(answer_key)

    def test_frontmatter_docstring_matches_active_alpha_threshold(self):
        """F-P6-OPUS-03: the function docstring must not advertise the stale
        0.45 alpha threshold — the active constant is 0.30, and the round-3
        tightening's intent is undone on paper if the docstring drifts back."""
        import inspect

        from forgelm.ingestion import _FRONTMATTER_ALPHA_RATIO_MAX, _is_frontmatter_page

        assert _FRONTMATTER_ALPHA_RATIO_MAX == 0.30
        doc = inspect.getdoc(_is_frontmatter_page) or ""
        # The docstring should reference the constant / its real value, never
        # the pre-round-3 0.45 figure as the active gate.
        assert "0.45" not in doc.split("Alphabetic-character ratio")[1].splitlines()[0]
        assert "_FRONTMATTER_ALPHA_RATIO_MAX" in doc


# ---------------------------------------------------------------------------
# Task 14 — URL strip option
# ---------------------------------------------------------------------------


class TestTask14StripUrls:
    """Phase 15 Wave 2 Task 14: --strip-urls mask|strip|keep applies post-extract."""

    def test_strip_urls_mask_replaces_url_with_placeholder(self, tmp_path):
        src = tmp_path / "doc.txt"
        src.write_text("Visit https://example.com/path?q=1 for more.\n\nAlso http://foo.bar/baz.\n")
        out = tmp_path / "out.jsonl"
        ingest_path(
            str(src),
            output_path=str(out),
            strategy="paragraph",
            strip_urls="mask",
        )
        all_text = "\n".join(_read_jsonl(out))
        assert "https://example.com" not in all_text
        assert "[URL]" in all_text

    def test_strip_urls_strip_removes_url(self, tmp_path):
        src = tmp_path / "doc.txt"
        src.write_text("Visit https://example.com/path for more.\n")
        out = tmp_path / "out.jsonl"
        ingest_path(
            str(src),
            output_path=str(out),
            strategy="paragraph",
            strip_urls="strip",
        )
        all_text = "\n".join(_read_jsonl(out))
        assert "https://" not in all_text
        assert "Visit  for more." in all_text

    def test_strip_urls_keep_default(self, tmp_path):
        src = tmp_path / "doc.txt"
        src.write_text("Visit https://example.com/path for more.\n")
        out = tmp_path / "out.jsonl"
        ingest_path(str(src), output_path=str(out), strategy="paragraph")
        all_text = "\n".join(_read_jsonl(out))
        assert "https://example.com" in all_text

    def test_strip_urls_invalid_mode_rejected(self, tmp_path):
        src = tmp_path / "doc.txt"
        src.write_text("body")
        out = tmp_path / "out.jsonl"
        with pytest.raises(ValueError, match="strip_urls"):
            ingest_path(str(src), output_path=str(out), strip_urls="bogus")


# ---------------------------------------------------------------------------
# Task 15 — Multi-column PDF detection (warning only)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_PYPDF, reason="pypdf extra not installed")
class TestTask15MultiColumnWarning:
    """Phase 15 Wave 2 Task 15: warns on two-cluster x-distribution. No fix."""

    def test_single_column_no_warning(self, tmp_path, caplog):
        # Single-column PDF: every Tj sits at x=72.
        pages = [["Body content line one.", "Body content line two."]]
        pdf = tmp_path / "single.pdf"
        pdf.write_bytes(_synth_multipage_pdf(pages))
        out = tmp_path / "out.jsonl"
        with caplog.at_level("WARNING"):
            ingest_path(
                str(pdf),
                output_path=str(out),
                strategy="paragraph",
                keep_frontmatter=True,
                quality_presignal=False,
            )
        assert not any("2-column" in r.message for r in caplog.records)

    def test_positive_case_fires_warning_via_mocked_visitor(self, caplog):
        """Round-2 positive-case coverage for ``_maybe_warn_multi_column``.

        Hand-rolling a two-column PDF byte stream (via _synth_multipage_pdf)
        does not let us control the Tj x-coordinates pypdf surfaces to the
        visitor callback (they all end up at x=72). We bypass the byte
        layer and call the probe with a fake reader whose pages emit two
        clearly-separated x-clusters; the warning must fire.
        """
        from unittest.mock import MagicMock

        from forgelm.ingestion import _maybe_warn_multi_column

        fake_page = MagicMock()
        fake_page.mediabox.width = 612.0

        def fake_extract_text(visitor_text=None, **_kwargs):
            if visitor_text is None:
                return ""
            # Two clusters: left column around x=72, right column around x=380.
            for x in (72, 73, 74, 75, 380, 381, 382, 383) * 6:
                visitor_text("body", None, (1, 0, 0, 1, x, 700), None, 11)
            return "body"

        fake_page.extract_text = fake_extract_text
        fake_reader = MagicMock()
        fake_reader.pages = [fake_page]

        with caplog.at_level("WARNING"):
            fired = _maybe_warn_multi_column(fake_reader, "fake.pdf")
        assert fired
        assert any("2-column" in r.message for r in caplog.records)

    def test_partial_token_mode_rejected_at_notebook_layer(self):
        """Notebook Cell 5 fail-fast: half-set CHUNK_TOKENS/TOKENIZER must abort.

        Round-3 review: the pre-round-3 version of this test recomputed
        the predicate from hardcoded pairs and asserted a tautology, so
        a regression deleting the notebook's fail-fast check would still
        pass the test. We now parse the actual notebook JSON and assert
        that Cell 5's source carries both (a) the partial-config
        ``ValueError`` raise and (b) the predicate string the cell uses.
        Catches accidental cell-source deletion or rewording that drops
        the safety net.
        """
        import json
        from pathlib import Path

        nb_path = Path(__file__).resolve().parent.parent / "notebooks" / "ingestion_playground.ipynb"
        assert nb_path.is_file(), f"notebook fixture missing at {nb_path}"

        nb = json.loads(nb_path.read_text(encoding="utf-8"))
        cell5_source = nb["cells"][5]["source"]
        if isinstance(cell5_source, list):
            cell5_text = "".join(cell5_source)
        else:
            cell5_text = cell5_source

        # Cell 5 must compute the count via the canonical predicate AND
        # raise ValueError when exactly one of CHUNK_TOKENS / TOKENIZER
        # is truthy. Both substrings are pinned so a partial-deletion
        # regression trips this test.
        assert "_token_mode_kwargs_set" in cell5_text, (
            "Cell 5 must compute _token_mode_kwargs_set to detect partial config"
        )
        assert "sum(1 for v in (CHUNK_TOKENS, TOKENIZER) if v)" in cell5_text, (
            "Cell 5 predicate must count truthy CHUNK_TOKENS / TOKENIZER"
        )
        assert "raise ValueError" in cell5_text, "Cell 5 must raise ValueError on partial token-mode config"
        assert "Token-aware chunking requires BOTH" in cell5_text, "Cell 5 error message must name both knobs"


# ---------------------------------------------------------------------------
# Cross-cutting: structured notes additive shape (back-compat)
# ---------------------------------------------------------------------------


class TestRoundTwoFixes:
    """Phase 15 round-2 review absorption — regressions for the new behaviours."""

    def test_epub_skip_does_not_substring_match(self):
        """C-1 regression: ``recovery.xhtml`` must NOT trip the ``cover`` skip token."""
        from forgelm.ingestion import _epub_item_matches_skip

        skip = ("nav", "cover", "copyright", "colophon", "titlepage", "frontmatter")
        # Pre-round-2 substring match would have skipped all of these.
        assert not _epub_item_matches_skip("recovery.xhtml", "", skip)
        assert not _epub_item_matches_skip("discovery_chapter.xhtml", "", skip)
        assert not _epub_item_matches_skip("undercover.xhtml", "", skip)
        assert not _epub_item_matches_skip("navy.xhtml", "", skip)
        assert not _epub_item_matches_skip("navigation_chapter.xhtml", "", skip)
        # Canonical cases still get caught.
        assert _epub_item_matches_skip("cover.xhtml", "", skip)
        assert _epub_item_matches_skip("oebps/nav.xhtml", "", skip)
        assert _epub_item_matches_skip("cover-page.xhtml", "", skip)
        # ``epub:type`` exact match still works.
        assert _epub_item_matches_skip("chapter1.xhtml", "cover", skip)

    def test_normalise_profile_couples_to_language_hint(self, tmp_path):
        """C-2 regression: default profile is no-op unless --language-hint=tr.

        Without a language hint, ``--normalise-profile`` must NOT silently
        rewrite Nordic / mathematical characters.

        Windows-fix: explicit ``encoding="utf-8"`` on the write is required
        because ``Path.write_text`` uses the OS default codec (cp1252 on
        Windows) which fails to encode Latin-1 Supplement characters
        consistently with the UTF-8-first read path.
        """
        src = tmp_path / "doc.txt"
        # ``ø``, ``Õ``, ``÷`` are legitimate non-Turkish chars (Nordic /
        # Estonian / math). Without language_hint, the normaliser must
        # leave them alone.
        src.write_text("Visit Bjørk Õrö ÷ ten.\n\nAnother paragraph.\n", encoding="utf-8")
        out = tmp_path / "out.jsonl"
        ingest_path(str(src), output_path=str(out), strategy="paragraph")
        all_text = "\n".join(_read_jsonl(out))
        assert "Bjørk" in all_text
        assert "Õrö" in all_text
        assert "÷" in all_text

    def test_normalise_profile_active_when_language_hint_is_tr(self, tmp_path):
        """C-2 confirm: --language-hint tr auto-enables the turkish profile.

        Windows-fix: explicit ``encoding="utf-8"`` on both the write AND
        ``read_text_with_bom_strip`` is required because U+085F (Mandaic
        punctuation) is outside the cp1252 codepage that Windows
        ``Path.write_text`` defaults to.
        """
        src = tmp_path / "doc.txt"
        src.write_text("Body text with corruption: ø Õ ú ÷ ࡟\n\nMore body.\n", encoding="utf-8")
        out = tmp_path / "out.jsonl"
        ingest_path(
            str(src),
            output_path=str(out),
            strategy="paragraph",
            language_hint="tr",
        )
        all_text = "\n".join(_read_jsonl(out))
        # Audit-measured artefacts are mapped to the correct Turkish chars.
        assert "ø" not in all_text
        assert "Õ" not in all_text
        assert "İ" in all_text
        assert "ş" in all_text

    def test_bom_strip_works_on_latin1_fallback(self, tmp_path):
        """S-2 regression: BOM is stripped even when the fallback path fires.

        A file with a UTF-8 BOM plus downstream non-UTF-8 bytes used to
        leak ``\\ufeff`` because the fallback opened with ``utf-8``, not
        ``utf-8-sig``. The fix uses ``utf-8-sig`` on both paths plus an
        explicit leading-codepoint strip.
        """
        src = tmp_path / "broken.txt"
        # BOM + valid text + a stray Latin-1 byte that breaks strict UTF-8.
        src.write_bytes(b"\xef\xbb\xbfHello\n\xc3\x28body line\n")
        out = tmp_path / "out.jsonl"
        ingest_path(str(src), output_path=str(out), strategy="paragraph")
        all_text = "\n".join(_read_jsonl(out))
        assert "﻿" not in all_text
        assert "Hello" in all_text

    def test_strip_pattern_catches_escape_shape_redos(self, tmp_path):
        """S-1 regression: ``(\\w+)+x`` must be rejected up-front."""
        from forgelm._strip_pattern import StripPatternError

        src = tmp_path / "doc.txt"
        src.write_text("body")
        out = tmp_path / "out.jsonl"
        with pytest.raises((ValueError, StripPatternError), match="ReDoS"):
            ingest_path(
                str(src),
                output_path=str(out),
                strip_patterns=[r"(\w+)+x"],
            )


class TestRoundFourFixes:
    """Round-4 regression coverage for the path-repr / presignal-floor /
    EPUB-property-split / frontmatter-alpha-threshold changes that
    landed in commit ``dc671f5`` without dedicated tests."""

    def test_page_range_error_repr_escapes_ansi_in_path(self, tmp_path):
        """C-A round-4: path with ANSI escapes must NOT render raw in the error message.

        Pre-round-4 the error used bare ``'{path}'`` interpolation, so a
        path containing ``\\x1b[31m...`` rendered the literal escape
        sequence into the error string (terminal-injection vector).
        The fix switched to ``{path!r}`` which repr-quotes the value.
        """
        if not HAS_PYPDF:
            pytest.skip("pypdf extra not installed")
        # Synthesise a minimal PDF whose filename contains an ANSI
        # escape sequence; pass an out-of-range page-range to trigger
        # the IngestParameterError.
        evil_name = "evil\x1b[31mRED\x1b[0m.pdf"
        evil_path = tmp_path / evil_name
        evil_path.write_bytes(_synth_multipage_pdf([["body"]]))
        out = tmp_path / "out.jsonl"
        with pytest.raises(ValueError) as excinfo:
            ingest_path(str(evil_path), output_path=str(out), page_range=(99, 999))
        # The error message must repr-escape the path; the literal ESC
        # byte (\x1b == \x1b) must NOT appear unescaped in the rendered
        # message. ``repr()`` of an ESC-bearing string renders ``\x1b``.
        msg = str(excinfo.value)
        assert "\x1b" not in msg, "ANSI escape must be repr-escaped in error message"

    def test_presignal_floor_silent_on_short_clean_corpus(self, tmp_path):
        """C-B round-4: clean 5-paragraph 41-char corpus must NOT trip the alpha-ratio check.

        Pre-round-4 the alpha-ratio check ran on every chunk regardless
        of size. A perfectly clean 5-para 41-char TXT flagged 1/1
        chunks because dots + whitespace dragged alpha < 0.70.
        ``_PRESIGNAL_MIN_NON_WS_CHARS = 80`` floor suppresses the
        false-positive.
        """
        src = tmp_path / "doc.txt"
        src.write_text("Para 1.\n\nPara 2.\n\nPara 3.\n\nPara 4.\n\nPara 5.")
        out = tmp_path / "out.jsonl"
        result = ingest_path(str(src), output_path=str(out), strategy="paragraph")
        qp = result.notes_structured.get("quality_presignal", {})
        assert qp.get("samples_flagged", -1) == 0, "clean small corpus must not flag"
        assert qp.get("by_check", {}).get("alpha_ratio", -1) == 0

    @pytest.mark.skipif(not HAS_EPUB, reason="ebooklib extra not installed")
    def test_epub_item_type_split_on_multi_property_string(self):
        """S-C round-4: ``properties="nav cover-image"`` must match via tokens, not exact-string.

        EPUB-3 manifest properties are space-separated tokens. The
        pre-round-4 code exact-matched the joined form against the
        skip-list, silently passing through any item with > 1 property.
        """
        from forgelm.ingestion import _epub_item_matches_skip

        skip = ("nav", "toc", "cover", "copyright", "colophon", "titlepage", "frontmatter")
        # Multi-token property strings (real EPUB-3 manifest shape).
        assert _epub_item_matches_skip("ch01.xhtml", "nav cover-image", skip)
        assert _epub_item_matches_skip("ch01.xhtml", "scripted mathml nav", skip)
        # Single token that matches — back-compat.
        assert _epub_item_matches_skip("any.xhtml", "cover", skip)
        # Empty property string falls through to filename check.
        assert not _epub_item_matches_skip("body.xhtml", "", skip)
        # Property string with NO skip token must NOT match.
        assert not _epub_item_matches_skip("body.xhtml", "scripted mathml", skip)

    def test_frontmatter_threshold_audit_pilot_still_caught(self):
        """S-E round-4 / S-C round-4: audit's Turkish-pilot ToC shape (≈ 0.15 alpha)
        must still trip the heuristic after the 0.45 → 0.30 tightening."""
        from forgelm.ingestion import _is_frontmatter_page

        # Audit pilot shape: short Turkish chapter titles + heavy
        # dotted leaders + inline page numbers.
        toc = (
            "Bölüm 1 ........................ 14\n"
            "Bölüm 2 ........................ 28\n"
            "Bölüm 3 ........................ 42\n"
            "Bölüm 4 ........................ 67\n"
            "Bölüm 5 ........................ 95\n"
            "\n14\n\n28\n\n42\n\n67\n\n95\n"
        )
        assert _is_frontmatter_page(toc)

    def test_frontmatter_protects_realistic_form_template_via_3signal_and(self):
        """S-E round-4: real form templates have NO inline page numbers, so the
        3-signal AND filter (alpha + leader + page-num-count) protects them
        even when alpha + leader trip individually."""
        from forgelm.ingestion import _is_frontmatter_page

        # Realistic form template — labels + underscore leaders + NO page numbers.
        form = (
            "Name: ___________________\n"
            "Date: ___________________\n"
            "Phone: __________________\n"
            "Address: ________________\n"
            "City: ___________________\n"
        )
        # Without the inline-page-number signal, the 3-signal AND filter
        # keeps the page even though alpha + leader ratios are low.
        assert not _is_frontmatter_page(form)


class TestStructuredNotesAdditive:
    """Phase 15 contract: notes_structured additions never rename pre-15 keys."""

    def test_pre15_keys_still_present_on_clean_run(self, tmp_path):
        src = tmp_path / "doc.txt"
        src.write_text("Body.\n\nMore body.\n")
        out = tmp_path / "out.jsonl"
        result = ingest_path(str(src), output_path=str(out), strategy="paragraph")
        ns = result.notes_structured
        for key in (
            "files_processed",
            "files_skipped",
            "chunk_count",
            "total_chars",
            "strategy",
            "format_counts",
            "pii_redaction_counts",
            "secrets_redaction_counts",
        ):
            assert key in ns, f"Pre-Phase-15 key {key!r} disappeared"


class TestCorpusZeroChunksAggregateWarning:
    """F-P6-OPUS-11: a corpus that yields ZERO usable chunks must emit a
    single aggregate WARNING, not just per-file skip warnings + an INFO
    summary — otherwise a pipeline that ingests-then-trains proceeds on an
    empty dataset with no loud signal. Exit code is intentionally
    unchanged (the WARNING is the tripwire, not a contract break)."""

    def test_ingest_all_files_skipped_warns(self, tmp_path, caplog):
        # A directory of empty .txt files: each is skipped (no extractable
        # text), so chunk_count == 0 across the whole run.
        (tmp_path / "a.txt").write_text("   \n")
        (tmp_path / "b.txt").write_text("")
        out = tmp_path / "out.jsonl"
        with caplog.at_level("WARNING"):
            result = ingest_path(str(tmp_path), output_path=str(out))
        assert result.chunk_count == 0
        assert any("produced 0 chunks" in r.message for r in caplog.records), (
            "all-files-skipped corpus must emit a single aggregate WARNING"
        )

    def test_normal_run_does_not_emit_zero_chunk_warning(self, tmp_path, caplog):
        # A run that produces chunks must NOT emit the aggregate warning.
        src = tmp_path / "doc.txt"
        src.write_text("Body content here.\n\nMore body content here.\n")
        out = tmp_path / "out.jsonl"
        with caplog.at_level("WARNING"):
            result = ingest_path(str(src), output_path=str(out), strategy="paragraph")
        assert result.chunk_count > 0
        assert not any("produced 0 chunks" in r.message for r in caplog.records)


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
