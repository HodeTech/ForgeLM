"""Bounded, fail-closed JSON reading for the verifiers.

A verifier that its own input can kill is not a verifier. Every read here
enforces a byte cap on the **same descriptor** it parses, so the bytes measured
are the bytes read.

Extracted out of :mod:`._pipeline_evidence` in Phase 16 S1 so the sibling
verifiers can reach it. Two reads in this package are still uncapped —
:func:`._annex_iv.verify_annex_iv_artifact` and
:func:`._model_integrity.verify_integrity` — because capping them changes a
verdict, which S1 is contractually not allowed to do. Both are recorded
against **S5** in the Phase 16 deferral cohort
(``docs/roadmap/risks-and-decisions.md``); the extraction is what makes that
fix a two-line import at each site rather than a re-derivation, and what stops
a third copy of the same logic appearing next time.

This module holds no verdict logic and no policy: only the primitive and the
two exceptions that let a caller tell "empty" from "too large" from
"malformed".
"""

from __future__ import annotations

import json
import os
from typing import Any


# ("Expecting value: line 1 column 1").  It gets its own exception because
# "the evidence file is empty" is a materially different — and far more
# legible — finding than "the JSON is malformed".
class _EmptyFileError(Exception):
    """Raised by :func:`_read_capped_json` for a zero-byte file."""


class _OversizeError(Exception):
    """Raised past the byte cap; carries the size so callers can name it."""

    def __init__(self, size: int) -> None:
        super().__init__(f"{size} bytes")
        self.size = size


# The size check MUST come from ``os.fstat`` on the open descriptor, not from
# a separate ``os.path.getsize`` — the rule
# ``compliance.compute_dataset_fingerprint`` already follows.  Under
# stat-then-open the file that was measured and the file that is read are two
# different observations: a payload can be small at the stat and arbitrarily
# large at the read, so the cap that exists to stop the verifier being killed
# by its own input is bypassed outright.  Stating the open descriptor closes
# that window — the bytes measured are the bytes read.
#
# The bounded ``read(cap + 1)`` is a second, independent guard: it rejects an
# over-cap payload even when fstat under-reports (a growing file, or a path
# where st_size is not authoritative).  The fstat is the correctness fix; the
# bounded read is what makes the cap true regardless of what the descriptor
# claims about itself.
#
# Binary mode so that read counts *bytes*.  In text mode ``read(n)`` counts
# decoded characters, so a multibyte UTF-8 payload could satisfy a character
# budget while carrying several times the byte cap.  The explicit decode
# leaves behaviour unchanged: invalid UTF-8 still raises UnicodeDecodeError,
# which every caller already routes.
def _read_capped_json(path: str, cap: int) -> Any:
    """Open *path* once, enforce *cap* on that same handle, then parse.

    Raises :class:`_OversizeError` past the cap and :class:`_EmptyFileError`
    on a zero-byte file; otherwise propagates what ``json.load`` would.
    """
    with open(path, "rb") as fh:
        size = os.fstat(fh.fileno()).st_size
        if size > cap:
            raise _OversizeError(size)
        raw = fh.read(cap + 1)
        if len(raw) > cap:
            raise _OversizeError(len(raw))
    if not raw:
        raise _EmptyFileError()
    return json.loads(raw.decode("utf-8"))
