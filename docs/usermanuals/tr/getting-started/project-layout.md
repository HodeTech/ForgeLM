---
title: Proje Yapısı
description: ForgeLM diskte config, veri, checkpoint ve artifact'ları nasıl düzenler.
---

# Proje Yapısı

ForgeLM dosyaların *nereye* gideceği konusunda görüş bildirir. Bu, koşuları yeniden üretilebilir, CI/CD hatlarını öngörülebilir ve audit reviewer'ları memnun eder. Bu yapıya birebir uymak zorunda değilsiniz — ama her override için bir flag vardır ve varsayılanlar aşağıdaki konvansiyonları kabul eder.

## Tipik bir proje ağacı

```text
my-finetune/
├── configs/                       — YAML konfigürasyon dosyaları
│   ├── customer-support.yaml      — trainer config'iniz
│   └── customer-support.dev.yaml  — iterasyon için hızlı varyant
├── data/                          — JSONL veri setleri
│   ├── train.jsonl
│   ├── validation.jsonl
│   └── preferences.jsonl          — DPO/SimPO chosen-rejected çiftleri
├── audit/                         — `forgelm audit` çıktısı
│   └── data_audit_report.json
├── checkpoints/                   — model çıktıları (gitignored)
│   └── customer-support/
│       ├── audit_log.jsonl        — append-only, hash zincirli olay günlüğü
│       ├── compliance/            — Article 10/11 kanıtı (JSON + YAML)
│       └── final_model/           — ağırlıklar, model card (README.md), model_integrity.json
├── ingested/                      — `forgelm ingest` üzerinden ham döküman → JSONL
└── .forgelm/                      — yerel cache (gitignored)
```

## Hangi komut nereye yazar

| Komut | Yazdığı yer | Notlar |
|---|---|---|
| `forgelm ingest` | `--output` (genelde `data/*.jsonl`) | Ham döküman → SFT-hazır JSONL. |
| `forgelm audit` | `--output` (genelde `audit/`) | PII / sızıntı / kalite raporu. |
| `forgelm --config X.yaml` | YAML'daki `training.output_dir` | Tüm eğitim artifact'ları. |
| `forgelm export` | `--output` (`.gguf` yolu) | Kuantize tek-dosya model. |
| `forgelm deploy` | `--output` (Modelfile, K8s manifest vb.) | Deployment iskeletleri. |
| `forgelm chat` | hiçbir yere (interaktif) | Terminal'e akıtır. |

## Neyi commit'lemek, neyi gitignore'a almak

:::tip
ForgeLM varsayılanları doğru şeyleri commit'leyip gerisini ignore'larsanız version control'la iyi geçinir.
:::

**Commit:**
- `configs/*.yaml` — koşularınız konfigürasyon; YAML kaynak doğrudur.
- `audit/*.json` — küçük, makine-okunur, PR review'larında faydalı ("audit numaraları geriledi mi?").
- `checkpoints/*/final_model/README.md` ve `checkpoints/*/compliance/` (model card ve compliance kanıtı, ikisi de küçük).

**Gitignore:**
- `data/` — genelde fazla büyük; DVC veya tercih ettiğiniz dataset registry'sine alın.
- `checkpoints/*/final_model/*.safetensors`, `checkpoint-*` dizinleri ve diğer ağırlık dosyaları — git için fazla büyük; HuggingFace Hub veya model registry'ye gönderin.
- `ingested/` — ham dokümanlardan yeniden inşa edilebilir.
- `.forgelm/` — yerel cache.

Başlangıç `.gitignore`:

```gitignore
# ForgeLM defaults
checkpoints/*/adapter_model.safetensors
checkpoints/*/*.safetensors
checkpoints/*/pytorch_model.bin
checkpoints/*/optimizer.pt
data/
ingested/
.forgelm/
```

## Repo konvansiyonları

| Konvansiyon | Varsayılan | Override |
|---|---|---|
| Config dosyası | `configs/<isim>.yaml` | `--config PATH` |
| Audit çıktısı | `./audit/` | `forgelm audit --output PATH` |
| Eğitim çıktısı | `./checkpoints/<isim>/` | YAML'da `training.output_dir:` |
| HuggingFace cache | `~/.cache/huggingface/` | `HF_HOME` env var (canonical; ForgeLM kendi cache-dir override'ını eklemiyor) |
| HuggingFace token | `HUGGINGFACE_TOKEN` env | YAML'da `auth.hf_token:` |

## Çoklu config iş akışı

Çoğu ekip proje başına iki YAML tutar — hızlı iterasyon için bir "dev" config (1 epoch, küçük subset, eval kapalı) ve her kapıyı açan bir "prod" config:

```text
configs/
├── customer-support.yaml          — tam koşu (release için bunu kullan)
└── customer-support.dev.yaml      — dev iterasyon (daha hızlı)
```

ForgeLM YAML, `extends:` miras anahtarını **desteklemez**; her config kendi başına yeterli bir `ForgeConfig`'tir. Prod ve dev config'ler arasında tekrarlamayı önlemek için tek dosyada YAML anchor (`&base` / `<<: *base`) kullanın ya da CI üzerinden templated diff:

```yaml
# customer-support.dev.yaml — kendi başına yeterli tam config (dev varyantı)
model:
  name_or_path: "Qwen/Qwen2.5-7B-Instruct"
  max_length: 2048

lora:
  r: 8
  alpha: 16
  target_modules: ["q_proj", "v_proj"]

training:
  trainer_type: "sft"
  num_train_epochs: 1                    # prod'da 3'tü
  max_steps: 200                          # iterasyonu sınırla
  per_device_train_batch_size: 1
  output_dir: "./checkpoints/customer-support.dev"

data:
  dataset_name_or_path: "data/dev/100rows.jsonl"

evaluation:
  benchmark: { enabled: false }
  safety:    { enabled: false }
```

Geliştirme sırasında dev varyantı, release için tam olanını koşturun. CI her main merge'inde tam olanını çalıştırır.

## Compliance artifact'ları nereye gider

Bir eğitim koşusu kanıtını `training.output_dir` içine yazar: en üstte `audit_log.jsonl` (`.manifest.json` genesis pin'iyle) ve `final_model/` yanında bir `compliance/` dizini; tam ağacı ve hangi dosyaların YAML'a bağlı olduğunu [İlk Koşunuz](#/getting-started/first-run) gösterir. `artifacts/` dizini yoktur. `compliance/` ve `audit_log.jsonl`'i bir incelemeciye teslim edin ya da CI artifact'larında pinleyin.

:::warn
**Her koşunun çıktı dizinini ayrı tutun.** Her koşunun kendi audit zinciri ve run id'si vardır; iki koşunun dosyalarını tek dizine kopyalamak onları karıştırır. Yalnızca audit log (`forgelm verify-audit`) ve `final_model/` (`forgelm verify-integrity`) tamper-check'lidir; `compliance/`'ı hiçbir şey hash'lemez.
:::

## Bkz.

- [Konfigürasyon Referansı](#/reference/configuration) — bu yolları kontrol eden tüm alanlar.
- [CI/CD Hatları](#/operations/cicd) — bu yapıyı GitHub Actions'a bağlamak.
- [Audit Log](#/compliance/audit-log) — artifact dizininin nasıl kullanıldığı.
