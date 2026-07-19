# ISO/IEC 27001:2022 Annex A — ForgeLM control mapping

> Reference table summarising how ForgeLM features map to ISO 27001:2022
> Annex A controls. Companion to
> [`../guides/iso_soc2_deployer_guide.md`](../guides/iso_soc2_deployer_guide.md)
> and the design doc
> [`../design/iso27001_soc2_alignment.md`](../design/iso27001_soc2_alignment.md).
>
> **Coverage tier legend:**
>
> - **`FL`** — *ForgeLM-supported*: ForgeLM directly produces audit evidence.
> - **`FL-helps`** — *Deployer responsibility, ForgeLM helps*: ForgeLM
>   provides partial evidence the deployer combines with other sources.
> - **`OOS`** — *Out of scope*: deployer-only, ForgeLM contributes nothing.
>
> **Coverage tally for this version:** FL 11 / FL-helps 48 / OOS 34
> (row-by-row recount of §3.1–§3.4 in the design doc;
> per-theme split A.5: 3 / 24 / 10, A.6: 0 / 5 / 3, A.7: 0 / 0 / 14,
> A.8: 8 / 19 / 7).

## A.5 Organisational controls (37)

| Control | Tier | ForgeLM evidence |
|---|---|---|
| A.5.1 Policies for information security | FL-helps | `audit_event_catalog.md` documents what's logged |
| A.5.2 Information security roles and responsibilities | FL-helps | `FORGELM_OPERATOR` + `roles_responsibilities.md` |
| A.5.3 Segregation of duties | FL-helps | `human_approval.required/granted` enforces trainer ≠ approver |
| A.5.4 Management responsibilities | FL-helps | `forgelm doctor`; `compliance_report.json`; `training_manifest.yaml` |
| A.5.5 Contact with authorities | OOS | — |
| A.5.6 Contact with special interest groups | OOS | — |
| A.5.7 Threat intelligence | OOS | — |
| A.5.8 Information security in project management | FL-helps | `risk_assessment` config; F-compliance-110 strict gate; Annex IV §9 |
| A.5.9 Inventory of information and other associated assets | FL-helps | `data_provenance.json`; `model_integrity.json`; SBOM |
| A.5.10 Acceptable use of information | OOS | — |
| A.5.11 Return of assets | OOS | — |
| A.5.12 Classification of information | FL-helps | `compliance.risk_classification` 5-tier |
| A.5.13 Labelling of information | FL-helps | `model_card.md`; risk class in manifest |
| A.5.14 Information transfer | FL-helps | `safe_post` webhook discipline |
| A.5.15 Access control | FL-helps | Operator identity + salted hashing |
| A.5.16 Identity management | FL-helps | `FORGELM_OPERATOR` env contract |
| A.5.17 Authentication information | FL-helps | `safe_post` masks auth headers; `_mask` hides tokens |
| A.5.18 Access rights | FL-helps | `human_approval` gate |
| A.5.19 Information security in supplier relationships | FL-helps | Dataset Hub commit SHA (`_fingerprint_hf_revision`, graded `loaded` / `unverified` / `local_path` / `unresolved`); base-model pin `model.revision` → `model_lineage.base_model_revision`; SBOM lists every dep.  `judge_model_revision` and `grpo_reward_model_revision` pin the judge and GRPO reward loads, though neither pin is recorded in any artefact.  Classifier and merge-source loads remain unpinned |
| A.5.20 Addressing information security within supplier agreements | OOS | — |
| A.5.21 Managing information security in the ICT supply chain | FL-helps | SBOM (CycloneDX 1.5); `pip-audit` nightly |
| A.5.22 Monitoring, review and change management of supplier services | OOS | — |
| A.5.23 Information security for use of cloud services | OOS | — |
| A.5.24 Information security incident management planning and preparation | FL-helps | `sop_incident_response.md`; audit chain preserves state |
| A.5.25 Assessment and decision on information security events | FL-helps | `data.erasure_failed`, `pipeline.failed`, `audit.classifier_load_failed` |
| A.5.26 Response to information security incidents | FL-helps | Audit chain HMAC preserves before/after |
| A.5.27 Learning from information security incidents | FL-helps | `model.reverted` events accumulate post-mortem evidence |
| A.5.28 Collection of evidence | FL | `audit_log.jsonl` forensic-grade; `forgelm verify-audit` validates |
| A.5.29 Information security during disruption | FL-helps | `auto_revert` baseline-flip; `model.reverted` event |
| A.5.30 ICT readiness for business continuity | OOS | — |
| A.5.31 Identification of legal, statutory, regulatory and contractual requirements | FL-helps | EU AI Act + GDPR mappings; Annex IV bundle |
| A.5.32 Intellectual property rights | FL-helps | License extraction in SBOM; HF model-card metadata |
| A.5.33 Protection of records | FL | Append-only + HMAC + manifest sidecar |
| A.5.34 Privacy and protection of PII | FL | `forgelm reverse-pii` Article 15; `forgelm purge` Article 17 (see also A.8.3) |
| A.5.35 Independent review of information security | OOS | — |
| A.5.36 Compliance with policies, rules and standards for information security | FL-helps | Pydantic config validation; `forgelm doctor`; CI gates |
| A.5.37 Documented operating procedures | FL-helps | `docs/qms/` SOPs |

