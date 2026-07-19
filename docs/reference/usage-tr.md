# Kullanım Rehberi

ForgeLM, komut satırı arayüzü üzerinden çalıştırılmak üzere tasarlanmıştır — hem yerel deneyler hem de otomatik CI/CD pipeline'ları için uygundur.

## Ön Koşullar

- Python 3.10+
- CUDA destekli NVIDIA GPU (önerilir; CPU modu çok yavaş)

```bash
git clone https://github.com/HodeTech/ForgeLM.git
cd ForgeLM
pip install -e .
```

### İsteğe bağlı kurulumlar

```bash
pip install -e ".[qlora]"        # 4-bit kuantizasyon (Linux)
pip install -e ".[unsloth]"      # Unsloth backend (Linux)
pip install -e ".[eval]"         # lm-evaluation-harness
pip install -e ".[tracking]"     # W&B deney takibi
pip install -e ".[distributed]"  # DeepSpeed çoklu GPU
pip install -e ".[merging]"      # native model birleştirme (TIES/DARE/SLERP — ekstra bağımlılık yok)
pip install -e ".[export]"       # GGUF export (llama-cpp-python, Windows-dışı)
pip install -e ".[chat]"         # forgelm chat'te Rich render'lama
```

## Kimlik Doğrulama

Gated modeller (Llama, Gemma) veya özel veri setleri için:

1. **Ortam Değişkeni** (önerilen): `export HUGGINGFACE_TOKEN="hf_xxxxx"`
2. **Config Dosyası**: YAML'da `auth: { hf_token: "hf_xxxxx" }`
3. **Yerel Cache**: `huggingface-cli login`

## CLI Referansı

### Temel Komutlar

```bash
forgelm --config my_config.yaml                                  # Model eğit
forgelm --wizard                                                 # Etkileşimli config oluşturucu
forgelm --wizard --wizard-start-from existing.yaml               # Mevcut bir config üzerinden iterate et
forgelm --config my_config.yaml --dry-run                        # Config doğrula (GPU gerektirmez)
forgelm --version                                                # Versiyon göster
```

### Çıktı ve Loglama

```bash
forgelm --config my_config.yaml --output-format json   # CI/CD için JSON çıktı
forgelm --config my_config.yaml --quiet                # Sadece uyarı/hata göster
forgelm --config my_config.yaml -q                     # Kısa form
forgelm --config my_config.yaml --log-level DEBUG      # Log seviyesi ayarla
```

### Eğitim Modları

```bash
forgelm --config my_config.yaml --resume               # Son checkpoint'ten devam
forgelm --config my_config.yaml --resume ./checkpoints/checkpoint-500
forgelm --config my_config.yaml --offline              # İzole mod (HF Hub yok)
```

### Çok Aşamalı Pipeline'lar (Faz 14, v0.7.0)

Config bir `pipeline:` bloğu taşıdığında aynı `forgelm --config ...` çağrısı tüm zinciri uçtan uca koşar (tipik olarak SFT → DPO → GRPO): otomatik zincirlenen model yolları ve tüm aşamaları kapsayan tek bir Annex IV manifesti ile.

```bash
# Tüm zinciri uçtan uca koş
forgelm --config pipeline.yaml

# Dry-run her aşamanın merge edilmiş config'ini + aşamalar arası zincir
# bütünlüğünü + aşama bazında training.output_dir çakışma kontrolünü GPU
# tahsisi yapmadan doğrular; tüm hataları çıkmadan önce toplar
forgelm --config pipeline.yaml --dry-run

# Tek bir adlı aşamayı yalıtılmış olarak yeniden koştur (audit / re-run
# senaryoları).  Önceki aşamanın disk üzerindeki output_dir/final_model'ından
# otomatik zincirler.
forgelm --config pipeline.yaml --stage dpo_stage

# Filtrelenen aşamanın otomatik zincirlenen giriş modelini override et
# (operatör kaçış kapısı — input_source: cli_override ile audit'lenir)
forgelm --config pipeline.yaml --stage dpo_stage --input-model ./other/checkpoint

# Adı verilen aşamadan itibaren devam et; tamamlanmış (veya operatör
# tarafından onaylanmış gated) aşamalar disk üzerinde çıktıları varsa
# INFO log seviyesinde atlanır
forgelm --config pipeline.yaml --resume-from dpo_stage

# Resume sırasındaki stale pipeline_config_hash'i kabul et (log'lanır +
# pipeline.force_resume eventi ile audit'lenir).  Aşama topoloji
# uyuşmazlığı (sayı / isim / sıra) bu flag'le bile reddedilir.
forgelm --config pipeline.yaml --resume-from dpo_stage --force-resume

# Biten bir pipeline'ın zincir seviyesi Annex IV manifestini doğrula
forgelm verify-annex-iv --pipeline ./pipeline_run
```

