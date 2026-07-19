#!/usr/bin/env python3
"""Release-record sync guard (CHANGELOG -> roadmap release notes).

The drift class this prevents
-----------------------------
Cutting a release is a multi-file operation.  ``CHANGELOG.md`` gets the
``## [X.Y.Z] — DATE`` section, ``pyproject.toml`` gets the version bump,
the tag goes out — and the release *record* asks for more edits that
nothing verified:

* a ``## vX.Y.Z — "Title" (DATE)`` section in
  ``docs/roadmap/releases.md``, and
* a refreshed ``**Released:**`` headline in ``docs/roadmap.md`` **and its
  Turkish mirror's ``**Yayınlandı:**`` headline** in
  ``docs/roadmap-tr.md``.

Those edits used to live in the *post-release* half of the checklist
("Close the phase"), which is after the satisfying part of the release is
done — exactly when a checklist stops being read.  **They were skipped
for two consecutive releases — v0.8.0 (2026-06-16) and v0.9.0
(2026-07-05).**  ``CHANGELOG.md`` recorded both as shipped while
``releases.md``'s newest entry was still ``v0.7.0`` (followed by a
``v0.7.x (Planned)`` section, which reads to a skimmer as if the record
were current), ``docs/roadmap.md`` still announced ``**Released:**
v0.7.0`` — and ``docs/roadmap-tr.md``, the copy nobody re-reads, had
drifted furthest of all at ``v0.5.0``.  A reader arriving at the roadmap
— the page the project points newcomers at — was told the product was
two (in Turkish, four) minor versions behind where PyPI actually had it.

The step has since moved to **pre-release step 4.5 ("Write the release
record — before the tag")** so that this guard can gate it *before* the
tag exists; see :data:`SKILL_REFERENCE`.  A guard cannot rescue a record
written after the tag, because the tag is what publishes to PyPI.

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

Fail-open defences (why this file is longer than its two rules)
---------------------------------------------------------------
A cross-file guard that *parses* its inputs has a failure mode worse than
any drift it can find: input it cannot read is input it reports as clean.
A review round drove three inputs through the original implementation —
``##[9.9.0] — 2026-07-19`` (no space after ``##``), a two-space-indented
heading, and a heading swallowed by a fence that a later, unrelated fence
re-closed — and all three exited **0** with an ``OK:`` line, because a
heading that does not match :data:`_CHANGELOG_HEADING_RE` simply is not a
release.  The success line even read ``OK: 0 released version(s) …``.
That is the same defect class as a severity-bucket-empty pip-audit run
reporting green.  Four layers close it:

* **A parse floor** (:func:`find_parse_floor_failures`).  Zero level-2
  headings in ``CHANGELOG.md``, zero released versions, or zero
  ``releases.md`` sections is a *broken invocation*, not a clean tree.
  Like the missing-input branch it exits ``1`` **regardless of
  ``--strict``** and reports on stderr: there is no local-iteration story
  in which "my changelog parsed as nothing" is drift to fix later.
* **A near-miss detector** (:func:`find_near_miss_headings`).  Any line
  shaped like a version heading that the file's own heading grammar
  rejected is reported with ``file:line``, the raw line, the specific
  defect, and the expected form.  **Decision: strict-failing, not
  advisory.**  The other advisory block in this file
  (:func:`find_unreadable_entries`) is advisory because a non-version
  level-2 section may legitimately appear one day — it is a heading the
  guard *understood* and judged irrelevant.  A near-miss is the opposite:
  a heading whose author plainly meant a release, one keystroke away from
  counting, and dropping it silently is precisely the incident above.
  There is no legitimate ``##[1.2.3]``.  When the parse is otherwise
  empty the floor fires too, so a near-miss beside an empty parse fails
  regardless of ``--strict``.
* **Fence-span accounting** (:func:`find_swallowed_headings`).  An
  unterminated fence was already fatal.  The subtler case is a fence that
  *is* closed — by a marker line the author meant as the opener of the
  next block.  :func:`scan_lines` follows CommonMark §4.5 (a closer uses
  the same character, is at least as long, and carries **no** info
  string), so `````bash`` inside an open block is content rather than a
  closer, and the span records those interior markers.  A span that
  swallows a would-be version heading **and** contains an interior marker
  is structurally broken: fatal.  A span that swallows one with no
  interior marker is a documentation sample: reported as a note, because
  `````text`` blocks showing the heading format are legitimate and the
  tests depend on them.
* **HTML-comment masking** (:func:`scan_lines`).  ``<!-- … -->`` regions
  are stripped from the content every parser reads.  Unmasked, a
  commented-out ``**Released:**`` line failed an otherwise-correct
  roadmap as a duplicate headline (a confirmed false positive), and —
  worse — a commented-out ``## v0.9.0`` section in ``releases.md`` would
  have *satisfied* a released version.  An unterminated ``<!--`` masks
  the remainder of the file, so it is fatal for the same reason an
  unterminated fence is.

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
are advisory, not fatal, for the reason given above: ``releases.md`` may
legitimately grow a non-version level-2 section one day.

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

A date that is *present but unreadable* is not a date that is absent.
``2026–07–05`` (unicode dashes) or ``2026/07/05`` used to fall through
:data:`_DATE_RE` and be treated as "this heading carries no date", which
skipped the cross-check silently — the same fail-open shape as a dropped
heading.  :func:`malformed_date` recognises the date-ish shape and
reports it as a strict failure instead.

Exit codes (per the ``tools/`` contract — NOT the public 0/1/2/3/4/5
surface that ``forgelm/`` honours):

- ``0`` — every released version has a non-planned, non-empty record
  whose date agrees with the changelog, and every roadmap headline names
  the newest release exactly once.
- ``1`` — in strict mode: at least one released version is unrecorded, a
  headline is stale/missing/duplicated, a changelog heading is malformed
  or a near miss, a non-exempt date pair disagrees, a date is present but
  unreadable, or a fence/comment is left open.  **Also ``1`` regardless
  of ``--strict`` when an input file is missing or when the parse floor
  is not met**: both are broken invocations rather than drift to iterate
  on locally, and a guard that cannot read its inputs must never report
  success.  Matches ``tools/check_usermanual_schema_drift.py``.

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
from typing import Dict, List, Literal, Optional, Sequence, Tuple

from packaging.version import InvalidVersion, Version

REPO_ROOT = Path(__file__).resolve().parent.parent
CHANGELOG_PATH = REPO_ROOT / "CHANGELOG.md"
RELEASES_PATH = REPO_ROOT / "docs" / "roadmap" / "releases.md"
ROADMAP_PATH = REPO_ROOT / "docs" / "roadmap.md"
ROADMAP_TR_PATH = REPO_ROOT / "docs" / "roadmap-tr.md"

#: Where the step that this guard enforces is written down.  Named
#: agent-neutrally: the skill tree is mirrored, and an agent working from
#: ``.agents/`` has no ``.claude/`` directory to open (and vice versa).
#: Points at *pre-release* step 4.5, not the post-release "Close the phase"
#: step it was moved out of — that move is the whole reason the guard can run
#: before the tag exists, and a pointer to the old location sends a releaser
#: to a section whose own text says the record does not belong there.
SKILL_REFERENCE = (
    "the cut-release skill — skills/cut-release/SKILL.md under .claude/ or .agents/ "
    '(pre-release step 4.5, "Write the release record — before the tag")'
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
# whole-file scan is O(n).  All run line-by-line (no re.MULTILINE, Rule 7).
# Fence, HTML-comment and near-miss recognition are line walkers rather than
# regexes (Rule 6 / Rule 8) — see :func:`scan_lines` and
# :func:`_diagnose_heading`.
#
# `## [0.9.0] — 2026-07-05`  ->  captures `0.9.0`
_CHANGELOG_HEADING_RE = re.compile(r"^##[ \t]+\[([^\]\n]{1,64})\]")
# `## v0.7.0 — "Pipeline Chains" (2026-05-15)`  ->  captures `v0.7.0`
# The token is taken as the first whitespace-delimited word and validated by
# `parse_version_token`, so heading punctuation never has to be regexed.
_RELEASES_HEADING_RE = re.compile(r"^##[ \t]+(\S{1,64})")
# Any level-2 ATX heading at all — the parse floor's "does this even look like
# a markdown document with sections?" probe.
_ANY_L2_HEADING_RE = re.compile(r"^##[ \t]")
# `**Released:** `v0.7.0` — "Phase 14 ..."`  ->  captures `v0.7.0`
_BACKTICK_TOKEN_RE = re.compile(r"`([^`\n]{1,64})`")
# `## [0.9.0] — 2026-07-05` / `## v0.9.0 — "..." (2026-07-05)`  ->  `2026-07-05`
# Fully bounded, no alternation, no backtracking surface; searched against a
# single heading line, never a whole file.
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
# The same shape with a separator a human might type or a smart editor might
# substitute: unicode dashes (U+2010..U+2015 hyphen..horizontal-bar, U+2212
# minus) and a slash.  Used only to tell "no date here" apart from "a date the
# guard cannot read"; `.` is deliberately NOT a separator, because `1234.56.78`
# is a version, not a date.
_LOOSE_DATE_RE = re.compile(r"\d{4}[-‐-―−/]\d{2}[-‐-―−/]\d{2}")

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

#: Opening / closing delimiters of an HTML comment.
_COMMENT_OPEN = "<!--"
_COMMENT_CLOSE = "-->"

#: Decoration a heading's version token may be wrapped in.  An explicit finite
#: set rather than a regex — "strip these characters from both ends" is a string
#: question, and `docs/standards/regex.md` Rule 8 says not to reach for `re` for
#: those.  Fullwidth/CJK brackets are listed because a heading typed on a
#: Turkish or CJK keyboard layout is exactly the near miss this detector exists
#: to name rather than drop.
_TOKEN_TRIM = "[](){}<>【】（）［］\"'`*_“”‘’"

#: Which heading grammar a scanned file uses.  ``releases.md`` accepts a bare
#: ``## v0.9.0``; ``CHANGELOG.md`` requires the Keep-a-Changelog ``## [0.9.0]``.
Dialect = Literal["changelog", "releases"]

_EXPECTED_SHAPE: Dict[str, str] = {
    "changelog": "## [X.Y.Z] — YYYY-MM-DD",
    "releases": '## vX.Y.Z — "Title" (YYYY-MM-DD)',
}


@dataclass(frozen=True)
class FenceSpan:
    """One fenced code block, with the fence lines it swallowed.

    ``interior_markers`` holds the 1-based lines *inside* the span that begin
    with a fence marker but did not close it (CommonMark §4.5: a closer uses the
    same character, is at least as long, and carries no info string).  Their
    presence is the signal that the author meant to open a second block and the
    first one was never really closed — see :func:`find_swallowed_headings`.
    """

    opener: int
    closer: Optional[int]
    interior_markers: Tuple[int, ...]


@dataclass(frozen=True)
class LineScan:
    """Per-line view of a markdown file with non-content regions neutralised."""

    #: True when the line is inside — or is — a code fence.
    mask: Tuple[bool, ...]
    #: The line with every ``<!-- … -->`` region removed; fenced lines are "".
    #: This is what every parser reads.
    content: Tuple[str, ...]
    #: The untouched source lines, so the fence reports can show what was masked.
    raw: Tuple[str, ...]
    #: 1-based line of a fence opener that is never closed, else ``None``.
    unterminated_fence: Optional[int]
    #: 1-based line of an HTML comment that is never closed, else ``None``.
    unterminated_comment: Optional[int]
    spans: Tuple[FenceSpan, ...]


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


@dataclass(frozen=True)
class NearMiss:
    """A line shaped like a version heading that the grammar rejected."""

    line: int
    raw: str
    reasons: Tuple[str, ...]
    dialect: str


@dataclass(frozen=True)
class SwallowedHeading:
    """A would-be version heading hidden inside a fenced block."""

    span: FenceSpan
    line: int
    raw: str
    #: True when the span also holds an interior fence marker, i.e. the fence
    #: structure is broken rather than the block being a deliberate sample.
    structural: bool


def _rel(path: Path) -> str:
    """Render ``path`` relative to the repo root, or absolutely if it is outside.

    The four inputs are module-level constants precisely so a test can point
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


