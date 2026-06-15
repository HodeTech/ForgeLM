# CI/CD Pipeline Entegrasyon Rehberi

ForgeLM otomasyon için tasarlandı. Bu rehber, fine-tuning'i GitHub
Actions, GitLab CI ve generic CI/CD pipeline'larına nasıl entegre
edeceğinizi gösterir.

---

## Çekirdek prensipler

ForgeLM'in CI/CD-yerlisi tasarımı şunları sağlar:

- **YAML-tahrikli**: Tüm eğitim koşumları version-controlled config
  dosyalarında tanımlanır
- **Anlamlı exit kodları**: `0` başarı, `1` config hatası, `2` eğitim
  hatası, `3` eval hatası, `4` insan onayı bekliyor
- **JSON çıktı**: Makine-okunabilir sonuçlar için `--output-format json`
- **Dry-run doğrulama**: GPU olmadan `--dry-run` doğrular
- **Webhook bildirimleri**: Başlangıç/başarı/hata için gerçek-zamanlı
  Slack/Teams alarmları

---

## GitHub Actions

### Temel eğitim iş akışı

```yaml
# .github/workflows/train.yml
name: Fine-Tune Model

on:
  push:
    paths:
      - 'configs/**'
      - 'data/**'
  workflow_dispatch:
    inputs:
      config:
        description: 'Config dosya yolu'
        required: true
        default: 'configs/production.yaml'

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install -e .
      - name: Config'i doğrula
        run: forgelm --config ${{ github.event.inputs.config || 'configs/production.yaml' }} --dry-run --output-format json

  train:
    needs: validate
    runs-on: [self-hosted, gpu]  # GPU runner
    steps:
      - uses: actions/checkout@v4
      - run: pip install -e ".[qlora,eval]"

      - name: Modeli eğit
        env:
          HUGGINGFACE_TOKEN: ${{ secrets.HF_TOKEN }}
          FORGELM_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK }}
        run: |
          forgelm --config ${{ github.event.inputs.config || 'configs/production.yaml' }} \
            --output-format json > training_result.json
          echo "EXIT_CODE=$?" >> $GITHUB_ENV

      - name: Model artefakt'larını yükle
        if: success()
        uses: actions/upload-artifact@v4
        with:
          name: fine-tuned-model
          path: checkpoints/final_model/

      - name: Eğitim sonuçlarını yükle
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: training-results
          path: |
            training_result.json
            checkpoints/compliance/
            checkpoints/benchmark/
```

### Çoklu-model matris eğitimi

```yaml
jobs:
  train:
    strategy:
      matrix:
        config:
          - configs/customer_support.yaml
          - configs/code_assistant.yaml
          - configs/legal_qa.yaml
    runs-on: [self-hosted, gpu]
    steps:
      - uses: actions/checkout@v4
      - run: pip install -e ".[qlora,eval]"
      - name: ${{ matrix.config }} eğit
        run: forgelm --config ${{ matrix.config }} --output-format json
```

---

## GitLab CI

```yaml
# .gitlab-ci.yml
stages:
  - validate
  - train
  - evaluate

validate:
  stage: validate
  image: python:3.11
  script:
    - pip install -e .
    - forgelm --config configs/production.yaml --dry-run --output-format json
  rules:
    - changes:
        - configs/**

train:
  stage: train
  tags:
    - gpu
  image: forgelm:latest  # ya da Dockerfile'dan build et
  variables:
    HUGGINGFACE_TOKEN: $HF_TOKEN
  script:
    - forgelm --config configs/production.yaml --output-format json > result.json
  artifacts:
    paths:
      - checkpoints/final_model/
      - checkpoints/compliance/
      - result.json
    expire_in: 30 days
  rules:
    - changes:
        - configs/**
        - data/**
```

---

## Docker tabanlı pipeline

Python kurulumu olmayan ortamlar için:

```bash
# Bir kez build et
docker build -t forgelm:latest --build-arg INSTALL_EVAL=true .

# Pipeline'da koştur
docker run --gpus all \
  -v $(pwd)/configs:/workspace/configs:ro \
  -v $(pwd)/data:/workspace/data:ro \
  -v $(pwd)/output:/workspace/output \
  -e HUGGINGFACE_TOKEN=$HF_TOKEN \
  forgelm:latest \
  --config /workspace/configs/job.yaml \
  --output-format json
```

