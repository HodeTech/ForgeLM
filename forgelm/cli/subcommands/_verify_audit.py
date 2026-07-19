"""``forgelm verify-audit`` dispatcher (Phase 6 closure plan)."""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional

from .._exit_codes import (
    EXIT_CONFIG_ERROR,
    EXIT_INTEGRITY_FAILURE,
    EXIT_SUCCESS,
    EXIT_TRAINING_ERROR,
)


def _emit_usage_error(output_format: str, msg: str) -> None:
    """Print an option/usage-error message in the requested format.

    Mirrors the 2-key ``{"success": false, "error": ...}`` envelope from
    ``docs/standards/error-handling.md`` ("What errors look like in JSON
    output") — JSON goes to stdout so CI pipelines get one ``json.loads``-
    able object; text goes to stderr like every other CLI error path.
    """
    if output_format == "json":
        print(json.dumps({"success": False, "error": msg}, indent=2))
    else:
        print(f"ERROR: {msg}", file=sys.stderr)


def _probe_log_readable(log_path: str, output_format: str) -> Optional[int]:
    """Return an exit code when ``log_path`` cannot be opened, else ``None``.

    ``verify_audit_log`` never raises — it folds "not found", "could not
    read" and "not valid UTF-8" into a ``VerifyResult(valid=False)`` that
    is structurally indistinguishable from a genuine chain break.  That is
    fine for a library caller reading ``reason``, but the CLI has to route
    a *read* failure (operator typo → 1, permission denied → 2) somewhere
    other than ``EXIT_INTEGRITY_FAILURE`` (6), which must mean "the chain
    is broken".

    So the dispatcher probes the file first with the same in-try exception
    dispatch the sibling ``verify-*`` subcommands use, rather than the
    older ``os.path.isfile`` pre-check that mapped permission-denied on an
    existing log to exit 1.  Once the probe succeeds, every
    ``valid=False`` from the verifier is an integrity verdict.

    A byte is read rather than merely opened so a mid-read I/O failure
    surfaces here (as exit 2) instead of inside the verifier, where it
    would be indistinguishable from a chain break.

    The explicit ``isdir`` check keeps the verdict platform-uniform: POSIX
    raises ``IsADirectoryError`` when opening a directory, Windows raises
    ``PermissionError``, which the ``OSError`` branch would otherwise map
    to exit 2 on one platform and exit 1 on the other.
    """
    if os.path.isdir(log_path):
        _emit_usage_error(output_format, f"audit log not found: {log_path} (path is a directory)")
        return EXIT_CONFIG_ERROR
    try:
        with open(log_path, "rb") as fh:
            fh.read(1)
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError) as exc:
        # Operator-actionable: wrong path, or a path component that is not
        # a directory.  Exit 1.
        _emit_usage_error(output_format, f"audit log not found: {log_path} ({exc.__class__.__name__})")
        return EXIT_CONFIG_ERROR
    except OSError as exc:
        # Genuine runtime I/O failure on a reachable path (permission
        # denied, mid-read I/O error).  Must follow the caller-input
        # subclasses above, which are OSError subclasses.  Exit 2.
        _emit_usage_error(output_format, f"could not read audit log {log_path}: {exc}")
        return EXIT_TRAINING_ERROR
    return None


def _verify_audit_json_payload(result: Any, hmac_secret: Optional[str]) -> Dict[str, Any]:
    """Build the JSON envelope documented at
    ``docs/usermanuals/en/reference/json-output.md`` ("forgelm verify-audit"):
    ``{success, valid, entries_count, hmac_verified, errors}``.

    ``forgelm.compliance.VerifyResult`` carries a single ``reason`` /
    ``first_invalid_index`` pair (one failure halts the chain walk), not a
    list — ``errors`` is therefore always 0 or 1 entries, formatted the
    same way as the text-mode ``FAIL [at line N]: <reason>`` message so
    both output modes report identically.

    ``hmac_verified`` mirrors the text-mode "(HMAC validated)" suffix
    logic (present iff a secret was supplied): ``None`` when no secret was
    configured (chain-only check, HMAC not evaluated), ``True`` when a
    secret was supplied and the whole verification passed, ``False`` when
    a secret was supplied and verification failed. ``VerifyResult`` does
    not separately flag "the failure was HMAC-specific" vs. "some other
    line failed first", so a secret-configured run that fails for any
    reason reports ``False`` rather than over-claiming precision the
    result object cannot support.
    """
    if result.valid:
        errors: List[str] = []
    elif result.first_invalid_index is not None:
        errors = [f"line {result.first_invalid_index}: {result.reason}"]
    else:
        errors = [result.reason or "audit log verification failed"]

    hmac_verified = bool(result.valid) if hmac_secret else None

    return {
        "success": result.valid,
        "valid": result.valid,
        "entries_count": result.entries_count,
        "hmac_verified": hmac_verified,
        "errors": errors,
    }


