---
title: Audit Log
description: Append-only event log over training, evaluation, and revert decisions — Article 12.
---

# Audit Log

EU AI Act Article 12 requires high-risk AI systems to maintain logs of operationally relevant events. ForgeLM's `audit_log.jsonl` is an append-only, SHA-256-anchored sequence of events covering training start, evaluation gates, auto-revert decisions, and model export.

## Format

One JSON object per line:

```jsonl
{"timestamp":"2026-04-29T14:01:32Z","run_id":"abc123","operator":"ci-runner@ml","event":"training.started","prev_hash":"genesis","_hmac":"..."}
{"timestamp":"2026-04-29T14:33:08Z","run_id":"abc123","operator":"ci-runner@ml","event":"audit.classifier_load_failed","prev_hash":"sha256:1a2b...","classifier":"meta-llama/Llama-Guard-3-8B","reason":"...","_hmac":"..."}
{"timestamp":"2026-04-29T14:33:10Z","run_id":"abc123","operator":"ci-runner@ml","event":"model.reverted","prev_hash":"sha256:3c4d...","reason":"safety","detail":"safe_ratio below threshold","_hmac":"..."}
{"timestamp":"2026-04-29T14:33:11Z","run_id":"abc123","operator":"ci-runner@ml","event":"pipeline.completed","prev_hash":"sha256:5e6f...","success":true,"_hmac":"..."}
```

(See the "Event types" table below and the [Audit Event Catalog on GitHub](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/audit_event_catalog.md) for the full canonical list. Earlier drafts referenced `run_start` / `run_complete` / `data_audit_complete` / `training_epoch_complete` / `benchmark_complete` / `safety_eval_complete` / `auto_revert` — none of those names ship; no call site in `forgelm/` emits them.)

Every entry has:
- **`timestamp`** — ISO-8601 UTC timestamp.
- **`run_id`** — the run that emitted the entry.
- **`operator`** — the resolved operator identity (`$FORGELM_OPERATOR`, or `<user>@<host>`).
- **`event`** — event type (see below).
- **`prev_hash`** — SHA-256 of the previous entry (chained for tamper-evidence; the first entry is `"genesis"`).
- **`_hmac`** — per-line HMAC tag, present only when `FORGELM_AUDIT_SECRET` is set.
- Event-specific fields.

There is **no** `seq` field. Gap- and deletion-detection rest entirely on the
`prev_hash` chain (and the genesis-manifest sidecar), not on sequence numbers.

## Event types

| Event | When emitted |
|---|---|
| `training.started` | Trainer enters fine-tuning. |
| `pipeline.completed` | End-to-end CLI run returned exit code 0. |
| `pipeline.failed` | Pipeline aborted with an error. |
| `model.reverted` | Auto-revert fired after a quality regression and deleted the saved model directory. Nothing is restored. |
| `human_approval.required` | `evaluation.require_human_approval=true` paused the run for an operator decision. |
| `human_approval.granted` | Operator approved a paused gate via `forgelm approve`. |
| `human_approval.rejected` | Operator rejected a paused gate via `forgelm reject`. |
| `audit.classifier_load_failed` | Safety classifier (e.g. Llama Guard) failed to load. |
| `compliance.governance_exported` | EU AI Act Article 10 governance report written. |
| `compliance.artifacts_exported` | Annex IV bundle (manifest + model card + audit zip) written. |
| `data.erasure_*` | Six-event family covering `forgelm purge` lifecycle (Article 17). |
| `data.access_request_query` | `forgelm reverse-pii` invocation (GDPR Article 15). |

The full event catalog (with payload schema and emitting site) lives in the
[Audit Event Catalog on GitHub](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/audit_event_catalog.md).

## Append-only by design

ForgeLM never rewrites prior log entries. New events go at the end. The chained `prev_hash` makes any modification detectable: if you change entry N, every entry from N+1 onwards has wrong `prev_hash` references.

:::warn
**Convention, not enforcement.** The toolkit writes append-only and hashes the chain, but the file lives on your filesystem — anyone with write access can edit it. For real tamper-evidence, ship the log to a separate write-once store (S3 Object Lock, ledger DB, HSM). This is your operational responsibility.
:::

## Verifying integrity

```shell
$ forgelm verify-audit <output_dir>/audit_log.jsonl
OK: 87 entries verified
```

With `FORGELM_AUDIT_SECRET` set, pass `--require-hmac` and the success line reads `OK: 87 entries verified (HMAC validated)`. A tampered or truncated log fails with `FAIL at line N: <reason>` (a `prev_hash` chain break, an HMAC mismatch, or a genesis-manifest mismatch). Investigate before treating it as evidence.

## Per-run

Each training run writes its own `<output_dir>/audit_log.jsonl` (top-level — not under `compliance/`) plus a genesis-pin sidecar `<output_dir>/audit_log.jsonl.manifest.json`. There is no project-wide global log file. For cross-run history, ship every run's output directory to the same upstream store (S3 prefix, ledger DB) and correlate by `run_id`.

## Configuration

There is **no** `compliance.audit_log:` block. The audit log is not a knob to enable/disable — every ForgeLM run automatically writes `<output_dir>/audit_log.jsonl`. To enable HMAC chaining, set `FORGELM_AUDIT_SECRET` in the env before invoking the trainer; there is no additional YAML knob.

Use a strong secret: 32+ random bytes from a secret manager. A short, low-entropy `FORGELM_AUDIT_SECRET` is accepted but logs a weak-secret WARNING (below 16 characters) because the per-line HMAC's strength is bounded by the secret's entropy. ForgeLM is not a key-management system — it consumes the secret, it does not generate or rotate it.

## Forwarding to external stores

ForgeLM does **not** ship a built-in log-forwarding layer. There is no `compliance.audit_log.forward_to:` block. Forward the log operationally:

```bash
# Use Filebeat / Fluent Bit / Vector to tail the JSONL and ship to S3 Object Lock / Splunk / Datadog.
filebeat -c filebeat.yml -e
```

Or upload post-run:

```bash
aws s3 cp <output_dir>/audit_log.jsonl s3://compliance-audit-logs/forgelm/<run_id>/ --no-progress
```

`forgelm verify-audit <output_dir>/audit_log.jsonl --require-hmac` afterwards confirms the chain still verifies after upload to S3.

## Reading the log

For human review:

```shell
$ jq -r '.event + "\t" + .timestamp' checkpoints/run/audit_log.jsonl
training.started               2026-04-29T14:01:32Z
audit.classifier_load_failed   2026-04-29T14:33:08Z
model.reverted                 2026-04-29T14:33:10Z
pipeline.completed             2026-04-29T14:33:11Z
```

For dashboards, the JSONL flows naturally into Loki, OpenSearch, or any log-aggregation tool.

## Common pitfalls

:::warn
**Editing the log "to fix a typo".** Don't. Even cosmetic edits break the chain hash and undermine the audit value. If you genuinely need to amend information, append a new event that references the run and timestamp of the entry being corrected — never rewrite the original line.
:::

:::warn
**Storing the log only on training-host disks.** A failed disk = lost audit evidence. Always forward to durable storage (S3 with versioning + Object Lock, ledger DB).
:::

:::tip
**Chain logs across runs in production.** When promoting a checkpoint to production, append a `model_promoted` event referencing the previous version. Auditors love a continuous chain of custody from training to deployment.
:::

## See also

- [Annex IV](#/compliance/annex-iv) — the technical doc that points at the audit log.
- [Auto-Revert](#/evaluation/auto-revert) — produces the `model.reverted` events.
- [Human Oversight](#/compliance/human-oversight) — produces the approval events.
