"""``forgelm verify-audit`` dispatcher (Phase 6 closure plan)."""

from __future__ import annotations

import json
import os
import stat
import sys
from typing import Any, Dict, List, Optional

from ...verify import is_audit_integrity_failure
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

    This is an **early, better-message** gate, not the exit-code contract:
    ``_verify_audit_log_classified`` classifies its own failures with a
    structural ``AUDIT_FAILURE_*`` token, and :func:`_exit_code_for_result`
    is the single place that turns a verdict into an exit code.  The probe
    exists so the common operator errors — wrong path, permission denied, a
    path that is not a log file at all — are reported in the CLI's own
    wording before the verifier is asked to make sense of them.

    What the probe does and does not catch
    --------------------------------------

    It confirms the path is a **regular file** and that the first byte can
    be read.  It says nothing about a read failure ten megabytes in: byte 1
    succeeding is no evidence about byte 10,000,000 (F2 — the previous
    docstring claimed otherwise).  That case is not lost, though: a
    mid-read ``OSError`` inside ``_read_audit_log_lines`` comes back tagged
    ``AUDIT_FAILURE_UNREADABLE`` and routes to exit 2 from the verdict
    itself, which is where the guarantee actually lives.

    The regular-file check is load-bearing, not tidiness (F1).  ``open()``
    succeeds on FIFOs, character devices and sockets, so the previous
    open-and-read-one-byte probe would **block forever** on a FIFO (waiting
    for a writer that never comes) and, on ``/dev/zero``, pass the probe and
    then hit the verifier's ``os.path.isfile`` guard — which reports "not
    found" and, under the old blanket mapping, exited **6**, telling CI the
    audit log had been tampered with.  ``os.stat`` answers the question
    without opening anything, so a FIFO can neither hang nor be misread.

    The explicit directory branch keeps the verdict platform-uniform: POSIX
    raises ``IsADirectoryError`` when opening a directory, Windows raises
    ``PermissionError``, which the ``OSError`` branch would otherwise map
    to exit 2 on one platform and exit 1 on the other.  Deciding it from
    ``st_mode`` removes the platform from the answer entirely.
    """
    try:
        st = os.stat(log_path)
    except (FileNotFoundError, NotADirectoryError) as exc:
        _emit_usage_error(output_format, f"audit log not found: {log_path} ({exc.__class__.__name__})")
        return EXIT_CONFIG_ERROR
    except OSError as exc:
        # Reachability failure that is not "absent": permission denied on a
        # parent directory, a symlink loop, a dead network mount.  Exit 2.
        _emit_usage_error(output_format, f"could not read audit log {log_path}: {exc}")
        return EXIT_TRAINING_ERROR

    if stat.S_ISDIR(st.st_mode):
        _emit_usage_error(output_format, f"audit log not found: {log_path} (path is a directory)")
        return EXIT_CONFIG_ERROR
    if not stat.S_ISREG(st.st_mode):
        # FIFO, character/block device, or socket.  Opening one would block
        # (FIFO) or stream without end (/dev/zero); either way there is no
        # audit log at this path to verify.  Operator-actionable → exit 1.
        _emit_usage_error(output_format, f"audit log not found: {log_path} (path is not a regular file)")
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


def _exit_code_for_result(result: Any, failure_kind: Optional[str]) -> int:
    """Map a verification outcome onto the public exit-code contract.

    The **one** place ``verify-audit`` decides its exit code.  It used to be
    decided twice — once in the JSON branch and once in the text branch —
    with every test covering only the JSON one, so the text branch's copy
    could be changed from 6 to 1 with the whole suite still green
    (F-4 / T-02).  One function, exercised by both branches, removes the
    class of bug rather than the instance.

    Routing reads the structured ``AUDIT_FAILURE_*`` token that
    ``_verify_audit_log_classified`` returns beside the result — via
    :func:`forgelm.verify.is_audit_integrity_failure` — never ``reason``
    prose:

    - valid → ``EXIT_SUCCESS`` (0)
    - chain / HMAC / genesis-manifest / non-UTF-8-record failure →
      ``EXIT_INTEGRITY_FAILURE`` (6)
    - the log exists but could not be read through →
      ``EXIT_TRAINING_ERROR`` (2), retryable
    - anything else (no log at that path, impossible option combination) →
      ``EXIT_CONFIG_ERROR`` (1)
    """
    from ...compliance import AUDIT_FAILURE_UNREADABLE

    if result.valid:
        return EXIT_SUCCESS
    if is_audit_integrity_failure(failure_kind):
        return EXIT_INTEGRITY_FAILURE
    if failure_kind == AUDIT_FAILURE_UNREADABLE:
        return EXIT_TRAINING_ERROR
    return EXIT_CONFIG_ERROR


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
    directly so the dispatcher can route the outcome through the same code
    path as the other subcommands.  The mapping itself lives in
    :func:`_exit_code_for_result` — one decision point for both output
    formats.  Exit-code contract:

    - ``EXIT_SUCCESS`` (0) — SHA-256 chain (and HMAC tags, when verified)
      intact.
    - ``EXIT_CONFIG_ERROR`` (1) — option/usage error: ``--require-hmac``
      without a secret env var, or a log path that does not exist / is not
      a regular file (a directory, FIFO or device).  The verification
      never ran.
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
    from ...compliance import _verify_audit_log_classified

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

    # The classified variant of ``verify_audit_log``: same verdict, plus the
    # ``AUDIT_FAILURE_*`` token the exit-code contract routes on.
    result, failure_kind = _verify_audit_log_classified(
        args.log_path,
        hmac_secret=hmac_secret,
        require_hmac=require_hmac,
    )

    # Decide the exit code once, then render.  Both output branches return
    # this same value; see _exit_code_for_result for why that matters.
    exit_code = _exit_code_for_result(result, failure_kind)

    if output_format == "json":
        print(json.dumps(_verify_audit_json_payload(result, hmac_secret), indent=2))
        return exit_code

    if result.valid:
        suffix = " (HMAC validated)" if hmac_secret else ""
        print(f"OK: {result.entries_count} entries verified{suffix}")
        return exit_code

    line = result.first_invalid_index
    if line is None:
        print(f"FAIL: {result.reason}", file=sys.stderr)
    else:
        print(f"FAIL at line {line}: {result.reason}", file=sys.stderr)
    return exit_code
