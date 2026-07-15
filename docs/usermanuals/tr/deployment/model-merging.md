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
model:
  name_or_path: "Qwen/Qwen2.5-7B-Instruct"   # her adapter'ın eğitildiği base model

merge:
  enabled: true
  method: "ties"
  models:
    - path: "./checkpoints/customer-support"
      weight: 0.5
    - path: "./checkpoints/code-assistant"
      weight: 0.3
    - path: "./checkpoints/math-reasoning"
      weight: 0.2
  ties_trim_fraction: 0.3              # büyüklüğe göre en küçük %30 delta'yı budar, üstteki ~%70'i tutar
  output_dir: "./checkpoints/merged"
```

```shell
$ forgelm --merge --config configs/merge.yaml
INFO Running TIES merge on 3 adapters...
INFO Model merge completed: 3 models merged with 'ties' → ./checkpoints/merged
```

## Hızlı örnek: Lineer

```yaml
model:
  name_or_path: "Qwen/Qwen2.5-7B-Instruct"

merge:
  enabled: true
  method: "linear"
  models:
    - { path: "./checkpoints/v1", weight: 0.5 }
    - { path: "./checkpoints/v2", weight: 0.5 }
  output_dir: "./checkpoints/v1-v2-blend"
```

Lineer en basit — ağırlıkları ortalar. Başlangıç noktası olarak her zaman çalışır; optimal olmayabilir.

## Algoritma parametreleri

| Algoritma | Anahtar parametreler |
|---|---|
| `linear` | `merge.models` içinde model başı `weight` (toplamı 1.0 olacak şekilde otomatik normalize edilir). |
| `slerp` | Ayrı bir faktör yok — interpolasyon ağırlığı `merge.models`'daki iki girdinin göreli `weight` değerinden türetilir. Tam olarak iki girdi gerektirir. |
| `ties` | `merge.ties_trim_fraction` — işaret oylamasından önce model başına budanacak en küçük büyüklükteki delta'ların oranı (varsayılan `0.2`, yani üstteki ~%80 tutulur). |
| `dare` | `merge.dare_drop_rate` — yeniden ölçeklemeden önce her delta'nın rastgele düşürülme olasılığı (varsayılan `0.3`). `merge.dare_seed` — DARE birleştirmesinin çalıştırmadan çalıştırmaya tekrarlanabilir olması için RNG seed (varsayılan `42`). |

## Birleştirme sonrası değerlendirme

Birleştirilmiş modeli her zaman yeniden değerlendirin — herhangi bir girdi modelden farklı bir model. `merge` ve `evaluation` ayrı üst düzey config bloklarıdır; `forgelm --merge` bittikten sonra ikinci bir config'in `model.name_or_path`'ini birleştirilmiş çıktı dizinine yönlendirip benchmark/güvenlik kapılarını doğrudan `--benchmark-only` ile (eğitim olmadan) çalıştırın:

```yaml
evaluation:
  benchmark:
    tasks: ["hellaswag", "humaneval", "gsm8k"]    # her uzmandan beceri karışımı
    min_score: 0.5
  safety:
    enabled: true
```

```shell
$ forgelm --benchmark-only ./checkpoints/merged --config configs/eval.yaml
```

Birleştirilmiş model herhangi bir görevde gerilerse uzmanlardan birine fallback yapın veya farklı algoritma deneyin.

## Birleştirme başarısızlıklarını teşhis

Kötü birleştirme belirtileri:

| Belirti | Olası sebep | Çözüm |
|---|---|---|
| Tutarlı ama generic çıktı | Lineer merge uzmanlaşmaları ortaladı | `merge.method`'u `ties`'a çevir, `ties_trim_fraction: 0.3` kullan |
| Bozuk çıktı | Adapter base uyuşmazlığı | Tüm adapter'ların aynı base'i kullandığını kontrol et |
| Her görevde rastgele düşük puan | `dare_drop_rate` çok yüksek (çok fazla delta düşürülüyor) | `merge.dare_drop_rate`'i düşür (0.1-0.3 dene) |
| Bir uzman baskın | Diğerlerine göre bir `weight` çok yüksek | `merge.models` içindeki `weight` değerlerini yeniden dengele |

## Konfigürasyon

```yaml
model:
  name_or_path: "Qwen/Qwen2.5-7B-Instruct"

merge:
  enabled: true
  method: "ties"
  models:
    - path: "./checkpoints/v1"
      weight: 0.4
    - path: "./checkpoints/v2"
      weight: 0.6
  ties_trim_fraction: 0.3              # ağırlıklar otomatik olarak 1.0'a normalize edilir
  output_dir: "./checkpoints/merged"
```

## Programatik birleştirme

Otomasyon hatları için:

```python
from forgelm.merging import merge_peft_adapters

result = merge_peft_adapters(
    base_model_path="Qwen/Qwen2.5-7B-Instruct",
    adapters=[
        {"path": "./checkpoints/v1", "weight": 0.5},
        {"path": "./checkpoints/v2", "weight": 0.5},
    ],
    method="ties",
    ties_trim_fraction=0.3,
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
