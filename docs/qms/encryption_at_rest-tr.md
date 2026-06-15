# QMS: Atıl-durumda Şifreleme

> Kalite Yönetim Sistemi rehberi — [YOUR ORGANIZATION]
> ISO 27001:2022 referansları: A.5.33, A.8.10, A.8.13, A.8.24
> SOC 2 referansları: CC6.1, CC6.7

## 1. Amaç

ForgeLM-ürettiği artefaktların atıl-durumda gizli kalması için
operatör-tarafı kontrolleri tanımla. ForgeLM **integrity-protected**
artefaktlar emit eder (HMAC-zincirli audit log'ları, SHA-256 model
integrity dosyaları, imzalı operatör talimatları) ama bunları
şifrelemez — şifreleme operatörün depolama katmanı tarafından
sahiplenilen substrate sorunudur.

Bu doküman her ForgeLM artefakt sınıfını önerilen şifreleme substrate'i
ve operatör eylemine eşler.

## 2. Kapsam

Aşağıdaki ForgeLM-ürettiği asset sınıfları:

| Asset sınıfı | Path pattern | Gizlilik endişesi |
|---|---|---|
| Model ağırlıkları — final | `<output_dir>/final_model/` | Eğitim verisi memorisation; rekabetçi moat |
| Model ağırlıkları — staging | `<output_dir>/final_model.staging.<run_id>/` | Final ile aynı, artı pre-approval state |
| Audit log | `<output_dir>/audit_log.jsonl` + `audit_log.jsonl.manifest.json` (genesis pin) | Operatör kimliği geçmişi; yapılandırma hashleri; uyumluluk için chain-of-custody — satır-seviyesi bütünlük per-line `_hmac` + `prev_hash` ile in-band'dir, ayrı bir `.sha256` sidecar yok |
| Per-output-dir salt | `<output_dir>/.forgelm_audit_salt` | Düşük-entropi identifier'ların salt'sız SHA-256'sı brute-force edilebilir — salt audit hash'ine wordlist direnci veren secret'tir |
| Eğitim corpusu | (operatör-tedarikli; tipik olarak `data/*.jsonl`) | PII; ticari sırlar; müşteri verisi |
| Quickstart-rendered config | (operatör path'i) | HF token'ları, webhook URL'leri, secret env-var isimleri |
| Uyumluluk paketi (Annex IV) | `<output_dir>/compliance/` ZIP | Yukarıdaki her artefaktın aggregate formu |

Operatör-kontrollü asset'ler (HF auth token'ları, webhook secret'ları,
`FORGELM_AUDIT_SECRET`) operatörün secrets-management substrate'inde
yaşar ve bu doküman için kapsam dışıdır — credential-handling rehberi
için `access_control.md`'a bakın.

## 3. Tehdit modeli

Bu rehberin savunduğu tehditler:

1. **Disk hırsızlığı / fiziksel kayıp** — laptop veya eğitim-host hard
   drive'ı düşman eline düşer.
2. **Yedek compromise** — yedek snapshot yanlış-yapılandırılmış bir
   bucket'a veya üçüncü-taraf bir cloud'a düşer; eski snapshot'lar
   mevcut şifreleme politikasından önce gelmiş olabilir.
3. **Shared-tenancy disk leak** — başka bir tenant'ın
   yanlış-yapılandırma yoluyla okuma erişimi kazandığı multi-tenant
   cloud depolaması.
4. **Log-ship intercept** — audit log'un SIEM'e şifrelenmemiş kanal
   üzerinden gönderimi.
5. **Decommissioned disklerden forensic recovery** — imha öncesi
   silinmemiş sektörler.

Açıkça kapsam DIŞI tehditler:

- **Live-memory düşman** — process memory dump'ları; operatörün
  endpoint detection / sandboxing bunu yönetir.
- **Meşru decryption credential'lı insider** — şifreleme yetkili
  erişime karşı koruyamaz; cevap erişim kontrolü + audit log'dur.

Uzun-ufuk (>5 yıl) — kısmi kapsam:

- **Kripto-algoritma kırılması (algoritma agility — ISO A.8.24).**
  ForgeLM, mevcut QMS doküman ömrü boyunca SHA-256 / AES-256-GCM'in
  sağlam kalacağını varsayar. Operatörün IT politikası NIST SP
  800-131A revizyonlarına atıf yapmalı ve 5-yıllık cadence'da
  yeniden değerlendirmelidir; ForgeLM bir algoritma geçişi
  yayınladığında (ör. SHA-3 audit chain) bu QMS dokümanı
  güncellenir. O zamana kadar algoritma-kırılma exposure'u
  residual'dir ve kabul edilmiştir.

## 4. Asset sınıfı başına önerilen kontroller

### 4.1 Model ağırlıkları (final + staging)

