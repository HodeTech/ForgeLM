---
title: GDPR / KVKK
description: Veri koruma uyumluluğu — PII minimizasyonu, veri sahibi hakları ve audit kanıtı.
---

# GDPR / KVKK

GDPR (Avrupa) ve KVKK (Türkiye), eğitimde kullanılan kişisel verilere koruma gereklilikleri getirir. ForgeLM'in rolü, kişisel veriyi eğitimden uzak tutmaktan daha dardır: tespit edebildiği tanımlayıcı biçimlerini maskeler, bir audit'in bulduğunu raporlar ve erişim ile silme talepleri için size komutlar verir. Hukuki dayanağınızı belirlemez, isimleri ya da diğer serbest-metin kişisel verileri bulmaz ve eğitilmiş bir modeli anonim yapmaz.

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

Ingest'te PII maskeleme, ForgeLM'in tespit ettiği tanımlayıcı biçimlerini bir placeholder ile değiştirir:

```shell
$ forgelm ingest ./policies/ --recursive --strategy markdown --pii-mask --output data/policies.jsonl
```

Dedektörler e-posta, telefon, kredi kartı, IBAN ile Türk, Alman, Fransız ve ABD ulusal kimlik biçimlerini kapsar; her eşleşme `[REDACTED]` olur. **İsimler, adresler, doğum tarihleri, sağlık verisi ve diğer serbest metin tespit edilmez**; desenler sıradan düzyazıyı maskelemek yerine sınırda bir eşleşmeyi kaçırmaya ayarlıdır. Maskeleme JSONL'daki kişisel veriyi azaltır; tanımlanabilir özneleri ondan kaldırmaz. Bkz. [PII Maskeleme](#/data/pii-masking).

### Hesap verebilirlik (Md. 5(2))

Her `forgelm audit` çalıştırması `data_audit_report.json` yazar. İçeriği:

- Denetlediğiniz veri için tespit edilen PII ve gizli bilgi kategorileri ve sayıları. Bir maskelemenin neyi yakalaması gerektiğini görmek için maskelenmemiş kaynağı, neyin sızdığını görmek için maskelenmiş çıktıyı denetleyin.
- Near-duplicate, kalite ve split-arası örtüşme özetleri ile isteğe bağlı bir Croissant kartı.
- Audit'in kendi üst verisi: ne zaman çalıştığı, ona verilen yol ve girdi.

Rapor satırları kaynak dokümanlara atfetmez, hash manifest taşımaz ve düz bir JSON dosyasıdır: kendisi audit log'a yazılmaz. Silme ve erişim talepleri (`forgelm purge`, `forgelm reverse-pii`) ise `forgelm verify-audit`'in denetlediği hash-zincirli log'a yazılır. Rapor `training.output_dir` içindeyse Annex IV paketinin Madde 10 veri yönetişimi bölümünü besler; böylece regülatörün "eğitim setinizde hangi kişisel veriler vardı?" sorusunun yapılandırılmış bir cevabı olur: dedektörlerin bulduğu, hiç olmadığına dair bir beyan değil.

### Saklama sınırlaması (Md. 5(1)(e))

ForgeLM ham kullanıcı verisini saklamaz — sizin kontrol ettiğiniz JSONL artifact'lar üretir. Saklama ufukları tanımlanabilir ve **raporlanır, zorlanmaz**:

```yaml
retention:
  raw_documents_retention_days: 90
  ephemeral_artefact_retention_days: 365
```

`forgelm purge --check-policy --config config.yaml` ufku aşmış artefaktları listeler ve her zaman 0 ile çıkar. Hiçbir şey otomatik silinmez ve bunun için audit log'a bir şey yazılmaz; süresi geçeni silmek depolama katmanınızın işidir. (`retention.enforce` ayrılmıştır: henüz hiçbir komut onu kullanmaz.)

## Veri sahibi talepleri

En sık talep tipleri ve ForgeLM nasıl yardım eder:

### Erişim hakkı (Md. 15)

"Hakkımda hangi kişisel verileri tutuyorsunuz?"

Kişinin tanımlayıcısını eğitim verinizde arayın:

```shell
$ forgelm reverse-pii --query "ali@example.com" data/*.jsonl
```

Tarama, tanımlayıcının geçtiği satırları raporlar; sıfır eşleşme geçerli bir cevaptır. Yalnızca işaret ettiğiniz dosyalarda gerçekten bulunanı bulur. Maskeleme yalnızca tespit ettiği tanımlayıcı biçimlerini kaldırır; bir isim ya da tespit edilemeyen bir varyant kalabilir ve maskelenmiş JSONL taraması ham kaynak dokümanları kapsamaz: onları ayrıca arayın. ForgeLM eğitilmiş bir modelin neyi hatırladığını söyleyemez.

### Silme hakkı (Md. 17)

