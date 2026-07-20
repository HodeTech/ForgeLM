# Quick Start Guide

Get your first fine-tuned model in 5 minutes.

---

## Prerequisites

- Python 3.10+
- NVIDIA GPU with CUDA (recommended; CPU works but is very slow)

## 1. Install

```bash
git clone https://github.com/HodeTech/ForgeLM.git
cd ForgeLM
pip install -e .

# Recommended: enable 4-bit quantization (Linux)
pip install -e ".[qlora]"
```

## 2. Generate Config

### Option 0: One-Command Quickstart Template (v0.4.5+)

The fastest path: pick a bundled template and let ForgeLM pick the model, dataset, and conservative defaults for you.

```bash
# List the bundled templates
forgelm quickstart --list

# Generate a config (and a small bundled seed dataset) for a customer-support assistant
forgelm quickstart customer-support --dry-run

# Run end-to-end: render config, train, then drop into chat with the result
forgelm quickstart customer-support
```

Bundled templates (all use QLoRA 4-bit, rank-8, batch=1 by default — safe to run on a single 12 GB GPU):

| Template | Trainer | What you get |
|---|---|---|
| `customer-support` | SFT | Polite, brand-safe support replies |
| `code-assistant` | SFT | Short Python/programming Q&A |
| `domain-expert` | SFT | Empty (BYOD — pair with your JSONL) |
| `medical-qa-tr` | SFT | Turkish medical Q&A with safety disclaimers |
| `grpo-math` | GRPO | Grade-school math reasoning |

ForgeLM auto-downsizes the model on small GPUs. Each template has its own fallback chosen for the task:

| Template | Primary (≥10 GB VRAM) | Fallback (<10 GB) |
|---|---|---|
| `customer-support` | Qwen/Qwen2.5-7B-Instruct | HuggingFaceTB/SmolLM2-1.7B-Instruct |
| `code-assistant` | Qwen/Qwen2.5-Coder-7B-Instruct | Qwen/Qwen2.5-Coder-1.5B-Instruct |
| `domain-expert` | Qwen/Qwen2.5-7B-Instruct | HuggingFaceTB/SmolLM2-1.7B-Instruct |
| `medical-qa-tr` | Qwen/Qwen2.5-7B-Instruct | Qwen/Qwen2.5-1.5B-Instruct |
| `grpo-math` | Qwen/Qwen2.5-Math-7B-Instruct | Qwen/Qwen2.5-Math-1.5B-Instruct |

Override with `--model your-org/your-model` or `--dataset path/to/your.jsonl`.

