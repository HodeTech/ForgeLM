# ISO/IEC 27001:2022 Annex A — ForgeLM kontrol haritası

> ForgeLM özelliklerinin ISO 27001:2022 Annex A kontrollerine nasıl
> eşlendiğini özetleyen referans tablo. Şu belgelere eşlik eder:
> [`../guides/iso_soc2_deployer_guide-tr.md`](../guides/iso_soc2_deployer_guide-tr.md)
> ve tasarım dökümanı
> [`../design/iso27001_soc2_alignment.md`](../design/iso27001_soc2_alignment.md).
>
> **Coverage tier legend:**
>
> - **`FL`** — *ForgeLM-supported*: ForgeLM doğrudan audit kanıt üretir.
> - **`FL-helps`** — *Deployer responsibility, ForgeLM helps*: ForgeLM,
>   operatörün diğer kaynaklarla birleştirdiği kısmi kanıt sağlar.
> - **`OOS`** — *Out of scope*: yalnız operatör; ForgeLM hiçbir katkı yapmaz.
>
> **Bu sürüm için kapsama özeti:** FL 11 / FL-helps 48 / OOS 34
> (design doc §3.1–§3.4 row-by-row yeniden sayım; tema başına
> A.5: 3 / 24 / 10, A.6: 0 / 5 / 3, A.7: 0 / 0 / 14, A.8: 8 / 19 / 7).

## A.5 Organizasyonel kontroller (37)

| Kontrol | Tier | ForgeLM kanıtı |
|---|---|---|
| A.5.1 Bilgi güvenliği için politikalar | FL-helps | `audit_event_catalog.md` neyin loglandığını dokümante eder |
| A.5.2 Bilgi güvenliği rolleri ve sorumlulukları | FL-helps | `FORGELM_OPERATOR` + `roles_responsibilities.md` |
| A.5.3 Görev ayrılığı | FL-helps | `human_approval.required/granted` trainer ≠ approver'ı zorlar |
| A.5.4 Yönetim sorumlulukları | FL-helps | `forgelm doctor`; `compliance_report.json`; `training_manifest.yaml` |
| A.5.5 Yetkililerle iletişim | OOS | — |
| A.5.6 Özel ilgi gruplarıyla iletişim | OOS | — |
| A.5.7 Tehdit istihbaratı | OOS | — |
| A.5.8 Proje yönetiminde bilgi güvenliği | FL-helps | `risk_assessment` config; F-compliance-110 strict gate; Annex IV §9 |
| A.5.9 Bilgi ve ilişkili varlık envanteri | FL-helps | `data_provenance.json`; `model_integrity.json`; SBOM |
| A.5.10 Bilginin kabul edilebilir kullanımı | OOS | — |
| A.5.11 Varlıkların iadesi | OOS | — |
| A.5.12 Bilgi sınıflandırması | FL-helps | `compliance.risk_classification` 5-tier |
| A.5.13 Bilgi etiketleme | FL-helps | `model_card.md`; manifest'te risk class |
| A.5.14 Bilgi transferi | FL-helps | `safe_post` webhook discipline |
| A.5.15 Erişim kontrolü | FL-helps | Operator identity + salted hashing |
| A.5.16 Kimlik yönetimi | FL-helps | `FORGELM_OPERATOR` env contract |
| A.5.17 Kimlik doğrulama bilgileri | FL-helps | `safe_post` auth header'ları masklar; `_mask` token'ları gizler |
| A.5.18 Erişim hakları | FL-helps | `human_approval` gate |
| A.5.19 Tedarikçi ilişkilerinde bilgi güvenliği | FL-helps | `_fingerprint_hf_revision`; SBOM her bağımlılığı listeler |
| A.5.20 Tedarikçi anlaşmalarında bilgi güvenliği | OOS | — |
| A.5.21 ICT tedarik zincirinde bilgi güvenliği yönetimi | FL-helps | SBOM (CycloneDX 1.5); `pip-audit` nightly |
| A.5.22 Tedarikçi hizmetlerinin izlenmesi, gözden geçirilmesi ve değişim yönetimi | OOS | — |
| A.5.23 Bulut hizmetleri için bilgi güvenliği | OOS | — |
| A.5.24 Bilgi güvenliği olay yönetimi planlaması ve hazırlığı | FL-helps | `sop_incident_response.md`; audit chain durumu korur |
| A.5.25 Bilgi güvenliği olaylarının değerlendirilmesi ve karara bağlanması | FL-helps | `data.erasure_failed`, `pipeline.failed`, `audit.classifier_load_failed` |
| A.5.26 Bilgi güvenliği olaylarına yanıt | FL-helps | Audit chain HMAC öncesi/sonrası durumu korur |
| A.5.27 Bilgi güvenliği olaylarından öğrenme | FL-helps | `model.reverted` olayları post-mortem kanıtı biriktirir |
| A.5.28 Kanıt toplama | FL | `audit_log.jsonl` forensic-grade; `forgelm verify-audit` doğrular |
| A.5.29 Aksaklık sırasında bilgi güvenliği | FL-helps | `auto_revert` baseline-flip; `model.reverted` olayı |
| A.5.30 İş sürekliliği için ICT hazırlığı | OOS | — |
| A.5.31 Yasal, kanuni, düzenleyici ve sözleşmesel gerekliliklerin tanımlanması | FL-helps | EU AI Act + GDPR mappings; Annex IV bundle |
| A.5.32 Fikri mülkiyet hakları | FL-helps | SBOM'da lisans çıkarımı; HF model-card metadata |
| A.5.33 Kayıtların korunması | FL | Append-only + HMAC + manifest sidecar |
| A.5.34 Gizlilik ve PII korunması | FL | `forgelm reverse-pii` Madde 15; `forgelm purge` Madde 17 (ayrıca bkz. A.8.3) |
| A.5.35 Bilgi güvenliğinin bağımsız incelenmesi | OOS | — |
| A.5.36 Bilgi güvenliği için politikalar, kurallar ve standartlara uyum | FL-helps | Pydantic config validation; `forgelm doctor`; CI gates |
| A.5.37 Dokümante edilmiş işletim prosedürleri | FL-helps | `docs/qms/` SOPs |

