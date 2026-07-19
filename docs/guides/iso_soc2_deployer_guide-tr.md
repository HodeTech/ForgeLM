# ISO 27001 / SOC 2 Type II — Operatör (deployer) denetim cookbook'u

> Hedef kitle: ISO 27001 iç denetim VEYA SOC 2 Type II gözlem dönemi
> için cevap hazırlayan compliance ekibiniz.
>
> **Kritik çerçeveleme**: yazılım ISO 27001 sertifikalı OLAMAZ — yalnız
> organizasyonlar sertifika alır. ForgeLM, eğitim pipeline'ınızda
> çalıştırıldığında denetçinin açıkça istediği auditable kanıtları
> ürettiği anlamda ISO 27001:2022 Annex A kontrolleri ve AICPA SOC 2
> Trust Services Criteria ile **uyumludur (aligned)**. Bu rehber,
> denetim katındaki yaygın soruları teker teker yürütür ve hangi
> ForgeLM artefaktının cevap verdiğini gösterir.
>
> Çapraz referans:
> [`docs/design/iso27001_soc2_alignment.md`](../design/iso27001_soc2_alignment.md)
> tam tasarım gerekçesi + 93 kontrollü kapsama haritası için.

## ForgeLM'in size out-of-the-box sağladıkları

ISO / SOC 2 denetçisinin en çok önem verdiği dört sütun, doğrudan
ForgeLM kanıtına sahip:

1. **Audit trail** — eğitim koşumu başına `audit_log.jsonl`,
   append-only HMAC + SHA-256 hash chain + genesis manifest sidecar.
   `forgelm verify-audit` zinciri uçtan uca doğrular.
2. **Change control** — Madde 14 staging gate (`forgelm approve` /
   `reject`) + `human_approval.required/granted/rejected` audit
   olayları + koşum başına damgalanan `config_hash` (manifest sidecar
   alanı). Her model promotion çift kontrollü ve forensic olarak
   attribute edilmiştir.
