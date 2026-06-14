"""argparse type validators + shared subparser flag registrar."""

from __future__ import annotations

import argparse


def _non_negative_int(value: str) -> int:
    """argparse type for flags that must be >= 0 (e.g. --near-dup-threshold).

    Raising :class:`argparse.ArgumentTypeError` lets argparse render a
    standard "invalid value" error and exit through its usual path,
    without us having to thread parser.error() into every call site.
    """
    try:
        ivalue = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(f"invalid integer: {value!r}") from exc
    if ivalue < 0:
        raise argparse.ArgumentTypeError(f"value must be ≥ 0, got {ivalue}")
    return ivalue


def _positive_int(value: str) -> int:
    """argparse type for flags that must be >= 1 (e.g. ``--workers``).

    Mirrors :func:`_non_negative_int` but rejects 0 so a typo
    (``--workers 0``) produces an immediate, helpful CLI error instead
    of getting validated downstream as ``ValueError`` deep inside an
    audit call.
    """
    try:
        ivalue = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(f"invalid integer: {value!r}") from exc
    if ivalue < 1:
        raise argparse.ArgumentTypeError(f"value must be >= 1, got {ivalue}")
    return ivalue


def _non_negative_float(value: str) -> float:
    """argparse type for ``--jaccard-threshold`` and similar floats.

    Mirrors :func:`_non_negative_int` (raises ``ArgumentTypeError`` so
    argparse owns the error path). Phase 12 uses this for the MinHash
    Jaccard threshold which must lie in ``[0.0, 1.0]`` -- values outside
    that range surface a clear error rather than producing nonsensical
    duplicate counts.
    """
    try:
        fvalue = float(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(f"invalid float: {value!r}") from exc
    if fvalue < 0.0 or fvalue > 1.0:
        raise argparse.ArgumentTypeError(f"value must be in [0.0, 1.0], got {fvalue}")
    return fvalue


def _sampling_temperature(value: str) -> float:
    """argparse type for ``--temperature`` (sampling temperature in [0.0, 2.0]).

    Mirrors :func:`_non_negative_float` but allows the conventional upper
    bound of 2.0 used by HF/OpenAI-style samplers, so a typo like
    ``--temperature -3`` or ``--temperature 50`` fails at the CLI boundary
    instead of producing a malformed generation config downstream.
    """
    try:
        fvalue = float(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(f"invalid float: {value!r}") from exc
    if fvalue < 0.0 or fvalue > 2.0:
        raise argparse.ArgumentTypeError(f"value must be in [0.0, 2.0], got {fvalue}")
    return fvalue


def _add_common_subparser_flags(p: argparse.ArgumentParser, *, include_output_format: bool) -> None:
    """Register the shared --quiet / --log-level / --output-format flags.

    Uses ``default=argparse.SUPPRESS`` so an explicit flag at the main-parser
    level (before the subcommand) is not clobbered when the subparser fills
    in its own defaults.
    """
    if include_output_format:
        p.add_argument(
            "--output-format",
            type=str,
            default=argparse.SUPPRESS,
            choices=["text", "json"],
            help="Output format: text (default) or json.",
        )
    p.add_argument("-q", "--quiet", action="store_true", default=argparse.SUPPRESS, help="Suppress INFO logs.")
    p.add_argument(
        "--log-level",
        type=str,
        default=argparse.SUPPRESS,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Set logging verbosity (default: INFO).",
    )
