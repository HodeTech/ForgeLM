---
title: CI/CD Hatları
description: ForgeLM'i öngörülebilir exit kodlarıyla GitHub Actions, GitLab CI veya Jenkins'e bağlayın.
---

# CI/CD Hatları

ForgeLM, CI/CD hattının bir adımı olarak temiz şekilde yerleşmek üzere tasarlandı. Her komutun öngörülebilir bir exit kodu, her çıktının yapılandırılmış bir formatı (JSON veya JSONL) ve her kapının net bir verdict'i var — arada gri bölge yok. Aynı exit-kod kontratı; terminalden, notebook'tan veya bir orchestrator'dan çalıştırsanız da geçerli.

## Exit-kod kontratı

| Exit | Anlamı | CI ne yapmalı |
|---|---|---|
| `0` | Başarı | Artifact'ları terfi ettir |
| `1` | Konfigürasyon hatası | Hızlıca başarısız ol; YAML'ı düzelt, sonra dene |
| `2` | Eğitim hatası | Hızlıca başarısız ol; kilitlenmeyi / OOM / işlenmeyen istisnayı araştır |
| `3` | Değerlendirme kapısı başarısız — `auto_revert: true` ile artefaktlar silindi | Başarısız olarak işaretle; gerilemeyi araştır |
| `4` | İnsan onayı bekliyor | Hattı askıya al; reviewer'ı tetikle |
| `5` | Sihirbaz iptal edildi — config üretilmedi | Hata değil; terfi ettirilecek bir şey yok |
| `6` | Bütünlük hatası — bir artefakt okundu ve hash karşılaştırması uyuşmadı | Yüksek sesle başarısız ol; artefakt sahibini çağır. Körlemesine terfi ettirme veya yeniden deneme |

Genel aralık `0`–`6`'dır. Pipeline'ınızın `case` bloğu eski 0–4 tablosuna göre yazıldıysa `5` ve `6` için dal ekleyin — özellikle `6` güvenlikle ilgili olandır ("bu artefakt imzalandıktan sonra değiştirildi") ve asla genel bir default'a düşmemelidir. Tam kontrat için [Exit Kodları](#/reference/exit-codes).

## GitHub Actions

Eksiksiz bir hat:

```yaml
# .github/workflows/train.yml
name: Eğit ve değerlendir

on:
  push:
    branches: [main]
    paths:
      - "configs/**"
      - "data/**.jsonl"
  workflow_dispatch:

jobs:
  audit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - uses: actions/setup-python@v6
        with: { python-version: "3.11" }
      - run: pip install 'forgelm[ingestion]'
      - name: Veri denetimi
        run: forgelm audit data/train.jsonl --output-format json | jq -e '.verdict != "errors" and .pii_summary.severity != "high"'

  train:
    needs: audit
    runs-on: gpu-runner                    # CUDA'lı self-hosted
    steps:
      - uses: actions/checkout@v5
      - run: pip install forgelm
      - name: Config doğrula
        run: forgelm --config configs/run.yaml --dry-run
      - name: VRAM fit-check
        run: forgelm --config configs/run.yaml --fit-check
      - name: Eğit
        run: forgelm --config configs/run.yaml
      - name: Artifact'ları yükle
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: training-artifacts
          path: checkpoints/run/
```

Artifact upload'taki `if: always()` önemli — başarısızlıkta bile audit log ve kısmi artifact'lar değerli.

## GitLab CI

```yaml
# .gitlab-ci.yml
stages:
  - audit
  - train
  - deploy

audit:
  stage: audit
  image: python:3.11
  script:
    - pip install 'forgelm[ingestion]'
    - forgelm audit data/train.jsonl --output-format json > audit.json
    - jq -e '.verdict != "errors" and .pii_summary.severity != "high"' audit.json

train:
  stage: train
  tags: [gpu]
  script:
    - pip install forgelm
    - forgelm --config configs/run.yaml --dry-run
    - forgelm --config configs/run.yaml --fit-check
    - forgelm --config configs/run.yaml
  artifacts:
    paths:
      - checkpoints/run/
    when: always

deploy:
  stage: deploy
  tags: [gpu]
  needs: [train]
  script:
    # Verify the audit chain before promoting (catches truncate / HMAC tamper).
    - forgelm verify-audit checkpoints/run/audit_log.jsonl --require-hmac
    # If `evaluation.require_human_approval` is on, the trainer exits 4 — promote here:
    #   forgelm approve <run_id> --output-dir checkpoints/run --comment "approved-by-CI"
    # Otherwise, ship `final_model/` with your existing deployment tooling.
    - ./scripts/promote-to-prod.sh checkpoints/run/final_model
  when: manual                          # gate manual promotion to prod
```