3. **Data lineage** — `data_provenance.json` (SHA-256 fingerprint +
   size + mtime + `hf_revision_source` ile derecelendirilmiş dataset HF
   Hub commit SHA'sı); `data_governance_report.json`
   (collection_method, annotation_process, known_biases,
   personal_data_included, dpia_completed).
4. **Supply chain** — yayın etiketi başına yayın matrisinin her (OS
   × Python-version) hücresi için emit edilen CycloneDX 1.5 SBOM.
   Wave 4 statik + dinamik güvenlik taraması için `pip-audit`
   nightly + `bandit` CI ekler.

## Denetim gözlem dönemi öncesi setup checklist'i

Denetçi sormadan **önce** indirmeniz gereken on iki madde.

### Identity + secrets

- [ ] **`FORGELM_OPERATOR` ayarla** her CI runner'da — makine-okur
      namespaced bir tanımlayıcı kullanın (örn.
      `gha:Acme/repo:training:run-${{ github.run_id }}`). Önerilen
      form için `docs/qms/access_control.md` §3'e bakın.
- [ ] **`FORGELM_AUDIT_SECRET` üretin** KMS / Vault'unuzda
      (32+ rastgele bayt, AES-256-GCM-strength entropi).
- [ ] **`FORGELM_AUDIT_SECRET` rotasyonunu output-dir lifecycle'lar
      arasında planlayın** — her girişin HMAC'ı emit anındaki secret'a
      bağlı olduğundan, rotasyon mevcut `audit_log.jsonl` +
      `.manifest.json` çiftini arşivledikten *sonra* yapılmalıdır,
      asla output-dir ortasında değil. (`forgelm verify-audit
      --require-hmac` secret karışmış zinciri tasarım gereği
      doğrulayamaz.)
- [ ] **Approver identity ≠ trainer identity'yi yapılandırın** —
      Madde 14 staging gate sizin in-pipeline Change Advisory
      Board'unuzdur.

### Pipeline yapılandırması

- [ ] **`evaluation.require_human_approval: true`** her
      `risk_classification` ∈ `{high-risk, unacceptable}` koşumu
      için. Unutursanız F-compliance-110 `ConfigError` raise eder.
- [ ] **Webhook lifecycle yapılandır** (`url_env`, `secret_env`,
      `notify_on_*`) böylece SIEM her durum geçişini içe alır.
- [ ] **`auto_revert: true` etkinleştir** üretim eğitimi için —
      kalite regresyonu otomatik olarak baseline modele döner.

### Audit + monitoring

- [ ] **Haftalık `forgelm verify-audit` cron zamanla** her üretim
      `<output_dir>` üzerinde. Sıfır-olmayan exit'e alarm bağla.
- [ ] **`audit_log.jsonl` gönder** write-once depolamaya (compliance
      modunda S3 Object Lock retention, Azure Immutable Blob,
      versioning'li MinIO).
- [ ] **Encrypt-at-rest substrate** model ağırlıkları, audit log,
      eğitim verisi için — asset başı substrate önerileri için
      `docs/qms/encryption_at_rest.md` belgesine bakın.

### Supply-chain hijyeni

- [ ] **ForgeLM'i pin'le** CI'da (`pip install forgelm==X.Y.Z`).
- [ ] **Nightly SBOM diff'lerine abone ol** bağımlı olduğunuz her
      yayın için.

## Denetçiyi kanıt üzerinde gezdirmek

En sık sorulan sekiz denetim-katı sorusu, kanıtı üreten birebir
ForgeLM artefaktı + grep / komut ile.

### S1: "Son 90 günde her model promotion için audit trail göster"

```bash
# Önce zincir bütünlüğünü doğrula (positional log_path; bu
# subcommand'ta tasarım gereği --json / --output-dir flag'i yoktur).
forgelm verify-audit ./outputs/audit_log.jsonl --require-hmac

# Sonra promotion olaylarını çıkar.
jq 'select(.event == "human_approval.granted")' ./outputs/audit_log.jsonl
```

Her `human_approval.granted` girişi şunları taşır:

- `operator` — kim onayladı (eğiten DEĞİL **onaylayan** kimliği).
- `run_id` — modeli üreten eğitim koşumuna geri bağlanır.
- `prev_hash` + `_hmac` — zincir bütünlüğü.
- Eğitim-koşumu kimliği — `config_hash`, temel model adı + adapter
  yöntemi (`model_lineage`) ve dataset fingerprint (`data_provenance`)
  — koşumun `compliance_report.json` dosyasında yaşar (yanındaki
  `training_manifest.yaml` yan dosyası düzleştirilmiş bir operatör
  özetidir ve bu üç bloğun hiçbirini taşımaz); `config_hash`
  ayrıca önceki `human_approval.required` audit event'ine de
  damgalanır, böylece denetçi `run_id` üzerinden o event'e (veya
  rapora) pivot eder ve `git log` içindeki YAML ile diff alır.
  (Not: ForgeLM temel modeli adıyla kaydeder ve `model.revision`
  ayarlandığında — ya da o olmadan commit çözülebildiğinde — temel-model
  yüklemesini sabitler ve o commit'i `model_lineage.base_model_revision`
  altına kaydeder. `revision_resolved`'ı yalnızca null değilken yeniden
  üretilebilirlik kanıtı sayın; yanındaki `revision_requested` değeri,
  operatörün `main` gibi hareketli bir ref'i sabitlediğini açıkça
  gösterir. Güvenlik sınıflandırıcısı, LLM judge, GRPO ödül modeli ve
  merge kaynak modelleri **hâlâ sabitlenmemiş olarak yüklenir** —
  bunların `*_revision` config alanları kabul edilir ve doğrulanır, ancak
  henüz hiçbir yükleyiciye iletilmez. Terfi edilen artefaktlar
  `model_integrity.json` / `model.integrity_verified` event'i üzerinden
  hash ile doğrulanmaya devam eder. `forgelm approvals`, `config_hash`'i
  `human_approval.required` event'inden okur ve hiçbir mevcut
  producer'ın emit etmediği legacy `config_fingerprint` anahtarına
  fallback yapar. Bkz. `docs/reference/approvals_subcommand.md`.)

### S2: "Change-control kanıtı göster — bu modeli kim onayladı?"

Zincirin kendisi change-control kanıtıdır. Çapraz referans:

```bash
# 1. Koşum X için training event'ı bul.
jq 'select(.run_id == "X" and .event == "training.started")' \
    ./outputs/audit_log.jsonl

# 2. Aynı koşum için approval event'ı bul.
jq 'select(.run_id == "X" and .event == "human_approval.granted")' \
    ./outputs/audit_log.jsonl

# 3. Operator ID'lerin farklı olduğunu teyit et.
jq -r 'select(.run_id == "X" and (.event == "training.started" or .event == "human_approval.granted")) | .operator' \
    ./outputs/audit_log.jsonl | sort -u
```

İki farklı operator ID görev ayrılığını kanıtlar (ISO A.5.3, SOC 2
CC1.5).

### S3: "Data lineage göster"

```bash
cat ./outputs/data_provenance.json
# {
#   "dataset_id": "Acme/customer-support-v3",
#   "hf_revision": "9c7c8f3...",
#   "hf_revision_source": "loaded",
#   "sha256": "ab12...",
#   "size_bytes": 14982011,
#   "modified": "2026-05-01T08:14:33Z",
#   ...
# }
```

`sha256` + `hf_revision` birlikte korpusu **yalnızca
`hf_revision_source` değeri `loaded` olduğunda** deterministik olarak
pin'ler (yerel dosyalar ek olarak `size_bytes` + `modified` alanları
taşır; alan adları `forgelm.compliance._fingerprint_local_file`'dan
gelir). Aynı girdi üzerinde `forgelm audit data/*.jsonl` çalıştıran
bir denetçi aynı fingerprint'i görmek zorundadır.

