#!/usr/bin/env python3
"""Release-record sync guard (CHANGELOG -> roadmap release notes).

The drift class this prevents
-----------------------------
Cutting a release is a multi-file operation.  ``CHANGELOG.md`` gets the
``## [X.Y.Z] — DATE`` section, ``pyproject.toml`` gets the version bump,
the tag goes out — and then the *post-release* half of the checklist
("Close the phase", step 4 of the ``cut-release`` skill) asks for more
edits that nothing verified:

* a ``## vX.Y.Z — "Title" (DATE)`` section in
  ``docs/roadmap/releases.md``, and
* a refreshed ``**Released:**`` headline in ``docs/roadmap.md`` **and its
  Turkish mirror's ``**Yayınlandı:**`` headline** in
  ``docs/roadmap-tr.md``.

Those edits happen after the satisfying part of the release is done,
which is exactly when a checklist stops being read.  **They were skipped
for two consecutive releases — v0.8.0 (2026-06-16) and v0.9.0
(2026-07-05).**  ``CHANGELOG.md`` recorded both as shipped while
``releases.md``'s newest entry was still ``v0.7.0`` (followed by a
``v0.7.x (Planned)`` section, which reads to a skimmer as if the record
were current), ``docs/roadmap.md`` still announced ``**Released:**
v0.7.0`` — and ``docs/roadmap-tr.md``, the copy nobody re-reads, had
drifted furthest of all at ``v0.5.0``.  A reader arriving at the roadmap
— the page the project points newcomers at — was told the product was
two (in Turkish, four) minor versions behind where PyPI actually had it.

Nothing detected it because the drift is *between* files that are each
internally consistent.  Only a cross-file check can see it, so:

1. **Every released version has a record.**  Parse each
   ``## [X.Y.Z] — DATE`` heading in ``CHANGELOG.md`` (``[Unreleased]`` is
   skipped by definition) and require a matching level-2 section in
   ``docs/roadmap/releases.md``.  Versions are compared *parsed*
   (:class:`packaging.version.Version`), not as strings, so ``v0.9.0``
   in the release notes satisfies ``[0.9.0]`` in the changelog and
   ``v0.10.0`` sorts above ``v0.9.0`` rather than below it.
2. **Every roadmap headline names the newest release.**  Each
   ``(file, marker)`` pair in :func:`headline_sources` — currently
   ``docs/roadmap.md`` / ``**Released:**`` and ``docs/roadmap-tr.md`` /
   ``**Yayınlandı:**`` — must name the highest released version in
   ``CHANGELOG.md``, and each is reported separately so fixing one does
   not mask the other.  Rule 1 alone would go green the moment someone
   appended a ``releases.md`` section while leaving a headline stale,
   which is half of the failure that actually occurred.  A headline
   marker appearing more than once in a file is itself a failure: with
   two candidate lines the guard would silently enforce whichever came
   first, so the headline is required to be unique.

What counts as a record
-----------------------
``releases.md`` legitimately carries forward-looking sections
(``## v0.7.x — "Pipeline Hardening" (Planned)``,
``## v0.6.0-pro — "Pro CLI" (Planned, gated)``).  A planned section is a
promise, not a record, so it can never satisfy a released version.  An
entry is treated as planned when ``(Planned`` appears in its heading or
when the section's ``**Status:**`` line begins with "Planned".  This
matters beyond pedantry: had ``v0.7.x`` been allowed to satisfy a
``0.7.x``-shaped lookup, the very state this guard was written for would
have passed.

A record also has to have a *body*: at least one non-blank line before
the next level-2 heading.  A bare heading is the cheapest possible way to
make this guard green without writing the release note it exists to
demand, and every real entry in the file carries at least a
``**Status:**`` line, so the requirement costs nothing and closes the
loophole.

Version tokens that are not PEP 440 versions (``v0.7.x``,
``v0.6.0-pro``) simply never match a released version — no special-casing
needed, they fail to parse.  Because an unreadable heading otherwise
surfaces only indirectly (as a confusing "missing release" for a version
that *is* written down, just unreadably), non-planned unparsable
headings are listed in their own advisory block naming file:line.  They
are advisory, not fatal: ``releases.md`` may legitimately grow a
non-version level-2 section one day, and the guard should say so rather
than block on it.

Dates must agree (with an explicit legacy exemption set)
--------------------------------------------------------
Both files date the same event, so a ``## [0.7.0] — 2026-05-14`` heading
and a ``## v0.7.0 — "Pipeline Chains" (2026-05-15)`` section disagree
about when v0.7.0 shipped, and a reader has no way to tell which is
right.  The guard extracts a bounded ``YYYY-MM-DD`` from each heading and
requires them to match.

**Decision: enforced (strict-failing), with a frozen legacy-exemption
set — not advisory-only.**  Three historical pairs already disagree
(:data:`_LEGACY_DATE_MISMATCHES`).  The two alternatives were both
rejected: editing history to make the guard green would rewrite a
released record to suit a tool, and an advisory-only check would print a
warning into an exit-0 build, which is the "nothing noticed" failure mode
this guard exists to close.  Exempting the three known pairs by explicit
version — never by a date-range floor, which would silently widen — keeps
history intact while making every *future* mismatch fatal.  A heading
with no date on either side is skipped (``## v0.5.0 — "..."`` and
``## v0.3.0 Release`` really have none); an exemption whose dates have
since been reconciled is reported as stale so the set shrinks instead of
rotting.

Fenced code blocks are skipped
------------------------------
Both parsers walk lines, and a fenced sample containing ``## [0.9.0] —
...`` or ``## v0.9.0 — "..."`` would otherwise mint a phantom release or
satisfy a real one from inside a code block.  :func:`scan_fences` builds
an inside-a-fence mask (a line walker, not a regex — ``docs/standards/
regex.md`` Rule 6) and every scan honours it.  An *unterminated* fence
masks the whole remainder of a file, which would silently disable
enforcement, so it is a failure in its own right.

Exit codes (per the ``tools/`` contract — NOT the public 0/1/2/3/4/5
surface that ``forgelm/`` honours):

- ``0`` — every released version has a non-planned, non-empty record
  whose date agrees with the changelog, and every roadmap headline names
  the newest release exactly once.
- ``1`` — in strict mode: at least one released version is unrecorded, a
  headline is stale/missing/duplicated, a changelog heading is malformed,
  a non-exempt date pair disagrees, or a fence is left open.  **Also
  ``1`` regardless of ``--strict`` when an input file is missing**: that
  is a broken invocation rather than drift to iterate on locally, and a
  guard that cannot read its inputs must never report success.  Matches
  ``tools/check_usermanual_schema_drift.py``.

CI wiring: runs in ``.github/workflows/ci.yml``'s ``validate`` job with
``--strict``, and is listed in the ``CLAUDE.md`` / ``AGENTS.md`` /
``CONTRIBUTING.md`` self-review gauntlet.

Usage::

    python3 tools/check_release_record_sync.py
    python3 tools/check_release_record_sync.py --strict
    python3 tools/check_release_record_sync.py --quiet
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from packaging.version import InvalidVersion, Version

REPO_ROOT = Path(__file__).resolve().parent.parent
CHANGELOG_PATH = REPO_ROOT / "CHANGELOG.md"
RELEASES_PATH = REPO_ROOT / "docs" / "roadmap" / "releases.md"
ROADMAP_PATH = REPO_ROOT / "docs" / "roadmap.md"
ROADMAP_TR_PATH = REPO_ROOT / "docs" / "roadmap-tr.md"

#: Where the post-release step that this guard enforces is written down.
#: Named agent-neutrally: the skill tree is mirrored, and an agent working
#: from ``.agents/`` has no ``.claude/`` directory to open (and vice versa).
SKILL_REFERENCE = (
    "the cut-release skill — skills/cut-release/SKILL.md under .claude/ or .agents/ "
    '("Post-release" -> "Close the phase")'
)

#: Bracket label that marks the not-yet-cut section of a Keep-a-Changelog file.
_UNRELEASED_LABEL = "unreleased"

#: Releases that predate ``docs/roadmap/releases.md`` itself.  The file opens
#: at ``v0.3.0``; ``v0.1.0`` and ``v0.2.0`` shipped before the roadmap tree
#: existed and were never back-filled.  Exempted explicitly rather than by
#: deriving a floor from the oldest section present — a floor silently widens
#: the exemption every time the oldest entry is edited or removed, which is the
#: same "nothing noticed" failure mode this guard exists to close.
#: Held as :class:`Version` objects, not strings: ``Version`` is hashable and
#: equates ``0.1`` with ``0.1.0``, so the membership test cannot be defeated by
#: a differently-normalised spelling of the same release.
_PRE_RECORD_RELEASES = frozenset({Version("0.1.0"), Version("0.2.0")})

#: Released versions whose ``CHANGELOG.md`` and ``docs/roadmap/releases.md``
#: dates already disagreed when the date cross-check landed.  Frozen as history,
#: NOT fixed: the changelog is an append-only record and a released entry is not
#: edited to suit a tool.  Every version outside this set must agree.
#:
#:   v0.7.0     CHANGELOG 2026-05-14  vs  releases.md 2026-05-15
#:   v0.5.7     CHANGELOG 2026-05-11  vs  releases.md 2026-05-10
#:   v0.3.1rc1  CHANGELOG 2026-03-28  vs  releases.md 2026-04-25
#:
#: If a pair is ever reconciled deliberately, the guard reports the exemption as
#: stale so this set shrinks rather than rots.
_LEGACY_DATE_MISMATCHES = frozenset({Version("0.7.0"), Version("0.5.7"), Version("0.3.1rc1")})

# Per docs/standards/regex.md: anchored, every quantifier bounded, and no two
# quantifiers competing for the same characters — a match attempt is O(1) and a
# whole-file scan is O(n).  All four run line-by-line (no re.MULTILINE, Rule 7).
#
# `## [0.9.0] — 2026-07-05`  ->  captures `0.9.0`
_CHANGELOG_HEADING_RE = re.compile(r"^##[ \t]+\[([^\]\n]{1,64})\]")
# `## v0.7.0 — "Pipeline Chains" (2026-05-15)`  ->  captures `v0.7.0`
# The token is taken as the first whitespace-delimited word and validated by
# `parse_version_token`, so heading punctuation never has to be regexed.
_RELEASES_HEADING_RE = re.compile(r"^##[ \t]+(\S{1,64})")
# `**Released:** `v0.7.0` — "Phase 14 ..."`  ->  captures `v0.7.0`
_BACKTICK_TOKEN_RE = re.compile(r"`([^`\n]{1,64})`")
# `## [0.9.0] — 2026-07-05` / `## v0.9.0 — "..." (2026-07-05)`  ->  `2026-07-05`
# Fully bounded, no alternation, no backtracking surface; searched against a
# single heading line, never a whole file.
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

#: Marker for a forward-looking section heading, e.g. `(Planned)` / `(Planned, gated)`.
_PLANNED_HEADING_RE = re.compile(r"\(planned", re.IGNORECASE)
#: `**Status:** Planned. Focus: ...` — a status line that opens with "Planned".
_PLANNED_STATUS_RE = re.compile(r"^\*\*Status:\*\*\s*planned", re.IGNORECASE)

#: Prefix of the roadmap headline that must name the newest released version,
#: per language.  English and Turkish carry the same claim in mirrored files.
ROADMAP_RELEASED_MARKER_EN = "**Released:**"
ROADMAP_RELEASED_MARKER_TR = "**Yayınlandı:**"

#: Opening/closing markers of a fenced code block (CommonMark §4.5 allows both).
_FENCE_MARKERS = ("```", "~~~")


@dataclass(frozen=True)
class FenceScan:
    """Per-line "is this line inside (or itself) a code fence?" mask."""

    mask: Tuple[bool, ...]
    #: 1-based line of a fence opener that is never closed, else ``None``.
    unterminated_line: Optional[int]


@dataclass(frozen=True)
class HeadlineSource:
    """One file whose headline must name the newest released version."""

    path: Path
    marker: str


@dataclass(frozen=True)
class ChangelogRelease:
    """One ``## [X.Y.Z] — DATE`` heading from ``CHANGELOG.md``."""

    version: str
    line: int
    heading: str
    parsed: Optional[Version]

    @property
    def is_malformed(self) -> bool:
        """True when the bracket label is not a PEP 440 version."""
        return self.parsed is None


