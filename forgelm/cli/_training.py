"""Training-mode pipeline (model → data → trainer.train → cleanup)."""

from __future__ import annotations

import json
import sys

from ._exit_codes import EXIT_AWAITING_APPROVAL, EXIT_EVAL_FAILURE, EXIT_SUCCESS, EXIT_TRAINING_ERROR
from ._logging import logger
from ._result import _output_result
from ._resume import _resolve_resume_checkpoint


def _report_training_error(
    json_output: bool, payload: dict, log_msg: str, exit_code: int, *, with_traceback: bool = False
) -> None:
    """Emit a training-pipeline error and exit with *exit_code*.

    Centralizes the "JSON to stdout vs human log message" split. Pass
    ``with_traceback=True`` for unexpected exceptions where the stack trace
    is more useful than a one-line message.
    """
    if with_traceback:
        logger.exception(log_msg)
    else:
        logger.error(log_msg)
    if json_output:
        print(json.dumps(payload))
    sys.exit(exit_code)


def _preflight_numpy_torch_abi(json_output: bool) -> None:
    """Fail fast on a known torch / NumPy ABI mismatch.

    The v0.5.7 ``pyproject.toml`` PEP 508 marker pins ``numpy<2`` on
    Intel Mac (the platform where ``torch<2.3`` + ``numpy>=2`` produces
    the cryptic ``_ARRAY_API not found`` / ``NameError: _C not defined``
    failure mode), but the marker only fires on fresh installs and
    ``pip install -U`` re-resolves.  A user who upgraded numpy
    out-of-band after installing ForgeLM ends up with a silently broken
    torch — and previously hit the failure deep inside training with no
    actionable error.

    This preflight runs the same probe as ``forgelm doctor``'s
    ``numpy.torch_abi`` check and aborts with the exact remediation
    command if the mismatch is present.  No-op on healthy platforms
    (Linux, Apple Silicon, fresh-install Intel Mac), so zero false
    positives.

    Any unexpected exception from the probe itself (a corrupted torch
    install where ``torch.__version__`` raises ``AttributeError``, for
    instance) converts into a structured ``abi_preflight_crashed``
    envelope so the operator never sees a raw Python traceback
    pre-empt the JSON contract — matching the rest of the CLI's
    "every failure path carries a JSON envelope" rule per
    ``docs/standards/error-handling.md``.
    """
    from ._abi_check import ABI_BROKEN, compute_numpy_torch_abi_status, format_abi_remediation

    try:
        status, torch_version, numpy_version = compute_numpy_torch_abi_status()
    except Exception as e:  # noqa: BLE001 — see docstring above: preflight
        # is a sentinel that must NEVER let an unexpected exception escape
        # to the user as a raw Python traceback.  CodeRabbit round-5
        # absorption: wrap the probe so the JSON envelope contract holds
        # even when the underlying torch / numpy install is corrupted
        # enough that ``compute_numpy_torch_abi_status`` itself raises.
        _report_training_error(
            json_output,
            payload={
                "success": False,
                "error": "abi_preflight_crashed",
                "exception_class": type(e).__name__,
                "exception_message": str(e),
                "remediation": (
                    "The torch/NumPy ABI preflight crashed unexpectedly. This "
                    "usually indicates a corrupted torch or numpy install. Run "
                    "`forgelm doctor` for the full environment diagnostic."
                ),
            },
            log_msg=(
                f"ABI preflight crashed unexpectedly ({type(e).__name__}: {e}). Run `forgelm doctor` for diagnostics."
            ),
            exit_code=EXIT_TRAINING_ERROR,
            with_traceback=True,
        )
        return

    if status != ABI_BROKEN:
        return
    remediation = format_abi_remediation(torch_version, numpy_version)
    _report_training_error(
        json_output,
        payload={
            "success": False,
            "error": "numpy_torch_abi_mismatch",
            "torch_version": torch_version,
            "numpy_version": numpy_version,
            "remediation": remediation,
        },
        log_msg=f"Aborting before training: {remediation}",
        exit_code=EXIT_TRAINING_ERROR,
    )


def _run_training_pipeline(config, args, json_output: bool) -> None:
    """Run the full training pipeline (model load → data → trainer.train → cleanup)."""
    # Preflight: detect Intel-Mac-style torch/NumPy ABI mismatch BEFORE
    # the heavy imports below would surface it as a cryptic NameError
    # mid-pipeline.  No-op on healthy platforms.
    _preflight_numpy_torch_abi(json_output)

    # Defer heavy imports so `--help`, `--version`, and `--dry-run` stay lightweight.
    # ImportError here means a required optional extra is missing — surface as install hint.
    try:
        from ..data import prepare_dataset
        from ..model import get_model_and_tokenizer
        from ..trainer import ForgeTrainer
        from ..utils import manage_checkpoints, setup_authentication
    except ImportError as e:
        _report_training_error(
            json_output,
            payload={"success": False, "error": f"Missing dependency: {e}"},
            log_msg=f"Missing dependency: {e}. Check your installation.",
            exit_code=EXIT_TRAINING_ERROR,
        )

    try:
        if not config.model.offline:
            setup_authentication(config.auth.hf_token if config.auth else None)
        else:
            logger.info("Skipping HF authentication (offline mode).")

        model, tokenizer = get_model_and_tokenizer(config)
        dataset = prepare_dataset(config, tokenizer)

        resume_checkpoint = None
        if args.resume:
            resume_checkpoint = _resolve_resume_checkpoint(config.training.output_dir, args.resume)

        trainer = ForgeTrainer(model=model, tokenizer=tokenizer, config=config, dataset=dataset)
        result = trainer.train(resume_from_checkpoint=resume_checkpoint)

        logger.info("Preserving intermediate checkpoints (action=keep).")
        manage_checkpoints(config.training.output_dir, action="keep")

        _output_result(result, args.output_format)
        # ``awaiting_approval`` is the authoritative discriminator (set only when
        # the gate fired). Route on it rather than the bare config flag: a run
        # whose post-train gate auto-reverted has success=False and falls through
        # to EXIT_EVAL_FAILURE even when require_human_approval was configured.
        if result.success and result.awaiting_approval:
            sys.exit(EXIT_AWAITING_APPROVAL)
        sys.exit(EXIT_SUCCESS if result.success else EXIT_EVAL_FAILURE)

    except Exception as e:  # noqa: BLE001 — best-effort: top-of-CLI training catch.  ForgeTrainer.train() crosses every concern (HF model load, dataset load, TRL trainer, safety eval, judge eval, audit emission, compliance export, webhook notify); any leak from the inner narrow-class catches must surface as a structured CLI failure (with traceback in the log) rather than a Python traceback dumped to stdout that breaks JSON parsers.  # NOSONAR
        _report_training_error(
            json_output,
            payload={"success": False, "error": str(e)},
            log_msg="Training pipeline failed.",
            exit_code=EXIT_TRAINING_ERROR,
            with_traceback=True,
        )
