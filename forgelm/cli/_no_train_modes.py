"""Non-training modes that share the top-level config: benchmark-only,
merge, generate-data, compliance-export, plus the dispatch helper that
chooses between them based on the parsed flags.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Optional, Tuple

from ..config import ForgeConfig
from ._dry_run import _run_dry_run
from ._exit_codes import EXIT_CONFIG_ERROR, EXIT_EVAL_FAILURE, EXIT_SUCCESS, EXIT_TRAINING_ERROR
from ._fit_check import _run_fit_check
from ._logging import logger


def _resolve_benchmark_load_target(model_path: str) -> Tuple[str, Optional[str]]:
    """Resolve ``(base_path, adapter_path_or_none)`` for the inference loader.

    A PEFT checkpoint is detected by the presence of ``adapter_config.json``
    inside ``model_path``.  When found, the base model identifier is read
    from ``adapter_config.json::base_model_name_or_path``; the adapter
    directory itself is returned as the second tuple element so the
    caller can pass ``adapter=...`` to :func:`forgelm.inference.load_model`.

    Exits with :data:`EXIT_CONFIG_ERROR` (with an actionable log line)
    when the adapter config is unreadable / malformed / missing the
    base-model field — these are all unrecoverable configuration errors
    that would otherwise surface as confusing crashes deep inside
    ``PeftModel.from_pretrained``.
    """
    adapter_cfg_path = os.path.join(model_path, "adapter_config.json")
    if not os.path.isfile(adapter_cfg_path):
        return model_path, None

    # adapter_config.json is HF-produced JSON — explicit UTF-8 keeps the
    # parse deterministic across Windows code-page locales.
    try:
        with open(adapter_cfg_path, encoding="utf-8") as f:
            adapter_meta = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.error("Failed to read PEFT adapter_config.json at %s: %s", adapter_cfg_path, e)
        sys.exit(EXIT_CONFIG_ERROR)

    base_path = adapter_meta.get("base_model_name_or_path")
    if not base_path:
        logger.error(
            "PEFT checkpoint at %s is missing 'base_model_name_or_path' in adapter_config.json — "
            "cannot reconstruct the base model + adapter combination.",
            model_path,
        )
        sys.exit(EXIT_CONFIG_ERROR)

    return base_path, model_path


def _run_benchmark_only(config: ForgeConfig, model_path: str, output_format: str) -> None:
    """Run benchmark evaluation on an existing model without training."""
    from ..benchmark import run_benchmark
    from ..inference import load_model

    eval_cfg = config.evaluation
    if not eval_cfg or not eval_cfg.benchmark or not eval_cfg.benchmark.tasks:
        logger.error("No benchmark tasks configured. Add evaluation.benchmark.tasks to your config.")
        sys.exit(EXIT_CONFIG_ERROR)

    bench_cfg = eval_cfg.benchmark

    base_path, adapter_path = _resolve_benchmark_load_target(model_path)
    if adapter_path:
        logger.info(
            "Detected PEFT checkpoint. Loading base model %s + adapter %s for benchmark.", base_path, adapter_path
        )
    else:
        logger.info("Loading model from %s for benchmark evaluation...", base_path)

    model, tokenizer = load_model(
        base_path,
        adapter=adapter_path,
        backend=config.model.backend,
        load_in_4bit=config.model.load_in_4bit,
        trust_remote_code=getattr(config.model, "trust_remote_code", False),
    )

    output_dir = bench_cfg.output_dir or os.path.join(os.path.dirname(model_path), "benchmark")

    result = run_benchmark(
        model=model,
        tokenizer=tokenizer,
        tasks=bench_cfg.tasks,
        num_fewshot=bench_cfg.num_fewshot,
        batch_size=bench_cfg.batch_size,
        limit=bench_cfg.limit,
        output_dir=output_dir,
        min_score=bench_cfg.min_score,
    )

    if output_format == "json":
        output = {
            "success": result.passed,
            "model_path": model_path,
            "benchmark": {
                "scores": result.scores,
                "average": result.average_score,
                "passed": result.passed,
            },
        }
        if result.failure_reason:
            output["failure_reason"] = result.failure_reason
        print(json.dumps(output, indent=2))
    else:
        logger.info("Benchmark Results:")
        for task, score in result.scores.items():
            logger.info("  %s: %.4f", task, score)
        logger.info("  Average: %.4f", result.average_score)
        if result.passed:
            logger.info("Benchmark evaluation PASSED.")
        else:
            logger.error("Benchmark evaluation FAILED: %s", result.failure_reason)

    if not result.passed:
        sys.exit(EXIT_EVAL_FAILURE)


def _run_merge(config: ForgeConfig, output_format: str) -> None:
    """Run model merging from config without training."""
    if not config.merge or not config.merge.enabled:
        logger.error("No merge configuration found or merge not enabled. Add a 'merge' section to your config.")
        sys.exit(EXIT_CONFIG_ERROR)

    from ..merging import merge_peft_adapters

    result = merge_peft_adapters(
        base_model_path=config.model.name_or_path,
        adapters=config.merge.models,
        method=config.merge.method,
        output_dir=config.merge.output_dir,
        trust_remote_code=config.model.trust_remote_code,
        ties_trim_fraction=config.merge.ties_trim_fraction,
        dare_drop_rate=config.merge.dare_drop_rate,
        dare_seed=config.merge.dare_seed,
    )

    if output_format == "json":
        print(
            json.dumps(
                {
                    "success": result.success,
                    "method": result.method,
                    "num_models": result.num_models,
                    "output_dir": result.output_dir,
                    "error": result.error,
                },
                indent=2,
            )
        )
    else:
        if result.success:
            logger.info(
                "Model merge completed: %d models merged with '%s' → %s",
                result.num_models,
                result.method,
                result.output_dir,
            )
        else:
            logger.error("Model merge failed: %s", result.error)

    if not result.success:
        sys.exit(EXIT_TRAINING_ERROR)


def _run_generate_data(config: ForgeConfig, output_format: str) -> None:
    """Generate synthetic training data using teacher model."""
    from ..synthetic import SyntheticDataGenerator

    if not config.synthetic or not config.synthetic.enabled:
        logger.error("Synthetic data generation not configured. Add 'synthetic' section to config.")
        sys.exit(EXIT_CONFIG_ERROR)

    generator = SyntheticDataGenerator(config)
    result = generator.generate()

    # A near-total generation failure must not slip through as success:true /
    # exit 0 just because one prompt happened to yield (F-P3-FABLE-61). Gate on
    # the configured ``synthetic.min_success_rate`` (default 0.0 = legacy "any
    # yield succeeds"); independently warn when the failure rate crosses a fixed
    # sanity bound so a degraded run is visible even without a configured floor.
    min_success_rate = config.synthetic.min_success_rate
    meets_threshold = result.successful > 0 and result.success_rate >= min_success_rate
    sanity_failure_rate = config.synthetic.sanity_failure_rate  # warn-only bound, regardless of the floor

    if output_format == "json":
        print(
            json.dumps(
                {
                    "success": meets_threshold,
                    "total_prompts": result.total_prompts,
                    "successful": result.successful,
                    "failed": result.failed,
                    "success_rate": round(result.success_rate, 4),
                    "min_success_rate": min_success_rate,
                    "output_file": result.output_file,
                    "duration_seconds": round(result.duration_seconds, 2),
                    "errors": result.errors[:10],
                },
                indent=2,
            )
        )
    else:
        logger.info(
            "Synthetic data generation: %d/%d successful (%.1f%%) → %s",
            result.successful,
            result.total_prompts,
            result.success_rate * 100,
            result.output_file,
        )

    if result.total_prompts > 0 and (1.0 - result.success_rate) > sanity_failure_rate:
        logger.warning(
            "Synthetic generation failure rate is %.1f%% (%d/%d prompts failed) — "
            "the resulting dataset may be small or skewed; inspect the errors before training.",
            (1.0 - result.success_rate) * 100,
            result.failed,
            result.total_prompts,
        )

    if not meets_threshold:
        if result.successful == 0:
            logger.error("Synthetic data generation produced no usable examples.")
        else:
            logger.error(
                "Synthetic data generation success rate %.1f%% is below the configured "
                "synthetic.min_success_rate of %.1f%%.",
                result.success_rate * 100,
                min_success_rate * 100,
            )
        sys.exit(EXIT_TRAINING_ERROR)


def _run_compliance_export(config: ForgeConfig, output_dir: str, output_format: str) -> None:
    """Generate compliance artifacts from config without training."""
    from ..compliance import export_compliance_artifacts, generate_training_manifest

    logger.info("Generating compliance artifacts to %s...", output_dir)
    manifest = generate_training_manifest(config=config, metrics={})
    files = export_compliance_artifacts(manifest, output_dir)

    if output_format == "json":
        print(json.dumps({"success": True, "files": files, "output_dir": output_dir}, indent=2))
    else:
        logger.info("Compliance artifacts exported:")
        for f in files:
            logger.info("  %s", f)


def _maybe_run_no_train_mode(config, args) -> None:
    """If a non-training mode flag is set, run it and exit."""
    if args.dry_run:
        _run_dry_run(config, args.output_format)
        sys.exit(EXIT_SUCCESS)
    if args.fit_check:
        _run_fit_check(config, args.output_format)
        sys.exit(EXIT_SUCCESS)
    if args.benchmark_only:
        _run_benchmark_only(config, args.benchmark_only, args.output_format)
        sys.exit(EXIT_SUCCESS)
    if args.merge:
        _run_merge(config, args.output_format)
        sys.exit(EXIT_SUCCESS)
    if args.generate_data:
        _run_generate_data(config, args.output_format)
        sys.exit(EXIT_SUCCESS)
    if args.compliance_export:
        _run_compliance_export(config, args.compliance_export, args.output_format)
        sys.exit(EXIT_SUCCESS)
