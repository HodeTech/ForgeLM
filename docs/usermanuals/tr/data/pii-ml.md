---
title: ML-NER PII (Presidio)
description: Regex PII katmanının üzerine person / organization / location tespiti ekleyen, opsiyonel Microsoft Presidio adaptörü.
---

# ML-NER PII Tespiti — `--pii-ml`

Varsayılan [PII Maskeleme](#/data/pii-masking) katmanı regex-anchored'dır ve GDPR Madde 10'un önemsediği *yapılandırılmış* identifier'ları (email, telefon, IBAN, kredi kartı, ulusal kimlikler) kapsar. Presidio NER, regex'in doğal olarak kaçırdığı *yapılandırılmamış* identifier'ları ekler: kişi isimleri, organizasyon isimleri, coğrafi yerler.

Bu katman **opt-in**'dir. `--pii-ml` geçilmediğinde varsayılan audit + ingest davranışı değişmez.

## Ne zaman kullanılır

`--pii-ml`'e şunlarda uzanın:

- Korpus serbest formlu prose (mülakat, müşteri mektubu, iç iletişim) ve isimler yapılandırılmış alanlarda değil.
- Compliance reviewer'ınız identifier'lara ek olarak person/org/location kapsamı hakkında soru soruyor.
- Çok dilli bir korpus audit ediyorsunuz (Presidio yerelleştirmeyi destekler; aşağıdaki [Dil Seçimi](#dil-seçimi) bölümüne bakın).

Şunlarda kullanmayın:

- Korpus zaten yoğun yapılandırılmış (CSV-şeklinde JSON, API logları); regex katmanın recall'u zaten istediğiniz yerde.
- CPU bütçeniz dar — Presidio regex'ten materyal olarak yavaştır (satır başına NER forward pass).
- Pre-staged spaCy NER modeli olmadan air-gap ortamda çalışıyorsunuz — [Air-Gap Operasyonu](#/operations/air-gap)'na bakın.

## İki adımlı kurulum

`presidio-analyzer` bir spaCy NER modelini transitively içermez. Kurulum iki satır:

```shell
$ pip install 'forgelm[ingestion-pii-ml]'
$ python -m spacy download en_core_web_lg
```

spaCy modeli yoksa `forgelm audit --pii-ml` hiçbir satır taranmadan **önce** typed bir `ImportError` raise eder — pre-flight checking bilinçli bir tasarım kararıdır (bkz. [Pre-flight neden önemli](#pre-flight-neden-önemli)).

## Hızlı örnek

```shell
$ forgelm audit data/customer-letters.jsonl --output ./audit/ --pii-ml
✓ format: instructions (8.400 satır)
⚠ PII: 12 email, 3 telefon, 1 IBAN (regex katmanı)
⚠ PII (ML): 47 person, 18 organization, 9 location (Presidio)
   en kötü kat: medium
```

Presidio bulguları aynı `pii_summary` ve `pii_severity` bloklarına disjoint kategori isimleriyle birleşir, böylece regex baseline ML sinyaliyle yan yana görünür kalır:

```json
{
  "pii_summary": {
    "email": 12,
    "phone": 3,
    "person": 47,
    "organization": 18,
    "location": 9
  },
  "pii_severity": {
    "by_tier": {"critical": 0, "high": 0, "medium": 59, "low": 30},
    "worst_tier": "medium"
  }
}
```

## Şiddet katmanları

Yeni kategoriler `forgelm.data_audit.PII_ML_SEVERITY` adında özel bir tabloda yaşar:

| Kategori | Kat | Sebep |
|---|---|---|
| `person` | medium | Diğer bağlamla birlikte bir isim re-identify edebilir; tek başına ulusal kimlikten daha zayıf. |
| `organization` | low | Kamu-kayıtlı varlıklar; sızıntı "bu kişi X'te çalışıyor" düzeyinde, "X'in ev adresi" değil. |
| `location` | low | Aynı mantık — coğrafi string'ler tek başına genellikle de-identification-resistant'tır. |

Bu katmanlar **bilinçli olarak** regex'in `critical`/`high` zeminlerinin (kredi kartları, ulusal kimlikler) altındadır. NER false-positive oranları regex-anchored detection'dan materyal olarak yüksektir; bu yüzden bir "person" bulgusu bir "credit_card" bulgusunun yaptığı gibi bir deployment'ı gate'lememeli.

## Dil Seçimi

Non-English korpusları audit etmek için `--pii-ml-language` geçin. ForgeLM, desteklenen haritadaki bir kod (varsayılan İngilizce'ye ek olarak `de`, `es`, `fr`, `it`, `ja`, `ko`, `nl`, `pl`, `pt`, `ru`, `zh`) verildiğinde istenen dil için otomatik olarak bir Presidio `AnalyzerEngine` inşa eder; önce konvansiyonel spaCy modelini kurun:

```bash
python -m spacy download de_core_news_lg
forgelm audit data/german-corpus.jsonl --pii-ml --pii-ml-language de
```

Bakımdaki bir spaCy modeli olmayan diller için (Türkçe, Arapça, vb.) çok-dilli fallback `xx`'i kullanın:

```bash
python -m spacy download xx_ent_wiki_sm
forgelm audit data/turkish-corpus.jsonl --pii-ml --pii-ml-language xx
```

Pre-flight (`_require_presidio(language=...)`) hiçbir satır taranmadan önce hem spaCy modelinin mevcudiyetini hem de dil kaydını doğrular; yanlış yapılandırma sessizce sıfır-bulgu döndürmek yerine actionable bir hata ile abort olur.

## Pre-flight neden önemli

`forgelm.data_audit._require_presidio()` **iki şeyi birden** kontrol eder: import sentinel (extra kurulu mu?) **ve** analyzer build (spaCy modeli mevcut mu?). Hiçbir satır taranmadan önce. Önceki prototipler sadece import'u kontrol ediyordu; bu özellikle kötü bir failure mode üretiyordu:

1. Audit başladı, satırları taramaya başladı.
2. İlk satır başına Presidio çağrısı `OSError("Can't find model 'en_core_web_lg'")` raise etti.
3. `detect_pii_ml`'in satır başına exception handler'ı hatayı yuttu.
4. Sonraki **her** satır da sıfır ML bulgusu döndürdü.
5. Audit hiç diagnostic olmadan yeşil tamamlandı — operatörün çalıştığını sandığı opt-in bir detector için kritik bir compliance kör noktası.

Mevcut pre-flight, eksik-model arızalarını install recipe ile peşinen yüzeye çıkararak bu boşluğu kapatır.

## Programatik API

```python
from forgelm.data_audit import detect_pii_ml, _require_presidio

# Sert pre-flight; eksikse install recipe ile ImportError raise eder.
_require_presidio()

text = "Alice Williams Pazartesi Acme Corp'un Berlin ofisini ziyaret ediyor."
counts = detect_pii_ml(text, language="en")
print(counts)
# {'person': 1, 'organization': 1, 'location': 1}
```

Fonksiyon non-string input, eksik extra'lar veya geçici analyzer hataları için boş dict döndürür (tek bir kötü satır audit'i bloke etmez). Sert arızalar (eksik spaCy modeli, kayıtlı olmayan dil) `_require_presidio(language=...)` tarafından peşinen yakalanır ve hiçbir satır taranmadan önce raise edilir — `audit_dataset`'i bypass ettiğinizde aynı pre-flight garantilerini istiyorsanız bunu açıkça çağırın.

## Bu katmanda OLMAYAN şeyler

- **DATE / TIME / NUMBER tespiti.** Presidio ForgeLM'in haritaladığı üç entity tipinden fazlasını destekler; diğerleri (DATE, NRP, CRYPTO, IP_ADDRESS, …) şu anda haritalı değil çünkü gizlilik semantikleri farklı. Compliance akışınız bunlara ihtiyaç duyuyorsa bir issue açın.
- **Presidio üzerinden PII *maskeleme*.** Mevcut adaptör tespit-only — maskeleme için regex `--pii-mask` flag'i hâlâ ingest tarafı yeniden yazımına sahiptir. Presidio'nun anonymizer modülü ayrı bir bağımlılıktır ve v0.7.0'da bağlı değildir.

## Yaygın tuzaklar

:::warn
**ML-NER'i sert bir gate olarak değerlendirme.** False-positive oranları regex-anchored detection'dan materyal olarak yüksek. Presidio bulgularını araştırma için *sinyal* olarak kullanın, auto-revert kriteri olarak değil.
:::

:::warn
**`--pii-ml`'i `--pii-ml-language` olmadan non-English korpus üzerinde çalıştırma.** Varsayılan İngilizce NER, Türkçe / Almanca / Çince metinde sıfıra yakın bulgu döndürür — ve audit dürüstçe sıfır raporlar. Dili açıkça ayarlayın.
:::

:::tip
**Air-gap deployment'lar:** spaCy modelini hedef hostta ön-stage edin (mirror'dan `python -m spacy download en_core_web_lg`) ve `python -m spacy validate` ile doğrulayın. Model import path'inde olduğunda pre-flight check geçer.
:::

## Bakınız

- [PII Maskeleme](#/data/pii-masking) — her zaman açık olan regex katmanı.
- [Veri Seti Denetimi](#/data/audit) — `--pii-ml`'in çağrıldığı yer.
- [GDPR / KVKK](#/compliance/gdpr) — düzenleyici bağlam.
- [Air-Gap Operasyonu](#/operations/air-gap) — spaCy modelini ön-stage etme.
