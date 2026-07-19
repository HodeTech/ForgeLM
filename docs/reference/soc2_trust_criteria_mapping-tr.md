# AICPA SOC 2 Trust Services Criteria — ForgeLM haritası

> ForgeLM özelliklerinin AICPA SOC 2 Trust Services Criteria (2017
> framework, 2022 revize) ile nasıl eşlendiğini özetleyen referans
> tablo. Şu belgelere eşlik eder:
> [`../guides/iso_soc2_deployer_guide-tr.md`](../guides/iso_soc2_deployer_guide-tr.md)
> ve tasarım dökümanı
> [`../design/iso27001_soc2_alignment.md`](../design/iso27001_soc2_alignment.md).

## Kategoriler

2017 SOC 2 framework'ü 5 kategori tanımlar:

1. **Security** (Common Criteria CC1.x–CC9.x) — zorunlu baseline.
2. **Availability** (A1.x) — opsiyonel.
3. **Processing Integrity** (PI1.x) — opsiyonel.
4. **Confidentiality** (C1.x) — opsiyonel.
5. **Privacy** (P1.x–P8.x) — opsiyonel.

SOC 2 *Type II* engagement'i 6–12 ay'lık bir pencerede operating
effectiveness'i gözlemler. Common Criteria zorunludur; opsiyonel
kategoriler engagement-bazında scoplanır.

## Security — Common Criteria

