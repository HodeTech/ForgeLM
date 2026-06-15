"""Versioned API contract for the ``forgelm`` library surface.

Phase 19 (Library API).  Two version strings live here so consumers
can pin against either independently:

- ``__version__`` — the package version (mirrors what
  ``importlib.metadata.version("forgelm")`` returns; sourced from
  pyproject.toml at install time, overridden to ``"0.0.0+dev"`` for
  raw-source checkouts).
- ``__api_version__`` — the *public Python API* contract version, bumped
  when a stable symbol's signature changes (additions are minor, removals
  / signature changes are major).  Consumers that depend on the library
  API can pin against this without coupling to the CLI version.

Per ``docs/design/library_api.md`` §5.1, the two versions track separately because the CLI surface and the
Python API have different stability windows — the CLI may grow new
subcommands without affecting library consumers, and library additions
do not necessarily ship a new CLI feature.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:
    __version__ = _pkg_version("forgelm")
except PackageNotFoundError:  # pragma: no cover — uninstalled-source path
    __version__ = "0.0.0+dev"

# Public Python API contract version.  Bump rules:
#   MAJOR  — removed or signature-changed a stable symbol.
#   MINOR  — added a new stable symbol.
#   PATCH  — implementation change with no API surface impact.
#
# Anchored at 1.0.0 with the v0.5.5 release (first PyPI publish of
# the formal Phase 19 library-API surface).  The public surface in
# ``forgelm.__all__`` is split into stable and experimental tiers — the
# authoritative per-symbol roster lives in ``forgelm._STABILITY_TIERS``
# (mirrored by the Tier column of
# ``docs/reference/library_api_reference.md``); only *stable* symbols are
# covered by the MAJOR-bump rule above.  v0.5.6 reverted the v0.5.5 torch
# min bump;
# v0.5.7 fixes a runtime ``SFTConfig.max_seq_length`` TypeError on
# modern trl (rename to ``max_length`` in trl 0.13+) plus the Intel
# Mac NumPy 2 ABI mismatch (numpy<2 marker + ``numpy.torch_abi``
# doctor probe).  None of these patches change the Python API
# surface, so ``__api_version__`` stays at 1.0.0.
__api_version__ = "1.0.0"

__all__ = ["__version__", "__api_version__"]
