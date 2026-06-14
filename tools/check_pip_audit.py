#!/usr/bin/env python3
"""Wave 4 / Faz 23 — `pip-audit` JSON output severity gate.

Reads the JSON report produced by ``pip-audit --format json`` and
applies ForgeLM's severity policy:

- ``HIGH`` / ``CRITICAL`` findings exit 1 (fail nightly).
- ``MEDIUM`` findings emit a ``::warning::`` GitHub annotation but do
  not fail.
- ``LOW`` findings are silent.

Used in ``.github/workflows/nightly.yml`` after the ``pip-audit`` step.

Optional opt-in ignore list via ``--ignores PATH``: every finding whose
``{id} ∪ aliases`` intersects an entry in the YAML file is suppressed
(emitting a ``::notice::`` annotation that names the id and the
``reason`` field) before severity bucketing.  Deployers running this
script standalone WITHOUT ``--ignores`` inherit no suppressions, in
keeping with the deployer-side risk-acceptance policy documented in
``docs/reference/supply_chain_security.md``.  ForgeLM's own nightly
points at ``tools/pip_audit_ignores.yaml``; see that file's header for
the schema.

Exit codes (per ``tools/`` contract — NOT the public 0/1/2/3/4 surface
that ``forgelm/`` honours):

- ``0`` — no high/critical CVEs and no UNKNOWN-severity findings
  (medium/low may be present and warned).
- ``1`` — at least one high or critical CVE, OR at least one
  UNKNOWN-severity finding (F-PR29-A7-11: pip-audit's JSON omits
  severity, so UNKNOWN means we cannot prove a vulnerability is
  low-impact; failing closed avoids silent drop), OR the input file is
  missing / unparseable, OR the ignore file (when supplied) is missing,
  unparseable, or schema-invalid.

Usage::

    # Standalone (no project-side suppressions — recommended for deployers):
    pip-audit --format json --output /tmp/pip-audit.json || true
    python3 tools/check_pip_audit.py /tmp/pip-audit.json

    # Project nightly (consumes the checked-in ignore file):
    python3 tools/check_pip_audit.py /tmp/pip-audit.json \\
        --ignores tools/pip_audit_ignores.yaml

Standards-side note: this helper exists to satisfy the ``|| true`` carve-out
in ``docs/standards/testing.md`` (CI bypass discipline).  The bash
``pip-audit --format json > out.json || true`` step that calls into us is
sanctioned ONLY because this helper enforces a severity-tiered (CVE
HIGH / CRITICAL) gate on the captured output — without it, the ``|| true``
would silently swallow real findings.  Removing this helper or replacing
it with ``pip-audit`` directly would break the contract; see the
``|| true`` discipline section of ``testing.md`` before touching either
side.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Optional

# pip-audit's JSON shape puts findings under ``dependencies[].vulns``,
# each vuln carrying ``id``, ``aliases``, ``description``, ``fix_versions``.
#
# Empirical note (verified against pip-audit 2.6.0–2.9.0 wheel sources
# during Wave 4 absorption round 2): pip-audit's ``_format/json.py``
# does NOT serialise OSV severity into the JSON output — ``aliases``
# is a flat list of CVE/GHSA identifier strings (per
# ``pip_audit/_service/interface.py``: ``aliases: set[str]``), no
# nested ``severity`` field appears at any nesting level.  This means
# `_vuln_severity` returns ``"UNKNOWN"`` for every vuln in a real
# pip-audit JSON report, and the UNKNOWN summary annotation handles
# the operator-triage path.  We retain the top-level string-severity
# branch to honour the documented CLAUDE.md / pyproject.toml schema
# (operators feeding hand-crafted JSON for non-pip-audit scanners can
# emit a top-level ``severity: "HIGH"`` and have the gate honour it).
_HIGH_TIERS: frozenset[str] = frozenset({"HIGH", "CRITICAL"})
_MED_TIERS: frozenset[str] = frozenset({"MEDIUM", "MODERATE"})


def _normalise_severity(raw: Optional[str]) -> str:
    """Upper-case + collapse synonyms; unknown/missing → ``UNKNOWN``."""
    if not raw:
        return "UNKNOWN"
    upper = raw.upper().strip()
    if upper == "MODERATE":
        return "MEDIUM"
    return upper


def _vuln_severity(vuln: dict[str, Any]) -> str:
    """Extract a single severity tier from a pip-audit vuln entry.

    Honours only the top-level ``severity`` string — pip-audit's JSON
    output never carries severity (verified against 2.6.0–2.9.0 wheel
    sources).  Hand-crafted JSON from non-pip-audit scanners can set
    a top-level ``severity: "HIGH"`` and have the gate honour it.
    Anything else falls through to ``"UNKNOWN"`` and surfaces via the
    UNKNOWN summary annotation in ``main()``.
    """
    direct = vuln.get("severity")
    if isinstance(direct, str):
        return _normalise_severity(direct)
    return "UNKNOWN"


def _iter_findings(report: dict[str, Any]) -> Iterable[tuple[str, dict[str, Any]]]:
    """Yield (package_name, vuln_dict) pairs from a pip-audit report."""
    deps = report.get("dependencies") or []
    if not isinstance(deps, list):
        return
    for dep in deps:
        if not isinstance(dep, dict):
            continue
        name = dep.get("name") or "<unknown-package>"
        for vuln in dep.get("vulns") or []:
            if isinstance(vuln, dict):
                yield name, vuln


def _format_finding(name: str, vuln: dict[str, Any], severity: str) -> str:
    vid = vuln.get("id") or "<no-id>"
    fix_versions = vuln.get("fix_versions") or vuln.get("fix_version") or []
    if isinstance(fix_versions, str):
        fix_text = fix_versions
    elif isinstance(fix_versions, list) and fix_versions:
        fix_text = ", ".join(str(v) for v in fix_versions)
    else:
        fix_text = "(no fix available)"
    return f"[{severity}] {name} {vid} — fix: {fix_text}"


def _load_report(report_path: Path) -> Optional[dict[str, Any]]:
    """Read + parse the pip-audit JSON; emit ``::error::`` annotations on
    failure and return ``None``.  Caller treats ``None`` as exit 1."""
    try:
        raw = report_path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"::error::pip-audit report not readable at {report_path}: {exc}", file=sys.stderr)
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"::error::pip-audit report at {report_path} is not valid JSON: {exc}", file=sys.stderr)
        return None


# Required keys per entry in the ignore file.  Missing any of them is a
# policy violation (an undocumented suppression), so the gate fails closed
# rather than silently accepting CVEs with no recorded justification.
#
# All required fields must be non-empty strings EXCEPT ``verified_at``,
# which must additionally be an ISO ``YYYY-MM-DD`` date.  ``reevaluate_after``
# is deliberately free text (e.g. "Each release cycle, or when …"), NOT a
# date — it captures the condition that retires the ignore, which is
# rarely a fixed calendar day.
_IGNORE_REQUIRED_STR_KEYS: tuple[str, ...] = (
    "id",
    "package",
    "reason",
    "threat_model",
    "reevaluate_after",
)
_IGNORE_REQUIRED_KEYS: frozenset[str] = frozenset({*_IGNORE_REQUIRED_STR_KEYS, "verified_at"})
_VERIFIED_AT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# A CVE suppression is a security-policy exception; it must not silently
# outlive the advisory state it was justified against.  When a ``verified_at``
# is older than this many days we emit a ``::warning::`` (not a hard fail, so an
# unattended nightly does not break over a weekend) so stale suppressions
# surface on the run summary for re-triage (F-P5-OPUS-14).
_VERIFIED_AT_STALE_DAYS = 90


def _verified_at_staleness_warning(entry: dict[str, Any], ignores_path: Path, today: date) -> Optional[str]:
    """Return a ``::warning::`` body when ``entry``'s ``verified_at`` is stale.

    ``verified_at`` is already shape-validated (``YYYY-MM-DD``) by the time
    this runs, so the parse cannot fail; we still guard defensively and skip
    silently on an unexpected value rather than crash the gate.
    """
    raw = entry.get("verified_at")
    if not isinstance(raw, str):
        return None
    try:
        verified = date.fromisoformat(raw.strip())
    except ValueError:
        return None
    age_days = (today - verified).days
    if age_days > _VERIFIED_AT_STALE_DAYS:
        return (
            f"pip-audit ignore id {entry.get('id')!r} in {ignores_path} was last "
            f"verified {age_days} days ago ({raw}) — older than the "
            f"{_VERIFIED_AT_STALE_DAYS}-day re-evaluation window. Re-verify the "
            f"advisory (reevaluate_after: {entry.get('reevaluate_after')!r}) and "
            f"refresh verified_at, or drop the suppression."
        )
    return None


def _validate_ignore_entry(entry: dict[str, Any], index: int, ignores_path: Path) -> Optional[str]:
    """Return an ``::error::`` body if ``entry`` is malformed, else ``None``.

    Caller guarantees every required key is present (so the missing-key
    error can name all gaps at once); this validates value *shape* so a
    well-meaning typo cannot weaken the gate:

    - required string fields must be non-empty (an empty ``reason`` or
      ``threat_model`` is an undocumented suppression);
    - ``verified_at`` must be a ``YYYY-MM-DD`` string (quote it in YAML
      so it is not parsed as a ``datetime.date``);
    - ``aliases``, when present, must be a list of strings — a bare
      string would be unpacked character-by-character into the id index
      and pollute matching.
    """
    prefix = f"pip-audit ignore entry #{index} (id={entry.get('id')!r}) in {ignores_path}"
    for field in _IGNORE_REQUIRED_STR_KEYS:
        value = entry[field]
        if not isinstance(value, str) or not value.strip():
            return f"{prefix} field '{field}' must be a non-empty string."
    verified_at = entry["verified_at"]
    if not isinstance(verified_at, str) or not _VERIFIED_AT_RE.match(verified_at.strip()):
        return (
            f"{prefix} field 'verified_at' must be a 'YYYY-MM-DD' string "
            f"(quote it in YAML so it is not parsed as a date)."
        )
    aliases = entry.get("aliases")
    if aliases is not None and (not isinstance(aliases, list) or not all(isinstance(a, str) for a in aliases)):
        return f"{prefix} field 'aliases' must be a list of strings."
    return None


def _load_ignores(ignores_path: Path) -> Optional[dict[str, dict[str, Any]]]:
    """Read + validate ``--ignores`` YAML; return ``{id_or_alias: entry}``.

    On any failure (file missing, YAML invalid, schema invalid) emits a
    ``::error::`` annotation and returns ``None`` so the caller fails
    closed — an unreadable ignore file must not be silently treated as
    "no ignores", or every CVE would suddenly fail an otherwise green
    gate without anyone noticing the YAML drifted.
    """
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - PyYAML is a runtime dep
        print(
            f"::error::--ignores requires PyYAML (`pip install pyyaml`): {exc}",
            file=sys.stderr,
        )
        return None

    try:
        raw = ignores_path.read_text(encoding="utf-8")
    except OSError as exc:
        print(
            f"::error::pip-audit ignore file not readable at {ignores_path}: {exc}",
            file=sys.stderr,
        )
        return None

    try:
        loaded = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        print(
            f"::error::pip-audit ignore file at {ignores_path} is not valid YAML: {exc}",
            file=sys.stderr,
        )
        return None

    if not isinstance(loaded, dict):
        print(
            f"::error::pip-audit ignore file at {ignores_path} must be a mapping with key 'ignores'.",
            file=sys.stderr,
        )
        return None
    entries = loaded.get("ignores")
    if not isinstance(entries, list):
        print(
            f"::error::pip-audit ignore file at {ignores_path} must define a top-level 'ignores:' list.",
            file=sys.stderr,
        )
        return None

    by_id: dict[str, dict[str, Any]] = {}
    today = date.today()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            print(
                f"::error::pip-audit ignore entry #{index} in {ignores_path} must be a mapping.",
                file=sys.stderr,
            )
            return None
        missing = _IGNORE_REQUIRED_KEYS - entry.keys()
        if missing:
            print(
                f"::error::pip-audit ignore entry #{index} (id={entry.get('id')!r}) "
                f"in {ignores_path} is missing required field(s): "
                f"{', '.join(sorted(missing))}.",
                file=sys.stderr,
            )
            return None
        shape_error = _validate_ignore_entry(entry, index, ignores_path)
        if shape_error:
            print(f"::error::{shape_error}", file=sys.stderr)
            return None
        stale_warning = _verified_at_staleness_warning(entry, ignores_path, today)
        if stale_warning:
            # Advisory-only: a stale suppression surfaces for re-triage but does
            # not fail the gate (F-P5-OPUS-14).
            print(f"::warning::{stale_warning}")
        primary_id = entry["id"]
        # Index by every alias so cross-DB lookups (PYSEC ↔ CVE ↔ GHSA)
        # match without the workflow having to know which form pip-audit
        # emits this week.  ``aliases`` is validated as a list of strings
        # above; ``or []`` also tolerates an explicit ``aliases:`` null.
        # Last write wins on duplicates, but we surface the dup so the
        # file stays clean.
        ids = {primary_id, *(entry.get("aliases") or [])}
        for ident in ids:
            if ident in by_id and by_id[ident] is not entry:
                print(
                    f"::warning::pip-audit ignore id {ident!r} appears under "
                    f"both {by_id[ident].get('id')!r} and {primary_id!r} "
                    f"in {ignores_path}; later entry wins."
                )
            by_id[ident] = entry
    return by_id


def _vuln_identifiers(vuln: dict[str, Any]) -> set[str]:
    """Return the union ``{id} ∪ aliases`` for ignore-match purposes."""
    ids: set[str] = set()
    primary = vuln.get("id")
    if isinstance(primary, str):
        ids.add(primary)
    aliases = vuln.get("aliases")
    if isinstance(aliases, list):
        ids.update(a for a in aliases if isinstance(a, str))
    return ids


def _bucket_findings(
    report: dict[str, Any],
    ignores: Optional[dict[str, dict[str, Any]]] = None,
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Walk every (name, vuln) pair and return
    ``(high, medium, unknown, suppressed)`` lists of pre-formatted lines.

    ``suppressed`` carries findings whose ``{id} ∪ aliases`` intersected
    the ignore set; the caller surfaces each one as a ``::notice::``
    annotation so suppressions stay audit-visible.  LOW tier is silent
    — the raw JSON remains in build artefacts for post-mortem if needed.
    """
    high: list[str] = []
    medium: list[str] = []
    unknown: list[str] = []
    suppressed: list[str] = []
    # Materialise the ignore id set once rather than rebuilding the keys
    # view on every finding.
    ignore_ids: set[str] = set(ignores) if ignores else set()
    for name, vuln in _iter_findings(report):
        if ignore_ids:
            matched = _vuln_identifiers(vuln) & ignore_ids
            if matched:
                # Pick the entry by any matching id — they all point at
                # the same dict thanks to alias indexing.
                entry = ignores[next(iter(matched))]
                vid = vuln.get("id") or "<no-id>"
                suppressed.append(f"{name} {vid} — reason: {entry.get('reason')}")
                continue
        severity = _vuln_severity(vuln)
        line = _format_finding(name, vuln, severity)
        if severity in _HIGH_TIERS:
            high.append(line)
        elif severity in _MED_TIERS:
            medium.append(line)
        elif severity == "UNKNOWN":
            unknown.append(line)
    return high, medium, unknown, suppressed