@dataclass(frozen=True)
class ReleaseEntry:
    """One level-2 section from ``docs/roadmap/releases.md``."""

    version: str
    line: int
    heading: str
    parsed: Optional[Version]
    planned: bool
    has_body: bool


@dataclass(frozen=True)
class DateMismatch:
    """A released version whose two headings disagree about its date."""

    release: ChangelogRelease
    entry: ReleaseEntry
    changelog_date: str
    record_date: str
    exempt: bool


def _rel(path: Path) -> str:
    """Render ``path`` relative to the repo root, or absolutely if it is outside.

    The three inputs are module-level constants precisely so a test can point
    them at a temporary tree; ``Path.relative_to`` raises on such a path, and a
    guard that crashes while formatting its own failure message is worse than
    one that prints an absolute path.
    """
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def headline_sources() -> Tuple[HeadlineSource, ...]:
    """Return every ``(file, marker)`` pair whose headline names the newest release.

    Built on each call rather than frozen into a module constant so the paths
    above stay the single override point — the test suite retargets them at a
    temporary tree to exercise the failure branches, and a table snapshotted at
    import time would silently keep pointing at the real repo.
    """
    return (
        HeadlineSource(ROADMAP_PATH, ROADMAP_RELEASED_MARKER_EN),
        HeadlineSource(ROADMAP_TR_PATH, ROADMAP_RELEASED_MARKER_TR),
    )


