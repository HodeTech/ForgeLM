"""Regression tests for Phase 12 review-cycle fixes.

These tests pin behaviour that the Phase 12 code review (2026-04-28)
flagged as wrong / regressed. Kept in a dedicated file so the original
Phase 12 acceptance suite (``test_data_audit_phase12.py`` /
``test_ingestion_phase12.py``) stays a clean record of the feature's
shape, while this file documents *what the review caught and how we
prevent it from coming back*.
"""

from __future__ import annotations

import json
from pathlib import Path

from forgelm.data_audit import (
    _row_quality_flags,
    audit_dataset,
    detect_secrets,
)
from forgelm.ingestion import (
    _chunk_markdown_tokens,
    _docx_table_to_markdown,
    _markdown_sections,
)


def _write_jsonl(path: Path, rows):
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# C1 — stdout JSON envelope: ``near_duplicate_pairs_per_split`` must remain
# present so v0.5.1 consumers (e.g. ``jq '.near_duplicate_pairs_per_split'``)
# don't break under v0.5.2.
# ---------------------------------------------------------------------------


class TestC1JsonEnvelopeBackcompat:
    def test_envelope_keeps_legacy_pairs_per_split_key(self, tmp_path, capsys):
        # Drives the CLI envelope path. ``audit_dataset`` returns the report;
        # the CLI shim is the boundary that exposes ``near_duplicate_pairs_per_split``.
        from forgelm.cli import _run_data_audit

        path = tmp_path / "x.jsonl"
        _write_jsonl(path, [{"text": "alpha"}, {"text": "alpha"}, {"text": "beta gamma"}])
        out_dir = tmp_path / "audit"
        _run_data_audit(str(path), str(out_dir), "json")
        captured = capsys.readouterr().out
        envelope = json.loads(captured)
        # Legacy key — pre-Phase-12 consumers depend on this exact name.
        assert "near_duplicate_pairs_per_split" in envelope
        # New richer key kept alongside (additive).
        assert "near_duplicate_summary" in envelope
        # Both must reference the same per-split data.
        assert envelope["near_duplicate_pairs_per_split"] == envelope["near_duplicate_summary"].get(
            "pairs_per_split", {}
        )


# ---------------------------------------------------------------------------
# C2 — quality filter: ``repeated_lines`` (the 5th plan-promised check) is
# implemented and surfaces in the audit JSON.
# ---------------------------------------------------------------------------


class TestC2RepeatedLinesQualityCheck:
    def test_top3_lines_over_30pct_flag(self):
        # Build a corpus where 4 of 6 lines are the same boilerplate line
        # (66 % > 30 % threshold).
        text = (
            "boilerplate header.\n"
            "boilerplate header.\n"
            "actual content here.\n"
            "boilerplate header.\n"
            "different content again.\n"
            "boilerplate header.\n"
        )
        flags = _row_quality_flags(text)
        assert "repeated_lines" in flags

    def test_diverse_lines_not_flagged(self):
        text = (
            "first unique line of prose.\n"
            "second unique line of prose.\n"
            "third unique line of prose.\n"
            "fourth unique line of prose.\n"
            "fifth unique line of prose.\n"
        )
        flags = _row_quality_flags(text)
        assert "repeated_lines" not in flags

    def test_quality_summary_surfaces_repeated_lines(self, tmp_path):
        path = tmp_path / "x.jsonl"
        _write_jsonl(
            path,
            [
                {"text": "Disclaimer X.\nDisclaimer X.\nbody.\nDisclaimer X.\nmore body.\nDisclaimer X.\n"},
                {"text": "innocent prose with no repetition pattern at all."},
            ],
        )
        report = audit_dataset(str(path), enable_quality_filter=True)
        by_check = report.quality_summary.get("by_check", {})
        assert by_check.get("repeated_lines", 0) >= 1


# ---------------------------------------------------------------------------
# C3 — DOCX table cell ``|`` characters must be escaped so the rendered
# markdown table parses with the correct column count.
# ---------------------------------------------------------------------------


