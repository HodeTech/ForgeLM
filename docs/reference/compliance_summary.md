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
| Data provenance | `data_provenance.json`; `compute_dataset_fingerprint` (SHA-256 + size + mtime); dataset Hub commit SHA graded by `hf_revision_source` |
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
  ZIPs the bundle.  Hub datasets are loaded at a resolved commit by
  `forgelm.data._resolve_hub_dataset_revision`, and it is that SHA —
  not a separate lookup — that `_fingerprint_hf_revision` records as
  `hf_revision_source: loaded`.
- CLI: `forgelm --config job.yaml --compliance-export ./out/`.

### Annex IV bundle provenance fields

The bundle records *which upstream artefacts* a run used, and — this is
the part that matters to an auditor — *how strongly each record is
evidenced*. Never read a SHA without reading the grade beside it.

**Dataset**, in `data_provenance.json` and the `data_provenance` block of
`compliance_report.json`:

| Key | Meaning |
|---|---|
| `hf_revision` | The dataset repo's Hub commit SHA. Absent when none is known. **Never a branch name, tag or moving ref** — a 40-character lowercase-hex commit SHA or nothing at all. |
| `hf_revision_source` | The grade. Present on **every** dataset fingerprint, one of four mutually exclusive values — see the table below. |
| `hf_revision_reason` | Present only when `hf_revision_source` is `unresolved`; free text (≤200 chars) stating why. |
| `source` | `huggingface_hub` for a Hub-id-shaped path; `local_directory` for a directory corpus; `unknown` for a path that is neither on disk nor Hub-id-shaped. A local **file** writes no `source` key at all. |
| `dataset_id` | The Hub repo id. Written only under `source: huggingface_hub` — a directory corpus has no Hub identity and no longer carries this key. |
| `resolved_path` | The symlink target, when the local file or directory path was a symlink. |

`hf_revision_source` values:

| Value | What `hf_revision` holds | What an auditor may conclude |
|---|---|---|
| `loaded` | A 40-hex commit SHA. | **Evidence.** `forgelm.data` resolved the SHA, passed it to `load_dataset(..., revision=...)`, and the load returned. **The only value an auditor may treat as evidence of what was trained on.** No `hf_revision_reason` is written. |
| `unverified` | A 40-hex commit SHA, shape-checked. | **A lead, not proof.** No load in this process pinned this dataset; the SHA came from a Hub lookup at manifest time — the canonical case is `forgelm compliance-only`, which writes a manifest without ever reading the corpus. Read it as "the repo's default-branch head when the manifest was written". If the upstream repo moved between the load and the manifest, this value names a commit the run never read. |
| `local_path` | Absent. | **An honest gap.** The corpus is files on disk, so no Hub commit exists and none was sought; no `hf_revision_reason` is written because nothing failed. Set for both a local file and a local directory. For a local **file** the evidence is the `sha256` content hash; for a local **directory there is no content hash** — the record identifies the path only. Mirrors `local_path` in the model-side `resolution_source` vocabulary. |
| `unresolved` | Absent — never fabricated. | **An honest gap.** A lookup was attempted and failed, or was refused. `hf_revision_reason` states why. |

The rule: `loaded` is evidence; `unverified` is a lead; `local_path` and
`unresolved` are honest gaps — and a gap is never grounds to infer a revision.

Reasons an auditor will see under `unresolved`:

| `hf_revision_reason` | Meaning |
|---|---|
| `offline mode — no Hub lookup was attempted` | The run was air-gapped (`model.offline: true`, or `HF_HUB_OFFLINE` / `HF_DATASETS_OFFLINE` / `TRANSFORMERS_OFFLINE`). Nothing was asked. |
| `huggingface_hub is not installed` | No Hub client available in the environment. |
| `<ExcType>: <message>` | The lookup was made and raised — Hub unreachable, gated repo, transport error. |
| `HF Hub returned no commit SHA for this dataset` | The lookup succeeded but carried no SHA. |
| `HF Hub returned a non-commit revision for this dataset: <repr>` | The Hub answered with something that is not a 40-hex lowercase commit — e.g. `'main'` — and it was **refused** rather than recorded. A moving ref in a field auditors read as a commit is exactly the failure this vocabulary exists to prevent. |
| `path is neither a local file or directory nor a Hugging Face Hub dataset id` | A typo'd or otherwise unusable path. No Hub request was made on its behalf. |

**Consumers that assumed "not a file ⇒ Hub" need updating.** Before this
release every non-file path — directories and typos included — was labelled
`source: huggingface_hub` with a `dataset_id`, and `hf_revision_source` was
written only on the Hub branch, so its absence was ambiguous between "an old
artefact" and "a local corpus".

**Offline behaviour.** `model.offline: true` now suppresses all Hub traffic on
the data and provenance path by argument passing rather than environment
side-effect, so a library consumer gets the same protection as a CLI user. In
offline mode the dataset-metadata fetch is skipped too, so `version`,
`description` and `download_size_bytes` are absent from an air-gapped manifest.

