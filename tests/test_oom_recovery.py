"""Regression tests for OOM recovery — verifies that _original_batch_size /
_original_grad_accum are stored correctly and that _export_compliance_if_needed
uses the pre-OOM values in the compliance manifest."""

from unittest.mock import MagicMock, patch

import pytest

# ForgeTrainer requires torch — skip all tests if not available
torch_available = True
try:
    import torch  # noqa: F401
except ImportError:
    torch_available = False


def _make_forge_config(batch_size=4, grad_accum=2, output_dir=None):
    """Build a minimal ForgeConfig with the given training parameters."""
    from forgelm.config import ForgeConfig

    data = {
        "model": {"name_or_path": "org/model"},
        "lora": {},
        "training": {
            "per_device_train_batch_size": batch_size,
            "gradient_accumulation_steps": grad_accum,
            "output_dir": output_dir or "./checkpoints",
        },
        "data": {"dataset_name_or_path": "org/dataset"},
    }
    return ForgeConfig(**data)


def _make_trainer(config, tmp_path):
    """Construct a ForgeTrainer with all heavy dependencies mocked out."""
    from forgelm.trainer import ForgeTrainer

    model = MagicMock()
    tokenizer = MagicMock()
    dataset = {"train": list(range(10))}

    with (
        patch("forgelm.trainer.WebhookNotifier"),
        patch("forgelm.compliance.AuditLogger"),
    ):
        trainer = ForgeTrainer.__new__(ForgeTrainer)
        trainer.model = model
        trainer.tokenizer = tokenizer
        trainer.config = config
        trainer.dataset = dataset
        trainer.checkpoint_dir = str(tmp_path)
        trainer.run_name = "test_run"
        trainer.notifier = MagicMock()
        trainer.audit = MagicMock()
    return trainer


@pytest.mark.skipif(not torch_available, reason="torch not installed")
class TestOriginalBatchSizeStoredOnTrain:
    def test_original_batch_size_stored(self, tmp_path):
        """ForgeTrainer.train() must set _original_batch_size before any training starts."""
        config = _make_forge_config(batch_size=8, grad_accum=4, output_dir=str(tmp_path))
        trainer = _make_trainer(config, tmp_path)

        # Patch all side-effectful calls in train()
        trainer.notifier = MagicMock()
        trainer.audit = MagicMock()
        trainer._build_trainer = MagicMock()
        trainer._run_with_oom_recovery = MagicMock(return_value=MagicMock(metrics={"train_loss": 0.5}))
        trainer.save_final_model = MagicMock()
        trainer.execute_evaluation_checks = MagicMock(return_value=True)
        trainer._run_benchmark_if_configured = MagicMock(return_value=None)
        trainer._run_safety_if_configured = MagicMock(return_value=None)
        trainer._run_judge_if_configured = MagicMock(return_value=None)
        trainer._generate_model_card = MagicMock()
        trainer._generate_model_integrity = MagicMock()
        trainer._generate_deployer_instructions = MagicMock()
        trainer._export_compliance_if_needed = MagicMock()
        trainer._collect_resource_usage = MagicMock(return_value=None)

        trainer.train()

        assert trainer._original_batch_size == 8

    def test_original_grad_accum_stored(self, tmp_path):
        """ForgeTrainer.train() must set _original_grad_accum before any training starts."""
        config = _make_forge_config(batch_size=4, grad_accum=8, output_dir=str(tmp_path))
        trainer = _make_trainer(config, tmp_path)

        trainer.notifier = MagicMock()
        trainer.audit = MagicMock()
        trainer._build_trainer = MagicMock()
        trainer._run_with_oom_recovery = MagicMock(return_value=MagicMock(metrics={"train_loss": 0.5}))
        trainer.save_final_model = MagicMock()
        trainer.execute_evaluation_checks = MagicMock(return_value=True)
        trainer._run_benchmark_if_configured = MagicMock(return_value=None)
        trainer._run_safety_if_configured = MagicMock(return_value=None)
        trainer._run_judge_if_configured = MagicMock(return_value=None)
        trainer._generate_model_card = MagicMock()
        trainer._generate_model_integrity = MagicMock()
        trainer._generate_deployer_instructions = MagicMock()
        trainer._export_compliance_if_needed = MagicMock()
        trainer._collect_resource_usage = MagicMock(return_value=None)

        trainer.train()

        assert trainer._original_grad_accum == 8

    def test_originals_match_initial_config_values(self, tmp_path):
        """_original_batch_size/_original_grad_accum must reflect the config values
        at the moment train() is called, not any later mutated values."""
        config = _make_forge_config(batch_size=16, grad_accum=2, output_dir=str(tmp_path))
        trainer = _make_trainer(config, tmp_path)

        trainer.notifier = MagicMock()
        trainer.audit = MagicMock()
        trainer._build_trainer = MagicMock()
        trainer._run_with_oom_recovery = MagicMock(return_value=MagicMock(metrics={"train_loss": 0.5}))
        trainer.save_final_model = MagicMock()
        trainer.execute_evaluation_checks = MagicMock(return_value=True)
        trainer._run_benchmark_if_configured = MagicMock(return_value=None)
        trainer._run_safety_if_configured = MagicMock(return_value=None)
        trainer._run_judge_if_configured = MagicMock(return_value=None)
        trainer._generate_model_card = MagicMock()
        trainer._generate_model_integrity = MagicMock()
        trainer._generate_deployer_instructions = MagicMock()
        trainer._export_compliance_if_needed = MagicMock()
        trainer._collect_resource_usage = MagicMock(return_value=None)

        trainer.train()

        assert trainer._original_batch_size == 16
        assert trainer._original_grad_accum == 2


