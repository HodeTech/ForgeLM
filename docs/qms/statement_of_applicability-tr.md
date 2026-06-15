# QMS: Statement of Applicability (SoA)

> Kalite Yönetim Sistemi — [YOUR ORGANIZATION]
> ISO 27001:2022 Annex A — clause 6.1.3 d) tarafından gerekli
> Çapraz referans: [`../design/iso27001_soc2_alignment.md`](../design/iso27001_soc2_alignment.md)
> tam mapping gerekçesi için.

## 1. Amaç

Statement of Applicability (SoA) bir ISO 27001 denetçisinin İLK
açtığı deliverable'dır. Her Annex A kontrolü için ISMS'inizin
**applicable** veya **excluded** olarak ele aldığını ve gerekçeyi
belirtir.

Bu şablon ForgeLM'in kendi kapsama haritasından pre-populated'dır
(çapraz referans verilen design dökümanına bakın). Operatör her
satırı ISMS context'lerine uyarlar — çoğu satır applicable kalır;
ForgeLM'in natively kapsadığı bir satır (örn. A.8.15 Logging)
operatöre atıfta bulunmak için spesifik ForgeLM-üretilmiş kanıt
verir.

## 2. SoA matrisi

Format: kontrol ID → applicability → gerekçe / ForgeLM kanıtı →
operatör-tarafı eylem.

### 2.1 A.5 Organisational controls (37)

