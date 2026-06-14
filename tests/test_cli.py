"""Unit tests for forgelm.cli module."""

import json
import os
from unittest.mock import patch

import pytest
import yaml

from forgelm.cli import (
    EXIT_CONFIG_ERROR,
    EXIT_EVAL_FAILURE,
    EXIT_SUCCESS,
    EXIT_TRAINING_ERROR,
    _get_version,
    _resolve_resume_checkpoint,
    _run_dry_run,
    _setup_logging,
    main,
)
from forgelm.config import ForgeConfig


class TestExitCodes:
    def test_exit_code_values(self):
        assert EXIT_SUCCESS == 0
        assert EXIT_CONFIG_ERROR == 1
        assert EXIT_TRAINING_ERROR == 2
        assert EXIT_EVAL_FAILURE == 3


class TestGetVersion:
    def test_returns_string(self):
        v = _get_version()
        assert isinstance(v, str)
        assert len(v) > 0


class TestSetupLogging:
    def test_setup_does_not_raise(self):
        _setup_logging("DEBUG")
        _setup_logging("INFO")
        _setup_logging("WARNING")
        _setup_logging("ERROR")

    def test_json_format_suppresses_logs(self):
        _setup_logging("DEBUG", json_format=True)


class TestDryRun:
    def test_dry_run_text_format(self, minimal_config):
        config = ForgeConfig(**minimal_config())
        _run_dry_run(config, "text")

    def test_dry_run_json_format(self, capsys, minimal_config):
        config = ForgeConfig(**minimal_config())
        _run_dry_run(config, "json")
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["status"] == "valid"
        assert result["model"] == "org/model"
        assert result["offline"] is False

    def test_dry_run_json_envelope_has_success_true(self, capsys, minimal_config):
        """F-P7-OPUS-16: the dry-run JSON envelope must carry the universal
        ``success`` key (json-output.md "Common conventions") so a CI
        consumer can branch on ``result["success"]`` without a KeyError on
        the happy path. ``status: "valid"`` is retained for back-compat."""
        config = ForgeConfig(**minimal_config())
        _run_dry_run(config, "json")
        result = json.loads(capsys.readouterr().out)
        assert result["success"] is True
        assert result["status"] == "valid"
        # ``success`` is the first key (the documented top-level wrapper).
        assert next(iter(result)) == "success"

    def test_dry_run_with_evaluation(self, minimal_config):
        data = minimal_config()
        data["evaluation"] = {"auto_revert": True, "max_acceptable_loss": 2.0}
        config = ForgeConfig(**data)
        _run_dry_run(config, "text")

    def test_dry_run_with_webhook(self, minimal_config):
        data = minimal_config()
        data["webhook"] = {"url": "https://example.com/hook"}
        config = ForgeConfig(**data)
        _run_dry_run(config, "text")

    def test_dry_run_trust_remote_code_warning(self, minimal_config):
        data = minimal_config()
        data["model"]["trust_remote_code"] = True
        config = ForgeConfig(**data)
        _run_dry_run(config, "text")

    def test_dry_run_offline_mode(self, capsys, minimal_config):
        data = minimal_config()
        data["model"]["offline"] = True
        config = ForgeConfig(**data)
        _run_dry_run(config, "json")
        result = json.loads(capsys.readouterr().out)
        assert result["offline"] is True


class TestResumeCheckpoint:
    def test_explicit_path(self, tmp_path):
        ckpt = tmp_path / "checkpoint-100"
        ckpt.mkdir()
        result = _resolve_resume_checkpoint(str(tmp_path), str(ckpt))
        assert result == str(ckpt)

    def test_auto_detect(self, tmp_path):
        (tmp_path / "checkpoint-100").mkdir()
        (tmp_path / "checkpoint-200").mkdir()
        (tmp_path / "checkpoint-50").mkdir()
        result = _resolve_resume_checkpoint(str(tmp_path), "auto")
        assert result.endswith("checkpoint-200")

    def test_auto_no_checkpoints(self, tmp_path):
        result = _resolve_resume_checkpoint(str(tmp_path), "auto")
        assert result is None

    def test_auto_no_directory(self):
        result = _resolve_resume_checkpoint("/nonexistent/dir", "auto")
        assert result is None


class TestMainEntrypoint:
    def test_missing_config_exits(self):
        with patch("sys.argv", ["forgelm"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == EXIT_CONFIG_ERROR

    def test_nonexistent_config_exits(self):
        with patch("sys.argv", ["forgelm", "--config", "/nonexistent/file.yaml"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == EXIT_CONFIG_ERROR

    def test_dry_run_exits_success(self, tmp_path, minimal_config):
        cfg_path = str(tmp_path / "config.yaml")
        with open(cfg_path, "w") as f:
            yaml.dump(minimal_config(), f)

        with patch("sys.argv", ["forgelm", "--config", cfg_path, "--dry-run"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == EXIT_SUCCESS

    def test_dry_run_json_output(self, tmp_path, capsys, minimal_config):
        cfg_path = str(tmp_path / "config.yaml")
        with open(cfg_path, "w") as f:
            yaml.dump(minimal_config(), f)

        with patch("sys.argv", ["forgelm", "--config", cfg_path, "--dry-run", "--output-format", "json"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == EXIT_SUCCESS

        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["status"] == "valid"

    def test_config_error_json_output(self, capsys):
        with patch("sys.argv", ["forgelm", "--config", "/nonexistent.yaml", "--output-format", "json"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == EXIT_CONFIG_ERROR

        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["success"] is False
        assert "error" in result

    def test_offline_flag_sets_env(self, tmp_path, minimal_config):
        cfg_path = str(tmp_path / "config.yaml")
        with open(cfg_path, "w") as f:
            yaml.dump(minimal_config(), f)

        with patch("sys.argv", ["forgelm", "--config", cfg_path, "--dry-run", "--offline"]):
            with pytest.raises(SystemExit):
                main()

        assert os.environ.get("HF_HUB_OFFLINE") == "1"
        # Cleanup
        for key in ["HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE"]:
            os.environ.pop(key, None)
