"""Tests for tools/check_pip_audit.py severity gate.

Pins the F-PR29-A7-11 post-flip policy: UNKNOWN severity now fails (was
silent advisory).  pip-audit's JSON shape omits OSV severity for almost
every real finding, so failing closed forces operator triage rather than
relying on missing severity to skip the gate.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_TOOL_PATH = _PROJECT_ROOT / "tools" / "check_pip_audit.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("check_pip_audit", _TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture(scope="module")
def tool():
    return _load_tool()


def _write_audit(tmp_path: Path, payload: dict) -> Path:
    p = tmp_path / "pip-audit.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def test_no_dependencies_passes(tool, tmp_path):
    """Empty pip-audit report exits 0."""
    p = _write_audit(tmp_path, {"dependencies": []})
    assert tool.main([str(_TOOL_PATH), str(p)]) == 0


def test_no_vulnerabilities_passes(tool, tmp_path):
    """Dependencies without vulns exit 0."""
    p = _write_audit(tmp_path, {"dependencies": [{"name": "pytest", "version": "8.0.0", "vulns": []}]})
    assert tool.main([str(_TOOL_PATH), str(p)]) == 0


def test_high_severity_fails(tool, tmp_path, capsys):
    """A single HIGH-severity vuln fails the gate (existing behaviour)."""
    p = _write_audit(
        tmp_path,
        {
            "dependencies": [
                {
                    "name": "synthetic-pkg",
                    "version": "1.0.0",
                    "vulns": [
                        {
                            "id": "CVE-2026-9999",
                            "severity": "HIGH",
                            "description": "synthetic test vulnerability",
                            "fix_versions": ["1.0.1"],
                        }
                    ],
                }
            ]
        },
    )
    assert tool.main([str(_TOOL_PATH), str(p)]) == 1
    captured = capsys.readouterr()
    assert "CVE-2026-9999" in captured.out
    assert "high/critical" in captured.out


def test_medium_severity_warns_does_not_fail(tool, tmp_path, capsys):
    """MEDIUM stays advisory — exit 0 with a ::warning:: annotation."""
    p = _write_audit(
        tmp_path,
        {
            "dependencies": [
                {
                    "name": "synthetic-pkg",
                    "version": "1.0.0",
                    "vulns": [
                        {
                            "id": "CVE-2026-1111",
                            "severity": "MEDIUM",
                            "description": "synthetic medium vulnerability",
                        }
                    ],
                }
            ]
        },
    )
    assert tool.main([str(_TOOL_PATH), str(p)]) == 0
    captured = capsys.readouterr()
    assert "::warning::pip-audit" in captured.out
    assert "CVE-2026-1111" in captured.out


def test_unknown_severity_fails_after_a7_11_flip(tool, tmp_path, capsys):
    """F-PR29-A7-11: UNKNOWN severity now fails (was silent advisory).

    pip-audit's JSON omits OSV severity for almost every vuln, so the
    previous warn-only branch converted real findings into noise the
    operator never saw.  Failing closed forces explicit triage.
    """
    p = _write_audit(
        tmp_path,
        {
            "dependencies": [
                {
                    "name": "synthetic-pkg",
                    "version": "1.0.0",
                    "vulns": [
                        {
                            "id": "GHSA-fake-fake-fake",
                            # No severity field -> _vuln_severity returns "UNKNOWN"
                            "description": "synthetic test vulnerability",
                        }
                    ],
                }
            ]
        },
    )
    assert tool.main([str(_TOOL_PATH), str(p)]) == 1
    captured = capsys.readouterr()
    assert "::error::pip-audit" in captured.out
    assert "GHSA-fake-fake-fake" in captured.out
    assert "without parseable" in captured.out


def test_missing_file_fails_with_error(tool, tmp_path, capsys):
    missing = tmp_path / "does-not-exist.json"
    assert tool.main([str(_TOOL_PATH), str(missing)]) == 1
    captured = capsys.readouterr()
    assert "::error::pip-audit report not readable" in captured.err


def test_invalid_json_fails_with_error(tool, tmp_path, capsys):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    assert tool.main([str(_TOOL_PATH), str(p)]) == 1
    captured = capsys.readouterr()
    assert "not valid JSON" in captured.err


def test_high_takes_precedence_over_unknown(tool, tmp_path, capsys):
    """HIGH branch still runs first — exit message names HIGH, not UNKNOWN."""
    p = _write_audit(
        tmp_path,
        {
            "dependencies": [
                {
                    "name": "pkg-high",
                    "version": "1.0.0",
                    "vulns": [{"id": "CVE-A", "severity": "HIGH"}],
                },
                {
                    "name": "pkg-unknown",
                    "version": "1.0.0",
                    "vulns": [{"id": "GHSA-B"}],
                },
            ]
        },
    )
    assert tool.main([str(_TOOL_PATH), str(p)]) == 1
    captured = capsys.readouterr()
    assert "high/critical" in captured.out


# ---------------------------------------------------------------------------
# Opt-in --ignores YAML support.
#
# Behaviour contract:
#   - Default (no --ignores) is unchanged → deployers running the tool
#     standalone get the unfiltered severity gate (existing tests above
#     still pass).
#   - --ignores PATH suppresses findings whose {id} ∪ aliases intersects
#     an ignore entry; each suppression is logged as ::notice:: so the
#     run summary still surfaces the audit trail.
#   - Schema is enforced: each entry must carry id/package/reason/
#     threat_model/verified_at/reevaluate_after — missing any field is a
#     policy violation (an undocumented suppression) and fails the gate.
# ---------------------------------------------------------------------------


def _write_ignores(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "pip_audit_ignores.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def _valid_ignore_entry(
    *,
    cve_id: str = "CVE-2026-9999",
    aliases: list[str] | None = None,
    package: str = "synthetic-pkg",
) -> str:
    lines = [
        f"  - id: {cve_id}",
        f"    package: {package}",
        "    reason: synthetic ignore for unit tests",
        "    threat_model: not reachable from any external surface",
        "    verified_at: '2026-05-21'",
        "    reevaluate_after: never (test fixture)",
    ]
    if aliases:
        lines.insert(1, f"    aliases: [{', '.join(aliases)}]")
    return "\n".join(lines)


def test_ignores_suppresses_by_primary_id(tool, tmp_path, capsys):
    """A finding whose `id` is listed is suppressed; gate exits 0."""
    audit = _write_audit(
        tmp_path,
        {
            "dependencies": [
                {
                    "name": "synthetic-pkg",
                    "version": "1.0.0",
                    "vulns": [{"id": "CVE-2026-9999", "severity": "HIGH"}],
                }
            ]
        },
    )
    ignores = _write_ignores(tmp_path, "ignores:\n" + _valid_ignore_entry())
    assert tool.main([str(_TOOL_PATH), str(audit), "--ignores", str(ignores)]) == 0
    captured = capsys.readouterr()
    assert "::notice::pip-audit suppressed" in captured.out
    assert "CVE-2026-9999" in captured.out
    # HIGH header must NOT appear — the finding was suppressed before bucketing.
    assert "high/critical" not in captured.out


def test_ignores_suppresses_by_alias(tool, tmp_path, capsys):
    """Pip-audit emits `id: PYSEC-…` with `aliases: [CVE-…]`; an
    ignore file referencing either form must match — the schema spec
    advertises `aliases:` precisely to bridge that lookup."""
    audit = _write_audit(
        tmp_path,
        {
            "dependencies": [
                {
                    "name": "torch",
                    "version": "2.12.0",
                    "vulns": [{"id": "PYSEC-2025-191", "aliases": ["CVE-2025-2953"]}],
                }
            ]
        },
    )
    # Ignore file references only the CVE alias, not the PYSEC primary id.
    ignores = _write_ignores(
        tmp_path,
        "ignores:\n" + _valid_ignore_entry(cve_id="CVE-2025-2953", aliases=["PYSEC-2025-191"], package="torch"),
    )
    assert tool.main([str(_TOOL_PATH), str(audit), "--ignores", str(ignores)]) == 0
    captured = capsys.readouterr()
    assert "PYSEC-2025-191" in captured.out
    assert "::notice::" in captured.out


def test_ignores_does_not_match_unrelated_findings(tool, tmp_path, capsys):
    """An ignore entry must not suppress a different CVE in the report.

    Catches a future refactor where the matcher accidentally became
    permissive (e.g., substring match instead of exact-id intersection).
    """
    audit = _write_audit(
        tmp_path,
        {
            "dependencies": [
                {
                    "name": "synthetic-pkg",
                    "version": "1.0.0",
                    "vulns": [{"id": "CVE-2026-0001", "severity": "HIGH"}],
                }
            ]
        },
    )
    ignores = _write_ignores(
        tmp_path,
        "ignores:\n" + _valid_ignore_entry(cve_id="CVE-2026-9999"),
    )
    assert tool.main([str(_TOOL_PATH), str(audit), "--ignores", str(ignores)]) == 1
    captured = capsys.readouterr()
    assert "::notice::" not in captured.out
    assert "CVE-2026-0001" in captured.out


def test_ignores_schema_missing_required_field_fails(tool, tmp_path, capsys):
    """An entry missing any required field must fail the gate.

    Otherwise an operator could short-circuit the policy by adding a
    bare `id: …` line without the written justification + re-evaluate
    condition the standard requires.
    """
    audit = _write_audit(tmp_path, {"dependencies": []})
    ignores = _write_ignores(
        tmp_path,
        "ignores:\n  - id: CVE-2026-0001\n    package: synthetic-pkg\n",
    )
    assert tool.main([str(_TOOL_PATH), str(audit), "--ignores", str(ignores)]) == 1
    captured = capsys.readouterr()
    err = captured.err
    assert "missing required field" in err
    # The field names that were missing should be named so the operator
    # can fix the file without re-reading the schema.
    for required in ("reason", "threat_model", "verified_at", "reevaluate_after"):
        assert required in err


def test_ignores_missing_file_fails(tool, tmp_path, capsys):
    """A nonexistent --ignores path is a hard error.

    Falling back to "no ignores" would silently turn project-side
    suppressions off, which could surface a flood of accepted-risk
    CVEs as fresh failures and obscure real regressions.
    """
    audit = _write_audit(tmp_path, {"dependencies": []})
    missing = tmp_path / "no-such-ignores.yaml"
    assert tool.main([str(_TOOL_PATH), str(audit), "--ignores", str(missing)]) == 1
    captured = capsys.readouterr()
    assert "ignore file not readable" in captured.err


def test_ignores_invalid_yaml_fails(tool, tmp_path, capsys):
    audit = _write_audit(tmp_path, {"dependencies": []})
    bad = tmp_path / "bad.yaml"
    bad.write_text("ignores:\n  - id: [unclosed", encoding="utf-8")
    assert tool.main([str(_TOOL_PATH), str(audit), "--ignores", str(bad)]) == 1
    captured = capsys.readouterr()
    assert "not valid YAML" in captured.err


def test_default_no_ignores_is_unfiltered_for_deployers(tool, tmp_path, capsys):
    """Without --ignores the gate must run the full severity policy.

    Documented contract in supply_chain_security.md: deployers
    invoking `python3 tools/check_pip_audit.py /tmp/pip-audit.json`
    standalone inherit none of the project-internal suppressions.
    """
    audit = _write_audit(
        tmp_path,
        {
            "dependencies": [
                {
                    "name": "synthetic-pkg",
                    "version": "1.0.0",
                    "vulns": [{"id": "CVE-2026-9999", "severity": "HIGH"}],
                }
            ]
        },
    )
    # No --ignores: even though this CVE id IS in the project's real
    # ignore file, a deployer invocation must still fail closed.
    assert tool.main([str(_TOOL_PATH), str(audit)]) == 1
    captured = capsys.readouterr()
    assert "::notice::" not in captured.out
    assert "CVE-2026-9999" in captured.out


def test_project_ignore_file_passes_schema_validation(tool):
    """The checked-in tools/pip_audit_ignores.yaml itself must satisfy
    the schema so the workflow never breaks on its own ignore file.

    This is a structural smoke test, not a semantic one — we only
    confirm the loader returns a non-None dict.  Bumping a CVE entry's
    semantic content (reason, dates, etc.) is reviewed by humans on PR.
    """
    project_file = _PROJECT_ROOT / "tools" / "pip_audit_ignores.yaml"
    assert project_file.exists(), "project ignore file must be checked in"
    loaded = tool._load_ignores(project_file)
    assert loaded is not None, "checked-in ignore file failed schema validation"
    # Every entry indexed under at least its primary id.
    assert "CVE-2026-1839" in loaded, (
        "the historical transformers ignore must still be present; if you removed it intentionally update this test"
    )
