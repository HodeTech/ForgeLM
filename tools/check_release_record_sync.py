#!/usr/bin/env python3
"""Release-record sync guard (CHANGELOG -> roadmap release notes).

The drift class this prevents
-----------------------------
Cutting a release is a multi-file operation.  ``CHANGELOG.md`` gets the
``## [X.Y.Z] — DATE`` section, ``pyproject.toml`` gets the version bump,
the tag goes out — and then the *post-release* half of the checklist
("Close the phase", step 4 of ``.claude/skills/cut-release/SKILL.md``)
asks for two more edits that nothing verified:

* a ``## vX.Y.Z — "Title" (DATE)`` section in
  ``docs/roadmap/releases.md``, and
* a refreshed ``**Released:**`` headline in ``docs/roadmap.md``.

Those two edits happen after the satisfying part of the release is done,
which is exactly when a checklist stops being read.  **They were skipped
for two consecutive releases — v0.8.0 (2026-06-16) and v0.9.0
(2026-07-05).**  ``CHANGELOG.md`` recorded both as shipped while
``releases.md``'s newest entry was still ``v0.7.0`` (followed by a
``v0.7.x (Planned)`` section, which reads to a skimmer as if the record
were current) and ``docs/roadmap.md`` still announced ``**Released:**
v0.7.0``.  A reader arriving at the roadmap — the page the project points
newcomers at — was told the product was two minor versions behind where
PyPI actually had it.

Nothing detected it because the drift is *between* files that are each
internally consistent.  Only a cross-file check can see it, so:

1. **Every released version has a record.**  Parse each
   ``## [X.Y.Z] — DATE`` heading in ``CHANGELOG.md`` (``[Unreleased]`` is
   skipped by definition) and require a matching level-2 section in
   ``docs/roadmap/releases.md``.  Versions are compared *parsed*
   (:class:`packaging.version.Version`), not as strings, so ``v0.9.0``
   in the release notes satisfies ``[0.9.0]`` in the changelog and
   ``v0.10.0`` sorts above ``v0.9.0`` rather than below it.
2. **The roadmap headline names the newest release.**  ``docs/roadmap.md``'s
   ``**Released:**`` line must name the highest released version in
   ``CHANGELOG.md``.  Rule 1 alone would go green the moment someone
   appended a ``releases.md`` section while leaving the headline stale,
   which is half of the failure that actually occurred.

A "(Planned)" section does not count
------------------------------------
``releases.md`` legitimately carries forward-looking sections
(``## v0.7.x — "Pipeline Hardening" (Planned)``,
``## v0.6.0-pro — "Pro CLI" (Planned, gated)``).  A planned section is a
promise, not a record, so it can never satisfy a released version.  An
entry is treated as planned when ``(Planned`` appears in its heading or
when the section's ``**Status:**`` line begins with "Planned".  This
matters beyond pedantry: had ``v0.7.x`` been allowed to satisfy a
``0.7.x``-shaped lookup, the very state this guard was written for would
have passed.

Version tokens that are not PEP 440 versions (``v0.7.x``,
``v0.6.0-pro``) simply never match a released version — no special-casing
needed, they fail to parse and are carried as unparsed entries.

Exit codes (per the ``tools/`` contract — NOT the public 0/1/2/3/4/5
surface that ``forgelm/`` honours):

- ``0`` — every released version has a non-planned record and the
  roadmap headline names the newest one.
- ``1`` — at least one released version is unrecorded, or the headline is
  stale, or a changelog heading is malformed (strict mode); or the guard
  could not resolve its inputs.

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
from typing import List, Optional, Sequence

from packaging.version import InvalidVersion, Version

REPO_ROOT = Path(__file__).resolve().parent.parent
CHANGELOG_PATH = REPO_ROOT / "CHANGELOG.md"
RELEASES_PATH = REPO_ROOT / "docs" / "roadmap" / "releases.md"
ROADMAP_PATH = REPO_ROOT / "docs" / "roadmap.md"

#: Where the post-release step that this guard enforces is written down.
SKILL_REFERENCE = '.claude/skills/cut-release/SKILL.md ("Post-release" -> "Close the phase")'

#: Bracket label that marks the not-yet-cut section of a Keep-a-Changelog file.
_UNRELEASED_LABEL = "unreleased"

#: Releases that predate ``docs/roadmap/releases.md`` itself.  The file opens
#: at ``v0.3.0``; ``v0.1.0`` and ``v0.2.0`` shipped before the roadmap tree
#: existed and were never back-filled.  Exempted explicitly rather than by
#: deriving a floor from the oldest section present — a floor silently widens
#: the exemption every time the oldest entry is edited or removed, which is the
#: same "nothing noticed" failure mode this guard exists to close.
_PRE_RECORD_RELEASES = frozenset({"0.1.0", "0.2.0"})

# Per docs/standards/regex.md: anchored, every quantifier bounded, and no two
# quantifiers competing for the same characters — a match attempt is O(1) and a
# whole-file scan is O(n).
#
# `## [0.9.0] — 2026-07-05`  ->  captures `0.9.0`
_CHANGELOG_HEADING_RE = re.compile(r"^##[ \t]+\[([^\]\n]{1,64})\]")
# `## v0.7.0 — "Pipeline Chains" (2026-05-15)`  ->  captures `v0.7.0`
# The token is taken as the first whitespace-delimited word and validated by
# `parse_version_token`, so heading punctuation never has to be regexed.
_RELEASES_HEADING_RE = re.compile(r"^##[ \t]+(\S{1,64})")
# `**Released:** `v0.7.0` — "Phase 14 ..."`  ->  captures `v0.7.0`
_BACKTICK_TOKEN_RE = re.compile(r"`([^`\n]{1,64})`")

#: Marker for a forward-looking section heading, e.g. `(Planned)` / `(Planned, gated)`.
_PLANNED_HEADING_RE = re.compile(r"\(planned", re.IGNORECASE)
#: `**Status:** Planned. Focus: ...` — a status line that opens with "Planned".
_PLANNED_STATUS_RE = re.compile(r"^\*\*Status:\*\*\s*planned", re.IGNORECASE)

#: Prefix of the roadmap headline that must name the newest released version.
_ROADMAP_RELEASED_MARKER = "**Released:**"


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


def parse_changelog_releases(text: str) -> List[ChangelogRelease]:
    """Return every non-``[Unreleased]`` version heading in a CHANGELOG body.

    Malformed labels are returned too (with ``parsed=None``) rather than
    dropped — a heading the guard cannot read is a heading it cannot
    enforce, and silently ignoring it would be a false green.
    """
    releases: List[ChangelogRelease] = []
    for index, line in enumerate(text.splitlines()):
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

    Each entry records whether it is *planned* — determined from the
    heading's ``(Planned`` marker or from the section's ``**Status:**``
    line opening with "Planned".  Both are checked because the two
    forward-looking sections in the real file announce themselves
    differently, and a planned section must never satisfy a released
    version (see the module docstring).
    """
    lines = text.splitlines()
    heading_indices = [i for i, line in enumerate(lines) if _RELEASES_HEADING_RE.match(line)]

    entries: List[ReleaseEntry] = []
    for position, index in enumerate(heading_indices):
        line = lines[index]
        token = _RELEASES_HEADING_RE.match(line).group(1)  # type: ignore[union-attr]
        end = heading_indices[position + 1] if position + 1 < len(heading_indices) else len(lines)
        planned = bool(_PLANNED_HEADING_RE.search(line)) or any(
            _PLANNED_STATUS_RE.match(body.strip()) for body in lines[index + 1 : end]
        )
        entries.append(
            ReleaseEntry(
                version=token,
                line=index + 1,
                heading=line.strip(),
                parsed=parse_version_token(token),
                planned=planned,
            ),
        )
    return entries


