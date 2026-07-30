#!/usr/bin/env python3
"""Every artefact that can carry a measurement must be written strictly.

CPython's ``json`` is permissive at both ends: ``json.dumps`` emits the bare
tokens ``NaN`` / ``Infinity`` (which RFC 8259 has no literal for, so ``jq``,
Go, Rust and ``JSON.parse`` all reject them), and ``json.loads`` accepts them
again — so a write-then-read-back test passes while the published artefact is
unreadable. ``forgelm._strict_json.dumps_strict`` sanitises and then verifies
with ``allow_nan=False``.

Phase 16 S2 converted four writers and declared the class closed. It was not:
its own review found ``compliance_report.json`` — an EU AI Act Art. 11 /
Annex IV artefact — still emitting bare ``NaN`` from the same metrics dict,
plus ``safety_results.json`` and ``safety_trend.jsonl``. Converting instances
does not close a class, so this guard names the modules that write
measurement-bearing artefacts and requires ``dumps_strict`` in each. A new
writer in one of them fails the build.

**Scope is deliberately narrow, and the exclusions are the interesting part.**

- Hash canonicalisation (``compute_config_hash``,
  ``compute_annex_iv_manifest_hash``, the audit HMAC body) is *excluded*:
  those serialisations define a digest, and changing their bytes would
  invalidate every hash already recorded in a shipped artefact. They are
  listed explicitly below rather than passed over silently.
- CLI error envelopes (``{"success": false, "error": "..."}``) carry only
  strings and are not scanned.
- ``forgelm/templates/`` is data, not code.

Exit codes follow the tools/ contract: 0 clean, 1 findings.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Modules whose file writes carry numbers a run measured. A `json.dump` /
# `json.dumps` here must go through `dumps_strict`.
_WRITER_MODULES = (
    "forgelm/compliance.py",
    "forgelm/benchmark.py",
    "forgelm/judge.py",
    "forgelm/trainer.py",
    "forgelm/webhook.py",
    "forgelm/safety/_results.py",
    "forgelm/data_audit/_orchestrator.py",
    "forgelm/cli/_pipeline.py",
    "forgelm/cli/_result.py",
)

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
    ("forgelm/compliance.py", "_manifest_json_default"): ("A serialisation hook, not a writer."),
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


def scan_file(rel_path: str) -> list[str]:
    path = _REPO_ROOT / rel_path
    if not path.exists():
        return [f"{rel_path}: listed in _WRITER_MODULES but does not exist"]

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    owner = _enclosing_functions(tree)
    findings: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr in ("dump", "dumps")):
            continue
        if not (isinstance(func.value, ast.Name) and func.value.id == "json"):
            continue
        enclosing = owner.get(node.lineno, "<module>")
        if (rel_path, enclosing) in _HASH_CANONICALISATION:
            continue
        findings.append(
            f"{rel_path}:{node.lineno}  json.{func.attr} inside {enclosing}() — use forgelm._strict_json.dumps_strict"
        )
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--strict", action="store_true", help="Accepted for gauntlet symmetry; always fatal.")
    parser.add_argument("--quiet", action="store_true", help="Suppress the success summary.")
    args = parser.parse_args(argv)

    findings: list[str] = []
    for rel_path in _WRITER_MODULES:
        findings.extend(scan_file(rel_path))

    # A stale exclusion is its own drift: it reads as a reviewed decision while
    # naming a function nobody can find.
    for (rel_path, func), reason in _HASH_CANONICALISATION.items():
        source = (_REPO_ROOT / rel_path).read_text(encoding="utf-8") if (_REPO_ROOT / rel_path).exists() else ""
        if f"def {func}" not in source:
            findings.append(f"{rel_path}: hash-canonicalisation exclusion names {func}(), which no longer exists")
        if not reason.strip():
            findings.append(f"{rel_path}:{func} exclusion carries no written reason")

    if findings:
        print("FAIL: measurement-bearing artefact written without strict JSON:")
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
            f"OK: {len(_WRITER_MODULES)} artefact-writing module(s) use strict JSON; "
            f"{len(_HASH_CANONICALISATION)} hash-canonicalisation site(s) exempt with reasons."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
