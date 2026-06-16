# GDPR Right-to-Erasure Guide (`forgelm purge`)

> **Article 17 of the EU General Data Protection Regulation** (the
> "right to be forgotten") gives data subjects the right to request
> erasure of personal data a controller holds about them. ForgeLM's
> `forgelm purge` subcommand is the operator-facing tool for honouring
> those requests against training corpora and run-scoped artefacts a
> ForgeLM deployment produced or consumed.

## What `forgelm purge` does (and does not)

**Does:**

- Erases a single row from a JSONL training corpus (`--row-id <id> --corpus <path>`).
- Deletes a run's staging directory or compliance bundle (`--run-id <id> --kind {staging,artefacts}`).
- Reports retention-policy violations against the loaded config's `retention:` block (`--check-policy`).
- Records every action with a tamper-evident audit-event chain — `request → completed` (or `request → failed`) — so a forensic reviewer can reconstruct exactly what happened.
- Hashes operator-supplied row identifiers with SHA-256 before they enter the audit log so the chain itself does not become a personal-data leak (Article 5(1)(c) data minimisation).

**Does not:**

- Re-train models. Removing a row from the corpus does not unmemorise it from already-trained weights — full retraining without the erased row is the only proper mitigation. The tool emits `data.erasure_warning_memorisation` when a `final_model/` exists for any run that consumed the corpus, so the operator sees the gap.
- Delete entries from the audit log itself. Article 17(3)(b) preserves audit / accounting records as a legal-obligation defence; the tool records the erasure as a new event rather than rewriting history.
- Push erasure notices to downstream consumers. Webhook receivers, dataset mirrors, deployed model endpoints, and third-party fine-tunes derived from a published checkpoint are the operator's runtime-layer responsibility (Article 17(2)). The tool emits `data.erasure_warning_external_copies` when the loaded config has a webhook block, so a downstream consumer querying the audit log sees the explicit reminder.
- Erase backups. Replicas, snapshots, and storage backups outside the operator's `<output_dir>` are infrastructure-side concerns.

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

