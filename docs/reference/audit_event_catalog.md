# Audit Event Catalog

> **Audience:** ForgeLM operators, regulators, and downstream verifiers reviewing the EU AI Act Article 12 record-keeping artefacts.
> **Mirror:** [audit_event_catalog-tr.md](audit_event_catalog-tr.md)

This catalog enumerates every event ForgeLM may append to `audit_log.jsonl`, the append-only, hash-chained record-keeping artefact mandated by EU AI Act Article 12. Every entry shares a common envelope; the `event` field selects one of the rows below.

## Common envelope

Each line is a single JSON object with at least the following fields:

| Field         | Type    | Description                                                                                               |
|---------------|---------|-----------------------------------------------------------------------------------------------------------|
| `timestamp`   | string  | ISO-8601 UTC timestamp (`datetime.now(timezone.utc).isoformat()`).                                        |
| `run_id`      | string  | Stable per-training-run identifier (`fg-<uuid12>`).                                                       |
| `operator`    | string  | Human-attributable identity, resolved by `AuditLogger` in priority order: (1) `$FORGELM_OPERATOR` if set; (2) `<getpass.getuser()>@<hostname>` when the username can be resolved; (3) `anonymous@<hostname>` only when `getpass.getuser()` fails *and* `FORGELM_ALLOW_ANONYMOUS_OPERATOR=1` is set (otherwise the run aborts loudly). |
| `event`       | string  | Dotted event name from this catalog.                                                                      |
| `prev_hash`   | string  | SHA-256 of the previous line (`"genesis"` for the first entry). Forms the tamper-evident hash chain.      |
| `_hmac`       | string? | Present only when `FORGELM_AUDIT_SECRET` is set. HMAC-SHA-256 of the line without `_hmac`.                |
| _payload_     | varies  | Event-specific keys, listed per row below.                                                                |

The hash chain advances after the line lands on disk (`flush` + `fsync`), so an unclean shutdown leaves the chain intact for resume.

## Event vocabulary

### Pipeline lifecycle

| Event                      | When emitted                                                              | Payload (in addition to envelope)                                                        | Article |
|----------------------------|---------------------------------------------------------------------------|------------------------------------------------------------------------------------------|---------|
| `pipeline.initialized`     | `ForgeTrainer.__init__` finished wiring config + audit logger; emitted before any model load. | `model`, `trainer_type` | 12 |
| `training.started`         | Trainer begins a fine-tuning run.                                         | _(no payload — envelope only)_                                                           | 12      |
| `training.oom_recovery`    | OOM recovery path halved `per_device_train_batch_size` and retried (mid-training event). | `old_batch_size`, `new_batch_size`, `new_grad_accum` | 12 / 15 |
| `benchmark.evaluation_completed` | `lm-eval-harness` finished evaluating the configured benchmark suite. | `passed`, `average`, `scores`                       | 15 |
| `safety.evaluation_completed`    | Safety evaluation finished (Llama Guard / ShieldGemma run).            | `passed`, `safe_ratio`, `total_count`, `safety_score`, `categories` | 15 |
| `judge.evaluation_completed`     | LLM-as-judge scoring finished.                                          | `passed`, `average_score`                            | 15 |
| `pipeline.completed`       | End-to-end CLI run (training + evaluation + export) returned exit code 0. | `success`, `metrics_summary`                                                              | 12      |
| `pipeline.failed`          | Pipeline aborted with an error before completion.                         | `error`                                                                                  | 12      |
| `pipeline.started`         | Multi-stage pipeline orchestrator began a fresh run (not a `--resume-from`). | `pipeline_run_id`, `config_hash`, `stage_count`, `stage_names`                          | 12      |
| `pipeline.force_resume`    | `--resume-from` proceeded past a stored config-hash mismatch because `--force-resume` was set. | `pipeline_run_id`, `old_config_hash`, `new_config_hash`               | 12      |
| `pipeline.stage_started`   | A pipeline stage began executing (after stage-config merge + validation).  | `pipeline_run_id`, `stage_name`, `stage_index`, `input_model`, `input_source`           | 12      |
| `pipeline.stage_completed` | A pipeline stage finished — `gate_decision=passed` on success, `failed` on a non-revert failure. | `pipeline_run_id`, `stage_name`, `gate_decision`, `metrics` (success only), `auto_revert_triggered` (failure path only) | 12      |
| `pipeline.resume_refused`  | `--resume-from` was refused because a prior stage is still awaiting human approval (Article 14 gate not yet cleared). | `pipeline_run_id`, `requested_stage`, `blocking_stage`, `blocking_status` | 12, 14 |
| `pipeline.stage_gated`     | A stage halted at the Article 14 human-approval gate (exit 4); the pipeline stops pending operator action. | `pipeline_run_id`, `stage_name`, `gate_decision` (`approval_pending`), `staging_path` | 12, 14 |
| `pipeline.stage_reverted`  | A stage's post-train gate auto-reverted the model (`auto_revert_triggered=true`); the chain stops. | `pipeline_run_id`, `stage_name`, `gate_decision` (`failed`), `auto_revert_triggered` | 12, 15 |

