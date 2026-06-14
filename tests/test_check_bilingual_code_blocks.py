"""Tests for tools/check_bilingual_code_blocks.py (H11 / F-P8-C-13).

This was a fully orphan guard — wired into no workflow and with no own test —
while its two sibling parity guards each ship 20+ tests. It enforces that an
EN/TR doc pair has the same number of fenced blocks AND the same top-level YAML
keys per block (so a translated ``yardımseverlik`` key vs ``helpfulness`` or a
collapsed pair of blocks is caught). These tests pin its detection logic, the
live-repo clean pass, and the CI wiring this package added.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TOOL_PATH = _REPO_ROOT / "tools" / "check_bilingual_code_blocks.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("check_bilingual_code_blocks", _TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    # Register before exec so the guard's dataclasses (Block) can resolve
    # ``cls.__module__`` during dataclass construction.
    sys.modules["check_bilingual_code_blocks"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture(scope="module")
def tool():
    return _load_tool()


def _write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


_FENCE = "```"


def test_matching_pair_no_diff(tool, tmp_path):
    en = _write(tmp_path, "p.md", f"# EN\n\n{_FENCE}yaml\nmodel: x\ntraining: y\n{_FENCE}\n")
    tr = _write(tmp_path, "p-tr.md", f"# TR\n\n{_FENCE}yaml\nmodel: x\ntraining: y\n{_FENCE}\n")
    assert tool._pair_diff(en, tr) == []


def test_unequal_block_count_flagged(tool, tmp_path):
    en = _write(tmp_path, "p.md", f"{_FENCE}yaml\na: 1\n{_FENCE}\n\n{_FENCE}bash\nls\n{_FENCE}\n")
    tr = _write(tmp_path, "p-tr.md", f"{_FENCE}yaml\na: 1\n{_FENCE}\n")
    diff = tool._pair_diff(en, tr)
    assert diff and "fenced-block count" in diff[0]


def test_divergent_yaml_keys_flagged(tool, tmp_path):
    # Same block count, but the TR block translated a top-level YAML key.
    en = _write(tmp_path, "p.md", f"{_FENCE}yaml\nhelpfulness: 1\n{_FENCE}\n")
    tr = _write(tmp_path, "p-tr.md", f"{_FENCE}yaml\nyardimseverlik: 1\n{_FENCE}\n")
    diff = tool._pair_diff(en, tr)
    assert diff and "top-level keys diverge" in diff[0]


def test_lang_mismatch_flagged(tool, tmp_path):
    en = _write(tmp_path, "p.md", f"{_FENCE}yaml\na: 1\n{_FENCE}\n")
    tr = _write(tmp_path, "p-tr.md", f"{_FENCE}json\n{{}}\n{_FENCE}\n")
    diff = tool._pair_diff(en, tr)
    assert diff and "lang differs" in diff[0]


def test_real_repo_is_clean(tool):
    assert tool.main(["--quiet"]) == 0


def test_guard_wired_into_ci():
    ci = (_REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "check_bilingual_code_blocks.py" in ci