---

## Webhook entegrasyonu

### Slack

```yaml
# ForgeLM config'inizde
webhook:
  url_env: "FORGELM_WEBHOOK_URL"
  notify_on_start: true
  notify_on_success: true
  notify_on_failure: true
```

ForgeLM yapılandırılmış payload'lar gönderir:

```json
{
  "event": "training.success",
  "run_name": "Llama-3.1-8B-Instruct_finetune",
  "status": "succeeded",
  "metrics": {
    "eval_loss": 1.25,
    "train_loss": 0.89,
    "benchmark/arc_easy": 0.72
  },
  "attachments": [
    {
      "title": "Eğitim Başarılı: Llama-3.1-8B-Instruct_finetune",
      "text": "İş başarıyla tamamlandı.\n\nMetrikler:\n• eval_loss: 1.2500\n• train_loss: 0.8900",
      "color": "#36a64f"
    }
  ]
}
```

```json
// Exit kod 4 — insan onayı bekliyor
{
  "event": "approval.required",
  "run_name": "Llama-3.1-8B-Instruct_finetune",
  "status": "awaiting_approval",
  "model_path": "./checkpoints/final_model.staging.<run_id>"
}
```

(Wire-format event adı `approval.required`'tır — tam 5-event yüzeyi
için bkz. `docs/reference/audit_event_catalog-tr.md` Webhook lifecycle
tablosu, emitter için bkz. `forgelm/webhook.py:notify_awaiting_approval`.)

---

## Exit kod yönetimi

```bash
forgelm --config job.yaml --output-format json > result.json
EXIT_CODE=$?

case $EXIT_CODE in
  0) echo "Eğitim başarılı" ;;
  1) echo "Config hatası — YAML'inizi düzeltin" ;;
  2) echo "Eğitim çöktü — GPU/bellek kontrol et" ;;
  3) echo "Değerlendirme başarısız — model kalitesi eşiğin altında" ;;
  4) echo "İnsan onayı bekleniyor — deploy'dan önce sonuçları incele" ;;
esac
```

---

## JSON çıktıyı parse etme

### Bash (jq)

```bash
# eval_loss al
forgelm --config job.yaml --output-format json | jq '.metrics.eval_loss'

# Benchmark geçti mi kontrol et
forgelm --config job.yaml --output-format json | jq '.benchmark.passed'

# GPU saatlerini al
forgelm --config job.yaml --output-format json | jq '.resource_usage.gpu_hours'
```

### Python

```python
import json
import subprocess

result = subprocess.run(
    ["forgelm", "--config", "job.yaml", "--output-format", "json"],
    capture_output=True, text=True
)
data = json.loads(result.stdout)
print(f"Başarı: {data['success']}")
print(f"Eval Loss: {data['metrics'].get('eval_loss')}")
print(f"GPU Saatleri: {data.get('resource_usage', {}).get('gpu_hours')}")
```

---

## En iyi pratikler

1. **Her zaman önce doğrula**: GPU eğitiminden önce hafif bir job'da
   `--dry-run` kullan
2. **Config'inizi git'e pinleyin**: Eğitim config'leri koddur — sürüm
   kontrolüne alın
3. **`--output-format json` kullanın**: Pipeline kararları için
   makine-okunabilir çıktı
4. **`auto_revert: true` set edin**: Bozulmuş modellerin deploy
   edilmesini engelle
5. **Air-gapped için `--offline` kullanın**: Modellerin/dataset'lerin
   önceden cache'lendiğinden emin olun
6. **`--resume` kullanın**: Preemptible instance'lardaki uzun eğitim
   işleri otomatik resume etmeli
7. **Exit kodları kontrol edin**: Farklı kodlar farklı şeyler ifade eder
   — onları handle edin
8. **Compliance artefakt'larını saklayın**: `checkpoints/compliance/`
   audit izlerini içerir
9. **Config hatalarının hızlı başarısız olmasını bekleyin**: v0.3.1rc1'den
   beri bilinmeyen YAML alanları hemen `ConfigError` atar — bu, GPU
   tahsis edilmeden CI'da typo'ları yakalar
10. **Compliance artefakt'ları sürüm kontrolünde**: Tam düzenleyici
    audit izleri için `checkpoints/compliance/` ve model kartlarını
    birlikte commit'lemeyi düşünün
