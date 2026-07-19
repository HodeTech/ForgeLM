# QMS: Statement of Applicability (SoA)

> Quality Management System — [YOUR ORGANIZATION]
> ISO 27001:2022 Annex A — required by clause 6.1.3 d)
> Cross-reference: [`docs/design/iso27001_soc2_alignment.md`](../design/iso27001_soc2_alignment.md)
> for the full mapping rationale.

## 1. Purpose

The Statement of Applicability (SoA) is the deliverable an ISO 27001
auditor opens FIRST. It states, for every Annex A control, whether
your ISMS treats it as **applicable** or **excluded**, and the
justification.

This template is pre-populated from ForgeLM's own coverage map (see
the cross-referenced design doc). The deployer adapts each row to
their ISMS context — most rows stay applicable; a row that ForgeLM
covers natively (e.g. A.8.15 Logging) gives the deployer specific
ForgeLM-produced evidence to cite.

## 2. SoA matrix

Format: control ID → applicability → justification → ForgeLM
contribution → deployer-side action.

### 2.1 A.5 Organisational controls (37)

| Control | Applicable? | Applicability rationale (ForgeLM evidence where applicable; otherwise operator-side scope) | Implementation status |
|---|---|---|---|
| A.5.1 Policies for information security | YES | EU AI Act Art. 17 mandates QMS; ForgeLM audit-event vocabulary documents what's logged | Adopt org-wide ISMS policy referencing ForgeLM audit log |
| A.5.2 Information security roles and responsibilities | YES | `roles_responsibilities.md` QMS template defines AI Officer / ML Lead / Data Steward / DPO | Adopt role definitions |
| A.5.3 Segregation of duties | YES | `human_approval.required/granted` **records** the approver identity (sourced from `FORGELM_OPERATOR`) so trainer ≠ approver is auditable; ForgeLM does not itself reject self-approval | Configure CI runner identity ≠ human reviewer identity at the IdP level so the recorded identities cannot collide |
| A.5.4 Management responsibilities | YES | `forgelm doctor`, `compliance_report.json` provide management-review artefacts | Monthly review cadence |
| A.5.5 Contact with authorities | YES | EU AI Act Art. 73 serious-incident reporting | Maintain regulator contact list |
| A.5.6 Contact with special interest groups | YES | ML safety / red-team community | Subscribe to relevant threat intel |
| A.5.7 Threat intelligence | YES | ML supply-chain CVE feeds, model-poisoning advisories | Subscribe to relevant feeds |
| A.5.8 Information security in project management | YES | `risk_assessment` config + F-compliance-110 strict gate; Annex IV §9 metadata | Embed in project sign-off |
| A.5.9 Inventory of information and other associated assets | YES | `data_provenance.json`, `model_integrity.json`, SBOM | Maintain corporate asset register |
| A.5.10 Acceptable use of information | YES | Standard corporate AUP | Adopt |
| A.5.11 Return of assets | YES | Decommissioning of deployed models / training hosts | Adopt |
| A.5.12 Classification of information | YES | `compliance.risk_classification` 5-tier maps to org confidentiality classes | Map ForgeLM tiers to corp classes |
| A.5.13 Labelling of information | YES | `model_card.md`, manifest stamps risk class | Apply org labels on top |
| A.5.14 Information transfer | YES | `safe_post` webhook discipline, no plaintext PII in payload | Sign DTAs with webhook recipients |
| A.5.15 Access control | YES | Operator identity + salted hashing | IdP integration |
| A.5.16 Identity management | YES | `FORGELM_OPERATOR` env contract | Configure CI runner identity |
| A.5.17 Authentication information | YES | `safe_post` masks auth headers; `_mask` hides tokens | Vault-store webhook secrets, HF tokens |
| A.5.18 Access rights | YES | `human_approval` gate | RBAC at IdP |
| A.5.19 Information security in supplier relationships | YES | Dataset Hub commit SHA (`_fingerprint_hf_revision`, graded `loaded` / `unverified` / `local_path` / `unresolved`); base-model pin `model.revision`; SBOM.  Judge and GRPO reward-model loads are pinned but recorded in no artefact; classifier / merge-source loads unpinned | Vendor risk programme |
| A.5.20 Addressing information security within supplier agreements | YES | Standard supplier MSA security clauses | Adopt |
| A.5.21 Managing information security in the ICT supply chain | YES | SBOM (Wave 2 era); `pip-audit` nightly (Wave 4) | CVE monitoring |
| A.5.22 Monitoring, review and change management of supplier services | YES | Vendor annual review | Adopt |
| A.5.23 Information security for use of cloud services | YES | Cloud provider security configuration | Cloud-specific controls |
| A.5.24 Information security incident management planning and preparation | YES | `sop_incident_response.md`; audit chain preserves state | Establish IR team |
| A.5.25 Assessment and decision on information security events | YES | `data.erasure_failed`, `pipeline.failed`, `audit.classifier_load_failed` events with `error_class` + `error_message` | Triage runbook |
| A.5.26 Response to information security incidents | YES | Audit chain HMAC preserves before/after | Document runbook |
| A.5.27 Learning from information security incidents | YES | `model.reverted` events accumulate post-mortem evidence | Weekly post-mortem cadence |
| A.5.28 Collection of evidence | YES | `audit_log.jsonl` forensic-grade; `forgelm verify-audit` validates | Ship to write-once storage |
| A.5.29 Information security during disruption | YES | `auto_revert` baseline-flip; `model.reverted` event | Document base-model retention |
| A.5.30 ICT readiness for business continuity | YES | DR planning | Adopt |
| A.5.31 Identification of legal, statutory, regulatory and contractual requirements | YES | EU AI Act + GDPR mappings; Annex IV bundle | Track rule changes |
| A.5.32 Intellectual property rights | YES | License extraction in SBOM; HF model-card metadata | Per-model license review |
| A.5.33 Protection of records | YES | Append-only + HMAC + manifest; off-site replica | Off-site backup |
| A.5.34 Privacy and protection of PII | YES | `forgelm reverse-pii` Article 15; `forgelm purge` Article 17; `forgelm audit` PII detection + masking | DSAR workflow + DPIA |
| A.5.35 Independent review of information security | YES | Annual external audit | Adopt |
| A.5.36 Compliance with policies, rules and standards for information security | YES | Pydantic validation; CI gates; `forgelm doctor` | Org-wide enforcement |
| A.5.37 Documented operating procedures | YES | QMS templates (5 Wave 0 SOPs + 4 Wave 4 / Faz 23 additions) | Adapt + adopt |

