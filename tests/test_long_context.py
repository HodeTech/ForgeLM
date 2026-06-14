"""Tests for long-context optimization features."""

import json

import pytest

from forgelm.config import ForgeConfig, load_config

BASE = {
    "model": {"name_or_path": "test/model"},
    "lora": {"r": 16, "alpha": 32},
    "data": {"dataset_name_or_path": "test.jsonl"},
}


def _config(**overrides):
    cfg = {**BASE, "training": {"output_dir": "./out"}}
    for key, val in overrides.items():
        if key == "training":
            cfg["training"].update(val)
        else:
            cfg[key] = val
    return ForgeConfig(**cfg)


class TestRopeScaling:
    def test_rope_disabled_by_default(self):
        config = _config()
        assert config.training.rope_scaling is None

    def test_rope_linear(self):
        config = _config(training={"rope_scaling": {"type": "linear", "factor": 4.0}})
        assert config.training.rope_scaling["type"] == "linear"
        assert config.training.rope_scaling["factor"] == pytest.approx(4.0)

    def test_rope_dynamic(self):
        config = _config(training={"rope_scaling": {"type": "dynamic", "factor": 2.0}})
        assert config.training.rope_scaling["type"] == "dynamic"

    def test_rope_yarn(self):
        config = _config(training={"rope_scaling": {"type": "yarn", "factor": 8.0}})
        assert config.training.rope_scaling["type"] == "yarn"
        assert config.training.rope_scaling["factor"] == pytest.approx(8.0)


class TestNeftune:
    def test_neftune_disabled_by_default(self):
        config = _config()
        assert config.training.neftune_noise_alpha is None

    def test_neftune_enabled(self):
        config = _config(training={"neftune_noise_alpha": 5.0})
        assert config.training.neftune_noise_alpha == pytest.approx(5.0)

    def test_neftune_custom_value(self):
        config = _config(training={"neftune_noise_alpha": 15.0})
        assert config.training.neftune_noise_alpha == pytest.approx(15.0)


class TestSlidingWindow:
    def test_sliding_window_disabled_by_default(self):
        config = _config()
        assert config.training.sliding_window_attention is None

    def test_sliding_window_custom(self):
        config = _config(training={"sliding_window_attention": 4096})
        assert config.training.sliding_window_attention == 4096


class TestSamplePacking:
    def test_sample_packing_disabled_by_default(self):
        config = _config()
        assert config.training.sample_packing is False
        assert config.training.packing is False

    def test_sample_packing_enabled_forwards_to_packing(self):
        # Deprecated alias: setting sample_packing forwards to packing so the
        # documented behaviour actually fires (it used to be a silent no-op).
        config = _config(training={"sample_packing": True})
        assert config.training.packing is True

    def test_sample_packing_emits_deprecation_warning(self):
        with pytest.warns(DeprecationWarning, match="sample_packing"):
            _config(training={"sample_packing": True})


class TestLongContextYaml:
    def test_yaml_round_trip(self, tmp_path):
        yaml_content = """
model:
  name_or_path: "test/model"
  max_length: 32768
lora:
  r: 16
  alpha: 32
data:
  dataset_name_or_path: "test.jsonl"
training:
  output_dir: "./out"
  rope_scaling:
    type: "yarn"
    factor: 4.0
  neftune_noise_alpha: 5.0
  sliding_window_attention: 4096
  sample_packing: true
"""
        config_file = tmp_path / "longctx.yaml"
        config_file.write_text(yaml_content)
        config = load_config(str(config_file))

        assert config.training.rope_scaling["type"] == "yarn"
        assert config.training.rope_scaling["factor"] == pytest.approx(4.0)
        assert config.training.neftune_noise_alpha == pytest.approx(5.0)
        assert config.training.sliding_window_attention == 4096
        assert config.training.sample_packing is True

    def test_config_template_still_valid(self):
        config = load_config("config_template.yaml")
        assert config.training.rope_scaling is None
        assert config.training.neftune_noise_alpha is None


class TestLongContextDryRun:
    def test_dry_run_shows_rope_scaling(self, tmp_path):
        from forgelm.cli import _run_dry_run

        yaml_content = """
model:
  name_or_path: "test/model"
lora:
  r: 16
  alpha: 32
data:
  dataset_name_or_path: "test.jsonl"
training:
  output_dir: "./out"
  rope_scaling:
    type: "linear"
    factor: 4.0
  neftune_noise_alpha: 10.0
"""
        config_file = tmp_path / "longctx.yaml"
        config_file.write_text(yaml_content)
        config = load_config(str(config_file))

        import io
        from contextlib import redirect_stdout

        f = io.StringIO()
        with redirect_stdout(f):
            _run_dry_run(config, output_format="json")
        output = json.loads(f.getvalue())

        assert output["rope_scaling"]["type"] == "linear"
        assert output["rope_scaling"]["factor"] == pytest.approx(4.0)
        assert output["neftune_noise_alpha"] == pytest.approx(10.0)
