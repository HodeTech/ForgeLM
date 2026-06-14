# Riskler, Fırsatlar, Rekabet ve Kararlar

> **Not:** Bu dosya yönetişim ve stratejik karar kayıtlarını içerir. Her quarterly gate'te yeniden değerlendirilir.

## Risk Matrix

### High Severity
| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| **Dependency Breaking Changes** (TRL, PEFT, Unsloth) | Training pipeline breaks without warning | High | Version pinning with upper bounds, CI nightly builds against latest deps, compatibility matrix |
| **EU AI Act Non-Compliance** (August 2026 deadline) | Enterprise customers cannot adopt ForgeLM for high-risk AI | High | Phase 8 deep compliance: Annex IV docs, audit log, risk assessment, human gate, data governance |
| **Safety Degradation from Fine-Tuning** | Fine-tuned models lose alignment, enterprise liability | High | Phase 6 safety evaluation pipeline, auto-revert on safety regression |
| **Alignment Method Lock-In** | ForgeLM supports only ORPO while market demands DPO/GRPO | High | Phase 5 is top priority — critical market expectation |
| **Compliance window closing** (Aug 2, 2026) | Enterprise leads not converted before competitors add compliance features | High | Enterprise outreach Day 1; Turkey market as first pilot; deep compliance features (Audit API, managed cloud) as moat |
| **Community flywheel not started** | 0 stars → 0 enterprise credibility → missed EU AI Act window | High | Phase 10.5 Quickstart prioritized; YouTube Academy; Show HN + Reddit launch |

### Medium Severity
| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| **MoE/VLM Architecture Shift** | ForgeLM cannot train dominant model architectures | Medium | Phase 7 addresses this; monitor PEFT library MoE support |
| **Scope Creep** (too many trainers, model types) | Maintenance burden exceeds capacity, core quality degrades | Medium | Strict phase gating, leverage TRL's existing trainers |
| **Ecosystem Commoditization** (Axolotl, LLaMA-Factory) | Competing tools add similar enterprise features | Medium | Double down on safety + compliance differentiation |
| **GPU/CUDA Version Fragmentation** | Users on different CUDA versions hit incompatibilities | Medium | Docker images pin CUDA versions, compatibility matrix |

### Low Severity
| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| **Model merging adoption uncertainty** | Feature built but rarely used | Low | Implement as separate CLI command, minimal core changes |
| **Notebook template maintenance** | Notebooks break with library updates | Low | Auto-generate from config templates, CI validation |

---

---

## Opportunity Analysis

### Immediate Opportunities
1. **Alignment Stack (Phase 5)** — DPO+GRPO support closes the most critical competitive gap. Every serious fine-tuning workflow in 2026 uses preference optimization.
2. **Safety-as-a-Feature (Phase 6)** — No competitor integrates safety evaluation into the training pipeline. ForgeLM can own "the safest way to fine-tune an LLM."

### Medium-Term Opportunities
3. **EU AI Act Compliance** — August 2026 deadline creates urgent demand. ForgeLM as the only tool generating compliance artifacts is a powerful enterprise sales argument.
4. **MoE Fine-Tuning** — Qwen3, DeepSeek-V3 dominance means MoE support is becoming table stakes.
5. **Cost Transparency** — GPU cost tracking per run enables enterprise budget planning and optimization. Simple to implement, high perceived value.

### Long-Term Opportunities
6. **Managed ForgeLM Service** — SaaS offering: upload data + config → receive trained model + compliance artifacts.
7. **Synthetic Data Pipeline** — Config-driven teacher model distillation before training. Unique integration no competitor offers.
8. **Training Marketplace** — Community-contributed config templates for common use cases.

---

---

## Competitive Positioning (Updated March 2026)

| Competitor | Stars | ForgeLM Advantage | ForgeLM Gap |
|------------|-------|-------------------|-------------|
| **LLaMA-Factory** | ~55-68K | CI/CD-native, safety eval, compliance | Web UI, 100+ models, GaLore/PiSSA, VLM |
| **Unsloth** | ~54-56K | Enterprise features, multi-trainer, safety | Speed (2-5x), Studio GUI, MoE optimization |
| **TRL** | ~17.6K | Full pipeline (not just trainers), Docker, evaluation | GRPO, official HF integration |
| **Axolotl** | ~11.4K | Simpler config, Docker, safety eval | GRPO, GDPO, sequence parallelism, MoE quant |
| **torchtune** | Meta-backed | Config-driven enterprise focus | Knowledge distillation, QAT, PyTorch-native |

