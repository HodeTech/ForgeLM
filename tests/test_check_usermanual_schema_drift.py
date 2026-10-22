"""Tests for tools/check_usermanual_schema_drift.py.

The guard AST-walks ``forgelm/config.py``'s Pydantic schema and checks every
fenced ``yaml`` block under ``docs/usermanuals/``: the block must be readable,
its top-level keys must be ``ForgeConfig`` blocks (or the block is listed as
another tool's YAML or a known-open block), and a fragment's nested keys must
be declared fields.  These tests plant a small synthetic config module on disk
(mirroring the ``forgelm/config.py`` style: ``BaseModel`` subclasses,
``Field(...)``, ``Optional[X]``, ``List[X]``, ``Dict[str, Any]``, ``alias=``)
and synthetic manual trees under ``tmp_path``, so each rule is pinned
independently of the real, evolving schema and pages.  A separate class runs
the guard against the real ``forgelm/config.py`` and ``docs/usermanuals/`` and
pins the invariant CI relies on: ``main(["--strict", "--quiet"])`` exits ``0``.
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


def _snippet(tool, body: str, path=Path("doc.md"), line=1):
    return tool.Snippet(path=path, line_start=line, body=body)


def _write(root: Path, lang: str, page: str, body: str) -> None:
    """Write a page whose only YAML block opens on line 3 and holds ``body``."""
    path = root / lang / page
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# Page\n\n```yaml\n{body}```\n", encoding="utf-8")


def _write_raw(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _scan(tool, schema, root, non_forgelm=None, known_open=None):
    return tool.scan(root, schema, schema["ForgeConfig"], non_forgelm or {}, known_open or {})


@pytest.fixture
def synthetic_main(tool, tmp_path, monkeypatch):
    """Point ``main()`` at a synthetic schema and an empty manual tree with empty tables."""
    config_path = tmp_path / "config.py"
    config_path.write_text(_SYNTHETIC_CONFIG, encoding="utf-8")
    root = tmp_path / "usermanuals"
    root.mkdir()
    monkeypatch.setattr(tool, "CONFIG_PATH", config_path)
    monkeypatch.setattr(tool, "USERMANUAL_ROOT", root)
    monkeypatch.setattr(tool, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tool, "_NON_FORGELM_YAML", {})
    monkeypatch.setattr(tool, "_KNOWN_OPEN", {})
    return root


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
    def test_fabricated_key_is_flagged(self, tool, synthetic_schema):
        top = synthetic_schema["ForgeConfig"]
        drifts = tool.check_snippet(_snippet(tool, "training:\n  bogus_field: 1\n"), synthetic_schema, top)
        assert len(drifts) == 1
        assert drifts[0].key_path == "training.bogus_field"

    def test_valid_keys_pass_clean(self, tool, synthetic_schema):
        top = synthetic_schema["ForgeConfig"]
        snippet = _snippet(tool, "training:\n  learning_rate: 0.001\n  mode: a\n")
        assert tool.check_snippet(snippet, synthetic_schema, top) == []

    def test_aliased_key_is_recognised(self, tool, synthetic_schema):
        top = synthetic_schema["ForgeConfig"]
        assert tool.check_snippet(_snippet(tool, "training:\n  max_new_tokens: 256\n"), synthetic_schema, top) == []

    def test_opaque_dict_field_is_never_descended(self, tool, synthetic_schema):
        # `leaf.extra` is Dict[str, Any] — arbitrary sub-keys must never
        # be flagged even though they aren't real schema fields anywhere.
        top = synthetic_schema["ForgeConfig"]
        snippet = _snippet(tool, "leaf:\n  name: x\n  extra:\n    anything_goes: 1\n")
        assert tool.check_snippet(snippet, synthetic_schema, top) == []

    def test_a_full_configs_nested_keys_are_left_to_check_yaml_snippets(self, tool, synthetic_schema):
        # model + training + data -> check_yaml_snippets.py validates the nested
        # keys with real Pydantic; this guard must not double-report them.
        top = synthetic_schema["ForgeConfig"]
        snippet = _snippet(
            tool, "model:\n  name_or_path: x\ntraining:\n  bogus_field: 1\ndata:\n  dataset_name_or_path: y\n"
        )
        assert tool.check_snippet(snippet, synthetic_schema, top) == []

    def test_unrecognised_top_level_key_is_not_a_nested_key_drift(self, tool, synthetic_schema):
        # An unrecognised top-level block is unknown_top_level_keys' concern.
        top = synthetic_schema["ForgeConfig"]
        assert tool.check_snippet(_snippet(tool, "services:\n  train:\n    image: x\n"), synthetic_schema, top) == []

    def test_list_of_nested_model_walks_each_item(self, tool, synthetic_schema):
        top = synthetic_schema["ForgeConfig"]
        snippet = _snippet(tool, "pipeline:\n  stages:\n    - id: s1\n      bogus: 1\n    - id: s2\n")
        assert [d.key_path for d in tool.check_snippet(snippet, synthetic_schema, top)] == ["pipeline.stages[0].bogus"]

    def test_a_non_string_nested_key_is_drift(self, tool, synthetic_schema):
        top = synthetic_schema["ForgeConfig"]
        snippet = _snippet(tool, "training:\n  2: x\n  true: y\n  on: z\n")
        assert sorted(d.key_path for d in tool.check_snippet(snippet, synthetic_schema, top)) == [
            "training.<bool: True>",
            "training.<int: 2>",
            "training.on",
        ]

    def test_an_unreadable_snippet_yields_no_drift_here_because_scan_reports_it(self, tool, synthetic_schema):
        top = synthetic_schema["ForgeConfig"]
        assert tool.check_snippet(_snippet(tool, "training:\n  - [unbalanced\n"), synthetic_schema, top) == []


class TestYamlReading:
    def test_only_true_and_false_are_booleans(self, tool):
        documents = tool.load_documents("on: 1\nyes: 2\noff: 3\nno: 4\ntrue: 5\nFALSE: 6\n")
        assert documents == [{"on": 1, "yes": 2, "off": 3, "no": 4, True: 5, False: 6}]

    def test_the_yaml_1_2_loader_leaves_pyyaml_untouched(self, tool):
        tool.load_documents("on: 1\n")
        assert tool.yaml.safe_load("on: 1\n") == {True: 1}

    def test_a_duplicate_key_is_a_parse_error_at_any_depth(self, tool):
        with pytest.raises(tool.yaml.YAMLError, match="found duplicate key 'x'"):
            tool.load_documents("a:\n  x: 1\n  x: 2\n")
        with pytest.raises(tool.yaml.YAMLError, match="found duplicate key 'x'"):
            tool.load_documents("- x: 1\n  x: 2\n")

    def test_a_key_overriding_a_merge_is_not_a_duplicate(self, tool):
        documents = tool.load_documents("base: &b\n  x: 1\nchild:\n  <<: *b\n  x: 2\nalso: *b\n")
        assert documents == [{"base": {"x": 1}, "child": {"x": 2}, "also": {"x": 1}}]

    def test_every_document_is_read(self, tool):
        assert tool.load_documents("a: 1\n---\n- b\n---\n") == [{"a": 1}, ["b"], None]


class TestUnknownTopLevelBlocks:
    """A block may not use a top-level key ForgeConfig rejects."""

    def test_fabricated_top_level_block_is_reported(self, tool, synthetic_schema):
        top = synthetic_schema["ForgeConfig"]
        keys = tool.unknown_top_level_keys(_snippet(tool, "chat:\n  default_temperature: 0.7\n"), top)
        assert keys == frozenset({"chat"})

    def test_known_blocks_and_nested_drift_are_not_unknown_blocks(self, tool, synthetic_schema):
        top = synthetic_schema["ForgeConfig"]
        assert tool.unknown_top_level_keys(_snippet(tool, "training:\n  bogus: 1\n"), top) == frozenset()

    def test_mixed_snippet_reports_only_the_unknown_keys(self, tool, synthetic_schema):
        top = synthetic_schema["ForgeConfig"]
        body = "training:\n  learning_rate: 0.1\noutput:\n  cost_tracking: {}\n"
        assert tool.unknown_top_level_keys(_snippet(tool, body), top) == frozenset({"output"})

    def test_a_full_config_still_has_its_block_names_checked(self, tool, synthetic_schema):
        # check_yaml_snippets.py skips a full config marked "# INVALID:", so
        # nothing else reads the block names of such an overview.
        top = synthetic_schema["ForgeConfig"]
        overview = _snippet(tool, "# INVALID: overview\nmodel: {...}\ntraining: {...}\ndata: {...}\nchat: {...}\n")
        assert tool.unknown_top_level_keys(overview, top) == frozenset({"chat"})
        assert tool.check_snippet(overview, synthetic_schema, top) == []

    def test_a_list_document_is_named_by_its_items(self, tool, synthetic_schema):
        top = synthetic_schema["ForgeConfig"]
        steps = _snippet(tool, "- name: Train\n  run: forgelm\n- uses: actions/cache@v4\n- plain\n- [nested]\n")
        assert tool.unknown_top_level_keys(steps, top) == frozenset(
            {"- name", "- run", "- uses", "- <scalar>", "- <list>"}
        )

    def test_a_scalar_document_and_a_non_string_key_are_unknown(self, tool, synthetic_schema):
        top = synthetic_schema["ForgeConfig"]
        assert tool.unknown_top_level_keys(_snippet(tool, "just text\n"), top) == frozenset({"<scalar>"})
        body = "2: a\ntrue: b\nnull: c\ntraining: {}\n"
        assert tool.unknown_top_level_keys(_snippet(tool, body), top) == frozenset(
            {"<int: 2>", "<bool: True>", "<NoneType: None>"}
        )

    def test_keys_python_reads_as_equal_are_duplicates(self, tool):
        # 1 and true are one dict key in Python, so PyYAML would keep only the last value.
        with pytest.raises(tool.yaml.YAMLError, match="found duplicate key True"):
            tool.load_documents("1: a\ntrue: b\n")

    def test_github_actions_on_key_is_the_string_on(self, tool, synthetic_schema):
        top = synthetic_schema["ForgeConfig"]
        body = "name: ci\non:\n  push: {}\njobs: {}\n"
        assert tool.unknown_top_level_keys(_snippet(tool, body), top) == frozenset({"jobs", "name", "on"})

    def test_an_unreadable_or_empty_snippet_has_no_keys_here_because_scan_reports_it(self, tool, synthetic_schema):
        top = synthetic_schema["ForgeConfig"]
        assert tool.unknown_top_level_keys(_snippet(tool, "chat:\n  - [unbalanced\n"), top) == frozenset()
        assert tool.unknown_top_level_keys(_snippet(tool, "# only a comment\n"), top) == frozenset()
        assert tool.unknown_top_level_keys(_snippet(tool, "training: {}\n---\n"), top) == frozenset()

    def test_key_matching_is_case_sensitive(self, tool, synthetic_schema):
        top = synthetic_schema["ForgeConfig"]
        snippet = _snippet(tool, "Training:\n  bogus: 1\n")
        assert tool.unknown_top_level_keys(snippet, top) == frozenset({"Training"})

    def test_every_document_of_a_multi_document_snippet_is_checked(self, tool, synthetic_schema):
        top = synthetic_schema["ForgeConfig"]
        body = "chat: {}\n---\noutput: {}\n---\ntraining:\n  bogus: 1\n"
        assert tool.unknown_top_level_keys(_snippet(tool, body), top) == frozenset({"chat", "output"})
        assert [d.key_path for d in tool.check_snippet(_snippet(tool, body), synthetic_schema, top)] == [
            "training.bogus"
        ]

    def test_an_opaque_top_level_field_is_not_an_unknown_block(self, tool, synthetic_schema):
        top = synthetic_schema["ForgeConfig"]
        snippet = _snippet(tool, "metadata:\n  anything: 1\n")
        assert tool.unknown_top_level_keys(snippet, top) == frozenset()
        assert tool.check_snippet(snippet, synthetic_schema, top) == []

    def test_scan_flags_an_unlisted_block_in_both_languages(self, tool, synthetic_schema, tmp_path):
        for lang in ("en", "tr"):
            _write(tmp_path, lang, "deployment/chat.md", "chat:\n  default_temperature: 0.7\n")
        result = _scan(tool, synthetic_schema, tmp_path)
        assert [(u.path.relative_to(tmp_path).as_posix(), sorted(u.keys)) for u in result.unknown_blocks] == [
            ("en/deployment/chat.md", ["chat"]),
            ("tr/deployment/chat.md", ["chat"]),
        ]
        assert not result.clean

    def test_scan_flags_an_unknown_block_inside_a_full_config(self, tool, synthetic_schema, tmp_path):
        body = "# INVALID: overview\nmodel: {}\ntraining: {}\ndata: {}\nchat: {}\n"
        for lang in ("en", "tr"):
            _write(tmp_path, lang, "reference/configuration.md", body)
        result = _scan(tool, synthetic_schema, tmp_path)
        assert [(u.line, u.keys) for u in result.unknown_blocks] == [(3, frozenset({"chat"}))] * 2

    def test_a_page_directly_under_a_language_directory_is_scanned(self, tool, synthetic_schema, tmp_path):
        for lang in ("en", "tr"):
            _write(tmp_path, lang, "flat.md", "chat: {}\n")
        result = _scan(tool, synthetic_schema, tmp_path)
        assert [u.path.relative_to(tmp_path).as_posix() for u in result.unknown_blocks] == ["en/flat.md", "tr/flat.md"]


class TestUnreadableBlocks:
    @pytest.mark.parametrize(
        ("opener", "closer"),
        [
            ("```yml", "```"),
            ("~~~yaml", "~~~"),
            ("```YAML", "```"),
            ('```yaml title="config.yaml"', "```"),
            ("````yaml", "````"),
            ("```{.yaml}", "```"),
        ],
    )
    def test_a_yaml_fence_the_extractor_cannot_read_is_a_finding(
        self, tool, synthetic_schema, tmp_path, opener, closer
    ):
        for lang in ("en", "tr"):
            _write_raw(tmp_path, f"{lang}/ops/a.md", f"# Page\n\n{opener}\nchat: {{}}\n{closer}\n")
        result = _scan(tool, synthetic_schema, tmp_path)
        assert [(b.path.relative_to(tmp_path).as_posix(), b.line, b.problem) for b in result.unreadable_blocks] == [
            ("en/ops/a.md", 3, f"YAML fence written {opener!r}"),
            ("tr/ops/a.md", 3, f"YAML fence written {opener!r}"),
        ]
        assert result.snippet_count == 0 and not result.clean

    def test_a_yaml_fence_indented_under_a_list_item_is_a_finding(self, tool, synthetic_schema, tmp_path):
        page = "# Page\n\n1. Write the config:\n\n   ```yaml\n   chat: {}\n   ```\n"
        for lang in ("en", "tr"):
            _write_raw(tmp_path, f"{lang}/ops/a.md", page)
        result = _scan(tool, synthetic_schema, tmp_path)
        assert [(b.line, b.problem) for b in result.unreadable_blocks] == [(5, "YAML fence written '```yaml'")] * 2

    def test_an_unclosed_yaml_fence_is_a_finding(self, tool, synthetic_schema, tmp_path):
        for lang in ("en", "tr"):
            _write_raw(tmp_path, f"{lang}/ops/a.md", "# Page\n\n```yaml\nchat: {}\n")
        result = _scan(tool, synthetic_schema, tmp_path)
        assert [(b.line, b.problem, b.hint) for b in result.unreadable_blocks] == [
            (3, "YAML fence never closed", tool._UNCLOSED_HINT)
        ] * 2

    def test_fences_in_other_languages_are_never_findings(self, tool, synthetic_schema, tmp_path):
        page = (
            "# Page\n\n```text\n```yml\nchat: {}\n```\n\n"  # a YAML-looking line inside a text block is content
            "~~~shell\nforgelm --help\n~~~\n\n"
            "````markdown\n~~~yaml\nchat: {}\n~~~\n````\n\n"
            '```json\n{"chat": {}}\n```\n\n'
            "```\nplain\n```\n\n"
            "```yaml\ntraining:\n  learning_rate: 0.1\n```\n"
        )
        for lang in ("en", "tr"):
            _write_raw(tmp_path, f"{lang}/ops/a.md", page)
        result = _scan(tool, synthetic_schema, tmp_path)
        # Per language only the last block is a ```yaml block; the ~~~yaml one is markdown-example content.
        assert result.clean and result.snippet_count == result.walked_count == 2

    def test_a_line_that_starts_with_inline_code_is_not_a_fence(self, tool, synthetic_schema, tmp_path):
        # CommonMark: a backtick fence's info string holds no backtick, so this is prose.
        page = "# Page\n\n```yml``` fences are not read.\n\n```yaml\ntraining: {}\n```\n"
        for lang in ("en", "tr"):
            _write_raw(tmp_path, f"{lang}/ops/a.md", page)
        result = _scan(tool, synthetic_schema, tmp_path)
        assert result.clean and result.walked_count == 2

    def test_a_yaml_block_inside_a_markdown_example_is_still_read(self, tool, synthetic_schema, tmp_path):
        page = "# Page\n\n````markdown\n```yaml\nchat: {}\n```\n````\n"
        for lang in ("en", "tr"):
            _write_raw(tmp_path, f"{lang}/ops/a.md", page)
        result = _scan(tool, synthetic_schema, tmp_path)
        assert [(u.line, u.keys) for u in result.unknown_blocks] == [(4, frozenset({"chat"}))] * 2
        assert result.unreadable_blocks == []

    def test_a_block_that_does_not_parse_is_a_finding(self, tool, synthetic_schema, tmp_path):
        for lang in ("en", "tr"):
            _write(tmp_path, lang, "ops/a.md", "chat:\n  default_temperature: 0.7\n  ...\n")
        result = _scan(tool, synthetic_schema, tmp_path)
        assert [
            (b.path.relative_to(tmp_path).as_posix(), b.line, b.problem, b.hint) for b in result.unreadable_blocks
        ] == [
            (
                f"{lang}/ops/a.md",
                3,
                "while scanning a simple key: could not find expected ':' at line 6",
                tool._PARSE_HINT,
            )
            for lang in ("en", "tr")
        ]
        assert result.unknown_blocks == [] and not result.clean

    def test_a_duplicate_key_is_a_finding(self, tool, synthetic_schema, tmp_path):
        for lang in ("en", "tr"):
            _write(tmp_path, lang, "ops/a.md", "training:\n  learning_rate: 0.1\n  learning_rate: 0.2\n")
        result = _scan(tool, synthetic_schema, tmp_path)
        assert [(b.line, b.problem, b.hint) for b in result.unreadable_blocks] == [
            (3, "found duplicate key 'learning_rate' at line 6", tool._DUPLICATE_HINT)
        ] * 2

    def test_a_date_shaped_key_that_is_no_date_is_a_finding(self, tool, synthetic_schema, tmp_path):
        for lang in ("en", "tr"):
            _write(tmp_path, lang, "ops/a.md", "2024-13-01: x\n")
        result = _scan(tool, synthetic_schema, tmp_path)
        assert [(b.line, b.problem) for b in result.unreadable_blocks] == [(3, "month must be in 1..12")] * 2

    def test_an_exempted_block_that_stops_parsing_is_reported_unreadable_not_stale(
        self, tool, synthetic_schema, tmp_path
    ):
        # Deleting the row would hide the block from every later check, so the
        # unreadable block is the finding, not the row.
        rows = {("ops/cicd.md", frozenset({"concurrency"})): "GitHub Actions: concurrency block"}
        for lang in ("en", "tr"):
            _write(tmp_path, lang, "ops/cicd.md", "concurrency:\n  group: ci\n  ...\n")
        result = _scan(tool, synthetic_schema, tmp_path, non_forgelm=rows)
        assert result.stale_rows == []
        assert [b.path.relative_to(tmp_path).as_posix() for b in result.unreadable_blocks] == [
            "en/ops/cicd.md",
            "tr/ops/cicd.md",
        ]

    def test_scan_parses_each_block_once(self, tool, synthetic_schema, tmp_path, monkeypatch):
        bodies = []
        real = tool.load_documents
        monkeypatch.setattr(tool, "load_documents", lambda body: bodies.append(body) or real(body))
        for lang in ("en", "tr"):
            _write(tmp_path, lang, "ops/a.md", "training:\n  bogus: 1\nchat: {}\n")
        result = _scan(tool, synthetic_schema, tmp_path)
        assert len(result.drifts) == len(result.unknown_blocks) == 2
        assert len(bodies) == result.snippet_count == 2


class TestExemptionRows:
    def test_a_listed_block_passes_in_every_language_and_survives_a_line_shift(self, tool, synthetic_schema, tmp_path):
        rows = {("operations/cicd.md", frozenset({"jobs", "name"})): "GitHub Actions: workflow"}
        _write(tmp_path, "en", "operations/cicd.md", "name: ci\njobs: {}\n")
        _write_raw(tmp_path, "tr/operations/cicd.md", "Intro line\n\nMore text\n\n```yaml\nname: ci\njobs: {}\n```\n")
        result = _scan(tool, synthetic_schema, tmp_path, non_forgelm=rows)
        assert result.clean and result.exempted_count == 2

    def test_known_open_rows_exempt_like_non_forgelm_rows(self, tool, synthetic_schema, tmp_path):
        rows = {("operations/troubleshooting.md", frozenset({"audit"})): "#425: tracked"}
        for lang in ("en", "tr"):
            _write(tmp_path, lang, "operations/troubleshooting.md", "audit:\n  quality_filter: {}\n")
        assert _scan(tool, synthetic_schema, tmp_path, known_open=rows).clean

    def test_a_row_must_match_the_exact_key_set(self, tool, synthetic_schema, tmp_path):
        rows = {("operations/cicd.md", frozenset({"jobs"})): "GitHub Actions: workflow"}
        for lang in ("en", "tr"):
            _write(tmp_path, lang, "operations/cicd.md", "jobs: {}\nchat: {}\n")
        result = _scan(tool, synthetic_schema, tmp_path, non_forgelm=rows)
        assert [sorted(u.keys) for u in result.unknown_blocks] == [["chat", "jobs"], ["chat", "jobs"]]
        assert [(row.page, row.keys) for row in result.stale_rows] == [("operations/cicd.md", frozenset({"jobs"}))]

    def test_a_row_with_extra_keys_does_not_exempt_a_trimmed_snippet(self, tool, synthetic_schema, tmp_path):
        rows = {("ops/a.md", frozenset({"jobs", "name"})): "GitHub Actions: workflow"}
        for lang in ("en", "tr"):
            _write(tmp_path, lang, "ops/a.md", "jobs: {}\n")
        result = _scan(tool, synthetic_schema, tmp_path, non_forgelm=rows)
        assert [sorted(u.keys) for u in result.unknown_blocks] == [["jobs"], ["jobs"]]
        assert [(row.page, row.keys) for row in result.stale_rows] == [("ops/a.md", frozenset({"jobs", "name"}))]

    def test_a_row_only_exempts_its_own_page(self, tool, synthetic_schema, tmp_path):
        rows = {("ops/a.md", frozenset({"jobs"})): "GitHub Actions: workflow"}
        for lang in ("en", "tr"):
            _write(tmp_path, lang, "ops/a.md", "jobs: {}\n")
            _write(tmp_path, lang, "ops/b.md", "jobs: {}\n")
        result = _scan(tool, synthetic_schema, tmp_path, non_forgelm=rows)
        assert [u.path.relative_to(tmp_path).as_posix() for u in result.unknown_blocks] == [
            "en/ops/b.md",
            "tr/ops/b.md",
        ]
        assert result.stale_rows == []

    @pytest.mark.parametrize("table", ["non_forgelm", "known_open"])
    def test_a_row_covers_one_block_per_language_not_every_block_with_its_keys(
        self, tool, synthetic_schema, tmp_path, table
    ):
        rows = {("ops/t.md", frozenset({"audit"})): "#425: tracked"}
        two_blocks = "audit:\n  quality_filter: {}\n```\n\n```yaml\naudit:\n  pii_threshold: 0.5\n"
        for lang in ("en", "tr"):
            _write(tmp_path, lang, "ops/t.md", two_blocks)
        result = _scan(tool, synthetic_schema, tmp_path, **{table: rows})
        assert [(row.page, row.repeated_in, row.missing_in) for row in result.stale_rows] == [
            ("ops/t.md", ("en", "tr"), ())
        ]

    def test_each_row_is_matched_on_its_own_not_per_page(self, tool, synthetic_schema, tmp_path):
        rows = {
            ("ops/a.md", frozenset({"jobs"})): "GitHub Actions: workflow",
            ("ops/a.md", frozenset({"concurrency"})): "GitHub Actions: concurrency block",
        }
        for lang in ("en", "tr"):
            _write(tmp_path, lang, "ops/a.md", "jobs: {}\n")
        result = _scan(tool, synthetic_schema, tmp_path, non_forgelm=rows)
        assert [(row.page, row.keys) for row in result.stale_rows] == [("ops/a.md", frozenset({"concurrency"}))]

    def test_a_row_that_matches_no_snippet_is_stale(self, tool, synthetic_schema, tmp_path):
        rows = {("operations/gpu-cost.md", frozenset({"gpu_profiles"})): "#427: tracked"}
        for lang in ("en", "tr"):
            _write(tmp_path, lang, "operations/gpu-cost.md", "training:\n  learning_rate: 0.1\n")
        result = _scan(tool, synthetic_schema, tmp_path, known_open=rows)
        assert [(row.table, row.page, row.keys, row.page_found) for row in result.stale_rows] == [
            ("_KNOWN_OPEN", "operations/gpu-cost.md", frozenset({"gpu_profiles"}), True)
        ]
        assert not result.clean

    def test_a_row_matched_in_only_one_language_is_stale(self, tool, synthetic_schema, tmp_path):
        rows = {("operations/gpu-cost.md", frozenset({"gpu_profiles"})): "#427: tracked"}
        _write(tmp_path, "en", "operations/gpu-cost.md", "gpu_profiles:\n  custom: []\n")
        _write(tmp_path, "tr", "operations/gpu-cost.md", "training:\n  learning_rate: 0.1\n")
        result = _scan(tool, synthetic_schema, tmp_path, known_open=rows)
        assert [(row.matched_in, row.missing_in) for row in result.stale_rows] == [(("en",), ("tr",))]

    def test_known_blocks_inside_mixed_or_exempted_snippets_are_still_walked(self, tool, synthetic_schema, tmp_path):
        rows = {("operations/cicd.md", frozenset({"chat"})): "#1: tracked"}
        for lang in ("en", "tr"):
            _write(tmp_path, lang, "operations/cicd.md", "training:\n  bogus: 1\nchat: {}\n")
        result = _scan(tool, synthetic_schema, tmp_path, known_open=rows)
        assert sorted({d.key_path for d in result.drifts}) == ["training.bogus"]
        assert result.unknown_blocks == [] and result.stale_rows == []
        # Without the row, the same snippet reports BOTH the nested drift and the unknown block.
        bare = _scan(tool, synthetic_schema, tmp_path)
        assert len(bare.drifts) == 2 and len(bare.unknown_blocks) == 2

    def test_a_file_outside_a_language_directory_is_checked_but_cannot_be_exempted(
        self, tool, synthetic_schema, tmp_path
    ):
        rows = {("README.md", frozenset({"jobs"})): "GitHub Actions: workflow"}
        _write_raw(tmp_path, "README.md", "```yaml\njobs: {}\ntraining:\n  bogus: 1\n```\n\n```yml\nchat: {}\n```\n")
        for lang in ("en", "tr"):
            _write(tmp_path, lang, "README.md", "jobs: {}\n")
        result = _scan(tool, synthetic_schema, tmp_path, non_forgelm=rows)
        assert result.stale_rows == []  # the language copies match; the stray file never can
        assert [(u.path.relative_to(tmp_path).as_posix(), u.page) for u in result.unknown_blocks] == [
            ("README.md", None)
        ]
        assert [d.key_path for d in result.drifts] == ["training.bogus"]
        assert [b.path.relative_to(tmp_path).as_posix() for b in result.unreadable_blocks] == ["README.md"]

    def test_a_deferred_language_needs_a_row_only_on_the_pages_it_has(self, tool, synthetic_schema, tmp_path):
        rows = {("ops/a.md", frozenset({"jobs"})): "GitHub Actions: workflow"}
        for lang in ("en", "tr"):
            _write(tmp_path, lang, "ops/a.md", "jobs: {}\n")
        _write(tmp_path, "de", "ops/b.md", "training:\n  learning_rate: 0.1\n")
        assert _scan(tool, synthetic_schema, tmp_path, non_forgelm=rows).clean
        _write(tmp_path, "de", "ops/a.md", "training:\n  learning_rate: 0.1\n")
        result = _scan(tool, synthetic_schema, tmp_path, non_forgelm=rows)
        assert [(row.missing_in, row.matched_in) for row in result.stale_rows] == [(("de",), ("en", "tr"))]

    def test_a_mandatory_language_needs_every_row_even_without_the_page(self, tool, synthetic_schema, tmp_path):
        rows = {("ops/a.md", frozenset({"jobs"})): "GitHub Actions: workflow"}
        _write(tmp_path, "en", "ops/a.md", "jobs: {}\n")
        _write(tmp_path, "tr", "ops/other.md", "training:\n  learning_rate: 0.1\n")
        result = _scan(tool, synthetic_schema, tmp_path, non_forgelm=rows)
        assert [(row.missing_in, row.matched_in) for row in result.stale_rows] == [(("tr",), ("en",))]

    def test_an_empty_tree_cannot_pass_vacuously(self, tool, synthetic_schema, tmp_path):
        rows = {("ops/a.md", frozenset({"jobs"})): "GitHub Actions: workflow"}
        empty = _scan(tool, synthetic_schema, tmp_path, non_forgelm=rows)
        assert not empty.clean and empty.snippet_count == 0
        assert [(row.missing_in, row.page_found) for row in empty.stale_rows] == [(("en", "tr"), False)]


class TestExemptionTables:
    def test_well_formed_tables_have_no_errors(self, tool):
        non_forgelm = {("a.md", frozenset({"jobs"})): "GitHub Actions: workflow"}
        known_open = {("b.md", frozenset({"audit"})): "#425: documents a block ForgeConfig rejects"}
        assert tool.table_errors(non_forgelm, known_open) == []

    def test_a_known_open_reason_must_start_with_its_issue(self, tool):
        for reason in ("tracked elsewhere", "see #425", "#0: zero", "#425:", "#425 no colon"):
            errors = tool.table_errors({}, {("b.md", frozenset({"audit"})): reason})
            assert errors == [
                "_KNOWN_OPEN: b.md  audit: the reason must start with the issue that owns the fix, e.g. '#123: '"
            ], reason

    def test_a_non_forgelm_reason_must_name_the_other_tool(self, tool):
        for reason in ("#425: fabricated", "GitHub Actions workflow", "ForgeLM: chat block", "vLLM: "):
            errors = tool.table_errors({("a.md", frozenset({"chat"})): reason}, {})
            assert errors == [
                f"_NON_FORGELM_YAML: a.md  chat: the reason must start with the other tool's name, "
                f"one of {tool._OTHER_TOOLS}, and ': '"
            ], reason

    def test_a_row_in_both_tables_or_with_no_keys_is_an_error(self, tool):
        row = ("a.md", frozenset({"jobs"}))
        errors = tool.table_errors({row: "GitHub Actions: workflow"}, {row: "#1: also here"})
        assert errors == ["a.md  jobs: listed in both _NON_FORGELM_YAML and _KNOWN_OPEN"]
        errors = tool.table_errors({("a.md", frozenset()): "GitHub Actions: workflow"}, {})
        assert errors == ["_NON_FORGELM_YAML: a.md  : an empty key set never matches a block"]

    def test_real_exemption_tables_are_well_formed(self, tool):
        assert tool.table_errors(tool._NON_FORGELM_YAML, tool._KNOWN_OPEN) == []
        # A fabricated block must never be filed as "another tool's YAML".
        assert not any(
            {"chat", "output", "gpu_profiles"} & keys or keys == {"audit"} for _, keys in tool._NON_FORGELM_YAML
        )


class TestMain:
    def test_a_missing_config_fails_in_both_modes(self, tool, synthetic_main, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(tool, "CONFIG_PATH", tmp_path / "gone.py")
        assert tool.main(["--strict"]) == 1
        assert f"check_usermanual_schema_drift: {tmp_path / 'gone.py'} not found." in capsys.readouterr().err
        assert tool.main([]) == 1

    def test_a_missing_manual_fails_in_both_modes(self, tool, synthetic_main, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(tool, "USERMANUAL_ROOT", tmp_path / "gone")
        assert tool.main(["--strict"]) == 1
        assert f"check_usermanual_schema_drift: {tmp_path / 'gone'} not found." in capsys.readouterr().err
        assert tool.main([]) == 1

    def test_a_config_without_forge_config_fails_in_both_modes(self, tool, synthetic_main, tmp_path, capsys):
        (tmp_path / "config.py").write_text(_SYNTHETIC_CONFIG.replace("class ForgeConfig", "class Other"), "utf-8")
        _write(synthetic_main, "en", "ops/a.md", "training: {}\n")
        assert tool.main(["--strict"]) == 1
        assert "could not locate ForgeConfig in forgelm/config.py" in capsys.readouterr().err
        assert tool.main([]) == 1

    def test_a_malformed_table_fails_in_both_modes(self, tool, synthetic_main, monkeypatch, capsys):
        _write(synthetic_main, "en", "ops/a.md", "training: {}\n")
        monkeypatch.setattr(tool, "_KNOWN_OPEN", {("ops/a.md", frozenset({"audit"})): "no issue"})
        assert tool.main(["--strict"]) == 1
        err = capsys.readouterr().err
        assert "check_usermanual_schema_drift: malformed exemption table." in err
        assert "  _KNOWN_OPEN: ops/a.md  audit: the reason must start with the issue" in err
        assert tool.main([]) == 1

    def test_an_empty_tree_fails_in_both_modes_even_with_empty_tables(self, tool, synthetic_main, capsys):
        assert tool.main(["--strict"]) == 1
        assert "no yaml blocks found" in capsys.readouterr().err
        assert tool.main([]) == 1

    def test_a_tree_whose_only_blocks_are_unreadable_reports_them_and_fails(self, tool, synthetic_main, capsys):
        for lang in ("en", "tr"):
            _write_raw(synthetic_main, f"{lang}/ops/a.md", "```yml\nchat: {}\n```\n")
        assert tool.main([]) == 1
        captured = capsys.readouterr()
        assert "FAIL: 2 YAML block(s) in docs/usermanuals/ cannot be read" in captured.out
        assert "no yaml blocks found" in captured.err

    def test_the_success_summary_says_what_was_walked_and_quiet_suppresses_it(self, tool, synthetic_main, capsys):
        for lang in ("en", "tr"):
            _write_raw(
                synthetic_main,
                f"{lang}/ops/a.md",
                "```yaml\ntraining:\n  learning_rate: 0.1\n```\n\n"
                "```yaml\nmodel: {}\ntraining: {}\ndata: {}\n```\n\n"
                "```yaml\njobs: {}\n```\n",
            )
        tool._NON_FORGELM_YAML[("ops/a.md", frozenset({"jobs"}))] = "GitHub Actions: workflow"
        assert tool.main([]) == 0
        assert capsys.readouterr().out == (
            "OK: 6 yaml block(s) under docs/usermanuals/: 2 walked key by key, 2 full config(s) with their block "
            "names checked here and their nested keys left to check_yaml_snippets.py, 2 exempted by a table row "
            "(ForgeConfig blocks inside them still walked).\n"
        )
        assert tool.main(["--strict", "--quiet"]) == 0
        assert capsys.readouterr().out == ""

    def test_an_unlisted_block_fails_strict_and_names_the_row_to_add(self, tool, synthetic_main, capsys):
        for lang in ("en", "tr"):
            _write(synthetic_main, lang, "deployment/chat.md", "chat:\n  default_temperature: 0.7\n")
        assert tool.main(["--strict"]) == 1
        assert capsys.readouterr().out.splitlines() == [
            "FAIL: 2 YAML block(s) in docs/usermanuals/ use top-level keys ForgeConfig rejects.",
            *(
                f"  usermanuals/{lang}/deployment/chat.md:3  chat  (not a ForgeConfig top-level block: correct the "
                'example, or add ("deployment/chat.md", frozenset({"chat"})) to _NON_FORGELM_YAML with '
                '"<tool>: <what it is>" if it is another tool\'s YAML, or to _KNOWN_OPEN with '
                '"#<issue>: <what is wrong>" if a tracked issue owns the fix)'
                for lang in ("en", "tr")
            ),
        ]
        assert tool.main([]) == 0

    def test_a_block_outside_a_language_directory_is_reported_as_not_exemptable(self, tool, synthetic_main, capsys):
        _write(synthetic_main, "en", "ops/a.md", "training: {}\n")
        _write_raw(synthetic_main, "README.md", "```yaml\nchat: {}\n```\n")
        assert tool.main(["--strict"]) == 1
        assert capsys.readouterr().out.splitlines()[1] == (
            "  usermanuals/README.md:1  chat  (not a ForgeConfig top-level block: correct the example; "
            "a file outside a language directory cannot be exempted)"
        )

    def test_an_unreadable_block_names_the_problem_and_the_fix(self, tool, synthetic_main, capsys):
        for lang in ("en", "tr"):
            _write(synthetic_main, lang, "ops/a.md", "chat:\n  default_temperature: 0.7\n  ...\n")
        _write_raw(synthetic_main, "en/ops/b.md", "```yml\ntraining: {}\n```\n")
        _write_raw(synthetic_main, "tr/ops/b.md", "```yaml\ntraining: {}\n")
        assert tool.main(["--strict"]) == 1
        assert capsys.readouterr().out.splitlines() == [
            "FAIL: 4 YAML block(s) in docs/usermanuals/ cannot be read, so no key in them is checked.",
            "  usermanuals/en/ops/a.md:3  while scanning a simple key: could not find expected ':' at line 6  "
            "(make it valid YAML: write an elision as a comment, e.g. `# ...`)",
            "  usermanuals/en/ops/b.md:1  YAML fence written '```yml'  (open the block with ```yaml at the start "
            "of a line and close it with ```: the schema guards read no other fence)",
            "  usermanuals/tr/ops/a.md:3  while scanning a simple key: could not find expected ':' at line 6  "
            "(make it valid YAML: write an elision as a comment, e.g. `# ...`)",
            "  usermanuals/tr/ops/b.md:1  YAML fence never closed  (close the block with ``` on a line of its own)",
        ]

    def test_a_nested_drift_alone_fails_strict_and_names_file_line_and_key(self, tool, synthetic_main, capsys):
        for lang in ("en", "tr"):
            _write(synthetic_main, lang, "ops/a.md", "training:\n  bogus: 1\n")
        assert tool.main(["--strict"]) == 1
        assert capsys.readouterr().out.splitlines() == [
            "FAIL: 2 fabricated schema key(s) found in docs/usermanuals/ YAML examples.",
            "  usermanuals/en/ops/a.md:3  training.bogus  (no such field on the ForgeConfig schema)",
            "  usermanuals/tr/ops/a.md:3  training.bogus  (no such field on the ForgeConfig schema)",
        ]
        assert tool.main([]) == 0

    def test_a_stale_row_fails_strict_and_says_what_to_do(self, tool, synthetic_main, monkeypatch, capsys):
        for lang in ("en", "tr"):
            _write(synthetic_main, lang, "ops/a.md", "training:\n  learning_rate: 0.1\n")
        monkeypatch.setattr(
            tool,
            "_KNOWN_OPEN",
            {
                ("ops/a.md", frozenset({"gone"})): "#1: was fixed",
                ("ops/moved.md", frozenset({"gone"})): "#2: page renamed",
            },
        )
        assert tool.main(["--strict"]) == 1
        assert capsys.readouterr().out.splitlines() == [
            "FAIL: 2 exemption row(s) do not match exactly one YAML block per language.",
            "  _KNOWN_OPEN: ops/a.md  gone  (#1: was fixed) — no block on the page has exactly these keys: if the "
            "block changed, update the row's key set; if it was corrected or removed, delete the row",
            "  _KNOWN_OPEN: ops/moved.md  gone  (#2: page renamed) — page not found in en, tr: if it was renamed, "
            "update the row's path; otherwise delete the row",
        ]
        assert tool.main([]) == 0

    def test_a_diverged_or_repeated_row_says_what_to_do(self, tool, synthetic_main, monkeypatch, capsys):
        _write(synthetic_main, "en", "ops/a.md", "jobs: {}\n```\n\n```yaml\njobs: {}\n")
        _write(synthetic_main, "tr", "ops/a.md", "jobs: {}\n")
        _write(synthetic_main, "en", "ops/b.md", "concurrency: {}\n")
        _write(synthetic_main, "tr", "ops/b.md", "training: {}\n")
        monkeypatch.setattr(
            tool,
            "_NON_FORGELM_YAML",
            {
                ("ops/a.md", frozenset({"jobs"})): "GitHub Actions: workflow",
                ("ops/b.md", frozenset({"concurrency"})): "GitHub Actions: concurrency block",
            },
        )
        assert tool.main(["--strict"]) == 1
        assert capsys.readouterr().out.splitlines() == [
            "FAIL: 2 exemption row(s) do not match exactly one YAML block per language.",
            "  _NON_FORGELM_YAML: ops/a.md  jobs  (GitHub Actions: workflow) — matches more than one block in en: "
            "a row exempts exactly one block per language; correct the extra block",
            "  _NON_FORGELM_YAML: ops/b.md  concurrency  (GitHub Actions: concurrency block) — unmatched in tr but "
            "matched in en: the language copies diverged; fix the page, not the row",
        ]

    def test_a_path_outside_the_repository_is_reported_as_given(self, tool, synthetic_schema, tmp_path, capsys):
        for lang in ("en", "tr"):
            _write(tmp_path, lang, "ops/a.md", "training:\n  bogus: 1\n")
        tool._report(_scan(tool, synthetic_schema, tmp_path))
        assert f"  {(tmp_path / 'en' / 'ops' / 'a.md').as_posix()}:3  training.bogus" in capsys.readouterr().out

    def test_invalid_arguments_exit_two(self, tool, capsys):
        with pytest.raises(SystemExit) as excinfo:
            tool.main(["--bogus"])
        assert excinfo.value.code == 2
        assert "unrecognized arguments: --bogus" in capsys.readouterr().err


class TestImportFailures:
    """A guard that cannot read its inputs must never report success."""

    @staticmethod
    def _exec_fresh_copy(name: str) -> None:
        spec = importlib.util.spec_from_file_location(name, _TOOL_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]

    def test_a_missing_pyyaml_fails_closed(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "path", list(sys.path))
        monkeypatch.setitem(sys.modules, "yaml", None)
        with pytest.raises(SystemExit) as excinfo:
            self._exec_fresh_copy("_drift_guard_without_yaml")
        assert excinfo.value.code == 1
        assert "check_usermanual_schema_drift: PyYAML not importable" in capsys.readouterr().err

    def test_an_unimportable_forgelm_exits_one_not_the_argument_error_code(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "path", list(sys.path))
        monkeypatch.delitem(sys.modules, "check_yaml_snippets", raising=False)
        monkeypatch.setitem(sys.modules, "forgelm.config", None)
        with pytest.raises(SystemExit) as excinfo:
            self._exec_fresh_copy("_drift_guard_without_forgelm")
        assert excinfo.value.code == 1
        assert "cannot import sibling guard internals (SystemExit(2))" in capsys.readouterr().err


class TestRealSchemaSmoke:
    """Pins the AST walker's shape against the real schema, and the zero-finding
    invariant CI enforces with ``--strict`` on the real ``docs/usermanuals/``."""

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

    def test_every_real_exemption_row_matches_one_block_per_language(self, tool):
        schema = tool.build_schema_map(tool.CONFIG_PATH)
        result = tool.scan(
            tool.USERMANUAL_ROOT, schema, schema["ForgeConfig"], tool._NON_FORGELM_YAML, tool._KNOWN_OPEN
        )
        assert result.stale_rows == [] and result.unknown_blocks == [] and result.unreadable_blocks == []
        assert result.exempted_count == 2 * (len(tool._NON_FORGELM_YAML) + len(tool._KNOWN_OPEN))

    def test_main_runs_against_the_real_repo_without_crashing(self, tool):
        # Advisory mode exits 0 on findings; this proves the full walk and report path run end to end.
        assert tool.main(["--quiet"]) == 0

    def test_main_strict_finds_nothing_in_the_real_corpus(self, tool):
        # The invariant CI relies on: ci.yml runs this guard with --strict, so a
        # pytest run alone fails as soon as a finding lands in docs/usermanuals/.
        assert tool.main(["--strict", "--quiet"]) == 0

    def test_guard_wired_into_ci(self):
        ci = (_REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        assert "check_usermanual_schema_drift.py --strict" in ci
