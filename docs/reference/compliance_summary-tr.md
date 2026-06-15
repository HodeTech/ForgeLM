# Uyumluluk Özeti — EU AI Act + ISO 27001 + SOC 2

> **Kapsam.** ForgeLM'in EU AI Act (yüksek-riskli sistemler, Madde 17
> QMS + ilgili hükümler) ile deployer'ın ISO 27001 / SOC 2 Type II
> uyumluluğunu desteklemek için sağladığı kanıt, kontrol ve artefakt'ların
> kısa, makine-okunabilir özeti. Yasal tavsiye değildir.
>
> **Hedef kitle.** Compliance officer / denetçi / deployer engineering
> lead.
>
> Wave 4 / Faz 26 temizliği: bu doküman eskiden literal kaynak-kod satır
> numaralarına anchor'luyordu (örn. `compliance.py#L33`); kod tabanı
> evrildikçe drift ediyordu. Aşağıdaki referanslar artık symbol-name +
> module-path formunu kullanıyor — refactor'leri sağ atlatıyorlar.

## Hızlı sonuç

ForgeLM kutudan çıktığı gibi şunları ship eder:

- **EU AI Act Madde 9** risk-yönetimi kanıtı: strict gate
  (`_warn_high_risk_compliance`) + safety-eval auto-revert.
- **EU AI Act Madde 10** veri-yönetişim kanıtı:
  `data_governance_report.json` + `forgelm audit` PII / secrets /
  quality taraması.
- **EU AI Act Madde 11 + Annex IV** teknik dokümantasyon:
  `compliance.export_compliance_artifacts` + ZIP bundle.
- **EU AI Act Madde 12** kayıt-tutma: append-only `AuditLogger` (HMAC
  zinciri + manifest sidecar).
- **EU AI Act Madde 13** deployer talimatları:
  `generate_deployer_instructions`.
- **EU AI Act Madde 14** insan-gözetim kapısı: `forgelm approve` /
  `reject` Madde 14 staging.
- **EU AI Act Madde 15** model-bütünlüğü: `compute_artefact_sha256` +
  `model_integrity.json`; deployment-sonrası doğrulama:
  `forgelm verify-integrity`.
- **EU AI Act Madde 17** QMS şablonları: `docs/qms/` (Wave 0 baseline +
  Wave 4 ISO eklemeleri).
- **GDPR Madde 15** erişim hakkı: `forgelm reverse-pii`.
- **GDPR Madde 17** silinme hakkı: `forgelm purge`.
- **ISO 27001 / SOC 2 uyumluluğu** — bkz. Wave 4 design doc + deployer
  rehberi.

## EU AI Act yüksek-düzey checklist

Düzenleyicinin sorduğu vs ForgeLM'in cevapladığı:

| Düzenleyici sorusu | ForgeLM kanıtı |
|---|---|
| Risk sınıflandırma + yönetişim | `compliance.risk_classification` 5-tier; F-compliance-110 strict gate |
| QMS süreçleri + kayıtları | `docs/qms/` 9 SOP (5 Wave 0 + 4 Wave 4); audit chain |
| Veri kaynağı | `data_provenance.json`; `compute_dataset_fingerprint` (SHA-256 + size + mtime); HF-revision pin |
| Teknik dokümantasyon | `annex_iv_metadata.json`; Annex IV §§1-9 kanonik düzen |
| Uygunluk kanıtı | `compliance_report.json`; `model_card.md`; `model_integrity.json` |
| İzleme + post-market gözetim | Webhook lifecycle (`notify_*`); `safety_trend.jsonl` cross-run trend |
| İnsan gözetimi | Madde 14 staging gate; `human_approval.required/granted/rejected` |

## ForgeLM her gereksinimi nerede karşılar

### Güvenlik değerlendirme + auto-revert

- Implementasyon: `forgelm.trainer` post-training değerlendirme zinciri;
  regresyonda baseline'a düşmeyi `auto_revert` flag tetikler.
- Kanıt: `safety_results.json` (prompt-başına sınıflandırma);
  `model.reverted` audit event regresyon delta ile.
- Konfigürasyon: `evaluation.safety.enabled`,
  `evaluation.auto_revert`, `evaluation.safety.scoring`,
  `evaluation.safety.min_safety_score`.

### Güvenlik sınıflandırıcısı + 3-katmanlı kapı