"Verilerimi silin."

Bir kişinin verisi eğitim setinizdeydi:
1. Satırları `forgelm reverse-pii` ile bulun, sonra `forgelm purge --row-id <id> --corpus <dosya>` ile kaldırın; her silme audit log'a yazılır.
2. Ham kaynak dokümanlar hâlâ içerebilir — ham deponuzdan düşürün ve gerekirse yeniden ingest edin.
3. Model bazı detayları ezberlemiş olabilir — aşağıdaki "model-seviyesi silme"ye bakın.

### Model-seviyesi silme

LLM'ler eğitimden nadir string'leri ezberleyebilir. PII maskelemeyle bile deploy edilmiş bir modelden tüm izleri kaldırmak zordur. ForgeLM'in savunmaları:

- **Girenleri azalt:** PII maskeleme ve deduplikasyon (ezberlenen veri genelde tekrarlanan veridir).
- **Ezber ölçülmez:** ForgeLM, eğitilmiş bir modelin belirli bir string'i üretip üretmediğini test etmez.
- **Son çare yeniden eğitim:** Maskelemeye rağmen belirli bir öznenin verisi sızdıysa, o kaynak olmadan yeniden eğitin.

Sıfır-tolerans senaryolarda (sağlık, hukuk) PII maskelemeyi eğitimden önce manuel inceleme ile birleştirin.

## DPIA (Veri Koruma Etki Değerlendirmesi)

Yüksek-riskli işleme için GDPR Md. 35 DPIA gerektirir. ForgeLM DPIA'nızı yazmaz, ama audit paketi girdi sağlar:

- Risk sınıflandırması → `compliance.risk_classification`'dan.
- Kişisel veri envanteri → `data_audit_report.json`'dan.
- Uygulanan azaltıcılar → `risk_assessment.mitigation_measures`'dan.
- Öngörülebilir kötüye kullanım / kalıntı riskler → `risk_assessment.foreseeable_misuse`'dan (serbest-metin listesi; şemada ayrı bir residual_risks alanı mevcut değildir).

DPIA çalışması için yukarıdaki girdileri [GitHub'daki QMS risk tedavi planı](https://github.com/HodeTech/ForgeLM/blob/main/docs/qms/risk_treatment_plan-tr.md) ve [Uygulanabilirlik Beyanı](https://github.com/HodeTech/ForgeLM/blob/main/docs/qms/statement_of_applicability-tr.md) ile eşleştirin (toolkit ile gelen GitHub QMS şablonları). Adanmış bir DPIA şablonu yol haritasındadır; şimdilik risk tedavi planı aynı zemini kapsıyor.

## Konfigürasyon referansı

ForgeLM'in GDPR hukuki meta verisi (hukuki dayanak, veri sorumlusu vb.) için YAML yüzeyi yoktur — bu öznitelikler `forgelm/config.py`'da değil, kuruluşunuzun hukuki belgelerinde ve DPIA'sında yer almalıdır. `ComplianceMetadataConfig` şeması (`compliance:` üst-düzey bloğu) yalnızca şunları kabul eder: `provider_name`, `provider_contact`, `system_name`, `intended_purpose`, `known_limitations`, `system_version` ve `risk_classification`. `compliance.data_protection` alt-bloğu mevcut değildir ve başlangıçta Pydantic tarafından reddedilir (`extra="forbid"`).

## Sık hatalar

:::warn
**PII maskelemeyi DPIA-yerine koymak.** Maskeleme teknik bir azaltıcıdır, hukuki değerlendirme değildir. DPIA, risklerin, azaltıcıların ve kalıntı zararın belgelenmiş analizidir — yüksek-riskli işleme için ayrıca gereklidir.
:::

:::warn
**İç verilerde audit'i atlamak.** İç veri, istenmeden kişisel veri ifşasının en yaygın kaynağıdır (çalışan kayıtları müşteri-destek eğitimine sızar). Her şeyi denetleyin.
:::

:::warn
**Uluslararası transferler.** Eğitim veriniz yargı sınırlarını aşıyorsa (Türk verisi AB'de eğitiliyor, AB verisi ABD'de eğitiliyor), ek koruma önlemleri uygulanır. ForgeLM'in transferler için bir ayarı yoktur; koruma önlemlerini DPIA'nıza kaydedin.
:::

:::tip
Sağlık ve finans gibi sektörlerde *ilk eğitim öncesi* gizlilik uzmanına danışın. ForgeLM varsayılanları makuldür ama sektöre-özgü hukuki incelemeyi yerine geçemez.
:::

## Bkz.

- [PII Maskeleme](#/data/pii-masking) — teknik uygulama.
- [Uyumluluk Genel Bakış](#/compliance/overview) — geniş bağlam.
- [Annex IV](#/compliance/annex-iv) — paketlenmiş compliance kanıtı.
