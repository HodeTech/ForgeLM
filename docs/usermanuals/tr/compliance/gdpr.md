---
title: GDPR / KVKK
description: Veri koruma uyumluluğu — PII minimizasyonu, veri sahibi hakları ve audit kanıtı.
---

# GDPR / KVKK

GDPR (Avrupa) ve KVKK (Türkiye), eğitimde kullanılan kişisel verilere koruma gereklilikleri getirir. ForgeLM'in rolü: kişisel verinin eğitime girmesini en başta engellemek, ne girdiğini belgelemek ve veri sahibi talepleri için kanıt üretmek.

## GDPR / KVKK eğitim verisinde ne ister

| İlke | Madde | Eğitim için anlamı |
|---|---|---|
| **Hukuka uygunluk** | GDPR Md. 5(1)(a) | Kişisel veri işlemek için hukuki bir dayanağınız olmalı. |
| **Amaç sınırlaması** | GDPR Md. 5(1)(b) | A amacı için toplanan veri, ilişkisiz B amacı için kullanılamaz. |
| **Veri minimizasyonu** | GDPR Md. 5(1)(c) | Gerekenden fazla kişisel veri toplamayın veya saklamayın. |
| **Doğruluk** | GDPR Md. 5(1)(d) | Veriyi doğru tutun; yanlış veriyi düzeltin veya silin. |
| **Saklama sınırlaması** | GDPR Md. 5(1)(e) | Gerekenden uzun saklamayın. |
| **Bütünlük & gizlilik** | GDPR Md. 5(1)(f) | Yetkisiz erişime karşı koruyun. |
| **Hesap verebilirlik** | GDPR Md. 5(2) | Uyumu gösterebilmelisiniz. |

KVKK (Türkiye) bu ilkeleri yakından yansıtır.

## ForgeLM her birini nasıl ele alıyor

### Veri minimizasyonu (Md. 5(1)(c))

Ingest'te PII maskeleme kişisel tanımlayıcıları placeholder'larla değiştirir:

```yaml
ingestion:
  pii_mask:
    enabled: true
    locale: "tr"
    categories: ["email", "phone", "iban", "id_tr"]
```

Veri JSONL'a düştüğünde tanımlanabilir özneler kaldırılmıştır. Bkz. [PII Maskeleme](#/data/pii-masking).

### Hesap verebilirlik (Md. 5(2))

Her audit aşağıdakileri belgeleyen `data_audit_report.json` üretir:

- Tespit edilen PII kategorileri ve sayıları (maskelemeden önce).
- Kaynak atfı (her satırın hangi dokümandan geldiği).
- Kalite ve dil dağılımı.
- Tamper-evidence için SHA-256 manifest.

Bu raporlar Annex IV paketine akar. Regülatör "eğitim setinizde hangi kişisel veriler vardı?" diye sorduğunda yapılandırılmış cevabınız olur.

### Saklama sınırlaması (Md. 5(1)(e))

ForgeLM ham kullanıcı verisini saklamaz — sizin kontrol ettiğiniz JSONL artifact'lar üretir. Otomatik saklama uygulanması için:

```yaml
ingestion:
  retention:
    raw_documents:
      ttl_days: 90                       # N gün sonra orijinalleri otomatik sil
    audit_reports:
      ttl_days: 365
```

(Asıl silme depolama katmanınızın sorumluluğu; ForgeLM sadece amaçlanan TTL'yi audit log'a kaydeder.)

## Veri sahibi talepleri

En sık talep tipleri ve ForgeLM nasıl yardım eder:

### Erişim hakkı (Md. 15)

"Hakkımda hangi kişisel verileri tutuyorsunuz?"

Eğitim verinizde reverse-PII koşturun:

```shell
$ forgelm reverse-pii --query "ali@example.com" data/*.jsonl
Maskelenmiş veride eşleşme bulunamadı.
```

PII ingest'te maskelendiği için modelden belirli birinin verisi geri çıkarılamaz. Audit raporu bunu teyit eder.

### Silme hakkı (Md. 17)

"Verilerimi silin."

