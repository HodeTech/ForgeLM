# QMS: Encryption at Rest

> Quality Management System guidance — [YOUR ORGANIZATION]
> ISO 27001:2022 references: A.5.33, A.8.10, A.8.13, A.8.24
> SOC 2 references: CC6.1, CC6.7

## 1. Purpose

Define the deployer-side controls required to keep ForgeLM-produced
assets confidential at rest. ForgeLM emits **integrity-protected**
artefacts (HMAC-chained audit logs, SHA-256 model integrity files,
signed deployer instructions) but does NOT encrypt them — encryption
is a substrate concern owned by the deployer's storage layer.

This document maps each ForgeLM artefact class to the recommended
encryption substrate and operator action.

## 2. Scope

The following ForgeLM-produced asset classes:

| Asset class | Path pattern | Confidentiality concern |
|---|---|---|
| Model weights — final | `<output_dir>/final_model/` | Memorisation of training data; competitive moat |
| Model weights — staging | `<output_dir>/final_model.staging.<run_id>/` | Same as final, plus pre-approval state |
| Audit log | `<output_dir>/audit_log.jsonl` + `audit_log.jsonl.manifest.json` (genesis pin) | Operator identity history; configuration hashes; chain-of-custody for compliance — line-level integrity is in-band via per-line `_hmac` + `prev_hash`, no separate `.sha256` sidecar |
| Per-output-dir salt | `<output_dir>/.forgelm_audit_salt` | Unsalted SHA-256 of low-entropy identifiers (emails, phones) is brute-forcible — the salt is the secret that gives the audit hash its wordlist resistance |
| Training corpus | (operator-supplied; typically `data/*.jsonl`) | PII; trade secrets; client data |
| Quickstart-rendered config | (operator path) | HF tokens, webhook URLs, secret env-var names |
| Compliance bundle (Annex IV) | `<output_dir>/compliance/` ZIP | Aggregated form of every above artefact |

Operator-controlled assets (HF auth tokens, webhook secrets,
`FORGELM_AUDIT_SECRET`) live in the deployer's secrets-management
substrate and are out of scope for this document — see
`access_control.md` for credential-handling guidance.

## 3. Threat model

The threats this guidance defends against:

1. **Disk theft / physical loss** — laptop or training-host hard
   drive falls into adversary hands.
2. **Backup compromise** — backup snapshot ends up on a misconfigured
   bucket or third-party cloud; older snapshots may pre-date current
   encryption policy.
3. **Shared-tenancy disk leak** — multi-tenant cloud storage where
   another tenant gains read access via misconfiguration.
4. **Log-ship intercept** — audit log shipped to SIEM over an
   unencrypted channel.
5. **Forensic recovery from decommissioned disks** — sectors not
   wiped before disposal.

Threats explicitly OUT of scope:

- **Live-memory adversary** — process memory dumps; the deployer's
  endpoint detection / sandboxing handles this.
- **Insider with legitimate decryption credentials** — encryption
  cannot defend against authorised access; access control + audit
  log is the answer.

Long-horizon (>5 years) — partial coverage:

- **Crypto-algorithm break (algorithm agility — ISO A.8.24).**
  ForgeLM assumes SHA-256 / AES-256-GCM remain intact for the
  current QMS document lifetime. The deployer's IT policy should
  reference NIST SP 800-131A revisions and reassess on a 5-year
  cadence; this QMS document is updated when ForgeLM ships an
  algorithm transition (e.g. SHA-3 audit chain). Until then,
  algorithm-break exposure is residual and accepted.

## 4. Recommended controls per asset class

### 4.1 Model weights (final + staging)

| Substrate | Recommendation |
|---|---|
| Linux training host | LUKS / dm-crypt full-disk encryption (kernel-supported, transparent) |
| macOS training host | FileVault (transparent) |
| Windows training host | BitLocker (TPM-backed key) |
| Linux container | Encrypted block device passed in via Docker `--mount type=bind,...` from a LUKS-protected host volume |
| AWS S3 | SSE-KMS with a customer-managed key (CMK); enable bucket-level default encryption + bucket policy `aws:SecureTransport=true` |
| Azure Blob | Customer-managed key via Azure Key Vault |
| GCS | Customer-Supplied Encryption Keys (CSEK) or Cloud KMS |
| HDFS / Ceph | Native at-rest encryption with deployer-managed KMS |

**Why full-disk and not file-level:** model weights are written by
HF Transformers / PEFT in many small files (sharded `.safetensors`,
adapter manifests). File-level GPG / age over each file produces
fragile file enumerations. Substrate-level encryption is transparent
and auditable.

**Key rotation:** at least annually for SSE-KMS; rotate the CMK
itself, not just the data key. ForgeLM is unaffected by rotation —
the model files re-encrypt at the storage layer without the
training pipeline noticing.

### 4.2 Audit log + manifest sidecar

**Critical contract:** the audit chain is integrity-protected at
rest by ForgeLM (HMAC + SHA-256 chain + genesis manifest). Encryption
is purely confidentiality. **Do NOT encrypt at a layer that
re-orders or re-frames the bytes** — once `.manifest.json` is signed
over a specific byte range, any encryption that perturbs that range
breaks `forgelm verify-audit`.

Safe substrates:

- Same-disk encryption (LUKS, FileVault, BitLocker) — transparent.
- Cloud blob storage with SSE-S3 or SSE-KMS — transparent.

