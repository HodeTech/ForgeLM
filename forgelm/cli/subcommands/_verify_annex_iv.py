"""``forgelm verify-annex-iv`` — EU AI Act Annex IV artifact verification.

Phase 36 closure of GH-004.  Mirrors the verify-audit pattern (Phase 6):
takes a path to a compliance artifact JSON file, validates the field
completeness against the EU AI Act Annex IV §1-9 requirement set, and
recomputes the manifest hash to detect tampering.

The library function lives in :mod:`forgelm.compliance` so integrators
can call it from their own pipelines without going through the CLI;
this module is the dispatcher + JSON-envelope wrapper.

Exit codes (per ``docs/standards/error-handling.md``):

- 0 — every required field present + manifest hash matches.
- 1 — operator-actionable: required field missing/empty, manifest hash
  mismatch, file not found / not a regular file, or malformed JSON
  (the artifact is not Annex IV compliant or not loadable as-is).
- 2 — genuine runtime I/O failure on an existing, reachable file.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, NoReturn, Tuple

from .._exit_codes import EXIT_CONFIG_ERROR, EXIT_SUCCESS, EXIT_TRAINING_ERROR
from .._logging import logger

# EU AI Act Annex IV §1-9 — the nine required categories every
# high-risk-system technical-documentation file must carry.  We map
# each category to the JSON keys we expect at the top level of the
# artifact (a small subset matches `compliance.py`'s emit shape).
#
# NOTE: this is a *minimum* set; richer artefacts may add more keys.
# The check fails when a required key is missing OR when its value is
# the empty string / empty dict / empty list (operator likely forgot
# to populate it from the auto-generation template).
#
# Identity-critical §1 sub-fields that must themselves be non-empty.
# Without this the top-level container check is satisfied by a
# ``system_identification`` dict whose every value is a blank
# placeholder — an Annex IV file with no provider identity and no
# system name would pass the completeness gate (F-P4-OPUS-17).
_SYSTEM_IDENTIFICATION_REQUIRED_SUBKEYS: Tuple[str, ...] = (
    "provider_name",
    "system_name",
    "intended_purpose",
)
_ANNEX_IV_REQUIRED_FIELDS: Tuple[Tuple[str, str], ...] = (
    ("system_identification", "Annex IV §1 — system identification (name, version, provider, intended_purpose)"),
    ("intended_purpose", "Annex IV §1 — intended purpose statement"),
    ("system_components", "Annex IV §2 — software / hardware components + supplier list"),
    ("computational_resources", "Annex IV §2(g) — compute resources used during training"),
    ("data_governance", "Annex IV §2(d) — data sources, governance, validation methodology"),
    ("technical_documentation", "Annex IV §3-5 — design + development methodology"),
    ("monitoring_and_logging", "Annex IV §6 — post-market monitoring + audit-log presence"),
    ("performance_metrics", "Annex IV §7 — accuracy / robustness / cybersecurity metrics"),
    ("risk_management", "Annex IV §9 — risk management system reference (Art. 9 alignment)"),
)


class VerifyAnnexIVResult:
    """Structured result of an Annex IV artifact verification.

    Mirrors ``forgelm.compliance.VerifyResult`` (used by verify-audit)
    so integrators get a uniform shape across the verification toolbelt.
    """

    __slots__ = ("valid", "reason", "missing_fields", "manifest_hash_actual", "manifest_hash_expected")

    def __init__(
        self,
        *,
        valid: bool,
        reason: str = "",
        missing_fields: List[str] | None = None,
        manifest_hash_actual: str = "",
        manifest_hash_expected: str = "",
    ) -> None:
        self.valid = valid
        self.reason = reason
        self.missing_fields = list(missing_fields or [])
        self.manifest_hash_actual = manifest_hash_actual
        self.manifest_hash_expected = manifest_hash_expected

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid": self.valid,
            "reason": self.reason,
            "missing_fields": list(self.missing_fields),
            "manifest_hash_actual": self.manifest_hash_actual,
            "manifest_hash_expected": self.manifest_hash_expected,
        }


def _output_error_and_exit(output_format: str, msg: str, exit_code: int) -> NoReturn:
    """Mirror the family helper from sibling subcommand modules."""
    if output_format == "json":
        print(json.dumps({"success": False, "error": msg}))
    else:
        logger.error(msg)
    sys.exit(exit_code)


def _is_field_populated(value: Any) -> bool:
    """Return ``True`` when the operator clearly populated the field.

    Empty string / empty list / empty dict / ``None`` count as "the
    operator forgot" (a placeholder still in the auto-generation
    template), not "the operator chose to leave it empty".
    """
    if value is None:
        return False
    if isinstance(value, (str, list, dict)) and len(value) == 0:
        return False
    return True


def verify_annex_iv_artifact(path: str) -> VerifyAnnexIVResult:
    """Library entry: verify an Annex IV JSON file's completeness + manifest hash.

    Used by ``forgelm verify-annex-iv`` and exposed for integrators via
    the package facade.  Returns a structured result; never raises on
    documented failure modes (the caller decides whether to exit 1 or
    2 based on the result class).  Raises :class:`OSError` for genuine
    I/O failures on an existing file (dispatcher → ``EXIT_TRAINING_ERROR``)
    and :class:`json.JSONDecodeError` for parse failures (dispatcher →
    ``EXIT_CONFIG_ERROR`` since malformed JSON is a caller-input error).
    """
    with open(path, "r", encoding="utf-8") as fh:
        artifact = json.load(fh)

    if not isinstance(artifact, dict):
        return VerifyAnnexIVResult(
            valid=False,
            reason=f"Artifact root is {type(artifact).__name__}, expected JSON object.",
        )

    # Required fields: walk the static catalog so a future schema
    # addition is one row in the tuple, not a code edit at every
    # call site.
    missing: List[str] = []
    for key, _description in _ANNEX_IV_REQUIRED_FIELDS:
        if not _is_field_populated(artifact.get(key)):
            missing.append(key)
    # Deepen §1: the system_identification container is non-empty as long
    # as it carries the 6 fixed keys, but a dict of all-blank placeholders
    # is exactly "the operator forgot".  Require the identity-critical
    # sub-fields to be populated too (F-P4-OPUS-17).
    sys_ident = artifact.get("system_identification")
    if isinstance(sys_ident, dict) and "system_identification" not in missing:
        for subkey in _SYSTEM_IDENTIFICATION_REQUIRED_SUBKEYS:
            if not _is_field_populated(sys_ident.get(subkey)):
                missing.append(f"system_identification.{subkey}")
    if missing:
        return VerifyAnnexIVResult(
            valid=False,
            reason=f"Missing or empty required Annex IV field(s): {', '.join(missing)}.",
            missing_fields=missing,
        )

    # Manifest hash recompute (tampering detection).  When the artifact
    # carries `metadata.manifest_hash` we recompute SHA-256 over the
    # canonical-JSON representation of the artifact MINUS the metadata
    # block (which itself contains the hash) and compare.
    metadata = artifact.get("metadata") if isinstance(artifact.get("metadata"), dict) else None
    expected = metadata.get("manifest_hash") if metadata else None
    if expected:
        actual = _compute_manifest_hash(artifact)
        if actual != expected:
            return VerifyAnnexIVResult(
                valid=False,
                reason="Manifest hash mismatch — artifact may have been modified after generation.",
                manifest_hash_actual=actual,
                manifest_hash_expected=expected,
            )
        return VerifyAnnexIVResult(
            valid=True,
            reason="All Annex IV §1-9 fields populated; manifest hash matches.",
            manifest_hash_actual=actual,
            manifest_hash_expected=expected,
        )

    # No manifest hash present — the field-completeness check is the
    # only signal we can give.  Pass with a note so the operator knows.
    return VerifyAnnexIVResult(
        valid=True,
        reason="All Annex IV §1-9 fields populated; no manifest_hash present so tampering detection skipped.",
    )


def _compute_manifest_hash(artifact: Dict[str, Any]) -> str:
    """Recompute the manifest hash the same way ``compliance.py`` writes it.

    Delegates to :func:`forgelm.compliance.compute_annex_iv_manifest_hash`
    so the writer + verifier canonicalisation cannot drift byte-for-byte.
    Wave 2b Round-4 review F-W2B-05 fix: the previous local
    implementation duplicated the canonicalisation logic; if the writer
    ever changed (added a new metadata key, switched separators, etc.)
    legitimate artefacts would fail their own verifier.
    """
    from forgelm.compliance import compute_annex_iv_manifest_hash

    return compute_annex_iv_manifest_hash(artifact)


def _run_pipeline_mode(path: str, output_format: str) -> None:
    """Verify a pipeline run directory's manifest + chain integrity.

    Reads ``<path>/compliance/pipeline_manifest.json``, runs the in-
    memory verifier + per-stage training_manifest existence check, and
    prints / exits.  Extracted from :func:`_run_verify_annex_iv_cmd`
    for Sonar python:S3776 cognitive-complexity hygiene.

    Exit-code mapping (Phase 14 post-release review — now mirrors the
    single-artefact path's policy in
    :func:`_verify_artefact_and_handle_io_errors`):

    - ``EXIT_SUCCESS (0)`` — empty violation list.
    - ``EXIT_CONFIG_ERROR (1)`` — operator-actionable input error:
      ``pipeline_manifest.json not found …`` (wrong directory),
      ``pipeline_manifest.json invalid JSON: …`` (reachable but
      corrupted on disk — operator can regenerate or fix), or any
      structural / chain-integrity violation.
    - ``EXIT_TRAINING_ERROR (2)`` — genuine runtime I/O failure on a
      reachable path: ``pipeline_manifest.json unreadable: …``
      sentinel from a non-parse OSError (locked file, mid-read I/O
      failure) or an uncaught OSError that bubbles past the verifier.

    Note: the previous release mapped ``invalid JSON`` to
    ``EXIT_TRAINING_ERROR`` because the verifier emitted a shared
    "unreadable" sentinel for both ``OSError`` and ``JSONDecodeError``.
    The verifier now emits distinct prefixes so the CLI can
    differentiate the two on the sentinel alone.
    """
    from forgelm.compliance import verify_pipeline_manifest_at_path

    # Defensive try/except: the verifier already maps OSError /
    # JSONDecodeError to violation strings, but a future change there
    # could let an exception bubble — fail loud rather than swallow.
    try:
        violations = verify_pipeline_manifest_at_path(path)
    except OSError as exc:
        msg = f"FAIL: pipeline manifest at {path} — runtime I/O error: {exc}"
        if output_format == "json":
            print(
                json.dumps(
                    {
                        "success": False,
                        "mode": "pipeline",
                        "path": os.path.abspath(path),
                        "violations": [str(exc)],
                    },
                    indent=2,
                )
            )
        else:
            print(msg)
        sys.exit(EXIT_TRAINING_ERROR)

    # Sentinel-based exit-code routing.  Only the OSError-shaped
    # ``unreadable`` sentinel maps to EXIT_TRAINING_ERROR; everything
    # else (``invalid JSON``, ``not found``, structural / chain
    # violations) is operator-actionable → EXIT_CONFIG_ERROR.
    runtime_io_error = any("unreadable" in v for v in violations)

    if output_format == "json":
        print(
            json.dumps(
                {
                    "success": not violations,
                    "mode": "pipeline",
                    "path": os.path.abspath(path),
                    "violations": violations,
                },
                indent=2,
            )
        )
    elif not violations:
        print(f"OK: pipeline manifest at {path}")
    else:
        print(f"FAIL: pipeline manifest at {path}")
        for v in violations:
            print(f"  - {v}")

    if not violations:
        sys.exit(EXIT_SUCCESS)
    sys.exit(EXIT_TRAINING_ERROR if runtime_io_error else EXIT_CONFIG_ERROR)


def _verify_artefact_and_handle_io_errors(path: str, output_format: str) -> "VerifyAnnexIVResult":
    """Run :func:`verify_annex_iv_artifact` with the documented I/O
    error-mapping policy.

    Extracted from :func:`_run_verify_annex_iv_cmd` for Sonar
    python:S3776 cognitive-complexity hygiene.  Each ``except`` branch
    exits the process via ``_output_error_and_exit`` — the function
    only returns on success.

    Exit-code mapping (per ``docs/reference/verify_annex_iv_subcommand.md``):

    - ``FileNotFoundError`` / ``IsADirectoryError`` / ``JSONDecodeError`` →
      ``EXIT_CONFIG_ERROR (1)`` (operator-actionable input error).
    - ``OSError`` (catch-all, must follow ``FileNotFoundError`` because
      Python's OSError hierarchy makes it a subclass) →
      ``EXIT_TRAINING_ERROR (2)`` (genuine runtime I/O failure).
    """
    try:
        return verify_annex_iv_artifact(path)
    except (FileNotFoundError, IsADirectoryError) as exc:
        _output_error_and_exit(
            output_format,
            f"Annex IV artifact not found or not a regular file: {path!r} ({exc.__class__.__name__}).",
            EXIT_CONFIG_ERROR,
        )
    except json.JSONDecodeError as exc:
        _output_error_and_exit(
            output_format,
            f"Annex IV artifact at {path!r} is not valid JSON: {exc.msg} (line {exc.lineno}).",
            EXIT_CONFIG_ERROR,
        )
    except OSError as exc:
        _output_error_and_exit(
            output_format,
            f"Could not read Annex IV artifact {path!r}: {exc}.",
            EXIT_TRAINING_ERROR,
        )


def _print_artefact_result(result: "VerifyAnnexIVResult", path: str, output_format: str) -> None:
    """Render the per-artefact verify result to stdout (JSON or text)."""
    payload = result.to_dict()
    payload["path"] = os.path.abspath(path)
    if output_format == "json":
        print(json.dumps({"success": result.valid, **payload}, indent=2))
        return
    if result.valid:
        print(f"OK: {path}")
        print(f"  {result.reason}")
        return
    print(f"FAIL: {path}")
    print(f"  {result.reason}")
    for missing in result.missing_fields:
        print(f"    - missing: {missing}")


def _run_verify_annex_iv_cmd(args, output_format: str) -> None:
    """Top-level dispatcher for ``forgelm verify-annex-iv <path>``.

    Two modes:

    - **Single artefact** (default): ``<path>`` is an Annex IV JSON file
      and the verifier checks field completeness + manifest hash.
    - **Pipeline** (``--pipeline`` flag): ``<path>`` is a pipeline run
      directory and the verifier reads
      ``<path>/compliance/pipeline_manifest.json`` and runs chain-
      integrity + stage-index + ``stopped_at`` coherence + per-stage
      training_manifest existence checks.  Returns a list of violations
      and exits 0 only when the list is empty.
    """
    path = getattr(args, "path", None)
    if not path:
        _output_error_and_exit(
            output_format,
            "verify-annex-iv requires a path argument: `forgelm verify-annex-iv <annex_iv.json>`.",
            EXIT_CONFIG_ERROR,
        )

    # Phase 14: ``--pipeline`` mode validates the chain-level manifest.
    if getattr(args, "pipeline", False):
        _run_pipeline_mode(path, output_format)

    # Single-artefact path: I/O errors map to documented exit codes via
    # the helper (which sys.exits on failure); on success we render the
    # result and exit with the artefact's validity verdict.
    result = _verify_artefact_and_handle_io_errors(path, output_format)
    _print_artefact_result(result, path, output_format)
    sys.exit(EXIT_SUCCESS if result.valid else EXIT_CONFIG_ERROR)


__all__ = [
    "VerifyAnnexIVResult",
    "_run_verify_annex_iv_cmd",
    "verify_annex_iv_artifact",
]