`hf_revision`'ı okumadan önce `hf_revision_source`'u okuyun:

| `hf_revision_source` | Ne anlama gelir | Kanıt olarak kullanılabilir mi? |
|---|---|---|
| `loaded` | ForgeLM deponun commit'ini çözdü, tam olarak o SHA'yı `load_dataset(..., revision=...)`'a geçirdi ve yükleme başarılı oldu. `hf_revision` okunan korpustur. | **Evet** |
| `unverified` | Bu süreçteki hiçbir yükleme dataset'i sabitlemedi. `hf_revision`, manifest yazılırken yapılan, deponun güncel varsayılan-dal başına ait bir Hub sorgusudur. Bir ipucu, kanıt değil. `forgelm compliance-only` her zaman bunu üretir, çünkü korpusu hiç okumadan paket yazar. | **Hayır** |
| `unresolved` | Hiçbir SHA belirlenemedi. `hf_revision` **yoktur** ve `hf_revision_reason` nedenini belirtir (offline mod, `huggingface_hub` kurulu değil, Hub erişilemez, gated depo, SHA dönmedi). ForgeLM asla SHA uydurmaz. | **Hayır — ve hiçbir iddia da yok** |

**Bu sürümden önce üretilen manifest'ler `hf_revision`'ı `hf_revision_source`
anahtarı olmadan taşır.** O SHA'lar, yüklemeyle hiçbir bağı olmadan
manifest-üretim zamanında yapılan bağımsız bir Hub sorgusundan alınmıştı;
dolayısıyla etiketsiz olsalar da yukarıdaki anlamda `unverified`'dırlar.
Eski bir paketteki çıplak bir `hf_revision`'ı, modeli hangi korpusun
eğittiğinin kanıtı saymayın.

### S4: "Supply chain göster"

```bash
# SBOM artefaktını GitHub release sayfasından indir.
gh release download v0.5.5 --pattern 'sbom-*'

# Veya istek üzerine yeniden üret.
python3 tools/generate_sbom.py > sbom.json

# Son yayınla diff al.
diff <(jq -S . sbom-prev.json) <(jq -S . sbom.json)
```

