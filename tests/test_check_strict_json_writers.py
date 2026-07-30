"""Tests for the strict-JSON writer-discipline guard.

Phase 16 S2 converted four writers and declared the class closed. Its own
review then found `compliance_report.json` — an EU AI Act Art. 11 / Annex IV
artefact — still emitting bare `NaN` from the same metrics dict, plus
`safety_results.json` and `safety_trend.jsonl`. Converting instances does not
close a class; this guard does.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def guard(monkeypatch):
    path = _REPO_ROOT / "tools" / "check_strict_json_writers.py"
    spec = importlib.util.spec_from_file_location("check_strict_json_writers", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "check_strict_json_writers", module)
    spec.loader.exec_module(module)
    return module


class TestAgainstTheRepo:
    def test_every_writer_module_is_clean(self, guard):
        assert guard.main(["--quiet"]) == 0

    def test_the_guard_reports_what_it_examined(self, guard, capsys):
        assert guard.main([]) == 0
        out = capsys.readouterr().out
        assert "artefact-writing module(s)" in out and "exempt with reasons" in out, (
            "a guard that prints nothing on success cannot be distinguished from one that ran nothing"
        )

    def test_every_listed_module_exists(self, guard):
        missing = [m for m in guard._WRITER_MODULES if not (_REPO_ROOT / m).exists()]
        assert not missing, f"_WRITER_MODULES names non-existent module(s): {missing}"


class TestExclusionsStayHonest:
    def test_each_exclusion_names_a_live_function(self, guard):
        """A stale exclusion reads as a reviewed decision while naming nothing.

        The guard's first run caught exactly this in its own table: it excluded
        `_canonical_entry_json`, a function that does not exist, so the real
        HMAC verifier was being flagged while a phantom was exempted.
        """
        for rel_path, func in guard._HASH_CANONICALISATION:
            source = (_REPO_ROOT / rel_path).read_text(encoding="utf-8")
            assert f"def {func}" in source, f"{rel_path} exclusion names {func}(), which does not exist"

    def test_each_exclusion_carries_a_reason(self, guard):
        blank = [k for k, v in guard._HASH_CANONICALISATION.items() if not v.strip()]
        assert not blank, f"exclusions with no written reason: {blank}"


class TestDetection:
    def test_a_raw_json_dump_in_a_writer_module_is_caught(self, guard, tmp_path, monkeypatch):
        """The mutation the guard exists for."""
        sample = tmp_path / "forgelm" / "writer.py"
        sample.parent.mkdir(parents=True)
        sample.write_text("import json\n\n\ndef save(payload, fh):\n    json.dump(payload, fh)\n", encoding="utf-8")
        monkeypatch.setattr(guard, "_REPO_ROOT", tmp_path)
        findings = guard.scan_file("forgelm/writer.py")
        assert findings and "save()" in findings[0]

    def test_dumps_strict_is_accepted(self, guard, tmp_path, monkeypatch):
        sample = tmp_path / "forgelm" / "writer.py"
        sample.parent.mkdir(parents=True)
        sample.write_text(
            "from ._strict_json import dumps_strict\n\n\ndef save(payload, fh):\n    fh.write(dumps_strict(payload))\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(guard, "_REPO_ROOT", tmp_path)
        assert guard.scan_file("forgelm/writer.py") == []
