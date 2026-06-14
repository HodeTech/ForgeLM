"""Unit tests for forgelm.config module."""

import logging
import os

import pytest
import yaml
from pydantic import ValidationError

from forgelm.config import (
    BenchmarkConfig,
    ConfigError,
    EvaluationConfig,
    ForgeConfig,
    JudgeConfig,
    LoraConfigModel,
    ModelConfig,
    SafetyConfig,
    TrainingConfig,
    WebhookConfig,
    load_config,
)
from tests._helpers.factories import minimal_config

# --- Helper ---


def _write_yaml(data: dict, path: str) -> str:
    with open(path, "w") as f:
        yaml.dump(data, f)
    return path


def _full_config() -> dict:
    """Smallest valid config dict using ``some-org/...`` names.

    Test assertions in this module reference these specific strings, so we
    pin them here on top of the shared ``minimal_config`` factory rather
    than inline an override at every call site.
    """
    return minimal_config(
        model={"name_or_path": "some-org/some-model"},
        data={"dataset_name_or_path": "some-org/some-dataset"},
    )


# --- ModelConfig ---


class TestModelConfig:
    def test_defaults(self):
        m = ModelConfig(name_or_path="org/model")
        assert m.backend == "transformers"
        assert m.load_in_4bit is True
        assert m.trust_remote_code is False
        assert m.max_length == 2048
        assert m.bnb_4bit_quant_type == "nf4"
        assert m.bnb_4bit_compute_dtype == "auto"

    def test_trust_remote_code_explicit(self):
        m = ModelConfig(name_or_path="org/model", trust_remote_code=True)
        assert m.trust_remote_code is True

    def test_unsloth_backend(self):
        m = ModelConfig(name_or_path="org/model", backend="unsloth")
        assert m.backend == "unsloth"


# --- LoraConfigModel ---


class TestLoraConfig:
    def test_defaults(self):
        lora = LoraConfigModel()
        assert lora.r == 8
        assert lora.alpha == 16
        assert lora.dropout == pytest.approx(0.1)
        assert lora.bias == "none"
        assert lora.use_dora is False
        assert lora.target_modules == ["q_proj", "v_proj"]
        assert lora.task_type == "CAUSAL_LM"

    def test_custom_target_modules(self):
        lora = LoraConfigModel(target_modules=["q_proj", "k_proj", "v_proj", "o_proj"])
        assert len(lora.target_modules) == 4

    def test_dora_enabled(self):
        lora = LoraConfigModel(use_dora=True)
        assert lora.use_dora is True


# --- TrainingConfig ---


class TestTrainingConfig:
    def test_defaults(self):
        t = TrainingConfig()
        assert t.output_dir == "./checkpoints"
        assert t.final_model_dir == "final_model"
        assert t.merge_adapters is False
        assert t.packing is False

    def test_custom_values(self):
        t = TrainingConfig(learning_rate=1e-4, num_train_epochs=5)
        assert t.learning_rate == pytest.approx(1e-4)
        assert t.num_train_epochs == 5


# --- EvaluationConfig ---


class TestEvaluationConfig:
    def test_defaults(self):
        e = EvaluationConfig()
        assert e.auto_revert is False
        assert e.max_acceptable_loss is None
        assert e.baseline_loss is None

    def test_auto_revert_with_max_loss(self):
        e = EvaluationConfig(auto_revert=True, max_acceptable_loss=2.5)
        assert e.auto_revert is True
        assert e.max_acceptable_loss == pytest.approx(2.5)


# --- WebhookConfig ---


class TestWebhookConfig:
    def test_defaults(self):
        w = WebhookConfig()
        assert w.url is None
        assert w.url_env is None
        assert w.notify_on_start is True
        assert w.notify_on_success is True
        assert w.notify_on_failure is True
        # F-W3T-09 regression: pin the F-compliance-106 default
        # (5 → 10) here next to every other field default so a
        # contributor editing WebhookConfig sees the pin in the
        # obvious reading position.
        assert w.timeout == 10

    def test_url_env(self):
        w = WebhookConfig(url_env="MY_WEBHOOK_URL")
        assert w.url_env == "MY_WEBHOOK_URL"


# --- ForgeConfig (full config) ---


