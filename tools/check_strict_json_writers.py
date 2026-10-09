#!/usr/bin/env python3
"""Every JSON ForgeLM serialises must go through ``dumps_strict``.

CPython's ``json`` is permissive at both ends: ``json.dumps`` emits the bare
tokens ``NaN`` / ``Infinity`` (which RFC 8259 has no literal for, so ``jq``,
Go, Rust and ``JSON.parse`` all reject them), and ``json.loads`` accepts them
again — so a write-then-read-back test passes while the published artefact is
unreadable. ``forgelm._strict_json.dumps_strict`` sanitises and then verifies
with ``allow_nan=False``.

Phase 16 S2 converted four writers and declared the class closed. It was not:
its own review found ``compliance_report.json`` still emitting bare ``NaN``. The
guard that followed named nine modules by hand — and the next review found
``ForgeConfig.model_dump_json``, the ``--dry-run`` output, ``model_integrity.json``
and the safety-eval envelope outside the list, because a hand-picked list is the
same enumerate-the-instances mistake one level up.

So the scope is the package, not a list: **every** ``.py`` under ``forgelm/``,
discovered from the filesystem. A new module is covered the day it is created.
The check is also alias-aware — ``import json as j``, ``from json import dumps``
and the third-party encoders (``orjson``, ``ujson``, ``simplejson``,
``rapidjson``) all count — because matching the literal name ``json`` only
forbade one spelling of the mistake.

**The only exclusions are digest inputs**, listed below with a written reason
and checked for staleness in both directions (the function must exist, and the
exclusion must actually suppress a finding). Hash canonicalisation defines a
digest, and changing its bytes would invalidate every hash already recorded in
a shipped artefact. Strictness is deliberately not a reason to exclude: a
payload that "only carries strings" costs one dict walk to route through
``dumps_strict`` and removes the need to prove that, and to keep proving it.

Exit codes follow the tools/ contract: 0 clean, 1 findings.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Modules that may call a raw encoder because they *are* the chokepoint.
_CHOKEPOINT_MODULES = ("forgelm/_strict_json.py",)

# Third-party encoders with the same (or a different, equally undocumented) policy
# on non-finite floats. ``orjson`` silently writes ``null``. None is a ForgeLM
# dependency; importing one for output needs a decision, not a drive-by.
_ALTERNATE_ENCODERS = frozenset({"orjson", "ujson", "simplejson", "rapidjson"})

# A guard that scans nothing passes. The package has ~100 modules; a count far below
# that means the discovery root is wrong, not that the code is clean.
_MIN_MODULES_EXPECTED = 50

# (module, enclosing function) pairs where raw `json.dumps` is the correct
# call because its output IS a digest input. Each carries its reason.
_HASH_CANONICALISATION = {
    ("forgelm/compliance.py", "compute_config_hash"): (
        "Defines the config hash recorded in every manifest; changing the bytes "
        "would invalidate hashes in already-shipped artefacts."
    ),
    ("forgelm/compliance.py", "compute_annex_iv_manifest_hash"): (
        "Defines the Annex IV manifest digest that verify-annex-iv recomputes."
    ),
    ("forgelm/compliance.py", "_verify_hmac_for_entry"): (
        "Recomputes the per-line audit HMAC and must mirror the writer "
        "byte-for-byte. log_event sanitises BEFORE tagging, so the bytes hashed "
        "here already carry no non-finite tokens; routing this through "
        "dumps_strict would sanitise twice and could not change the result, but "
        "it would make the verifier's canonicalisation differ textually from "
        "the writer's — the exact drift this pairing exists to prevent."
    ),
}


def _enclosing_functions(tree: ast.AST) -> dict[int, str]:
    """Map every line number to the innermost function that contains it."""
    owner: dict[int, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(node, "end_lineno", node.lineno)
            for line in range(node.lineno, end + 1):
                owner[line] = node.name
    return owner


def _encoder_bindings(tree: ast.AST) -> tuple[set[str], set[str]]:
    """Names that reach a JSON encoder: ``(module aliases, directly-imported callables)``.

    ``import json`` / ``import json as j`` / ``import orjson`` bind a *module* name;
    ``from json import dumps [as d]`` binds the callable itself. Both must be seen, or
    the guard forbids exactly one spelling of the mistake.
    """
    modules: set[str] = set()
    callables: set[str] = set()
    encoders = {"json", *_ALTERNATE_ENCODERS}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in encoders:
                    modules.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module.split(".")[0] in encoders:
                for alias in node.names:
                    if alias.name in ("dump", "dumps"):
                        callables.add(alias.asname or alias.name)
    return modules, callables


def scan_file(rel_path: str, used_exclusions: set[tuple[str, str]] | None = None) -> list[str]:
    path = _REPO_ROOT / rel_path
    if not path.exists():
        return [f"{rel_path}: does not exist"]

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    owner = _enclosing_functions(tree)
    modules, callables = _encoder_bindings(tree)
    findings: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in ("dump", "dumps"):
            if not (isinstance(func.value, ast.Name) and func.value.id in modules):
                continue
            spelled = f"{func.value.id}.{func.attr}"
        elif isinstance(func, ast.Name) and func.id in callables:
            spelled = func.id
        else:
            continue
        enclosing = owner.get(node.lineno, "<module>")
        if (rel_path, enclosing) in _HASH_CANONICALISATION:
            if used_exclusions is not None:
                used_exclusions.add((rel_path, enclosing))
            continue
        findings.append(
            f"{rel_path}:{node.lineno}  {spelled} inside {enclosing}() — use forgelm._strict_json.dumps_strict"
        )
    return findings


def _package_modules() -> list[str]:
    """Every module under ``forgelm/``, from the filesystem, minus the chokepoint itself."""
    root = _REPO_ROOT / "forgelm"
    return sorted(
        rel
        for rel in (path.relative_to(_REPO_ROOT).as_posix() for path in root.rglob("*.py"))
        if rel not in _CHOKEPOINT_MODULES
    )


def _defines_function(rel_path: str, func: str) -> bool:
    path = _REPO_ROOT / rel_path
    if not path.exists():
        return False
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func for node in ast.walk(tree)
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--strict", action="store_true", help="Accepted for gauntlet symmetry; always fatal.")
    parser.add_argument("--quiet", action="store_true", help="Suppress the success summary.")
    args = parser.parse_args(argv)

    modules = _package_modules()
    findings: list[str] = []
    if len(modules) < _MIN_MODULES_EXPECTED:
        findings.append(
            f"only {len(modules)} module(s) discovered under forgelm/ (expected >= {_MIN_MODULES_EXPECTED}); "
            "a guard that scans nothing passes"
        )

    used: set[tuple[str, str]] = set()
    for rel_path in modules:
        findings.extend(scan_file(rel_path, used))

    # A stale exclusion is its own drift: it reads as a reviewed decision while
    # naming a function nobody can find, or one that no longer encodes anything.
    for (rel_path, func), reason in _HASH_CANONICALISATION.items():
        if not _defines_function(rel_path, func):
            findings.append(f"{rel_path}: hash-canonicalisation exclusion names {func}(), which no longer exists")
        elif (rel_path, func) not in used:
            findings.append(f"{rel_path}: exclusion for {func}() suppresses nothing — delete it")
        if not reason.strip():
            findings.append(f"{rel_path}:{func} exclusion carries no written reason")

    if findings:
        print("FAIL: JSON serialised without dumps_strict:")
        for finding in findings:
            print(f"  ✗ {finding}")
        print(
            "\njson.dumps emits the bare tokens NaN/Infinity, which RFC 8259 has no literal for — "
            "and json.loads accepts them again, so a read-back test cannot see it. Route the write "
            "through dumps_strict, or add a hash-canonicalisation exclusion with a written reason."
        )
        return 1

    if not args.quiet:
        print(
            f"OK: {len(modules)} module(s) under forgelm/ scanned, none serialises JSON outside dumps_strict; "
            f"{len(_HASH_CANONICALISATION)} hash-canonicalisation site(s) exempt with reasons."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
