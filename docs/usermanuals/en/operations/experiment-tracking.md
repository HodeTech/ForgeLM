---
title: Experiment Tracking
description: W&B, MLflow, and TensorBoard integration via the report_to setting.
---

# Experiment Tracking

ForgeLM doesn't reinvent experiment tracking — it integrates with whatever your team already uses via the `training.report_to` field. The supported backends are `tensorboard`, `wandb`, `mlflow`, and `none` (the four `Literal` values on `TrainingConfig.report_to` in `forgelm/config.py`).

## Quick example

```yaml
training:
  trainer_type: "sft"
  report_to: "wandb"                     # single value: tensorboard | wandb | mlflow | none
  run_name: "customer-support-v1.2.0"    # optional; auto-generated when None
```

ForgeLM streams loss, learning rate, evaluation metrics, and benchmark scores to the configured backend. **Per-backend nested config blocks (e.g. `training.wandb: { project: ... }`) are not part of the schema** — each backend's connection / authentication / project / artifact behaviour is configured via its own well-known environment variables (the framework convention HF Transformers' `Trainer` follows). The only ForgeLM-side knobs are `training.report_to` (which backend to use) and `training.run_name`.

## Supported backends

### Weights & Biases (W&B)

```yaml
training:
  report_to: "wandb"
```

Configuration via environment variables (no nested YAML block):

- `WANDB_API_KEY` — auth token (or run `wandb login` once on the training host).
- `WANDB_PROJECT` — project name.
- `WANDB_ENTITY` — team / org slug.
- `WANDB_LOG_MODEL` — set to `true` to upload checkpoints as W&B artefacts.

W&B requires the `[tracking]` extra: `pip install 'forgelm[tracking]'`.

### MLflow

```yaml
training:
  report_to: "mlflow"
```

Configuration via environment variables:

- `MLFLOW_TRACKING_URI` — server URL (e.g. `http://mlflow.internal:5000`).
- `MLFLOW_EXPERIMENT_NAME` — experiment name.
- `MLFLOW_TRACKING_USERNAME` / `MLFLOW_TRACKING_PASSWORD` (or `MLFLOW_TRACKING_TOKEN`).

MLflow requires the `[tracking]` extra.

### TensorBoard

```yaml
training:
  report_to: "tensorboard"
```

The default. Log files land at `<training.output_dir>/runs/`. No external service is needed, but TensorBoard integration requires installing either `tensorboard` (bundled with PyTorch ≥ 1.4) or `tensorboardX` separately — HF Transformers probes for either at import time (`transformers.integrations.is_tensorboard_available`) and `TensorBoardCallback` raises loudly if neither is present.

### Streaming to multiple backends

`training.report_to` is a single Literal value, not a list. To stream to multiple backends in the same run, use ForgeLM's `--report-to` CLI override; it maps to the list-accepting constructor argument on `transformers.TrainingArguments.report_to`. (`TRAINER_REPORT_TO` is **not** an HF Transformers convention and is not honoured by ForgeLM either — only the constructor / CLI-override paths are supported.) The single-Literal config field is the safe default that pins one canonical backend.

## What gets logged

| Metric | When |
|---|---|
| `train/loss` | Every step |
| `train/lr` | Every step |
| `train/grad_norm` | Every step (always logged by HF Trainer) |
| `eval/loss` | Every eval interval |
| `benchmark/<task>` | Once per run (after eval) |
| `safety/<category>/max` | Once per run (after safety eval) |
| `safety/<category>/mean` | Once per run |
| `system/gpu_utilization` | Sampled every 30s |
| `system/vram_used_gb` | Sampled every 30s |

## Run naming

```yaml
training:
  run_name: "customer-support-v1-2"     # plain string; null = auto-generated
```

`training.run_name` is the only ForgeLM-side run-naming knob. There is no `training.tags:` list and no `training.notes:` field — set tags / notes / artifact-upload / artifact-type via the **backend's own environment variables** before invoking the trainer:

```bash
# W&B
export WANDB_TAGS="dpo,qlora,tr,v1.2"
export WANDB_NOTES="Increased dpo_beta from 0.1 to 0.15"
export WANDB_LOG_MODEL="checkpoint"   # or "end" — controls artifact upload

# MLflow
export MLFLOW_TAGS='{"trainer":"dpo","quantization":"qlora"}'
```

## Artifact management

ForgeLM does **not** expose a `training.wandb:` or `training.mlflow:` sub-block. Artifact upload is configured via backend environment variables (`WANDB_LOG_MODEL`, `MLFLOW_TRACKING_URI` + per-run logging APIs in your launch wrapper) — the same convention HF Transformers' `Trainer` follows.

For very large checkpoints, prefer model registries (HuggingFace Hub) over W&B/MLflow artifact stores. Their free tiers cap at smallish sizes.

## Comparing runs

Each backend's UI handles comparison naturally — comparable runs share a `run_name` prefix, tags, and config hash. A built-in CLI summary is on the way:

> Note: The `forgelm compare-runs` subcommand is planned for a future Pro CLI tier release (see the [Phase 13 roadmap on GitHub](https://github.com/HodeTech/ForgeLM/blob/main/docs/roadmap.md)). Today the same comparison runs through your tracking backend's UI (W&B / MLflow / Comet) or a small `jq` over each run's JSON envelope.

Today's working flow (W&B / MLflow / Comet UI is the canonical surface; below is the `jq` shortcut for ad-hoc CLI comparison):

```shell
$ for v in v1.0 v1.1 v1.2; do
    jq --arg v "$v" '{run: $v, hellaswag: .benchmark.hellaswag, truthfulqa: .benchmark.truthfulqa, S5_max: .safety.S5}' \
       runs/$v/eval.json
  done
```

The dedicated `forgelm compare-runs` UX (planned, NOT runnable today):

```text
# preview (planned Pro CLI — NOT runnable today)
forgelm compare-runs runs/v1.0 runs/v1.1 runs/v1.2
                  v1.0    v1.1    v1.2
hellaswag        0.612   0.617   0.621
truthfulqa       0.480   0.482   0.475   ↓
S5_max           0.041   0.038   0.082   ↑↑
loss             1.43    1.39    1.35
```

The arrows are auto-derived: green for improvement, red for regression of significance.

## Common pitfalls

:::warn
**Hardcoding API keys.** Never put W&B / MLflow / Comet keys directly in YAML. Always use `${ENV_VAR}` interpolation. Check `audit_log.jsonl` to confirm secrets weren't included in the dumped config.
:::

:::warn
**Reporting to unreachable backends.** If `wandb.ai` is down or your firewall blocks it, ForgeLM logs a warning but doesn't fail training. Watch the warnings; otherwise you might miss that nothing's being logged.
:::

:::tip
**Use multiple backends in parallel.** TensorBoard for local debugging during a run, W&B for cross-team collaboration after. Configure both — there's no extra cost.
:::

## See also

- [Configuration Reference](#/reference/configuration) — full `report_to` and per-backend settings.
- [JSON Output Mode](#/operations/cicd) — for piping logs into your own tracking.
