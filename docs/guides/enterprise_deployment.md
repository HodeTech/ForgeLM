# Enterprise Deployment Guide

Deploy ForgeLM in production environments: Docker, air-gapped, multi-GPU, and on-premise.

---

## Docker Deployment

### Build

```bash
# Standard image (with QLoRA)
docker build -t forgelm .

# With benchmarking
docker build -t forgelm:eval --build-arg INSTALL_EVAL=true .

# Full image (QLoRA + Unsloth + eval)
docker build -t forgelm:full \
  --build-arg INSTALL_EVAL=true \
  --build-arg INSTALL_UNSLOTH=true .
```

### Run

```bash
# Training
docker run --gpus all \
  -v $(pwd)/my_config.yaml:/workspace/config.yaml:ro \
  -v $(pwd)/data:/workspace/data:ro \
  -v $(pwd)/output:/workspace/output \
  forgelm --config /workspace/config.yaml

# Dry-run (no GPU needed)
docker run \
  -v $(pwd)/my_config.yaml:/workspace/config.yaml:ro \
  forgelm --config /workspace/config.yaml --dry-run --output-format json

# Interactive wizard
docker run -it forgelm --wizard
```

### Docker Compose

```bash
# Training
docker compose run --rm train --config /workspace/configs/job.yaml

# TensorBoard (http://localhost:6006)
docker compose up tensorboard
```

### Multi-GPU with Docker

```bash
docker run --gpus all \
  --shm-size=16g \
  -v $(pwd)/config.yaml:/workspace/config.yaml:ro \
  -v $(pwd)/output:/workspace/output \
  forgelm:full \
  torchrun --nproc_per_node=4 -m forgelm.cli --config /workspace/config.yaml
```

> `--shm-size=16g` is required for multi-GPU — PyTorch's inter-process communication needs more shared memory than Docker's 64MB default.

---

## Air-Gapped / Offline Deployment

For environments without internet access (banking, healthcare, defense):

### Step 1: Pre-download on Connected Machine

```bash
# Download model
python -c "
from transformers import AutoModelForCausalLM, AutoTokenizer
AutoModelForCausalLM.from_pretrained('meta-llama/Llama-3.1-8B-Instruct', cache_dir='./model_cache')
AutoTokenizer.from_pretrained('meta-llama/Llama-3.1-8B-Instruct', cache_dir='./model_cache')
"

# Download dataset
python -c "
from datasets import load_dataset
ds = load_dataset('your_org/dataset', cache_dir='./data_cache')
ds.save_to_disk('./data/my_dataset')
"
```

### Step 2: Transfer to Air-Gapped Machine

Copy `model_cache/`, `data/`, and ForgeLM package to the isolated machine.

### Step 3: Configure for Offline

```yaml
model:
  name_or_path: "/data/model_cache/models--meta-llama--Llama-3.1-8B-Instruct/snapshots/..."
  offline: true  # or use --offline CLI flag

data:
  dataset_name_or_path: "/data/my_dataset"
```

### Step 4: Run

```bash
forgelm --config job.yaml --offline
# or
forgelm --config job.yaml  # offline: true in YAML does the same
```

ForgeLM sets `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, `HF_DATASETS_OFFLINE=1` automatically.

---

## Multi-GPU Training

### DeepSpeed

```yaml
distributed:
  strategy: "deepspeed"
  deepspeed_config: "/etc/forgelm/ds_zero2.json"  # your own DeepSpeed JSON; the zero2/zero3/zero3_offload preset names need a source checkout
```

```bash
torchrun --nproc_per_node=4 -m forgelm.cli --config job.yaml
```

### FSDP

```yaml
distributed:
  strategy: "fsdp"
  fsdp_strategy: "full_shard"
  fsdp_auto_wrap: true
```

```bash
torchrun --nproc_per_node=4 -m forgelm.cli --config job.yaml
```

See the [Distributed Training Guide](../reference/distributed_training.md) for detailed ZeRO stage comparison and multi-node setup.

---

## Security Checklist

| Setting | Recommended Value | Why |
|---------|------------------|-----|
| `trust_remote_code` | `false` | Prevents arbitrary code execution from model repos |
| `auth.hf_token` | Use `HUGGINGFACE_TOKEN` env var | Don't hardcode tokens in YAML |
| `webhook.url` | Always use `url_env` with environment variable | Webhook tokens excluded from model cards (v0.3.1rc1+), but avoid direct URLs for credential hygiene |
| `offline` | `true` for air-gapped | Prevents any network calls |

---

## Checkpoint Resume for Preemptible Instances

On spot/preemptible GPU instances, training can be interrupted. ForgeLM supports automatic resume:

```bash
# First run
forgelm --config job.yaml

# After interruption — auto-detect latest checkpoint
forgelm --config job.yaml --resume

# Or specify exact checkpoint
forgelm --config job.yaml --resume ./checkpoints/checkpoint-500
```

---

## Resource Monitoring

ForgeLM automatically tracks and reports:

```json
{
  "resource_usage": {
    "gpu_model": "NVIDIA A100 80GB",
    "peak_vram_gb": 22.1,
    "gpu_count": 4,
    "training_duration_seconds": 8640,
    "gpu_hours": 9.6
  }
}
```

Access via JSON output:
```bash
forgelm --config job.yaml --output-format json | jq '.resource_usage'
```

---

## Compliance Artifacts

Every training run automatically generates compliance artifacts in `checkpoints/compliance/`:

```
checkpoints/compliance/
├── compliance_report.json    # Full structured audit trail
├── training_manifest.yaml    # Human-readable summary
└── data_provenance.json      # Dataset fingerprints (SHA-256)
```

These are generated automatically — no additional configuration needed.

### Full Evidence Bundle

The complete compliance artifact set (generated automatically):

```
checkpoints/compliance/
├── compliance_report.json
├── training_manifest.yaml
├── data_provenance.json
├── risk_assessment.json
├── data_governance_report.json
├── annex_iv_metadata.json
├── deployer_instructions.md
├── model_integrity.json
└── audit_log.jsonl          # Tamper-evident hash chain, continuous across restarts
```

Export compliance artifacts without re-training:
```bash
forgelm --config job.yaml --compliance-export ./audit/
```

---

## Production Config Template

```yaml
model:
  name_or_path: "meta-llama/Llama-3.1-8B-Instruct"
  load_in_4bit: true
  trust_remote_code: false
  backend: "transformers"

lora:
  r: 16
  alpha: 32
  dropout: 0.05
  method: "dora"
  target_modules: ["q_proj", "k_proj", "v_proj", "o_proj"]

training:
  trainer_type: "sft"
  output_dir: "./checkpoints"
  num_train_epochs: 3
  per_device_train_batch_size: 4
  gradient_accumulation_steps: 4
  learning_rate: 2.0e-5
  eval_steps: 100
  save_steps: 100
  report_to: "wandb"

data:
  dataset_name_or_path: "./data/training_data.jsonl"

evaluation:
  auto_revert: true
  max_acceptable_loss: 2.0
  benchmark:
    enabled: true
    tasks: ["arc_easy", "hellaswag"]
    min_score: 0.4
  safety:
    enabled: true
    classifier: "meta-llama/Llama-Guard-3-8B"
    test_prompts: "./data/safety_prompts.jsonl"

webhook:
  url_env: "FORGELM_WEBHOOK_URL"
  notify_on_start: true
  notify_on_success: true
  notify_on_failure: true
```
