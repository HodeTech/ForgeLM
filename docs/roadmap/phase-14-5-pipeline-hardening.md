# Phase 14.5: Pipeline Hardening (post-release review deferrals)

> **Status:** **Tasks 1-4 delivered** in the `v0.9.x` cycle (unreleased at the time of writing; entries sit under `[Unreleased]` in [`CHANGELOG.md`](../../CHANGELOG.md)).  **Task 5 (the SonarCloud S3776 cognitive-complexity refactor) is NOT delivered** — it was never part of the four v0.7.0 release-cut deferrals this phase was created for, and was appended to this file later.  Do not read "Phase 14.5 shipped" as "this file is closed".  As of 2026-07-20 Task 5 is additionally recorded as **not scheduled and not to be executed as originally specified**: re-measurement found the entry's counts, function list, file:line references and acceptance criterion all wrong.  It is gated on a stated condition, not on a version — see the task body.
>
> Originally targeted at `v0.7.x`; the cycle closed and both v0.8.0 and v0.9.0 shipped without it, so the target moved forward to the next open line rather than naming a version the mainline had already passed.  Originates as the 4 review findings explicitly deferred during the v0.7.0 release cut (see PR #54 + the `risks-and-decisions.md` "2026-05-15 — v0.7.0 release review deferrals" section).  Wave 2 carry-overs from Phase 14 (intra-stage resume, DAG pipelines, parallel exec, wizard pipeline path) are tracked at the bottom as **future phases**, not in-flight Phase 14.5 work.
>
> **Where the delivered shape differs from the plan below.**  The task descriptions are preserved as written so the delta is auditable rather than quietly erased:
>
> - **Task 1 was already shipped before this delivery began, under a different finding ID.**  The chain manifest hash + non-chain-field tamper detection landed in **`v0.8.0`** (commit `e7c3321`, 2026-06-14) as `F-P4-OPUS-20`, one of six compliance-completeness gaps closed in that commit.  Nobody noticed at the time that it also satisfied this Task 1 / `F-PR54-H6`, so the row stayed open and was carried forward — and the `v0.9.x` records initially presented it as part of *this* delivery.  It is not.  This delivery contributed **documentation only** for Task 1 (the `--pipeline` section of [`../reference/verify_annex_iv_subcommand.md`](../reference/verify_annex_iv_subcommand.md)); the behaviour has been in operators' hands since v0.8.0.  Marked `[x]` because the outcome is delivered, not because it was delivered here.
>   Where the shipped shape differs from the plan below: it did not add a separate `compute_pipeline_manifest_hash` — `generate_pipeline_manifest` stamps `metadata.manifest_hash` using the existing `compute_annex_iv_manifest_hash`, so writer and verifier share one canonicalisation routine instead of two that can drift.  The planned exit-code mapping ("hash mismatch → `EXIT_CONFIG_ERROR (1)`") was **rejected**: `EXIT_INTEGRITY_FAILURE (6)` shipped between the plan and the work, and a recomputed digest that disagrees is the definition of "compared and did not match".  A mismatch exits `6`.
> - **Task 2**'s planned mapping ("per-stage failures → `EXIT_CONFIG_ERROR (1)`") was likewise superseded.  Rotten evidence — zero bytes, malformed JSON, invalid UTF-8, excessive nesting, missing Annex IV fields, hash mismatch, path escape, symlink, directory, oversize — exits `6`.  A third routing token, `UNVERIFIED::`, was added for the genuinely different case where the verifier reached the evidence and nothing attested to it (a complete-but-unhashed artefact), which exits `1`.
> - **Task 2 also exposed a writer defect the plan did not anticipate, and the first fix for it was wrong.**  The task assumed each stage's recorded pointer named a real file; it never did.  The orchestrator recorded `training_manifest.json`, which no ForgeLM version writes, so the deep parse found nothing on every real run.  The first attempt fixed this **reader-side only** — the verifier resolved the legacy basename to its `annex_iv_metadata.json` sibling and, failing that, reported `UNVERIFIED` — which *inverted the tamper signal*: deleted evidence (exit `1`) routed softer than corrupted evidence (exit `6`), and the message asserted the cause was "a writer defect, not tampering", which the verifier could not know.  The writer is now corrected at source (`forgelm/cli/_pipeline.py` records `annex_iv_metadata.json`), the legacy fallback is version-gated to pre-0.9.1 manifests only, and missing evidence routes on whether the run configured a `compliance:` block.  Recorded here because the plan's framing — "the pointer is fine, only the parse is missing" — is what let the defect survive design review.
> - **The chain manifest hash is unkeyed, and neither the plan nor the first delivery said so.**  Task 1 below specifies a plain SHA-256 and the delivery stamped one, but the records that followed described it as though it authenticated the manifest — a `v0.9.x` entry claimed `forgelm_version` and the `annex_iv` block "cannot be edited to unlock the softer path without a hash mismatch".  That does not follow: `compute_annex_iv_manifest_hash` is public and takes no secret, so whoever can write the manifest can recompute the digest.  The hash detects accidental corruption, careless edits and drift; it does not detect an informed forgery.  A concrete consequence, since only stages whose `status` is exactly `completed` are examined: flipping a stage's status, deleting its `annex_iv_metadata.json` and recomputing the digest drops the stage from the report with `hash_state` still `verified` (reproduced on a two-stage chain; the single-stage case is caught by the `final_status: completed` with zero completed stages rule).  Closing this needs authentication, not another reader-side check — the audit log's `FORGELM_AUDIT_SECRET` / per-line `_hmac` / `--require-hmac` pattern is the precedent, and no equivalent exists for `verify-annex-iv`.  Recorded as an open design limitation, not a delivered item; the threat model is now stated in [`../reference/verify_annex_iv_subcommand.md`](../reference/verify_annex_iv_subcommand.md) and corrected in [`risks-and-decisions.md`](risks-and-decisions.md).
> - **Task 3** shipped `docs/reference/webhook_schema.md` + TR mirror.  The planned `event_kind` discriminator was **not** added — no such field exists on the wire, and inventing one for a doc would have documented a payload ForgeLM does not send.  The planned `docs/standards/webhook_schema.md` was not created either; the contributor-facing rules already live in `docs/standards/logging-observability.md`, which now points at the new reference.  The optional `SUPPORTED_EVENTS` constant + `tools/check_webhook_event_vocabulary.py` guard were not built — see the open follow-ups below.
> - **Task 4** landed as `_ALLOWED_EXTRA_PAYLOAD_KEYS`, not `_ALLOWED_PIPELINE_EXTRAS`, and holds four keys rather than the six sketched below: `gate_decision` and `staging_path` are **not** passed by any shipped notifier, and registering keys nothing emits would have made the allowlist a wish-list rather than a description of the wire.  A value-type screen (JSON scalars only) and a base-field collision screen were added beyond the plan.
>
> **Open follow-ups from this delivery** (small, tracked here so they are not lost):
>
> - **Task 3's optional third sub-task was not built and remains open:** the `WebhookNotifier.SUPPORTED_EVENTS: frozenset[str]` class constant and the paired `tools/check_webhook_event_vocabulary.py --strict` guard.  Until they exist, the eight-event table in [`../reference/webhook_schema.md`](../reference/webhook_schema.md) is held against the emission sites by review alone, so the documented vocabulary can drift from what `forgelm/webhook.py` actually sends without CI noticing.  It was optional in the plan and stays optional; it is listed here because Task 3's delta note points at this section, and a cross-reference that leads nowhere is how a deferral gets lost.
>
> Three further follow-ups this delivery opened were closed before the step was committed:
>
> - `docs/reference/webhook_schema.md` ↔ `webhook_schema-tr.md` is registered in
>   `tools/check_bilingual_parity.py::_PAIRS`.  It was caught by the test that audits the guard's own
>   hand-maintained registry, not by the guard itself — the registry is exactly the kind of hand-kept list
>   that rots, so the audit test is what holds it honest.
> - `--pipeline`'s argparse help said the verifier checks "per-stage training_manifest existence", which is
>   what it did before Task 2.  It now says it deep-parses each completed stage's Annex IV evidence.
> - The "all 222 existing tests stay green" claim under **Requirements** was re-derived (330) rather than
>   carried forward.
>
> **Note:** This file details a single planned phase.  See [../roadmap.md](../roadmap.md) for the cross-phase summary; the Phase 14 design + shipped scope is archived in [completed-phases.md#phase-14-multi-stage-pipeline-chains-v070](completed-phases.md#phase-14--multi-stage-pipeline-chains-v070).

**Goal:** Close the four pipeline-manifest + webhook hygiene items that v0.7.0 deliberately deferred because each one carried a non-trivial design surface (golden-manifest regeneration, recursive Annex IV verification, webhook schema documentation, structured-payload typing).  Each item is small in code surface but needs careful test-fixture management; bundling them into one focused sub-phase isolates the change so Phase 15-style review absorption can land cleanly.

**Priority:** Medium — none of the four blocks production usage.  The chain-level Annex IV manifest already passes the structural verifier; per-stage manifests retain their existing canonical hashes; webhook receivers tolerate the new event names as plain strings; the `**extra` payload merge is caller-controlled (orchestrator-internal).  Hardening lands on the v0.9.x cycle as bandwidth allows.

**Estimated Effort:** Medium (~1-2 weeks across all four tasks + their review absorption).

> **Context:** PR #54 review pass classified all four findings as "real refactor / hardening opportunity, not a release blocker".  The deferrals are tracked here (and as `F-PR54-...` rows in `risks-and-decisions.md`) so the work cannot drift.

## Tasks

1. [x] **Canonical pipeline manifest hash** (HIGH 6)
   The chain-level `compliance/pipeline_manifest.json` does not carry a canonical hash of its own bytes.  Structural verifier (`_verify_manifest_payload`) catches chain-integrity / index / status drift but accepts edits to non-chain fields (provider metadata, metrics, `final_status`, per-stage `error` strings) without protest.  Single-artefact Annex IV already pins this surface via `compute_annex_iv_manifest_hash()`; pipeline manifest should mirror that pattern.

   ```python
   # forgelm/compliance.py — new helper alongside compute_annex_iv_manifest_hash
   def compute_pipeline_manifest_hash(manifest: Dict[str, Any]) -> str:
       """SHA-256 of the manifest's canonical JSON serialisation.

       Canonical = sorted keys, no extraneous whitespace, no manifest_hash
       field itself.  Mirrors the single-artefact pattern at
       `compute_annex_iv_manifest_hash`.
       """
       payload = {k: v for k, v in manifest.items() if k != "manifest_hash"}
       canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
       return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()
   ```

   - **`generate_pipeline_manifest`** stamps `manifest["manifest_hash"]` at write time.
   - **`_verify_manifest_payload`** re-computes the hash and adds a `manifest_hash_mismatch` violation when the on-disk value diverges.
   - **Golden fixtures** under `tests/fixtures/pipeline/` re-baseline once; backward-compat note in CHANGELOG for operators who pinned a manifest hash in external systems.
   - **CLI:** `forgelm verify-annex-iv --pipeline <dir>` exit-code mapping stays: hash mismatch → `EXIT_CONFIG_ERROR (1)` (manifest is operator-fixable; treat the same as a chain-integrity violation).

2. [x] **Per-stage `training_manifest.json` deep parse validation** (HIGH 7)
   `verify_pipeline_manifest_at_path` currently checks `os.path.isfile(per_stage_manifest)` only.  A zero-byte / malformed-JSON / tampered file still passes the existence check; the verifier reports "OK" while one of the per-stage Annex IV artefacts is rotten.

   ```python
   # forgelm/compliance.py — verify_pipeline_manifest_at_path additions
   from .compliance import verify_annex_iv_artifact  # already public

   for idx, stage in enumerate(well_formed_stages):
       per_stage_manifest = stage.get("training_manifest")
       if per_stage_manifest and stage.get("status") == "completed":
           # Existing existence check stays; ADD deep verification:
           try:
               result = verify_annex_iv_artifact(per_stage_manifest)
           except (OSError, json.JSONDecodeError) as e:
               violations.append(
                   f"Stage {stage.get('name')!r}: per-stage manifest at "
                   f"{per_stage_manifest!r} unreadable: {e}"
               )
               continue
           if not result.valid:
               violations.append(
                   f"Stage {stage.get('name')!r}: per-stage manifest at "
                   f"{per_stage_manifest!r} failed Annex IV verification: "
                   f"{result.reason} (missing: {result.missing_fields})"
               )
   ```

   - **Recursive verification semantics** — pipeline verifier becomes the chain-level aggregator over per-stage verifiers; documented contract in `docs/reference/verify_annex_iv_subcommand.md` (+ TR mirror).
   - **Performance bound** — N stages × O(1) per-stage verifier; manifests are small (~kB).  No need for parallelism.
   - **Exit-code mapping** — per-stage failures are operator-fixable (regenerate the per-stage manifest from training_run output, or accept that the run is lost and start fresh); route to `EXIT_CONFIG_ERROR (1)` alongside the existing chain-integrity violations.
   - **Tests:** extend `tests/test_pipeline_compliance.py::TestVerifyPipelineManifestAtPath` with `test_per_stage_manifest_zero_byte`, `test_per_stage_manifest_malformed_json`, `test_per_stage_manifest_missing_required_field`.

3. [x] **Webhook `pipeline.*` event vocabulary documentation** (MEDIUM 10)
   v0.7.0 introduced 7 new `pipeline.*` event names alongside the pre-existing 5-event `training.*` vocabulary.  The receiver-side contract (Slack / Teams / Discord webhook adapters; Make.com / Zapier flows; downstream enum-validating consumers) was implicit — `event` is documented as a string, not a frozen enum — but never explicitly enumerated in a single canonical reference.  v0.7.0's CHANGELOG lists the seven new events, but a downstream consumer searching for the authoritative list has to read CHANGELOG.

   Three sub-tasks:

   - **Add `docs/reference/webhook_schema.md` + `-tr.md`** as the canonical reference.  Sections: per-event payload shape, `event_kind` discriminator (`"training"` vs `"pipeline"`), backward-compat note for pre-v0.7.0 enum-validating receivers.
   - **Update `docs/standards/webhook_schema.md`** (if it exists; otherwise add) with the explicit "event field is an open-ended string, not a frozen enum" rule + the post-v0.7.0 vocabulary.
   - **Optional:** add a `WebhookNotifier.SUPPORTED_EVENTS: frozenset[str]` class constant + a `tools/check_webhook_event_vocabulary.py` `--strict` guard so the documented vocabulary cannot drift from the actual emission sites.

4. [x] **`WebhookNotifier._send(**extra)` explicit allowlist** (MEDIUM 11)
   PR #53's blocker fix added `**extra` to `_send` so `notify_pipeline_*` could pass `stage_count` / `final_status` / `stopped_at` / `stage_name` through to the receiver payload.  Today `**extra` accepts any keyword the caller passes; in practice all callers are orchestrator-internal (controlled), but a future contributor passing external user input through `_send` would have nothing stopping that input from landing in the payload.

   ```python
   # forgelm/webhook.py
   _ALLOWED_PIPELINE_EXTRAS: frozenset[str] = frozenset({
       "stage_count", "final_status", "stopped_at",
       "stage_name", "gate_decision", "staging_path",
   })

   def _send(self, *, event, ..., **extra) -> None:
       ...
       unknown = set(extra) - _ALLOWED_PIPELINE_EXTRAS
       if unknown:
           logger.warning(
               "Webhook _send received unknown extras (dropped): %s",
               sorted(unknown),
           )
       for key in _ALLOWED_PIPELINE_EXTRAS & set(extra):
           if key not in payload:
               payload[key] = extra[key]
   ```

   - **Allowlist enumerated against actual emission sites** — single source of truth in the module.
   - **`tests/test_webhook.py`** gets a new `TestSendExtrasAllowlist` class: every `_ALLOWED_PIPELINE_EXTRAS` member must be emitted by at least one `notify_pipeline_*` method; an unexpected extra triggers the WARN log + drop.
   - **Optional follow-up:** typed `WebhookPayload` `TypedDict` so static type checkers catch unknown keys at edit time.

5. [ ] **Cognitive Complexity refactor — SonarCloud S3776** (LOW 12) — **NOT SCHEDULED. The task as originally written was false; do not execute it as specified.**

   *Rewritten 2026-07-20 after re-measuring against the tree rather than against the entry.* The original text (preserved in git history) claimed SonarCloud's `python:S3776` flagged **six** functions over the 15 ceiling, named them in a table with file:line and per-function counts, and set the acceptance criterion "the S3776 issue count drops from 6 → 0 on the next analysis run". Every load-bearing part of that is wrong now, and some of it was wrong when written:

   - **The count is off by roughly an order of magnitude.** An in-repo AST approximation of cognitive complexity over `forgelm/` finds **~46** functions above 15, not 6. The exact number depends on the metric implementation — this figure is an approximation and should be treated as such — but no plausible implementation turns 46 into 6.
   - **The table omits the worst offenders entirely.** The two largest are `ingest_path` (`forgelm/ingestion.py`, ~73) and `verify_integrity` (`forgelm/verify.py`, ~37), neither of which appears in the original six. Also above the ceiling and unlisted: `manage_checkpoints` (`forgelm/utils.py`, ~39), `verify_pipeline_stage_evidence` (`forgelm/verify.py`, ~34), `audit_dataset` (`forgelm/data_audit/_orchestrator.py`, ~28). A refactor executed against the original table would have spent its effort on the wrong functions.
   - **At least one listed function no longer breaches.** `_parse_webhook_value` was listed at 17; it now measures ~9 and is comfortably under the ceiling. It was reduced by unrelated work in a later cycle and nobody revisited the entry.
   - **Every file:line in the table is stale.** `safe_post` is at `forgelm/_http.py:476`, not `:272`; `_parse_webhook_value` at `_collectors.py:133`, not `:96`; `_print_preflight_checklist` at `_orchestrator.py:1198`, not `:1155`. The table is a snapshot of a tree that no longer exists.
   - **The acceptance criterion is unobservable.** `sonar-project.properties` exists at the repo root, but **no workflow in `.github/workflows/` references Sonar at all**, and the properties file sets nothing for `S3776`. "The issue count drops 6 → 0 on the next analysis run" names a scan that this repo never triggers. This is the same failure mode this cycle has been closing repeatedly: *a check that reports success without examining its subject* — here, a verification step that cites a CI integration that does not exist.

   **Decision: do not do the refactor.** The stated benefit is readability; the cost is a wide mechanical diff across `_http.py` (the SSRF chokepoint), the wizard orchestrator and the ingestion path, for **zero correctness gain** — none of these are defects and every call site is already covered by the test suite. A large no-behaviour-change diff over the SSRF chokepoint carries more risk than the readability it buys.

   **The real prerequisite, and the condition for revisiting.** This item is not blocked on a version; it is blocked on the metric becoming observable. Revisit when **either**:

   1. SonarCloud is actually wired into a workflow, so `S3776` counts are produced on PRs and a regression can be caught — at which point the honest first step is to record the true baseline, not to assert a target; **or**
   2. an in-repo cognitive-complexity ceiling check lands under `tools/` (the `ast`-based approach used to produce the numbers above is sufficient), wired as a **ratchet** rather than a threshold — the same shape as `tools/check_module_size.py`'s per-file LOC budgets, so existing breaches are frozen at their current values and new growth fails, without demanding a 46-function refactor up front.

   Until one of those holds there is no way to observe whether a refactor helped, and a refactor whose success cannot be measured is not a task. **Do not retarget this to a version number** — a version is a prediction that can quietly become false, which is exactly how this entry rotted. The condition above cannot become false without someone doing the work that makes it true.

## Requirements

- **Backward compatibility, byte-identical.**  Pre-v0.7.x configs without a `pipeline:` block continue to reach `forgelm/trainer.py` byte-identical to v0.6.0 — orchestrator surface unchanged.
- **No fixture mass-regeneration.**  Existing `tests/fixtures/pipeline/*.yaml` and their golden manifests are *amended*, not replaced.  Each task that touches a fixture adds a single migration commit.
- **Webhook contract widening, not narrowing.**  Adding `_send` allowlist is the only narrowing; the documented vocabulary surface widens (more events, more fields).  No receiver should need to update *unless* they were already hard-validating the pre-v0.7.0 `event` enum (in which case CHANGELOG calls it out as a breaking change for v0.7.x).
- **Test surface preserved.**  Every existing pipeline + webhook + verification test stays green — 330 collected across `test_pipeline_compliance.py`, `test_webhook.py` and `test_verification_toolbelt.py` at delivery (2026-07-20), re-derived rather than carried forward; the "222" this line used to assert was written at planning time and was never true of the suite that shipped.  Each task adds tests; none rewrite existing assertions except where the contract genuinely tightens (Task 1's hash-mismatch addition, and the no-hash manifest that used to report success).

## Validation gate to ship Phase 14.5

- All 4 tasks land with regression tests (a single `_ALLOWED_PIPELINE_EXTRAS` test counts).
- `forgelm verify-annex-iv --pipeline <dir>` exit 0 on every existing `tests/fixtures/pipeline/` golden manifest after the hash + deep-parse rules apply.
- Bilingual parity + anchor resolution + CLI help consistency guards green at PR open.
- `__api_version__` bumps MINOR if **any** of (a) `compute_pipeline_manifest_hash` is added to `forgelm.__all__`, (b) `verify_pipeline_manifest_at_path` signature changes, (c) `WebhookNotifier._send` adds a parameter that downstream library consumers could observe.  Otherwise stays.

## Delivery

- **Target release:** `v0.9.1` (patch) if all 4 tasks ship together within the v0.9.x cycle.  If only Tasks 1 + 2 land, prefer `v0.9.1` patch (manifest hardening) + `v0.9.2` patch (webhook hygiene) split.
- **Entry gate:** PR #54 is merged + v0.7.0 PyPI tag verified (already true at the time this file lands).
- **CHANGELOG plan:** each task lands a one-line bullet under `[Unreleased]` per Keep-a-Changelog convention; at `v0.9.1` tag time the `[Unreleased]` block is renamed to `[0.9.1] — YYYY-MM-DD`.
- **Wave 2 / future-phase carry-overs** (NOT part of Phase 14.5; tracked here only so the items aren't lost):
  - Intra-stage HF `Trainer.train(resume_from_checkpoint=...)` integration — would let `--resume-from` pick up mid-stage rather than at stage boundaries.  Gated on `Trainer` API stability + concrete operator demand.
  - DAG pipelines (non-linear stage dependencies) — requires a config-schema redesign (`needs:` / `depends_on:` per stage) and explicit dependency declaration; horizon `v1.x` or later.
  - Parallel stage execution (independent branches running concurrently) — gated on the DAG schema; same horizon.
  - `forgelm wizard` pipeline path — gated on operator demand after v0.7.0 ships.  Wizard currently emits single-stage configs only (documented limitation in `docs/guides/pipeline.md`).

---

## Cross-references

- **Phase 14 shipped scope:** [completed-phases.md#phase-14-multi-stage-pipeline-chains-v070](completed-phases.md#phase-14--multi-stage-pipeline-chains-v070)
- **Pipeline operator guide:** [`../guides/pipeline.md`](../guides/pipeline.md) ([Türkçe](../guides/pipeline-tr.md))
- **Pipeline schema reference:** [`../reference/configuration.md`](../reference/configuration.md#pipeline-optional--multi-stage-training-chains-phase-14) ([Türkçe](../reference/configuration-tr.md#pipeline-isteğe-bağlı--çok-aşamalı-eğitim-zincirleri-faz-14))
- **CLI surface:** [`../reference/usage.md`](../reference/usage.md) ([Türkçe](../reference/usage-tr.md))
- **Deferred-findings tracking:** [risks-and-decisions.md](risks-and-decisions.md) — "2026-05-15 — v0.7.0 release review deferrals" section
- **Code surface (planned):** [`forgelm/compliance.py`](../../forgelm/compliance.py) (`compute_pipeline_manifest_hash` + `verify_pipeline_manifest_at_path` deep-parse), [`forgelm/webhook.py`](../../forgelm/webhook.py) (`_ALLOWED_PIPELINE_EXTRAS`), `docs/reference/webhook_schema.md` (new file)
- **Pattern reference:** Phase 15's review-absorption discipline + fixture amendment model is the working precedent.
