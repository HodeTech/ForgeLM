#!/usr/bin/env python3
"""Wave 6 / Faz 31 — audit-event catalog ↔ code cross-check.

**Exact scope — read this before trusting a green run.**

This guard reconciles two sets:

1. **Code side** — every dotted, lower-snake-case string literal appearing
   in one of four *emission contexts* in a ``*.py`` file under
   ``forgelm/``: the first positional argument of ``log_event(...)`` /
   ``_audit_event(...)``, an ``event=`` keyword value, an ``"event":``
   JSON value, or the right-hand side of an ``_EVT*`` constant
   declaration. This covers the Article 12 audit vocabulary **and** the
   webhook notification vocabulary, because both use the same literal
   shapes and both are documented in the same catalog file.
2. **Catalog side** — every backticked dotted name in the first column of
   a pipe-table row in ``docs/reference/audit_event_catalog.md``.

Anything outside those two definitions is **not** examined. In
particular this guard does **not** see ``forgelm/quickstart.py``'s
``quickstart_audit.jsonl`` (it keys on ``event_type``, not ``event``) or
``forgelm/safety/_results.py``'s ``safety_trend.jsonl`` (no event key at
all). Both are deliberately outside the Article 12 chain — see the
catalog's "Logs this catalog does not cover" section. A passing run here
is not coverage for either file.

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
#
# NO NAMESPACE WHITELIST — and that is the point.
# ----------------------------------------------
# Until this revision, both the code-scan regex and the catalog-scan regex
# were built from one hand-maintained ``_EVENT_NAMESPACES`` tuple. That tuple
# omitted ``evaluation``, so ``evaluation.loss_gate_completed`` — a live
# Article 12 event declared in ``forgelm/trainer.py`` and documented in the
# catalog — was invisible to *both* halves of the reconciliation. The
# blindness was symmetric, which is exactly why it never failed: zero found on
# each side, so the two sides "agreed" and the guard printed OK. Renaming the
# event in code, or deleting its catalog row, would have changed nothing.
#
# That is the same defect the guard exists to catch, one level up: a
# hand-maintained list feeding a drift detector is itself undetected drift.
# The whitelist is therefore deleted rather than extended. What actually
# suppresses false positives is the *emission context* anchor below, not the
# namespace list — confirmed empirically: scanning the whole tree with no
# namespace constraint yields exactly the 44 previously-known events plus the
# one the whitelist was hiding, and no spurious matches.
#
# A list that does not exist cannot go stale. The one hand-maintained list
# that remains (``_NON_EVENT_SECOND_SEGMENTS``) is applied to the code side
# ONLY, never the catalog side, so its failure mode is loud: if it ever
# swallows a real event name, that event's catalog row becomes an unmatched
# ghost row and the guard fails. Keep that asymmetry — it is what makes the
# remaining list safe to hand-maintain.
_EVENT_NAME_RE = r"[a-z][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)+"
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
# name lives in the first column wrapped in backticks. Same namespace-free
# name pattern as the code side — deliberately, so neither side can be blind
# to a namespace the other side can see.
_CATALOG_ROW_RE = re.compile(
    r"^\|\s*`(?P<name>" + _EVENT_NAME_RE + r")`\s*\|",
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
#
# This is the only hand-maintained list left in the guard. It is applied in
# :func:`emitted_events` ONLY — never in :func:`catalogued_events` — so that
# a wrong entry cannot blind both sides at once. If a real event ever had a
# second segment listed here, the code side would drop it while the catalog
# side kept it, and the reconciliation would fail with a ghost row. Do not
# "optimise" that asymmetry away.
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


def scan_emitted(forgelm_root: Path) -> Tuple[Set[Tuple[str, Path]], int, int]:
    """Scan ``forgelm_root`` for event literals in emission context.

    Returns ``(pairs, files_scanned, emission_sites)`` where ``pairs`` is
    ``{(event_name, source_path)}``, ``files_scanned`` is the number of
    ``*.py`` files actually read, and ``emission_sites`` is the total number
    of matching literals (before de-duplication by name).

    The counts exist so the success line can name what it examined instead of
    merely asserting that it was happy. A guard reporting OK over zero files
    is the failure this tool was itself found guilty of.
    """
    out: Set[Tuple[str, Path]] = set()
    files_scanned = 0
    emission_sites = 0
    for py in sorted(forgelm_root.rglob("*.py")):
        try:
            text = py.read_text(encoding="utf-8")
        except OSError:
            continue
        files_scanned += 1
        for match in _EVENT_LITERAL_RE.finditer(text):
            name = match.group("name")
            second = name.split(".", 1)[1].split(".", 1)[0]
            if second in _NON_EVENT_SECOND_SEGMENTS:
                continue
            emission_sites += 1
            out.add((name, py))
    return out, files_scanned, emission_sites


def emitted_events(forgelm_root: Path) -> Set[Tuple[str, Path]]:
    """Return ``{(event_name, source_path)}`` for every event literal in
    ``forgelm/``. Filename-shaped matches (``data.jsonl`` etc.) are
    filtered out via :data:`_NON_EVENT_SECOND_SEGMENTS`.
    """
    return scan_emitted(forgelm_root)[0]


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

    emitted, files_scanned, emission_sites = scan_emitted(args.forgelm_root)
    catalogued = catalogued_events(args.catalog)

    emitted_names = {name for name, _ in emitted}

    # Empty-scan tripwire. Two empty sets reconcile perfectly and would print
    # OK — the precise shape of "a check that reports success without
    # examining its subject". If the source tree, the emission regex, or the
    # catalog table format ever changes such that a side reads as empty, that
    # is a broken guard, not a clean repo.
    if files_scanned == 0:
        print(f"FAIL: no *.py files found under {args.forgelm_root} — nothing was examined.", file=sys.stderr)
        return 1
    if not emitted_names:
        print(
            f"FAIL: scanned {files_scanned} file(s) under {args.forgelm_root} and matched zero event "
            "literals. The emission regex or the source tree changed; this is a broken guard, "
            "not a clean tree.",
            file=sys.stderr,
        )
        return 1
    if not catalogued:
        print(
            f"FAIL: parsed zero event rows out of {args.catalog}. The catalog table format changed "
            "and the row regex no longer matches it.",
            file=sys.stderr,
        )
        return 1

    code_only = (emitted_names - catalogued) - _CODE_ONLY_ALLOWLIST
    catalog_only = (catalogued - emitted_names) - _CATALOG_ONLY_ALLOWLIST

    if code_only or catalog_only:
        print("FAIL: audit-event catalog drift detected.")
        _print_code_only(code_only, emitted)
        _print_catalog_only(catalog_only)
        return 1

    if not args.quiet:
        print(
            f"OK: {emission_sites} emission site(s) across {files_scanned} *.py file(s) under "
            f"{args.forgelm_root} yield {len(emitted_names)} distinct event name(s), matching all "
            f"{len(catalogued)} table row(s) in {args.catalog} in both directions."
        )
        print(
            "     Scope: dotted literals in log_event()/_audit_event() first-arg, event= kwarg, "
            '"event": JSON values, and _EVT* constant declarations — Article 12 audit and webhook '
            "vocabularies alike. NOT examined: quickstart_audit.jsonl (keys on event_type) and "
            "safety_trend.jsonl (no event key). See the catalog's \"Logs this catalog does not "
            'cover" section.'
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
