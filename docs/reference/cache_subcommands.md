# `forgelm cache-models` & `forgelm cache-tasks` Reference

> **Mirror:** [cache_subcommands-tr.md](cache_subcommands-tr.md)
>
> The air-gap pre-fetch pair. On a connected machine, populate the local HuggingFace Hub cache (`cache-models`) and the lm-evaluation-harness datasets cache (`cache-tasks`); transfer the resulting trees to an offline host where `forgelm doctor --offline` validates them and the trainer runs with `local_files_only=True`.

## Synopsis

```shell
forgelm cache-models --model HUB_ID [--model HUB_ID ...] [--safety HUB_ID]
                     [--output DIR] [--audit-dir DIR]
                     [--output-format {text,json}] [-q] [--log-level LEVEL]

forgelm cache-tasks  --tasks CSV
                     [--output DIR] [--audit-dir DIR]
                     [--output-format {text,json}] [-q] [--log-level LEVEL]
```

Implementation: [`forgelm/cli/subcommands/_cache.py`](../../forgelm/cli/subcommands/_cache.py).

## `cache-models` flags

| Flag | Type | Default | Description |
|---|---|---|---|
| `--model HUB_ID` | string (repeatable) | — | HuggingFace Hub ID (e.g. `meta-llama/Llama-3.2-3B`) or local path. Repeat for multiple models. |
| `--safety HUB_ID` | string | — | Optional safety classifier to pre-cache (e.g. `meta-llama/Llama-Guard-3-8B`). Appended to the model list internally. |
| `--output DIR` | path | env-resolved | Cache directory override. **Resolution order:** `--output` > `HF_HUB_CACHE` > `HF_HOME/hub` > `~/.cache/huggingface/hub`. A diverging `--output` emits a warning because [`forgelm doctor --offline`](doctor_subcommand.md) and the trainer both read the env-var chain, **not** `--output`. |
| `--audit-dir DIR` | path | `--output` | Where to append `cache.populate_models_*` events. Use this when the operator stages artefacts under a directory different from the audit log. |
| `--output-format` | `text` \| `json` | `text` | Renderer. |
| `-q`, `--quiet` | bool | `false` | Suppress INFO logs. |
| `--log-level` | `DEBUG`/`INFO`/`WARNING`/`ERROR` | `INFO` | Logging verbosity. |

At least one of `--model` or `--safety` is required; both may be combined to stage a base model + Llama Guard in a single invocation.

## `cache-tasks` flags

| Flag | Type | Default | Description |
|---|---|---|---|
| `--tasks CSV` | string (required) | — | Comma-separated lm-eval task names (e.g. `hellaswag,arc_easy,truthfulqa,mmlu`). Whitespace around commas is tolerated. |
| `--output DIR` | path | env-resolved | Cache directory override. **Resolution order:** `--output` > `HF_DATASETS_CACHE` > `HF_HOME/datasets` > `~/.cache/huggingface/datasets`. The runtime stamps `HF_DATASETS_CACHE` to the resolved path inside a `try/finally` so a long-lived process / subsequent test does not see the stamp leak. |
| `--audit-dir DIR` | path | `--output` | Where to append `cache.populate_tasks_*` events. |
| `--output-format` | `text` \| `json` | `text` | Renderer. |
| `-q`, `--quiet` | bool | `false` | Suppress INFO logs. |
| `--log-level` | `DEBUG`/`INFO`/`WARNING`/`ERROR` | `INFO` | Logging verbosity. |

`cache-tasks` requires the `[eval]` extra (`pip install 'forgelm[eval]'`); a missing import surfaces as `EXIT_CONFIG_ERROR` (operator-actionable), not `EXIT_TRAINING_ERROR`.

## Cache-tree layout

The HF cache is partitioned by purpose; setting `HF_HUB_CACHE` does **not** redirect dataset downloads, and vice versa.

