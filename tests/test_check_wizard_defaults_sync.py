"""Tests for ``tools/check_wizard_defaults_sync.py`` (G2 / review-cycle 3).

The CI guard's happy path is implicitly covered by the gauntlet's
``python tools/check_wizard_defaults_sync.py`` invocation in
``CLAUDE.md``.  This module pins the FAIL path: when the shipped
artefacts disagree with a fresh schema regeneration, ``main()`` must
exit non-zero AND surface a meaningful diff.

Without this test a regression in the diff-printing logic (or in the
byte-comparison branch) could cause the guard to silently report
"OK" against a corrupted JSON, producing exactly the schema↔shipped
drift the guard is meant to catch.

The guard also exercises ``tools/generate_wizard_defaults.py``'s
new B1 (Optional[BaseModel] unwrap) + B2 (PydanticUndefined identity
comparison) hardening as a side effect — the regenerated artefacts
must be byte-identical to the committed ones, which only works if
the walk produces deterministic output.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


class TestCIGuardFailPath:
    """The guard must exit non-zero + show a diff when artefacts drift."""

    def test_corrupt_json_triggers_failure(self, tmp_path, monkeypatch, capsys):
        from tools import check_wizard_defaults_sync as guard

        # Redirect the guard's PYTHON_TARGET to a tmp file containing
        # deliberately wrong JSON; the JS_TARGET stays pointed at the
        # real (correct) shipped file so we can verify the guard
        # discovers the drift on Python side specifically.
        bad_python = tmp_path / "_defaults.json"
        bad_python.write_text(
            '{\n  "lora": {\n    "r": 99\n  }\n}\n',  # wrong: schema says r=8
            encoding="utf-8",
        )
        monkeypatch.setattr(guard, "PYTHON_TARGET", bad_python)
        rc = guard.main()
        assert rc != 0, "guard must exit non-zero on schema↔JSON drift"
        out = capsys.readouterr().out
        assert "drifted" in out, "diff message must call out the drift"
        # The guard prints "Fix: … python tools/generate_wizard_defaults.py"
        # so the operator knows how to repair without scanning docs.
        assert "tools/generate_wizard_defaults.py" in out

    def test_missing_target_triggers_failure(self, tmp_path, monkeypatch, capsys):
        from tools import check_wizard_defaults_sync as guard

        # Point at a non-existent file; the guard must NOT crash and
        # MUST report the missing-file failure clearly.
        nonexistent = tmp_path / "_defaults.json"  # never created
        monkeypatch.setattr(guard, "PYTHON_TARGET", nonexistent)
        rc = guard.main()
        assert rc != 0
        out = capsys.readouterr().out
        assert "missing" in out.lower()

    def test_diff_output_truncated_at_5_lines(self, tmp_path, monkeypatch, capsys):
        from tools import check_wizard_defaults_sync as guard

        # Force MANY differing lines to verify the truncation ellipsis fires.
        bad_lines = "\n".join(f'  "wrong_key_{i}": {i}' for i in range(20))
        bad_python = tmp_path / "_defaults.json"
        bad_python.write_text("{\n" + bad_lines + "\n}\n", encoding="utf-8")
        monkeypatch.setattr(guard, "PYTHON_TARGET", bad_python)
        rc = guard.main()
        assert rc != 0
        out = capsys.readouterr().out
        # Truncation marker is part of the contract — operators should
        # not be flooded with thousands of diff lines.
        assert "truncated" in out.lower()


class TestGeneratorOptionalUnwrap:
    """B1 — generator unwraps ``Optional[BaseModel]`` so flagged sub-fields aren't lost."""

    def test_unwrap_basemodel_handles_optional(self):
        # Construct a synthetic Optional[BaseModel] annotation and
        # assert _unwrap_basemodel returns the inner class.  Avoids
        # depending on which ForgeConfig sub-blocks are Optional today.
        from typing import Optional

        from pydantic import BaseModel

        from tools.generate_wizard_defaults import _unwrap_basemodel

        class _Inner(BaseModel):
            pass

        assert _unwrap_basemodel(_Inner) is _Inner
        assert _unwrap_basemodel(Optional[_Inner]) is _Inner

    def test_unwrap_basemodel_returns_none_for_unrelated_types(self):
        from tools.generate_wizard_defaults import _unwrap_basemodel

        assert _unwrap_basemodel(int) is None
        assert _unwrap_basemodel(str) is None

    def test_walk_picks_up_flag_on_optional_submodel(self):
        # Synthetic schema where the Optional[Inner] sub-block carries
        # a wizard-flagged field; pre-B1 the walk would silently skip
        # this entirely.
        from typing import Optional

        from pydantic import BaseModel, Field

        from tools.generate_wizard_defaults import _walk_model

        class _Inner(BaseModel):
            magic: int = Field(default=42, json_schema_extra={"wizard": True})

        class _Outer(BaseModel):
            inner: Optional[_Inner] = None

        sink = {}
        # Drive the walk from _Outer's perspective the same way
        # ``collect_defaults`` does at the top level.
        from tools.generate_wizard_defaults import _unwrap_basemodel

        for name, field_info in _Outer.model_fields.items():
            nested = _unwrap_basemodel(field_info.annotation)
            if nested is not None:
                _walk_model(nested, name, sink)
        assert sink == {"inner": {"magic": 42}}, "Optional[Inner] sub-block was silently skipped — B1 regression"


class TestGeneratorTopLevelScalarFlag:
    """F-P1-FAB-41 — a wizard-flagged top-level scalar must not be dropped."""

    def test_collect_defaults_emits_top_level_scalar_under_root(self, monkeypatch):
        from pydantic import BaseModel, Field

        import tools.generate_wizard_defaults as gen

        class _Inner(BaseModel):
            nested: int = Field(default=8, json_schema_extra={"wizard": True})

        class _Root(BaseModel):
            seed: int = Field(default=42, json_schema_extra={"wizard": True})
            lora: _Inner = Field(default_factory=_Inner)

        monkeypatch.setattr(gen, "ForgeConfig", _Root)
        out = gen.collect_defaults()
        # Pre-fix the flagged top-level ``seed`` scalar silently vanished.
        assert out.get("root") == {"seed": 42}
        assert out.get("lora") == {"nested": 8}

    def test_walk_model_raises_on_flag_misplaced_on_submodel_field(self):
        from typing import Optional

        from pydantic import BaseModel, Field

        from tools.generate_wizard_defaults import _walk_model

        class _Inner(BaseModel):
            r: int = Field(default=8, description="x")

        class _Root(BaseModel):
            # Wizard flag wrongly placed on the submodel-typed field itself.
            lora: Optional[_Inner] = Field(default=None, json_schema_extra={"wizard": True})

        with pytest.raises(ValueError, match="submodel-typed field"):
            _walk_model(_Root, "root", {})


class TestGeneratorPydanticUndefinedIdentity:
    """B2 — required fields detected via ``is PydanticUndefined``, not repr()."""

    def test_required_wizard_flagged_field_skipped(self):
        from pydantic import BaseModel, Field

        from tools.generate_wizard_defaults import _walk_model

        class _Schema(BaseModel):
            # No default — Pydantic stores PydanticUndefined as the sentinel.
            required_thing: str = Field(json_schema_extra={"wizard": True})

        sink = {}
        _walk_model(_Schema, "test", sink)
        # Required fields must not appear in the output (no default to
        # serialise); the walk should skip them via identity check.
        assert sink == {}, "Required field with no default leaked into output"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
