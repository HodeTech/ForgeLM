"""Phase 24 — `tools/check_bilingual_parity.py` regression tests.

The bilingual parity tool is a CI gate: a regression that silently
under-reports drift would let a mirror skew without anyone noticing.
We pin the recognition contracts that the CI gate's correctness rests
on:

1. Heading extraction skips fenced code blocks.
2. Heading-shaped lines INSIDE a fenced block are NOT treated as
   headings (otherwise ``# noqa: E402`` in a Python snippet would
   register as an H1).
3. Identical H2/H3/H4 spines pass; any drift fails (count diff,
   reorder, depth change, missing-mirror).
4. ``--strict`` flips the exit code; advisory mode reports anyway.
5. The canonical pair set in the repo passes ``--strict`` against
   the live filesystem (the same gate CI runs).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# Load the tool from its file path without leaking ``tools/`` onto
# ``sys.path``.  Same pattern as ``tests/test_check_field_descriptions.py``.
_TOOLS_DIR = Path(__file__).parent.parent / "tools"
_PARITY_PATH = _TOOLS_DIR / "check_bilingual_parity.py"
_spec = importlib.util.spec_from_file_location("check_bilingual_parity", _PARITY_PATH)
assert _spec is not None and _spec.loader is not None, f"could not load parity-tool spec from {_PARITY_PATH!r}"
check_bilingual_parity = importlib.util.module_from_spec(_spec)
sys.modules["check_bilingual_parity"] = check_bilingual_parity
_spec.loader.exec_module(check_bilingual_parity)

extract_headings = check_bilingual_parity.extract_headings
diff_pair = check_bilingual_parity.diff_pair
scan_pairs = check_bilingual_parity.scan_pairs
main = check_bilingual_parity.main


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Heading extraction
# ---------------------------------------------------------------------------


class TestExtractHeadings:
    def test_atx_headings_at_each_depth(self, tmp_path: Path) -> None:
        src = _write(
            tmp_path / "doc.md",
            "# Title\n## H2 one\n### H3\n#### H4\n##### H5\n###### H6\nbody\n",
        )
        headings = extract_headings(src)
        assert [h.level for h in headings] == [1, 2, 3, 4, 5, 6]
        assert headings[0].text == "Title"
        assert headings[2].text == "H3"

    def test_fenced_code_blocks_ignored(self, tmp_path: Path) -> None:
        """Heading-shaped lines inside a code block are CONTENT, not
        document structure.  A regression that re-treated them as
        headings would inflate the parity tool's counts on every
        Python doc and produce false negatives for translators."""
        src = _write(
            tmp_path / "doc.md",
            ("## Real H2\n```python\n# noqa: E402\n## not a real heading\n```\n## Second real H2\n"),
        )
        headings = extract_headings(src)
        assert [h.level for h in headings] == [2, 2]
        assert [h.text for h in headings] == ["Real H2", "Second real H2"]

    def test_tilde_fence_also_recognised(self, tmp_path: Path) -> None:
        src = _write(
            tmp_path / "doc.md",
            "## H2\n~~~yaml\n## not heading\n~~~\n## After\n",
        )
        headings = extract_headings(src)
        assert [h.level for h in headings] == [2, 2]

    def test_longer_opening_fence_not_closed_by_shorter_run(self, tmp_path: Path) -> None:
        """CommonMark §4.5: a closing fence must use the same marker
        character AND be at least as long as the opening fence.  A
        4-backtick opener is NOT closed by a 3-backtick line; the
        ``## inside`` line in between must stay treated as code, not
        as a heading."""
        src = _write(
            tmp_path / "doc.md",
            "## Real H2\n````\n## inside long fence\n```\n## still inside\n````\n## After close\n",
        )
        headings = extract_headings(src)
        assert [h.level for h in headings] == [2, 2]
        assert [h.text for h in headings] == ["Real H2", "After close"]

    def test_backtick_fence_not_closed_by_tilde_fence(self, tmp_path: Path) -> None:
        """CommonMark §4.5: closing fence must use the same marker
        character as the opener.  A ``~~~`` line cannot close a
        ```` ``` ```` fence."""
        src = _write(
            tmp_path / "doc.md",
            "## Real H2\n```\n## inside backtick fence\n~~~\n## still inside\n```\n## After close\n",
        )
        headings = extract_headings(src)
        assert [h.level for h in headings] == [2, 2]
        assert [h.text for h in headings] == ["Real H2", "After close"]

    def test_shorter_opening_fence_closed_by_longer_run(self, tmp_path: Path) -> None:
        """F-W3FU-T-06 regression: CommonMark §4.5 allows a closer
        LONGER than the opener (the spec says "at least as long as",
        not "equal to").  Pin the production ``len(marker) >=
        len(fence_marker)`` semantics so a hand-edit dropping ``>=``
        to ``==`` would surface in CI."""
        src = _write(
            tmp_path / "doc.md",
            "## Real H2\n```\n## inside short opener\n````\n## After\n",
        )
        headings = extract_headings(src)
        assert [h.level for h in headings] == [2, 2]
        assert [h.text for h in headings] == ["Real H2", "After"]

    def test_setext_underline_after_atx_heading_does_not_register(self, tmp_path: Path) -> None:
        """F-W3FU-T-09 regression: ``test_setext_headings_are_not_matched``
        pins the case where setext appears at the start of a doc.
        This sibling test pins the case where a setext underline
        appears AFTER an ATX heading — production should still skip
        the setext (only ATX is recognised)."""
        src = _write(
            tmp_path / "doc.md",
            "## ATX H2 first\nSome paragraph\n=========\n## After\n",
        )
        headings = extract_headings(src)
        assert [h.level for h in headings] == [2, 2]
        assert [h.text for h in headings] == ["ATX H2 first", "After"]

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert extract_headings(tmp_path / "missing.md") == []

    def test_setext_headings_are_not_matched(self, tmp_path: Path) -> None:
        """F-W3T-03 regression: setext (``===``, ``---``) underlines
        must NOT register as headings.  The project's docs use ATX
        exclusively per ``docs/standards/documentation.md`` and a setext
        heading in a mirror is itself a lint finding (see ``_HEADING_RE``
        comment in the production code).  A regex tweak that accidentally
        widened the matcher to setext would silently inflate the parity
        tool's counts on every doc that mixes setext."""
        src = _write(
            tmp_path / "doc.md",
            "Setext H1\n=========\n## Real H2\nSetext H2\n---------\n",
        )
        headings = extract_headings(src)
        assert [h.level for h in headings] == [2]
        assert [h.text for h in headings] == ["Real H2"]


