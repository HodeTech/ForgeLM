---
title: Benchmark Entegrasyonu
description: lm-evaluation-harness görevlerini ortalama doğruluk alt sınırı ve otomatik geri almayla koşturun.
---

# Benchmark Entegrasyonu

ForgeLM, [lm-evaluation-harness](https://github.com/EleutherAI/lm-evaluation-harness) ile entegre — LLM'ler için standart benchmark suite'i — ve üzerine üretim katmanını ekler: minimum ortalama doğruluk alt sınırı, regresyonda otomatik geri alma ve compliance paketinize akan yapılandırılmış artifact'lar.

## Hızlı örnek

```yaml
evaluation:
  benchmark:
    enabled: true
    tasks: ["hellaswag", "arc_easy", "truthfulqa", "mmlu"]
    min_score: 0.55                      # tüm görevler arası ortalama doğruluk alt sınırı
    num_fewshot: 0                       # zero-shot eval
    batch_size: 8
    output_dir: "./checkpoints/run/artifacts/"
```

Eğitimden sonra ForgeLM listelenen görevleri koşturur, ortalama skoru hesaplar ve:
- Ortalama skor `min_score`'u karşılıyorsa veya aşıyorsa → koşu başarılı (exit 0)
- Ortalama skor `min_score`'un altına düşerse → son-iyi checkpoint'e otomatik geri al, exit 3

## Desteklenen görevler

`lm-evaluation-harness`'taki her şey çalışır. Sık seçimler:

| Görev | Ölçtüğü |
|---|---|
| `hellaswag` | Sağduyu tamamlama |
| `arc_easy`, `arc_challenge` | İlkokul fen bilimleri |
| `truthfulqa` | Yaygın yanılgılara dayanıklılık |
| `mmlu` | Geniş çoklu-görev bilgi |
| `winogrande` | Zamir çözümlemesi |
| `gsm8k` | İlkokul matematiği (CoT ile) |
| `humaneval` | Kod tamamlama |

Türkçe projeler için ForgeLM, Türkçe-özgü görevlere uyarlanmış `mmlu_tr` ve `belebele_tr` şablonları yayınlar.

## Doğruluk alt sınırı

`min_score`, listelenen tüm görevler genelinde minimum kabul edilebilir post-train **ortalama** puanı tanımlar. Model yalnızca ortalama doğruluk bu değeri karşıladığında veya aştığında terfi eder.

```yaml
evaluation:
  benchmark:
    tasks: ["hellaswag", "mmlu", "truthfulqa"]
    min_score: 0.50                      # ortalama doğruluk alt sınırı (0.0–1.0)
```

`min_score` `null` olduğunda (varsayılan), benchmark'lar çalıştırılır ve sonuçlar kaydedilir, ancak skor terfini engellemez. `0.0` değeri alt sınır olmamasıyla eşdeğerdir.

:::tip
`min_score`'u pre-training ortalama baseline'ınızdan biraz altına ayarlayın. Hedef: *iyileştirme* zorunlu kılmak değil, *gerilemeyi* yakalamak. Hedef görevde %5 kazanan ama hellaswag'da %2 kaybeden model genelde iyidir; ortalaması %15 düşen bozuktur.
:::

## Pre-train baseline

Hangi `min_score`'u koyacağınızı bilmek için bir pre-training baseline lazım. `--benchmark-only` flag'ini (eğitim yapmadan mevcut bir modeli değerlendirir) tasks + output path'i pin'leyen bir config ile kullanın:

```yaml
# baseline.yaml
model:
  name_or_path: "Qwen/Qwen2.5-7B-Instruct"
evaluation:
  benchmark:
    tasks: ["hellaswag", "arc_easy", "truthfulqa", "mmlu"]
    output_dir: "baselines/qwen-2.5-7b/"
```

```shell
$ forgelm --config baseline.yaml --benchmark-only "Qwen/Qwen2.5-7B-Instruct"
{"hellaswag": 0.61, "arc_easy": 0.75, "truthfulqa": 0.49, "mmlu": 0.52}
```

Sonuçlar `baselines/qwen-2.5-7b/benchmark_results.json`'a yazılır — `output_dir` dizini adlandırır; dosya adı her zaman `benchmark_results.json`'dur.

Makul bir alt sınır baseline ortalaması eksi 0.03 (stokastik dalgalanma için %3 pay):

```yaml
evaluation:
  benchmark:
    tasks: ["hellaswag", "arc_easy", "truthfulqa", "mmlu"]
    min_score: 0.56                    # baseline ortalaması ~0.59 - 0.03
```

## Çıktı artifact'ları

Eval'den sonra ForgeLM şunları yazar:

```text
checkpoints/run/artifacts/
└── benchmark_results.json             ← görev başı puanlar + genel geçti/kaldı
```

`benchmark_results.json` yapısı:

```json
{
  "tasks": ["hellaswag", "truthfulqa"],
  "scores": {
    "hellaswag": 0.617,
    "truthfulqa": 0.42
  },
  "average_score": 0.5185,
  "passed": false,
  "num_fewshot": 0,
  "limit": null
}
```

CI hatları `passed` (bool) ve `average_score`'u parse eder (tek `min_score` alt sınırı görev başına değil, tüm görevlerin ortalamasına karşı kontrol edilir). Gating mantığı için bkz. [Otomatik Geri Alma](#/evaluation/auto-revert).

## Konfigürasyon parametreleri

| Parametre | Tip | Vars. | Açıklama |
|---|---|---|---|
| `enabled` | bool | `false` | Ana anahtar. |
| `tasks` | list | `[]` | lm-eval-harness görev adları. |
| `min_score` | float | `null` | Minimum ortalama doğruluk alt sınırı (0.0–1.0). Ortalama skor bu değerin altına düşünce otomatik geri alma tetiklenir. |
| `num_fewshot` | int | `null` | Few-shot örnek sayısı. `null` her görevin belgelenmiş varsayılanını kullanır. |
| `batch_size` | string | `"auto"` | Eval batch size: `"auto"` veya bir tamsayı string. |
| `limit` | int | `null` | Görev başı satır sınırı — hızlı smoke test için. |
| `output_dir` | string | `null` | Benchmark sonuçları JSON'ının kaydedileceği yer. Varsayılan olarak eğitim `output_dir`. |

## Sık hatalar

:::warn
**`min_score`'u pre-train baseline ortalamasından yüksek ayarlamak.** `min_score`'u base modelin ortalama görev skorunun üstüne koyarsanız her koşu başarısız olur — otomatik geri alma devreye girer ve hiç checkpoint alamazsınız. Her zaman `baseline ortalaması - margin` ile başlayın.
:::

:::warn
**Yayınlanan kamuya açık sonuçlarla `num_fewshot` uyuşmazlığı.** Kamuya açık leaderboard'lar belirli shot sayılarında raporlar (ör. MMLU kanonik olarak 5-shot). Sonuçların karşılaştırılabilir olmasını istiyorsanız aynı ayarı kullanın.
:::

:::tip
**`limit` ile iterasyonu hızlandırın.** `limit: 100` ayarlamak görev başına 100 satır koşturur (binlerce yerine) — ~10× hızlı eval. Dev config'lerinde kullanın; üretim için kaldırın.
:::

## Bkz.

- [Otomatik Geri Alma](#/evaluation/auto-revert) — `min_score` karşılanmadığında ne olur.
- [LLM-as-Judge](#/evaluation/judge) — benchmark ötesi nitel eval.
- [Trend İzleme](#/evaluation/trend-tracking) — koşular arası puanları karşılaştırma.
