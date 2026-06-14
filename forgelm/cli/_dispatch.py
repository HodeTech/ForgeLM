"""Top-level dispatcher + ``main()`` entry point.

Subcommand dispatchers are looked up via the package facade
(:mod:`forgelm.cli`) at call time so test monkeypatches against
``forgelm.cli._run_*_cmd`` resolve correctly.
"""

from __future__ import annotations

import json
import sys
import warnings

from ._config_load import _apply_offline_flag, _load_config_or_exit
from ._exit_codes import EXIT_CONFIG_ERROR, EXIT_SUCCESS, EXIT_TRAINING_ERROR, _clamp_exit_code
from ._logging import _setup_logging, logger
from ._no_train_modes import _maybe_run_no_train_mode
from ._parser import parse_args
from ._training import _run_training_pipeline
from ._wizard import _maybe_run_wizard


def _output_format_for(args) -> str:
    """Pull the ``--output-format`` value off ``args`` with the standard default."""
    return getattr(args, "output_format", "text")


def _dispatch_subcommand(command: str, args) -> None:
    """Run a Phase 10 / 10.5 / 11 / 11.5 / Wave 2a subcommand and exit.

    Handles every subcommand registered in the ``table`` literal below
    (the authoritative, drift-proof list — adding a subcommand is a
    single-row edit there).  Each terminates the process via ``sys.exit``
    after its own dispatcher returns — the trainer code path never runs
    when a subcommand is in play.

    Dispatchers are looked up via the package facade so tests that
    ``patch("forgelm.cli._run_*_cmd", ...)`` see their mock invoked.
    The dispatch table replaced an if/elif chain in Wave 2a Round-2 to
    drop SonarCloud S3776 cognitive complexity (was 16, ceiling 15) and
    to keep the registry literal — adding a new subcommand is now a
    single-row edit.

    **SIGINT exit-code policy (Round-3 + Round-5 reconciliation):**

    The dispatcher catches ``KeyboardInterrupt`` and exits with
    ``EXIT_TRAINING_ERROR`` (= 2, "runtime-error class") so CI/CD
    branches on a documented public code, not Python's shell-shaped
    ``130`` (= ``128 + SIGINT``).  Two subcommands have nuance the
    blanket policy does not capture:

    - ``chat`` REPL catches ``KeyboardInterrupt`` at its input prompt
      itself (``forgelm/chat.py:125``), prints ``[Goodbye]``, and
      returns normally — exit ``EXIT_SUCCESS`` (0) by REPL design.  An
      in-flight ``KeyboardInterrupt`` *during* generation bubbles past
      the REPL and lands on this dispatcher's catch → 2.
    - ``verify-audit`` is wrapped in the same try/except as the dict-
      table dispatch (Round-5 fix), so ``SIGINT`` during a long
      verify-of-100K-events lands on 2 just like the others.  Returns
      its own exit code on success (the only dispatcher that does so).

    Returned exit codes (verify-audit and the pipeline path) pass through
    :func:`_clamp_exit_code`, so a computed or signal-derived code is
    coerced to ``EXIT_TRAINING_ERROR`` rather than leaking verbatim —
    making the ``_exit_codes`` module-docstring invariant real at this
    seam (F-P7-OPUS-29).
    """
    # Late import via the package facade so monkeypatched
    # ``forgelm.cli._run_*_cmd`` references resolve correctly.
    from forgelm import cli as _cli_facade

    # name -> dispatcher attribute on the package facade.  Resolved lazily
    # so test-time monkeypatches against ``forgelm.cli._run_*_cmd`` are
    # honoured.  ``verify-audit`` is in the table but takes a different
    # call shape (returns int exit code instead of sys.exit-ing) — see
    # the special-case branch below.
    table = {
        "chat": "_run_chat_cmd",
        "export": "_run_export_cmd",
        "deploy": "_run_deploy_cmd",
        "quickstart": "_run_quickstart_cmd",
        "ingest": "_run_ingest_cmd",
        "audit": "_run_audit_cmd",
        "doctor": "_run_doctor_cmd",
        "verify-audit": "_run_verify_audit_cmd",
        "approve": "_run_approve_cmd",
        "reject": "_run_reject_cmd",
        "approvals": "_run_approvals_cmd",
        "purge": "_run_purge_cmd",
        "reverse-pii": "_run_reverse_pii_cmd",
        "cache-models": "_run_cache_models_cmd",
        "cache-tasks": "_run_cache_tasks_cmd",
        "verify-annex-iv": "_run_verify_annex_iv_cmd",
        "safety-eval": "_run_safety_eval_cmd",
        "verify-gguf": "_run_verify_gguf_cmd",
        "verify-integrity": "_run_verify_integrity_cmd",
    }
    dispatcher_name = table.get(command)
    if dispatcher_name is None:
        logger.error("Unrecognized subcommand: %r. This is a bug — please report it.", command)
        sys.exit(EXIT_TRAINING_ERROR)
    dispatcher = getattr(_cli_facade, dispatcher_name)

    # Three call shapes: ``chat`` takes only ``args`` (its REPL handles
    # output formatting itself); ``verify-audit`` returns an exit code
    # instead of sys.exit-ing; everything else takes ``(args,
    # output_format)`` and exits internally.  All three flow through
    # the same KeyboardInterrupt handler so SIGINT lands on the
    # documented public exit-code contract regardless of subcommand
    # (Round-5 F-R5-02 fix — verify-audit was previously special-cased
    # outside the try/except, leaving SIGINT during a long verify to
    # bubble up as Python's shell-shaped 130).
    output_format = _output_format_for(args)
    try:
        if command == "chat":
            dispatcher(args)
        elif command == "verify-audit":
            sys.exit(_clamp_exit_code(dispatcher(args)))
        else:
            dispatcher(args, output_format)
    except KeyboardInterrupt:
        sys.exit(EXIT_TRAINING_ERROR)
    sys.exit(EXIT_SUCCESS)


