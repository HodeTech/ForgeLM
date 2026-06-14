---
title: GDPR Erasure & Access
description: Honour Article 15 (right of access) and Article 17 (right to erasure) requests with the `forgelm purge` and `forgelm reverse-pii` subcommands.
---

# GDPR Erasure & Access

ForgeLM ships sister subcommands for the two most-common GDPR data-subject rights: `forgelm purge` (Article 17 — right to erasure) and `forgelm reverse-pii` (Article 15 — right of access). They share a per-output-dir salt so a compliance reviewer can correlate erasures and access requests for the same data subject in a single tamper-evident audit chain — without the cleartext identifier ever leaving the operator's terminal.

This page is the operator quick-reference. The deeper deployer-flow walkthrough lives in the [GDPR erasure deployer guide on GitHub](https://github.com/HodeTech/ForgeLM/blob/main/docs/guides/gdpr_erasure.md); per-flag references are in the in-manual [CLI Reference](#/reference/cli) and the dedicated subcommand pages [`purge_subcommand.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/purge_subcommand.md) + [`reverse_pii_subcommand.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/reverse_pii_subcommand.md) on GitHub.

## Two rights, one chain

| Right | Subcommand | What it answers |
|---|---|---|
| **Article 15** — right of access | `forgelm reverse-pii` | "Do you hold any record of me?" — scans JSONL corpora for the subject's identifier. |
| **Article 17** — right to erasure | `forgelm purge` | "Delete every record of me." — atomically removes a row, a staging directory, or a compliance bundle. |

Both subcommands hash the subject's identifier with the **same** per-output-dir salt (`<output_dir>/.forgelm_audit_salt`) before recording the audit row. The `query_hash` field on `data.access_request_query` and the `target_id` field on `data.erasure_*` are byte-identical for the same subject, which lets a reviewer correlate the two without cleartext.

## Article 17 — `forgelm purge`

### Three modes

```shell
# Corpus-row erasure
forgelm purge --row-id "ali@example.com" --corpus train.jsonl \
    --output-dir ./outputs --justification "GDPR Art.17 ticket #1234"

# Run-scoped erasure
forgelm purge --run-id fg-abc123def456 --kind staging --output-dir ./outputs
forgelm purge --run-id fg-abc123def456 --kind artefacts --output-dir ./outputs

# Retention-policy report (read-only)
forgelm purge --check-policy --config configs/run.yaml --output-dir ./outputs
```

### What gets recorded

Six events ride the audit chain (see the [Audit Event Catalog on GitHub](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/audit_event_catalog.md)):

- `data.erasure_requested` — emitted FIRST, before any deletion.
- `data.erasure_completed` — emitted LAST after the disk operation succeeded.
- `data.erasure_failed` — emitted instead when the disk operation raised, or no matching row/run was found.
- `data.erasure_warning_memorisation` — corpus-row erasure × `final_model/` exists for any run that consumed this corpus (the row is gone from disk but may still be memorised in the trained weights).
- `data.erasure_warning_synthetic_data_present` — corpus-row erasure × `synthetic_data*.jsonl` exists in `output_dir`.
- `data.erasure_warning_external_copies` — loaded config has a non-empty `webhook` block; downstream consumers may have received notices.

### What it does NOT do

- **Re-train models.** Removing a row from the corpus does not unmemorise it from already-trained weights — full retraining without the erased row is the only proper mitigation. The tool emits `data.erasure_warning_memorisation` so the gap is visible.
- **Delete the audit log.** Article 17(3)(b) preserves audit / accounting records as a legal-obligation defence; the tool records the erasure as a new event rather than rewriting history.
- **Push notices to downstream consumers.** Webhook receivers, dataset mirrors, and deployed model endpoints are the operator's runtime-layer responsibility (Article 17(2)). The `data.erasure_warning_external_copies` event is the explicit reminder.

## Article 15 — `forgelm reverse-pii`

### Two scan modes

```shell
# Plaintext residual scan: detects mask leaks (operator believed the
# corpus was masked but a residual span slipped through).
forgelm reverse-pii --query "alice@example.com" --type email \
    --output-dir ./outputs data/*.jsonl

# Hash-mask scan: corpus was masked through an EXTERNAL pipeline that
# embedded SHA256(salt + identifier) digests using purge's per-output-dir
# salt as the shared secret.
forgelm reverse-pii --query "alice@example.com" --type email \
    --salt-source per_dir --output-dir ./outputs data/*.jsonl
```

### What `reverse-pii` records

One event per invocation (see catalog):

- `data.access_request_query` — emitted after the scan (or after a mid-scan I/O failure, with `error_*` fields). Carries the **hashed** `query_hash`, the `identifier_type`, the `scan_mode`, the list of `files_scanned`, the `match_count`, and the `salt_source`.

The chain row never carries the cleartext identifier — the salt protects against wordlist attacks against the audit log itself.

### `--type custom` regex caveat

`--type custom` interprets `--query` as a Python regex. On POSIX main-thread invocations a 30s per-file SIGALRM budget guards against ReDoS hangs. **On Windows AND on POSIX worker threads the SIGALRM guard is a no-op** — operators running `--type custom` from a worker thread or on Windows must vet their regex themselves.

## Salt + audit-secret separation

The per-output-dir salt at `<output_dir>/.forgelm_audit_salt` (mode `0600`, atomic O_EXCL write on first use) is the **identifier-hash salt**. It does NOT participate in the audit-chain HMAC key, which is derived independently as `SHA-256(FORGELM_AUDIT_SECRET ‖ run_id)` inside ForgeLM's `AuditLogger`.

The two primitives are intentionally separate:

- Rotating `FORGELM_AUDIT_SECRET` rotates **only the chain HMAC key**. Identifier hashes derive from the per-output-dir salt and are **stable across env-var rotations**, so a `forgelm reverse-pii` query mounted on the same `--output-dir` after a rotation still correlates with `forgelm purge` events recorded before it.
- Inspecting either primitive does not reveal the other.
- The per-output-dir salt is the only input to identifier hashing; it persists for the lifetime of the output dir, isolating the chain HMAC's rotation cadence from the cross-tool digest correlation.

## Exit codes

Both subcommands follow the project's `0/1/2/3/4` contract:

| Code | Meaning |
|---|---|
| 0 | Success. `forgelm purge --check-policy` exits 0 even when violations are found (report-not-gate semantic). `forgelm reverse-pii` exits 0 even when no matches are found (Article 15 explicitly accepts "no records" as a valid answer). |
| 1 | Config error: bad flags, unreadable corpus, malformed regex, conflicting retention values, unwritable `--audit-dir`. |
| 2 | Runtime error: I/O failure, permission denied, ReDoS SIGALRM timeout, atomic-rename failure. |

Codes 3 (`EXIT_EVAL_FAILURE`) and 4 (`EXIT_AWAITING_APPROVAL`) are not part of either subcommand's surface.

## Common pitfalls

:::warn
**Pasting the subject's identifier into `--justification`.** The justification is recorded verbatim in the audit chain. Reference your internal ticket id ("GDPR Art.17 ticket #1234") instead.
:::

:::warn
**Running `forgelm purge` without checking for memorisation.** If a `final_model/` exists for any run that consumed the corpus, the row is gone from disk but may still be encoded in the weights. The `data.erasure_warning_memorisation` event surfaces the gap; act on it (re-train without the erased row, or document the residual risk in your DPIA).
:::

:::tip
**Pin `--output-dir` explicitly.** The implicit fallback that resolves `--output-dir` from the first corpus path emits a WARNING naming the resolved directory; for cross-tool correlation between `purge` and `reverse-pii`, always pass `--output-dir` explicitly so both subcommands see the same `.forgelm_audit_salt`.
:::

:::tip
**Wire `forgelm verify-audit` into your DSAR closeout.** Once the erasure / access event is in the chain, run `forgelm verify-audit <output_dir>/audit_log.jsonl --require-hmac` and attach the verification output to the DSAR ticket. The auditor will read it.
:::

## See also

- [Audit Log](#/compliance/audit-log) — where the `data.*` events are recorded.
- [Human Oversight](#/compliance/human-oversight) — Article 14 sister gate that pairs with the GDPR rights for high-risk deployments.
- [GDPR erasure deployer guide](https://github.com/HodeTech/ForgeLM/blob/main/docs/guides/gdpr_erasure.md) — full deployer-flow walkthrough (GitHub source).
- [`purge_subcommand.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/purge_subcommand.md) — `forgelm purge` per-flag reference (GitHub source).
- [`reverse_pii_subcommand.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/reverse_pii_subcommand.md) — `forgelm reverse-pii` per-flag reference (GitHub source).
