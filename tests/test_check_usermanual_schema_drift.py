"""Tests for tools/check_usermanual_schema_drift.py.

The guard AST-walks ``forgelm/config.py``'s Pydantic schema and flags
fenced ``yaml`` fragments under ``docs/usermanuals/`` that use a key
which doesn't exist on the resolved model. These tests plant a small
synthetic config module on disk (mirroring the ``forgelm/config.py``
style: ``BaseModel`` subclasses, ``Field(...)``, ``Optional[X]``,
``List[X]``, ``Dict[str, Any]``, ``alias=``) so the resolution logic is
pinned independently of the real, evolving schema. A separate smoke
test exercises the AST walker against the real ``forgelm/config.py`` to
catch a schema-shape regression (e.g. ``ForgeConfig`` renamed or losing
a top-level block) without asserting anything about current doc content
— this guard ships advisory-only precisely because it already found
real (and out-of-scope-to-fix-here) drift in ``docs/usermanuals/``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TOOL_PATH = _REPO_ROOT / "tools" / "check_usermanual_schema_drift.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("check_usermanual_schema_drift", _TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_usermanual_schema_drift"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture(scope="module")
def tool():
    return _load_tool()


_SYNTHETIC_CONFIG = """
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field


class LeafConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="leaf name")
    tags: List[str] = Field(default=[], description="opaque list of scalars")
    extra: Dict[str, Any] = Field(default={}, description="opaque free-form map")


class StageConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="stage id")
    leaf: Optional[LeafConfig] = Field(default=None, description="nested block")


class PipelineConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stages: List[StageConfig] = Field(default=[], description="ordered stages")


class TrainingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    learning_rate: float = Field(default=2e-4, description="lr")
    max_completion_length: int = Field(
        default=512, alias="max_new_tokens", description="aliased field"
    )
    mode: Literal["a", "b"] = Field(default="a", description="opaque literal")


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name_or_path: str = Field(description="model path")


class DataConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_name_or_path: str = Field(description="dataset path")


class ForgeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: ModelConfig = Field(description="model block")
    training: TrainingConfig = Field(description="training block")
    data: DataConfig = Field(description="data block")
    leaf: Optional[LeafConfig] = Field(default=None, description="top-level leaf block")
    pipeline: Optional[PipelineConfig] = Field(default=None, description="pipeline block")
