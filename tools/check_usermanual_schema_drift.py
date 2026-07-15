#!/usr/bin/env python3
"""User-manual YAML-example schema-drift guard.

The critical/high findings that motivated this guard were fabricated
config keys inside ``docs/usermanuals/`` YAML examples — a page showing
a ``training:`` or ``lora:`` fragment with a key that does not exist on
the live Pydantic schema.  ``tools/check_yaml_snippets.py`` already runs
real ``ForgeConfig(**data)`` validation, but only on snippets carrying
**all three** required top-level keys (``model`` + ``training`` +
``data``) — fragmentary single-block examples (the common case in a
user-manual page that is explaining one section at a time) are
intentionally skipped there because they cannot be validated as a
complete config.  This guard closes that gap for the fragment case,
without duplicating full-config validation.

Approach (AST-based, no import of ``forgelm.config`` — mirrors
``tools/check_field_descriptions.py``'s ClassDef walker so this stays a
fast, dependency-free lint-stage check):

1. Parse ``forgelm/config.py`` and build, for every Pydantic
   ``BaseModel`` subclass, a map of ``field_name -> FieldType`` where
   ``FieldType`` records whether the field resolves to another known
   Pydantic model (directly, through ``Optional[X]``, or through
   ``List[X]``) or is a leaf/opaque field (``str``, ``int``, ``Literal``,
   ``Dict[str, Any]``, ``List[str]``, ...).  Fields with a Pydantic
   ``alias=`` are indexed under both the field name and the alias (see
   ``TrainingConfig.grpo_max_completion_length``).
2. ``ForgeConfig``'s own field set is the set of top-level block names
   (``model``, ``lora``, ``training``, ``data``, ``auth``,
   ``evaluation``, ``webhook``, ``distributed``, ``merge``,
   ``compliance``, ``risk_assessment``, ``monitoring``, ``synthetic``,
   ``retention``, ``pipeline``) — derived from the schema, not
   hand-listed, so it never drifts from ``forgelm/config.py``.
3. Walk every fenced ``yaml`` block under ``docs/usermanuals/**/*.md``.
   Skip blocks that carry the full ``model``/``training``/``data``
   triplet (``check_yaml_snippets.py`` already validates those with a
   real ``ForgeConfig(**data)`` call — stronger than an AST key-path
   check and the single source of truth for full-config validation).
   For every remaining top-level key that matches a known ``ForgeConfig``
   block name, recursively walk the mapping and flag any key that is not
   a declared field (or alias) of the corresponding Pydantic model.
   Fields resolving to an opaque type (``Dict[str, Any]``, ``Literal``,
   scalars, ...) are leaves — their contents are never descended into,
   which is what keeps this guard from false-flagging genuinely
   free-form fields like ``MergeConfig.models: List[Dict[str, Any]]``.

Deliberately out of scope (would need real Pydantic validation, not an
AST key-path walk, to check safely): value type/range checks, `Literal`
choice validation, cross-field validators. This guard only answers
"does this key exist on the schema" — narrow, but exactly the class of
drift (fabricated field names) that motivated it.

Exit codes (per ``tools/`` contract — NOT the public 0/1/2/3/4 surface
that ``forgelm/`` honours):

- ``0`` — every checked key path resolves against the schema.
- ``1`` — at least one fabricated key path found (strict mode), or
  invalid arguments.

CI wiring: this guard runs in ``.github/workflows/ci.yml`` **without**
``--strict`` (report-only). At introduction it already found real
fabricated-key drift in ``docs/usermanuals/`` (e.g.
``deployment/model-merging.md``'s ``merge:`` block uses
``algorithm``/``base_model``/``output`` — none of which are
``MergeConfig`` fields; the real names are ``method``/``models``
(list of ``{path, weight}``)/``output_dir``) — a docs-content fix that
is out of scope for this tool. Flip to ``--strict`` once that drift is
cleaned up; don't infer a timeline from this comment, check the tool's
own advisory-mode output for the current live count.

Usage::

    python3 tools/check_usermanual_schema_drift.py
    python3 tools/check_usermanual_schema_drift.py --strict
    python3 tools/check_usermanual_schema_drift.py --quiet
"""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import yaml
except ImportError as exc:  # pragma: no cover — defensive
    print(f"check_usermanual_schema_drift: PyYAML not importable ({exc}); skipping.", file=sys.stderr)
    sys.exit(0)

# Reuse the sibling guards' AST plumbing / fence-extraction rather than
# re-implementing them — a single source of truth for "how do we find
# Pydantic classes" and "how do we find fenced yaml blocks".
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from check_field_descriptions import _import_aliases, _pydantic_class_names  # type: ignore
    from check_yaml_snippets import Snippet, extract_yaml_snippets  # type: ignore
except ImportError as exc:  # pragma: no cover — defensive
    print(f"check_usermanual_schema_drift: cannot import sibling guard internals ({exc}).", file=sys.stderr)
    sys.exit(1)


REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "forgelm" / "config.py"
USERMANUAL_ROOT = REPO_ROOT / "docs" / "usermanuals"