**Tek-aşama flag reddi:** Config bir `pipeline:` bloğu taşıdığında `--fit-check`, `--merge`, `--generate-data`, `--compliance-export`, `--benchmark-only` desteklenmez — ya bloğu kaldırın ya da flag'i kaldırın.  Tersine, `--stage`, `--resume-from`, `--force-resume`, `--input-model` `pipeline:` bloğu gerektirir — tek-aşama config'e karşı çalıştırıldıklarında flag'i sessizce yok saymak yerine `EXIT_CONFIG_ERROR (1)` ile çıkar.

Operatör adım adım: [Çok Aşamalı Pipeline kılavuzu](../guides/pipeline-tr.md).  Şema detayları: [`pipeline` config bloğu](configuration-tr.md#pipeline-isteğe-bağlı--çok-aşamalı-eğitim-zincirleri-faz-14).

### VRAM Fit Check

Eğitim öncesi config'inizin GPU belleğine sığıp sığmayacağını tahmin edin:

```bash
# Metin çıktı (insan-okunabilir verdict)
forgelm --config my_config.yaml --fit-check

# JSON çıktı (CI/CD pipeline'ları için)
forgelm --config my_config.yaml --fit-check --output-format json
```

Çıktı: tahmini tepe VRAM, kullanılabilir VRAM (GPU varsa), verdict (`FITS` / `TIGHT` / `OOM` / `UNKNOWN`) ve sıralı öneriler. GPU yoksa hipotetik moda düşer.

### Eğitim Sonrası: Chat, Export, Deploy

Eğitim sonrasında, fine-tuned modelinizle CLI'dan doğrudan etkileşime geçin ve dağıtın. Bu alt-komutlar `--config` olmadan çalışır.

```bash
# İnteraktif chat REPL (varsayılan: streaming)
forgelm chat ./checkpoints/final_model
forgelm chat ./checkpoints/final_model --adapter ./adapter
forgelm chat ./checkpoints/final_model --system "Yardımcı bir asistansın." --temperature 0.8

# GGUF'a export (Ollama, LM Studio, llama.cpp için)
# Gerekli: pip install forgelm[export]
forgelm export ./checkpoints/final_model --output model.gguf --quant q4_k_m
forgelm export ./checkpoints/final_model --output model.gguf --quant q8_0 --adapter ./adapter

# Deployment config dosyaları üret
forgelm deploy ./checkpoints/final_model --target ollama --output ./Modelfile
forgelm deploy ./checkpoints/final_model --target vllm --output ./vllm_config.yaml
forgelm deploy ./checkpoints/final_model --target tgi --output ./docker-compose.yaml
forgelm deploy ./checkpoints/final_model --target hf-endpoints --output ./endpoint.json
forgelm deploy ./checkpoints/final_model --target ollama --output ./Modelfile --system "Kısa konuş."
```

**Chat slash komutları:** `/reset`, `/save [file]`, `/temperature N`, `/system [prompt]`, `/help`, `/exit`

**Export kuantizasyon seviyeleri:** `q2_k`, `q3_k_m`, `q4_k_m` (önerilen), `q5_k_m`, `q8_0`, `f16`

### Sentetik Veri Üretimi

```bash
# Öğretmen model distillasyonu ile sentetik eğitim verisi üret
forgelm --config my_config.yaml --generate-data
```

Bu komut, eğitim başlamadan önce bir öğretmen modelden eğitim verisi üretmek için `synthetic` config bölümünü kullanır. Tüm sentetik veri seçenekleri için [Konfigürasyon Rehberi](configuration-tr.md)'ne bakın.

### Doküman Yutma (v0.5.0)

Ham PDF / DOCX / EPUB / TXT / Markdown'ı SFT'ye uygun JSONL'a dönüştürür. Opsiyonel bağımlılık: `pip install forgelm[ingestion]`. Ayrıntılar için [Doküman Yutma Rehberi](../guides/ingestion-tr.md).

```bash
# Tek dosya
forgelm ingest ./book.epub --output data/sft.jsonl

# Recursive dizin yürüyüşü + paragraf chunking
forgelm ingest ./policies/ --recursive --output data/policies.jsonl

# Kayan pencere + örtüşme (uzun teknik dokümanlar)
forgelm ingest ./scan.pdf --strategy sliding --chunk-size 1024 --overlap 128 \
  --output data/scan.jsonl

# Yazmadan önce PII'yi maskele
forgelm ingest ./customer_emails/ --pii-mask --output data/anon.jsonl

# Token-aware chunking — chunk'ları modelinizin vocab'ına göre boyutlandırır
forgelm ingest ./policies/ --recursive --output data/policies.jsonl \
  --chunk-tokens 1024 --tokenizer "Qwen/Qwen2.5-7B-Instruct"

# Teknik wiki / README'ler için markdown-aware splitter
forgelm ingest ./engineering_wiki/ --recursive --strategy markdown \
  --output data/wiki.jsonl

# Chunk'lar JSONL'a inmeden önce credential'ları temizle
forgelm ingest ./mixed_corpus/ --secrets-mask --output data/clean.jsonl

# Secrets + PII maskelemeyi birleştir (önce secrets, sonra PII —
# örtüşen span'ler iki kez sayılmasın)
forgelm ingest ./mixed_corpus/ --secrets-mask --pii-mask --output data/scrubbed.jsonl

# Yukarıdaki kombinasyonun tek-flag kısayolu
forgelm ingest ./mixed_corpus/ --all-mask --output data/scrubbed.jsonl
```

### Veri Seti Denetimi (v0.5.0)

CPU-only kalite + governance denetimi. `data_audit_report.json` üretir. Ayrıntılar için [Denetim Rehberi](../guides/data_audit-tr.md).

```bash
# Tek split (subcommand)
forgelm audit data/sft.jsonl --output ./audit/

# Çoklu split (train.jsonl / validation.jsonl / test.jsonl içeren dizin)
forgelm audit data/ --output ./audit/

# Tüm split'leri göster (bulgu olmasa bile)
forgelm audit data/ --verbose

# Özel Hamming eşiği
forgelm audit data/ --near-dup-threshold 5

# >50K satırlık korpuslar için MinHash LSH dedup
# Önce extra'yı kurun:  pip install forgelm[ingestion-scale]
forgelm audit data/large_corpus.jsonl --dedup-method minhash --jaccard-threshold 0.85

# Opt-in heuristik kalite filtresi (Gopher/C4 stili)
forgelm audit data/ --quality-filter

# Audit'in yanında bir Google Croissant 1.0 dataset card emit et
forgelm audit data/ --output ./audit/ --croissant

# Regex PII detector üzerine Presidio NER katmanı ekle
# (`[ingestion-pii-ml]` extra'sını gerektirir — person/organization/location kategorileri ekler)
forgelm audit data/ --output ./audit/ --pii-ml

# stdout'a makine-okunabilir özet
forgelm audit data/sft.jsonl --output ./audit/ --output-format json
```

Denetim şunları yakalar: split başına örnek sayısı + uzunluk dağılımı, top-3 dil tespiti, **LSH-banded** simhash near-duplicate oranı (uç eşiklerde brute-force fallback; opsiyonel **MinHash LSH** yolu `--dedup-method minhash` ile), cross-split sızıntı (sessiz train-test örtüşmesi), PII flag sayıları + **şiddet katmanları** (`pii_severity` bloğu her PII tipini critical / high / medium / low olarak puanlar ve bir `worst_tier` verdict yüzdürür), her zaman açık **credentials/secrets taraması** (`secrets_summary` — `forgelm.data_audit.SECRET_TYPES`'ta tanımlı dokuz aile: `aws_access_key`, `github_token`, `slack_token`, `openai_api_key`, `google_api_key`, `jwt`, `openssh_private_key` (tam BEGIN…END bloğu), `pgp_private_key` (tam BEGIN…END bloğu), `azure_storage_key`), ve opt-in **heuristik kalite filtresi** (`--quality-filter`, `quality_summary` bloğu üretir).

Trainer'ın `output_dir`'ünde `data_audit_report.json` mevcutsa, bulgular EU AI Act Madde 10 governance artifact'ında `data_audit` anahtarı altında otomatik olarak inline edilir.

### Değerlendirme, Birleştirme ve Uyumluluk

```bash
forgelm --config my_config.yaml --benchmark-only /path/to/model   # Sadece değerlendir
forgelm --config my_config.yaml --merge                            # Modelleri birleştir
forgelm --config my_config.yaml --compliance-export ./audit/       # Uyumluluk belgeleri
```

## Çıkış Kodları

| Kod | Anlam | CI/CD Aksiyonu |
|-----|-------|---------------|
| `0` | Başarı | Modeli deploy et |
| `1` | Config hatası | YAML'ı düzelt |
| `2` | Eğitim hatası | GPU/bellek/bağımlılıkları kontrol et |
| `3` | Değerlendirme hatası | Model eşiğin altında |
| `4` | Onay bekleniyor | İnsan incelemesi gerekli (`require_human_approval: true`) |
| `5` | Wizard iptal edildi | `forgelm --wizard` YAML üretmeden çıktı (Ctrl-C, non-tty reddi, kaydetmeyi reddetme) — `0`'dan ayrı ki CI "wizard tamamlandı" ile "wizard hiçbir şey yazmadı" arasını ayırt edebilsin |
| `6` | Bütünlük arızası | Yalnızca `verify-audit` / `verify-annex-iv` / `verify-gguf` / `verify-integrity`: hedef artefakt başarıyla okundu ve hash/zincir/manifest kontrolü başarısız oldu — operatör hatası değil, güvenlik olayı. Aynı subcommand'larda bir çağıran/girdi hatası (bozuk yol, geçersiz girdi) hâlâ `1` ile çıkar |

## Eğitim Çıktısı

```
checkpoints/
├── final_model/
│   ├── adapter_config.json
│   ├── adapter_model.safetensors
│   ├── tokenizer.json
│   ├── tokenizer_config.json
│   ├── README.md                    # Otomatik model kartı
│   ├── deployer_instructions.md     # Dağıtıcı rehberi (Madde 13)
│   └── model_integrity.json         # SHA-256 checksum'lar (Madde 15)
├── compliance/
│   ├── compliance_report.json       # Tam denetim izi (Annex IV)
│   ├── training_manifest.yaml       # Okunabilir özet
│   ├── data_provenance.json         # Veri seti parmak izleri
│   ├── risk_assessment.json         # Risk beyanı (Madde 9)
│   └── annex_iv_metadata.json       # Annex IV metadata
├── audit_log.jsonl                  # Yapılandırılmış olay logu (Madde 12)
├── safety/
│   ├── safety_results.json          # Güvenlik sonuçları (kategori, ciddiyet)
│   └── safety_trend.jsonl           # Çalışmalar arası trend
└── benchmark/                       # Benchmark sonuçları
```

## Günlükler ve İzleme

ForgeLM yapılandırılmış formatta stderr'e log yazar:
```text
2026-03-24 10:30:00 [INFO] forgelm.trainer: Starting training...
2026-03-24 11:45:00 [WARNING] forgelm.trainer: eval_steps (200) is larger than dataset (50 samples).
```

### TensorBoard

```bash
tensorboard --logdir=./checkpoints/runs/
```

### GaLore (Bellek Verimli Tam Parametre Eğitimi)

GaLore, LoRA'ya alternatif olarak optimizer seviyesinde bellek optimizasyonu sağlar ve gradient düşük rank projeksiyonu ile tam parametre eğitimine olanak tanır:

```yaml
training:
  galore_enabled: true
  galore_optim: "galore_adamw_8bit"
  galore_rank: 128
  galore_update_proj_gap: 200
  galore_scale: 0.25
  galore_proj_type: "std"
  galore_target_modules: ["q_proj", "k_proj", "v_proj", "o_proj"]
```

### Uzun Bağlam Eğitimi

RoPE ölçekleme, NEFTune gürültü enjeksiyonu, kayan pencere dikkat ve örnek paketleme ile genişletilmiş bağlam penceresi desteğini etkinleştirin:

```yaml
training:
  rope_scaling: {type: "linear", factor: 2.0}  # dict formu: type ∈ {"linear","dynamic","yarn","longrope"}, factor ≥ 1.0
  neftune_noise_alpha: 5.0            # Daha iyi genelleme için NEFTune gürültüsü
  sliding_window_attention: 4096      # Kayan pencere boyutu (token)
  packing: true                       # Kısa örnekleri tam uzunluklu dizilere paketle
```

### GPU Maliyet Tahmini

ForgeLM GPU modelinizi otomatik algılar (16 GPU modeli desteklenir; bkz. `forgelm.trainer.ForgeTrainer._GPU_PRICING`) ve eğitim çalışması başına tahmini maliyeti takip eder. Çıktı JSON sonuçlarına, webhook bildirimlerine ve model kartlarına dahil edilir:

```
GPU Maliyet Tahmini:
  GPU Modeli: NVIDIA A100 80GB
  GPU Saati: 2.4
  Tahmini Maliyet: $7.20 USD
  Tepe VRAM: 22.1 GB
```

Özel maliyet oranı belirlemek için:

```yaml
training:
  gpu_cost_per_hour: 3.00  # GPU-saat başına USD
```

### W&B

```yaml
training:
  report_to: "wandb"
  run_name: "my-experiment"
```

### Webhook Bildirimleri

```yaml
webhook:
  url_env: "FORGELM_WEBHOOK_URL"
  notify_on_start: true
  notify_on_success: true
  notify_on_failure: true
```

## Docker

```bash
docker build -t forgelm --build-arg INSTALL_EVAL=true .

docker run --gpus all \
  -v $(pwd)/config.yaml:/workspace/config.yaml:ro \
  -v $(pwd)/output:/workspace/output \
  forgelm --config /workspace/config.yaml

# Çoklu GPU
docker run --gpus all --shm-size=16g forgelm \
  torchrun --nproc_per_node=4 -m forgelm.cli --config /workspace/config.yaml
```
