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
import statistics
import sys
import time
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TOOL_PATH = _REPO_ROOT / "tools" / "check_source_path_refs.py"
_ANCHOR_TOOL_PATH = _REPO_ROOT / "tools" / "check_anchor_resolution.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _load_tool():
    return _load(_TOOL_PATH, "check_source_path_refs")


@pytest.fixture(scope="module")
def tool():
    return _load_tool()


@pytest.fixture(scope="module")
def anchor_tool():
    """The *other* guard, loaded so the boundary between them can be measured
    rather than asserted in a docstring."""
    return _load(_ANCHOR_TOOL_PATH, "check_anchor_resolution")


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
# The boundary with check_anchor_resolution.py — measured, not asserted
# --------------------------------------------------------------------------


# Every prose surface family that can carry a Markdown link, with the number of
# directory levels between it and the repo root (so a relative href to a
# repo-root path can be constructed for each).
_LINK_BEARING_SURFACES: tuple[str, ...] = (
    "CLAUDE.md",
    "AGENTS.md",
    "README.md",
    "CONTRIBUTING.md",
    ".claude/skills/review-pr/SKILL.md",
    ".agents/skills/review-pr/SKILL.md",
    "docs/standards/coding.md",
    "docs/usermanuals/en/reference/json-output.md",
    "notebooks/demo.ipynb",
    # Record surfaces: skipped ENTIRELY by this guard, but still scanned by the
    # anchor guard, so their hrefs remain covered. The hand-off runs both ways.
    "docs/roadmap/releases.md",
    "docs/design/library_api.md",
)

# NOT in the list above: ``docs/analysis/**``. Neither guard covers it — this
# one treats it as a record surface, the anchor guard excludes it by default —
# and that is correct rather than a gap: it is gitignored working memory, absent
# from a fresh clone, and check_no_analysis_refs.py forbids the public tree from
# citing it at all.


def _relative_href(rel: str, target: str) -> str:
    """Build an href from *rel* to repo-root-relative *target*."""
    depth = len(Path(rel).parts) - 1
    return "../" * depth + target


class TestAnchorGuardBoundary:
    """The two guards must jointly cover every ``[text](href)`` to a source path.

    The first version of this guard stripped hrefs unconditionally, justified by
    the claim that ``check_anchor_resolution.py`` owns Markdown links. That guard
    runs with ``--scope docs``, so the claim held only inside ``docs/``: a
    ``[the module](forgelm/safety.py)`` link in ``CLAUDE.md`` was stripped here,
    never seen there, and passed both guards green.

    These tests re-derive the boundary by executing BOTH guards, so neither
    guard's scope can change without failing here. A docstring cannot do that.
    """

    @staticmethod
    def _tree(tmp_path: Path, rel: str, body: str) -> Path:
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        # The anchor guard errors out if its scope directory is absent.
        (tmp_path / "docs").mkdir(parents=True, exist_ok=True)
        return tmp_path

    @pytest.mark.parametrize("rel", _LINK_BEARING_SURFACES)
    def test_a_dead_link_is_caught_by_at_least_one_guard(self, tool, anchor_tool, tmp_path, rel):
        href = _relative_href(rel, "forgelm/gone.py")
        root = self._tree(tmp_path, rel, f"see [the module]({href}) now\n")

        path_refs = tool.main(["--repo-root", str(root), "--strict", "--quiet"])
        anchors = anchor_tool.main(["--repo-root", str(root), "--strict", "--quiet"])

        assert 1 in (path_refs, anchors), (
            f"{rel}: a Markdown link to a non-existent source path passed BOTH guards "
            f"(path-refs={path_refs}, anchors={anchors}) — the gap is open again"
        )

    @pytest.mark.parametrize("rel", _LINK_BEARING_SURFACES)
    def test_exactly_one_guard_reports_it(self, tool, anchor_tool, tmp_path, rel):
        """Joint coverage is not enough — double-reporting a single defect
        trains maintainers to fix it twice or to distrust one of the guards."""
        href = _relative_href(rel, "forgelm/gone.py")
        root = self._tree(tmp_path, rel, f"see [the module]({href}) now\n")

        path_refs = tool.main(["--repo-root", str(root), "--strict", "--quiet"])
        anchors = anchor_tool.main(["--repo-root", str(root), "--strict", "--quiet"])

        assert (path_refs, anchors).count(1) == 1, f"{rel}: reported by both guards"

    def test_the_anchor_guard_genuinely_does_not_see_non_docs_surfaces(self, anchor_tool, tmp_path):
        """The premise this guard's href handling rests on.

        If the anchor guard ever widens its scope past ``docs/``, this test
        fails and the hand-off in ``_anchor_guard_covers`` must be rewidened to
        match — rather than the two guards silently both reporting.
        """
        (tmp_path / "docs").mkdir()
        (tmp_path / "CLAUDE.md").write_text("see [x](forgelm/gone.py)\n", encoding="utf-8")
        assert anchor_tool.main(["--repo-root", str(tmp_path), "--strict", "--quiet"]) == 0

    @pytest.mark.parametrize(
        "rel,covered",
        [
            ("docs/standards/coding.md", True),
            ("docs/usermanuals/tr/reference/json-output.md", True),
            # Excluded from the anchor guard by its default ``--exclude analysis``.
            ("docs/analysis/notes.md", False),
            # Under docs/ but not Markdown — the anchor guard globs ``*.md``.
            ("docs/assets/data.json", False),
            # Outside the anchor guard's ``--scope docs`` entirely.
            ("CLAUDE.md", False),
            ("README.md", False),
            (".claude/skills/review-pr/SKILL.md", False),
            (".agents/skills/review-pr/SKILL.md", False),
            ("site/compliance.html", False),
            ("notebooks/demo.ipynb", False),
        ],
    )
    def test_anchor_guard_covers_mirrors_the_live_invocation(self, tool, rel, covered):
        assert tool._anchor_guard_covers(rel) is covered

    def test_docs_hrefs_are_not_checked_here(self, tool, tmp_path):
        # Delegation, positively asserted: the same dead href that fails in
        # CLAUDE.md must pass here when it lives under docs/.
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "x.md").write_text("see [m](../forgelm/gone.py)\n", encoding="utf-8")
        assert tool.main(["--repo-root", str(tmp_path), "--strict"]) == 0

    def test_a_line_naming_the_same_path_twice_reports_it_once(self, tool, tmp_path, capsys):
        """One defect, one finding.

        The input matters. ``[`forgelm/x.py`](forgelm/x.py)`` does NOT exercise
        the de-duplicator: ``_strip_markdown_links`` replaces every occurrence
        of the href inside the matched link, which blanks the identical link
        text too, leaving a single reference. Deleting the de-duplicator
        survived that input. The shape that actually collides is a backticked
        path elsewhere on the same line as a link to it.
        """
        (tmp_path / "CLAUDE.md").write_text(
            "see `forgelm/gone.py` in [the module](forgelm/gone.py)\n", encoding="utf-8"
        )
        assert tool.main(["--repo-root", str(tmp_path), "--strict"]) == 1
        assert capsys.readouterr().out.count("✗") == 1


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
# Unit — relative references
# --------------------------------------------------------------------------


