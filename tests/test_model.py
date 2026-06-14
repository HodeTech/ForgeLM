"""Tests for forgelm.model — loading core, no GPU, no network (F-P8-C-02).

Before this package model.py had near-zero behavioural coverage: every test
that named it either asserted the export symbol (test_library_api.py), replaced
the whole module with a fake (the pipeline tests), or patched the function to
assert it is NOT called (test_no_train_modes.py). The real bodies of
``_resolve_bnb_compute_dtype`` (and its ``ValueError`` raise),
``_device_map_for``, ``_build_model_kwargs``, ``_load_tokenizer``, and
``_build_lora_config`` never executed, so a regression in dtype resolution,
device-map selection, quantization-kwargs gating, pad-token handling, or LoRA
target resolution would ship green.

These tests exercise those bodies directly. ``torch`` is the real (CPU)
import; ``torch.cuda.is_available`` is mocked so the GPU branches are reachable
on a laptop. transformers/peft entry points are mocked per the testing
standard (mock at the network/heavy-import boundary, use real config objects).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from forgelm import model as model_mod
from forgelm.config import ForgeConfig
from tests._helpers.factories import minimal_config


def _cfg(**overrides) -> ForgeConfig:
    return ForgeConfig(**minimal_config(**overrides))


class TestResolveBnbComputeDtype:
    """``_resolve_bnb_compute_dtype`` maps the YAML string to a torch dtype."""

    @pytest.mark.parametrize(
        "dtype_str,attr",
        [
            ("bf16", "bfloat16"),
            ("bfloat16", "bfloat16"),
            ("fp16", "float16"),
            ("float16", "float16"),
            ("fp32", "float32"),
            ("float32", "float32"),
        ],
    )
    def test_known_dtype_strings_resolve(self, dtype_str, attr):
        import torch

        assert model_mod._resolve_bnb_compute_dtype(dtype_str) is getattr(torch, attr)

    def test_raises_on_unsupported_dtype(self):
        with pytest.raises(ValueError, match="Unsupported bnb_4bit"):
            model_mod._resolve_bnb_compute_dtype("int4")

    def test_auto_resolves_without_raising(self):
        # 'auto' picks bf16/fp16 from cuda support — both are valid torch
        # dtypes; we only assert it does not raise (the cuda-support check is
        # mocked so the bf16 branch is deterministic on a CPU runner).
        import torch

        with patch("torch.cuda.is_bf16_supported", return_value=False):
            assert model_mod._resolve_bnb_compute_dtype("auto") is torch.float16


class TestDeviceMap:
    """``_device_map_for`` picks a device_map suited to the environment."""

    def test_distributed_returns_none(self):
        cfg = _cfg()
        assert model_mod._device_map_for(cfg, is_distributed=True) is None

    def test_four_bit_returns_auto(self):
        cfg = _cfg(model={"name_or_path": "org/model", "load_in_4bit": True})
        assert model_mod._device_map_for(cfg, is_distributed=False) == "auto"

    def test_single_gpu_pins_device_zero(self):
        cfg = _cfg(model={"name_or_path": "org/model", "load_in_4bit": False})
        with patch("torch.cuda.is_available", return_value=True):
            assert model_mod._device_map_for(cfg, is_distributed=False) == {"": 0}

    def test_cpu_only_returns_none(self):
        cfg = _cfg(model={"name_or_path": "org/model", "load_in_4bit": False})
        with patch("torch.cuda.is_available", return_value=False):
            assert model_mod._device_map_for(cfg, is_distributed=False) is None


class TestBuildModelKwargs:
    """``_build_model_kwargs`` gates the quantization config on CUDA presence."""

    def test_quantization_keys_gated_off_without_cuda(self):
        # load_in_4bit=True but CUDA unavailable → no quantization_config key,
        # and the model loads full precision (the loud-skip path).
        cfg = _cfg(model={"name_or_path": "org/model", "load_in_4bit": True})
        with patch("torch.cuda.is_available", return_value=False):
            kwargs = model_mod._build_model_kwargs(cfg, trust_remote_code=False)
        assert "quantization_config" not in kwargs

    def test_quantization_config_added_with_cuda(self):
        # Explicit bf16 dtype so ``_resolve_bnb_compute_dtype`` does not take
        # the 'auto' branch (which calls torch.cuda.is_bf16_supported and
        # asserts on CUDA-less builds).
        cfg = _cfg(model={"name_or_path": "org/model", "load_in_4bit": True, "bnb_4bit_compute_dtype": "bf16"})
        fake_bnb = MagicMock(name="BitsAndBytesConfig")
        with (
            patch("torch.cuda.is_available", return_value=True),
            patch.dict(
                "sys.modules",
                {"transformers": MagicMock(BitsAndBytesConfig=fake_bnb)},
            ),
        ):
            kwargs = model_mod._build_model_kwargs(cfg, trust_remote_code=False)
        assert "quantization_config" in kwargs
        fake_bnb.assert_called_once()

    def test_rope_and_sliding_window_passed_through(self):
        cfg = _cfg(
            model={"name_or_path": "org/model", "load_in_4bit": False},
            training={"rope_scaling": {"type": "linear", "factor": 2.0}, "sliding_window_attention": 4096},
        )
        with patch("torch.cuda.is_available", return_value=False):
            kwargs = model_mod._build_model_kwargs(cfg, trust_remote_code=False)
        assert kwargs["rope_scaling"] == {"type": "linear", "factor": 2.0}
        assert kwargs["sliding_window"] == 4096


class TestLoadTokenizer:
    """``_load_tokenizer`` ensures a pad_token and routes VLMs to AutoProcessor."""

    def test_pad_token_falls_back_to_eos(self):
        # SimpleNamespace (not MagicMock) so the ``getattr(tok, "tokenizer",
        # tok)`` inner-tokenizer probe falls back to ``tok`` itself rather than
        # auto-vivifying a ``.tokenizer`` child mock.
        fake_tok = SimpleNamespace(pad_token=None, eos_token="</s>")
        fake_transformers = MagicMock()
        fake_transformers.AutoTokenizer.from_pretrained.return_value = fake_tok
        cfg = _cfg()
        with patch.dict("sys.modules", {"transformers": fake_transformers}):
            out = model_mod._load_tokenizer(cfg, trust_remote_code=False)
        assert out is fake_tok
        assert fake_tok.pad_token == "</s>"

    def test_existing_pad_token_preserved(self):
        fake_tok = SimpleNamespace(pad_token="[PAD]", eos_token="</s>")
        fake_transformers = MagicMock()
        fake_transformers.AutoTokenizer.from_pretrained.return_value = fake_tok
        cfg = _cfg()
        with patch.dict("sys.modules", {"transformers": fake_transformers}):
            model_mod._load_tokenizer(cfg, trust_remote_code=False)
        assert fake_tok.pad_token == "[PAD]"


class TestBuildLoraConfig:
    """``_build_lora_config`` resolves the PEFT method and target modules."""

    def test_lora_target_modules_reach_peft(self):
        captured = {}

        def _fake_lora_config(**kwargs):
            captured.update(kwargs)
            return MagicMock(name="LoraConfig")

        cfg = _cfg(lora={"target_modules": ["q_proj", "k_proj"], "r": 8})
        with patch.dict("sys.modules", {"peft": MagicMock(LoraConfig=_fake_lora_config)}):
            model_mod._build_lora_config(cfg)
        assert captured["target_modules"] == ["q_proj", "k_proj"]
        assert captured["r"] == 8
        # default method 'lora' → no PiSSA init key
        assert "init_lora_weights" not in captured

    def test_pissa_sets_init_weights(self):
        captured = {}

        def _fake_lora_config(**kwargs):
            captured.update(kwargs)
            return MagicMock(name="LoraConfig")

        cfg = _cfg(lora={"method": "pissa"})
        with patch.dict("sys.modules", {"peft": MagicMock(LoraConfig=_fake_lora_config)}):
            model_mod._build_lora_config(cfg)
        assert captured["init_lora_weights"] == "pissa"


class TestResolvePeftFlags:
    """``_resolve_peft_flags`` is the single source of (use_dora, use_rslora, method)."""

    def test_method_dora_enables_use_dora(self):
        cfg = _cfg(lora={"method": "dora"})
        use_dora, use_rslora, method = model_mod._resolve_peft_flags(cfg)
        assert use_dora is True
        assert method == "dora"

    def test_method_rslora_enables_use_rslora(self):
        cfg = _cfg(lora={"method": "rslora"})
        use_dora, use_rslora, method = model_mod._resolve_peft_flags(cfg)
        assert use_rslora is True
        assert method == "rslora"