| CC | Başlık | ForgeLM kanıtı |
|---|---|---|
| CC1.1 | Bütünlüğe bağlılık göstergesi | `roles_responsibilities.md` QMS template; AI Officer rolü |
| CC1.2 | Yönetişim bağımsızlığı | Approval gate operator ≠ trainer |
| CC1.3 | Yapı, raporlama hatları kurma | `roles_responsibilities.md` AI Officer / ML Lead / DPO tanımlar |
| CC1.4 | Yetkinliğe bağlılık göstergesi | Yıllık EU AI Act / GDPR eğitimi (operatör politikası) |
| CC1.5 | Hesap verebilirliği uygular | `FORGELM_OPERATOR` attribution + audit chain |
| CC2.1 | İç kontrolleri içe iletişim | `audit_event_catalog.md` + `deployer_instructions.md` |
| CC2.2 | Dışa iletişim | Madde 13 `deployer_instructions.md`; Annex IV public summary |
| CC2.3 | Düzenleyicilerle iletişim | Annex IV bundle + `compliance_report.json` operatörün talep üzerine düzenleyiciye gönderdiği artefaktlardır; ForgeLM iletişim kanalını işletmez (operatör-tarafı kontrol) |
| CC3.1 | Uygun hedefler belirler | `compliance.intended_purpose`; risk classification |
| CC3.2 | Riskleri tanımlar ve analiz eder | `risk_assessment` Pydantic block; safety eval; `risk_treatment_plan.md` |
| CC3.3 | Sahtekarlık risklerini değerlendirir | Audit log tamper-evidence; HMAC chain; manifest sidecar |
| CC3.4 | Değişimleri tanımlar ve değerlendirir | `human_approval.required/granted/rejected` audit zinciri; koşum başına `config_hash` (`compliance_report.json`'a, `human_approval.required` event'ine ve JSON zarfına damgalanır); diff için `model_integrity.json` / `model.integrity_verified` SHA-256 artefakt hash'leri; `model.revision` upstream temel modeli sabitler ve `model_lineage.base_model_revision` yüklemenin kullandığı commit'i kaydeder (judge ve GRPO ödül modeli kendi `*_revision` alanlarıyla sabitlenir, ancak hiçbir pin bir artefakta kaydedilmez; sınıflandırıcı ve merge-kaynağı yüklemeleri sabitlenmemiş kalır) |
| CC4.1 | Değerlendirmeleri seçer, geliştirir, gerçekleştirir | `forgelm verify-audit`; `forgelm safety-eval` |
| CC4.2 | İç kontrol eksikliklerini iletir | `pipeline.failed`/`reverted`/`erasure_failed` olayları |
| CC5.1 | Kontrol aktivitelerini seçer, geliştirir | F-compliance-110 strict gate; auto-revert; staging |
| CC5.2 | Genel BT kontrollerini seçer | `safe_post` HTTP discipline; Pydantic config validation |
| CC5.3 | Politikalar ve prosedürler dağıtır | `docs/qms/` 5 SOPs (Wave 0); 4 yeni Wave 4 / Faz 23'te |
| CC6.1 | Mantıksal-erişim güvenlik yazılımı | Operator-id attribution; HMAC chain |
| CC6.2 | Yeni iç kullanıcıları yetkilendirir | `human_approval.required`/`granted` chain |
| CC6.3 | Sonlandırılan kullanıcıların erişimini kaldırır | Operatör CI runner identity'sini iptal eder |
| CC6.4 | Fiziksel erişimi kısıtlar | OOS — datacenter güvenliği |
| CC6.5 | Yetkisiz imhaya karşı korur | `forgelm purge` Madde 17; salted hashing |
| CC6.6 | Mantıksal-erişim kontrolleri uygular | Salted hashing audit olaylarında; `forgelm reverse-pii` |
| CC6.7 | Bilgi hareketini kısıtlar | `safe_post` egress discipline; webhook payload curation |
| CC6.8 | Yetkisiz yazılımı tespit eder/önler | SBOM; `pip-audit` nightly; `bandit` CI |
| CC7.1 | Zafiyetleri tespit eder | `pip-audit` nightly; CVE feed |
| CC7.2 | Sistem bileşenlerini izler | `forgelm verify-audit`; `forgelm verify-gguf`; `forgelm verify-integrity`; `safety_trend.jsonl` |
| CC7.3 | Güvenlik olaylarını değerlendirir | `data.erasure_failed`, `pipeline.failed` olayları `error_class` + `error_message` ile |
| CC7.4 | Güvenlik olaylarına yanıt verir | `auto_revert`; `model.reverted` olayı |
| CC7.5 | Düzeltici eylemleri tanımlar, geliştirir | `human_approval.rejected`; `sop_change_management.md` |
| CC8.1 | Değişimleri yetkilendirir | `forgelm approve` Madde 14 gate; staging dir |
| CC9.1 | Riskleri tanımlar, yönetir | `risk_assessment` config + safety eval; `risk_treatment_plan.md` |
| CC9.2 | Tedarikçi + iş ortağı riskini yönetir | SBOM; `loaded` / `unverified` / `local_path` / `unresolved` olarak derecelendirilen dataset Hub commit SHA'sı; temel-model pin'i (`model.revision` → `model_lineage.base_model_revision`); lisans çıkarımı.  **Sabitlenen ama hiçbir artefakta kaydedilmeyen:** LLM judge, GRPO ödül modeli.  **Kapsanmayan:** güvenlik sınıflandırıcısı, merge kaynakları |

## Availability (A1.x)

ForgeLM tek-node CLI'dır; availability ağırlıklı olarak operatör-tarafıdır.

| Kontrol | ForgeLM katkısı |
|---|---|
| A1.1 Kapasite planlama | `forgelm doctor` resource report + `resource_usage` manifest |
| A1.2 Olaylardan kurtarma | `auto_revert` swap-back; resume'da audit chain continuity |
| A1.3 Çevresel korumalar | OOS — substrate-side |

## Processing Integrity (PI1.x)

Güçlü ForgeLM katkısı.

