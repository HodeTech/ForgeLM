#!/usr/bin/env python3
"""Skill-mirror parity guard (``.claude/skills/`` <-> ``.agents/skills/``).

The drift class this prevents
-----------------------------
ForgeLM ships its agent skills twice: once under ``.claude/skills/`` (read by
Claude Code) and once under ``.agents/skills/`` (read by every other agent
harness).  The two trees are *the same document* — same checklist, same
rules — and they are edited by hand.

Nothing checked that they stayed the same.  ``tests/test_guard_wiring.py``
pins the ``CLAUDE.md`` <-> ``AGENTS.md`` gauntlet lists to each other, but the
skill trees underneath had no equivalent, and a skill is exactly the kind of
file an editor forgets to mirror: the change that motivated this guard —
reordering the ``cut-release`` release ritual so the roadmap record is written
*before* the tag — required the identical edit in two files, in two
directories, that no tool compared.  A one-copy edit is silent: the agent that
reads the un-edited copy simply keeps following the old, wrong procedure, and
the only symptom is the process failure the edit was meant to prevent
recurring in half of all runs.

So: every skill present under one root must be present under the other, and
every mirrored file must be byte-identical **after** the substitution
allowlist below is applied.

The substitution allowlist
--------------------------
A handful of differences between the two trees are legitimate and load-bearing
— the two copies address different harnesses, and a copy that names the other
harness's directory would send its reader to a path that does not exist in its
checkout.  :data:`SUBSTITUTIONS` is the complete, closed list:

===========================  =====================  =====================
Canonical token              ``.claude/`` spelling  ``.agents/`` spelling
===========================  =====================  =====================
``<SKILL_ROOT>``             ``.claude/``           ``.agents/``
``<AGENT_INSTRUCTIONS_DOC>`` ``CLAUDE.md``          ``AGENTS.md``
``<AGENT_NAME>``             ``Claude``             ``Codex``
===========================  =====================  =====================

Anything else that differs is drift and fails the guard.

**Both sides are normalised to the canonical token** rather than rewriting the
``.claude/`` copy into the expected ``.agents/`` copy.  Directional rewriting
reads stricter, but it breaks on prose that legitimately names *both* trees —
"the mirrored SKILL.md under ``.claude/`` or ``.agents/``" is a sentence both
copies want to carry verbatim, and a directional rule would rewrite it on one
side only and report a phantom failure.  Since agent-neutral prose naming both
roots is the pattern this repo already reaches for (see
``tools/check_release_record_sync.py``'s ``SKILL_REFERENCE``), the
normalisation has to tolerate it.

The accepted cost: a copy that uses the *other* tree's spelling throughout —
``.agents/skills/review-pr/SKILL.md`` saying "Claude as reviewer" — normalises
to the same token and passes.  That is a cosmetic inversion in text addressed
to a specific harness, not the structural drift this guard exists to catch,
and it is bounded by the three rules above: substitution can only ever collapse
those spellings, never a difference in wording, ordering, or content.
``tests/test_check_skill_mirror_parity.py`` pins that claim.

Exit codes (per the ``tools/`` contract — NOT the public 0/1/2/3/4/5 surface
that ``forgelm/`` honours):

- ``0`` — every skill exists under both roots and every mirrored file matches
  after substitution.
- ``1`` — in strict mode: a skill is present under one root only, a file is
  present in one copy of a skill only, or a mirrored file's content differs.
  **Also ``1`` regardless of ``--strict`` when a skill root is missing
  entirely**: that is a broken invocation rather than drift to iterate on
  locally, and a guard that cannot read its inputs must never report success.
  Matches ``tools/check_release_record_sync.py``.

CI wiring: runs in ``.github/workflows/ci.yml``'s ``validate`` job with
``--strict``, and is listed in the ``CLAUDE.md`` / ``AGENTS.md`` /
``CONTRIBUTING.md`` self-review gauntlet.

Usage::

    python3 tools/check_skill_mirror_parity.py
    python3 tools/check_skill_mirror_parity.py --strict
    python3 tools/check_skill_mirror_parity.py --quiet
"""

from __future__ import annotations

