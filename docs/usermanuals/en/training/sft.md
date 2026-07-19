---
title: Supervised Fine-Tuning (SFT)
description: The foundation of every alignment pipeline — train a base model on instruction pairs.
---

# Supervised Fine-Tuning (SFT)

SFT is the workhorse of post-training. You give the model examples of correct outputs and it learns the pattern. Almost every project starts here, even if it ends in DPO or GRPO.

## When to use SFT

| Use SFT when... | Don't use SFT when... |
|---|---|
| You have prompt-completion pairs (the most common shape). | You only have preference pairs. Use [DPO](#/training/dpo) directly (but see warning below). |
| You're starting from a base model and need to teach format. | You already SFT-trained and only need preference alignment. |
| You want simple, stable, well-understood training dynamics. | You need RL-style reward optimisation. Use [GRPO](#/training/grpo). |

:::tip
SFT first, almost always. Even teams with rich preference data SFT before DPO/SimPO/KTO — going straight to preference learning on a base model produces unstable, format-broken outputs.
:::

## Quick example

```yaml
model:
  name_or_path: "Qwen/Qwen2.5-7B-Instruct"
  max_length: 4096
  backend: "transformers"            # or "unsloth" — replaces the legacy `use_unsloth: true` flag

lora:
  r: 16
  alpha: 32
  method: "lora"                     # or "dora" / "pissa" / "rslora"
  target_modules: ["q_proj", "k_proj", "v_proj", "o_proj"]

data:
  dataset_name_or_path: "data/train.jsonl"

training:
  trainer_type: "sft"
  num_train_epochs: 3
  per_device_train_batch_size: 4
  gradient_accumulation_steps: 2
  learning_rate: 2.0e-4
  warmup_ratio: 0.03
  output_dir: "./checkpoints/sft"
  packing: false                     # set true to bin-pack short samples
```

```shell
$ forgelm --config configs/sft.yaml --dry-run
$ forgelm --config configs/sft.yaml
```

## Dataset format

Two formats supported. See [Dataset Formats](#/concepts/data-formats) for full detail.

**Single-turn `instructions`:**
```json
{"prompt": "Translate to French: 'Good morning'.", "completion": "Bonjour."}
```

**Multi-turn `messages`:**
```json
{"messages": [
  {"role": "user", "content": "..."},
  {"role": "assistant", "content": "..."}
]}
```

## Configuration parameters

The SFT-specific knobs live alongside the standard `training` block.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `training.learning_rate` | float | `2e-4` | LoRA: 1e-4 to 5e-4. Full-parameter: 1e-5 to 5e-5. |
| `training.num_train_epochs` | int | `3` | More epochs = more memorisation, less generalisation. |
| `training.per_device_train_batch_size` | int | `4` | Per-device. Multiply by `gradient_accumulation_steps` for effective batch. |
| `training.packing` | bool | `false` | Pack short sequences together for throughput. Adds 30-50% speed. |
| `training.sample_packing` | bool | `false` | **Deprecated** alias for `packing`; forwards to `packing: true` with a `DeprecationWarning`. Scheduled for removal in v1.0.0 — use `packing`. |
| `training.neftune_noise_alpha` | float | `null` | Embedding-noise regularisation. `5.0` improves on small datasets. |
| `model.max_length` | int | `2048` | Context window during training (lives under `model:`, not `training:`). Longer = more VRAM. |

The full parameter list is in [Configuration Reference](#/reference/configuration).

## Compute and memory

SFT is the lightest of all post-training paradigms in memory:

| Model | LoRA rank | `max_length` | Approx VRAM (QLoRA 4-bit) |
|---|---|---|---|
| 7B | 16 | 4096 | 8 GB |
| 13B | 16 | 4096 | 14 GB |
| 8B Llama 3 | 32 | 8192 | 16 GB |
| 70B | 16 | 2048 | needs 2× A100 + ZeRO |

Always run `--fit-check` before submitting:

```shell
$ forgelm --config configs/sft.yaml --fit-check
FITS  est. peak 7.8 GB / 12 GB available
```

## Common pitfalls

:::warn
**Learning rate too high for full fine-tunes.** 2e-4 works for LoRA but melts a full-parameter run. For full fine-tuning, drop to 1e-5 to 5e-5.
:::

:::warn
**Loss going up instead of down.** Likely a tokenizer mismatch — your data was formatted for a different chat template than the one in the model's tokenizer. Re-run `forgelm audit` and check the rendered samples in the audit report.
:::

:::warn
**Loss going up after a tokenizer change.** Switching `model.name_or_path` between models that ship different chat templates without re-running `forgelm audit` against the new tokenizer is the most common foot-gun. The audit's rendered-sample preview shows what the trainer will actually see — verify the format matches before committing.
:::

:::tip
**Sample packing speeds things up dramatically.** If your average sample is much shorter than `max_length`, set `packing: true` — ForgeLM bin-packs short examples into single sequences for 30-50% throughput improvement. No quality difference for instruction tuning.
:::

## What you get on disk

After training:

```text
checkpoints/sft/
├── adapter_model.safetensors      ← LoRA weights (or merged checkpoint if not using LoRA)
├── README.md                      ← model card
├── config_snapshot.yaml           ← exact config used
└── compliance/                   ← Annex IV bundle (auto-generated when the `compliance:` config block is present)
```

## See also

- [DPO](#/training/dpo) — the usual next step after SFT.
- [LoRA, QLoRA, DoRA](#/training/lora) — parameter-efficient SFT.
- [Dataset Audit](#/data/audit) — always run before SFT.
- [Configuration Reference](#/reference/configuration) — all training parameters.
