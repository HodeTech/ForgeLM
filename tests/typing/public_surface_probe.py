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

``*cast(Any, ()), **cast(Any, {})`` makes each probe arity-independent — mypy
still resolves the callee and still reports ``no-untyped-call``, but no
argument list has to be maintained here as signatures evolve.

**What this gate does and does not catch**, stated precisely because the first
version of this file over-claimed:

- It catches a **fully unannotated** callable reachable from a probe line —
  every name in ``forgelm.__all__``, and every public method declared in
  ``forgelm/`` on the classes among them.
- It does **not** catch *partial* annotation loss. ``--disallow-untyped-calls``
  fires only when a callee is wholly dynamic, so dropping just the ``-> X`` or
  adding one untyped parameter to an otherwise-typed signature stays green.
  That is mypy's semantics, not an oversight here; closing it would need a
  different instrument (per-symbol ``assert_type`` rows), and the claim is
  narrowed rather than the gap hidden.

Nothing in this module runs: the functions are never called, and ``pytest``
does not collect it (no ``test_`` prefix, and ``tests/typing/`` holds no
``__init__.py``). ``tests/test_library_api.py`` asserts this file covers
``forgelm.__all__`` and every public method, so the roster cannot drift behind
the surface it is supposed to pin.
"""

from __future__ import annotations

from typing import Any, cast

# ``typing.assert_type`` is 3.11+; the project supports 3.10.
from typing_extensions import assert_type

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


def _probe_public_methods() -> None:
    """One call per public method declared in ``forgelm/`` on an exported class.

    Constructing an object is not enough: ``--disallow-untyped-calls`` needs a
    call site for the method itself. Without these lines
    ``ForgeTrainer.train`` — named in this file's own docstring as the
    regression the probe closes — could lose every annotation with the gate
    still reporting success.
    """
    forgelm.AuditLogger(*cast(Any, ()), **cast(Any, {})).log_event(*cast(Any, ()), **cast(Any, {}))
    forgelm.ForgeConfig(*cast(Any, ()), **cast(Any, {})).model_dump(*cast(Any, ()), **cast(Any, {}))
    forgelm.ForgeConfig(*cast(Any, ()), **cast(Any, {})).model_dump_json(*cast(Any, ()), **cast(Any, {}))
    forgelm.ForgeTrainer(*cast(Any, ()), **cast(Any, {})).execute_evaluation_checks(*cast(Any, ()), **cast(Any, {}))
    forgelm.ForgeTrainer(*cast(Any, ()), **cast(Any, {})).save_final_model(*cast(Any, ()), **cast(Any, {}))
    forgelm.ForgeTrainer(*cast(Any, ()), **cast(Any, {})).train(*cast(Any, ()), **cast(Any, {}))
    forgelm.SyntheticDataGenerator(*cast(Any, ()), **cast(Any, {})).generate(*cast(Any, ()), **cast(Any, {}))
    forgelm.VerifyAnnexIVResult(*cast(Any, ()), **cast(Any, {})).to_dict(*cast(Any, ()), **cast(Any, {}))
    forgelm.VerifyGgufResult(*cast(Any, ()), **cast(Any, {})).to_dict(*cast(Any, ()), **cast(Any, {}))
    forgelm.VerifyIntegrityResult(*cast(Any, ()), **cast(Any, {})).to_dict(*cast(Any, ()), **cast(Any, {}))
    forgelm.WebhookNotifier(*cast(Any, ()), **cast(Any, {})).notify_awaiting_approval(*cast(Any, ()), **cast(Any, {}))
    forgelm.WebhookNotifier(*cast(Any, ()), **cast(Any, {})).notify_failure(*cast(Any, ()), **cast(Any, {}))
    forgelm.WebhookNotifier(*cast(Any, ()), **cast(Any, {})).notify_pipeline_completed(*cast(Any, ()), **cast(Any, {}))
    forgelm.WebhookNotifier(*cast(Any, ()), **cast(Any, {})).notify_pipeline_reverted(*cast(Any, ()), **cast(Any, {}))
    forgelm.WebhookNotifier(*cast(Any, ()), **cast(Any, {})).notify_pipeline_started(*cast(Any, ()), **cast(Any, {}))
    forgelm.WebhookNotifier(*cast(Any, ()), **cast(Any, {})).notify_reverted(*cast(Any, ()), **cast(Any, {}))
    forgelm.WebhookNotifier(*cast(Any, ()), **cast(Any, {})).notify_start(*cast(Any, ()), **cast(Any, {}))
    forgelm.WebhookNotifier(*cast(Any, ()), **cast(Any, {})).notify_success(*cast(Any, ()), **cast(Any, {}))


def _probe_public_constants() -> None:
    """Constants must keep a concrete type, not decay to ``Any``.

    ``assert_type`` rather than an annotated assignment: ``Any`` is assignable
    to ``str``, so ``_v: str = forgelm.__version__`` stays green when the
    constant decays, while ``assert_type`` fails on it.
    """
    assert_type(forgelm.__api_version__, str)
    assert_type(forgelm.__version__, str)
