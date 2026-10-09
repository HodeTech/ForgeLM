"""GGUF artefact integrity verification.

Owns :class:`VerifyGgufResult`, the magic-header check, the optional
``gguf`` metadata parse and the SHA-256 sidecar comparison, plus the
predicate that decides whether a failure is tampering (exit 6) or bad
input (exit 1).
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import Any, Dict

_GGUF_MAGIC = b"GGUF"
_SIDECAR_SUFFIX = ".sha256"

# A ``.sha256`` sidecar is one line: a 64-char digest optionally followed by
# `` *<filename>`` (sha256sum format).  Only the first whitespace-separated
# token is ever used, so the read is bounded generously rather than slurping
# whatever a writer put next to the artefact.
_SIDECAR_MAX_CHARS = 4096

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


def verify_gguf(path: str) -> VerifyGgufResult:
    """Library entry: verify a GGUF file's integrity.

    Three-layer check:

    1. **Magic header** — first 4 bytes must equal ``b"GGUF"``.  Anything
       else means the file is not a GGUF (operator likely passed the
       wrong path or a corrupted download).
    2. **Metadata block** (optional, when the ``gguf`` package is
       installed): parse the metadata + tensor descriptors via the
       upstream reader; a parse failure means either the file was
       truncated / corrupted, or the installed ``gguf`` release cannot
       read this file's format revision.
    3. **SHA-256 sidecar** (optional, when ``<path>.sha256`` exists):
       recompute the file hash and compare to the sidecar's contents.
       The forgelm exporter writes this sidecar by default; mismatch
       means the file was modified after export.

    A metadata-parse failure deliberately does **not** short-circuit the
    sidecar comparison (D1-07).  The sidecar is the stronger evidence: it
    can prove the file is byte-identical to what was exported, which
    settles the corruption-vs-parser-incompatibility ambiguity that the
    metadata error alone leaves open.  The two signals are therefore both
    collected and reported together, and
    :func:`is_gguf_integrity_failure` decides the verdict from the pair.

    Returns the structured result; raises :class:`OSError` for I/O
    failures so the dispatcher can surface them as ``EXIT_TRAINING_ERROR``.
    """
    checks: Dict[str, Any] = {
        "magic_ok": False,
        "metadata_parsed": False,
        "metadata_error": None,
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
    metadata_error = metadata_check.get("error")
    checks["metadata_error"] = metadata_error
    # NOTE: no early return here.  See the "does not short-circuit" note in
    # the docstring — the sidecar below can prove the bytes are exactly what
    # was exported, which downgrades this from "corrupted artefact" to
    # "your gguf package cannot read this file".
    if metadata_check.get("tensor_count") is not None:
        checks["tensor_count"] = metadata_check["tensor_count"]

    # SHA-256 sidecar (optional, but fail-closed on malformed contents).
    sidecar_path = path + _SIDECAR_SUFFIX
    if os.path.isfile(sidecar_path):
        checks["sidecar_present"] = True
        actual = _file_sha256(path)
        # Bounded read: only the first whitespace-separated token is ever
        # used, but ``fh.read()`` would pull an arbitrarily large file into
        # memory first.  A sidecar sits next to the artefact and is written
        # by whoever can write the artefact's directory, so an unbounded read
        # here is the same "verifier killed by its own input" exposure the
        # byte caps above exist to close.  A legitimate sidecar is one line.
        #
        # Decoding stays STRICT: ``_run_verify_gguf_cmd`` handles
        # ``UnicodeDecodeError`` explicitly for this exact branch, so
        # ``errors="replace"`` would silently delete a routed failure path.
        # Text mode is correct here — ``read(n)`` counts characters and so
        # cannot split a multi-byte sequence.
        with open(sidecar_path, "r", encoding="utf-8") as fh:
            expected_text = fh.read(_SIDECAR_MAX_CHARS).strip()
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

    if metadata_error:
        # Every comparison that *could* run has now run.  Report the parse
        # failure alongside what the sidecar established, so the operator
        # message says which of the two situations they are in.
        if checks["sidecar_match"]:
            detail = (
                "  The SHA-256 sidecar matches, so the file is byte-identical to what was "
                "exported — this is almost certainly a `gguf` package version that cannot "
                "read this file's format revision, not a corrupted artifact.  Upgrade `gguf` "
                "and re-run before treating it as a tampering event."
            )
        else:
            detail = (
                "  No SHA-256 sidecar was available to rule out corruption, so the file must "
                "be treated as truncated or damaged."
            )
        return VerifyGgufResult(
            valid=False,
            reason=f"GGUF metadata block could not be parsed: {metadata_error}.{detail}",
            checks=checks,
        )

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


def is_gguf_integrity_failure(result: VerifyGgufResult) -> bool:
    """Return ``True`` when a GGUF result failed an **integrity** check.

    Walks the same three layers ``verify_gguf`` checks, in order, reading
    only the structured ``checks`` dict:

    - ``magic_ok`` false → the first four bytes are not ``b"GGUF"``.  The
      file is not a GGUF at all; the overwhelmingly common cause is the
      operator naming the wrong path.  **Input error → exit 1.**
    - sidecar present but ``sha256_expected`` is not a 64-char hex digest
      → the sidecar itself is unusable (empty, ``TODO``, truncated paste).
      Nothing was compared.  **Input error → exit 1.**
    - sidecar present with a well-formed digest that does not match →
      the file changed after export.  **Integrity → exit 6.**  This holds
      whether or not the metadata block also failed to parse: a checksum
      mismatch is the strongest evidence available and it dominates.
    - sidecar present with a well-formed digest that **matches**, yet the
      result is still invalid → the only remaining failure is a metadata
      parse error, and the checksum has just proven the file is
      byte-identical to what was exported.  Nothing was tampered with; the
      overwhelmingly likely cause is a ``gguf`` package too old for this
      file's format revision.  **Input error → exit 1** (D1-07: exit 6
      means "page the artefact owner", and a library-version mismatch must
      never trigger that).
    - magic OK, no usable sidecar, and still invalid → a GGUF whose
      metadata block could not be parsed, with nothing available to rule
      out corruption.  The artefact must be treated as structurally
      broken.  **Integrity → exit 6.**
    """
    if result.valid:
        return False
    checks = result.checks
    if not checks.get("magic_ok"):
        return False
    if checks.get("sidecar_present"):
        # ``sha256_expected`` is only absent when the sidecar branch never
        # ran, which ``sidecar_present`` already rules out; default to ""
        # so a hand-built result cannot raise here.
        if not _SHA256_HEX_RE.match(checks.get("sha256_expected") or ""):
            return False
        # Read the recorded comparison outcome rather than re-deriving it.
        # ``is not True`` rather than ``is False`` so an incomplete result
        # (``sidecar_match`` absent, which ``verify_gguf`` never produces
        # but a hand-built result could) fails *closed* onto the tamper
        # verdict.  Only a positively-recorded match — the checksum proving
        # the bytes are what was exported — earns the softer exit 1.
        return checks.get("sidecar_match") is not True
    return True