**ForgeLM's evolving niche:** Config-driven, CI/CD-native, **safety-conscious**, enterprise LLM fine-tuning. The safety + compliance angle is the strongest differentiator available — no competitor addresses it.

---

---

## Deferred Findings (Tracked, not dropped)

> **Why this section exists.** PR #29 master review identified 15 findings whose fix-strategy is **defer to v0.6.x with explicit roadmap row**. Each row makes the deferred finding visible forever — silently dropping items is the failure mode this section guards against. Rows are removed only when the underlying work lands.

### 2026-05-06 — PR #29 master review deferrals → v0.6.x

| Finding ID | Severity | Area | Reason for deferral | Cost | Owner |
|------------|----------|------|---------------------|------|-------|
| **F-PR29-A1-05** — `ForgeTrainer` god-object split | Low | Architecture | `forgelm/trainer.py:222-1472` is 1250+ LOC / 30+ methods; architecture.md mandates ~1000-LOC sub-package boundary. Defensible at trigger but fragile. Plan `trainer/` sub-package: `_kwargs.py` (kwarg fold-in), `_runtime.py` (OOM + DeepSpeed), `_finalize.py` (artifacts + approval), `_artifacts.py` (compliance + model-card + integrity + deployer); preserve `forgelm.trainer.ForgeTrainer` + `forgelm.trainer.train` public surface. Refactor risk + integration test churn too high for closure cycle. | ~1 day | TBD |
| **F-PR29-A1-06 / A1-07 / A1-08** — Other ~1000-LOC ceiling violations | Low | Architecture | `forgelm/cli/subcommands/_purge.py:1382` (split `_purge/_row_id.py`, `_run_id.py`, `_check_policy.py`, `_shared.py`), `forgelm/cli/_parser.py:1135` (split `_parser/_train.py`, `_inspect.py`, `_data.py`, `_run.py`), `forgelm/compliance.py:1502` (split `_audit_log.py`, `_annex_iv.py`, `_provenance.py`, `_gdpr.py`), `forgelm/ingestion.py:1443` (split `_readers.py`, `_chunkers.py`, `_pipeline.py`). New `tools/check_module_size.py --strict` (Wave 2-10) warns at 1000 / fails at 1500 LOC for new modules; existing four are grandfathered. Pick one per minor release. | ~4-8 h each | TBD |
| **F-PR29-A4-03** — Test factory adoption sweep | Low | Tests | `tests/_helpers/factories.minimal_config` advertised as single source of truth but only 14/47 test files use it; 20+ files repeat inline `{"model": {...}}` dicts. Schema change to `ForgeConfig` would silently break inline dicts. Bulk-edit + spot-check + add `tools/check_test_factory_adoption.py` failing CI when adoption rate drops below 70 %. | ~3-4 h | TBD |
| **F-PR29-A4-04** — Test naming sweep (phase/faz → feature) | Low | Tests | 9 test files violate `testing.md:49` "one `test_<module>.py` per `forgelm/<module>.py`": `test_phase7.py`, `test_phase12_5.py`, `test_phase12_review_fixes.py`, `test_data_audit_phase12.py`, `test_ingestion_phase12.py`, `test_cli_phase10.py`, `test_wizard_phase11.py`, `test_wizard_phase11_5.py`, `test_faz27_narrow_exceptions.py`. Mechanical `git mv` preserving history + add `tools/check_test_naming.py` failing on `test_(phase\|faz)\d+` patterns. | ~2 h | TBD |
| **F-PR29-A4-05** — `tests/runtime_smoke.py` disposition | Low | Tests | File named `runtime_smoke.py` (not `test_runtime_smoke.py`) so pytest skips it (0 collected). Hardcodes `tmp_smoke_test/`, downloads real HuggingFace model. Decision: move to `tools/manual_smoke.py` and document in `release.md` as release-gate manual check, OR rename + mock model loading + use `tmp_path` fixture. | ~1 h | TBD |
| **F-PR29-A6-02** — Module size guard rollout | Low | CI / Architecture | Sister to A1-05/06/07/08. `tools/check_module_size.py` ships in PR #29 (Wave 2-10) but only enforced on NEW modules; the 7 existing over-ceiling modules are grandfathered. v0.6.x: pick one module per minor release for split. | rolling | TBD |
| **F-PR29-A6-07** — PEM/PGP secret regex line-walker rewrite | Low | Security / Regex | `forgelm/data_audit/_secrets.py:67-78` uses `.*?` + `re.DOTALL` between PEM markers. Not a Rule 6 violation per se (no back-reference), but spirit-Rule-6 + invokes Sonar S5852. Replace with line-walker state machine mirroring `_strip_code_fences`. Add ReDoS-budget benchmark in `tests/test_secrets_redos_regression.py`. | ~2 h | TBD |
| **F-PR29-A6-12** — Secret-mask centralisation across env-rendering paths | Medium | Security | `forgelm/cli/subcommands/_doctor.py:974` and `_purge.py:445` render env-name → value pairs without applying `_mask_env_value_for_audit` (which exists at `_doctor.py:682` for `FORGELM_OPERATOR` only). Operator passing `HF_TOKEN` or `*_API_KEY` leaks unmasked value in doctor JSON / purge stdout. Centralise on a `_SECRET_LIKE_VAR_PATTERNS` allowlist applied at every rendering boundary; add `tests/test_doctor.py::test_secret_envs_are_masked`. | ~1-2 h | TBD |
| **F-PR29-A6-14** — `FORGELM_ALLOW_ANONYMOUS_OPERATOR` → YAML field | Low | Config | Behaviour gate (audit-logger refuse-or-proceed) currently env-var only; CLAUDE.md "config-driven" rule allows env-vars only for secrets / identity. Add `compliance.allow_anonymous_operator: bool = False` to `ComplianceConfig` (`forgelm/config.py`); teach `AuditLogger.__init__` to read both env var AND config field with YAML taking precedence (env stays as CI fallback). | ~30 min | TBD |
| **F-PR29-A6-18** — `docs/usermanuals/{en,tr}/` parity registry coverage (F-W5-S5 deferred) | Low | Docs / CI | Bilingual-parity gate covers 39 registered pairs; `docs/usermanuals/en/` ↔ `docs/usermanuals/tr/` 65 page pairs are NOT registered. Extend `tools/check_bilingual_parity.py::_PAIRS` to autodiscover usermanual pairs (or whitelist them explicitly). Holds because mass-registering would inject a wave of pre-existing drift into `--strict` and block unrelated PRs; needs its own cleanup pass first. | ~2 h | TBD |
| **F-PR29-A7-10** — Ruff D-rules pilot | Low | Lint / Style | `pyproject.toml [tool.ruff.lint] select = ["F", "E9", "W", "B", "I"]` is narrow vs `coding.md` discipline. Pilot enabling `D` (pydocstyle Google convention via `[tool.ruff.lint.pydocstyle] convention = "google"`) on `forgelm/` only. `UP` (pyupgrade) is smaller win; could land sooner. | ~1 day | TBD |
| **F-PR29-A8-07** — Sonar hotspots accept-as-false-positive | Low | CI / Security | 7 SonarCloud Security Hotspots from PR #29 absorbed via NOSONAR markers (PR #36 + 70cd546 prose-comment cleanup). Codacy / SonarCloud gates may still report ACTION_REQUIRED at PR-level even with markers in place. Either land follow-up "Mark as Safe" review pass in SonarCloud UI, or accept as deferred with this row. | rolling | TBD |
| **F-PR29-A8-10** — Deterministic batching test for safety + judge | Low | Tests | Carry-over C-3 routed to Faz 28 (Wave 3). Code change shipped (`batch_size` param + OOM fallback) but determinism contract is unpinned by tests. Future regression breaking byte-for-byte determinism across `batch_size` values would ship undetected. Add `tests/test_safety_advanced.py::TestBatchingDeterminism` running `evaluate_safety` on same fixture with `batch_size=1,2,4,8` and asserting byte-equal output. | ~30 min | TBD |
| **F-PR29-A8-02 / C-2** — `tools/build_usermanuals.py` performance optimization | Medium | Tooling | Carry-over C-2 (incremental cache + `ProcessPoolExecutor` for `markdown.Markdown` reuse) silently dropped. Implementation: hoist `markdown.Markdown(...)` to module-level `_MD`, replace per-call construction with `_MD.reset()`. Wrap per-language loop in `ProcessPoolExecutor(max_workers=min(6, os.cpu_count()))`. Add mtime / sha256 cache → `(html, headings)`. Add regression test pinning ≥ 5× speedup. **F-performance-105 is Open at HEAD despite CHANGELOG implying Closed** — explicitly surfaced here so the discrepancy isn't lost. | ~4 h | TBD |
| **F-PR29-A8-16** — Cross-PR finding-ID convention | Low | Process | Wave 1 used `F-Wave1-NN`, Wave 2a `F-R5-NN`, Wave 5 `F-W5-S5`, PR #29 `F-PR29-AX-NN`. Codify canonical scheme. (Actually being addressed in `code-review.md` by Wave 2-2 — included here as the rolling enforcement target so the convention doesn't drift again.) | rolling | TBD |
| **F-PR29-A2-04 / cost_tracking ghost** — `output.cost_tracking:` config block implementation | Medium | Config / Cost | The `output.cost_tracking:` YAML block (`enabled`, `rate_per_hour`, `currency`, `alert_threshold_usd`, `halt_threshold_usd`) is referenced by `docs/usermanuals/{en,tr}/operations/gpu-cost.md` and the YAML reference in `docs/usermanuals/{en,tr}/reference/configuration.md` but **does not exist in `forgelm/config.py`** as of v0.5.5. Doc has been updated with "planned for v0.6.x" banners + commented-out YAML; config-side implementation requires `CostTrackingConfig` Pydantic model + audit-event emission on threshold crossing + webhook lifecycle event. | ~1 day | TBD |
| **F-PR29-A6-15-followup** — Bare `# NOSONAR` rule-code sweep | Low | Lint / Style | ~30 BLE001 sites in `forgelm/` use bare `# NOSONAR` without the rule code. Wave 2-4 added the NOSONAR-discipline section to `coding.md` (rule code + rationale required). Sweep all bare-NOSONAR sites and append `python:S<id>` per the new policy. Mechanical refactor; no behaviour change. | ~2 h | TBD |
| **F-PR29-A5-anchor-slugifier** — anchor-resolution checker GFM divergence | Low | Tooling | Wave 2-7 surfaced two anchor-slugger divergences vs GitHub-flavored Markdown in `tools/check_anchor_resolution.py`: (i) multi-dash collapse (local checker collapses `-+` → `-`; GFM preserves), (ii) duplicate-heading disambiguation (GFM appends `-1`/`-2`; local stores in `set`). Two fixed locally as workarounds in `safety_compliance-tr.md`; align checker's slugifier with GFM in v0.6.x. | ~2 h | TBD |
| **F-PR29-A2-13-design-doc-paths** — `library-api-design.md` `forgelm/api.py` references | Low | Docs | Wave 2-13 added a Status callout in §5.1 noting impl diverged from design (3-segment vs 2-segment `__api_version__`), but ~7 downstream sentences still reference `forgelm/api.py` as the version-home module (the file does not exist; `forgelm/_version.py` is canonical). Sweep `__api_version__` location refs in design doc to point at `_version.py` for full historical-vs-current clarity. Doc-only. | ~30 min | TBD |
| **F-PR29-A6-05-issue-link** — open GitHub issue tracking semantic-chunking deferral | Low | Docs / Tracking | `forgelm/ingestion.py:_chunk_semantic` raises `NotImplementedError` referencing this row. Open a GitHub issue at https://github.com/HodeTech/ForgeLM/issues describing the embedding-based semantic-chunking deferral (per [completed-phases.md "Phase 12 — Data Curation Maturity"](completed-phases.md#phase-12-data-curation-maturity), "deferred to Phase 13+ for Annex IV reproducibility"); update the message in `ingestion.py:_chunk_semantic` to reference the real issue number. | ~5 min | TBD |
| **F-PR29-A6-lock-file** — `requirements.lock` for development environments | Low | Supply-chain | SonarCloud `text:S8565` flags `pyproject.toml` for missing `uv.lock` / `poetry.lock`. ForgeLM uses pip + setuptools (not uv / poetry), and bounded-range `[project.dependencies]` + nightly `pip-audit` + per-OS CycloneDX SBOMs cover the supply-chain determinism story. A pip-tools workflow producing `requirements.lock` (input: `pyproject.toml` extras; output: pinned hash-locked txt) would be a strict improvement for development environment reproducibility — track here for v0.6.x; SonarCloud finding suppressed via `# NOSONAR text:S8565` at `pyproject.toml:1`. | ~2 h | TBD |

