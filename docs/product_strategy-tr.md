# ForgeLM Ürün Stratejisi

> Son Güncelleme: 2026-04-25
> Amaç: ForgeLM'in pazar konumunu, farklılaşmasını, hedef kullanıcılarını ve stratejik yönünü tanımlamak.

---

## Misyon

ForgeLM, LLM ince ayarını (fine-tuning) **güvenli, denetlenebilir ve tekrarlanabilir** kılar — config-tabanlı, CI/CD-uyumlu, her ortamda çalışır.

---

## Üç Temel Sütun

### 1. Güvenlik Önce (Safety-First)

İnce ayar, bir modelin güvenlik özelliklerini — sessizce — bozabilir. ForgeLM, güvenlik değerlendirmesini sonradan eklenen isteğe bağlı bir özellik olarak değil, pipeline'ın birinci sınıf bir aşaması olarak ele alır.

- **Llama Guard 3** güvenlik değerlendirmesi eğitim pipeline'ının içinde çalışır; eğitim sonrası değil
- **3 katmanlı güvenlik kapısı**: ikili geçer/kalır + güven ağırlıklı skor + 14 zarar kategorisinde (S1-S14) önem analizi
- **Otomatik geri alma (auto-revert)**: güvenlik regresyonu tespit edilirse, çıktılar yazılmadan önce eğitim sonucu otomatik olarak reddedilir
- **Çalıştırmalar arası trend takibi**: güvenlik skorları her çalıştırmada loglanır; zamanla oluşan kayan bozulma yakalanır
- **`auto_revert_on_safety_failure`** config alanı doğrudan YAML'a yazılır — CI'da güvenlik kapısı uygulamak için özel script gerekmez

### 2. Uyumluluk Yerleşik (Compliance-Native)

2 Ağustos 2026 itibarıyla AB Yapay Zeka Yasası (EU AI Act) yüksek riskli YZ sistemlerine tam olarak uygulanıyor. ForgeLM, normal bir eğitim çalışmasının yan ürünü olarak compliance kanıt paketi otomatik üreten tek açık kaynak ince ayar aracıdır.

- **EU AI Act Madde 9-17 + Ek IV** — her madde yapılandırılmış bir çıktı dosyasına karşılık gelir:
  - Mad. 9 Risk Değerlendirmesi → `risk_assessment.json`
  - Mad. 10 Veri Yönetişimi → `data_governance_report.json`
  - Mad. 11 Teknik Dokümantasyon → `annex_iv_metadata.json`
  - Mad. 12 Kayıt Tutma → `audit_log.jsonl`
  - Mad. 13 Şeffaflık → `deployer_instructions.md`
  - Mad. 14 İnsan Gözetimi → `require_human_approval: true` + çıkış kodu 4
  - Mad. 15 Doğruluk ve Sağlamlık → `model_integrity.json` + güvenlik değerlendirme sonuçları
- **Müdahaleye dayanıklı denetim günlüğü**: SHA-256 zinciri — her kayıt bir öncekinin hash'ini içerir; silme veya değiştirme tespit edilebilir
- **İnsan onayı kapısı**: Çıkış kodu 4, CI/CD orchestrator'larına "insan onayı bekleniyor" sinyali verir; nihai model `final_model.staging/` dizinine iner — operatörler `forgelm approve <run_id>` ile promote eder (atomik rename → `final_model/`) veya `forgelm reject <run_id>` ile reddeder; her iki karar da denetim zincirine işlenir

### 3. Config-Tabanlı CI/CD

ForgeLM'in temel mimari kimliği: bir YAML dosyası bir eğitim çalışmasını eksiksiz tanımlar. İmperatif kod gerekmez, ortam değişkeni aranmaz, gizli çalışma durumu yok.

