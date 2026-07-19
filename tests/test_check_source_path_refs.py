"""Tests for tools/check_source_path_refs.py.

The guard exists because the ``forgelm/safety.py`` -> ``forgelm/safety/``
split shipped 39 dangling references across 16 files in the very commit that
moved the file, and no guard could see them: they were backticked inline
paths, skill-checklist entries, site HTML and notebook JSON, not the
``[text](href)`` Markdown links under ``docs/`` that
``check_anchor_resolution.py`` validates.

Three layers, mirroring ``tests/test_check_release_record_sync.py``:

* **Unit** — the matcher, the fence tracker, the Markdown-link stripper and
  the surface/record classifiers pinned against synthetic in-memory input, so
  they stay independent of the real, evolving corpus.
* **Enforcement** (:class:`TestEnforcement`) — ``main()`` driven against a
  temporary tree via ``--repo-root``, so every failure branch executes and its
  exit code is asserted in both strict and advisory mode. Without this layer
  the ``return 1 if strict else 0`` surface is unreached: a live-repo-only
  suite is green by construction.
* **Live repo** (:class:`TestRealRepo`) — the invariant CI relies on,
  ``main(["--strict", "--quiet"]) == 0``.

The false-positive controls get *positive* coverage too (a fenced block, a
record surface, an exempted line must NOT be reported). A guard that cries
wolf gets disabled, so its silence on legitimate input is as much a contract
as its noise on dead references.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TOOL_PATH = _REPO_ROOT / "tools" / "check_source_path_refs.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("check_source_path_refs", _TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_source_path_refs"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture(scope="module")
def tool():
    return _load_tool()


def _matches(tool, line: str) -> list[str]:
    """Return the source paths the guard's pattern extracts from *line*."""
    return [m.group(1) for m in tool._PATH_RE.finditer(line)]


# --------------------------------------------------------------------------
# Unit — the path matcher
# --------------------------------------------------------------------------


class TestPathPattern:
    @pytest.mark.parametrize(
        "line,expected",
        [
            ("see `forgelm/safety.py` for details", ["forgelm/safety.py"]),
            ("run `tools/check_bandit.py` first", ["tools/check_bandit.py"]),
            ("covered by tests/test_safety.py today", ["tests/test_safety.py"]),
            ("nested `forgelm/cli/subcommands/_doctor.py`", ["forgelm/cli/subcommands/_doctor.py"]),
            ("the `forgelm/safety/` package", ["forgelm/safety/"]),
        ],
    )
    def test_matches_repo_rooted_source_paths(self, tool, line, expected):
        assert _matches(tool, line) == expected

    @pytest.mark.parametrize(
        "line",
        [
            # Not a source root — the reader's own config, 76 hits on a clean
            # tree and the single largest noise source the scope study found.
            "edit `configs/run.yaml` to taste",
            # docs/ is the anchor guard's territory.
            "see `docs/reference/foo.md`",
            # A longer path that merely CONTAINS a root name.
            "vendored at `third_party/forgelm/safety.py`",
            # An identifier that ends in a root name.
            "the `my_tools/helper.py` script",
            # Prose slash, no extension and no trailing slash.
            "the forgelm/safety module",
            # Bare module reference without a path separator.
            "import forgelm.safety",
        ],
    )
    def test_does_not_match_out_of_scope_shapes(self, tool, line):
        assert _matches(tool, line) == []

    def test_does_not_partial_match_a_longer_extension(self, tool):
        # ``tests/test_http.python`` must not report as ``tests/test_http.py``.
        assert _matches(tool, "see tests/test_http.python here") == []

    def test_finds_multiple_paths_on_one_line(self, tool):
        found = _matches(tool, "`forgelm/a.py` and `tools/b.py` and `tests/c.py`")
        assert found == ["forgelm/a.py", "tools/b.py", "tests/c.py"]


# --------------------------------------------------------------------------
# Unit — Markdown-link stripping (no double-reporting with the anchor guard)
# --------------------------------------------------------------------------


class TestMarkdownLinkStripping:
    def test_link_href_is_removed(self, tool):
        line = "see [the module](forgelm/safety.py) now"
        assert _matches(tool, tool._strip_markdown_links(line)) == []

    def test_link_text_is_preserved(self, tool):
        # A backticked path used AS link text is still a prose reference this
        # guard owns; only the href belongs to check_anchor_resolution.py.
        line = "see [`forgelm/safety.py`](../../forgelm/safety.py)"
        assert _matches(tool, tool._strip_markdown_links(line)) == ["forgelm/safety.py"]

    def test_plain_backticked_path_survives_stripping(self, tool):
        line = "see `forgelm/safety.py` for details"
        assert tool._strip_markdown_links(line) == line


# --------------------------------------------------------------------------
# Unit — fenced-block skipping
# --------------------------------------------------------------------------


