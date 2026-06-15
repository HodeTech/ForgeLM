"""Phase 15 Wave 2 Task 11 — operator-supplied strip-pattern with ReDoS guard.

The ``--strip-pattern REGEX`` flag lets operators delete known-boilerplate
lines that ForgeLM's automatic dedup heuristic misses (running headers
with varying content, watermark lines, DOI / journal headers, etc.).
Because the pattern is **operator-controlled**, two failure modes have
to be ruled out before the ingestion run starts:

1. **Pathological back-tracking** — e.g. ``(a+)+b`` against a long
   string of ``a`` characters is the textbook ReDoS shape. Validating
   the pattern up-front at CLI parse time catches the most common
   anti-patterns described in ``docs/standards/regex.md`` (rules 4 + 6).
2. **Run-time hang** — even a pattern that *looks* safe can hit a
   degenerate input. A SIGALRM-based timeout per regex bounds the
   worst-case match cost; on Windows + non-main threads the timeout is
   a no-op and the operator is expected to vet the pattern themselves
   (same trade-off ``forgelm reverse-pii --type custom`` makes).

The public surface is intentionally small:

* :func:`validate_strip_pattern` — CLI-time structural validation.
* :func:`compile_strip_patterns` — turn a list of validated patterns
  into compiled :class:`re.Pattern` objects.
* :func:`apply_strip_patterns` — strip every matching span (or whole
  line, in MULTILINE mode) with a per-pattern SIGALRM guard.
* :class:`StripPatternError` — single exception type the CLI catches.

This module imports only stdlib so it can run before any ingestion
extras are installed.
"""

from __future__ import annotations

import logging
import os
import re
import signal
import threading
import time
from typing import List, Optional, Tuple

logger = logging.getLogger("forgelm.ingestion.strip_pattern")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_TIMEOUT_S: int = 5
"""Per-pattern match budget in seconds.

Five seconds is generous for legitimate boilerplate-stripping on a
typical 200-page corpus (each page's extracted text is < 4 KB and a
sane regex finishes in microseconds), but small enough that a
pathological pattern aborts the run before it eats the user's session.
Operator can opt out via ``--strip-pattern-no-timeout`` for
known-safe patterns on POSIX main thread; the guard is always a no-op
elsewhere.
"""

_MAX_PATTERN_LENGTH: int = 2048
"""Generous upper bound on the operator's pattern string.

ReDoS is largely a function of pattern complexity, not length, but a
genuinely useful boilerplate-stripping pattern fits inside this
envelope easily. Anything over 2 KB is overwhelmingly more likely to
be a paste-bin accident than a legitimate use case; rejecting it at
CLI-parse time saves a confusing run-time hang.
"""


class StripPatternError(ValueError):
    """Raised when a strip-pattern is malformed or fails the ReDoS guard.

    Inherits from :class:`ValueError` so existing CLI dispatchers that
    catch ``ValueError`` (per ``forgelm/cli/subcommands/_ingest.py``)
    keep working without an explicit ``except`` for this subclass.
    """


# ---------------------------------------------------------------------------
# Structural validation (CLI-parse time)
# ---------------------------------------------------------------------------


def validate_strip_pattern(pattern: str) -> str:
    """Validate ``pattern`` up-front; return it verbatim on success.

    Mirrors the SonarCloud ``python:S5852`` / regex.md rule 4 + 6
    structural checks ForgeLM applies to its own internal regexes:

    * Reject empty / overlong patterns.
    * Reject patterns with **two unbounded greedy / lazy quantifiers
      in sequence** that share a character class (rule 4) — the
      ``(a+)+b`` / ``(.+?)(.+)`` shapes.
    * Reject patterns combining ``.*?`` with a back-reference under
      DOTALL (rule 6) — even when CPython handles it, SonarCloud
      flags it and the operator can rewrite as multiple narrower
      patterns.
    * Reject patterns that fail :func:`re.compile` (syntax error).

    Raises :class:`StripPatternError` with a message that names the
    offending construct so the operator can rewrite the pattern.
    """
    if not pattern:
        raise StripPatternError("--strip-pattern must be a non-empty regex.")
    if len(pattern) > _MAX_PATTERN_LENGTH:
        raise StripPatternError(
            f"--strip-pattern is too long ({len(pattern)} chars; max {_MAX_PATTERN_LENGTH}). "
            "If your boilerplate is more than 2 KB, split it across multiple --strip-pattern flags."
        )
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        raise StripPatternError(f"--strip-pattern {pattern!r} is not a valid regular expression: {exc}") from exc

    _check_unbounded_quantifier_sequence(pattern)
    _check_dotall_backreference(pattern, compiled.flags)

    return pattern


