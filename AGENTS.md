# AGENTS.md — Project Guidance for AI Agents

> **Audience:** Codex (and other AI coding agents) working on ForgeLM. Complements — does not replace — the human-facing [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/standards/](docs/standards/).

## What ForgeLM is (in one line)

A **config-driven, enterprise-grade LLM fine-tuning toolkit** — YAML in, fine-tuned model + compliance artifacts out. Drives the same workflow from a terminal, a notebook, or a CI/CD pipeline step. Covers SFT → DPO → SimPO → KTO → ORPO → GRPO, with integrated safety evaluation, EU AI Act compliance, and auto-revert on quality regression.

Not a framework for training from scratch. Not an inference engine. Not a GUI. Read [docs/product_strategy.md](docs/product_strategy.md) for the 5-minute background.

## What you must read before editing code

**Every time, in this order:**

1. **[docs/standards/README.md](docs/standards/README.md)** — index of all engineering standards
2. **The specific standard** matching what you're about to change:
   - Python code → [coding.md](docs/standards/coding.md) + [architecture.md](docs/standards/architecture.md)
   - **Any `re.compile` / regex change → [regex.md](docs/standards/regex.md)** (ReDoS exposure, fixture fragmentation, the 8 hard rules distilled from Phase 11/11.5/12 review cycles)
   - Error paths → [error-handling.md](docs/standards/error-handling.md)
   - Anything with output → [logging-observability.md](docs/standards/logging-observability.md)
   - Tests → [testing.md](docs/standards/testing.md)
   - Docs → [documentation.md](docs/standards/documentation.md) + [localization.md](docs/standards/localization.md)
   - PR / review → [code-review.md](docs/standards/code-review.md)
   - Release → [release.md](docs/standards/release.md)
3. **[CONTRIBUTING.md](CONTRIBUTING.md)** — the human-facing summary
4. **The relevant roadmap file** — if implementing a planned phase, find it under [docs/roadmap/](docs/roadmap/)

Do not invent conventions. If you cannot find the pattern for what you're about to add, ask the user — don't guess.

## Skills

When a task maps to a common pattern, invoke the matching skill from [.agents/skills/](.agents/skills/):

| Task | Skill |
|---|---|
| Adding a YAML config field | [add-config-field](.agents/skills/add-config-field/SKILL.md) |
| Adding a larger trainer / evaluator / module feature | [add-trainer-feature](.agents/skills/add-trainer-feature/SKILL.md) |
| Writing tests | [add-test](.agents/skills/add-test/SKILL.md) |
| Updating bilingual docs (EN ↔ TR) | [sync-bilingual-docs](.agents/skills/sync-bilingual-docs/SKILL.md) |
| Reviewing a PR (own or peer) | [review-pr](.agents/skills/review-pr/SKILL.md) |
| Cutting a release | [cut-release](.agents/skills/cut-release/SKILL.md) |

Each skill's `SKILL.md` has the full checklist. Follow it; don't skip steps to save time.

## Repository structure at a glance

```text
ForgeLM/
├── forgelm/                 # Source code: ~22 single-file modules + 2 sub-packages
│   ├── cli/                 # CLI package (Phase 15 split): _parser, _dispatch,
│   │                        # _exit_codes, subcommands/{ingest, audit, chat,
│   │                        # export, deploy, quickstart, doctor, cache,
│   │                        # purge, reverse_pii, approve, approvals,
│   │                        # safety_eval, verify_audit, verify_annex_iv,
│   │                        # verify_gguf, ...}
│   ├── data_audit/          # Audit package (Phase 14 split): _orchestrator,
│   │                        # _aggregator, _streaming, _simhash, _minhash,
│   │                        # _pii_regex, _pii_ml, _secrets, _quality,
│   │                        # _croissant, _summary, _splits
│   ├── config.py            # Pydantic schemas (23 models)
│   ├── trainer.py           # TRL wrapper (SFT/DPO/SimPO/KTO/ORPO/GRPO)
│   ├── model.py             # HF + PEFT model loading
│   ├── data.py              # Dataset loading + format detection
│   ├── ingestion.py         # Raw docs → SFT JSONL (`forgelm ingest`)
│   ├── safety.py            # Llama Guard + harm categories + auto-revert
│   ├── compliance.py        # EU AI Act Articles 9-17 + Annex IV + GDPR purge / reverse-pii primitives
│   ├── webhook.py           # Slack/Teams notifications (5-event vocabulary)
│   ├── grpo_rewards.py      # Built-in GRPO format/length shaping reward fallback
│   ├── _http.py             # SSRF-guarded HTTP chokepoint (safe_post / safe_get)
│   ├── _version.py          # `__version__` + `__api_version__` (decoupled)
│   └── ...                  # benchmark, judge, merging, synthetic, wizard,
│                            # quickstart, model_card, fit_check, deploy, chat,
│                            # export, inference, results, utils
├── tests/                   # 70 test modules; count grows over time (run `pytest --collect-only -q` for current)
├── tools/                   # CI guards: check_anchor_resolution,
│                            # check_bilingual_parity, check_cli_help_consistency,
│                            # check_field_descriptions, check_no_analysis_refs,
│                            # check_pip_audit, check_bandit, check_site_claims,
│                            # check_usermanual_self_contained,
│                            # check_wizard_defaults_sync, generate_sbom,
│                            # generate_wizard_defaults, build_usermanuals
├── docs/
│   ├── roadmap.md           # Public roadmap (short index)
│   ├── roadmap/             # Detailed phase files + archive
│   ├── reference/           # User-facing API/config reference
│   ├── guides/              # User-facing tutorials
│   ├── usermanuals/{en,tr}/ # 4-section user manual (training, eval, deploy, ref)
│   ├── design/              # Design specs (internal)
│   ├── standards/           # Engineering standards (this project's rulebook)
│   ├── qms/                 # Quality management SOPs (EU AI Act Art. 17, EN+TR)
│   ├── analysis/            # Research, code reviews, external repo analyses
│   └── marketing/           # Local-only (gitignored): marketing + strategy
├── config_template.yaml     # Canonical YAML example — CI dry-runs this
├── pyproject.toml           # Build, deps, ruff, pytest, coverage config
├── CHANGELOG.md             # Keep-a-Changelog format
├── README.md                # User-facing project summary
├── CONTRIBUTING.md          # Human contributor guide
└── AGENTS.md                # This file
```