**Base model**, in `compliance_report.json` under
`model_lineage.base_model_revision`:

| Key | Meaning |
|---|---|
| `repo_id` | `model.name_or_path` verbatim. |
| `revision_requested` | `model.revision` verbatim, or `null`. Kept beside the resolved SHA so a symbolic pin such as `main` or `v1.0` shows plainly as a moving ref rather than passing for a commit. |
| `revision_resolved` | A confirmed 40-hex commit SHA, or `null`. **A value here always means the base-model load in that run was pinned to it.** It is never the requested string echoed back, and never a SHA from an independent Hub query. |
| `resolution_source` | `local_path` (a directory on disk — no Hub commit exists); `resolved` (no pin asked for, the Hub confirmed the SHA); `pinned_resolved` (pin asked for and confirmed); `cache` (SHA read from the local commit-addressed HF cache); `pinned_unverified` (pin asked for, nothing confirmed it); `unresolved` (nothing determined). |
| `revision_pinned` | The exact string handed to `revision=`. Equals `revision_resolved` when a SHA was confirmed; equals `revision_requested` when the operator pinned a ref nothing could confirm; `null` when the load was unpinned. |
| `reason` | Present **only** when no base-model load happened in that process at all, stating so in words. `forgelm compliance-only` is the canonical case: it writes a bundle without ever loading the model and reports `resolution_source: unresolved` with this reason rather than inventing a SHA. |

Manifest generation performs **no Hub lookup of its own** for the base
model — by design, and covered by a test that fails if one is
reintroduced. Provenance is written only after the load returns; a load
that raises leaves no claim behind.

**Every other pinned role**, in `compliance_report.json` under
`model_lineage.component_revisions` — a **list**, sibling to
`base_model_revision` (which is unchanged and still present), sorted by
`(role, repo_id)` so the artefact is byte-stable across runs that load the same
models in a different order:

| Key | Meaning |
|---|---|
| `role` | One of six contract values, which never change: `base_model`, `safety_classifier`, `llm_judge`, `grpo_reward_model`, `teacher_model`, `fit_check`. |
| `repo_id` | The repo the load named. A role is not unique — two roles may legitimately name the same repo (Llama-Guard as both classifier and judge), and GRPO may be re-run against a second reward model. |
| `revision_requested` | The operator's literal, or `null`. |
| `revision_resolved` | A confirmed 40-hex commit SHA, or `null`. **Never the requested string echoed back.** |
| `resolution_source` | Same vocabulary as `base_model_revision`: `local_path`, `resolved`, `pinned_resolved`, `cache`, `pinned_unverified`, `unresolved`. |
| `revision_pinned` | The exact string handed to `revision=`, which **may be a moving ref**. |

Which config field produces which role: `model.revision` → `base_model` (which
also keeps its own `base_model_revision` block, from the same registry entry, so
the two can never disagree); `evaluation.safety.classifier_revision` →
`safety_classifier`; `evaluation.llm_judge.judge_model_revision` → `llm_judge`;
`training.grpo_reward_model_revision` → `grpo_reward_model`;
`synthetic.teacher_revision` → `teacher_model`.

Two readings an auditor must not make:

- **`component_revisions: []` does not mean "no pins were configured."** It
  means no pinned load completed in this process — `forgelm compliance-only`, an
  all-local-path config, or a manifest written before any load.
- **A null `revision_resolved` does not mean the run was unpinned.** It means no
  SHA could be confirmed; the run may still have been pinned to a ref, which
  `revision_pinned` records verbatim.

Three limits to state plainly:

- The **safety classifier** pin applies to the training-time gate only.
  Standalone `forgelm safety-eval` takes no `--config` and has no
  `--classifier-revision` flag, so its classifier load is unpinned and logs an
  UNPINNED warning naming the repo. A verdict from that subcommand is not
  pinned evidence.
- The **`fit_check` role is reserved but never emitted.** `model.revision` *is*
  forwarded to the VRAM-estimate `AutoConfig` probe, so that load is pinned, but
  the probe registers no provenance, so no `fit_check` entry ever appears.
- The flattened **`training_manifest.yaml`** sidecar carries none of
  this. It is an operator summary (`base_model`, `adapter_method`,
  `trainer_type`, `dataset`, `epochs`, `final_metrics`) with no
  `model_lineage` or `data_provenance` block at all — read
  `compliance_report.json` for provenance.

**Backward compatibility.** `component_revisions` is purely additive.
`forgelm verify-annex-iv` gates on top-level Annex IV sections and does not
inspect `model_lineage`, so artefacts written before this release remain valid
and artefacts written after verify identically. A newly generated artefact
naturally carries a different `manifest_hash` than a pre-change build of the
same run would have produced; an archived artefact keeps its own self-consistent
hash.

Full field semantics and the config surface:
[`configuration.md`](configuration.md#hub-revision-pinning).

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
