"""ForgeLM — config-driven, enterprise-grade LLM fine-tuning toolkit.

This is the package facade.  The CLI surface is exposed via the
``forgelm`` console script (and ``python -m forgelm.cli``); the Python
**library API** that integrators reach via ``from forgelm import ...``
is documented in
``docs/design/library_api.md`` and finalised here in Phase 19.

Lazy-import discipline (Phase 19):

- ``import forgelm`` is *cheap*: no torch, no transformers, no trl, no
  datasets at import time.  Only ``importlib.metadata`` and a tiny
  module-level immutable state mapping are touched.
- Heavy attributes (``ForgeTrainer``, ``audit_dataset``,
  ``setup_authentication``, etc.) are resolved on first attribute
  access via the module-level ``__getattr__`` hook (PEP 562); each
  resolved value is cached in ``globals()`` so subsequent accesses are
  zero-cost.
- ``dir(forgelm)`` lists the full public surface even before any
  attribute has been accessed (so IDE autocomplete + ``help(forgelm)``
  see every name immediately).

Stability tiers (per design §2):

- **Stable** symbols — semver-protected; signature changes require a
  major version bump of ``__api_version__`` (see
  :mod:`forgelm._version`).
- **Experimental** symbols — surface may change without a major bump
  but the symbol is still public.
- **Internal** — anything not in ``__all__`` is internal and may
  change at any time.

The per-symbol tier is recorded once in :data:`_STABILITY_TIERS` below;
that map is the single source of truth the user-facing reference doc and
the ``__api_version__`` MAJOR-bump rule both key off.

PEP 561 type-hint distribution: the ``forgelm/py.typed`` marker file
ships in the wheel so ``mypy --strict`` / ``pyright`` consumers see
the in-source type hints without needing a separate stubs package.
"""

from __future__ import annotations as _annotations

from types import MappingProxyType as _MappingProxyType
from typing import TYPE_CHECKING as _TYPE_CHECKING

from ._version import __api_version__, __version__
from .config import ConfigError, ForgeConfig, load_config

# F-PR29-A3-06: rebind ``from __future__ import annotations`` and the
# ``typing.TYPE_CHECKING`` import as underscore-prefixed names so they
# do NOT appear in ``dir(forgelm)``.  The design doc rule (§2.3) states
# that a name is *internal* iff it is not in ``forgelm.__all__`` AND
# does not appear in ``dir(forgelm)``; without the rename the parser
# pragma binding ``annotations`` and the typing helper ``TYPE_CHECKING``
# both leak as if they were public surface.  The submodule attribute
# ``config`` (attached as a side effect of ``from .config import ...``)
# is filtered out at the ``__dir__`` boundary instead — see ``__dir__``
# below — because submodule attributes are an unavoidable Python
# import-system convention and renaming the eager import would break
# downstream ``forgelm.config`` references.
del _annotations  # parser pragma — runtime binding is unused after rename.

# Public surface (stable + experimental tiers — see ``_STABILITY_TIERS``
# below for the per-symbol tier).  Order matches design §2.1 tier
# listing (Stable first, then Experimental).  Anything absent from this
# list is internal — operators may import it but the package gives no
# stability guarantee.
#
# Pylint cannot statically follow the PEP 562 ``__getattr__`` resolver
# below, so it incorrectly flags every lazy name as ``E0603 Undefined
# variable name 'X' in __all__``.  The TYPE_CHECKING block (~line 159)
# imports each name for mypy / pyright; runtime resolution goes through
# ``_LAZY_SYMBOLS`` (~line 121).  Disable the false positive at the
# module level.
# pylint: disable=undefined-all-variable
__all__ = [
    # Versioning.
    "__version__",
    "__api_version__",
    # Configuration.
    "load_config",
    "ForgeConfig",
    "ConfigError",
    # Training entry point.
    "ForgeTrainer",
    "TrainResult",
    # Data preparation + audit.
    "prepare_dataset",
    "get_model_and_tokenizer",
    "audit_dataset",
    "AuditReport",
    # PII / secrets / dedup utility belt.
    "detect_pii",
    "mask_pii",
    "detect_secrets",
    "mask_secrets",
    "compute_simhash",
    "compute_minhash",
    # Compliance / audit log.
    "AuditLogger",
    "verify_audit_log",
    "VerifyResult",
    # Phase 36 verification toolbelt (library entries).
    "verify_annex_iv_artifact",
    "VerifyAnnexIVResult",
    "verify_gguf",
    "VerifyGgufResult",
    "verify_integrity",
    "VerifyIntegrityResult",
    # Webhook notifier (experimental — surface may change).
    "WebhookNotifier",
    # Auxiliary.
    "setup_authentication",
    "manage_checkpoints",
    "run_benchmark",
    "BenchmarkResult",
    "SyntheticDataGenerator",
]


