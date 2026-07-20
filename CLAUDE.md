# CLAUDE.md — Project Guidance for AI Agents

> **Audience:** Claude Code (and other AI coding agents) working on ForgeLM. Complements — does not replace — the human-facing [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/standards/](docs/standards/).

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

When a task maps to a common pattern, invoke the matching skill from [.claude/skills/](.claude/skills/):

| Task | Skill |
|---|---|
| Adding a YAML config field | [add-config-field](.claude/skills/add-config-field/SKILL.md) |
| Adding a larger trainer / evaluator / module feature | [add-trainer-feature](.claude/skills/add-trainer-feature/SKILL.md) |
| Writing tests | [add-test](.claude/skills/add-test/SKILL.md) |
| Updating bilingual docs (EN ↔ TR) | [sync-bilingual-docs](.claude/skills/sync-bilingual-docs/SKILL.md) |
| Reviewing a PR (own or peer) | [review-pr](.claude/skills/review-pr/SKILL.md) |
| Cutting a release | [cut-release](.claude/skills/cut-release/SKILL.md) |

Each skill's `SKILL.md` has the full checklist. Follow it; don't skip steps to save time.

## Repository structure at a glance

```text
ForgeLM/
├── forgelm/                 # Source code: ~21 single-file modules + 4 sub-packages
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
│   ├── wizard/              # Interactive --wizard config generation: _collectors,
│   │                        # _orchestrator, _state, _byod, _io, _defaults.json
│   ├── config.py            # Pydantic schemas (23 models)
│   ├── trainer.py           # TRL wrapper (SFT/DPO/SimPO/KTO/ORPO/GRPO)
│   ├── model.py             # HF + PEFT model loading
│   ├── data.py              # Dataset loading + format detection
│   ├── ingestion.py         # Raw docs → SFT JSONL (`forgelm ingest`)
│   ├── safety/              # Safety package (post-v0.9.1 split): _types,
│   │                        # _inputs, _generate, _classifier,
│   │                        # _score_classification, _score_generation,
│   │                        # _gates, _results, _orchestrator
│   ├── compliance.py        # EU AI Act Articles 9-17 + Annex IV + GDPR purge / reverse-pii primitives
│   ├── webhook.py           # Slack/Teams notifications (5-event vocabulary)
│   ├── grpo_rewards.py      # Built-in GRPO format/length shaping reward fallback
│   ├── _http.py             # SSRF-guarded HTTP chokepoint (safe_post / safe_get)
│   ├── _version.py          # `__version__` + `__api_version__` (decoupled)
│   └── ...                  # benchmark, judge, merging, synthetic,
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
└── CLAUDE.md                # This file
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

Reinforced in the internal marketing strategy notes (`docs/marketing/strategy/05-yapmayacaklarimiz.md`, gitignored, not present in this checkout). Do not propose or implement:

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
   python3 tools/check_import_origin.py --strict && \
     ruff format . && ruff check . && pytest tests/ && \
     python3 -m forgelm --config config_template.yaml --dry-run && \
     python3 tools/check_bilingual_parity.py --strict && \
     python3 tools/check_anchor_resolution.py --strict && \
     python3 tools/check_cli_help_consistency.py --strict && \
     python3 tools/check_cli_exit_code_prose.py --strict && \
     python3 tools/check_wizard_defaults_sync.py && \
     python3 tools/check_no_analysis_refs.py && \
     python3 tools/check_no_unguarded_sys_modules_pop.py && \
     python3 tools/check_audit_event_catalog.py --strict && \
     python3 tools/check_tr_links_prefer_mirror.py --strict && \
     python3 tools/check_usermanual_self_contained.py --strict && \
     python3 tools/check_notebook_pins.py --strict && \
     python3 tools/check_usermanual_schema_drift.py --strict && \
     python3 tools/check_deprecation_targets.py --strict && \
     python3 tools/check_release_record_sync.py --strict && \
     python3 tools/check_skill_mirror_parity.py --strict && \
     python3 tools/check_source_path_refs.py --strict && \
     python3 tools/check_readme_links.py --strict && \
     python3 tools/update_site_version.py --check
   ```

   **Do not "simplify" `python3 -m forgelm` back to `forgelm`.** A
   console script's `sys.path[0]` is its own `bin/` directory, never the
   cwd, so `forgelm …` imports whatever is installed in site-packages; a
   stale non-editable install made that step validate a weeks-old
   package while reporting success on an unrelated working tree. `-m`
   puts the cwd first on `sys.path`, so it runs the checkout. The
   import-origin guard leads the chain for the same reason and must stay
   first: it asserts the premise every later step depends on — that the
   `forgelm` being imported is the one you just edited — and `-m` alone
   does not cover the `tools/check_*.py` guards that import `forgelm`
   with `sys.path[0] == tools/`.

   All twenty-three must pass (the usermanual-schema-drift guard —
   `check_usermanual_schema_drift.py --strict` — validates that every
   fenced YAML key under `docs/usermanuals/` resolves against the real
   `ForgeConfig` schema, catching fabricated-field examples that would
   fail `--dry-run`). The first four are the historical gauntlet;
   the three doc guards (Wave 3 / Wave 4 / Wave 5 additions) catch
   bilingual structural drift, broken markdown anchors, and CLI ↔ docs
   help-text drift before the PR opens.  Its companion, the
   exit-code-prose guard (`check_cli_exit_code_prose.py --strict`,
   Step 3 follow-up), covers the half `check_cli_help_consistency.py`
   does not: that guard validates invocation *syntax*, never exit-code
   *prose*, so `forgelm verify-audit --help` told operators "1 means
   tampering" for a full release cycle after the routing moved to
   `EXIT_INTEGRITY_FAILURE` (6).  It asserts one bit per subcommand in
   both directions — a dispatcher that can emit 6 must say so in
   `--help`, one that cannot must not claim it — with both sides
   derived from source (`_dispatch.py`'s routing table plus the
   `subcommands/` modules against `_parser.py`'s `help=` literals), so
   there is no mapping table to rot. The wizard-defaults guard
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
   `v0.5.5`, which this guard now prevents.  The notebook-pin guard
   (v0.7.0 / F-P8-C-09) verifies that every `*.ipynb` in the repo
   pins its kernel and package versions so that example notebooks
   remain reproducible; it was wired into CI at the v0.7.0 pin bump
   but was absent from the local gauntlet until this update.  The
   deprecation-target guard (post-v0.9.0 Opus review) reads
   `forgelm/config.py`'s `DEPRECATION_REMOVAL_VERSION` constant as the
   single source of truth for when `lora.use_dora` / `lora.use_rslora` /
   `training.sample_packing` disappear, then fails on any claim in
   `forgelm/`, `config_template.yaml`, `docs/` or `tests/` that names a
   different version — and on a target the shipping `pyproject.toml`
   version has already reached, since removing a YAML field is MAJOR
   (`docs/standards/release.md`) and a due promise is a false one. The
   literal was previously duplicated across ~20 sites and rotted twice
   (v0.9.0 → v0.10.0 → v1.0.0), each retarget leaving stragglers behind.
   The release-record-sync guard (post-v0.9.0 backlog sweep) enforces the
   `cut-release` skill's release-record step: every `## [X.Y.Z] — DATE`
   heading in `CHANGELOG.md` must have a non-planned section in
   `docs/roadmap/releases.md`, and `docs/roadmap.md`'s `**Released:**`
   headline must name the newest released version. That step used to live
   after the satisfying part of a release, which is when a checklist stops
   being read — it was skipped for two consecutive releases (v0.8.0 and
   v0.9.0), leaving the public roadmap announcing v0.7.0 while PyPI had
   v0.9.0. It has since been moved into the *pre-release* checklist
   (`docs/standards/release.md` step 7 / the skill's step 4.5) so the
   guard is satisfiable before the tag fires `publish.yml`; post-tag, it
   could only turn red on the next PR, after the stale roadmap had already
   shipped. A `(Planned)` section is a promise, not a record, and never
   satisfies a released version. The skill-mirror-parity guard (same
   cycle) pins `.claude/skills/<name>/` against `.agents/skills/<name>/`:
   same skills, same files, identical content once the documented
   substitution allowlist (`.claude/`↔`.agents/`, `CLAUDE.md`↔`AGENTS.md`,
   `Claude`↔`Codex`) is applied. The two trees are one document shipped to
   two harnesses and edited by hand — nothing compared them until the
   cut-release reorder above had to be written twice, and a one-copy edit
   leaves half of all agent runs following the superseded procedure.
   The source-path-reference guard (post-v0.9.1 `safety/` split) fails
   when prose names a `forgelm/`, `tools/` or `tests/` path that does not
   exist on disk. The `forgelm/safety.py` → `forgelm/safety/` split moved
   the file cleanly and shipped **39 dangling references across 16 files**
   in the very commit whose purpose was moving it. Nothing noticed,
   because nothing could see them: `check_anchor_resolution.py` validates
   `[text](href)` links under `docs/` only, while the dead references were
   backticked inline paths, `.claude/`/`.agents/` skill checklists,
   `site/*.html` copy, notebook JSON, and the repository-structure tree in
   this file. Scope was set by measurement, not taste — matching every
   top-level directory yielded 266 findings on a clean tree, so the guard
   is restricted to the three real source trees (14 findings, all genuine)
   and skips fenced blocks, `docs/roadmap/` and `docs/design/` (records and
   unbuilt proposals), and Markdown link hrefs (the anchor guard's job, so
   a broken link is reported once, never twice). When a reference must keep
   a path that no longer exists — a statement about the past, or a file the
   reader is being told to create — add it to `_EXEMPT` with a written
   justification rather than weakening the pattern.
   The README-link guard (post-v0.10.0 README audit) closes the last
   uncovered high-traffic surface. `pyproject.toml` sets
   `readme = "README.md"` with no long-description URL rewriting, so PyPI
   serves the file verbatim and every *relative* href in it resolves
   against `pypi.org` — dead for precisely the reader who arrived by
   running `pip install forgelm`. **38 such links shipped**, and no guard
   could see them: `check_anchor_resolution.py` defaults to `--root docs`
   and reports "OK: 259 markdown file(s) under docs/" without ever opening
   the README, `check_source_path_refs.py` scans the README but only for
   backticked source paths, and `check_doc_numerical_claims.py` walks
   `DOCS.rglob("*.md")`. The project's front door sat outside the coverage
   of every guard that would have kept it honest, which is why it
   accumulated fourteen false claims while `docs/` stayed comparatively
   clean. The guard applies the absolute-https rule only to the surface
   that is actually rendered off GitHub (`README.md`) and the
   resolve-on-disk rule to every surface it scans — `CONTRIBUTING.md`
   keeps its relative links, because they are correct there and a guard
   that fires on correct input gets disabled. Both halves are offline: an
   in-repo `blob/main/<path>` URL is checked by stripping the prefix, so
   converting a link to absolute form cannot trade a PyPI 404 for a
   universal one.

## Etiquette when communicating with the user

- **State results directly.** No filler like "Great question!" or "Let me help you with that."
- **Brief updates during work.** One sentence per tool call max. Silence is worse than terse.
- **Surface decision points.** If you encounter something that requires a judgement call beyond the stated task, stop and ask. Don't silently expand scope.
- **Flag trade-offs.** If your implementation picks A over B for non-obvious reasons, say so in your summary.
- **Turkish is welcome.** User writes in Turkish; respond in Turkish unless technical content is clearly cleaner in English. Code and file content: English only.

## When in doubt

1. Check the relevant [docs/standards/](docs/standards/) file.
2. Check for a matching skill in [.claude/skills/](.claude/skills/).
3. Find the closest existing pattern in the codebase and follow it.
4. Ask the user rather than guess.

## Memory and context

- The `docs/marketing/` directory is gitignored (internal strategy). Content there is real; treat it as a source of truth for direction but don't reference it in public-facing code or docs.
- The `docs/analysis/` directory is gitignored research / audit working memory (PR-cycle review notes, external-repo comparisons, drafts). **Never reference its contents from production code, public docs, CHANGELOG entries, commit messages, or PR descriptions.** Decisions distilled from those notes live in `docs/standards/`, `docs/roadmap/`, the CHANGELOG, and inline code comments — those are the citations reviewers see.
- The roadmap ([docs/roadmap.md](docs/roadmap.md)) is what ships. The marketing strategy roadmap (`docs/marketing/marketing_strategy_roadmap.md`, gitignored, not present in this checkout) is what gets announced. Don't conflate the two.

---

**If you've read this far and you're about to start work:** open the relevant standard + skill now. Then begin.