def _parse_argv(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=argv[0],
        description="Apply ForgeLM's severity gate to a pip-audit JSON report.",
    )
    parser.add_argument(
        "report",
        type=Path,
        help="path to pip-audit JSON report (output of `pip-audit --format json`).",
    )
    parser.add_argument(
        "--ignores",
        type=Path,
        default=None,
        help=(
            "optional YAML file listing CVE ids to suppress (each must "
            "carry id/package/reason/threat_model/verified_at/"
            "reevaluate_after). Without this flag no suppressions are "
            "applied — deployers running this tool standalone get the "
            "full unfiltered gate."
        ),
    )
    return parser.parse_args(argv[1:])


def main(argv: list[str]) -> int:
    args = _parse_argv(argv)

    report = _load_report(args.report)
    if report is None:
        return 1

    ignores: Optional[dict[str, dict[str, Any]]] = None
    if args.ignores is not None:
        ignores = _load_ignores(args.ignores)
        if ignores is None:
            return 1

    high, medium, unknown, suppressed = _bucket_findings(report, ignores)

    for line in suppressed:
        # ::notice:: keeps every suppression in the run log so a reviewer
        # can spot-check that the ignore file hasn't accidentally hidden
        # a freshly-rescored CVE.
        print(f"::notice::pip-audit suppressed (project ignore list): {line}")

    for line in medium:
        # GitHub Actions annotation; surfaces in the run summary without
        # failing the build.
        print(f"::warning::pip-audit {line}")

    if high:
        for line in high:
            print(f"::error::pip-audit {line}")
        print(f"::error::pip-audit found {len(high)} high/critical-severity finding(s); failing the run.")
        return 1

    if unknown:
        # F-PR29-A7-11 (post-flip policy): UNKNOWN-severity findings now
        # fail the gate.  Rationale: pip-audit's JSON omits OSV severity
        # for almost every real vuln, so the previous "warn only" branch
        # converted nearly all findings into a silent advisory — operators
        # never saw the failures.  Failing closed surfaces every vuln for
        # explicit triage; if a vuln is genuinely low-impact, the operator
        # documents it (e.g. via a pip-audit ignore file or a YAML allow
        # entry) rather than relying on missing severity to skip the gate.
        for line in unknown:
            print(f"::error::pip-audit {line}")
        print(
            f"::error::pip-audit found {len(unknown)} finding(s) without parseable "
            f"severity in {args.report}; pip-audit's JSON does not serialise OSV "
            f"severity, so each must be reviewed manually (failing closed)."
        )
        return 1

    if medium:
        print(f"pip-audit: {len(medium)} medium-severity finding(s) (warning only).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
