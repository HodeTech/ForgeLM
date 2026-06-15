# ForgeLM Mimarisi

ForgeLM modülerlik ve genişletilebilirlik gözetilerek tasarlanmıştır. İş akışı her biri ayrı bir modül tarafından yönetilen aşamalara bölünmüştür.

## Sistem Genel Bakışı

```
forgelm --config job.yaml
    │
    ├── cli/                → CLI paketi (Faz 15 split)
    │   ├── _parser.py          → 19 subcommand + global flag
    │   ├── _dispatch.py        → Mod yönlendirici
    │   ├── _exit_codes.py      → 0/1/2/3/4/5 sözleşmesi
    │   └── subcommands/        → Subcommand-başına handler'lar
    │       ├── ingest, audit, chat, export, deploy, doctor,
    │       │   cache, purge, reverse_pii, approve, approvals,
    │       │   safety_eval, verify_audit, verify_annex_iv,
    │       │   verify_gguf, verify_integrity, quickstart
    ├── config.py           → Pydantic doğrulama (23 config modeli)
    ├── utils.py            → HF kimlik doğrulama
    ├── model.py            → Model + tokenizer + LoRA/PEFT yükleme
    ├── data.py             → Veri seti yükleme + formatlama
    ├── data_audit/         → Audit paketi (Faz 14 split)
    │   ├── _orchestrator, _aggregator, _streaming, _simhash,
    │   │   _minhash, _pii_regex, _pii_ml, _secrets, _quality,
    │   │   _croissant, _summary, _splits
    ├── trainer.py          → Eğitim (TRL üzerinden 6 trainer tipi)
    │   ├── benchmark.py        → lm-eval-harness değerlendirme
    │   ├── safety.py           → Güvenlik kontrolü (confidence, S1-S14, ciddiyet)
    │   ├── judge.py            → LLM-Hakim puanlama
    │   ├── model_card.py       → HF model kartı üretimi
    │   ├── compliance.py       → EU AI Act denetim belgeleri + audit log
    │   └── webhook.py          → Slack/Teams bildirimleri
    ├── merging.py          → TIES/DARE/SLERP model birleştirme
    ├── synthetic.py        → Sentetik veri üretimi
    └── wizard/             → Etkileşimli config üretici (alt-paket, Faz 22)
```

## Dizin Yapısı

```
ForgeLM/
├── forgelm/                  # Çekirdek Python paketi (~22 tek-dosya modül + 2 alt-paket)
│   ├── __init__.py           # Hızlı CLI başlatma için lazy import
│   ├── cli/                  # CLI alt-paketi (Faz 15 split)
│   │   ├── _parser.py            # 19 subcommand + global flag
│   │   ├── _dispatch.py          # Mod yönlendirici
│   │   ├── _exit_codes.py        # Public 0/1/2/3/4/5 sözleşmesi
│   │   └── subcommands/          # Per-subcommand handler modülleri
│   │       └── _audit, _ingest, _chat, _export, _deploy, _doctor,
│   │           _cache, _purge, _reverse_pii, _approve, _approvals,
│   │           _safety_eval, _verify_audit, _verify_annex_iv,
│   │           _verify_gguf, _verify_integrity, _quickstart
│   ├── data_audit/           # Audit alt-paketi (Faz 14 split)
│   │   └── _orchestrator, _aggregator, _streaming, _simhash,
│   │       _minhash, _pii_regex, _pii_ml, _secrets, _quality,
│   │       _croissant, _summary, _splits, _types, _optional
│   ├── config.py             # 23 Pydantic config modeli
│   ├── data.py               # Veri yükleme (SFT/DPO/KTO/GRPO/multimodal)
│   ├── ingestion.py          # Ham doküman → SFT JSONL (PDF/DOCX/EPUB/TXT/Markdown)
│   ├── model.py              # Model + LoRA/DoRA/PiSSA + MoE algılama
│   ├── trainer.py            # Eğitim orkestrasyonu (6 trainer tipi)
│   ├── inference.py          # Paylaşılan çıkarım ilkelleri (yükleme/üretme/akış)
│   ├── chat.py               # Etkileşimli terminal REPL
│   ├── export.py             # GGUF dışa aktarma (llama-cpp-python)
│   ├── fit_check.py          # Ön uçuş VRAM tahmincisi
│   ├── deploy.py             # Dağıtım config üreteci (Ollama/vLLM/TGI/HF Endpoints)
│   ├── results.py            # TrainResult dataclass
│   ├── benchmark.py          # lm-evaluation-harness entegrasyonu
│   ├── safety.py             # Güvenlik değerlendirme (Llama Guard, S1-S14)
│   ├── judge.py              # LLM-Hakim (API + yerel)
│   ├── compliance.py         # EU AI Act uyumluluk + AuditLogger + kaynak takibi
│   ├── model_card.py         # HF uyumlu model kartı üretimi
│   ├── merging.py            # Model birleştirme (TIES/DARE/SLERP/linear)
│   ├── synthetic.py          # Sentetik veri üretimi (öğretmen→öğrenci)
│   ├── grpo_rewards.py       # Yerleşik GRPO format/uzunluk ödül şekillendiricileri
│   ├── quickstart.py         # Tek komutlu hazır şablonlar
│   ├── wizard/               # Etkileşimli yapılandırma sihirbazı (alt-paket — Faz 22)
│   ├── webhook.py            # Webhook bildirimleri (Slack/Teams)
│   ├── _http.py              # SSRF-korumalı HTTP chokepoint
│   ├── _version.py           # __version__ + __api_version__ (decoupled)
│   └── utils.py              # Kimlik doğrulama + checkpoint yönetimi
├── forgelm/templates/        # 5 quickstart şablon paketi
├── configs/
│   ├── deepspeed/            # ZeRO-2, ZeRO-3, ZeRO-3+Offload ön ayarları
│   └── safety_prompts/       # Yerleşik adversarial prompt kütüphanesi (140 prompt, 6 kategori)
├── notebooks/                # 10 Colab-uyumlu Jupyter notebook
├── tests/                    # ~70 test modülü
├── tools/                    # CI guard'ları: bilingual_parity, anchor_resolution,
│                              # cli_help_consistency, yaml_snippets,
│                              # audit_event_catalog, library_api_doc,
│                              # doc_numerical_claims, bilingual_code_blocks
├── docs/
│   ├── guides/               # Kullanıcı rehberleri (ingest, audit, alignment, CI/CD, …)
│   └── qms/                  # EU AI Act QMS SOP şablonları
├── Dockerfile                # Çok aşamalı Docker yapısı
├── docker-compose.yaml       # Eğitim + TensorBoard servisleri
└── config_template.yaml      # Açıklamalı config örneği
```

