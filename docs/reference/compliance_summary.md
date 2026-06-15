# Compliance Summary — EU AI Act + ISO 27001 + SOC 2

> **Scope.** Concise, machine-friendly summary of how ForgeLM
> implements evidence, controls and artefacts that support
> compliance with the EU AI Act (high-risk systems, Article 17 QMS
> + related provisions) AND the deployer's ISO 27001 / SOC 2 Type II
> alignment. Not legal advice.
>
> **Audience.** Compliance officer / auditor / deployer engineering
> lead.
>
> Wave 4 / Faz 26 cleanup: this document used to anchor at literal
> source-code line numbers (e.g. `compliance.py#L33`) that drifted
> as the codebase evolved.  References below now use symbol-name
> + module-path form so they survive refactors.

## Quick conclusion

ForgeLM ships out-of-the-box:

- **EU AI Act Article 9** risk-management evidence via the strict
  gate (`_warn_high_risk_compliance`) + safety-eval auto-revert.
- **EU AI Act Article 10** data-governance evidence via
  `data_governance_report.json` + `forgelm audit` PII / secrets /
  quality scan.
- **EU AI Act Article 11 + Annex IV** technical documentation via
  `compliance.export_compliance_artifacts` + ZIP bundle.
- **EU AI Act Article 12** record-keeping via append-only
  `AuditLogger` (HMAC chain + manifest sidecar).
- **EU AI Act Article 13** deployer instructions via
  `generate_deployer_instructions`.
- **EU AI Act Article 14** human-oversight gate via
  `forgelm approve` / `reject` Article 14 staging.
- **EU AI Act Article 15** model-integrity via
  `compute_artefact_sha256` + `model_integrity.json`; post-deployment
  verification via `forgelm verify-integrity`.
- **EU AI Act Article 17** QMS templates in `docs/qms/` (Wave 0
  baseline + Wave 4 ISO additions).
- **GDPR Article 15** right-of-access via `forgelm reverse-pii`.
- **GDPR Article 17** right-to-erasure via `forgelm purge`.
- **ISO 27001 / SOC 2 alignment** — see Wave 4 design doc + deployer
  guide.

## EU AI Act high-level checklist

What the regulator asks vs. how ForgeLM answers:

| Regulator question | ForgeLM evidence |
|---|---|
| Risk classification + governance | `compliance.risk_classification` 5-tier; F-compliance-110 strict gate |
| QMS processes + records | `docs/qms/` 9 SOPs (5 Wave 0 + 4 Wave 4); audit chain |
| Data provenance | `data_provenance.json`; `compute_dataset_fingerprint` (SHA-256 + size + mtime); HF-revision pin |
| Technical documentation | `annex_iv_metadata.json`; Annex IV §§1-9 canonical layout |
| Conformity evidence | `compliance_report.json`; `model_card.md`; `model_integrity.json` |
| Monitoring + post-market surveillance | Webhook lifecycle (`notify_*`); `safety_trend.jsonl` cross-run trend |
| Human oversight | Article 14 staging gate; `human_approval.required/granted/rejected` |

## Where ForgeLM meets each requirement

### Safety evaluation + auto-revert

- Implementation: `forgelm.trainer` post-training evaluation chain;
  `auto_revert` flag triggers fall-back to baseline on regression.
- Evidence: `safety_results.json` (per-prompt classification);
  `model.reverted` audit event with regression delta.
- Configuration: `evaluation.safety.enabled`,
  `evaluation.auto_revert`, `evaluation.safety.scoring`,
  `evaluation.safety.min_safety_score`.

### Safety classifier + 3-layer gate

- Implementation: `forgelm.safety` runs Llama Guard 3 (or operator-
  configured classifier) on the bundled
  `forgelm/safety_prompts/default_probes.jsonl` corpus — 51 prompts
  across 18 harm categories (`benign-control`, `animal-cruelty`,
  `biosecurity`, `controlled-substances`, `credentials`, `csam`,
  `cybersecurity`, `extremism`, `fraud`, `harassment`, `hate-speech`,
  `jailbreak`, `malware`, `medical-misinfo`, `privacy-violence`,
  `self-harm`, `sexual-content`, `weapons-violence`). Operators with
  larger external corpora point `--probes` at their own JSONL.