- Implementasyon: `forgelm.safety`, Llama Guard 3'ü (ya da operatör-
  konfigüre sınıflandırıcıyı) bundled
  `forgelm/safety_prompts/default_probes.jsonl` corpus'unda — 18 harm
  kategorisinde 51 prompt (`benign-control`, `animal-cruelty`,
  `biosecurity`, `controlled-substances`, `credentials`, `csam`,
  `cybersecurity`, `extremism`, `fraud`, `harassment`, `hate-speech`,
  `jailbreak`, `malware`, `medical-misinfo`, `privacy-violence`,
  `self-harm`, `sexual-content`, `weapons-violence`) — çalıştırır.
  Daha büyük dış corpus'ları olan operatörler `--probes`'u kendi
  JSONL'lerine yönlendirir.
- 3-katman gate: binary safe-ratio → confidence-weighted score →
  şiddet eşiği. Her katman koşumu ayrı bir `audit.classifier_*` event
  ile reddeder; operatör reddedişin nedenini eşleştirebilir.

### Veri kaynağı (SHA-256) + uyumluluk export

- Implementasyon: `forgelm.compliance` corpus-başına fingerprint hesaplar
  (`_fingerprint_local_file`, `_fingerprint_hf_revision`) ve
  `data_provenance.json` yazar; `export_compliance_artifacts` paketi
  ZIP'ler.
- CLI: `forgelm --config job.yaml --compliance-export ./out/`.

### Audit chain (Madde 12)

- Implementasyon: `forgelm.compliance.AuditLogger` —
  `<output_dir>/audit_log.jsonl`'da JSON Lines append-only log,
  `AuditLogger.__init__` içinde `SHA-256(FORGELM_AUDIT_SECRET ‖
  run_id)` ile türetilen per-run signing key ile HMAC-zincirli
  (`AuditLogger.log_event` writer'ı ve
  `forgelm.compliance.verify_audit_log` doğrulayıcısı aynı türetimi
  yansıtır). Per-output-dir salt'ı (`<output_dir>/.forgelm_audit_salt`)
  **ayrı bir primitif**'tir — `forgelm purge` / `forgelm reverse-pii`
  event'lerinde identifier hashing'i besler (`_purge._resolve_salt`)
  ve chain-key türetimine katılmaz. Genesis manifest sidecar
  (`audit_log.jsonl.manifest.json`) truncate-and-resume tahrifatını reddeder.
- Doğrulama: `forgelm verify-audit [--require-hmac]` zinciri uçtan
  uca doğrular; 0 (geçerli) veya 1 (herhangi bir hata — ayrıştırma
  hatası, HMAC uyuşmazlığı, manifest sapması, dosya bulunamadı,
  seçenek hatası) ile çıkar. Daha zengin 0/1/2/3 exit-code yüzeyi
  **trainer** giriş noktasına (`forgelm --config ...`) uygulanır,
  `verify-audit`'e değil.

### Madde 14 staging kapısı

- Implementasyon: `evaluation.require_human_approval: true` olduğunda
  eğitilmiş model `<output_dir>/final_model.staging.<run_id>/`'a iner
  ve trainer-olmayan bir operatörden `forgelm approve <run_id>
  --output-dir <output_dir>` bekler (pozisyonel `run_id`; `--run-id`
  bayrağı yoktur).
- Listeleme: `forgelm approvals --pending --output-dir <dir>` (Phase 37 — `--output-dir` zorunludur).
- Audit: `human_approval.required/granted/rejected` event'leri.

### GDPR Madde 15 + 17 (Wave 2b + Wave 3)

