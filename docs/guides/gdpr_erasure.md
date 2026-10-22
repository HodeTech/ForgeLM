# GDPR Right-to-Erasure Guide (`forgelm purge`)

> **Article 17 of the EU General Data Protection Regulation** (the
> "right to be forgotten") gives data subjects the right to request
> erasure of personal data a controller holds about them. ForgeLM's
> `forgelm purge` subcommand is one step of answering such a request: it
> removes rows from the training corpora and run-scoped artefacts a ForgeLM
> deployment produced or consumed. Identifying the person, deciding whether
> an Article 17(1) ground applies, answering within the Article 12 deadline
> and everything about the trained weights remain your process.

## What `forgelm purge` does (and does not)

**Does:**

- Erases a single row from a JSONL training corpus (`--row-id <id> --corpus <path>`).
- Deletes a run's staging directory, and compliance files whose names embed the run id (`--run-id <id> --kind {staging,artefacts}`; `artefacts` does not find the standard bundle, see below).
- Reports retention-policy violations against the loaded config's `retention:` block (`--check-policy`).
- Records every action with a tamper-evident audit-event chain — `request → completed` (or `request → failed`) — so a forensic reviewer can reconstruct exactly what happened.
- Hashes operator-supplied row identifiers with a per-output-dir salt before they enter the audit log, so the log does not hold the identifier in clear (Article 5(1)(c) data minimisation). The digest is personal data, not anonymous data (recital 26), and it counts as pseudonymisation (Article 4(5)) only if the additional information is kept separately under its own controls. By default the `.forgelm_audit_salt` file sits next to the log, so anyone who can read both can recompute a digest from a guessed identifier; set `FORGELM_AUDIT_SECRET` (it is XOR-ed into the salt, so the file alone is no longer enough) and protect the log and the salt like personal data.

**Does not:**

