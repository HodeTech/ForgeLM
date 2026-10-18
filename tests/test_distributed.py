"""Unit tests for distributed training configuration (DeepSpeed/FSDP)."""

import json
import os

import pytest
import yaml
from pydantic import ValidationError

from forgelm.config import (
    DistributedConfig,
    ForgeConfig,
    load_config,
)

# --- DistributedConfig ---


class TestDistributedConfig:
    def test_defaults(self):
        d = DistributedConfig()
        assert d.strategy is None
        assert d.deepspeed_config is None
        assert d.fsdp_strategy == "full_shard"
        assert d.fsdp_auto_wrap is True
        assert d.fsdp_offload is False

    def test_deepspeed_strategy(self):
        d = DistributedConfig(strategy="deepspeed", deepspeed_config="zero2")
        assert d.strategy == "deepspeed"
        assert d.deepspeed_config == "zero2"

    def test_fsdp_strategy(self):
        d = DistributedConfig(strategy="fsdp", fsdp_strategy="shard_grad_op")
        assert d.strategy == "fsdp"
        assert d.fsdp_strategy == "shard_grad_op"

    def test_fsdp_with_offload(self):
        d = DistributedConfig(strategy="fsdp", fsdp_offload=True)
        assert d.fsdp_offload is True

    @pytest.mark.parametrize("bad_strategy", ["DeepSpeed", "ddp", "fsdpp", "horovod", "FSDP"])
    def test_strategy_unknown_value_raises(self, bad_strategy):
        # The field is a Literal["deepspeed", "fsdp"]; an unsupported value used
        # to validate and then silently run single-GPU at trainer.py (logger
        # "Unknown distributed strategy ... Ignoring.").  It must now fail at
        # config time.
        with pytest.raises(ValidationError):
            DistributedConfig(strategy=bad_strategy)

    def test_strategy_none_still_allowed(self):
        assert DistributedConfig(strategy=None).strategy is None


# --- ForgeConfig with distributed ---


class TestForgeConfigDistributed:
    def test_no_distributed(self, minimal_config):
        cfg = ForgeConfig(**minimal_config())
        assert cfg.distributed is None

    def test_deepspeed_config(self, minimal_config):
        cfg = ForgeConfig(**minimal_config(distributed={"strategy": "deepspeed", "deepspeed_config": "zero3"}))
        assert cfg.distributed.strategy == "deepspeed"
        assert cfg.distributed.deepspeed_config == "zero3"

    def test_fsdp_config(self, minimal_config):
        cfg = ForgeConfig(**minimal_config(distributed={"strategy": "fsdp", "fsdp_strategy": "shard_grad_op"}))
        assert cfg.distributed.strategy == "fsdp"
        assert cfg.distributed.fsdp_strategy == "shard_grad_op"

    def test_unsloth_distributed_raises(self, minimal_config):
        """Unsloth + distributed should raise ValueError."""
        import pytest

        with pytest.raises((ValueError, TypeError)):
            ForgeConfig(
                **minimal_config(
                    model={"name_or_path": "org/model", "backend": "unsloth"},
                    distributed={"strategy": "deepspeed"},
                )
            )

    def test_zero3_qlora_warning(self, caplog, minimal_config):
        """QLoRA + ZeRO-3 should produce a warning."""
        import logging

        with caplog.at_level(logging.WARNING, logger="forgelm.config"):
            ForgeConfig(
                **minimal_config(
                    model={"name_or_path": "org/model", "load_in_4bit": True},
                    distributed={"strategy": "deepspeed", "deepspeed_config": "zero3"},
                )
            )
        assert "QLoRA (4-bit) with DeepSpeed ZeRO-3" in caplog.text

    def test_zero2_qlora_no_warning(self, caplog, minimal_config):
        """QLoRA + ZeRO-2 should NOT produce the ZeRO-3 warning."""
        import logging

        with caplog.at_level(logging.WARNING, logger="forgelm.config"):
            ForgeConfig(
                **minimal_config(
                    model={"name_or_path": "org/model", "load_in_4bit": True},
                    distributed={"strategy": "deepspeed", "deepspeed_config": "zero2"},
                )
            )
        assert "QLoRA (4-bit) with DeepSpeed ZeRO-3" not in caplog.text


# --- YAML load with distributed ---


