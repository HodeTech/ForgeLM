# Konfigürasyon Rehberi

ForgeLM tüm yapılandırma için YAML dosyalarını kullanır — bildirimsel, sürüm kontrollü ve CI/CD-uyumlu.

Tam açıklamalı örnek için `config_template.yaml` dosyasına bakın.

---

## `model`

| Alan | Tip | Varsayılan | Açıklama |
|------|-----|-----------|----------|
| `name_or_path` | string | *zorunlu* | HuggingFace model ID veya yerel yol |
| `max_length` | int | `2048` | Maksimum bağlam uzunluğu |
| `load_in_4bit` | bool | `true` | QLoRA 4-bit NF4 kuantizasyon |
| `backend` | string | `"transformers"` | `"transformers"` veya `"unsloth"` (2-5x hızlı, Linux) |
| `trust_remote_code` | bool | `false` | Model depolarından özel kod çalıştırma. **Güvenlik riski** |
| `offline` | bool | `false` | İzole mod: HF Hub çağrısı yok. Modeller/veri setleri yerel olmalı |
| `bnb_4bit_use_double_quant` | bool | `true` | Ekstra VRAM tasarrufu için çift kuantizasyon |
| `bnb_4bit_quant_type` | string | `"nf4"` | Kuantizasyon tipi (`"nf4"` veya `"fp4"`) |
| `bnb_4bit_compute_dtype` | string | `"auto"` | Hesaplama dtype'ı: `"auto"`, `"bfloat16"`, `"float16"`, `"float32"` (son üçü kısa takma adları da kabul eder: `"bf16"`, `"fp16"`, `"fp32"`) |

#### `model.moe` (İsteğe bağlı — MoE modeller)

| Alan | Tip | Varsayılan | Açıklama |
|------|-----|-----------|----------|
| `quantize_experts` | bool | `false` | İnaktif expert ağırlıklarını int8'e kuantize et |
| `experts_to_train` | string | `"all"` | `"all"` veya virgülle ayrılmış indeksler |

#### `model.multimodal` (İsteğe bağlı — VLM modeller)

| Alan | Tip | Varsayılan | Açıklama |
|------|-----|-----------|----------|
| `enabled` | bool | `false` | Görüntü-dil modeli (VLM) fine-tuning'i etkinleştir |
| `image_column` | string | `"image"` | Veri setinde görüntü yolu / URL'i taşıyan kolon adı |
| `text_column` | string | `"text"` | Metin / caption taşıyan kolon adı |

---

## `lora`

| Alan | Tip | Varsayılan | Açıklama |
|------|-----|-----------|----------|
| `r` | int | `8` | LoRA rank. Yüksek = daha fazla parametre |
| `alpha` | int | `16` | LoRA ölçekleme faktörü |
| `dropout` | float | `0.1` | Dropout olasılığı |
| `bias` | string | `"none"` | `"none"`, `"all"` veya `"lora_only"` |
| `method` | string | `"lora"` | PEFT yöntemi: `"lora"`, `"dora"`, `"pissa"`, `"rslora"` |
| `use_dora` | bool | `false` | **Kullanımdan kaldırıldı** — `method: "dora"` için takma ad; v0.10.0'da kaldırılır. `true` ayarlamak `DeprecationWarning` ile `method: "dora"`'ya yönlendirir. Bunun yerine `method` kullanın. |
| `use_rslora` | bool | `false` | **Kullanımdan kaldırıldı** — `method: "rslora"` için takma ad (r>64 için önerilir); v0.10.0'da kaldırılır. `true` ayarlamak `DeprecationWarning` ile `method: "rslora"`'ya yönlendirir. Bunun yerine `method` kullanın. |
| `target_modules` | list | `["q_proj", "v_proj"]` | LoRA uygulanacak modüller |
| `task_type` | string | `"CAUSAL_LM"` | PEFT için görev tipi |

> `use_dora` ve `use_rslora` birbirini dışlar; her biri, farklı bir PEFT yöntemi belirten açıkça set edilmiş bir `method` ile de çelişir (ör. `method: "rslora"` iken `use_dora: true`) — her iki durum da config-load zamanında `ConfigError` (çıkış kodu 1) fırlatır. Kullanımdan kaldırılan boolean bayraklar yerine doğrudan `method` ayarlayın.

---

## `training`

