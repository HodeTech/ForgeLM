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
in :data:`_DEFERRED_SPLITS`.

Deferral policy — a budget, not a due date
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Until 2026-07-20 every deferred module carried the label *"defer to
v0.6.x split"*.  That label was written honestly at v0.5.5 and was
still being printed at v0.9.1 — three minor releases after the named
cycle closed, with no module having been split and every one of them
larger than when it was grandfathered.  It is the third instance of
one rot pattern in this cycle (see ``docs/roadmap/risks-and-decisions.md``,
2026-07-20 entry): **a deferral recorded as a version literal makes a
prediction, and a prediction nobody re-reads decays into a false
statement.**

The replacement records something that cannot go stale, because it
asserts nothing about the future: each entry pins the module's
**measured LOC at the moment it was deferred** as a budget.  The
guard then enforces a *ratchet* — the deferred **file** may stay
over the ceiling, but that file may not grow past its budget.
Splitting is still the goal; holding the line is the enforceable
part.

Consequently:

* There is no version literal in this file to retarget, and no
  "planned for vX.Y" comment that a future reader has to
  cross-check against the shipped version.
* A deferred file that grows past its budget FAILS the guard in
  every mode (see "Thresholds" below).  Previously it could drift
  from 1038 to 2000 LOC emitting nothing but a WARN.
* Every entry carries a prose ``reason`` naming the concerns that
  the split would separate, so the backlog is actionable by
  someone who did not write it.

What the ratchet does and does not enforce
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The unit of enforcement is **a file**, not the concern that file
owns.  Saying so precisely matters: an earlier wording of this
docstring promised that "a deferred module may not grow", which is
true of the file and not of the concern each ``reason`` field
describes.

Concretely — a reproduction, not a hypothetical: adding a new
803-LOC sibling module — a ``_trainer_overflow.py`` dropped beside
the deferred ``forgelm/trainer.py`` — passes ``--strict`` with exit
0.  No FAIL, no WARN, and the summary still reads "0 NEW over
warn-threshold",
because 803 is under the 1000-LOC ceiling every non-deferred module
is held to.  The concern grew; no file did.

That is the intended behaviour, not a hole left unplugged.  Every
``reason`` below literally names the sibling files a split should
produce (``_kwargs``, ``_runtime``, ``_finalize``, ``_artifacts``).
A guard that charged new sibling files against the parent's budget
would penalise the exact refactor it is asking for.  The alternative
— budgeting per *concern* — requires a hand-maintained
file-to-concern map, which is precisely the rot-prone artefact the
budget mechanism was introduced to replace.

The enforced invariant, stated exactly:

* No file named in :data:`_DEFERRED_SPLITS` exceeds its recorded
  budget.
* No other file under ``forgelm/`` exceeds the warn / fail
  thresholds.

Total LOC across the tree is **not** bounded by this guard, and a
concern spread across several under-ceiling files is invisible to
it.  Noticing that is a code-review responsibility; the guard's job
is the per-file ceiling.

The ordered backlog, the honest assessment of *when* (or whether)
each split lands, and the cost estimates live in
``docs/roadmap/risks-and-decisions.md`` — deliberately in the
roadmap rather than here, so the record survives a rewrite of this
tool.  This module is the enforcement; that section is the plan.

Admitting a NEW entry
~~~~~~~~~~~~~~~~~~~~~
Only when the split is non-trivial enough to materially risk
behavioural regression if bundled into a feature PR.  Every new
entry MUST carry a ``reason`` and a ``risks-and-decisions.md`` row.

Raising a budget (the escape hatch), and how it is enforced
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Sometimes a deferred module legitimately must grow before it can be
split — a security fix in ``compliance.py`` should not be blocked on
a day-long refactor.  The escape hatch is therefore deliberately
**explicit rather than implicit**: raise the ``budget`` literal in
this file, in the same PR, with the reason appended to the entry's
``budget_history``.  That makes every grant of extra headroom a
reviewed line in a diff with a stated justification, instead of the
silent 1038 → 2147 drift that the old WARN-only policy permitted.
Lowering a budget after a trim needs no ceremony — the guard
suggests it.

That ``budget_history`` requirement is **checked, not merely
asked for**.  Each entry also carries ``deferred_at_loc``: the
measurement taken when the module was deferred.  Unlike ``budget``
it is a historical fact and is never edited, which lets the guard
detect a raise without access to the diff — ``budget >
deferred_at_loc`` means somebody granted headroom, and
:func:`_validate_entries` FAILs in every mode when the matching
``budget_history`` note is missing or blank.  A documented
requirement that nothing checks is the failure mode this whole
re-tracking exists to correct; leaving one in the mechanism that
corrects it would be self-defeating.

