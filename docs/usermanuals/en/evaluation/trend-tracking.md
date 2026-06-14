---
title: Trend Tracking
description: Compare evaluation results across runs to spot slow drifts before they cross thresholds.
---

# Trend Tracking

Per-run thresholds catch regressions; trend tracking catches drift. A category that's been creeping up over five runs is a different (and often more important) signal than a one-off spike. ForgeLM stores eval results in a per-project history file and reports trends every time you run.

## Quick example

After several runs of the same project, the audit report includes a trend section:

```json
{
  "trend": {
    "lookback_runs": 10,
    "benchmark": {
      "hellaswag": {"trend": "stable", "delta_per_run": 0.001},
      "truthfulqa": {"trend": "drifting_down", "delta_per_run": -0.012, "concern": "medium"}
    },
    "safety": {
      "S5": {"trend": "drifting_up", "delta_per_run": 0.04, "concern": "high"},
      "S10": {"trend": "stable", "delta_per_run": 0.001}
    }
  }
}
```

The `concern` levels:

| Level | Trigger |
|---|---|
| `none` | No drift detected over lookback window. |
| `low` | Drift trend statistically present but small. |
| `medium` | Steady drift; will hit threshold within ~10 runs at current rate. |
| `high` | Steady drift; will hit threshold within ~3 runs. |
| `critical` | Already at or near threshold AND drifting. |

## How drift is computed

For each metric (benchmark task or safety category):

1. Pull the last N runs from the project history.
2. Linear-regress the score on run index.
3. Test slope against zero with a t-test.
4. If slope is significant *and* its magnitude is over the noise floor, report drift.

`lookback_runs` defaults to 10 — adjust based on how often you train.

## Configuration

```yaml
evaluation:
  trend:
    enabled: true
    history_file: "./.forgelm/eval-history.jsonl"
    lookback_runs: 10
    drift_p_threshold: 0.05             # statistical significance
    fail_on_concern: "high"             # exit 3 if any drift hits 'high'
```

`fail_on_concern: high` upgrades trend tracking from "advisory" to "gating" — your CI will fail not just on per-run regressions but also on drifts headed for trouble.

## Where the history file lives

By default, `.forgelm/eval-history.jsonl` in the project root. Each run appends one row:

```json
{"ts": "2026-04-29T14:33:04Z", "run_id": "abc123", "config_hash": "deadbeef", "benchmark": {...}, "safety": {...}}
```

Commit this file. It's small (one row per run, JSON), and it's the only way to track trends across CI runs and contributors.

## Visualisation

ForgeLM ships a CLI report. The dedicated `forgelm trend` subcommand is planned for v0.6.0+ Pro CLI tier (see the [Phase 13 roadmap on GitHub](https://github.com/HodeTech/ForgeLM/blob/main/docs/roadmap.md)) — today the same data is queryable directly from the JSONL with `jq`. Today's working flow:

```shell
# Last 20 S5 (defamation) scores from the trend log:
$ jq -r 'select(.safety.S5 != null) | "\(.ts) \(.safety.S5)"' \
    .forgelm/eval-history.jsonl | tail -20
```

For visualisation, the JSONL is easy to load into Grafana / Datadog
(see "For dashboards" below).

The dedicated `forgelm trend` subcommand will look like this when it
ships in v0.6.0+ — pseudo-output, NOT runnable today:

```text
# planned-v0.6.0+ pseudo-output (not runnable today):
$ forgelm trend --metric "safety.S5" --lookback 20

S5 (defamation) — last 20 runs:

  0.42 ┤                                                ╭────●
  0.30 ┤                                          ╭─────╯
  0.18 ┤                              ╭───────────╯
  0.06 ┤   ●─────●─────●─────●────────╯
       └─┴───────────────────────────────────────────────────┘
         1  3  5  7  9  11 13 15 17 19  20

Linear fit: slope=+0.018/run, p=0.001 — drifting up (high concern)
```

For dashboards, the JSONL is easy to load into Grafana or Datadog:

```shell
$ jq '.benchmark.truthfulqa, .ts' .forgelm/eval-history.jsonl > truthfulqa-trend.csv
```

## Run identification

Each run has a `run_id` (UUID) and a `config_hash` (hash of the YAML config). When you compare runs, compare like-for-like — a hyperparameter change can shift baselines without that being a regression.

Filter the history. Today's working flow uses `jq`:

```shell
$ jq 'select(.config_hash == "deadbeef") | .benchmark.hellaswag' \
    .forgelm/eval-history.jsonl | tail -30
```

The dedicated `forgelm trend` subcommand (planned v0.6.0+ Pro CLI form, not runnable today):

```text
$ forgelm trend --metric "benchmark.hellaswag" \
    --filter "config_hash=deadbeef" \
    --lookback 30
```

## Common pitfalls

:::warn
**Mixing config-changed runs with config-stable runs.** A trend computed across runs with different configs is meaningless. Use `--filter config_hash` for like-for-like.
:::

:::warn
**Lookback too short.** With `lookback_runs: 3`, every random fluctuation looks like drift. Stay at 10+ for stable signal.
:::

:::tip
**Annotate the history.** When you intentionally change something (new dataset, new hyperparameters), commit a note to `.forgelm/eval-history.jsonl` explaining why baselines might shift. Future you will thank past you.
:::

## See also

- [Benchmark Integration](#/evaluation/benchmarks) — produces the data.
- [Llama Guard Safety](#/evaluation/safety) — produces safety scores.
- [Auto-Revert](#/evaluation/auto-revert) — sister gate with per-run focus.