def scan_fences(lines: Sequence[str]) -> FenceScan:
    """Return a mask marking every line that is inside — or is — a code fence.

    A line walker rather than a regex, per ``docs/standards/regex.md`` Rule 6
    (a fenced-block regex needs a back-reference plus ``DOTALL``, the textbook
    ReDoS shape); this is O(n) by construction.

    The state machine deliberately does not track *which* fence character
    opened the block, so a ``~~~`` line inside a ```` ``` ```` block toggles
    state early.  That simplification is safe here because the imbalance it
    creates surfaces through ``unterminated_line``, which callers treat as a
    failure rather than parsing on regardless.
    """
    mask: List[bool] = []
    opener: Optional[int] = None
    for index, line in enumerate(lines):
        if line.lstrip().startswith(_FENCE_MARKERS):
            # The fence marker line itself is never content either way.
            mask.append(True)
            opener = None if opener is not None else index + 1
            continue
        mask.append(opener is not None)
    return FenceScan(mask=tuple(mask), unterminated_line=opener)


def parse_version_token(token: str) -> Optional[Version]:
    """Return ``token`` as a :class:`Version`, or ``None`` when it is not one.

    A leading ``v``/``V`` is stripped so ``v0.9.0`` (release-notes style)
    and ``0.9.0`` (changelog style) compare equal.  Deliberately lenient
    about *what* fails: ``v0.7.x`` and ``v0.6.0-pro`` are real headings in
    ``releases.md`` that are not versions, and the caller treats an
    unparsed token as "matches nothing" rather than as an error.
    """
    stripped = token.strip()
    if stripped[:1] in ("v", "V"):
        stripped = stripped[1:]
    try:
        return Version(stripped)
    except InvalidVersion:
        return None


