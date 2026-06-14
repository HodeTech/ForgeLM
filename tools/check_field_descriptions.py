#!/usr/bin/env python3
"""Phase 16 — Pydantic ``description=`` CI guard.

Walks every Pydantic ``BaseModel`` subclass under ``forgelm/config.py``
and reports fields that lack a ``description=`` argument on their
``Field(...)`` declaration.  In ``--strict`` mode (used by CI), exits
with code 1 when any field is missing a description.

The check is AST-based rather than runtime-based so it does not import
``forgelm.config`` (which would pull Pydantic + every transitive
dependency).  An AST scan is deterministic, fast, and runs on every CI
build (see ``.github/workflows/ci.yml``).

This guard is the enforcement half of the schema↔reference discipline:
it makes a missing ``description=`` fail the build so that
``docs/reference/configuration.md`` (and its ``-tr.md`` mirror), which
are maintained by hand, always have authoritative field text to mirror.
There is no autogenerator — the docs are written and reviewed manually;
this guard guarantees the source text they depend on exists.

Usage:

    # Report missing descriptions; exit 0 either way (advisory).
    python tools/check_field_descriptions.py forgelm/config.py

    # CI gate: exit 1 on any missing description.
    python tools/check_field_descriptions.py --strict forgelm/config.py
"""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from typing import List, Optional, Sequence

# Pydantic field-only assignments we care about.  ``Field(...)`` may be
# the RHS directly (positional default + ``description=``) or wrapped
# in ``Optional[...]`` / annotated types — we only inspect the call
# itself.  Bare type annotations without a default are treated as
# "no description" for purposes of the migration audit.
_FIELD_CALL_NAMES: frozenset[str] = frozenset({"Field"})


@dataclass(frozen=True)
class MissingDescription:
    """One ``Field(...)`` declaration that lacks a ``description=``."""

    class_name: str
    field_name: str
    line: int


def _is_pydantic_model(class_node: ast.ClassDef) -> bool:
    """Return ``True`` when ``class_node`` inherits from BaseModel.

    Conservative: matches ``class Foo(BaseModel)``, ``class Foo(pydantic.BaseModel)``,
    and any class whose bases textually include ``BaseModel`` as the
    rightmost identifier.  False positives here just mean we audit a
    couple of extra classes that probably aren't Pydantic models — the
    cost is negligible.
    """
    for base in class_node.bases:
        # Bare name, e.g. ``BaseModel``.
        if isinstance(base, ast.Name) and base.id == "BaseModel":
            return True
        # Attribute access, e.g. ``pydantic.BaseModel``.
        if isinstance(base, ast.Attribute) and base.attr == "BaseModel":
            return True
    return False


def _is_field_call(node: ast.AST) -> bool:
    """Return ``True`` when ``node`` is a ``Field(...)`` call."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name) and func.id in _FIELD_CALL_NAMES:
        return True
    if isinstance(func, ast.Attribute) and func.attr in _FIELD_CALL_NAMES:
        return True
    return False


def _has_description_kwarg(call: ast.Call) -> bool:
    """Return ``True`` when ``call.keywords`` contains ``description=``."""
    return any(kw.arg == "description" for kw in call.keywords)


def _annotation_has_described_field(annotation: ast.AST) -> bool:
    """Return ``True`` when ``annotation`` is ``Annotated[T, Field(..., description=...)]``.

    Pydantic v2 supports embedding ``Field(...)`` inside the type
    annotation via :class:`typing.Annotated` — a field declared as
    ``foo: Annotated[int, Field(default=8, description="...")]`` has
    ``stmt.value = None`` (no RHS default) but the description lives
    in the annotation.  Without recognising this form the scanner
    would false-flag a perfectly-valid Pydantic v2 idiom as
    "missing description".
    """
    if not isinstance(annotation, ast.Subscript):
        return False
    base = annotation.value
    base_name: Optional[str] = None
    if isinstance(base, ast.Name):
        base_name = base.id
    elif isinstance(base, ast.Attribute):
        base_name = base.attr
    if base_name != "Annotated":
        return False
    slice_node = annotation.slice
    # Python 3.9+: ast.Subscript.slice is the inner expression directly
    # (no wrapping ast.Index since 3.9).  For Annotated[T, X, Y, ...]
    # that expression is a Tuple of the type + metadata args.
    if isinstance(slice_node, ast.Tuple):
        elts = slice_node.elts
    else:
        elts = [slice_node]
    return any(_is_field_call(elt) and _has_description_kwarg(elt) for elt in elts)


def _scan_class(class_node: ast.ClassDef) -> List[MissingDescription]:
    """Walk a Pydantic class body; report fields whose Field() lacks description."""
    missing: List[MissingDescription] = []
    for stmt in class_node.body:
        result = _scan_field_stmt(class_node.name, stmt)
        if result is not None:
            missing.append(result)
    return missing


def _scan_field_stmt(class_name: str, stmt: ast.AST) -> Optional[MissingDescription]:
    """Inspect one class-body statement; return a missing-description record or None.

    Cognitive-complexity factor-out (SonarCloud S3776) of the per-stmt
    branch chain inside :func:`_scan_class`.  Returns ``None`` when the
    statement is irrelevant (non-AnnAssign, non-Name target, private,
    ``model_config``, or carries a description in the annotation /
    Field call); returns a populated :class:`MissingDescription` when
    the field is config-eligible but lacks a description.
    """
    if not isinstance(stmt, ast.AnnAssign):
        return None
    target = stmt.target
    if not isinstance(target, ast.Name):
        return None
    # Skip Pydantic's own machinery (``model_config``) and any
    # private attributes — those aren't config knobs.
    if target.id.startswith("_") or target.id == "model_config":
        return None
    # ``Annotated[T, Field(..., description=...)]`` (Pydantic v2):
    # description lives in the annotation, not the RHS.
    if _annotation_has_described_field(stmt.annotation):
        return None
    # Bare annotation (no default) → flag for the migration audit.
    if stmt.value is None:
        return MissingDescription(class_name, target.id, stmt.lineno)
    # ``foo: int = Field(...)`` form: report when the call lacks description=.
    if _is_field_call(stmt.value):
        if _has_description_kwarg(stmt.value):
            return None
        return MissingDescription(class_name, target.id, stmt.lineno)
    # RHS is a literal default (e.g. ``r: int = 8``); no Field(...) to
    # inspect, so by construction there's no description.
    return MissingDescription(class_name, target.id, stmt.lineno)


def scan_file(path: str) -> List[MissingDescription]:
    """Parse ``path`` and return every Pydantic field missing a description."""
    with open(path, "r", encoding="utf-8") as fh:
        source = fh.read()
    tree = ast.parse(source, filename=path)
    missing: List[MissingDescription] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and _is_pydantic_model(node):
            missing.extend(_scan_class(node))
    return missing


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Phase 16 — verify every Pydantic field carries a description=.",
    )
    parser.add_argument(
        "paths",
        nargs="+",
        help="One or more Python files to scan (typically `forgelm/config.py`).",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 when any field is missing a description (CI gate).",
    )
    args = parser.parse_args(argv)

    total_missing: List[MissingDescription] = []
    for path in args.paths:
        total_missing.extend(scan_file(path))

    if not total_missing:
        print("OK: every Pydantic field carries a description=.")
        return 0

    print(f"Found {len(total_missing)} field(s) missing description:")
    for m in total_missing:
        print(f"  {m.class_name}.{m.field_name}  (line {m.line})")
    if args.strict:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
