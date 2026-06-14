---
title: YAML Templates
description: Full working configurations for common workflows — copy, adapt, run.
---

# YAML Templates

ForgeLM ships **5 first-class quickstart templates** under
`forgelm/templates/<name>/config.yaml`. Each one is a complete, dry-run-validated
config you can adapt instead of writing one from scratch. Materialise any of
them with:

```shell
$ forgelm quickstart <template-name> --output config.yaml
$ forgelm --config config.yaml --dry-run
$ forgelm --config config.yaml
```

The five templates and the user journey each one optimises for:

| Template | Trainer | Workflow |
|---|---|---|
| `customer-support` | SFT | Multi-turn helpful + safe assistant — the most common production pattern. |
| `code-assistant` | SFT | Code-completion / pair-programming model. |
| `domain-expert` | SFT | BYOD (Bring Your Own Dataset) on a domain-specific corpus. |
| `medical-qa-tr` | SFT | Turkish-language medical QA (regulated domain — ships with `risk_assessment` + safety eval pre-wired). |
| `grpo-math` | GRPO | Reward-model fine-tuning on math benchmarks (built-in format/length reward shaping). |

The canonical template content is **the source of truth** — it is what
`forgelm quickstart` materialises and what CI dry-runs against the live
Pydantic schema. The worked examples below are abridged copies for quick
visual reference; if anything in this page disagrees with
`forgelm/templates/<name>/config.yaml`, the template wins.

## Customer-support assistant (SFT)

```yaml
model:
  name_or_path: "Qwen/Qwen2.5-7B-Instruct"
  max_length: 2048
  backend: "transformers"
  trust_remote_code: false

lora:
  r: 8
  alpha: 16
  dropout: 0.05
  bias: "none"
  target_modules: ["q_proj", "v_proj"]
  task_type: "CAUSAL_LM"

training:
  output_dir: "./checkpoints/customer-support"
  final_model_dir: "final_model"
  trainer_type: "sft"
  num_train_epochs: 3
  per_device_train_batch_size: 1
  gradient_accumulation_steps: 8
  learning_rate: 2.0e-5
  warmup_ratio: 0.1
  weight_decay: 0.01
  eval_steps: 50
  save_steps: 50
  save_total_limit: 2

data:
  dataset_name_or_path: "data/customer-support.jsonl"
  shuffle: true
  clean_text: true
```

## SFT → DPO chain (preference alignment)

The SFT step above produces `./checkpoints/customer-support/final_model/`,
which then feeds DPO on a separately-collected preference dataset:

```yaml
model:
  name_or_path: "./checkpoints/customer-support/final_model"
  max_length: 4096

lora:
  r: 16
  alpha: 32
  method: "lora"
  target_modules: ["q_proj", "k_proj", "v_proj", "o_proj"]

data:
  dataset_name_or_path: "data/customer-support-prefs.jsonl"

training:
  output_dir: "./checkpoints/customer-support-v1.2"
  trainer_type: "dpo"
  num_train_epochs: 1
  per_device_train_batch_size: 2
  gradient_accumulation_steps: 8
  learning_rate: 5.0e-6
  warmup_ratio: 0.05
  packing: true
  dpo_beta: 0.1                                # flat field — KL strength

evaluation:
  require_human_approval: true                 # Article 14 oversight gate
  auto_revert: true                            # rolls back on regression
  benchmark:
    enabled: true
    tasks: ["hellaswag", "arc_easy", "truthfulqa_mc1"]
    min_score: 0.45                            # single floor across averaged tasks
  safety:
    enabled: true
    classifier: "meta-llama/Llama-Guard-3-8B"
    track_categories: true
    severity_thresholds:
      S1: 0.05
      S2: 0.05
      S5: 0.10
      S10: 0.05

compliance:
  provider_name: "Acme Corp"
  provider_contact: "compliance@acme.example"
  system_name: "customer-support-v1.2"
  intended_purpose: "Customer-support assistant for a Turkish telecom"
  risk_classification: "high-risk"
  system_version: "1.2.0"

webhook:
  url_env: "SLACK_WEBHOOK"
  notify_on_start: true
  notify_on_success: true
  notify_on_failure: true
```

