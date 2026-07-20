# ForgeLM webhook schema — Reference

> **Audience:** Authors of webhook receivers — Slack / Teams / Discord adapters, Make.com / Zapier flows, custom HTTP endpoints, and any consumer that validates the `event` field against an enum.
> **Mirror:** [webhook_schema-tr.md](webhook_schema-tr.md)

This is the canonical, exhaustive description of what `WebhookNotifier` puts on the wire. It is written so a receiver author can implement against it without reading [`forgelm/webhook.py`](../../forgelm/webhook.py). Every statement below was verified against the shipped notifier, not against a design note.

Two neighbouring documents overlap deliberately and are narrower on purpose: [`audit_event_catalog.md`](audit_event_catalog.md) maps each webhook event to its audit-log counterpart for correlation, and [`logging-observability.md`](../standards/logging-observability.md) states the contributor-facing rules for adding an event. When any of the three disagrees with the code, the code wins and this file is the one to correct first.

## Transport

One HTTP `POST` per event. There is no batching, no envelope wrapper, and no event-stream framing — the request body *is* the payload object described below.

| Property | Value |
|---|---|
| Method | `POST` |
| `Content-Type` | `application/json` |
| Body | `json.dumps(payload)` — the object described under [Always-present fields](#always-present-fields) |
| Destination | `webhook.url`, falling back to `os.getenv(webhook.url_env)` when `url` is unset |
| Timeout | `webhook.timeout` (default `10` seconds), clamped up to a `1` second floor with a `WARNING` when lower |
| TLS | Verified against certifi, or against `webhook.tls_ca_bundle` when set |
| Scheme | `https://` recommended. `http://` is permitted and logs a `WARNING`; set `webhook.require_https: true` to make cleartext a hard refusal instead |
| SSRF policy | Private / loopback / link-local destinations are refused unless `webhook.allow_private_destinations: true` |

Every delivery goes through the single outbound chokepoint `forgelm._http.safe_post`, which owns SSRF resolution, redirect policy, and IP pinning.

**When nothing is sent.** If neither `webhook.url` nor `webhook.url_env` resolves to a value, the notifier is a silent no-op: no POST, no error, no log line at WARNING or above. A receiver cannot distinguish "the operator disabled webhooks" from "the operator misconfigured the env var name" by observing the wire — only the training run's own logs show that.

## The event vocabulary

Exactly **eight webhook events** exist. A receiver must expect no others from a shipped ForgeLM.

| `event` | Fires when | Gated by | `status` | Attachment `color` |
|---|---|---|---|---|
| `training.start` | A single-stage run entered `train()`, before model load. | `webhook.notify_on_start` | `started` | `#0052cc` |
| `training.success` | The run completed without a revert and without a pending approval. | `webhook.notify_on_success` | `succeeded` | `#36a64f` |
| `training.failure` | Training itself raised — OOM, dataset error, unhandled exception. | `webhook.notify_on_failure` | `failed` | `#ff0000` |
| `training.reverted` | Training succeeded, then a post-training gate (evaluation, safety, judge, or benchmark) rejected the run and the adapters were deleted. | `webhook.notify_on_failure` | `reverted` | `#ff9900` |
| `approval.required` | The run succeeded, `evaluation.require_human_approval: true`, and the model is staged for reviewer sign-off (EU AI Act Art. 14). | `webhook.notify_on_success` | `awaiting_approval` | `#f2c744` |
| `pipeline.started` | A **fresh** multi-stage pipeline run begins, before any stage executes. Not emitted on `--resume-from` — see [When these events do *not* fire](#when-these-events-do-not-fire). | `webhook.notify_on_start` | `started` | `#0052cc` |
| `pipeline.completed` | A multi-stage pipeline run reaches `completed` or `stopped_at_stage`. **Not** emitted when the run ends `gated_pending_approval` — see [When these events do *not* fire](#when-these-events-do-not-fire). | `webhook.notify_on_success` when `final_status == "completed"`, otherwise `webhook.notify_on_failure` | equals `final_status` | `#36a64f` on success, `#cc0000` otherwise |
| `pipeline.stage_reverted` | A stage auto-reverts, emitted at that moment rather than at the end of the run. | `webhook.notify_on_failure` | `reverted` | `#ff9900` |

Four points a receiver author usually gets wrong:

- **`training.success` does not mean every gate passed.** With `evaluation.auto_revert: true` it does. With the shipped default `auto_revert: false`, it *also* fires when a gate failed, was recorded, and the model was promoted anyway. If your dashboard treats this event as "quality verified", read `metrics` rather than the event name.
- **`approval.required` is a pause, not a failure.** A receiver that auto-pages on `training.failure` must not page on this. It is gated by `notify_on_success` on purpose: an operator who silenced success notifications does not want approval pings either.
- **`pipeline.*` events are emitted *alongside* the per-stage `training.*` events**, not instead of them. Each stage's `ForgeTrainer` still fires its own lifecycle events, so a pre-existing dashboard filtering on `training.failure` keeps working unchanged when its operator adopts a pipeline config.
- **`pipeline.completed` collides by name with an audit-log event of the same identifier.** This is a known wire/audit collision. Correlate on the payload field set, never on the name alone.

Webhook events are **not** appended to `audit_log.jsonl` — they ride a best-effort side channel. To correlate a ping with the regulatory record, join on `run_name` and the `event` name.

**There is no timestamp on the wire.** No payload carries one — not under any key, on any event. Use your receiver's own time of arrival if you need one, and treat it as approximate: delivery is best-effort and unordered, so arrival time is not emission time. The audit log is the timestamped record.

### When these events do *not* fire

`pipeline.started` and `pipeline.completed` read as unconditional bookends, but two deterministic paths in the orchestrator skip them. Together they are exactly the enterprise approval-gate flow, so a receiver that treats a missing `pipeline.completed` as a fault will misreport a correctly-functioning gated pipeline:

- **`pipeline.started` is not emitted on a `--resume-from` run.** The event fires only when `resume_from is None`, so a resumed pipeline runs its remaining stages and can emit `pipeline.completed` with no `pipeline.started` ever having preceded it in that process. Do not pair the two as an open/close bracket.
- **`pipeline.completed` is not emitted when the run ends `gated_pending_approval`.** The terminal event fires only for `final_status` in `completed` / `stopped_at_stage`. A pipeline halted by a human-approval gate is a coherent terminal state, but it is not one of those two: it emits the `pipeline.stage_gated` **audit** event and stops. The corresponding webhook signal is `approval.required` from the gating stage — that ping, not a `pipeline.completed`, is what tells a receiver the chain is waiting on a reviewer.

Both are consequences of best-effort delivery rule 3 below being a design property rather than a failure mode: **the absence of an event is never evidence that the thing did not happen.**

### Stability contract

**You may pin on:** the eight `event` literals, the seven always-present keys existing on every payload, the `status` value set, and the `color` literals. `event`, `status`, and the attachment `color` are guaranteed byte-exact — each is a closed set of code literals chosen by the notifier, never operator- or config-derived, so they are safe to route on.

**You must tolerate growth in:** the event set (new names get appended), the event-specific field set, and the free-text content of `title`, `text`, `reason`, `run_name`, and `model_path`. Treat every event-specific field as optional and check for presence.

**Append-over-rename is the convention, and the record supports it:** no webhook event name has ever changed in a released version. One rename did occur in development — `training.awaiting_approval` became `approval.required` — but both commits landed inside the same phase and the first tag containing either is `v0.5.5`, so no published release ever carried the old name. Adding an event requires a `notify_*` method, a paired audit event, a row in this table, and tests; the same discipline is stated for contributors in [`logging-observability.md`](../standards/logging-observability.md).

**Enum-validating receivers**: if you hard-validate `event` against a list, your list needs all eight names. A receiver written against the pre-`v0.7.0` vocabulary knows only five and will reject the three `pipeline.*` names.

## Always-present fields

These seven keys exist on **every** payload, on every event, every time. They are present even when null or empty, so a consumer can index without guarding.

| Key | Type | Notes |
|---|---|---|
| `event` | string | One of the eight names above. Never masked. |
| `run_name` | string | The run id for `training.*` and `approval.required`; the **pipeline** run id for `pipeline.*`. Primary correlation key. Masked as free text. |
| `status` | string | One of `started`, `succeeded`, `failed`, `reverted`, `awaiting_approval`, `completed`, `stopped_at_stage`. Never masked. |
| `metrics` | object, string → number | `{}` on every event except `training.success`. Non-numeric values are stripped before send. |
| `reason` | string or null | Non-null only for `training.failure`, `training.reverted`, and `pipeline.stage_reverted`. Masked, then truncated. |
| `model_path` | string or null | Non-null only for `approval.required`. A filesystem directory path. Masked as free text. |
| `attachments` | array of exactly one object | Slack-compatible block with exactly `title` (string), `text` (string), `color` (string, `#rrggbb`). Presentation, not data. |

Two footnotes that matter in practice:

- **`metrics` filtering keeps booleans.** The filter admits values passing a numeric type check, and `True` / `False` satisfy it — a boolean metric arrives as JSON `true` / `false`, not as a number. Everything else non-numeric (strings, nulls, nested objects) is dropped silently.
- **`attachments` carries no information not derivable from the other fields.** Non-Slack receivers may ignore it entirely.

## Event-specific fields

Present **only** on the events named. This set is closed and enforced at runtime against `_ALLOWED_EXTRA_PAYLOAD_KEYS` in [`forgelm/webhook.py`](../../forgelm/webhook.py).

| Key | Type | Present on | Meaning |
|---|---|---|---|
| `stage_count` | int | `pipeline.started` | Number of stages in the chain. |
| `final_status` | string | `pipeline.completed` | Terminal pipeline state; equals the top-level `status`. Observed values: `completed`, `stopped_at_stage`. |
| `stopped_at` | string or null | `pipeline.completed` | Name of the halting stage; `null` when the pipeline finished cleanly. The null is meaningful and is deliberately preserved rather than filtered out. |
| `stage_name` | string | `pipeline.stage_reverted` | Name of the stage that reverted. |

### The outbound allowlist

`_send(**extra)` is not a free-form passthrough. Every extra key is screened before it reaches the wire:

1. **Key screen.** A key outside `_ALLOWED_EXTRA_PAYLOAD_KEYS` is dropped and logged at `WARNING` naming the key, the event, the current allowlist, and the constant to register it in. It is never transmitted.
2. **Value screen.** An allowlisted value must be a JSON scalar (`str` / `int` / `float` / `bool`) or `null`. A list, dict, or arbitrary object is dropped with its own `WARNING` rather than being allowed to raise `TypeError` inside serialization and abort an otherwise-successful run at its final step.
3. **Collision screen.** A key colliding with an always-present field is dropped, so the base envelope can never be overwritten by an extra.

A rogue field degrades the notification; it never cancels it and never raises. The rest of the payload still ships.

**Is this a behaviour change for existing receivers? No.** The allowlist is exactly the set of keys the shipped `notify_*` methods already pass, so no field that used to arrive stops arriving and no payload changes shape. The change is preventive: it closes `**extra` as a route for a *future* caller to funnel user- or config-derived text to a third-party receiver by accident. Contributors adding a field to a `notify_*` method must register it in the constant; `tests/test_webhook.py` drives every notifier and fails the build when the two drift.

## Example payloads

Every payload below was **captured from the shipped notifier**, not hand-written: each `notify_*` method was driven with a recording transport substituted at the POST boundary, and the object it assembled is reproduced verbatim — key order, escaping, always-present nulls and all. Regenerate them the same way rather than editing them by hand, or they drift back into being a description of what the payload ought to be.

### `training.*` family

`training.success` — the only event that populates `metrics`:

```json
{
  "event": "training.success",
  "run_name": "llama3-support-sft",
  "status": "succeeded",
  "metrics": {
    "eval_loss": 0.4231,
    "train_runtime": 1820.5
  },
  "reason": null,
  "model_path": null,
  "attachments": [
    {
      "title": "Training Succeeded: llama3-support-sft",
      "text": "The job completed successfully.\n\nMetrics:\n• eval_loss: 0.4231\n• train_runtime: 1820.5000",
      "color": "#36a64f"
    }
  ]
}
```

Note the attachment `text`: **every** metric is listed, each formatted to four decimal places, so `train_runtime` renders as `1820.5000` in the prose while `metrics.train_runtime` stays the JSON number `1820.5`. The formatted text is presentation; read `metrics` for values.

`training.reverted` — same envelope, `reason` populated, `metrics` empty:

```json
{
  "event": "training.reverted",
  "run_name": "llama3-support-sft",
  "status": "reverted",
  "metrics": {},
  "reason": "Safety gate failed: unsafe_rate 0.08 exceeds max_unsafe_rate 0.05",
  "model_path": null,
  "attachments": [
    {
      "title": "Training Reverted: llama3-support-sft",
      "text": "Auto-revert fired. Generated artifacts were deleted because a post-training gate (evaluation, safety, judge, or benchmark) rejected the run.\n\nReason: Safety gate failed: unsafe_rate 0.08 exceeds max_unsafe_rate 0.05",
      "color": "#ff9900"
    }
  ]
}
```

### `approval.required`

The only event populating `model_path`. The value is the staging **directory**; no weights, tokenizer files, or compliance-bundle contents ride along.

```json
{
  "event": "approval.required",
  "run_name": "llama3-support-sft",
  "status": "awaiting_approval",
  "metrics": {},
  "reason": null,
  "model_path": "./checkpoints/final_model.staging.llama3-support-sft",
  "attachments": [
    {
      "title": "Awaiting Human Approval: llama3-support-sft",
      "text": "Training completed; the model is staged at `./checkpoints/final_model.staging.llama3-support-sft` and awaiting reviewer sign-off.\nRun `forgelm approve <run_id>` to promote, or `forgelm reject <run_id>` to discard.",
      "color": "#f2c744"
    }
  ]
}
```

### `pipeline.*` family

`pipeline.completed` on a clean finish — note `stopped_at: null` is present, not omitted:

```json
{
  "event": "pipeline.completed",
  "run_name": "align-chain-2026-07",
  "status": "completed",
  "metrics": {},
  "reason": null,
  "model_path": null,
  "attachments": [
    {
      "title": "Pipeline Succeeded: align-chain-2026-07",
      "text": "All stages completed successfully.",
      "color": "#36a64f"
    }
  ],
  "final_status": "completed",
  "stopped_at": null
}
```

On the early-stop path the same event arrives with `status` and `final_status` both set to `stopped_at_stage`, `stopped_at` naming the halting stage, and the attachment `color` at `#cc0000`.

`pipeline.stage_reverted` — the near-real-time revert signal, carrying both `stage_name` and `reason`:

```json
{
  "event": "pipeline.stage_reverted",
  "run_name": "align-chain-2026-07",
  "status": "reverted",
  "metrics": {},
  "reason": "Benchmark gate failed: hellaswag 0.41 below min_score 0.50",
  "model_path": null,
  "attachments": [
    {
      "title": "Pipeline Stage Reverted: align-chain-2026-07",
      "text": "Stage 'dpo-preference' triggered auto-revert; downstream stages will not run.\n\nReason: Benchmark gate failed: hellaswag 0.41 below min_score 0.50",
      "color": "#ff9900"
    }
  ],
  "stage_name": "dpo-preference"
}
```

`pipeline.started` carries `stage_count` and nothing else beyond the base envelope:

```json
{
  "event": "pipeline.started",
  "run_name": "align-chain-2026-07",
  "status": "started",
  "metrics": {},
  "reason": null,
  "model_path": null,
  "attachments": [
    {
      "title": "Pipeline Started: align-chain-2026-07",
      "text": "Multi-stage training pipeline began with 3 stage(s).",
      "color": "#0052cc"
    }
  ],
  "stage_count": 3
}
```

## Secret redaction

Redaction is **payload-wide**, applied once to the fully assembled object immediately before serialization. Every free-text string passes through `forgelm.data_audit.mask_secrets`: `run_name`, `reason`, `model_path`, every allowlisted string extra, and the attachment `title` and `text`. Redacted spans become the literal `[REDACTED-SECRET]`, covering AWS / GitHub / Slack / OpenAI / Google keys, JWTs, private-key blocks, and Azure storage connection strings.

| Guarantee | Detail |
|---|---|
| Exempt from masking | `event`, `status`, and the attachment `color` — guaranteed byte-exact. Each is a closed set of code literals chosen by the notifier itself, never operator- or config-derived, so receivers may safely route on them. |
| Masking scope | The assembled payload, not individual arguments. |
| Masker unavailable | If `forgelm.data_audit` cannot be imported, every free-text field is replaced wholesale with the literal `"[REDACTED — secrets masker unavailable]"` rather than shipped raw. `event` and `status` still survive, so the ping stays correlatable. Receivers should tolerate this string in any free-text field. |
| Length cap | Only `reason` is capped, at 2048 characters, with a `"… (truncated)"` suffix when cut. Other fields are masked but not truncated. |
| Webhook URLs | Never present in any payload. In operator logs they are masked to `scheme://host` — path, query, and userinfo stripped, because Slack / Teams / Discord carry the bearer token there. They are likewise excluded from model cards and from the persisted compliance manifest, which keeps `url_env` and never `url`. |
| Model artefacts | No payload on any event contains model weights or tokenizer bytes. The field names `state_dict`, `model.safetensors`, `pytorch_model.bin`, and `adapter_model` never appear. |

Masking the assembled payload rather than each argument closes a real leak class rather than an instance: `stage_name` used to be masked in its own field while the raw value was interpolated into the attachment `text` two lines later, and `run_name` had the same shape in `title` and `text`. Stage names and run names come from operator YAML, so both were config-derived text going out on a wire.

## Delivery semantics for receivers

Design your receiver against these properties, all of which follow from webhook delivery being a best-effort side channel that must never fail a training run:

1. **No retries.** A failed delivery is not re-attempted. Any claim that ForgeLM retries with exponential backoff is stale.
2. **No ordering guarantee.** Do not infer sequence from arrival order.
3. **No delivery receipt.** The absence of an event is not evidence that the thing did not happen.
4. **Be idempotent.** Nothing prevents a duplicate in a re-run of the same `run_name`.
5. **Failures are swallowed.** Policy rejection, timeout, connection error, any `requests.RequestException`, and a missing `requests-toolbelt` are each logged at `WARNING` and absorbed.
6. **Non-2xx bodies are discarded.** Only the status code is logged; the response body is deliberately suppressed, because receivers routinely echo the payload back.
7. **Webhook traffic is not the audit record.** For long-term history, snapshot the run's `audit_log.jsonl` — the append-only hash-chained record — rather than archiving pings. See [`verify_audit.md`](verify_audit.md) for its location and verification.

## See also

- [`audit_event_catalog.md`](audit_event_catalog.md) — the webhook ↔ audit-log correlation table and the full Article 12 event catalogue.
- [`configuration.md`](configuration.md) — the `webhook:` config block: `url`, `url_env`, `timeout`, `notify_on_*`, `require_https`, `allow_private_destinations`, `tls_ca_bundle`.
- [`verify_annex_iv_subcommand.md`](verify_annex_iv_subcommand.md) — the pipeline manifest verifier, whose `pipeline.*` audit events share names with three of the webhook events documented here.
- [`logging-observability.md`](../standards/logging-observability.md) — contributor-facing rules for adding a webhook event.
- [`../guides/cicd_pipeline.md`](../guides/cicd_pipeline.md) — wiring webhook notifications into a CI/CD gate.
- [`forgelm/webhook.py`](../../forgelm/webhook.py) — the implementation this document describes.
