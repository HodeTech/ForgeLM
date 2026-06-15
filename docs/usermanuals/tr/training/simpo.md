---
title: Simple Preference Optimization (SimPO)
description: Referans modelsiz tercih öğrenmesi — bellekte ikinci model olmadan DPO.
---

# Simple Preference Optimization (SimPO)

SimPO, DPO'nun sadeleştirilmiş kuzenidir; referans modeli denklemden çıkarır. Ödünleşim: yaklaşık yarı VRAM, biraz daha az kararlılık.

## Ne zaman SimPO

| SimPO kullan: | DPO tercih et: |
|---|---|
| VRAM bütçesi dar (ör. tek 24 GB GPU'da 13B+). | İkinci modeli bellekte tutabiliyorsanız. |
| Temiz referans checkpoint'iniz yok. | SFT checkpoint'iniz yüksek kalite ve güvenilir. |
| Daha basit eğitim dinamiği istiyorsanız. | Kararlılık VRAM'den önemliyse. |

:::tip
Pratik kural: [`--fit-check`](#/operations/vram-fit-check) DPO için `OOM` raporlarsa, daha küçük model veya kısa context düşünmeden önce SimPO'ya geçin. Standart benchmark'larda kalite farkı genelde %5'in altındadır.
:::

## Hızlı örnek

```yaml
model:
  name_or_path: "./checkpoints/sft-base"
  max_length: 4096

lora:
  r: 16
  alpha: 32
  method: "lora"
  target_modules: ["q_proj", "k_proj", "v_proj", "o_proj"]

data:
  dataset_name_or_path: "data/preferences.jsonl"

training:
  trainer_type: "simpo"
  num_train_epochs: 1
  per_device_train_batch_size: 2
  learning_rate: 8.0e-7
  simpo_beta: 2.0            # different scale than DPO's 0.1 — flat field
  simpo_gamma: 0.5           # margin term — flat field
  output_dir: "./checkpoints/simpo"
```

## Veri formatı

DPO ile aynı — `prompt`, `chosen`, `rejected` taşıyan `preference` formatı.
Bkz. [Veri Formatları](#/concepts/data-formats).

## Parametreler

SimPO knob'ları `training:` altında flat alanlardır (nested `training.simpo:` bloğu yok):

| Parametre | Tip | Vars. | Açıklama |
|---|---|---|---|
| `training.simpo_beta` | float | `2.0` | Uzunluk-normalize ödül ölçeği. Yüksek = güçlü tercih kayması. **DPO beta ile aynı ölçek değil.** |
| `training.simpo_gamma` | float | `0.5` | Margin — chosen ve rejected log-likelihood arasında SimPO'nun korumaya çalıştığı boşluk. |
| `training.trainer_type` | string | `"sft"` | SimPO eğitim yolunu açmak için `"simpo"` olarak set edin. |

ForgeLM `loss_type`, `length_normalize` veya `label_smoothing`'i yapılandırılabilir alan olarak **sunmaz** — TRL'in CPO/SimPO trainer'ı kütüphane varsayılanlarıyla çalışır (sigmoid loss, length normalisation her zaman açık, label smoothing yok).

## Bellek

~1.2× SFT belleği — DPO'nun 2×'inden çok daha hafif.

| Model | LoRA rank | `max_length` | VRAM (QLoRA 4-bit) |
|---|---|---|---|
| 7B | 16 | 4096 | 9 GB |
| 13B | 16 | 4096 | 16 GB |
| 8B | 32 | 8192 | 18 GB |

## `simpo_beta` ve `simpo_gamma`

| Kombinasyon | Davranış |
|---|---|
| `simpo_beta=2.0`, `simpo_gamma=0.5` | Varsayılan. Dengeli. |
| `simpo_beta=2.5`, `simpo_gamma=1.0` | Daha agresif tercih kayması. |
| `simpo_beta=1.5`, `simpo_gamma=0.3` | Daha yumuşak, orijinal SFT çıktılarına yakın. |

:::warn
SimPO'nun `simpo_beta`'sı DPO'nun `dpo_beta`'sından farklı ölçekte. DPO hyperparam'larını kopyalamayın — SimPO varsayılanlarından başlayın.
:::

## Sık hatalar

:::warn
**Beta'da aşırıya kaçma.** SimPO DPO'dan daha duyarlıdır; yüksek `beta` + küçük dataset chosen'ı agresif tercih eden ama genel yetenek kaybeden model üretir. Eğitim sırasında benchmark puanlarınızı izleyin.
:::

:::warn
**SFT'siz SimPO.** DPO ile aynı uyarı — kaliteli SFT checkpoint'ten başlayın, ham base modelden değil.
:::

## Bkz.

- [DPO](#/training/dpo) — referans-tabanlı kuzen.
- [ORPO](#/training/orpo) — SFT ve tercih kaybını tek aşamada.
- [Konfigürasyon Referansı](#/reference/configuration).