# Snippets carrying the full required triplet are already validated for
# real by check_yaml_snippets.py; re-checking their key paths here would
# duplicate that (stronger) check and could produce confusing double
# reports on the same line.
_FULL_CONFIG_KEYS = frozenset({"model", "training", "data"})


@dataclass(frozen=True)
class FieldType:
    """What a Pydantic field's annotation resolves to, for key-path walking."""

    nested_class: Optional[str]  # class name if this field is a (or a list of) known model
    is_list: bool  # True when the annotation is List[<nested_class>]


@dataclass(frozen=True)
class KeyDrift:
    """One key path in a usermanual YAML snippet that has no schema field."""

    path: Path
    line: int
    key_path: str


def _unwrap_subscript(node: ast.Subscript) -> Tuple[Optional[str], List[ast.AST]]:
    """Return ``(base_name, slice_elements)`` for a subscript annotation.

    ``Optional[X]`` / ``List[X]`` / ``Dict[K, V]`` / ``Union[X, Y]`` all
    parse as ``ast.Subscript``; the slice is a single element or (for
    multi-arg generics) an ``ast.Tuple``.
    """
    base = node.value
    base_name: Optional[str] = None
    if isinstance(base, ast.Name):
        base_name = base.id
    elif isinstance(base, ast.Attribute):
        base_name = base.attr
    slice_node = node.slice
    elts = slice_node.elts if isinstance(slice_node, ast.Tuple) else [slice_node]
    return base_name, elts


def _resolve_annotation(annotation: ast.AST, pydantic_classes: "set[str]") -> FieldType:
    """Resolve a field's type annotation to a :class:`FieldType`.

    Only descends through ``Optional[X]`` and ``List[X]`` — anything else
    (``Dict[...]``, ``Literal[...]``, bare scalars, ``Union`` with more
    than one non-``None`` member, forward refs to unknown classes) is
    treated as opaque (``nested_class=None``), which is the fail-safe
    direction: an opaque field is never descended into, so this can only
    under-report drift, never false-flag a legitimate free-form field.
    """
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        # Forward reference, e.g. ``pipeline: Optional["PipelineConfig"]``.
        name = annotation.value
        return FieldType(nested_class=name if name in pydantic_classes else None, is_list=False)
    if isinstance(annotation, ast.Name):
        return FieldType(nested_class=annotation.id if annotation.id in pydantic_classes else None, is_list=False)
    if isinstance(annotation, ast.Subscript):
        base_name, elts = _unwrap_subscript(annotation)
        if base_name == "Optional" and len(elts) == 1:
            return _resolve_annotation(elts[0], pydantic_classes)
        if base_name == "Union":
            non_none = [e for e in elts if not (isinstance(e, ast.Constant) and e.value is None)]
            if len(non_none) == 1:
                return _resolve_annotation(non_none[0], pydantic_classes)
            return FieldType(nested_class=None, is_list=False)
        if base_name in ("List", "list") and len(elts) == 1:
            inner = _resolve_annotation(elts[0], pydantic_classes)
            if inner.nested_class is not None:
                return FieldType(nested_class=inner.nested_class, is_list=True)
            return FieldType(nested_class=None, is_list=False)
        return FieldType(nested_class=None, is_list=False)
    return FieldType(nested_class=None, is_list=False)


def _field_alias(call: ast.Call) -> Optional[str]:
    """Return the ``Field(..., alias="...")`` string value, or None."""
    for kw in call.keywords:
        if kw.arg == "alias" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            return kw.value.value
    return None


def _scan_class_fields(
    class_node: ast.ClassDef,
    pydantic_classes: "set[str]",
    field_names: "frozenset[str]",
) -> Dict[str, FieldType]:
    """Return ``{field_name_or_alias: FieldType}`` for one Pydantic class body.

    Only direct ``AnnAssign`` statements in the class body are
    considered (matching ``forgelm/config.py``'s style — no
    conditionally-declared fields today); ``model_config`` and private/
    ``ClassVar`` attributes are skipped, mirroring
    ``check_field_descriptions.py``'s field-eligibility rules.
    """
    fields: Dict[str, FieldType] = {}
    for stmt in class_node.body:
        if not isinstance(stmt, ast.AnnAssign):
            continue
        target = stmt.target
        if not isinstance(target, ast.Name):
            continue
        if target.id.startswith("_") or target.id == "model_config":
            continue
        annotation = stmt.annotation
        if isinstance(annotation, ast.Subscript):
            base_name, _ = _unwrap_subscript(annotation)
            if base_name == "ClassVar":
                continue
        elif isinstance(annotation, ast.Name) and annotation.id == "ClassVar":
            continue
        field_type = _resolve_annotation(annotation, pydantic_classes)
        fields[target.id] = field_type
        if isinstance(stmt.value, ast.Call):
            func = stmt.value.func
            is_field_call = (isinstance(func, ast.Name) and func.id in field_names) or (
                isinstance(func, ast.Attribute) and func.attr in field_names
            )
            if is_field_call:
                alias = _field_alias(stmt.value)
                if alias:
                    fields[alias] = field_type
    return fields


