"""``forgelm audit`` dispatcher + the shared dataset-audit worker.

``_run_data_audit`` is the underlying worker behind the ``forgelm audit``
subcommand. (The legacy ``forgelm --data-audit PATH`` flag that previously
shared this code path was removed in v0.8.0.)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

from .._exit_codes import EXIT_CONFIG_ERROR, EXIT_TRAINING_ERROR
from .._logging import logger


def _run_data_audit(
    audit_input: str,
    output_dir: Optional[str],
    output_format: str,
    *,
    verbose: bool = False,
    near_dup_threshold: Optional[int] = None,
    dedup_method: str = "simhash",
    minhash_jaccard: Optional[float] = None,
    enable_quality_filter: bool = False,
    enable_pii_ml: bool = False,
    pii_ml_language: str = "en",
    emit_croissant: bool = False,
    workers: int = 1,
) -> None:
    """Phase 11 / 11.5 / 12 dispatch: dataset quality + governance audit.

    Reached via the ``forgelm audit`` subcommand. (The legacy
    ``--data-audit`` flag that previously shared this worker was removed in
    v0.8.0; this helper stays single-purpose.)
    """
    from ...data_audit import (
        DEFAULT_MINHASH_JACCARD,
        DEFAULT_NEAR_DUP_HAMMING,
        audit_dataset,
        summarize_report,
    )

    target = output_dir or "./audit"
    threshold = near_dup_threshold if near_dup_threshold is not None else DEFAULT_NEAR_DUP_HAMMING
    jaccard = minhash_jaccard if minhash_jaccard is not None else DEFAULT_MINHASH_JACCARD
    try:
        report = audit_dataset(
            audit_input,
            output_dir=target,
            near_dup_threshold=threshold,
            dedup_method=dedup_method,
            minhash_jaccard=jaccard,
            enable_quality_filter=enable_quality_filter,
            enable_pii_ml=enable_pii_ml,
            pii_ml_language=pii_ml_language,
            emit_croissant=emit_croissant,
            workers=workers,
        )
    except OSError as exc:
        # OSError covers FileNotFoundError / PermissionError / ENOSPC /
        # IsADirectoryError that bubble up from _resolve_input or
        # _read_jsonl_split when the target is unreachable BEFORE the
        # per-split tolerance loop kicks in.
        if output_format == "json":
            print(json.dumps({"success": False, "error": str(exc)}))
        else:
            logger.error("Audit failed: %s", exc)
        sys.exit(EXIT_CONFIG_ERROR)
    except ImportError as exc:
        # Phase 12: --dedup-method=minhash needs the optional 'ingestion-scale'
        # extra. Treat the same way other subcommands handle missing extras —
        # EXIT_TRAINING_ERROR rather than EXIT_CONFIG_ERROR so CI/CD retry
        # logic distinguishes "config invalid" from "extras missing".
        if output_format == "json":
            print(json.dumps({"success": False, "error": str(exc)}))
        else:
            logger.error("%s", exc)
        sys.exit(EXIT_TRAINING_ERROR)

    if output_format == "json":
        # Stdout summary only — full report goes to disk under --output. A
        # multi-split audit can grow to tens of KB of JSON which would drown
        # downstream pipeline logs. Operators that want everything via stdout
        # can read the file path from `report_path` and slurp it.
        summary = {
            "success": True,
            "report_path": str(Path(target) / "data_audit_report.json"),
            "generated_at": report.generated_at,
            "source_input": report.source_input,
            "total_samples": report.total_samples,
            "splits": {name: info.get("sample_count", 0) for name, info in report.splits.items()},
            "pii_summary": report.pii_summary,
            "pii_severity": report.pii_severity,
            "secrets_summary": report.secrets_summary,
            "quality_summary": report.quality_summary,
            # Pre-Phase-12 envelope key — kept verbatim so any pre-Phase-12
            # JSON consumer (e.g. ``jq '.near_duplicate_pairs_per_split.train'``)
            # keeps working. The richer ``near_duplicate_summary`` below
            # carries the same data plus method/threshold metadata.
            "near_duplicate_pairs_per_split": report.near_duplicate_summary.get("pairs_per_split", {}),
            "near_duplicate_summary": report.near_duplicate_summary,
            "cross_split_leakage_pairs": list((report.cross_split_overlap.get("pairs") or {}).keys()),
            # Phase 12.5: Croissant 1.0 dataset card. Empty dict when the
            # ``--croissant`` flag was not passed — same additive shape as
            # ``secrets_summary`` / ``quality_summary``. Surfacing it here
            # mirrors the on-disk report so a CI step that reads stdout
            # via ``--output-format json`` does not need to slurp the
            # file separately.
            "croissant": report.croissant,
            "notes": report.notes,
        }
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print(summarize_report(report, verbose=verbose))
        print(f"\nReport written to: {Path(target) / 'data_audit_report.json'}")


def _run_audit_cmd(args, output_format: str) -> None:
    """Phase 11.5 / 12 dispatch for the ``forgelm audit PATH`` subcommand.

    The audit subparser uses ``argparse.SUPPRESS`` for ``--output``, so when
    the operator doesn't pass it the attribute is missing from ``args`` and
    ``getattr(..., None)`` lets the top-level ``--output`` (default=None) win.
    ``_run_data_audit`` applies the canonical ``./audit`` fallback when both
    end up None.

    Re-imports ``_run_data_audit`` from the package facade so test patches
    on ``forgelm.cli._run_data_audit`` are honoured even when the command is
    dispatched from inside the package.
    """
    # Late import via the package facade so monkeypatched
    # ``forgelm.cli._run_data_audit`` references resolve correctly.
    from forgelm import cli as _cli_facade

    _cli_facade._run_data_audit(
        args.input_path,
        getattr(args, "output", None),
        output_format,
        verbose=getattr(args, "verbose", False),
        near_dup_threshold=getattr(args, "near_dup_threshold", None),
        dedup_method=getattr(args, "dedup_method", "simhash"),
        minhash_jaccard=getattr(args, "jaccard_threshold", None),
        enable_quality_filter=getattr(args, "quality_filter", True),
        enable_pii_ml=getattr(args, "pii_ml", False),
        pii_ml_language=getattr(args, "pii_ml_language", "en"),
        emit_croissant=getattr(args, "croissant", False),
        workers=getattr(args, "workers", 1),
    )