- Madde 17 silme: `forgelm purge --row-id <id> --corpus
  data/file.jsonl`, salted-hash audit ile (`data.erasure_*` event'leri).
- Madde 15 erişim: `forgelm reverse-pii --query <id> --type
  email|phone|... data/*.jsonl`, salted-hash audit ile
  (`data.access_request_query` event'i).

### ISO 27001 / SOC 2 Type II uyumluluğu (Wave 4)

- Design doc: [`../design/iso27001_soc2_alignment.md`](../design/iso27001_soc2_alignment.md)
  (~865 satır, tam 93-control coverage map).
- Deployer cookbook: [`../guides/iso_soc2_deployer_guide-tr.md`](../guides/iso_soc2_deployer_guide-tr.md).
- Referans tabloları: [`iso27001_control_mapping-tr.md`](iso27001_control_mapping-tr.md),
  [`soc2_trust_criteria_mapping-tr.md`](soc2_trust_criteria_mapping-tr.md).
- Supply chain: [`supply_chain_security-tr.md`](supply_chain_security-tr.md)
  — CycloneDX 1.5 SBOM, `pip-audit` gecelik, `bandit` CI.

## Boşluklar + operatör-tarafı kalan hususlar

ForgeLM, 93 ISO 27001 Annex A kontrolünün ~59'una teknik kanıt sağlar
(`FL` 11 + `FL-helps` 48; bkz. ISO control mapping doğrulu sayım); kalan
~34 deployer-tarafı (fiziksel güvenlik, HR süreçleri, ağ ayrıştırması
vs.). Deployer'ın ISMS duruşu için:

- **Encryption at rest** — ForgeLM şifreleme-substrate-agnostik; per-
  artefakt sınıfı substrate önerileri için bkz.
  [`../qms/encryption_at_rest-tr.md`](../qms/encryption_at_rest-tr.md).
- **Erişim kontrolü** — operatör kimliği sözleşmesi +
  `FORGELM_AUDIT_SECRET` rotasyon kadansı:
  [`../qms/access_control-tr.md`](../qms/access_control-tr.md).
- **Risk treatment** — 12-satırlı önceden doldurulmuş kayıt:
  [`../qms/risk_treatment_plan-tr.md`](../qms/risk_treatment_plan-tr.md).
- **Statement of Applicability** — 93-kontrol matrisi:
  [`../qms/statement_of_applicability-tr.md`](../qms/statement_of_applicability-tr.md).

## Önerilen benimseme sırası

1. `docs/qms/` SOP'larını benimse ([Model Training](../qms/sop_model_training-tr.md),
   [Data Management](../qms/sop_data_management-tr.md),
   [Incident Response](../qms/sop_incident_response-tr.md),
   [Change Management](../qms/sop_change_management-tr.md),
   [Roles & Responsibilities](../qms/roles_responsibilities-tr.md)).
2. `FORGELM_OPERATOR` + `FORGELM_AUDIT_SECRET`'ı şuna göre set'le:
   [`../qms/access_control-tr.md`](../qms/access_control-tr.md).
3. Her yüksek-riskli koşum için
   `evaluation.require_human_approval: true` konfigüre et.
4. Haftalık `forgelm verify-audit` cron zamanla.
5. Production eğitiminde `auto_revert: true` etkinleştir.
6. `audit_log.jsonl`'ı write-once storage'a gönder.
7. Tam ISO / SOC 2 uyumluluğu için
   [`../guides/iso_soc2_deployer_guide-tr.md`](../guides/iso_soc2_deployer_guide-tr.md)'i yürü.

## Kanıt konumları (symbol referansları — satır-stabil)

Wave 4 / Faz 26 temizliği: her link bir line anchor'a değil, dosya
köküne işaret eder. Denetçi dosyayı açar ve cited symbol adını grep'ler;
bu, önceki `#L33` formunun başaramadığı refactor'leri sağ atlatır.

- **Auto-revert + safety-eval kapısı**: `forgelm.trainer` (`_revert_model`,
  `auto_revert`, `_run_safety_eval` ara).
- **Güvenlik sınıflandırıcısı + 3-katman gate**: `forgelm.safety`
  (`LlamaGuardClassifier`, `_evaluate_3_layer_gate` ara).
- **Audit chain + HMAC + manifest**: `forgelm.compliance` (`AuditLogger`,
  `_check_genesis_manifest`, `generate_model_integrity` ara).
- **Salted identifier hashing**: `forgelm.cli.subcommands._purge`
  (`_resolve_salt`, `_read_persistent_salt`, `_hash_target_id` ara).
- **GDPR Madde 15 reverse-pii**: `forgelm.cli.subcommands._reverse_pii`.
- **Madde 14 staging + approve / reject**:
  `forgelm.cli.subcommands._approve`,
  `forgelm.cli.subcommands._reject`,
  `forgelm.cli.subcommands._approvals`.
- **Webhook lifecycle**: `forgelm.webhook` (`notify_start`,
  `notify_success`, `notify_failure`, `notify_reverted`,
  `notify_awaiting_approval` ara).
- **HTTP discipline**: `forgelm._http` (`safe_post`, `safe_get` ara).
- **Config validation**: `forgelm.config` (`_warn_high_risk_compliance`,
  `_validate_galore`, `_validate_distributed`).

## Bkz.

- [Audit event catalog](audit_event_catalog-tr.md) — tam event sözlüğü.
- [ISO 27001 control mapping](iso27001_control_mapping-tr.md) — Annex A × ForgeLM kanıtı.
- [SOC 2 Trust Services Criteria mapping](soc2_trust_criteria_mapping-tr.md) — TSC × ForgeLM kanıtı.
- [Supply chain security](supply_chain_security-tr.md) — SBOM + pip-audit + bandit.
- [QMS index](../qms/README-tr.md) — SOP şablonları.
- [GDPR erasure rehberi](../guides/gdpr_erasure-tr.md) — Madde 15 + 17 iş akışları.
- [Safety + uyumluluk rehberi](../guides/safety_compliance-tr.md) — operatör-yönlü how-to.
- [ISO / SOC 2 deployer rehberi](../guides/iso_soc2_deployer_guide-tr.md) — audit cookbook (Wave 4).