def extract_date(heading: str) -> Optional[str]:
    """Return the first ``YYYY-MM-DD`` in ``heading``, or ``None`` if it has none.

    Both heading dialects put the date after the version, so "first date on the
    line" is unambiguous: ``## [0.3.1rc1] — 2026-03-28 (included in v0.4.0
    branch)`` yields the release date, not the parenthetical.
    """
    match = _DATE_RE.search(heading)
    return match.group(0) if match is not None else None


def parse_changelog_releases(text: str) -> List[ChangelogRelease]:
    """Return every non-``[Unreleased]`` version heading in a CHANGELOG body.

    Lines inside fenced code blocks are skipped — a ```` ``` ```` sample of a
    changelog heading documents the format, it does not declare a release.

    Malformed labels are returned too (with ``parsed=None``) rather than
    dropped — a heading the guard cannot read is a heading it cannot
    enforce, and silently ignoring it would be a false green.
    """
    lines = text.splitlines()
    fences = scan_fences(lines)
    releases: List[ChangelogRelease] = []
    for index, line in enumerate(lines):
        if fences.mask[index]:
            continue
        match = _CHANGELOG_HEADING_RE.match(line)
        if match is None:
            continue
        label = match.group(1).strip()
        if label.lower() == _UNRELEASED_LABEL:
            continue
        releases.append(
            ChangelogRelease(
                version=label,
                line=index + 1,
                heading=line.strip(),
                parsed=parse_version_token(label),
            ),
        )
    return releases


