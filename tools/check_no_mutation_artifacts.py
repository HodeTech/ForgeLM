#!/usr/bin/env python3
"""Refuse mutation-testing scaffolding in shipped source.

Mutation testing is how this project proves a guard is real: disable it,
confirm a test goes red, restore. The technique is sound and used
deliberately throughout Phase 16. The hazard is the restore step, and it is
not hypothetical — during S2 a review agent's `if False:` was left in a
shared working tree, swept into a commit by `git add -A`, and shipped at
HEAD with the full gauntlet green, because the branch it disabled had no
test. `ruff` does not flag `if False:` (it is legal Python), so nothing in
29 guards and 4,644 tests saw it.

This guard is the cheap structural answer: the patterns below have no
legitimate place in `forgelm/`, so their presence is by definition a
leftover. It does not replace test coverage — the real fix for that
incident was writing the missing test — it removes the class of accident
where scaffolding survives the session that created it.

`if TYPE_CHECKING:` and `if sys.platform` style guards are untouched: only
literal always-false / always-true conditions and explicit mutation markers
are matched.

Exit codes follow the tools/ contract: 0 clean, 1 findings.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCANNED = ("forgelm",)

# Explicit markers a mutation harness (or a human doing the same by hand)
# leaves behind. Matched as whole comment tokens so ordinary prose is safe.
_MARKER_COMMENTS = ("# MUTANT", "# MUTATION", "# mutation-test", "# TEMP-DISABLE")


def _constant_condition(node: ast.stmt) -> str | None:
    """Describe *node* when its test is a literal that disables or forces a branch.

    ``while True:`` is excluded and must stay excluded: it is the idiomatic
    Python infinite loop and appears fourteen times in this codebase in REPL
    read loops, re-prompt loops and a backwards file scan. Flagging it would
    make this guard noise, and a noisy guard is one people learn to skip —
    which is the failure mode it exists to prevent.

    What is left has no legitimate use in shipped source:

    - ``if False:`` / ``while False:`` — the block beneath is unreachable, so
      the code reads as live while doing nothing. This is the exact shape that
      shipped at HEAD during S2.
    - ``if True:`` — a condition was replaced by a constant, forcing the
      branch. The inverse mutation, and the same accident.
    """
    keyword = "while" if isinstance(node, ast.While) else "if"
    test = node.test
    if not (isinstance(test, ast.Constant) and isinstance(test.value, bool)):
        return None
    if keyword == "while" and test.value is True:
        return None
    return f"{keyword} {test.value}:"


def scan_file(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    findings: list[str] = []

    for lineno, line in enumerate(text.splitlines(), start=1):
        for marker in _MARKER_COMMENTS:
            if marker in line:
                findings.append(f"{path.relative_to(_REPO_ROOT)}:{lineno}  {marker} marker left in shipped source")

    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as exc:  # pragma: no cover - a syntax error is its own failure
        return findings + [f"{path.relative_to(_REPO_ROOT)}: could not parse ({exc})"]

    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.While)):
            described = _constant_condition(node)
            if described:
                findings.append(
                    f"{path.relative_to(_REPO_ROOT)}:{node.lineno}  `{described}` — a literal "
                    "always-false/true condition disables the block beneath it"
                )
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--strict", action="store_true", help="Accepted for gauntlet symmetry; this guard is always fatal."
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress the success summary.")
    parser.parse_args(argv)

    findings: list[str] = []
    scanned = 0
    for root in _SCANNED:
        for path in sorted((_REPO_ROOT / root).rglob("*.py")):
            scanned += 1
            findings.extend(scan_file(path))

    if findings:
        print("FAIL: mutation-testing scaffolding found in shipped source:")
        for finding in findings:
            print(f"  ✗ {finding}")
        print(
            "\nRestore the code the marker or literal condition disabled. If a branch is "
            "genuinely unreachable, delete it rather than switching it off — a disabled "
            "branch that still reads as live is how a dead guard survives review."
        )
        return 1

    if not parser.parse_args(argv).quiet:
        print(f"OK: {scanned} module(s) under {'/, '.join(_SCANNED)}/ carry no mutation scaffolding.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
