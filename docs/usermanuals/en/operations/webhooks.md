---
title: Webhooks
description: Slack and Teams notifications on training events — start, success, failure, auto-revert.
---

# Webhooks

ForgeLM fires structured webhooks on training milestones. Wire them into Slack, Teams, or any incident tool that accepts JSON payloads — get the right context to the right humans without anyone watching a log.

## Quick example

```yaml
webhook:
  url_env: "SLACK_WEBHOOK"           # reads URL from $SLACK_WEBHOOK at runtime
  notify_on_start: true              # default true
  notify_on_success: true            # default true
  notify_on_failure: true            # default true (covers training.failure + training.reverted)
```

The notifier emits a generic JSON payload — Slack and Teams ingest it directly via
their incoming-webhook endpoints. Per-event subscription is not currently
configurable; toggle the three `notify_on_*` flags to coarse-control which
lifecycle events fire.

ForgeLM picks up `${SLACK_WEBHOOK}` from environment variables. Common pattern:

```shell
$ export SLACK_WEBHOOK="https://hooks.slack.com/services/T.../B.../..."
$ forgelm --config configs/run.yaml
```

## Wire-format events

ForgeLM emits exactly **eight** webhook events: five single-stage
lifecycle events plus the three `pipeline.*` events the multi-stage
orchestrator emits alongside them. The table below is the canonical
surface mirrored in the
[Audit Event Catalog on GitHub](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/audit_event_catalog.md):

| Event | When fired | Gated by |
|---|---|---|
| `training.start` | `train()` enters, before model load. | `webhook.notify_on_start` |
| `training.success` | All gates pass; no human-approval requirement. | `webhook.notify_on_success` |
| `training.failure` | Training raised (OOM, dataset error, unhandled exception). | `webhook.notify_on_failure` |
| `training.reverted` | A post-training gate (eval / safety / judge / benchmark) rejected the run and `_revert_model` rolled adapters back. | `webhook.notify_on_failure` |
| `approval.required` | Run succeeded, `evaluation.require_human_approval=true` is set, model staged for review (EU AI Act Article 14). | `webhook.notify_on_success` |
| `pipeline.started` | A multi-stage pipeline run begins, before any stage executes. | `webhook.notify_on_start` |
| `pipeline.completed` | A multi-stage pipeline run reaches its terminal state. | `webhook.notify_on_success` / `webhook.notify_on_failure` |
| `pipeline.stage_reverted` | A pipeline stage auto-reverts, before downstream stages are skipped. | `webhook.notify_on_failure` |

## Payload shape

Single generic JSON shape — Slack / Teams / Discord all accept it
directly via incoming-webhook endpoints; ForgeLM does **not** wrap it in
provider-specific templates:

```json
{
  "event": "training.reverted",
  "run_name": "customer-support-v1.2.0",
  "status": "reverted",
  "reason": "safety regression: S5 hate-speech +0.18 over baseline"
}
```

Payload keys vary by event; the full per-event field list is in the
[Audit Event Catalog on GitHub](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/audit_event_catalog.md)
under the *Webhook lifecycle events* table.

## Slack / Teams / Discord ingestion

The single generic JSON payload shown above is what Slack, Teams, and Discord
all expect on their incoming-webhook endpoints. There are **no per-provider
templates** in `WebhookConfig` (no `template:`, no `events:` allow-list, no
`channel:` / `mention_on_failure:` formatting knobs, no per-destination
fan-out array). Routing and formatting happen on the receiving side:

- **Slack** — paste the payload into a Slack workflow or incoming-webhook
  app; Slack renders the JSON's top-level fields. To get a richer
  formatted card, point the webhook at a relay (Slack workflow / AWS
  Lambda / your own gateway) that translates the ForgeLM payload into
  Slack Block Kit.
- **Microsoft Teams** — similar pattern. Teams renders incoming JSON
  natively but the visual is plain; for MessageCard / Adaptive Card
  formatting, run a relay.
- **Discord** — accepts the JSON directly via the bot/webhook URL.

For multiple destinations, run multiple separate ForgeLM training
configs (each pinned to its own `webhook.url_env`) or fan out from a
single ForgeLM webhook to multiple downstream tools at the relay
layer. ForgeLM does not natively support a `webhooks: [...]` array.

## Cross-cutting webhook fields

Real `WebhookConfig` (see `forgelm/config.py::WebhookConfig`):

