#!/usr/bin/env python3
"""Wave 6 / Faz 31 — audit-event catalog ↔ code cross-check.

Inventories every dotted-namespace audit event emitted by ``forgelm/``
(grep for ``log_event("...")`` / ``event="..."``) and diffs against the
canonical event table in ``docs/reference/audit_event_catalog.md``.

Two failure modes:

- **Code ⊃ Catalog** — an event is emitted in code but not documented
  in the catalog. Surfaces P8 of the 2026-05-07 docs audit.
- **Catalog ⊃ Code** — the catalog claims an event that code never
  emits ("ghost row"). Surfaces the reverse-direction drift.

Exit codes (per ``tools/`` contract — NOT the public 0/1/2/3/4 surface
that ``forgelm/`` honours):

- ``0`` — emitted-events set ≡ catalog-events set.
- ``1`` — at least one event diverges.

Usage::

    python3 tools/check_audit_event_catalog.py
    python3 tools/check_audit_event_catalog.py --strict   # alias of default
    python3 tools/check_audit_event_catalog.py --quiet    # silent on success

Plan reference: 2026-05-07 docs audit §10 (CI gate proposals) gate #3.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Set, Tuple

# Match dotted-namespace event literals **only in an emission context** so
# config-path / message literals that happen to share an event namespace
# (e.g. the string ``'training.output_dir'`` inside an error message) are not
# miscounted as emitted events. The four emission shapes are:
#
#   * Direct call first-arg: ``log_event("foo.bar", ...)`` /
#     ``self._audit_event("foo.bar", ...)`` (whitespace/newline tolerant).
#   * Keyword form: ``event="foo.bar"`` (webhook ``_send`` payloads).
#   * JSON-shaped: ``"event": "foo.bar"``.
#   * Constant declaration: ``_EVT_REVERT_TRIGGERED = "model.reverted"`` — the
#     canonical declaration site for events emitted via constant indirection
#     (``log_event(_EVT_REVERT_TRIGGERED)``).
#
# Anchoring on the emission context (rather than any quoted dotted string)
# kills the config-path false positive (F-P8-C-12) without dropping a single
# genuine event — verified empirically against the prior broad regex.
_EVENT_NAMESPACES = (
    "training",
    "pipeline",
    "model",
    "human_approval",
    "audit",
    "compliance",
    "data",
    "cache",
    "cli",
    "approval",
    "safety",
    "benchmark",
    "judge",
)
_EVENT_NAME_RE = r"(?:" + "|".join(_EVENT_NAMESPACES) + r")\.[a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)*"
# Emission-context prefix: a real event literal is the first arg of an emit
# call, a ``event=`` kwarg, a ``"event":`` JSON value, or an ``_EVT*`` constant.
_EMISSION_PREFIX_RE = (
    r"(?:"
    r"(?:log_event|_audit_event)\(\s*"  # direct emission call, first positional arg
    r"|event\s*=\s*"  # keyword form: event="..."
    r"|[\"']event[\"']\s*:\s*"  # JSON-shaped: "event": "..."
    r"|_EVT[A-Z0-9_]*\s*=\s*"  # constant declaration: _EVT_X = "..."
    r")"
)
_EVENT_LITERAL_RE = re.compile(_EMISSION_PREFIX_RE + r'["\'](?P<name>' + _EVENT_NAME_RE + r')["\']')


# Match catalog table rows. Catalog uses pipe-table format; the event
# name lives in the first column wrapped in backticks.
_CATALOG_ROW_RE = re.compile(
    r"^\|\s*`(?P<name>(?:" + "|".join(_EVENT_NAMESPACES) + r")\.[a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)*)`\s*\|",
    re.MULTILINE,
)


# Events that are *intentionally* in code but not catalogued (and vice
# versa). Each entry is a `(name, reason)` pair so future readers see
# why the exception exists.
_CODE_ONLY_ALLOWLIST = frozenset(
    {
        # Add here when an emit site is intentionally undocumented (e.g.
        # debug-only events that don't ship in production audit logs).
    }
)
_CATALOG_ONLY_ALLOWLIST = frozenset(
    {
        # Add here when a catalog row covers an event family that doesn't
        # appear in code yet (forward-compat, Phase N+ backlog).
    }
)


# Common file-extension second-segments that look like dotted events
# but are paths (e.g. ``"data.jsonl"`` in ``quickstart.py``). The regex
# is intentionally broad so we catch indirect emissions; these
# string-literal exclusions kill the obvious filename false-positives.
_NON_EVENT_SECOND_SEGMENTS = frozenset(
    {
        "jsonl",
        "json",
        "yaml",
        "yml",
        "txt",
        "md",
        "py",
        "pkl",
        "pt",
        "safetensors",
        "log",
        "csv",
        "tsv",
        "ini",
        "toml",
    }
)


def emitted_events(forgelm_root: Path) -> Set[Tuple[str, Path]]:
    """Return ``{(event_name, source_path)}`` for every event literal in
    ``forgelm/``. Filename-shaped matches (``data.jsonl`` etc.) are
    filtered out via :data:`_NON_EVENT_SECOND_SEGMENTS`.
    """
    out: Set[Tuple[str, Path]] = set()
    for py in sorted(forgelm_root.rglob("*.py")):
        try:
            text = py.read_text(encoding="utf-8")
        except OSError:
            continue
        for match in _EVENT_LITERAL_RE.finditer(text):
            name = match.group("name")
            second = name.split(".", 1)[1].split(".", 1)[0]
            if second in _NON_EVENT_SECOND_SEGMENTS:
                continue
            out.add((name, py))
    return out


def catalogued_events(catalog_path: Path) -> Set[str]:
    """Return the set of event names listed in the catalog markdown."""
    text = catalog_path.read_text(encoding="utf-8")
    return {match.group("name") for match in _CATALOG_ROW_RE.finditer(text)}


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Cross-check the audit-event vocabulary: emitted events in "
            "forgelm/ must match the catalog in "
            "docs/reference/audit_event_catalog.md (in both directions)."
        ),
    )
    parser.add_argument(
        "--forgelm-root",
        type=Path,
        default=Path("forgelm"),
        help="Path to the forgelm source tree (default: forgelm/).",
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=Path("docs/reference/audit_event_catalog.md"),
        help="Path to the canonical event catalog markdown.",
    )
    parser.add_argument("--strict", action="store_true", help="Alias of default; exits 1 on drift.")
    parser.add_argument("--quiet", action="store_true", help="Suppress success summary.")
    return parser


def _print_code_only(code_only: Set[str], emitted: Set[Tuple[str, Path]]) -> None:
    """Print events emitted in code but missing from the catalog, with source pointers."""
    if not code_only:
        return
    print(f"\n  Emitted in code but missing from catalog ({len(code_only)}):")
    for name in sorted(code_only):
        src = next((p for n, p in emitted if n == name), None)
        where = f"  ← {src}" if src else ""
        print(f"    - {name}{where}")


def _print_catalog_only(catalog_only: Set[str]) -> None:
    """Print events in the catalog that no code path emits."""
    if not catalog_only:
        return
    print(f"\n  In catalog but never emitted in code ({len(catalog_only)}):")
    for name in sorted(catalog_only):
        print(f"    - {name}")


def main(argv=None) -> int:
    args = _build_arg_parser().parse_args(argv)

    if not args.forgelm_root.exists():
        print(f"check_audit_event_catalog: --forgelm-root {args.forgelm_root!r} does not exist.", file=sys.stderr)
        return 1
    if not args.catalog.exists():
        print(f"check_audit_event_catalog: --catalog {args.catalog!r} does not exist.", file=sys.stderr)
        return 1

    emitted = emitted_events(args.forgelm_root)
    catalogued = catalogued_events(args.catalog)

    emitted_names = {name for name, _ in emitted}
    code_only = (emitted_names - catalogued) - _CODE_ONLY_ALLOWLIST
    catalog_only = (catalogued - emitted_names) - _CATALOG_ONLY_ALLOWLIST

    if code_only or catalog_only:
        print("FAIL: audit-event catalog drift detected.")
        _print_code_only(code_only, emitted)
        _print_catalog_only(catalog_only)
        return 1

    if not args.quiet:
        print(
            f"OK: {len(emitted_names)} unique audit-event(s) in forgelm/ all match the "
            f"{len(catalogued)} catalog row(s) under {args.catalog}."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