Unsafe substrates:

- Custom application-layer wrapping that base64s + GPG-encrypts the
  JSONL — breaks `forgelm verify-audit` unless decryption happens
  before the verify command runs.

**Off-site replication:** ship to write-once storage (S3 Object Lock
in compliance mode, Azure Immutable Blob, MinIO with versioning) so
even an authorised operator cannot retroactively delete entries.
ForgeLM's append-only contract is on-host; durability is the
deployer's responsibility.

### 4.3 Per-output-dir salt (`.forgelm_audit_salt`)

This is the most operationally-sensitive single file ForgeLM emits.
Compromising it lets an attacker run an offline dictionary attack
against `data.access_request_query` and `data.erasure_*` audit events
to recover the cleartext identifiers ForgeLM intentionally hashed.

**Recommended treatment:**

- Mode `0600` on the file (`chmod 600 .forgelm_audit_salt`).
- Owned by the CI pipeline service account, not a human user.
- Backed up only to substrates that match the audit log's encryption
  posture (NEVER plaintext to a debug bucket).
- Rotated only when the corresponding `<output_dir>` is decommissioned
  — rotating mid-output-dir invalidates `forgelm verify-audit`'s
  salted-hash assumptions for prior events.

**Defence-in-depth:** export `FORGELM_AUDIT_SECRET` from your KMS so
the on-disk salt becomes one half of a two-component key. An attacker
who steals the disk but not the env secret cannot reverse the audit
hash. See `access_control.md` §3.4 for env-secret rotation.

### 4.4 Training corpus

The corpus is operator-supplied and operator-encrypted. ForgeLM
contributes:

- `forgelm audit` PII detection + secrets scan to flag what NEEDS
  encryption (e.g. corpus that turned out to contain credit-card
  numbers).
- `data_audit_report.json` with severity tiers — operator decides
  whether to re-encrypt, mask, or quarantine flagged corpora.
- `data.dataset_id` + `data_provenance.json` SHA-256 fingerprint —
  detects substrate-level tampering after encryption.

**Trade secret protection:** if the corpus is the deployer's
intellectual property (proprietary support tickets, internal
documentation), treat it as Top Secret and encrypt at the substrate
layer with a CMK whose key access is logged. Train within an isolated
VPC; do NOT let `forgelm` egress the corpus to webhook endpoints
(this is already enforced — `safe_post` never carries raw training
rows in webhook payloads).

### 4.5 Quickstart-rendered config + operator YAML

Configs may carry HF auth tokens (`HF_TOKEN`) or webhook URLs that
embed a host-internal hostname. Treat them as credential-bearing:

- Store in a config-management substrate (Ansible Vault, Doppler,
  Vault) NOT a flat file in the home directory.
- For ephemeral CI use, render the config from secrets-manager values
  at job start; delete the rendered file in the job teardown.
- ForgeLM's `config_hash` (per-run manifest sidecar field) in audit events is computed AFTER
  any secret expansion, so two config files differing only in their
  secret values produce different hashes — auditors can detect a
  config swap mid-run.

### 4.6 Compliance bundle (Annex IV ZIP)

The `forgelm --compliance-export` ZIP aggregates every artefact above
into one file. Treat it as the union of all asset classes:

- Encrypted at the substrate the most-sensitive asset requires
  (typically the training corpus dictates).
- Shipped to the auditor over an authenticated channel (e.g.
  password-protected ZIP via a regulator-supplied portal; signed S3
  pre-signed URL with 24-hour expiry).
- Receipt confirmation logged back into the audit chain via
  `compliance.artifacts_exported` event.

## 5. ForgeLM's contribution

ForgeLM does not implement encryption itself but DOES:

1. **Detect what to encrypt.** `forgelm audit` flags PII, secrets,
   and credentials in training data so the operator can encrypt or
   mask before training.
2. **Report what is encrypted.** ForgeLM does not currently surface a
   substrate-level `encryption_at_rest` flag in
   `data_governance_report.json` — the operator records this fact
   out-of-band (e.g. in the QMS evidence bundle alongside the cloud
   provider's KMS attestation) until a config-level field is added
   (Phase 28+ backlog).
3. **Verify post-encryption integrity.** `forgelm verify-audit`
   confirms the chain-after-decryption matches the chain-as-emitted;
   any substrate-level corruption is detectable.
4. **Hash low-entropy identifiers.** Salted SHA-256 means the audit
   chain itself does not need encryption to be confidential at rest
   — only the salt does.

## 6. Verification checklist

For a deployer auditor walking your encryption-at-rest controls:

- [ ] All training-host disks pass `dmsetup table` (Linux) or
      `manage-bde -status` (Windows) showing encryption-in-use.
- [ ] Cloud storage buckets' default-encryption setting is enabled
      with a customer-managed key.
- [ ] `<output_dir>/.forgelm_audit_salt` is mode `0600` and owned by
      a service account.
- [ ] Audit log replication target is write-once (S3 Object Lock,
      Azure Immutable Blob).
- [ ] Quarterly key-rotation evidence in KMS audit log.
- [ ] No ForgeLM artefact lives outside an encrypted substrate
      (use `find` + storage-policy reports to confirm).

## 7. Review

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | [DATE] | [AUTHOR] | Initial version (Wave 4 / Faz 23) |