### 2.2 A.6 People controls (8)

| Control | Applicable? | Justification | Implementation status |
|---|---|---|---|
| A.6.1 Screening | YES | HR background-check policy | Adopt |
| A.6.2 Terms and conditions of employment | YES | Standard NDA / IP clauses | Adopt |
| A.6.3 Information security awareness, education and training | YES | `audit_event_catalog.md` doubles as training material; `deployer_instructions.md` Article 13 output | Annual EU AI Act + GDPR training |
| A.6.4 Disciplinary process | YES | Operator attribution preserves accountability | Adopt |
| A.6.5 Responsibilities after termination or change of employment | YES | Operator id rotation; old IDs remain in audit history | Revoke departed CI runner; audit prior actions |
| A.6.6 Confidentiality or non-disclosure agreements | YES | Standard NDA | Adopt |
| A.6.7 Remote working | YES | `forgelm doctor --offline`; air-gap pre-cache | VPN policy |
| A.6.8 Information security event reporting | YES | `pipeline.failed`, `data.erasure_failed`, `audit.classifier_load_failed` events; webhook lifecycle | SIEM ingestion + alerting |

### 2.3 A.7 Physical controls (14) — typically EXCLUDED from a software-toolkit ISMS

These controls are excluded from ForgeLM's ISMS scope because ForgeLM
is a software toolkit. **The deployer's ISMS scope DOES include them
in their own SoA;** ForgeLM's exclusion here is purely about the
ForgeLM-specific control inventory.