The residual gap, stated rather than glossed: an editor could change
``budget`` and ``deferred_at_loc`` in the same commit and defeat the
check.  That is two deliberate falsifications of a field documented
as immutable, in a small tool where a budget literal is a one-line
diff — not the zero-friction silent drift the old policy allowed.

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
* **Fail at ``> 1500``** — fatal (exit 1) for non-deferred modules.
  A 50% over-ceiling module is an architectural emergency.
* ``--strict`` mode promotes the warn threshold to a fatal one for
  non-deferred modules.
* **Deferred files: fatal on growth past their recorded budget**,
  in every mode.  Staying over the ceiling is the granted
  concession; that file growing is not.  Below budget they emit a
  WARN carrying the split plan, so the debt stays visible.  A new
  sibling file is a NEW module and is held to the thresholds above,
  not to the parent's budget — see "What the ratchet does and does
  not enforce".

Stale entries
-------------
An entry whose module has fallen back under the ceiling (with a
hysteresis margin, so a module oscillating around 1000 LOC does not
flap the build) is reported as stale and is fatal under ``--strict``:
the deferral has been paid off and the entry should be deleted, at
which point the module is held to the normal ceiling like any other.
An entry pointing at a path that no longer exists is fatal in every
mode — that is the signal a split landed (or a file moved) without
this list being updated.

Exit codes (per the ``tools/`` contract — NOT the public 0/1/2/3/4
surface that ``forgelm/`` honours):

* ``0`` — no NEW drift, and no deferred file over its budget.
* ``1`` — at least one NEW over-threshold module, a deferred file
  that grew past its budget, an entry whose budget was raised
  without a ``budget_history`` note, a dangling entry, a stale entry
  under ``--strict``, or invalid arguments.

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

# Hysteresis margin for stale-entry detection.  A deferred module is
# only called "paid off" once it is 10% below the ceiling, so a module
# hovering at 995-1005 LOC does not alternate between "over ceiling"
# and "stale entry" on consecutive commits.
_STALE_MARGIN = 0.10
_STALE_THRESHOLD = int(_WARN_THRESHOLD * (1 - _STALE_MARGIN))  # 900


@dataclass(frozen=True)
class _DeferredSplit:
    """One *file* allowed to stay over the ceiling, with a growth budget.

    The scope is the single file named by the dict key, not the
    concern ``reason`` describes: a new sibling module is NEW drift
    held to the normal ceiling, never charged against this budget.
    See the module docstring, "What the ratchet does and does not
    enforce".

    ``budget`` is the file's measured LOC at the moment of deferral
    (or at the last explicitly-reviewed raise).  Exceeding it is fatal.

    ``deferred_at_loc`` is the measurement taken when the module was
    deferred.  It is a historical fact, **never edited** — not even
    when the budget is raised — which is what lets
    :func:`_validate_entries` recognise a raise without the diff.

    ``reason`` names the concerns a split would separate — it is what
    makes this list an actionable backlog rather than an exemption
    list.  ``budget_history`` records every raise, so the diff-level
    justification survives in the file and not only in ``git log``;
    it is required whenever ``budget > deferred_at_loc``.
    """

    budget: int
    deferred_at_loc: int
    reason: str
    budget_history: tuple[str, ...] = ()