| Alan | Tip | Varsayılan | Açıklama |
|------|-----|-----------|----------|
| `output_dir` | string | `"./checkpoints"` | Checkpoint kayıt dizini |
| `final_model_dir` | string | `"final_model"` | Nihai artefaktlar için alt dizin |
| `merge_adapters` | bool | `false` | Kaydedilmeden önce adapter'ları temel modele birleştir |
| `trainer_type` | string | `"sft"` | `"sft"`, `"dpo"`, `"simpo"`, `"kto"`, `"orpo"`, `"grpo"` |
| `max_steps` | int | `-1` | Sıkı adım üst sınırı. `-1` = `num_train_epochs` kullanılır; pozitif bir değer epoch'ları geçersiz kılar. |
| `num_train_epochs` | int | `3` | Eğitim epoch sayısı (yalnızca `max_steps == -1` iken dikkate alınır). |
| `per_device_train_batch_size` | int | `4` | GPU başına batch boyutu |
| `gradient_accumulation_steps` | int | `2` | Geri yayılımdan önce biriktirilecek adım sayısı |
| `learning_rate` | float | `2e-5` | Öğrenme oranı (hizalama için daha düşük: 5e-6) |
| `warmup_ratio` | float | `0.1` | Isınma oranı |
| `weight_decay` | float | `0.01` | AdamW ağırlık bozunumu |
| `eval_steps` | int | `200` | Her N adımda bir değerlendir |
| `save_steps` | int | `200` | Her N adımda bir checkpoint kaydet |
| `save_total_limit` | int | `3` | Tutulacak maksimum checkpoint sayısı |
| `early_stopping_patience` | int | `3` | Doğrulama kaybı iyileşmeden N değerlendirme sonra dur (yalnızca bir doğrulama bölünmesi varsa etkin). |
| `packing` | bool | `false` | Dizi paketleme (yalnızca SFT) |
| `report_to` | string | `"tensorboard"` | `"tensorboard"`, `"wandb"`, `"mlflow"`, `"none"` |
| `run_name` | string | `null` | W&B/MLflow çalışma adı (null ise otomatik üretilir) |

#### OOM Recovery (Bellek Hatası Kurtarma)

CUDA bellek yetersizliği (out-of-memory) hatalarında `per_device_train_batch_size` değerini
otomatik olarak yarıya indirir, `gradient_accumulation_steps` değerini ikiye katlar ve
training'i yeniden dener. Efektif batch boyutu korunur.

| Alan | Tip | Varsayılan | Açıklama |
|------|-----|-----------|----------|
| `oom_recovery` | bool | `false` | CUDA OOM hatalarında batch boyutunu küçülterek yeniden dene |
| `oom_recovery_min_batch_size` | int | `1` | Bu batch boyutuna ulaşınca denemeyi durdur |

**Örnek:**

```yaml
training:
  per_device_train_batch_size: 8
  gradient_accumulation_steps: 2
  oom_recovery: true
  oom_recovery_min_batch_size: 1
```

#### GaLore (Optimizer Seviyesinde Bellek Optimizasyonu)

| Alan | Tip | Varsayılan | Açıklama |
|------|-----|-----------|----------|
| `galore_enabled` | bool | `false` | GaLore gradient düşük rank projeksiyonunu etkinleştir |
| `galore_optim` | string | `"galore_adamw"` | GaLore optimizer varyantı. Şunlardan biri: `"galore_adamw"`, `"galore_adamw_8bit"`, `"galore_adafactor"`, `"galore_adamw_layerwise"`, `"galore_adamw_8bit_layerwise"`, `"galore_adafactor_layerwise"`. `_8bit` optimizer-state VRAM'ini yarıya indirir; `_layerwise` per-layer recompute ile peak VRAM'i düşürür. |
| `galore_rank` | int | `128` | Gradient projeksiyonu için rank |
| `galore_update_proj_gap` | int | `200` | Projeksiyon güncellemeleri arası adım sayısı |
| `galore_scale` | float | `0.25` | GaLore ölçekleme faktörü |
| `galore_proj_type` | string | `"std"` | Projeksiyon tipi: `"std"`, `"reverse_std"`, `"right"`, `"left"`, `"full"` |
| `galore_target_modules` | `Optional[List[str]]` | `null` | GaLore uygulanacak modül-adı regex pattern'leri. `null` `[r".*.attn.*", r".*.mlp.*"]`'ye düşer (attention + MLP katmanları). |

#### Uzun Bağlam Eğitimi

| Alan | Tip | Varsayılan | Açıklama |
|------|-----|-----------|----------|
| `rope_scaling` | `Optional[Dict[str, Any]]` | `null` | RoPE ölçekleme yöntemi sözlüğü (`{"type": "linear", "factor": 2.0}` vs.). Desteklenen tipler: `"linear"`, `"dynamic"`, `"yarn"`, `"longrope"`. |
| `neftune_noise_alpha` | float | `null` | NEFTune gürültü enjeksiyonu alpha değeri (ör. `5.0`) |
| `sliding_window_attention` | int | `null` | Kayan pencere dikkat boyutu (token) |
| `sample_packing` | bool | `false` | **Kullanımdan kaldırıldı** — `packing` için takma ad (TRL tek bir packing düğmesi sunar). `true` ayarlamak `DeprecationWarning` ile `packing: true`'ya yönlendirir; v0.9.0'da kaldırılır. Bunun yerine `packing` kullanın. |

#### GPU Maliyet Tahmini

| Alan | Tip | Varsayılan | Açıklama |
|------|-----|-----------|----------|
| `gpu_cost_per_hour` | float | `null` | Özel GPU maliyet oranı (USD/saat). null ise GPU modelinden otomatik algılanır |

#### Hizalama Parametreleri

