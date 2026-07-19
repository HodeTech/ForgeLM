---
title: Exit Kodları
description: ForgeLM'in exit-kod kontratı — CI/CD hatlarının kamuya açık API'si.
---

# Exit Kodları

ForgeLM'in exit kodları kamuya açık bir kontrattır. CI/CD hatları, scheduler'lar ve dashboard'lar bunlara dayanır. Sürümler arası sessizce değişmez.

## Kontrat

| Exit | Sabit | Anlam | Tipik CI aksiyonu |
|---|---|---|---|
| **0** | `EXIT_SUCCESS` | Koşu tamamlandı ve checkpoint terfi etti. `evaluation.auto_revert: true` ile tüm kapılar da geçti; sevk edilen varsayılan `auto_revert: false` ile başarısız bir benchmark/güvenlik/judge kapısı **JSON çıktısına kaydedilir ama exit kodunu değiştirmez** — aşağıdaki ["exit 0 tam olarak ne garanti eder"](#exit-0-tam-olarak-ne-garanti-eder) bölümüne bakın. | Hattı sürdür (`auto_revert` kapalıysa kapı bloklarını parse et) |
| **1** | `EXIT_CONFIG_ERROR` | YAML geçersiz, dosya yok, env var ayarsız veya argüman bozuk. | Hızlı başarısız |
| **2** | `EXIT_TRAINING_ERROR` | Eğitim sırasında runtime hatası (config veya değerlendirme kapısı dışı her ele alınmamış istisna: data yükleme, OOM, NaN loss, I/O başarısızlığı, mid-stream audit-iteration OSError). | İncele; logları yüzeyle |
| **3** | `EXIT_EVAL_FAILURE` | Bir benchmark/güvenlik/judge kapısı geçemedi **ve** model otomatik geri alındı (`evaluation.auto_revert: true` gerektirir). `auto_revert: false` ile başarısız bir kapı exit 3 üretmez — koşu, JSON kapı bloklarına kaydedilen başarısızlıkla 0 çıkar. | İncele; terfi ETTİRME |
| **4** | `EXIT_AWAITING_APPROVAL` | `evaluation.require_human_approval: true` engelliyor. | Hattı tut; reviewer'ı tetikle |
| **5** | `EXIT_WIZARD_CANCELLED` | `forgelm --wizard` YAML üretmeden çıktı — Ctrl-C, non-tty stdin reddi veya operatör kaydetmeyi reddetti. `EXIT_SUCCESS`'tan ayrı ki CI "wizard tamamlandı" ile "wizard hiçbir şey yazmadı" arasını ayırt edebilsin. | No-op olarak kabul et; mesajı yüzeyle; eski config ile DEVAM ETME |
| **6** | `EXIT_INTEGRITY_FAILURE` | `verify-audit` / `verify-annex-iv` / `verify-gguf` / `verify-integrity` hedef artefaktı başarıyla okudu ve **bütünlük kontrolü başarısız oldu** — kırık bir audit-log hash zinciri, bir Annex IV manifest hash uyuşmazlığı, bir GGUF metadata/SHA-256 sidecar uyuşmazlığı veya `model_integrity.json` ile artık eşleşmeyen model dosyaları. Yalnızca dört `verify-*` subcommand'ına özgü; başka hiçbir komut bunu üretmez. | Config düzeltmesi değil, güvenlik olayı olarak ele al — artefaktın sahibini uyar, tekrar deneme |

Bu yedi tam sayı tüm kamuya açık kontratı oluşturur — kanonik tanım için bkz. [`forgelm/cli/_exit_codes.py`](https://github.com/HodeTech/ForgeLM/blob/main/forgelm/cli/_exit_codes.py). Diğer her sıfır olmayan değer (sinyal kaynaklı 128+N kodları dahil) süreç çıkmadan önce `EXIT_TRAINING_ERROR` (2) değerine sıkıştırılır.

**`verify-*` exit kodlarını okumak: 1'e karşı 6.** Dört `verify-*` subcommand'ı özelinde, `EXIT_CONFIG_ERROR` (1) ile `EXIT_INTEGRITY_FAILURE` (6) tek bir soruda ayrışır: doğrulayıcı bir şeyi karşılaştıracak kadar ileri gitti mi? Eksik bir dosya, bozuk bir manifest veya bir magic-header uyuşmazlığı (dosya hiç GGUF değil) doğrulayıcının hiçbir şey karşılaştıramadığı anlamına gelir — exit 1. Yeniden hesaplanan bir hash, zincir bağlantısı veya kayıtlıyla uyuşmayan bir manifest girdisi, doğrulayıcının karşılaştırdığı ve artefaktın başarısız olduğu anlamına gelir — exit 6. İki durum, tahrifat gibi görünse de bilerek 1 tarafında bırakılmıştır: bir GGUF magic-header uyuşmazlığı (tahrifat değil dosya-tipi kararı) ve model dizininin dışına çıkan bir yola sahip `verify-integrity` manifest girdisi (doğrulayıcı hiçbir şey okumadan önce ağaç-dışı bir yolu hash'lemeyi reddeder, yani hiçbir şey karşılaştırılmamıştır). Kod başına tam döküm için her doğrulayıcının kendi exit-kod tablosuna bakın ([CLI Referansı](#/reference/cli)'ndan bağlantılı).

## CI pattern'lerine eşleme

### GitHub Actions

```yaml
- name: Train
  id: train
  run: forgelm --config configs/run.yaml
  continue-on-error: true

- name: Block on regression
  if: steps.train.outcome == 'failure'
  run: |
    if [ "${{ steps.train.outputs.exit-code }}" = "3" ]; then
      echo "::error::Regresyon tespit edildi — audit log'u inceleyin"
      exit 1
    fi
```

Çoğu hat için basit pattern yeterli:

```yaml
- name: Train
  run: forgelm --config configs/run.yaml
  # Sıfır olmayan exit step'i fail eder. Artifact upload step'i hâlâ çalışır (if: always()).
```

### GitLab CI

```yaml
train:
  script:
    - forgelm --config configs/run.yaml
  allow_failure:
    exit_codes: [4]                    # exit 4 (onay bekleme) CI'yı fail etmez
```

### Jenkins

```groovy
stage('Train') {
  steps {
    script {
      def status = sh(script: 'forgelm --config configs/run.yaml', returnStatus: true)
      if (status == 4) {
        currentBuild.result = 'UNSTABLE'   // onay için beklet
      } else if (status != 0) {
        error "Eğitim ${status} çıkış kodu ile başarısız oldu"
      }
    }
  }
}
```

## Hangi durum hangi exit

| Durum | ForgeLM exit |
|---|---|
| YAML'da typo (ör. `learnng_rate`) | 1 |
| YAML'da `${HF_TOKEN}` ama env var yok | 1 |
| `--config` var olmayan dosyaya işaret ediyor | 1 |
| Eğitim ortasında final loss NaN / OOM / I/O hatası | 2 |
| `forgelm verify-audit` zincir kopması veya HMAC uyuşmazlığı | 6 (log okundu ve zincir doğrulanmadı — `--require-hmac` secret olmadan gibi bir opsiyon hatası veya eksik log dosyası 1'de kalır; bkz. manuel içindeki [Audit Log Doğrulama](#/compliance/verify-audit) sayfası) |
| `forgelm verify-audit`, var olan ama **sıfır girdi** tutan bir log üzerinde | Bir genesis manifest ilk girdiyi sabitliyorsa 6 (boşa truncate edilmiş — bir karşılaştırma yapıldı ve başarısız oldu); manifest yoksa 1 (referans yok, hiçbir şey karşılaştırılamadı). Asla 0 değil — boş bir log hiçbir zaman geçerli bir taze-çalıştırma durumu değildir |
| `forgelm verify-gguf` / `verify-annex-iv` / `verify-integrity` — artefakt okundu, hash/manifest uyuşmuyor | 6 |
| `forgelm verify-*` — yol eksik, okunamıyor veya girdi bozuk | 1 |
| DPO koşusu, Llama Guard S5 toleransı aştı | `evaluation.auto_revert: true` ile 3; shipped default `false` ile 0 (JSON gate bloklarında kaydedilir) |
| Benchmark hellaswag floor altına düştü | `evaluation.auto_revert: true` ile 3; shipped default `false` ile 0 (JSON gate bloklarında kaydedilir) |
| `evaluation.require_human_approval: true` ve onay imzalanmamış | 4 |
| Kullanıcı Ctrl+C (sinyal kaynaklı 128+N) | 2 (sıkıştırılır) |

## Programatik tespit

Exit kodu kontrat tek başına yeterli — POSIX kabuklarda `$?`, cmd'de `%ERRORLEVEL%`, PowerShell'de `$LASTEXITCODE` ile veya CI runner'ınızın ifade dilindeki karşılığıyla okuyun (ör. GitHub Actions'ta `steps.<id>.outputs.exit-code`, Jenkins'te `returnStatus: true`). Daha zengin postmortem bağlam için (regrese kategoriler, restore edilmiş checkpoint yolu vb.) bir sidecar yerine koşunun output dizini altına yazılan yapısal `audit_log.jsonl` olayını parse edin.

## "exit 0" tam olarak ne garanti eder

0 ile çıkan koşu:
- Config'i hatasız doğrulamış.
- Modeli ve dataset'i yüklemiş.
- Tüm konfigüre eğitim adımlarını tamamlamış.
- Model card yazmış.
- Annex IV paketi yazmış (konfigüre ise).
- Manifest.json'u tüm artifact'lar üzerinde SHA-256 ile yazmış.
- Opsiyonel: GGUF, deployment config yazmış.
- Audit log'u `pipeline.completed` ile kapatmış (kanonik event adı).

**Kapılar ve exit 0.** *Geçen* bir benchmark/güvenlik/judge kapısının exit-0 garantisinin parçası olup olmadığı `evaluation.auto_revert`'e bağlıdır:

- `evaluation.auto_revert: true` ile (EU AI Act yüksek-riskli varsayılanı), başarısız bir kapı modeli otomatik geri alır ve **3** ile çıkar — yani exit 0 *gerçekten* tüm konfigüre kapıların geçtiği anlamına gelir.
- Sevk edilen varsayılan `evaluation.auto_revert: false` ile, başarısız bir kapı **kaydedilir** (JSON çıktısındaki `benchmark` / `safety` / `judge` bloğu `*_passed: false` taşır) ama model yine terfi eder ve koşu **0** ile çıkar. Bu JSON bloklarını okuyun; kapı başarısını yalnızca exit 0'dan çıkarsamayın.

Tasarım gereği "kısmi başarı" exit kodu yok — başarısız bir kapının exit kodunu değiştirmesini istiyorsanız `auto_revert`'i açın.

## Uyumluluk garantisi

Exit kodları 0-6 sürümler arası kararlıdır. Yeni kodlar eklenebilir (7, 8, …) ama mevcutların semantiği değişmez. Yukarıdaki kontrata pinli CI hatları ForgeLM yükseltmelerinde çalışmaya devam eder.

`EXIT_INTEGRITY_FAILURE` (6) semantik değişikliği değil, katkısaldır: yalnızca dört `verify-*` subcommand'ında önceden 1 ile çıkan durumların bir alt kümesini daraltır. Bir `verify-*` bütünlük arızasını yakalamak için `exit code == 1` doğrulayan bir hat, `== 6`'yı da kontrol edecek şekilde güncellenmelidir; yalnızca `!= 0`'a dallanan veya `verify-*`'ı `set -e` altında çalıştıran bir hat etkilenmez — 1 ve 6 ikisi de sıfır olmayan kalır ve step'i başarısız kılmaya devam eder.

## Bkz.

- [CI/CD Hatları](#/operations/cicd) — bu kontratı kullanan pattern'ler.
- [CLI Referansı](#/reference/cli) — bu kodları üreten tüm komutlar.
- [Otomatik Geri Alma](#/evaluation/auto-revert) — exit 3 üretir.
- [İnsan Gözetimi](#/compliance/human-oversight) — exit 4 üretir.
