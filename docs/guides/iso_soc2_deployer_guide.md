# ISO 27001 / SOC 2 Type II — Deployer's audit cookbook

> Audience: your compliance team responding to an ISO 27001 internal
> audit OR a SOC 2 Type II observation period.
>
> **Critical framing**: software cannot be ISO 27001 certified — only
> organisations can. ForgeLM is **aligned** with the ISO 27001:2022
> Annex A controls and the AICPA SOC 2 Trust Services Criteria, in
> the sense that running ForgeLM in your training pipeline produces
> auditable evidence the auditor explicitly asks for. This guide
> walks each common audit-floor question and shows you which
> ForgeLM artefact answers it.
>
> Cross-reference:
> [`docs/design/iso27001_soc2_alignment.md`](../design/iso27001_soc2_alignment.md)
> for the full design rationale + 93-control coverage map.

## What ForgeLM gives you out-of-the-box

The four pillars an ISO / SOC 2 auditor cares about most all have
direct ForgeLM evidence:

1. **Audit trail** — `audit_log.jsonl` per training run, append-only
   with HMAC + SHA-256 hash chain + genesis manifest sidecar.
   `forgelm verify-audit` validates the chain end-to-end.
2. **Change control** — Article 14 staging gate (`forgelm approve` /
   `reject`) + `human_approval.required/granted/rejected` audit
   events + `config_hash` (per-run manifest sidecar field) stamped per run. Every
   model promotion is dual-controlled and forensically attributed.
3. **Data lineage** — `data_provenance.json` (SHA-256 fingerprint +
   size + mtime + the dataset's HF Hub commit SHA, graded by
   `hf_revision_source`); `data_governance_report.json`
   (collection_method, annotation_process, known_biases,
   personal_data_included, dpia_completed).
4. **Supply chain** — CycloneDX 1.5 SBOM emitted per release tag for
   every (OS × Python-version) cell of the publish matrix. Wave 4
   adds `pip-audit` nightly + `bandit` CI for static + dynamic
   security scanning.

## Setup checklist before audit observation period

Twelve items to land **before** the auditor asks.

### Identity + secrets

- [ ] **Set `FORGELM_OPERATOR`** on every CI runner — use a
      machine-readable namespaced identifier (e.g.
      `gha:Acme/repo:training:run-${{ github.run_id }}`). See
      `docs/qms/access_control.md` §3 for the recommended form.
- [ ] **Generate `FORGELM_AUDIT_SECRET`** in your KMS / Vault
      (32+ random bytes, AES-256-GCM-strength entropy).
- [ ] **Plan `FORGELM_AUDIT_SECRET` rotation between output-dir
      lifecycles** — every entry's HMAC is bound to the secret live
      at emit time, so rotation must happen *after* archiving the
      current `audit_log.jsonl` + `.manifest.json` pair, never
      mid-output-dir. (`forgelm verify-audit --require-hmac` cannot
      verify a chain that mixes secrets — by design.)
- [ ] **Configure approver identity ≠ trainer identity** — the
      Article 14 staging gate is your in-pipeline Change Advisory
      Board.

### Pipeline configuration

- [ ] **`evaluation.require_human_approval: true`** for every
      `risk_classification` ∈ `{high-risk, unacceptable}` run.
      F-compliance-110 raises `ConfigError` if you forget.
- [ ] **Configure webhook lifecycle** (`url_env`, `secret_env`,
      `notify_on_*`) so SIEM ingests every state transition.
- [ ] **Enable `auto_revert: true`** for production training so
      a quality regression rolls back to the baseline model
      automatically.

### Audit + monitoring

- [ ] **Schedule weekly `forgelm verify-audit` cron** on every
      production `<output_dir>`. Wire alerts on non-zero exit.
- [ ] **Ship `audit_log.jsonl`** to write-once storage (S3 Object
      Lock with compliance-mode retention, Azure Immutable Blob,
      MinIO with versioning).
- [ ] **Encrypt-at-rest substrate** for model weights, audit logs,
      training data — see `docs/qms/encryption_at_rest.md` for
      per-asset substrate recommendations.

