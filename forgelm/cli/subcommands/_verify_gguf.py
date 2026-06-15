"""``forgelm verify-gguf`` — GGUF integrity check.

Phase 36 closure of GH-009.  The deployment-integrity counterpart to
``verify-annex-iv``: takes a path to a GGUF model file, validates the
4-byte magic header, optionally parses the metadata block (when the
``gguf`` Python package is installed), and checks a SHA-256 manifest
sidecar (``<model>.gguf.sha256``) when present.

Exit codes (per ``docs/standards/error-handling.md`` and the public
contract in ``docs/reference/verify_gguf_subcommand.md``):

- 0 — ``EXIT_SUCCESS``: magic OK, metadata parses, SHA-256 matches
  sidecar (when present).
- 1 — ``EXIT_CONFIG_ERROR``: caller / input error (missing path, not
  a regular file, magic mismatch, metadata corruption, malformed
  sidecar, SHA-256 mismatch).  Artifact is not safe to serve.
- 2 — ``EXIT_TRAINING_ERROR``: genuine runtime I/O failure on a
  reachable path (read error, permission denied mid-read, etc.).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from typing import Any, Dict, NoReturn

from .._exit_codes import EXIT_CONFIG_ERROR, EXIT_SUCCESS, EXIT_TRAINING_ERROR
from .._logging import logger

_GGUF_MAGIC = b"GGUF"
_SIDECAR_SUFFIX = ".sha256"

# A SHA-256 sidecar must contain a 64-character hex digest.  Anything
# else (empty file, "TODO" placeholder, truncated paste, wrong-algorithm
# digest) is malformed; verify_gguf fails closed rather than silently
# accepting an unverifiable artefact.
_SHA256_HEX_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class VerifyGgufResult:
    """Structured GGUF verification result."""

    __slots__ = ("valid", "reason", "checks")

    def __init__(self, *, valid: bool, reason: str = "", checks: Dict[str, Any] | None = None) -> None:
        self.valid = valid
        self.reason = reason
        self.checks = dict(checks or {})

    def to_dict(self) -> Dict[str, Any]:
        return {"valid": self.valid, "reason": self.reason, "checks": dict(self.checks)}


def _output_error_and_exit(output_format: str, msg: str, exit_code: int) -> NoReturn:
    if output_format == "json":
        # ``indent=2`` matches the success/result envelope below so this
        # subcommand emits one consistent JSON shape on every branch.
        print(json.dumps({"success": False, "error": msg}, indent=2))
    else:
        logger.error(msg)
    sys.exit(exit_code)


def verify_gguf(path: str) -> VerifyGgufResult:
    """Library entry: verify a GGUF file's integrity.

    Three-layer check:

    1. **Magic header** — first 4 bytes must equal ``b"GGUF"``.  Anything
       else means the file is not a GGUF (operator likely passed the
       wrong path or a corrupted download).
    2. **Metadata block** (optional, when the ``gguf`` package is
       installed): parse the metadata + tensor descriptors via the
       upstream reader; mismatch means the writer crashed mid-stream
       or the file was truncated.
    3. **SHA-256 sidecar** (optional, when ``<path>.sha256`` exists):
       recompute the file hash and compare to the sidecar's contents.
       The forgelm exporter writes this sidecar by default; mismatch
       means the file was modified after export.

    Returns the structured result; raises :class:`OSError` for I/O
    failures so the dispatcher can surface them as ``EXIT_TRAINING_ERROR``.
    """
    checks: Dict[str, Any] = {
        "magic_ok": False,
        "metadata_parsed": False,
        "sidecar_present": False,
        "sidecar_match": None,
    }
    with open(path, "rb") as fh:
        head = fh.read(len(_GGUF_MAGIC))
    if head != _GGUF_MAGIC:
        return VerifyGgufResult(
            valid=False,
            reason=f"Magic header mismatch: expected {_GGUF_MAGIC!r}, got {head!r}.  Not a GGUF file or corrupted.",
            checks=checks,
        )
    checks["magic_ok"] = True

    metadata_check = _maybe_parse_metadata(path)
    checks["metadata_parsed"] = metadata_check["parsed"]
    if metadata_check.get("error"):
        # Metadata corruption is a real integrity problem.
        return VerifyGgufResult(
            valid=False,
            reason=f"GGUF metadata block could not be parsed: {metadata_check['error']}",
            checks=checks,
        )
    if metadata_check.get("tensor_count") is not None:
        checks["tensor_count"] = metadata_check["tensor_count"]

    # SHA-256 sidecar (optional, but fail-closed on malformed contents).
    sidecar_path = path + _SIDECAR_SUFFIX
    if os.path.isfile(sidecar_path):
        checks["sidecar_present"] = True
        actual = _file_sha256(path)
        with open(sidecar_path, "r", encoding="utf-8") as fh:
            expected_text = fh.read().strip()
        # Sidecars are typically `<hex> *<filename>` (sha256sum format)
        # OR plain `<hex>`.  Take the first whitespace-separated token.
        expected = expected_text.split()[0] if expected_text else ""
        checks["sha256_actual"] = actual
        checks["sha256_expected"] = expected
        if not _SHA256_HEX_RE.match(expected):
            # Empty / non-hex / wrong-length sidecar.  Fail closed:
            # ignoring it would let a malformed sidecar masquerade as
            # "verified".  A genuinely-absent sidecar is the operator's
            # explicit choice (no file → no check); a *present but
            # malformed* sidecar is operator error we must surface.
            checks["sidecar_match"] = False
            return VerifyGgufResult(
                valid=False,
                reason=(
                    "Malformed SHA-256 sidecar: expected a 64-character hex digest, "
                    f"got {expected_text[:64]!r}.  Regenerate the sidecar (e.g. "
                    "`sha256sum model.gguf > model.gguf.sha256`) or remove it to "
                    "skip the check."
                ),
                checks=checks,
            )
        if actual != expected:
            checks["sidecar_match"] = False
            return VerifyGgufResult(
                valid=False,
                reason=f"SHA-256 sidecar mismatch — file modified after export.  Expected {expected[:16]}…, got {actual[:16]}….",
                checks=checks,
            )
        checks["sidecar_match"] = True

    return VerifyGgufResult(
        valid=True,
        reason="GGUF magic OK"
        + (", metadata parsed" if checks["metadata_parsed"] else "")
        + (", SHA-256 sidecar match" if checks["sidecar_match"] else ""),
        checks=checks,
    )


def _maybe_parse_metadata(path: str) -> Dict[str, Any]:
    """Best-effort GGUF metadata parse via the optional ``gguf`` package.

    Returns ``{"parsed": bool, "error": str|None, "tensor_count": int|None}``.

    **Optional-dependency policy** (per ``CLAUDE.md`` and
    ``docs/standards/coding.md``): ``gguf`` is *not* a core ForgeLM
    dependency — operators using `verify-gguf` to spot-check exported
    artefacts on a minimal install legitimately do not have it.
    Absent ``gguf`` package = ``parsed=False``, ``error=None`` and the
    caller treats this as "metadata check skipped" (the magic-header
    + SHA-256-sidecar checks are the load-bearing integrity surface).
    Raising ``ImportError`` here would break the subcommand for the
    optional-extra-not-installed path and contradict the project
    standard.  Genuine corruption (file present but reader crashes)
    surfaces as a real ``error`` string and the caller fails closed.
    """
    try:
        from gguf import GGUFReader  # type: ignore[import-untyped]
    except ImportError:
        return {"parsed": False, "error": None, "tensor_count": None}
    try:
        reader = GGUFReader(path, "r")
        tensor_count = len(getattr(reader, "tensors", []) or [])
        return {"parsed": True, "error": None, "tensor_count": tensor_count}
    except Exception as exc:  # noqa: BLE001 — gguf has no clean exception hierarchy (struct.error, IndexError, ValueError, AttributeError, OSError). Catching BaseException would swallow KeyboardInterrupt/SystemExit, which we want to propagate. Acceptable per error-handling.md best-effort carve-out: verifier reports failure-path, never silent. # NOSONAR
        return {"parsed": False, "error": f"{exc.__class__.__name__}: {exc}", "tensor_count": None}


def _file_sha256(path: str) -> str:
    """Stream the file through SHA-256; never loads the whole file."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


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
    sys.exit(EXIT_SUCCESS if result.valid else EXIT_CONFIG_ERROR)


__all__ = [
    "VerifyGgufResult",
    "_run_verify_gguf_cmd",
    "verify_gguf",
]
