"""Unit tests for forgelm.trainer module (non-GPU tests only)."""

from unittest.mock import MagicMock, patch

import pytest

from forgelm.results import TrainResult

# ForgeTrainer requires torch — skip evaluation tests if not available
torch_available = True
try:
    import torch  # noqa: F401
except ImportError:
    torch_available = False


class TestTrainResult:
    def test_success_result(self):
        result = TrainResult(
            success=True,
            metrics={"eval_loss": 0.5, "train_loss": 0.3},
            final_model_path="/path/to/model",
        )
        assert result.success is True
        assert result.metrics["eval_loss"] == pytest.approx(0.5)
        assert result.final_model_path == "/path/to/model"
        assert result.reverted is False
        assert result.error is None

    def test_reverted_result(self):
        result = TrainResult(
            success=False,
            metrics={"eval_loss": 3.5},
            reverted=True,
        )
        assert result.success is False
        assert result.reverted is True
        assert result.final_model_path is None

    def test_error_result(self):
        result = TrainResult(
            success=False,
            error="OOM error",
        )
        assert result.success is False
        assert result.error == "OOM error"
        assert result.metrics == {}

    def test_empty_metrics_default(self):
        result = TrainResult(success=True)
        assert result.metrics == {}


@pytest.mark.skipif(not torch_available, reason="torch not installed")
class TestEvaluationChecks:
    """Test execute_evaluation_checks via a minimal ForgeTrainer mock."""

    def _make_trainer(self, auto_revert=True, max_loss=None, baseline_loss=None):
        """Create a ForgeTrainer with mocked dependencies."""
        from forgelm.config import ForgeConfig

        config_data = {
            "model": {"name_or_path": "org/model"},
            "lora": {},
            "training": {"output_dir": "/tmp/test_forge_eval"},
            "data": {"dataset_name_or_path": "org/dataset"},
            "evaluation": {
                "auto_revert": auto_revert,
                "max_acceptable_loss": max_loss,
                "baseline_loss": baseline_loss,
            },
        }
        config = ForgeConfig(**config_data)

        # Import after config to avoid heavy deps at module level
        from forgelm.trainer import ForgeTrainer

        with patch("forgelm.trainer.WebhookNotifier"):
            trainer = ForgeTrainer.__new__(ForgeTrainer)
            trainer.config = config
            trainer.dataset = {"train": ["dummy"], "validation": ["dummy"]}
            trainer.checkpoint_dir = "/tmp/test_forge_eval"
            trainer.run_name = "test_finetune"
            trainer.notifier = MagicMock()
            # _revert_model emits an audit event before destructive action;
            # mock the audit logger so revert paths don't AttributeError.
            trainer.audit = MagicMock()
        return trainer

    def test_no_evaluation_config(self):
        from forgelm.config import ForgeConfig
        from forgelm.trainer import ForgeTrainer

        config = ForgeConfig(
            model={"name_or_path": "org/model"},
            lora={},
            training={},
            data={"dataset_name_or_path": "org/dataset"},
        )
        with patch("forgelm.trainer.WebhookNotifier"):
            trainer = ForgeTrainer.__new__(ForgeTrainer)
            trainer.config = config
            trainer.dataset = {"train": []}
            trainer.checkpoint_dir = "/tmp/test"
            trainer.run_name = "test"
            trainer.notifier = MagicMock()
            trainer.audit = MagicMock()

        assert trainer.execute_evaluation_checks("/tmp/test/final", {"eval_loss": 5.0}) is True

    def test_max_loss_exceeded(self):
        trainer = self._make_trainer(max_loss=2.0)
        result = trainer.execute_evaluation_checks("/tmp/nonexistent", {"eval_loss": 3.0})
        assert result is False

    def test_max_loss_within_bounds(self):
        trainer = self._make_trainer(max_loss=2.0)
        result = trainer.execute_evaluation_checks("/tmp/nonexistent", {"eval_loss": 1.5})
        assert result is True

    def test_baseline_regression(self):
        trainer = self._make_trainer(baseline_loss=1.0)
        result = trainer.execute_evaluation_checks("/tmp/nonexistent", {"eval_loss": 1.5})
        assert result is False

    def test_baseline_improvement(self):
        trainer = self._make_trainer(baseline_loss=2.0)
        result = trainer.execute_evaluation_checks("/tmp/nonexistent", {"eval_loss": 1.5})
        assert result is True

    def test_nan_eval_loss(self):
        trainer = self._make_trainer(max_loss=2.0)
        result = trainer.execute_evaluation_checks("/tmp/nonexistent", {"eval_loss": float("nan")})
        assert result is False

    def test_inf_eval_loss(self):
        trainer = self._make_trainer(max_loss=2.0)
        result = trainer.execute_evaluation_checks("/tmp/nonexistent", {"eval_loss": float("inf")})
        assert result is False

    def test_missing_eval_loss(self):
        trainer = self._make_trainer(max_loss=2.0)
        result = trainer.execute_evaluation_checks("/tmp/nonexistent", {"train_loss": 0.5})
        assert result is True  # Skip check when no eval_loss

    def test_no_validation_data(self):
        trainer = self._make_trainer(max_loss=2.0)
        trainer.dataset = {"train": []}  # No validation
        result = trainer.execute_evaluation_checks("/tmp/nonexistent", {"eval_loss": 5.0})
        assert result is True  # Skip when no validation

    def test_auto_revert_disabled(self):
        trainer = self._make_trainer(auto_revert=False, max_loss=0.1)
        result = trainer.execute_evaluation_checks("/tmp/nonexistent", {"eval_loss": 5.0})
        assert result is True  # auto_revert=False means always pass

    def test_auto_revert_disabled_still_detects_breach(self, caplog):
        """F-P3-FABLE-24: with auto_revert=false a configured max_acceptable_loss is
        still EVALUATED — a breach logs a WARNING naming the threshold (detection),
        but the model is kept (no revert, return True)."""
        trainer = self._make_trainer(auto_revert=False, max_loss=0.1)
        with patch.object(trainer, "_revert_model") as revert, caplog.at_level("WARNING"):
            result = trainer.execute_evaluation_checks("/tmp/nonexistent", {"eval_loss": 5.0})
        assert result is True  # detection-only, model kept
        revert.assert_not_called()
        assert "max_acceptable_loss" in caplog.text
        assert "auto_revert=false" in caplog.text

    def test_auto_revert_disabled_detects_nan_divergence(self, caplog):
        """F-P3-FABLE-24: a NaN eval_loss (training diverged) is detected and logged
        even when auto_revert=false; the diverged model is NOT silently shipped with
        no signal (but is also not reverted)."""
        trainer = self._make_trainer(auto_revert=False, max_loss=0.1)
        with patch.object(trainer, "_revert_model") as revert, caplog.at_level("ERROR"):
            result = trainer.execute_evaluation_checks("/tmp/nonexistent", {"eval_loss": float("nan")})
        assert result is True
        revert.assert_not_called()
        assert "diverged" in caplog.text

    def test_auto_revert_disabled_no_threshold_is_silent_passthrough(self):
        """No threshold/baseline + auto_revert=false → nothing to detect, early True."""
        trainer = self._make_trainer(auto_revert=False)
        with patch.object(trainer, "_revert_model") as revert:
            result = trainer.execute_evaluation_checks("/tmp/nonexistent", {"eval_loss": float("nan")})
        assert result is True
        revert.assert_not_called()

    def test_failed_benchmark_gate_when_auto_revert_disabled_continues_recording_failure(self):
        """F-P1-FAB-14: with the shipped default ``auto_revert=False`` a failed
        benchmark gate is *recorded* (``benchmark_passed=False``, scores attached)
        but the pipeline continues — ``_apply_benchmark_result`` returns True, no
        revert, no model deletion. This is the behaviour the corrected
        error-handling.md row 0 / exit-codes.md documents (exit 0 does NOT imply
        every gate passed unless ``auto_revert`` is on)."""
        trainer = self._make_trainer(auto_revert=False)
        train_result = TrainResult(success=True, metrics={}, final_model_path="/tmp/nonexistent/final")
        metrics: dict[str, float] = {}
        failing_benchmark = MagicMock()
        failing_benchmark.passed = False
        failing_benchmark.scores = {"hellaswag": 0.30}
        failing_benchmark.average_score = 0.30
        failing_benchmark.failure_reason = "Benchmark score below threshold."

        with patch.object(trainer, "_revert_model") as revert:
            result = trainer._apply_benchmark_result(failing_benchmark, train_result, metrics, "/tmp/nonexistent/final")

        assert result is True  # continue → run still exits 0
        assert train_result.benchmark_passed is False  # failure recorded
        assert train_result.success is True  # NOT reverted
        assert train_result.reverted is False
        revert.assert_not_called()  # model not destroyed

    def test_failed_benchmark_gate_when_auto_revert_enabled_reverts_and_halts(self):
        """F-P1-FAB-14 counterpart: with ``auto_revert=True`` the SAME failing
        gate reverts the model and halts (returns False → exit 3), so exit 0
        legitimately means every gate passed on the auto_revert path."""
        trainer = self._make_trainer(auto_revert=True)
        train_result = TrainResult(success=True, metrics={}, final_model_path="/tmp/nonexistent/final")
        metrics: dict[str, float] = {}
        failing_benchmark = MagicMock()
        failing_benchmark.passed = False
        failing_benchmark.scores = {"hellaswag": 0.30}
        failing_benchmark.average_score = 0.30
        failing_benchmark.failure_reason = "Benchmark score below threshold."

        with patch.object(trainer, "_revert_model") as revert:
            result = trainer._apply_benchmark_result(failing_benchmark, train_result, metrics, "/tmp/nonexistent/final")

        assert result is False  # halt → exit 3
        assert train_result.benchmark_passed is False
        assert train_result.reverted is True
        revert.assert_called_once()


