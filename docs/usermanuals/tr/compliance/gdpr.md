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

Dedektörler e-posta, telefon, kredi kartı, IBAN ile Türk, Alman, Fransız ve ABD ulusal kimlik biçimlerini kapsar; her eşleşme `[REDACTED]` olur. **İsimler, adresler, doğum tarihleri, sağlık verisi ve diğer serbest metin tespit edilmez**; desenler sıradan düzyazıyı maskelemek yerine sınırda bir eşleşmeyi kaçırmaya ayarlıdır. Maskeleme JSONL'daki kişisel veriyi azaltır; tanımlanabilir özneleri ondan kaldırmaz. İsimleri, kurumları ve yerleri bulmak için `forgelm audit --pii-ml` çalıştırın: Presidio NER **tespiti** ekler (raporlar, maskelemez) ve `ingestion-pii-ml` extra'sını ile bir spaCy modeli gerektirir. Bkz. [PII Maskeleme](#/data/pii-masking) ve [ML-NER PII](#/data/pii-ml).

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
$ forgelm reverse-pii --query "ali@example.com" --output-dir ./outputs data/*.jsonl
No matches for identifier hash 3f2a9c1e5b7d4a60… (literal, plaintext mode) in 3 file(s).
```

Tarama, tanımlayıcının geçtiği dosyayı ve satırı raporlar (snippet'ler ham corpus metnidir; kalıcı bir log'a yönlendirmeyin); yukarıdaki gibi sıfır eşleşme geçerli bir cevaptır. Basılan hash, audit event'inin kaydettiği tuzlu tanımlayıcı hash'idir, tanımlayıcının kendisi değildir. `--output-dir`, ForgeLM'in dizin başına tuzu (`.forgelm_audit_salt`) tuttuğu ve `data.access_request_query` event'ini eklediği yerdir: `forgelm purge`'e verdiğiniz dizini verin, böylece iki event aynı zincire katılır. Verilmezse ikisi de ilk corpus dosyasının yanına yazılır ve ForgeLM bir uyarı loglar. Yalnızca işaret ettiğiniz dosyalarda gerçekten bulunanı bulur. Maskeleme yalnızca tespit ettiği tanımlayıcı biçimlerini kaldırır; bir isim ya da tespit edilemeyen bir varyant kalabilir ve maskelenmiş JSONL taraması ham kaynak dokümanları kapsamaz: onları ayrıca arayın. ForgeLM eğitilmiş bir modelin neyi hatırladığını söyleyemez.

### Silme hakkı (Md. 17)

"Verilerimi silin."

Bir kişinin verisi eğitim setinizdeydi:
1. Satırları `forgelm reverse-pii` ile bulun, sonra `forgelm purge --row-id <id> --corpus <dosya>` ile kaldırın; her silme audit log'a yazılır. `--row-id` satırın `id` veya `row_id` alanıyla eşleştirilir, satır numarasıyla asla; `forgelm ingest` bu alanlardan hiçbirini yazmaz. Bu yüzden **eğitimden önce** her satıra kalıcı bir id verin (aşağıya bakın). Id'si olmayan bir corpus satır satır silinemez.
2. Ham kaynak dokümanlar hâlâ içerebilir — ham deponuzdan düşürün ve gerekirse yeniden ingest edin.
3. Model bazı detayları ezberlemiş olabilir — aşağıdaki "model-seviyesi silme"ye bakın.

Satırları adreslenebilir kılmak için corpus oluşturulurken her JSONL satırına bir kez benzersiz bir `id` verin ve dosya sonradan filtrelenir ya da yeniden sıralanırsa bu id'yi koruyun. `id`'si eksik, null ya da boş olan satırlar yeni bir id alır; `id`'sini (ya da `purge`'ın da eşleştirdiği `row_id`'yi) başka bir satırın da kullandığı satırda betik durur, çünkü `forgelm purge --row-id` iki satırı tek eşleşme olarak görür (varsayılanda reddeder, `--row-matches all` ile her eşleşmeyi siler) ve id'yi hangi satırın koruyacağına sizin karar vermeniz gerekir. Çıktı önce geçici bir dosyaya yazılır ve yalnızca her satır doğrulandıktan sonra yerine taşınır; böylece başarısız bir çalıştırma mevcut çıktı dosyasına dokunmaz:

```python
import json, os, uuid

SRC, DST = "data/train.jsonl", "data/train.with-ids.jsonl"
tmp, seen = DST + ".tmp", set()
try:
    with open(SRC, encoding="utf-8") as src, open(tmp, "w", encoding="utf-8") as dst:
        for line_no, line in enumerate(src, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("id") in (None, ""):
                row["id"] = str(uuid.uuid4())
            ids = {str(row[key]) for key in ("id", "row_id") if row.get(key) not in (None, "")}
            if ids & seen:
                raise SystemExit(f"line {line_no}: id {sorted(ids & seen)[0]!r} is already used by another row")
            seen |= ids
            dst.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(tmp, DST)  # reached only when every row passed
finally:
    if os.path.exists(tmp):
        os.remove(tmp)
```

Eğitimi id'li dosyadan yapın. `forgelm reverse-pii` tanımlayıcının geçtiği dosyayı ve satırı raporlar; o satırın `id` değerini dosyadan okuyup `forgelm purge --row-id`'ye verin. Eğitimden sonra id'leri yeniden atamak audit log ile corpus arasındaki bağı koparır; bu yüzden bir kez atayın.

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
