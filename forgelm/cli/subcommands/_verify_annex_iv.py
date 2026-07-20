"""``forgelm verify-annex-iv`` — EU AI Act Annex IV artifact verification.

Phase 36 closure of GH-004.  Mirrors the verify-audit pattern (Phase 6):
takes a path to a compliance artifact JSON file, validates the field
completeness against the EU AI Act Annex IV §1-9 requirement set, and
recomputes the manifest hash to detect tampering.

The library function lives in :mod:`forgelm.verify` so integrators can
call it from their own pipelines without importing the CLI layer; this
module is the dispatcher + JSON-envelope wrapper.  The ``--pipeline``
mode's chain verifier lives in :mod:`forgelm.compliance` alongside the
pipeline-manifest writer.

Exit codes (per ``docs/standards/error-handling.md``):

- 0 — every required field present + manifest hash matches.
- 1 — operator-actionable input error: required field missing/empty (the
  artifact was never fully populated), file not found / not a regular
  file, malformed JSON, or invalid UTF-8 encoding.
- 2 — genuine runtime I/O failure on an existing, reachable file.
- 6 — integrity failure: the artifact is complete and readable but its
  recomputed manifest hash disagrees with the recorded one (single-artefact
  mode), or the pipeline manifest failed a structural / chain-integrity /
  per-stage-evidence check (``--pipeline`` mode).  The document was
  modified after generation.
"""

from __future__ import annotations

import json
import os
import sys
from typing import NoReturn

from ...verify import (
    VerifyAnnexIVResult,  # noqa: F401 — re-exported for the forgelm.cli facade
    is_annex_iv_integrity_failure,
    verify_annex_iv_artifact,  # noqa: F401 — re-exported for the forgelm.cli facade
)
from .._exit_codes import (
    EXIT_CONFIG_ERROR,
    EXIT_INTEGRITY_FAILURE,
    EXIT_SUCCESS,
    EXIT_TRAINING_ERROR,
)
from .._logging import logger


def _output_error_and_exit(output_format: str, msg: str, exit_code: int) -> NoReturn:
    """Mirror the family helper from sibling subcommand modules.

    ``indent=2`` matches the sibling ``_verify_integrity.py`` /
    ``_verify_gguf.py`` copies of this helper (and this module's own
    success-path ``_print_artefact_result`` / ``_run_pipeline_mode``) so
    this subcommand emits one consistent JSON shape on every branch
    (Wave 2 review finding: the error envelope was the one branch left
    unindented).
    """
    if output_format == "json":
        print(json.dumps({"success": False, "error": msg}, indent=2))
    else:
        logger.error(msg)
    sys.exit(exit_code)


