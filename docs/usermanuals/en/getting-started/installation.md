---
title: Installation
description: Install ForgeLM from PyPI, with optional extras for ingestion, scale, and export.
---

# Installation

ForgeLM ships as a single PyPI package with optional dependency groups (called *extras*) for the heavier features. Most users start with the base install and add extras as needed.

## Requirements

| Requirement | Minimum | Recommended |
|---|---|---|
| Python | 3.10 | 3.11+ |
| OS | Linux, macOS | Linux for GPU training |
| RAM | 8 GB | 16 GB+ |
| Disk | 5 GB free | 50 GB+ for model caches |
| GPU (training) | None for ingest/audit | Single 12 GB CUDA GPU minimum for SFT 7B |

:::note
**No GPU?** You can still use ForgeLM for ingestion, audit, evaluation prep, and deployment config generation — every CPU-only workflow runs on the same `forgelm` command.
:::

## Base install

```shell
$ pip install forgelm
```

This gives you the trainer, all six alignment paradigms, evaluation, safety scoring, compliance artifact generation, and the CLI. It's enough to fine-tune a model end-to-end if your data is already in JSONL format.

Verify the install:

```shell
$ forgelm --version
$ forgelm --help
```

## Optional extras

ForgeLM splits heavy or rarely-needed dependencies into extras so you don't pull them in unless you need them.

### Document ingestion (`[ingestion]`)

```shell
$ pip install 'forgelm[ingestion]'
```

Adds support for ingesting PDF, DOCX, EPUB, TXT, and Markdown files into SFT-ready JSONL via `forgelm ingest`. Pulls in `pypdf`, `python-docx`, `ebooklib`, and a few smaller text-handling libraries.

### Large-corpus deduplication (`[ingestion-scale]`)

```shell
$ pip install 'forgelm[ingestion-scale]'
```

Adds MinHash LSH (via `datasketch`) for near-duplicate detection on corpora bigger than ~50 K rows. The default simhash-based detector is fast and exact-recall, but MinHash scales to millions of rows. Pull this in only if you need the scale.

### GGUF export (`[export]`)

```shell
$ pip install 'forgelm[export]'
```

Adds quantized GGUF export for local inference (Ollama, llama.cpp). Pulls in the `gguf` writer and supporting libraries. Optional because not every workflow ends in GGUF — many users hand off to vLLM or TGI directly.

### Distributed training (`[distributed]`)

```shell
$ pip install 'forgelm[distributed]'
```

Adds DeepSpeed ZeRO-2 / ZeRO-3 support for multi-GPU training. Required only if you're training a model larger than fits on a single GPU. (The extra name is `distributed`; the actual dep it pulls is `deepspeed>=0.14.0`.)

### Combining extras

`pyproject.toml` does not define an `[all]` aggregate. Combine the extras you actually need by listing them comma-separated:

```shell
$ pip install 'forgelm[qlora,eval,tracking,merging,export,ingestion]'
```

This is intentional — pulling DeepSpeed / bitsandbytes / Presidio / sentence-transformers all at once on a CPU-only laptop is rarely what an operator wants, so we keep the choice explicit.

## Container install

If you'd rather not install Python dependencies on your host, the official Docker image bundles ForgeLM with all extras:

```shell
$ docker pull ghcr.io/hodetech/forgelm:latest
$ docker run --gpus all -v $PWD:/workspace ghcr.io/hodetech/forgelm:latest \
    forgelm --config /workspace/configs/run.yaml
```

A `docker-compose.yml` is also published; see [Docker Operations](#/operations/docker) for the multi-service pattern (training + experiment tracking + webhook receiver).

## Verifying GPU access

If you've installed ForgeLM for GPU training, confirm CUDA is wired up correctly:

```shell
$ forgelm doctor
```

`forgelm doctor` reports a tabular pass / warn / fail diagnostic over:
- Python version (>=3.10 floor; >=3.11 recommended)
- torch + CUDA availability (CPU-only is a `warn`, not a `fail`)
- GPU inventory (per-device VRAM in GiB)
- Optional extras: `qlora`, `unsloth`, `distributed`, `eval`, `tracking`, `merging`, `export`, `ingestion`, `ingestion-pii-ml`, `ingestion-scale` — missing extras `warn` with the exact `pip install 'forgelm[<name>]'` hint
- HuggingFace Hub reachability (via `HF_ENDPOINT` if set; skipped under `--offline`)
- Workspace disk space (<10 GiB → `fail`, <50 GiB → `warn`)
- `FORGELM_OPERATOR` audit-identity hint (Article 12)

Pass `--output-format json` for a structured envelope (`{"success": bool, "checks": [...], "summary": {pass, warn, fail}}`); pass `--offline` for air-gap mode (skips the network probe and instead inspects the local HF cache).

:::tip
Run `forgelm doctor` *before* `forgelm --config ...` for any new environment. It catches missing CUDA libraries, version mismatches, and GPU-not-found errors in two seconds rather than two hours into training.
:::

## Common installation issues

:::warn
**`bitsandbytes` import error on macOS / Apple Silicon.** `bitsandbytes` does not currently support Metal/MPS. On macOS, ForgeLM falls back to full-precision training. For 4-bit quantized training (QLoRA), you need a Linux host with a CUDA GPU.
:::

:::warn
**`undefined symbol: __cudaRegisterFatBinaryEnd`.** Your PyTorch and CUDA toolkit versions are mismatched. Reinstall PyTorch matching your CUDA version: `pip install torch --index-url https://download.pytorch.org/whl/cu121` (replace `cu121` with your CUDA version).
:::

:::warn
**`OSError: HuggingFace token not found`.** Some models (e.g. Llama 3, Llama Guard) require a HuggingFace access token. Set it via `huggingface-cli login` or the `HF_TOKEN` environment variable. See [CLI Reference](#/reference/cli) for all auth env vars.
:::

## Next steps

Now that ForgeLM is installed, head to [Your First Run](#/getting-started/first-run) for an end-to-end training walkthrough — you'll have a fine-tuned model in about 5 minutes of reading and 30 minutes of GPU time.
