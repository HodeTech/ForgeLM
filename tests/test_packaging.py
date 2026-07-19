"""Packaging regression tests (Phase 10.5 / wheel-install net).

Editable installs hide ``package_data`` mistakes because
``Path(__file__).parent`` resolves quickstart templates from the source
checkout regardless of what setuptools actually copied. These tests
exercise the *importlib.resources* path — which mirrors what a real
``pip install forgelm-X.Y.Z-py3-none-any.whl`` exposes — and assert that
the YAML/JSONL/Markdown assets advertised by :mod:`forgelm.quickstart`
are reachable as package resources.

A missing assertion here means the wheel will silently ship without a
template asset; the corresponding nightly job (``wheel-install-smoke``)
catches the same class of regression end-to-end.
"""

from __future__ import annotations

import importlib.resources as ir
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

import forgelm.templates
from forgelm.quickstart import TEMPLATES


def test_templates_dir_is_a_real_python_package() -> None:
    """``forgelm.templates`` must be an importable subpackage.

    Without an ``__init__.py``, ``importlib.resources`` would fall back to
    namespace-package semantics that do not surface bundled data files
    after a wheel install.
    """

    init_file = getattr(forgelm.templates, "__file__", None)
    assert init_file is not None, (
        "forgelm.templates has no __file__ attribute — it became a namespace "
        "package. Wheels would not bundle the templates' data files. "
        "Restore forgelm/templates/__init__.py."
    )
    init_path = Path(init_file)
    assert init_path.is_file(), (
        f"forgelm.templates.__init__.py missing at {init_path}; templates would not be importable from a wheel install."
    )
    assert init_path.name == "__init__.py"


def test_each_template_directory_is_discoverable_via_importlib_resources() -> None:
    """Every registered template's bundled assets must resolve via importlib.resources."""

    root = ir.files("forgelm.templates")
    for name, template in TEMPLATES.items():
        config_resource = root / name / "config.yaml"
        assert config_resource.is_file(), (
            f"Template '{name}' missing config.yaml as a package resource — package_data globs likely fail to ship it."
        )
        if template.bundled_dataset:
            dataset_resource = root / name / "data.jsonl"
            assert dataset_resource.is_file(), (
                f"Template '{name}' advertises bundled_dataset=True but data.jsonl is not packaged as a resource."
            )


def test_top_level_licenses_md_is_packaged() -> None:
    """The top-level LICENSES.md inside forgelm/templates/ must ship in the wheel."""

    licenses_resource = ir.files("forgelm.templates") / "LICENSES.md"
    assert licenses_resource.is_file(), (
        "forgelm/templates/LICENSES.md is not packaged — top-level *.md "
        "glob in [tool.setuptools.package-data] may be missing."
    )


def test_domain_expert_readme_is_packaged() -> None:
    """The domain-expert README explains the BYOD flow and must travel with the wheel."""

    readme_resource = ir.files("forgelm.templates") / "domain-expert" / "README.md"
    assert readme_resource.is_file(), (
        "forgelm/templates/domain-expert/README.md is not packaged — subdirectory */*.md glob may be missing."
    )


def test_pyproject_package_data_globs_cover_every_extension() -> None:
    """Guard against accidental removal of the package_data globs.

    We assert (a) the ``forgelm.templates`` key exists and (b) the four
    glob patterns we rely on are all present. Any future edit that drops
    one of these patterns will trip this test before it ships a broken
    wheel.
    """

    try:
        import tomllib  # type: ignore[import-not-found]
    except ModuleNotFoundError:  # pragma: no cover — Python <3.11 fallback
        try:
            import tomli as tomllib  # type: ignore[import-not-found, no-redef]
        except ModuleNotFoundError:
            pytest.skip("Neither tomllib (3.11+) nor tomli is available; package_data glob assertion skipped.")

    pyproject_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
    assert pyproject_path.is_file(), f"pyproject.toml not found at {pyproject_path}"

    with pyproject_path.open("rb") as fh:
        pyproject = tomllib.load(fh)

    package_data = pyproject.get("tool", {}).get("setuptools", {}).get("package-data", {})
    assert "forgelm.templates" in package_data, (
        "[tool.setuptools.package-data] is missing the 'forgelm.templates' key; "
        "wheel installs would not bundle quickstart assets."
    )

    globs = package_data["forgelm.templates"]
    required_patterns = {"*.md", "*/*.yaml", "*/*.jsonl", "*/*.md"}
    missing = required_patterns - set(globs)
    assert not missing, (
        f"package_data['forgelm.templates'] is missing required glob(s): {sorted(missing)}. Present: {sorted(globs)}."
    )


def _load_optional_dependencies() -> dict:
    try:
        import tomllib  # type: ignore[import-not-found]
    except ModuleNotFoundError:  # pragma: no cover — Python 3.10 fallback
        try:
            import tomli as tomllib  # type: ignore[import-not-found, no-redef]
        except ModuleNotFoundError:
            pytest.skip("Neither tomllib (3.11+) nor tomli is available; pyproject extras assertion skipped.")
    pyproject_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
    with pyproject_path.open("rb") as fh:
        pyproject = tomllib.load(fh)
    return pyproject.get("project", {}).get("optional-dependencies", {})


def test_no_extra_declares_unimported_mergekit() -> None:
    """F-P3-FABLE-20: mergekit was declared in the ``merging`` extra but never
    imported anywhere — a heavy, env-conflicting dependency for zero benefit
    (merging is native peft+torch). It must not reappear in any extra."""
    extras = _load_optional_dependencies()
    for extra, deps in extras.items():
        for dep in deps:
            assert "mergekit" not in dep.lower(), (
                f"extra '{extra}' declares mergekit ({dep!r}); model merging is native — no mergekit dep."
            )


