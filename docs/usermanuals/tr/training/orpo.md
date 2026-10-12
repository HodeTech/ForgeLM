---
title: Odds Ratio Preference Optimization (ORPO)
description: SFT ve tercih öğrenmesini tek bir eğitim aşamasında birleştirin.
---

# Odds Ratio Preference Optimization (ORPO)

ORPO, SFT ve DPO'yu tek bir eğitim geçişinde toplar. Loss, standart bir SFT terimini (`chosen` üzerinde) `rejected`'a karşı odds-ratio cezasıyla birleştirir. Sonuç: daha hızlı wall-clock, ayrı aşama yok, referans model yok.

ForgeLM önce üst düzey `trl.ORPOConfig` / `trl.ORPOTrainer` sınıflarını, yoksa `trl.experimental.orpo` yolunu kullanır.
Bu alternatif, önceden haber verilmeden değişebilen veya kaldırılabilen bir **üst bağımlılık experimental API'sidir**.
ForgeLM `TRLExperimentalWarning` uyarısını korur, seçilen yolu ve TRL sürümünü loglar; gerekli sınıf iki konumda da
yoksa açıklayıcı bir config hatası verir.

## Ne zaman ORPO

| ORPO kullan: | SFT → DPO kullan: |
|---|---|
| İki yerine tek aşama istiyorsunuz. | Hizalama kararından önce SFT'yi incelemek istiyorsunuz. |
| Wall-clock zamanı önemli (CI/CD gece koşusu). | Tercih verisi üzerinde SFT'den çok yineleyeceksiniz. |
| Hem çiftler hem temiz SFT verisi hazır. | Sadece SFT verisi var, tercihler sonra gelecek. |
| Base modelden başlıyorsunuz. | Zaten SFT eğitilmiş modeli hizalıyorsunuz. |

:::tip
ORPO, base modelden *direkt* iyi çalışan tek hizalama yöntemidir — kendi SFT terimini içerir.
:::

## Hızlı örnek

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
  dataset_name_or_path: "data/preferences.jsonl"

training:
  trainer_type: "orpo"
  num_train_epochs: 1
  per_device_train_batch_size: 2
  learning_rate: 5.0e-6
  orpo_beta: 0.1                 # düz field — odds-ratio cezasının ağırlığı
  output_dir: "./checkpoints/orpo"
```

## Veri formatı

`preference` formatı: `prompt`, `chosen`, `rejected`. ORPO'nun SFT terimi `chosen` üzerinde eğitir; odds-ratio terimi `rejected`'ı cezalandırır.

## Parametreler

ORPO knob'ları `training:` altında flat alanlardır (nested `training.orpo:` bloğu YOK — bkz. `forgelm/config.py` `TrainingConfig`):

| Parametre | Tip | Vars. | Açıklama |
|---|---|---|---|
| `training.orpo_beta` | float | `0.1` | Odds-ratio ceza terimi gücü. Yüksek = güçlü tercih kayması. |
| `training.trainer_type` | string | `"sft"` | ORPO eğitim yolunu açmak için `"orpo"` olarak set edin. |

ForgeLM `loss_type` veya `sft_weight`'i yapılandırılabilir alan olarak **sunmaz** — TRL'in `ORPOTrainer`'ı kütüphane varsayılanlarıyla çalışır (sigmoid loss, SFT ağırlığı = 1.0).

## Bellek

~1.5× SFT belleği — referans model gerekmez (DPO'nun aksine), ama loss her satır için hem `chosen` hem `rejected`'ı işler.

## ORPO'nun zorlandığı yerler

| Durum | Neden |
|---|---|
| Çok küçük tercih dataset (<2K satır) | Birleşik loss ayrı SFT+DPO'dan çok veri gerek |
| Tercih çiftleri arası kalite çok değişken | Birleşik gradient gürültülü |
| Hizalamadan önce SFT çıktılarını incelemek gerek | ORPO ara SFT checkpoint'i üretmez |

Bu durumlarda SFT ve DPO'yu ayrı çalıştırın.

## Sık hatalar

:::warn
**Zaten SFT eğitilmiş modelde ORPO.** ORPO'nun SFT terimi modeli `chosen` yanıtlarda eğitmeye devam eder — istediğiniz buysa tamam, ama "SFT checkpoint'imin üstüne sadece DPO" istiyorsanız [DPO](#/training/dpo) kullanın.
:::

:::warn
**`beta`'yı çok yüksek ayarlama.** Çok yüksek `beta` SFT terimini bastırır — sonuçta "karşılaştırmayı kazanan" ama yararlı olmayan bozuk format çıktılar üretir.
:::

## Bkz.

- [SFT](#/training/sft) ve [DPO](#/training/dpo) — ORPO'nun sıkıştırdığı iki aşama.
- [Konfigürasyon Referansı](#/reference/configuration).
