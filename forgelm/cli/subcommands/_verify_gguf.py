"""``forgelm verify-gguf`` — GGUF integrity check.

Phase 36 closure of GH-009.  The deployment-integrity counterpart to
``verify-annex-iv``: :func:`forgelm.verify.verify_gguf` validates the
4-byte magic header, optionally parses the metadata block (when the
``gguf`` Python package is installed), and checks a SHA-256 manifest
sidecar (``<model>.gguf.sha256``) when present.

This module is the CLI seam only — argument handling, I/O-error → exit-code
mapping, and the text / JSON output envelope.  The verifier itself lives in
:mod:`forgelm.verify` so library consumers reaching ``forgelm.verify_gguf``
never import the CLI layer.

Exit codes (per ``docs/standards/error-handling.md`` and the public
contract in ``docs/reference/verify_gguf_subcommand.md``):

- 0 — ``EXIT_SUCCESS``: magic OK, metadata parses, SHA-256 matches
  sidecar (when present).
- 1 — ``EXIT_CONFIG_ERROR``: caller / input error (missing path, not
  a regular file, magic mismatch — the file is not a GGUF at all —,
  malformed sidecar, non-UTF-8 sidecar).  Nothing was compared; the
  operator has to fix the command or the sidecar.
- 2 — ``EXIT_TRAINING_ERROR``: genuine runtime I/O failure on a
  reachable path (read error, permission denied mid-read, etc.).
- 6 — ``EXIT_INTEGRITY_FAILURE``: the file *is* a GGUF and it failed its
  integrity check — SHA-256 sidecar mismatch (modified after export) or a
  metadata block that could not be parsed (truncated / corrupted stream).
  Artifact is not safe to serve.
"""

from __future__ import annotations

import json
import os
import sys
from typing import NoReturn

from ...verify import (
    VerifyGgufResult,  # noqa: F401 — re-exported for the forgelm.cli facade
    is_gguf_integrity_failure,
    verify_gguf,  # noqa: F401 — re-exported for the forgelm.cli facade
)
from .._exit_codes import (
    EXIT_CONFIG_ERROR,
    EXIT_INTEGRITY_FAILURE,
    EXIT_SUCCESS,
    EXIT_TRAINING_ERROR,
)
from .._logging import logger


def _output_error_and_exit(output_format: str, msg: str, exit_code: int) -> NoReturn:
    if output_format == "json":
        # ``indent=2`` matches the success/result envelope below so this
        # subcommand emits one consistent JSON shape on every branch.
        print(json.dumps({"success": False, "error": msg}, indent=2))
    else:
        logger.error(msg)
    sys.exit(exit_code)


def _run_verify_gguf_cmd(args, output_format: str) -> None:
    """Top-level dispatcher for ``forgelm verify-gguf <path>``."""
    path = getattr(args, "path", None)
    if not path:
        _output_error_and_exit(
            output_format,
            "verify-gguf requires a path argument: `forgelm verify-gguf <model.gguf>`.",
            EXIT_CONFIG_ERROR,
        )
    # Round 6 absorption: replace the `os.path.isfile()` pre-check with
    # in-try exception dispatch so permission-denied on an existing
    # file routes to exit 2 instead of exit 1.  See _verify_annex_iv.py
    # for the same shape and the rationale.
    try:
        result = verify_gguf(path)
    except (FileNotFoundError, IsADirectoryError) as exc:
        # Caller-input error: the path is missing or refers to a
        # directory.  Exit 1 per the public contract.
        _output_error_and_exit(
            output_format,
            f"GGUF file not found or not a regular file: {path!r} ({exc.__class__.__name__}).",
            EXIT_CONFIG_ERROR,
        )
    except UnicodeDecodeError as exc:
        # A non-UTF-8 ``<model>.gguf.sha256`` sidecar (the only text-mode
        # read in verify_gguf; the model file itself is opened binary).
        # UnicodeDecodeError is a ValueError subclass, not an OSError
        # subclass, so without this branch a corrupted/binary sidecar
        # escaped the except chain and crashed with a raw traceback and
        # no JSON envelope.  Caller-input error → exit 1, matching the
        # malformed-sidecar branch inside verify_gguf and the same fix in
        # _verify_annex_iv.py / _verify_integrity.py.
        _output_error_and_exit(
            output_format,
            f"GGUF SHA-256 sidecar for {path!r} is not valid UTF-8: {exc}.",
            EXIT_CONFIG_ERROR,
        )
    except OSError as exc:
        # Genuine runtime I/O failure on a reachable path (permission
        # denied, mid-read I/O error, etc.).  Order matters because
        # FileNotFoundError is a subclass of OSError; the specific
        # caller-input subclasses MUST be caught above.
        _output_error_and_exit(
            output_format,
            f"Could not read GGUF file {path!r}: {exc}.",
            EXIT_TRAINING_ERROR,
        )

    payload = result.to_dict()
    payload["path"] = os.path.abspath(path)
    if output_format == "json":
        print(json.dumps({"success": result.valid, **payload}, indent=2))
    else:
        marker = "OK" if result.valid else "FAIL"
        print(f"{marker}: {path}")
        print(f"  {result.reason}")
        for k, v in result.checks.items():
            print(f"    {k}: {v}")
    if result.valid:
        sys.exit(EXIT_SUCCESS)
    # A checksum mismatch / corrupt metadata block on a real GGUF is a
    # tampering signal (6); a wrong-file or malformed-sidecar verdict is
    # operator-actionable input (1).
    sys.exit(EXIT_INTEGRITY_FAILURE if is_gguf_integrity_failure(result) else EXIT_CONFIG_ERROR)


__all__ = [
    "VerifyGgufResult",
    "_run_verify_gguf_cmd",
    "verify_gguf",
]
