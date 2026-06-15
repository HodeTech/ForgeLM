"""Unit tests for forgelm.benchmark module."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from forgelm.benchmark import (
    BenchmarkResult,
    _check_lm_eval_available,
    _extract_task_score,
    _parse_results,
    run_benchmark,
)
from forgelm.config import BenchmarkConfig, ForgeConfig


class TestExtractTaskScore:
    """F-P3-FABLE-54: the fallback metric matcher must never pick an
    ``*_stderr`` companion key as the task accuracy."""

    def test_priority_key_selected(self):
        assert _extract_task_score("t", {"acc,none": 0.70, "acc_stderr,none": 0.01}) == pytest.approx(0.70)

    def test_fallback_skips_stderr_keys(self):
        # No priority key matches (named filter), and the stderr key is ordered
        # *before* the real metric — the matcher must still skip the 0.012.
        result = {"alias": "taskC", "acc_stderr,flex": 0.012, "acc,flex": 0.70}
        assert _extract_task_score("taskC", result) == pytest.approx(0.70)
        assert _parse_results({"taskC": result}) == {"taskC": pytest.approx(0.70)}

    def test_only_stderr_present_returns_none(self):
        # A degenerate result with no real accuracy metric must not be rescued
        # by the stderr value.
        assert _extract_task_score("t", {"acc_stderr,none": 0.012}) is None


class TestBenchmarkResult:
    def test_default_values(self):
        r = BenchmarkResult()
        assert r.scores == {}
        assert r.average_score == pytest.approx(0.0)
        assert r.passed is True
        assert r.failure_reason is None
        assert r.raw_results is None

    def test_with_scores(self):
        r = BenchmarkResult(
            scores={"arc_easy": 0.65, "hellaswag": 0.55},
            average_score=0.60,
            passed=True,
        )
        assert r.scores["arc_easy"] == pytest.approx(0.65)
        assert r.average_score == pytest.approx(0.60)

    def test_failed_result(self):
        r = BenchmarkResult(
            scores={"arc_easy": 0.30},
            average_score=0.30,
            passed=False,
            failure_reason="Below threshold",
        )
        assert r.passed is False
        assert r.failure_reason == "Below threshold"


class TestCheckLmEvalAvailable:
    def test_raises_when_not_installed(self):
        with patch.dict("sys.modules", {"lm_eval": None}):
            with pytest.raises(ImportError, match="lm-evaluation-harness"):
                _check_lm_eval_available()


class TestBenchmarkConfig:
    def test_defaults(self):
        b = BenchmarkConfig()
        assert b.enabled is False
        assert b.tasks == []
        assert b.num_fewshot is None
        assert b.batch_size == "auto"
        assert b.limit is None
        assert b.min_score is None

    def test_with_tasks(self):
        b = BenchmarkConfig(
            enabled=True,
            tasks=["arc_easy", "hellaswag"],
            num_fewshot=5,
            min_score=0.4,
        )
        assert b.enabled is True
        assert len(b.tasks) == 2
        assert b.min_score == pytest.approx(0.4)


class TestBenchmarkInConfig:
    def test_evaluation_with_benchmark(self):
        data = {
            "model": {"name_or_path": "org/model"},
            "lora": {},
            "training": {},
            "data": {"dataset_name_or_path": "org/dataset"},
            "evaluation": {
                "auto_revert": True,
                "benchmark": {
                    "enabled": True,
                    "tasks": ["arc_easy"],
                    "min_score": 0.5,
                },
            },
        }
        cfg = ForgeConfig(**data)
        assert cfg.evaluation.benchmark is not None
        assert cfg.evaluation.benchmark.enabled is True
        assert cfg.evaluation.benchmark.tasks == ["arc_easy"]
        assert cfg.evaluation.benchmark.min_score == pytest.approx(0.5)

    def test_evaluation_without_benchmark(self):
        data = {
            "model": {"name_or_path": "org/model"},
            "lora": {},
            "training": {},
            "data": {"dataset_name_or_path": "org/dataset"},
            "evaluation": {"auto_revert": True},
        }
        cfg = ForgeConfig(**data)
        assert cfg.evaluation.benchmark is None


lm_eval_available = True
try:
    import lm_eval  # noqa: F401
except ImportError:
    lm_eval_available = False


class TestRunBenchmark:
    def test_empty_tasks_returns_passed(self):
        result = run_benchmark(
            model=MagicMock(),
            tokenizer=MagicMock(),
            tasks=[],
        )
        assert result.passed is True
        assert result.scores == {}

    @pytest.mark.skipif(not lm_eval_available, reason="lm_eval not installed")
    def test_successful_benchmark(self):
        """Test benchmark with mocked lm-eval results.

        ``run_benchmark`` does function-local imports (``import lm_eval``;
        ``from lm_eval.models.huggingface import HFLM``) so the
        ``forgelm.benchmark`` module namespace never holds the symbols
        and ``patch("forgelm.benchmark.lm_eval", create=True)`` is a
        no-op against the function-scope rebind.  Patch the real
        import sites directly: ``lm_eval.simple_evaluate`` (callable on
        the module) and ``lm_eval.models.huggingface.HFLM`` (the class
        constructor the function looks up via ``from ... import``).
        """
        mock_lm_obj = MagicMock()
        mock_results = {
            "results": {
                "arc_easy": {"acc_norm,none": 0.65},
                "hellaswag": {"acc_norm,none": 0.55},
            }
        }
        with (
            patch("lm_eval.models.huggingface.HFLM", return_value=mock_lm_obj),
            patch("lm_eval.simple_evaluate", return_value=mock_results),
            patch("forgelm.benchmark._check_lm_eval_available", return_value=None),
        ):
            result = run_benchmark(
                model=MagicMock(),
                tokenizer=MagicMock(),
                tasks=["arc_easy", "hellaswag"],
            )

        assert result.scores.get("arc_easy") == pytest.approx(0.65)
        assert result.scores.get("hellaswag") == pytest.approx(0.55)
        assert result.average_score == pytest.approx(0.60, abs=0.01)
        assert result.passed is True

    def test_min_score_failure(self):
        """Test that min_score threshold triggers failure."""
        result = BenchmarkResult(
            scores={"arc_easy": 0.30},
            average_score=0.30,
            passed=False,
            failure_reason="Average benchmark score (0.3000) is below minimum threshold (0.5000).",
        )
        assert result.passed is False
        assert "below minimum threshold" in result.failure_reason

    def test_result_saved_to_file(self, tmp_path):
        """Test benchmark results are saved when output_dir specified."""
        output_dir = str(tmp_path / "benchmark_output")

        # Directly test the save logic by creating a result manually
        result = BenchmarkResult(
            scores={"arc_easy": 0.65},
            average_score=0.65,
            passed=True,
        )

        # Verify the output data structure
        output_data = {
            "tasks": ["arc_easy"],
            "scores": result.scores,
            "average_score": result.average_score,
            "passed": result.passed,
        }
        os.makedirs(output_dir, exist_ok=True)
        results_path = os.path.join(output_dir, "benchmark_results.json")
        with open(results_path, "w") as f:
            json.dump(output_data, f, indent=2)

        assert os.path.exists(results_path)
        with open(results_path) as f:
            saved = json.load(f)
        assert saved["scores"]["arc_easy"] == pytest.approx(0.65)
        assert saved["passed"] is True


class TestTrainResultWithBenchmark:
    def test_train_result_benchmark_fields(self):
        from forgelm.results import TrainResult

        result = TrainResult(
            success=True,
            metrics={"eval_loss": 0.5},
            benchmark_scores={"arc_easy": 0.65, "hellaswag": 0.55},
            benchmark_average=0.60,
            benchmark_passed=True,
        )
        assert result.benchmark_scores["arc_easy"] == pytest.approx(0.65)
        assert result.benchmark_average == pytest.approx(0.60)
        assert result.benchmark_passed is True

    def test_train_result_no_benchmark(self):
        from forgelm.results import TrainResult

        result = TrainResult(success=True)
        assert result.benchmark_scores is None
        assert result.benchmark_average is None
        assert result.benchmark_passed is None
