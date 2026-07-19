# ForgeLM Architecture

ForgeLM is designed with modularity and extensibility in mind. The workflow is broken down into distinct stages, each handled by a dedicated module.

## System Overview

```
forgelm --config job.yaml
    │
    ├── cli/                → CLI package (Phase 15 split)
    │   ├── _parser.py          → 19 subcommands + global flags
    │   ├── _dispatch.py        → Mode dispatcher
    │   ├── _exit_codes.py      → 0/1/2/3/4/5/6 contract
    │   └── subcommands/        → Per-subcommand handlers (19 subcommands)
    │       ├── ingest, audit, chat, export, deploy, doctor,
    │       │   cache-models, cache-tasks, purge, reverse-pii,
    │       │   approve, reject, approvals, safety-eval,
    │       │   verify-audit, verify-annex-iv, verify-gguf,
    │       │   verify-integrity, quickstart
    ├── config.py           → Pydantic validation (23 config models)
    ├── utils.py            → HF authentication
    ├── model.py            → Load model + tokenizer + LoRA/PEFT
    ├── data.py             → Load + format dataset
    ├── data_audit/         → Audit package (Phase 14 split)
    │   ├── _orchestrator, _aggregator, _streaming, _simhash,
    │   │   _minhash, _pii_regex, _pii_ml, _secrets, _quality,
    │   │   _croissant, _summary, _splits
    ├── trainer.py          → Train (6 trainer types via TRL)
    │   ├── benchmark.py        → lm-eval-harness evaluation
    │   ├── safety/            → Llama Guard safety check (sub-package)
    │   ├── judge.py            → LLM-as-Judge scoring
    │   ├── model_card.py       → Auto-generate HF model card
    │   ├── compliance.py       → EU AI Act audit artifacts
    │   ├── verify.py           → Annex IV / GGUF / model-integrity verification
    │   └── webhook.py          → Slack/Teams notifications
    ├── merging.py          → TIES/DARE/SLERP model merge
    ├── synthetic.py        → Synthetic data generation
    └── wizard/             → Interactive config generator (sub-package, Phase 22)
```

## Directory Layout

```
ForgeLM/
├── forgelm/                # Core Python package (~21 single-file modules + 4 sub-packages)
│   ├── __init__.py         # Lazy imports for fast CLI startup
│   ├── cli/                # CLI sub-package (Phase 15 split)
│   │   ├── _parser.py          # 19 subcommands + global flags
│   │   ├── _dispatch.py        # Mode dispatcher
│   │   ├── _exit_codes.py      # Public 0/1/2/3/4/5/6 contract
│   │   └── subcommands/        # Per-subcommand handler modules
│   │       └── _audit, _ingest, _chat, _export, _deploy, _doctor,
│   │           _cache, _purge, _reverse_pii, _approve, _approvals,
│   │           _safety_eval, _verify_audit, _verify_annex_iv,
│   │           _verify_gguf, _verify_integrity, _quickstart
│   ├── data_audit/         # Data-audit sub-package (Phase 14 split)
│   │   └── _orchestrator, _aggregator, _streaming, _simhash,
│   │       _minhash, _pii_regex, _pii_ml, _secrets, _quality,
│   │       _croissant, _summary, _splits, _types, _optional
│   ├── config.py           # 23 Pydantic config models
│   ├── data.py             # Dataset loading (SFT/DPO/KTO/GRPO/multimodal)
│   ├── ingestion.py        # Raw docs → SFT JSONL (PDF/DOCX/EPUB/TXT/Markdown)
│   ├── model.py            # Model + LoRA/DoRA/PiSSA + MoE detection
│   ├── trainer.py          # Training orchestration (6 trainer types)
│   ├── inference.py        # Shared inference primitives (load/generate/stream)
│   ├── chat.py             # Interactive terminal REPL with slash commands
│   ├── export.py           # GGUF export via llama-cpp-python
│   ├── fit_check.py        # Pre-flight VRAM estimator
│   ├── deploy.py           # Deployment config generator (Ollama/vLLM/TGI/HF Endpoints)
│   ├── results.py          # TrainResult dataclass (no heavy deps)
│   ├── benchmark.py        # lm-evaluation-harness integration
│   ├── safety/             # Safety sub-package (post-v0.9.1 split)
│   │   └── _types, _inputs, _generate, _classifier,
│   │       _score_classification, _score_generation, _gates,
│   │       _results, _orchestrator
│   ├── judge.py            # LLM-as-Judge (API + local)
│   ├── compliance.py       # EU AI Act compliance + audit log + provenance
│   ├── verify.py           # Annex IV / GGUF / model-integrity verification primitives
│   ├── model_card.py       # HF-compatible model card generation
│   ├── merging.py          # Model merging (TIES/DARE/SLERP/linear)
│   ├── synthetic.py        # Synthetic data generation (teacher→student)
│   ├── grpo_rewards.py     # Built-in GRPO format/length reward shapers
│   ├── quickstart.py       # Bundled one-command templates
│   ├── wizard/             # Interactive configuration wizard (sub-package — Phase 22)
│   ├── webhook.py          # Webhook notifications (Slack/Teams)
│   ├── _http.py            # SSRF-guarded HTTP chokepoint
│   ├── _version.py         # __version__ + __api_version__ (decoupled)
│   └── utils.py            # Authentication + checkpoint management
├── forgelm/templates/      # 5 quickstart template bundles
├── configs/
│   ├── deepspeed/          # ZeRO-2, ZeRO-3, ZeRO-3+Offload presets
│   └── safety_prompts/     # Built-in adversarial prompt library (140 prompts, 6 categories)
├── notebooks/              # 10 Colab-ready Jupyter notebooks
├── tests/                  # ~70 test modules
├── tools/                  # CI guards: bilingual_parity, anchor_resolution,
│                            # cli_help_consistency, yaml_snippets,
│                            # audit_event_catalog, library_api_doc,
│                            # doc_numerical_claims, bilingual_code_blocks
├── docs/                   # Guides, reference docs, QMS templates
│   ├── guides/             # User guides (ingestion, audit, alignment, CI/CD, …)
│   └── qms/                # EU AI Act QMS SOP templates
├── Dockerfile              # Multi-stage Docker build
├── docker-compose.yaml     # Train + TensorBoard services
├── config_template.yaml    # Annotated config example
└── CONTRIBUTING.md         # Contributor guide
```