def parse_release_entries(text: str) -> List[ReleaseEntry]:
    """Return every level-2 section in a ``releases.md`` body.

    Headings inside fenced code blocks are skipped, for the same reason as in
    :func:`parse_changelog_releases`.

    Each entry records whether it is *planned* — determined from the
    heading's ``(Planned`` marker or from the section's ``**Status:**``
    line opening with "Planned".  Both are checked because the two
    forward-looking sections in the real file announce themselves
    differently, and a planned section must never satisfy a released
    version (see the module docstring).  The ``**Status:**`` scan skips
    fenced body lines so a quoted example cannot flip the flag; ``has_body``
    counts them, because a section whose whole body is a code block is still
    a written-up release.
    """
    lines = text.splitlines()
    fences = scan_fences(lines)
    heading_indices = [
        index for index, line in enumerate(lines) if not fences.mask[index] and _RELEASES_HEADING_RE.match(line)
    ]

    entries: List[ReleaseEntry] = []
    for position, index in enumerate(heading_indices):
        line = lines[index]
        token = _RELEASES_HEADING_RE.match(line).group(1)  # type: ignore[union-attr]
        end = heading_indices[position + 1] if position + 1 < len(heading_indices) else len(lines)
        body = range(index + 1, end)
        planned = bool(_PLANNED_HEADING_RE.search(line)) or any(
            not fences.mask[i] and _PLANNED_STATUS_RE.match(lines[i].strip()) for i in body
        )
        entries.append(
            ReleaseEntry(
                version=token,
                line=index + 1,
                heading=line.strip(),
                parsed=parse_version_token(token),
                planned=planned,
                has_body=any(lines[i].strip() for i in body),
            ),
        )
    return entries


def find_headline_versions(text: str, marker: str) -> List[Optional[str]]:
    """Return the version token named on **every** ``marker`` line in ``text``.

    One list element per headline line found, so a caller can tell "no
    headline" (empty list) from "two competing headlines" (length 2) — with
    duplicates, whichever line happened to come first would otherwise be
    enforced and the other silently ignored.

    Each element is the first backticked token on its line (the files' own
    convention: ``**Released:** `v0.9.0` — "..."``), falling back to the first
    whitespace-delimited word after the marker if the backticks are ever
    dropped, or ``None`` if the line carries no token at all.  Lines inside
    code fences are skipped.
    """
    lines = text.splitlines()
    fences = scan_fences(lines)
    found: List[Optional[str]] = []
    for index, line in enumerate(lines):
        if fences.mask[index]:
            continue
        stripped = line.strip()
        if not stripped.startswith(marker):
            continue
        remainder = stripped[len(marker) :]
        backticked = _BACKTICK_TOKEN_RE.search(remainder)
        if backticked is not None:
            found.append(backticked.group(1).strip())
            continue
        words = remainder.split()
        found.append(words[0] if words else None)
    return found