| Control | Applicable to deployer? | Excluded-from-ForgeLM justification |
|---|---|---|
| A.7.1 Physical perimeters | YES (deployer ISMS) | ForgeLM is software; substrate-side |
| A.7.2 Physical entry | YES | Datacenter-side |
| A.7.3 Securing offices, rooms and facilities | YES | Datacenter-side |
| A.7.4 Physical security monitoring | YES | CCTV — substrate-side |
| A.7.5 Protecting against physical and environmental threats | YES | Substrate-side |
| A.7.6 Working in secure areas | YES | SCIF policies |
| A.7.7 Clear desk and clear screen | YES | Endpoint policy |
| A.7.8 Equipment siting and protection | YES | Hardware placement |
| A.7.9 Security of assets off-premises | YES | MDM |
| A.7.10 Storage media | YES | LUKS / FileVault / BitLocker — substrate-side |
| A.7.11 Supporting utilities | YES | UPS / generator |
| A.7.12 Cabling security | YES | Datacenter cabling |
| A.7.13 Equipment maintenance | YES | Hardware refresh |
| A.7.14 Secure disposal or re-use of equipment | YES | E-waste policy |

### 2.4 A.8 Technological controls (34)

| Control | Applicable? | ForgeLM evidence | Deployer-side action |
|---|---|---|---|
| A.8.1 User endpoint devices | YES | `forgelm doctor` — Python / CUDA / GPU / extras / HF auth / disk / `FORGELM_OPERATOR` checks | Endpoint hardening |
| A.8.2 Privileged access rights | YES | Operator attribution; approval gate non-trainer operator | RBAC at IdP |
| A.8.3 Information access restriction | YES | Salted identifier hashing; `forgelm reverse-pii` Article 15 | DSAR workflow |
| A.8.4 Access to source code | YES | `model.trust_remote_code=False` default; `model.revision` pins the base-model repo, bounding the repo-bundled code `trust_remote_code: true` would run.  `_fingerprint_hf_revision` covers the **dataset** repo only | VCS access control; require `model.revision` wherever `trust_remote_code: true` is approved |
| A.8.5 Secure authentication | YES | `safe_post` rejects auth headers on non-HTTPS; webhook secret discipline | MFA + token rotation |
| A.8.6 Capacity management | YES | `forgelm doctor` resource report; `resource_usage` manifest block | Quota / autoscaling |
| A.8.7 Protection against malware | YES | Antivirus on training hosts | Adopt |
| A.8.8 Management of technical vulnerabilities | YES | SBOM; `pip-audit` nightly; `bandit` CI; Pydantic config validation | OS patch management |
| A.8.9 Configuration management | YES | YAML validated via Pydantic; `forgelm --dry-run` gate; `config_hash` (per-run manifest sidecar field) in audit | Version-control configs |
| A.8.10 Information deletion | YES | `forgelm purge` Article 17; salted hash audit; `data.erasure_warning_memorisation` flag | DSAR workflow |
| A.8.11 Data masking | YES | `forgelm audit` regex + Presidio ML-NER; `_SECRET_PATTERNS` credentials scan | Masking policy |
| A.8.12 Data leakage prevention | YES | `forgelm reverse-pii` plaintext residual scan; webhook never carries raw rows | Egress DLP |
| A.8.13 Information backup | YES | `audit_log.jsonl` + `.manifest.json` backupable; `forgelm --compliance-export` ZIP | Off-site backup |
| A.8.14 Redundancy of information processing facilities | YES | Multi-AZ infra | Adopt |
| A.8.15 Logging | YES | `audit_log.jsonl` JSON Lines + HMAC + genesis manifest; `forgelm verify-audit` validates | Ship to SIEM |
| A.8.16 Monitoring activities | YES | Webhook lifecycle events; `safety_trend.jsonl` cross-run trend | Alert thresholds |
| A.8.17 Clock synchronisation | YES | All audit entries ISO-8601 UTC | NTP on training hosts |
| A.8.18 Use of privileged utility programs | YES | OS-level privilege management | Adopt |
| A.8.19 Installation of software on operational systems | YES | `forgelm doctor` reports installed packages; `pyproject.toml` pins; `pip install forgelm==X.Y.Z` | Package allowlist |
| A.8.20 Networks security | YES | `safe_post` HTTPS-only / SSRF guard / no-redirect; `model.trust_remote_code=False` | Egress firewall |
| A.8.21 Security of network services | YES | TLS-only webhooks (HTTPS-only with SSRF guard); audit-log chain HMAC via `FORGELM_AUDIT_SECRET` (note: webhook **bodies** are not HMAC-signed — destination-side authenticity is the receiving system's responsibility) | TLS 1.2+ enforcement |
| A.8.22 Segregation of networks | YES | VPC / subnet design | Adopt |
| A.8.23 Web filtering | YES | Egress proxy | Adopt |
| A.8.24 Use of cryptography | YES | SHA-256 + HMAC-SHA-256 (audit chain key = `SHA-256(FORGELM_AUDIT_SECRET ‖ run_id)`, see `AuditLogger.__init__` at `forgelm/compliance.py:164`); separately, salted SHA-256 identifier hashing for `forgelm purge` / `forgelm reverse-pii` (`_purge._resolve_salt`, distinct concern — does not participate in chain-key derivation) | KMS for `FORGELM_AUDIT_SECRET` |
| A.8.25 Secure development life cycle | YES | `docs/standards/code-review.md`, `release.md`, CI gates | SDLC framework |
| A.8.26 Application security requirements | YES | F-compliance-110 strict gate; Pydantic validation; ReDoS guard in `_reverse_pii` | App-level threat modelling |
| A.8.27 Secure system architecture and engineering principles | YES | Append-only audit log architecture; HMAC chain; lazy import; SSRF guard | Defence-in-depth |
| A.8.28 Secure coding | YES | `docs/standards/coding.md`; type hints; CommonMark escaping in `_sanitize_md_list` | Custom-extension review |
| A.8.29 Security testing in development and acceptance | YES | `pytest` ~1493 tests; `bandit` static analysis; `forgelm safety-eval` standalone gate | E2E security tests |
| A.8.30 Outsourced development | YES | Third-party-developer security | Adopt |
| A.8.31 Separation of development, test and production environments | YES | `forgelm --dry-run`; staging dir; `evaluation.require_human_approval` | Separate pipelines |
| A.8.32 Change management | YES | `human_approval.required/granted/rejected`; `config_hash` (per-run manifest sidecar field); staging snapshot | CAB process |
| A.8.33 Test information | YES | `forgelm audit` flags PII / secrets in test sets too | Test-data-handling policy |
| A.8.34 Protection of information systems during audit testing | YES | Read-only audit access | Adopt |

## 3. Coverage tally

**Terminology.** The per-theme tally below classifies every control
using the same three-tier scheme as the cross-referenced design doc's
§3: **`FL`** (*ForgeLM-supported* — ForgeLM directly produces audit
evidence), **`FL-helps`** (*deployer responsibility, ForgeLM helps* —
ForgeLM provides partial evidence the deployer combines with other
sources), and **`OOS`** (*Out of scope* — deployer-only, ForgeLM
contributes nothing). `OOS` is a per-**control** count spread across
every theme and is distinct from the table's "Excluded (ForgeLM
scope)" column, which counts only entire control **themes** ForgeLM's
inventory excludes wholesale (today just A.7 Physical, 14 controls) —
that is why the two numbers (14 vs. 34) differ.

