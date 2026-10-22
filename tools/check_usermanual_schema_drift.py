#!/usr/bin/env python3
"""User-manual YAML-example schema-drift guard.

A YAML example under ``docs/usermanuals/`` is something a reader copies into a
config, so every key in it must be one ``ForgeConfig`` accepts unless the
example is another tool's YAML.  ``tools/check_yaml_snippets.py`` validates a
full config (``model`` + ``training`` + ``data``) with a real
``ForgeConfig(**data)`` and skips everything else.  This guard covers the rest:
fragments that explain one block at a time, the block names of every example
(full configs included), and examples that neither schema guard can read.

The schema is read from ``forgelm/config.py``'s AST (mirroring
``tools/check_field_descriptions.py``'s class walker), so resolving it imports
nothing.  The fence extractor is ``tools/check_yaml_snippets.py``'s, which
imports ``forgelm``, so ``forgelm`` must still be importable.

Rules
-----

Every ``*.md`` under ``docs/usermanuals/`` is read, a file outside a language
directory included: it is checked like any page, but no table row can exempt it.

1. **Schema.**  Every Pydantic class in ``forgelm/config.py`` maps each field,
   and each ``alias=``, either to a nested model (directly or through
   ``Optional[X]`` / ``List[X]``) or to an opaque leaf (``str``, ``Literal``,
   ``Dict[str, Any]``, ``List[str]`` ...).  ``ForgeConfig``'s own fields are the
   top-level block names.
2. **Readable blocks.**  A YAML block must be opened by ```` ```yaml ```` at the
   start of a line and closed by ```` ``` ````, the only form this guard and
   ``check_yaml_snippets.py`` read.  A YAML fence spelled any other way (```` ```yml ````, ``~~~yaml``,
   ```` ```YAML ````, ```` ```yaml title="..." ````, a fence indented under a
   list item) or never closed is a finding, and so is a block that does not
   parse.  Blocks are read with YAML 1.2 booleans, where only ``true`` and
   ``false`` are booleans, so GitHub Actions' ``on:`` is the key ``"on"``, not
   ``True``.  A key that appears twice in one mapping is a parse error, because
   PyYAML would silently keep the last value.  A fenced block in another
   language is never read, and a YAML-looking fence inside one is its content.
3. **Block names.**  In every document of every block, a full config as much as
   a fragment, each top-level key must be a ``ForgeConfig`` block; any other key
   makes it an *unknown block*.  A document that is not a mapping cannot be a
   ``ForgeConfig``: a list is named by its items' keys (``- name``), a scalar as
   ``<scalar>``, and a key that is not a string is shown as ``<int: 1>``.
4. **Nested keys.**  In a fragment (a document without the full ``model`` /
   ``training`` / ``data`` triple), every key under a block that resolves to a
   model is walked recursively, and a key that is not a declared field or alias
   of that model, a non-string key included, is drift.  Opaque fields are never
   descended into, so a free-form map such as
   ``MergeConfig.models: List[Dict[str, Any]]`` is never false-flagged.
5. **Exemptions.**  An unknown block passes only when its page (relative to the
   language directory) and its exact key set form a row of one of two tables:

   - ``_NON_FORGELM_YAML``: another tool's YAML (a GitHub Actions workflow, a
     compose file).  The reason starts with that tool's name from
     ``_OTHER_TOOLS``.
   - ``_KNOWN_OPEN``: a fabricated or unimplemented block that a tracked issue
     owns.  The reason starts with the issue (``#123: ...``), and the change
     that corrects the page deletes the row.

   ``ForgeConfig`` blocks inside an exempted block are still walked.  A row must
   match exactly one block per language: always in ``en`` and ``tr``, and in a
   deferred language (``de``, ``fr``, ``es``, ``zh`` may be partial, see
   ``docs/standards/localization.md``) only when that directory has the page.
   A row that matches no block, or a second block, is a finding, unless the
   page holds a block this guard cannot read; that block is reported instead.

Known limits
------------

- Only key names are checked.  Value types and ranges, ``Literal`` choices and
  cross-field validators need real Pydantic validation.
- The nested keys of a full config are left to ``check_yaml_snippets.py``.  For
  a full config marked ``# INVALID:`` (deliberately invalid) neither guard
  checks them; only its block names are checked, here.
- A list document is identified only by the top-level keys of its items.
- Inside a fenced block of another language, a ```` ```yaml ```` fence at the
  start of a line is still read, because the extractor does not track nesting.

Exit codes (the ``tools/`` contract, not the public 0-6 surface of ``forgelm``):

- ``0``: no finding, or findings printed in advisory mode (no ``--strict``).
- ``1``: a finding under ``--strict``; and in every mode, no YAML block found,
  ``forgelm/config.py``, ``docs/usermanuals/`` or ``ForgeConfig`` missing,
  PyYAML or ``forgelm`` not importable, or a malformed exemption table.
- ``2``: invalid command-line arguments (``argparse``).

CI wiring: ``.github/workflows/ci.yml`` runs this guard with ``--strict``.

Usage::

    python3 tools/check_usermanual_schema_drift.py
    python3 tools/check_usermanual_schema_drift.py --strict
    python3 tools/check_usermanual_schema_drift.py --quiet
"""

