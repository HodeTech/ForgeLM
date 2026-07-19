"""Tests for tools/check_release_record_sync.py.

The guard cross-checks four files that a release is supposed to touch
together: every ``## [X.Y.Z] — DATE`` heading in ``CHANGELOG.md`` must have a
non-planned, non-empty section in ``docs/roadmap/releases.md``, and both
``docs/roadmap.md``'s ``**Released:**`` headline and ``docs/roadmap-tr.md``'s
``**Yayınlandı:**`` headline must name the newest released version. The
post-release step that produces those edits was skipped for v0.8.0 and v0.9.0,
leaving the public roadmap two minor versions behind PyPI — and the Turkish
mirror, the copy nobody re-reads, four behind at v0.5.0.

Three layers:

* **Unit** — detection logic pinned against synthetic in-memory fixtures so it
  stays independent of the real, evolving corpus.
* **Enforcement** (:class:`TestEnforcement`) — ``main()`` driven against a
  temporary tree via monkeypatched module paths, so every *failure* branch is
  executed and its exit code asserted in both strict and advisory mode. Without
  this layer the guard's whole ``failed = True`` / ``return 1`` surface is
  unreached: a live-repo-only suite is green by construction, and mutating
  ``return 1 if args.strict else 0`` to ``return 0`` left it fully green.
* **Live repo** (:class:`TestRealRepo`) — the invariant CI actually relies on,
  ``main(["--strict", "--quiet"]) == 0``, mirroring
  ``tests/test_check_deprecation_targets.py``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from packaging.version import Version

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


class TestScanFences:
    def test_lines_inside_and_including_a_fence_are_masked(self, tool):
        scan = tool.scan_fences(["before", "```bash", "inside", "```", "after"])
        assert scan.mask == (False, True, True, True, False)
        assert scan.unterminated_line is None

    def test_tilde_fences_count_too(self, tool):
        # CommonMark §4.5 allows `~~~` as well as backticks.
        scan = tool.scan_fences(["~~~", "inside", "~~~", "after"])
        assert scan.mask == (True, True, True, False)

    def test_indented_fence_still_toggles(self, tool):
        scan = tool.scan_fences(["  ```", "inside", "  ```"])
        assert scan.mask == (True, True, True)

    def test_unterminated_fence_is_reported_with_its_line(self, tool):
        # An open fence masks the whole remainder of the file, which would
        # silently disable enforcement — the caller has to be able to fail on it.
        scan = tool.scan_fences(["intro", "```", "swallowed"])
        assert scan.unterminated_line == 2
        assert scan.mask == (False, True, True)


class TestExtractDate:
    def test_first_date_on_the_line_wins(self, tool):
        # The real v0.3.1rc1 heading trails a parenthetical after the date.
        assert tool.extract_date("## [0.3.1rc1] — 2026-03-28 (included in v0.4.0 branch)") == "2026-03-28"

    def test_parenthesised_date_is_found(self, tool):
        assert tool.extract_date('## v0.7.0 — "Pipeline Chains" (2026-05-15)') == "2026-05-15"

    def test_dateless_heading_returns_none(self, tool):
        assert tool.extract_date('## v0.5.0 — "Document Ingestion"') is None


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

    def test_fenced_heading_does_not_mint_a_phantom_release(self, tool):
        # A ``` sample documenting the heading format is not a release; without
        # fence-skipping it would demand a releases.md section for v9.9.9.
        text = "# Changelog\n\n```text\n## [9.9.9] — 2099-01-01\n```\n\n## [0.9.0] — 2026-07-05\n"
        assert [r.version for r in tool.parse_changelog_releases(text)] == ["0.9.0"]


class TestParseReleaseEntries:
    def test_titled_heading_is_parsed(self, tool):
        text = '## v0.7.0 — "Pipeline Chains" (2026-05-15)\n\n**Status:** Released.\n'
        (entry,) = tool.parse_release_entries(text)
        assert str(entry.parsed) == "0.7.0"
        assert entry.planned is False
        assert entry.has_body is True

    def test_bare_heading_without_a_date_is_parsed(self, tool):
        # `## v0.3.0 Release` and `## v0.5.0 — "..."` (no date) both occur.
        entries = tool.parse_release_entries('## v0.3.0 Release\n\nnotes\n\n## v0.5.0 — "Ingestion"\n\nnotes\n')
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

    def test_body_less_heading_is_flagged(self, tool):
        text = '## v0.9.0 — "Shipped" (2026-07-05)\n\n\n## v1.0.0 — "Next"\n\nnotes\n'
        empty, filled = tool.parse_release_entries(text)
        assert empty.has_body is False
        assert filled.has_body is True

    def test_fenced_body_still_counts_as_a_body(self, tool):
        # A section whose whole body is a code sample is still a written-up release.
        text = '## v0.9.0 — "Shipped" (2026-07-05)\n\n```yaml\nkey: value\n```\n'
        (entry,) = tool.parse_release_entries(text)
        assert entry.has_body is True

    def test_fenced_heading_is_not_an_entry(self, tool):
        text = '## v0.9.0 — "Shipped" (2026-07-05)\n\n```text\n## v9.9.9 — "Sample"\n```\n'
        entries = tool.parse_release_entries(text)
        assert [str(e.parsed) for e in entries] == ["0.9.0"]

    def test_fenced_status_line_does_not_flip_the_planned_flag(self, tool):
        text = '## v0.9.0 — "Shipped" (2026-07-05)\n\n```text\n**Status:** Planned.\n```\n'
        (entry,) = tool.parse_release_entries(text)
        assert entry.planned is False


class TestIsRecord:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ('## v0.9.0 — "Shipped" (2026-07-05)\n\n**Status:** Released.\n', True),
            ('## v0.9.0 — "Someday" (Planned)\n\n**Status:** Planned.\n', False),
            ('## v0.9.0 — "Shipped" (2026-07-05)\n\n', False),  # body-less
            ('## v0.7.x — "Hardening"\n\nnotes\n', False),  # not a version
        ],
    )
    def test_all_three_requirements(self, tool, text, expected):
        (entry,) = tool.parse_release_entries(text)
        assert tool.is_record(entry) is expected


class TestFindUnreadableEntries:
    def test_planned_non_versions_are_not_reported(self, tool):
        # `v0.7.x` / `v0.6.0-pro` are real, expected, planned headings.
        entries = tool.parse_release_entries('## v0.7.x — "Hardening" (Planned)\n\nnotes\n')
        assert tool.find_unreadable_entries(entries) == []

    def test_unparsable_non_planned_heading_is_named(self, tool):
        entries = tool.parse_release_entries("## Appendix — notes\n\nbody\n")
        (unreadable,) = tool.find_unreadable_entries(entries)
        assert unreadable.line == 1
        assert unreadable.heading == "## Appendix — notes"


class TestFindMissingReleases:
    def _changelog(self, tool, *versions):
        return tool.parse_changelog_releases("".join(f"## [{v}] — 2026-07-05\n" for v in versions))

    def _entries(self, tool, text):
        return tool.parse_release_entries(text)

    def test_recorded_version_is_not_missing(self, tool):
        releases = self._changelog(tool, "0.9.0")
        entries = self._entries(tool, '## v0.9.0 — "Shipped" (2026-07-05)\n\n**Status:** Released.\n')
        assert tool.find_missing_releases(releases, entries) == []

    def test_unrecorded_version_is_missing(self, tool):
        releases = self._changelog(tool, "0.9.0")
        entries = self._entries(tool, '## v0.7.0 — "Pipeline Chains" (2026-05-15)\n\nnotes\n')
        assert [str(r.parsed) for r in tool.find_missing_releases(releases, entries)] == ["0.9.0"]

    def test_planned_entry_does_not_satisfy_a_released_version(self, tool):
        # THE regression case. A `(Planned)` section is a promise, not a record;
        # had one been allowed to count, the exact state this guard was written
        # for would have passed.
        releases = self._changelog(tool, "0.9.0")
        entries = self._entries(tool, '## v0.9.0 — "Someday" (Planned)\n\nnotes\n')
        assert [str(r.parsed) for r in tool.find_missing_releases(releases, entries)] == ["0.9.0"]

    def test_planned_status_entry_does_not_satisfy_either(self, tool):
        releases = self._changelog(tool, "0.9.0")
        entries = self._entries(tool, '## v0.9.0 — "Someday"\n\n**Status:** Planned.\n')
        assert [str(r.parsed) for r in tool.find_missing_releases(releases, entries)] == ["0.9.0"]

    def test_body_less_entry_does_not_satisfy_a_released_version(self, tool):
        # A bare heading is the cheapest way to make this guard green without
        # writing the release note it exists to demand.
        releases = self._changelog(tool, "0.9.0")
        entries = self._entries(tool, '## v0.9.0 — "Shipped" (2026-07-05)\n\n')
        assert [str(r.parsed) for r in tool.find_missing_releases(releases, entries)] == ["0.9.0"]

    def test_fenced_entry_does_not_satisfy_a_released_version(self, tool):
        releases = self._changelog(tool, "0.9.0")
        entries = self._entries(tool, '## v0.7.0 — "Old"\n\nnotes\n\n```text\n## v0.9.0 — "Sample"\n\nnotes\n```\n')
        assert [str(r.parsed) for r in tool.find_missing_releases(releases, entries)] == ["0.9.0"]

    def test_non_version_heading_never_satisfies_a_release(self, tool):
        # `## v0.7.x` must not be mistaken for a record of 0.7.0.
        releases = self._changelog(tool, "0.7.0")
        entries = self._entries(tool, '## v0.7.x — "Pipeline Hardening" (Planned)\n\nnotes\n')
        assert [str(r.parsed) for r in tool.find_missing_releases(releases, entries)] == ["0.7.0"]

    def test_prerelease_is_required_and_matched_exactly(self, tool):
        releases = self._changelog(tool, "0.3.1rc1")
        assert tool.find_missing_releases(releases, self._entries(tool, "## v0.3.1rc1 x\n\nnotes\n")) == []
        # The final release is a different version and must not stand in for it.
        missing = tool.find_missing_releases(releases, self._entries(tool, "## v0.3.1 x\n\nnotes\n"))
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

    def test_pre_record_exemption_is_version_normalised(self, tool):
        # Held as Version objects, so `0.1` and `0.1.0` are the same release —
        # a string set would have reported `[0.1]` as unrecorded.
        assert tool.find_missing_releases(self._changelog(tool, "0.1"), []) == []
        assert tool._PRE_RECORD_RELEASES == frozenset({Version("0.1.0"), Version("0.2.0")})

    def test_report_is_ordered_oldest_first(self, tool):
        releases = self._changelog(tool, "0.9.0", "0.8.0")
        missing = tool.find_missing_releases(releases, [])
        assert [str(r.parsed) for r in missing] == ["0.8.0", "0.9.0"]


class TestFindDateMismatches:
    def _pair(self, tool, changelog_date, record_date, version="0.9.0"):
        releases = tool.parse_changelog_releases(f"## [{version}] — {changelog_date}\n")
        entries = tool.parse_release_entries(f'## v{version} — "T" ({record_date})\n\nnotes\n')
        return releases, entries

    def test_agreeing_dates_are_silent(self, tool):
        assert tool.find_date_mismatches(*self._pair(tool, "2026-07-05", "2026-07-05")) == []

    def test_disagreeing_dates_are_reported(self, tool):
        (mismatch,) = tool.find_date_mismatches(*self._pair(tool, "2026-07-05", "2026-07-06"))
        assert (mismatch.changelog_date, mismatch.record_date) == ("2026-07-05", "2026-07-06")
        assert mismatch.exempt is False

    def test_legacy_pairs_are_exempt_not_erased(self, tool):
        # The three historical mismatches are frozen as history, not "fixed":
        # the changelog is an append-only record.
        (mismatch,) = tool.find_date_mismatches(*self._pair(tool, "2026-05-14", "2026-05-15", version="0.7.0"))
        assert mismatch.exempt is True

    @pytest.mark.parametrize("version", ["0.7.0", "0.5.7", "0.3.1rc1"])
    def test_the_exemption_set_is_exactly_the_known_three(self, tool, version):
        assert Version(version) in tool._LEGACY_DATE_MISMATCHES
        assert len(tool._LEGACY_DATE_MISMATCHES) == 3

    def test_a_missing_date_on_either_side_is_skipped(self, tool):
        # `## v0.5.0 — "Document Ingestion..."` genuinely carries no date.
        releases = tool.parse_changelog_releases("## [0.5.0] — 2026-04-30\n")
        entries = tool.parse_release_entries('## v0.5.0 — "Ingestion"\n\nnotes\n')
        assert tool.find_date_mismatches(releases, entries) == []

    def test_planned_entry_is_not_date_checked(self, tool):
        releases = tool.parse_changelog_releases("## [0.9.0] — 2026-07-05\n")
        entries = tool.parse_release_entries('## v0.9.0 — "Someday" (Planned) 2026-07-06\n\nnotes\n')
        assert tool.find_date_mismatches(releases, entries) == []


class TestFindStaleDateExemptions:
    def test_reconciled_exemption_is_reported(self, tool):
        releases = tool.parse_changelog_releases("## [0.7.0] — 2026-05-15\n")
        entries = tool.parse_release_entries('## v0.7.0 — "Chains" (2026-05-15)\n\nnotes\n')
        assert tool.find_stale_date_exemptions(releases, entries) == [Version("0.7.0")]

    def test_still_mismatched_exemption_is_not_stale(self, tool):
        releases = tool.parse_changelog_releases("## [0.7.0] — 2026-05-14\n")
        entries = tool.parse_release_entries('## v0.7.0 — "Chains" (2026-05-15)\n\nnotes\n')
        assert tool.find_stale_date_exemptions(releases, entries) == []


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


class TestFindHeadlineVersions:
    def test_backticked_token_is_extracted(self, tool):
        text = '**Released:** `v0.9.0` — "Phase 21" — PyPI 2026-07-05.\n'
        assert tool.find_headline_versions(text, tool.ROADMAP_RELEASED_MARKER_EN) == ["v0.9.0"]

    def test_turkish_marker_is_read_from_the_mirror(self, tool):
        text = '**Yayınlandı:** `v0.9.0` — "Faz 21" — PyPI 2026-07-05.\n'
        assert tool.find_headline_versions(text, tool.ROADMAP_RELEASED_MARKER_TR) == ["v0.9.0"]

    def test_markers_do_not_cross_match(self, tool):
        text = "**Yayınlandı:** `v0.9.0`\n"
        assert tool.find_headline_versions(text, tool.ROADMAP_RELEASED_MARKER_EN) == []

    def test_unbackticked_fallback(self, tool):
        text = "**Released:** v0.9.0 — shipped\n"
        assert tool.find_headline_versions(text, tool.ROADMAP_RELEASED_MARKER_EN) == ["v0.9.0"]

    def test_missing_line_returns_empty(self, tool):
        assert tool.find_headline_versions("# Roadmap\n\nNothing here.\n", tool.ROADMAP_RELEASED_MARKER_EN) == []

    def test_later_backticks_do_not_win(self, tool):
        text = "**Released:** `v0.9.0` — see `v0.7.0` for the previous entry.\n"
        assert tool.find_headline_versions(text, tool.ROADMAP_RELEASED_MARKER_EN) == ["v0.9.0"]

    def test_every_headline_line_is_returned(self, tool):
        # Two candidate lines: the caller must be able to see both rather than
        # silently enforcing whichever came first.
        text = "**Released:** `v0.9.0`\n\nprose\n\n**Released:** `v0.7.0`\n"
        assert tool.find_headline_versions(text, tool.ROADMAP_RELEASED_MARKER_EN) == ["v0.9.0", "v0.7.0"]

    def test_tokenless_headline_is_a_none_element(self, tool):
        assert tool.find_headline_versions("**Released:**\n", tool.ROADMAP_RELEASED_MARKER_EN) == [None]

    def test_fenced_headline_is_ignored(self, tool):
        text = "```text\n**Released:** `v9.9.9`\n```\n\n**Released:** `v0.9.0`\n"
        assert tool.find_headline_versions(text, tool.ROADMAP_RELEASED_MARKER_EN) == ["v0.9.0"]


class TestHeadlineSources:
    def test_both_mirrors_are_covered(self, tool):
        sources = tool.headline_sources()
        assert [(source.path.name, source.marker) for source in sources] == [
            ("roadmap.md", "**Released:**"),
            ("roadmap-tr.md", "**Yayınlandı:**"),
        ]

    def test_table_is_rebuilt_per_call(self, tool, tmp_path, monkeypatch):
        # A table snapshotted at import time would keep pointing at the real repo
        # and silently ignore the module-level path constants.
        monkeypatch.setattr(tool, "ROADMAP_TR_PATH", tmp_path / "elsewhere-tr.md")
        assert tool.headline_sources()[1].path == tmp_path / "elsewhere-tr.md"


# --------------------------------------------------------------------------
# Enforcement: main() against a temporary tree.
# --------------------------------------------------------------------------

_GREEN_CHANGELOG = "# Changelog\n\n## [Unreleased]\n\n## [0.9.0] — 2026-07-05\n\n### Added\n- a thing\n"
_GREEN_RELEASES = '# Releases\n\n## v0.9.0 — "Shipped" (2026-07-05)\n\n**Status:** Released.\n'
_GREEN_ROADMAP = "# Roadmap\n\n**Released:** `v0.9.0` — shipped 2026-07-05.\n"
_GREEN_ROADMAP_TR = "# Yol Haritası\n\n**Yayınlandı:** `v0.9.0` — 2026-07-05'te yayınlandı.\n"


class _FakeTree:
    """The guard's four inputs, redirected onto ``tmp_path``.

    ``main()`` reads its paths from module-level constants, so monkeypatching
    them is the whole mechanism — it lets a test author a *failing* corpus and
    execute the enforcement branches that the live repo, green by construction,
    never reaches.
    """

    def __init__(self, tool, tmp_path, monkeypatch):
        self.changelog = tmp_path / "CHANGELOG.md"
        self.releases = tmp_path / "releases.md"
        self.roadmap = tmp_path / "roadmap.md"
        self.roadmap_tr = tmp_path / "roadmap-tr.md"
        monkeypatch.setattr(tool, "CHANGELOG_PATH", self.changelog)
        monkeypatch.setattr(tool, "RELEASES_PATH", self.releases)
        monkeypatch.setattr(tool, "ROADMAP_PATH", self.roadmap)
        monkeypatch.setattr(tool, "ROADMAP_TR_PATH", self.roadmap_tr)
        self.write()

    def write(self, *, changelog=None, releases=None, roadmap=None, roadmap_tr=None):
        """Write the corpus; every omitted file keeps its green default."""
        self.changelog.write_text(_GREEN_CHANGELOG if changelog is None else changelog, encoding="utf-8")
        self.releases.write_text(_GREEN_RELEASES if releases is None else releases, encoding="utf-8")
        self.roadmap.write_text(_GREEN_ROADMAP if roadmap is None else roadmap, encoding="utf-8")
        self.roadmap_tr.write_text(_GREEN_ROADMAP_TR if roadmap_tr is None else roadmap_tr, encoding="utf-8")


@pytest.fixture
def tree(tool, tmp_path, monkeypatch):
    return _FakeTree(tool, tmp_path, monkeypatch)


class TestEnforcement:
    """Every failure branch: exit 1 under ``--strict``, exit 0 advisory.

    This is the layer that was missing. With only live-repo assertions, mutating
    ``return 1 if args.strict else 0`` to ``return 0`` — or dropping the
    ``failed = True`` on the unrecorded-release branch — left the suite green.
    """

    def _fails(self, tool, capsys, fragment):
        """Assert strict exits 1, advisory exits 0, and the report says why."""
        strict = tool.main(["--strict"])
        out = capsys.readouterr().out
        advisory = tool.main([])
        capsys.readouterr()
        assert (strict, advisory) == (1, 0), out
        assert fragment in out, out
        return out

    def test_green_tree_passes(self, tool, tree, capsys):
        assert tool.main(["--strict"]) == 0
        assert "OK:" in capsys.readouterr().out

    def test_unrecorded_release_fails(self, tool, tree, capsys):
        tree.write(releases='# Releases\n\n## v0.7.0 — "Old" (2026-05-15)\n\n**Status:** Released.\n')
        out = self._fails(tool, capsys, "have no entry in")
        assert "v0.9.0" in out

    def test_planned_section_does_not_rescue_an_unrecorded_release(self, tool, tree, capsys):
        tree.write(releases='# Releases\n\n## v0.9.0 — "Someday" (Planned)\n\n**Status:** Planned.\n')
        self._fails(tool, capsys, "have no entry in")

    def test_body_less_section_does_not_rescue_an_unrecorded_release(self, tool, tree, capsys):
        tree.write(releases='# Releases\n\n## v0.9.0 — "Shipped" (2026-07-05)\n')
        self._fails(tool, capsys, "have no entry in")

    def test_fenced_section_does_not_rescue_an_unrecorded_release(self, tool, tree, capsys):
        tree.write(releases='# Releases\n\n```text\n## v0.9.0 — "Shipped" (2026-07-05)\n\nnotes\n```\n')
        self._fails(tool, capsys, "have no entry in")

    def test_stale_english_headline_fails(self, tool, tree, capsys):
        tree.write(roadmap="# Roadmap\n\n**Released:** `v0.7.0` — stale.\n")
        out = self._fails(tool, capsys, "names v0.7.0")
        assert "roadmap.md" in out

    def test_stale_turkish_headline_fails(self, tool, tree, capsys):
        # The TR mirror had drifted furthest of all (v0.5.0) before this guard;
        # an unguarded mirror is an unmaintained mirror.
        out_fragment = "**Yayınlandı:**"
        tree.write(roadmap_tr="# Yol Haritası\n\n**Yayınlandı:** `v0.5.0` — eski.\n")
        out = self._fails(tool, capsys, out_fragment)
        assert "roadmap-tr.md" in out

    def test_each_headline_is_reported_separately(self, tool, tree, capsys):
        # Neither mirror may be masked by the other's failure.
        tree.write(
            roadmap="# Roadmap\n\n**Released:** `v0.7.0` — stale.\n",
            roadmap_tr="# Yol Haritası\n\n**Yayınlandı:** `v0.5.0` — eski.\n",
        )
        out = self._fails(tool, capsys, "roadmap.md")
        assert "roadmap-tr.md" in out
        assert out.count("FAIL:") == 2

    @pytest.mark.parametrize("target", ["roadmap", "roadmap_tr"])
    def test_missing_headline_line_fails(self, tool, tree, capsys, target):
        tree.write(**{target: "# Roadmap\n\nNo headline at all.\n"})
        self._fails(tool, capsys, "cannot be checked against the newest release")

    def test_duplicate_headline_fails(self, tool, tree, capsys):
        # With two candidates the guard would enforce whichever came first and
        # silently ignore the other.
        tree.write(roadmap="# Roadmap\n\n**Released:** `v0.9.0`\n\nprose\n\n**Released:** `v0.7.0`\n")
        self._fails(tool, capsys, "the headline must be unique")

    def test_malformed_changelog_heading_fails(self, tool, tree, capsys):
        tree.write(changelog="# Changelog\n\n## [banana] — 2026-07-05\n\n## [0.9.0] — 2026-07-05\n")
        out = self._fails(tool, capsys, "not readable as a version")
        assert "banana" in out

    def test_changelog_with_no_release_at_all_fails(self, tool, tree, capsys):
        tree.write(changelog="# Changelog\n\n## [Unreleased]\n\n### Added\n- a thing\n")
        self._fails(tool, capsys, "declares no released version at all")

    def test_date_mismatch_fails(self, tool, tree, capsys):
        tree.write(releases='# Releases\n\n## v0.9.0 — "Shipped" (2026-07-06)\n\n**Status:** Released.\n')
        out = self._fails(tool, capsys, "dated differently")
        assert "2026-07-05" in out and "2026-07-06" in out

    def test_legacy_date_mismatch_is_exempt_and_noted(self, tool, tree, capsys):
        tree.write(
            changelog="# Changelog\n\n## [0.7.0] — 2026-05-14\n\n### Added\n- a thing\n",
            releases='# Releases\n\n## v0.7.0 — "Chains" (2026-05-15)\n\n**Status:** Released.\n',
            roadmap="# Roadmap\n\n**Released:** `v0.7.0` — shipped.\n",
            roadmap_tr="# Yol Haritası\n\n**Yayınlandı:** `v0.7.0` — yayınlandı.\n",
        )
        assert tool.main(["--strict"]) == 0
        out = capsys.readouterr().out
        assert "known historical pair" in out

    def test_stale_exemption_is_noted_while_still_passing(self, tool, tree, capsys):
        tree.write(
            changelog="# Changelog\n\n## [0.7.0] — 2026-05-15\n\n### Added\n- a thing\n",
            releases='# Releases\n\n## v0.7.0 — "Chains" (2026-05-15)\n\n**Status:** Released.\n',
            roadmap="# Roadmap\n\n**Released:** `v0.7.0` — shipped.\n",
            roadmap_tr="# Yol Haritası\n\n**Yayınlandı:** `v0.7.0` — yayınlandı.\n",
        )
        assert tool.main(["--strict"]) == 0
        assert "drop it from the exemption set" in capsys.readouterr().out

    def test_unterminated_fence_fails(self, tool, tree, capsys):
        # An open fence masks the remainder of the file, which would disable the
        # guard silently — worse than any drift it could hide.
        tree.write(changelog=_GREEN_CHANGELOG + "\n```text\nnever closed\n")
        self._fails(tool, capsys, "opens a code fence that is never closed")

    def test_unreadable_release_heading_is_advised_not_fatal(self, tool, tree, capsys):
        tree.write(releases=_GREEN_RELEASES + "\n## Appendix — extra notes\n\nbody\n")
        assert tool.main(["--strict"]) == 0
        out = capsys.readouterr().out
        assert "NOTE:" in out and "Appendix" in out

    @pytest.mark.parametrize("attribute", ["CHANGELOG_PATH", "RELEASES_PATH", "ROADMAP_PATH", "ROADMAP_TR_PATH"])
    def test_missing_input_file_always_fails(self, tool, tree, capsys, monkeypatch, tmp_path, attribute):
        # Deliberately NOT advisory-gated: an unreadable input is a broken
        # invocation, not drift to iterate on locally, and a guard that cannot
        # read its inputs must never report success.  Matches
        # tools/check_usermanual_schema_drift.py.
        monkeypatch.setattr(tool, attribute, tmp_path / "gone.md")
        assert tool.main(["--strict"]) == 1
        assert tool.main([]) == 1
        assert "not found" in capsys.readouterr().err

    def test_quiet_suppresses_the_success_summary_only(self, tool, tree, capsys):
        assert tool.main(["--strict", "--quiet"]) == 0
        assert capsys.readouterr().out == ""

    def test_remediation_names_the_cut_release_skill_agent_neutrally(self, tool, tree, capsys):
        # The operator-facing message is the whole point of the guard failing.
        # `.claude/` does not exist for an agent working from the `.agents/`
        # mirror, so the pointer must name both.
        tree.write(releases='# Releases\n\n## v0.7.0 — "Old" (2026-05-15)\n\n**Status:** Released.\n')
        out = self._fails(tool, capsys, "cut-release")
        assert "skills/cut-release/SKILL.md" in out
        assert ".claude/" in out and ".agents/" in out

    def test_report_paths_survive_an_out_of_tree_corpus(self, tool, tree, capsys):
        # `Path.relative_to` raises for a path outside the repo; a guard that
        # crashes formatting its own failure is worse than one printing an
        # absolute path.
        tree.write(releases="# Releases\n\nnothing here.\n")
        out = self._fails(tool, capsys, "have no entry in")
        assert str(tree.releases) in out


class TestRealRepo:
    """Pins the invariant CI enforces: ci.yml's validate job runs this guard
    with ``--strict``, so a pytest run alone must also fail if a release record
    goes missing or a roadmap headline goes stale."""

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

    def test_real_records_all_have_bodies(self, tool):
        entries = tool.parse_release_entries(tool.RELEASES_PATH.read_text(encoding="utf-8"))
        assert all(entry.has_body for entry in entries if entry.parsed is not None and not entry.planned)

    @pytest.mark.parametrize("attribute", ["ROADMAP_PATH", "ROADMAP_TR_PATH"])
    def test_both_roadmap_mirrors_carry_exactly_one_headline(self, tool, attribute):
        source = next(s for s in tool.headline_sources() if s.path == getattr(tool, attribute))
        headlines = tool.find_headline_versions(source.path.read_text(encoding="utf-8"), source.marker)
        assert len(headlines) == 1

    def test_the_three_legacy_date_mismatches_are_still_real(self, tool):
        # If history is ever reconciled, the exemption must shrink rather than
        # linger as a silent blind spot — the guard says so itself, and this
        # test keeps the claim in the docstring honest.
        releases = tool.parse_changelog_releases(tool.CHANGELOG_PATH.read_text(encoding="utf-8"))
        entries = tool.parse_release_entries(tool.RELEASES_PATH.read_text(encoding="utf-8"))
        exempt = {m.release.parsed for m in tool.find_date_mismatches(releases, entries) if m.exempt}
        assert exempt == tool._LEGACY_DATE_MISMATCHES
        assert tool.find_stale_date_exemptions(releases, entries) == []

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

    @pytest.mark.parametrize("mirror", [".claude", ".agents"])
    def test_cut_release_skill_points_at_the_guard(self, mirror):
        # Both skill mirrors, because the guard's own remediation text names both.
        skill = (_REPO_ROOT / mirror / "skills" / "cut-release" / "SKILL.md").read_text(encoding="utf-8")
        assert "check_release_record_sync" in skill
