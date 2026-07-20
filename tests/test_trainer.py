"""Unit tests for forgelm.trainer module (non-GPU tests only)."""

import os
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

    def test_nan_baseline_config_is_ignored(self, caplog):
        """A config-supplied NaN baseline_loss must be silently discarded so it
        cannot covertly disable the regression check.  The guard must log a
        WARNING mentioning 'NaN or Inf' and the call must return True (no revert)
        because the baseline regression gate is disarmed, not triggered."""
        trainer = self._make_trainer(auto_revert=True, baseline_loss=float("nan"))
        with patch.object(trainer, "_revert_model") as revert, caplog.at_level("WARNING"):
            result = trainer.execute_evaluation_checks("/tmp/nonexistent", {"eval_loss": 1.5})
        assert result is True
        revert.assert_not_called()
        assert any("NaN or Inf" in r.getMessage() for r in caplog.records)

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

    def _loss_gate_calls(self, trainer):
        from forgelm.trainer import _EVT_LOSS_GATE_COMPLETED

        return [c for c in trainer.audit.log_event.call_args_list if c.args and c.args[0] == _EVT_LOSS_GATE_COMPLETED]

    def test_loss_gate_emits_evaluation_completed_on_pass(self):
        """F-P4-OPUS-26: a passing loss gate emits a discrete decision event
        carrying ``passed=True`` and the thresholds it was checked against —
        symmetric with the benchmark/safety/judge gates."""
        trainer = self._make_trainer(max_loss=2.0, baseline_loss=3.0)
        assert trainer.execute_evaluation_checks("/tmp/nonexistent", {"eval_loss": 1.5}) is True
        calls = self._loss_gate_calls(trainer)
        assert len(calls) == 1
        kwargs = calls[0].kwargs
        assert kwargs["passed"] is True
        assert kwargs["eval_loss"] == pytest.approx(1.5)
        assert kwargs["max_acceptable_loss"] == pytest.approx(2.0)
        assert kwargs["baseline_loss"] == pytest.approx(3.0)

    def test_loss_gate_emits_evaluation_completed_on_fail(self):
        """F-P4-OPUS-26: a threshold breach emits the same decision event with
        ``passed=False`` before the revert, so the accept/reject record exists
        independently of ``model.reverted``."""
        trainer = self._make_trainer(max_loss=2.0)
        assert trainer.execute_evaluation_checks("/tmp/nonexistent", {"eval_loss": 3.0}) is False
        calls = self._loss_gate_calls(trainer)
        assert len(calls) == 1
        assert calls[0].kwargs["passed"] is False
        assert calls[0].kwargs["eval_loss"] == pytest.approx(3.0)

    def test_loss_gate_event_eval_loss_non_finite_is_stringified(self):
        """A NaN/Inf divergence records eval_loss as a string sentinel so the
        audit JSONL stays valid JSON (F-P4-OPUS-26)."""
        trainer = self._make_trainer(max_loss=2.0)
        trainer.execute_evaluation_checks("/tmp/nonexistent", {"eval_loss": float("nan")})
        calls = self._loss_gate_calls(trainer)
        assert len(calls) == 1
        assert calls[0].kwargs["passed"] is False
        assert isinstance(calls[0].kwargs["eval_loss"], str)  # "nan", not a bare float

    def test_revert_emits_audit_event_before_rmtree(self, tmp_path):
        """F-P4-OPUS-27: ``_revert_model`` must emit ``model.reverted`` BEFORE the
        destructive ``shutil.rmtree`` so the record survives even if the delete
        explodes. Patch rmtree to raise and assert the emit still happened."""
        from forgelm.trainer import _EVT_REVERT_TRIGGERED

        trainer = self._make_trainer(max_loss=2.0)
        final_path = tmp_path / "final"
        final_path.mkdir()
        (final_path / "adapter.bin").write_text("weights")

        with patch("forgelm.trainer.shutil.rmtree", side_effect=OSError("disk gone")) as rmtree:
            # The OSError is caught inside _revert_model (logged, non-fatal) —
            # the emit must have already run.
            trainer._revert_model(str(final_path), "boom", source="threshold")

        rmtree.assert_called_once()
        revert_calls = [
            c for c in trainer.audit.log_event.call_args_list if c.args and c.args[0] == _EVT_REVERT_TRIGGERED
        ]
        assert len(revert_calls) == 1, "model.reverted must be emitted exactly once, even when rmtree fails"

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

    @pytest.mark.parametrize("gate", ["benchmark", "safety", "judge"])
    def test_gate_failure_without_auto_revert_logs_warning(self, caplog, gate):
        """F-P3-FABLE-49: a gate that fails while ``auto_revert=false`` keeps the
        model, so the operator must see a WARNING connecting the ERROR-level gate
        failure to the subsequent exit 0 — otherwise the auto_revert=false
        rationale has to be reverse-engineered from the config."""
        import logging

        trainer = self._make_trainer(auto_revert=False)
        train_result = TrainResult(success=True, metrics={}, final_model_path="/tmp/nonexistent/final")
        metrics: dict[str, float] = {}

        failing = MagicMock()
        failing.passed = False
        if gate == "benchmark":
            failing.scores = {"hellaswag": 0.30}
            failing.average_score = 0.30
            failing.failure_reason = "Benchmark score below threshold."
            apply = trainer._apply_benchmark_result
        elif gate == "safety":
            failing.safety_score = 0.10
            failing.safe_ratio = 0.10
            failing.category_distribution = {}
            failing.severity_distribution = {}
            failing.low_confidence_count = 0
            failing.total_count = 5
            failing.failure_reason = "Safety check failed."
            apply = trainer._apply_safety_result
        else:
            failing.average_score = 2.0
            failing.details = []
            failing.failure_reason = "Judge score below threshold."
            apply = trainer._apply_judge_result

        with caplog.at_level(logging.WARNING, logger="forgelm.trainer"):
            result = apply(failing, train_result, metrics, "/tmp/nonexistent/final")

        assert result is True  # model kept, run continues
        warnings = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING and gate in r.getMessage() and "auto_revert=false" in r.getMessage()
        ]
        assert len(warnings) == 1, f"expected one '{gate}' kept-no-revert WARNING"
        assert train_result.error == failing.failure_reason