def _force_utf8_console_streams() -> None:
    """Promote stdout/stderr to UTF-8 on Windows consoles.

    ForgeLM's CLI help text and prose use Unicode arrows / dashes
    (``→``, ``—``).  The Windows console default codec (``cp1252``)
    cannot encode these characters and ``argparse``'s ``--help``
    print path raises ``UnicodeEncodeError`` mid-write, propagating
    through subprocess-based test runners as a non-zero exit before
    the help text actually finishes printing.  ``reconfigure`` is a
    no-op on POSIX (stdout is already utf-8) and on Windows it flips
    the stream encoding so the cp1252 fallback never triggers.
    ``errors="replace"`` is a belt-and-braces fallback so a future
    code-point we miss prints as ``?`` instead of crashing.

    Python 3.7+ guarantees ``reconfigure`` exists on text streams
    (``TextIOWrapper``); when the CLI is invoked with a buffer
    type that lacks it (rare embedded use), the call is skipped.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                # OSError: stream not a tty or already detached.
                # ValueError: caller already set conflicting state.
                # Both are non-fatal — fall back to whatever encoding
                # the platform default chose.
                pass


def main():
    # PR #29 F-CLI-01: wrap the entire entry path in the same SIGINT
    # contract as the subcommand dispatcher.  Without this, a Ctrl-C
    # struck while ``parse_args()`` is constructing argparse help text,
    # validating a long --workers integer, walking through the
    # interactive wizard, or loading the YAML config bubbles up to
    # Python's default handler and exits with shell-shaped 130
    # (= 128+SIGINT) — outside the documented public 0/1/2/3/4 surface
    # and surprising to CI/CD scripts that branch on exit code 2.
    _force_utf8_console_streams()
    try:
        return _main_inner()
    except KeyboardInterrupt:
        sys.exit(EXIT_TRAINING_ERROR)


def _dispatch_legacy_data_audit(args) -> None:
    """Handle ``forgelm --data-audit PATH`` (deprecated flag).

    Pulled out of ``_main_inner`` for Sonar python:S3776 cognitive-
    complexity hygiene.  Emits the standard deprecation
    ``DeprecationWarning`` + an audit-log breadcrumb (best-effort), then
    delegates to the canonical ``_run_data_audit`` and ``sys.exit``s.
    Calls back to ``_main_inner`` are impossible — this function always
    terminates the process.
    """
    json_output = args.output_format == "json"
    log_level = "WARNING" if args.quiet else args.log_level
    _setup_logging(log_level, json_format=json_output)
    # Phase 13 (Faz 13): emit a structured Python ``DeprecationWarning``
    # so `pytest -W error::DeprecationWarning` and `python -Wd` tooling
    # surface it, plus an append-only audit-log event so operators who
    # only read the JSONL trail see the migration signal too. Cadence
    # for the v0.8.0 removal follows the "Deprecation cadence" section
    # in ``docs/standards/release.md`` (one-minor warning window
    # minimum).  Originally targeted v0.7.0; pushed one minor out to
    # v0.8.0 at the v0.7.0 cut to preserve the one-minor warning
    # window for operators who only upgrade once per minor release.
    warnings.warn(
        "`forgelm --data-audit PATH` is deprecated and will be removed "
        "in v0.8.0. Use the `forgelm audit PATH` subcommand instead — "
        "same behaviour, same output. "
        "See docs/standards/release.md#deprecation-cadence for the removal timeline.",
        DeprecationWarning,
        stacklevel=2,
    )
    # Audit-log event: append-only Article 12 record of the legacy
    # invocation so compliance reviewers can prove the operator was
    # warned.  Best-effort — a read-only output dir must not abort the
    # actual audit run.
    legacy_target = args.output or "./audit"
    try:
        from ..compliance import AuditLogger
        from ..config import ConfigError

        AuditLogger(legacy_target).log_event(
            "cli.legacy_flag_invoked",
            flag="--data-audit",
            replacement="forgelm audit",
            version="v0.8.0 removal",
        )
    except (OSError, ConfigError) as audit_exc:
        logger.warning(
            "Failed to emit deprecation audit event to %s: %s",
            legacy_target,
            audit_exc,
        )
    # Late import via the package facade so monkeypatched
    # ``forgelm.cli._run_data_audit`` references resolve correctly.
    from forgelm import cli as _cli_facade

    # The deprecation warning, the parser help, and the helper docstring all
    # promise "same behaviour, same output" as `forgelm audit PATH`.  The
    # subcommand defaults --quality-filter ON (v0.6.0+), so the legacy alias
    # must pass enable_quality_filter=True to keep that promise — otherwise it
    # falls back to the helper's opt-in default (False) and emits a
    # quality_summary-less data_audit_report.json (F-P7-OPUS-01).
    _cli_facade._run_data_audit(
        args.data_audit,
        args.output,
        args.output_format,
        enable_quality_filter=True,
    )
    sys.exit(EXIT_SUCCESS)


def _dispatch_pipeline_mode(config, args) -> None:
    """Handle a config that carries a ``pipeline:`` block.

    Pulled out of ``_main_inner`` for Sonar python:S3776 cognitive-
    complexity hygiene.  Re-reads the YAML *bytes* (not the parsed
    semantic content — regulators audit the on-disk artefact) and
    routes to :func:`forgelm.cli._pipeline.run_pipeline_from_args`.
    Always terminates the process via ``sys.exit``.

    Layering rule (Phase 14 F-B-1): this branch MUST run before
    ``_maybe_run_no_train_mode``.  The single-stage no-train modes
    (``--dry-run``, ``--fit-check``, ``--benchmark-only``,
    ``--merge``, ``--generate-data``, ``--compliance-export``)
    ``sys.exit`` internally; if they ran first on a pipeline config,
    the orchestrator would never reach the multi-stage validation path
    and the documented ``--dry-run`` per-stage error-collection
    contract would be silently violated.
    """
    from ._pipeline import run_pipeline_from_args

    try:
        with open(args.config, "rb") as f:
            pipeline_yaml_bytes = f.read()
    except OSError as e:
        logger.error("Failed to re-read pipeline YAML for hashing: %s", e)
        sys.exit(EXIT_CONFIG_ERROR)

    sys.exit(_clamp_exit_code(run_pipeline_from_args(config, pipeline_yaml_bytes, args)))


def _main_inner() -> None:
    """Entry-point body — extracted so ``main`` can wrap a single
    ``KeyboardInterrupt`` handler around every step from argparse to
    training pipeline launch."""
    args = parse_args()

    # Phase 10 subcommand dispatch — no --config required.
    command = getattr(args, "command", None)
    if command is not None:
        json_output = getattr(args, "output_format", "text") == "json"
        log_level = "WARNING" if getattr(args, "quiet", False) else getattr(args, "log_level", "INFO")
        _setup_logging(log_level, json_format=json_output)
        _dispatch_subcommand(command, args)

    # --data-audit operates on a JSONL file/directory only — no config needed.
    # Run before the config-required check so operators can audit raw data
    # without writing a YAML. Phase 11.5 promoted the same code path to
    # `forgelm audit PATH` (a real subcommand); the legacy flag is preserved
    # as an alias and slated for removal in v0.8.0.  (Removal originally
    # targeted v0.7.0; pushed one minor out at the v0.7.0 cut to preserve
    # the documented one-minor warning window — see the matching
    # DeprecationWarning message + audit-event ``version`` field below
    # and ``docs/standards/release.md#deprecation-cadence``.)
    if getattr(args, "data_audit", None):
        _dispatch_legacy_data_audit(args)

    _maybe_run_wizard(args)

    if not args.config:
        if getattr(args, "output_format", "text") == "json":
            print(json.dumps({"success": False, "error": "--config is required."}))
        else:
            print("Error: --config is required. Use --help for usage.", file=sys.stderr)
        sys.exit(EXIT_CONFIG_ERROR)

    json_output = args.output_format == "json"
    log_level = "WARNING" if args.quiet else args.log_level
    _setup_logging(log_level, json_format=json_output)

    config = _load_config_or_exit(args.config, json_output)
    _apply_offline_flag(config, args.offline)

    if config.pipeline is not None:
        _dispatch_pipeline_mode(config, args)

    # Phase 14 post-release review: pipeline-only flags must not
    # silently route through the single-stage training path.  Pre-fix,
    # ``forgelm --config single_stage.yaml --stage dpo_stage`` would
    # ignore ``--stage`` and run the root YAML's trainer — surprising
    # the operator who expected the flag to be load-bearing.
    _PIPELINE_ONLY_FLAGS = (
        ("stage", "--stage"),
        ("resume_from", "--resume-from"),
        ("force_resume", "--force-resume"),
        ("input_model", "--input-model"),
    )
    for attr, flag_name in _PIPELINE_ONLY_FLAGS:
        if getattr(args, attr, None):
            logger.error(
                "`%s` requires a config with a `pipeline:` block — this is a "
                "multi-stage orchestrator flag.  Either add `pipeline:` to "
                "the YAML or remove the flag.",
                flag_name,
            )
            sys.exit(EXIT_CONFIG_ERROR)

    # Single-stage path — unchanged from v0.6.0.
    _maybe_run_no_train_mode(config, args)
    _run_training_pipeline(config, args, json_output)
