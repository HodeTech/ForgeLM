# Hızlı Başlangıç Rehberi

İlk fine-tune'lu modelinizi 5 dakikada alın.

---

## Ön gereklilikler

- Python 3.10+
- CUDA destekli NVIDIA GPU (önerilen; CPU çalışır ama çok yavaş)

## 1. Kurulum

```bash
git clone https://github.com/HodeTech/ForgeLM.git
cd ForgeLM
pip install -e .

# Recommended: enable 4-bit quantization (Linux)
pip install -e ".[qlora]"
```

## 2. Config üret

### Seçenek 0: Tek-Komut Quickstart Şablonu (v0.4.5+)

En hızlı yol: bundled bir şablon seç, modeli, dataset'i ve muhafazakar
varsayılanları ForgeLM'e bırak.

```bash
# Bundled şablonları listele
forgelm quickstart --list

# customer-support asistanı için config (ve küçük bundled seed dataset) üret
forgelm quickstart customer-support --dry-run

# Uçtan uca: config render et, eğit, sonuçla sohbete düş
forgelm quickstart customer-support
```

Bundled şablonlar (hepsi varsayılan olarak QLoRA 4-bit, rank-8, batch=1
kullanır — tek 12 GB GPU'da güvenli):

| Şablon | Trainer | Ne alırsınız |
|---|---|---|
| `customer-support` | SFT | Kibar, marka-güvenli destek yanıtları |
| `code-assistant` | SFT | Kısa Python/programlama Q&A |
| `domain-expert` | SFT | Boş (BYOD — kendi JSONL'inizle eşleştirin) |
| `medical-qa-tr` | SFT | Türkçe tıbbi Q&A güvenlik uyarılarıyla |
| `grpo-math` | GRPO | İlkokul matematik akıl yürütme |

ForgeLM küçük GPU'larda modeli otomatik küçültür. Her şablonun göreve
seçilmiş kendi fallback'i vardır:

| Şablon | Birincil (≥10 GB VRAM) | Fallback (<10 GB) |
|---|---|---|
| `customer-support` | Qwen/Qwen2.5-7B-Instruct | HuggingFaceTB/SmolLM2-1.7B-Instruct |
| `code-assistant` | Qwen/Qwen2.5-Coder-7B-Instruct | Qwen/Qwen2.5-Coder-1.5B-Instruct |
| `domain-expert` | Qwen/Qwen2.5-7B-Instruct | HuggingFaceTB/SmolLM2-1.7B-Instruct |
| `medical-qa-tr` | Qwen/Qwen2.5-7B-Instruct | Qwen/Qwen2.5-1.5B-Instruct |
| `grpo-math` | Qwen/Qwen2.5-Math-7B-Instruct | Qwen/Qwen2.5-Math-1.5B-Instruct |

`--model your-org/your-model` ya da `--dataset path/to/your.jsonl` ile
override edin.

Bundled seed dataset'lerin lisansları için bkz. [LICENSES.md](https://github.com/HodeTech/ForgeLM/blob/main/forgelm/templates/LICENSES.md)
(CC-BY-SA 4.0, yazar-orijinal).

### Seçenek A: Etkileşimli Sihirbaz

```bash
forgelm --wizard
```

Sihirbaz önce curated quickstart-template kısayolu önerir; reddedilirse 9 adımlı etkileşimli akış açılır (welcome / use-case / model / strategy / trainer / dataset / training-params / compliance / operations) ve her `ForgeConfig` bloğunu kapsar — model, LoRA / DoRA / PiSSA / rsLoRA / GaLore stratejisi, trainer-spesifik hyperparam'lar (`dpo_beta` / `simpo_*` / `kto_beta` / `orpo_beta` / `grpo_*`), EU AI Act Madde 9 / 10 / 11 / 12+17 uyumluluk metadata, retention, monitoring, evaluation kapıları, webhook, sentetik veri — ve kullanıma-hazır bir YAML yazar. Geri dönmek için `back` / `b`, sıfırlamak için `reset` / `r`; state `~/.cache/forgelm/wizard_state.yaml`'da persistent, Ctrl-C / yeni oturum kaldığı yerden devam edebilir.

Review-cycle 2 (2026-05-09) ile eklenen operatör guardrail'ları: sihirbaz çıkmadan önce kayıtlı YAML'ı `ForgeConfig.model_validate` ile doğrular (schema reddetmeleri 30 dakika sonra eğitim sırasında değil, anında yüzeylenir), mevcut config dosyasını üzerine yazmadan önce sorar (reddederseniz `_2.yaml` / `_3.yaml` ile auto-suffix'ler), non-tty stdin altında çalışmayı reddeder (scriptli koşumlar için `forgelm quickstart <template>` kullanın), pre-flight checklist (GPU/VRAM/dataset/risk-tier sinyalleri) basar ve Ctrl-C / cancel durumunda `EXIT_WIZARD_CANCELLED = 5` ile çıkar — böylece CI "sihirbaz tamamlandı" ile "sihirbaz hiçbir şey yazmadı"yı ayırt edebilir.

**Idempotent yeniden koşum (PR-D, 2026-05-09):** mevcut bir config üzerinde önceki cevapları kaybetmeden iterate etmek için `--wizard-start-from` kullanın:

```bash
forgelm --wizard --wizard-start-from my_config.yaml
```

Sihirbaz YAML'ı okur, `ForgeConfig` ile önden doğrular (schema reddetmesi anında patlar) ve her adımın prompt'larını yüklenmiş değerlerle besler — her sayısal / metin prompt'unda Enter'a basmak mevcut değeri korur. Choice prompt'ları (Strategy, Target modules, Trainer, Use-case) yüklenen değeri tespit edip default index'i ona göre kaydırır, böylece Enter yine korur. Use-case step'i (Adım 2), mevcut model + trainer seçimi tespit edildiğinde **tamamen atlanır** — ilk template preset'inin bunları override etmesini önlemek için. Save akışı aynı path'e overwrite'a default'lar; mevcut overwrite confirmation üzerine yazmadan önce hâlâ tetiklenir.

**Dikkat:** prompt edilmeyen Annex IV / risk-assessment alanları (örn. `lora.dropout`, `lora.bias`, `lora.task_type`) artık override edilmek yerine `setdefault` pattern'iyle korunuyor — review-cycle 4 (PR-E, 2026-05-09) bu regression'ı kapattı.

### Seçenek B: Şablon Kopyala

```bash
cp config_template.yaml my_config.yaml
```

`my_config.yaml`'i düzenle — minimum şunları set'le:

```yaml
model:
  name_or_path: "HuggingFaceTB/SmolLM2-1.7B-Instruct"  # ya da kendi modeliniz

data:
  dataset_name_or_path: "timdettmers/openassistant-guanaco"  # ya da kendi dataset'iniz
```

### Seçenek C: Ham dokümanım var (PDF / DOCX / EPUB), JSONL değil

Önce Phase 11 ingestion + audit pipeline'ı koşturun, ardından yukarıdaki
seçeneklerden birini ortaya çıkan JSONL'ye yönlendirin:

```bash
pip install -e ".[ingestion]"
forgelm ingest ./policies/ --recursive --output data/policies.jsonl
forgelm audit data/policies.jsonl --output ./audit/
# Şimdi `data/policies.jsonl` config'e takılmaya hazır.
```

Chunking stratejileri, PII maskeleme ve audit'in yüzeylediği yönetişim
sinyalleri için bkz. [Doküman Ingestion Rehberi](ingestion-tr.md) ve
[Veri Seti Audit Rehberi](data_audit-tr.md).

## 3. Doğrula (Dry Run)

```bash
forgelm --config my_config.yaml --dry-run
```

Bu, config'inizi doğrular, model/dataset erişilebilirliğini kontrol eder
ve çözümlenmiş tüm parametreleri gösterir — hiçbir ağır şey indirmeden.

Makine-okunabilir çıktı için:

```bash
forgelm --config my_config.yaml --dry-run --output-format json
```

## 4. Eğit

```bash
forgelm --config my_config.yaml
```

Hepsi bu kadar. ForgeLM şunları halleder:

- Model indirme ve quantization
- Chat template'leriyle dataset formatlama
- LoRA adapter kurulumu
- Erken durdurma ile eğitim
- Değerlendirme ve model kaydetme
- Model kartı üretimi

## 5. Modelinizi bulun

Eğitim sonrası adapter'ınız şuraya kaydedilir:

```text
./checkpoints/final_model/
├── adapter_config.json
├── adapter_model.safetensors
├── tokenizer.json
├── tokenizer_config.json
└── README.md  (otomatik üretilen model kartı)
```

## 5.5 Eğitim öncesi GPU belleğini kontrol edin

Uzun bir koşum başlatmadan önce config'inizin GPU belleğine sığıp
sığmadığını tahmin edin:

```bash
forgelm --config my_config.yaml --fit-check
# GPU: RTX 3060 12GB — Estimated peak: 10.8 GB — Verdict: FITS
# Or: Verdict: TIGHT — Enable gradient checkpointing and reduce batch size
# Or: Verdict: UNKNOWN — No GPU detected (hypothetical estimate)
```

Çıktı bir döküm (taban ağırlıklar, LoRA adapter, optimizer state,
aktivasyonlar) ve bellek darken sıralı öneriler içerir. CI/CD
entegrasyonu için `--output-format json` kullanın.

Eğitim sırasında OOM alırsanız, [Troubleshooting rehberinin](troubleshooting-tr.md)
detaylı çözümleri vardır.

## 6. Modelinizi kullanın

```python
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer

base = AutoModelForCausalLM.from_pretrained("HuggingFaceTB/SmolLM2-1.7B-Instruct")
model = PeftModel.from_pretrained(base, "./checkpoints/final_model")
tokenizer = AutoTokenizer.from_pretrained("./checkpoints/final_model")

inputs = tokenizer("ForgeLM nedir?", return_tensors="pt")
output = model.generate(**inputs, max_new_tokens=200)
print(tokenizer.decode(output[0], skip_special_tokens=True))
```

### Modelinizi kullanma (v0.4.0+)

Eğitilmiş modelinizle doğrudan etkileşim kurun ve deploy edin:

```bash
# Fine-tune'lu modelinizle sohbet (varsayılan streaming)
forgelm chat ./checkpoints/final_model

# GGUF'a export (Ollama, LM Studio, llama.cpp için)
# Gerekli: pip install forgelm[export]
forgelm export ./checkpoints/final_model --output model.gguf --quant q4_k_m

# Deployment config'leri üret (sunucu başlatılmaz)
forgelm deploy ./checkpoints/final_model --target ollama --output ./Modelfile
forgelm deploy ./checkpoints/final_model --target vllm --output ./vllm_config.yaml
```

---

## Sık config ayarlamaları

### Daha hızlı eğitim için Unsloth kullan (yalnız Linux)

```bash
pip install -e ".[unsloth]"
```

```yaml
model:
  backend: "unsloth"
```

### Aynı rank'ta daha iyi kalite için DoRA'yı aç

```yaml
lora:
  method: "dora"  # DoRA adapter (aynı rank'ta standart LoRA'dan daha iyi kalite)
  # Not: lora.use_dora deprecated; bunun yerine method: "dora" kullanın
```

### Webhook bildirimleri ekle (Slack/Teams)

```yaml
webhook:
  url_env: "FORGELM_WEBHOOK_URL"
  notify_on_start: true
  notify_on_success: true
  notify_on_failure: true
```

```bash
export FORGELM_WEBHOOK_URL="https://hooks.slack.com/services/T.../B.../xxx"
forgelm --config my_config.yaml
```

### OOM recovery'i aç (otomatik batch size azaltma)

```yaml
training:
  per_device_train_batch_size: 8
  gradient_accumulation_steps: 2
  oom_recovery: true
  oom_recovery_min_batch_size: 1
```

### Kötü modelleri otomatik geri al

```yaml
evaluation:
  auto_revert: true
  max_acceptable_loss: 2.0
```

Fine-tune'lu modelin eval loss'u eşiği aşarsa, ForgeLM adapter'ı
otomatik siler ve kod 3 ile çıkar.

---

### Bellek-verimli tam-parametre eğitimi için GaLore'u aç

GaLore, gradient low-rank projection ile tam-parametre eğitimini
mümkün kılan, çok daha az bellek kullanan bir LoRA alternatifidir:

```yaml
training:
  galore_enabled: true
  galore_optim: "galore_adamw_8bit"
  galore_rank: 128
```

### Sentetik eğitim verisi üret

Fine-tuning'den önce eğitim verisi üretmek için bir teacher modeli
kullanın:

```bash
forgelm --config my_config.yaml --generate-data
```

```yaml
synthetic:
  enabled: true
  teacher_model: "gpt-4o"
  teacher_backend: "api"
  api_key_env: "OPENAI_API_KEY"
  api_base: "https://api.openai.com/v1"
  seed_file: "seed_prompts.jsonl"
  output_file: "synthetic_data.jsonl"
  output_format: "messages"
```

Sentetik satır sayısı seed-file boyutu ile kontrol edilir (seed başına
bir teacher çağrısı); tam alan seti için `forgelm/config.py` içindeki
`SyntheticConfig` Pydantic modeline bakın
([repo-arama](https://github.com/HodeTech/ForgeLM/search?q=class+SyntheticConfig)).

---

## Sonraki adımlar

- [CI/CD Pipeline Entegrasyonu](cicd_pipeline-tr.md) — pipeline'ınızda eğitimi otomatikleştir
- [Hizalama Rehberi](alignment-tr.md) — DPO, SimPO, KTO, GRPO
- [Kurumsal Dağıtım (İngilizce)](enterprise_deployment.md) — Docker, offline, multi-GPU
- [Güvenlik & Uyumluluk](safety_compliance-tr.md) — EU AI Act, güvenlik değerlendirmesi
- [Troubleshooting](troubleshooting-tr.md) — sık sorunlar ve çözümler

### Çalıştırılabilir notebook'lar (Colab)

- [Hızlı Başlangıç — SFT](../../notebooks/quickstart_sft.ipynb)
- [Eğitim-sonrası iş akışı](../../notebooks/post_training_workflow.ipynb) — `--fit-check` → `chat` → `export` → `deploy`
- [Çoklu-Veri Eğitimi](../../notebooks/multi_dataset.ipynb), [GaLore Bellek Optimizasyonu](../../notebooks/galore_memory_optimization.ipynb), [Sentetik Veri Pipeline'ı](../../notebooks/synthetic_data_training.ipynb)
- [Güvenlik Değerlendirme & Red-Teaming](../../notebooks/safety_evaluation.ipynb)
- Hizalama: [DPO](../../notebooks/dpo_alignment.ipynb), [KTO](../../notebooks/kto_binary_feedback.ipynb), [GRPO](../../notebooks/grpo_reasoning.ipynb)
