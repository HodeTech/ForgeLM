"""Alignment configuration, real ORPO/SimPO construction, and TRL import compatibility."""

import importlib
import logging
import sys
import warnings
from types import ModuleType

import pytest
import yaml

from forgelm.config import ForgeConfig, TrainingConfig, load_config

# --- TrainingConfig trainer_type ---


class TestTrainerTypeConfig:
    def test_default_is_sft(self):
        t = TrainingConfig()
        assert t.trainer_type == "sft"

    def test_all_valid_types(self, minimal_config):
        for tt in ["sft", "orpo", "dpo", "simpo", "kto", "grpo"]:
            cfg = ForgeConfig(**minimal_config(training={"trainer_type": tt}))
            assert cfg.training.trainer_type == tt

    def test_invalid_trainer_type_raises(self, minimal_config):
        with pytest.raises((ValueError, TypeError)):
            ForgeConfig(**minimal_config(training={"trainer_type": "invalid"}))

    def test_dpo_parameters(self):
        t = TrainingConfig(trainer_type="dpo", dpo_beta=0.2)
        assert t.dpo_beta == pytest.approx(0.2)

    def test_simpo_parameters(self):
        t = TrainingConfig(trainer_type="simpo", simpo_gamma=1.0, simpo_beta=3.0)
        assert t.simpo_gamma == pytest.approx(1.0)
        assert t.simpo_beta == pytest.approx(3.0)

    def test_kto_parameters(self):
        t = TrainingConfig(trainer_type="kto", kto_beta=0.05)
        assert t.kto_beta == pytest.approx(0.05)

    def test_grpo_parameters(self):
        # Legacy alias `grpo_max_new_tokens` must still be accepted on input;
        # the canonical attribute is `grpo_max_completion_length` (matches TRL).
        t = TrainingConfig(
            trainer_type="grpo",
            grpo_num_generations=8,
            grpo_max_new_tokens=1024,
        )
        assert t.grpo_num_generations == 8
        assert t.grpo_max_completion_length == 1024

    def test_grpo_defaults(self):
        t = TrainingConfig(trainer_type="grpo")
        assert t.grpo_num_generations == 4
        assert t.grpo_max_completion_length == 512


# --- Full config with alignment ---


class TestAlignmentFullConfig:
    def test_dpo_config_from_yaml(self, tmp_path, minimal_config):
        data = minimal_config(
            training={
                "trainer_type": "dpo",
                "dpo_beta": 0.15,
                "learning_rate": 5e-6,
            }
        )
        cfg_path = str(tmp_path / "config.yaml")
        with open(cfg_path, "w") as f:
            yaml.dump(data, f)
        cfg = load_config(cfg_path)
        assert cfg.training.trainer_type == "dpo"
        assert cfg.training.dpo_beta == pytest.approx(0.15)

    def test_simpo_config_from_yaml(self, tmp_path, minimal_config):
        data = minimal_config(
            training={
                "trainer_type": "simpo",
                "simpo_gamma": 0.8,
            }
        )
        cfg_path = str(tmp_path / "config.yaml")
        with open(cfg_path, "w") as f:
            yaml.dump(data, f)
        cfg = load_config(cfg_path)
        assert cfg.training.trainer_type == "simpo"
        assert cfg.training.simpo_gamma == pytest.approx(0.8)

    def test_grpo_config_from_yaml(self, tmp_path, minimal_config):
        data = minimal_config(
            training={
                "trainer_type": "grpo",
                "grpo_num_generations": 6,
                "grpo_max_new_tokens": 256,
            }
        )
        cfg_path = str(tmp_path / "config.yaml")
        with open(cfg_path, "w") as f:
            yaml.dump(data, f)
        cfg = load_config(cfg_path)
        assert cfg.training.trainer_type == "grpo"
        assert cfg.training.grpo_num_generations == 6


# --- Dry-run with alignment trainers ---


