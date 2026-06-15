---
title: Konfigürasyon Referansı
description: ForgeLM'in anladığı her YAML alanı — tipler, varsayılanlar, notlar.
---

# Konfigürasyon Referansı

Bu, ForgeLM'in kabul ettiği her YAML alanının kanonik referansıdır. Şema Pydantic ile zorlanır; `forgelm --config X.yaml --dry-run` dosyanızı şemaya karşı doğrular.

Üst seviye config 15 bloktan oluşur:

```yaml
model:           {...}
lora:            {...}
training:        {...}
data:            {...}
auth:            {...}
evaluation:      {...}
webhook:         {...}
distributed:     {...}
merge:           {...}
compliance:      {...}
risk_assessment: {...}
monitoring:      {...}
synthetic:       {...}
retention:       {...}
pipeline:        {...}
```

> **Not:** `galore` alanları ayrı bir üst-seviye blok değil, `training:` içinde düz alt-alanlardır (`galore_*` önekiyle). Aşağıdaki [`training:`](#training) bölümüne bakın.

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

## `data:`

```yaml
data:
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
    min_score: null  # görevler arası ortalama skalar taban (kaldırılan per-task floors dict'in yerine)
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
  auto_revert: false  # boolean; EU AI Act yüksek-risk regresyon kapısını etkinleştirmek için true yapın
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

## `webhook:`

```yaml
webhook:
  url: null                                   # Slack / Teams / Discord / özel; url_env tercihli
  url_env: null                               # webhook URL'sini taşıyan env değişkeni
  notify_on_start: true
  notify_on_success: true
  notify_on_failure: true
  timeout: 10                                 # HTTP istek zaman aşımı (saniye)
  allow_private_destinations: false           # küme-içi endpoint'ler için SSRF opt-in
  require_https: false                        # true olduğunda düz http:// URL'leri reddeder
  tls_ca_bundle: null                         # özel CA paketi yolu (kurumsal MITM)
```

## `risk_assessment:`

```yaml
risk_assessment:
  intended_use: ""                            # Madde 9(2)(a): amaçlanan kullanım (serbest metin)
  foreseeable_misuse: []                      # Madde 9(2)(b): öngörülebilir kötüye kullanım senaryoları
  risk_category: "minimal-risk"              # unknown | minimal-risk | limited-risk | high-risk | unacceptable
  mitigation_measures: []                    # Madde 9(2)(c): azaltım adımları
  vulnerable_groups_considered: false        # Madde 9(2)(b): kırılgan gruplar değerlendirildi mi
```

## `monitoring:`

```yaml
monitoring:
  enabled: false                              # Madde 12 pazar-sonrası izlemeyi etkinleştir
  endpoint: ""                               # İzleme webhook URL'si (Prometheus / Datadog / özel)
  endpoint_env: null                          # endpoint'i geçersiz kılan env değişkeni
  metrics_export: "none"                     # none | prometheus | datadog | custom_webhook
  alert_on_drift: true                       # drift tespitinde webhook uyarısı
  check_interval_hours: 24                   # izleme periyodu (saat)
```

## `retention:`

```yaml
retention:
  audit_log_retention_days: 1825             # varsayılan 5 yıl (Madde 5(1)(e))
  staging_ttl_days: 7                        # forgelm reject sonrası staging modelini saklama süresi
  ephemeral_artefact_retention_days: 90      # uyumluluk paketleri, denetim raporları
  raw_documents_retention_days: 90           # ingest edilen PDF/DOCX/EPUB/TXT/Markdown
  enforce: "log_only"                        # log_only | warn_on_excess | block_on_excess
```

## `pipeline:`

```yaml
pipeline:
  output_dir: "./pipeline_run"               # pipeline-seviyesi çıktı dizini
  stages:                                     # sıralı eğitim aşamaları listesi (min 1)
    - name: "sft"
      training: { trainer: "sft", epochs: 3 }
    - name: "dpo"
      training: { trainer: "dpo", epochs: 1 }
```

## `auth:`

```yaml
auth:
  hf_token: null                              # ${HF_TOKEN} env tercihli
  openai_api_key: null
  anthropic_api_key: null
```

## `deployment:`

`deployment:` üst-seviye YAML anahtarı yoktur — `ForgeConfig` bilinmeyen anahtarları reddeder (`extra="forbid"`), dolayısıyla eğitim config'inize eklerseniz yükleme anında `ConfigError` fırlar. Deployment knob'ları YAML yerine `forgelm deploy` CLI bayrakları olarak açılır. Canlı target seçenekleri `--target {ollama,vllm,tgi,hf-endpoints}`'dir; tam surface için [Deploy hedefleri sayfasına](#/deployment/deploy-targets) ve [CLI referansına](#/reference/cli) bakın.

> **Henüz planlanmadı:** YAML-destekli `deployment:` bölümü v0.7.0 sonrasına ertelenmiştir. O zamana kadar, üçüncü taraf şablonlarda gördüğünüz herhangi bir "deployment:" YAML'ını bilgilendirici sayın; otoriter olan yalnızca `forgelm deploy` bayraklarıdır.

## Bkz.

- [CLI Referansı](#/reference/cli) — YAML alanlarını tamamlayan bayraklar.
- [YAML Şablonları](#/reference/yaml-templates) — tam çalışan örnekler.
