"""Tests for tools/check_release_record_sync.py.

The guard cross-checks three files that a release is supposed to touch
together: every ``## [X.Y.Z] — DATE`` heading in ``CHANGELOG.md`` must have a
non-planned section in ``docs/roadmap/releases.md``, and ``docs/roadmap.md``'s
``**Released:**`` headline must name the newest released version. The
post-release step that produces those two edits was skipped for v0.8.0 and
v0.9.0, leaving the public roadmap two minor versions behind PyPI.

Detection logic is pinned against synthetic in-memory fixtures so it stays
independent of the real, evolving corpus: a recorded version, a missing
version, a ``(Planned)`` section that must not count as a record, a
``**Status:** Planned`` section likewise, prerelease/rc handling, non-version
headings (``v0.7.x``, ``v0.6.0-pro``), and a malformed changelog heading. A
separate live-repo class asserts the invariant CI actually relies on —
``main(["--strict", "--quiet"]) == 0`` — mirroring
``tests/test_check_deprecation_targets.py``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TOOL_PATH = _REPO_ROOT / "tools" / "check_release_record_sync.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("check_release_record_sync", _TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_release_record_sync"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture(scope="module")
def tool():
    return _load_tool()


class TestParseVersionToken:
    @pytest.mark.parametrize("token", ["v0.9.0", "V0.9.0", "0.9.0"])
    def test_prefix_is_optional_and_case_insensitive(self, tool, token):
        # releases.md writes `v0.9.0`, CHANGELOG.md writes `[0.9.0]`; they have
        # to compare equal or every single release would report as missing.
        assert str(tool.parse_version_token(token)) == "0.9.0"

    def test_release_candidate_parses(self, tool):
        assert str(tool.parse_version_token("v0.3.1rc1")) == "0.3.1rc1"

    @pytest.mark.parametrize("token", ["v0.7.x", "v0.6.0-pro", "Release", "", "vNOPE"])
    def test_non_versions_return_none(self, tool, token):
        # These are real releases.md heading tokens. They must not raise and
        # must never match a released version.
        assert tool.parse_version_token(token) is None


class TestParseChangelogReleases:
    def test_unreleased_is_skipped(self, tool):
        text = "## [Unreleased]\n\n### Added\n- thing\n\n## [0.9.0] — 2026-07-05\n"
        assert [r.version for r in tool.parse_changelog_releases(text)] == ["0.9.0"]

    def test_unreleased_match_is_case_insensitive(self, tool):
        assert tool.parse_changelog_releases("## [unreleased]\n") == []

    def test_line_and_heading_are_carried_for_the_report(self, tool):
        text = "# Changelog\n\n## [0.8.0] — 2026-06-16\n"
        (release,) = tool.parse_changelog_releases(text)
        assert release.line == 3
        assert release.heading == "## [0.8.0] — 2026-06-16"
        assert release.is_malformed is False

    def test_prerelease_heading_is_a_release(self, tool):
        text = "## [0.3.1rc1] — 2026-03-28 (included in v0.4.0 branch)\n"
        (release,) = tool.parse_changelog_releases(text)
        assert str(release.parsed) == "0.3.1rc1"

    def test_malformed_heading_is_reported_not_dropped(self, tool):
        # A heading the guard cannot read is a heading it cannot enforce.
        # Silently skipping it would be a false green.
        text = "## [banana] — 2026-07-05\n"
        (release,) = tool.parse_changelog_releases(text)
        assert release.is_malformed is True
        assert release.version == "banana"

    def test_deeper_headings_are_not_releases(self, tool):
        assert tool.parse_changelog_releases("### [0.9.0] — 2026-07-05\n") == []

    def test_prose_mentioning_a_version_is_not_a_heading(self, tool):
        assert tool.parse_changelog_releases("See `[0.9.0]` for details.\n") == []


class TestParseReleaseEntries:
    def test_titled_heading_is_parsed(self, tool):
        text = '## v0.7.0 — "Pipeline Chains" (2026-05-15)\n'
        (entry,) = tool.parse_release_entries(text)
        assert str(entry.parsed) == "0.7.0"
        assert entry.planned is False

    def test_bare_heading_without_a_date_is_parsed(self, tool):
        # `## v0.3.0 Release` and `## v0.5.0 — "..."` (no date) both occur.
        entries = tool.parse_release_entries('## v0.3.0 Release\n\n## v0.5.0 — "Ingestion"\n')
        assert [str(e.parsed) for e in entries] == ["0.3.0", "0.5.0"]

    def test_planned_heading_marker_is_detected(self, tool):
        text = '## v0.7.x — "Pipeline Hardening" (Planned)\n'
        (entry,) = tool.parse_release_entries(text)
        assert entry.planned is True

    def test_planned_with_a_qualifier_is_detected(self, tool):
        text = '## v0.6.0-pro — "Pro CLI" (Planned, gated)\n'
        (entry,) = tool.parse_release_entries(text)
        assert entry.planned is True

    def test_planned_status_line_is_detected(self, tool):
        # The heading looks like a real release; only the body says otherwise.
        text = '## v1.0.0 — "The Big One"\n\n**Status:** Planned. Focus: everything.\n'
        (entry,) = tool.parse_release_entries(text)
        assert entry.planned is True

    def test_non_planned_status_line_does_not_flip_the_flag(self, tool):
        # v0.3.1rc1's real status is "Folded into v0.4.0" — a shipped release
        # described unusually, which must still count as a record.
        text = '## v0.3.1rc1 — "Hardening" (2026-04-25)\n\n**Status:** Folded into v0.4.0\n'
        (entry,) = tool.parse_release_entries(text)
        assert entry.planned is False

    def test_planned_status_does_not_leak_into_the_next_section(self, tool):
        text = '## v0.9.0 — "Shipped" (2026-07-05)\n\nreal notes\n\n## v1.0.0 — "Next"\n\n**Status:** Planned.\n'
        shipped, upcoming = tool.parse_release_entries(text)
        assert shipped.planned is False
        assert upcoming.planned is True


class TestFindMissingReleases:
    def _changelog(self, tool, *versions):
        return tool.parse_changelog_releases("".join(f"## [{v}] — 2026-07-05\n" for v in versions))

    def test_recorded_version_is_not_missing(self, tool):
        releases = self._changelog(tool, "0.9.0")
        entries = tool.parse_release_entries('## v0.9.0 — "Shipped" (2026-07-05)\n')
        assert tool.find_missing_releases(releases, entries) == []

    def test_unrecorded_version_is_missing(self, tool):
        releases = self._changelog(tool, "0.9.0")
        entries = tool.parse_release_entries('## v0.7.0 — "Pipeline Chains" (2026-05-15)\n')
        assert [str(r.parsed) for r in tool.find_missing_releases(releases, entries)] == ["0.9.0"]

    def test_planned_entry_does_not_satisfy_a_released_version(self, tool):
        # THE regression case. A `(Planned)` section is a promise, not a record;
        # had one been allowed to count, the exact state this guard was written
        # for would have passed.
        releases = self._changelog(tool, "0.9.0")
        entries = tool.parse_release_entries('## v0.9.0 — "Someday" (Planned)\n')
        assert [str(r.parsed) for r in tool.find_missing_releases(releases, entries)] == ["0.9.0"]

    def test_planned_status_entry_does_not_satisfy_either(self, tool):
        releases = self._changelog(tool, "0.9.0")
        entries = tool.parse_release_entries('## v0.9.0 — "Someday"\n\n**Status:** Planned.\n')
        assert [str(r.parsed) for r in tool.find_missing_releases(releases, entries)] == ["0.9.0"]

    def test_non_version_heading_never_satisfies_a_release(self, tool):
        # `## v0.7.x` must not be mistaken for a record of 0.7.0.
        releases = self._changelog(tool, "0.7.0")
        entries = tool.parse_release_entries('## v0.7.x — "Pipeline Hardening" (Planned)\n')
        assert [str(r.parsed) for r in tool.find_missing_releases(releases, entries)] == ["0.7.0"]

    def test_prerelease_is_required_and_matched_exactly(self, tool):
        releases = self._changelog(tool, "0.3.1rc1")
        assert tool.find_missing_releases(releases, tool.parse_release_entries("## v0.3.1rc1 x\n")) == []
        # The final release is a different version and must not stand in for it.
        missing = tool.find_missing_releases(releases, tool.parse_release_entries("## v0.3.1 x\n"))
        assert [str(r.parsed) for r in missing] == ["0.3.1rc1"]

    def test_malformed_changelog_heading_is_not_reported_as_missing(self, tool):
        # It is reported separately as malformed; double-reporting it as a
        # missing release would send the reader to the wrong file.
        releases = self._changelog(tool, "banana")
        assert tool.find_missing_releases(releases, []) == []

    def test_pre_record_releases_are_exempt(self, tool):
        # releases.md opens at v0.3.0; v0.1.0/v0.2.0 predate the roadmap tree.
        releases = self._changelog(tool, "0.1.0", "0.2.0")
        assert tool.find_missing_releases(releases, []) == []

    def test_report_is_ordered_oldest_first(self, tool):
        releases = self._changelog(tool, "0.9.0", "0.8.0")
        missing = tool.find_missing_releases(releases, [])
        assert [str(r.parsed) for r in missing] == ["0.8.0", "0.9.0"]


class TestNewestRelease:
    def test_comparison_is_semantic_not_lexical(self, tool):
        # "0.10.0" < "0.9.0" as strings; Version() must order them correctly.
        releases = tool.parse_changelog_releases("## [0.9.0] — a\n## [0.10.0] — b\n")
        assert str(tool.newest_release(releases).parsed) == "0.10.0"

    def test_prerelease_ranks_below_its_final(self, tool):
        releases = tool.parse_changelog_releases("## [1.0.0] — a\n## [1.0.0rc1] — b\n")
        assert str(tool.newest_release(releases).parsed) == "1.0.0"

    def test_malformed_headings_are_ignored_when_ranking(self, tool):
        releases = tool.parse_changelog_releases("## [banana] — a\n## [0.9.0] — b\n")
        assert str(tool.newest_release(releases).parsed) == "0.9.0"

    def test_no_parsable_release_returns_none(self, tool):
        assert tool.newest_release(tool.parse_changelog_releases("## [banana] — a\n")) is None


class TestExtractRoadmapReleasedVersion:
    def test_backticked_token_is_extracted(self, tool):
        text = '**Released:** `v0.9.0` — "Phase 21" — PyPI 2026-07-05.\n'
        assert tool.extract_roadmap_released_version(text) == "v0.9.0"

    def test_unbackticked_fallback(self, tool):
        assert tool.extract_roadmap_released_version("**Released:** v0.9.0 — shipped\n") == "v0.9.0"

    def test_missing_line_returns_none(self, tool):
        assert tool.extract_roadmap_released_version("# Roadmap\n\nNothing here.\n") is None

    def test_later_backticks_do_not_win(self, tool):
        text = "**Released:** `v0.9.0` — see `v0.7.0` for the previous entry.\n"
        assert tool.extract_roadmap_released_version(text) == "v0.9.0"


class TestRealRepo:
    """Pins the invariant CI enforces: ci.yml's validate job runs this guard
    with ``--strict``, so a pytest run alone must also fail if a release record
    goes missing or the roadmap headline goes stale."""

    def test_strict_run_is_clean(self, tool):
        assert tool.main(["--strict", "--quiet"]) == 0

    def test_advisory_run_exits_zero(self, tool):
        # Advisory mode reports but never fails — the sibling guards' contract.
        assert tool.main(["--quiet"]) == 0

    def test_real_changelog_has_releases_to_check(self, tool):
        # A guard that silently matches nothing is dead enforcement — pin a
        # floor so a parsing regression cannot make it vacuously green.
        releases = tool.parse_changelog_releases(tool.CHANGELOG_PATH.read_text(encoding="utf-8"))
        assert len(releases) >= 10

    def test_real_releases_file_has_a_planned_section(self, tool):
        # The planned-does-not-count rule is only meaningful while planned
        # sections actually exist in the file it polices.
        entries = tool.parse_release_entries(tool.RELEASES_PATH.read_text(encoding="utf-8"))
        assert any(entry.planned for entry in entries)

    def test_guard_wired_into_ci(self):
        # Assert the exact invocation, not just the filename: a step that runs
        # the guard without --strict reports drift and still exits 0, which is
        # the fake-green failure mode the guard exists to prevent.
        ci = (_REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        assert "python3 tools/check_release_record_sync.py --strict" in ci

    @pytest.mark.parametrize("doc", ["CLAUDE.md", "AGENTS.md", "CONTRIBUTING.md"])
    def test_guard_listed_in_the_gauntlet(self, doc):
        text = (_REPO_ROOT / doc).read_text(encoding="utf-8")
        assert "python3 tools/check_release_record_sync.py --strict" in text

    def test_cut_release_skill_points_at_the_guard(self):
        skill = (_REPO_ROOT / ".claude" / "skills" / "cut-release" / "SKILL.md").read_text(encoding="utf-8")
        assert "check_release_record_sync" in skill
