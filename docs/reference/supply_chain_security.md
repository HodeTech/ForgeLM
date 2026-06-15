# Supply-chain security — SBOM + vulnerability scanning

> Reference doc for ForgeLM's supply-chain security tooling: SBOM
> generation (Wave 2 era), pip-audit nightly (Wave 4 / Faz 23),
> bandit static analysis (Wave 4 / Faz 23). Audience: deployer
> compliance team + auditor.

## Software Bill of Materials (SBOM)

### Format

ForgeLM emits **CycloneDX 1.5 JSON**. Reasons:

- ISO 27001 / SOC 2 auditors accept either CycloneDX or SPDX; the
  open-source dependency-scanning ecosystem (Dependency-Track ≥ 4.10,
  OWASP Dep-Check, Snyk Open Source) consumes CycloneDX 1.5
  natively.
- The ForgeLM emitter (`tools/generate_sbom.py`) is pure-stdlib + zero
  external dependencies — the SBOM step cannot silently degrade an
  otherwise-green release matrix.
- SPDX conversion is a one-line `cyclonedx-py` invocation if a
  specific deployer requires SPDX.

### Generation

The emitter runs on every release tag (`v*` pattern) inside the
publish workflow:

```yaml
# .github/workflows/publish.yml (excerpt)
- name: Generate SBOM (CycloneDX 1.5)
  shell: bash
  run: python tools/generate_sbom.py > sbom-${{ matrix.os }}-py${{ matrix.python }}.json

- uses: actions/upload-artifact@v5
  with:
    name: sbom-${{ matrix.os }}-py${{ matrix.python }}
    path: sbom-${{ matrix.os }}-py${{ matrix.python }}.json
```

One SBOM per (OS × Python-version) cell of the cross-OS test matrix.
The artefacts are downloadable from the GitHub release page.

### Determinism contract

Wave 4 / Faz 23 adds a determinism test (`tests/test_supply_chain_security.py`):

- Two consecutive invocations on the same Python environment must
  produce content-identical SBOMs (modulo `serialNumber` and
  `metadata.timestamp`, both intentionally varying per CycloneDX
  semantics).
- `tests/test_supply_chain_security.py::TestGenerateSbomDeterministic::test_two_invocations_produce_same_content`
  pins this in CI.

An auditor can re-emit the SBOM from the corresponding `git tag`
and diff against the artefact attached to the GitHub release; any
non-trivial difference indicates dependency-resolution drift in the
build environment.

### Consuming the SBOM

```bash
# Download all SBOMs for a release. Replace v<X.Y.Z> with the tag you
# pinned in your CI; the SBOM artefact format is stable across releases.
gh release download v<X.Y.Z> --pattern 'sbom-*'

# Pretty-print for human review.
jq . sbom-ubuntu-latest-py3.11.json | less

# List every dependency name + version.
jq -r '.components[] | "\(.name) \(.version)"' sbom-ubuntu-latest-py3.11.json

# Diff between two releases.
diff <(jq -S '.components | sort_by(.purl)' sbom-prev.json) \
     <(jq -S '.components | sort_by(.purl)' sbom.json)

# Ingest into Dependency-Track.
curl -X POST -H "X-Api-Key: $DT_KEY" -H "Content-Type: application/octet-stream" \
    --data-binary @sbom-ubuntu-latest-py3.11.json \
    https://deptrack.example.com/api/v1/bom
```

## pip-audit (transitive CVE scan)

Wave 4 / Faz 23 adds `pip-audit` to the nightly workflow. Behaviour:

- Runs daily at 03:00 UTC on the `closure/wave3-integration`-derived
  development branch + main.
- Severity policy via `tools/check_pip_audit.py`:
  - **HIGH / CRITICAL** → exit 1 (fails nightly; opens a GitHub issue
    via the existing notify-failure job).
  - **MEDIUM / MODERATE** → `::warning::` annotation; nightly stays
    green.
  - **LOW** → silent.
  - **UNKNOWN** → exit 1 (fails the run, one `::error::` per finding).
    pip-audit's JSON does not carry OSV severity, so almost every real
    finding lands here — failing closed forces explicit operator triage
    rather than letting a missing-severity field silently skip the gate.
    To accept a specific CVE, document it via the opt-in ignore file
    (see Suppression below); do not rely on the UNKNOWN bucket to stay
    green.
