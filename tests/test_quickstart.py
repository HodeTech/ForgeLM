"""Tests for the quickstart layer (Phase 10.5).

These tests are collected without requiring torch/transformers — the module
under test never imports the training stack on the happy path. The CLI
dispatch tests stop short of actually invoking subprocess training.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
import yaml

from forgelm.quickstart import (
    TEMPLATES,
    auto_select_model,
    format_template_list,
    get_template,
    list_templates,
    run_quickstart,
    summarize_result,
    template_assets,
    templates_dir,
)

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_registered_templates_match_expected_set(self):
        # Spec: five templates ship in the initial cut.
        assert set(TEMPLATES.keys()) == {
            "customer-support",
            "code-assistant",
            "domain-expert",
            "medical-qa-tr",
            "grpo-math",
        }

    def test_get_template_returns_dataclass(self):
        tpl = get_template("customer-support")
        assert tpl.name == "customer-support"
        assert tpl.trainer_type == "sft"

    def test_get_template_unknown_raises_with_helpful_message(self):
        with pytest.raises(ValueError, match="Unknown template"):
            get_template("does-not-exist")

    def test_list_templates_preserves_insertion_order(self):
        names = [t.name for t in list_templates()]
        assert names[0] == "customer-support"
        # Math template comes last in the registry — guards against random
        # iteration order regressions.
        assert names[-1] == "grpo-math"

    def test_format_template_list_includes_every_name(self):
        rendered = format_template_list()
        for name in TEMPLATES:
            assert name in rendered

    def test_fallback_models_match_template_language_and_domain(self):
        # code-assistant must fall back to a code-tuned smaller model, not a
        # generic chat model — SmolLM2 doesn't write usable code.
        assert TEMPLATES["code-assistant"].fallback_model == "Qwen/Qwen2.5-Coder-1.5B-Instruct"
        # medical-qa-tr is a Turkish-language template; the fallback must
        # speak Turkish (SmolLM2 is mostly English).
        assert "Qwen2.5-1.5B" in TEMPLATES["medical-qa-tr"].fallback_model


# ---------------------------------------------------------------------------
# Bundled assets
# ---------------------------------------------------------------------------


class TestBundledAssets:
    def test_templates_dir_exists_inside_package(self):
        d = templates_dir()
        assert d.is_dir()
        # Sentinel — the licenses index is part of the spec.
        assert (d / "LICENSES.md").is_file()

    @pytest.mark.parametrize("name", list(TEMPLATES.keys()))
    def test_each_template_has_yaml_config(self, name):
        cfg_path, _ = template_assets(name)
        assert cfg_path.is_file()
        with open(cfg_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        assert "model" in cfg
        assert "training" in cfg
        assert "data" in cfg
        # All templates must declare a placeholder dataset path that the
        # quickstart layer overwrites — guards against forgetting to wire it.
        assert "PLACEHOLDER" in cfg["data"]["dataset_name_or_path"]

    @pytest.mark.parametrize(
        "name",
        [n for n, t in TEMPLATES.items() if t.bundled_dataset],
    )
    def test_bundled_datasets_parse_as_jsonl(self, name):
        _, data_path = template_assets(name)
        assert data_path is not None
        with open(data_path, encoding="utf-8") as f:
            lines = [line for line in f if line.strip()]
        assert len(lines) > 0
        for line in lines[:3]:
            json.loads(line)  # raises if malformed

    def test_domain_expert_intentionally_has_no_bundled_data(self):
        _, data_path = template_assets("domain-expert")
        assert data_path is None
        readme = templates_dir() / "domain-expert" / "README.md"
        assert readme.is_file()  # spec: BYOD path must be documented

    def test_grpo_math_dataset_carries_gold_answers(self):
        # Built-in regex math reward keys off `gold_answer`; without it the
        # trainer raises ValueError. Guards against accidentally dropping the
        # field when refreshing the seed dataset.
        _, data_path = template_assets("grpo-math")
        assert data_path is not None
        with open(data_path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                obj = json.loads(line)
                assert "gold_answer" in obj, "grpo-math row missing 'gold_answer'"
                assert obj["gold_answer"], "grpo-math row has empty 'gold_answer'"

    def test_conservative_defaults_in_every_config(self):
        # Spec: QLoRA 4-bit, rank ≤ 8, gradient checkpointing intent, batch=1.
        for name in TEMPLATES:
            cfg_path, _ = template_assets(name)
            with open(cfg_path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            assert cfg["model"]["load_in_4bit"] is True, f"{name} not 4-bit"
            assert cfg["lora"]["r"] <= 8, f"{name} LoRA rank > 8"
            assert cfg["training"]["per_device_train_batch_size"] == 1, f"{name} batch != 1"


# ---------------------------------------------------------------------------
# auto_select_model
# ---------------------------------------------------------------------------


class TestAutoSelectModel:
    def test_uses_primary_when_vram_above_threshold(self):
        tpl = get_template("customer-support")
        model, reason = auto_select_model(tpl, available_vram_gb=24.0)
        assert model == tpl.primary_model
        assert "primary" in reason

    def test_downsizes_to_fallback_when_vram_below_threshold(self):
        tpl = get_template("customer-support")
        model, reason = auto_select_model(tpl, available_vram_gb=6.0)
        assert model == tpl.fallback_model
        assert "auto-downsized" in reason

    def test_no_gpu_returns_fallback_with_cpu_note(self):
        tpl = get_template("grpo-math")
        model, reason = auto_select_model(tpl, available_vram_gb=None)
        # No-GPU path now downsizes to the fallback model — primary 7B class
        # is unloadable on CPU/Mac.
        assert model == tpl.fallback_model
        assert "no GPU detected" in reason


# ---------------------------------------------------------------------------
# run_quickstart — generation flow (no training invoked)
# ---------------------------------------------------------------------------


class TestRunQuickstart:
    def test_generates_yaml_with_model_and_dataset_substituted(self, tmp_path):
        out = tmp_path / "out.yaml"
        result = run_quickstart(
            "customer-support",
            output_path=str(out),
            available_vram_gb=24.0,  # force primary model selection
        )

        assert result.config_path == out
        assert out.is_file()

        with open(out, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        # Both placeholders must have been replaced with real values.
        assert cfg["model"]["name_or_path"] == result.template.primary_model
        assert cfg["data"]["dataset_name_or_path"] == result.dataset_path
        assert "PLACEHOLDER" not in cfg["data"]["dataset_name_or_path"]

    def test_model_override_wins_over_auto_select(self, tmp_path):
        out = tmp_path / "override.yaml"
        result = run_quickstart(
            "customer-support",
            output_path=str(out),
            model_override="my-org/custom-model",
            available_vram_gb=4.0,  # would normally trigger fallback
        )
        assert result.chosen_model == "my-org/custom-model"
        assert "model-override" in result.selection_reason

    def test_dataset_override_skips_bundled_copy(self, tmp_path):
        external = tmp_path / "my-data.jsonl"
        external.write_text('{"messages": [{"role": "user", "content": "x"}]}\n')

        out = tmp_path / "out.yaml"
        result = run_quickstart(
            "customer-support",
            output_path=str(out),
            dataset_override=str(external),
        )
        assert result.dataset_path == str(external)
        # No "copied seed dataset" note when the user supplies their own.
        assert not any("copied seed dataset" in n for n in result.extra_notes)

    def test_domain_expert_without_dataset_override_raises(self, tmp_path):
        with pytest.raises(ValueError, match="does not bundle a dataset"):
            run_quickstart("domain-expert", output_path=str(tmp_path / "x.yaml"))

    def test_domain_expert_with_dataset_override_succeeds(self, tmp_path):
        ds = tmp_path / "byo.jsonl"
        ds.write_text('{"messages": [{"role": "user", "content": "hi"}]}\n')
        result = run_quickstart(
            "domain-expert",
            output_path=str(tmp_path / "out.yaml"),
            dataset_override=str(ds),
        )
        assert result.dataset_path == str(ds)

    def test_unknown_template_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown template"):
            run_quickstart("not-a-template")

    def test_default_output_path_lands_under_configs(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = run_quickstart("customer-support", available_vram_gb=24.0)
        # New scheme: configs/<template>-<utc-ts>/config.yaml — the per-run
        # subdirectory exists so a second quickstart cannot overwrite it.
        assert result.config_path.parent.parent.name == "configs"
        assert result.config_path.parent.name.startswith("customer-support-")
        assert result.config_path.name == "config.yaml"
        assert result.config_path.is_file()

    def test_summary_includes_dry_run_hint(self, tmp_path):
        result = run_quickstart(
            "code-assistant",
            output_path=str(tmp_path / "x.yaml"),
            dry_run=True,
            available_vram_gb=24.0,
        )
        text = summarize_result(result)
        assert "Dry-run only" in text
        assert "forgelm --config" in text


# ---------------------------------------------------------------------------
# Smoke: every template renders with the auto-detected VRAM path mocked out
# ---------------------------------------------------------------------------


class TestTemplatesSmoke:
    @pytest.mark.parametrize("name", [n for n in TEMPLATES if TEMPLATES[n].bundled_dataset])
    def test_every_bundled_template_renders_to_yaml(self, name, tmp_path):
        # Mock the GPU probe so the test stays GPU-independent.
        with patch("forgelm.quickstart._detect_available_vram_gb", return_value=24.0):
            result = run_quickstart(name, output_path=str(tmp_path / f"{name}.yaml"))
        assert result.config_path.is_file()
        with open(result.config_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        # Generated config must round-trip through pydantic later, so the
        # absolute minimum invariant: shape is dict-of-dicts with the
        # required top-level sections.
        for section in ("model", "lora", "training", "data"):
            assert isinstance(cfg.get(section), dict), f"{name} missing or wrong type for `{section}`"


# ---------------------------------------------------------------------------
# CLI integration (no training subprocess)
# ---------------------------------------------------------------------------


class TestCLIQuickstart:
    def test_quickstart_list_text_output(self, capsys):
        from forgelm.cli import main

        with patch("sys.argv", ["forgelm", "quickstart", "--list"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        for name in TEMPLATES:
            assert name in captured.out

    def test_quickstart_list_json_output(self, capsys):
        from forgelm.cli import main

        with patch(
            "sys.argv",
            ["forgelm", "--output-format", "json", "quickstart", "--list"],
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        # F-P7-OPUS-24: --list JSON is wrapped in the universal success
        # envelope (success/templates/count), not a bare top-level array, so
        # a consumer can branch on the single ``success`` key like every
        # other subcommand.
        assert isinstance(payload, dict)
        assert payload["success"] is True
        assert payload["count"] == len(payload["templates"])
        names = {entry["name"] for entry in payload["templates"]}
        assert names == set(TEMPLATES)

    def test_quickstart_dry_run_writes_yaml_and_exits_clean(self, tmp_path, capsys):
        from forgelm.cli import main

        out = tmp_path / "dry.yaml"
        with patch("forgelm.quickstart._detect_available_vram_gb", return_value=24.0):
            with patch(
                "sys.argv",
                [
                    "forgelm",
                    "quickstart",
                    "customer-support",
                    "--dry-run",
                    "--output",
                    str(out),
                ],
            ):
                with pytest.raises(SystemExit) as exc_info:
                    main()
        assert exc_info.value.code == 0
        assert out.is_file()
        captured = capsys.readouterr()
        assert "Dry-run only" in captured.out

    def test_quickstart_without_template_or_list_errors(self):
        from forgelm.cli import main

        with patch("sys.argv", ["forgelm", "quickstart"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
        # CONFIG_ERROR rather than 0 — user must specify a template or --list.
        assert exc_info.value.code != 0


# ---------------------------------------------------------------------------
# Regression: generated config validates against ForgeConfig
# ---------------------------------------------------------------------------


class TestGeneratedConfigValidates:
    @pytest.mark.parametrize("name", [n for n in TEMPLATES if TEMPLATES[n].bundled_dataset])
    def test_generated_yaml_passes_pydantic_validation(self, name, tmp_path):
        """Templates must produce configs that the loader accepts unchanged.

        This is the single strongest guard against template drift — if a
        template starts emitting a YAML the trainer rejects, this test fails
        at the next nightly regardless of GPU availability.
        """
        from forgelm.config import load_config

        with patch("forgelm.quickstart._detect_available_vram_gb", return_value=24.0):
            result = run_quickstart(name, output_path=str(tmp_path / f"{name}.yaml"))

        cfg = load_config(str(result.config_path))
        assert cfg.model.name_or_path == result.chosen_model
        assert cfg.training.trainer_type == TEMPLATES[name].trainer_type