@pytest.mark.skipif(not torch_available, reason="torch not installed")
class TestGovernanceSectionMissingEvent:
    """F-P4-OPUS-23: when data_audit_report.json is absent the governance bundle
    silently drops the Article 10 data-quality section. The append-only log must
    record that gap with a discrete ``compliance.governance_section_missing``
    event, not only an ephemeral WARNING."""

    def _make_trainer(self, tmp_path):
        from forgelm.config import ForgeConfig
        from forgelm.trainer import ForgeTrainer

        config = ForgeConfig(
            model={"name_or_path": "org/model"},
            lora={},
            training={"output_dir": str(tmp_path)},
            data={"dataset_name_or_path": "org/dataset"},
            evaluation={"auto_revert": False},
        )
        with patch("forgelm.trainer.WebhookNotifier"):
            trainer = ForgeTrainer.__new__(ForgeTrainer)
            trainer.config = config
            trainer.dataset = {"train": ["x"], "validation": ["y"]}
            trainer.checkpoint_dir = str(tmp_path)
            trainer.run_name = "test"
            trainer.notifier = MagicMock()
            trainer.audit = MagicMock()
            trainer.audit.run_id = "fg-test"
        return trainer

    def _event_names(self, trainer):
        return [c.args[0] for c in trainer.audit.log_event.call_args_list if c.args]

    def _export_stub(self, tmp_path):
        """export_compliance_artifacts normally creates ``compliance/``; the
        governance writer relies on it existing, so emulate that side effect."""

        def _stub(manifest, compliance_dir):
            os.makedirs(compliance_dir, exist_ok=True)

        return _stub

    def test_missing_data_audit_emits_section_missing_event(self, tmp_path):
        trainer = self._make_trainer(tmp_path)  # no data_audit_report.json on disk
        result = TrainResult(success=True, metrics={}, final_model_path=str(tmp_path / "final"))
        # Keep the heavy manifest machinery out of the way; only the governance
        # branch is under test.
        with (
            patch("forgelm.compliance.generate_training_manifest", return_value={"x": 1}),
            patch("forgelm.compliance.export_compliance_artifacts", side_effect=self._export_stub(tmp_path)),
        ):
            trainer._export_compliance_if_needed({"eval_loss": 1.0}, result)

        names = self._event_names(trainer)
        assert "compliance.governance_exported" in names
        assert "compliance.governance_section_missing" in names

    def test_present_data_audit_does_not_emit_section_missing(self, tmp_path):
        import json as _json

        (tmp_path / "data_audit_report.json").write_text(
            _json.dumps({"total_samples": 5, "pii_summary": {}}), encoding="utf-8"
        )
        trainer = self._make_trainer(tmp_path)
        result = TrainResult(success=True, metrics={}, final_model_path=str(tmp_path / "final"))
        with (
            patch("forgelm.compliance.generate_training_manifest", return_value={"x": 1}),
            patch("forgelm.compliance.export_compliance_artifacts", side_effect=self._export_stub(tmp_path)),
        ):
            trainer._export_compliance_if_needed({"eval_loss": 1.0}, result)

        names = self._event_names(trainer)
        assert "compliance.governance_exported" in names
        assert "compliance.governance_section_missing" not in names


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

    def _make_trainer(self, auto_revert, tmp_path):
        from forgelm.config import ForgeConfig
        from forgelm.trainer import ForgeTrainer

        config = ForgeConfig(
            model={"name_or_path": "org/model"},
            lora={},
            training={"output_dir": str(tmp_path)},
            data={"dataset_name_or_path": "org/dataset"},
            evaluation={"auto_revert": auto_revert},
        )
        with patch("forgelm.trainer.WebhookNotifier"):
            trainer = ForgeTrainer.__new__(ForgeTrainer)
            trainer.config = config
            trainer.dataset = {"train": ["x"], "validation": ["y"]}
            trainer.checkpoint_dir = str(tmp_path)
            trainer.run_name = "gate_apply"
            trainer.notifier = MagicMock()
            trainer.audit = MagicMock()
        return trainer

    def test_safety_fail_with_auto_revert_reverts_and_marks_result(self, tmp_path):
        trainer = self._make_trainer(auto_revert=True, tmp_path=tmp_path)
        result = TrainResult(success=True, metrics={}, final_model_path=str(tmp_path / "final"))
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
            cont = trainer._apply_safety_result(safety, result, {}, str(tmp_path / "final"))
        assert cont is False  # halt → exit 3
        assert result.reverted is True
        assert result.staging_path is None  # cleared by _mark_reverted
        revert.assert_called_once()

    def test_safety_infra_failure_with_auto_revert_does_not_revert_model(self, tmp_path):
        """Infrastructure safety failures (evaluation_completed=False) must never
        trigger auto-revert even when auto_revert=True.  A classifier that fails
        to load is an infra misconfiguration, not a genuine gate failure — deleting
        a successfully trained model over it would be wrong."""
        trainer = self._make_trainer(auto_revert=True, tmp_path=tmp_path)
        result = TrainResult(success=True, metrics={}, final_model_path=str(tmp_path / "final"))
        metrics: dict[str, float] = {}
        from forgelm.safety import SafetyResult

        infra_fail = SafetyResult(passed=False, evaluation_completed=False, safe_ratio=0.0)
        with patch.object(trainer, "_revert_model") as revert:
            cont = trainer._apply_safety_result(infra_fail, result, metrics, str(tmp_path / "final"))
        assert cont is True  # pipeline continues — infra failure, not gate failure
        revert.assert_not_called()  # model must not be deleted
        assert result.reverted is False

    def test_safety_infra_failure_audit_payload_does_not_report_perfect_ratio(self, tmp_path):
        """F-P3-FABLE-26 trainer-side: an infra-failure SafetyResult (safe_ratio=0.0,
        total_count=0) must not surface a 1.0 metric / audit payload."""
        trainer = self._make_trainer(auto_revert=False, tmp_path=tmp_path)
        result = TrainResult(success=True, metrics={}, final_model_path=str(tmp_path / "final"))
        metrics: dict[str, float] = {}
        from forgelm.safety import SafetyResult

        infra_fail = SafetyResult(passed=False, evaluation_completed=False, safe_ratio=0.0)
        cont = trainer._apply_safety_result(infra_fail, result, metrics, str(tmp_path / "final"))
        assert cont is True  # recorded, not reverted (auto_revert off)
        assert metrics["safety/safe_ratio"] == 0.0
        audit_kwargs = trainer.audit.log_event.call_args.kwargs
        assert audit_kwargs["safe_ratio"] == 0.0
        assert audit_kwargs["total_count"] == 0

    def test_judge_fail_with_auto_revert_reverts(self, tmp_path):
        trainer = self._make_trainer(auto_revert=True, tmp_path=tmp_path)
        result = TrainResult(success=True, metrics={}, final_model_path=str(tmp_path / "final"))
        judge = MagicMock(passed=False, average_score=2.0, details=[], failure_reason="below min_score")
        with patch.object(trainer, "_revert_model") as revert:
            cont = trainer._apply_judge_result(judge, result, {}, str(tmp_path / "final"))
        assert cont is False
        assert result.reverted is True
        revert.assert_called_once()

    def test_judge_fail_without_auto_revert_records_but_continues(self, tmp_path):
        trainer = self._make_trainer(auto_revert=False, tmp_path=tmp_path)
        result = TrainResult(success=True, metrics={}, final_model_path=str(tmp_path / "final"))
        metrics: dict[str, float] = {}
        judge = MagicMock(passed=False, average_score=2.0, details=[], failure_reason="below min_score")
        with patch.object(trainer, "_revert_model") as revert:
            cont = trainer._apply_judge_result(judge, result, metrics, str(tmp_path / "final"))
        assert cont is True
        assert result.judge_score == 2.0
        assert result.reverted is False
        revert.assert_not_called()

    def test_none_gate_results_are_noops(self, tmp_path):
        trainer = self._make_trainer(auto_revert=True, tmp_path=tmp_path)
        result = TrainResult(success=True, metrics={})
        assert trainer._apply_safety_result(None, result, {}, str(tmp_path)) is True
        assert trainer._apply_judge_result(None, result, {}, str(tmp_path)) is True
        assert trainer._apply_benchmark_result(None, result, {}, str(tmp_path)) is True
        trainer.audit.log_event.assert_not_called()


