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

Out of scope: models built at runtime via ``pydantic.create_model()``
are invisible to any ClassDef-based AST walk.  ``forgelm/config.py``
declares its schema with class statements (no ``create_model``); if that
ever changes, this guard will not see the dynamically-built fields.

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


def _base_names(class_node: ast.ClassDef) -> List[str]:
    """Return the rightmost identifier of each base of ``class_node``.

    ``class Foo(BaseModel)`` → ``["BaseModel"]``;
    ``class Foo(pydantic.BaseModel)`` → ``["BaseModel"]``;
    ``class Leaf(ForgeBase)`` → ``["ForgeBase"]``.
    """
    names: List[str] = []
    for base in class_node.bases:
        if isinstance(base, ast.Name):
            names.append(base.id)
        elif isinstance(base, ast.Attribute):
            names.append(base.attr)
    return names


def _directly_inherits_base_model(class_node: ast.ClassDef) -> bool:
    """Return ``True`` when a *direct* base is spelled ``BaseModel``/``*.BaseModel``."""
    return "BaseModel" in _base_names(class_node)


def _pydantic_class_names(tree: ast.Module) -> "set[str]":
    """Collect every class in ``tree`` that reaches ``BaseModel`` transitively.

    F-P1-FAB-29: the original per-class check only matched a *direct*
    ``BaseModel`` base, so a shared intermediate base
    (``class ForgeBase(BaseModel): ...`` then ``class Leaf(ForgeBase): ...``)
    silently exempted ``Leaf`` from the description audit.  We now run a
    fixed-point pass over all ClassDefs in the file: a class is Pydantic
    if a direct base is ``BaseModel`` *or* a base name is the name of a
    class already known to be Pydantic in this file.  Pure-AST, no import.
    """
    class_defs = [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
    pydantic: set[str] = set()
    changed = True
    while changed:
        changed = False
        for node in class_defs:
            if node.name in pydantic:
                continue
            if _directly_inherits_base_model(node) or any(name in pydantic for name in _base_names(node)):
                pydantic.add(node.name)
                changed = True
    return pydantic


def _import_aliases(tree: ast.Module) -> "tuple[frozenset[str], frozenset[str]]":
    """Resolve local aliases for ``Field`` and ``Annotated``.

    ``from pydantic import Field as F`` / ``from typing import Annotated as Ann``
    are valid idioms; without resolving the alias the scanner false-flags
    ``F(default=8, description=...)`` and ``Ann[int, Field(...)]`` as missing
    descriptions (F-P1-FAB-40, fail-closed).  Returns ``(field_names,
    annotated_names)`` including the canonical spellings.
    """
    field_names = set(_FIELD_CALL_NAMES)
    annotated_names = {"Annotated"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        for alias in node.names:
            if alias.name == "Field":
                field_names.add(alias.asname or alias.name)
            elif alias.name == "Annotated":
                annotated_names.add(alias.asname or alias.name)
    return frozenset(field_names), frozenset(annotated_names)


def _is_field_call(node: ast.AST, field_names: "frozenset[str]" = _FIELD_CALL_NAMES) -> bool:
    """Return ``True`` when ``node`` is a ``Field(...)`` call (or an alias of it)."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name) and func.id in field_names:
        return True
    if isinstance(func, ast.Attribute) and func.attr in field_names:
        return True
    return False


def _is_classvar_annotation(annotation: ast.AST) -> bool:
    """Return ``True`` when ``annotation`` is ``ClassVar`` / ``ClassVar[...]``.

    ``ClassVar`` declares a class-level constant, not a Pydantic field, so it
    must not be audited for a ``description=`` (F-P1-FAB-40).
    """
    base: ast.AST = annotation.value if isinstance(annotation, ast.Subscript) else annotation
    if isinstance(base, ast.Name):
        return base.id == "ClassVar"
    if isinstance(base, ast.Attribute):
        return base.attr == "ClassVar"
    return False


def _has_description_kwarg(call: ast.Call) -> bool:
    """Return ``True`` when ``call.keywords`` contains ``description=``."""
    return any(kw.arg == "description" for kw in call.keywords)


def _annotation_has_described_field(
    annotation: ast.AST,
    field_names: "frozenset[str]" = _FIELD_CALL_NAMES,
    annotated_names: "frozenset[str]" = frozenset({"Annotated"}),
) -> bool:
    """Return ``True`` when ``annotation`` is ``Annotated[T, Field(..., description=...)]``.

    Pydantic v2 supports embedding ``Field(...)`` inside the type
    annotation via :class:`typing.Annotated` — a field declared as
    ``foo: Annotated[int, Field(default=8, description="...")]`` has
    ``stmt.value = None`` (no RHS default) but the description lives
    in the annotation.  Without recognising this form the scanner
    would false-flag a perfectly-valid Pydantic v2 idiom as
    "missing description".  ``field_names``/``annotated_names`` carry
    resolved import aliases (F-P1-FAB-40).
    """
    if not isinstance(annotation, ast.Subscript):
        return False
    base = annotation.value
    base_name: Optional[str] = None
    if isinstance(base, ast.Name):
        base_name = base.id
    elif isinstance(base, ast.Attribute):
        base_name = base.attr
    if base_name not in annotated_names:
        return False
    slice_node = annotation.slice
    # Python 3.9+: ast.Subscript.slice is the inner expression directly
    # (no wrapping ast.Index since 3.9).  For Annotated[T, X, Y, ...]
    # that expression is a Tuple of the type + metadata args.
    if isinstance(slice_node, ast.Tuple):
        elts = slice_node.elts
    else:
        elts = [slice_node]
    return any(_is_field_call(elt, field_names) and _has_description_kwarg(elt) for elt in elts)


def _scan_body(
    class_name: str,
    body: Sequence[ast.AST],
    field_names: "frozenset[str]",
    annotated_names: "frozenset[str]",
) -> List[MissingDescription]:
    """Scan a sequence of statements, descending into conditional blocks.

    F-P1-FAB-39: a field declared under ``if sys.version_info >= (3, 10):``
    or inside a ``try:`` is still a real runtime field, so the audit must
    look inside ``ast.If`` / ``ast.Try`` bodies (and their ``orelse`` /
    handler bodies) rather than only direct class-body statements.
    """
    missing: List[MissingDescription] = []
    for stmt in body:
        if isinstance(stmt, ast.If):
            missing.extend(_scan_body(class_name, stmt.body, field_names, annotated_names))
            missing.extend(_scan_body(class_name, stmt.orelse, field_names, annotated_names))
            continue
        if isinstance(stmt, ast.Try):
            missing.extend(_scan_body(class_name, stmt.body, field_names, annotated_names))
            for handler in stmt.handlers:
                missing.extend(_scan_body(class_name, handler.body, field_names, annotated_names))
            missing.extend(_scan_body(class_name, stmt.orelse, field_names, annotated_names))
            missing.extend(_scan_body(class_name, stmt.finalbody, field_names, annotated_names))
            continue
        result = _scan_field_stmt(class_name, stmt, field_names, annotated_names)
        if result is not None:
            missing.append(result)
    return missing


def _scan_class(
    class_node: ast.ClassDef,
    field_names: "frozenset[str]" = _FIELD_CALL_NAMES,
    annotated_names: "frozenset[str]" = frozenset({"Annotated"}),
) -> List[MissingDescription]:
    """Walk a Pydantic class body; report fields whose Field() lacks description."""
    return _scan_body(class_node.name, class_node.body, field_names, annotated_names)


def _scan_field_stmt(
    class_name: str,
    stmt: ast.AST,
    field_names: "frozenset[str]" = _FIELD_CALL_NAMES,
    annotated_names: "frozenset[str]" = frozenset({"Annotated"}),
) -> Optional[MissingDescription]:
    """Inspect one class-body statement; return a missing-description record or None.

    Cognitive-complexity factor-out (SonarCloud S3776) of the per-stmt
    branch chain inside :func:`_scan_class`.  Returns ``None`` when the
    statement is irrelevant (non-AnnAssign, non-Name target, private,
    ``model_config``, ``ClassVar`` constant, or carries a description in
    the annotation / Field call); returns a populated
    :class:`MissingDescription` when the field is config-eligible but
    lacks a description.
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
    # ``ClassVar[...]`` is a class-level constant, not a Pydantic field
    # (F-P1-FAB-40) — never audit it for a description.
    if _is_classvar_annotation(stmt.annotation):
        return None
    # ``Annotated[T, Field(..., description=...)]`` (Pydantic v2):
    # description lives in the annotation, not the RHS.
    if _annotation_has_described_field(stmt.annotation, field_names, annotated_names):
        return None
    # Bare annotation (no default) → flag for the migration audit.
    if stmt.value is None:
        return MissingDescription(class_name, target.id, stmt.lineno)
    # ``foo: int = Field(...)`` form: report when the call lacks description=.
    if _is_field_call(stmt.value, field_names):
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
    pydantic_classes = _pydantic_class_names(tree)
    field_names, annotated_names = _import_aliases(tree)
    missing: List[MissingDescription] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name in pydantic_classes:
            missing.extend(_scan_class(node, field_names, annotated_names))
    return missing


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Phase 16 — verify every Pydantic field carries a description=.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        default=["forgelm/config.py"],
        help="One or more Python files to scan (default: forgelm/config.py).",
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
