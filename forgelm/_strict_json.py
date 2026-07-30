"""Strict JSON serialisation for every artefact ForgeLM publishes.

CPython's ``json`` module is permissive at **both** ends, and the two
permissions compound into a defect that testing does not catch:

- ``json.dumps`` defaults to ``allow_nan=True``, emitting the bare tokens
  ``NaN``, ``Infinity`` and ``-Infinity``. None of them is JSON — RFC 8259
  has no non-finite number literal — so the output is rejected by ``jq``,
  Go's ``encoding/json``, Rust's ``serde_json``, ``JSON.parse`` and every
  strict parser an operator's pipeline is likely to use.
- ``json.loads`` accepts those same tokens. So a test that writes an
  artefact and reads it back with ``json.loads`` passes, which is why this
  survived: the round trip is not an oracle for the contract the artefact
  actually publishes.

Where that mattered: the append-only audit log is an EU AI Act Art. 12
artefact, and one non-finite metric anywhere in an event payload made the
line unparseable for an auditor's tooling while ForgeLM reported success.
The training result envelope on stdout is what CI/CD branches on. The
webhook body goes to a third-party endpoint that will simply reject it.

The policy here is **sanitise, then verify**:

1. :func:`sanitize_non_finite` walks the structure and replaces every
   non-finite float with its ``repr`` (``"nan"``, ``"inf"``, ``"-inf"``).
   A string is honest — it preserves the measurement for a human reading
   the artefact, and it cannot be mistaken for a number by a consumer
   computing on the field. Dropping the key would erase evidence; writing
   ``null`` would be indistinguishable from "not measured".
2. :func:`dumps_strict` then serialises with ``allow_nan=False``, which
   raises ``ValueError`` on anything the sanitiser missed. That second
   pass is the tripwire: it is what stops a future code path introducing a
   new non-finite field that nobody notices, rather than trusting the walk
   to have been exhaustive forever.
"""

from __future__ import annotations

import json
import math
from typing import Any, Callable, Optional

__all__ = ["dumps_strict", "sanitize_non_finite"]


def sanitize_non_finite(value: Any) -> Any:
    """Return *value* with every non-finite float replaced by its ``repr``.

    Recurses through dicts, lists and tuples. Leaves every other type
    untouched — including ``bool``, which is an ``int`` subclass and must not
    be treated as a number here.
    """
    if isinstance(value, dict):
        return {key: sanitize_non_finite(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_non_finite(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return repr(value)
    return value


def dumps_strict(payload: Any, *, default: Optional[Callable[[Any], Any]] = None, **kwargs: Any) -> str:
    """``json.dumps`` that cannot emit a non-finite token.

    Sanitises first, then serialises with ``allow_nan=False`` so anything the
    walk missed raises ``ValueError`` here rather than shipping an artefact no
    strict parser will read.
    """
    return json.dumps(sanitize_non_finite(payload), allow_nan=False, default=default, **kwargs)