class TestDryRunAlignment:
    def test_dry_run_shows_trainer_type(self, capsys, minimal_config):
        from forgelm.cli import _run_dry_run

        cfg = ForgeConfig(**minimal_config(training={"trainer_type": "dpo"}))
        _run_dry_run(cfg, "json")
        import json

        result = json.loads(capsys.readouterr().out)
        assert result["status"] == "valid"

    def test_dry_run_grpo(self, capsys, minimal_config):
        from forgelm.cli import _run_dry_run

        cfg = ForgeConfig(**minimal_config(training={"trainer_type": "grpo"}))
        _run_dry_run(cfg, "json")
        import json

        result = json.loads(capsys.readouterr().out)
        assert result["status"] == "valid"


# --- Config template ---


class TestConfigTemplateAlignment:
    def test_config_template_still_valid(self):
        """Ensure config_template.yaml still parses after alignment changes."""
        import os

        template_path = os.path.join(os.path.dirname(__file__), "..", "config_template.yaml")
        if os.path.exists(template_path):
            cfg = load_config(template_path)
            assert cfg.training.trainer_type == "sft"


@pytest.mark.parametrize(
    ("trainer_type", "config_name", "trainer_name", "beta"),
    [("orpo", "ORPOConfig", "ORPOTrainer", 0.17), ("simpo", "CPOConfig", "CPOTrainer", 2.7)],
)
@pytest.mark.parametrize(("warmup_ratio", "expected_warmup_steps"), [(0.0, 0), (0.03, 4), (0.4, 41), (1.0, 101)])
def test_real_preference_trainer_construction(
    tmp_path,
    minimal_config,
    monkeypatch,
    trainer_type,
    config_name,
    trainer_name,
    beta,
    warmup_ratio,
    expected_warmup_steps,
):
    """Build real TRL configs/trainers with a local CPU model and tokenizer.

    No Hub fixtures, fake TRL classes, or importability skips: upstream API
    drift must fail this test on every Python version in the CI matrix.
    """
    import torch
    from datasets import Dataset
    from tokenizers import Tokenizer
    from tokenizers.models import WordLevel
    from tokenizers.pre_tokenizers import Whitespace
    from transformers import GPT2Config, GPT2LMHeadModel, PreTrainedTokenizerFast

    from forgelm.trainer import ForgeTrainer

    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    torch.manual_seed(42)
    vocab = {word: index for index, word in enumerate(["<unk>", "<pad>", "<eos>", "Hello", "good", "bad"])}
    backend = Tokenizer(WordLevel(vocab, unk_token="<unk>"))
    backend.pre_tokenizer = Whitespace()
    tokenizer = PreTrainedTokenizerFast(
        tokenizer_object=backend, unk_token="<unk>", pad_token="<pad>", eos_token="<eos>"
    )
    model = GPT2LMHeadModel(
        GPT2Config(
            vocab_size=len(vocab),
            n_positions=64,
            n_embd=8,
            n_layer=1,
            n_head=1,
            bos_token_id=2,
            eos_token_id=2,
            pad_token_id=1,
        )
    )
    config = ForgeConfig.model_validate(
        minimal_config(
            model={"name_or_path": "local-test-model", "max_length": 64},
            training={
                "trainer_type": trainer_type,
                "output_dir": str(tmp_path),
                "report_to": "none",
                "orpo_beta": beta,
                "simpo_beta": beta,
                "simpo_gamma": 0.8,
                "warmup_ratio": warmup_ratio,
            },
        )
    )
    wrapper = ForgeTrainer.__new__(ForgeTrainer)
    wrapper.config = config
    wrapper.model = model
    wrapper.tokenizer = tokenizer
    wrapper.dataset = {"train": Dataset.from_list([{"prompt": "Hello", "chosen": "good", "rejected": "bad"}])}
    wrapper.checkpoint_dir = str(tmp_path)
    wrapper.run_name = "real-preference-construction"
    wrapper._build_trainer(callbacks=[])

    actual = wrapper.trainer
    assert type(actual).__name__ == trainer_name
    assert type(actual.args).__name__ == config_name
    assert actual.model is model
    assert actual.processing_class is tokenizer
    assert actual.args.use_cpu is True
    assert actual.args.max_length == 64
    assert actual.args.beta == pytest.approx(beta)
    assert actual.args.warmup_steps == pytest.approx(warmup_ratio)
    assert 0.0 <= actual.args.warmup_steps < 1.0
    assert actual.args.get_warmup_steps(101) == expected_warmup_steps
    assert len(actual.train_dataset) == 1
    if trainer_type == "simpo":
        assert actual.args.loss_type == "simpo"
        assert actual.args.cpo_alpha == 0.0
        assert actual.args.simpo_gamma == pytest.approx(0.8)


