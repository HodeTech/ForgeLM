---
title: Denetimli Fine-Tuning (SFT)
description: Her alignment hattının temeli — base modeli talimat çiftleri üzerinde eğitin.
---

# Denetimli Fine-Tuning (SFT)

SFT, post-training'in iş atıdır. Modele doğru çıktıların örneklerini verirsiniz, deseni öğrenir. Neredeyse her proje burada başlar; sonu DPO veya GRPO olsa bile.

## Ne zaman SFT

| SFT kullan: | SFT kullanma: |
|---|---|
| Prompt-completion çifti var (en sık şekil). | Sadece tercih çifti var. Doğrudan [DPO](#/training/dpo) (ama aşağıdaki uyarı). |
| Base modelden başlıyorsunuz, format öğretmek gerek. | Zaten SFT eğittiniz; sadece tercih hizalaması gerekli. |
| Basit, kararlı, iyi anlaşılan dinamik istiyorsunuz. | RL stili reward optimizasyonu gerek. [GRPO](#/training/grpo). |

:::tip
Önce SFT, neredeyse her zaman. Zengin tercih verisi olan ekipler bile DPO/SimPO/KTO'dan önce SFT yapar — base modele direkt tercih öğrenme kararsız, formatı bozuk çıktı üretir.
:::

## Hızlı örnek

```yaml
model:
  name_or_path: "Qwen/Qwen2.5-7B-Instruct"
  max_length: 4096
  backend: "transformers"            # or "unsloth" — replaces the legacy `use_unsloth: true` flag

lora:
  r: 16
  alpha: 32
  method: "lora"                     # or "dora" / "pissa" / "rslora"
  target_modules: ["q_proj", "k_proj", "v_proj", "o_proj"]

data:
  dataset_name_or_path: "data/train.jsonl"

training:
  trainer_type: "sft"
  num_train_epochs: 3
  per_device_train_batch_size: 4
  gradient_accumulation_steps: 2
  learning_rate: 2.0e-4
  warmup_ratio: 0.03
  output_dir: "./checkpoints/sft"
  packing: false                     # set to true to bin-pack short samples
```

```shell
$ forgelm --config configs/sft.yaml --dry-run
$ forgelm --config configs/sft.yaml
```

## Veri formatı

İki format desteklenir. Detay için bkz. [Dataset Formatları](#/concepts/data-formats).

**Tek-tur `instructions`:**
```json
{"prompt": "Fransızcaya çevir: 'Good morning'.", "completion": "Bonjour."}
```

**Multi-turn `messages`:**
```json
{"messages": [
  {"role": "user", "content": "..."},
  {"role": "assistant", "content": "..."}
]}
```

## Konfigürasyon parametreleri

SFT-özgü ayarlar standart `training` bloğuyla yan yanadır.

| Parametre | Tip | Vars. | Açıklama |
|---|---|---|---|
| `training.learning_rate` | float | `2e-5` | Şema varsayılanı full-parametre eğitimi için ayarlanmıştır (1e-5 - 5e-5). **LoRA koşuları bunu açıkça ayarlamalıdır** — 1e-4 - 5e-4 — yukarıdaki hızlı örnekte olduğu gibi. Atlanırsa LoRA 2e-5 ile eğitilir ve sessizce yetersiz eğitilmiş olur. |
| `training.num_train_epochs` | int | `3` | Daha çok = daha çok ezberleme, daha az genelleme. |
| `training.per_device_train_batch_size` | int | `4` | Cihaz başına. Etkili batch için `gradient_accumulation_steps` ile çarpın. |
| `training.packing` | bool | `false` | Kısa dizileri throughput için paketle. %30-50 hız. |
| `training.sample_packing` | bool | `false` | **Kullanımdan kaldırıldı** — `packing` için takma ad; `DeprecationWarning` ile `packing: true`'ya yönlendirir. v1.0.0'da kaldırılacak — `packing` kullanın. |
| `training.neftune_noise_alpha` | float | `null` | Embedding-noise regülarizasyonu. Küçük dataset'lerde `5.0` iyileştirir. |
| `model.max_length` | int | `2048` | Eğitimdeki context (yapı `model:` altında, `training:` değil). Uzun = çok VRAM. |

Tam parametre listesi: [Konfigürasyon Referansı](#/reference/configuration).

## Compute ve bellek

SFT bellek olarak en hafif post-training paradigmasıdır:

| Model | LoRA rank | `max_length` | Yaklaşık VRAM (QLoRA 4-bit) |
|---|---|---|---|
| 7B | 16 | 4096 | 8 GB |
| 13B | 16 | 4096 | 14 GB |
| 8B Llama 3 | 32 | 8192 | 16 GB |
| 70B | 16 | 2048 | 2× A100 + ZeRO gerekir |

Her zaman göndermeden önce `--fit-check` koşturun:

```shell
$ forgelm --config configs/sft.yaml --fit-check
FITS  est. peak 7.8 GB / 12 GB available
```

## Sık yapılan hatalar

:::warn
**Full fine-tune için çok yüksek learning rate.** 2e-4 LoRA'da çalışır ama full-parametre koşusunu eritir. Full FT için 1e-5 - 5e-5'e düşürün.
:::

:::warn
**Loss düşmek yerine artıyor.** Muhtemelen tokenizer uyuşmazlığı — veriniz modelin tokenizer'ındaki chat template'tan farklı format için. `forgelm audit` koşturun ve audit raporundaki render edilmiş örnekleri kontrol edin.
:::

:::warn
**Tokenizer değişikliği sonrası loss artıyor.** `model.name_or_path`'i farklı chat template ship eden modeller arasında değiştirip yeni tokenizer'a karşı `forgelm audit` koşturmamak en sık foot-gun'dır. Audit'in render edilmiş örnek önizlemesi trainer'ın gerçekte ne göreceğini gösterir — commit'lemeden önce format'ın eşleştiğini doğrulayın.
:::

:::tip
**Sample packing dramatik hızlandırır.** Ortalama örneğiniz `max_length`'ten çok kısaysa `packing: true` ayarlayın — kısa örnekleri tek diziye paketler, %30-50 throughput. Talimat ayarı için kalite farkı yok.
:::

## Diskte ne elde edersiniz

Eğitim sonrası:

```text
checkpoints/sft/
├── adapter_model.safetensors      ← LoRA ağırlıkları (LoRA kullanılmıyorsa merged checkpoint)
├── README.md                      ← model kartı
├── config_snapshot.yaml           ← kullanılan tam config
└── compliance/                   ← Annex IV bundle (`compliance:` config bloğu varsa otomatik üretilir)
```

## Bkz.

- [DPO](#/training/dpo) — SFT'den sonra olağan adım.
- [LoRA, QLoRA, DoRA](#/training/lora) — parametre-verimli SFT.
- [Veri Seti Denetimi](#/data/audit) — SFT'den önce daima.
- [Konfigürasyon Referansı](#/reference/configuration) — tüm eğitim parametreleri.