class TestFenceSkipping:
    def test_fenced_lines_are_skipped_in_markdown(self, tool):
        text = "before\n```\nforgelm/gone.py\n```\nafter\n"
        bodies = [line for _, line in tool._iter_prose_lines("docs/x.md", text)]
        assert bodies == ["before", "after"]

    def test_tilde_fences_are_honoured(self, tool):
        text = "before\n~~~\nforgelm/gone.py\n~~~\nafter\n"
        bodies = [line for _, line in tool._iter_prose_lines("docs/x.md", text)]
        assert bodies == ["before", "after"]

    def test_line_numbers_are_absolute_not_relative(self, tool):
        # A maintainer opens the file at the reported line; an offset by the
        # number of skipped fence lines would send them to the wrong place.
        text = "a\n```\nx\n```\nb\n"
        assert list(tool._iter_prose_lines("docs/x.md", text)) == [(1, "a"), (5, "b")]

    def test_notebooks_are_not_fence_tracked(self, tool):
        # .ipynb is JSON; a "```" inside a markdown cell's escaped string must
        # not start a fence and swallow the rest of the notebook.
        text = '{"source": ["```\\n"]}\n"forgelm/gone.py"\n'
        bodies = [line for _, line in tool._iter_prose_lines("notebooks/x.ipynb", text)]
        assert len(bodies) == 2


# --------------------------------------------------------------------------
# Unit — surface + record classification
# --------------------------------------------------------------------------


class TestSurfaceSelection:
    @pytest.mark.parametrize(
        "rel",
        [
            "CLAUDE.md",
            "AGENTS.md",
            "CONTRIBUTING.md",
            "README.md",
            "docs/standards/coding.md",
            "docs/usermanuals/en/evaluation/safety.md",
            ".claude/skills/review-pr/SKILL.md",
            ".agents/skills/review-pr/SKILL.md",
            "site/compliance.html",
            "site/js/translations.js",
            "notebooks/safety_evaluation.ipynb",
        ],
    )
    def test_scanned_surfaces(self, tool, rel):
        assert tool._is_scanned(rel)

    @pytest.mark.parametrize(
        "rel",
        [
            # Source + test trees are code, not prose surfaces.
            "forgelm/safety/_gates.py",
            "tests/test_safety_advanced.py",
            "tools/check_module_size.py",
            # A record of the past; the split announcement MUST name the old path.
            "CHANGELOG.md",
            # Record surfaces.
            "docs/roadmap/risks-and-decisions.md",
            "docs/roadmap/releases.md",
            "docs/design/library_api.md",
            # Gitignored working memory (check_no_analysis_refs.py owns these).
            "docs/analysis/private/notes.md",
            "docs/marketing/strategy/05.md",
            # Wrong extension for its tree.
            "site/style.css",
        ],
    )
    def test_unscanned_surfaces(self, tool, rel):
        assert not tool._is_scanned(rel)

    def test_changelog_split_announcement_is_out_of_scope(self, tool):
        # The v0.9.1 entry announcing the split necessarily names
        # forgelm/safety.py. Flagging it would demand falsifying the record.
        assert not tool._is_scanned("CHANGELOG.md")


# --------------------------------------------------------------------------
# Enforcement — main() against a temporary tree
# --------------------------------------------------------------------------


