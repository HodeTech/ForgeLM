# AICPA SOC 2 Trust Services Criteria — ForgeLM mapping

> Reference table summarising how ForgeLM features map to the AICPA
> SOC 2 Trust Services Criteria (2017 framework, revised 2022).
> Companion to
> [`../guides/iso_soc2_deployer_guide.md`](../guides/iso_soc2_deployer_guide.md)
> and the design doc
> [`../design/iso27001_soc2_alignment.md`](../design/iso27001_soc2_alignment.md).

## Categories

The 2017 SOC 2 framework defines 5 categories:

1. **Security** (Common Criteria CC1.x–CC9.x) — mandatory baseline.
2. **Availability** (A1.x) — optional.
3. **Processing Integrity** (PI1.x) — optional.
4. **Confidentiality** (C1.x) — optional.
5. **Privacy** (P1.x–P8.x) — optional.

A SOC 2 *Type II* engagement observes operating effectiveness over a
6–12 month window. Common Criteria are mandatory; the optional
categories are scoped per-engagement.

## Security — Common Criteria

| CC | Title | ForgeLM evidence |
|---|---|---|
| CC1.1 | Demonstrates commitment to integrity | `roles_responsibilities.md` QMS template; AI Officer role |
| CC1.2 | Independence of governance | Approval gate operator ≠ trainer |
| CC1.3 | Establishes structures, reporting lines | `roles_responsibilities.md` defines AI Officer / ML Lead / DPO |
| CC1.4 | Demonstrates commitment to competence | Annual EU AI Act / GDPR training (deployer policy) |
| CC1.5 | Enforces accountability | `FORGELM_OPERATOR` attribution + audit chain |
| CC2.1 | Communicates internally about controls | `audit_event_catalog.md` + `deployer_instructions.md` |
| CC2.2 | Communicates externally | Article 13 `deployer_instructions.md`; Annex IV public summary |
| CC2.3 | Communicates with regulators | Annex IV bundle + `compliance_report.json` are the artefacts the deployer ships to the regulator on request; ForgeLM does NOT operate the communication channel itself (deployer-side control) |
| CC3.1 | Specifies suitable objectives | `compliance.intended_purpose`; risk classification |
| CC3.2 | Identifies and analyses risks | `risk_assessment` Pydantic block; safety eval; `risk_treatment_plan.md` |
| CC3.3 | Considers fraud risks | Audit log tamper-evidence; HMAC chain; manifest sidecar |
| CC3.4 | Identifies and assesses changes | `human_approval.required/granted/rejected` audit chain; per-run `config_hash` (stamped into `compliance_report.json`, the `human_approval.required` event, and the JSON envelope); `model_integrity.json` / `model.integrity_verified` SHA-256 artifact hashes for diff; `model.revision` pins the upstream base model and `model_lineage.base_model_revision` records the commit the load used (classifier / judge / reward-model / merge-source loads remain unpinned) |
| CC4.1 | Selects, develops, performs evaluations | `forgelm verify-audit`; `forgelm safety-eval` |
| CC4.2 | Communicates internal-control deficiencies | `pipeline.failed`/`reverted`/`erasure_failed` events |
| CC5.1 | Selects, develops control activities | F-compliance-110 strict gate; auto-revert; staging |
| CC5.2 | Selects general IT controls | `safe_post` HTTP discipline; Pydantic config validation |
| CC5.3 | Deploys policies and procedures | `docs/qms/` 5 SOPs (Wave 0); 4 new in Wave 4 / Faz 23 |
| CC6.1 | Logical-access security software | Operator-id attribution; HMAC chain |
| CC6.2 | Authorises new internal users | `human_approval.required`/`granted` chain |
| CC6.3 | Removes access for terminated users | Deployer revokes CI runner identity |
| CC6.4 | Restricts physical access | OOS — datacenter security |
| CC6.5 | Protects against unauthorised disposal | `forgelm purge` Article 17; salted hashing |
| CC6.6 | Implements logical-access controls | Salted hashing in audit events; `forgelm reverse-pii` |
| CC6.7 | Restricts movement of information | `safe_post` egress discipline; webhook payload curation |
| CC6.8 | Detects/prevents unauthorised software | SBOM; `pip-audit` nightly; `bandit` CI |
| CC7.1 | Detects vulnerabilities | `pip-audit` nightly; CVE feed |
| CC7.2 | Monitors system components | `forgelm verify-audit`; `forgelm verify-gguf`; `forgelm verify-integrity`; `safety_trend.jsonl` |
| CC7.3 | Evaluates security events | `data.erasure_failed`, `pipeline.failed` events with `error_class` + `error_message` |
| CC7.4 | Responds to security events | `auto_revert`; `model.reverted` event |
| CC7.5 | Identifies, develops corrective actions | `human_approval.rejected`; `sop_change_management.md` |
| CC8.1 | Authorises changes | `forgelm approve` Article 14 gate; staging dir |
| CC9.1 | Identifies, manages risks | `risk_assessment` config + safety eval; `risk_treatment_plan.md` |
| CC9.2 | Manages vendor + business-partner risk | SBOM; dataset Hub commit SHA graded `loaded` / `unverified` / `unresolved`; base-model pin (`model.revision` → `model_lineage.base_model_revision`); license extraction.  **Not covered:** safety classifier, LLM judge, GRPO reward model, merge sources |

