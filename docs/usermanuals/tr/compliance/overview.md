---
title: Uyumluluk Genel Bakış
description: ForgeLM EU AI Act, GDPR ve ISO 27001 gerekliliklerini eğitim hattına nasıl eşler.
---

# Uyumluluk Genel Bakış

ForgeLM, eğitim hatlarını sadece bir CTO'ya değil, bir regülatöre savunması gereken ekipler için yapıldı. Her başarılı (veya başarısız) koşu, EU AI Act Madde 9-17'ye, GDPR Madde 5'e ve ISO 27001 kontrol hedeflerine temiz şekilde eşlenen yapılandırılmış bir kanıt paketi üretir.

```mermaid
flowchart TD
    Run[Eğitim koşusu] --> Audit[Veri denetimi]
    Run --> Train[Eğitim + checkpoint]
    Run --> Eval[Eval + güvenlik]
    Run --> Doc[Otomatik model card]

    Audit --> A10[Madde 10<br/>Veri yönetimi]
    Train --> A12[Madde 12<br/>Audit log]
    Eval --> A9[Madde 9<br/>Risk yönetimi]
    Eval --> A15[Madde 15<br/>Doğruluk]
    Doc --> A13[Madde 13<br/>Şeffaflık]
    Audit --> A11[Madde 11<br/>Annex IV]
    Train --> A11
    Eval --> A11

    classDef artifact fill:#1c2030,stroke:#0ea5e9,color:#e6e7ec
    classDef article fill:#161a24,stroke:#f97316,color:#e6e7ec
    classDef start fill:#1c2030,stroke:#22c55e,color:#e6e7ec
    class Run start
    class Audit,Train,Eval,Doc artifact
    class A9,A10,A11,A12,A13,A15 article
```

## Üretilen şeyler

`compliance:` bloğu doldurulmuş herhangi bir koşu (özellikle `compliance.risk_classification` artı `compliance.provider_name` / `compliance.intended_purpose`) standart top-level audit log'un yanında Madde 11 teknik dokümantasyonunu yayınlar:

```text
checkpoints/run/
├── audit_log.jsonl                ← Madde 12 — append-only event log (top-level)
├── audit_log.jsonl.manifest.json  ← genesis-pin sidecar (truncate-evidence)
└── compliance/
    ├── annex_iv_metadata.json     ← Madde 11 — teknik dokümantasyon (`forgelm verify-annex-iv` ile eşli)
    ├── data_audit_report.json     ← Madde 10 — veri yönetişim kanıtı
    ├── safety_report.json         ← Madde 9 + 15 — risk + güvenlik (when `evaluation.safety.enabled`)
    ├── benchmark_results.json     ← Madde 15 — doğruluk (when `evaluation.benchmark.enabled`)
    └── README.md                  ← Madde 13 — HuggingFace-uyumlu model card
```

`compliance.annex_iv: true` knob'ı **yoktur** — Annex IV emisyonu `compliance.risk_classification` ve doldurulmuş `compliance.provider_*` / `compliance.intended_purpose` varlığıyla sürülür. Aynı şekilde, ForgeLM `conformity_declaration.md` **üretmez** — Madde 16 uygunluk beyanı kod artifact'ı değil, deployer'ın imzaladığı teslim edilebilirdir. (`forgelm verify-annex-iv <yol>/annex_iv_metadata.json` Annex IV bundle'ını audit log'a karşı çapraz kontrol eder, ancak uygunluk beyanı yazmaz.)

## ForgeLM'in karşıladığı maddeler