@pytest.mark.skipif(not torch_available, reason="torch not installed")
class TestUnscoredVerdictsDoNotDestroyTheModel:
    """End-to-end: real ``run_safety_evaluation`` → real ``_apply_safety_result``.

    No mocked SafetyResult anywhere in this class.  A hand-built result proves
    the trainer honours ``evaluation_completed``; it proves nothing about
    whether the safety package sets it on the run that actually matters, and
    the seam between the two is exactly where this defect lived: the package
    reported a plain gate failure for six malformed verdicts, and the trainer
    dutifully deleted a model nothing had ever measured as unsafe.

    ``_revert_model`` is left unpatched and run against a real directory, so
    "the model survived" is asserted against the filesystem rather than
    against a mock's call count.
    """

    def _trainer(self, tmp_path, auto_revert=True):
        from forgelm.config import ForgeConfig
        from forgelm.trainer import ForgeTrainer

        config = ForgeConfig(
            model={"name_or_path": "org/model"},
            lora={},
            training={"output_dir": str(tmp_path)},
            data={"dataset_name_or_path": "org/dataset"},
            evaluation={"auto_revert": auto_revert},
        )
        trainer = ForgeTrainer.__new__(ForgeTrainer)
        trainer.config = config
        trainer.dataset = {"train": ["x"], "validation": ["y"]}
        trainer.checkpoint_dir = str(tmp_path)
        trainer.run_name = "unscored_e2e"
        trainer.notifier = MagicMock()
        trainer.audit = MagicMock()
        return trainer

    def _evaluate(self, tmp_path, monkeypatch, verdicts):
        """Run the real safety pass over a scripted sequence of guard verdicts."""
        import json

        from forgelm import safety as _safety

        n = len(verdicts)
        probes = tmp_path / "probes.jsonl"
        probes.write_text("".join(json.dumps({"prompt": f"probe {i}"}) + "\n" for i in range(n)))
        monkeypatch.setattr(_safety._orchestrator, "_generate_safety_responses", lambda *a, **k: ["resp"] * n)
        monkeypatch.setattr(_safety._orchestrator, "_release_model_from_gpu", lambda *a, **k: None)
        monkeypatch.setattr(
            _safety._score_generation, "_load_generative_guard", lambda *a, **k: (MagicMock(), MagicMock())
        )
        script = iter(verdicts)
        monkeypatch.setattr(_safety._score_generation, "_generate_guard_verdict", lambda *a, **k: next(script))
        return _safety.run_safety_evaluation(
            model=MagicMock(),
            tokenizer=MagicMock(),
            classifier_path="meta-llama/Llama-Guard-3-8B",
            test_prompts_path=str(probes),
            output_dir=str(tmp_path / "out"),
        )

    def _final_model(self, tmp_path):
        final = tmp_path / "final_model"
        final.mkdir()
        (final / "adapter_model.safetensors").write_text("weights")
        return final

    def test_six_hiccups_in_a_hundred_do_not_delete_the_model(self, tmp_path, monkeypatch):
        # The reproduction: an ordinary CUDA OOM / off-protocol reply rate of
        # 6% clears the default max_safety_regression of 0.05 on its own.
        safety = self._evaluate(tmp_path, monkeypatch, ["safe"] * 94 + [""] * 6)
        trainer = self._trainer(tmp_path, auto_revert=True)
        final = self._final_model(tmp_path)
        result = TrainResult(success=True, metrics={}, final_model_path=str(final))

        cont = trainer._apply_safety_result(safety, result, {}, str(final))

        assert final.exists(), "a run with no scored-unsafe verdict must not lose its model"
        assert (final / "adapter_model.safetensors").exists()
        assert cont is True, "pipeline continues — no usable evidence, not a measured failure"
        assert result.reverted is False
        # Failed, and recorded as failed. Withheld revert is not a pass.
        assert result.safety_passed is False
        assert result.error and "NOT auto-reverted" in result.error

    def test_genuinely_unsafe_model_is_still_deleted(self, tmp_path, monkeypatch):
        # The non-negotiable direction. Well-formed unsafe verdicts are a
        # measurement, and auto-revert must fire on them exactly as before.
        safety = self._evaluate(tmp_path, monkeypatch, ["unsafe\nS1"] * 40 + ["safe"] * 60)
        trainer = self._trainer(tmp_path, auto_revert=True)
        final = self._final_model(tmp_path)
        result = TrainResult(success=True, metrics={}, final_model_path=str(final))

        cont = trainer._apply_safety_result(safety, result, {}, str(final))

        assert not final.exists(), "a measured unsafe model must still be reverted"
        assert cont is False, "halt → exit 3"
        assert result.reverted is True

    def test_mixed_run_failing_on_scored_evidence_alone_still_reverts(self, tmp_path, monkeypatch):
        # Unscored rows present, but the scored rows breach the gate without
        # them. Evidence of harm exists, so revert proceeds.
        safety = self._evaluate(tmp_path, monkeypatch, ["unsafe\nS1"] * 20 + [""] * 6 + ["safe"] * 74)
        trainer = self._trainer(tmp_path, auto_revert=True)
        final = self._final_model(tmp_path)
        result = TrainResult(success=True, metrics={}, final_model_path=str(final))

        cont = trainer._apply_safety_result(safety, result, {}, str(final))

        assert not final.exists()
        assert cont is False
        assert result.reverted is True

    def test_wholly_non_guard_checkpoint_still_abstains(self, tmp_path, monkeypatch):
        # The Step-7 case, re-pinned through the trainer rather than a mock.
        safety = self._evaluate(tmp_path, monkeypatch, ["I'm sorry, I can't help with that."] * 10)
        trainer = self._trainer(tmp_path, auto_revert=True)
        final = self._final_model(tmp_path)
        result = TrainResult(success=True, metrics={}, final_model_path=str(final))

        cont = trainer._apply_safety_result(safety, result, {}, str(final))

        assert final.exists()
        assert cont is True
        assert result.reverted is False
        assert "could not be performed" in result.error

    def test_kept_model_log_does_not_blame_auto_revert(self, tmp_path, monkeypatch, caplog):
        # The operator has auto_revert=true. Telling them the model was kept
        # "because auto_revert=false" is a plain lie about their own config, on
        # the one path where they most need to trust the log.
        import logging

        safety = self._evaluate(tmp_path, monkeypatch, ["safe"] * 94 + [""] * 6)
        trainer = self._trainer(tmp_path, auto_revert=True)
        final = self._final_model(tmp_path)
        result = TrainResult(success=True, metrics={}, final_model_path=str(final))

        with caplog.at_level(logging.WARNING, logger="forgelm.trainer"):
            trainer._apply_safety_result(safety, result, {}, str(final))

        kept_lines = [r.getMessage() for r in caplog.records if "keeping model" in r.getMessage()]
        assert kept_lines, "the kept-model line must still be emitted"
        assert not any("auto_revert=false" in line for line in kept_lines)
        assert any("no usable evidence" in line for line in kept_lines)

    def test_audit_payload_separates_scored_from_unscored(self, tmp_path, monkeypatch):
        # EU AI Act Art. 12: the append-only record has to let an auditor tell
        # a measured safety failure from one the classifier never answered.
        safety = self._evaluate(tmp_path, monkeypatch, ["unsafe\nS1"] * 20 + [""] * 4 + ["safe"] * 76)
        trainer = self._trainer(tmp_path, auto_revert=False)
        final = self._final_model(tmp_path)
        result = TrainResult(success=True, metrics={}, final_model_path=str(final))

        trainer._apply_safety_result(safety, result, {}, str(final))

        payload = next(
            call.kwargs
            for call in trainer.audit.log_event.call_args_list
            if call.args and call.args[0] == "safety.evaluation_completed"
        )
        assert payload["passed"] is False
        assert payload["evaluation_completed"] is True  # scored rows fail on their own
        assert payload["scored_unsafe_count"] == 20
        assert payload["unscored_count"] == 4
        assert payload["total_count"] == 100

    def test_audit_payload_marks_the_withheld_run(self, tmp_path, monkeypatch):
        safety = self._evaluate(tmp_path, monkeypatch, ["safe"] * 94 + [""] * 6)
        trainer = self._trainer(tmp_path, auto_revert=True)
        final = self._final_model(tmp_path)
        result = TrainResult(success=True, metrics={}, final_model_path=str(final))

        trainer._apply_safety_result(safety, result, {}, str(final))

        payload = next(
            call.kwargs
            for call in trainer.audit.log_event.call_args_list
            if call.args and call.args[0] == "safety.evaluation_completed"
        )
        assert payload["evaluation_completed"] is False
        assert payload["scored_unsafe_count"] == 0
        assert payload["unscored_count"] == 6


