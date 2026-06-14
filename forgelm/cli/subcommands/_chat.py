"""``forgelm chat`` dispatcher."""

from __future__ import annotations

import sys

from .._exit_codes import EXIT_TRAINING_ERROR
from .._logging import logger


def _run_chat_cmd(args) -> None:
    """Dispatch the ``forgelm chat`` subcommand."""
    try:
        from ...chat import run_chat
    except ImportError as e:
        logger.error("Missing dependency for chat: %s", e)
        sys.exit(EXIT_TRAINING_ERROR)

    try:
        run_chat(
            model_path=args.model_path,
            adapter=args.adapter,
            system_prompt=args.system,
            temperature=args.temperature,
            max_new_tokens=args.max_new_tokens,
            stream=not args.no_stream,
            load_in_4bit=args.load_in_4bit,
            load_in_8bit=args.load_in_8bit,
            trust_remote_code=args.trust_remote_code,
            backend=args.backend,
        )
    except Exception as e:  # noqa: BLE001 — best-effort: top-of-subcommand catch.  ``run_chat`` loads the model + tokenizer, optionally a PEFT adapter, and runs the REPL loop; failure surface includes OSError (model path), RuntimeError (CUDA), ValueError (tokenizer), plus chat-internal generation errors.  KeyboardInterrupt is a BaseException and is NOT caught here — an in-flight Ctrl-C propagates to the dispatcher's ``except KeyboardInterrupt`` (exit 2).  EXIT_TRAINING_ERROR is the documented public contract for a failed dispatched subcommand.  # NOSONAR
        logger.exception("Chat session failed: %s", e)
        sys.exit(EXIT_TRAINING_ERROR)
