"""``forgelm quickstart`` dispatcher (template → config → train → chat)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .._exit_codes import _PUBLIC_EXIT_CODES, EXIT_CONFIG_ERROR, EXIT_SUCCESS, EXIT_TRAINING_ERROR
from .._logging import _CLI_MODULE, logger


def _build_quickstart_inherited_flags(args) -> tuple[list[str], list[str]]:
    """Return (train_flags, chat_flags) propagated from parent argv.

    Both lists carry --quiet / --log-level / --offline. Neither list
    carries --output-format json: the quickstart parent owns the JSON
    envelope, and forwarding it to the training subprocess produces two
    top-level JSON objects on stdout (subprocess result + parent envelope)
    making the stream unparseable by any JSON consumer.
    """
    train_flags: list[str] = []
    chat_flags: list[str] = []
    if getattr(args, "quiet", False):
        train_flags.append("--quiet")
        chat_flags.append("--quiet")
    log_level = getattr(args, "log_level", None)
    if log_level:
        train_flags += ["--log-level", log_level]
        chat_flags += ["--log-level", log_level]
    if getattr(args, "offline", False):
        train_flags.append("--offline")
        chat_flags.append("--offline")
    return train_flags, chat_flags


def _emit_quickstart_list(output_format: str) -> None:
    """Print the registered quickstart templates and exit (text or JSON)."""
    from ...quickstart import format_template_list, list_templates

    if output_format == "json":
        payload = [
            {
                "name": t.name,
                "title": t.title,
                "description": t.description,
                "primary_model": t.primary_model,
                "fallback_model": t.fallback_model,
                "trainer_type": t.trainer_type,
                "estimated_minutes": t.estimated_minutes,
                "min_vram_for_primary_gb": t.min_vram_for_primary_gb,
                "bundled_dataset": t.bundled_dataset,
                "license_note": t.license_note,
            }
            for t in list_templates()
        ]
        # Wrap in the universal success envelope so consumers can branch on
        # the single ``success`` key (json-output.md "Common conventions"),
        # matching the sibling list command ``approvals --pending`` rather
        # than emitting a bare top-level array.
        print(json.dumps({"success": True, "templates": payload, "count": len(payload)}, indent=2))
    else:
        print(format_template_list())


def _emit_quickstart_result(result, output_format: str, *, chat_launched: bool = False) -> None:
    """Print the quickstart-generation summary (JSON envelope or text).

    ``chat_launched`` records whether the interactive chat REPL was
    auto-launched.  It is always ``False`` in JSON mode — interactive chat
    has no place in a machine-readable run — so consumers can rely on the
    field rather than inferring stream contents.
    """
    from ...quickstart import summarize_result

    if output_format == "json":
        print(
            json.dumps(
                {
                    "success": True,
                    "template": result.template.name,
                    "config_path": str(result.config_path),
                    "model": result.chosen_model,
                    "dataset": result.dataset_path,
                    "selection_reason": result.selection_reason,
                    "dry_run": result.dry_run,
                    "chat_launched": chat_launched,
                    "notes": result.extra_notes,
                },
                indent=2,
            )
        )
    else:
        print(summarize_result(result))


def _load_quickstart_train_paths(config_path: Path) -> tuple[str, str]:
    """Read the generated YAML and return ``(output_dir, final_model_dir)``.

    Kept tiny + standalone so quickstart never has to import the heavy config
    validation pipeline just to find the trained checkpoint directory.
    """
    import yaml

    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    training = cfg.get("training", {}) or {}
    return (
        training.get("output_dir", "./checkpoints"),
        training.get("final_model_dir", "final_model"),
    )


def _run_quickstart_train_subprocess(args, config_path: Path) -> int:
    """Spawn `forgelm --config <generated>` as a child process and return a mapped exit code.

    Returns the documented public exit code (0/1/2/3/4) — never the raw
    subprocess returncode. Signal-derived codes like 137 (SIGKILL) or 139
    (SIGSEGV) collapse to ``EXIT_TRAINING_ERROR`` so they do not leak out
    of the public CLI contract.

    Returning instead of calling ``sys.exit`` lets the parent dispatcher
    own the JSON failure envelope: a JSON consumer must see one structured
    ``{"success": false, ...}`` document on stdout, not an unannotated
    non-zero exit + bare log line.
    """
    import subprocess  # nosec B404 — argv-list usage only

    inherited, _ = _build_quickstart_inherited_flags(args)
    train_cmd = [sys.executable, "-m", _CLI_MODULE, *inherited, "--config", os.path.abspath(config_path)]
    logger.info("Starting training: %s", " ".join(train_cmd))
    # Security justification (Codacy / Bandit B603 / ruff S603):
    # - argv is a fixed list, not a shell string — shell=False is implicit.
    # - argv[0] is sys.executable (the running Python), not a user-controlled
    #   command name. argv[1] is "-m" with the literal _CLI_MODULE constant.
    # - The only user-influenced segment is `os.path.abspath(config_path)`,
    #   which is passed verbatim as a single argv element (no shell expansion,
    #   no string concatenation).
    # → No command-injection or shell-metachar surface. Safe to ignore.
    try:
        train_rc = subprocess.run(train_cmd, check=False).returncode  # noqa: S603  # nosec B603
    except OSError as exc:
        logger.error("Failed to launch training subprocess: %s", exc)
        return EXIT_TRAINING_ERROR
    if train_rc != 0:
        logger.error("Training exited with code %d", train_rc)
        return train_rc if train_rc in _PUBLIC_EXIT_CODES else EXIT_TRAINING_ERROR
    return EXIT_SUCCESS


def _run_quickstart_chat_subprocess(args, config_path: Path) -> None:
    """Auto-launch `forgelm chat <trained-model>` after a successful training run."""
    import subprocess  # nosec B404 — argv-list usage only

    # Re-import via the package facade so monkeypatched
    # ``forgelm.cli._load_quickstart_train_paths`` references resolve.
    from forgelm import cli as _cli_facade

    output_dir, final_subdir = _cli_facade._load_quickstart_train_paths(config_path)
    final_model_dir = Path(output_dir) / final_subdir
    if not final_model_dir.is_dir():
        logger.warning(
            "Skipping auto-chat: trained model directory not found at %s. Run `forgelm chat <model_path>` manually.",
            final_model_dir,
        )
        return

    _, inherited_chat = _build_quickstart_inherited_flags(args)
    chat_cmd = [sys.executable, "-m", _CLI_MODULE, *inherited_chat, "chat", os.path.abspath(final_model_dir)]
    logger.info("Launching chat REPL: %s", " ".join(chat_cmd))
    # Same security justification as the training subprocess above:
    # argv list-form, sys.executable head, _CLI_MODULE literal, only the
    # final_model_dir is dynamic and passed as a single argv element. No
    # shell, no concatenation → no injection surface.
    try:
        chat_rc = subprocess.run(chat_cmd, check=False).returncode  # noqa: S603  # nosec B603
    except OSError as exc:
        logger.warning("Failed to launch chat REPL (%s); run `forgelm chat <model_path>` manually.", exc)
        return
    # 130 == SIGINT (Ctrl-C is the normal way to leave the REPL). Anything else
    # non-zero is a crash worth surfacing, but chat exit is not the operator's
    # training-success signal so we still exit 0 — the training run already
    # succeeded by the time we got here.
    if chat_rc not in (0, 130):
        logger.warning("Chat subprocess exited with code %d", chat_rc)


def _run_quickstart_train_then_chat(args, result, output_format: str) -> None:
    """Run training then (unless --no-chat) auto-launch the chat REPL.

    Extracted from ``_run_quickstart_cmd`` so the dispatcher stays a flat
    sequence of steps.  When training fails, emit the JSON failure envelope
    (so machine consumers see a structured error) and exit with the mapped
    code; when training succeeds, return so the caller can emit the success
    envelope.
    """
    # Spec: invoke training automatically. Use a subprocess so each phase keeps
    # its own clean process state and Ctrl-C is honoured cleanly.
    train_rc = _run_quickstart_train_subprocess(args, result.config_path)
    if train_rc != EXIT_SUCCESS:
        if output_format == "json":
            print(
                json.dumps(
                    {
                        "success": False,
                        "error": f"Training subprocess failed with exit code {train_rc}.",
                        "exit_code": train_rc,
                        "template": result.template.name,
                        "config_path": str(result.config_path),
                    },
                    indent=2,
                )
            )
        sys.exit(train_rc)

    if args.no_chat:
        return

    # In JSON mode the parent emits a machine-readable success envelope on
    # stdout; the chat REPL writes its banner/prompts/replies to stdout too,
    # which would interleave human prose with the JSON document and break
    # any parser (logging-observability.md: "JSON goes to stdout only.
    # Never mix."). Interactive chat is a human-UX affordance, so suppress
    # it in JSON mode — the envelope's ``chat_launched: false`` tells the
    # consumer it was skipped.
    if output_format == "json":
        return

    _run_quickstart_chat_subprocess(args, result.config_path)


def _run_quickstart_cmd(args, output_format: str) -> None:
    """Dispatch the ``forgelm quickstart`` subcommand.

    Three flows: ``--list`` (print templates and exit); plain
    ``forgelm quickstart TEMPLATE`` (generate config + auto-train + auto-chat);
    ``--dry-run`` (generate config, print next step, do not train).
    """
    from ...quickstart import run_quickstart

    if args.list:
        _emit_quickstart_list(output_format)
        sys.exit(EXIT_SUCCESS)

    if not args.template:
        err = "forgelm quickstart: TEMPLATE is required (or pass --list to see the menu)."
        if output_format == "json":
            print(json.dumps({"success": False, "error": err}))
        else:
            logger.error(err)
        sys.exit(EXIT_CONFIG_ERROR)

    try:
        result = run_quickstart(
            args.template,
            model_override=args.model,
            dataset_override=args.dataset,
            output_path=args.output,
            dry_run=args.dry_run,
        )
    except (ValueError, OSError) as e:
        # ValueError covers invalid template / model / dataset arguments.
        # OSError covers the full filesystem failure surface raised by
        # ``run_quickstart`` and helpers like ``_resolve_dataset``:
        # FileNotFoundError (missing template/dataset), FileExistsError (refuses
        # to clobber a populated --output dir), PermissionError (read-only
        # target), IsADirectoryError, and other I/O failures all reach here
        # with the same actionable error path instead of a Python traceback.
        if output_format == "json":
            print(json.dumps({"success": False, "error": str(e)}))
        else:
            logger.error("Quickstart failed: %s", e)
        sys.exit(EXIT_CONFIG_ERROR)

    # Text mode: emit before training so humans see the config summary immediately.
    # JSON mode non-dry-run: defer until after training so {"success": True} is
    # never printed before we know training actually succeeded.
    if output_format != "json":
        _emit_quickstart_result(result, output_format)

    if result.dry_run:
        if output_format == "json":
            _emit_quickstart_result(result, output_format)
        sys.exit(EXIT_SUCCESS)

    _run_quickstart_train_then_chat(args, result, output_format)

    # Training + chat completed without error.  Emit JSON summary now that
    # outcome is confirmed; text mode already emitted above.
    if output_format == "json":
        _emit_quickstart_result(result, output_format)
    sys.exit(EXIT_SUCCESS)