**Removal contract.** A row is removed from this table only when:
1. The underlying fix lands on `main` (PR merged), AND
2. The CHANGELOG entry references the F-ID, AND
3. Any associated CI guard (e.g. `tools/check_test_naming.py`) is in place if the deferral row mentioned one.

Until all three conditions are met, the row stays — even if the work is "in progress."

### 2026-05-15 — v0.7.0 release-review deferrals → Phase 14.5 (v0.7.x)

> **Source.** PR #54 release-review pass (consolidated reviewer findings).  14 findings classified at PR merge time; 10 of 14 landed in v0.7.0 commit `d3ed8cd`.  The 4 below are the explicitly-deferred items, each carrying real refactor cost that warranted its own sub-phase rather than being squeezed into the release-prep PR.  Tracked as [Phase 14.5 — Pipeline Hardening](phase-14-5-pipeline-hardening.md).

| Finding ID | Severity | Area | Reason for deferral | Cost | Owner |
|------------|----------|------|---------------------|------|-------|
| **F-PR54-H6-manifest-hash** — canonical pipeline manifest hash + non-chain-field tamper detection | High | Compliance / Annex IV | Chain-level `pipeline_manifest.json` carries no `manifest_hash` field; structural verifier catches chain-integrity / index / status drift but accepts edits to non-chain fields (provider metadata, metrics, `final_status`, per-stage `error` strings).  Single-artefact path already pins this surface via `compute_annex_iv_manifest_hash()`; pipeline manifest should mirror.  Defer: needs canonical-serialisation rules, golden-fixture re-baseline, and a CHANGELOG breaking-ish note for operators who pinned a manifest hash in external systems.  Hash-chained audit log already narrows the tampering window; immediate risk is low. | ~1 day | TBD |
| **F-PR54-H7-per-stage-deep-parse** — disk verifier deep-parses each per-stage `training_manifest.json` | High | Compliance / Annex IV | `verify_pipeline_manifest_at_path` currently `os.path.isfile(...)`-only on each completed stage's per-stage manifest.  Zero-byte / malformed-JSON / tampered files pass.  Defer: recursive verification semantics need a documented contract; per-stage Annex IV verifier already exists (`verify_annex_iv_artifact`) so the implementation is straightforward, but extending the existing `TestVerifyPipelineManifestAtPath` test class with zero-byte / malformed / missing-field scenarios + the 6 golden manifests under `tests/fixtures/pipeline/` need a coordinated update.  Pairs with `F-PR54-H6-manifest-hash` — both land together as "pipeline manifest verification rewrite". | ~1 day | TBD |
| **F-PR54-M10-webhook-vocab-docs** — webhook `pipeline.*` event vocabulary documentation | Medium | Docs / Webhook | v0.7.0 introduces 7 new `pipeline.*` event names alongside the pre-existing 5-event `training.*` vocabulary; existing CHANGELOG entry lists them but no canonical reference doc enumerates the full post-v0.7.0 vocabulary + payload shape.  Receivers that hard-validate the `event` field against a frozen enum (rare; Slack / Teams render as plain string) would need to update.  Defer: low real-world risk (no enum-validating receiver reported), but a `docs/reference/webhook_schema.md` canonical reference is the long-term home + an optional `tools/check_webhook_event_vocabulary.py` `--strict` guard prevents documented-vs-emitted drift. | ~3-4 h | TBD |
| **F-PR54-M11-send-extras-allowlist** — `WebhookNotifier._send(**extra)` explicit allowlist | Medium | Security / Webhook | PR #53 added `**extra` to `_send` so `notify_pipeline_*` could pass `stage_count` / `final_status` / `stopped_at` / `stage_name` through to the receiver payload.  All current callers are orchestrator-internal (caller-controlled); a future contributor passing external user input through `_send` would have nothing stopping that input from landing in the webhook payload.  Defer: real risk vector requires future code change to materialise (no immediate exposure); good hygiene to add `_ALLOWED_PIPELINE_EXTRAS: frozenset[str]` + WARN-and-drop on unknown extras, plus a TypedDict at the call sites for static-type catch. | ~1-2 h | TBD |