"""


@pytest.fixture(scope="module")
def synthetic_schema(tool, tmp_path_factory):
    config_dir = tmp_path_factory.mktemp("schema")
    config_path = config_dir / "config.py"
    config_path.write_text(_SYNTHETIC_CONFIG, encoding="utf-8")
    return tool.build_schema_map(config_path)


class TestResolveAnnotation:
    def test_bare_name_matching_known_class(self, tool, synthetic_schema):
        pydantic_classes = set(synthetic_schema.keys())
        node = tool.ast.parse("x: LeafConfig").body[0].annotation
        result = tool._resolve_annotation(node, pydantic_classes)
        assert result.nested_class == "LeafConfig"
        assert result.is_list is False

    def test_optional_unwraps_to_inner_class(self, tool, synthetic_schema):
        pydantic_classes = set(synthetic_schema.keys())
        node = tool.ast.parse("x: Optional[LeafConfig]").body[0].annotation
        result = tool._resolve_annotation(node, pydantic_classes)
        assert result.nested_class == "LeafConfig"

    def test_list_of_known_class_sets_is_list(self, tool, synthetic_schema):
        pydantic_classes = set(synthetic_schema.keys())
        node = tool.ast.parse("x: List[StageConfig]").body[0].annotation
        result = tool._resolve_annotation(node, pydantic_classes)
        assert result.nested_class == "StageConfig"
        assert result.is_list is True

    def test_dict_str_any_is_opaque(self, tool, synthetic_schema):
        pydantic_classes = set(synthetic_schema.keys())
        node = tool.ast.parse("x: Dict[str, Any]").body[0].annotation
        result = tool._resolve_annotation(node, pydantic_classes)
        assert result.nested_class is None

    def test_list_of_scalars_is_opaque(self, tool, synthetic_schema):
        pydantic_classes = set(synthetic_schema.keys())
        node = tool.ast.parse("x: List[str]").body[0].annotation
        result = tool._resolve_annotation(node, pydantic_classes)
        assert result.nested_class is None

    def test_literal_is_opaque(self, tool, synthetic_schema):
        pydantic_classes = set(synthetic_schema.keys())
        node = tool.ast.parse('x: Literal["a", "b"]').body[0].annotation
        result = tool._resolve_annotation(node, pydantic_classes)
        assert result.nested_class is None

    def test_forward_ref_string_resolves(self, tool, synthetic_schema):
        pydantic_classes = set(synthetic_schema.keys())
        node = tool.ast.parse('x: Optional["PipelineConfig"]').body[0].annotation
        result = tool._resolve_annotation(node, pydantic_classes)
        assert result.nested_class == "PipelineConfig"


class TestBuildSchemaMap:
    def test_forge_config_top_level_matches_declared_blocks(self, synthetic_schema):
        top = synthetic_schema["ForgeConfig"]
        assert set(top.keys()) == {"model", "training", "data", "leaf", "pipeline"}

    def test_alias_indexed_alongside_field_name(self, synthetic_schema):
        training = synthetic_schema["TrainingConfig"]
        assert "max_completion_length" in training
        assert "max_new_tokens" in training  # the Field(alias=...) value
        assert training["max_new_tokens"].nested_class is None  # scalar, not a nested model

    def test_opaque_fields_have_no_nested_class(self, synthetic_schema):
        leaf = synthetic_schema["LeafConfig"]
        assert leaf["tags"].nested_class is None
        assert leaf["extra"].nested_class is None


class TestCheckSnippet:
    def _snippet(self, tool, body: str, path=Path("doc.md"), line=1):
        return tool.Snippet(path=path, line_start=line, body=body)

    def test_fabricated_key_is_flagged(self, tool, synthetic_schema):
        top = synthetic_schema["ForgeConfig"]
        snippet = self._snippet(tool, "training:\n  bogus_field: 1\n")
        drifts = tool.check_snippet(snippet, synthetic_schema, top)
        assert len(drifts) == 1
        assert drifts[0].key_path == "training.bogus_field"

    def test_valid_keys_pass_clean(self, tool, synthetic_schema):
        top = synthetic_schema["ForgeConfig"]
        snippet = self._snippet(tool, "training:\n  learning_rate: 0.001\n  mode: a\n")
        assert tool.check_snippet(snippet, synthetic_schema, top) == []

    def test_aliased_key_is_recognised(self, tool, synthetic_schema):
        top = synthetic_schema["ForgeConfig"]
        snippet = self._snippet(tool, "training:\n  max_new_tokens: 256\n")
        assert tool.check_snippet(snippet, synthetic_schema, top) == []

    def test_opaque_dict_field_is_never_descended(self, tool, synthetic_schema):
        # `leaf.extra` is Dict[str, Any] — arbitrary sub-keys must never
        # be flagged even though they aren't real schema fields anywhere.
        top = synthetic_schema["ForgeConfig"]
        snippet = self._snippet(tool, "leaf:\n  name: x\n  extra:\n    anything_goes: 1\n")
        assert tool.check_snippet(snippet, synthetic_schema, top) == []

    def test_full_triplet_snippet_is_skipped(self, tool, synthetic_schema):
        # model + training + data all present -> deferred to
        # check_yaml_snippets.py's real Pydantic validation; a fabricated
        # key here must NOT be double-reported by this guard.
        top = synthetic_schema["ForgeConfig"]
        snippet = self._snippet(
            tool,
            "model:\n  name_or_path: x\ntraining:\n  bogus_field: 1\ndata:\n  dataset_name_or_path: y\n",
        )
        assert tool.check_snippet(snippet, synthetic_schema, top) == []

    def test_unrecognised_top_level_key_is_out_of_scope(self, tool, synthetic_schema):
        # A yaml block unrelated to ForgeConfig (e.g. a docker-compose or
        # deepspeed example) must not be flagged just because its keys
        # happen not to match anything.
        top = synthetic_schema["ForgeConfig"]
        snippet = self._snippet(tool, "services:\n  train:\n    image: x\n")
        assert tool.check_snippet(snippet, synthetic_schema, top) == []

    def test_list_of_nested_model_walks_each_item(self, tool, synthetic_schema):
        top = synthetic_schema["ForgeConfig"]
        snippet = self._snippet(
            tool,
            "pipeline:\n  stages:\n    - id: s1\n      bogus: 1\n    - id: s2\n",
        )
        drifts = tool.check_snippet(snippet, synthetic_schema, top)
        assert len(drifts) == 1
        assert drifts[0].key_path == "pipeline.stages[0].bogus"

    def test_unparseable_yaml_is_not_this_guards_concern(self, tool, synthetic_schema):
        top = synthetic_schema["ForgeConfig"]
        snippet = self._snippet(tool, "training:\n  - [unbalanced\n")
        assert tool.check_snippet(snippet, synthetic_schema, top) == []


class TestRealSchemaSmoke:
    """Regression-pins the AST walker's shape against the real schema
    without asserting anything about current doc content (which this
    guard ships advisory-only for — see module docstring)."""

    def test_forge_config_resolves_with_known_top_level_blocks(self, tool):
        schema = tool.build_schema_map(tool.CONFIG_PATH)
        top = schema["ForgeConfig"]
        expected = {
            "model",
            "lora",
            "training",
            "data",
            "auth",
            "evaluation",
            "webhook",
            "distributed",
            "merge",
            "compliance",
            "risk_assessment",
            "monitoring",
            "synthetic",
            "retention",
            "pipeline",
        }
        assert expected.issubset(top.keys())

    def test_main_runs_against_the_real_repo_without_crashing(self, tool):
        # Advisory mode (no --strict) always exits 0; this only proves the
        # full walk + report path executes cleanly end-to-end.
        assert tool.main(["--quiet"]) == 0

    def test_guard_wired_into_ci(self):
        ci = (_REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        assert "check_usermanual_schema_drift.py" in ci
