---
title: Model Merging
description: Combine multiple LoRA adapters into one model with TIES, DARE, SLERP, or linear merge.
---

# Model Merging

Model merging combines several fine-tuned models (or LoRA adapters) into one. Useful when you have specialists (one for code, one for support, one for math) and want a generalist that retains some capability of each. ForgeLM supports four merge algorithms via `forgelm --merge`.

## When to use merging

| Use merging when... | Don't use merging when... |
|---|---|
| You have multiple LoRA adapters trained on overlapping bases. | The "specialists" are radically different (different bases, different sizes). |
| You want one deployable model instead of multiple. | You need different behaviours per request — route at inference instead. |
| You're exploring multi-skill models without training from scratch. | Production reliability matters more than capability breadth. |

Merging trades a bit of each specialist's quality for breadth. Always re-evaluate after merging.

## Algorithm choice

| Algorithm | What it does | When it shines |
|---|---|---|
| **Linear** | Average weights with configurable per-adapter coefficients. | Same-architecture, well-aligned adapters. Simplest. |
| **SLERP** | Spherical linear interpolation between two adapters. | Two-way merges; preserves manifold geometry. |
| **TIES** | Trim, Elect-sign, Disjoint-merge. Drops near-zero deltas, resolves conflicts by sign. | 3+ adapters; common starting point. |
| **DARE** | Drop-and-Rescale. Randomly zeroes weight deltas, rescales survivors. | Mitigates interference; pairs well with TIES (DARE-TIES). |

## Quick example: TIES

```yaml
merge:
  enabled: true
  algorithm: "ties"
  base_model: "Qwen/Qwen2.5-7B-Instruct"
  models:
    - path: "./checkpoints/customer-support"
      weight: 0.5
    - path: "./checkpoints/code-assistant"
      weight: 0.3
    - path: "./checkpoints/math-reasoning"
      weight: 0.2
  parameters:
    threshold: 0.7                      # TIES-specific: top-K% of deltas to keep
  output:
    dir: "./checkpoints/merged"
    model_card: true
```

```shell
$ forgelm --merge --config configs/merge.yaml
✓ loaded 3 adapters
✓ TIES merge: kept top 70% of deltas, resolved 1247 sign conflicts
✓ wrote ./checkpoints/merged
✓ generated model card
```

## Quick example: Linear

```yaml
merge:
  enabled: true
  algorithm: "linear"
  base_model: "Qwen/Qwen2.5-7B-Instruct"
  models:
    - { path: "./checkpoints/v1", weight: 0.5 }
    - { path: "./checkpoints/v2", weight: 0.5 }
  output:
    dir: "./checkpoints/v1-v2-blend"
```

Linear is the simplest — just averages weights. Always works as a starting point; might not be optimal.

## Algorithm parameters

| Algorithm | Key parameters |
|---|---|
| `linear` | `weights:` per model |
| `slerp` | `t:` interpolation factor (0.0 = first adapter, 1.0 = second) |
| `ties` | `threshold:` (top-K% of deltas to keep, 0.6-0.8 typical), `density:` (alternative formulation) |
| `dare` | `density:` (fraction to keep, 0.5-0.9), `epsilon:` (rescaling) |
| `dare_ties` | Both DARE and TIES parameters |

## Evaluating after merging

Always re-evaluate the merged model — it's a different model than any of the inputs.

```yaml
merge:
  enabled: true
  algorithm: "ties"
  ...
  evaluation:
    benchmark:
      tasks: ["hellaswag", "humaneval", "gsm8k"]    # mix of skills from each specialist
      floors:
        hellaswag: 0.55
        humaneval: 0.40
        gsm8k: 0.50
    safety:
      enabled: true
```

If the merged model regresses on any task, fall back to one of the specialists or try a different algorithm.

## Diagnosing merge failures

Symptoms of a bad merge:

| Symptom | Likely cause | Fix |
|---|---|---|
| Coherent but generic outputs | Linear merge averaged out specialisations | Try TIES with `threshold: 0.7` |
| Garbled outputs | Adapter base mismatch | Check all adapters use the same base model |
| Random low scores on every task | DARE density too low | Raise `density:` to 0.9 |
| One specialist dominates | Linear weight too high for that adapter | Rebalance weights |

## Configuration

```yaml
merge:
  enabled: true
  algorithm: "ties"
  base_model: "Qwen/Qwen2.5-7B-Instruct"
  models:
    - path: "./checkpoints/v1"
      weight: 0.4
    - path: "./checkpoints/v2"
      weight: 0.6
  parameters:
    threshold: 0.7
    normalize: true                     # normalise weights to sum to 1.0
  output:
    dir: "./checkpoints/merged"
    model_card: true
    save_format: "safetensors"          # or pytorch
```

## Programmatic merging

For automation pipelines:

```python
from forgelm.merging import merge_adapters

merge_adapters(
    base="Qwen/Qwen2.5-7B-Instruct",
    adapters=[
        ("./checkpoints/v1", 0.5),
        ("./checkpoints/v2", 0.5),
    ],
    algorithm="ties",
    threshold=0.7,
    output_dir="./checkpoints/merged",
)
```

## Common pitfalls

:::warn
**Merging across different bases.** Adapters trained on Qwen2.5-7B can't be merged with adapters trained on Llama-3-8B — different parameter shapes. ForgeLM rejects this at merge time with a clear error.
:::

:::warn
**Skipping eval on the merged model.** Treating "we merged 3 specialists" as a guarantee of "we have a generalist" is wishful thinking. Re-evaluate.
:::

:::warn
**Compounding merges.** Merging A+B, then merging the result with C, is generally worse than merging A+B+C in one shot. Use a single multi-way merge.
:::

:::tip
For exploratory merging, generate a small grid of `(algorithm, parameters)` combinations and evaluate each. A `forgelm merge-sweep` helper that automates this remains **planned post-Phase 14** — Phase 14 itself shipped multi-stage SFT/DPO/GRPO pipeline chaining in `v0.7.0` (see the [Phase 14 completed-phases entry on GitHub](https://github.com/HodeTech/ForgeLM/blob/main/docs/roadmap/completed-phases.md#phase-14-multi-stage-pipeline-chains-v070)) but did not include a merge-sweep CLI; that helper waits for explicit operator demand. Until then, write a small shell loop that calls `forgelm` once per `(algorithm, parameters)` pair.
:::

## See also

- [LoRA, QLoRA, DoRA](#/training/lora) — produces the adapters that get merged.
- [Configuration Reference](#/reference/configuration) — full `merge:` block.
- [Synthetic Data](#/data/synthetic-data) — alternative to merging for capability breadth.