## A.6 People controls (8)

| Control | Tier | ForgeLM evidence |
|---|---|---|
| A.6.1 Screening | OOS | — |
| A.6.2 Terms and conditions of employment | OOS | — |
| A.6.3 Information security awareness, education and training | FL-helps | `audit_event_catalog.md` doubles as training material |
| A.6.4 Disciplinary process | FL-helps | Operator attribution preserves accountability |
| A.6.5 Responsibilities after termination or change of employment | FL-helps | Operator id rotation; old IDs remain in audit history |
| A.6.6 Confidentiality or non-disclosure agreements | OOS | — |
| A.6.7 Remote working | FL-helps | `forgelm doctor --offline`; air-gap pre-cache |
| A.6.8 Information security event reporting | FL-helps | `pipeline.failed`, `data.erasure_failed`, `audit.classifier_load_failed`; webhook |

## A.7 Physical controls (14)

All A.7 controls are **OOS** (ForgeLM is software). Listed for
completeness so the deployer's SoA is auditable end-to-end.

| Control | Tier |
|---|---|
| A.7.1 Physical perimeters | OOS |
| A.7.2 Physical entry | OOS |
| A.7.3 Securing offices, rooms and facilities | OOS |
| A.7.4 Physical security monitoring | OOS |
| A.7.5 Protecting against physical and environmental threats | OOS |
| A.7.6 Working in secure areas | OOS |
| A.7.7 Clear desk and clear screen | OOS |
| A.7.8 Equipment siting and protection | OOS |
| A.7.9 Security of assets off-premises | OOS |
| A.7.10 Storage media | OOS |
| A.7.11 Supporting utilities | OOS |
| A.7.12 Cabling security | OOS |
| A.7.13 Equipment maintenance | OOS |
| A.7.14 Secure disposal or re-use of equipment | OOS |

## A.8 Technological controls (34)

