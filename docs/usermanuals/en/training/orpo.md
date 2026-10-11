---
title: Odds Ratio Preference Optimization (ORPO)
description: Combine SFT and preference learning into a single training stage.
---

# Odds Ratio Preference Optimization (ORPO)

ORPO collapses SFT and DPO into one training pass. The loss combines a standard SFT term (on `chosen`) with an odds-ratio penalty against `rejected`. The result: faster wall-clock, no separate stages, no reference model.

ForgeLM prefers top-level `trl.ORPOConfig` / `trl.ORPOTrainer`, falling back to `trl.experimental.orpo`.
The fallback is an **upstream experimental API** that can change or disappear without notice.
ForgeLM preserves `TRLExperimentalWarning` and logs the selected path and TRL version; if neither location
exports the required class, it raises an actionable config error.

## When to use ORPO

| Use ORPO when... | Use SFT → DPO when... |
|---|---|
| You want one training stage instead of two. | You want to inspect SFT before deciding on alignment. |
| Wall-clock time matters (e.g. CI/CD overnight runs). | You'll iterate on preference data more than SFT data. |
| You have both pairs and clean SFT data ready. | You only have SFT data now and preferences later. |
| You're starting from a base model. | You're aligning an already-SFT-trained model. |

:::tip
ORPO is the only alignment method that works well *directly* from a base model without an SFT prerequisite — because it includes its own SFT term.
:::

## Quick example

```yaml
model:
  name_or_path: "Qwen/Qwen2.5-7B-Instruct"
  max_length: 4096

lora:
  r: 16
  alpha: 32
  method: "lora"
  target_modules: ["q_proj", "k_proj", "v_proj", "o_proj"]

data:
  dataset_name_or_path: "data/preferences.jsonl"

training:
  trainer_type: "orpo"
  num_train_epochs: 1
  per_device_train_batch_size: 2
  learning_rate: 5.0e-6
  orpo_beta: 0.1                 # flat field — weight of the odds-ratio penalty
  output_dir: "./checkpoints/orpo"
```

## Dataset format

`preference` format with `prompt`, `chosen`, `rejected`. ORPO's SFT term trains on `chosen`; the odds-ratio term penalises `rejected`. See [Dataset Formats](#/concepts/data-formats).

## Configuration parameters

ORPO knobs are flat fields under `training:` (no nested `training.orpo:` block — see `forgelm/config.py` `TrainingConfig`):

| Parameter | Type | Default | Description |
|---|---|---|---|
| `training.orpo_beta` | float | `0.1` | Odds-ratio penalty strength. Higher = stronger preference shift. |
| `training.trainer_type` | string | `"sft"` | Set to `"orpo"` to enable the ORPO training path. |

ForgeLM does **not** expose `loss_type` or `sft_weight` as configurable fields — TRL's `ORPOTrainer` runs with library defaults (sigmoid loss, SFT weight = 1.0).

## Compute and memory

About 1.5× SFT memory — no reference model required (unlike DPO), but the loss processes both `chosen` and `rejected` per row.

## When ORPO struggles

| Situation | Why |
|---|---|
| Very small preference dataset (<2K rows) | Combined loss needs more data than separate SFT+DPO |
| Quality varies wildly across preference pairs | The combined gradient gets noisy |
| You need to inspect SFT outputs before alignment | ORPO doesn't produce an intermediate SFT checkpoint |

In those cases, run SFT and DPO as separate stages.

## Common pitfalls

:::warn
**Using ORPO on a model that's already SFT-trained.** ORPO's SFT term will continue training the model on the `chosen` responses — fine if you want that, but if you wanted "DPO-only on top of my SFT checkpoint", use [DPO](#/training/dpo) instead.
:::

:::warn
**Setting `beta` too high.** ORPO's `beta` controls how much the odds-ratio term dominates. Too high and the SFT term gets drowned out — leading to malformed outputs that "win the comparison" but aren't useful.
:::

## See also

- [SFT](#/training/sft) and [DPO](#/training/dpo) — the two-stage approach ORPO compresses.
- [Configuration Reference](#/reference/configuration) — full parameter list.