### Article 14 — Human Oversight

| Event                        | When emitted                                                                                            | Payload                                              | Article |
|------------------------------|---------------------------------------------------------------------------------------------------------|------------------------------------------------------|---------|
| `human_approval.required`    | A gate marked `requires_human_approval: true` paused the pipeline awaiting an operator decision.        | `gate`, `reason`, `metrics`, `staging_path`, `run_id`                      | 14      |
| `human_approval.granted`     | Operator approved the paused gate via `forgelm approve`.                                                | `gate`, `approver`, `comment`, `run_id`, `promote_strategy`                | 14      |
| `human_approval.rejected`    | Operator rejected the paused gate via `forgelm reject`.                                                 | `gate`, `approver`, `comment`, `run_id`, `staging_path`                    | 14      |

### Article 15 — Model Integrity (auto-revert + safety)

| Event                          | When emitted                                                                                              | Payload                                                       | Article |
|--------------------------------|-----------------------------------------------------------------------------------------------------------|---------------------------------------------------------------|---------|
| `model.reverted`               | Auto-revert restored a previous checkpoint after a quality regression. _(Faz 8 — webhook-coupled.)_       | `reason` (the gate that fired: `benchmark` / `safety` / `judge` / etc.), `detail` (the human-readable failure reason from the gate) | 15      |
| `model.integrity_verified`     | Final-model integrity manifest (`model_integrity.json`) was written and re-hashed successfully after training. | `artifacts` (count of files re-hashed)                         | 15      |
| `audit.classifier_load_failed` | Safety classifier (e.g., Llama Guard) failed to load. The run still records `passed=False`.              | `classifier`, `reason`                                        | 15      |

### Article 11 + Annex IV — Compliance artefacts

| Event                            | When emitted                                                                | Payload                                          | Article    |
|----------------------------------|-----------------------------------------------------------------------------|--------------------------------------------------|------------|
| `compliance.governance_exported` | Article 10 data governance report written to disk.                          | `output_path`, `dataset_count`                   | 10         |
| `compliance.governance_failed`   | Governance report generation aborted (e.g., schema mismatch).               | `reason`                                         | 10         |
| `compliance.artifacts_exported`  | Annex IV technical documentation bundle (manifest, model card, audit zip). | `output_dir`, `files`                            | 11, Annex IV |

### Article 17 — GDPR Right-to-Erasure (Phase 21 — `forgelm purge`)

| Event                                      | When emitted                                                                                                  | Payload                                                                                                  | Article |
|--------------------------------------------|---------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------|---------|
| `data.erasure_requested`                   | First step of any `forgelm purge --row-id` / `--run-id` invocation, BEFORE any deletion.  `--check-policy` is read-only and emits no events. | `target_kind` ∈ `{row, staging, artefacts}`, `target_id` (hashed in row mode), `salt_source` (row mode), `corpus_path` (row), `output_dir` (run), `justification`, `dry_run` | 17      |
| `data.erasure_completed`                   | Successful deletion finished.                                                                                 | All `requested` fields + `bytes_freed`, `files_modified`, `pre_erasure_line_number` (row mode), `match_count` (row mode) | 17      |
| `data.erasure_failed`                      | Disk operation raised, OR no matching row/run found, OR multi-row policy refused on ambiguity.                | All `requested` fields + `error_class`, `error_message`                                                  | 17      |
| `data.erasure_warning_memorisation`        | Row erasure × `final_model/` exists for any run that consumed this corpus.                                    | All `completed` fields + `affected_run_ids`                                                              | 17      |
| `data.erasure_warning_synthetic_data_present` | Row erasure × `synthetic_data*.jsonl` exists in `output_dir`.                                              | All `completed` fields + `synthetic_files`                                                               | 17      |
| `data.erasure_warning_external_copies`     | Loaded config has a non-empty `webhook` block; downstream consumers may have received notices.                | All `completed` fields + `webhook_targets` (redacted URLs)                                               | 17      |