**Bundled landing.** All four rows ship together as [Phase 14.5 — Pipeline Hardening](phase-14-5-pipeline-hardening.md) on the v0.7.x patch cycle; each row's CHANGELOG bullet references its F-ID per the removal contract above.

---

---

## Decision Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-03-23 | Inserted Phase 2.5 (Reliability) before new features | Product analysis revealed critical silent failure and test coverage gaps |
| 2026-03-23 | Deprioritized direct cloud API integration (original Phase 3) | Unsustainable maintenance burden, 3rd-party dependency risk, scope creep |
| 2026-03-23 | Adopted Docker-based deployment strategy | Portable, user-controlled infrastructure, minimal maintenance for ForgeLM team |
| 2026-03-23 | Moved wizard mode from Phase 2 to Phase 3 | Reliability work takes priority over UX improvements |
| 2026-03-23 | Added `trust_remote_code` as enterprise adoption blocker | Security risk incompatible with regulated industries |
| 2026-03-23 | Phase 5 (Alignment Stack) prioritized as Critical | Competitive analysis: DPO/GRPO support is market expectation, ORPO alone insufficient |
| 2026-03-23 | Phase 6 (Safety & Compliance) chosen as primary differentiator | No competitor integrates safety evaluation or EU AI Act compliance. August 2026 deadline creates urgency |
| 2026-03-23 | Phase 7 (MoE/VLM/Merging) scoped as ongoing | Model landscape shifting to MoE and multimodal; must support but not at expense of Phase 5-6 |
| 2026-03-23 | Leverage TRL trainers for alignment methods | TRL already implements DPO, KTO, GRPO — ForgeLM wraps with config, evaluation, and pipeline integration rather than reimplementing |
| 2026-03-24 | Phase 8 (EU AI Act Deep Compliance) added with 10 tasks | Gap analysis against Articles 9-17 + Annex IV revealed 6 major gaps. Tier 1 (5 tasks) must complete before August 2, 2026 enforcement. This is ForgeLM's strongest differentiator — no competitor addresses EU AI Act systematically |
| 2026-03-24 | Phase 9 (Advanced Safety Scoring) added with 8 tasks | Community feedback: binary safe/unsafe classification insufficient for production. Confidence scores, harm categories, severity levels, and trend tracking needed. Strengthens core differentiator |
| 2026-04-25 | Phase ordering revised: Quickstart (formerly Phase 12) moved to Phase 10.5, before Data Ingestion | Community flywheel: Quickstart drives stars → enterprise leads → EU AI Act compliance sales. Delay = missed EU AI Act window. |
| 2026-04-25 | Phase 14 (Multi-Stage Pipeline) added to roadmap | Enterprises need SFT → DPO → GRPO chained training as first-class feature; currently requires manual config juggling |
| 2026-04-25 | Enterprise outreach moved to Day 1 (previously Week 7-12) | EU AI Act enforcement in 99 days; enterprise sales cycle is 3-6 months; starting at Week 7 means missing the window |
| 2026-04-25 | v0.3.1rc1 security + config hardening release | Comprehensive code review revealed webhook URL leakage, audit log chain gap, GRPO callable bug, TIES merging error — all fixed |
| 2026-04-25 | Turkey/BDDK/KVKK market added as priority enterprise target | First-mover in Turkish compliance market; BDDK AI guidelines publishing in 2026; no competitors; path to first enterprise case study |
| 2026-04-30 | v0.5.0 release — Phases 11 + 11.5 + 12 + 12.5 consolidation | Four originally-sequential phases (`v0.5.0`–`v0.5.3`) form one coherent ingest → polish → mature → polish surface that's hard to use in parts; consolidated into a single user-facing release |
| 2026-04-30 | PyPI publish landed for v0.5.0 | Document Ingestion + Data Curation Pipeline shipped to PyPI; closes the "merged on main, publish pending" gap that drifted across roadmap, releases.md, and CHANGELOG.md |
| 2026-04-30 | Closure cycle scope: Path B (full Faz 1-38 sweep) chosen over Path A (release immediately, fix in v0.5.6) | Master review surfaced 175 findings + 4 user-added scope items (Library API, ISO 27001 / SOC 2, GDPR full, Article 14 staging). Releasing v0.5.5 with all open items closed is more maintainable than 5 sequential patch releases; 5/5 quality bar requires the closure-cycle bundle as one tag |
| 2026-05-04 | Bilingual policy formalised — EN+TR mandatory, DE/FR/ES/ZH user-manual deferred | Site i18n picker keeps DE/FR/ES/ZH for the marketing surface but `docs/usermanuals/<lang>/` is authored only in EN+TR; the deferred languages fall back to EN via the `tableForLang(...) → DEFAULT='en'` chain. Translation cycle for 4 deferred languages explicitly out of v0.5.5 scope |
| 2026-05-05 | `tools/check_bilingual_parity.py` introduced as `--strict` CI guard (Wave 3 / Faz 24) | EN/TR H2/H3/H4 spine parity check runs on every PR. Scope expanded from 9/9 to 23/23 pairs by Wave 4 as new QMS bilingual mirrors landed |
| 2026-05-06 | `tools/check_anchor_resolution.py` flipped from advisory to `--strict` CI gate (Wave 4 / Faz 26 → Wave 5 cleanup) | Markdown anchor resolution found 36 baseline drifts across `docs/usermanuals/` after Wave 4. Wave 5 Task N drove the baseline to 0 and flipped the gate; future PRs cannot reintroduce broken anchors |
| 2026-05-06 | `tools/check_cli_help_consistency.py` introduced as `--strict` CI gate (Wave 5 / Faz 30 Task J) | CLI `--help` output ↔ `docs/usermanuals/{en,tr}/reference/cli.md` parity check. Wave 5 Tasks J + N drove the baseline to 0 and flipped the gate, closing the CLI / docs drift class entirely |
| 2026-05-06 | v0.5.5 release sequence — `cut-release` skill drives `pyproject.toml` bump → CHANGELOG finalize → `git tag -s v0.5.5` → `git push origin main v0.5.5` | Tag push triggers `publish.yml` cross-OS matrix (3 OS × 4 Python = 12 combos); each combo installs the wheel + runs pytest + emits a per-combo CycloneDX SBOM. PyPI publish job runs only after every matrix combo is green. No manual workflow_dispatch path; the tag IS the contract |
| 2026-05-11 | v0.6.0 cut — Phase 15 Ingestion Pipeline Reliability shipped, Phase 14 displaced | The 2026-05-11 pilot exposed silent-failure gaps across PDF / DOCX / EPUB / TXT / Markdown ingestion that gated v0.6.0's credibility.  Phase 14 (Multi-Stage Pipeline Chains, originally slotted for v0.6.0) re-scheduled to v0.7.0 to give Phase 15 a focused release.  Five review-absorption rounds (Gemini + CodeRabbit + Sonar + Codacy + independent self-review) absorbed into one v0.6.0 tag. |
| 2026-05-15 | v0.7.0 cut — Phase 14 Multi-Stage Pipeline Chains shipped, SSRF DNS-rebinding hardening folded in | One YAML, one CLI invocation, one Annex IV manifest covering SFT → DPO → GRPO (auto-chained model paths, per-stage gates, crash-safe state, 7 new pipeline-scoped audit events).  PR #53 absorbed 5 review rounds + PR #54 absorbed 10 of 14 release-review findings; the remaining 4 are tracked as Phase 14.5 (`F-PR54-H6/H7/M10/M11` rows above).  v0.6.0 SSRF hardening (issue #14, `requests-toolbelt` dependency) folded into the same release.  Re-tag required after the initial v0.7.0 tag pushed `cbffe62` and the cross-OS Windows matrix cells failed on a POSIX-specific atomic-write race test (`24de388` retags onto a Windows-skipped test after the initial tag was deleted; PyPI was NOT touched between tags so re-cut is clean). |
| 2026-05-15 | Phase 14.5 introduced as the home for v0.7.x review-deferred items | PR #54 review classified 4 findings as "real refactor / hardening opportunity, not a release blocker".  Carving them into their own sub-phase ([phase-14-5-pipeline-hardening.md](phase-14-5-pipeline-hardening.md)) follows the Phase 12.6 / Phase 15 pattern: focused sub-phase with explicit review-absorption discipline beats squeezing hardening into a release-prep PR.  Wave 2 carry-overs (intra-stage HF `Trainer.train(resume_from_checkpoint=...)`, DAG pipelines, parallel stage execution, `forgelm wizard` pipeline path) tracked at the bottom of the Phase 14.5 doc as future-phase backlog, not in-flight Phase 14.5 work. |