import argparse
import difflib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent

#: The two mirrored skill trees.  Module-level so the test suite can retarget
#: them at a temporary tree to exercise the failure branches.
CLAUDE_SKILLS_ROOT = REPO_ROOT / ".claude" / "skills"
AGENTS_SKILLS_ROOT = REPO_ROOT / ".agents" / "skills"

#: The file every skill directory must contain.  Named explicitly (rather than
#: inferred) so a skill directory that lost its SKILL.md is reported as a
#: missing skill instead of quietly comparing an empty file inventory.
SKILL_ENTRYPOINT = "SKILL.md"


@dataclass(frozen=True)
class Substitution:
    """One legitimate per-tree spelling difference.

    ``canonical`` is an internal placeholder that appears in neither tree; both
    ``claude`` and ``agents`` spellings collapse to it before comparison.
    """

    canonical: str
    claude: str
    agents: str


#: The complete, closed allowlist — see the module docstring's table.  Order is
#: significant and deliberate: the longest, most specific spellings come first
#: so that ``CLAUDE.md`` is consumed as a whole before the bare agent name could
#: match part of it.  (``Claude`` is capitalised differently from ``CLAUDE``, so
#: the two do not actually collide today; the ordering keeps that a property of
#: the table rather than a coincidence of casing.)
SUBSTITUTIONS: Tuple[Substitution, ...] = (
    # Directory paths.  A ``.claude/``-rooted path is dead text in a checkout
    # driven from ``.agents/`` and vice versa, so each copy names its own root.
    Substitution(canonical="\x00SKILL_ROOT\x00", claude=".claude/", agents=".agents/"),
    # The root agent-instructions file, itself a mirrored pair.
    Substitution(canonical="\x00AGENT_DOC\x00", claude="CLAUDE.md", agents="AGENTS.md"),
    # The harness the copy is addressed to, in prose ("including Claude as
    # reviewer").  Bare name only — never a substring of the doc names above.
    Substitution(canonical="\x00AGENT_NAME\x00", claude="Claude", agents="Codex"),
)


@dataclass(frozen=True)
class SkillPair:
    """One skill name and its two directories (either may be absent)."""

    name: str
    claude_dir: Path
    agents_dir: Path

    @property
    def in_claude(self) -> bool:
        return self.claude_dir.is_dir()

    @property
    def in_agents(self) -> bool:
        return self.agents_dir.is_dir()


@dataclass(frozen=True)
class ContentDiff:
    """A mirrored file whose two copies differ after substitution."""

    skill: str
    relative: str
    claude_path: Path
    agents_path: Path
    diff: Tuple[str, ...]


@dataclass(frozen=True)
class InventoryDiff:
    """A skill whose two directories do not hold the same set of files."""

    skill: str
    claude_only: Tuple[str, ...]
    agents_only: Tuple[str, ...]


def _rel(path: Path) -> str:
    """Render ``path`` relative to the repo root, or absolutely if outside it.

    The roots above are module constants precisely so a test can point them at a
    temporary tree; ``Path.relative_to`` raises on such a path, and a guard that
    crashes while formatting its own failure message is worse than one that
    prints an absolute path.
    """
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def normalise(text: str, *, tree: str) -> str:
    """Collapse every allowlisted spelling in ``text`` to its canonical token.

    ``tree`` selects which side's spelling is *expected*, but both spellings are
    replaced regardless: prose that names the other tree on purpose (an
    agent-neutral "under ``.claude/`` or ``.agents/``" reference) must normalise
    identically in both copies.  See the module docstring for why this is a
    normalisation rather than a directional rewrite.

    The parameter is kept because it documents the caller's intent at every call
    site and keeps the signature ready for a future rule that genuinely needs to
    know which side it is looking at.
    """
    if tree not in ("claude", "agents"):  # pragma: no cover - programming error
        raise ValueError(f"tree must be 'claude' or 'agents', got {tree!r}")
    for substitution in SUBSTITUTIONS:
        # Longest spelling first within a rule, so a rule whose two spellings
        # overlap cannot leave a partial match behind.
        for spelling in sorted((substitution.claude, substitution.agents), key=len, reverse=True):
            text = text.replace(spelling, substitution.canonical)
    return text