## A.6 İnsan kaynağı kontrolleri (8)

| Kontrol | Tier | ForgeLM kanıtı |
|---|---|---|
| A.6.1 Tarama | OOS | — |
| A.6.2 İstihdam şartları ve koşulları | OOS | — |
| A.6.3 Bilgi güvenliği farkındalığı, eğitimi ve öğretimi | FL-helps | `audit_event_catalog.md` eğitim materyali işlevi de görür |
| A.6.4 Disiplin süreci | FL-helps | Operator attribution hesap verebilirliği korur |
| A.6.5 İstihdam değişimi veya sonlandırma sonrası sorumluluklar | FL-helps | Operator id rotasyonu; eski ID'ler audit history'de kalır |
| A.6.6 Gizlilik veya açıklamama anlaşmaları | OOS | — |
| A.6.7 Uzaktan çalışma | FL-helps | `forgelm doctor --offline`; air-gap pre-cache |
| A.6.8 Bilgi güvenliği olay raporlama | FL-helps | `pipeline.failed`, `data.erasure_failed`, `audit.classifier_load_failed`; webhook |

## A.7 Fiziksel kontroller (14)

Tüm A.7 kontrolleri **OOS**'tur (ForgeLM yazılımdır). Operatörün
SoA'sının uçtan uca auditable olması için tamlık adına listelenmiştir.

| Kontrol | Tier |
|---|---|
| A.7.1 Fiziksel çevreler | OOS |
| A.7.2 Fiziksel giriş | OOS |
| A.7.3 Ofislerin, odaların ve tesislerin güvenliğinin sağlanması | OOS |
| A.7.4 Fiziksel güvenlik izleme | OOS |
| A.7.5 Fiziksel ve çevresel tehditlere karşı koruma | OOS |
| A.7.6 Güvenli alanlarda çalışma | OOS |
| A.7.7 Açık masa ve açık ekran | OOS |
| A.7.8 Ekipman yerleşimi ve korunması | OOS |
| A.7.9 İşletme dışındaki varlıkların güvenliği | OOS |
| A.7.10 Depolama medyası | OOS |
| A.7.11 Destekleyici altyapılar | OOS |
| A.7.12 Kablolama güvenliği | OOS |
| A.7.13 Ekipman bakımı | OOS |
| A.7.14 Ekipmanın güvenli imhası veya yeniden kullanımı | OOS |

## A.8 Teknolojik kontroller (34)

