---
title: Audit Log
description: Append-only event log over training, evaluation, and revert decisions — Article 12.
---

# Audit Log

EU AI Act Article 12 requires high-risk AI systems to maintain logs of operationally relevant events. ForgeLM's `audit_log.jsonl` is an append-only, SHA-256-anchored sequence of events covering training start, evaluation gates, auto-revert decisions, and model export.

## Format

One JSON object per line:

```jsonl
{"ts":"2026-04-29T14:01:32Z","seq":1,"event":"training.started","run_id":"abc123","operator":"ci-runner@ml","_hmac":"..."}
{"ts":"2026-04-29T14:33:08Z","seq":2,"event":"audit.classifier_load_failed","classifier":"meta-llama/Llama-Guard-3-8B","reason":"...","_hmac":"..."}
{"ts":"2026-04-29T14:33:10Z","seq":3,"event":"model.reverted","reason":"safety.regression","metrics":{...},"_hmac":"..."}
{"ts":"2026-04-29T14:33:11Z","seq":4,"event":"pipeline.completed","exit_code":0,"prev_hash":"sha256:beef...","_hmac":"..."}
```

(See the "Event types" table below and the [Audit Event Catalog on GitHub](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/audit_event_catalog.md) for the full canonical list. Earlier drafts referenced `run_start` / `run_complete` / `data_audit_complete` / `training_epoch_complete` / `benchmark_complete` / `safety_eval_complete` / `auto_revert` — none of those names ship; no call site in `forgelm/` emits them.)

Every entry has:
- **`ts`** — ISO-8601 UTC timestamp.
- **`seq`** — monotonic sequence number within the run (resets per-run).
- **`event`** — event type (see below).
- **`prev_hash`** — SHA-256 of the previous entry (chained for tamper-evidence).
- Event-specific fields.

## Event types

| Event | When emitted |
|---|---|
| `training.started` | Trainer enters fine-tuning. |
| `pipeline.completed` | End-to-end CLI run returned exit code 0. |
| `pipeline.failed` | Pipeline aborted with an error. |
| `model.reverted` | Auto-revert restored a previous checkpoint after a quality regression. |
| `human_approval.required` | `evaluation.require_human_approval=true` paused the run for an operator decision. |
| `human_approval.granted` | Operator approved a paused gate via `forgelm approve`. |
| `human_approval.rejected` | Operator rejected a paused gate via `forgelm reject`. |
| `audit.classifier_load_failed` | Safety classifier (e.g. Llama Guard) failed to load. |
| `compliance.governance_exported` | EU AI Act Article 10 governance report written. |
| `compliance.artifacts_exported` | Annex IV bundle (manifest + model card + audit zip) written. |
| `data.erasure_*` | Six-event family covering `forgelm purge` lifecycle (Article 17). |
| `data.access_request_query` | `forgelm reverse-pii` invocation (GDPR Article 15). |
| `cli.legacy_flag_invoked` | A deprecated CLI flag was used. |

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
✓ 87 entries, all timestamps monotonic
✓ all prev_hash chains valid
✓ no gaps in seq numbers
```

If `verify-audit` reports a chain break, the log was modified after generation. Investigate before treating it as evidence.

## Per-run

Each training run writes its own `<output_dir>/audit_log.jsonl` (top-level — not under `compliance/`) plus a genesis-pin sidecar `<output_dir>/audit_log.jsonl.manifest.json`. There is no project-wide global log file. For cross-run history, ship every run's output directory to the same upstream store (S3 prefix, ledger DB) and correlate by `run_id`.

## Configuration

There is **no** `compliance.audit_log:` block. The audit log is not a knob to enable/disable — every ForgeLM run automatically writes `<output_dir>/audit_log.jsonl`. To enable HMAC chaining, set `FORGELM_AUDIT_SECRET` in the env before invoking the trainer; there is no additional YAML knob.

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
$ jq -r '.event + "\t" + .ts' checkpoints/run/audit_log.jsonl
training.started               2026-04-29T14:01:32Z
audit.classifier_load_failed   2026-04-29T14:33:08Z
model.reverted                 2026-04-29T14:33:10Z
pipeline.completed             2026-04-29T14:33:11Z
```

For dashboards, the JSONL flows naturally into Loki, OpenSearch, or any log-aggregation tool.

## Common pitfalls

:::warn
**Editing the log "to fix a typo".** Don't. Even cosmetic edits break the chain hash and undermine the audit value. If you genuinely need to amend information, append a new event of type `correction` with a `corrects_seq` reference.
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
