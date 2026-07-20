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

Tam olarak beş kontrol vardır. Aşağıdaki adlar audit raporundaki `quality_summary.by_check` haritasında görünen tanımlayıcılardır; doğrudan bunlara göre kapı kurabilirsiniz.

| Kontrol (`by_check` anahtarı) | Tetiklenme koşulu |
|---|---|
| `low_alpha_ratio` | Harfler, boşluk olmayan karakterlerin **%70'inden azını** oluşturuyor. |
| `low_punct_endings` | Boş olmayan satırların **%50'sinden azı** noktalama ile bitiyor. |
| `abnormal_mean_word_length` | Ortalama kelime uzunluğu **3.0–12.0** karakter penceresinin dışında. |
| `short_paragraphs` | `\n\n` ile ayrılmış blokların **%50'sinden fazlası** 5 kelimeden az içeriyor. |
| `repeated_lines` | Gerçekten tekrar eden (sayı ≥ 2) ilk 3 satır, tüm satırların **%30'undan fazlasını** kaplıyor. |

Sabitler `forgelm/data_audit/_quality.py` dosyasından okundu.

:::warn
**İçerik uzunluğu kontrolü ve madde-işareti oranı kontrolü yoktur.** Bu sayfanın önceki sürümleri `min_content_length` (50 karakter) ve `max_bullet_ratio` (0.90) alanlarını, 0.55'lik bir `min_alpha_ratio` ve 3–10'luk bir ortalama kelime uzunluğu penceresiyle birlikte listeliyordu. Bu adların hiçbiri mevcut değil ve gerçek olan iki sayı da olduğundan düşük gösterilmişti: alfa eşiği 0.55 değil **0.70**, üst kelime uzunluğu sınırı ise 10 değil **12.0**. Madde-işareti veya kod ağırlıklı bir corpus'u eski tabloya göre ayarladıysanız yeniden kontrol edin — alfa kontrolü belgelenenden belirgin şekilde daha sıkıdır.
:::

Bunlardan birini meşru ihlal eden corpus'lar için (ör. kod-ağırlıklı dataset'ler alfa oranını ihlal eder) `--no-quality-filter` ile o koşu için filtreyi tamamen atlayın.

## Tasarım gereği muhafazakar

Eşikler *flag, düşürme* için ayarlandı. Sebepler:

1. Domain uyumsuzluğu — web crawl'lara ayarlanmış kalite filtresi medikal veya hukuki metinde yanlış yargı verir.
2. Sessiz düşürme kullanıcıya görünmez. Flag göstermek ve insanın karar vermesi daha iyidir.
3. Audit raporları dataset sürümleri arasında karşılaştırılır; flag sayısındaki ani değişim bilgilendiricidir.

Daha sıkı filtreleme isterseniz — örneğin pre-training'e giden kamu web crawl'ında — filtreyi uç durumların manuel incelemesi ile birleştirin.

## Kalite flag'lerini okuma

:::warn
**Kalite filtresi için genel (public) bir programatik API yoktur.** Bu sayfanın önceki sürümleri `from forgelm.data_audit import score_quality` satırını belgeliyordu. Bu import `ImportError: cannot import name 'score_quality'` hatası verir — fonksiyon hiç var olmadı; örnek çıktısında gösterilen `symbol_density` ve `short_content` flag adları da öyle.
:::

Desteklenen yüzey audit raporudur. `quality_summary.by_check` size corpus genelinde kontrol başına sayımları verir:

```shell
forgelm audit data/ --output-format json | jq '.quality_summary'
```

```json
{
  "samples_flagged": 5,
  "samples_evaluated": 360,
  "by_check": {"low_punct_endings": 3, "short_paragraphs": 2},
  "overall_quality_score": 0.9861
}
```

Split başına sayımlar da `.splits.<ad>.quality_samples_flagged` ve `.splits.<ad>.quality_samples_evaluated` altında bulunur.

Satır seviyesinde flag'lere ihtiyacınız varsa, altta yatan yardımcı `forgelm.data_audit._quality._row_quality_flags(text) -> List[str]` fonksiyonudur; tetiklenen beş kontrol adının alt kümesini döndürür (temiz metin için boş liste). Fonksiyon private'dır — baştaki alt çizgi hiçbir kararlılık garantisi taşımadığı ve bir deprecation döngüsü olmadan değişebileceği anlamına gelir. Buna bağımlıysanız ForgeLM sürümünüzü sabitleyin.

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