- 3-layer gate: binary safe-ratio → confidence-weighted score →
  severity threshold.  Each layer fails the run with a distinct
  `audit.classifier_*` event so the operator can attribute the
  rejection.

### Data provenance (SHA-256) + compliance export

- Implementation: `forgelm.compliance` computes per-corpus
  fingerprints (`_fingerprint_local_file`, `_fingerprint_hf_revision`)
  and writes `data_provenance.json`; `export_compliance_artifacts`
  ZIPs the bundle.
- CLI: `forgelm --config job.yaml --compliance-export ./out/`.

### Audit chain (Article 12)

- Implementation: `forgelm.compliance.AuditLogger` — JSON Lines
  append-only log at `<output_dir>/audit_log.jsonl`, HMAC-chained
  with a per-run signing key derived inside
  `AuditLogger.__init__` as `SHA-256(FORGELM_AUDIT_SECRET ‖ run_id)`
  (the writer at `AuditLogger.log_event` and the verifier at
  `forgelm.compliance.verify_audit_log` mirror the same derivation).
  The per-output-dir salt at `<output_dir>/.forgelm_audit_salt`
  is a **distinct primitive** — it salts identifier hashing in
  `forgelm purge` / `forgelm reverse-pii` events
  (`_purge._resolve_salt`) and does NOT participate in chain-key
  derivation. Genesis manifest sidecar
  (`audit_log.jsonl.manifest.json`) refuses truncate-and-resume
  tampering.
- Verification: `forgelm verify-audit [--require-hmac]` validates
  the chain end-to-end; exits 0 (valid) or 1 (any failure — parse
  error, HMAC mismatch, manifest divergence, file not found,
  option error). The richer 0/1/2/3 exit-code surface applies to
  the **trainer** entry-point (`forgelm --config ...`), not to
  `verify-audit`.

### Article 14 staging gate

- Implementation: when `evaluation.require_human_approval: true`
  the trained model lands in
  `<output_dir>/final_model.staging.<run_id>/` awaiting
  `forgelm approve <run_id> --output-dir <output_dir>` from a
  non-trainer operator (positional `run_id`; `--run-id` is not a
  flag).
- Listing: `forgelm approvals --pending --output-dir <dir>` (Phase 37 — `--output-dir` is required).
- Audit: `human_approval.required/granted/rejected` events.

### GDPR Article 15 + 17 (Wave 2b + Wave 3)

- Article 17 erasure: `forgelm purge --row-id <id> --corpus
  data/file.jsonl` with salted-hash audit (`data.erasure_*` events).
- Article 15 access: `forgelm reverse-pii --query <id> --type
  email|phone|... data/*.jsonl` with salted-hash audit
  (`data.access_request_query` event).

### ISO 27001 / SOC 2 Type II alignment (Wave 4)

- Design doc: [`../design/iso27001_soc2_alignment.md`](../design/iso27001_soc2_alignment.md)
  (~865 lines, full 93-control coverage map).
- Deployer cookbook: [`../guides/iso_soc2_deployer_guide.md`](../guides/iso_soc2_deployer_guide.md).
- Reference tables: [`iso27001_control_mapping.md`](iso27001_control_mapping.md),
  [`soc2_trust_criteria_mapping.md`](soc2_trust_criteria_mapping.md).
- Supply chain: [`supply_chain_security.md`](supply_chain_security.md)
  — CycloneDX 1.5 SBOM, `pip-audit` nightly, `bandit` CI.

## Gaps + residual operator-side considerations

ForgeLM ships technical evidence for ~59 of the 93 ISO 27001 Annex A
controls (11 marked `FL` "full" + 48 `FL-helps` "partial" in
`docs/reference/iso27001_control_mapping.md`); the remaining ~34 are
`OOS` deployer-side (physical security, HR processes, network
segregation, etc.).  For the deployer's
ISMS posture:

