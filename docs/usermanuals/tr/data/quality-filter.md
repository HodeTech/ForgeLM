---
title: Kalite Filtresi
description: Düşük-kaliteli eğitim satırlarını yakalamak için Gopher, C4 ve RefinedWeb'den heuristikler.
---

# Kalite Filtresi

Eğitim verinizdeki tüm satırlar eşit faydalı değildir. Boilerplate, OCR hataları, tekrarlanan satırlar ve saf-simge gürültüsü sinyali sulandırır. ForgeLM'in kalite filtresi Gopher, C4 ve RefinedWeb araştırma soylarından heuristikler uygular — muhafazakar şekilde, sessizce satır düşürmeden.

## Flaglenenler

| Heuristik | Yakaladığı |
|---|---|
| **Düşük alfa oranı** | `<%55` alfabetik karakter — genelde kod dump'ları, log spam veya saf simgeler. |
| **Anormal ortalama kelime uzunluğu** | Ortalama `<3` veya `>10` karakterli kelimeler — sıklıkla OCR çöpü veya sadece-URL satırlar. |
| **Tekrarlayan satır oranı** | Satırların `>%30`'u tekrarlanmış — boilerplate veya çıkarma artifact'ı. |
| **Kısa içerik** | Konfigüre minimum altında toplam uzunluk — sıklıkla çıkarma sonrası boş. |
| **Sadece-bullet satırlar** | Satırların `>%90`'ı bullet işaretiyle başlıyor — genelde çıkarılmış nav menüleri. |
| **Simge yoğunluğu** | Aşırı `_-=#*` yoğunluğu — genelde render edilmiş tablolar veya pre-format metin. |

Her satır audit raporunda `quality_flags` listesi alır. Filtre asla otomatik düşürmez; karar size ait.

## Hızlı örnek

```shell
$ forgelm audit data/ingested.jsonl
⚠ kalite flag'leri:
   short_response: 24
   repeated_lines: 12
   abnormal_word_length: 6
   bullet_only: 3
```

Audit, düşük kaliteli satırları *flagler* ama silmez. `forgelm audit` yalnızca raporlar; satır düşürmez veya temizlenmiş JSONL yazmaz. Flaglenen satırları kaldırmak için audit JSON'ını `jq` ile bir downstream manuel adım olarak süzün ve sonucu doğrulamak için `forgelm audit`'i yeniden koşturun.

> **Not:** YAML konfigürasyonunda `audit:` üst düzey bloğu yoktur (`ForgeConfig` bilinmeyen anahtarları reddeder). Eski taslakta gösterilen `drop_flagged` ve `write_clean_output` alanları mevcut değildir; otomatik-düşür-ve-temiz-yaz uygulanmamıştır. Kalite kontrollerini tamamen atlamak için `--no-quality-filter` kullanın.

```shell
# v0.6.0+: quality-filter varsayılan AÇIK; explicit flag zararsız.
$ forgelm audit data/ingested.jsonl
✓ wrote audit/data_audit_report.json (quality_summary: 45 / 12,400 flagged)

# Pre-v0.6.0 (veya açık olmak için) flag geçin:
$ forgelm audit data/ingested.jsonl --quality-filter

# CI gate'leriniz opt-in semantiğine bağlıysa yeni varsayılandan çıkın:
$ forgelm audit data/ingested.jsonl --no-quality-filter
```

## Eşik ayarlama

Kalite filtresi eşik konfigürasyonu mevcut sürümde YAML alanı olarak açık değildir — eşikler aşağıda listelenen heuristik varsayılanlarına sabit. `--quality-filter` / `--no-quality-filter` CLI bayrakları filtrenin çalışıp çalışmayacağını kontrol eder; eşik başına override bayrağı yoktur.

| Heuristik | Varsayılan |
|---|---|
| `min_alpha_ratio` | 0.55 |
| `min_mean_word_length` | 3 |
| `max_mean_word_length` | 10 |
| `max_repeated_line_ratio` | 0.30 |
| `min_content_length` | 50 karakter |
| `max_bullet_ratio` | 0.90 |

Bunlardan birini meşru ihlal eden corpus'lar için (ör. kod-ağırlıklı dataset'ler alfa oranını ihlal eder) `--no-quality-filter` ile o koşu için filtreyi tamamen atlayın.

## Tasarım gereği muhafazakar

Eşikler *flag, düşürme* için ayarlandı. Sebepler:

1. Domain uyumsuzluğu — web crawl'lara ayarlanmış kalite filtresi medikal veya hukuki metinde yanlış yargı verir.
2. Sessiz düşürme kullanıcıya görünmez. Flag göstermek ve insanın karar vermesi daha iyidir.
3. Audit raporları dataset sürümleri arasında karşılaştırılır; flag sayısındaki ani değişim bilgilendiricidir.

Daha sıkı filtreleme isterseniz — örneğin pre-training'e giden kamu web crawl'ında — filtreyi uç durumların manuel incelemesi ile birleştirin.

## Programatik API

```python
from forgelm.data_audit import score_quality

text = "= = = = = = = =\n* * *\n[içerik yok]"
flags = score_quality(text)
print(flags)
# {'low_alpha_ratio': True, 'symbol_density': True, 'short_content': True}
```

## Sık hatalar

:::warn
**İncelemeden satır kaldırma.** Audit raporundaki flaglenen satırları `jq` ile süzerken dikkatli olun — kaldırmalar sessizdir. Temizlenmiş dataset'in geçtiğini doğrulamak için sonuç üzerinde her zaman `forgelm audit` koşturun.
:::

:::warn
**Kod dataset'lerini varsayılan eşiklerle filtrelemek.** Kod prose'tan daha çok simge ve daha kısa ortalama kelime uzunluğu içerir. Etkilenen kontrolleri kapatın veya kod-özgü eşikler kullanın.
:::

## Bkz.

- [Veri Seti Denetimi](#/data/audit) — kalite filtresini standart audit'in parçası olarak koşturur.
- [Doküman Ingest'i](#/data/ingestion) — çoğu kalite sorunu çıkarma zamanında doğar.