## Jenkins

```groovy
pipeline {
  agent any
  stages {
    stage('Audit') {
      steps {
        sh '''forgelm audit data/train.jsonl --output-format json | jq -e '.verdict != "errors" and .pii_summary.severity != "high"' '''
      }
    }
    stage('Train') {
      agent { label 'gpu' }
      steps {
        sh 'forgelm --config configs/run.yaml --dry-run'
        sh 'forgelm --config configs/run.yaml --fit-check'
        sh 'forgelm --config configs/run.yaml'
      }
      post {
        always {
          archiveArtifacts artifacts: 'checkpoints/run/**'
        }
      }
    }
  }
}
```

## JSON çıktı modu

Koşu çıktısının programatik parse'ı için `--output-format json` kullanın:

```shell
$ forgelm --config configs/run.yaml --output-format json | jq '.verdict'
"success"
```

ForgeLM'in stderr'a yazdığı her olay stdout'ta da JSON nesnesi olarak çıkar — log aggregator'ınıza veya dashboard'unuza pipe'lamaya hazır.

## Model cache

Model indirme taze CI koşusunun en yavaş kısmıdır. Cache'leyin:

```yaml
# GitHub Actions
- uses: actions/cache@v4
  with:
    path: ~/.cache/huggingface
    key: hf-${{ runner.os }}-${{ hashFiles('configs/**.yaml') }}
```

Çoklu-runner kurulumunda cache'i paylaşımlı depolamaya (S3, NFS) yönlendirin; tüm runner'lar paylaşır.

## Eşzamanlılık kontrolü

Çoğu proje eğitimi paralel değil seri koşturur — aynı GPU'da paralel iş OOM'a yol açar. Concurrency kullanın:

```yaml
# GitHub Actions
concurrency:
  group: training-${{ github.ref }}
  cancel-in-progress: false              # iptal etme; sıraya al
```

## Self-hosted GPU runner'ları

GitHub Actions için self-hosted GPU:

1. GitHub runner'ı GPU host'a kurun.
2. Etiketleyin: `self-hosted, linux, x64, gpu`.
3. Job'ta referans verin: `runs-on: [self-hosted, gpu]`.

ForgeLM, runner'ı CUDA, Python ve önceden kurulu extra'larla kurmak için bir `Dockerfile.runner` referansı yayınlar.

## Sık hatalar

:::warn
**Exit kodları bastırmak.** `forgelm ... || true` kapı kontratının tüm amacını yok eder. Gerçekten devam etmeniz gerekiyorsa exit koduna göre dallanın.
:::

:::warn
**Audit ve eğitimi aynı runner'da çalıştırmak.** Audit CPU-only; eğitim GPU ister. Önce ucuz runner'da audit çalıştırın, audit geçerse pahalı GPU runner'da eğitin. Veride bug olduğunda GPU zamanı boşa harcanmaz.
:::

:::warn
**Başarısızlıkta artifact yüklememek.** Bir koşu başarısız olduğunda audit log en değerli kanıttır. Her zaman artifact upload'lara `if: always()` (GitHub) / `when: always` (GitLab) koyun.
:::

:::tip
Gece eğitimleri için otomatik geri alma + Slack webhook konfigüre edin. Sabah ya terfi etmiş bir modele ya da net bir olay raporuna uyanırsınız — asla "bilmiyorum, sabah 3'te bozuldu" gizemine değil.
:::

## Bkz.

- [Exit Kodları](#/reference/exit-codes) — CI'nın dayandığı kontrat.
- [Webhook'lar](#/operations/webhooks) — Slack/Teams bildirimleri.
- [Otomatik Geri Alma](#/evaluation/auto-revert) — exit 3'ü tetikleyen.
