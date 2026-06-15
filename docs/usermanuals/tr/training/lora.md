---
title: LoRA, QLoRA, DoRA
description: Parametre-verimli fine-tuning — ağırlıkların ~%1'ini eğit, kalitenin ~%95'ini al.
---

# LoRA, QLoRA, DoRA

Low-Rank Adaptation (LoRA) ve varyantları büyük modelleri küçük GPU'larda fine-tune etmenize olanak tanır. Tüm ağırlıkları güncellemek yerine donmuş base modelin yanında düşük-rank "adapter" matrisleri eğitirsiniz — tipik olarak parametrelerin ~%1'i, VRAM'in ~%10'u, full fine-tuning'in ~%90-95 kalitesi.

ForgeLM LoRA / QLoRA / DoRA'yı *her* trainer'a uygular (SFT, DPO, SimPO, KTO, ORPO, GRPO) — algoritmanın değil optimizer'ın özelliğidir.

## Hangi varyantı

```mermaid
flowchart TD
    Q1{"Consumer GPU'da<br/>(<24 GB) sığmalı mı?"}
    Q2{"Full-precision<br/>ağırlıklar dondurulsun mu?"}
    Q3{"LoRA maliyetinde<br/>maksimum kalite mi?"}
    Full[Full fine-tuning]
    LoRA([LoRA])
    QLoRA([QLoRA])
    DoRA([DoRA])

    Q1 -->|Hayır, VRAM bol| Q2
    Q2 -->|Evet| LoRA
    Q2 -->|Hayır, base eritilebilir| Full
    Q1 -->|Evet| Q3
    Q3 -->|Evet| DoRA
    Q3 -->|Hayır, varsayılan| QLoRA

    classDef question fill:#161a24,stroke:#0ea5e9,color:#e6e7ec
    classDef result fill:#1c2030,stroke:#22c55e,color:#e6e7ec
    classDef alt fill:#1c2030,stroke:#9ea3b3,color:#e6e7ec
    class Q1,Q2,Q3 question
    class LoRA,QLoRA,DoRA result
    class Full alt
```

## Hızlı referans

| Varyant | Ne değişir | VRAM (full'a göre) | Kullan |
|---|---|---|---|
| **LoRA** | Attention/MLP yanına düşük-rank matrisler | %30-40 | Full-precision base'lerde varsayılan. |
| **QLoRA** | LoRA + base'in 4-bit NF4 quantizasyonu | %10-15 | Consumer GPU varsayılanı. |
| **DoRA** | LoRA = magnitude × direction | %35-45 | LoRA maliyetinde en yüksek kalite; ~%5-10 yavaş. |
| **PiSSA** | Principal singular bileşenlerden başlatılan LoRA | %30-40 | Küçük dataset'te LoRA'dan hızlı yakınsama. |
| **rsLoRA** | Rank-stabilised scaling ile LoRA | %30-40 | Yüksek rank'larda (r ≥ 64) daha kararlı. |

## Hızlı örnek

```yaml
model:
  name_or_path: "Qwen/Qwen2.5-7B-Instruct"
  load_in_4bit: true                    # QLoRA
  bnb_4bit_quant_type: "nf4"
  bnb_4bit_compute_dtype: "bfloat16"

lora:
  r: 16
  alpha: 32
  dropout: 0.05
  use_dora: false                       # DoRA için true
  target_modules: ["q_proj", "k_proj", "v_proj", "o_proj"]
  modules_to_save: []                   # full-precision modüller (ör. embedding)

training:
  trainer_type: "sft"
  learning_rate: 2.0e-4                 # LoRA full FT'den yüksek LR tolere eder
```

## `r` rank seçimi

Rank LoRA kalitesi için en önemli hyperparam.

| Rank | Eğitilebilir param (7B) | Kullan |
|---|---|---|
| 4 | %0.05 | Stil transferi, sadece format. Ucuz. |
| 8 | %0.1 | Domain adaptation; küçük dataset (<5K). |
| 16 | %0.2 | **Varsayılan.** Çoğu kullanım. |
| 32 | %0.4 | Daha büyük dataset (50K+); zor görevler. |
| 64 | %0.8 | Full FT kalitesine yaklaşır. |
| 128 | %1.5 | Verim azalan; genelde full FT daha iyi. |

`alpha` genelde rank'le ölçeklenir — `alpha = 2 × r` yaygın kural.

## Target modules

```yaml
lora:
  target_modules: "all-linear"          # en geniş — her Linear
  # veya:
  target_modules: ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]
  # veya sadece attention:
  target_modules: ["q_proj", "v_proj"]
```

Geniş = daha çok kapasite ama daha çok VRAM. Varsayılan (Q/K/V/O) çoğu görev için doğru denge.

## DoRA — ne zaman değer

DoRA her ağırlığı magnitude vektörü ve direction matrisine ayırır. Empirik: DoRA LoRA ile full fine-tuning arasındaki farkı daraltır, sıklıkla full FT kalitesini eşler.

```yaml
lora:
  r: 16
  alpha: 32
  use_dora: true
```

Ödünleşim: ~%5-10 yavaş eğitim ve ~%10 fazla VRAM. Kullan:
- Aksi halde full fine-tuning'e yükselmek gerekiyorsa.
- Zor görevde LoRA underfitting görüyorsanız.

## Sık hatalar

:::warn
**`r`'yi çok yüksek ayarlamak.** Rank 128 LoRA compute ve kalite olarak kısmi full fine-tune'a yakın — ve genelde daha küçük model + full FT daha iyi. `r > 64` sürekli geliyorsa yaklaşımı yeniden düşünün.
:::

:::warn
**Embedding değişiklikleri için `modules_to_save` unutmak.** Tokenizer'a yeni token eklerseniz embedding ve lm_head full-precision eğitim ister:
```yaml
lora:
  modules_to_save: ["embed_tokens", "lm_head"]
```
:::

:::warn
**Inference için yanlış base yüklemek.** QLoRA eğittiğinizde adapter full precision saklanır ama base 4-bit kalır. Inference'ta:
- Base'i 4-bit yükleyip adapter uygula, veya
- Servis için adapter'ı full-precision base'e merge edin.

ForgeLM'in `forgelm export`'u doğru yapar.
:::

:::tip
**Sadece adapter kaydet.** Varsayılan davranış: ForgeLM sadece adapter ağırlıklarını kaydeder (~50-200 MB) + base'i işaret eden model card. Deployment için merge: `forgelm export ./checkpoints/run --merge`.
:::

## Bkz.

- [GaLore](#/training/galore) — LoRA seviyesi bellekte full-parametre eğitim.
- [Konfigürasyon Referansı](#/reference/configuration) — her LoRA/quantization alanı.
- [Model Birleştirme](#/deployment/model-merging) — birden çok adapter birleştirmek.
