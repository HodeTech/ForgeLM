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
| `forgelm verify-integrity` | Verify a model directory against its Article 15 SHA-256 integrity manifest. |
| `forgelm approve` | Sign a human approval request and promote `final_model.staging/`. |
| `forgelm reject` | Reject a human approval request; the staging directory is preserved for forensics. |
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
| `--stage NAME` | Multi-stage pipelines only: run a single named stage in isolation. Non-first stages need the previous stage's on-disk output, or `--input-model`. |
| `--resume-from NAME` | Multi-stage pipelines only: resume an interrupted run from a named stage onward. Completed stages whose `output_model` paths exist are skipped. **Refuses to run when the on-disk `pipeline_config_hash` differs from the current YAML** unless `--force-resume` is also passed. |
| `--force-resume` | Multi-stage pipelines only: bypass the `--resume-from` stale-config guard. Logged at WARNING and recorded in the audit event. |
| `--input-model PATH` | Multi-stage pipelines only: with `--stage`, replaces the auto-chained input model. The audit entry records `input_source: cli_override` so reviewers see the chain was broken intentionally. |
| `--generate-data` | Generate synthetic training data using the teacher model. No training. |
| `--compliance-export OUTPUT_DIR` | Export EU AI Act compliance artifacts (audit trail, data provenance, Annex IV) to OUTPUT_DIR. Run after training so the manifest is complete. |
| `--output DIR` | Output directory for `--compliance-export` (default: `./compliance/`). |
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

Probes Python version, torch / CUDA / GPU, optional extras, HF Hub reachability (or HF cache when `--offline`), disk space, and operator identity. Exit codes: `0` = all pass (warnings OK), `1` = at least one fail, `2` = a probe itself crashed.

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
    [--allow-secrets] [--allow-pii] \
    [--output-format {text,json}]
```

| Flag | Description |
|---|---|
| `--allow-secrets` | Record credential findings without failing (exit `0` with a `SUPPRESSED` warning instead of `3`). |
| `--allow-pii` | Record critical-tier PII findings (`credit_card`, `iban`) without failing. Independent of `--allow-secrets`. |

`--workers N` parallelises split-level processing; the on-disk JSON is byte-identical across worker counts (modulo the `generated_at` timestamp). The full per-flag table — including the canonical authoritative list synced to `forgelm/cli/_parser.py::_add_audit_subcommand` — lives in [Dataset Audit](#/data/audit). Earlier drafts of this page documented `--strict`, `--skip-pii`, `--skip-secrets`, `--skip-quality`, `--skip-leakage`, `--remove-duplicates`, `--remove-cross-split-overlap`, `--output-clean`, `--show-leakage`, `--minhash-jaccard`, `--minhash-num-perm`, `--dedup-algo`, `--dedup-threshold`, `--sample-rate`, and `--add-row-ids` — none exist in the parser. Use the canonical names above.

## Ingest: `forgelm ingest`

```shell
$ forgelm ingest INPUT_PATH \
    --output PATH.jsonl \
    [--recursive] \
    [--strategy {sliding,paragraph,markdown}] \
    [--chunk-size N] [--overlap N] \
    [--chunk-tokens N] [--overlap-tokens N] [--tokenizer MODEL_NAME] \
    [--input-encoding CODEC] \
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

`--input-encoding CODEC` pins the source codec for `.txt` / `.md` input
only — PDF / DOCX / EPUB carry their own encoding metadata and ignore
this flag. Default (unset) auto-detects via `utf-8-sig` with a
BOM-strip + `errors="replace"` fallback, unchanged from the prior
behaviour. Pass a legacy codec name (e.g. `cp1254`, `cp1252`,
`latin-1`) to decode older Windows-exported corpora correctly instead
of replacing every non-ASCII byte with `U+FFFD`. An unrecognised codec
name is rejected up front, before any file is read, with a config
error (`1`).

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

