---
title: Air-Gap Operation
description: Train and evaluate without internet — pre-cache everything, run in isolated networks.
---

# Air-Gap Operation

For regulated industries (defence, healthcare, certain financial sectors) and high-security customer environments, training must happen on networks with no internet access. ForgeLM is designed to operate fully air-gapped: every internet-touching step has an offline equivalent.

## What needs to be online (one-time)

Before going air-gapped, pre-cache:

| Resource | Where to cache |
|---|---|
| Base model | `~/.cache/huggingface/hub/` |
| Tokeniser | (same — bundled with model) |
| Llama Guard | `~/.cache/huggingface/hub/` |
| `lm-evaluation-harness` task definitions | `~/.cache/lm-evaluation-harness/` |
| Python packages (`forgelm` + extras) | local pip wheel cache |

Pre-cache from a connected machine:

```shell
$ forgelm cache-models \
    --model "Qwen/Qwen2.5-7B-Instruct" \
    --safety "meta-llama/Llama-Guard-3-8B" \
    --output ./airgap-bundle/

$ forgelm cache-tasks \
    --tasks hellaswag,arc_easy,truthfulqa,mmlu \
    --output ./airgap-bundle/
```

Transfer `airgap-bundle/` to the offline host (USB, scp, whatever your security policy allows).

## On the air-gapped host

Set environment variables to point at the local cache and disable any network calls:

```shell
$ export HF_HOME="$(pwd)/airgap-bundle/huggingface"
$ export TRANSFORMERS_OFFLINE=1
$ export HF_HUB_OFFLINE=1
$ export HF_DATASETS_OFFLINE=1

$ forgelm --config configs/run.yaml --offline
```

The `--offline` flag tells ForgeLM to refuse any network call. If your config references a model that isn't cached, training fails with a clear error message:

```text
ERROR: model "meta-llama/Llama-Guard-3-8B" not in local cache.
       Pre-cache from a connected machine: forgelm cache-models --safety meta-llama/Llama-Guard-3-8B
```

## What `--offline` enforces

| Subsystem | Behaviour with `--offline` |
|---|---|
| HuggingFace Hub downloads | Disabled. Fail-fast if a model/tokeniser isn't cached. |
| W&B / MLflow / Comet | Disabled (or local-only for MLflow with file URI). |
| OpenAI / Anthropic judges | Disabled. Use a local judge model. |
| Webhooks | Disabled (or restricted to internal hosts via `webhook.allow_private`). |
| pip install (during runtime) | Disabled. |
| Telemetry | None — ForgeLM never phones home regardless. |

## Local synthetic data

If you need synthetic data generation in air-gap, use a local teacher:

```yaml
synthetic:
  enabled: true
  teacher_model: "Qwen/Qwen2.5-72B-Instruct"  # HF Hub id — must be cached
  teacher_backend: "local"                    # not "api" — no network call
```

There is no nested `synthetic.teacher:` sub-block (`provider` / `model` / `load_in_4bit`) — `teacher_model` and `teacher_backend` are flat fields on `synthetic:`. An API-backed teacher (`teacher_backend: "api"`, e.g. OpenAI / Anthropic) fails on `--offline`.

## Local LLM-as-judge

```yaml
evaluation:
  llm_judge:
    enabled: true
    judge_model: "Qwen/Qwen2.5-72B-Instruct"  # plain string — local model path or HF Hub id
```

`judge_model` is a plain string field, not a nested `{provider, model}` object — a local HF path/id with no `judge_api_key_env` set (the default) resolves to a local judge.

A 72B local judge is slower than `gpt-4o-mini` but the quality is comparable for typical use cases. See [LLM-as-Judge](#/evaluation/judge).

## Evaluation in air-gap

`lm-evaluation-harness` typically downloads task definitions and datasets at runtime. Pre-cache them:

```shell
$ forgelm cache-tasks --tasks hellaswag,arc_easy,truthfulqa,mmlu
```

There is no `evaluation.benchmark.tasks_dir` (or any other) YAML field to point at the cache — `forgelm cache-tasks` resolves the same `HF_DATASETS_CACHE` (falling back to `HF_HOME/datasets`) environment variable that `lm-evaluation-harness`'s underlying `datasets` library reads at eval time. Set the environment variables from ["On the air-gapped host"](#on-the-air-gapped-host) above (`HF_HOME`, `HF_DATASETS_OFFLINE=1`) before running `--benchmark-only` and the cache is picked up automatically — no config block needed.

## Verifying air-gap mode

```shell
$ forgelm doctor --offline
forgelm doctor — environment check

  [✓ pass] python.version          Python 3.11.4 (CPython).
  [✓ pass] torch.cuda              torch 2.4.0 with CUDA 12.4.
  [✓ pass] gpu.inventory           1 GPU(s) — GPU0: NVIDIA A100 (80.0 GiB).
  [✓ pass] extras.qlora            Installed (module bitsandbytes, purpose: 4-bit / 8-bit QLoRA training).
  [✓ pass] extras.eval             Installed (module lm_eval, purpose: lm-evaluation-harness benchmark scoring).
  [! warn] extras.tracking         Optional extra missing — install with: pip install 'forgelm[tracking]' (purpose: Weights & Biases experiment tracking).
  [✓ pass] hf_hub.offline_cache    HF cache at /opt/airgap/huggingface/hub: 3.4 GiB across 47 file(s). HF_HUB_OFFLINE=1.
  [✓ pass] disk.workspace          Workspace /opt/airgap — 412.0 GiB free of 500.0 GiB.
  [! warn] operator.identity       FORGELM_OPERATOR not set; audit events will fall back to 'airgap-op@nodeA'. Pin FORGELM_OPERATOR=<id> for CI / pipeline runs.

Summary: 7 pass, 2 warn, 0 fail.
```

If `forgelm doctor --offline` reports anything as `fail`, fix that *before* the air-gapped operator wastes their time. The HF cache scan honours `HF_HUB_CACHE` first, then `HF_HOME/hub`, then the default `~/.cache/huggingface/hub` — point `HF_HUB_CACHE` at your bundled cache to make the scan deterministic.

## Bundle size estimate

For a typical fine-tuning project:

| Resource | Approx size |
|---|---|
| Base model (7B) in safetensors | 14 GB |
| Llama Guard 3 8B | 16 GB |
| Tokeniser + config | <100 MB |
| Eval task definitions + cached datasets | 500 MB - 2 GB |
| Python wheels (forgelm + extras) | 1-2 GB |
| **Total** | **~32-34 GB** |

Plan storage accordingly.

## Common pitfalls

:::warn
**Forgetting to set the environment variables.** Without `HF_HUB_OFFLINE=1`, HuggingFace libraries silently try to phone home and fall back to local cache. The fall-back works fine but you've now made an outbound connection — a compliance violation in some environments.
:::

:::warn
**Using `--offline` without pre-caching.** Training will fail at the first model load. Run `forgelm doctor --offline` before kicking off training to catch missing resources upfront.
:::

:::tip
**Build a CI image with everything pre-cached.** For air-gapped CI, package the pre-cached bundle into a Docker image. Every CI run starts from the cached state. See [Docker](#/operations/docker).
:::

## See also

- [Docker](#/operations/docker) — building images with pre-cached models.
- [Installation](#/getting-started/installation) — base install.
- [LLM-as-Judge](#/evaluation/judge) — local judge models.