_PREFERENCE_SYMBOLS = [
    ("ORPOConfig", "trl.experimental.orpo"),
    ("ORPOTrainer", "trl.experimental.orpo"),
    ("CPOConfig", "trl.experimental.cpo"),
    ("CPOTrainer", "trl.experimental.cpo"),
]


@pytest.mark.parametrize(("name", "experimental"), _PREFERENCE_SYMBOLS)
def test_real_installed_preference_symbols(name, experimental):
    """Resolve actual installed classes, without mocks, downloads, or skips."""
    from transformers import Trainer, TrainingArguments

    from forgelm._trl_compat import _resolve_preference_symbol

    resolved = _resolve_preference_symbol(name)
    trl = importlib.import_module("trl")
    expected = getattr(trl, name, None)
    if expected is None:
        expected = getattr(importlib.import_module(experimental), name)
    assert resolved is expected
    assert resolved.__name__ == name
    assert issubclass(resolved, TrainingArguments if name.endswith("Config") else Trainer)


def _fake_trl_exports(monkeypatch, experimental):
    """Isolate only the third-party import surface for resolver boundary tests."""
    top = ModuleType("trl")
    top.__version__ = "test-version"
    parent = ModuleType("trl.experimental")
    parent.__path__ = []
    fallback = ModuleType(experimental)
    for module in (top, parent, fallback):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    return top, fallback


def test_preference_resolver_prefers_top_level(monkeypatch, caplog):
    from forgelm._trl_compat import _resolve_preference_symbol

    top, fallback = _fake_trl_exports(monkeypatch, "trl.experimental.orpo")
    top.ORPOTrainer = type("StableTrainer", (), {})
    fallback.ORPOTrainer = type("ExperimentalTrainer", (), {})
    with caplog.at_level(logging.INFO, logger="forgelm._trl_compat"):
        assert _resolve_preference_symbol("ORPOTrainer") is top.ORPOTrainer
    assert not caplog.records


def test_preference_resolver_accepts_patch_on_real_trl_module(monkeypatch):
    from forgelm._trl_compat import _resolve_preference_symbol

    trl = importlib.import_module("trl")
    exported = type("PatchedTrainer", (), {})
    monkeypatch.setattr(trl, "ORPOTrainer", exported, raising=False)
    assert _resolve_preference_symbol("ORPOTrainer") is exported


@pytest.mark.parametrize(("name", "experimental"), _PREFERENCE_SYMBOLS)
@pytest.mark.parametrize("location", ["top", "experimental"])
def test_preference_resolver_accepts_dynamic_export_missing_from_dir(monkeypatch, name, experimental, location):
    from forgelm._trl_compat import _resolve_preference_symbol

    top, fallback = _fake_trl_exports(monkeypatch, experimental)
    module = top if location == "top" else fallback
    exported = type(name, (), {})

    def dynamic_export(attr):
        if attr == name:
            return exported
        raise AttributeError(attr, name=attr, obj=module)

    module.__getattr__ = dynamic_export
    module.__dir__ = lambda: []
    assert _resolve_preference_symbol(name) is exported


@pytest.mark.parametrize("location", ["top", "experimental"])
def test_preference_resolver_keeps_transitive_attribute_error_for_unlisted_export(monkeypatch, location):
    from forgelm._trl_compat import _resolve_preference_symbol

    top, fallback = _fake_trl_exports(monkeypatch, "trl.experimental.orpo")
    module = top if location == "top" else fallback
    error = AttributeError("dependency attribute missing", name="dependency_setting", obj=object())

    def broken_export(attr):
        if attr == "ORPOTrainer":
            raise error
        raise AttributeError(attr, name=attr, obj=module)

    module.__getattr__ = broken_export
    module.__dir__ = lambda: []
    with pytest.raises(AttributeError, match="dependency attribute missing") as raised:
        _resolve_preference_symbol("ORPOTrainer")
    assert raised.value is error


