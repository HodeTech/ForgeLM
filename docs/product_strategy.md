# ForgeLM Product Strategy

> Last Updated: 2026-04-25
> Purpose: Define ForgeLM's market position, differentiation, target users, and strategic direction.

---

## Mission Statement

ForgeLM makes LLM fine-tuning **safe, auditable, and repeatable** — config-driven, CI/CD-native, runs anywhere.

---

## The Three Pillars

### 1. Safety-First

Fine-tuning can degrade a model's safety properties — silently. ForgeLM treats safety evaluation as a first-class pipeline stage, not an optional add-on.

- **Llama Guard 3** safety evaluation runs inside the training pipeline (not post-hoc)
- **3-layer safety gate**: binary pass/fail + confidence-weighted score + per-category severity analysis covering 14 harm categories (S1-S14)
- **Auto-revert**: if safety regression is detected against baseline, training output is automatically rejected before artifacts are written
- **Cross-run trend tracking**: safety scores are logged per-run and trended over time to catch gradual drift
- **`auto_revert_on_safety_failure`** is a first-class config field — teams can enforce safety gates in CI without any custom scripting

### 2. Compliance-Native

As of August 2, 2026, EU AI Act applies to high-risk AI systems. ForgeLM is the only open-source fine-tuning tool that generates compliance evidence bundles automatically as a byproduct of a normal training run.

- **EU AI Act Articles 9-17 + Annex IV** — each article maps to a structured output artifact:
  - Art. 9 Risk Assessment → `risk_assessment.json`
  - Art. 10 Data Governance → `data_governance_report.json`
  - Art. 11 Technical Documentation → `annex_iv_metadata.json`
  - Art. 12 Record-Keeping → `audit_log.jsonl`
  - Art. 13 Transparency → `deployer_instructions.md`
  - Art. 14 Human Oversight → `require_human_approval: true` + exit code 4
  - Art. 15 Accuracy & Robustness → `model_integrity.json` + safety evaluation results
- **Tamper-evident audit log**: SHA-256 hash chain — each entry includes the hash of the previous entry; deletion or modification is detectable
- **Human approval gate**: exit code 4 signals "awaiting human approval" to CI/CD orchestrators; the final model lands in `final_model.staging/` — operators run `forgelm approve <run_id>` to promote (atomic rename to `final_model/`) or `forgelm reject <run_id>` to discard, with both decisions recorded in the audit chain

### 3. Config-Driven CI/CD

ForgeLM's core architectural identity: a YAML file fully describes a training run. No imperative code required. No environment variable hunting. No hidden runtime state.

- **Single YAML in, fine-tuned model + compliance artifacts out** — deterministic and version-controllable
- **Meaningful exit codes** for pipeline orchestration: 0 (success), 1 (config/validation error), 2 (training error), 3 (evaluation/safety failure), 4 (awaiting human approval)
- **`--dry-run`** validates the full pipeline — config, data, model loading — without allocating a GPU
- **Structured JSON output** on all evaluation and compliance steps — parseable by any downstream system
- **Docker-native**: official multi-stage images for GPU and CPU; offline/air-gapped mode supported
- **Git-friendly**: YAML configs diff cleanly; compliance artifacts are plain JSON/Markdown

---

## Target Users

### Primary: MLOps / Platform Engineers

**Profile:** Build and maintain automated training pipelines; operate in GitOps/CI environments; need headless, deterministic execution.

**Why ForgeLM:** Meaningful exit codes map directly to pipeline branch logic. `--dry-run` validates config before queuing a GPU job. Structured JSON output integrates with observability platforms. Webhook notifications (Slack/Teams) hook into existing incident workflows.

**Representative workflow:** `git push → GitHub Actions triggers → forgelm --config job.yaml → exit code → deploy or alert`

### Secondary: ML Engineers at Regulated Industries