class _FakeCell:
    def __init__(self, text):
        self.text = text


class _FakeRow:
    def __init__(self, cells):
        self.cells = [_FakeCell(c) for c in cells]


class _FakeTable:
    def __init__(self, rows):
        self.rows = [_FakeRow(r) for r in rows]


class TestC3DocxPipeEscape:
    def test_pipe_in_cell_escaped(self):
        table = _FakeTable(
            [
                ["Name", "Value"],
                ["a|b", "x"],
                ["c", "d|e"],
            ]
        )
        rendered = _docx_table_to_markdown(table)
        # The body rows must escape the inline pipes — otherwise downstream
        # markdown parsers see 4 columns instead of 2.
        assert "a\\|b" in rendered
        assert "d\\|e" in rendered
        # Each body line still has exactly 3 separators (= 2 columns).
        body_lines = [line for line in rendered.splitlines() if line.startswith("| ")][1:]
        # 3 separators because of leading/trailing/middle ``|``; escaped
        # ``\|`` doesn't act as a separator.
        for line in body_lines:
            # Real separators only — strip escaped ``\|`` first.
            stripped = line.replace("\\|", "")
            assert stripped.count("|") == 3, f"bad row: {line!r}"

    def test_backslash_in_cell_escaped(self):
        table = _FakeTable([["A"], ["c:\\path"]])
        rendered = _docx_table_to_markdown(table)
        # Backslash must be escaped (``\\`` → ``\\\\``) so it survives one
        # round-trip through a markdown-aware tokeniser.
        assert "c:\\\\path" in rendered

    def test_newline_in_cell_collapsed(self):
        table = _FakeTable([["Header"], ["multi\nline\ncell"]])
        rendered = _docx_table_to_markdown(table)
        # Newlines collapse to spaces — markdown tables can't carry them.
        assert "multi line line cell" not in rendered  # not duplicated
        assert "multi line cell" in rendered


# ---------------------------------------------------------------------------
# C4 — JWT regex must reject prose-shaped ``eyJ.eyJ.X`` strings while
# continuing to flag real JWTs (``alg`` / ``typ`` / etc. headers).
# ---------------------------------------------------------------------------


class TestC4JwtRegexNarrowing:
    def test_real_jwt_still_detected(self):
        # Build the JWT from inert fragments so secret scanners on the repo
        # don't mistake the fixture for a real leaked token. The regex still
        # has to match the canonical alg-prefix-anchored shape.
        header = "eyJhbGciOiJIUzI1NiJ9"  # base64 of {"alg":"HS256"}
        payload = "eyJzdWIiOiIxIn0"  # base64 of {"sub":"1"}
        signature = "SflKxwRJSMe" + "KKF2QT4fwpMeJ"
        text = f"Authorization: Bearer {header}.{payload}.{signature}"
        result = detect_secrets(text)
        assert result.get("jwt") == 1

    def test_realistic_hs256_token_detected(self):
        # Same fragmentation pattern as ``test_real_jwt_still_detected`` —
        # builds a longer payload to exercise the looser min-length branch.
        # Decoded header is the JWT type with HS256 algorithm.
        header = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        payload = "eyJzdWIiOiIxMjM0NSIs" + "Im5hbWUiOiJKb2huIERvZSJ9"
        signature = "abcdefghij" + "klmnopqrstuvwxyz123"
        text = f"token={header}.{payload}.{signature}"
        result = detect_secrets(text)
        assert result.get("jwt") == 1

    def test_prose_eyj_shape_not_flagged(self):
        # Reviewer's false-positive shape: ``eyJ.eyJ.X`` in casual prose,
        # not a real JWT (header is just ``eyJ`` followed by random bytes,
        # missing ``alg`` / ``typ`` / ``kid`` etc.).
        text = "Look at this base64: eyJhYmNkZWY.eyJ4eXp.aGVsbG8 — could be a JWT shape but is not."
        result = detect_secrets(text)
        assert "jwt" not in result

    def test_jwt_detection_covers_each_canonical_header_anchor(self):
        # Behavioural counterpart to the previous pattern-introspection
        # test: each canonical JWT header prefix (``alg`` / ``typ`` /
        # ``kid`` / ``cty`` / ``enc`` / ``api``) must trip the detector
        # via ``detect_secrets`` — so a future regex refactor that
        # achieves the same matching semantics with different anchor
        # encoding still passes, but a broadening that drops one of the
        # required prefixes still fails. Tokens are built from inert
        # fragments to keep repo-wide secret scanners off them.
        # Each base64 fragment below is the start of a JWT header
        # claim: ``eyJ`` + ``hbGc``=``alg``, ``0eXA``=``typ``,
        # ``raWQ``=``kid``, ``jdHk``=``cty``, ``lbmM``=``enc``,
        # ``hcGk``=``api`` (decoded under-the-hood).
        anchors = ("hbGc", "0eXA", "raWQ", "jdHk", "lbmM", "hcGk")
        signature = "SflKxwRJSMe" + "KKF2QT4fwpMeJ"
        payload = "eyJzdWIiOiIxIn0"
        for anchor in anchors:
            header = "eyJ" + anchor + "iOiJfdGVzdF9fIn0"  # JSON-shaped after decode
            text = f"Authorization: Bearer {header}.{payload}.{signature}"
            assert detect_secrets(text).get("jwt") == 1, (
                f"JWT detector missed header anchor {anchor!r} — regex broadening regression?"
            )


