"""Tests for tools/check_yaml_snippets.py (H11 / F-P8-C-07).

This guard validates every ForgeConfig-shaped YAML snippet in docs/ against the
Pydantic schema. It was unwired despite catching real doc-vs-schema drift (the
``severity_thresholds: S1/S5/...`` snippet that this package fixed). These tests
pin the ForgeConfig sniff, the validator's accept/skip/fail behaviour, the
live-repo clean pass, and the CI wiring this package added.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TOOL_PATH = _REPO_ROOT / "tools" / "check_yaml_snippets.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("check_yaml_snippets", _TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    # Register before exec so the guard's dataclasses (Snippet) can resolve
    # ``cls.__module__`` during dataclass construction.
    sys.modules["check_yaml_snippets"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture(scope="module")
def tool():
    return _load_tool()


def _snippet(tool, body: str):
    return tool.Snippet(path=Path("doc.md"), line_start=1, body=body)


def test_looks_like_forgelm_config_requires_triplet(tool):
    assert tool.looks_like_forgelm_config({"model": {}, "training": {}, "data": {}})
    # missing the required triplet → fragment, not a full config
    assert not tool.looks_like_forgelm_config({"lora": {}})
    assert not tool.looks_like_forgelm_config("not a dict")


def test_valid_config_snippet_passes(tool):
    body = (
        'model:\n  name_or_path: "org/m"\n'
        "lora: {}\n"
        'training:\n  trainer_type: "sft"\n'
        'data:\n  dataset_name_or_path: "org/d"\n'
    )
    assert tool.validate_snippet(_snippet(tool, body)) is None


def test_invalid_config_snippet_fails(tool):
    # An unknown nested value rejected by the schema (extra=forbid / validator).
    body = (
        'model:\n  name_or_path: "org/m"\n'
        'training:\n  trainer_type: "not-a-real-trainer"\n'
        'data:\n  dataset_name_or_path: "org/d"\n'
    )
    failure = tool.validate_snippet(_snippet(tool, body))
    assert failure is not None
    assert "model" in failure.reason or "trainer" in failure.reason.lower()


def test_fragment_snippet_is_skipped(tool):
    # No model/training/data triplet → illustrative fragment, not validated.
    assert tool.validate_snippet(_snippet(tool, "lora:\n  r: 16\n")) is None


def test_invalid_marker_opts_out(tool):
    body = "# INVALID: shows a bad example\nmodel:\n  name_or_path: 123\ntraining: {}\ndata: {}\n"
    assert tool.validate_snippet(_snippet(tool, body)) is None


def test_real_repo_is_clean(tool):
    assert tool.main(["--quiet"]) == 0


def test_guard_wired_into_ci():
    ci = (_REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "check_yaml_snippets.py" in ci
