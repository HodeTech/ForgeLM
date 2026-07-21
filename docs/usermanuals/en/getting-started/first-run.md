---
title: Your First Run
description: Run your first ForgeLM training job in 5 minutes — install, validate, train, evaluate.
---

# Your First Run

This page walks you through a complete training run, from a fresh `pip install` to a finished checkpoint with audit artifacts. Allow ~5 minutes of reading and ~30 minutes of GPU time.

```mermaid
flowchart LR
    A[pip install] --> B[forgelm doctor]
    B --> C[forgelm quickstart]
    C --> D[--dry-run]
    D --> E[--fit-check]
    E --> F[Training]
    F --> G[Eval + Annex IV]
    G --> H[forgelm chat]
    classDef setup fill:#1c2030,stroke:#0ea5e9,color:#e6e7ec
    classDef validate fill:#1c2030,stroke:#eab308,color:#e6e7ec
    classDef run fill:#1c2030,stroke:#22c55e,color:#e6e7ec
    class A,B,C setup
    class D,E validate
    class F,G,H run
```

## 1. Verify your environment

Before anything else, run `forgelm doctor` to check that Python, PyTorch, CUDA, and any optional dependencies are wired up correctly:

```shell
$ forgelm doctor
forgelm doctor — environment check

  [✓ pass] python.version          Python 3.11.4 (CPython).
  [✓ pass] torch.cuda              torch 2.4.0 with CUDA 12.4.
  [✓ pass] gpu.inventory           1 GPU(s) — GPU0: NVIDIA RTX 4090 (24.0 GiB).
  [✓ pass] extras.qlora            Installed (module bitsandbytes, purpose: 4-bit / 8-bit QLoRA training (Linux GPUs only)).
  [✓ pass] extras.unsloth          Installed (module unsloth, purpose: Unsloth-accelerated training (Linux GPUs only)).
  [! warn] extras.distributed      Optional extra missing — install with: pip install 'forgelm[distributed]' (purpose: DeepSpeed ZeRO + offload distributed training (Linux + macOS only)).
  [✓ pass] hf_hub.reachable        HuggingFace Hub reachable (HTTP 200).
  [✓ pass] disk.workspace          Workspace /home/me/forgelm — 387.0 GiB free of 500.0 GiB.
  [! warn] operator.identity       FORGELM_OPERATOR not set; audit events will fall back to 'me@workstation'. Pin FORGELM_OPERATOR=<id> for CI / pipeline runs.

Summary: 7 pass, 2 warn, 0 fail.
```

`--output-format json` returns a structured envelope (`{"success": bool, "checks": [...], "summary": {...}}`) so CI can filter on individual probe results without parsing the table. Pass `--offline` to skip the HF Hub network probe and inspect the local cache instead — useful for air-gapped deployments.