# ---------------------------------------------------------------------------
# Review-1#4 — quality filter must not flag rows that are predominantly
# fenced markdown code blocks (legitimate SFT content for code-instruct).
# ---------------------------------------------------------------------------


class TestQualityFilterIgnoresCodeFences:
    def test_pure_code_block_returns_no_flags(self):
        text = "```python\ndef f(x):\n    return x + 1\n```\n"
        # Pure code → strip leaves ``""`` → no flags (not "low_alpha_ratio").
        assert _row_quality_flags(text) == []

    def test_prose_with_code_block_judged_on_prose_only(self):
        text = (
            "Here is a function that adds one to its input. "
            "It is a simple unary mapping. "
            "Use it for arithmetic chaining.\n\n"
            "```python\n"
            "def f(x):\n"
            "    return x + 1\n"
            "```\n\n"
            "The function is pure and side-effect free."
        )
        flags = _row_quality_flags(text)
        # Prose around the code passes all heuristics — the code block must
        # not have dragged the alpha-ratio below 70 %.
        assert flags == []


# ---------------------------------------------------------------------------
# H4 — ``_chunk_markdown_tokens`` (token-aware twin) must respect the token
# cap and inline the heading breadcrumb. The original Phase 12 suite only
# tested the character-mode twin.
# ---------------------------------------------------------------------------


class _StubTokenizer:
    """Word-count tokenizer for deterministic token-cap tests."""

    def encode(self, text, add_special_tokens=False):  # noqa: ARG002
        return list(range(len(text.split())))


class TestChunkMarkdownTokens:
    def test_short_doc_packs_into_one_chunk(self):
        text = "# H1\n\nshort body."
        chunks = list(_chunk_markdown_tokens(text, max_tokens=100, tokenizer=_StubTokenizer()))
        assert len(chunks) == 1
        assert "# H1" in chunks[0]

    def test_separator_token_included_in_budget(self):
        # Two sections, each ~5 tokens. Token cap forces them into separate
        # chunks; the second chunk must inline the parent breadcrumb.
        text = (
            "# Project\n\n"
            "alpha beta gamma delta epsilon\n\n"
            "## Background\n\n"
            "uno dos tres cuatro cinco seis siete ocho nueve diez"
        )
        chunks = list(_chunk_markdown_tokens(text, max_tokens=8, tokenizer=_StubTokenizer()))
        assert len(chunks) >= 2
        background = next(c for c in chunks if "Background" in c)
        # Breadcrumb invariant: the parent heading rides into the
        # downstream chunk so SFT loss sees the document context.
        assert "# Project" in background

    def test_invalid_max_tokens_raises(self):
        import pytest

        with pytest.raises(ValueError):
            list(_chunk_markdown_tokens("# H1\n\nbody.", max_tokens=0, tokenizer=_StubTokenizer()))


