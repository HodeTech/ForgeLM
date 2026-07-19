#!/usr/bin/env python3
"""CI guard — prose must not point at source files that no longer exist.

Why this exists
---------------
The v0.9.1 ``forgelm/safety.py`` -> ``forgelm/safety/`` package split moved
the file cleanly (an AST symbol diff and a 4,100-input differential fuzz
proved the runtime behaviour identical) and then shipped **39 dangling
``forgelm/safety.py`` references across 16 files** — in the exact commit
whose entire purpose was moving that file. Nothing noticed.

Nothing noticed because no guard could see them. ``check_anchor_resolution.py``
validates ``[text](href)`` Markdown links under ``docs/`` only; the dead
references lived in backticked inline paths (``` `forgelm/safety.py` ```),
in ``.claude/skills/`` and ``.agents/skills/`` checklists, in ``site/*.html``
marketing copy, in notebook JSON, and in the repository-structure tree inside
``CLAUDE.md`` — none of which are Markdown links, and most of which are not
under ``docs/``.

That is the fourth instance in one review cycle of a single pattern: a sweep
touches thirty surfaces and misses the two or three that matter. This guard
closes the class rather than the instance.

What this adds over ``check_anchor_resolution.py``
--------------------------------------------------
No overlap by construction — the two guards are disjoint on both axes:

===================  ==============================  ==========================
                     check_anchor_resolution.py      this guard
===================  ==============================  ==========================
Reference shape      ``[text](href)`` Markdown only  backticked paths, bare
                                                     prose paths, HTML text,
                                                     notebook JSON strings —
                                                     Markdown links EXCLUDED
Surfaces scanned     ``docs/**/*.md``                ``docs/``, ``site/``,
                                                     ``notebooks/``,
                                                     ``.claude/skills/``,
                                                     ``.agents/skills/``,
                                                     ``CLAUDE.md``,
                                                     ``AGENTS.md``,
                                                     ``CONTRIBUTING.md``,
                                                     ``README.md``
Target validated     any relative path + anchors     ``forgelm/``, ``tools/``,
                                                     ``tests/`` paths only
===================  ==============================  ==========================

Markdown ``[...](...)`` link targets are explicitly stripped before matching,
so a broken link is reported by exactly one guard and never both.

Scope: why only ``forgelm/``, ``tools/`` and ``tests/``
-------------------------------------------------------
The scope was chosen by measurement, not by taste. Matching every path under
every top-level directory produced **266 findings on a clean tree**; skipping
fenced blocks cut that to 118; restricting the roots to the three real source
trees cut it to **14 findings covering 6 unique paths, every one of which was
a genuine defect or a documented exemption below**. A guard that reports 118
mostly-legitimate paths gets disabled within a week, which is strictly worse
than no guard.

Deliberately NOT matched, with the reason each would be noise:

- ``configs/`` — 76 hits on a clean tree. These name the *reader's* config
  file (``configs/run.yaml``), which by design does not exist in this repo.
- ``docs/`` — Markdown links there are already the anchor guard's job, and
  the residue is placeholder prose (``docs/reference/foo.md``,
  ``docs/reference/X.md``) in the bilingual-docs skill.
- ``docs/marketing/`` and ``docs/analysis/`` — gitignored working memory;
  ``check_no_analysis_refs.py`` owns those and enforces the opposite rule.
- ``site/`` and ``notebooks/`` as *targets* — rarely cross-referenced, and
  self-references inside those trees are relative, not repo-rooted.

False-positive controls
-----------------------
Five layers, in order of application:

1. **Root restriction** — the path must start ``forgelm/``, ``tools/`` or
   ``tests/`` and end in a known source extension. ``forgelm/yourmodule.py``
   in an illustrative sentence is still caught, so it needs layer 4 or 5.
2. **Fenced-block skipping** — ``` ``` ``` and ``~~~`` blocks in Markdown are
   skipped entirely. A fence showing a user's own directory layout, a shell
   transcript, or a proposed-but-unbuilt tree is illustrative by construction.
3. **Record-surface exclusion** (``_RECORD_SURFACES``) — whole trees whose
   *genre* is "a statement about the past or the hypothetical future":
   ``docs/roadmap/`` (release records + promises about unbuilt files) and
   ``docs/design/`` (proposed layouts that may never be built). Editing those
   to match today's tree would rewrite history, which is the wrong fix.
4. **Line exemptions** (``_EXEMPT``) — per-file substrings for individual
   lines that legitimately name a path that no longer exists, each with a
   written justification. This is the narrow instrument; prefer it.
5. **Illustrative markers** — a line containing ``e.g.``, ``for example``,
   ``such as``, ``örneğin`` or ``hypothetical`` adjacent to the match is NOT
   auto-exempted. That was considered and rejected: it is trivially wide
   enough to hide a real regression behind a stray "e.g.". Use ``_EXEMPT``.

Run via::

    python3 tools/check_source_path_refs.py
    python3 tools/check_source_path_refs.py --strict   # exit 1 on drift
    python3 tools/check_source_path_refs.py --quiet    # silent on success

Exit codes (per ``tools/`` contract — NOT the public 0/1/2/3/4/5 surface
that ``forgelm/`` honours):

- ``0`` — every referenced source path resolves (or ``--strict`` absent).
- ``1`` — at least one dead source-path reference, or an unreadable file.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Source trees whose contents are real files in THIS repo, so a reference to
# a path under one of them is a checkable claim. See the module docstring for
# why configs/ and docs/ are excluded.
_SOURCE_ROOTS: tuple[str, ...] = ("forgelm", "tools", "tests")

# Extensions that mark a token as a file path rather than prose. A trailing
# slash (directory reference) is accepted separately by the pattern.
_SOURCE_EXTS: tuple[str, ...] = (
    "py",
    "yaml",
    "yml",
    "json",
    "jsonl",
    "ipynb",
    "toml",
    "txt",
    "sh",
    "cfg",
    "ini",
    "md",
)

# A repo-rooted source path. Construction notes:
#
# * The leading look-behind rejects a match that continues a longer path
#   (``docs/forgelm/x.py``) or an identifier (``my_tools/a.py``).
# * The body is a negated-free but bounded character class with a single
#   quantifier — no nested quantifiers, so no ReDoS surface
#   (docs/standards/regex.md rule 3).
# * The trailing look-ahead prevents a partial match against a longer name
#   (``tests/test_http.python``).
_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_./-])"
    r"((?:" + "|".join(_SOURCE_ROOTS) + r")/"
    r"[A-Za-z0-9_./-]{0,200}?"
    r"(?:\.(?:" + "|".join(_SOURCE_EXTS) + r")|/))"
    r"(?![A-Za-z0-9_-])"
)

# Markdown inline link — the anchor guard's exclusive territory. Stripped
# from each line before path matching so the two guards never both report
# the same defect.
_MD_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]*)\)")

# Fenced code block delimiters (``` or ~~~), possibly indented.
_FENCE_RE = re.compile(r"^\s*(?:```|~~~)")

# Whole trees excluded because their genre is historical record or unbuilt
# proposal. Each entry carries the reason it cannot be "fixed" by editing.
_RECORD_SURFACES: tuple[str, ...] = (
    # Release records ("v0.5.5 shipped forgelm/data_audit.py"), dated
    # decision entries, and phase files that promise files not yet built
    # (e.g. an optional future tools/check_webhook_event_vocabulary.py).
    # Retargeting these to today's layout would falsify the record.
    "docs/roadmap/",
    # Design specs describe a PROPOSED module layout at the time of writing;
    # several document splits that were reshaped or never executed.
    "docs/design/",
    # Gitignored working memory — absent from fresh clones, and
    # check_no_analysis_refs.py already forbids the public tree citing it.
    "docs/analysis/",
    "docs/marketing/",
)

# Individual lines that legitimately name a path which no longer exists.
# Format: ``{relative_path: frozenset_of_substrings_that_legitimise_it}``.
# A finding is suppressed when the offending line contains ANY substring.
# Keep substrings as specific as possible — a broad one hides regressions.
#
# Every entry MUST carry a written justification in the comment above it.
_EXEMPT: dict[str, frozenset[str]] = {
    # The add-trainer-feature checklist explains WHERE the CLI parser lives
    # by narrating the Phase 15 split that put it there: "Phase 15 split the
    # monolithic forgelm/cli.py into ...". The old path is the subject of a
    # historical sentence, and the same line already names the live target
    # (forgelm/cli/_parser.py). Retargeting it would destroy the explanation.
    ".claude/skills/add-trainer-feature/SKILL.md": frozenset({"Phase 15 split the monolithic"}),
    ".agents/skills/add-trainer-feature/SKILL.md": frozenset({"Phase 15 split the monolithic"}),
    # The JSON-envelope manual instructs the READER to create a test file
    # ("A test in tests/test_json_envelope_contract.py ... that pins the exact
    # set of top-level keys"). The path is prescriptive — a file the reader is
    # being told to write — not a pointer to something that should already
    # exist. Both language mirrors carry the same sentence.
    "docs/usermanuals/en/reference/json-output.md": frozenset({"test_json_envelope_contract.py"}),
    "docs/usermanuals/tr/reference/json-output.md": frozenset({"test_json_envelope_contract.py"}),
    # The agent-guidance gauntlet section explains WHY this guard exists by
    # narrating the incident: "The `forgelm/safety.py` -> `forgelm/safety/`
    # split moved the file cleanly and shipped 39 dangling references...".
    # The old path is the subject of a historical sentence and the same
    # sentence names the live replacement. Caught by the guard against its own
    # documentation on the commit that added it — the correct fix is this
    # exemption, not softening the pattern.
    "CLAUDE.md": frozenset({"split moved"}),
    "AGENTS.md": frozenset({"split moved"}),
    # NOTE: this guard's own module docstring names dead paths as worked
    # examples (forgelm/safety.py, configs/run.yaml) and needs NO exemption —
    # tools/*.py is code, not a scanned prose surface. An entry here for a
    # file _is_scanned() rejects would silence nothing while reading as though
    # it did; test_every_exempt_file_is_a_scanned_surface enforces that.
}

# Files scanned outside the directory globs below.
_TOP_LEVEL_SURFACES: frozenset[str] = frozenset({"CLAUDE.md", "AGENTS.md", "CONTRIBUTING.md", "README.md"})


@dataclass(frozen=True)
class DeadRef:
    """A prose reference to a source path that does not exist on disk."""

    source: str
    line: int
    path: str
    context: str


def _is_record_surface(rel: str) -> bool:
    """Return True iff *rel* lives in a historical-record / proposal tree."""
    return rel.startswith(_RECORD_SURFACES)


def _is_scanned(rel: str) -> bool:
    """Return True iff *rel* is a prose surface this guard validates."""
    if _is_record_surface(rel):
        return False
    if rel in _TOP_LEVEL_SURFACES:
        return True
    if rel.startswith(("docs/", ".claude/skills/", ".agents/skills/")) and rel.endswith(".md"):
        return True
    if rel.startswith("site/") and rel.endswith((".html", ".js")):
        return True
    return rel.startswith("notebooks/") and rel.endswith(".ipynb")


def _enumerate_surfaces(repo_root: Path) -> list[str]:
    """Return git-tracked prose surfaces, sorted for determinism.

    ``git ls-files`` (not ``Path.rglob``) is deliberate: it enumerates only
    tracked files, so gitignored working-memory trees that still exist on a
    maintainer's disk are skipped without an explicit exclude clause.
    """
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        # Outside a git checkout there is no tracked-file ground truth;
        # fall back to a filesystem walk over the same shapes.
        found = {
            p.relative_to(repo_root).as_posix()
            for pattern in (
                "*.md",
                "docs/**/*.md",
                ".claude/**/*.md",
                ".agents/**/*.md",
                "site/**/*.html",
                "site/**/*.js",
                "notebooks/**/*.ipynb",
            )
            for p in repo_root.glob(pattern)
            if p.is_file()
        }
        return sorted(rel for rel in found if _is_scanned(rel))
    return sorted(rel for rel in result.stdout.splitlines() if _is_scanned(rel))


def _strip_markdown_links(line: str) -> str:
    """Blank out ``[text](href)`` hrefs — the anchor guard owns those.

    The link TEXT is preserved (a backticked path used as link text is still
    a prose reference this guard should validate); only the href is removed.
    """
    return _MD_LINK_RE.sub(lambda m: m.group(0).replace(m.group(1), ""), line)


def _iter_prose_lines(rel: str, text: str) -> Iterable[tuple[int, str]]:
    """Yield ``(line_no, line)`` for prose lines, skipping fenced blocks.

    Fence tracking applies to Markdown only. Notebook ``.ipynb`` files are
    JSON: their cell sources are escaped strings on physical lines, so raw
    line scanning is correct and reported line numbers point at the real
    file line a maintainer opens.
    """
    track_fences = rel.endswith(".md")
    in_fence = False
    for line_no, line in enumerate(text.splitlines(), start=1):
        if track_fences and _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        yield line_no, line


def _check_file(repo_root: Path, rel: str) -> list[DeadRef]:
    """Return dead source-path references found in *rel*.

    Fail-closed: an unreadable or non-UTF-8 file is reported as a finding
    rather than skipped, so CI surfaces the problem instead of going green
    on input it never actually read.
    """
    path = repo_root / rel
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [DeadRef(rel, 0, "<unreadable>", f"{exc.__class__.__name__}: {exc}")]

    exempt = _EXEMPT.get(rel, frozenset())
    findings: list[DeadRef] = []
    for line_no, raw in _iter_prose_lines(rel, text):
        if any(needle in raw for needle in exempt):
            continue
        for match in _PATH_RE.finditer(_strip_markdown_links(raw)):
            candidate = match.group(1)
            if (repo_root / candidate).exists():
                continue
            findings.append(DeadRef(rel, line_no, candidate, raw.strip()[:160]))
    return findings


def _collect(repo_root: Path) -> list[DeadRef]:
    dead: list[DeadRef] = []
    for rel in _enumerate_surfaces(repo_root):
        dead.extend(_check_file(repo_root, rel))
    return dead


def _report(dead: Sequence[DeadRef], surface_count: int, strict: bool) -> int:
    print(f"{'FAIL' if strict else 'WARN'}: prose references to source paths that do not exist:")
    for ref in dead:
        print(f"  ✗ {ref.source}:{ref.line}  {ref.path}")
        print(f"      {ref.context}")
    print(
        f"\n{len(dead)} dead source-path reference(s) across {surface_count} prose surface(s).\n"
        "Fix: retarget each reference at the path that owns the thing being discussed\n"
        "today (after a module split, that is the specific submodule — not the package).\n"
        "If the reference is a statement about the PAST that must keep the old path, or a\n"
        "path the reader is being told to create, add the file + a distinguishing substring\n"
        "to ``_EXEMPT`` in tools/check_source_path_refs.py WITH a written justification.\n"
        "Do not weaken the pattern to make a real dangling reference disappear."
    )
    return 1 if strict else 0


def _build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate that source paths named in repo prose exist on disk.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=_REPO_ROOT,
        help="Repository root (default: parent of tools/).",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Strict mode: exit 1 on any dead reference. Default (no flag) is "
            "advisory: report to stdout but exit 0 — useful for local iteration. "
            "CI (ci.yml validate job) invokes this tool with --strict."
        ),
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress the OK summary on success.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_argparser().parse_args(argv)
    repo_root = args.repo_root.resolve()
    if not repo_root.is_dir():
        print(f"error: repo root not found: {repo_root}", file=sys.stderr)
        return 1

    surfaces = _enumerate_surfaces(repo_root)
    dead = _collect(repo_root)
    if dead:
        return _report(dead, len(surfaces), args.strict)

    if not args.quiet:
        print(f"OK: {len(surfaces)} prose surface(s) reference only source paths that exist.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