def _run_verify_audit_cmd(args) -> int:
    """Phase 6 (closure plan) dispatch for ``forgelm verify-audit LOG_PATH``.

    Returns the process exit code rather than calling :func:`sys.exit`
    directly so the dispatcher can route the (0/1) outcome through the
    same code path as the other subcommands. Exit-code contract:

    - ``EXIT_SUCCESS`` (0) — SHA-256 chain (and HMAC tags, when verified)
      intact.
    - ``EXIT_CONFIG_ERROR`` (1) — option/usage error: ``--require-hmac``
      without a secret env var, or a log path that does not exist.  The
      verification never ran.
    - ``EXIT_TRAINING_ERROR`` (2) — the log exists but could not be read
      (permission denied, mid-read I/O failure).  Retryable.
    - ``EXIT_INTEGRITY_FAILURE`` (6) — the log was read and the chain does
      not verify: chain break, HMAC mismatch, genesis-manifest mismatch,
      an undecodable line, or non-UTF-8 bytes inside the log.  This is the
      tampering signal, and it is the reason the code exists — previously
      a broken hash chain and a mistyped path both exited 1, so a CI
      pipeline could not tell a security event from an operator typo.

    Output format: reads ``args.output_format`` (default ``"text"``) and
    emits the JSON envelope documented at
    ``docs/usermanuals/en/reference/json-output.md`` when it is
    ``"json"``.  ``--output-format`` is registered on this subcommand's
    own subparser (``forgelm/cli/_parser.py``'s
    ``_add_verify_audit_subcommand``, via ``include_output_format=True``),
    matching every sibling verify-* subcommand, so it can be placed either
    before the subcommand name (``forgelm --output-format json
    verify-audit LOG_PATH``) or after it (``forgelm verify-audit LOG_PATH
    --output-format json``).
    """
    from ...compliance import verify_audit_log

    output_format = getattr(args, "output_format", "text")
    secret_var = args.hmac_secret_env or ""
    hmac_secret = os.getenv(secret_var) if secret_var else None
    require_hmac = bool(getattr(args, "require_hmac", False))

    if require_hmac and not hmac_secret:
        _emit_usage_error(output_format, f"--require-hmac specified but ${secret_var} is unset.")
        return EXIT_CONFIG_ERROR  # 1 — option/usage error

    read_error_code = _probe_log_readable(args.log_path, output_format)
    if read_error_code is not None:
        return read_error_code  # 1 — missing log file; 2 — unreadable log file

    result = verify_audit_log(
        args.log_path,
        hmac_secret=hmac_secret,
        require_hmac=require_hmac,
    )

    if output_format == "json":
        print(json.dumps(_verify_audit_json_payload(result, hmac_secret), indent=2))
        return EXIT_SUCCESS if result.valid else EXIT_INTEGRITY_FAILURE

    if result.valid:
        suffix = " (HMAC validated)" if hmac_secret else ""
        print(f"OK: {result.entries_count} entries verified{suffix}")
        return EXIT_SUCCESS

    line = result.first_invalid_index
    if line is None:
        print(f"FAIL: {result.reason}", file=sys.stderr)
    else:
        print(f"FAIL at line {line}: {result.reason}", file=sys.stderr)
    # The log was located and read (``_probe_log_readable`` cleared it),
    # so any negative verdict here is a chain / HMAC / manifest integrity
    # failure — exit 6, not 1.
    return EXIT_INTEGRITY_FAILURE