| Kontrol | Tier | ForgeLM kanıtı |
|---|---|---|
| A.8.1 Kullanıcı uç cihazları | FL-helps | `forgelm doctor` env özeti |
| A.8.2 Ayrıcalıklı erişim hakları | FL-helps | Operator attribution; approval-gate operator separation |
| A.8.3 Bilgi erişim kısıtlaması | FL | Salted identifier hashing; `forgelm reverse-pii` Madde 15 |
| A.8.4 Kaynak koduna erişim | FL-helps | `model.trust_remote_code=False` default; `_fingerprint_hf_revision` |
| A.8.5 Güvenli kimlik doğrulama | FL-helps | `safe_post` non-HTTPS'de auth header reddeder |
| A.8.6 Kapasite yönetimi | FL-helps | `forgelm doctor` resource report; `resource_usage` manifest block |
| A.8.7 Kötü amaçlı yazılıma karşı koruma | OOS | — |
| A.8.8 Teknik zafiyetlerin yönetimi | FL-helps | SBOM; `pip-audit` nightly; `bandit` CI |
| A.8.9 Yapılandırma yönetimi | FL | YAML Pydantic ile valide (`extra="forbid"` her config bloğunda); `forgelm --dry-run` eğitim yapmadan resolve + valide eder; koşum başına `config_hash` (bkz. `compute_config_hash`) `training_manifest.yaml`'a, `human_approval.required` audit event'ine ve JSON çıktı zarfına bağlanır; `model_integrity.json`'da (`model.integrity_verified` audit event) çıktı artefaktlarının SHA-256 hash'leri |
| A.8.10 Bilgi silme | FL | `forgelm purge` Madde 17; salted-hash audit; `data.erasure_warning_memorisation` |
| A.8.11 Veri maskeleme | FL | `forgelm audit` regex + Presidio ML-NER |
| A.8.12 Veri sızıntısı önleme | FL | `forgelm reverse-pii` plaintext residual scan |
| A.8.13 Bilgi yedekleme | FL-helps | `audit_log.jsonl` + manifest yedeklenebilir |
| A.8.14 Bilgi işlem tesislerinin yedekliliği | OOS | — |
| A.8.15 Loglama | FL | `audit_log.jsonl` JSON Lines + HMAC + genesis manifest |
| A.8.16 İzleme aktiviteleri | FL-helps | Webhook lifecycle olayları; `safety_trend.jsonl` cross-run trend |
| A.8.17 Saat senkronizasyonu | FL-helps | Tüm audit girişleri ISO-8601 UTC |
| A.8.18 Ayrıcalıklı yardımcı program kullanımı | OOS | — |
| A.8.19 Operasyonel sistemlerde yazılım kurulumu | FL-helps | `forgelm doctor` paketler; `pyproject.toml` pin'ler |
| A.8.20 Ağ güvenliği | FL-helps | `safe_post` HTTPS-only / SSRF guard / no-redirect |
| A.8.21 Ağ hizmetlerinin güvenliği | FL-helps | TLS-only webhooks (HTTPS + SSRF guard); audit-log chain HMAC `FORGELM_AUDIT_SECRET` üzerinden (not: webhook **gövdeleri** HMAC ile imzalanmaz) |
| A.8.22 Ağ ayrımı | OOS | — |
| A.8.23 Web filtreleme | OOS | — |
| A.8.24 Kriptografi kullanımı | FL | SHA-256 + HMAC chain (per-run imzalama anahtarı = `SHA-256(FORGELM_AUDIT_SECRET ‖ run_id)`, bkz. `forgelm/compliance.py:104-114`); ayrıca purge / reverse-pii için salted SHA-256 identifier hashing |
| A.8.25 Güvenli geliştirme yaşam döngüsü | FL-helps | `docs/standards/code-review.md`, `release.md`, CI gates |
| A.8.26 Uygulama güvenliği gereksinimleri | FL-helps | F-compliance-110 strict gate; ReDoS guard |
| A.8.27 Güvenli sistem mimarisi ve mühendislik prensipleri | FL-helps | Append-only audit log mimarisi |
| A.8.28 Güvenli kodlama | FL-helps | `docs/standards/coding.md`; type hints; CommonMark escaping |
| A.8.29 Geliştirme ve kabul aşamasında güvenlik testi | FL-helps | `pytest` ~1493 test; `bandit` static analysis |
| A.8.30 Dış kaynaklı geliştirme | OOS | — |
| A.8.31 Geliştirme, test ve üretim ortamlarının ayrılması | FL-helps | `forgelm --dry-run`; staging dir |
| A.8.32 Değişim yönetimi | FL | `human_approval.required/granted/rejected` audit zinciri; promotion'a kadar staging snapshot saklanır; `model_integrity.json`'daki (`model.integrity_verified` audit event) SHA-256 artefakt hash'leri `forgelm verify-integrity`'nin terfi edilmiş bir modeli kayıtlı manifest'ine karşı diff'lemesini sağlar — not: ForgeLM bugün upstream temel-model Hub revision SHA'sını pin'lemez, yalnızca config kimliğini (`config_hash`) ve çıktı artefaktlarını pin'ler |
| A.8.33 Test bilgisi | FL-helps | `forgelm audit` test setlerinde de PII / secrets'i flag eder |
| A.8.34 Denetim testi sırasında bilgi sistemlerinin korunması | OOS | — |

## Bkz.

- [`../guides/iso_soc2_deployer_guide-tr.md`](../guides/iso_soc2_deployer_guide-tr.md) — operatör denetim cookbook'u.
- [`../design/iso27001_soc2_alignment.md`](../design/iso27001_soc2_alignment.md) — tam tasarım gerekçesi.
- [`soc2_trust_criteria_mapping-tr.md`](soc2_trust_criteria_mapping-tr.md) — SOC 2 mapping eşlikçisi.
- [`supply_chain_security-tr.md`](supply_chain_security-tr.md) — SBOM + pip-audit + bandit.
- [`audit_event_catalog-tr.md`](audit_event_catalog-tr.md) — audit-event vocabulary.
