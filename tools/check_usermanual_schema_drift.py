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

Approach: the schema is resolved by AST-parsing ``forgelm/config.py`` as
text (mirroring ``tools/check_field_descriptions.py``'s ClassDef walker)
rather than importing the Pydantic models, so schema resolution itself
pulls in no runtime dependencies. Note the YAML-fence extraction reuses
``tools/check_yaml_snippets.py``, so the guard still needs ``forgelm``
importable and exits non-zero if it is not.

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

A fragment may also carry a top-level key that is not a ``ForgeConfig``
block at all — ``chat:``, ``output:``, ``gpu_profiles:``.  Those used to
be skipped as "someone else's YAML", which let a reader copy a block that
Pydantic rejects with ``extra_forbidden`` while this guard stayed green.
They are now flagged too, unless the (page, key set) pair is listed with
a written reason in one of two tables below:

- ``_NON_FORGELM_YAML`` — the snippet really is another tool's YAML (a
  GitHub Actions workflow, a docker-compose file, a vLLM server config).
- ``_KNOWN_OPEN`` — the block is fabricated or not implemented and a
  tracked issue owns the fix.  The row is deleted in the same change that
  corrects the page.

Both tables are keyed by the page path relative to its language directory
plus the exact set of unknown top-level keys, so the English and Turkish
copies share one row and a line shift never invalidates it.  A row that
matches nothing (the snippet was fixed, moved or removed) is itself a
failure, so the tables cannot rot into a blanket allowlist.

Deliberately out of scope (would need real Pydantic validation, not an
AST key-path walk, to check safely): value type/range checks, `Literal`
choice validation, cross-field validators. This guard only answers
"does this key exist on the schema" — narrow, but exactly the class of
drift (fabricated field names) that motivated it.

Exit codes (per ``tools/`` contract — NOT the public 0/1/2/3/4 surface
that ``forgelm/`` honours):

- ``0`` — every checked key path resolves against the schema.
- ``1`` — at least one fabricated key path, unrecognised top-level block
  or stale exemption row found (strict mode), or no YAML block found at all.
  (Invalid command-line arguments exit ``2`` through ``argparse``.)

Known limits: a snippet ``yaml.safe_load_all`` cannot parse is skipped
(``check_yaml_snippets.py`` reports parse errors on full configs), and a
top-level key YAML 1.1 reads as a non-string (``on:``, ``yes:``, ``1:``) is
not checked — GitHub Actions' ``on:`` is exactly such a key.

CI wiring: this guard runs in ``.github/workflows/ci.yml`` with
``--strict`` — any fabricated key path fails the build. The
introduction-time backlog (38 instances across 8 EN/TR page-pairs,
e.g. ``deployment/model-merging.md``'s ``merge:`` block that used
``algorithm``/``base_model``/``output`` instead of the real
``MergeConfig`` names ``method``/``models``/``output_dir``) has been
cleaned, so the guard now protects against regressions rather than
merely reporting a standing backlog.

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
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

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

# (page relative to its language directory, exact set of unrecognised
# top-level keys) -> why the block is allowed to stay.
_PageKeys = Tuple[str, FrozenSet[str]]

# Snippets that are another tool's YAML, not a ForgeLM config.
_NON_FORGELM_YAML: Dict[_PageKeys, str] = {
    ("compliance/human-approval-gate.md", frozenset({"env", "steps"})): "GitHub Actions workflow excerpt",
    (
        "deployment/deploy-targets.md",
        frozenset(
            {
                "chat_template",
                "dtype",
                "gpu_memory_utilization",
                "max_model_len",
                "served_model_name",
                "tokenizer_mode",
                "trust_remote_code",
            }
        ),
    ): "vLLM engine config sample (tokenizer_mode / served_model_name / chat_template are not emitted by `forgelm deploy`: #435)",
    ("operations/cicd.md", frozenset({"jobs", "name"})): "GitHub Actions workflow",
    ("operations/cicd.md", frozenset({"audit", "deploy", "stages", "train"})): "GitLab CI pipeline",
    ("operations/cicd.md", frozenset({"concurrency"})): "GitHub Actions concurrency block",
    ("operations/docker.md", frozenset({"services", "version", "volumes"})): "docker-compose file",
    ("operations/supply-chain.md", frozenset({"ignores"})): "ignore file read by tools/check_pip_audit.py --ignores",
    ("reference/exit-codes.md", frozenset({"train"})): "GitLab CI job",
}