class TestBaselineLossCapture:
    """F-P2-FAB-17: _measure_baseline_loss gating + happy / fallback / missing paths."""

    def _make_trainer(self, *, auto_revert=True, baseline_loss=None, validation=True, trainer_type="sft"):
        from forgelm.config import ForgeConfig
        from forgelm.trainer import ForgeTrainer

        config = ForgeConfig(
            model={"name_or_path": "org/model"},
            lora={},
            training={"output_dir": "/tmp/test_baseline", "trainer_type": trainer_type},
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

    def test_baseline_skipped_for_grpo_even_with_validation_split(self):
        """GRPO builds its trainer with no eval_dataset, so calling
        ``self.trainer.evaluate()`` for a baseline would raise ValueError. The
        baseline measurement must be skipped entirely for GRPO — even on the
        default path where a validation split exists and auto_revert is on."""
        trainer = self._make_trainer(trainer_type="grpo")
        trainer.trainer.model = MagicMock(spec=[])
        trainer.trainer.evaluate = MagicMock(side_effect=ValueError("Trainer: evaluation requires an eval_dataset."))
        metrics: dict[str, float] = {}
        trainer._measure_baseline_loss(metrics)
        trainer.trainer.evaluate.assert_not_called()
        assert trainer.config.evaluation.baseline_loss is None
        assert "baseline_eval_loss" not in metrics


@pytest.mark.skipif(not torch_available, reason="torch not installed")
class TestClassifierRewardDtype:
    """The GRPO classifier reward model loads at a resolved compute dtype
    (bf16 if supported, else fp16) ONLY on a CUDA host, so it does not OOM
    beside a 4-bit policy model. On a CPU-only host it must fall back to
    float32 (the checkpoint default): `_resolve_bnb_compute_dtype("auto")`
    still resolves to float16 with no CUDA device, and `device_map="auto"`
    places the model on CPU — an fp16 matmul on CPU is not implemented by
    PyTorch (`addmm_impl_cpu_` RuntimeError), which crashed the CPU-only
    GRPO reward path before this fix."""

    def test_reward_model_loaded_with_resolved_dtype_on_cuda(self):
        import torch

        from forgelm.trainer import ForgeTrainer

        captured: dict = {}

        def fake_model_from_pretrained(_path, **kwargs):
            captured.update(kwargs)
            return MagicMock()

        # `_resolve_bnb_compute_dtype` itself queries
        # `torch.cuda.is_bf16_supported()`, which on a real CUDA-less test
        # runner would try to touch an actual device once `is_available()`
        # is patched True. Stub the resolver directly (like the export.py
        # dispatcher tests stub `forgelm.export.export_model`) so this test
        # only exercises the CUDA-vs-CPU branch under test, not torch's own
        # device-query internals.
        with (
            patch("torch.cuda.is_available", return_value=True),
            patch("forgelm.model._resolve_bnb_compute_dtype", return_value=torch.bfloat16) as resolve_mock,
            patch(
                "transformers.AutoModelForSequenceClassification.from_pretrained",
                side_effect=fake_model_from_pretrained,
            ),
            patch("transformers.AutoTokenizer.from_pretrained", return_value=MagicMock()),
            # The reward load now resolves a revision first; stub the resolver
            # so this dtype test cannot reach the Hub.
            patch("forgelm.compliance.resolve_model_revision", return_value={"repo_id": "org/reward"}),
        ):
            ForgeTrainer._build_classifier_reward("org/reward")

        resolve_mock.assert_called_once_with("auto")
        assert captured.get("dtype") == torch.bfloat16, (
            f"reward-model dtype on a CUDA host must be the resolved compute dtype; got {captured.get('dtype')!r}"
        )

    def test_reward_model_falls_back_to_float32_without_cuda(self):
        """Regression: forcing the resolved bf16/fp16 dtype unconditionally
        crashes the CPU-only reward path; a CPU-only host must get float32."""
        import torch

        from forgelm.trainer import ForgeTrainer

        captured: dict = {}

        def fake_model_from_pretrained(_path, **kwargs):
            captured.update(kwargs)
            return MagicMock()

        with (
            patch("torch.cuda.is_available", return_value=False),
            patch(
                "transformers.AutoModelForSequenceClassification.from_pretrained",
                side_effect=fake_model_from_pretrained,
            ),
            patch("transformers.AutoTokenizer.from_pretrained", return_value=MagicMock()),
            # The reward load now resolves a revision first; stub the resolver
            # so this dtype test cannot reach the Hub.
            patch("forgelm.compliance.resolve_model_revision", return_value={"repo_id": "org/reward"}),
        ):
            ForgeTrainer._build_classifier_reward("org/reward")

        assert captured.get("dtype") == torch.float32, (
            f"reward-model dtype on a CPU-only host must fall back to float32; got {captured.get('dtype')!r}"
        )


class TestSaveFinalModelFallback:
    """F-P2-FAB-39: save_final_model's narrow-tuple fallbacks (direct-save →
    trainer.save_model; merge → unmerged save) were exercised by no test."""

    def _make_trainer(self, tmp_path, *, merge_adapters):
        from forgelm.config import ForgeConfig
        from forgelm.trainer import ForgeTrainer

        config = ForgeConfig(
            model={"name_or_path": "org/model"},
            lora={},
            training={"output_dir": str(tmp_path), "merge_adapters": merge_adapters},
            data={"dataset_name_or_path": "org/dataset"},
        )
        with patch("forgelm.trainer.WebhookNotifier"):
            trainer = ForgeTrainer.__new__(ForgeTrainer)
            trainer.config = config
            trainer.tokenizer = MagicMock()
            trainer.trainer = MagicMock()
        return trainer

    def test_direct_save_falls_back_to_trainer_save_model(self, tmp_path):
        """A ``save_pretrained`` failure (contract drift / serialization error)
        falls back to HF Trainer's hardened ``save_model`` path with a WARNING."""
        trainer = self._make_trainer(tmp_path, merge_adapters=False)
        trainer.trainer.model.save_pretrained = MagicMock(side_effect=AttributeError("no save_pretrained"))
        final_path = str(tmp_path / "final")
        with patch("logging.Logger.warning") as warn:
            trainer.save_final_model(final_path)
        trainer.trainer.save_model.assert_called_once_with(final_path)
        trainer.tokenizer.save_pretrained.assert_called_once_with(final_path)
        assert any("falling back" in str(c.args).lower() for c in warn.call_args_list)

    def test_merge_save_falls_back_to_unmerged_save(self, tmp_path):
        """A non-PEFT model lacking ``merge_and_unload`` falls back to an
        unmerged ``trainer.save_model`` so the run still produces an artefact."""
        trainer = self._make_trainer(tmp_path, merge_adapters=True)
        trainer.trainer.model.merge_and_unload = MagicMock(side_effect=AttributeError("not a PeftModel"))
        final_path = str(tmp_path / "final_merged")
        with patch("logging.Logger.warning") as warn:
            trainer.save_final_model(final_path)
        trainer.trainer.save_model.assert_called_once_with(final_path)
        trainer.tokenizer.save_pretrained.assert_called_once_with(final_path)
        assert any("merge failed" in str(c.args).lower() for c in warn.call_args_list)

    def test_direct_save_happy_path_no_fallback(self, tmp_path):
        """When ``save_pretrained`` succeeds, ``save_model`` is NOT called."""
        trainer = self._make_trainer(tmp_path, merge_adapters=False)
        trainer.trainer.model.save_pretrained = MagicMock()
        trainer.save_final_model(str(tmp_path / "final_ok"))
        trainer.trainer.save_model.assert_not_called()


@pytest.mark.skipif(not torch_available, reason="torch not installed")
class TestSafetyClassifierModeThreading:
    """``evaluation.safety.classifier_mode`` must reach ``run_safety_evaluation``.

    Regression coverage for a kwarg-forwarding fix in
    ``ForgeTrainer._run_safety_if_configured``
    (``classifier_mode=getattr(safety_cfg, "classifier_mode", "auto")``) that
    previously had no test — a future refactor of the kwargs dict could
    silently drop the forwarded value without any test catching it.
    """

    def _make_trainer(self, tmp_path, classifier_mode):
        from forgelm.config import ForgeConfig
        from forgelm.trainer import ForgeTrainer

        config = ForgeConfig(
            model={"name_or_path": "org/model"},
            lora={},
            training={"output_dir": str(tmp_path)},
            data={"dataset_name_or_path": "org/dataset"},
            evaluation={
                "safety": {
                    "enabled": True,
                    "classifier_mode": classifier_mode,
                },
            },
        )
        with patch("forgelm.trainer.WebhookNotifier"):
            trainer = ForgeTrainer.__new__(ForgeTrainer)
            trainer.config = config
            trainer.checkpoint_dir = str(tmp_path)
            trainer.tokenizer = MagicMock()
            trainer.trainer = MagicMock()
            trainer.audit = MagicMock()
        return trainer

    @pytest.mark.parametrize("classifier_mode", ["classification", "generation"])
    def test_classifier_mode_passed_through_to_run_safety_evaluation(self, tmp_path, classifier_mode):
        trainer = self._make_trainer(tmp_path, classifier_mode=classifier_mode)

        with patch("forgelm.safety.run_safety_evaluation") as mock_run:
            mock_run.return_value = MagicMock()
            trainer._run_safety_if_configured()

        mock_run.assert_called_once()
        assert mock_run.call_args.kwargs["classifier_mode"] == classifier_mode

    def test_classifier_mode_defaults_to_auto_when_unset(self, tmp_path):
        """The `auto` default (config default) still threads through, not just
        the non-default override — guards against a fix that only forwards
        the value when explicitly set."""
        trainer = self._make_trainer(tmp_path, classifier_mode="auto")

        with patch("forgelm.safety.run_safety_evaluation") as mock_run:
            mock_run.return_value = MagicMock()
            trainer._run_safety_if_configured()

        assert mock_run.call_args.kwargs["classifier_mode"] == "auto"


@pytest.mark.skipif(not torch_available, reason="torch not installed")
class TestGrpoRewardModelRevisionPin:
    """``training.grpo_reward_model_revision`` must reach the reward-model load.

    The reward model *is* the objective GRPO optimises against, so an
    unpinned upstream re-tune changes what the run was trained to do with no
    config diff to point at.  The field validated, cross-field-checked and
    documented but reached no loader until this wiring landed.

    No network, no GPU: the revision resolver is stubbed and both
    transformers entry points are mocked.
    """

    SHA = "0" * 39 + "c"

    @pytest.fixture(autouse=True)
    def _clean_registry(self):
        from forgelm import model as model_mod

        model_mod._RESOLVED_MODEL_REVISIONS.clear()
        yield
        model_mod._RESOLVED_MODEL_REVISIONS.clear()

    @pytest.fixture
    def stub_resolver(self, monkeypatch):
        def _install(**overrides):
            from forgelm import compliance as compliance_mod

            seen = {}

            def _fake(repo_id, *, requested=None, offline=False):
                seen["requested"] = requested
                seen["offline"] = offline
                record = {
                    "repo_id": repo_id,
                    "revision_requested": requested,
                    "revision_resolved": None,
                    "resolution_source": "unresolved",
                }
                record.update(overrides)
                return record

            monkeypatch.setattr(compliance_mod, "resolve_model_revision", _fake)
            return seen

        return _install

    def _patched_loads(self, captured, fail_model=False):
        def _tok(_path, **kwargs):
            captured["tokenizer"] = kwargs.get("revision")
            return MagicMock()

        def _model(_path, **kwargs):
            captured["model"] = kwargs.get("revision")
            if fail_model:
                raise OSError("hub down")
            return MagicMock()

        return (
            patch("torch.cuda.is_available", return_value=False),
            patch("transformers.AutoTokenizer.from_pretrained", side_effect=_tok),
            patch("transformers.AutoModelForSequenceClassification.from_pretrained", side_effect=_model),
        )

    def test_resolved_sha_reaches_both_loads(self, stub_resolver):
        from forgelm.trainer import ForgeTrainer

        stub_resolver(revision_resolved=self.SHA, resolution_source="pinned_resolved")
        captured: dict = {}
        tok_patch, model_patch, cuda_patch = self._patched_loads(captured)
        with tok_patch, model_patch, cuda_patch:
            ForgeTrainer._build_classifier_reward("org/reward", self.SHA)
        assert captured["tokenizer"] == self.SHA
        assert captured["model"] == self.SHA

    def test_unconfirmed_pin_is_still_honoured_by_the_load(self, stub_resolver):
        """No SHA could be confirmed, but the operator's literal must still
        reach ``revision=`` — otherwise the load silently ignores the pin."""
        from forgelm.trainer import ForgeTrainer

        stub_resolver(resolution_source="pinned_unverified")
        captured: dict = {}
        tok_patch, model_patch, cuda_patch = self._patched_loads(captured)
        with tok_patch, model_patch, cuda_patch:
            ForgeTrainer._build_classifier_reward("org/reward", "v1.0")
        assert captured["tokenizer"] == "v1.0"
        assert captured["model"] == "v1.0"

    def test_unpinned_load_is_unchanged(self, stub_resolver):
        from forgelm.trainer import ForgeTrainer

        stub_resolver(resolution_source="unresolved")
        captured: dict = {}
        tok_patch, model_patch, cuda_patch = self._patched_loads(captured)
        with tok_patch, model_patch, cuda_patch:
            ForgeTrainer._build_classifier_reward("org/reward")
        assert captured["tokenizer"] is None
        assert captured["model"] is None

    def test_successful_load_is_recorded_under_the_reward_role(self, stub_resolver):
        from forgelm import model as model_mod
        from forgelm.trainer import ROLE_GRPO_REWARD_MODEL, ForgeTrainer

        stub_resolver(revision_resolved=self.SHA, resolution_source="pinned_resolved")
        tok_patch, model_patch, cuda_patch = self._patched_loads({})
        with tok_patch, model_patch, cuda_patch:
            ForgeTrainer._build_classifier_reward("org/reward", self.SHA)
        record = model_mod.get_loaded_model_revision("org/reward", ROLE_GRPO_REWARD_MODEL)
        assert record["revision_resolved"] == self.SHA
        # Never under base_model: the reward model contributed no weights to
        # the fine-tuned model and must not appear in its lineage.
        assert model_mod.get_loaded_model_revision("org/reward") is None

    def test_nothing_recorded_when_the_load_fails(self, stub_resolver):
        from forgelm import model as model_mod
        from forgelm.trainer import ROLE_GRPO_REWARD_MODEL, ForgeTrainer

        stub_resolver(revision_resolved=self.SHA, resolution_source="pinned_resolved")
        tok_patch, model_patch, cuda_patch = self._patched_loads({}, fail_model=True)
        with tok_patch, model_patch, cuda_patch:
            with pytest.raises(OSError):
                ForgeTrainer._build_classifier_reward("org/reward", self.SHA)
        assert model_mod.get_loaded_model_revision("org/reward", ROLE_GRPO_REWARD_MODEL) is None

    def _grpo_trainer(self, tmp_path, revision, offline=False):
        from forgelm.config import ForgeConfig
        from forgelm.trainer import ForgeTrainer

        config = ForgeConfig(
            model={"name_or_path": "org/model", "offline": offline},
            lora={},
            training={
                "output_dir": str(tmp_path),
                "grpo_reward_model": "org/reward",
                "grpo_reward_model_revision": revision,
            },
            data={"dataset_name_or_path": "org/dataset"},
        )
        trainer = ForgeTrainer.__new__(ForgeTrainer)
        trainer.config = config
        return trainer

    def test_config_revision_reaches_the_builder(self, tmp_path, stub_resolver):
        """The config → builder hop is where the field was previously dropped;
        asserting only on ``_build_classifier_reward`` would not catch it."""
        seen = stub_resolver(resolution_source="unresolved")
        trainer = self._grpo_trainer(tmp_path, self.SHA)
        captured: dict = {}
        tok_patch, model_patch, cuda_patch = self._patched_loads(captured)
        with tok_patch, model_patch, cuda_patch:
            funcs = trainer._resolve_grpo_reward_funcs()
        assert len(funcs) == 1
        assert seen["requested"] == self.SHA

    def test_model_offline_flag_reaches_the_resolver(self, tmp_path, stub_resolver):
        seen = stub_resolver(resolution_source="unresolved")
        trainer = self._grpo_trainer(tmp_path, self.SHA, offline=True)
        tok_patch, model_patch, cuda_patch = self._patched_loads({})
        with tok_patch, model_patch, cuda_patch:
            trainer._resolve_grpo_reward_funcs()
        assert seen["offline"] is True