The bundled Annex IV bundle (`compliance/`) is generated automatically when the
`compliance:` block is present and `risk_classification` is `high-risk` or
`unacceptable`. The five wire-format webhook events
(`training.{start,success,failure,reverted}`, `approval.required`) fire
without a separate event-subscription list — the three `notify_on_*` flags are
the entire surface (see [Webhooks](#/operations/webhooks)).

## GRPO — math reasoning

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
  dataset_name_or_path: "data/math-prompts.jsonl"

training:
  output_dir: "./checkpoints/grpo-math"
  trainer_type: "grpo"
  num_train_epochs: 1
  per_device_train_batch_size: 1
  gradient_accumulation_steps: 16
  learning_rate: 1.0e-6
  grpo_num_generations: 8                # samples per prompt
  grpo_max_completion_length: 512        # cap per generation
  grpo_reward_model: "my_reward.score"   # optional — falls back to built-in format/length shaping
```

`forgelm/grpo_rewards.py` ships an always-on format/length reward fallback;
`grpo_reward_model` is opt-in for domain-specific scorers.

## Field reference

The canonical schema lives in [`forgelm/config.py`](https://github.com/HodeTech/ForgeLM/blob/main/forgelm/config.py)
under the Pydantic models `ForgeConfig`, `ModelConfig`, `LoraConfig`,
`TrainingConfig`, `DataConfig`, `EvaluationConfig`, `BenchmarkConfig`,
`SafetyConfig`, `JudgeConfig`, `ComplianceMetadataConfig`,
`WebhookConfig`, `RetentionConfig`. Every field carries a docstring that
the [Configuration Reference](#/reference/configuration) page surfaces.

## Removed templates / fields

Earlier drafts of this page advertised templates and fields that never
shipped or have been renamed. Use the canonical names instead:

| Old (won't validate) | New |
|---|---|
| `model.use_unsloth: true` | `model.backend: "unsloth"` |
| `model.load_in_4bit: true` | (removed — use `lora.method: "qlora"` for the QLoRA path) |
| `training.trainer: "..."` | `training.trainer_type: "..."` |
| `training.epochs: N` | `training.num_train_epochs: N` |
| `training.batch_size: N` | `training.per_device_train_batch_size: N` |
| `training.scheduler: "cosine"` | (HF-side default; not surfaced as a flat field) |
| `training.{dpo,simpo,kto,orpo,grpo}: { ... }` (nested) | flat `training.{dpo_beta, simpo_beta, kto_beta, ...}` |
| Top-level `datasets: [{path, format}]` array | `data: { dataset_name_or_path: ... }` (singular) |
| Top-level `output: { dir, gguf, webhook, ... }` | `training.output_dir: ...` (scalar) + separate top-level `webhook:` block |
| `compliance.human_approval: true` | `evaluation.require_human_approval: true` |
| `compliance.annex_iv: true` | (auto-generated when `compliance:` block is present + risk tier triggers it) |
| `compliance.{deployment_geographies, responsible_party, version, standards, notes, data_protection, audit_log, approval, post_market_plan, license}` | (not in `ComplianceMetadataConfig` — only the seven canonical fields) |
| `evaluation.benchmark.floors: {per-task dict}` | `evaluation.benchmark.min_score: scalar` (single averaged floor) |
| `evaluation.auto_revert: { enabled: true }` | `evaluation.auto_revert: true` (boolean) |
| `safety.model: "..."` | `safety.classifier: "..."` |
| `safety.block_categories: [list]` | `safety.track_categories: true` + `safety.severity_thresholds: {dict}` |

## See also

- [Configuration Reference](#/reference/configuration) — every field with type and default.
- [Quickstart Templates](#/getting-started/first-run) — operator walkthrough of `forgelm quickstart`.
- [Training paradigms](#/training/sft) — per-trainer YAML examples in canonical schema.
