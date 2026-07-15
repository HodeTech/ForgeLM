"""``forgelm export`` dispatcher."""

from __future__ import annotations

import json
import sys

from .._exit_codes import EXIT_CONFIG_ERROR, EXIT_TRAINING_ERROR
from .._logging import logger


def _run_export_cmd(args, output_format: str) -> None:
    """Dispatch the ``forgelm export`` subcommand."""
    from ...export import export_model

    result = export_model(
        model_path=args.model_path,
        output_path=args.output,
        output_format=args.format,
        quant=args.quant,
        adapter=args.adapter,
        update_integrity=not args.no_integrity_update,
    )

    if output_format == "json":
        print(
            json.dumps(
                {
                    "success": result.success,
                    "output_path": result.output_path,
                    "format": result.format,
                    "quant": result.quant,
                    "requested_quant": result.requested_quant,
                    "manual_step_required": result.manual_step_required,
                    "followup_command": result.followup_command,
                    "sha256": result.sha256,
                    "size_bytes": result.size_bytes,
                    "error": result.error,
                },
                indent=2,
            )
        )
    else:
        if result.success:
            logger.info(
                "Export complete: %s (quant=%s, sha256=%s…)",
                result.output_path,
                result.quant,
                (result.sha256 or "")[:12],
            )
        else:
            logger.error("Export failed: %s", result.error)

    if not result.success:
        # Operator-input failures (bad format/quant, malformed converter env)
        # are config errors (exit 1); converter/merge/infra failures are
        # runtime errors (exit 2) — see ExportResult.error_kind (F-P7-OPUS-36).
        if result.error_kind == "config":
            sys.exit(EXIT_CONFIG_ERROR)
        sys.exit(EXIT_TRAINING_ERROR)