class TestEnforcement:
    @staticmethod
    def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
        for rel, body in files.items():
            target = tmp_path / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")
        return tmp_path

    def test_dead_reference_fails_strict(self, tool, tmp_path, capsys):
        root = self._tree(tmp_path, {"CLAUDE.md": "see `forgelm/safety.py` today\n"})
        assert tool.main(["--repo-root", str(root), "--strict"]) == 1
        out = capsys.readouterr().out
        assert "CLAUDE.md:1" in out
        assert "forgelm/safety.py" in out
        assert "FAIL" in out

    def test_dead_reference_is_advisory_without_strict(self, tool, tmp_path, capsys):
        root = self._tree(tmp_path, {"CLAUDE.md": "see `forgelm/safety.py` today\n"})
        assert tool.main(["--repo-root", str(root)]) == 0
        out = capsys.readouterr().out
        # Advisory mode must still DIAGNOSE — a mutation that silenced
        # non-strict output would otherwise survive.
        assert "WARN" in out
        assert "forgelm/safety.py" in out

    def test_live_reference_passes(self, tool, tmp_path, capsys):
        root = self._tree(
            tmp_path,
            {
                "CLAUDE.md": "see `forgelm/safety/_gates.py` today\n",
                "forgelm/safety/_gates.py": "# real\n",
            },
        )
        assert tool.main(["--repo-root", str(root), "--strict"]) == 0
        assert "OK:" in capsys.readouterr().out

    def test_directory_reference_resolves(self, tool, tmp_path):
        root = self._tree(
            tmp_path,
            {
                "CLAUDE.md": "the `forgelm/safety/` package\n",
                "forgelm/safety/__init__.py": "",
            },
        )
        assert tool.main(["--repo-root", str(root), "--strict"]) == 0

    def test_fenced_block_is_not_reported(self, tool, tmp_path):
        root = self._tree(
            tmp_path,
            {"CLAUDE.md": "intro\n```text\nforgelm/yourmodule.py\n```\noutro\n"},
        )
        assert tool.main(["--repo-root", str(root), "--strict"]) == 0

    def test_record_surface_is_not_reported(self, tool, tmp_path):
        root = self._tree(
            tmp_path,
            {"docs/roadmap/releases.md": "v0.5.5 shipped `forgelm/data_audit.py`\n"},
        )
        assert tool.main(["--repo-root", str(root), "--strict"]) == 0

    def test_markdown_link_href_is_left_to_the_anchor_guard(self, tool, tmp_path):
        root = self._tree(tmp_path, {"docs/x.md": "see [module](../forgelm/gone.py)\n"})
        assert tool.main(["--repo-root", str(root), "--strict"]) == 0

    def test_exempt_line_is_suppressed(self, tool, tmp_path, monkeypatch):
        root = self._tree(tmp_path, {"CLAUDE.md": "legacy note about `forgelm/gone.py`\n"})
        assert tool.main(["--repo-root", str(root), "--strict"]) == 1
        monkeypatch.setitem(tool._EXEMPT, "CLAUDE.md", frozenset({"legacy note"}))
        assert tool.main(["--repo-root", str(root), "--strict"]) == 0

    def test_exemption_is_line_scoped_not_file_scoped(self, tool, tmp_path, monkeypatch):
        # An exempted file must still be checked on its OTHER lines, or one
        # justification would blanket-silence a whole document.
        root = self._tree(
            tmp_path,
            {"CLAUDE.md": "legacy note about `forgelm/gone.py`\nlive `forgelm/alive.py`\n"},
        )
        monkeypatch.setitem(tool._EXEMPT, "CLAUDE.md", frozenset({"legacy note"}))
        assert tool.main(["--repo-root", str(root), "--strict"]) == 1

    def test_notebook_json_is_scanned(self, tool, tmp_path, capsys):
        root = self._tree(
            tmp_path,
            {"notebooks/demo.ipynb": '{"cells":[{"source":["from forgelm/safety.py import x"]}]}\n'},
        )
        assert tool.main(["--repo-root", str(root), "--strict"]) == 1
        assert "notebooks/demo.ipynb" in capsys.readouterr().out

    def test_site_html_is_scanned(self, tool, tmp_path, capsys):
        root = self._tree(tmp_path, {"site/compliance.html": "<b>forgelm/safety.py</b>\n"})
        assert tool.main(["--repo-root", str(root), "--strict"]) == 1
        assert "site/compliance.html" in capsys.readouterr().out

    def test_skill_trees_are_scanned(self, tool, tmp_path, capsys):
        root = self._tree(
            tmp_path,
            {
                ".claude/skills/review-pr/SKILL.md": "see `forgelm/gone.py`\n",
                ".agents/skills/review-pr/SKILL.md": "see `forgelm/gone.py`\n",
            },
        )
        assert tool.main(["--repo-root", str(root), "--strict"]) == 1
        out = capsys.readouterr().out
        assert ".claude/skills/review-pr/SKILL.md" in out
        assert ".agents/skills/review-pr/SKILL.md" in out

    def test_missing_repo_root_errors(self, tool, tmp_path):
        assert tool.main(["--repo-root", str(tmp_path / "nope"), "--strict"]) == 1

    def test_quiet_suppresses_only_the_success_line(self, tool, tmp_path, capsys):
        root = self._tree(tmp_path, {"CLAUDE.md": "nothing here\n"})
        assert tool.main(["--repo-root", str(root), "--strict", "--quiet"]) == 0
        assert capsys.readouterr().out == ""

    def test_quiet_does_not_suppress_failures(self, tool, tmp_path, capsys):
        root = self._tree(tmp_path, {"CLAUDE.md": "see `forgelm/gone.py`\n"})
        assert tool.main(["--repo-root", str(root), "--strict", "--quiet"]) == 1
        assert "forgelm/gone.py" in capsys.readouterr().out

    def test_unreadable_file_fails_closed(self, tool, tmp_path, capsys):
        # A non-UTF-8 surface must be reported, not silently skipped — going
        # green on input the guard never read is the failure mode this
        # project outlaws.
        root = tmp_path
        (root / "CLAUDE.md").write_bytes(b"\xff\xfe invalid utf-8 \xff")
        assert tool.main(["--repo-root", str(root), "--strict"]) == 1
        assert "unreadable" in capsys.readouterr().out