CycloneDX 1.5 JSON her transitive bağımlılığı purl
(`pkg:pypi/...`) ve sürümle listeler. Dependency-Track CVE
korelasyonu için bunu doğal olarak içe alır.

### S5: "Erişim kontrolleri göster — yalnız yetkili reviewer'ların model onayladığını nasıl ispatlıyorsunuz?"

İki katman:

1. **IdP katmanı** — CI runner kimlikleriniz ve insan reviewer
   kimlikleriniz IdP'nizden çıkar. Denetçi issuance + revocation
   cadence'ini teyit etmek için IdP audit log'unuzu yürür.
2. **ForgeLM katmanı** — her approval event onaylayanın
   `FORGELM_OPERATOR`'unu kaydeder. Onaylayanın approval anında
   yetkili olduğunu teyit etmek için IdP audit log'u ile çapraz
   referans.

```bash
# Tüm approval event'leri approver id + timestamp ile.
jq -r 'select(.event == "human_approval.granted") |
       [.timestamp, .operator, .run_id] | @tsv' \
    ./outputs/audit_log.jsonl
```

### S6: "Encryption posture göster"

Bu **deployer-side**'dır — ForgeLM artefaktları şifrelemez,
substrate şifreler. Referans:

- `docs/qms/encryption_at_rest.md` — kurum içi politikanız ForgeLM
  artefakt sınıflarına eşlenmiş.
- KMS audit log — encryption-in-use'un substrate-side kanıtı.
- Operatör-tarafı şifreleme tanıklığı — ForgeLM config-seviyesi bir
  `encryption_at_rest` field'ı yüzeyleyene kadar (Phase 28+ backlog'u)
  QMS evidence bundle'ınızda out-of-band olarak kaydedilir.

### S7: "Incident response göster — koşum ortasında safety classifier crash ederse ne olur?"

Referans:

- `docs/qms/sop_incident_response.md` §4 (Wave 4 / Faz 23
  genişletme) güvenlik-incident playbook için.
- Zincirin kendisi: `audit.classifier_load_failed` event yangın +
  `pipeline.failed` propagate + koşum `final_model/` dizini ÜRETMEZ.
- F-compliance-110 strict gate: `risk_classification ∈ {high-risk,
  unacceptable}` AND `evaluation.safety.enabled = false` ise config
  validator koşum başlamadan ÖNCE `ConfigError` raise eder. Denetçi
  son 90 günde safety gate'siz hiçbir high-risk koşumu görmez.

### S8: "GDPR Madde 15 + 17 talebine cevap verebildiğini göster"

Madde 15 (erişim hakkı):

```bash
forgelm reverse-pii --query "alice@example.com" --type email \
    --output-dir ./outputs data/*.jsonl
# JSON envelope { matches: [...], match_count: N } döner
# Audit chain QUERY HASH'LENMİŞ ŞEKİLDE bir
# `data.access_request_query` event damgalar (asla raw — salt audit
# log'un kendisine karşı wordlist saldırılarına karşı koruma sağlar).
```

Madde 17 (silme hakkı):

```bash
forgelm purge --row-id "alice@example.com" --corpus data/2026Q2.jsonl \
    --output-dir ./outputs
# Audit chain `target_id` HASH'LENMİŞ ŞEKİLDE
# `data.erasure_requested` → `data.erasure_completed` (veya
# `data.erasure_failed`) damgalar.  Eğer satırı eğiten bir model
# `final_model/`e sahipse `data.erasure_warning_memorisation` DA
# yangın eder ve memorisation residual riskini özneye bildirmek
# zorundasınız.
```