# ---------------------------------------------------------------------------
# N1 — CommonMark allows up to 3 leading spaces before an ATX heading.
# ---------------------------------------------------------------------------


class TestCommonMarkIndentedHeadings:
    def test_two_leading_spaces_treated_as_heading(self):
        # 0-3 leading spaces is still a heading per CommonMark §4.2.
        text = "  # Indented heading\n\nbody under it.\n"
        sections = _markdown_sections(text)
        assert len(sections) == 1
        path, body = sections[0]
        # Heading text is recovered (with the ``#`` prefix preserved).
        assert path[0].lstrip().startswith("#")
        assert "body under it" in body

    def test_four_leading_spaces_is_not_heading(self):
        # 4+ spaces makes it an indented code block — must NOT split.
        text = "# Real H1\n\nintro.\n\n    # Not a heading\n\nstill body.\n"
        sections = _markdown_sections(text)
        # Single section because the 4-space line is body, not a heading.
        assert len(sections) == 1


# ---------------------------------------------------------------------------
# Round-2 review — additional fixes pinned against re-introduction.
# ---------------------------------------------------------------------------


class TestTildeFenceRecognised:
    """``~~~`` fences should toggle code-block state just like backticks."""

    def test_tilde_fence_blocks_inner_heading_split(self):
        text = (
            "# Real Heading\n\n"
            "intro before code\n\n"
            "~~~bash\n"
            "# this is a shell prompt inside ~~~ fence — must NOT split\n"
            "echo hi\n"
            "~~~\n\n"
            "more body."
        )
        sections = _markdown_sections(text)
        assert len(sections) == 1
        body = sections[0][1]
        assert "this is a shell prompt" in body
        assert "more body" in body


class TestFenceRunLengthRule:
    """CommonMark §4.5: a close fence must use ≥ as many chars as the open."""

    def test_four_backtick_block_not_closed_by_three(self):
        # The 3-backtick line inside the 4-backtick block must NOT close it.
        # Therefore the heading after the inner ``\`\`\`\`` (which IS a valid
        # close) is the only heading split. Pre-fix this regressed: a 3-tick
        # close prematurely terminated the 4-tick block, splitting the inner
        # ``# nested heading`` as a real heading.
        text = (
            "# Outer\n\n"
            "intro\n\n"
            "````\n"
            "```\n"
            "# nested heading inside 4-tick block — must NOT split\n"
            "```\n"
            "````\n\n"
            "outer body continues."
        )
        sections = _markdown_sections(text)
        assert len(sections) == 1, f"expected 1 section, got {len(sections)}: {[s[0] for s in sections]}"
        body = sections[0][1]
        assert "nested heading inside 4-tick block" in body
        assert "outer body continues" in body

    def test_strip_code_fences_respects_length_rule(self):
        # Same shape exercised against the audit-side stripper. The 3-tick
        # line in the middle is content; only the matching 4-tick line closes.
        from forgelm.data_audit import _strip_code_fences

        text = (
            "before\n"
            "````\n"
            "```\n"  # NOT a close — too short
            "still inside\n"
            "````\n"  # actual close — same length as open
            "after"
        )
        result = _strip_code_fences(text)
        # Block fully removed, surrounding lines preserved.
        assert "still inside" not in result
        assert "before" in result
        assert "after" in result

    def test_close_with_info_string_does_not_close(self):
        # CommonMark: closing fence may NOT carry an info string.
        # ``~~~bash`` opens a tilde block; another ``~~~bash`` line inside
        # it is content (info string disqualifies it from being a close),
        # so the block stays open until ``~~~`` (no info) is seen.
        from forgelm.data_audit import _strip_code_fences

        text = (
            "before\n"
            "~~~bash\n"
            "~~~bash\n"  # info string → not a close, treated as content
            "still inside\n"
            "~~~\n"  # no info string → valid close
            "after"
        )
        result = _strip_code_fences(text)
        assert "still inside" not in result
        assert "after" in result


