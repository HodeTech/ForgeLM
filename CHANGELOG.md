# Changelog

All notable changes to ForgeLM are documented here.

## [Unreleased]

_(v0.9.1 dev cycle — entries land here as PRs merge.)_

### Added

- **`forgelm safety-eval --max-safety-regression RATIO` — the gate's threshold
  is now visible and settable.** Float in `[0.0, 1.0]`, default `0.05`,
  unchanged from the previous hard-wired behaviour; omitting the flag is
  byte-identical to before. This adds **no gate**. The unsafe-ratio gate always
  ran, but the CLI never passed a value, so every standalone run was gated at
  `run_safety_evaluation`'s signature default — a number absent from `--help`,
  from the text output and from the JSON envelope. An operator branching CI on
  exit `3` was branching on a threshold they could not read. Despite the name
  it is an **absolute** ceiling, not a baseline-relative bound: nothing measures
  pre-training safety anywhere. The comparison is strictly greater-than and only
  fires when at least one unsafe response was recorded, so
  `--max-safety-regression 0.0` still passes a clean run. Out-of-range,
  non-numeric and `nan` values are argparse usage errors and exit `2`.
  **Not added, and not planned:** `--config` and `--classifier-revision`. Nine
  `evaluation.safety.*` YAML fields and two of the three gates
  (`min_safety_score`, `severity_thresholds`) remain unreachable from this
  subcommand, and its classifier load stays unpinned.
- **New public constant `forgelm.safety.DEFAULT_MAX_SAFETY_REGRESSION`** (`0.05`),
  exported in `forgelm.safety.__all__`. Single source for both the CLI flag
  default and `run_safety_evaluation`'s signature default so the two cannot
  drift. Deliberately **not** a `SafetyEvalThresholds` field — the orchestrator
  takes it as its own parameter and the training path still sources it from
  `evaluation.safety.max_safety_regression`.
- **`forgelm safety-eval` output echoes the threshold it gated on.** The JSON
  envelope gains one key, `max_safety_regression` (**additive**; no key was
  renamed or removed — a rename would be MAJOR). The text output gains one line
  after `safe_ratio`. A consumer reading `passed: false` beside `safe_ratio`
  previously had no way to see which ceiling the ratio was compared against.
- **Hub revision pinning — five optional config fields, all five wired.**
  `model.revision`, `synthetic.teacher_revision`,
  `evaluation.safety.classifier_revision`,
  `evaluation.llm_judge.judge_model_revision` and
  `training.grpo_reward_model_revision` each accept a 40-hex HF Hub commit SHA
  (either case) or a branch/tag/ref, stored verbatim — ForgeLM never normalises
  or case-folds the value. All are `Optional[str] = None`, all opt-in; omitting
  them changes nothing.
  **Honoured in this release:** `model.revision` pins every load of the base
  repo at the *same* commit — `AutoTokenizer`, the `AutoProcessor` VLM path,
  `AutoModelForCausalLM` / `AutoModelForImageTextToText`, and the `AutoConfig`
  probe behind `--fit-check` — so the tokenizer can never drift from the weights
  it is recorded beside. `synthetic.teacher_revision` pins the local teacher's
  tokenizer and weights under `teacher_backend: local`.
  `evaluation.llm_judge.judge_model_revision` pins the **local** judge's
  tokenizer and weights (an API judge is loaded by the provider, not the Hub —
  the schema rejects the field alongside `judge_api_key_env`), and
  `training.grpo_reward_model_revision` pins the GRPO reward tokenizer and
  sequence-classification model (rejected without `grpo_reward_model`). For
  every honoured field the value is resolved to a commit SHA first and that
  exact SHA is passed as `revision=` to **every** `from_pretrained` for the
  repo, so tokenizer and model can never come from different commits. When no
  SHA can be confirmed — offline, no `huggingface_hub`, an unreachable or gated
  repo — the operator's literal is still passed verbatim, so the pin is never
  silently dropped, and a `WARNING` says no SHA was verified. Leaving a field
  unset is unchanged behaviour and warns that the load is unpinned.
  These two are not cosmetic: the reward model **is** the objective GRPO
  optimises against, so an unpinned upstream re-tune changes what the run was
  trained to do; the judge's score feeds the auto-revert `min_score` gate, so an
  unpinned judge lets two runs of identical YAML promote and block the same
  model.
  `evaluation.safety.classifier_revision` also pins the harm classifier behind
  the auto-revert gate. **Scope limit:** the training-time safety gate only.
  Standalone `forgelm safety-eval` takes no `--config` and has no
  `--classifier-revision` flag, so its classifier load is unpinned and logs an
  UNPINNED warning naming the repo; a verdict from that subcommand is not
  pinned evidence.
  **Three of these five shipped dead earlier in this same dev cycle.**
  `judge_model_revision`, `grpo_reward_model_revision` and
  `classifier_revision` were each accepted, validated, cross-field-checked and
  documented while reaching no loader at all. An operator who set one believed
  their judge scores, reward model or safety verdicts were reproducible when
  they were not — the same confident falsehood this work exists to remove, in a
  new place. The first two were wired in a follow-up commit; `classifier_revision`
  was missed a second time because its load site *did* pass `revision=` while
  neither caller — the training path nor the CLI path — passed the field down to
  it. Verifying a load site is not verifying the caller chain.
  Rejected at validation (exit `1`, fires under `--dry-run`, no network): an
  empty / whitespace-bearing / control-character / leading-dash / >255-char
  literal; `judge_model_revision` with `judge_api_key_env`; `teacher_revision`
  with `teacher_backend` of `api` or `file`; `grpo_reward_model_revision`
  without `grpo_reward_model`. Warned but accepted: a non-40-hex ref (a branch
  or tag is accepted and **does not pin** — upstream can repoint it), and
  `model.revision` set against an existing local directory (warned rather than
  raised, because the check's verdict would otherwise depend on whether that
  directory exists on the validating machine). `config_template.yaml` ships all
  five commented out, so no live SHA rots and the CI dry-run stays independent
  of Hub state (`forgelm/config.py`, `forgelm/model.py`, `forgelm/fit_check.py`,
  `forgelm/synthetic.py`, `forgelm/safety.py`).
- **Base-model provenance in the Annex IV bundle.** `compliance_report.json`'s
  `model_lineage` block gains `base_model_revision` — `repo_id`,
  `revision_requested` (the operator's literal, so a symbolic `main` shows
  plainly as a moving ref), `revision_resolved` (a confirmed 40-hex SHA or
  `null`), `resolution_source` (`local_path` / `resolved` / `pinned_resolved` /
  `cache` / `pinned_unverified` / `unresolved`), `revision_pinned` (the exact
  string handed to `revision=`), and `reason` (present only when no base-model
  load happened at all — the `forgelm compliance-only` case). **A non-null
  `revision_resolved` always means the load in that process was pinned to it:**
  manifest generation performs no Hub lookup of its own, provenance is written
  only after the load returns, and a load that raises leaves no claim behind.
  Existing `model_lineage` keys are unchanged and the addition is additive. Note
  that the flattened `training_manifest.yaml` sidecar carries no `model_lineage`
  block at all — it is an operator summary; read `compliance_report.json` for
  provenance (`forgelm/compliance.py`).
- **Every pinned role reaches the Annex IV bundle —
  `model_lineage.component_revisions`.** A **list** sibling to
  `base_model_revision` (which is unchanged and still present), one entry per
  completed pinned load in the process, sorted by `(role, repo_id)` so the
  artefact is byte-stable across runs that load the same models in a different
  order. Each entry carries `role`, `repo_id`, `revision_requested`,
  `revision_resolved` (a confirmed 40-hex SHA or `null` — never the requested
  string echoed back), `resolution_source` and `revision_pinned` (the exact
  string handed to `revision=`, which may be a moving ref). Six role names are
  artefact contract and never change: `base_model`, `safety_classifier`,
  `llm_judge`, `grpo_reward_model`, `teacher_model`, `fit_check`. Until this
  block existed, four roles resolved a revision, pinned their load to it,
  recorded it — and had it dropped on the floor, because the registry's only
  reader asked for the base model; that is what made the per-load warning's "the
  manifest will record …" promise false for four of the six.
  **Two readings the artefact does not support:** `component_revisions: []`
  means no pinned load completed in this process (`forgelm compliance-only`, an
  all-local-path config, a manifest written before any load) and is *not* a
  statement that no pins were configured; a null `revision_resolved` means no
  SHA could be confirmed, not that the run was unpinned — `revision_pinned`
  records the ref verbatim.
  `fit_check` is reserved but never emitted: `model.revision` *is* forwarded to
  the VRAM-estimate `AutoConfig` probe, so that load is pinned, but the probe
  registers no provenance.
  Purely additive: `forgelm verify-annex-iv` gates on top-level Annex IV
  sections and does not inspect `model_lineage`, so artefacts written before
  this release remain valid and artefacts written after verify identically. A
  newly generated artefact naturally carries a different `manifest_hash` than a
  pre-change build of the same run would have produced; archived artefacts keep
  their own self-consistent hash (`forgelm/compliance.py`, `forgelm/model.py`).
- **`forgelm.compliance.resolve_model_revision(repo_id, *, requested=None,
  offline=False)`** — returns `{"repo_id", "revision_requested",
  "revision_resolved", "resolution_source"}` for a model repo. Never raises.
  `revision_resolved` is a validated 40-character commit SHA or `None`; a
  requested pin is never echoed into it, so a value there always means something
  confirmed it. With `offline=True` it short-circuits before any Hub client is
  imported and consults only the commit-addressed local cache. **Callers must
  pass `revision_resolved` into the load itself** — a SHA obtained here and then
  not used to pin the load must not be recorded as provenance
  (`forgelm/compliance.py`).
- **Per-load revision logging.** One line per pinnable load: `INFO` when a
  commit was confirmed (role, repo, SHA); `WARNING` when a pin was configured
  but no SHA could be confirmed, stating that the load will use the requested
  ref as-is and naming its destination precisely — "this role's entry under
  `model_lineage.component_revisions` will record that no SHA was verified
  rather than assert one", replacing an earlier claim about "the Annex IV
  manifest" that was true only for the base model; `WARNING` when a Hub repo
  loads **unpinned**, naming the
  repo and the config field that would pin it. Local paths are never warned
  about — a directory has no Hub commit, and warning on it would train operators
  to ignore the warning that matters (`forgelm/model.py`).
- **The generated model card reproduces the pin.** When the base-model SHA is
  known the usage snippet emits
  `AutoModelForCausalLM.from_pretrained("org/model", revision="<sha>")`; when it
  is not, the kwarg is omitted entirely — never `revision="None"`. A card shipped
  inside an Annex IV bundle that tells a downstream reader to load an unpinned
  repo undercuts the reproducibility claim the surrounding bundle makes
  (`forgelm/model_card.py`).
- **`python -m forgelm`** (`forgelm/__main__.py`) — behaviour and exit codes
  byte-identical to the `forgelm` console script. The contributor gauntlet now
  uses this form, and it is not a stylistic preference: a console script's
  `sys.path[0]` is its own `bin/` directory, so `forgelm --config … --dry-run`
  validates whatever is installed in site-packages rather than the working
  tree. A stale non-editable install made that step report success against a
  package weeks older than the checkout.
- **`tools/check_cli_exit_code_prose.py`** — fails when a `--help` string in
  `forgelm/cli/_parser.py` claims an exit code the CLI does not actually
  return. `--help` is the most authoritative place an operator reads the
  contract, and it was the one surface the `EXIT_INTEGRITY_FAILURE` change
  missed: the parser still told operators that tampering exits `1`, and one
  line still described the constant as deferred to a version that had already
  shipped it. The existing CLI-help guard could not catch this because it
  validates invocation *syntax*, not exit-code *prose*.
- **`tools/check_import_origin.py`** — fails when `import forgelm` resolves
  outside the checkout, catching a stale or shadowing install before it can
  produce a false-green gauntlet run. It leads the gauntlet rather than
  joining the end of it: several other guards import `forgelm` themselves and
  run with `sys.path[0]` set to `tools/`, so they inherited the same blind
  spot — one of them validates documentation examples against the schema and
  would have reported OK while reading a stale one.
- **`forgelm doctor` now reports `forgelm.install`** — version, resolved
  package directory, and whether it sits inside site-packages — as the first
  row, so a pasted bug report identifies which code actually ran.
