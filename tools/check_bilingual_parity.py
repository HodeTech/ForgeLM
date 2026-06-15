#!/usr/bin/env python3
"""Phase 24 — bilingual EN ↔ TR doc structural parity guard.

The project's localisation standard
(:doc:`docs/standards/localization.md`) requires that every TR mirror
of a user-facing doc carry the same H1 / H2 / H3 / H4 spine as the EN
original — same count, same nesting depth, same sequence.  Translated
text differs by definition; structural drift does not.

Replaces the inline H2-only count check at
``.github/workflows/ci.yml:197-222`` with an extended H2 + H3 + H4
diff that catches the more subtle drifts the previous check missed:

- Section added in EN but not TR (or vice versa).
- H3 demoted to H4 in one mirror but not the other.
- Reordering between mirrors (operator reads them side-by-side and
  loses their place).

Usage:

    # Advisory mode — report drift, exit 0.
    python3 tools/check_bilingual_parity.py

    # Strict mode (CI gate) — exit 1 on any drift.
    python3 tools/check_bilingual_parity.py --strict

    # Restrict to one pair (useful while editing).
    python3 tools/check_bilingual_parity.py --only docs/guides/ingestion.md

Exit codes (per the parity tool's own contract — ``forgelm/`` exit
codes do NOT apply since this script lives under ``tools/``):

    0 — parity (or advisory mode reporting drift but not gating).
    1 — strict drift detected, OR invalid argument (unknown ``--only``,
        unparseable ``--levels``).

CI gate authors must NOT wrap the strict invocation in ``|| true`` —
exit 1 IS the gate.

The tool is AST-free and Pydantic-free — it only needs ``re`` and the
filesystem, so it runs in the same lint job as ``ruff`` without any
optional extra.

Pair registry: see ``_PAIRS`` below.  A new bilingual doc gets added
in two places: the registry here AND the
``check_bilingual_parity.py`` invocation in CI; the registry's
docstring documents both.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

# Pair registry.  Tuples of (EN path, TR path) keyed off the
# repository root.  When a new mirrored doc is added, register it
# here; CI will then pick it up automatically.  Order is alphabetical
# for predictable reporting.
_PAIRS: Tuple[Tuple[str, str], ...] = (
    # docs/ (top-level)
    # ``docs/roadmap.md`` ↔ ``docs/roadmap-tr.md`` is a MANDATORY mirror per
    # localization.md §"What is bilingual"; registering it here gives the
    # standard's promise a real structural-parity gate (F-P8-C-21).
    ("docs/roadmap.md", "docs/roadmap-tr.md"),
    # docs/guides/
    ("docs/guides/air_gap_deployment.md", "docs/guides/air_gap_deployment-tr.md"),
    ("docs/guides/alignment.md", "docs/guides/alignment-tr.md"),
    ("docs/guides/cicd_pipeline.md", "docs/guides/cicd_pipeline-tr.md"),
    ("docs/guides/data_audit.md", "docs/guides/data_audit-tr.md"),
    ("docs/guides/gdpr_erasure.md", "docs/guides/gdpr_erasure-tr.md"),
    ("docs/guides/getting-started.md", "docs/guides/getting-started-tr.md"),
    ("docs/guides/human_approval_gate.md", "docs/guides/human_approval_gate-tr.md"),
    ("docs/guides/ingestion.md", "docs/guides/ingestion-tr.md"),
    ("docs/guides/iso_soc2_deployer_guide.md", "docs/guides/iso_soc2_deployer_guide-tr.md"),
    ("docs/guides/library_api.md", "docs/guides/library_api-tr.md"),
    ("docs/guides/performance.md", "docs/guides/performance-tr.md"),
    ("docs/guides/pipeline.md", "docs/guides/pipeline-tr.md"),
    ("docs/guides/quickstart.md", "docs/guides/quickstart-tr.md"),
    ("docs/guides/safety_compliance.md", "docs/guides/safety_compliance-tr.md"),
    ("docs/guides/troubleshooting.md", "docs/guides/troubleshooting-tr.md"),
    # docs/qms/
    ("docs/qms/README.md", "docs/qms/README-tr.md"),
    ("docs/qms/access_control.md", "docs/qms/access_control-tr.md"),
    ("docs/qms/encryption_at_rest.md", "docs/qms/encryption_at_rest-tr.md"),
    ("docs/qms/risk_treatment_plan.md", "docs/qms/risk_treatment_plan-tr.md"),
    ("docs/qms/roles_responsibilities.md", "docs/qms/roles_responsibilities-tr.md"),
    ("docs/qms/sop_change_management.md", "docs/qms/sop_change_management-tr.md"),
    ("docs/qms/sop_data_management.md", "docs/qms/sop_data_management-tr.md"),
    ("docs/qms/sop_incident_response.md", "docs/qms/sop_incident_response-tr.md"),
    ("docs/qms/sop_model_training.md", "docs/qms/sop_model_training-tr.md"),
    ("docs/qms/statement_of_applicability.md", "docs/qms/statement_of_applicability-tr.md"),
    # docs/reference/
    ("docs/reference/approvals_subcommand.md", "docs/reference/approvals_subcommand-tr.md"),
    ("docs/reference/approve_subcommand.md", "docs/reference/approve_subcommand-tr.md"),
    ("docs/reference/architecture.md", "docs/reference/architecture-tr.md"),
    ("docs/reference/audit_event_catalog.md", "docs/reference/audit_event_catalog-tr.md"),
    ("docs/reference/cache_subcommands.md", "docs/reference/cache_subcommands-tr.md"),
    ("docs/reference/compliance_summary.md", "docs/reference/compliance_summary-tr.md"),
    ("docs/reference/configuration.md", "docs/reference/configuration-tr.md"),
    ("docs/reference/data_ingestion_architecture.md", "docs/reference/data_ingestion_architecture-tr.md"),
    ("docs/reference/data_preparation.md", "docs/reference/data_preparation-tr.md"),
    ("docs/reference/distributed_training.md", "docs/reference/distributed_training-tr.md"),
    ("docs/reference/doctor_subcommand.md", "docs/reference/doctor_subcommand-tr.md"),
    ("docs/reference/iso27001_control_mapping.md", "docs/reference/iso27001_control_mapping-tr.md"),
    ("docs/reference/library_api_reference.md", "docs/reference/library_api_reference-tr.md"),
    ("docs/reference/purge_subcommand.md", "docs/reference/purge_subcommand-tr.md"),
    ("docs/reference/reverse_pii_subcommand.md", "docs/reference/reverse_pii_subcommand-tr.md"),
    ("docs/reference/safety_eval_subcommand.md", "docs/reference/safety_eval_subcommand-tr.md"),
    ("docs/reference/soc2_trust_criteria_mapping.md", "docs/reference/soc2_trust_criteria_mapping-tr.md"),
    ("docs/reference/supply_chain_security.md", "docs/reference/supply_chain_security-tr.md"),
    ("docs/reference/usage.md", "docs/reference/usage-tr.md"),
    ("docs/reference/verify_annex_iv_subcommand.md", "docs/reference/verify_annex_iv_subcommand-tr.md"),
    ("docs/reference/verify_audit.md", "docs/reference/verify_audit-tr.md"),
    ("docs/reference/verify_gguf_subcommand.md", "docs/reference/verify_gguf_subcommand-tr.md"),
    ("docs/reference/verify_integrity_subcommand.md", "docs/reference/verify_integrity_subcommand-tr.md"),
    # docs/usermanuals/ pairs are NOT hand-listed here — they are
    # auto-discovered by ``_usermanual_pairs`` below (every
    # ``docs/usermanuals/en/**/*.md`` paired with its ``tr/`` mirror) and
    # appended to the scanned set.  Earlier this registry held only the
    # single ``training/pipelines.md`` pair, on the rationale that
    # ``tools/build_usermanuals.py`` validates the rest — but that build
    # only checks path existence and silently substitutes the EN source
    # when a TR page is missing (``used_fallback``); it never compares the
    # H2/H3/H4 spine.  So 65 of 66 usermanual pairs had no heading-spine
    # gate (F-P8-C-10).  Auto-discovery closes that gap and auto-covers
    # future pages without a registry edit.
)

# Root of the bilingual user-manual tree.  EN is the source language; TR
# mirrors live at the same relative path under ``tr/``.  Deeper locales
# (de/fr/es/zh) are deferred tiers that intentionally fall back to EN, so
# we scan ONLY the en↔tr pair here.
_USERMANUAL_EN_ROOT = "docs/usermanuals/en"
_USERMANUAL_TR_ROOT = "docs/usermanuals/tr"

# Match a Markdown ATX heading prefix: 1-6 leading hashes followed by
# at least one space.  We deliberately exclude setext headings
# (``===``/``---`` underlines) — the project's docs use ATX exclusively
# per ``docs/standards/documentation.md``, and a setext heading in a
# mirror would itself be a lint finding.
#
# Sonar python:S5852 hotspot avoidance: the previous single-shot regex
# ``^(#{1,6})\s+(.+?)\s*#*\s*$`` chained a lazy ``(.+?)`` against
# ``\s*#*\s*$``, exposing polynomial backtracking on a pathological line
# of all spaces and hashes.  We use one minimal regex for the prefix
# (single quantifier on a fixed character class) and a pure-Python
# deterministic trimmer for the optional ATX-closing run of hashes —
# no nested quantifiers anywhere on the heading-recognition path.
_HEADING_PREFIX_RE = re.compile(r"^(#{1,6}) +")

# Code fence opening / closing — heading-like lines inside a code
# block are content, not document structure.  We track ``open / close``
# state via a simple boolean toggle on triple-backtick or triple-tilde.
_FENCE_RE = re.compile(r"^(```+|~~~+)")


@dataclass(frozen=True)
class Heading:
    """One heading extracted from a markdown document."""

    level: int
    text: str
    line: int

    def signature(self) -> str:
        """Stable signature for parity reporting.

        We do NOT compare heading *text* across the mirror (the whole
        point of localisation is that the text differs).  We do
        compare nesting depth — that's the structural invariant the
        translator must preserve.
        """
        return f"H{self.level}"


def extract_headings(path: Path) -> List[Heading]:
    """Return every ATX heading in ``path``, ignoring code fences.

    Quietly returns ``[]`` when the file does not exist (callers
    surface that as a "missing mirror" diagnostic).  Other I/O errors
    propagate so the operator sees the underlying cause.
    """
    if not path.is_file():
        return []
    headings: List[Heading] = []
    in_fence = False
    fence_marker: Optional[str] = None
    with open(path, "r", encoding="utf-8") as fh:
        for line_no, raw in enumerate(fh, start=1):
            line = raw.rstrip("\n")
            fence_match = _FENCE_RE.match(line)
            if fence_match:
                marker = fence_match.group(1)
                if not in_fence:
                    # Remember the full opening run; CommonMark §4.5
                    # requires the closing fence to use the same
                    # marker character (backtick vs tilde) AND at
                    # least as many of them.  Storing the literal
                    # opener lets us enforce both invariants.
                    in_fence = True
                    fence_marker = marker
                elif fence_marker is not None and marker[0] == fence_marker[0] and len(marker) >= len(fence_marker):
                    in_fence = False
                    fence_marker = None
                continue
            if in_fence:
                continue
            prefix_match = _HEADING_PREFIX_RE.match(line)
            if prefix_match is None:
                continue
            level = len(prefix_match.group(1))
            body = line[prefix_match.end() :]
            text = _strip_atx_close_and_whitespace(body)
            if not text:
                # ``# `` or ``## ##`` with no body is not a heading we
                # want to compare structurally — skip.
                continue
            headings.append(Heading(level=level, text=text, line=line_no))
    return headings


def _strip_atx_close_and_whitespace(body: str) -> str:
    """Strip ATX-closing hashes (``Foo ##`` → ``Foo``) deterministically.

    Pure-Python O(n) walk — no regex, no backtracking surface.  Only
    treats a trailing run of hashes as an ATX close when at least one
    whitespace character precedes it, matching CommonMark §4.2.
    """
    stripped = body.rstrip()
    end = len(stripped)
    hash_start = end
    while hash_start > 0 and stripped[hash_start - 1] == "#":
        hash_start -= 1
    if hash_start < end and hash_start > 0 and stripped[hash_start - 1] in " \t":
        # ``stripped[:hash_start - 1]`` drops the separator + the run of
        # hashes; rstrip the result to clear any inner trailing space.
        return stripped[: hash_start - 1].rstrip()
    return stripped


@dataclass(frozen=True)
class PairDrift:
    """A single drift finding for an EN/TR doc pair."""

    en_path: str
    tr_path: str
    summary: str
    detail: List[str]

    def render(self) -> str:
        head = f"FAIL: {self.en_path}  ↔  {self.tr_path}"
        body = "\n".join(f"  {line}" for line in self.detail)
        return f"{head}\n  {self.summary}\n{body}" if body else f"{head}\n  {self.summary}"


def diff_pair(en_path: Path, tr_path: Path, *, levels: Sequence[int] = (2, 3, 4)) -> Optional[PairDrift]:
    """Return a :class:`PairDrift` when EN/TR structural spines diverge.

    ``levels`` selects which heading depths to compare; defaults to H2
    + H3 + H4 because that's the granularity the translation standard
    pins (H1 is the doc title and is captured by the file naming
    convention, deeper than H4 is interpreted as content not
    structure).

    Returns ``None`` when both mirrors have identical level sequences
    at the selected depths.
    """
    if not en_path.is_file():
        return PairDrift(
            en_path=str(en_path),
            tr_path=str(tr_path),
            summary=f"EN file is missing: {en_path}",
            detail=[],
        )
    if not tr_path.is_file():
        return PairDrift(
            en_path=str(en_path),
            tr_path=str(tr_path),
            summary=f"TR mirror is missing: {tr_path}",
            detail=[],
        )

    en_headings = [h for h in extract_headings(en_path) if h.level in levels]
    tr_headings = [h for h in extract_headings(tr_path) if h.level in levels]

    en_signature = [h.signature() for h in en_headings]
    tr_signature = [h.signature() for h in tr_headings]

    if en_signature == tr_signature:
        return None

    detail: List[str] = []
    # Per-level count summary first — fast scan for the operator.
    for level in levels:
        en_count = sum(1 for h in en_headings if h.level == level)
        tr_count = sum(1 for h in tr_headings if h.level == level)
        if en_count != tr_count:
            detail.append(f"H{level}: EN={en_count}  TR={tr_count}  Δ={en_count - tr_count:+d}")
    if not detail:
        # Counts match but the *sequence* of levels differs.
        detail.append(
            "Heading counts match but ordering differs — open both files side-by-side; "
            "a section was reordered or its depth changed."
        )

    # Per-line diff: cap at the first 12 mismatches to keep the report
    # readable.  Operators editing a single drift do not need to see
    # every downstream consequence.
    diff_lines = _signature_diff_lines(en_headings, tr_headings)
    if diff_lines:
        detail.append("First mismatched headings (EN | TR):")
        detail.extend(f"  {line}" for line in diff_lines[:12])
        if len(diff_lines) > 12:
            detail.append(f"  …({len(diff_lines) - 12} more, fix the first ones first)")

    summary = f"{len(diff_lines)} structural drift(s) across H{','.join(str(level) for level in levels)}"
    return PairDrift(
        en_path=str(en_path),
        tr_path=str(tr_path),
        summary=summary,
        detail=detail,
    )


def _signature_diff_lines(en: List[Heading], tr: List[Heading]) -> List[str]:
    """Return human-readable per-pair lines for headings whose level
    differs at the same index, plus orphan tails on either side."""
    lines: List[str] = []
    max_len = max(len(en), len(tr))
    for i in range(max_len):
        en_h = en[i] if i < len(en) else None
        tr_h = tr[i] if i < len(tr) else None
        if en_h is None:
            # `max_len = max(len(en), len(tr))` guarantees at least one side
            # has a heading at this index; if EN is None then TR must be set.
            # Replace the bandit-flagged ``assert`` with a defensive narrowing
            # ``if`` so the invariant holds even when Python is run with -O.
            if tr_h is None:  # pragma: no cover — invariant guard
                continue
            lines.append(f"(none)  | TR:{tr_h.signature()} L{tr_h.line} {tr_h.text!r}")
            continue
        if tr_h is None:
            lines.append(f"EN:{en_h.signature()} L{en_h.line} {en_h.text!r}  | (none)")
            continue
        if en_h.level == tr_h.level:
            continue
        lines.append(
            f"EN:{en_h.signature()} L{en_h.line} {en_h.text!r}  | TR:{tr_h.signature()} L{tr_h.line} {tr_h.text!r}"
        )
    return lines


def scan_pairs(
    pairs: Iterable[Tuple[str, str]],
    *,
    repo_root: Path,
    levels: Sequence[int] = (2, 3, 4),
) -> List[PairDrift]:
    """Run :func:`diff_pair` over every registered pair; return drifts only."""
    drifts: List[PairDrift] = []
    for en_rel, tr_rel in pairs:
        drift = diff_pair(repo_root / en_rel, repo_root / tr_rel, levels=levels)
        if drift is not None:
            drifts.append(drift)
    return drifts


def _usermanual_pairs(repo_root: Path) -> Tuple[Tuple[str, str], ...]:
    """Auto-discover every EN/TR user-manual page pair.

    Walks ``docs/usermanuals/en/**/*.md`` and pairs each page with its
    ``tr/`` mirror at the same relative path.  Returns repo-relative
    POSIX path tuples (matching the hand-written ``_PAIRS`` style) sorted
    for predictable reporting.  A missing TR mirror is still emitted as a
    pair so :func:`diff_pair` surfaces it as a "TR mirror is missing"
    drift — that is exactly the silent-EN-fallback gap this closes.
    """
    en_root = repo_root / _USERMANUAL_EN_ROOT
    if not en_root.is_dir():
        return ()
    pairs: List[Tuple[str, str]] = []
    for en_path in sorted(en_root.rglob("*.md")):
        rel = en_path.relative_to(en_root).as_posix()
        en_rel = f"{_USERMANUAL_EN_ROOT}/{rel}"
        tr_rel = f"{_USERMANUAL_TR_ROOT}/{rel}"
        pairs.append((en_rel, tr_rel))
    return tuple(pairs)


def _all_pairs(repo_root: Path) -> Tuple[Tuple[str, str], ...]:
    """The full scanned set: the explicit registry + discovered usermanuals.

    Deduplicated (a usermanual page accidentally also hand-listed in
    ``_PAIRS`` is scanned once) and order-stable.
    """
    seen: set[Tuple[str, str]] = set()
    merged: List[Tuple[str, str]] = []
    for pair in (*_PAIRS, *_usermanual_pairs(repo_root)):
        if pair in seen:
            continue
        seen.add(pair)
        merged.append(pair)
    return tuple(merged)


def _resolve_pairs(only: Optional[str], repo_root: Path) -> Tuple[Tuple[str, str], ...]:
    """When ``--only`` is supplied, return just that pair (matched on
    either side); otherwise return the full scanned set (explicit
    registry + auto-discovered user-manual pairs)."""
    all_pairs = _all_pairs(repo_root)
    if only is None:
        return all_pairs
    target = os.path.normpath(only)
    for en, tr in all_pairs:
        if os.path.normpath(en) == target or os.path.normpath(tr) == target:
            return ((en, tr),)
    raise SystemExit(
        f"--only {only!r} did not match any registered or discovered pair.  "
        f"Choose one of:\n  " + "\n  ".join(f"{en}  ↔  {tr}" for en, tr in all_pairs)
    )


def _format_summary(drifts: List[PairDrift], total_pairs: int) -> str:
    if not drifts:
        return f"OK: all {total_pairs} bilingual doc pair(s) carry the same H2/H3/H4 spine."
    return (
        f"FAIL: {len(drifts)} of {total_pairs} bilingual doc pair(s) drifted "
        "(see per-pair details above).  Edit the TR mirror so the H2/H3/H4 "
        "sequence matches the EN original; translated text may differ but "
        "structural depth + ordering must not."
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Phase 24 — verify EN/TR bilingual doc structural parity (H2 + H3 + H4).",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 on any structural drift (CI gate).  Without --strict the tool reports findings but exits 0 (advisory mode).",
    )
    parser.add_argument(
        "--only",
        type=str,
        default=None,
        metavar="PATH",
        help="Restrict the scan to one EN or TR file (matched against the registered pairs).",
    )
    parser.add_argument(
        "--levels",
        type=str,
        default="2,3,4",
        metavar="CSV",
        help="Comma-separated heading levels to compare (default: `2,3,4`).  Use `2` to mirror the legacy CI behaviour.",
    )
    parser.add_argument(
        "--repo-root",
        type=str,
        default=None,
        metavar="DIR",
        help="Repository root (default: derived from this script's path).",
    )
    args = parser.parse_args(argv)

    try:
        levels = tuple(int(part.strip()) for part in args.levels.split(",") if part.strip())
    except ValueError:
        print(
            f"--levels {args.levels!r} is not a comma-separated list of integers.",
            file=sys.stderr,
        )
        return 1
    if not levels or any(level < 1 or level > 6 for level in levels):
        print(
            f"--levels values must be 1-6 ATX heading depths; got {levels!r}.",
            file=sys.stderr,
        )
        return 1

    repo_root = Path(args.repo_root).resolve() if args.repo_root else Path(__file__).resolve().parent.parent
    pairs = _resolve_pairs(args.only, repo_root)
    drifts = scan_pairs(pairs, repo_root=repo_root, levels=levels)

    for drift in drifts:
        print(drift.render())
        print()
    print(_format_summary(drifts, len(pairs)))

    if args.strict and drifts:
        return 1
    return 0


__all__ = [
    "Heading",
    "PairDrift",
    "_PAIRS",
    "_all_pairs",
    "_usermanual_pairs",
    "diff_pair",
    "extract_headings",
    "main",
    "scan_pairs",
]


if __name__ == "__main__":
    sys.exit(main())
