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
| **Kredi kartı** | `4111-1111-1111-1111` | Visa/MC/Amex/Discover pattern + Luhn (görünür benzerlerde false-positive yok) |
| **IBAN** | `TR12 0006 4000 0011 2345 6789 01` | Ülke-bilinçli checksum |
| **Ulusal ID — Türkiye** | 11 haneli TC kimlik | Modulo-10 + modulo-11 checksum |
| **Ulusal ID — Almanya** | Steuer-ID | Format + checksum |
| **Ulusal ID — Fransa** | NIR (sosyal güvenlik) | Format + key validation |
| **US SSN** | `123-45-6789` | Format + reserved-block dışlama |
| **IPv4 / IPv6** | `192.168.1.1`, `2001:db8::1` | Standart regex (varsayılan kapalı; opt-in) |

## Hızlı örnek

Ingest zamanında:

```shell
$ forgelm ingest ./policies/ \
    --recursive --strategy markdown \
    --pii-mask \
    --output data/policies.jsonl
✓ 12,240 chunk üzerinde 18 PII eşleşmesi maskelendi
```

Ingest sonrası her eşleşme etiketli placeholder'la değiştirilir:

```text
Önce: "CV'nizi ali@example.com'a gönderin veya +90 532 555 7890'ı arayın."
Sonra: "CV'nizi [EMAIL_REDACTED]'a gönderin veya [PHONE_REDACTED]'ı arayın."
```

Placeholder dataset boyunca tutarlı; model "şu slot'a *bir* e-posta gelir" öğrenebilir — sadece spesifik olanı değil.

## Yayınlanan etiketler

| Etiket | Yerine geçer |
|---|---|
| `[EMAIL_REDACTED]` | E-posta adresleri |
| `[PHONE_REDACTED]` | Telefon numaraları |
| `[CREDITCARD_REDACTED]` | Kredi kartları (Luhn doğrulamalı) |
| `[IBAN_REDACTED]` | IBAN'lar |
| `[ID_TR_REDACTED]` | TC kimlik numaraları |
| `[ID_DE_REDACTED]` | Steuer-ID'ler |
| `[ID_FR_REDACTED]` | NIR numaraları |
| `[SSN_REDACTED]` | US SSN'ler |
| `[IP_REDACTED]` | IP adresleri |

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

> **Not:** YAML konfigürasyonunda `ingestion:` üst düzey bloğu yoktur (`ForgeConfig` bilinmeyen anahtarları reddeder). Regex PII katmanı için locale ve kategori seçimi yalnızca programatik API üzerinden — `mask_pii(text, locale="de")` — ya da Presidio ML-NER geçişi için `--pii-ml-language de` gibi CLI bayrakları aracılığıyla mevcuttur.

## Programatik API

Ingest dışı PII tespiti gerektiren pipeline'lar için:

```python
from forgelm.data_audit import detect_pii, mask_pii

text = "E-posta: ali@example.com, Tel: +90 532 555 7890"
hits = detect_pii(text, locale="tr")
print(hits)
# [{'category': 'email', 'span': (8, 23), 'value': 'ali@example.com'},
#  {'category': 'phone', 'span': (29, 45), 'value': '+90 532 555 7890'}]

masked = mask_pii(text, locale="tr")
print(masked)
# E-posta: [EMAIL_REDACTED], Tel: [PHONE_REDACTED]
```

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