# --------------------------------------------------------------------------
# Regression — the incident that motivated the guard
# --------------------------------------------------------------------------


class TestSafetySplitRegression:
    """Each shape that carried a dangling ref through the safety split."""

    @pytest.mark.parametrize(
        "rel,body",
        [
            ("CLAUDE.md", "│   ├── safety.py            # Llama Guard\nforgelm/safety.py\n"),
            ("docs/standards/architecture.md", "The gate lives in `forgelm/safety.py`.\n"),
            (".claude/skills/add-trainer-feature/SKILL.md", "- [ ] `forgelm/safety.py`\n"),
            (".agents/skills/add-trainer-feature/SKILL.md", "- [ ] `forgelm/safety.py`\n"),
            ("site/compliance.html", "<p>Implemented in forgelm/safety.py</p>\n"),
            ("notebooks/safety_evaluation.ipynb", '{"source":["forgelm/safety.py"]}\n'),
        ],
    )
    def test_each_dangling_shape_is_caught(self, tool, tmp_path, rel, body):
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        assert tool.main(["--repo-root", str(tmp_path), "--strict"]) == 1

    def test_the_post_split_layout_is_clean(self, tool, tmp_path):
        (tmp_path / "forgelm" / "safety").mkdir(parents=True)
        (tmp_path / "forgelm" / "safety" / "_gates.py").write_text("", encoding="utf-8")
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "a.md").write_text("The gate lives in `forgelm/safety/_gates.py`.\n", encoding="utf-8")
        assert tool.main(["--repo-root", str(tmp_path), "--strict"]) == 0


# --------------------------------------------------------------------------
# Exemption-table hygiene
# --------------------------------------------------------------------------


class TestExemptionHygiene:
    def test_every_exempt_file_exists(self, tool):
        """A stale exemption is a rationale that has rotted into a lie."""
        missing = [rel for rel in tool._EXEMPT if not (_REPO_ROOT / rel).exists()]
        assert not missing, f"_EXEMPT names non-existent file(s): {missing}"

    def test_every_exempt_file_is_a_scanned_surface(self, tool):
        """An exemption for a file the guard never scans silences nothing.

        Caught during this guard's own review: an entry for
        ``tools/check_source_path_refs.py`` (whose docstring names dead paths
        as worked examples) read as load-bearing but was inert, because
        ``tools/*.py`` is code rather than a prose surface. A dead exemption is
        worse than none — the next maintainer trusts it.
        """
        unscanned = [rel for rel in tool._EXEMPT if not tool._is_scanned(rel)]
        assert not unscanned, f"_EXEMPT entries for files the guard never scans: {unscanned}"

    def test_no_exemption_is_empty(self, tool):
        """An empty substring set silences nothing and misleads the reader."""
        empty = [rel for rel, needles in tool._EXEMPT.items() if not needles]
        assert not empty, f"_EXEMPT entries with no substrings: {empty}"

    def test_no_exemption_substring_is_trivially_broad(self, tool):
        """A 1-3 character needle would blanket-silence its file."""
        broad = [(rel, needle) for rel, needles in tool._EXEMPT.items() for needle in needles if len(needle) < 8]
        assert not broad, f"suspiciously broad _EXEMPT substrings: {broad}"

    def test_every_exemption_still_fires(self, tool):
        """An exemption whose line was since rewritten is dead weight — the
        justification no longer describes anything in the file."""
        unused = []
        for rel, needles in tool._EXEMPT.items():
            path = _REPO_ROOT / rel
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            for needle in needles:
                if needle not in text:
                    unused.append((rel, needle))
        assert not unused, f"_EXEMPT substrings that match nothing: {unused}"


# --------------------------------------------------------------------------
# Live repo
# --------------------------------------------------------------------------


class TestRealRepo:
    def test_repo_is_clean(self, tool, capsys):
        exit_code = tool.main(["--strict", "--quiet"])
        assert exit_code == 0, capsys.readouterr().out

    def test_guard_scans_a_nontrivial_corpus(self, tool):
        """A misconfigured surface filter that matched nothing would make the
        clean assertion above vacuous."""
        surfaces = tool._enumerate_surfaces(_REPO_ROOT)
        assert len(surfaces) > 100, f"only {len(surfaces)} surfaces enumerated"

    def test_all_four_surface_families_are_represented(self, tool):
        surfaces = tool._enumerate_surfaces(_REPO_ROOT)
        assert any(s.startswith("docs/") for s in surfaces)
        assert any(s.startswith(".claude/skills/") for s in surfaces)
        assert any(s.startswith(".agents/skills/") for s in surfaces)
        assert any(s.startswith("site/") for s in surfaces)
        assert "CLAUDE.md" in surfaces
