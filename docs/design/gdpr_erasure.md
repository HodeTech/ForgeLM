# ForgeLM GDPR Right-to-Erasure — Design

> **Scope:** Design specification for ForgeLM's GDPR Article 17
> right-to-erasure tool surface — the `forgelm purge` subcommand
> semantics, the `RetentionConfig` Pydantic schema, and the audit-chain
> integration that records each erasure / refusal event. Living spec —
> kept in sync with the implementation under
> `forgelm/cli/subcommands/_purge.py` and `forgelm/config.py`
> (`RetentionConfig`).

**Regulatory anchor:** EU GDPR Article 17 ("right to erasure" / "right
to be forgotten") + Article 5(1)(e) ("storage limitation").

**Status:** Implemented in v0.5.5. See `CHANGELOG.md` and
`docs/guides/gdpr_erasure.md` for the user-facing surface.

---

## 0. Why this document exists

ForgeLM emits training-data corpora, model adapters, audit logs, and derived artefacts that may contain personal data of identifiable EU data subjects.  Article 17 of the GDPR gives those subjects the right to request that this data be erased.  Article 5(1)(e) requires that any personal data not actively required for the declared purpose is removed by default after a defined retention horizon.

ForgeLM today has no operator-actionable tool for either obligation:

- An operator who receives an Article 17 erasure request has to grep the corpus by hand, edit the JSONL, and hope nothing else references the row.
- There is no retention policy in the config schema, so no signal that the audit log has outgrown its lawful basis.
- The user-manual page `docs/usermanuals/en/compliance/gdpr.md:51-64` documents `ingestion.retention.raw_documents.ttl_days` enforcement that no implementation backs.

Phase 21 closes the gap with a real implementation.  This Phase 20 document specifies exactly what that implementation must do — what to delete, what to record, what to preserve, what to refuse — so the next phase has a single source of truth instead of redoing legal mapping in code review.

---

## 1. GDPR Article 17 — what the regulation actually requires

**Article 17(1)** lists six trigger conditions; ForgeLM only needs to act on three in scope for a fine-tuning toolkit:

| Trigger | Practical meaning for ForgeLM | In scope? |
|---|---|---|
| (a) Processing no longer necessary | Operator decides corpus row X is no longer required for the declared model purpose. | ✅ |
| (b) Lawful basis withdrawn (consent revoked) | Data subject revokes consent that originally allowed inclusion in training data. | ✅ |
| (c) Subject objects under Art. 21 + no overriding lawful basis | Subject files an objection; controller does not have overriding legitimate interest. | ✅ |
| (d) Unlawfully processed | Compliance audit finds an upstream lawful-basis defect. | ✅ |
| (e) Legal obligation | Court order. | (out of scope — operator handles externally; tool just records) |
| (f) Child's data offered without parental consent | Subject was a minor at point of collection. | (out of scope — operator handles externally) |

**Article 17(2)** requires the controller to inform downstream processors / publishers of the erasure when the data has been disclosed.  ForgeLM is not a publisher (we do not run inference); the deployer is.  We document this boundary in the operator guide rather than implement push notification.

**Article 17(3)** lists exemptions where erasure does **not** apply — including (3)(b) compliance with a legal obligation of EU law, which is the entire reason the audit log itself is preserved.

The take-away: erasure operates on **training data + derived artefacts**, not on the audit log.  The audit log records *that an erasure happened* (Art. 5(2) accountability) but the original entries that reference the now-erased subject stay in place because the audit log is the legal-obligation record under Art. 17(3)(b).

---

## 2. ForgeLM artefact taxonomy — what could carry personal data

| Artefact | Where it lives | Carries personal data? | Erasure strategy |
|---|---|---|---|
| **Training corpus rows** | `<corpus>.jsonl` (operator-supplied) | Yes — the whole point | **Delete row in place** + audit event |
| **Audit log entries** | `<output_dir>/audit_log.jsonl` (append-only) | Indirectly (operator name, run config, possibly file paths that name a subject) | **Never delete original** — append a `data.erasure_completed` event that references the deleted row's `row_id`.  Article 17(3)(b) exemption. |
| **Model adapters** | `<output_dir>/final_model/` (PEFT adapter + tokenizer) | **Memorised** training samples — extraction risk for high-overfit models | **Document escalation**: full retraining is the only proper resolution.  Tool emits `data.erasure_warning_memorisation` audit event flagging the gap. |
| **Final model staging** | `<output_dir>/final_model.staging.<run_id>/` | Same memorisation risk | Operator chooses: delete staging (pre-promotion) or treat as adapters above |
| **Compliance artefacts** | `<output_dir>/compliance/*.{json,yaml}` | References to corpus paths (could disclose subject names via filename) | **Regenerate** after erasure (or `forgelm purge --kind artefacts` rewrites them) |
| **Data audit reports** | `<output_dir>/data_audit_report.json` | `lang_sample` field carries up-to-512-char snippets from random rows (could include subject text) | **Regenerate** post-erasure (or hand-edit the snippets — the report is operator-facing only) |
| **Synthetic / GRPO logs** | `<output_dir>/synthetic_data.jsonl`, GRPO completions | Yes if generated from a prompt that named the subject | **Delete row** with the same row-id mechanism as training corpus |
| **Webhook history** | (external system; ForgeLM does not store) | Possibly | Out of scope — operator handles in the receiving system |

The four artefact kinds the operator can act on in-tool: `corpus_rows`, `staging`, `artefacts`, `logs` (where `logs` means "regenerate audit-summary derivatives, not the chain itself").

---

## 3. Retention policy — `RetentionConfig`

Article 5(1)(e) requires personal data to be kept "no longer than is necessary".  ForgeLM ships a config block so the operator can declare their retention horizon and the tool can warn / refuse runs that violate it.

### 3.1 Prior-state acknowledgement (audited 2026-05-02)

Two pre-existing surfaces overlap with this design and must be resolved up front so Phase 21 does not ship a second source of truth:

- **`EvaluationConfig.staging_ttl_days`** (`forgelm/config.py:301`, shipped in Wave 1 Faz 9) is a doc-only field — its docstring promises Phase 21 enforcement.  Phase 21 implements the enforcement under the new `RetentionConfig.staging_ttl_days` (this design); the `EvaluationConfig` field is **deprecated** in the same release per `docs/standards/release.md` cadence: emit a `DeprecationWarning` on access in v0.5.5, alias-forward the value to `retention.staging_ttl_days`, keep both working in v0.6.x, remove `evaluation.staging_ttl_days` in v0.7.0.

  **Conflict-resolution semantics (dual-set window v0.5.5–v0.6.x):**

  - When only `evaluation.staging_ttl_days` is set → alias-forward to `retention.staging_ttl_days` and emit a single `DeprecationWarning` naming the new field + the v0.8.0 removal target.
  - When only `retention.staging_ttl_days` is set → no warning; canonical path.
  - In the case where **both** are set with **identical** values → emit `DeprecationWarning` for the deprecated field; the canonical `retention.staging_ttl_days` value is used; the operator's intent is unambiguous.
  - If **both** are set with **different** values → raise `ConfigError` at validation time naming both keys, both values, and instructing the operator to remove the deprecated entry.  Silent winner = wrong winner; ambiguous configs are refused.

  Phase 21 ships `tests/test_config.py::test_staging_ttl_days_dual_set_with_different_values_refused` asserting the `ConfigError` and that the message mentions both keys + both values.

  **Tracking issue (per `docs/standards/release.md:95`):** Phase 21 also files a tracking issue `'Remove EvaluationConfig.staging_ttl_days in v0.8.0'` and links it from both the `DeprecationWarning` message text and the v0.5.5 CHANGELOG `### Deprecated` entry.  The v0.8.0 removal PR closes that issue.
- **`docs/usermanuals/en/compliance/gdpr.md` lines 51-64** + closure plan §15.5 row GH-023 reference an `ingestion.retention.raw_documents.ttl_days` shape.  This design (§10 Q1) standardises on the top-level `retention.*` form because the policy covers more than ingestion artefacts (audit logs, staging dirs, ephemeral snapshots).  Phase 21 updates the GDPR user-manual page in the same PR; closure plan §15.5 GH-023 entry is amended to "absorbed under top-level `retention.*`" rather than carrying the `ingestion.retention.*` shape forward.

### 3.2 Schema

New Pydantic block in `forgelm/config.py`:

```python
class RetentionConfig(BaseModel):
    """Art. 5(1)(e): storage-limitation horizons for ForgeLM-emitted artefacts."""

    model_config = ConfigDict(extra="forbid")

    # Audit log retention horizon. EU AI Act Article 12(2) sets the floor
    # at 6 months; Article 11 (technical documentation) extends to 10 years
    # post-deployment for high-risk systems. GDPR Article 30 record-keeping
    # commonly demands ≥3 years. We default to 1825 days (5 years) as a
    # middle-of-the-road horizon that satisfies the AI Act floor with a
    # comfortable margin AND covers the GDPR controller-record requirement.
    # Operators with stricter compliance (Article 11 deployer obligation,
    # ISO 27001 retention contracts) can set this higher; Article 12(2)
    # operators with no AI Act obligation can lower to 180 (6 months).
    # Set to 0 to disable horizon enforcement entirely (not recommended).
    audit_log_retention_days: int = Field(default=1825, ge=0)

    # Staging directory time-to-live. Article 14 staging is meant to hold a
    # model for human review, not as long-term storage. Default 7 days; an
    # operator running a multi-week review cycle should bump this.
    staging_ttl_days: int = Field(default=7, ge=0)

    # Ephemeral artefact retention (data audit reports, synthetic-data
    # snapshots, GRPO completion logs). Default 90 days = one quarter, which
    # is long enough to debug a regression but short enough to limit
    # storage-limitation exposure on subject names that may have leaked into
    # `lang_sample` fields.
    ephemeral_artefact_retention_days: int = Field(default=90, ge=0)

    # Raw-document retention (typically `data/raw_documents/<run_id>/`).
    # Phase 21 implementation note: this field was added to the shipped
    # RetentionConfig after the original design draft — the `forgelm
    # purge --check-policy` scan ages each `raw_documents/<run_id>/`
    # directory against its owning run's audit-genesis timestamp.
    # Default 90 days; matches the typical ingestion-window cadence in
    # most deployer QMS templates.
    raw_documents_retention_days: int = Field(default=90, ge=0)

    # Enforcement mode: how strict is the policy?
    # - "log_only": writes a notice to the audit log; never refuses a run.
    # - "warn_on_excess": logger.warning + audit notice; run continues.
    # - "block_on_excess": ConfigError, training does not start.
    enforce: Literal["log_only", "warn_on_excess", "block_on_excess"] = "log_only"
```

`ForgeConfig` grows an optional `retention: Optional[RetentionConfig] = None` field.  When unset, the existing default-permissive behaviour holds (no retention check).  When set, the trainer's pre-flight gate runs the check.

### 3.3 mtime caveat (and the in-band age field)

The retention check uses filesystem `mtime` as the primary age signal, which is **unreliable in isolation** — a `cp -p`, a tape restore, or a `touch` resets it without changing the data age.  Phase 21 mitigates this in two layers:

1. **Belt:** for run-scoped artefacts (compliance/, data audit reports, staging dirs), the *canonical* age is the `timestamp` field on the run's audit-log genesis event — that field is HMAC-signed when `FORGELM_AUDIT_SECRET` is set and cannot be silently reset by a filesystem operation.  Phase 21 prefers this in-band timestamp; mtime is the fallback when no audit log is present.
2. **Suspenders:** for stand-alone JSONL corpora (no audit log next to them), Phase 21 still uses mtime but logs a `data.retention_age_source=mtime` note on the violation so an operator reading the report knows the age signal is filesystem-derived.

The `--check-policy` JSON output includes `age_source ∈ {audit, mtime}` per artefact; a downstream consumer can filter for `audit`-sourced ages if it distrusts `mtime`.

### 3.4 Where the policy is checked

Three sites:

1. **`ForgeTrainer.train()` pre-flight** (before any data is read): scan `output_dir` for artefacts whose `mtime` exceeds the retention window; emit per-violation audit events; honour `enforce`.
2. **`forgelm purge --check-policy` (no --row-id / --run-id)**: read-only scan; reports violations as a structured summary (text + JSON output formats).
3. **`forgelm audit`** (existing subcommand): when `retention` is set in the loaded config, the audit report's `notes` block lists violations as a friendly heads-up.

### 3.5 What "violation" means

For each retention horizon:
- Compare artefact `mtime` against `now - <horizon>_days`.
- If older, the artefact has overstayed the declared lawful basis.
- The audit log itself is **never deleted automatically** even when the policy says it has overstayed — Article 17(3)(b) exemption applies and the operator must make the deletion call by hand.  The tool only flags.

The compliance artefacts (`compliance/*.json`) are tied to a specific run; their lifetime is the run's lifetime, not a separate horizon.  In-place deletion is supported via `forgelm purge --run-id <id> --kind artefacts` (§4.1) which removes `<output_dir>/compliance/*.json` for the named run while leaving the audit log + final model intact — useful when an operator wants to regenerate the bundle after an Article 17 corpus erasure changed the lineage.  Whole-`<output_dir>` deletion remains the operator's manual escalation when the entire run is being scrubbed.

---

## 4. The `forgelm purge` subcommand

### 4.1 Modes (mutually exclusive)

```text
forgelm purge --row-id <id> --corpus <path>          # corpus-row erasure
forgelm purge --run-id <id> --kind {staging,artefacts}   # run-scoped erasure
forgelm purge --check-policy [--config <path>]       # retention dry-run
```

A single invocation does exactly one of the three.  Combining flags is a `ConfigError`.

### 4.2 Flag semantics

| Flag | Required when | Type | Purpose |
|---|---|---|---|
| `--row-id <id>` | corpus mode | str | Row to delete.  **Required:** the JSONL must carry a stable id field (e.g. `"id"` or `"row_id"` key) on every row.  Line-number fallback is **rejected** — operators with id-less corpora must pre-populate ids via an operator-side script (a `forgelm audit --add-row-ids` helper is on the Phase 28 backlog) first, otherwise a re-ordered file would silently delete the wrong row. |
| `--corpus <path>` | corpus mode | str | Path to **a single JSONL file**.  Directory mode is **rejected** in `--row-id` mode — multi-file purges are an operator script (loop over files), not a `forgelm purge` flag, because GDPR Article 17 expects an erasure decision *per row* with its own audit event.  An invocation that matches the same id in two files is a "DELETE without WHERE" hazard. |
| `--row-matches` | corpus mode optional | enum | `one` (default; refuses if 0 or ≥2 matches in the file) / `all` (deletes every match, requires explicit opt-in for the ambiguity).  Without this flag, multi-row matches in a single file abort with `EXIT_CONFIG_ERROR` so the operator confirms intent before bulk delete. |
| `--run-id <id>` | run mode | str | Run id matching `<output_dir>/audit_log.jsonl`'s top-level `run_id`. |
| `--kind {staging,artefacts}` | run mode | enum | Which derived artefacts to erase.  `staging` = `final_model.staging.*`; `artefacts` = `compliance/`, `data_audit_report.json`, derived JSONL snapshots. |
| `--output-dir <dir>` | run mode | str | Where to look for the run.  Default `./` (current dir). |
| `--check-policy` | policy mode | bool | Dry-run scan; reports violations against the loaded config's `retention` block. |
| `--config <path>` | policy mode (optional) | str | Config to load for the retention block; defaults to `./forgelm.yaml` then walks up. |
| `--justification <text>` | always optional | str | Free-text reason recorded in the audit event.  Strongly recommended for compliance review. |
| `--dry-run` | always optional (corpus / run mode only) | bool | Print what would be deleted; do not modify.  **Mutually exclusive with `--check-policy`** (which is itself a dry-run report); combining the two is a `ConfigError`. |
| `--yes` *(not yet implemented; Phase 28+ backlog)* | always optional | bool | The original design called for a `--yes` flag to skip an interactive "confirm erasure of row X?" prompt. The shipped `forgelm purge` is **non-interactive by default** — it never prompts, so `--yes` was not wired in v0.5.5. CI invocations get the same behaviour as TTY invocations: the operator-supplied `--justification` carries the consent record into the audit chain. The flag is preserved on the Phase 28+ backlog if a future interactive review-prompt mode lands. |
| `--output-format {text,json}` | always optional | enum | Output format.  Default `text`. |

### 4.3 Exit codes

Standard ForgeLM 0/1/2/3/4 contract — all codes from `forgelm.cli._exit_codes` reused, no new value introduced by `forgelm purge` (the global contract was extended with `EXIT_WIZARD_CANCELLED = 5` in review-cycle 2 for the interactive wizard surface; purge does not consume code 5):

| Code | Constant | Meaning |
|---|---|---|
| 0 | `EXIT_SUCCESS` | Erasure completed (or `--dry-run` reported successfully). |
| 1 | `EXIT_CONFIG_ERROR` | Config-level error: missing flag, flag combination, unknown row-id (corpus mode), unknown run-id (run mode), `--row-id` directory mode without `--row-matches=all`, multi-row match without `--row-matches=all`. (`--yes` is not implemented — see §3.5 for the consent model; the shipped `forgelm purge` is non-interactive on every invocation, so there is no "missing `--yes` on a non-TTY" failure mode.) |
| 2 | `EXIT_TRAINING_ERROR` | Runtime / I/O failure: corpus unreadable, audit-log write failure, atomic-rename failure, partial commit recovery. |
| 3 | `EXIT_EVAL_FAILURE` | Reserved for the trainer pre-flight gate when `enforce: block_on_excess` is configured (see §3.4 in the spirit of the master `error-handling.md` exit-code contract).  **Not** used by `forgelm purge --check-policy` — that path is a report, not a gate; see §10 Q5 + the paragraph below. |
| 4 | `EXIT_AWAITING_APPROVAL` | Reserved for its trainer-pipeline meaning per `docs/standards/error-handling.md` ("Training + evals passed, but `require_human_approval: true` — staged, awaiting human sign-off").  **Not** reused by `forgelm purge`. Since the shipped `forgelm purge` is non-interactive (no prompt, no `--yes` flag), there is no "operator declined the prompt" path; consent is recorded in the operator-supplied `--justification` and the audit chain. |

**`--check-policy` always exits 0** (per §10 Q5 — the resolved decision: report-not-gate semantic).  Operators who want a CI gate use `--output-format json` and pipe to `jq '.violations | length'` themselves; CI then branches on that count rather than on the exit code.  This keeps the public contract `docs/standards/error-handling.md` (0/1/2/3/4 trainer + 5 wizard-cancel) consistent across every ForgeLM subcommand: a code-3 from `forgelm purge` always means "trainer pre-flight gate failed", never "report found something".

### 4.4 Atomicity

**Corpus row deletion** uses the same tempfile + atomic rename pattern as `_atomic_write_json` in `_orchestrator.py`, with a strict event-ordering invariant so a partial failure leaves an investigable trail:

1. **Audit `data.erasure_requested` FIRST** — fields include `target_kind="row"`, `target_id` (hashed per §5.4), `corpus_path`, `justification`, `dry_run`.  If this write fails, abort immediately; nothing on disk has changed.
2. Locate the matching row + record the pre-erasure line number for the completion event.
3. Open the source JSONL, open a `NamedTemporaryFile` in the same directory.
4. Stream rows, skipping the matched id.
5. `os.fsync` + `os.replace` to swap.
6. **Audit `data.erasure_completed` LAST** — same fields plus `bytes_freed`, `pre_erasure_line_number`, `files_modified=[corpus_path]`.

Recovery path for a step-5 failure (rename fails after the temp file is fully written): the `data.erasure_requested` event is in the chain, the temp file is left on disk for forensic inspection, and a `data.erasure_failed` event is appended carrying the original error.  An operator inspecting the log sees the request → failure pair and can manually decide whether to retry or revert.

**Recovery path for a step-6 failure** (rename succeeded, completion event write fails): the row is gone from the corpus but the chain has only the request event.  This is detectable: a `request` event without a matching `completed` / `failed` event within the same minute is the operator's signal to verify the corpus state and append a manual `data.erasure_completed` (or `_failed`) event by hand.  Phase 21 ships a `tests/test_gdpr_erasure.py::test_partial_commit_emits_failed_event` test that simulates this race + asserts the chain stays interpretable.

**Run-scoped erasure** for `kind=staging` uses `shutil.rmtree(staging_path)` after a `os.path.commonpath` check (same defence-in-depth as `_approve.py`'s staging path validator) so an attacker who can rewrite the audit log cannot point us at `/etc`.

**Run-scoped erasure** for `kind=artefacts` deletes a fixed allow-list (`compliance/`, `data_audit_report.json`, `data_governance_report.json`) — never a glob.  Allow-list lives in `forgelm.compliance` next to the artefact emitter.

### 4.5 What `forgelm purge` does NOT do

| Scenario | Why out of scope |
|---|---|
| **Re-train the model after corpus erasure** | Operator decision; ForgeLM cannot infer the right downstream pipeline. |
| **Distributed corpus deletion** | Single-node only.  Multi-node corpus shards live on separate filesystems; the operator runs `forgelm purge --row-id` on each. |
| **Webhook receiver cleanup** | The Slack / Teams thread that received `notify_awaiting_approval` is in the operator's chat platform, not in ForgeLM.  We log a notice. |
| **Audit log rewriting** | The chain is append-only by construction (Art. 17(3)(b) exemption).  Erasure adds an event; never edits an entry. |
| **Model weight forgetting** | "Machine unlearning" is an active research area; we do not ship a half-baked impl.  We emit `data.erasure_warning_memorisation` so the operator knows the gap is theirs to close. |

---

## 5. Audit chain — how erasure stays compatible with Article 12

The ForgeLM audit log is append-only and HMAC-signed (when `FORGELM_AUDIT_SECRET` is set).  Erasure must not break either property.

### 5.1 New audit events

Six new events in `audit_event_catalog.md` — three core erasure events plus three operator-warning events that surface gaps Phase 21 cannot close in code:

| Event | When emitted | Required fields |
|---|---|---|
| `data.erasure_requested` | First step of any `forgelm purge --row-id` / `--run-id` invocation, *before* any deletion (see §4.4 commit ordering).  If deletion then fails, the request record stays in the chain so a forensic reviewer can see the intent.  `--check-policy` is read-only and does not emit this event. | `target_kind` ∈ {row, staging, artefacts}, `target_id` (hashed per §5.4 for row mode), `salt_source` ∈ {`"env_var"`, `"per_dir"`} (row mode only; see §5.4), `corpus_path` (corpus mode), `output_dir` (run mode), `justification`, `dry_run` |
| `data.erasure_completed` | Successful deletion finishes. | All `data.erasure_requested` fields + `bytes_freed`, `files_modified` (list), `pre_erasure_line_number` (corpus mode) |
| `data.erasure_failed` | Deletion failed (atomic rename failure, permission denied, I/O error). | All `data.erasure_requested` fields + `error_class`, `error_message` (already-redacted via §5.4 policy + `_http`'s mask helper) |
| `data.erasure_warning_memorisation` | Emitted alongside `data.erasure_completed` when `target_kind="row"` AND a `<output_dir>/final_model/` exists for any run that consumed this corpus.  Signals that the row is gone from the corpus but may still be memorised in trained weights. | All `data.erasure_completed` fields + `affected_run_ids` (list of run ids whose final_model used this corpus), `recommendation` (literal `"full retraining without the erased row is the only proper mitigation"`) |
| `data.erasure_warning_synthetic_data_present` | Emitted alongside `data.erasure_completed` when `target_kind="row"` AND any `<output_dir>/synthetic_data*.jsonl` exists.  Signals that the row may have produced derivative synthetic snippets that the row→snippet mapping no longer connects. | All `data.erasure_completed` fields + `synthetic_files` (list of paths), `recommendation` (literal `"regenerate synthetic data after erasure to ensure no derivatives reference the erased row"`) |
| `data.erasure_warning_external_copies` | Emitted alongside `data.erasure_completed` when the loaded config has a non-empty `webhook` block.  Signals that downstream consumers may have received notices that referenced the now-erased data; ForgeLM's local erasure does not propagate. | All `data.erasure_completed` fields + `webhook_targets` (list of redacted URLs), `recommendation` (literal `"propagate the erasure to downstream processors per Article 17(2)"`) |

### 5.2 Chain-integrity post-erasure

Because erasure only **appends** events (never edits), the SHA-256 chain stays valid by construction.  We add a regression test in `tests/test_gdpr_erasure.py`:

1. Build a 5-event chain with `AuditLogger`.
2. Run `forgelm purge --row-id 42 --corpus train.jsonl`.
3. Re-run `verify_audit_log(path)`; assert `valid is True` and the chain is now 7 events long (5 + erasure_requested + erasure_completed).

### 5.3 Identifier hashing in audit events

Event bodies carry a `target_id` field whose handling depends on `target_kind`:

- **Run mode** (`target_kind ∈ {staging, artefacts, policy_check}`):  `target_id` is an operational identifier the trainer minted (`fg-abc123…`) — not derived from any subject input.  Stays in the clear.
- **Row mode** (`target_kind = "row"`):  `target_id` is operator-supplied and may be subject-derived (email, username, ticket reference).  Hashed at emit time per §5.4 — see the table row for the salt rules.  Operator-facing examples in this section that show `row_id=42` are illustrative of *what the operator typed* on the CLI; the *persisted audit-event field* carries the SHA-256(salt + value) digest, never the raw row id.

If the **justification** field includes something that *might* be PII (e.g. operator accidentally pastes the data subject's email into the justification), we do **not** scrub it automatically — the operator chose to write it.  We document this in the operator guide: "Do not paste subject identifiers into `--justification`; reference your internal ticket id instead."  Note that `target_id` in row mode is hashed regardless (§5.4) — the justification is the only operator-controlled field that can leak subject text into the chain in cleartext.

### 5.4 Personal-data minimisation across every audit-event field

Article 17(3)(b) preserves the audit log itself, but Article 5(1)(c) (data minimisation) still applies to **what we put in each event**.  Phase 21 enumerates every field on every new audit event and classifies it; categories that may carry personal data get a hash / redaction policy at emit time so the persisted log carries the minimum identifying surface needed for accountability.

| Event field | Source / shape | Personal-data category | Phase 21 policy |
|---|---|---|---|
| `target_kind` | enum: `row` / `staging` / `artefacts` / `policy_check` | None | clear |
| `target_id` (row mode) | Row id from the JSONL `id` (or `row_id`) field — canonical and only source.  Line-number fallback is **rejected** at the CLI per §4.2; operators with id-less corpora must pre-populate ids via an operator-side script (a `forgelm audit --add-row-ids` helper is on the Phase 28 backlog) before invoking `forgelm purge --row-id`. | **Possibly personal** (operator-supplied row IDs may be email / username / ticket numbers) | **SHA-256(salt + value)** when emitted.  Salt = first 16 bytes of `FORGELM_AUDIT_SECRET` if set, else a per-output-dir salt persisted at `<output_dir>/.forgelm_audit_salt` (16 random bytes, mode 0600, written on first emission). |
| `salt_source` (row mode, all erasure events) | Resolved salt source for the row's `target_id` hash | None (operational metadata) | One of `"env_var"` / `"per_dir"`.  Persisted on every `data.erasure_requested` / `data.erasure_completed` / `data.erasure_failed` event in row mode so a compliance reviewer reconciling two erasure events for the same subject can detect a salt-source change.  Salt-source mismatch across events for the same operator-supplied row id IS a hash discontinuity (different salt → different hash); the field exists so the discontinuity is visible in the chain instead of silently producing a "different subject" misreading. |
| `target_id` (run mode) | `fg-<hex>` from AuditLogger | None (operational) | clear |
| `corpus_path` | filesystem path | **Possibly personal** when path includes a subject name (rare but real) | clear by default; operator guide flags the risk + recommends symlinking through a stable name |
| `output_dir` | filesystem path | Same risk as `corpus_path` | clear; same guide note |
| `justification` | operator free-text | **Operator's choice** | clear; operator guide explicitly forbids pasting subject identifiers, recommends ticket reference |
| `dry_run` | bool | None | clear |
| `bytes_freed` | int | None | clear |
| `files_modified` | list[str] | Same risk as `corpus_path` | clear |
| `pre_erasure_line_number` | int | None | clear |
| `error_class` | exception class name | None | clear |
| `error_message` | str | **Possibly personal** (Python tracebacks include `repr()` of objects, which may include row content) | route through `forgelm._http._mask_secrets_in_text` first; redact obvious PII (email regex, phone regex) via the existing `forgelm.data_audit._pii_regex` mask helpers |
| `operator` (carried on every AuditLogger event) | `FORGELM_OPERATOR` or `getuser()@host` | **Personal** | clear (Article 12 record-keeping requires identifiable operator; this is the explicit Article 5(1)(b) lawful basis) |

The take-away: **the only field this design hashes by default is `target_id` in row mode**.  Everything else either is genuinely operational, is the operator's responsibility (justification), or is masked through the existing Wave 1 `_mask_secrets_in_text` helper.  Phase 21 ships a unit test `tests/test_gdpr_erasure.py::test_target_id_hashed_in_audit_event` that asserts the raw row id never appears in the persisted JSONL after a `forgelm purge --row-id ali@example.com --corpus train.jsonl` run.

---

## 6. Marketing claim revision

**Two surfaces** need updating in Phase 21 (verified 2026-05-04):

1. **`docs/guides/safety_compliance.md`** — does NOT currently contain a "GDPR-aware" paragraph (verified by `grep -n "GDPR" docs/guides/safety_compliance.md` — only an example training prompt at line 227).  Phase 21 adds a new top-level `## GDPR right-to-erasure (Article 17)` section after the existing PII section so the guide explicitly covers the new tooling.

2. **`docs/usermanuals/en/compliance/gdpr.md` lines 51-64 (+ TR mirror)** — currently documents an `ingestion.retention.raw_documents.ttl_days` enforcement that no implementation backs.  Phase 21 rewrites the YAML example to point at the new top-level `retention.*` block (per §3.1) and updates the `forgelm purge` subsection to match the shipped flag surface (per §4.2).

The replacement section text (used for both surfaces with appropriate adaptation):

```markdown
## GDPR right-to-erasure (Article 17)

ForgeLM provides operator tooling for honouring Article 17 erasure requests
in three scopes:

1. **Training corpus row erasure.** `forgelm purge --row-id <id> --corpus <path>`
   atomically rewrites the JSONL, dropping the matched row.
2. **Run-scoped artefact erasure.** `forgelm purge --run-id <id>
   --kind {staging,artefacts}` deletes the model staging directory or the
   compliance bundle for a specific run.
3. **Retention policy enforcement.** Configure `retention.*` in your YAML;
   `forgelm purge --check-policy` lists artefacts that have overstayed the
   declared horizon.

**Scope limitation.**  ForgeLM's erasure tooling acts on the **local
artefacts ForgeLM itself produced or consumed inside the operator's
training output directory**.  The operator remains responsible for:

- replicas, snapshots, and backups of the corpus stored outside the
  ForgeLM output directory;
- downstream consumers (deployed model endpoints, third-party fine-tunes
  derived from a published checkpoint, dataset mirrors on HF Hub);
- webhook receivers that persisted approval / rejection notices in
  external systems (Slack threads, Teams channels, ticket trackers);
- legal-hold copies an upstream compliance team may have placed beyond
  the operator's reach.

When the loaded config carries a webhook target, Phase 21 emits
`data.erasure_warning_external_copies` alongside the
`data.erasure_completed` event so a downstream consumer querying the
audit log sees an explicit reminder that the local erasure is not the
end of the Article 17 obligation.

What ForgeLM does **not** do:

- Re-training after erasure (your decision; the row is gone, the next
  training run starts from a clean corpus).
- Forgetting from already-trained model weights (this is an active research
  area called "machine unlearning"; the only proper mitigation today is
  full retraining without the erased data).
- Pushing erasure notices to downstream processors (Article 17(2)) — that
  is your runtime layer's responsibility, not the training toolkit's.

See [`docs/guides/gdpr_erasure.md`](../guides/gdpr_erasure.md) for the operator how-to
and the audit-event reference (Phase 21 deliverable; the file is added by
the same PR that ships `forgelm purge`).
```

A new dedicated guide `docs/guides/gdpr_erasure.md` walks through the three commands with concrete examples.

---

## 7. Test strategy — `tests/test_gdpr_erasure.py`

Phase 21 ships **at least 7 tests** covering the acceptance surface.  Every test runs without `torch` (this subcommand is pure stdlib + Pydantic).

| # | Test | Asserts |
|---|---|---|
| 1 | `test_row_erasure_rewrites_jsonl_atomically` | Row matching `row_id=42` removed from `train.jsonl`; other 99 rows byte-identical to pre-erasure. |
| 2 | `test_row_erasure_emits_audit_events` | Two new audit events landed: `data.erasure_requested` then `data.erasure_completed`, in that order, with matching `target_id` field. |
| 3 | `test_run_scoped_staging_erasure` | `--run-id fg-abc --kind staging` removes `final_model.staging.fg-abc/` recursively; final dir untouched if it exists. |
| 4 | `test_run_scoped_artefacts_erasure` | `--run-id fg-abc --kind artefacts` removes the `compliance/` allow-list; `final_model/` untouched. |
| 5 | `test_audit_chain_verifies_post_erasure` | After erasure, `verify_audit_log(path).valid is True`; chain length grew by 2 events. |
| 6 | `test_check_policy_reports_violations` | `--check-policy` against a fixture with overstayed artefacts returns the correct violation list (text + JSON). |
| 7 | `test_unknown_row_id_clear_error` | Unknown `--row-id` exits 1 with a message that names the file scanned and the id searched. |
| 8 | `test_unknown_run_id_clear_error` | Unknown `--run-id` exits 1; same shape. |
| 9 | `test_purge_path_traversal_refused` | A tampered audit log that points `staging_path` outside `output_dir` is refused (defence-in-depth from `_approve.py`). |
| 10 | `test_dry_run_does_not_modify` | `--dry-run` prints what would be deleted; corpus + audit log byte-identical post-call. |
| 11 | `test_concurrent_purge_calls_atomic` | Two parallel `forgelm purge --row-id <same>` invocations: the second sees a "row already erased" notice and exits 0; one event chain has at most one `data.erasure_completed` for that row. |

Tests 1-7 are the closure-plan minimum (7 tests in §8 Phase 21 acceptance); 8-11 are added defensive coverage so the next audit doesn't find regression gaps.

---

## 8. Implementation file map (Phase 21)

| File | Change |
|---|---|
| `forgelm/config.py` | Add `RetentionConfig` Pydantic block; `ForgeConfig.retention: Optional[RetentionConfig] = None`. |
| `forgelm/cli/subcommands/_purge.py` (new) | Dispatcher with the three modes. |
| `forgelm/cli/_parser.py` | `_add_purge_subcommand` registrar + flag definitions. |
| `forgelm/cli/_dispatch.py` | Add `purge` branch. |
| `forgelm/cli/__init__.py` | Re-export `_run_purge_cmd` for tests. |
| `forgelm/compliance.py` | Add `record_erasure_event(target_kind, target_id, justification, dry_run, **fields)` helper that the dispatcher calls. |
| `forgelm/erasure.py` (new, optional) | If the corpus-rewrite + run-scoped delete logic grows past ~150 lines, split it out of `_purge.py` so the dispatcher stays thin. |
| `forgelm/trainer.py` | Pre-flight retention check in `ForgeTrainer.train()` (when `config.retention` is set). |
| `tests/test_gdpr_erasure.py` (new) | The 11 tests from §7. |
| `docs/guides/gdpr_erasure.md` (new) | Operator how-to. |
| `docs/guides/safety_compliance.md` | Add a new `## GDPR right-to-erasure (Article 17)` section after the PII section using the §6 text. |
| `docs/usermanuals/en/compliance/gdpr.md` (+ `docs/usermanuals/tr/compliance/gdpr.md`) | Rewrite lines 51-64 YAML example: replace `ingestion.retention.raw_documents.ttl_days` with the top-level `retention.*` block per §3.1; update the `forgelm purge` subsection to match the §4.2 flag surface. |
| `docs/qms/sop_data_management.md` | Add a "Retention + erasure procedure" subsection cross-linking to the new guide. |
| `docs/reference/audit_event_catalog.md` (+ -tr) | Six new event rows (§5.1): `data.erasure_requested`, `data.erasure_completed`, `data.erasure_failed`, `data.erasure_warning_memorisation`, `data.erasure_warning_synthetic_data_present`, `data.erasure_warning_external_copies`. |
| `docs/reference/configuration.md` | (auto-gen via Phase 16 once that lands; for Phase 21 we hand-add the rows so they appear immediately). |
| `CHANGELOG.md` | `[Unreleased]` → "GDPR right-to-erasure" entry. |
| `forgelm/__init__.py` | (Phase 19 territory — Library API exposes `forgelm.purge_row` etc. — Phase 21 only ships the subcommand.) |

---

## 9. Wave 1 review carry-over absorption

The closure plan §15.5 lists three open items that intersect Phase 21:

| Carry-over | Resolution in this design |
|---|---|
| **GH-023** — `ingestion.retention.raw_documents.ttl_days` config key cited by GDPR docs but not in `ForgeConfig` | Phase 21 adds `RetentionConfig` *as a top-level `retention:` block*, not nested under `ingestion`.  The GDPR docs are updated to reference `retention.audit_log_retention_days` etc.  An `ingestion.retention.*` alias is **not** added — the docs were ahead of the implementation, and the cleaner shape is to put retention under its own root key (it covers more than just ingestion artefacts). |
| **GH-013** — `forgelm purge` documented but not implemented | Phase 21 ships exactly the form documented in `docs/usermanuals/en/compliance/gdpr-erasure.md` (`--row-id`, `--corpus`, `--run-id`, `--kind`, `--check-policy`).  The user-manual page becomes accurate post-merge. |
| **Concurrent-approve race** | Phase 21 introduces `acquire_run_lock(run_id)` context manager (filelock pattern) for the audit-log + purge + approve coordination.  Phase 22 ISO/SOC 2 scope already lists this; Phase 21 is the first place we actually need it (concurrent purge of the same row), so we ship the lock here and Phase 22 documents the broader policy. |

---

## 10. Open questions resolved

### Q1. Should `RetentionConfig` live under `ingestion.retention.*` or as a top-level `retention.*`?

**Decision:** top-level `retention.*`.  Reasons: (a) audit-log retention has nothing to do with ingestion; (b) the GDPR docs that referenced the nested form were written before the implementation existed — a top-level block is the cleaner shape.  GH-023 carry-over: docs update follows the code, not vice versa.

### Q2. Should `forgelm purge --row-id` accept a regex / glob?

**Decision:** no.  Erasure is a high-impact action; a glob that matched 1000 rows when the operator meant 1 row would be a disaster.  One row per call.  An operator with a large erasure batch wraps `forgelm purge` in a loop and has the audit log show the per-row provenance.

### Q3. Should we ship a "machine unlearning" stub even as a no-op?

**Decision:** no.  The only honest signal is the `data.erasure_warning_memorisation` audit event when an operator runs `--row-id` against a corpus that already produced model weights.  A stub function that returns success would be a Trion-style false claim; we explicitly avoid it.

### Q4. Should erasure be loud (interactive confirmation) by default?

**Decision (revised — shipped behaviour as of v0.5.5):** non-interactive, with consent carried by `--justification`. The original design called for an interactive `[y/N]` prompt gated by `--yes`, but the shipped `forgelm purge` is **non-interactive on every invocation** — it never prompts, and the `--yes` flag was not wired (see §3.5). Misuse defence is now provided by (a) the mandatory `--justification` text (which lands in the `data.erasure_*` audit event as the consent record), (b) `--dry-run` as the operator's preview path, and (c) `--row-matches=all` as the explicit opt-in for multi-row deletes. The `--yes` flag is preserved on the Phase 28+ backlog if a future review-prompt mode is added.

### Q5. Should `--check-policy` return non-zero when violations are found?

**Decision:** no.  `--check-policy` is a *report*, not a *gate*.  The CI gate is the trainer pre-flight (when `enforce: block_on_excess` is configured).  Mixing gate + report semantics into one exit code makes the tool harder to script.  Operators who want a CI gate use `--output-format json` and pipe to `jq '.violations | length'` themselves.

### Q6. Should we delete the synthetic-data snapshots on row erasure?

**Decision:** out of scope for Phase 21.  Synthetic data was generated from a teacher prompt that referenced the corpus; the relationship row → synthetic snippet is not preserved.  Phase 21 emits a `data.erasure_warning_synthetic_data_present` event when synthetic snapshots exist for the run; the operator decides whether to regenerate.

---

## 11. Out of scope (deliberately)

- **Per-row consent metadata** — the consent record is the operator's CRM / data platform problem; ForgeLM consumes a JSONL row as input and trusts the operator's lawful basis.
- **GDPR Articles 13-14 (notice obligations)** — also runtime / data-platform concern.
- **Cross-corpus dedup propagation** — if row 42 in `train.jsonl` was a near-duplicate of row 17 in `validation.jsonl`, the operator must purge both with two calls.
- **Federated / multi-node erasure** — Phase 21 is single-node.
- **Fully-automated retention enforcement** (cron, scheduler, etc.) — out of scope for the toolkit; the operator wires their own scheduler around `forgelm purge --check-policy`.
- **Erasure of historical CHANGELOG entries / git history** — not within ForgeLM's reach; operator's git tooling.

---

## 12. Implementation status

The design above shipped end-to-end in v0.5.5:

- **`RetentionConfig`** (§3): pinned in `forgelm/config.py` as a top-level `retention:` block with five fields (`audit_log_retention_days`, `staging_ttl_days`, `ephemeral_artefact_retention_days`, `raw_documents_retention_days`, `enforce`). `EvaluationConfig.staging_ttl_days` deprecated with `DeprecationWarning` + alias-forwarding to `retention.staging_ttl_days`.
- **`forgelm purge` subcommand** (§4): pinned in `forgelm/cli/subcommands/_purge.py` with `--row-id`, `--corpus`, `--run-id`, `--kind`, `--check-policy`, atomic-rewrite + recovery semantics.
- **Audit events** (§5): six new events live in `audit_event_catalog.md` — `data.erasure_requested`, `data.erasure_completed`, `data.erasure_failed`, plus three operator-warning events.
- **Tests** (§7): `tests/test_gdpr_erasure.py` ships the regression coverage (unknown row id, multi-match refusal, partial-commit recovery, retention horizon enforcement, audit-PII redaction, etc.).
- **User-facing docs** (§6, §8): `docs/guides/gdpr_erasure{,-tr}.md` + `docs/usermanuals/{en,tr}/compliance/gdpr-erasure.md` reference the implemented surface.