- **Tek YAML girer, ince ayarlı model + compliance kanıtları çıkar** — deterministik ve sürüm kontrollü
- **Anlamlı çıkış kodları** pipeline orkestrasyonu için: 0 (başarı), 1 (config/doğrulama hatası), 2 (eğitim hatası), 3 (değerlendirme/güvenlik hatası), 4 (insan onayı bekleniyor)
- **`--dry-run`**: GPU tahsis etmeden config, veri ve model yüklemeyi tam olarak doğrular
- **Yapılandırılmış JSON çıktı**: tüm değerlendirme ve compliance adımlarında aşağı akış sistemlerince parse edilebilir
- **Docker-native**: GPU ve CPU için resmi çok aşamalı imajlar; çevrimdışı/izole (air-gapped) mod desteklenir
- **Git uyumlu**: YAML config'ler temiz diff verir; compliance dosyaları düz JSON/Markdown

---

## Hedef Kullanıcılar

### Birincil: MLOps / Platform Mühendisleri

**Profil:** Otomatik eğitim pipeline'ları kurar ve yönetir; GitOps/CI ortamlarında çalışır; başsız (headless), deterministik yürütmeye ihtiyaç duyar.

**ForgeLM'i neden seçerler:** Anlamlı çıkış kodları doğrudan pipeline dallanma mantığına karşılık gelir. `--dry-run`, GPU kuyruğuna alınmadan config'i doğrular. Yapılandırılmış JSON çıktı, gözlemlenebilirlik platformlarıyla entegre olur. Webhook bildirimleri (Slack/Teams) mevcut olay yönetimi akışlarına bağlanır.

**Temsili iş akışı:** `git push → GitHub Actions tetiklenir → forgelm --config job.yaml → çıkış kodu → dağıt veya uyarı ver`

### İkincil: Düzenlemeye Tabi Sektörlerdeki ML Mühendisleri

**Profil:** Bankacılık, sağlık, savunma, kamu. Tescilli veriyi harici API'lere gönderemez. EU AI Act (AB), KVKK/BDDK (Türkiye), HIPAA (ABD) veya eşdeğer düzenlemelere tabidir. Tam denetim izleriyle şirket içi veya izole ortamda çalışmayı gerektirir.

**ForgeLM'i neden seçerler:** Tam EU AI Act compliance kanıt paketini otomatik üreten tek ince ayar aracı. Şirket içi Docker dağıtımı. `trust_remote_code: false` varsayılan. İnsan onayı kapısı (çıkış kodu 4) üretim dağıtımını erteler; nihai model `final_model.staging/` dizinine iner; operatörler promote için `forgelm approve <run_id>`, reddetmek içinse `forgelm reject <run_id>` çalıştırır. SHA-256 zinciriyle denetim günlüğü kayıt tutma gereksinimlerini karşılar.

### Üçüncül: Bağımsız Araştırmacılar ve Geliştiriciler

**Profil:** Derin altyapı bilgisi olmadan model ince ayarı yapmak ister; SFT/DPO/GRPO ile deney yapıyor; sağlam varsayılanlara ihtiyaç duyar.

**ForgeLM'i neden seçerler:** Sihirbaz modu (`forgelm wizard`) yapılandırmayı etkileşimli olarak yönlendirir. Quickstart şablonları (Faz 10.5) en yaygın ince ayar senaryoları için hazır config'ler sunar. Config-tabanlı yaklaşım, çalışmaları tekrarlanabilir ve paylaşılabilir yapar. Tek araçta 6 trainer yöntemi (SFT/DPO/SimPO/KTO/ORPO/GRPO).

---

## ForgeLM Ne DEĞİLDİR

Açıkça kapsam dışı — şimdi ve uzun vadede:

- **GUI veya web uygulaması değildir.** Arayüz config dosyalarıdır. Web arayüzü isteyen kullanıcılar LLaMA-Factory veya AutoTrain kullanmalıdır. Pro CLI ücretli kullanıcılar için bir dashboard ekleyecek; açık kaynak çekirdek başsız kalır.
- **Bulut altyapı sağlayıcısı değildir.** ForgeLM model eğitir — compute sağlamaz, yönetmez, faturalandırmaz. Kullanıcılar kendi GPU'larını getirir (şirket içi, RunPod, Lambda Labs, AWS vb.).
- **Model sunum veya inference platformu değildir.** ForgeLM eğitilmiş adaptörler ve compliance kanıtları üretir. Dağıtım kullanıcının sorumluluğundadır; vLLM, Ollama, TGI veya llama.cpp'ye devredilir.
- **Genel ML çerçevesi değildir.** Yalnızca LLM ince ayarı. Bilgisayarlı görü, klasik ML veya sıfırdan ön-eğitim (pretraining) değil.
- **Özel inference motoru değildir.** Özel CUDA kernel'i veya özel quantization uygulaması yoktur. ForgeLM bu işleri bitsandbytes/AWQ/GPTQ/HQQ ve mevcut runtime'lara devreder.