def _fence_marker(line: str) -> Optional[Tuple[str, int, str]]:
    """Return ``(char, run_length, info_string)`` for a fence line, else ``None``."""
    stripped = line.lstrip()
    if not stripped.startswith(_FENCE_MARKERS):
        return None
    char = stripped[0]
    run = len(stripped) - len(stripped.lstrip(char))
    return char, run, stripped[run:].strip()


def _strip_inline_comments(line: str) -> Tuple[str, bool]:
    """Remove every complete ``<!-- … -->`` from ``line``.

    Returns the surviving text and whether a comment is still open at the end of
    the line.  Text is *removed* rather than the whole line being masked: a
    ``**Released:** `v0.9.0` <!-- note -->`` headline is real content with a
    comment attached, and blanking the line would lose the claim being checked.
    """
    surviving = line
    while True:
        start = surviving.find(_COMMENT_OPEN)
        if start == -1:
            return surviving, False
        end = surviving.find(_COMMENT_CLOSE, start + len(_COMMENT_OPEN))
        if end == -1:
            return surviving[:start], True
        surviving = surviving[:start] + surviving[end + len(_COMMENT_CLOSE) :]


def scan_lines(lines: Sequence[str]) -> LineScan:
    """Neutralise code fences and HTML comments, line by line.

    A line walker rather than a regex, per ``docs/standards/regex.md`` Rule 6
    (a fenced-block regex needs a back-reference plus ``DOTALL``, the textbook
    ReDoS shape); this is O(n) by construction.

    Fence closing follows CommonMark §4.5 strictly: a closer must use the same
    character as its opener, be at least as long, and carry **no** info string.
    The earlier implementation toggled on any fence-marker line, which meant a
    ```` ```bash ```` line that the author intended as the *opener* of the next
    block silently closed the previous one — masking everything between and
    leaving the file looking balanced.  Under the strict rule that line is
    recorded as an interior marker of the still-open span, which is what makes
    :func:`find_swallowed_headings` able to tell a broken fence apart from a
    deliberate sample.

    An *unterminated* fence — or an unterminated ``<!--`` — masks the whole
    remainder of a file, which would silently disable enforcement, so each is
    reported for the caller to fail on.
    """
    mask: List[bool] = []
    content: List[str] = []
    spans: List[FenceSpan] = []

    fence_char: Optional[str] = None
    fence_run = 0
    fence_opener: Optional[int] = None
    interior: List[int] = []
    comment_opener: Optional[int] = None
    in_comment = False

    for index, line in enumerate(lines):
        if fence_opener is not None:
            # Inside a fence: neither headings nor comments are content.
            mask.append(True)
            content.append("")
            marker = _fence_marker(line)
            if marker is None:
                continue
            char, run, info = marker
            if char == fence_char and run >= fence_run and not info:
                spans.append(FenceSpan(opener=fence_opener, closer=index + 1, interior_markers=tuple(interior)))
                fence_char, fence_run, fence_opener, interior = None, 0, None, []
            else:
                interior.append(index + 1)
            continue

        if in_comment:
            mask.append(False)
            end = line.find(_COMMENT_CLOSE)
            if end == -1:
                content.append("")
                continue
            in_comment = False
            surviving, in_comment = _strip_inline_comments(line[end + len(_COMMENT_CLOSE) :])
            if in_comment:
                comment_opener = index + 1
            content.append(surviving)
            continue

        marker = _fence_marker(line)
        if marker is not None:
            mask.append(True)
            content.append("")
            fence_char, fence_run, _info = marker
            fence_opener = index + 1
            interior = []
            continue

        surviving, in_comment = _strip_inline_comments(line)
        if in_comment:
            comment_opener = index + 1
        mask.append(False)
        content.append(surviving)

    if fence_opener is not None:
        spans.append(FenceSpan(opener=fence_opener, closer=None, interior_markers=tuple(interior)))

    return LineScan(
        mask=tuple(mask),
        content=tuple(content),
        raw=tuple(lines),
        unterminated_fence=fence_opener,
        unterminated_comment=comment_opener if in_comment else None,
        spans=tuple(spans),
    )


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

    "Has none" genuinely means none — a date written with unicode dashes or
    slashes is caught by :func:`malformed_date` instead of quietly disappearing.
    """
    match = _DATE_RE.search(heading)
    return match.group(0) if match is not None else None


def malformed_date(heading: str) -> Optional[str]:
    """Return the first date-shaped token that :data:`_DATE_RE` cannot read.

    A date that is present but unparseable must not be treated as a date that is
    absent: :func:`find_date_mismatches` skips a dateless heading by design, so
    ``2026–07–05`` used to disable the cross-check silently rather than flag it.

    Positions are compared rather than mere presence, because "first date on the
    line" is :func:`extract_date`'s contract: a good date followed by a
    slash-separated one in a parenthetical is not a defect, a bad one *before*
    the good one is.
    """
    loose = _LOOSE_DATE_RE.search(heading)
    if loose is None:
        return None
    exact = _DATE_RE.search(heading)
    if exact is not None and exact.start() <= loose.start():
        return None
    return loose.group(0)


def _is_version_heading(raw: str, dialect: Dialect) -> bool:
    """True when ``raw`` parses as a *version* heading in ``dialect``.

    Stricter than the heading regexes alone, which accept any level-2 heading in
    the ``releases.md`` dialect and ``## [Unreleased]`` in the changelog one.
    A fenced ``## Installation`` is not a swallowed release.
    """
    accepted = _CHANGELOG_HEADING_RE if dialect == "changelog" else _RELEASES_HEADING_RE
    match = accepted.match(raw)
    return match is not None and parse_version_token(match.group(1)) is not None


def _diagnose_heading(raw: str, dialect: Dialect) -> Tuple[str, ...]:
    """Return why ``raw`` failed to parse as a version heading, or ``()``.

    Empty means "not a near miss": either the line is not heading-shaped, or its
    first token is not a version, or the grammar accepted it outright.  Written
    as string operations rather than a permissive "almost a heading" regex —
    ``docs/standards/regex.md`` Rule 8 — because every extra alternation in such
    a pattern is another way to mis-classify a legitimate prose heading.
    """
    lstripped = raw.lstrip()
    if not lstripped.startswith("#"):
        return ()
    hashes = len(lstripped) - len(lstripped.lstrip("#"))
    if hashes > 6:  # `#######` is paragraph text in CommonMark, not a heading.
        return ()

    rest = lstripped[hashes:]
    body = rest.lstrip()
    if not body:
        return ()
    token = body.split()[0]
    if parse_version_token(token.strip(_TOKEN_TRIM)) is None:
        return ()

    accepted = _CHANGELOG_HEADING_RE if dialect == "changelog" else _RELEASES_HEADING_RE
    if accepted.match(raw) is not None:
        return ()

    reasons: List[str] = []
    indent = len(raw) - len(lstripped)
    if indent:
        reasons.append(f"indented by {indent} character(s) — an ATX heading must start at column 1")
    if hashes != 2:
        reasons.append(f"heading depth is '{'#' * hashes}', expected '##'")
    gap = rest[: len(rest) - len(body)]
    if not gap:
        reasons.append("no space between '##' and the version")
    elif any(char not in " \t" for char in gap):
        codepoints = " ".join(f"U+{ord(char):04X}" for char in gap)
        reasons.append(f"the separator after '##' is not a space or tab ({codepoints})")
    if dialect == "changelog" and not token.startswith("["):
        reasons.append("the version is not wrapped in ASCII '[' ... ']'")
    if raw != raw.rstrip():
        reasons.append("trailing whitespace")
    return tuple(reasons)


def find_near_miss_headings(scan: LineScan, dialect: Dialect) -> List[NearMiss]:
    """Return every line that looks like a version heading but did not parse.

    Strict-failing by decision (see the module docstring): unlike an unreadable
    ``releases.md`` section, which is a heading the guard *understood* and found
    irrelevant, a near miss is one keystroke from counting and there is no
    legitimate reason to write it.  Silently dropping it is the exact incident
    this guard exists to prevent, one level down.

    Fenced lines are excluded — a sample inside a fence is deliberate, and
    :func:`find_swallowed_headings` covers the case where the fence itself is
    the defect.
    """
    misses: List[NearMiss] = []
    for index, line in enumerate(scan.content):
        if scan.mask[index]:
            continue
        reasons = _diagnose_heading(line, dialect)
        if reasons:
            misses.append(NearMiss(line=index + 1, raw=line.rstrip("\n"), reasons=reasons, dialect=dialect))
    return misses


def find_swallowed_headings(scan: LineScan, dialect: Dialect) -> List[SwallowedHeading]:
    """Return would-be version headings hidden inside a fenced span.

    ``structural`` marks the dangerous variety: the span also contains a fence
    marker that did not close it, i.e. the author opened a second block inside
    what they believed was closed text.  Everything between is masked and the
    file still looks balanced — the failure mode that made a ``## [9.9.0]``
    heading invisible while the guard printed ``OK:``.

    A span with no interior marker is a documentation sample (a ```` ```text ````
    block showing the heading format), which is legitimate and reported only as
    a note.
    """
    swallowed: List[SwallowedHeading] = []
    for span in scan.spans:
        end = span.closer if span.closer is not None else len(scan.raw) + 1
        for line_number in range(span.opener + 1, end):
            raw_line = scan.raw[line_number - 1]
            if not _is_version_heading(raw_line, dialect) and not _diagnose_heading(raw_line, dialect):
                continue
            swallowed.append(
                SwallowedHeading(
                    span=span,
                    line=line_number,
                    raw=raw_line.rstrip(),
                    structural=bool(span.interior_markers),
                ),
            )
    return swallowed


def parse_changelog_releases(text: str) -> List[ChangelogRelease]:
    """Return every non-``[Unreleased]`` version heading in a CHANGELOG body.

    Lines inside fenced code blocks are skipped — a ```` ``` ```` sample of a
    changelog heading documents the format, it does not declare a release — and
    HTML-comment regions are stripped, so a commented-out heading cannot mint a
    phantom release.

    Malformed labels are returned too (with ``parsed=None``) rather than
    dropped — a heading the guard cannot read is a heading it cannot
    enforce, and silently ignoring it would be a false green.
    """
    scan = scan_lines(text.splitlines())
    releases: List[ChangelogRelease] = []
    for index, line in enumerate(scan.content):
        if scan.mask[index]:
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

    Headings inside fenced code blocks — and inside HTML comments — are skipped,
    for the same reason as in :func:`parse_changelog_releases`.  A commented-out
    section is the more dangerous direction of the two: it would otherwise
    *satisfy* a released version that has no visible record.

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
    scan = scan_lines(text.splitlines())
    lines = scan.content
    heading_indices = [
        index for index, line in enumerate(lines) if not scan.mask[index] and _RELEASES_HEADING_RE.match(line)
    ]

    entries: List[ReleaseEntry] = []
    for position, index in enumerate(heading_indices):
        line = lines[index]
        token = _RELEASES_HEADING_RE.match(line).group(1)  # type: ignore[union-attr]
        end = heading_indices[position + 1] if position + 1 < len(heading_indices) else len(lines)
        body = range(index + 1, end)
        planned = bool(_PLANNED_HEADING_RE.search(line)) or any(
            not scan.mask[i] and _PLANNED_STATUS_RE.match(lines[i].strip()) for i in body
        )
        entries.append(
            ReleaseEntry(
                version=token,
                line=index + 1,
                heading=line.strip(),
                parsed=parse_version_token(token),
                planned=planned,
                has_body=any(lines[i].strip() or scan.mask[i] for i in body),
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
    dropped, or ``None`` if the line carries no token at all.  Lines inside code
    fences are skipped, and HTML-comment regions are stripped: a commented-out
    ``**Released:**`` line used to be counted as a second headline and fail an
    otherwise-correct roadmap.
    """
    scan = scan_lines(text.splitlines())
    found: List[Optional[str]] = []
    for index, line in enumerate(scan.content):
        if scan.mask[index]:
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
    comparison against a missing value would be noise, not enforcement.  A date
    that is present but unreadable is *not* an omission — :func:`malformed_date`
    reports it separately so the skip cannot be bought with a unicode dash.
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


def find_parse_floor_failures(
    changelog_scan: LineScan,
    releases: Sequence[ChangelogRelease],
    entries: Sequence[ReleaseEntry],
) -> List[str]:
    """Return the reasons the inputs did not look like the documents they claim to be.

    A guard that parses nothing out of ``CHANGELOG.md`` and prints ``OK: 0
    released version(s) …`` is fail-open by construction — the same defect class
    as a pip-audit run whose severity buckets are all empty reporting green.
    Each condition here means the *parser* is wrong about the input, not that
    the tree is clean, so the caller exits 1 regardless of ``--strict``, exactly
    as it does for a missing file.

    (A genuinely release-less ``CHANGELOG.md`` would trip this too.  That is the
    right trade: this repo has shipped 10+ releases — ``TestRealRepo`` pins the
    floor — so "zero releases" here can only mean a parse failure, and a project
    with no releases has nothing for this guard to cross-check anyway.)
    """
    reasons: List[str] = []
    headings = sum(
        1
        for index, line in enumerate(changelog_scan.content)
        if not changelog_scan.mask[index] and _ANY_L2_HEADING_RE.match(line)
    )
    if headings == 0:
        reasons.append("it contains no '## ' level-2 heading at all — this does not look like a CHANGELOG")
    if not releases:
        reasons.append("no released version was parsed from it (only '[Unreleased]', or none at all)")
    if not entries:
        reasons.append(f"no level-2 section was parsed from {_rel(RELEASES_PATH)}")
    return reasons


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
            "malformed or near-miss changelog heading, unreadable date or date "
            "mismatch.  Default (no flag) is advisory: report to stdout but exit "
            "0 — useful for local iteration.  A missing input file, or a parse "
            "floor failure, exits 1 either way."
        ),
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help=(
            "Suppress the success summary and the purely informational notes "
            "(the frozen historical date pairs).  Failures and actionable notes "
            "always print — a --quiet that hid drift would be its own fail-open."
        ),
    )
    return parser


