"""Meta-tests for the pytest marker roster (F-P8-C-20).

Every marker declared in ``[tool.pytest.ini_options].markers`` must be
applied to at least one test — a declared-but-unused marker is dead
configuration that misleads contributors (the previous roster declared
``unit``/``integration``/``smoke``/``slow``/``fixture_drift`` with ZERO
usages and documented a ``-m`` selection that never existed).

``--strict-markers`` (set in ``addopts``) guarantees the reverse: an
*undefined* marker is now an error, not a silent no-op.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = REPO_ROOT / "tests"

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - Python < 3.11 fallback
    import tomli as tomllib


def _declared_markers() -> set[str]:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    raw = data["tool"]["pytest"]["ini_options"]["markers"]
    # Each entry is "name: description"; take the name before the colon.
    return {entry.split(":", 1)[0].strip() for entry in raw}


def _applied_markers() -> set[str]:
    applied: set[str] = set()
    pat = re.compile(r"(?:@pytest\.mark\.|pytestmark\s*=\s*pytest\.mark\.)(\w+)")
    for path in TESTS_DIR.rglob("test_*.py"):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        applied.update(pat.findall(text))
    return applied


def test_every_declared_marker_is_applied():
    declared = _declared_markers()
    applied = _applied_markers()
    unused = declared - applied
    assert unused == set(), (
        f"markers declared in pyproject but never applied: {sorted(unused)} — "
        "apply them to a test or remove them from "
        "[tool.pytest.ini_options].markers (F-P8-C-20)."
    )


def test_strict_markers_enabled():
    addopts = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "--strict-markers" in addopts, (
        "--strict-markers must stay in addopts so a typo'd @pytest.mark is an error, not a silent no-op."
    )


def test_fixture_drift_marks_cost_estimation():
    # The publish workflow excludes drift-sensitive tests via
    # `-m 'not fixture_drift'`; that selection is only real if the marker
    # is actually applied to the cost-estimation module.
    text = (TESTS_DIR / "test_cost_estimation.py").read_text(encoding="utf-8")
    assert "pytest.mark.fixture_drift" in text


def test_publish_uses_marker_selection():
    publish = (REPO_ROOT / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")
    assert "not fixture_drift" in publish, "publish.yml must select via the marker, not a brittle --ignore path."