- Re-train models. Removing a row from the corpus does not unmemorise it from already-trained weights — full retraining without the erased row is the only proper mitigation. The tool emits `data.erasure_warning_memorisation` when a `final_model/` directory exists directly under `--output-dir` (which defaults to the corpus's directory, so pass your training output directory), so the operator sees the gap. It cannot tell which runs consumed the corpus.
- Delete entries from the audit log itself. The tool records the erasure as a new event rather than rewriting history, and never deletes an audit-log entry. Whether entries that name the data subject may be kept is the controller's decision, and it has to be documented. If an Article 17(1) ground applies, the entries stay only where an Article 17(3) exception covers them: 17(3)(b) applies only to the extent processing is necessary for compliance with a legal obligation under Union or Member State law to which the controller is subject (or for a task in the public interest or in the exercise of official authority), and 17(3)(e) covers the establishment, exercise or defence of legal claims. If no Article 17(1) ground applies (the entries still being necessary for the purpose they were collected for rules out only 17(1)(a); also check consent withdrawal, an Article 21 objection and unlawful processing), erasure is not owed, but the request still gets an answer without delay and at the latest within one month of receipt, giving the reasons and the possibility of complaining to a supervisory authority and seeking a judicial remedy (Article 12(4)).
- Push erasure notices to downstream consumers. Webhook receivers, dataset mirrors, deployed model endpoints, and third-party fine-tunes derived from a published checkpoint are the operator's runtime-layer responsibility (GDPR Article 19 requires telling each recipient the data was disclosed to about the erasure, unless that proves impossible or involves disproportionate effort, and telling the data subject about those recipients on request; Article 17(2) adds reasonable steps where the controller made the data public). The tool emits `data.erasure_warning_external_copies` in row mode when the config you pass with `--config` has a webhook block, so a downstream consumer querying the audit log sees the explicit reminder.
- Erase backups. Replicas, snapshots, and storage backups outside the operator's `<output_dir>` are infrastructure-side concerns.
- Make the whole response. A complete response also needs steps ForgeLM does not perform: verifying the requester's identity with reasonable measures (recital 64; Article 12(6) lets you ask for more information where you have reasonable doubts); answering within one month of receipt (Article 12(3); extendable by two further months for complex or numerous requests, with notice in the first month); for an access request, giving the data subject a copy of the data and the Article 15(1)(a)-(h) information (Article 15(3)) and, where the data went to a third country, the Article 46 safeguards (Article 15(2)), without adversely affecting other people's rights (Article 15(4)); for an erasure request, deciding whether an Article 17(1) ground applies and which Article 17(3) exception, if any, covers what you keep; telling recipients (Article 19); re-running the access search after a purge to confirm the rows are gone; and, if you refuse a request, informing the requester without delay and at the latest within one month of the reasons, the possibility of complaining and the possibility of seeking a judicial remedy (Article 12(4)). Whether the trained model counts as personal data is a separate question (EDPB Opinion 28/2024).

## Three modes

### 1. Corpus-row erasure

```shell
$ forgelm purge \
    --row-id "ali@example.com" \
    --corpus train.jsonl \
    --justification "GDPR Art.17 ticket #1234" \
    --output-dir ./outputs
```

The tool:

1. Resolves the per-output-dir salt at `<output_dir>/.forgelm_audit_salt` (creates it on first use; mode `0600`; XOR'd with `FORGELM_AUDIT_SECRET[:16]` if the env var is set). **Note:** this XOR feeds **identifier hashing only** — the audit-chain HMAC key is derived independently as `SHA-256(FORGELM_AUDIT_SECRET ‖ run_id)` (see [`docs/qms/access_control.md`](../qms/access_control.md) §3.4 / `AuditLogger.__init__` in `forgelm/compliance.py`). The two primitives are intentionally separate.
2. Writes `data.erasure_requested` to the audit log with the **hashed** target_id (`SHA-256(salt + value)`) and the operator-supplied justification.
3. Locates the matching JSONL row by its `id` (or `row_id`) field — line-number fallback is **rejected** to defend against silent wrong-row deletion on a re-ordered file. ForgeLM does not currently provide an id-population helper (a `forgelm audit --add-row-ids` flag is on the Phase 28 backlog); operators with id-less corpora must pre-populate ids with an operator-side script before invoking purge (the GDPR / KVKK user-manual page carries a tested Python script that gives every row without one a UUID and refuses duplicate ids).
4. Atomically rewrites the corpus (writes a sibling temp file + `os.replace`); operators get either the full pre-erasure file or the full post-erasure file, never a partial state.
5. Detects warning conditions and emits the matching events (memorisation, synthetic-data presence, external copies). The external-copies check reads the `webhook` block of the config you pass with `--config` (a literal `url`, or a `url_env` variable that is exported in the purge environment); without `--config`, or with an unset `url_env`, it cannot fire.
6. Writes `data.erasure_completed` (or `data.erasure_failed` if the disk operation raised) so the chain reflects the final state.

Multi-row matches default to refusal (`--row-matches one`); pass `--row-matches all` to delete every row sharing the id (operator confirms intent).

Use `--dry-run` to preview the deletion + emit the audit chain (with `dry_run=true`) without modifying disk.

### 2. Run-scoped artefact erasure

```shell
$ forgelm purge --run-id fg-abc123def456 --kind staging --output-dir ./outputs
$ forgelm purge --run-id fg-abc123def456 --kind artefacts --output-dir ./outputs
```

- `--kind staging` removes `<output_dir>/final_model.staging.<run_id>/`, and the legacy `final_model.staging/` only if it carries a `staging_run_id` marker whose content equals the `<run_id>` (ForgeLM does not write that marker; otherwise remove the legacy directory yourself).
- `--kind artefacts` removes any file directly under `<output_dir>/compliance/` whose name contains the `<run_id>` bounded by `_`, `-`, `.` or the start/end of the name (not only `.json`). **It does not find the standard bundle:** the trainer writes fixed basenames (`compliance_report.json`, `training_manifest.yaml`, ...) that contain no run id, so on a normal output directory the command finds nothing, emits `data.erasure_failed` (`NoMatchingArtefacts`), exits 1 and deletes nothing. That holds for a real run id (`fg-…`): a generic token that appears in a fixed name, such as `json` or `report`, does match those files and deletes them, so always pass the full run id. Remove the bundle through your storage layer until the resolver is fixed.
- `--kind logs` is **intentionally absent**: the audit log is append-only and the tool never deletes it (see the "Does not" list above for who decides whether its entries may be kept).

### 3. Retention-policy report

```shell
$ forgelm purge --check-policy --config configs/run.yaml --output-dir ./outputs
```

Read-only scan. Walks the output directory, compares each artefact's age (canonical: the run's first audit event for a `final_model.staging.<run_id>/` directory, else the audit log's genesis `timestamp`; fallback: filesystem `mtime` with `age_source=mtime` flag) against the loaded config's `retention:` horizons, and emits a structured violation list.

**Successful policy reports exit 0** (report-not-gate semantic per design §10 Q5). Config-load failures exit with `EXIT_CONFIG_ERROR` (non-zero): an explicit `--config` that is missing, unreadable, or fails Pydantic validation surfaces as `EXIT_CONFIG_ERROR` so the operator does not mistake "loader failed" for "no violations". Operators wiring a CI gate use `--output-format json` and test the report themselves, for example `jq -e '.success == true and has("count") and .count == 0'` under `set -o pipefail`: `count` is absent when no `retention:` block was loaded, so the gate fails instead of passing on a directory that was never checked, and `--output-dir` must be the real training output directory (a missing one reports `count: 0`). This keeps the public exit-code contract consistent across every ForgeLM subcommand.

## The `retention:` config block

```yaml
retention:
  audit_log_retention_days: 1825          # 5 years (ForgeLM default, not a legal period)
  staging_ttl_days: 7                     # age at which `purge --check-policy` reports a staging dir as overdue
  ephemeral_artefact_retention_days: 90   # compliance/ and data_audit_report.json, aged from the audit log's first event
  raw_documents_retention_days: 90        # only <output_dir>/raw_documents/ and ingestion_output/ (ForgeLM does not create them)
  enforce: log_only                       # reserved: no command acts on it yet (log_only / warn_on_excess / block_on_excess)
```

Setting any horizon to `0` turns the report off for that artefact kind (nothing is deleted either way). The horizons are reported by `forgelm purge --check-policy` (read-only; exits 0 however many it finds); nothing is deleted or blocked automatically. ForgeLM does not create `raw_documents/` or `ingestion_output/`; `raw_documents_retention_days` has an effect only if you keep source documents there. `enforce` is reserved: no command acts on it yet, so all three values behave like `log_only`.

## Removed: `evaluation.staging_ttl_days`

The `evaluation.staging_ttl_days` field (originally shipped in v0.5.5) was **removed in v0.8.0**. Use `retention.staging_ttl_days` — the canonical field — instead. YAML files that still set `evaluation.staging_ttl_days` will fail config-load with `EXIT_CONFIG_ERROR` in v0.8.0 and later.

## Audit-event vocabulary

Six new events ship with `forgelm purge` (catalogued in `docs/reference/audit_event_catalog.md`):

| Event | When | Key fields |
|---|---|---|
| `data.erasure_requested` | First step of any `forgelm purge --row-id` / `--run-id` invocation, before any deletion (`--check-policy` is read-only and emits no audit events) | `target_kind` ∈ `{row, staging, artefacts}`, `target_id` (hashed in row mode), `salt_source` (row mode), `corpus_path` (row), `output_dir` (run), `justification`, `dry_run` |
| `data.erasure_completed` | Successful deletion finishes | All `requested` fields + `bytes_freed`, `files_modified`, `pre_erasure_line_number` (row mode), `match_count` (row mode) |
| `data.erasure_failed` | Disk operation raised, OR no matching row/run found, OR multi-row policy refused on ambiguity | All `requested` fields + `error_class`, `error_message` |
| `data.erasure_warning_memorisation` | **Row mode only.** Row erasure × a `final_model/` directory exists directly under `--output-dir`. (Run mode does not emit memorisation warnings — the run-scoped `staging` / `artefacts` kinds are by definition pre-promotion.) | All `completed` fields + `affected_run_ids` |
| `data.erasure_warning_synthetic_data_present` | **Row mode only.** Row erasure × `synthetic_data*.jsonl` exists in `output_dir`. (Run mode does not surface this warning — synthetic data is corpus-scoped, not run-scoped.) | All `completed` fields + `synthetic_files` |
| `data.erasure_warning_external_copies` | **Row mode only, when `--config` is given.** Loaded config has a webhook block. (Run mode never loads `--config`.) | All `completed` fields + `webhook_targets` |

## Verifying a chain post-erasure

`forgelm verify-audit <output_dir>/audit_log.jsonl` continues to validate the chain after any number of erasure events — the tool appends new events, never rewrites old ones, so the SHA-256 hash chain stays intact.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success, or a successful `--check-policy` report (report-not-gate semantic). |
| 1 | Config error: unknown `--row-id`, no staging directory / compliance file matching `--run-id` (including `--kind artefacts` against the standard fixed-name bundle), a missing `--corpus` or `--row-id` without `--corpus`, a missing `--kind`, no mode selected, or `--check-policy --config <path>` that is missing / unreadable / fails Pydantic validation. |
| 2 | Runtime error: I/O failure, permission denied, atomic-rename failure; also an argument-parser usage error (two modes together such as `--row-id` with `--run-id`, `--kind logs`), which prints usage instead of a JSON envelope. |

`--check-policy` never returns code 3. A successful report exits 0; a failure to load the supplied `--config` exits with `EXIT_CONFIG_ERROR` (non-zero) rather than degrading to a misleading "no violations" report. Operators wiring a CI gate compute the violation count from JSON output themselves (per design §10 Q5).

## Article 15 right-of-access (`forgelm reverse-pii`)

The companion subcommand for the *other* GDPR data-subject right —
"is my data in the corpus?" — is `forgelm reverse-pii`.  Where
`purge` answers "delete my row," `reverse-pii` answers "find every
line where my identifier appears."

```shell
# Plaintext residual scan: detects mask leaks (operator believed
# the corpus was masked but a residual span slipped through).
$ forgelm reverse-pii --query "alice@example.com" --type email \
    --output-dir ./outputs data/*.jsonl

# Hash-mask scan: corpus was masked through an EXTERNAL pipeline
# that embedded SHA256(salt + identifier) digests using the same
# per-output-dir salt purge uses for `target_id` audit-event
# hashing.  ForgeLM does not ship a hash-replacement ingest
# strategy of its own; this mode is for operators who built one
# outside the toolkit using purge's salt as the shared secret.
$ forgelm reverse-pii --query "alice@example.com" --type email \
    --salt-source per_dir --output-dir ./outputs data/*.jsonl
```

The audit chain records `data.access_request_query` with the
identifier *salted-and-hashed* using the same per-output-dir salt
that `forgelm purge` uses for its `target_id` field — Article 15
access requests must not themselves leak the subject's data into the
audit log.  The salted form is a stable per-identifier fingerprint
that lets a compliance reviewer correlate Article 17 (purge) and
Article 15 (reverse-pii) events for the same subject without seeing the
cleartext.  The digests match only when the `--row-id` and `--query` values
are the same text, both commands use the same `--output-dir`, and
`FORGELM_AUDIT_SECRET` is in the same state for both (unset, or the same
value); run the access request in its default plaintext mode: `--salt-source` is
not needed for the digests to match, and with it the hash-mask scan looks
for digests inside the corpus, so a plaintext corpus reports zero matches
even when the person is there.  When
the ids differ, link a request to its erasure
through the ticket reference you pass to `forgelm purge --justification`.

**Identifier types** (`--type`): `literal` (default), `email`,
`phone`, `tr_id`, `us_ssn`, `iban`, `credit_card`, `custom`.  All
non-`custom` types treat the query as a literal substring (no regex
shape match — that's the *audit-time* detector's job, not the
access-request answer).  `custom` interprets the query as a Python
regex; on POSIX **main-thread** invocations a 30s per-file SIGALRM
budget guards against ReDoS hangs.  On Windows AND on POSIX worker
threads the SIGALRM guard is a no-op (signal handlers must be
installed from the main thread); operators running
`forgelm reverse-pii --type custom` from a worker thread or on
Windows must vet their regex themselves.

**Audit-dir default**: the audit chain is written to
`<output-dir>/audit_log.jsonl` by default — the same path
`forgelm purge` uses, so a `verify-audit` run correlates Article 17
(erasure) and Article 15 (access) events for the same subject in
one chain.  Pass `--audit-dir <writable-dir>` to override; an
explicit `--audit-dir` that the dispatcher cannot write to refuses
the run with `EXIT_TRAINING_ERROR` rather than silently dropping
the Article 15 forensic record.

**Exit codes:** `0` = scan completed (matches list may be empty);
`1` = config error (empty query, malformed regex, empty glob); `2` =
runtime error (mid-scan I/O failure).  See
[`../usermanuals/en/reference/json-output.md`](../usermanuals/en/reference/json-output.md)
for the JSON envelope schema.

## See also

- `docs/qms/sop_data_management.md` — the data-lifecycle SOP (collection, quality, storage); it does not contain a retention or erasure procedure, so write one from this guide.
- `docs/usermanuals/en/compliance/overview.md` — the operator-facing compliance overview that links here.
- [`docs/design/gdpr_erasure.md`](../design/gdpr_erasure.md) — the Phase 20 design document that this implementation realises.
