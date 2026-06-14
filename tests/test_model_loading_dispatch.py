"""H1 model.py loading-dispatch correctness (F-P3-FABLE-07/09/10/11).

- MoE detection must recognise Qwen3-MoE (``num_experts``) and DeepSeek-V3
  (``n_routed_experts``), not just Mixtral/Phi (``num_local_experts``).
- The unsloth backend must honour ``lora.method`` (dora/rslora/pissa), not just
  the deprecated boolean shortcuts.
- ``load_in_4bit`` on a non-CUDA host must warn loudly, never silently skip.
- The multimodal AutoProcessor path must not crash on ``pad_token_id``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from forgelm import model as m

# --- F-P3-FABLE-07: MoE expert-count detection across architectures ----------


@pytest.mark.parametrize(
    ("attrs", "expected"),
    [
        ({"num_local_experts": 8}, 8),  # Mixtral / Phi-MoE
        ({"num_experts": 60}, 60),  # Qwen2/Qwen3-MoE
        ({"n_routed_experts": 256}, 256),  # DeepSeek-V3
        ({"hidden_size": 4096}, None),  # dense model
    ],
)
def test_resolve_expert_count(attrs, expected):
    assert m._resolve_expert_count(SimpleNamespace(**attrs)) == expected


def test_detect_moe_experts_qwen3_shape():
    """A Qwen3-MoE-shaped config (``num_experts``) must be detected — previously
    gated on ``num_local_experts`` and silently no-op'd."""
    model = MagicMock()
    model.config = SimpleNamespace(num_experts=60)
    config = SimpleNamespace(model=SimpleNamespace(moe=SimpleNamespace(quantize_experts=False, experts_to_train="0,1")))
    assert m._detect_moe_experts(model, config) == 60


def test_apply_moe_post_peft_calls_freeze(monkeypatch):
    """Expert selection runs POST-PEFT via _apply_moe_post_peft (F-P3-FABLE-02)."""
    called = {}
    monkeypatch.setattr(m, "_freeze_unselected_experts", lambda *a, **k: called.setdefault("freeze", a))
    monkeypatch.setattr(m, "_apply_moe_expert_quantization", lambda *a, **k: None)
    model = MagicMock()
    config = SimpleNamespace(model=SimpleNamespace(moe=SimpleNamespace(quantize_experts=False, experts_to_train="0,1")))
    m._apply_moe_post_peft(model, config, 60)
    assert "freeze" in called, "experts_to_train must reach _freeze_unselected_experts post-PEFT"


def test_detect_moe_experts_warns_when_not_moe(caplog):
    """moe configured but no recognised expert attribute → loud WARNING, returns None."""
    model = MagicMock()
    model.config = SimpleNamespace(hidden_size=4096)  # dense
    config = SimpleNamespace(model=SimpleNamespace(moe=SimpleNamespace(quantize_experts=True, experts_to_train="0")))
    with caplog.at_level("WARNING", logger="forgelm.model"):
        assert m._detect_moe_experts(model, config) is None
    assert any("no recognised MoE" in r.message for r in caplog.records)


# --- F-P3-FABLE-09: unsloth honours lora.method ------------------------------


def _peft_cfg(method, use_dora=False, use_rslora=False):
    return SimpleNamespace(lora=SimpleNamespace(method=method, use_dora=use_dora, use_rslora=use_rslora))


@pytest.mark.parametrize(
    ("method", "expected"),
    [("dora", (True, False, "dora")), ("rslora", (False, True, "rslora")), ("lora", (False, False, "lora"))],
)
def test_resolve_peft_flags(method, expected):
    assert m._resolve_peft_flags(_peft_cfg(method)) == expected


def test_unsloth_honours_dora_method(monkeypatch):
    """``method: dora`` + backend unsloth must pass ``use_dora=True`` to
    FastLanguageModel.get_peft_model (was silently plain LoRA)."""
    captured = {}

    fake_flm = MagicMock()
    fake_flm.from_pretrained.return_value = (MagicMock(), MagicMock())
    fake_flm.get_peft_model.side_effect = lambda model, **kw: captured.update(kw) or model
    fake_unsloth = SimpleNamespace(FastLanguageModel=fake_flm)
    monkeypatch.setitem(__import__("sys").modules, "unsloth", fake_unsloth)

    config = SimpleNamespace(
        model=SimpleNamespace(name_or_path="org/m", max_length=2048, load_in_4bit=False),
        lora=SimpleNamespace(
            method="dora",
            use_dora=False,
            use_rslora=False,
            r=8,
            target_modules=["q_proj"],
            alpha=16,
            dropout=0.0,
            bias="none",
        ),
    )
    m._load_unsloth(config)
    assert captured.get("use_dora") is True, "unsloth must honour lora.method='dora'"