| Theme | Total | Applicable | Excluded (ForgeLM scope) | FL-supported | FL-helps |
|---|---|---|---|---|---|
| A.5 Organisational | 37 | 37 | 0 | 3 | 24 |
| A.6 People | 8 | 8 | 0 | 0 | 5 |
| A.7 Physical | 14 | 14 (deployer ISMS) | 14 (ForgeLM-specific) | 0 | 0 |
| A.8 Technological | 34 | 34 | 0 | 8 | 19 |
| **Total** | **93** | **93 (deployer ISMS)** | **14 (ForgeLM-specific)** | **11** | **48** |

Row-by-row recount of §2.1–§2.4 above (the SoA matrix). Per-theme
`FL`/`FL-helps`/`OOS` tally — A.5: 3 / 24 / 10 OOS; A.6: 0 / 5 / 3
OOS; A.7: 0 / 0 / 14 OOS; A.8: 8 / 19 / 7 OOS — sums to 11 `FL` + 48
`FL-helps` + 34 `OOS` = 93. Cross-check the design doc's §3 "Coverage
tally" paragraph (`docs/design/iso27001_soc2_alignment.md`); the two
must match.

## 4. Review

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | [DATE] | [AUTHOR] | Initial version (Wave 4 / Faz 23) — 93 controls scored against ForgeLM v0.5.5 |

Annual review cadence:

- Re-confirm applicability (rare changes — most controls stay
  applicable).
- Update ForgeLM-evidence column when a new ForgeLM phase ships.
- Update implementation-status column to reflect deployer-side
  control posture.
