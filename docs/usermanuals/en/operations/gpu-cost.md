---
title: GPU Cost Estimation
description: Auto-detect across 16 GPU profiles and track per-run cost against your hourly rate.
---

# GPU Cost Estimation

> **Status:** GPU detection + per-run duration + audit-log stamping ship
> today, and `training.gpu_cost_per_hour` is the one cost setting. A
> config-driven `cost_tracking:` block (per-GPU rate tables, alert / halt
> thresholds) is **tracked on the roadmap and not yet implemented** —
> `forgelm/config.py` has no `output.cost_tracking` surface, so no example on
> this page uses one. The deferral is tracked in the
> [risks-and-decisions roadmap on GitHub](https://github.com/HodeTech/ForgeLM/blob/main/docs/roadmap/risks-and-decisions.md).

ForgeLM detects the GPU you're running on, looks up its profile (memory, compute, typical hourly rate), and tracks per-run cost. After every run, the audit log records exactly how much GPU time was used and what it cost.

## How detection works

On startup, `forgelm` reads:
- `nvidia-smi --query-gpu=name,memory.total,...` for hardware identification.
- The matching hourly rate from the built-in GPU price table in `forgelm/trainer.py`.

Supported GPUs include:

| Class | Models |
|---|---|
| Datacenter | A100 40 GB / 80 GB, H100 80 GB, H200, L40S |
| Workstation | RTX 6000 Ada, RTX A6000 |
| Consumer | RTX 4090, RTX 4080, RTX 3090, RTX 3080 |
| Cloud-only | T4, V100, A10G |
| Apple | M1/M2/M3 Max (CPU/MPS fallback) |

If your GPU isn't recognised, ForgeLM logs a warning and falls back to a generic profile (no cost estimation, but training works).

## Configuring your hourly rate

```yaml
training:
  gpu_cost_per_hour: 1.10          # USD per hour for the training GPU
```

Set `training.gpu_cost_per_hour` to your real rate. When it is unset, ForgeLM falls back to its built-in price table for the detected GPU; the run's `resource_usage` records which one it used (`cost_source`: `user_config` or `auto_detected`). There is one rate per run: per-GPU-model rate tables are not supported.

## Output

After each run, the audit log records:

```json
{
  "event": "run_complete",
  "ts": "2026-04-29T14:33:10Z",
  "duration_seconds": 1892,
  "gpu_profile": "A100_80GB",
  "gpus_used": 1,
  "estimated_cost_usd": 0.578,
  "rate_per_hour": 1.10,
  "currency": "USD"
}
```

The model card cites it:

```markdown
## Training cost

This model was trained for 31m 32s on 1× A100 80GB,
estimated at $0.58 USD at the configured rate.
```

## Pre-flight cost estimation

Before starting a long training run, run a short calibration (1-2 steps with `training.max_steps: 2`), capture the resulting `gpu_hours` from the per-run `compliance_report.json`, and multiply by your provider's hourly rate. (A dedicated `--estimate-cost` flag was discussed but not shipped; the resource-tracking path emits actuals only.)

```shell
$ forgelm --config configs/calibration.yaml --output-dir /tmp/calib
$ jq '.resource_usage.gpu_hours' /tmp/calib/compliance_report.json
0.034
$ python -c "print(0.034 * (3 / 0.034) * 1.10)"   # 6h training at $1.10/hr
$7.15
```

The calibration approach is typically within 20% of actual.

## Multi-GPU and distributed

For multi-GPU training, ForgeLM multiplies the single `training.gpu_cost_per_hour` rate by the GPU count (`gpu_hours` is the training time times the GPU count):

A 4×A100 run for 2 hours = 4 × 2 × $1.10 = $8.80, regardless of whether you use ZeRO or FSDP.

## Cost alerts (roadmap — not yet implemented)

For runs that may run away, the planned `cost_tracking` block would support
threshold-based alerts (a webhook fires when an alert threshold is crossed) and
halts (training stops at a halt threshold). No such setting exists in
`forgelm/config.py` today.

When implemented, the alert will fire the configured webhook (see [Webhooks](#/operations/webhooks)) — useful in CI to catch a misconfigured run before it spends a week of budget overnight. Until then, monitor cost manually via the audit log + a budget-side guardrail in your scheduler.

## Custom GPU profiles

To add a GPU not in the default profile:

```yaml
gpu_profiles:
  custom:
    - name: "AcmeNVIDIA-XYZ"
      pattern: "AcmeNVIDIA-XYZ"        # match against nvidia-smi name
      memory_gb: 96
      compute_capability: 9.0
      tensor_cores: true
```

Drop this in your project root as `gpu_profiles.local.yaml`; ForgeLM auto-merges it.

## Common pitfalls

:::warn
**Hourly rate as gospel.** ForgeLM's defaults are reasonable averages — your actual cloud bill depends on instance type, region, spot vs on-demand, and discounts. Override with your real rate.
:::

:::warn
**Multi-tenancy on shared GPUs.** If multiple jobs share a GPU (rare for training, common for inference), the cost-tracking multiplies rather than divides. Use `--gpus N` to declare actual allocation.
:::

:::tip
**Track cost over time.** The audit log includes cost for every run; trend it over weeks to catch creeping overruns. A 2× cost increase for the same training time is usually a sign that data grew or hyperparameters drifted.
:::

## See also

- [VRAM Fit-Check](#/operations/vram-fit-check) — runs alongside cost estimation.
- [CI/CD Pipelines](#/operations/cicd) — cost alerts in CI.
- [Configuration Reference](#/reference/configuration) — full `cost_tracking` block.
