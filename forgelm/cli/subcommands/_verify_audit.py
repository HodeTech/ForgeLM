"""``forgelm verify-audit`` dispatcher (Phase 6 closure plan)."""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional

from .._exit_codes import EXIT_CONFIG_ERROR, EXIT_SUCCESS


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
    - ``EXIT_CONFIG_ERROR`` (1) — used for both option/usage errors
      (``--require-hmac`` without a secret env var, log path not found)
      and chain integrity / tampering detection (chain break, HMAC
      mismatch, manifest mismatch, JSON decode error). Both are
      operator-actionable failures; the trimmed exit-code contract maps
      them to the same numeric 1 even though semantically the constant
      name leans toward "config" — a dedicated ``EXIT_VALIDATION_ERROR``
      / ``EXIT_INTEGRITY_FAILURE`` constant is deferred to v0.6.x to
      avoid expanding the public surface here.

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

    if not os.path.isfile(args.log_path):
        _emit_usage_error(output_format, f"audit log not found: {args.log_path}")
        return EXIT_CONFIG_ERROR  # 1 — option/usage error (missing log file)

    result = verify_audit_log(
        args.log_path,
        hmac_secret=hmac_secret,
        require_hmac=require_hmac,
    )

    if output_format == "json":
        print(json.dumps(_verify_audit_json_payload(result, hmac_secret), indent=2))
        return EXIT_SUCCESS if result.valid else EXIT_CONFIG_ERROR

    if result.valid:
        suffix = " (HMAC validated)" if hmac_secret else ""
        print(f"OK: {result.entries_count} entries verified{suffix}")
        return EXIT_SUCCESS

    line = result.first_invalid_index
    if line is None:
        print(f"FAIL: {result.reason}", file=sys.stderr)
    else:
        print(f"FAIL at line {line}: {result.reason}", file=sys.stderr)
    return EXIT_CONFIG_ERROR  # 1 — chain/HMAC integrity failure