def discover_skills() -> List[SkillPair]:
    """Return every skill name seen under either root, sorted, deduplicated."""
    names = set()
    for root in (CLAUDE_SKILLS_ROOT, AGENTS_SKILLS_ROOT):
        if not root.is_dir():
            continue
        names.update(child.name for child in root.iterdir() if child.is_dir())
    return [
        SkillPair(name=name, claude_dir=CLAUDE_SKILLS_ROOT / name, agents_dir=AGENTS_SKILLS_ROOT / name)
        for name in sorted(names)
    ]


def list_files(directory: Path) -> List[str]:
    """Return every file under ``directory`` as a sorted relative POSIX path.

    Whole-tree rather than ``SKILL.md``-only: a skill that grows a
    ``references/`` page added to one copy alone is the same silent one-copy
    edit, just one directory deeper.
    """
    if not directory.is_dir():
        return []
    return sorted(path.relative_to(directory).as_posix() for path in directory.rglob("*") if path.is_file())


def compare_file(claude_path: Path, agents_path: Path) -> Optional[Tuple[str, ...]]:
    """Return a unified diff of the two copies, or ``None`` when they match.

    Text is compared after :func:`normalise`.  A file that is not valid UTF-8
    (no skill ships one today, but nothing forbids it) falls back to a raw byte
    comparison — substitution is meaningless there, and silently skipping the
    file would be a hole in the guard.
    """
    claude_bytes = claude_path.read_bytes()
    agents_bytes = agents_path.read_bytes()
    try:
        claude_text = claude_bytes.decode("utf-8")
        agents_text = agents_bytes.decode("utf-8")
    except UnicodeDecodeError:
        if claude_bytes == agents_bytes:
            return None
        return (f"binary content differs ({len(claude_bytes)} vs {len(agents_bytes)} bytes)",)

    claude_norm = normalise(claude_text, tree="claude")
    agents_norm = normalise(agents_text, tree="agents")
    if claude_norm == agents_norm:
        return None

    # Diff the ORIGINAL text, not the normalised text: the operator has to edit
    # the real files, and a diff full of \x00SKILL_ROOT\x00 placeholders would
    # be unreadable.  Only the match/no-match decision uses normalised text.
    diff = difflib.unified_diff(
        claude_text.splitlines(),
        agents_text.splitlines(),
        fromfile=_rel(claude_path),
        tofile=_rel(agents_path),
        lineterm="",
        n=1,
    )
    return tuple(diff)


def find_missing_skills(pairs: Sequence[SkillPair]) -> List[SkillPair]:
    """Return skills that exist under exactly one root."""
    return [pair for pair in pairs if pair.in_claude != pair.in_agents]


def find_inventory_diffs(pairs: Sequence[SkillPair]) -> List[InventoryDiff]:
    """Return skills whose two directories hold different sets of files."""
    diffs: List[InventoryDiff] = []
    for pair in pairs:
        if not (pair.in_claude and pair.in_agents):
            continue
        claude_files = set(list_files(pair.claude_dir))
        agents_files = set(list_files(pair.agents_dir))
        if claude_files == agents_files:
            continue
        diffs.append(
            InventoryDiff(
                skill=pair.name,
                claude_only=tuple(sorted(claude_files - agents_files)),
                agents_only=tuple(sorted(agents_files - claude_files)),
            ),
        )
    return diffs


def find_missing_entrypoints(pairs: Sequence[SkillPair]) -> List[Path]:
    """Return skill directories that exist under both roots but lack SKILL.md."""
    missing: List[Path] = []
    for pair in pairs:
        if not (pair.in_claude and pair.in_agents):
            continue
        for directory in (pair.claude_dir, pair.agents_dir):
            if not (directory / SKILL_ENTRYPOINT).is_file():
                missing.append(directory / SKILL_ENTRYPOINT)
    return missing


