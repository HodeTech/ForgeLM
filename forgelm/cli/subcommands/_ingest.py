"""``forgelm ingest`` dispatcher (Phase 11 raw-document → JSONL flow).

Phase 15 (v0.6.0) added the script-sanity / glyph-normalisation /
strip-pattern / page-range / front-matter / strip-urls / quality
pre-signal knobs; they all funnel through the same dispatch path so
the JSON envelope on the way out stays a single-source contract.
"""

from __future__ import annotations

import json
import sys
from typing import NoReturn, Optional, Tuple

from ..._strip_pattern import DEFAULT_TIMEOUT_S as _STRIP_PATTERN_DEFAULT_TIMEOUT_S
from .._exit_codes import EXIT_CONFIG_ERROR, EXIT_TRAINING_ERROR
from .._logging import logger


def _emit_error_and_exit(
    exc: Exception,
    *,
    output_format: str,
    exit_code: int,
    log_prefix: str = "%s",
) -> NoReturn:
    """Centralised JSON-vs-text error envelope + ``sys.exit`` helper.

    Round-2 review (nit on _ingest.py:82-168): the dispatcher used to
    repeat the same ``if json: print(...) else: logger.error(...)``
    pattern in every ``except`` block. Centralising the envelope keeps
    the four code paths byte-identical in behaviour while removing
    duplication, and lowers the cognitive-complexity score Sonar
    python:S3776 flagged on ``_run_ingest_cmd``.
    """
    if output_format == "json":
        print(json.dumps({"success": False, "error": str(exc)}))
    else:
        logger.error(log_prefix, exc)
    sys.exit(exit_code)


def _parse_page_range(value: Optional[str]) -> Optional[Tuple[int, int]]:
    """Parse ``--page-range START-END`` into a ``(start, end)`` tuple.

    Raises ``ValueError`` with an operator-facing message on malformed
    input so the surrounding ``except ValueError`` block converts it to
    EXIT_CONFIG_ERROR.
    """
    if value is None:
        return None
    raw = value.strip()
    if "-" not in raw:
        raise ValueError(f"--page-range must be 'START-END' (e.g. '12-193'); got {raw!r}.")
    parts = raw.split("-")
    if len(parts) != 2:
        raise ValueError(f"--page-range must be exactly two integers joined by '-'; got {raw!r}.")
    try:
        start = int(parts[0])
        end = int(parts[1])
    except ValueError as exc:
        raise ValueError(f"--page-range parts must be integers (e.g. '12-193'); got {raw!r}.") from exc
    return (start, end)


def _resolve_normalise_profile(args) -> str:
    """Resolve --normalise-profile vs --no-normalise-unicode vs --language-hint.

    Phase 15 round-1 review (C-2): the original implementation
    defaulted to the ``turkish`` profile unconditionally, which
    silently rewrote legitimate non-Turkish letters (Norwegian
    ``ø``, Estonian ``Õ``, math ``÷``). The fix couples the profile
    to the operator's language hint:

    * Explicit ``--no-normalise-unicode`` always wins (``"none"``).
    * Explicit ``--normalise-profile X`` wins next (operator
      override).
    * Otherwise, derive from ``--language-hint``: ``tr`` → ``turkish``;
      every other hint (and the unset case) → ``"none"``.

    So a Turkish operator who already passes ``--language-hint tr``
    keeps getting normalisation "for free"; everyone else is safe by
    default.
    """
    if getattr(args, "no_normalise_unicode", False):
        return "none"
    explicit_profile = getattr(args, "normalise_profile", None)
    if explicit_profile is not None:
        return explicit_profile
    language_hint = (getattr(args, "language_hint", None) or "").lower()
    if language_hint == "tr":
        return "turkish"
    return "none"


