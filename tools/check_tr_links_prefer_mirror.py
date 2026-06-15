#!/usr/bin/env python3
"""CI guard — Turkish docs must link to the TR mirror when one exists (F-P8-C-04).

``docs/standards/localization.md`` ("Structural mirror rule" / "What to do when
the EN doc changes") requires a ``*-tr.md`` page to route its in-prose
cross-references to the *Turkish* sibling — a Turkish operator following a
'Bkz.'/'See also' link must stay in Turkish, not silently land on the English
page. ``check_anchor_resolution.py`` only proves the link RESOLVES (both files
exist); ``check_bilingual_parity.py`` only diffs heading spines. Neither catches
a ``*-tr.md`` page that links ``audit_event_catalog.md`` when
``audit_event_catalog-tr.md`` exists one suffix away.

This guard walks every ``docs/**/*-tr.md``; for each relative ``.md`` link whose
target has an existing ``<stem>-tr.md`` mirror it fails, UNLESS the link is one
of the sanctioned carve-outs:

- The ``**Ayna:**`` / ``**Mirror:**`` backlink line, which intentionally points
  at the EN original it mirrors.
- A link target already ending in ``-tr.md`` (already a mirror link).
- Absolute ``https://`` URLs, ``#anchor``-only, and ``mailto:`` links.

Fix a finding by appending ``-tr`` to the link target stem (preserving the
translated link TEXT). Run with ``--fix`` to apply the mechanical rewrite.

Exit codes (per ``tools/`` contract — NOT the public 0/1/2/3/4 surface that
``forgelm/`` honours):

- ``0`` — every TR cross-link prefers an existing TR mirror.
- ``1`` — at least one TR page links the EN sibling despite a TR mirror.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import List, Tuple

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DOCS = _REPO_ROOT / "docs"

# Inline markdown links: ``](target)``. The target is
# captured up to the first closing paren; markdown does not allow an unescaped
# ``)`` inside a plain link target, so this is sufficient for our doc corpus.
_LINK_RE = re.compile(r"\]\(([^)]+)\)")

# A line carrying the mirror backlink marker intentionally points back at the
# EN original; never rewrite it. Matched case-insensitively on the bold marker.
_BACKLINK_MARKERS = ("**ayna:**", "**mirror:**")


def _is_backlink_line(line: str) -> bool:
    low = line.lower()
    return any(marker in low for marker in _BACKLINK_MARKERS)


def _mirror_for(tr_file: Path, target: str) -> Path | None:
    """Return the existing ``<stem>-tr.md`` mirror for *target* relative to
    *tr_file*, or ``None`` when the link is not a rewritable EN-sibling link.
    """
    if target.startswith(("http://", "https://", "#", "mailto:")):
        return None
    path_part = target.split("#", 1)[0]
    if not path_part.endswith(".md") or path_part.endswith("-tr.md"):
        return None
    linked = (tr_file.parent / path_part).resolve()
    mirror = linked.with_name(linked.stem + "-tr.md")
    return mirror if mirror.exists() else None


def _scan_file(tr_file: Path) -> List[Tuple[int, str]]:
    """Return ``(line_no, leaking_target)`` for each EN-sibling link with a
    TR mirror, skipping the ``**Ayna:**`` backlink line."""
    findings: List[Tuple[int, str]] = []
    text = tr_file.read_text(encoding="utf-8")
    for line_no, line in enumerate(text.splitlines(), start=1):
        if _is_backlink_line(line):
            continue
        for match in _LINK_RE.finditer(line):
            target = match.group(1).strip()
            if _mirror_for(tr_file, target) is not None:
                findings.append((line_no, target))
    return findings


def _rewrite_target(target: str) -> str:
    """Append ``-tr`` to the link-target stem, preserving any ``#anchor``."""
    path_part, sep, frag = target.partition("#")
    new_path = path_part[: -len(".md")] + "-tr.md"
    return new_path + sep + frag


def _fix_file(tr_file: Path) -> int:
    """Rewrite EN-sibling links to their TR mirror in place; return fix count."""
    lines = tr_file.read_text(encoding="utf-8").splitlines(keepends=True)
    fixes = 0
    for i, line in enumerate(lines):
        if _is_backlink_line(line):
            continue

        def _sub(match: re.Match) -> str:
            nonlocal fixes
            target = match.group(1).strip()
            if _mirror_for(tr_file, target) is None:
                return match.group(0)
            fixes += 1
            return "](" + _rewrite_target(target) + ")"

        lines[i] = _LINK_RE.sub(_sub, line)
    if fixes:
        tr_file.write_text("".join(lines), encoding="utf-8")
    return fixes


def _tr_files() -> List[Path]:
    return sorted(_DOCS.glob("**/*-tr.md"))


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true", help="alias of default (exit 1 on any leak)")
    parser.add_argument("--quiet", action="store_true", help="silent on success")
    parser.add_argument("--fix", action="store_true", help="rewrite EN-sibling links to the TR mirror in place")
    args = parser.parse_args(argv)

    if args.fix:
        total_fixed = 0
        for tr_file in _tr_files():
            n = _fix_file(tr_file)
            if n:
                print(f"  fixed {n} link(s) in {tr_file.relative_to(_REPO_ROOT)}")
                total_fixed += n
        print(f"Rewrote {total_fixed} EN-sibling link(s) to their TR mirror.")
        return 0

    total = 0
    for tr_file in _tr_files():
        findings = _scan_file(tr_file)
        if not findings:
            continue
        rel = tr_file.relative_to(_REPO_ROOT)
        for line_no, target in findings:
            print(f"  ✗ {rel}:{line_no}  links '{target}' but '{_rewrite_target(target)}' exists")
            total += 1
    if total:
        print(
            f"\n{total} Turkish cross-link(s) route to the English sibling despite an "
            "existing -tr.md mirror.\n"
            "Fix: append '-tr' to the link target stem (keep the translated link text), "
            "or run:\n    python3 tools/check_tr_links_prefer_mirror.py --fix\n"
            "The **Ayna:** backlink line is exempt. See docs/standards/localization.md "
            "('Structural mirror rule')."
        )
        return 1
    if not args.quiet:
        print(f"OK: {len(_tr_files())} Turkish doc(s) prefer their TR mirror for every cross-link.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