## Bileşen Detayları

### `cli/`
Orkestratör (Faz 15 split). `_parser.py` 19 subcommand'ı (`audit`, `approve`, `approvals`, `reject`, `cache-models`, `cache-tasks`, `chat`, `deploy`, `doctor`, `export`, `ingest`, `purge`, `quickstart`, `reverse-pii`, `safety-eval`, `verify-annex-iv`, `verify-audit`, `verify-gguf`, `verify-integrity`) artı eski training-mode flag setini kaydeder. `_dispatch.py` `subcommands/` altındaki uygun handler'a yönlendirir. `_exit_codes.py` public 0/1/2/3/4/5 sözleşmesini tanımlar (5 = sihirbaz iptal edildi).

### `config.py`
23 Pydantic v2 modeli: ModelConfig, LoraConfigModel, TrainingConfig, DataConfig, DataGovernanceConfig, EvaluationConfig, SafetyConfig, BenchmarkConfig, JudgeConfig, WebhookConfig, DistributedConfig, MergeConfig, ComplianceMetadataConfig, RetentionConfig, RiskAssessmentConfig, MonitoringConfig, MoeConfig, MultimodalConfig, AuthConfig, SyntheticConfig, PipelineStage, PipelineConfig + üst-düzey ForgeConfig. Çapraz alan doğrulaması içerir.

### `data.py`
HuggingFace `datasets` kütüphanesi ile arayüz. Veri formatını otomatik algılar (SFT, DPO, KTO, GRPO, multimodal) ve uyumsuzlukta önerili trainer_type ile hata verir. Mix ratio ile çoklu veri seti karıştırma. `tokenizer.apply_chat_template()` ile sohbet şablonları.

### `model.py`
Transformers veya Unsloth backend ile model yükleme. QLoRA (4-bit NF4), PEFT (LoRA, DoRA, PiSSA, rsLoRA), MoE expert kuantizasyon/seçimi. Dağıtık-bilinçli (DeepSpeed/FSDP). Multimodal-bilinçli (AutoProcessor).

### `trainer.py`
TRL trainer'larını (SFTTrainer, DPOTrainer, KTOTrainer, ORPOTrainer, CPOTrainer/SimPO, GRPOTrainer) sarar. Pipeline: baseline → eğitim → kayıp → benchmark → güvenlik → LLM-hakim → model kaydet → model kartı → uyumluluk → webhook. GaLore optimizer seviyesinde bellek optimizasyonu (tam parametre eğitimi için gradient düşük rank projeksiyonu) ve uzun bağlam özellikleri (RoPE ölçekleme, NEFTune gürültü enjeksiyonu, kayan pencere dikkat, örnek paketleme) destekler. Otomatik geri alma, insan onay kapısı, denetim loglama, kaynak takibi içerir.

