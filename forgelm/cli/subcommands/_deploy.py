"""``forgelm deploy`` dispatcher."""

from __future__ import annotations

import json
import sys

from .._exit_codes import EXIT_CONFIG_ERROR, EXIT_TRAINING_ERROR
from .._logging import logger


def _run_deploy_cmd(args, output_format: str) -> None:
    """Dispatch the ``forgelm deploy`` subcommand."""
    from ...deploy import HFEndpointsOptions, generate_deploy_config

    result = generate_deploy_config(
        model_path=args.model_path,
        target=args.target,
        output_path=args.output,
        system_prompt=args.system,
        max_length=args.max_length,
        trust_remote_code=args.trust_remote_code,
        gpu_memory_utilization=args.gpu_memory_utilization,
        port=args.port,
        hf_endpoints=HFEndpointsOptions(vendor=getattr(args, "vendor", "aws")),
    )

    if output_format == "json":
        print(
            json.dumps(
                {
                    "success": result.success,
                    "target": result.target,
                    "output_path": result.output_path,
                    "error": result.error,
                },
                indent=2,
            )
        )
    else:
        if result.success:
            logger.info("Deploy config written: %s (target=%s)", result.output_path, result.target)
        else:
            logger.error("Deploy config generation failed: %s", result.error)

    if not result.success:
        # A caller-input error (unsupported target, model_path not a
        # directory) is exit 1 per the public contract; only a genuine
        # runtime failure (filesystem write error) is exit 2 — a
        # per-target generator bug (e.g. KeyError/TypeError from a
        # template render) is not caught here and propagates instead.
        # Mirrors verify-gguf's input(1)/runtime(2) split.
        sys.exit(EXIT_CONFIG_ERROR if result.error_kind == "input" else EXIT_TRAINING_ERROR)