Bir kişinin verisi eğitim setinizdeydi:
1. Maskelenmiş JSONL kimlik bilgilerini içermiyor — zaten minimize edilmiş.
2. Ham kaynak dokümanlar hâlâ içerebilir — ham deponuzdan düşürün ve gerekirse yeniden ingest edin.
3. Model bazı detayları ezberlemiş olabilir — aşağıdaki "model-seviyesi silme"ye bakın.

### Model-seviyesi silme

LLM'ler eğitimden nadir string'leri ezberleyebilir. PII maskelemeyle bile deploy edilmiş bir modelden tüm izleri kaldırmak zordur. ForgeLM'in savunmaları:

- **Ezberi önle:** PII maskeleme, deduplikasyon (ezberlenen veri genelde tekrarlanan veridir).
- **Ezberi tespit et:** Audit aşaması bilinen PII pattern'leriyle örtüşen satırları flagler.
- **Son çare yeniden eğitim:** Maskelemeye rağmen belirli bir öznenin verisi sızdıysa, o kaynak olmadan yeniden eğitin.

Sıfır-tolerans senaryolarda (sağlık, hukuk) PII maskelemeyi eğitimden önce manuel inceleme ile birleştirin.

## DPIA (Veri Koruma Etki Değerlendirmesi)

Yüksek-riskli işleme için GDPR Md. 35 DPIA gerektirir. ForgeLM DPIA'nızı yazmaz, ama audit paketi girdi sağlar:

- Risk sınıflandırması → `compliance.risk_classification`'dan.
- Kişisel veri envanteri → `data_audit_report.json`'dan.
- Uygulanan azaltıcılar → `compliance.risk_assessment.mitigations`'dan.
- Kalıntı riskler → `compliance.risk_assessment.residual_risks`'tan.

DPIA çalışması için yukarıdaki girdileri [GitHub'daki QMS risk tedavi planı](https://github.com/HodeTech/ForgeLM/blob/main/docs/qms/risk_treatment_plan-tr.md) ve [Uygulanabilirlik Beyanı](https://github.com/HodeTech/ForgeLM/blob/main/docs/qms/statement_of_applicability-tr.md) ile eşleştirin (toolkit ile gelen GitHub QMS şablonları). Adanmış bir DPIA şablonu yol haritasındadır; şimdilik risk tedavi planı aynı zemini kapsıyor.

## Konfigürasyon referansı

```yaml
compliance:
  data_protection:
    framework: "GDPR"                          # GDPR | KVKK | both
    lawful_basis: "legitimate-interest"        # consent | contract | legal-obligation | ...
    purpose: "X için müşteri-destek asistanı"
    data_controller: "Acme Corp"
    data_subjects: "telekom müşterileri"
    retention_basis: "model yaşam döngüsü (~3 yıl) artı audit dönemi"
    international_transfers:
      enabled: false                          # eğitim verisi sınır geçiyorsa true
      safeguards: "Standart Sözleşme Hükümleri 2021/914"
```

## Sık hatalar

:::warn
**PII maskelemeyi DPIA-yerine koymak.** Maskeleme teknik bir azaltıcıdır, hukuki değerlendirme değildir. DPIA, risklerin, azaltıcıların ve kalıntı zararın belgelenmiş analizidir — yüksek-riskli işleme için ayrıca gereklidir.
:::

:::warn
**İç verilerde audit'i atlamak.** İç veri, istenmeden kişisel veri ifşasının en yaygın kaynağıdır (çalışan kayıtları müşteri-destek eğitimine sızar). Her şeyi denetleyin.
:::

:::warn
**Uluslararası transferler.** Eğitim veriniz yargı sınırlarını aşıyorsa (Türk verisi AB'de eğitiliyor, AB verisi ABD'de eğitiliyor), ek koruma önlemleri uygulanır. `international_transfers.enabled: true` ayarlayın ve koruma önlemlerini belgeleyin.
:::

:::tip
Sağlık ve finans gibi sektörlerde *ilk eğitim öncesi* gizlilik uzmanına danışın. ForgeLM varsayılanları makuldür ama sektöre-özgü hukuki incelemeyi yerine geçemez.
:::

## Bkz.

- [PII Maskeleme](#/data/pii-masking) — teknik uygulama.
- [Uyumluluk Genel Bakış](#/compliance/overview) — geniş bağlam.
- [Annex IV](#/compliance/annex-iv) — paketlenmiş compliance kanıtı.
