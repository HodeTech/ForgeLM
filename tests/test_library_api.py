"""Phase 19 — Library API integration tests.

Verifies the public Python surface that
``docs/design/library_api.md`` pins:

- Stable symbol set matches ``forgelm.__all__``.
- Lazy-import discipline holds — ``import forgelm`` does NOT pull
  ``torch`` / ``transformers`` / ``trl``.
- Attribute access through the ``__getattr__`` hook returns the
  expected source object and caches it for subsequent accesses.
- ``dir(forgelm)`` lists the full public surface (IDE autocomplete +
  ``help(forgelm)`` discovery).
- ``forgelm/py.typed`` PEP 561 marker is shipped with the package.
- ``__api_version__`` follows the contract in ``forgelm/_version.py``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Stable surface
# ---------------------------------------------------------------------------


_EXPECTED_STABLE_SYMBOLS = {
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
    # Phase 36 verification toolbelt.
    "verify_annex_iv_artifact",
    "VerifyAnnexIVResult",
    "verify_gguf",
    "VerifyGgufResult",
    "verify_integrity",
    "VerifyIntegrityResult",
    # Webhook notifier.
    "WebhookNotifier",
    # Auxiliary.
    "setup_authentication",
    "manage_checkpoints",
    "run_benchmark",
    "BenchmarkResult",
    "SyntheticDataGenerator",
}


class TestPublicSurface:
    def test_all_exposes_every_documented_symbol(self) -> None:
        import forgelm

        actual = set(forgelm.__all__)
        missing = _EXPECTED_STABLE_SYMBOLS - actual
        extra = actual - _EXPECTED_STABLE_SYMBOLS
        assert not missing, f"forgelm.__all__ is missing documented symbols: {sorted(missing)}"
        assert not extra, f"forgelm.__all__ has undocumented additions: {sorted(extra)}"

    def test_dir_lists_full_surface_before_any_attribute_access(self) -> None:
        """dir(forgelm) MUST list every public name even before any
        lazy attribute has been accessed (IDE autocomplete + help())."""
        # We intentionally do NOT delete forgelm from sys.modules here:
        # other tests in the suite rely on submodule attributes (e.g.
        # `forgelm.model`) being populated by their `import forgelm.X`
        # statements.  The contract this test pins — "every name in
        # __all__ is in dir(forgelm)" — holds regardless of which lazy
        # symbols have been resolved already, because __dir__ reads
        # from __all__ + globals().
        import forgelm

        listing = dir(forgelm)
        for name in _EXPECTED_STABLE_SYMBOLS:
            assert name in listing, f"dir(forgelm) is missing public symbol {name!r}"

    def test_dir_does_not_leak_private_module_constants(self) -> None:
        """F-19-02: ``dir(forgelm)`` must not surface single-underscore
        implementation details (``_LAZY_SYMBOLS``, ``_M_DATA_AUDIT``, …)
        as if they were public API.  Convention: `dir()` lists the
        public surface; dunders (``__version__`` etc.) are explicitly in
        ``__all__`` and survive the filter."""
        import forgelm

        leaked = [n for n in dir(forgelm) if n.startswith("_") and not n.startswith("__")]
        assert leaked == [], f"dir(forgelm) leaks single-underscore private names: {leaked}"

    def test_dir_leaks_no_internal_names_outside_all(self) -> None:
        """F-PR29-A3-06: every public name in ``dir(forgelm)`` must
        appear in ``forgelm.__all__``.  Catches regressions like the
        original
        ``from typing import TYPE_CHECKING`` /
        ``from __future__ import annotations`` /
        ``from .config import ...`` triple, which leaked
        ``TYPE_CHECKING``, ``annotations``, and ``config`` as if they
        were public surface despite being implementation details / a
        submodule attribute."""
        import forgelm

        leaked = {n for n in dir(forgelm) if not n.startswith("_")} - set(forgelm.__all__)
        assert not leaked, f"Names exposed without being in __all__: {sorted(leaked)}"

    def test_design_doc_experimental_symbols_are_exported(self) -> None:
        """F-PR29-A3-05: symbols listed Experimental in
        ``docs/design/library_api.md``
        MUST be importable from the top-level ``forgelm`` package.
        Otherwise consumers who follow the design doc and write
        ``from forgelm import compute_minhash`` get an
        ``AttributeError`` deep in the lazy resolver.  Hardcoded list
        because parsing the design doc would couple the test to that
        document's prose structure."""
        import forgelm

        experimental_symbols = ["compute_minhash"]  # extend as the design doc adds more.
        for sym in experimental_symbols:
            assert hasattr(forgelm, sym), (
                f"{sym!r} is listed Experimental in the library-api design doc but is "
                "missing from the top-level forgelm namespace."
            )

    def test_api_version_bump_rule_block_survives(self) -> None:
        """F-19-01 corollary: pin the canonical bump-rule block in
        ``forgelm/_version.py`` so a refactor cannot silently delete the
        rule the ``cut-release`` skill references."""
        import inspect

        from forgelm import _version

        src = inspect.getsource(_version)
        assert "MAJOR" in src and "MINOR" in src and "PATCH" in src, (
            "forgelm/_version.py must keep the MAJOR/MINOR/PATCH bump-rule block "
            "(referenced from .claude/skills/cut-release/SKILL.md and docs/standards/release.md)."
        )

    def test_py_typed_marker_present(self) -> None:
        """PEP 561: forgelm/py.typed must ship in the wheel + source."""
        import forgelm

        marker = Path(forgelm.__file__).parent / "py.typed"
        assert marker.is_file(), f"forgelm/py.typed marker is missing at {marker}"

    def test_api_version_is_semver_string(self) -> None:
        from forgelm import __api_version__

        parts = __api_version__.split(".")
        assert len(parts) == 3, f"__api_version__ should be MAJOR.MINOR.PATCH, got {__api_version__!r}"
        for p in parts:
            assert p.isdigit(), f"__api_version__ part {p!r} is not numeric (got {__api_version__!r})"

    def test_attribute_typo_raises_attribute_error_not_import_error(self) -> None:
        """A typo on a public attribute must surface as AttributeError
        (not ImportError) so consumers get a clean error message."""
        import forgelm

        with pytest.raises(AttributeError, match="forgelm"):
            _ = forgelm.ForeTrainer  # typo


