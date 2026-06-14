"""Tests for tools/check_site_chrome_parity.py (H11 / F-P8-C-07).

This guard parses site/js/translations.js and enforces that the active-tier
(EN<->TR) translation-key sets stay in lockstep. It was unwired and untested.
These tests pin the block parser, the active-tier drift check, the live-repo
clean pass (default mode — deferred de/fr/es/zh tiers gate only under --strict,
which is a known backlog and intentionally NOT wired into CI), and the CI
wiring this package added.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TOOL_PATH = _REPO_ROOT / "tools" / "check_site_chrome_parity.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("check_site_chrome_parity", _TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture(scope="module")
def tool():
    return _load_tool()


def test_parse_blocks_collects_keys(tool):
    source = 'T.en = {\n  "nav_home": "Home",\n  "nav_docs": "Docs",\n};\n'
    blocks = tool._parse_blocks(source)
    assert blocks["en"] == {"nav_home", "nav_docs"}


def test_parse_object_assign_block(tool):
    source = 'Object.assign(T.tr, {\n  "nav_home": "Ana Sayfa",\n});\n'
    blocks = tool._parse_blocks(source)
    assert blocks["tr"] == {"nav_home"}


def test_active_tier_lockstep_passes(tool):
    assert tool._check_active_tier({"a", "b"}, {"a", "b"}) is True


def test_active_tier_drift_fails(tool):
    assert tool._check_active_tier({"a", "b"}, {"a"}) is False


def test_real_site_in_lockstep(tool):
    """Default mode enforces only EN<->TR active-tier parity — the live mode
    wired into CI."""
    assert tool.main([]) == 0


def test_guard_wired_into_ci():
    ci = (_REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "check_site_chrome_parity.py" in ci