@pytest.mark.skipif(not torch_available, reason="torch not installed")
class TestComplianceManifestUsesOriginalBatchSize:
    def test_export_compliance_uses_original_batch_size(self, tmp_path):
        """After OOM mutates config.training.per_device_train_batch_size,
        _export_compliance_if_needed must temporarily restore the original values
        so the manifest captures what the user actually configured."""
        from forgelm.results import TrainResult

        config = _make_forge_config(batch_size=16, grad_accum=2, output_dir=str(tmp_path))
        trainer = _make_trainer(config, tmp_path)

        # Simulate what train() does at the start
        trainer._original_batch_size = 16
        trainer._original_grad_accum = 2

        # Simulate OOM having mutated config values
        config.training.per_device_train_batch_size = 4  # halved twice
        config.training.gradient_accumulation_steps = 8  # doubled twice

        result = TrainResult(success=True)
        metrics = {"eval_loss": 0.5}

        captured_manifests = []

        def capture_manifest(config, **kwargs):
            # Record the batch_size that generate_training_manifest sees
            captured_manifests.append(config.training.per_device_train_batch_size)
            return {
                "model_lineage": {},
                "training_parameters": {},
                "data_provenance": {},
                "evaluation_results": {"metrics": {}},
            }

        with (
            patch("forgelm.compliance.generate_training_manifest", side_effect=capture_manifest),
            patch("forgelm.compliance.export_compliance_artifacts"),
        ):
            trainer._export_compliance_if_needed(metrics, result)

        assert len(captured_manifests) == 1
        # Must see the ORIGINAL batch size, not the OOM-halved value
        assert captured_manifests[0] == 16

    def test_export_compliance_restores_config_after_call(self, tmp_path):
        """Config values must be restored to mutated (OOM) values after manifest generation."""
        from forgelm.results import TrainResult

        config = _make_forge_config(batch_size=16, grad_accum=2, output_dir=str(tmp_path))
        trainer = _make_trainer(config, tmp_path)

        trainer._original_batch_size = 16
        trainer._original_grad_accum = 2

        # Simulate OOM
        config.training.per_device_train_batch_size = 4
        config.training.gradient_accumulation_steps = 8

        result = TrainResult(success=True)

        with (
            patch(
                "forgelm.compliance.generate_training_manifest",
                return_value={
                    "model_lineage": {},
                    "training_parameters": {},
                    "data_provenance": {},
                    "evaluation_results": {"metrics": {}},
                },
            ),
            patch("forgelm.compliance.export_compliance_artifacts"),
        ):
            trainer._export_compliance_if_needed({}, result)

        # After the call, config must reflect the OOM-mutated values again
        assert config.training.per_device_train_batch_size == 4
        assert config.training.gradient_accumulation_steps == 8

    def test_manifest_records_oom_recovery_block(self, tmp_path):
        """F-P3-FABLE-04: when OOM mutated the batch size, the manifest must carry
        an explicit ``oom_recovery`` block recording BOTH configured and effective
        values, so the model card (effective) and manifest (configured) no longer
        silently contradict."""
        from forgelm.results import TrainResult

        config = _make_forge_config(batch_size=16, grad_accum=2, output_dir=str(tmp_path))
        trainer = _make_trainer(config, tmp_path)
        trainer._original_batch_size = 16
        trainer._original_grad_accum = 2
        config.training.per_device_train_batch_size = 4  # OOM-mutated (effective)
        config.training.gradient_accumulation_steps = 8

        exported = []
        with (
            patch(
                "forgelm.compliance.generate_training_manifest",
                side_effect=lambda **kw: {
                    "training_parameters": {},
                    "model_lineage": {},
                    "data_provenance": {},
                    "evaluation_results": {"metrics": {}},
                },
            ),
            patch(
                "forgelm.compliance.export_compliance_artifacts",
                side_effect=lambda manifest, d: exported.append(manifest),
            ),
        ):
            trainer._export_compliance_if_needed({}, TrainResult(success=True))

        oom = exported[0]["training_parameters"]["oom_recovery"]
        assert oom["applied"] is True
        assert oom["configured_batch_size"] == 16
        assert oom["effective_batch_size"] == 4
        assert oom["configured_gradient_accumulation_steps"] == 2
        assert oom["effective_gradient_accumulation_steps"] == 8

    def test_no_oom_recovery_block_when_no_oom(self, tmp_path):
        """No OOM (configured == effective) → no ``oom_recovery`` block."""
        from forgelm.results import TrainResult

        config = _make_forge_config(batch_size=8, grad_accum=2, output_dir=str(tmp_path))
        trainer = _make_trainer(config, tmp_path)
        trainer._original_batch_size = 8
        trainer._original_grad_accum = 2

        exported = []
        with (
            patch(
                "forgelm.compliance.generate_training_manifest",
                side_effect=lambda **kw: {
                    "training_parameters": {},
                    "model_lineage": {},
                    "data_provenance": {},
                    "evaluation_results": {"metrics": {}},
                },
            ),
            patch(
                "forgelm.compliance.export_compliance_artifacts",
                side_effect=lambda manifest, d: exported.append(manifest),
            ),
        ):
            trainer._export_compliance_if_needed({}, TrainResult(success=True))

        assert "oom_recovery" not in exported[0]["training_parameters"]

    def test_export_compliance_restore_is_exception_safe(self, tmp_path):
        """F-P3-FABLE-04: if manifest generation raises, config must be restored to
        the EFFECTIVE (post-OOM) values via finally, not left at the configured
        values under the outer best-effort catch."""
        from forgelm.results import TrainResult

        config = _make_forge_config(batch_size=16, grad_accum=2, output_dir=str(tmp_path))
        trainer = _make_trainer(config, tmp_path)
        trainer._original_batch_size = 16
        trainer._original_grad_accum = 2
        config.training.per_device_train_batch_size = 4
        config.training.gradient_accumulation_steps = 8

        def boom(**kwargs):
            raise RuntimeError("manifest build failed")

        with (
            patch("forgelm.compliance.generate_training_manifest", side_effect=boom),
            patch("forgelm.compliance.export_compliance_artifacts"),
        ):
            trainer._export_compliance_if_needed({}, TrainResult(success=True))

        # finally restored the effective values despite the exception.
        assert config.training.per_device_train_batch_size == 4
        assert config.training.gradient_accumulation_steps == 8