from __future__ import annotations

import argparse
import ast
import itertools
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

# Fail closed: a guard that cannot read YAML must never report success.
try:
    import yaml
except ImportError as exc:
    print(
        f"check_usermanual_schema_drift: PyYAML not importable ({exc}). "
        "Install it with `pip install pyyaml` (a declared runtime dependency).",
        file=sys.stderr,
    )
    sys.exit(1)

# Reuse the sibling guards' AST plumbing / fence-extraction rather than
# re-implementing them — a single source of truth for "how do we find
# Pydantic classes" and "how do we find fenced yaml blocks".
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from check_field_descriptions import _import_aliases, _pydantic_class_names  # type: ignore
    from check_yaml_snippets import Snippet, extract_yaml_snippets  # type: ignore
except (ImportError, SystemExit) as exc:
    # check_yaml_snippets exits 2 when forgelm is not importable; 2 is this
    # guard's argument-error code, so every import failure exits 1 here.
    print(f"check_usermanual_schema_drift: cannot import sibling guard internals ({exc!r}).", file=sys.stderr)
    sys.exit(1)


REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "forgelm" / "config.py"
USERMANUAL_ROOT = REPO_ROOT / "docs" / "usermanuals"

# A document carrying the full required triple is validated for real by
# check_yaml_snippets.py; walking its nested keys here would duplicate that
# (stronger) check, so only its block names are checked here.
_FULL_CONFIG_KEYS = frozenset({"model", "training", "data"})

# EN and TR are mandatory for every page; a deferred language may be partial
# (docs/usermanuals/_meta.yaml, docs/standards/localization.md).
_MANDATORY_LANGUAGES = frozenset({"en", "tr"})

_YAML_LANGUAGES = frozenset({"yaml", "yml"})
_CANONICAL_YAML_FENCE = "```yaml"
# A fence under a nested list item is indented; this bound covers several levels.
_MAX_FENCE_INDENT = 16

_BOOL_TAG = "tag:yaml.org,2002:bool"
_MERGE_TAG = "tag:yaml.org,2002:merge"

# Names that stand for something other than a mapping key in an unknown block.
_SCALAR = "<scalar>"
_LIST = "<list>"

_PARSE_HINT = "make it valid YAML: write an elision as a comment, e.g. `# ...`"
_DUPLICATE_HINT = "a key may appear once per mapping: show an alternative as a comment"
_UNCLOSED_HINT = "close the block with ``` on a line of its own"
_FENCE_HINT = (
    "open the block with ```yaml at the start of a line and close it with ```: the schema guards read no other fence"
)

# (page relative to its language directory, exact set of unrecognised
# top-level keys) -> why the block is allowed to stay.  Each row exempts
# exactly one block per language directory: a second block with the same key
# set on the same page is a new finding, not a copy of the listed one.
_PageKeys = Tuple[str, FrozenSet[str]]

