# SOP: Veri Yönetimi ve Yönetişim

> Standart İşletim Prosedürü — [YOUR ORGANIZATION]
> EU AI Act Referansı: Madde 17(1)(f), Madde 10

## 1. Amaç

LLM fine-tuning için eğitim verisinin toplanması, annotation'ı,
saklanması ve yönetişimi için standartları tanımla.

## 2. Kapsam

Model eğitimi, validation ve değerlendirme için kullanılan tüm veri kümeleri.

## 3. Roller

| Rol | Sorumluluk |
|------|---------------|
| **Data Steward** | Veri kalitesi gözetimi, yönetişim uyumluluğu |
| **ML Engineer** | Veri hazırlama, ön işleme, format doğrulama |
| **DPO (Data Protection Officer)** | Kişisel veri değerlendirmesi, DPIA inceleme |

## 4. Veri Toplama

### 4.1 Gereksinimler

- [ ] Veri kaynağını ve toplama yöntemini config'de dokümante et:
  ```yaml
  data:
    governance:
      collection_method: "Dahili bilgi tabanından manuel curasyon"
  ```
- [ ] Temsil ediciliği değerlendir: veri hedeflenen dağıtım bağlamını yansıtıyor mu?
- [ ] Coğrafi, demografik ve bağlamsal denge kontrolü
- [ ] Bilinen biasları config'de dokümante et:
  ```yaml
  data:
    governance:
      known_biases: "Veri kümesi EU bölgesindeki İngilizce konuşan müşterilere meyilli"
  ```

### 4.2 Kişisel Veri

- [ ] Veri kümesinin kişisel veri içerip içermediğini belirle
- [ ] İçeriyorsa: Veri Koruma Etki Değerlendirmesi (DPIA) tamamla
- [ ] Config'de dokümante et:
  ```yaml
  data:
    governance:
      personal_data_included: true
      dpia_completed: true
  ```
- [ ] Veri minimizasyonu uygula — yalnız gerekli kişisel veriyi dahil et
- [ ] Mümkün olduğunda anonimleştirme/pseudonymization uygula

## 5. Veri Hazırlama

### 5.1 Annotation

- [ ] Annotation sürecini dokümante et:
  ```yaml
  data:
    governance:
      annotation_process: "Örnek başına iki annotator, kıdemli annotator tarafından arabuluculuk"
  ```
- [ ] Annotator-arası anlaşma kayıtlarını koru
- [ ] Annotation rehberlerini sürüm kontrol et

### 5.2 Kalite Kontrolleri

ForgeLM otomatik kontroller:
- Veri kümesi fingerprinting (SHA-256 hash, boyut, timestamp)
- Trainer tipine göre format doğrulama (SFT, DPO, KTO, GRPO)
- Metin temizleme (`clean_text: true`)
- **ForgeLM audit pipeline (v0.5.0+, `forgelm audit <jsonl>`; legacy
  `forgelm --data-audit` flag'i v0.8.0'da kaldırıldı)** —
  per-split sample sayıları, uzunluk dağılımı, top-3 dil tespiti,
  near-duplicate oranı (varsayılan 64-bit simhash, veya >50K-row
  corpora için `--dedup-method minhash` üzerinden **MinHash LSH**),
  cross-split leakage check, severity tier'lı PII flag sayıları
  (email / phone / issuer-prefix + Luhn credit card / IBAN / TR–DE–FR–US
  national IDs) ve always-on **secrets/credentials scan** (AWS /
  GitHub / Slack / OpenAI / Google / JWT / private-key headers) ile
  `data_audit_report.json` üretir; opsiyonel **heuristic quality
  filter** (`--quality-filter`) `quality_summary` block ekler.
  `forgelm audit --croissant` raporun yanına Google Croissant 1.0
  veri kümesi kartı emit eder.  Trainer'ın `output_dir`'inde mevcut
  olduğunda rapor EU AI Act Madde 10 yönetişim artefaktına otomatik
  inline edilir — operatörler bundle'ı kendine yeterli tutmak için
  audit'i eğitim **öncesi** çalıştırmalıdır.  Phase'ler 11 + 11.5 +
  12 + 12.5 birlikte `v0.5.0` consolidation release'inde gönderildi
  (PyPI 2026-04-30).
- **ForgeLM ingestion pipeline (v0.5.0+, `forgelm ingest`)** — ham
  PDF / DOCX / EPUB / TXT / Markdown'u SFT-ready JSONL'e çevirir,
  paragraph / sliding / **markdown-aware** chunking, DOCX→markdown
  tablo koruması, opsiyonel **Presidio ML-NER** adapter (`--pii-ml`,
  `[ingestion-pii-ml]` extra) ve chunk'lar depolamaya ulaşmadan
  tespit edilen PII / credential span'larını redact etmek için
  `--pii-mask` / `--secrets-mask` / `--all-mask` shorthand'leri.

Manuel kontroller:
- [ ] `forgelm audit` çalıştır ve `data_audit_report.json` incele:
  cross-split leakage > %0, near-duplicate oranı, beklenmedik dil
  karışımı, PII flag sayıları, **`secrets_summary` (sıfır olmayan
  herhangi bir sayı stop-the-line bir olaydır; eğitim öncesi
  remediate et)** ve `--quality-filter` kullanıldıysa
  `quality_summary`.
- [ ] Sample inceleme: 50+ rastgele örnek incele.
- [ ] Etiket doğruluğunu doğrula (preference/KTO verisi için).

## 6. Veri Saklama ve Retention

- Eğitim verisi sürüm kontrollü veya immutable depolamada saklanır
- SHA-256 fingerprint her eğitim koşumu için `data_provenance.json`'da kaydedilir
- Eğitim verisi en az **5 yıl** model artefaktlarıyla birlikte saklanır
- Erişim yetkili ML ekibi üyeleriyle sınırlıdır

## 7. İnceleme

| Versiyon | Tarih | Yazar | Değişiklikler |
|---------|------|--------|---------|
| 1.0 | [DATE] | [AUTHOR] | İlk versiyon |