| Alan | Tip | Varsayılan | Kullanan |
|------|-----|-----------|---------|
| `dpo_beta` | float | `0.1` | DPO sıcaklık |
| `simpo_gamma` | float | `0.5` | SimPO marj terimi |
| `simpo_beta` | float | `2.0` | SimPO ölçekleme |
| `kto_beta` | float | `0.1` | KTO kayıp parametresi |
| `orpo_beta` | float | `0.1` | ORPO odds ratio ağırlığı |
| `grpo_num_generations` | int | `4` | GRPO: prompt başına yanıt |
| `grpo_max_completion_length` | int | `512` | GRPO: completion başına maksimum token (eski takma ad `grpo_max_new_tokens` kabul edilir) |
| `grpo_reward_model` | string | `null` | GRPO: ödül modeli yolu (HF veya yerel) |

---

## `data`

| Alan | Tip | Varsayılan | Açıklama |
|------|-----|-----------|----------|
| `dataset_name_or_path` | string | *zorunlu* | HF veri seti ID veya yerel JSONL |
| `extra_datasets` | list | `null` | Karıştırılacak ek veri setleri |
| `mix_ratio` | list | `null` | Veri seti başına ağırlık (ör. `[0.7, 0.3]`) |
| `shuffle` | bool | `true` | Eğitim verisini karıştır |
| `clean_text` | bool | `true` | Fazladan boşlukları temizle |
| `add_eos` | bool | `true` | Dizilere EOS token'ı ekle |

#### `data.governance` (İsteğe bağlı — EU AI Act Madde 10)

| Alan | Tip | Varsayılan | Açıklama |
|------|-----|-----------|----------|
| `collection_method` | string | `""` | Veri toplama yöntemi |
| `annotation_process` | string | `""` | Etiketleme süreci |
| `known_biases` | string | `""` | Bilinen önyargılar |
| `personal_data_included` | bool | `false` | Kişisel veri içeriyor |
| `dpia_completed` | bool | `false` | Veri Koruma Etki Değerlendirmesi |

---

## `evaluation` (İsteğe bağlı)

| Alan | Tip | Varsayılan | Açıklama |
|------|-----|-----------|----------|
| `auto_revert` | bool | `false` | Değerlendirme başarısız olursa modeli sil |
| `max_acceptable_loss` | float | `null` | eval_loss üst sınırı |
| `baseline_loss` | float | `null` | `null` ise otomatik hesaplanır |
| `require_human_approval` | bool | `false` | İnsan incelemesi için duraklat (çıkış kodu 4) |

#### `evaluation.benchmark` (İsteğe bağlı)

| Alan | Tip | Varsayılan | Açıklama |
|------|-----|-----------|----------|
| `enabled` | bool | `false` | lm-eval-harness benchmark'ları |
| `tasks` | list | `[]` | Görev isimleri (ör. `["arc_easy", "hellaswag"]`) |
| `num_fewshot` | int | `null` | Few-shot örnek sayısı (görev varsayılanı) |
| `batch_size` | string | `"auto"` | Değerlendirme batch boyutu |
| `limit` | int | `null` | Görev başına örnek sayısı (hızlı kontroller için) |
| `output_dir` | string | `null` | Benchmark sonuç JSON'unun yazılacağı yer. `null` = training `output_dir`. |
| `min_score` | float | `null` | Minimum ortalama doğruluk |

> `enabled: true`, `tasks` içinde en az bir görev gerektirir — görevi olmayan etkin bir benchmark kapısı config yüklemesinde reddedilir.

#### `evaluation.safety` (İsteğe bağlı)

| Alan | Tip | Varsayılan | Açıklama |
|------|-----|-----------|----------|
| `enabled` | bool | `false` | Güvenlik sınıflandırıcı değerlendirmesi |
| `classifier` | string | `"meta-llama/Llama-Guard-3-8B"` | Güvenlik sınıflandırıcı modeli. Varsayılan kutudan çıkar çıkmaz çalışır: `classifier_mode: auto` altında generation tabanlı Llama-Guard puanlamasıyla değerlendirilir |
| `classifier_mode` | string | `"auto"` | Sınıflandırıcının nasıl puanlanacağı: `auto` (bilinen bir generative Llama-Guard checkpoint'i için generation, diğerleri için `text-classification`), `classification` (pipeline'ı zorlar — eğitilmiş `safe`/`unsafe` başlığı gerektirir) veya `generation` (generation tabanlı Llama-Guard puanlamasını zorlar) |
| `test_prompts` | string | `"safety_prompts.jsonl"` | Adversarial test prompt dosyası. Yerleşik: `configs/safety_prompts/` |
| `max_safety_regression` | float | `0.05` | Maksimum güvensiz oran (binary kapı) |
| `scoring` | string | `"binary"` | Puanlama modu: `"binary"` veya `"confidence_weighted"` |
| `min_safety_score` | float | `null` | Ağırlıklı skor eşiği (confidence_weighted için) |
| `min_classifier_confidence` | float | `0.7` | Düşük güven uyarı eşiği |
| `track_categories` | bool | `false` | Llama Guard S1-S14 zarar kategorilerini ayrıştır |
| `severity_thresholds` | dict | `null` | Ciddiyet bazlı sınırlar: `{"critical": 0, "high": 0.01}` |
| `batch_size` | int | `8` | Güvenlik değerlendirmesi için batched generation boyutu. `1` batching'i devre dışı bırakır; geniş VRAM'de throughput için artırın, küçük VRAM'de OOM riskini azaltmak için düşürün. |
| `include_eval_samples` | bool | `false` | Ham `prompt` / `response` dizgelerini `safety_results.json`'a yazar. GDPR / EU AI Act Madde 10 gizliliği için **varsayılan olarak kapalı** — adversarial prompt'lar ve yanıtlar hassas içerik açığa çıkarabilir. Yalnızca hata ayıklama için açın. |

