# CI/CD Pipeline Integration Guide

ForgeLM is built for automation. This guide shows how to integrate fine-tuning into GitHub Actions, GitLab CI, and generic CI/CD pipelines.

---

## Core Principles

ForgeLM's CI/CD-native design provides:
- **YAML-driven**: Entire training runs defined in version-controlled config files
- **Meaningful exit codes**: `0` success, `1` config error, `2` training error, `3` eval failure, `4` awaiting approval
- **JSON output**: `--output-format json` for machine-readable results
- **Dry-run validation**: `--dry-run` validates without GPU
- **Webhook notifications**: Real-time Slack/Teams alerts on start/success/failure

---

## GitHub Actions

### Basic Training Workflow

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
        description: 'Config file path'
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
      - name: Validate config
        run: forgelm --config ${{ github.event.inputs.config || 'configs/production.yaml' }} --dry-run --output-format json

  train:
    needs: validate
    runs-on: [self-hosted, gpu]  # GPU runner
    steps:
      - uses: actions/checkout@v4
      - run: pip install -e ".[qlora,eval]"

      - name: Train model
        env:
          HUGGINGFACE_TOKEN: ${{ secrets.HF_TOKEN }}
          FORGELM_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK }}
        run: |
          forgelm --config ${{ github.event.inputs.config || 'configs/production.yaml' }} \
            --output-format json > training_result.json
          echo "EXIT_CODE=$?" >> $GITHUB_ENV

      - name: Upload model artifacts
        if: success()
        uses: actions/upload-artifact@v4
        with:
          name: fine-tuned-model
          path: checkpoints/final_model/

      - name: Upload training results
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: training-results
          path: |
            training_result.json
            checkpoints/compliance/
            checkpoints/benchmark/
```

### Multi-Model Matrix Training

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
      - name: Train ${{ matrix.config }}
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
  image: forgelm:latest  # or build from Dockerfile
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

## Docker-Based Pipeline

For environments without Python setup:

```bash
# Build once
docker build -t forgelm:latest --build-arg INSTALL_EVAL=true .

# Run in pipeline
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

## Webhook Integration

### Slack

```yaml
# In your ForgeLM config
webhook:
  url_env: "FORGELM_WEBHOOK_URL"
  notify_on_start: true
  notify_on_success: true
  notify_on_failure: true
```

ForgeLM sends structured payloads:

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
      "title": "Training Succeeded: Llama-3.1-8B-Instruct_finetune",
      "text": "The job completed successfully.\n\nMetrics:\n• eval_loss: 1.2500\n• train_loss: 0.8900",
      "color": "#36a64f"
    }
  ]
}
```

```json
// Exit code 4 — awaiting human approval
{
  "event": "approval.required",
  "run_name": "Llama-3.1-8B-Instruct_finetune",
  "status": "awaiting_approval",
  "model_path": "./checkpoints/final_model.staging.<run_id>"
}
```

(Wire-format event name is `approval.required` — see `docs/reference/audit_event_catalog.md` Webhook lifecycle table for the full 5-event surface and `forgelm/webhook.py:notify_awaiting_approval` for the emitter.)

---

## Exit Code Handling

```bash
forgelm --config job.yaml --output-format json > result.json
EXIT_CODE=$?

case $EXIT_CODE in
  0) echo "Training succeeded" ;;
  1) echo "Config error — fix your YAML" ;;
  2) echo "Training crashed — check GPU/memory" ;;
  3) echo "Evaluation failed — model quality below threshold" ;;
  4) echo "Awaiting human approval — review results before deploying" ;;
esac
```

---

## Parsing JSON Output

### Bash (jq)

```bash
# Get eval_loss
forgelm --config job.yaml --output-format json | jq '.metrics.eval_loss'

# Check if benchmark passed
forgelm --config job.yaml --output-format json | jq '.benchmark.passed'

# Get GPU hours
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
print(f"Success: {data['success']}")
print(f"Eval Loss: {data['metrics'].get('eval_loss')}")
print(f"GPU Hours: {data.get('resource_usage', {}).get('gpu_hours')}")
```

---

## Best Practices

1. **Always validate first**: Use `--dry-run` in a lightweight job before GPU training
2. **Pin your config in git**: Training configs are code — version control them
3. **Use `--output-format json`**: Machine-readable output for pipeline decisions
4. **Set `auto_revert: true`**: Prevent deploying degraded models
5. **Use `--offline` for air-gapped**: Ensure models/datasets are pre-cached
6. **Use `--resume`**: Long training jobs on preemptible instances should auto-resume
7. **Check exit codes**: Different codes mean different things — handle them
8. **Store compliance artifacts**: `checkpoints/compliance/` contains audit trails
9. **Expect config errors to fail fast**: Since v0.3.1rc1, unknown YAML fields raise `ConfigError` immediately — this catches typos in CI before GPU allocation
10. **Compliance artifacts in version control**: Consider committing `checkpoints/compliance/` alongside model cards for full regulatory audit trails