| Kontrol | Applicable? | Uygulanabilirlik gerekçesi (uygun olduğunda ForgeLM kanıtı; aksi halde operatör-tarafı kapsam) | Implementation status |
|---|---|---|---|
| A.5.1 Bilgi güvenliği için politikalar | YES | EU AI Act Md. 17 QMS gerektirir; ForgeLM audit-event vocabulary neyin loglandığını dokümante eder | ForgeLM audit log'u referans veren kurum-genelinde ISMS politikası benimse |
| A.5.2 Bilgi güvenliği rolleri ve sorumlulukları | YES | `roles_responsibilities.md` QMS şablonu AI Officer / ML Lead / Data Steward / DPO tanımlar | Rol tanımlarını benimse |
| A.5.3 Görev ayrılığı | YES | `human_approval.required/granted` onaylayıcı kimliğini (`FORGELM_OPERATOR`'dan) **kaydeder** böylece eğitici ≠ onaylayıcı denetlenebilir; ForgeLM kendisi self-approval'ı reddetmez | CI runner kimliği ≠ insan reviewer kimliğini IdP seviyesinde yapılandır ki kayıtlı kimlikler çakışamasın |
| A.5.4 Yönetim sorumlulukları | YES | `forgelm doctor`, `compliance_report.json` yönetim-review artefaktları sağlar | Aylık review cadence |
| A.5.5 Yetkililerle iletişim | YES | EU AI Act Md. 73 ciddi-olay raporlama | Düzenleyici contact listesi tut |
| A.5.6 Özel ilgi gruplarıyla iletişim | YES | ML safety / red-team topluluğu | İlgili threat intel'e abone ol |
| A.5.7 Tehdit istihbaratı | YES | ML supply-chain CVE feed'leri, model-poisoning advisory'leri | İlgili feed'lere abone ol |
| A.5.8 Proje yönetiminde bilgi güvenliği | YES | `risk_assessment` config + F-compliance-110 strict gate; Annex IV §9 metadata | Proje sign-off'unda göm |
| A.5.9 Bilgi ve diğer ilişkili varlıkların envanteri | YES | `data_provenance.json`, `model_integrity.json`, SBOM | Kurumsal asset register tut |
| A.5.10 Bilginin kabul edilebilir kullanımı | YES | Standart kurumsal AUP | Benimse |
| A.5.11 Varlıkların iadesi | YES | Dağıtılan modellerin / eğitim host'larının decommissioning'i | Benimse |
| A.5.12 Bilgi sınıflandırması | YES | `compliance.risk_classification` 5-tier kurumsal gizlilik sınıflarına haritalanır | ForgeLM tier'larını corp class'larına haritala |
| A.5.13 Bilgi etiketleme | YES | `model_card.md`, manifest risk class'ı damgalar | Üzerine kurumsal etiket uygula |
| A.5.14 Bilgi transferi | YES | `safe_post` webhook discipline, payload'da plaintext PII yok | Webhook recipient'larıyla DTA imzala |
| A.5.15 Erişim kontrolü | YES | Operatör kimliği + salted hashing | IdP entegrasyonu |
| A.5.16 Kimlik yönetimi | YES | `FORGELM_OPERATOR` env contract | CI runner identity'yi yapılandır |
| A.5.17 Kimlik doğrulama bilgileri | YES | `safe_post` auth header'larını masklar; `_mask` token'ları gizler | Vault-store webhook secret'ları, HF token'ları |
| A.5.18 Erişim hakları | YES | `human_approval` gate | IdP'de RBAC |
| A.5.19 Tedarikçi ilişkilerinde bilgi güvenliği | YES | `_fingerprint_hf_revision`; SBOM | Vendor risk programı |
| A.5.20 Tedarikçi anlaşmalarında bilgi güvenliği | YES | Standart tedarikçi MSA güvenlik clause'ları | Benimse |
| A.5.21 ICT tedarik zincirinde bilgi güvenliği yönetimi | YES | SBOM (Wave 2 dönemi); `pip-audit` nightly (Wave 4) | CVE izleme |
| A.5.22 Tedarikçi hizmetlerinin izlenmesi, gözden geçirilmesi ve değişim yönetimi | YES | Vendor yıllık review | Benimse |
| A.5.23 Bulut hizmetleri için bilgi güvenliği | YES | Cloud sağlayıcı güvenlik yapılandırması | Cloud-spesifik kontroller |
| A.5.24 Bilgi güvenliği olay yönetimi planlaması ve hazırlığı | YES | `sop_incident_response.md`; audit chain durumu korur | IR ekibi kur |
| A.5.25 Bilgi güvenliği olaylarının değerlendirilmesi ve karara bağlanması | YES | `data.erasure_failed`, `pipeline.failed`, `audit.classifier_load_failed` event'ler `error_class` + `error_message` ile | Triage runbook |
| A.5.26 Bilgi güvenliği olaylarına yanıt | YES | Audit chain HMAC öncesi/sonrası korur | Runbook dokümante et |
| A.5.27 Bilgi güvenliği olaylarından öğrenme | YES | `model.reverted` event'leri post-mortem kanıtı biriktirir | Haftalık post-mortem cadence |
| A.5.28 Kanıt toplama | YES | `audit_log.jsonl` forensic-grade; `forgelm verify-audit` doğrular | Write-once depolamaya gönder |
| A.5.29 Aksaklık sırasında bilgi güvenliği | YES | `auto_revert` baseline-flip; `model.reverted` event | Base-model retention dokümante et |
| A.5.30 İş sürekliliği için ICT hazırlığı | YES | DR planlama | Benimse |
| A.5.31 Yasal, kanuni, düzenleyici ve sözleşmesel gerekliliklerin tanımlanması | YES | EU AI Act + GDPR mapping'ler; Annex IV bundle | Kural değişikliklerini izle |
| A.5.32 Fikri mülkiyet hakları | YES | SBOM'da lisans çıkarımı; HF model-card metadata | Per-model lisans review |
| A.5.33 Kayıtların korunması | YES | Append-only + HMAC + manifest sidecar; off-site replica | Off-site backup |
| A.5.34 Gizlilik ve PII korunması | YES | `forgelm reverse-pii` Md. 15; `forgelm purge` Md. 17; `forgelm audit` PII tespiti + maskeleme | DSAR workflow + DPIA |
| A.5.35 Bilgi güvenliğinin bağımsız incelenmesi | YES | Yıllık external denetim | Benimse |
| A.5.36 Bilgi güvenliği için politikalara, kurallara ve standartlara uyum | YES | Pydantic validation; CI gate'ler; `forgelm doctor` | Kurum-genelinde uygulama |
| A.5.37 Dokümante edilmiş işletim prosedürleri | YES | QMS şablonları (5 Wave 0 SOPs + 4 Wave 4 / Faz 23 eklemeleri) | Uyarlama + benimseme |

### 2.2 A.6 People controls (8)

| Kontrol | Applicable? | Gerekçe | Implementation status |
|---|---|---|---|
| A.6.1 Tarama | YES | HR background-check politikası | Benimse |
| A.6.2 İstihdam şartları ve koşulları | YES | Standart NDA / IP clause'ları | Benimse |
| A.6.3 Bilgi güvenliği farkındalığı, eğitimi ve öğretimi | YES | `audit_event_catalog.md` eğitim materyali işlevi de görür; `deployer_instructions.md` Md. 13 çıktısı | Yıllık EU AI Act + GDPR eğitimi |
| A.6.4 Disiplin süreci | YES | Operatör attribution hesap verebilirliği korur | Benimse |
| A.6.5 İstihdam değişimi veya sonlandırma sonrası sorumluluklar | YES | Operatör id rotasyonu; eski ID'ler audit history'de kalır | Ayrılan CI runner'ı iptal et; önceki eylemleri denetle |
| A.6.6 Gizlilik veya açıklamama anlaşmaları | YES | Standart NDA | Benimse |
| A.6.7 Uzaktan çalışma | YES | `forgelm doctor --offline`; air-gap pre-cache | VPN politikası |
| A.6.8 Bilgi güvenliği olay raporlama | YES | `pipeline.failed`, `data.erasure_failed`, `audit.classifier_load_failed` event'ler; webhook lifecycle | SIEM ingestion + alarm |

### 2.3 A.7 Physical controls (14) — bir software-toolkit ISMS'sinden tipik olarak EXCLUDED

Bu kontroller ForgeLM'in ISMS scope'undan hariç tutulur çünkü ForgeLM
bir software toolkit'tir. **Operatörün ISMS scope'u kendi SoA'sında
bunları DAHİL EDER;** ForgeLM'in burada exclusion'ı yalnız
ForgeLM-spesifik kontrol envanteriyle ilgilidir.

| Kontrol | Operatöre Applicable? | Excluded-from-ForgeLM gerekçesi |
|---|---|---|
| A.7.1 Fiziksel çevreler | YES (operatör ISMS) | ForgeLM yazılımdır; substrate-side |
| A.7.2 Fiziksel giriş | YES | Datacenter-side |
| A.7.3 Ofislerin, odaların ve tesislerin güvenliğinin sağlanması | YES | Datacenter-side |
| A.7.4 Fiziksel güvenlik izleme | YES | CCTV — substrate-side |
| A.7.5 Fiziksel ve çevresel tehditlere karşı koruma | YES | Substrate-side |
| A.7.6 Güvenli alanlarda çalışma | YES | SCIF politikaları |
| A.7.7 Açık masa ve açık ekran | YES | Endpoint politikası |
| A.7.8 Ekipman yerleşimi ve korunması | YES | Hardware placement |
| A.7.9 İşletme dışındaki varlıkların güvenliği | YES | MDM |
| A.7.10 Depolama medyası | YES | LUKS / FileVault / BitLocker — substrate-side |
| A.7.11 Destekleyici altyapılar | YES | UPS / generator |
| A.7.12 Kablolama güvenliği | YES | Datacenter cabling |
| A.7.13 Ekipman bakımı | YES | Hardware refresh |
| A.7.14 Ekipmanın güvenli imhası veya yeniden kullanımı | YES | E-waste politikası |

### 2.4 A.8 Technological controls (34)

| Kontrol | Applicable? | ForgeLM kanıtı | Operatör-tarafı eylem |
|---|---|---|---|
| A.8.1 Kullanıcı uç cihazları | YES | `forgelm doctor` — Python / CUDA / GPU / extras / HF auth / disk / `FORGELM_OPERATOR` checks | Endpoint hardening |
| A.8.2 Ayrıcalıklı erişim hakları | YES | Operatör attribution; approval gate non-trainer operator | IdP'de RBAC |
| A.8.3 Bilgi erişim kısıtlaması | YES | Salted identifier hashing; `forgelm reverse-pii` Md. 15 | DSAR workflow |
| A.8.4 Kaynak koduna erişim | YES | `model.trust_remote_code=False` default; `_fingerprint_hf_revision` commit SHA pin'ler | VCS erişim kontrolü |
| A.8.5 Güvenli kimlik doğrulama | YES | `safe_post` non-HTTPS'de auth header reddeder; webhook secret discipline | MFA + token rotasyonu |
| A.8.6 Kapasite yönetimi | YES | `forgelm doctor` resource report; `resource_usage` manifest block | Quota / autoscaling |
| A.8.7 Kötü amaçlı yazılıma karşı koruma | YES | Eğitim host'larında antivirus | Benimse |
| A.8.8 Teknik zafiyetlerin yönetimi | YES | SBOM; `pip-audit` nightly; `bandit` CI; Pydantic config validation | OS patch yönetimi |
| A.8.9 Yapılandırma yönetimi | YES | YAML Pydantic ile valide; `forgelm --dry-run` gate; `config_hash` (per-run manifest sidecar field) audit'te | Config'leri sürüm-kontrol |
| A.8.10 Bilgi silme | YES | `forgelm purge` Md. 17; salted hash audit; `data.erasure_warning_memorisation` flag | DSAR workflow |
| A.8.11 Veri maskeleme | YES | `forgelm audit` regex + Presidio ML-NER; `_SECRET_PATTERNS` credentials scan | Maskeleme politikası |
| A.8.12 Veri sızıntısı önleme | YES | `forgelm reverse-pii` plaintext residual scan; webhook asla raw rows taşımaz | Egress DLP |
| A.8.13 Bilgi yedekleme | YES | `audit_log.jsonl` + `.manifest.json` yedeklenebilir; `forgelm --compliance-export` ZIP | Off-site yedek |
| A.8.14 Bilgi işlem tesislerinin yedekliliği | YES | Multi-AZ infra | Benimse |
| A.8.15 Loglama | YES | `audit_log.jsonl` JSON Lines + HMAC + genesis manifest; `forgelm verify-audit` doğrular | SIEM'e gönder |
| A.8.16 İzleme aktiviteleri | YES | Webhook lifecycle event'leri; `safety_trend.jsonl` cross-run trend | Alarm eşikleri |
| A.8.17 Saat senkronizasyonu | YES | Tüm audit girişleri ISO-8601 UTC | Eğitim host'larında NTP |
| A.8.18 Ayrıcalıklı yardımcı program kullanımı | YES | OS-seviyesi privilege management | Benimse |
| A.8.19 Operasyonel sistemlerde yazılım kurulumu | YES | `forgelm doctor` paketleri rapor eder; `pyproject.toml` pin'ler; `pip install forgelm==X.Y.Z` | Package allowlist |
| A.8.20 Ağ güvenliği | YES | `safe_post` HTTPS-only / SSRF guard / no-redirect; `model.trust_remote_code=False` | Egress firewall |
| A.8.21 Ağ hizmetlerinin güvenliği | YES | TLS-only webhooks (HTTPS-only + SSRF guard); audit-log chain HMAC `FORGELM_AUDIT_SECRET` üzerinden (not: webhook **gövdeleri** HMAC ile imzalanmaz — hedef-tarafı authenticity alıcı sistemin sorumluluğundadır) | TLS 1.2+ enforcement |
| A.8.22 Ağ ayrımı | YES | VPC / subnet design | Benimse |
| A.8.23 Web filtreleme | YES | Egress proxy | Benimse |
| A.8.24 Kriptografi kullanımı | YES | SHA-256 + HMAC-SHA-256 (audit chain key = `SHA-256(FORGELM_AUDIT_SECRET ‖ run_id)`, bkz. `AuditLogger.__init__` — `forgelm/compliance.py:164`); ayrıca `forgelm purge` / `forgelm reverse-pii` için salted SHA-256 identifier hashing (`_purge._resolve_salt`, ayrı bir konu — chain-key türetimine katılmaz) | `FORGELM_AUDIT_SECRET` için KMS |
| A.8.25 Güvenli geliştirme yaşam döngüsü | YES | `docs/standards/code-review.md`, `release.md`, CI gate'leri | SDLC framework |
| A.8.26 Uygulama güvenliği gereksinimleri | YES | F-compliance-110 strict gate; Pydantic validation; `_reverse_pii`'da ReDoS guard | App-seviyesi tehdit modelleme |
| A.8.27 Güvenli sistem mimarisi ve mühendislik prensipleri | YES | Append-only audit log mimarisi; HMAC chain; lazy import; SSRF guard | Defence-in-depth |
| A.8.28 Güvenli kodlama | YES | `docs/standards/coding.md`; type hints; `_sanitize_md_list`'te CommonMark escaping | Custom-extension review |
| A.8.29 Geliştirme ve kabul aşamasında güvenlik testi | YES | `pytest` ~1493 test; `bandit` static analysis; `forgelm safety-eval` standalone gate | E2E güvenlik testleri |
| A.8.30 Dış kaynaklı geliştirme | YES | Üçüncü-taraf-developer güvenliği | Benimse |
| A.8.31 Geliştirme, test ve üretim ortamlarının ayrılması | YES | `forgelm --dry-run`; staging dir; `evaluation.require_human_approval` | Ayrı pipeline'lar |
| A.8.32 Değişim yönetimi | YES | `human_approval.required/granted/rejected`; `config_hash` (per-run manifest sidecar field); staging snapshot | CAB süreci |
| A.8.33 Test bilgisi | YES | `forgelm audit` test setlerinde de PII / secrets'i flag eder | Test-data-handling politikası |
| A.8.34 Denetim testi sırasında bilgi sistemlerinin korunması | YES | Read-only audit erişimi | Benimse |

## 3. Kapsama özeti

| Tema | Toplam | Applicable | Excluded (ForgeLM scope) | FL-supported | FL-helps |
|---|---|---|---|---|---|
| A.5 Organisational | 37 | 37 | 0 | 3 | 24 |
| A.6 People | 8 | 8 | 0 | 0 | 5 |
| A.7 Physical | 14 | 14 (operatör ISMS) | 14 (ForgeLM-spesifik) | 0 | 0 |
| A.8 Technological | 34 | 34 | 0 | 8 | 19 |
| **Toplam** | **93** | **93 (operatör ISMS)** | **14 (ForgeLM-spesifik)** | **11** | **48** |

Yukarıdaki §2.1–§2.4 (SoA matrisi) row-by-row yeniden sayım. Tema
başına tally — A.5: 3 / 24 / 10 OOS; A.6: 0 / 5 / 3 OOS; A.7: 0 /
0 / 14 OOS; A.8: 8 / 19 / 7 OOS — toplam 11 `FL` + 48 `FL-helps`
+ 34 OOS = 93. Design doc'un §3 "Coverage tally" paragrafıyla
(`docs/design/iso27001_soc2_alignment.md`)
çapraz kontrol; ikisi eşleşmek zorunda.

## 4. İnceleme

| Versiyon | Tarih | Yazar | Değişiklikler |
|---------|------|--------|---------|
| 1.0 | [DATE] | [AUTHOR] | İlk versiyon (Wave 4 / Faz 23) — 93 kontrol ForgeLM v0.5.5'e karşı skorlandı |

Yıllık review cadence:

- Applicability'yi yeniden teyit et (nadir değişimler — çoğu kontrol
  applicable kalır).
- Yeni bir ForgeLM phase göndertiğinde ForgeLM-evidence column'unu
  güncelle.
- Implementation-status column'unu operatör-tarafı kontrol postürünü
  yansıtmak için güncelle.
