# Multi-Stage Training Pipelines

Phase 14 — landing in `v0.7.0` (CHANGELOG entry dated 2026-05-14; PyPI tag pending).

ForgeLM's `pipeline:` config block chains 2 or more training stages
(typically SFT → DPO → GRPO) into one config-driven, dry-run-validatable,
Annex-IV-traceable run.  Before Phase 14, the same workflow required
3+ separate config files, manual `model.name_or_path` editing between
stages, and an external shell script for orchestration.  After Phase 14,
a single `forgelm --config pipeline.yaml` invocation runs the entire
chain end-to-end.

---

## When to use a pipeline (and when not to)

Use the `pipeline:` block when **all** of the following hold:

- You are running 2 or more training stages in sequence (e.g. SFT
  followed by DPO).
- Each stage's input model is the previous stage's output model.
- You want a single Annex IV manifest covering the entire chain.

Do **not** use the `pipeline:` block when:

- You only need to run a single training paradigm.  Single-stage configs
  (the v0.6.0 default) remain the canonical case and stay byte-identical
  to their pre-Phase-14 behaviour.
- You need non-linear stage dependencies (DAG-shaped pipelines).
  Phase 14 ships sequential pipelines only; the schema reserves the
  surface for a future DAG extension.
- You want parallel stage execution (independent branches running
  concurrently).  Same horizon as the DAG support — Wave 2 or later.

---

## Anatomy of a pipeline config

```yaml
# Root config — provides defaults that every stage inherits from
# unless the stage overrides them.
model:
  name_or_path: "meta-llama/Llama-3-8B"      # Stage 0's starting model
lora:
  r: 8
  alpha: 16
training:
  trainer_type: "sft"                         # Used by stages that
                                              # inherit the training block
data:
  dataset_name_or_path: "./placeholder.jsonl" # Required at root; each
                                              # stage overrides per-stage

# Pipeline-level block — declares the chain.
pipeline:
  output_dir: "./pipeline_run"                # Hosts pipeline_state.json,
                                              # compliance/pipeline_manifest.json,
                                              # and the pipeline-level
                                              # audit_log.jsonl
  stages:
    - name: sft_stage                         # ^[a-z0-9_]{1,32}$
      training:
        trainer_type: "sft"                   # Required per stage
        output_dir: "./pipeline_run/stage1_sft"
        num_train_epochs: 3
      data:
        dataset_name_or_path: "./data/sft.jsonl"

    - name: dpo_stage
      training:
        trainer_type: "dpo"
        output_dir: "./pipeline_run/stage2_dpo"
        num_train_epochs: 1
        dpo_beta: 0.1
      data:
        dataset_name_or_path: "./data/preferences.jsonl"

    - name: grpo_stage
      training:
        trainer_type: "grpo"
        output_dir: "./pipeline_run/stage3_grpo"
        grpo_num_generations: 4
      data:
        dataset_name_or_path: "./data/math_prompts.jsonl"
```

**Auto-chain:** each stage's `model.name_or_path` is automatically set to
the previous stage's `training.output_dir/final_model` — you do not
write that path by hand, and you cannot accidentally point stage N's
DPO trainer at the base model instead of stage N-1's SFT output.

---

## Inheritance matrix

Section-wholesale override semantics — if a stage declares a top-level
block (`model`, `lora`, `training`, `data`, `evaluation`), the **entire**
block replaces the root's; if the stage omits the block, the root's
block is inherited verbatim.  No field-level deep-merge: "if you want to
inherit, omit the block; if you want to override, supply the full block."

| Section | Inheritance | Notes |
|---|---|---|
| `model.name_or_path` | **Auto-chained** (overrides root from stage 1 onward) | Set to previous stage's `training.output_dir/final_model`.  Stage 0 reads root `model.name_or_path`.  An explicit per-stage `model:` block disables auto-chaining for that stage (escape hatch). |
| `model.*` (other fields) | Inherited unless `model:` block overridden | `backend`, `load_in_4bit`, `trust_remote_code`, `max_length`, `chat_template` follow the root. |
| `lora` | Inherited unless `lora:` block overridden | **Critical edge case:** stage 2 DPO with a *different* `lora.r` than stage 1 SFT means stage 2 is **a fresh LoRA over the merged SFT model**, not a continuation of SFT's LoRA.  Operator-visible behaviour. |
| `data` | Inherited unless `data:` block overridden (strongly discouraged) | The schema does **not** force a per-stage override — operators may rerun the same dataset against a different trainer for ablation studies — but pipelines that legitimately reuse the same dataset across stages are vanishingly rare in production. The operator guide flags inheritance as a smell; almost every real chain has SFT use a curated SFT dataset, DPO use a preference-pairs dataset, and GRPO use a math/reward dataset. |
| `training` | Inherited unless `training:` block overridden | `trainer_type` MUST be set explicitly per stage (audit-clarity validator).  Other fields wholesale-replaced when the block is overridden. |
| `evaluation` | Inherited unless `evaluation:` block overridden | Per-stage gates (loss thresholds, auto_revert, safety, judge, human-approval) live here. |
| `distributed` | Root only — **per-stage rejected** | Distributed strategy must be consistent across the run. |
| `webhook` | Root only — **per-stage rejected** | Per-stage events carry the stage name in the payload. |
| `compliance` | Root only — **per-stage rejected** | Provider/system/risk metadata is pipeline-level. |
| `risk_assessment`, `monitoring`, `retention`, `synthetic`, `merge`, `auth` | Root only — **per-stage rejected** | Pipeline-level concerns. |