# ---------------------------------------------------------------------------
# Pair diff: pass / fail / missing
# ---------------------------------------------------------------------------


class TestDiffPair:
    def test_identical_spines_pass(self, tmp_path: Path) -> None:
        en = _write(tmp_path / "en.md", "## A\n### A1\n## B\n#### B1a\n")
        tr = _write(tmp_path / "tr.md", "## E\n### E1\n## D\n#### D1a\n")  # different text, same spine
        assert diff_pair(en, tr) is None

    def test_h3_count_drift_flagged(self, tmp_path: Path) -> None:
        en = _write(tmp_path / "en.md", "## A\n### A1\n### A2\n")
        tr = _write(tmp_path / "tr.md", "## A\n### A1\n")
        drift = diff_pair(en, tr)
        assert drift is not None
        assert "H3: EN=2  TR=1" in "\n".join(drift.detail)

    def test_demoted_section_flagged(self, tmp_path: Path) -> None:
        """``## Foo`` in EN but ``### Foo`` in TR — both have one
        heading at the index, but the level differs.  Reports it as
        a per-line mismatch even though counts at one level may match."""
        en = _write(tmp_path / "en.md", "## A\n## B\n")
        tr = _write(tmp_path / "tr.md", "## A\n### B\n")
        drift = diff_pair(en, tr)
        assert drift is not None
        rendered = drift.render()
        assert "EN:H2" in rendered and "TR:H3" in rendered

    def test_missing_tr_mirror_diagnosed(self, tmp_path: Path) -> None:
        en = _write(tmp_path / "en.md", "## A\n")
        drift = diff_pair(en, tmp_path / "tr.md")
        assert drift is not None
        assert "TR mirror is missing" in drift.summary

    def test_missing_en_diagnosed(self, tmp_path: Path) -> None:
        tr = _write(tmp_path / "tr.md", "## A\n")
        drift = diff_pair(tmp_path / "en.md", tr)
        assert drift is not None
        assert "EN file is missing" in drift.summary

    def test_levels_filter_restricts_comparison(self, tmp_path: Path) -> None:
        """``levels=(2,)`` mirrors the legacy CI behaviour — H2 only.
        Drift in H3 must NOT be reported under that filter."""
        en = _write(tmp_path / "en.md", "## A\n### A1\n## B\n")
        tr = _write(tmp_path / "tr.md", "## A\n## B\n")  # missing H3
        # Under H2-only filter, the spines match (2 H2's each).
        assert diff_pair(en, tr, levels=(2,)) is None
        # Under H2+H3 (default), the H3 drift is reported.
        assert diff_pair(en, tr) is not None


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