# Fabricated or unimplemented blocks that a tracked issue still owns.  Delete
# the row in the same change that corrects the page (a stale row fails).
_KNOWN_OPEN: Dict[_PageKeys, str] = {
    (
        "operations/troubleshooting.md",
        frozenset({"audit"}),
    ): "#425: documents an `audit.quality_filter.skip` block that ForgeConfig rejects",
    (
        "operations/gpu-cost.md",
        frozenset({"gpu_profiles"}),
    ): "#427: documents custom GPU profiles that do not exist",
}


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


@dataclass(frozen=True)
class UnknownBlock:
    """Top-level keys of a usermanual YAML fragment that are not ForgeConfig blocks."""

    path: Path
    line: int
    keys: FrozenSet[str]


@dataclass(frozen=True)
class StaleRow:
    """An exemption row that does not match a snippet in every language directory."""

    table: str
    page: str
    keys: FrozenSet[str]
    reason: str
    matched_in: Tuple[str, ...]
    missing_in: Tuple[str, ...]


@dataclass(frozen=True)
class ScanResult:
    """Everything one pass over a usermanual tree found."""

    drifts: List[KeyDrift]
    unknown_blocks: List[UnknownBlock]
    stale_rows: List[StaleRow]
    snippet_count: int

    @property
    def clean(self) -> bool:
        return not (self.drifts or self.unknown_blocks or self.stale_rows)


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


def _parse_fragments(snippet: "Snippet") -> List[dict]:
    """Return the fragment mappings of ``snippet`` that this guard checks.

    A snippet may hold several YAML documents (``---``); each mapping is
    returned. Unparseable YAML, non-mapping documents and full configs that
    ``check_yaml_snippets.py`` already validates with a real
    ``ForgeConfig(**data)`` are left out.
    """
    try:
        documents = list(yaml.safe_load_all(snippet.body))
    except yaml.YAMLError:
        return []
    return [doc for doc in documents if isinstance(doc, dict) and not _FULL_CONFIG_KEYS.issubset(doc.keys())]


def unknown_top_level_keys(snippet: "Snippet", top_level: Dict[str, FieldType]) -> FrozenSet[str]:
    """Return the string top-level keys of ``snippet`` that are not ForgeConfig blocks."""
    return frozenset(
        key for doc in _parse_fragments(snippet) for key in doc if isinstance(key, str) and key not in top_level
    )


def check_snippet(
    snippet: "Snippet", schema: Dict[str, Dict[str, FieldType]], top_level: Dict[str, FieldType]
) -> List[KeyDrift]:
    """Check one fenced yaml snippet; return the key-path drift found.

    Only the keys *inside* recognised ForgeConfig blocks are walked here; an
    unrecognised top-level block is :func:`unknown_top_level_keys`'s concern.
    """
    drifts: List[KeyDrift] = []
    for doc in _parse_fragments(snippet):
        for key, sub_value in doc.items():
            if not isinstance(key, str):
                continue
            field_type = top_level.get(key)
            if field_type is None or field_type.nested_class is None:
                continue  # unrecognised or opaque top-level block — see unknown_top_level_keys
            for key_path in _walk_value(sub_value, field_type.nested_class, schema, key):
                drifts.append(KeyDrift(path=snippet.path, line=snippet.line_start, key_path=key_path))
    return drifts


def walk_usermanuals(root: Path) -> List[Path]:
    """Return every ``*.md`` under ``root`` (sorted, recursive)."""
    return [p for p in sorted(root.rglob("*.md")) if p.is_file()]


def _language_and_page(root: Path, md_path: Path) -> Optional[Tuple[str, str]]:
    """Split ``<root>/<lang>/<section>/<page>.md`` into ``(lang, "<section>/<page>.md")``.

    A file directly under ``root`` belongs to no language directory and returns ``None``.
    """
    parts = md_path.relative_to(root).parts
    if len(parts) < 2:
        return None
    return parts[0], "/".join(parts[1:])


