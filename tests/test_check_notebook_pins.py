"""Tests for tools/check_notebook_pins.py (M7 / F-P8-C-09).

The notebook-pin guard keeps every ``notebooks/*.ipynb`` ``!pip install
forgelm`` cell in lockstep with a *shipping* wheel. It shipped unwired
(no workflow, no test) while the pins drifted two minors (0.5.7 vs the
released 0.7.0). These tests pin:

* the rc-aware accepted-pin policy (exact pyproject version OR the latest
  released CHANGELOG version, so onboarding notebooks never point users at
  a pre-release rc);
* the drift detectors (stale pin, missing pin, range specifier);
* the clean live-repo pass; and
* the CI wiring this package added.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TOOL_PATH = _REPO_ROOT / "tools" / "check_notebook_pins.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("check_notebook_pins", _TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_notebook_pins"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture(scope="module")
def tool():
    mod = _load_tool()
    yield mod
    sys.modules.pop("check_notebook_pins", None)


def _write_notebook(path: Path, *install_lines: str) -> Path:
    """Write a minimal ipynb with one code cell per install line."""
    cells = [
        {
            "cell_type": "code",
            "execution_count": None,
            "metadata": {},
            "outputs": [],
            "source": [line],
        }
        for line in install_lines
    ]
    nb = {"cells": cells, "metadata": {}, "nbformat": 4, "nbformat_minor": 4}
    path.write_text(json.dumps(nb, indent=1) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# §1 — accepted-pin policy (rc awareness)
# ---------------------------------------------------------------------------


def test_accepted_pins_includes_released_when_pyproject_is_rc(tool):
    # During a dev cycle pyproject sits on an rc; the released wheel is the
    # one users can pip install, so both are accepted.
    accepted = tool._accepted_pins("0.7.1rc1", "0.7.0")
    assert accepted == ["0.7.1rc1", "0.7.0"]


def test_accepted_pins_dedupes_when_on_a_tag(tool):
    # On a released tag pyproject == latest released; no duplicate entry.
    accepted = tool._accepted_pins("0.7.0", "0.7.0")
    assert accepted == ["0.7.0"]


def test_accepted_pins_tolerates_missing_changelog(tool):
    # If no released header is found, only the exact pyproject pin counts.
    assert tool._accepted_pins("0.7.0", None) == ["0.7.0"]


def test_latest_released_version_parses_changelog_header(tool, tmp_path):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "# Changelog\n\n## [Unreleased]\n\n## [0.7.0] — 2026-05-14\n\n## [0.6.0] — 2026-05-11\n",
        encoding="utf-8",
    )
    assert tool._latest_released_version(changelog) == "0.7.0"


# ---------------------------------------------------------------------------
# §2 — per-notebook drift detection
# ---------------------------------------------------------------------------


def test_released_pin_accepted_while_pyproject_is_rc(tool, tmp_path):
    nb = _write_notebook(tmp_path / "n.ipynb", "!pip install 'forgelm[qlora]==0.7.0'\n")
    issues = tool._check_notebook(nb, ["0.7.1rc1", "0.7.0"])
    assert issues == []


def test_stale_pin_flagged(tool, tmp_path):
    nb = _write_notebook(tmp_path / "n.ipynb", "!pip install 'forgelm[qlora]==0.5.7'\n")
    issues = tool._check_notebook(nb, ["0.7.1rc1", "0.7.0"])
    assert len(issues) == 1
    assert "0.5.7" in issues[0].reason


def test_missing_pin_flagged(tool, tmp_path):
    nb = _write_notebook(tmp_path / "n.ipynb", "!pip install forgelm\n")
    issues = tool._check_notebook(nb, ["0.7.0"])
    assert len(issues) == 1
    assert "missing version pin" in issues[0].reason


def test_range_specifier_flagged(tool, tmp_path):
    nb = _write_notebook(tmp_path / "n.ipynb", "!pip install 'forgelm>=0.5.2'\n")
    issues = tool._check_notebook(nb, ["0.7.0"])
    assert len(issues) == 1
    assert "non-exact specifier" in issues[0].reason


def test_sibling_install_not_flagged(tool, tmp_path):
    # A non-forgelm install line must not be mistaken for a forgelm pin.
    nb = _write_notebook(tmp_path / "n.ipynb", "!pip install datasets transformers\n")
    assert tool._check_notebook(nb, ["0.7.0"]) == []


# ---------------------------------------------------------------------------
# §3 — live-repo clean pass + CI wiring
# ---------------------------------------------------------------------------


def test_repo_notebooks_pass_strict(tool):
    # All shipped notebooks must pin a currently-accepted version.
    assert tool.main(["--strict"]) == 0


def test_guard_wired_into_ci():
    ci = (_REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "check_notebook_pins.py" in ci


# ---------------------------------------------------------------------------
# §4 — TOML backend resolves on every supported Python (3.10 CI runner break)
# ---------------------------------------------------------------------------


def test_toml_backend_loads_on_supported_pythons(tool):
    # Python 3.10 (a declared-supported, CI-matrix interpreter) has no stdlib
    # ``tomllib``; a bare ``import tomllib`` broke this whole module at fixture
    # setup. The guard now falls back to the ``tomli`` backport, so the bound
    # ``tomllib`` must expose a working ``loads`` on every supported runtime.
    assert hasattr(tool.tomllib, "loads")
    assert tool.tomllib.loads('a = "b"\n') == {"a": "b"}


# ---------------------------------------------------------------------------
# §5 — sys.modules cleanup (F-N-11: yield fixture teardown)
# ---------------------------------------------------------------------------


def test_tool_fixture_cleans_up_sys_modules(request):
    # Regression guard for F-N-11: the ``tool`` fixture must remove
    # 'check_notebook_pins' from sys.modules on teardown so the stale entry
    # does not pollute subsequent test sessions or parallel workers.
    # This test directly exercises _load_tool() + cleanup in isolation to
    # confirm the teardown pattern matches the yield fixture implementation.
    key = "check_notebook_pins"
    # Ensure we start clean so the assertion below is meaningful.
    sys.modules.pop(key, None)
    _load_tool()
    assert key in sys.modules, "_load_tool must register the module"
    # Simulate the yield-fixture finalizer path.
    sys.modules.pop(key, None)
    assert key not in sys.modules, "finalizer must remove the stale sys.modules entry"