def test_merging_extra_is_a_noop() -> None:
    """The ``merging`` extra is retained for `pip install forgelm[merging]`
    backward-compat but installs nothing (merging needs no extra packages)."""
    extras = _load_optional_dependencies()
    assert extras.get("merging", None) == [], (
        f"the 'merging' extra should be an empty no-op; got {extras.get('merging')!r}"
    )


# ---------------------------------------------------------------------------
# [build-system].requires setuptools security floor (PYSEC-2026-3447).
#
# The nightly supply-chain gate (tools/check_pip_audit.py, run via
# .github/workflows/nightly.yml) failed closed for days starting 2026-07-15
# on setuptools PYSEC-2026-3447, fixed in setuptools 83.0.0. The fix raised
# [build-system].requires' setuptools floor to ">=83.0.0" so a build-from-
# source can never provision a vulnerable build toolchain. Nothing besides
# this test stops a routine hygiene PR from silently lowering that floor
# again — machine-check it so the regression fails CI immediately instead
# of waiting for the nightly to go red.
# ---------------------------------------------------------------------------

# The PYSEC-2026-3447 fix floor. Do not lower without re-verifying the
# advisory is resolved by an earlier setuptools release.
_SETUPTOOLS_SECURITY_FLOOR = Version("83.0.0")

# Operators that establish a lower bound on the admissible versions. `~=X.Y`
# and `==X.Y.*` both floor at their own version, so they count; `<`, `<=`,
# `!=` and `===` do not bound below and are ignored when deriving the floor.
_LOWER_BOUND_OPERATORS = frozenset({">=", ">", "==", "~="})


def _load_build_system_requires() -> list:
    try:
        import tomllib  # type: ignore[import-not-found]
    except ModuleNotFoundError:  # pragma: no cover — Python 3.10 fallback
        try:
            import tomli as tomllib  # type: ignore[import-not-found, no-redef]
        except ModuleNotFoundError:
            pytest.skip("Neither tomllib (3.11+) nor tomli is available; build-system requires assertion skipped.")
    pyproject_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
    with pyproject_path.open("rb") as fh:
        pyproject = tomllib.load(fh)
    return pyproject.get("build-system", {}).get("requires", [])


def test_setuptools_security_floor_meets_pysec_2026_3447() -> None:
    """[build-system].requires must pin setuptools>=83.0.0 (PYSEC-2026-3447).

    setuptools 83.0.0 is the first release carrying the fix for
    PYSEC-2026-3447 — the CVE that took ForgeLM's nightly pip-audit gate
    red for days (2026-07-15 onward) until the floor was raised. This is a
    security floor, not a feature floor: a hygiene PR that bumps or
    reformats [build-system].requires must not silently drop below it.

    Parsing goes through ``packaging`` rather than a hand-rolled regex so
    the assertion tracks what pip would actually resolve: ``setuptools >=
    83.0.0``, ``setuptools[core]>=83.0.0,<84``, ``setuptools ~= 83.0`` and
    ``setuptools==83.1.0`` are all semantically fine and must pass, while a
    regex over the raw string rejects most of them for cosmetic reasons.
    """
    requires = _load_build_system_requires()
    parsed = [Requirement(entry) for entry in requires]
    setuptools_reqs = [r for r in parsed if canonicalize_name(r.name) == "setuptools"]
    assert setuptools_reqs, (
        "[build-system].requires has no 'setuptools' entry; PYSEC-2026-3447 "
        "(fixed in setuptools 83.0.0) requires an explicit lower-bound pin."
    )
    assert len(setuptools_reqs) == 1, (
        f"expected exactly one setuptools requirement in [build-system].requires, found {requires!r}"
    )
    requirement = setuptools_reqs[0]
    specifier_set = requirement.specifier

    lower_bounds = [
        Version(spec.version.rstrip(".*")) for spec in specifier_set if spec.operator in _LOWER_BOUND_OPERATORS
    ]
    assert lower_bounds, (
        f"[build-system].requires setuptools entry {str(requirement)!r} has no lower bound; "
        f"PYSEC-2026-3447 (fixed in setuptools {_SETUPTOOLS_SECURITY_FLOOR}) requires an explicit "
        "floor, not just an upper bound or an unpinned dependency."
    )
    effective_floor = max(lower_bounds)
    assert effective_floor >= _SETUPTOOLS_SECURITY_FLOOR, (
        f"[build-system].requires setuptools entry {str(requirement)!r} floors at {effective_floor}, "
        f"which is below the PYSEC-2026-3447 fix floor of setuptools>={_SETUPTOOLS_SECURITY_FLOOR}. "
        "Do not lower this floor without re-verifying the advisory is resolved by an earlier release."
    )
    # The property the floor exists to guarantee, asserted directly: no
    # pre-fix setuptools may satisfy the pin. Catches an exotic specifier
    # whose effective floor the operator modelling above reads too kindly.
    vulnerable = Version("82.999.999")
    assert not specifier_set.contains(vulnerable, prereleases=True), (
        f"[build-system].requires setuptools entry {str(requirement)!r} still admits {vulnerable}, "
        f"which predates the PYSEC-2026-3447 fix in setuptools {_SETUPTOOLS_SECURITY_FLOOR}."
    )