_NESTED_UNBOUNDED_REJECTION_MSG = (
    "contains a nested unbounded quantifier (e.g. `(a+)+`, `(\\w+)+`, "
    "`(\\w+\\s+)+`), which is a textbook ReDoS shape (regex.md rule 4). "
    "Rewrite with bounded repetition (e.g. `a{1,100}`) or split across "
    "multiple narrower --strip-pattern flags."
)


def _scan_atom_end(pattern: str, i: int, n: int) -> int:
    """Return the index just past the atom that starts at ``pattern[i]``.

    An *atom* is one of: an escape pair (``\\X``), a character class
    (``[…]`` honouring escapes), or a single literal character. Group
    boundaries are handled by the caller, not here. Extracted from
    :func:`_check_unbounded_quantifier_sequence` purely to keep the
    caller below the Sonar S3776 cognitive-complexity ceiling.
    """
    char = pattern[i]
    if char == "\\":
        return i + 2
    if char == "[":
        j = i + 1
        while j < n:
            if pattern[j] == "\\":
                j += 2
                continue
            if pattern[j] == "]":
                break
            j += 1
        return j + 1
    return i + 1


def _advance_past_brace_quantifier(pattern: str, atom_end: int, n: int) -> int:
    """Skip a ``{n,m}`` quantifier span so the outer loop won't re-parse digits."""
    j = atom_end + 1
    while j < n and pattern[j] != "}":
        j += 1
    return j + 1


def _close_group_or_raise(
    pattern: str,
    i: int,
    n: int,
    stack: List[Tuple[int, bool]],
) -> int:
    """Handle ``)`` at ``pattern[i]``; raise on the nested-unbounded shape.

    Pops the current group, checks the immediately-following character
    for ``+`` / ``*``, and propagates the "this atom was unbounded" flag
    to the parent group. Returns the post-quantifier index so the
    caller's main loop can advance.

    F-P6-OPUS-06: a just-closed group that ended on an unbounded atom
    is *itself* an unbounded sub-expression even when its own ``)`` is
    NOT directly quantified. Wrapping the inner quantifier in an extra
    group — ``((a+))+b`` — used to reset the parent's flag to ``False``,
    so the outer ``+`` no longer saw an unbounded inner atom and the
    catastrophic shape slipped past. The parent therefore inherits
    ``outer_quantifier-unbounded OR inner_unbounded`` so that an
    enclosing quantifier on a redundantly-wrapped unbounded group is
    still rejected.
    """
    _open_idx, inner_unbounded = stack.pop() if len(stack) > 1 else (-1, False)
    outer_quantifier = pattern[i + 1] if i + 1 < n else ""
    if inner_unbounded and outer_quantifier in ("+", "*"):
        raise StripPatternError(f"--strip-pattern {pattern!r} {_NESTED_UNBOUNDED_REJECTION_MSG}")
    if stack:
        top_open, _ = stack[-1]
        group_unbounded = outer_quantifier in ("+", "*") or inner_unbounded
        stack[-1] = (top_open, group_unbounded)
    next_i = i + 1
    if outer_quantifier in ("+", "*", "?"):
        next_i += 1
    return next_i


