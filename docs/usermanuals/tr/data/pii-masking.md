---
title: PII Maskeleme
description: E-posta, telefon, kredi kartı, IBAN ve ulusal kimlikleri ingest sırasında tespit edip redakte edin.
---

# PII Maskeleme

Eğitim setinizdeki kişisel veri hem regülatif tehlikedir (GDPR Md. 5(1)(c) — veri minimizasyonu) hem operasyonel tehlikedir (model ezberler ve geri verir). ForgeLM'in PII maskeleyicisi dokuz kategori PII'yi tespit eder ve ingest zamanında, satırlar JSONL'a düşmeden redakte eder.

## Tespit edilenler

| Kategori | Örnekler | Nasıl |
|---|---|---|
| **E-posta** | `ali@example.com` | RFC 5321-uyumlu regex |
| **Telefon** | `+90 532 123 45 67`, `(555) 123-4567` | E.164-uyumlu pattern + locale varyantları |
| **Kredi kartı** | `4111-1111-1111-1111` | Issuer öneki (Visa/MC/Amex/Discover/JCB/UnionPay) + Luhn — yalnızca Luhn değil; her IMEI Luhn'dan geçer |
| **IBAN** | `TR12 0006 4000 0011 2345 6789 01` | Ülke-bilinçli checksum |
| **Ulusal ID — Türkiye** | 11 haneli TC kimlik | Modulo-10 + modulo-11 checksum |
| **Ulusal ID — Almanya** | Steuer-ID | Format + checksum |
| **Ulusal ID — Fransa** | NIR (sosyal güvenlik) | Format + key validation |
| **US SSN** | `123-45-6789` | Format + reserved-block dışlama |

Bu sekizi tam kümedir (`forgelm/data_audit/_pii_regex.py` içindeki `_PII_PATTERNS`). **IP adresleri tespit edilmez** — bu sayfanın önceki sürümleri bir "IPv4 / IPv6 (varsayılan kapalı; opt-in)" satırı listeliyordu, ancak böyle bir pattern ve böyle bir opt-in mevcut değil. GDPR değerlendirmeniz IP adreslerini kişisel veri sayıyorsa, bunun için ayrı bir kontrole ihtiyacınız var.

## Hızlı örnek

Ingest zamanında:

```shell
$ forgelm ingest ./policies/ \
    --recursive --strategy markdown \
    --pii-mask \
    --output data/policies.jsonl
✓ 12,240 chunk üzerinde 18 PII eşleşmesi maskelendi
```

Ingest sonrası her eşleşme bir placeholder'la değiştirilir:

```text
Önce: "CV'nizi ali@example.com adresine gönderin veya +90 532 123 45 67 numarasını arayın."
Sonra: "CV'nizi [REDACTED] adresine gönderin veya [REDACTED] 67 numarasını arayın."
```

Placeholder dataset boyunca tutarlı; model "şu slot'a maskelenmiş *bir şey* gelir" öğrenebilir — sadece spesifik değeri değil.

## Yayınlanan placeholder

:::warn
**Kategori başına değil, tek bir placeholder vardır.** Tespit edilen her span — e-posta, telefon, kredi kartı, IBAN, TC kimlik, Steuer-ID, NIR, US SSN — tek bir düz `[REDACTED]` ile değiştirilir. Bu sayfanın önceki sürümleri kategori başına etiketlerden oluşan dokuz satırlık bir tablo yayımlıyordu (`[EMAIL_REDACTED]`, `[PHONE_REDACTED]`, `[IP_REDACTED]`, …). Bu string'lerin hiçbiri kod tabanının hiçbir yerinde mevcut değil. Hangi kategorinin maskelendiğini geri elde etmek için redaksiyon etiketlerini parse eden bir tüketici bunu yapamaz — o bilgi yalnızca audit raporundaki `pii_summary` sayımlarında yaşar.
:::

