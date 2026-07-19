---
title: Trend İzleme
description: Eşikleri aşmadan önce yavaş drift'leri yakalamak için güvenlik puanlarını koşular arası karşılaştırın.
---

# Trend İzleme

Koşu başı eşikler regresyonları yakalar; trend izleme drift'i yakalar. Beş koşudur düşen bir güvenlik puanı, bir kerelik düşüşten farklı (ve genelde daha önemli) bir sinyaldir. ForgeLM'in bugünkü trend izlemesi bilinçli olarak küçük: her güvenlik değerlendirmesi bir JSON Lines geçmiş dosyasına tek satır ekler ve bu geçmişi bir drift sinyaline dönüştürmek size kalır (bir `jq` sorgusu, bir notebook veya bir Grafana/Datadog dashboard'u). Config-driven bir istatistiksel drift dedektörü ve `evaluation.trend:` config bloğu yoktur — `evaluation` şemasında `trend` alanı yoktur.

## Hızlı örnek

`evaluation.safety.enabled: true` her çalıştığında (eğitim sırasında veya özel `forgelm safety-eval` subcommand'ı ile), ForgeLM `safety_results.json`'un yanına `safety_trend.jsonl`'a bir satır ekler:

```json
{"timestamp": "2026-04-29T14:33:04Z", "safety_score": 0.94, "safe_ratio": 0.96, "passed": true}
{"timestamp": "2026-05-03T09:12:47Z", "safety_score": 0.91, "safe_ratio": 0.93, "passed": true}
{"timestamp": "2026-05-10T16:45:02Z", "safety_score": 0.85, "safe_ratio": 0.88, "passed": false}
```

Koşu başına bir satır, dört alan: `timestamp`, `safety_score`, `safe_ratio`, `passed`. Görev-kategorisi başına (`S5`, `S10`, ...) trend yoktur ve benchmark trend'i de yoktur — `forgelm/benchmark.py` hiç trend dosyası yazmaz; yalnızca güvenlik yolu yazar.

## Drift'i kendiniz hesaplama

ForgeLM bu dosya üzerinde sizin için regresyon veya anlamlılık testi çalıştırmaz. `jq` ile drift'i yakalamanın basit, dürüst bir yolu:

```shell
$ jq -s '
    map(.safety_score) as $s |
    ($s | add / length) as $avg |
    {runs: ($s | length), average: $avg, latest: $s[-1], delta: ($s[-1] - $avg)}
  ' ./checkpoints/safety/safety_trend.jsonl
```

`delta` birkaç kontrol boyunca sürekli negatifse, `safety_score` düşüş eğilimindedir — bugün ForgeLM'de hiçbir şey bunun üzerine otomatik geri almasa da, bunu bir `min_safety_score` regresyonu gibi ele alın. Daha titiz bir şey için (doğrusal fit, p-değerleri, kategori başına kırılım), JSONL'ı pandas'a veya bir dashboard aracına aktarın — ForgeLM'in buradaki işi temiz veri üretmektir, onu analiz etmek değil.

## Konfigürasyon

Açılacak bir şey yok. Trend loglaması bir güvenlik değerlendirmesinin koşulsuz yan etkisidir — `evaluation.safety.enabled: true` her çalıştığında (eğitim zamanında veya `forgelm safety-eval` ile), trend satırı otomatik olarak eklenir:

```yaml
evaluation:
  safety:
    enabled: true
```

Ayarlanacak bir `lookback_runs`, `drift_p_threshold` veya `fail_on_concern` anahtarı yoktur — bu alanların hiçbiri `SafetyConfig` üzerinde veya `ForgeConfig`'in başka bir yerinde mevcut değildir.

## Geçmiş dosyası nerede

`safety_trend.jsonl`, güvenlik-değerlendirme çıktısının geri kalanıyla aynı dizinde, `safety_results.json`'un yanına yazılır:

- Eğitim-zamanı güvenlik kapısı: `<training.output_dir>/safety/safety_trend.jsonl` (varsayılan `./checkpoints/safety/safety_trend.jsonl`).
- Bağımsız `forgelm safety-eval --output-dir DIR`: `DIR/safety_trend.jsonl`.

Varsayılan `training.output_dir` genelde koşu başına farklı (ve genelde gitignore'lu) olduğundan, geçmiş yalnızca aynı çıktı dizinini paylaşan koşular arasında birikir. Koşu başına tek satır yerine uzun soluklu bir trend çizgisi istiyorsanız, birden çok koşuyu aynı `training.output_dir`'e yönlendirin veya her kaydedilmiş checkpoint için sonradan `forgelm safety-eval --output-dir <paylaşılan-dizin>` çalıştırın.

## Görselleştirme

ForgeLM bugün bir `forgelm trend` CLI raporu yayınlamıyor. Koşular-arası karşılaştırma — güvenlik trend'i dahil — Pro CLI gözlemlenebilirlik dashboard'unun kapsamında (traction'a bağlı; bkz. [GitHub'daki Faz 13 yol haritası](https://github.com/HodeTech/ForgeLM/blob/main/docs/roadmap.md)), ücretsiz katman CLI subcommand'ı değil. O yayınlanana kadar, JSONL'a karşı `jq` çalışan akıştır:

```shell
$ jq -r '"\(.timestamp) \(.safety_score)"' ./checkpoints/safety/safety_trend.jsonl | tail -20
```

Dashboard'lar için JSONL doğrudan Grafana veya Datadog'a yüklenir:

```shell
$ jq -c '.' ./checkpoints/safety/safety_trend.jsonl > safety-trend.ndjson
```

## Koşu tanımlama

`safety_trend.jsonl` satırları yalnızca `timestamp`, `safety_score`, `safe_ratio` ve `passed` taşır — karşı join yapılacak bir `run_id` veya `config_hash` alanı yoktur. Bir trend satırını belirli bir eğitim koşusuyla ilişkilendirmeniz gerekiyorsa, yerleşik bir join anahtarı beklemek yerine `timestamp`'i kendi koşu logunuzla (veya o koşu için `audit_log.jsonl`'un `training_started` / `training_completed` olaylarıyla) çapraz referanslayın.

```shell
$ jq -r 'select(.passed == false) | .timestamp' ./checkpoints/safety/safety_trend.jsonl
```

## Sık hatalar

:::warn
**Otomatik drift uyarıları beklemek.** ForgeLM'de hiçbir şey `safety_trend.jsonl`'ı izlemez ve çoklu-koşu trend'i yüzünden bir koşuyu başarısız kılmaz — yalnızca mevcut koşunun `evaluation.safety.max_safety_regression` / `min_safety_score` kapıları exit kodunu belirler. Trend analizi bugün tavsiye niteliğinde ve manuel.
:::

:::warn
**Farklı `training.output_dir` değerlerini karşılaştırmak.** Her koşu yeni bir dizine yazarsa, `safety_trend.jsonl` dizin başına bir satırdan fazla birikmez. Gerçek bir trend elde etmek için dizini yeniden kullanın (veya birden çok `safety_trend.jsonl` dosyasını kendiniz birleştirin).
:::

:::tip
**Trend dosyasının yanında kendi koşu loginuzu tutun.** `run_id`/`config_hash` join anahtarı olmadığından, `timestamp`'i config/koşu ile eşleyen hafif bir dış log (spreadsheet, CI artifact'ı veya `audit_log.jsonl`) trend verisini kullanışlı kılan şeydir.
:::

## Bkz.

- [Llama Guard Güvenliği](#/evaluation/safety) — bu sayfanın izlediği `safety_score` / `safe_ratio`'yu üretir.
- [Otomatik Geri Alma](#/evaluation/auto-revert) — koşu başı kapı; trend izleme tavsiye niteliğinde, gating değil.
- [Benchmark Entegrasyonu](#/evaluation/benchmarks) — kendi trend dosyası olmayan ayrı bir kapı.
