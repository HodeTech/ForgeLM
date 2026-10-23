# SOP: Model Eğitimi ve Fine-Tuning

> Standart İşletim Prosedürü — [YOUR ORGANIZATION]
> EU AI Act Referansı: Madde 17(1)(b)(c)(d)

## 1. Amaç

Her adımda kalite, güvenlik ve uyumluluğu sağlayarak dil modellerinin
fine-tuning'i için standart prosedürü tanımla.

## 2. Kapsam

ForgeLM veya eşdeğer araçlar kullanan tüm LLM fine-tuning aktivitelerine uygulanır.

## 3. Roller

| Rol | Sorumluluk |
|------|---------------|
| **ML Engineer** | Config hazırlar, veri hazırlar, eğitim yürütür |
| **ML Lead / Reviewer** | Config ve değerlendirme sonuçlarını inceler, dağıtımı onaylar |
| **Data Steward** | Veri kalitesi ve yönetişim uyumluluğunu doğrular |
| **AI Officer** | Yüksek-riskli modeller için final onay |

## 4. Prosedür

### 4.1 Eğitim Öncesi

- [ ] Hedeflenen amacı tanımla ve config'de dokümante et (`compliance.intended_purpose`)
- [ ] Risk değerlendirmesini tamamla (YAML'de `risk_assessment:` bölümü)
- [ ] Veri kümesi kalitesini doğrula (data governance raporu çalıştır)
- [ ] Eğitim configini incele ve onayla (ML Lead PR review)
- [ ] Dry-run doğrulaması: `forgelm --config job.yaml --dry-run`

### 4.2 Eğitim Yürütme

- [ ] Eğitimi çalıştır: `forgelm --config job.yaml --output-format json`
- [ ] Webhook bildirimleri veya TensorBoard ile izle
- [ ] Eğitim otomatik artefaktlar üretir:
  - `audit_log.jsonl` — olay izi
  - `compliance_report.json` — tam audit kaydı
  - `data_provenance.json` — veri kümesi fingerprintleri

### 4.3 Eğitim Sonrası Değerlendirme

ForgeLM tarafından otomatik:
- [ ] Loss tabanlı değerlendirme (eval_loss vs baseline)
- [ ] Benchmark değerlendirmesi (lm-eval-harness görevleri)
- [ ] Güvenlik değerlendirmesi (Llama Guard classifier)
- [ ] LLM-as-Judge kalite skorlama

Manuel:
- [ ] ML Lead değerlendirme sonuçlarını inceler
- [ ] Eğer `require_human_approval: true` ise, ML Lead `checkpoints/compliance/` inceler ve dağıtımı onaylar
- [ ] 10+ temsili prompt üzerinde model çıktılarını spot-check et

### 4.4 Dağıtım Onayı

- [ ] **minimal-risk** için: ML Lead onayı yeterli
- [ ] **limited-risk** için: ML Lead + AI Officer onayı
- [ ] **high-risk** için: ML Lead + AI Officer + Yasal/Compliance review
- [ ] Operatör (deployer) talimatları (`deployer_instructions.md`) dağıtım ekibiyle paylaşılır
- [ ] Model bütünlüğü doğrulanır: `model_integrity.json` checksumları eşleşir

### 4.5 Kayıt Tutma

- Uyumluluk artefaktları saklama politikanızın belirlediği süre boyunca saklanır (örneğin EU AI Act Madde 18, yüksek riskli sistem sağlayıcılarından teknik dokümantasyonu, kalite yönetimi dokümantasyonunu ve AB uygunluk beyanını sistem piyasaya arz edildikten veya hizmete alındıktan sonra 10 yıl saklamalarını ister; size neyin uygulandığını kontrol edin)
- Kanıt paketi: `forgelm --config job.yaml --compliance-export ./archive/`
- Mümkünse immutable/append-only depolamada sakla

## 5. İstisnalar

Bu SOP'tan herhangi bir sapma dokümante edilmeli ve AI Officer tarafından onaylanmalıdır.

## 6. İnceleme

Bu SOP yıllık olarak veya eğitim pipeline'ında önemli değişiklikler olduğunda incelenir.

| Versiyon | Tarih | Yazar | Değişiklikler |
|---------|------|--------|---------|
| 1.0 | [DATE] | [AUTHOR] | İlk versiyon |