A stage that declares any of the root-only sections is rejected at
config-load time with `EXIT_CONFIG_ERROR (1)` and the offending section
name in the message.

---

## CLI

```bash
# End-to-end pipeline run.
forgelm --config pipeline.yaml

# Dry-run validates every stage (Pydantic + chain integrity) before any
# GPU is allocated.  Collects all errors before exiting, like
# `pytest --collectonly`.
forgelm --config pipeline.yaml --dry-run

# Run a single named stage in isolation (audit / re-run scenarios).
# Non-first stages require the previous stage's on-disk output, or
# an explicit --input-model override.
forgelm --config pipeline.yaml --stage dpo_stage

# Resume after a failed / interrupted run from a named stage onward.
# Already-completed stages whose output paths exist on disk are
# skipped (logged at INFO).
forgelm --config pipeline.yaml --resume-from dpo_stage

# Override the auto-chained input model for a single stage (escape hatch).
# The audit log records `input_source: cli_override`.
forgelm --config pipeline.yaml --stage dpo_stage --input-model ./other/checkpoint
```

### `--stage <name>` partial-run rules

| Scenario | Behaviour |
|---|---|
| `--stage <name>` and `<name>` is the first stage | Reads root `model.name_or_path`; runs normally. |
| `--stage <name>` and the previous stage's `output_dir/final_model` exists on disk | Auto-chains as if the previous stage had just finished. |
| `--stage <name>` and the previous stage's output is missing | Hard-fails with `EXIT_CONFIG_ERROR (1)`: `Stage <name> requires <prev_stage> output at <path>; pass --input-model <path> to override or run the full pipeline first.`  No silent fallback to root `model.name_or_path`. |
| `--stage <name> --input-model <path>` | Operator escape hatch: skips the auto-chain, uses `<path>`.  Audit-log records `input_source: cli_override`. |
| `--stage <name>` where `<name>` is not in the config | Hard-fails at parse time with the list of valid stage names. |

### `--resume-from <name>` semantics

- State file: `<pipeline.output_dir>/pipeline_state.json` (atomic-write).
- Stages with `status: completed` whose `output_model` path still exists
  on disk are **skipped** and their per-stage training manifests are
  preserved.  The named stage and every stage after it are re-run.
- **Stale-state guard:** if the on-disk state file's
  `pipeline_config_hash` differs from the current parse of the YAML
  bytes, resume fails with `EXIT_CONFIG_ERROR (1)` — preventing
  "I resumed against a config that was edited mid-flight" silent
  divergence.  Override via `--force-resume` (logged at WARNING,
  recorded in the audit event).
- Phase 14 resumes at **stage boundaries** only.  Intra-stage HF
  `Trainer.train(resume_from_checkpoint=...)` integration is deferred
  to a Phase 14.x follow-up — see Limitations below.

---

## Human-approval gate within a pipeline

If a stage carries `evaluation.require_human_approval: true`, ForgeLM's
existing Phase 9 flow runs unchanged: the model lands in
`final_model.staging.<run_id>/`, the orchestrator captures the staging
path in the pipeline state with `status: gated_pending_approval`, and
the run exits with `EXIT_AWAITING_APPROVAL (4)`.

Downstream stages stay `pending` (not `skipped_due_to_prior_revert`)
so a subsequent resume picks them up after the approval:

```bash
# Stage 1 SFT gates pending approval — pipeline exits 4.
$ forgelm --config pipeline.yaml
# ... → exit 4

# Operator inspects the staged model, then approves.
$ forgelm approve <run_id> --output-dir ./pipeline_run/stage1_sft
# ... → final_model/ is promoted, exit 0

# Resume the pipeline from DPO; SFT is skipped (status=completed
# on disk).  DPO's input_model points at the *promoted* final_model/
# path, not the staging path.
$ forgelm --config pipeline.yaml --resume-from dpo_stage
# ... → exit 0 when the rest of the chain succeeds
```

---

## Audit events

The orchestrator emits these events into the pipeline-level
`audit_log.jsonl` (under `pipeline.output_dir`):

- `pipeline.started` — run id, config hash, stage count, stage names
- `pipeline.stage_started` — stage name, index, input model, input source
- `pipeline.stage_completed` — stage name, gate decision (`passed` / `failed`), metrics summary
- `pipeline.stage_gated` — stage name, gate decision `approval_pending`, staging path (emitted when a stage exits `EXIT_AWAITING_APPROVAL`)
- `pipeline.stage_reverted` — stage name, auto-revert reason
- `pipeline.force_resume` — operator-approved stale-hash override with `old_config_hash` + `new_config_hash`
- `pipeline.completed` — final status, stopped-at stage (if any)