| Substrate | Öneri |
|---|---|
| Linux eğitim hostu | LUKS / dm-crypt full-disk encryption (kernel-supported, transparent) |
| macOS eğitim hostu | FileVault (transparent) |
| Windows eğitim hostu | BitLocker (TPM-backed key) |
| Linux container | LUKS-protected host volume'dan Docker `--mount type=bind,...` üzerinden geçirilen şifrelenmiş block device |
| AWS S3 | Customer-managed key (CMK) ile SSE-KMS; bucket-level default encryption + bucket policy `aws:SecureTransport=true` etkinleştir |
| Azure Blob | Azure Key Vault üzerinden customer-managed key |
| GCS | Customer-Supplied Encryption Keys (CSEK) veya Cloud KMS |
| HDFS / Ceph | Operatör-managed KMS ile native at-rest encryption |

**Neden full-disk dosya-seviyesi değil:** model ağırlıkları HF
Transformers / PEFT tarafından birçok küçük dosyaya yazılır (sharded
`.safetensors`, adapter manifestleri). Her dosya üzerinde dosya-
seviyesi GPG / age kırılgan dosya enumeration'ları üretir. Substrate-
seviyesi şifreleme transparent ve denetlenebilirdir.

**Anahtar rotasyonu:** SSE-KMS için en az yıllık; sadece data key'i
değil CMK'nın kendisini rotate et. ForgeLM rotasyondan etkilenmez —
model dosyaları storage katmanında eğitim pipeline'ı fark etmeden
yeniden şifrelenir.

### 4.2 Audit log + manifest sidecar

**Kritik contract:** audit chain ForgeLM tarafından atıl-durumda
integrity-protected'tır (HMAC + SHA-256 chain + genesis manifest).
Şifreleme yalnız gizliliktir. **Bayt'ları yeniden sıralayan veya
yeniden çerçeveleyen bir katmanda şifreleme YAPMA** — `.manifest.json`
belirli bir bayt aralığı üzerinde imzalandıktan sonra, o aralığı
bozan herhangi bir şifreleme `forgelm verify-audit`'i bozar.

Güvenli substrate'ler:

- Aynı-disk şifreleme (LUKS, FileVault, BitLocker) — transparent.
- SSE-S3 veya SSE-KMS ile cloud blob storage — transparent.

Güvensiz substrate'ler:

- JSONL'i base64 + GPG-encrypt eden custom uygulama-katmanı sarma —
  decryption verify komutu çalışmadan önce gerçekleşmediği sürece
  `forgelm verify-audit`'i bozar.