@pytest.mark.parametrize(("name", "experimental"), _PREFERENCE_SYMBOLS)
def test_preference_resolver_fallback_keeps_warning_and_logs_version(monkeypatch, caplog, name, experimental):
    from forgelm._trl_compat import _resolve_preference_symbol

    _, fallback = _fake_trl_exports(monkeypatch, experimental)
    exported = type(name, (), {})

    def lazy_export(attr):
        if attr == name:
            warnings.warn("upstream experimental API", UserWarning, stacklevel=2)
            return exported
        raise AttributeError(attr, name=attr, obj=fallback)

    fallback.__getattr__ = lazy_export
    fallback.__dir__ = lambda: [name]
    with (
        caplog.at_level(logging.INFO, logger="forgelm._trl_compat"),
        pytest.warns(UserWarning, match="upstream experimental"),
    ):
        assert _resolve_preference_symbol(name) is exported
    assert f"{experimental}.{name}" in caplog.text
    assert "TRL test-version" in caplog.text
    assert "may change or be removed" in caplog.text


@pytest.mark.parametrize("missing", ["export", "module", "parent"])
def test_preference_resolver_reports_missing_exports(monkeypatch, missing):
    from forgelm._trl_compat import _resolve_preference_symbol
    from forgelm.config import ConfigError

    _fake_trl_exports(monkeypatch, "trl.experimental.orpo")
    if missing != "export":
        monkeypatch.setitem(sys.modules, "trl.experimental.orpo", None)
    if missing == "parent":
        monkeypatch.delitem(sys.modules, "trl.experimental.orpo")
        monkeypatch.setitem(sys.modules, "trl.experimental", None)
    with pytest.raises(ConfigError, match="TRL test-version exports neither") as raised:
        _resolve_preference_symbol("ORPOConfig")
    assert "trl.ORPOConfig" in str(raised.value)
    assert "trl.experimental.orpo.ORPOConfig" in str(raised.value)
    assert "trl>=1.0.0,<2.0.0" in str(raised.value)
    assert "training.trainer_type" in str(raised.value)


@pytest.mark.parametrize("location", ["top", "experimental"])
@pytest.mark.parametrize("error_type", [ImportError, ModuleNotFoundError, RuntimeError, AttributeError])
def test_preference_resolver_preserves_dependency_errors(monkeypatch, location, error_type):
    from forgelm._trl_compat import _resolve_preference_symbol

    top, fallback = _fake_trl_exports(monkeypatch, "trl.experimental.orpo")
    broken = top if location == "top" else fallback
    error = error_type("dependency failed to load")

    def broken_export(attr):
        if attr == "ORPOTrainer":
            raise error
        raise AttributeError(attr, name=attr, obj=broken)

    broken.__getattr__ = broken_export
    broken.__dir__ = lambda: ["ORPOTrainer"]
    with pytest.raises(error_type, match="dependency failed to load") as raised:
        _resolve_preference_symbol("ORPOTrainer")
    assert raised.value is error


def test_preference_resolver_preserves_missing_transitive_module(tmp_path, monkeypatch):
    from forgelm._trl_compat import _resolve_preference_symbol

    _fake_trl_exports(monkeypatch, "trl.experimental.orpo")
    monkeypatch.delitem(sys.modules, "trl.experimental.orpo")
    sys.modules["trl.experimental"].__path__ = [str(tmp_path)]
    (tmp_path / "orpo.py").write_text("import missing_trl_test_dependency\n", encoding="utf-8")
    with pytest.raises(ModuleNotFoundError) as raised:
        _resolve_preference_symbol("ORPOConfig")
    assert raised.value.name == "missing_trl_test_dependency"
