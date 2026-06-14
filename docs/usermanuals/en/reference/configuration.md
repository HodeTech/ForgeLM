---
title: Configuration Reference
description: Every YAML field ForgeLM understands, with types, defaults, and notes.
---

# Configuration Reference

This is the canonical reference for every YAML field ForgeLM accepts. The schema is enforced by Pydantic; running `forgelm --config X.yaml --dry-run` validates your file against it.

The top-level config has 13 blocks:

```yaml
model:           {...}
lora:            {...}
galore:          {...}
datasets:        [...]
training:        {...}
evaluation:      {...}
synthetic:       {...}
merge:           {...}
distributed:     {...}
compliance:      {...}
output:          {...}
auth:            {...}
deployment:      {...}
```

## `model:`

```yaml
model:
  name_or_path: "Qwen/Qwen2.5-7B-Instruct"   # HF id or local path (required)
  trust_remote_code: false                    # only set true if you trust the model's repo
  max_length: 4096                            # context for training
  load_in_4bit: false                         # QLoRA toggle
  load_in_8bit: false
  bnb_4bit_quant_type: "nf4"                  # nf4 | fp4
  bnb_4bit_compute_dtype: "bfloat16"          # bfloat16 | float16 | float32
  use_unsloth: false                          # 2-5× speedup on supported models
  attention_implementation: "auto"            # auto | flash_attention_2 | sdpa | eager
  rope_scaling:                               # see [Long-Context](#/training/long-context)
    type: "linear"                            # linear | dynamic | yarn | longrope
    factor: 4.0
  sliding_window: null                        # int — sliding window for long context
  torch_dtype: "auto"                         # auto-pick based on quantisation
```

## `lora:`

```yaml
lora:
  r: 16                                       # rank — see [LoRA](#/training/lora)
  alpha: 32
  dropout: 0.05
  target_modules: ["q_proj", "k_proj", "v_proj", "o_proj"]
  modules_to_save: []                         # full-precision modules (e.g. embeddings)
  use_dora: false                             # DoRA variant
  use_pissa: false                            # PiSSA initialisation
  use_rslora: false                           # rsLoRA scaling
```

## `galore:` (alternative to `lora:`)

```yaml
galore:
  enabled: false                              # incompatible with lora.r when true
  rank: 256
  update_proj_gap: 200
  scale: 0.25
  proj_type: "std"                            # std | reverse_std | right | left
  target_modules: ["attn", "mlp"]
```

## `datasets:`

```yaml
datasets:
  - path: "data/train.jsonl"                  # required
    format: "messages"                        # auto-detected if omitted
    weight: 1.0                               # mixing weight; sum across all datasets
    split: "train"                            # train | val | test
    streaming: false
```