**Off-site replikasyon:** write-once depolamaya gönder (uyumluluk
modunda S3 Object Lock, Azure Immutable Blob, versioning'li MinIO)
böylece yetkili operatör bile geriye dönük girişleri silemez.
ForgeLM'in append-only contract'ı host-üzerindedir; durabilite
operatörün sorumluluğudur.

### 4.3 Per-output-dir salt (`.forgelm_audit_salt`)

Bu, ForgeLM'in emit ettiği operasyonel olarak en hassas tek dosyadır.
Onu compromise etmek bir saldırgana ForgeLM'in kasıtlı olarak
hashlediği cleartext identifier'ları geri kazanmak için
`data.access_request_query` ve `data.erasure_*` audit olaylarına
karşı offline dictionary saldırısı çalıştırmasına izin verir.

**Önerilen tedavi:**

- Dosyada mod `0600` (`chmod 600 .forgelm_audit_salt`).
- İnsan kullanıcı değil CI pipeline service hesabı tarafından
  sahip edilmiş.
- Sadece audit log'un şifreleme posture'ı ile eşleşen substrate'lere
  yedeklenir (HİÇBİR ZAMAN debug bucket'a plaintext).
- Sadece karşılık gelen `<output_dir>` decommissioned olduğunda
  rotate edilir — output-dir-ortasında rotate etmek `forgelm
  verify-audit`'in önceki olaylar için salted-hash varsayımlarını
  geçersiz kılar.

**Defence-in-depth:** `FORGELM_AUDIT_SECRET`'i KMS'inizden export et
böylece on-disk salt iki-bileşenli anahtarın yarısı haline gelir.
Diski çalan ama env secret'ini çalmayan bir saldırgan audit hash'ini
tersine çeviremez. Env-secret rotasyonu için `access_control.md`
§3.4'e bakın.

### 4.4 Eğitim corpusu

Corpus operatör-tedarikli ve operatör-şifrelenmiş'tir. ForgeLM
katkı yapar:

- Şifreleme GEREKEN şeyleri flag'lemek için `forgelm audit` PII
  tespiti + secrets scan (örn. credit-card numarası içerdiği ortaya
  çıkan corpus).
- Severity tier'ları ile `data_audit_report.json` — operatör
  flag'lenen corpus'ları yeniden şifreleyip şifrelemeyeceğine,
  maskeleyeceğine veya karantinaya alacağına karar verir.
- `data.dataset_id` + `data_provenance.json` SHA-256 fingerprint —
  şifreleme sonrası substrate-seviyesi tampering'i tespit eder.

**Ticari sır koruması:** corpus operatörün fikri mülkiyetiyse
(proprietary destek bilet'leri, dahili dokümantasyon), Top Secret
olarak ele al ve key access'i loglanan bir CMK ile substrate
katmanında şifrele. İzole bir VPC içinde eğit; `forgelm`'in corpusu
webhook endpoint'lerine egress etmesine İZİN VERME (bu zaten
zorunludur — `safe_post` webhook payload'larında asla raw eğitim
satırları taşımaz).

### 4.5 Quickstart-rendered config + operatör YAML

Configler HF auth token'ları (`HF_TOKEN`) veya host-internal
hostname gömen webhook URL'leri taşıyabilir. Credential-bearing
olarak ele al:

- Home dizinindeki düz dosya değil bir config-management substrate'inde
  sakla (Ansible Vault, Doppler, Vault).
- Geçici CI kullanımı için, configi job başlangıcında secrets-manager
  değerlerinden render et; rendered dosyayı job teardown'da sil.
- ForgeLM'in audit olaylarındaki `config_hash` (per-run manifest sidecar field) herhangi bir
  secret expansion'dan SONRA hesaplanır, yani sadece secret değerlerinde
  farklı olan iki config dosyası farklı hash'ler üretir — denetçiler
  koşum-ortası bir config swap'i tespit edebilir.

### 4.6 Uyumluluk paketi (Annex IV ZIP)

`forgelm --compliance-export` ZIP yukarıdaki her artefaktı tek bir
dosyada aggregate eder. Yukarıdaki tüm asset sınıflarının birleşimi
olarak ele al:

- En-hassas asset'in gerektirdiği substrate'de şifrelenmiş (tipik
  olarak eğitim corpusu dikte eder).
- Authenticated kanal üzerinden denetçiye gönderildi (örn. düzenleyici-
  tedarikli portal üzerinden parola-korumalı ZIP; 24-saat süre dolmalı
  imzalı S3 pre-signed URL).
- `compliance.artifacts_exported` event üzerinden audit chain'e geri
  receipt confirmation log'u.

## 5. ForgeLM'in katkısı

ForgeLM kendisi şifreleme uygulamaz ama ŞUNLARI YAPAR:

1. **Neyi şifreleyeceğini tespit et.** `forgelm audit` eğitim
   verisindeki PII, secrets ve credential'ları flag'ler ki operatör
   eğitim öncesi şifreleyebilir veya maskeleyebilir.
2. **Neyin şifrelendiğini rapor et.** ForgeLM şu anda
   `data_governance_report.json`'da substrate-seviyesi bir
   `encryption_at_rest` bayrağı yüzeylemiyor — operatör bu gerçeği
   out-of-band kayda alır (örn. cloud provider'ın KMS attestation'ı
   ile birlikte QMS evidence bundle'ında) ta ki config-seviyesi bir
   field eklenene kadar (Phase 28+ backlog'u).
3. **Şifreleme-sonrası bütünlüğü doğrula.** `forgelm verify-audit`
   chain-after-decryption'ın chain-as-emitted ile eşleştiğini teyit
   eder; substrate-seviyesi herhangi bir bozulma tespit edilebilirdir.
4. **Düşük-entropi identifier'ları hash'le.** Salted SHA-256, audit
   chain'in kendisinin atıl-durumda gizli olması için şifrelenmesine
   ihtiyaç olmadığı anlamına gelir — yalnız salt'ın.

## 6. Doğrulama checklist'i

Şifreleme-at-rest kontrollerinizi yürüyen operatör denetçi için:

- [ ] Tüm eğitim-host diskleri `dmsetup table` (Linux) veya
      `manage-bde -status` (Windows) encryption-in-use gösteriyor.
- [ ] Cloud depolama bucket'larının default-encryption ayarı
      customer-managed key ile etkin.
- [ ] `<output_dir>/.forgelm_audit_salt` mod `0600` ve service
      hesabı tarafından sahipli.
- [ ] Audit log replikasyon hedefi write-once (S3 Object Lock,
      Azure Immutable Blob).
- [ ] KMS audit log'unda quarterly anahtar-rotasyon kanıtı.
- [ ] Hiçbir ForgeLM artefaktı şifrelenmiş substrate dışında
      yaşamıyor (teyit etmek için `find` + storage-policy raporları
      kullan).

## 7. İnceleme

| Versiyon | Tarih | Yazar | Değişiklikler |
|---------|------|--------|---------|
| 1.0 | [DATE] | [AUTHOR] | İlk versiyon (Wave 4 / Faz 23) |
