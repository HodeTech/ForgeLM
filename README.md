# ForgeLM

[![PyPI](https://img.shields.io/pypi/v/forgelm.svg)](https://pypi.org/project/forgelm/)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![CI](https://github.com/HodeTech/ForgeLM/actions/workflows/ci.yml/badge.svg)](https://github.com/HodeTech/ForgeLM/actions/workflows/ci.yml)

**A config-driven LLM fine-tuning toolkit for everyone — from solo researchers to enterprise platform teams.** SFT → DPO → SimPO → KTO → ORPO → GRPO, with safety evaluation, EU AI Act compliance, and CI/CD-native design baked in. YAML in — fine-tuned model + audit artefacts out.

Use it interactively from a Jupyter notebook, drop it into a CI/CD pipeline, or run it from the terminal — the same YAML and the same Python API drive every entry point. Runs on Linux, macOS, and Windows.[^1]

---

## Quick Start

```bash
pip install forgelm

# Fastest path: a bundled template that runs on a 12 GB GPU
forgelm quickstart customer-support

# Or generate a config interactively
forgelm --wizard

# Validate, fit-check, then train
forgelm --config my_config.yaml --dry-run
forgelm --config my_config.yaml --fit-check
forgelm --config my_config.yaml

# After training: chat, export, deploy
forgelm chat ./checkpoints/final_model
forgelm export ./checkpoints/final_model --quant q4_k_m
forgelm deploy ./checkpoints/final_model --target ollama
```

See the [Quick Start Guide](docs/guides/quickstart.md) for the full walkthrough.

---

## Why ForgeLM

- **Config-driven.** Behaviour is set in validated YAML — reproducible across notebooks, terminals, and CI runs with no hidden env-var flags.
- **Full alignment stack.** Every modern post-training method in one tool, one schema.
- **Safety and compliance are first-class.** Not an afterthought, not a separate product.
- **CI/CD-native.** Stable exit codes (`0/1/2/3/4/5`), JSON output, append-only audit log, deterministic dry-runs.
- **Bring-your-own-data.** PDF / DOCX / EPUB / Markdown → SFT-ready JSONL with a single command.
- **Open source.** Apache-2.0, no telemetry, no required cloud service.

---

## Features

### Training
- **6 trainer types:** SFT, DPO, SimPO, KTO, ORPO, GRPO
- **Memory-efficient methods:** 4-bit QLoRA, DoRA, PiSSA, rsLoRA, GaLore
- **Backends:** Unsloth (2–5× faster) or standard Transformers
- **Distributed:** DeepSpeed ZeRO-2/3, FSDP, multi-GPU, MoE-aware (Qwen3, Mixtral, DeepSeek)
- **Long-context:** RoPE scaling, NEFTune, sliding-window attention, sample packing
- **Multi-dataset mixing** and **synthetic data distillation** (teacher → student)

### Data Pipeline
- `forgelm ingest` — PDF / DOCX / EPUB / TXT / Markdown → SFT-ready JSONL, with token-aware and markdown-aware chunking
- `forgelm audit` — length, language, near-duplicate detection (SimHash + optional MinHash LSH), cross-split leakage, PII (TR / DE / FR / US-SSN, Luhn-validated), and a 9-family secrets scan
- **PII masking on ingest** (emails, phones, cards, IBAN, national IDs) and **secrets masking** before chunks land in the JSONL
- **Croissant 1.0 dataset cards** — the same JSON doubles as your EU AI Act Article 10 governance artefact

### Evaluation & Safety
- **Benchmarks** via `lm-evaluation-harness`
- **LLM-as-Judge** scoring (OpenAI API or local model)
- **Llama Guard safety classifier** with S1–S14 harm categories, severity tiers, and cross-run trend tracking
- **Auto-revert** — runs that fail loss, benchmark, or safety thresholds are discarded before artefacts are written
- **VRAM fit-check** — pre-flight `FITS / TIGHT / OOM / UNKNOWN` estimator with concrete recommendations

### Production & Deployment
- `forgelm chat` — streaming REPL with slash commands and optional safety routing
- `forgelm export` — GGUF export (6 quant levels) via `llama-cpp-python`
- `forgelm deploy` — generates Ollama, vLLM, TGI, or HF Endpoints configs
- **Model merging** (TIES, DARE, SLERP, linear) and auto-generated HF model cards
- **Webhooks** (Slack / Teams) and tracking via W&B / MLflow / TensorBoard
- **Stable Python API:** `from forgelm import ForgeTrainer, audit_dataset, verify_audit_log, ...` — every CLI surface has a typed entry point

---

## Compliance & Safety

Most fine-tuning tools stop at "the model trained." ForgeLM produces the artefacts an auditor will ask for next:

- **EU AI Act** — auto-generated Annex IV technical documentation, Article 10 data governance, Article 14 human-oversight staging gate
- **GDPR** — `forgelm purge` (Article 17 right-to-erasure) and `forgelm reverse-pii` (Article 15 right-of-access)
- **Append-only audit log** — HMAC-chained when `FORGELM_AUDIT_SECRET` is configured; every decision gate emits a structured event
- **Supply-chain hardening** — CycloneDX 1.5 SBOM per release, nightly `pip-audit` + `bandit`, `gitleaks` pre-commit
- **ISO 27001 / SOC 2 alignment** — software cannot be certified, but ForgeLM produces the change-management, data-lineage, and audit-trail evidence your deployer's auditor needs. See the [Deployer Audit Guide](docs/guides/iso_soc2_deployer_guide.md).

Full details: [Safety & Compliance Guide](docs/guides/safety_compliance.md) · [Supply-Chain Security](docs/reference/supply_chain_security.md)

---

## Documentation

| Topic | English | Türkçe |
|---|---|---|
| Quick Start | [quickstart.md](docs/guides/quickstart.md) | — |
| Document Ingestion | [ingestion.md](docs/guides/ingestion.md) | [ingestion-tr.md](docs/guides/ingestion-tr.md) |
| Dataset Audit | [data_audit.md](docs/guides/data_audit.md) | [data_audit-tr.md](docs/guides/data_audit-tr.md) |
| Alignment (DPO / SimPO / KTO / GRPO) | [alignment.md](docs/guides/alignment.md) | — |
| Multi-Stage Pipelines | [pipeline.md](docs/guides/pipeline.md) | [pipeline-tr.md](docs/guides/pipeline-tr.md) |
| CI/CD Integration | [cicd_pipeline.md](docs/guides/cicd_pipeline.md) | [cicd_pipeline-tr.md](docs/guides/cicd_pipeline-tr.md) |
| Enterprise Deployment | [enterprise_deployment.md](docs/guides/enterprise_deployment.md) | — |
| Safety & Compliance | [safety_compliance.md](docs/guides/safety_compliance.md) | — |
| Troubleshooting & FAQ | [troubleshooting.md](docs/guides/troubleshooting.md) | — |
| **Architecture Reference** | [architecture.md](docs/reference/architecture.md) | [architecture-tr.md](docs/reference/architecture-tr.md) |
| **Configuration Reference** | [configuration.md](docs/reference/configuration.md) | [configuration-tr.md](docs/reference/configuration-tr.md) |
| **Product Strategy & Roadmap** | [product_strategy.md](docs/product_strategy.md) · [roadmap.md](docs/roadmap.md) | [product_strategy-tr.md](docs/product_strategy-tr.md) · [roadmap-tr.md](docs/roadmap-tr.md) |

---

## Notebooks

Featured walkthroughs, runnable in Colab on a free T4 GPU:

- [Quick Start — SFT Fine-Tuning](notebooks/quickstart_sft.ipynb) [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/HodeTech/ForgeLM/blob/main/notebooks/quickstart_sft.ipynb)
- [GRPO Reasoning RL](notebooks/grpo_reasoning.ipynb) [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/HodeTech/ForgeLM/blob/main/notebooks/grpo_reasoning.ipynb)
- [Safety Evaluation & Red-Teaming](notebooks/safety_evaluation.ipynb) [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/HodeTech/ForgeLM/blob/main/notebooks/safety_evaluation.ipynb)

See [notebooks/](notebooks/) for the full set (DPO, KTO, multi-dataset, GaLore, synthetic data, post-training workflow, data curation).

---

## Installation

```bash
# From PyPI
pip install forgelm

# From source
git clone https://github.com/HodeTech/ForgeLM.git
cd ForgeLM
pip install -e .
```

**Prerequisites:** Python 3.10+, `torch>=2.2.0`. Platform-specific notes are in the [installation guide](docs/usermanuals/en/getting-started/installation.md).

### Optional extras

```bash
pip install "forgelm[qlora]"            # 4-bit quantization (Linux)
pip install "forgelm[unsloth]"          # Unsloth backend (Linux)
pip install "forgelm[eval]"             # lm-evaluation-harness
pip install "forgelm[tracking]"         # W&B / MLflow
pip install "forgelm[distributed]"      # DeepSpeed
pip install "forgelm[merging]"          # mergekit
pip install "forgelm[ingestion]"        # PDF / DOCX / EPUB / Markdown
pip install "forgelm[ingestion-scale]"  # MinHash LSH for large corpora
pip install "forgelm[ingestion-pii-ml]" # Presidio NER (also needs spaCy model)
pip install "forgelm[export]"           # GGUF via llama-cpp-python
pip install "forgelm[chat]"             # Rich terminal rendering
```

---

## Docker

```bash
docker build -t forgelm --build-arg INSTALL_EVAL=true .

docker run --gpus all \
  -v $(pwd)/my_config.yaml:/workspace/config.yaml \
  -v $(pwd)/output:/workspace/output \
  forgelm --config /workspace/config.yaml
```

Multi-GPU and air-gapped deployment patterns are documented in the [Enterprise Deployment Guide](docs/guides/enterprise_deployment.md).

---

## Contributing & License

Contributions are welcome — start with [CONTRIBUTING.md](CONTRIBUTING.md) and the engineering standards in [docs/standards/](docs/standards/).

Licensed under the [Apache License 2.0](LICENSE).

[^1]: `qlora` and `unsloth` extras depend on Linux-only upstream wheels; on macOS and Windows the install succeeds but those backends are skipped via a `sys_platform == 'linux'` marker. All other extras are cross-platform.
