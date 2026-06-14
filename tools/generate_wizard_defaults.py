"""Schema-driven wizard defaults generator (F1 / review-cycle 3).

Walks every Pydantic submodel reachable from :class:`forgelm.config.ForgeConfig`,
finds fields marked with ``json_schema_extra={"wizard": True}``, extracts
the schema default, and writes two artefacts:

1. ``forgelm/wizard/_defaults.json`` — Python consumer; loaded via
   :mod:`importlib.resources` from :func:`forgelm.wizard._state._load_defaults`.
2. ``site/js/wizard_defaults.js`` — Web consumer; emitted as a tiny script
   that sets ``globalThis.WIZARD_DEFAULTS = {...}`` so :file:`site/js/wizard.js`'s
   ``defaultState()`` can read schema-aligned values without a build step.

Run after any schema default change::

    python tools/generate_wizard_defaults.py

The CI guard ``tools/check_wizard_defaults_sync.py`` regenerates this
script's expected output in memory and byte-compares it against the
committed files; PRs that change a wizard-flagged default without
regenerating fail the doc-guard step.

Why a generator instead of importing live values at runtime?

- The web wizard is a static site (no Python runtime to query schema
  during page load) — it needs JSON-as-data shipped with the assets.
- The Python side benefits too: the wizard module's import-time cost
  stays low (no Pydantic model walk), and the JSON is auditable in git.
- Round-tripping through JSON forces all wizard-relevant defaults to be
  JSON-serialisable primitives — no datetimes, no Path, no ``Field``s
  leaking into the operator's YAML.
"""

from __future__ import annotations

import json
import sys
import typing
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Type

# Allow `python tools/generate_wizard_defaults.py` from the repo root
# without a separate ``python -m forgelm.tools`` install.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from pydantic import BaseModel  # noqa: E402  -- after path tweak
from pydantic_core import PydanticUndefined  # noqa: E402  -- after path tweak

from forgelm.config import ForgeConfig  # noqa: E402  -- after path tweak

# ---------------------------------------------------------------------------
# Wizard-flag contract
#
# Fields carrying ``json_schema_extra={"wizard": True}`` MUST have
# JSON-primitive defaults (int / float / bool / str / None / list of
# primitives / dict of primitives).  ``Path``, ``datetime``, callables,
# and arbitrary BaseModel instances will fail ``json.dumps`` and bring
# down both the generator and the CI guard.  This contract is enforced
# implicitly by the JSON serialisation step at the end of ``main()``;
# document it here so future contributors don't get surprised.
#
# Optional[BaseModel] sub-blocks are unwrapped via :mod:`typing` so a
# wizard flag on, say, ``evaluation.cost_ceiling_usd`` (where
# ``evaluation: Optional[EvaluationConfig]``) is picked up correctly
# rather than silently skipped (B1 fix from review-cycle 3).
# ---------------------------------------------------------------------------

# Output destinations — both kept in lockstep by the CI guard.
PYTHON_TARGET = _REPO_ROOT / "forgelm" / "wizard" / "_defaults.json"
JS_TARGET = _REPO_ROOT / "site" / "js" / "wizard_defaults.js"

# Stable section order in the output JSON / JS — matches the order
# fields appear inside ForgeConfig so visual diffs read naturally.
_SECTION_ORDER: Tuple[str, ...] = (
    "model",
    "lora",
    "training",
    "data",
)


def _is_wizard_flagged(field_info: Any) -> bool:
    """Return True when *field_info* carries ``json_schema_extra={"wizard": True}``.

    Pydantic v2 stores the extra dict on ``FieldInfo.json_schema_extra``;
    operators may also set it via ``Field(..., wizard=True)`` shortcut
    that lands as a nested ``"extra": {...}`` mapping in older releases.
    Cover both shapes defensively.
    """
    extra = getattr(field_info, "json_schema_extra", None)
    if isinstance(extra, dict):
        return bool(extra.get("wizard"))
    return False


def _unwrap_basemodel(annotation: Any) -> Optional[Type[BaseModel]]:
    """Return the underlying :class:`BaseModel` subclass, or ``None``.

    Handles three shapes:
        - bare class: ``EvaluationConfig`` → ``EvaluationConfig``
        - ``Optional[Cls]`` (== ``Union[Cls, None]``) → ``Cls``
        - ``Union[Cls, OtherCls]`` → first ``BaseModel`` found

    B1 fix (review-cycle 3): the previous version only matched the
    bare-class case, so ``Optional[XxxConfig]`` sub-blocks (10 of 14
    top-level ForgeConfig fields) were silently skipped — a wizard
    flag on any field inside one of them would never reach the JSON.
    """
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    args = typing.get_args(annotation)
    if not args:
        return None
    for arg in args:
        if isinstance(arg, type) and issubclass(arg, BaseModel):
            return arg
    return None


