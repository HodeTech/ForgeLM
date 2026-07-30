"""Tests for the mutation-scaffolding guard.

The guard exists because of a real incident: during Phase 16 S2 a review
agent's `if False:` was left in a shared working tree, swept into a commit by
`git add -A`, and shipped at HEAD — disabling the benchmark fail-closed gate
that step had just added — while 4,644 tests and 29 CI guards reported green.
`ruff` does not flag `if False:`; it is legal Python.

The guard is deliberately narrow. `while True:` appears fourteen times in this
codebase as the idiomatic infinite loop, and flagging it would make the guard
noise — which is how a guard becomes something people learn to skip.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_guard(monkeypatch):
    path = _REPO_ROOT / "tools" / "check_no_mutation_artifacts.py"
    spec = importlib.util.spec_from_file_location("check_no_mutation_artifacts", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "check_no_mutation_artifacts", module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def guard(monkeypatch):
    return _load_guard(monkeypatch)


class TestDetection:
    @pytest.mark.parametrize(
        "source,expected",
        [
            ("if False:\n    x = 1\n", True),
            ("if True:\n    x = 1\n", True),
            ("while False:\n    x = 1\n", True),
            # The idiom. Must never fire.
            ("while True:\n    break\n", False),
            ("if x:\n    y = 1\n", False),
            ("if TYPE_CHECKING:\n    import os\n", False),
            ("if sys.platform == 'win32':\n    x = 1\n", False),
        ],
        ids=["if-false", "if-true", "while-false", "while-true", "normal", "type-checking", "platform"],
    )
    def test_only_literal_branches_are_flagged(self, guard, tmp_path, source, expected):
        sample = tmp_path / "sample.py"
        sample.write_text(source, encoding="utf-8")
        monkey = guard._REPO_ROOT
        guard._REPO_ROOT = tmp_path
        try:
            findings = guard.scan_file(sample)
        finally:
            guard._REPO_ROOT = monkey
        assert bool(findings) is expected, f"{source!r} -> {findings}"

    def test_the_message_names_the_right_keyword(self, guard, tmp_path):
        """A `while False:` reported as `if False:` sends the reader to the wrong line.

        The guard's first version formatted every finding as `if <value>:`
        regardless of node type, which also made its own output look like it
        had found fourteen `if True:` statements that were really `while True:`.
        """
        sample = tmp_path / "sample.py"
        sample.write_text("while False:\n    x = 1\n", encoding="utf-8")
        guard._REPO_ROOT, original = tmp_path, guard._REPO_ROOT
        try:
            findings = guard.scan_file(sample)
        finally:
            guard._REPO_ROOT = original
        assert "while False:" in findings[0]

    def test_explicit_markers_are_flagged(self, guard, tmp_path):
        sample = tmp_path / "sample.py"
        sample.write_text("x = 1  # MUTANT: flipped for the experiment\n", encoding="utf-8")
        guard._REPO_ROOT, original = tmp_path, guard._REPO_ROOT
        try:
            findings = guard.scan_file(sample)
        finally:
            guard._REPO_ROOT = original
        assert findings and "MUTANT" in findings[0]


class TestAgainstTheRepo:
    def test_shipped_source_is_clean(self, guard):
        assert guard.main(["--quiet"]) == 0, (
            "forgelm/ carries mutation scaffolding — a disabled branch that still reads as live code"
        )

    def test_the_guard_reports_what_it_examined(self, guard, capsys):
        assert guard.main([]) == 0
        assert "module(s) under" in capsys.readouterr().out, (
            "a guard that prints nothing on success cannot be distinguished from one that ran nothing"
        )
