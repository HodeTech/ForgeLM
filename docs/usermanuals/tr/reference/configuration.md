---
title: Konfigürasyon Referansı
description: ForgeLM'in anladığı her YAML alanı — tipler, varsayılanlar, notlar.
---

# Konfigürasyon Referansı

Bu, ForgeLM'in kabul ettiği her YAML alanının kanonik referansıdır. Şema Pydantic ile zorlanır; `forgelm --config X.yaml --dry-run` dosyanızı şemaya karşı doğrular.

Üst seviye config 15 bloktan oluşur:

```yaml
# INVALID: yalnızca yapı genel görünümü — her bloğun gerçek alanları aşağıda
# belgelenmiştir; {...} yer tutucuları çalıştırılabilir bir config değildir.
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
  load_in_4bit: false                         # QLoRA toggle (yalnızca NF4/FP4 — ayrı bir 8-bit toggle'ı yok)
  backend: "transformers"                     # transformers | unsloth (yalnızca Linux + CUDA, 2-5× hız)
  bnb_4bit_quant_type: "nf4"                  # nf4 | fp4
  bnb_4bit_compute_dtype: "bfloat16"          # auto | bfloat16 | float16 | float32 (bf16/fp16/fp32 takma adları kabul edilir)
  bnb_4bit_use_double_quant: true             # bitsandbytes double-quantisation (küçük ekstra VRAM kazancı)
  offline: false                              # air-gapped mod: HF Hub ağ çağrılarını reddet
```