def _report_masking_failures(scans: Dict[Path, LineScan]) -> bool:
    """Print a block per input with an unclosed fence or comment.  True if any."""
    failed = False
    for path, scan in scans.items():
        rel = _rel(path)
        if scan.unterminated_fence is not None:
            failed = True
            print(f"FAIL: {rel}:{scan.unterminated_fence} opens a code fence that is never closed.")
            print("  Everything after it is treated as fenced and skipped, which would silently")
            print("  disable this guard for the rest of the file.  Close the fence.")
        if scan.unterminated_comment is not None:
            failed = True
            print(f"FAIL: {rel}:{scan.unterminated_comment} opens an HTML comment that is never closed.")
            print("  Everything after it is commented out and skipped, which would silently")
            print("  disable this guard for the rest of the file.  Close the comment with '-->'.")
    return failed


def _report_near_misses(misses: Sequence[NearMiss], path: Path) -> bool:
    """Print the near-miss block for one file.  True if any (always a failure)."""
    if not misses:
        return False
    rel = _rel(path)
    print(f"FAIL: {len(misses)} line(s) in {rel} look like a version heading but did not parse as one.")
    for miss in misses:
        print(f"  {rel}:{miss.line}  {miss.raw}")
        for reason in miss.reasons:
            print(f"      - {reason}")
        print(f"      expected: {_EXPECTED_SHAPE[miss.dialect]}")
    print("  A heading the grammar rejects is dropped silently, so the release it declares")
    print("  becomes invisible to this guard — which is the drift the guard exists to catch.")
    return True


