---
title: ISO 27001 / SOC 2 Operatörü
description: Denetim katı cookbook'u — sekiz yaygın soru, bunları yanıtlayan ForgeLM artefaktları ve 93-kontrollü SoA matrisi.
---

# ISO 27001 / SOC 2 Operatörü

> Yazılım ISO 27001 sertifikalı OLAMAZ — yalnız organizasyonlar sertifika alır. ForgeLM, ISO 27001:2022 Annex A kontrolleri ve AICPA SOC 2 Trust Services Criteria ile **uyumludur (aligned)**: ForgeLM'i eğitim pipeline'ınızda çalıştırmak, denetçinin açıkça istediği auditable kanıtları üretir. Bu sayfa navigasyon girişidir; derin cookbook docs ağacında yaşar.

## ForgeLM'in size sağladıkları

ISO / SOC 2 denetçisinin en çok önem verdiği dört sütun, doğrudan ForgeLM kanıtına sahiptir:

1. **Denetim izi** — eğitim çalışması başına `audit_log.jsonl`, HMAC + SHA-256 hash chain + genesis manifest sidecar ile append-only. `forgelm verify-audit` zinciri uçtan uca doğrular.
2. **Değişiklik kontrolü** — Article 14 staging gate'i (`forgelm approve` / `reject`) + `human_approval.required/granted/rejected` audit olayları + run başına damgalanmış `config_hash` (per-run manifest sidecar field).
3. **Veri lineage** — `data_provenance.json` + `data_governance_report.json` birlikte corpus + yönetişim duruşunu deterministik olarak pinler.
4. **Tedarik zinciri** — sürüm başına yayınlanan CycloneDX 1.5 SBOM, gecelik `pip-audit`, statik + dinamik güvenlik tarama için CI'da `bandit`.

## Sekiz denetim katı sorusu

Derin rehber her birini tam `jq` / CLI komutu + döndürdüğü artefakt ile yanıtlar:

1. "Son 90 gündeki her model promotion için audit trail'i göster" → `forgelm verify-audit` + `jq 'select(.event == "human_approval.granted")'`.
2. "Bana change-control kanıtını göster — bu modeli kim onayladı?" → `training.started` + `human_approval.granted` olaylarını çapraz referans alın; iki farklı operator ID, görev ayrılığını (segregation of duties) kanıtlar (ISO A.5.3, SOC 2 CC1.5).
3. "Veri lineage'ını göster" → `data_provenance.json`; `sha256` + `hf_revision` corpus'u pinler — ancak yalnızca `hf_revision_source` `loaded` olduğunda. `unverified`, SHA'nın paket yazılırken yapılan ve yüklemeyle hiçbir bağı olmayan bir Hub sorgusu olduğu anlamına gelir; `unresolved` ise hiçbir SHA alınmadığı ve hiçbir iddiada bulunulmadığı anlamına gelir.
4. "Tedarik zincirini göster" → `gh release download v0.7.0 --pattern 'sbom-*'`; CycloneDX 1.5 JSON.
5. "Erişim kontrollerini göster" → IdP audit log + `FORGELM_OPERATOR` çapraz referansı.
6. "Şifreleme duruşunu göster" → operatör-tarafı substrate (KMS audit log + `data_governance_report.json`).
7. "Olay müdahalesini göster" → `audit.classifier_load_failed` + F-compliance-110 strict gate.
8. "GDPR Article 15 + 17'ye yanıt verebileceğinizi göster" → `forgelm reverse-pii` + `forgelm purge`.

## Daha fazla okumak için nereye

Aşağıdaki dosyaların tümü toolkit ile birlikte GitHub'da yayımlanır — depoda yer alırlar ama manual görüntüleyicisinin parçası değildirler.

- Derin rehber — tam operatör cookbook'u, on-iki maddelik kurulum checklist'i, yaygın tuzaklar:
  [`iso_soc2_deployer_guide-tr.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/guides/iso_soc2_deployer_guide-tr.md)
- Tam ISO 27001:2022 Annex A kontrol eşlemesi × ForgeLM kanıtı:
  [`iso27001_control_mapping-tr.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/iso27001_control_mapping-tr.md)
- Tam SOC 2 Trust Services Criteria eşlemesi:
  [`soc2_trust_criteria_mapping-tr.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/soc2_trust_criteria_mapping-tr.md)
- Önceden doldurulmuş 93-kontrollü Statement of Applicability matrisi:
  [`statement_of_applicability-tr.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/qms/statement_of_applicability-tr.md)
- Önceden doldurulmuş risk register:
  [`risk_treatment_plan-tr.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/qms/risk_treatment_plan-tr.md)
- Substrate-tarafı şifreleme rehberliği:
  [`encryption_at_rest-tr.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/qms/encryption_at_rest-tr.md)
- Operatör kimliği + secret yönetimi:
  [`access_control-tr.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/qms/access_control-tr.md)
- Olay müdahale runbook'u:
  [`sop_incident_response-tr.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/qms/sop_incident_response-tr.md)
- Değişiklik yönetimi runbook'u:
  [`sop_change_management-tr.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/qms/sop_change_management-tr.md)

## Ayrıca bakınız

- [Audit Log](#/compliance/audit-log) — denetçinin yürüdüğü append-only zincir.
- [Human Oversight](#/compliance/human-oversight) — Article 14 staging gate'i.
- [Tedarik Zinciri](#/operations/supply-chain) — SBOM + pip-audit + bandit operatör yüzeyi.
- [CI/CD Pipeline'ları](#/operations/cicd) — pipeline'ın kanıtı nasıl ürettiği.