def extract_roadmap_released_version(text: str) -> Optional[str]:
    """Return the version token named on ``docs/roadmap.md``'s ``**Released:**`` line.

    Returns the first backticked token on that line (the file's own
    convention: ``**Released:** `v0.7.0` — "..."``), falling back to the
    first whitespace-delimited word after the marker if the backticks are
    ever dropped.  ``None`` means the line itself is missing, which is a
    failure in its own right — the headline cannot be checked if it does
    not exist.
    """
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith(_ROADMAP_RELEASED_MARKER):
            continue
        remainder = stripped[len(_ROADMAP_RELEASED_MARKER) :]
        backticked = _BACKTICK_TOKEN_RE.search(remainder)
        if backticked is not None:
            return backticked.group(1).strip()
        words = remainder.split()
        return words[0] if words else None
    return None


def is_exempt(release: ChangelogRelease) -> bool:
    """True when ``release`` predates ``docs/roadmap/releases.md`` itself."""
    if release.parsed is None:
        return False
    return str(release.parsed) in _PRE_RECORD_RELEASES


def find_missing_releases(
    releases: Sequence[ChangelogRelease],
    entries: Sequence[ReleaseEntry],
) -> List[ChangelogRelease]:
    """Return released versions with no non-planned section in ``releases.md``."""
    recorded = {entry.parsed for entry in entries if entry.parsed is not None and not entry.planned}
    missing = [
        release
        for release in releases
        if not release.is_malformed and not is_exempt(release) and release.parsed not in recorded
    ]
    # Oldest first: the report doubles as a to-do list, and release notes are
    # written in the order the releases happened.
    return sorted(missing, key=lambda release: release.parsed)  # type: ignore[arg-type,return-value]


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
            "section in docs/roadmap/releases.md, and that docs/roadmap.md's "
            "'**Released:**' headline names the newest one."
        ),
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Strict mode: exit 1 on any unrecorded release, stale headline or "
            "malformed changelog heading.  Default (no flag) is advisory: "
            "report to stdout but exit 0 — useful for local iteration."
        ),
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress success summary.")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_arg_parser().parse_args(argv)

    for path in (CHANGELOG_PATH, RELEASES_PATH, ROADMAP_PATH):
        if not path.is_file():
            print(f"check_release_record_sync: {path} not found.", file=sys.stderr)
            return 1

    releases = parse_changelog_releases(CHANGELOG_PATH.read_text(encoding="utf-8"))
    entries = parse_release_entries(RELEASES_PATH.read_text(encoding="utf-8"))
    roadmap_text = ROADMAP_PATH.read_text(encoding="utf-8")

    failed = False

    malformed = [release for release in releases if release.is_malformed]
    if malformed:
        failed = True
        print(f"FAIL: {len(malformed)} CHANGELOG.md release heading(s) are not readable as a version.")
        for release in malformed:
            print(f"  CHANGELOG.md:{release.line}  {release.heading}")
        print("  Use the Keep-a-Changelog form '## [X.Y.Z] — YYYY-MM-DD' (or '## [Unreleased]').")

    missing = find_missing_releases(releases, entries)
    if missing:
        failed = True
        rel_releases = RELEASES_PATH.relative_to(REPO_ROOT)
        print(f"FAIL: {len(missing)} released version(s) in CHANGELOG.md have no entry in {rel_releases}.")
        for release in missing:
            print(f"  v{release.parsed}  —  CHANGELOG.md:{release.line}  {release.heading}")
        print(
            f"  Add a '## vX.Y.Z — \"Title\" (YYYY-MM-DD)' section to {rel_releases} for each "
            "version above (a '(Planned)' section does not count as a record), then refresh "
            f"{ROADMAP_PATH.relative_to(REPO_ROOT)}'s '{_ROADMAP_RELEASED_MARKER}' line."
        )
        print(f"  This is the post-release step in {SKILL_REFERENCE}.")

    newest = newest_release(releases)
    if newest is None:
        failed = True
        print("FAIL: CHANGELOG.md declares no released version at all — nothing to cross-check.")
    else:
        headline = extract_roadmap_released_version(roadmap_text)
        rel_roadmap = ROADMAP_PATH.relative_to(REPO_ROOT)
        if headline is None:
            failed = True
            print(
                f"FAIL: {rel_roadmap} has no '{_ROADMAP_RELEASED_MARKER}' line, so the roadmap "
                f"headline cannot be checked against the newest release (v{newest.parsed})."
            )
        elif parse_version_token(headline) != newest.parsed:
            failed = True
            print(
                f"FAIL: {rel_roadmap}'s '{_ROADMAP_RELEASED_MARKER}' line names {headline}, but the "
                f"newest released version in CHANGELOG.md is v{newest.parsed} "
                f"(CHANGELOG.md:{newest.line})."
            )
            print(f"  Update the headline to v{newest.parsed} — see the post-release step in {SKILL_REFERENCE}.")

    if failed:
        return 1 if args.strict else 0

    if not args.quiet:
        checked = [release for release in releases if not is_exempt(release)]
        print(
            f"OK: {len(checked)} released version(s) in CHANGELOG.md all have a section in "
            f"{RELEASES_PATH.relative_to(REPO_ROOT)}; "
            f"{ROADMAP_PATH.relative_to(REPO_ROOT)}'s headline names the newest "
            f"(v{newest.parsed})."  # type: ignore[union-attr]
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