class TestTrainingArgsValidationGuard:
    """P1-2 regression: when no validation split exists, the training-args
    builder must downshift eval_strategy to ``"no"`` and disable
    load_best_model_at_end / metric_for_best_model.  Otherwise HF Trainer
    refuses to construct with ``eval_strategy="steps"`` + ``eval_dataset=None``.
    """

    def _seed_trainer(self, tmp_path, dataset):
        from forgelm.config import ForgeConfig
        from forgelm.trainer import ForgeTrainer

        config = ForgeConfig(
            **{
                "model": {"name_or_path": "org/model", "max_length": 2048},
                "lora": {},
                "training": {"trainer_type": "sft", "output_dir": str(tmp_path)},
                "data": {"dataset_name_or_path": "org/dataset"},
            }
        )
        trainer = ForgeTrainer.__new__(ForgeTrainer)
        trainer.model = MagicMock()
        trainer.tokenizer = MagicMock()
        trainer.config = config
        trainer.dataset = dataset
        trainer.checkpoint_dir = str(tmp_path)
        trainer.run_name = "training_args_test"
        trainer.notifier = MagicMock()
        trainer.audit = MagicMock()
        return trainer

    def test_validation_present_keeps_eval_strategy(self, tmp_path):
        trainer = self._seed_trainer(tmp_path, {"train": list(range(20)), "validation": list(range(2))})
        kwargs = trainer._get_common_training_kwargs()
        assert kwargs["eval_strategy"] == "steps"
        assert kwargs["load_best_model_at_end"] is True
        assert kwargs["metric_for_best_model"] == "eval_loss"
        assert kwargs["greater_is_better"] is False

    def test_no_validation_downshifts_eval_strategy(self, tmp_path):
        trainer = self._seed_trainer(tmp_path, {"train": list(range(20))})
        kwargs = trainer._get_common_training_kwargs()
        assert kwargs["eval_strategy"] == "no", (
            "HF Trainer rejects eval_strategy != 'no' with eval_dataset=None; "
            "the builder must downshift when no validation split exists"
        )
        assert kwargs["load_best_model_at_end"] is False
        assert kwargs["metric_for_best_model"] is None
        assert kwargs["greater_is_better"] is None

    def test_empty_validation_downshifts_eval_strategy(self, tmp_path):
        """Empty list counts as no validation — bool(self.dataset.get('validation')) is False."""
        trainer = self._seed_trainer(tmp_path, {"train": list(range(20)), "validation": []})
        kwargs = trainer._get_common_training_kwargs()
        assert kwargs["eval_strategy"] == "no"
        assert kwargs["load_best_model_at_end"] is False


