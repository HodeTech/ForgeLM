---
title: Supply Chain
description: Operator surface for ForgeLM's SBOM + pip-audit + bandit pipeline — what runs nightly, what runs per-tag, where to fetch artefacts.
---

# Supply Chain

ForgeLM emits a CycloneDX 1.5 SBOM per release tag, and runs `pip-audit` + `bandit` nightly via the `nightly.yml` workflow (also re-run on every release-tag publish). This page is the operator's mental model: what runs when, where to get the artefacts, and how to mirror the same checks locally.

## What runs when

| Trigger | Tool | Result | Failure policy |
|---|---|---|---|
| Release tag (`v*`) | `tools/generate_sbom.py` | One CycloneDX 1.5 JSON per (OS × Python-version) cell, attached to the GitHub release | SBOM step is pure-stdlib; cannot silently degrade a green release matrix |
| Nightly 03:00 UTC | `pip-audit` (via `tools/check_pip_audit.py`) | OSV / GHSA scan against installed deps | HIGH/CRITICAL → exit 1 + GitHub issue; MEDIUM → `::warning::`; LOW → silent |
| Nightly 03:00 UTC + release tag | `bandit` (via `tools/check_bandit.py`) | Static security scan of `forgelm/` (excludes `tests/`) | HIGH → exit 1; MEDIUM → `::warning::`; LOW → silent |

## Where to get the SBOM

```bash
# All SBOMs for a release.
gh release download "$(gh release view --json tagName -q .tagName)" --pattern 'sbom-*'

# Pretty-print one.
jq . sbom-ubuntu-latest-py3.11.json | less

# Diff between two releases (lists added/removed dependencies).
diff <(jq -S '.components | sort_by(.purl)' sbom-prev.json) \
     <(jq -S '.components | sort_by(.purl)' sbom.json)

# Ingest into Dependency-Track for CVE correlation.
curl -X POST -H "X-Api-Key: $DT_KEY" -H "Content-Type: application/octet-stream" \
    --data-binary @sbom-ubuntu-latest-py3.11.json \
    https://deptrack.example.com/api/v1/bom
```

The SBOM is determinism-pinned — two consecutive emissions on the same Python environment produce content-identical JSON (modulo `serialNumber` and `metadata.timestamp`, both intentionally varying per CycloneDX semantics).

## Mirror the nightly check locally

Before pushing a PR, run the same check ForgeLM's nightly enforces:

```bash
pip install 'forgelm[security]'
pip-audit --strict --format json --output /tmp/pip-audit.json
python3 tools/check_pip_audit.py /tmp/pip-audit.json
```

Exit 0 means the same severity policy ForgeLM CI applies passed. Exit 1 means a HIGH/CRITICAL vulnerability needs addressing before the nightly fires.

## Mirror the bandit check locally

```bash
pip install 'forgelm[security]'
bandit -r forgelm/ -f json -o /tmp/bandit.json
python3 tools/check_bandit.py /tmp/bandit.json
```

`tests/` is excluded because test fixtures legitimately use insecure patterns (`assert`, dummy secrets). Production code under `forgelm/` is the scope.

## When a CVE is acknowledged but not yet fixable

If upstream has not yet released the fix and you've documented the CVE in your deployer-side risk acceptance log, write a YAML ignore file and pass it to `check_pip_audit.py` via the opt-in `--ignores` flag:

```yaml
# your_ignores.yaml
ignores:
  - id: CVE-2026-XXXX
    package: some-pkg
    reason: brief one-line summary
    threat_model: why your deployment's surface does not expose the affected API
    verified_at: '2026-05-21'
    reevaluate_after: each quarter, or when upstream ships the fix
```

```bash
pip-audit --strict --format json --output /tmp/pip-audit.json
python3 tools/check_pip_audit.py /tmp/pip-audit.json --ignores your_ignores.yaml
```

Missing any required field (`id`, `package`, `reason`, `threat_model`, `verified_at`, `reevaluate_after`) — or giving one a malformed value (empty string, a non-`YYYY-MM-DD` `verified_at`, or an `aliases` that is not a list of strings) — causes the gate to fail closed, so undocumented suppressions cannot land silently. Every match is logged as a `::notice::` annotation in the run summary.

ForgeLM does **not** ship a default project-level ignore list. The project's own nightly carries a checked-in `tools/pip_audit_ignores.yaml` (for project-internal triage), but `check_pip_audit.py` reads no ignores at all without `--ignores`, so deployers running the tool standalone inherit nothing. Every deployer-side suppression is documented in your own risk acceptance log and quarterly-reviewed.

## Where to read more

- The full reference (severity policy, suppression syntax, related ISO/SOC 2 controls):
  [`supply_chain_security.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/supply_chain_security.md) (GitHub source).
- The deployer audit cookbook (Q4 walks the SBOM, Q5 walks access controls, Q7 walks incident response):
  [`iso_soc2_deployer_guide.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/guides/iso_soc2_deployer_guide.md) (GitHub source).
- The SBOM emitter source (pure stdlib, zero deps):
  [`tools/generate_sbom.py`](https://github.com/HodeTech/ForgeLM/blob/main/tools/generate_sbom.py) (GitHub source).

## See also

- [ISO 27001 / SOC 2 Deployer](#/operations/iso-soc2-deployer) — the audit-floor cookbook entry.
- [CI/CD Pipelines](#/operations/cicd) — where the nightly + per-PR checks land.
- [Air-gap Pre-cache](#/operations/air-gap) — pre-caching deps for SBOM determinism.