# Machine-readable stability tier for every public symbol (F-P1-FAB-27).
# This is the single source of truth that the user-facing reference doc
# (``docs/reference/library_api_reference.md``) and the ``__api_version__``
# MAJOR-bump rule both key off — previously the roster was contradicted
# across ``_version.py``, the module docstring, the reference doc and the
# test fixtures.  ``tests/test_library_api.py`` asserts this map equals the
# reference doc's Tier column and covers exactly ``__all__``.
#
# Tier semantics (design §2): ``stable`` symbols are semver-protected and a
# signature change requires an ``__api_version__`` MAJOR bump; ``experimental``
# symbols may change without a MAJOR bump (the surface is still public).
#
# Wrapped in ``MappingProxyType`` (architecture.md §4: "no module-level
# mutable state" — only "immutable registries" are permitted as module-level
# state).  A plain ``dict`` here would let a stray ``forgelm._STABILITY_TIERS
# ["X"] = ...`` (e.g. a test that forgot ``monkeypatch.setattr``, or a
# notebook cell) permanently corrupt the registry for the rest of the
# process, since module-level singletons persist across subsequent ``import
# forgelm`` calls in the same interpreter.  The proxy makes such a mutation
# raise ``TypeError`` immediately at the mutation site instead.
_STABILITY_TIERS: _MappingProxyType[str, str] = _MappingProxyType(
    {
        "__version__": "stable",
        "__api_version__": "stable",
        "load_config": "stable",
        "ForgeConfig": "stable",
        "ConfigError": "stable",
        "ForgeTrainer": "stable",
        "TrainResult": "stable",
        "prepare_dataset": "experimental",
        "get_model_and_tokenizer": "experimental",
        "audit_dataset": "stable",
        "AuditReport": "stable",
        "detect_pii": "stable",
        "mask_pii": "stable",
        "detect_secrets": "stable",
        "mask_secrets": "stable",
        "compute_simhash": "experimental",
        "compute_minhash": "experimental",
        "AuditLogger": "stable",
        "verify_audit_log": "stable",
        "VerifyResult": "stable",
        "verify_annex_iv_artifact": "stable",
        "VerifyAnnexIVResult": "stable",
        "verify_gguf": "stable",
        "VerifyGgufResult": "stable",
        "verify_integrity": "stable",
        "VerifyIntegrityResult": "stable",
        "WebhookNotifier": "experimental",
        "setup_authentication": "experimental",
        "manage_checkpoints": "experimental",
        "run_benchmark": "experimental",
        "BenchmarkResult": "experimental",
        "SyntheticDataGenerator": "experimental",
    }
)


# Submodule path constants — kept here so a future rename (e.g.
# ``forgelm.data_audit`` → ``forgelm.data_audit.api``) is a single-line
# edit instead of a 7-row find-and-replace, and so SonarCloud's S1192
# duplicate-literal check stays green.
_M_DATA_AUDIT = "forgelm.data_audit"
_M_COMPLIANCE = "forgelm.compliance"
# The three artefact verifiers moved out of ``forgelm.cli.subcommands.
# _verify_*`` and into a single library module: resolving a *stable*
# public symbol must not drag the CLI layer into a library consumer's
# import graph, and ``architecture.md`` §5 keeps CLI modules to argument
# parsing + dispatch.  The CLI subcommands now import from here.
_M_VERIFY = "forgelm.verify"
_M_UTILS = "forgelm.utils"
_M_BENCHMARK = "forgelm.benchmark"

# Mapping from public symbol name → ``(submodule_path, attr_name)``.
# Centralised so adding a new lazy export is one row, the
# ``__getattr__`` hook stays a generic dispatcher, and ``__dir__`` can
# enumerate the surface without triggering imports.
#
# Wrapped in ``MappingProxyType`` for the same reason as ``_STABILITY_TIERS``
# above: this table is load-bearing for the public-surface contract (it
# drives ``__getattr__`` resolution), so it must not be mutable module-level
# state per architecture.md §4.
_LAZY_SYMBOLS: _MappingProxyType[str, tuple[str, str]] = _MappingProxyType(
    {
        "ForgeTrainer": ("forgelm.trainer", "ForgeTrainer"),
        "TrainResult": ("forgelm.results", "TrainResult"),
        "prepare_dataset": ("forgelm.data", "prepare_dataset"),
        "get_model_and_tokenizer": ("forgelm.model", "get_model_and_tokenizer"),
        "audit_dataset": (_M_DATA_AUDIT, "audit_dataset"),
        "AuditReport": (_M_DATA_AUDIT, "AuditReport"),
        "detect_pii": (_M_DATA_AUDIT, "detect_pii"),
        "mask_pii": (_M_DATA_AUDIT, "mask_pii"),
        "detect_secrets": (_M_DATA_AUDIT, "detect_secrets"),
        "mask_secrets": (_M_DATA_AUDIT, "mask_secrets"),
        "compute_simhash": (_M_DATA_AUDIT, "compute_simhash"),
        "compute_minhash": (_M_DATA_AUDIT, "compute_minhash"),
        "AuditLogger": (_M_COMPLIANCE, "AuditLogger"),
        "verify_audit_log": (_M_COMPLIANCE, "verify_audit_log"),
        "VerifyResult": (_M_COMPLIANCE, "VerifyResult"),
        "verify_annex_iv_artifact": (_M_VERIFY, "verify_annex_iv_artifact"),
        "VerifyAnnexIVResult": (_M_VERIFY, "VerifyAnnexIVResult"),
        "verify_gguf": (_M_VERIFY, "verify_gguf"),
        "VerifyGgufResult": (_M_VERIFY, "VerifyGgufResult"),
        "verify_integrity": (_M_VERIFY, "verify_integrity"),
        "VerifyIntegrityResult": (_M_VERIFY, "VerifyIntegrityResult"),
        "WebhookNotifier": ("forgelm.webhook", "WebhookNotifier"),
        "setup_authentication": (_M_UTILS, "setup_authentication"),
        "manage_checkpoints": (_M_UTILS, "manage_checkpoints"),
        "run_benchmark": (_M_BENCHMARK, "run_benchmark"),
        "BenchmarkResult": (_M_BENCHMARK, "BenchmarkResult"),
        "SyntheticDataGenerator": ("forgelm.synthetic", "SyntheticDataGenerator"),
    }
)


