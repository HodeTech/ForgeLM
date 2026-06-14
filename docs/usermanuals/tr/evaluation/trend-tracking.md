---
title: Trend İzleme
description: Eval sonuçlarını koşular arası karşılaştırın — eşikleri aşmadan önce yavaş drift'leri yakalayın.
---

# Trend İzleme

Koşu başı eşikler regresyonları yakalar; trend izleme drift'i yakalar. Beş koşudur sürekli yükselen kategori, bir kerelik sıçramadan farklı (ve genelde daha önemli) bir sinyaldir. ForgeLM eval sonuçlarını proje başına geçmiş dosyasında saklar ve her koşuda trend raporlar.

## Hızlı örnek

Aynı projenin birkaç koşusundan sonra audit raporuna trend bölümü dahil olur:

```json
{
  "trend": {
    "lookback_runs": 10,
    "benchmark": {
      "hellaswag": {"trend": "stable", "delta_per_run": 0.001},
      "truthfulqa": {"trend": "drifting_down", "delta_per_run": -0.012, "concern": "medium"}
    },
    "safety": {
      "S5": {"trend": "drifting_up", "delta_per_run": 0.04, "concern": "high"},
      "S10": {"trend": "stable", "delta_per_run": 0.001}
    }
  }
}
```

`concern` seviyeleri:

| Seviye | Tetikleyici |
|---|---|
| `none` | Lookback penceresinde drift yok. |
| `low` | İstatistiksel drift var ama küçük. |
| `medium` | Sabit drift; mevcut hızla ~10 koşuda eşiğe çarpacak. |
| `high` | Sabit drift; ~3 koşuda eşiğe çarpacak. |
| `critical` | Eşiğe yakın VE drift devam ediyor. |

## Drift nasıl hesaplanır

Her metrik (benchmark görevi veya güvenlik kategorisi) için:

1. Proje geçmişinden son N koşuyu çek.
2. Puanı koşu indeksine karşı doğrusal regresle.
3. Slope'u t-testle sıfıra karşı test et.
4. Slope anlamlı *ve* büyüklüğü gürültü tabanının üstündeyse drift raporla.

`lookback_runs` varsayılan 10 — eğitim sıklığınıza göre ayarlayın.

## Konfigürasyon

```yaml
evaluation:
  trend:
    enabled: true
    history_file: "./.forgelm/eval-history.jsonl"
    lookback_runs: 10
    drift_p_threshold: 0.05             # istatistiksel anlamlılık
    fail_on_concern: "high"             # drift 'high' olursa exit 3
```

`fail_on_concern: high` trend izlemeyi "tavsiye"den "gating"e yükseltir — CI'nız sadece koşu başı regresyonlarda değil, soruna doğru giden drift'lerde de başarısız olur.

## Geçmiş dosyası nerede

Varsayılan `.forgelm/eval-history.jsonl`, proje kök dizininde. Her koşu bir satır ekler:

```json
{"ts": "2026-04-29T14:33:04Z", "run_id": "abc123", "config_hash": "deadbeef", "benchmark": {...}, "safety": {...}}
```

Bu dosyayı commit edin. Küçüktür (koşu başına bir satır, JSON) ve CI koşuları ile katkıda bulunanlar arası trend izlemenin tek yoludur.

## Görselleştirme

ForgeLM bir CLI raporu yayınlar. Özel `forgelm trend` subcommand'ı v0.6.0+ Pro CLI seviyesi için planlanmıştır ([GitHub'daki Phase 13 yol haritası](https://github.com/HodeTech/ForgeLM/blob/main/docs/roadmap.md)) — bugün aynı veri JSONL'dan `jq` ile sorgulanabilir. Bugünkü çalışan akış:

```shell
# Son 20 S5 (iftira) skoru:
$ jq -r 'select(.safety.S5 != null) | "\(.ts) \(.safety.S5)"' \
    .forgelm/eval-history.jsonl | tail -20
```

Görselleştirme için JSONL Grafana / Datadog'a kolay yüklenir
("Dashboard için" altta).

Özel `forgelm trend` subcommand'ı v0.6.0+ ship edince şöyle
görünecek — pseudo-output, BUGÜN runnable DEĞİL:

```text
# planlanan-v0.6.0+ pseudo-output (bugün runnable değil):
$ forgelm trend --metric "safety.S5" --lookback 20

S5 (iftira) — son 20 koşu:

  0.42 ┤                                                ╭────●
  0.30 ┤                                          ╭─────╯
  0.18 ┤                              ╭───────────╯
  0.06 ┤   ●─────●─────●─────●────────╯
       └─┴───────────────────────────────────────────────────┘
         1  3  5  7  9  11 13 15 17 19  20

Doğrusal fit: slope=+0.018/koşu, p=0.001 — yukarı drift (high concern)
```

Dashboard için JSONL Grafana veya Datadog'a kolay yüklenir:

```shell
$ jq '.benchmark.truthfulqa, .ts' .forgelm/eval-history.jsonl > truthfulqa-trend.csv
```

## Koşu tanımlama

Her koşunun `run_id` (UUID) ve `config_hash` (YAML config'in hash'i) vardır. Koşuları karşılaştırırken benzer-için-benzer karşılaştırın — hyperparam değişikliği, regresyon olmadan baseline'ı kaydırabilir.

Geçmişi filtrele. Bugünkü çalışan akış `jq` kullanır:

```shell
$ jq 'select(.config_hash == "deadbeef") | .benchmark.hellaswag' \
    .forgelm/eval-history.jsonl | tail -30
```

Özel `forgelm trend` subcommand'ı (planlanan v0.6.0+ Pro CLI formu, bugün runnable değil):

```text
$ forgelm trend --metric "benchmark.hellaswag" \
    --filter "config_hash=deadbeef" \
    --lookback 30
```

## Sık hatalar

:::warn
**Config-değişen koşularla config-sabit koşuları karıştırmak.** Farklı config'li koşular arasında hesaplanan trend anlamsızdır. Benzer-için-benzer için `--filter config_hash` kullanın.
:::

:::warn
**Çok kısa lookback.** `lookback_runs: 3` ile her rastgele dalgalanma drift gibi görünür. Kararlı sinyal için 10+'da kalın.
:::

:::tip
**Geçmişi açıklayın.** Bilerek bir şey değiştirdiğinizde (yeni dataset, yeni hyperparam) `.forgelm/eval-history.jsonl`'a baseline'ların neden kayabileceğini açıklayan bir not commit edin. Gelecekteki kendiniz şimdiki kendinize teşekkür edecek.
:::

## Bkz.

- [Benchmark Entegrasyonu](#/evaluation/benchmarks) — veriyi üretir.
- [Llama Guard Güvenliği](#/evaluation/safety) — güvenlik puanlarını üretir.
- [Otomatik Geri Alma](#/evaluation/auto-revert) — koşu başı odaklı kardeş kapı.
