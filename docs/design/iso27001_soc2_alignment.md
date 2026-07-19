# ISO 27001 / SOC 2 Type II — ForgeLM Alignment Design

> **Scope:** Deployer evidence map showing how ForgeLM artefacts
> (audit-trail, change-management, data-governance, encryption-at-rest,
> incident-response records) populate the ISO 27001 / SOC 2 Type II
> control surface a deployer faces during certification audit. Living
> spec — kept in sync with the implementation under
> `docs/qms/`, `docs/reference/iso27001_control_mapping.md`,
> `docs/reference/soc2_trust_criteria_mapping.md`, the pip-audit /
> bandit CI guards, and the QMS documents.

**Important framing.** ForgeLM is a Python library + CLI. *Software cannot be
ISO 27001 certified* — only **organisations** running an ISMS can. What
ForgeLM *can* do is make a deployer's ISO 27001 certification audit AND a SOC 2
Type II audit demonstrably faster: the audit-trail, change-management,
data-governance, encryption, and incident-response evidence the auditor
requests is produced by ForgeLM as a byproduct of running the training
pipeline. This document is the deployer's evidence map.

The phrasing in customer-facing material is therefore **"ISO 27001 / SOC 2
Type II alignment"** — never "certified" / "compliant". The
README ISO / SOC 2 section follows this rule explicitly.

**Status:** Implemented in v0.5.5. See `CHANGELOG.md`,
`docs/guides/iso_soc2_deployer_guide.md`, and the QMS / reference
documents listed under §Scope above for the user-facing surface.

## 1. Background and scope

### 1.1 Why this matters

Most prospective deployers of an LLM fine-tuning toolkit operate inside an
organisation that already holds (or is pursuing) ISO 27001 + SOC 2 Type II. The
auditor will ask:

- *"Show me the audit trail for every training run that produced model X."*
- *"Show me the access controls — who approved the deployment, when?"*
- *"Show me the data lineage: where did the training set come from, what's its
  fingerprint?"*
- *"Show me the supply-chain inventory: what dependencies did this model
  process?"*
- *"Show me the incident response runbook: what happens if the safety
  classifier crashes during a run?"*
- *"Show me the encryption-at-rest policy for model weights and audit logs."*

Every one of these questions has a precise mechanical answer in ForgeLM's
current code surface. This document maps each ISO 27001:2022 Annex A control
and each SOC 2 Trust Services Criterion to that answer.

### 1.2 Audience

Three readers:

1. **The deployer's compliance team** — uses §3 (ISO Annex A mapping) and §4
   (SOC 2 TSC mapping) directly as audit evidence index.
2. **The deployer's engineering team** — uses §6–§8 (SBOM, vuln scanning,
   QMS docs) to integrate ForgeLM evidence into their own controls.
3. **The ForgeLM maintainer (Cemil)** — uses §13 (Faz 23 implementation
   plan) as the work breakdown for the next wave.

### 1.3 What ForgeLM is NOT in this framing

- **Not a substitute for the deployer's ISMS.** ForgeLM does not police
  organisational policies, employment contracts, physical security, or
  third-party vendor management. Annex A controls in those areas (A.5.x
  organisational, A.6.1–A.6.6 people, A.7.x physical) are deployer-side full
  stop.
- **Not a CA / certificate authority.** Salt + HMAC chain are tamper-evident,
  not non-repudiation in a PKI sense.
- **Not a SIEM / log aggregator.** ForgeLM emits audit events to a JSONL file;
  the deployer ships them to Splunk / Datadog / etc. via webhook or filebeat.
- **Not a key-management system.** ForgeLM consumes `FORGELM_AUDIT_SECRET`
  from the env; the deployer rotates the secret in their KMS.

This is the same "deployer-toolkit boundary" articulated in the root
`CLAUDE.md` "What ForgeLM is not" section — ForgeLM produces evidence;
the deployer's existing infrastructure consumes it.

### 1.4 Bağımlılıklar (closure-plan)

Faz 22 depends on every functional baseline that produces auditable evidence:

| Dep | Wave | What it gives Faz 22                              |
|-----|------|---------------------------------------------------|
| F-3 | W1   | Operator identity (`FORGELM_OPERATOR`, audit bind)|
| F-6 | W1   | `forgelm verify-audit` (chain integrity)          |
| F-7 | W1   | `safe_post` HTTP discipline (SSRF / TLS / scheme) |
| F-8 | W1   | Webhook lifecycle vocabulary                      |
| F-9 | W1   | Article 14 staging + `forgelm approve`/`reject`   |
| F-21| W2b  | GDPR Article 17 erasure (`forgelm purge`)         |
| F-38| W3   | GDPR Article 15 access (`forgelm reverse-pii`)    |

Wave 4 (this design + Faz 23 implementation) closes the deployer-cookbook
gap that previously existed.

## 2. ForgeLM control surface inventory

Cross-reference table of every ForgeLM feature that produces or enforces
auditable evidence. Cited symbols are real (verified against
`closure/wave4-integration` HEAD `b87c872`).

| Capability                          | Symbol                                                     | Evidence artefact                                       |
|-------------------------------------|------------------------------------------------------------|---------------------------------------------------------|
| Append-only audit log               | `forgelm.compliance.AuditLogger`                           | `audit_log.jsonl` + `.manifest.json`                    |
| HMAC chain verification             | `forgelm verify-audit` (+ `--require-hmac`)                | exit 0/1/2/3, JSON envelope                             |
| Operator identity                   | `FORGELM_OPERATOR`, `getpass.getuser()` fallback           | every audit entry's `operator` field                    |
| Genesis manifest sidecar            | `_check_genesis_manifest`, manifest-truncation refusal     | `<output_dir>/audit_log.manifest.json`                  |
| Salted identifier hashing           | `_purge._resolve_salt`, `_purge._hash_target_id`           | `data.erasure_*` `target_id`, `data.access_request_query` `query_hash` |
| Per-output-dir salt + env XOR       | `<output_dir>/.forgelm_audit_salt` + `FORGELM_AUDIT_SECRET`| salt-source label in audit events                       |
| GDPR Article 17 erasure             | `forgelm purge` (Phase 21)                                 | `data.erasure_requested/completed/failed/warning_*`     |
| GDPR Article 15 access              | `forgelm reverse-pii` (Phase 38)                           | `data.access_request_query`                             |
| Article 14 staging gate             | `forgelm approve` / `reject` (Phase 9)                     | `human_approval.required/granted/rejected`              |
| Approval listing                    | `forgelm approvals` (Phase 37)                             | `--pending` + `--show` flows                            |
| Pre-flight env check                | `forgelm doctor` (Phase 34)                                | check categories: python/cuda/extras/HF auth/disk/operator |
| Air-gap pre-cache                   | `forgelm cache-models` / `cache-tasks` (Phase 35)          | `cache.populate_*_requested/completed/failed`           |
| Standalone safety eval              | `forgelm safety-eval` (Phase 36)                           | `safety_results.json`, exit-code gate                   |
| Annex IV bundle verification        | `forgelm verify-annex-iv` (Phase 36)                       | manifest-hash check, missing-artefact detection         |
| GGUF integrity                      | `forgelm verify-gguf` (Phase 36)                           | magic header + `.sha256` sidecar                        |
| Risk classification gate            | `_warn_high_risk_compliance` (Faz 28 strict gate)          | `ConfigError` for high-risk + safety-disabled           |
| Auto-revert                         | `evaluation.auto_revert` + safety-eval                     | `model.reverted` audit event                         |
| Webhook lifecycle                   | `notify_start/succeeded/failed/reverted/awaiting_approval` (Phase 8) | webhook event payload + audit trail              |
| HTTP discipline                     | `safe_post` (Phase 7)                                      | SSRF guard, TLS-only, no-redirect, header masking       |
| Data audit pipeline                 | `forgelm audit` (Phase 11)                                 | `data_audit_report.json` (PII / secrets / dedup)        |
| Annex IV §8 governance              | `_data_governance_block` (compliance.py)                   | `data_governance_report.json` Article 10                |
| Model integrity generation          | `generate_model_integrity` (delegates to `_hash_file`)     | `model_integrity.json`                                  |
| Model-integrity manifest verification | `forgelm verify-integrity` (Wave 2b / Art. 15)           | exit 0/1/2, JSON envelope                               |
| SBOM                                | `tools/generate_sbom.py` (Wave 2 era)                      | CycloneDX 1.5 JSON per (OS, py-version), uploaded as release artefact |
| Bilingual EN+TR docs                | `tools/check_bilingual_parity.py` (Phase 24)               | strict-mode diff exit code                              |