class TestForgeConfig:
    def test_minimal_config(self):
        cfg = ForgeConfig(**_full_config())
        assert cfg.model.name_or_path == "some-org/some-model"
        assert cfg.auth is None
        assert cfg.evaluation is None
        assert cfg.webhook is None

    def test_full_config(self):
        data = _full_config()
        data["auth"] = {"hf_token": "hf_test"}
        data["evaluation"] = {"auto_revert": True, "max_acceptable_loss": 2.0}
        data["webhook"] = {"url": "https://example.com/hook"}
        cfg = ForgeConfig(**data)
        assert cfg.auth.hf_token == "hf_test"
        assert cfg.evaluation.auto_revert is True
        assert cfg.webhook.url == "https://example.com/hook"

    def test_invalid_type_raises(self):
        data = _full_config()
        data["model"]["max_length"] = "not_a_number"
        with pytest.raises((ValueError, TypeError)):
            ForgeConfig(**data)

    def test_missing_required_field(self):
        data = _full_config()
        del data["data"]["dataset_name_or_path"]
        with pytest.raises((ValueError, TypeError, KeyError)):
            ForgeConfig(**data)


# --- load_config ---


class TestLoadConfig:
    def test_valid_file(self, tmp_path):
        cfg_path = str(tmp_path / "config.yaml")
        _write_yaml(_full_config(), cfg_path)
        cfg = load_config(cfg_path)
        assert isinstance(cfg, ForgeConfig)
        assert cfg.model.name_or_path == "some-org/some-model"

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/path/config.yaml")

    def test_invalid_yaml_raises(self, tmp_path):
        from forgelm.config import ConfigError

        cfg_path = str(tmp_path / "bad.yaml")
        with open(cfg_path, "w") as f:
            f.write(": : invalid yaml [[[")
        with pytest.raises(ConfigError):
            load_config(cfg_path)

    def test_config_template_parses(self):
        """Ensure the shipped config_template.yaml is always valid."""
        template_path = os.path.join(os.path.dirname(__file__), "..", "config_template.yaml")
        if os.path.exists(template_path):
            cfg = load_config(template_path)
            assert cfg.model.name_or_path

    def test_trust_remote_code_in_yaml(self, tmp_path):
        data = _full_config()
        data["model"]["trust_remote_code"] = True
        cfg_path = str(tmp_path / "config.yaml")
        _write_yaml(data, cfg_path)
        cfg = load_config(cfg_path)
        assert cfg.model.trust_remote_code is True

    def test_extra_fields_raise_error(self, tmp_path):
        """Unknown keys in any sub-model must raise ConfigError (extra='forbid')."""
        data = _full_config()
        data["model"]["unknown_field_xyz"] = 42
        cfg_path = str(tmp_path / "config.yaml")
        _write_yaml(data, cfg_path)
        with pytest.raises(ConfigError, match="Extra inputs are not permitted"):
            load_config(cfg_path)

    def test_extra_fields_forbidden_in_training(self, tmp_path):
        """Extra fields in training sub-model must raise ConfigError."""
        data = _full_config()
        data["training"]["nonexistent_training_param"] = 999
        cfg_path = str(tmp_path / "config.yaml")
        _write_yaml(data, cfg_path)
        with pytest.raises(ConfigError, match="Extra inputs are not permitted"):
            load_config(cfg_path)

    def test_extra_fields_forbidden_in_lora(self, tmp_path):
        """Extra fields in lora sub-model must raise ConfigError."""
        data = _full_config()
        data["lora"]["typo_lora_param"] = True
        cfg_path = str(tmp_path / "config.yaml")
        _write_yaml(data, cfg_path)
        with pytest.raises(ConfigError, match="Extra inputs are not permitted"):
            load_config(cfg_path)

    def test_extra_fields_forbidden_in_data(self, tmp_path):
        """Extra fields in data sub-model must raise ConfigError."""
        data = _full_config()
        data["data"]["unknown_data_option"] = "bad"
        cfg_path = str(tmp_path / "config.yaml")
        _write_yaml(data, cfg_path)
        with pytest.raises(ConfigError, match="Extra inputs are not permitted"):
            load_config(cfg_path)


# --- DataConfig validators ---


class TestDataConfigValidators:
    def test_mix_ratio_negative_raises(self):
        from forgelm.config import DataConfig

        with pytest.raises(Exception, match="non-negative"):
            DataConfig(dataset_name_or_path="org/d", mix_ratio=[-0.5, 1.0])

    def test_mix_ratio_all_zero_raises(self):
        from forgelm.config import DataConfig

        with pytest.raises(Exception, match="cannot all be zero"):
            DataConfig(dataset_name_or_path="org/d", mix_ratio=[0.0, 0.0])

    def test_mix_ratio_valid_passes(self):
        from forgelm.config import DataConfig

        d = DataConfig(dataset_name_or_path="org/d", mix_ratio=[0.7, 0.3])
        assert d.mix_ratio == [0.7, 0.3]

    def test_mix_ratio_none_passes(self):
        from forgelm.config import DataConfig

        d = DataConfig(dataset_name_or_path="org/d")
        assert d.mix_ratio is None