def find_content_diffs(pairs: Sequence[SkillPair]) -> List[ContentDiff]:
    """Return every mirrored file whose copies differ after substitution."""
    diffs: List[ContentDiff] = []
    for pair in pairs:
        if not (pair.in_claude and pair.in_agents):
            continue
        shared = sorted(set(list_files(pair.claude_dir)) & set(list_files(pair.agents_dir)))
        for relative in shared:
            claude_path = pair.claude_dir / relative
            agents_path = pair.agents_dir / relative
            diff = compare_file(claude_path, agents_path)
            if diff is None:
                continue
            diffs.append(
                ContentDiff(
                    skill=pair.name,
                    relative=relative,
                    claude_path=claude_path,
                    agents_path=agents_path,
                    diff=diff,
                ),
            )
    return diffs


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Verify .claude/skills/<name>/ and .agents/skills/<name>/ stay mirrored: "
            "same skills, same files, identical content once the documented "
            ".claude/ <-> .agents/ substitution allowlist is applied."
        ),
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Strict mode: exit 1 on any one-sided skill, one-sided file or content "
            "difference.  Default (no flag) is advisory: report to stdout but exit 0 "
            "— useful for local iteration.  A missing skill root exits 1 either way."
        ),
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress success summary.")
    return parser


def _remediation() -> str:
    """The one-line instruction printed under every failure block."""
    return (
        "  The two skill trees are the same document for two harnesses — edit both copies "
        "in the same change.  Only these spellings may differ: "
        + ", ".join(f"{s.claude!r}/{s.agents!r}" for s in SUBSTITUTIONS)
        + "."
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_arg_parser().parse_args(argv)

    for root in (CLAUDE_SKILLS_ROOT, AGENTS_SKILLS_ROOT):
        if not root.is_dir():
            print(f"check_skill_mirror_parity: {root} not found.", file=sys.stderr)
            # Deliberately not advisory-gated: see the module docstring's
            # exit-code note.  Unreadable inputs are a broken invocation.
            return 1

    pairs = discover_skills()
    failed = False

    missing_skills = find_missing_skills(pairs)
    if missing_skills:
        failed = True
        print(f"FAIL: {len(missing_skills)} skill(s) exist under only one root.")
        for pair in missing_skills:
            present, absent = (
                (pair.claude_dir, pair.agents_dir) if pair.in_claude else (pair.agents_dir, pair.claude_dir)
            )
            print(f"  {pair.name}  —  present at {_rel(present)}/, absent at {_rel(absent)}/")
        print(_remediation())

    missing_entrypoints = find_missing_entrypoints(pairs)
    if missing_entrypoints:
        failed = True
        print(f"FAIL: {len(missing_entrypoints)} skill director(ies) have no {SKILL_ENTRYPOINT}.")
        for path in missing_entrypoints:
            print(f"  {_rel(path)}")
        print(f"  A skill directory without {SKILL_ENTRYPOINT} is not a skill; add it or remove the directory.")

    inventory_diffs = find_inventory_diffs(pairs)
    if inventory_diffs:
        failed = True
        print(f"FAIL: {len(inventory_diffs)} skill(s) hold different files under the two roots.")
        for diff in inventory_diffs:
            for relative in diff.claude_only:
                print(f"  {diff.skill}: {relative} exists under {_rel(CLAUDE_SKILLS_ROOT)}/ only")
            for relative in diff.agents_only:
                print(f"  {diff.skill}: {relative} exists under {_rel(AGENTS_SKILLS_ROOT)}/ only")
        print(_remediation())

    content_diffs = find_content_diffs(pairs)
    if content_diffs:
        failed = True
        print(f"FAIL: {len(content_diffs)} mirrored file(s) differ beyond the substitution allowlist.")
        for diff in content_diffs:
            print(f"  {diff.skill}/{diff.relative}:")
            for line in diff.diff:
                print(f"    {line}")
        print(_remediation())

    if failed:
        return 1 if args.strict else 0

    if not args.quiet:
        files = sum(len(list_files(pair.claude_dir)) for pair in pairs)
        print(
            f"OK: {len(pairs)} skill(s) / {files} file(s) are mirrored between "
            f"{_rel(CLAUDE_SKILLS_ROOT)}/ and {_rel(AGENTS_SKILLS_ROOT)}/."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
