# Logging & Observability Standard

> **Scope:** Anything that writes to stderr/stdout, appends to a file, or sends over the network.
> **Enforced by:** Code review + tests under `tests/test_compliance.py`, `tests/test_webhook.py`.

## Logger setup

From [`forgelm/cli/_logging.py`](../../forgelm/cli/_logging.py):

```python
def _setup_logging(log_level: str, json_format: bool = False) -> None:
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)
    if json_format:
        numeric_level = logging.WARNING
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
```

**Rules:**

1. **One logger per module.** At the top:

   ```python
   import logging
   logger = logging.getLogger("forgelm.compliance")  # match module path
   ```

2. **Never use `print()` in library code.** Only modules under [`forgelm/cli/`](../../forgelm/cli/) (JSON envelopes + human-facing CLI text on stdout), [`forgelm/chat.py`](../../forgelm/chat.py) (interactive REPL output, including token-by-token streaming), and [`forgelm/wizard/`](../../forgelm/wizard/) (the interactive config wizard, whose `_io._print` indirection routes operator-facing prompts to stdout so they are `capsys`-capturable) may use raw `print()`. Every other library module must use the project logger so callers can configure verbosity. The `chat.py` and `wizard/` carve-outs exist because an interactive UI's output **is** the user interface — routing it through `logger` would force users to reconfigure log handlers to see prompts and replies.

3. **`--output-format json` downgrades to WARNING.** When a pipeline is reading JSON on stdout, human-friendly INFO spam on stderr drowns the signal. Keep stderr quiet in JSON mode.

4. **Log levels:**

   | Level | When |
   |---|---|
   | `DEBUG` | Step-level details a developer would want while investigating |
   | `INFO` | Normal progress: "Loading model X", "Epoch 3/10 complete" |
   | `WARNING` | Recoverable problems: webhook failed, optional feature skipped |
   | `ERROR` | User-visible failure before exiting: config error, training crash |
   | `CRITICAL` | Reserved. Don't use — if it's that bad, raise. |

## Logging + raising

When raising an exception that will cause a non-zero exit, log at `ERROR` right before. From `cli.py`:

```python
except ValidationError as e:
    logger.error("Configuration error:\n%s", e)
    sys.exit(EXIT_CONFIG_ERROR)
```

When raising inside a library module, **don't** log — let the caller decide. Double-logging is noise.

## Structured JSON output

Machine-readable output goes to stdout as a single JSON object (or one JSON object per line for streaming). The format:

```json
{
  "status": "success" | "error" | "reverted" | "awaiting_approval",
  "exit_code": 0,
  "run_id": "fg-abc123",
  "config_hash": "sha256:...",
  "metrics": {...},
  "resource_usage": {
    "gpu_hours": 2.4,
    "peak_vram_gb": 22.1,
    "training_duration_seconds": 8640,
    "gpu_model": "NVIDIA A100 80GB",
    "estimated_cost_usd": 7.20
  },
  "artifacts": {...}
}
```

**Rules:**

1. JSON goes to **stdout** only. Logs go to **stderr**. Never mix.
2. Every JSON run output includes `run_id` and `config_hash` (reproducibility).
3. If the run errored, include `error_type` and `message` — see [error-handling.md](error-handling.md).

## Audit log (EU AI Act Article 12)

From [`forgelm/compliance.py`](../../forgelm/compliance.py):

```python
class AuditLogger:
    """Append-only JSON Lines audit log for EU AI Act Art. 12 record-keeping."""
    def log_event(self, event: str, **details) -> None:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": self.run_id,
            "operator": self.operator,
            "event": event,
            "prev_hash": self._prev_hash,
        }
```

**Rules:**

1. **Append-only.** Never rewrite or delete entries. If something is wrong, write a compensating entry with `event: "correction"`.
2. **Hash chain.** Every entry includes `prev_hash`, forming a tamper-evident chain. Don't break it.
3. **UTC timestamps in ISO 8601.** Wall-clock time with timezone is audit-garbage.
4. **Events are verbs in past tense:** `training.started`, `evaluation.safety.failed`, `model.reverted`. Not `StartTraining`, not `Training Started`.
5. **Operator is required.** Either from env (`FORGELM_OPERATOR`), config, or fall back to the OS username. Never "unknown".

