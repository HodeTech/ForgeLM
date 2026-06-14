"""``forgelm verify-integrity`` — Art. 15 model-integrity verification.

The consuming counterpart to :func:`forgelm.compliance.generate_model_integrity`.
That writer computes a SHA-256 manifest (``model_integrity.json``) over every
file in a trained model directory; this command reads the manifest back, re-walks
the directory, recomputes each file's SHA-256, and reports any file that was
**added**, **removed**, or **changed** since the manifest was written.  Without
it the Art. 15 section header ("Model Integrity *Verification*") over-claimed —
only the generate side shipped (F-P4-OPUS-14).

Exit codes (per ``docs/standards/error-handling.md`` and the public contract
in ``docs/reference/verify_integrity_subcommand.md``):

- 0 — ``EXIT_SUCCESS``: every recorded artifact present and unchanged, no
  unexpected extra files.
- 1 — ``EXIT_CONFIG_ERROR``: caller / input error (missing path, manifest not
  found / not a regular file, malformed JSON) OR an integrity mismatch
  (changed / removed / added file).  The artifacts do not match the manifest.
- 2 — ``EXIT_TRAINING_ERROR``: genuine runtime I/O failure on a reachable path
  (read error, permission denied mid-walk, etc.).
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, NoReturn

from .._exit_codes import EXIT_CONFIG_ERROR, EXIT_SUCCESS, EXIT_TRAINING_ERROR
from .._logging import logger

_MANIFEST_NAME = "model_integrity.json"


class VerifyIntegrityResult:
    """Structured result of a model-integrity verification.

    Mirrors the sibling verify-* result shapes so integrators get a
    uniform surface across the verification toolbelt.
    """

    __slots__ = ("valid", "reason", "changed", "removed", "added", "verified_count")

    def __init__(
        self,
        *,
        valid: bool,
        reason: str = "",
        changed: List[str] | None = None,
        removed: List[str] | None = None,
        added: List[str] | None = None,
        verified_count: int = 0,
    ) -> None:
        self.valid = valid
        self.reason = reason
        self.changed = list(changed or [])
        self.removed = list(removed or [])
        self.added = list(added or [])
        self.verified_count = verified_count

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid": self.valid,
            "reason": self.reason,
            "changed": list(self.changed),
            "removed": list(self.removed),
            "added": list(self.added),
            "verified_count": self.verified_count,
        }


def _output_error_and_exit(output_format: str, msg: str, exit_code: int) -> NoReturn:
    if output_format == "json":
        print(json.dumps({"success": False, "error": msg}))
    else:
        logger.error(msg)
    sys.exit(exit_code)


def verify_integrity(model_dir: str) -> VerifyIntegrityResult:
    """Library entry: verify a model directory against its integrity manifest.

    Reads ``<model_dir>/model_integrity.json`` (produced by
    :func:`forgelm.compliance.generate_model_integrity`), recomputes the
    SHA-256 of every recorded artifact, and walks the directory to detect
    files that exist on disk but are absent from the manifest.

    The manifest itself (``model_integrity.json``) is excluded from the
    walk — it is generated after the model artifacts and is not one of
    the recorded hashes, so it would otherwise always surface as an
    "added" file.

    Returns the structured result; raises :class:`FileNotFoundError` when
    the manifest is missing, :class:`json.JSONDecodeError` when it is
    malformed, and :class:`OSError` for genuine I/O failures while
    re-hashing — the dispatcher maps each to its documented exit code.
    """
    from forgelm.compliance import _hash_file

    manifest_path = os.path.join(model_dir, _MANIFEST_NAME)
    with open(manifest_path, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)

    recorded = manifest.get("artifacts", []) if isinstance(manifest, dict) else []
    recorded_rel = {entry["file"] for entry in recorded if isinstance(entry, dict) and "file" in entry}

    changed: List[str] = []
    removed: List[str] = []
    verified = 0
    for entry in recorded:
        if not isinstance(entry, dict) or "file" not in entry:
            continue
        rel_path = entry["file"]
        abs_path = os.path.join(model_dir, rel_path)
        if not os.path.isfile(abs_path):
            removed.append(rel_path)
            continue
        actual = _hash_file(abs_path, rel_path)
        if actual["sha256"] != entry.get("sha256"):
            changed.append(rel_path)
        else:
            verified += 1

    # Files on disk not recorded in the manifest = added since generation.
    added: List[str] = []
    for root, _dirs, files in os.walk(model_dir):
        for filename in files:
            abs_path = os.path.join(root, filename)
            rel_path = os.path.relpath(abs_path, model_dir)
            if rel_path == _MANIFEST_NAME:
                continue
            if rel_path not in recorded_rel:
                added.append(rel_path)

    if changed or removed or added:
        parts = []
        if changed:
            parts.append(f"{len(changed)} changed")
        if removed:
            parts.append(f"{len(removed)} removed")
        if added:
            parts.append(f"{len(added)} added")
        return VerifyIntegrityResult(
            valid=False,
            reason="Model artifacts do not match model_integrity.json: " + ", ".join(parts) + ".",
            changed=sorted(changed),
            removed=sorted(removed),
            added=sorted(added),
            verified_count=verified,
        )

    return VerifyIntegrityResult(
        valid=True,
        reason=f"All {verified} recorded artifact(s) present and unchanged.",
        verified_count=verified,
    )


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
    except IsADirectoryError as exc:
        _output_error_and_exit(
            output_format,
            f"Integrity manifest path is a directory, not a file: {os.path.join(path, _MANIFEST_NAME)!r} ({exc.__class__.__name__}).",
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
    sys.exit(EXIT_SUCCESS if result.valid else EXIT_CONFIG_ERROR)


__all__ = [
    "VerifyIntegrityResult",
    "_run_verify_integrity_cmd",
    "verify_integrity",
]