def _walk_model(model: Type[BaseModel], section: str, sink: Dict[str, Dict[str, Any]]) -> None:
    """Populate *sink* with wizard-flagged defaults from *model*.

    Recurses into nested ``BaseModel`` types so a wizard flag on a deep
    field still surfaces — including ``Optional[XxxConfig]`` (B1).
    ``model_fields`` is Pydantic v2's introspection API; we read each
    field's ``default`` attribute directly because the live Pydantic
    instance would substitute :data:`pydantic_core.PydanticUndefined`
    for required fields (which we skip via identity comparison — B2).
    """
    for name, field_info in model.model_fields.items():
        nested = _unwrap_basemodel(field_info.annotation)
        if nested is not None:
            # F-P1-FAB-41: a wizard flag belongs on a *scalar* field, not on
            # a submodel-typed field — the latter is silently ignored because
            # we recurse into the submodel instead.  Fail loudly so the
            # misplacement is caught at generation time rather than producing
            # a wizard that never receives the flagged default.
            if _is_wizard_flagged(field_info):
                raise ValueError(
                    f"Wizard flag on submodel-typed field {model.__name__}.{name!r} is ignored; "
                    "place json_schema_extra={'wizard': True} on a scalar field inside the submodel instead."
                )
            # Surface the nested submodel under its own section name
            # rather than the parent's; this keeps the JSON shape
            # operator-readable (``evaluation.safety_eval``, not
            # ``evaluation.evaluation.safety_eval``).
            _walk_model(nested, name, sink)
            continue
        if not _is_wizard_flagged(field_info):
            continue
        default = field_info.default
        # B2: identity comparison against the canonical sentinel —
        # ``repr().startswith()`` was brittle across Pydantic v2 minor
        # releases.  Required fields (no default) carry the sentinel;
        # skip them silently because wizard-flagged fields should
        # always carry a real default.
        if default is PydanticUndefined:
            continue
        sink.setdefault(section, {})[name] = default


def collect_defaults() -> Dict[str, Dict[str, Any]]:
    """Walk ForgeConfig + every nested submodel; return wizard-flagged defaults."""
    sink: Dict[str, Dict[str, Any]] = {}
    for top_name, field_info in ForgeConfig.model_fields.items():
        nested = _unwrap_basemodel(field_info.annotation)
        if nested is not None:
            _walk_model(nested, top_name, sink)
            continue
        # F-P1-FAB-41: a wizard-flagged *scalar* directly on ForgeConfig was
        # silently dropped because the top-level loop only recursed into
        # submodels.  Emit it under a reserved ``root`` section so the flag
        # reaches the wizard (ForgeConfig has no such field today; this keeps
        # the generator honest if one is ever added).
        if _is_wizard_flagged(field_info) and field_info.default is not PydanticUndefined:
            sink.setdefault("root", {})[top_name] = field_info.default
    # Reorder for deterministic output (sections that aren't in
    # ``_SECTION_ORDER`` come last, alphabetised for repeatability).
    ordered: Dict[str, Dict[str, Any]] = {}
    for section in _SECTION_ORDER:
        if section in sink:
            ordered[section] = dict(sorted(sink[section].items()))
    for section in sorted(sink):
        if section not in ordered:
            ordered[section] = dict(sorted(sink[section].items()))
    return ordered


_HEADER_PYTHON = (
    "{\n"
    '  "//": "Schema-derived wizard defaults — DO NOT EDIT BY HAND.",\n'
    '  "//1": "Regenerate via: python tools/generate_wizard_defaults.py",\n'
    '  "//2": "CI guard: tools/check_wizard_defaults_sync.py rejects manual drift.",\n'
)


def render_python_json(defaults: Dict[str, Dict[str, Any]]) -> str:
    """JSON output with a leading ``//`` comment block for human readers.

    The comment lines use the YAML-style ``"//"`` key idiom — JSON itself
    has no comment syntax, but Python's ``json.load`` happily parses
    string keys named ``"//"`` and the wizard ignores them when
    consuming the file (only specific section keys are read).
    """
    body = json.dumps(defaults, indent=2, ensure_ascii=False)
    # Strip the opening brace from ``body`` and prepend our header so
    # the comments precede the real keys.  Defaults dict is non-empty
    # so ``body`` always starts with ``{\n``.
    payload = body[2:]  # drop the leading "{\n"
    return _HEADER_PYTHON + payload + "\n"


def render_js_literal(defaults: Dict[str, Dict[str, Any]]) -> str:
    """Render as a tiny JS asset that exposes ``globalThis.WIZARD_DEFAULTS``.

    Uses a guarded global lookup (``globalThis`` with a ``window``
    fallback) so the asset still loads on browsers older than ES2020
    where ``globalThis`` is undefined, and so non-browser bundlers
    that polyfill ``window`` keep working without a ReferenceError.
    """
    body = json.dumps(defaults, indent=2, ensure_ascii=False)
    return (
        "/**\n"
        " * Schema-derived wizard defaults — DO NOT EDIT BY HAND.\n"
        " * Regenerate via: python tools/generate_wizard_defaults.py\n"
        " * CI guard: tools/check_wizard_defaults_sync.py rejects manual drift.\n"
        " *\n"
        " * Consumed by site/js/wizard.js's defaultState() so the web\n"
        " * wizard's accept-all-defaults YAML matches ForgeConfig() byte-\n"
        " * for-byte.  Loaded BEFORE wizard.js in the HTML pages that\n"
        " * mount the wizard modal.\n"
        " */\n"
        "(typeof globalThis !== 'undefined' ? globalThis : window)"
        f".WIZARD_DEFAULTS = {body};\n"
    )


def main() -> int:
    defaults = collect_defaults()
    PYTHON_TARGET.parent.mkdir(parents=True, exist_ok=True)
    JS_TARGET.parent.mkdir(parents=True, exist_ok=True)
    PYTHON_TARGET.write_text(render_python_json(defaults), encoding="utf-8")
    JS_TARGET.write_text(render_js_literal(defaults), encoding="utf-8")
    section_count = len(defaults)
    field_count = sum(len(v) for v in defaults.values())
    print(
        f"Wrote {section_count} section(s) / {field_count} wizard-flagged default(s):"
        f"\n  - {PYTHON_TARGET.relative_to(_REPO_ROOT)}"
        f"\n  - {JS_TARGET.relative_to(_REPO_ROOT)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
