---
title: GaLore
description: Full-parameter training in LoRA-level memory via gradient low-rank projection.
---

# GaLore

GaLore (**G**radient **L**ow-**R**ank Projection) trains *all* parameters of a model — but at LoRA-level memory cost. Instead of storing the full optimiser state, GaLore projects gradients to a low-rank subspace, periodically re-projecting as training proceeds.

The result: full fine-tune quality at ~LoRA memory.

## When to use GaLore

| Use GaLore when... | Use LoRA/QLoRA when... |
|---|---|
| You want full-parameter training but VRAM is tight. | A small adapter is all you need. |
| LoRA underfits — quality plateaus before convergence. | LoRA at rank 32-64 already meets your bar. |
| You have time for slightly slower training (~15-20% slower per step). | Wall-clock speed matters more than raw quality. |
| You're fine-tuning math or reasoning models where every weight matters. | You're doing instruction tuning. |

## Quick example

```yaml
model:
  name_or_path: "Qwen/Qwen2.5-7B-Instruct"
  load_in_4bit: false                  # GaLore prefers full precision
  max_length: 4096

training:
  trainer_type: "sft"
  learning_rate: 1.0e-5                # full-FT learning rate, not LoRA
  galore_enabled: true
  galore_optim: "galore_adamw_8bit"    # GaLore optimiser variant
  galore_rank: 256                     # higher than LoRA default (128) — projection rank
  galore_update_proj_gap: 200          # re-project every N steps
  galore_scale: 0.25
  galore_proj_type: "std"              # std (default), reverse_std, right, left
  output_dir: "./checkpoints/galore"
```

Note: when `training.galore_enabled: true`, ForgeLM automatically uses the GaLore-aware optimiser; `lora` should not be configured at the same time.

## Configuration parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `training.galore_enabled` | bool | `false` | Master switch. |
| `training.galore_optim` | string | `"galore_adamw"` | GaLore optimiser variant: `galore_adamw`, `galore_adamw_8bit`, `galore_adafactor`, or their `_layerwise` counterparts. `_8bit` halves optimiser-state VRAM; `_layerwise` recomputes per-layer for a lower peak. |
| `training.galore_rank` | int | `128` | Gradient projection rank. Higher = closer to full-FT, more memory. |
| `training.galore_update_proj_gap` | int | `200` | Steps between re-projections. Lower = adapt to changing gradients faster. |
| `training.galore_scale` | float | `0.25` | Scaling on the projected gradients. |
| `training.galore_proj_type` | string | `"std"` | Projection direction. `std` is the default; experiment if convergence stalls. |
| `training.galore_target_modules` | list | `null` | Which modules to project gradients for. |

## Memory comparison

For a 7B model at `max_length: 4096`, batch size 1:

| Method | Trainable params | VRAM (full precision) | VRAM (4-bit base) |
|---|---|---|---|
| Full FT | 100% | 56 GB | n/a |
| LoRA r=16 | 0.2% | 18 GB | 9 GB (QLoRA) |
| **GaLore r=256** | **100%** | **22 GB** | n/a |

So GaLore at r=256 (illustrative; the shipped default is r=128) lets you full-fine-tune a 7B model on a single 24 GB GPU — roughly the same VRAM as plain LoRA at full precision.

## Compute

GaLore is ~15-20% slower per step than LoRA because of the projection/re-projection overhead. End-to-end the difference often disappears: GaLore frequently converges in fewer steps because it has access to the full parameter space.

## Common pitfalls

:::warn
**Trying to combine GaLore with LoRA.** They're alternatives, not complements. ForgeLM's config schema rejects setting both `lora.r` and `training.galore_enabled`.
:::

:::warn
**Using LoRA learning rates.** GaLore is full-parameter — use full-FT learning rates (1e-5 to 5e-5), not LoRA learning rates (1e-4 to 5e-4).
:::

:::warn
**Setting `update_proj_gap` too high.** Re-projecting infrequently means the gradient subspace doesn't track the optimisation trajectory well. Default 200 is reasonable; don't go above 500.
:::

## See also

- [LoRA, QLoRA, DoRA](#/training/lora) — the more common alternative.
- [Distributed Training](#/training/distributed) — for models bigger than a single GPU.
- [Configuration Reference](#/reference/configuration) — full GaLore parameter list.