@pytest.mark.skipif(not torch_available, reason="torch not installed")
class TestGateRunnerImportContract:
    """F-P3-FABLE-25: configured eval gates must fail fast / fail loud on a missing
    extra, never silently degrade to a skip with exit 0."""

    def _seed(self, tmp_path, eval_overrides):
        from forgelm.config import ForgeConfig
        from forgelm.trainer import ForgeTrainer

        config = ForgeConfig(
            model={"name_or_path": "org/model"},
            lora={},
            training={"output_dir": str(tmp_path)},
            data={"dataset_name_or_path": "org/dataset"},
            evaluation=eval_overrides,
        )
        trainer = ForgeTrainer.__new__(ForgeTrainer)
        trainer.config = config
        trainer.dataset = {"train": list(range(10)), "validation": list(range(2))}
        trainer.checkpoint_dir = str(tmp_path)
        trainer.run_name = "gate_runner_test"
        trainer.notifier = MagicMock()
        trainer.audit = MagicMock()
        trainer.model = MagicMock()
        trainer.tokenizer = MagicMock()
        trainer.trainer = MagicMock()
        return trainer

    def test_benchmark_enabled_without_lm_eval_fails_at_preflight(self, tmp_path, monkeypatch):
        """A benchmark gate enabled without lm-eval raises ImportError (with the
        install hint) at the config-validation preflight — BEFORE training — rather
        than after a full run as exit 2."""
        trainer = self._seed(
            tmp_path,
            {"benchmark": {"enabled": True, "tasks": ["arc_easy"], "min_score": 0.5}},
        )
        monkeypatch.delitem(__import__("sys").modules, "lm_eval", raising=False)

        def _raise():
            raise ImportError(
                "lm-evaluation-harness is required for benchmarking but not installed. "
                "Install it with: pip install forgelm[eval]"
            )

        monkeypatch.setattr("forgelm.benchmark._check_lm_eval_available", _raise)
        with pytest.raises(ImportError, match="forgelm\\[eval\\]"):
            trainer._validate_evaluation_config()

    def test_run_benchmark_does_not_swallow_importerror_into_none(self, tmp_path, monkeypatch):
        """The benchmark runner re-raises a real ImportError with the install hint
        instead of returning None (which would silently skip the gate)."""
        trainer = self._seed(
            tmp_path,
            {"benchmark": {"enabled": True, "tasks": ["arc_easy"], "min_score": 0.5}},
        )

        import forgelm.benchmark as _bm

        monkeypatch.setattr(_bm, "run_benchmark", None, raising=False)
        # Force the local import inside _run_benchmark_if_configured to raise.
        monkeypatch.delattr(_bm, "run_benchmark")
        with pytest.raises(ImportError, match="forgelm\\[eval\\]"):
            trainer._run_benchmark_if_configured()