| Control | Tier | ForgeLM evidence |
|---|---|---|
| A.8.1 User endpoint devices | FL-helps | `forgelm doctor` env summary |
| A.8.2 Privileged access rights | FL-helps | Operator attribution; approval-gate operator separation |
| A.8.3 Information access restriction | FL | Salted identifier hashing; `forgelm reverse-pii` Article 15 |
| A.8.4 Access to source code | FL-helps | `model.trust_remote_code=False` default; `model.revision` pins the base-model repo, which bounds the repo-bundled code `trust_remote_code: true` would run.  `_fingerprint_hf_revision` covers the **dataset** repo only and is not evidence about executable code |
| A.8.5 Secure authentication | FL-helps | `safe_post` rejects auth headers on non-HTTPS |
| A.8.6 Capacity management | FL-helps | `forgelm doctor` resource report; `resource_usage` manifest block |
| A.8.7 Protection against malware | OOS | — |
| A.8.8 Management of technical vulnerabilities | FL-helps | SBOM; `pip-audit` nightly; `bandit` CI |
| A.8.9 Configuration management | FL | YAML validated via Pydantic (`extra="forbid"` on every config block); `forgelm --dry-run` resolves and validates without training; per-run `config_hash` (see `compute_config_hash`) bound into `training_manifest.yaml`, the `human_approval.required` audit event, and the JSON output envelope; output artifact SHA-256 hashes in `model_integrity.json` (`model.integrity_verified` audit event) |
| A.8.10 Information deletion | FL | `forgelm purge` Article 17; salted-hash audit; `data.erasure_warning_memorisation` |
| A.8.11 Data masking | FL | `forgelm audit` regex + Presidio ML-NER |
| A.8.12 Data leakage prevention | FL | `forgelm reverse-pii` plaintext residual scan |
| A.8.13 Information backup | FL-helps | `audit_log.jsonl` + manifest backupable |
| A.8.14 Redundancy of information processing facilities | OOS | — |
| A.8.15 Logging | FL | `audit_log.jsonl` JSON Lines + HMAC + genesis manifest |
| A.8.16 Monitoring activities | FL-helps | Webhook lifecycle events; `safety_trend.jsonl` cross-run trend |
| A.8.17 Clock synchronisation | FL-helps | All audit entries ISO-8601 UTC |
| A.8.18 Use of privileged utility programs | OOS | — |
| A.8.19 Installation of software on operational systems | FL-helps | `forgelm doctor` packages; `pyproject.toml` pins |
| A.8.20 Networks security | FL-helps | `safe_post` HTTPS-only / SSRF guard / no-redirect |
| A.8.21 Security of network services | FL-helps | TLS-only webhooks (HTTPS + SSRF guard); audit-log chain HMAC via `FORGELM_AUDIT_SECRET` (note: webhook **bodies** are not HMAC-signed) |
| A.8.22 Segregation of networks | OOS | — |
| A.8.23 Web filtering | OOS | — |
| A.8.24 Use of cryptography | FL | SHA-256 + HMAC chain (per-run signing key = `SHA-256(FORGELM_AUDIT_SECRET ‖ run_id)`, see `forgelm/compliance.py:104-114`); separately, salted SHA-256 identifier hashing for purge / reverse-pii |
| A.8.25 Secure development life cycle | FL-helps | `docs/standards/code-review.md`, `release.md`, CI gates |
| A.8.26 Application security requirements | FL-helps | F-compliance-110 strict gate; ReDoS guard |
| A.8.27 Secure system architecture and engineering principles | FL-helps | Append-only audit log architecture |
| A.8.28 Secure coding | FL-helps | `docs/standards/coding.md`; type hints; CommonMark escaping |
| A.8.29 Security testing in development and acceptance | FL-helps | `pytest` ~1493 tests; `bandit` static analysis |
| A.8.30 Outsourced development | OOS | — |
| A.8.31 Separation of development, test and production environments | FL-helps | `forgelm --dry-run`; staging dir |
| A.8.32 Change management | FL | `human_approval.required/granted/rejected` audit chain; staging snapshot retained until promotion; SHA-256 artifact hashes in `model_integrity.json` (`model.integrity_verified` audit event) let `forgelm verify-integrity` diff a promoted model against its recorded manifest — and `model.revision` now pins the upstream base-model Hub revision, recorded in `compliance_report.json`'s `model_lineage.base_model_revision` (`revision_resolved` non-null = the load in that run was pinned to it).  The judge and GRPO reward model are pinned by their `*_revision` fields but neither pin is recorded in any artefact; the classifier and merge sources are still unpinned |
| A.8.33 Test information | FL-helps | `forgelm audit` flags PII / secrets in test sets too |
| A.8.34 Protection of information systems during audit testing | OOS | — |

## See also

- [`../guides/iso_soc2_deployer_guide.md`](../guides/iso_soc2_deployer_guide.md) — deployer audit cookbook.
- [`../design/iso27001_soc2_alignment.md`](../design/iso27001_soc2_alignment.md) — full design rationale.
- [`soc2_trust_criteria_mapping.md`](soc2_trust_criteria_mapping.md) — SOC 2 mapping companion.
- [`supply_chain_security.md`](supply_chain_security.md) — SBOM + pip-audit + bandit.
- [`audit_event_catalog.md`](audit_event_catalog.md) — audit-event vocabulary.