Cross-tool digest correlation (purge `target_id` == reverse-pii
`query_hash` aynı `output_dir`'deki aynı identifier için) Wave 3
follow-up testleriyle pin'lenmiştir.

## Yaygın tuzaklar

Operatörlerin ilk denetimlerinde yanlış yaptığı şeyler:

1. **Pipeline'lar arasında `FORGELM_OPERATOR` paylaşımı.** "Sadece
   `FORGELM_OPERATOR=ci` yaptık" — audit chain yorumlanamaz hale
   gelir çünkü her giriş aynı string'e attribute olur. Pipeline
   başına + koşum başına namespaced identifier kullanın.
2. **Webhook URL'lerini YAML'de saklamak.** `webhook.url_env:
   SLACK_WEBHOOK` → secret manager'dan runtime'da resolve; plaintext
   webhook URL'sini asla version control'e commit'leme. Rotate edilse
   bile denetçi `git log`'da tarihsel exposure görür. (Not: ForgeLM şu
   an webhook gövdelerini **imzalamıyor** — gateway-düzeyi bütünlük
   mTLS ya da hedef-tarafı kontrollerine bağlı; HMAC body imzası Phase
   28+ backlog'unda.)
3. **CI'da `forgelm verify-audit` atlamak.** "Audit chain'e zımnen
   güveniyoruz" savunulabilir bir pozisyon değildir. Haftalık cron
   + sıfır-olmayan exit'e alarm zamanlayın; alarm geçmişini
   denetçiye gösterin.
4. **Manifest sidecar'ı unutmak.** `forgelm verify-audit` zinciri
   sidecar olmadan da uçtan uca yürür (manifest temel chain check
   için kesin gerekli değildir), ama **truncate-and-resume
   tampering**'ini ortaya çıkaran şey sidecar'dır — mevcut olduğunda
   verifier, manifest'in pin'lenmiş first-entry SHA-256 + run_id
   alanlarını canlı log'un ilk satırıyla çapraz kontrol eder.
   Manifest olmadan bu saldırı sınıfı sessizce iner. Tam
   tamper-detection kapsaması için **ikisini de** aynı write-once
   substrate'e backup'layın.
5. **Üretim eğitiminde `auto_revert` yok.** Her zaman yeşil
   eğitime bahis koyuyorsanız, regulator-reportable incident'a
   bir safety-classifier degradation'ı uzaktasınız. `auto_revert:
   true` etkinleştirin ve `model.reverted` event'lerinin
   çalışan safeguard kanıtı olarak birikmesine izin verin.
6. **Üretimde `FORGELM_ALLOW_ANONYMOUS_OPERATOR=1` ile ForgeLM
   çalıştırmak.** O env var yalnız kısa-ömürlü test koşumları
   içindir. Operator identity olmadan üretim koşumları ISO A.6.4
   + A.6.5 bulgusudur.

## Bkz.

- [`../design/iso27001_soc2_alignment.md`](../design/iso27001_soc2_alignment.md) — tam tasarım gerekçesi + 93 kontrol haritası.
- [`../qms/encryption_at_rest-tr.md`](../qms/encryption_at_rest-tr.md) — substrate-side şifreleme rehberi.
- [`../qms/access_control-tr.md`](../qms/access_control-tr.md) — operator identity + secrets management.
- [`../qms/risk_treatment_plan-tr.md`](../qms/risk_treatment_plan-tr.md) — pre-populated risk register.
- [`../qms/statement_of_applicability-tr.md`](../qms/statement_of_applicability-tr.md) — 93-kontrol SoA matrisi.
- [`../qms/sop_incident_response-tr.md`](../qms/sop_incident_response-tr.md) — incident response runbook (Wave 4 genişletme).
- [`../qms/sop_change_management-tr.md`](../qms/sop_change_management-tr.md) — change management runbook (Wave 4 genişletme).
- [`../reference/iso27001_control_mapping-tr.md`](../reference/iso27001_control_mapping-tr.md) — ISO 27001:2022 Annex A kontrolleri × ForgeLM kanıtı.
- [`../reference/soc2_trust_criteria_mapping-tr.md`](../reference/soc2_trust_criteria_mapping-tr.md) — SOC 2 Trust Services Criteria × ForgeLM kanıtı.
- [`../reference/supply_chain_security-tr.md`](../reference/supply_chain_security-tr.md) — SBOM + pip-audit + bandit overview.
- [`../reference/audit_event_catalog-tr.md`](../reference/audit_event_catalog-tr.md) — audit-event vocabulary.