def is_record(entry: ReleaseEntry) -> bool:
    """True when ``entry`` can satisfy a released version.

    Three requirements, each closing a way to be green without writing the
    release note: it must parse as a version, it must not be a ``(Planned)``
    promise, and it must have a body.
    """
    return entry.parsed is not None and not entry.planned and entry.has_body


def records_by_version(entries: Sequence[ReleaseEntry]) -> Dict[Version, ReleaseEntry]:
    """Index the record-worthy entries by version, first occurrence winning."""
    index: Dict[Version, ReleaseEntry] = {}
    for entry in entries:
        if is_record(entry):
            index.setdefault(entry.parsed, entry)  # type: ignore[arg-type]
    return index


def is_exempt(release: ChangelogRelease) -> bool:
    """True when ``release`` predates ``docs/roadmap/releases.md`` itself."""
    if release.parsed is None:
        return False
    return release.parsed in _PRE_RECORD_RELEASES


def find_unreadable_entries(entries: Sequence[ReleaseEntry]) -> List[ReleaseEntry]:
    """Return non-planned ``releases.md`` headings that are not readable versions.

    ``## v0.7.x`` / ``## v0.6.0-pro`` are planned sections and expected; anything
    else unparsable is worth naming, because otherwise the operator only sees a
    "missing release" for a version that *is* written down, just unreadably.
    """
    return [entry for entry in entries if entry.parsed is None and not entry.planned]


def find_missing_releases(
    releases: Sequence[ChangelogRelease],
    entries: Sequence[ReleaseEntry],
) -> List[ChangelogRelease]:
    """Return released versions with no record-worthy section in ``releases.md``."""
    recorded = set(records_by_version(entries))
    missing = [
        release
        for release in releases
        if not release.is_malformed and not is_exempt(release) and release.parsed not in recorded
    ]
    # Oldest first: the report doubles as a to-do list, and release notes are
    # written in the order the releases happened.
    return sorted(missing, key=lambda release: release.parsed)  # type: ignore[arg-type,return-value]


def find_date_mismatches(
    releases: Sequence[ChangelogRelease],
    entries: Sequence[ReleaseEntry],
) -> List[DateMismatch]:
    """Return released versions whose two headings disagree about the date.

    Each result carries ``exempt`` so the caller can report the frozen
    historical pairs (:data:`_LEGACY_DATE_MISMATCHES`) as a note while failing
    on everything else.  Versions where either heading omits a date are skipped:
    ``## v0.5.0 — "Document Ingestion..."`` genuinely has none, and inventing a
    comparison against a missing value would be noise, not enforcement.
    """
    records = records_by_version(entries)
    mismatches: List[DateMismatch] = []
    for release in releases:
        if release.parsed is None:
            continue
        entry = records.get(release.parsed)
        if entry is None:
            continue
        changelog_date = extract_date(release.heading)
        record_date = extract_date(entry.heading)
        if changelog_date is None or record_date is None or changelog_date == record_date:
            continue
        mismatches.append(
            DateMismatch(
                release=release,
                entry=entry,
                changelog_date=changelog_date,
                record_date=record_date,
                exempt=release.parsed in _LEGACY_DATE_MISMATCHES,
            ),
        )
    return sorted(mismatches, key=lambda mismatch: mismatch.release.parsed)  # type: ignore[arg-type,return-value]


def find_stale_date_exemptions(
    releases: Sequence[ChangelogRelease],
    entries: Sequence[ReleaseEntry],
) -> List[Version]:
    """Return exempted versions whose dates now agree, so the set can shrink.

    An exemption that outlives the mismatch it excused is dead weight that
    quietly widens the guard's blind spot.
    """
    records = records_by_version(entries)
    reconciled: List[Version] = []
    for release in releases:
        if release.parsed is None or release.parsed not in _LEGACY_DATE_MISMATCHES:
            continue
        entry = records.get(release.parsed)
        if entry is None:
            continue
        changelog_date = extract_date(release.heading)
        record_date = extract_date(entry.heading)
        if changelog_date is not None and changelog_date == record_date:
            reconciled.append(release.parsed)
    return sorted(reconciled)


