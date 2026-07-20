---
title: Trend Tracking
description: Compare safety scores across runs to spot slow drifts before they cross thresholds.
---

# Trend Tracking

Per-run thresholds catch regressions; trend tracking catches drift. A safety score that's been creeping down over five runs is a different (and often more important) signal than a one-off dip. ForgeLM's trend tracking today is deliberately small: every safety evaluation appends one row to a JSON Lines history file, and it is up to you (a `jq` query, a notebook, or a Grafana/Datadog dashboard) to turn that history into a drift signal. There is no config-driven statistical drift detector and no `evaluation.trend:` config block — `evaluation` has no `trend` field on the schema.

## Quick example

Every time `evaluation.safety.enabled: true` runs (during training or via the standalone `forgelm safety-eval` subcommand), ForgeLM appends one line to `safety_trend.jsonl` next to `safety_results.json`:

```json
{"timestamp": "2026-04-29T14:33:04Z", "safety_score": 0.94, "safe_ratio": 0.96, "passed": true, "scored_unsafe_count": 2, "unscored_count": 0, "evaluation_completed": true}
{"timestamp": "2026-05-03T09:12:47Z", "safety_score": 0.91, "safe_ratio": 0.93, "passed": true, "scored_unsafe_count": 3, "unscored_count": 1, "evaluation_completed": true}
{"timestamp": "2026-05-10T16:45:02Z", "safety_score": 0.85, "safe_ratio": 0.88, "passed": false, "scored_unsafe_count": 6, "unscored_count": 2, "evaluation_completed": true}
```

Seven fields, one line per run:

| Field | Meaning |
|---|---|
| `timestamp` | UTC ISO-8601 timestamp of the evaluation. |
| `safety_score` | Aggregate score, rounded to 4 decimals. |
| `safe_ratio` | Share of probes judged safe, rounded to 4 decimals. |
| `passed` | Whether the run cleared its configured safety gates. |
| `scored_unsafe_count` | Probes the classifier read **and** judged unsafe. |
| `unscored_count` | Probes the classifier never produced a usable verdict for. |
| `evaluation_completed` | `false` when the run produced no usable evidence about the model. |

The last three are written whenever attribution telemetry is available, which is the normal path (`forgelm/safety/_results.py::_append_trend_entry`). Rows produced by library callers using the older four-argument signature omit them — treat them as absent rather than zero.

There is no per-harm-category (`S5`, `S10`, ...) trend and no benchmark trend — `forgelm/benchmark.py` does not write a trend file at all; only the safety path does.

### Reading `unscored_count`: classifier degradation vs model regression

This is the distinction the three added columns exist to make, and `safety_score` alone cannot express it.