### `results.py`
Hafif `TrainResult` dataclass — torch/transformers olmadan import edilebilir. Başarı durumu, metrikler, benchmark skorları, kaynak kullanımı, güvenlik pass/fail ve hakim skorlarını taşır.

### `benchmark.py`
EleutherAI `lm-evaluation-harness`'i sarar. Yapılandırılabilir benchmark görevleri çalıştırır, accuracy metriklerini çıkarır, `min_score` eşiğini uygular ve sonuçları kaydeder. Opsiyonel bağımlılık: `pip install forgelm[eval]`.

### `safety.py`
İki puanlama modu: binary (güvenli/güvensiz oranı) ve confidence-weighted (sınıflandırıcı güven skoru). 3 katmanlı güvenlik kapısı: binary oran → confidence skoru → ciddiyet eşiği. Llama Guard S1-S14 zarar kategorileri, ciddiyet seviyeleri (kritik/yüksek/orta/düşük), düşük güven uyarıları, çalışmalar arası trend takibi (safety_trend.jsonl).

### `judge.py`
LLM-as-Judge değerlendirme: API tabanlı hakimler (OpenAI-uyumlu endpoint) ve yerel model hakimleri destekler. Markdown code block çıkarmalı sağlam JSON parse'i içerir. 1-10 ölçeğinde puanlar; yapılandırılabilir minimum eşik.

### `compliance.py`
EU AI Act uyumluluk motoru — Madde 9-17:
- `AuditLogger`: Benzersiz run_id ile ekleme-yalnızca JSON Lines olay logu (Madde 12)
- `generate_training_manifest()`: Annex IV metadata (Madde 11)
- `generate_data_governance_report()`: Veri kalitesi istatistikleri (Madde 10)
- `generate_model_integrity()`: SHA-256 checksum'lar (Madde 15)
- `generate_deployer_instructions()`: Dağıtıcı talimatları (Madde 13)
- `export_evidence_bundle()`: Denetçiler için ZIP arşivi

### `model_card.py`
HuggingFace-uyumlu README.md üretir: YAML front-matter, eğitim parametreleri tablosu, metrikler, benchmark sonuçları, config snippet ve kullanım örneği.  Auth token'ları export edilen config'ten dışlar.

### `merging.py`
4 strateji: linear interpolasyon, TIES-Merging, DARE, SLERP. State dict seviyesinde — mergekit bağımlılığı gerektirmez.

### `synthetic.py`
Öğretmen-öğrenci distillasyonu ile sentetik veri üretimi. `SyntheticDataGenerator` sınıfı bir öğretmen modeli (API tabanlı veya yerel) alır, tohum promptlardan eğitim örnekleri üretir ve formatlanmış JSONL veri setleri çıktılar. `--generate-data` CLI flag'i veya `synthetic` config bölümü ile tetiklenir. Yapılandırılabilir öğretmen backend'leri, çıktı formatları ve üretim parametrelerini destekler.

### `wizard/`

Etkileşimli CLI sihirbazı. Faz 22 modernizasyonu (2026-05-08) ile `site/js/wizard.js`'e parite getirildi: 9-adımlı state machine (welcome / use-case / model / strategy / trainer / dataset / training-params / compliance / operations), trainer-spesifik hyperparam'lar (`dpo_beta` / `simpo_beta` + `simpo_gamma` / `kto_beta` / `orpo_beta` / `grpo_*`), tam PEFT method kapsamı (`lora` / `dora` / `pissa` / `rslora`) + GaLore ayrı axis, EU AI Act Madde 9 + 10 + 11 + 12+17 uyumluluk akordeon'ları, F-compliance-110 strict-tier auto-coercion, `back` / `reset` navigasyon, XDG-aware persistence (`$XDG_CACHE_HOME/forgelm/wizard_state.yaml`), step-diff preview, beginner / expert toggle ve Phase 11.5 / 12.5 BYOD inline ingest + audit helper'ları (`_offer_ingest_for_directory`, `_offer_audit_for_jsonl`).

### `webhook.py`
Eğitim başlangıcı/başarı/başarısızlık durumlarında yapılandırılmış JSON payload gönderir. Yapılandırılabilir timeout ile güvenilir hata yönetimi.

### `utils.py`
HuggingFace kimlik doğrulama (config, env var, yerel cache — modern XDG yolu). Checkpoint yönetimi (tutma, silme, UUID-ekli sıkıştırma).