## Availability (A1.x)

ForgeLM is a single-node CLI; availability is dominantly
deployer-side.

| Control | ForgeLM contribution |
|---|---|
| A1.1 Capacity planning | `forgelm doctor` resource report + `resource_usage` manifest |
| A1.2 Recovery from incidents | `auto_revert` swap-back; audit chain continuity on resume |
| A1.3 Environmental protections | OOS — substrate-side |

## Processing Integrity (PI1.x)

Strong ForgeLM contribution.

| Control | ForgeLM contribution |
|---|---|
| PI1.1 Quality of inputs | `compute_dataset_fingerprint`; `data_governance_report` |
| PI1.2 System processing | `forgelm verify-audit`; `data_audit_report.json` |
| PI1.3 Outputs are accurate | `model_integrity.json` SHA-256 checksums; `model_card.md` |
| PI1.4 Inputs traceable | `compute_dataset_fingerprint` (dataset SHA-256 fingerprint) + `_fingerprint_hf_revision` (dataset HF Hub commit SHA) recorded in `data_provenance.json` and the `data_provenance` block of `compliance_report.json`.  **The SHA is evidence of the corpus that trained the model only when `hf_revision_source: loaded`;** `unverified` is a manifest-time Hub lookup with no coupling to the load, and `unresolved` means no SHA was obtained.  `_describe_adapter_method` + `config_hash` + `base_model_revision` in `model_lineage` |
| PI1.5 Outputs traceable | Annex IV bundle co-locates manifest + report + audit + integrity |

## Confidentiality (C1.x)

| Control | ForgeLM contribution |
|---|---|
| C1.1 Protection of confidential information | `forgelm audit` regex + Presidio ML-NER PII detection; `_SECRET_PATTERNS` credentials scan |
| C1.2 Disposal of confidential information | `forgelm purge` Article 17; salted-hash audit |

## Privacy (P1.x – P8.x)

| Control | ForgeLM contribution |
|---|---|
| P1.1 Privacy notice | Article 13 `deployer_instructions.md` |
| P2.1 Choice and consent | `evaluation.require_human_approval` Article 14 gate |
| P3.1 Collection | `data.governance.personal_data_included`; `dpia_completed` |
| P3.2 Quality of personal data | `data_audit_report.json` quality stats |
| P4.1 Use, retention, and disposal | `retention.staging_ttl_days` (canonical; legacy alias `evaluation.staging_ttl_days` was removed in v0.8.0); `forgelm purge --check-policy` |
| P5.1 Access | `forgelm reverse-pii` Article 15 scan; salted query-hash |
| P5.2 Inquiries and complaints | (Deployer-side workflow) |
| P6.1 Disclosure to third parties | `safe_post` webhook discipline (TLS-only, SSRF guard, redirect refusal); note: webhook bodies are NOT HMAC-signed — see ISO 27001 A.8.21 |
| P6.2 Third-party agreements | (Deployer DPAs) |
| P7.1 Breach notification | `data.erasure_failed`, `audit.classifier_load_failed` events |
| P7.2 Breach disclosure | (Deployer regulator-contact playbook) |
| P8.1 Inquiries, complaints, and disputes | `forgelm reverse-pii` + `forgelm purge` chain |

## Deployer add-ons (not in ForgeLM)

- **Credential management:** Vault for `FORGELM_AUDIT_SECRET`,
  webhook URL, API keys.
- **Evidence archive:** Write-once storage (S3 Object Lock, Azure
  Immutable Blob) + long-term retention policy.
- **Access control:** IdP integration for operator attribution; MFA
  on approval decisions.
- **Incident response:** Playbook for `data.erasure_failed`, safety
  classifier crashes, audit-chain breaks.
- **Monitoring:** SIEM ingestion of audit logs; alerting on
  high/unacceptable-risk gates; threshold tuning for safety_eval.
- **Documentation:** Risk Management file, privacy notice, deployer
  instructions distribution, Annex IV posting.

## See also

- [`../guides/iso_soc2_deployer_guide.md`](../guides/iso_soc2_deployer_guide.md) — deployer audit cookbook.
- [`../design/iso27001_soc2_alignment.md`](../design/iso27001_soc2_alignment.md) — full design rationale.
- [`iso27001_control_mapping.md`](iso27001_control_mapping.md) — ISO 27001 mapping companion.
- [`supply_chain_security.md`](supply_chain_security.md) — SBOM + pip-audit + bandit.
- [`audit_event_catalog.md`](audit_event_catalog.md) — audit-event vocabulary.
