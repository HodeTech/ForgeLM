# SOP: AI Modelleri için Değişim Yönetimi

> Standart İşletim Prosedürü — [YOUR ORGANIZATION]
> EU AI Act Referansı: Madde 17(1)(b)(c)
> ISO 27001:2022: A.5.36, A.8.9, A.8.32
> SOC 2: CC8.1, CC3.4, CC5.3

## 1. Amaç

Fine-tuned modellere, eğitim yapılandırmalarına ve değerlendirme
kriterlerine yapılan değişiklikleri yönetme prosedürünü tanımla.

## 2. Değişim Kategorileri

| Kategori | Örnekler | Onay Gerekli |
|----------|---------|------------------|
| **Major** | Yeni base model, yeni trainer tipi, risk kategorisi değişimi | AI Officer + ML Lead |
| **Minor** | Hyperparameter tuning, LoRA rank değişimi, veri kümesi güncellemesi | ML Lead |
| **Patch** | Bug fix, config formatlama, dokümantasyon | ML Engineer (self-approve) |

## 3. Değişim Prosedürü

### 3.1 Önerme

1. Branch oluştur: `git checkout -b change/description`
2. Eğitim configini (YAML) değiştir
3. PR açıklamasında değişim gerekçesini dokümante et
4. Dry-run çalıştır: `forgelm --config new_config.yaml --dry-run`

### 3.2 İnceleme

1. `main`'e Pull Request aç
2. PR şunları içermelidir:
   - [ ] Neyin değiştiğini gösteren config diff
   - [ ] Değişim gerekçesi
   - [ ] Model kalitesi/güvenliği üzerinde beklenen etki
   - [ ] **Major** değişiklikler için: güncellenmiş risk değerlendirmesi
3. İnceleyici kontroller:
   - [ ] Config doğrulanır (`--dry-run` geçer)
   - [ ] Güvenlik değerlendirmesi etkin (yüksek-riskli modeller için)
   - [ ] Config'de secret veya token yok

### 3.3 Yürütme

1. PR'ı merge et (branch protection kuralları başına onay gerektirir)
2. CI/CD pipeline eğitimi tetikler: `forgelm --config job.yaml`
3. ForgeLM yeni koşum için uyumluluk artefaktları otomatik üretir
4. Yeni `audit_log.jsonl` girişleri değişimi izler

### 3.4 Doğrulama

1. Yeni model metriklerini önceki sürümle karşılaştır
2. Benchmark skorlarını, güvenlik skorlarını, judge skorlarını incele
3. `require_human_approval: true` ise: dağıtım öncesi açık onay

### 3.5 Geri Alma

Yeni model daha kötüyse:
- ForgeLM `auto_revert` eğitim sırasında otomatik geri alma yapar
- Dağıtım sonrası geri alma için:
  1. Önceki model sürümünü yeniden dağıt
  2. Geri almayı olay log'una dokümante et
  3. Kök nedeni soruştur

## 4. Formal değişim-kontrol mekanizması olarak CI gate'leri

Wave 4 / Faz 23 genişletmesi: bu bölüm, hangi CI gate'lerinin
operatöre dönük değişim-kontrol kanıtı oluşturduğunu formalize
eder. ISO A.8.32 "bilgi işleme tesisleri ve sistemlere yapılan
değişimler değişim yönetim prosedürlerine tabi olmalıdır" gerektirir;
aşağıdaki tablo ForgeLM-instrumented operatörler için prosedürü
dokümante eder.

| Gate | Araç | Arıza modu | ISO kontrolü |
|---|---|---|---|
| Lint clean | `ruff check .` + `ruff format --check .` | Stil / sözdizimi sorunları | A.8.28 |
| Pydantic field-description guard | `tools/check_field_descriptions.py --strict forgelm/config.py` | Operatöre dönük config drift | A.5.36 |
| HTTP discipline | `ci.yml`'da `grep`-tabanlı regex guard | Disiplinsiz `requests.*` / `urllib.*` / `httpx.*` çağrısı | A.8.20 |
| Birim + entegrasyon testleri | `pytest -q` | Dokümante edilmiş contract'a regresyon | A.8.29 |
| Coverage zemini | `--cov-fail-under=40` (`pyproject.toml`) | Yeni kod kapsamsız | A.8.29 |
| CLI dry-run | `forgelm --config config_template.yaml --dry-run` | Config schema break | A.8.9 |
| Site-as-tested-surface | `tools/check_site_claims.py --strict` | Pazarlama iddiası koddan drift | A.5.31 |
| Bilingual H2/H3/H4 parity | `tools/check_bilingual_parity.py --strict` | EN/TR docs yapısal drift | A.5.36 |
| Bandit static-security (Wave 4) | `bandit -c pyproject.toml -r forgelm/` | High-severity bulgu | A.8.26, A.8.28 |
| pip-audit nightly (Wave 4) | `pip-audit --format json` | High-severity transitive CVE | A.8.8, A.8.21 |
| SBOM determinism (Wave 4) | `pytest tests/test_supply_chain_security.py` | SBOM içerik drift | A.5.21, A.8.8 |

