"""Wave 4 / Faz 26 — tools/check_anchor_resolution.py regression tests.

The anchor-resolution checker is a CI-bound gate (once Faz 30
broken-link cleanup completes); a regression that silently
under-reports drift would let docs anchor decay go unnoticed.

Pinned contracts:

1. Repo-relative paths resolve against the source file's parent.
2. Markdown anchor links resolve against the target file's headers
   using GitHub-flavoured slugify.
3. External URLs (https://, mailto:, javascript:) are skipped.
4. SPA hash-router fragments (`#/path`) are skipped.
5. Pure anchors (`#section`) resolve against the SAME file.
6. Line-anchor references on code files (`forgelm/x.py#L42`) are
   flagged as brittle.
7. `--strict` flips exit code on broken; advisory mode reports but
   exits 0.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TOOLS = _REPO_ROOT / "tools"
_TOOL_PATH = _TOOLS / "check_anchor_resolution.py"


def _load_tool() -> object:
    spec = importlib.util.spec_from_file_location("check_anchor_resolution", _TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_anchor_resolution"] = module
    spec.loader.exec_module(module)
    return module


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# §1 — slugify_heading: GFM-style header anchor algorithm
# ---------------------------------------------------------------------------


class TestSlugifyHeading:
    def test_lowercases_and_dashes_spaces(self):
        tool = _load_tool()
        assert tool._slugify_heading("Hello World") == "hello-world"

    def test_strips_punctuation(self):
        tool = _load_tool()
        assert tool._slugify_heading("Section 1.2: Why?") == "section-12-why"

    def test_preserves_consecutive_dashes(self):
        # F-P8-C-14: GFM does NOT collapse runs of dashes.  ``a -- b   c``
        # renders the anchor ``a----b---c`` on github.com (each surviving
        # literal dash plus one dash per whitespace char).  The earlier
        # ``re.sub(r"-+", "-", …)`` collapse diverged from GitHub and both
        # masked links that 404 and rejected correct GFM-slug links.
        tool = _load_tool()
        assert tool._slugify_heading("a -- b   c") == "a----b---c"
        assert tool._slugify_heading("Foo - Bar") == "foo---bar"
        assert tool._slugify_heading("A  B") == "a--b"
        # Real in-repo heading that the dash-collapse bug masked.
        assert tool._slugify_heading("Phase 11.5 — Ingestion / Audit Polish") == "phase-115--ingestion--audit-polish"

    def test_collapsed_slug_link_is_flagged_broken(self, tmp_path: Path):
        # F-P8-C-14 resolution lens: a link written with the OLD collapsed
        # slug must now be reported broken, while the GFM slug resolves.
        tool = _load_tool()
        target = _write(
            tmp_path / "docs" / "target.md",
            "# Title\n\n## Phase 14 — Multi-Stage Chains (v0.7.0)\n\nbody\n",
        )
        anchors = tool._extract_anchors(target)
        assert "phase-14--multi-stage-chains-v070" in anchors
        # The collapsed form GitHub would 404 on is absent.
        assert "phase-14-multi-stage-chains-v070" not in anchors

    def test_unicode_word_chars_preserved(self):
        # Turkish letters, etc. — \w with re.UNICODE matches them.
        tool = _load_tool()
        assert tool._slugify_heading("Dil Seçimi") == "dil-seçimi"

    def test_trims_leading_trailing_dashes(self):
        tool = _load_tool()
        assert tool._slugify_heading("  a  ") == "a"
        assert tool._slugify_heading("--a--") == "a"

    def test_apostrophe_stripped(self):
        # F-W4-TR-07 absorption: GFM strips apostrophes in "What's New".
        tool = _load_tool()
        assert tool._slugify_heading("What's New") == "whats-new"

    def test_backtick_stripped(self):
        # F-W4-TR-07 absorption: GFM strips backticks in code-quoted headings.
        tool = _load_tool()
        assert tool._slugify_heading("Heading with `code`") == "heading-with-code"

    def test_atx_closing_hashes_stripped(self):
        # ATX trailing-hash decoration must be stripped before slugify.
        tool = _load_tool()
        assert tool._normalise_heading_body("My Title #") == "My Title"
        assert tool._normalise_heading_body("My Title  ###  ") == "My Title"

    def test_atx_closing_hashes_without_preceding_space_still_stripped(self):
        # F-W4FU-TR-09 absorption: the helper greedily strips the
        # closing-hash run even without preceding whitespace.  Off-spec
        # per CommonMark §4.2 but defensible — pin current behaviour
        # so a future tightening is a deliberate change, not silent
        # drift.
        tool = _load_tool()
        assert tool._normalise_heading_body("foo###") == "foo"

    def test_atx_all_hashes_rejected(self):
        # F-W4FU-TR-05 / F-W4FU-07 absorption: ``## ##`` (all-hash
        # body) trims to "" after closing-hash strip; the parser must
        # reject rather than register the empty slug as an anchor.
        tool = _load_tool()
        assert tool._parse_atx_heading("## ##") is None
        assert tool._parse_atx_heading("### #") is None
        # Symmetric happy path: real heading still parses.
        assert tool._parse_atx_heading("## Real Heading") == "Real Heading"


# ---------------------------------------------------------------------------
# §2 — Resolution: relative paths + anchors
# ---------------------------------------------------------------------------


class TestResolveRelativePath:
    def test_existing_relative_path_resolves(self, tmp_path: Path):
        tool = _load_tool()
        _write(tmp_path / "subdir" / "target.md", "# Target\n")
        source = _write(tmp_path / "src.md", "[link](subdir/target.md)\n")
        results = list(tool._extract_links(source))
        assert len(results) == 1
        assert tool._resolve_link(results[0], tmp_path) is None

    def test_missing_relative_path_flagged(self, tmp_path: Path):
        tool = _load_tool()
        source = _write(tmp_path / "src.md", "[link](missing.md)\n")
        results = list(tool._extract_links(source))
        broken = tool._resolve_link(results[0], tmp_path)
        assert broken is not None
        assert "target file not found" in broken.reason

    def test_external_url_skipped(self, tmp_path: Path):
        tool = _load_tool()
        source = _write(tmp_path / "src.md", "[link](https://example.com)\n")
        results = list(tool._extract_links(source))
        assert tool._resolve_link(results[0], tmp_path) is None

    def test_mailto_skipped(self, tmp_path: Path):
        tool = _load_tool()
        source = _write(tmp_path / "src.md", "[email](mailto:me@example.com)\n")
        assert tool._resolve_link(next(iter(tool._extract_links(source))), tmp_path) is None

    def test_spa_hash_router_skipped(self, tmp_path: Path):
        tool = _load_tool()
        source = _write(tmp_path / "src.md", "[ref](#/reference/usage)\n")
        assert tool._resolve_link(next(iter(tool._extract_links(source))), tmp_path) is None

    def test_tel_skipped(self, tmp_path: Path):
        # F-W4-TR-04 absorption: tel: scheme must be in skip list.
        tool = _load_tool()
        source = _write(tmp_path / "src.md", "[call](tel:+15551234567)\n")
        assert tool._resolve_link(next(iter(tool._extract_links(source))), tmp_path) is None

    def test_javascript_skipped(self, tmp_path: Path):
        # F-W4-TR-04 absorption: javascript: scheme must be in skip list.
        tool = _load_tool()
        source = _write(tmp_path / "src.md", "[js](javascript:void(0))\n")
        assert tool._resolve_link(next(iter(tool._extract_links(source))), tmp_path) is None

    def test_image_link_missing_target_flagged(self, tmp_path: Path):
        # F-W4-TR-06 absorption: image-link regex parity with link form.
        tool = _load_tool()
        source = _write(tmp_path / "src.md", "![alt](missing.png)\n")
        results = list(tool._extract_links(source))
        assert len(results) == 1, "regex must match the [alt](src) inside ![alt](src)"
        broken = tool._resolve_link(results[0], tmp_path)
        assert broken is not None and "target file not found" in broken.reason

    def test_image_link_present_target_resolves(self, tmp_path: Path):
        tool = _load_tool()
        _write(tmp_path / "ok.png", "")
        source = _write(tmp_path / "src.md", "![ok](ok.png)\n")
        assert tool._resolve_link(next(iter(tool._extract_links(source))), tmp_path) is None


class TestResolveAnchor:
    def test_self_anchor_resolves(self, tmp_path: Path):
        tool = _load_tool()
        source = _write(
            tmp_path / "src.md",
            "# Top\n\n## My Section\n\n[link](#my-section)\n",
        )
        results = list(tool._extract_links(source))
        assert tool._resolve_link(results[0], tmp_path) is None

    def test_self_anchor_missing_flagged(self, tmp_path: Path):
        tool = _load_tool()
        source = _write(
            tmp_path / "src.md",
            "# Top\n\n[link](#nonexistent)\n",
        )
        results = list(tool._extract_links(source))
        broken = tool._resolve_link(results[0], tmp_path)
        assert broken is not None
        assert "anchor" in broken.reason

    def test_cross_file_anchor_resolves(self, tmp_path: Path):
        tool = _load_tool()
        _write(tmp_path / "target.md", "## Section A\n")
        source = _write(tmp_path / "src.md", "[link](target.md#section-a)\n")
        results = list(tool._extract_links(source))
        assert tool._resolve_link(results[0], tmp_path) is None

    def test_line_anchor_on_code_file_flagged_as_brittle(self, tmp_path: Path):
        tool = _load_tool()
        # Plant a real .py file so target-not-found doesn't fire first.
        _write(tmp_path / "x.py", "def foo(): pass\n")
        source = _write(tmp_path / "src.md", "[ref](x.py#L42)\n")
        results = list(tool._extract_links(source))
        broken = tool._resolve_link(results[0], tmp_path)
        assert broken is not None
        assert "brittle" in broken.reason

    def test_non_line_anchor_on_code_file_silent(self, tmp_path: Path):
        tool = _load_tool()
        _write(tmp_path / "x.py", "def foo(): pass\n")
        source = _write(tmp_path / "src.md", "[ref](x.py#some-symbol-anchor)\n")
        results = list(tool._extract_links(source))
        # Code-file anchors that don't match #L<digits> are accepted
        # (they may be e.g. function-name fragments rendered by GitHub).
        assert tool._resolve_link(results[0], tmp_path) is None


# ---------------------------------------------------------------------------
# §3 — CLI surface: --strict, --quiet, --exclude
# ---------------------------------------------------------------------------


class TestCLI:
    def test_clean_tree_exits_zero(self, tmp_path: Path, capsys):
        tool = _load_tool()
        scope = tmp_path / "docs"
        _write(scope / "a.md", "# A\n[link](b.md)\n")
        _write(scope / "b.md", "# B\n")
        rc = tool.main(["--repo-root", str(tmp_path), "--scope", "docs"])
        assert rc == 0
        captured = capsys.readouterr().out
        assert "OK" in captured

    def test_advisory_mode_reports_but_exits_zero(self, tmp_path: Path, capsys):
        tool = _load_tool()
        scope = tmp_path / "docs"
        _write(scope / "a.md", "# A\n[broken](missing.md)\n")
        rc = tool.main(["--repo-root", str(tmp_path), "--scope", "docs"])
        assert rc == 0
        captured = capsys.readouterr().out
        assert "WARN" in captured
        assert "missing.md" in captured

    def test_strict_mode_exits_one_on_broken(self, tmp_path: Path, capsys):
        tool = _load_tool()
        scope = tmp_path / "docs"
        _write(scope / "a.md", "# A\n[broken](missing.md)\n")
        rc = tool.main(["--repo-root", str(tmp_path), "--scope", "docs", "--strict"])
        assert rc == 1
        captured = capsys.readouterr().out
        assert "FAIL" in captured

    def test_strict_mode_exits_one_on_broken_anchor(self, tmp_path: Path, capsys):
        # F-W4-TR-03 absorption (tightened in F-W4FU-TR-06):
        # anchor-not-found and file-not-found must be symmetrically
        # gated under --strict; the broken-link reason must
        # disambiguate "anchor not found" from "target file not found".
        tool = _load_tool()
        scope = tmp_path / "docs"
        _write(scope / "target.md", "# Target\n\n## Real Section\n")
        _write(scope / "src.md", "[link](target.md#nonexistent)\n")
        rc = tool.main(["--repo-root", str(tmp_path), "--scope", "docs", "--strict"])
        assert rc == 1
        out = capsys.readouterr().out
        assert "FAIL" in out
        assert "anchor" in out, f"reason should distinguish anchor-not-found from file-not-found; got: {out!r}"

    def test_quiet_suppresses_ok_summary(self, tmp_path: Path, capsys):
        tool = _load_tool()
        scope = tmp_path / "docs"
        _write(scope / "a.md", "# A\n")
        rc = tool.main(["--repo-root", str(tmp_path), "--scope", "docs", "--quiet"])
        assert rc == 0
        assert "OK" not in capsys.readouterr().out

    def test_exclude_strips_subtree(self, tmp_path: Path, capsys):
        tool = _load_tool()
        scope = tmp_path / "docs"
        _write(scope / "public.md", "# A\n[ok](b.md)\n")
        _write(scope / "b.md", "# B\n")
        # The "internal" subdirectory has a broken link but is excluded.
        _write(scope / "internal" / "research.md", "[broken](../../tmp/foo.md)\n")
        rc = tool.main(
            [
                "--repo-root",
                str(tmp_path),
                "--scope",
                "docs",
                "--exclude",
                "internal",
                "--strict",
            ]
        )
        assert rc == 0

    def test_missing_scope_exits_one(self, tmp_path: Path, capsys):
        tool = _load_tool()
        rc = tool.main(["--repo-root", str(tmp_path), "--scope", "nonexistent"])
        assert rc == 1
        assert "scope directory not found" in capsys.readouterr().err
