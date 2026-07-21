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

**Correction (2026-07-19):** this section previously listed a third bullet claiming the `--data-audit` flag was "fully removed (was deprecated in v0.5.0)" at v0.5.5. That was never true — CHANGELOG [`[0.5.5]`](../../CHANGELOG.md#055--2026-05-10) `### Deprecated` shows the flag still just emitting a `DeprecationWarning` at this release, with removal "scheduled for v0.7.0" at the time. `--data-audit` remained a working deprecation alias through v0.5.5, v0.6.0, and v0.7.0 and was not actually removed until **v0.8.0** — see the [v0.8.0 `### Removed`](#v080--model-integrity-verification--deprecation-cleanup-2026-06-16) entry below. The bullet is removed rather than corrected in place since no v0.5.5 change to `--data-audit` was breaking.

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

PR #53 (Phase 14 implementation) absorbed **5 review rounds**: 3 blocking + 4 significant + 14 nitpicks across dispatch order, force-resume audit event, strict chain integrity, `--input-model` empty-string normalisation, exit-code consistency, `--stage <non-first>` chain integrity, audit-run-id pinning, topology guard unconditional execution, `output_dir` collision in `run()`, pipeline-only-flag rejection on non-pipeline configs, reference docs + roadmap state cleanup.  PR #54 (release prep) absorbed an additional **10 of 14 findings** (3 blockers + 5 HIGH + 2 MEDIUM); the remaining 4 were tracked at the time as [Phase 14.5 — Pipeline Hardening](phase-14-5-pipeline-hardening.md), targeting what was then the next open cycle, `v0.7.x`.  That cycle closed with both v0.8.0 and v0.9.0 shipping without it; Phase 14.5 now targets the `v0.9.x` cycle instead — see that file for current status.

### Full changelog

See [CHANGELOG.md `[0.7.0]`](../../CHANGELOG.md#070--2026-05-14) for the complete list of additions, changes, fixes (including the review-absorption-round details), and the SSRF Security entry.

---

## v0.8.0 — "Model Integrity Verification & Deprecation Cleanup" (2026-06-16)

**Status:** Released to PyPI 2026-06-16. Minor release on top of v0.7.0. GitHub Release: [v0.8.0](https://github.com/HodeTech/ForgeLM/releases/tag/v0.8.0).

### Summary

v0.8.0 adds standalone model-integrity verification, exposes previously-hardcoded merge and synthetic-data knobs as config fields, hardens two config validators that previously failed silently, and completes the deprecation cadence for two long-standing removals — `evaluation.staging_ttl_days` (deprecated v0.5.5) and the `--data-audit` CLI flag (deprecated v0.5.0) — both of which cleared the release standard's one-minor-minimum overlap by a wide margin; see the Removed section below for the exact per-field chain.

### Highlights

- **`forgelm verify-integrity MODEL_DIR`** — new subcommand and `forgelm.verify_integrity()` / `VerifyIntegrityResult` public API. Re-hashes a trained model directory against its EU AI Act Article 15 `model_integrity.json` SHA-256 manifest and reports `changed` / `removed` / `added` artifacts. Exit `0` (all match) / `1` (mismatch or input error) / `2` (runtime I/O failure); `--output-format json` for CI gates. Bumps `__api_version__` to `1.1.0`.
- **Config-driven merge hyperparameters** — `merge.ties_trim_fraction`, `merge.dare_drop_rate`, and `merge.dare_seed` expose the TIES/DARE knobs that were previously fixed module constants (defaults unchanged: `0.2`, `0.3`, `42`).
- **Config-driven synthetic sanity bound** — `synthetic.sanity_failure_rate` (default `0.2`) replaces the hardcoded warn-only failure-rate threshold in `forgelm --generate-data`; independent of `min_success_rate`, which still gates the exit code.
- **Config validation hardened** — `distributed.strategy` is now a `Literal["deepspeed", "fsdp"]` (an unsupported value such as `horovod` used to validate and then silently run single-GPU). `data.mix_ratio` now rejects non-finite weights (NaN / inf) and must carry exactly one weight per dataset; a length mismatch used to raise no config error and silently fall back to uniform mixing at runtime. Both now fail fast at config time (exit 1).

### Deprecated

- **`training.sample_packing`** becomes a deprecated alias for `training.packing` — it was previously a documented-but-unconsumed no-op field; it now forwards to `packing` with a `DeprecationWarning` so the documented behaviour actually fires. Removal target: `v1.0.0` (removing a YAML field is a MAJOR change — see [docs/standards/release.md](../standards/release.md#deprecation-cadence)).
- **Target history:** CHANGELOG [`[0.8.0]`](../../CHANGELOG.md#080--2026-06-16) originally announced this removal for **v0.9.0**. That promise could not be kept — removing a YAML field is a MAJOR change per the release standard, and no MINOR release may ship one — so the target drifted forward undocumented (`v0.9.0` → `v0.10.0`) before being formally corrected to the canonical `v1.0.0` stated above. <!-- deprecation-target-ok: names the v0.9.0/v0.10.0 targets v0.8.0-era docs once carried, both superseded by the canonical v1.0.0 in the bullet directly above. -->

### Removed

- **`evaluation.staging_ttl_days`** — removed. Use the canonical `retention.staging_ttl_days`; `EvaluationConfig` is `extra="forbid"`, so the legacy key now raises a validation error instead of forwarding. Deprecated in **v0.5.5**; a three-minor window. <!-- deprecation-target-ok: the version tokens here describe this field's own chain, not the deprecated-packing-alias target the guard tracks; they land in its claim window only because the "### Removed" heading sits within two lines of that bullet. -->
- **`forgelm --data-audit PATH`** CLI flag — removed. Use the first-class `forgelm audit PATH` subcommand (identical behaviour and output); `argparse` now rejects the flag (exit 2). Deprecated in **v0.5.0**; a three-minor window.
- **`cli.legacy_flag_invoked`** audit event — recorded use of the removed `--data-audit` flag; dropped from the audit-event catalog.

> **On the two deprecation dates above.** CHANGELOG's `[0.8.0]` entry says both fields were "deprecated in v0.7.0". That is wrong, and it is wrong for one reason applied twice: v0.7.0 is the version each field's *removal was scheduled for*, not the version it was deprecated in. The primary sources are CHANGELOG [`[0.5.5]`](../../CHANGELOG.md#055--2026-05-10) ("Removal scheduled for v0.7.0") and [`[0.5.0]`](../../CHANGELOG.md#050--2026-04-30) ("removal targeted no earlier than `v0.7.0`"). CHANGELOG is append-only, so its `[0.8.0]` bullet keeps the original wording and carries a dated errata note beneath it.
>
> **Counting rule:** minor hops are counted by the Y digit of `MAJOR.MINOR.PATCH`, per [`release.md`](../standards/release.md)'s versioning table and its deprecation-cadence example, which names only `Y.0` releases. A patch tag such as `v0.5.5` anchors to the `v0.5` line and adds no hop — so both windows are v0.5.x → v0.6.0 → v0.7.0 → v0.8.0, three minors, well past the standard's one-minor minimum.

### Fixed

- Eval artefact privacy-redaction (in effect since v0.7.0) is now documented in the CHANGELOG: `safety_results.json` / `judge_results.json` omit raw `prompt` / `response` / judge `reason` text unless the opt-in `include_eval_samples` flags are set.  The safety-side redaction set has since grown a third member, `raw_verdict` — the generative guard's own output, added alongside generation-based scoring — so enabling `evaluation.safety.include_eval_samples` now writes guard text to disk as well as probe text.
- A pipeline config combining `pipeline:` + `retention.staging_ttl_days` + any `evaluation:` block no longer raises a false `ConfigError` on the stage-merge round-trip.

### Security

- **Nightly pip-audit gate — transformers PYSEC-2025-217 / CVE-2025-14929.** Advisory records an X-CLIP checkpoint-conversion deserialization RCE (CVSS AV:L/UI:R — local + user-interaction required); no fixed version existed in the `transformers<5.0.0` range at the time. Codebase check 2026-05-24 found no X-CLIP usage in `forgelm/` and no direct `torch.load` calls; risk accepted in `tools/pip_audit_ignores.yaml`, re-evaluated each release cycle. This suppression became moot once v0.9.0 raised the `transformers` floor past the affected range and removed it.

### Public surface changes

- New CLI subcommand `forgelm verify-integrity`; new public API `forgelm.verify_integrity()` / `VerifyIntegrityResult`. `__api_version__` bumps `1.0.0 → 1.1.0` (new stable library symbol added to `forgelm.__all__`). `__version__` bumps `0.7.0 → 0.8.0` (MINOR).
- Schema removal: `evaluation.staging_ttl_days` (deprecated v0.5.5) and `--data-audit PATH` (deprecated v0.5.0) are both gone — see the Removed section above for the full per-field chain and dates; neither followed a "one-minor warning window opened in v0.7.0" as an earlier version of this entry claimed.

### Full changelog

See [CHANGELOG.md `[0.8.0]`](../../CHANGELOG.md#080--2026-06-16) for the complete list of additions, changes, deprecations, removals, and fixes.

---

## v0.11.0 — "The Front Door" (2026-07-21)

**Status:** Released — a MINOR bump carrying two **breaking** changes (see `### Breaking` in [`CHANGELOG.md`](../../CHANGELOG.md)): `forgelm audit` now exits `3` on a critical-tier PII finding (credit card / IBAN) where it exited `0`, and the `[distributed]` extra now installs nothing on Windows (marked `sys_platform != 'win32'`) instead of failing the whole install on a source build.

Focus: the README — the project's highest-traffic document and the one surface no CI guard could see.

### The README audit

A four-agent audit against the code, with independent verification, found **fourteen claims that did not survive execution** — a broken `forgelm export` command in the Quick Start, auto-revert described as skipping a save when it deletes the model directory, a Croissant card sold as doubling as the Article 10 artefact, an unkeyed SHA-256 manifest called "proof-of-integrity", "every CLI surface has a typed entry point" when half raise `AttributeError`, and a `revision`-field list naming the tokenizer instead of the judge model. The root cause was structural: the README sat outside the scope of every guard that keeps `docs/` honest. `tools/check_readme_links.py` (the 29th guard) now enforces PyPI-safe absolute links, and `check_doc_numerical_claims.py` derives and enforces the test-module and CI-guard counts on the README.

### The PII gate, and two review rounds

The critical-tier PII gate began as a straightforward mirror of the v0.10.0 secrets gate. An Opus review found it fired on clean numeric corpora — Luhn alone clears ~9.8% of 16-digit runs and every IMEI by construction — so an issuer-prefix (IIN) requirement was added. A Sonnet second pass, chartered to scrutinise that fix, found the tightening had over-narrowed and silently dropped Diners Club, Maestro, Mir and part of the Discover range. The shipped table re-covers the low-collision brands (net false-positive rate ~1.1%) while still excluding IMEIs; Maestro is documented as a deliberate omission. Three independent agents converged on the same precision/recall balance.

### Also

- **`[tracking-mlflow]` extra** — `report_to: "mlflow"` was an accepted config value whose documented install path installed only `wandb`; MLflow now has its own extra and a `forgelm doctor` probe row.
- **Auto-revert drift swept** — "restores a previous checkpoint" was corrected in seven places (it deletes), including two `config.py` field descriptions that reach the generated Configuration Reference.
- **The Unsloth "2-5× faster" figure** — an unsourced upstream number restated as fact across 18 sites in two languages — was removed where bare and attributed where kept.

`__api_version__` stays at `1.1.0` — no stable library symbol added or changed. Test count 4460 → 4560; 28 → 29 CI guards.

### Full changelog

See [CHANGELOG.md `[0.11.0]`](../../CHANGELOG.md#0110--2026-07-21).

---

## v0.10.0 — "Promises Kept" (2026-07-20)

**Status:** Released — a MINOR bump carrying one **breaking** behaviour change (see `### Breaking` in [`CHANGELOG.md`](../../CHANGELOG.md)): `forgelm audit` now exits `3` when its secrets scan finds a credential, where it previously printed `Secrets : CRITICAL — N flagged` and exited `0`. Any CI step wired up as a credential-leak gate had a gate that could not fire; it fires now.

Focus: closing every item this project had promised and not delivered, across seven steps with an Opus and a Sonnet review round each.

### Security

- **The safety gate could return PASS for a model it had scored wrong.** Generation-mode verdict parsing accepted any first line *beginning with* `safe`, so a checkpoint that is not a guard — one replying `SAFETY: this is harmful` — cleared the gate, auto-revert did nothing, and the compliance artefact recorded that safety had been checked. If you ran `evaluation.safety` with `classifier_mode: generation` against anything other than a real Llama-Guard, re-run it: a PASS from before this release is not evidence.
- **The `forgelm audit` credential-leak gate had never fired.** See the breaking note above.
- **Destroyed Annex IV evidence could be certified clean.** Deleting a stage's evidence, marking the stage skipped and re-stamping the manifest hash produced exit `0`. The stage census is now cross-checked against the HMAC-protected audit log.

### Added

- **`EXIT_INTEGRITY_FAILURE = 6`** — a tampered artefact and a mistyped path no longer share exit `1`.
- **HF revision pinning** — five optional `revision` fields, plus provenance recording that distinguishes a verified pin from a best-effort one.
- **`python -m forgelm`**, and `--max-safety-regression` on `safety-eval`.
- **`forgelm audit --allow-secrets`** for auditing a corpus before scrubbing it.

### Changed

- **`forgelm/verify.py`** now owns the verification primitives; **`forgelm/safety/`** is a nine-module package. Public import paths are unchanged.
- Pipeline verification deep-parses each completed stage's Annex IV evidence instead of checking the file exists.

### Fixed

- **`verify-annex-iv --pipeline` raised a tamper alarm on every clean run** — the orchestrator recorded an evidence path no writer produced.
- **The dataset SHA recorded for provenance was obtained independently of the load**, so it could name a commit the run never used.
- Auto-revert no longer deletes a model when the safety evaluation produced no usable evidence.

Six new CI guards landed alongside: import origin, source-path references, CLI exit-code prose, skill-mirror parity, release-record sync, and a LOC-budget ratchet replacing a version-labelled deferral list. Test count 3374 → 4460.

## v0.9.0 — "transformers 5.x Migration & CVE-2026-4372 Fix" (2026-07-05)

**Status:** Released to PyPI 2026-07-05. Minor release on top of v0.8.0 — **breaking in effect: drops Intel Mac (x86_64) support** (the `transformers>=5.3.0` floor pulls `torch>=2.4.0`, for which PyPI ships no x86_64-Darwin wheel; Apple Silicon, Linux, and Windows are unaffected). GitHub Release: [v0.9.0](https://github.com/HodeTech/ForgeLM/releases/tag/v0.9.0).

### Summary

v0.9.0 raises the `transformers` dependency floor to `>=5.3.0,<6.0.0` — the first release carrying the fix for **CVE-2026-4372**, a critical `AutoModelForCausalLM.from_pretrained()` remote-code-execution vulnerability — and cascades the co-dependency floors that transformers 5.x requires (`torch`, `huggingface_hub`, `peft`, `accelerate`, `datasets`, `trl`, `requests`). Dropping transformers 4.x support pulls `torch>=2.4.0` along with it, and PyPI publishes no `torch>=2.4` wheel for Intel Mac (x86_64) — **that platform can no longer install ForgeLM's core stack.** Apple Silicon, Linux, and Windows are unaffected.

### Breaking changes

- **transformers 5.x required.** Floor raised to `transformers>=5.3.0,<6.0.0`; transformers 4.x support removed. Cascaded co-dependency floors: `torch>=2.4.0`, `huggingface_hub>=1.3.0,<2.0.0`, `peft>=0.19.0`, `accelerate>=1.4.0`, `datasets>=4.7.0,<6.0.0`, `trl>=1.0.0`, `requests>=2.32.2`.
- **Intel Mac (x86_64) support dropped.** transformers 5 requires `torch>=2.4`, for which PyPI publishes no x86_64-Darwin wheel, so that platform can no longer install ForgeLM. Apple Silicon, Linux, and Windows are unaffected. The now-moot `numpy<2; darwin x86_64` ABI-guard marker was removed with it.
- **`from_pretrained` dtype kwarg renamed.** `torch_dtype=` → `dtype=` at the two base-model load sites (`export.py`, `synthetic.py`); `torch_dtype` is a deprecated alias in transformers 5 slated for removal. Behaviour is unchanged.
- **`safe_serialization=True` dropped** from the three model-save call sites (`export.py`, `merging.py`, `trainer.py`) — safetensors is the enforced default in transformers 5, so the kwarg no longer applies. On-disk output is unchanged.

### Security

- **CVE-2026-4372** — a critical `AutoModelForCausalLM.from_pretrained()` RCE in transformers <5.3.0 (a malicious `config.json` `_attn_implementation_internal` field downloads and executes attacker code, bypassing `trust_remote_code`) — is resolved by the `transformers>=5.3.0` floor above. Two now-inert transformers suppressions in `tools/pip_audit_ignores.yaml` (`CVE-2026-1839`, fixed in 5.0.0rc3; `PYSEC-2025-217` / `CVE-2025-14929`, the X-CLIP RCE) were removed.

### Public surface changes

- `__api_version__` stays at `1.1.0` — no stable library symbol added or changed. `__version__` bumps `0.8.0 → 0.9.0` (MINOR release, though the dropped-platform-support change is breaking in effect for Intel Mac operators — see the Breaking changes section above).

### Full changelog

See [CHANGELOG.md `[0.9.0]`](../../CHANGELOG.md#090--2026-07-05) for the complete list of dependency-floor changes and the CVE fix.

---

## v0.9.x — "Pipeline Hardening" (Planned)

**Status:** Merged on main, publish pending. Focus: [Phase 14.5](phase-14-5-pipeline-hardening.md).  All four review-deferred items from v0.7.0 are now closed.  Three landed here and sit under `[Unreleased]` in [`CHANGELOG.md`](../../CHANGELOG.md): per-stage evidence deep-parse validation, the canonical webhook vocabulary reference, and the `WebhookNotifier._send(**extra)` explicit allowlist.  The fourth — the canonical pipeline manifest hash + non-chain-field tamper detection (`F-PR54-H6`) — **shipped in v0.8.0** (commit `e7c3321`, 2026-06-14) as `F-P4-OPUS-20`; the Phase 14.5 row was never closed, so it was carried forward and briefly recorded as part of this delivery.  This cycle contributed its documentation, not its behaviour.  Originally targeted the v0.7.x patch cycle; that cycle closed with v0.8.0 and v0.9.0 shipping without the remainder, so the rest landed on the v0.9.x cycle instead.

Three scope notes an auditor should not have to reconstruct.  **A compliance signal was inverted and is now corrected.**  Before this cycle, `forgelm verify-annex-iv --pipeline` exited `6` on *every* clean pipeline run: the orchestrator recorded each stage's Annex IV evidence as `training_manifest.json`, a filename no ForgeLM version has ever written, so the pointer always dangled.  The first fix was reader-side only and made things worse — because a dangling pointer and a deleted file are indistinguishable, *deleted* evidence routed to exit `1` while *corrupted* evidence routed to exit `6`, and the message asserted "a writer defect, not tampering".  Deleting Annex IV evidence is archetypal Article 12 tampering and is more severe than corrupting it.  The writer now records `annex_iv_metadata.json`, the legacy basename resolves only for chain manifests written before `0.9.1`, and missing evidence routes on whether the run configured a `compliance:` block (configured → `6`, unconfigured → `1`).  An operator who suppressed this alarm should re-enable it.  **Exit-code mapping changed from the plan.** Phase 14.5's task descriptions predate `EXIT_INTEGRITY_FAILURE = 6` and route manifest-hash and per-stage failures to `EXIT_CONFIG_ERROR (1)`; as delivered, a recomputed digest that disagrees — or per-stage evidence that is zero-byte, malformed, oversize, symlinked, escaping, or missing a required Annex IV field — exits `6`, and a new `UNVERIFIED::` routing token carries the genuinely different "reached the evidence, nothing attested to it" case at `1`.  **Task 5 is not part of this delivery, and is itself closed as NOT SCHEDULED.** The SonarCloud S3776 cognitive-complexity refactor was appended to the phase file after its creation and was never one of the four v0.7.0 deferrals.  Re-measurement against the tree found every load-bearing claim in the entry wrong — it named six breaching functions where an in-repo AST approximation finds ~46, omitted the two worst (`ingest_path` ~73, `verify_integrity` ~37), listed one (`_parse_webhook_value`) that no longer breaches, carried stale `file:line` references throughout, and set its acceptance criterion against a SonarCloud scan that no workflow in this repo runs.  The refactor is declined on merit (a wide mechanical diff across the SSRF chokepoint, the wizard orchestrator and the ingestion path for zero correctness gain), and the item is gated on a stated condition rather than a version: either Sonar is wired into a workflow, or an in-repo `ast` cognitive-complexity ceiling lands under `tools/` as a ratchet in the shape of `check_module_size.py`'s per-file budgets.  See [`phase-14-5-pipeline-hardening.md`](phase-14-5-pipeline-hardening.md) Task 5 for the full re-measurement.

---

## v0.6.0-pro — "Pro CLI" (Planned, gated)

Focus: [Phase 13](phase-13-pro-cli.md). Gated on traction validation — do not ship before `v0.6.0` reaches ≥1K monthly PyPI installs and ≥2 paying support contracts. The ISO 27001 / SOC 2 baseline shipped in `v0.5.5` underpins the Pro CLI's enterprise audit story.