@pytest.mark.skipif(not torch_available, reason="torch not installed")
class TestGateApplication:
    """F-P2-FAB-16: trainer-side gate application + revert/continue matrix."""

    def _make_trainer(self, auto_revert):
        from forgelm.config import ForgeConfig
        from forgelm.trainer import ForgeTrainer

        config = ForgeConfig(
            model={"name_or_path": "org/model"},
            lora={},
            training={"output_dir": "/tmp/test_forge_gate"},
            data={"dataset_name_or_path": "org/dataset"},
            evaluation={"auto_revert": auto_revert},
        )
        with patch("forgelm.trainer.WebhookNotifier"):
            trainer = ForgeTrainer.__new__(ForgeTrainer)
            trainer.config = config
            trainer.dataset = {"train": ["x"], "validation": ["y"]}
            trainer.checkpoint_dir = "/tmp/test_forge_gate"
            trainer.run_name = "gate_apply"
            trainer.notifier = MagicMock()
            trainer.audit = MagicMock()
        return trainer

    def test_safety_fail_with_auto_revert_reverts_and_marks_result(self):
        trainer = self._make_trainer(auto_revert=True)
        result = TrainResult(success=True, metrics={}, final_model_path="/tmp/x/final")
        safety = MagicMock(
            passed=False,
            safety_score=0.4,
            safe_ratio=0.4,
            total_count=10,
            category_distribution={},
            severity_distribution={},
            low_confidence_count=0,
            failure_reason="unsafe ratio too high",
        )
        with patch.object(trainer, "_revert_model") as revert:
            cont = trainer._apply_safety_result(safety, result, {}, "/tmp/x/final")
        assert cont is False  # halt → exit 3
        assert result.reverted is True
        assert result.staging_path is None  # cleared by _mark_reverted
        revert.assert_called_once()

    def test_safety_infra_failure_audit_payload_does_not_report_perfect_ratio(self):
        """F-P3-FABLE-26 trainer-side: an infra-failure SafetyResult (safe_ratio=0.0,
        total_count=0) must not surface a 1.0 metric / audit payload."""
        trainer = self._make_trainer(auto_revert=False)
        result = TrainResult(success=True, metrics={}, final_model_path="/tmp/x/final")
        metrics: dict[str, float] = {}
        from forgelm.safety import SafetyResult

        infra_fail = SafetyResult(passed=False, evaluation_completed=False, safe_ratio=0.0)
        cont = trainer._apply_safety_result(infra_fail, result, metrics, "/tmp/x/final")
        assert cont is True  # recorded, not reverted (auto_revert off)
        assert metrics["safety/safe_ratio"] == 0.0
        audit_kwargs = trainer.audit.log_event.call_args.kwargs
        assert audit_kwargs["safe_ratio"] == 0.0
        assert audit_kwargs["total_count"] == 0

    def test_judge_fail_with_auto_revert_reverts(self):
        trainer = self._make_trainer(auto_revert=True)
        result = TrainResult(success=True, metrics={}, final_model_path="/tmp/x/final")
        judge = MagicMock(passed=False, average_score=2.0, details=[], failure_reason="below min_score")
        with patch.object(trainer, "_revert_model") as revert:
            cont = trainer._apply_judge_result(judge, result, {}, "/tmp/x/final")
        assert cont is False
        assert result.reverted is True
        revert.assert_called_once()

    def test_judge_fail_without_auto_revert_records_but_continues(self):
        trainer = self._make_trainer(auto_revert=False)
        result = TrainResult(success=True, metrics={}, final_model_path="/tmp/x/final")
        metrics: dict[str, float] = {}
        judge = MagicMock(passed=False, average_score=2.0, details=[], failure_reason="below min_score")
        with patch.object(trainer, "_revert_model") as revert:
            cont = trainer._apply_judge_result(judge, result, metrics, "/tmp/x/final")
        assert cont is True
        assert result.judge_score == 2.0
        assert result.reverted is False
        revert.assert_not_called()

    def test_none_gate_results_are_noops(self):
        trainer = self._make_trainer(auto_revert=True)
        result = TrainResult(success=True, metrics={})
        assert trainer._apply_safety_result(None, result, {}, "/tmp/x") is True
        assert trainer._apply_judge_result(None, result, {}, "/tmp/x") is True
        assert trainer._apply_benchmark_result(None, result, {}, "/tmp/x") is True
        trainer.audit.log_event.assert_not_called()


