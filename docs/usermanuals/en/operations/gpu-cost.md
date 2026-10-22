---
title: GPU Cost Estimation
description: Detect the GPU, price the run from your hourly rate or a built-in table, and record the cost of every run.
---

# GPU Cost Estimation

> **Status:** GPU detection, the training duration and a cost estimate are
> recorded today for every run that passes its evaluation-loss and benchmark gates, and `training.gpu_cost_per_hour` is the one cost
> setting. A config-driven `cost_tracking:` block (per-GPU rate tables, alert /
> halt thresholds) is **tracked on the roadmap and not yet implemented** —
> `forgelm/config.py` has no `output.cost_tracking` surface, so no example on
> this page uses one. The deferral is tracked in the
> [risks-and-decisions roadmap on GitHub](https://github.com/HodeTech/ForgeLM/blob/main/docs/roadmap/risks-and-decisions.md).

When a training run passes its evaluation-loss and benchmark gates (a run that fails one returns before the cost is collected), ForgeLM records how long it trained, on how many GPUs, and what that cost at an hourly rate: yours if you set one, otherwise a built-in price for the detected GPU. The estimate is written next to the other run results; it is not a bill, and it does not stop or alert anything.

## How detection works

After training, ForgeLM asks PyTorch for the first CUDA device's name (`torch.cuda.get_device_name(0)`), the number of CUDA devices the process can see, and the peak memory allocated on the first device. It takes the training time from the Hugging Face trainer's `train_runtime`. It does not call `nvidia-smi`. Without a CUDA GPU none of the GPU fields are recorded.

The rate comes from `training.gpu_cost_per_hour` when set, and otherwise from the price table `_GPU_PRICING` in `forgelm/trainer.py`, which is matched against the exact device name that PyTorch reports:

| Class | Recognised names |
|---|---|
| Tesla | Tesla T4, Tesla P100, Tesla V100, Tesla K80 |
| NVIDIA data center | NVIDIA A10G, NVIDIA A100-SXM4-40GB, NVIDIA A100-SXM4-80GB, NVIDIA A100 80GB PCIe, NVIDIA H100 80GB HBM3, NVIDIA H100 SXM5 80GB, NVIDIA H200, NVIDIA L4, NVIDIA L40S, NVIDIA B200 |
| GeForce (self-hosted) | NVIDIA GeForce RTX 3090, NVIDIA GeForce RTX 4090 |

The prices in that table are approximate on-demand cloud averages (for the GeForce cards, an estimate of electricity plus amortisation). A GPU that is not in the table, for example `NVIDIA RTX A6000` or `NVIDIA H100 PCIe`, is **not** reported with a warning: the run simply has no estimated cost unless you set `training.gpu_cost_per_hour`.

## Configuring your hourly rate

```yaml
training:
  gpu_cost_per_hour: 1.10          # USD per hour, per GPU
```

Set `training.gpu_cost_per_hour` to your real rate. When it is unset, ForgeLM falls back to its built-in price table for the detected GPU; the run's `resource_usage` records which one it used in `cost_source`:

| `cost_source` | Meaning |
|---|---|
| `user_config` | The rate came from `training.gpu_cost_per_hour` (`0` is accepted and records a zero cost). |
| `auto_detected` | The device name matched a table entry exactly. |
| `fuzzy_match` | The device name partly matched a table entry. This is a guess: `NVIDIA L40`, for instance, is priced as `NVIDIA L40S`. |

When no rate resolves, `cost_source`, `gpu_cost_per_hour_usd` and `estimated_cost_usd` are absent.

:::warn
**Machines without a CUDA GPU (CPU, Apple silicon).** There is no device name, and an empty name partly matches every table entry and the longest one, `NVIDIA GeForce RTX 3090`, wins, so the run is recorded at a placeholder `0.15` USD per hour with `cost_source: fuzzy_match`. Do not read that as a real cost; set `training.gpu_cost_per_hour` explicitly on such machines (`0` if the run is free to you).
:::

There is one rate per run, and it is per GPU: per-GPU-model rate tables are not supported.

## Output

The estimate appears in these places:

- **`compliance/compliance_report.json`**, under `resource_usage`, in the run's `training.output_dir`; it is written at the end of a run that also passes the safety and judge gates (a reverted run exports no report). The same data is copied into `annex_iv_metadata.json` as `computational_resources`.
- **The `--output-format json` result**, as `resource_usage` and a top-level `estimated_cost_usd`.
- **The console**, as an `Estimated training cost` line and a `GPU-hours` line that names the pricing source (text mode).
- **The run metrics**: every numeric field is copied in as `resource/<field>` (for example `resource/estimated_cost_usd`). The copies appear in the result's `metrics`, in the success webhook and in the `metrics_summary` of the `pipeline.completed` audit event (not emitted when the run stops at the human-approval gate). They are not sent to W&B, MLflow or TensorBoard.

A 4-GPU run that trained for 1892 seconds at `gpu_cost_per_hour: 1.10` records:

```json
{
  "resource_usage": {
    "gpu_model": "NVIDIA A100-SXM4-80GB",
    "peak_vram_gb": 41.7,
    "gpu_count": 4,
    "training_duration_seconds": 1892.0,
    "gpu_hours": 2.102,
    "cost_source": "user_config",
    "gpu_cost_per_hour_usd": 1.1,
    "estimated_cost_usd": 2.3124
  }
}
```

The model card does not include the cost.

## Pre-flight cost estimation

Before starting a long training run, run a short calibration with a hard step cap, read `gpu_hours` from its `resource_usage`, scale it to the full step count and multiply by your hourly rate. A dedicated `--estimate-cost` flag was discussed but not shipped; the resource-tracking path records actuals only.

```yaml
# configs/calibration.yaml — your real config, plus:
training:
  max_steps: 20
  output_dir: /tmp/calib
```

```shell
$ forgelm --config configs/calibration.yaml
$ jq '.resource_usage.gpu_hours' /tmp/calib/compliance/compliance_report.json
0.034
$ python -c "print(round(0.034 * (3000 / 20) * 1.10, 2))"   # 3,000 steps at $1.10 per GPU-hour
5.61
```

Treat the result as a rough figure. The first steps include warm-up, and the mix of sequence lengths in a few steps rarely matches the whole dataset, so run enough steps to see a stable step time before scaling it.

## Multi-GPU and distributed

`gpu_hours` is the training time multiplied by the number of CUDA devices visible to the process, and the single `training.gpu_cost_per_hour` rate is applied per GPU.

A 4×A100 run for 2 hours = 4 × 2 × $1.10 = $8.80, regardless of whether you use ZeRO or FSDP.

If the job uses fewer GPUs than the machine has, restrict the devices it can see (for example with `CUDA_VISIBLE_DEVICES`), or the estimate counts GPUs the run never used.

## Cost alerts (roadmap — not yet implemented)

For runs that may run away, the planned `cost_tracking` block would support
threshold-based alerts (a webhook fires when an alert threshold is crossed) and
halts (training stops at a halt threshold). No such setting exists in
`forgelm/config.py` today.

When implemented, the alert will fire the configured webhook (see [Webhooks](#/operations/webhooks)) — useful in CI to catch a misconfigured run before it spends a week of budget overnight. Until then, monitor cost manually via the recorded `resource_usage` and a budget-side guardrail in your scheduler.

## Custom GPU profiles

:::warn
**Not implemented.** ForgeLM has no `gpu_profiles` setting and reads no `gpu_profiles.local.yaml`; the snippet below is the planned shape only and a config that contains it is rejected. Today the way to price a GPU that is not in the table is `training.gpu_cost_per_hour`, which works for any GPU.
:::

To add a GPU not in the built-in table (planned):

```yaml
gpu_profiles:
  custom:
    - name: "AcmeNVIDIA-XYZ"
      pattern: "AcmeNVIDIA-XYZ"        # match against the device name
      memory_gb: 96
      compute_capability: 9.0
      tensor_cores: true
```

## Common pitfalls

:::warn
**Hourly rate as gospel.** ForgeLM's defaults are rough averages — your actual cloud bill depends on instance type, region, spot vs on-demand, and discounts. Override with your real rate.
:::

:::warn
**Shared GPUs.** The estimate bills the whole training time on every visible GPU; there is no setting for a fractional or shared allocation. If other jobs share the GPU, divide the figure yourself.
:::

:::tip
**Track cost over time.** Every run records its cost; trend it over weeks to catch creeping overruns. A 2× cost increase for the same training time is usually a sign that data grew or hyperparameters drifted.
:::

## See also

- [VRAM Fit-Check](#/operations/vram-fit-check) — runs alongside cost estimation.
- [CI/CD Pipelines](#/operations/cicd) — cost alerts in CI.
- [Configuration Reference](#/reference/configuration) — the `training.gpu_cost_per_hour` field.