# The tools whose YAML the user manual shows.  A _NON_FORGELM_YAML reason starts
# with one of these names, so a row cannot claim "another tool" without naming it.
_OTHER_TOOLS = ("GitHub Actions", "GitLab CI", "docker-compose", "vLLM", "tools/check_pip_audit.py")

# A _KNOWN_OPEN reason starts with the issue that owns the fix.
_ISSUE_REASON_RE = re.compile(r"#[1-9][0-9]{0,6}: \S")

# Snippets that are another tool's YAML, not a ForgeLM config.
_NON_FORGELM_YAML: Dict[_PageKeys, str] = {
    ("compliance/human-approval-gate.md", frozenset({"env", "steps"})): "GitHub Actions: workflow excerpt",
    ("data/audit.md", frozenset({"- name", "- run"})): "GitHub Actions: workflow steps",
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
    ): "vLLM: engine config sample (tokenizer_mode / served_model_name / chat_template are not emitted by `forgelm deploy`: #435)",
    ("evaluation/auto-revert.md", frozenset({"- name", "- run"})): "GitHub Actions: workflow step",
    ("operations/cicd.md", frozenset({"jobs", "name", "on"})): "GitHub Actions: workflow",
    ("operations/cicd.md", frozenset({"- uses", "- with"})): "GitHub Actions: cache step",
    ("operations/cicd.md", frozenset({"audit", "deploy", "stages", "train"})): "GitLab CI: pipeline",
    ("operations/cicd.md", frozenset({"concurrency"})): "GitHub Actions: concurrency block",
    ("operations/docker.md", frozenset({"services", "version", "volumes"})): "docker-compose: compose file",
    ("operations/supply-chain.md", frozenset({"ignores"})): "tools/check_pip_audit.py: file read by --ignores",
    (
        "reference/exit-codes.md",
        frozenset({"- continue-on-error", "- id", "- if", "- name", "- run"}),
    ): "GitHub Actions: workflow steps",
    ("reference/exit-codes.md", frozenset({"- name", "- run"})): "GitHub Actions: workflow step",
    ("reference/exit-codes.md", frozenset({"train"})): "GitLab CI: job",
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
    """A usermanual YAML block whose top-level keys are not all ForgeConfig blocks."""

    path: Path
    line: int
    keys: FrozenSet[str]
    page: Optional[str]  # relative to the language directory; None outside one


@dataclass(frozen=True)
class UnreadableBlock:
    """A usermanual YAML block that no key check can read."""

    path: Path
    line: int
    problem: str
    hint: str


@dataclass(frozen=True)
class StaleRow:
    """An exemption row that does not match exactly one block per language directory."""

    table: str
    page: str
    keys: FrozenSet[str]
    reason: str
    matched_in: Tuple[str, ...]
    missing_in: Tuple[str, ...]
    repeated_in: Tuple[str, ...]
    page_found: bool


@dataclass(frozen=True)
class ScanResult:
    """Everything one pass over a usermanual tree found."""

    drifts: List[KeyDrift]
    unknown_blocks: List[UnknownBlock]
    unreadable_blocks: List[UnreadableBlock]
    stale_rows: List[StaleRow]
    snippet_count: int
    walked_count: int
    full_config_count: int
    exempted_count: int

    @property
    def clean(self) -> bool:
        return not (self.drifts or self.unknown_blocks or self.unreadable_blocks or self.stale_rows)


class _ExampleLoader(yaml.SafeLoader):
    """``SafeLoader`` with YAML 1.2 booleans: only ``true`` and ``false`` are booleans.

    YAML 1.1 also reads ``on``, ``off``, ``yes`` and ``no`` as booleans, which
    turns GitHub Actions' ``on:`` key into ``True``.
    """


