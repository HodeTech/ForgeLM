#!/usr/bin/env python3
"""CLI ``--help`` exit-code prose guard (Step 3 follow-up, F-2).

Why this guard exists
---------------------

Step 3 introduced ``EXIT_INTEGRITY_FAILURE = 6`` for the four ``verify-*``
subcommands and swept ~30 doc pages, but ``forgelm/cli/_parser.py`` — the
single most authoritative place an operator reads the exit-code contract —
was never touched.  ``forgelm verify-audit --help`` kept telling CI
engineers that "1 means tampering" for a full release cycle while the
process actually exited 6, and ``--require-hmac`` kept promising "exit 1
if any line lacks an _hmac field" when that path exits 6.

``check_cli_help_consistency.py`` did not catch it: that guard validates
invocation *syntax* (does this flag exist?), never exit-code *prose*.  So
the claim rotted in the one surface with no mechanical reader.

What it asserts
---------------

One bit, in both directions, per subcommand:

1. **No hidden integrity code.** If a subcommand's dispatcher module can
   emit ``EXIT_INTEGRITY_FAILURE``, its ``--help`` prose must say so.
2. **No phantom integrity code.** If it cannot, the prose must not claim 6.

Why only code 6, and why only this shape
----------------------------------------

The obvious generalisation — "every exit code a dispatcher can emit must
appear in its help text" — was tried and rejected.  ``verify-audit`` can
emit 2 (unreadable log), and forcing that into a terse argparse
``description`` fights the house rule that help text is not
documentation; the per-subcommand module docstrings already carry the
full table.  The mirror generalisation — "every integer in help prose
must be emittable" — false-positives on incidental numerals ("4-byte
``GGUF`` magic header", "Annex IV §1-9"), and disambiguating those means
matching exit-code *context* in prose, i.e. exactly the fragile
prose-regex this repo's standards warn against.

Code 6 is the one worth a guard slot: it is the newest member of the
public contract, it is the one a security-relevant CI branch keys on, and
it is the one that just demonstrated it can rot silently.

**Both sides are derived from code — there is no mapping table to
maintain**, which is the property that stops this guard from merely
relocating the drift:

- *Can it emit 6?*  ``forgelm/cli/_dispatch.py``'s command→dispatcher dict
  is read by AST, the dispatcher function is located in
  ``forgelm/cli/subcommands/``, and that module is scanned for a
  ``EXIT_INTEGRITY_FAILURE`` reference.
- *Does it say 6?*  ``forgelm/cli/_parser.py`` is read by AST; for each
  ``subparsers.add_parser("<name>", ...)`` every ``description=`` and
  ``help=`` string literal in the enclosing registration function is
  collected.  Function docstrings are excluded — they are not what
  ``--help`` prints.

Exit codes (per ``tools/`` contract — NOT the public 0..6 surface that
``forgelm/`` honours):

- ``0`` — no drift, or advisory mode (default) with findings reported.
- ``1`` — at least one finding in ``--strict`` mode, or a structural
  failure that means the guard could not check anything.

Usage::

    python3 tools/check_cli_exit_code_prose.py
    python3 tools/check_cli_exit_code_prose.py --strict
    python3 tools/check_cli_exit_code_prose.py --quiet
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
PARSER_PATH = REPO_ROOT / "forgelm" / "cli" / "_parser.py"
DISPATCH_PATH = REPO_ROOT / "forgelm" / "cli" / "_dispatch.py"
SUBCOMMANDS_DIR = REPO_ROOT / "forgelm" / "cli" / "subcommands"

INTEGRITY_CONSTANT = "EXIT_INTEGRITY_FAILURE"
INTEGRITY_CODE = 6

# Two anchored alternatives covering the natural ways to state the claim:
#   "exit 6 if ...", "exits 6 when ...", "exit code ... 6 means ..."
#   "6 means tampering", "6 when any artifact was changed"
# Quantifiers are bounded (regex.md rule 3) and never compete for the same
# characters (rule 4); `[ \t]` is used instead of `\s` (rule 5).  The `[^.;]`
# body keeps the claim inside one sentence/clause so "exit 1 ... ; 6 ..." is
# not read as a single "exit 6" claim.
_CLAIMS_INTEGRITY_CODE = re.compile(
    r"(?:\bexits?\b(?:[ \t]+code)?[^.;]{0,40}?\b6\b)"
    r"|(?:\b6\b[ \t]+(?:means|when|on|if|for)\b)",
    re.IGNORECASE,
)


class GuardError(RuntimeError):
    """The guard could not establish one of its two derived inputs."""


# ---------------------------------------------------------------------------
# Side A — which subcommands can emit EXIT_INTEGRITY_FAILURE?
# ---------------------------------------------------------------------------


def _read_ast(path: Path) -> ast.Module:
    if not path.is_file():
        raise GuardError(f"{path.relative_to(REPO_ROOT)} not found")
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def extract_dispatch_table() -> Dict[str, str]:
    """Return ``{subcommand: dispatcher_function_name}`` from ``_dispatch.py``.

    Located structurally: the dict literal whose values are all string
    constants naming ``_run_*_cmd`` functions.  Reading the real routing
    table (rather than assuming ``verify-audit`` → ``_run_verify_audit_cmd``)
    means a subcommand that is renamed or re-pointed stays covered.
    """
    tree = _read_ast(DISPATCH_PATH)
    best: Dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        table: Dict[str, str] = {}
        for key, value in zip(node.keys, node.values):
            if (
                isinstance(key, ast.Constant)
                and isinstance(key.value, str)
                and isinstance(value, ast.Constant)
                and isinstance(value.value, str)
                and value.value.startswith("_run_")
                and value.value.endswith("_cmd")
            ):
                table[key.value] = value.value
            else:
                table = {}
                break
        if len(table) > len(best):
            best = table
    if not best:
        raise GuardError(
            f"no command->dispatcher dict found in {DISPATCH_PATH.relative_to(REPO_ROOT)}; "
            "the routing table moved or changed shape — update this guard."
        )
    return best


def _module_defining(function_name: str) -> Optional[Path]:
    """Return the subcommands module that defines ``function_name``."""
    for path in sorted(SUBCOMMANDS_DIR.glob("_*.py")):
        for node in ast.walk(_read_ast(path)):
            if isinstance(node, ast.FunctionDef) and node.name == function_name:
                return path
    return None


def commands_that_can_emit_integrity_code(table: Dict[str, str]) -> Set[str]:
    """Subcommands whose dispatcher module references ``EXIT_INTEGRITY_FAILURE``.

    Module-level rather than function-level: the routing frequently lives in
    a helper (``_verify_audit.py``'s probe, ``_verify_annex_iv.py``'s
    ``_classify_pipeline_result``) that the dispatcher calls, and a
    per-function scan would miss those and under-report.
    """
    emitters: Set[str] = set()
    for command, dispatcher in sorted(table.items()):
        module = _module_defining(dispatcher)
        if module is None:
            # A dispatcher the routing table names but no module defines is a
            # real defect (the CLI would AttributeError at runtime), but it is
            # not this guard's contract to diagnose — stay silent and let the
            # CLI smoke tests own it.
            continue
        names = {node.id for node in ast.walk(_read_ast(module)) if isinstance(node, ast.Name)} | {
            alias.asname or alias.name
            for node in ast.walk(_read_ast(module))
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        if INTEGRITY_CONSTANT in names:
            emitters.add(command)
    return emitters


# ---------------------------------------------------------------------------
# Side B — what does each subcommand's --help prose claim?
# ---------------------------------------------------------------------------


def _string_kwarg(call: ast.Call, name: str) -> Optional[str]:
    for keyword in call.keywords:
        if keyword.arg == name and isinstance(keyword.value, ast.Constant):
            if isinstance(keyword.value.value, str):
                return keyword.value.value
    return None


def extract_help_prose() -> Dict[str, str]:
    """Return ``{subcommand: operator-visible help prose}`` from ``_parser.py``.

    For each registration function containing a
    ``subparsers.add_parser("<name>", ...)`` call, concatenates every
    ``description=`` and ``help=`` string literal in that function — i.e.
    the subcommand blurb, its description paragraph, and every flag's help
    line.  Function docstrings are deliberately excluded: they document
    maintainers, ``--help`` documents operators, and only the latter is the
    contract surface an operator reads.
    """
    tree = _read_ast(PARSER_PATH)
    prose: Dict[str, str] = {}
    for func in ast.walk(tree):
        if not isinstance(func, ast.FunctionDef):
            continue
        name: Optional[str] = None
        chunks: List[str] = []
        for node in ast.walk(func):
            if not isinstance(node, ast.Call):
                continue
            attr = node.func
            if isinstance(attr, ast.Attribute) and attr.attr == "add_parser":
                if node.args and isinstance(node.args[0], ast.Constant):
                    if isinstance(node.args[0].value, str):
                        name = node.args[0].value
            for kwarg in ("description", "help"):
                text = _string_kwarg(node, kwarg)
                if text:
                    chunks.append(text)
        if name is not None:
            prose[name] = "\n".join(chunks)
    if not prose:
        raise GuardError(
            f"no add_parser() registrations found in {PARSER_PATH.relative_to(REPO_ROOT)}; "
            "the parser layout changed — update this guard."
        )
    return prose


def claims_integrity_code(text: str) -> bool:
    return bool(_CLAIMS_INTEGRITY_CODE.search(text))


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


def collect_findings() -> Tuple[List[str], int]:
    """Return ``(findings, subcommands_checked)``."""
    table = extract_dispatch_table()
    emitters = commands_that_can_emit_integrity_code(table)
    prose = extract_help_prose()

    findings: List[str] = []
    checked = 0
    for command in sorted(prose):
        if command not in table:
            # Registered in the parser but not routed (training-mode aliases
            # and the like) — nothing to compare against.
            continue
        checked += 1
        says = claims_integrity_code(prose[command])
        can = command in emitters
        if can and not says:
            findings.append(
                f"  {command}: dispatcher can exit {INTEGRITY_CODE} "
                f"({INTEGRITY_CONSTANT}) but --help never says so.\n"
                f"      Operators read --help as the exit-code contract; a missing 6 "
                f"reads as 'this can only exit 0 or 1'.\n"
                f"      Add a clause of the form 'exits 6 when ...' or "
                f"'6 when ...' to the subcommand description or a flag's help."
            )
        elif says and not can:
            findings.append(
                f"  {command}: --help claims exit {INTEGRITY_CODE} but its dispatcher "
                f"module never references {INTEGRITY_CONSTANT}.\n"
                f"      Either the claim is stale, or the routing regressed to "
                f"EXIT_CONFIG_ERROR — check forgelm/cli/subcommands/ before "
                f"editing the help text."
            )
    return findings, checked


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Verify that exit-code-6 claims in forgelm/cli/_parser.py help strings "
            "agree with which dispatchers can actually emit EXIT_INTEGRITY_FAILURE."
        ),
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Strict mode: exit 1 on any finding.  Default (no flag) is "
            "advisory: report to stdout but exit 0 — useful for local iteration."
        ),
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress success summary.")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_arg_parser().parse_args(argv)

    try:
        findings, checked = collect_findings()
    except GuardError as exc:
        # A structural failure is never advisory: the guard checked nothing,
        # and reporting success would be a silent failure.
        print(f"check_cli_exit_code_prose: {exc}", file=sys.stderr)
        return 1

    if findings:
        print(f"FAIL: {len(findings)} exit-code-{INTEGRITY_CODE} help-text drift finding(s).")
        for finding in findings:
            print(finding)
        return 1 if args.strict else 0

    if not args.quiet:
        print(
            f"OK: {checked} routed subcommand(s) checked; every "
            f"{INTEGRITY_CONSTANT} emitter documents exit {INTEGRITY_CODE} in "
            "--help, and no non-emitter claims it."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
