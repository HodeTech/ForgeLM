---
title: Simple Preference Optimization (SimPO)
description: Reference-free preference learning — DPO without keeping a reference model in memory.
---

# Simple Preference Optimization (SimPO)

SimPO is a streamlined cousin of DPO that removes the reference model from the equation. The trade-off: roughly half the VRAM, slightly less stability.

ForgeLM prefers top-level `trl.CPOConfig` / `trl.CPOTrainer`, falling back to `trl.experimental.cpo`.
The fallback is an **upstream experimental API** that can change or disappear without notice.
ForgeLM preserves `TRLExperimentalWarning` and logs the selected path and TRL version; if neither location
exports the required class, it raises an actionable config error.

## When to use SimPO

| Use SimPO when... | Use DPO instead when... |
|---|---|
| Your VRAM budget is tight (e.g. 13B+ on a single 24 GB GPU). | You can afford the second model in memory. |
| You don't have a clean reference checkpoint. | Your SFT checkpoint is high quality and trustworthy. |
| You want simpler training dynamics. | Stability and reproducibility matter more than VRAM. |

:::tip
A practical rule: if [`--fit-check`](#/operations/vram-fit-check) reports `OOM` for DPO, switch to SimPO before considering smaller models or shorter contexts. The quality gap is usually less than 5% on standard benchmarks.
:::

## Quick example

```yaml
model:
  name_or_path: "./checkpoints/sft-base"
  max_length: 4096

lora:
  r: 16
  alpha: 32
  method: "lora"
  target_modules: ["q_proj", "k_proj", "v_proj", "o_proj"]

data:
  dataset_name_or_path: "data/preferences.jsonl"

training:
  trainer_type: "simpo"
  num_train_epochs: 1
  per_device_train_batch_size: 2
  learning_rate: 8.0e-7      # ~10× smaller than DPO
  simpo_beta: 2.0            # higher than DPO's 0.1 — flat field
  simpo_gamma: 1.0           # margin term — flat field
  output_dir: "./checkpoints/simpo"
```

## Dataset format

Same as DPO — `preference` format with `prompt`, `chosen`, `rejected`. See [Dataset Formats](#/concepts/data-formats).

## Configuration parameters

SimPO knobs are flat fields under `training:` (no nested `training.simpo:` block):

| Parameter | Type | Default | Description |
|---|---|---|---|
| `training.simpo_beta` | float | `2.0` | Length-normalised reward scale. Higher = stronger preference shift. SimPO's beta is *not* the same scale as DPO's. |
| `training.simpo_gamma` | float | `0.5` | Margin term — the gap SimPO tries to maintain between chosen and rejected log-likelihoods. |
| `training.trainer_type` | string | `"sft"` | Set to `"simpo"` to enable the SimPO training path. |

ForgeLM fixes `loss_type="simpo"` and `cpo_alpha=0` (pure SimPO, no NLL term), passing `simpo_beta` as `beta`
and forwarding `simpo_gamma`. TRL length-normalises the response log-probabilities for this loss.
ForgeLM does **not** expose `loss_type`, `length_normalize`, or `label_smoothing` as configurable fields;
label smoothing stays at TRL's default `0`.

## Compute and memory

| Model | LoRA rank | `max_length` | Approx VRAM (QLoRA 4-bit) |
|---|---|---|---|
| 7B | 16 | 4096 | 9 GB |
| 13B | 16 | 4096 | 16 GB |
| 8B | 32 | 8192 | 18 GB |

About 1.2× SFT memory — much lighter than DPO's 2×.

## Choosing `simpo_beta` and `simpo_gamma`

| Combination | Behaviour |
|---|---|
| `simpo_beta=2.0`, `simpo_gamma=0.5` | Default. Balanced. |
| `simpo_beta=2.5`, `simpo_gamma=1.0` | More aggressive preference shift. |
| `simpo_beta=1.5`, `simpo_gamma=0.3` | Gentler, closer to original SFT outputs. |

:::warn
SimPO's `simpo_beta` is on a different scale than DPO's `dpo_beta`. Don't copy DPO hyperparameters as-is — start from SimPO defaults.
:::

## Common pitfalls

:::warn
**Overshooting on beta.** SimPO is more sensitive than DPO; high `beta` plus a small dataset produces a model that aggressively prefers chosen but loses general capability. Watch your benchmark scores during training.
:::

:::warn
**Using SimPO without SFT first.** Same warning as DPO — start from a quality SFT checkpoint, not a raw base model.
:::

## See also

- [DPO](#/training/dpo) — the reference-based cousin; switch to SimPO when VRAM is tight.
- [ORPO](#/training/orpo) — combines SFT and preference loss in one stage.
- [Configuration Reference](#/reference/configuration) — full parameter list.