| Kontrol | ForgeLM katkısı |
|---|---|
| PI1.1 Girdi kalitesi | `compute_dataset_fingerprint`; `data_governance_report` |
| PI1.2 Sistem işleme | `forgelm verify-audit`; `data_audit_report.json` |
| PI1.3 Çıktıların doğruluğu | `model_integrity.json` SHA-256 checksums; `model_card.md` |
| PI1.4 Girdilerin izlenebilirliği | `data_provenance.json`'da ve `compliance_report.json`'un `data_provenance` bloğunda kayıtlı `compute_dataset_fingerprint` (dataset SHA-256 fingerprint) + `_fingerprint_hf_revision` (dataset HF Hub commit SHA'sı).  **SHA, yalnızca `hf_revision_source: loaded` olduğunda modeli eğiten korpusun kanıtıdır;** `unverified`, yüklemeyle hiçbir bağı olmayan manifest-zamanı bir Hub sorgusudur ve `unresolved` hiç SHA alınamadığı anlamına gelir.  `model_lineage`'de `_describe_adapter_method` + `config_hash` + `base_model_revision` |
| PI1.5 Çıktıların izlenebilirliği | Annex IV bundle manifest + report + audit + integrity'i co-locate eder |

## Confidentiality (C1.x)

| Kontrol | ForgeLM katkısı |
|---|---|
| C1.1 Gizli bilgilerin korunması | `forgelm audit` regex + Presidio ML-NER PII tespiti; `_SECRET_PATTERNS` credentials scan |
| C1.2 Gizli bilgilerin imhası | `forgelm purge` Madde 17; salted-hash audit |

## Privacy (P1.x – P8.x)

| Kontrol | ForgeLM katkısı |
|---|---|
| P1.1 Gizlilik bildirimi | Madde 13 `deployer_instructions.md` |
| P2.1 Seçim ve onay | `evaluation.require_human_approval` Madde 14 gate |
| P3.1 Toplama | `data.governance.personal_data_included`; `dpia_completed` |
| P3.2 Kişisel veri kalitesi | `data_audit_report.json` quality stats |
| P4.1 Kullanım, saklama ve imha | `retention.staging_ttl_days` (kanonik; eski takma ad `evaluation.staging_ttl_days` v0.8.0'da kaldırıldı); `forgelm purge --check-policy` |
| P5.1 Erişim | `forgelm reverse-pii` Madde 15 scan; salted query-hash |
| P5.2 Sorular ve şikayetler | (Operatör-tarafı workflow) |
| P6.1 Üçüncü taraflara açıklama | `safe_post` webhook discipline (yalnız TLS, SSRF koruması, yönlendirme reddi); not: webhook gövdeleri HMAC ile imzalanmaz — bkz. ISO 27001 A.8.21 |
| P6.2 Üçüncü taraf anlaşmaları | (Operatör DPA'ları) |
| P7.1 İhlal bildirimi | `data.erasure_failed`, `audit.classifier_load_failed` olayları |
| P7.2 İhlal açıklaması | (Operatör regulator-contact playbook) |
| P8.1 Sorular, şikayetler ve anlaşmazlıklar | `forgelm reverse-pii` + `forgelm purge` chain |

## Operatör eklentileri (ForgeLM'de değil)

- **Credential management:** `FORGELM_AUDIT_SECRET`, webhook URL,
  API anahtarları için Vault.
- **Evidence archive:** Write-once depolama (S3 Object Lock, Azure
  Immutable Blob) + uzun vadeli retention politikası.
- **Access control:** Operator attribution için IdP entegrasyonu;
  approval kararlarında MFA.
- **Incident response:** `data.erasure_failed`, safety classifier
  crash'leri, audit-chain kırıklıkları için playbook.
- **Monitoring:** Audit log'ların SIEM ingestion'ı; high/unacceptable-
  risk gate'lerde alarm; safety_eval için threshold tuning.
- **Documentation:** Risk Management dosyası, gizlilik bildirimi,
  deployer instructions dağıtımı, Annex IV posting.

## Bkz.

- [`../guides/iso_soc2_deployer_guide-tr.md`](../guides/iso_soc2_deployer_guide-tr.md) — operatör denetim cookbook'u.
- [`../design/iso27001_soc2_alignment.md`](../design/iso27001_soc2_alignment.md) — tam tasarım gerekçesi.
- [`iso27001_control_mapping-tr.md`](iso27001_control_mapping-tr.md) — ISO 27001 mapping eşlikçisi.
- [`supply_chain_security-tr.md`](supply_chain_security-tr.md) — SBOM + pip-audit + bandit.
- [`audit_event_catalog-tr.md`](audit_event_catalog-tr.md) — audit-event vocabulary.
