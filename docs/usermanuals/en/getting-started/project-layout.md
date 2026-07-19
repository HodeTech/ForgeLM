---
title: Project Layout
description: How ForgeLM organises configs, data, checkpoints, and artifacts on disk.
---

# Project Layout

ForgeLM is opinionated about *where* files go. This makes runs reproducible, CI/CD pipelines predictable, and audit reviewers happy. You don't have to follow this layout exactly — but every override has a flag, and the defaults assume the conventions below.

## A typical project tree

```text
my-finetune/
├── configs/                       — YAML configuration files
│   ├── customer-support.yaml      — your trainer config
│   └── customer-support.dev.yaml  — fast variant for iteration
├── data/                          — JSONL datasets
│   ├── train.jsonl
│   ├── validation.jsonl
│   └── preferences.jsonl          — DPO/SimPO chosen-rejected pairs
├── audit/                         — output of `forgelm audit`
│   └── data_audit_report.json
├── checkpoints/                   — model outputs (gitignored)
│   └── customer-support/
│       ├── adapter_model.safetensors
│       ├── README.md              — model card (Article 13)
│       ├── config_snapshot.yaml   — frozen copy of the config used
│       └── artifacts/             — compliance evidence bundle
├── ingested/                      — raw documents → JSONL via `forgelm ingest`
└── .forgelm/                      — local cache (also gitignored)
```

## Where each command writes

| Command | Writes to | Notes |
|---|---|---|
| `forgelm ingest` | `--output` (typically `data/*.jsonl`) | Raw docs → SFT-ready JSONL. |
| `forgelm audit` | `--output` (typically `audit/`) | PII / leakage / quality report. |
| `forgelm --config X.yaml` | `training.output_dir` from YAML | Full training artifacts. |
| `forgelm export` | `--output` (path to `.gguf`) | Quantised single-file model. |
| `forgelm deploy` | `--output` (Modelfile, K8s manifest, etc.) | Deployment scaffolds. |
| `forgelm chat` | nothing (interactive) | Streams to terminal. |

## What to commit, what to gitignore

:::tip
ForgeLM's defaults play well with version control if you commit the right things and ignore the rest.
:::

**Commit:**
- `configs/*.yaml` — your runs are configurations; the YAML is the source of truth.
- `audit/*.json` — small, machine-readable, useful in PR reviews ("did the audit numbers regress?").
- `checkpoints/*/README.md` and `checkpoints/*/artifacts/` (model card and audit bundle, both small).

**Gitignore:**
- `data/` — usually too large; track via DVC or your preferred dataset registry instead.
- `checkpoints/*/adapter_model.safetensors` and other weight files — too large for git, push to HuggingFace Hub or model registry.
- `ingested/` — re-buildable from raw documents.
- `.forgelm/` — local cache.

A starter `.gitignore`:

```gitignore
# ForgeLM defaults
checkpoints/*/adapter_model.safetensors
checkpoints/*/*.safetensors
checkpoints/*/pytorch_model.bin
checkpoints/*/optimizer.pt
data/
ingested/
.forgelm/
```

## Repository conventions

ForgeLM uses these path conventions — change them with command-line flags but keep them consistent across a project:

| Convention | Default | Override |
|---|---|---|
| Config file | `configs/<name>.yaml` | `--config PATH` |
| Audit output | `./audit/` | `forgelm audit --output PATH` |
| Training output | `./checkpoints/<name>/` | `training.output_dir:` in YAML |
| HuggingFace cache | `~/.cache/huggingface/` | `HF_HOME` env var (canonical; ForgeLM does not introduce its own cache-dir override) |
| HuggingFace token | env `HUGGINGFACE_TOKEN` | `auth.hf_token:` in YAML |

## Multi-config workflows

Most teams keep two YAMLs per project — a fast "dev" config (1 epoch, small subset, no safety eval) for rapid iteration and a "prod" config that enables every gate:

```text
configs/
├── customer-support.yaml          — full run (use this for releases)
└── customer-support.dev.yaml      — dev iteration (faster)
```

ForgeLM YAML does **not** support a `extends:` inheritance key; each config is a self-contained `ForgeConfig`. To avoid duplication between prod and dev configs, use a YAML anchor (`&base` / `<<: *base`) in a single file, or templated diff via your CI:

```yaml
# customer-support.dev.yaml — full self-contained config (the dev variant)
model:
  name_or_path: "Qwen/Qwen2.5-7B-Instruct"
  max_length: 2048

lora:
  r: 8
  alpha: 16
  target_modules: ["q_proj", "v_proj"]

training:
  trainer_type: "sft"
  num_train_epochs: 1                    # was 3 in prod
  max_steps: 200                          # cap iterations
  per_device_train_batch_size: 1
  output_dir: "./checkpoints/customer-support.dev"

data:
  dataset_name_or_path: "data/dev/100rows.jsonl"

evaluation:
  benchmark: { enabled: false }
  safety:    { enabled: false }
```

Run the dev variant during development, the full one for release. CI runs the full one on every merge to main.

## Where compliance artifacts go

Every successful (or failed) training run creates a `checkpoints/<name>/artifacts/` directory containing the evidence bundle described in [Compliance Overview](#/compliance/overview). This is the directory you hand to a regulator or pin in your CI artifacts.

:::warn
**Don't merge the artifacts/ directory across runs.** Each run is a separate evidence bundle; mixing them breaks the SHA-256 manifest and undermines tamper-evidence.
:::

## See also

- [Configuration Reference](#/reference/configuration) — every field that controls these paths.
- [CI/CD Pipelines](#/operations/cicd) — how to wire this layout into GitHub Actions.
- [Audit Log](#/compliance/audit-log) — how the artifacts directory is used.
