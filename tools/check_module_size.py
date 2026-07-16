#!/usr/bin/env python3
"""Wave 2-9 — module-size ceiling guard for ``forgelm/``.

The architecture standard
(:doc:`docs/standards/architecture.md`, "~1000-line ceiling is the
trigger for a sub-package split") sets a soft cap of **1000 lines of
code** per module under ``forgelm/``.  Beyond that, the file owns too
many concerns and should be split into a sub-package
(``module_name/`` directory with the same public import path).

This guard catches **future drift** without forcing an immediate
refactor of the modules that already sit over the ceiling, recorded
in :data:`_GRANDFATHERED_OVER_CEILING`.

Grandfather policy
~~~~~~~~~~~~~~~~~~

The initial seven entries (Wave 2-9, PR #29 HEAD audit) are tracked
for a v0.6.x sub-package split (see
``docs/roadmap/risks-and-decisions.md``).

New entries added after PR #29 are admitted **only** when the
sub-package split is non-trivial enough to materially risk
behavioural regression at the release-prep stage — i.e. the kind of
split that needs its own PR / Wave with isolated tests rather than
being bundled into a feature PR.  Every new entry MUST carry:

* an inline comment naming the **phase** + **release cycle** in
  which the split lands;
* a follow-up tracking artefact (roadmap entry, issue, or
  ``risks-and-decisions.md`` row) so the deferred work can't get
  lost.

The current addition beyond the original seven is
``forgelm/cli/_pipeline.py`` (Phase 14, v0.7.0; split tracked for
v0.7.x alongside the Phase 15 audit-package split pattern).  See
that file's inline comment in :data:`_GRANDFATHERED_OVER_CEILING`
below for the tracking pointer.

LOC metric
----------
The canonical metric is **non-blank, non-comment-only lines** —
i.e. the standard "code lines" notion used by ``cloc`` / ``tokei``.
Excluded from the count:

* Blank lines.
* Lines whose stripped content starts with ``#`` (pure-comment lines,
  including shebangs and the file-level ``# noqa`` markers).

Included (intentionally):

* Module / class / function docstrings.  They are part of the file's
  review burden — a 600-line docstring still represents 600 lines of
  prose someone has to maintain — and excluding them would let
  contributors silently grow a module by inflating its docstrings.

Thresholds
----------
* **Warn at ``> 1000``** — non-fatal in default mode.  The guard
  prints a one-line warning per offender; CI may surface this as a
  soft signal.
* **Fail at ``> 1500``** — fatal (exit 1) for non-grandfathered
  modules.  A 50% over-ceiling module is an architectural emergency.
* ``--strict`` mode promotes the warn threshold to a fatal one for
  non-grandfathered modules.  Grandfathered modules continue to emit
  WARN (with the "v0.6.x defer" hint) regardless of mode.

Exit codes (per the ``tools/`` contract — NOT the public 0/1/2/3/4
surface that ``forgelm/`` honours):

* ``0`` — no NEW drift; every over-threshold module is grandfathered.
* ``1`` — at least one NEW over-threshold module (or ``--strict``
  applied to a NEW over-warn module), or invalid arguments.

Usage::

    python3 tools/check_module_size.py
    python3 tools/check_module_size.py --strict
    python3 tools/check_module_size.py --quiet
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

_WARN_THRESHOLD = 1000
_FAIL_THRESHOLD = 1500

# Modules that already exceeded the architecture-doc ceiling at PR #29
# HEAD (v0.5.5-prerelease cleanup).  Splits are deferred to v0.6.x —
# the guard's job is to prevent NEW drift, not force their refactor
# today.  When a grandfathered module is split (or trimmed below
# threshold), remove its entry here in the same PR that lands the
# split.
#
# Paths are POSIX-style relative to the repository root so behaviour
# is identical on macOS / Linux / Windows-WSL.
_GRANDFATHERED_OVER_CEILING: frozenset[str] = frozenset(
    {
        "forgelm/compliance.py",
        "forgelm/trainer.py",
        "forgelm/ingestion.py",
        "forgelm/cli/subcommands/_purge.py",
        "forgelm/config.py",
        "forgelm/cli/_parser.py",
        "forgelm/cli/subcommands/_doctor.py",
        # Phase 14 (v0.7.0) — multi-stage pipeline orchestrator
        # at ~1060 LOC: orchestrator state machine + manifest
        # builder + audit/webhook hooks + 6 helper methods that the
        # SonarCloud cognitive-complexity refactor cycle pulled out
        # of the original ``run()`` body.  A sub-package split
        # (``forgelm/cli/_pipeline/{__init__,_state,_events,
        # _verify}.py``) is tracked for the v0.7.x cycle and will
        # land alongside the Phase 15 audit-package split pattern.
        "forgelm/cli/_pipeline.py",
        # v0.9.1 — crossed 1000 LOC when generation-based Llama-Guard
        # scoring was added (the causal-LM load + moderation chat-template
        # build + safe/unsafe verdict parser + the dual classification /
        # generation aggregators, on top of the existing pipeline scorer).
        # A ``forgelm/safety/{__init__,_classify,_generate,_gates}.py``
        # sub-package split is the planned next step per the guard's own
        # advisory; grandfathered here until that lands.
        "forgelm/safety.py",
    }
)


@dataclass(frozen=True)
class _Measurement:
    """One ``forgelm/`` Python file with its measured code-line count."""

    path: str  # POSIX-relative to the repo root.
    loc: int


def _count_code_lines(path: Path) -> int:
    """Count non-blank, non-comment-only lines in a Python file.

    Docstrings are intentionally counted (see module docstring for
    rationale).  We do not parse the file as Python AST; a line-level
    classification is sufficient for the size signal and orders of
    magnitude faster on a 75-file walk.
    """
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        count += 1
    return count


def _walk_forgelm(root: Path) -> list[Path]:
    """Return all ``.py`` files under ``root``, sorted, excluding caches."""
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _measure(repo_root: Path, forgelm_root: Path) -> list[_Measurement]:
    """Walk ``forgelm/`` and return one :class:`_Measurement` per file."""
    out: list[_Measurement] = []
    for f in _walk_forgelm(forgelm_root):
        rel = f.relative_to(repo_root).as_posix()
        out.append(_Measurement(path=rel, loc=_count_code_lines(f)))
    return out


def _classify(
    measurements: Sequence[_Measurement],
) -> tuple[list[_Measurement], list[_Measurement]]:
    """Partition measurements into (over-warn, over-fail) bands.

    ``over-fail`` is a strict subset relationship: a module over the
    fail threshold is reported in ``over-fail`` only (not also in
    ``over-warn``) so callers can render the two bands without
    duplication.
    """
    over_warn: list[_Measurement] = []
    over_fail: list[_Measurement] = []
    for m in measurements:
        if m.loc > _FAIL_THRESHOLD:
            over_fail.append(m)
        elif m.loc > _WARN_THRESHOLD:
            over_warn.append(m)
    return over_warn, over_fail


def _is_grandfathered(path: str) -> bool:
    return path in _GRANDFATHERED_OVER_CEILING


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Verify no module under forgelm/ has drifted past the architecture-doc 1000-LOC sub-package-split ceiling."
        ),
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Treat the warn threshold (>1000 LOC) as fatal for "
            "non-grandfathered modules.  Grandfathered modules still "
            "emit WARN (with v0.6.x defer hint) regardless of mode."
        ),
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress the per-file WARN lines and the OK summary; print only on FAIL.",
    )
    parser.add_argument(
        "--repo-root",
        default=None,
        help=(
            "Override the repository root (defaults to the parent of "
            "the directory containing this script).  Test-only knob."
        ),
    )
    return parser


def _emit_band(  # noqa: PLR0913 — explicit args win over a config object for one call site
    *,
    new_items: list,
    grandfathered_items: list,
    threshold: int,
    threshold_label: str,
    fail_drift_text: str,
    warn_grandfathered_text: str,
    warn_new_text: Optional[str],
    strict: bool,
    quiet: bool,
) -> bool:
    """Render one threshold band (FAIL vs WARN); return ``True`` iff fatal.

    ``warn_new_text`` is ``None`` for the FAIL band (new drift is always
    fatal regardless of strict mode); supplied for the WARN band where
    ``--strict`` upgrades non-grandfathered drift to fatal."""
    fatal = False
    for m in new_items:
        if warn_new_text is None or strict:
            text = fail_drift_text if warn_new_text is None else warn_new_text
            print(
                f"FAIL: {m.path} = {m.loc} LOC (> {threshold} {threshold_label}; {text})",
                file=sys.stderr,
            )
            fatal = True
        elif not quiet:
            print(f"WARN: {m.path} = {m.loc} LOC (> {threshold} {threshold_label}; {warn_new_text})")
    if not quiet:
        for m in grandfathered_items:
            print(f"WARN: {m.path} = {m.loc} LOC (> {threshold} {threshold_label}; {warn_grandfathered_text})")
    return fatal


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Walk ``forgelm/`` and apply the size-ceiling policy.

    Returns the process exit code (0 / 1).  Centralised so tests can
    invoke ``main([...])`` without ``sys.exit``-ing the test runner.
    """
    args = _build_arg_parser().parse_args(argv)
    repo_root = Path(args.repo_root).resolve() if args.repo_root is not None else Path(__file__).resolve().parent.parent
    forgelm_root = repo_root / "forgelm"
    if not forgelm_root.is_dir():
        print(
            f"ERROR: forgelm/ source tree not found at {forgelm_root}",
            file=sys.stderr,
        )
        return 1

    measurements = _measure(repo_root, forgelm_root)
    over_warn, over_fail = _classify(measurements)

    new_over_fail = [m for m in over_fail if not _is_grandfathered(m.path)]
    grandfathered_over_fail = [m for m in over_fail if _is_grandfathered(m.path)]
    new_over_warn = [m for m in over_warn if not _is_grandfathered(m.path)]
    grandfathered_over_warn = [m for m in over_warn if _is_grandfathered(m.path)]

    fatal = _emit_band(
        new_items=new_over_fail,
        grandfathered_items=grandfathered_over_fail,
        threshold=_FAIL_THRESHOLD,
        threshold_label="fail-threshold",
        fail_drift_text="NEW drift — split into a sub-package before merge",
        warn_grandfathered_text="grandfathered, defer to v0.6.x split",
        warn_new_text=None,
        strict=args.strict,
        quiet=args.quiet,
    )
    fatal = (
        _emit_band(
            new_items=new_over_warn,
            grandfathered_items=grandfathered_over_warn,
            threshold=_WARN_THRESHOLD,
            threshold_label="warn-threshold",
            fail_drift_text="--strict mode — NEW drift, plan a sub-package split",
            warn_grandfathered_text="grandfathered, defer to v0.6.x split",
            warn_new_text="plan a sub-package split before this grows further",
            strict=args.strict,
            quiet=args.quiet,
        )
        or fatal
    )

    grandfathered_count = len(grandfathered_over_fail) + len(grandfathered_over_warn)
    if not args.quiet:
        print(
            f"Checked {len(measurements)} modules under forgelm/; "
            f"{len(over_warn)} over warn-threshold ({_WARN_THRESHOLD}), "
            f"{len(over_fail)} over fail-threshold ({_FAIL_THRESHOLD}), "
            f"{grandfathered_count} grandfathered."
        )

    return 1 if fatal else 0


if __name__ == "__main__":
    sys.exit(main())