Not in this table: features that exist in the codebase but do not produce
audit-grade evidence (e.g. CLI argument parsing, Pydantic models for non-
compliance fields, training metric snapshots without HMAC).

## 3. ISO 27001:2022 Annex A control mapping

ISO 27001:2022 Annex A organises 93 controls into four themes:
- **A.5 Organisational** (37 controls)
- **A.6 People** (8 controls)
- **A.7 Physical** (14 controls)
- **A.8 Technological** (34 controls)

Each row classifies coverage as one of:

- **`FL`** — *ForgeLM-supported*: ForgeLM directly produces audit evidence.
- **`FL-helps`** — *Deployer responsibility, ForgeLM helps*: ForgeLM provides
  partial evidence the deployer combines with other sources.
- **`OOS`** — *Out of scope*: deployer-only, ForgeLM contributes nothing.

### 3.1 A.5 Organisational controls

| Control | Coverage | ForgeLM evidence | Deployer action | Out-of-scope note |
|---|---|---|---|---|
| A.5.1 Policies | FL-helps | Audit event vocabulary in `audit_event_catalog.md` documents what's logged | Ratify ISMS policy referencing ForgeLM audit log as authoritative source | ForgeLM does not enforce policy; only logs evidence |
| A.5.2 Roles & responsibilities | FL-helps | `FORGELM_OPERATOR` + `roles_responsibilities.md` QMS template | Appoint AI Officer / ML Lead / Data Steward / DPO with named owners | Org-chart specifics deployer-side |
| A.5.3 Segregation of duties | FL-helps | `human_approval.required` / `granted` / `rejected` events; `forgelm approve` requires ≠ trainer operator-id | Enforce CI-runner identity ≠ deployment-approver identity at IdP layer | ForgeLM records the IDs; doesn't enforce IdP-level separation |
| A.5.4 Management responsibilities | FL-helps | `forgelm doctor` env summary; `compliance_report.json`; `training_manifest.yaml` | Monthly review of audit-log + safety-eval outcomes; document escalation | Management process deployer-side |
| A.5.5 Contact with authorities | OOS | — | Establish regulator contact for serious-incident reporting (EU AI Act Art. 73) | ForgeLM cannot interface with regulators |
| A.5.6 Contact with special interest groups | OOS | — | ML safety / red-team community engagement | Out of scope |
| A.5.7 Threat intelligence | OOS | — | Subscribe to ML supply-chain CVE feeds, model-poisoning advisories | Out of scope |
| A.5.8 Information security in project management | FL-helps | `risk_assessment` config block; F-compliance-110 strict gate; Annex IV §9 metadata | Embed ForgeLM dry-run + safety-eval gates in project sign-off checklist | Project methodology deployer-side |
| A.5.9 Inventory of information & associated assets | FL-helps | `data_provenance.json`, `model_integrity.json`, SBOM | Maintain corporate asset register linking model SHA + data fingerprint | Org asset registry deployer-side |
| A.5.10 Acceptable use of information | OOS | — | Acceptable-use policy for trained-model outputs | Out of scope |
| A.5.11 Return of assets | OOS | — | Decommissioning of deployed models | Out of scope |
| A.5.12 Classification of information | FL-helps | `compliance.risk_classification` 5-tier (`unacceptable`/`high-risk`/`limited-risk`/`minimal-risk`/`out-of-scope`) | Map ForgeLM risk tiers to corporate confidentiality classes | Org classification scheme deployer-side |
| A.5.13 Labelling of information | FL-helps | `model_card.md` produced per run; risk classification stamped into manifest | Apply org-wide labelling on top of ForgeLM artefacts | Label taxonomy deployer-side |
| A.5.14 Information transfer | FL-helps | `safe_post` webhook discipline (TLS / SSRF / scheme); no plaintext PII in webhook payload | Sign data-transfer agreements with webhook recipients (Slack, Teams, etc.) | DTA legal deployer-side |
| A.5.15 Access control | FL-helps | Operator identity attribution; salted identifier hashing | IdP integration + per-runner credentials | Access-policy authoring deployer-side |
| A.5.16 Identity management | FL-helps | `FORGELM_OPERATOR` env contract; `getpass.getuser()` fallback | Configure CI runner to set FORGELM_OPERATOR from CI metadata | Identity directory deployer-side |
| A.5.17 Authentication information | FL-helps | `safe_post` rejects auth headers in non-HTTPS requests; `_mask` hides tokens in error logs | Vault-store webhook secrets, HF tokens, model-registry credentials | KMS deployer-side |
| A.5.18 Access rights | FL-helps | `human_approval` gate gives a recordable "deny" path | Manage reviewer-list per `evaluation.require_human_approval` | RBAC deployer-side |
| A.5.19 Information security in supplier relationships | FL-helps | `_fingerprint_hf_revision` records the **dataset** repo's Hub commit SHA, graded `loaded` (the SHA `load_dataset` was pinned to), `unverified` (a lookup not tied to any load), `local_path` (files on disk — no Hub commit exists) or `unresolved` (no SHA obtained; the reason is stated); `model.revision` pins the **base model** and `model_lineage.base_model_revision` records what the load used; SBOM lists every transitive dep. The safety classifier, LLM judge, GRPO reward model and synthetic teacher are pinned by `evaluation.safety.classifier_revision`, `evaluation.llm_judge.judge_model_revision`, `training.grpo_reward_model_revision` and `synthetic.teacher_revision`, and each resolved commit is recorded under `model_lineage.component_revisions`. **Residual risk:** merge sources are still loaded unpinned, and standalone `forgelm safety-eval` takes no config so its classifier load is unpinned — see `docs/reference/configuration.md` "Hub revision pinning" | Vendor risk assessments for HF, base-model providers | Vendor questionnaires deployer-side |
| A.5.20 Addressing information security within supplier agreements | OOS | — | Supplier MSA security clauses | Out of scope |
| A.5.21 Managing information security in the ICT supply chain | FL-helps | SBOM (CycloneDX 1.5 per OS/py-version) attached to every release; `pip-audit` (Faz 23) | Track upstream CVE advisories; subscribe to GitHub Dependabot for forgelm itself | Org-wide supply-chain program deployer-side |
| A.5.22 Monitoring, review and change management of supplier services | OOS | — | Supplier annual review | Out of scope |
| A.5.23 Information security for use of cloud services | OOS | — | Cloud-provider security configuration | ForgeLM is cloud-agnostic |
| A.5.24 Information security incident management planning and preparation | FL-helps | `sop_incident_response.md` QMS template; audit chain preserves incident state | Establish IR team, on-call rotation | Org structure deployer-side |
| A.5.25 Assessment and decision on information security events | FL-helps | `data.erasure_failed`, `pipeline.failed`, `audit.classifier_load_failed` events with `error_class`+`error_message` | Triage runbook tied to event class | Triage process deployer-side |
| A.5.26 Response to information security incidents | FL-helps | Audit chain preserves before/after state through HMAC chain | Document runbook actions per event class | Runbook deployer-side |
| A.5.27 Learning from information security incidents | FL-helps | `model.reverted` events accumulate post-mortem evidence | Weekly post-mortem cadence | Process deployer-side |
| A.5.28 Collection of evidence | FL | `audit_log.jsonl` is forensic-grade (HMAC + hash chain + manifest sidecar); `forgelm verify-audit` validates | Ship to write-once storage (S3 Object Lock, Azure Immutable Blob) | Storage substrate deployer-side |
| A.5.29 Information security during disruption | FL-helps | `auto_revert` flips to baseline model on safety regression; `model.reverted` event | Document base-model retention; multi-region replicas | DR infra deployer-side |
| A.5.30 ICT readiness for business continuity | OOS | — | DR planning | Out of scope |
| A.5.31 Identification of legal, statutory, regulatory and contractual requirements | FL-helps | EU AI Act mapping in `compliance.py`; GDPR Article 15/17 handling; Annex IV bundle | Track jurisdictional rule changes (e.g. UK Online Safety Act updates) | Legal monitoring deployer-side |
| A.5.32 Intellectual property rights | FL-helps | License extraction in SBOM; HF Hub model-card metadata recorded in manifest | Track per-model license terms | Legal review deployer-side |
| A.5.33 Protection of records | FL | `audit_log.jsonl` append-only with HMAC + manifest sidecar (`_check_genesis_manifest` refuses truncate-and-resume) | Off-site replicate the audit log | Storage substrate deployer-side |
| A.5.34 Privacy and protection of PII | FL | `forgelm reverse-pii` Article 15; `forgelm purge` Article 17; `forgelm audit` PII detection + masking; salted hashing in audit events | Subject access request workflow + DPIA | Workflow + DPIA template deployer-side |
| A.5.35 Independent review of information security | OOS | — | Annual external audit | Out of scope |
| A.5.36 Compliance with policies, rules and standards for information security | FL-helps | Pydantic config validation; `forgelm doctor`; CI gates (`forgelm --dry-run`) | Org-wide policy enforcement | Out of scope (org governance) |
| A.5.37 Documented operating procedures | FL-helps | QMS templates (5 SOPs in `docs/qms/` shipping; 4 more added Faz 23) | Adapt to org context | Org-specific deployer-side |