## Component Details

### `cli/`
The orchestrator (Phase 15 split). `_parser.py` registers 19 subcommands (`audit`, `approve`, `approvals`, `reject`, `cache-models`, `cache-tasks`, `chat`, `deploy`, `doctor`, `export`, `ingest`, `purge`, `quickstart`, `reverse-pii`, `safety-eval`, `verify-annex-iv`, `verify-audit`, `verify-gguf`, `verify-integrity`) plus the legacy training-mode flag set. `_dispatch.py` routes to the appropriate handler in `subcommands/`. `_exit_codes.py` defines the public 0/1/2/3/4/5/6 contract (5 = wizard cancelled, 6 = integrity failure — the `verify-*` subcommands only, when a read artefact fails its hash/chain check). The verification primitives themselves live in `forgelm/verify.py`, not under `cli/`, keeping the CLI subcommand modules thin dispatchers per `docs/standards/architecture.md`'s "CLI is a thin shim" rule.

### `config.py`
23 Pydantic v2 models providing strict validation for all YAML configuration. Includes cross-field validation (e.g., high-risk classification enforces safety evaluation). Config models cover: model, LoRA, training, data, evaluation, safety, benchmark, judge, webhook, distributed, merge, compliance, retention, risk assessment, monitoring, MoE, multimodal, data governance, and synthetic-data generation.

### `data.py`
Interfaces with HuggingFace `datasets` library. Auto-detects dataset format (SFT, DPO, KTO, GRPO, multimodal) and validates against `trainer_type`. Handles multi-dataset mixing with configurable ratios. Applies chat templates via `tokenizer.apply_chat_template()` with fallback formatting.

### `model.py`
Loads models via HuggingFace Transformers or Unsloth backend. Configures QLoRA (4-bit NF4), PEFT adapters (LoRA, DoRA, PiSSA, rsLoRA), and MoE expert quantization/selection. Distributed-aware: skips `device_map="auto"` when DeepSpeed/FSDP is active. Multimodal-aware: loads `AutoProcessor` instead of `AutoTokenizer` for VLM models.

### `trainer.py`
Wraps TRL's trainers (SFTTrainer, DPOTrainer, KTOTrainer, ORPOTrainer, CPOTrainer/SimPO, GRPOTrainer) with ForgeLM's pipeline: baseline evaluation → training → post-training evaluation chain (loss → benchmark → safety → LLM-judge) → model save → model card → compliance artifacts → webhook notification. Supports GaLore optimizer-level memory optimization (gradient low-rank projection for full-parameter training) and long-context features (RoPE scaling, NEFTune noise injection, sliding window attention, sample packing). Includes auto-revert, human approval gate, audit logging, and resource tracking.