1. Resolves the per-output-dir salt at `<output_dir>/.forgelm_audit_salt` (creates it on first use; mode `0600`; XOR'd with `FORGELM_AUDIT_SECRET[:16]` if the env var is set). **Note:** this XOR feeds **identifier hashing only** — the audit-chain HMAC key is derived independently as `SHA-256(FORGELM_AUDIT_SECRET ‖ run_id)` (see [`docs/qms/access_control.md`](../qms/access_control.md) §3.4 / `forgelm/compliance.py:104-114`). The two primitives are intentionally separate.
2. Writes `data.erasure_requested` to the audit log with the **hashed** target_id (`SHA-256(salt + value)`) and the operator-supplied justification.
3. Locates the matching JSONL row by its `id` (or `row_id`) field — line-number fallback is **rejected** to defend against silent wrong-row deletion on a re-ordered file. ForgeLM does not currently provide an id-population helper (a `forgelm audit --add-row-ids` flag is on the Phase 28 backlog); operators with id-less corpora must pre-populate ids with an operator-side script (e.g. `jq -c 'to_entries | with_entries(...)'` or a one-shot Python loop) before invoking purge.
4. Atomically rewrites the corpus (writes a sibling temp file + `os.replace`); operators get either the full pre-erasure file or the full post-erasure file, never a partial state.
5. Detects warning conditions and emits the matching events (memorisation, synthetic-data presence, external copies).
6. Writes `data.erasure_completed` (or `data.erasure_failed` if the disk operation raised) so the chain reflects the final state.

Multi-row matches default to refusal (`--row-matches one`); pass `--row-matches all` to delete every row sharing the id (operator confirms intent).

Use `--dry-run` to preview the deletion + emit the audit chain (with `dry_run=true`) without modifying disk.

### 2. Run-scoped artefact erasure

```shell
$ forgelm purge --run-id fg-abc123def456 --kind staging --output-dir ./outputs
$ forgelm purge --run-id fg-abc123def456 --kind artefacts --output-dir ./outputs
```

- `--kind staging` removes `<output_dir>/final_model.staging.<run_id>/` (and the legacy `final_model.staging/` if present).
- `--kind artefacts` removes `<output_dir>/compliance/*.json` files whose name embeds the `<run_id>`.
- `--kind logs` is **intentionally absent**: audit logs are append-only Article 17(3)(b) records and are not deleted by the tool.

### 3. Retention-policy report

```shell
$ forgelm purge --check-policy --config configs/run.yaml --output-dir ./outputs
```

Read-only scan. Walks the output directory, compares each artefact's age (canonical: audit-log genesis `timestamp`; fallback: filesystem `mtime` with `age_source=mtime` flag) against the loaded config's `retention:` horizons, and emits a structured violation list.

**Successful policy reports exit 0** (report-not-gate semantic per design §10 Q5). Config-load failures exit with `EXIT_CONFIG_ERROR` (non-zero): an explicit `--config` that is missing, unreadable, or fails Pydantic validation surfaces as `EXIT_CONFIG_ERROR` so the operator does not mistake "loader failed" for "no violations". Operators wiring a CI gate use `--output-format json` and pipe to `jq '.violations | length'` themselves; this keeps the public exit-code contract `0/1/2/3/4` consistent across every ForgeLM subcommand.

## The `retention:` config block

```yaml
retention:
  audit_log_retention_days: 1825          # 5 years (Article 12 record-keeping)
  staging_ttl_days: 7                     # one work-week to act on a `forgelm reject`
  ephemeral_artefact_retention_days: 90   # quarterly review cadence
  raw_documents_retention_days: 90        # ingestion-window before re-running data audit
  enforce: log_only                       # log_only / warn_on_excess / block_on_excess
```

Setting any horizon to `0` disables the policy for that artefact kind (retain indefinitely). `enforce` controls how the trainer pre-flight gate reacts to violations.

## Removed: `evaluation.staging_ttl_days`

The `evaluation.staging_ttl_days` field (originally shipped in v0.5.5) was **removed in v0.8.0**. Use `retention.staging_ttl_days` — the canonical field — instead. YAML files that still set `evaluation.staging_ttl_days` will fail config-load with `EXIT_CONFIG_ERROR` in v0.8.0 and later.

## Audit-event vocabulary

Six new events ship with `forgelm purge` (catalogued in `docs/reference/audit_event_catalog.md`):

| Event | When | Key fields |
|---|---|---|
| `data.erasure_requested` | First step of any `forgelm purge --row-id` / `--run-id` invocation, before any deletion (`--check-policy` is read-only and emits no audit events) | `target_kind` ∈ `{row, staging, artefacts}`, `target_id` (hashed in row mode), `salt_source` (row mode), `corpus_path` (row), `output_dir` (run), `justification`, `dry_run` |
| `data.erasure_completed` | Successful deletion finishes | All `requested` fields + `bytes_freed`, `files_modified`, `pre_erasure_line_number` (row mode), `match_count` (row mode) |
| `data.erasure_failed` | Disk operation raised, OR no matching row/run found, OR multi-row policy refused on ambiguity | All `requested` fields + `error_class`, `error_message` |
| `data.erasure_warning_memorisation` | **Row mode only.** Row erasure × `final_model/` exists for any run that consumed the corpus. (Run mode does not emit memorisation warnings — the run-scoped `staging` / `artefacts` kinds are by definition pre-promotion.) | All `completed` fields + `affected_run_ids` |
| `data.erasure_warning_synthetic_data_present` | **Row mode only.** Row erasure × `synthetic_data*.jsonl` exists in `output_dir`. (Run mode does not surface this warning — synthetic data is corpus-scoped, not run-scoped.) | All `completed` fields + `synthetic_files` |
| `data.erasure_warning_external_copies` | Loaded config has a webhook block (both modes). | All `completed` fields + `webhook_targets` |

## Verifying a chain post-erasure

`forgelm verify-audit <output_dir>/audit_log.jsonl` continues to validate the chain after any number of erasure events — the tool appends new events, never rewrites old ones, so the SHA-256 hash chain stays intact.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success, or a successful `--check-policy` report (report-not-gate semantic). |
| 1 | Config error: unknown `--row-id`, missing `--corpus`, mutually-exclusive flag combination, conflicting `staging_ttl_days` values, or `--check-policy --config <path>` that is missing / unreadable / fails Pydantic validation. |
| 2 | Runtime error: I/O failure, permission denied, atomic-rename failure. |

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
Article 15 (reverse-pii) events for the same subject (the digests
match) without seeing the cleartext.

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

- `docs/qms/sop_data_management.md` — the full data-lifecycle SOP including the retention + erasure procedures.
- `docs/usermanuals/en/compliance/overview.md` — the operator-facing compliance overview that links here.
- [`docs/design/gdpr_erasure.md`](../design/gdpr_erasure.md) — the Phase 20 design document that this implementation realises.
