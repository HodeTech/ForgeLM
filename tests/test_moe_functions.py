"""Unit tests for MoE expert quantization and freezing functions."""

from unittest.mock import MagicMock

import pytest

torch = pytest.importorskip("torch")

from forgelm.model import _apply_moe_expert_quantization, _freeze_unselected_experts


def _make_mock_model(num_experts=4):
    """Mock a PEFT-wrapped MoE model: a trainable LoRA adapter param under each
    expert (the post-``get_peft_model`` reality the freeze now targets) plus a
    frozen base expert weight module for the quantization sweep."""
    model = MagicMock()
    model.is_loaded_in_4bit = False  # not quantised → quantization pass runs
    params = {}
    modules = {}

    for layer in range(2):
        for expert_idx in range(num_experts):
            # Trainable LoRA adapter param injected under each expert.
            lname = f"base_model.model.layers.{layer}.mlp.experts.{expert_idx}.up_proj.lora_A.default.weight"
            params[lname] = torch.randn(8, 16, requires_grad=True)

            # Frozen base expert weight module (eligible for the half-precision recast).
            mname = f"model.layers.{layer}.mlp.experts.{expert_idx}.up_proj"
            mod = MagicMock()
            w = torch.randn(16, 16)
            w.requires_grad = False
            mod.weight = w
            modules[mname] = mod

    model.named_parameters.return_value = list(params.items())
    model.named_modules.return_value = list(modules.items())
    return model, params


class TestApplyMoeExpertQuantization:
    def test_runs_without_error(self):
        model, _ = _make_mock_model(4)
        # Should not raise
        _apply_moe_expert_quantization(model)

    def test_4bit_model_skips_quantization(self, caplog):
        """F-P3-FABLE-08: a 4-bit-loaded model's expert weights are packed
        Params4bit; recasting them corrupts the quant state. The pass must skip."""
        import logging

        model, _ = _make_mock_model(4)
        model.is_loaded_in_4bit = True
        # Make the modules' weights look like packed 4-bit storage too.
        for _name, mod in model.named_modules.return_value:
            mod.weight = torch.zeros(4, 4, dtype=torch.uint8)
        with caplog.at_level(logging.INFO, logger="forgelm.model"):
            _apply_moe_expert_quantization(model)
        assert "already loaded in 4-bit" in caplog.text
        # Nothing recast (still uint8).
        for _name, mod in model.named_modules.return_value:
            assert mod.weight.dtype == torch.uint8

    def test_logs_info(self, caplog):
        import logging

        model, _ = _make_mock_model(4)
        with caplog.at_level(logging.INFO, logger="forgelm.model"):
            _apply_moe_expert_quantization(model)
        # Should log something about quantization
        assert "quantization" in caplog.text.lower() or "expert" in caplog.text.lower()


class TestFreezeUnselectedExperts:
    def test_freezes_unselected(self):
        model, params = _make_mock_model(4)
        _freeze_unselected_experts(model, "0,1", 4)

        # Experts 2 and 3 should be frozen (requires_grad=False)
        frozen_count = sum(1 for _, p in params.items() if not p.requires_grad)
        # Experts 0,1 remain trainable; experts 2,3 frozen
        assert frozen_count > 0

    def test_all_experts_no_freeze(self):
        model, params = _make_mock_model(4)
        _freeze_unselected_experts(model, "0,1,2,3", 4)

        # All selected — nothing should be frozen
        frozen_count = sum(1 for _, p in params.items() if not p.requires_grad)
        assert frozen_count == 0

    def test_invalid_format_warns(self, caplog):
        import logging

        model, _ = _make_mock_model(4)
        with caplog.at_level(logging.WARNING, logger="forgelm.model"):
            _freeze_unselected_experts(model, "abc,def", 4)
        assert "Invalid experts_to_train" in caplog.text

    def test_out_of_range_indices_warns(self, caplog):
        import logging

        model, _ = _make_mock_model(4)
        with caplog.at_level(logging.WARNING, logger="forgelm.model"):
            _freeze_unselected_experts(model, "0,1,99", 4)
        assert "exceed" in caplog.text.lower() or "99" in caplog.text

    def test_single_expert(self):
        model, params = _make_mock_model(4)
        _freeze_unselected_experts(model, "2", 4)

        # Only expert 2 trainable — experts 0,1,3 frozen
        frozen_count = sum(1 for _, p in params.items() if not p.requires_grad)
        assert frozen_count > 0

    def test_selection_constrains_adapter_set(self):
        """F-P3-FABLE-02 core invariant: the trainable adapter set must DIFFER
        between a restricted selection and 'all'. Previously it was invariant."""
        model_sel, params_sel = _make_mock_model(4)
        _freeze_unselected_experts(model_sel, "0", 4)
        trainable_sel = {n for n, p in params_sel.items() if p.requires_grad}

        model_all, params_all = _make_mock_model(4)
        # "all" path: the helper isn't called (experts_to_train == "all") — every
        # adapter stays trainable.
        trainable_all = {n for n, p in params_all.items() if p.requires_grad}

        assert trainable_sel != trainable_all, "expert selection must constrain the trainable adapters"
        assert all("experts.0." in n for n in trainable_sel), "only expert 0 adapters remain trainable"

    def test_no_expert_adapters_warns(self, caplog):
        """If lora.target_modules doesn't cover experts (no expert adapters
        injected), expert selection has no effect → loud WARNING."""
        import logging

        model = MagicMock()
        # Only non-expert adapters present.
        model.named_parameters.return_value = [
            ("base_model.model.layers.0.self_attn.q_proj.lora_A.default.weight", torch.randn(4, 4, requires_grad=True))
        ]
        model.peft_config = MagicMock(target_modules=["q_proj", "v_proj"])
        with caplog.at_level(logging.WARNING, logger="forgelm.model"):
            _freeze_unselected_experts(model, "0,1", 4)
        assert "no effect" in caplog.text.lower()