class TestLoadConfigDistributed:
    def test_load_deepspeed_yaml(self, tmp_path, minimal_config):
        data = minimal_config(distributed={"strategy": "deepspeed", "deepspeed_config": "zero2"})
        cfg_path = str(tmp_path / "config.yaml")
        with open(cfg_path, "w") as f:
            yaml.dump(data, f)
        cfg = load_config(cfg_path)
        assert cfg.distributed.strategy == "deepspeed"

    def test_load_fsdp_yaml(self, tmp_path, minimal_config):
        data = minimal_config(
            distributed={
                "strategy": "fsdp",
                "fsdp_strategy": "full_shard",
                "fsdp_offload": True,
            }
        )
        cfg_path = str(tmp_path / "config.yaml")
        with open(cfg_path, "w") as f:
            yaml.dump(data, f)
        cfg = load_config(cfg_path)
        assert cfg.distributed.strategy == "fsdp"
        assert cfg.distributed.fsdp_offload is True


# --- DeepSpeed config resolution ---


class TestDeepSpeedConfigResolution:
    def test_preset_files_exist(self):
        """Verify that all DeepSpeed preset JSON files are valid."""
        configs_dir = os.path.join(os.path.dirname(__file__), "..", "configs", "deepspeed")
        for preset in ["zero2.json", "zero3.json", "zero3_offload.json"]:
            path = os.path.join(configs_dir, preset)
            assert os.path.isfile(path), f"Missing preset: {path}"
            with open(path) as f:
                data = json.load(f)
            assert "zero_optimization" in data
            assert "stage" in data["zero_optimization"]

    def test_zero2_is_stage_2(self):
        path = os.path.join(os.path.dirname(__file__), "..", "configs", "deepspeed", "zero2.json")
        with open(path) as f:
            data = json.load(f)
        assert data["zero_optimization"]["stage"] == 2

    def test_zero3_is_stage_3(self):
        path = os.path.join(os.path.dirname(__file__), "..", "configs", "deepspeed", "zero3.json")
        with open(path) as f:
            data = json.load(f)
        assert data["zero_optimization"]["stage"] == 3

    def test_zero3_offload_has_cpu_offload(self):
        path = os.path.join(os.path.dirname(__file__), "..", "configs", "deepspeed", "zero3_offload.json")
        with open(path) as f:
            data = json.load(f)
        assert data["zero_optimization"]["stage"] == 3
        assert data["zero_optimization"]["offload_optimizer"]["device"] == "cpu"
        assert data["zero_optimization"]["offload_param"]["device"] == "cpu"

    def test_all_presets_use_auto_values(self):
        """Ensure presets use 'auto' so HF Trainer resolves values from TrainingArguments."""
        configs_dir = os.path.join(os.path.dirname(__file__), "..", "configs", "deepspeed")
        for preset in ["zero2.json", "zero3.json", "zero3_offload.json"]:
            with open(os.path.join(configs_dir, preset)) as f:
                data = json.load(f)
            assert data["train_batch_size"] == "auto"
            assert data["train_micro_batch_size_per_gpu"] == "auto"
            assert data["gradient_accumulation_steps"] == "auto"

    def test_missing_custom_path_raises_config_error_not_filenotfound(self, tmp_path):
        """A missing custom ``deepspeed_config`` path is an operator-fixable YAML
        mistake → ``ConfigError`` (exit 1 at the CLI), not ``FileNotFoundError``
        which the generic top-of-CLI catch maps to exit 2 (F-P2-FAB-25)."""
        from forgelm.config import ConfigError
        from forgelm.trainer import ForgeTrainer

        trainer = ForgeTrainer.__new__(ForgeTrainer)
        missing = str(tmp_path / "does_not_exist.json")
        with pytest.raises(ConfigError) as exc_info:
            trainer._resolve_deepspeed_config(missing)
        assert "deepspeed_config" in str(exc_info.value)
        # FileNotFoundError is an OSError subclass; ConfigError must NOT be one.
        assert not isinstance(exc_info.value, FileNotFoundError)

    def test_unknown_preset_raises_config_error(self):
        """An unknown preset name (typo) is also a config-class failure."""
        from forgelm.config import ConfigError
        from forgelm.trainer import ForgeTrainer

        trainer = ForgeTrainer.__new__(ForgeTrainer)
        with pytest.raises(ConfigError):
            trainer._resolve_deepspeed_config("zero99")

    def test_valid_preset_still_resolves(self):
        """The happy path is unchanged: a real preset resolves to a file path."""
        from forgelm.trainer import ForgeTrainer

        trainer = ForgeTrainer.__new__(ForgeTrainer)
        resolved = trainer._resolve_deepspeed_config("zero2")
        assert resolved.endswith("zero2.json")
        assert os.path.isfile(resolved)