def _classify_pipeline_violations(violations: list[str]) -> tuple[int, list[str]]:
    """Map a pipeline-manifest violation list to (exit_code, display_strings).

    Routing keys off the stable machine prefixes
    :data:`forgelm.compliance.PIPELINE_MANIFEST_IO_ERROR_PREFIX` and
    :data:`forgelm.compliance.PIPELINE_MANIFEST_INPUT_ERROR_PREFIX`, never
    on free text — a reworded violation must not be able to flip the
    exit-code contract (F-P4-OPUS-25).

    Precedence, **integrity first**:

    - Any *untagged* violation → ``EXIT_INTEGRITY_FAILURE`` (6): the
      manifest parsed and failed a structural, chain-integrity, or
      per-stage-evidence check.  Something rewrote the run's record.
    - Any ``IO_ERROR::`` violation → ``EXIT_TRAINING_ERROR`` (2): the
      manifest or a stage artefact exists but could not be read (locked
      file, mid-read I/O failure).  Retryable infrastructure problem.
    - Any ``INPUT_ERROR::`` violation → ``EXIT_CONFIG_ERROR`` (1): the
      manifest is absent (wrong directory) or unparseable.  The verifier
      never saw a payload, so it has no integrity verdict to give.
    - Any ``UNVERIFIED::`` violation → ``EXIT_CONFIG_ERROR`` (1): the
      verifier reached the evidence but nothing attested to it.  Not a
      pass and not tampering — no comparison happened.
    - No violations → ``EXIT_SUCCESS`` (0).

    Integrity moved to the front to close a masking bug: the shipped order
    returned on the tagged prefixes first, so a single unreadable stage
    artefact (2) or an unhashed one (1) *downgraded* a genuine tamper
    finding reported in the same run.  A verifier must never let a weaker
    finding hide a stronger one.

    The routing tokens are internal and are stripped from every returned
    display string so they never reach operator-facing output.
    """
    from forgelm.compliance import (
        PIPELINE_MANIFEST_INPUT_ERROR_PREFIX,
        PIPELINE_MANIFEST_IO_ERROR_PREFIX,
        PIPELINE_MANIFEST_UNVERIFIED_PREFIX,
    )

    display: list[str] = []
    runtime_io_error = False
    input_error = False
    unverified = False
    integrity_error = False
    for violation in violations:
        if violation.startswith(PIPELINE_MANIFEST_IO_ERROR_PREFIX):
            runtime_io_error = True
            display.append(violation[len(PIPELINE_MANIFEST_IO_ERROR_PREFIX) :])
        elif violation.startswith(PIPELINE_MANIFEST_INPUT_ERROR_PREFIX):
            input_error = True
            display.append(violation[len(PIPELINE_MANIFEST_INPUT_ERROR_PREFIX) :])
        elif violation.startswith(PIPELINE_MANIFEST_UNVERIFIED_PREFIX):
            unverified = True
            display.append(violation[len(PIPELINE_MANIFEST_UNVERIFIED_PREFIX) :])
        else:
            integrity_error = True
            display.append(violation)

    if not violations:
        return EXIT_SUCCESS, display
    if integrity_error:
        return EXIT_INTEGRITY_FAILURE, display
    if runtime_io_error:
        return EXIT_TRAINING_ERROR, display
    if input_error or unverified:
        return EXIT_CONFIG_ERROR, display
    return EXIT_INTEGRITY_FAILURE, display


def _run_pipeline_mode(path: str, output_format: str) -> NoReturn:
    """Verify a pipeline run directory's manifest + chain integrity.

    Reads ``<path>/compliance/pipeline_manifest.json``, runs the in-
    memory verifier + per-stage training_manifest existence check, and
    prints / exits.  Extracted from :func:`_run_verify_annex_iv_cmd`
    for Sonar python:S3776 cognitive-complexity hygiene.

    Exit-code mapping is owned by :func:`_classify_pipeline_violations`;
    see its docstring for the four-way split.
    """
    from forgelm.verify import verify_pipeline_manifest_report

    # Defensive try/except: the verifier already maps OSError /
    # JSONDecodeError / UnicodeDecodeError to violation strings, but a
    # future change there could let an exception bubble — fail loud rather
    # than swallow.  ``UnicodeDecodeError`` needs its own branch because it
    # is a ``ValueError`` subclass, not an ``OSError`` one, and is a caller-
    # input verdict (exit 1) rather than a retryable I/O failure (exit 2) —
    # matching the three sibling single-artefact paths (D1-08).
    try:
        report = verify_pipeline_manifest_report(path)
        violations = report.violations
    except UnicodeDecodeError as exc:
        if output_format == "json":
            print(
                json.dumps(
                    {
                        "success": False,
                        "mode": "pipeline",
                        "path": os.path.abspath(path),
                        "violations": [f"pipeline manifest is not valid UTF-8: {exc}"],
                    },
                    indent=2,
                )
            )
        else:
            print(f"FAIL: pipeline manifest at {path} — not valid UTF-8: {exc}")
        sys.exit(EXIT_CONFIG_ERROR)
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

    exit_code, display_violations = _classify_pipeline_violations(violations)

    if output_format == "json":
        print(
            json.dumps(
                {
                    "success": not violations,
                    "mode": "pipeline",
                    "path": os.path.abspath(path),
                    "violations": display_violations,
                    # How much was actually examined, and whether the chain
                    # manifest's own hash attested to it.  A verifier that
                    # prints only a verdict lets "OK" mean both "checked
                    # everything" and "checked nothing"; these four fields are
                    # what make those distinguishable to CI (F-PR54-H6/H7).
                    **report.to_dict(),
                },
                indent=2,
            )
        )
    elif not violations:
        # hash_state separates "valid" from "verified".  A pre-v0.8.0 archived
        # manifest carries no manifest_hash: its structural and chain rules
        # passed, but nothing attested to its non-chain fields, and saying
        # plain "OK" would overclaim.
        if report.hash_state == "verified":
            print(f"OK: pipeline manifest at {path} (hash verified, {report.evidence_verified} stage artefact(s))")
        else:
            print(f"OK (UNVERIFIED): pipeline manifest at {path} — no manifest_hash; tampering not checked")
    else:
        print(f"FAIL: pipeline manifest at {path}")
        for v in display_violations:
            print(f"  - {v}")

    sys.exit(exit_code)


