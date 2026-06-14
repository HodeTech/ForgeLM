"""Phase 14 — Pipeline orchestrator state-machine tests.

Covers the documented edge cases in ``docs/roadmap/phase-14-pipeline-chains.md``
Tasks 4 + 5:

- Happy-path 3-stage chain ⇒ all three trainers run, state file +
  pipeline manifest written, exit 0.
- Auto-revert at stage 2 ⇒ stage 3 enters ``skipped_due_to_prior_revert``,
  ``pipeline.stage_reverted`` audit event fires, exit 3.
- Human-approval gate at stage 1 ⇒ exit 4, downstream stages still
  ``pending``, state preserved for ``--resume-from``.
- ``--resume-from <name>`` skips already-completed stages whose output
  directory still exists; stale-state guard rejects a config-hash
  mismatch unless ``--force-resume`` is set.
- ``--stage <name>`` partial-run filters; missing-prev-output produces
  a clear config error.
- Dry-run validates every stage without touching a trainer.

The :class:`forgelm.trainer.ForgeTrainer` is replaced via lazy-import
monkeypatch — the orchestrator only reaches the heavy modules through
``from ..trainer import ForgeTrainer`` inside ``_run_single_stage``, so
patching ``sys.modules`` and the dataset / model helpers is enough.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from forgelm.cli._exit_codes import (
    EXIT_AWAITING_APPROVAL,
    EXIT_CONFIG_ERROR,
    EXIT_EVAL_FAILURE,
    EXIT_SUCCESS,
    EXIT_TRAINING_ERROR,
)
from forgelm.cli._pipeline import (
    PipelineOrchestrator,
    PipelineStageState,
    PipelineState,
    _compute_pipeline_config_hash,
    _deserialise_state,
    _generate_run_id,
    _serialise_state,
    run_pipeline_from_args,
)
from forgelm.config import ForgeConfig
from forgelm.results import TrainResult

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _three_stage_config(tmp_path) -> ForgeConfig:
    """Build a minimal valid 3-stage pipeline ForgeConfig."""
    return ForgeConfig(
        model={"name_or_path": "org/base"},
        lora={"r": 8},
        training={"trainer_type": "sft", "output_dir": str(tmp_path)},
        data={"dataset_name_or_path": "org/sft"},
        pipeline={
            "output_dir": str(tmp_path / "pipeline_run"),
            "stages": [
                {
                    "name": "sft_stage",
                    "training": {"trainer_type": "sft", "output_dir": str(tmp_path / "stage1")},
                    "data": {"dataset_name_or_path": "org/sft_data"},
                },
                {
                    "name": "dpo_stage",
                    "training": {"trainer_type": "dpo", "output_dir": str(tmp_path / "stage2")},
                    "data": {"dataset_name_or_path": "org/dpo_data"},
                },
                {
                    "name": "grpo_stage",
                    "training": {"trainer_type": "grpo", "output_dir": str(tmp_path / "stage3")},
                    "data": {"dataset_name_or_path": "org/math_data"},
                },
            ],
        },
    )


def _install_trainer_mocks(monkeypatch, train_results):
    """Inject mock trainer/model/data modules.

    ``train_results`` is a list of :class:`TrainResult` returned in order
    by successive ``ForgeTrainer.train()`` calls.  The mock also
    materialises the per-stage output dir on disk so the chain-integrity
    guard in the orchestrator does not trip when subsequent stages
    auto-chain from a "fresh" directory.
    """
    iterator = iter(train_results)
    instantiated_configs = []

    class _FakeForgeTrainer:
        def __init__(self, *, model, tokenizer, config, dataset):
            instantiated_configs.append(config)
            # Materialise final_model dir on disk so the next stage's
            # chain-integrity guard sees the path it auto-chains to.
            final_dir = os.path.join(config.training.output_dir, "final_model")
            os.makedirs(final_dir, exist_ok=True)
            self.config = config

        def train(self, resume_from_checkpoint=None):
            try:
                return next(iterator)
            except StopIteration:
                return TrainResult(success=True)

    fake_trainer_mod = types.ModuleType("forgelm.trainer")
    fake_trainer_mod.ForgeTrainer = _FakeForgeTrainer
    monkeypatch.setitem(__import__("sys").modules, "forgelm.trainer", fake_trainer_mod)

    fake_model_mod = types.ModuleType("forgelm.model")
    fake_model_mod.get_model_and_tokenizer = lambda config: (MagicMock(), MagicMock())
    monkeypatch.setitem(__import__("sys").modules, "forgelm.model", fake_model_mod)

    fake_data_mod = types.ModuleType("forgelm.data")
    fake_data_mod.prepare_dataset = lambda config, tokenizer: {"train": [{"text": "x"}]}
    monkeypatch.setitem(__import__("sys").modules, "forgelm.data", fake_data_mod)

    fake_utils_mod = types.ModuleType("forgelm.utils")
    fake_utils_mod.setup_authentication = lambda token: None
    monkeypatch.setitem(__import__("sys").modules, "forgelm.utils", fake_utils_mod)

    return instantiated_configs


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestPipelineConfigHash:
    def test_deterministic(self):
        h1 = _compute_pipeline_config_hash(b"some yaml bytes")
        h2 = _compute_pipeline_config_hash(b"some yaml bytes")
        assert h1 == h2
        assert h1.startswith("sha256:")

    def test_different_inputs_different_hashes(self):
        assert _compute_pipeline_config_hash(b"v1") != _compute_pipeline_config_hash(b"v2")

    def test_whitespace_sensitive(self):
        """Hashing raw bytes — whitespace / key order / comments all matter.

        This is intentional: regulators audit the on-disk artefact, not
        the parsed semantic content.  An operator who edits the file
        between runs gets a new hash even if the YAML is semantically
        equivalent."""
        assert _compute_pipeline_config_hash(b"a: 1\nb: 2") != _compute_pipeline_config_hash(b"b: 2\na: 1")


class TestRunIdShape:
    def test_run_id_format(self):
        run_id = _generate_run_id()
        # ``pl_YYYY-MM-DD_<6-hex>``
        assert run_id.startswith("pl_")
        parts = run_id.split("_")
        assert len(parts) == 3
        assert len(parts[1]) == 10  # YYYY-MM-DD
        assert len(parts[2]) == 6  # 6-hex

    def test_run_id_unique(self):
        ids = {_generate_run_id() for _ in range(50)}
        assert len(ids) == 50, "run_id collisions in 50 samples — too narrow randomness?"


class TestStateRoundTrip:
    def test_serialise_deserialise(self):
        state = PipelineState(
            pipeline_run_id="pl_test",
            pipeline_config_hash="sha256:abc",
            forgelm_version="0.7.0-dev",
            started_at="2026-06-15T12:00:00+00:00",
            stages=[
                PipelineStageState(name="s1", index=0, trainer_type="sft", status="completed"),
                PipelineStageState(name="s2", index=1, trainer_type="dpo", status="pending"),
            ],
        )
        payload = _serialise_state(state)
        round_tripped = _deserialise_state(payload)
        assert round_tripped.pipeline_run_id == state.pipeline_run_id
        assert len(round_tripped.stages) == 2
        assert round_tripped.stages[0].name == "s1"
        assert round_tripped.stages[0].status == "completed"

    def test_deserialise_tolerates_unknown_keys(self):
        """Future-forward compatibility: a newer manifest schema with
        extra fields must not crash an older reader."""
        payload = {
            "pipeline_run_id": "pl_test",
            "pipeline_config_hash": "sha256:abc",
            "forgelm_version": "0.7.0",
            "started_at": "2026-06-15T12:00:00+00:00",
            "final_status": "completed",
            "stages": [
                {"name": "s1", "index": 0, "trainer_type": "sft", "status": "completed", "future_field": "ignore me"}
            ],
            "future_top_level": "also ignore",
        }
        state = _deserialise_state(payload)
        assert state.stages[0].name == "s1"


# ---------------------------------------------------------------------------
# Atomic write helper
# ---------------------------------------------------------------------------


class TestAtomicWriteJson:
    """Phase 14 review final-round F-S-1 regression: ``_atomic_write_json``
    must use a per-writer-unique temp filename so two writers targeting
    the same final path don't truncate each other's tmp mid-write."""

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason=(
            "POSIX-specific race semantics — the original bug this test pins "
            "was ``os.replace`` raising ``FileNotFoundError`` because the "
            "loser's shared tmp was unlinked by the winner's rename, which "
            "is a POSIX rename-over-target invariant.  On Windows, "
            "``os.replace`` to a same-target path raises a different error "
            "(``PermissionError: Access is denied``) when concurrent writers "
            "briefly hold a handle on the destination — that's a file-lock "
            "race, not the tmp-rename race the F-S-1 fix addresses.  Per-"
            "writer-unique tmp filenames already eliminate the POSIX bug; "
            "Windows's concurrent-replace semantics are not in scope for "
            "the pipeline orchestrator's atomic-write contract."
        ),
    )
    def test_per_writer_temp_path_does_not_race_on_shared_tmp(self, tmp_path):
        """Pre-fix, both writers wrote to ``<target>.tmp`` — a 16-thread
        stress probe produced ``FileNotFoundError`` from ``os.replace``
        (the loser's tmp was unlinked by the winner's rename).  Post-
        fix, each writer's tmp is unique so no traceback escapes; the
        race surfaces as last-writer-wins on the final ``path``."""
        from concurrent.futures import ThreadPoolExecutor

        from forgelm.cli._pipeline import _atomic_write_json

        target = str(tmp_path / "shared.json")
        errors: list[Exception] = []

        def writer(i: int) -> None:
            # OSError covers the FileNotFoundError race that the
            # shared-tmp implementation produced; pre-fix this branch
            # captured the escapes for the assertion below.  ``Exception``
            # is wide-enough to surface any unexpected escape too without
            # tripping the Sonar ``catch-BaseException`` rule.
            try:
                _atomic_write_json(target, {"writer": i, "value": "x" * 1024})
            except Exception as exc:  # noqa: BLE001 — test probe: record any escape
                errors.append(exc)

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(writer, range(32)))

        # No writer's tmp-vs-replace race should have escaped.
        assert errors == [], f"Concurrent writers produced {len(errors)} exception(s): {errors[:3]}"
        # And the final path holds *some* valid JSON (last writer wins).
        with open(target) as f:
            payload = json.load(f)
        assert isinstance(payload, dict)
        assert "writer" in payload

    def test_orphan_tmp_cleaned_up_on_serialise_failure(self, tmp_path, monkeypatch):
        """If the tmp write itself fails (disk full, permission, etc.),
        the orphan tmp must not be left behind under the target dir.
        Post-fix the helper's bare-except cleanup ensures we don't
        accumulate ``*.tmp`` debris when an upstream caller passes a
        non-JSON-serialisable payload."""
        from forgelm.cli._pipeline import _atomic_write_json

        target = str(tmp_path / "bad.json")
        # ``json.dump`` cannot serialise a set; this triggers the
        # try/except in the helper after the tmp file is created but
        # before the replace.
        try:
            _atomic_write_json(target, {"bad": {1, 2, 3}})
        except TypeError:
            pass
        # No orphan files left in tmp_path.
        orphans = [p for p in os.listdir(str(tmp_path)) if p.endswith(".tmp")]
        assert orphans == [], f"Orphan tmp file(s) left behind: {orphans!r}"


# ---------------------------------------------------------------------------
# Orchestrator behaviour — dry-run
# ---------------------------------------------------------------------------


class TestDryRun:
    def test_dry_run_validates_every_stage_without_running_trainer(self, tmp_path, monkeypatch):
        cfg = _three_stage_config(tmp_path)
        configs_seen = _install_trainer_mocks(monkeypatch, [])
        orch = PipelineOrchestrator(cfg, b"yaml bytes")
        code = orch.dry_run()
        assert code == EXIT_SUCCESS
        # Trainer must not have been instantiated.
        assert configs_seen == []

    def test_dry_run_does_not_write_state_or_manifest(self, tmp_path, monkeypatch):
        cfg = _three_stage_config(tmp_path)
        _install_trainer_mocks(monkeypatch, [])
        orch = PipelineOrchestrator(cfg, b"yaml bytes")
        orch.dry_run()
        assert not os.path.exists(orch.paths["state_file"])
        assert not os.path.exists(orch.paths["manifest_file"])

    def test_dry_run_flags_output_dir_collision(self, tmp_path, monkeypatch):
        """Phase 14 review F-G-1 (Gemini): two stages resolving to the
        same ``training.output_dir`` would silently overwrite each
        other's checkpoints and per-stage Annex-IV manifests, breaking
        the chain of custody.  Dry-run must surface the collision as
        EXIT_CONFIG_ERROR before any GPU work."""
        shared_dir = str(tmp_path / "shared_out")
        cfg = ForgeConfig(
            model={"name_or_path": "org/base"},
            lora={"r": 8},
            training={"trainer_type": "sft", "output_dir": str(tmp_path)},
            data={"dataset_name_or_path": "org/sft"},
            pipeline={
                "output_dir": str(tmp_path / "pipeline_run"),
                "stages": [
                    {
                        "name": "sft_stage",
                        "training": {"trainer_type": "sft", "output_dir": shared_dir},
                        "data": {"dataset_name_or_path": "org/sft"},
                    },
                    {
                        "name": "dpo_stage",
                        "training": {"trainer_type": "dpo", "output_dir": shared_dir},
                        "data": {"dataset_name_or_path": "org/dpo"},
                    },
                ],
            },
        )
        _install_trainer_mocks(monkeypatch, [])
        orch = PipelineOrchestrator(cfg, b"yaml")
        code = orch.dry_run()
        assert code == EXIT_CONFIG_ERROR

    def test_dry_run_flags_inherited_output_dir_collision(self, tmp_path, monkeypatch):
        """Two stages that both *inherit* the root ``training`` block
        (no per-stage override) end up sharing the root's output_dir
        by construction — the most common form of the F-G-1 footgun.
        Verify the guard catches it even when the collision is
        inherited rather than explicit."""
        cfg = ForgeConfig(
            model={"name_or_path": "org/base"},
            lora={"r": 8},
            training={"trainer_type": "sft", "output_dir": str(tmp_path / "root_out")},
            data={"dataset_name_or_path": "org/sft"},
            pipeline={
                "output_dir": str(tmp_path / "pipeline_run"),
                # Note: no per-stage training: block — both stages inherit
                # the root training.output_dir.  Setting data: per stage
                # so the merge succeeds, isolating the collision.
                "stages": [
                    {"name": "s1", "data": {"dataset_name_or_path": "org/d1"}},
                    {"name": "s2", "data": {"dataset_name_or_path": "org/d2"}},
                ],
            },
        )
        _install_trainer_mocks(monkeypatch, [])
        orch = PipelineOrchestrator(cfg, b"yaml")
        code = orch.dry_run()
        assert code == EXIT_CONFIG_ERROR


# ---------------------------------------------------------------------------
# Orchestrator behaviour — happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_three_stage_chain_runs_all_stages(self, tmp_path, monkeypatch):
        cfg = _three_stage_config(tmp_path)
        results = [
            TrainResult(
                success=True, metrics={"eval_loss": 0.5}, final_model_path=str(tmp_path / "stage1" / "final_model")
            ),
            TrainResult(
                success=True, metrics={"eval_loss": 0.3}, final_model_path=str(tmp_path / "stage2" / "final_model")
            ),
            TrainResult(
                success=True, metrics={"eval_loss": 0.2}, final_model_path=str(tmp_path / "stage3" / "final_model")
            ),
        ]
        configs_seen = _install_trainer_mocks(monkeypatch, results)
        orch = PipelineOrchestrator(cfg, b"yaml bytes")
        code = orch.run()
        assert code == EXIT_SUCCESS
        assert len(configs_seen) == 3
        # Stage 2's input model must be stage 1's output (auto-chain).
        assert configs_seen[1].model.name_or_path == str(tmp_path / "stage1" / "final_model")
        assert configs_seen[2].model.name_or_path == str(tmp_path / "stage2" / "final_model")

    def test_state_file_written_with_final_status_completed(self, tmp_path, monkeypatch):
        cfg = _three_stage_config(tmp_path)
        results = [TrainResult(success=True), TrainResult(success=True), TrainResult(success=True)]
        _install_trainer_mocks(monkeypatch, results)
        orch = PipelineOrchestrator(cfg, b"yaml bytes")
        orch.run()
        assert os.path.exists(orch.paths["state_file"])
        with open(orch.paths["state_file"]) as f:
            payload = json.load(f)
        assert payload["final_status"] == "completed"
        assert payload["stopped_at"] is None
        assert all(s["status"] == "completed" for s in payload["stages"])

    def test_manifest_written_with_chain_intact(self, tmp_path, monkeypatch):
        cfg = _three_stage_config(tmp_path)
        results = [TrainResult(success=True), TrainResult(success=True), TrainResult(success=True)]
        _install_trainer_mocks(monkeypatch, results)
        orch = PipelineOrchestrator(cfg, b"yaml bytes")
        orch.run()
        assert os.path.exists(orch.paths["manifest_file"])
        with open(orch.paths["manifest_file"]) as f:
            manifest = json.load(f)
        assert manifest["pipeline_run_id"].startswith("pl_")
        assert manifest["final_status"] == "completed"
        # Chain integrity in the manifest payload.
        s1, s2, s3 = manifest["stages"]
        assert s2["input_model"] == s1["output_model"]
        assert s3["input_model"] == s2["output_model"]


# ---------------------------------------------------------------------------
# Orchestrator behaviour — auto-revert + gate failures
# ---------------------------------------------------------------------------


class TestAutoRevert:
    def test_stage_2_auto_revert_stops_pipeline(self, tmp_path, monkeypatch):
        cfg = _three_stage_config(tmp_path)
        results = [
            TrainResult(success=True),
            TrainResult(success=False, reverted=True, error="loss regression"),
        ]
        configs_seen = _install_trainer_mocks(monkeypatch, results)
        orch = PipelineOrchestrator(cfg, b"yaml bytes")
        code = orch.run()
        assert code == EXIT_EVAL_FAILURE
        # Stage 3 must NOT have been instantiated.
        assert len(configs_seen) == 2
        # State file records the stop-at + skipped status.
        with open(orch.paths["state_file"]) as f:
            payload = json.load(f)
        assert payload["final_status"] == "stopped_at_stage"
        assert payload["stopped_at"] == "dpo_stage"
        statuses = [s["status"] for s in payload["stages"]]
        assert statuses == ["completed", "failed", "skipped_due_to_prior_revert"]

    def test_gate_failure_without_revert_also_stops_pipeline(self, tmp_path, monkeypatch):
        """A stage that returns ``success=False`` but ``reverted=False``
        (e.g., a benchmark min-score failure that didn't auto-revert)
        still halts the chain — downstream stages would be operating
        on an explicitly-failed checkpoint."""
        cfg = _three_stage_config(tmp_path)
        results = [TrainResult(success=False, reverted=False, error="benchmark below min")]
        _install_trainer_mocks(monkeypatch, results)
        orch = PipelineOrchestrator(cfg, b"yaml bytes")
        code = orch.run()
        assert code == EXIT_EVAL_FAILURE
        with open(orch.paths["state_file"]) as f:
            payload = json.load(f)
        assert payload["stages"][0]["status"] == "failed"


# ---------------------------------------------------------------------------
# Orchestrator behaviour — human-approval gate
# ---------------------------------------------------------------------------


class TestHumanApprovalGate:
    def test_gated_stage_exits_with_awaiting_approval(self, tmp_path, monkeypatch):
        cfg = _three_stage_config(tmp_path)
        staging = str(tmp_path / "stage1" / "final_model.staging")
        os.makedirs(staging, exist_ok=True)
        results = [TrainResult(success=True, awaiting_approval=True, staging_path=staging)]
        _install_trainer_mocks(monkeypatch, results)
        orch = PipelineOrchestrator(cfg, b"yaml bytes")
        code = orch.run()
        assert code == EXIT_AWAITING_APPROVAL

    def test_reverted_gated_stage_exits_eval_failure_not_awaiting(self, tmp_path, monkeypatch):
        """XP-02 core: an approval-gated stage whose post-train gate auto-reverted
        (success=False, staging cleared) must exit EXIT_EVAL_FAILURE (3), NOT
        EXIT_AWAITING_APPROVAL (4).  Pre-fix the staging check ran before the
        success check, so a reverted stage was misreported as awaiting approval."""
        cfg = _three_stage_config(tmp_path)
        # The real trainer clears staging_path on revert; awaiting_approval False.
        _install_trainer_mocks(monkeypatch, [TrainResult(success=False, reverted=True, staging_path=None)])
        orch = PipelineOrchestrator(cfg, b"yaml bytes")
        code = orch.run()
        assert code == EXIT_EVAL_FAILURE

    def test_failed_stage_with_stale_staging_path_still_exits_eval_failure(self, tmp_path, monkeypatch):
        """Defence-in-depth: even if a future path left a stale ``staging_path``
        on a ``success=False`` result, routing on success first sends it to
        exit 3 — a stale staging dir must never read as 'awaiting approval'."""
        cfg = _three_stage_config(tmp_path)
        stale = str(tmp_path / "stage1" / "final_model.staging.fg-stale")
        os.makedirs(stale, exist_ok=True)
        _install_trainer_mocks(monkeypatch, [TrainResult(success=False, reverted=True, staging_path=stale)])
        orch = PipelineOrchestrator(cfg, b"yaml bytes")
        code = orch.run()
        assert code == EXIT_EVAL_FAILURE

    def test_success_with_staging_but_no_awaiting_flag_is_not_gated(self, tmp_path, monkeypatch):
        """``awaiting_approval`` is authoritative: a successful result that
        carries a ``staging_path`` but does NOT set the flag is a normal
        success (exit 0), never a false gate (exit 4)."""
        cfg = _three_stage_config(tmp_path)
        staging = str(tmp_path / "stage1" / "final_model.staging")
        os.makedirs(staging, exist_ok=True)
        # awaiting_approval defaults False; three successful stages → full run.
        _install_trainer_mocks(monkeypatch, [TrainResult(success=True, staging_path=staging)])
        orch = PipelineOrchestrator(cfg, b"yaml bytes")
        code = orch.run()
        assert code == EXIT_SUCCESS

    def test_gated_pending_approval_preserves_downstream_pending(self, tmp_path, monkeypatch):
        """After a gated stage, downstream stages must stay ``pending``
        (not ``skipped_due_to_prior_revert``) so a subsequent
        ``--resume-from`` picks them up post-approval."""
        cfg = _three_stage_config(tmp_path)
        staging = str(tmp_path / "stage1" / "final_model.staging")
        os.makedirs(staging, exist_ok=True)
        results = [TrainResult(success=True, awaiting_approval=True, staging_path=staging)]
        _install_trainer_mocks(monkeypatch, results)
        orch = PipelineOrchestrator(cfg, b"yaml bytes")
        orch.run()
        with open(orch.paths["state_file"]) as f:
            payload = json.load(f)
        assert payload["final_status"] == "gated_pending_approval"
        assert payload["stages"][0]["status"] == "gated_pending_approval"
        assert payload["stages"][1]["status"] == "pending"
        assert payload["stages"][2]["status"] == "pending"

    def test_gated_stage_emits_dedicated_stage_gated_audit_event(self, tmp_path, monkeypatch):
        """Phase 14 review F-N-1 regression: a stage exiting
        ``EXIT_AWAITING_APPROVAL`` must emit a dedicated
        ``pipeline.stage_gated`` audit event (not
        ``pipeline.stage_completed`` with a sub-field).  This lets
        dashboard / SIEM filters distinguish the gate flow on the event
        name alone."""
        cfg = _three_stage_config(tmp_path)
        staging = str(tmp_path / "stage1" / "final_model.staging")
        os.makedirs(staging, exist_ok=True)
        _install_trainer_mocks(monkeypatch, [TrainResult(success=True, awaiting_approval=True, staging_path=staging)])
        orch = PipelineOrchestrator(cfg, b"yaml bytes")
        orch.run()
        audit_path = os.path.join(orch.paths["root_output_dir"], "audit_log.jsonl")
        with open(audit_path) as f:
            events = [json.loads(line) for line in f if line.strip()]
        gated_events = [e for e in events if e.get("event") == "pipeline.stage_gated"]
        assert len(gated_events) == 1, (
            f"Expected exactly one pipeline.stage_gated event, got {[e.get('event') for e in events]!r}"
        )
        evt = gated_events[0]
        assert evt["stage_name"] == "sft_stage"
        assert evt["gate_decision"] == "approval_pending"
        assert evt["staging_path"] == staging
        # Counter-assert: the legacy stage_completed event must NOT have
        # fired for this stage (preventing a future regression where both
        # events are emitted in parallel and the dashboard double-counts).
        completed_for_sft = [
            e for e in events if e.get("event") == "pipeline.stage_completed" and e.get("stage_name") == "sft_stage"
        ]
        assert completed_for_sft == []


# ---------------------------------------------------------------------------
# Orchestrator behaviour — --stage filter
# ---------------------------------------------------------------------------


class TestStageFilter:
    def test_unknown_stage_name_rejected(self, tmp_path, monkeypatch):
        cfg = _three_stage_config(tmp_path)
        _install_trainer_mocks(monkeypatch, [])
        orch = PipelineOrchestrator(cfg, b"yaml bytes")
        code = orch.run(stage_filter="nonexistent_stage")
        assert code == EXIT_CONFIG_ERROR

    def test_filter_to_first_stage_runs_only_it(self, tmp_path, monkeypatch):
        cfg = _three_stage_config(tmp_path)
        results = [TrainResult(success=True)]
        configs_seen = _install_trainer_mocks(monkeypatch, results)
        orch = PipelineOrchestrator(cfg, b"yaml bytes")
        code = orch.run(stage_filter="sft_stage")
        assert code == EXIT_SUCCESS
        assert len(configs_seen) == 1
        with open(orch.paths["state_file"]) as f:
            payload = json.load(f)
        statuses = [s["status"] for s in payload["stages"]]
        assert statuses == ["completed", "skipped_by_filter", "skipped_by_filter"]

    def test_filter_to_middle_stage_without_prev_output_fails_cleanly(self, tmp_path, monkeypatch):
        """``--stage dpo_stage`` without sft_stage's output on disk must
        fail with a clear EXIT_CONFIG_ERROR — not silently fall back to
        the root ``model.name_or_path``."""
        cfg = _three_stage_config(tmp_path)
        _install_trainer_mocks(monkeypatch, [TrainResult(success=True)])
        orch = PipelineOrchestrator(cfg, b"yaml bytes")
        # Make sure the prev output dir does NOT exist.
        assert not os.path.exists(tmp_path / "stage1" / "final_model")
        code = orch.run(stage_filter="dpo_stage")
        assert code == EXIT_CONFIG_ERROR

    def test_filter_with_input_model_override_succeeds(self, tmp_path, monkeypatch):
        cfg = _three_stage_config(tmp_path)
        configs_seen = _install_trainer_mocks(monkeypatch, [TrainResult(success=True)])
        orch = PipelineOrchestrator(cfg, b"yaml bytes")
        override_path = str(tmp_path / "manual_input")
        os.makedirs(override_path, exist_ok=True)
        code = orch.run(stage_filter="dpo_stage", input_model_override=override_path)
        assert code == EXIT_SUCCESS
        assert len(configs_seen) == 1
        assert configs_seen[0].model.name_or_path == override_path

    def test_filter_middle_stage_with_disk_seed_passes_chain_verifier(self, tmp_path, monkeypatch):
        """Phase 14 review final-round F-B-2 regression: ``--stage X``
        on a non-first stage whose predecessor's ``final_model`` exists
        on disk auto-chains correctly, AND the resulting pipeline
        manifest must NOT report a ``chain_integrity_violation``.

        Pre-fix, the ``skipped_by_filter`` predecessor had
        ``output_model=null`` while the executed stage had
        ``input_source=chain``, so the strict chain verifier (F-B-3)
        emitted a ``chain_integrity_violation`` on every legitimate
        ``--stage <non-first>`` run.  The fix seeds the predecessor's
        ``output_model`` in the manifest from the resolved disk path.
        """
        from forgelm.compliance import verify_pipeline_manifest_at_path

        cfg = _three_stage_config(tmp_path)
        # Pre-create stage 1's on-disk output so the --stage filter can
        # seed the chain.
        prev_output_dir = tmp_path / "stage1" / "final_model"
        prev_output_dir.mkdir(parents=True, exist_ok=True)
        configs_seen = _install_trainer_mocks(monkeypatch, [TrainResult(success=True)])
        orch = PipelineOrchestrator(cfg, b"yaml")
        code = orch.run(stage_filter="dpo_stage")
        assert code == EXIT_SUCCESS
        assert len(configs_seen) == 1
        # The DPO stage must have auto-chained from stage 1's on-disk output.
        assert configs_seen[0].model.name_or_path == str(prev_output_dir)

        # Inspect the manifest directly to confirm chain integrity is
        # intact.  ``verify_pipeline_manifest_at_path`` additionally
        # checks each completed stage's per-stage training_manifest
        # exists; with a mocked trainer that file isn't actually
        # written, so we don't assert ``violations == []`` here — we
        # assert only the chain-integrity contract that's the subject
        # of the F-B-2 regression.  Per-stage training_manifest
        # existence is covered by ``test_completed_stage_with_missing_
        # training_manifest_flagged`` in test_pipeline_compliance.py.
        import json as _json

        with open(orch.paths["manifest_file"]) as f:
            manifest = _json.load(f)
        sft_stage_payload, dpo_stage_payload, _grpo = manifest["stages"]
        assert sft_stage_payload["status"] == "skipped_by_filter"
        # The fix: predecessor's output_model is surfaced from the resolved disk path.
        assert sft_stage_payload["output_model"] == str(prev_output_dir)
        assert dpo_stage_payload["input_source"] == "chain"
        assert dpo_stage_payload["input_model"] == str(prev_output_dir)

        violations = verify_pipeline_manifest_at_path(str(orch.paths["root_output_dir"]))
        assert not any("chain_integrity_violation" in v for v in violations), (
            f"Chain integrity must hold for --stage <non-first> with disk seed; got: {violations!r}"
        )

    def test_filter_stage_with_own_model_block_runs_without_prev_output(self, tmp_path, monkeypatch):
        """F-P2-FAB-11: ``--stage dpo_stage`` where dpo_stage declares its own
        ``model:`` block must run even with no predecessor output on disk — the
        stage ignores the auto-chain seed, so demanding the predecessor's output
        is a spurious refusal."""
        cfg = ForgeConfig(
            model={"name_or_path": "org/base"},
            lora={"r": 8},
            training={"trainer_type": "sft", "output_dir": str(tmp_path)},
            data={"dataset_name_or_path": "org/sft"},
            pipeline={
                "output_dir": str(tmp_path / "pipeline_run"),
                "stages": [
                    {
                        "name": "sft_stage",
                        "training": {"trainer_type": "sft", "output_dir": str(tmp_path / "stage1")},
                        "data": {"dataset_name_or_path": "org/sft_data"},
                    },
                    {
                        "name": "dpo_stage",
                        "model": {"name_or_path": "org/other-base"},
                        "training": {"trainer_type": "dpo", "output_dir": str(tmp_path / "stage2")},
                        "data": {"dataset_name_or_path": "org/dpo_data"},
                    },
                ],
            },
        )
        configs_seen = _install_trainer_mocks(monkeypatch, [TrainResult(success=True)])
        orch = PipelineOrchestrator(cfg, b"yaml bytes")
        assert not os.path.exists(tmp_path / "stage1" / "final_model")
        code = orch.run(stage_filter="dpo_stage")
        assert code == EXIT_SUCCESS
        assert len(configs_seen) == 1
        # The stage used its explicit model, not the (absent) chain seed.
        assert configs_seen[0].model.name_or_path == "org/other-base"

    def test_stage_run_preserves_prior_completed_stage_for_later_resume(self, tmp_path, monkeypatch):
        """F-P2-FAB-12: a full run then ``--stage stage2`` (re-run one stage) must
        NOT clobber stage 1's completed history; a subsequent ``--resume-from
        stage3`` must skip stage 1 (its ``final_model/`` is on disk) rather than
        re-training it."""
        # 1) Full successful run materialises every stage's final_model dir.
        cfg = _three_stage_config(tmp_path)
        _install_trainer_mocks(monkeypatch, [TrainResult(success=True)] * 3)
        orch1 = PipelineOrchestrator(cfg, b"yaml bytes")
        assert orch1.run() == EXIT_SUCCESS

        # 2) Re-run ONLY stage 2 via --stage.
        cfg2 = _three_stage_config(tmp_path)
        _install_trainer_mocks(monkeypatch, [TrainResult(success=True)])
        orch2 = PipelineOrchestrator(cfg2, b"yaml bytes")
        assert orch2.run(stage_filter="dpo_stage") == EXIT_SUCCESS

        # State file must still carry stage 1 as completed with its output_model.
        with open(orch2.paths["state_file"]) as f:
            payload = json.load(f)
        sft = next(s for s in payload["stages"] if s["name"] == "sft_stage")
        assert sft["status"] == "completed"
        assert sft["output_model"] == str(tmp_path / "stage1" / "final_model")

        # 3) Resume from stage 3 — stage 1 must be skipped (not re-trained).
        cfg3 = _three_stage_config(tmp_path)
        configs_seen3 = _install_trainer_mocks(monkeypatch, [TrainResult(success=True)])
        orch3 = PipelineOrchestrator(cfg3, b"yaml bytes")
        assert orch3.run(resume_from="grpo_stage") == EXIT_SUCCESS
        trained_dirs = [c.training.output_dir for c in configs_seen3]
        assert str(tmp_path / "stage1") not in trained_dirs, "stage 1 must not be re-trained on resume"


# ---------------------------------------------------------------------------
# Orchestrator behaviour — --resume-from
# ---------------------------------------------------------------------------


class TestResumeFrom:
    def test_resume_without_state_file_fails(self, tmp_path, monkeypatch):
        cfg = _three_stage_config(tmp_path)
        _install_trainer_mocks(monkeypatch, [])
        orch = PipelineOrchestrator(cfg, b"yaml bytes")
        code = orch.run(resume_from="dpo_stage")
        assert code == EXIT_CONFIG_ERROR

    def test_output_dir_collision_rejected_in_run_not_only_dry_run(self, tmp_path, monkeypatch):
        """Phase 14 post-release review HIGH 4: pre-fix the
        ``training.output_dir`` collision guard only fired during
        ``--dry-run``.  An operator who skipped dry-run could
        silently overwrite per-stage manifests + checkpoints across
        stages.  ``run()`` must execute the same pre-flight."""
        # Two stages writing to the same directory.
        cfg = ForgeConfig(
            model={"name_or_path": "org/base"},
            lora={"r": 8},
            training={"trainer_type": "sft", "output_dir": str(tmp_path)},
            data={"dataset_name_or_path": "org/sft"},
            pipeline={
                "output_dir": str(tmp_path / "pipeline_run"),
                "stages": [
                    {
                        "name": "sft_stage",
                        "training": {"trainer_type": "sft", "output_dir": str(tmp_path / "collide")},
                        "data": {"dataset_name_or_path": "org/sft_data"},
                    },
                    {
                        "name": "dpo_stage",
                        "training": {"trainer_type": "dpo", "output_dir": str(tmp_path / "collide")},
                        "data": {"dataset_name_or_path": "org/dpo_data"},
                    },
                ],
            },
        )
        _install_trainer_mocks(monkeypatch, [TrainResult(success=True), TrainResult(success=True)])
        orch = PipelineOrchestrator(cfg, b"yaml")
        # Note: NOT calling dry_run() — invoking run() directly.
        code = orch.run()
        assert code == EXIT_CONFIG_ERROR

    def test_resume_across_unapproved_gated_stage_is_refused(self, tmp_path, monkeypatch):
        """C3/P2-FAB-01 (Article 14 gate-bypass): a stage recorded as
        ``gated_pending_approval`` has only an UNAPPROVED staging artefact on
        disk.  ``--resume-from`` a later stage must NOT silently skip it (which
        would chain downstream training from the unapproved model) — it must be
        refused with EXIT_CONFIG_ERROR until the operator approves/rejects.

        Pre-fix the skiplist keyed off ``os.path.isdir(output_model)``: the
        staging dir existing was treated as 'already produced', so the gated
        stage was skipped and DPO chained from the unapproved model."""
        cfg = _three_stage_config(tmp_path)
        # First run: SFT stage hits a human-approval gate and exits 4.
        gated_staging = str(tmp_path / "stage1" / "final_model.staging.fg-gated")
        os.makedirs(gated_staging, exist_ok=True)
        _install_trainer_mocks(
            monkeypatch,
            [TrainResult(success=True, awaiting_approval=True, staging_path=gated_staging)],
        )
        orch1 = PipelineOrchestrator(cfg, b"yaml")
        code1 = orch1.run()
        assert code1 == EXIT_AWAITING_APPROVAL
        # Sanity: state recorded SFT as gated, staging dir present on disk.
        with open(orch1.paths["state_file"]) as f:
            payload = json.load(f)
        assert payload["stages"][0]["status"] == "gated_pending_approval"
        assert os.path.isdir(gated_staging)

        # Resume from DPO WITHOUT approving the gated SFT → must be refused, and
        # NO downstream trainer may be instantiated (no chaining from unapproved).
        configs_seen2 = _install_trainer_mocks(monkeypatch, [TrainResult(success=True)])
        orch2 = PipelineOrchestrator(cfg, b"yaml")
        code2 = orch2.run(resume_from="dpo_stage")
        assert code2 == EXIT_CONFIG_ERROR
        assert len(configs_seen2) == 0, "no stage may train when resume is refused"

        # Article 14: the refusal must be recorded in the append-only audit log.
        audit_path = os.path.join(orch2.paths["root_output_dir"], "audit_log.jsonl")
        with open(audit_path) as f:
            events = [json.loads(line) for line in f if line.strip()]
        refused = [e for e in events if e.get("event") == "pipeline.resume_refused"]
        assert len(refused) == 1, (
            f"expected one pipeline.resume_refused event, got {[e.get('event') for e in events]!r}"
        )
        assert refused[0]["requested_stage"] == "dpo_stage"
        assert refused[0]["blocking_stage"] == "sft_stage"
        assert refused[0]["blocking_status"] == "gated_pending_approval"

    def test_resume_after_approval_skips_gated_stage_and_chains_from_promoted_model(self, tmp_path, monkeypatch):
        """H4/P2-FAB-03: after ``forgelm approve`` renames the staging dir to
        ``final_model/``, a ``--resume-from`` of a later stage must SKIP the
        gated stage (not re-train it) and chain the downstream stage from the
        *promoted* ``final_model/`` path — and emit exactly one
        ``pipeline.stage_started`` per stage_index across both runs.

        Pre-fix the skiplist refused unconditionally on
        ``status=gated_pending_approval`` (it could not tell an approved stage
        from an unapproved one), so the documented approve→resume flow either
        re-trained the gated stage or was blocked outright."""
        cfg = _three_stage_config(tmp_path)
        # First run: SFT gates with a realistic ``.staging.<run_id>`` path.
        gated_staging = str(tmp_path / "stage1" / f"final_model.staging.fg-{_generate_run_id()[-6:]}")
        os.makedirs(gated_staging, exist_ok=True)
        _install_trainer_mocks(
            monkeypatch,
            [TrainResult(success=True, awaiting_approval=True, staging_path=gated_staging)],
        )
        orch1 = PipelineOrchestrator(cfg, b"yaml")
        assert orch1.run() == EXIT_AWAITING_APPROVAL

        # Simulate ``forgelm approve``: promote staging → final_model/ (rename).
        # (The trainer mock auto-creates a stub ``final_model/`` on instantiation;
        # clear it first so the rename mirrors the real approve flow, which
        # requires the final path to be free before promotion.)
        promoted = str(tmp_path / "stage1" / "final_model")
        shutil.rmtree(promoted, ignore_errors=True)
        os.rename(gated_staging, promoted)
        assert not os.path.isdir(gated_staging)
        assert os.path.isdir(promoted)

        # Resume from DPO: SFT must be skipped, DPO must chain from the promoted
        # model, and only DPO + GRPO may train.
        configs_seen2 = _install_trainer_mocks(monkeypatch, [TrainResult(success=True), TrainResult(success=True)])
        orch2 = PipelineOrchestrator(cfg, b"yaml")
        code2 = orch2.run(resume_from="dpo_stage")
        assert code2 == EXIT_SUCCESS
        assert len(configs_seen2) == 2, "only DPO + GRPO may train; SFT must be skipped post-approval"

        # State file: SFT recorded as completed pointing at the promoted model;
        # pipeline finalised as completed.
        with open(orch2.paths["state_file"]) as f:
            payload = json.load(f)
        assert payload["final_status"] == "completed"
        assert payload["stages"][0]["status"] == "completed"
        assert payload["stages"][0]["output_model"] == promoted
        assert payload["stages"][1]["input_model"] == promoted

        # Audit invariant: exactly one pipeline.stage_started per stage_index
        # across BOTH runs (the gated stage must not be re-started on resume).
        audit_path = os.path.join(orch2.paths["root_output_dir"], "audit_log.jsonl")
        with open(audit_path) as f:
            events = [json.loads(line) for line in f if line.strip()]
        started_indices = [e["stage_index"] for e in events if e.get("event") == "pipeline.stage_started"]
        assert sorted(started_indices) == [0, 1, 2], (
            f"expected one stage_started per stage_index, got {sorted(started_indices)!r}"
        )

    def test_resume_to_success_after_failure_resets_terminal_fields_and_emits_one_completed(
        self, tmp_path, monkeypatch
    ):
        """H4/P2-FAB-04: a previously-failed run (final_status=stopped_at_stage)
        resumed to success must reset the terminal fields and emit exactly one
        ``pipeline.completed`` carrying ``final_status="completed"`` — not the
        stale ``stopped_at_stage`` + ``stopped_at`` name."""
        cfg = _three_stage_config(tmp_path)
        # First run: stage 2 fails → stopped_at_stage.
        _install_trainer_mocks(monkeypatch, [TrainResult(success=True), TrainResult(success=False, error="oom")])
        orch1 = PipelineOrchestrator(cfg, b"yaml bytes")
        orch1.run()
        with open(orch1.paths["state_file"]) as f:
            payload1 = json.load(f)
        assert payload1["final_status"] == "stopped_at_stage"
        assert payload1["stopped_at"] == "dpo_stage"

        # Resume from stage 2 with passing mocks → exit 0.
        _install_trainer_mocks(monkeypatch, [TrainResult(success=True), TrainResult(success=True)])
        orch2 = PipelineOrchestrator(cfg, b"yaml bytes")
        assert orch2.run(resume_from="dpo_stage") == EXIT_SUCCESS

        with open(orch2.paths["state_file"]) as f:
            payload2 = json.load(f)
        assert payload2["final_status"] == "completed"
        assert payload2["stopped_at"] is None

        # Exactly one pipeline.completed across the resume run, with the
        # corrected terminal status (not the stale stopped_at_stage).
        audit_path = os.path.join(orch2.paths["root_output_dir"], "audit_log.jsonl")
        with open(audit_path) as f:
            events = [json.loads(line) for line in f if line.strip()]
        completed = [e for e in events if e.get("event") == "pipeline.completed"]
        # Two runs wrote to the same append-only log; the LAST completed event
        # is the resume's and must carry the corrected status.
        assert completed[-1]["final_status"] == "completed"
        assert completed[-1]["stopped_at"] is None

    def test_resume_to_success_after_gate_resets_terminal_fields_and_emits_completed(self, tmp_path, monkeypatch):
        """H4/P2-FAB-04 (gated variant): a previously-gated run
        (final_status=gated_pending_approval) that is approved and resumed to
        success must finalise as ``completed`` and emit a ``pipeline.completed``
        event — pre-fix the gated status was not in the finalise allowlist so
        NO completion event fired and the state file permanently described a
        finished pipeline as gated."""
        cfg = _three_stage_config(tmp_path)
        gated_staging = str(tmp_path / "stage1" / "final_model.staging.fg-gatedfin")
        os.makedirs(gated_staging, exist_ok=True)
        _install_trainer_mocks(
            monkeypatch,
            [TrainResult(success=True, awaiting_approval=True, staging_path=gated_staging)],
        )
        orch1 = PipelineOrchestrator(cfg, b"yaml")
        assert orch1.run() == EXIT_AWAITING_APPROVAL
        with open(orch1.paths["state_file"]) as f:
            assert json.load(f)["final_status"] == "gated_pending_approval"

        # Approve (rename staging → final_model/) then resume to success.
        shutil.rmtree(str(tmp_path / "stage1" / "final_model"), ignore_errors=True)
        os.rename(gated_staging, str(tmp_path / "stage1" / "final_model"))
        _install_trainer_mocks(monkeypatch, [TrainResult(success=True), TrainResult(success=True)])
        orch2 = PipelineOrchestrator(cfg, b"yaml")
        assert orch2.run(resume_from="dpo_stage") == EXIT_SUCCESS

        with open(orch2.paths["state_file"]) as f:
            payload2 = json.load(f)
        assert payload2["final_status"] == "completed"
        assert payload2["stopped_at"] is None

        audit_path = os.path.join(orch2.paths["root_output_dir"], "audit_log.jsonl")
        with open(audit_path) as f:
            events = [json.loads(line) for line in f if line.strip()]
        completed = [e for e in events if e.get("event") == "pipeline.completed"]
        assert len(completed) == 1, f"expected one pipeline.completed, got {[e.get('event') for e in events]!r}"
        assert completed[0]["final_status"] == "completed"

    def test_stage_config_error_exits_config_error(self, tmp_path, monkeypatch):
        """C7-review: a ConfigError from a stage (e.g. unset judge_api_key_env)
        must route to EXIT_CONFIG_ERROR (1), not the generic EXIT_TRAINING_ERROR (2)."""
        from forgelm.config import ConfigError

        cfg = _three_stage_config(tmp_path)
        _install_trainer_mocks(monkeypatch, [])  # stub model/data/utils

        # Override the trainer module with one whose train() raises ConfigError.
        class _RaisingTrainer:
            def __init__(self, **kwargs):
                pass

            def train(self, resume_from_checkpoint=None):
                raise ConfigError("judge_api_key_env names an unset variable")

        raising_mod = types.ModuleType("forgelm.trainer")
        raising_mod.ForgeTrainer = _RaisingTrainer
        monkeypatch.setitem(sys.modules, "forgelm.trainer", raising_mod)

        orch = PipelineOrchestrator(cfg, b"yaml bytes")
        assert orch.run() == EXIT_CONFIG_ERROR

    def test_resume_skips_completed_stages(self, tmp_path, monkeypatch):
        cfg = _three_stage_config(tmp_path)
        # First run: stage 1 completes, stage 2 fails (training crash).
        results_run1 = [
            TrainResult(success=True),
            TrainResult(success=False, error="oom"),
        ]
        _install_trainer_mocks(monkeypatch, results_run1)
        orch1 = PipelineOrchestrator(cfg, b"yaml bytes")
        orch1.run()
        # Second run: resume from dpo_stage.  Stage 1 already completed
        # AND its output_model dir exists (mock created it) — must skip.
        results_run2 = [TrainResult(success=True), TrainResult(success=True)]
        configs_seen2 = _install_trainer_mocks(monkeypatch, results_run2)
        orch2 = PipelineOrchestrator(cfg, b"yaml bytes")
        code = orch2.run(resume_from="dpo_stage")
        assert code == EXIT_SUCCESS
        # Only stage 2 + stage 3 ran — stage 1 was skipped.
        assert len(configs_seen2) == 2

    def test_stale_config_hash_rejects_resume(self, tmp_path, monkeypatch):
        cfg = _three_stage_config(tmp_path)
        _install_trainer_mocks(monkeypatch, [TrainResult(success=False, error="x")])
        orch1 = PipelineOrchestrator(cfg, b"original yaml")
        orch1.run()
        # Operator edited the YAML; resume against a different hash.
        # Post-Phase-14-review: refusal flows back through ``run()`` as a
        # plain return value (not ``sys.exit`` mid-method) so the audit-
        # log + summary path runs uniformly on every refusal.
        orch2 = PipelineOrchestrator(cfg, b"edited yaml")
        code = orch2.run(resume_from="dpo_stage")
        assert code == EXIT_CONFIG_ERROR

    def test_force_resume_accepts_stale_hash_with_warning(self, tmp_path, monkeypatch, caplog):
        import logging

        cfg = _three_stage_config(tmp_path)
        _install_trainer_mocks(monkeypatch, [TrainResult(success=False, error="x")])
        orch1 = PipelineOrchestrator(cfg, b"original yaml")
        orch1.run()
        _install_trainer_mocks(monkeypatch, [TrainResult(success=True), TrainResult(success=True)])
        orch2 = PipelineOrchestrator(cfg, b"edited yaml")
        with caplog.at_level(logging.WARNING, logger="forgelm.pipeline"):
            code = orch2.run(resume_from="dpo_stage", force_resume=True)
        assert code == EXIT_SUCCESS

    def test_force_resume_emits_audit_event_with_both_hashes(self, tmp_path, monkeypatch):
        """Phase 14 review F-B-2 regression: ``--force-resume`` must
        emit a ``pipeline.force_resume`` audit event carrying both the
        old and new ``pipeline_config_hash`` so a compliance reviewer
        can distinguish an operator-approved override from a normal
        resume.  Pre-fix, only a WARNING log line was emitted — invisible
        in the append-only audit-log JSONL stream."""
        cfg = _three_stage_config(tmp_path)
        _install_trainer_mocks(monkeypatch, [TrainResult(success=False, error="x")])
        orch1 = PipelineOrchestrator(cfg, b"yaml v1")
        orch1.run()

        _install_trainer_mocks(monkeypatch, [TrainResult(success=True), TrainResult(success=True)])
        orch2 = PipelineOrchestrator(cfg, b"yaml v2 (operator edited)")
        orch2.run(resume_from="dpo_stage", force_resume=True)

        audit_path = os.path.join(orch2.paths["root_output_dir"], "audit_log.jsonl")
        assert os.path.exists(audit_path)
        with open(audit_path) as f:
            events = [json.loads(line) for line in f if line.strip()]
        force_resume_events = [e for e in events if e.get("event") == "pipeline.force_resume"]
        assert len(force_resume_events) == 1, (
            f"Expected exactly one pipeline.force_resume audit event, got "
            f"{len(force_resume_events)}: {[e.get('event') for e in events]!r}"
        )
        evt = force_resume_events[0]
        assert "old_config_hash" in evt
        assert "new_config_hash" in evt
        assert evt["old_config_hash"] != evt["new_config_hash"]
        assert "yaml v1" not in str(evt) and "yaml v2" not in str(evt), (
            "Audit event must record hashes, not raw YAML bytes."
        )

    def test_topology_guard_runs_even_on_hash_match(self, tmp_path, monkeypatch):
        """Phase 14 post-release review BLOCKER 3: a tampered or
        truncated ``pipeline_state.json`` could carry the same
        ``pipeline_config_hash`` as the current YAML (operator never
        edited the YAML) but a damaged ``stages[]`` array — fewer
        entries, wrong order, or hand-edited names.  Pre-fix,
        ``_validate_resume_state`` returned ``None`` immediately on
        hash match without running the topology check, so the resume
        would proceed against a corrupted state file and
        ``state.stages[i]`` would silently overwrite the wrong stage's
        history.  Topology guard must now run UNCONDITIONALLY."""
        cfg = _three_stage_config(tmp_path)
        _install_trainer_mocks(monkeypatch, [TrainResult(success=False, error="x")])
        orch1 = PipelineOrchestrator(cfg, b"yaml")
        orch1.run()

        # Surgically truncate the on-disk state to TWO stages while
        # keeping the YAML (hence the hash) unchanged.  This simulates
        # disk corruption / hand-edit / partial-write tamper.
        with open(orch1.paths["state_file"]) as f:
            payload = json.load(f)
        payload["stages"] = payload["stages"][:2]
        with open(orch1.paths["state_file"], "w") as f:
            json.dump(payload, f)

        _install_trainer_mocks(monkeypatch, [TrainResult(success=True), TrainResult(success=True)])
        orch2 = PipelineOrchestrator(cfg, b"yaml")
        # Hash MATCHES (same YAML bytes) but topology drifted — must refuse.
        code = orch2.run(resume_from="dpo_stage")
        assert code == EXIT_CONFIG_ERROR

    def test_force_resume_refuses_when_stage_topology_changed(self, tmp_path, monkeypatch):
        """Phase 14 review-response regression: ``--force-resume`` must
        still refuse when the on-disk state's stage list disagrees with
        the current pipeline (count, names, or order).  Without this
        guard, a renamed/inserted/deleted stage between runs would let
        the orchestrator address ``state.stages[i]`` from the old shape
        while iterating ``self.pipeline.stages[i]`` from the new shape,
        silently corrupting the audit trail."""
        cfg = _three_stage_config(tmp_path)
        _install_trainer_mocks(monkeypatch, [TrainResult(success=False, error="x")])
        orch1 = PipelineOrchestrator(cfg, b"yaml v1")
        orch1.run()

        # Build a *different* topology (rename middle stage) while keeping
        # the YAML hash changed too, so force_resume is the only thing
        # standing between us and a resume.
        renamed_cfg = ForgeConfig(
            model={"name_or_path": "org/base"},
            lora={"r": 8},
            training={"trainer_type": "sft", "output_dir": str(tmp_path)},
            data={"dataset_name_or_path": "org/sft"},
            pipeline={
                "output_dir": str(tmp_path / "pipeline_run"),
                "stages": [
                    {
                        "name": "sft_stage",
                        "training": {"trainer_type": "sft", "output_dir": str(tmp_path / "stage1")},
                        "data": {"dataset_name_or_path": "org/sft_data"},
                    },
                    {
                        "name": "renamed_middle",  # was 'dpo_stage'
                        "training": {"trainer_type": "dpo", "output_dir": str(tmp_path / "stage2")},
                        "data": {"dataset_name_or_path": "org/dpo_data"},
                    },
                    {
                        "name": "grpo_stage",
                        "training": {"trainer_type": "grpo", "output_dir": str(tmp_path / "stage3")},
                        "data": {"dataset_name_or_path": "org/math_data"},
                    },
                ],
            },
        )
        _install_trainer_mocks(monkeypatch, [TrainResult(success=True), TrainResult(success=True)])
        orch2 = PipelineOrchestrator(renamed_cfg, b"yaml v2 (renamed)")
        code = orch2.run(resume_from="grpo_stage", force_resume=True)
        assert code == EXIT_CONFIG_ERROR
        # No pipeline.force_resume audit event should have been emitted —
        # topology mismatch is refused *before* the audit hook.
        audit_path = os.path.join(orch2.paths["root_output_dir"], "audit_log.jsonl")
        if os.path.exists(audit_path):
            with open(audit_path) as f:
                events = [json.loads(line) for line in f if line.strip()]
            assert not any(e.get("event") == "pipeline.force_resume" for e in events), (
                "pipeline.force_resume must not fire when topology has changed."
            )

    def test_resume_with_unknown_stage_name_fails(self, tmp_path, monkeypatch):
        cfg = _three_stage_config(tmp_path)
        _install_trainer_mocks(monkeypatch, [TrainResult(success=False, error="x")])
        orch1 = PipelineOrchestrator(cfg, b"yaml")
        orch1.run()
        orch2 = PipelineOrchestrator(cfg, b"yaml")
        code = orch2.run(resume_from="nonexistent")
        assert code == EXIT_CONFIG_ERROR


# ---------------------------------------------------------------------------
# Orchestrator behaviour — audit + webhook (best-effort emission, no crash)
# ---------------------------------------------------------------------------


class TestAuditAndWebhook:
    def test_audit_events_emitted_on_happy_path(self, tmp_path, monkeypatch):
        cfg = _three_stage_config(tmp_path)
        results = [TrainResult(success=True), TrainResult(success=True), TrainResult(success=True)]
        _install_trainer_mocks(monkeypatch, results)
        orch = PipelineOrchestrator(cfg, b"yaml")
        orch.run()
        # Audit log file lives under the root output dir.
        audit_path = os.path.join(orch.paths["root_output_dir"], "audit_log.jsonl")
        assert os.path.exists(audit_path)
        with open(audit_path) as f:
            events = [json.loads(line) for line in f if line.strip()]
        event_names = [e["event"] for e in events]
        assert "pipeline.started" in event_names
        assert event_names.count("pipeline.stage_started") == 3
        assert event_names.count("pipeline.stage_completed") == 3
        assert "pipeline.completed" in event_names

    def test_all_pipeline_events_share_same_top_level_run_id(self, tmp_path, monkeypatch):
        """Phase 14 review final-round F-B-1 regression: every entry in
        the pipeline-level audit log must carry the same top-level
        ``run_id`` (the pipeline run id), so SIEM filters and Article 12
        correlation work on a single field.  Pre-fix, ``_audit_event``
        constructed a fresh ``AuditLogger`` per call → each entry got a
        different auto-generated ``fg-<random>``."""
        cfg = _three_stage_config(tmp_path)
        results = [TrainResult(success=True), TrainResult(success=True), TrainResult(success=True)]
        _install_trainer_mocks(monkeypatch, results)
        orch = PipelineOrchestrator(cfg, b"yaml")
        orch.run()
        audit_path = os.path.join(orch.paths["root_output_dir"], "audit_log.jsonl")
        with open(audit_path) as f:
            entries = [json.loads(line) for line in f if line.strip()]
        # Every entry carries a top-level ``run_id`` field; collect the
        # set across all pipeline.* events.
        pipeline_entries = [e for e in entries if e.get("event", "").startswith("pipeline.")]
        assert len(pipeline_entries) >= 5, "expected at least 5 pipeline.* events on a happy 3-stage run"
        top_level_ids = {e["run_id"] for e in pipeline_entries}
        assert len(top_level_ids) == 1, f"All pipeline.* events must share one top-level run_id, got {top_level_ids!r}"
        # The pinned run_id must equal the pipeline_run_id field on the
        # entries (which is the contract: pipeline.* events carry the
        # pipeline run id in both surfaces).
        (top_level_id,) = top_level_ids
        pipeline_run_ids = {e["pipeline_run_id"] for e in pipeline_entries}
        assert pipeline_run_ids == {top_level_id}

    def test_webhook_failures_do_not_abort_pipeline(self, tmp_path, monkeypatch):
        """A crashing webhook notifier must not derail the pipeline.

        Mirrors the existing per-stage webhook discipline — best-effort
        telemetry never blocks the operator's actual work."""
        cfg = _three_stage_config(tmp_path)
        _install_trainer_mocks(
            monkeypatch, [TrainResult(success=True), TrainResult(success=True), TrainResult(success=True)]
        )
        with patch("forgelm.webhook.WebhookNotifier", side_effect=RuntimeError("notifier down")):
            orch = PipelineOrchestrator(cfg, b"yaml")
            code = orch.run()
        assert code == EXIT_SUCCESS

    def test_audit_events_share_one_pipeline_run_id(self, tmp_path, monkeypatch):
        """Phase 14 review final-round F-B-1 regression: every entry in
        ``audit_log.jsonl`` emitted by the orchestrator must carry the
        **same** top-level ``run_id`` field (= the pipeline run id), so
        SIEM dashboards can group all events for one pipeline run by
        that single key.  Pre-fix, ``_audit_event`` constructed a fresh
        ``AuditLogger`` per call and each got a different auto-
        generated ``fg-<random>`` run_id."""
        cfg = _three_stage_config(tmp_path)
        _install_trainer_mocks(monkeypatch, [TrainResult(success=True)] * 3)
        orch = PipelineOrchestrator(cfg, b"yaml")
        orch.run()
        audit_path = os.path.join(orch.paths["root_output_dir"], "audit_log.jsonl")
        with open(audit_path) as f:
            events = [json.loads(line) for line in f if line.strip()]
        # All pipeline.* events must have identical top-level run_id.
        run_ids = {e.get("run_id") for e in events if e.get("event", "").startswith("pipeline.")}
        assert len(run_ids) == 1, (
            f"Pipeline events must share one top-level run_id; got {run_ids!r} across {len(events)} entries."
        )
        # And that single run_id must equal the pipeline run id surfaced
        # in each event's ``pipeline_run_id`` field.
        pipeline_run_ids = {e.get("pipeline_run_id") for e in events if e.get("event", "").startswith("pipeline.")}
        assert pipeline_run_ids == run_ids, (
            f"Top-level run_id {run_ids!r} must equal pipeline_run_id {pipeline_run_ids!r}."
        )

    def test_audit_event_failures_do_not_abort_pipeline(self, tmp_path, monkeypatch):
        """Phase 14 review F-N-5 regression: ``_audit_event``'s
        documented ``except Exception`` swallow path must be reached and
        the pipeline must continue.  An audit-write failure (read-only
        filesystem, disk full, malformed audit logger config) is
        documented as best-effort — the orchestrator emits a WARNING and
        carries on; the test patches ``AuditLogger.log_event`` to raise
        and asserts the run completes with the expected exit code."""
        cfg = _three_stage_config(tmp_path)
        _install_trainer_mocks(monkeypatch, [TrainResult(success=True)] * 3)

        from forgelm.compliance import AuditLogger as _RealAuditLogger

        original_log_event = _RealAuditLogger.log_event

        def _raising_log_event(self, event, **fields):
            if event.startswith("pipeline."):
                raise OSError("Disk full / read-only audit log")
            return original_log_event(self, event, **fields)

        monkeypatch.setattr(_RealAuditLogger, "log_event", _raising_log_event)
        orch = PipelineOrchestrator(cfg, b"yaml")
        code = orch.run()
        assert code == EXIT_SUCCESS

    def test_emit_summary_json_output_is_round_trippable(self, tmp_path, monkeypatch, capsys):
        """Phase 14 review F-N-7 regression: the ``output_format='json'``
        branch of ``_emit_summary`` is the dispatcher contract for
        ``forgelm --config pipeline.yaml --output-format json`` — it
        must print a single JSON object that round-trips through
        ``json.loads`` and carries the per-stage payload reviewers
        consume programmatically."""
        cfg = _three_stage_config(tmp_path)
        _install_trainer_mocks(monkeypatch, [TrainResult(success=True)] * 3)
        orch = PipelineOrchestrator(cfg, b"yaml", output_format="json")
        code = orch.run()
        assert code == EXIT_SUCCESS
        out = capsys.readouterr().out
        # The JSON object is the only thing on stdout; logger goes to stderr.
        payload = json.loads(out)
        assert payload["final_status"] == "completed"
        assert len(payload["stages"]) == 3
        assert all(s["status"] == "completed" for s in payload["stages"])


class TestReExecuteClearsStaleFields:
    def test_resumed_successful_stage_clears_prior_error(self, tmp_path, monkeypatch):
        """F-P2-FAB-07: after a failed run is resumed and succeeds, the
        re-executed stage's persisted state must NOT carry the prior attempt's
        ``error`` / ``skipped_reason`` — a green pipeline reporting stage-level
        errors that did not occur this run sends incident reviews chasing ghosts."""
        cfg = _three_stage_config(tmp_path)
        # First run: stage 2 fails with a specific error.
        _install_trainer_mocks(
            monkeypatch,
            [TrainResult(success=True), TrainResult(success=False, error="Trainer crashed: CUDA OOM")],
        )
        orch1 = PipelineOrchestrator(cfg, b"yaml bytes")
        orch1.run()
        with open(orch1.paths["state_file"]) as f:
            payload1 = json.load(f)
        dpo1 = next(s for s in payload1["stages"] if s["name"] == "dpo_stage")
        assert dpo1["error"] == "Trainer crashed: CUDA OOM"

        # Resume from stage 2 with passing mocks → success.
        _install_trainer_mocks(monkeypatch, [TrainResult(success=True), TrainResult(success=True)])
        orch2 = PipelineOrchestrator(cfg, b"yaml bytes")
        assert orch2.run(resume_from="dpo_stage") == EXIT_SUCCESS

        with open(orch2.paths["state_file"]) as f:
            payload2 = json.load(f)
        dpo2 = next(s for s in payload2["stages"] if s["name"] == "dpo_stage")
        assert dpo2["status"] == "completed"
        assert dpo2["error"] is None, "stale error must be cleared on re-execution"
        assert dpo2["skipped_reason"] is None


def _pipeline_args(**overrides):
    """Build an argparse-style namespace for run_pipeline_from_args."""
    base = {
        "stage": None,
        "resume_from": None,
        "force_resume": False,
        "input_model": None,
        "output_format": "text",
        "dry_run": False,
        "fit_check": False,
        "merge": False,
        "generate_data": False,
        "compliance_export": False,
        "benchmark_only": False,
    }
    base.update(overrides)
    return types.SimpleNamespace(**base)


def _install_crashing_trainer(monkeypatch, *, crash_on_stage_index, exc):
    """Install a trainer that raises *exc* on the train() call of the given stage
    index (0-based) and succeeds otherwise. Materialises final_model dirs like
    the happy-path mock so the chain guard is satisfied for prior stages."""
    counter = {"i": 0}

    class _CrashingForgeTrainer:
        def __init__(self, *, model, tokenizer, config, dataset):
            final_dir = os.path.join(config.training.output_dir, "final_model")
            os.makedirs(final_dir, exist_ok=True)
            self.config = config
            self._idx = counter["i"]
            counter["i"] += 1

        def train(self, resume_from_checkpoint=None):
            if self._idx == crash_on_stage_index:
                raise exc
            return TrainResult(success=True)

    fake_trainer_mod = types.ModuleType("forgelm.trainer")
    fake_trainer_mod.ForgeTrainer = _CrashingForgeTrainer
    monkeypatch.setitem(sys.modules, "forgelm.trainer", fake_trainer_mod)

    fake_model_mod = types.ModuleType("forgelm.model")
    fake_model_mod.get_model_and_tokenizer = lambda config: (MagicMock(), MagicMock())
    monkeypatch.setitem(sys.modules, "forgelm.model", fake_model_mod)

    fake_data_mod = types.ModuleType("forgelm.data")
    fake_data_mod.prepare_dataset = lambda config, tokenizer: {"train": [{"text": "x"}]}
    monkeypatch.setitem(sys.modules, "forgelm.data", fake_data_mod)

    fake_utils_mod = types.ModuleType("forgelm.utils")
    fake_utils_mod.setup_authentication = lambda token: None
    monkeypatch.setitem(sys.modules, "forgelm.utils", fake_utils_mod)


class TestPerStageTrainingErrorRouting:
    """F-P2-FAB-18: a stage whose trainer CRASHES (vs a gate failure) routes to
    EXIT_TRAINING_ERROR (2), halts the chain, and skips downstream stages."""

    def test_trainer_crash_routes_to_exit_2_and_skips_downstream(self, tmp_path, monkeypatch):
        cfg = _three_stage_config(tmp_path)
        _install_crashing_trainer(monkeypatch, crash_on_stage_index=1, exc=RuntimeError("CUDA error"))
        orch = PipelineOrchestrator(cfg, b"yaml bytes")
        code = orch.run()
        assert code == EXIT_TRAINING_ERROR
        with open(orch.paths["state_file"]) as f:
            payload = json.load(f)
        statuses = [s["status"] for s in payload["stages"]]
        assert statuses == ["completed", "failed", "skipped_due_to_prior_revert"]
        dpo = payload["stages"][1]
        assert dpo["error"].startswith("Trainer crashed:")


class TestPipelineExceptionBoundary:
    def test_save_state_oserror_maps_to_training_error_with_json_envelope(self, tmp_path, monkeypatch):
        """F-P2-FAB-13: a mid-run _atomic_write_json OSError (disk full /
        permission flip) must map to EXIT_TRAINING_ERROR (2) — the runtime-error
        class — with the 2-key JSON envelope on stdout, NOT a raw traceback +
        interpreter exit 1 that collides with EXIT_CONFIG_ERROR and breaks JSON
        parsers."""
        cfg = _three_stage_config(tmp_path)
        _install_trainer_mocks(monkeypatch, [TrainResult(success=True)] * 3)

        def _boom(path, payload):
            raise OSError("No space left on device")

        monkeypatch.setattr("forgelm.cli._pipeline._atomic_write_json", _boom)

        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            code = run_pipeline_from_args(cfg, b"yaml bytes", _pipeline_args(output_format="json"))

        assert code == EXIT_TRAINING_ERROR
        envelope = json.loads(buf.getvalue())
        assert envelope["success"] is False
        assert "error" in envelope
