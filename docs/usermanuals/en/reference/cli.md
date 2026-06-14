---
title: CLI Reference
description: Every forgelm subcommand and flag, with auth setup and common patterns.
---

# CLI Reference

ForgeLM ships a single `forgelm` binary with subcommands. This page is the canonical reference; for tutorial-level guidance, see [Your First Run](#/getting-started/first-run).

## Top-level subcommands

| Command | What it does |
|---|---|
| `forgelm` (no subcommand) | Train (with `--config`). |
| `forgelm doctor` | Environment check — Python, CUDA, GPU, deps, HF cache. |
| `forgelm quickstart` | List or instantiate bundled templates. |
| `forgelm ingest` | PDF/DOCX/EPUB → JSONL conversion. |
| `forgelm audit` | Pre-train data audit (PII / secrets / dedup / leakage / quality). |
| `forgelm chat` | Interactive REPL. |
| `forgelm export` | GGUF export with quantisation. |
| `forgelm deploy` | Generate deployment config (Ollama, vLLM, TGI, HF Endpoints). |
| `forgelm verify-audit` | Validate audit log chain (timestamps, prev_hash, HMAC). |
| `forgelm verify-annex-iv` | Verify an exported Annex IV artefact (§1-9 fields + manifest hash). |
| `forgelm verify-gguf` | Verify GGUF model file integrity (magic header + metadata + SHA-256 sidecar). |
| `forgelm approve` | Sign a human approval request and promote `final_model.staging/`. |
| `forgelm reject` | Reject a human approval request and discard staging. |
| `forgelm approvals` | List pending approvals (`--pending`) or inspect one (`--show RUN_ID`). |
| `forgelm purge` | GDPR Article 17 erasure: row-id, run-id, or `--check-policy` retention report. |
| `forgelm reverse-pii` | GDPR Article 15 right-of-access: search masked corpora for a subject's identifier (plaintext or hash-mask scan). |
| `forgelm cache-models` | Air-gap workflow: pre-populate the HuggingFace Hub cache for one or more models. |
| `forgelm cache-tasks` | Air-gap workflow: pre-populate the lm-eval task dataset cache (requires `[eval]` extra). |
| `forgelm safety-eval` | Standalone safety evaluation against a model checkpoint (Llama Guard by default). |

Run `forgelm <subcommand> --help` for any of these.

## Top-level flags (training mode — used with `--config`)

| Flag | Description |
|---|---|
| `--config PATH` | YAML config file path. Required for training. |
| `--wizard` | Launch interactive configuration wizard to generate a `config.yaml`. |
| `--wizard-start-from PATH` | Pre-populate the wizard from an existing YAML so each step's prompts default to the operator's prior answers (idempotent re-run). Combine with `--wizard`. |
| `--dry-run` | Validate configuration and check model/dataset access; no training. |
| `--fit-check` | Estimate peak training VRAM; no model load. Requires `--config`. |
| `--resume [PATH]` | Resume training. Bare `--resume` auto-detects last checkpoint; `--resume PATH` resumes from a specific one. |
| `--offline` | Air-gapped mode: disable all HF Hub network calls. Models and datasets must be available locally. |
| `--benchmark-only MODEL_PATH` | Run benchmark evaluation on an existing model without training. Requires `evaluation.benchmark` config. |
| `--merge` | Run model merging from the `merge:` config block. No training. |
| `--generate-data` | Generate synthetic training data using the teacher model. No training. |
| `--compliance-export OUTPUT_DIR` | Export EU AI Act compliance artifacts (audit trail, data provenance, Annex IV) to OUTPUT_DIR. Run after training so the manifest is complete. |
| `--data-audit PATH` | **Deprecated alias** for `forgelm audit PATH`. Scheduled for removal in v0.8.0. New scripts should use the subcommand. |
| `--output DIR` | Output directory for `--data-audit` / `--compliance-export` (default: `./audit/` or `./compliance/`). |
| `--output-format {text,json}` | Output format for results (default: `text`). JSON for CI. |
| `--quiet, -q` | Suppress INFO logs. Only show warnings and errors. |
| `--log-level {DEBUG,INFO,WARNING,ERROR}` | Set logging verbosity (default: INFO). |
| `--version` | Print version. |
| `--help, -h` | Show help. |

## Training: `forgelm`

Most-used patterns:

```shell
$ forgelm --config configs/run.yaml --dry-run        # validate
$ forgelm --config configs/run.yaml --fit-check      # VRAM check
$ forgelm --config configs/run.yaml                  # train
$ forgelm --config configs/run.yaml --resume         # resume auto-detected last checkpoint
$ forgelm --config configs/run.yaml --resume /path   # resume from a specific checkpoint
$ forgelm --config configs/run.yaml --merge          # run as a merge job
$ forgelm --config configs/run.yaml --generate-data  # synthetic data only
```

## Doctor: `forgelm doctor`

```shell
$ forgelm doctor                                     # full env check
$ forgelm doctor --offline                           # air-gap variant: cache + offline-env probes
$ forgelm doctor --output-format json | jq .         # CI-friendly envelope
```

Probes Python version, torch / CUDA / GPU, optional extras, HF Hub reachability (or HF cache when `--offline`), disk space, operator identity, and audit-secret configuration. Exit codes: `0` = all pass (warnings OK), `1` = at least one fail, `2` = a probe itself crashed.

## Audit: `forgelm audit`

```shell
$ forgelm audit INPUT_PATH \
    [--output DIR] \
    [--verbose] \
    [--near-dup-threshold N] \
    [--dedup-method {simhash,minhash}] \
    [--jaccard-threshold X] \
    [--quality-filter] \
    [--croissant] \
    [--pii-ml] [--pii-ml-language LANG] \
    [--workers N] \
    [--output-format {text,json}]
```

`--workers N` parallelises split-level processing; the on-disk JSON is byte-identical across worker counts (modulo the `generated_at` timestamp). The full per-flag table — including the canonical authoritative list synced to `forgelm/cli/_parser.py::_add_audit_subcommand` — lives in [Dataset Audit](#/data/audit). Earlier drafts of this page documented `--strict`, `--skip-pii`, `--skip-secrets`, `--skip-quality`, `--skip-leakage`, `--remove-duplicates`, `--remove-cross-split-overlap`, `--output-clean`, `--show-leakage`, `--minhash-jaccard`, `--minhash-num-perm`, `--dedup-algo`, `--dedup-threshold`, `--sample-rate`, and `--add-row-ids` — none exist in the parser. Use the canonical names above.

## Ingest: `forgelm ingest`

```shell
$ forgelm ingest INPUT_PATH \
    --output PATH.jsonl \
    [--recursive] \
    [--strategy {sliding,paragraph,markdown}] \
    [--chunk-size N] [--overlap N] \
    [--chunk-tokens N] [--overlap-tokens N] [--tokenizer MODEL_NAME] \
    [--pii-mask] [--secrets-mask] [--all-mask] \
    [--language-hint LANG] [--script-sanity-threshold X] \
    [--normalise-profile {turkish,none} | --no-normalise-unicode] \
    [--no-quality-presignal] \
    [--epub-no-skip-frontmatter] [--keep-md-frontmatter] \
    [--strip-pattern REGEX ...] [--strip-pattern-no-timeout] \
    [--page-range START-END] [--keep-frontmatter] \
    [--strip-urls {keep,mask,strip}] \
    [--output-format {text,json}]
```

Pass `--output-format json` to get the machine-readable envelope
described in [JSON Output Contract](#/reference/json-output) — useful
for CI gates that branch on chunk count / files-processed without
parsing the text summary. Phase 15 (v0.6.0) added the
`--language-hint`, `--script-sanity-threshold`, `--normalise-profile`,
`--no-normalise-unicode`, `--no-quality-presignal`,
`--epub-no-skip-frontmatter`, `--keep-md-frontmatter`, `--strip-pattern`,
`--strip-pattern-no-timeout`, `--page-range`, `--keep-frontmatter`, and
`--strip-urls` flags. See [Document Ingestion](#/data/ingestion).

## Chat: `forgelm chat`

```shell
$ forgelm chat MODEL_PATH \
    [--adapter PATH] \
    [--system "system prompt"] \
    [--temperature 0.7] [--max-new-tokens 512] [--no-stream] \
    [--load-in-4bit | --load-in-8bit] \
    [--trust-remote-code] \
    [--backend {transformers,unsloth}]
```

Slash commands within the REPL: `/reset`, `/save [file]`, `/temperature N`, `/system [prompt]`, `/help` (alias `/?`), `/exit` (alias `/quit`). See [Interactive Chat](#/deployment/chat).

## Export: `forgelm export`

```shell
$ forgelm export CHECKPOINT_DIR \
    --output PATH.gguf \
    --quant {q2_k,q3_k_m,q4_k_m,q5_k_m,q8_0,f16} \
    [--adapter PATH] \
    [--no-integrity-update]
```

Comma-separate `--quant` for multiple levels in one command. See [GGUF Export](#/deployment/gguf-export).

## Deploy: `forgelm deploy`

```shell
$ forgelm deploy MODEL_PATH \
    --target {ollama,vllm,tgi,hf-endpoints} \
    [--output PATH] \
    [--system "PROMPT"]                              # Ollama only
    [--max-length 4096] \
    [--gpu-memory-utilization 0.90]                  # vLLM
    [--port 8080]                                    # TGI
    [--trust-remote-code]                            # vLLM
    [--vendor aws]                                   # HF Endpoints
```

See [Deploy Targets](#/deployment/deploy-targets).

## Approvals: `forgelm approvals` / `forgelm approve` / `forgelm reject`

```shell
$ forgelm approvals --pending                        # list pending approval gates
$ forgelm approvals --show RUN_ID                    # inspect a specific run's chain + staging
$ forgelm approve  RUN_ID --comment "Reviewed by N." # promote final_model.staging/ → final_model/
$ forgelm reject   RUN_ID --comment "Reason ..."     # discard staging
```

See [Human Oversight Gate](#/compliance/human-oversight). Exit codes: `0` = pending list / approval recorded, `1` = unknown run_id / config error, `4` (training mode only) = awaiting approval.

## Verify audit log: `forgelm verify-audit`

```shell
$ forgelm verify-audit PATH/TO/audit_log.jsonl
$ forgelm verify-audit PATH/TO/audit_log.jsonl --hmac-secret-env FORGELM_AUDIT_SECRET
$ forgelm verify-audit PATH/TO/audit_log.jsonl --require-hmac
```

Validates monotonic timestamps, `prev_hash` chain integrity, `seq` gap detection, and (when configured) HMAC signatures. Exit `0` on a valid chain; non-zero with a structured error envelope on tamper detection.

## Authentication

ForgeLM picks up credentials from environment variables. Never put them in YAML.

| Provider | Env var | Used for |
|---|---|---|
| HuggingFace | `HF_TOKEN` (alias: `HUGGINGFACE_TOKEN`) | Gated models (Llama, Llama Guard) |
| OpenAI | `OPENAI_API_KEY` | LLM-as-judge, synthetic data |
| Anthropic | `ANTHROPIC_API_KEY` | LLM-as-judge, synthetic data |
| W&B | `WANDB_API_KEY` | Experiment tracking |
| Cohere | `COHERE_API_KEY` | (synthetic data) |

YAML interpolation:

```yaml
auth:
  hf_token: "${HF_TOKEN}"
synthetic:
  teacher:
    api_key: "${OPENAI_API_KEY}"
```

If the env var isn't set, ForgeLM fails at config load with a clear error — better than crashing 6 hours into training because of a missing token.

## Exit codes

| Exit | Meaning |
|---|---|
| 0 | Success |
| 1 | Config / argument error |
| 2 | Audit warnings (with `--strict`) / probe crash (`forgelm doctor`) |
| 3 | Auto-revert / regression |
| 4 | Awaiting human approval (training pipeline) |
| 5 | Wizard cancelled (operator declined to save / non-tty refusal) |
| 130 | User interrupted (Ctrl+C) |

See [Exit Codes](#/reference/exit-codes) for the full contract.

## Environment variables

| Variable | What it sets |
|---|---|
| `HF_TOKEN` / `HUGGINGFACE_TOKEN` | HuggingFace authentication |
| `HF_HOME` | HuggingFace cache root (default `~/.cache/huggingface`) |
| `HF_HUB_CACHE` | Override the HF Hub cache directory specifically (precedence: `HF_HUB_CACHE` > `HF_HOME/hub` > default) |
| `HF_HUB_OFFLINE=1` | Disable HF Hub network calls |
| `HF_ENDPOINT` | HF Hub endpoint override (for self-hosted mirrors); honoured by `forgelm doctor` |
| `TRANSFORMERS_OFFLINE=1` | Disable transformers library network calls |
| `HF_DATASETS_OFFLINE=1` | Disable datasets library network calls |
| `FORGELM_OPERATOR` | Operator identity recorded in audit events (overrides `getpass.getuser()@hostname`) |
| `FORGELM_ALLOW_ANONYMOUS_OPERATOR` | When `1`, permit the audit log to record an anonymous operator (otherwise an unresolved identity is an error) |
| `FORGELM_AUDIT_SECRET` | HMAC signing key for the audit log chain (enables tamper-detection) |
| `FORGELM_GGUF_CONVERTER` | Path to a custom `convert-hf-to-gguf.py` script |

## Common patterns

### "Just train and don't bother me"

```shell
$ forgelm --config configs/run.yaml --output-format json | tee run.log
```

### "Run audit, then train if clean"

```shell
$ forgelm audit data/
$ forgelm --config configs/run.yaml
```

Run the two commands sequentially in your CI pipeline; `forgelm audit` exits non-zero on policy violations so the second command never fires on a dirty corpus.

### "Train with human approval gate; promote later"

```shell
$ forgelm --config configs/run.yaml                  # exits 4 if approval gate fires
$ forgelm approvals --pending                        # discover the pending run
$ forgelm approve RUN_ID --comment "Reviewed."       # promote staging
```

### "Train, export GGUF, deploy to Ollama"

```yaml
# configs/run.yaml
output:
  gguf:
    enabled: true
deployment:
  target: ollama
```

```shell
$ forgelm --config configs/run.yaml
# Training, export, and deploy config generation all happen.
```

## See also

- [Configuration Reference](#/reference/configuration) — YAML companion.
- [Exit Codes](#/reference/exit-codes) — gate contract for CI.
- [YAML Templates](#/reference/yaml-templates) — full working configs.