# --- Dry-run with distributed ---


class TestDryRunDistributed:
    def test_dry_run_json_shows_distributed(self, tmp_path, capsys, minimal_config):
        from forgelm.cli import _run_dry_run

        cfg = ForgeConfig(**minimal_config(distributed={"strategy": "deepspeed", "deepspeed_config": "zero3"}))
        _run_dry_run(cfg, "json")
        result = json.loads(capsys.readouterr().out)
        assert result["distributed"] == "deepspeed"

    def test_dry_run_json_no_distributed(self, capsys, minimal_config):
        from forgelm.cli import _run_dry_run

        cfg = ForgeConfig(**minimal_config())
        _run_dry_run(cfg, "json")
        result = json.loads(capsys.readouterr().out)
        assert result["distributed"] is None


# --- Missing-DeepSpeed diagnostics ---


class TestDeepSpeedAvailabilityCheck:
    """ForgeLM never imports DeepSpeed itself — it hands ``deepspeed=<config>``
    to ``TrainingArguments``. Without this check the missing-dependency error
    surfaces from inside Transformers, naming neither ForgeLM's extra nor the
    config key that asked for it.

    The check matters more than it used to: the ``distributed`` extra now
    carries ``sys_platform != 'win32'`` (DeepSpeed publishes no Windows
    wheels, and an unmarked requirement failed the *whole* install there on a
    source build), so on Windows ``pip install forgelm[distributed]``
    succeeds with DeepSpeed absent and this is the first place the operator
    can learn that.
    """

    @staticmethod
    def _without_deepspeed(monkeypatch):
        import builtins

        real = builtins.__import__

        def fake(name, *args, **kwargs):
            if name == "deepspeed":
                raise ImportError("No module named 'deepspeed'")
            return real(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake)

    def test_raises_import_error_naming_the_extra(self, monkeypatch):
        from forgelm.trainer import ForgeTrainer

        self._without_deepspeed(monkeypatch)
        with pytest.raises(ImportError) as exc:
            ForgeTrainer._check_deepspeed_available()
        message = str(exc.value)
        assert "pip install forgelm[distributed]" in message
        assert "distributed.strategy='deepspeed'" in message

    def test_message_points_windows_users_at_fsdp(self, monkeypatch):
        """The extra installs nothing on Windows, so 'install it' is not enough."""
        from forgelm.trainer import ForgeTrainer

        self._without_deepspeed(monkeypatch)
        with pytest.raises(ImportError) as exc:
            ForgeTrainer._check_deepspeed_available()
        assert "fsdp" in str(exc.value).lower()

    def test_passes_when_deepspeed_is_importable(self, monkeypatch):
        """Must not raise on a machine that has it — a check that always fires
        is a check that gets deleted."""
        import sys
        import types

        from forgelm.trainer import ForgeTrainer

        monkeypatch.setitem(sys.modules, "deepspeed", types.ModuleType("deepspeed"))
        ForgeTrainer._check_deepspeed_available()  # no raise

    def test_fsdp_strategy_does_not_require_deepspeed(self, monkeypatch, minimal_config):
        """The check is scoped to the strategy that needs it."""
        from forgelm.trainer import ForgeTrainer

        self._without_deepspeed(monkeypatch)
        cfg = ForgeConfig(**minimal_config(distributed={"strategy": "fsdp"}))
        kwargs: dict = {}
        ForgeTrainer._apply_distributed_config(object.__new__(ForgeTrainer), kwargs, cfg.distributed)
        assert "deepspeed" not in kwargs


# --- FSDP: strategies transformers' FSDP2 default cannot express (#104) ---


