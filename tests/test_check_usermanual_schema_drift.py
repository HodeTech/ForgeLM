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
a top-level block). The guard's introduction-time backlog (38 fabricated
key-path instances across 8 EN/TR page-pairs) has since been cleaned, and
``.github/workflows/ci.yml`` now runs it with ``--strict`` — so a
real-corpus test here also pins the zero-drift invariant CI relies on by
calling ``main(["--strict", "--quiet"])`` and asserting exit ``0``,
not just that the walk doesn't crash.
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
    metadata: Dict[str, Any] = Field(default={}, description="opaque top-level map")
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
        assert set(top.keys()) == {"model", "training", "data", "leaf", "pipeline", "metadata"}

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

    def test_unrecognised_top_level_key_is_not_a_nested_key_drift(self, tool, synthetic_schema):
        # check_snippet only walks the keys *inside* recognised ForgeConfig
        # blocks; an unrecognised top-level block is reported by
        # unknown_top_level_keys / scan (see TestUnknownTopLevelBlocks).
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


class TestUnknownTopLevelBlocks:
    """CR-A17a-007: a fragment may not use a top-level key ForgeConfig rejects."""

    @staticmethod
    def _snippet(tool, body: str):
        return tool.Snippet(path=Path("doc.md"), line_start=1, body=body)

    @staticmethod
    def _write(root: Path, lang: str, page: str, body: str) -> None:
        path = root / lang / page
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# Page\n\n```yaml\n{body}```\n", encoding="utf-8")

    def test_fabricated_top_level_block_is_reported(self, tool, synthetic_schema):
        top = synthetic_schema["ForgeConfig"]
        keys = tool.unknown_top_level_keys(self._snippet(tool, "chat:\n  default_temperature: 0.7\n"), top)
        assert keys == frozenset({"chat"})

    def test_known_blocks_and_nested_drift_are_not_unknown_blocks(self, tool, synthetic_schema):
        top = synthetic_schema["ForgeConfig"]
        assert tool.unknown_top_level_keys(self._snippet(tool, "training:\n  bogus: 1\n"), top) == frozenset()

    def test_mixed_snippet_reports_only_the_unknown_keys(self, tool, synthetic_schema):
        top = synthetic_schema["ForgeConfig"]
        body = "training:\n  learning_rate: 0.1\noutput:\n  cost_tracking: {}\n"
        assert tool.unknown_top_level_keys(self._snippet(tool, body), top) == frozenset({"output"})

    def test_full_config_and_unparseable_and_non_mapping_snippets_are_skipped(self, tool, synthetic_schema):
        top = synthetic_schema["ForgeConfig"]
        full = "model:\n  name_or_path: x\ntraining: {}\ndata:\n  dataset_name_or_path: y\nchat: {}\n"
        assert tool.unknown_top_level_keys(self._snippet(tool, full), top) == frozenset()
        assert tool.unknown_top_level_keys(self._snippet(tool, "chat:\n  - [unbalanced\n"), top) == frozenset()
        assert tool.unknown_top_level_keys(self._snippet(tool, "- chat\n- output\n"), top) == frozenset()

    def test_scan_flags_an_unlisted_block_in_both_languages(self, tool, synthetic_schema, tmp_path):
        for lang in ("en", "tr"):
            self._write(tmp_path, lang, "deployment/chat.md", "chat:\n  default_temperature: 0.7\n")
        result = tool.scan(tmp_path, synthetic_schema, synthetic_schema["ForgeConfig"], {}, {})
        assert [(u.path.relative_to(tmp_path).as_posix(), sorted(u.keys)) for u in result.unknown_blocks] == [
            ("en/deployment/chat.md", ["chat"]),
            ("tr/deployment/chat.md", ["chat"]),
        ]
        assert not result.clean

    def test_a_listed_block_passes_in_every_language_and_survives_a_line_shift(self, tool, synthetic_schema, tmp_path):
        rows = {("operations/cicd.md", frozenset({"jobs", "name"})): "GitHub Actions workflow"}
        self._write(tmp_path, "en", "operations/cicd.md", "name: ci\njobs: {}\n")
        (tmp_path / "tr" / "operations").mkdir(parents=True)
        (tmp_path / "tr" / "operations" / "cicd.md").write_text(
            "Intro line\n\nMore text\n\n```yaml\nname: ci\njobs: {}\n```\n", encoding="utf-8"
        )
        result = tool.scan(tmp_path, synthetic_schema, synthetic_schema["ForgeConfig"], rows, {})
        assert result.clean

    def test_known_open_rows_behave_like_non_forgelm_rows(self, tool, synthetic_schema, tmp_path):
        rows = {("operations/troubleshooting.md", frozenset({"audit"})): "tracked elsewhere"}
        for lang in ("en", "tr"):
            self._write(tmp_path, lang, "operations/troubleshooting.md", "audit:\n  quality_filter: {}\n")
        result = tool.scan(tmp_path, synthetic_schema, synthetic_schema["ForgeConfig"], {}, rows)
        assert result.clean

    def test_a_row_must_match_the_exact_key_set(self, tool, synthetic_schema, tmp_path):
        rows = {("operations/cicd.md", frozenset({"jobs"})): "GitHub Actions workflow"}
        for lang in ("en", "tr"):
            self._write(tmp_path, lang, "operations/cicd.md", "jobs: {}\nchat: {}\n")
        result = tool.scan(tmp_path, synthetic_schema, synthetic_schema["ForgeConfig"], rows, {})
        assert [sorted(u.keys) for u in result.unknown_blocks] == [["chat", "jobs"], ["chat", "jobs"]]
        assert [(row.page, row.keys) for row in result.stale_rows] == [("operations/cicd.md", frozenset({"jobs"}))]

    def test_a_row_that_matches_no_snippet_is_stale(self, tool, synthetic_schema, tmp_path):
        rows = {("operations/gpu-cost.md", frozenset({"gpu_profiles"})): "tracked elsewhere"}
        for lang in ("en", "tr"):
            self._write(tmp_path, lang, "operations/gpu-cost.md", "training:\n  learning_rate: 0.1\n")
        result = tool.scan(tmp_path, synthetic_schema, synthetic_schema["ForgeConfig"], {}, rows)
        assert [(row.table, row.page, row.keys) for row in result.stale_rows] == [
            ("_KNOWN_OPEN", "operations/gpu-cost.md", frozenset({"gpu_profiles"}))
        ]
        assert not result.clean

    def test_a_row_matched_in_only_one_language_is_stale(self, tool, synthetic_schema, tmp_path):
        rows = {("operations/gpu-cost.md", frozenset({"gpu_profiles"})): "tracked elsewhere"}
        self._write(tmp_path, "en", "operations/gpu-cost.md", "gpu_profiles:\n  custom: []\n")
        self._write(tmp_path, "tr", "operations/gpu-cost.md", "training:\n  learning_rate: 0.1\n")
        result = tool.scan(tmp_path, synthetic_schema, synthetic_schema["ForgeConfig"], {}, rows)
        assert len(result.stale_rows) == 1
        stale = result.stale_rows[0]
        assert (stale.matched_in, stale.missing_in) == (("en",), ("tr",))

    def test_known_blocks_inside_mixed_or_exempted_snippets_are_still_walked(self, tool, synthetic_schema, tmp_path):
        rows = {("operations/cicd.md", frozenset({"chat"})): "tracked elsewhere"}
        for lang in ("en", "tr"):
            self._write(tmp_path, lang, "operations/cicd.md", "training:\n  bogus: 1\nchat: {}\n")
        result = tool.scan(tmp_path, synthetic_schema, synthetic_schema["ForgeConfig"], rows, {})
        assert sorted({d.key_path for d in result.drifts}) == ["training.bogus"]
        assert result.unknown_blocks == [] and result.stale_rows == []
        # Without the row, the same snippet reports BOTH the nested drift and the unknown block.
        bare = tool.scan(tmp_path, synthetic_schema, synthetic_schema["ForgeConfig"], {}, {})
        assert len(bare.drifts) == 2 and len(bare.unknown_blocks) == 2

    def test_each_row_is_matched_on_its_own_not_per_page(self, tool, synthetic_schema, tmp_path):
        rows = {
            ("ops/a.md", frozenset({"jobs"})): "GitHub Actions",
            ("ops/a.md", frozenset({"concurrency"})): "GitHub Actions concurrency",
        }
        for lang in ("en", "tr"):
            self._write(tmp_path, lang, "ops/a.md", "jobs: {}\n")
        result = tool.scan(tmp_path, synthetic_schema, synthetic_schema["ForgeConfig"], rows, {})
        assert [(row.page, row.keys) for row in result.stale_rows] == [("ops/a.md", frozenset({"concurrency"}))]

    def test_key_matching_is_case_sensitive(self, tool, synthetic_schema):
        top = synthetic_schema["ForgeConfig"]
        assert tool.unknown_top_level_keys(self._snippet(tool, "Training:\n  bogus: 1\n"), top) == frozenset(
            {"Training"}
        )

    def test_every_document_of_a_multi_document_snippet_is_checked(self, tool, synthetic_schema):
        top = synthetic_schema["ForgeConfig"]
        body = "chat: {}\n---\noutput: {}\n---\ntraining:\n  bogus: 1\n"
        assert tool.unknown_top_level_keys(self._snippet(tool, body), top) == frozenset({"chat", "output"})
        assert [d.key_path for d in tool.check_snippet(self._snippet(tool, body), synthetic_schema, top)] == [
            "training.bogus"
        ]

    def test_an_empty_tree_cannot_pass_vacuously(self, tool, synthetic_schema, tmp_path, monkeypatch):
        rows = {("ops/a.md", frozenset({"jobs"})): "GitHub Actions"}
        empty = tool.scan(tmp_path, synthetic_schema, synthetic_schema["ForgeConfig"], rows, {})
        assert not empty.clean and empty.snippet_count == 0
        config_path = tmp_path / "config.py"
        config_path.write_text(_SYNTHETIC_CONFIG, encoding="utf-8")
        root = tmp_path / "usermanuals"
        root.mkdir()
        monkeypatch.setattr(tool, "CONFIG_PATH", config_path)
        monkeypatch.setattr(tool, "USERMANUAL_ROOT", root)
        assert tool.main(["--strict", "--quiet"]) == 1

    def test_a_stray_top_level_file_is_not_a_language_directory(self, tool, synthetic_schema, tmp_path):
        rows = {("ops/a.md", frozenset({"jobs"})): "GitHub Actions"}
        for lang in ("en", "tr"):
            self._write(tmp_path, lang, "ops/a.md", "jobs: {}\n")
        (tmp_path / "README.md").write_text("```yaml\njobs: {}\n```\n", encoding="utf-8")
        assert tool.scan(tmp_path, synthetic_schema, synthetic_schema["ForgeConfig"], rows, {}).clean

    def test_a_partial_language_directory_names_the_missing_language(self, tool, synthetic_schema, tmp_path, capsys):
        rows = {("ops/a.md", frozenset({"jobs"})): "GitHub Actions"}
        for lang in ("en", "tr"):
            self._write(tmp_path, lang, "ops/a.md", "jobs: {}\n")
        self._write(tmp_path, "de", "ops/b.md", "training:\n  learning_rate: 0.1\n")
        result = tool.scan(tmp_path, synthetic_schema, synthetic_schema["ForgeConfig"], rows, {})
        assert [(row.missing_in, row.matched_in) for row in result.stale_rows] == [(("de",), ("en", "tr"))]
        tool._report(result)
        assert "unmatched in de but matched in en, tr" in capsys.readouterr().out

    def test_main_fails_strict_when_only_a_stale_row_remains(
        self, tool, synthetic_schema, tmp_path, monkeypatch, capsys
    ):
        config_path = tmp_path / "config.py"
        config_path.write_text(_SYNTHETIC_CONFIG, encoding="utf-8")
        root = tmp_path / "usermanuals"
        for lang in ("en", "tr"):
            self._write(root, lang, "ops/a.md", "training:\n  learning_rate: 0.1\n")
        monkeypatch.setattr(tool, "CONFIG_PATH", config_path)
        monkeypatch.setattr(tool, "USERMANUAL_ROOT", root)
        monkeypatch.setattr(tool, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(tool, "_NON_FORGELM_YAML", {})
        monkeypatch.setattr(tool, "_KNOWN_OPEN", {("ops/a.md", frozenset({"gone"})): "was fixed"})
        assert tool.main(["--strict"]) == 1
        out = capsys.readouterr().out
        assert "_KNOWN_OPEN: ops/a.md  gone" in out and "delete the row" in out
        assert tool.main([]) == 0

    def test_an_opaque_top_level_field_is_not_an_unknown_block(self, tool, synthetic_schema):
        top = synthetic_schema["ForgeConfig"]
        snippet = self._snippet(tool, "metadata:\n  anything: 1\n")
        assert tool.unknown_top_level_keys(snippet, top) == frozenset()
        assert tool.check_snippet(snippet, synthetic_schema, top) == []

    def test_a_row_with_extra_keys_does_not_exempt_a_trimmed_snippet(self, tool, synthetic_schema, tmp_path):
        rows = {("ops/a.md", frozenset({"jobs", "name"})): "GitHub Actions"}
        for lang in ("en", "tr"):
            self._write(tmp_path, lang, "ops/a.md", "jobs: {}\n")
        result = tool.scan(tmp_path, synthetic_schema, synthetic_schema["ForgeConfig"], rows, {})
        assert [sorted(u.keys) for u in result.unknown_blocks] == [["jobs"], ["jobs"]]
        assert [(row.page, row.keys) for row in result.stale_rows] == [("ops/a.md", frozenset({"jobs", "name"}))]

    def test_a_page_directly_under_a_language_directory_is_scanned(self, tool, synthetic_schema, tmp_path):
        for lang in ("en", "tr"):
            self._write(tmp_path, lang, "flat.md", "chat: {}\n")
        result = tool.scan(tmp_path, synthetic_schema, synthetic_schema["ForgeConfig"], {}, {})
        assert [u.path.relative_to(tmp_path).as_posix() for u in result.unknown_blocks] == ["en/flat.md", "tr/flat.md"]

    def test_main_strict_fails_on_a_nested_drift_alone_and_names_file_line_and_key(
        self, tool, tmp_path, monkeypatch, capsys
    ):
        config_path = tmp_path / "config.py"
        config_path.write_text(_SYNTHETIC_CONFIG, encoding="utf-8")
        root = tmp_path / "usermanuals"
        for lang in ("en", "tr"):
            self._write(root, lang, "ops/a.md", "training:\n  bogus: 1\n")
        monkeypatch.setattr(tool, "CONFIG_PATH", config_path)
        monkeypatch.setattr(tool, "USERMANUAL_ROOT", root)
        monkeypatch.setattr(tool, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(tool, "_NON_FORGELM_YAML", {})
        monkeypatch.setattr(tool, "_KNOWN_OPEN", {})
        assert tool.main(["--strict"]) == 1
        out = capsys.readouterr().out
        assert "usermanuals/en/ops/a.md:3  training.bogus  (no such field on the ForgeConfig schema)" in out
        assert tool.main([]) == 0

    def test_an_empty_tree_fails_in_both_modes_even_with_empty_tables(self, tool, tmp_path, monkeypatch, capsys):
        config_path = tmp_path / "config.py"
        config_path.write_text(_SYNTHETIC_CONFIG, encoding="utf-8")
        root = tmp_path / "usermanuals"
        root.mkdir()
        monkeypatch.setattr(tool, "CONFIG_PATH", config_path)
        monkeypatch.setattr(tool, "USERMANUAL_ROOT", root)
        monkeypatch.setattr(tool, "_NON_FORGELM_YAML", {})
        monkeypatch.setattr(tool, "_KNOWN_OPEN", {})
        assert tool.main(["--strict"]) == 1
        assert "no yaml blocks found" in capsys.readouterr().err
        assert tool.main([]) == 1

    def test_main_strict_exits_one_on_an_unlisted_block_and_zero_in_advisory_mode(
        self, tool, synthetic_schema, tmp_path, monkeypatch, capsys
    ):
        config_path = tmp_path / "config.py"
        config_path.write_text(_SYNTHETIC_CONFIG, encoding="utf-8")
        root = tmp_path / "usermanuals"
        for lang in ("en", "tr"):
            self._write(root, lang, "deployment/chat.md", "chat:\n  default_temperature: 0.7\n")
        monkeypatch.setattr(tool, "CONFIG_PATH", config_path)
        monkeypatch.setattr(tool, "USERMANUAL_ROOT", root)
        monkeypatch.setattr(tool, "REPO_ROOT", tmp_path)
        assert tool.main(["--strict"]) == 1
        out = capsys.readouterr().out
        assert "usermanuals/en/deployment/chat.md:3  chat" in out and "_NON_FORGELM_YAML" in out
        assert tool.main([]) == 0

    def test_every_real_exemption_row_is_matched_in_every_language(self, tool):
        schema = tool.build_schema_map(tool.CONFIG_PATH)
        result = tool.scan(
            tool.USERMANUAL_ROOT, schema, schema["ForgeConfig"], tool._NON_FORGELM_YAML, tool._KNOWN_OPEN
        )
        assert result.stale_rows == [] and result.unknown_blocks == []

    def test_real_exemption_tables_carry_a_reason_and_do_not_overlap(self, tool):
        for table in (tool._NON_FORGELM_YAML, tool._KNOWN_OPEN):
            assert all(reason.strip() for reason in table.values())
        assert not set(tool._NON_FORGELM_YAML) & set(tool._KNOWN_OPEN)
        # A fabricated block must never be filed as "another tool's YAML".
        assert not any({"chat", "output"} & keys for _, keys in tool._NON_FORGELM_YAML)


class TestRealSchemaSmoke:
    """Regression-pins the AST walker's shape against the real schema, and
    separately pins the zero-drift invariant that CI enforces with
    ``--strict`` against the real ``docs/usermanuals/`` corpus (see module
    docstring)."""

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
        # Advisory mode (no --strict) always exits 0 regardless of drift; this
        # only proves the full walk + report path executes cleanly end-to-end.
        assert tool.main(["--quiet"]) == 0

    def test_main_strict_finds_zero_drift_in_the_real_corpus(self, tool):
        # This is the invariant CI actually relies on: ci.yml's validate job
        # runs this guard with --strict, which exits 1 on any fabricated
        # schema key path. Calling main() the same way here means a pytest
        # run alone — not just the raw CI shell step — fails if new drift
        # creeps into docs/usermanuals/.
        assert tool.main(["--strict", "--quiet"]) == 0

    def test_guard_wired_into_ci(self):
        ci = (_REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        assert "check_usermanual_schema_drift.py" in ci
