---
title: ISO 27001 / SOC 2 Deployer
description: Audit-floor cookbook — eight common questions, the ForgeLM artefacts that answer them, and the 93-control SoA matrix.
---

# ISO 27001 / SOC 2 Deployer

> Software cannot be ISO 27001 certified — only organisations can. ForgeLM is **aligned** with ISO 27001:2022 Annex A controls and the AICPA SOC 2 Trust Services Criteria: running ForgeLM in your training pipeline produces auditable evidence the auditor explicitly asks for. This page is the navigation entry; the deep cookbook lives in the docs tree.

## What ForgeLM gives you

Four pillars an ISO / SOC 2 auditor cares about most have direct ForgeLM evidence:

1. **Audit trail** — `audit_log.jsonl` per training run, append-only with HMAC + SHA-256 hash chain + genesis manifest sidecar. `forgelm verify-audit` validates the chain end-to-end.
2. **Change control** — Article 14 staging gate (`forgelm approve` / `reject`) + `human_approval.required/granted/rejected` audit events + `config_hash` (per-run manifest sidecar field) stamped per run.
3. **Data lineage** — `data_provenance.json` + `data_governance_report.json` together pin corpus + governance posture deterministically.
4. **Supply chain** — CycloneDX 1.5 SBOM emitted per release, `pip-audit` nightly, `bandit` CI for static + dynamic security scanning.

## The eight audit-floor questions

The deep guide answers each with the exact `jq` / CLI command + the artefact it returns:

1. "Show me the audit trail for every model promotion in the past 90 days" → `forgelm verify-audit` + `jq 'select(.event == "human_approval.granted")'`.
2. "Show me the change-control evidence — who approved this model?" → cross-reference `training.started` + `human_approval.granted` events; two distinct operator IDs prove segregation of duties (ISO A.5.3, SOC 2 CC1.5).
3. "Show me the data lineage" → `data_provenance.json`; `sha256` + `hf_revision` pin the corpus.
4. "Show me the supply chain" → `gh release download v0.5.5 --pattern 'sbom-*'`; CycloneDX 1.5 JSON.
5. "Show me the access controls" → IdP audit log + `FORGELM_OPERATOR` cross-reference.
6. "Show me the encryption posture" → deployer-side substrate (KMS audit log + `data_governance_report.json`).
7. "Show me the incident response" → `audit.classifier_load_failed` + F-compliance-110 strict gate.
8. "Show me you can respond to GDPR Article 15 + 17" → `forgelm reverse-pii` + `forgelm purge`.

## Where to read more

All of the following live alongside the toolkit on GitHub — they ship in the repository but are not part of this in-manual viewer.

- The deep guide — full deployer cookbook, twelve-item setup checklist, common pitfalls:
  [`iso_soc2_deployer_guide.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/guides/iso_soc2_deployer_guide.md)
- Full ISO 27001:2022 Annex A control mapping × ForgeLM evidence:
  [`iso27001_control_mapping.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/iso27001_control_mapping.md)
- Full SOC 2 Trust Services Criteria mapping:
  [`soc2_trust_criteria_mapping.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/soc2_trust_criteria_mapping.md)
- Pre-populated 93-control Statement of Applicability matrix:
  [`statement_of_applicability.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/qms/statement_of_applicability.md)
- Pre-populated risk register:
  [`risk_treatment_plan.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/qms/risk_treatment_plan.md)
- Substrate-side encryption guidance:
  [`encryption_at_rest.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/qms/encryption_at_rest.md)
- Operator identity + secrets management:
  [`access_control.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/qms/access_control.md)
- Incident response runbook:
  [`sop_incident_response.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/qms/sop_incident_response.md)
- Change management runbook:
  [`sop_change_management.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/qms/sop_change_management.md)

## See also

- [Audit Log](#/compliance/audit-log) — the append-only chain the auditor walks.
- [Human Oversight](#/compliance/human-oversight) — Article 14 staging gate.
- [Supply Chain](#/operations/supply-chain) — SBOM + pip-audit + bandit operator surface.
- [CI/CD Pipelines](#/operations/cicd) — how the pipeline produces the evidence.