These events live alongside the existing `training.*` events that each
stage's `ForgeTrainer` continues to emit; pre-existing Slack / Teams
dashboards filtering on `training.failure` keep working unchanged.

The matching webhook methods are `notify_pipeline_started`,
`notify_pipeline_completed`, and `notify_pipeline_reverted` — see
`forgelm/webhook.py`.

---

## Annex IV manifest

Every stage transition rewrites `<pipeline.output_dir>/compliance/
pipeline_manifest.json` (atomic-write).  The pipeline manifest is the
**index** that ties the per-stage `training_manifest.json` files into
one verifiable chain; per-stage manifests remain individually valid
against the existing single-stage Annex IV schema.

Validate a complete run with the verifier:

```bash
forgelm verify-annex-iv --pipeline ./pipeline_run
```

The verifier returns exit 0 when:

1. Every required top-level key is present and well-formed.
2. Stage indices form `0..N-1` in order.
3. Every chain stage's `input_model` equals the previous executed
   stage's `output_model` (operator `--input-model` overrides are
   recorded with `input_source: cli_override` and exempted from this
   check, by design — auditors cross-reference the audit log to
   distinguish a legitimate override from a corrupt manifest).
4. Every per-stage `training_manifest` path points at a real file.
5. `stopped_at` (if set) names a real stage whose status is `failed`
   or `gated_pending_approval`.

A non-zero exit code lists the violations in the order they were
discovered.

---

## Limitations (Phase 14 Wave 1)

- **No intra-stage checkpoint resume.**  `--resume-from` picks up at
  stage boundaries only.  If a stage's `ForgeTrainer.train()` crashes
  half-way, the resume re-runs that stage from epoch 0.  Wave 2.
- **Sequential only — no DAG semantics.**  Stages execute in the order
  they appear in `pipeline.stages`.  Branches / fan-out / merge are
  deferred.
- **No parallel stage execution.**  Even when two stages are logically
  independent, the orchestrator runs them sequentially.
- **No multi-process locking on `pipeline.output_dir`.**  Running two
  `forgelm --config pipeline.yaml` invocations against the same
  `pipeline.output_dir` simultaneously will race on
  `pipeline_state.json` + the manifest — pick distinct
  `pipeline.output_dir` per concurrent run.  The atomic-write helper
  uses per-writer-unique temp filenames so the race surfaces as
  last-writer-wins (not a `FileNotFoundError` traceback), but the
  loser's per-stage progress is invisible to its own process.
- **Stage-vs-pipeline `output_dir` overlap is allowed but interleaves
  the audit log.**  When a stage's resolved `training.output_dir`
  equals `pipeline.output_dir`, the stage's `training.*` events and
  the pipeline's `pipeline.*` events land in the same
  `audit_log.jsonl` (one file per directory).  The hash-chain + per-
  line flock keep the merged file internally consistent and the
  `tests/fixtures/pipeline/inheritance_matrix.yaml` fixture
  deliberately exercises this layout, but SIEM filters that pin a
  single `event_kind` per file should pick distinct directories.
- **`--force-resume` topology guard catches positional breaks only.**
  Adding, deleting, or renaming a stage between runs is refused even
  under `--force-resume` (state.stages[i] would address a different
  stage than pipeline.stages[i]).  *Value-level* edits — different
  `trainer_type`, `training.output_dir`, hyperparameters under
  identical stage names — are accepted by design; `--force-resume`
  is the operator's signal that they understand the divergence.
- **No `forgelm wizard` integration.**  Single-stage configs are
  wizard-buildable; pipelines are operator-grade and use the manual
  YAML surface.  The wizard's job stays "produce a working single-stage
  config you can hand-edit into a pipeline if you outgrow it."
- **No bundled pipeline-demo notebook.**  The 11 demo notebooks under
  `notebooks/` already cover each training paradigm individually and
  remain the recommended interactive surface; an end-to-end pipeline
  demo would duplicate every notebook's setup boilerplate three times.
  The fixture suite under `tests/fixtures/pipeline/` gives reviewers
  exactly the same surface as a notebook would, with the advantage of
  being byte-comparable to a golden manifest.

---

## Cross-references

- Phase 14 shipped scope: [docs/roadmap/completed-phases.md](../roadmap/completed-phases.md#phase-14--multi-stage-pipeline-chains-v070)
- Phase 14.5 follow-up plan: [docs/roadmap/phase-14-5-pipeline-hardening.md](../roadmap/phase-14-5-pipeline-hardening.md)
- Roadmap entry: [docs/roadmap.md](../roadmap.md)
- Annex IV verifier: `forgelm verify-annex-iv --pipeline <run_dir>` (see CLI help)
- Audit log standard: [docs/standards/logging-observability.md](../standards/logging-observability.md)
- Single-stage trainer guide (everything inherits from this surface):
  [docs/guides/alignment.md](alignment.md)
