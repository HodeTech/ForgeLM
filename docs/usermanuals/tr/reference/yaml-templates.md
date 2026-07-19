---
title: YAML Şablonları
description: Yaygın iş akışları için tam çalışan konfigürasyonlar — kopyala, uyarla, koştur.
---

# YAML Şablonları

ForgeLM `forgelm/templates/<name>/config.yaml` altında **5 birinci-sınıf
quickstart şablonu** ship eder. Her biri sıfırdan yazmak yerine uyarlayacağınız
tam ve dry-run-validate edilmiş bir config'tir. Herhangi birini şuna ile
materyalize edin:

```shell
$ forgelm quickstart <şablon-adı> --output config.yaml
$ forgelm --config config.yaml --dry-run
$ forgelm --config config.yaml
```

Beş şablon ve her birinin optimize ettiği kullanıcı yolu:

| Şablon | Trainer | İş akışı |
|---|---|---|
| `customer-support` | SFT | Multi-turn yardımcı + güvenli asistan — en yaygın production pattern. |
| `code-assistant` | SFT | Kod-tamamlama / pair-programming modeli. |
| `domain-expert` | SFT | Domain-spesifik corpus üzerinde BYOD (Bring Your Own Dataset). |
| `medical-qa-tr` | SFT | Türkçe tıbbi soru-cevap (regüle alan — `risk_assessment` + safety eval ön-bağlı ship olur). |
| `grpo-math` | GRPO | Matematik benchmark'ları üzerinde reward-model fine-tuning (yerleşik format/length reward shaping). |

Kanonik şablon içeriği **doğruluk kaynağıdır** — `forgelm quickstart`'ın
materyalize ettiği ve CI'ın canlı Pydantic şemasına karşı dry-run yaptığı
şeydir. Aşağıdaki örnekler hızlı görsel referans için kısaltılmış kopyalardır;
bu sayfa `forgelm/templates/<name>/config.yaml` ile çelişiyorsa şablon
kazanır.

## Müşteri-destek asistanı (SFT)

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

## SFT → DPO zinciri (preference hizalama)

Yukarıdaki SFT adımı `./checkpoints/customer-support/final_model/` üretir;
ardından bu çıktı, ayrı toplanmış bir preference dataset'i üzerinde DPO'yu
besler:

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
  dpo_beta: 0.1                                # düz field — KL gücü

evaluation:
  require_human_approval: true                 # Madde 14 gözetim kapısı
  auto_revert: true                            # regresyonda geri alır
  benchmark:
    enabled: true
    tasks: ["hellaswag", "arc_easy", "truthfulqa_mc1"]
    min_score: 0.45                            # ortalama görevler üzerinde tek taban
  safety:
    enabled: true
    classifier: "meta-llama/Llama-Guard-3-8B"
    track_categories: true
    severity_thresholds:                         # anahtarlar önem düzeyleridir, Llama-Guard kategori kodları değil
      critical: 0.0                              # sıfır tolerans
      high: 0.01
      medium: 0.05
      low: 0.10

compliance:
  provider_name: "Acme Corp"
  provider_contact: "compliance@acme.example"
  system_name: "customer-support-v1.2"
  intended_purpose: "Bir Türk telekom için müşteri destek asistanı"
  risk_classification: "high-risk"
  system_version: "1.2.0"

webhook:
  url_env: "SLACK_WEBHOOK"
  notify_on_start: true
  notify_on_success: true
  notify_on_failure: true
```

Bundled Annex IV paketi (`compliance/`), `compliance:` bloğu mevcut ve
`risk_classification` `high-risk` ya da `unacceptable` olduğunda otomatik
üretilir. Beş wire-format webhook event'i
(`training.{start,success,failure,reverted}`, `approval.required`) ayrı bir
event-subscription listesi olmadan fırlar — üç `notify_on_*` flag'i tüm
yüzeydir (bkz. [Webhook'lar](#/operations/webhooks)).

## GRPO — matematik akıl yürütme

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
  grpo_num_generations: 8                # prompt başına örnek
  grpo_max_completion_length: 512        # üretim başına üst sınır
  grpo_reward_model: "my_reward.score"   # opsiyonel — yerleşik format/length shaping fallback'ine düşer
```