| Cache | Resolved by | Populated by | What lives there |
|---|---|---|---|
| **Hub cache** | `HF_HUB_CACHE` > `HF_HOME/hub` > `~/.cache/huggingface/hub` | `cache-models` | Model snapshots, tokenizers, configs (the `huggingface_hub.snapshot_download` blob store). |
| **Datasets cache** | `HF_DATASETS_CACHE` > `HF_HOME/datasets` > `~/.cache/huggingface/datasets` | `cache-tasks` | Parquet shards, processed Arrow splits (the `datasets` library's own cache). |

> `FORGELM_CACHE_DIR` is **not** a ForgeLM env var. Use the canonical HuggingFace ones above.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Every requested model / task cached successfully. |
| `1` | Config error — empty `--model`+`--safety`, malformed model name, empty `--tasks`, unknown lm-eval task name, missing `[eval]` extra. |
| `2` | Runtime error — HF Hub transport failure, disk-full, `huggingface_hub` import broken, one or more task datasets not staged. |

`cache-models` reports a partial-batch failure: the audit chain records `cache.populate_models_failed` with `models_completed=[<list-so-far>]` so the operator knows what *did* land before the crash and can resume by re-running with the failing model omitted.

`cache-tasks` reports one the same way. Any task ending `cached: false` — a download error, or a task for which lm-eval exposes no downloadable dataset — makes the whole command exit `2` with `success: false`, and the failure envelope keeps the per-task rows under `tasks`. **Changed in 0.11:** through 0.10 those failures were recorded in `tasks[].error` while the command still exited 0 and logged `cache.populate_tasks_completed`, so a CI job gating on `jq -e '.success'` packaged an incomplete cache and shipped it to the air-gapped host.

## Audit events emitted

| Event | When emitted | Payload (in addition to envelope) | Article |
|---|---|---|---|
| `cache.populate_models_requested` | `cache-models` invocation begins. | `models`, `cache_dir`, `safety_classifier` | 12 |
| `cache.populate_models_completed` | Every model downloaded successfully. | All `requested` fields + `total_size_bytes`, `count` | 12 |
| `cache.populate_models_failed` | One or more model downloads failed (transport, disk-full, HF auth). | All `requested` fields + `models_completed`, `error_class`, `error_message` | 12 |
| `cache.populate_tasks_requested` | `cache-tasks` invocation begins. | `tasks`, `cache_dir` | 12 |
| `cache.populate_tasks_completed` | Every lm-eval task dataset prepared successfully. | All `requested` fields + `count` | 12 |
| `cache.populate_tasks_failed` | Unknown task name, OR no task staged at all. | All `requested` fields + `tasks_completed`, `error_class`, `error_message` (enumeration failure) or `tasks_cached`, `tasks_failed`, `tasks_unavailable` (nothing staged) | 12 |
| `cache.populate_tasks_partial` | Some tasks staged, some did not. | All `requested` fields + `tasks_cached`, `tasks_failed`, `tasks_unavailable` | 12 |

Audit-logger construction is **best-effort**: an operator without `FORGELM_OPERATOR` set on a connected staging machine sees a debug-level note, and the run continues without the audit chain. The cache subcommands' value is in the on-disk artefacts, not the audit chain. Mirror entries: [`audit_event_catalog.md`](audit_event_catalog.md) §Air-gap pre-cache.

## Examples

### Cache a base model + Llama Guard for offline training

```shell
$ forgelm cache-models \
    --model "Qwen/Qwen2.5-7B-Instruct" \
    --safety "meta-llama/Llama-Guard-3-8B"
Cached 2 model(s); 28415.32 MiB total under /home/me/.cache/huggingface/hub.
  - Qwen/Qwen2.5-7B-Instruct: 14207.66 MiB (412.8s)
  - meta-llama/Llama-Guard-3-8B: 14207.66 MiB (398.2s)
```

### Cache multiple models in a single invocation

```shell
$ forgelm cache-models \
    --model "meta-llama/Llama-3.2-3B" \
    --model "meta-llama/Llama-3.2-3B-Instruct" \
    --safety "meta-llama/Llama-Guard-3-8B" \
    --output ./airgap-bundle/hub
```

The `--output` divergence warning fires unless `HF_HUB_CACHE=$PWD/airgap-bundle/hub` is also set. Resolve by either dropping `--output` or pinning the env var to match the bundle path.

### Cache lm-eval tasks

```shell
$ forgelm cache-tasks --tasks "hellaswag,arc_easy,truthfulqa,mmlu"
Cached 4 of 4 task(s) under /home/me/.cache/huggingface/datasets.
  - hellaswag: ok
  - arc_easy: ok
  - truthfulqa: ok
  - mmlu: ok
```

### CI bundle staging (JSON envelope)

```shell
$ forgelm cache-models \
    --model "Qwen/Qwen2.5-7B-Instruct" \
    --safety "meta-llama/Llama-Guard-3-8B" \
    --output-format json -q \
  | jq '.success'
true
```

```json
{
  "success": true,
  "models": [
    {"name": "Qwen/Qwen2.5-7B-Instruct", "cached_path": "...", "size_bytes": 14897516032, "size_mb": 14207.66, "duration_s": 412.8},
    {"name": "meta-llama/Llama-Guard-3-8B", "cached_path": "...", "size_bytes": 14897516032, "size_mb": 14207.66, "duration_s": 398.2}
  ],
  "total_size_mb": 28415.32,
  "cache_dir": "/home/me/.cache/huggingface/hub"
}
```

## See also

- [Air-gap deployment guide](../guides/air_gap_deployment.md) — full operator cookbook for the connected-machine → bundle → air-gapped-host workflow.
- [`doctor_subcommand.md`](doctor_subcommand.md) — `forgelm doctor --offline` validates the populated cache.
- [`audit_event_catalog.md`](audit_event_catalog.md) — full audit-event catalog.
- [Air-gap manual page](../usermanuals/en/operations/air-gap.md) — operator-facing summary.
- [JSON output schema](../usermanuals/en/reference/json-output.md) — locked envelope contract.
