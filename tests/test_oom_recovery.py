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
