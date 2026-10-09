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
conditions that are *statically* always-true / always-false and explicit
mutation markers are matched. "Statically" means folded from literals —
`False`, `0`, `1`, `None`, `""`, `not True`, `False and x` — not just the
spelling `False`, because a mutation harness that writes `if 0:` has done
precisely the same thing and the first version of this guard could not see it.

Exit codes follow the tools/ contract: 0 clean, 1 findings.
"""

from __future__ import annotations

import argparse
import ast
import io
import sys
import tokenize
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCANNED = ("forgelm",)
# The package has ~100 modules; far fewer means the scan root is wrong, not that the code is clean.
_MIN_MODULES_EXPECTED = 50

# Explicit markers a mutation harness (or a human doing the same by hand)
# leaves behind. Matched as whole comment tokens so ordinary prose is safe.
_MARKER_COMMENTS = ("# MUTANT", "# MUTATION", "# mutation-test", "# TEMP-DISABLE")


def _static_truth(node: ast.expr) -> bool | None:
    """The truth value of *node* if it folds from literals alone, else ``None``.

    Handles a constant, ``not <foldable>`` and ``and`` / ``or`` with Python's
    short-circuit semantics (``False and x`` is False whatever ``x`` is). Anything
    that reads a name, calls, or compares is not static and returns ``None``, which
    keeps ``if x:``, ``if TYPE_CHECKING:`` and ``if sys.platform == ...`` out of scope.
    """
    if isinstance(node, ast.Constant):
        return bool(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        inner = _static_truth(node.operand)
        return None if inner is None else not inner
    if isinstance(node, ast.BoolOp):
        values = [_static_truth(v) for v in node.values]
        if isinstance(node.op, ast.And):
            if any(v is False for v in values):
                return False
            return True if all(v is True for v in values) else None
        if any(v is True for v in values):
            return True
        return False if all(v is False for v in values) else None
    return None


def _constant_condition(node: ast.AST) -> str | None:
    """Describe *node* when its test statically disables or forces a branch.

    ``while <truthy>:`` is excluded and must stay excluded: ``while True:`` is the
    idiomatic Python infinite loop and appears fourteen times in this codebase in
    REPL read loops, re-prompt loops and a backwards file scan. Flagging it would
    make this guard noise, and a noisy guard is one people learn to skip — which is
    the failure mode it exists to prevent.

    What is left has no legitimate use in shipped source:

    - a falsy `if` / `while` / ternary test (`False`, `0`, `None`, `""`, `not True`):
      the block beneath is unreachable, so the code reads as live while doing
      nothing. This is the exact shape that shipped at HEAD during S2.
    - a truthy `if` / ternary test (`True`, `1`, `not False`): a condition was
      replaced by a constant, forcing the branch. The inverse mutation, and the
      same accident.
    """
    truth = _static_truth(node.test)  # type: ignore[attr-defined]
    if truth is None:
        return None
    if isinstance(node, ast.While):
        return "while <always-false>:" if truth is False else None
    keyword = "x if <cond> else y" if isinstance(node, ast.IfExp) else "if"
    return f"{keyword} [condition is always {truth}]"


def _marker_comments(text: str) -> list[tuple[int, str]]:
    """``(line, marker)`` for each marker found in a real comment token.

    Tokenising, rather than ``marker in line``, keeps a *string literal* that merely
    contains ``# MUTATION`` (a docstring, a help text, a test fixture embedded in
    source) from failing the build — a guard that cries wolf gets disabled.
    """
    found: list[tuple[int, str]] = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                found.extend((tok.start[0], marker) for marker in _MARKER_COMMENTS if marker in tok.string)
    except (tokenize.TokenError, IndentationError):  # pragma: no cover - the ast.parse below reports it
        pass
    return found


def scan_file(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    findings: list[str] = []

    for lineno, marker in _marker_comments(text):
        findings.append(f"{path.relative_to(_REPO_ROOT)}:{lineno}  {marker} marker left in shipped source")

    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as exc:  # pragma: no cover - a syntax error is its own failure
        return findings + [f"{path.relative_to(_REPO_ROOT)}: could not parse ({exc})"]

    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.While, ast.IfExp)):
            described = _constant_condition(node)
            if described:
                findings.append(
                    f"{path.relative_to(_REPO_ROOT)}:{node.lineno}  `{described}` — a literal "
                    "always-false/true condition disables or forces the branch beneath it"
                )
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--strict", action="store_true", help="Accepted for gauntlet symmetry; this guard is always fatal."
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress the success summary.")
    args = parser.parse_args(argv)

    findings: list[str] = []
    scanned = 0
    for root in _SCANNED:
        for path in sorted((_REPO_ROOT / root).rglob("*.py")):
            scanned += 1
            findings.extend(scan_file(path))
    if scanned < _MIN_MODULES_EXPECTED:
        findings.append(
            f"only {scanned} module(s) scanned (expected >= {_MIN_MODULES_EXPECTED}); a guard that scans nothing passes"
        )

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

    if not args.quiet:
        print(f"OK: {scanned} module(s) under {'/, '.join(_SCANNED)}/ carry no mutation scaffolding.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