class TestCli:
    def test_strict_returns_one_on_drift(self, tmp_path: Path, monkeypatch) -> None:
        # Plant a fake repo with a single drifted pair.
        repo = tmp_path / "repo"
        _write(repo / "docs" / "guide.md", "## A\n### A1\n")
        _write(repo / "docs" / "guide-tr.md", "## A\n")
        monkeypatch.setattr(check_bilingual_parity, "_PAIRS", (("docs/guide.md", "docs/guide-tr.md"),))
        rc = main(["--strict", "--repo-root", str(repo)])
        assert rc == 1

    def test_advisory_mode_returns_zero_even_on_drift(self, tmp_path: Path, monkeypatch) -> None:
        repo = tmp_path / "repo"
        _write(repo / "docs" / "guide.md", "## A\n### A1\n")
        _write(repo / "docs" / "guide-tr.md", "## A\n")
        monkeypatch.setattr(check_bilingual_parity, "_PAIRS", (("docs/guide.md", "docs/guide-tr.md"),))
        rc = main(["--repo-root", str(repo)])  # no --strict
        assert rc == 0

    def test_clean_pair_strict_returns_zero(self, tmp_path: Path, monkeypatch) -> None:
        repo = tmp_path / "repo"
        _write(repo / "docs" / "guide.md", "## A\n### A1\n")
        _write(repo / "docs" / "guide-tr.md", "## E\n### E1\n")
        monkeypatch.setattr(check_bilingual_parity, "_PAIRS", (("docs/guide.md", "docs/guide-tr.md"),))
        rc = main(["--strict", "--repo-root", str(repo)])
        assert rc == 0

    def test_only_filter_restricts_to_one_pair(self, tmp_path: Path, monkeypatch) -> None:
        repo = tmp_path / "repo"
        _write(repo / "docs" / "a.md", "## X\n")
        _write(repo / "docs" / "a-tr.md", "## X\n")
        # Drifted pair NOT in --only.
        _write(repo / "docs" / "b.md", "## A\n## B\n")
        _write(repo / "docs" / "b-tr.md", "## A\n")
        monkeypatch.setattr(
            check_bilingual_parity,
            "_PAIRS",
            (("docs/a.md", "docs/a-tr.md"), ("docs/b.md", "docs/b-tr.md")),
        )
        # Run with --only on the clean pair → should pass strict.
        rc = main(["--strict", "--repo-root", str(repo), "--only", "docs/a.md"])
        assert rc == 0

    def test_invalid_levels_rejected(self, tmp_path: Path) -> None:
        rc = main(["--levels", "0,7", "--repo-root", str(tmp_path)])
        assert rc == 1


# ---------------------------------------------------------------------------
# Live-repo smoke: the canonical pair set must pass --strict
# ---------------------------------------------------------------------------


class TestCanonicalRepoPasses:
    def test_repository_pairs_pass_strict(self) -> None:
        """The repo's actual EN/TR pairs must pass ``--strict`` so the
        CI gate stays green.  This is the test that fails first if a
        future PR drifts a mirror without fixing it.

        DELIBERATELY REDUNDANT with the CI lint job at
        ``.github/workflows/ci.yml`` (F-W3T-08 docstring) — a drift
        here surfaces in the pytest run too so contributors see it
        locally before push.  Do not delete one in favour of the
        other; they catch different categories of regression (this
        one catches a drift introduced in the same PR as the test
        edit; the lint job catches a drift introduced after the test
        ran)."""
        repo_root = Path(__file__).parent.parent
        rc = main(["--strict", "--repo-root", str(repo_root)])
        assert rc == 0, (
            "Live-repo strict parity check failed; run "
            "`python3 tools/check_bilingual_parity.py` for the per-pair "
            "drift report."
        )

    def test_every_tr_mirror_appears_in_pair_registry(self) -> None:
        """F-W3T-07 regression: a ``*-tr.md`` file added under the
        parity tool's curated domain (``docs/guides/`` +
        ``docs/reference/``) without a ``_PAIRS`` registry entry would
        silently bypass the strict gate.  This meta-test asserts the
        registry is the source of truth for those two directories.

        Out-of-scope for THIS registry test: ``docs/usermanuals/`` is
        now spine-gated via auto-discovery (``_usermanual_pairs``, see
        ``TestUsermanualSpineParity``), not the hand-written ``_PAIRS``
        registry — so it is checked elsewhere, not here.  Top-level docs
        like ``docs/roadmap-tr.md`` and ``docs/product_strategy-tr.md``
        diverge intentionally; gitignored working-memory directories
        (``docs/marketing/``, ``docs/analysis/``) are excluded by
        construction.

        Allowlist: ``safety_compliance-tr.md`` carries a known
        in-progress structural drift (34 H2/H3/H4 deltas as of Wave
        3); the EN → TR completion is tracked as a separate Wave 4
        translation task and the file would block this gate today.
        Remove from the allowlist once parity is achieved.
        """
        repo_root = Path(__file__).parent.parent
        scoped_dirs = [repo_root / "docs" / "guides", repo_root / "docs" / "reference"]
        # Files whose TR mirror is acknowledged as structurally
        # incomplete; do NOT add new entries here without an explicit
        # tracking ticket.
        in_progress_allowlist = {"docs/guides/safety_compliance-tr.md"}
        tr_files: list[str] = []
        for scoped_dir in scoped_dirs:
            if not scoped_dir.is_dir():
                continue
            for path in scoped_dir.rglob("*-tr.md"):
                tr_files.append(path.relative_to(repo_root).as_posix())
        tr_files.sort()
        registered = {tr for _, tr in check_bilingual_parity._PAIRS}
        orphans = [path for path in tr_files if path not in registered and path not in in_progress_allowlist]
        assert orphans == [], (
            f"{orphans} carry a TR mirror but are not registered in "
            "_PAIRS — register them in tools/check_bilingual_parity.py "
            "or the strict gate will silently skip drift detection."
        )