### `results.py`
Lightweight `TrainResult` dataclass — importable without torch/transformers. Carries success status, metrics, benchmark scores, resource usage, safety pass/fail, and judge scores.

### `benchmark.py`
Wraps EleutherAI `lm-evaluation-harness`. Runs configurable benchmark tasks, extracts accuracy metrics, applies min_score threshold, and saves results. Optional dependency: `pip install forgelm[eval]`.

### `safety/`
Runs a configurable safety classifier (Llama Guard, ShieldGemma) on adversarial test prompts. Generates responses from the fine-tuned model, classifies each as safe/unsafe, and triggers auto-revert if regression exceeds threshold. Errors are treated as unsafe (fail-safe principle).

### `judge.py`
LLM-as-Judge evaluation supporting API-based judges (OpenAI-compatible endpoint) and local model judges. Includes robust JSON parsing with markdown code block extraction. Scores on 1-10 scale with configurable minimum threshold.

### `compliance.py`
EU AI Act compliance engine covering Articles 9-17:
- `AuditLogger`: Append-only JSON Lines event log with unique run IDs
- `generate_training_manifest()`: Annex IV technical documentation
- `generate_data_governance_report()`: Data quality statistics
- `generate_model_integrity()`: SHA-256 checksums of output artifacts
- `generate_deployer_instructions()`: Art. 13 deployer document
- `export_compliance_artifacts()`: All artifacts to directory
- `export_evidence_bundle()`: ZIP archive for auditors

### `verify.py`
Consuming counterpart to `compliance.py`'s writers — re-hashes and re-validates the artifacts `compliance.py` produces:
- `verify_annex_iv_artifact()`: field completeness + manifest-hash tamper check for an Annex IV JSON bundle
- `verify_gguf()`: magic header + optional metadata parse + SHA-256 sidecar check for an exported GGUF file
- `verify_integrity()`: re-walks a model directory against `model_integrity.json`, reporting changed/removed/added artifacts
- `is_annex_iv_integrity_failure()` / `is_gguf_integrity_failure()` / `is_model_integrity_failure()`: structural (never string-matched) predicates the `verify-*` CLI subcommands use to route between `EXIT_CONFIG_ERROR` (1, nothing was compared) and `EXIT_INTEGRITY_FAILURE` (6, compared and disagreed)

`verify_audit_log` deliberately stays in `compliance.py` rather than moving here — it must mirror `AuditLogger.log_event`'s canonicalisation byte-for-byte, and separating a writer from its verifier is the drift hazard this module's docstring warns against.

### `model_card.py`
Generates HuggingFace-compatible README.md with YAML front matter, training parameters table, metrics, benchmark results, config snippet, and usage example. Excludes auth tokens from exported config.

### `merging.py`
Model merging with 4 strategies: linear interpolation, TIES-Merging (trim + sign election + merge), DARE (random drop + rescale), and SLERP (spherical interpolation for 2 models). Operates on state dicts — no mergekit dependency required.

### `synthetic.py`
Synthetic data generation via teacher-to-student distillation. The `SyntheticDataGenerator` class takes a teacher model (API-based or local), generates training samples from seed prompts, and outputs formatted JSONL datasets. Triggered via `--generate-data` CLI flag or `synthetic` config section. Supports configurable teacher backends, output formats, and generation parameters.

### `wizard/`

Interactive CLI wizard for generating valid YAML configs. Phase 22 modernisation (2026-05-08) brought the CLI to parity with `site/js/wizard.js`: 9-step state machine (welcome / use-case / model / strategy / trainer / dataset / training-params / compliance / operations), per-trainer hyperparameters (`dpo_beta` / `simpo_beta` + `simpo_gamma` / `kto_beta` / `orpo_beta` / `grpo_*`), full PEFT method coverage (`lora` / `dora` / `pissa` / `rslora`) plus GaLore axis, EU AI Act Article 9 + 10 + 11 + 12+17 compliance accordions, F-compliance-110 strict-tier auto-coercion, `back` / `reset` navigation, XDG-aware persistence at `$XDG_CACHE_HOME/forgelm/wizard_state.yaml`, step-diff preview, beginner / expert toggle, and the Phase 11.5 / 12.5 BYOD inline ingest + audit helpers (`_offer_ingest_for_directory`, `_offer_audit_for_jsonl`).

### `webhook.py`
Sends structured JSON payloads to Slack/Teams/generic webhooks on training start, success, and failure. Supports URL from config or environment variable. Graceful error handling with configurable timeout.

### `utils.py`
HuggingFace authentication (token from config, env var, or local cache with modern XDG path support) and checkpoint management (keep, delete, compress with UUID-suffixed archives).