| Field | Default | Notes |
|---|---|---|
| `url` | `null` | Inline URL — prefer `url_env` for secret hygiene. |
| `url_env` | `null` | Env-var name carrying the URL. Overrides `url` when set. |
| `notify_on_start` | `true` | Gates `training.start` AND `pipeline.started`. |
| `notify_on_success` | `true` | Gates `training.success`, `approval.required`, AND a successful `pipeline.completed`. |
| `notify_on_failure` | `true` | Gates `training.failure`, `training.reverted`, `pipeline.stage_reverted`, AND a failing `pipeline.completed`. |
| `timeout` | `10` | HTTP timeout in seconds; clamped to ≥ 1s. |
| `allow_private_destinations` | `false` | Opt-in for RFC 1918 / loopback / link-local / RFC 6598 CGNAT (`100.64.0.0/10`) destinations (in-cluster Slack proxy, on-prem Teams gateway). Defaults reject — SSRF guard. |
| `require_https` | `false` | TLS-only enforcement. `true` refuses a plaintext `http://` URL (the SSRF guard raises; the POST is skipped) instead of warning-and-sending. Default `false` preserves warn-then-send. |
| `tls_ca_bundle` | `null` | Path to a custom CA bundle (corporate MITM CA). When unset, `certifi`'s bundled store is used. |

There is no `template:`, `events: [...]`, `headers: {...}`,
`retries:`, `redact:`, `allow_private:`, `channel:`, or
`mention_on_failure:` field. Header injection, retry strategy,
redaction (the curated payload is already curated), and routing all
live outside ForgeLM.

## Security considerations

- **TLS strongly recommended.** ForgeLM permits both HTTPS and HTTP webhook URLs — HTTP destinations log a `Webhook URL uses HTTP (not HTTPS). Data will be sent unencrypted.` warning but are not rejected by default (see `forgelm/webhook.py` `_send`). On a regulated estate set `webhook.require_https: true` to make a plaintext `http://` URL a hard failure (the delivery is refused, not sent). Pin `https://` URLs in production.
- **Curated payload.** ForgeLM never includes raw training data, full configs, or unredacted PII in webhook payloads. The notifier wraps a fixed-shape JSON; there is no `webhook.redact` toggle because there's nothing user-controllable to redact.
- **Server-Side Request Forgery (SSRF) guard.** ForgeLM blocks webhook URLs pointing at internal IPs — RFC 1918, loopback, link-local (incl. cloud IMDS at `169.254.169.254`), RFC 6598 Shared Address Space / Carrier-Grade-NAT (`100.64.0.0/10`, which also covers Alibaba Cloud ECS's IMDS at `100.100.100.200` — the stdlib `ipaddress` predicates don't flag that block as private or reserved on their own), plus reserved and multicast destinations — unless you explicitly opt-in with `webhook.allow_private_destinations: true`. This prevents misconfigured runs from probing your internal network or leaking the payload to a cloud metadata service.
- **No HMAC body signing.** ForgeLM does not sign webhook bodies — destination-side authenticity falls to TLS + URL secrecy via `url_env` plus the receiving system's bearer-token / signed-request controls (Slack signing secret, Teams connector token).

## Common pitfalls

:::warn
**Webhook silently failing.** A 4xx response from the webhook endpoint shouldn't fail the training run, but ForgeLM also shouldn't silently swallow the error. Check `audit_log.jsonl` for `webhook_failed` events; investigate why your endpoint rejected.
:::

:::warn
**Expecting per-epoch webhooks.** ForgeLM does not emit a per-epoch event — only the eight events listed above. If you need per-epoch progress, scrape it from the trainer's stdout / `audit_log.jsonl` rather than expecting a webhook fan-out.
:::

:::tip
**Smoke-test webhooks before going live.** ForgeLM does not ship a `--webhook-test` flag. `--dry-run` is config-validation only — it does **not** run the trainer lifecycle, so webhooks aren't exercised. The right options are (a) run a **real tiny training run** (small dataset + low `num_train_epochs`) so the lifecycle fires end-to-end against a staging webhook URL; or (b) POST a curated synthetic payload via `curl` to confirm the destination renders it correctly.
:::

## See also

- [CI/CD Pipelines](#/operations/cicd) — the natural home of webhooks.
- [Auto-Revert](#/evaluation/auto-revert) — produces the most actionable webhook.
- [Human Oversight](#/compliance/human-oversight) — webhook-driven approval flow.