---

## Rekabet Konumu

| Boyut | ForgeLM | LLaMA-Factory | Unsloth | Axolotl |
|---|---|---|---|---|
| Güvenlik değerlendirmesi (entegre) | ✅ Llama Guard S1-S14 | ❌ | ❌ | ❌ |
| EU AI Act uyumluluğu | ✅ Mad. 9-17 + Ek IV | ❌ | ❌ | ❌ |
| CI/CD-native çıkış kodları | ✅ 0/1/2/3/4/5/6 | Kısmi | ❌ | Kısmi |
| Web UI | ❌ (kasıtlı) | ✅ | ✅ Studio | ❌ |
| Hız optimizasyonu | Standart | Standart | ✅ 2-5x | Standart |
| Config-tabanlı YAML | ✅ Tam | Kısmi | ❌ | ✅ |
| Çok GPU | ✅ DeepSpeed/FSDP | ✅ | ❌ | ✅ |
| Trainer yöntemleri | ✅ 6 (SFT/DPO/SimPO/KTO/ORPO/GRPO) | ✅ Çok | Kısmi | Kısmi |
| Regresyonda otomatik geri alma | ✅ | ❌ | ❌ | ❌ |
| SHA-256 denetim zinciri | ✅ | ❌ | ❌ | ❌ |

**Net konum:** ForgeLM ham hızda (Unsloth) ve model çeşitliliği/GUI'da (LLaMA-Factory) geridedir. Bunlar bilinçli takaslardır. Güvenlik + uyumluluk ekseninde ForgeLM kategoriksel olarak öndedir — kademeli değil, bunu sistematik biçimde yapan tek araç olma anlamında.

---

## Stratejik Kararlar

### 1. Birincil Farklılaştırıcı: Güvenlik ve Uyumluluk

**Karar tarihi:** 2026-03-23

Güvenlik değerlendirmesi ve compliance kanıt üretimi isteğe bağlı özellikler değildir — hedef pazar için temel değer önerisidir. ForgeLM hız veya model çeşitliliğiyle rekabet etmez; güvenilirlik ve denetlenebilirlikle rekabet eder.

**Çıkarım:** Her yeni büyük özellik, bu konumu güçlendirip güçlendirmediği perspektifinden değerlendirilir. Güvenlik/uyumluluk derinliğini artırmayan hız özellikleri daha düşük önceliğe sahiptir.

### 2. Her Zaman Config-Tabanlı — Çekirdekte Web UI Yok

**Karar türü:** Kalıcı mimari seçim

Config-tabanlı kimlik ForgeLM'i CI/CD-uyumlu, Git-dostu ve tekrarlanabilir kılar. Web UI, başsız pipeline-önce modeliyle bağdaşmayan durum yönetimi, kimlik doğrulama ve çok kullanıcılı oturum mantığı gerektirir. Pro CLI ücretli kullanıcılar için dashboard ekleyecek; OSS çekirdek başsız kalır.

### 3. İsteğe Bağlı Bağımlılıklar Extras Olarak

Ağır bağımlılıklar `pyproject.toml`'daki `[project.optional-dependencies]` altında tanımlanır: `qlora`, `unsloth`, `eval`, `tracking`, `distributed`, `merging`. Eksik olduklarında sessizce `None` döndürmek yerine açık yükleme ipucu içeren `ImportError` fırlatılır.

**Gerekçe:** Temel kurulum saniyeler içinde tamamlanabilir olmalıdır. Bağımlılık çakışmaları, ML araçlarında katılımı engelleyen en yaygın sorundur. İsteğe bağlı extras, kullanıcıların yalnızca ihtiyaç duydukları şeyi kurmalarına olanak tanır.

### 4. Özelliklerden Önce Güvenilirlik

