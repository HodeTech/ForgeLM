---
title: Dil Tespiti
description: Satır başı dil tanımlama — "Türkçe olmalıyken %12'si İngilizce kaydı" bug'ını yakalayın.
---

# Dil Tespiti

Gerçek-dünya dataset'leri nominal olarak tek dilde olur ama kazara başkalarını içerebilir — Türkçe corpus'a yapıştırılmış İngilizce dokümantasyon, İspanyolca dataset'te Fransızca hukuk boilerplate. ForgeLM satır başı dili tespit eder ve audit zamanında dağılımı raporlar.

## Hızlı örnek

```shell
$ forgelm audit data/medical-tr.jsonl
✓ dil: %99.2 tr, %0.5 en, %0.3 diğer
   12 satır 'en' (muhtemelen kazara)
   5 satır 'mixed' (her iki dili içeren)
```

Audit raporu satır başı dili ayrıştırır ve aykırı satırların indislerini listeler:

```shell
$ jq '.language_outliers[:3]' audit/data_audit_report.json
[
  {"row": 1240, "detected": "en", "expected": "tr", "snippet": "Lorem ipsum dolor..."},
  {"row": 4521, "detected": "en", "expected": "tr", "snippet": "Patient should call..."},
  {"row": 9012, "detected": "mixed", "expected": "tr", "snippet": "Hasta günde 3 kez two..."}
]
```

## Detector

`langdetect` kullanır (Google CLD2'nin saf-Python port'u). 55+ dili kutudan çıktığı gibi destekler. Performans: satır başı ~1ms, GPU yok.

Çok kısa satırlarda (<50 karakter) dil tespiti güvenilmez olur — ForgeLM bunları tahmin etmek yerine `unknown` olarak işaretler.

## Konfigürasyon

> **Not:** YAML konfigürasyonunda `audit:` üst düzey bloğu yoktur (`ForgeConfig` bilinmeyen anahtarları reddeder). Dil tespiti `forgelm audit`'te otomatik ve her zaman aktiftir. `--pii-ml-language` bayrağı yalnızca Presidio ML-NER katmanı için geçerlidir; `langdetect` tabanlı dil dağılım kontrolü için değil.

Dataset'iniz bilinçli olarak çok dilli ise audit herhangi bir konfigürasyona gerek kalmadan dağılımı raporlar — aşağıdaki [Sık hatalar](#sık-hatalar) bölümündeki ipucuna bakın.

## Dil dağılım raporu

```json
{
  "language_distribution": {
    "tr": 0.992,
    "en": 0.005,
    "ar": 0.001,
    "unknown": 0.002
  },
  "language_outliers": 17,
  "expected": "tr"
}
```

## Sık hatalar

:::warn
**Çıkarılmış PDF metninde dil tespiti.** PDF çıkarımı bazen aksi takdirde-Türkçe içerikte İngilizce boilerplate ("Confidential", "Page 1 of N", "© 2026 Company") koşuları korur ve tespiti şaşırtır. Bunları ingest'te ön-filtreleyin.
:::

:::warn
**`min_chars`'i çok düşük ayarlamak.** Kısa satırlar gürültülü tespit üretir. 50'nin üstünde kalın; kalite raporları için 100 daha güvenli.
:::

:::tip
**Çok dilli dataset'ler.** Dataset'iniz bilinçli çok dilli ise (çeviri çiftleri, çok dilli chat) `expected` ayarlamayın — audit sadece dağılımı raporlasın ve tek doküman içinde karışan satırları flaglesin.
:::

## Bkz.

- [Veri Seti Denetimi](#/data/audit) — dil tespitini standart audit'in parçası olarak koşturur.
- [Doküman Ingest'i](#/data/ingestion) — ham metin chunk'lar; `forgelm audit --pii-ml-language LANG` Phase 12.5 ML-NER PII katmanı için dil ipucudur.
