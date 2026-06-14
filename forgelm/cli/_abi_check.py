"""Shared torch / NumPy ABI compatibility probe.

Used in two places with two different contracts:

- :mod:`forgelm.cli.subcommands._doctor` wraps the verdict in a
  ``_CheckResult`` for the ``numpy.torch_abi`` probe (informational
  diagnostic — pass / fail with details).
- :mod:`forgelm.cli._training` calls it as an **early-fail gate** before
  the training pipeline imports torch heavily.  Detecting the mismatch
  here means the operator sees an actionable remediation message
  ("``pip install 'numpy<2'``") instead of a cryptic
  ``NameError: name '_C' is not defined`` mid-training.

The check itself: torch < 2.3 was compiled against NumPy 1.x and silently
degrades when paired with NumPy 2.x (the well-known ``_ARRAY_API not
found`` UserWarning from torch's C++ tensor-numpy bridge).  Intel Mac
(x86_64) is the platform where this routinely bites — PyTorch Foundation
no longer publishes torch >= 2.3 wheels for that target.  v0.5.7 ships a
PEP 508 marker in ``pyproject.toml`` that pins ``numpy<2`` on Intel Mac
for fresh installs; this preflight catches the residual cases (env
manually drifted; user installed torch out-of-band).
"""

from __future__ import annotations

import re
import warnings
from typing import Optional, Tuple

# Status vocabulary.  Centralised so a future rename cannot drift across
# doctor + training callers.
ABI_OK = "compatible"
ABI_BROKEN = "incompatible"
ABI_SKIPPED_TORCH = "skipped_torch_missing"
ABI_SKIPPED_NUMPY = "skipped_numpy_missing"


def _major_minor(version: str) -> Tuple[int, int]:
    """Extract the leading (MAJOR, MINOR) tuple from a version string.

    Tolerates prerelease ("2.2.0a0") and local-version ("2.2.0+cpu")
    suffixes by matching only the leading ``\\d+\\.\\d+`` pair.
    Returns ``(0, 0)`` on a totally unparseable string — callers that
    care about the forensic distinction (the doctor probe) wrap with
    their own debug-logging call.

    Note: a (0, 0) return must be treated as "version unknown, do not
    classify" by callers; the threshold check in
    :func:`compute_numpy_torch_abi_status` short-circuits to
    :data:`ABI_OK` when either side fails to parse to avoid a false-
    positive ``ABI_BROKEN`` on corporate forks with non-semver tags
    (a torch fork tagged ``foo-bar`` would otherwise parse to (0, 0)
    < (2, 3) and unconditionally trip the gate).
    """
    match = re.match(r"^(\d+)\.(\d+)", version)
    if not match:
        return (0, 0)
    return (int(match.group(1)), int(match.group(2)))


def compute_numpy_torch_abi_status() -> Tuple[str, Optional[str], Optional[str]]:
    """Return the ABI verdict + version strings.

    Three-tuple ``(status, torch_version, numpy_version)``:

    - ``status`` is one of :data:`ABI_OK`, :data:`ABI_BROKEN`,
      :data:`ABI_SKIPPED_TORCH`, :data:`ABI_SKIPPED_NUMPY`.
    - ``torch_version`` / ``numpy_version`` are ``None`` when the
      corresponding library is not installed.

    torch + numpy are imported with Python warnings suppressed so this
    probe does not emit a duplicate ``UserWarning``.  Note: the
    underlying ``_ARRAY_API not found`` message ships from torch's C++
    side via ``fprintf(stderr, …)`` and is outside Python's warnings
    machinery — see the doctor probe docstring for the full caveat.
    """
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            import torch
    except ImportError:
        return (ABI_SKIPPED_TORCH, None, None)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            import numpy
    except ImportError:
        return (ABI_SKIPPED_NUMPY, torch.__version__, None)

    torch_mm = _major_minor(torch.__version__)
    numpy_mm = _major_minor(numpy.__version__)
    # Fail-safe: if either version string didn't parse (corporate forks,
    # non-semver tags), don't classify.  The mismatch window
    # ``torch_mm < (2, 3) AND numpy_mm >= (2, 0)`` is only meaningful
    # when both tuples are real; a (0, 0) fallback comparing as
    # "< (2, 3) True" would trip the gate on any unparseable build.
    if torch_mm == (0, 0) or numpy_mm == (0, 0):
        return (ABI_OK, torch.__version__, numpy.__version__)
    # The known incompatibility window: torch < 2.3 was built against
    # NumPy 1.x and silently degrades when paired with NumPy 2.x.
    if torch_mm < (2, 3) and numpy_mm >= (2, 0):
        return (ABI_BROKEN, torch.__version__, numpy.__version__)
    return (ABI_OK, torch.__version__, numpy.__version__)


def format_abi_remediation(torch_version: str, numpy_version: str) -> str:
    """Human-readable remediation hint for an incompatible ABI.

    Both version strings are required to be populated — callers are
    expected to invoke this only after observing :data:`ABI_BROKEN`,
    which guarantees both versions came back non-``None`` from
    :func:`compute_numpy_torch_abi_status`.  A ``ValueError`` guards
    against future call sites that drift from this contract (not an
    ``assert`` — the check must survive ``python -O``).

    Kept identical to the doctor probe's ``detail`` line so the
    operator sees the same fix instructions whether they hit the
    training preflight or ran ``forgelm doctor`` first.
    """
    if torch_version is None or numpy_version is None:
        raise ValueError(
            "format_abi_remediation requires both torch_version and numpy_version; "
            f"got torch_version={torch_version!r}, numpy_version={numpy_version!r}. "
            "This helper is only meaningful after an ABI_BROKEN verdict — check the "
            "call site for a misclassification."
        )
    return (
        f"torch {torch_version} (compiled against NumPy 1.x) is paired with "
        f"numpy {numpy_version}. This triggers an `_ARRAY_API not found` "
        "ABI mismatch and silently degrades the numpy bridge inside torch. "
        "Fix with: pip install 'numpy<2' (or upgrade to torch>=2.3 if your "
        "platform has wheels for it). Run `forgelm doctor` for the full "
        "environment diagnostic."
    )
