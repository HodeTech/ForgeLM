# Sürüm Notları

> **Not:** Bu dosya yayınlanmış ve yakında yayınlanacak sürümleri takip eder. Her sürüm, bir veya daha fazla tamamlanmış phase'e karşılık gelir.

## v0.3.0 Release

**Status:** Complete
**Release Date:** March 2026

### Features:
1. [x] **GaLore**: Optimizer-level memory optimization — full-parameter training via gradient low-rank projection as an alternative to LoRA. Config fields: `galore_enabled`, `galore_optim`, `galore_rank`, `galore_update_proj_gap`, `galore_scale`, `galore_proj_type`, `galore_target_modules`.
2. [x] **Long-Context Training**: RoPE scaling, NEFTune noise injection, sliding window attention, and sequence packing for extended context windows. Config fields: `rope_scaling`, `neftune_noise_alpha`, `sliding_window_attention`, `packing` (`sample_packing` is a deprecated alias, removal targeted for v1.0.0).
3. [x] **Synthetic Data Pipeline**: Teacher-to-student distillation via `--generate-data` CLI flag. New `SyntheticDataGenerator` class in `forgelm/synthetic.py`. Configurable teacher model, backend, seed prompts, and output format.
4. [x] **PyPI Publishing**: `pip install forgelm` now works. Automated publishing via `publish.yml` GitHub Actions workflow.
5. [x] **GPU Cost Estimation**: Auto-detection for 16 GPU models with per-run cost tracking. Included in JSON output, webhook notifications, and model cards.
6. [x] **Nightly CI**: `.github/workflows/nightly.yml` for compatibility testing against latest dependency versions.
7. [x] **Expanded Adversarial Prompts**: 6 category files, 140 prompts (up from 50) covering general safety, bias/discrimination, harmful instructions, privacy/PII, misinformation, and jailbreak attempts.

---

## v0.3.1rc1 — "Security & Config Hardening" (2026-04-25)

**Status:** Folded into v0.4.0 (changes shipped as part of the v0.4.0 release; no standalone tag)

### Changes:
- **Security**: Webhook URLs excluded from HuggingFace Hub model cards — prevent credential leaks
- **Security**: User-supplied strings sanitized before Markdown template embedding (content injection prevention)
- **Config robustness**: All 19 Pydantic sub-models now enforce `extra="forbid"` — YAML typos are errors, not silent bugs
- **Config robustness**: Deprecated `lora.use_dora` / `lora.use_rslora` booleans now auto-normalize to `lora.method: "dora"/"rslora"` with deprecation warnings
- **Compliance**: Audit log hash chain now restores continuity across process restarts — cross-run tamper evidence
- **Compliance**: Compliance manifests correctly report pre-OOM-recovery batch size
- **GRPO**: Reward model path now correctly wrapped as callable (was passing string, causing TypeError)
- **Safety**: Safety classifier now receives full `[INST] prompt [/INST] response` conversation context (was response-only)
- **Data**: Extension-less files now raise clear ValueError instead of silently loading wrong format
- **Merging**: TIES tie-breaking fixed (zero-vote no longer zeros parameters); DARE now deterministic with seed=42
- **Config validators**: New — mix_ratio negative/all-zero, float32+4bit warning, high LoRA rank warning, eval_steps>save_steps warning
- **Tests**: 25 new regression tests; coverage threshold raised from 25% to 40%

---

## v0.4.0 — "Post-Training Completion" (2026-04-26)