## Non-negotiable project principles

These come from the standards documents; summarized here for quick reference:

1. **Config-driven.** Behaviour is determined by validated YAML. No env-var sniffing for behaviour (only for secrets). No hardcoded feature flags.
2. **Reliability before features.** Every new capability ships with tests, docs, and CI coverage. "I'll add tests later" = the PR is not ready.
3. **Optional dependencies as extras.** Heavy deps (`bitsandbytes`, `unsloth`, `deepspeed`, `lm-eval`, `wandb`, `mergekit`) live under `[project.optional-dependencies]` and raise `ImportError` with an install hint when missing.
4. **Exit codes are a public contract.** 0/1/2/3/4/5 — see [error-handling.md](docs/standards/error-handling.md) for the full table (`0=success`, `1=config`, `2=training`, `3=eval-failure`, `4=awaiting-approval`, `5=wizard-cancelled`). CI/CD pipelines depend on these.
5. **Append-only audit log.** Every decision gate emits a structured event. Never edit or delete entries.
6. **No silent failures.** No bare `except:`, no `except Exception: pass`, no `|| true` in CI, no logging-and-swallowing for anything except explicitly-non-fatal paths (webhooks, cleanup).
7. **Bilingual where it counts.** User-facing docs are EN + TR mirrors. Code, CLI output, logs, config keys are English only.
8. **Config-driven features are opt-in.** Enterprise features (compliance export, human approval, safety eval) are opt-in; new users aren't burdened.

## What ForgeLM is not

Reinforced in [docs/marketing/strategy/05-yapmayacaklarimiz.md](docs/marketing/strategy/05-yapmayacaklarimiz.md). Do not propose or implement:

- **Web UI / GUI.** Config-driven is the identity. Dashboard for Pro CLI only.
- **Custom inference engine.** Hand off to Ollama / vLLM / TGI / llama.cpp.
- **Custom model architectures.** HuggingFace owns that.
- **Custom quantization kernels.** bitsandbytes / AWQ / GPTQ / HQQ own that.
- **Pretraining pipelines.** Fine-tuning only.
- **GPU marketplace or serving infra.** User brings their own GPU.
- **LLM leaderboards or community adapter zoos.** HF Hub already exists.

If a task pushes in any of those directions, raise it with the user before implementing.

## Common pitfalls (from prior reviews)

Learned the hard way across multiple PR-cycle audits and external-repo
comparisons; treat each bullet as a hard rule:

- **Documentation drift** — marketing claims that code doesn't back up. Every README claim must point to real code.
- **Silent import fallbacks** — `try: import X; except: X = None` hides missing deps behind mysterious `AttributeError` later.
- **CI `|| true`** — fake green status. Outlawed.
- **Stub code tagged "Production Ready"** — `NotImplementedError("Planned for Phase N", issue=#42)` only, never silent stubs.
- **Single-language comment drift** — mixing Turkish + Spanish + English in code comments. English only in code.
- **Zero-byte or misplaced files** — leftover artifacts from refactors. Clean up.

## How to work on a task

Default workflow for a non-trivial change:

1. **Understand first.** Read the relevant standard. Read the similar existing code.
2. **Plan second.** If the task is multi-step, use `TodoWrite` to track your plan.
3. **Invoke the right skill.** If the task maps to one, follow the SKILL.md end-to-end.
4. **Code third.** Smallest possible diff. One concern per change.
5. **Test immediately.** Write the test before or alongside the code, never after merge.
6. **Verify before opening PR.** Run the self-review command:

   ```bash
   ruff format . && ruff check . && pytest tests/ && \
     forgelm --config config_template.yaml --dry-run && \
     python3 tools/check_bilingual_parity.py --strict && \
     python3 tools/check_anchor_resolution.py --strict && \
     python3 tools/check_cli_help_consistency.py --strict && \
     python3 tools/check_wizard_defaults_sync.py && \
     python3 tools/check_no_analysis_refs.py && \
     python3 tools/check_no_unguarded_sys_modules_pop.py && \
     python3 tools/check_audit_event_catalog.py --strict && \
     python3 tools/check_tr_links_prefer_mirror.py --strict && \
     python3 tools/check_usermanual_self_contained.py --strict && \
     python3 tools/update_site_version.py --check
   ```

   All fourteen must pass. The first four are the historical gauntlet;
   the three doc guards (Wave 3 / Wave 4 / Wave 5 additions) catch
   bilingual structural drift, broken markdown anchors, and CLI ↔ docs
   help-text drift before the PR opens. The wizard-defaults guard
   (review-cycle 3) catches schema-vs-shipped-JSON drift for the
   wizard's source-of-truth defaults. The working-memory-refs guard
   (review-cycle 5) keeps the public tree from citing gitignored
   `docs/marketing/` or `docs/analysis/` paths — see
   `docs/standards/documentation.md` "Working-memory directories".
   The unguarded-`sys.modules`-pop guard (v0.5.7 round-4) flags any
   `sys.modules.pop("torch"|"numpy"|"trl"|…)` without
   `monkeypatch.delitem` — the v0.5.7 round-3 review traced 35
   spurious full-suite failures to that exact pattern.  The
   audit-event-catalog guard (full-project-review W0/C7) cross-checks
   every dotted audit event emitted in `forgelm/` against the canonical
   table in `docs/reference/audit_event_catalog.md` in both directions —
   the append-only audit log is an EU AI Act Art. 12 contract, and this
   guard was previously unwired while six `pipeline.*` stage events
   drifted into the code uncatalogued.  The TR-links-prefer-mirror
   guard (full-project-review W1/H11, F-P8-C-04) fails when a
   `docs/**/*-tr.md` page links the un-suffixed English sibling even
   though a `<stem>-tr.md` mirror exists — a Turkish reader following
   an in-prose link must stay in Turkish; the `**Ayna:**` backlink
   line is the one exempt case.  62 leaks across 19 files were swept
   to zero when this guard landed — see `docs/standards/localization.md`
   "Structural mirror rule".  The
   usermanual self-contained guard (post-v0.7.0 cycle) blocks any
   link inside `docs/usermanuals/` that would 404 in the static SPA
   viewer: every link must be either a `#/<section>/<page>` SPA
   route backed by a real manual page or an absolute HTTPS URL.
   Repo-relative `../../../guides/...` references fail the gate —
   see `docs/standards/documentation.md` "User-manual link
   discipline".  The site-version guard (v0.6.0 retag cycle)
   re-derives the marketing site's displayed version from CHANGELOG's
   latest released header and fails the PR if any of the 15+ literals
   across `site/*.html` and `site/js/translations.js` has drifted; the
   v0.5.5 → v0.6.0 release shipped with the hero badge still reading
   `v0.5.5`, which this guard now prevents.

## Etiquette when communicating with the user

- **State results directly.** No filler like "Great question!" or "Let me help you with that."
- **Brief updates during work.** One sentence per tool call max. Silence is worse than terse.
- **Surface decision points.** If you encounter something that requires a judgement call beyond the stated task, stop and ask. Don't silently expand scope.
- **Flag trade-offs.** If your implementation picks A over B for non-obvious reasons, say so in your summary.
- **Turkish is welcome.** User writes in Turkish; respond in Turkish unless technical content is clearly cleaner in English. Code and file content: English only.

## When in doubt

1. Check the relevant [docs/standards/](docs/standards/) file.
2. Check for a matching skill in [.agents/skills/](.agents/skills/).
3. Find the closest existing pattern in the codebase and follow it.
4. Ask the user rather than guess.

## Memory and context

- The `docs/marketing/` directory is gitignored (internal strategy). Content there is real; treat it as a source of truth for direction but don't reference it in public-facing code or docs.
- The `docs/analysis/` directory is gitignored research / audit working memory (PR-cycle review notes, external-repo comparisons, drafts). **Never reference its contents from production code, public docs, CHANGELOG entries, commit messages, or PR descriptions.** Decisions distilled from those notes live in `docs/standards/`, `docs/roadmap/`, the CHANGELOG, and inline code comments — those are the citations reviewers see.
- The roadmap ([docs/roadmap.md](docs/roadmap.md)) is what ships. The marketing strategy roadmap ([docs/marketing/marketing_strategy_roadmap.md](docs/marketing/marketing_strategy_roadmap.md)) is what gets announced. Don't conflate the two.

---

**If you've read this far and you're about to start work:** open the relevant standard + skill now. Then begin.