# Modules permitted to remain over the architecture-doc ceiling, each
# pinned to the LOC it measured when deferred.  There is deliberately
# no target version here — see the module docstring for why the old
# "defer to v0.6.x" labelling was removed.  The ordered backlog and the
# honest "will this actually be split soon?" assessment live in
# ``docs/roadmap/risks-and-decisions.md`` (2026-07-20 section).
#
# When a module is split or trimmed under the ceiling, delete its entry
# in the same PR that lands the change.
#
# Paths are POSIX-style relative to the repository root so behaviour
# is identical on macOS / Linux / Windows-WSL.
#
# Budgets below were re-measured on 2026-07-20.  The previous policy
# recorded no budget at all, so the growth each module accumulated
# between PR #29 (v0.5.5) and v0.9.1 is unenforceable retroactively and
# is baselined here rather than pretended away; the roadmap section
# records the deltas (e.g. compliance.py 1502 → 2147) so the cost of
# the WARN-only years is on the record.
#
# ``deferred_at_loc`` equals ``budget`` for every entry below because
# none has been raised yet.  It is NOT a duplicate to keep in sync:
# ``budget`` is the current allowance and may be edited; ``deferred_at_loc``
# is the 2026-07-20 measurement and never is.  Their divergence is how
# _validate_entries() sees a raise without reading the diff.
_DEFERRED_SPLITS: dict[str, _DeferredSplit] = {
    "forgelm/compliance.py": _DeferredSplit(
        budget=2471,
        deferred_at_loc=2147,
        reason=(
            "EU AI Act Art. 9-17 + Annex IV builder + hash-chained audit log + "
            "GDPR purge/reverse-PII primitives. Split candidates: _audit_log, "
            "_annex_iv, _provenance, _gdpr."
        ),
        budget_history=(
            "2026-07-20: 2147 -> 2471 (+324) for the pipeline-manifest audit-log "
            "corroborator (corroborate_pipeline_stage_census and helpers). The chain "
            "manifest's metadata.manifest_hash is an UNKEYED SHA-256 from a public "
            "function, so an attacker who can write the manifest can re-stamp it and "
            "erase a stage from the verifier's scrutiny; audit_log.jsonl's per-line "
            "_hmac is the only keyed integrity tag in the system. The code lands here "
            "rather than in verify.py precisely to REUSE the existing reader, chain "
            "walk and HMAC helper — a writer and a verifier that canonicalise "
            "separately is a documented hazard in this repo, and duplicating the "
            "canonicalisation to dodge a budget would be the worse trade. Pays down "
            "with the _audit_log split already named above, which this code is "
            "entirely inside.",
        ),
    ),
    "forgelm/verify.py": _DeferredSplit(
        budget=1013,
        deferred_at_loc=1013,
        reason=(
            "Three unrelated verifiers in one module: single-artefact Annex IV "
            "(field completeness + manifest hash), the pipeline chain's per-stage "
            "evidence deep-parse, and GGUF magic/metadata/sidecar integrity. Split "
            "candidates: _annex_iv, _pipeline_evidence, _gguf. Crossed the 1000-LOC "
            "ceiling on 2026-07-20 wiring the audit-log corroboration outcome into "
            "PipelineEvidenceReport; deferred rather than split in the same change "
            "because the split moves the exit-code routing tokens that the CLI and "
            "tests both pin, and that belongs in its own diff."
        ),
    ),
    "forgelm/ingestion.py": _DeferredSplit(
        budget=2110,
        deferred_at_loc=2110,
        reason=(
            "PDF/DOCX/EPUB/TXT/Markdown readers + chunkers + SFT-JSONL emitter. "
            "Split candidates: _readers, _chunkers, _pipeline."
        ),
    ),
    "forgelm/config.py": _DeferredSplit(
        budget=1795,
        deferred_at_loc=1795,
        reason=(
            "23 Pydantic models + cross-field validators + deprecation shims in one "
            "schema module. Splitting risks changing import-time validation order, so "
            "this is the highest-risk entry despite being mechanical-looking."
        ),
    ),
    "forgelm/trainer.py": _DeferredSplit(
        budget=1432,
        deferred_at_loc=1432,
        reason=(
            "ForgeTrainer god-object: TRL kwarg fold-in + OOM/DeepSpeed runtime + "
            "artifact finalisation + compliance/model-card/deployer hand-off. Split "
            "candidates: _kwargs, _runtime, _finalize, _artifacts (F-PR29-A1-05)."
        ),
    ),
    "forgelm/cli/_pipeline.py": _DeferredSplit(
        budget=1332,
        deferred_at_loc=1332,
        reason=(
            "Multi-stage pipeline orchestrator: state machine + manifest builder + "
            "audit/webhook hooks. Split candidates: _state, _events, _verify. "
            "Coupled to the Phase 14.5 manifest-verification rewrite, so splitting "
            "first would force rebasing that work."
        ),
    ),
    "forgelm/cli/_parser.py": _DeferredSplit(
        budget=1332,
        deferred_at_loc=1320,
        reason=(
            "Argparse wiring for the full CLI surface. Split candidates: _train, "
            "_inspect, _data, _run. Low behavioural risk but every subcommand's "
            "--help text is pinned by check_cli_help_consistency.py."
        ),
        budget_history=(
            "2026-07-20: 1320 -> 1332 (+12) for `safety-eval "
            "--max-safety-regression` plus the import of its default constant. The "
            "safety gate this flag drives was already live and already reachable at "
            "exit 3, but the CLI never passed the threshold, so every standalone run "
            "was gated at run_safety_evaluation's 0.05 signature default — a number "
            "an operator branching CI on exit 3 could not see in --help, could not "
            "set, and could not read back from the JSON envelope. The whole cost is "
            "one add_argument block in _add_safety_eval_subcommand; there is nowhere "
            "else an argparse flag can be declared. Pays down with the _inspect "
            "split already named above, which owns this subparser.",
        ),
    ),
    "forgelm/cli/subcommands/_purge.py": _DeferredSplit(
        budget=1215,
        deferred_at_loc=1215,
        reason=(
            "GDPR purge: row-id resolution + run-id resolution + retention-policy "
            "checks. Split candidates: _row_id, _run_id, _check_policy, _shared."
        ),
    ),
    # NOTE: ``forgelm/safety.py`` was deferred here at v0.9.1 and has since been
    # split into the ``forgelm/safety/`` sub-package (``_types``, ``_inputs``,
    # ``_generate``, ``_classifier``, ``_score_classification``,
    # ``_score_generation``, ``_gates``, ``_results``, ``_orchestrator`` behind a
    # re-exporting ``__init__``). Largest resulting module is ~20% of the
    # ceiling, so no entry is needed. Kept as a comment so the removal is
    # legible in blame rather than looking like an accidental deletion.
    #
    # NOTE: ``forgelm/cli/subcommands/_doctor.py`` was grandfathered at PR #29
    # (v0.5.5) and has since been trimmed to 950 LOC — under the ceiling. Its
    # entry was removed on 2026-07-20 during the re-tracking sweep; it had been
    # carrying a "defer to v0.6.x split" WARN for a debt that no longer existed.
    # It is now held to the normal ceiling like any other module.
}


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