**Status:** Released — published to PyPI on 2026-04-26 ([release notes](https://github.com/HodeTech/ForgeLM/releases/tag/v0.4.0)).

Odak: [Phase 10](completed-phases.md). Full post-training handoff: inference, chat, GGUF export, VRAM fit-check, deployment config generation.

### Features:
1. [x] **`forgelm/inference.py`** — Shared generation primitives: `load_model`, `generate`, `generate_stream` (streaming via background thread, with timeout-based deadlock guard), `logit_stats`, `adaptive_sample`. Supports transformers + peft (merge-and-unload) + unsloth backends.
2. [x] **`forgelm chat`** — Interactive terminal REPL with streaming output, `/reset`, `/save` (system prompt persisted), `/temperature`, `/system` slash commands. Optional `rich` rendering with markup escape on token output. History capped at 50 turns.
3. [x] **`forgelm export`** — GGUF conversion via `llama-cpp-python`'s `convert_hf_to_gguf.py`. Supports adapter merge before conversion. K-quants (`q2_k`/`q3_k_m`/`q4_k_m`/`q5_k_m`) routed through an honest `.f16.gguf` intermediate (manifest SHA-256 always matches the file actually written); `q8_0` and `f16` are direct. SHA-256 appended to `model_integrity.json`. `pip install forgelm[export]`.
4. [x] **`forgelm --fit-check`** — Pre-flight VRAM estimator. Architecture via `AutoConfig` with word-bounded size-hint fallback. Formula: base weights + (LoRA adapter ⊕ GaLore projection — mutually exclusive) + optimizer state (AdamW/8bit/GaLore) + activations (gradient-checkpointing aware). Verdicts: FITS / TIGHT / OOM / UNKNOWN. `--output-format json` for CI/CD.
5. [x] **`forgelm deploy`** — Deployment config generator for 4 targets: `ollama` (Modelfile), `vllm` (YAML), `tgi` (docker-compose.yaml), `hf-endpoints` (JSON). Local-path validation rejects HF Hub IDs for `tgi`/`ollama` (would silently produce broken volumes). Does not run the server itself.
6. [x] **`pip install forgelm[export]`** — Optional `llama-cpp-python>=0.2.90` extra. `pip install forgelm[chat]` — Optional `rich>=13.0.0` extra.

### Notebooks:
- New: [`post_training_workflow.ipynb`](../../notebooks/post_training_workflow.ipynb) — end-to-end Phase 10 toolchain walkthrough.
- Updated: `quickstart_sft.ipynb` gets a "Next Steps" section pointing into the new toolchain.

---

## v0.4.5 — "Quickstart Layer" (2026-04-26)

**Status:** Released — published to PyPI on 2026-04-26 ([release notes](https://github.com/HodeTech/ForgeLM/releases/tag/v0.4.5)). Focus: [Phase 10.5](completed-phases.md) (Quickstart). One-command bundled templates, sample datasets, opinionated defaults — primary community growth driver.

### Features:
1. [x] **`forgelm/quickstart.py`** — Template registry (`@dataclass(frozen=True) Template`), `auto_select_model()` GPU-aware downsizing (≥10 GB VRAM → primary model; otherwise fallback ≤2B), `run_quickstart()` end-to-end orchestrator that copies the bundled seed dataset, substitutes `model.name_or_path` and `data.dataset_name_or_path`, and writes a `configs/<template>-YYYYMMDDHHMMSS.yaml` the existing trainer accepts unchanged.
2. [x] **`forgelm quickstart <template>` CLI subcommand** — `--list` (text + JSON via `--output-format json`), `--model` / `--dataset` overrides, `--dry-run` (generate config but skip training), `--no-chat` (skip post-training chat REPL), `--output` (custom YAML path). On a successful train, subprocess-invokes `forgelm chat <output_dir>` for an immediate sanity loop. Top-level flags (`--output-format`, `--quiet`, `--log-level`, `--offline`) propagate to the train + chat subprocesses.
3. [x] **5 bundled templates** under [`forgelm/templates/`](../../forgelm/templates/):
   - `customer-support` (Qwen2.5-7B-Instruct ↔ SmolLM2-1.7B-Instruct, SFT, 58-example seed JSONL)
   - `code-assistant` (Qwen2.5-Coder-7B-Instruct ↔ Qwen2.5-Coder-1.5B-Instruct, SFT, 59-example seed)
   - `domain-expert` (Qwen2.5-7B-Instruct ↔ SmolLM2-1.7B-Instruct, BYOD — empty data, README walks through the workflow)
   - `medical-qa-tr` (Qwen2.5-7B-Instruct ↔ Qwen2.5-1.5B-Instruct, SFT, 49 Turkish Q&A with safety disclaimers)
   - `grpo-math` (Qwen2.5-Math-7B-Instruct ↔ Qwen2.5-Math-1.5B-Instruct, GRPO trainer, 40 grade-school math prompts each carrying a `gold_answer` for the built-in regex correctness reward)
4. [x] **Conservative defaults** — every template ships with QLoRA 4-bit NF4, rank=8, batch=1 + gradient accumulation, gradient checkpointing on, safety eval / compliance artifacts opt-in only.
5. [x] **GRPO baseline reward** — when `grpo_reward_model` is unset, `forgelm/grpo_rewards.combined_format_length_reward` (format-match × 0.8 + length-shaping × 0.2) is wired by default so prompt-only datasets don't crash inside `trl.GRPOTrainer`. If the dataset additionally carries a `gold_answer` field (the bundled grpo-math seed does), `_math_reward_fn` is appended for an additive correctness signal.
6. [x] **Wizard integration** — `forgelm --wizard` now opens with "Start from a template?". Yes → invokes the quickstart flow (BYOD path validates the supplied dataset path before continuing); No → falls through to the existing 8-step interactive flow. No bifurcation: same code paths, same YAML schema.
7. [x] **License hygiene** — [`forgelm/templates/LICENSES.md`](../../forgelm/templates/LICENSES.md) catalogs all bundled seed datasets (CC-BY-SA 4.0, author-original); contributing guide for new templates.
8. [x] **Tests + CI** — `tests/test_quickstart.py`, `tests/test_quickstart_hardening.py`, `tests/test_grpo_math_reward.py`, `tests/test_grpo_format_reward.py`, `tests/test_wizard_byod.py`, `tests/test_cli_quickstart_wiring.py`, `tests/test_packaging.py`. Includes a regression test that loads every generated YAML through `load_config` (the strongest guard against template drift). Nightly CI smoke-tests every template via `quickstart --dry-run` + `--config <out> --dry-run`, plus a dedicated `wheel-install-smoke` job that builds the wheel and reruns quickstart from a fresh venv to catch broken `package_data` globs.
9. [x] **`pyproject.toml` `[tool.setuptools.package-data]`** — bundles `*.yaml`, `*.jsonl`, `*.md` under `forgelm.templates` into the wheel so `pip install forgelm` users get the templates too.

---

## v0.5.0 — "Document Ingestion + Data Curation Pipeline"

**Status:** ✅ Done — released to [PyPI 2026-04-30](https://pypi.org/project/forgelm/0.5.0/) (Phases 11 + 11.5 + 12 + 12.5 consolidated; merged on `main` 2026-04-29). One hardening follow-up tracked outside the release: [#14 — webhook SSRF DNS-rebinding TOCTOU](https://github.com/HodeTech/ForgeLM/issues/14) (defence-in-depth on top of the existing `allow_private_destinations: false` default).

> **Note on consolidation.** Originally planned as four sequential PyPI tags (`v0.5.0` / `v0.5.1` / `v0.5.2` / `v0.5.3`), the four phases were consolidated into a single `v0.5.0` because they form one coherent surface (ingest → polish → mature → polish) hard to use in parts. Git history retains the four phases as separate commit batches; this entry collapses them into one user-facing release. CHANGELOG.md preserves the phase boundaries inside the `[0.5.0]` section so reviewers can map back to PR history (#11, #12, #13, #18).

### Phase 11 — Document Ingestion & Data Audit

1. [x] **`forgelm/ingestion.py` + `forgelm ingest` subcommand** — Multi-format document → JSONL pipeline with `paragraph` (default) and `sliding` chunking strategies, recursive directory walk, optional `--pii-mask`. Supported extensions: `.pdf` (`pypdf`), `.docx` (`python-docx`), `.epub` (`ebooklib` + `beautifulsoup4`), `.txt`, `.md`. Output is `{"text": ...}` JSONL recognised by ForgeLM's data loader as pre-formatted SFT input. OCR is intentionally out of scope; scanned PDFs warn and produce zero chunks.
2. [x] **`forgelm/data_audit.py` + `forgelm --data-audit` flag** — Per-split metrics (sample count, column schema, length distribution `min/max/mean/p50/p95`, top-3 language detection, null/empty rate), 64-bit simhash near-duplicate detection within each split, cross-split overlap report (catches train-test leakage), PII regex with Luhn-validated credit cards and TC Kimlik checksum-validated TR IDs. CPU-only, no network.
3. [x] **EU AI Act Article 10 integration** — `generate_data_governance_report` inlines `data_audit_report.json` under the `data_audit` key when present in the trainer's `output_dir`.
4. [x] **`pyproject.toml` `[ingestion]` extra** — `pypdf`, `python-docx`, `ebooklib`, `beautifulsoup4`, `langdetect`. Cross-platform; no native compilation.

### Phase 11.5 — Ingestion / Audit Polish

1. [x] **`forgelm audit` subcommand** — promotes the `--data-audit` flag to a first-class subcommand. Flag preserved as a deprecation alias.
2. [x] **LSH-banded near-duplicate detection** — replaces the `O(n²)` pair scan with locality-sensitive-hashing bands; drops average-case to `O(n × k)` and unblocks 100K+ row corpora.
3. [x] **Streaming `_read_jsonl_split`** — JSONL reader yields rows lazily; per-split aggregator stays generator-based until simhash collection.
4. [x] **Token-aware `--chunk-tokens`** — sizes chunks against an HF tokenizer instead of raw character counts.
5. [x] **PDF page-level header / footer dedup** — repeated page headers (company watermark, page number) stripped automatically.
6. [x] **PII severity tiers** — `pii_severity` block grades each PII type as `low / medium / high / critical` + worst-tier verdict.
7. [x] **`summarize_report` truncation policy** — multi-split summaries default to `verbose=False`.
8. [x] **Structured ingestion notes** — parallel `notes_structured: {key: value}` map for programmatic consumers.
9. [x] **Wizard "ingest first" entry point** — first-class wizard option that routes to `forgelm ingest`.
10. [x] **xxhash backend + token-level memo** — drop-in faster non-crypto digest path; `lru_cache`-memoised repeat tokens for 2–5× speedup.
11. [x] **Atomic audit report write** — tempfile + atomic rename.

### Phase 12 — Data Curation Maturity (Tier 1)

1. [x] **MinHash LSH dedup option** — opt-in `--dedup-method minhash --jaccard-threshold 0.85` via `datasketch` (`[ingestion-scale]` extra). Default simhash + LSH banding stays untouched.
2. [x] **Markdown-aware splitter** — `--strategy markdown` preserves heading hierarchy (`# H1` / `## H2`), keeps fenced code blocks atomic, and inlines a heading breadcrumb so SFT loss sees document context.
3. [x] **Code / secrets leakage tagger** — new `secrets_summary` block in audit JSON (nine families per `forgelm.data_audit.SECRET_TYPES`). Ingest gains `--secrets-mask` (mask order: secrets → PII).
4. [x] **Heuristic quality filter** — opt-in `--quality-filter` adds a `quality_summary` block with Gopher / C4 / RefinedWeb-style heuristics.
5. [x] **DOCX / Markdown table preservation** — `_extract_docx` emits markdown table syntax instead of the previous `" | "` flat join.

### Phase 12.5 — Data Curation Polish (backlog items #1–#4)

1. [x] **Presidio adapter (item #1)** — `forgelm audit --pii-ml [--pii-ml-language LANG]` layers Presidio NER on top of the regex detector via the optional `[ingestion-pii-ml]` extra. Adds `person` / `organization` / `location` categories. Pre-flight check covers BOTH the missing-extra branch AND the missing-spaCy-model branch (`presidio-analyzer` does NOT transitively ship `en_core_web_lg`; the install recipe is two lines, raised as `ImportError` before any rows are scanned).
2. [x] **Croissant 1.0 metadata (item #2)** — `forgelm audit --croissant` populates a new `croissant` key in `data_audit_report.json` with a Google Croissant 1.0 dataset card. Card carries `cr:FileObject` per JSONL split, `cr:RecordSet` per split with `cr:Field` entries from column detection. Conformant with `mlcommons.org/croissant/1.0`.
3. [x] **`forgelm ingest --all-mask` (item #3)** — one-flag shorthand for `--secrets-mask --pii-mask` in the documented order. Set-union with explicit flags.
4. [x] **Wizard "audit first" (item #4)** — when the wizard resolves a JSONL (typed or produced by ingest), it offers to run `forgelm audit` inline and prints the verdict. Closes the BYOD audit loop.

### Hardening follow-up (tracked outside this release)

- [#14 — webhook SSRF DNS-rebinding TOCTOU](https://github.com/HodeTech/ForgeLM/issues/14): defence-in-depth on top of the existing `allow_private_destinations: false` default. Slated for `v0.5.1`.

---

## v0.6.0 — "Phase 15 Ingestion Pipeline Reliability" (2026-05-11)

**Status:** Released to PyPI 2026-05-11. Minor release on top of v0.5.7. Five review-absorption rounds (Gemini + CodeRabbit + Sonar + Codacy + independent self-review) ship in the same release. GitHub Release: [v0.6.0](https://github.com/HodeTech/ForgeLM/releases/tag/v0.6.0).

### Summary

Closes the silent-failure gap the 2026-05-11 ingestion pilot exposed across PDF / DOCX / EPUB / TXT / Markdown ingestion plus the user-facing playground notebook. The pre-Phase-15 `forgelm ingest` reported mechanical success while emitting silently-bad SFT data — multi-line running headers polluting 74/82 chunks on the audit's pilot PDF, pypdf font-fallback glyph corruption (`ø Õ ú ÷`), custom-bullet `U+085F` artefacts, recurring institutional-URL noise, ToC underscore-leader leakage, and silent front-matter chunk drops. v0.6.0 turns each failure mode into either an auto-correction or a loud operator-facing signal.

### Highlights

- **Window-based PDF edge dedup** (`_PDF_EDGE_WINDOW = 3`) catches variable-outer-line + constant-deeper-line corpora (the audit §1.1 trap) at both top + bottom edges in a single pass; second-pass dedup mops up survivor headers after paragraph packing.
- **Language-aware Unicode-block sanity check** (`forgelm/_script_sanity.py`, supports tr/en/de/fr/es/it/pt with discrete diacritic allow-lists) — fires WARNING + structured `script_sanity_summary` on out-of-script char ratios above 1.5 %. CLI dispatch WARN's when `--language-hint` is outside the supported list (round-5 self-review C-B).
- **Turkish pypdf glyph normalisation profile** (`forgelm/_pypdf_normalise.py`) maps audit-measured artefacts → correct Turkish characters. Default `"none"` to prevent silent rewrites of non-Turkish text; auto-derives `"turkish"` only when `--language-hint tr` is set. `forgelm doctor` verifies via the new `pypdf_normalise.turkish` diagnostic probe.
- **Audit `--quality-filter` flipped to default-on**; new `--no-quality-filter` companion preserves the pre-v0.6.0 opt-in semantics.
- **Ingest-time quality pre-signal** (`[WARN] N/M chunks below ingestion quality threshold`) with an 80-char floor so clean small corpora don't false-positive.
- **DOCX explicit header / footer subtraction** via `doc.sections[i].header.paragraphs` + `.footer.paragraphs`; 80-char length floor protects legitimate body paragraphs matching a header verbatim.
- **EPUB spine-order + whole-token nav / cover / copyright skip** — token-splitter prevents the `recovery.xhtml` → `cover` substring trap; EPUB-3 manifest properties string also flows through the same splitter; `"toc"` token removed to avoid `historical_toc.xhtml` false-positives.
- **TXT UTF-8 BOM strip + MD YAML frontmatter detection** with `utf-8-sig` on both decode paths + explicit leading-`﻿` strip.
- **`forgelm ingest --strip-pattern REGEX`** with ReDoS guard — rejects nested unbounded quantifiers + DOTALL back-ref shapes including escape-shape variants (`(\w+)+x`); 5-second SIGALRM per-pattern budget on POSIX, clamped to `min(timeout_s, previous_remaining)` so nested calls cannot extend an outer deadline.
- **`forgelm ingest --page-range START-END`** restricts PDF extraction to a 1-indexed slice. New `IngestParameterError(ValueError)` propagates through the per-file soft-fail catch to `EXIT_CONFIG_ERROR (1)`. Error messages `repr`-escape paths (`{path!r}`) to prevent ANSI / control-char injection.
- **Front-matter / back-matter heuristic** (alpha < 0.30 + leader ratio > 0.10 covering both underscore + dot runs + ≥ 5 inline page-number matches) — default-on, opt-out via `--keep-frontmatter`. Calibrated for the audit's Turkish-pilot ToC shape.
- **`forgelm ingest --strip-urls {keep,mask,strip}`** with bounded character class (no truncation), independent of `--all-mask`.
- **Multi-column PDF detection** (warning only) via pypdf's `visitor_text` callback sampling.
- **Notebook UX alignment** — `notebooks/ingestion_playground.ipynb` Cells 5 / 8 / 9 / 10 rewritten with token-aware mode knobs (fail-fast on partial config), quality-filter description, explicit `--quality-filter`, `quality_summary` pretty-print.

### Public surface changes

- 12 new `forgelm ingest` flags + audit `--quality-filter` default-on flip.
- `IngestionResult` gained 5 additive fields (`pdf_paragraph_packed_lines_stripped`, `script_sanity_triggered`, `strip_pattern_substitutions`, `urls_handled`, `frontmatter_pages_dropped`). No pre-Phase-15 key was renamed.
- `__api_version__` stays at `1.0.0` — no new stable library symbol added to `forgelm.__all__`. `__version__` bumps 0.5.7 → 0.6.0 (MINOR).

### Full changelog

See [CHANGELOG.md `[0.6.0]`](../../CHANGELOG.md#060--2026-05-11) for the complete list of additions, changes, and fixes (including the five review-absorption-round details).

---

## v0.5.7 — "SFT trainer trl-modernisation fix" (2026-05-10)

**Status:** Released to PyPI 2026-05-10. Patch on top of v0.5.6. GitHub Release: [v0.5.7](https://github.com/HodeTech/ForgeLM/releases/tag/v0.5.7).

### Summary

Fixes a `TypeError` in the SFT trainer that prevented every SFT training run from starting on modern `trl` (0.13+ and the 1.x line). The `max_seq_length` parameter was renamed to `max_length` on `SFTConfig` upstream; v0.5.6 still passed the old name unconditionally, so `forgelm --config <yaml>` crashed at trainer-args build time on any environment that pulled a current trl wheel — notably the Colab default `pip install forgelm` path.

### Highlights

- **Runtime signature detection** — `_get_training_args_for_type` now inspects `SFTConfig.__init__` and picks `max_length` (trl 0.13+) or `max_seq_length` (trl 0.12.x) at runtime, so both ends of the supported range (`trl>=0.12.0,<2.0.0`) work without intervention.
- **Three new regression tests** in `tests/test_trainer_sft_config.py` pin the modern-trl path, the legacy-trl path, and that `packing` / `dataset_text_field` continue to be propagated.
- **No effect on DPO / SimPO / KTO / ORPO / GRPO trainers** — those `*Config` parameter sets were not affected by the trl 0.13 rename.

### Full changelog

See [CHANGELOG.md `[0.5.7]`](../../CHANGELOG.md#057--2026-05-11).

---

## v0.5.6 — "Intel Mac install fix" (2026-05-10)

**Status:** Released to PyPI 2026-05-10. Patch on top of v0.5.5. GitHub Release: [v0.5.6](https://github.com/HodeTech/ForgeLM/releases/tag/v0.5.6).

### Summary

Reverts the v0.5.5 `torch>=2.3.0` floor back to `torch>=2.2.0`. The 2.3 floor was inaccurate (no v2.3-specific PyTorch API is referenced in production code) and made `pip install forgelm` silently downgrade existing users to v0.5.0 on Intel Mac (x86_64) hosts, where PyPI has no `torch>=2.3` wheel. v0.5.6 restores Intel Mac installability without losing any v0.5.5 functionality.

### Highlights

- **`pyproject.toml`** — `torch>=2.3.0,<3.0.0` → `torch>=2.2.0,<3.0.0`. No other dependency changes.
- **Intel Mac (x86_64) installability restored** — `pip install -U forgelm` from a v0.5.0 install now correctly upgrades to v0.5.6 instead of silently staying on v0.5.0.
- **Fix is dependency-only** — every v0.5.5 feature (Library API, GDPR purge / reverse-pii, ISO/SOC 2 alignment, operational subcommands, CLI wizard parity) is unchanged in v0.5.6.

### Full changelog

See [CHANGELOG.md `[0.5.6]`](../../CHANGELOG.md#056--2026-05-10).

---

## v0.5.5 — "Closure Cycle Bundle + Phase 22 Wizard + Site Documentation Sweep" (2026-05-10)

**Status:** Released to PyPI 2026-05-10 via the cross-OS publish workflow ([`.github/workflows/publish.yml`](../../.github/workflows/publish.yml)) which gates PyPI publish on 12 wheel-install matrix combos (3 OS × 4 Python). GitHub Release: [v0.5.5](https://github.com/HodeTech/ForgeLM/releases/tag/v0.5.5).

### Summary

v0.5.5 promotes ForgeLM from a CLI fine-tuning tool to a complete enterprise pipeline. The release ships a stable Python library API for downstream embedders, GDPR Article 15 + 17 tooling (`forgelm reverse-pii` + `forgelm purge`), an environment / supply-chain / verification toolbelt of operational subcommands (`doctor`, `cache-models`, `cache-tasks`, `safety-eval`, `verify-audit`, `verify-annex-iv`, `verify-gguf`, `approve` / `reject` / `approvals`), the ISO 27001 / SOC 2 Type II alignment artefacts (93-control deployer cookbook + 4 new QMS docs + bilingual mirror sweep), a CLI wizard surface that reaches parity with the in-browser counterpart, and a tag-driven cross-OS release pipeline with per-combo CycloneDX SBOM. Every claim on `forgelm.dev` was re-validated against the live code; the `forgelm/cli.py` and `forgelm/data_audit.py` monoliths were split into focused sub-packages while preserving their public import surface.

### Highlights

- **Library API (`forgelm.__all__`)** — every CLI surface has a stable Python entry point with PEP 561 typing (`py.typed`), lazy-import facade (`import forgelm` does not pull `torch`), and `__api_version__` decoupled from the CLI `__version__`.
- **GDPR Article 17 (`forgelm purge`)** — three-mode dispatcher (row erasure / run-scoped artefact / read-only policy report) with per-output-dir-salted SHA-256 audit events; `RetentionConfig` Pydantic block with four configurable horizons.
- **GDPR Article 15 (`forgelm reverse-pii`)** — locate identifier matches across JSONL artefacts; literal / email / phone / regional-id / regex modes; identifier salted-and-hashed before audit emission.
- **Operational subcommands** — `forgelm doctor` (env / GPU / CUDA / extras pre-flight + JSON envelope), `cache-models` + `cache-tasks` (air-gap pre-cache for HF Hub + lm-eval), `safety-eval` (standalone Llama Guard with bundled 50-prompt × 14-category default probes), `verify-audit` / `verify-annex-iv` / `verify-gguf` (compliance + artefact integrity toolbelt), `approve` / `reject` / `approvals` (Article 14 staging-gate management).
- **CLI wizard parity-with-web** — same 9-step flow as `forgelm.dev/quickstart`, schema-driven defaults shared between the two surfaces (CI guard fails on drift), idempotent re-run via `--wizard-start-from <yaml>`, distinct `EXIT_WIZARD_CANCELLED = 5` exit code (additive; public surface now `0–5`).
- **ISO 27001 / SOC 2 Type II alignment** — 93-control deployer cookbook ([`docs/guides/iso_soc2_deployer_guide.md`](../guides/iso_soc2_deployer_guide.md)), 4 new QMS docs (encryption at rest, access control, risk treatment plan, statement of applicability) with 10 new TR mirrors, 2 new reference tables.
- **Supply-chain security** — CycloneDX 1.5 SBOM per release-tag matrix combo, `pip-audit` + `bandit` nightly + on-tag (HIGH/CRITICAL → exit 1, MEDIUM → warning), opt-in `gitleaks` pre-commit, new `[security]` extra.
- **Cross-OS release-tag matrix** — `publish.yml` runs Linux + macOS + Windows × Python 3.10 / 3.11 / 3.12 / 3.13 = 12 combos before PyPI publish; OIDC trusted publishing.
- **Doc CI guards** — bilingual parity (40 pairs), anchor resolution, CLI ↔ docs help consistency, no-analysis-refs, wizard-defaults-sync, Pydantic field-description (all `--strict`).
- **`forgelm/cli/` + `forgelm/data_audit/` package splits** — legacy 2300-line + 3098-line monoliths decomposed into 24-module + 14-module sub-packages while preserving public import surface. 16 broad `except Exception` sites narrowed; 6 enum-shaped config fields tightened to `Literal[...]`.
- **Site documentation correction sweep** — every visible YAML / artefact-path / CLI / schema claim on `site/*.html` validated against the live `forgelm/` surface; `i18n` parity at 731 keys per locale across EN + TR + DE + FR + ES + ZH.

### Breaking changes (deliberate)

- High-risk / unacceptable `risk_classification` combined with `evaluation.safety.enabled=false` now raises `ConfigError` at config-load time (was a warning). EU AI Act Article 9 risk-management evidence cannot be derived from a disabled safety eval.
- `WebhookConfig.timeout` default raised from 5s to 10s. Slack/Teams gateway latency spikes regularly cross 5s; webhook failure is best-effort but a timeout silently degrades the audit chain.
- `--data-audit` flag fully removed (was deprecated in v0.5.0). Use the `forgelm audit` subcommand instead.

### Full changelog

See [CHANGELOG.md `[0.5.5]`](../../CHANGELOG.md#055--2026-05-10) for the complete list of additions, changes, fixes, deprecations, and removals.

---

## v0.7.0 — "Pipeline Chains" (2026-05-15)

**Status:** Released to PyPI 2026-05-15 via the cross-OS publish workflow ([`.github/workflows/publish.yml`](../../.github/workflows/publish.yml)) — all 12 wheel-install matrix combos (3 OS × 4 Python) green before OIDC trusted publish. GitHub Release: [v0.7.0](https://github.com/HodeTech/ForgeLM/releases/tag/v0.7.0).

### Summary

v0.7.0 ships [Phase 14 — Multi-Stage Pipeline Chains](completed-phases.md#phase-14--multi-stage-pipeline-chains-v070): one YAML, one CLI invocation, one Annex IV manifest covering SFT → DPO → GRPO (or any sequence of supported trainers).  Re-scheduled from v0.6.0 → v0.7.0 because v0.6.0 shipped Phase 15 (Ingestion Pipeline Reliability) after the 2026-05-11 pilot exposed the silent-failure gap that gated v0.6.0's credibility.  v0.7.0 also folds in the critical DNS-rebinding TOCTOU SSRF hardening (issue #14) on the webhook / judge / synthetic outbound paths.

### Highlights

- **`pipeline:` config block** at the root of `ForgeConfig` chains one or more training stages with section-wholesale inheritance (`model` / `lora` / `training` / `data` / `evaluation`) and root-only enforcement for cross-stage concerns (`distributed` / `webhook` / `compliance` / `risk_assessment` / `monitoring` / `retention` / `synthetic` / `merge` / `auth`).  New Pydantic models `PipelineStage` + `PipelineConfig` enforce stage-name uniqueness, `^[a-z0-9_]{1,32}$` identifier shape, ≥1-stage minimum, and explicit-`trainer_type` audit-clarity validation per stage.
- **Pipeline orchestrator** (`forgelm/cli/_pipeline.py`) drives the chain end-to-end with auto-chained `model.name_or_path`, atomic state persistence to `<pipeline.output_dir>/pipeline_state.json`, and **7 new pipeline-scoped audit events** all sharing a single top-level `run_id` for SIEM-style grouping: `pipeline.started`, `pipeline.stage_started`, `pipeline.stage_completed`, `pipeline.stage_gated`, `pipeline.stage_reverted`, `pipeline.force_resume`, `pipeline.completed`.
- **CLI flags** `--stage <name>`, `--resume-from <name>`, `--force-resume`, `--input-model <path>`.  `--dry-run` collects every per-stage validation error before exiting + runs the cross-stage `training.output_dir` collision guard as a pre-flight.  Pipeline-only flags rejected on non-pipeline configs (and vice versa) with explicit `EXIT_CONFIG_ERROR`.
- **Pipeline Annex IV manifest** (`compliance/pipeline_manifest.json`) + `forgelm verify-annex-iv --pipeline <run_dir>` mode.  Structural + chain-integrity verification, per-stage `training_manifest.json` existence check.
- **Webhook integration**: `WebhookNotifier.notify_pipeline_started / _completed / _reverted` mirror the orchestrator's audit events; pre-existing `training.*` consumers see no new events on non-pipeline runs.
- **Security — DNS-rebinding TOCTOU SSRF hardening (issue #14)** — `_resolve_safe_destination` resolves the hostname exactly once and rebuilds the outbound URL with the returned public IP literal so `requests` never re-resolves at connect time; original hostname preserved via `Host` header + SNI using `requests_toolbelt.adapters.host_header_ssl.HostHeaderSSLAdapter`.  `requests-toolbelt>=1.0.0,<2.0.0` is now a hard dependency.
- **Documentation surface**: bilingual operator guide (`docs/guides/pipeline.md` + `pipeline-tr.md`), sidebar user-manual page (`docs/usermanuals/{en,tr}/training/pipelines.md`), `docs/reference/configuration.{md,-tr.md}` schema reference, `docs/reference/usage.{md,-tr.md}` CLI surface, site marketing surface refresh (features card, 4th hero slide, 6-language i18n).

### Public surface changes

- New `pipeline:` config block (optional — omitting it preserves v0.6.0 byte-identical single-stage path; orchestrator module is never imported on non-pipeline configs).
- New CLI flags: `--stage`, `--resume-from`, `--force-resume`, `--input-model`; new verify mode: `verify-annex-iv --pipeline`.
- `__api_version__` stays at `1.0.0` — Phase 14 added symbols only inside `forgelm.compliance` / `forgelm.config` submodules; none re-exported in `forgelm.__all__`.  `__version__` bumps `0.6.0 → 0.7.0` (MINOR).
- Deprecation cadence: `--data-audit PATH` removal target pushed from v0.7.0 → **v0.8.0** to preserve the one-minor warning window (per `docs/standards/release.md#deprecation-cadence`).

### Review-absorption history

PR #53 (Phase 14 implementation) absorbed **5 review rounds**: 3 blocking + 4 significant + 14 nitpicks across dispatch order, force-resume audit event, strict chain integrity, `--input-model` empty-string normalisation, exit-code consistency, `--stage <non-first>` chain integrity, audit-run-id pinning, topology guard unconditional execution, `output_dir` collision in `run()`, pipeline-only-flag rejection on non-pipeline configs, reference docs + roadmap state cleanup.  PR #54 (release prep) absorbed an additional **10 of 14 findings** (3 blockers + 5 HIGH + 2 MEDIUM); the remaining 4 are tracked as [Phase 14.5 — Pipeline Hardening](phase-14-5-pipeline-hardening.md) for the v0.7.x cycle.

### Full changelog

See [CHANGELOG.md `[0.7.0]`](../../CHANGELOG.md#070--2026-05-14) for the complete list of additions, changes, fixes (including the review-absorption-round details), and the SSRF Security entry.

---

## v0.7.x — "Pipeline Hardening" (Planned)

**Status:** Planned. Focus: [Phase 14.5](phase-14-5-pipeline-hardening.md).  Four review-deferred items from v0.7.0: canonical pipeline manifest hash + non-chain-field tamper detection, per-stage `training_manifest.json` deep-parse validation, webhook `pipeline.*` event vocabulary documentation, `WebhookNotifier._send(**extra)` explicit allowlist.  Targets the v0.7.x patch cycle (v0.7.1 or split across v0.7.1 / v0.7.2 depending on bandwidth).

---

## v0.6.0-pro — "Pro CLI" (Planned, gated)

Focus: [Phase 13](phase-13-pro-cli.md). Gated on traction validation — do not ship before `v0.6.0` reaches ≥1K monthly PyPI installs and ≥2 paying support contracts. The ISO 27001 / SOC 2 baseline shipped in `v0.5.5` underpins the Pro CLI's enterprise audit story.