### Article 15 — GDPR Right-of-Access (Phase 38 — `forgelm reverse-pii`)

| Event                          | When emitted                                                                                                         | Payload                                                                                                                                      | Article |
|--------------------------------|----------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------|---------|
| `data.access_request_query`    | Every `forgelm reverse-pii` invocation, after the scan completes (or after a mid-scan I/O failure — with `error_*`). | `query_hash` (salted SHA-256 of raw identifier — never raw; reuses purge's per-output-dir salt), `identifier_type` ∈ `{literal, email, phone, tr_id, us_ssn, iban, credit_card, custom}`, `scan_mode` ∈ `{plaintext, hash}`, `salt_source` ∈ `{plaintext, per_dir, env_var}`, `files_scanned` (paths), `match_count`, optional `error_class`/`error_message` | GDPR Art. 15 |

### Air-gap pre-cache (Phase 35 — `forgelm cache-models` / `cache-tasks`)

| Event                                | When emitted                                                                  | Payload                                                                | Article |
|--------------------------------------|-------------------------------------------------------------------------------|------------------------------------------------------------------------|---------|
| `cache.populate_models_requested`    | `forgelm cache-models` invocation begins.                                     | `models`, `cache_dir`, `safety_classifier`                             | 12      |
| `cache.populate_models_completed`    | Every model downloaded successfully.                                          | All `requested` fields + `total_size_bytes`, `count`                   | 12      |
| `cache.populate_models_failed`       | One or more model downloads failed (transport, disk-full, HF auth).           | All `requested` fields + `models_completed`, `error_class`, `error_message` | 12 |
| `cache.populate_tasks_requested`     | `forgelm cache-tasks` invocation begins.                                      | `tasks`, `cache_dir`                                                   | 12      |
| `cache.populate_tasks_completed`     | Every lm-eval task dataset prepared successfully.                             | All `requested` fields + `count`                                       | 12      |
| `cache.populate_tasks_failed`        | Unknown task name OR dataset download failure.                                | All `requested` fields + `tasks_completed`, `error_class`, `error_message` | 12 |

### CLI / migration

| Event                       | When emitted                                                                                       | Payload                          | Article |
|-----------------------------|----------------------------------------------------------------------------------------------------|----------------------------------|---------|
| `cli.legacy_flag_invoked`   | A deprecated CLI flag was used (e.g., `--data-audit`).                                             | `flag`, `replacement`, `version` | 12      |

### Audit-system events (meta)

| Event                          | When emitted                                                                                      | Payload                              | Article |
|--------------------------------|---------------------------------------------------------------------------------------------------|--------------------------------------|---------|
| `audit.classifier_load_failed` | _(See Article 15 row above.)_                                                                     | `classifier`, `reason`               | 15      |

## Adding a new event

1. Pick a dotted name following the existing namespaces (`training.*`, `compliance.*`, `audit.*`, `human_approval.*`, `model.*`, `cli.*`).
2. Add a row to the table above, including the payload keys and the Article it supports.
3. Mirror the row to [audit_event_catalog-tr.md](audit_event_catalog-tr.md).
4. Emit via `AuditLogger.log_event(event, **payload)`. Never call `json.dump` directly into `audit_log.jsonl`; the hash chain depends on the canonical writer.

## Tamper-evidence summary

| Mechanism                | Defends against                                                       | Always on?                                           |
|--------------------------|-----------------------------------------------------------------------|------------------------------------------------------|
| SHA-256 hash chain       | Single-line edits, deletions, reorderings.                            | Yes.                                                 |
| Genesis manifest sidecar | Whole-log truncation back to "genesis".                               | Yes (written once on first event).                   |
| `flock(LOCK_EX)`         | Interleaved writes from concurrent trainers sharing the directory.    | Yes (Unix); no-op on Windows.                        |
| `flush` + `fsync`        | Power-cut / kernel-panic loss between buffer write and chain advance. | Yes.                                                 |
| HMAC-SHA-256 per line    | Forged re-signing after log rewrite.                                  | Only when `FORGELM_AUDIT_SECRET` is set.             |

## Webhook events

Webhook payloads (Slack / Teams / generic HTTP) are a separate vocabulary scoped to operator notifications, not the regulatory record. Webhook events are **not** appended to `audit_log.jsonl`; they ride the side-channel notification bus. The canonical lifecycle vocabulary is also documented in [logging-observability.md](../standards/logging-observability.md).

These five lifecycle events are the **only** events that webhook
receivers should expect from `WebhookNotifier`. Each one mirrors a
corresponding audit-log event so a downstream operator can correlate
webhook ping → audit entry by `run_name` + timestamp. Implementation:
`forgelm/webhook.py`.

| Webhook `event` | Audit-log mirror | Trigger | Gated by | Required payload fields |
|---|---|---|---|---|
| `training.start` | `training.started` | `train()` entered, before model load. | `webhook.notify_on_start` | `run_name`, `status="started"` |
| `training.success` | `pipeline.completed` | All gates passed, no human-approval requirement. | `webhook.notify_on_success` | `run_name`, `status="succeeded"`, `metrics` |
| `training.failure` | `pipeline.failed` | Training itself raised (OOM, dataset error, unhandled exception). | `webhook.notify_on_failure` | `run_name`, `status="failed"`, `reason` (masked, ≤2048 chars) |
| `training.reverted` | `model.reverted` | A post-training gate (evaluation, safety, judge, benchmark) rejected the run and `_revert_model` deleted the adapters. | `webhook.notify_on_failure` | `run_name`, `status="reverted"`, `reason` (masked, ≤2048 chars) |
| `approval.required` | `human_approval.required` | Run succeeded, `evaluation.require_human_approval=true`, model staged for review (EU AI Act Art. 14). | `webhook.notify_on_success` | `run_name`, `status="awaiting_approval"`, `model_path` |

### Why two of these split lifecycle states

- **`training.failure` vs `training.reverted`** — dashboards need to tell
  "the trainer crashed" apart from "the trainer succeeded, but the
  quality / safety / judge gate said no". Both are operationally
  actionable, but they require different runbooks. Faz 8 introduced
  `notify_reverted` precisely so a Slack channel can colour-code the two
  cases differently (`#ff0000` vs `#ff9900`).
- **`approval.required`** — fires *after* the run succeeded but *before*
  the operator has approved deploy. It is not a failure; it is a pause.
  Receivers that auto-page on `training.failure` should **not** page on
  `approval.required`.

### Payload schema

Every webhook event ships the same envelope:

```json
{
  "event": "training.start | training.success | training.failure | training.reverted | approval.required",
  "run_name": "<string>",
  "status": "started | succeeded | failed | reverted | awaiting_approval",
  "metrics": {"<name>": <number>, ...},
  "reason": "<masked string or null>",
  "model_path": "<filesystem path or null>",
  "attachments": [{"title": "...", "text": "...", "color": "..."}]
}
```

`metrics`, `reason`, and `model_path` are always present in the schema;
only populated for the events that need them. `attachments` is the
Slack-compatible block — other receivers may ignore it.

### Security guarantees

1. **Reasons are masked.** Every `reason` field passes through
   `forgelm.data_audit.mask_secrets` before transport, so AWS / GitHub /
   Slack / OpenAI / Google / JWT / private-key blocks / Azure storage
   strings do not leave the process. If `data_audit` cannot be imported,
   the field is replaced with `"[REDACTED — secrets masker unavailable]"`
   rather than the raw string.
2. **Reasons are truncated to 2048 chars.** Stack traces longer than
   that are clipped with `"… (truncated)"`.
3. **No model weights.** `approval.required` carries the staging
   filesystem path only. Weights stay on disk; the operator already
   controls that directory.
4. **No webhook URL leakage.** URLs are masked in logs (`scheme://host/<first-segment>/...`)
   and the response body is suppressed on non-2xx.
5. **SSRF guard.** Private / loopback / link-local destinations are
   refused unless `webhook.allow_private_destinations=true`.

### Retention guidance

Webhook payloads are **transient**. They are not the audit record.
Receivers that need long-term history should snapshot the audit JSONL
(`<output_dir>/audit_log.jsonl`) rather than archiving webhook
traffic, because the audit log is the append-only hash-chained record
and the webhook stream is best-effort.
