# Kalite Yönetim Sistemi (QMS) Şablonları

> **EU AI Act Madde 17** yüksek-riskli AI sistem sağlayıcılarının bir Kalite Yönetim Sistemi kurmasını gerektirir. Bu şablonlar bir başlangıç noktası sağlar — kurumunuza uyarlayın.

## Şablonlar

| Şablon | EU AI Act Referansı | Amaç |
|----------|-------------------|---------|
| [Model Eğitimi SOP](sop_model_training-tr.md) | Md. 17(1)(b)(c)(d) | Fine-tuning onay, yürütme ve doğrulama için standart prosedür |
| [Veri Yönetimi SOP](sop_data_management-tr.md) | Md. 17(1)(f), Md. 10 | Veri toplama, annotation, kalite güvencesi ve yönetişim |
| [Olay Yanıtı SOP](sop_incident_response-tr.md) | Md. 17(1)(h)(i) | Model arızalarını, güvenlik olaylarını ve düzeltici eylemleri yönetme |
| [Değişim Yönetimi SOP](sop_change_management-tr.md) | Md. 17(1)(b)(c) | Versiyonlama, inceleme, onay ve geri alma prosedürleri |
| [Roller & Sorumluluklar](roles_responsibilities-tr.md) | Md. 17(1)(m) | AI Officer, Data Steward, ML Engineer, Compliance Officer rolleri |
| [Erişim Kontrolü](access_control-tr.md) | Md. 17(1)(c) + ISO A.5.15-A.8.5 | Operatör kimliği + secret rotasyonu (Wave 4 / Faz 23) |
| [Encryption at Rest](encryption_at_rest-tr.md) | Md. 17(1)(c) + ISO A.5.33 + A.8.10 + A.8.24 | Substrate-side şifreleme rehberi (Wave 4 / Faz 23) |
| [Risk Treatment Plan](risk_treatment_plan-tr.md) | Md. 17(1)(c) + ISO A.5.7-A.8.30 | ISO 27005 risk register şablonu (Wave 4 / Faz 23) |
| [Statement of Applicability](statement_of_applicability-tr.md) | Md. 17(1)(c) + ISO 6.1.3 d) | 93-kontrol uygulanabilirlik matrisi (Wave 4 / Faz 23) |

## Nasıl Kullanılır

1. Bu şablonları kurumunuzun iç dokümantasyon sistemine kopyalayın
2. `[YOUR ORGANIZATION]` ifadesini şirket adınızla değiştirin
3. Her role gerçek isimler atayın
4. Yasal/uyumluluk ekibinizle inceleyin ve onaylayın
5. ForgeLM'in otomatik artefaktlarını kanıt olarak referans verin

## Bkz.

- [Uyumluluk özeti](../reference/compliance_summary-tr.md) — özlü EU AI Act + ISO + SOC 2 kanıt haritası.
- [ISO / SOC 2 operatör (deployer) rehberi](../guides/iso_soc2_deployer_guide-tr.md) — Wave 4 denetim cookbook'u.
- [Audit event kataloğu](../reference/audit_event_catalog-tr.md) — tam event vocabulary.

## ForgeLM Otomatik Artefaktları (QMS'ye Eşleşir)

| QMS Gereksinimi | ForgeLM Artefaktı | Üreten |
|----------------|-----------------|-------------|
| Eğitim kayıtları | `compliance_report.json` | `forgelm --config job.yaml` |
| Veri provenance | `data_provenance.json` | Koşum başına otomatik |
| Değerlendirme kanıtı | `benchmark_results.json`, `safety_results.json` | Koşum başına otomatik |
| Model kimliği | `model_integrity.json` | Koşum başına otomatik |
| Audit trail | `audit_log.jsonl` | Koşum başına otomatik |
| Risk değerlendirmesi | `risk_assessment.json` | Config `risk_assessment:` bölümünden |
| Operatör (deployer) talimatları | `deployer_instructions.md` | Koşum başına otomatik |