def build_schema_map(config_path: Path) -> Dict[str, Dict[str, FieldType]]:
    """Parse ``config_path`` and return ``{class_name: {field: FieldType}}``."""
    source = config_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(config_path))
    pydantic_classes = _pydantic_class_names(tree)
    field_names, _ = _import_aliases(tree)
    schema: Dict[str, Dict[str, FieldType]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name in pydantic_classes:
            schema[node.name] = _scan_class_fields(node, pydantic_classes, field_names)
    return schema


def _walk_value(
    value: object,
    class_name: str,
    schema: Dict[str, Dict[str, FieldType]],
    path: str,
) -> List[str]:
    """Recursively validate ``value`` (a parsed YAML mapping) against ``class_name``.

    Returns a list of key-path strings that have no matching schema
    field.  Leaves (opaque fields) and non-dict values under a resolved
    model field are not descended into further — see module docstring.
    """
    if not isinstance(value, dict):
        return []
    fields = schema.get(class_name)
    if fields is None:
        return []
    drift: List[str] = []
    for key, sub_value in value.items():
        if not isinstance(key, str):
            continue
        field_type = fields.get(key)
        if field_type is None:
            drift.append(f"{path}.{key}")
            continue
        if field_type.nested_class is None:
            continue
        if field_type.is_list:
            if isinstance(sub_value, list):
                for idx, item in enumerate(sub_value):
                    drift.extend(_walk_value(item, field_type.nested_class, schema, f"{path}.{key}[{idx}]"))
        else:
            drift.extend(_walk_value(sub_value, field_type.nested_class, schema, f"{path}.{key}"))
    return drift


def check_snippet(
    snippet: "Snippet", schema: Dict[str, Dict[str, FieldType]], top_level: Dict[str, FieldType]
) -> List[KeyDrift]:
    """Check one fenced yaml snippet; return the key-path drift found."""
    try:
        parsed = yaml.safe_load(snippet.body)
    except yaml.YAMLError:
        return []  # not this guard's concern — check_yaml_snippets covers parse errors on full configs
    if not isinstance(parsed, dict):
        return []
    if _FULL_CONFIG_KEYS.issubset(parsed.keys()):
        return []  # fully covered by check_yaml_snippets.py's real Pydantic validation
    drifts: List[KeyDrift] = []
    for key, sub_value in parsed.items():
        if not isinstance(key, str):
            continue
        field_type = top_level.get(key)
        if field_type is None or field_type.nested_class is None:
            continue  # not a recognised ForgeConfig top-level block — out of scope
        for key_path in _walk_value(sub_value, field_type.nested_class, schema, key):
            drifts.append(KeyDrift(path=snippet.path, line=snippet.line_start, key_path=key_path))
    return drifts


def walk_usermanuals(root: Path) -> List[Path]:
    """Return every ``*.md`` under ``root`` (sorted, recursive)."""
    return [p for p in sorted(root.rglob("*.md")) if p.is_file()]


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Verify fenced yaml examples in docs/usermanuals/ only use keys that "
            "exist on the live forgelm.config.ForgeConfig schema (AST-derived, no import)."
        ),
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Strict mode: exit 1 on any drift finding.  Default (no flag) is "
            "advisory: report drift to stdout but exit 0 — useful for local "
            "iteration."
        ),
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress success summary.")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_arg_parser().parse_args(argv)

    if not CONFIG_PATH.is_file():
        print(f"check_usermanual_schema_drift: {CONFIG_PATH} not found.", file=sys.stderr)
        return 1
    if not USERMANUAL_ROOT.is_dir():
        print(f"check_usermanual_schema_drift: {USERMANUAL_ROOT} not found.", file=sys.stderr)
        return 1

    schema = build_schema_map(CONFIG_PATH)
    top_level = schema.get("ForgeConfig")
    if not top_level:
        print("check_usermanual_schema_drift: could not locate ForgeConfig in forgelm/config.py.", file=sys.stderr)
        return 1

    all_drift: List[KeyDrift] = []
    snippet_count = 0
    for md_path in walk_usermanuals(USERMANUAL_ROOT):
        for snippet in extract_yaml_snippets(md_path):
            snippet_count += 1
            drifts = check_snippet(snippet, schema, top_level)
            if drifts:
                all_drift.extend(drifts)

    if all_drift:
        print(f"FAIL: {len(all_drift)} fabricated schema key(s) found in docs/usermanuals/ YAML examples.")
        for d in all_drift:
            rel = d.path.relative_to(REPO_ROOT) if d.path.is_absolute() else d.path
            print(f"  {rel}:{d.line}  {d.key_path}  (no such field on the ForgeConfig schema)")
        if args.strict:
            return 1
        return 0

    if not args.quiet:
        print(
            f"OK: {snippet_count} yaml block(s) under docs/usermanuals/ checked; "
            f"every recognised ForgeConfig key path resolves against the schema."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
