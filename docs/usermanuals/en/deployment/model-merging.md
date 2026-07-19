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
model:
  name_or_path: "Qwen/Qwen2.5-7B-Instruct"   # base model every adapter was trained on

merge:
  enabled: true
  method: "ties"
  models:
    - path: "./checkpoints/customer-support"
      weight: 0.5
    - path: "./checkpoints/code-assistant"
      weight: 0.3
    - path: "./checkpoints/math-reasoning"
      weight: 0.2
  ties_trim_fraction: 0.3              # trims the bottom 30% of deltas by magnitude, keeps the top ~70%
  output_dir: "./checkpoints/merged"
```

```shell
$ forgelm --merge --config configs/merge.yaml
INFO Running TIES merge on 3 adapters...
INFO Model merge completed: 3 models merged with 'ties' → ./checkpoints/merged
```

## Quick example: Linear

```yaml
model:
  name_or_path: "Qwen/Qwen2.5-7B-Instruct"

merge:
  enabled: true
  method: "linear"
  models:
    - { path: "./checkpoints/v1", weight: 0.5 }
    - { path: "./checkpoints/v2", weight: 0.5 }
  output_dir: "./checkpoints/v1-v2-blend"
```

Linear is the simplest — just averages weights. Always works as a starting point; might not be optimal.

## Algorithm parameters

| Algorithm | Key parameters |
|---|---|
| `linear` | Per-model `weight` in `merge.models` (auto-normalised to sum to 1.0). |
| `slerp` | No separate factor — the interpolation weight is derived from the two entries' relative `weight` in `merge.models`. Requires exactly two entries. |
| `ties` | `merge.ties_trim_fraction` — fraction of smallest-magnitude deltas trimmed per model before the sign vote (default `0.2`, i.e. keep the top ~80%). |
| `dare` | `merge.dare_drop_rate` — probability each delta is randomly dropped before rescaling (default `0.3`). `merge.dare_seed` — RNG seed so a DARE merge is reproducible run-to-run (default `42`). |

## Evaluating after merging

Always re-evaluate the merged model — it's a different model than any of the inputs. `merge` and `evaluation` are separate top-level config blocks; after `forgelm --merge` finishes, point a second config's `model.name_or_path` at the merged output directory and run the benchmark gate against it directly with `--benchmark-only` (no training). `--benchmark-only` only reads `evaluation.benchmark` — it never invokes the safety classifier, so an `evaluation.safety` block in the same config is silently ignored on this code path. Run the two gates as two separate commands:

```yaml
evaluation:
  benchmark:
    tasks: ["hellaswag", "humaneval", "gsm8k"]    # mix of skills from each specialist
    min_score: 0.5
```

```shell
$ forgelm --benchmark-only ./checkpoints/merged --config configs/eval.yaml
$ forgelm safety-eval --model ./checkpoints/merged --default-probes --output-dir ./checkpoints/merged/safety
```

The standalone `safety-eval` subcommand is documented in [Llama Guard Safety](#/evaluation/safety). If the merged model regresses on any task or safety category, fall back to one of the specialists or try a different algorithm.

## Diagnosing merge failures

Symptoms of a bad merge:

| Symptom | Likely cause | Fix |
|---|---|---|
| Coherent but generic outputs | Linear merge averaged out specialisations | Switch `merge.method` to `ties` with `ties_trim_fraction: 0.3` |
| Garbled outputs | Adapter base mismatch | Check all adapters use the same base model |
| Random low scores on every task | `dare_drop_rate` too high (too many deltas dropped) | Lower `merge.dare_drop_rate` (try 0.1-0.3) |
| One specialist dominates | One `weight` too high relative to the rest | Rebalance the `weight` values in `merge.models` |

## Configuration

```yaml
model:
  name_or_path: "Qwen/Qwen2.5-7B-Instruct"

merge:
  enabled: true
  method: "ties"
  models:
    - path: "./checkpoints/v1"
      weight: 0.4
    - path: "./checkpoints/v2"
      weight: 0.6
  ties_trim_fraction: 0.3              # weights are auto-normalised to sum to 1.0
  output_dir: "./checkpoints/merged"
```

## Programmatic merging

For automation pipelines:

```python
from forgelm.merging import merge_peft_adapters

result = merge_peft_adapters(
    base_model_path="Qwen/Qwen2.5-7B-Instruct",
    adapters=[
        {"path": "./checkpoints/v1", "weight": 0.5},
        {"path": "./checkpoints/v2", "weight": 0.5},
    ],
    method="ties",
    ties_trim_fraction=0.3,
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