#### `evaluation.llm_judge` (İsteğe bağlı)

| Alan | Tip | Varsayılan | Açıklama |
|------|-----|-----------|----------|
| `enabled` | bool | `false` | LLM-Hakim puanlama |
| `judge_model` | string | `"gpt-4o"` | Hakim modeli (API veya yerel) |
| `judge_api_key_env` | string | `null` | API anahtarı için ortam değişkeni adı (null = yerel hakim) |
| `judge_api_base` | string | `null` | Hakim API base URL'sini geçersiz kıl (Azure OpenAI, kendi barındırılan vLLM, OpenAI-uyumlu gateway, ör. `https://api.together.xyz/v1`). Tanımlı değilse SDK'nın varsayılan endpoint'i kullanılır. |
| `eval_dataset` | string | `"eval_prompts.jsonl"` | Değerlendirme prompt dosyası |
| `min_score` | float | `5.0` | Minimum ortalama puan (1-10) |
| `batch_size` | int | `8` | LLM-hakim turunda puanlanan (prompt, completion) çift sayısı. `1` batching'i devre dışı bırakır. |
| `include_eval_samples` | bool | `false` | Ham eval `prompt`, `response` ve hakim `reason` dizgelerini `judge_results.json`'a yazar. GDPR / EU AI Act Madde 10 gizliliği için **varsayılan olarak kapalı** — hakim gerekçesi eval setinden PII alıntılayabilir. Yalnızca hata ayıklama için açın. |