# ---------------------------------------------------------------------------
# Lazy-import discipline
# ---------------------------------------------------------------------------


class TestLazyImportDiscipline:
    def test_import_forgelm_does_not_pull_torch(self) -> None:
        """`import forgelm` cold MUST NOT pull torch / transformers /
        trl into sys.modules.  Operators running `forgelm doctor` on a
        machine without torch installed would otherwise crash before
        the doctor probe runs.
        """
        # Use a subprocess so a previously-imported torch in this test
        # process doesn't pollute the assertion.
        script = (
            "import sys; "
            "import forgelm; "
            "loaded = sorted(m for m in sys.modules if m in {'torch', 'transformers', 'trl', 'datasets', 'peft'}); "
            "print(','.join(loaded))"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, f"subprocess crashed: {result.stderr}"
        loaded = result.stdout.strip()
        assert loaded == "", (
            "import forgelm pulled heavy deps it should not.  "
            f"sys.modules contains: {loaded!r}.  Lazy-import contract broken."
        )

    def test_attribute_reference_does_not_pull_torch(self) -> None:
        """Even *referencing* `forgelm.ForgeTrainer` (without
        instantiating it) must not pull torch — the reference resolves
        to the class object via __getattr__ + lazy import of
        forgelm.trainer, but trainer.py defers torch imports to method
        bodies (per the existing tests/test_lazy_imports.py contract).
        """
        script = "import sys; import forgelm; _ = forgelm.ForgeTrainer; loaded = 'torch' in sys.modules; print(loaded)"
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, f"subprocess crashed: {result.stderr}"
        # stdout is "True" or "False"; we want False.
        assert result.stdout.strip() == "False", (
            "Referencing forgelm.ForgeTrainer pulled torch.  Lazy-import contract broken."
        )

    def test_import_forgelm_does_not_pull_other_forgelm_submodules(self) -> None:
        """F-19-T-01: the broader lazy-import contract is that ``import
        forgelm`` resolves only the eager pair (``_version`` + ``config``)
        and the facade itself; it must NOT pull heavy submodules like
        ``forgelm.trainer`` / ``forgelm.compliance`` / ``forgelm.model``
        / ``forgelm.data_audit`` / ``forgelm.synthetic``.  A regression
        that adds a top-level ``from .trainer import ForgeTrainer`` to
        ``__init__.py`` would still pass the torch-only test (because
        trainer.py defers torch) but breaks the lazy-import promise
        for the heavy submodules themselves."""
        forbidden = (
            "forgelm.trainer",
            "forgelm.compliance",
            "forgelm.model",
            "forgelm.data",
            "forgelm.data_audit",
            "forgelm.synthetic",
            "forgelm.benchmark",
            "forgelm.results",
            "forgelm.webhook",
        )
        script = (
            "import sys; "
            "import forgelm; "
            "loaded = sorted(m for m in sys.modules if m in {"
            + ", ".join(repr(name) for name in forbidden)
            + "}); print(','.join(loaded))"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, f"subprocess crashed: {result.stderr}"
        assert result.stdout.strip() == "", (
            "import forgelm pulled heavy submodules it should not.  "
            f"sys.modules contains: {result.stdout.strip()!r}.  "
            "Lazy-import contract for forgelm.* submodules broken."
        )

    def test_dir_does_not_trigger_imports(self) -> None:
        """F-19-T-02: ``dir(forgelm)`` must enumerate via
        ``__all__ + globals()`` without lazily resolving any submodule.
        A regression that re-implemented ``__dir__`` to walk
        ``_LAZY_SYMBOLS`` and resolve each entry would silently make
        ``dir(forgelm)`` an O(n) submodule-import, defeating the
        IDE-cheap-discovery contract."""
        script = (
            "import sys; "
            "import forgelm; "
            "before = {m for m in sys.modules if m.startswith('forgelm.')}; "
            "_ = dir(forgelm); "
            "after = {m for m in sys.modules if m.startswith('forgelm.')}; "
            "delta = sorted(after - before); "
            "print(','.join(delta))"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, f"subprocess crashed: {result.stderr}"
        assert result.stdout.strip() == "", (
            f"dir(forgelm) triggered submodule imports: {result.stdout.strip()!r}.  "
            "__dir__ must read from __all__ + globals() only."
        )


# ---------------------------------------------------------------------------
# Lazy resolution + caching semantics
# ---------------------------------------------------------------------------


class TestLazyResolutionSemantics:
    def test_first_access_resolves_via_getattr(self) -> None:
        """The first access to a lazy symbol routes through
        ``__getattr__`` and returns the underlying object (not a stub)."""
        import forgelm

        # Use a torch-free symbol so the test doesn't drag in heavy deps.
        # `audit_dataset` is in `forgelm.data_audit` and is a real callable.
        result = forgelm.audit_dataset
        assert callable(result)

    def test_second_access_hits_globals_cache(self) -> None:
        """After the first access, the value is cached in module
        ``globals()`` so the ``__getattr__`` hook does not fire again.
        We can't easily prove the hook didn't fire, but we can prove
        the value is in globals() post-access (the documented PEP 562
        cache mechanism)."""
        import forgelm

        _ = forgelm.AuditLogger
        # Now `AuditLogger` should be present in the module's globals.
        assert "AuditLogger" in vars(forgelm)
        # And subsequent access returns the same object.
        assert forgelm.AuditLogger is vars(forgelm)["AuditLogger"]

    def test_compute_minhash_lazy_loads(self) -> None:
        """F-PR29-A3-05: ``forgelm.compute_minhash`` must resolve via
        the lazy-load infrastructure (``_LAZY_SYMBOLS`` →
        ``forgelm.data_audit.compute_minhash``) rather than raise
        ``AttributeError``.  Pinned separately from the
        Experimental-tier coverage above so a regression that drops
        ``compute_minhash`` from ``_LAZY_SYMBOLS`` while keeping it in
        ``__all__`` still trips this test."""
        import forgelm

        fn = forgelm.compute_minhash
        assert callable(fn), f"forgelm.compute_minhash is not callable: {fn!r}"


# ---------------------------------------------------------------------------
# End-to-end library entry points (torch-free where possible)
# ---------------------------------------------------------------------------


class TestLibraryEntryPoints:
    def test_load_config_round_trip(self, tmp_path: Path) -> None:
        from forgelm import ForgeConfig, load_config

        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            """
model:
  name_or_path: gpt2
  backend: transformers
lora:
  r: 8
training:
  trainer_type: sft
  output_dir: ./out
  num_train_epochs: 1
data:
  dataset_name_or_path: train.jsonl
"""
        )
        cfg = load_config(str(config_path))
        assert isinstance(cfg, ForgeConfig)
        assert cfg.model.name_or_path == "gpt2"

    def test_audit_logger_roundtrip(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("FORGELM_OPERATOR", "library-test@host")

        from forgelm import AuditLogger, verify_audit_log

        AuditLogger(str(tmp_path)).log_event("library.smoke_test", note="hello")
        result = verify_audit_log(str(tmp_path / "audit_log.jsonl"))
        assert result.valid is True

    def test_verify_annex_iv_library_function(self, tmp_path: Path) -> None:
        import json

        from forgelm import verify_annex_iv_artifact

        artifact = {
            "system_identification": {
                "name": "x",
                # Identity-critical §1 sub-fields the verifier requires
                # to be non-empty (F-P4-OPUS-17).
                "provider_name": "Acme",
                "system_name": "x",
                "intended_purpose": "y",
            },
            "intended_purpose": "y",
            "system_components": ["a"],
            "computational_resources": {"gpu": "x"},
            "data_governance": {"sources": ["x"]},
            "technical_documentation": {"design": "x"},
            "monitoring_and_logging": {"audit_log": "x"},
            "performance_metrics": {"loss": 1.0},
            "risk_management": {"art9": "x"},
        }
        path = tmp_path / "annex_iv.json"
        path.write_text(json.dumps(artifact))
        result = verify_annex_iv_artifact(str(path))
        assert result.valid is True

    def test_verify_gguf_library_function(self, tmp_path: Path) -> None:
        from forgelm import verify_gguf

        path = tmp_path / "model.gguf"
        path.write_bytes(b"GGUF" + b"\x00" * 256)
        result = verify_gguf(str(path))
        assert result.valid is True


# ---------------------------------------------------------------------------
# Stability-tier roster — single source of truth (F-P1-FAB-27)
# ---------------------------------------------------------------------------


class TestStabilityTierMap:
    """The per-symbol tier was previously contradicted across ``_version.py``,
    the module docstring, the reference doc and the test fixtures.  Pin the
    in-code ``_STABILITY_TIERS`` map as the single source of truth and assert
    it agrees with the user-facing reference doc's Tier column.
    """

    @staticmethod
    def _reference_tier_map() -> dict[str, str]:
        import re

        ref = (Path(__file__).parent.parent / "docs" / "reference" / "library_api_reference.md").read_text(
            encoding="utf-8"
        )
        # Top-level symbol rows only: `| `forgelm.SYM` | <Tier> |` where SYM
        # has no dot (method rows like `forgelm.ForgeTrainer.train` are skipped).
        rows = re.findall(
            r"^\|\s*`forgelm\.([A-Za-z_][A-Za-z0-9_]*)`\s*\|\s*(Stable|Experimental)\s*\|",
            ref,
            re.M,
        )
        return {sym: tier.lower() for sym, tier in rows}

    def test_tier_map_covers_exactly_all(self) -> None:
        import forgelm

        assert set(forgelm._STABILITY_TIERS) == set(forgelm.__all__), (
            "_STABILITY_TIERS must record a tier for every public symbol and nothing else."
        )

    def test_tier_map_matches_reference_doc(self) -> None:
        import forgelm

        ref_tiers = self._reference_tier_map()
        # The reference doc carries a row for every top-level public symbol.
        assert set(ref_tiers) == set(forgelm.__all__), (
            f"Reference doc Tier column drifted from forgelm.__all__: {sorted(set(ref_tiers) ^ set(forgelm.__all__))}"
        )
        assert ref_tiers == forgelm._STABILITY_TIERS, (
            "library_api_reference.md Tier column disagrees with forgelm._STABILITY_TIERS: "
            f"{ {k: (ref_tiers.get(k), forgelm._STABILITY_TIERS.get(k)) for k in set(ref_tiers) | set(forgelm._STABILITY_TIERS) if ref_tiers.get(k) != forgelm._STABILITY_TIERS.get(k)} }"
        )

    def test_tiers_are_only_stable_or_experimental(self) -> None:
        import forgelm

        assert set(forgelm._STABILITY_TIERS.values()) <= {"stable", "experimental"}


# ---------------------------------------------------------------------------
# Lazy-symbol / __all__ / TYPE_CHECKING parity (F-P1-FAB-28)
# ---------------------------------------------------------------------------


class TestLazySymbolParity:
    """Adding/removing a public symbol requires synchronised edits to
    ``__all__``, ``_LAZY_SYMBOLS`` and the TYPE_CHECKING block.  Only the
    ``__all__`` leg was tested; deleting a ``_LAZY_SYMBOLS`` row broke
    ``from forgelm import X`` while the suite stayed green.  These tests pin
    the other two legs.
    """

    # Eager (non-lazy) public symbols: the versioning + config trio imported
    # directly in __init__.py rather than via the __getattr__ resolver.
    _EAGER = {"__version__", "__api_version__", "load_config", "ForgeConfig", "ConfigError"}

    def test_lazy_union_eager_equals_all(self) -> None:
        import forgelm

        assert set(forgelm._LAZY_SYMBOLS) | self._EAGER == set(forgelm.__all__), (
            "_LAZY_SYMBOLS ∪ eager symbols must equal __all__: "
            f"{sorted((set(forgelm._LAZY_SYMBOLS) | self._EAGER) ^ set(forgelm.__all__))}"
        )

    def test_every_public_symbol_resolves(self) -> None:
        """Resolve every name in __all__ through the facade in a fresh
        subprocess so a deleted ``_LAZY_SYMBOLS`` row (which leaves the name
        in __all__ + dir() but breaks ``from forgelm import X``) is caught.
        Subprocess keeps the heavy-submodule pollution out of the suite
        process, mirroring the lazy-discipline tests above.
        """
        script = (
            "import forgelm; "
            "missing = [n for n in forgelm.__all__ if not hasattr(forgelm, n)]; "
            "assert not missing, missing; "
            "print('OK')"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, f"a public symbol failed to resolve through the facade: {result.stderr}"
        assert result.stdout.strip() == "OK"

    def test_type_checking_block_parity(self) -> None:
        """Every lazy symbol must be imported inside the ``if _TYPE_CHECKING:``
        block (and vice versa) so mypy / pyright consumers see the surface.
        Parsed via AST so no heavy import is triggered."""
        import ast

        import forgelm

        src = Path(forgelm.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        tc_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.If):
                test = node.test
                is_tc = (isinstance(test, ast.Name) and test.id == "_TYPE_CHECKING") or (
                    isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
                )
                if not is_tc:
                    continue
                for stmt in ast.walk(node):
                    if isinstance(stmt, ast.ImportFrom):
                        for alias in stmt.names:
                            tc_names.add(alias.asname or alias.name)
        assert tc_names == set(forgelm._LAZY_SYMBOLS), (
            f"TYPE_CHECKING import block drifted from _LAZY_SYMBOLS: {sorted(tc_names ^ set(forgelm._LAZY_SYMBOLS))}"
        )


# ---------------------------------------------------------------------------
# Stable-symbol signature snapshot (F-P1-FAB-26)
# ---------------------------------------------------------------------------


class TestApiSignatureSnapshot:
    """No automated control forced an ``__api_version__`` MAJOR bump when a
    stable symbol's signature changed (the design-doc-promised
    ``tools/check_api_compat.py`` was never created).  This snapshot test
    fails whenever a public symbol's signature / field roster differs from
    the recorded snapshot keyed to the current ``__api_version__`` — the
    fixer then bumps the version (per the rules in ``_version.py``) and
    regenerates the snapshot.
    """

    @staticmethod
    def _describe(obj: object) -> dict:
        import dataclasses
        import inspect

        from pydantic import BaseModel

        if isinstance(obj, type) and issubclass(obj, BaseModel):
            return {"kind": "pydantic_model", "fields": sorted(obj.model_fields.keys())}
        if dataclasses.is_dataclass(obj):
            return {"kind": "dataclass", "fields": [f.name for f in dataclasses.fields(obj)]}
        try:
            return {"kind": "callable", "signature": str(inspect.signature(obj))}
        except (TypeError, ValueError):
            return {"kind": "callable", "signature": None}

    def _live_snapshot(self) -> dict:
        import forgelm

        symbols = {
            name: self._describe(getattr(forgelm, name))
            for name in forgelm.__all__
            if name not in ("__version__", "__api_version__")
        }
        return {"__api_version__": forgelm.__api_version__, "symbols": symbols}

    def test_stable_signatures_match_recorded_snapshot(self) -> None:
        import json

        import forgelm

        snapshot_path = Path(__file__).parent / "_data" / f"api_signatures_{forgelm.__api_version__}.json"
        assert snapshot_path.is_file(), (
            f"No signature snapshot for __api_version__={forgelm.__api_version__}. "
            "Generate tests/_data/api_signatures_<ver>.json and commit it with the bump."
        )
        recorded = json.loads(snapshot_path.read_text(encoding="utf-8"))
        live = self._live_snapshot()
        assert live == recorded, (
            "Public API surface changed without updating the snapshot. If this is an "
            "intentional change, bump __api_version__ MAJOR (signature change) or MINOR "
            "(additive) per forgelm/_version.py and regenerate "
            f"tests/_data/api_signatures_{forgelm.__api_version__}.json."
        )
