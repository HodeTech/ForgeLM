"""Wave 4 / Faz 23 — supply-chain security tooling tests.

Three contracts pinned here:

1. ``tools/generate_sbom.py`` produces deterministic content for the
   same Python environment (modulo timestamp + UUID serial number,
   both intentionally non-deterministic per CycloneDX 1.5 semantics).
2. ``tools/check_pip_audit.py`` severity-tiering: high/critical → exit 1,
   medium → warning, low/unknown → silent.
3. ``tools/check_bandit.py`` severity-tiering: high → exit 1, medium →
   warning, low → silent.

The two helper-script tests exercise synthetic JSON fixtures so the
suite stays offline — no actual ``pip-audit`` or ``bandit`` run.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TOOLS = _REPO_ROOT / "tools"
_SBOM_TOOL = _TOOLS / "generate_sbom.py"
_PIP_AUDIT_TOOL = _TOOLS / "check_pip_audit.py"
_BANDIT_TOOL = _TOOLS / "check_bandit.py"


def _load_tool_module(path: Path, name: str):
    """Import a ``tools/*.py`` script as a module without polluting ``sys.path``.

    Same pattern as ``tests/test_check_bilingual_parity.py`` and
    ``tests/test_check_field_descriptions.py``.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path!r}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# §1 — generate_sbom.py determinism contract
# ---------------------------------------------------------------------------


class TestGenerateSbomDeterministic:
    """The SBOM emitter must produce content-stable output for a fixed env.

    CycloneDX 1.5 semantics intentionally vary two top-level fields
    across runs:

    - ``serialNumber`` — a fresh UUID per run (CycloneDX 1.5 spec
      treats each emit as a distinct BOM document).
    - ``metadata.timestamp`` — wall-clock at emit time.

    Stripping those two fields, the SBOM body (``components``, all
    metadata other than ``timestamp``, BOM format/version) MUST be
    byte-identical for two consecutive invocations on the same Python
    environment.  That contract is what auditors verify when asking
    "is the dependency list reproducible from the git tag?".
    """

    @staticmethod
    def _strip_volatile(sbom_json: str) -> dict:
        sbom = json.loads(sbom_json)
        sbom.pop("serialNumber", None)
        meta = sbom.get("metadata") or {}
        meta.pop("timestamp", None)
        sbom["metadata"] = meta
        return sbom

    def test_two_invocations_produce_same_content(self) -> None:
        first = subprocess.run(
            [sys.executable, str(_SBOM_TOOL)],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        second = subprocess.run(
            [sys.executable, str(_SBOM_TOOL)],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        # Modulo ``serialNumber`` + ``metadata.timestamp`` (both volatile
        # by CycloneDX 1.5 design), the two SBOMs must be content-equal.
        normalised_a = self._strip_volatile(first.stdout)
        normalised_b = self._strip_volatile(second.stdout)
        assert normalised_a == normalised_b, (
            "SBOM emitter is non-deterministic on the same environment; "
            "the content (excluding serialNumber + timestamp) must be stable "
            "so an auditor can reproduce it from the git tag"
        )

    def test_required_cyclonedx_1_5_fields_present(self) -> None:
        result = subprocess.run(
            [sys.executable, str(_SBOM_TOOL)],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        sbom = json.loads(result.stdout)
        assert sbom.get("bomFormat") == "CycloneDX"
        assert sbom.get("specVersion") == "1.5"
        assert "serialNumber" in sbom
        assert isinstance(sbom.get("components"), list)
        meta = sbom.get("metadata") or {}
        assert "timestamp" in meta
        assert (meta.get("component") or {}).get("name") == "forgelm"
        # Every component must have purl + bom-ref + name + version
        # so the SBOM is consumable by Dependency-Track / Snyk / etc.
        for comp in sbom["components"]:
            assert comp.get("type") == "library", comp
            assert comp.get("name"), comp
            assert comp.get("version"), comp
            assert comp.get("purl", "").startswith("pkg:pypi/"), comp

    def test_components_emitted_in_canonical_bom_ref_order(self) -> None:
        """F-P5-OPUS-18: components are sorted by ``bom-ref`` (PEP 503-normalized
        purl) so the emitted order is stable regardless of pip version / install
        order — the documented "re-emit from the git tag and diff" auditor flow
        then holds with a plain diff, not just after a ``jq sort_by``."""
        sbom_mod = _load_tool_module(_SBOM_TOOL, "generate_sbom_canon")
        components = sbom_mod.build_sbom()["components"]
        bom_refs = [c["bom-ref"] for c in components]
        assert bom_refs == sorted(bom_refs), (
            "SBOM components are not in canonical bom-ref order; a future refactor "
            "dropped the sort and will cause spurious ordering diffs against the "
            "released artefact"
        )


# ---------------------------------------------------------------------------
# §2 — check_pip_audit.py severity-tiering
# ---------------------------------------------------------------------------


class TestCheckPipAudit:
    """Synthetic JSON fixtures exercise each severity tier without
    needing pip-audit installed.  Real pip-audit shape captured from
    the v2.7 schema."""

    @pytest.fixture
    def tool(self):
        return _load_tool_module(_PIP_AUDIT_TOOL, "check_pip_audit")

    def _write_report(self, tmp_path: Path, payload: dict) -> Path:
        path = tmp_path / "pip-audit.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_clean_report_exits_zero(self, tmp_path: Path, tool) -> None:
        path = self._write_report(tmp_path, {"dependencies": []})
        rc = tool.main(["check_pip_audit", str(path)])
        assert rc == 0

    def test_low_severity_silent(self, tmp_path: Path, tool, capsys) -> None:
        report = {
            "dependencies": [
                {
                    "name": "example",
                    "version": "1.0.0",
                    "vulns": [
                        {"id": "GHSA-aaaa-bbbb-cccc", "severity": "LOW", "fix_versions": ["1.0.1"]},
                    ],
                }
            ]
        }
        rc = tool.main(["check_pip_audit", str(self._write_report(tmp_path, report))])
        captured = capsys.readouterr().out
        assert rc == 0
        assert "::warning::" not in captured
        assert "::error::" not in captured

    def test_medium_severity_warns_but_does_not_fail(self, tmp_path: Path, tool, capsys) -> None:
        report = {
            "dependencies": [
                {
                    "name": "midpkg",
                    "version": "2.0.0",
                    "vulns": [
                        {"id": "GHSA-mid", "severity": "MEDIUM", "fix_versions": ["2.0.1"]},
                    ],
                }
            ]
        }
        rc = tool.main(["check_pip_audit", str(self._write_report(tmp_path, report))])
        captured = capsys.readouterr().out
        assert rc == 0
        assert "::warning::pip-audit" in captured
        assert "midpkg" in captured

    def test_high_severity_fails(self, tmp_path: Path, tool, capsys) -> None:
        report = {
            "dependencies": [
                {
                    "name": "scarypkg",
                    "version": "0.1.0",
                    "vulns": [
                        {"id": "CVE-2026-XXXX", "severity": "HIGH", "fix_versions": []},
                    ],
                }
            ]
        }
        rc = tool.main(["check_pip_audit", str(self._write_report(tmp_path, report))])
        captured = capsys.readouterr().out
        assert rc == 1
        assert "::error::pip-audit" in captured
        assert "scarypkg" in captured

    def test_critical_severity_also_fails(self, tmp_path: Path, tool) -> None:
        report = {
            "dependencies": [
                {
                    "name": "scarypkg",
                    "version": "0.1.0",
                    "vulns": [
                        {"id": "CVE-2026-XXXX", "severity": "CRITICAL", "fix_versions": []},
                    ],
                }
            ]
        }
        rc = tool.main(["check_pip_audit", str(self._write_report(tmp_path, report))])
        assert rc == 1

    def test_moderate_synonym_treated_as_medium(self, tmp_path: Path, tool, capsys) -> None:
        # Some advisory feeds use "MODERATE" instead of "MEDIUM"; the
        # tier normaliser must collapse them.
        report = {
            "dependencies": [
                {
                    "name": "syn",
                    "version": "1.0.0",
                    "vulns": [{"id": "GHSA-syn", "severity": "MODERATE"}],
                }
            ]
        }
        rc = tool.main(["check_pip_audit", str(self._write_report(tmp_path, report))])
        assert rc == 0
        assert "::warning::" in capsys.readouterr().out

    def test_missing_file_fails_loudly(self, tmp_path: Path, tool, capsys) -> None:
        rc = tool.main(["check_pip_audit", str(tmp_path / "nope.json")])
        assert rc == 1
        assert "::error::pip-audit report not readable" in capsys.readouterr().err

    def test_invalid_json_fails_loudly(self, tmp_path: Path, tool, capsys) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        rc = tool.main(["check_pip_audit", str(bad)])
        assert rc == 1
        assert "::error::pip-audit report" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# §3 — check_bandit.py severity-tiering
# ---------------------------------------------------------------------------


class TestCheckBandit:
    @pytest.fixture
    def tool(self):
        return _load_tool_module(_BANDIT_TOOL, "check_bandit")

    def _write_report(self, tmp_path: Path, payload: dict) -> Path:
        path = tmp_path / "bandit.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_clean_report_exits_zero(self, tmp_path: Path, tool) -> None:
        rc = tool.main(["check_bandit", str(self._write_report(tmp_path, {"results": []}))])
        assert rc == 0

    def test_low_severity_silent(self, tmp_path: Path, tool, capsys) -> None:
        report = {
            "results": [
                {
                    "test_id": "B101",
                    "test_name": "assert_used",
                    "issue_severity": "LOW",
                    "issue_confidence": "HIGH",
                    "filename": "forgelm/x.py",
                    "line_number": 1,
                    "issue_text": "assert used",
                }
            ]
        }
        rc = tool.main(["check_bandit", str(self._write_report(tmp_path, report))])
        captured = capsys.readouterr().out
        assert rc == 0
        assert "::warning::" not in captured
        assert "::error::" not in captured

    def test_medium_severity_warns_but_does_not_fail(self, tmp_path: Path, tool, capsys) -> None:
        report = {
            "results": [
                {
                    "test_id": "B603",
                    "test_name": "subprocess_without_shell_equals_true",
                    "issue_severity": "MEDIUM",
                    "issue_confidence": "MEDIUM",
                    "filename": "forgelm/x.py",
                    "line_number": 42,
                    "issue_text": "subprocess call",
                }
            ]
        }
        rc = tool.main(["check_bandit", str(self._write_report(tmp_path, report))])
        captured = capsys.readouterr().out
        assert rc == 0
        assert "::warning::bandit" in captured
        assert "B603" in captured

    def test_high_severity_fails(self, tmp_path: Path, tool, capsys) -> None:
        report = {
            "results": [
                {
                    "test_id": "B301",
                    "test_name": "pickle",
                    "issue_severity": "HIGH",
                    "issue_confidence": "HIGH",
                    "filename": "forgelm/x.py",
                    "line_number": 10,
                    "issue_text": "pickle.loads on untrusted input",
                }
            ]
        }
        rc = tool.main(["check_bandit", str(self._write_report(tmp_path, report))])
        captured = capsys.readouterr().out
        assert rc == 1
        assert "::error::bandit" in captured
        assert "B301" in captured

    def test_missing_file_fails_loudly(self, tmp_path: Path, tool, capsys) -> None:
        rc = tool.main(["check_bandit", str(tmp_path / "nope.json")])
        assert rc == 1
        assert "::error::bandit report not readable" in capsys.readouterr().err

    def test_results_not_a_list_fails(self, tmp_path: Path, tool, capsys) -> None:
        bad = self._write_report(tmp_path, {"results": "not-a-list"})
        rc = tool.main(["check_bandit", str(bad)])
        assert rc == 1
        assert "results" in capsys.readouterr().err

    def test_results_null_fails(self, tmp_path: Path, tool, capsys) -> None:
        # F-W4-06 absorption: a report with ``"results": null`` is malformed
        # and must NOT silently pass via ``or []``.
        bad = self._write_report(tmp_path, {"results": None})
        rc = tool.main(["check_bandit", str(bad)])
        assert rc == 1
        assert "null" in capsys.readouterr().err

    def test_results_missing_key_fails(self, tmp_path: Path, tool, capsys) -> None:
        # Missing ``results`` key — distinct from ``null`` value.
        bad = self._write_report(tmp_path, {"errors": [], "metrics": {}})
        rc = tool.main(["check_bandit", str(bad)])
        assert rc == 1
        assert "missing 'results'" in capsys.readouterr().err

    def test_undefined_severity_summary_warning(self, tmp_path: Path, tool, capsys) -> None:
        # F-W4-TR-01 absorption (tightened in F-W4FU-TR-02): bandit's
        # documented UNDEFINED tier surfaces a single summary warning,
        # not per-finding spam, not silence.
        report = {
            "results": [
                {
                    "test_id": "B999",
                    "test_name": "custom_rule_no_severity",
                    # No issue_severity field → bandit emits UNDEFINED.
                    "issue_confidence": "LOW",
                    "filename": "forgelm/x.py",
                    "line_number": 1,
                    "issue_text": "novel finding",
                }
            ]
        }
        report_path = self._write_report(tmp_path, report)
        rc = tool.main(["check_bandit", str(report_path)])
        captured = capsys.readouterr().out
        assert rc == 0
        # Pin the summary phrasing exactly — a regression that flipped
        # UNDEFINED handling to per-finding ``[UNDEFINED/LOW]`` warnings
        # would still satisfy a loose substring check.
        assert "::warning::bandit 1 issue(s) with UNDEFINED severity" in captured, (
            f"expected single summary annotation; got: {captured!r}"
        )
        assert str(report_path) in captured, "summary must include the artefact path for SRE triage"
        # Negative: the per-finding format prefix MUST NOT appear.
        assert "[UNDEFINED/LOW]" not in captured, (
            "UNDEFINED issues must surface only via the summary, not per-finding warnings"
        )


class TestCheckPipAuditExtraShapes:
    """Extra pip-audit fixtures pinning the UNKNOWN summary contract.

    Wave 4 followup absorption (F-W4FU-TR-01): the speculative
    CVSS-from-score derivation + aliases-nested fallback + 4 fictional
    fixtures were dropped after the test-rigor agent's wheel inspection
    confirmed pip-audit 2.6.0–2.9.0 never serialise severity into the
    JSON output.  ``_vuln_severity`` returns ``"UNKNOWN"`` for every
    real pip-audit vuln; this class pins the operator-triage path.
    """

    @pytest.fixture
    def tool(self):
        return _load_tool_module(_PIP_AUDIT_TOOL, "check_pip_audit_extra")

    def _write_report(self, tmp_path: Path, payload: dict) -> Path:
        path = tmp_path / "pip-audit.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_missing_severity_field_summary_warning(self, tmp_path: Path, tool, capsys) -> None:
        # Real pip-audit JSON: vuln carries id + aliases (set of CVE/GHSA
        # identifier strings) + fix_versions, NO severity field at any
        # nesting level.  Gate surfaces UNKNOWN as ::error:: (not
        # ::warning::) with the artefact path included.
        #
        # F-PR29-A7-11 post-flip policy: UNKNOWN severity now FAILS (was
        # silent advisory). pip-audit's JSON omits OSV severity for almost
        # every real finding, so failing closed forces explicit operator
        # triage rather than relying on missing severity to skip the gate.
        # Test renamed-in-spirit: still pinning the UNKNOWN rendering
        # (artefact path included), but exit code is now 1 and emission
        # is `::error::` rather than `::warning::`.
        report = {
            "dependencies": [
                {
                    "name": "u",
                    "version": "1.0.0",
                    "vulns": [
                        {
                            "id": "GHSA-no-sev",
                            "fix_versions": ["1.0.1"],
                            "aliases": ["CVE-2026-NOSEV"],
                        }
                    ],
                }
            ]
        }
        report_path = self._write_report(tmp_path, report)
        rc = tool.main(["check_pip_audit", str(report_path)])
        captured = capsys.readouterr().out
        assert rc == 1
        assert "::error::pip-audit" in captured
        assert "without parseable" in captured
        assert str(report_path) in captured, (
            "UNKNOWN error summary must include the artefact path so an "
            "incident-triage SRE can grep without walking the workflow YAML"
        )

    def test_hand_crafted_top_level_severity_string_honoured(self, tmp_path: Path, tool) -> None:
        # Hand-crafted JSON from non-pip-audit scanners that emit a
        # top-level ``severity: "HIGH"`` must still fail the gate; this
        # is the only severity path real ``_vuln_severity`` honours.
        report = {
            "dependencies": [
                {
                    "name": "external-scanner",
                    "version": "1.0.0",
                    "vulns": [{"id": "EXT-2026-001", "severity": "HIGH", "fix_versions": []}],
                }
            ]
        }
        rc = tool.main(["check_pip_audit", str(self._write_report(tmp_path, report))])
        assert rc == 1, "top-level severity string is the documented escape hatch"


class TestSbomSerialNumberUniqueness:
    """F-W4-TR-08 absorption: the determinism contract strips
    serialNumber before comparing; this test pins the *uniqueness*
    side of CycloneDX 1.5 — two runs MUST emit different
    ``serialNumber`` values so Dependency-Track ingest does not see
    duplicate uploads."""

    def test_serial_number_changes_between_runs(self) -> None:
        first = subprocess.run(
            [sys.executable, str(_SBOM_TOOL)],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        second = subprocess.run(
            [sys.executable, str(_SBOM_TOOL)],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        sn_a = json.loads(first.stdout).get("serialNumber")
        sn_b = json.loads(second.stdout).get("serialNumber")
        assert sn_a and sn_b and sn_a != sn_b, (
            "serialNumber must be a fresh UUID per run per CycloneDX 1.5; "
            "Dependency-Track rejects duplicate-serial uploads"
        )


# ---------------------------------------------------------------------------
# §4 — supply_chain_security.md UNKNOWN-policy doc matches the code (F-P5-OPUS-04)
# ---------------------------------------------------------------------------


class TestSupplyChainDocMatchesPipAuditPolicy:
    """F-P5-OPUS-04 regression: ``check_pip_audit.py`` fails closed on
    UNKNOWN-severity findings (the dominant path, since pip-audit's JSON
    omits OSV severity).  The reference doc (EN + TR) must describe that
    fail-closed policy, not the pre-2026-05-24 'stays green' / 'yeşil kalır'
    advisory wording.
    """

    _EN = _REPO_ROOT / "docs" / "reference" / "supply_chain_security.md"
    _TR = _REPO_ROOT / "docs" / "reference" / "supply_chain_security-tr.md"

    @staticmethod
    def _unknown_bullet(doc_text: str) -> str:
        """Return the UNKNOWN bullet's text (line + indented continuations)."""
        lines = doc_text.splitlines()
        for idx, line in enumerate(lines):
            if "**UNKNOWN**" in line:
                block = [line]
                # Gather indented continuation lines until the next bullet.
                for cont in lines[idx + 1 :]:
                    if cont.strip().startswith("- ") or not cont.strip():
                        break
                    block.append(cont)
                return "\n".join(block)
        raise AssertionError("UNKNOWN policy bullet not found in supply-chain doc")

    def test_en_unknown_bullet_says_fail_closed_not_stays_green(self) -> None:
        bullet = self._unknown_bullet(self._EN.read_text(encoding="utf-8"))
        assert "exit 1" in bullet, f"EN UNKNOWN bullet must document the fail-closed exit code: {bullet!r}"
        assert "stays green" not in bullet, f"EN UNKNOWN bullet still claims 'stays green': {bullet!r}"

    def test_tr_unknown_bullet_says_fail_closed_not_stays_green(self) -> None:
        bullet = self._unknown_bullet(self._TR.read_text(encoding="utf-8"))
        assert "exit 1" in bullet, f"TR UNKNOWN bullet must document the fail-closed exit code: {bullet!r}"
        # The stale declarative claim was "Nightly yeşil kalır" (it stays
        # green).  The corrected text may still say "yeşil kalmak için …
        # güvenmeyin" (do not rely on staying green), so match the precise
        # stale phrasing rather than the bare substring.
        assert "yeşil kalır" not in bullet, f"TR UNKNOWN bullet still declares 'yeşil kalır': {bullet!r}"