_ExampleLoader.yaml_implicit_resolvers = {
    first: [(tag, pattern) for tag, pattern in resolvers if tag != _BOOL_TAG]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_ExampleLoader.add_implicit_resolver(_BOOL_TAG, re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF"))


class _DuplicateKeyError(yaml.constructor.ConstructorError):
    """A mapping holds the same key twice."""


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


def _key_text(key: object) -> str:
    """Return how a mapping key is named in a finding and in a table row."""
    return key if isinstance(key, str) else f"<{type(key).__name__}: {key!r}>"


def _walk_value(
    value: object,
    class_name: str,
    schema: Dict[str, Dict[str, FieldType]],
    path: str,
) -> List[str]:
    """Recursively validate ``value`` (a parsed YAML mapping) against ``class_name``.

    Returns a list of key-path strings that have no matching schema field;
    a key that is not a string can never be a field, so it is one of them.
    Leaves (opaque fields) and non-dict values under a resolved model field
    are not descended into further — see module docstring.
    """
    if not isinstance(value, dict):
        return []
    fields = schema.get(class_name)
    if fields is None:
        return []
    drift: List[str] = []
    for key, sub_value in value.items():
        field_type = fields.get(key)
        if field_type is None:
            drift.append(f"{path}.{_key_text(key)}")
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


def _reject_duplicate_keys(loader: _ExampleLoader, root: yaml.Node) -> None:
    """Raise :class:`_DuplicateKeyError` when a mapping under ``root`` holds a key twice.

    PyYAML keeps the last value without a word, so a reader who copies the
    block gets a value the page did not mean.  A key that overrides a ``<<:``
    merge is not a duplicate: merges are expanded after this check.
    """
    pending: List[yaml.Node] = [root]
    visited: Set[int] = set()
    while pending:
        node = pending.pop()
        if id(node) in visited:
            continue
        visited.add(id(node))
        if isinstance(node, yaml.SequenceNode):
            pending.extend(node.value)
        elif isinstance(node, yaml.MappingNode):
            seen: Set[object] = set()
            for key_node, value_node in node.value:
                pending.extend((key_node, value_node))
                if not isinstance(key_node, yaml.ScalarNode) or key_node.tag == _MERGE_TAG:
                    continue
                key = loader.construct_object(key_node)
                if key in seen:
                    raise _DuplicateKeyError(None, None, f"found duplicate key {key!r}", key_node.start_mark)
                seen.add(key)


def load_documents(body: str) -> List[object]:
    """Parse every document of ``body`` (``---`` separates documents) the way this guard reads them.

    Raises ``yaml.YAMLError`` (a duplicate key included) or ``ValueError`` (a
    date-shaped scalar that is not a real date) when ``body`` cannot be read.
    """
    loader = _ExampleLoader(body)
    try:
        documents: List[object] = []
        while loader.check_node():
            node = loader.get_node()
            _reject_duplicate_keys(loader, node)
            documents.append(loader.construct_document(node))
        return documents
    finally:
        loader.dispose()


def _describe_parse_error(snippet: "Snippet", exc: Exception) -> str:
    """Return PyYAML's account of ``exc`` with line numbers of the Markdown file."""
    if not isinstance(exc, yaml.MarkedYAMLError):
        return str(exc)

    def line_of(mark: Optional[yaml.Mark]) -> Optional[int]:
        return None if mark is None else snippet.line_start + 1 + mark.line

    problem_line = line_of(exc.problem_mark)
    text = exc.problem or ""
    if exc.context:
        context_line = line_of(exc.context_mark)
        context = exc.context if context_line in (None, problem_line) else f"{exc.context} at line {context_line}"
        text = f"{context}: {text}" if text else context
    return f"{text} at line {problem_line}" if problem_line is not None else text


def _read(snippet: "Snippet") -> Tuple[List[object], Optional[UnreadableBlock]]:
    """Return the documents of ``snippet``, or no document and why it cannot be read."""
    try:
        return load_documents(snippet.body), None
    except (yaml.YAMLError, ValueError) as exc:
        hint = _DUPLICATE_HINT if isinstance(exc, _DuplicateKeyError) else _PARSE_HINT
        return [], UnreadableBlock(snippet.path, snippet.line_start, _describe_parse_error(snippet, exc), hint)
    except RecursionError:
        return [], UnreadableBlock(snippet.path, snippet.line_start, "nested too deeply to read", _PARSE_HINT)


def _is_full_config(document: object) -> bool:
    return isinstance(document, dict) and _FULL_CONFIG_KEYS.issubset(document)


def _item_keys(item: object) -> List[str]:
    """Return the names a list document's item contributes to an unknown block."""
    if isinstance(item, dict):
        return [_key_text(key) for key in item]
    return [_LIST if isinstance(item, list) else _SCALAR]


def _unknown_keys(documents: List[object], top_level: Dict[str, FieldType]) -> FrozenSet[str]:
    """Return the names that make ``documents`` an unknown block (empty when every key is a ForgeConfig block)."""
    keys: Set[str] = set()
    for document in documents:
        if document is None:
            continue
        if isinstance(document, dict):
            keys.update(_key_text(key) for key in document if key not in top_level)
        elif isinstance(document, list):
            keys.update(f"- {name}" for item in document for name in _item_keys(item))
        else:
            keys.add(_SCALAR)
    return frozenset(keys)


def _nested_drift(
    documents: List[object], schema: Dict[str, Dict[str, FieldType]], top_level: Dict[str, FieldType]
) -> List[str]:
    """Return the key paths inside the ForgeConfig blocks of every fragment that have no schema field."""
    drift: List[str] = []
    for document in documents:
        if not isinstance(document, dict) or _is_full_config(document):
            continue
        for key, sub_value in document.items():
            field_type = top_level.get(key)
            if field_type is None or field_type.nested_class is None:
                continue  # an unknown block (see _unknown_keys) or an opaque block
            drift.extend(_walk_value(sub_value, field_type.nested_class, schema, key))
    return drift


def unknown_top_level_keys(snippet: "Snippet", top_level: Dict[str, FieldType]) -> FrozenSet[str]:
    """Return the names that make ``snippet`` an unknown block; empty when it cannot be read (scan() reports that)."""
    documents, _ = _read(snippet)
    return _unknown_keys(documents, top_level)


def check_snippet(
    snippet: "Snippet", schema: Dict[str, Dict[str, FieldType]], top_level: Dict[str, FieldType]
) -> List[KeyDrift]:
    """Return the nested key-path drift of one snippet; empty when it cannot be read (scan() reports that)."""
    documents, _ = _read(snippet)
    return [
        KeyDrift(path=snippet.path, line=snippet.line_start, key_path=key_path)
        for key_path in _nested_drift(documents, schema, top_level)
    ]


def _fence_of(line: str) -> Optional[Tuple[str, int, str]]:
    """Return ``(marker, length, info string)`` when ``line`` is a code fence, else ``None``."""
    body = line.lstrip(" \t")
    if len(line) - len(body) > _MAX_FENCE_INDENT or body[:3] not in ("```", "~~~"):
        return None
    marker = body[0]
    length = len(body) - len(body.lstrip(marker))
    info = body[length:].strip()
    if marker == "`" and "`" in info:
        return None  # CommonMark: a backtick fence's info string holds no backtick
    return marker, length, info


# A fence opened inside a block quote or a list item (``> ```yaml``, ``- ```yaml``) is not one the extractor reads either.
_CONTAINER_PREFIX_RE = re.compile(r"^(?:[ \t]*(?:>|[-*+]|\d{1,9}[.)])[ \t]?)+")


def _strip_container(line: str) -> str:
    """Return ``line`` without its leading block-quote and list-item markers."""
    return _CONTAINER_PREFIX_RE.sub("", line, count=1)


def _fence_language(info: str) -> str:
    """Return the language word of a fence's info string, lower-cased (``YAML title="x"`` gives ``yaml``)."""
    return "".join(itertools.takewhile(lambda ch: ch.isalnum() or ch in "-_+", info.lstrip("{."))).lower()


def unreadable_yaml_fences(lines: List[str], snippets: List["Snippet"]) -> List[Tuple[int, str, str]]:
    """Return ``(line, problem, hint)`` for every YAML fence whose block ``snippets`` does not hold.

    Fences are tracked the CommonMark way (a block closes on a fence of the
    same marker, at least as long, with no info string), so the content of a
    fenced block in another language is never taken for a fence.
    """
    read = {snippet.line_start for snippet in snippets}
    found: List[Tuple[int, str, str]] = []
    open_fence: Optional[Tuple[str, int, bool]] = None  # (marker, length, opened inside a block quote or list item)
    for number, line in enumerate(lines, 1):
        prefixed = False
        if open_fence is None:
            fence = _fence_of(line)
            if fence is None:
                fence = _fence_of(_strip_container(line))
                prefixed = fence is not None
        else:
            fence = _fence_of(_strip_container(line) if open_fence[2] else line)
        if fence is None:
            continue
        marker, length, info = fence
        if open_fence is not None:
            if marker == open_fence[0] and length >= open_fence[1] and not info:
                open_fence = None
            continue
        open_fence = (marker, length, prefixed)
        if number in read or _fence_language(info) not in _YAML_LANGUAGES:
            continue
        if line.rstrip() == _CANONICAL_YAML_FENCE:
            found.append((number, "YAML fence never closed", _UNCLOSED_HINT))
        else:
            found.append((number, f"YAML fence written {line.strip()!r}", _FENCE_HINT))
    return found


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


def _stale_rows(
    tables: Tuple[Tuple[str, Dict[_PageKeys, str]], ...],
    matched: "Counter[Tuple[str, str, FrozenSet[str]]]",
    pages: Dict[str, Set[str]],
    unreadable_pages: Set[Tuple[str, str]],
) -> List[StaleRow]:
    """Return every row that does not match exactly one block in each language that must carry it."""
    stale: List[StaleRow] = []
    for table_name, table in tables:
        for (page, keys), reason in table.items():
            expected = sorted(_MANDATORY_LANGUAGES | {lang for lang, names in pages.items() if page in names})
            present = tuple(lang for lang in expected if matched[(lang, page, keys)])
            repeated = tuple(lang for lang in present if matched[(lang, page, keys)] > 1)
            # A page holding a block this guard cannot read is reported as unreadable instead.
            missing = tuple(
                lang for lang in expected if not matched[(lang, page, keys)] and (lang, page) not in unreadable_pages
            )
            if missing or repeated:
                page_found = any(page in pages.get(lang, set()) for lang in expected)
                stale.append(StaleRow(table_name, page, keys, reason, present, missing, repeated, page_found))
    return stale


def scan(
    root: Path,
    schema: Dict[str, Dict[str, FieldType]],
    top_level: Dict[str, FieldType],
    non_forgelm: Dict[_PageKeys, str],
    known_open: Dict[_PageKeys, str],
) -> ScanResult:
    """Read every fenced yaml block under ``root`` and classify what it holds."""
    drifts: List[KeyDrift] = []
    unknown: List[UnknownBlock] = []
    unreadable: List[UnreadableBlock] = []
    matched: "Counter[Tuple[str, str, FrozenSet[str]]]" = Counter()
    tally: "Counter[str]" = Counter()
    pages: Dict[str, Set[str]] = {}
    unreadable_pages: Set[Tuple[str, str]] = set()
    for md_path in walk_usermanuals(root):
        located = _language_and_page(root, md_path)
        if located is not None:
            pages.setdefault(located[0], set()).add(located[1])
        snippets = extract_yaml_snippets(md_path)
        lines = md_path.read_text(encoding="utf-8").splitlines()
        problems = [
            UnreadableBlock(md_path, number, problem, hint)
            for number, problem, hint in unreadable_yaml_fences(lines, snippets)
        ]
        for snippet in snippets:
            tally["found"] += 1
            documents, problem = _read(snippet)
            if problem is not None:
                problems.append(problem)
                continue
            try:
                snippet_drifts = [
                    KeyDrift(path=snippet.path, line=snippet.line_start, key_path=key_path)
                    for key_path in _nested_drift(documents, schema, top_level)
                ]
                keys = _unknown_keys(documents, top_level)
                is_full = any(_is_full_config(doc) for doc in documents)
            except RecursionError:
                problems.append(
                    UnreadableBlock(snippet.path, snippet.line_start, "nested too deeply to check", _PARSE_HINT)
                )
                continue
            drifts.extend(snippet_drifts)
            if not keys:
                tally["full" if is_full else "walked"] += 1
            elif located is not None and ((located[1], keys) in non_forgelm or (located[1], keys) in known_open):
                matched[(located[0], located[1], keys)] += 1
                tally["exempted"] += 1
            else:
                unknown.append(UnknownBlock(snippet.path, snippet.line_start, keys, located[1] if located else None))
        if problems and located is not None:
            unreadable_pages.add(located)
        unreadable.extend(sorted(problems, key=lambda block: block.line))
    stale = _stale_rows(
        (("_NON_FORGELM_YAML", non_forgelm), ("_KNOWN_OPEN", known_open)), matched, pages, unreadable_pages
    )
    return ScanResult(
        drifts=drifts,
        unknown_blocks=unknown,
        unreadable_blocks=unreadable,
        stale_rows=stale,
        snippet_count=tally["found"],
        walked_count=tally["walked"],
        full_config_count=tally["full"],
        exempted_count=tally["exempted"],
    )


def _keys_text(keys: FrozenSet[str]) -> str:
    return ", ".join(sorted(keys))


def table_errors(non_forgelm: Dict[_PageKeys, str], known_open: Dict[_PageKeys, str]) -> List[str]:
    """Return what is wrong with the exemption tables; empty when every row is in the right table."""
    errors = [
        f"{page}  {_keys_text(keys)}: listed in both _NON_FORGELM_YAML and _KNOWN_OPEN"
        for page, keys in sorted(set(non_forgelm) & set(known_open), key=lambda row: (row[0], sorted(row[1])))
    ]
    tool_prefixes = tuple(f"{tool}: " for tool in _OTHER_TOOLS)
    for table_name, table in (("_NON_FORGELM_YAML", non_forgelm), ("_KNOWN_OPEN", known_open)):
        for (page, keys), reason in table.items():
            where = f"{table_name}: {page}  {_keys_text(keys)}"
            if not keys:
                errors.append(f"{where}: an empty key set never matches a block")
            if table_name == "_KNOWN_OPEN":
                if not _ISSUE_REASON_RE.match(reason):
                    errors.append(f"{where}: the reason must start with the issue that owns the fix, e.g. '#123: '")
            elif not reason.startswith(tool_prefixes) or not reason.split(": ", 1)[1].strip():
                errors.append(
                    f"{where}: the reason must start with the other tool's name, one of {_OTHER_TOOLS}, and ': '"
                )
    return errors


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Check that every fenced YAML example in docs/usermanuals/ is readable and uses only keys the "
            "ForgeConfig schema accepts (the schema is read from forgelm/config.py's AST), unless a table row "
            "lists the block as another tool's YAML or a known-open block that a tracked issue owns."
        ),
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Exit 1 on any finding.  Without it, findings are printed and the exit code is 0 (advisory mode for "
            "local iteration); a missing input, PyYAML or forgelm, or a malformed exemption table exits 1 either way."
        ),
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress the success summary.")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_arg_parser().parse_args(argv)

    if not CONFIG_PATH.is_file():
        print(f"check_usermanual_schema_drift: {CONFIG_PATH} not found.", file=sys.stderr)
        return 1
    if not USERMANUAL_ROOT.is_dir():
        print(f"check_usermanual_schema_drift: {USERMANUAL_ROOT} not found.", file=sys.stderr)
        return 1
    errors = table_errors(_NON_FORGELM_YAML, _KNOWN_OPEN)
    if errors:
        print("check_usermanual_schema_drift: malformed exemption table.", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return 1

    schema = build_schema_map(CONFIG_PATH)
    top_level = schema.get("ForgeConfig")
    if not top_level:
        print("check_usermanual_schema_drift: could not locate ForgeConfig in forgelm/config.py.", file=sys.stderr)
        return 1

    result = scan(USERMANUAL_ROOT, schema, top_level, _NON_FORGELM_YAML, _KNOWN_OPEN)

    if not result.clean:
        _report(result)
    if result.snippet_count == 0:
        print(f"check_usermanual_schema_drift: no yaml blocks found under {USERMANUAL_ROOT}.", file=sys.stderr)
        return 1
    if not result.clean:
        return 1 if args.strict else 0

    if not args.quiet:
        print(
            f"OK: {result.snippet_count} yaml block(s) under docs/usermanuals/: "
            f"{result.walked_count} walked key by key, "
            f"{result.full_config_count} full config(s) with their block names checked here and their nested keys "
            f"left to check_yaml_snippets.py, "
            f"{result.exempted_count} exempted by a table row (ForgeConfig blocks inside them still walked)."
        )
    return 0


def _display(path: Path) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _row_literal(page: str, keys: FrozenSet[str]) -> str:
    """Return the table key for ``page`` and ``keys`` as Python source, ready to paste."""
    return f"({json.dumps(page)}, frozenset({{{', '.join(json.dumps(key) for key in sorted(keys))}}}))"


def _unknown_block_advice(block: UnknownBlock) -> str:
    if block.page is None:
        return "not a ForgeConfig top-level block: correct the example; a file outside a language directory cannot be exempted"
    return (
        f"not a ForgeConfig top-level block: correct the example, or add {_row_literal(block.page, block.keys)} "
        f'to _NON_FORGELM_YAML with "<tool>: <what it is>" if it is another tool\'s YAML, or to _KNOWN_OPEN '
        f'with "#<issue>: <what is wrong>" if a tracked issue owns the fix'
    )


def _stale_row_advice(row: StaleRow) -> str:
    if row.repeated_in:
        return (
            f"matches more than one block in {', '.join(row.repeated_in)}: a row exempts exactly one block per "
            f"language; correct the extra block"
        )
    if row.matched_in:
        return (
            f"unmatched in {', '.join(row.missing_in)} but matched in {', '.join(row.matched_in)}: the language "
            f"copies diverged; fix the page, not the row"
        )
    if not row.page_found:
        return (
            f"page not found in {', '.join(row.missing_in)}: if it was renamed, update the row's path; "
            f"otherwise delete the row"
        )
    return (
        "no block on the page has exactly these keys: if the block changed, update the row's key set; "
        "if it was corrected or removed, delete the row"
    )


def _report(result: ScanResult) -> None:
    """Print every finding of a failed scan."""
    if result.drifts:
        print(f"FAIL: {len(result.drifts)} fabricated schema key(s) found in docs/usermanuals/ YAML examples.")
        for d in result.drifts:
            print(f"  {_display(d.path)}:{d.line}  {d.key_path}  (no such field on the ForgeConfig schema)")
    if result.unknown_blocks:
        print(
            f"FAIL: {len(result.unknown_blocks)} YAML block(s) in docs/usermanuals/ use top-level keys ForgeConfig rejects."
        )
        for u in result.unknown_blocks:
            print(f"  {_display(u.path)}:{u.line}  {_keys_text(u.keys)}  ({_unknown_block_advice(u)})")
    if result.unreadable_blocks:
        print(
            f"FAIL: {len(result.unreadable_blocks)} YAML block(s) in docs/usermanuals/ cannot be read, "
            f"so no key in them is checked."
        )
        for b in result.unreadable_blocks:
            print(f"  {_display(b.path)}:{b.line}  {b.problem}  ({b.hint})")
    if result.stale_rows:
        print(f"FAIL: {len(result.stale_rows)} exemption row(s) do not match exactly one YAML block per language.")
        for row in result.stale_rows:
            print(f"  {row.table}: {row.page}  {_keys_text(row.keys)}  ({row.reason}) — {_stale_row_advice(row)}")


if __name__ == "__main__":
    sys.exit(main())
