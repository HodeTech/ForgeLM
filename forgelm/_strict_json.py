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
    if isinstance(value, bool):
        # ``bool`` is an ``int`` subclass and ``int`` is not handled below, but
        # say so explicitly: a future widening of the numeric branch that
        # forgets this turns ``True`` into ``"1"`` in every artefact.
        return value
    if isinstance(value, (int, float)) or _is_numpy_scalar(value):
        # ``float(value)`` first, then ``math.isfinite``. Both conversions
        # matter and for different reasons.
        #
        # lm-eval returns ``numpy.float64`` and ``numpy.float32``. The first
        # *is* a ``float`` subclass, so a bare ``isinstance(value, float)``
        # caught it — but ``repr()`` on it yields ``"np.float64(nan)"`` under
        # NumPy 2, so a compliance artefact would carry a NumPy-version-
        # dependent string instead of the measurement. The second is **not** a
        # ``float`` subclass at all and fell through entirely, landing on
        # whatever ``default=`` the caller passed — correct only by accident,
        # and a ``TypeError`` rather than the intended ``ValueError`` when no
        # default was given.
        #
        # Normalising through ``float()`` gives one representation for every
        # numeric type: ``"nan"``, ``"inf"``, ``"-inf"``.
        try:
            as_float = float(value)
        except (TypeError, ValueError, OverflowError):
            return value
        if not math.isfinite(as_float):
            return repr(as_float)
        return _native_number(value)
    return value


def _native_number(value: Any) -> Any:
    """A finite number as the Python type ``json`` can write, without widening it.

    The non-finite half of the NumPy problem is not the whole of it: a finite
    ``numpy.float32`` / ``int64`` / ``bool_`` is not JSON serialisable either, so
    it raised ``TypeError`` from a writer with no ``default=`` and was silently
    turned into a *string* by one with ``default=str``. Both break the rule this
    module states — one representation per numeric type, a number stays a number.

    A ``float32`` goes through its shortest round-trip decimal (``"0.85"``), not
    ``float()``, which would publish its binary expansion (``0.8500000238418579``).
    """
    if isinstance(value, (int, float)):
        return value  # Python numbers, and ``numpy.float64`` (a ``float`` subclass), json already writes
    kind = getattr(getattr(value, "dtype", None), "kind", "")
    if kind == "b":
        return bool(value)
    if kind in ("i", "u"):
        return int(value)
    if kind == "f":
        return float(str(value))
    return value


def _is_numpy_scalar(value: Any) -> bool:
    """True for a NumPy scalar, without importing NumPy.

    NumPy is a transitive dependency, not a declared one, and this module is
    imported by the audit logger — which must not acquire a hard dependency on
    the scientific stack to write a log line. The duck-type check is on the
    attributes every ``numpy.generic`` carries.
    """
    return hasattr(value, "dtype") and hasattr(value, "item") and not isinstance(value, (str, bytes))


def dumps_strict(payload: Any, *, default: Optional[Callable[[Any], Any]] = None, **kwargs: Any) -> str:
    """``json.dumps`` that cannot emit a non-finite token.

    Sanitises first, then serialises with ``allow_nan=False`` so anything the
    walk missed raises ``ValueError`` here rather than shipping an artefact no
    strict parser will read.
    """
    return json.dumps(sanitize_non_finite(payload), allow_nan=False, default=default, **kwargs)
