---
title: Konfigürasyon Referansı
description: ForgeLM'in anladığı her YAML alanı — tipler, varsayılanlar, notlar.
---

# Konfigürasyon Referansı

Bu, ForgeLM'in kabul ettiği her YAML alanının kanonik referansıdır. Şema Pydantic ile zorlanır; `forgelm --config X.yaml --dry-run` dosyanızı şemaya karşı doğrular.

Üst seviye config 13 bloktan oluşur:

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
  name_or_path: "Qwen/Qwen2.5-7B-Instruct"   # HF id veya yerel yol (gerekli)
  trust_remote_code: false                    # sadece güveniyorsanız true
  max_length: 4096                            # eğitim context'i
  load_in_4bit: false                         # QLoRA toggle
  load_in_8bit: false
  bnb_4bit_quant_type: "nf4"                  # nf4 | fp4
  bnb_4bit_compute_dtype: "bfloat16"
  use_unsloth: false                          # desteklenen modellerde 2-5× hız
  attention_implementation: "auto"            # auto | flash_attention_2 | sdpa | eager
  rope_scaling:
    type: "linear"                            # linear | dynamic | yarn | longrope
    factor: 4.0
  sliding_window: null
  torch_dtype: "auto"
```

## `lora:`

```yaml
lora:
  r: 16
  alpha: 32
  dropout: 0.05
  target_modules: ["q_proj", "k_proj", "v_proj", "o_proj"]
  modules_to_save: []
  use_dora: false
  use_pissa: false
  use_rslora: false
```

## `galore:` (lora alternatifi)

```yaml
galore:
  enabled: false                              # lora.r ile çakışır
  rank: 256
  update_proj_gap: 200
  scale: 0.25
  proj_type: "std"
  target_modules: ["attn", "mlp"]
```

## `datasets:`

```yaml
datasets:
  - path: "data/train.jsonl"                  # gerekli
    format: "messages"                        # belirtilmezse otomatik algılanır
    weight: 1.0
    split: "train"                            # train | val | test
    streaming: false
```

Format seçenekleri: `instructions`, `messages`, `preference`, `binary`, `reward`. Bkz. [Dataset Formatları](#/concepts/data-formats).

## `training:`

```yaml
training:
  trainer: "sft"                              # sft | dpo | simpo | kto | orpo | grpo
  epochs: 3
  max_steps: -1
  batch_size: 4
  gradient_accumulation_steps: 1
  learning_rate: 2.0e-4
  scheduler: "cosine"
  warmup_ratio: 0.03
  weight_decay: 0.0
  optimizer: "adamw_8bit"
  seed: 42
  packing: false
  neftune_noise_alpha: null
  loss_on_completions_only: true
  log_grad_norm: false
  report_to: ["tensorboard"]
  run_name: null
  tags: []
  notes: null

  # Trainer-özgü bloklar
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
  max_length: null
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
    test_prompts: null
    severity_threshold: "medium"
    regression_tolerance: 0.05
    baseline: null
  judge:
    enabled: false
    mode: "pairwise"
    judge_model: { provider: "openai", model: "gpt-4o-mini" }
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
  guards: {}
```

## `synthetic:`

```yaml
synthetic:
  enabled: false
  teacher: { provider: "openai", model: "gpt-4o", api_key: "${OPENAI_API_KEY}" }
  seed_prompts: "data/seeds.jsonl"
  output: "data/synthetic.jsonl"
  num_samples: 1000
  temperature: 0.7
  prompt_template: "default"
  budget_usd: null
  rate_limit: { requests_per_minute: 100, burst: 10 }
```

## `merge:`

```yaml
merge:
  enabled: false
  algorithm: "ties"                           # linear | slerp | ties | dare | dare_ties
  base_model: null
  models: [{ path: "./checkpoints/v1", weight: 0.5 }]
  parameters: { threshold: 0.7, density: 0.7, t: 0.5 }
  output: { dir: "./checkpoints/merged", model_card: true }
```

## `distributed:`

```yaml
distributed:
  strategy: "single"                          # single | deepspeed | fsdp
  zero_stage: null                            # 2 | 3
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
  intended_purpose: null                      # annex_iv: true ise gerekli
  risk_classification: null
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
    international_transfers: { enabled: false, safeguards: null }
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
  dir: "./checkpoints/run"                    # gerekli
  model_card: true
  save_format: "safetensors"
  save_strategy: "epoch"
  save_steps: 500
  webhook:
    url: null
    template: "slack"
    events: []
  # cost_tracking:                             # v0.6.x'e planlandı — bkz. GPU Maliyet Tahmini sayfası + risks-and-decisions.md
  #   enabled: false                           # v0.5.5'te forgelm/config.py tarafından honure edilmez
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
  hf_token: null                              # ${HF_TOKEN} env tercihli
  openai_api_key: null
  anthropic_api_key: null
```

## `deployment:`

v0.5.5'te `deployment:` üst-seviye YAML anahtarı yoktur — `ForgeConfig` bilinmeyen anahtarları reddeder (`extra="forbid"`), dolayısıyla eğitim config'inize eklerseniz yükleme anında `ConfigError` fırlar. Deployment knob'ları YAML yerine `forgelm deploy` CLI bayrakları olarak açılır. Canlı target seçenekleri `--target {ollama,vllm,tgi,hf-endpoints}`'dir; tam surface için [Deploy hedefleri sayfasına](#/deployment/deploy-targets) ve [CLI referansına](#/reference/cli) bakın.

> **v0.6.0+ için planlanan:** YAML-destekli `deployment:` bölümü [GitHub'daki Phase 14 pipeline-chains yol haritasında](https://github.com/HodeTech/ForgeLM/blob/main/docs/roadmap.md) (eski v0.5.x placeholder'larından ertelendi). O zamana kadar, üçüncü taraf şablonlarda gördüğünüz herhangi bir "deployment:" YAML'ını bilgilendirici sayın; otoriter olan yalnızca `forgelm deploy` bayraklarıdır.

## Bkz.

- [CLI Referansı](#/reference/cli) — YAML alanlarını tamamlayan bayraklar.
- [YAML Şablonları](#/reference/yaml-templates) — tam çalışan örnekler.