def _check_unbounded_quantifier_sequence(pattern: str) -> None:
    """Reject patterns with two unbounded quantifiers in sequence.

    The classic ``(a+)+`` ReDoS shape — and its character-class
    cousins ``(\\w+)+``, ``(\\d+)+``, ``(\\s+)+``, ``([abc]+)+``,
    ``(\\w+\\s+)+`` — explodes on adversarial input even when CPython's
    engine is "well-behaved" in practice. Phase 15 round-1 review (S-1)
    found the original backward-walk validator skipped the escape-shape
    variants because stepping past a ``\\`` consumed the matching ``(``;
    the rewrite below walks **forward atom-by-atom**, tracking per
    group whether the most recently parsed atom was unbounded
    (quantified with ``+`` / ``*``). When a group's closing ``)`` is
    immediately followed by another ``+`` / ``*``, that is the
    nested-unbounded shape and we reject.

    The check is structural, not a full-AST parse — regex.md rule 4
    is about pattern *shape*, so an atom-by-atom forward scan is
    sufficient. The SIGALRM timeout remains the safety net for the
    long tail of exotic variants we still miss. Atom parsing and the
    close-paren handler are extracted into helpers (``_scan_atom_end``,
    ``_close_group_or_raise``, ``_advance_past_brace_quantifier``) to
    keep this function below Sonar S3776's cognitive-complexity ceiling.

    Scope (post-round-3 review S-A):
        This validator covers the **nested** unbounded shape
        documented in ``docs/standards/regex.md`` rule 4 — i.e. a
        group whose last atom carries ``+`` / ``*`` AND whose
        closing ``)`` is itself followed by another ``+`` / ``*``.
        Redundantly-wrapped variants (``((a+))+b``, ``(((a+)))+b``)
        are covered too: a closed group that ended on an unbounded
        atom propagates its unbounded flag up to the parent
        (F-P6-OPUS-06), so an enclosing quantifier still trips the
        reject even through extra grouping parentheses.
        Adjacent / sequential unbounded shapes (``(.+?)(.+)``,
        ``(?:a|b)+(c|d)+``, ``(.+?)[ \\t]*$``) are also mentioned
        by regex.md rule 4 but are **not** caught here. They
        empirically do not backtrack catastrophically under
        CPython 3.10–3.13 on adversarial 100-char input (verified
        in the round-3 review), so they fall back on the per-pattern
        SIGALRM timeout as the runtime safety net. Operators on
        non-CPython runtimes (PyPy 7.x, Pyston) should treat the
        SIGALRM net as the primary defence and consider replacing
        adjacent-quantified patterns with bounded alternatives.
        Alternation-branch variants where one branch is unbounded
        (``(a+|b)+c``) are also **not** caught here — the validator
        tracks only the **last atom per group**, not each branch. In
        ``(a+|b)+c`` the last parsed atom inside the group is the
        literal ``b``, which is bounded, so the outer ``+``
        bypasses the reject even though the ``a+`` branch can
        backtrack catastrophically (O(2^n), verified in review
        F-L-04). The SIGALRM timeout is the runtime defence for
        these alternation-branch shapes.
    """
    # Each stack entry is ``(open_index, last_atom_unbounded)``. The
    # bottom entry represents the implicit "outer" pattern; pushed
    # entries represent groups. ``last_atom_unbounded`` is updated to
    # match the most recently parsed atom — bounded atoms reset it,
    # unbounded ones set it.
    stack: List[Tuple[int, bool]] = [(-1, False)]
    i = 0
    n = len(pattern)
    while i < n:
        char = pattern[i]
        if char == "(":
            stack.append((i, False))
            i += 1
            continue
        if char == ")":
            i = _close_group_or_raise(pattern, i, n, stack)
            continue

        atom_end = _scan_atom_end(pattern, i, n)
        quantifier = pattern[atom_end] if atom_end < n else ""
        if stack:
            top_open, _ = stack[-1]
            stack[-1] = (top_open, quantifier in ("+", "*"))

        i = atom_end
        if quantifier in ("+", "*", "?"):
            i += 1
        elif quantifier == "{":
            i = _advance_past_brace_quantifier(pattern, atom_end, n)


def _check_dotall_backreference(pattern: str, flags: int) -> None:
    """Reject ``.*?`` + back-reference under DOTALL.

    Mirrors regex.md rule 6 / SonarCloud python:S5852. Cheap to detect
    structurally: presence of ``.*?`` (or ``.+?``) **and** a back-reference
    (``\\1`` … or ``(?P=name)``) under :data:`re.DOTALL`. The pattern
    flags are read from the compiled object so operator-supplied inline
    ``(?s)`` flags are honoured.
    """
    if not (flags & re.DOTALL):
        return
    if ".*?" not in pattern and ".+?" not in pattern:
        return
    # Look for any back-reference: ``\<digit>`` or ``(?P=name)``.
    if re.search(r"\\[1-9]|\(\?P=", pattern):
        raise StripPatternError(
            f"--strip-pattern {pattern!r} combines a lazy `.*?` / `.+?` with a "
            "back-reference under DOTALL — this is the SonarCloud python:S5852 "
            "polynomial-runtime shape. Rewrite as a line-walker or split the "
            "pattern across multiple --strip-pattern flags."
        )


# ---------------------------------------------------------------------------
# Compilation
# ---------------------------------------------------------------------------


def compile_strip_patterns(patterns: List[str]) -> List[Tuple[str, re.Pattern[str]]]:
    """Compile a list of validated strip-patterns to ``(raw, compiled)`` pairs.

    The raw string is preserved alongside the compiled pattern so the
    timeout warning can quote the offending pattern verbatim instead of
    re-deriving it from :attr:`re.Pattern.pattern` (which collapses some
    operator-supplied character classes).
    """
    out: List[Tuple[str, re.Pattern[str]]] = []
    for raw in patterns:
        validate_strip_pattern(raw)
        # MULTILINE so ``^`` / ``$`` work line-by-line, which is the dominant
        # use case (strip an entire boilerplate line, not a substring).
        compiled = re.compile(raw, flags=re.MULTILINE)
        out.append((raw, compiled))
    return out


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------