def _report_swallowed(swallowed: Sequence[SwallowedHeading], path: Path) -> bool:
    """Print fenced-away version headings.  True if any is structurally broken."""
    if not swallowed:
        return False
    rel = _rel(path)
    broken = [item for item in swallowed if item.structural]
    for item in swallowed:
        span = item.span
        closer = span.closer if span.closer is not None else "EOF"
        label = "FAIL" if item.structural else "NOTE"
        print(f"{label}: {rel}:{item.line} is a version heading swallowed by the fence at {rel}:{span.opener}")
        print(f"  (span {span.opener}-{closer}):  {item.raw}")
        if item.structural:
            markers = ", ".join(str(line) for line in span.interior_markers)
            print(f"  The span also contains fence marker(s) at line(s) {markers} that did not close it")
            print("  (CommonMark §4.5: a closer repeats the opener's character, is at least as long,")
            print("  and carries no info string).  The fence structure is broken, not a sample.")
        else:
            print("  Treated as a documentation sample and skipped.  If it declares a real release,")
            print("  move it outside the fence.")
    return bool(broken)


def _report_malformed_dates(
    releases: Sequence[ChangelogRelease],
    entries: Sequence[ReleaseEntry],
) -> bool:
    """Print headings whose date is present but unreadable.  True if any."""
    offenders: List[Tuple[str, int, str, str]] = []
    for release in releases:
        token = malformed_date(release.heading)
        if token is not None:
            offenders.append((_rel(CHANGELOG_PATH), release.line, release.heading, token))
    for entry in entries:
        token = malformed_date(entry.heading)
        if token is not None:
            offenders.append((_rel(RELEASES_PATH), entry.line, entry.heading, token))
    if not offenders:
        return False
    print(f"FAIL: {len(offenders)} heading(s) carry a date that is not 'YYYY-MM-DD'.")
    for rel, line, heading, token in offenders:
        print(f"  {rel}:{line}  {heading}")
        print(f"      unreadable date token: {token!r} — use ASCII hyphens, e.g. '2026-07-05'")
    print("  An unreadable date is skipped by the changelog/releases.md date cross-check,")
    print("  so it would silently buy an exemption the release has not earned.")
    return True


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
            print(f"  Update the headline to v{newest.parsed} — see pre-release step 4.5 in {SKILL_REFERENCE}.")
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
    scans = {path: scan_lines(text.splitlines()) for path, text in texts.items()}
    releases = parse_changelog_releases(texts[CHANGELOG_PATH])
    entries = parse_release_entries(texts[RELEASES_PATH])

    rel_changelog = _rel(CHANGELOG_PATH)
    rel_releases = _rel(RELEASES_PATH)

    # Diagnostics first, so a floor failure still tells the operator *why* the
    # parse came up empty rather than only that it did.
    failed = _report_masking_failures(scans)

    dialects: Dict[Path, Dialect] = {CHANGELOG_PATH: "changelog", RELEASES_PATH: "releases"}
    for path, dialect in dialects.items():
        if _report_near_misses(find_near_miss_headings(scans[path], dialect), path):
            failed = True
        if _report_swallowed(find_swallowed_headings(scans[path], dialect), path):
            failed = True

    floor = find_parse_floor_failures(scans[CHANGELOG_PATH], releases, entries)
    if floor:
        print(f"check_release_record_sync: {rel_changelog} did not parse as a CHANGELOG:", file=sys.stderr)
        for reason in floor:
            print(f"  - {reason}", file=sys.stderr)
        print(
            "  Parsing nothing is a broken invocation, not a clean tree; a guard that reports "
            "success on input it cannot read is fail-open.  See the diagnostics above on stdout.",
            file=sys.stderr,
        )
        # Deliberately not advisory-gated, exactly like the missing-input branch.
        return 1

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

    if _report_malformed_dates(releases, entries):
        failed = True

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
        print(f"  This is pre-release step 4.5 in {SKILL_REFERENCE}.")

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
        # them, but `--quiet` ("only tell me what I have to act on") suppresses them.
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
        print(f"FAIL: {rel_changelog} declares no readable released version — nothing to cross-check.")
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
