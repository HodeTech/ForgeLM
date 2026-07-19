"""``forgelm verify-integrity`` — Art. 15 model-integrity verification.

The consuming counterpart to :func:`forgelm.compliance.generate_model_integrity`.
That writer computes a SHA-256 manifest (``model_integrity.json``) over every
file in a trained model directory; :func:`forgelm.verify.verify_integrity` reads
the manifest back, re-walks the directory, recomputes each file's SHA-256, and
reports any file that was **added**, **removed**, or **changed** since the
manifest was written.  Without it the Art. 15 section header ("Model Integrity
*Verification*") over-claimed — only the generate side shipped (F-P4-OPUS-14).

This module is the CLI seam only: argument handling, I/O-error → exit-code
mapping, and the text / JSON output envelope.  The verifier itself lives in
:mod:`forgelm.verify` so library consumers reaching ``forgelm.verify_integrity``
never import the CLI layer.

Exit codes (per ``docs/standards/error-handling.md`` and the public contract
in ``docs/reference/verify_integrity_subcommand.md``):

- 0 — ``EXIT_SUCCESS``: every recorded artifact present and unchanged, no
  unexpected extra files.
- 1 — ``EXIT_CONFIG_ERROR``: caller / input error (missing path, the path is a
  file rather than a model directory, manifest not found / not a regular file,
  malformed JSON, invalid UTF-8 encoding, a non-list ``artifacts`` container, a
  manifest entry whose path is non-string or escapes the model directory).  The
  manifest could not be used, so no artifact was ever compared.
- 2 — ``EXIT_TRAINING_ERROR``: genuine runtime I/O failure on a reachable path
  (read error, permission denied mid-walk, etc.).
- 6 — ``EXIT_INTEGRITY_FAILURE``: the manifest parsed and the walk ran, but at
  least one artifact was changed / removed / added.  The deployed weights are
  not the weights that were signed off — a security event, distinct from the
  operator-actionable input errors on 1.
"""

from __future__ import annotations

import json
import os
import sys
from typing import NoReturn

from ...verify import (
    VerifyIntegrityResult,  # noqa: F401 — re-exported for the forgelm.cli facade
    is_model_integrity_failure,
    verify_integrity,  # noqa: F401 — re-exported for the forgelm.cli facade
)
from .._exit_codes import (
    EXIT_CONFIG_ERROR,
    EXIT_INTEGRITY_FAILURE,
    EXIT_SUCCESS,
    EXIT_TRAINING_ERROR,
)
from .._logging import logger

_MANIFEST_NAME = "model_integrity.json"


def _output_error_and_exit(output_format: str, msg: str, exit_code: int) -> NoReturn:
    if output_format == "json":
        # indent=2 matches the success/result envelope below so this subcommand
        # emits one consistent JSON shape on every branch.
        print(json.dumps({"success": False, "error": msg}, indent=2))
    else:
        logger.error(msg)
    sys.exit(exit_code)


def _run_verify_integrity_cmd(args, output_format: str) -> None:
    """Top-level dispatcher for ``forgelm verify-integrity <model_dir>``."""
    path = getattr(args, "path", None)
    if not path:
        _output_error_and_exit(
            output_format,
            "verify-integrity requires a path argument: `forgelm verify-integrity <model_dir>`.",
            EXIT_CONFIG_ERROR,
        )

    try:
        result = verify_integrity(path)
    except FileNotFoundError as exc:
        # The model_integrity.json manifest is missing (or the model dir
        # does not exist).  Operator-actionable → exit 1.
        _output_error_and_exit(
            output_format,
            f"Integrity manifest not found: expected {os.path.join(path, _MANIFEST_NAME)!r} ({exc.__class__.__name__}).",
            EXIT_CONFIG_ERROR,
        )
    except json.JSONDecodeError as exc:
        _output_error_and_exit(
            output_format,
            f"Integrity manifest at {os.path.join(path, _MANIFEST_NAME)!r} is not valid JSON: {exc.msg} (line {exc.lineno}).",
            EXIT_CONFIG_ERROR,
        )
    except UnicodeDecodeError as exc:
        # A non-UTF-8 model_integrity.json (disk corruption, an
        # interrupted write, or a binary file pointed at by mistake).
        # UnicodeDecodeError is a ValueError subclass, not an OSError
        # subclass, so without this branch it escaped the except chain
        # and crashed with a raw traceback and no JSON envelope.  Caller-
        # input error → exit 1, mirroring the malformed-JSON branch above
        # and the same fix in _verify_annex_iv.py / _verify_gguf.py.
        _output_error_and_exit(
            output_format,
            f"Integrity manifest at {os.path.join(path, _MANIFEST_NAME)!r} is not valid UTF-8: {exc}.",
            EXIT_CONFIG_ERROR,
        )
    except IsADirectoryError as exc:
        _output_error_and_exit(
            output_format,
            f"Integrity manifest path is a directory, not a file: {os.path.join(path, _MANIFEST_NAME)!r} ({exc.__class__.__name__}).",
            EXIT_CONFIG_ERROR,
        )
    except NotADirectoryError as exc:
        # The supplied path is a regular file, not a model directory, so
        # joining the manifest name and opening it raises NotADirectoryError.
        # This is caller input (wrong argument) → exit 1, not a runtime I/O
        # failure that the generic OSError branch would map to exit 2.
        _output_error_and_exit(
            output_format,
            f"verify-integrity expects a model directory, not a file: {path!r} ({exc.__class__.__name__}).",
            EXIT_CONFIG_ERROR,
        )
    except OSError as exc:
        # Genuine runtime I/O failure on a reachable path (permission
        # denied mid-walk, mid-read I/O error).  Order matters because
        # the caller-input subclasses above are subclasses of OSError.
        _output_error_and_exit(
            output_format,
            f"Could not verify model integrity for {path!r}: {exc}.",
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
        for rel in result.changed:
            print(f"    changed: {rel}")
        for rel in result.removed:
            print(f"    removed: {rel}")
        for rel in result.added:
            print(f"    added:   {rel}")
    if result.valid:
        sys.exit(EXIT_SUCCESS)
    # Artifacts that disagree with a usable manifest are a tampering
    # signal (6); an unusable manifest is operator-actionable input (1).
    sys.exit(EXIT_INTEGRITY_FAILURE if is_model_integrity_failure(result) else EXIT_CONFIG_ERROR)


__all__ = [
    "VerifyIntegrityResult",
    "_run_verify_integrity_cmd",
    "verify_integrity",
]
