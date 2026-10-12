"""Resolve ORPO/SimPO classes across TRL's stable and experimental exports."""

import logging
from importlib import import_module
from typing import Any

from .config import ConfigError

logger = logging.getLogger("forgelm._trl_compat")

_EXPERIMENTAL_MODULES = {
    "ORPOConfig": "trl.experimental.orpo",
    "ORPOTrainer": "trl.experimental.orpo",
    "CPOConfig": "trl.experimental.cpo",
    "CPOTrainer": "trl.experimental.cpo",
}


def _resolve_preference_symbol(name: str) -> Any:
    """Prefer top-level TRL, then its experimental ORPO/CPO exports.

    Only an absent export or module permits fallback. Dependency-load errors
    propagate unchanged, and TRL's experimental warning remains visible.

    Raises:
        ConfigError: Neither supported location exports the requested class.
    """
    experimental = _EXPERIMENTAL_MODULES[name]
    trl = import_module("trl")
    version = getattr(trl, "__version__", "unknown")
    for module_name in ("trl", experimental):
        try:
            module = import_module(module_name)
        except ModuleNotFoundError as exc:
            # An absent parent/package is different from a missing dependency
            # imported by that package; preserve the latter's original error.
            if exc.name is None or not (module_name == exc.name or module_name.startswith(exc.name + ".")):
                raise
            continue
        try:
            symbol = getattr(module, name)
        except AttributeError as exc:
            # Dynamic exports need not appear in __dir__. Only an unlisted
            # missing attribute on this module permits fallback; a declared
            # lazy export or a transitive attribute-load failure must surface.
            if name in dir(module) or exc.name != name or exc.obj is not module:
                raise
            continue
        if module_name == experimental:
            logger.info(
                "Using %s.%s from TRL %s; this upstream experimental API may change or be removed without notice.",
                module_name,
                name,
                version,
            )
        return symbol
    raise ConfigError(
        f"TRL {version} exports neither trl.{name} nor {experimental}.{name}. "
        "Install a compatible TRL release within ForgeLM's declared range (trl>=1.0.0,<2.0.0) "
        "that provides this class, or select another training.trainer_type."
    )