def scan(
    root: Path,
    schema: Dict[str, Dict[str, FieldType]],
    top_level: Dict[str, FieldType],
    non_forgelm: Dict[_PageKeys, str],
    known_open: Dict[_PageKeys, str],
) -> ScanResult:
    """Walk every fenced yaml block under ``root`` and classify what it contains."""
    drifts: List[KeyDrift] = []
    unknown: List[UnknownBlock] = []
    matched: Set[Tuple[str, str, FrozenSet[str]]] = set()
    languages: Set[str] = set()
    snippet_count = 0
    for md_path in walk_usermanuals(root):
        located = _language_and_page(root, md_path)
        if located is None:
            continue
        language, page = located
        languages.add(language)
        for snippet in extract_yaml_snippets(md_path):
            snippet_count += 1
            drifts.extend(check_snippet(snippet, schema, top_level))
            keys = unknown_top_level_keys(snippet, top_level)
            if not keys:
                continue
            if (page, keys) in non_forgelm or (page, keys) in known_open:
                matched.add((language, page, keys))
            else:
                unknown.append(UnknownBlock(path=snippet.path, line=snippet.line_start, keys=keys))
    stale: List[StaleRow] = []
    for table_name, table in (("_NON_FORGELM_YAML", non_forgelm), ("_KNOWN_OPEN", known_open)):
        for (page, keys), reason in table.items():
            present = tuple(lang for lang in sorted(languages) if (lang, page, keys) in matched)
            missing = tuple(lang for lang in sorted(languages) if (lang, page, keys) not in matched)
            if missing or not languages:
                stale.append(StaleRow(table_name, page, keys, reason, present, missing))
    return ScanResult(drifts=drifts, unknown_blocks=unknown, stale_rows=stale, snippet_count=snippet_count)


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

    result = scan(USERMANUAL_ROOT, schema, top_level, _NON_FORGELM_YAML, _KNOWN_OPEN)

    if result.snippet_count == 0:
        print(f"check_usermanual_schema_drift: no yaml blocks found under {USERMANUAL_ROOT}.", file=sys.stderr)
        return 1
    if not result.clean:
        _report(result)
        return 1 if args.strict else 0

    if not args.quiet:
        print(
            f"OK: {result.snippet_count} yaml block(s) under docs/usermanuals/ checked; "
            f"every recognised ForgeConfig key path resolves against the schema and every "
            f"other top-level block is another tool's YAML or a listed known-open block."
        )
    return 0


def _report(result: ScanResult) -> None:
    """Print every finding of a failed scan."""
    if result.drifts:
        print(f"FAIL: {len(result.drifts)} fabricated schema key(s) found in docs/usermanuals/ YAML examples.")
        for d in result.drifts:
            rel = d.path.relative_to(REPO_ROOT) if d.path.is_absolute() else d.path
            print(f"  {rel}:{d.line}  {d.key_path}  (no such field on the ForgeConfig schema)")
    if result.unknown_blocks:
        print(
            f"FAIL: {len(result.unknown_blocks)} YAML block(s) in docs/usermanuals/ use top-level keys ForgeConfig rejects."
        )
        for u in result.unknown_blocks:
            rel = u.path.relative_to(REPO_ROOT) if u.path.is_absolute() else u.path
            print(
                f"  {rel}:{u.line}  {', '.join(sorted(u.keys))}  (not a ForgeConfig top-level block: correct the "
                f"example, or list it in _NON_FORGELM_YAML with a reason if it is another tool's YAML, or in "
                f"_KNOWN_OPEN if a tracked issue owns the fix)"
            )
    if result.stale_rows:
        print(f"FAIL: {len(result.stale_rows)} exemption row(s) do not match a YAML block in every language.")
        for row in result.stale_rows:
            where = f"{row.table}: {row.page}  {', '.join(sorted(row.keys))}  ({row.reason})"
            if row.matched_in:
                print(
                    f"  {where} — unmatched in {', '.join(row.missing_in)} but matched in "
                    f"{', '.join(row.matched_in)}: the language copies diverged; fix the page, not the row"
                )
            else:
                print(f"  {where} — matches no snippet in any language: delete the row")


if __name__ == "__main__":
    sys.exit(main())