@pytest.mark.skipif(not torch_available, reason="torch not installed")
class TestBaselineLossCapture:
    """F-P2-FAB-17: _measure_baseline_loss gating + happy / fallback / missing paths."""

    def _make_trainer(self, *, auto_revert=True, baseline_loss=None, validation=True):
        from forgelm.config import ForgeConfig
        from forgelm.trainer import ForgeTrainer

        config = ForgeConfig(
            model={"name_or_path": "org/model"},
            lora={},
            training={"output_dir": "/tmp/test_baseline"},
            data={"dataset_name_or_path": "org/dataset"},
            evaluation={"auto_revert": auto_revert, "baseline_loss": baseline_loss},
        )
        dataset = {"train": ["x"], "validation": ["y"]} if validation else {"train": ["x"]}
        with patch("forgelm.trainer.WebhookNotifier"):
            trainer = ForgeTrainer.__new__(ForgeTrainer)
            trainer.config = config
            trainer.dataset = dataset
            trainer.checkpoint_dir = "/tmp/test_baseline"
            trainer.run_name = "baseline"
            trainer.notifier = MagicMock()
            trainer.audit = MagicMock()
            trainer.trainer = MagicMock()
        return trainer

    def test_baseline_captured_and_armed(self):
        trainer = self._make_trainer()
        # model without disable_adapter → plain evaluate() path
        model_obj = MagicMock(spec=[])  # no disable_adapter attr
        trainer.trainer.model = model_obj
        trainer.trainer.evaluate = MagicMock(return_value={"eval_loss": 1.25})
        metrics: dict[str, float] = {}
        trainer._measure_baseline_loss(metrics)
        assert trainer.config.evaluation.baseline_loss == pytest.approx(1.25)
        assert metrics["baseline_eval_loss"] == pytest.approx(1.25)

    def test_baseline_skipped_when_no_auto_revert(self):
        trainer = self._make_trainer(auto_revert=False)
        trainer.trainer.evaluate = MagicMock(return_value={"eval_loss": 1.25})
        metrics: dict[str, float] = {}
        trainer._measure_baseline_loss(metrics)
        # Gating condition not met → no evaluate, no mutation.
        trainer.trainer.evaluate.assert_not_called()
        assert "baseline_eval_loss" not in metrics

    def test_baseline_skipped_when_already_configured(self):
        trainer = self._make_trainer(baseline_loss=0.9)
        trainer.trainer.evaluate = MagicMock(return_value={"eval_loss": 1.25})
        trainer._measure_baseline_loss({})
        trainer.trainer.evaluate.assert_not_called()
        assert trainer.config.evaluation.baseline_loss == pytest.approx(0.9)

    def test_baseline_missing_eval_loss_does_not_arm_gate(self):
        trainer = self._make_trainer()
        model_obj = MagicMock(spec=[])
        trainer.trainer.model = model_obj
        trainer.trainer.evaluate = MagicMock(return_value={"something_else": 1.0})
        trainer._measure_baseline_loss({})
        assert trainer.config.evaluation.baseline_loss is None

    def test_baseline_disable_adapter_fallback_used_on_error(self):
        trainer = self._make_trainer()

        class _Model:
            def disable_adapter(self):
                raise RuntimeError("adapter graph locked")

        trainer.trainer.model = _Model()
        trainer.trainer.evaluate = MagicMock(return_value={"eval_loss": 0.8})
        trainer._measure_baseline_loss({})
        # Fallback evaluate() (with adapters) supplied the baseline.
        assert trainer.config.evaluation.baseline_loss == pytest.approx(0.8)