def newest_release(releases: Sequence[ChangelogRelease]) -> Optional[ChangelogRelease]:
    """Return the highest released version, compared semantically.

    Never a string compare: ``"0.10.0" < "0.9.0"`` lexically
    (``docs/standards/release.md`` §``__api_version__``).
    """
    parsable = [release for release in releases if not release.is_malformed]
    if not parsable:
        return None
    return max(parsable, key=lambda release: release.parsed)  # type: ignore[arg-type,return-value]


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Verify every released version in CHANGELOG.md has a (non-planned) "
            "section in docs/roadmap/releases.md, and that the '**Released:**' / "
            "'**Yayınlandı:**' headlines in docs/roadmap.md and docs/roadmap-tr.md "
            "name the newest one."
        ),
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Strict mode: exit 1 on any unrecorded release, stale headline, "
            "malformed changelog heading or date mismatch.  Default (no flag) is "
            "advisory: report to stdout but exit 0 — useful for local iteration.  "
            "A missing input file exits 1 either way."
        ),
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress success summary.")
    return parser


def _report_unterminated_fences(texts: Dict[Path, str]) -> bool:
    """Print a block per input whose code fence is never closed.  True if any."""
    failed = False
    for path, text in texts.items():
        scan = scan_fences(text.splitlines())
        if scan.unterminated_line is None:
            continue
        failed = True
        print(f"FAIL: {_rel(path)}:{scan.unterminated_line} opens a code fence that is never closed.")
        print("  Everything after it is treated as fenced and skipped, which would silently")
        print("  disable this guard for the rest of the file.  Close the fence.")
    return failed