`ModelConfig`'te `load_in_8bit`, `use_unsloth`, `attention_implementation` veya `torch_dtype` alanı yoktur — `extra="forbid"` dördünü de `--dry-run`'da reddeder. Ayrı bir 8-bit toggle'ı yoktur (`load_in_4bit` tek quantisation anahtarıdır); Unsloth backend'i bir boolean bayrak değil `backend: "unsloth"` ile seçilir; ForgeLM'de attention-implementation seçici yoktur; ve compute dtype ayrı bir `torch_dtype` alanı değil `bnb_4bit_compute_dtype` ile ayarlanır. `rope_scaling` ve sliding-window override'ı `ModelConfig` değil `TrainingConfig` alanlarıdır (`training.rope_scaling`, `training.sliding_window_attention`) — kavram için aşağıdaki [`training:`](#training) bölümüne ve [Uzun Context Fine-Tuning](#/training/long-context) sayfasına bakın.

## `lora:`

```yaml
lora:
  r: 16
  alpha: 32
  dropout: 0.05
  bias: "none"                                # none | all | lora_only
  method: "lora"                              # lora | dora | pissa | rslora
  target_modules: ["q_proj", "k_proj", "v_proj", "o_proj"]
  use_dora: false                             # method: "dora" için deprecated boolean kısayolu; v0.10.0'da kaldırılır
  use_rslora: false                           # method: "rslora" için deprecated boolean kısayolu; v0.10.0'da kaldırılır
```

`LoraConfigModel`'de `modules_to_save` veya `use_pissa` alanı yoktur — `extra="forbid"` ikisini de `--dry-run`'da reddeder. PiSSA initialisation bir boolean toggle değil `method: "pissa"` ile seçilir; `use_dora` / `use_rslora`, `method: "dora"` / `method: "rslora"` için deprecated boolean kısayollardır, v0.10.0'da kaldırılması planlanmıştır — ikisini birden ayarlamak, veya birini çelişen açık bir `method:` ile ayarlamak config hatasıdır.

## `data:`

```yaml
data:
  dataset_name_or_path: "data/train.jsonl"    # HF Hub id, yerel JSONL yolu veya JSONL dizini (gerekli)
  extra_datasets:                             # primary'ye ek olarak karıştırılacak datasetler
    - "org/extra_dataset"
  mix_ratio: [0.8, 0.2]                       # dataset başına bir ağırlık (primary + extras); belirtilmezse eşit
  shuffle: true                               # train/validation ayrımından önce birleşik korpusu karıştır
  clean_text: true                            # fazladan boşluk + kontrol karakterlerini temizle
  add_eos: true                               # EOS token ekle (üretimin nerede duracağını bilmesi için)
  governance:                                 # Madde 10 veri-yönetişimi metadata'sı (opsiyonel)
    collection_method: ""
    annotation_process: ""
    known_biases: ""
    personal_data_included: false
    dpia_completed: false
```

`data:` bir **liste değil, tek bir objedir** — tam olarak bir primary dataset vardır (`dataset_name_or_path`), ek datasetler `extra_datasets` + `mix_ratio` ile karıştırılır (dataset başına bir ağırlık, önce primary). Format dosya başına otomatik algılanır; desteklenen şekiller `instructions`, `messages`, `preference`, `binary`, `reward` — bkz. [Dataset Formatları](#/concepts/data-formats).

## `training:`

```yaml
training:
  output_dir: "./checkpoints"                 # checkpoint + audit log + uyumluluk paketi burada
  final_model_dir: "final_model"              # output_dir altında nihai modelin alt dizini
  merge_adapters: false                       # SFT bittiğinde LoRA adaptörlerini base modele merge et
  trainer_type: "sft"                         # sft | dpo | simpo | kto | orpo | grpo
  max_steps: -1                               # -1 = num_train_epochs kullan; pozitif değer epoch'ları geçersiz kılar
  num_train_epochs: 3
  per_device_train_batch_size: 4
  gradient_accumulation_steps: 2
  learning_rate: 2.0e-5
  warmup_ratio: 0.1
  weight_decay: 0.01
  eval_steps: 200
  save_steps: 200
  save_total_limit: 3
  early_stopping_patience: 3                  # validation-loss iyileşmesi olmayan N eval sonrası dur
  packing: false                              # sequence packing (SFT)
  rope_scaling: null                          # dict — long-context RoPE scaling, örn. {type: "yarn", factor: 4.0}; bkz. [Uzun Context](#/training/long-context)
  sliding_window_attention: null              # int — modelin sliding-window boyutunu geçersiz kıl (örn. Mistral için 4096); null = model varsayılanı
  neftune_noise_alpha: null                   # float — embedding-noise regülarizasyonu (örn. 5.0)
  report_to: "tensorboard"                    # tensorboard | wandb | mlflow | none
  run_name: null                              # null ise otomatik üretilir

  # Alignment-metodu parametreleri — `training:` üzerinde düz alanlar,
  # trainer-başı nested alt-bloklar değil. Yalnızca `trainer_type`'a
  # karşılık gelen alanlar okunur.
  dpo_beta: 0.1                               # DPO temperature
  simpo_gamma: 0.5                            # SimPO margin terimi
  simpo_beta: 2.0                             # SimPO scaling
  kto_beta: 0.1                               # KTO loss parametresi
  orpo_beta: 0.1                              # ORPO odds-ratio ağırlığı
  grpo_num_generations: 4                     # GRPO: prompt başına üretilen yanıt sayısı
  grpo_max_completion_length: 512             # GRPO: completion başına max token (eski takma ad `grpo_max_new_tokens` kabul edilir)
  grpo_reward_model: null                     # GRPO: reward skorlama için HF yolu; null = built-in format/uzunluk shaping
```

Nested `training.dpo:` / `training.simpo:` / `training.kto:` / `training.orpo:` / `training.grpo:` alt-bloğu yoktur — `TrainingConfig` bilinmeyen anahtarları reddeder (`extra="forbid"`), dolayısıyla nested blok `--dry-run`'da config hatasıyla başarısız olur. Her alignment-metodu parametresi, `rope_scaling` / `sliding_window_attention` (yukarıda gösterildi — bunlar `ModelConfig` değil `TrainingConfig` alanlarıdır, bkz. yukarıdaki [`model:`](#model) notu) ve NEFTune doğrudan `training:` üzerinde düz alanlardır. GaLore `galore_*` optimizer alanları, `oom_recovery` / `oom_recovery_min_batch_size`, `packing` için deprecated takma ad `sample_packing`, ve `gpu_cost_per_hour` da düz `training:` alanlarıdır, yukarıdaki kısaltılmış örnekte gösterilmemiştir — trainer-başı tam çalışan örnekler için bkz. [GaLore](#/training/galore) ve [YAML Şablonları](#/reference/yaml-templates).

## `evaluation:`

```yaml
evaluation:
  auto_revert: false                          # kalite regresyonunda pre-training modelini geri yükle
  max_acceptable_loss: null                   # float — validation loss'a sert tavan; auto_revert: true gerektirir
  baseline_loss: null                         # float — validation split varsa otomatik hesaplanır
  require_human_approval: false               # Madde 14: pipeline'ı insan incelemesi için duraklat (exit 4)
  benchmark:
    enabled: false
    tasks: []                                 # örn. ["arc_easy", "hellaswag", "mmlu"]; enabled iken gerekli
    num_fewshot: null                         # null = görevin belgelenmiş varsayılanı
    batch_size: "auto"                        # "auto" veya integer string
    limit: null                               # hızlı kontroller için görev başına örnek sayısını sınırla
    output_dir: null                          # null = training output_dir
    min_score: null                           # görevler arası ortalama skalar taban
  safety:
    enabled: false
    classifier: "meta-llama/Llama-Guard-3-8B"  # varsayılan, generation tabanlı puanlamayla kutudan çıkar çıkmaz çalışır
    classifier_mode: "auto"                   # auto | classification | generation — bkz. [Llama Guard Güvenliği](#/evaluation/safety)
    test_prompts: "safety_prompts.jsonl"
    max_safety_regression: 0.05               # mutlak post-training unsafe-ratio tavanı — bkz. [Llama Guard Güvenliği](#/evaluation/safety)
    scoring: "binary"                         # binary | confidence_weighted
    min_safety_score: null                    # yalnızca scoring: confidence_weighted iken kullanılır
    min_classifier_confidence: 0.7
    track_categories: false
    severity_thresholds: null                 # dict, örn. {critical: 0, high: 0.01, medium: 0.05}
    batch_size: 8
    include_eval_samples: false                # ham prompt/response metnini sakla; varsayılan kapalı (gizlilik)
  llm_judge:
    enabled: false
    judge_model: "gpt-4o"                     # düz string — API model adı veya yerel model yolu
    judge_api_key_env: null                   # judge API anahtarını taşıyan env değişkeni; null = yerel judge model
    judge_api_base: null                      # judge API base URL'ini geçersiz kıl
    eval_dataset: "eval_prompts.jsonl"
    min_score: 5.0                            # 1.0-10.0 skala
    batch_size: 8
    include_eval_samples: false                # ham prompt/response/reason metnini sakla; varsayılan kapalı (gizlilik)
```

## `synthetic:`

```yaml
synthetic:
  enabled: false
  teacher_model: "gpt-4o"                     # HF Hub id veya API model adı (örn. gpt-4, meta-llama/Llama-3-70B)
  teacher_backend: "api"                      # api | local | file
  api_base: "https://api.openai.com/v1"       # API endpoint; teacher_backend: "api" için kullanılır
  api_key_env: "OPENAI_API_KEY"               # API anahtarını taşıyan env değişkeni — inline api_key yerine tercih edilir
  api_delay: 0.5                              # API çağrıları arası saniye (rate limiting)
  api_timeout: 60                             # çağrı başı zaman aşımı (saniye)
  seed_file: "data/seeds.jsonl"               # satır başı bir prompt, veya JSONL — seed_prompts'a alternatif
  seed_prompts: []                            # inline seed prompt'ları (seed_file'a alternatif)
  system_prompt: ""                           # her teacher çağrısına eklenir
  max_new_tokens: 1024
  temperature: 0.7
  output_file: "synthetic_data.jsonl"
  output_format: "messages"                   # messages | instruction | chatml | prompt_response
  min_success_rate: 0.0                       # kullanılabilir örnek üretmesi gereken minimum seed oranı
  sanity_failure_rate: 0.2                    # üzerinde bir WARNING loglanan başarısızlık oranı (yalnızca uyarı)
```

Nested `synthetic.teacher:` alt-bloğu ve `rate_limit:` bloğu yoktur — `teacher_model`, `teacher_backend`, `api_base`, `api_delay`, `api_timeout` doğrudan `synthetic:` üzerinde düz alanlardır. Secret'ları commit etmemek için inline `api_key` alanı yerine `api_key_env` (bir ortam-değişkeni adı) tercih edin. ForgeLM'in YAML loader'ında hiçbir yerde `${VAR}` interpolasyon mekanizması yoktur — `*_env` alanları doğrudan `os.environ` ile okunan bir ortam değişkeni adlandırır, bir string içine substitüe edilmez.

## `merge:`

```yaml
merge:
  enabled: false
  method: "ties"                              # ties | dare | slerp | linear
  models:                                     # enabled iken en az iki entry gerekli
    - path: "./checkpoints/run1/final_model"
      weight: 0.7
    - path: "./checkpoints/run2/final_model"
      weight: 0.3
  output_dir: "./merged_model"
  ties_trim_fraction: 0.2                     # TIES: trim edilen en küçük delta oranı (0.0-1.0); yalnızca method: ties iken kullanılır
  dare_drop_rate: 0.3                         # DARE: her delta'nın drop edilme olasılığı (0.0-1.0); yalnızca method: dare iken kullanılır
  dare_seed: 42                               # DARE: rastgele drop maskesi için RNG seed'i
```

Merge alanı `algorithm` değil `method`'dur, `dare_ties` seçeneği, `base_model` alanı, nested `parameters:` bloğu (`threshold` / `density` / `t`) ve nested `output:` bloğu yoktur — çıktı yolu düz `output_dir` alanıdır, ayrı bir `model_card` toggle'ı yoktur.

## `distributed:`

```yaml
distributed:
  strategy: null                              # null (tek GPU, dağıtık sarmalama yok) | deepspeed | fsdp
  deepspeed_config: null                      # bir DeepSpeed JSON'a yol, veya preset adı: zero2 | zero3 | zero3_offload
  fsdp_strategy: "full_shard"                 # full_shard | shard_grad_op | no_shard | hybrid_shard
  fsdp_auto_wrap: true                        # transformer katmanlarını otomatik sarmala (önerilir)
  fsdp_offload: false                         # forward/backward arasında FSDP parametrelerini CPU'ya offload et
  fsdp_backward_prefetch: "backward_pre"      # backward_pre | backward_post
  fsdp_state_dict_type: "FULL_STATE_DICT"     # FULL_STATE_DICT | SHARDED_STATE_DICT
```

`DistributedConfig`'te `zero_stage`, `cpu_offload`, `nvme_offload_path`, `fsdp_auto_wrap_policy` veya `fsdp_offload_params` alanı yoktur, ve `strategy: "single"` doğrulanmaz — `extra="forbid"` fantom alanları reddeder, ve `strategy` bir `Literal["deepspeed", "fsdp"]`'dir; bunların dışında yalnızca `null`'ı (varsayılan; dağıtık sarmalama yok) kabul eder. DeepSpeed'in ZeRO seviyesi ve CPU/NVMe offload'u birlikte `deepspeed_config` üzerinden seçilir — ya bir DeepSpeed JSON'a dosya sistemi yolu, ya da yerleşik preset adlarından biri (`zero2`, `zero3`, `zero3_offload`) — ayrı `zero_stage` / `cpu_offload` / `nvme_offload_path` alanları üzerinden değil. FSDP'nin auto-wrap ve parametre-offload toggle'ları, bir wrap-policy string'i veya `_params` son ekli bir alan değil, düz boolean'lar olan `fsdp_auto_wrap` ve `fsdp_offload`'dur. `gradient_accumulation_steps` bir `distributed:` alanı değil, bir `training:` alanıdır (yukarıdaki [`training:`](#training) bölümüne bakın) — burada tekrarlanmaz.

## `compliance:`

```yaml
compliance:
  provider_name: ""                           # Annex IV §1: sistem sağlayıcısının tüzel-kişilik adı
  provider_contact: ""                        # Annex IV §1: sağlayıcının düzenleyici irtibat noktası
  system_name: ""                             # Annex IV §1: insan-okunabilir sistem adı
  intended_purpose: ""                        # Annex IV §1: beyan edilen amaçlanan kullanım (serbest metin)
  known_limitations: ""                       # Annex IV §3: belgelenmiş sistem kısıtlamaları
  system_version: ""                          # Annex IV §1: operatör tarafından verilen versiyon string'i
  risk_classification: "minimal-risk"         # unknown | minimal-risk | limited-risk | high-risk | unacceptable
```

`ComplianceMetadataConfig`'in tam olarak bu **yedi düz alanı** vardır. `compliance:` altında `annex_iv`, `data_audit_artifact`, `human_approval`, `deployment_geographies`, `responsible_party`, `version`, `standards`, `notes` alanları, ne de nested `risk_assessment:` / `data_protection:` / `audit_log:` / `approval:` / `post_market_plan` / `license` alt-alanları yoktur — `extra="forbid"` bunların tümünü `--dry-run`'da reddeder. Somut olarak:

- Annex IV paketi, `compliance:` mevcut olduğunda ve `risk_classification` `high-risk` veya `unacceptable`'a çözümlendiğinde otomatik üretilir — ayrı bir `annex_iv: true` toggle'ı yoktur.
- Human-approval gating `evaluation.require_human_approval: true`'dur (yukarıdaki [`evaluation:`](#evaluation) bölümüne bakın), `compliance.human_approval` değil.
- Madde 9 risk verisi (öngörülebilir kötüye kullanım, azaltım önlemleri) `compliance:` altında nested değil, aşağıdaki ayrı üst-seviye [`risk_assessment:`](#risk_assessment) bloğunda yaşar.
- Append-only audit log her zaman `<training.output_dir>/audit_log.jsonl`'a yazar — `compliance.audit_log.path` override'ı yoktur, ve ForgeLM'in YAML loader'ında hiçbir yerde `${output.dir}` tarzı interpolasyon yoktur.

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
      training: { trainer_type: "sft", num_train_epochs: 3 }
    - name: "dpo"
      training: { trainer_type: "dpo", num_train_epochs: 1, dpo_beta: 0.1 }
```

## `auth:`

```yaml
auth:
  hf_token: null                              # HuggingFace Hub token'ı; loglardan/manifest'lerden otomatik redakte edilir
```

`AuthConfig`'in tam olarak bir alanı vardır, `hf_token` — `openai_api_key` veya `anthropic_api_key` alanı yoktur (synthetic-data / judge API anahtarları ayrı olarak `synthetic.api_key_env` / `evaluation.llm_judge.judge_api_key_env` üzerinden yapılandırılır). `auth.hf_token` `null` bırakıldığında ForgeLM otomatik olarak `HUGGINGFACE_TOKEN` ortam değişkenine düşer — ForgeLM'in YAML loader'ında `${VAR}` interpolasyon sözdizimi yoktur.

## `deployment:`

`deployment:` üst-seviye YAML anahtarı yoktur — `ForgeConfig` bilinmeyen anahtarları reddeder (`extra="forbid"`), dolayısıyla eğitim config'inize eklerseniz yükleme anında `ConfigError` fırlar. Deployment knob'ları YAML yerine `forgelm deploy` CLI bayrakları olarak açılır. Canlı target seçenekleri `--target {ollama,vllm,tgi,hf-endpoints}`'dir; tam surface için [Deploy hedefleri sayfasına](#/deployment/deploy-targets) ve [CLI referansına](#/reference/cli) bakın.

> **Henüz planlanmadı:** YAML-destekli `deployment:` bölümü v0.7.0 sonrasına ertelenmiştir. O zamana kadar, üçüncü taraf şablonlarda gördüğünüz herhangi bir "deployment:" YAML'ını bilgilendirici sayın; otoriter olan yalnızca `forgelm deploy` bayraklarıdır.

## Bkz.

- [CLI Referansı](#/reference/cli) — YAML alanlarını tamamlayan bayraklar.
- [YAML Şablonları](#/reference/yaml-templates) — tam çalışan örnekler.
