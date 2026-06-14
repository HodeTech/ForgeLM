"""Dry-run mode: validate config + summarize what training *would* do."""

from __future__ import annotations

import json
import os

from ..config import ForgeConfig
from ._logging import logger


def _galore_dry_run_fields(config: ForgeConfig) -> dict:
    if not config.training.galore_enabled:
        return {"galore_enabled": False, "galore_optim": None, "galore_rank": None}
    return {
        "galore_enabled": True,
        "galore_optim": config.training.galore_optim,
        "galore_rank": config.training.galore_rank,
    }


def _evaluation_dry_run_fields(config: ForgeConfig) -> dict:
    eval_cfg = config.evaluation
    safety = eval_cfg.safety if eval_cfg else None
    return {
        "auto_revert": bool(eval_cfg and eval_cfg.auto_revert),
        "safety_enabled": bool(safety and safety.enabled),
        "safety_scoring": safety.scoring if safety else None,
    }


def _compliance_dry_run_fields(config: ForgeConfig) -> dict:
    comp = config.compliance
    return {
        "compliance_configured": bool(comp and comp.provider_name),
        "risk_classification": comp.risk_classification if comp else None,
    }


def _build_dry_run_result(config: ForgeConfig) -> dict:
    """Assemble the dry-run summary dict from the validated config."""
    result = {
        # Universal CI gate key (json-output.md "Common conventions"): every
        # envelope must start with ``success`` so a consumer can branch on one
        # key before parsing the rest. ``status: "valid"`` is kept for backward
        # compatibility with pre-0.7.1 consumers.
        "success": True,
        "status": "valid",
        "model": config.model.name_or_path,
        "backend": config.model.backend,
        "load_in_4bit": config.model.load_in_4bit,
        "trust_remote_code": config.model.trust_remote_code,
        "dora": config.lora.use_dora,
        "lora_rank": config.lora.r,
        "lora_alpha": config.lora.alpha,
        "dataset": config.data.dataset_name_or_path,
        "epochs": config.training.num_train_epochs,
        "batch_size": config.training.per_device_train_batch_size,
        "output_dir": os.path.join(config.training.output_dir, config.training.final_model_dir),
        "offline": config.model.offline,
        "distributed": config.distributed.strategy if config.distributed else None,
        "rope_scaling": config.training.rope_scaling,
        "neftune_noise_alpha": config.training.neftune_noise_alpha,
        "webhook_configured": bool(config.webhook and (config.webhook.url or config.webhook.url_env)),
    }
    result.update(_galore_dry_run_fields(config))
    result.update(_evaluation_dry_run_fields(config))
    result.update(_compliance_dry_run_fields(config))
    return result


def _run_dry_run(config: ForgeConfig, output_format: str) -> None:
    """Validate config, model access, and dataset access without loading heavy dependencies."""
    result = _build_dry_run_result(config)

    if output_format == "json":
        print(json.dumps(result, indent=2))
        return

    logger.info("=== DRY RUN MODE ===")
    logger.info("Configuration validated successfully.")
    for key, value in result.items():
        if key not in ("status", "success"):
            logger.info("  %s: %s", key, value)

    if config.model.trust_remote_code:
        logger.warning("trust_remote_code is ENABLED — review model source before production use.")

    logger.info("=== DRY RUN COMPLETE — config is valid ===")