### 3.2 A.6 People controls

| Control | Coverage | ForgeLM evidence | Deployer action | Out-of-scope note |
|---|---|---|---|---|
| A.6.1 Screening | OOS | — | Background checks | HR domain |
| A.6.2 Terms and conditions of employment | OOS | — | NDAs / IP clauses | HR domain |
| A.6.3 Information security awareness, education and training | FL-helps | `audit_event_catalog.md` doubles as training material; `deployer_instructions.md` Article 13 output | Annual EU AI Act + GDPR training | Curriculum deployer-side |
| A.6.4 Disciplinary process | FL-helps | Operator attribution on every audit entry preserves accountability | Disciplinary process tied to `FORGELM_OPERATOR` audit | Out of scope (HR) |
| A.6.5 Responsibilities after termination or change of employment | FL-helps | Operator identity rotation simply changes `FORGELM_OPERATOR`; old IDs remain in audit history | Revoke departed staff's CI runner access; audit prior actions | Out of scope (HR) |
| A.6.6 Confidentiality or non-disclosure agreements | OOS | — | NDA on training data | Legal deployer-side |
| A.6.7 Remote working | FL-helps | `forgelm doctor --offline`; air-gap pre-cache (Phase 35) | VPN policy + endpoint hardening | Endpoint security deployer-side |
| A.6.8 Information security event reporting | FL-helps | `pipeline.failed`, `data.erasure_failed`, `audit.classifier_load_failed` events accumulate; `forgelm webhook` notifies external systems | SIEM ingestion + alerting | Out of scope (SIEM) |

### 3.3 A.7 Physical controls

ForgeLM is a software toolkit. All A.7 controls are **OOS**:

| Control | Note |
|---|---|
| A.7.1 Physical perimeters | Datacenter / office security |
| A.7.2 Physical entry | Badge access |
| A.7.3 Securing offices, rooms, facilities | Clean-desk policy |
| A.7.4 Physical security monitoring | CCTV |
| A.7.5 Protecting against physical and environmental threats | Fire / flood / temp |
| A.7.6 Working in secure areas | SCIF compliance |
| A.7.7 Clear desk and clear screen | Endpoint policy |
| A.7.8 Equipment siting and protection | Hardware placement |
| A.7.9 Security of assets off-premises | Mobile-device management |
| A.7.10 Storage media | Disk-encryption policy on training nodes (deployer KMS) |
| A.7.11 Supporting utilities | UPS / generator |
| A.7.12 Cabling security | Datacenter cabling |
| A.7.13 Equipment maintenance | Hardware refresh |
| A.7.14 Secure disposal or re-use of equipment | E-waste / decommissioning |

The deployer's existing IT operations procedures cover the entire A.7 theme.
ForgeLM contributes nothing here.

### 3.4 A.8 Technological controls

| Control | Coverage | ForgeLM evidence | Deployer action |
|---|---|---|---|
| A.8.1 User endpoint devices | FL-helps | `forgelm doctor` — Python / CUDA / GPU / extras / HF auth / disk / `FORGELM_OPERATOR` checks | Endpoint hardening (disk encryption, MDM) |
| A.8.2 Privileged access rights | FL-helps | Operator attribution on every audit entry; approval gate requires explicit non-trainer operator | RBAC at IdP layer |
| A.8.3 Information access restriction | FL | Salted identifier hashing in audit events; `forgelm reverse-pii` exposes Article 15 access path; `data_audit_report.json` PII registry | Subject access request workflow |
| A.8.4 Access to source code | FL-helps | `model.trust_remote_code` defaults `False`. **`model.revision` pins the base-model repo, which is what bounds the repo-bundled code `trust_remote_code: true` would execute** — a run with `trust_remote_code: true` and no `model.revision` executes whatever code the upstream default branch carries at load time. `_fingerprint_hf_revision` is evidence about the **dataset** repo only and says nothing about executable code. The judge and GRPO reward-model loads are pinned by their `*_revision` fields; the classifier load is not.  All three run with `trust_remote_code=False` | VCS access control; require `model.revision` wherever `trust_remote_code: true` is approved |
| A.8.5 Secure authentication | FL-helps | `safe_post` rejects auth headers on non-HTTPS; webhook secret discipline | MFA + token rotation |
| A.8.6 Capacity management | FL-helps | `forgelm doctor` reports VRAM / CPU / RAM; `resource_usage` block in manifest | Quota / autoscaling |
| A.8.7 Protection against malware | OOS | — | Antivirus on training hosts |
| A.8.8 Management of technical vulnerabilities | FL-helps | SBOM (CycloneDX 1.5); `pip-audit` nightly (Faz 23); `bandit` CI (Faz 23); Pydantic config validation | OS patch management |
| A.8.9 Configuration management | FL | YAML config validated via Pydantic; `forgelm --dry-run` gate; `config_hash` (per-run manifest sidecar field) in audit events | Version-control configs |
| A.8.10 Information deletion | FL | `forgelm purge` Article 17; salted-hash audit; `data.erasure_warning_memorisation` flags model-weight retention risk | DSAR workflow |
| A.8.11 Data masking | FL | `forgelm audit` regex + Presidio ML-NER masking; `_SECRET_PATTERNS` credentials scan | Masking strategy decisions (replace vs delete) |
| A.8.12 Data leakage prevention | FL | `forgelm reverse-pii` plaintext residual scan; webhook never carries raw training rows | DLP at egress / endpoint |
| A.8.13 Information backup | FL-helps | `audit_log.jsonl` + `.manifest.json` form a backupable unit; Annex IV bundle (`forgelm --compliance-export`) | Off-site backup with encryption |
| A.8.14 Redundancy of information processing facilities | OOS | — | Multi-AZ infra |
| A.8.15 Logging | FL | `audit_log.jsonl` JSON Lines + HMAC chain + genesis manifest; `forgelm verify-audit` validates | Ship to SIEM |
| A.8.16 Monitoring activities | FL-helps | Webhook lifecycle events; `safety_trend.jsonl` cross-run trend tracking | Define alert thresholds |
| A.8.17 Clock synchronisation | FL-helps | All audit entries ISO-8601 UTC | NTP on training hosts |
| A.8.18 Use of privileged utility programs | OOS | — | OS-level privilege management |
| A.8.19 Installation of software on operational systems | FL-helps | `forgelm doctor` reports installed packages + versions; `pyproject.toml` pins; `pip install forgelm==X.Y.Z` | Package allowlist |
| A.8.20 Networks security | FL-helps | `safe_post` HTTPS-only, SSRF guard, no redirect; `model.trust_remote_code=False` default | Egress firewall |
| A.8.21 Security of network services | FL-helps | TLS-only webhooks; `FORGELM_AUDIT_SECRET` HMAC | TLS 1.2+ enforcement; cert rotation |
| A.8.22 Segregation of networks | OOS | — | VPC / subnet design |
| A.8.23 Web filtering | OOS | — | Egress proxy |
| A.8.24 Use of cryptography | FL | SHA-256 + HMAC-SHA-256 (audit chain key = `SHA-256(FORGELM_AUDIT_SECRET ‖ run_id)`, see `AuditLogger.__init__`, `forgelm/compliance.py:164`); salted SHA-256 identifier hashing for purge / reverse-pii (`_purge._resolve_salt`, distinct concern) | KMS for `FORGELM_AUDIT_SECRET` |
| A.8.25 Secure development life cycle | FL-helps | `docs/standards/code-review.md` + `docs/standards/release.md` + CI gates (ruff, pytest, dry-run, parity, SBOM) | SDLC framework |
| A.8.26 Application security requirements | FL-helps | F-compliance-110 strict gate; Pydantic config validation; ReDoS guard in `_reverse_pii` | App-level threat modelling |
| A.8.27 Secure system architecture and engineering principles | FL-helps | Append-only audit log architecture (`flock`+`fsync` per line); audit chain HMAC; lazy import discipline; `safe_post` SSRF guard | Defence-in-depth design |
| A.8.28 Secure coding | FL-helps | `docs/standards/coding.md` standards; type hints throughout; CommonMark escaping in `_sanitize_md_list` | Custom-extension review |
| A.8.29 Security testing in development and acceptance | FL-helps | `pytest` test suite across ~70 modules (post-Wave 5); `bandit` static analysis (Faz 23); `forgelm safety-eval` standalone gate | E2E security tests |
| A.8.30 Outsourced development | OOS | — | Third-party-developer security |
| A.8.31 Separation of development, test and production environments | FL-helps | `forgelm --dry-run` flag; staging dir (Phase 9); `evaluation.require_human_approval` gate | Separate pipelines |
| A.8.32 Change management | FL | `human_approval.required/granted/rejected` chain; `config_hash` (per-run manifest sidecar field); staging dir snapshot | Change Advisory Board process |
| A.8.33 Test information | FL-helps | `forgelm audit` flags PII / secrets in test sets too | Test-data-handling policy |
| A.8.34 Protection of information systems during audit testing | OOS | — | Read-only audit access |

