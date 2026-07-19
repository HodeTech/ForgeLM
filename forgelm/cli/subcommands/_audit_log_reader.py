"""Single-source audit-log JSONL reader for the approval / approvals / purge family.

Background — Wave 2a Round-1 review (XPR-01) flagged that ForgeLM had three
near-identical JSONL parsers across :mod:`._approve` and :mod:`._approvals`,
with a fourth coming in Phase 21 (``forgelm purge``).  Each copy duplicated
the malformed-line accounting, the OSError handling, and the
``isinstance(event, dict)`` guard — so a future fix to any one of those
policies (e.g. switching to streaming reads, adding a maximum line length,
emitting structured error events) would have to be applied in ``N``
places at once.

This module is the single place that policy lives.  Two helpers cover the
two read patterns the family uses:

- :func:`iter_audit_events` — generator yielding ``(line_number, event)``
  pairs.  Two parsing modes: ``strict=False`` (default) skips malformed
  entries and emits a single summary warning at end (best for ``--pending``
  enumeration where partial corruption shouldn't bail).  ``strict=True``
  raises :class:`AuditLogParseError` on the first malformed entry (best
  for approve / reject decision guards where silently skipping a corrupted
  decision record could cause a wrong "approval not yet granted" verdict).
- :func:`find_latest_event_for_run` — convenience wrapper that returns the
  most-recent event matching a predicate, or ``None``.  Used by the
  approve / reject decision-guard checks; defaults to ``strict=True`` so
  decision lookups fail fast on log corruption.

Both helpers route OSError on file-open through the module logger and
return an empty result so the caller sees a missing log as "no matching
event" rather than as a crash.

**Performance note (Wave 2a Round-2 review F-INFRA-01):** both helpers are
``O(n)`` over the full audit log per call.  Live-tested at 10K events:
~42 ms per walk on Python 3.11.  Multi-tenant operator dirs that accumulate
audit events for a year may trip into seconds-per-call territory — at that
scale, callers should batch their lookups (do a single ``iter_audit_events``
walk and accumulate all needed events) rather than calling
``find_latest_event_for_run`` repeatedly.

**Deliberate scope (Wave 2a Round-2 review F-XPR-01-01):**
``forgelm/compliance.py::verify_audit_log`` keeps its own JSONL parser
because it must hash the *raw line bytes* for SHA-256 chain verification;
this module yields decoded ``dict`` events without preserving raw bytes,
so the verifier cannot adopt it as-is.  That divergence is intentional and
documented.  If a future refactor extends ``iter_audit_events`` to
optionally yield ``(line_no, raw_line, event)`` tuples, the verifier can
adopt the shared parser.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable, Dict, Iterator, Optional, Tuple

logger = logging.getLogger("forgelm.cli.audit_log_reader")


class AuditLogParseError(ValueError):
    """Raised by :func:`iter_audit_events` in strict mode when a line is malformed.

    Carries ``audit_log_path`` and ``line_number`` so callers / log
    consumers can pinpoint the corrupted record.  Subclass of
    :class:`ValueError` so callers that want broad handling can still use
    ``except ValueError`` without catching unrelated I/O errors.
    """

    def __init__(self, audit_log_path: str, line_number: int, reason: str) -> None:
        self.audit_log_path = audit_log_path
        self.line_number = line_number
        self.reason = reason
        super().__init__(f"{audit_log_path}:{line_number}: {reason}")


def _parse_nonempty_line(
    audit_log_path: str,
    line_number: int,
    line: str,
    *,
    strict: bool,
) -> Optional[Dict[str, Any]]:
    """Parse one non-empty audit-log line.

    Returns the parsed dict, or ``None`` when the line is malformed and
    ``strict=False`` (the caller treats ``None`` as "skip + count").  In
    strict mode raises :class:`AuditLogParseError` on JSON-decode failure
    or non-dict root.

    Wave 2a Round-2 nit: extracted from :func:`iter_audit_events` so the
    iterator stays at one level of nesting (open → for line → maybe-yield)
    and the per-line policy lives in one place.
    """
    try:
        event = json.loads(line)
    except json.JSONDecodeError as exc:
        if strict:
            raise AuditLogParseError(
                audit_log_path,
                line_number,
                f"invalid JSON ({exc.msg})",
            ) from exc
        return None
    if not isinstance(event, dict):
        if strict:
            raise AuditLogParseError(
                audit_log_path,
                line_number,
                f"JSON root is {type(event).__name__}, not dict",
            )
        return None
    return event


def iter_audit_events(
    audit_log_path: str,
    *,
    strict: bool = False,
) -> Iterator[Tuple[int, Dict[str, Any]]]:
    """Yield ``(line_number, event_dict)`` from an append-only JSONL audit log.

    Two modes:

    - ``strict=False`` (default): skips blank lines, malformed JSON,
      non-UTF-8 bytes, and non-dict roots silently.  Emits a single
      ``logger.warning`` at the end summarising the skip count (so
      callers learn about corruption without paying per-line log cost).
      Use this for enumeration paths where partial corruption should not
      bail (e.g. ``forgelm approvals --pending``).
    - ``strict=True``: raises :class:`AuditLogParseError` on the first
      malformed entry (including a non-UTF-8 line).  Use this for paths
      where silently skipping a corrupted record could cause a wrong
      verdict (e.g. approve / reject decision guards: a corrupted
      ``human_approval.granted`` line that gets skipped looks identical
      to "no approval yet" and would double-grant on the operator's next
      attempt).

    Returns nothing (no events yielded, no warning, no raise) when:
    - the file does not exist (a freshly-bootstrapped output dir
      legitimately has no audit log yet); or
    - the file cannot be opened (OSError logged at ERROR level so
      operators see why nothing came through).

    The file is opened in binary mode and each line is decoded
    individually — not opened in text mode with ``encoding="utf-8"`` —
    so that a single non-UTF-8 line (a partial multi-byte sequence from a
    crash mid-write, or bit-flip corruption) is a per-line, recoverable
    failure rather than a fatal ``UnicodeDecodeError`` raised out of the
    generator.  Text-mode iteration cannot offer this: once
    ``TextIOWrapper``'s incremental decoder faults on a chunk, its
    internal buffer position is left past the point where a corrected
    per-line resume is possible, silently losing whatever lines followed
    the bad one.  Binary iteration splits purely on the ``b"\\n"`` byte,
    so a decode failure on one line never disturbs the next.

    The caller is responsible for closing nothing — this generator owns
    the file handle via ``with`` and releases it on iterator exhaustion.
    """
    if not os.path.isfile(audit_log_path):
        return
    try:
        fh = open(audit_log_path, "rb")
    except OSError:
        logger.exception("Cannot open audit log %s", audit_log_path)
        return
    skipped_lines = 0
    with fh:
        for line_number, raw_bytes in enumerate(fh, start=1):
            try:
                raw = raw_bytes.decode("utf-8")
            except UnicodeDecodeError as exc:
                if strict:
                    raise AuditLogParseError(
                        audit_log_path,
                        line_number,
                        f"invalid UTF-8 encoding ({exc})",
                    ) from exc
                skipped_lines += 1
                continue
            line = raw.strip()
            if not line:
                continue
            event = _parse_nonempty_line(audit_log_path, line_number, line, strict=strict)
            if event is None:
                skipped_lines += 1
                continue
            yield line_number, event
    if skipped_lines:
        logger.warning(
            "Skipped %d malformed line(s) while parsing %s.",
            skipped_lines,
            audit_log_path,
        )


def find_latest_event_for_run(
    audit_log_path: str,
    *,
    run_id: str,
    matches: Callable[[Dict[str, Any]], bool],
    strict: bool = True,
) -> Optional[Dict[str, Any]]:
    """Return the most-recent event matching ``matches`` for ``run_id``.

    Walks the entire log (audit logs are append-only so "most recent"
    means the last matching entry encountered).  Returns ``None`` when
    no event matches.  ``O(n)`` over the full log per call — see the
    module docstring's performance note.

    ``matches`` is the predicate the caller cares about — typically
    ``lambda e: e.get("event") == "human_approval.required"`` or a
    membership check against a frozenset of decision-event names.
    Keeping the predicate as a callable lets every caller in the
    approve / approvals / purge family share the parser without
    coupling it to a specific event vocabulary.

    Defaults to ``strict=True`` so the approve / reject decision-guard
    callers fail fast if the log is corrupted; an enumeration caller
    (``--pending``) may pass ``strict=False`` to keep scanning past
    individually-corrupted lines.
    """
    latest: Optional[Dict[str, Any]] = None
    for _line_no, event in iter_audit_events(audit_log_path, strict=strict):
        if event.get("run_id") != run_id:
            continue
        if matches(event):
            latest = event
    return latest


def is_audit_log_readable(audit_log_path: str) -> bool:
    """Return ``True`` iff the audit log exists and is readable by the
    current process.

    Wave 2a Round-5 review (F-R5-01): the approve / reject / approvals
    family all need the same pre-iteration readability gate so a
    chmod-broken audit log does not masquerade as "no events found".
    Centralised here so the policy lives next to the parser it gates.

    The check is a non-binding hint:  ``iter_audit_events`` still has to
    swallow OSError on open because TOCTOU windows + race conditions
    around `chmod` can fire between this call and the open.  Callers
    that want a clear operator-facing error use this helper for the
    common case and trust the parser's logger.error fallback for the
    sub-millisecond race.
    """
    if not os.path.isfile(audit_log_path):
        return False
    return os.access(audit_log_path, os.R_OK)


__all__ = [
    "AuditLogParseError",
    "iter_audit_events",
    "find_latest_event_for_run",
    "is_audit_log_readable",
]