def _is_deferred(path: str) -> bool:
    return path in _DEFERRED_SPLITS


def _validate_entries(entries: dict[str, _DeferredSplit]) -> bool:
    """Check the ``budget_history`` contract; return ``True`` iff fatal.

    Runs before any file is measured, because this is a policy defect
    in the list itself rather than a size problem in the tree — it is
    fatal in every mode, including ``--quiet``.

    ``deferred_at_loc`` is the immutable measurement taken at deferral,
    so ``budget > deferred_at_loc`` is exactly the signal "somebody
    granted this entry extra headroom".  The module docstring promises
    that every such grant carries a written justification; this is
    where that promise is kept rather than merely stated.

    A budget *below* ``deferred_at_loc`` means the module was trimmed
    and the budget lowered to match — that needs no justification and
    is deliberately not flagged.
    """
    fatal = False
    for path in sorted(entries):
        entry = entries[path]
        notes = [note for note in entry.budget_history if note.strip()]
        if len(notes) != len(entry.budget_history):
            print(
                f"FAIL: {path} has a blank budget_history note. Every entry in "
                f"budget_history must state why the headroom was granted; an empty "
                f"string is an unreviewed raise wearing a justification's clothes.",
                file=sys.stderr,
            )
            fatal = True
        if entry.budget > entry.deferred_at_loc and not notes:
            print(
                f"FAIL: {path} has budget {entry.budget} above its deferred_at_loc "
                f"of {entry.deferred_at_loc} (+{entry.budget - entry.deferred_at_loc}) "
                f"with an empty budget_history. Raising a budget is the documented "
                f"escape hatch, but it must be a reviewed line in a diff with a stated "
                f"reason: append that reason to this entry's budget_history in "
                f"tools/check_module_size.py. Do not edit deferred_at_loc — it records "
                f"the measurement at deferral and is what makes the raise visible.",
                file=sys.stderr,
            )
            fatal = True
    return fatal