# ---------------------------------------------------------------------------
# F-P8-C-10 — usermanual page pairs are spine-gated via auto-discovery
# ---------------------------------------------------------------------------


class TestUsermanualSpineParity:
    def test_all_en_usermanual_pages_are_discovered(self) -> None:
        """Every ``docs/usermanuals/en/**/*.md`` page must be in the
        auto-discovered pair set.  Previously only 1 of 66 was registered,
        so 65 page pairs had no H2/H3/H4 spine gate (F-P8-C-10)."""
        repo_root = Path(__file__).parent.parent
        en_root = repo_root / "docs" / "usermanuals" / "en"
        en_pages = {f"docs/usermanuals/en/{p.relative_to(en_root).as_posix()}" for p in en_root.rglob("*.md")}
        discovered_en = {en for en, _ in check_bilingual_parity._usermanual_pairs(repo_root)}
        missing = en_pages - discovered_en
        assert missing == set(), f"usermanual pages without a spine-parity pair: {sorted(missing)}"
        # And the discovered set joins the full scanned set.
        all_en = {en for en, _ in check_bilingual_parity._all_pairs(repo_root)}
        assert en_pages <= all_en

    def test_missing_tr_usermanual_page_is_flagged(self, tmp_path: Path, monkeypatch) -> None:
        """A deleted TR usermanual page must surface as a missing-mirror
        drift — the silent EN-fallback gap the registry left open."""
        # Isolate to the discovered usermanual pairs: clear the explicit
        # registry so the absent guides/reference paths under tmp don't
        # add noise.
        monkeypatch.setattr(check_bilingual_parity, "_PAIRS", ())
        en_root = tmp_path / "docs" / "usermanuals" / "en" / "data"
        en_root.mkdir(parents=True)
        (en_root / "audit.md").write_text("# Audit\n\n## Overview\n", encoding="utf-8")
        # NOTE: no tr/ mirror is written → it is missing.
        rc = main(["--strict", "--repo-root", str(tmp_path)])
        assert rc == 1

    def test_drifted_tr_usermanual_spine_is_flagged(self, tmp_path: Path, monkeypatch) -> None:
        """A TR usermanual page missing an H2 section the EN page has must
        fail the strict gate now that usermanuals are spine-gated."""
        monkeypatch.setattr(check_bilingual_parity, "_PAIRS", ())
        base = tmp_path / "docs" / "usermanuals"
        en = base / "en" / "data" / "audit.md"
        tr = base / "tr" / "data" / "audit.md"
        en.parent.mkdir(parents=True)
        tr.parent.mkdir(parents=True)
        en.write_text("# Audit\n\n## Overview\n\n## Details\n", encoding="utf-8")
        tr.write_text("# Denetim\n\n## Genel Bakış\n", encoding="utf-8")  # missing 2nd H2
        rc = main(["--strict", "--repo-root", str(tmp_path)])
        assert rc == 1

    def test_clean_tr_usermanual_pair_passes(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(check_bilingual_parity, "_PAIRS", ())
        base = tmp_path / "docs" / "usermanuals"
        en = base / "en" / "data" / "audit.md"
        tr = base / "tr" / "data" / "audit.md"
        en.parent.mkdir(parents=True)
        tr.parent.mkdir(parents=True)
        en.write_text("# Audit\n\n## Overview\n\n## Details\n", encoding="utf-8")
        tr.write_text("# Denetim\n\n## Genel Bakış\n\n## Ayrıntılar\n", encoding="utf-8")
        rc = main(["--strict", "--repo-root", str(tmp_path)])
        assert rc == 0