### Supply-chain hygiene

- [ ] **Pin ForgeLM** in CI (`pip install forgelm==X.Y.Z`).
- [ ] **Subscribe nightly SBOM diffs** for every release you
      depend on.

## Walking the auditor through evidence

The eight most common audit-floor questions, with the exact ForgeLM
artefact + grep / command that produces the evidence.

### Q1: "Show me the audit trail for every model promotion in the past 90 days"

```bash
# Verify the chain integrity first (positional log_path; no --json /
# --output-dir flags exist on this subcommand by design).
forgelm verify-audit ./outputs/audit_log.jsonl --require-hmac

# Then extract the promotion events.
jq 'select(.event == "human_approval.granted")' ./outputs/audit_log.jsonl
```

Each `human_approval.granted` entry carries:

- `operator` — who approved (the **approving** identity, not the
  trainer).
- `run_id` — links back to the training run that produced the model.
- `prev_hash` + `_hmac` — chain integrity.
- The training-run identity — `config_hash`, the base model name +
  adapter method (`model_lineage`), and the dataset fingerprint
  (`data_provenance`) — lives in the run's `compliance_report.json`
  (the `training_manifest.yaml` sidecar beside it is a flattened
  operator summary and carries none of those three blocks);
  `config_hash` is additionally stamped on the earlier
  `human_approval.required` audit event, so an auditor pivots by
  `run_id` to that event (or the report) and diffs the YAML in `git
  log`. (Note: ForgeLM records the base model by name and, when
  `model.revision` is set — or when the commit can be resolved without
  one — pins the base-model load and records that commit under
  `model_lineage.base_model_revision`. Treat `revision_resolved` as
  reproducibility evidence only when it is non-null; the
  `revision_requested` value beside it shows plainly when the operator
  pinned a moving ref such as `main`. The safety classifier, LLM judge,
  GRPO reward model and synthetic teacher are **pinned by their
  `*_revision` config fields**, and each resolved commit is recorded
  under `model_lineage.component_revisions` beside the base model's own
  block. Merge-source models remain unpinned, and standalone `forgelm
  safety-eval` takes no config so its classifier load is unpinned —
  only the training-time safety gate honours
  `evaluation.safety.classifier_revision`. The promoted artefacts
  remain hash-verified through `model_integrity.json` / the
  `model.integrity_verified` event. `forgelm approvals` reads `config_hash` from the
  `human_approval.required` event and falls back to a legacy
  `config_fingerprint` key that no current producer emits. See
  `docs/reference/approvals_subcommand.md`.)

### Q2: "Show me the change-control evidence — who approved this model?"

The chain itself **is** the change-control evidence. Cross-reference:

```bash
# 1. Find the training event for run X.
jq 'select(.run_id == "X" and .event == "training.started")' \
    ./outputs/audit_log.jsonl

# 2. Find the approval event for the same run.
jq 'select(.run_id == "X" and .event == "human_approval.granted")' \
    ./outputs/audit_log.jsonl

# 3. Confirm the operator IDs differ.
jq -r 'select(.run_id == "X" and (.event == "training.started" or .event == "human_approval.granted")) | .operator' \
    ./outputs/audit_log.jsonl | sort -u
```

Two distinct operator IDs prove segregation of duties (ISO A.5.3,
SOC 2 CC1.5).

### Q3: "Show me the data lineage"

```bash
cat ./outputs/data_provenance.json
# {
#   "dataset_id": "Acme/customer-support-v3",
#   "hf_revision": "9c7c8f3...",
#   "hf_revision_source": "loaded",
#   "sha256": "ab12...",
#   "size_bytes": 14982011,
#   "modified": "2026-05-01T08:14:33Z",
#   ...
# }
```

`sha256` + `hf_revision` together pin the corpus deterministically
**only when `hf_revision_source` reads `loaded`** (local files also
carry `size_bytes` + `modified`; the field shape comes from
`forgelm.compliance._fingerprint_local_file`). An auditor running
`forgelm audit data/*.jsonl` on the same input must see the same
fingerprint.