def _emit_deferred(
    measurements: Sequence[_Measurement],
    *,
    strict: bool,
    quiet: bool,
) -> bool:
    """Apply the ratchet to every deferred module; return ``True`` iff fatal.

    Three distinct signals, deliberately not collapsed into one:

    * **over budget** — the module grew since it was deferred. Fatal in
      every mode; this is the case the old WARN-only policy missed.
    * **dangling** — the entry names a path that no longer exists,
      i.e. a split landed without this list being updated. Fatal in
      every mode.
    * **stale** — the module fell back under the ceiling (with
      hysteresis). Fatal under ``--strict``; the fix is deleting a
      line, and leaving it in place is how the list accumulates
      exemptions for debts already paid.
    """
    by_path = {m.path: m for m in measurements}
    fatal = False
    for path in sorted(_DEFERRED_SPLITS):
        entry = _DEFERRED_SPLITS[path]
        measured = by_path.get(path)
        if measured is None:
            print(
                f"FAIL: {path} is listed as a deferred split but does not exist; "
                f"remove its entry from _DEFERRED_SPLITS (a split or move landed "
                f"without updating this list).",
                file=sys.stderr,
            )
            fatal = True
            continue
        if measured.loc > entry.budget:
            print(
                f"FAIL: {path} = {measured.loc} LOC, over its deferred-split budget "
                f"of {entry.budget} (+{measured.loc - entry.budget}). A deferred "
                f"file may stay over the ceiling but may not grow past that budget "
                f"(the ratchet is per-file; a new sibling module is NEW drift held to "
                f"the normal ceiling, not charged here). Either land the "
                f"split ({entry.reason}) or raise the budget in "
                f"tools/check_module_size.py with a budget_history note saying why.",
                file=sys.stderr,
            )
            fatal = True
            continue
        if measured.loc <= _STALE_THRESHOLD:
            message = (
                f"{path} = {measured.loc} LOC is back under the ceiling "
                f"({_WARN_THRESHOLD}); delete its now-paid-off entry from "
                f"_DEFERRED_SPLITS so it is held to the normal ceiling."
            )
            if strict:
                print(f"FAIL: {message}", file=sys.stderr)
                fatal = True
            elif not quiet:
                print(f"STALE: {message}")
            continue
        if not quiet:
            headroom = entry.budget - measured.loc
            print(
                f"WARN: {path} = {measured.loc} LOC (deferred split, budget "
                f"{entry.budget}, {headroom} LOC headroom); {entry.reason}"
            )
    return fatal


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
            "Treat the warn threshold (>1000 LOC) as fatal for modules "
            "with no deferred-split entry, and treat a stale entry (module "
            "back under the ceiling) as fatal.  Deferred modules are always "
            "fatal on growth past their recorded budget, in either mode."
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


def _emit_band(
    *,
    new_items: Sequence[_Measurement],
    threshold: int,
    threshold_label: str,
    fail_drift_text: str,
    warn_new_text: Optional[str],
    strict: bool,
    quiet: bool,
) -> bool:
    """Render one threshold band for NEW drift; return ``True`` iff fatal.

    Deferred modules never reach here — they are handled by
    :func:`_emit_deferred`, which applies the budget ratchet instead of
    a bare threshold comparison.

    ``warn_new_text`` is ``None`` for the FAIL band (new drift is always
    fatal regardless of strict mode); supplied for the WARN band where
    ``--strict`` upgrades it to fatal.
    """
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

    # The list's own policy contract is checked before any measurement:
    # a budget raised without a stated justification is a defect in the
    # deferral record, independent of what the tree currently measures.
    fatal_entries = _validate_entries(_DEFERRED_SPLITS)

    measurements = _measure(repo_root, forgelm_root)

    # Deferred modules are scored against their recorded budget, not
    # against the raw thresholds, so they are removed from the band
    # classification entirely rather than being classified and then
    # exempted.
    fatal = _emit_deferred(measurements, strict=args.strict, quiet=args.quiet) or fatal_entries

    new_measurements = [m for m in measurements if not _is_deferred(m.path)]
    over_warn, over_fail = _classify(new_measurements)

    fatal = (
        _emit_band(
            new_items=over_fail,
            threshold=_FAIL_THRESHOLD,
            threshold_label="fail-threshold",
            fail_drift_text="NEW drift — split into a sub-package before merge",
            warn_new_text=None,
            strict=args.strict,
            quiet=args.quiet,
        )
        or fatal
    )
    fatal = (
        _emit_band(
            new_items=over_warn,
            threshold=_WARN_THRESHOLD,
            threshold_label="warn-threshold",
            fail_drift_text="--strict mode — NEW drift, plan a sub-package split",
            warn_new_text="plan a sub-package split before this grows further",
            strict=args.strict,
            quiet=args.quiet,
        )
        or fatal
    )

    if not args.quiet:
        deferred_loc = sum(e.budget for e in _DEFERRED_SPLITS.values())
        print(
            f"Checked {len(measurements)} modules under forgelm/; "
            f"{len(over_warn)} NEW over warn-threshold ({_WARN_THRESHOLD}), "
            f"{len(over_fail)} NEW over fail-threshold ({_FAIL_THRESHOLD}), "
            f"{len(_DEFERRED_SPLITS)} deferred splits holding "
            f"{deferred_loc} LOC of budgeted debt."
        )

    return 1 if fatal else 0


if __name__ == "__main__":
    sys.exit(main())