class TestPrivateKeyFullBlock:
    """``mask_secrets`` redacts the entire PEM/PGP envelope, not just BEGIN."""

    def test_openssh_full_block_redacted(self):
        from forgelm.data_audit import mask_secrets

        # Build the envelope from fragments — no actual key body in source.
        begin = "-----" + "BEGIN " + "OPENSSH PRIVATE KEY-----"
        end = "-----" + "END " + "OPENSSH PRIVATE KEY-----"
        body = "abcdefghij" * 10
        original = f"context\n{begin}\n{body}\n{end}\nmore context"
        masked = mask_secrets(original)
        # The body bytes must be gone — not just the header line.
        assert body not in masked
        assert begin not in masked
        assert end not in masked
        assert "[REDACTED-SECRET]" in masked
        # Surrounding prose untouched.
        assert "context" in masked
        assert "more context" in masked


class TestMarkdownOverlapValidation:
    """Markdown chunkers must reject explicit non-zero overlap."""

    def test_strategy_dispatch_rejects_overlap(self):
        import pytest

        from forgelm.ingestion import _strategy_dispatch

        with pytest.raises(ValueError, match=r"overlap.*not supported for --strategy markdown"):
            list(_strategy_dispatch("markdown", "# H1\n\nbody.", chunk_size=100, overlap=10))

    def test_strategy_dispatch_tokens_rejects_overlap(self):
        import pytest

        from forgelm.ingestion import _strategy_dispatch_tokens

        with pytest.raises(ValueError, match=r"overlap.*not supported for --strategy markdown"):
            list(
                _strategy_dispatch_tokens(
                    "markdown",
                    "# H1\n\nbody.",
                    chunk_tokens=100,
                    overlap_tokens=5,
                    tokenizer=_StubTokenizer(),
                )
            )

    def test_default_overlap_does_not_trip_markdown(self, tmp_path):
        # The CLI default overlap (200) historically tripped the validator
        # when the user picked --strategy markdown. ``ingest_path``'s
        # ``overlap=None`` sentinel resolves the default per strategy so the
        # validator only fires on user-supplied non-zero values.
        from forgelm.ingestion import ingest_path

        src = tmp_path / "doc.md"
        src.write_text("# H1\n\nbody alpha beta.", encoding="utf-8")
        out = tmp_path / "out.jsonl"
        # No ``overlap=`` kwarg — should pass through silently.
        result = ingest_path(str(src), output_path=str(out), strategy="markdown", chunk_size=100)
        assert result.chunk_count >= 1


