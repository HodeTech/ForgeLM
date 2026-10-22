---
title: GDPR Erasure & Access
description: Support Article 15 (access) and Article 17 (erasure) requests with the `forgelm purge` and `forgelm reverse-pii` subcommands.
---

# GDPR Erasure & Access

ForgeLM ships sister subcommands that support two common GDPR data-subject requests: `forgelm purge` removes rows (one step of an Article 17 erasure) and `forgelm reverse-pii` finds rows (an input to an Article 15 response). Neither is a complete response on its own. They share a per-output-dir salt so a compliance reviewer can correlate erasures and access requests for the same data subject in a single tamper-evident audit chain — without the cleartext identifier ever leaving the operator's terminal.

This page is the operator quick-reference. The deeper deployer-flow walkthrough lives in the [GDPR erasure deployer guide on GitHub](https://github.com/HodeTech/ForgeLM/blob/main/docs/guides/gdpr_erasure.md); per-flag references are in the in-manual [CLI Reference](#/reference/cli) and the dedicated subcommand pages [`purge_subcommand.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/purge_subcommand.md) + [`reverse_pii_subcommand.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/reverse_pii_subcommand.md) on GitHub.

## Two rights, one chain

| Request | Subcommand | What it does |
|---|---|---|
| **Article 15** — access | `forgelm reverse-pii` | Finds the lines that contain the subject's identifier in the JSONL corpora you scan: an input to the response, not the response. |
| **Article 17** — erasure | `forgelm purge` | Atomically removes a corpus row by id (one step of an erasure, not all of it); with `--run-id` it removes a staging directory or compliance files whose names embed the run id. |

Both subcommands hash the subject's identifier with the **same** per-output-dir salt (`<output_dir>/.forgelm_audit_salt`) before recording the audit row. The `query_hash` field on `data.access_request_query` and the `target_id` field on `data.erasure_*` are byte-identical only when the `--row-id` value and the `--query` value are the same text, both commands use the same `--output-dir`, and `FORGELM_AUDIT_SECRET` is in the same state for both; otherwise link the two with the ticket reference you pass to `forgelm purge --justification`.

## Article 17 — `forgelm purge`

### Three modes

```shell
# Corpus-row erasure
forgelm purge --row-id "ali@example.com" --corpus train.jsonl \
    --output-dir ./outputs --justification "GDPR Art.17 ticket #1234"

# Run-scoped erasure
forgelm purge --run-id fg-abc123def456 --kind staging --output-dir ./outputs
forgelm purge --run-id fg-abc123def456 --kind artefacts --output-dir ./outputs   # finds nothing on the standard bundle, see below

# Retention-policy report (read-only)
forgelm purge --check-policy --config configs/run.yaml --output-dir ./outputs
```

### What gets recorded

Six events ride the audit chain (see the [Audit Event Catalog on GitHub](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/audit_event_catalog.md)):

- `data.erasure_requested` — emitted FIRST, before any deletion.
- `data.erasure_completed` — emitted LAST after the disk operation succeeded.
- `data.erasure_failed` — emitted instead when the disk operation raised, or no matching row/run was found.
- `data.erasure_warning_memorisation` — corpus-row erasure × a `final_model/` exists directly under `--output-dir` (the row is gone from disk but may still be memorised in the trained weights).
- `data.erasure_warning_synthetic_data_present` — corpus-row erasure × `synthetic_data*.jsonl` exists in `output_dir`.
- `data.erasure_warning_external_copies` — corpus-row erasure only, and only when you pass `--config`: the config's `webhook` block is non-empty (a literal `url`, or a `url_env` variable exported in the purge environment); downstream consumers may have received notices.

### What it does NOT do

- **Re-train models.** Removing a row from the corpus does not unmemorise it from already-trained weights — full retraining without the erased row is the only proper mitigation. The tool emits `data.erasure_warning_memorisation` so the gap is visible.
- **Delete the audit log.** The tool records the erasure as a new event rather than rewriting history, and never deletes an audit-log entry. Whether entries that name the data subject may be kept is the controller's decision, and it has to be documented. If an Article 17(1) ground applies, the entries stay only where an Article 17(3) exception covers them: 17(3)(b) applies only to the extent processing is necessary for compliance with a legal obligation under Union or Member State law to which the controller is subject (or for a task in the public interest or in the exercise of official authority), and 17(3)(e) covers legal claims. If no Article 17(1) ground applies (for example the entries are still necessary for the purpose they were collected for), erasure is not owed, but the request still gets a reasoned answer within the Article 12 deadline, with the right to complain (Article 12(4)).
- **Remove an unmarked legacy `final_model.staging/`.** `--kind staging` deletes `final_model.staging.<run_id>/`, and the legacy `final_model.staging/` only when it holds a `staging_run_id` file equal to the run id. ForgeLM does not write that marker, so remove an unmarked legacy directory yourself.
- **Find the standard compliance bundle with `--kind artefacts`.** The trainer writes fixed basenames (`compliance_report.json`, `training_manifest.yaml`, ...) that contain no run id, and the resolver matches only filenames that embed it. On a normal output directory the command finds nothing, emits `data.erasure_failed` (`NoMatchingArtefacts`), exits 1 and deletes nothing; remove the bundle through your storage layer.
- **Push notices to downstream consumers.** Webhook receivers, dataset mirrors, and deployed model endpoints are the operator's runtime-layer responsibility (GDPR Article 19 requires telling each recipient the data was disclosed to about the erasure, unless that proves impossible or involves disproportionate effort, and telling the data subject about those recipients on request; Article 17(2) adds reasonable steps where the controller made the data public). The `data.erasure_warning_external_copies` event is the explicit reminder.
- **Make the whole response.** A complete response also needs steps ForgeLM does not perform: verifying the requester's identity (Article 12(6)); answering within one month of receipt (Article 12(3); extendable by two further months for complex or numerous requests, with notice in the first month); giving the data subject a copy of the data and the Article 15(1)(a)-(h) information (Article 15(3)) without adversely affecting other people's rights (Article 15(4)); deciding whether an Article 17(1) ground applies and which Article 17(3) exception, if any, covers what you keep; telling recipients (Article 19); re-running the access search after a purge to confirm the rows are gone; and, if you refuse a request, giving reasons and the right to complain within the same deadline (Article 12(4)). Whether the trained model counts as personal data is a separate question (EDPB Opinion 28/2024).

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

The chain row never carries the cleartext identifier, only a salted digest. The digest is pseudonymised, not anonymous: the salt file sits next to the log, so whoever can read both can test a guessed identifier against it. Protect the log and the salt with the same access controls and treat the digests as personal data.

### `--type custom` regex caveat

`--type custom` interprets `--query` as a Python regex. On POSIX main-thread invocations a 30s per-file SIGALRM budget guards against ReDoS hangs. **On Windows AND on POSIX worker threads the SIGALRM guard is a no-op** — operators running `--type custom` from a worker thread or on Windows must vet their regex themselves.

## Salt + audit-secret separation

The per-output-dir salt at `<output_dir>/.forgelm_audit_salt` (mode `0600`, atomic O_EXCL write on first use) is the **identifier-hash salt**. It does NOT participate in the audit-chain HMAC key, which is derived independently as `SHA-256(FORGELM_AUDIT_SECRET ‖ run_id)` inside ForgeLM's `AuditLogger`.

The two primitives are intentionally separate:

- Rotating `FORGELM_AUDIT_SECRET` rotates the chain HMAC key **and**, whenever the variable is set, the identifier-hash salt: `forgelm purge` XORs the first 16 bytes of the secret into the per-output-dir salt and records `salt_source=env_var`, so its `target_id` digests change with the secret. A plaintext `forgelm reverse-pii` query (no `--salt-source`) resolves the salt the same way, so it correlates with a purge run under the same secret state. `--salt-source` is a different feature: a hash-mask scan for corpora whose identifiers were hashed outside ForgeLM, which finds nothing in a plaintext corpus.
- Inspecting either primitive does not reveal the other.
- The per-output-dir salt is always an input to identifier hashing and persists for the lifetime of the output dir; the secret, when set, is a second input.

## Exit codes

Both subcommands follow the project's `0/1/2/3/4` contract:

| Code | Meaning |
|---|---|
| 0 | Success. `forgelm purge --check-policy` exits 0 even when violations are found (report-not-gate semantic). `forgelm reverse-pii` exits 0 even when no matches are found (a zero-match scan only says the literal text is absent from the files you scanned; it does not by itself confirm that you hold no data about the person). |
| 1 | Config error: a missing `--kind`, `--row-id` without `--corpus`, malformed regex, no staging directory / compliance file matching `--run-id`, a `--config` that cannot be loaded. |
| 2 | Runtime error: I/O failure, permission denied, unwritable `--audit-dir`, an argument-parser usage error (two modes together, `--kind logs`), ReDoS SIGALRM timeout, atomic-rename failure. |

Codes 3 (`EXIT_EVAL_FAILURE`) and 4 (`EXIT_AWAITING_APPROVAL`) are not part of either subcommand's surface.

## Common pitfalls

:::warn
**Pasting the subject's identifier into `--justification`.** The justification is recorded verbatim in the audit chain. Reference your internal ticket id ("GDPR Art.17 ticket #1234") instead.
:::

:::warn
**Running `forgelm purge` without checking for memorisation.** If a trained model exists, the row is gone from disk but may still be encoded in the weights. The `data.erasure_warning_memorisation` event surfaces the gap only when a `final_model/` sits directly under `--output-dir` — which defaults to the corpus's directory, so pass your training output directory; act on it (re-train without the erased row, or document the residual risk in your DPIA).
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
