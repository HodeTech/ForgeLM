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

    def test_auto_skips_unicode_digit_suffix_without_crashing(self, tmp_path, caplog):
        """``"²".isdigit()`` is True but ``int("²")`` raises ValueError; the
        resolver must ignore such a dir (and any non-decimal suffix) with a
        WARNING instead of crashing ``--resume auto`` (F-P2-FAB-37)."""
        import logging

        (tmp_path / "checkpoint-7").mkdir()
        (tmp_path / "checkpoint-abc").mkdir()
        (tmp_path / "checkpoint-²").mkdir()  # superscript-2: isdigit True, int() raises
        with caplog.at_level(logging.WARNING):
            result = _resolve_resume_checkpoint(str(tmp_path), "auto")
        assert result.endswith("checkpoint-7")
        assert any("non-numeric suffix" in r.message for r in caplog.records)

    def test_auto_malformed_suffix_does_not_tie_with_checkpoint_zero(self, tmp_path):
        """A non-numeric suffix used to key to 0 and tie with ``checkpoint-0``;
        after filtering it can no longer win over a real numbered checkpoint."""
        (tmp_path / "checkpoint-0").mkdir()
        (tmp_path / "checkpoint-bad").mkdir()
        (tmp_path / "checkpoint-3").mkdir()
        result = _resolve_resume_checkpoint(str(tmp_path), "auto")
        assert result.endswith("checkpoint-3")

    def test_auto_only_malformed_starts_fresh(self, tmp_path):
        """If every candidate has a non-decimal suffix, none are eligible."""
        (tmp_path / "checkpoint-abc").mkdir()
        (tmp_path / "checkpoint-²").mkdir()
        result = _resolve_resume_checkpoint(str(tmp_path), "auto")
        assert result is None

    def test_auto_rejects_multi_dash_numeric_suffix(self, tmp_path, caplog):
        """``split("-")[-1]`` treated ``checkpoint-bad-900`` as suffix ``900`` and
        could auto-select it as the latest; exact prefix slicing makes the suffix
        ``bad-900`` (non-decimal) so it is ignored with a WARNING and the real
        ``checkpoint-5`` wins."""
        import logging

        (tmp_path / "checkpoint-bad-900").mkdir()
        (tmp_path / "checkpoint-5").mkdir()
        with caplog.at_level(logging.WARNING):
            result = _resolve_resume_checkpoint(str(tmp_path), "auto")
        assert result.endswith("checkpoint-5")
        assert any("non-numeric suffix" in r.message and "checkpoint-bad-900" in r.message for r in caplog.records)

    def test_auto_only_multi_dash_numeric_starts_fresh(self, tmp_path):
        """With only ``checkpoint-bad-900`` present the resolver starts fresh."""
        (tmp_path / "checkpoint-bad-900").mkdir()
        result = _resolve_resume_checkpoint(str(tmp_path), "auto")
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