See [LICENSES.md](https://github.com/HodeTech/ForgeLM/blob/main/forgelm/templates/LICENSES.md) for the licenses of bundled seed datasets (CC-BY-SA 4.0, author-original).

### Option A: Interactive Wizard

```bash
forgelm --wizard
```

The wizard offers a curated quickstart-template shortcut first; declining opens a 9-step interactive flow (welcome / use-case / model / strategy / trainer / dataset / training-params / compliance / operations) that covers every `ForgeConfig` block — model, LoRA / DoRA / PiSSA / rsLoRA / GaLore strategy, per-trainer hyperparameters (`dpo_beta` / `simpo_*` / `kto_beta` / `orpo_beta` / `grpo_*`), EU AI Act Article 9 / 10 / 11 / 12+17 compliance metadata, retention, monitoring, evaluation gates, webhooks, synthetic data — and writes a ready-to-use YAML. Type `back` / `b` to navigate backwards, `reset` / `r` to start over; state is persisted to `~/.cache/forgelm/wizard_state.yaml` so a Ctrl-C / fresh session can resume.

Operator guardrails layered on by review-cycle 2 (2026-05-09): the wizard runs `ForgeConfig.model_validate` on the saved YAML before exit (so schema rejections surface inline, not 30 minutes into training), prompts before overwriting an existing config (auto-suffixes `_2.yaml` / `_3.yaml` if you decline), refuses to launch under non-tty stdin (use `forgelm quickstart <template>` for scripted runs), prints a pre-flight checklist (GPU/VRAM/dataset/risk-tier signals), and exits `EXIT_WIZARD_CANCELLED = 5` on Ctrl-C / cancel so CI can tell "wizard finished" from "wizard never wrote anything".

**Idempotent re-run (PR-D, 2026-05-09):** to iterate on an existing config without losing prior answers, pass `--wizard-start-from`:

```bash
forgelm --wizard --wizard-start-from my_config.yaml
```

The wizard reads the YAML, validates it against `ForgeConfig` up-front (immediate failure on schema violation), and seeds each step's prompts with the loaded values — pressing Enter at each numeric / text prompt keeps the existing value.  Choice prompts (Strategy, Target modules, Trainer, Use-case) detect the loaded value and shift their default index to match, so Enter still preserves it.  The use-case step (Step 2) is skipped entirely when an existing model + trainer choice is detected, to avoid overwriting them with the first template's preset.  The save flow defaults to overwriting the same path; the existing overwrite confirmation still fires before clobbering.

**Heads-up:** non-prompted Annex IV / risk-assessment fields (e.g., `lora.dropout`, `lora.bias`, `lora.task_type`) are now preserved via `setdefault` patterns rather than overwritten — review-cycle 4 (PR-E, 2026-05-09) closed this regression.

### Option B: Copy Template

```bash
cp config_template.yaml my_config.yaml
```

Edit `my_config.yaml` — at minimum set:
```yaml
model:
  name_or_path: "HuggingFaceTB/SmolLM2-1.7B-Instruct"  # or your model

data:
  dataset_name_or_path: "timdettmers/openassistant-guanaco"  # or your dataset
```

### Option C: I have raw documents (PDFs / DOCX / EPUBs), not JSONL

Run the Phase 11 ingestion + audit pipeline first, then point any of the
options above at the resulting JSONL:

```bash
pip install -e ".[ingestion]"
forgelm ingest ./policies/ --recursive --output data/policies.jsonl
forgelm audit data/policies.jsonl --output ./audit/
# Now `data/policies.jsonl` is ready to plug into a config.
```

See the [Document Ingestion Guide](ingestion.md) and [Dataset Audit
Guide](data_audit.md) for chunking strategies, PII masking, and the
governance signals the audit surfaces.

## 3. Validate (Dry Run)

```bash
forgelm --config my_config.yaml --dry-run
```

This validates your config, checks model/dataset accessibility, and shows all resolved parameters — without downloading anything heavy.

For machine-readable output:
```bash
forgelm --config my_config.yaml --dry-run --output-format json
```

## 4. Train

```bash
forgelm --config my_config.yaml
```

That's it. ForgeLM handles:
- Model download and quantization
- Dataset formatting with chat templates
- LoRA adapter setup
- Training with early stopping
- Evaluation and model saving
- Model card generation

## 5. Find Your Model

After training, your adapter is saved to:

```text
./checkpoints/final_model/
├── adapter_config.json
├── adapter_model.safetensors
├── tokenizer.json
├── tokenizer_config.json
└── README.md  (auto-generated model card)
```

## 5.5 Check GPU Memory Before Training

Before starting a long run, estimate if your config fits in GPU memory:

```bash
forgelm --config my_config.yaml --fit-check
# GPU: RTX 3060 12GB — Estimated peak: 10.8 GB — Verdict: FITS
# Or: Verdict: TIGHT — Enable gradient checkpointing and reduce batch size
# Or: Verdict: UNKNOWN — No GPU detected (hypothetical estimate)
```

Output includes a breakdown (base weights, LoRA adapter, optimizer state, activations) and ordered recommendations when memory is tight. Use `--output-format json` for CI/CD integration.

If you hit OOM during training, the [Troubleshooting guide](troubleshooting.md) has detailed solutions.

## 6. Use Your Model

```python
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer

base = AutoModelForCausalLM.from_pretrained("HuggingFaceTB/SmolLM2-1.7B-Instruct")
model = PeftModel.from_pretrained(base, "./checkpoints/final_model")
tokenizer = AutoTokenizer.from_pretrained("./checkpoints/final_model")

inputs = tokenizer("What is ForgeLM?", return_tensors="pt")
output = model.generate(**inputs, max_new_tokens=200)
print(tokenizer.decode(output[0], skip_special_tokens=True))
```

### Using Your Model (v0.4.0+)

Interact with and deploy your trained model directly:

```bash
# Chat with your fine-tuned model (streaming by default)
forgelm chat ./checkpoints/final_model

# Export to GGUF (for Ollama, LM Studio, llama.cpp)
# Requires: pip install forgelm[export]
forgelm export ./checkpoints/final_model --output model.gguf --quant q4_k_m

# Generate deployment configs (no server is started)
forgelm deploy ./checkpoints/final_model --target ollama --output ./Modelfile
forgelm deploy ./checkpoints/final_model --target vllm --output ./vllm_config.yaml
```

---

## Common Config Tweaks

### Use Unsloth for faster training (Linux only)

```bash
pip install -e ".[unsloth]"
```

```yaml
model:
  backend: "unsloth"
```

### Enable DoRA for better quality at same rank

```yaml
lora:
  method: "dora"  # DoRA adapter (better quality than standard LoRA at same rank)
  # Note: lora.use_dora is deprecated; use method: "dora" instead
```

### Add webhook notifications (Slack/Teams)

```yaml
webhook:
  url_env: "FORGELM_WEBHOOK_URL"
  notify_on_start: true
  notify_on_success: true
  notify_on_failure: true
```

```bash
export FORGELM_WEBHOOK_URL="https://hooks.slack.com/services/T.../B.../xxx"
forgelm --config my_config.yaml
```

### Enable OOM recovery (automatic batch size reduction)

```yaml
training:
  per_device_train_batch_size: 8
  gradient_accumulation_steps: 2
  oom_recovery: true
  oom_recovery_min_batch_size: 1
```

### Auto-revert bad models

```yaml
evaluation:
  auto_revert: true
  max_acceptable_loss: 2.0
```

If the fine-tuned model's eval loss exceeds the threshold, ForgeLM automatically deletes the adapter and exits with code 3.

---

### Enable GaLore for memory-efficient full-parameter training

GaLore is an alternative to LoRA that enables full-parameter training via gradient low-rank projection, using significantly less memory:

```yaml
training:
  galore_enabled: true
  galore_optim: "galore_adamw_8bit"
  galore_rank: 128
```

### Generate synthetic training data

Use a teacher model to generate training data before fine-tuning:

```bash
forgelm --config my_config.yaml --generate-data
```

```yaml
synthetic:
  enabled: true
  teacher_model: "gpt-4o"
  teacher_backend: "api"
  api_key_env: "OPENAI_API_KEY"
  api_base: "https://api.openai.com/v1"
  seed_file: "seed_prompts.jsonl"
  output_file: "synthetic_data.jsonl"
  output_format: "messages"
```

The number of synthetic rows is controlled by the seed-file size (one teacher call per seed); see the `SyntheticConfig` Pydantic model in `forgelm/config.py` for the full field set ([repo search](https://github.com/HodeTech/ForgeLM/search?q=class+SyntheticConfig)).

---

## Next Steps

- [CI/CD Pipeline Integration](cicd_pipeline.md) — automate training in your pipeline
- [Alignment Guide](alignment.md) — DPO, SimPO, KTO, GRPO
- [Enterprise Deployment](enterprise_deployment.md) — Docker, offline, multi-GPU
- [Safety & Compliance](safety_compliance.md) — EU AI Act, safety evaluation
- [Troubleshooting](troubleshooting.md) — common issues and solutions

### Runnable notebooks (Colab)

- [Quick Start — SFT](../../notebooks/quickstart_sft.ipynb)
- [Post-Training Workflow](../../notebooks/post_training_workflow.ipynb) — `--fit-check` → `chat` → `export` → `deploy`
- [Multi-Dataset Training](../../notebooks/multi_dataset.ipynb), [GaLore Memory Optimization](../../notebooks/galore_memory_optimization.ipynb), [Synthetic Data Pipeline](../../notebooks/synthetic_data_training.ipynb)
- [Safety Evaluation & Red-Teaming](../../notebooks/safety_evaluation.ipynb)
- Alignment: [DPO](../../notebooks/dpo_alignment.ipynb), [KTO](../../notebooks/kto_binary_feedback.ipynb), [GRPO](../../notebooks/grpo_reasoning.ipynb)
