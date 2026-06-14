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

Bu altı tam sayı tüm kamuya açık kontratı oluşturur — kanonik tanım için bkz. [`forgelm/cli/_exit_codes.py`](https://github.com/HodeTech/ForgeLM/blob/main/forgelm/cli/_exit_codes.py). Diğer her sıfır olmayan değer (sinyal kaynaklı 128+N kodları dahil) süreç çıkmadan önce `EXIT_TRAINING_ERROR` (2) değerine sıkıştırılır.

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
| `forgelm verify-audit` zincir kopması veya HMAC uyuşmazlığı | 1 (v0.5.5 döngüsünde EXIT_CONFIG_ERROR hem opsiyon hatalarını hem bütünlük arızalarını kapsar; v0.6.x deprecation notu için bkz. manuel içindeki [Audit Log Doğrulama](#/compliance/verify-audit) sayfası) |
| DPO koşusu, Llama Guard S5 toleransı aştı | 3 |
| Benchmark hellaswag floor altına düştü | 3 |
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

Exit kodları 0-5 sürümler arası kararlıdır. Yeni kodlar eklenebilir (6, 7, …) ama mevcutların semantiği değişmez. Yukarıdaki kontrata pinli CI hatları ForgeLM yükseltmelerinde çalışmaya devam eder.

## Bkz.

- [CI/CD Hatları](#/operations/cicd) — bu kontratı kullanan pattern'ler.
- [CLI Referansı](#/reference/cli) — bu kodları üreten tüm komutlar.
- [Otomatik Geri Alma](#/evaluation/auto-revert) — exit 3 üretir.
- [İnsan Gözetimi](#/compliance/human-oversight) — exit 4 üretir.
