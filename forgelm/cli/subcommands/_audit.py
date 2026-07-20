"""``forgelm audit`` dispatcher + the shared dataset-audit worker.

``_run_data_audit`` is the underlying worker behind the ``forgelm audit``
subcommand. (The legacy ``forgelm --data-audit PATH`` flag that previously
shared this code path was removed in v0.8.0.)

Exit codes:

- ``0`` — the audit ran and no critical finding gated it.
- ``1`` — ``EXIT_CONFIG_ERROR``: the input was unreachable (missing path,
  permission denied) so the audit never ran.
- ``2`` — ``EXIT_TRAINING_ERROR``: a required optional extra was missing.
- ``3`` — ``EXIT_EVAL_FAILURE``: the audit ran, wrote its report, and a
  critical-severity secrets finding failed the gate.  See
  :func:`_exit_on_critical_secrets` for why 3 and not a new code.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

from .._exit_codes import EXIT_CONFIG_ERROR, EXIT_EVAL_FAILURE, EXIT_TRAINING_ERROR
from .._logging import logger


def _exit_on_critical_secrets(verdict: dict, *, allow_secrets: bool) -> None:
    """Fail the process when the audit found critical-severity secrets.

    Why this gate exists
    --------------------
    ``forgelm audit`` used to print ``Secrets : CRITICAL — N flagged`` and
    exit ``0``.  Every CI pipeline wired up as a credential-leak gate was
    therefore dead on arrival: the step reported success while its own
    output said otherwise, which is exactly the silent failure the project
    principles outlaw.  Detecting a leaked key and then telling the
    pipeline everything is fine is worse than not scanning at all, because
    the operator believes they are covered.

    Why ``EXIT_EVAL_FAILURE`` (3)
    -----------------------------
    Weighed against the public contract (0 success, 1 config/caller error,
    2 training/runtime, 3 eval failure, 4 awaiting approval, 5 wizard
    cancelled, 6 integrity failure):

    - Not ``1`` — the invocation was well-formed and the input was
      readable.  Nothing about the *command* is wrong; conflating a
      credential leak with a typo'd path would make both unbranchable.
    - Not ``2`` — nothing crashed.  The audit completed and wrote its
      report; ``2`` is the runtime-fault class.
    - Not ``6`` — no artefact integrity claim was violated.  ``6`` means a
      hash that used to match no longer does (Annex IV manifest, audit-log
      chain, GGUF sidecar); training data was never signed.
    - ``3`` fits exactly: an evaluation gate examined an artefact and said
      no.  This is the same semantics as ``forgelm safety-eval``'s
      non-passing branch (``subcommands/_safety_eval.py``) and the
      retention ``block_on_excess`` gate.  A new public exit code is a
      permanent contract addition and this incident does not need one.

    ``allow_secrets`` is an explicit per-invocation escape hatch for the
    legitimate cases — a redaction workflow that audits *before* running
    ``forgelm ingest --secrets-mask``, or a fixture corpus that carries
    known dummy credentials.  It is deliberately not a config field and
    not an env var: it has to be typed into the command, where a reviewer
    reading the pipeline diff can see it.  The gate itself stays on by
    default, so silence is never the result of forgetting a flag.
    """
    if not verdict.get("failed"):
        return
    breakdown = ", ".join(f"{kind}={count}" for kind, count in verdict["critical_types"].items())
    if allow_secrets:
        logger.warning(
            "Secrets gate SUPPRESSED by --allow-secrets: %d critical credential/secret span(s) "
            "found (%s). The findings are recorded in the audit report; exiting 0 as requested.",
            verdict["critical_total"],
            breakdown,
        )
        return
    logger.error(
        "Secrets gate FAILED (critical): %d credential/secret span(s) detected (%s). "
        "Do not train on this corpus — a credential in training data is memorised and "
        "re-emitted at inference time. Scrub it with `forgelm ingest --secrets-mask`, or "
        "re-run `forgelm audit --allow-secrets` to record the findings without failing "
        "the pipeline. Exiting %d.",
        verdict["critical_total"],
        breakdown,
        EXIT_EVAL_FAILURE,
    )
    sys.exit(EXIT_EVAL_FAILURE)


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
    allow_secrets: bool = False,
) -> None:
    """Phase 11 / 11.5 / 12 dispatch: dataset quality + governance audit.

    Reached via the ``forgelm audit`` subcommand. (The legacy
    ``--data-audit`` flag that previously shared this worker was removed in
    v0.8.0; this helper stays single-purpose.)

    ``allow_secrets=True`` (CLI ``--allow-secrets``) suppresses the
    critical-secrets exit code only; detection, reporting and the on-disk
    report are unaffected.  See :func:`_exit_on_critical_secrets`.
    """
    from ...data_audit import (
        DEFAULT_MINHASH_JACCARD,
        DEFAULT_NEAR_DUP_HAMMING,
        audit_dataset,
        secrets_gate_verdict,
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

    # Computed before rendering so both output formats agree with the exit
    # code: the JSON envelope's ``success`` must never say True while the
    # process exits 3.
    gate = secrets_gate_verdict(report.secrets_summary)
    gate_failed = bool(gate["failed"]) and not allow_secrets

    if output_format == "json":
        # Stdout summary only — full report goes to disk under --output. A
        # multi-split audit can grow to tens of KB of JSON which would drown
        # downstream pipeline logs. Operators that want everything via stdout
        # can read the file path from `report_path` and slurp it.
        summary = {
            # False when a critical secrets finding gated the run, matching
            # the non-zero exit. Mirrors `forgelm safety-eval`, which already
            # sets ``success`` from its gate verdict rather than from "the
            # command completed". The audit still ran and ``report_path``
            # still points at a complete report either way.
            "success": not gate_failed,
            "report_path": str(Path(target) / "data_audit_report.json"),
            "generated_at": report.generated_at,
            "source_input": report.source_input,
            "total_samples": report.total_samples,
            "splits": {name: info.get("sample_count", 0) for name, info in report.splits.items()},
            "pii_summary": report.pii_summary,
            "pii_severity": report.pii_severity,
            "secrets_summary": report.secrets_summary,
            # Additive envelope key: the gate verdict behind the exit code.
            # ``status`` is "passed" (nothing critical), "failed" (exiting 3),
            # or "suppressed" (findings present, --allow-secrets passed).
            # A consumer that only reads ``secrets_summary`` is unaffected.
            "secrets_gate": {
                "status": (
                    "suppressed" if (gate["failed"] and allow_secrets) else ("failed" if gate_failed else "passed")
                ),
                "severity": gate["severity"],
                "critical_total": gate["critical_total"],
                "critical_types": gate["critical_types"],
                "allow_secrets": allow_secrets,
            },
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

    # Last — the report is on disk and rendered before the gate fires, so a
    # failing run still leaves the operator everything they need to triage.
    _exit_on_critical_secrets(gate, allow_secrets=allow_secrets)


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
        allow_secrets=getattr(args, "allow_secrets", False),
    )
