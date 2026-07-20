---
title: Benchmark Integration
description: Run lm-evaluation-harness tasks with an average accuracy floor and auto-revert.
---

# Benchmark Integration

ForgeLM integrates with [lm-evaluation-harness](https://github.com/EleutherAI/lm-evaluation-harness) — the standard benchmark suite for LLMs — and adds the production layer on top: a minimum average accuracy floor, auto-revert on regression, and structured artifacts that flow into your compliance bundle.

## Quick example

```yaml
evaluation:
  auto_revert: true                      # REQUIRED for the exit-3 behaviour below;
                                         # the schema default is false
  benchmark:
    enabled: true
    tasks: ["hellaswag", "arc_easy", "truthfulqa", "mmlu"]
    min_score: 0.55                      # average accuracy floor across all tasks
    num_fewshot: 0                       # zero-shot eval
    batch_size: 8
    output_dir: "./checkpoints/run/artifacts/"
```

After training, ForgeLM runs the listed tasks, computes the mean score, and:
- Mean score meets or exceeds `min_score` → run succeeds (exit 0)
- Mean score falls below `min_score` → the benchmark gate fails. What happens next depends on `evaluation.auto_revert`:
  - `auto_revert: true` → the trained artefacts are **deleted** and the run exits `3` (`EXIT_EVAL_FAILURE`).
  - `auto_revert: false` (**the shipped default**) → the failure is recorded in the audit log and the JSON `benchmark` block, but the model is still promoted, the pipeline continues to the safety and judge stages, and the run exits `0`.

:::warn
**A benchmark regression does not fail your build on the shipped defaults.** `EvaluationConfig.auto_revert` defaults to `False`, so `forgelm --config run.yaml` exits `0` even when the mean score falls below `min_score` — and the regressed model is promoted. If you are wiring this gate into CI, set `evaluation.auto_revert: true` (as the example above does), or branch on `passed` in the JSON envelope rather than on `$?`. Note that "revert" means deletion, not rollback — see [Auto-Revert](#/evaluation/auto-revert).
:::

## Supported tasks

Anything in `lm-evaluation-harness` works. Common picks:

| Task | What it measures |
|---|---|
| `hellaswag` | Commonsense completion |
| `arc_easy`, `arc_challenge` | Grade-school science |
| `truthfulqa` | Resistance to common misconceptions |
| `mmlu` | Broad multitask knowledge |
| `winogrande` | Pronoun resolution |
| `gsm8k` | Grade-school math (chain of thought) |
| `humaneval` | Code completion |

For Turkish projects, ForgeLM ships templates for `mmlu_tr` and `belebele_tr` adapted to Turkish-specific tasks.

## Accuracy floor

`min_score` defines the minimum acceptable post-training **mean** score across all listed tasks. The model is only promoted when the average accuracy meets or exceeds this value.

```yaml
evaluation:
  benchmark:
    tasks: ["hellaswag", "mmlu", "truthfulqa"]
    min_score: 0.50                      # average accuracy floor (0.0–1.0)
```

When `min_score` is `null` (the default), benchmarks are run and results are recorded, but the score never blocks promotion. A value of `0.0` is equivalent to no floor.

:::tip
Set `min_score` slightly below your pre-training average baseline. Goal: catch *regressions*, not require improvement on every task. A model that gains 5% on the target task while losing 2% on hellaswag is usually fine; one whose average drops 15% is broken.
:::

## Pre-train baselines

To know what floor to set, you need a pre-training baseline. Use the `--benchmark-only` flag (which evaluates an existing model without training) with a config that pins the tasks + output path:

```yaml
# baseline.yaml
model:
  name_or_path: "Qwen/Qwen2.5-7B-Instruct"
evaluation:
  benchmark:
    tasks: ["hellaswag", "arc_easy", "truthfulqa", "mmlu"]
    output_dir: "baselines/qwen-2.5-7b/"
```

```shell
$ forgelm --config baseline.yaml --benchmark-only "Qwen/Qwen2.5-7B-Instruct"
{"hellaswag": 0.61, "arc_easy": 0.75, "truthfulqa": 0.49, "mmlu": 0.52}
```

Results land at `baselines/qwen-2.5-7b/benchmark_results.json` — `output_dir` names the directory; the filename is always `benchmark_results.json`.

A reasonable floor is the baseline average minus 0.03 (3% slack for stochastic variation):

```yaml
evaluation:
  benchmark:
    tasks: ["hellaswag", "arc_easy", "truthfulqa", "mmlu"]
    min_score: 0.56                    # baseline average ~0.59 - 0.03
```

## Output artifacts

After eval, ForgeLM writes:

```text
checkpoints/run/artifacts/
└── benchmark_results.json             ← per-task scores + overall pass/fail
```

`benchmark_results.json` structure:

```json
{
  "tasks": ["hellaswag", "truthfulqa"],
  "scores": {
    "hellaswag": 0.617,
    "truthfulqa": 0.42
  },
  "average_score": 0.5185,
  "passed": false,
  "num_fewshot": 0,
  "limit": null
}
```

CI pipelines parse `passed` (bool) and `average_score` (the single `min_score` floor is checked against the mean across all tasks, not per task). See [Auto-Revert](#/evaluation/auto-revert) for the gating logic.

## Configuration parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `false` | Master switch. |
| `tasks` | list | `[]` | Task names from lm-eval-harness. |
| `min_score` | float | `null` | Minimum average accuracy floor (0.0–1.0). Auto-revert triggers when mean score falls below this. |
| `num_fewshot` | int | `null` | Few-shot example count. `null` uses each task's documented default. |
| `batch_size` | string | `"auto"` | Eval batch size: `"auto"` or an integer string. |
| `limit` | int | `null` | Cap rows per task — for fast smoke tests. |
| `output_dir` | string | `null` | Where to save benchmark results JSON. Defaults to the training `output_dir`. |

## Common pitfalls

:::warn
**`min_score` above pre-train baseline average.** Set `min_score` higher than the base model's mean task score and every run fails — auto-revert kicks in and you never get a checkpoint. Always start with `baseline average - margin`.
:::

:::warn
**`num_fewshot` mismatch with reported public results.** Public leaderboards report at specific shot counts (e.g. MMLU is canonically 5-shot). Use the same setting if you want results to be comparable.
:::

:::tip
**Speed up iteration with `limit`.** Setting `limit: 100` runs 100 rows per task (instead of thousands) for ~10× faster eval. Use this in dev configs; remove for production.
:::

## See also

- [Auto-Revert](#/evaluation/auto-revert) — what happens when `min_score` is not met.
- [LLM-as-Judge](#/evaluation/judge) — qualitative eval beyond benchmarks.
- [Trend Tracking](#/evaluation/trend-tracking) — comparing scores across runs.