Faz 2.5 (güvenilirlik sağlamlaştırma) yeni özellik geliştirmesinden önce eklendi — ve bu model tekrarlanır. Her yeni büyük yetenek, merge edilmeden önce testler, dokümantasyon ve CI kapsamıyla birlikte gelir. "Testleri sonra eklerim" = PR hazır değil.

**Çıkarım:** ForgeLM çeyrek başına rakiplere göre daha az özellik sunar; ancak her özellik üretime hazır güvenilirlikle gelir. Bu tradeoff, düzenlemeye tabi sektör hedef pazarı için doğrudur.

### 5. EU AI Act Uyumluluğu Zaman Baskılı Hendek

EU AI Act tam uygulaması 2 Ağustos 2026'da başlıyor. Başka hiçbir açık kaynak ince ayar aracı compliance kanıt paketi üretmiyor. Şimdiden rakipler compliance özellikleri ekleyene kadar geçen bu pencere — birincil enterprise satış fırsatıdır.

**Çıkarım:** Enterprise outreach hemen başlamalıdır, Phase 10+ tamamlanana kadar beklenemez. Compliance hendeği gerçek ama geçicidir; rakipler yürürlükten 6-12 ay içinde yanıt verecektir.

---

## Başarı Metrikleri

### Kısa Vade (Ekim 2026'ya kadar — 6 ay)

- EU AI Act yürürlüğü (2 Ağustos) ilk nitelikli enterprise başvurularını getiriyor
- v0.4.0 (Post-Training Completion — inference handoff, chat, export) yayınlandı
- v0.4.5 (Quickstart Katmanı — şablonlar, sihirbaz iyileştirmeleri) yayınlandı
- 1.000+ GitHub yıldızı
- İlk enterprise pilot sözleşmesi imzalandı (bankacılık, sağlık veya kamu sektörü)
- YouTube Academy 5+ videoyla başlatıldı

### Orta Vade (Nisan 2027'ye kadar — 12 ay)

- 5.000+ GitHub yıldızı
- 3+ enterprise destek sözleşmesi (Silver/Gold/Platinum katmanı)
- $50.000–$150.000 ARR
- ForgeLM Cloud MVP özel beta'da
- Ücretli kullanıcılara Pro CLI alpha sunuldu
- 3-5 aktif harici contributor

### Uzun Vade (Nisan 2028'e kadar — 24 ay)

- **Güvenli, uyumlu LLM ince ayarı** için standart açık kaynak araç olarak tanınma
- 15.000+ GitHub yıldızı
- $500.000+ ARR
- Çok bölgeli ForgeLM Cloud genel kullanıma açıldı
- 50+ topluluk quickstart şablonu
- Enterprise compliance platformlarından (OneTrust, ServiceNow AI Control Tower, SAS) potansiyel satın alma ilgisi

---

## Dokümantasyon Haritası

| Belge | Amaç |
|---|---|
| [README.md](../README.md) | Proje genel bakışı ve hızlı başlangıç |
| [CONTRIBUTING.md](../CONTRIBUTING.md) | Katkı rehberi |
| [Yol Haritası](roadmap-tr.md) | Faz bazlı yürütme planı |
| [Ürün Stratejisi](product_strategy-tr.md) | Pazar konumu ve stratejik yön (bu belge) |
| [Mimari](reference/architecture-tr.md) | Sistem tasarımı ve bileşen detayları |
| [Yapılandırma Rehberi](reference/configuration-tr.md) | YAML parametre referansı |
| [Kullanım Rehberi](reference/usage-tr.md) | Eğitim nasıl çalıştırılır |
| [Veri Hazırlama](reference/data_preparation-tr.md) | Veri seti format gereksinimleri |
| [Standartlar Dizini](standards/README.md) | Mühendislik standartları ve kurallar |
| [Sihirbaz Modu Tasarımı](design/wizard_mode.md) | Etkileşimli yapılandırma sihirbazı tasarımı |
| [Blackwell Tasarımı](design/blackwell_optimized.md) | GB10/Blackwell optimizasyon tasarımı |

Tüm belgeler İngilizce (`*.md`) ve Türkçe (`*-tr.md`) olarak mevcuttur.