**Profile:** Banking, healthcare, defense, government. Cannot send proprietary data to external APIs. Face EU AI Act (EU), KVKK/BDDK (Turkey), HIPAA (US), or equivalent regulations. Require on-premise or air-gapped execution with full audit trails.

**Why ForgeLM:** The only fine-tuning tool that produces a complete EU AI Act compliance evidence bundle automatically. On-premise Docker deployment. `trust_remote_code: false` by default. Human approval gate (exit code 4) defers production deployment; the final model lands in `final_model.staging/`; operators run `forgelm approve <run_id>` to promote, or `forgelm reject <run_id>` to discard. Audit log with SHA-256 hash chain satisfies record-keeping requirements.

### Tertiary: Independent Researchers and Developers

**Profile:** Want to fine-tune models without deep infrastructure knowledge; experimenting with SFT/DPO/GRPO; need sensible defaults.

**Why ForgeLM:** Wizard mode (`forgelm wizard`) guides configuration interactively. Quickstart templates (Phase 10.5) will provide working configs for the most common fine-tuning scenarios. The config-driven approach means runs are reproducible and shareable. 6 trainer methods (SFT/DPO/SimPO/KTO/ORPO/GRPO) in one tool.

---

## What ForgeLM Is NOT

Explicitly out of scope — now and long-term:

- **A GUI or web application.** Config files are the interface. Users who want a web UI should use LLaMA-Factory or AutoTrain. ForgeLM's Pro CLI will add a dashboard for paying users, but the core tool is headless.
- **A cloud infrastructure provider.** ForgeLM trains models — it does not provision, manage, or bill for compute. Users bring their own GPU (on-premise, RunPod, Lambda Labs, AWS, etc.).
- **A model serving or inference platform.** ForgeLM outputs trained adapters and compliance artifacts. Deployment is the user's responsibility; hand off to vLLM, Ollama, TGI, or llama.cpp.
- **A general ML framework.** LLM fine-tuning only. Not computer vision, not classical ML, not pretraining from scratch.
- **A custom inference engine.** No custom CUDA kernels, no custom quantization implementations. ForgeLM delegates to bitsandbytes/AWQ/GPTQ/HQQ and existing runtimes.

---

## Competitive Position

| Dimension | ForgeLM | LLaMA-Factory | Unsloth | Axolotl |
|---|---|---|---|---|
| Safety eval (integrated) | ✅ Llama Guard S1-S14 | ❌ | ❌ | ❌ |
| EU AI Act compliance | ✅ Art. 9-17 + Annex IV | ❌ | ❌ | ❌ |
| CI/CD-native exit codes | ✅ 0/1/2/3/4/5/6 | Partial | ❌ | Partial |
| Web UI | ❌ (by design) | ✅ | ✅ Studio | ❌ |
| Speed optimization | Standard | Standard | ✅ 2-5x | Standard |
| Config-driven YAML | ✅ Full | Partial | ❌ | ✅ |
| Multi-GPU | ✅ DeepSpeed/FSDP | ✅ | ❌ | ✅ |
| Trainer methods | ✅ 6 (SFT/DPO/SimPO/KTO/ORPO/GRPO) | ✅ Many | Partial | Partial |
| Auto-revert on regression | ✅ | ❌ | ❌ | ❌ |
| Tamper-evident audit log | ✅ SHA-256 chain | ❌ | ❌ | ❌ |

**Net position:** ForgeLM is behind on raw speed (Unsloth) and model variety/GUI (LLaMA-Factory). These are deliberate tradeoffs. The safety + compliance axis is where ForgeLM is categorically ahead — not incrementally better, but the only tool doing it systematically.

---

## Strategic Decisions

### 1. Safety and Compliance as Primary Differentiator

**Decision date:** 2026-03-23

Safety evaluation and compliance artifact generation are not optional features — they are the core value proposition for the target market. ForgeLM does not compete on speed or model variety; it competes on trustworthiness and auditability.