def _run_ingest_cmd(args, output_format: str) -> None:
    """Phase 11 dispatch (with Phase 15 knobs): raw documents → SFT-ready JSONL."""
    # Phase 12.5: --all-mask is a shorthand that ORs into the two individual
    # masking flags. Resolve it here at the CLI boundary so ``ingest_path``
    # keeps its narrow API (only ``pii_mask`` / ``secrets_mask`` booleans).
    all_mask = getattr(args, "all_mask", False)
    pii_mask = bool(getattr(args, "pii_mask", False)) or all_mask
    secrets_mask = bool(getattr(args, "secrets_mask", False)) or all_mask

    # Lazy import: ``forgelm.ingestion`` is kept out of module top-level so
    # ``import forgelm.cli`` (run on every console-script invocation, including
    # ``forgelm --help``) does not eagerly pull the ingestion package — and its
    # transitive optional-dependency probes — into ``sys.modules``. The three
    # names are imported together *before* the try-block so that
    # ``OptionalDependencyError`` is guaranteed to be bound when the
    # ``except`` clause runs; if the import itself fails it propagates as a
    # plain ``ImportError`` (a real bug, not an operator-facing condition).
    from ..._strip_pattern import StripPatternError
    from ...ingestion import OptionalDependencyError, ingest_path, summarize_result

    # Phase 15 args (Wave 1 + Wave 2). Resolved up here so the
    # ``ingest_path`` call below stays a single straight-line.
    try:
        page_range = _parse_page_range(getattr(args, "page_range", None))
    except ValueError as exc:
        _emit_error_and_exit(exc, output_format=output_format, exit_code=EXIT_CONFIG_ERROR)
    strip_patterns = getattr(args, "strip_pattern", None) or None
    strip_pattern_timeout = (
        None if getattr(args, "strip_pattern_no_timeout", False) else _STRIP_PATTERN_DEFAULT_TIMEOUT_S
    )
    quality_presignal = not getattr(args, "no_quality_presignal", False)

    # Round-5 independent review (C-B): if the operator passes a
    # ``--language-hint`` that the script-sanity layer doesn't have an
    # allow-list for, the check would silently no-op and produce a
    # ``script_sanity_summary`` block with ``max_ratio=0.0`` — the
    # textbook silent-failure UX Phase 15 was launched to fix, just
    # transplanted to a new surface. Warn loudly so the operator either
    # picks a supported hint or accepts the no-op.
    language_hint_raw = getattr(args, "language_hint", None)
    if language_hint_raw:
        from ..._script_sanity import SUPPORTED_LANGUAGES

        if language_hint_raw.lower() not in SUPPORTED_LANGUAGES:
            logger.warning(
                "--language-hint %r is not in the script-sanity allow-list (%s). "
                "Script-sanity check will silently no-op for this corpus — the "
                "`script_sanity_summary` block in notes_structured will report "
                "`max_ratio: 0.0` even on heavily corrupted text. Use one of the "
                "supported codes or omit the flag to disable the check explicitly.",
                language_hint_raw,
                ", ".join(SUPPORTED_LANGUAGES),
            )

    sanity_threshold_raw = getattr(args, "script_sanity_threshold", None)
    sanity_kwargs = {} if sanity_threshold_raw is None else {"script_sanity_threshold": sanity_threshold_raw}

    try:
        result = ingest_path(
            args.input_path,
            output_path=args.output,
            chunk_size=args.chunk_size,
            overlap=args.overlap,
            strategy=args.strategy,
            recursive=args.recursive,
            pii_mask=pii_mask,
            secrets_mask=secrets_mask,
            input_encoding=getattr(args, "input_encoding", None),
            chunk_tokens=getattr(args, "chunk_tokens", None),
            overlap_tokens=getattr(args, "overlap_tokens", 0),
            tokenizer=getattr(args, "tokenizer", None),
            language_hint=getattr(args, "language_hint", None),
            normalise_profile=_resolve_normalise_profile(args),
            keep_md_frontmatter=getattr(args, "keep_md_frontmatter", False),
            epub_skip_frontmatter=not getattr(args, "epub_no_skip_frontmatter", False),
            keep_frontmatter=getattr(args, "keep_frontmatter", False),
            page_range=page_range,
            strip_patterns=strip_patterns,
            strip_pattern_timeout=strip_pattern_timeout,
            strip_urls=getattr(args, "strip_urls", "keep"),
            quality_presignal=quality_presignal,
            **sanity_kwargs,
        )
    except StripPatternError as exc:
        # Phase 15 Wave 2: a ReDoS-prone --strip-pattern aborts the run
        # before any I/O. StripPatternError is a ValueError subclass so it
        # would be caught by the generic block below; routed here first so
        # the error message says "strip-pattern" specifically, not "ingest
        # failed". Exit code stays EXIT_CONFIG_ERROR per the contract.
        _emit_error_and_exit(
            exc,
            output_format=output_format,
            exit_code=EXIT_CONFIG_ERROR,
            log_prefix="Strip-pattern rejected: %s",
        )
    except (
        FileNotFoundError,  # NOSONAR — OSError subclass; caught before bare OSError so a bad --input path stays a config error
        PermissionError,  # NOSONAR — OSError subclass; an unreadable --input / unwritable --output dir is operator-fixable, not retry-able
        IsADirectoryError,  # NOSONAR — OSError subclass; --output pointing at a directory is operator-fixable, not retry-able
        ValueError,
    ) as exc:
        # Operator-input errors → EXIT_CONFIG_ERROR ("fix your invocation").
        # FileNotFoundError = missing --input path; PermissionError /
        # IsADirectoryError = a wrong --input / --output that the operator
        # fixes by correcting the path or its permissions, not by retrying;
        # ValueError (incl. the IngestParameterError / StripPatternError
        # subclasses) = invalid chunking / page-range / strip-pattern /
        # input-encoding parameters. These OSError subclasses are listed
        # before the bare-OSError clause below so they route here, not to the
        # runtime bucket. A CI/CD pipeline that auto-retries on exit 2 must not
        # loop on an operator-fixable permission error.
        _emit_error_and_exit(
            exc,
            output_format=output_format,
            exit_code=EXIT_CONFIG_ERROR,
            log_prefix="Ingest failed: %s",
        )
    except OSError as exc:
        # Genuine mid-run I/O fault → EXIT_TRAINING_ERROR so CI/CD retry logic
        # treats it like other mid-run crashes rather than a fix-the-YAML
        # config error. This bucket is ENOSPC surfacing from the atomic
        # mid-write, locked-file open() errors, and broken-symlink walk
        # failures — none of which the operator fixes by editing parameters.
        # The operator-fixable OSError subclasses (FileNotFoundError /
        # PermissionError / IsADirectoryError) are caught above. Matches the
        # exit-code contract table (2 = crashed/failed mid-run).
        _emit_error_and_exit(
            exc,
            output_format=output_format,
            exit_code=EXIT_TRAINING_ERROR,
            log_prefix="Ingest failed (I/O): %s",
        )
    except OptionalDependencyError as exc:
        # Catch the narrow optional-extras subclass only.  Plain ``ImportError``
        # would mask genuine bugs (e.g. an internal forgelm import error) under
        # the same install-hint envelope; letting those propagate preserves the
        # original traceback for the operator.  Convention across subcommands:
        # a missing optional extra is a *runtime* failure of the dispatched
        # feature, not a config validation failure — exit with
        # EXIT_TRAINING_ERROR so CI/CD retry logic treats it the same way.
        _emit_error_and_exit(exc, output_format=output_format, exit_code=EXIT_TRAINING_ERROR)

    if output_format == "json":
        print(
            json.dumps(
                {
                    "success": True,
                    "output_path": str(result.output_path),
                    "chunk_count": result.chunk_count,
                    "files_processed": result.files_processed,
                    "files_skipped": result.files_skipped,
                    "total_chars": result.total_chars,
                    "format_counts": result.format_counts,
                    "pii_redaction_counts": result.pii_redaction_counts,
                    "secrets_redaction_counts": result.secrets_redaction_counts,
                    "pdf_header_footer_lines_stripped": result.pdf_header_footer_lines_stripped,
                    # Phase 15 additions — additive fields, no rename of any
                    # pre-Phase-15 key so v0.5 consumers keep parsing.
                    "pdf_paragraph_packed_lines_stripped": result.pdf_paragraph_packed_lines_stripped,
                    "script_sanity_triggered": result.script_sanity_triggered,
                    "strip_pattern_substitutions": result.strip_pattern_substitutions,
                    "urls_handled": result.urls_handled,
                    "frontmatter_pages_dropped": result.frontmatter_pages_dropped,
                    "notes": result.extra_notes,
                    "notes_structured": result.notes_structured,
                },
                indent=2,
            )
        )
    else:
        print(summarize_result(result))