`--quant` takes a single level per invocation; run `forgelm export` once per level for multiple GGUF outputs. See [GGUF Export](#/deployment/gguf-export).

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

`--output-dir` is **required** on all three subcommands — it is where the approval chain and `final_model.staging/` live. Omitting it is an argparse error (exit `2`), not a helpful default:

```shell
$ forgelm approvals --pending --output-dir ./checkpoints                        # list pending approval gates
$ forgelm approvals --show RUN_ID --output-dir ./checkpoints                    # inspect a run's chain + staging
$ forgelm approve  RUN_ID --output-dir ./checkpoints --comment "Reviewed by N." # promote staging → final_model/
$ forgelm reject   RUN_ID --output-dir ./checkpoints --comment "Reason ..."     # record rejection (staging preserved)
```

See [Human Oversight Gate](#/compliance/human-oversight). Exit codes: `0` = pending list / approval recorded, `1` = unknown run_id / config error, `2` = argparse usage error (a missing `--output-dir` lands here), `4` (training mode only) = awaiting approval.

## Verify audit log: `forgelm verify-audit`

```shell
$ forgelm verify-audit PATH/TO/audit_log.jsonl
$ forgelm verify-audit PATH/TO/audit_log.jsonl --hmac-secret-env FORGELM_AUDIT_SECRET
$ forgelm verify-audit PATH/TO/audit_log.jsonl --require-hmac
```

Validates monotonic timestamps, `prev_hash` chain integrity, `seq` gap detection, and (when configured) HMAC signatures. Exit `0` on a valid chain of at least one entry; exit `6` with a structured error envelope on tamper detection (chain break, HMAC mismatch, genesis-manifest mismatch, or a zero-entry log whose genesis manifest pins a first entry); exit `1` when nothing could be compared (missing path, `--require-hmac` without a secret, or a zero-entry log with no genesis manifest); exit `2` on a genuine runtime I/O failure. See [Verify Audit](#/compliance/verify-audit).

## Verify Annex IV: `forgelm verify-annex-iv`

```shell
$ forgelm verify-annex-iv PATH/TO/annex_iv_metadata.json
$ forgelm verify-annex-iv PATH/TO/annex_iv_metadata.json --output-format json
$ forgelm verify-annex-iv RUN_DIR --pipeline               # chain-level verification
```

Single-artefact mode validates the nine Annex IV §1-9 field categories and recomputes the manifest hash. Exit `0` when valid; `1` when a required field is missing or still holds a template placeholder (nothing was compared); `6` when every field is populated but the manifest hash no longer matches; `2` on a genuine runtime I/O failure.

`--pipeline` reinterprets the positional argument as a **run directory** and validates `<dir>/compliance/pipeline_manifest.json` — chain integrity, stage-index ordering, `stopped_at` coherence, a deep parse of every completed stage's Annex IV evidence, and a cross-check of the stage census against the audit log. It emits a different 12-key envelope with its own four-way exit mapping: `0` clean, `6` integrity failure, `2` unreadable artefact (retryable), `1` manifest absent/unparseable **or** evidence reached but unattested. Integrity is evaluated first so a weaker finding cannot mask a stronger one.

Single-artefact mode does **not** consult the audit log; only `--pipeline` does. See [Verify Annex IV](#/compliance/annex-iv).

## Safety eval: `forgelm safety-eval`

```shell
$ forgelm safety-eval --model ./checkpoints/final_model --default-probes
$ forgelm safety-eval --model ./checkpoints/final_model \
    --probes probes.jsonl \
    --classifier meta-llama/Llama-Guard-3-8B \
    --output-dir ./eval \
    --max-new-tokens 512 \
    --max-safety-regression 0.05 \
    --output-format json
```

| Flag | Description |
|---|---|
| `--model PATH` | **Required.** HuggingFace Hub ID or local checkpoint dir. GGUF is not supported — run against the pre-export HF checkpoint. |
| `--probes JSONL` | Probe file; each line is `{"prompt": ..., "category": ...}`. Mutually exclusive with `--default-probes`; exactly one is required. |
| `--default-probes` | Use the bundled 51-prompt probe set covering 18 harm categories. |
| `--classifier PATH` | Harm classifier (default: `meta-llama/Llama-Guard-3-8B`). |
| `--output-dir DIR` | Where per-prompt results + audit log are written (default: cwd). |
| `--max-new-tokens N` | Max tokens per generated response (default: 512). |
| `--max-safety-regression RATIO` | Maximum tolerated unsafe-response ratio in `[0.0, 1.0]` before the run fails the gate (default: `0.05`). **Absolute bound, not baseline-relative.** Exceeding it exits `3`. The value is echoed into the JSON envelope as `max_safety_regression`, so a CI job branching on exit `3` can read the threshold that decided it. |

Exit codes: `0` = passed; `1` = config error reached by the dispatcher; `2` = argparse usage error, a runtime error, **or** an evaluation that could not produce a verdict (`evaluation_completed=False` — not evidence about the model); `3` = the gate said no. See [Safety Evaluation](#/evaluation/safety) and [JSON Output Schemas](#/reference/json-output).

## Verify model integrity: `forgelm verify-integrity`

```shell
$ forgelm verify-integrity MODEL_DIR
$ forgelm verify-integrity MODEL_DIR --output-format json
```

Reads `<MODEL_DIR>/model_integrity.json` (written by the compliance export at training time) and re-computes the SHA-256 of every recorded artifact. Reports files that were **changed**, **removed**, or **added** since the manifest was generated. The manifest file itself is excluded from the walk. Exit `0` when every recorded artifact is present and unchanged and no extra files exist; exit `6` on any mismatch (changed / removed / added file — the manifest parsed and the walk ran); exit `1` on an input error that returns before anything is hashed (missing path, manifest not found, malformed JSON, out-of-tree manifest entry); exit `2` on a genuine runtime I/O failure. See [Verify Integrity](#/compliance/verify-integrity).

## Authentication

ForgeLM picks up credentials from environment variables. Never put them in YAML.

| Provider | Env var | Used for |
|---|---|---|
| HuggingFace | `HF_TOKEN` (alias: `HUGGINGFACE_TOKEN`) | Gated models (Llama, Llama Guard) |
| OpenAI | `OPENAI_API_KEY` | LLM-as-judge, synthetic data |
| Anthropic | `ANTHROPIC_API_KEY` | LLM-as-judge, synthetic data |
| W&B | `WANDB_API_KEY` | Experiment tracking |
| Cohere | `COHERE_API_KEY` | (synthetic data) |

ForgeLM's YAML loader is plain `yaml.safe_load` — there is no `${VAR}` shell-style interpolation. Two different patterns cover the credentials above:

- **HF token:** don't set anything under `auth:` — export `HF_TOKEN` (or the legacy `HUGGINGFACE_TOKEN`) in the shell and both `huggingface_hub`'s own auto-pickup and ForgeLM's login step find it.
- **Synthetic-data teacher API key:** name the env var in `synthetic.api_key_env` (a field on `SyntheticConfig`, not a nested `teacher:` object — the teacher model itself is `synthetic.teacher_model`):

```yaml
synthetic:
  teacher_model: "gpt-4o"
  teacher_backend: "api"
  api_key_env: "OPENAI_API_KEY"      # names the env var; the key itself never touches YAML
```

There is no config-time check that the named env var actually resolves — an unset `api_key_env` sends the request with no `Authorization` header, and the teacher API rejects it (typically HTTP 401) on the first call. Export the env var before running `--generate-data` so that failure surfaces immediately rather than mid-run.

## Exit codes

| Exit | Meaning |
|---|---|
| 0 | Success |
| 1 | Config / semantic validation error (bad YAML, missing file, empty `--query`, etc.) |
| 2 | Argparse usage error (unknown flag/subcommand, missing required arg, bad choice, out-of-range type validator), training crash, probe crash (`forgelm doctor`), or a clamped Ctrl+C |
| 3 | Auto-revert / regression |
| 4 | Awaiting human approval (training pipeline) |
| 5 | Wizard cancelled (operator declined to save / non-tty refusal) |
| 6 | Integrity failure on one of the four `verify-*` subcommands — the artefact was read and its hash / chain / manifest comparison failed |

`argparse` usage errors (mistyped flag, missing required argument, bad `choices`,
or a type-validator boundary) exit **2** — argparse's own `error()` convention —
while config / semantic validation reached *after* parsing exits **1**. A Ctrl+C
is signal-derived 130 but is clamped to **2** (`EXIT_TRAINING_ERROR`) before the
process exits, so no exit code outside the public `0–6` set is ever returned.

For the four `verify-*` subcommands, `1` and `6` split on one question: did the
verifier get far enough to compare anything? Nothing compared (missing path,
malformed manifest, a file that isn't a GGUF at all) is **1**; compared and
disagreed is **6**.

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

`forgelm audit` gates on **two things**: it exits `3` when the always-on credential scan finds something, and when the PII scan finds critical-tier PII (`credit_card` / `iban` — the checksum-validated categories). Otherwise it exits `0`. So `&&` does chain correctly for both cases:

```shell
$ forgelm audit data/           # exits 3 on a credential or a critical-tier PII finding
$ forgelm --config configs/run.yaml
```

Run them as separate `set -e` steps (or join with `&&`); the training step is skipped when the audit exits `3`.

:::warn
**Sub-critical PII, leakage and quality do not gate.** A corpus carrying a plaintext SSN at `worst_tier: "high"`, or train/eval overlap, exits `0` as long as it holds no credentials and no critical-tier PII. National IDs, emails and phone numbers are matched on shape and deliberately over-report, so gating on them would fail clean corpora — see [Dataset Audit](#/data/audit) for the full reasoning. If your policy covers those, parse the envelope yourself:

```shell
$ forgelm audit data/ --output-format json > audit.json   # exits 3 on secrets or critical PII
$ jq -e '(.pii_severity.worst_tier // "none") != "high" and (.cross_split_leakage_pairs | length) == 0' audit.json
$ forgelm --config configs/run.yaml
```

Under `set -e` (or GitHub Actions' default), the failing `jq -e` stops the job before training starts. `pii_severity.worst_tier` is `null` on a clean corpus, so keep the `// "none"` fallback. See [JSON Output Schemas](#/reference/json-output) for the full envelope.
:::

Pass `--allow-secrets` to record credential findings without failing — for the legitimate case of auditing a corpus you already know contains them. `--allow-pii` does the same for the PII gate; the two are independent, so passing one leaves the other armed.

### "Train with human approval gate; promote later"

```shell
$ forgelm --config configs/run.yaml                                         # exits 4 if approval gate fires
$ forgelm approvals --pending --output-dir ./checkpoints                    # discover the pending run
$ forgelm approve RUN_ID --output-dir ./checkpoints --comment "Reviewed."   # promote staging
```

### "Train, export GGUF, deploy to Ollama"

There is no `output:` or `deployment:` top-level YAML key — `ForgeConfig` rejects unknown keys (`extra="forbid"`), so a config carrying either fails `--dry-run` immediately. Export and deploy are separate CLI steps run *after* training completes, not config-driven pipeline stages:

```shell
$ forgelm --config configs/run.yaml                                    # 1. train (writes ./checkpoints/final_model)
$ forgelm export ./checkpoints/final_model --output model.gguf --quant q4_k_m   # 2. export to GGUF
$ forgelm deploy ./checkpoints/final_model --target ollama --output ./Modelfile # 3. generate the Ollama Modelfile
```

See [Export: `forgelm export`](#export-forgelm-export) and [Deploy: `forgelm deploy`](#deploy-forgelm-deploy) above, and the [Configuration Reference `deployment:`](#/reference/configuration) section for the full explanation of why there is no YAML-driven deploy step.

## See also

- [Configuration Reference](#/reference/configuration) — YAML companion.
- [Exit Codes](#/reference/exit-codes) — gate contract for CI.
- [YAML Templates](#/reference/yaml-templates) — full working configs.
