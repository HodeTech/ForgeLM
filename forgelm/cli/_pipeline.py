"""Phase 14 — Multi-stage training pipeline orchestrator.

Top-level entry point for ``forgelm --config pipeline.yaml`` runs.  The
orchestrator iterates over the ordered :class:`PipelineConfig.stages`,
materialises a flat :class:`ForgeConfig` per stage via
:func:`forgelm.config.merge_pipeline_stage_config`, hands it to a fresh
:class:`forgelm.trainer.ForgeTrainer`, and threads the state through a
crash-safe JSON state file + an append-only audit-log event stream.

Layering rule — see ``docs/roadmap/phase-14-pipeline-chains.md`` Task 2:
``forgelm/trainer.py`` is **not** aware of the pipeline layer.  Each
stage is a single-stage run from the trainer's point of view; only this
module knows there is an outer loop.  When a config carries no
``pipeline:`` block, this module is never imported.

Key responsibilities:

- **Auto-chain** each stage's ``model.name_or_path`` to the previous
  stage's output path (or honour an explicit stage override / CLI
  ``--input-model`` override).
- **Per-stage gates** — auto-revert and human-approval are composed
  one stage at a time; a failed gate stops the chain and marks
  downstream stages ``skipped_due_to_prior_revert``.
- **Crash-safe state** — every stage transition is atomically written to
  ``<root_output_dir>/pipeline_state.json`` (tmp file + ``os.replace``).
  ``--resume-from`` reads that file and skips ``completed`` stages whose
  outputs still exist on disk.
- **Annex-IV manifest** — every transition also rewrites
  ``<root_output_dir>/compliance/pipeline_manifest.json`` (delegated to
  :mod:`forgelm.compliance`) so reviewers can correlate every chain
  failure to a frozen artefact.
- **Audit + webhook** — emits ``pipeline.started`` / ``.stage_started``
  / ``.stage_completed`` / ``.stage_reverted`` / ``.completed`` audit
  events alongside the existing per-stage ``training.*`` vocabulary; the
  webhook notifier gains ``notify_pipeline_*`` methods that the orchestrator
  calls at the same transitions.
- **Exit-code routing** — the pipeline aggregates per-stage outcomes
  via ``worst_exit = max(worst_exit, code)``, which gives the
  precedence order
  ``EXIT_AWAITING_APPROVAL (4)`` > ``EXIT_EVAL_FAILURE (3)`` >
  ``EXIT_TRAINING_ERROR (2)`` > ``EXIT_CONFIG_ERROR (1)`` >
  ``EXIT_SUCCESS (0)``.  In practice an approval gate causes an
  immediate ``break`` (no later stages run) and any other failure
  sets ``chain_broken = True`` (downstream stages skip), so the
  precedence rule is reached only when a single stage produces a
  single non-zero code.  Numeric ordering of the exit-code constants
  matches the documented severity ramp — operators reading the exit
  code in a shell only see the "worst" outcome from a single stage
  that ran.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import ValidationError

from ..config import ConfigError, ForgeConfig, PipelineConfig, PipelineStage, merge_pipeline_stage_config
from ._exit_codes import (
    EXIT_AWAITING_APPROVAL,
    EXIT_CONFIG_ERROR,
    EXIT_EVAL_FAILURE,
    EXIT_SUCCESS,
    EXIT_TRAINING_ERROR,
)

logger = logging.getLogger("forgelm.pipeline")


# ---------------------------------------------------------------------------
# State enums + dataclasses
# ---------------------------------------------------------------------------


StageStatusLiteral = Literal[
    "pending",
    "running",
    "completed",
    "failed",
    "gated_pending_approval",
    "skipped_due_to_prior_revert",
    "skipped_by_filter",
]
"""Per-stage status values.  See Phase 14 Task 3 for the meaning of each."""


InputSourceLiteral = Literal["root", "chain", "stage_explicit", "cli_override"]
"""How the orchestrator resolved a stage's input model path.  Recorded on
the per-stage state so reviewers can trace any chain interruption
(``cli_override`` is the operator escape hatch that intentionally breaks
the chain)."""


FinalStatusLiteral = Literal[
    "in_progress",
    "completed",
    "stopped_at_stage",
    "gated_pending_approval",
]


@dataclass
class PipelineStageState:
    """Per-stage state snapshot persisted to disk + included in the manifest."""

    name: str
    index: int
    trainer_type: str
    status: StageStatusLiteral = "pending"
    input_model: Optional[str] = None
    input_source: Optional[InputSourceLiteral] = None
    output_model: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    duration_seconds: Optional[float] = None
    training_manifest: Optional[str] = None
    metrics: Dict[str, float] = field(default_factory=dict)
    gate_decision: Optional[str] = None
    auto_revert_triggered: bool = False
    skipped_reason: Optional[str] = None
    exit_code: Optional[int] = None
    error: Optional[str] = None


@dataclass
class PipelineState:
    """Top-level pipeline-run state snapshot.

    Round-tripped to ``pipeline_state.json`` after every stage transition.
    Compatible with :mod:`json` (atomic-write via tmp + ``os.replace``);
    no nested non-serialisable types.
    """

    pipeline_run_id: str
    pipeline_config_hash: str
    forgelm_version: str
    started_at: str
    finished_at: Optional[str] = None
    final_status: FinalStatusLiteral = "in_progress"
    stopped_at: Optional[str] = None
    stages: List[PipelineStageState] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc_iso_now() -> str:
    """UTC ISO-8601 timestamp with seconds precision.

    Mirrors the format ``forgelm/compliance.py`` already uses for audit
    log entries so timestamps line up across the two surfaces.
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _generate_run_id() -> str:
    """Return a pipeline run id of shape ``pl_YYYY-MM-DD_<6-hex>``.

    The date prefix lets operators eyeball runs by week without consulting
    the manifest; the 6-hex suffix gives ~16 million combinations per day,
    which is more than enough to disambiguate same-day re-runs even in
    aggressive CI loops.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"pl_{today}_{secrets.token_hex(3)}"


def _compute_pipeline_config_hash(pipeline_yaml_bytes: bytes) -> str:
    """SHA-256 of the *raw bytes* of the pipeline YAML file.

    Hashing the bytes (not the parsed dict) is intentional: it locks the
    on-disk artefact, which is what regulators audit.  YAML
    comments / key ordering / whitespace are all in scope; an operator
    who edits the file between runs gets a different hash even if the
    semantic content is unchanged, and the ``--resume-from`` stale-state
    guard catches it.
    """
    return "sha256:" + hashlib.sha256(pipeline_yaml_bytes).hexdigest()


# The on-disk staging-suffix contract shared with ``forgelm approve``.  A gated
# stage's ``output_model`` points at ``<final_path>.staging.<run_id>``; after the
# operator runs ``forgelm approve`` that directory is renamed to ``<final_path>``.
# Kept in sync with ``forgelm/cli/subcommands/_approve.py`` (``_STAGING_SUFFIX``);
# not imported across the layer to avoid a CLI-subcommand → orchestrator cycle.
_STAGING_SUFFIX = ".staging"


def _derive_promoted_path(staging_path: str) -> str:
    """Return the promoted ``final_model/`` path for a gated stage's staging dir.

    Strips the staging suffix (and any runtime ``.<run_id>`` segment appended
    after it) from ``staging_path`` so ``final_model.staging.abc123`` yields
    ``final_model``.  Mirrors the derivation in
    ``forgelm/cli/subcommands/_approve.py`` (the rename target ``forgelm
    approve`` promotes to) — ``rfind`` locates the last occurrence of the
    suffix so a trailing run-id segment is handled correctly.
    """
    idx = staging_path.rfind(_STAGING_SUFFIX)
    return staging_path[:idx] if idx != -1 else staging_path


def _pipeline_paths(root_cfg: ForgeConfig) -> Dict[str, str]:
    """Resolve the canonical filesystem paths for the pipeline run.

    All pipeline-level artefacts (state file, manifest, audit log) live
    under :attr:`PipelineConfig.output_dir` (defaults to
    ``./pipeline_run``).  Per-stage trainer artefacts continue to live
    under each stage's own ``training.output_dir`` (as in single-stage
    runs) — the pipeline output dir is the *index* directory, distinct
    from the stage checkpoint directories so operators can ``rm -rf``
    individual stages without nuking the pipeline manifest.
    """
    if root_cfg.pipeline is None:  # defensive — orchestrator constructor catches this earlier
        raise ValueError("Cannot derive pipeline paths from a config with no `pipeline` block.")
    root_output_dir = root_cfg.pipeline.output_dir
    return {
        "root_output_dir": root_output_dir,
        "state_file": os.path.join(root_output_dir, "pipeline_state.json"),
        "manifest_dir": os.path.join(root_output_dir, "compliance"),
        "manifest_file": os.path.join(root_output_dir, "compliance", "pipeline_manifest.json"),
    }


def _atomic_write_json(path: str, payload: Dict[str, Any]) -> None:
    """Write ``payload`` to ``path`` atomically.

    Pattern: serialise to a sibling temp file then ``os.replace`` it
    onto the final name — guarantees a reader on any concurrent process
    either sees the previous version or the new version, never a half-
    written file.

    The temp filename is per-writer-unique (``<basename>.<pid>.<rand>.tmp``)
    rather than a shared ``<path>.tmp`` so two writers targeting the same
    final path don't truncate each other's tmp mid-write — pre-fix, a
    concurrent invocation produced ``FileNotFoundError`` from
    ``os.replace`` (the loser's tmp was unlinked by the winner's
    rename).  Phase 14 doesn't *support* concurrent orchestrators on
    the same ``pipeline.output_dir`` (see ``docs/guides/pipeline.md``
    "Limitations"), but the per-writer tmp is cheap defence so an
    accidental double-invocation surfaces as last-writer-wins rather
    than a Python traceback.  The temp lives in the same directory as
    ``path`` so ``os.replace`` stays a same-filesystem atomic rename.

    No fsync: the temp is not flushed to stable storage before the rename, so
    on power loss the *previous* version may survive instead of the new one.
    That is acceptable for resumable state/manifest files — a re-run
    re-derives the dropped update — but means this helper MUST NOT be reused
    for append-only audit data, where durability of each committed entry is a
    contract (use the audit logger's fsync-backed writer for that).
    """
    target_dir = os.path.dirname(path) or "."
    os.makedirs(target_dir, exist_ok=True)
    tmp = f"{path}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=False)
        os.replace(tmp, path)
    except Exception:
        # If the write failed before the replace, clean up the orphan
        # temp.  After a successful replace there's nothing to remove
        # (the tmp name no longer exists), so silently ignore that
        # branch.  Don't swallow the original exception either; we
        # only swallow the cleanup-of-orphan-tmp failure.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _serialise_state(state: PipelineState) -> Dict[str, Any]:
    """Return a JSON-ready dict representation of a :class:`PipelineState`."""
    return asdict(state)


def _deserialise_state(payload: Dict[str, Any]) -> PipelineState:
    """Round-trip :class:`PipelineState` from its on-disk JSON shape.

    Tolerates unknown keys (future-forward compat) and missing optionals
    (legacy state files).  Type-coerces ``stages[]`` back into
    :class:`PipelineStageState` instances so the rest of the orchestrator
    sees a strongly-typed object.
    """
    stages_raw = payload.get("stages", [])
    stages = [
        PipelineStageState(**{k: v for k, v in s.items() if k in PipelineStageState.__dataclass_fields__})
        for s in stages_raw
    ]
    return PipelineState(
        pipeline_run_id=payload["pipeline_run_id"],
        pipeline_config_hash=payload["pipeline_config_hash"],
        forgelm_version=payload.get("forgelm_version", "unknown"),
        started_at=payload["started_at"],
        finished_at=payload.get("finished_at"),
        final_status=payload.get("final_status", "in_progress"),
        stopped_at=payload.get("stopped_at"),
        stages=stages,
    )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class PipelineOrchestrator:
    """Drives a multi-stage pipeline run end-to-end.

    Constructor takes the root config and the *bytes* of the pipeline YAML
    (the bytes are hashed for the stale-resume guard; the parsed config
    drives the actual stage iteration).
    """

    def __init__(
        self,
        root_cfg: ForgeConfig,
        pipeline_yaml_bytes: bytes,
        *,
        output_format: str = "text",
    ) -> None:
        if root_cfg.pipeline is None:
            # Defensive — callers should only reach this path when a
            # ``pipeline:`` block is present.  A None ``pipeline`` here
            # signals a CLI dispatch bug; fail loud rather than running
            # an empty pipeline.
            raise ValueError("PipelineOrchestrator instantiated with a config that has no `pipeline` block.")
        self.root_cfg: ForgeConfig = root_cfg
        self.pipeline: PipelineConfig = root_cfg.pipeline
        self.config_hash: str = _compute_pipeline_config_hash(pipeline_yaml_bytes)
        self.output_format: str = output_format
        self.paths: Dict[str, str] = _pipeline_paths(root_cfg)
        # Lazy-initialised audit-log writer.  Pinned to the pipeline run
        # id on first event so every ``pipeline.*`` entry in the JSONL
        # stream carries the same top-level ``run_id`` (Phase 14 review
        # final-round F-B-1).  Pre-fix, ``_audit_event`` constructed a
        # fresh ``AuditLogger`` per call → every event got a different
        # auto-generated ``fg-<random>`` run_id, breaking SIEM-style
        # grouping and the Article 12 record-keeping correlation
        # contract.  The pipeline_run_id is set in state.pipeline_run_id
        # before any event fires, so the caller passes it through.
        self._audit_logger: Optional[Any] = None

    # -- run-mode entry points ------------------------------------------------

    def _prepare_resume_or_init_state(
        self,
        *,
        resume_from: Optional[str],
        force_resume: bool,
        stage_filter: Optional[str] = None,
    ) -> "tuple[Optional[PipelineState], Optional[int]]":
        """Load + validate state for ``--resume-from`` or build a fresh state.

        Returns ``(state, None)`` on success; ``(None, EXIT_CONFIG_ERROR)``
        when the resume path is invalid (missing state file or stale-hash
        refused without ``--force-resume``).  Extracted from
        :meth:`run` for Sonar python:S3776 cognitive-complexity hygiene.
        """
        existing = self._load_state_file()
        if resume_from is not None and existing is None:
            logger.error(
                "Cannot --resume-from %r: no pipeline_state.json found at %s.  Run the pipeline once first.",
                resume_from,
                self.paths["state_file"],
            )
            return None, EXIT_CONFIG_ERROR
        if resume_from is not None:
            refusal = self._validate_resume_state(existing, force=force_resume)
            if refusal is not None:
                return None, EXIT_CONFIG_ERROR
            # Reset the prior run's terminal fields so a resumed run is treated
            # as in-flight again.  Without this, a previously-failed run keeps
            # ``final_status="stopped_at_stage"`` (or a previously-gated run
            # keeps ``"gated_pending_approval"``) and ``_finalise_pipeline``'s
            # ``in_progress``-guarded rewrite never fires — so even a fully
            # successful resume emits a ``pipeline.completed`` carrying the
            # stale terminal status (or, for the gated case, emits nothing at
            # all).  History stays in the append-only audit log; the state file
            # is current-state, not history (P2-FAB-04).
            existing.final_status = "in_progress"
            existing.stopped_at = None
            existing.finished_at = None
            return existing, None

        # ``--stage X`` on a pipeline that already ran: build a fresh state but
        # CARRY OVER the prior run's stage history (status / output_model /
        # metrics) for every non-filtered stage when the topology is unchanged.
        # Otherwise ``_init_state`` clobbers pipeline_state.json — the other
        # stages become ``skipped_by_filter`` with ``output_model=None``, and a
        # later ``--resume-from`` re-trains them even though their ``final_model/``
        # sits on disk (F-P2-FAB-12).
        if stage_filter is not None and existing is not None:
            return self._init_state_preserving(existing, stage_filter=stage_filter), None
        return self._init_state(), None

    def _init_state_preserving(self, existing: PipelineState, *, stage_filter: str) -> PipelineState:
        """Fresh state for a ``--stage`` run that retains prior stage history.

        Carries over the prior run's per-stage ``status`` / ``output_model`` /
        ``metrics`` for every stage except the filtered one, but only when the
        stage topology is unchanged (same ordered stage names). On a topology
        mismatch we fall back to a clean ``_init_state`` — positional carry-over
        would otherwise graft one stage's history onto another.
        """
        fresh = self._init_state()
        prior_names = [s.name for s in existing.stages]
        current_names = [s.name for s in fresh.stages]
        if prior_names != current_names:
            return fresh
        prior_by_name = {s.name: s for s in existing.stages}
        for stage_state in fresh.stages:
            if stage_state.name == stage_filter:
                continue
            prior = prior_by_name.get(stage_state.name)
            if prior is None:
                continue
            stage_state.status = prior.status
            stage_state.output_model = prior.output_model
            stage_state.input_model = prior.input_model
            stage_state.input_source = prior.input_source
            stage_state.metrics = dict(prior.metrics)
            stage_state.gate_decision = prior.gate_decision
            stage_state.training_manifest = prior.training_manifest
        return fresh

    def _resolve_resume_skiplist(
        self,
        *,
        state: PipelineState,
        resume_from: str,
    ) -> "tuple[Optional[List[str]], Optional[int]]":
        """Compute the list of already-completed stages to skip on resume.

        Returns ``(skiplist, None)`` on success or ``(None,
        EXIT_CONFIG_ERROR)`` when ``resume_from`` names a stage that
        doesn't exist.  Stages before the resume index whose status is
        ``completed`` and whose ``output_model`` directory exists on
        disk are kept-as-is.  Extracted from :meth:`run` for Sonar
        python:S3776 cognitive-complexity hygiene.
        """
        try:
            resume_idx = next(i for i, s in enumerate(self.pipeline.stages) if s.name == resume_from)
        except StopIteration:
            logger.error(
                "Cannot --resume-from %r: stage name not found in pipeline (valid names: %s).",
                resume_from,
                ", ".join(s.name for s in self.pipeline.stages),
            )
            return None, EXIT_CONFIG_ERROR
        # Skip-eligibility is decided on the **state-file status**, NOT on
        # disk presence of ``output_model``.  A stage that halted at the
        # Article 14 human-approval gate is recorded as
        # ``gated_pending_approval`` and its ``output_model`` points at the
        # UNAPPROVED staging directory (``final_model.staging.<run_id>``).
        # That directory existing on disk is exactly NOT proof the artefact
        # may be consumed — skipping such a stage would chain downstream
        # training from a model no human has signed off, defeating the gate
        # (P2-FAB-01).
        #
        # ``forgelm approve`` promotes by **renaming** the staging dir to the
        # canonical ``final_model/`` path and does not (cannot — it runs against
        # a single stage's output dir, not the pipeline state file) rewrite
        # ``pipeline_state.json``.  So a post-approval resume still sees
        # ``status=gated_pending_approval`` on disk.  We distinguish the two
        # cases by the exact filesystem transition ``approve`` performs: the
        # UNAPPROVED staging dir is **renamed away** and the **derived promoted
        # path** (staging suffix stripped) now exists in its place.  Approval is
        # therefore proven only when the staging dir is GONE *and* the promoted
        # path is present — that stage is skip-eligible and downstream must chain
        # from the *promoted* path (P2-FAB-03).  While the staging dir is still
        # on disk (or the promoted path is absent), no human has signed off:
        # refuse with an actionable error (P2-FAB-01).  Requiring the staging dir
        # to be gone — not merely the promoted path to exist — keeps an unrelated
        # ``final_model/`` sitting next to a still-staged gate from being
        # mistaken for an approval.
        stages_to_skip_completed: List[str] = []
        for prior_state in state.stages[:resume_idx]:
            if prior_state.status == "gated_pending_approval":
                staging_path = prior_state.output_model
                promoted_path = _derive_promoted_path(staging_path) if staging_path else None
                approved = bool(
                    staging_path and promoted_path and not os.path.isdir(staging_path) and os.path.isdir(promoted_path)
                )
                if approved:
                    # Operator approved (staging renamed → final_model/).  Skip
                    # the stage and rewrite ``output_model`` to the promoted
                    # path so the auto-chain seeds downstream from the approved
                    # weights, not the vanished staging dir.  Flip the status to
                    # ``completed`` so the persisted state stops claiming the
                    # stage is still pending approval after a successful resume.
                    prior_state.output_model = promoted_path
                    prior_state.status = "completed"
                    stages_to_skip_completed.append(prior_state.name)
                    logger.info(
                        "Resuming: gated stage %r was approved (promoted model at %s); "
                        "skipping and chaining from the approved model.",
                        prior_state.name,
                        promoted_path,
                    )
                    continue
                logger.error(
                    "Cannot --resume-from %r: prior stage %r is awaiting human approval "
                    "(status=gated_pending_approval) and has NOT been approved.  Resuming "
                    "would chain downstream training from the unapproved staging model and "
                    "bypass the Article 14 gate.  Run `forgelm approve <run_id> --output-dir "
                    "<stage_output_dir>` (or `forgelm reject ...`) before resuming.",
                    resume_from,
                    prior_state.name,
                )
                # Article 14: record the blocked-resume decision in the
                # append-only audit log so the refusal is reviewable.
                self._audit_event(
                    "pipeline.resume_refused",
                    pipeline_run_id=state.pipeline_run_id,
                    requested_stage=resume_from,
                    blocking_stage=prior_state.name,
                    blocking_status=prior_state.status,
                )
                return None, EXIT_CONFIG_ERROR
            if (
                prior_state.status == "completed"
                and prior_state.output_model
                and os.path.isdir(prior_state.output_model)
            ):
                stages_to_skip_completed.append(prior_state.name)
                logger.info(
                    "Resuming: stage %r (%s) already produced output at %s; skipping.",
                    prior_state.name,
                    prior_state.status,
                    prior_state.output_model,
                )
        return stages_to_skip_completed, None

    def _resolve_stage_filter_setup(
        self,
        *,
        stage_filter: str,
        input_model_override: Optional[str],
    ) -> "tuple[Optional[str], Optional[int]]":
        """Validate ``--stage <name>`` + seed the auto-chain on a non-first stage.

        Returns ``(prev_output_for_filter, None)`` on success or
        ``(None, EXIT_CONFIG_ERROR)`` when the stage name is unknown or
        the previous stage's output directory is missing (and no
        ``--input-model`` override was supplied to bypass the chain).
        Extracted from :meth:`run` for Sonar python:S3776 cognitive-
        complexity hygiene.
        """
        if not any(s.name == stage_filter for s in self.pipeline.stages):
            logger.error(
                "Cannot --stage %r: stage name not found in pipeline (valid names: %s).",
                stage_filter,
                ", ".join(s.name for s in self.pipeline.stages),
            )
            return None, EXIT_CONFIG_ERROR

        if input_model_override is not None:
            return None, None

        filter_idx = next(i for i, s in enumerate(self.pipeline.stages) if s.name == stage_filter)
        if filter_idx == 0:
            return None, None

        # A stage that declares its own ``model:`` block ignores the auto-chain
        # seed entirely (merge priority: stage-explicit model wins; the chain
        # applies only when ``stage.model is None``). Demanding the predecessor's
        # output on disk is a spurious refusal for such a stage (F-P2-FAB-11).
        if self.pipeline.stages[filter_idx].model is not None:
            return None, None

        prev_stage = self.pipeline.stages[filter_idx - 1]
        # Guard the merge like the in-loop _merge_and_validate_stage does: a
        # per-stage Pydantic/merge failure here must route to EXIT_CONFIG_ERROR
        # via the logged error path, not escape as a raw traceback (F-P2-FAB-13).
        try:
            prev_merged = merge_pipeline_stage_config(self.root_cfg, prev_stage, prev_output_model=None)
        except (ConfigError, ValidationError) as exc:
            logger.error(
                "Cannot --stage %r: the preceding stage %r failed config validation: %s",
                stage_filter,
                prev_stage.name,
                exc,
            )
            return None, EXIT_CONFIG_ERROR
        candidate = os.path.join(prev_merged.training.output_dir, "final_model")
        if not os.path.isdir(candidate):
            logger.error(
                "Stage %r requires the previous stage's output at %s; "
                "pass --input-model <path> to override or run the full pipeline first.",
                stage_filter,
                candidate,
            )
            return None, EXIT_CONFIG_ERROR
        return candidate, None

    def _handle_stage_outcome(
        self,
        *,
        stage: PipelineStage,
        stage_state: PipelineStageState,
        exit_code: int,
        state: PipelineState,
        worst_exit: int,
    ) -> "tuple[int, Optional[str], bool, bool]":
        """Persist + audit + webhook the outcome of a single executed stage.

        Returns ``(worst_exit, new_prev_output, chain_broken, should_break)``:

        - ``worst_exit`` — the updated highest-priority exit code so far.
        - ``new_prev_output`` — the auto-chain seed for the next stage
          (``stage_state.output_model`` on success, ``None`` otherwise).
        - ``chain_broken`` — ``True`` for any non-success outcome; the
          caller marks downstream stages skipped on it.
        - ``should_break`` — ``True`` only for ``EXIT_AWAITING_APPROVAL``;
          the caller exits the stage loop immediately so a subsequent
          ``--resume-from`` picks up the remaining stages post-approval.

        Extracted from :meth:`run` for Sonar python:S3776 cognitive-
        complexity hygiene.
        """
        if exit_code == EXIT_AWAITING_APPROVAL:
            # Human-approval gate fired.  Pipeline exits; remaining
            # stages stay ``pending`` so a subsequent ``--resume-from``
            # picks them up after operator action.
            stage_state.status = "gated_pending_approval"
            state.final_status = "gated_pending_approval"
            state.stopped_at = stage.name
            state.finished_at = _utc_iso_now()
            new_worst = max(worst_exit, EXIT_AWAITING_APPROVAL)
            self._save_state(state)
            # Phase 14 review F-N-1: dedicated ``pipeline.stage_gated``
            # event (instead of ``pipeline.stage_completed`` with a
            # ``gate_decision=approval_pending`` sub-field).  Lets
            # dashboard / SIEM rules filter on the event name alone.
            self._audit_event(
                "pipeline.stage_gated",
                pipeline_run_id=state.pipeline_run_id,
                stage_name=stage.name,
                gate_decision="approval_pending",
                staging_path=stage_state.output_model,
            )
            return new_worst, None, True, True

        if exit_code == EXIT_SUCCESS:
            stage_state.status = "completed"
            stage_state.gate_decision = "passed"
            self._save_state(state)
            self._audit_event(
                "pipeline.stage_completed",
                pipeline_run_id=state.pipeline_run_id,
                stage_name=stage.name,
                gate_decision="passed",
                metrics=stage_state.metrics,
            )
            return worst_exit, stage_state.output_model, False, False

        # Anything else (EXIT_TRAINING_ERROR / EXIT_EVAL_FAILURE):
        # chain stops; downstream stages will skip with
        # ``skipped_due_to_prior_revert``.
        stage_state.status = "failed"
        stage_state.gate_decision = "failed"
        state.stopped_at = stage.name
        new_worst = max(worst_exit, exit_code)
        self._save_state(state)
        self._audit_event(
            "pipeline.stage_reverted" if stage_state.auto_revert_triggered else "pipeline.stage_completed",
            pipeline_run_id=state.pipeline_run_id,
            stage_name=stage.name,
            gate_decision="failed",
            auto_revert_triggered=stage_state.auto_revert_triggered,
        )
        self._notify_pipeline(
            "reverted" if stage_state.auto_revert_triggered else "stage_failed",
            state,
            stage_state=stage_state,
        )
        return new_worst, None, True, False

    def _execute_one_stage(
        self,
        *,
        index: int,
        stage: PipelineStage,
        state: PipelineState,
        prev_output: Optional[str],
        stage_filter: Optional[str],
        input_model_override: Optional[str],
    ) -> int:
        """Setup + invoke a single stage's trainer.  Returns the per-stage exit code.

        Caller is responsible for translating the returned exit code
        into the next-state transition via :meth:`_handle_stage_outcome`.
        Extracted from :meth:`run` for Sonar python:S3776 hygiene.
        """
        stage_state = state.stages[index]
        stage_state.index = index
        stage_state.name = stage.name
        stage_state.trainer_type = (
            stage.training.trainer_type if stage.training else self.root_cfg.training.trainer_type
        )
        stage_state.started_at = _utc_iso_now()
        stage_state.status = "running"
        # Clear every attempt-scoped field from a prior attempt loaded off the
        # resumed state file. Otherwise a stage that failed then resumed-to-success
        # persists status="completed" alongside a stale error="Trainer crashed: …"
        # (and a stale skipped_reason / gate_decision), so the green state file
        # reports per-stage errors that did not occur this run (F-P2-FAB-07).
        stage_state.error = None
        stage_state.skipped_reason = None
        stage_state.gate_decision = None
        stage_state.auto_revert_triggered = False
        stage_state.exit_code = None
        stage_state.finished_at = None
        stage_state.duration_seconds = None
        stage_state.output_model = None
        self._save_state(state)

        # ``--input-model`` attaches to a single filtered stage only.
        override_for_this_stage = (
            input_model_override if (stage_filter is not None and stage.name == stage_filter) else None
        )

        exit_code = self._run_single_stage(
            stage=stage,
            stage_state=stage_state,
            prev_output=prev_output,
            input_model_override=override_for_this_stage,
            state=state,
        )
        stage_state.exit_code = exit_code
        stage_state.finished_at = _utc_iso_now()
        try:
            start_dt = datetime.fromisoformat(stage_state.started_at)
            end_dt = datetime.fromisoformat(stage_state.finished_at)
            stage_state.duration_seconds = (end_dt - start_dt).total_seconds()
        except (TypeError, ValueError):
            stage_state.duration_seconds = None
        return exit_code

    def _finalise_pipeline(self, state: PipelineState, worst_exit: int) -> None:
        """Mark the pipeline finished + emit the terminal events.

        Mirrors the run-loop tail.  Extracted from :meth:`run` for
        Sonar python:S3776 cognitive-complexity hygiene.

        On entry ``final_status`` is ``in_progress`` for any run that ran
        the stage loop to its natural end (fresh runs start there; a
        ``--resume-from`` run is reset to ``in_progress`` in
        :meth:`_prepare_resume_or_init_state` so a resumed-successful run
        no longer carries the prior run's stale ``stopped_at_stage`` /
        ``gated_pending_approval`` terminal status — P2-FAB-04).  The only
        way it is *not* ``in_progress`` here is when a stage re-gated this
        run (``gated_pending_approval``); that is a coherent terminal state
        — the gate already set ``stopped_at`` / ``finished_at`` and emitted
        ``pipeline.stage_gated``, so we persist it but emit no
        ``pipeline.completed``.
        """
        if state.final_status == "in_progress":
            state.final_status = "completed" if worst_exit == EXIT_SUCCESS else "stopped_at_stage"
            state.finished_at = _utc_iso_now()
        self._save_state(state)

        if state.final_status in ("completed", "stopped_at_stage"):
            self._audit_event(
                "pipeline.completed",
                pipeline_run_id=state.pipeline_run_id,
                final_status=state.final_status,
                stopped_at=state.stopped_at,
            )
            self._notify_pipeline("completed", state)

    def _stage_skip_reason(
        self,
        *,
        stage: PipelineStage,
        stage_state: PipelineStageState,
        state: PipelineState,
        stage_filter: Optional[str],
        stages_to_skip_completed: List[str],
        chain_broken: bool,
    ) -> Optional[str]:
        """Decide whether the stage should be skipped, and update state accordingly.

        Returns one of:

        - ``"filtered"`` — ``--stage X`` was set and this is not the
          named stage.  Status set to ``skipped_by_filter``.
        - ``"already_completed"`` — ``--resume-from`` picked this stage
          up as already-done; caller seeds ``prev_output`` from
          ``stage_state.output_model``.
        - ``"chain_broken"`` — a prior stage in the same run reverted
          or failed; status set to ``skipped_due_to_prior_revert``.
        - ``None`` — proceed to execute.

        Extracted from :meth:`run` for Sonar python:S3776 cognitive-
        complexity hygiene.
        """
        if stage_filter is not None and stage.name != stage_filter:
            # Preserve a stage's prior ``completed`` history (carried over by
            # ``_init_state_preserving``) instead of downgrading it to
            # ``skipped_by_filter`` with no ``output_model`` — otherwise a later
            # ``--resume-from`` would re-train a stage whose ``final_model/`` is
            # already on disk (F-P2-FAB-12).  Only stamp ``skipped_by_filter`` on
            # stages with no prior completed output to record.
            if not (stage_state.status == "completed" and stage_state.output_model):
                stage_state.status = "skipped_by_filter"
                stage_state.skipped_reason = f"--stage {stage_filter!r} was specified; only that stage runs."
            self._save_state(state)
            return "filtered"
        if stage.name in stages_to_skip_completed:
            return "already_completed"
        if chain_broken:
            stage_state.status = "skipped_due_to_prior_revert"
            stage_state.skipped_reason = (
                f"Stage {state.stopped_at!r} triggered auto_revert; downstream stages did not run."
            )
            self._save_state(state)
            return "chain_broken"
        return None

    def _run_stage_loop(
        self,
        *,
        state: PipelineState,
        stage_filter: Optional[str],
        stages_to_skip_completed: List[str],
        input_model_override: Optional[str],
        prev_output_for_filter: Optional[str],
    ) -> int:
        """Iterate over ``self.pipeline.stages`` and return the aggregated ``worst_exit``.

        Each iteration is one of three things:

        1. A skipped stage (filter / already-done / chain broken) — see
           :meth:`_stage_skip_reason` for the routing rules.
        2. An executed stage — :meth:`_execute_one_stage` runs the
           trainer and :meth:`_handle_stage_outcome` records the
           transition + audit events.
        3. A gated stage (``EXIT_AWAITING_APPROVAL``) — the outcome
           handler sets ``should_break`` and the loop returns early so
           downstream stages stay ``pending`` for a subsequent
           ``--resume-from``.

        Extracted from :meth:`run` for Sonar python:S3776 cognitive-
        complexity hygiene.
        """
        worst_exit: int = EXIT_SUCCESS
        prev_output: Optional[str] = prev_output_for_filter
        chain_broken: bool = False
        for i, stage in enumerate(self.pipeline.stages):
            stage_state = state.stages[i]
            skip_reason = self._stage_skip_reason(
                stage=stage,
                stage_state=stage_state,
                state=state,
                stage_filter=stage_filter,
                stages_to_skip_completed=stages_to_skip_completed,
                chain_broken=chain_broken,
            )
            if skip_reason == "already_completed":
                prev_output = stage_state.output_model
                continue
            if skip_reason is not None:
                continue

            exit_code = self._execute_one_stage(
                index=i,
                stage=stage,
                state=state,
                prev_output=prev_output,
                stage_filter=stage_filter,
                input_model_override=input_model_override,
            )
            worst_exit, new_prev_output, chain_broken_now, should_break = self._handle_stage_outcome(
                stage=stage,
                stage_state=stage_state,
                exit_code=exit_code,
                state=state,
                worst_exit=worst_exit,
            )
            prev_output = new_prev_output if new_prev_output is not None else prev_output
            chain_broken = chain_broken or chain_broken_now
            if should_break:
                break
        return worst_exit

    def _check_output_dir_collisions(self) -> Optional[str]:
        """Pre-flight: detect two stages writing to the same ``training.output_dir``.

        Returns ``None`` on clean, or a single human-readable error
        string listing every collision.  Phase 14 post-release review:
        previously this check lived only in :meth:`dry_run`, so an
        operator running ``forgelm --config pipeline.yaml`` without
        first running ``--dry-run`` could overwrite per-stage manifests
        and checkpoints across stages silently.  Now called from both
        :meth:`dry_run` and :meth:`run` so the guard is unmissable.
        """
        prev_output: Optional[str] = None
        seen_dirs: Dict[str, str] = {}
        collisions: List[str] = []
        for stage in self.pipeline.stages:
            try:
                merged = merge_pipeline_stage_config(
                    self.root_cfg,
                    stage,
                    prev_output_model=prev_output,
                )
            except Exception as exc:  # noqa: BLE001 — see DEBUG log below
                # Merge failures are intentionally skipped here because
                # they re-surface through the per-stage pipeline-error
                # path with full diagnostics — the pre-flight's job is
                # scoped to ``output_dir`` layout only.  Codacy / bandit
                # B112 flagged the bare ``continue`` as a silent-skip
                # anti-pattern; logging the exception at DEBUG keeps the
                # skip *traceable* without aborting the pre-flight on
                # what is by-design a per-stage local failure.
                logger.debug(
                    "Pre-flight skipped collision check for stage %r: merge raised %s (%s); "
                    "the per-stage run path will surface the same error.",
                    stage.name,
                    type(exc).__name__,
                    exc,
                )
                continue
            abs_out = os.path.abspath(merged.training.output_dir)
            if abs_out in seen_dirs:
                collisions.append(
                    f"Stage {stage.name!r}: training.output_dir collides with stage "
                    f"{seen_dirs[abs_out]!r} (both resolve to {abs_out!r})."
                )
            else:
                seen_dirs[abs_out] = stage.name
            prev_output = os.path.join(merged.training.output_dir, "final_model")
        if collisions:
            return (
                "Pre-flight: per-stage `training.output_dir` collision(s) detected; "
                "per-stage checkpoints and manifests would overwrite each other.  "
                "Set a unique `training.output_dir` in each stage.\n  " + "\n  ".join(collisions)
            )
        return None

    def run(
        self,
        *,
        stage_filter: Optional[str] = None,
        resume_from: Optional[str] = None,
        force_resume: bool = False,
        input_model_override: Optional[str] = None,
    ) -> int:
        """Execute the pipeline.  Returns a process exit code.

        ``stage_filter`` and ``resume_from`` are mutually exclusive; the
        caller (CLI parser) validates this before reaching here.

        Exit-code aggregation uses ``worst_exit = max(worst_exit, code)``
        across stages, giving the numeric precedence:
        ``EXIT_AWAITING_APPROVAL (4)`` > ``EXIT_EVAL_FAILURE (3)`` >
        ``EXIT_TRAINING_ERROR (2)`` > ``EXIT_CONFIG_ERROR (1)`` >
        ``EXIT_SUCCESS (0)``.  An approval gate triggers an immediate
        ``break`` (later stages stay ``pending``) and any other non-zero
        outcome sets ``chain_broken = True`` (later stages skip with
        ``skipped_due_to_prior_revert``), so multi-stage compounding is
        rare — in practice the return value reflects the single failing
        stage's exit code.
        """
        # 0. Pre-flight: output_dir collisions across stages.  Same
        # guard that ``dry_run`` runs; mirrored here so an operator
        # who skipped ``--dry-run`` doesn't silently overwrite
        # per-stage checkpoints + manifests.
        collision_msg = self._check_output_dir_collisions()
        if collision_msg is not None:
            logger.error(collision_msg)
            return EXIT_CONFIG_ERROR

        # 1. Load (or initialise) state.
        state, state_err = self._prepare_resume_or_init_state(
            resume_from=resume_from, force_resume=force_resume, stage_filter=stage_filter
        )
        if state is None:
            return state_err  # type: ignore[return-value]

        # 2. Determine which stages to execute.
        stages_to_skip_completed: List[str] = []
        if resume_from is not None:
            skiplist, skiplist_err = self._resolve_resume_skiplist(state=state, resume_from=resume_from)
            if skiplist is None:
                return skiplist_err  # type: ignore[return-value]
            stages_to_skip_completed = skiplist

        # 2a. ``--stage`` validation + auto-chain seeding.
        prev_output_for_filter: Optional[str] = None
        if stage_filter is not None:
            seed, filter_err = self._resolve_stage_filter_setup(
                stage_filter=stage_filter, input_model_override=input_model_override
            )
            if filter_err is not None:
                return filter_err
            prev_output_for_filter = seed
            # Phase 14 review final-round F-B-2: surface the disk-seed
            # path on the *predecessor* stage's manifest entry as well.
            # When ``--stage X`` re-runs only the named stage, its
            # predecessor is recorded as ``skipped_by_filter`` with no
            # ``output_model``.  The filtered stage records
            # ``input_source: "chain"`` (we auto-chained from the seed),
            # so the strict chain verifier (F-B-3) compares stage X's
            # ``input_model`` against ``stages[X-1].output_model`` and
            # flags a ``chain_integrity_violation`` even though the
            # chain is intact on disk.  We seed the predecessor's
            # ``output_model`` here so the manifest faithfully
            # represents "stage X-1 produced this path (in a prior
            # run); stage X chained from it now".
            if seed is not None:
                filter_idx = next(i for i, s in enumerate(self.pipeline.stages) if s.name == stage_filter)
                if filter_idx > 0:
                    state.stages[filter_idx - 1].output_model = seed

        # 3. Emit pipeline.started + webhook (fresh runs only).
        if resume_from is None:
            self._audit_event(
                "pipeline.started",
                pipeline_run_id=state.pipeline_run_id,
                config_hash=self.config_hash,
                stage_count=len(self.pipeline.stages),
                stage_names=[s.name for s in self.pipeline.stages],
            )
            self._notify_pipeline("started", state)

        # 4. Iterate stages (delegated so this method stays at a
        # bounded cognitive-complexity score).
        worst_exit = self._run_stage_loop(
            state=state,
            stage_filter=stage_filter,
            stages_to_skip_completed=stages_to_skip_completed,
            input_model_override=input_model_override,
            prev_output_for_filter=prev_output_for_filter,
        )

        # 5+6. Final state + manifest + summary.
        self._finalise_pipeline(state, worst_exit)
        self._emit_summary(state)
        return worst_exit

    def dry_run(self) -> int:
        """Validate every stage without allocating any GPU resources.

        Collects all per-stage Pydantic / merge errors before exiting,
        mirroring ``pytest --collectonly``.  Returns
        :data:`EXIT_CONFIG_ERROR (1)` if any stage fails validation,
        :data:`EXIT_SUCCESS (0)` otherwise.

        Additionally checks that no two stages resolve to the same
        ``training.output_dir`` — colliding output dirs would cause one
        stage's checkpoints and per-stage Annex-IV manifest to overwrite
        another's, breaking the chain of custody silently (Phase 14
        review F-G-1).  Because stage-level ``training`` blocks
        *inherit* the root ``training`` block wholesale unless the
        stage supplies a full override, two stages that both inherit
        end up sharing the root's ``output_dir`` — that's the canonical
        misconfiguration this guard catches.
        """
        errors: List[str] = []
        prev_output: Optional[str] = None
        seen_dirs: Dict[str, str] = {}  # abs output_dir -> first stage that claimed it
        for stage in self.pipeline.stages:
            try:
                merged = merge_pipeline_stage_config(
                    self.root_cfg,
                    stage,
                    prev_output_model=prev_output,
                )
            except Exception as exc:  # noqa: BLE001 — best-effort: collect every per-stage validation error for a single operator report instead of stopping at the first.
                errors.append(f"Stage {stage.name!r}: merge failed: {exc}")
                continue

            # Collision check (Phase 14 review F-G-1): every stage must
            # write its checkpoints + per-stage manifest into a distinct
            # directory.  Comparing absolute paths so ``./out`` and
            # ``out/`` collide as one.
            abs_out = os.path.abspath(merged.training.output_dir)
            if abs_out in seen_dirs:
                errors.append(
                    f"Stage {stage.name!r}: training.output_dir collides with stage "
                    f"{seen_dirs[abs_out]!r} (both resolve to {abs_out!r}); per-stage "
                    "checkpoints and manifests would overwrite each other.  Set a "
                    "unique 'training.output_dir' in each stage."
                )
            else:
                seen_dirs[abs_out] = stage.name

            # Project the output path the next stage would auto-chain to.
            prev_output = os.path.join(merged.training.output_dir, "final_model")

        if errors:
            logger.error("Pipeline dry-run found %d stage error(s):", len(errors))
            for e in errors:
                logger.error("  %s", e)
            return EXIT_CONFIG_ERROR

        logger.info(
            "Pipeline dry-run OK: %d stage(s) validated; no GPU was allocated.",
            len(self.pipeline.stages),
        )
        return EXIT_SUCCESS

    # -- internals ------------------------------------------------------------

    def _init_state(self) -> PipelineState:
        """Build a fresh :class:`PipelineState` from the parsed pipeline."""
        from .._version import __version__

        stages = [
            PipelineStageState(
                name=s.name,
                index=i,
                trainer_type=(s.training.trainer_type if s.training else self.root_cfg.training.trainer_type),
            )
            for i, s in enumerate(self.pipeline.stages)
        ]
        return PipelineState(
            pipeline_run_id=_generate_run_id(),
            pipeline_config_hash=self.config_hash,
            forgelm_version=__version__,
            started_at=_utc_iso_now(),
            stages=stages,
        )

    def _validate_resume_state(self, existing: PipelineState, *, force: bool) -> Optional[str]:
        """Stale-state guard for ``--resume-from``.

        If the on-disk state file's ``pipeline_config_hash`` differs from
        the current parse of the pipeline YAML, the operator edited the
        file between the failed run and the resume call — resuming
        against a different config silently produces a chain whose
        history doesn't match its current shape.  We refuse unless
        ``--force-resume`` is set (logged at WARNING **and** emitted as a
        ``pipeline.force_resume`` audit event so reviewers see why the
        divergence was accepted).

        Topology vs value-level edits (Phase 14 review final-round
        clarification):

        - **Topology change** (adding, deleting, or renaming a stage —
          the ordered ``[s.name for s in stages]`` list changes) is
          refused **even under** ``--force-resume`` because
          ``state.stages[i]`` is positionally addressed and a topology
          shift would silently overwrite a different stage's history.
        - **Value-level edits** (different ``trainer_type``,
          ``training.output_dir``, hyperparameters under unchanged
          stage names) trip the hash check but are *accepted* under
          ``--force-resume``.  This is the documented escape hatch:
          the operator is signalling "I know the on-disk state and the
          current config diverge; proceed and record the
          ``pipeline.force_resume`` audit breadcrumb."  Reviewers can
          reconstruct the divergence from the event payload's
          ``old_config_hash`` / ``new_config_hash`` fields.

        Return semantics:

        - ``None`` — the resume is OK to proceed (no hash divergence, or
          divergence accepted via ``--force-resume``).
        - error-string — the resume must abort; the caller maps this to
          ``EXIT_CONFIG_ERROR``.  Using a return value (rather than
          ``sys.exit`` mid-method) keeps the orchestrator's exit policy
          consistent with the rest of ``run()`` — every refusal flows
          back through the same ``return`` branch, the same audit-log
          finalisation runs, and the test surface uses a uniform
          ``assert code == EXIT_CONFIG_ERROR`` shape.
        """
        # Topology guard runs UNCONDITIONALLY — even on hash match —
        # because the YAML's bytes hash does not constrain the
        # on-disk state file's contents.  A direct edit / corruption /
        # truncation of ``pipeline_state.json`` could leave the same
        # ``pipeline_config_hash`` recorded inside while the
        # ``stages[]`` array drifts (missing entries, wrong order,
        # tampered ``output_model`` paths).  Without this check, a
        # ``state.stages[i]`` whose name doesn't match
        # ``self.pipeline.stages[i]`` would silently overwrite the
        # wrong stage's history.
        #
        # *Value-level* edits — different ``trainer_type``,
        # ``training.output_dir``, hyperparameters under identical
        # stage names — trip the hash check but are *accepted by
        # design* under ``--force-resume``.  The flag is the operator's
        # explicit signal that they understand the on-disk state and
        # the current config diverge; the guard's job is only to
        # protect the positional-addressing invariant.  See
        # ``docs/guides/pipeline.md`` "Limitations" for the documented
        # contract.
        on_disk_names = [s.name for s in existing.stages]
        current_names = [s.name for s in self.pipeline.stages]
        if on_disk_names != current_names:
            msg = (
                f"Refusing to --resume-from: pipeline stage topology disagrees "
                f"with the on-disk state file (this is refused even with "
                f"--force-resume because state.stages[i] is positionally "
                f"addressed).\n"
                f"  on-disk stages:  {on_disk_names!r}\n"
                f"  current stages:  {current_names!r}\n"
                "Inspect / regenerate the state file before resuming."
            )
            logger.error(msg)
            return msg

        if existing.pipeline_config_hash == self.config_hash:
            return None
        if not force:
            msg = (
                f"Refusing to --resume-from: pipeline_config_hash mismatch.\n"
                f"  on-disk: {existing.pipeline_config_hash}\n"
                f"  current: {self.config_hash}\n"
                "Pass --force-resume to override (logged for audit)."
            )
            logger.error(msg)
            return msg

        old_hash = existing.pipeline_config_hash
        logger.warning(
            "Resuming with --force-resume despite pipeline_config_hash mismatch: %s → %s.",
            old_hash,
            self.config_hash,
        )
        # Append-only audit event so a reviewer reading the JSONL stream
        # later can distinguish an operator-approved stale-config resume
        # from a normal resume.  Emitted BEFORE the stored hash is
        # updated so the event payload still records both sides of the
        # divergence.
        self._audit_event(
            "pipeline.force_resume",
            pipeline_run_id=existing.pipeline_run_id,
            old_config_hash=old_hash,
            new_config_hash=self.config_hash,
        )
        # Update the stored hash to match the now-trusted current config
        # so subsequent transitions don't trip the guard again.
        existing.pipeline_config_hash = self.config_hash
        return None

    def _load_state_file(self) -> Optional[PipelineState]:
        path = self.paths["state_file"]
        if not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Failed to read pipeline_state.json at %s: %s.  Treating as missing.", path, e)
            return None
        # Phase 14 review F-R1-S4: a tampered state file (wrong shape,
        # missing required keys, ``stages`` not a list of dicts, etc.)
        # could crash ``_deserialise_state`` with an opaque
        # ``TypeError`` / ``KeyError`` traceback instead of producing
        # an actionable config-error path.  Catch the common shape
        # failures here and treat the file as missing — the caller's
        # ``--resume-from`` branch then surfaces ``EXIT_CONFIG_ERROR``
        # with a clear message.
        try:
            return _deserialise_state(payload)
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            # AttributeError covers the case where ``stages`` items aren't
            # dicts (e.g. an attacker wrote ``stages: [null, "foo"]``) —
            # ``s.items()`` then raises AttributeError on the str / None.
            logger.warning(
                "pipeline_state.json at %s is structurally invalid (%s); treating as missing.",
                path,
                e,
            )
            return None

    def _save_state(self, state: PipelineState) -> None:
        """Persist state file + refresh pipeline manifest.

        Both writes are atomic.  Manifest export is delegated to
        :mod:`forgelm.compliance` so the schema lives in one place.
        """
        _atomic_write_json(self.paths["state_file"], _serialise_state(state))
        # Lazy import — compliance has its own heavy deps for the
        # Annex-IV verifier path; only needed when we actually export.
        from ..compliance import generate_pipeline_manifest

        manifest = generate_pipeline_manifest(state=state, root_cfg=self.root_cfg)
        _atomic_write_json(self.paths["manifest_file"], manifest)

    def _audit_event(self, event: str, *, pipeline_run_id: str, **fields: Any) -> None:
        """Append-only audit log entry under the **pipeline** root output dir.

        Two separate audit-log streams exist on a multi-stage run:

        - **Pipeline-level** (this method): ``<pipeline.output_dir>/
          audit_log.jsonl`` — carries ``pipeline.*`` lifecycle events
          (started / stage_started / stage_completed / stage_gated /
          stage_reverted / force_resume / completed).
        - **Stage-level** (emitted by each ``ForgeTrainer``):
          ``<stage.training.output_dir>/audit_log.jsonl`` — carries the
          existing ``training.*`` vocabulary unchanged from a single-
          stage v0.6.0 run.

        Both files are append-only JSONL.  Reviewers can correlate by
        joining on ``pipeline_run_id`` (present in every pipeline event)
        with the per-stage ``training_manifest.json`` ``training_run_id``.
        See ``docs/guides/pipeline.md`` "Audit events" for the full
        join key matrix.

        Phase 14 review final-round F-B-1: the ``AuditLogger`` instance
        is cached on the orchestrator and pinned to the pipeline run
        id, so every entry's top-level ``run_id`` field equals the
        pipeline run id (instead of a fresh auto-generated
        ``fg-<random>`` per event, which broke SIEM-style grouping and
        the Article 12 correlation contract).  ``pipeline_run_id`` is
        now a required keyword argument so callers cannot silently
        regress this.
        """
        try:
            from ..compliance import AuditLogger

            if self._audit_logger is None:
                self._audit_logger = AuditLogger(self.paths["root_output_dir"], run_id=pipeline_run_id)
            self._audit_logger.log_event(event, pipeline_run_id=pipeline_run_id, **fields)
        except Exception as exc:  # noqa: BLE001 — audit emission is best-effort telemetry; a write failure must not abort the pipeline.
            logger.warning("Failed to emit audit event %r: %s", event, exc)

    def _notify_pipeline(
        self,
        kind: Literal["started", "completed", "reverted", "stage_failed"],
        state: PipelineState,
        *,
        stage_state: Optional[PipelineStageState] = None,
    ) -> None:
        """Webhook fan-out for pipeline lifecycle transitions.

        Notifier methods are optional (added in Phase 14, defended via
        ``getattr`` so a fork that pinned an older webhook module
        doesn't crash here).
        """
        try:
            from ..webhook import WebhookNotifier

            notifier = WebhookNotifier(self.root_cfg)
            stage_count = len(self.pipeline.stages)
            if kind == "started":
                method = getattr(notifier, "notify_pipeline_started", None)
                if method:
                    method(run_id=state.pipeline_run_id, stage_count=stage_count)
            elif kind == "completed":
                method = getattr(notifier, "notify_pipeline_completed", None)
                if method:
                    method(run_id=state.pipeline_run_id, final_status=state.final_status, stopped_at=state.stopped_at)
            elif kind == "reverted":
                method = getattr(notifier, "notify_pipeline_reverted", None)
                if method and stage_state is not None:
                    method(
                        run_id=state.pipeline_run_id,
                        stage_name=stage_state.name,
                        reason=stage_state.error or "auto_revert",
                    )
            # ``stage_failed`` is currently only an audit event — no
            # dedicated webhook method.  Existing per-stage
            # ``training.failure`` from ForgeTrainer's notifier covers
            # the operator surface; the pipeline-level event would be
            # redundant noise on Slack.
        except Exception as exc:  # noqa: BLE001 — best-effort notification; webhook outage must not derail the pipeline.
            logger.warning("Failed to emit pipeline webhook %r: %s", kind, exc)

    @staticmethod
    def _resolve_input_source(
        *,
        input_model_override: Optional[str],
        stage_model_set: bool,
        prev_output: Optional[str],
    ) -> InputSourceLiteral:
        """Resolve a stage's ``input_source`` from the priority ladder.

        Priority order: CLI override > stage explicit > chain (prev
        output) > root.  See the ``InputSourceLiteral`` docstring for
        what each label means.  Extracted from ``_run_single_stage``
        for Sonar python:S3776 cognitive-complexity hygiene; pure
        function so the caller stays linear.
        """
        if input_model_override is not None:
            return "cli_override"
        if stage_model_set:
            return "stage_explicit"
        if prev_output is not None:
            return "chain"
        return "root"

    def _merge_and_validate_stage(
        self,
        *,
        stage: PipelineStage,
        stage_state: PipelineStageState,
        prev_output: Optional[str],
        input_model_override: Optional[str],
    ) -> Optional[Any]:
        """Merge the per-stage ForgeConfig + run the chain-presence guard.

        Returns the merged ``ForgeConfig`` on success, or ``None`` if a
        config-error path already populated ``stage_state.error`` and
        the caller should return ``EXIT_CONFIG_ERROR``.  Extracted from
        ``_run_single_stage`` for Sonar python:S3776 cognitive-
        complexity hygiene.
        """
        try:
            stage_cfg = merge_pipeline_stage_config(
                self.root_cfg,
                stage,
                prev_output_model=prev_output,
                input_model_override=input_model_override,
            )
        except Exception as exc:  # noqa: BLE001
            stage_state.error = f"Config merge failed: {exc}"
            logger.exception("Stage %r config merge failed.", stage.name)
            return None

        stage_state.input_model = stage_cfg.model.name_or_path

        # Chain-presence guard: when auto-chained, the previous stage's
        # output_dir/final_model leaf must exist.  Phase 14 review F-S-1.
        if stage_state.input_source == "chain" and not os.path.isdir(stage_cfg.model.name_or_path):
            stage_state.error = (
                f"Stage {stage.name!r} requires the previous stage's output at "
                f"{stage_cfg.model.name_or_path}; pass --input-model <path> to "
                f"override or run the full pipeline first."
            )
            logger.error(stage_state.error)
            return None

        return stage_cfg

    def _invoke_trainer(
        self,
        *,
        stage_cfg: Any,
        stage_name: str,
        stage_state: PipelineStageState,
    ) -> Optional[Any]:
        """Run a single stage's :class:`ForgeTrainer` lifecycle.

        Returns the ``TrainResult`` on success, ``None`` on import /
        runtime failure (caller maps to the right exit code via
        ``stage_state.error``).  Extracted from ``_run_single_stage``
        for Sonar python:S3776 cognitive-complexity hygiene.
        """
        # Defer heavy imports so dry-run / --stage on a different stage
        # doesn't load torch.
        try:
            from ..data import prepare_dataset
            from ..model import get_model_and_tokenizer
            from ..trainer import ForgeTrainer
            from ..utils import setup_authentication
        except ImportError as e:
            stage_state.error = f"Missing training dependency: {e}"
            logger.error(stage_state.error)
            return None

        # Top-of-stage catch.  ForgeTrainer.train() crosses every
        # concern (HF load, dataset, TRL, safety, judge, audit,
        # compliance, webhook); any uncaught exception must surface as
        # a per-stage failure rather than a Python traceback that
        # strands the pipeline state.
        try:
            if not stage_cfg.model.offline:
                setup_authentication(stage_cfg.auth.hf_token if stage_cfg.auth else None)
            model, tokenizer = get_model_and_tokenizer(stage_cfg)
            dataset = prepare_dataset(stage_cfg, tokenizer)
            trainer = ForgeTrainer(model=model, tokenizer=tokenizer, config=stage_cfg, dataset=dataset)
            return trainer.train()
        except ConfigError:
            # A config/environment error is exit 1, not the runtime-error class —
            # propagate so the caller routes it to EXIT_CONFIG_ERROR rather than
            # the generic EXIT_TRAINING_ERROR the None-return below maps to.
            raise
        except Exception as exc:  # noqa: BLE001
            stage_state.error = f"Trainer crashed: {exc}"
            logger.exception("Stage %r trainer crashed.", stage_name)
            return None

    def _run_single_stage(
        self,
        *,
        stage: PipelineStage,
        stage_state: PipelineStageState,
        prev_output: Optional[str],
        input_model_override: Optional[str],
        state: PipelineState,
    ) -> int:
        """Materialise the per-stage ForgeConfig, run the trainer, record results.

        Returns the per-stage exit code; the caller decides what to do
        with it (continue, halt, gate).
        """
        stage_state.input_source = self._resolve_input_source(
            input_model_override=input_model_override,
            stage_model_set=stage.model is not None,
            prev_output=prev_output,
        )

        stage_cfg = self._merge_and_validate_stage(
            stage=stage,
            stage_state=stage_state,
            prev_output=prev_output,
            input_model_override=input_model_override,
        )
        if stage_cfg is None:
            return EXIT_CONFIG_ERROR

        self._audit_event(
            "pipeline.stage_started",
            pipeline_run_id=state.pipeline_run_id,
            stage_name=stage.name,
            stage_index=stage_state.index,
            input_model=stage_state.input_model,
            input_source=stage_state.input_source,
        )

        try:
            result = self._invoke_trainer(
                stage_cfg=stage_cfg,
                stage_name=stage.name,
                stage_state=stage_state,
            )
        except ConfigError as exc:
            stage_state.error = str(exc)
            logger.error("Stage %r failed with a configuration error: %s", stage.name, exc)
            return EXIT_CONFIG_ERROR
        if result is None:
            return EXIT_TRAINING_ERROR

        # Capture results onto the stage state.
        stage_state.metrics = {k: float(v) for k, v in (result.metrics or {}).items() if isinstance(v, (int, float))}
        stage_state.auto_revert_triggered = bool(result.reverted)
        stage_state.training_manifest = os.path.join(
            stage_cfg.training.output_dir, "compliance", "training_manifest.json"
        )

        # Auto-revert or gate failure takes precedence: a reverted stage is an
        # eval-failure (exit 3), never "awaiting approval" (exit 4). Revert
        # deletes the staging dir and clears ``staging_path``, but route on
        # ``success`` first so any result that still carries a stale staging_path
        # cannot be misreported as awaiting approval.
        if not result.success:
            stage_state.error = result.error or "Stage gate failed."
            return EXIT_EVAL_FAILURE

        # Human-approval gate: the stage genuinely awaits human sign-off
        # (success=True, model staged). ``awaiting_approval`` is the
        # authoritative discriminator — not ``bool(staging_path)``, which can
        # survive a revert.
        if result.awaiting_approval:
            stage_state.output_model = result.staging_path
            return EXIT_AWAITING_APPROVAL

        # Normal success path.
        stage_state.output_model = result.final_model_path or os.path.join(stage_cfg.training.output_dir, "final_model")
        return EXIT_SUCCESS

    # -- summary --------------------------------------------------------------

    def _emit_summary(self, state: PipelineState) -> None:
        """Operator-facing run summary (text or JSON envelope)."""
        if self.output_format == "json":
            print(json.dumps(_serialise_state(state), indent=2))
            return

        logger.info("Pipeline %s — final_status=%s", state.pipeline_run_id, state.final_status)
        for s in state.stages:
            logger.info(
                "  [%s] %s — trainer=%s, exit=%s, output=%s",
                s.status,
                s.name,
                s.trainer_type,
                s.exit_code if s.exit_code is not None else "—",
                s.output_model or "—",
            )


# ---------------------------------------------------------------------------
# Entry point (CLI dispatcher delegates here)
# ---------------------------------------------------------------------------


def run_pipeline_from_args(
    root_cfg: ForgeConfig,
    pipeline_yaml_bytes: bytes,
    args: Any,
) -> int:
    """Dispatcher entry — orchestrates flag → orchestrator wiring.

    ``args`` is the parsed ``argparse.Namespace`` from the CLI; only the
    pipeline-relevant attributes are read.  Keeping this glue thin makes
    the orchestrator itself trivial to unit-test against a fake args
    namespace.
    """
    stage_filter = getattr(args, "stage", None)
    resume_from = getattr(args, "resume_from", None)
    force_resume = bool(getattr(args, "force_resume", False))
    # Phase 14 review F-F-2: argparse cannot reject an empty string
    # value for ``--input-model "" --stage X`` at parse time, and the
    # downstream merge helper uses ``is not None`` rather than a truthy
    # check, so the empty string would silently overwrite the auto-
    # chained model path.  Normalise to ``None`` here so the rest of the
    # orchestrator sees "no override" rather than "override with nothing".
    raw_input_model = getattr(args, "input_model", None)
    input_model_override = raw_input_model if raw_input_model else None
    output_format = getattr(args, "output_format", "text")
    dry_run = bool(getattr(args, "dry_run", False))

    if stage_filter and resume_from:
        logger.error("`--stage` and `--resume-from` are mutually exclusive.")
        return EXIT_CONFIG_ERROR
    if input_model_override and not stage_filter:
        logger.error("`--input-model` requires `--stage <name>`.")
        return EXIT_CONFIG_ERROR

    # Phase 14 post-release review: reject single-stage no-train flags
    # on a ``pipeline:`` config.  ``_dispatch.py`` routes here BEFORE
    # ``_maybe_run_no_train_mode`` (the F-B-1 fix on PR #53 was
    # explicit: otherwise the legacy no-train modes would ``sys.exit``
    # before the orchestrator's per-stage validation could run), but
    # that means ``--fit-check`` / ``--merge`` / ``--generate-data`` /
    # ``--compliance-export`` / ``--benchmark-only`` would silently
    # trigger a full pipeline training run instead of the single-stage
    # operation the operator asked for.  Reject them explicitly with
    # an actionable error so the operator either drops the flag or
    # switches to a single-stage config.  ``--dry-run`` IS supported
    # on pipelines (collects every per-stage validation error before
    # exiting) and is handled below.
    _UNSUPPORTED_ON_PIPELINE = (
        ("fit_check", "--fit-check"),
        ("merge", "--merge"),
        ("generate_data", "--generate-data"),
        ("compliance_export", "--compliance-export"),
        ("benchmark_only", "--benchmark-only"),
    )
    for attr, flag_name in _UNSUPPORTED_ON_PIPELINE:
        if getattr(args, attr, None):
            logger.error(
                "`%s` is not supported when the config carries a `pipeline:` "
                "block.  These flags operate on a single-stage config; either "
                "drop the `pipeline:` block or remove the flag.  Use `--dry-run` "
                "for multi-stage validation without GPU allocation.",
                flag_name,
            )
            return EXIT_CONFIG_ERROR

    orchestrator = PipelineOrchestrator(
        root_cfg=root_cfg,
        pipeline_yaml_bytes=pipeline_yaml_bytes,
        output_format=output_format,
    )

    if dry_run:
        return orchestrator.dry_run()

    # Top-level exception boundary (F-P2-FAB-13). Single-run training has one in
    # cli/_training.py; the pipeline path was called bare, so a mid-run
    # _save_state OSError (disk full / permission flip) escaped as a raw
    # traceback → interpreter exit 1, indistinguishable from EXIT_CONFIG_ERROR
    # and breaking JSON-mode stdout parsers. Map config-class failures to exit 1
    # and everything else to the runtime-error class (exit 2), always emitting
    # the 2-key JSON error envelope in JSON mode.
    try:
        return orchestrator.run(
            stage_filter=stage_filter,
            resume_from=resume_from,
            force_resume=force_resume,
            input_model_override=input_model_override,
        )
    except (ConfigError, ValidationError) as e:
        logger.error("Pipeline configuration error: %s", e)
        if output_format == "json":
            print(json.dumps({"success": False, "error": str(e)}))
        return EXIT_CONFIG_ERROR
    except Exception as e:  # noqa: BLE001 — best-effort: top-of-CLI pipeline catch mirrors cli/_training.py. The orchestrator crosses state-file I/O, manifest generation, per-stage merge/validation, and trainer invocation; any leak must surface as a structured CLI failure (traceback in the log) and the 2-key JSON envelope on stdout rather than a raw traceback that breaks JSON parsers.  # NOSONAR
        logger.exception("Pipeline run failed.")
        if output_format == "json":
            print(json.dumps({"success": False, "error": str(e)}))
        return EXIT_TRAINING_ERROR