class TestRegexLinearity:
    """Round-2.5: pin O(n) behaviour on patterns the static analyser flagged.

    These regexes accept operator-controlled input (markdown / code in
    audit JSONL or ingest source files), so a quadratic-time regex is a
    DoS vector for any CI/CD pipeline that runs ForgeLM on a webhook
    trigger. The benchmarks below were authored after a confirmed
    100ms-at-n=2000 / 600ms-at-n=5000 ReDoS on the previous heading
    pattern. Fail threshold is generous (1 second on N=10K) so a slow
    CI host doesn't false-positive — but a real ReDoS would blow far
    past it.
    """

    # The three tests below verify *linear* scaling with input size, not
    # absolute wall-clock. Absolute thresholds (``elapsed_ms < 1000``) are
    # flaky on shared CI under load; a growth-ratio check is independent of
    # the host's nominal speed. ``_LINEAR_SCALE_TOLERANCE`` allows the
    # measured cost to grow up to 3× the input ratio before we flag it as
    # super-linear — generous enough to absorb cache warm-up + GC noise on
    # small inputs, tight enough to catch O(n²) ReDoS regressions.
    _LINEAR_SCALE_TOLERANCE = 3

    def _measure_median_ms(self, fn, payload, samples=5):
        import statistics
        import time

        observations = []
        for _ in range(samples):
            t0 = time.perf_counter()
            fn(payload)
            observations.append((time.perf_counter() - t0) * 1000)
        return statistics.median(observations)

    def test_heading_pattern_linear_on_pathological_whitespace(self):
        # Old pattern: ``[ \t]+(.+?)[ \t]*$`` had three greedy/lazy
        # quantifiers competing for trailing whitespace. New pattern
        # anchors on \S so the engine has no ambiguity.
        from forgelm.ingestion import _MARKDOWN_HEADING_PATTERN

        sizes = (1_000, 5_000, 10_000)
        timings = {n: self._measure_median_ms(_MARKDOWN_HEADING_PATTERN.match, "# a" + " \t" * n + "x") for n in sizes}
        smallest = sizes[0]
        # Floor the baseline at 0.001 ms so a sub-microsecond measurement
        # (which is below the perf_counter resolution on some hosts)
        # doesn't make the ratio explode.
        baseline = max(timings[smallest], 0.001)
        for n in sizes[1:]:
            ratio = timings[n] / baseline
            allowed = (n / smallest) * self._LINEAR_SCALE_TOLERANCE
            assert ratio <= allowed, (
                f"heading regex grew {ratio:.1f}× from n={smallest} to n={n} "
                f"(allowed {allowed:.1f}×); possible ReDoS regression. timings_ms={timings}"
            )

    def test_heading_pattern_linear_on_nonmatching_whitespace(self):
        # F-P6-OPUS-17: the sibling above feeds a MATCHING line (terminates in
        # 'x'). The hotter worst case for the anchored body
        # ``(\S(?:[^\n]*\S)?)[ \t]*$`` is the NON-matching all-trailing-
        # whitespace line ``'# ' + ' \t'*n`` (no terminal \S): the optional
        # inner group must backtrack across the whole whitespace run before the
        # overall match fails. Both shapes are linear today; this keeps the
        # regression net symmetric across the match / no-match paths.
        from forgelm.ingestion import _MARKDOWN_HEADING_PATTERN

        sizes = (1_000, 5_000, 10_000)
        timings = {n: self._measure_median_ms(_MARKDOWN_HEADING_PATTERN.match, "# " + " \t" * n) for n in sizes}
        smallest = sizes[0]
        # Sanity: this payload genuinely does NOT match (it is the no-trailing-\S
        # worst case), so the benchmark exercises the failing-match backtrack.
        assert _MARKDOWN_HEADING_PATTERN.match("# " + " \t" * smallest) is None
        baseline = max(timings[smallest], 0.001)
        for n in sizes[1:]:
            ratio = timings[n] / baseline
            allowed = (n / smallest) * self._LINEAR_SCALE_TOLERANCE
            assert ratio <= allowed, (
                f"heading regex grew {ratio:.1f}× from n={smallest} to n={n} on the "
                f"non-matching shape (allowed {allowed:.1f}×); possible ReDoS regression. "
                f"timings_ms={timings}"
            )

    def test_strip_code_fences_linear_on_unclosed_blocks(self):
        # Old regex: ``.*?`` + back-reference + DOTALL. Replaced with
        # a per-line state machine. Pathological shape: many opening
        # fences and no close ever — old regex went linear-ish in CPython
        # but SonarCloud flagged the polynomial-runtime risk; the walker
        # is provably O(n) and silences the analyser.
        from forgelm.data_audit import _strip_code_fences

        sizes = (1_000, 5_000, 10_000)
        timings = {n: self._measure_median_ms(_strip_code_fences, "```\nx\n" * n + "no close ever") for n in sizes}
        smallest = sizes[0]
        baseline = max(timings[smallest], 0.001)
        for n in sizes[1:]:
            ratio = timings[n] / baseline
            allowed = (n / smallest) * self._LINEAR_SCALE_TOLERANCE
            assert ratio <= allowed, (
                f"_strip_code_fences grew {ratio:.1f}× from n={smallest} to n={n} "
                f"(allowed {allowed:.1f}×); possible regression. timings_ms={timings}"
            )

    def test_parse_md_fence_linear_on_long_runs(self):
        # The previous ``_MARKDOWN_CODE_FENCE`` regex
        # ``^ {0,3}(?P<fence>`{3,}|~{3,})(?P<rest>[^\n]*)$`` had two
        # unbounded greedy quantifiers in sequence (``{3,}`` and
        # ``[^\n]*``) over overlapping character classes — SonarCloud
        # python:S5852 flagged it as polynomial-runtime risk. Replaced
        # with ``_parse_md_fence`` (non-regex parser); provably O(n).
        from forgelm.ingestion import _parse_md_fence

        sizes = (1_000, 10_000, 100_000)
        # Pure backtick run — worst-case shape for the old regex.
        timings = {n: self._measure_median_ms(_parse_md_fence, "`" * n) for n in sizes}
        smallest = sizes[0]
        baseline = max(timings[smallest], 0.001)
        for n in sizes[1:]:
            ratio = timings[n] / baseline
            allowed = (n / smallest) * self._LINEAR_SCALE_TOLERANCE
            assert ratio <= allowed, (
                f"_parse_md_fence grew {ratio:.1f}× from n={smallest} to n={n} "
                f"(allowed {allowed:.1f}×); possible regression. timings_ms={timings}"
            )

    def test_parse_md_fence_behaviour(self):
        # Behavioural pins so the non-regex parser stays drop-in compatible
        # with what the previous regex returned via named groups.
        from forgelm.ingestion import _parse_md_fence

        # Opener with info string.
        assert _parse_md_fence("```python") == ("`", 3, "python")
        # 4-backtick opener — callers use run_len for CommonMark §4.5 close check.
        assert _parse_md_fence("````") == ("`", 4, "")
        # Tilde fence with 2-space indent (allowed; <4).
        assert _parse_md_fence("  ~~~bash") == ("~", 3, "bash")
        # 4-space indent → indented code block, not a fence.
        assert _parse_md_fence("    ```") is None
        # 2-char run → not a fence.
        assert _parse_md_fence("``") is None
        # Mismatched chars after the run — run ends at the first non-fence char.
        assert _parse_md_fence("```~~~") == ("`", 3, "~~~")


