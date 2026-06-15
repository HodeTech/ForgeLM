"""Tests for tools/check_no_unguarded_sys_modules_pop.py (H11 / F-P8-C-08).

This guard is the ONE that is wired into ci.yml but had no own test, so a regex
regression would silently neuter an enforced gate (the v0.5.7 round-3 footgun
the guard exists to prevent). It was also bypassable: the original ``= None``
pattern missed ``= object()`` / ``= SimpleNamespace()`` rebinds, and the
per-physical-line scan missed the multi-line split form.

These tests pin both the detected forms (each known bypass exits 1) and the
sanctioned forms (delitem / patch.dict / a guarded-module equality comparison
stay green), plus the live-repo clean pass and CI wiring.

IMPORTANT: the offending source fragments are assembled at runtime (the
``_SM`` prefix is built by string concatenation) rather than written as
literals, so this test FILE does not itself contain a matchable ``sys.modules``
eviction — the guard scans tests/ and would otherwise flag its own test source.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TOOL_PATH = _REPO_ROOT / "tools" / "check_no_unguarded_sys_modules_pop.py"

# Built from pieces so the literal eviction call never appears in this source.
_SM = "sys" + ".modules"


def _load_tool():
    spec = importlib.util.spec_from_file_location("check_no_unguarded_sys_modules_pop", _TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture(scope="module")
def tool():
    return _load_tool()


def _scan(tool, tmp_path: Path, source: str) -> list:
    f = tmp_path / "fixture.py"
    f.write_text(source, encoding="utf-8")
    return tool._scan_file(f)


# --- Forms that MUST be flagged -------------------------------------------


def test_pop_literal_flagged(tool, tmp_path):
    assert _scan(tool, tmp_path, f'{_SM}.pop("torch")\n')


def test_del_flagged(tool, tmp_path):
    assert _scan(tool, tmp_path, f'del {_SM}["numpy"]\n')


def test_assign_none_flagged(tool, tmp_path):
    assert _scan(tool, tmp_path, f'{_SM}["torch"] = None\n')


def test_assign_object_flagged(tool, tmp_path):
    """F-P8-C-08 bypass: ``= object()`` leaves a fake module unrestored exactly
    like ``= None`` — the original guard missed it."""
    assert _scan(tool, tmp_path, f'{_SM}["torch"] = object()\n')


def test_assign_namespace_flagged(tool, tmp_path):
    assert _scan(tool, tmp_path, f"import types\n{_SM}['trl'] = types.SimpleNamespace()\n")


def test_multiline_pop_flagged(tool, tmp_path):
    """F-P8-C-08 bypass: a ``.pop(`` split across physical lines slipped past
    the per-physical-line scan until logical lines were rejoined."""
    findings = _scan(tool, tmp_path, f'{_SM}.pop(\n    "transformers")\n')
    assert findings
    # Reported line number points at the statement start, not the closing line.
    assert findings[0][0] == 1


# --- Forms that MUST NOT be flagged ---------------------------------------


def test_monkeypatch_delitem_not_flagged(tool, tmp_path):
    assert _scan(tool, tmp_path, f'monkeypatch.delitem({_SM}, "torch", raising=False)\n') == []


def test_patch_dict_not_flagged(tool, tmp_path):
    assert _scan(tool, tmp_path, f"patch.dict({_SM}, " + '{"torch": fake})\n') == []


def test_equality_comparison_not_flagged(tool, tmp_path):
    """``==`` is a comparison, not a rebind — the negative lookahead keeps it
    out of the assignment pattern."""
    assert _scan(tool, tmp_path, f'if {_SM}["torch"] == sentinel:\n    pass\n') == []


def test_unguarded_pure_module_not_flagged(tool, tmp_path):
    """A Python-pure helper module is outside the guarded set — evicting it does
    not corrupt the C-extension session, so it is allowed."""
    assert _scan(tool, tmp_path, f'{_SM}.pop("forgelm.utils")\n') == []


def test_comment_line_not_flagged(tool, tmp_path):
    assert _scan(tool, tmp_path, f'# {_SM}.pop("torch") is forbidden\n') == []


# --- Live repo + wiring ----------------------------------------------------


def test_real_repo_is_clean(tool):
    """The shipped tests/ + forgelm/ tree carries no unguarded eviction — this
    is the live tripwire ci.yml runs on every PR."""
    assert tool.main() == 0


def test_guard_wired_into_ci():
    ci = (_REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "check_no_unguarded_sys_modules_pop.py" in ci