class TestNonTrainingModeMutualExclusion:
    """Finding 2: the six non-training-mode flags each ``sys.exit()`` in an
    ordered if-chain in ``_maybe_run_no_train_mode``; passing two at once used
    to silently run only the first and drop the rest with a 0 exit code.  They
    are now in an ``add_mutually_exclusive_group`` so argparse rejects the
    combination at parse time (exit 2, "not allowed with")."""

    @pytest.mark.parametrize(
        ("flag_a", "flag_b"),
        [
            ("--dry-run", "--merge"),
            ("--merge", "--generate-data"),
            ("--dry-run", "--compliance-export"),
            ("--fit-check", "--benchmark-only"),
        ],
    )
    def test_two_mode_flags_are_rejected(self, flag_a, flag_b, capsys):
        argv = ["forgelm", flag_a]
        # Value-taking mode flags need an argument so argparse reaches the
        # mutual-exclusion check rather than an "expected one argument" error.
        if flag_a in ("--benchmark-only", "--compliance-export"):
            argv.append("model_or_dir")
        argv.append(flag_b)
        if flag_b in ("--benchmark-only", "--compliance-export"):
            argv.append("model_or_dir")
        with patch("sys.argv", argv):
            with pytest.raises(SystemExit) as exc_info:
                main()
        # argparse usage errors exit 2 (its own convention, matching every
        # other choices/mutually-exclusive rejection in this CLI).
        assert exc_info.value.code == 2
        assert "not allowed with" in capsys.readouterr().err

    def test_single_mode_flag_still_accepted(self, tmp_path, minimal_config):
        """A single mode flag must still parse + run (no false rejection)."""
        cfg_path = str(tmp_path / "config.yaml")
        with open(cfg_path, "w") as f:
            yaml.dump(minimal_config(), f)
        with patch("sys.argv", ["forgelm", "--config", cfg_path, "--dry-run"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == EXIT_SUCCESS


class TestSharedSubcommandFlags:
    @pytest.mark.parametrize(
        "command,extra,flag,value,expected",
        [
            ("purge", [], "--dry-run", [], True),
            ("purge", [], "--config", ["policy.yaml"], "policy.yaml"),
            ("quickstart", [], "--dry-run", [], True),
            ("doctor", [], "--offline", [], True),
            ("deploy", ["model", "--target", "ollama"], "--output", ["serving"], "serving"),
            ("quickstart", [], "--output", ["config.yaml"], "config.yaml"),
            ("cache-models", ["--model", "org/model"], "--output", ["cache"], "cache"),
            ("cache-tasks", ["--tasks", "hellaswag"], "--output", ["cache"], "cache"),
        ],
    )
    @pytest.mark.parametrize("before", [True, False], ids=["before-subcommand", "after-subcommand"])
    def test_explicit_flag_is_preserved(self, command, extra, flag, value, expected, before):
        from forgelm.cli._parser import parse_args

        option = [flag, *value]
        args = [*option, command, *extra] if before else [command, *extra, *option]
        with patch("sys.argv", ["forgelm", *args]):
            result = parse_args()

        assert getattr(result, flag.removeprefix("--").replace("-", "_")) == expected

    @pytest.mark.parametrize(
        "args,attribute,expected",
        [
            (["purge"], "dry_run", False),
            (["purge"], "config", None),
            (["quickstart"], "dry_run", False),
            (["doctor"], "offline", False),
            (["deploy", "model", "--target", "ollama"], "output", None),
            (["quickstart"], "output", None),
            (["cache-models", "--model", "org/model"], "output", None),
            (["cache-tasks", "--tasks", "hellaswag"], "output", None),
        ],
    )
    def test_omitted_flags_keep_their_defaults(self, args, attribute, expected):
        from forgelm.cli._parser import parse_args

        with patch("sys.argv", ["forgelm", *args]):
            result = parse_args()

        assert getattr(result, attribute) == expected

    def test_subcommand_value_overrides_root_value(self):
        from forgelm.cli._parser import parse_args

        with patch("sys.argv", ["forgelm", "--output", "root.yaml", "quickstart", "--output", "local.yaml"]):
            result = parse_args()

        assert result.output == "local.yaml"


class TestParserPublicApi:
    def test_parse_args_has_namespace_return_annotation(self):
        """Finding 8: ``parse_args`` is the one function in _parser imported and
        called from other modules; coding.md requires a return annotation on it.
        (``from __future__ import annotations`` keeps the value as a string.)"""
        from forgelm.cli._parser import parse_args

        assert parse_args.__annotations__.get("return") == "argparse.Namespace"

    def test_facade_reexports_every_subcommand_registrar(self):
        """Finding 5: the ``forgelm.cli`` facade docstring promises every
        registrar resolves *here* so monkeypatch works; the Phase-14+ half was
        missing.  All 19 registrars must now be importable from the facade."""
        from forgelm import cli

        registrars = [
            "_add_chat_subcommand",
            "_add_export_subcommand",
            "_add_deploy_subcommand",
            "_add_quickstart_subcommand",
            "_add_ingest_subcommand",
            "_add_audit_subcommand",
            "_add_doctor_subcommand",
            "_add_verify_audit_subcommand",
            "_add_approve_subcommand",
            "_add_reject_subcommand",
            "_add_approvals_subcommand",
            "_add_purge_subcommand",
            "_add_reverse_pii_subcommand",
            "_add_cache_models_subcommand",
            "_add_cache_tasks_subcommand",
            "_add_verify_annex_iv_subcommand",
            "_add_safety_eval_subcommand",
            "_add_verify_gguf_subcommand",
            "_add_verify_integrity_subcommand",
        ]
        for name in registrars:
            assert callable(getattr(cli, name, None)), f"{name} is not re-exported from forgelm.cli"


class TestHubTelemetryDefault:
    """The CLI keeps TRL / huggingface_hub from sending the anonymous usage ping (#95)."""

    @staticmethod
    def _unset(monkeypatch):
        """Make the variable absent *and* make monkeypatch restore that, so a later set does not leak."""
        monkeypatch.setenv("HF_HUB_DISABLE_TELEMETRY", "placeholder")
        monkeypatch.delenv("HF_HUB_DISABLE_TELEMETRY")

    @staticmethod
    def _clean_env():
        env = {k: v for k, v in os.environ.items() if k != "HF_HUB_DISABLE_TELEMETRY"}
        env["PYTHONPATH"] = os.getcwd() + os.pathsep + env.get("PYTHONPATH", "")
        return env

    def test_it_is_set_when_the_operator_did_not_choose(self, monkeypatch):
        from forgelm.cli._config_load import _disable_hub_telemetry

        self._unset(monkeypatch)
        _disable_hub_telemetry()
        assert os.environ["HF_HUB_DISABLE_TELEMETRY"] == "1"

    @pytest.mark.parametrize("chosen", ["0", "1", "false"])
    def test_an_operators_own_value_wins(self, monkeypatch, chosen):
        from forgelm.cli._config_load import _disable_hub_telemetry

        monkeypatch.setenv("HF_HUB_DISABLE_TELEMETRY", chosen)
        _disable_hub_telemetry()
        assert os.environ["HF_HUB_DISABLE_TELEMETRY"] == chosen

    def test_main_sets_it_before_anything_else_runs(self, monkeypatch):
        from forgelm.cli import _dispatch

        self._unset(monkeypatch)
        seen = {}

        def fake_inner():
            seen["at_dispatch"] = os.environ.get("HF_HUB_DISABLE_TELEMETRY")
            return 0

        with patch.object(_dispatch, "_main_inner", fake_inner):
            _dispatch.main()
        assert seen["at_dispatch"] == "1"

    def test_importing_the_cli_does_not_import_huggingface_hub(self):
        # huggingface_hub reads HF_HUB_DISABLE_TELEMETRY once, at import. main() sets the variable, so nothing the CLI
        # module imports at load time may import it first (a top-level `import transformers` in forgelm.cli would).
        import subprocess
        import sys

        code = "import sys, forgelm.cli; print(any(m == 'huggingface_hub' or m.startswith('huggingface_hub.') for m in sys.modules))"
        out = subprocess.run(
            [sys.executable, "-c", code], env=self._clean_env(), capture_output=True, text=True, timeout=120
        )
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == "False"

    def test_a_fresh_process_that_calls_it_reads_the_variable_as_true(self):
        pytest.importorskip("huggingface_hub")
        import subprocess
        import sys

        code = (
            "from forgelm.cli._config_load import _disable_hub_telemetry; _disable_hub_telemetry(); "
            "import huggingface_hub.constants as c; print(c.HF_HUB_DISABLE_TELEMETRY)"
        )
        out = subprocess.run(
            [sys.executable, "-c", code], env=self._clean_env(), capture_output=True, text=True, timeout=120
        )
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == "True"


class TestReservedRetentionEnforceWarning:
    """`retention.enforce` is accepted but no command reads it; the CLI says so (#92)."""

    @staticmethod
    def _config(enforce):
        from forgelm.config import ForgeConfig
        from tests._helpers.factories import minimal_config

        return ForgeConfig(**minimal_config(retention={"enforce": enforce}))

    def test_the_default_is_silent(self, caplog):
        import logging

        from forgelm.cli._config_load import _warn_reserved_retention_enforce

        with caplog.at_level(logging.WARNING):
            _warn_reserved_retention_enforce(self._config("log_only"))
        assert not [r for r in caplog.records if "retention.enforce" in r.getMessage()]

    @pytest.mark.parametrize("mode", ["warn_on_excess", "block_on_excess"])
    def test_a_gate_that_does_not_exist_is_called_out(self, caplog, mode):
        import logging

        from forgelm.cli._config_load import _warn_reserved_retention_enforce

        with caplog.at_level(logging.WARNING):
            _warn_reserved_retention_enforce(self._config(mode))
        warned = [r.getMessage() for r in caplog.records if "retention.enforce" in r.getMessage()]
        assert len(warned) == 1 and mode in warned[0] and "no command acts on it" in warned[0]

    def test_loading_a_config_file_triggers_it(self, tmp_path, caplog, minimal_config):
        import logging

        from forgelm.cli._config_load import _load_config_or_exit

        path = tmp_path / "c.yaml"
        path.write_text(yaml.dump(minimal_config(retention={"enforce": "block_on_excess"})))
        with caplog.at_level(logging.WARNING):
            _load_config_or_exit(str(path), json_output=False)
        assert any("retention.enforce" in r.getMessage() for r in caplog.records)


class TestUnslothUnsupportedOverridesRejectedAtLoad:
    """`rope_scaling` / `sliding_window_attention` need a transformers config the unsloth backend never builds (#103)."""

    @pytest.mark.parametrize(
        "training",
        [{"rope_scaling": {"type": "linear", "factor": 2.0}}, {"sliding_window_attention": 4096}],
    )
    def test_config_load_exits_1_so_dry_run_fails_before_training(self, tmp_path, minimal_config, caplog, training):
        import logging

        from forgelm.cli._config_load import _load_config_or_exit

        path = tmp_path / "c.yaml"
        path.write_text(
            yaml.dump(minimal_config(model={"name_or_path": "org/model", "backend": "unsloth"}, training=training))
        )
        with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as exc:
            _load_config_or_exit(str(path), json_output=False)
        assert exc.value.code == 1
        assert "not supported by the unsloth backend" in caplog.text

    def test_the_transformers_backend_still_loads(self, tmp_path, minimal_config):
        from forgelm.cli._config_load import _load_config_or_exit

        path = tmp_path / "c.yaml"
        path.write_text(yaml.dump(minimal_config(training={"rope_scaling": {"type": "linear", "factor": 2.0}})))
        assert _load_config_or_exit(str(path), json_output=False).training.rope_scaling is not None


class TestCicdJsonOutputMode:
    """CR-A04-008: the training JSON envelope has no ``verdict`` key, and
    JSON mode does not mirror log lines onto stdout.

    ``docs/usermanuals/{en,tr}/operations/cicd.md`` used to show
    ``jq '.verdict'`` printing ``"success"`` and claimed every stderr event
    was also a JSON object on stdout. A consumer branching on
    ``.verdict == "success"`` saw null, and a log aggregator on that stream
    received nothing.
    """

    _SHELL = "$ forgelm --config configs/run.yaml --output-format json | jq '.success'\ntrue"

    @staticmethod
    def _section(text: str, heading: str) -> str:
        start = text.index(heading)
        body = text[start:]
        next_h2 = body.find("\n## ", len(heading))
        return body if next_h2 == -1 else body[:next_h2]

    def test_training_envelope_success_is_boolean_and_has_no_verdict(self, capsys):
        from forgelm.cli._result import _output_result
        from forgelm.results import TrainResult

        _output_result(TrainResult(success=True, metrics={"loss": 0.1}), "json")
        raw = capsys.readouterr().out
        envelope = json.loads(raw)
        # The old ``jq '.verdict'`` path: the key is absent, so the comparison
        # a CI script was told to make never matches.
        assert "verdict" not in envelope
        assert envelope.get("verdict") != "success"
        # ``jq '.success'`` prints the boolean ``true``, not the string "success".
        assert envelope["success"] is True
        assert json.loads(raw)["success"] is True

    def test_json_mode_logs_stay_plain_text_on_stderr(self, capsys):
        import logging

        from forgelm.cli._logging import logger as cli_logger

        root = logging.getLogger()
        saved_handlers = root.handlers[:]
        saved_level = root.level
        saved_cli_level = cli_logger.level
        for handler in saved_handlers:
            root.removeHandler(handler)
        cli_logger.setLevel(logging.NOTSET)
        try:
            # A fresh process is what CI runs. ``basicConfig`` is a no-op once
            # any handler exists, so drop pytest's capture handler first.
            _setup_logging("DEBUG", json_format=True)
            cli_logger.info("pipeline-started")
            cli_logger.warning("gate-tripped")
            captured = capsys.readouterr()
        finally:
            for handler in root.handlers[:]:
                root.removeHandler(handler)
            for handler in saved_handlers:
                root.addHandler(handler)
            root.setLevel(saved_level)
            cli_logger.setLevel(saved_cli_level)

        assert captured.out == ""
        assert "pipeline-started" not in captured.err
        assert "gate-tripped" in captured.err
        assert "[WARNING]" in captured.err
        with pytest.raises(json.JSONDecodeError):
            json.loads(captured.err.strip())

    @pytest.mark.parametrize(
        ("lang", "heading", "prose"),
        [
            (
                "en",
                "## JSON output mode",
                "Log lines remain plain text on stderr. JSON mode does not emit those lines on stdout.",
            ),
            (
                "tr",
                "## JSON çıktı modu",
                "Log satırları stderr'da düz metin olarak kalır. JSON modu bu satırları stdout'a yazmaz.",
            ),
        ],
    )
    def test_cicd_manual_documents_success_not_a_stdout_event_stream(self, lang, heading, prose):
        from pathlib import Path

        page = Path(__file__).resolve().parent.parent / "docs" / "usermanuals" / lang / "operations" / "cicd.md"
        section = self._section(page.read_text(encoding="utf-8"), heading)
        assert self._SHELL in section
        assert "jq '.verdict'" not in section
        assert '"success"' not in section
        # The retired claim: every stderr event is also a JSON object on stdout.
        assert "also emitted as a JSON object on stdout" not in section
        assert "stdout'ta da JSON nesnesi" not in section
        assert prose in section