> **Hakim girdisi kırpma:** her puanlama prompt'u oluşturulurken hakim,
> eval prompt'unun en fazla ilk **500 karakterini** ve model yanıtının en fazla
> ilk **1000 karakterini** görür. Bu, hakim prompt'unu sınırlı tutar (ve API
> yolunu ucuz kılar); tipik bir `max_new_tokens` üretim bütçesinin altındadır,
> bu yüzden çok uzun yanıtlar yalnızca baştaki bir parça üzerinden değerlendirilir.
> Bir satır gerçekten kırpıldığında ForgeLM tek seferlik bir `WARNING` kaydeder.
> Limitler sabittir (henüz config ile ayarlanamaz) — uzun biçimli ince ayarlar
> için `min_score` ayarlarken bunu göz önünde bulundurun.
>
> **Kaldırıldı:** `evaluation.staging_ttl_days`,
> [`retention.staging_ttl_days`](#retention-isteğe-bağlı--gdpr-madde-17-silme-ufukları)
> tarafından devralınmış ve v0.8.0'da kaldırılmıştır. `retention.staging_ttl_days`
> kullanın; eski anahtarı hâlâ set eden YAML dosyaları `EXIT_CONFIG_ERROR` ile
> config-load başarısızlığına yol açar.

---

## `retention` (İsteğe bağlı — GDPR Madde 17 silme ufukları)

Uyumluluk, eğitim ve değerlendirme artefaktları için saklama ufuklarını
belirler. Ufuklar GDPR Madde 5(1)(e) "saklama sınırlaması" ve Madde 17
"silme hakkı" tarihlerini onurlandırır. `enforce` anahtarı yalnız-loglama,
uyarı ve sert-engelleme modları arasında geçiş yaparak regüle edilen bir CI
kapısının saklama ufkunu eski bir çalışma alanını yeniden kullanarak sessizce
uzatmasını engeller.

| Alan | Tip | Varsayılan | Açıklama |
|------|-----|-----------|----------|
| `audit_log_retention_days` | int | `1825` (~5 yıl) | `audit_log.jsonl` dosyasının Madde 5(1)(e) kapsamında "geciken" olarak işaretlenmeden önce saklanacağı gün sayısı. `0` süresiz saklamayı belirtir (Madde 17(3)(b) savunması). |
| `staging_ttl_days` | int | `7` | `forgelm reject` kararından sonra `final_model.staging.<run_id>/` dizininin planlı temizlenmeden önce saklanacağı gün sayısı. `0` süresiz saklama anlamına gelir. v0.8.0'da kaldırılan `evaluation.staging_ttl_days` yerine geçer. |
| `ephemeral_artefact_retention_days` | int | `90` | Uyumluluk paketleri, veri denetim raporları ve diğer çalışma kapsamlı türetilmiş artefaktların saklanma süresi (gün). `0` süresiz saklama. |
| `raw_documents_retention_days` | int | `90` | İngest edilmiş ham belgelerin (PDF / DOCX / EPUB / TXT / Markdown) operatörün ingestion-output dizininde saklanma süresi (gün). `0` süresiz saklama. |
| `enforce` | string | `"log_only"` | Politika uygulama modu: `"log_only"` (yalnızca audit log), `"warn_on_excess"` (stderr'e yapılandırılmış uyarı), `"block_on_excess"` (`EXIT_EVAL_FAILURE` = 3 ile trainer ön-kontrolünü iptal eder). |

> **Kaldırıldı:** `evaluation.staging_ttl_days` (v0.5.5 itibarıyla kullanımdan
> kaldırılmıştı) v0.8.0'da kaldırılmıştır. Artık geçerli tek form
> `retention.staging_ttl_days`'dir. Eski anahtarı hâlâ set eden YAML dosyaları
> `EXIT_CONFIG_ERROR` ile config-load başarısızlığına yol açar.

---

## `compliance` (İsteğe bağlı — EU AI Act Madde 11 + Annex IV)

| Alan | Tip | Varsayılan | Açıklama |
|------|-----|-----------|----------|
| `provider_name` | string | `""` | Kuruluş adı |
| `provider_contact` | string | `""` | İletişim e-postası |
| `system_name` | string | `""` | Yapay zeka sistemi adı |
| `intended_purpose` | string | `""` | Modelin amacı |
| `known_limitations` | string | `""` | Kullanılmaması gereken durumlar |
| `system_version` | string | `""` | Sürüm tanımlayıcısı |
| `risk_classification` | string | `"minimal-risk"` | 5 EU AI Act `RiskTier` değerinden biri: `"unknown"` (sınıflandırma öncesi yer tutucu), `"minimal-risk"`, `"limited-risk"`, `"high-risk"` (Madde 6 — tam Annex IV dokümantasyonu), `"unacceptable"` (Madde 5 yasaklı uygulama — başlangıçta uyarı bandı yayınlar). |

> **Sıkı kapı:** `risk_classification`'ı (veya aşağıdaki kardeş alan `risk_assessment.risk_category`'yi) `"high-risk"` veya `"unacceptable"` olarak ayarlamak [`evaluation.safety.enabled: true`](#evaluationsafety-isteğe-bağlı) **gerektirir**. Atlanırsa config-load / `--dry-run` zamanında `ConfigError` (çıkış kodu 1) fırlatılır — EU AI Act Madde 9 risk-yönetimi kanıtı devre dışı bir safety eval'dan türetilemez.

## `risk_assessment` (İsteğe bağlı — EU AI Act Madde 9)

| Alan | Tip | Varsayılan | Açıklama |
|------|-----|-----------|----------|
| `intended_use` | string | `""` | Kullanım amacı |
| `foreseeable_misuse` | list | `[]` | Öngörülen kötüye kullanım senaryoları |
| `risk_category` | string | `"minimal-risk"` | `compliance.risk_classification` ile aynı 5 `RiskTier` değeri: `"unknown"`, `"minimal-risk"`, `"limited-risk"`, `"high-risk"`, `"unacceptable"`. Auto-revert eşiklerini ve Annex IV kapısını etkiler. |
| `mitigation_measures` | list | `[]` | Risk azaltma önlemleri |
| `vulnerable_groups_considered` | bool | `false` | Savunmasız gruplar üzerindeki etki değerlendirildi |

> **Sıkı kapı:** yukarıdaki [`compliance.risk_classification`](#compliance-isteğe-bağlı--eu-ai-act-madde-11--annex-iv) ile aynı — `risk_category`'i `"high-risk"` veya `"unacceptable"` olarak ayarlamak `evaluation.safety.enabled: true` gerektirir, aksi halde config-load `ConfigError` (çıkış kodu 1) fırlatır. Kapı her iki alan üzerinden OR'lanır: ikisinden biri sıkı bir tier'daysa kapı tetiklenir.

## `monitoring` (İsteğe bağlı — EU AI Act Madde 12+17)

| Alan | Tip | Varsayılan | Açıklama |
|------|-----|-----------|----------|
| `enabled` | bool | `false` | İzleme hook'larını etkinleştir |
| `endpoint` | string | `""` | İzleme webhook URL'si |
| `endpoint_env` | string | `null` | Endpoint için ortam değişkeni adı |
| `metrics_export` | string | `"none"` | `"none"`, `"prometheus"`, `"datadog"`, `"custom_webhook"` |
| `alert_on_drift` | bool | `true` | Model sapmasında uyar |
| `check_interval_hours` | int | `24` | İzleme kontrol aralığı (saat) |

## `distributed` (İsteğe bağlı)

| Alan | Tip | Varsayılan | Açıklama |
|------|-----|-----------|----------|
| `strategy` | string | `null` | `"deepspeed"` veya `"fsdp"` (null = tek GPU) |
| `deepspeed_config` | string | `null` | Ön ayar (`"zero2"`, `"zero3"`, `"zero3_offload"`) veya JSON yolu |
| `fsdp_strategy` | string | `"full_shard"` | `"full_shard"`, `"shard_grad_op"`, `"hybrid_shard"`, `"no_shard"` |
| `fsdp_auto_wrap` | bool | `true` | Transformer katmanlarını otomatik sar |
| `fsdp_offload` | bool | `false` | Parametreleri CPU'ya taşı |
| `fsdp_backward_prefetch` | string | `"backward_pre"` | `"backward_pre"` veya `"backward_post"` |
| `fsdp_state_dict_type` | string | `"FULL_STATE_DICT"` | `"FULL_STATE_DICT"` veya `"SHARDED_STATE_DICT"` |

## `synthetic` (İsteğe bağlı — Sentetik Veri Üretimi)

| Alan | Tip | Varsayılan | Açıklama |
|------|-----|-----------|----------|
| `enabled` | bool | `false` | Öğretmen → öğrenci sentetik veri üretimini etkinleştir. |
| `teacher_model` | string | `""` | HF Hub ID veya API model adı (ör. `gpt-4o`, `meta-llama/Llama-3-70B`). |
| `teacher_backend` | string | `"api"` | Şunlardan biri: `"api"` (OpenAI/Anthropic-uyumlu), `"local"` (HF in-process), `"file"` (önceden üretilmiş JSONL'i oku). |
| `api_base` | string | `""` | API endpoint, ör. `https://api.openai.com/v1` veya self-hosted vLLM gateway. |
| `api_key` | `Optional[str]` | `null` | Inline API anahtarı. Secret'ları commit'lememek için `api_key_env`'i tercih edin — inline set edildiğinde, serialize edilmiş config'te değer `***REDACTED***` olur. |
| `api_key_env` | `Optional[str]` | `null` | API anahtarını taşıyan env var adı (ör. `OPENAI_API_KEY`). |
| `api_delay` | float | `0.5` | Öğretmen çağrıları arası saniye (rate limiting). |
| `api_timeout` | int | `60` | Çağrı başına API timeout (saniye). |
| `seed_file` | string | `""` | Tohum prompt dosyası yolu (JSONL veya plain text, satır başı bir prompt). |
| `seed_prompts` | `List[str]` | `[]` | Inline tohum prompt'lar (`seed_file` alternatifi). |
| `system_prompt` | string | `""` | Her öğretmen çağrısının başına eklenen system prompt. |
| `max_new_tokens` | int | `1024` | Öğretmen yanıtı başına maksimum token. |
| `temperature` | float | `0.7` | Öğretmene geçirilen örnekleme sıcaklığı. |
| `output_file` | string | `"synthetic_data.jsonl"` | Çıktı JSONL dosya yolu. |
| `output_format` | string | `"messages"` | Şunlardan biri: `"messages"` (chat-style array), `"instruction"` (Alpaca-style), `"chatml"`, `"prompt_response"`. **`chatml`, ForgeLM'in eski `{User, Assistant}` anahtar düzenini üretir — OpenAI `<\|im_start\|>` ChatML işaretlemesini DEĞİL.** Taşınabilir bir sohbet formatı için `messages` kullanın. |
| `min_success_rate` | float | `0.0` | `forgelm --generate-data`'nin 0 çıkış kodu vermesi için seed prompt'ların başarılı olması gereken minimum oran (0.0–1.0). Varsayılan `0.0`, eski "sıfırdan farklı herhangi bir verim başarılıdır" davranışını korur; bir CI hattının neredeyse boş bir veri kümesiyle devam etmemesi için yükseltin. |
| `sanity_failure_rate` | float | `0.2` | `forgelm --generate-data`'nin, veri kümesinin küçük veya çarpık olabileceğine dair bir `WARNING` kaydettiği başarısızlık oranı eşiği (0.0–1.0) — çıkış kodunu belirleyen `min_success_rate`'ten bağımsızdır. Varsayılan `0.2`, prompt'ların %20'sinden fazlası başarısız olduğunda uyarır. |

---

## `webhook` (İsteğe bağlı)

| Alan | Tip | Varsayılan | Açıklama |
|------|-----|-----------|----------|
| `url` | string | `null` | Webhook hedef URL |
| `url_env` | string | `null` | URL'yi içeren ortam değişkeni adı |
| `notify_on_start` | bool | `true` | Eğitim başlangıcında bildir |
| `notify_on_success` | bool | `true` | Başarıda bildir |
| `notify_on_failure` | bool | `true` | Hata durumunda bildir |
| `timeout` | int | `10` | HTTP istek zaman aşımı (saniye). Notifier ≥ 1s'ye clamp'ler. v0.5.5'te varsayılan 10s'ye çıkarıldı (önceden 5s'di) — Slack/Teams gateway gecikme atışları production'da düzenli olarak 5s'yi aşıyor ve bir webhook zaman aşımı audit chain'i sessizce zayıflatıyor (webhook arızası best-effort). |
| `allow_private_destinations` | bool | `false` | RFC1918 / loopback / link-local hedeflere webhook gönderimine izin verir (cluster içi Slack proxy, on-prem Teams gateway gibi). Varsayılan yalnızca genel internet — SSRF koruması |
| `require_https` | bool | `false` | TLS-only zorlama. `true`, plaintext bir `http://` URL'ini reddeder (SSRF chokepoint raise eder; POST atlanır), warn-and-send yerine. Varsayılan `false`, warn-then-send davranışını korur |
| `tls_ca_bundle` | string | `null` | `requests`'e `verify=` olarak iletilen özel CA bundle yolu (örn. kurumsal MITM CA). Boşsa `certifi` paketinin gömülü deposu kullanılır |

## `merge` (İsteğe bağlı)

| Alan | Tip | Varsayılan | Açıklama |
|------|-----|-----------|----------|
| `enabled` | bool | `false` | Model birleştirmeyi etkinleştir |
| `method` | string | `"ties"` | `"ties"`, `"dare"`, `"slerp"`, `"linear"` |
| `models` | list | `[]` | `{path, weight}` sözlük listesi |
| `output_dir` | string | `"./merged_model"` | Çıktı dizini |
| `ties_trim_fraction` | float | `0.2` | TIES: görev başına kırpılan en küçük büyüklükteki delta'ların oranı (0.0–1.0). Yalnızca `method` `ties` olduğunda kullanılır. |
| `dare_drop_rate` | float | `0.3` | DARE: yeniden ölçeklemeden önce her delta'nın rastgele düşürülme olasılığı (0.0–1.0). Yalnızca `method` `dare` olduğunda kullanılır. |
| `dare_seed` | int | `42` | DARE: rastgele düşürme maskesi için RNG seed'i; bir birleştirme çalıştırmadan çalıştırmaya tekrarlanabilir olur. |

> `enabled: true`, `models` içinde her biri bir `path` anahtarı taşıyan en az iki girdi gerektirir — ikiden az kaynak model (veya `path` eksik bir girdi) içeren bir birleştirme config-load zamanında reddedilir.

> **TIES/DARE varsayılan hiperparametreleri kasıtlı olarak korumacıdır.**
> ForgeLM'in yerel `ties` birleştirmesi, ağırlıkların büyüklüğe göre alttaki
> **%20**'sini kırpar (üstteki %80'i tutar); `dare` birleştirmesi sabit bir
> seed ile `drop_rate=0.3` kullanır. Bu varsayılanlar, yayımlanmış TIES (üstteki
> ~%20'yi tut) ve DARE (`drop_rate` 0.9+) varsayılanlarından kasıtlı olarak daha
> korumacıdır — daha fazla sinyal tutarlar, böylece iki-adaptörlü bir birleştirme
> kutudan çıktığı haliyle daha az yıkıcıdır, ancak sonuç makaleye sadık bir
> birleştirmeden farklı olacaktır. Yayımlanmış seyreklik rejimlerine ihtiyaç
> duyan operatörler `ties_trim_fraction` / `dare_drop_rate` değerlerini
> yükseltebilir (veya mergekit gibi harici bir araçla birleştirebilir).

## `auth` (İsteğe bağlı)

| Alan | Tip | Varsayılan | Açıklama |
|------|-----|-----------|----------|
| `hf_token` | string | `null` | HuggingFace tokeni (tercih: `HUGGINGFACE_TOKEN` ortam değişkeni) |

---

## `pipeline` (İsteğe bağlı — Çok Aşamalı Eğitim Zincirleri, Faz 14)

2+ eğitim aşamasını (tipik olarak SFT → DPO → GRPO) tek bir config-tabanlı koşuda zincirler: otomatik zincirleme, aşama bazında kapılar, crash-safe resume ve zincir seviyesi Annex IV manifesti.  Atlandığında ForgeLM v0.6.0 tek-aşamalı koşusu ile byte-byte aynı davranır; orkestratör modülü import edilmez.  Operatör adım adım: [Çok Aşamalı Pipeline kılavuzu](../guides/pipeline-tr.md).

| Alan | Tip | Varsayılan | Açıklama |
|------|-----|-----------|----------|
| `output_dir` | string | `"./pipeline_run"` | Zincir seviyesi artefakların kök dizini: `pipeline_state.json`, `compliance/pipeline_manifest.json` ve pipeline-kapsamlı `audit_log.jsonl`.  Aşama bazında trainer artefaktları her aşamanın kendi `training.output_dir`'ı altında kalır. |
| `stages` | `List[PipelineStage]` | *zorunlu* (en az 1 aşama) | Sıralı aşama listesi.  Her aşamanın `model.name_or_path`'ı, aşama explicit `model:` bloğu vermediği sürece, önceki aşamanın `training.output_dir/final_model`'ına otomatik ayarlanır. |

### `pipeline.stages[].*` — PipelineStage alanları

`PipelineStage`, root config üzerine bindirilen aşama bazında bir override'dır.  Bölüm-toptan miras: bir blok atlanırsa root'un bloğu birebir miras alınır; blok verilirse root'unkini TAMAMEN değiştirir (deep-merge yok).

| Alan | Tip | Varsayılan | Açıklama |
|------|-----|-----------|----------|
| `name` | string | — (zorunlu) | `^[a-z0-9_]{1,32}$` deseniyle eşleşen aşama tanımlayıcısı.  Pipeline içinde benzersiz.  `--stage <ad>`, `--resume-from <ad>`, audit-log payload'larında ve aşama bazında manifest girdilerinde kullanılır. |
| `model` | `Optional[ModelConfig]` | `null` | Root `model:` bloğunun aşama bazında override'ı.  `null` iken önceki aşamanın `final_model`'ından otomatik zincirlenir (aşama 0 için root).  Set edildiğinde o aşama için otomatik zincirleme devre dışı (operatör kaçış kapısı). |
| `lora` | `Optional[LoraConfigModel]` | `null` | Aşama bazında LoRA config.  `null` ise root'tan toptan miras alınır. |
| `training` | `Optional[TrainingConfig]` | `null` | Aşama bazında training config.  `null` ise root'tan toptan miras alınır.  **Verildiğinde `trainer_type` AÇIKÇA SET EDİLMEK ZORUNDA** — her aşama hangi hizalama paradigmasını koştuğunu manifestte audit-clarity için kaydeder. |
| `data` | `Optional[DataConfig]` | `null` | Aşama bazında data config.  `null` ise root'tan toptan miras alınır; aşama bazında override norm — her aşama tipik olarak farklı bir dataset tüketir (SFT/DPO/preference/vb.). |
| `evaluation` | `Optional[EvaluationConfig]` | `null` | Aşama bazında kapılar (loss eşikleri, `auto_revert`, safety, judge, human-approval).  Her aşama kendi kapısını bağımsız konfigüre edebilir. |

Sadece-root bölümleri — **aşama seviyesinde reddedilir**, `EXIT_CONFIG_ERROR (1)`: `distributed`, `webhook`, `compliance`, `risk_assessment`, `monitoring`, `retention`, `synthetic`, `merge`, `auth`.  Bunlar pipeline seviyesi konulardır (distributed stratejisi koşu boyunca tutarlı kalır; compliance metadata tüm zinciri kapsar; vb.).

### Örnek

```yaml
# Root varsayılanları — blok atlayan aşamalarca miras alınır.
model: { name_or_path: "meta-llama/Llama-3-8B" }
lora: { r: 8, alpha: 16 }
training: { trainer_type: "sft", output_dir: "./placeholder" }
data: { dataset_name_or_path: "./placeholder.jsonl" }

pipeline:
  output_dir: "./pipeline_run"
  stages:
    - name: sft_stage
      training: { trainer_type: "sft", output_dir: "./pipeline_run/stage1_sft" }
      data: { dataset_name_or_path: "./data/sft.jsonl" }
    - name: dpo_stage
      training: { trainer_type: "dpo", output_dir: "./pipeline_run/stage2_dpo", dpo_beta: 0.1 }
      data: { dataset_name_or_path: "./data/preferences.jsonl" }
    - name: grpo_stage
      training: { trainer_type: "grpo", output_dir: "./pipeline_run/stage3_grpo" }
      data: { dataset_name_or_path: "./data/math_prompts.jsonl" }
```

### CLI yüzeyi

| Flag | Etki |
|------|------|
| `--stage <ad>` | Sadece adı verilen aşamayı yalıtılmış olarak koşar (audit / re-run senaryoları).  Önceki aşamanın disk üzerindeki çıktısından otomatik zincirler. |
| `--resume-from <ad>` | Adı verilen aşamadan itibaren devam eder; tamamlanmış (veya operatör tarafından onaylanmış gated) aşamalar disk üzerinde çıktıları varsa atlanır. |
| `--force-resume` | Resume sırasındaki `pipeline_config_hash` uyuşmazlığını kabul eder (log'lanır + `pipeline.force_resume` ile audit'lenir).  Aşama topoloji uyuşmazlığı (sayı / isim / sıra) bu flag'le bile reddedilir. |
| `--input-model <yol>` | Operatör kaçış kapısı — `--stage` hedefi için otomatik zincirlenen modeli override eder.  Audit log `input_source: cli_override` ile kaydedilir. |
| `--dry-run` | Her aşamanın merge edilmiş config'ini + cross-stage zincir bütünlüğünü + `training.output_dir` çakışma kontrolünü herhangi bir GPU tahsisi olmadan doğrular; tüm hataları çıkmadan önce toplar. |

`--fit-check`, `--merge`, `--generate-data`, `--compliance-export`, `--benchmark-only` flag'leri tek-aşama operasyonlarıdır ve `pipeline:` bloğu mevcut olduğunda dispatch zamanında reddedilir — ya `pipeline:` bloğunu kaldırın ya da flag'i kaldırın.

### Doğrulayıcı

```bash
forgelm verify-annex-iv --pipeline <pipeline.output_dir>
```

Zincir seviyesi manifestin yapısal alanlarını, zincir bütünlüğünü (her `input_source: chain` aşaması kendi önceki aşamasının `output_model`'ına eşleşir), aşama bazında `training_manifest.json` varlığını ve `stopped_at` / running-status tutarlılığını doğrular.  Temiz manifest için `0`, config / zincir ihlali için `1`, runtime I/O hatası için `2` ile çıkar.