Varsayılan `[REDACTED]`'dır (`mask_pii`'nin `replacement` parametresi). Paralel sır maskeleyici `[REDACTED-SECRET]` kullanır — bkz. [Sır Temizleme](#/data/secrets). Yukarıdaki örnekte telefon pattern'ının numaranın tamamını her zaman tüketmediğine dikkat edin; maskelemeye güvenmeden önce kendi formatlarınıza karşı doğrulayın.

## Tasarım gereği muhafazakar

PII regex'leri bilinçli olarak **düşük false-positive oran** için ayarlanır. Sınırda eşleşmeyi atlamayı (false negative) prose'unuzdaki PII olmayan string'i redakte etmeye (false positive) tercih eder. Sebepler:

1. False positive sessizce verinizi bozar — gerçek kelimeleri `[EMAIL_REDACTED]` ile değiştirmek örnekleri mahveder.
2. Audit aşaması maskelemenin kaçırdığını yakalar; satır başına düzeltme veya düşürme kararı sizde.
3. Agresif regex'ler gerçek-dünya ML pipeline kesintilerine yol açtı (Phase 11.5 olayı [GitHub'daki katkıda bulunan regex standardında](https://github.com/HodeTech/ForgeLM/blob/main/docs/standards/regex.md) belgelenmiştir).

Daha katı tespit gerekirse — örneğin yüksek-stake bir hukuk corpus'u — maskeleyiciyi manuel inceleme adımıyla birleştirin. Regex'leri zorlamayın.

## Sadece-audit modu

Modifiye etmeden tespit için:

```shell
$ forgelm audit data/policies.jsonl
⚠ PII: 18 e-posta, 4 telefon, 2 IBAN (orta seviye)
```

Audit raporu satır indisleri ve offset'leri listeler; spesifik vakaları inceleyebilirsiniz.

## Locale'ler

| Locale | Telefon | Ulusal ID | Notlar |
|---|---|---|---|
| TR (varsayılan) | E.164 + Türkiye formatları | TC kimlik | En çok ayarlanan. |
| DE | E.164 + Almanya formatları | Steuer-ID | |
| FR | E.164 + Fransa formatları | NIR | |
| US | E.164 + (xxx) xxx-xxxx | Reserved-block dışlamalı SSN | |
| Global | Sadece E.164 | yok | Bilinmeyen locale fallback. |

`forgelm ingest --pii-mask` (veya audit eşdeğeri) tarafından
tetiklenen regex-bazlı PII katmanı, yukarıdaki tablodaki tüm
pattern'leri locale flag'i olmadan tespit eder. Açık dil ipuçlu
Presidio ML-NER pass için audit subcommand'ını kullanın:

```shell
$ forgelm audit ./data/*.jsonl --output ./out/ --pii-ml --pii-ml-language de
```

Presidio ML-NER geçişi için locale ipucu vermek üzere CLI bayrağını kullanın:

```shell
$ forgelm ingest ./corpus/ --pii-mask --output out.jsonl
```

> **Not:** YAML konfigürasyonunda `ingestion:` üst düzey bloğu yoktur (`ForgeConfig` bilinmeyen anahtarları reddeder) ve regex PII katmanı için hiçbir yerde locale veya kategori seçimi yoktur — ne YAML'da, ne CLI'da, ne de programatik API'de. `_PII_PATTERNS` locale boyutu olmayan tek bir düz sözlüktür; her pattern her zaman aktiftir. `--pii-ml-language` bayrağı **yalnızca** opsiyonel Presidio ML-NER geçişi için geçerlidir, regex katmanı için değil.

## Programatik API

Ingest dışı PII tespiti gerektiren pipeline'lar için. Her iki fonksiyon da tek bir string alır:

```python
from forgelm.data_audit import detect_pii, mask_pii

text = "Email: ali@example.com, Phone: +90 532 123 45 67"
print(detect_pii(text))
# {'email': 1, 'phone': 1}

print(mask_pii(text))
# Email: [REDACTED], Phone: [REDACTED] 67
```

İmzalar: `detect_pii(text) -> Dict[str, int]` ve `mask_pii(text, replacement='[REDACTED]', *, return_counts=False)`.

:::warn
**`locale=` anahtar kelimesi yoktur.** `detect_pii(text, locale="tr")` çağrısı `TypeError: detect_pii() got an unexpected keyword argument 'locale'` hatası verir; `mask_pii` için de aynısı geçerlidir. Bu sayfanın önceki sürümleri her ikisini de, bir span listesi dönüş şekliyle (`[{'category': ..., 'span': ..., 'value': ...}]`) ve kategori başına placeholder'larla (`[EMAIL_REDACTED]` / `[PHONE_REDACTED]`) birlikte belgeliyordu. Gerçek dönüş düz bir `{tür: sayı}` haritasıdır ve maskeleme **tek bir tekdüze placeholder** kullanır — varsayılan olarak `[REDACTED]`, `replacement=` ile değiştirilebilir. Satır seviyesinde span çıkarımı mevcut değildir.
:::

Tespit edilen sekiz pattern türü: `credit_card`, `de_id`, `email`, `fr_ssn`, `iban`, `phone`, `tr_id`, `us_ssn`.

## Sık hatalar

:::warn
**Compliance sertifikasyonu için PII maskelemeye güvenmek.** PII maskeleme savunma derinliği önlemidir, sertifikasyon değil. Yüksek-riskli corpus için (hukuk, tıp), maskelemeyi manuel inceleme ile birleştirin. ForgeLM PII'yi modifiye etmeden flagleyen `audit` modu yayınlar; inceleyebilirsiniz.
:::

:::warn
**Test etmeden özel PII kategorileri.** Repo'nun `regex.md` standardı yeni pattern eklemek için 8 sıkı kural belgeler. Test checklist'ini atlamak false-positive bug'ların yayınlanmasının yolu.
:::

## Bkz.

- [Veri Seti Denetimi](#/data/audit) — modifiye etmeden PII tespiti koşturur.
- [ML-NER PII (Presidio)](#/data/pii-ml) — regex katmanının yakalayamadığı yapılandırılmamış identifier'lar (person / organization / location) için opsiyonel opt-in katman.
- [Birleşik Maskeleme](#/data/all-mask) — PII + sırlar maskelemesini doğru sırada koşturmak için `--all-mask` kısayolu.
- [Sırların Temizlenmesi](#/data/secrets) — kimlik bilgileri için kardeş özellik.
- [GDPR / KVKK](#/compliance/gdpr) — regülatif bağlam.