# ``TYPE_CHECKING`` is False at runtime so this block never executes;
# but type checkers (mypy, pyright) read it to understand the public
# surface without losing the lazy-import semantics.  Without these
# imports, ``mypy --strict`` on a downstream consumer's
# ``from forgelm import ForgeTrainer`` would raise "Module has no
# attribute ForgeTrainer" because the attribute is only synthesised at
# runtime via ``__getattr__``.
if _TYPE_CHECKING:  # pragma: no cover — type-only imports
    from .benchmark import BenchmarkResult, run_benchmark  # noqa: F401
    from .compliance import AuditLogger, VerifyResult, verify_audit_log  # noqa: F401
    from .data import prepare_dataset  # noqa: F401
    from .data_audit import (  # noqa: F401
        AuditReport,
        audit_dataset,
        compute_minhash,
        compute_simhash,
        detect_pii,
        detect_secrets,
        mask_pii,
        mask_secrets,
    )
    from .model import get_model_and_tokenizer  # noqa: F401
    from .results import TrainResult  # noqa: F401
    from .synthetic import SyntheticDataGenerator  # noqa: F401
    from .trainer import ForgeTrainer  # noqa: F401
    from .utils import manage_checkpoints, setup_authentication  # noqa: F401
    from .verify import (  # noqa: F401
        VerifyAnnexIVResult,
        VerifyGgufResult,
        VerifyIntegrityResult,
        verify_annex_iv_artifact,
        verify_gguf,
        verify_integrity,
    )
    from .webhook import WebhookNotifier  # noqa: F401


def __getattr__(name: str):
    """PEP 562 lazy attribute resolver for the public surface.

    Looks ``name`` up in :data:`_LAZY_SYMBOLS`, imports the source
    submodule, fetches the attribute, and caches the result back into
    the module's ``globals()`` so subsequent accesses skip this hook
    entirely (zero-cost after first touch).  Anything not in the
    lazy-symbols table raises :class:`AttributeError` so typos surface
    as ``AttributeError: module 'forgelm' has no attribute 'XYZ'``
    instead of a confusing ``ImportError`` deep in the resolver.
    """
    target = _LAZY_SYMBOLS.get(name)
    if target is None:
        raise AttributeError(f"module 'forgelm' has no attribute {name!r}")
    module_path, attr_name = target
    import importlib

    module = importlib.import_module(module_path)
    value = getattr(module, attr_name)
    # Cache the resolved value so the hook never fires again for this
    # name.  globals() write is intentional — it's the documented PEP
    # 562 mechanism for one-shot lazy resolution.
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Surface the full public API in ``dir(forgelm)`` even before any
    attribute has been accessed.  Important for IDE autocomplete and
    ``help(forgelm)`` discovery.

    F-19-02: filter out single-underscore implementation details
    (``_LAZY_SYMBOLS``, ``_M_DATA_AUDIT``, …) so the listing reflects
    only the public surface.  Dunders (``__version__``,
    ``__api_version__``) are explicitly in ``__all__`` and survive the
    filter.

    F-PR29-A3-06: Python's import system attaches imported submodules
    as attributes of the parent package (so ``from .config import X``
    silently injects ``forgelm.config`` into this module's globals).
    To honour the design-doc rule that every public name in
    ``dir(forgelm)`` must appear in ``__all__``, we intersect the
    globals listing with ``__all__`` rather than unioning — submodule
    attributes are still reachable via ``forgelm.config`` (Python's
    attribute-lookup falls through to ``globals()``), they just stop
    advertising themselves as if they were Stable surface.  Lazy
    symbols listed in ``_LAZY_SYMBOLS`` but not yet resolved still
    appear because they're members of ``__all__``.
    """
    # ``__all__`` is the single source of truth for the advertised surface
    # (dunders such as ``__version__`` / ``__api_version__`` are already
    # members), so the listing is exactly its sorted, de-duplicated contents.
    return sorted(set(__all__))