**Audit events MUST be emitted for:**

- Training start, success, failure, auto-revert
- Config validation pass/fail
- Safety/benchmark/judge gate decisions (pass or fail, with scores)
- Human approval gate triggered (exit code 4)
- Compliance export invoked

## Webhook notifications

From [`forgelm/webhook.py`](../../forgelm/webhook.py):

```python
class WebhookNotifier:
    def _send(self, *, event: str, run_name: str, status: str, ...):
        payload = {
            "event": event,
            "run_name": run_name,
            "status": status,
            "metrics": safe_metrics,
        }
```

**Rules:**

1. **Webhooks never abort training.** A 500 from Slack is a warning, not a failure. Wrap the POST in `try/except requests.RequestException` and log at `WARNING`.
2. **Timeout every request.** `webhook.timeout` defaults to 10s and is clamped up to a 1s floor by the notifier. There is no upper clamp — the schema's `ge=1` is the only bound.
3. **Sanitize payload.** Never send API keys, full config contents, or sensitive data paths. Fields reaching `payload` are allowlisted: the base envelope is fixed, and `_send(**extra)` screens every extra key against `_ALLOWED_EXTRA_PAYLOAD_KEYS`, dropping and logging anything unregistered.
4. **Lifecycle events:** `training.start`, `training.success`, `training.failure`, `training.reverted`, `approval.required` — see the table below for the canonical list. Each webhook event mirrors a distinct audit-log event (e.g., webhook `training.start` ↔ audit `training.started`); the two vocabularies are paired but not identical because past-tense audit identifiers describe a finalized record while webhooks announce a state transition.
5. **No retry.** A failed delivery is logged at `WARNING` and abandoned; the notifier makes exactly one POST attempt per event and never re-sends. This rule previously promised "up to 3 times with exponential backoff", which the shipped notifier has never done — receivers must be idempotent and must not treat a missing event as proof the transition did not occur. Adding retry is a deliberate design change, not a bug fix: it needs a bounded-backoff budget that cannot delay a training run's exit.

### Webhook event vocabulary

The eight events below are the **only** events that webhook receivers
should expect: five single-stage lifecycle events (`training.*` +
`approval.required`) plus the three-event `pipeline.*` family that the
multi-stage orchestrator emits *alongside* (not replacing) the per-stage
lifecycle events. Adding new ones requires a docs update here, a `Notifier`
method, a paired audit event, and tests. Do not invent ad-hoc event strings.

Adding a new event **appends** to this vocabulary; renaming a released
event name is a breaking change for every enum-validating receiver and
has never been done. The receiver-facing contract — payload shapes,
types, stability guarantees, the extra-field allowlist, and the redaction
rules — is published as
[`docs/reference/webhook_schema.md`](../reference/webhook_schema.md);
keep it in step with any change made here.

| Event | Emitted when | Required fields | Notes |
|---|---|---|---|
| `training.start` | `train()` is entered, before model load. | `run_name`, `status="started"` | Mirrors audit event `training.started`. Gated by `webhook.notify_on_start`. |
| `training.success` | The run completed and was not reverted or held for approval. With `evaluation.auto_revert: true` this implies all gates passed; with the shipped default `auto_revert: false` it also fires when a gate failed but was only *recorded* (the failure rides in `metrics`, the model is still promoted). | `run_name`, `status="succeeded"`, `metrics` | Mirrors audit event `pipeline.completed`. Gated by `webhook.notify_on_success`. |
| `training.failure` | Training itself crashed (OOM, dataset error, exception in pipeline). | `run_name`, `status="failed"`, `reason` (masked, ≤2048 chars) | Mirrors audit event `pipeline.failed`. Gated by `webhook.notify_on_failure`. |
| `training.reverted` | A post-training gate (eval / safety / judge / benchmark) rejected the run and `_revert_model` deleted the adapters. | `run_name`, `status="reverted"`, `reason` (masked, ≤2048 chars) | Distinct from `training.failure` so dashboards can separate "training crashed" from "training succeeded but quality regressed". Mirrors audit event `model.reverted`. Gated by `webhook.notify_on_failure`. |
| `approval.required` | Run succeeded, `evaluation.require_human_approval=true`, model staged for review. | `run_name`, `status="awaiting_approval"`, `model_path` (filesystem path) | Mirrors audit event `human_approval.required`. Operator gets a real-time ping instead of having to poll the audit JSONL. Model weights themselves are **never** in the payload — only the staging path. Gated by `webhook.notify_on_success`. |
| `pipeline.started` | A multi-stage pipeline run begins, before any stage executes. | `run_name` (the pipeline run id), `status="started"`, `stage_count` | Multi-stage only. Mirrors audit event `pipeline.started`. Gated by `webhook.notify_on_start`. |
| `pipeline.completed` | A multi-stage pipeline run reaches its terminal state (success, failure, or stopped-at-stage). | `run_name`, `status` (the `final_status`), `final_status`, `stopped_at` | Multi-stage only. Shares the identifier of the audit event `pipeline.completed` (a known wire/audit name collision; correlate on the payload field-set, not the name alone). Gated by `webhook.notify_on_success` (clean finish) or `webhook.notify_on_failure` (early stop). |
| `pipeline.stage_reverted` | A pipeline stage auto-reverts, before downstream stages are marked skipped. | `run_name`, `status="reverted"`, `stage_name`, `reason` (masked, ≤2048 chars) | Multi-stage only. Mirrors audit event `pipeline.stage_reverted`. Gated by `webhook.notify_on_failure`. |