class TestMinHashDistinctSemantic:
    """``minhash_distinct`` must count *unique sketches*, mirroring simhash."""

    def test_duplicate_rows_produce_one_distinct(self, tmp_path):
        # Two identical rows + one unrelated row → 2 distinct simhash
        # fingerprints. MinHash should report the same shape.
        # Uses pytest's ``tmp_path`` fixture so we don't mutate the repo
        # tree and parallel pytest runs don't collide on a shared name.
        import importlib

        if importlib.util.find_spec("datasketch") is None:  # type: ignore[attr-defined]
            import pytest

            pytest.skip("datasketch (ingestion-scale extra) not installed")

        from forgelm.data_audit import audit_dataset

        path = tmp_path / "minhash_distinct"
        path.mkdir()
        jsonl = path / "x.jsonl"
        with open(jsonl, "w", encoding="utf-8") as fh:
            for row in [
                {"text": "alpha beta gamma delta epsilon zeta"},
                {"text": "alpha beta gamma delta epsilon zeta"},
                {"text": "completely unrelated payload tokens"},
            ]:
                fh.write(json.dumps(row) + "\n")
        report = audit_dataset(str(jsonl), dedup_method="minhash")
        train_info = report.splits["train"]
        # 2 distinct sketches: one for the duplicate pair, one for the
        # unrelated row. Pre-fix this returned 3 (count of non-empty rows).
        assert train_info["minhash_distinct"] == 2