**Coverage tally for §3:** 93 controls scored, recounted row-by-row
across §3.1–§3.4. **`FL` (full)**: 11 · **`FL-helps`**: 48 ·
**`OOS`**: 34. Per-theme split — A.5: 3 / 24 / 10; A.6: 0 / 5 / 3;
A.7: 0 / 0 / 14; A.8: 8 / 19 / 7. The OOS bucket is dominated by
A.7 physical (14) + A.5 organisational governance (10) + A.8 cloud /
network (7) — all expected for a deployer-side toolkit boundary.

## 4. SOC 2 Trust Services Criteria mapping

The 2017 SOC 2 framework (revised 2022) defines 5 categories:

- **Security** (Common Criteria CC1.x–CC9.x) — mandatory baseline.
- **Availability** (A1.x) — optional.
- **Processing Integrity** (PI1.x) — optional.
- **Confidentiality** (C1.x) — optional.
- **Privacy** (P1.x–P8.x) — optional.

A SOC 2 *Type II* audit observes operating effectiveness over a 6–12 month
window. Every Common Criteria control here is expected; the optional
categories are scoped per-engagement.

### 4.1 Security — Common Criteria

| CC      | Title                                  | ForgeLM evidence                                                 | Deployer gap |
|---------|----------------------------------------|------------------------------------------------------------------|--------------|
| CC1.1   | Demonstrates commitment to integrity   | `roles_responsibilities.md` QMS template; AI-Officer role definition | Sign tone-at-the-top doc |
| CC1.2   | Independence of governance             | Approval gate operator ≠ trainer                                 | Reporting structure |
| CC1.3   | Establishes structures, reporting lines| `roles_responsibilities.md` AI Officer / ML Lead / DPO / Compliance Officer | Adopt |
| CC1.4   | Demonstrates commitment to competence  | Annual EU AI Act / GDPR training (deployer policy)               | Curriculum + record |
| CC1.5   | Enforces accountability                | `FORGELM_OPERATOR` attribution + audit chain                     | IdP integration |
| CC2.1   | Communicates internally about controls | `audit_event_catalog.md` + `deployer_instructions.md`            | Internal comms plan |
| CC2.2   | Communicates externally                | Article 13 `deployer_instructions.md`; Annex IV public summary   | Customer-facing comms |
| CC2.3   | Communicates with regulators           | Annex IV bundle + `compliance_report.json` are the artefacts the deployer ships to the regulator on request; ForgeLM does NOT operate the communication channel itself | Regulator contact list + ship process |
| CC3.1   | Specifies suitable objectives          | `compliance.intended_purpose`; risk classification               | Adopt |
| CC3.2   | Identifies and analyses risks          | `risk_assessment` Pydantic block; safety eval                    | Risk register |
| CC3.3   | Considers fraud risks                  | Audit log tamper-evidence; HMAC chain; manifest sidecar         | Fraud taxonomy |
| CC3.4   | Identifies and assesses changes        | `human_approval.required` gate; `pipeline.config_hash`           | Change review board |
| CC4.1   | Selects, develops, performs evaluations| `forgelm verify-audit`; `forgelm safety-eval`                    | Periodic schedule |
| CC4.2   | Communicates internal-control deficiencies | `pipeline.failed`/`reverted`/`erasure_failed` audit events     | Deficiency reporting |
| CC5.1   | Selects, develops control activities   | F-compliance-110 strict gate; auto-revert; staging               | Control matrix |
| CC5.2   | Selects general IT controls            | `safe_post` HTTP discipline; Pydantic config validation          | Adopt |
| CC5.3   | Deploys policies and procedures        | `docs/qms/` 5 SOPs (Wave 0); 4 new in Faz 23                     | Adoption record |
| CC6.1   | Logical-access security software       | Operator-id attribution; HMAC chain                              | IdP / RBAC |
| CC6.2   | Authorises new internal users          | `human_approval.required`/`granted` chain                        | Onboarding workflow |
| CC6.3   | Removes access for terminated users    | (Deployer revokes CI runner identity)                            | Offboarding workflow |
| CC6.4   | Restricts physical access              | OOS                                                              | Datacenter security |
| CC6.5   | Protects against unauthorised disposal | `forgelm purge` Article 17; salted hashing                       | Off-site disposal |
| CC6.6   | Implements logical-access controls     | Salted hashing in audit events; PII reverse scan                 | RBAC |
| CC6.7   | Restricts movement of information       | `safe_post` egress discipline; webhook payload curation         | Egress policy |
| CC6.8   | Detects/prevents unauthorised software  | SBOM (Wave 2 era); `pip-audit` nightly (Faz 23); `bandit` CI (Faz 23) | Allowlist |
| CC7.1   | Detects vulnerabilities                | `pip-audit` nightly (Faz 23); CVE feed                           | Vuln-mgmt cadence |
| CC7.2   | Monitors system components             | `forgelm verify-audit`; `forgelm verify-gguf`; `safety_trend.jsonl` | SIEM dashboards |
| CC7.3   | Evaluates security events              | `data.erasure_failed`, `pipeline.failed` events with `error_class`+`error_message` | Triage runbook |
| CC7.4   | Responds to security events            | `auto_revert`, `model.reverted` event; `data.erasure_failed` event | IR runbook |
| CC7.5   | Identifies, develops corrective actions| `human_approval.rejected` event; `sop_change_management.md`     | CAPA cadence |
| CC8.1   | Authorises changes                     | `forgelm approve` Article 14 gate; staging dir                  | Change Advisory Board |
| CC9.1   | Identifies, manages risks              | `risk_assessment` config + safety eval; `risk_treatment_plan.md` (Faz 23) | Risk register |
| CC9.2   | Manages vendor + business-partner risk | SBOM; dataset Hub commit SHA (`_fingerprint_hf_revision`, labelled `loaded` / `unverified` / `local_path` / `unresolved`); base-model pin (`model.revision` → `model_lineage.base_model_revision`); license extraction.  **Pinned and recorded** under `model_lineage.component_revisions`: safety classifier, LLM judge, GRPO reward model, synthetic teacher.  **Not covered:** merge sources, and `forgelm safety-eval` (takes no `--config`) | Vendor-risk programme |