- **`EXIT_INTEGRITY_FAILURE = 6`** — a new public CLI exit code for the four
  `verify-*` subcommands (`verify-audit`, `verify-annex-iv`, `verify-gguf`,
  `verify-integrity`). Previously a tampered artifact and a mistyped path both
  exited `1`, so a CI pipeline could not tell an operator error from a security
  event. The line: `6` means the verifier read the target artefact and it
  failed its integrity check — a broken audit-log hash chain, an Annex IV
  manifest hash mismatch, a GGUF metadata/SHA-256 sidecar mismatch, or model
  files that no longer match `model_integrity.json`; `1` still means the
  verifier never got far enough to compare anything (missing path, malformed
  input, unreadable artefact). Four deliberate exceptions stay on `1` even
  though they look tamper-adjacent: a `verify-gguf` magic-header mismatch (the
  file isn't a GGUF at all — a file-type verdict, not a tamper verdict); a
  `verify-integrity` manifest entry whose path escapes the model directory
  (the verifier refuses to hash an out-of-tree path before reading anything,
  so nothing was compared); a `verify-gguf` metadata-parse failure on a file
  whose SHA-256 sidecar matches (the bytes are provably what was exported, so
  the parse error is a library-version problem, not tampering); and a
  `verify-audit` zero-entry log with no genesis manifest (it must fail, but
  with no baseline in existence the verifier cannot tell a wiped log from a
  mistyped path). The full rationale for each lives in
  [`docs/standards/error-handling.md`](docs/standards/error-handling.md)
  (`forgelm/cli/_exit_codes.py`, `forgelm/verify.py`).
- **Generation-based Llama-Guard safety scoring — the default classifier now
  works out of the box.** The shipped default `meta-llama/Llama-Guard-3-8B` is a
  generative checkpoint that cannot be scored through the `text-classification`
  pipeline; ForgeLM now loads it as a causal LM, moderates each prompt/response
  pair via the Llama-Guard chat template, and parses the `safe` / `unsafe\nS<n>`
  verdict (fail-closed on malformed output) into the same safety report. A new
  `evaluation.safety.classifier_mode` field (`auto` | `classification` |
  `generation`, default `auto`) selects the path — `auto` routes a known
  generative Llama-Guard checkpoint to generation scoring and everything else to
  the classification pipeline. The prior fail-fast now fires only for the genuine
  misconfiguration (`classification` mode + a generative checkpoint)
  (`forgelm/safety.py`, `forgelm/config.py`).
- **`ingest --input-encoding`** to read source documents in a non-UTF-8 legacy
  encoding, and **`verify-audit … --output-format json`** now works when the flag
  follows the subcommand (matching the other `verify-*` commands).
- **A CI guard (`check_usermanual_schema_drift.py`) that validates every fenced
  YAML key in `docs/usermanuals/` against the real `ForgeConfig` schema**, so a
  user-manual example that uses a nonexistent field is caught in CI instead of
  by a reader's `--dry-run` failure. The widened `check_bilingual_code_blocks`
  guard now also covers the user manuals.
- **A CI guard (`check_release_record_sync.py`) that enforces the post-release
  bookkeeping step.** Every released version in `CHANGELOG.md` must have a
  matching section in `docs/roadmap/releases.md` — one with an actual body, since
  a bare heading is not a record and a `(Planned)` section never counts as one —
  and the `**Released:**` headline in `docs/roadmap.md` **and** the
  `**Yayınlandı:**` headline in its Turkish mirror must both name the newest one.
  Release dates are cross-checked between the two files, and headings inside
  fenced code blocks or HTML comments are ignored so a sample snippet cannot
  satisfy a real release. The guard is fail-closed by construction: a heading
  that was plainly meant to be a release but does not parse (a missing space
  after `##`, an indented or wrong-depth heading, a unicode-dash date, a
  heading swallowed by an unbalanced fence) is reported by file and line
  rather than silently dropped, and parsing zero releases out of a CHANGELOG
  is treated as a broken invocation rather than a clean tree — because a guard
  that reports success on input it could not read is the failure it exists to
  prevent. The release ritual's "update the roadmap" step had been skipped for
  two consecutive releases — `v0.8.0` and `v0.9.0` shipped while the roadmap
  still announced `v0.7.0` as current, and the Turkish mirror had drifted four
  minors behind — so it is now checked rather than trusted. The step also moved
  *ahead* of the tag in `docs/standards/release.md` and the `cut-release` skill:
  written after the tag, the record could only redden the guard on the next PR,
  by which time PyPI had already shipped with a stale roadmap.
- **A CI guard (`check_skill_mirror_parity.py`) that keeps `.claude/skills/` and
  `.agents/skills/` from drifting apart.** Every skill is shipped twice — once
  for Claude Code, once for the agent-agnostic tree — and each edit has to be
  applied to both copies by hand. The guard compares them after normalising the
  three legitimate agent-specific spellings (`.claude/`↔`.agents/`,
  `CLAUDE.md`↔`AGENTS.md`, `Claude`↔`Codex`) and fails on any other difference,
  on a skill or file present on only one side, or on a missing `SKILL.md`.
- **A CI guard (`check_deprecation_targets.py`) that keeps every deprecation
  removal promise honest.** `forgelm/config.py` now carries a single
  `DEPRECATION_REMOVAL_VERSION` constant that every runtime deprecation message
  is built from, and the guard fails the build when any claim across `forgelm/`,
  `config_template.yaml`, `docs/**` or `tests/**` names a different version —
  or when the promised version is no longer ahead of the shipping version (i.e.
  the promise has gone retroactively false). This is the drift class that let
  the same removal date rot twice (`v0.9.0` → `v0.10.0` → `v1.0.0`); it is now
  mechanically impossible to reintroduce silently.

- **New CI guard: `tools/check_source_path_refs.py` — prose may not name a
  source file that does not exist.** The `forgelm/safety.py` →
  `forgelm/safety/` split moved the file cleanly (an AST symbol diff and a
  4,100-input differential fuzz proved the runtime behaviour identical) and
  shipped **39 dangling `forgelm/safety.py` references across 16 files** in the
  very commit whose purpose was moving that file. Nothing noticed, because
  nothing could see them: `check_anchor_resolution.py` validates
  `[text](href)` links under `docs/` only, while the dead references lived in
  backticked inline paths, `.claude/skills/` and `.agents/skills/` checklists,
  `site/*.html` copy, notebook JSON, and `CLAUDE.md`'s repository-structure
  tree. The guard resolves every `forgelm/`, `tools/` and `tests/` path named
  in prose against the filesystem, across `docs/`, `site/`, `notebooks/`, both
  skill trees, `CLAUDE.md`, `AGENTS.md`, `CONTRIBUTING.md` and `README.md`.
  Scope was chosen by measurement, not taste: matching every top-level
  directory produced 266 findings on a clean tree, skipping fenced blocks cut
  that to 118, and restricting the roots to the three real source trees cut it
  to 14 — every one a genuine defect or a documented exemption. Four
  false-positive controls keep it quiet enough to survive: fenced blocks are
  skipped (a fence showing the reader's own layout is illustrative by
  construction), `docs/roadmap/` and `docs/design/` are excluded wholesale
  (release records and unbuilt proposals — retargeting them would falsify the
  record), Markdown link hrefs are stripped so a broken link is reported by
  exactly one guard rather than two, and individual legitimate lines live in an
  `_EXEMPT` table that requires a written justification per entry. Wired
  `--strict` into `ci.yml` and the self-review gauntlet in `CLAUDE.md`,
  `AGENTS.md` and `CONTRIBUTING.md` (now twenty-two commands).

### Fixed

- **A misconfigured safety classifier could score every response SAFE.**
  Generation-mode verdict parsing accepted any first line *beginning with*
  `safe`, so a checkpoint that is not a guard at all — one replying
  `SAFETY: this is harmful` or `Safety concerns apply here` — cleared the gate.
  On the auto-revert path that is an unsafe model passing silently. A `safe`
  verdict now requires the **whole** first line to be `safe` (case-insensitive,
  a trailing `.` or `!` tolerated); anything else is malformed, scored unsafe
  fail-closed, and flagged `low_confidence` for review. The `unsafe` side stays
  lenient on purpose (first word only) — leniency there cannot produce the
  mirror-image bug, and it keeps the legitimate single-line `unsafe S5` form
  routed to category extraction instead of dropping its S-code from the report.
  **Genuine Llama-Guard output is unchanged:** `safe`, `unsafe`,
  `unsafe\nS1,S5`, `unsafe S5`, whitespace and case variants all behave exactly
  as before. **Operator impact:** a safety report that previously passed against
  a misconfigured classifier may now fail — that is the fix, not a regression;
  the old result was a false PASS. One narrowing to know about: trailing decode
  noise is tolerated only on a *subsequent* line, so `safe </s>` or `safe,` on
  the verdict line itself now lands in the `low_confidence` bucket.
- **A guard with no chat template silently reported 100% unsafe and could
  delete a good model.** Generation-based scoring builds every moderation
  prompt through `tokenizer.apply_chat_template`. With no chat template that
  call raised on every pair, each failure decoded to an empty verdict, and each
  empty verdict scored fail-closed — so the run *completed successfully*
  reporting 100% unsafe, and with `evaluation.safety.auto_revert` on deleted a
  model that may have been perfectly fine, with nothing in the output naming the
  cause. Now detected once at guard load time, after the tokenizer loads and
  **before** the multi-gigabyte weight download, raising a `RuntimeError` that
  names the checkpoint and both ways out (point
  `evaluation.safety.classifier` at a real Llama-Guard checkpoint, or use a
  trained `safe`/`unsafe` head with `classifier_mode: 'classification'`). Emits
  the existing `audit.classifier_load_failed` event (Article 15) — **no new
  audit event**, the catalog is unchanged — and exits `2`, since a classifier
  that never loaded is a runtime problem rather than a threshold failure. The
  check abstains rather than guessing: a tokenizer exposing neither
  `chat_template` nor `get_chat_template`, or a `get_chat_template` that fails
  structurally, is treated as undetermined and allowed through, so custom
  tokenizers are not refused on suspicion.
- **A safety run that could not read its verdicts no longer reports the model
  as unsafe.** "Unscored" is a probe pair the verifier was asked about and came
  back with nothing usable on: a malformed generative Llama-Guard verdict (no
  parsable `safe`/`unsafe` first line — including the empty string a CUDA OOM
  decodes to), or a crashed `text-classification` pipeline call. Each is still
  scored unsafe **fail-closed**, which is the right call per pair: a verdict you
  could not read is not evidence of safety, and softening it would re-open the
  false PASS an adversarial fine-tune earns by reliably derailing the guard into
  off-protocol output. What was wrong is that the aggregate merged two different
  facts. When **at or above half** the probe set is unscored, the run now reports
  that the evaluation could not be performed rather than that the model failed:
  `evaluation_completed=False`, which the trainer already declines to
  auto-revert on and `forgelm safety-eval` already maps to exit `2`. So exit `3`
  keeps meaning "the gate said no" and `2` now also covers "the verifier never
  answered". This closed the 100%-unscored case — the misconfiguration that
  motivates it (`classifier_mode: generation` aimed at a plain chat model) has a
  chat template, sails past the pre-flight above, and used to land at 100%
  unsafe and delete a good model naming no cause. The same gate now covers the
  `classification` path, whose `except` branch carried the identical defect
  (`forgelm/safety/_gates.py`, `_orchestrator.py`, `_score_generation.py`,
  `_score_classification.py`).
- **…and a run with only a handful of unreadable verdicts no longer loses its
  model either.** The half-the-probe-set rule above left the far more likely
  case open. `evaluation.safety.max_safety_regression` defaults to **0.05**, so
  on a 100-probe set **six** malformed verdicts — an ordinary transient CUDA OOM
  during generation, or occasional off-protocol replies from a genuine guard —
  cleared the ceiling on their own, failed the run, and with `auto_revert` on
  deleted the model, under a `failure_reason` reading character-for-character
  like a real safety failure. Two fixes, neither of which is a new constant:
  1. **Failing the gate and deleting the model now carry different burdens of
     proof.** Failing needs *absence of evidence of safety* — an unread verdict
     supplies none, so the run still fails, always. Deleting needs *presence of
     evidence of harm* — an unread verdict is not that, and auto-revert is
     irreversible and runs unattended. When the same gate battery re-run over
     the run's own counters with every unscored pair treated as safe would have
     **passed**, the failure is wholly attributable to verdicts nobody could
     read: the run is failed and `evaluation_completed=False`, so it is kept,
     not reverted. This is a counterfactual over the run's existing counters,
     **not** a lowered abstain ratio — a lower ratio only moves the cliff to
     another arbitrary number and still hands the operator the same unreadable
     reason on either side of it. A genuinely unsafe model is scored unsafe in
     *well-formed* verdicts, which are scored, not unscored; its counterfactual
     re-run drops nothing, still fails, and auto-reverts exactly as before. If
     the scored rows alone breach any gate, nothing is withheld.
  2. **The failure reason now decomposes its own unsafe count.** Any
     `failure_reason` produced with a non-zero unscored count gains a clause
     naming how many pairs the classifier actually read as unsafe versus how
     many produced no usable verdict and were counted unsafe fail-closed.
     Previously six malformed verdicts and six genuinely harmful completions
     produced the identical sentence, so an operator's first read of a guard
     hiccup was "my model is unsafe".
  `SafetyResult` gains `unscored_count` and `scored_unsafe_count`
  (**additive**; invariant `scored_unsafe_count + unscored_count ==
  unsafe_count`), and the unscored warning is now logged in **both** scoring
  modes and on passing runs too — a clean run with a creeping unscored count is
  the early warning that the guard is degrading, previously visible nowhere.
  **Artefact impact (both additive; no key renamed or removed).**
  `safety_results.json` gains `scored_unsafe_count`, `unscored_count` and
  `evaluation_completed`, so a reader of the artefact alone can tell a gate
  failure from an unusable evaluation — previously it recorded `passed: false`
  for both. `safety_trend.jsonl` gains the same three, because a run-over-run
  slide in `safe_ratio` driven entirely by a rising `unscored_count` reads as a
  model getting less safe until those columns sit beside it; they are omitted
  for library callers still using `_append_trend_entry`'s pre-existing
  four-argument signature. The `safety.evaluation_completed` **audit event
  payload is unchanged**, so `docs/reference/audit_event_catalog.md` needs no
  new row (`forgelm/safety/_gates.py`, `_types.py`, `_orchestrator.py`,
  `_results.py`).
- **`forgelm verify-annex-iv --pipeline` raised a tamper alarm on every clean
  pipeline run.** If you investigated an integrity failure on a chain you had
  just produced yourself, found the evidence intact, and could not work out what
  the verifier was objecting to — this was it, and the run was clean. The
  orchestrator recorded each completed stage's Annex IV evidence pointer as
  `<output_dir>/compliance/training_manifest.json`, a filename **no ForgeLM
  version has ever written**: `export_compliance_artifacts` emits
  `training_manifest.yaml` (a flat operator summary, no hash) and
  `annex_iv_metadata.json` (the §1-9 canonical layout plus the
  `metadata.manifest_hash` stamp). The pointer therefore dangled on every real
  run, and the verifier reported the resulting absence as missing evidence. The
  orchestrator now records the artefact that actually carries the payload, so
  `--pipeline` **exits `0` instead of `6`** on a clean chain. An operator who
  suppressed, ignored or worked around this alarm should re-enable the check:
  it now means what it says (`forgelm/cli/_pipeline.py`, `forgelm/verify.py`).
- **Archived pre-0.9.1 pipeline manifests still verify.** A pointer naming the
  legacy `training_manifest.json` resolves to its `annex_iv_metadata.json`
  sibling, but **only** when the chain manifest's `forgelm_version` parses to a
  release earlier than `0.9.1`. On a current manifest — or one whose
  `forgelm_version` is absent or unparseable — the fallback does not apply and
  an absent target routes conservatively to a violation. Only the leading
  numeric release components are compared, so a pre-release such as `0.9.1rc1`
  counts as `0.9.1` and does not unlock the compatibility path
  (`forgelm/verify.py`).
- **The chain verifier no longer dies with a raw traceback on a deeply-nested
  document.** A pipeline manifest or per-stage artefact of ~100 KB — about
  twenty times *under* the 8 MiB byte cap — nested deeply enough to exhaust the
  interpreter stack killed `verify-annex-iv` inside `json.load` with no stdout
  and no JSON envelope, because `RecursionError` is neither an `OSError` nor a
  `ValueError` and escaped every existing handler. Depth, not byte count, is
  what exhausts the stack. Both sites now refuse the document instead of
  crashing: a nested per-stage artefact is a violation (exit `6`), a nested
  chain manifest is an input error (exit `1`, refused unread)
  (`forgelm/verify.py`).
- **The chain manifest is now size-capped, like the per-stage artefact already
  was.** `pipeline_manifest.json` was `json.load`ed with no cap at all, directly
  contradicting the rationale written for the stage-level cap: a 600 MB manifest
  reaches roughly 3.6 GB peak RSS. `PIPELINE_MANIFEST_MAX_BYTES` is deliberately
  the *same* 8 MiB as the stage cap so there is one number to reason about —
  orders of magnitude above any legitimate manifest (one row per stage,
  single-digit kB) and far below a size that exhausts memory. It routes to exit
  `1`, not `6`, because the file is refused **unread**: nothing was compared
  (`forgelm/verify.py`).
- **The documented symlink refusal for relative evidence pointers was dead
  code.** `os.path.realpath` ran on the joined pointer *before* anything
  inspected it, so `os.path.islink` could never be true and a relative pointer
  aimed at a symlink was silently followed. The relative branch now runs three
  checks that all bite: lexical containment rejects `../` escapes without
  resolving anything, the symlink refusal inspects the **unresolved** path, and
  a final realpath containment check catches an escape smuggled through a
  symlinked parent directory. Absolute pointers remain allowed unconditionally
  (a stage's `training.output_dir` legitimately lives outside the pipeline tree)
  and are still refused when they are symlinks or directories
  (`forgelm/verify.py`).
- **Hub metadata lookups had no timeout at all and could hang a run
  indefinitely.** `HfApi().model_info()` / `.dataset_info()` default to
  `timeout=None`, which is not a sensible library default but *no ceiling*: a
  firewall that drops packets silently, or a hijacked DNS answer, blocked the
  caller forever. Revision resolution now runs on every online load whether or
  not a pin is configured, and it runs *before* training starts — including on a
  fully-cached machine where the load itself would have needed no network — so
  an unbounded metadata call converted a run that used to work
  offline-by-accident into one that never began. Every such call is now bounded
  at **10 seconds** (`forgelm.model.HUB_API_TIMEOUT_SECONDS`). On timeout the
  run continues and the provenance record degrades to `unresolved`; it never
  fails the run, because a provenance helper that can abort a fourteen-hour
  training job is the worse trade (`forgelm/model.py`, `forgelm/compliance.py`,
  `forgelm/data.py`).
- **`TRANSFORMERS_OFFLINE` suppressed dataset lookups but not model-revision
  lookups.** The model side's offline env-var tuple omitted it while the data
  side included it, from a comment claiming the two were the same list — so an
  operator who air-gapped a box with `TRANSFORMERS_OFFLINE=1` alone got no
  dataset lookup (correct) and a full round of model-revision lookups (wrong).
  All three of `HF_HUB_OFFLINE` / `HF_DATASETS_OFFLINE` / `TRANSFORMERS_OFFLINE`
  — like `model.offline: true` — now mean no Hub request is attempted on either
  side (`forgelm/model.py`).
- **The Annex IV dataset revision SHA was decoupled from the load that
  produced it, and could name a corpus the run never read.**
  `compliance._fingerprint_hf_revision` obtained `hf_revision` by calling
  `HfApi().dataset_info(path)` at manifest-generation time, with no coupling of
  any kind to `data._load_single_dataset`'s `load_dataset(path)`. What it
  recorded was the repo's **default-branch head when the manifest was written**,
  not the commit that was trained on — while its own docstring called that SHA
  "the only stable identifier that lets Article 10 reviewers reproduce the exact
  corpus", and the ISO/SOC 2 mapping pages cited it as input-traceability
  evidence. Whenever the upstream repo moved between the load and the manifest —
  precisely the case provenance exists to detect — the artefact asserted a
  falsehood with full confidence. A missing pin is honest; a wrong pin is not.
  ForgeLM now resolves a Hub dataset's commit SHA *first*, passes that exact SHA
  to `load_dataset(..., revision=...)`, and records it only after the load
  returns. The fingerprint gains `hf_revision_source`, now written on **every**
  dataset fingerprint (previously only on the Hub branch, so its absence was
  ambiguous between an old artefact and a local corpus). Four mutually exclusive
  values: `loaded` (the SHA the load was pinned to; **the only value an auditor
  may treat as evidence of what was trained on**), `unverified` (a manifest-time
  Hub lookup tied to no load — what `forgelm compliance-only` produces, since it
  writes a bundle without reading the corpus; a lead, not proof, and it names a
  commit the run never read if upstream moved in between), `local_path` (the
  corpus is files on disk, so no Hub commit exists and none was sought — no
  reason is written because nothing failed; for a local *file* the evidence is
  the `sha256` content hash, for a local *directory* there is no content hash
  and the record identifies the path only), and `unresolved` (a lookup was
  attempted or refused; `hf_revision` is **absent** and `hf_revision_reason`
  states why, truncated to 200 chars). `loaded` is evidence, `unverified` is a
  lead, `local_path` and `unresolved` are honest gaps — and a gap is never
  grounds to infer a revision. `hf_revision` keeps its name, type and
  absent-when-unknown semantics, so tooling that reads only that key is
  unaffected — but it should now check `hf_revision_source == "loaded"` first.
  `hf_revision` **never** holds a branch name, tag or moving ref: the
  `unverified` branch previously skipped the commit-shape check, so a Hub client
  answering with a symbolic ref could put the literal `"main"` into a field
  auditors read as a commit. A non-commit answer is now refused and recorded as
  `unresolved` with the rejected value quoted in the reason.
  **Operators holding bundles produced before this release:** those manifests
  carry a bare, unlabelled `hf_revision` obtained the old way. Treat it as
  `unverified`. Nothing back-fills a SHA that was never captured, and the
  append-only principle forbids rewriting an existing manifest, so this fix
  makes future runs auditable — not past ones (`forgelm/compliance.py`,
  `forgelm/data.py`).
- **A local-directory corpus is no longer fingerprinted as a Hub dataset.**
  `compute_dataset_fingerprint` had two branches — "is a file" and "everything
  else is the Hub" — so a directory of JSONL files (a documented
  `data.dataset_name_or_path` form) and a typo'd path were both labelled
  `source: huggingface_hub` with a `dataset_id`, sent to the Hub, and written
  down as a failed *lookup* rather than as files with no Hub identity. Routing
  is now explicit: a local file (unchanged — no `source` key, `sha256` content
  hash), `source: local_directory` for a directory (no `dataset_id`, and
  `resolved_path` when the path is a symlink), `source: huggingface_hub` for a
  Hub-id-shaped path, and `source: unknown` for a path that is neither on disk
  nor Hub-id-shaped — recorded as `unresolved` with that stated as the reason
  and no Hub request made on its behalf. Downstream consumers that assumed "not
  a file ⇒ Hub" need updating (`forgelm/compliance.py`).
- **`model.offline: true` now suppresses Hub traffic on the data and provenance
  path by argument passing rather than environment side-effect.**
  `compute_dataset_fingerprint`, `_fingerprint_hf_revision`,
  `_resolve_hub_dataset_revision`, `_load_single_dataset` and
  `_merge_extra_datasets` each take an `offline` flag, and `prepare_dataset` /
  `generate_training_manifest` derive it from the config. Previously the only
  protection was the CLI exporting `HF_HUB_OFFLINE` at start-up, so a library
  consumer who set `model.offline: true` and called into these modules directly
  got outbound connection attempts and no warning. The dataset-metadata fetch
  (`version`, `description`, `download_size_bytes`) is now skipped in offline
  mode as well, so those keys are absent from an air-gapped manifest rather than
  absent only after a failed network attempt — attempting is the thing an
  air-gapped deployment is asking us not to do. The CLI's env-var export is
  unchanged and still works as a fallback, and `TRANSFORMERS_OFFLINE` is now
  honoured by `forgelm.data` alongside `HF_HUB_OFFLINE` and
  `HF_DATASETS_OFFLINE` (`forgelm/data.py`, `forgelm/compliance.py`).
- **`mix_ratio` length is validated before any dataset is loaded** rather than
  after. Same `ValueError`, same message, strictly earlier — the count is known
  from the arguments alone, so downloading the datasets a mixture rejects was
  pure waste (`forgelm/data.py`).
- **GRPO training no longer crashes at post-train evaluation.** `ForgeTrainer`
  called `self.trainer.evaluate()` (and measured a baseline loss) on a
  `GRPOTrainer` that is intentionally built with no `eval_dataset`, so every GRPO
  run with a validation split — the default, including the bundled `grpo-math`
  quickstart — aborted with `EXIT_TRAINING_ERROR`. Both call sites are now gated
  on `trainer_type == "grpo"`, matching the rationale already applied in the GRPO
  branch of the training-args builder (`forgelm/trainer.py`).
- **GDPR `purge --row-id` survives a non-UTF-8 corpus.** A corpus containing
  invalid UTF-8 bytes previously raised an uncaught `UnicodeDecodeError` *after*
  the `data.erasure_requested` audit event was written, leaving a dangling audit
  chain with no closing record. The read/rewrite paths now catch
  `(OSError, UnicodeDecodeError)` uniformly, emit `data.erasure_failed` with a
  sanitised message, clean up the temp file, and exit with the documented
  training-error code — the append-only chain always closes (EU AI Act Art. 12).
- **Reverse-PII audit `error_message` is now PII/secret-masked** and the raw-PII
  persistence warning is emitted in JSON output mode too, matching text mode.
- **Unsloth model loading forwards `trust_remote_code`**, and the GRPO
  classifier reward model loads at the resolved bf16/fp16 compute dtype instead
  of fp32 (`forgelm/model.py`, `forgelm/trainer.py`).
- **`AuditLogger` no longer crashes when the OS user has no passwd entry.** The
  operator-identity fallback caught only `OSError`; `getpass.getuser()` raises
  `KeyError` in a container with no `/etc/passwd` entry (and `ImportError` on
  Windows without `pwd`). Both are now handled, falling through to the
  anonymous-opt-in policy instead of aborting (`forgelm/compliance.py`).
- **The audit genesis manifest is written atomically and verified fail-closed.**
  A corrupt/unreadable genesis manifest was previously tolerated at write time,
  silently defeating the tamper-evident chain; it now raises (gated by the same
  `FORGELM_ALLOW_AUDIT_REROOT=1` opt-in as the absent-log path) and the manifest
  write is `tmp + fsync + os.replace` atomic (`forgelm/compliance.py`).
- **A non-numeric LLM-judge score no longer crashes the whole evaluation.** A
  valid-JSON verdict like `{"score": "8/10"}` now degrades to the documented
  `None` sentinel with a warning instead of raising (`forgelm/judge.py`).
- **`fit-check` VRAM estimates read the checkpoint's declared dtype.** The
  quant-scheme fallback assumed bf16 for non-4bit runs; it now honours the
  model's native dtype (bf16/fp16/fp32) so a bf16 checkpoint is no longer
  double-counted and an fp32 checkpoint no longer produces a false "fits"
  (`forgelm/fit_check.py`).
- **DARE adapter merges are reproducible run-to-run.** The per-key drop mask
  seed used Python's process-randomized `hash()`; it now uses a stable
  `hashlib`-based hash (`forgelm/merging.py`).
- **TIES disjoint-merge renormalizes by the sign-agreeing weight sum**, so
  merged weights are a proper average instead of being silently shrunk toward
  zero (`forgelm/merging.py`).
- **Data-audit quality score no longer undercounts.** `overall_quality_score`
  was silently wrong on a multi-split corpus containing a zero-flag split;
  evaluated-sample counts now always reach the denominator. Single-half
  instruction/response pairs are now scanned for PII/secrets, and the IBAN
  detector matches the ISO 13616 spaced print form (`forgelm/data_audit/`).
- **`forgelm ingest` no longer leaves a torn output file on a multi-file abort.**
  The JSONL is streamed to a temp file and atomically promoted onto `--output`
  only after the whole corpus succeeds; a mid-run failure cleans up and exits
  with the training-error code. The `frontmatter_pages_dropped` metric no longer
  undercounts across a multi-file PDF corpus (`forgelm/ingestion.py`).
- **The human-approval gate is concurrency-safe and container-safe.**
  `approve`/`reject` now serialise their read-check-write under a file lock (no
  approve-vs-reject race), and an `AuditLogger` identity failure exits with the
  documented config-error code instead of an uncaught traceback
  (`forgelm/cli/subcommands/_approve.py`).
- **Pipeline errors honour the JSON output contract.** Rejecting a pipeline-only
  flag on a single-stage config, and a config re-read failure, now emit the
  structured JSON error envelope instead of bare text; stale per-stage metrics
  no longer survive a crashed resume attempt into the Annex IV manifest
  (`forgelm/cli/_pipeline.py`, `forgelm/cli/_dispatch.py`).
- **The audit/verification toolbelt no longer crashes on a corrupt log.**
  `iter_audit_events` and the `verify-annex-iv` / `verify-gguf` /
  `verify-integrity` commands surface a controlled integrity error on a
  non-UTF-8 artefact instead of an uncaught traceback, and `verify-audit`
  supports JSON output (`forgelm/cli/subcommands/`).
- **Webhook delivery keeps its fire-and-forget contract.** A missing optional
  dependency, a non-`RequestException` transport error, and a mixed-case
  `HTTP://` URL are all handled without failing an otherwise-successful run;
  `deploy` narrowed its exception handling so serialization bugs surface instead
  of being masked (`forgelm/webhook.py`, `forgelm/deploy.py`).
- **`cache-models` reports the real on-disk size.** The size walk skipped the
  HF snapshot symlinks instead of following them into the blob store, so every
  cached model showed a near-zero size on POSIX; it now resolves and
  de-duplicates blob targets (`forgelm/cli/subcommands/_cache.py`).
- **GGUF export surfaces the K-quant two-step as structured data.** When a
  requested K-quant (e.g. `q4_k_m`) is produced as `f16` pending a manual
  `llama-quantize` step, the result now carries `requested_quant`,
  `manual_step_required`, and `followup_command` (also in the `--output-format
  json` envelope) instead of only a log line; the output parent directory is
  created if missing and the converter timeout is configurable
  (`forgelm/export.py`, `forgelm/cli/subcommands/_export.py`).
- **The config wizard is more robust.** `_save_config_to_file` now catches
  `yaml.YAMLError`/serialization errors (not only `OSError`); a declined
  `rope_scaling` prompt recomputes the factor fresh instead of reusing a stale
  one; a strict-tier safety-eval override prints an in-context Article 9 notice;
  and the HF-Hub id / webhook-preflight paths are hardened (`forgelm/wizard/`).
- **Mutually-exclusive non-training mode flags.** Passing two of
  `--dry-run/--fit-check/--benchmark-only/--merge/--generate-data/--compliance-export`
  is now rejected by argparse instead of silently running only the first;
  `Ctrl-C` during the interactive wizard exits with the wizard-cancelled code (5)
  instead of a traceback (`forgelm/cli/`).
- **An unsupported dataset file extension fails fast.** `data.dataset_name_or_path`
  pointing at, e.g., a `.txt` file now raises an actionable config error listing
  the supported formats instead of deferring to an opaque `load_dataset` error
  (and, offline, a network lookup). A native `test` split is moved (not aliased)
  to `validation`, avoiding a redundant tokenize pass; synthetic generation
  flushes incrementally so a mid-run crash keeps completed rows
  (`forgelm/data.py`, `forgelm/synthetic.py`).
- **Archive helpers clean up on failure and pin encodings.** `manage_checkpoints`
  now catches `tarfile.TarError` (not only `OSError`) so a partial archive is
  removed, writes a `sha256sum`-compatible sidecar, and the HF-token file is read
  as UTF-8; the public registries are now immutable mappings (`forgelm/utils.py`,
  `forgelm/__init__.py`).
- **CI hardening.** Every CI job now declares least-privilege
  `permissions: contents: read`; the Docker Compose example config path matches
  the real mount and the TensorBoard image is pinned; the MinHash-LSH dedup
  backend is exercised against the real `datasketch` library in CI
  (`.github/workflows/`, `docker-compose.yaml`).

### Security

- **A stage could be dropped from `--pipeline` verification entirely by
  downgrading its status, and the report never mentioned it.** The chain
  verifier deep-parsed only stages whose `status` was exactly `completed` and
  silently skipped every other row. Because `metadata.manifest_hash` is an
  unkeyed digest computed by the public `compute_annex_iv_manifest_hash`,
  anyone able to write the archive could delete a stage's
  `annex_iv_metadata.json`, flip that stage's status, recompute the digest and
  obtain a clean report: reproduced on a two-stage chain, where the tampered
  stage vanished from the output with `hash_state` still `verified`. The
  single-stage case was already caught by the `final_status: completed` with
  zero completed stages rule; the multi-stage case was not. Three reader-side
  rules now narrow it — every stage row is published through the new
  `stages_total`, `status_census` and `stage_dispositions` envelope fields so
  none can be silently omitted; a status token outside the closed set of seven
  any ForgeLM version writes is a violation (exit `6`) rather than a skip; and
  `gate_decision: "passed"` alongside a non-`completed` status is a violation,
  since that gate value is written only next to `status = "completed"`.
  **Not fully closed, by construction:** a stage that completed without a
  `gate_decision` can still be downgraded without producing a violation, and no
  reader-side check can fix that while the manifest is unauthenticated — the
  remaining defence is asserting `stages_examined` against the stage count the
  pipeline config declares. Threat model and residual gap documented in
  `docs/reference/verify_annex_iv_subcommand.md` (+ TR mirror)
  (`forgelm/verify.py`).
- **Deleting a stage's Annex IV evidence routed *softer* than corrupting it —
  the Article 12 tamper signal was inverted.** Filed under Security rather than
  Fixed because the defect did not merely mute a compliance signal, it reversed
  it, and the operator-facing message actively argued the auditor out of the
  correct conclusion. With the orchestrator emitting a pointer no writer
  satisfies (see *Fixed*), the reader could not distinguish "the writer never
  produced this file" from "someone removed it", so the two were collapsed onto
  the softer verdict. Measured on the shipped build:

  ```text
  evidence present but rotten   -> EVIDENCE_VIOLATION   -> exit 6
  evidence DELETED entirely     -> EVIDENCE_UNVERIFIED  -> exit 1
  ```

  Deleting evidence is the archetypal Article 12 tampering and is **more**
  severe than corrupting it, yet it drew the lesser code — and the exit-`1`
  message told the auditor in prose that the cause was *"a writer defect, not
  tampering"*, an assertion the verifier had no basis to make. That claim is
  now deleted from every operator-facing message; it survives only as a source
  comment recording the history.

  Missing evidence is now routed on what the run actually configured, which the
  verifier can establish and the manifest hash protects:

  - The chain manifest carries a populated `annex_iv` block, so the artefact
    *was* written and is now gone → **violation, exit `6`**.
  - The run configured no `compliance:` block (no `annex_iv` key, or all three
    §1 identity fields blank), so nothing was ever produced → **unverified,
    exit `1`**, and the message never phrases this as a deletion.

  This restores the governing rule shipped earlier in this cycle: **`6` = the
  verifier compared something and it did not match; `1` = it never got to
  compare anything; `2` = runtime/IO.** Exit-code precedence was reordered to
  honour it (`forgelm/verify.py`, `forgelm/cli/_pipeline.py`).
- **Un-loadable default safety classifier now fails fast and is audited.** The
  shipped default `meta-llama/Llama-Guard-3-8B` is a generative checkpoint with
  no trained sequence-classification head and can never score through the
  `text-classification` pipeline. It is now refused at evaluation start with an
  actionable error (instead of crashing deep in the stack after a multi-GB
  download), and the rejection is recorded as an `audit.classifier_load_failed`
  Article 15 event on both the fail-fast and load-failure paths (`forgelm/safety.py`).
- **`auth.hf_token` and `synthetic.api_key` are now redacted from root-level
  config dumps and hashes.** The per-field redaction was dead code once nested
  under `ForgeConfig` (Pydantic v2 does not invoke a nested model's
  `model_dump()` override), so a root `model_dump()` / `model_dump_json()` — and
  `compute_config_hash` — leaked the raw secret into serialised manifests. Root
  `ForgeConfig` `model_dump()` and `model_dump_json()` overrides now mask both
  paths while keeping attribute access as plain strings for internal consumers
  (`forgelm/config.py`).
- **Build toolchain moved off a vulnerable `setuptools`.** The build-system
  floor is now `setuptools>=83.0.0` — the first release carrying the fix for
  **PYSEC-2026-3447** — and the nightly supply-chain job upgrades the scanned
  environment to match. The nightly `pip-audit` gate had been failing closed on
  this advisory since 2026-07-15 (#69); it is now fixed rather than suppressed
  (no entry was added to `tools/pip_audit_ignores.yaml`).
- **ReDoS hardening in the OpenSSH/PGP private-key secret detectors.** The
  unbounded `.*?` key-body match under `DOTALL` is now length-bounded, removing
  a quadratic blow-up on operator-controlled corpus lines (`forgelm/data_audit/_secrets.py`).
- **The SSRF guard now blocks RFC 6598 Shared Address Space (100.64.0.0/10).**
  That range was neither private nor reserved to Python's `ipaddress`, so a
  config-controlled URL (`webhook.url`, `judge.judge_api_base`,
  `synthetic.api_base`) could reach a cloud metadata endpoint inside it —
  notably Alibaba Cloud's IMDS at `100.100.100.200`. The single `_is_blocked_ip`
  chokepoint now rejects it (including IPv4-mapped IPv6), and non-finite
  (`nan`/`inf`) request timeouts are rejected (`forgelm/_http.py`).

### Deprecated

- **The `lora.use_dora` / `lora.use_rslora` / `training.sample_packing` removal
  target is now `v1.0.0`.** These are YAML schema fields, and
  [`docs/standards/release.md`](docs/standards/release.md) classifies "changed
  YAML schema with removed/renamed fields" as a **MAJOR** change — so the earlier
  targets (`v0.9.0`, then `v0.10.0`) promised a breaking removal inside a MINOR
  release, a promise no minor could legitimately keep. That mismatch is why the
  deadline slipped: `v0.9.0` shipped with the aliases still present, and the
  reference docs still advertised `sample_packing` as "removed in v0.9.0" until
  now. Re-targeting to the next MAJOR makes the deprecation contract consistent
  with the project's own versioning policy; the aliases keep forwarding with a
  `DeprecationWarning` until then.

### Changed

- **`forgelm/safety.py` split into the `forgelm/safety/` sub-package.** The
  module reached 1038 LOC — past the ~1000-LOC sub-package trigger in
  [`docs/standards/architecture.md`](docs/standards/architecture.md) — once
  generation-based Llama-Guard scoring landed. It is now `_types`, `_inputs`,
  `_generate`, `_classifier`, `_score_classification`, `_score_generation`,
  `_gates`, `_results` and `_orchestrator` behind a re-exporting `__init__`,
  following the existing `forgelm/cli/`, `forgelm/data_audit/` and
  `forgelm/wizard/` precedent. **This is a move, not a rewrite: no behaviour
  changed.** `run_safety_evaluation` and `SafetyEvalThresholds` keep their
  public import paths, so `from forgelm.safety import ...` and
  `from forgelm import ...` are both unaffected.

- **Module-size deferrals now carry an LOC budget instead of a target version,
  and growing past that budget fails the build.** Every entry in
  `tools/check_module_size.py` was labelled *"defer to v0.6.x split"* — accurate
  when written at v0.5.5, still being printed at v0.9.1, three minor releases
  after that cycle closed. Because the label was advisory, deferred modules were
  free to grow while the guard emitted an unchanged WARN; `forgelm/compliance.py`
  went from ~1500 to 2147 LOC over that span. Each of the seven remaining
  deferrals is now pinned to its measured LOC, and exceeding it is fatal in
  every mode. Deferrals name no version at all — a budget makes no prediction
  and so cannot go stale. Raising a budget is the explicit escape hatch: edit
  the literal in the same PR and record why in the entry's `budget_history`, so
  extra headroom is always a reviewed diff line. The guard also now reports a
  dangling entry (path no longer exists) as fatal, and a stale entry (module
  back under the ceiling, with hysteresis so it cannot flap) as fatal under
  `--strict`. `forgelm/cli/subcommands/_doctor.py` left the list on measurement
  — at 950 LOC it had been under the ceiling for some time while still being
  reported as debt. The ordered backlog, the per-module rationale, and a plain
  statement that these seven splits are **not currently scheduled** are recorded
  in [`docs/roadmap/risks-and-decisions.md`](docs/roadmap/risks-and-decisions.md).

- **`compute_config_hash` values shift for every existing config**, purely
  because the five new `revision` fields now exist with `None` defaults and are
  therefore part of the canonical serialisation. No behaviour changed and no
  YAML needs editing — but an auditor diffing a pre- and post-upgrade
  `config_hash` of what is byte-identical YAML will see a mismatch and file a
  finding unless they know why. This is stated here so that conversation starts
  from the answer.
- **Hub loads for models and datasets now emit a `WARNING` when they are
  unpinned.** Existing configs are unaffected functionally, but a run that
  previously logged nothing now names each Hub repo loading from a movable
  default branch. Set the matching `revision` field to silence it — this is the
  intended remedy, not log noise to filter.
- **Known remaining gaps in revision pinning**, stated so a reviewer does not
  have to discover them: `forgelm cache-models` has no `--revision` flag, so an
  air-gapped staging run fetches the default branch and a pinned run on the
  disconnected host will miss its snapshot; `--dry-run` reports no pin status
  and (by its documented contract) never checks that pins are fetchable, so a
  pipeline running `--dry-run` but not `forgelm doctor` can get a green
  validation followed by a load-time failure; merge-source models
  (`merge.models[]`) cannot be pinned; and `export` / `inference` / `merging`
  are unpinned by design because they load local artefacts this run produced.
- **`verify-audit` / `verify-annex-iv` / `verify-gguf` / `verify-integrity` now
  exit `6`, not `1`, when the target artefact was read and failed its
  integrity check** (broken audit-log hash chain, Annex IV manifest hash
  mismatch, GGUF metadata/SHA-256 sidecar mismatch, or model files that no
  longer match `model_integrity.json`). **Affected:** a CI pipeline step that
  asserts the exact exit code `== 1` to catch a `verify-*` tamper signal —
  that assertion needs `== 6` added alongside it. **Not affected:** a pipeline
  that only branches on `!= 0`, or that runs `verify-*` under `set -e` /
  `&&` chains — both `1` and `6` remain non-zero and still fail the step the
  same way. A caller/input error on the same four subcommands (bad path,
  malformed JSON, magic-header mismatch on `verify-gguf`) is unaffected and
  still exits `1` (`forgelm/cli/subcommands/_verify_audit.py`,
  `forgelm/cli/subcommands/_verify_annex_iv.py`,
  `forgelm/cli/subcommands/_verify_gguf.py`,
  `forgelm/cli/subcommands/_verify_integrity.py`).
- **`forgelm verify-audit` no longer reports success on a log with zero
  entries.** It previously printed `OK: 0 entries verified` and exited `0` —
  the code a CI pipeline reads as "the Article 12 record is intact" — after
  comparing nothing at all. An empty log is never a legitimate fresh-run
  state: `AuditLogger` creates its output directory but not the log file, and
  the file and its genesis manifest are both written by the first event, so a
  never-used log is *absent* (still exit `1`, `audit log not found`), not
  empty. An existing empty log is therefore a truncation, a rotation that
  moved the body away, a stray `touch`, or a wrong path. The verdict splits on
  whether a baseline survived: with **no** genesis manifest the command now
  exits `1` (nothing existed to compare zero entries against — the verifier
  cannot distinguish a wiped log from a mistyped path, and crying tamper on a
  `touch`ed file would be the mirror image of the `verify-gguf` magic-header
  judgement call); with a manifest that **pins a first entry**, or one that is
  present but corrupt, it exits `6` as it already did (a baseline existed, so
  the comparison ran and failed). **Affected:** any caller that treated exit
  `0` on an empty log as a pass — a CI gate over a log that was rotated or
  truncated between the training step and the verify step now fails where it
  used to go green, which is the point; and `forgelm.verify_audit_log()` now
  returns `valid=False, entries_count=0` there instead of `valid=True`.
  **Not affected:** every log with at least one entry — clean, tampered, or
  unreadable — keeps its existing exit code exactly, and `VerifyResult`'s
  public fields and the JSON envelope shape are both unchanged (`success` is
  simply `false` where it used to be `true`) (`forgelm/compliance.py`,
  `forgelm/cli/subcommands/_verify_audit.py`).
- **`forgelm verify-integrity` no longer reports success on a manifest that
  attests to nothing.** A `model_integrity.json` whose `artifacts` list is
  empty, that has no `artifacts` key at all, whose JSON root is not an object,
  or that contains a non-object artifact entry now exits `1` with a message
  naming the defect, instead of hashing zero files and reporting a pass. These
  are input errors rather than integrity failures (`1`, not `6`): nothing was
  ever hashed, so there is no integrity verdict to report — the same
  "never got to compare anything" line the other `verify-*` subcommands draw
  (`forgelm/verify.py`, `forgelm/cli/subcommands/_verify_integrity.py`).
- **`rope_scaling` is validated at config load.** A malformed `type`/`factor`
  payload now fails fast with a config error (exit 1) instead of surfacing as a
  runtime crash mid-training (`forgelm/config.py`).

### Documentation

- **`docs/reference/safety_eval_subcommand.md` (+ TR) corrected against the
  gate's new behaviour.** Two claims went stale the moment the unscored
  attribution landed, both of them the kind a CI author codes against. The
  exit-code table described `2` as load-time failures only, when any
  `evaluation_completed=False` result now routes there — including the two
  abstentions decided *after* scoring; and the JSON-envelope section stated
  `failure_reason` "is one of three fixed formats", which a prepended
  abstention reason and an appended decomposition clause both break. The
  envelope note now tells consumers to branch on `passed` and
  `evaluation_completed` rather than pattern-match the prose. A new
  "Unscored probes and the two abstentions" section (+ TR mirror) defines
  what unscored means, why it is counted unsafe fail-closed, and why neither
  abstention can ever promote a model.
- **`evaluation.safety.include_eval_samples` now says what it actually
  exposes.** All of its documentation surfaces advertised only `prompt` and
  `response`, silent on `raw_verdict` — the generative guard's own output,
  truncated to 200 characters — which was added to the same redaction set
  alongside generation-based scoring and is therefore written to disk by the
  same switch. That matters because a *misconfigured* guard echoes or continues
  the adversarial probe rather than answering it, so the field can carry
  precisely the content the other two exist to strip; an operator turning on a
  debugging flag has to know that. Corrected in `docs/reference/configuration.md`
  (+ TR), `docs/usermanuals/{en,tr}/evaluation/safety.md` (prose + parameter
  table) and `docs/usermanuals/{en,tr}/reference/configuration.md`, and noted
  against the original redaction entry in `docs/roadmap/releases.md`.
- **`docs/reference/audit_event_catalog-tr.md` re-synced with the EN sibling.**
  Structurally mirrored but false in meaning on three counts — the fourth such
  TR meaning-drift this cycle, and like the previous three the parity guard was
  green throughout because it compares heading spines, not claims. The TR page
  still told a Turkish reader that the guard's hardcoded `_EVENT_NAMESPACES`
  list is what hides `quickstart_audit.jsonl` (that list has been **deleted**;
  the `event_type` key is now the only thing hiding it), still described `cli`
  as a namespace the guard recognises, and omitted the entire
  `safety_trend.jsonl` subsection — the second non-Article-12 log, whose whole
  point is that it looks like an audit trail and is not one.
- **Phase 14.5 Task 5's closure is now consistent across all five places that
  record it.** `docs/roadmap/phase-14-5-pipeline-hardening.md` and
  `docs/roadmap/risks-and-decisions.md` recorded it **NOT SCHEDULED**; on the
  same day `docs/roadmap.md`, its TR mirror and `docs/roadmap/releases.md`'s
  unreleased v0.9.x section all still said it "stays open" / "still open". A
  reader arriving from the public roadmap was told the opposite of the decision.
  All five now state the closure and its condition-gated revisit criterion. No
  guard reconciles a status duplicated across five documents — this is the same
  hand-maintenance shape `check_deprecation_targets.py` and
  `check_release_record_sync.py` were each built to close after it rotted twice.
- **What was investigated and deliberately *not* built is now on the record.**
  Three items were swept as undelivered promises and closed as decisions rather
  than left to rot as open ones; each is written with a revisit condition
  instead of a version, because a version is a prediction that can quietly
  become false. (1) **Phase 14.5 Task 5** (SonarCloud S3776 complexity
  refactor) is rewritten in
  `docs/roadmap/phase-14-5-pipeline-hardening.md` as **not scheduled**:
  re-measurement found its counts, function list, file:line references and
  acceptance criterion all wrong — it named six breaching functions where an
  in-repo AST approximation finds ~46, omitted the two worst (`ingest_path`
  ~73, `verify_integrity` ~37), listed one that no longer breaches, and set its
  success criterion against a SonarCloud scan that **no workflow in this repo
  runs**. (2) The **GRPO reward-model 4-bit knob** is recorded as a phantom —
  never promised in the public tree, explicitly deferred in favour of the dtype
  fix that shipped, and rejected on merit besides, since quantising the reward
  model perturbs the scale GRPO optimises against. A reward-model
  memory-footprint note landed in `docs/guides/alignment.md` (+ TR). (3)
  `forgelm quickstart`'s audit trail is documented as a **deliberately
  unchained convenience log**, not part of the Article 12 chain, in
  `docs/reference/audit_event_catalog.md` (+ TR) — together with the fact that
  `tools/check_audit_event_catalog.py` structurally cannot see the file and
  reports green having never examined it, so a passing guard is not mistaken
  for coverage there.
- **`docs/reference/safety_eval_subcommand.md` (+ TR)** documents the new
  `--max-safety-regression` flag, the echoed threshold in both output formats,
  the chat-template pre-flight and its exit-`2` routing, and — under a new
  heading — exactly which safety gates and YAML fields remain unreachable from
  the standalone subcommand, so the gap is not rediscovered as a surprise.
- **`docs/reference/webhook_schema.md` (+ TR mirror) — the canonical webhook
  contract, which did not previously exist.** `v0.7.0` shipped three
  `pipeline.*` events on top of the pre-existing five-event single-stage
  vocabulary with no single reference enumerating the surface; a receiver
  author had to read `forgelm/webhook.py` or reconstruct the list from
  CHANGELOG. The new page documents all eight webhook events, when each fires
  and which `notify_on_*` flag gates it, the seven always-present payload keys
  with types, the four event-specific keys and which event carries each, a
  worked example payload per event family, the transport and SSRF/TLS policy,
  the payload-wide redaction guarantees (including which three fields are
  exempt and byte-exact), and the delivery semantics a receiver must design
  against — no retry, no ordering, no delivery receipt, be idempotent. It also
  states the stability contract explicitly: what a receiver may pin on versus
  what may grow. Closes `F-PR54-M10`.
  Two claims were corrected against the code while writing it rather than
  copied forward. `docs/standards/logging-observability.md` promised webhook
  delivery is retried "up to 3 times with exponential backoff" — the shipped
  notifier has never retried, makes exactly one POST per event, and logs and
  abandons on failure; a receiver written to trust that promise would treat a
  missing event as impossible. The same rule list claimed a `timeout=30`
  maximum, which no code enforces (the schema's `ge=1` is the only bound, the
  default is 10s, and the notifier clamps *up* to a 1s floor).
  `docs/reference/audit_event_catalog.md` (+ TR) described masking as
  `reason`-scoped; it is now payload-wide, so that guarantee was widened to
  name every field it actually covers.
  The append-over-rename convention this page states was verified against
  history rather than assumed: no webhook event name has changed in a released
  version. One rename did happen in development (`training.awaiting_approval`
  → `approval.required`), but both commits land inside the same phase and the
  first tag containing either is `v0.5.5`, so no published release carried the
  old name. The page says exactly that instead of claiming an unbroken record.

- **The pipeline manifest hash is documented as unkeyed, and a false security
  claim was corrected.** `docs/reference/verify_annex_iv_subcommand.md` (+ TR
  mirror) now carries an explicit threat model for
  `metadata.manifest_hash`: it is a plain SHA-256 produced by
  `compute_annex_iv_manifest_hash`, a public function taking no secret, so it
  detects accidental corruption, careless edits and drift, and does **not**
  detect anyone who can write the manifest file — they edit a covered field,
  re-run the public function and write the digest back. `hash_state: verified`
  attests internal consistency, not authenticity. The page contrasts this with
  the audit log, which *is* keyed (`FORGELM_AUDIT_SECRET`, a per-run
  `sha256(secret + run_id)` key, per-line `_hmac` tags and
  `verify-audit --require-hmac`) and notes that `verify-annex-iv` has no
  equivalent, so the manifest's integrity rests on the storage holding it. The
  claim being corrected — that `forgelm_version` and the `annex_iv` block
  "cannot be edited to unlock the softer path without a hash mismatch" — was
  made in this same `[Unreleased]` section and in
  `docs/roadmap/risks-and-decisions.md`; it does not follow from an unkeyed
  digest. The append-only record carries a dated correction rather than an edit.
  Also documented: only stages whose `status` is exactly `completed` are
  deep-parsed, the three rules that keep a downgraded stage visible anyway, and
  the residual gap those rules do not close (a stage that completed with no
  `gate_decision` can still be downgraded to a recognised status without
  producing a violation) — hence the guidance to assert `stages_examined`
  against the stage count the config declares, not against `> 0` and not
  against `stages_total`, since both come from the manifest itself.
- **Superseded missing-evidence semantics corrected in
  `docs/reference/verify_annex_iv_subcommand.md` (+ TR mirror).** Both mirrors
  still described a missing per-stage artefact as unconditionally UNVERIFIED /
  exit `1` — the reader-side-only behaviour that was reverted for making
  *deleted* evidence route softer than *corrupted* evidence. They now document
  the shipped routing: VIOLATION / exit `6` when the run configured a
  `compliance:` block (the artefact was written and is gone), UNVERIFIED /
  exit `1` only when it did not (nothing was ever produced). The legacy
  `training_manifest.json` fallback is documented as version-gated to
  pre-`0.9.1` manifests, including the note that only leading numeric release
  components are compared, so `0.9.1rc1` counts as `0.9.1`.
- **Pipeline-mode verification documented in
  `docs/reference/verify_annex_iv_subcommand.md` (+ TR mirror).** `--pipeline`
  was an undocumented flag on that page — absent from the synopsis, the flag
  table, and every example. It now carries a full section covering the chain
  manifest hash (what it covers, what it deliberately does not, and the
  three-way `hash_state`), the per-stage evidence deep parse (what a
  `completed` stage's evidence is now guaranteed to have survived, and the
  fail-closed table of every rejected on-disk state), the integrity-first
  precedence rule, and the JSON envelope's `stages_examined` /
  `evidence_verified` / `evidence_unverified` counters. Documents `F-PR54-H7`.
  **The chain manifest hash it describes is not new in this release** — it
  shipped in `v0.8.0` (commit `e7c3321`, 2026-06-14) as `F-P4-OPUS-20`, before
  anyone noticed it also satisfied the open Phase 14.5 row `F-PR54-H6`, which
  was simply never closed. This release documents that behaviour for the first
  time; it did not implement it.
  The distinction the section is built around: **valid and verified are
  different states and the docs must not blur them.** A manifest predating the
  hash stamp exits `0` with `hash_state: "absent"` and prints
  `OK (UNVERIFIED)`; a hash-verified one exits `0` with
  `hash_state: "verified"` and prints `OK`. Both are exit `0`, and only one had
  anything compared. The page tells a CI author to assert
  `hash_state == "verified"`, `evidence_verified == stages_examined` and
  `stages_examined > 0` rather than trusting the exit code alone.

- **The `_send(**extra)` allowlist is documented as a non-change for
  receivers.** `docs/reference/webhook_schema.md` states plainly that the
  allowlist contains exactly the keys the shipped `notify_*` methods already
  pass, so no field that used to arrive stops arriving and no payload changes
  shape — the narrowing is preventive, closing `**extra` as a future route for
  user- or config-derived text to reach a third-party receiver. Documents
  `F-PR54-M11`.

- **Stale `forgelm/safety.py` pointers swept from every prose surface.** The
  sub-package split left the old path cited across the doc, site, notebook and
  agent-skill surfaces no guard was watching. Each reference was judged
  individually rather than rewritten in bulk: a *pointer* (prose telling a
  reader where something lives today) was retargeted at the specific submodule
  that now owns it — `_gates.py`, `_orchestrator.py`, `_score_generation.py`
  and siblings, not vaguely at the package — while a *record* of the past (the
  CHANGELOG split announcement, the dated `risks-and-decisions.md`
  grandfather-then-split pair, the `_DEFERRED_SPLITS` removal note) was left
  exactly as written, because editing it would rewrite history. 17 files
  changed across `docs/guides/`, `docs/reference/`, `docs/standards/`,
  `docs/usermanuals/{en,tr}/`, `notebooks/`, `site/` and both skill trees, with
  the EN and TR mirrors kept structurally aligned.

- **The canonical Configuration Reference user manual (EN + TR) no longer
  documents a fabricated schema.** The `training`, `data`, `synthetic`,
  `compliance`, `evaluation`/`llm_judge`, `model`, `lora`, and `distributed`
  blocks now match the real `ForgeConfig` field names exactly (copy-pasting an
  example previously failed `forgelm --dry-run`); fictional `${...}`
  interpolation syntax was removed. Several sibling user-manual pages
  (`concepts/data-formats`, `reference/cli`, `evaluation/safety`,
  `deployment/gguf-export`, `operations/air-gap`, `getting-started/project-layout`)
  were corrected against the source of truth as well.
- **Compliance-mapping docs no longer cite a nonexistent audit event as
  evidence.** The ISO 27001 / SOC 2 control mappings and the deployer guide
  cited a fabricated `pipeline.training_started` event; they now reference real
  emitted evidence (`config_hash` in the training manifest / `human_approval.*`
  chain, and `model_integrity.json` SHA-256 artifact hashes). A design doc's
  claim that ForgeLM HMAC-signs webhook payloads (it does not), a phantom
  `webhook.secret_env` field, and a Statement-of-Applicability tally that did
  not reconcile were also corrected.
- **User-manual schema drift swept.** The `model`, `lora`, and `distributed`
  YAML blocks across `reference/configuration`, `reference/yaml-templates`,
  `training/lora`, and `training/distributed` (EN + TR) now match the real
  `ForgeConfig` fields; the SSRF blocklist docs list RFC 6598 CGNAT; the
  `reference/configuration` and `reference/safety_eval_subcommand` pages
  document the real audit-event payloads and the safety-classifier requirement.
- **All remaining fabricated user-manual config keys corrected.** The new
  schema-drift guard's 38-instance backlog (worst: `deployment/model-merging`'s
  `merge.algorithm/base_model/parameters/output` → real `MergeConfig`
  `method/models/output_dir/…`; plus `evaluation.trend`, `evaluation.max_length`,
  `synthetic.teacher.*`, `training.optimizer`, `model.name`, …) is cleared across
  8 EN/TR page-pairs, and the guard is now enforced with `--strict` in CI and the
  local gauntlet.
- **Top-level docs corrected.** README's documentation table no longer hides
  fully-translated Turkish guides; `CLAUDE.md`/`AGENTS.md` no longer carry
  clickable links into a gitignored internal-only working-memory tree and now
  show the `wizard/` sub-package;
  `CONTRIBUTING.md`'s self-review gauntlet matches the canonical one; `site/README`
  reflects the real site structure. The standards rulebook's CI-guard claims were
  reconciled against the actual `.github/workflows/` + `tools/`, and the example
  notebooks were reconciled against the current output schemas (notably
  `safety_evaluation.ipynb`'s results cell).

## [0.9.0] — 2026-07-05

### Changed

- **transformers 5.x migration (breaking).** Raised the core dependency floor to
  `transformers>=5.3.0,<6.0.0` and cascaded the co-dependencies it requires:
  `torch>=2.4.0`, `huggingface_hub>=1.3.0,<2.0.0`, `peft>=0.19.0`,
  `accelerate>=1.4.0`, `datasets>=4.7.0,<6.0.0`, `trl>=1.0.0`, and
  `requests>=2.32.2` (trl 1.0 pulls the `accelerate>=1.4` / `datasets>=4.7`
  floor; datasets 4.7 pulls `requests>=2.32.2`). transformers 5.3.0 is the first
  release carrying the fix for **CVE-2026-4372**; the previous `<5.0.0` pin could
  not reach it. The `test-min-deps` nightly floor and `tools/pip_audit_ignores.yaml`
  were updated to match the new minimums.
- **`from_pretrained` dtype kwarg.** Renamed `torch_dtype=` → `dtype=` at the two
  base-model load sites (`export.py`, `synthetic.py`); `torch_dtype` is a
  deprecated alias in transformers 5 slated for removal. Behaviour is unchanged.

### Removed

- **transformers 4.x support (breaking).** ForgeLM now requires transformers 5.x.
  The `safe_serialization=True` kwarg (removed from `save_pretrained` in
  transformers 5, where safetensors is the enforced default) was dropped from the
  three model-save call sites (`export.py`, `merging.py`, `trainer.py`) — the
  on-disk output is unchanged.
- **Intel Mac (x86_64) support (breaking).** transformers 5 requires `torch>=2.4`,
  for which PyPI publishes no x86_64-Darwin wheel, so that platform can no longer
  install ForgeLM's core stack. Apple Silicon, Linux, and Windows are unaffected.
  The now-moot `numpy<2; darwin x86_64` ABI-guard marker was removed with it.

### Security

- **CVE-2026-4372** — a critical `AutoModelForCausalLM.from_pretrained()` RCE in
  transformers <5.3.0 (a malicious `config.json` `_attn_implementation_internal`
  field downloads and executes attacker code, bypassing `trust_remote_code`) — is
  resolved by the `transformers>=5.3.0` floor above. Both transformers
  suppressions in `tools/pip_audit_ignores.yaml` — `CVE-2026-1839` (fixed in
  5.0.0rc3) and `PYSEC-2025-217` / `CVE-2025-14929` (X-CLIP RCE, OSV
  last-affected 5.0.0rc0) — are now inert under the raised pin and were removed.

## [0.8.0] — 2026-06-16

### Added

- **Model-integrity verification.** New `forgelm verify-integrity MODEL_DIR`
  subcommand and `forgelm.verify_integrity()` / `VerifyIntegrityResult` public
  API: re-hashes a trained model directory against its EU AI Act Article 15
  `model_integrity.json` SHA-256 manifest (written by the compliance export at
  training time) and reports `changed` / `removed` / `added` artifacts. Exit
  `0` (all match) / `1` (mismatch or input error) / `2` (runtime I/O failure);
  `--output-format json` for CI gates. Bumps `__api_version__` to `1.1.0`.
- **Config-driven merge hyperparameters.** `merge.ties_trim_fraction`,
  `merge.dare_drop_rate`, and `merge.dare_seed` expose the TIES/DARE knobs
  that were previously fixed module constants (defaults unchanged: `0.2`,
  `0.3`, `42`). Operators can now reach paper-faithful sparsity from YAML.
- **Config-driven synthetic sanity bound.** `synthetic.sanity_failure_rate`
  (default `0.2`) replaces the hardcoded warn-only failure-rate threshold in
  `forgelm --generate-data`; it is independent of `min_success_rate`, which
  still gates the exit code.

### Changed

- **Config validation hardened.** `distributed.strategy` is now a
  `Literal["deepspeed", "fsdp"]` (an unsupported value such as `horovod`
  used to validate and then silently run single-GPU). `data.mix_ratio`
  now rejects non-finite weights (NaN / inf) and must carry exactly one
  weight per dataset (`1 primary + len(extra_datasets)`); a length
  mismatch raised no config error and silently fell back to uniform
  mixing at runtime. Both now fail fast at config time (exit 1).

### Deprecated

- **`training.sample_packing`** is now a deprecated alias for
  `training.packing`. It was previously a documented-but-unconsumed field
  (a silent no-op); it now forwards to `packing` with a
  `DeprecationWarning` so the documented behaviour actually fires. Use
  `packing` instead — `sample_packing` is removed in **v0.9.0**. See
  [docs/standards/release.md](docs/standards/release.md#deprecation-cadence).

### Removed

- **`evaluation.staging_ttl_days`** (deprecated in v0.7.0) is removed. Use the
  canonical `retention.staging_ttl_days`; `EvaluationConfig` is `extra="forbid"`,
  so the legacy key now raises a validation error instead of forwarding.
  > **Errata (2026-07-19):** "deprecated in v0.7.0" above is wrong and is left
  > in place because released entries are not rewritten. The field was actually
  > deprecated in **v0.5.5**, whose entry reads "Removal scheduled for v0.7.0" —
  > the *scheduled-removal* version was misread as the *deprecation* version.
  > Counting only true MINOR (Y-digit) releases per
  > `docs/standards/release.md`'s Versioning table — a patch tag such as
  > v0.5.5 anchors to its v0.5 minor line and adds no separate hop — the real
  > warning window was three minors (v0.5.5 → v0.6.0 → v0.7.0 → v0.8.0), not
  > one. The same misreading applies to the `--data-audit PATH` alias in the
  > bullet below: it was deprecated in **v0.5.0** ("Removal targeted no
  > earlier than v0.7.0"), also a three-minor window (v0.5.0 → v0.6.0 →
  > v0.7.0 → v0.8.0) under the same count, since both fields' deprecations
  > anchor within the same v0.5 minor line. Neither correction changes what
  > v0.8.0 shipped — only the history it cites.
- **`forgelm --data-audit PATH`** CLI flag (deprecated in v0.7.0) is removed.
  Use the first-class `forgelm audit PATH` subcommand — identical behaviour and
  output. `argparse` now rejects the flag (exit 2).
- **`cli.legacy_flag_invoked`** audit event is no longer emitted (it recorded
  use of the removed `--data-audit` flag) and has been dropped from the
  audit-event catalog.

### Fixed

- **Eval artefact privacy-redaction documented.** `safety_results.json` and
  `judge_results.json` have been privacy-redacted by default since v0.7.0;
  this entry adds the previously missing CHANGELOG documentation. Raw
  `prompt` / `response` / judge `reason` strings are not persisted unless the
  opt-in flags `evaluation.safety.include_eval_samples` and
  `evaluation.llm_judge.include_eval_samples` (both default `false`) are set.
  This honours GDPR / EU AI Act Article 10 privacy-by-default — adversarial
  prompts and judge reasoning can quote sensitive content. Set the flag to
  `true` only for debugging.
- **Nightly pip-audit gate — transformers PYSEC-2025-217 / CVE-2025-14929.**
  Advisory records X-CLIP checkpoint-conversion deserialization RCE
  (CVSS AV:L/UI:R — local + user-interaction required). No fixed version in
  the `transformers<5.0.0` range. Codebase check 2026-05-24: no X-CLIP usage
  in `forgelm/`, no direct `torch.load` calls. Risk accepted in
  `tools/pip_audit_ignores.yaml`; re-evaluate each release cycle.
- **Pipeline configs with `pipeline:` + `retention.staging_ttl_days` +
  any `evaluation:` block** no longer raise a false
  `ConfigError ("Conflicting staging_ttl_days values")`. The stage-merge
  round-trip re-materialised the deprecated `staging_ttl_days=7` default as
  if the operator had written it; the dump now excludes unset defaults.

## [0.7.0] — 2026-05-14

Phase 14 (Multi-Stage Pipeline Chains) closes the "operators have to write
shell wrappers to chain SFT → DPO → GRPO" gap that's lived in the issue
queue since v0.4.  One YAML, one CLI invocation, one Annex IV manifest for
the whole chain — including auto-chained inputs, per-stage gates,
crash-safe resume, and 7 new pipeline-scoped audit events that join on a
single top-level `run_id`.  The release also lands a critical SSRF
hardening for outbound webhook / judge / synthetic destinations
(issue [#14](https://github.com/HodeTech/ForgeLM/issues/14)).
Single-stage configs reach `forgelm/trainer.py` byte-identical to
v0.6.0; the orchestrator module is never imported when no `pipeline:`
block is present.

### Added (Phase 14 — Multi-Stage Pipeline Chains)

- **`pipeline:` config block** at the root of `ForgeConfig` chains
  one or more training stages (typically SFT → DPO → GRPO) into one
  config-driven run.  New Pydantic models `PipelineStage` and
  `PipelineConfig` enforce stage-name uniqueness, `^[a-z0-9_]{1,32}$`
  identifier shape, **at least 1 stage** (`min_length=1` — single-stage
  ergonomics are still better served by omitting the `pipeline:`
  block, but the schema accepts a 1-element pipeline so an operator
  can iterate up to a multi-stage chain without re-shaping the YAML),
  and explicit-`trainer_type` audit-clarity validation per stage.  Section-wholesale inheritance:
  `model` / `lora` / `training` / `data` / `evaluation` blocks
  inherit from the root if omitted or fully replace it when set.
  `distributed` / `webhook` / `compliance` / `risk_assessment` /
  `monitoring` / `retention` / `synthetic` / `merge` / `auth` are
  root-only and rejected per-stage with `EXIT_CONFIG_ERROR (1)`.
- **`forgelm/cli/_pipeline.py` orchestrator** drives the chain
  end-to-end.  Auto-chains each stage's `model.name_or_path` to the
  previous stage's output, persists state atomically to
  `<pipeline.output_dir>/pipeline_state.json` after every transition,
  and emits 7 new audit events: `pipeline.started`,
  `pipeline.stage_started`, `pipeline.stage_completed`,
  `pipeline.stage_gated` (when a stage exits
  `EXIT_AWAITING_APPROVAL` — review-cycle F-N-1),
  `pipeline.stage_reverted`, `pipeline.force_resume` (operator-
  approved stale-config override — review-cycle F-B-2), and
  `pipeline.completed`.  Every entry's top-level `run_id` is pinned
  to the pipeline run id (review-cycle final-round F-B-1).  Existing
  `training.*` per-stage events from `ForgeTrainer` are preserved —
  pre-existing Slack / Teams dashboards filtering on `training.failure`
  keep working unchanged.
- **CLI flags** `--stage <name>` (single-stage filter for audit /
  re-run), `--resume-from <name>` (stage-boundary resume),
  `--force-resume` (stale-config-hash override), and `--input-model
  <path>` (auto-chain escape hatch, recorded with `input_source:
  cli_override` in the audit log).
- **`--dry-run` multi-stage validation** — when the config carries a
  `pipeline:` block, dry-run validates every stage's merged config +
  the chain-integrity assertion (stage N's auto-chained input is
  under stage N-1's `output_dir`).  Errors are collected before
  exiting (à la `pytest --collectonly`).
- **Pipeline Annex IV manifest** — `compliance/pipeline_manifest.json`
  at `<pipeline.output_dir>` indexes per-stage `training_manifest.json`
  files into one verifiable chain artefact, carrying
  `pipeline_run_id` + `pipeline_config_hash` (SHA-256 of the YAML
  bytes) for reproducibility.  `forgelm verify-annex-iv --pipeline
  <run_dir>` walks the index, checks chain integrity, and asserts
  every referenced per-stage manifest exists on disk.
- **Webhook notifications** — `WebhookNotifier.notify_pipeline_started`,
  `notify_pipeline_completed`, and `notify_pipeline_reverted`
  mirror the orchestrator's audit events; gated by the existing
  `notify_on_start` / `notify_on_success` / `notify_on_failure`
  config flags so operators silencing per-stage events also silence
  the pipeline-level pings.
- **Bilingual operator guide** — new `docs/guides/pipeline.md` +
  `docs/guides/pipeline-tr.md` (canonical "first pipeline" walkthrough,
  inheritance matrix, CLI semantics, Annex IV verifier flow,
  Limitations section).  `config_template.yaml` gains a commented-out
  `pipeline:` example block.

### Changed (Phase 14)

- **Backward compatibility preserved.**  A config file without a
  `pipeline:` key reaches `forgelm/trainer.py` byte-identically to
  v0.6.0; the orchestrator module is never imported.
- `compliance.py` exports four new symbols: `generate_pipeline_manifest`
  (called by the orchestrator after every transition),
  `export_pipeline_manifest` (atomic write to
  `<pipeline.output_dir>/compliance/pipeline_manifest.json`),
  `verify_pipeline_manifest(manifest: dict) -> List[str]` (in-memory
  structural + chain-integrity check, returns a list of human-readable
  violations — empty when valid), and `verify_pipeline_manifest_at_path
  (pipeline_dir: str) -> List[str]` (disk-backed wrapper used by the
  `forgelm verify-annex-iv --pipeline <dir>` CLI mode).  The shared
  validator lives in private helper `_verify_manifest_payload` so the
  in-memory and disk-bound entry points cannot drift.

### Fixed

Addresses the consolidated reviewer-pass findings from Phase 14 review-response
(PR [#53](https://github.com/HodeTech/ForgeLM/pull/53)) — 3 blocking +
4 significant + Gemini's 3 inline comments — against the initial Phase
14 merge candidate:

- **F-B-1** — `forgelm --config pipeline.yaml --dry-run` now reaches
  the pipeline orchestrator's per-stage validator instead of the
  legacy single-stage dry-run path; the `_dispatch.py` ordering was
  inverted (no-train-mode ran before the pipeline branch).
- **F-B-2** — `--force-resume` now emits a `pipeline.force_resume`
  audit event carrying `old_config_hash` + `new_config_hash` so
  compliance reviewers can distinguish an operator-approved override
  from a normal resume (was previously a WARNING log only —
  invisible in the append-only JSONL stream).
- **F-B-3** — pipeline manifest verifier now compares every chain
  stage against its **immediate** predecessor, not the most-recent
  stage with an `output_model`.  Pre-fix, a manifest where stage N-1
  failed without saving an `output_model` could appear to chain
  stage N from stage N-2, masking the broken link.
- **F-F-1 / F-G-3 (Gemini)** — auto-chain existence guard now
  checks the resolved model directory itself, not its parent — used
  to accept `./stage1/` even when `./stage1/final_model` was
  missing.
- **F-F-2** — `--input-model ""` (empty string) is normalised to
  `None` at dispatch time so it no longer slips past the truthy
  guard and silently overwrites the auto-chained model path with an
  empty string.
- **F-F-3** — `_validate_resume_state` now returns its refusal
  through the normal `run()` flow instead of `sys.exit`-ing mid-method,
  so every refusal path emits the same audit-log finalisation and
  the test surface uses one uniform `assert code == EXIT_CONFIG_ERROR`
  shape.
- **F-S-2 / F-N-2** — verifier surfaces stages still in `running`
  status on a finalised manifest as a `chain_integrity_violation`
  candidate (a tell of a crashed orchestrator).  Also `_load_state_file`
  catches `KeyError` / `TypeError` / `ValueError` / `AttributeError`
  around `_deserialise_state` so a tampered state file produces an
  actionable config error rather than a Python traceback.
- **F-N-1** — gated stages now emit a dedicated
  `pipeline.stage_gated` audit event (was `pipeline.stage_completed`
  with a `gate_decision=approval_pending` sub-field).  Lets
  dashboard / SIEM filters distinguish the gate flow on the event
  name alone.
- **F-G-1 (Gemini)** — pipeline dry-run now flags `training.output_dir`
  collisions across stages (two stages writing to the same dir would
  silently overwrite each other's checkpoints + per-stage Annex IV
  manifests).  Covers both the explicit-override and inherited-from-
  root collision cases.

### Security

- **Webhook / judge / synthetic SSRF guard — DNS-rebinding TOCTOU
  hardening (issue #14).** Pre-fix, `_is_private_destination()`
  pre-resolved the hostname and `requests.post()` then ran its own
  DNS lookup at connect time; an attacker-controlled DNS server with
  TTL=0 could return a public IP on the first lookup (passing the
  guard) and a private IP on the second (when `requests` connected),
  leaking the payload + bearer token to a private destination
  (loopback, RFC1918, or cloud IMDS at `169.254.169.254`). New
  `_resolve_safe_destination()` helper resolves the hostname exactly
  once and `safe_post` / `safe_get` rebuild the outbound URL with the
  returned public IP literal so `requests` never re-resolves. The
  original hostname is preserved via the `Host` header (and SNI for
  HTTPS) using
  `requests_toolbelt.adapters.host_header_ssl.HostHeaderSSLAdapter`
  so virtual-hosted endpoints (Slack / Teams / Discord) and
  certificate validation still work correctly. `allow_private=True`
  callers (operator-blessed internal destinations) keep the legacy
  flow so split-horizon DNS / in-cluster resolution still works.
  `requests-toolbelt>=1.0.0,<2.0.0` is now a hard dependency.

## [0.6.0] — 2026-05-11

Phase 15 (Ingestion Pipeline Reliability) Wave 1 + Wave 2. Closes the
silent-failure gap the 2026-05-11 pilot exposed across PDF / DOCX /
EPUB / TXT / MD ingestion plus the user-facing playground notebook.
Five review-absorption rounds (Gemini / CodeRabbit / Sonar / Codacy +
independent self-review) ship in the same release.

### Added (Phase 15 Wave 1)

- **`forgelm/_pypdf_normalise.py`** — Turkish glyph normalisation
  profile (`turkish` default, `none` opt-out, future-pluggable
  `--normalise-profile`) mapping the audit-measured pypdf font-fallback
  artefacts (`ø Õ ú ÷ ࡟` → `İ ı ş ğ •`) back to their correct Turkish
  characters at chunk-write time.
- **`forgelm/_script_sanity.py`** — language-aware Unicode-block sanity
  check (`tr` / `en` / `de` / `fr` / `es` / `it` / `pt`) that fires a
  WARNING + structured `script_sanity_summary` block when the out-of-
  script ratio exceeds the calibrated 1.5 % threshold. Catches both
  pypdf font corruption and TXT encoding mis-routing.
- **`forgelm ingest --language-hint LANG`** + `--script-sanity-threshold`
  + `--normalise-profile {turkish,none}` + `--no-normalise-unicode` CLI
  flags wiring Tasks 2 + 3 to the operator surface.
- **`forgelm ingest` end-of-run quality pre-signal (Task 4)** — three
  cheap row-level checks (alpha-ratio, weird-char ratio, repeated-line
  ratio) emitting `[WARN] N/M chunks below ingestion quality threshold`
  + a structured `quality_presignal` block in `notes_structured`. Opt
  out via `--no-quality-presignal`.
- **`forgelm doctor` pypdf-normalise diagnostic (`pypdf_normalise.turkish`)**
  — confirms the glyph-normalisation table loaded and round-trips
  cleanly without running a test ingest.
- **DOCX explicit header / footer extraction (Task 6)** — `_extract_docx`
  reads `doc.sections[i].header.paragraphs` + `.footer.paragraphs` and
  subtracts those lines from the body before chunking.
- **EPUB spine-order + nav / cover / copyright skip (Task 7)** —
  `_extract_epub` iterates `book.spine` (reading order) instead of
  file order; default skip-list (`nav`, `cover`, `copyright`,
  `colophon`, `titlepage`, `frontmatter`) opt-out via
  `--epub-no-skip-frontmatter`.
- **TXT UTF-8 BOM strip + MD YAML frontmatter detection (Task 8)** —
  `_extract_text` / new `_extract_markdown` strip the BOM via
  `encoding="utf-8-sig"` and detect `---\n...\n---\n` YAML frontmatter;
  `--keep-md-frontmatter` opts back in.
- **`forgelm/ingestion.strip_paragraph_packed_headers`** — second-pass
  dedup against paragraph-packed text catches header lines that
  survived the page-level pass.
- **`tests/test_ingestion_reliability.py`** — 36 regression tests
  locking the Wave 1 + Wave 2 behaviour across PDF / DOCX / EPUB /
  TXT / MD fixtures.

### Added (Phase 15 Wave 2)

- **`forgelm ingest --strip-pattern REGEX` (Task 11)** — operator-
  controlled regex stripping with ReDoS guard. Patterns are
  structurally validated at CLI-parse time (rejects nested unbounded
  quantifiers like `(a+)+b` and `.*?` + back-reference under DOTALL
  per the SonarCloud `python:S5852` shape rule), wrapped in a 5-second
  per-pattern SIGALRM budget on POSIX. Opt out of the timeout via
  `--strip-pattern-no-timeout`.
- **`forgelm ingest --page-range START-END` (Task 12)** — restrict
  PDF extraction to a contiguous page slice (1-indexed inclusive).
  Validation failures (`start < 1`, `start > end`,
  `start > page_count`) abort the run with `EXIT_CONFIG_ERROR` via a
  new `IngestParameterError(ValueError)` that bypasses the per-file
  soft-fail catch.
- **PDF front-matter / back-matter heuristic (Task 13, default ON)** —
  three-signal heuristic (alpha < 0.45 + underscore > 0.10 + ≥ 5
  inline page-number matches) drops up to 12 leading + 12 trailing
  pages and emits a WARNING + `frontmatter_pages_dropped` field in
  `notes_structured`. Opt out via `--keep-frontmatter`.
- **`forgelm ingest --strip-urls {keep,mask,strip}` (Task 14)** —
  detected URLs are masked with `[URL]`, stripped outright, or kept
  (default). Independent of `--all-mask` (URL handling is a
  content-shape decision, not a GDPR redaction).
- **PDF multi-column layout warning (Task 15)** — samples the first
  three pages' Tj-text positions via pypdf's `visitor_text` callback,
  fires a WARNING when a > 30 %-of-page-width two-cluster gap is
  detected. No fix attempt — operator switches strategy / pre-
  processes externally.

### Changed (Phase 15)

- **`forgelm audit --quality-filter` is now default-ON in v0.6.0**
  (Task 5). Pre-v0.6.0 invocations with explicit `--quality-filter`
  keep identical behaviour. Operators wanting the pre-v0.6.0 opt-in
  semantics pass the new `--no-quality-filter` companion flag.
- **`_strip_repeating_page_lines` window-based dedup (Task 1)** —
  replaces the pre-Phase-15 outermost-row-only iteration. Catches
  variable-outer-line + constant-deeper-line corpora (the audit §1.1
  trap) by inspecting the top-3 / bottom-3 rows per page on every
  pass. A second pass runs after paragraph packing to mop up survivor
  headers.
- **`notebooks/ingestion_playground.ipynb` (Task 9)** — Cell 5 exposes
  `CHUNK_TOKENS` + `TOKENIZER` + `LANGUAGE_HINT` knobs; Cell 8 markdown
  explains the new quality-filter checks; Cell 9 passes
  `--quality-filter` explicitly for forward-compat across v0.5 → v0.6;
  Cell 10 pretty-prints the `quality_summary` block alongside the
  existing PII / secrets / near-duplicate / language sections.
- **`IngestionResult` schema** — additive Phase 15 fields:
  `pdf_paragraph_packed_lines_stripped`, `script_sanity_triggered`,
  `strip_pattern_substitutions`, `urls_handled`,
  `frontmatter_pages_dropped`. No pre-Phase-15 key was renamed.

### Fixed (Phase 15 review-absorption — 5 rounds)

Five review-absorption rounds (Gemini + CodeRabbit + Sonar + Codacy +
independent self-review) shipped in the same release. Highlights:

- **`forgelm/cli/_training.py::_preflight_numpy_torch_abi`** — any
  unexpected exception from the underlying probe (corrupted torch
  install where `torch.__version__` raises `AttributeError`, etc.)
  is now caught and converted into a structured
  `abi_preflight_crashed` JSON envelope. Previously the raw Python
  traceback would pre-empt the `--output-format json` contract that
  every other CLI failure path honours. Exit code stays
  `EXIT_TRAINING_ERROR` (= 2), matching the broken-ABI verdict so
  CI/CD branching doesn't need to distinguish "ABI bad" from "ABI
  probe died".
- **`forgelm ingest --language-hint`** — a hint outside
  `forgelm._script_sanity.SUPPORTED_LANGUAGES` (e.g. `zh`) now
  triggers a WARNING at CLI dispatch time naming the supported codes
  instead of silently no-opping the script-sanity layer. Round-5
  self-review C-B finding.
- **EPUB skip-list whole-token match** — `recovery.xhtml` no longer
  matches `cover` via substring; `_epub_item_matches_skip` splits
  filenames + EPUB-3 manifest properties on path/extension/separator
  characters and matches whole tokens. Skipped items emit a WARNING
  naming the affected files. Round-1 C-1 + round-3 S-C findings.
- **Default `normalise_profile`** flipped from `"turkish"` to
  `"none"`; CLI dispatcher + library `ingest_path` both auto-derive
  `"turkish"` only when `--language-hint tr` is set. Prevents silent
  rewrites of legitimate non-Turkish letters (Norwegian `ø`,
  Estonian `Õ`, math `÷`). Round-1 C-2 finding.
- **ReDoS validator** caught escape-shape variants (`(\w+)+x`,
  `(\d+)+x`, `(\s+)+x`, `(\w+\s+)+x`) the pre-round-1 backward-walk
  validator skipped, and now clamps the per-pattern SIGALRM to
  `min(timeout_s, previous_remaining)` so nested calls cannot
  extend an outer caller's deadline. Round-1 S-1 + round-2 alarm
  clamp findings.
- **Operator-controlled-string injection vector** in
  `IngestParameterError` / decrypt-fail / `Could not open PDF`
  messages — paths now repr-escape via `{path!r}` so ANSI escape
  sequences / control chars / embedded quotes cannot leak into the
  rendered error. Round-4 C-A finding.
- **Quality-presignal false positive on clean small corpora** —
  chunks below 80 non-whitespace characters skip the alpha-ratio
  check so a 5-paragraph 41-char TXT no longer emits
  `[WARN] 1/1 chunks below ingestion quality threshold`. Round-4
  C-B finding.
- **Front-matter heuristic** dot-leader detection
  (`r"[._]{3,}"`), denominator parity with `alpha_ratio` (non-
  whitespace chars), and alpha threshold tightened 0.45 → 0.30.
  Catches dotted-leader ToCs the pre-round-3 underscore-only count
  missed; protects realistic form templates via the 3-signal AND
  filter. Round-3 + round-4 + round-5 findings.

### Fixed (other — earlier review absorption)

- **`forgelm/cli/_training.py::_preflight_numpy_torch_abi`** — any
  unexpected exception from the underlying probe (corrupted torch
  install where `torch.__version__` raises `AttributeError`, etc.)
  is now caught and converted into a structured
  `abi_preflight_crashed` JSON envelope. Previously the raw Python
  traceback would pre-empt the `--output-format json` contract that
  every other CLI failure path honours. Exit code stays
  `EXIT_TRAINING_ERROR` (= 2), matching the broken-ABI verdict so
  CI/CD branching doesn't need to distinguish "ABI bad" from "ABI
  probe died".
- **`CLAUDE.md`** — exit-code contract was stated as `0/1/2/3/4`,
  but the canonical table in `docs/standards/error-handling.md`
  documented `0/1/2/3/4/5` since v0.5.5 (Phase 22 added
  `EXIT_WIZARD_CANCELLED = 5`). CLAUDE.md now matches the actual
  contract — the standard was right, the agent guidance was stale.
- **`docs/roadmap/completed-phases.md::Phase 12` summary** — Tier 1
  status line claimed a `[ingestion-secrets]` extra "via
  `detect-secrets` with regex fallback", but that extra was never
  published; only `[ingestion-pii-ml]` exists in `pyproject.toml`'s
  extras surface. Reworded to record the historical plan accurately
  ("`detect-secrets` integration was originally planned as a
  `[ingestion-secrets]` extra but deferred — only
  `[ingestion-pii-ml]` ultimately shipped"). Pure docs accuracy fix.

## [0.5.7] — 2026-05-11

Patch release on top of `v0.5.6`. Two production blockers and one UX
gap that bit Intel Mac operators:

1. **SFT trainer `TypeError`** on modern `trl` (0.13 + 1.x). trl 0.13
   renamed `SFTConfig.max_seq_length` → `max_length` and removed the
   old kwarg; v0.5.6 was still passing `max_seq_length` unconditionally,
   so `forgelm --config <yaml>` crashed with
   `TypeError: SFTConfig.__init__() got an unexpected keyword argument
   'max_seq_length'` on any environment that pulled a current trl
   wheel (notably the Colab default `pip install forgelm` path).
2. **NumPy 2.x ↔ torch 2.2 binary-ABI mismatch** exposed by the v0.5.6
   Intel Mac torch revert. With `torch>=2.2.0` as the floor and no
   upper-pin on numpy, pip resolved `numpy>=2` on Intel Mac (x86_64)
   hosts; torch 2.2 was compiled against NumPy 1.x and silently
   degraded its numpy bridge with `_ARRAY_API not found`.
3. **Operator UX:** the cryptic `NameError: name '_C' is not defined`
   that surfaced from the ABI mismatch would previously bite
   mid-training with no actionable hint. v0.5.7 ships a doctor probe
   and a training-pipeline preflight so the operator gets a single-line
   `pip install 'numpy<2'` remediation instead.

Release engineering: this patch absorbs five review rounds against
PRs #44 / #45 — the SFT + ABI fixes are listed under **Fixed** below;
the UX additions (preflight + doctor probe + JSON-envelope contract +
shared `_abi_check` helper) are under **Added**; pure test/CI
hardening (the `sys.modules` pollution cascade fix, the new CI guard,
pytest-randomly adoption) is under **Internal**.

### Added

- **`forgelm doctor` — `numpy.torch_abi` probe.** New diagnostic
  inspects torch + numpy major-version pairing and emits a `fail`
  with a `pip install 'numpy<2'` remediation hint when a torch < 2.3
  install is paired with NumPy ≥ 2 (the canonical Intel Mac install
  failure mode). Probe imports torch / numpy with warnings suppressed
  so the probe itself does not emit a duplicate Python `UserWarning`
  (the underlying torch C++ `fprintf(stderr, "_ARRAY_API not found",
  …)` message is outside Python's warnings machinery and cannot be
  intercepted from a long-lived CLI process — documented honestly in
  the probe docstring). Surfaces in the
  `forgelm doctor --output-format json` envelope so CI consumers
  catch the issue programmatically.
- **Training-pipeline ABI preflight (`forgelm/cli/_training.py::_preflight_numpy_torch_abi`).**
  A shared helper at `forgelm/cli/_abi_check.py` runs the same probe
  as `forgelm doctor`, and `_run_training_pipeline` now calls it
  before importing the heavy stack. An operator who hits a residual
  Intel Mac NumPy 2 mismatch (env drift, out-of-band
  `pip install -U numpy`) now sees a single-line error with the
  exact remediation command instead of a cryptic
  `NameError: name '_C' is not defined` from torch mid-training. The
  PEP 508 marker in `pyproject.toml` is the primary fix for fresh
  installs; the preflight is the second line of defense for drifted
  environments. Any unexpected crash from the probe itself converts
  into a structured `abi_preflight_crashed` JSON envelope (same
  `EXIT_TRAINING_ERROR` = 2 class) rather than a raw Python
  traceback. The two envelope shapes (`numpy_torch_abi_mismatch` +
  `abi_preflight_crashed`) are documented in
  [`docs/usermanuals/{en,tr}/reference/json-output.md`](docs/usermanuals/en/reference/json-output.md)
  for CI consumers.

### Fixed

- **`forgelm/trainer.py::_get_training_args_for_type`** — the SFT
  branch now inspects `SFTConfig.__init__`'s signature at runtime and
  picks the correct sequence-length-cap parameter name. trl 0.13+
  (including the 1.x line) receives `max_length`; trl 0.12.x — still
  within the `pyproject.toml` floor `trl>=0.12.0,<2.0.0` — keeps
  receiving `max_seq_length`. If a future trl release exposes
  neither name as a discoverable parameter, the branch now raises
  `ValueError` (not warn-and-continue) listing the actually-detected
  parameters — silently dropping an explicit `model.max_length` YAML
  setting would violate `docs/standards/error-handling.md` "no silent
  failures". No code change for DPO / SimPO / KTO / ORPO / GRPO
  trainers (their `*Config` parameters were not affected by the
  rename).
- **`pyproject.toml` — Intel Mac (x86_64) NumPy 2 ABI mismatch.**
  Added `numpy<2; sys_platform == 'darwin' and platform_machine ==
  'x86_64'` so pip's resolver caps numpy at the 1.x line on the only
  platform that is wheel-locked to torch 2.2.x. Other platforms keep
  numpy unconstrained — torch 2.3+ on Linux / Apple Silicon / Windows
  is binary-compatible with NumPy 2.x. Affected `pip install forgelm`
  users on Intel Mac since the v0.5.6 torch revert.

### Internal

- **Test-pollution cascade fix.** Three pre-existing tests
  (`tests/test_doctor.py` × 2, `tests/test_quickstart_compat.py` × 1)
  popped `sys.modules['torch']` / `sys.modules['numpy']` without
  restoring them; the stranded modules half-initialised every
  subsequent `import torch` in the pytest session (`torch._C` never
  re-bound), causing every downstream `from trl import SFTConfig` and
  a long tail of merging / moe / quickstart / wizard / judge / safety
  tests to fail in the full suite even though they passed in isolation
  (35 spurious failures observed on the Intel Mac dev box). Swapped
  for `monkeypatch.delitem` so the modules are auto-restored on test
  teardown.
- **New CI guard `tools/check_no_unguarded_sys_modules_pop.py`** —
  fails on any `sys.modules.pop("torch"|"numpy"|"trl"|…)` or
  `del sys.modules["…"]` without `monkeypatch.delitem`. Wired into
  the self-review gauntlet (`CLAUDE.md`) and `.github/workflows/ci.yml`
  so the regression class becomes impossible to silently re-introduce.
- **`pytest-randomly>=3.15.0,<4.0.0` added to `[dev]` extra.**
  Shuffles test order per session so order-dependent bugs (the
  failure mode above) surface in CI instead of waiting for a Python
  micro-bump or runner change to flip collection. Reproduce a
  specific shuffle with `pytest --randomly-seed=<n>`.
- **`forgelm/cli/_abi_check.py`** — new shared helper module carries
  the ABI verdict + remediation logic. Both the doctor probe and the
  training preflight consume it as a single source of truth. Includes
  a `(0, 0)` parse-fallback short-circuit so corporate forks with
  non-semver torch / numpy tags can't trip a false-positive
  `ABI_BROKEN`.
- **`forgelm/cli/subcommands/_doctor.py::_check_numpy_torch_abi`**
  refactored to call into the shared helper rather than duplicating
  the version-parsing + threshold logic. Exhaustive-enum guard at the
  end now `raise`s `RuntimeError` instead of `assert`-ing, so
  `python -O` doesn't strip the check.
- **Documentation hygiene.** `CLAUDE.md` exit-code contract line now
  reads `0/1/2/3/4/5` to match the canonical table in
  `docs/standards/error-handling.md` (the standard had documented
  `EXIT_WIZARD_CANCELLED = 5` since v0.5.5; the agent guidance was
  stale). `docs/roadmap/completed-phases.md` Phase 12 Tier 1 status
  line no longer advertises a `[ingestion-secrets]` extra that was
  never published — only `[ingestion-pii-ml]` exists. One leftover
  `(Faz 12)` Turkish residue in the v0.5.2 CHANGELOG entry rewritten
  to `(Phase 12)`.

## [0.5.6] — 2026-05-10

**Status:** Released to PyPI 2026-05-10 via the cross-OS publish
workflow ([`.github/workflows/publish.yml`](.github/workflows/publish.yml))
which gates PyPI publish on 12 wheel-install matrix combos
(3 OS × 4 Python). GitHub Release:
[v0.5.6](https://github.com/HodeTech/ForgeLM/releases/tag/v0.5.6).

Patch release. Reverts the v0.5.5 `torch>=2.3.0` minimum back to
`torch>=2.2.0` to restore Intel Mac (x86_64) installability. The
`torch>=2.3` floor in v0.5.5 was inaccurate — no v2.3-specific
PyTorch API is referenced in production code. The single citation
in `tests/test_grpo_reward.py` is a comment explaining the test's
graceful skip when `trl.GRPOTrainer`'s lazy import fails on a
torch/trl mismatch; the skip pattern
(`pytest.mark.skipif(not grpo_patchable, ...)`) already handles that
case across torch 2.2 + 2.3. Production FSDP usage in
`forgelm/trainer.py` delegates string options to
`transformers.TrainingArguments` and never imports
`torch.distributed.fsdp.FSDPModule` directly.

### Changed

- **`pyproject.toml`** — `torch>=2.3.0,<3.0.0` →
  `torch>=2.2.0,<3.0.0`. Other dependencies untouched.

### Fixed

- **`pip install forgelm` silently downgrading to v0.5.0 on Intel
  Mac (x86_64) hosts.** The PyTorch Foundation stopped publishing
  `torch>=2.3` wheels for Intel Mac (only Apple Silicon / Linux /
  Windows wheels exist for 2.3+). v0.5.5's `torch>=2.3.0` requirement
  caused pip's resolver to silently fall back to v0.5.0 (which
  pinned `torch>=2.1.0`) for those hosts — `pip install forgelm`
  appeared to succeed but installed a year-old version with no
  GDPR / Library API / operational subcommands. v0.5.6 lowers the
  floor to `torch>=2.2.0`, the highest minor available across all
  supported platforms (including Intel Mac × Python 3.11). Existing
  v0.5.0 users on Intel Mac can now upgrade with `pip install -U
  forgelm` and reach v0.5.6 cleanly.

## [0.5.5] — 2026-05-10

"Closure Cycle Bundle + Phase 22 Wizard + Site Documentation Sweep."
v0.5.5 consolidates the closure-cycle backlog (Library API, GDPR
right-of-erasure + right-of-access, ISO 27001 / SOC 2 alignment,
operational subcommands, supply-chain security, cross-OS release
matrix) and three follow-up PRs that landed before the PyPI tag was
cut (Phase 22 CLI wizard modernisation, site documentation correction
sweep, release-prep + nightly pip-audit gate fix).

### Added

#### Library API

- **Stable Python entry points for every CLI subcommand.**
  `forgelm/__init__.py` is now a strict lazy-import facade with
  PEP 562 `__getattr__`, `__dir__` enumeration of the public surface,
  a `TYPE_CHECKING` block for `mypy --strict` consumers, and per-symbol
  caching in `globals()`. `import forgelm` does not pull `torch`;
  resolved values are zero-cost on the second access. New
  `forgelm/_version.py` separates `__version__` (package) from
  `__api_version__` (Python library API contract); `forgelm/py.typed`
  marker shipped via `pyproject.toml` package-data. The `__all__`
  enumerates configuration (`load_config`, `ForgeConfig`, `ConfigError`),
  training (`ForgeTrainer`, `TrainResult`), data (`prepare_dataset`,
  `get_model_and_tokenizer`, `audit_dataset`, `AuditReport`), PII /
  secrets / dedup utilities (`detect_pii`, `mask_pii`, `detect_secrets`,
  `mask_secrets`, `compute_simhash`), compliance (`AuditLogger`,
  `verify_audit_log`, `VerifyResult`), verification toolbelt
  (`verify_annex_iv_artifact`, `VerifyAnnexIVResult`, `verify_gguf`,
  `VerifyGgufResult`), webhooks (`WebhookNotifier`), and auxiliary
  (`setup_authentication`, `manage_checkpoints`, `run_benchmark`,
  `BenchmarkResult`, `SyntheticDataGenerator`).

#### GDPR right-of-erasure (Article 17)

- **`forgelm purge`** — three-mode dispatcher:
  - `--row-id <id> --corpus <path>` — atomic JSONL row erasure with
    a SHA-256(salt + id) hashed audit event. A per-output-dir salt at
    `<output_dir>/.forgelm_audit_salt` (mode `0600`) persists
    regardless of the `FORGELM_AUDIT_SECRET` toggle; env-var-set
    invocations XOR the persistent salt with the secret prefix and
    record `salt_source="env_var"` so a salt-source change between
    invocations is detectable in the chain.
  - `--run-id <id> --kind {staging,artefacts}` — run-scoped artefact
    erasure (staging directory or compliance bundle).
  - `--check-policy` — read-only retention-policy violation report
    (always exits 0; report-not-gate by design).
- **`RetentionConfig`** — new Pydantic block with four horizons
  (`audit_log_retention_days=1825`, `staging_ttl_days=7`,
  `ephemeral_artefact_retention_days=90`,
  `raw_documents_retention_days=90`) and
  `enforce ∈ {log_only, warn_on_excess, block_on_excess}`.
  `EvaluationConfig.staging_ttl_days` alias-forwards to
  `retention.staging_ttl_days` with a single `DeprecationWarning`;
  conflicting values raise `ConfigError`. Removal scheduled for v0.7.0.
- **Six new `data.erasure_*` audit events** catalogued bilingually
  (three core erasure events + three operator-warning events),
  plus an operator guide at
  [`docs/guides/gdpr_erasure.md`](docs/guides/gdpr_erasure.md)
  (+ TR mirror).

#### GDPR right-of-access (Article 15)

- **`forgelm reverse-pii --query VALUE [--type literal|email|phone|tr_id|us_ssn|iban|credit_card|custom] [--salt-source per_dir|env_var] JSONL_GLOB...`** —
  walks JSONL corpora and reports every line where the supplied
  identifier appears. Two scan modes: *plaintext residual* (mask-leak
  detection) and *hash-mask* (reuses `forgelm purge`'s per-output-dir
  salt to re-derive the digest, so a purge → reverse-pii cycle for the
  same subject yields matching digests). Snippets are centred on the
  matched span and capped at 160 chars.
- **Default `--type` is `literal`** — a stray
  `--query alice@example.com` matches the literal e-mail substring,
  not the regex shape `alice@exampleXcom`. Operators wanting raw regex
  pass `--type custom` explicitly; on POSIX a 30s SIGALRM budget guards
  against ReDoS hangs.
- **New `data.access_request_query` audit event** with the identifier
  salted-and-hashed before emission so Article 15 access requests
  don't themselves leak the subject's data into the audit log.

#### Operational subcommands

- **`forgelm doctor`** — environment pre-flight: Python version,
  torch + CUDA, GPU inventory, every optional extra advertised in
  `pyproject.toml`, HuggingFace Hub reachability, workspace disk
  space, and the `FORGELM_OPERATOR` audit-identity hint. Tabular text
  or JSON envelope (`--output-format json`). `--offline` skips the
  HF Hub network probe and inspects the local cache. Honours
  `HF_HUB_OFFLINE=1` / `TRANSFORMERS_OFFLINE=1` /
  `HF_DATASETS_OFFLINE=1` implicitly. Heavy deps lazy-imported inside
  individual probes so `forgelm doctor` runs on a brand-new machine.
- **`forgelm cache-models --model M [--safety S] [--output DIR]`** —
  populates the HF cache via `huggingface_hub.snapshot_download`.
  Cache resolution: `--output > HF_HUB_CACHE > HF_HOME/hub > ~/.cache/huggingface/hub`.
  Repeatable `--model` flag.
- **`forgelm cache-tasks --tasks CSV`** — populates the lm-eval task
  dataset cache via `lm_eval.tasks.get_task_dict` +
  `dataset.download_and_prepare()`. Requires `[eval]` extra.
- **`forgelm safety-eval --model <path> {--probes <jsonl> | --default-probes}`** —
  standalone counterpart to the training-time safety gate. Wraps
  `forgelm.safety.run_safety_evaluation`; supports HF + GGUF models
  (GGUF requires `[export]` extra). Bundled probe set at
  `forgelm/safety_prompts/default_probes.jsonl` (50 prompts × 14
  harm categories — controlled-substances, jailbreak, hate-speech,
  self-harm, csam, etc.).
- **`forgelm verify-annex-iv <path>`** — verifies an EU AI Act
  Annex IV artifact JSON: nine required field categories per
  Annex IV §1-9 + manifest-hash recompute (canonical-JSON SHA-256
  against `metadata.manifest_hash`).
  `verify_annex_iv_artifact(path) → VerifyAnnexIVResult` exposed as
  a public library function.
- **`forgelm verify-gguf <path>`** — three-layer GGUF integrity check:
  4-byte `GGUF` magic header, optional metadata parse via the `gguf`
  package, optional SHA-256 sidecar (`<path>.sha256`) comparison.
  `verify_gguf(path) → VerifyGgufResult` exposed as a public library
  function.
- **`forgelm verify-audit`** — verifies the integrity of the
  append-only audit log chain (HMAC + SHA-256). `verify_audit_log` is
  a public library function.
- **`forgelm approvals --pending`** lists every run whose audit log
  carries a `human_approval.required` event without a matching
  terminal decision; **`--show RUN_ID`** prints the full
  approval-gate audit chain plus the on-disk staging directory layout.
  Tabular text or JSON envelope. Defence-in-depth path-traversal
  guard rejects tampered staging paths outside the output directory;
  latest-wins semantics correctly classify re-staged runs as pending.
- **`forgelm approve <run_id>` / `forgelm reject <run_id>`** — manage
  the Article 14 human-approval gate. `approve` atomically renames
  `final_model.staging/` → `final_model/` (with a `shutil.move`
  fallback on cross-device output mounts), emits
  `human_approval.granted`, and fires a `notify_success` webhook.
  `reject` records `human_approval.rejected` and leaves the staging
  directory in place for forensic review. Approver identity resolves
  via `FORGELM_OPERATOR` → `getpass.getuser()` → `"anonymous"`.

#### CLI wizard parity-with-web

`forgelm --wizard` now covers the same nine user-visible stages as
the in-browser wizard at `forgelm.dev/quickstart` and produces the
same generated YAML. Internal step IDs and the trainer-vs-model order
may differ between the two surfaces by design — both flows reach an
equivalent `ForgeConfig` shape; the divergence is documented inline at
`forgelm/wizard/_orchestrator.py`. The CLI flow is welcome →
use-case → model → strategy → trainer → dataset → training-params →
compliance → evaluation, with `back` / `b` to navigate backwards and
`reset` / `r` to clear in-memory state. The wizard module was split
into a sub-package (`forgelm/wizard/`) for orchestrator / state /
collectors / BYOD / IO concerns.

- **Idempotent re-run:** `forgelm --wizard --wizard-start-from
  /path/to/existing.yaml` reads the YAML, validates it against
  `ForgeConfig` up front, and seeds the wizard with the loaded values
  so a bare Enter at every prompt keeps the operator's earlier answer.
  Save flow defaults to the same path; existing overwrite confirmation
  still fires.
- **State persistence:** snapshot at
  `$XDG_CACHE_HOME/forgelm/wizard_state.yaml` (or
  `~/.cache/forgelm/wizard_state.yaml`) saved after every completed
  step; resume on next launch when the snapshot is present and
  schema-compatible. Snapshot is `chmod 0o600` and cleared on
  successful completion. Atomic writes via temp file + `fsync` +
  `os.replace` prevent half-written state under SIGKILL / power loss.
- **Schema-driven defaults:** wizard-relevant fields in
  `forgelm/config.py` are flagged with
  `json_schema_extra={"wizard": True}`. A generator script
  (`tools/generate_wizard_defaults.py`) walks the schema and writes
  `forgelm/wizard/_defaults.json` (consumed by the CLI wizard via
  `importlib.resources`) and `site/js/wizard_defaults.js` (consumed
  by the web wizard's `defaultState()`). A CI guard
  (`tools/check_wizard_defaults_sync.py`) fails the run on schema-
  vs-shipped-JSON drift. Hardcoded fallbacks survive only when the
  JSON is missing entirely (broken pip install).
- **Trainer-specific hyperparameters:** `dpo_beta`, `simpo_beta` /
  `simpo_gamma`, `kto_beta`, `orpo_beta`, `grpo_num_generations`,
  `grpo_max_completion_length`, `grpo_reward_model` surface per
  `trainer_type`. SFT short-circuits.
- **PEFT method breadth:** the strategy step offers all four
  schema-supported `lora.method` values (`lora`, `dora`, `pissa`,
  `rslora`) plus GaLore as a separate axis. All six `galore_optim`
  Literal values surfaced, including the three `_layerwise` variants.
  RoPE scaling adds `longrope` (full 4-of-4 schema coverage).
- **Compliance depth:** `compliance` (Article 11 + Annex IV §1) plus
  optional `risk_assessment` (Article 9), `data.governance`
  (Article 10), `retention` (GDPR Article 5(1)(e) + 17), `monitoring`
  (Article 12 + 17), `evaluation.benchmark`, `evaluation.llm_judge`,
  `synthetic` blocks all configurable from the wizard.
- **High-risk auto-coercion:** `risk_classification ∈ {high-risk,
  unacceptable}` automatically enables `evaluation.safety` +
  `evaluation.require_human_approval` with a visible operator notice
  — front-stops the schema-side `F-compliance-110` `ConfigError`.
- **Webhook URL parsing with SSRF preflight:** single prompt accepts
  a literal URL or `env:VAR_NAME` reference; the URL is
  `urlparse`-validated, HTTPS recommended, and loopback / RFC1918 /
  link-local destinations rejected up front by reusing
  `forgelm._http._is_private_destination` so typos like
  `http://10.0.0.1/x` fail at config time instead of training time.
- **Configuration summary:** the wizard prints the full generated
  YAML alongside the labelled headline so the operator sees every
  block (webhook / evaluation / compliance / risk / retention /
  monitoring) without `cat`-ing the file.
- **Step-diff preview:** each completed step prints
  `+ key.path: value` / `~ key.path: before → after` so the operator
  sees exactly what changed mid-flow.
- **Beginner / expert toggle:** beginner mode prefixes each step with
  a 2-3-line tutorial paragraph; expert mode is silent.
- **Use-case integration:** the curated quickstart-template list is
  available as Step 2 of the full flow, seeding sensible defaults for
  later steps without locking anything down. Use-case keys mirror
  `forgelm/quickstart.py::TEMPLATES` exactly; `quickstart.py` is the
  single source of truth.
- **Pre-flight checklist:** GPU / VRAM / dataset / risk-tier signals
  surface before the configuration summary, calling out common
  operator errors (low-VRAM full-precision, missing local file, strict
  tier without safety eval).
- **Distinct exit code for wizard cancel:** new
  `EXIT_WIZARD_CANCELLED = 5`. Clean cancels exit `5` instead of `0`
  so CI can distinguish "wizard finished" from "wizard never produced
  output". Public exit-code surface is now `0–5`.
- **Best-effort readline:** arrow-key line editing + history on
  Linux/macOS via stdlib `readline` import; Windows unaffected.
- **Validate-on-exit:** the wizard runs `ForgeConfig.model_validate`
  on the saved YAML before declaring success. Schema rejections
  surface inline with the offending field rather than 30 minutes into
  a failed training run.
- **Overwrite confirmation + auto-suffix:** the save flow detects
  pre-existing files, asks before clobbering, and falls back to the
  next free `_2.yaml` / `_3.yaml` slot when declined.
- **Non-tty stdin refusal:** `forgelm --wizard < answers.txt` used to
  silently produce empty configs; the wizard now refuses to launch on
  a non-tty stdin and points the operator at
  `forgelm quickstart <template>` for deterministic scripted
  generation.

#### ISO 27001 / SOC 2 Type II alignment

- **93-control deployer cookbook** at
  [`docs/guides/iso_soc2_deployer_guide.md`](docs/guides/iso_soc2_deployer_guide.md)
  (+ TR mirror) — every ISO 27001:2022 Annex A control and every
  SOC 2 Trust Services Criterion mapped to the ForgeLM feature that
  produces audit evidence. Coverage: FL (full) 11 / FL-helps 48 /
  OOS 34. 10-row Decision Log + 10-question deployer FAQ.
- **4 new QMS docs (EN + TR mirrors):** `encryption_at_rest.md`
  (substrate-side encryption guidance per ForgeLM artefact class),
  `access_control.md` (operator identity contract;
  `FORGELM_OPERATOR` form recommendations; `FORGELM_AUDIT_SECRET`
  rotation cadence), `risk_treatment_plan.md` (12-row ISO 27005 risk
  register), `statement_of_applicability.md` (93-control SoA matrix).
  Plus expansions of `sop_incident_response.md` (security incidents:
  audit-chain integrity violation, credential leak, supply-chain CVE,
  webhook compromise, GDPR Art. 15/17 DSAR playbooks) and
  `sop_change_management.md` (CI gates as formal change-control
  mechanism).
- **2 new reference tables:** `iso27001_control_mapping.md` (93
  controls × ForgeLM evidence) + `soc2_trust_criteria_mapping.md`
  (each with TR mirror).
- **`README.md`** — new "ISO 27001 / SOC 2 Type II Alignment" bullet
  under Enterprise & MLOps with explicit "alignment, not certified"
  framing.

#### Supply-chain security

- **`pyproject.toml`** — new `[security]` optional extra (`pip-audit`,
  `bandit[toml]`).
- **`tools/check_pip_audit.py`** + **`tools/check_bandit.py`** —
  JSON severity gates; HIGH / CRITICAL → exit 1, MEDIUM →
  `::warning::`, LOW silent.
- **`.github/workflows/ci.yml`** — `bandit` step on `forgelm/`
  (production code only; tests/ excluded).
- **`.github/workflows/nightly.yml`** — new `supply-chain-security`
  job running `pip-audit` + `bandit` daily at 03:00 UTC.
- **`tools/generate_sbom.py`** — stdlib-only CycloneDX 1.5 emitter;
  called from each `cross-os-tests` matrix combo to produce a
  per-OS-and-Python SBOM artefact.

#### Cross-OS release-tag matrix

- **`.github/workflows/publish.yml`** — tag-driven
  `build → cross-os-tests → publish` chain over 3 OS × 4 Python = 12
  combinations; packaged-wheel install (not editable); SBOM artifact
  upload per combo; OIDC trusted publishing.

#### Pre-commit hooks (optional)

- **`.pre-commit-config.yaml`** — opt-in local hooks (`ruff`,
  `ruff-format`, `gitleaks`, trailing-whitespace, end-of-file-fixer,
  check-yaml/-toml, check-merge-conflict). CI keeps enforcing the
  same checks; pre-commit is ergonomic optimization, not a duplicate
  enforcement boundary.

#### `forgelm audit --workers N`

- **Split-level parallelism** for the audit pipeline. `--workers N`
  (default 1) runs each split in its own `multiprocessing.Pool`
  worker (spawn-method, pinned in code). Speed-up scales with the
  number of splits — `--workers 3` on a `train` / `validation` /
  `test` corpus typically yields a near-linear speed-up. Single-split
  corpora ignore values >1.
- **Determinism contract pinned by tests.** The merge step that
  builds the final report stays single-threaded so
  `data_audit_report.json` is byte-identical across worker counts
  (only `generated_at` differs as expected — stripped textually
  before SHA-256 comparison).
- **CLI exposure.** `forgelm audit --workers N` registered on the
  audit subparser with a new `_positive_int` argparse type validator
  (rejects 0 / negatives at parse time).

#### Documentation

- **50 new doc files** across guides + reference + usermanuals
  (EN + TR mirrors). Highlights:
  - 11 new reference docs covering every shipped subcommand
    (`verify_audit`, `verify_annex_iv_subcommand`,
    `verify_gguf_subcommand`, `purge_subcommand`,
    `reverse_pii_subcommand`, `approve_subcommand`,
    `approvals_subcommand`, `doctor_subcommand`, `cache_subcommands`,
    `safety_eval_subcommand`, `library_api_reference`).
  - 5 new guides: `getting-started.md` (v0.5.5 onboarding canonical,
    opens with `forgelm doctor`); `air_gap_deployment.md` (deep
    deployer cookbook for `cache-models` / `cache-tasks`);
    `human_approval_gate.md`; `library_api.md`; `performance.md`.
  - 9 new usermanual pages:
    `compliance/{verify-audit, annex-iv, gdpr-erasure, human-approval-gate}`,
    `deployment/verify-gguf`, `operations/{iso-soc2-deployer, supply-chain}`,
    `reference/{library-api, performance}`.
- **Bilingual TR mirror sweep** across `docs/qms/*.md` (10 mirrors)
  and 4 doc pairs brought to structural parity with their EN
  originals.
- **i18n parity:** German / French / Spanish / Chinese now match
  English and Turkish at 731 keys each (was 689). The 168
  previously-untranslated strings cover the regulator-facing surfaces
  (`compliance.gdpr15.*`, `compliance.gdpr17.*`, `compliance.iso.*`,
  `features.gov.*`, `features.eval.safetyeval.*`, `features.ent.*`).
- **`docs/reference/audit_event_catalog.md` (+ TR mirror)** —
  comprehensive event vocabulary with payload schemas.
- **`docs/standards/release.md`** — "Deprecation cadence" section.

#### Doc CI guards

- **`tools/check_bilingual_parity.py --strict`** — replaces the
  inline H2-only check in `ci.yml` with extended H2 + H3 + H4
  structural diff. Detects missing sections, depth changes, reorders.
  Pair registry now 40.
- **`tools/check_anchor_resolution.py --strict`** — markdown link
  resolution guard; baseline cleanup brought broken anchors from
  36 → 0.
- **`tools/check_cli_help_consistency.py --strict`** — discovers the
  live parser surface by spawning `forgelm <subcommand> --help` for
  every shipped subcommand; walks docs / README for fenced
  bash/shell `forgelm` invocations; reports drift classes
  (subcommand not in parser; flag not in parser; flag value not in
  parser's `choices` list).
- **`tools/check_field_descriptions.py --strict forgelm/config.py`** — AST-based
  scanner of Pydantic `BaseModel` subclasses; exits 1 on any field
  missing a `description=`.
- **`tools/check_no_analysis_refs.py`** — prohibits citations to
  gitignored working-memory directories from the public tree.
- **`tools/check_wizard_defaults_sync.py`** — fails CI on schema-vs-
  shipped-JSON drift for the wizard's source-of-truth defaults.

#### HTTP discipline

- **`forgelm/_http.safe_get(url, *, headers, timeout, ..., method="GET"|"HEAD")`** —
  disciplined outbound GET / HEAD mirroring `safe_post`'s policy
  contract (scheme allowlist, SSRF guard, timeout floor, redirect
  refusal, secret-mask error path, TLS verify). Used by
  `forgelm doctor` and any future probe / telemetry / registry ping
  that needs an outbound read.
- **`forgelm/_http.safe_post`** — single boundary
  for outbound HTTP with SSRF guard, redirect refusal, scheme policy,
  timeout floor, TLS pinning, secret-mask error reasons. Migrated
  webhook + judge + synthetic call sites.
- **`.github/workflows/ci.yml`** `lint-http-discipline` step greps
  `forgelm/` for `requests.*(...)`, `urllib.request.urlopen(...)`,
  `httpx.*(...)` outside `forgelm/_http.py` and fails on any hit.

#### Article 14 staging directory

- When `evaluation.require_human_approval=true`, the trainer now
  saves the final adapters to `final_model.staging/` instead of
  writing to `final_model/` before review. The
  `human_approval.required` audit event payload now also carries
  `staging_path` and `run_id` so downstream tooling can cross-check
  the approval against the originating run.
- **`evaluation.staging_ttl_days`** config field documents the
  retention horizon for `final_model.staging/` after a
  `forgelm reject`; default 7 days. Auto-deletion enforcement is
  delegated to `forgelm purge` (GDPR right-to-erasure tooling).

#### New audit events

- **Erasure family (six events):** `data.erasure_requested`,
  `data.erasure_completed`, `data.erasure_failed`, plus three
  operator-warning events.
- **Access-request family:** `data.access_request_query`
  (Article 15) — identifier salted-and-hashed before emission.
- **Cache-population family (six events):** `cache.populate_*`.
- **Approval family:** `human_approval.required`,
  `human_approval.granted`, `human_approval.rejected`,
  `approval.required`.
- **Training lifecycle:** `training.reverted` paired with
  `WebhookNotifier.notify_reverted`.
- **Operator-error family:** `audit.classifier_load_failed`,
  `cli.legacy_flag_invoked`.

#### Other additions

- **`SafetyEvalThresholds` dataclass** bundles five Phase 9 knobs so
  `run_safety_evaluation` stays under the 13-param ceiling.
- **`tests/test_lazy_imports.py`** — regression test pinning that
  `import forgelm.trainer` / `import forgelm.model` do not eagerly
  load torch.

### Changed

- **`forgelm/cli.py` → `forgelm/cli/` package.** The ~2300-line
  monolith was split into a 24-module package (`subcommands/`,
  `_dispatch`, `_training`, `_dry_run`, `_result`, `_resume`,
  `_logging`, `_exit_codes`, etc.). The `forgelm.cli:main` entry
  point and `python -m forgelm.cli` are preserved; dispatcher uses
  late-binding facade re-resolution so test monkeypatches
  (`forgelm.cli._run_chat_cmd` etc.) keep resolving correctly.
- **`forgelm/data_audit.py` → `forgelm/data_audit/` package.** The
  3098-line monolith was split into a 14-module package (`_optional`,
  `_types`, `_pii_regex`, `_pii_ml`, `_secrets`, `_simhash`,
  `_minhash`, `_quality`, `_streaming`, `_aggregator`, `_splits`,
  `_summary`, `_croissant`, `_orchestrator`). The public
  `forgelm.data_audit.X` import surface — including the
  test-touched private helpers — is preserved by `__init__.py`
  re-exports so external callers and the test suite keep working
  without code changes.
- **Pydantic `description=` migration** — every Pydantic field across
  the config schema (19 model classes) migrated to
  `Field(default=..., description=...)` form. Operator-facing copy
  pulled from existing inline comments + variable semantics; the
  configuration reference can now be auto-generated from the schema
  in lockstep with the code.
- **Site copy now matches the live code surface.** Sweep across
  `site/*.html` and `site/js/translations.js` correcting drift that
  had accumulated against the production code:
  - **YAML demo accuracy:** the homepage hero YAML demo and the
    quickstart `verify-audit` example now use real Pydantic field
    names and accept paths that pass the live CLI check; copying any
    visible snippet and running `forgelm --config <copy> --dry-run`
    works as advertised.
  - **Compliance artefact tree:** the EU AI Act / ISO 27001 page
    redrawn against the actual on-disk layout — `compliance/`
    sub-tree (was `artifacts/`), `audit_log.jsonl` at the checkpoint
    root, `final_model/` carrying the model card + deployer
    instructions + integrity manifest. Removed phantom
    `config_snapshot.yaml` row (no code path emits it).
  - **Ghost YAML keys removed:** `compliance.config_hash` and
    `compliance.human_approval` (which `ComplianceMetadataConfig`'s
    `extra="forbid"` rejected) replaced with the canonical
    `evaluation.require_human_approval` and a description of
    `forgelm verify-audit` chain integrity.
  - **Ghost CLI flag removed:** `--model-card` no longer mentioned
    on the Article 13 evidence cell; the model card is auto-generated
    on every successful run.
  - **Stale namespace + symbol-list corrected:** Library API
    references now use `from forgelm import …` with real symbols from
    `forgelm.__all__` (`ForgeTrainer`, `audit_dataset`,
    `verify_audit_log`, `verify_annex_iv_artifact`, `mask_pii`).
  - **Wording aligned with live behaviour:** auto-revert (deletes
    artefacts + exits with `EXIT_EVAL_FAILURE = 3`, not "rolls back
    to last-good checkpoint"), exit codes (`0–5`, was `0/1/2/3/4`),
    Annex IV "nine §1-9 sections" (was "eight"),
    `forgelm safety-eval` accepted formats (`--probes` JSONL or
    `--default-probes`; outputs `safety_results.json` +
    `safety_trend.jsonl`), `forgelm verify-annex-iv` claim narrowed
    to schema completeness + `manifest_hash` (audit-chain integrity
    is the separate `verify-audit` command), `forgelm purge`
    documented as emitting `data.erasure_requested` +
    `data.erasure_completed` audit events.
  - **Wizard preset divergence:** the in-browser `USE_CASE_PRESETS`
    is now byte-equivalent to `forgelm/quickstart.py::TEMPLATES`.
    Operators who finish the wizard see the same model IDs and
    bundled-dataset paths the CLI `quickstart` would have set.
  - **JSON-LD `operatingSystem`:** all 8 pages updated from
    `"Linux, macOS"` to `"Linux, macOS, Windows"`, matching the
    README's tri-platform pitch and the PyPI
    `Operating System :: OS Independent` classifier.
  - **Mermaid disclosure:** the privacy page now lists three
    third-party requests (Google Fonts, jsDelivr / Mermaid on the
    Guide page only, Formspree on contact-form submit) in all six
    locales.
  - **Audit-event scope:** Article 12 description widened from
    "training start, eval gates, auto-revert decisions" to the real
    vocabulary (training start/end, eval gates, auto-revert
    decisions, human-approval gates, GDPR Article 17 erasure,
    Article 15 access-request queries, model export) with HMAC +
    SHA-256 chain language replacing the per-artefact SHA-256
    wording.
- **Working-memory directories cleanup.** Operator-local research,
  audit drafts, and external-repo comparisons now live in
  working-memory directories that are strictly gitignored with no
  exceptions and never appear in fresh clones. The previous
  re-include carve-outs that exposed individual files were dropped,
  the working-memory tree was untracked at the directory level, and
  every public-tree citation pointing into it was rewritten or
  removed. The rule is now codified in
  `docs/standards/documentation.md` ("Working-memory directories"),
  enforced by a new CI guard (`tools/check_no_analysis_refs.py`)
  wired into the self-review chain in `CLAUDE.md`. False positives
  (functional path filters in production code) are handled via an
  `_EXEMPT` allowlist with per-entry justification comments.
- **Minimum required `torch` bumped from 2.1.0 to 2.3.0.**
  `torch.distributed.fsdp.FSDPModule` (introduced in torch 2.3) is
  referenced by `tests/test_grpo_reward.py` and runtime GRPO paths.
- **16 broad `except Exception` sites narrowed** across
  `_streaming.py`, `trainer.py`, `safety.py`, `judge.py`,
  `compliance.py`, `ingestion.py` to specific exception classes;
  7 sites retained with `# noqa: BLE001` and rationale comments per
  `docs/standards/error-handling.md` carve-out. MoE expert-name
  resolver migrated from hardcoded substring match to regex-registry
  (`_EXPERT_NAME_PATTERNS`) covering Mixtral, Qwen 3 MoE,
  DeepSeek-V3, Phi-MoE.
- **6 enum-shaped config fields tightened to `Literal[...]`** —
  `LoraConfig.bias`, `DistributedConfig.fsdp_backward_prefetch` /
  `fsdp_state_dict_type`, `SafetyConfig.scoring`,
  `ComplianceMetadataConfig.risk_classification`,
  `TrainingConfig.galore_optim` / `galore_proj_type`. Pydantic now
  validates whitelist at parse time; bespoke runtime validators
  removed.
- **`AuditLogger`** — operator identity raises `ConfigError` instead
  of falling back to literal `"unknown"`.
  `getpass.getuser()@socket.gethostname()` chain with
  `FORGELM_ALLOW_ANONYMOUS_OPERATOR=1` escape hatch.
- **`AuditLogger.log_event`** — `os.fsync(f.fileno())` after flush;
  chain durability across power-cut.
- **`compute_dataset_fingerprint`** split into three helpers (local
  file / HF metadata / HF revision); HF Hub revision SHA pinned.
- **Safety eval batched with token-pad-longest + per-batch CUDA-OOM
  fallback** — `_generate_safety_responses` and
  `_generate_responses_batched` use `batch_size=8` default with
  single-prompt fallback on OOM. Per-batch error handling extracted
  to `_generate_*_batch_with_oom_retry` helpers.
- **`_chunk_paragraph_tokens`** — single-encode + offset slicing
  (performance fix).
- **`_post_payload`** — delegates to `safe_post` with
  `min_timeout=1.0` for back-compat.
- **7 notebooks** install from PyPI (`forgelm[qlora]==0.5.5`) instead
  of `git+https://...`.
- **CI** enforces `pytest --cov-fail-under=40` via `pyproject.toml`
  `addopts`. Matrix `fail-fast: false`; `usermanuals-validate.yml`
  runs on push + PR.
- **Site honesty:** `compliance.html` artefact tree, `quickstart.html`
  template names, GPU stat (16 vs claimed 18) — all refreshed against
  real code.
- **QMS `sop_data_management.md`** — single v0.5.0 story; v0.5.1+ /
  v0.5.2 splits removed.
- **Roadmap** (`roadmap.md`, `roadmap-tr.md`, `releases.md`) — v0.5.5
  marked released; tristate status legend added.
- **Webhook config persisted into compliance manifest.**
  `generate_training_manifest` now writes `webhook_config` into
  `<output_dir>/compliance/compliance_report.json` so `forgelm
  approve` / `forgelm reject` (which run with no `--config` flag)
  can rebuild the notifier and fire the success / rejection webhook.
  Operator secrets resolve from env at runtime via `url_env` /
  `secret_env` so persisting the config shape is safe.
- **`forgelm.webhook`** exports `_is_private_destination` via
  `__all__` for back-compat (helper moved to `forgelm._http`).
- **Standards updates** — `architecture.md` reflects the package
  splits; `documentation.md` cites the new CI guards;
  `localization.md` formalises EN + TR mandatory + DE / FR / ES / ZH
  deferred; `release.md` documents the v0.5.5 release sequence;
  `testing.md` test count refreshed.

### Fixed

- **Nightly pip-audit gate — `transformers` CVE-2026-1839**
  (issue [#37](https://github.com/HodeTech/ForgeLM/issues/37)). The
  Supply-chain security workflow flagged the CVE whose published fix
  lives in `transformers 5.0.0rc3` (release candidate). ForgeLM's
  `pyproject.toml` pins `transformers>=4.38.0,<5.0.0`; the 5.x branch
  is a major version bump that breaks downstream callers (TRL adapter
  signature changes + tokenizer-config API drift), and there is no
  4.x backport available at the time of writing. Stop-gap: an
  explicit `--ignore-vuln CVE-2026-1839` in
  `.github/workflows/nightly.yml` with documented rationale and a
  remove-after condition (revisit at every release; remove the ignore
  once `transformers` ships a 4.x point release with the fix or
  ForgeLM cuts a tracked major-version-bump cycle).
- **Wizard YAML output is now `safe_dump(allow_unicode=True)` with
  explicit UTF-8 file handles** — prevents `!!python/object` tags
  from leaking into generated YAML when a collector returns a Path
  or set, and stops mojibake on non-ASCII compliance fields.
- **Wizard hardware-detection cache.** `_detect_hardware()` runs once
  per session; the welcome step and the post-save pre-flight
  checklist share the result instead of paying a second torch import
  + CUDA enumeration (~50–200 ms saved).
- **Wizard back / reset semantics.** `WizardBack` restores
  `state.config` from a `copy.deepcopy` snapshot taken before the
  step ran (partial mutations no longer leak into the previous
  step's prompts). `WizardReset` re-loops with a fresh state instead
  of treating the reset as a completed run and trying to save an
  empty config.
- **Wizard BYOD path validation.** Typed dataset values are now
  checked as a directory of ingestible docs, a JSONL/JSON file, or
  an HF Hub ID before being accepted; bare typos no longer silently
  become `data.dataset_name_or_path`.
- **Wizard non-empty re-prompt for Article 9 / Article 10 free-text
  fields** (`intended_use`, `foreseeable_misuse`,
  `mitigation_measures`, `collection_method`, `annotation_process`,
  `known_biases`). Empty values used to slip through and surface as
  Pydantic `ConfigError` at training-time load.
- **CUDA capability check.** `torch.cuda.get_device_properties(0).total_mem`
  → `total_memory` in `_detect_hardware` (the previous attribute
  doesn't exist; welcome step crashed on real CUDA hosts).
- **Wizard cross-tab sync.** The web wizard listens for `storage`
  events and reloads state when a sibling tab edits the same wizard,
  eliminating the "last write wins" race.
- **Wizard bundled safety-probes resolution.** The wizard's default
  safety probe set is now resolved through
  `importlib.resources.files("forgelm.safety_prompts")`, fixing the
  `pip install forgelm` regression where
  `configs/safety_prompts/general_safety.jsonl` was the wizard
  default but never shipped in the wheel.
- **`forgelm.compliance.verify_audit_log`** as a public function —
  closes a critical gap where the chain integrity check existed but
  had no library entry point.
- **Audit event catalog and CLI sample drift fixed** — placeholder
  `<TBD>` entries in `audit_event_catalog.md` filled;
  trailing-whitespace cleaned; CLI help sample in
  `docs/reference/usage.md` brought in sync with current subcommand
  surface.
- **Tier 1 ghost-feature drift:**
  - `verify-log` → `verify-audit` rename in user manuals.
  - `forgelm chat` slash commands aligned with parser: removed
    `/load`, `/top_p`, `/max_tokens`, `/safety on|off`;
    `/quit` → `/exit`.
  - `q6_k` quant level removed from GGUF docs (parser only supports
    `q2_k|q3_k_m|q4_k_m|q5_k_m|q8_0|f16`); `f16` row added.
  - `FORGELM_RESUME_TOKEN` env var removed from manuals; replaced
    with the canonical CLI subcommand flow (`forgelm approve` /
    `reject` + `forgelm approvals --pending`).
  - `FORGELM_CACHE_DIR` env var removed; `HF_HOME` declared
    canonical.
  - `forgelm benchmark --model "..."` (subcommand form that doesn't
    exist) → `forgelm --config <yaml> --benchmark-only <path>` (the
    shipped flag form).
  - `--export-bundle` → `--compliance-export` rename.
  - `kserve` / `triton` rows removed from deploy targets (parser
    only ships `{ollama,vllm,tgi,hf-endpoints}`).
  - Ingest flag drift: `--max-tokens` → `--chunk-tokens`; removed
    non-existent `--language` / `--include` / `--exclude` /
    `--format` / `--pii-locale` flags; `--strategy` choices clarified
    to `{sliding,paragraph,markdown}`.
- **Documentation and CI guard plumbing:** `docs/design/wizard_mode.md`
  rewritten to describe the actual 9-step flow;
  `docs/reference/architecture{,-tr}.md` heading updated from
  `wizard.py` to `wizard/` to match the sub-package layout;
  `docs/usermanuals/{en,tr}/compliance/human-approval-gate.md`
  cross-references exit code 5 alongside the existing exit-4
  discussion.

### Deprecated

- **`forgelm --data-audit PATH`** — the legacy flag now emits a
  `DeprecationWarning` and an `cli.legacy_flag_invoked` audit-log
  event on every invocation. Behaviour is unchanged; the flag is
  scheduled for removal in **v0.7.0**. Migrate to the
  `forgelm audit PATH` subcommand (same output, same exit codes).
  See [docs/standards/release.md](docs/standards/release.md#deprecation-cadence)
  for the removal timeline.
- **`evaluation.staging_ttl_days`** alias-forwards to
  `retention.staging_ttl_days` with a single `DeprecationWarning`;
  conflicting values raise `ConfigError`. Removal scheduled for
  v0.7.0.

### Removed

- **`[ingestion-secrets]` extra (`detect-secrets>=1.5.0,<2.0.0`)** —
  reserved during Phase 12 for a follow-up integration that never
  landed. The `detect-secrets` scanner expects file paths while
  ForgeLM audits row-level JSONL streams, so the wire-up was rejected
  as architecturally incompatible. The prefix-anchored regex set in
  `forgelm/data_audit/_secrets.py` (9-family coverage: AWS, GitHub,
  Slack, OpenAI, Google API, JWT, OpenSSH, PGP, Azure storage) is
  the sole detection backend and stays the sole detection backend.
  Removed the dead extra from `pyproject.toml`, the install snippet
  from `README.md`, and the "fallback regex set" framing from the
  secrets module docstring.

### Breaking changes (deliberate)

- **High-risk + safety-disabled now raises `ConfigError`.**
  `risk_classification ∈ {high-risk, unacceptable}` combined with
  `evaluation.safety.enabled=false` now raises at config-load time
  (was a warning). EU AI Act Article 9 risk-management evidence
  cannot be derived from a disabled safety eval. Operators with
  sandboxed runs must lower the `risk_classification` or enable
  safety.
- **`WebhookConfig.timeout` default raised 5s → 10s.** Slack/Teams
  gateway latency spikes regularly cross 5s; webhook failure is
  best-effort but a timeout silently degrades the audit chain.

---

## [0.5.0] — 2026-04-30

**Theme:** "Document Ingestion + Data Curation Pipeline" — Phases 11,
11.5, 12, and 12.5 ship as one comprehensive release.

> **Note on consolidation.** Originally planned as four sequential
> PyPI tags (`v0.5.0` / `v0.5.1` / `v0.5.2` / `v0.5.3`) but consolidated
> into a single `v0.5.0` because the four phases form one coherent
> surface (ingest → polish → mature → polish) that's hard to use in
> parts. Git history retains the four phases as separate commit
> batches; this entry collapses them into the user-facing release
> notes. Section markers below preserve the phase boundary so
> reviewers can map back to [docs/roadmap/releases.md](docs/roadmap/releases.md).

The release adds:

- **Phase 11** — `forgelm ingest` (PDF / DOCX / EPUB / TXT / Markdown
  → SFT-ready JSONL) + `forgelm audit` (length / language /
  near-duplicate / cross-split leakage / PII) + EU AI Act Article 10
  governance integration.
- **Phase 11.5** — operational polish on the Phase 11 surface: LSH
  banding, streaming reader, token-aware chunking, PDF
  header/footer dedup, PII severity tiers, atomic audit writes.
- **Phase 12** — data curation maturity: MinHash LSH dedup option,
  markdown-aware splitter, code/secrets leakage tagger, heuristic
  quality filter, DOCX table preservation.
- **Phase 12.5** — small additive polish: `--all-mask` shorthand,
  Croissant 1.0 dataset card emission, optional Presidio ML-NER PII
  adapter, wizard "audit first" entry point.

CI / docs / standards bookkeeping accompanying every phase is folded
into "Cross-cutting review hardening" at the bottom (rounds 1–12 of
review-cycle fixes applied across the four phases above).

---

### Phase 12.5 — Data Curation Polish (backlog items #1–#4)

Four follow-up items from
[`docs/roadmap/completed-phases.md`](docs/roadmap/completed-phases.md)
ship together — none require new architecture; each is a small
additive surface on top of the Phase 12 ingestion + audit lineage.

- **`forgelm ingest --all-mask`** (item #3) — one-flag shorthand for
  `--secrets-mask --pii-mask` in the documented mask order (secrets
  first so combined detectors don't double-count overlapping spans).
  Composes additively with explicit flags (set-union, no error). Pure
  UX; no new behaviour.
- **`forgelm audit --croissant`** (item #2) — opt-in
  [Google Croissant 1.0](http://mlcommons.org/croissant/) dataset card
  emitted under a new `croissant` key in `data_audit_report.json`. The
  card carries dataset-level identity, one `cr:FileObject` per JSONL
  split, and a `cr:RecordSet` per split with `cr:Field` entries
  derived from the audit's column detection. Existing audit JSON keys
  are byte-equivalent — the block stays empty when the flag is off
  (same precedent as `secrets_summary` / `quality_summary`). Lets the
  same JSON file double as both the EU AI Act Article 10 governance
  artifact and a Croissant-consumer dataset card.
  - `url` and `contentUrl` use the as-typed input string and the
    relative split filename, never the resolved absolute filesystem
    path, so cards published to HuggingFace / MLCommons don't leak
    the auditor's local layout.
  - Croissant `version` (`sc:version`, dataset version) is omitted
    deliberately — the audit doesn't have first-class evidence for
    it; vocab conformance is declared via `conformsTo`. Operators
    that publish hand-edit `version` like they do `license` /
    `citeAs`.
  - The card is now also surfaced in the `--output-format json`
    stdout envelope alongside the on-disk report so CI consumers
    don't need a second file slurp.
- **`forgelm audit --pii-ml [--pii-ml-language LANG]`** + new
  `[ingestion-pii-ml]` extra (item #1) — opt-in
  [Presidio](https://github.com/microsoft/presidio) ML-NER PII detector
  layered on top of the existing regex detector. Adds the
  unstructured-identifier categories the regex inherently misses
  (`person`, `organization`, `location`) into the same `pii_summary` /
  `pii_severity` blocks under disjoint category names. Severity tiers
  in the new `PII_ML_SEVERITY` table: `person → medium`,
  `organization → low`, `location → low` (deliberately below the regex
  `critical`/`high` tiers because NER false-positive rates are
  materially higher than regex-anchored detection). The pre-flight
  check covers BOTH the missing-extra branch AND the missing-spaCy-model
  branch — `presidio-analyzer` does *not* transitively ship a spaCy
  NER model, so the install recipe is now two lines:
  ```bash
  pip install 'forgelm[ingestion-pii-ml]'
  python -m spacy download en_core_web_lg
  ```
  Without either, `forgelm audit --pii-ml` raises `ImportError` with
  the recipe before any rows are scanned. Per-row Presidio failures
  are scoped to `(ValueError, RuntimeError)` so a single malformed row
  never blocks the audit, but a deep `OSError` from a missing model
  surfaces loudly instead of silently scoring zero ML coverage.
  `--pii-ml-language` (default `"en"`) lets non-English corpora point
  at the matching spaCy model; Presidio raises a typed exception when
  no engine is registered for the requested language.
- **Wizard "audit first" entry point** (item #4) — when the wizard
  resolves a JSONL (either typed directly or produced by the
  Phase 11.5 `_offer_ingest_for_directory` ingest flow), it now offers
  to run `forgelm audit` on it inline and prints `summarize_report`'s
  verdict before continuing. Mirrors the
  `_offer_ingest_for_directory` shape exactly. Closes the BYOD audit
  loop end-to-end. Audit is informational, not a gate — failures fall
  through to the "continue without audit" path.

Touch points (so the next reviewer can audit blast radius quickly):

- `forgelm/ingestion.py` — no module changes (the flag composes at the
  CLI boundary into the existing `pii_mask` / `secrets_mask` booleans).
- `forgelm/cli.py` — three new flags on the existing subparsers
  (`--all-mask` on `forgelm ingest`; `--croissant` and `--pii-ml` on
  `forgelm audit`); dispatcher signatures threaded through.
- `forgelm/data_audit.py` — `_HAS_PRESIDIO` sentinel, `_require_presidio`,
  `_get_presidio_analyzer` (cached), `detect_pii_ml`,
  `PII_ML_SEVERITY`, `PII_ML_TYPES`, `_PRESIDIO_ENTITY_MAP`,
  `_build_croissant_metadata`, `_CROISSANT_CONTEXT`. New
  `enable_pii_ml` / `emit_croissant` parameters on `audit_dataset` /
  `_process_split` / `_audit_split`; new `enable_pii_ml` field on
  `_StreamingAggregator`; new `croissant` field on `AuditReport`.
  `_build_pii_severity` now consults the merged
  `PII_SEVERITY ∪ PII_ML_SEVERITY` table.
- `forgelm/wizard.py` — new `_offer_audit_for_jsonl(path)` helper;
  invoked from `_offer_ingest_for_directory` (after ingest produces
  a JSONL), `_validate_local_jsonl` (after a directly-provided JSONL
  passes validation), and `_prompt_dataset_path_with_ingest_offer`
  (after a non-directory JSONL is provided to the full wizard).
- `pyproject.toml` — new `[ingestion-pii-ml]` extra
  (`presidio-analyzer>=2.2.0,<3.0.0`).
- `tests/test_phase12_5.py` — 11 new tests, four classes (one per
  backlog row).
- `tests/test_wizard_byod.py` — three existing tests get an extra
  `"n"` answer to decline the new audit-first offer (the offer
  behaviour has its own coverage in `test_phase12_5.py`).
- Docs — `README.md` install matrix + Phase 12.5 feature line;
  `docs/standards/architecture.md` extras matrix; `docs/guides/ingestion{,-tr}.md`
  + `docs/guides/data_audit{,-tr}.md` get dedicated sections per
  feature; `notebooks/data_curation.ipynb` mentions `--all-mask` and
  the Phase 12.5 audit add-ons inline.

### Fixed — post-PR-#13 review-cycle batches (rounds 8-12)

Inline-comment batches landing on top of PR #13 (now merged to `main`).
Same review surface as rounds 4-7; further hardening on top of the
`v0.5.2` content.

- **Audit log hardening** (`forgelm/compliance.py`) — HMAC `_hmac` field is now
  emitted only when `FORGELM_AUDIT_SECRET` is set; without a secret, a key
  derived solely from the public `run_id` would be forgeable, so we no longer
  claim tamper-evidence we cannot deliver. `log_event` re-reads the chain head
  from disk under the same `flock` so two writers sharing the same log can no
  longer fork the chain. `_read_chain_head` refuses to derive a head from a
  tail that does not end with `\n` (truncated last record). The oversize-
  final-entry case is recovered by re-reading from `seek_start` without
  skipping the partial first line.
- **Deployer-instructions Markdown injection** (`forgelm/compliance.py::generate_deployer_instructions`) —
  config-derived strings (`system_name`, `model.name_or_path`, fine-tuning
  method, model location, foreseeable-misuse bullets, metric names) now go
  through `_sanitize_md` before template substitution; pipes / backticks /
  link syntax in any of those can no longer break out of table cells or
  bullets in the generated Article 13 document.
- **Quality-filter denominator** (`forgelm/data_audit.py::_build_quality_summary`) —
  `overall_quality_score` now divides by the number of rows the filter
  actually evaluated (text-bearing dict rows) instead of `total_samples`.
  A corpus that's 50 % null but 100 % clean on the rest now reads `1.0`
  instead of `0.5`.
- **NumPy-fast-path bits guard** (`forgelm/data_audit.py::compute_simhash`) —
  the `_compute_simhash_numpy` dispatch now also gates on `bits <= 64`;
  without it, `np.uint64` would silently truncate digests wider than 64
  bits.
- **Sliding-overlap clamp** (`forgelm/ingestion.py::ingest_path`) — when
  `--overlap` is not passed and the strategy is `sliding`, the implicit
  `DEFAULT_SLIDING_OVERLAP` (200) is now clamped to `chunk_size // 2`. A
  small `--chunk-size 300` used to trip `_chunk_sliding`'s
  "overlap > chunk_size // 2" guard with the default overlap — surfacing
  as a confusing error for a knob the user did not set.
- **Batch-tokenizer narrow except** (`forgelm/ingestion.py::_count_section_tokens`) —
  the bare `except Exception` around the batched `tokenizer(blocks)` call
  is now narrowed to `(TypeError, ValueError)` (the documented
  unsupported-batch signal); the returned `BatchEncoding` is shape-
  validated before its `input_ids` is consumed. Real bugs (corrupted
  input, OOM, etc.) no longer mask behind the slow per-block fallback.
- **Webhook secret-fallback safety** (`forgelm/webhook.py`) —
  `requests.post` now passes `allow_redirects=False` (an SSRF-pre-validated
  URL cannot be redirected to a private destination) and the
  `mask_secrets` `ImportError` fallback emits `[REDACTED — secrets
  masker unavailable]` instead of the raw 512-char reason prefix.
  See [#14](https://github.com/HodeTech/ForgeLM/issues/14) for the
  remaining DNS-rebinding TOCTOU follow-up tracked for `v0.5.3`.
- **Trainer governance failure visibility** (`forgelm/trainer.py`) — the
  `data_governance_report.json` export try/except now catches the full
  `Exception` set (was `OSError` only) so non-IO failures (`TypeError`,
  `ValueError`, `AttributeError`) still surface as
  `compliance.governance_failed` audit events instead of crashing the
  surrounding compliance flow. The rollup `compliance.artifacts_exported`
  event is gated on a `governance_ok` flag so the audit chain truthfully
  reflects which artefacts are actually on disk.
- **Compliance manifest exception narrowing** (`forgelm/compliance.py`) —
  the broad `except Exception` around the HF Hub `load_dataset_builder`
  fingerprint fetch is now a tuple of realistic failure modes
  (`ImportError`, `FileNotFoundError`, `ValueError`, `AttributeError`,
  `ConnectionError`, `TimeoutError`).
- **Strict messages-format validation** (`forgelm/data.py`) —
  `_process_messages_format` now explicitly checks `isinstance(role, str)`
  and `isinstance(content, str)` before formatting; non-string content
  (dicts, ints) used to be silently coerced via f-string `__format__`
  and slip through into training.
- **Wizard ASCII regex flag** (`forgelm/wizard.py`) — `_HF_HUB_ID_RE`
  now compiles with `re.ASCII` so the `\w` class means
  `[A-Za-z0-9_]`. HF Hub IDs are ASCII-only, and Unicode-aware `\w`
  would otherwise accept characters the Hub itself rejects.
- **GGUF converter case-insensitive validation** (`forgelm/export.py`) —
  the `FORGELM_GGUF_CONVERTER` `.py` suffix check now uses
  `casefold()` (cross-platform: HFS+/NTFS), and `export_model()`'s
  catch widened from `(ImportError, FileNotFoundError)` to also
  include `ValueError` so a non-`.py` env override produces an
  `ExportResult` instead of crashing the caller.
- **Markdown chunker complexity refactor** (`forgelm/ingestion.py`) —
  `_chunk_markdown_tokens` split into `_build_markdown_section_blocks`
  (render breadcrumb + body), `_count_section_tokens` (batch tokenizer
  call with per-block fallback), and the main chunker (greedy packing).
  Cognitive complexity drops from 16 → ~8.
- **Bidirectional MinHash extraction** (`forgelm/data_audit.py`) — the
  two near-identical `a→lsh_b` / `b→lsh_a` query loops in
  `_count_leaked_rows_minhash_bidirectional` were extracted into one
  `_count_leaks_against_index` helper. Complexity drops from 24 → ~5;
  the SonarCloud duplication metric on this file goes away.
- **Streaming length digest** (`forgelm/data_audit.py`) — the per-split
  text-length distribution is now accumulated via a bounded
  `_LengthDigest` (Algorithm R reservoir, 100K cap) instead of an
  unbounded `List[int]`. Audit memory on multi-million-row splits is
  now O(1) instead of O(n).
- **Documentation drift sweep (round N)** — five compliance-summary
  links repointed to `../../forgelm/...`, two missing FSDP knobs
  (`fsdp_backward_prefetch`, `fsdp_state_dict_type`) and two webhook
  knobs (`allow_private_destinations`, `tls_ca_bundle`) added to both
  EN and TR `configuration` reference; Pro CLI section added to
  `README.md`; CI now runs a bilingual H2 parity check across seven
  EN/TR doc pairs (`configuration`, `usage`, `distributed_training`,
  `data_preparation`, `architecture`, `ingestion`, `data_audit`); test
  count refreshed to 47 in `CONTRIBUTING.md` to match
  `architecture.md`; secrets list aligned to the full nine families
  in `forgelm.data_audit.SECRET_TYPES` (was missing two private-key
  splits + Azure storage in some prose).
- **Phase 12 fenced log block** in both `usage.md` and `usage-tr.md`
  now uses ```` ```text ```` so markdownlint MD040 stops flagging it.

### Fixed — multi-agent master review (rounds 4-7)

Multi-dimension review (business, code, compliance, documentation, performance, security) surfaced a cluster of correctness, claim/evidence, and silent-failure issues that have been swept in batches.

- **Version drift** — `forgelm.__version__` was hard-coded to `0.5.0rc1` in [`forgelm/__init__.py`](forgelm/__init__.py) while `pyproject.toml` declared `0.5.2rc1`. The literal is now derived from the installed distribution via `importlib.metadata.version("forgelm")` (with a `0.0.0+dev` fallback for raw source checkouts), and `compliance._get_version()` follows the same resolution path so audit / Annex IV manifests stamp the correct producer version.
- **Audit log integrity** (`forgelm/compliance.py::AuditLogger`) — `_load_last_hash` previously re-rooted the chain to `"genesis"` on any read failure with only a `logger.debug` message; `log_event` advanced `_prev_hash` *before* the file write and swallowed write failures with `logger.warning`. Both paths now distinguish file-missing from file-unreadable, raise on real I/O errors, and only advance the hash chain after a successful write.
- **`compute_dataset_fingerprint` TOCTOU** — `@lru_cache(maxsize=32)` keyed on the path string only would return stale fingerprints when the file was rewritten in place. Cache dropped; symlinks resolved before hashing; `os.stat()` now captured atomically alongside the SHA-256 stream so size/mtime cannot drift between the two reads.
- **`generate_data_governance_report` wiring** — defined and tested but never called from production code. Now invoked by `_export_compliance_if_needed` so `data_governance_report.json` actually lands in the trainer's `output_dir` per EU AI Act Article 10.
- **Silent-failure sweep** — replaced `except Exception:` swallows with concrete-class catches + log + raise/sentinel: `data.py::_process_messages_format` (catches malformed message rows by exception class, raises an explicit `ValueError`), `safety.py::_release_model_from_gpu` (`RuntimeError`/`OOM` only), `cli.py::_load_config_or_exit` (split `yaml.YAMLError` + `pydantic.ValidationError` for clearer error messages), `config.py::ForgeConfig.load_config` (specific Pydantic / YAML branches).
- **Pydantic schema discipline** — six bare-`str` fields (`trainer_type`, `merge.method`, `model.backend`, `distributed.fsdp_strategy`, `risk_assessment.risk_category`, `monitoring.metrics_export`) converted to `Literal[...]` so JSON Schema / IDE auto-complete surfaces the allowed values; redundant runtime validators dropped.
- **Webhook hardening** — `forgelm/webhook.py` now refuses non-loopback private destinations without explicit opt-in (`webhook.allow_private_destinations`), runs the failure-reason payload through `mask_secrets`, passes `verify=True` explicitly to `requests.post`, and rejects `timeout < 1`.
- **Performance** — `forgelm/trainer.py` lazy-imports `torch` / `transformers` / `trl` into method bodies, dropping CLI cold-start cost by ~700-1500 ms on `forgelm audit` and `forgelm --help`. Audit's `agg.minhashes` is no longer copied via `list(...)` before LSH (saves ~1 GB on 1M-row splits).
- **Documentation** — refreshed module / test / notebook counts in `CONTRIBUTING.md` and `docs/reference/architecture.md`; added `forgelm/templates/` to the directory layout. Removed `forgelm chat --safety` from `usage.md` (flag does not exist in `cli.py`). `coverage.fail_under` in `docs/standards/testing.md` now matches `pyproject.toml` (40, not 25).

### Fixed — round 3.5 review (`_MARKDOWN_CODE_FENCE` regex → non-regex parser)

SonarCloud `python:S5852` flagged `_MARKDOWN_CODE_FENCE` (`forgelm/ingestion.py` L515) — the regex `^ {0,3}(?P<fence>` `` ` ``{3,}|~{3,})(?P<rest>[^\n]*)$` had **two unbounded greedy quantifiers in sequence over overlapping character classes** (the fence run is `` ` `` / `~`; the `rest` capture's `[^\n]` includes both fence chars), the textbook polynomial-runtime shape per regex.md rule 4.

- Empirically linear in CPython (50K-char pure-backtick run = 16 μs), but the static analyser can't prove that — and we already use non-regex line walkers everywhere else for markdown parsing (regex.md rule 6).
- Replaced with `_parse_md_fence(line)` — a non-regex parser that returns `(fence_char, run_length, rest_after_run)` or `None`. Provably O(n) per line; 100K-char pure-backtick run measures ~10 μs.
- `_markdown_sections` updated to use the helper directly (no behavioural change — the helper returns the same tuple shape the regex's named groups did).
- 2 new regression tests in `tests/test_phase12_review_fixes.py::TestRegexLinearity` — `test_parse_md_fence_linear_on_long_runs` (≤ 100 ms cap on N=100K) + `test_parse_md_fence_behaviour` (pinned outputs for opener with info string, 4-char fence, 2-space indent, 4-space indent → None, sub-3-char run → None, mismatched chars after run).

### Fixed — round 3 review (post-`69ee6ab`)

Round-3 review caught two real correctness bugs (Unicode `\w` in
secret regexes, fence-length rule violation in markdown / code-fence
tracking) plus a handful of doc / fixture parity issues. All applied.

- **`re.ASCII` flag on secret regexes** (`forgelm/data_audit.py`) —
  Last commit changed `[A-Za-z0-9_-]` → `[\w-]` in `github_token` /
  `openai_api_key` / `google_api_key` / `jwt`, but Python's default
  `\w` is **Unicode-aware** (matches `ünicode`, `türkçe`, …), which
  would broaden the match universe to include non-ASCII chars that
  real credentials never contain. Added `flags=re.ASCII` to all four
  patterns so `\w` is restricted to ASCII. Patterns that already use
  explicit ASCII character classes (`aws_access_key`, `slack_token`,
  the explicit `[A-Z0-9]` ones) are unchanged.
- **`regex.md` Rule 1 corrected** — Previous wording stated
  `[A-Za-z0-9_]` and `\w` are equivalent in Python. They are not.
  Rewrote the rule with a side-by-side example showing the Unicode /
  ASCII divergence, plus a decision table: ASCII-only inputs → `\w`
  with `re.ASCII` (or explicit class), natural-language inputs →
  bare `\w` (Unicode-aware), mixed → be explicit.
- **CommonMark fence-length rule enforced** (`forgelm/data_audit.py`
  + `forgelm/ingestion.py`) — CommonMark §4.5 requires the closing
  fence to use **at least as many** fence characters as the opener.
  Both `_strip_code_fences` and `_markdown_sections` previously
  tracked only the fence character, so a 4-backtick opener (` ```` `)
  was prematurely closed by a 3-backtick line. `_is_code_fence_open`
  now returns `(char, run_length)`; `_is_code_fence_close` accepts
  the minimum run-length and rejects shorter closes. The markdown
  splitter's `_MARKDOWN_CODE_FENCE` regex captures the fence run
  (`(?P<fence>...)`) and the rest of the line (`(?P<rest>...)`) so
  the splitter can also enforce "no info string on close" alongside
  the length rule. All three CommonMark §4.5 close-side rules
  (matching char + run length ≥ open + no info string) now hold.
- **`data_audit.md` reframes `[ingestion-secrets]`** — The doc
  previously implied installing the extra layered `detect-secrets`
  on top of the regex fallback. The current code does not invoke
  `detect-secrets` at all. Reworded as forward-compatibility:
  installing the extra is safe to pin in requirements files but
  doesn't change audit behaviour today.
- **`README` clarifies `semantic` chunking strategy** — Listed as
  reserved/planned: the implementation raises `NotImplementedError`
  and the CLI hides it from `--strategy` choices. Previous wording
  implied it was available at runtime.
- **`ingestion-tr.md` CLI synopsis adds Phase 12 flags** —
  `--strategy markdown` and `--secrets-mask` now appear in the
  options block; short Turkish description for each.
- **`review-pr` skill heading updated** — "The six-question review"
  → "The seven-question review" to match the regex-check question
  added in the previous commit.
- **`data_curation.ipynb` fixture credentials fragmented** —
  `deploy_runbook.txt` fixture now builds `AKIA…` / `ghp_…` strings
  at runtime from inert fragments (same convention as
  `tests/test_data_audit_phase12.py::FAKE_AWS_KEY`). Repo-wide
  secret scanners no longer flag the notebook source.
- **`data_curation.ipynb` MinHash install uses the project extra** —
  `pip install 'datasketch>=1.6.0,<2.0.0'` →
  `pip install 'forgelm[ingestion-scale]==0.5.2'` so the recipe
  matches the install hint baked into
  `forgelm.data_audit._require_datasketch`.
- **`TestMinHashDistinctSemantic` uses pytest's `tmp_path`** — Was
  creating a directory under `tests/` which mutated the repo and
  broke parallel pytest runs. Now uses the standard `tmp_path`
  fixture; no manual cleanup needed.
- **3 new fence-length regression tests** in
  `tests/test_phase12_review_fixes.py::TestFenceRunLengthRule`:
  4-backtick block not closed by 3 backticks; `_strip_code_fences`
  respects the length rule; close lines with info strings are
  treated as content (CommonMark §4.5 conformance).

### Added — Regex hygiene standard

- **New standard `docs/standards/regex.md`** — codifies 8 hard rules absorbed from Phase 11/11.5/12 review cycles (no `[A-Za-z0-9_]` shorthand, no single-char character classes, bound your quantifiers, no two competing quantifiers over the same class, no `\s` under MULTILINE, no `.*?` + back-reference + DOTALL, anchored `^` / `$`, no leading `^.*`). Each rule cites the concrete review finding that produced it. Includes a ReDoS-exposure budget (10K-char pathological-input benchmark must stay ≤ 10ms) and test fixture hygiene rules (build credential-shaped strings from inert fragments at runtime). Linked from `coding.md`, `code-review.md`, the `review-pr` skill, and `CLAUDE.md`'s "read before editing" entry point.
- **`code-review.md` checklist gains a regex section** — explicit `git diff` recipe to surface modified `re.compile` / `re.match` / `re.sub` calls + per-regex audit checklist.
- **`review-pr` skill gains a regex check** — same checklist, applied during self-review before opening a PR.

### Fixed — Phase 12 review cycle round 2.5 (post-`30ef590`)

Round-2.5 review surfaced two confirmed ReDoS shapes that the earlier rounds missed; the regex hygiene sweep above also caught a handful of style-only deviations across the codebase.

- **ReDoS confirmed in `_MARKDOWN_HEADING_PATTERN`** (`forgelm/ingestion.py`) — Old pattern `[ \t]+(.+?)[ \t]*$` had three quantifiers competing for trailing whitespace; pathological input `"# a" + " \t" * n + "x"` ran in O(n²) time (100ms at n=2000, 600ms at n=5000, 2.1s at n=10000 measured in CPython 3.11). Replaced with a non-whitespace anchor on the body capture: `[ \t]+(\S(?:[^\n]*\S)?)[ \t]*$`. Result: linear (10μs at n=10000 — 200000× speedup).
- **`_CODE_FENCE_BLOCK` regex replaced with state machine** (`forgelm/data_audit.py`) — Old form used `.*?` + back-reference + `re.DOTALL`, which SonarCloud `python:S5852` flags as a polynomial-runtime risk even though it benchmarks linearly in CPython. Replaced with a per-line state machine (`_strip_code_fences` + `_is_code_fence_open` + `_is_code_fence_close`) that is provably O(n) and matches the same line-walker pattern as `_markdown_sections`. Behaviour pinned bit-for-bit on 7 fixtures.
- **`[A-Za-z0-9_-]` → `[\w-]`** in `openai_api_key`, `google_api_key`, `jwt` (3 places) regexes per regex.md rule 1.
- **`\s*$` → `[ \t]*$`** in `_PUNCT_END_PATTERN` (callers pre-split into single lines, so the `\s` newline-overlap is dead weight) per regex.md rule 5.
- **Bounded `_HF_HUB_ID_RE`** (`forgelm/wizard.py`) — `[A-Za-z0-9._-]+` → `[\w.-]{1,96}` (HF Hub username + repo name max length) per regex.md rule 3 — defence-in-depth, no behaviour change for well-formed HF IDs.

### ReDoS regression tests

- **New `TestRegexLinearity` class in `tests/test_phase12_review_fixes.py`** — pinned 1-second wall-clock cap on N=10000 pathological inputs for both `_MARKDOWN_HEADING_PATTERN` and `_strip_code_fences`. A real ReDoS would blow far past the threshold; a slow CI host won't false-positive.
- **Empirical sweep over all 25 forgelm regexes** confirmed linear scaling under 50K-character adversarial input. Slowest pattern (`openssh_private_key`, full-block PEM) measures 0.5ms — ~10μs/KB. The sweep is reproducible via the snippet documented in regex.md.

### Fixed — Phase 12 review cycle round 2 (post-`bf8ca82`)

Second-round review of the Phase 12 commit surfaced 22 findings spanning correctness, regex coverage, code-smell hygiene, type widening, and documentation parity. All addressed.

- **Private-key blocks redacted in full** (`forgelm/data_audit.py`) — Old `openssh_private_key` / `pgp_private_key` regexes only matched the `BEGIN` header line, so `mask_secrets` left the entire base64 body + `END` line in clear text. Now both patterns match the full PEM/PGP envelope (BEGIN through matching END inclusive) under `re.DOTALL`. The literal block markers are split across `r"-----" + r"BEGIN " + r"..."` concatenations to keep repo-wide secret scanners (gitleaks / trufflehog) silent.
- **Fenced code blocks recognise tildes too** (`forgelm/data_audit.py` + `forgelm/ingestion.py`) — `_CODE_FENCE_BLOCK` (audit's quality-filter strip) and `_MARKDOWN_CODE_FENCE` (ingest's markdown splitter) only matched triple-backtick fences; CommonMark §4.5 also allows `~~~`. Both now recognise either fence character with up to 3 leading spaces. The markdown splitter additionally tracks the *opening* fence character so a stray `\`\`\`` inside a `~~~` block (or vice-versa) doesn't toggle state.
- **DOCX block order preserved** (`forgelm/ingestion.py`: `_iter_docx_blocks`) — `_extract_docx` previously appended every paragraph followed by every table, reordering content. New helper walks `doc.element.body` in source order, dispatches on `<w:p>` vs. `<w:tbl>`, and renders each block in place.
- **Markdown overlap rejected explicitly** — `_strategy_dispatch` and `_strategy_dispatch_tokens` raise `ValueError` when `--strategy markdown` is combined with a non-zero overlap, rather than silently dropping it. To keep the CLI's historical default `--overlap 200` from spuriously tripping the validator on a `--strategy markdown` invocation that didn't ask for overlap, `--overlap`'s argparse default is now `None`; `ingest_path` resolves that sentinel to `200` for the sliding strategy and `0` for paragraph / markdown.
- **`minhash_distinct` counts unique sketches** (`forgelm/data_audit.py`) — Previously returned the count of non-empty rows, breaking parity with `simhash_distinct` (which is *unique fingerprints*). Now hashes each MinHash via `m.hashvalues.tobytes()` and counts the distinct set, matching simhash semantics.
- **`_row_quality_flags` typed `Optional[str]`** — The function already accepted `None` at runtime; the signature now reflects that and the test's `# type: ignore[arg-type]` suppression is gone.
- **Cognitive-complexity refactors** — `_row_quality_flags` (CCN 22 → ≤ 10 via per-check helpers `_check_low_alpha_ratio` / `_check_low_punct_endings` / `_check_abnormal_mean_word_length` / `_check_short_paragraphs` / `_check_repeated_lines`); `find_near_duplicates_minhash` (CCN 21 → ≤ 10 via `_build_minhash_lsh` + `_emit_minhash_pair`); `audit_dataset` (CCN 21 → ≤ 12 via `_fold_outcome_into_summary` + `_build_quality_summary` + `_build_near_duplicate_summary`).
- **Regex / lint code-smells** — `[A-Za-z0-9_]` → `\w` in the GitHub PAT pattern; `[ ]{0,3}` → ` {0,3}` (single-char class collapsed) in markdown patterns; `\s` → `[ \t]` in heading pattern (mitigates the polynomial-backtracking concern SonarCloud flagged); duplicate `"chunk_tokens must be positive"` / `"max_chunk_size must be positive"` literal strings extracted to module constants `_CHUNK_TOKENS_POSITIVE_MSG` / `_CHUNK_SIZE_POSITIVE_MSG`; `_MARKDOWN_OVERLAP_UNSUPPORTED_MSG` constant for the new validator; comprehension `["| " + " | ".join(c for c in row) + " |"]` simplified to `["| " + " | ".join(row) + " |"]`.
- **Documentation parity** — `docs/guides/data_audit.md` quality-filter bullet list and JSON example now include `repeated_lines` and a note about code-fence stripping. `docs/guides/ingestion-tr.md` mirrors the EN guide's chunking-strategies table (markdown row added) and gains a new "secrets/credential masking (Phase 12)" section. `CHANGELOG`'s Phase 12 entry no longer overstates the `[ingestion-secrets]` extra: the regex set is the sole detection backend in v0.5.2, and the `detect-secrets` package is reserved for a follow-up release. `README` separates "From PyPI" and "From a local clone" install blocks so copy-paste users don't hit `-e .` confusion.
- **Test fixtures fragmented** — All hardcoded credential / JWT literals in `tests/test_data_audit_phase12.py`, `tests/test_ingestion_phase12.py`, and `tests/test_phase12_review_fixes.py` now built at runtime from inert string fragments (e.g. `"AKIA" + "IOSFODNN7" + "EXAMPLE"`). The regex still has to match the canonical shape, but no full literal credential lives in the source tree — silences gitleaks / trufflehog scans of the repo without changing behaviour.
- **5 new round-2 regression tests** (`tests/test_phase12_review_fixes.py`) — `TestTildeFenceRecognised` (~~~-fenced code blocks block heading splits), `TestPrivateKeyFullBlock` (full PEM body redaction), `TestMarkdownOverlapValidation` (rejection on explicit non-zero overlap; default-overlap pass-through), `TestMinHashDistinctSemantic` (unique-sketches semantic).
- **Notebook ruff format** — `notebooks/post_training_workflow.ipynb` reformatted to satisfy `ruff format --check` in CI; `notebooks/data_curation.ipynb` install line pinned to `forgelm[ingestion]==0.5.2` rather than the moving `main` branch.

### Fixed — Phase 12 review cycle (post-`2f5722a`)

Round-1 review of the Phase 12 commit surfaced four critical regressions / bugs and several lower-severity issues. All addressed before tagging `v0.5.2`. No new functionality; only correctness, honesty, and parity fixes.

- **JSON envelope back-compat** (`forgelm/cli.py`) — `_run_data_audit`'s stdout JSON envelope dropped the v0.5.1 `near_duplicate_pairs_per_split` top-level key when the richer `near_duplicate_summary` block was added. Pre-Phase-12 CI consumers (`jq '.near_duplicate_pairs_per_split.train'`) would have started getting `null`. Restored as an additive key alongside the new one. Plan / CHANGELOG language updated from *"byte-identical default report"* to *"schema-additive"* — older parsers keep working, but on-disk JSON is no longer byte-identical because `secrets_summary`, `near_duplicate_summary.method`, and `cross_split_overlap.method` are now always present.
- **Quality filter completes the planned check set** (`forgelm/data_audit.py`) — Plan promised five Gopher / C4 / RefinedWeb-style heuristics; v0.5.2 shipped four. Added the missing `repeated_lines` check (top-3 actually-repeating distinct lines covering > 30 % of non-empty lines flag the row — pinned on count ≥ 2 so short all-unique documents don't false-positive). Surfaces in `quality_summary.by_check.repeated_lines`.
- **Quality filter respects fenced markdown code** (`forgelm/data_audit.py`: `_strip_code_fences`) — Code blocks legitimately have low alpha ratio + missing end-of-line punctuation + short paragraphs and tripped every prose heuristic, polluting the `quality_summary` on legitimate code-instruct corpora. `_row_quality_flags` now strips fenced ``` … ``` blocks before applying the heuristics; pure-code rows return `[]` instead of being flagged on shape grounds.
- **DOCX table cells escape `|` and `\`** (`forgelm/ingestion.py`: `_escape_md_cell`) — `_docx_table_to_markdown` joined cell text directly into a markdown table row, so a cell containing `a|b` was parsed by downstream tokenisers as two extra columns. Now escapes `|` → `\|` and `\` → `\\` per CommonMark, and collapses embedded newlines to spaces (markdown tables can't carry multi-line cells).
- **JWT regex narrowed** (`forgelm/data_audit.py`) — Old pattern `\beyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b` false-positived on prose like `eyJfoo.eyJbar.baz`. Anchored on the canonical JWT header alphabet (`alg` / `typ` / `kid` / `cty` / `enc` / `api`'s base64url prefixes — `hbGc`, `0eXA`, `raWQ`, `jdHk`, `lbmM`, `hcGk`) plus minimum lengths on payload and signature. Real JWTs (including the original test fixture) still match; arbitrary `eyJ.eyJ.X`-shaped prose does not.
- **MinHash docstring honest about the metric** (`forgelm/data_audit.py`) — `compute_minhash` previously claimed it surfaces "the same class of near-duplicates" as simhash. The two use different similarity metrics (set-Jaccard over distinct tokens vs. frequency-weighted bit-cosine) and disagree on documents with high token-frequency variance. Docstring rewritten to make the divergence explicit. Roadmap "byte-identical" wording corrected in the same spirit.
- **CommonMark indented headings recognised** (`forgelm/ingestion.py`) — `_MARKDOWN_HEADING_PATTERN` and `_MARKDOWN_CODE_FENCE` allow up to 3 leading spaces per CommonMark §4.2; 4+ spaces still fall through as indented code blocks (correctly *not* split as headings).
- **Cog complexity restored to ≤ 15** — `_aggregator_to_info` (split into `_populate_schema_block` / `_populate_optional_findings` / `_within_split_pairs`) and `_markdown_sections` (split into `_push_heading_onto_path` / `_trim_blank_edges`) factored to stay under the Phase 11.5 ceiling.
- **Defensive lazy import in `compute_minhash`** — Empty input now returns `None` without paying the `_require_datasketch()` raise path. Same effect for `_count_leaked_rows_minhash` when the entire target list is empty (LSH-construction skipped).
- **Mask-order rationale honest** (`forgelm/ingestion.py`: `_emit_chunk`) — Old docstring claimed today's regex sets overlap; in practice the shipped fixtures show no overlap. Rewritten to describe the ordering as *defensive* (favour secrets when ordering matters at all; future-proof against new PII / secret regexes that may overlap, e.g. Azure connection strings vs. IBANs).
- **Markdown chunkers document the no-overlap contract** — `_chunk_markdown` and `_chunk_markdown_tokens` docstrings explicitly state that `--overlap` / `--overlap-tokens` are silently ignored when `--strategy markdown` is selected (sections are atomic; overlapping would slice mid-section and break the breadcrumb invariant).
- **Type hints tightened** — `IngestionResult.format_counts` / `pii_redaction_counts` / `secrets_redaction_counts` and the local counters in `ingest_path` typed as `Dict[str, int]` instead of bare `dict`.
- **Turkish documentation parity** (`docs/guides/data_audit-tr.md`) — Three Phase 12 H3 sections (MinHash LSH, Code/secret tagger, Heuristic quality filter) were missing from the TR mirror; added at the same detail level as the EN guide.
- **18 regression tests** (`tests/test_phase12_review_fixes.py`) — One class per finding, pinning the fixes against re-introduction. Covers the JSON envelope shape, `repeated_lines` detection on real boilerplate vs. all-unique short docs, DOCX `|` / `\` / newline escaping, JWT header-alphabet anchors with the prose-shape false-positive, code-fence stripping in the quality filter, the token-aware markdown chunker (previously untested), and CommonMark 0-3-space indented headings.

### Added — Phase 12 (Data Curation Maturity, targeting v0.5.2)

Direct continuation of the Phase 11 / 11.5 ingestion + audit lineage. Closes the four concrete gaps surfaced by the post-`v0.5.1` competitive review (LLaMA-Factory / Axolotl / Unsloth / NeMo Curator / Dolma / RedPajama / LlamaIndex / LangChain / Marker / Docling). Tier 1 (5 must-have tasks) shipped; Tier 2/3 (Presidio adapter, Croissant metadata, `--all-mask`, wizard "audit first") deferred to a [Phase 12.5 backlog](docs/roadmap/completed-phases.md).

- **MinHash LSH dedup option** (`forgelm/data_audit.py`: `compute_minhash`, `find_near_duplicates_minhash`, `_count_leaked_rows_minhash`) — Opt-in `--dedup-method minhash --jaccard-threshold 0.85` route via the optional `[ingestion-scale]` extra (`datasketch>=1.6.0`). Default simhash + LSH banding from Phase 11.5 stays untouched and remains the only method that runs without an optional dependency. `audit_dataset(...)` API gains `dedup_method`, `minhash_jaccard`, `minhash_num_perm` parameters; `near_duplicate_summary.method` records which path ran. Cross-split overlap + within-split duplicate scan share the same method flag. MinHash is approximate (permutation noise; default `num_perm=128`) — pin `num_perm` for cross-run determinism.
- **Markdown-aware splitter** (`forgelm/ingestion.py`: `_chunk_markdown`, `_chunk_markdown_tokens`, `_markdown_sections`, `_heading_breadcrumb`) — New `--strategy markdown` parses heading hierarchy (`# H1` … `###### H6`), keeps code-fenced blocks atomic (heading-shaped lines inside ```` ``` ```` blocks are not interpreted as section boundaries), and inlines a heading **breadcrumb** at the top of each chunk so SFT loss sees the document context. Composes with the Phase 11.5 token-aware mode (`--chunk-tokens` + `--tokenizer`).
- **Code / secret leakage tagger** (`forgelm/data_audit.py`: `detect_secrets`, `mask_secrets`, `_SECRET_PATTERNS`) — Always-on audit-side scan with a **prefix-anchored regex set** (the sole detection backend in this release) covering AWS access keys (`AKIA…` / `ASIA…`), GitHub PATs (`ghp_`, `gho_`, `ghs_`, `ghu_`, `ghr_`, `github_pat_`), Slack tokens, OpenAI API keys (`sk-…` / `sk-proj-…`), Google API keys, JWTs anchored on canonical header alphabet, full OpenSSH / RSA / DSA / EC / PGP private-key blocks (BEGIN through END inclusive — `mask_secrets` redacts the entire block, not just the header line), and Azure storage connection strings. Adds a `secrets_summary` block alongside `pii_summary`. Ingest side: `forgelm ingest --secrets-mask` rewrites detected spans with `[REDACTED-SECRET]`; runs **before** PII masking as a defensive ordering so future overlapping detectors (PII vs secret regex) can't double-count. The optional `[ingestion-secrets]` extra (`detect-secrets>=1.5.0`) is reserved for a follow-up release — the current code does **not** invoke the `detect-secrets` package (its plugin model assumes file paths, not streaming chunks); install only as forward-compatibility for the eventual integration.
- **Heuristic quality filter** (`forgelm/data_audit.py`: `_row_quality_flags`, `_QUALITY_CHECKS`) — Opt-in `forgelm audit --quality-filter` runs Gopher / C4 / RefinedWeb-style checks per row: `low_alpha_ratio` (< 70 % letters among non-whitespace), `low_punct_endings` (< 50 % of non-empty lines end with punctuation), `abnormal_mean_word_length` (outside 3–12 chars), `short_paragraphs` (> 50 % of `\n\n`-blocks have < 5 words). Surfaces `quality_summary` with per-check counts, `samples_flagged`, and `overall_quality_score`. ML-based classifiers (fastText / DeBERTa) deliberately out of scope — keeps the audit deterministic for Annex IV reproducibility.
- **DOCX / Markdown table preservation** (`forgelm/ingestion.py`: `_docx_table_to_markdown`) — `_extract_docx` now renders tables as markdown table syntax (header row + `---` separator + body rows) instead of the previous `" | "`-joined flat line. Uneven rows are right-padded with empty cells; all-blank rows are dropped; the first non-empty row becomes the header (no heuristic — that's the convention DOCX authors use). Combined with `--strategy markdown` the table block stays intact across chunks.

### Public API additions

- `AuditReport` gains `secrets_summary: Dict[str, int]` and `quality_summary: Dict[str, Any]` fields (additive — Phase 11/11.5 consumers reading just `pii_summary` / `near_duplicate_summary` keep working).
- `IngestionResult` gains `secrets_redaction_counts: dict` field.
- `audit_dataset(...)` accepts `dedup_method`, `minhash_jaccard`, `minhash_num_perm`, `enable_quality_filter` keyword arguments.
- `ingest_path(...)` accepts `secrets_mask: bool` keyword argument.
- New constants: `DEDUP_METHODS`, `DEFAULT_MINHASH_JACCARD`, `DEFAULT_MINHASH_NUM_PERM`, `SECRET_TYPES`.

### CLI additions

- `forgelm ingest`: `--strategy markdown`, `--secrets-mask`.
- `forgelm audit`: `--dedup-method {simhash,minhash}`, `--jaccard-threshold X` (validated to `[0.0, 1.0]` at parse time), `--quality-filter`.
- New argparse type helper `_non_negative_float` (mirrors `_non_negative_int`'s pattern).
- `_run_data_audit` now distinguishes `EXIT_CONFIG_ERROR` (filesystem/path errors) from `EXIT_TRAINING_ERROR` (missing `[ingestion-scale]` extra when `--dedup-method=minhash` was requested).

### `pyproject.toml`

- New extras: `[ingestion-scale]` (`datasketch>=1.6.0,<2.0.0`), `[ingestion-secrets]` (`detect-secrets>=1.5.0,<2.0.0`).
- Version bump `0.5.1rc1 → 0.5.2rc1`.

### Tests

- `tests/test_data_audit_phase12.py` — 18 new tests across `TestSecretsDetection`, `TestSecretsMasking`, `TestAuditPicksUpSecrets`, `TestQualityFilterPerRow`, `TestQualityFilterEnabled`, `TestMinHashLshDedup` (skipped without `datasketch`), `TestMinHashMissingExtra`.
- `tests/test_ingestion_phase12.py` — 13 new tests across `TestMarkdownSections`, `TestChunkMarkdown`, `TestMarkdownStrategyExposed`, `TestDocxTableToMarkdown`, `TestSecretsMaskIngest`.
- `tests/test_cli_subcommands.py` — `test_audit_quality_filter_flag`, `test_audit_rejects_invalid_jaccard_threshold` added to `TestAuditSubcommand`.

### Changed (no behavioural delta unless noted)

- `_StreamingAggregator` gains `minhashes`, `secrets_counts`, `quality_flags_counts`, `quality_samples_flagged`, `dedup_method`, `minhash_num_perm`, `enable_quality_filter` fields. Field rename: `_SplitOutcome.fingerprints` → `_SplitOutcome.signatures` (the same field carries simhash ints OR MinHash instances, depending on method).
- `_audit_split(...)` now returns `(info, signatures, pii_split, parse_errors, decode_errors)` where `signatures` is method-dependent. `_process_split` and `audit_dataset` were updated in lockstep.
- `_pair_leak_payload` and `_cross_split_overlap` switched to keyword-only `dedup_method` parameter and dispatch on it (simhash → Hamming; minhash → Jaccard).
- `describe_strategies()` now lists `markdown` alongside `sliding` / `paragraph` / `semantic`.

### Added — Phase 11.5 (Ingestion / Audit Polish, targeting v0.5.1)

Operational polish on top of `v0.5.0`'s ingestion + audit surface — no new training capabilities, but materially better handling for large corpora and a cleaner CLI shape. All 12 follow-ups carved out of Phase 11's review backlog.

- **`forgelm audit PATH` subcommand** — Promotes the `--data-audit` top-level flag to a first-class subcommand with `--verbose`, `--near-dup-threshold`, and its own `--output` default (`./audit/`). The legacy `forgelm --data-audit PATH` flag keeps working as a deprecation alias and logs a one-line notice; existing CI pipelines need no changes. Removal targeted no earlier than `v0.7.0`.
- **LSH-banded near-duplicate detection** (`find_near_duplicates`, `_count_leaked_rows`) — Pigeonhole-banded LSH (default `bands = threshold + 1`) drops within-split + cross-split scans from `O(n²)` to ~`O(n × k)`. Recall stays exact at the default Hamming threshold; brute-force fallback remains for edge thresholds where bands shrink below 4 bits. Unblocks audits on 100K+ row corpora.
- **Streaming `_read_jsonl_split`** — The audit's JSONL reader is now a generator yielding `(row, parse_err, decode_err)`; `_audit_split` consumes it row-by-row via a `_StreamingAggregator` so RAM stays bounded on multi-million-row splits. Per-line tolerance semantics (parse errors, decode errors, non-dict rows) preserved.
- **Token-aware ingestion** (`--chunk-tokens`, `--tokenizer`, `--overlap-tokens`) — Optional flags on `forgelm ingest` size chunks against an HF `AutoTokenizer.encode` instead of raw character counts, so chunks line up with `model.max_length` exactly. `--tokenizer` is required with `--chunk-tokens` (we refuse to default to a hidden vocab because the chunk count would silently differ per-model). `trust_remote_code=False` is hard-pinned for safety.
- **PDF page-level header / footer dedup** (`_strip_repeating_page_lines`) — Lines that recur as the first or last non-empty line on ≥ 70 % of a PDF's pages (company watermarks, page numbers, copyright lines) are stripped automatically before chunking. Reduces audit `near_duplicate_pairs` noise on long policy / book PDFs. Skipped on documents shorter than 3 pages.
- **PII severity tiers** — Audit JSON now carries a `pii_severity` block (`total`, `by_tier`, `by_type`, `worst_tier`) alongside the flat `pii_summary`. Tiers map regulatory weighting: `credit_card` / `iban` → critical (PCI-DSS), national IDs → high (GDPR Art. 9), `email` → medium, `phone` → low. The aggregate notes line leads with the worst tier (`WORST tier: CRITICAL`) so reviewers cannot miss it.
- **`summarize_report` truncation policy** — Default `verbose=False` folds zero-finding splits into a single tail line so multi-split summaries stay short; `--verbose` on the new `audit` subcommand reverses this for full output. Has no effect on the on-disk JSON report.
- **Structured ingestion notes** — `IngestionResult.extra_notes` keeps the human-readable list; new `notes_structured: {key: value}` (and an explicit `pdf_header_footer_lines_stripped` field) carries machine-readable counts for CI/CD consumers. JSON output exposes both.
- **Wizard "ingest first" entry point** — `_offer_ingest_for_directory` + `_prompt_dataset_path_with_ingest_offer`: BYOD quickstart and the full 8-step wizard now offer to run `forgelm ingest` inline when the typed dataset path is a directory of raw documents, then feed the produced JSONL straight back into the BYOD path. Closes the onboarding loop end-to-end.
- **xxhash backend for simhash + token-level memo** — Optional `xxhash.xxh3_64` digest path (added to `forgelm[ingestion]`); BLAKE2b stays as the fallback. The Python-level speedup is modest (~1.3× raw, ~1.05× end-to-end after the cache below absorbs Zipfian repeats — xxhash's "4-10×" figure refers to C-level pure-hash microbenchmarks, not the Python wrapping path). The bigger wall-clock win is the new module-scope `lru_cache(maxsize=10_000)` that memoises the per-token digest — most corpora's token traffic is dominated by a few thousand frequent tokens, so the cache hit rate is very high.
- **Atomic audit-report write** — `data_audit_report.json` is now written via `tempfile.NamedTemporaryFile` + `os.replace` so a crashed audit can never leave a half-written report on disk. `newline="\n"` pinned for byte-exact reproducibility across Windows / Linux / macOS.

### Tests

- `tests/test_data_audit.py` — `TestLshBandedNearDuplicates` (LSH parity vs. brute force + high-threshold fallback), `TestPiiSeverity` (critical-tier verdict + neutral case), `TestSummarizeVerbosePolicy` (clean splits folded vs. expanded), `TestAtomicWrite` (no `.tmp` leftovers), `TestStreamingReader` (per-line tuple yields), `TestTokenCachePerformance` (cross-text cache hits).
- `tests/test_ingestion.py` — `TestPdfHeaderFooterDedup` (multi-page header/footer collapse, short-doc skip, no-repeats pass-through), `TestStructuredIngestionNotes`, `TestTokenAwareCli` (validates the `--chunk-tokens` requires `--tokenizer` rule).
- `tests/test_cli_subcommands.py` — `TestAuditSubcommand` (subcommand happy path, JSON envelope, legacy `--data-audit` alias).
- `tests/test_wizard_byod.py` — refreshed for the new ingest-first wording (empty directory rejection, decline-the-ingest-offer path).

### Changed — Phase 11 (no behavioural delta unless noted)

- `AuditReport` gains a `pii_severity: Dict[str, Any]` field. JSON consumers reading only `pii_summary` continue to work; the new field is additive.
- `find_near_duplicates(fingerprints, *, threshold, bits=64)` accepts a `bits` keyword for adaptive banding (default 64 matches `compute_simhash`).
- `_read_jsonl_split` is now a generator. The legacy buffered tuple return is gone — callers that were materialising rows can wrap with `list(...)`.
- `_audit_split(split_name, path, ...)` now takes a path instead of an in-memory list; `_process_split` calls it directly. Returns `(info, fingerprints, pii_split, parse_errors, decode_errors)` so OSError handling stays in the orchestrator.

### Previously added — Phase 11

**Document Ingestion & Data Audit (Phase 11)** — bridges raw enterprise corpora (legal, medical, policy manuals) to ForgeLM's training data format and surfaces governance signals before training starts.

- **`forgelm/ingestion.py`** + **`forgelm ingest`** subcommand:
  - Multi-format extractors: PDF (`pypdf`), DOCX (`python-docx`), EPUB (`ebooklib` + `beautifulsoup4`), TXT, Markdown.
  - Two chunking strategies: `paragraph` (default; greedy, never splits a paragraph) and `sliding` (fixed window with `--overlap`). `semantic` raises `NotImplementedError` and is reserved for a follow-up phase.
  - Output is `{"text": "..."}` JSONL — recognized as pre-formatted SFT input by `forgelm/data.py` without further preprocessing.
  - `--recursive` walks directory trees; unsupported extensions are skipped silently, supported files with no extractable text skip with a warning.
  - `--pii-mask` redacts detected PII spans before chunks land in the JSONL (shared regex set with the audit module).
  - OCR is intentionally out of scope; scanned PDFs without a text layer warn and produce zero chunks.

- **`forgelm/data_audit.py`** + **`forgelm --data-audit`** top-level flag:
  - Per-split metrics: sample count, column schema, text length distribution (`min/max/mean/p50/p95`), null/empty rate, top-3 language detection (best-effort via `langdetect`).
  - 64-bit simhash near-duplicate detection within each split; Hamming-distance threshold 3 ≈ 95% similarity (canonical web-page-dedup setting).
  - Cross-split overlap report — guards against silent train-test leakage that destroys benchmark fidelity.
  - PII regex set (`email`, `phone`, `credit_card` Luhn-validated, `iban`, `tr_id` checksum-validated, `de_id`, `fr_ssn`, `us_ssn`); per-split + aggregate counts.
  - Layout: single `.jsonl` file → treated as `train`; directory containing `train.jsonl` / `validation.jsonl` / `test.jsonl` (any subset) auto-discovered.
  - Writes `data_audit_report.json` under `--output` (default `./audit/`); `--output-format json` mirrors the report on stdout for CI/CD pipelines.
  - CPU-only; no GPU, no network.

- **EU AI Act Article 10 integration** — `generate_data_governance_report` now inlines `data_audit_report.json` under the `data_audit` key when present in the trainer's `output_dir`. Compliance bundle becomes a single self-contained document instead of a pointer.

- **`pyproject.toml` `[ingestion]` extra** — `pypdf`, `python-docx`, `ebooklib`, `beautifulsoup4`, `langdetect`. Cross-platform, no native compilation.

- **Tests** — `tests/test_ingestion.py` (TXT path + chunking strategies; PDF round-trip skips when `pypdf` missing) and `tests/test_data_audit.py` (PII regex + Luhn / TC Kimlik validators, simhash properties, end-to-end audit on file + split-keyed directory layouts, governance integration). All GPU/network-free.

- **Documentation** — new guides at `docs/guides/ingestion.md` and `docs/guides/data_audit.md`; README feature section, CLI epilog, install matrix, and roadmap status updated.

---

## [0.4.5] — 2026-04-26

### Added

**Quickstart Layer (Phase 10.5)** — One-command bundled templates with opinionated defaults. Primary community-growth driver: closes the gap between "I just installed ForgeLM" and "I have a fine-tuned model running locally."

- **`forgelm/quickstart.py`** — Template registry + orchestrator:
  - `Template` (frozen dataclass) — `name`, `title`, `description`, `primary_model`, `fallback_model`, `trainer_type`, `estimated_minutes`, `min_vram_for_primary_gb`, `bundled_dataset`, `license_note`.
  - `TEMPLATES: Dict[str, Template]` — 5 entries: `customer-support`, `code-assistant`, `domain-expert`, `medical-qa-tr`, `grpo-math`.
  - `auto_select_model(template, available_vram_gb)` — picks primary model when VRAM ≥ threshold (10–12 GB), fallback otherwise; explicit `no-gpu-detected` reason when CUDA is absent.
  - `_detect_available_vram_gb()` — wraps `torch.cuda.mem_get_info()`; returns `None` when no GPU (test mock point).
  - `run_quickstart(template_name, *, model_override, dataset_override, output_path, dry_run, available_vram_gb)` → `QuickstartResult` — copies seed dataset, substitutes `model.name_or_path` and `data.dataset_name_or_path`, writes `configs/<template>-YYYYMMDDHHMMSS.yaml`. Generated YAML is identical in shape to a hand-written one — same trainer, same schema.
  - `format_template_list()`, `summarize_result(result)` — text/JSON renderers for CLI use.

- **`forgelm quickstart <template>` CLI subcommand** (in `forgelm/cli.py`):
  - `--list` — prints the registry; honors top-level `--output-format json` for CI.
  - `--model <id>` — override auto-selected model.
  - `--dataset <path>` — override the bundled seed dataset (required for `domain-expert`).
  - `--output <path>` — custom YAML output path (default: `./configs/<template>-<timestamp>.yaml`).
  - `--dry-run` — generate config only; skip training and chat.
  - `--no-chat` — train but skip the post-training chat REPL.
  - On a successful run, subprocess-invokes `forgelm --config <out>` and then `forgelm chat <output_dir>` (unless `--no-chat`).

- **Wizard integration** — `forgelm --wizard` now opens with "Start from a template?":
  - Yes → routes to the quickstart selector; the wizard becomes a thin shell over `run_quickstart()`.
  - No → falls through to the existing 8-step interactive flow.
  - No bifurcation: identical code paths and YAML schema downstream.

- **5 bundled templates** under `forgelm/templates/`:
  - `customer-support/` — Qwen2.5-7B-Instruct primary, SmolLM2-1.7B-Instruct fallback. SFT trainer. 58-example seed JSONL in `{"messages": [...]}` format.
  - `code-assistant/` — Qwen2.5-Coder-7B-Instruct primary, Qwen2.5-Coder-1.5B-Instruct fallback (code-tuned smaller variant, not generic SmolLM2). SFT. 59-example Python/programming Q&A.
  - `domain-expert/` — Qwen2.5-7B-Instruct primary, SmolLM2-1.7B-Instruct fallback. BYOD; empty data with a README explaining how to pair with `forgelm ingest` (Phase 11) or a custom JSONL.
  - `medical-qa-tr/` — Qwen2.5-7B-Instruct primary, Qwen2.5-1.5B-Instruct fallback (Turkish-capable, not English-only SmolLM2). SFT, 49 Turkish Q&A; every answer ends with "Tıbbi acil durumlarda 112'yi arayın..." (medical-disclaimer guardrail).
  - `grpo-math/` — Qwen2.5-Math-7B-Instruct primary, Qwen2.5-Math-1.5B-Instruct fallback. GRPO trainer (`grpo_num_generations: 4`). 40 grade-school math word problems in prompt-only format, each carrying a `gold_answer` field for the built-in regex correctness reward.

- **Conservative defaults** in every template config:
  - QLoRA 4-bit NF4, LoRA rank=8, `per_device_train_batch_size=1`, gradient checkpointing on, safety eval / compliance artifacts opt-in only.
  - Designed so the smallest fallback model + the bundled seed dataset run end-to-end on a 12 GB consumer GPU.

- **`forgelm/templates/LICENSES.md`** — Full attribution for bundled seed datasets (CC-BY-SA 4.0, author-original); contributing guide for new templates; medical-disclaimer note for `medical-qa-tr`.

- **`pyproject.toml` `[tool.setuptools.package-data]`** — bundles `*.yaml`, `*.jsonl`, `*.md` under `forgelm.templates` into the wheel so `pip install forgelm` users get the templates without a source checkout.

- **GRPO baseline reward** — `forgelm/grpo_rewards.py` ships a default reward bundle so prompt-only datasets don't crash inside `trl.GRPOTrainer`. When `grpo_reward_model` is unset the trainer wires `combined_format_length_reward` (0.8 × format-match + 0.2 × length-shaping); if the dataset additionally carries a `gold_answer` field (the bundled `grpo-math` seed does), `_math_reward_fn` is appended so TRL sums correctness on top of format teaching.

- **Tests** — All GPU-independent via TRL/torch FSDP-aware skip-if pattern:
  - `tests/test_quickstart.py` — registry consistency, bundled-asset shape, `auto_select_model` primary/fallback/no-gpu, end-to-end `run_quickstart`, CLI dispatch, regression test that loads every generated YAML through `load_config` (strongest guard against template drift).
  - `tests/test_quickstart_hardening.py` — PR review hardening (path validation, model override edges, dry-run wiring).
  - `tests/test_grpo_math_reward.py` — pure-Python unit tests for `_normalize_answer`, `_answers_match`, `_math_reward_fn`, `_dataset_has_gold_answers`.
  - `tests/test_grpo_format_reward.py` — `format_match_reward`, `length_shaping_reward`, `combined_format_length_reward`, plus trainer integration.
  - `tests/test_wizard_byod.py` — wizard BYOD dataset path validation (existence, directory, malformed JSONL, valid JSONL, HF Hub IDs, `~` expansion).
  - `tests/test_cli_quickstart_wiring.py` — `--offline` propagation, separate chat inheritance, chat exit-code 0/130 handling.
  - `tests/test_packaging.py` — wheel `package_data` smoke (catches editable-install-only template paths).
  - `tests/test_grpo_reward.py` — extended with no-reward-model + gold-answer wiring assertions.

- **CI** — `.github/workflows/nightly.yml`:
  - Per-template quickstart smoke (4 of 5 — `domain-expert` is BYOD and covered by pytest).
  - New `wheel-install-smoke` job: builds the wheel, installs it into a fresh venv from `/tmp` (so the source tree is off `sys.path`), and reruns `quickstart --list` + `quickstart --dry-run` to catch broken `package_data` globs that editable installs hide.

### Documentation

- New "Option 0: One-Command Quickstart Template" section at the top of `docs/guides/quickstart.md`.
- `docs/roadmap.md`, `docs/roadmap-tr.md`, `docs/roadmap/completed-phases.md`, `docs/roadmap/releases.md` updated to mark Phase 10.5 as Done.
- `README.md` quickstart section updated to lead with `forgelm quickstart`.

---

## [0.4.0] — 2026-04-26

### Added

**Post-Training Completion (Phase 10)**

- **`forgelm/inference.py`** — Shared generation primitives for all post-training features:
  - `load_model(path, adapter, backend, load_in_4bit, load_in_8bit, trust_remote_code)` — loads HF model + tokenizer; optional PEFT adapter merge via `merge_and_unload()`; unsloth backend support
  - `generate(model, tokenizer, prompt, *, messages, system_prompt, history, max_new_tokens, temperature, top_k, top_p, repetition_penalty)` — non-streaming text generation
  - `generate_stream(...)` — streaming via `TextIteratorStreamer` in daemon thread; yields token chunks
  - `logit_stats(logits)` — returns `{entropy, top1_prob, effective_vocab}` for token-level confidence inspection
  - `adaptive_sample(logits, temperature, top_k, top_p, entropy_threshold)` — greedy below entropy threshold, nucleus sampling above
  - `_build_prompt` — uses `tokenizer.apply_chat_template` when available; falls back to `"role: content\n"` join

- **`forgelm/chat.py`** — Interactive terminal REPL (`ChatSession` class + `run_chat()` entry point):
  - Streaming output by default; `--no-stream` flag for non-streaming
  - Slash commands: `/reset`, `/save [file]`, `/temperature N`, `/system [prompt]`, `/help`, `/exit`
  - History management with 50-turn cap (`_MAX_HISTORY_PAIRS`)
  - Optional `rich` rendering via `pip install forgelm[chat]`
  - Optional `--safety` flag routes each response through Llama Guard

- **`forgelm/fit_check.py`** — VRAM pre-flight advisor:
  - `estimate_vram(config)` → `FitCheckResult(verdict, estimated_gb, available_gb, breakdown, recommendations)`
  - Verdicts: `FITS` (< 85% GPU), `TIGHT` (85-95%), `OOM` (> 95%), `UNKNOWN` (no GPU)
  - Architecture loaded via `transformers.AutoConfig`; fallback size-hint dict for 7b/8b/13b/70b families
  - VRAM components: base weights + LoRA adapter + optimizer state (AdamW/8-bit/GaLore-aware) + activations (gradient-checkpointing divides by √layers)
  - `format_fit_check(result)` — human-readable summary; `--output-format json` for CI/CD
  - Hypothetical mode when no CUDA detected — still estimates based on architecture

- **`forgelm/export.py`** — GGUF model export:
  - `export_model(model_path, output_path, *, format, quant, adapter, update_integrity, extra_args)` → `ExportResult`
  - Wraps `llama-cpp-python`'s `convert_hf_to_gguf.py` — no reimplementation of conversion logic
  - Supported quantizations: `q2_k`, `q3_k_m`, `q4_k_m`, `q5_k_m`, `q8_0`, `f16`
  - **K-quant note**: `q2_k`/`q3_k_m`/`q4_k_m`/`q5_k_m` require a two-step flow.
    `forgelm export ... --quant q4_k_m model.gguf` produces an intermediate
    `model.f16.gguf`; run `llama-quantize model.f16.gguf model.gguf Q4_K_M`
    afterward to obtain the K-quant. The `ExportResult.quant` field reflects
    what was actually written (so `model_integrity.json` SHA-256 stays honest)
  - Adapter merge: loads base + PEFT, saves merged fp16 weights before conversion
  - `_sha256_file` — chunked 64 KB reads for large models
  - `_update_integrity_manifest` — appends export artifact (path, quant, sha256, size_bytes) to `model_integrity.json`
  - Optional dependency: `pip install forgelm[export]` (`llama-cpp-python>=0.2.90`)

- **`forgelm/deploy.py`** — Deployment config file generation:
  - `generate_deploy_config(model_path, target, output_path, *, system_prompt, max_length, temperature, top_k, top_p, ...)` → `DeployResult`
  - Target `ollama`: Modelfile with FROM, SYSTEM (double-quote escaped), PARAMETER directives
  - Target `vllm`: YAML engine config with GPU memory utilization, dtype, trust_remote_code
  - Target `tgi`: docker-compose.yaml with GPU resource reservation, port mapping, max-input/total-length
  - Target `hf-endpoints`: JSON spec with model repository, task, compute instance, region, framework
  - Case-insensitive target matching; default output filenames per target

- **CLI subcommands** (`forgelm/cli.py`):
  - `forgelm chat MODEL_PATH [--adapter] [--system] [--temperature] [--max-new-tokens] [--safety] [--no-stream] [--load-in-4bit] [--load-in-8bit] [--trust-remote-code] [--backend]`
  - `forgelm export MODEL_PATH --output FILE [--format gguf] [--quant q4_k_m] [--adapter] [--no-integrity-update]`
  - `forgelm deploy MODEL_PATH --target TARGET [--output FILE] [--system] [--max-length] [--temperature] [--top-k] [--top-p] [--trust-remote-code]`
  - `forgelm --config CONFIG --fit-check [--output-format json]`
  - All subcommands work without `--config`; backward-compatible with existing flat CLI

- **Optional extras** in `pyproject.toml`:
  - `forgelm[export]` — `llama-cpp-python>=0.2.90` (non-Windows)
  - `forgelm[chat]` — `rich>=13.0.0`

- **New test modules**:
  - `tests/test_inference.py` — 16 tests covering `_build_prompt`, `_to_messages`, `logit_stats`, `adaptive_sample`, `load_model`, `generate` with custom torch stub (no GPU required)
  - `tests/test_fit_check.py` — 18 tests covering parameter estimation, VRAM components, GPU scenarios (no CUDA, 4 GB, 80 GB), `format_fit_check`
  - `tests/test_export.py` — 12 tests covering SHA-256, integrity manifest, GGUF export flow with subprocess mock
  - `tests/test_deploy.py` — 21 tests covering all 4 target generators and `generate_deploy_config` integration
  - `tests/test_cli_phase10.py` — 22 tests covering `--fit-check`, all deploy targets, export subcommand, chat subcommand, subcommand routing

### Changed

- **`forgelm/__init__.py`** — version bumped to `0.4.0`
- **`forgelm/cli.py`** — added subparser architecture with `chat`, `export`, `deploy` subcommands; added `--fit-check` flag; `KeyboardInterrupt` caught in chat dispatch for graceful exit
- **`forgelm/wizard.py`** — (no changes needed; Phase 10 features are all CLI-driven, not wizard-driven)

### Breaking

- **`forgelm.compliance.export_compliance_artifacts`** signature changed from
  `(manifest, config, output_dir)` to `(manifest, output_dir)`. The `config`
  argument was unused (the manifest already contains all derived values).
  External callers must drop the second positional argument.
- **`forgelm.export.export_model`** keyword `format=` renamed to
  `output_format=` to avoid shadowing the `format` builtin. Update
  `export_model(..., format="gguf", ...)` → `export_model(...,
  output_format="gguf", ...)`.
- **`forgelm.deploy.generate_deploy_config`** parameter list collapsed from
  18 → 11 args. The HF Endpoints fields (task/instance_size/instance_type/
  region/framework/vendor) are now grouped as
  `hf_endpoints: HFEndpointsOptions = None`; sampling defaults
  (temperature/top_k/top_p) are grouped as
  `sampling: SamplingOptions = None`. Pass instances of those dataclasses
  instead of the individual kwargs.

---

## [0.3.1rc1] — 2026-03-28 (included in v0.4.0 branch)

### Added
- **Engineering standards** (`docs/standards/`) — 9 standard documents: coding, architecture, error-handling, logging-observability, testing, documentation, localization, code-review, release.
- **AI agent skills** (`.claude/skills/`) — 6 task-specific SKILL.md checklists: add-config-field, add-trainer-feature, add-test, sync-bilingual-docs, cut-release, review-pr.
- **CLAUDE.md** — Root-level AI agent guidance file with non-negotiable project principles, skill table, and repo structure map.
- **Phase 10-13 planning docs** (`docs/roadmap/phase-*.md`) — Detailed planning for Post-Training Completion, Data Ingestion, Quickstart Layer, and Pro CLI.

### Changed
- **docs/ reorganization** — Reference docs moved to `docs/reference/`, design specs to `docs/design/`. All internal links updated (29 link fixes).
- **Roadmap refactored** — `docs/roadmap.md` reduced from 910 to 78 lines; phase details moved to `docs/roadmap/` subdirectory.

### Fixed (Security & Config Hardening)
- Webhook URLs excluded from HuggingFace Hub model cards — prevents credential leaks
- User-supplied strings sanitized before Markdown template embedding (content injection prevention)
- All 19 Pydantic sub-models enforce `extra="forbid"` — YAML typos are errors, not silent bugs
- Deprecated `lora.use_dora` / `lora.use_rslora` booleans auto-normalize to `lora.method` with warnings
- Audit log hash chain restores continuity across process restarts
- Compliance manifests correctly report pre-OOM-recovery batch size
- GRPO reward model path correctly wrapped as callable
- Safety classifier receives full `[INST] prompt [/INST] response` context
- Extension-less files raise clear `ValueError` instead of silently loading wrong format
- TIES tie-breaking fixed; DARE now deterministic with `seed=42`

## [0.3.0] — 2026-03-28

### Added

**GaLore Optimizer Integration**
- Full-parameter training via gradient low-rank projection — alternative to LoRA
- 6 optimizer variants: `galore_adamw`, `galore_adamw_8bit`, `galore_adafactor`, + layerwise versions
- Configurable rank, update_proj_gap, scale, proj_type, target_modules
- Validation: layerwise + multi-GPU incompatibility detection, LoRA co-existence warning

**Long-Context Optimizations**
- RoPE scaling support: linear, dynamic, YaRN, LongRoPE with configurable factor
- NEFTune noise injection (`neftune_noise_alpha`) for improved training quality
- Sliding window attention override for Mistral-family models
- Sample packing for efficient short-sequence training

**Synthetic Data Pipeline**
- Teacher→student distillation with `--generate-data` CLI command
- Three teacher backends: API (OpenAI-compatible), local (HuggingFace model), file (pre-generated)
- Configurable system prompt, temperature, max_new_tokens, rate limiting
- Four output formats: messages (chat), instruction, chatml, prompt_response
- Seed prompts from JSONL file or inline config

**GPU Cost Estimation**
- Auto-detection for 18 GPU models (T4, A100, H100, RTX 4090, etc.)
- Per-run cost calculation based on training duration and GPU type
- Manual override via `training.gpu_cost_per_hour`

**CI/CD & Publishing**
- PyPI publishing workflow (`.github/workflows/publish.yml`) — `pip install forgelm`
- Nightly compatibility testing (`.github/workflows/nightly.yml`)
- Expanded adversarial prompt library: 140 prompts across 6 categories (was 50/3)

**Wizard Enhancements**
- GaLore strategy option with rank and optimizer selection
- Long-context auto-detection (max_length > 4096) with RoPE scaling prompt
- NEFTune noise injection option

### Fixed
- SFTConfig `max_length` → `max_seq_length` for TRL compatibility
- `device_map={"":0}` for single GPU without 4-bit (prevents model splitting)
- Gradient checkpointing disabled on CPU (requires CUDA)
- Pre-formatted `text` column datasets now properly handled
- Chat template applied during inference in notebooks

### Changed
- Version bump: 0.2.0 → 0.3.0
- All notebooks use SmolLM2-135M for faster Colab testing (was 1.7B)
- Notebooks include base vs fine-tuned model comparison
- 297 tests (up from 242), 0 lint errors

---

## [0.2.0] — 2026-03-26

Major release: ForgeLM goes from a basic SFT fine-tuning tool to a full-stack LLM training platform with alignment, distributed training, safety evaluation, and EU AI Act compliance.

### Added

**Alignment & Post-Training Stack**
- 6 trainer types: SFT, DPO, SimPO, KTO, ORPO, GRPO
- Per-trainer hyperparameters (`dpo_beta`, `kto_beta`, `grpo_num_generations`, etc.)
- Dataset format auto-detection with trainer_type mismatch suggestions

**Distributed Training**
- DeepSpeed ZeRO-2, ZeRO-3, ZeRO-3+Offload presets
- FSDP support with sharding strategies (FULL_SHARD, SHARD_GRAD_OP)
- Unsloth + distributed conflict detection

**Safety & Evaluation**
- Safety classifier gate (Llama Guard) with binary and confidence-weighted scoring
- S1-S14 harm category breakdown with severity levels (critical/high/medium/low)
- Low-confidence alert system for uncertain classifications
- Cross-run safety trend tracking (`safety_trend.jsonl`)
- LLM-as-Judge scoring (API and local model support)
- Automated benchmark evaluation via lm-evaluation-harness
- Built-in adversarial prompt library (50 prompts across 8 categories)
- Human approval gate (`require_human_approval`, exit code 4)

**EU AI Act Compliance (Articles 9-17)**
- Annex IV technical documentation generator
- Structured audit event log (`audit_log.jsonl`) with hash chaining
- Risk assessment declaration (risk level, domain, mitigations)
- Data governance reporting (source, quality, bias mitigation)
- Model integrity verification (SHA-256 checksums for all artifacts)
- Deployer instructions generator (Article 13)
- Evidence bundle export (ZIP archive for auditors)
- QMS SOP templates (5 documents: training, validation, monitoring, change, incident)
- Post-market monitoring configuration scaffold

**Model Capabilities**
- MoE fine-tuning support (expert quantization, selective training)
- Multimodal VLM pipeline detection
- Model merging: TIES, DARE, SLERP, linear interpolation
- Advanced PEFT methods: PiSSA, rsLoRA, DoRA
- Automatic model card generation (HuggingFace format)

**CLI & UX**
- `--wizard` interactive config generator with GPU detection
- `--dry-run` config validation (JSON and text output)
- `--benchmark-only` evaluate existing models without training
- `--merge` standalone model merging
- `--compliance-export` generate audit artifacts
- `--quiet` suppress INFO logs
- `--offline` air-gapped mode (HF_HUB_OFFLINE)
- `--resume` checkpoint resume (auto-detect or explicit path)
- `--output-format json` machine-readable output
- `--log-level` configurable logging
- Exit codes: 0 (success), 1 (config error), 2 (training error), 3 (eval failure), 4 (awaiting approval)

**Infrastructure**
- Docker multi-stage build + docker-compose (training + TensorBoard)
- CI pipeline: 3 parallel jobs (lint, test matrix 3.10/3.11/3.12, validate)
- Ruff linting + formatting enforced
- 242 unit tests across 20 test files
- Branch protection rules on main
- GitHub issue templates (bug report, feature request) + PR template
- Apache License 2.0
- CONTRIBUTING.md + CODE_OF_CONDUCT.md

**Documentation**
- 6 user guides (quickstart, alignment, CI/CD, enterprise, safety, troubleshooting)
- 5 Colab-ready notebooks (SFT, DPO, KTO, GRPO, multi-dataset)
- Full EN/TR documentation (architecture, configuration, usage, roadmap)

### Changed

- Structured logging (`logging` module) replaces all `print()` calls
- Config validation via Pydantic v2 with `extra="forbid"` (typos caught)
- `trust_remote_code` now configurable via YAML (default: false)
- `bf16`/`fp16` auto-detected based on GPU capability
- `no_cuda` replaced with `use_cpu` (HF deprecation)
- `device_map` uses `{"": 0}` on single GPU without 4-bit (prevents model splitting)
- `gradient_checkpointing` auto-disabled on CPU
- `num_proc` for dataset processing scales with CPU count
- `enable_input_require_grads` always called for LoRA compatibility
- Dependency upper bounds pinned to prevent breaking changes
- `max_length` → `max_seq_length` for TRL SFTConfig compatibility
- `text` column datasets supported without reformatting

### Fixed

- 54 code review findings resolved (4 critical, 12 high, 19 medium, 14 low)
- Silent exception handling eliminated across all modules
- MoE expert quantization no longer corrupts weights (was using int8 cast)
- SLERP merge saves/restores base state correctly
- Webhook sanitizes metrics to numeric values only
- DARE merge handles `drop_rate >= 1.0` without division by zero
- Early stopping callback only added when validation data exists
- Audit log uses hash chaining for tamper evidence
- Model integrity hashes all files recursively (not just top-level)
- Checkpoint cleanup only removes `checkpoint-*` dirs (not entire output_dir)

## [0.1.0] — 2026-01-15

### Added

- Initial release
- SFT fine-tuning with TRL SFTTrainer
- LoRA/QLoRA (4-bit NF4) via PEFT
- Unsloth backend support
- DoRA adapter support
- YAML-based configuration
- Webhook notifications (Slack/Teams)
- Model versioning
- Basic evaluation checks (max loss, baseline comparison)
- Auto-revert on quality degradation

[Unreleased]: https://github.com/HodeTech/ForgeLM/compare/v0.9.0...HEAD
[0.9.0]: https://github.com/HodeTech/ForgeLM/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/HodeTech/ForgeLM/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/HodeTech/ForgeLM/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/HodeTech/ForgeLM/compare/v0.5.7...v0.6.0
[0.5.7]: https://github.com/HodeTech/ForgeLM/compare/v0.5.6...v0.5.7
[0.5.6]: https://github.com/HodeTech/ForgeLM/compare/v0.5.5...v0.5.6
[0.5.5]: https://github.com/HodeTech/ForgeLM/compare/v0.5.0...v0.5.5