Multiple datasets allowed. Format options: `instructions`, `messages`, `preference`, `binary`, `reward`. See [Dataset Formats](#/concepts/data-formats).

## `training:`

```yaml
training:
  trainer: "sft"                              # sft | dpo | simpo | kto | orpo | grpo
  epochs: 3
  max_steps: -1                               # -1 means use epochs
  batch_size: 4
  gradient_accumulation_steps: 1
  learning_rate: 2.0e-4
  scheduler: "cosine"                         # cosine | linear | constant | warmup_cosine
  warmup_ratio: 0.03
  weight_decay: 0.0
  optimizer: "adamw_8bit"                     # adamw | adamw_8bit | galore_adamw_8bit
  seed: 42
  packing: false
  neftune_noise_alpha: null                   # float — embedding noise regularisation
  loss_on_completions_only: true              # SFT-specific
  log_grad_norm: false
  report_to: ["tensorboard"]                  # see [Experiment Tracking](#/operations/experiment-tracking)
  run_name: null                              # auto-generated from config hash
  tags: []
  notes: null

  # Trainer-specific blocks
  dpo: { beta: 0.1, loss_type: "sigmoid", reference_free: false }
  simpo: { beta: 2.0, gamma: 1.0, length_normalize: true }
  kto: { beta: 0.1, desirable_weight: 1.0, undesirable_weight: 1.0 }
  orpo: { beta: 0.1, sft_weight: 1.0 }
  grpo:
    group_size: 8
    beta: 0.04
    reward_function: "my_module.score"
    format_reward: 0.2
    answer_pattern: null
    temperature: 0.9
```

## `evaluation:`

```yaml
evaluation:
  enabled: true
  max_length: null                            # null = same as training
  benchmark:
    enabled: false
    tasks: []
    floors: {}
    num_fewshot: 0
    batch_size: 8
    limit: null
  safety:
    enabled: false
    model: "meta-llama/Llama-Guard-3-8B"
    block_categories: []
    test_prompts: null                        # null = built-in default probes
    severity_threshold: "medium"              # low | medium | high | critical
    regression_tolerance: 0.05
    baseline: null
  judge:
    enabled: false
    mode: "pairwise"                          # pairwise | single-rubric | elo
    judge_model:
      provider: "openai"
      model: "gpt-4o-mini"
    baseline_model: null
    test_prompts: null
    num_samples: 200
    rubric: "default"
    self_consistency: 1
    swap_positions: true
    budget_usd: null
  trend:
    enabled: false
    history_file: ".forgelm/eval-history.jsonl"
    lookback_runs: 10
    drift_p_threshold: 0.05
    fail_on_concern: "high"
  auto_revert:
    enabled: false
    last_good_checkpoint: null
    notify_on_revert: true
    keep_failed_checkpoint: true
  guards: {}                                  # custom callable guards
```

## `synthetic:`

```yaml
synthetic:
  enabled: false
  teacher:
    provider: "openai"                        # openai | anthropic | local | vllm
    model: "gpt-4o"
    api_key: "${OPENAI_API_KEY}"
  seed_prompts: "data/seeds.jsonl"
  output: "data/synthetic.jsonl"
  num_samples: 1000
  temperature: 0.7
  prompt_template: "default"
  budget_usd: null
  rate_limit:
    requests_per_minute: 100
    burst: 10
```

## `merge:`

```yaml
merge:
  enabled: false
  algorithm: "ties"                           # linear | slerp | ties | dare | dare_ties
  base_model: null                            # required when enabled
  models:
    - path: "./checkpoints/v1"
      weight: 0.5
  parameters:
    threshold: 0.7                            # TIES
    density: 0.7                              # DARE
    t: 0.5                                    # SLERP
  output:
    dir: "./checkpoints/merged"
    model_card: true
```

## `distributed:`

```yaml
distributed:
  strategy: "single"                          # single | deepspeed | fsdp
  zero_stage: null                            # 2 | 3 (when strategy=deepspeed)
  cpu_offload: false
  nvme_offload_path: null
  fsdp_state_dict_type: "FULL_STATE_DICT"
  fsdp_auto_wrap_policy: "TRANSFORMER_BASED_WRAP"
  fsdp_offload_params: false
  gradient_accumulation_steps: 1
```

## `compliance:`

```yaml
compliance:
  annex_iv: false
  data_audit_artifact: null
  human_approval: false
  intended_purpose: null                      # required if annex_iv: true
  risk_classification: null                   # required if annex_iv: true
  deployment_geographies: []
  responsible_party: null
  version: null
  standards: []
  notes: null
  risk_assessment:
    foreseeable_misuse: []
    mitigations: []
    residual_risks: []
  data_protection:
    framework: null                           # GDPR | KVKK | both
    lawful_basis: null
    purpose: null
    data_controller: null
    international_transfers:
      enabled: false
      safeguards: null
  audit_log:
    enabled: false
    path: "${output.dir}/artifacts/audit_log.jsonl"
    forward_to: []
  approval:
    request_webhook: null
    signature_method: "cli"
    timeout_hours: 48
    require_role: null
    quorum: 1
  post_market_plan: null
  license: "Apache-2.0"
```

## `output:`

```yaml
output:
  dir: "./checkpoints/run"                    # required
  model_card: true
  save_format: "safetensors"                  # safetensors | pytorch
  save_strategy: "epoch"                      # epoch | steps | no
  save_steps: 500
  webhook:                                    # see [Webhooks](#/operations/webhooks)
    url: null
    template: "slack"
    events: []
  # cost_tracking:                             # roadmap — not yet implemented; see GPU Cost Estimation page + risks-and-decisions.md
  #   enabled: false                           # NOT honoured by forgelm/config.py (no output.cost_tracking surface)
  #   rate_per_hour: {}
  #   currency: "USD"
  #   alert_threshold_usd: null
  #   halt_threshold_usd: null
  gguf:
    enabled: false
    quant_levels: ["q4_k_m"]
    output_dir: "${output.dir}/gguf/"
    manifest: true
```

## `auth:`

```yaml
auth:
  hf_token: null                              # ${HF_TOKEN} via env (preferred)
  openai_api_key: null
  anthropic_api_key: null
```

## `deployment:`

There is no `deployment:` top-level YAML key — `ForgeConfig` rejects unknown keys (`extra="forbid"`), so adding one to your training config raises `ConfigError` at load time. Deployment knobs are exposed as `forgelm deploy` CLI flags instead. The live target choices are `--target {ollama,vllm,tgi,hf-endpoints}`; see the [Deploy targets page](#/deployment/deploy-targets) and the [CLI reference](#/reference/cli) for the full surface.

> **Planned for v0.6.0+:** A YAML-backed `deployment:` section is on the [Phase 14 pipeline-chains roadmap on GitHub](https://github.com/HodeTech/ForgeLM/blob/main/docs/roadmap.md) (deferred from earlier v0.5.x placeholders). Until then, treat any "deployment:" YAML you find in third-party templates as informational; only the `forgelm deploy` flags are authoritative.

## See also

- [CLI Reference](#/reference/cli) — flags that complement YAML fields.
- [YAML Templates](#/reference/yaml-templates) — full working examples.
- [Configuration Overview (concepts)](#/concepts/alignment-overview) — what these fields mean conceptually.