class TestRelativeReferences:
    """Relative paths resolve against the referring file's directory.

    The original ``_PATH_RE`` look-behind ``(?<![A-Za-z0-9_./-])`` was
    documented as rejecting a match that continues a longer path
    (``docs/forgelm/x.py``). The same ``/`` in that class silently rejected
    ``../forgelm/x.py`` too — a shape that is not merely legal but is the
    NORMAL form for a Markdown href from a nested file.
    """

    @pytest.mark.parametrize(
        "line,expected",
        [
            ("see `../forgelm/safety.py`", ["../forgelm/safety.py"]),
            ("see `../../forgelm/safety.py`", ["../../forgelm/safety.py"]),
            ('<a href="../tools/check_bandit.py">x</a>', ["../tools/check_bandit.py"]),
            ("the `../forgelm/safety/` package", ["../forgelm/safety/"]),
        ],
    )
    def test_relative_prose_paths_are_matched(self, tool, line, expected):
        assert _matches(tool, line) == expected

    def test_a_longer_path_is_still_rejected(self, tool):
        # The regression the look-behind exists to prevent must survive the
        # addition of the ``../`` prefix.
        assert _matches(tool, "vendored at `third_party/forgelm/safety.py`") == []
        assert _matches(tool, "see `docs/forgelm/safety.py`") == []

    def test_relative_prose_path_resolves_against_the_referring_directory(self, tool, tmp_path):
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "x.md").write_text("see `../forgelm/live.py`\n", encoding="utf-8")
        (tmp_path / "forgelm").mkdir()
        (tmp_path / "forgelm" / "live.py").write_text("", encoding="utf-8")
        assert tool.main(["--repo-root", str(tmp_path), "--strict"]) == 0

    def test_relative_prose_path_that_dangles_is_reported(self, tool, tmp_path, capsys):
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "x.md").write_text("see `../forgelm/gone.py`\n", encoding="utf-8")
        assert tool.main(["--repo-root", str(tmp_path), "--strict"]) == 1
        assert "../forgelm/gone.py" in capsys.readouterr().out

    def test_a_reference_climbing_above_the_repo_root_is_dropped(self, tool, tmp_path):
        # ``../../forgelm/x.py`` from a top-level file names something outside
        # the tree; it is not a checkable claim about THIS repo, so it must not
        # be reported as a dead in-repo path.
        (tmp_path / "CLAUDE.md").write_text("see `../../forgelm/elsewhere.py`\n", encoding="utf-8")
        assert tool.main(["--repo-root", str(tmp_path), "--strict"]) == 0

    def test_a_relative_href_resolves_against_the_referring_directory(self, tool, tmp_path):
        skill = tmp_path / ".claude" / "skills" / "review-pr"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("see [m](../../../forgelm/live.py)\n", encoding="utf-8")
        (tmp_path / "forgelm").mkdir()
        (tmp_path / "forgelm" / "live.py").write_text("", encoding="utf-8")
        assert tool.main(["--repo-root", str(tmp_path), "--strict"]) == 0

    def test_a_repo_rooted_href_from_a_nested_file_uses_the_root_fallback(self, tool, tmp_path):
        # check_anchor_resolution.py::_locate_target accepts a legacy
        # repo-rooted href from a nested file. This guard must not reject what
        # that guard accepts, or the two disagree on the same corpus.
        skill = tmp_path / ".claude" / "skills" / "review-pr"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("see [m](forgelm/live.py)\n", encoding="utf-8")
        (tmp_path / "forgelm").mkdir()
        (tmp_path / "forgelm" / "live.py").write_text("", encoding="utf-8")
        assert tool.main(["--repo-root", str(tmp_path), "--strict"]) == 0

    def test_a_repo_rooted_href_from_a_nested_file_is_still_CHECKED(self, tool, tmp_path, capsys):
        """The negative half of the root-fallback contract.

        Asserting only that a LIVE repo-rooted href passes is vacuous: dropping
        the fallback entirely would also make it pass, because the
        directory-relative candidate (``.claude/skills/review-pr/forgelm/...``)
        is filtered out for not lying under a source root. Deleting the
        fallback survived that test and was caught only by this one.
        """
        skill = tmp_path / ".claude" / "skills" / "review-pr"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("see [m](forgelm/gone.py)\n", encoding="utf-8")
        assert tool.main(["--repo-root", str(tmp_path), "--strict"]) == 1
        assert "forgelm/gone.py" in capsys.readouterr().out

    @pytest.mark.parametrize(
        "href",
        [
            "https://github.com/HodeTech/ForgeLM/blob/main/forgelm/gone.py",
            "http://example.invalid/forgelm/gone.py",
            "mailto:someone@example.com",
            "#/reference/json-output",
            "#a-heading",
            "/forgelm/gone.py",
            "tel:+900000000",
        ],
    )
    def test_hrefs_that_make_no_in_repo_claim_are_skipped(self, tool, tmp_path, href):
        (tmp_path / "CLAUDE.md").write_text(f"see [m]({href})\n", encoding="utf-8")
        assert tool.main(["--repo-root", str(tmp_path), "--strict"]) == 0

    @pytest.mark.parametrize(
        "href",
        [
            "http://a/../../forgelm/gone.py",
            "https://x/y/../../../forgelm/gone.py",
        ],
    )
    def test_an_external_url_cannot_climb_into_the_source_roots(self, tool, tmp_path, href):
        """Why ``_HREF_SKIP_RE`` is load-bearing rather than decorative.

        Without the scheme pre-filter, ``http://a/../../forgelm/gone.py``
        normalises to exactly ``forgelm/gone.py`` — the ``..`` segments eat the
        host and then the ``http:`` segment — and the guard would report a dead
        in-repo path for a URL that names no repo file at all. Every *other*
        external URL shape is filtered incidentally by ``_is_source_shaped``,
        so this is the one input that kills a mutant deleting the pre-filter.
        """
        (tmp_path / "CLAUDE.md").write_text(f"see [m]({href})\n", encoding="utf-8")
        assert tool.main(["--repo-root", str(tmp_path), "--strict"]) == 0

    def test_an_href_anchor_and_query_are_stripped_before_resolution(self, tool, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("see [m](forgelm/live.py#L10)\n", encoding="utf-8")
        (tmp_path / "forgelm").mkdir()
        (tmp_path / "forgelm" / "live.py").write_text("", encoding="utf-8")
        assert tool.main(["--repo-root", str(tmp_path), "--strict"]) == 0

    def test_an_href_outside_the_source_roots_is_not_this_guards_business(self, tool, tmp_path):
        # Target axis: only forgelm/, tools/, tests/. A dead docs/ href in
        # CLAUDE.md is out of scope by design (see the scope study) — widening
        # here would reintroduce the noise class that study removed.
        (tmp_path / "CLAUDE.md").write_text("see [m](docs/reference/gone.md)\n", encoding="utf-8")
        assert tool.main(["--repo-root", str(tmp_path), "--strict"]) == 0

    def test_a_relative_reference_resolving_out_of_the_source_roots_is_dropped(self, tool, tmp_path):
        # ``../forgelm/x.py`` from docs/standards/ resolves to docs/forgelm/x.py,
        # which is not under a source root; out of scope rather than reported
        # against a path the author never wrote.
        std = tmp_path / "docs" / "standards"
        std.mkdir(parents=True)
        (std / "x.md").write_text("see `../forgelm/gone.py`\n", encoding="utf-8")
        assert tool.main(["--repo-root", str(tmp_path), "--strict"]) == 0

    def test_a_reference_style_link_definition_is_caught(self, tool, tmp_path, capsys):
        # ``[ref]: forgelm/gone.py`` is not an inline link, so neither guard's
        # link regex sees it — but it is bare prose, which this guard matches.
        (tmp_path / "CLAUDE.md").write_text("[ref]: forgelm/gone.py\n", encoding="utf-8")
        assert tool.main(["--repo-root", str(tmp_path), "--strict"]) == 1
        assert "forgelm/gone.py" in capsys.readouterr().out

    def test_an_html_attribute_reference_is_caught(self, tool, tmp_path, capsys):
        site = tmp_path / "site"
        site.mkdir()
        (site / "compliance.html").write_text('<a href="../forgelm/gone.py">gate</a>\n', encoding="utf-8")
        assert tool.main(["--repo-root", str(tmp_path), "--strict"]) == 1
        assert "../forgelm/gone.py" in capsys.readouterr().out

    def test_a_table_cell_reference_is_caught(self, tool, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("| gate | `forgelm/gone.py` | yes |\n", encoding="utf-8")
        assert tool.main(["--repo-root", str(tmp_path), "--strict"]) == 1

    def test_a_notebook_markdown_cell_href_is_caught(self, tool, tmp_path):
        nb = tmp_path / "notebooks"
        nb.mkdir()
        (nb / "demo.ipynb").write_text(
            '{"cells":[{"cell_type":"markdown","source":["see [m](../forgelm/gone.py)"]}]}\n',
            encoding="utf-8",
        )
        assert tool.main(["--repo-root", str(tmp_path), "--strict"]) == 1

    def test_a_path_wrapped_across_a_line_break_is_documented_as_missed(self, tool, tmp_path):
        """Known limitation, pinned so it is a decision rather than a surprise.

        Matching is per physical line. If this ever starts failing, someone
        added multi-line joining and the module docstring's "Known limitation"
        paragraph must be updated to match.
        """
        (tmp_path / "CLAUDE.md").write_text("see `forgelm/\ngone.py`\n", encoding="utf-8")
        assert tool.main(["--repo-root", str(tmp_path), "--strict"]) == 0


# --------------------------------------------------------------------------
# Unit — ReDoS linearity (docs/standards/regex.md, ReDoS exposure budget)
# --------------------------------------------------------------------------


class TestPatternLinearity:
    """The ``(?:\\.\\./){0,8}`` prefix is bounded and cannot compete with the
    body quantifier — the mandatory root alternation sits between them. Pinned
    empirically per the standard: 1K/5K/10K, median of 5, ~linear growth."""

    @pytest.mark.parametrize(
        "label,build",
        [
            ("bounded prefix, never reaches a root", lambda n: "../" * n),
            ("body quantifier, no extension ever", lambda n: "forgelm/" + "a/" * n),
            ("root and prefix interleaved", lambda n: "tools/../" * n),
            ("near-miss tail", lambda n: "." * n + "forgelm/x.p"),
        ],
    )
    def test_growth_is_approximately_linear(self, tool, label, build):
        timings = {}
        for n in (1_000, 5_000, 10_000):
            payload = build(n)
            runs = []
            for _ in range(5):
                start = time.perf_counter()
                list(tool._PATH_RE.finditer(payload))
                runs.append(time.perf_counter() - start)
            timings[n] = statistics.median(runs)

        # Safety floor: a real ReDoS blows past this by orders of magnitude.
        assert timings[10_000] < 1.0, f"{label}: 10K input took {timings[10_000]:.3f}s"
        # Shape: 10x the input must not cost anywhere near 100x the time.
        # A generous 20x ceiling absorbs CI jitter while still failing on the
        # quadratic blow-up that a competing-quantifier regression would show.
        ratio = timings[10_000] / max(timings[1_000], 1e-9)
        assert ratio < 20, f"{label}: 10x input cost {ratio:.1f}x time (super-linear)"


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
