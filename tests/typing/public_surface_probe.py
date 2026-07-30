"""Type-level probe for ForgeLM's public surface. **Never executed.**

This file exists to give ``mypy --strict`` something to check. The gate it
backs — ``mypy --strict --follow-imports=silent forgelm/__init__.py
forgelm/_version.py`` — type-checks exactly two functions, ``__getattr__`` and
``__dir__``, neither of which is in ``__all__``. ``--follow-imports=silent``
discards every diagnostic raised *inside* the defining modules, and
``forgelm/__init__.py`` never calls anything, so stripping the annotations off
``load_config``, ``verify_integrity`` or ``ForgeTrainer.train`` left the gate
at ``Success: no issues found`` while the documented "100% typed stable
surface" claim silently became false.

The mechanism: ``--strict`` implies ``--disallow-untyped-calls``, which fires
at the **call site** when the callee has no annotations. Call sites live here,
in a file passed on the command line and therefore not "followed", so the
diagnostic is reported rather than silenced.

``*cast(Any, ())`` makes each probe arity-independent — mypy still resolves the
callee and still reports ``no-untyped-call``, but no argument list has to be
maintained here as signatures evolve. Nothing in this module runs: the
functions are never called, and ``pytest`` does not collect it (the filename
carries no ``test_`` prefix and ``tests/typing/`` holds no ``__init__.py``).

Regenerate the symbol list when ``forgelm.__all__`` changes;
``tests/test_library_api.py`` asserts this file covers it.
"""

from __future__ import annotations

from typing import Any, cast

import forgelm


def _probe_public_callables() -> None:
    """One call per public callable, from an annotated context."""
    forgelm.AuditLogger(*cast(Any, ()), **cast(Any, {}))
    forgelm.AuditReport(*cast(Any, ()), **cast(Any, {}))
    forgelm.BenchmarkResult(*cast(Any, ()), **cast(Any, {}))
    forgelm.ConfigError(*cast(Any, ()), **cast(Any, {}))
    forgelm.ForgeConfig(*cast(Any, ()), **cast(Any, {}))
    forgelm.ForgeTrainer(*cast(Any, ()), **cast(Any, {}))
    forgelm.SyntheticDataGenerator(*cast(Any, ()), **cast(Any, {}))
    forgelm.TrainResult(*cast(Any, ()), **cast(Any, {}))
    forgelm.VerifyAnnexIVResult(*cast(Any, ()), **cast(Any, {}))
    forgelm.VerifyGgufResult(*cast(Any, ()), **cast(Any, {}))
    forgelm.VerifyIntegrityResult(*cast(Any, ()), **cast(Any, {}))
    forgelm.VerifyResult(*cast(Any, ()), **cast(Any, {}))
    forgelm.WebhookNotifier(*cast(Any, ()), **cast(Any, {}))
    forgelm.audit_dataset(*cast(Any, ()), **cast(Any, {}))
    forgelm.compute_minhash(*cast(Any, ()), **cast(Any, {}))
    forgelm.compute_simhash(*cast(Any, ()), **cast(Any, {}))
    forgelm.detect_pii(*cast(Any, ()), **cast(Any, {}))
    forgelm.detect_secrets(*cast(Any, ()), **cast(Any, {}))
    forgelm.get_model_and_tokenizer(*cast(Any, ()), **cast(Any, {}))
    forgelm.load_config(*cast(Any, ()), **cast(Any, {}))
    forgelm.manage_checkpoints(*cast(Any, ()), **cast(Any, {}))
    forgelm.mask_pii(*cast(Any, ()), **cast(Any, {}))
    forgelm.mask_secrets(*cast(Any, ()), **cast(Any, {}))
    forgelm.prepare_dataset(*cast(Any, ()), **cast(Any, {}))
    forgelm.run_benchmark(*cast(Any, ()), **cast(Any, {}))
    forgelm.setup_authentication(*cast(Any, ()), **cast(Any, {}))
    forgelm.verify_annex_iv_artifact(*cast(Any, ()), **cast(Any, {}))
    forgelm.verify_audit_log(*cast(Any, ()), **cast(Any, {}))
    forgelm.verify_gguf(*cast(Any, ()), **cast(Any, {}))
    forgelm.verify_integrity(*cast(Any, ()), **cast(Any, {}))


def _probe_public_constants() -> None:
    """Constants must keep a concrete type, not decay to ``Any``."""
    _api_version: str = forgelm.__api_version__
    del _api_version
    _version: str = forgelm.__version__
    del _version