| Madde | Konu | ForgeLM nasıl |
|---|---|---|
| **9** | Risk yönetimi | Otomatik geri alma + eşik kapıları + trend izleme. |
| **10** | Veri yönetimi | `forgelm audit` veri seti başına yönetişim kanıtı üretir. |
| **11** | Teknik dokümantasyon | `annex_iv_metadata.json` dolu Annex IV. |
| **12** | Kayıt tutma | Eğitim başlangıcı, eval kapıları, geri alma kararlarını kapsayan append-only `audit_log.jsonl`. |
| **13** | Şeffaflık | Otomatik üretilen model card; yetenekleri, sınırları, eğitim özetini listeler. |
| **14** | İnsan gözetimi | Opsiyonel `evaluation.require_human_approval: true` insan imzalayana kadar terfi engeller. |
| **15** | Doğruluk ve sağlamlık | Benchmark kapıları + güvenlik eval + cybersec (ingest'te PII / sırlar). |
| **16-17** | Uygunluk ve QMS | Beyan iskeleti + toolkit ile birlikte gelen QMS SOP'ları. |

Tam kod referansları için [GitHub'daki Compliance özeti](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/compliance_summary.md).

## ForgeLM'in iddia *etmediği*

:::warn
ForgeLM Annex IV tarzı teknik dokümantasyon **üretir**. Sisteminizi AI Act kapsamında bir yüksek-riskli AI sistemi olarak **sertifikalandırmaz** — bu, herhangi bir toolkit'in kapsamı dışındaki bir notified-body veya öz-değerlendirme faaliyetidir.

Audit log konvansiyonel olarak append-only ve SHA-256-anchored. Gerçek tamper-evidence için log'u ayrı, write-once depoya (S3 Object Lock, ledger DB) göndermeniz gerekir. Toolkit artifact'ı üretebilir; chain-of-custody operasyonel sorumluluğunuzdur.

PII / sırlar regex setleri tasarım gereği muhafazakar — false-negative'ları false-positive'lara tercih eder. Yüksek riskli corpus için manuel inceleme ile birlikte kullanın.
:::

## Compliance artifact'larını etkinleştir

YAML'da:

```yaml
compliance:
  provider_name: "Acme Corp"
  provider_contact: "compliance@acme.example"
  system_name: "TR Telekom Destek Asistanı"
  intended_purpose: "Türk telekom için müşteri-destek asistanı"
  known_limitations: "Tıbbi, hukuki veya finansal tavsiye için değildir."
  system_version: "v1.0.0"
  risk_classification: "high-risk"    # şunlardan biri: unknown | minimal-risk | limited-risk | high-risk | unacceptable

evaluation:
  require_human_approval: true        # opsiyonel Madde 14 kapısı (`compliance.human_approval` DEĞİL)
```

`compliance.annex_iv`, `compliance.data_audit_artifact`, `compliance.human_approval`, `compliance.deployment_geographies` veya `compliance.responsible_party` alanı yoktur — bunlar bu sayfanın daha önceki taslaklarının uydurduğu phantom anahtarlardır. Kanonik şema `forgelm/config.py` içindeki `ComplianceMetadataConfig`'tir. Veri-audit kanıtını pinlemek için trainer'la aynı `--output-dir`'a karşı `forgelm audit <corpus>` çalıştırın; ForgeLM `data_audit_report.json`'u otomatik yakalar.

`compliance:` bloğundan her alan `annex_iv_metadata.json`'a akar.

## Annex IV neyi içerir

Annex IV artefact'ının sekiz bölümü vardır; tamamı otomatik doldurulur:

1. **Genel açıklama** — model adı, kullanım amacı, dağıtım coğrafyası.
2. **Detaylı sistem açıklaması** — base model, eğitim paradigması, veri seti özeti.
3. **İzleme** — eval eşikleri, otomatik geri alma tetikleyicileri, trend takibi.
4. **Risk yönetimi** — risk sınıflandırması, azaltıcı önlemler, kalan riskler.
5. **Yaşam döngüsü** — eğitim tarihi, sürüm, kaynak verisine referanslar.
6. **Standartlar** — listelenen uyumluluk çerçeveleri (EU AI Act, GDPR, ISO 27001).
7. **Uyumluluk beyanı** — iskelet; nihai beyan insan imzası gerektirir.
8. **Pazar-sonrası izleme planı** — dağıtılmış gözetim config'ine işaretçi.

Tam şema için bkz. [Annex IV](#/compliance/annex-iv).

## Operasyonel sorumluluklar (siz, ForgeLM değil)

Toolkit kanıt üretir; insanlar sertifikasyonu üretir. Ekibiniz şunlardan sorumlu:

- Üretime giden her koşunun audit paketini incelemek.
- Audit log'u tamper-evidence için write-once depoya göndermek.
- Gerektiğinde notified-body ile uygunluk değerlendirmesi yapmak.
- Model deploy edildikten sonra post-market monitoring'i sürdürmek.
- Veri sahibi taleplerini (GDPR Madde 15-22) ele almak.

`docs/qms/`'deki ForgeLM QMS SOP'ları operasyonel tarafı kapsar.

## Bkz.

- [Annex IV](#/compliance/annex-iv) — Madde 11 artifact spec.
- [Audit Log](#/compliance/audit-log) — Madde 12.
- [İnsan Gözetimi](#/compliance/human-oversight) — Madde 14.
- [Model Card](#/compliance/model-card) — Madde 13.
- [GDPR / KVKK](#/compliance/gdpr).