**Payload schema (every event):**

```json
{
  "event": "<one of the eight above>",
  "run_name": "<string>",
  "status": "<started|succeeded|failed|reverted|awaiting_approval|completed|stopped_at_stage>",
  "metrics": {"<name>": <number>, ...},
  "reason": "<masked string or null>",
  "model_path": "<staging path or null>",
  "attachments": [{"title": "...", "text": "...", "color": "..."}]
}
```

`metrics`, `reason`, and `model_path` are always present in the schema; only
populated for the events that need them. `attachments` is Slack-compatible
formatting that other receivers may ignore.

**Retention guidance:** Webhook payloads are transient — they are *not* the
audit record. Receivers that need long-term history should snapshot the
audit JSONL (`<output_dir>/compliance/audit_log.jsonl`) rather than archiving
webhook traffic, because the audit log is the append-only hash-chained
record and the webhook stream is best-effort.

## Third-party tracking (W&B / MLflow / TensorBoard)

Routed through `trainer_args.report_to`. Rules:

1. Optional — must degrade gracefully if the tracker isn't installed or configured.
2. Opt-in via config (`tracking.wandb.enabled: true`), never by default.
3. Credentials from environment variables only, never from YAML.
4. Same metric names across trackers. Don't emit `eval/loss` to W&B and `eval_loss` to TensorBoard.

## Secrets

**Never log secrets.** Specifically:

- `HF_TOKEN`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, any `*_TOKEN` or `*_KEY` env var
- Webhook URLs (they're bearer tokens)
- Connection strings in config
- User prompts or generated content in production mode (PII risk)

Safe pattern: log the *presence* of a secret, not its value.

```python
if os.environ.get("HF_TOKEN"):
    logger.info("HF_TOKEN detected, authenticated mode enabled")
else:
    logger.info("No HF_TOKEN set, using public models only")
```

## Performance logging

Resource metrics go in the JSON output and the model card, not scattered through INFO logs:

- `gpu_hours`, `peak_vram_gb`, `training_duration_seconds`
- `gpu_model`, `estimated_cost_usd`
- `tokens_per_second` (if measurable)

These come from `forgelm/utils.py` helpers + `torch.cuda.max_memory_allocated()` + time sampling. Don't reinvent the collection path.

## Quick checklist

Before your PR:

- [ ] Every module has a logger named `forgelm.<module>`.
- [ ] No `print()` outside `forgelm/cli/` (JSON + CLI text), `forgelm/chat.py` (REPL output), and `forgelm/wizard/` (interactive wizard prompts).
- [ ] Every `sys.exit(!=0)` is preceded by `logger.error(...)`.
- [ ] JSON output fields match the schema above, validated by a test.
- [ ] Audit events fire for every decision gate you added.
- [ ] Webhook failures are wrapped in try/except and logged at WARNING.
- [ ] No secrets printed or logged.