### 4.2 Availability (A1.x)

ForgeLM is a single-node CLI; availability is dominantly deployer-side.

| Control | ForgeLM contribution | Deployer responsibility |
|---|---|---|
| A1.1 Capacity planning | `forgelm doctor` resource report + `resource_usage` manifest block | Multi-AZ + autoscaling |
| A1.2 Recovery from incidents | `auto_revert` swap-back to baseline; audit chain continuity on resume | Runbook + drills |
| A1.3 Environmental protections | OOS | Datacenter UPS / generator |

### 4.3 Processing Integrity (PI1.x) — strong ForgeLM contribution

| Control | ForgeLM contribution |
|---|---|
| PI1.1 Quality of inputs | `compute_dataset_fingerprint` (SHA-256 + size + mtime); `data_governance_report` (collection_method, annotation_process, known_biases, personal_data_included, dpia_completed) |
| PI1.2 System processing | `forgelm verify-audit` validates HMAC chain end-to-end; `data_audit_report.json` flags PII / secrets / dedup before training |
| PI1.3 Outputs are accurate | `model_integrity.json` SHA-256 checksums per artefact; `model_card.md` with HF YAML front-matter |
| PI1.4 Inputs traceable | `_describe_adapter_method` canonicalisation; `pipeline.config_hash`; dataset Hub commit SHA — trustworthy as input traceability only when `hf_revision_source: loaded`, since `unverified` is a manifest-time lookup with no coupling to the load |
| PI1.5 Outputs traceable | Annex IV bundle co-locates manifest + report + audit + integrity in one ZIP |

### 4.4 Confidentiality (C1.x)

| Control | ForgeLM contribution |
|---|---|
| C1.1 Protection of confidential information | `forgelm audit` regex + Presidio ML-NER PII detection; `_SECRET_PATTERNS` credentials scan; AWS / GitHub / Slack / OpenAI / Google / JWT key matchers |
| C1.2 Disposal of confidential information | `forgelm purge` Article 17 with salted-hash audit (no raw identifier in chain); `data.erasure_warning_memorisation` flags model-weight retention risk |

### 4.5 Privacy (P1.x – P8.x)

| Control | ForgeLM contribution |
|---|---|
| P1.1 Privacy notice | Article 13 `deployer_instructions.md` |
| P2.1 Choice and consent | `evaluation.require_human_approval` Article 14 gate |
| P3.1 Collection | `data.governance.personal_data_included` boolean; `dpia_completed` flag |
| P3.2 Quality of personal data | `data_audit_report.json` quality stats |
| P4.1 Use, retention, and disposal | `retention.staging_ttl_days` (canonical; legacy alias `evaluation.staging_ttl_days` forwards transparently during the v0.5.5 → v0.6.x deprecation window) + `forgelm purge --check-policy` retention audit |
| P5.1 Access | `forgelm reverse-pii` Article 15 scan; salted query-hash in audit |
| P5.2 Inquiries and complaints | (Deployer-side workflow) |
| P6.1 Disclosure to third parties | `safe_post` webhook discipline (TLS + URL-secrecy); HMAC payload signing not yet implemented (Phase 28+ backlog) — destination-side authenticity is the receiving system's responsibility |
| P6.2 Third-party agreements | (Deployer DPAs) |
| P7.1 Breach notification | `data.erasure_failed`, `audit.classifier_load_failed` events feed breach-detection |
| P7.2 Breach disclosure | (Deployer regulator-contact playbook) |
| P8.1 Inquiries, complaints, and disputes | `forgelm reverse-pii` + `forgelm purge` chain provides forensic evidence |

## 5. Gap assessment — what ForgeLM does NOT yet ship

Items that the closure plan + this design identify as gaps closed in
Faz 23, **not** as out-of-scope:

1. **`pip-audit` nightly integration.** SBOM exists; supply-chain CVE
   scanning does not. Faz 23 adds a nightly job that fails on
   high-severity findings.
2. **`bandit` CI integration.** Static security scan; `[tool.bandit]`
   config block exists in `pyproject.toml` but no workflow consumes it
   yet. Faz 23 wires it into `ci.yml`.
3. **`[project.optional-dependencies] security`** extra. Bundles
   `pip-audit` + `bandit` for installable security tooling.
4. **`docs/qms/encryption_at_rest.md`** + `-tr.md`. Operator-facing
   guidance for model weights / audit logs / training data encryption.
5. **`docs/qms/access_control.md`** + `-tr.md`. Multi-user pipelines;
   OS-level user isolation; `FORGELM_OPERATOR` rotation guidance.
6. **`docs/qms/risk_treatment_plan.md`** + `-tr.md`. Risk register
   format with ForgeLM identified-risk rows pre-filled.
7. **`docs/qms/statement_of_applicability.md`** + `-tr.md`. ISO Annex A
   × applicable / excluded × justification matrix (the table from §3
   reformatted as the QMS-style SoA).
