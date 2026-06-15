---
title: Model Card
description: Otomatik üretilen şeffaflık dokümantasyonu — Madde 13.
---

# Model Card

EU AI Act Madde 13 şeffaflığı zorunlu kılar: deploy edilen AI sistemleri net dokümantasyonla birlikte sunulmalıdır — yetenekler, sınırlar, amaçlanan kullanım, eğitim verisi. ForgeLM her başarılı koşudan sonra HuggingFace-uyumlu `README.md` model card'ı üretir.

## ForgeLM'in otomatik doldurduğu

| Bölüm | Kaynak |
|---|---|
| **Model detayları** | `model.name_or_path`, eğitim paradigması, sürüm. |
| **Amaçlanan kullanım** | `compliance.intended_purpose`. |
| **Kapsam dışı kullanım** | `compliance.risk_assessment.foreseeable_misuse`. |
| **Eğitim verisi** | Her `datasets:` girdisinin audit özeti. |
| **Eğitim prosedürü** | YAML config snapshot. |
| **Değerlendirme** | `benchmark_results.json` özeti. |
| **Güvenlik** | `safety_report.json` özeti. |
| **Sınırlamalar** | `compliance.risk_assessment.residual_risks`. |
| **Atıf** | Sentetik veri kullanıldıysa teacher modeli atıfla anar. |
| **Lisans** | `compliance.license` alanından. |

Çıktı, HuggingFace Hub'a yüklenmeye hazır `checkpoints/run/README.md` dosyasıdır.

## Örnek çıktı

```markdown
# Acme Customer Support v1.2.0

ForgeLM 0.7.0 kullanılarak Qwen2.5-7B-Instruct'tan fine-tune edilmiş
müşteri-destek asistanı.

## Model detayları

- **Base model:** Qwen/Qwen2.5-7B-Instruct
- **Fine-tuning paradigması:** SFT → DPO
- **Parametre-verimli yöntem:** QLoRA (rank 16, alpha 32, DoRA açık)
- **Eğitim:** 2026-04-29
- **Diller:** Türkçe, İngilizce
- **Lisans:** Apache 2.0

## Amaçlanan kullanım

Türk telekom için çok dilli müşteri-destek asistanı. Authenticated
kullanıcı oturumlarında faturalandırma, plan ve teknik destek
sorularını yanıtlamak üzere deploy edilir.

## Kapsam dışı kullanım

Bu model şunlar için **uygun değildir**:
- Sosyal mühendislikte müşteri kimliği taklidi.
- Sahte fatura üretimi.
- Türkçe/İngilizce dil çiftinin dışındaki kullanım.
- Authentication veya rate-limiting olmadan kullanım.

## Eğitim verisi

- 12,400 tercih satırı (`data/preferences.jsonl`)
  - Audit verdict: warnings (12 PII orta-seviye flag, ingest'te maskelendi)
  - Split-arası örtüşme: 0
  - Dil dağılımı: %99.2 TR, %0.5 EN

## Eğitim prosedürü

Tam config `config_snapshot.yaml`'da. Öne çıkanlar:

- Trainer: `dpo`
- Beta: 0.1
- Learning rate: 5e-6
- Epoch: 1
- Batch size: 2 (32 etkili, accumulation ile)

## Değerlendirme

| Görev | Puan | Floor | Verdict |
|---|---|---|---|
| hellaswag | 0.617 | 0.55 | geçti |
| truthfulqa | 0.482 | 0.45 | geçti |
| arc_easy | 0.74 | 0.70 | geçti |

## Güvenlik

S1–S14 üzerinde Llama Guard 3 8B skorlama:

- Tüm bloklu kategoriler (S1, S2, S5, S10) pre-train baseline'ın 0.05 içinde.
- High severity'de kategori yok.

Tam rapor `artifacts/safety_report.json`'da.

## Sınırlamalar

- System prompt'a karşı adversarial jailbreak'ler ara sıra başarılı olabilir.
- 4096 token üzerindeki konuşma turlarında performans düşer.
- Model sadece Türkçe-İngilizce iki dilli veri üzerinde eğitildi — başka
  dil desteği yok.

## Uyumluluk

- EU AI Act: `artifacts/annex_iv_metadata.json`'da Annex IV teknik dokümantasyonu
- GDPR: PII ingest'te maskelendi; eğitim verisi tanımlanabilir özne tutmaz
- Audit log: `artifacts/audit_log.jsonl`

Ticari kullanım için bkz. `LICENSE`.

## Atıf

Bu modeli kullanırsanız lütfen şöyle atıf yapın:

```
@misc{acme2026,
  title  = {Acme Customer Support v1.2.0},
  author = {Acme Corp},
  year   = {2026},
  note   = {ForgeLM 0.7.0 ile fine-tune edildi}
}
```
```

## Konfigürasyon

Model card üretimi her başarılı koşudan sonra otomatiktir — etkinleştirmek veya devre dışı bırakmak için YAML anahtarı yoktur. ForgeLM'de `output:` üst-düzey config bloğu bulunmaz; böyle bir blok eklenirse Pydantic doğrulaması tarafından reddedilir (`extra="forbid"`). Template'i özelleştirmeniz gerekiyorsa, belgelemeden önce gerçek alan adı için `forgelm/config.py`'daki güncel şemayı kontrol edin.

## Manuel eklemeler

Varsayılan model card ForgeLM'in otomatik belirleyebildiklerini kapsar. Manuel eklemeler (teşekkürler, özel uyarılar) için koşudan sonra üretilen `README.md`'ye `## Notlar` bölümü ekleyin. Audit log bunu `model_card_amended` olayı olarak işler.

## Sık hatalar

:::warn
**Önceki koşulardan kalmış model card.** Her koşu `README.md`'yi üzerine yazar. Önceki sürümü manuel düzenlediyseniz o düzenlemeler kaybolur. Koşular arası kalıcı olması gereken eklemeler için onları `compliance.notes` YAML alanına koyun.
:::

:::warn
**`compliance.license`'ı unutmak.** Olmadan otomatik üretilen card "Lisans: belirtilmemiş" gösterir; çoğu dahili inceleme süreci başarısız olur. Lisansı açıkça ayarlayın.
:::

:::tip
HuggingFace Hub yayını için ForgeLM'in model card'ı HuggingFace'in standart front-matter formatını kullanır — `language:`, `license:`, `tags:` vb. — Hub UI'da doğru render olur.
:::

## Bkz.

- [Annex IV](#/compliance/annex-iv) — Madde 11 kardeşi.
- [Uyumluluk Genel Bakış](#/compliance/overview) — bağlam.
- [Konfigürasyon Referansı](#/reference/configuration) — model card girdileri olarak kullanılan `compliance.*` alanları.