- **Encryption at rest** — ForgeLM is encryption-substrate-agnostic;
  see [`../qms/encryption_at_rest.md`](../qms/encryption_at_rest.md)
  for substrate recommendations per artefact class.
- **Access control** — operator identity contract +
  `FORGELM_AUDIT_SECRET` rotation cadence in
  [`../qms/access_control.md`](../qms/access_control.md).
- **Risk treatment** — pre-populated 12-row register in
  [`../qms/risk_treatment_plan.md`](../qms/risk_treatment_plan.md).
- **Statement of Applicability** — 93-control matrix in
  [`../qms/statement_of_applicability.md`](../qms/statement_of_applicability.md).

## Recommended adoption sequence

1. Adopt the `docs/qms/` SOPs ([Model Training](../qms/sop_model_training.md),
   [Data Management](../qms/sop_data_management.md),
   [Incident Response](../qms/sop_incident_response.md),
   [Change Management](../qms/sop_change_management.md),
   [Roles & Responsibilities](../qms/roles_responsibilities.md)).
2. Set `FORGELM_OPERATOR` + `FORGELM_AUDIT_SECRET` per
   [`../qms/access_control.md`](../qms/access_control.md).
3. Configure `evaluation.require_human_approval: true` for every
   high-risk run.
4. Schedule weekly `forgelm verify-audit` cron.
5. Enable `auto_revert: true` in production training.
6. Ship `audit_log.jsonl` to write-once storage.
7. For full ISO / SOC 2 alignment: walk
   [`../guides/iso_soc2_deployer_guide.md`](../guides/iso_soc2_deployer_guide.md).

## Evidence locations (symbol references — line-stable)

Wave 4 / Faz 26 cleanup: each link points at the file root, not a
line anchor.  The auditor opens the file and greps the cited symbol
name; this survives refactors that the prior `#L33` form did not.

- **Auto-revert + safety-eval gate**: `forgelm.trainer` (search for
  `_revert_model`, `auto_revert`, `_run_safety_eval`).
- **Safety classifier + 3-layer gate**: `forgelm.safety` (search for
  `LlamaGuardClassifier`, `_evaluate_3_layer_gate`).
- **Audit chain + HMAC + manifest**: `forgelm.compliance` (search
  for `AuditLogger`, `_check_genesis_manifest`,
  `generate_model_integrity`).
- **Salted identifier hashing**: `forgelm.cli.subcommands._purge`
  (search for `_resolve_salt`, `_read_persistent_salt`,
  `_hash_target_id`).
- **GDPR Article 15 reverse-pii**: `forgelm.cli.subcommands._reverse_pii`.
- **Article 14 staging + approve / reject**:
  `forgelm.cli.subcommands._approve`,
  `forgelm.cli.subcommands._reject`,
  `forgelm.cli.subcommands._approvals`.
- **Webhook lifecycle**: `forgelm.webhook` (search for
  `notify_start`, `notify_success`, `notify_failure`,
  `notify_reverted`, `notify_awaiting_approval`).
- **HTTP discipline**: `forgelm._http` (search for `safe_post`,
  `safe_get`).
- **Config validation**: `forgelm.config`
  (`_warn_high_risk_compliance`, `_validate_galore`,
  `_validate_distributed`).

## See also

- [Audit event catalog](audit_event_catalog.md) — full event vocabulary.
- [ISO 27001 control mapping](iso27001_control_mapping.md) — Annex A × ForgeLM evidence.
- [SOC 2 Trust Services Criteria mapping](soc2_trust_criteria_mapping.md) — TSC × ForgeLM evidence.
- [Supply chain security](supply_chain_security.md) — SBOM + pip-audit + bandit.
- [QMS index](../qms/README.md) — SOP templates.
- [GDPR erasure guide](../guides/gdpr_erasure.md) — Article 15 + 17 workflows.
- [Safety + compliance guide](../guides/safety_compliance.md) — operator-facing how-to.
- [ISO / SOC 2 deployer guide](../guides/iso_soc2_deployer_guide.md) — audit cookbook (Wave 4).