def apply_strip_patterns(
    text: str,
    patterns: List[Tuple[str, re.Pattern[str]]],
    *,
    timeout_s: Optional[int] = DEFAULT_TIMEOUT_S,
    logger_override: Optional[logging.Logger] = None,
) -> Tuple[str, int]:
    """Strip every match from ``text``; return ``(new_text, total_matches)``.

    Patterns are applied in order. Each match is replaced with an empty
    string. On POSIX main-thread runs a per-pattern SIGALRM budget of
    ``timeout_s`` seconds bounds the worst-case backtracking cost; on
    Windows or worker threads the guard is a no-op (same trade-off
    ``forgelm reverse-pii --type custom`` makes).

    When a timeout fires, the offending pattern is **skipped** with a
    loud ``WARNING`` and the remaining patterns continue to apply.
    Skipping is the right trade-off: aborting the ingestion run would
    drop user data on the floor, while silently swallowing the timeout
    would leave the operator wondering why their pattern did nothing.

    Args:
        text: Text to scan.
        patterns: Output of :func:`compile_strip_patterns`.
        timeout_s: Per-pattern budget. ``None`` disables the guard
            entirely (used by ``--strip-pattern-no-timeout``). A
            non-positive integer is **rejected** with :class:`ValueError`
            — review round-2 (S-1) found that silently treating
            ``0`` / negatives as "no timeout" hid operator misconfiguration
            of the documented 5-second default.
        logger_override: Test seam; defaults to the module logger.

    Returns:
        The stripped text and the total number of substitutions made.
    """
    if timeout_s is not None and timeout_s <= 0:
        raise ValueError(
            f"timeout_s must be a positive int or None (got {timeout_s!r}); "
            "use None to disable the SIGALRM guard explicitly."
        )

    use_logger = logger_override or logger
    if not patterns or not text:
        return text, 0

    use_alarm = (
        timeout_s is not None
        and os.name == "posix"
        and hasattr(signal, "SIGALRM")
        and threading.current_thread() is threading.main_thread()
    )

    total_subs = 0
    out = text
    for raw, compiled in patterns:
        # Round-5 review (S-E) perf nit: if an earlier pattern consumed
        # the entire text, skip the remaining ones — each would otherwise
        # set up + tear down a SIGALRM around a no-op subn call.
        if not out:
            break
        new_out, subs = _apply_one(out, raw, compiled, timeout_s, use_alarm, use_logger)
        out = new_out
        total_subs += subs
    return out, total_subs


def _apply_one(
    text: str,
    raw: str,
    compiled: re.Pattern[str],
    timeout_s: Optional[int],
    use_alarm: bool,
    use_logger: logging.Logger,
) -> Tuple[str, int]:
    """Apply one pattern under the ReDoS guard; warn + skip on timeout."""
    if not use_alarm or timeout_s is None:
        new_out, subs = compiled.subn("", text)
        return new_out, subs

    def _alarm(_sig, _frame):  # pragma: no cover — exercised via integration tests
        raise OSError(f"strip-pattern {raw!r} exceeded {timeout_s}s (ReDoS guard)")

    previous_handler = signal.signal(signal.SIGALRM, _alarm)
    # Round-3 review (Gemini): the inner alarm must NOT extend an outer
    # caller's shorter remaining budget. ``signal.alarm(N)`` returns the
    # seconds remaining on any prior alarm; clamp our budget to the
    # smaller of ``timeout_s`` and that outer remainder so a nested
    # caller can never exceed the surrounding deadline.
    previous_remaining = signal.alarm(0)
    if previous_remaining > 0:
        chosen = min(timeout_s, previous_remaining)
    else:
        chosen = timeout_s
    signal.alarm(chosen)
    started = time.monotonic()
    try:
        new_out, subs = compiled.subn("", text)
        return new_out, subs
    except OSError as exc:
        use_logger.warning(
            "Skipping --strip-pattern (timeout): %s. Remaining patterns will still apply.",
            exc,
        )
        return text, 0
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_remaining > 0:
            # Honour the outer caller's ORIGINAL remaining budget (not
            # ``chosen``) minus the wall-clock seconds we burned. Round
            # up partial seconds and clamp to ≥1 so we never silently
            # cancel the outer alarm.
            elapsed = time.monotonic() - started
            remaining = max(int(previous_remaining - elapsed) + 1, 1)
            signal.alarm(remaining)


__all__ = [
    "DEFAULT_TIMEOUT_S",
    "StripPatternError",
    "validate_strip_pattern",
    "compile_strip_patterns",
    "apply_strip_patterns",
]
