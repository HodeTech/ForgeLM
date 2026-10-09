# Phase 16: Trust Surface Hardening (full-project review remediation)

> **Status:** **In progress — pre-work, S1 and S2 delivered; S3-S16 are not.**
> This document is the execution plan approved on 2026-07-30 for the 83
> remediation units produced by the 2026-07-29/30 full-project review.
> Delivered so far: the pre-work standards reconciliation (P-1…P-5c);
> **S1** — `forgelm/verify.py` split into `forgelm/verify/`, the guard-inventory
> meta-test made bidirectional across three documents, and the public-surface
> `mypy --strict` gate wired and green; and **S2** — the numeric domain modelled
> in the schema, four gates that failed *open* on a non-finite value now failing
> closed, and every artefact writer routed through a strict-JSON chokepoint.
> **Ten of the eighty-three units are closed — `OPS-09`, `OPS-10`, `OPS-23`
> in S1, and `C31-NUMERIC-CONFIG`, `C33-TRAIN-JSON-CONTRACT`, `CORE-03`,
> `CORE-10`, `CORE-14`, `GAP-02`, `TRUST-15` in S2 — and seventy-three are
> not.**  Counted the way §Coverage cross-check counts: a cluster is one unit,
> so S2's seven canonical units absorb nine review findings.
> (Plus the seven pre-work corrections P-1…P-5c, which §Pre-work excludes from
> the unit count by design.)  Do not read "Phase 16 exists" as "Phase 16
> is done" — the checkbox state below is the record.
>
> **Phase number.** 16 follows [Phase 15](completed-phases.md#phase-15--ingestion-pipeline-reliability-v060)
> in the main sequential track.  The `Phase 22` row in [roadmap.md](../roadmap.md)
> belongs to the separate Faz 1-38 closure-cycle numbering used for `v0.5.5`
> and is not part of this sequence; 16-21 were never allocated.
>
> **Release cycle: the open `0.11.1rc1` cycle.  Release *number*: derived at
> the cut, not fixed here.**  The work lands on the current development cycle;
> what it is tagged as follows from what it contains.
> [`../standards/release.md`](../standards/release.md#versioning) defines PATCH
> as "bug fixes, docs, dependency version tweaks, internal refactor with **no
> user-visible change**", and its deprecation cadence adds "patch releases
> never remove anything".  This phase carries three `### Breaking` steps —
> `cache-tasks` moving exit 0 → 2 is literally the table's "changing an exit
> code's meaning: **Yes**" row — so if those steps land as planned the same
> table forces a **MINOR** bump.  The number is therefore a *consequence* of
> the delivered content, resolved by the `cut-release` checklist against the
> table, and stating it in advance is exactly the prediction that has already
> rotted three times in this repository (`F-PR29-*` → `v0.6.x`, `F-PR54-*` →
> `v0.7.x` → `v0.9.x`, the module-size labels).  Deferrals below likewise
> state a **budget or a condition, never a due date**.

**Goal:** Close the gap between what ForgeLM *reports* and what ForgeLM
*established*.  The review found no broken tests and no failing guard — it
found success bits that are set without the evidence to justify them: quality
and safety gates that pass on `NaN`, verifiers that report `valid` from a
4-byte header, a cache command that reports `completed` when every download
failed, a release matrix that installs a wheel and then tests the checkout,
and published claims about legal dates, container images and exit codes that
the code does not back.

**Priority:** High — the review placed the baseline on **HOLD** for the next
stable tag.  The blocking families are irreversible data loss on the export
path, fail-open evaluation gates, two advertised trainers that cannot import
on any permitted dependency version, and a publish DAG that has never executed
a line of the wheel it publishes.

**Estimated Effort:** Large.  Sixteen steps, five of them XL (S2, S7, S8, S12, S15).  Each step
carries its own Opus and Sonnet review round before the next begins.

> **Context:** The 2026-07-29/30 full-project review examined 628 tracked files
> (211,279 lines) against a green baseline — 4,560 tests passing, 86.60 %
> statement coverage, 29/29 CI guards green.  It produced 95 raw evidence
> records, reduced by strict same-root-cause/same-fix/same-regression-suite
> dedupe to **83 canonical remediation units** (0 Critical / 24 High /
> 52 Medium / 7 Low).  All 27 raw HIGH claims were independently re-verified
> against the live tree before this plan was written; **27/27 were confirmed**,
> 14 of them proving *broader* than first reported.  Seven sub-claims and two
> recommended remedies were refuted — those are recorded in §"Refuted" below
> rather than silently dropped.

## Finding IDs

Findings keep the IDs the review campaign assigned: **`F-W20260729-<topic>-<NN>`**,
where `<topic>` ∈ `CORE` (config/model/data/trainer/evaluation/merge),
`TRUST` (compliance/audit/safety/ingestion/GDPR), `DOCS` (docs/site/notebooks/
localization), `OPS` (CI/release/packaging/test system), `CLI` (CLI/wizard/
chat/export/deploy/cache), `GAP` (cross-wave production-ledger and test-oracle
gaps).

This already matches the canonical wave-scoped scheme in
[`../standards/code-review.md`](../standards/code-review.md#finding-id-naming-convention)
(`F-W<N>-<topic>-<NN>`), so **no re-key is performed**.  A re-key was
considered and rejected: rule 2 of that section puts the ID into permanent git
history, and renaming IDs that are already canonical would create an alias map
with no beneficiary.

Eleven units are clusters of two or three raw IDs that share one root
boundary, one fix and one regression suite.  They are written `C31-*` / `C33-*`
below and always listed with their members.

**Cross-reference rules apply** ([`code-review.md`](../standards/code-review.md#finding-id-naming-convention)):
the absorbing commit cites `Absorbs F-W20260729-CORE-01` in the **message
body**, never the title; CHANGELOG cites an ID only where the finding
warranted a public call-out.

## Decisions this phase executes

Twenty cross-cutting decisions were settled before Step 1.  Each is recorded
as a dated narrative section in [risks-and-decisions.md](risks-and-decisions.md)
with its rejected alternatives named.  The table below is an index, not the
record.

| # | Decision | Outcome | Step |
|---|---|---|---|
| C-1 | One non-finite benchmark task score fails the whole gate | Fail-closed; valid-fraction floor deferred as a budget row | S2 |
| C-2 | `evaluation.llm_judge.min_valid_fraction` default | `0.8` | S3 |
| C-3 | `verify-gguf` magic-only verdict | Opt-in `--require-strong-layer`; default flip deferred to the next MAJOR | S5 |
| C-4 | `forgelm export` writes the `.sha256` sidecar its docs already claim | Yes | S5 |
| C-5 | `cache-tasks` partial batch | Hard failure (`success:false`, exit 2) | S3 |
| C-6 | `model_lineage.quantization` semantics | Keep meaning; add `*_effective` beside it | S8 |
| C-7 | Effective fields when no training run occurred | `"not_observed"` sentinel | S8 |
| C-8 | `MergeInput` strictness | `extra='forbid'` + `gt=0.0` | S2 |
| C-9 | `training.gradient_checkpointing` as a real field | No — delete fit-check's invalid recommendation | S8 |
| C-10 | ORPO / SimPO on a dependency range that exports neither | Route through `trl.experimental` | S4 |
| C-11 | `[export]` extra contract | External converter becomes an explicit, validated prerequisite | S7 |
| C-12 | Wheel testing | Accept the split: source suite + new wheel-contract step | S12 |
| C-13 | Release-record check on rc tags | Exempt PEP 440 pre-releases; still enforce tag↔version and ancestry | S12 |
| C-14 | Publish gated on supply-chain scan | Yes — add the gate | S12 |
| C-15 | Container distribution | Local `docker build` is the contract; GHCR tracked as a condition | S13 |
| C-16 | `docker-compose.yaml` cache path | Fix in the same change, `### Fixed` naming the orphaned-volume effect | S13 |
| C-17 | Finding-ID re-key | **Rejected** — IDs are already canonical | — |
| C-18 | Split `forgelm/trainer.py` before the behaviour steps | No — budget-raise per step | S1 |
| C-19 | Coverage ratchet | Diff-coverage enforcing at S14 only; global floor = measured − 2. **Amended 2026-07-30** — the "advisory at S1" limb was withdrawn during S1: a non-failing CI step needs `continue-on-error`/`\|\| true`, which principle 6 outlaws | S14 |
| C-20 | Does this phase cut a release | No — every PR files under `[Unreleased]` | — |
| GTM | AI Act moat framing after Regulation (EU) 2026/1744 | Retarget to 2 December 2027, framing preserved | S15 |

## Three structural facts that fix the ordering

**1. Six of the eight grandfathered modules have 0-4 LOC of headroom, and this
phase grows all of them.**  Measured at baseline with
`./.venv/bin/python tools/check_module_size.py`:

| Module | LOC | Budget | Headroom | Steps that grow it |
|---|---:|---:|---:|---|
| `forgelm/config.py` | 1795 | 1795 | **0** | S2, S3, S8, S10 |
| `forgelm/compliance.py` | 2471 | 2471 | **0** | S8, S9 |
| `forgelm/ingestion.py` | 2110 | 2110 | **0** | S6, S10 |
| `forgelm/cli/subcommands/_purge.py` | 1215 | 1215 | **0** | S9 |
| `forgelm/verify.py` | 1013 | 1013 | **0** | S5 |
| `forgelm/cli/_parser.py` | 1370 | 1372 | 2 | S5, S7, S11 |
| `forgelm/cli/_pipeline.py` | 1331 | 1332 | 1 | — |
| `forgelm/trainer.py` | 1456 | 1460 | 4 | S2, S3, S4, S8 |

Growth past budget is **fatal in every mode**, and a budget raised above the
immutable `deferred_at_loc` with an empty `budget_history` fails
unconditionally.  This makes `OPS-10` a prerequisite rather than a cleanup, and
it is why S1 is first.  Per **C-18** each step carries its own
`budget_history` line in its own diff; `trainer.py` is not split first, because
a god-object split immediately ahead of four behaviour changes maximises
rebase risk.

The ledger names its own first split.  `tools/check_module_size.py` on
`verify.py`: *"deferred rather than split in the same change because the split
moves the exit-code routing tokens that the CLI and tests both pin, and that
belongs in its own diff."*  S5 is exactly the diff that moves those tokens.
So `verify.py` is split in S1, behaviour-neutral, before S5 changes verdict
semantics.

**2. The gauntlet runs under `./.venv/bin/python`.**  The system `python3` is
3.9.6 and lacks `packaging` / `tomllib`; `check_release_record_sync.py` and
`check_deprecation_targets.py` fail there for environment reasons that are
indistinguishable from findings.  Every step's verification block uses the
venv interpreter, and every dry-run uses `python3 -m forgelm`, never the
console script — see [`CLAUDE.md`](../../CLAUDE.md) "Verify before opening PR"
for why.

**3. There is no ADR convention in this repository, and one is not invented
here.**  The established form is a dated narrative section in
[risks-and-decisions.md](risks-and-decisions.md) — canonical exemplar,
`### 2026-07-20 — Module-size deferrals re-tracked as budgets (no target version)`.
Creating `docs/adr/` would itself violate
[`../standards/documentation.md`](../standards/documentation.md)'s directory
rules without a standards PR first.

## Pre-work: the standards themselves are wrong

These are not findings.  They are errors in the documents a remediation agent
reads *before* touching code, and each would actively mislead the work below.
[`../standards/README.md`](../standards/README.md) meta-rule 4 makes batching
doc corrections **ahead** of code work explicitly legitimate; batching them
after is not.

- [x] **P-1 — Bare `forgelm …` invocations in normative documents.**
  `code-review.md:226`, `testing.md:184,263`, `error-handling.md:299` and six
  `SKILL.md` files invoke the console script.  A console script's `sys.path[0]`
  is its own `bin/`, so a stale non-editable install validates a weeks-old
  package while reporting success.  Replace with `python3 -m forgelm` and
  prepend `check_import_origin.py --strict`.  **Highest-risk item in the
  phase**: this is precisely how a remediation agent would "verify" a fix that
  never ran.
- [x] **P-2 — `CLAUDE.md:113` / `AGENTS.md:113` publish the exit contract as
  `0/1/2/3/4/5`.**  `EXIT_INTEGRITY_FAILURE = 6` exists
  (`forgelm/cli/_exit_codes.py:39`) and the same files explain it ninety lines
  later.  Principle 4 is labelled non-negotiable; an agent treating the
  truncated list as binding will misclassify a genuine integrity failure.
  *(Two lines only.  The meta-test and the `compliance_summary` prose are
  `OPS-24` in S15.)*
- [x] **P-3 — `code-review.md:111` (`≤ 10 ms`) contradicts `regex.md:157`**
  ("don't pin a hard ms cutoff"; existing tests use ≤ 100 ms / ≤ 1 s).  One
  order of magnitude apart, and S10 and S15 both touch regexes.
- [x] **P-4 — Two incompatible closed lists of CHANGELOG categories.**
  `documentation.md:281` says "Added / Changed / Fixed / Removed / Deprecated.
  No others."; `CHANGELOG.md` uses `### Breaking` and `### Security`, and this
  phase will file both.  Delete the list from `documentation.md`; cross-link
  `release.md#changelog`.
- [x] **P-5 — `forgelm/cli.py` has not existed since Phase 15**, yet
  `error-handling.md:68`, `logging-observability.md:47`,
  `architecture.md:17,249`, `documentation.md:219` and two skills cite it as
  live.  Invisible to `check_source_path_refs.py` (mermaid fences and bare
  basenames).  Steps S5-S11 all edit `forgelm/cli/`.
- [x] **P-5b — Skill-taught patterns that are stale or wrong.**
  `add-config-field/SKILL.md:45-59` teaches a field style that **fails**
  `check_field_descriptions.py --strict`; `:37-38` names `ComplianceConfig` /
  `TrackingConfig`, which do not exist (the real classes are
  `ComplianceMetadataConfig`, `MonitoringConfig`, `RiskAssessmentConfig`).
  `add-test/SKILL.md:113,123` teaches two mock targets that intercept nothing
  after the `safety/` and `_http.py` refactors — a test written to that
  instruction passes while exercising nothing.
  `sync-bilingual-docs/SKILL.md:8,120` claims CI does not enforce bilingual
  parity, false since Wave 3.  `review-pr/SKILL.md` says "seven-question" at
  `:22` and "six questions" at `:111,:187`, and its step-6 one-liner
  under-tests by nineteen commands.
- [x] **P-5c — Counts and rosters in the rulebooks.**  `CLAUDE.md:79` says
  "~70 test modules" (actual **124**); `:49` says "~21 single-file modules + 4
  sub-packages" (actual **27** modules + **5** packages, roster omitting
  `verify.py`, `_pypdf_normalise.py`, `_script_sanity.py`,
  `_strip_pattern.py`); `architecture.md:164` and `CLAUDE.md:72` say the
  webhook vocabulary is 5 events (it is **8**, already documented at
  `logging-observability.md:143-167`) — S3 and S8 must not act on the 5-event
  premise.  `CONTRIBUTING.md:105` repeats the stale test-file count.

**Mirror discipline.**  Every `.claude/skills/` edit is two identical writes
(`check_skill_mirror_parity.py --strict`; substitution allowlist is
`.claude/`↔`.agents/`, `CLAUDE.md`↔`AGENTS.md`, `Claude`↔`Codex` only).
`CLAUDE.md` + `AGENTS.md` are pinned by `tests/test_guard_wiring.py:98-101`;
`CONTRIBUTING.md` is pinned by nothing.  Treat all five documents plus both
skill trees as one atomic edit unit.

**Deliberately excluded from pre-work:** `CONTRIBUTING.md`'s missing
`check_readme_links.py` entry and its stale guard count are `OPS-09` and belong
to **S1**, which also lands the reverse-parity meta-test that prevents
recurrence.  Fixing the symptom without the guard is what produced the drift.

## Tasks

Ordering principle throughout: **behaviour → the contracts that describe
behaviour → the documents that describe the contracts.**  No step reopens a
file an earlier step has settled for the same reason.

Each step's exit condition is: implementation committed, then an aggressive
multi-dimensional Opus review round with verified findings fixed and
committed, then the same with Sonnet, then the next step.

---

1. [x] **S1 — Measurement instruments: module budgets, guard inventory, typing gate** (M)
   Units: `OPS-10`, `OPS-09`, `OPS-23`.

   All three are the same defect class: *the project's instruments for
   measuring the project are miscalibrated or unwired.*  The size ledger
   freezes debt with no exit, the local gauntlet advertises a completeness it
   does not have (it omits ten to eleven live CI guards, and the meta-test is
   one-directional so it cannot see the omission), and the typing gate is
   documented as CI-blocking while being neither wired nor green.

   - Split `forgelm/verify.py` → `forgelm/verify/{__init__,_annex_iv,_pipeline_evidence,_gguf}.py`,
     behaviour-neutral, preserving every `forgelm.verify.*` public name; remove
     its `_DEFERRED_SPLITS` entry.
   - Record the remaining seven budgets as a dated decision naming, per module,
     the split boundary, an owner and a **condition** for payment.
   - Make `tests/test_guard_wiring.py` bidirectional across `CLAUDE.md`,
     `AGENTS.md` **and** `CONTRIBUTING.md`, comparing guard **invocations**
     (name *and* flags), and pin the non-guard steps too. Replace the `>= 19`
     floor with set-equality against `ci.yml` in both directions — a count
     assertion was considered and rejected as subsumed by the set comparison,
     which is strictly stronger.
   - Add the `forgelm.__getattr__` return annotation; wire the documented
     `mypy --strict --follow-imports=silent forgelm/__init__.py forgelm/_version.py`
     into `ci.yml`; record the 47 internal typing errors as a budget, not a
     blocker.
   - Diff-coverage is **not** introduced here — see the 2026-07-30 amendment to
     C-19 in [risks-and-decisions.md](risks-and-decisions.md). It lands once, in
     S14, enforcing.

   *Exit:* wiring a new `tools/check_*.py` into `ci.yml` without adding it to
   all three documents fails the meta-test; removing a return annotation on an
   exported symbol turns CI red; aggregate deferred LOC drops by 1013.

2. [x] **S2 — Numeric-domain integrity: finite config, fail-closed gates, strict serializers** (XL, 4 commits)
   Units: `C31-NUMERIC-CONFIG` (= `CORE-01` + `CORE-05`), `CORE-03`, `GAP-02`,
   `C33-TRAIN-JSON-CONTRACT` (= `CLI-06` + `GAP-05`), `TRUST-15`, `CORE-10`,
   `CORE-14`.

   One root cause with three ingresses: **IEEE-754 non-finite values crossing a
   trust boundary at which a decision is made, plus CPython's permissive
   `json.dumps(allow_nan=True)` default.**  Every member is the same mechanism
   (`x > nan` is False, so every threshold comparison fails *open*) or the same
   serializer default.  `max_acceptable_loss: .nan` passes the loss gate;
   a `NaN` lm-eval score passes the benchmark gate; a `NaN` merge weight
   propagates into tensors — and SLERP does worse, silently discarding the
   operator's weights and interpolating at `t=0.5`.

   Commits: (1) config schema finite/bounded types + typed `MergeInput` +
   call-site `model_dump()`; (2) runtime `math.isfinite` fail-closed guards in
   trainer / benchmark / merging / safety gates; (3) serializer strictness —
   `allow_nan=False` plus a recursive sanitizer in the result envelope,
   `AuditLogger.log_event`, `_save_benchmark_json` and the webhook body;
   (4) the conditional `success is False ⇒ non-empty error` invariant + docs.

   Preserve and assert, do not re-implement: `eval_loss` is already fail-closed
   on NaN/Inf; a `-inf` threshold already fails *closed*; a task with no
   accuracy metric already fails a positive threshold; `min_score` is already
   bounded; a missing `path` and fewer than two merge models are already
   rejected; an exact zero weight-sum is already rejected.

   *Exit:* a parametrized `.nan / .inf / -.inf / out-of-range` matrix fails at
   both sub-config and `ForgeConfig` level with **exit 1**; a config built via
   `model_construct` cannot produce `passed=True` at any gate; no artefact can
   contain a bare `NaN`/`Infinity`, verified by reading back with a **strict**
   parser (`json.loads(..., parse_constant=_reject)`) — Python's permissive
   loader is never the sole oracle; deleting any `isfinite` guard turns a test
   red.

   **Delivered** across ten commits — the planned four, plus three in-flight
   corrections and the two review rounds.  Exit criteria, one by one:

   - *Parametrized matrix at both levels, exit 1* — **met.**  Sub-config level
     in `tests/test_config.py` / `tests/test_trainer.py`; whole-file level in
     `tests/test_config_non_finite_matrix.py`, which loads real YAML through
     the CLI's own loader and asserts `SystemExit.code == 1`.  That module
     also pins the premise (`.nan` really resolves to a float) and carries a
     negative control, because a schema that refused everything would satisfy
     a rejection-only matrix.
   - *`model_construct` cannot produce `passed=True`* — **met** for the loss
     gate and the safety gate, whose runtime guards are asserted against a
     config mutated after validation.  Not asserted for every gate in the
     codebase; S3 owns the remaining aggregate verdicts and inherits the
     obligation.
   - *No bare `NaN`/`Infinity`, strict-parser verified* — **met**, and
     widened: the Opus round found the four hand-patched call sites were four
     instances of a class with eighteen members, so `_strict_json.dumps_strict`
     became the single chokepoint and `tools/check_strict_json_writers.py`
     keeps new artefact writers from bypassing it.
   - *Deleting any `isfinite` guard turns a test red* — **met**, verified by
     mutation rather than by inspection each time.

   Two process defects surfaced here and are fixed structurally, not by
   resolve: a review agent's scaffolding (`if False:`) was swept into a commit
   by `git add -A` and disabled a live gate with the full gauntlet green — now
   blocked by `tools/check_no_mutation_artifacts.py`, and review rounds run in
   isolated worktrees.  And a multi-edit script aborted midway, so a fix the
   commit message claimed shipped had not — the guard is now that a fix of the
   form "stop returning a hardcoded literal" is tested by reading the
   expression out of `inspect.getsource`, not by asserting the returned value.

3. [ ] **S3 — Evidence sufficiency: per-item errors must reach the aggregate verdict** (L)
   Units: `TRUST-03`, `CORE-04`, `C33-CACHE-TASK-VERDICT` (= `CLI-07` + `GAP-04`).

   Identical root cause in three places: **a helper converts a per-item failure
   into a value (`""`, `None`, `cached=False`) and the aggregating caller never
   folds that value into the verdict, the audit event, or the exit code.**  In
   all three the outer `except` is structurally dead.  A target model that
   raises on every probe yields empty strings a classifier may score `safe`; an
   existing-but-empty judge prompt file returns `passed=True`; a `cache-tasks`
   batch in which every download failed returns `success:true`, exit 0 and a
   `cache.populate_tasks_completed` audit event.

   The sibling `_run_cache_models_cmd` already implements the correct pattern
   and is the in-repo template.

   Per **C-2**, `evaluation.llm_judge.min_valid_fraction` defaults to `0.8`.
   Per **C-5**, a partial `cache-tasks` batch is a hard failure; the
   contradicting best-effort contract in `json-output.md:612-617` is rewritten,
   not preserved.

   *Exit:* one `0/N, K/N, N/N` matrix per surface; a *successfully generated*
   empty response stays distinguishable from a generation failure (no naive
   `if not response` anywhere); "average below threshold" and "insufficient
   valid evidence" carry distinguishable `failure_reason` prefixes; the air-gap
   guide's `jq -e '.success'` gate stops an incomplete bundle.

4. [ ] **S4 — Dependency-range truth for the six advertised trainers** (L)
   Units: `GAP-03`, `CORE-02`.

   README, the site and the `trainer_type` enum advertise six alignment methods
   and both of TRL's official data formats; the shipped dependency contract
   delivers neither.  `ORPOConfig` / `CPOConfig` / `ORPOTrainer` / `CPOTrainer`
   are absent from the top-level `trl` namespace across the **entire** declared
   `trl>=1.0.0,<2.0.0` range — verified by inspecting the 1.0.0, 1.7.1 and
   1.9.2 wheels — so the review's "pin an upper bound" remedy does not exist.
   Separately, TRL hands conversational completions to custom rewards as
   structured message lists, and all four built-in reward paths assume `str`.

   Per **C-10**, add a version-aware `_import_trl_symbol` resolver that falls
   back to `trl.experimental.orpo` / `trl.experimental.cpo`, emits one INFO
   line per fallback naming the instability, and raises an actionable
   `ConfigError`-shaped failure naming the installed `trl.__version__` if
   neither location works.  **Never set `TRL_EXPERIMENTAL_SILENCE`** — that is
   the silent-import-fallback anti-pattern
   ([`CLAUDE.md`](../../CLAUDE.md) "Common pitfalls").

   Add `_completion_text()` to `forgelm/grpo_rewards.py` as the single
   canonicalisation point, with a documented policy, routed through all four
   reward paths.  Never `str(list)`.

   *Exit:* all six `trainer_type` values construct training args **and** a
   trainer at both the declared minimum and the resolver-current endpoint with
   **zero skips** (asserted, `pytest -rs`); a missing advertised trainer class
   **fails**, never `pytest.skip()`; a parity matrix proves
   `f(['…']) == f([[{'role':'assistant','content':'…'}]])` for all four reward
   paths; flat-string outcomes are byte-identical to baseline.

5. [ ] **S5 — Verification truth: three states, not a success bit** (L)
   Units: `TRUST-04`, `TRUST-05`, `CLI-03`.

   One modelling error in three verifiers: **a three-valued reality
   (verified / unverified / invalid) projected onto a two-valued `valid` plus
   an exit code**, so "no strong layer ran" shares a success bit with "every
   strong layer passed".  Broader than reported: the exporter **never writes**
   a `.sha256` sidecar for GGUF — the only writer of `<path>.sha256` in
   `forgelm/` is `utils.py:132-148`, for checkpoint tarballs — and `gguf` is in
   no dependency group, so magic-only `valid=true` is the **default** outcome
   for ForgeLM's own artefacts, not an edge case.  `verify.py:997-999`,
   `gguf-export.md:19,22` and `verify-gguf.md:168` all state the opposite.

   `CLI-03` is the writer half of the same seam: export writes its hash to
   `exported_artifacts`, a key the verifier never reads.  It fails *closed* —
   the verifier reports `added` and refuses to pass — so this is a
   round-trip-integrity defect, not a false PASS.

   Per **C-3**, add `checks['strong_layer_ran']` and an opt-in
   `--require-strong-layer`; the default flip is deferred to the next MAJOR
   because default-fail breaks the `jq -e '.valid'` CI recipe this project's
   own manual recommends.  Per **C-4**, `forgelm export` starts writing the
   sidecar its docstring already claims, which makes the strong layer present
   by default and the future default flip nearly free.

   *Exit:* a 4-byte `b"GGUF"` file with no parser and no sidecar does not
   report an unqualified success; hash-less Annex IV and pipeline roots do not
   return exit 0 by default while any legacy override still reports
   `verified:false`; an export→tamper→verify round trip exits **6** on a
   flipped byte; the existing 1-vs-6 split is preserved exactly and remains
   **structural** (typed fields, never `reason` prose); the existing per-stage
   `UNVERIFIED` behaviour is asserted unchanged.

6. [ ] **S6 — Invocation-owned resources: temp state, atomic writes, worker lifetime** (M)
   Units: `C33-EXPORT-TEMP-OWNERSHIP` (= `CLI-01` + `GAP-01`), `TRUST-10`,
   `CLI-19`, `CLI-08`.

   Every member is: **a resource whose lifetime belongs to one invocation is
   named or owned globally.**  `export_model` derives
   `<model_path>_merged_for_export` by string concatenation, adopts any
   pre-existing directory at that path with `makedirs(exist_ok=True)`,
   overwrites it with `save_pretrained`, then `rmtree`s it — returning
   `success=True`.  Broader than reported: the `try/finally` opens at
   `export.py:445`, **after** the merge at `:428-433`, so when `_merge_adapter`
   raises there is no cleanup at all and a user directory has already been
   partially clobbered.  Two concurrent exports of the same model collide on
   the same path.

   Fix by one discipline — *allocate uniquely, bind cleanup to the handle
   returned at creation, put the release in `finally`* — and promote the
   atomic-write primitive currently local to `wizard/_state.py:428-450` to a
   shared utility.  Keep the scratch directory in the model's parent, not the
   system tmpdir: a merged 70B fp16 checkpoint would trade a data-loss bug for
   an ENOSPC bug.

   *Exit:* a pre-existing scratch directory containing a sentinel survives
   byte-identical on export **success** and on merge **failure**; two
   sequential exports use different scratch directories;
   `shutil.rmtree(..., ignore_errors=True)` is replaced by a logged `OSError`;
   the 32-concurrent-writer ingest stress pattern from
   `tests/test_pipeline_orchestrator.py:262-316` is ported and produces no
   orphan temp files; a mid-write exception leaves the previous deploy/
   quickstart destination bytes unchanged; a timed-out chat streamer terminates
   its worker within a bounded time.

7. [ ] **S7 — CLI and wizard boundary: policy declared must be policy enforced** (XL, 3 commits)
   Units: `C33-CLI-ERROR-ENVELOPE` (= `CLI-05` + `CLI-11`), `CLI-04`, `CLI-02`,
   `CLI-17`, `CLI-18`, `CLI-09`, `CLI-12`, `CLI-13`.

   All eight are **the CLI boundary declaring something it does not enforce or
   cannot deliver.**  Materially broader than reported: `--offline` is applied
   at `_dispatch.py:243`, *after* `_dispatch_subcommand` at `:225`, so the
   global flag is inert for **every** subcommand — export, deploy, ingest,
   audit, safety-eval, cache-\*, quickstart's parent process — not only `chat`.
   `doctor` is the sole exception, and only because it declares its own
   subparser-level flag.  Meanwhile `docs/usermanuals/en/reference/cli.md:47`
   promises "disable all HF Hub network calls".

   Per **C-11**, `[export]` stops proving capability by `import llama_cpp` and
   starts probing for a real converter; `_doctor.py:107` uses the same false
   proxy and is corrected with it; the terminal `FileNotFoundError` message
   names the actual install step instead of the currently-wrong
   `pip install 'llama-cpp-python…'`.

   Commits: (1) central seam — offline policy before dispatch and `offline`
   threaded into `run_chat`/`load_model`, plus a canonical failure renderer for
   `UnicodeDecodeError`/`OSError`/no-train modes; (2) capability honesty —
   converter probe, quantization/temperature validation shared between parser
   and REPL, `cache-models` local-path branch; (3) wizard — three-state
   keep/replace/disable per collector, `WizardOutcome.valid`, path resolution
   inside the try block.

   *Exit:* every no-train mode emits **exactly one** JSON object on stdout plus
   the documented exit code, with no log text on stdout; invalid-UTF-8 config,
   read-only cache and unwritable export parent each produce one
   `{"success":false,…}` object in **separate subprocess tests**;
   `forgelm --offline chat HUB_ID` reaches no network function under sentinels
   and `local_files_only=True` reaches both `from_pretrained` calls; an invalid
   generated config leaves the file, **preserves** the state snapshot, prints
   no "start later" message and exits 1; explicit-disable answers leave fields
   absent while bare-Enter preserves them, both directions asserted.

8. [ ] **S8 — Run-scoped effective state and provenance** (XL)
   Units: `C31-RUN-OUTCOME` (= `CORE-06` + `CORE-07` + `CORE-12`), `CORE-09`,
   `TRUST-08`, `OPS-22`.

   Six symptoms, one cause: **artifact producers and estimators re-interpret
   the original config instead of consuming this run's observed effective
   state, and what provenance does exist is bound to a module singleton instead
   of the run.**  The model card misses DoRA, PiSSA and rsLoRA in all three of
   its surfaces; the manifest's `training_parameters.dora` is `False` for
   `method: dora`, contradicting the same manifest's own `adapter_method` — an
   internally inconsistent compliance artefact.

   Per **C-6**, keep `model_lineage.quantization` meaning "requested" and add
   `quantization_effective` beside it: repointing an existing key silently
   changes its meaning inside a hash-sealed Annex IV artefact.  Per **C-7**,
   `forgelm --compliance-export` (no training run) writes a `"not_observed"`
   sentinel — `null` is ambiguous with "observed as none" and omission is
   invisible to a reviewer.  Per **C-9**, `training.gradient_checkpointing`
   does **not** become a real field; fit-check's invalid recommendation is
   deleted, since the trainer's unconditional CUDA behaviour is correct.

   Architecture constraint: do **not** add `CARD→MODEL` or `COMPLIANCE→MODEL`
   edges — [`../standards/architecture.md`](../standards/architecture.md) has
   no such edges.  The record travels from `ForgeTrainer` as a parameter.

   *Exit:* a 4×2 matrix (`lora.method` × CUDA available/absent, `load_in_4bit`)
   proves card and manifest name the method and quantization **actually
   applied**, with the requested value retained in its own field; run A→B and
   B→A in one process produce manifests with no cross-contamination; a failed
   `merge_and_unload()` is visible identically in audit log, card and manifest;
   every YAML snippet fit-check emits validates against `ForgeConfig`; a
   directory fingerprint changes on content change and not on traversal order;
   a test **enumerates all loader surfaces** so a new one cannot be added
   silently.

9. [ ] **S9 — Compliance evidence integrity and GDPR erasure completeness** (L)
   Units: `TRUST-02`, `TRUST-13`, `TRUST-14`, `TRUST-01`, `TRUST-07`.

   All five are the compliance surface **claiming a stronger evidentiary state
   than it holds**.  `purge --kind artefacts` cannot find the compliance bundle
   ForgeLM itself writes, because the exporter emits fixed basenames while the
   resolver looks for filenames embedding the run id.  It fails *safe* — it
   deletes nothing, emits `data.erasure_failed` with `NoMatchingArtefacts` and
   exits 1 — so the defect is completeness, not destruction.  Broader than
   reported: `trainer.py:1989` also writes `data_governance_report.json` into
   that directory, and `cli/_pipeline.py:229-230` writes
   `compliance/pipeline_manifest.json`, which belongs to the whole pipeline and
   must **not** be deleted by one run's purge.

   Rewrite `_artefact_targets_for_run` to the allow-list + ownership-proof
   model the design doc already specifies, mirroring the
   `_legacy_staging_owned_by` marker pattern already in that file.

   *Exit:* a **real** bundle produced by `generate_training_manifest()` +
   `export_compliance_artifacts()` is fully removed by a matching run id and
   untouched by a wrong one, while `pipeline_manifest.json` and
   `audit_log.jsonl` survive — a writer→reader integration test, not a
   synthetic-filename fixture, and the fabricated docstring examples at
   `_purge.py:998-1014` are deleted; a malformed line sharing an identifier
   blocks the completion record; `reverse-pii` returns non-zero when
   `AuditLogger` init raises; `OSError` injected separately at the manifest
   `open`, `fsync` and `os.replace` steps never yields silent success; a
   `required → rejected → required` chain is served by **one shared**
   ordered-state helper across the listing and mutation surfaces.

10. [ ] **S10 — Data-layer fidelity, scale and privacy** (L)
    Units: `TRUST-06`, `TRUST-09`, `TRUST-11`, `TRUST-12`, `CORE-08`,
    `CORE-11`, `CORE-13`.

    Two intertwined threads sharing one report schema: **the data layer reports
    a clean or complete result it did not compute** (dropped Presidio rows, a
    no-op script-sanity check, silent decode replacement, a duplicate scan that
    cannot finish), and **a declared data intent is silently not honoured**
    (`multimodal.text_column` is dead; `mix_ratio` does not produce the
    requested blend).  `CORE-13` joins because it is the same layer's privacy
    contract on the same error paths — a synthetic empty-response WARNING logs
    raw prompt content.

    `find_near_duplicates` and `find_near_duplicates_minhash` are in
    `data_audit.__all__`, so the pair-list shape is **public API** and the fix
    must be additive.  The dominant cost is avoidable in closed form: group on
    exact fingerprint before LSH, so k identical rows contribute
    `k(k-1)/2` arithmetically instead of materialising k(k-1)/2 tuples.

    *Exit:* 5,000 identical records still yield exactly 12,497,500 pairs with
    **subquadratic** peak memory under `tracemalloc`, and a property test holds
    `count_… == len(find_…)` across random inputs including the brute-force
    fallback; every-row Presidio failure yields `coverage=0, incomplete=true`;
    `tr`/`TR`/`Tr` normalise byte-identically; a single invalid byte in a long
    file reports `decode_replacements=1`; a `text_column` absent from the
    dataset fails **early**; realized mix ratios and row counts appear in the
    result artefact; a `caplog` test proves known PII tokens appear in **no**
    log record while the warning still identifies the prompt by index and
    length.

11. [ ] **S11 — Deploy/serving generator contract** (M)
    Units: `CLI-10`, `CLI-14`, `CLI-15`, `CLI-16`.

    One subcommand's contract in four layers — what it accepts, what it emits,
    and what it claims to do — bound by a single acceptance mechanism: **golden
    tests comparing the manual's fenced blocks against real
    `generate_deploy_config()` output.**  The manual documents a TGI output of
    `tgi-launcher.sh` + Dockerfile; the generator returns a single
    docker-compose document.  Three auto-detection claims have no
    implementation, and `deploy-targets.md:154` ("override any auto-detection
    with explicit YAML") contradicts the same page's own Configuration section
    27 lines earlier and is un-implementable, since `ForgeConfig` has
    `extra="forbid"` and no `deployment:` block exists.

    *Exit:* every deploy command in the manual runs in a temporary checkout and
    the resulting tree **and** quoted snippets match, EN and TR alike; a
    negative test pins `--output <existing dir>` → `success=False`; absolute
    and relative local paths to HF Endpoints are config errors with no side
    effects; a parametrized accepted/rejected flag matrix per target holds, TGI
    rejects port 65536, and input + output budget ≤ the declared context
    window; the generated compose parses to a non-`latest` image and a
    read-only `/data` mount.

12. [ ] **S12 — Release DAG and release-time evidence** (XL)
    Units: `OPS-01`, `OPS-02`, `OPS-03`, `OPS-04`, `OPS-05`, `OPS-06`,
    `OPS-15`, `OPS-16`, `OPS-19`.

    Every member is a defect **in the release DAG or in the evidence that DAG
    is supposed to produce**, and they share one blast radius: they can only be
    verified by pushing throwaway tags.  Splitting them means two separate tag
    campaigns.

    Broader than reported: **zero lines** of the wheel's Python modules execute
    in any of the twelve matrix cells.  `tests/__init__.py` exists, so under
    pytest's default `prepend` import mode the basedir walk puts the checkout
    root ahead of site-packages; the SBOM step is metadata-only too.  What the
    matrix genuinely does gate — wheel build, metadata validity, `twine check`,
    cross-platform dependency resolution — is real and is kept.

    Per **C-12**, add a `wheel-contract` step run from **outside** the
    checkout, mirroring `nightly.yml`'s existing `wheel-install-smoke`; the
    alternative (making all 124 test modules resolve `forgelm` from
    site-packages) is an L/XL test-architecture rewrite for small marginal
    gain.  Per **C-13**, the release-record check exempts PEP 440
    pre-releases — `CHANGELOG.md` has no rc sections today while `release.md`
    mandates an rc before every minor, so enforcing it would block the flow the
    project actually uses — while tag↔version and ancestry are enforced for
    every tag.  Per **C-14**, a `supply-chain-gate` job runs the same two
    helpers the nightly uses against the **tagged SHA**, because the EN/TR
    manuals already promise operators exactly that.

    *Exit:* deliberately omitting a package-data glob turns the matrix **red**
    at the wheel-contract step while `pytest tests/` stays green; a mismatched,
    off-main or record-less tag cannot reach `build`; a tagged SHA with an
    unsuppressed `pip-audit` finding cannot reach `publish`, and a clean one
    leaves a retained artefact referencing that SHA; every referable BOM object
    has a globally unique `bom-ref` validated against the **official CycloneDX
    schema** plus the semantic checks the schema cannot express; the **exact
    documented command** downloads all SBOMs for a real released tag; a policy
    guard rejects any non-local `uses:` that is not a full 40-character SHA;
    a meta-test compares documentation claims against the workflow trigger
    inventory so no document can claim enforcement CI does not perform.

    **Local verification is impossible for four of these criteria.**  Plan:
    scratch branch, throwaway `v0.0.0-test-*` tags per rejection case, a
    deliberately broken wheel to prove the new step sees what the old one could
    not, then restore, re-tag green, delete every throwaway tag from the remote,
    and confirm no `[X.Y.Z]` heading was left in `CHANGELOG.md`.

13. [ ] **S13 — Packaging metadata, dependency single-source, container distribution** (L)
    Units: `C31-DOCKER-RUNBOOK` (= `DOCS-05` + `OPS-13`), `OPS-11`, `OPS-12`,
    `OPS-14`, `C31-DEPENDENCY-SOURCE` (= `OPS-07` + `OPS-08`), `OPS-17`,
    `OPS-18`.

    All seven are **the distribution surface: what a user installs and what
    they run it in**, and all share one root cause — hand-maintained duplicates
    of a single truth.  Broader than reported: `docker-compose.yaml:39` itself
    mounts `hf_cache:/root/.cache/huggingface` while the image drops to
    `USER forgelm`, so the cache never persists — an **artifact defect**, not a
    documentation defect.  The manual advertises three image variants
    (`latest`, `slim`, `airgap ~30 GB with pre-cached weights`) that nothing
    builds, and every documented `docker run … forgelm --config …` resolves to
    `forgelm forgelm …` against the shipped `ENTRYPOINT`.

    Per **C-15**, local `docker build` is the permanent distribution contract;
    the three phantom variants are deleted and both manuals are re-derived from
    the artifacts on disk.  A published image is tracked as a **condition**,
    not a version — a publish pipeline is new release surface (registry auth,
    `packages: write`, immutable digest policy, provenance + SBOM that
    `release.md` does not currently cover) and the `airgap` variant raises
    separate model-weight redistribution questions.  Per **C-16**, the compose
    cache path is fixed in the same change, with a `### Fixed` entry naming the
    orphaned-`hf_cache`-volume effect explicitly.

    **Prerequisite:** no standard governs Docker, and none governs SBOM as
    rules — see "Standards to be written" below.  There is currently nothing to
    review this work against.

    *Exit:* README and both manuals describe **exactly one** distribution path;
    a guard fails when a first-party registry reference has no backing publish
    workflow, when a compose filename in prose is absent from disk, or when a
    documented service name is absent from `services:`; the documented first
    container command succeeds in a `workflow_dispatch` smoke job; two
    disposable `docker compose run --rm` invocations share one cached snapshot
    without root; the documented wizard command leaves a validated YAML on the
    **host** after `--rm` that passes `python3 -m forgelm --config <file> --dry-run`;
    a meta-test compares normalised direct dependencies between the nightly
    minimum-deps list and package metadata; a clean isolated build emits **no**
    packaging-metadata deprecation warning and `twine check dist/*` passes.

14. [ ] **S14 — Test-quality ratchet** (M)
    Units: `OPS-20`, `OPS-21`.

    Both are "the test suite's own quality contract is weaker than its actual
    quality" — a 40 % floor guarding a measured 86.60 %, and a curated subset of
    tests that assert only "did not raise" and would pass against a `pass`
    body.

    Deliberately late (**C-19**): ratcheting at position 1 would gate every
    remediation PR on a floor calibrated against pre-remediation code, and
    roughly half the no-assert triage list sits in files S2-S11 rewrite anyway.
    Diff-coverage lands here too, **enforcing** — the plan's original
    "advisory at S1" step was withdrawn in S1 because a CI step that cannot go
    red is the fake green principle 6 outlaws.

    *Exit:* diff-coverage on changed lines **and** a ratcheted global
    `fail_under` both enforced, with explicit measured thresholds for
    `compliance.py`, `verify/`, `_http.py`, `safety/` and `config.py`; replacing
    the body of each curated production function with a no-op makes its test
    **fail**; the 71-function inventory is triaged manually into legitimate
    no-exception contracts versus silent-pass risks with the disposition
    recorded; `testing.md`'s stated floor matches the enforced one.

15. [ ] **S15 — Documentation: claims bound to code** (XL, 4 commits)
    Units: `DOCS-01`, `DOCS-02`, `DOCS-04`, `DOCS-03`,
    `C31-EXIT-CODE-PROSE` (= `DOCS-07` + `OPS-24`),
    `C31-DOC-SCHEMA-PROSE` (= `DOCS-08` + `DOCS-09`), `DOCS-14`, `DOCS-15`,
    `DOCS-16`, `DOCS-06` (residual wording only).

    Every member is **a published claim the code does not back, plus the guard
    that would have caught it**, and they share one hard constraint: EN, TR and
    all six site locales change in the same commit.

    Regulation (EU) 2026/1744 moves Annex III high-risk obligations to
    **2 December 2027** and Annex I to **2 August 2028**; five current surfaces
    still tie "full enforcement" to 2 August 2026, and the original wording
    additionally conflated the two obligation groups.  Per the GTM decision,
    `product_strategy.md:139`'s time-sensitive-moat framing is **retargeted**
    to the new date rather than rewritten.

    The auto-revert "rollback" myth has **eight** surviving instances across
    nine files: the prior sweep was phrase-scoped to "restores a previous
    checkpoint" and blind to "rolls back to the baseline model", "baseline-flip"
    and "flips to baseline" — one claim under four wordings, two of them in
    formal ISO A.5.29 control rows.  `_revert_model` deletes; it restores
    nothing.

    `docs/roadmap/risks-and-decisions.md:176` is **not** a target: it sits under
    the append-only Decision Log.  The live risk rows are `:11`, `:14`, `:42`.

    Commits: (1) legal dates + freshness guard; (2) auto-revert semantics sweep
    + semantic guard; (3) site runtime claims across six locales + static
    fallbacks + `site/index.html:575` + privacy/localStorage + a11y + assets;
    (4) exit-code prose + ghost config keys + staging path + guard extensions.

    *Exit:* no current surface ties high-risk obligations to August 2026, and a
    guard fails on reintroduction; **no locale** claims unconditional exit 3 or
    a generated conformity declaration, verified in a rendered-DOM-equivalent
    test for EN plus at least one non-EN locale; a grep for restore claims over
    current surfaces returns **zero**, with the canonical `auto-revert.md` page
    exempt because it must quote the wrong claim to refute it; a localStorage
    snapshot after every wizard step contains **no** credential-class value;
    every current surface states `verify-audit` as **0/1/2/6** with the
    "1 = unverified, 6 = compared and failed" distinction, and a meta-test scans
    the canonical exit-code statements in all four rulebooks against
    `_PUBLIC_EXIT_CODES`; an **inline-code** config key in prose that does not
    resolve against `ForgeConfig` fails the guard; `aria-expanded` tracks drawer
    state across all four flows; every asset referenced from `site/*.html`
    resolves in the deploy source tree.

    **Rollout:** extend `check_cli_exit_code_prose.py` non-strict first, triage
    the baseline, then flip — the documented `check_anchor_resolution.py`
    pattern.

16. [ ] **S16 — Documentation source-of-truth and localization governance** (M)
    Units: `C31-LOCALIZATION-GOV` (= `DOCS-10` + `DOCS-18`), `DOCS-11`,
    `DOCS-12`, `DOCS-13`, `DOCS-17`.

    Every member is **a hand-maintained restatement of a fact that has a
    canonical source** — the release version, the locale key count, the wheel
    version, the config schema, the docs taxonomy — and every fix is the same
    move: derive or register, then ratchet.  `DOCS-12`'s roadmap current-state
    lines and `DOCS-11`'s strategy restatements are the same literal duplicated
    across files, closed by the same extended `check_release_record_sync.py`.

    Absolutely last: it ratchets parity guards to `--strict` and adds
    table/list-row parity, and running that ratchet before S15's large
    bilingual edits would force those edits to satisfy a moving target.

    *Exit:* `localization.md`'s locale-status section matches
    `check_site_chrome_parity.py --strict` output and CI runs that guard **with**
    `--strict`; `check_bilingual_parity.py --strict` covers
    `docs/product_strategy.md` and fails when a mirror drops a table row or list
    item inside a matched section; `docs/roadmap.md` and its TR mirror name
    **exactly one** latest release in headline, current-state sentence, status
    table, mermaid graph and documentation map; every notebook version literal
    derives from one constant and the pin guard fails when a downloaded artifact
    tag differs from the wheel pin; `docs/README.md` exists, matches
    `documentation.md:11-26`, is reachable from the `pyproject.toml`
    Documentation URL, and labels canonical versus archival surfaces.

## Coverage cross-check

| Step | Canonical units | Count |
|---|---|---:|
| S1 | `OPS-09`, `OPS-10`, `OPS-23` | 3 |
| S2 | `C31-NUMERIC-CONFIG`, `C33-TRAIN-JSON-CONTRACT`, `CORE-03`, `CORE-10`, `CORE-14`, `GAP-02`, `TRUST-15` | 7 |
| S3 | `TRUST-03`, `CORE-04`, `C33-CACHE-TASK-VERDICT` | 3 |
| S4 | `GAP-03`, `CORE-02` | 2 |
| S5 | `TRUST-04`, `TRUST-05`, `CLI-03` | 3 |
| S6 | `C33-EXPORT-TEMP-OWNERSHIP`, `TRUST-10`, `CLI-19`, `CLI-08` | 4 |
| S7 | `C33-CLI-ERROR-ENVELOPE`, `CLI-02`, `CLI-04`, `CLI-09`, `CLI-12`, `CLI-13`, `CLI-17`, `CLI-18` | 8 |
| S8 | `C31-RUN-OUTCOME`, `CORE-09`, `TRUST-08`, `OPS-22` | 4 |
| S9 | `TRUST-01`, `TRUST-02`, `TRUST-07`, `TRUST-13`, `TRUST-14` | 5 |
| S10 | `TRUST-06`, `TRUST-09`, `TRUST-11`, `TRUST-12`, `CORE-08`, `CORE-11`, `CORE-13` | 7 |
| S11 | `CLI-10`, `CLI-14`, `CLI-15`, `CLI-16` | 4 |
| S12 | `OPS-01`, `OPS-02`, `OPS-03`, `OPS-04`, `OPS-05`, `OPS-06`, `OPS-15`, `OPS-16`, `OPS-19` | 9 |
| S13 | `C31-DOCKER-RUNBOOK`, `C31-DEPENDENCY-SOURCE`, `OPS-11`, `OPS-12`, `OPS-14`, `OPS-17`, `OPS-18` | 7 |
| S14 | `OPS-20`, `OPS-21` | 2 |
| S15 | `DOCS-01`, `DOCS-02`, `DOCS-03`, `DOCS-04`, `DOCS-06`, `DOCS-14`, `DOCS-15`, `DOCS-16`, `C31-EXIT-CODE-PROSE`, `C31-DOC-SCHEMA-PROSE` | 10 |
| S16 | `C31-LOCALIZATION-GOV`, `DOCS-11`, `DOCS-12`, `DOCS-13`, `DOCS-17` | 5 |
| | **Total** | **83** |

All eleven clusters and seventy-two singletons are accounted for.  No unit
appears twice; none is dropped.

## Refuted, narrowed, and already correct

Recorded so that nothing is silently discarded and so that no step
re-implements behaviour that already works.

**Claims withdrawn — do not implement:**

- `DOCS-06`'s main claim ("the same `data_audit_report.json` doubles as a
  Croissant card and an Article 10 artifact" is a wrong-artifact model) is
  **refuted**.  `AuditReport` genuinely carries split stats, PII, secrets,
  duplicate/leakage, language and quality findings, and Article 10 mandates no
  ForgeLM filename.  Only an absolute wording residual survives, in S15.
- `DOCS-02`'s "every audit report becomes an Article 10 evidence document"
  sub-claim is **withdrawn**; the finding stays on its other two claims.
- `DOCS-08`'s "the operator assumes an HMAC authenticity control" is
  **withdrawn** — the guide never promises body-signature verification.  The
  ghost `webhook.secret_env` field is the real defect.
- `DOCS-01`'s pointer at `risks-and-decisions.md:176` is **refuted** — that
  line is append-only Decision Log history.

**Recommended remedies proven unavailable:**

- `GAP-03`'s "pin an upper bound below the first incompatible TRL release"
  **cannot be done**: no version in the declared range exports the symbols,
  including the floor.  Hence **C-10**.
- `OPS-01`'s "copy `tests/` plus fixtures into a scratch directory" is
  **infeasible**: `conftest.py` and five modules import `tests._helpers`, which
  requires the repo root on `sys.path` — i.e. requires the very
  `tests/__init__.py` that shadows site-packages — and about ten guard tests
  read the `forgelm/` and `tools/` source trees off disk.  Hence **C-12**.

**Behaviour that is already correct and must be asserted, not rebuilt:**
`eval_loss` NaN handling and `-inf` threshold direction (S2); the empty-metric
benchmark path and `min_score` bounds (S2); missing judge file, all-invalid
score set, HTTP 401/403 and SSRF failures, and non-finite judge score routing
(S3); the model runtime's correct honouring of `lora.method` (S8); the
purge resolver's fail-safe `NoMatchingArtefacts` behaviour (S9); the durable
fsync of the main Art. 12 event before the sidecar attempt (S9); hash-less
per-stage evidence already raising `UNVERIFIED` (S5); bandit's B615 findings
being mostly false positives on revision-pinned loaders (S8); and the 61
existing `EXIT_INTEGRITY_FAILURE` assertions that already satisfy `DOCS-07`'s
third acceptance item (S15 lands the guard, not new coverage).

## Standards to be written

Two rule gaps must be closed **before their step**, not before Step 1.  Both
are cases where the artifact ships and no document governs it, so there is
nothing for a reviewer to review against.

- [ ] **Docker standard** (blocks S13).  `Dockerfile`, `docker-compose.yaml`,
  `.dockerignore` and the EN/TR operations manual all ship; the only rule
  anywhere is a post-release checkbox at `release.md:182`, and there are zero
  Docker references in `.github/workflows/`.  Needs: base-image digest-pinning
  policy, runtime-versus-devel stage rule, non-root user with an owned cache
  path, service naming, and a CI smoke obligation.
- [ ] **SBOM / supply-chain standard** (blocks S12).  SBOM appears only as
  descriptive prose and one command.  Needs: CycloneDX version, component
  completeness (root exclusion, `bom-ref` uniqueness, license coverage,
  dependency graph), retention channel and window, and the meaning of each
  `check_pip_audit.py` severity tier.

Optional and low priority, foldable into S16's `docs/README.md` work: a
statement of what `site/` is and what a claim on it must be backed by, and an
index for `docs/usermanuals/` tying together its link discipline, bilingual
duty, schema-drift gate, build step, own workflow, and the fact that
`json-output.md` is a MAJOR-locked contract.

## Requirements

- **Every step is one root-cause family**, committed on its own, followed by an
  Opus review round and then a Sonnet review round, each with verified findings
  fixed and committed before the next step begins.
- **No fixture mass-regeneration.**  S8 changes manifest content; each
  regenerated golden fixture needs a named reason.
- **At least seven existing tests pin the defect** and will go red as *correct*
  signals: `test_judge_functions.py:673-684`, `test_trainer.py:1013-1056`,
  `test_library_api.py:490-506,508-558`,
  `test_verification_toolbelt.py:286-337,2281-2299`,
  `test_gdpr_erasure.py:231-249`, `test_deploy.py:191-194`,
  `test_phase12_5.py:734-772`, `test_trainer_max_length.py:41-65`.  Each is
  rewritten in the same PR as its fix, with the old expectation named in the
  commit body — otherwise a reviewer reads a red test as a broken change.
- **Bilingual atomicity.**  Every EN edit ships with its TR mirror in the same
  commit, and TR in-prose links target `-tr.md` mirrors.  Machine-translated
  output is forbidden; the documented path when Turkish cannot be written is a
  `<!-- TR translation pending -->` marker plus a tracked issue, never English
  pasted into the TR file.
- **Audit-catalog atomicity.**  `check_audit_event_catalog.py --strict` is
  bidirectional; a new or renamed event needs its EN and TR catalog rows in the
  same PR.
- **Module budgets.**  Any step growing a deferred module carries its
  `budget_history` justification in the same diff.
- **Guard widening is a two-phase rollout.**  Five steps widen an existing
  guard's scope and one flips a guard to `--strict`.  Run non-strict, triage the
  baseline into fix-now versus a dated deferral row, then flip.
- **ID hygiene.**  Finding IDs appear in commit bodies and, where warranted, in
  the CHANGELOG.  Working-memory directory paths never appear in source, public
  docs, CHANGELOG, commit messages or PR descriptions
  (`tools/check_no_analysis_refs.py`).

## Validation gate

Per step, under `./.venv/bin/python`:

```bash
python3 tools/check_import_origin.py --strict && \
  ruff format . && ruff check . && pytest tests/ && \
  python3 -m forgelm --config config_template.yaml --dry-run && \
  <the remaining guards listed in CLAUDE.md "Verify before opening PR">
```

Plus, per step, its own acceptance criteria above, and:

- a reproducer that is **red before the fix and green after**;
- for `S12`, the throwaway-tag campaign described in that step, since four of
  its criteria are observable only in a workflow run;
- for `S13`, a `workflow_dispatch` container smoke job — the unit suite's
  autouse network guard and the CMake requirement put this out of reach of
  `pytest`;
- for `S4`, two real dependency resolutions at the declared minimum and current
  endpoints, with the zero-skip assertion enforced in CI or it will silently
  re-soften.

## Delivery

Per **C-20** no step cuts a release.  Every PR files under `[Unreleased]` in
[`CHANGELOG.md`](../../CHANGELOG.md); a single `chore: release` commit follows
the phase once all sixteen steps and their review rounds are complete, on the
open `0.11.1rc1` cycle.

**The tag is derived at that commit, not before.**  Run the `cut-release`
checklist against [`../standards/release.md`](../standards/release.md#versioning)'s
bump table with the accumulated `[Unreleased]` sections in hand.  As planned,
the `### Breaking` entries below make PATCH unavailable — the table's
"changing an exit code's meaning" row is a direct hit, and the deprecation
cadence forbids a patch release from removing anything — so the expected
outcome is a MINOR bump.  A PATCH tag is reachable only by moving every
breaking item out of the phase into the deferral cohort, which is a scope
decision, not a numbering one.

Steps carrying `### Breaking` entries: S2 (configs that load today now exit 1;
`run_benchmark` verdict change; merge config-error routing moves exit 2 → 1),
S3 (gate verdict flips; `cache-tasks` exit 0 → 2 with a rewritten JSON
contract), S9 (`AuditLogger.log_event` fail-closed; `reverse-pii` and purge
move to non-zero on states that returned 0).  Steps carrying `### Changed`:
S5, S7, S11, S12, S13.  `### Security` is expected in S12.

Per-1.0 the release standard already treats every minor as potentially
breaking; the operative obligation per step is therefore **which CHANGELOG
section and which named caller**, recorded in the step's PR description.

## Cross-references

- [roadmap.md](../roadmap.md) — cross-phase summary
- [risks-and-decisions.md](risks-and-decisions.md) — the twenty decision
  records, the deferral cohort this phase opens, and the module-size budget
  policy
- [`../standards/README.md`](../standards/README.md) — the standards index this
  phase's pre-work corrects
- [completed-phases.md](completed-phases.md) — Phase 15 and earlier
- [phase-14-5-pipeline-hardening.md](phase-14-5-pipeline-hardening.md) — the
  open `SUPPORTED_EVENTS` / webhook-vocabulary follow-up, and the unkeyed
  pipeline-manifest-hash limitation this phase does not close
