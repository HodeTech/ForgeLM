# SOP: AI Modelleri için Olay Yanıtı

> Standart İşletim Prosedürü — [YOUR ORGANIZATION]
> EU AI Act Referansı: Madde 17(1)(h)(i)
> ISO 27001:2022: A.5.24, A.5.25, A.5.26, A.5.27, A.6.8, A.8.15, A.8.16
> SOC 2: CC4.2, CC7.3, CC7.4, CC7.5, CC9.2

## 1. Amaç

Dağıtılan fine-tuned modeller için güvenlik olaylarını, model
arızalarını ve düzeltici eylemleri yönetme prosedürünü tanımla.
Wave 4 / Faz 23 genişletmesi: §4 güvenlik-olay playbook'unu
(audit-chain integrity, credential leak, supply-chain CVE, webhook
target compromise, GDPR DSAR'lar) mevcut AI-safety olay akışı
yanında kapsar.

## 2. Olay Sınıflandırması

| Önem | Tanım | Yanıt Süresi | Örnek |
|----------|-----------|--------------|---------|
| **Critical** | Model zararlı, ayrımcı veya tehlikeli çıktı üretir | Anında (< 1 saat) | Güvenlik classifier arızası, zararlı içerik üretimi |
| **High** | Model iş kararlarını etkileyen yanlış çıktı üretir | < 4 saat | Yanlış politika bilgisi, yanlış finansal veri |
| **Medium** | Model kalitesinde düşüş tespit edildi | < 24 saat | Eşik altı doğruluk düşüşü, hallucination artışı |
| **Low** | Küçük kalite sorunu, kozmetik | < 1 hafta | Format hataları, ara sıra alakasız yanıtlar |

## 3. Olay Yanıt Prosedürü

### 3.1 Tespit

Olaylar şunlardan tespit edilebilir:
- Runtime izleme alarmları (`monitoring.alert_on_drift: true` ise)
- Kullanıcı/operatör raporları
- Periyodik kalite denetimleri
- ForgeLM webhook arıza bildirimleri

### 3.2 Anında Eylemler

**Critical/High için:**
1. [ ] **Durdur**: Modeli üretimden kaldır veya fallback'e geç
2. [ ] **Dokümante et**: Olay detaylarını kaydet (girdi, çıktı, timestamp, etki)
3. [ ] **Bildir**: AI Officer ve etkilenen paydaşları uyar
4. [ ] **Koru**: İnceleme için model artefaktlarını ve logları sakla

### 3.3 Soruşturma

1. [ ] Bildirilen girdi ile sorunu tekrarla
2. [ ] Eğitim koşumu detayları için `audit_log.jsonl` kontrol et
3. [ ] Orijinal eğitimden `safety_results.json` incele
4. [ ] Model davranışını baseline ile karşılaştır
5. [ ] Kök nedeni tanımla (veri sorunu, eğitim sorunu, dağıtım sorunu)

### 3.4 Düzeltici Eylem

| Kök Neden | Eylem |
|-----------|--------|
| Eğitim verisi sorunu | Veriyi düzelt → yeniden eğit → yeniden değerlendir → yeniden dağıt |
| Güvenlik regresyonu | Önceki model versiyonuna geri dön |
| Yapılandırma hatası | Configi düzelt → düzeltilmiş parametrelerle yeniden eğit |
| Dağıtım hatası | Dağıtımı düzelt, model iyi |

### 3.5 Olay Sonrası

1. [ ] Kök nedeni ve çözümü dokümante et
2. [ ] Yeni risk tespit edildiyse risk değerlendirmesini güncelle
3. [ ] Olay senaryosunu kapsamak için güvenlik test promptlarını güncelle
4. [ ] Bu SOP'yu gerekirse incele ve güncelle
5. [ ] EU AI Act için: ciddi olayları **15 gün** içinde ilgili otoriteye rapor et

## 4. Güvenlik olayları — Wave 4 / Faz 23 genişletmesi

Wave 4 ISO 27001 / SOC 2 alignment kapanışı aşağıdaki güvenlik-olay
playbook'unu ekler. AI-safety olayları (§§1–3) ve güvenlik olayları
ikisi de bu SOP'tan akar; ayırt edici tespit olay sınıfıdır.

### 4.1 Audit-chain integrity ihlali

**Tetik:** `forgelm verify-audit` `6` ile çıkar — `EXIT_INTEGRITY_FAILURE`
(chain hash mismatch, manifest sidecar truncation, HMAC signature
mismatch). Exit `1` (verifier hiç çalışmadı: eksik log yolu ya da secret
olmadan `--require-hmac`) ve exit `2` (runtime I/O hatası) güvenlik olayı
değil, operatör / ortam hatasıdır — bu playbook'u açmadan önce düzeltip
yeniden çalıştırın.

**Önem:** Critical.

**Runbook:**

1. [ ] **İzole et** etkilenen `<output_dir>` — daha fazla yazımı
       önlemek için dizin üzerinde `chmod 0500`.
2. [ ] **Kanıtı koru** — `audit_log.jsonl`, genesis-manifest
       sidecar `audit_log.jsonl.manifest.json`, ve
       `<output_dir>/.forgelm_audit_salt`'ı write-once forensic
       substrate'e (S3 Object Lock, Azure Immutable Blob) kopyala.
       (ForgeLM per-line `.sha256` sidecar üretmez; chain bütünlük
       kanıtı her satırın `_hmac` ve `prev_hash` alanlarında ve
       genesis manifest'te tutulur.)
3. [ ] **Son güvenilen girişi tanımla** —
       `forgelm verify-audit ./outputs/audit_log.jsonl --require-hmac --output-format json 2>&1 | tee verify.log`
       çalıştır; verifier ilk başarısızlıkta durur ve hatalı satır
       numarasını (`first_invalid_index`) raporlar. **Başka bir şeye
       bakmadan önce exit kodunu okuyun:** `6` bütünlük verdiktidir —
       log okundu ve doğrulanmıyor, yani gerçek bir olaydır. `1`
       verifier'ın hiçbir şeyi karşılaştıracak kadar ilerlemediği
       anlamına gelir (log yolu eksik ya da bu shell'de secret env
       var'ı set değilken `--require-hmac`), `2` ise runtime I/O
       hatasıdır; ikisi de sizin kurulumunuzdur, tahrifat kanıtı
       değildir — escalate etmeden önce düzeltip yeniden çalıştırın.
       Tam sınır gerekiyorsa, `head -n N audit_log.jsonl > tmp.jsonl`
       ile manuel bisect yapın ve `0` ile çıkan en büyük N bulunana
       kadar `verify-audit`'ı `tmp.jsonl`'a karşı yeniden çalıştırın —
       sıfır-olmayan her kodu "zincir N'de kırıldı" saymak, döngüye bir
       `1` (export edilmemiş secret) girdiği anda yanlış sınırda
       yakınsar. O satıra kadar her şey forensic olarak güvenilirdir;
       sonrası tainted sayılmalıdır. Kısaltılmış `tmp.jsonl`'ın genesis
       manifest sidecar'ı olmadığına dikkat edin; bu nedenle bisect
       yalnızca zincir sürekliliğini kanıtlar — manifest çapraz
       kontrolü yukarıdaki adımda orijinal log'a karşı çalışır.
4. [ ] **Bildir** AI Officer + Güvenlik ekibi + DPO (kötü satırdan
       sonra herhangi bir PII-bearing event varsa).
5. [ ] **Karar ver** tainted-tail girişlerini kanıt olarak (önerilen)
       saklamak veya geri sarmak hakkında.
6. [ ] **Denetle** şüpheli zaman penceresi sırasında `<output_dir>`
       substrate'ine yetkisiz yazma erişimi için IdP'yi.

### 4.2 Credential leak tespit edildi

**Tetik:** `forgelm audit` `_SECRET_PATTERNS` regex'i eğitim
korpusunda veya webhook log'unda bir credential ile eşleşir; VEYA
external CVE / breach disclosure kullanılan bir token'a atıf yapar.

**Önem:** Critical.

**Runbook:**

1. [ ] **Sızdırılan credential'ı hemen rotate et** issuing
       authority'de (HF Hub token, GitHub PAT, Slack webhook, OpenAI
       API key, AWS access key, vb.).
2. [ ] **`forgelm purge --row-id <leaked-row>` çalıştır** sızdırılan
       credential satırını içeren her korpus karşı.
3. [ ] **Koşumu memorisation-tainted olarak flag'le** —
       `data.erasure_warning_memorisation` olayı bunu dokümante eder.
4. [ ] **Rotasyonu dokümante et** KMS audit log'unda; ForgeLM
       `data.erasure_completed` olayı timestamp'ine geri bağla.
5. [ ] **Sıfırdan yeniden eğit** yüksek-riskli dağıtımlar için.
6. [ ] **Eğitim-veri-onboarding checklist'ini güncelle** `forgelm
       audit <corpus>` pre-flight gerektirmek için (secrets taraması
       her zaman açık; rapordan `secrets_summary`'yi yüzeye çıkarın
       ve sıfır olmayan eşleşmelerde koşumu engelleyin).

### 4.3 Supply-chain CVE flag'lendi

**Tetik:** `pip-audit` nightly high-severity fail eder (Wave 4 / Faz
23 bu gate'i tanıttı); VEYA ForgeLM'in kullandığı bir bağımlılık
üzerinde CVE advisory düşer.

**Önem:** High.

**Runbook:**

1. [ ] **Güvenli sürüme pin'le** `pyproject.toml`'da.
2. [ ] **SBOM'u yeniden inşa et** yeni pinlenen küme için
       (`tools/generate_sbom.py`).
3. [ ] **Bağımlı artefaktları yeniden üret** — etkilenen dep'i
       tüketen bağımlı eğitim pipeline'larını yeniden çalıştır.
4. [ ] **Operatörleri bilgilendir** etkilenen dep ile bir model
       eğitim-anı env'inde zaten gönderilmişse
       (`compliance_report.json` env'i listeler).
5. [ ] **Tracking ticket aç** CVE id + SBOM diff + etkilenen
       koşumlar ile (etkilenenleri tanımlamak için audit log'un
       `config_hash` (per-run manifest sidecar field)'ini kullan).

### 4.4 Webhook target ele geçirildi

**Tetik:** Slack / Teams / custom-webhook recipient bir ihlali
teyit eder; VEYA `safe_post` `_mask` redact'lı Authorization
header'larını gösterir ki bir saldırgan bunları gözlemlemiş
olabilir.

**Önem:** High.

**Runbook:**

1. [ ] **Webhook URL'sini ve destination-tarafı bearer token'ını
       hemen rotate et** (URL'yi `webhook.url_env` ile resolve
       ediyorsan, secret manager'da yeni değeri set'le; ForgeLM şu
       an gövdeleri HMAC ile imzalamıyor).
2. [ ] **Audit chain'i yürü** saldırganın olayları recipient'a splice
       etmediğini teyit için: `audit_log.jsonl`'ı çekirdek lifecycle
       olaylarına göre filtrele
       (`jq 'select(.event | test("^(training\\.|pipeline\\.|human_approval\\.)"))'`)
       ve `run_id` penceresindeki her lifecycle olayının beklenen
       sırayla eşleştiğini doğrula. Not: webhook wire-event adları
       (`training.start`, `training.success`, vb.) audit-log olay
       adlarından (`training.started`, `pipeline.completed`, vb.)
       farklıdır — `audit_log.jsonl`'da yalnız audit-log olayları
       görünür; `notify_*` log'a asla yazılmayan dahili metot
       adlarıdır. Bir `run_id` için eşleşmeyen timestamp'lar veya
       beklenmedik girişler splice sinyalidir.
3. [ ] **`safe_post` error log'larını kontrol et** rotation sonrası
       redact'lı Authorization header'lar için — saldırganın artık
       geçerli token tutmadığını teyit et.
4. [ ] **Receiving system log'larını denetle** splice'lı olaylar
       tarafından tetiklenen beklenmedik eylemler için (Slack
       channel postaları, Teams kartları, Jira ticket'lar).

### 4.5 GDPR Madde 15 (erişim hakkı) talebi

**Tetik:** Veri sahibi operatörün DSAR portalı veya iletişim formu
üzerinden erişim talebi gönderir.

**Önem:** Medium (regülasyon deadline 30 gün).

**Runbook:**

1. [ ] **Sahip kimliğini doğrula** operatörün DSAR prosedürü başına.
2. [ ] **`forgelm reverse-pii --query <verified-identifier>
       --type <category> data/*.jsonl --output-dir <run-dir>` çalıştır**.
3. [ ] **Hash-mask scan** ForgeLM'in maskelediği herhangi bir
       korpus için (`forgelm reverse-pii --query <id>
       --salt-source per_dir --output-dir <run-dir>`).
4. [ ] **Yanıt mektubu oluştur** DSAR şablonu başına; koşumun
       `data.access_request_query` audit-event id'sine atıf yap.
5. [ ] **Yanıt mektubunu sakla** audit chain yanında.

### 4.6 GDPR Madde 17 (silme hakkı) talebi

**Tetik:** Sahip silme talebi gönderir.

**Önem:** Medium (regülasyon deadline 30 gün; bazı EU üye
ülkeleri daha kısa yanıt gerektirir).

**Runbook:**

1. [ ] **Kimliği doğrula** operatörün DSAR prosedürü başına.
2. [ ] **`forgelm purge --row-id <verified-id> --corpus
       data/<file>.jsonl --output-dir <run-dir>` çalıştır**.
3. [ ] **`data.erasure_warning_memorisation` olayını kontrol et** —
       satırı eğiten bir model `final_model/` artefaktına sahipse,
       memorisation residual riski uygulanır.
4. [ ] **Memorisation caveat'ı sahibe bildir** yanıt mektubunda
       (şablon: "satırı sildik ama önceden eğitilmiş bir model bunu
       memorise etmiş olabilir; uygun şekilde yeniden eğiteceğiz veya
       ek safeguard'lar uygulayacağız").
5. [ ] **Yüksek-riskli dağıtımlar için** (finansal danışmanlık, tıbbi
       triaj): sıfırdan yeniden eğit.
6. [ ] **Audit-event chain'ini sakla** completion + warning'lerin
       fired olduğunu kanıtlayan.

## 5. Ciddi Olay Raporlama (EU AI Act)

Madde 73 altında, sağlayıcılar ciddi olayları piyasa gözetim
otoritelerine raporlamalıdır. "Ciddi bir olay" şunları içerir:
- Ölüm veya sağlığa ciddi zarar
- Temel haklara ciddi ihlal
- Kritik altyapıya ciddi aksaklık

**Rapor:** Etkilenen EU üye ülkesinin ulusal piyasa gözetim otoritesi
**Süre:** Farkına varıldıktan sonraki 15 gün içinde

## 6. İnceleme

| Versiyon | Tarih | Yazar | Değişiklikler |
|---------|------|--------|---------|
| 1.0 | [DATE] | [AUTHOR] | İlk versiyon |
| 1.1 | 2026-05-05 | Wave 4 / Faz 23 | §4 güvenlik-olay playbook'u eklendi (audit-chain integrity, credential leak, supply-chain CVE, webhook compromise, GDPR Md. 15/17 DSAR'ları); başlıkta ISO 27001:2022 + SOC 2 kontrol haritalaması |
| 1.2 | 2026-07-19 | `EXIT_INTEGRITY_FAILURE` döngüsü | §4.1 `EXIT_INTEGRITY_FAILURE` (6) üzerine yeniden hedeflendi: tetik artık "sıfır-olmayan" değil exit `6`; son-güvenilen-giriş bisect'i `6`'yı (bütünlük verdikti) `1`'den (verifier hiç çalışmadı) / `2`'den (I/O hatası) ayırıyor, böylece bir kurulum hatası tahrifat sınırı sanılamıyor |
