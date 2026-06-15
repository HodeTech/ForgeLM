"""Unit tests for Phase 7: MoE, multimodal, merging, advanced PEFT."""

import json
import os

import yaml

from forgelm.config import (
    ForgeConfig,
    LoraConfigModel,
    MergeConfig,
    MoeConfig,
    MultimodalConfig,
    load_config,
)
from forgelm.merging import MergeResult

# --- MoE Config ---


class TestMoeConfig:
    def test_defaults(self):
        m = MoeConfig()
        assert m.quantize_experts is False
        assert m.experts_to_train == "all"

    def test_custom(self):
        m = MoeConfig(quantize_experts=True, experts_to_train="0,1,2")
        assert m.quantize_experts is True

    def test_in_model_config(self, minimal_config):
        cfg = ForgeConfig(**minimal_config(model={"name_or_path": "org/moe-model", "moe": {"quantize_experts": True}}))
        assert cfg.model.moe.quantize_experts is True

    def test_model_config_without_moe(self, minimal_config):
        cfg = ForgeConfig(**minimal_config())
        assert cfg.model.moe is None


# --- Multimodal Config ---


class TestMultimodalConfig:
    def test_defaults(self):
        m = MultimodalConfig()
        assert m.enabled is False
        assert m.image_column == "image"

    def test_enabled(self):
        m = MultimodalConfig(enabled=True, image_column="img_path")
        assert m.enabled is True
        assert m.image_column == "img_path"


# --- Merge Config ---


class TestMergeConfig:
    def test_defaults(self):
        m = MergeConfig()
        assert m.enabled is False
        assert m.method == "ties"
        assert m.models == []

    def test_with_models(self):
        m = MergeConfig(
            enabled=True,
            method="slerp",
            models=[
                {"path": "./model_a", "weight": 0.7},
                {"path": "./model_b", "weight": 0.3},
            ],
        )
        assert len(m.models) == 2
        assert m.method == "slerp"

    def test_in_forge_config(self, minimal_config):
        cfg = ForgeConfig(
            **minimal_config(
                merge={
                    "enabled": True,
                    "method": "linear",
                    "models": [{"path": "a", "weight": 0.5}, {"path": "b", "weight": 0.5}],
                }
            )
        )
        assert cfg.merge.enabled is True
        assert cfg.merge.method == "linear"


class TestMergeResult:
    def test_success(self):
        r = MergeResult(success=True, output_dir="/merged", method="ties", num_models=3)
        assert r.success is True
        assert r.num_models == 3

    def test_failure(self):
        r = MergeResult(success=False, error="No adapters")
        assert r.success is False


# --- Advanced PEFT ---


class TestAdvancedPeft:
    def test_default_method(self):
        lora = LoraConfigModel()
        assert lora.method == "lora"
        assert lora.use_rslora is False

    def test_pissa_method(self):
        lora = LoraConfigModel(method="pissa")
        assert lora.method == "pissa"

    def test_rslora_method(self):
        lora = LoraConfigModel(method="rslora", use_rslora=True)
        assert lora.use_rslora is True

    def test_dora_via_method(self):
        lora = LoraConfigModel(method="dora")
        assert lora.method == "dora"

    def test_backward_compat_use_dora(self):
        # use_dora=True now auto-normalizes method to "dora" with a deprecation warning
        lora = LoraConfigModel(use_dora=True)
        assert lora.use_dora is True
        assert lora.method == "dora"

    def test_full_config_pissa(self, tmp_path, minimal_config):
        data = minimal_config(lora={"method": "pissa", "r": 32})
        cfg_path = str(tmp_path / "config.yaml")
        with open(cfg_path, "w") as f:
            yaml.dump(data, f)
        cfg = load_config(cfg_path)
        assert cfg.lora.method == "pissa"
        assert cfg.lora.r == 32

    def test_full_config_rslora(self, tmp_path, minimal_config):
        data = minimal_config(lora={"method": "rslora", "use_rslora": True, "r": 128})
        cfg_path = str(tmp_path / "config.yaml")
        with open(cfg_path, "w") as f:
            yaml.dump(data, f)
        cfg = load_config(cfg_path)
        assert cfg.lora.use_rslora is True
        assert cfg.lora.r == 128


# --- YAML parsing ---


class TestPhase7YamlParsing:
    def test_moe_yaml(self, tmp_path, minimal_config):
        data = minimal_config(
            model={
                "name_or_path": "Qwen/Qwen3-30B-A3B",
                "moe": {"quantize_experts": True, "experts_to_train": "0,1"},
            }
        )
        cfg_path = str(tmp_path / "config.yaml")
        with open(cfg_path, "w") as f:
            yaml.dump(data, f)
        cfg = load_config(cfg_path)
        assert cfg.model.moe.quantize_experts is True

    def test_merge_yaml(self, tmp_path, minimal_config):
        data = minimal_config(
            merge={
                "enabled": True,
                "method": "dare",
                "models": [{"path": "a", "weight": 0.6}, {"path": "b", "weight": 0.4}],
                "output_dir": "/tmp/merged",
            }
        )
        cfg_path = str(tmp_path / "config.yaml")
        with open(cfg_path, "w") as f:
            yaml.dump(data, f)
        cfg = load_config(cfg_path)
        assert cfg.merge.method == "dare"
        assert len(cfg.merge.models) == 2

    def test_config_template_still_valid(self):
        template_path = os.path.join(os.path.dirname(__file__), "..", "config_template.yaml")
        if os.path.exists(template_path):
            cfg = load_config(template_path)
            assert cfg.model.name_or_path


# --- Notebooks exist ---


class TestNotebooks:
    def test_quickstart_notebook_exists(self):
        path = os.path.join(os.path.dirname(__file__), "..", "notebooks", "quickstart_sft.ipynb")
        assert os.path.isfile(path)
        with open(path) as f:
            nb = json.load(f)
        assert nb["nbformat"] == 4
        assert len(nb["cells"]) > 0

    def test_dpo_notebook_exists(self):
        path = os.path.join(os.path.dirname(__file__), "..", "notebooks", "dpo_alignment.ipynb")
        assert os.path.isfile(path)
        with open(path) as f:
            nb = json.load(f)
        assert nb["nbformat"] == 4
