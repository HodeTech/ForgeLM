---
title: VRAM Fit-Check
description: Pre-flight memory estimation — fits, tight, OOM, or unknown — before submitting a long job.
---

# VRAM Fit-Check

`--fit-check` runs a static analysis of your config and reports whether peak VRAM will fit the available GPU. No training happens, no data is loaded — it just answers the question "will this OOM?" in seconds.

## Quick example

```shell
$ forgelm --config configs/customer-support.yaml --fit-check
FITS  est. peak 11.4 GB / 12 GB available

Components:
  Model weights (4-bit):     3.8 GB
  KV cache (max_length=4096): 1.4 GB
  Activations (batch=2):     2.6 GB
  Optimizer state (LoRA):    1.3 GB
  Reference model (DPO):     1.9 GB  ← would OOM without QLoRA
  Buffer (10%):              0.4 GB
                             -------
  Total estimated peak:     11.4 GB

Recommendations: none — this configuration fits.
```

## Verdicts

| Verdict | Meaning | Action |
|---|---|---|
| `FITS` | Comfortably within VRAM. | Proceed. |
| `TIGHT` | Within VRAM but no headroom for activation bursts. | Reduce `max_length` or batch size. |
| `OOM` | Will not fit. | Apply suggested fixes. |
| `UNKNOWN` | GPU profile not in database. | Train conservatively; report to GitHub Issues. |

## What `OOM` looks like

```text
$ forgelm --config configs/large-context.yaml --fit-check
OOM   est. peak 32.4 GB / 24 GB available

Components:
  Model weights (full prec): 14.2 GB
  KV cache (max_length=32768): 6.8 GB     ← biggest contributor
  ...

Suggested fixes (any one usually resolves):
  1. Enable QLoRA: model.load_in_4bit: true   (saves ~10 GB)
  2. Reduce max_length: 32768 → 8192          (saves ~5 GB)
  3. Reduce batch_size: 4 → 2                 (saves ~2 GB)
  4. Switch to SimPO from DPO (no ref model)  (saves ~10 GB)
  5. Enable distributed.zero_stage: 2         (multi-GPU)
```

## What components are estimated

| Component | Depends on |
|---|---|
| Model weights | Model size + precision (full / 8-bit / 4-bit) |
| KV cache | `max_length` × hidden dim × layers × heads |
| Activations | `batch_size` × `max_length` × hidden dim |
| Gradients | Model size × precision (or LoRA rank if PEFT) |
| Optimizer state | Adam: 2× model size × precision; LoRA: 2× rank × ... |
| Reference model | DPO/KTO only — full copy of model |
| Reward model | GRPO only |
| Activation memory peaks | Estimated from architecture-specific patterns |
| Buffer | 10% safety margin |

## How accurate is it?

Empirically, `--fit-check` is correct ~95% of the time. The 5% failures are usually:

- Unusual model architectures (MoE with sparse expert routing).
- Sample packing with very high token efficiency (better than expected).
- DeepSpeed offload configurations (lower than expected).

For peace of mind on borderline cases, the verdict displays an estimate range with confidence:

```text
TIGHT  est. peak 21.8 GB / 24 GB (95% confidence: 19.4 - 23.9 GB)
```

If the upper end of the confidence range exceeds available VRAM, treat the run as OOM-likely.

## Multi-GPU

For distributed training, fit-check reads the GPU count from `nvidia-smi` automatically:

```shell
$ forgelm --config configs/zero3.yaml --fit-check
FITS  est. peak 14.2 GB / 80 GB per GPU (sharded across 4)

ZeRO-3 sharding:
  Optimizer state: 1/4 per GPU
  Gradients: 1/4 per GPU
  Parameters: 1/4 per GPU
```

(There is no `--gpus N` flag; the estimator probes the live device tree.)

## Programmatic API

For dashboards or automation. `estimate_vram` takes a **loaded config object**, not a path:

```python
from forgelm.config import load_config
from forgelm.fit_check import estimate_vram

config = load_config("configs/run.yaml")
result = estimate_vram(config)

print(f"Verdict:   {result.verdict}")
print(f"Estimated: {result.estimated_gb:.1f} GB")
if result.available_gb is None:
    print("Available: unknown (no CUDA device visible)")
else:
    print(f"Available: {result.available_gb:.1f} GB")
for note in result.recommendations:
    print(f"  - {note}")
```

`FitCheckResult` fields: `verdict`, `estimated_gb`, `available_gb`, `recommendations`, `breakdown`, `hypothetical`, `notes`. `forgelm.fit_check` also exports `format_fit_check` for the human-readable rendering the CLI uses.

:::warn
**`available_gb` is `None` when no CUDA device is visible**, and `verdict` is then `UNKNOWN` — the estimator can still compute `estimated_gb`, but it has nothing to compare it against. Guard for `None` before formatting, as above; a dashboard that assumes a float crashes on any CPU-only runner.
:::

:::warn
**`estimate_peak_memory` and `available_memory` do not exist.** Earlier versions of this page documented both, plus a `config_path=` keyword and the attributes `.peak_gb` / `.total_gb`. The import fails outright. Use `estimate_vram(config)` with the field names listed above.
:::

## When --fit-check is wrong

Edge cases where the estimate is off:

- **Unusual MoE routing.** Some MoE models have load patterns the estimator doesn't model. Run a short calibration training and compare actual peak.
- **CPU offload.** ZeRO-3 with `offload_param: cpu` reduces VRAM unpredictably; estimate is conservative (over-estimates VRAM use).
- **Very long sequences** (>64K). The `O(N²)` attention term dominates; small differences in implementation matter.

For these cases, treat any `TIGHT` verdict from `--fit-check` as a hard refusal to start training, and run a short calibration training (1-2 steps with `training.max_steps: 2`) to compare actual peak against the estimate. (A `--fit-check-strict` flag was discussed but not shipped — use the calibration approach instead.)

## Common pitfalls

:::warn
**Skipping fit-check on "I think this fits" runs.** A 5-minute fit-check saves you from a 6-hour OOM 6 hours into training. Always run it.
:::

:::warn
**Running with `TIGHT` verdict.** Tight runs OOM intermittently — the first few epochs fit, then a particularly long sequence triggers an activation burst. Either reduce something or be ready for crashes.
:::

:::tip
**Fit-check before --dry-run.** Order matters: dry-run downloads the model (slow); fit-check is a static analysis (fast). If fit-check says OOM, you've saved the download. Always run fit-check first.
:::

## See also

- [GPU Cost Estimation](#/operations/gpu-cost) — sister pre-flight check.
- [Distributed Training](#/training/distributed) — for when single-GPU OOMs.
- [LoRA, QLoRA, DoRA](#/training/lora) — common OOM remedy.
