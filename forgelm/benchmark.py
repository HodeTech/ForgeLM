"""Post-training benchmark evaluation via EleutherAI lm-evaluation-harness.

This module is optional — requires `pip install forgelm[eval]`.
"""

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("forgelm.benchmark")


@dataclass
class BenchmarkResult:
    """Holds the results of a benchmark evaluation run."""

    scores: Dict[str, float] = field(default_factory=dict)  # task_name -> accuracy
    average_score: float = 0.0
    passed: bool = True
    failure_reason: Optional[str] = None
    raw_results: Optional[Dict[str, Any]] = None  # full lm-eval output


def _check_lm_eval_available() -> None:
    """Check if lm-eval is installed."""
    try:
        import lm_eval  # noqa: F401
    except ImportError as e:
        raise ImportError(
            "lm-evaluation-harness is required for benchmarking but not installed. "
            "Install it with: pip install forgelm[eval]"
        ) from e


# Keys lm-eval may use for accuracy, in priority order
_ACC_METRIC_KEYS = ("acc_norm,none", "acc,none", "acc_norm", "acc")


def _extract_task_score(task_name: str, task_result: Dict[str, Any]) -> Optional[float]:
    """Pick the most appropriate accuracy metric out of an lm-eval task result."""
    for key in _ACC_METRIC_KEYS:
        score = task_result.get(key)
        if score is not None:
            logger.info("  %s: %.4f", task_name, score)
            return float(score)

    # Fallback: any key that looks like an accuracy metric — but never an
    # ``*_stderr`` companion key. lm-eval emits a paired standard-error key
    # (``acc_stderr,none``, ``acc_norm_stderr,<filter>``) alongside every
    # accuracy metric; its substring still contains "acc", so without the
    # stderr exclusion a value like 0.012 could be selected as the task's
    # accuracy when it precedes the real metric in dict order (F-P3-FABLE-54).
    for key, value in task_result.items():
        if isinstance(value, (int, float)) and "acc" in key and "stderr" not in key:
            logger.info("  %s: %.4f (%s)", task_name, value, key)
            return float(value)

    logger.warning("  %s: no accuracy metric found in results", task_name)
    return None


def _parse_results(raw_results: Dict[str, Any]) -> Dict[str, float]:
    """Convert raw lm-eval per-task output into a flat task → accuracy map."""
    scores: Dict[str, float] = {}
    for task_name, task_result in raw_results.items():
        score = _extract_task_score(task_name, task_result)
        if score is not None:
            scores[task_name] = score
    return scores


def _save_benchmark_json(
    output_dir: str,
    tasks: List[str],
    scores: Dict[str, float],
    average_score: float,
    passed: bool,
    num_fewshot: Optional[int],
    limit: Optional[int],
) -> None:
    """Persist the benchmark summary to ``benchmark_results.json``."""
    os.makedirs(output_dir, exist_ok=True)
    results_path = os.path.join(output_dir, "benchmark_results.json")
    try:
        with open(results_path, "w") as f:
            json.dump(
                {
                    "tasks": tasks,
                    "scores": scores,
                    "average_score": average_score,
                    "passed": passed,
                    "num_fewshot": num_fewshot,
                    "limit": limit,
                },
                f,
                indent=2,
            )
        logger.info("Benchmark results saved to %s", results_path)
    except (OSError, TypeError, ValueError) as e:
        # OSError: filesystem (ENOSPC, permission, broken parent dir).
        # TypeError/ValueError: ``json.dump`` rejecting an unserialisable
        # object inside the lm-eval result tree.  Saving the artefact is
        # non-fatal: the run already completed and metrics live in the
        # returned BenchmarkResult.
        logger.warning("Failed to save benchmark results: %s", e)


def run_benchmark(
    model: Any,
    tokenizer: Any,
    tasks: List[str],
    num_fewshot: Optional[int] = None,
    batch_size: str = "auto",
    limit: Optional[int] = None,
    output_dir: Optional[str] = None,
    min_score: Optional[float] = None,
) -> BenchmarkResult:
    """Run lm-evaluation-harness benchmarks on a model.

    Args:
        model: The model to evaluate (HF model or PEFT model).
        tokenizer: The tokenizer for the model.
        tasks: List of benchmark task names (e.g. ["arc_easy", "hellaswag"]).
        num_fewshot: Number of few-shot examples. None = task default.
        batch_size: Batch size for evaluation. "auto" for automatic.
        limit: Limit number of samples per task (None = all).
        output_dir: Directory to save benchmark results JSON.
        min_score: Minimum average accuracy threshold. If below, result.passed = False.

    Returns:
        BenchmarkResult with per-task scores and pass/fail status.
    """
    if not tasks:
        logger.warning("No benchmark tasks specified. Skipping benchmark.")
        return BenchmarkResult(passed=True)

    _check_lm_eval_available()

    import lm_eval
    from lm_eval.models.huggingface import HFLM

    logger.info("Starting benchmark evaluation with tasks: %s", tasks)

    try:
        lm_obj = HFLM(pretrained=model, tokenizer=tokenizer, batch_size=batch_size)
    except Exception as e:  # noqa: BLE001 — best-effort: lm-eval HFLM wrapper construction crosses HF model introspection (AttributeError on architecture mismatch), tokenizer compatibility (ValueError), CUDA init (RuntimeError), and lm-eval-internal config parsing; surfacing as BenchmarkResult(passed=False) is the documented hard-failure surface so the trainer auto-revert gate can react.  # NOSONAR
        logger.exception("Failed to initialize lm-eval model wrapper")
        return BenchmarkResult(passed=False, failure_reason=f"Model wrapper initialization failed: {e}")

    task_kwargs: Dict[str, Any] = {}
    if num_fewshot is not None:
        task_kwargs["num_fewshot"] = num_fewshot

    try:
        results = lm_eval.simple_evaluate(model=lm_obj, tasks=tasks, limit=limit, **task_kwargs)
    except Exception as e:  # noqa: BLE001 — best-effort: lm-eval simple_evaluate runs a wide task surface (dataset download OSError, task spec ValueError, model.generate RuntimeError on CUDA OOM/dtype mismatch, lm-eval-internal AssertionError); BenchmarkResult(passed=False) is the documented hard-failure surface for the auto-revert gate.  # NOSONAR
        logger.exception("Benchmark evaluation failed")
        return BenchmarkResult(passed=False, failure_reason=f"Evaluation execution failed: {e}")

    raw_results = results.get("results", {})
    scores = _parse_results(raw_results)
    average_score = sum(scores.values()) / len(scores) if scores else 0.0
    logger.info("Average benchmark score: %.4f", average_score)

    passed = True
    failure_reason = None
    if min_score is not None and average_score < min_score:
        passed = False
        failure_reason = f"Average benchmark score ({average_score:.4f}) is below minimum threshold ({min_score:.4f})."
        logger.error("BENCHMARK FAILED: %s", failure_reason)

    if output_dir:
        _save_benchmark_json(output_dir, tasks, scores, average_score, passed, num_fewshot, limit)

    return BenchmarkResult(
        scores=scores,
        average_score=average_score,
        passed=passed,
        failure_reason=failure_reason,
        raw_results=raw_results,
    )