8. **`docs/guides/iso_soc2_deployer_guide.md`** + `-tr.md`. Deployer-
   facing audit cookbook ("how to use ForgeLM evidence in your ISO /
   SOC 2 audit"). Step-by-step.
9. **`docs/reference/iso27001_control_mapping.md`** + `-tr.md`. The §3
   table as a reference doc (vs design-doc).
10. **`docs/reference/soc2_trust_criteria_mapping.md`** + `-tr.md`. The
    §4 table as a reference doc.
11. **README ISO/SOC 2 alignment section.** 1 paragraph + link to the
    deployer guide.
12. **`sop_incident_response.md` expansion.** ISO A.5.24–A.5.27 mapping;
    security-incident playbook that runs alongside the existing safety-
    incident text.
13. **`sop_change_management.md` expansion.** ISO A.8.32 mapping; CI
    gates explicitly named as the change-control mechanism.
14. **CHANGELOG entry.** Wave 4 / Faz 22-23 line.
15. **`tests/test_sbom.py::test_deterministic`.** Same git tree → byte-
    identical SBOM; pin determinism contract.

## 6. SBOM tooling — current state and Faz 23 additions

**What's already shipped (Wave 2 era):**

`tools/generate_sbom.py` produces CycloneDX 1.5 JSON (177 lines, pure
stdlib emitter). `.github/workflows/publish.yml` runs it on every
release tag in the `cross-os-tests` job (line 94–101) and uploads
`sbom-<os>-py<version>.json` per (OS, Python) pair as artefacts.

**Why CycloneDX, not SPDX (closure-plan revision).** The closure plan
originally called for SPDX 2.3. The Wave 2 implementer chose CycloneDX
1.5 instead because:

- Both formats are ISO/SOC 2 auditable; auditors accept either.
- CycloneDX 1.5 has stronger first-class supply-chain semantics
  (vulnerability disclosures, BOM dependencies graph).
- `Dependency-Track` ≥ 4.10 — the most common open-source SBOM
  consumer — natively ingests CycloneDX 1.5.
- The existing emitter has zero external dependencies (pure stdlib +
  `importlib.metadata`).

Faz 23 keeps CycloneDX 1.5; SPDX format addition is deferred to a
later wave if a deployer specifically requests SPDX. (The deployer
guide in Faz 23 documents this choice and offers the user a
"convert with `cyclonedx-py`" recipe if SPDX is required.)

**What Faz 23 adds:**

- `tests/test_sbom.py::test_deterministic` — same input must produce
  byte-identical output.
- `tests/test_sbom.py::test_cyclonedx_schema_valid` — JSON validates
  against CycloneDX 1.5 schema (use `jsonschema` from optional dev
  extra, not at runtime).
- Documentation: `docs/reference/supply_chain_security.md` (+ TR)
  documenting the SBOM artefact, where to download it, how to
  consume it.

## 7. Vulnerability scanning — Faz 23 implementation plan

### 7.1 `pip-audit` (supply-chain CVE scan)

- **Where:** `.github/workflows/nightly.yml` (existing daily 03:00 UTC).
- **Step:**
  ```yaml
  - name: pip-audit (supply-chain CVE scan)
    if: always()  # run even if previous step soft-failed
    run: |
      pip install 'pip-audit>=2.7.0,<3.0.0'
      pip-audit --strict --format json --output /tmp/pip-audit.json || true
      python tools/check_pip_audit.py /tmp/pip-audit.json
  ```
- **Behaviour:** New helper `tools/check_pip_audit.py` parses the
  JSON; `high`-severity findings exit 1 (fail nightly); medium / low
  emit warnings via `::warning::` GitHub annotation.
- **Suppression:** When a CVE is acknowledged + tracked, list it in
  `tools/.pip-audit-ignore.json` with rationale + review date.

### 7.2 `bandit` (static code security)

- **Where:** `.github/workflows/ci.yml` (every PR + push).
- **Scope:** `forgelm/` only (`exclude_dirs = ["tests", ".venv", "build", "dist"]`
  already in `pyproject.toml`).
- **Step:**
  ```yaml
  - name: bandit (static security scan)
    run: |
      pip install 'bandit[toml]>=1.7.0,<2.0.0'
      bandit -c pyproject.toml -r forgelm/ -f json -o /tmp/bandit.json || true
      python tools/check_bandit.py /tmp/bandit.json
  ```
- **Behaviour:** New helper `tools/check_bandit.py` parses the JSON;
  `HIGH` severity exits 1, `MEDIUM` warns, `LOW` silent.
- **Suppression:** Inline `# nosec B101` (or whichever ID) with
  justification in a trailing comment on the same line.

### 7.3 Optional extra

`pyproject.toml [project.optional-dependencies]`:

```toml
security = [
  "pip-audit>=2.7.0,<3.0.0",
  "bandit[toml]>=1.7.0,<2.0.0",
]
```

Operators install via `pip install forgelm[security]` if they want
to run the same checks locally.

### 7.4 Determinism

The output of `pip-audit` is non-deterministic (depends on CVE
database state); the goal is "no high-sev finding", not bit-stability.
The output of `bandit` IS deterministic over a fixed code tree;
`tests/test_bandit_clean.py` (post-Faz-23) pins the exit-code-0
contract for a clean tree.

## 8. New QMS document plans

The four documents listed in §5 (#4–#7) are authored in Faz 23. Each
follows the existing QMS template style (see `docs/qms/sop_*.md` for
Wave 0 templates). Bilingual (`-tr.md` siblings).

### 8.1 `encryption_at_rest.md`

Outline (~150 lines):

1. Purpose — operator-facing guidance for what ForgeLM does NOT
   encrypt, and what the deployer must encrypt.
2. Scope — model weights (`final_model/`, `staging_model.<run_id>/`),
   audit logs (`audit_log.jsonl` + `.manifest.json`), training data
   (operator-supplied), config files (`config.yaml`).
3. Threat model — disk theft, backup theft, shared-tenancy disk
   leak, log-shipping intercept.
4. Recommended controls per asset class — LUKS / dm-crypt for whole-
   disk, S3 SSE-KMS for blob, GPG / age for individual files,
   `gpg-agent` for `FORGELM_AUDIT_SECRET`.
5. ForgeLM contribution — none structurally (operator chooses
   substrate); ForgeLM produces the auditable evidence on top.
6. Verification — `forgelm verify-audit` works on encrypted-at-rest
   audit logs as long as the operator decrypts before invoking; the
   audit chain itself is integrity-protected, encryption is
   confidentiality-only.

### 8.2 `access_control.md`

Outline (~180 lines):

1. Purpose — multi-user / multi-pipeline ForgeLM deployments.
2. `FORGELM_OPERATOR` env contract — required for every CI run; rotated
   when staff change.
3. OS-level isolation — separate Unix user per pipeline; `chmod 0700`
   on `output_dir`; `chown` on audit logs.
4. CI runner identity — best practice: GitHub Actions OIDC token →
   `FORGELM_OPERATOR=ci/<workflow>/<run_id>`.
5. Approval gate identity separation — trainer ≠ approver enforced
   by `forgelm approve` (audits operator id at approve time).
6. Identity migration when staff change — old `FORGELM_OPERATOR` IDs
   remain in the audit history immutably; new identity for new runs.

### 8.3 `risk_treatment_plan.md`

Outline (~250 lines):

Pre-populated risk register following the ISO 27005 risk-treatment
template:

| ID | Risk | Likelihood | Impact | Treatment | Residual |
|----|------|------------|--------|-----------|----------|
| R-01 | Training-data poisoning (adversarial corpus) | Med | High | `forgelm audit` PII / secrets / quality scan; data fingerprint pinned in manifest; deployer pre-flight review | Med → Low |
| R-02 | Supply-chain compromise (compromised PyPI dep) | Low | High | SBOM; `pip-audit` nightly; `forgelm doctor` pre-flight | Low |
| R-03 | Credential leak (HF token in config / log) | Med | Med | `safe_post` mask; `[ingestion-pii-ml]` secrets scan; `_sanitize_md_list` in deployer instructions | Low |
| R-04 | Audit-log tampering | Low | High | Append-only + HMAC + manifest sidecar; `forgelm verify-audit` | Low |
| R-05 | Memorisation of removed PII (Article 17) | High | Med | `data.erasure_warning_memorisation` flag; `forgelm safety-eval` post-erasure | Med |
| R-06 | Safety-classifier load failure | Med | High | F-compliance-110 strict gate raises `ConfigError`; `audit.classifier_load_failed` event | Low |
| R-07 | Webhook SSRF / data exfiltration | Low | Med | `safe_post` SSRF guard; HTTPS-only; URL-secrecy via `url_env` (no HMAC body signing — Phase 28+ backlog) | Low |
| R-08 | ReDoS via `--type custom` regex | Low | Low | POSIX SIGALRM 30s budget in `_scan_file_with_alarm` | Low |
| R-09 | Cross-tool digest mismatch (purge vs reverse-pii) | Low | Med | Salted-SHA-256 reuse via `_resolve_salt`; `salt_source` in audit | Low |
| R-10 | Unauthorised model deployment | Med | High | `evaluation.require_human_approval` Article 14 gate; staging dir | Low |

Each row gets ~10 lines of detail in the published doc.

### 8.4 `statement_of_applicability.md`

Outline (~120 lines):

The §3 table reformatted as a QMS-style Statement of Applicability:

| Control | Applicable? | Justification | Implementation status (Wave 4) |
|---|---|---|---|
| A.5.1 Policies for information security | Applicable | EU AI Act Art. 17 mandates QMS | Implemented via `docs/qms/` SOPs |
| ... | ... | ... | ... |

Plus the explicit "excluded" rows (A.7 physical controls etc.) with
"Excluded — ForgeLM is a software toolkit, deployer infra-side
domain" justification.

## 9. Incident response runbook expansion (`sop_incident_response.md`)

**Current state:** 76 lines, focused on AI-safety incidents (model
produces harmful output, accuracy drop, formatting errors).

**Faz 23 additions:** ~80 new lines for security incidents:

1. **Audit chain integrity violation** — `forgelm verify-audit` exits
   non-zero. Runbook: isolate output_dir, capture `audit_log.jsonl`
   + `.manifest.json` + `.sha256` sidecar, contact security team,
   re-derive previous trusted state.
2. **Credential leak detected** — `forgelm audit` `_SECRET_PATTERNS`
   match in training corpus or webhook log. Runbook: rotate the leaked
   credential immediately, run `forgelm purge --row-id <leaked-row>`,
   flag the run as `data.erasure_warning_memorisation`.
3. **Supply-chain CVE flagged** — `pip-audit` nightly fails high-
   severity. Runbook: pin to safe version, rebuild SBOM, regenerate
   dependent artefacts, notify deployer if model already shipped.
4. **Webhook target compromised** — webhook recipient confirms breach.
   Runbook: rotate the webhook URL (read via `webhook.url_env`) and
   the destination-side bearer token; re-emit lifecycle events from
   the audit chain to confirm attacker did not splice events. (HMAC
   body signing is not yet implemented in ForgeLM — Phase 28+ backlog.)
5. **PII subject access request (Article 15)** — `forgelm reverse-pii`
   workflow + DSR response template.
6. **PII erasure request (Article 17)** — `forgelm purge` workflow +
   memorisation-warning communication template.

ISO mapping section gets added: A.5.24, A.5.25, A.5.26, A.5.27,
A.6.8, A.8.15, A.8.16.

## 10. Change management runbook expansion (`sop_change_management.md`)

**Current state:** 75 lines, focused on training-config changes
(major / minor / patch).

**Faz 23 additions:** ~60 new lines:

1. **CI gates as control** — explicit table of: ruff format / ruff
   check / pytest / parity strict / SBOM determinism / dry-run all
   green BEFORE merge. ISO A.8.32 mapping.
2. **Approval gate as Change Advisory Board substitute** — Article 14
   `forgelm approve` flow as the change-authorisation event. CC8.1
   mapping.
3. **Rollback procedure** — `auto_revert` for in-pipeline; manual
   redeploy of previous model SHA for post-deployment.
4. **Configuration drift detection** — `tools/check_field_descriptions.py --strict`
   (CI) fails the build when a Pydantic field lacks a `description=`, and
   `tools/check_bilingual_parity.py --strict` enforces EN↔TR doc parity;
   the operator-facing configuration reference is maintained by hand and
   reviewed against the schema.
5. **SBOM drift detection** — `tools/generate_sbom.py` deterministic
   contract: a release's SBOM is reproducible from the corresponding
   `git tag`.

## 11. Deployer-side audit cookbook (`iso_soc2_deployer_guide.md`)

**Outline (~400 lines):**

1. **Audience** — your compliance team responding to an ISO 27001
   internal audit OR a SOC 2 Type II observation period.
2. **What ForgeLM gives you out-of-the-box** — bullet list mapping
   to §3 + §4 of this design doc.
3. **Setup checklist before audit observation period:**
   - Set `FORGELM_OPERATOR` per CI runner.
   - Set `FORGELM_AUDIT_SECRET` from KMS (32+ random bytes).
   - Enable `evaluation.require_human_approval` for every high /
     unacceptable risk run.
   - Configure webhook to ship lifecycle events to SIEM.
   - Schedule weekly `forgelm verify-audit` cron.
4. **Walking the auditor through evidence:**
   - "Show me the audit trail" → `audit_log.jsonl` + `forgelm verify-audit`.
   - "Show me the change controls" → CI logs + `human_approval.granted`
     events + `config_hash` (per-run manifest sidecar field).
   - "Show me the data lineage" → `data_provenance.json`,
     `compute_dataset_fingerprint`, and the dataset Hub commit SHA —
     check `hf_revision_source: loaded` before treating the SHA as
     evidence of the corpus that trained the model.
   - "Show me the supply chain" → SBOM artefacts on every release tag.
   - "Show me the access controls" → `FORGELM_OPERATOR` rotation log
     + IdP MFA records.
   - "Show me the encryption posture" → `docs/qms/encryption_at_rest.md`
     deployer config + KMS audit log.
   - "Show me the incident response" → `sop_incident_response.md`
     (deployer-adapted) + `pipeline.failed`/`reverted` history.
5. **Common pitfalls and how to avoid them.**
6. **References.**

## 12. CI/CD integration plan

| Pipeline | Step | Phase added | Failure behaviour |
|---|---|---|---|
| ci.yml | `bandit -r forgelm/ -c pyproject.toml` | Faz 23 | High-sev → fail; med → warn |
| ci.yml | `pytest tests/test_sbom.py` | Faz 23 | SBOM determinism contract; fail on diff |
| ci.yml | `python3 tools/check_anchor_resolution.py` | Faz 26 | Markdown anchor resolution; strict mode in CI |
| ci.yml | `tools/check_cli_help_consistency.py` | Faz 30 | CLI help ↔ doc cross-reference; strict mode |
| nightly.yml | `pip-audit --strict` | Faz 23 | High-sev → fail; med → warn |
| nightly.yml | `bandit` repeat | Faz 23 | High-sev → fail; med → warn |
| publish.yml | `tools/generate_sbom.py` (per OS×py) | (existing Wave 2) | Already integrated |
| publish.yml | upload SBOM to release | Faz 23 (deferred) | `gh release upload` step (deferred to maintainer triage) |

## 13. Faz 23 implementation work breakdown

(Used by the Faz 23 PR description as the task list.)

### 13.1 Tooling

- [x] `pyproject.toml` — add `[project.optional-dependencies] security`.
- [x] `tools/check_pip_audit.py` — parse pip-audit JSON, exit 1 on
      high-severity, warn on med/low.
- [x] `tools/check_bandit.py` — parse bandit JSON, same severity
      tiering.
- [x] `tests/test_sbom.py` — determinism contract + schema validation.
- [x] `.github/workflows/ci.yml` — bandit step.
- [x] `.github/workflows/nightly.yml` — pip-audit + bandit steps.

### 13.2 QMS docs (EN + TR pairs)

- [x] `docs/qms/encryption_at_rest.md` (+ `-tr.md`) — §8.1 outline.
- [x] `docs/qms/access_control.md` (+ `-tr.md`) — §8.2 outline.
- [x] `docs/qms/risk_treatment_plan.md` (+ `-tr.md`) — §8.3 outline.
- [x] `docs/qms/statement_of_applicability.md` (+ `-tr.md`) — §8.4 outline.
- [x] `docs/qms/sop_incident_response.md` — §9 expansion (+ TR mirror
      lands in Faz 26).
- [x] `docs/qms/sop_change_management.md` — §10 expansion (+ TR mirror
      lands in Faz 26).

### 13.3 Guides + references

- [x] `docs/guides/iso_soc2_deployer_guide.md` (+ `-tr.md`) — §11 outline.
- [x] `docs/reference/iso27001_control_mapping.md` (+ `-tr.md`) — §3
      table reformatted as reference doc.
- [x] `docs/reference/soc2_trust_criteria_mapping.md` (+ `-tr.md`) — §4
      table reformatted as reference doc.
- [x] `docs/reference/supply_chain_security.md` (+ `-tr.md`) — SBOM /
      pip-audit / bandit overview.

### 13.4 Cross-cutting

- [x] `README.md` — 1 paragraph "ISO 27001 / SOC 2 Type II alignment"
      section + link to deployer guide.
- [x] `CHANGELOG.md` — Wave 4 / Faz 22-23 line.
- [x] `tools/check_bilingual_parity.py` `_PAIRS` — register the new
      EN ↔ TR pairs (≈8 new pairs).

### 13.5 Tests

- [x] `tests/test_sbom.py::test_deterministic`.
- [x] `tests/test_sbom.py::test_cyclonedx_1_5_schema_valid`.
- [x] `tests/test_pip_audit_check.py` — `tools/check_pip_audit.py`
      severity-tiering logic on synthetic JSON.
- [x] `tests/test_bandit_check.py` — `tools/check_bandit.py` severity
      tiering on synthetic JSON.

## 14. Acceptance criteria

Faz 22 (this design):

- [x] **Length ≥ 800 lines** (closure-plan acceptance bar). This file
      will satisfy that on commit.
- [x] **§3 covers all 93 ISO 27001:2022 Annex A controls.** ✓ (37 + 8
      + 14 + 34 = 93).
- [x] **§4 covers all 5 SOC 2 categories with full Common Criteria.** ✓.
- [x] **§13 work breakdown is the canonical Faz 23 task list.** ✓.
- [x] **Cited symbols are real.** ✓ (all symbols verified against
      `closure/wave4-integration` HEAD `b87c872`).
- [x] **Bilingual parity unaffected.** ✓ (single-language design
      document; design docs are EN-only and excluded from
      ``check_bilingual_parity._PAIRS``).
- [x] **Adds zero behaviour change.** ✓ (design doc only).

Faz 23 (next phase, gated on this design landing):

See §13 above for the full task list. Acceptance: §13 checkboxes all
ticked + tests green + bilingual parity strict.

## 15. Out of scope for Faz 22 + 23

Items deferred to a later wave or explicitly out of plan:

- **PCI DSS, HIPAA, NIST CSF mappings.** ISO + SOC 2 are the requested
  baseline; other frameworks share most controls and can re-use this
  evidence map but aren't authored as separate docs.
- **SPDX 2.3 SBOM generation.** CycloneDX 1.5 is the chosen format;
  SPDX is convertible via `cyclonedx-py` if required by a specific
  deployer.
- **External penetration test.** Operator responsibility; ForgeLM
  cannot pen-test itself.
- **Bug bounty programme.** Operator-org choice.
- **FedRAMP / IL5 government clouds.** Specialised compliance regime
  out of plan scope.

## 16. Decision log

Material decisions taken during Faz 22 authoring. Each row is a
contract that future authors should not silently reverse.

| ID | Decision | Rationale | Reversible? |
|---|---|---|---|
| D-22-01 | Customer-facing wording is **"alignment"**, not **"compliance"** / **"certified"** | Software cannot be certified; only organisations can. Mis-wording invites regulator pushback | No (legal cost; would require customer-facing retraction) |
| D-22-02 | SBOM stays CycloneDX 1.5; SPDX deferred | Existing Wave 2 emitter is pure-stdlib + zero external deps; CycloneDX 1.5 is the open-source-tooling default consumed by `Dependency-Track` | Yes — operator can convert via `cyclonedx-py` if SPDX is required |
| D-22-03 | `pip-audit` + `bandit` shipped as **`security`** optional extra (not core dep) | Core install stays lean; ops teams installing for CI/SCA opt in | Yes — promotion to core dep is one-line edit |
| D-22-04 | `bandit` runs only on `forgelm/` (not `tests/`) | Test fixtures legitimately use insecure patterns (assert, dummy keys, pickle); scanning them produces signal-noise | Yes — wider scope possible if `# nosec` annotations sweep is done first |
| D-22-05 | New QMS docs ship in Faz 23 as EN-only first; TR mirrors land in Faz 26 with the existing-QMS TR sweep | Avoid cross-wave doc-pair churn; Faz 26 is the canonical QMS bilingual phase | Yes — TR can land in Faz 23 if maintainer prefers single-wave delivery |
| D-22-06 | Statement of Applicability is a SUMMARY of §3, not a third copy of the table | Single source of truth (this design doc); the QMS SoA cites + references rather than duplicates | Yes |
| D-22-07 | Risk Treatment Plan rows are **pre-filled** from ForgeLM's own threat model | Deployer customises; ForgeLM-introduced risks are owner-known and shouldn't require deployer rediscovery | Yes |
| D-22-08 | Faz 23 does NOT add SPDX SBOM, FedRAMP mapping, NIST CSF mapping, HIPAA mapping | Closure-plan scope is ISO + SOC 2; other frameworks share evidence and re-use this map | Yes — additional mappings are pure additive |
| D-22-09 | Audit chain encryption-at-rest is **deployer responsibility** | ForgeLM's tamper-evidence is integrity-only; confidentiality is substrate-side | No (architecturally) — adding crypto would conflate concerns |
| D-22-10 | `FORGELM_AUDIT_SECRET` rotation is **deployer responsibility** + documented in `access_control.md` | Secret-management is KMS domain; ForgeLM consumes, doesn't manage | No (architecturally) |

## 17. FAQ for deployers

Anticipated questions from the deployer's compliance team:

**Q1.** *Is ForgeLM ISO 27001 certified?*
**A.** No. ISO 27001 certifies organisations, not software libraries.
ForgeLM is **aligned** with ISO 27001:2022 Annex A in that it produces
auditable evidence for 59 of the 93 controls (11 `FL` + 48
`FL-helps`). The remaining 34 controls (mainly A.7 physical,
A.5 organisational governance, A.8 network / cloud) are
deployer-side. See §3 for the full mapping.

**Q2.** *Is ForgeLM SOC 2 Type II compliant?*
**A.** Same answer as Q1. ForgeLM is **aligned** with the SOC 2
Trust Services Criteria — the deployer's auditor uses ForgeLM
evidence (audit log, model integrity, data fingerprint, SBOM,
approval-gate events, etc.) when assessing the deployer's controls.

**Q3.** *Where do I store `FORGELM_AUDIT_SECRET`?*
**A.** In your KMS / secrets vault (HashiCorp Vault, AWS Secrets
Manager, Azure Key Vault). Inject via env var at CI runner start.
See `docs/qms/access_control.md` (Faz 23) for the rotation cadence
recommendation. Rotation must occur **between output-dir lifecycles**
(after archiving the current `audit_log.jsonl` + `.manifest.json`
pair), not mid-output-dir — every entry's HMAC is bound to the secret
live at emit time, and `forgelm verify-audit --require-hmac` cannot
verify a chain that mixes secrets.

**Q4.** *What encryption does ForgeLM apply at rest?*
**A.** None — ForgeLM is encryption-substrate-agnostic. The deployer
is responsible for disk / blob encryption. ForgeLM's audit chain is
**integrity-protected** (HMAC + manifest sidecar), which is
orthogonal to confidentiality. See `docs/qms/encryption_at_rest.md`
(Faz 23) for the recommended substrate per asset class.

**Q5.** *How do I respond to a GDPR Article 15 access request using
ForgeLM?*
**A.** `forgelm reverse-pii --query <subject-id> data/*.jsonl` (after
running with `--salt-source per_dir` if your corpus is hash-masked).
The audit event `data.access_request_query` records the request in
the chain (with the identifier salted-and-hashed, never raw).

**Q6.** *How do I respond to a GDPR Article 17 erasure request?*
**A.** `forgelm purge --row-id <subject-id> --corpus path/to/data.jsonl`.
The audit chain records the request + completion + any warnings
(`memorisation`, `synthetic_data_present`, `external_copies`). See
`docs/guides/gdpr_erasure.md` for the full workflow.

**Q7.** *Can ForgeLM help me pass a SOC 2 Type II observation?*
**A.** Yes — ForgeLM's audit log is HMAC-chained and append-only;
running `forgelm verify-audit` periodically (weekly cron is the
deployer-guide recommendation) provides continuous-monitoring
evidence. See §11 deployer guide outline.

**Q8.** *Where's the SBOM?*
**A.** Generated on every release tag (`v*` pattern) by
`tools/generate_sbom.py` and uploaded as a release artefact for each
(OS × Python-version) matrix cell. Format is CycloneDX 1.5 JSON.

**Q9.** *Does ForgeLM scan its own dependencies for CVEs?*
**A.** Yes — Faz 23 adds `pip-audit` to the nightly workflow.
High-severity CVEs fail the nightly; medium / low warn.

**Q10.** *Does ForgeLM perform static security analysis on its own
code?*
**A.** Yes — Faz 23 adds `bandit` to the CI workflow. Scope:
`forgelm/` (production code only). High severity fails CI.

## 18. References

- [ISO/IEC 27001:2022 Annex A](https://www.iso.org/standard/27001) —
  controls source-of-truth.
- [AICPA SOC 2 Type II 2017 Trust Services Criteria (revised 2022)](https://www.aicpa-cima.com/) —
  TSC source-of-truth.
- [CycloneDX 1.5 specification](https://cyclonedx.org/specification/overview/) —
  SBOM format.
- [`docs/qms/`](../qms/) — QMS templates (statement of applicability,
  data management SOP, change management, incident response, key
  management, retention).
- [`docs/reference/audit_event_catalog.md`](../reference/audit_event_catalog.md)
  — audit-event vocabulary.
- [`docs/reference/iso27001_control_mapping.md`](../reference/iso27001_control_mapping.md)
  — ISO/IEC 27001:2022 Annex A control → ForgeLM evidence map.
- [`docs/reference/soc2_trust_criteria_mapping.md`](../reference/soc2_trust_criteria_mapping.md)
  — SOC 2 Type II Trust Services Criteria → ForgeLM evidence map.
- [`docs/standards/`](../standards/) — engineering standards.

---

*End of Faz 22 design document. Length verification: see commit
diff.*