Read `hf_revision_source` before you read `hf_revision`:

| `hf_revision_source` | What it means | Usable as evidence? |
|---|---|---|
| `loaded` | ForgeLM resolved the repo's commit, passed that exact SHA to `load_dataset(..., revision=...)`, and the load succeeded. `hf_revision` is the corpus that was read. | **Yes** |
| `unverified` | No load in this process pinned the dataset. `hf_revision` is a Hub lookup of the repo's current default-branch head, made when the manifest was written. A breadcrumb, not proof. `forgelm compliance-only` always produces this, because it writes a bundle without ever reading the corpus. | **No** |
| `unresolved` | No SHA could be established. `hf_revision` is **absent** and `hf_revision_reason` states why (offline mode, `huggingface_hub` not installed, Hub unreachable, gated repo, no SHA returned). ForgeLM never invents a SHA. | **No — and nothing is claimed** |

**Manifests produced before this release carry `hf_revision` with no
`hf_revision_source` key.** Those SHAs were obtained by an independent
Hub lookup at manifest-generation time with no coupling to the load, so
they are `unverified` in the sense above even though they are unlabelled.
Do not treat a bare `hf_revision` from an older bundle as proof of which
corpus trained the model.

### Q4: "Show me the supply chain"

```bash
# Download the SBOM artefact from the GitHub release page.
gh release download v0.5.5 --pattern 'sbom-*'

# Or regenerate on demand.
python3 tools/generate_sbom.py > sbom.json

# Diff against last release.
diff <(jq -S . sbom-prev.json) <(jq -S . sbom.json)
```

The CycloneDX 1.5 JSON lists every transitive dependency with its
purl (`pkg:pypi/...`) and version. Dependency-Track ingests this
natively for CVE correlation.

### Q5: "Show me the access controls — how do you prove only authorised reviewers approve models?"

Two layers:

1. **IdP layer** — your CI runner identities and human reviewer
   identities are issued by your IdP. The auditor walks your IdP
   audit log to confirm the issuance + revocation cadence.
2. **ForgeLM layer** — every approval event records the approver's
   `FORGELM_OPERATOR`. Cross-reference with the IdP audit log to
   confirm the approver was authorised at approval time.

```bash
# All approval events with approver id + timestamp.
jq -r 'select(.event == "human_approval.granted") |
       [.timestamp, .operator, .run_id] | @tsv' \
    ./outputs/audit_log.jsonl
```

### Q6: "Show me the encryption posture"

This is **deployer-side** — ForgeLM does not encrypt artefacts,
substrate does. Reference:

- `docs/qms/encryption_at_rest.md` — your in-house policy mapped to
  ForgeLM artefact classes.
- KMS audit log — substrate-side evidence of encryption-in-use.
- Operator-side encryption attestation — recorded out-of-band in your
  QMS evidence bundle until ForgeLM exposes a config-level
  `encryption_at_rest` field (Phase 28+ backlog).

### Q7: "Show me the incident response — what happens if the safety classifier crashes mid-run?"

Reference:

- `docs/qms/sop_incident_response.md` §4 (Wave 4 / Faz 23 expansion)
  for the security-incident playbook.
- The audit chain itself: `audit.classifier_load_failed` event fires
  + `pipeline.failed` propagates + the run does NOT produce a
  `final_model/` directory.
- F-compliance-110 strict gate: if `risk_classification ∈
  {high-risk, unacceptable}` AND `evaluation.safety.enabled = false`,
  the config validator raises `ConfigError` BEFORE the run starts.
  The auditor sees zero high-risk runs without a safety gate in
  the past 90 days.

### Q8: "Show me you can respond to a GDPR Article 15 + 17 request"

Article 15 (right of access):

```bash
forgelm reverse-pii --query "alice@example.com" --type email \
    --output-dir ./outputs data/*.jsonl
# JSON envelope returns { matches: [...], match_count: N }
# The audit chain stamps a `data.access_request_query` event with
# the QUERY HASHED (never raw — the salt protects against wordlist
# attacks against the audit log itself).
```