# --- LoraConfigModel deprecation normalisation ---


class TestLoraDeprecation:
    def test_use_rslora_deprecated_normalizes_method(self):
        """use_rslora=True must auto-set method='rslora'."""
        lora = LoraConfigModel(use_rslora=True)
        assert lora.method == "rslora"

    def test_use_dora_deprecated_normalizes_method(self):
        """use_dora=True must auto-set method='dora'."""
        lora = LoraConfigModel(use_dora=True)
        assert lora.method == "dora"


# --- ModelConfig float32+4bit warning ---


class TestModelConfigWarnings:
    def test_float32_qlora_warning(self, caplog):
        """bnb_4bit_compute_dtype='float32' with load_in_4bit=True must emit a WARNING."""
        with caplog.at_level(logging.WARNING, logger="forgelm.config"):
            ModelConfig(name_or_path="org/m", load_in_4bit=True, bnb_4bit_compute_dtype="float32")
        assert any("negates most VRAM savings" in r.message for r in caplog.records)

    def test_bfloat16_no_warning(self, caplog):
        """bfloat16 compute dtype must NOT trigger the float32 warning."""
        with caplog.at_level(logging.WARNING, logger="forgelm.config"):
            ModelConfig(name_or_path="org/m", load_in_4bit=True, bnb_4bit_compute_dtype="bfloat16")
        assert not any("negates most VRAM savings" in r.message for r in caplog.records)


# --- H2: safety/judge/benchmark gate threshold validation (XP-06) ---


class TestSafetyGateValidation:
    """Reachable SafetyConfig states that silently disabled a configured gate
    must now be rejected (or auto-corrected) at config time.

    Findings F-P1-FAB-04, F-P1-FAB-07, F-P1-FAB-08, F-P3-FABLE-15.
    """

    def test_min_safety_score_with_binary_scoring_raises(self):
        with pytest.raises(ValidationError, match="confidence_weighted"):
            SafetyConfig(enabled=True, scoring="binary", min_safety_score=0.99)

    def test_min_safety_score_with_confidence_weighted_accepted(self):
        s = SafetyConfig(enabled=True, scoring="confidence_weighted", min_safety_score=0.9)
        assert s.min_safety_score == pytest.approx(0.9)

    def test_max_safety_regression_above_one_raises(self):
        with pytest.raises(ValidationError):
            SafetyConfig(enabled=True, max_safety_regression=5.0)

    def test_min_classifier_confidence_negative_raises(self):
        with pytest.raises(ValidationError):
            SafetyConfig(enabled=True, min_classifier_confidence=-2.0)

    def test_severity_thresholds_unknown_key_raises(self):
        with pytest.raises(ValidationError, match="not a recognized severity"):
            SafetyConfig(enabled=True, track_categories=True, severity_thresholds={"Critical": 0.0})

    def test_severity_thresholds_value_above_one_raises(self):
        with pytest.raises(ValidationError, match=r"\[0.0, 1.0\]"):
            SafetyConfig(enabled=True, track_categories=True, severity_thresholds={"high": 5.0})

    def test_severity_thresholds_without_track_categories_auto_enables(self, caplog):
        with caplog.at_level(logging.WARNING, logger="forgelm.config"):
            s = SafetyConfig(enabled=True, severity_thresholds={"critical": 0.0})
        assert s.track_categories is True
        assert any("auto-enabling track_categories" in r.message for r in caplog.records)

    def test_judge_min_score_above_scale_raises(self):
        with pytest.raises(ValidationError):
            JudgeConfig(enabled=True, min_score=99)

    def test_judge_min_score_below_scale_raises(self):
        with pytest.raises(ValidationError):
            JudgeConfig(enabled=True, min_score=0)

    def test_benchmark_min_score_above_one_raises(self):
        with pytest.raises(ValidationError):
            BenchmarkConfig(enabled=True, min_score=7.0)

    def test_benchmark_min_score_in_range_accepted(self):
        b = BenchmarkConfig(enabled=True, min_score=0.6, tasks=["arc_easy"])
        assert b.min_score == pytest.approx(0.6)