- Uses the OSV / GHSA databases (pip-audit's default).

Operators install the same tooling locally:

```bash
pip install forgelm[security]
pip-audit --strict --format json --output /tmp/pip-audit.json
python3 tools/check_pip_audit.py /tmp/pip-audit.json
```

### Suppression (intentional CVE acceptance)

ForgeLM does NOT ship a default project-level ignore list — a
deployer running `python3 tools/check_pip_audit.py /tmp/pip-audit.json`
standalone inherits no suppressions and sees the full unfiltered
gate. Document any deployer-side acceptance in your own risk
acceptance log and pass it via the opt-in flag:

```bash
python3 tools/check_pip_audit.py /tmp/pip-audit.json \
    --ignores path/to/your_ignores.yaml
```

Each entry in the YAML file must carry `id`, `package`, `reason`,
`threat_model`, `verified_at`, and `reevaluate_after` (optional:
`aliases`, `references`); missing any required field — or giving one
a malformed value (an empty string, a `verified_at` that is not a
`YYYY-MM-DD` date, or `aliases` that is not a list of strings) —
causes the gate to fail closed, so an undocumented suppression cannot
land silently. Every match is logged as a `::notice::` annotation in
the run summary so the audit trail stays visible.

ForgeLM's own nightly does carry a checked-in
[`tools/pip_audit_ignores.yaml`](../../tools/pip_audit_ignores.yaml) for
project-internal triage (currently **two** transformers advisories
(`CVE-2026-1839` and `PYSEC-2025-217` / `CVE-2025-14929`), nine
no-fix torch advisories, and one OSV-misclassified markdown record). That file is consumed only by the
project's own workflow via `--ignores`; deployers do not inherit it.
Review at every release cycle.

## bandit (static security analysis)

Wave 4 / Faz 23 adds `bandit` to:

- `.github/workflows/ci.yml` — every PR + push to main.
- `.github/workflows/nightly.yml` — daily at 03:00 UTC.

Scope: `forgelm/` (production code only). `tests/` is excluded
because test fixtures legitimately use insecure patterns (`assert`,
dummy secrets, `pickle.loads` on test inputs).

Severity policy via `tools/check_bandit.py`:

- **HIGH** → exit 1 (fails CI / nightly).
- **MEDIUM** → `::warning::` annotation; CI / nightly stays green.
- **LOW / UNDEFINED** → silent.

Configuration in `pyproject.toml`:

```toml
[tool.bandit]
exclude_dirs = ["tests", ".venv", "build", "dist"]
skips = ["B101"]  # assert_used — irrelevant once tests/ is excluded
```

### Suppression syntax

For a specific bandit rule that legitimately fires on production
code with a documented justification:

```python
import subprocess
# nosec B603 B607 — args are literal, not operator-controlled
result = subprocess.run([sys.executable, "-m", "pip", "list"], ...)
```

Format: `# nosec <RULE>[ <RULE2>]` followed by an explanation.
Bandit ignores the line; `tools/check_bandit.py` confirms the
suppression is justified by the trailing prose.

## Optional `[security]` extra

`pyproject.toml` exposes the security tooling as an opt-in extra:

```toml
[project.optional-dependencies]
security = [
  "pip-audit>=2.7.0,<3.0.0",
  "bandit[toml]>=1.7.0,<2.0.0",
]
```

Operators install via:

```bash
pip install forgelm[security]
```

This pulls in `pip-audit` and `bandit` so the same checks ForgeLM's
nightly + CI workflows enforce can be run locally before pushing a
PR.

## Related controls

| Standard | Control | Mapping |
|---|---|---|
| ISO 27001:2022 | A.5.21 | Managing information security in the ICT supply chain |
| ISO 27001:2022 | A.8.8 | Management of technical vulnerabilities |
| SOC 2 | CC7.1 | Detects vulnerabilities |
| SOC 2 | CC8.1 | Authorises changes (SBOM diff against last release) |
| SOC 2 | CC9.2 | Manages vendor + business-partner risk |
| EU AI Act | Art. 9 risk management | Supply-chain risk part of overall risk register |

## See also

- [`../guides/iso_soc2_deployer_guide.md`](../guides/iso_soc2_deployer_guide.md) — deployer audit cookbook.
- [`iso27001_control_mapping.md`](iso27001_control_mapping.md) — ISO 27001 Annex A controls × ForgeLM evidence.
- [`soc2_trust_criteria_mapping.md`](soc2_trust_criteria_mapping.md) — SOC 2 Trust Services Criteria × ForgeLM evidence.
- [`../qms/risk_treatment_plan.md`](../qms/risk_treatment_plan.md) — pre-populated risk register.
- [`../qms/sop_change_management.md`](../qms/sop_change_management.md) — change management runbook.
- [`../qms/sop_incident_response.md`](../qms/sop_incident_response.md) — incident response runbook.
- `tools/generate_sbom.py` — CycloneDX 1.5 emitter.
- `tools/check_pip_audit.py` — pip-audit severity gate.
- `tools/check_bandit.py` — bandit severity gate.
