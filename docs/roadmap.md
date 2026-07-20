# ForgeLM Roadmap

> **Configuration-driven, enterprise-grade LLM fine-tuning platform** — built on three principles: reliability before features, enterprise differentiation over feature parity, every capability config-driven and testable.

## Status at a glance

| Type | Phase | Status |
|-----|-------|--------|
| ✅ Done | [Phase 1-9](roadmap/completed-phases.md) | SOTA upgrades, evaluation, reliability, enterprise integration, ecosystem, alignment stack, safety, EU AI Act compliance (Articles 9-17 + Annex IV), advanced safety intelligence |
| ✅ Done | [Phase 10 — Post-Training Completion](roadmap/completed-phases.md) | `inference.py`, `chat`, `export` (GGUF), `--fit-check`, `deploy` — shipped `v0.4.0` |
| ✅ Done | [Phase 10.5 — Quickstart Layer & Onboarding](roadmap/completed-phases.md) | `forgelm quickstart <template>`, 5 bundled templates with seed datasets — shipped `v0.4.5` |
| ✅ Done | [Phase 11 + 11.5 + 12 + 12.5 — Document Ingestion & Data Curation Pipeline](roadmap/releases.md#v050--document-ingestion--data-curation-pipeline) | `forgelm ingest`, `forgelm audit`, PII regex + simhash dedup, LSH banding, streaming reader, PII severity tiers, wizard ingest+audit, MinHash LSH dedup, markdown splitter, code/secrets scan, quality heuristics, DOCX table preservation, `--all-mask`, Croissant 1.0, Presidio NER — shipped `v0.5.0` (PyPI 2026-04-30) |
| ✅ Done | [Phase 12.6 — Closure Cycle (37 content phases + 1 release tag = 38 entries across 5 waves)](roadmap/completed-phases.md) | Library API, GDPR purge + reverse-pii, ISO 27001 / SOC 2 alignment, doctor + cache subcommands, compliance verification toolbelt, bilingual mirror sweep + 4 CI guards, supply-chain security, cross-OS release matrix — bundled into `v0.5.5` (PyPI 2026-05-10) |
| ✅ Done | Phase 22 — CLI wizard parity with the in-browser surface | `forgelm --wizard` runs the same 9-step flow as the web wizard (welcome → use-case → model → strategy → trainer → dataset → training-params → compliance → evaluation), with idempotent re-run via `--wizard-start-from <yaml>`, schema-driven defaults SOT, distinct `EXIT_WIZARD_CANCELLED = 5` exit code, state persistence under `$XDG_CACHE_HOME`, and validate-on-exit — bundled into `v0.5.5` (PyPI 2026-05-10) |
| ✅ Done | Site documentation correction sweep | All visible YAML / artefact-path / CLI / schema claims on `site/*.html` now validate against the live `forgelm/` surface. Hero YAML demo rewritten with real Pydantic field names, compliance artefact tree redrawn against the on-disk layout, ghost YAML keys + CLI flags removed, wording aligned with live behaviour. Six-language i18n (en / tr / de / fr / es / zh) now at full parity (731 keys each) — bundled into `v0.5.5` (PyPI 2026-05-10) |
| ✅ Done | [Phase 14 — Multi-Stage Pipeline Chains](roadmap/completed-phases.md#phase-14--multi-stage-pipeline-chains-v070) | SFT → DPO → GRPO chained config, pipeline provenance artifacts, 7 new pipeline-scoped audit events, `forgelm verify-annex-iv --pipeline` mode — shipped `v0.7.0` (PyPI 2026-05-15; re-scheduled from v0.6.0 after Phase 15 displaced it via the 2026-05-11 ingestion pilot) |
| 🚧 In review | [Phase 14.5 — Pipeline Hardening](roadmap/phase-14-5-pipeline-hardening.md) | All four v0.7.0 review-deferred items closed, publish pending → `v0.9.x` patch cycle. Landing here: per-stage evidence deep-parse validation (plus the writer fix that stopped `--pipeline` raising a tamper alarm on clean runs), the canonical webhook vocabulary reference (`docs/reference/webhook_schema.md`), and the `WebhookNotifier._send(**extra)` explicit allowlist. The fourth — the canonical pipeline manifest hash + non-chain-field tamper detection — **actually shipped in v0.8.0** under a different finding ID and was only documented here; the row had stayed open by oversight. The file's later-appended Task 5 (SonarCloud S3776 cognitive-complexity refactor) is **not** part of this delivery, and is itself closed as **NOT SCHEDULED**: re-measurement found its counts, function list, file:line references and acceptance criterion all wrong, so it is now gated on a stated condition — Sonar actually wired into a workflow, or an in-repo `ast` complexity ratchet under `tools/` — rather than on a version |
| ✅ Done | [Phase 15 — Ingestion Pipeline Reliability](roadmap/completed-phases.md#phase-15--ingestion-pipeline-reliability-v060) | Wave 1 + Wave 2 + 5 review-absorption rounds: window-based multi-line PDF dedup, Turkish glyph normalisation profile (language-hint-coupled default), language-aware Unicode-block sanity check, ingest-time quality pre-signal, default-on audit `--quality-filter`, DOCX explicit header/footer subtraction, EPUB spine + whole-token nav/cover skip, TXT UTF-8 BOM + MD YAML frontmatter strip, notebook playground alignment, plus Wave 2 `--strip-pattern` (ReDoS-guarded), `--page-range`, front-matter heuristic, `--strip-urls`, multi-column warning — shipped `v0.6.0` (PyPI 2026-05-11) |
| 📋 Planned | [Phase 13 — Pro CLI & Observability Dashboard](roadmap/phase-13-pro-cli.md) | License-gated dashboard, HPO, scheduled jobs, team config store → `v0.6.0-pro` — Pro tier versions independently of the OSS core, so `v0.6.0-pro` is Pro's own v0.6.0, not parity with OSS `v0.6.0` (gated on adoption + ISO/SOC 2 baseline shipped in v0.5.5) |

> **Status legend:** ✅ Released (PyPI) · 🟡 Merged on main, publish pending · 🚧 In review (PR open) · 📋 / ⏳ Planned

**Released:** `v0.9.0` — "transformers 5.x Migration & CVE-2026-4372 Fix" — PyPI 2026-07-05.  Raises the `transformers` floor to `>=5.3.0,<6.0.0` — fixing **CVE-2026-4372**, a critical `from_pretrained` remote-code-execution vulnerability — and cascades the co-dependency floors it requires (`torch`, `huggingface_hub`, `peft`, `accelerate`, `datasets`, `trl`, `requests`).  Drops Intel Mac (x86_64) support — PyPI publishes no `torch>=2.4` wheel for that platform; Apple Silicon, Linux, and Windows are unaffected.  A dependency-migration release — no new roadmap phase.  See [releases.md](roadmap/releases.md#v090--transformers-5x-migration--cve-2026-4372-fix-2026-07-05).

**`v0.8.0`** — "Model Integrity Verification & Deprecation Cleanup" — PyPI 2026-06-16.  Adds `forgelm verify-integrity` (re-hashes a trained model directory against its Article 15 `model_integrity.json` manifest) plus config-driven merge (`merge.ties_trim_fraction` / `dare_drop_rate` / `dare_seed`) and synthetic-sanity (`synthetic.sanity_failure_rate`) knobs; hardens `distributed.strategy` / `data.mix_ratio` validation; completes the deprecation cadence for `evaluation.staging_ttl_days` (deprecated v0.5.5) and the `--data-audit` CLI flag (deprecated v0.5.0), removing both.  A maintenance release — no new roadmap phase.  See [releases.md](roadmap/releases.md#v080--model-integrity-verification--deprecation-cleanup-2026-06-16).

**`v0.7.0`** — "Phase 14 Multi-Stage Pipeline Chains" — PyPI 2026-05-15.  Chains 2+ training stages (SFT → DPO → GRPO etc.) into one config-driven run with auto-chained model paths, per-stage gates, crash-safe resume, and a chain-level Annex IV manifest.  Also lands a critical DNS-rebinding TOCTOU SSRF hardening (issue #14).  See [completed-phases.md](roadmap/completed-phases.md#phase-14--multi-stage-pipeline-chains-v070).

**`v0.6.0`** — "Phase 15 Ingestion Pipeline Reliability" (2026-05-11).  Closes the silent-failure gap exposed by the 2026-05-11 pilot across PDF / DOCX / EPUB / TXT / Markdown ingestion plus the playground notebook — Wave 1 + Wave 2 + 5 review-absorption rounds in one release.  See [completed-phases.md](roadmap/completed-phases.md#phase-15--ingestion-pipeline-reliability-v060) for the full list of ingest-time guards (window-based PDF dedup, Turkish glyph normalisation profile, language-aware Unicode-block sanity check, ingest-time quality pre-signal, default-on audit `--quality-filter`, DOCX explicit header/footer subtraction, EPUB spine + whole-token nav/cover skip, TXT UTF-8 BOM + MD YAML frontmatter strip, notebook playground alignment, Wave 2 `--strip-pattern` (ReDoS-guarded) + `--page-range` + front-matter heuristic + `--strip-urls` + multi-column warning).

**`v0.5.0`** — "Document Ingestion + Data Curation Pipeline" — PyPI 2026-04-30 (Phases 11 + 11.5 + 12 + 12.5 consolidated).

- **Phase 11** — `forgelm ingest` (PDF / DOCX / EPUB / TXT / Markdown → SFT-ready JSONL) + `forgelm audit` (length / language / near-duplicate / cross-split leakage / PII regex with Luhn + TC Kimlik validators) + EU AI Act Article 10 governance integration.
- **Phase 11.5** — operational polish: LSH-banded near-duplicate detection, streaming JSONL reader, token-aware `--chunk-tokens`, PDF page-level header/footer dedup, `forgelm audit` subcommand, PII severity tiers, atomic audit writes, wizard "ingest first" entry point.
- **Phase 12** — data curation maturity: MinHash LSH dedup option (`--dedup-method minhash`, `[ingestion-scale]` extra), markdown-aware splitter (`--strategy markdown`), code/secrets leakage tagger (`--secrets-mask`, `secrets_summary` always-on), heuristic quality filter (`--quality-filter`), DOCX/Markdown table preservation.
- **Phase 12.5** — small additive polish: `--all-mask` shorthand for combined PII + secrets scrubbing, `forgelm audit --croissant` emits a Google Croissant 1.0 dataset card, optional Presidio ML-NER PII adapter (`--pii-ml`, `[ingestion-pii-ml]` extra), wizard "audit first" entry point.

Originally planned as four sequential PyPI tags (`v0.5.0` / `v0.5.1` / `v0.5.2` / `v0.5.3`), consolidated into one comprehensive `v0.5.0` release because the four phases form one coherent surface (ingest → polish → mature → polish) hard to use in parts.

**Earlier:** `v0.4.5` — Quickstart Layer (2026-04-26); `v0.4.0` — Post-Training Completion (2026-04-26).

**Current state:** `v0.9.0` is the latest released version. 21 phases (1, 2, 2.5, 3, 4, 5, 5.5, 6, 7, 8, 9, 10, 10.5, 11, 11.5, 12, 12.5, 12.6, 14, 15, 22) shipped on PyPI through `v0.7.0`; `v0.8.0` and `v0.9.0` are subsequent maintenance / dependency-migration releases that did not add a new roadmap phase — see [releases.md](roadmap/releases.md) for their full scope.  Phase 13 ships separately as `v0.6.0-pro` (Pro tier versions independently of the OSS core) once adoption gates are met.

> **Phase 12.6 task / sub-task dual-axis note:** Phase 12.6 is itself a 38-task closure cycle (Tasks 1-38) tracked at [`roadmap/completed-phases.md`](roadmap/completed-phases.md); per-wave PR descriptions carry the closure-task delta.

## Quick summary of what's planned

> **Note:** Arrows depict shipping order, not phase numbers (Phase 15 shipped in v0.6.0; Phase 14 shipped in v0.7.0 after Phase 15 displaced it; v0.8.0 and v0.9.0 are maintenance / dependency-migration releases with no new phase, shown on their own maintenance-release track below rather than as phase output; Phase 13 ships later under the Pro tier).

```mermaid
graph LR
    P10[Phase 10<br/>Post-Training<br/>Completion] --> P105[Phase 10.5<br/>Quickstart<br/>Layer]
    P10 --> P11[Phase 11<br/>Data<br/>Ingestion]
    P105 --> P11
    P11 --> P115[Phase 11.5<br/>Ingestion<br/>Polish]
    P115 --> P12[Phase 12<br/>Data Curation<br/>Maturity]
    P12 --> P125[Phase 12.5<br/>Data Curation<br/>Follow-up]
    P125 --> P126[Phase 12.6<br/>Closure<br/>Cycle]
    P126 --> P15[Phase 15<br/>Ingestion<br/>Reliability]
    P15 --> P14[Phase 14<br/>Pipeline<br/>Chains]
    P14 --> P145[Phase 14.5<br/>Pipeline<br/>Hardening]
    P14 --> P13[Phase 13<br/>Pro CLI<br/>+ Dashboard]
    P15 --> P13

    P10 -.-> V1[v0.4.0]
    P105 -.-> V15[v0.4.5]
    P11 -.-> V2[v0.5.0 ✅ Released]
    P115 -.-> V2
    P12 -.-> V2
    P125 -.-> V2
    P126 -.-> V25[v0.5.5]
    P15 -.-> V23[v0.6.0 ✅ Released]
    P14 -.-> V27[v0.7.0 ✅ Released]
    P145 -.-> V275[v0.9.x]
    P13 -.-> V3[v0.6.0-pro]

    subgraph MAINT["Maintenance releases (no new phase)"]
        V28[v0.8.0 ✅ Released]
        V29[v0.9.0 ✅ Released]
    end

    V27 ==>|maintenance| V28
    V28 ==>|maintenance| V29

    style P10 fill:#003300,stroke:#00ff88
    style P105 fill:#003300,stroke:#00ff88
    style P11 fill:#004400,stroke:#88ff88
    style P115 fill:#004400,stroke:#88ff88
    style P12 fill:#004400,stroke:#88ff88
    style P125 fill:#004400,stroke:#88ff88
    style P15 fill:#004400,stroke:#88ff88
    style P14 fill:#002244,stroke:#00aaff
    style P13 fill:#442200,stroke:#ffaa00
    style MAINT fill:#222222,stroke:#888888
```

## Guiding principles

1. **Reliability before features.** Every new capability ships with tests, docs, and CI coverage.
2. **Enterprise differentiation over feature parity.** ForgeLM's edge is safety + compliance, not feature count. Don't compete on features already owned by Unsloth (speed), LLaMA-Factory (GUI), or Axolotl (sequence parallelism).
3. **Config-driven, testable, optional.** Every new capability is a YAML flag. No global state, no magic, no mandatory integrations.
4. **Kill criteria over hype criteria.** Every phase has a measurable quarterly gate. Missed gates = rethink, not push harder.

## Documentation map

```
docs/
├── roadmap.md                                  # This file — short index
├── roadmap-tr.md                               # Turkish mirror
└── roadmap/
    ├── completed-phases.md                     # Phase 1-12.6 + 15 + 14 archive (detailed) — Phase 10 / 10.5 / 11 / 11.5 / 12 / 12.5 / 12.6 / 15 / 14 absorbed inline (shipped as v0.4.0 / v0.4.5 / v0.5.0 / v0.5.5 / v0.6.0 / v0.7.0; 15 precedes 14 in the file, matching shipping order)
    ├── phase-13-pro-cli.md                     # Planned — v0.6.0-pro (gated)
    ├── phase-14-5-pipeline-hardening.md        # 4 review-deferred items closed (one shipped in v0.8.0), publish pending; Task 5 (S3776) closed as NOT SCHEDULED (condition-gated)
    ├── releases.md                             # v0.3.0 → v0.9.0 release notes
    └── risks-and-decisions.md                  # Risk matrix, opportunities, competitive positioning, decision log
```

## How this roadmap is maintained

- **Weekly** — Progress check against active phase's tasks.
- **Monthly** — Decision log update if scope changes (`roadmap/risks-and-decisions.md`).
- **Quarterly** — Full review: close completed phases, re-prioritize planned phases, update competitive analysis. Each phase gate has explicit kill criteria: if the gate is missed, the phase is rethought — not just delayed.
- **Annually** — Archive completed phases to `completed-phases.md`, retire outdated planning files.

## Related documents

- [Product Strategy](product_strategy.md) — Market position, target users, strategic decisions
- [Architecture](reference/architecture.md) — System design reference
- [Configuration Guide](reference/configuration.md) — YAML reference for all phases
- [Usage Guide](reference/usage.md) — How to run ForgeLM
- **Internal only:** Marketing + strategy planning in `docs/marketing/` (gitignored)

---

**For individual phase details:** Follow the links in the status table above.
**For the big picture:** Start with [Product Strategy](product_strategy.md) → pick a phase → read its dedicated file.