**Implication:** Every new major feature is evaluated against whether it strengthens or dilutes this position. Features that add speed without improving safety/compliance are lower priority than features that deepen compliance coverage.

### 2. Config-Driven Always — No Web UI in Core

**Decision type:** Permanent architectural choice

The config-driven identity is what makes ForgeLM CI/CD-native, Git-friendly, and reproducible. A web UI would require state management, auth, and multi-user session logic that is incompatible with the headless, pipeline-first model. Pro CLI will add a dashboard for paying users; the OSS core stays headless.

### 3. Optional Dependencies as Extras

Heavy dependencies live under `[project.optional-dependencies]` in `pyproject.toml`: `qlora`, `unsloth`, `eval`, `tracking`, `distributed`, `merging`. Each raises `ImportError` with an explicit install hint when missing — no silent `None` fallbacks.

**Rationale:** Core installation must remain installable in seconds. Dependency conflicts are the #1 onboarding blocker for ML tools. Optional extras allow users to install exactly what they need.

### 4. Reliability Before Features

Phase 2.5 (reliability hardening) was inserted before new feature development — and this pattern repeats. Every new major capability ships with tests, documentation, and CI coverage before merge. "I'll add tests later" = the PR is not ready.

**Implication:** ForgeLM ships fewer features per quarter than competitors, but each feature is production-reliable. This is the right tradeoff for the regulated-industry target market.

### 5. EU AI Act Compliance as the Time-Sensitive Moat

EU AI Act full enforcement begins August 2, 2026. No other open-source fine-tuning tool generates compliance evidence bundles. This window — between now and when competitors add compliance features — is the primary enterprise sales opportunity.

**Implication:** Enterprise outreach must begin immediately, not after Phase 10+ is complete. The compliance moat is real but temporary; competitors will respond within 6-12 months of enforcement.

---

## Success Metrics

### Short-Term (by October 2026 — 6 months)

- EU AI Act enforcement (August 2) drives first qualified enterprise inquiries
- v0.4.0 (Post-Training Completion — inference handoff, chat, export) shipped
- v0.4.5 (Quickstart Layer — templates, wizard improvements) shipped
- 1,000+ GitHub stars
- First enterprise pilot contract signed (banking, healthcare, or government sector)
- YouTube Academy launched with 5+ videos

### Medium-Term (by April 2027 — 12 months)

- 5,000+ GitHub stars
- 3+ enterprise support contracts (Silver/Gold/Platinum tier)
- $50,000–$150,000 ARR
- ForgeLM Cloud MVP in private beta
- Pro CLI alpha available to paying users
- 3-5 active external contributors

### Long-Term (by April 2028 — 24 months)

- Recognized as the standard open-source tool for **safe, compliant LLM fine-tuning**
- 15,000+ GitHub stars
- $500,000+ ARR
- Multi-region ForgeLM Cloud in general availability
- 50+ community quickstart templates
- Potential acquisition interest from enterprise compliance platforms (OneTrust, ServiceNow AI Control Tower, SAS)

---

## Documentation Map

| Document | Purpose |
|---|---|
| [README.md](../README.md) | Project overview and quick start |
| [CONTRIBUTING.md](../CONTRIBUTING.md) | Contributor guide |
| [Roadmap](roadmap.md) | Phase-based execution plan |
| [Product Strategy](product_strategy.md) | Market position and strategic direction (this document) |
| [Architecture](reference/architecture.md) | System design and component details |
| [Configuration Guide](reference/configuration.md) | YAML parameter reference |
| [Usage Guide](reference/usage.md) | How to run training |
| [Data Preparation](reference/data_preparation.md) | Dataset format requirements |
| [Standards Index](standards/README.md) | Engineering standards and conventions |
| [Wizard Mode Design](design/wizard_mode.md) | Interactive configuration wizard design |
| [Blackwell Design](design/blackwell_optimized.md) | GB10/Blackwell optimization design |

All documents are available in Turkish (`*-tr.md`).