def _verify_artefact_and_handle_io_errors(path: str, output_format: str) -> VerifyAnnexIVResult:
    """Run :func:`forgelm.verify.verify_annex_iv_artifact` with the
    documented I/O error-mapping policy.

    Extracted from :func:`_run_verify_annex_iv_cmd` for Sonar
    python:S3776 cognitive-complexity hygiene.  Each ``except`` branch
    exits the process via ``_output_error_and_exit`` — the function
    only returns on success.

    Exit-code mapping (per ``docs/reference/verify_annex_iv_subcommand.md``):

    - ``FileNotFoundError`` / ``IsADirectoryError`` / ``JSONDecodeError`` /
      ``UnicodeDecodeError`` → ``EXIT_CONFIG_ERROR (1)`` (operator-
      actionable input error — a corrupted, truncated, or non-JSON binary
      file is not fixable by retrying the same read).
    - ``OSError`` (catch-all, must follow ``FileNotFoundError`` because
      Python's OSError hierarchy makes it a subclass) →
      ``EXIT_TRAINING_ERROR (2)`` (genuine runtime I/O failure).

    ``UnicodeDecodeError`` is a :class:`ValueError` subclass, not an
    :class:`OSError` subclass, so it needs its own branch — without it, a
    target file with invalid UTF-8 bytes (disk corruption, an interrupted
    write, or an operator pointing the tool at a binary file) propagated
    uncaught out of :func:`forgelm.verify.verify_annex_iv_artifact`,
    crashing with a raw traceback instead of the documented envelope
    (mirrors the fix applied to
    ``forgelm.compliance._read_audit_log_lines`` for the same failure mode
    on the audit-log side).
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
    except UnicodeDecodeError as exc:
        _output_error_and_exit(
            output_format,
            f"Annex IV artifact at {path!r} is not valid UTF-8: {exc}.",
            EXIT_CONFIG_ERROR,
        )
    except OSError as exc:
        _output_error_and_exit(
            output_format,
            f"Could not read Annex IV artifact {path!r}: {exc}.",
            EXIT_TRAINING_ERROR,
        )


def _print_artefact_result(result: VerifyAnnexIVResult, path: str, output_format: str) -> None:
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
    if result.valid:
        sys.exit(EXIT_SUCCESS)
    # A manifest-hash mismatch on an otherwise-complete artefact is
    # tampering (6); missing/blank required fields mean the operator never
    # finished populating the document (1).
    sys.exit(EXIT_INTEGRITY_FAILURE if is_annex_iv_integrity_failure(result) else EXIT_CONFIG_ERROR)


__all__ = [
    "VerifyAnnexIVResult",
    "_run_verify_annex_iv_cmd",
    "verify_annex_iv_artifact",
]