Bir değişim her gate geçene kadar `main`'e (veya Wave-style flow'lar
için `development`'a) merge edilemez. PR review template'i
(`.github/PULL_REQUEST_TEMPLATE.md`) her gate'in yeşil olduğunu
açıkça teyit eden bir checkbox gerektirmelidir.

### 4.1 Change Advisory Board (CAB) yedek olarak Approval Gate

Yeni eğitim koşumu üreten değişimler için (yapılandırma-yalnız
değişimler vs.), **Madde 14 staging gate** operatörün in-pipeline
CAB'sıdır:

1. CI eğitim job'u modeli
   `<output_dir>/final_model.staging.<run_id>/`'e yerleştirir.
2. `human_approval.required` audit event yangın eder.
3. Bir reviewer (eğiten DEĞİL)
   `forgelm approve <run_id> --output-dir <output_dir>` çalıştırır
   (not: `run_id` positional'dır — `--run-id` flag'i yoktur).
4. `human_approval.granted` audit event yangın eder; model
   atomik rename ile `<output_dir>/final_model/`'e promote olur.
5. Reddedilirse `human_approval.rejected` event yangın eder; model
   forensic inceleme için `final_model.staging.<run_id>/`'de kalır,
   retention süresi dolana kadar (`retention.staging_ttl_days` —
   config'te).

Bu, operatöre SOC 2 CC8.1 kanıtı verir: her model promotion
attribute, çift kontrollü ve forensic olarak kayıtlıdır.

### 4.2 Yapılandırma drift tespiti

`tools/check_field_descriptions.py --strict` (Phase 16) her CI build'inde
(`ci.yml`) çalışır ve `forgelm/config.py`'deki herhangi bir Pydantic
alanı `description=` argümanından yoksunsa non-zero çıkar. Operatöre dönük
`docs/reference/configuration.md` (ve `-tr.md` mirror'ı) elle bakımı
yapılır ve schema'ya karşı incelenir; `tools/check_bilingual_parity.py --strict`
EN↔TR yapısal pariteyi zorlar. Otomatik üretici yoktur — guard her schema
alanının yetkili açıklama metni taşıdığını garanti eder ve değişiklik
incelemesi referans doc'unun bunu yansıttığını kontrol eder.

Bu, schema'nın evrildiği ama operatöre dönük doc'un geri kaldığı
"doc drift" arıza modunu kapatır. ISO A.5.36 bunu zorunlu mekanizma
olarak atfeder.

### 4.3 SBOM drift tespiti

`tools/generate_sbom.py` (Wave 2 dönemi) her release tag'inde
(`publish.yml`) (OS × Python-version) başına deterministic CycloneDX
1.5 SBOM üretir. Wave 4 bir determinism contract testi ekler
(`tests/test_supply_chain_security.py::TestGenerateSbomDeterministic`).

Drift tespiti: bir release'in SBOM'u `git tag`'inden tekrarlanabilir.
Bir denetçi bunu istek üzerine yeniden emit edip GitHub release'e
ekli artefakta diff alabilir.

## 5. Versiyon İzleme

Her eğitim koşumu üretir:
- ForgeLM versiyonu, config hash, timestamps ile `compliance_report.json`
- Artefakt SHA-256 checksumları ile `model_integrity.json`
- Tam olay geçmişi ile `audit_log.jsonl`

Herhangi bir modeli tam eğitim yapılandırması ve verisine geri izlemek için bunları kullan.

## 6. İnceleme

| Versiyon | Tarih | Yazar | Değişiklikler |
|---------|------|--------|---------|
| 1.0 | [DATE] | [AUTHOR] | İlk versiyon |
| 1.1 | 2026-05-05 | Wave 4 / Faz 23 | §4 CI-gates-as-change-control tablosu eklendi (11 gate × ISO kontrolleri); §4.1 Madde 14 approval gate CAB substitute olarak; §4.2 `check_field_descriptions.py` + elle doc incelemesi üzerinden config-drift tespiti; §4.3 `generate_sbom.py` + determinism testi üzerinden SBOM drift tespiti |
| 1.2 | 2026-06-14 | Wave 1 / H7 | §4.2 düzeltildi: config-drift kontrolü `check_field_descriptions.py --strict` + elle doc incelemesi + bilingual-parity guard'dır; önceden atıfta bulunulan `regenerate_config_doc.py` otomatik üreticisi hiç var olmadı |