def _report_headlines(newest: ChangelogRelease, texts: Dict[Path, str]) -> bool:
    """Check every headline source against ``newest``.  True if any failed.

    Each source is reported separately and none short-circuits the others: the
    Turkish mirror is the copy that drifts furthest precisely because it is the
    one nobody re-reads, so it must never be masked by an English failure.
    """
    failed = False
    for source in headline_sources():
        rel = _rel(source.path)
        headlines = find_headline_versions(texts[source.path], source.marker)
        if not headlines:
            failed = True
            print(
                f"FAIL: {rel} has no '{source.marker}' line, so its headline cannot be "
                f"checked against the newest release (v{newest.parsed})."
            )
            continue
        if len(headlines) > 1:
            failed = True
            print(
                f"FAIL: {rel} has {len(headlines)} '{source.marker}' lines "
                f"({', '.join(str(headline) for headline in headlines)}); the headline must be unique, "
                "or the guard would enforce whichever came first and ignore the rest."
            )
            continue
        headline = headlines[0]
        if headline is None or parse_version_token(headline) != newest.parsed:
            failed = True
            print(
                f"FAIL: {rel}'s '{source.marker}' line names {headline}, but the newest "
                f"released version in CHANGELOG.md is v{newest.parsed} "
                f"({_rel(CHANGELOG_PATH)}:{newest.line})."
            )
            print(f"  Update the headline to v{newest.parsed} — see the post-release step in {SKILL_REFERENCE}.")
    return failed


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_arg_parser().parse_args(argv)

    inputs = [CHANGELOG_PATH, RELEASES_PATH, *(source.path for source in headline_sources())]
    for path in inputs:
        if not path.is_file():
            print(f"check_release_record_sync: {path} not found.", file=sys.stderr)
            # Deliberately not advisory-gated: see the module docstring's exit-code
            # note.  Unreadable inputs are a broken invocation, not drift.
            return 1

    texts = {path: path.read_text(encoding="utf-8") for path in inputs}
    releases = parse_changelog_releases(texts[CHANGELOG_PATH])
    entries = parse_release_entries(texts[RELEASES_PATH])

    failed = _report_unterminated_fences(texts)
    rel_changelog = _rel(CHANGELOG_PATH)
    rel_releases = _rel(RELEASES_PATH)

    malformed = [release for release in releases if release.is_malformed]
    if malformed:
        failed = True
        print(f"FAIL: {len(malformed)} {rel_changelog} release heading(s) are not readable as a version.")
        for release in malformed:
            print(f"  {rel_changelog}:{release.line}  {release.heading}")
        print("  Use the Keep-a-Changelog form '## [X.Y.Z] — YYYY-MM-DD' (or '## [Unreleased]').")

    unreadable = find_unreadable_entries(entries)
    if unreadable:
        # Advisory: a non-version level-2 section may be legitimate one day.  It
        # is named anyway so it never surfaces only as a puzzling "missing release".
        print(f"NOTE: {len(unreadable)} {rel_releases} heading(s) are not readable as a version.")
        for entry in unreadable:
            print(f"  {rel_releases}:{entry.line}  {entry.heading}")
        print("  They cannot satisfy any released version; rename them or mark them '(Planned)'.")

    missing = find_missing_releases(releases, entries)
    if missing:
        failed = True
        print(f"FAIL: {len(missing)} released version(s) in {rel_changelog} have no entry in {rel_releases}.")
        for release in missing:
            print(f"  v{release.parsed}  —  {rel_changelog}:{release.line}  {release.heading}")
        print(
            f"  Add a '## vX.Y.Z — \"Title\" (YYYY-MM-DD)' section to {rel_releases} for each "
            "version above — with a body; a heading on its own and a '(Planned)' section both "
            f"fail to count as a record — then refresh the headline in "
            f"{' and '.join(_rel(source.path) for source in headline_sources())}."
        )
        print(f"  This is the post-release step in {SKILL_REFERENCE}.")

    mismatches = find_date_mismatches(releases, entries)
    live = [mismatch for mismatch in mismatches if not mismatch.exempt]
    if live:
        failed = True
        print(f"FAIL: {len(live)} released version(s) are dated differently in {rel_changelog} and {rel_releases}.")
        for mismatch in live:
            print(
                f"  v{mismatch.release.parsed}  —  {rel_changelog}:{mismatch.release.line} says "
                f"{mismatch.changelog_date}, {rel_releases}:{mismatch.entry.line} says {mismatch.record_date}."
            )
        print("  Both headings date the same event; make them agree (the tag date wins).")
    if not args.quiet:
        # Informational, not actionable: these pairs are frozen history.  They are
        # worth stating so a reader of a green log knows the date check ran and saw
        # them, but `--quiet` ("only tell me about problems") suppresses them.
        for mismatch in (mismatch for mismatch in mismatches if mismatch.exempt):
            print(
                f"NOTE: v{mismatch.release.parsed}'s dates disagree ({mismatch.changelog_date} vs "
                f"{mismatch.record_date}) — a known historical pair, exempt by _LEGACY_DATE_MISMATCHES."
            )
    for version in find_stale_date_exemptions(releases, entries):
        print(
            f"NOTE: v{version} is listed in _LEGACY_DATE_MISMATCHES but its dates now agree — "
            "drop it from the exemption set."
        )

    newest = newest_release(releases)
    if newest is None:
        failed = True
        print(f"FAIL: {rel_changelog} declares no released version at all — nothing to cross-check.")
    elif _report_headlines(newest, texts):
        failed = True

    if failed:
        return 1 if args.strict else 0

    if not args.quiet:
        checked = [release for release in releases if not is_exempt(release)]
        print(
            f"OK: {len(checked)} released version(s) in {rel_changelog} all have a section in "
            f"{rel_releases}; "
            f"{' and '.join(_rel(source.path) for source in headline_sources())} name the newest "
            f"(v{newest.parsed})."  # type: ignore[union-attr]
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