:::tip
If `forgelm doctor` reports a problem (missing CUDA, version mismatch, no GPU), fix that first. Every other ForgeLM command will fail in confusing ways otherwise. See [Troubleshooting](#/operations/troubleshooting).
:::

## 2. Pick a bundled template

ForgeLM ships with five starter templates that cover most real-world fine-tuning scenarios. List them:

```shell
$ forgelm quickstart --list
Available quickstart templates:

  customer-support
    Customer Support Assistant — Polite, brand-safe support replies. SFT on a tiny seed FAQ dataset.
    primary=Qwen/Qwen2.5-7B-Instruct  fallback=HuggingFaceTB/SmolLM2-1.7B-Instruct  trainer=sft  ~15min  [x] bundled data

  code-assistant
    Code Assistant — Short code-question Q&A. SFT on a curated programming seed set.
    primary=Qwen/Qwen2.5-Coder-7B-Instruct  fallback=Qwen/Qwen2.5-Coder-1.5B-Instruct  trainer=sft  ~25min  [x] bundled data

  domain-expert
    Domain Expert (BYOD — bring your own docs) — Empty data — pair with `forgelm ingest` (Phase 11) or a custom JSONL.
    primary=Qwen/Qwen2.5-7B-Instruct  fallback=HuggingFaceTB/SmolLM2-1.7B-Instruct  trainer=sft  ~20min  [ ] user-supplied data

  medical-qa-tr
    Medical Q&A (Türkçe / Turkish) — Turkish medical-question SFT seed. Disclaimers baked in; not clinical advice.
    primary=Qwen/Qwen2.5-7B-Instruct  fallback=Qwen/Qwen2.5-1.5B-Instruct  trainer=sft  ~15min  [x] bundled data

  grpo-math
    Math Reasoning via GRPO — Group Relative Policy Optimization on grade-school math problems.
    primary=Qwen/Qwen2.5-Math-7B-Instruct  fallback=Qwen/Qwen2.5-Math-1.5B-Instruct  trainer=grpo  ~45min  [x] bundled data
```

For your first run, pick `customer-support` — it's the smallest and finishes in roughly 15 minutes on the primary model. It is an **SFT-only** run: four of the five templates use the `sft` trainer, and only `grpo-math` uses a different one (`grpo`). No bundled template runs a preference stage such as DPO or ORPO.

Pass `--output` so the config lands at a stable, predictable path — the default is a timestamped directory (`./configs/<template>-<timestamp>/config.yaml`), which is fine but changes on every invocation:

```shell
$ forgelm quickstart customer-support --output configs/quickstart-customer-support.yaml
Template       : customer-support — Customer Support Assistant
Model          : Qwen/Qwen2.5-7B-Instruct
Dataset        : configs/customer-support.jsonl
Generated YAML : configs/quickstart-customer-support.yaml
Trainer        : sft
Est. wall-clock: ~15 minutes on primary model
Note           : copied seed dataset to configs/customer-support.jsonl
```

On a machine with no GPU, quickstart automatically substitutes the smaller fallback model and says so on a `Selection :` line.

The generated YAML is yours to keep, edit, and version-control. Open it.

## 3. Validate the config (`--dry-run`)

Always validate before training. `--dry-run` parses your YAML, checks every referenced file exists, downloads metadata for the model and tokenizer, and reports any structural problems — without using a single GPU second:

```shell
$ forgelm --config configs/quickstart-customer-support.yaml --dry-run
✓ config validates
✓ datasets reachable
✓ tokenizer downloadable
✓ output directory writable
```

:::warn
A failing `--dry-run` is a configuration problem, not a training problem. Fix it before going further. Most "training crashed at step 0" reports trace back to skipped dry-runs.
:::

## 4. Estimate VRAM (`--fit-check`)

Different models, different `max_length`, different LoRA ranks all change peak memory. `--fit-check` runs a static analysis and reports whether your job will fit:

```shell
$ forgelm --config configs/quickstart-customer-support.yaml --fit-check
FITS  est. peak 11.4 GB / 12 GB available
```

Possible verdicts:

| Verdict | Meaning |
|---|---|
| `FITS` | Comfortably within VRAM budget. Proceed. |
| `TIGHT` | Within budget but no headroom for activation bursts. Reduce `max_length` or batch size. |
| `OOM` | Will not fit. Suggested fixes printed (e.g. enable QLoRA, lower batch size). |
| `UNKNOWN` | Architecture not in the GPU profile database — train conservatively or report. |

## 5. Train

```shell
$ forgelm --config configs/quickstart-customer-support.yaml
[2026-04-28 14:01:32] config validated
[2026-04-28 14:01:33] auditing data/customer-support.jsonl (12,400 rows, 3 splits)
[2026-04-28 14:01:35] PII flags: 0 critical, 5 medium · cross-split overlap: 0
[2026-04-28 14:01:37] SFT epoch 1/3 · loss=2.31 → 1.42
[2026-04-28 14:18:55] DPO preference pass · β=0.1 · KL=4.2
[2026-04-28 14:32:11] benchmark hellaswag=0.62 truthfulqa=0.48
[2026-04-28 14:33:02] Llama Guard S1-S14: clean
[2026-04-28 14:33:04] Annex IV → checkpoints/customer-support/artifacts/annex_iv_metadata.json
[2026-04-28 14:33:04] ✔ finished, exit 0
```

## 6. Try the model

```shell
$ forgelm chat ./checkpoints/customer-support
forgelm> how do I cancel my subscription?
You can cancel from Settings → Billing → Cancel subscription. Your access
continues until the end of the current billing period…
```

## What you got on disk

```text
checkpoints/customer-support/
├── artifacts/
│   ├── annex_iv_metadata.json              ← Article 11 technical documentation
│   ├── audit_log.jsonl            ← Article 12 append-only event log
│   ├── data_audit_report.json     ← Article 10 data governance evidence
│   ├── safety_report.json         ← Llama Guard verdict
│   ├── benchmark_results.json     ← Per-task accuracy
│   └── manifest.json              ← SHA-256 over every artifact
├── README.md                      ← Article 13 model card
├── config_snapshot.yaml           ← The exact YAML this run used
└── adapter_model.safetensors      ← LoRA weights (or merged checkpoint)
```

That `artifacts/` directory is the deliverable for compliance reviews. Every file inside is hashed in `manifest.json` for tamper-evidence.

## Next steps

- [Project Layout](#/getting-started/project-layout) — where ForgeLM puts things and why.
- [Choosing a Trainer](#/concepts/choosing-trainer) — moving past `customer-support` to your own use case.
- [Configuration Reference](#/reference/configuration) — every YAML field, in detail.
