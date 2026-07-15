"""Unit tests for forgelm.fit_check module."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from forgelm.config import ForgeConfig


def _make_torch_no_cuda():
    t = MagicMock()
    t.cuda.is_available.return_value = False
    t.cuda.mem_get_info.side_effect = RuntimeError("No CUDA")
    return t


def _make_torch_with_cuda(total_bytes=12 * 1024**3):
    t = MagicMock()
    t.cuda.is_available.return_value = True
    free = int(total_bytes * 0.9)
    t.cuda.mem_get_info.return_value = (free, total_bytes)
    return t


# ---------------------------------------------------------------------------
# _estimate_param_count
# ---------------------------------------------------------------------------


class TestEstimateParamCount:
    def test_llama_7b_ballpark(self):
        from forgelm.fit_check import _estimate_param_count

        arch = {
            "hidden_size": 4096,
            "num_hidden_layers": 32,
            "intermediate_size": 11008,
            "vocab_size": 32000,
            "num_attention_heads": 32,
            "num_key_value_heads": 32,
        }
        count = _estimate_param_count(arch)
        # Llama 7B is ~7B params; allow ±50% for the heuristic
        assert 3_000_000_000 < count < 14_000_000_000

    def test_larger_model_bigger_count(self):
        from forgelm.fit_check import _estimate_param_count

        arch_small = {
            "hidden_size": 2048,
            "num_hidden_layers": 16,
            "intermediate_size": 5504,
            "vocab_size": 32000,
            "num_attention_heads": 16,
            "num_key_value_heads": 16,
        }
        arch_large = {
            "hidden_size": 8192,
            "num_hidden_layers": 80,
            "intermediate_size": 28672,
            "vocab_size": 32000,
            "num_attention_heads": 64,
            "num_key_value_heads": 8,
        }
        assert _estimate_param_count(arch_small) < _estimate_param_count(arch_large)


class TestLoadArchParamsGqaDerivation:
    """F-P3-FABLE-58: a checkpoint that reports num_attention_heads but omits
    num_key_value_heads must derive kv heads == attention heads, not fall back
    to the old hardcoded default of 32."""

    def _stub_transformers(self, cfg_obj):
        transformers_stub = MagicMock()
        transformers_stub.AutoConfig.from_pretrained.return_value = cfg_obj
        return transformers_stub

    def test_missing_kv_heads_derives_from_attention_heads(self):
        from types import SimpleNamespace

        # SimpleNamespace WITHOUT num_key_value_heads → getattr(..., None) is None.
        cfg = SimpleNamespace(
            hidden_size=4096,
            num_hidden_layers=32,
            intermediate_size=11008,
            vocab_size=32000,
            num_attention_heads=64,
        )
        with patch.dict(sys.modules, {"transformers": self._stub_transformers(cfg)}):
            from forgelm.fit_check import _load_arch_params

            arch = _load_arch_params("org/exotic-model")
        assert arch["num_key_value_heads"] == 64, "kv heads must derive from attention heads, not default 32"

    def test_explicit_kv_heads_preserved(self):
        from types import SimpleNamespace

        cfg = SimpleNamespace(
            hidden_size=4096,
            num_hidden_layers=32,
            intermediate_size=11008,
            vocab_size=32000,
            num_attention_heads=32,
            num_key_value_heads=8,  # genuine GQA
        )
        with patch.dict(sys.modules, {"transformers": self._stub_transformers(cfg)}):
            from forgelm.fit_check import _load_arch_params

            arch = _load_arch_params("org/gqa-model")
        assert arch["num_key_value_heads"] == 8


def test_lora_adapter_gb_helper_removed():
    """F-P3-FABLE-59: the dead, drift-prone _lora_adapter_gb helper is gone."""
    import forgelm.fit_check as fc

    assert not hasattr(fc, "_lora_adapter_gb")


# ---------------------------------------------------------------------------
# VRAM component helpers
# ---------------------------------------------------------------------------


class TestBaseModelGb:
    def test_4bit_lower_than_bf16(self):
        from forgelm.fit_check import _base_model_gb

        params = 7_000_000_000
        assert _base_model_gb(params, "4bit") < _base_model_gb(params, "bf16")

    def test_fp32_highest(self):
        from forgelm.fit_check import _base_model_gb

        params = 7_000_000_000
        assert _base_model_gb(params, "fp32") > _base_model_gb(params, "bf16")

    def test_known_value(self):
        from forgelm.fit_check import _base_model_gb

        # 2 GiB = 2 × 1024³ bytes = exactly 1_073_741_824 params × 2 bytes (fp16)
        params = 2 * 1024**3 // 2  # = 1_073_741_824
        result = _base_model_gb(params, "fp16")
        assert abs(result - 2.0) < 0.001


class TestOptimizerStateGb:
    def test_adamw_8x(self):
        from forgelm.fit_check import _optimizer_state_gb

        # Exact: 100 × 1024² params × 8 bytes = 100 × 8 MiB = 800 MiB = 0.78125 GiB
        params = 100 * 1024**2  # exactly 100 MiB / 1 byte = 104_857_600 params
        result = _optimizer_state_gb(params, "adamw")
        expected = params * 8 / (1024**3)
        assert abs(result - expected) < 0.001

    def test_8bit_adam_lower(self):
        from forgelm.fit_check import _optimizer_state_gb

        params = 50_000_000
        assert _optimizer_state_gb(params, "galore_adamw_8bit") < _optimizer_state_gb(params, "adamw")


class TestActivationGb:
    def test_gradient_checkpointing_reduces_memory(self):
        from forgelm.fit_check import _activation_gb

        arch = {
            "hidden_size": 4096,
            "num_hidden_layers": 32,
            "intermediate_size": 11008,
            "vocab_size": 32000,
            "num_attention_heads": 32,
            "num_key_value_heads": 32,
        }
        without = _activation_gb(arch, batch_size=4, seq_len=2048, gradient_checkpointing=False)
        with_gc = _activation_gb(arch, batch_size=4, seq_len=2048, gradient_checkpointing=True)
        assert with_gc < without

    def test_larger_batch_more_memory(self):
        from forgelm.fit_check import _activation_gb

        arch = {
            "hidden_size": 4096,
            "num_hidden_layers": 32,
            "intermediate_size": 11008,
            "vocab_size": 32000,
            "num_attention_heads": 32,
            "num_key_value_heads": 32,
        }
        small = _activation_gb(arch, batch_size=1, seq_len=512, gradient_checkpointing=False)
        large = _activation_gb(arch, batch_size=8, seq_len=2048, gradient_checkpointing=False)
        assert large > small


# ---------------------------------------------------------------------------
# estimate_vram — integration tests with mocked torch + AutoConfig
# ---------------------------------------------------------------------------


class TestEstimateVramNoCuda:
    def test_returns_fit_check_result(self, minimal_config):
        torch_stub = _make_torch_no_cuda()

        auto_config_stub = MagicMock()
        auto_config_stub.from_pretrained.return_value = MagicMock(
            hidden_size=4096,
            num_hidden_layers=32,
            intermediate_size=11008,
            vocab_size=32000,
            num_attention_heads=32,
            num_key_value_heads=32,
        )
        transformers_stub = MagicMock()
        transformers_stub.AutoConfig = auto_config_stub

        with patch.dict(sys.modules, {"torch": torch_stub, "transformers": transformers_stub}):
            from forgelm.fit_check import estimate_vram

            cfg = ForgeConfig(**minimal_config(model={"name_or_path": "org/llama-7b"}))
            result = estimate_vram(cfg)

        assert result.verdict == "UNKNOWN"
        assert result.hypothetical is True
        assert result.estimated_gb > 0

    def test_breakdown_keys_present(self, minimal_config):
        torch_stub = _make_torch_no_cuda()
        auto_config_stub = MagicMock()
        auto_config_stub.from_pretrained.return_value = MagicMock(
            hidden_size=2048,
            num_hidden_layers=16,
            intermediate_size=5504,
            vocab_size=32000,
            num_attention_heads=16,
            num_key_value_heads=16,
        )
        transformers_stub = MagicMock()
        transformers_stub.AutoConfig = auto_config_stub

        with patch.dict(sys.modules, {"torch": torch_stub, "transformers": transformers_stub}):
            from forgelm.fit_check import estimate_vram

            cfg = ForgeConfig(**minimal_config(model={"name_or_path": "org/llama-7b"}))
            result = estimate_vram(cfg)

        assert "base_model_gb" in result.breakdown
        assert "lora_adapter_gb" in result.breakdown
        assert "optimizer_state_gb" in result.breakdown
        assert "activations_gb" in result.breakdown


class TestEstimateVramWithCuda:
    def test_fits_on_large_gpu(self, minimal_config):
        # 80 GB A100 — a 7B model in 4-bit should FITS easily
        torch_stub = _make_torch_with_cuda(total_bytes=80 * 1024**3)

        auto_config_stub = MagicMock()
        auto_config_stub.from_pretrained.return_value = MagicMock(
            hidden_size=4096,
            num_hidden_layers=32,
            intermediate_size=11008,
            vocab_size=32000,
            num_attention_heads=32,
            num_key_value_heads=32,
        )
        transformers_stub = MagicMock()
        transformers_stub.AutoConfig = auto_config_stub

        with patch.dict(sys.modules, {"torch": torch_stub, "transformers": transformers_stub}):
            from forgelm.fit_check import estimate_vram

            cfg = ForgeConfig(**minimal_config(model={"name_or_path": "llama-7b", "load_in_4bit": True}))
            result = estimate_vram(cfg)

        assert result.verdict == "FITS"
        # available_gb uses free_bytes (90% of 80 GiB in the mock = 72 GiB)
        assert result.available_gb == pytest.approx(72.0, abs=0.1)

    def test_oom_on_tiny_gpu(self, minimal_config):
        # 4 GB GPU — a large model should OOM
        torch_stub = _make_torch_with_cuda(total_bytes=4 * 1024**3)

        auto_config_stub = MagicMock()
        # Simulate a 70B-class model architecture
        auto_config_stub.from_pretrained.return_value = MagicMock(
            hidden_size=8192,
            num_hidden_layers=80,
            intermediate_size=28672,
            vocab_size=32000,
            num_attention_heads=64,
            num_key_value_heads=8,
        )
        transformers_stub = MagicMock()
        transformers_stub.AutoConfig = auto_config_stub

        with patch.dict(sys.modules, {"torch": torch_stub, "transformers": transformers_stub}):
            from forgelm.fit_check import estimate_vram

            cfg = ForgeConfig(**minimal_config(model={"name_or_path": "org/llama-7b"}))
            result = estimate_vram(cfg)

        assert result.verdict == "OOM"


class TestEstimateVramRecommendations:
    def test_recommendations_provided_when_tight(self, minimal_config):
        # 8 GB GPU — 7B model bf16 is tight/OOM
        torch_stub = _make_torch_with_cuda(total_bytes=8 * 1024**3)

        auto_config_stub = MagicMock()
        auto_config_stub.from_pretrained.return_value = MagicMock(
            hidden_size=4096,
            num_hidden_layers=32,
            intermediate_size=11008,
            vocab_size=32000,
            num_attention_heads=32,
            num_key_value_heads=32,
        )
        transformers_stub = MagicMock()
        transformers_stub.AutoConfig = auto_config_stub

        with patch.dict(sys.modules, {"torch": torch_stub, "transformers": transformers_stub}):
            from forgelm.fit_check import estimate_vram

            # No 4-bit → higher memory usage on a small GPU
            cfg = ForgeConfig(**minimal_config(model={"name_or_path": "llama-7b", "load_in_4bit": False}))
            result = estimate_vram(cfg)

        # Should have recommendations (could be OOM or TIGHT)
        if result.verdict in ("OOM", "TIGHT"):
            assert len(result.recommendations) > 0

    def test_no_recommendations_when_fits_comfortably(self, minimal_config):
        # 80 GB GPU, 4-bit quantized — should FITS with no recs
        torch_stub = _make_torch_with_cuda(total_bytes=80 * 1024**3)

        auto_config_stub = MagicMock()
        auto_config_stub.from_pretrained.return_value = MagicMock(
            hidden_size=4096,
            num_hidden_layers=32,
            intermediate_size=11008,
            vocab_size=32000,
            num_attention_heads=32,
            num_key_value_heads=32,
        )
        transformers_stub = MagicMock()
        transformers_stub.AutoConfig = auto_config_stub

        with patch.dict(sys.modules, {"torch": torch_stub, "transformers": transformers_stub}):
            from forgelm.fit_check import estimate_vram

            cfg = ForgeConfig(**minimal_config(model={"name_or_path": "llama-7b", "load_in_4bit": True}))
            result = estimate_vram(cfg)

        assert result.verdict == "FITS"
        assert result.recommendations == []


# ---------------------------------------------------------------------------
# format_fit_check output
# ---------------------------------------------------------------------------


class TestFormatFitCheck:
    def test_contains_verdict(self):
        from forgelm.fit_check import FitCheckResult, format_fit_check

        result = FitCheckResult(
            verdict="FITS",
            estimated_gb=8.2,
            available_gb=24.0,
            recommendations=[],
            breakdown={"base_model_gb": 4.0, "lora_adapter_gb": 0.1},
        )
        text = format_fit_check(result)
        assert "FITS" in text
        assert "8.2" in text
        assert "24.0" in text

    def test_contains_recommendations(self):
        from forgelm.fit_check import FitCheckResult, format_fit_check

        result = FitCheckResult(
            verdict="OOM",
            estimated_gb=30.0,
            available_gb=12.0,
            recommendations=["Reduce batch size", "Enable gradient checkpointing"],
            breakdown={},
        )
        text = format_fit_check(result)
        assert "Reduce batch size" in text
        assert "Enable gradient checkpointing" in text

    def test_hypothetical_mode_note(self):
        from forgelm.fit_check import FitCheckResult, format_fit_check

        result = FitCheckResult(
            verdict="UNKNOWN",
            estimated_gb=12.0,
            available_gb=None,
            hypothetical=True,
        )
        text = format_fit_check(result)
        # Verdict token must appear in the rendered output
        assert "UNKNOWN" in text
        # AND the hypothetical/no-GPU note must explicitly be present
        assert "not detected" in text or "hypothetical" in text


# ---------------------------------------------------------------------------
# _load_arch_params fallback
# ---------------------------------------------------------------------------


class TestLoadArchParams:
    def test_fallback_when_autoconfig_fails(self):
        """If AutoConfig raises, size hints are used as fallback."""
        transformers_stub = MagicMock()
        transformers_stub.AutoConfig.from_pretrained.side_effect = Exception("network error")

        with patch.dict(sys.modules, {"transformers": transformers_stub}):
            from forgelm.fit_check import _load_arch_params

            params = _load_arch_params("meta-llama/Llama-2-7b-hf")

        assert params["hidden_size"] > 0
        assert params["num_hidden_layers"] > 0
        # "7b" hint should match
        assert params["hidden_size"] == 4096

    def test_uses_config_when_available(self):
        mock_cfg = MagicMock()
        mock_cfg.hidden_size = 2048
        mock_cfg.num_hidden_layers = 24
        mock_cfg.intermediate_size = 8192
        mock_cfg.vocab_size = 50000
        mock_cfg.num_attention_heads = 16
        mock_cfg.num_key_value_heads = 16

        transformers_stub = MagicMock()
        transformers_stub.AutoConfig.from_pretrained.return_value = mock_cfg

        with patch.dict(sys.modules, {"transformers": transformers_stub}):
            from forgelm.fit_check import _load_arch_params

            params = _load_arch_params("org/custom-model")

        assert params["hidden_size"] == 2048
        assert params["vocab_size"] == 50000


# ---------------------------------------------------------------------------
# _resolve_quant_scheme — must mirror model.py's actual dtype load path
# ---------------------------------------------------------------------------


class TestResolveQuantScheme:
    """The base weights load under transformers' default ``dtype="auto"`` (model.py
    passes no explicit dtype), i.e. the checkpoint's native dtype.  The old code
    assumed a phantom ``bf16`` for every non-4bit run, halving the base-model
    footprint on fp32 checkpoints and risking a false FITS verdict → OOM."""

    def _model(self, **overrides):
        from forgelm.config import ModelConfig

        return ModelConfig(name_or_path="org/model", **overrides)

    def test_load_in_4bit_returns_4bit(self):
        from forgelm.fit_check import _resolve_quant_scheme

        assert _resolve_quant_scheme(self._model(load_in_4bit=True)) == "4bit"

    def test_no_dtype_known_defaults_to_fp32_not_bf16(self):
        # Unknown checkpoint dtype (config unavailable / no dtype entry) → the
        # conservative over-estimate, NOT the old silent bf16 that under-counted
        # base-model memory by 2x.
        from forgelm.fit_check import _resolve_quant_scheme

        assert _resolve_quant_scheme(self._model(load_in_4bit=False)) == "fp32"

    @pytest.mark.parametrize(
        "declared,expected",
        [
            ("torch.bfloat16", "bf16"),  # str() form of a torch.dtype, as AutoConfig yields
            ("torch.float16", "fp16"),
            ("torch.float32", "fp32"),
            ("bfloat16", "bf16"),  # plain-string form
            ("float32", "fp32"),
        ],
    )
    def test_declared_checkpoint_dtype_is_honored(self, declared, expected):
        from forgelm.fit_check import _resolve_quant_scheme

        assert _resolve_quant_scheme(self._model(load_in_4bit=False), declared_dtype=declared) == expected


class TestEstimateVramReadsCheckpointDtype:
    """End-to-end: estimate_vram must thread the HF config's declared dtype
    into the base-model estimate, so a fp32 checkpoint is estimated at ~2x the
    base memory of a bf16 checkpoint (rather than both assumed bf16)."""

    @staticmethod
    def _transformers_stub(dtype_str):
        auto_config_stub = MagicMock()
        auto_config_stub.from_pretrained.return_value = MagicMock(
            hidden_size=4096,
            num_hidden_layers=32,
            intermediate_size=11008,
            vocab_size=32000,
            num_attention_heads=32,
            num_key_value_heads=32,
            dtype=dtype_str,
        )
        transformers_stub = MagicMock()
        transformers_stub.AutoConfig = auto_config_stub
        return transformers_stub

    def test_fp32_checkpoint_base_is_double_bf16(self, minimal_config):
        torch_stub = _make_torch_no_cuda()
        from forgelm.fit_check import estimate_vram

        with patch.dict(sys.modules, {"torch": torch_stub, "transformers": self._transformers_stub("bfloat16")}):
            cfg = ForgeConfig(**minimal_config(model={"name_or_path": "org/m", "load_in_4bit": False}))
            r_bf16 = estimate_vram(cfg)

        with patch.dict(sys.modules, {"torch": torch_stub, "transformers": self._transformers_stub("float32")}):
            cfg = ForgeConfig(**minimal_config(model={"name_or_path": "org/m", "load_in_4bit": False}))
            r_fp32 = estimate_vram(cfg)

        assert r_bf16.breakdown["quant_scheme"] == "bf16"
        assert r_fp32.breakdown["quant_scheme"] == "fp32"
        assert r_fp32.breakdown["base_model_gb"] == pytest.approx(2 * r_bf16.breakdown["base_model_gb"], rel=0.01)