class _FakeCallback:
    """Stand-in for an HF default callback (distinct class per name)."""


class _DefaultFlowCallback(_FakeCallback):
    pass


class _ProgressCallback(_FakeCallback):
    pass


@pytest.mark.skipif(not torch_available, reason="torch not installed")
class TestOomRecoveryLoop:
    """F-P2-FAB-15 / F-P3-FABLE-22 / F-P3-FABLE-23: drive the real
    _run_with_oom_recovery loop body (no MagicMock substitution of the loop)."""

    def _seed(self, tmp_path, *, oom_recovery=True, min_bs=1, batch_size=8, grad_accum=1):
        config = _make_forge_config(batch_size=batch_size, grad_accum=grad_accum, output_dir=str(tmp_path))
        config.training.oom_recovery = oom_recovery
        config.training.oom_recovery_min_batch_size = min_bs
        trainer = _make_trainer(config, tmp_path)
        trainer.audit = MagicMock()
        return trainer

    def test_oom_rebuild_passes_only_user_callbacks_not_handler_defaults(self, tmp_path):
        """F-P3-FABLE-22: the rebuild must pass the user-supplied callbacks, NOT the
        live handler list (which already contains HF's instantiated defaults), so
        defaults are not duplicated on every OOM retry."""
        from forgelm.results import TrainResult  # noqa: F401  (keep import style consistent)

        trainer = self._seed(tmp_path, batch_size=8, min_bs=1)

        user_cb = _FakeCallback()
        trainer._user_callbacks = [user_cb]

        # A faithful trainer whose callback_handler ALSO carries HF defaults —
        # the buggy code path read THIS list and re-fed the defaults back in.
        fake_trainer = MagicMock()
        fake_trainer.callback_handler.callbacks = [_DefaultFlowCallback(), _ProgressCallback(), user_cb]
        # First train() raises OOM, second succeeds.
        fake_trainer.train.side_effect = [RuntimeError("CUDA out of memory"), MagicMock(metrics={})]
        trainer.trainer = fake_trainer

        captured = []

        def fake_build(callbacks):
            captured.append(list(callbacks))

        trainer._build_trainer = fake_build

        with patch("torch.cuda.empty_cache"):
            trainer._run_with_oom_recovery(None)

        assert len(captured) == 1  # exactly one rebuild
        rebuilt = captured[0]
        # Only the user callback is re-passed — no DefaultFlow/Progress defaults.
        assert rebuilt == [user_cb]
        assert not any(isinstance(c, (_DefaultFlowCallback, _ProgressCallback)) for c in rebuilt)

    def test_oom_recovery_at_floor_raises_clean_oom_not_zerodivision(self, tmp_path):
        """F-P3-FABLE-23: with min_bs clamped to >=1 and batch already at the floor,
        the handler re-raises the OOM with the 'cannot recover' diagnostic — never a
        ZeroDivisionError from current_bs // new_bs."""
        # min_bs=0 would historically slip past validation; the loop clamps it to 1.
        trainer = self._seed(tmp_path, batch_size=1, min_bs=0)
        fake_trainer = MagicMock()
        fake_trainer.train.side_effect = RuntimeError("CUDA out of memory")
        trainer.trainer = fake_trainer
        trainer._user_callbacks = []

        with patch("torch.cuda.empty_cache"), pytest.raises(RuntimeError, match="out of memory"):
            trainer._run_with_oom_recovery(None)

    def test_oom_recovery_halves_batch_and_emits_audit_event(self, tmp_path):
        """F-P2-FAB-15: one OOM halves batch (8→4), doubles grad-accum, emits the
        training.oom_recovery audit event, then retries to success."""
        trainer = self._seed(tmp_path, batch_size=8, grad_accum=1, min_bs=1)
        fake_trainer = MagicMock()
        fake_trainer.callback_handler.callbacks = []
        fake_trainer.train.side_effect = [RuntimeError("CUDA out of memory"), MagicMock(metrics={})]
        trainer.trainer = fake_trainer
        trainer._user_callbacks = []
        trainer._build_trainer = MagicMock()

        with patch("torch.cuda.empty_cache"):
            trainer._run_with_oom_recovery(None)

        assert trainer.config.training.per_device_train_batch_size == 4
        assert trainer.config.training.gradient_accumulation_steps == 2
        events = [c.args[0] for c in trainer.audit.log_event.call_args_list]
        assert "training.oom_recovery" in events

    def test_non_oom_runtime_error_propagates_unchanged(self, tmp_path):
        """A non-OOM RuntimeError is re-raised immediately (no retry, no rebuild)."""
        trainer = self._seed(tmp_path, batch_size=8, min_bs=1)
        fake_trainer = MagicMock()
        fake_trainer.train.side_effect = RuntimeError("some other crash")
        trainer.trainer = fake_trainer
        trainer._user_callbacks = []
        trainer._build_trainer = MagicMock()

        with pytest.raises(RuntimeError, match="some other crash"):
            trainer._run_with_oom_recovery(None)
        trainer._build_trainer.assert_not_called()


