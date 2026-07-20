# ForgeLM

[![PyPI](https://img.shields.io/pypi/v/forgelm.svg)](https://pypi.org/project/forgelm/)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![CI](https://github.com/HodeTech/ForgeLM/actions/workflows/ci.yml/badge.svg)](https://github.com/HodeTech/ForgeLM/actions/workflows/ci.yml)

**The config-driven LLM fine-tuning toolkit for teams that ship models into regulated environments.** YAML in — fine-tuned model, safety report, and EU AI Act audit artefacts out, with stable exit codes so a failed safety gate fails your pipeline instead of your launch.

New to fine-tuning? `forgelm quickstart customer-support` generates a config and seed dataset sized for a 12 GB GPU, trains it, and drops you into a chat REPL with the result — and you can ignore everything below until you need it.

---

## The config

This is the whole interface. No hidden env-var flags, no imperative glue script:

```yaml
model:
  name_or_path: "Qwen/Qwen2.5-7B-Instruct"
  load_in_4bit: true                      # QLoRA — 4-bit base weights
  # revision: "<40-hex commit SHA>"       # pin the exact Hub commit for reproducibility

data:
  dataset_name_or_path: "./data/support.jsonl"

training:
  trainer_type: "sft"                     # sft | dpo | simpo | kto | orpo | grpo
  output_dir: "./checkpoints"
  num_train_epochs: 3

lora:
  r: 16
  alpha: 32

evaluation:
  auto_revert: true                       # off by default — see the caveat below
  max_acceptable_loss: 0.8
  safety:
    enabled: true
    classifier: "meta-llama/Llama-Guard-3-8B"
    max_safety_regression: 0.02           # >2% unsafe responses fails the gate
```

Every key is a validated Pydantic field — a typo or an unenforceable threshold is a startup error, not a silent no-op three hours into a run. The full surface is in the [Configuration Reference](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/configuration.md).

---

## Quick Start

```bash
pip install forgelm

# Zero-to-trained-model on a bundled template (5 available: forgelm quickstart --list).
# This generates the config AND trains it; add --dry-run to stop at the config.
forgelm quickstart customer-support

# Or, from a config of your own — validate, estimate VRAM, then train
forgelm --config my_config.yaml --dry-run
forgelm --config my_config.yaml --fit-check
forgelm --config my_config.yaml

# After training: chat, export to GGUF, generate a serving config
forgelm chat ./checkpoints/final_model
forgelm export ./checkpoints/final_model --output model-q4.gguf --quant q4_k_m
forgelm deploy ./checkpoints/final_model --target ollama
```

`python -m forgelm …` is equivalent and is what CI should use: a console script's `sys.path[0]` is its own `bin/`, so `forgelm …` runs whatever is in site-packages rather than the checkout you just built. `forgelm --wizard` generates a config interactively. Full walkthrough: [Quick Start Guide](https://github.com/HodeTech/ForgeLM/blob/main/docs/guides/quickstart.md).

---

## What comes out

```text
checkpoints/
├── final_model/
│   └── model_integrity.json          # SHA-256 of every artefact in this directory
├── compliance/
│   ├── annex_iv_metadata.json        # EU AI Act Annex IV technical documentation
│   ├── data_governance_report.json   # Article 10 — inlines the dataset audit report
│   ├── compliance_report.json        # Article 11 manifest
│   └── …                             # data_provenance.json, training_manifest.yaml
└── audit_log.jsonl                   # Article 12 — append-only, hash-chained
```

Every decision gate appends one line to `audit_log.jsonl`. Here is a real safety gate failing:

```json
{"timestamp": "2026-07-20T18:41:00.356441+00:00", "run_id": "fg-13f28267fe1c", "operator": "ci-runner@build-07", "event": "safety.evaluation_completed", "prev_hash": "f39b5678b9ccf3ebf259da457fdea175d4046f5d6479a6f9916cf0963a21246b", "passed": false, "safe_ratio": 0.91, "total_count": 200, "safety_score": 0.91, "categories": {"S1": 4, "S9": 14}}
```

`prev_hash` chains each line to the one before it. Every event is catalogued in the [Audit Event Catalog](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/audit_event_catalog.md).

---

## Exit codes

CI/CD branches on these. They are a public contract; any other value is clamped to 2 rather than leaking a signal-derived code.

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Config error — invalid YAML, bad path, failed schema validation |
| 2 | Training/runtime error |
| 3 | Evaluation gate failed — loss, benchmark, safety, judge, or a critical secrets finding |
| 4 | Awaiting human approval (Article 14 gate; the model is staged, not promoted) |
| 5 | Wizard cancelled before writing a config |
| 6 | Integrity failure — an artefact was read and its hash did not match |

1 and 6 are deliberately distinct: a mistyped path is an operator typo (1 — fix the command), whereas a hash that no longer matches is a security event (6 — page whoever owns the artefact).

---

## Compliance & safety

- **EU AI Act** — Annex IV technical documentation, Article 10 data governance, Article 12 audit log, Article 14 human-oversight staging gate (`forgelm approve`, exit 4).
- **GDPR** — `forgelm purge` (Article 17 erasure) and `forgelm reverse-pii` (Article 15 access).
- **Auto-revert** *(opt-in: `evaluation.auto_revert: true`, off by default)* — when a run breaches a loss, benchmark, safety, or judge threshold, the saved model directory is **deleted** and the failure is recorded in the audit log. Left off, a breach is logged and the model is kept. Withheld regardless of the setting when the safety evaluation produced no usable evidence (classifier load failure, unanswered probes) — an unread verdict fails the run but does not delete the model.
- **Model & log integrity** — a SHA-256 manifest per trained model (`forgelm verify-integrity`) detects changed, removed, or added artefacts, and `forgelm verify-audit` validates the audit-log hash chain (HMAC-authenticated when `FORGELM_AUDIT_SECRET` is set). Both exit **6** on mismatch. Neither is keyed unless you set `FORGELM_AUDIT_SECRET`: without it the manifest can be re-stamped by anyone who can write the model directory, and editing the audit log's *last* line leaves the chain self-consistent. Both catch corruption and accidental drift; for adversarial tamper-evidence, set the secret and pair with write-once storage.
- **Reproducibility** — five optional `revision` fields pin the base model, safety classifier, LLM judge, distillation teacher, and GRPO reward model to exact Hub commits (the tokenizer shares the base model's pin). A 40-hex SHA pins; a branch or tag is accepted with a warning, because upstream can repoint it.
- **Supply chain** — CycloneDX SBOM per release, nightly `pip-audit` + `bandit`, `gitleaks` pre-commit.

[Safety & Compliance Guide](https://github.com/HodeTech/ForgeLM/blob/main/docs/guides/safety_compliance.md) · [Deployer Audit Guide (ISO 27001 / SOC 2)](https://github.com/HodeTech/ForgeLM/blob/main/docs/guides/iso_soc2_deployer_guide.md) · [Supply-Chain Security](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/supply_chain_security.md)

---

## `forgelm audit` — before you spend a GPU-hour

One command, no model download, no training commitment. It scans a corpus for length and language distribution, near-duplicates (SimHash, optional MinHash LSH), cross-split leakage, quality flags, PII across 8 categories (email, phone, IBAN, credit card, and TR / DE / FR / US-SSN national IDs — with checksum validation on cards (Luhn), IBANs (mod-97) and TR IDs; the rest are shape-matched and deliberately over-report), and a 9-family secrets scan.

**The secrets gate exits 3.** This is the one that stops a build:

```console
$ forgelm audit ./corpus.jsonl
[ERROR] Secrets gate FAILED (critical): 2 credential/secret span(s) detected
(aws_access_key=1, github_token=1). Do not train on this corpus — a credential
in training data is memorised and re-emitted at inference time. Scrub it with
`forgelm ingest --secrets-mask`, or re-run `forgelm audit --allow-secrets` to
record the findings without failing the pipeline. Exiting 3.
```

Changed in v0.10.0 — this gate previously never fired. `--allow-secrets` is the documented escape hatch for auditing a corpus before scrubbing it, or for fixtures with known dummy credentials. `--croissant` embeds a Croissant 1.0 dataset card under the `croissant` key of `data_audit_report.json`; that report is inlined into `data_governance_report.json` at compliance-export time. [Dataset Audit Guide](https://github.com/HodeTech/ForgeLM/blob/main/docs/guides/data_audit.md)

---

## Training & deployment

| | |
|---|---|
| **Trainers** | 6 types: SFT, DPO, SimPO, KTO, ORPO, GRPO — one schema |
| **Memory** | 4-bit QLoRA, DoRA, PiSSA, rsLoRA, GaLore; on CUDA OOM the batch is halved and gradient accumulation doubled, preserving the effective batch, then the step retries |
| **Backends** | Transformers (default) or Unsloth — Linux + CUDA only, and incompatible with `lora.method: pissa` |
| **Scale** | DeepSpeed ZeRO-2/3, FSDP, multi-GPU, MoE-aware (Qwen3, Mixtral, DeepSeek); RoPE scaling, NEFTune, sliding-window attention, sequence packing |
| **Data in** | `forgelm ingest` turns PDF / DOCX / EPUB / TXT / Markdown into SFT-ready JSONL, with PII and secrets masking before chunks are written |
| **Evaluation** | `lm-evaluation-harness` benchmarks, LLM-as-judge, Llama Guard safety scoring with S1–S14 categories; `--fit-check` reports `FITS / TIGHT / OOM / UNKNOWN` before you allocate a GPU |
| **Out** | GGUF export (6 quant levels), Ollama / vLLM / TGI / HF Endpoints configs, model merging (TIES, DARE, SLERP, linear), auto-generated model cards, Slack / Teams webhooks, W&B / MLflow / TensorBoard |

**Stable Python API** — `from forgelm import ForgeTrainer, audit_dataset, verify_audit_log, …`; training, dataset audit, and the artefact verifiers have typed, semver-protected entry points.

Also here: multi-dataset mixing, and synthetic distillation from a teacher model into a smaller student.

**What it is not:** no web UI, no custom inference engine (hand off to Ollama, vLLM, TGI, llama.cpp), no custom architectures or quantization kernels, no pretraining. Fine-tuning, evaluation, and the evidence trail, only — backed by 123 test modules / 4,524 tests and 29 CI guards that fail the build on documentation and schema drift. **No telemetry:** ForgeLM makes no outbound call you did not configure.

---

## Install

```bash
pip install forgelm                     # core
pip install "forgelm[qlora]"            # 4-bit quantization (Linux)
pip install "forgelm[ingestion]"        # PDF / DOCX / EPUB / Markdown

# From source, for contributors
git clone https://github.com/HodeTech/ForgeLM.git && cd ForgeLM && pip install -e .
```

**Prerequisites:** Python 3.10+, `torch>=2.4.0` (required by `transformers>=5.3.0`). Intel Macs (x86_64) are not supported — PyPI has no `torch>=2.4` wheel for that platform. Heavy backends ship as optional extras; the [installation guide](https://github.com/HodeTech/ForgeLM/blob/main/docs/usermanuals/en/getting-started/installation.md) lists each one with its platform constraints.[^1]

Docker — no image is published, so build it locally first (`ENTRYPOINT` is `forgelm`):

```bash
docker build -t forgelm --build-arg INSTALL_EVAL=true .
docker run --gpus all -v $(pwd)/my_config.yaml:/workspace/config.yaml \
  -v $(pwd)/output:/workspace/output forgelm --config /workspace/config.yaml
```

Multi-GPU and air-gapped patterns: [Enterprise Deployment Guide](https://github.com/HodeTech/ForgeLM/blob/main/docs/guides/enterprise_deployment.md).

---

## Documentation & notebooks

[Quick Start](https://github.com/HodeTech/ForgeLM/blob/main/docs/guides/quickstart.md) · [Configuration Reference](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/configuration.md) · [Architecture](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/architecture.md) · [Safety & Compliance](https://github.com/HodeTech/ForgeLM/blob/main/docs/guides/safety_compliance.md) · [Troubleshooting & FAQ](https://github.com/HodeTech/ForgeLM/blob/main/docs/guides/troubleshooting.md) · [all guides](https://github.com/HodeTech/ForgeLM/tree/main/docs/guides) · [roadmap](https://github.com/HodeTech/ForgeLM/blob/main/docs/roadmap.md)

**Türkçe** — 15 of the 16 guides have a Turkish mirror: [Hızlı Başlangıç](https://github.com/HodeTech/ForgeLM/blob/main/docs/guides/quickstart-tr.md) · [Konfigürasyon Referansı](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/configuration-tr.md) · [Güvenlik ve Uyumluluk](https://github.com/HodeTech/ForgeLM/blob/main/docs/guides/safety_compliance-tr.md) · [Sorun Giderme](https://github.com/HodeTech/ForgeLM/blob/main/docs/guides/troubleshooting-tr.md)

- [Quick Start — SFT Fine-Tuning](https://github.com/HodeTech/ForgeLM/blob/main/notebooks/quickstart_sft.ipynb) [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/HodeTech/ForgeLM/blob/main/notebooks/quickstart_sft.ipynb)
- [GRPO Reasoning RL](https://github.com/HodeTech/ForgeLM/blob/main/notebooks/grpo_reasoning.ipynb) [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/HodeTech/ForgeLM/blob/main/notebooks/grpo_reasoning.ipynb)
- [Safety Evaluation & Red-Teaming](https://github.com/HodeTech/ForgeLM/blob/main/notebooks/safety_evaluation.ipynb) [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/HodeTech/ForgeLM/blob/main/notebooks/safety_evaluation.ipynb)

The first two run on a free Colab T4. The safety notebook needs a gated Llama-Guard-3-8B licence (`HF_TOKEN`) and more VRAM than a free T4 provides. [11 notebooks in total](https://github.com/HodeTech/ForgeLM/tree/main/notebooks) (DPO, KTO, multi-dataset, GaLore, synthetic data, data curation, post-training workflow).

---

## Contributing & license

Start with [CONTRIBUTING.md](https://github.com/HodeTech/ForgeLM/blob/main/CONTRIBUTING.md) and the engineering standards in [docs/standards/](https://github.com/HodeTech/ForgeLM/tree/main/docs/standards). Licensed under [Apache 2.0](https://github.com/HodeTech/ForgeLM/blob/main/LICENSE).

[^1]: `qlora` and `unsloth` pin their upstream wheels behind a `sys_platform == 'linux'` marker, so on macOS and Windows the install succeeds and those backends are simply absent. `export` skips `llama-cpp-python` on Windows via a `sys_platform != 'win32'` marker. `distributed` carries **no** marker and DeepSpeed publishes no Windows wheels, so `pip install "forgelm[distributed]"` on Windows attempts a source build and typically fails outright rather than degrading gracefully.