class TestFsdp1OnlyStrategiesAreRejectedAtConfigTime:
    """``no_shard`` and ``hybrid_shard`` used to pass ``--dry-run`` and exit 2 at trainer construction.

    transformers 5.10+ runs FSDP2 by default and raises ``fsdp=hybrid_shard is only available with FSDP1`` when
    ``TrainingArguments`` is built, so the config is refused up front (exit 1) instead of after the model is loaded.
    """

    @pytest.mark.parametrize("strategy", ["no_shard", "hybrid_shard"])
    def test_the_schema_refuses_them(self, strategy):
        with pytest.raises(ValidationError, match="fsdp_strategy"):
            DistributedConfig(strategy="fsdp", fsdp_strategy=strategy)

    @pytest.mark.parametrize("strategy", ["no_shard", "hybrid_shard"])
    def test_dry_run_exits_with_a_config_error(self, tmp_path, minimal_config, strategy):
        from unittest.mock import patch

        from forgelm.cli import EXIT_CONFIG_ERROR, main

        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(yaml.dump(minimal_config(distributed={"strategy": "fsdp", "fsdp_strategy": strategy})))
        with patch("sys.argv", ["forgelm", "--config", str(cfg_path), "--dry-run"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == EXIT_CONFIG_ERROR

    @pytest.mark.parametrize("strategy", ["full_shard", "shard_grad_op"])
    @pytest.mark.parametrize("auto_wrap", [True, False])
    @pytest.mark.parametrize("offload", [True, False])
    @pytest.mark.parametrize("prefetch", ["backward_pre", "backward_post"])
    @pytest.mark.parametrize("state_dict", ["FULL_STATE_DICT", "SHARDED_STATE_DICT"])
    def test_every_accepted_variant_builds_training_arguments(
        self, tmp_path, strategy, auto_wrap, offload, prefetch, state_dict
    ):
        """What the schema accepts must be what ``TrainingArguments`` accepts (no config that dies later)."""
        from types import SimpleNamespace

        from transformers import TrainingArguments

        from forgelm.trainer import ForgeTrainer

        dist_cfg = DistributedConfig(
            strategy="fsdp",
            fsdp_strategy=strategy,
            fsdp_auto_wrap=auto_wrap,
            fsdp_offload=offload,
            fsdp_backward_prefetch=prefetch,
            fsdp_state_dict_type=state_dict,
        )
        kwargs: dict = {}
        ForgeTrainer._apply_distributed_config(SimpleNamespace(), kwargs, dist_cfg)
        TrainingArguments(output_dir=str(tmp_path), use_cpu=True, report_to=[], **kwargs)


class TestInertFsdpOptionsAreCalledOut:
    """``fsdp_auto_wrap: false`` and ``fsdp_backward_prefetch: backward_post`` are ignored by FSDP2; say so (#104)."""

    @staticmethod
    def _config(**distributed):
        from tests._helpers.factories import minimal_config

        return ForgeConfig(**minimal_config(distributed={"strategy": "fsdp", **distributed}))

    @staticmethod
    def _warnings(config, caplog):
        import logging

        from forgelm.cli._config_load import _warn_inert_fsdp_options

        with caplog.at_level(logging.WARNING):
            _warn_inert_fsdp_options(config)
        return [r.getMessage() for r in caplog.records if "distributed.fsdp_" in r.getMessage()]

    def test_the_defaults_are_silent(self, caplog):
        assert self._warnings(self._config(), caplog) == []

    def test_turning_auto_wrap_off_is_called_out(self, caplog):
        warned = self._warnings(self._config(fsdp_auto_wrap=False), caplog)
        assert len(warned) == 1 and "fsdp_auto_wrap=false has no effect" in warned[0]

    def test_backward_post_is_called_out(self, caplog):
        warned = self._warnings(self._config(fsdp_backward_prefetch="backward_post"), caplog)
        assert len(warned) == 1 and "backward_post" in warned[0]

    def test_other_strategies_and_no_distributed_block_are_silent(self, caplog):
        from tests._helpers.factories import minimal_config

        deepspeed = ForgeConfig(**minimal_config(distributed={"strategy": "deepspeed", "fsdp_auto_wrap": False}))
        plain = ForgeConfig(**minimal_config())
        assert self._warnings(deepspeed, caplog) == []
        assert self._warnings(plain, caplog) == []

    def test_a_dry_run_reaches_the_warning(self, tmp_path, caplog, minimal_config):
        import logging
        from unittest.mock import patch

        from forgelm.cli import EXIT_SUCCESS, main

        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(yaml.dump(minimal_config(distributed={"strategy": "fsdp", "fsdp_auto_wrap": False})))
        with caplog.at_level(logging.WARNING):
            with patch("sys.argv", ["forgelm", "--config", str(cfg_path), "--dry-run"]):
                with pytest.raises(SystemExit) as exc_info:
                    main()
        assert exc_info.value.code == EXIT_SUCCESS
        assert any("fsdp_auto_wrap=false has no effect" in r.getMessage() for r in caplog.records)
