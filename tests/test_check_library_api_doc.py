"""Tests for tools/check_library_api_doc.py (H11 / F-P8-C-07).

This guard cross-checks ``forgelm.__all__`` against the symbol roster in
docs/reference/library_api_reference.md (both directions). It was unwired into
any workflow and had no own test, so a renamed/removed public symbol could
drift from the reference page. These tests pin the doc-symbol scraper, both
drift directions via a synthetic ``--doc`` file, the live-repo clean pass, and
the CI wiring this package added.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TOOL_PATH = _REPO_ROOT / "tools" / "check_library_api_doc.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("check_library_api_doc", _TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture(scope="module")
def tool():
    return _load_tool()


def _doc(tmp_path: Path, names) -> Path:
    rows = "\n".join(f"| `forgelm.{n}` | desc |" for n in names)
    p = tmp_path / "lib.md"
    p.write_text("| Symbol | Desc |\n|---|---|\n" + rows + "\n", encoding="utf-8")
    return p


def test_doc_symbols_scraper(tool, tmp_path):
    doc = _doc(tmp_path, ["ForgeTrainer", "AuditLogger", "ForgeTrainer.train"])
    assert tool.doc_symbols(doc) == {"ForgeTrainer", "AuditLogger", "ForgeTrainer.train"}


def test_real_repo_in_sync(tool):
    """The shipped reference doc matches forgelm.__all__ — the live tripwire."""
    assert tool.main(["--quiet"]) == 0


def test_symbol_missing_from_doc_fails(tool, tmp_path):
    import forgelm

    # A doc listing only ONE real symbol drops the rest of __all__ → drift.
    one = sorted(forgelm.__all__)[0]
    doc = _doc(tmp_path, [one])
    assert tool.main(["--doc", str(doc), "--quiet"]) == 1


def test_ghost_doc_row_fails(tool, tmp_path):
    import forgelm

    # Every real symbol PLUS a ghost that __all__ does not export → drift.
    doc = _doc(tmp_path, list(forgelm.__all__) + ["TotallyRemovedSymbol"])
    assert tool.main(["--doc", str(doc), "--quiet"]) == 1


def test_missing_doc_file_fails(tool, tmp_path):
    assert tool.main(["--doc", str(tmp_path / "nope.md"), "--quiet"]) == 1


def test_guard_wired_into_ci():
    ci = (_REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "check_library_api_doc.py" in ci