@pytest.mark.skipif(not torch_available, reason="torch not installed")
class TestTrainConstructionFailure:
    """F-P2-FAB-09 / F-P2-FAB-20: a _build_trainer construction failure (and any
    _run_training_pipeline failure) must still emit pipeline.failed + notify_failure
    and re-raise, after notify_start already fired."""

    def test_build_trainer_failure_emits_pipeline_failed_and_notifies(self, tmp_path):
        config = _make_forge_config(batch_size=8, grad_accum=2, output_dir=str(tmp_path))
        trainer = _make_trainer(config, tmp_path)
        trainer.notifier = MagicMock()
        trainer.audit = MagicMock()
        trainer.dataset = {"train": list(range(10))}  # no validation → no EarlyStopping cb

        def _boom(callbacks):
            raise RuntimeError("DeepSpeed config not found")

        trainer._build_trainer = _boom

        with pytest.raises(RuntimeError, match="DeepSpeed config"):
            trainer.train()

        # notify_start fired, then the failure path closed the lifecycle.
        trainer.notifier.notify_start.assert_called_once()
        trainer.notifier.notify_failure.assert_called_once()
        events = [c.args[0] for c in trainer.audit.log_event.call_args_list]
        assert "pipeline.failed" in events

    def test_run_pipeline_failure_emits_pipeline_failed_and_reraises(self, tmp_path):
        config = _make_forge_config(batch_size=8, grad_accum=2, output_dir=str(tmp_path))
        trainer = _make_trainer(config, tmp_path)
        trainer.notifier = MagicMock()
        trainer.audit = MagicMock()
        trainer.dataset = {"train": list(range(10))}
        trainer._build_trainer = MagicMock()
        trainer._run_training_pipeline = MagicMock(side_effect=ValueError("optimizer exploded"))

        with pytest.raises(ValueError, match="optimizer exploded"):
            trainer.train()

        trainer.notifier.notify_failure.assert_called_once()
        events = [c.args[0] for c in trainer.audit.log_event.call_args_list]
        assert "pipeline.failed" in events
