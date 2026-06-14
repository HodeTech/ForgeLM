"""TrainResult formatting helpers (JSON envelope + text-mode logs)."""

from __future__ import annotations

import json

from ._logging import logger


def _build_result_json_envelope(result) -> dict:
    """Assemble the JSON envelope for a TrainResult; only populated sub-blocks are added."""
    output = {
        "success": result.success,
        "metrics": result.metrics,
        "final_model_path": result.final_model_path,
        "reverted": result.reverted,
        # Article 14 discriminator: lets a JSON consumer tell "staged, awaiting
        # human sign-off" (exit 4, model NOT yet at final_model_path) apart from
        # an ordinary success (exit 0). See docs json-output.md.
        "awaiting_approval": result.awaiting_approval,
    }
    # Only present when the human-approval gate fired; carries the on-disk
    # staging directory the operator passes to `forgelm approve/reject`.
    if result.staging_path:
        output["staging_path"] = result.staging_path
    if result.benchmark_scores is not None:
        output["benchmark"] = {
            "scores": result.benchmark_scores,
            "average": result.benchmark_average,
            "passed": result.benchmark_passed,
        }
    if result.resource_usage:
        output["resource_usage"] = result.resource_usage
    if result.estimated_cost_usd is not None:
        output["estimated_cost_usd"] = result.estimated_cost_usd
    if result.safety_passed is not None:
        output["safety"] = {
            "passed": result.safety_passed,
            "safety_score": result.safety_score,
            "categories": result.safety_categories,
            "severity": result.safety_severity,
            "low_confidence_count": result.safety_low_confidence,
        }
    if result.judge_score is not None:
        output["judge"] = {"average_score": result.judge_score}
    return output


def _log_result_status(result) -> None:
    """Log success/failure headline (text mode)."""
    if result.success:
        logger.info("ForgeLM Training Pipeline Completed Successfully!")
        if result.final_model_path:
            logger.info("Final model saved to: %s", result.final_model_path)
    elif result.reverted:
        logger.error("ForgeLM Pipeline failed autonomous evaluation. Model was reverted.")
    else:
        logger.error("ForgeLM Pipeline failed.")


def _log_cost_summary(result) -> None:
    """Log estimated cost + GPU-hour breakdown when available (text mode)."""
    if result.estimated_cost_usd is None:
        return
    logger.info("Estimated training cost: $%.4f", result.estimated_cost_usd)
    if not result.resource_usage:
        return
    gpu_hours = result.resource_usage.get("gpu_hours")
    cost_source = result.resource_usage.get("cost_source", "unknown")
    if gpu_hours:
        logger.info("  GPU-hours: %.3f (pricing: %s)", gpu_hours, cost_source)


def _log_benchmark_summary(result) -> None:
    """Log per-task benchmark scores + average (text mode)."""
    if not result.benchmark_scores:
        return
    logger.info("Benchmark Results:")
    for task, score in result.benchmark_scores.items():
        logger.info("  %s: %.4f", task, score)
    if result.benchmark_average is not None:
        logger.info("  Average: %.4f", result.benchmark_average)


def _output_result(result, output_format: str) -> None:
    """Output training result in the requested format."""
    if output_format == "json":
        # ``default=str`` mirrors the doctor JSON renderer's defence
        # (Wave 2a Round-5 F-R5-06): ``TrainResult.resource_usage`` is typed
        # ``Optional[Dict[str, Any]]`` so a downstream monitor injecting a
        # ``Path`` / ``datetime`` / ``numpy`` scalar would otherwise raise
        # ``TypeError`` and dump a Python traceback to stdout — breaking
        # CI pipelines that parse stdout as JSON.  Coercing to ``str`` for
        # any non-JSON-native type preserves the documented envelope shape.
        print(json.dumps(_build_result_json_envelope(result), indent=2, default=str))
        return
    _log_result_status(result)
    _log_cost_summary(result)
    _log_benchmark_summary(result)
