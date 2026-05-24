---
title: Model Birleştirme
description: TIES, DARE, SLERP veya lineer merge ile birden çok LoRA adapter'ı tek modelde birleştirin.
---

# Model Birleştirme

Model birleştirme birden çok fine-tuned modeli (veya LoRA adapter'ı) tek modele toplar. Uzmanlarınız varsa (biri kod, biri destek, biri matematik) ve her birinin yeteneğini koruyacak bir generalist isterseniz faydalı. ForgeLM `forgelm --merge` ile dört birleştirme algoritması destekler.

## Ne zaman birleştirme

| Birleştirin: | Birleştirmeyin: |
|---|---|
| Aynı base'de eğitilmiş çoklu LoRA adapter'ı var. | "Uzmanlar" radikal farklı (farklı base, farklı boyut). |
| Çoklu deploy edilebilir model yerine tek model istiyorsunuz. | İstek başına farklı davranış gerek — inference'ta route edin. |
| Sıfırdan eğitim olmadan multi-skill model keşfediyorsunuz. | Üretim güvenilirliği yetenek genişliğinden önemli. |

Birleştirme her uzmanın kalitesinden biraz feda eder, genişlik kazanır. Birleştirmeden sonra her zaman yeniden değerlendirin.

## Algoritma seçimi

| Algoritma | Yaptığı | Parladığı yer |
|---|---|---|
| **Lineer** | Adapter başı katsayılarla ağırlık ortalaması. | Aynı-mimari, iyi-hizalanmış adapter'lar. En basit. |
| **SLERP** | İki adapter arası küresel doğrusal interpolasyon. | İki-yollu birleştirme; manifold geometrisini korur. |
| **TIES** | Trim, Elect-sign, Disjoint-merge. Sıfıra yakın delta'ları düşürür, çatışmayı işaretle çözer. | 3+ adapter; yaygın başlangıç noktası. |
| **DARE** | Drop-and-Rescale. Ağırlık delta'larını rastgele sıfırlar, hayatta kalanları yeniden ölçeklendirir. | Etkileşimi azaltır; TIES ile iyi gider (DARE-TIES). |

## Hızlı örnek: TIES

```yaml
merge:
  enabled: true
  algorithm: "ties"
  base_model: "Qwen/Qwen2.5-7B-Instruct"
  models:
    - path: "./checkpoints/customer-support"
      weight: 0.5
    - path: "./checkpoints/code-assistant"
      weight: 0.3
    - path: "./checkpoints/math-reasoning"
      weight: 0.2
  parameters:
    threshold: 0.7                      # TIES-özgü: tutulacak delta'ların top-K%'si
  output:
    dir: "./checkpoints/merged"
    model_card: true
```

```shell
$ forgelm --merge --config configs/merge.yaml
✓ 3 adapter yüklendi
✓ TIES merge: top %70 delta tutuldu, 1247 işaret çatışması çözüldü
✓ ./checkpoints/merged yazıldı
✓ model card üretildi
```

## Hızlı örnek: Lineer

```yaml
merge:
  enabled: true
  algorithm: "linear"
  base_model: "Qwen/Qwen2.5-7B-Instruct"
  models:
    - { path: "./checkpoints/v1", weight: 0.5 }
    - { path: "./checkpoints/v2", weight: 0.5 }
  output:
    dir: "./checkpoints/v1-v2-blend"
```

Lineer en basit — ağırlıkları ortalar. Başlangıç noktası olarak her zaman çalışır; optimal olmayabilir.

## Algoritma parametreleri

| Algoritma | Anahtar parametreler |
|---|---|
| `linear` | Model başı `weights:` |
| `slerp` | `t:` interpolasyon faktörü (0.0 = ilk adapter, 1.0 = ikinci) |
| `ties` | `threshold:` (tutulacak delta'ların top-K%'si, tipik 0.6-0.8), `density:` (alternatif formülasyon) |
| `dare` | `density:` (tutulacak oran, 0.5-0.9), `epsilon:` (yeniden ölçekleme) |
| `dare_ties` | Hem DARE hem TIES parametreleri |

## Birleştirme sonrası değerlendirme

Birleştirilmiş modeli her zaman yeniden değerlendirin — herhangi bir girdi modelden farklı bir model.

```yaml
merge:
  enabled: true
  algorithm: "ties"
  ...
  evaluation:
    benchmark:
      tasks: ["hellaswag", "humaneval", "gsm8k"]    # her uzmandan beceri karışımı
      floors:
        hellaswag: 0.55
        humaneval: 0.40
        gsm8k: 0.50
    safety:
      enabled: true
```

Birleştirilmiş model herhangi bir görevde gerilerse uzmanlardan birine fallback yapın veya farklı algoritma deneyin.

## Birleştirme başarısızlıklarını teşhis

Kötü birleştirme belirtileri:

| Belirti | Olası sebep | Çözüm |
|---|---|---|
| Tutarlı ama generic çıktı | Lineer merge uzmanlaşmaları ortaladı | `threshold: 0.7` ile TIES dene |
| Bozuk çıktı | Adapter base uyuşmazlığı | Tüm adapter'ların aynı base'i kullandığını kontrol et |
| Her görevde rastgele düşük puan | DARE density çok düşük | `density:`'i 0.9'a yükselt |
| Bir uzman baskın | Lineer ağırlık o adapter için çok yüksek | Ağırlıkları yeniden dengele |

## Konfigürasyon

```yaml
merge:
  enabled: true
  algorithm: "ties"
  base_model: "Qwen/Qwen2.5-7B-Instruct"
  models:
    - path: "./checkpoints/v1"
      weight: 0.4
    - path: "./checkpoints/v2"
      weight: 0.6
  parameters:
    threshold: 0.7
    normalize: true                     # ağırlıkları 1.0'a normalize et
  output:
    dir: "./checkpoints/merged"
    model_card: true
    save_format: "safetensors"
```

## Programatik birleştirme

Otomasyon hatları için:

```python
from forgelm.merging import merge_adapters

merge_adapters(
    base="Qwen/Qwen2.5-7B-Instruct",
    adapters=[
        ("./checkpoints/v1", 0.5),
        ("./checkpoints/v2", 0.5),
    ],
    algorithm="ties",
    threshold=0.7,
    output_dir="./checkpoints/merged",
)
```

## Sık hatalar

:::warn
**Farklı base'lerde birleştirme.** Qwen2.5-7B'de eğitilen adapter'lar Llama-3-8B'de eğitilenle birleştirilemez — farklı parametre şekilleri. ForgeLM bunu birleştirme zamanında net bir hatayla reddeder.
:::

:::warn
**Birleştirilmiş modelde eval'i atlamak.** "3 uzmanı birleştirdik"'i "generalist'imiz var" garantisi olarak görmek dilekçe düşüncesidir. Yeniden değerlendirin.
:::

:::warn
**Birleştirme bileşimi.** A+B'yi birleştirmek, sonucu C ile birleştirmek genelde A+B+C'yi tek seferde birleştirmekten kötüdür. Tek çoklu-yollu merge kullanın.
:::

:::tip
Keşif birleştirmesi için küçük bir `(algoritma, parametre)` kombinasyonu grid'i üretip her birini değerlendirin. Bunu otomatikleştiren bir `forgelm merge-sweep` yardımcısı **Faz 14 sonrası** planlanmaya devam ediyor — Faz 14'ün kendisi çok-aşamalı SFT/DPO/GRPO pipeline zincirlemesini `v0.7.0` ile yayınladı (bkz. [GitHub'daki Faz 14 completed-phases girişi](https://github.com/HodeTech/ForgeLM/blob/main/docs/roadmap/completed-phases.md#phase-14-multi-stage-pipeline-chains-v070)) ama merge-sweep CLI'sını içermedi; o yardımcı explicit operatör talebini bekliyor. O zamana kadar her `(algoritma, parametre)` çiftini `forgelm` ile bir kez çağıran küçük bir shell döngüsü yazın.
:::

## Bkz.

- [LoRA, QLoRA, DoRA](#/training/lora) — birleştirilen adapter'ları üretir.
- [Konfigürasyon Referansı](#/reference/configuration) — tam `merge:` bloğu.
- [Sentetik Veri](#/data/synthetic-data) — yetenek genişliği için birleştirmeye alternatif.