def test_unsloth_pissa_raises(monkeypatch):
    """``method: pissa`` + unsloth must raise (no silent downgrade to LoRA)."""
    from forgelm.config import ConfigError

    fake_flm = MagicMock()
    fake_flm.from_pretrained.return_value = (MagicMock(), MagicMock())
    monkeypatch.setitem(__import__("sys").modules, "unsloth", SimpleNamespace(FastLanguageModel=fake_flm))

    config = SimpleNamespace(
        model=SimpleNamespace(name_or_path="org/m", max_length=2048, load_in_4bit=False),
        lora=SimpleNamespace(
            method="pissa",
            use_dora=False,
            use_rslora=False,
            r=8,
            target_modules=["q_proj"],
            alpha=16,
            dropout=0.0,
            bias="none",
        ),
    )
    with pytest.raises(ConfigError, match="pissa"):
        m._load_unsloth(config)


def test_unsloth_missing_dep_error_has_canonical_install_hint(monkeypatch):
    """F-P3-FABLE-52: the ImportError must carry the canonical extra-install
    hint (``pip install 'forgelm[unsloth]'``), not a bare 'Please install it.'."""
    # A stub module without FastLanguageModel makes `from unsloth import
    # FastLanguageModel` raise ImportError without needing unsloth uninstalled.
    monkeypatch.setitem(__import__("sys").modules, "unsloth", SimpleNamespace())

    config = SimpleNamespace(
        model=SimpleNamespace(name_or_path="org/m", max_length=2048, load_in_4bit=False),
        lora=SimpleNamespace(method="lora", use_dora=False, use_rslora=False),
    )
    with pytest.raises(ImportError, match=r"pip install 'forgelm\[unsloth\]'"):
        m._load_unsloth(config)


# --- F-P3-FABLE-10: load_in_4bit on non-CUDA warns ---------------------------


def test_load_in_4bit_without_cuda_warns_and_skips_quant(caplog):
    torch = pytest.importorskip("torch")
    config = SimpleNamespace(
        model=SimpleNamespace(
            load_in_4bit=True,
            bnb_4bit_compute_dtype="auto",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_quant_type="nf4",
        ),
        training=SimpleNamespace(rope_scaling=None, sliding_window_attention=None),
        distributed=None,
    )
    with patch.object(torch.cuda, "is_available", return_value=False):
        with caplog.at_level("WARNING", logger="forgelm.model"):
            kwargs = m._build_model_kwargs(config, trust_remote_code=False)
    assert "quantization_config" not in kwargs
    assert any("CUDA is unavailable" in r.message for r in caplog.records)


# --- F-P3-FABLE-11: VLM AutoProcessor pad handling ---------------------------


def test_pad_handling_uses_inner_tokenizer_for_processor(monkeypatch):
    """A processor (no pad_token_id of its own) must have pad_token resolved on
    its inner ``.tokenizer`` rather than raising AttributeError."""
    inner = SimpleNamespace(pad_token=None, eos_token="<eos>")
    processor = SimpleNamespace(tokenizer=inner)  # no pad_token attribute on the processor itself

    fake_proc_cls = MagicMock()
    fake_proc_cls.from_pretrained.return_value = processor
    monkeypatch.setitem(
        __import__("sys").modules,
        "transformers",
        SimpleNamespace(AutoProcessor=fake_proc_cls, AutoTokenizer=MagicMock()),
    )

    config = SimpleNamespace(model=SimpleNamespace(name_or_path="org/vlm", multimodal=SimpleNamespace(enabled=True)))
    tok = m._load_tokenizer(config, trust_remote_code=False)
    assert tok is processor
    assert inner.pad_token == "<eos>", "inner tokenizer pad_token must be set from eos_token"