- **`scored_unsafe_count` rising, `unscored_count` flat** — the classifier is reading your model's responses and increasingly disliking them. This is a genuine model regression.
- **`unscored_count` rising, `scored_unsafe_count` flat** — the classifier is failing to return usable verdicts more often (an OOM in the guard, a truncated generation, a changed upstream checkpoint). Your model may be unchanged. A falling `safe_ratio` driven entirely by this reads as "the model is getting less safe" until you put the two columns side by side.
- **`evaluation_completed: false`** — the run produced no usable evidence at all. Do not read `passed: false` on such a row as evidence about the model, and note that the training-time auto-revert is deliberately withheld in this case (see [Auto-Revert](#/evaluation/auto-revert)).

A `jq` view that separates the two signals:

```shell
$ jq -r '"\(.timestamp) unsafe=\(.scored_unsafe_count // "n/a") unscored=\(.unscored_count // "n/a") completed=\(.evaluation_completed // "n/a")"' \
    ./checkpoints/safety/safety_trend.jsonl | tail -20
```

## Computing drift yourself

ForgeLM does not run a regression or significance test on this file for you. A simple, honest way to spot drift with `jq`:

```shell
$ jq -s '
    map(.safety_score) as $s |
    ($s | add / length) as $avg |
    {runs: ($s | length), average: $avg, latest: $s[-1], delta: ($s[-1] - $avg)}
  ' ./checkpoints/safety/safety_trend.jsonl
```

If `delta` is consistently negative across several checks, `safety_score` is trending down — treat it the same way you'd treat a `min_safety_score` regression, even though nothing in ForgeLM will auto-revert on it today. For anything more rigorous (linear fit, p-values, per-category breakdowns), export the JSONL into pandas or a dashboard tool — ForgeLM's job here is producing clean data, not analysing it.

## Configuration

There is nothing to turn on. Trend logging is an unconditional side effect of a safety evaluation — whenever `evaluation.safety.enabled: true` runs (training-time or `forgelm safety-eval`), the trend row is appended automatically:

```yaml
evaluation:
  safety:
    enabled: true
```

There is no `lookback_runs`, `drift_p_threshold`, or `fail_on_concern` knob to set — none of those fields exist on `SafetyConfig` or anywhere else in `ForgeConfig`.

## Where the history file lives

`safety_trend.jsonl` is written next to `safety_results.json`, in the same directory as the rest of the safety-evaluation output:

- Training-time safety gate: `<training.output_dir>/safety/safety_trend.jsonl` (default `./checkpoints/safety/safety_trend.jsonl`).
- Standalone `forgelm safety-eval --output-dir DIR`: `DIR/safety_trend.jsonl`.

Because the default `training.output_dir` is typically per-run (and often gitignored), history only accumulates across runs that share the same output directory. Point multiple runs at the same `training.output_dir`, or run `forgelm safety-eval --output-dir <shared-dir>` against each saved checkpoint after the fact, if you want a long-running trend line instead of one row per run.

## Visualisation

ForgeLM does not ship a `forgelm trend` CLI report today. Cross-run comparison — including safety trend — is scoped as part of the Pro CLI observability dashboard (traction-gated; see the [Phase 13 roadmap on GitHub](https://github.com/HodeTech/ForgeLM/blob/main/docs/roadmap.md)), not a free-tier CLI subcommand. Until it ships, `jq` against the JSONL is the working flow:

```shell
$ jq -r '"\(.timestamp) \(.safety_score)"' ./checkpoints/safety/safety_trend.jsonl | tail -20
```

For dashboards, the JSONL loads directly into Grafana or Datadog:

```shell
$ jq -c '.' ./checkpoints/safety/safety_trend.jsonl > safety-trend.ndjson
```

## Run identification

`safety_trend.jsonl` rows carry no `run_id` and no `config_hash` field to join against. If you need to correlate a trend row with a specific training run, cross-reference the `timestamp` against your own run log (or `audit_log.jsonl`'s `training_started` / `training_completed` events for that run) rather than expecting a built-in join key.

```shell
$ jq -r 'select(.passed == false) | .timestamp' ./checkpoints/safety/safety_trend.jsonl
```

## Common pitfalls

:::warn
**Expecting automatic drift alerts.** Nothing in ForgeLM watches `safety_trend.jsonl` and fails a run because of a multi-run trend — only the current run's `evaluation.safety.max_safety_regression` / `min_safety_score` gates the exit code. Trend analysis is advisory and manual today.
:::

:::warn
**Comparing across different `training.output_dir` values.** If every run writes to a fresh directory, `safety_trend.jsonl` never accumulates more than one row per directory. Reuse the directory (or aggregate multiple `safety_trend.jsonl` files yourself) to get a real trend.
:::

:::tip
**Keep your own run log alongside the trend file.** Since there's no `run_id`/`config_hash` join key, a lightweight external log (spreadsheet, CI artifact, or `audit_log.jsonl`) that maps `timestamp` → config/run is what makes the trend data actionable.
:::

## See also

- [Llama Guard Safety](#/evaluation/safety) — produces the `safety_score` / `safe_ratio` this page tracks.
- [Auto-Revert](#/evaluation/auto-revert) — the per-run gate; trend tracking is advisory, not gating.
- [Benchmark Integration](#/evaluation/benchmarks) — a separate gate with no trend file of its own.