Article 17 (right to erasure):

```bash
forgelm purge --row-id "alice@example.com" --corpus data/2026Q2.jsonl \
    --output-dir ./outputs
# The audit chain stamps `data.erasure_requested` →
# `data.erasure_completed` (or `data.erasure_failed`) with
# `target_id` HASHED.  If a model trained on the row has
# `final_model/`, `data.erasure_warning_memorisation` ALSO fires
# and you must communicate the memorisation residual risk to the
# subject.
```

The cross-tool digest correlation (purge `target_id` ==
reverse-pii `query_hash` for the same identifier in the same
`output_dir`) is pinned by Wave 3 follow-up tests.

## Common pitfalls

Things deployers get wrong on their first audit:

1. **Sharing `FORGELM_OPERATOR` across pipelines.** "We just set
   `FORGELM_OPERATOR=ci`" — the audit chain becomes uninterpretable
   because every entry attributes to the same string. Use namespaced
   identifiers per pipeline + per run.
2. **Storing webhook URLs in YAML.** Use `webhook.url_env: SLACK_WEBHOOK`
   → resolve from your secret manager at runtime; never commit a
   plaintext webhook URL to version control. The auditor sees
   historical exposure in `git log` even after rotation. (Note: ForgeLM
   does **not** sign webhook bodies today — gateway-level integrity
   relies on mTLS or destination-side checks; HMAC body signing is on
   the Phase 28+ backlog.)
3. **Skipping `forgelm verify-audit` in CI.** "We trust the audit
   chain implicitly" is not a defensible position. Schedule the
   weekly cron + alert on non-zero exit; have the alert history to
   show the auditor.
4. **Forgetting the manifest sidecar.** `forgelm verify-audit`
   walks the chain end-to-end without the sidecar (the manifest is
   not strictly required for the basic chain check), but the
   sidecar is what surfaces **truncate-and-resume tampering** —
   when present, the verifier cross-checks the manifest's pinned
   first-entry SHA-256 + run_id against the live log's first line.
   Without the manifest, that class of attack lands silently.
   Back **both** files up to the same write-once substrate for
   full tamper-detection coverage.
5. **No `auto_revert` on production training.** If you're betting
   on always-green training, you're a single safety-classifier
   degradation away from a regulator-reportable incident. Enable
   `auto_revert: true` and let `model.reverted` events accumulate
   as evidence of working safeguards.
6. **Running ForgeLM with `FORGELM_ALLOW_ANONYMOUS_OPERATOR=1` in
   production.** That env var exists for short-lived test runs only.
   Production runs without operator identity are an ISO A.6.4 +
   A.6.5 finding.

## References

- [`../design/iso27001_soc2_alignment.md`](../design/iso27001_soc2_alignment.md) — full design rationale + 93-control coverage map.
- [`../qms/encryption_at_rest.md`](../qms/encryption_at_rest.md) — substrate-side encryption guidance.
- [`../qms/access_control.md`](../qms/access_control.md) — operator identity + secrets management.
- [`../qms/risk_treatment_plan.md`](../qms/risk_treatment_plan.md) — pre-populated risk register.
- [`../qms/statement_of_applicability.md`](../qms/statement_of_applicability.md) — 93-control SoA matrix.
- [`../qms/sop_incident_response.md`](../qms/sop_incident_response.md) — incident response runbook (Wave 4 expansion).
- [`../qms/sop_change_management.md`](../qms/sop_change_management.md) — change management runbook (Wave 4 expansion).
- [`../reference/iso27001_control_mapping.md`](../reference/iso27001_control_mapping.md) — ISO 27001:2022 Annex A controls × ForgeLM evidence.
- [`../reference/soc2_trust_criteria_mapping.md`](../reference/soc2_trust_criteria_mapping.md) — SOC 2 Trust Services Criteria × ForgeLM evidence.
- [`../reference/supply_chain_security.md`](../reference/supply_chain_security.md) — SBOM + pip-audit + bandit overview.
- [`../reference/audit_event_catalog.md`](../reference/audit_event_catalog.md) — audit-event vocabulary.