`forgelm/grpo_rewards.py` her zaman aktif bir format/length reward fallback'i
ship eder; `grpo_reward_model`'i yalnızca domain-spesifik scorer'lar için
opt-in olarak kullanın.

## Field referansı

Kanonik şema [`forgelm/config.py`](https://github.com/HodeTech/ForgeLM/blob/main/forgelm/config.py)
altında, Pydantic modelleri `ForgeConfig`, `ModelConfig`, `LoraConfig`,
`TrainingConfig`, `DataConfig`, `EvaluationConfig`, `BenchmarkConfig`,
`SafetyConfig`, `JudgeConfig`, `ComplianceMetadataConfig`,
`WebhookConfig`, `RetentionConfig` içinde yer alır. Her alanın bir docstring'i
vardır ve [Konfigürasyon Referansı](#/reference/configuration) sayfası bunu
yüzeyler.

## Kaldırılan şablonlar / field'lar

Bu sayfanın eski sürümleri hiç ship olmamış ya da yeniden adlandırılmış
şablonlar / field'lar tanıtıyordu. Bunun yerine kanonik adları kullanın:

| Eski (validate olmaz) | Yeni |
|---|---|
| `model.use_unsloth: true` | `model.backend: "unsloth"` |
| `training.trainer: "..."` | `training.trainer_type: "..."` |
| `training.epochs: N` | `training.num_train_epochs: N` |
| `training.batch_size: N` | `training.per_device_train_batch_size: N` |
| `training.scheduler: "cosine"` | (HF-tarafı default; düz field olarak yüzeylenmedi) |
| `training.{dpo,simpo,kto,orpo,grpo}: { ... }` (nested) | düz `training.{dpo_beta, simpo_beta, kto_beta, ...}` |
| Üst-düzey `datasets: [{path, format}]` array | `data: { dataset_name_or_path: ... }` (tekil) |
| Üst-düzey `output: { dir, gguf, webhook, ... }` | `training.output_dir: ...` (skaler) + ayrı üst-düzey `webhook:` bloğu |
| `compliance.human_approval: true` | `evaluation.require_human_approval: true` |
| `compliance.annex_iv: true` | (`compliance:` bloğu mevcut + risk tier tetiklediğinde otomatik üretilir) |
| `compliance.{deployment_geographies, responsible_party, version, standards, notes, data_protection, audit_log, approval, post_market_plan, license}` | (`ComplianceMetadataConfig`'te yok — yalnızca yedi kanonik field) |
| `evaluation.benchmark.floors: {per-task dict}` | `evaluation.benchmark.min_score: scalar` (ortalama tek taban) |
| `evaluation.auto_revert: { enabled: true }` | `evaluation.auto_revert: true` (boolean) |
| `safety.model: "..."` | `safety.classifier: "..."` |
| `safety.block_categories: [list]` | `safety.track_categories: true` + `safety.severity_thresholds: {dict}` |

`model.load_in_4bit` bu tabloda **yer almaz** — canlı bir `ModelConfig`
field'ıdır (default `true`) ve QLoRA anahtarı olmaya devam eder.
`LoraConfigModel.method`'ın `"qlora"` diye bir literal'ı yoktur; yalnızca
`lora`, `dora`, `pissa` veya `rslora` kabul eder. QLoRA, `model.load_in_4bit: true`
ile herhangi bir LoRA `method:` değerinin birleşimidir — bkz.
[LoRA, QLoRA, DoRA](#/training/lora).

## Bkz.

- [Konfigürasyon Referansı](#/reference/configuration) — type ve default ile her field.
- [Quickstart Şablonları](#/getting-started/first-run) — `forgelm quickstart`'ın operatör turu.
- [Eğitim paradigmaları](#/training/sft) — kanonik şemada per-trainer YAML örnekleri.
