"""Tests for Phase 10 CLI additions: chat/export/deploy subcommands and --fit-check."""

from __future__ import annotations

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest
import yaml

from forgelm.cli import (
    EXIT_CONFIG_ERROR,
    EXIT_SUCCESS,
    EXIT_TRAINING_ERROR,
    _run_fit_check,
    main,
)
from forgelm.config import ForgeConfig


def _minimal_cfg_dict(**overrides):
    data = {
        "model": {"name_or_path": "org/model"},
        "lora": {},
        "training": {},
        "data": {"dataset_name_or_path": "org/dataset"},
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# --fit-check flag
# ---------------------------------------------------------------------------


class TestFitCheckFlag:
    def test_fit_check_text_output(self, tmp_path, capsys):
        cfg_path = str(tmp_path / "config.yaml")
        with open(cfg_path, "w") as f:
            yaml.dump(_minimal_cfg_dict(), f)

        torch_stub = MagicMock()
        torch_stub.cuda.is_available.return_value = False

        transformers_stub = MagicMock()
        transformers_stub.AutoConfig.from_pretrained.return_value = MagicMock(
            hidden_size=4096,
            num_hidden_layers=32,
            intermediate_size=11008,
            vocab_size=32000,
            num_attention_heads=32,
            num_key_value_heads=32,
        )

        with patch("sys.argv", ["forgelm", "--config", cfg_path, "--fit-check"]):
            with patch.dict(sys.modules, {"torch": torch_stub, "transformers": transformers_stub}):
                with pytest.raises(SystemExit) as exc_info:
                    main()
        assert exc_info.value.code == EXIT_SUCCESS

        captured = capsys.readouterr()
        assert "VRAM Fit Check" in captured.out or "UNKNOWN" in captured.out

    def test_fit_check_json_output(self, tmp_path, capsys):
        cfg_path = str(tmp_path / "config.yaml")
        with open(cfg_path, "w") as f:
            yaml.dump(_minimal_cfg_dict(), f)

        torch_stub = MagicMock()
        torch_stub.cuda.is_available.return_value = False

        transformers_stub = MagicMock()
        transformers_stub.AutoConfig.from_pretrained.return_value = MagicMock(
            hidden_size=4096,
            num_hidden_layers=32,
            intermediate_size=11008,
            vocab_size=32000,
            num_attention_heads=32,
            num_key_value_heads=32,
        )

        with patch("sys.argv", ["forgelm", "--config", cfg_path, "--fit-check", "--output-format", "json"]):
            with patch.dict(sys.modules, {"torch": torch_stub, "transformers": transformers_stub}):
                with pytest.raises(SystemExit) as exc_info:
                    main()
        assert exc_info.value.code == EXIT_SUCCESS

        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert "verdict" in result
        assert "estimated_gb" in result
        assert "breakdown" in result

    def test_fit_check_without_config_fails(self):
        with patch("sys.argv", ["forgelm", "--fit-check"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == EXIT_CONFIG_ERROR


# ---------------------------------------------------------------------------
# forgelm deploy subcommand
# ---------------------------------------------------------------------------


class TestDeployCLI:
    def test_deploy_ollama_exits_success(self, tmp_path):
        model_dir = tmp_path / "model"
        model_dir.mkdir()
        out = str(tmp_path / "Modelfile")
        with patch("sys.argv", ["forgelm", "deploy", str(model_dir), "--target", "ollama", "--output", out]):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == EXIT_SUCCESS
        assert os.path.isfile(out)

    def test_deploy_vllm_exits_success(self, tmp_path):
        out = str(tmp_path / "vllm.yaml")
        # vllm accepts HF Hub IDs; no local-path validation
        with patch("sys.argv", ["forgelm", "deploy", "./model", "--target", "vllm", "--output", out]):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == EXIT_SUCCESS

    def test_deploy_tgi_exits_success(self, tmp_path):
        model_dir = tmp_path / "model"
        model_dir.mkdir()
        out = str(tmp_path / "docker-compose.yaml")
        with patch("sys.argv", ["forgelm", "deploy", str(model_dir), "--target", "tgi", "--output", out]):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == EXIT_SUCCESS

    def test_deploy_hf_endpoints_exits_success(self, tmp_path):
        out = str(tmp_path / "endpoint.json")
        # hf-endpoints expects HF Hub repo IDs; no local-path validation
        with patch("sys.argv", ["forgelm", "deploy", "./model", "--target", "hf-endpoints", "--output", out]):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == EXIT_SUCCESS

    def test_deploy_invalid_target_is_argparse_rejected(self, tmp_path, capsys):
        """F-P7-OPUS-37: ``--target`` has argparse choices, so a bogus value is
        rejected by argparse (exit 2) BEFORE generate_deploy_config runs — pin
        that explicitly rather than relying on 2 == EXIT_TRAINING_ERROR."""
        out = str(tmp_path / "out.cfg")
        with patch("sys.argv", ["forgelm", "deploy", "./model", "--target", "bogus", "--output", out]):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 2
        assert "invalid choice" in capsys.readouterr().err

    def test_generate_deploy_config_unsupported_target_returns_failure(self):
        """F-P7-OPUS-37: cover the library-level SUPPORTED_TARGETS branch that
        the CLI seam can never reach (argparse blocks bad targets first)."""
        from forgelm.deploy import generate_deploy_config

        result = generate_deploy_config("m", "bogus")
        assert result.success is False
        assert "Unsupported target" in (result.error or "")

    def test_export_success_json_envelope_keys(self, tmp_path, capsys):
        """F-P7-OPUS-37: pin the export success-envelope top-level key set so a
        future field rename/removal in the dispatcher is caught."""
        from forgelm.export import ExportResult

        ok = ExportResult(
            success=True,
            output_path="/tmp/m.gguf",
            format="gguf",
            quant="q4_k_m",
            sha256="abc",
            size_bytes=10,
        )
        with patch("forgelm.export.export_model", return_value=ok):
            with patch("sys.argv", ["forgelm", "--output-format", "json", "export", "./m", "--output", "/tmp/m.gguf"]):
                with pytest.raises(SystemExit) as exc_info:
                    main()
        assert exc_info.value.code == EXIT_SUCCESS
        envelope = json.loads(capsys.readouterr().out)
        assert set(envelope) == {
            "success",
            "output_path",
            "format",
            "quant",
            "requested_quant",
            "manual_step_required",
            "followup_command",
            "sha256",
            "size_bytes",
            "error",
        }

    def test_deploy_json_output(self, tmp_path, capsys):
        model_dir = tmp_path / "model"
        model_dir.mkdir()
        out = str(tmp_path / "Modelfile")
        with patch(
            "sys.argv",
            ["forgelm", "--output-format", "json", "deploy", str(model_dir), "--target", "ollama", "--output", out],
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == EXIT_SUCCESS

        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["success"] is True
        assert result["target"] == "ollama"

    def test_deploy_with_system_prompt(self, tmp_path):
        model_dir = tmp_path / "model"
        model_dir.mkdir()
        out = str(tmp_path / "Modelfile")
        with patch(
            "sys.argv",
            [
                "forgelm",
                "deploy",
                str(model_dir),
                "--target",
                "ollama",
                "--output",
                out,
                "--system",
                "You are helpful.",
            ],
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == EXIT_SUCCESS
        with open(out) as f:
            content = f.read()
        assert "You are helpful." in content

    def test_deploy_does_not_require_config(self, tmp_path):
        """forgelm deploy must work without --config."""
        model_dir = tmp_path / "model"
        model_dir.mkdir()
        out = str(tmp_path / "Modelfile")
        with patch("sys.argv", ["forgelm", "deploy", str(model_dir), "--target", "ollama", "--output", out]):
            with pytest.raises(SystemExit) as exc_info:
                main()
        # Must exit with success, not CONFIG_ERROR
        assert exc_info.value.code != EXIT_CONFIG_ERROR


# ---------------------------------------------------------------------------
# forgelm export subcommand
# ---------------------------------------------------------------------------


class TestExportCLI:
    def test_export_missing_llama_cpp_exits_error(self, tmp_path):
        out = str(tmp_path / "model.gguf")
        with patch.dict(sys.modules, {"llama_cpp": None}):
            with patch("sys.argv", ["forgelm", "export", "./model", "--output", out]):
                with pytest.raises(SystemExit) as exc_info:
                    main()
        assert exc_info.value.code == EXIT_TRAINING_ERROR

    def test_export_malformed_converter_env_exits_config_error(self, tmp_path, monkeypatch):
        """F-P7-OPUS-36: a malformed FORGELM_GGUF_CONVERTER (non-.py path) is
        operator input → EXIT_CONFIG_ERROR (1), not EXIT_TRAINING_ERROR (2)."""
        monkeypatch.setenv("FORGELM_GGUF_CONVERTER", str(tmp_path / "not_a_script.bin"))
        out = str(tmp_path / "model.gguf")
        with patch("sys.argv", ["forgelm", "export", "./model", "--output", out]):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == EXIT_CONFIG_ERROR

    def test_export_json_on_failure(self, tmp_path, capsys):
        out = str(tmp_path / "model.gguf")
        with patch.dict(sys.modules, {"llama_cpp": None}):
            with patch(
                "sys.argv",
                [
                    "forgelm",
                    "--output-format",
                    "json",
                    "export",
                    "./model",
                    "--output",
                    out,
                ],
            ):
                with pytest.raises(SystemExit) as exc_info:
                    main()

        assert exc_info.value.code == EXIT_TRAINING_ERROR
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["success"] is False

    def test_export_does_not_require_config(self, tmp_path):
        """forgelm export must work without --config."""
        out = str(tmp_path / "model.gguf")
        with patch.dict(sys.modules, {"llama_cpp": None}):
            with patch("sys.argv", ["forgelm", "export", "./model", "--output", out]):
                with pytest.raises(SystemExit) as exc_info:
                    main()
        # Error because llama_cpp missing — but NOT CONFIG_ERROR
        assert exc_info.value.code != EXIT_CONFIG_ERROR

    def test_export_success_path(self, tmp_path):
        out = str(tmp_path / "model.gguf")

        llama_cpp_stub = MagicMock()
        pkg_dir = str(tmp_path / "llama_cpp")
        os.makedirs(pkg_dir, exist_ok=True)
        llama_cpp_stub.__file__ = os.path.join(pkg_dir, "__init__.py")
        open(os.path.join(pkg_dir, "convert_hf_to_gguf.py"), "w").close()

        def fake_run(cmd, **kwargs):
            actual = cmd[cmd.index("--outfile") + 1] if "--outfile" in cmd else out
            with open(actual, "wb") as f:
                f.write(b"gguf data")
            m = MagicMock()
            m.returncode = 0
            m.stderr = ""
            return m

        with patch.dict(sys.modules, {"llama_cpp": llama_cpp_stub}):
            with patch("subprocess.run", side_effect=fake_run):
                with patch(
                    "sys.argv",
                    ["forgelm", "export", str(tmp_path), "--output", out, "--quant", "q8_0"],
                ):
                    with pytest.raises(SystemExit) as exc_info:
                        main()

        assert exc_info.value.code == EXIT_SUCCESS

    def test_export_rejects_comma_quant(self, capsys):
        # F-P7-OPUS-13: --quant is a single-value choices flag; the comma
        # form the docs once advertised is rejected by argparse (exit 2).
        with patch("sys.argv", ["forgelm", "export", "./model", "--output", "o.gguf", "--quant", "q4_k_m,q8_0"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 2
        err = capsys.readouterr().err
        assert "invalid choice" in err


# ---------------------------------------------------------------------------
# json-output.md envelope-contract parity (F-P7-OPUS-12)
# ---------------------------------------------------------------------------


class TestLifecycleEnvelopeDocs:
    def test_export_and_deploy_envelopes_documented(self):
        """json-output.md (EN + TR) must carry export + deploy sections."""
        from pathlib import Path

        repo_root = Path(__file__).resolve().parent.parent
        for lang in ("en", "tr"):
            doc = repo_root / "docs" / "usermanuals" / lang / "reference" / "json-output.md"
            text = doc.read_text(encoding="utf-8")
            assert "## `forgelm export`" in text, f"{lang} json-output.md missing export section"
            assert "## `forgelm deploy`" in text, f"{lang} json-output.md missing deploy section"


# ---------------------------------------------------------------------------
# forgelm chat subcommand (smoke tests; no actual model loaded)
# ---------------------------------------------------------------------------


class TestChatCLI:
    def test_chat_does_not_require_config(self):
        """Running forgelm chat without --config must not exit with CONFIG_ERROR.

        Smoke check only — the SIGINT exit-code contract is pinned by the
        two dedicated tests below."""
        with patch("forgelm.cli._run_chat_cmd", return_value=None):
            with patch("sys.argv", ["forgelm", "chat", "./model"]):
                with pytest.raises(SystemExit) as exc_info:
                    main()
        assert exc_info.value.code != EXIT_CONFIG_ERROR

    def test_chat_clean_exit_returns_success(self):
        """F-P7-OPUS-28: a clean REPL exit (run_chat returns normally) must
        exit EXIT_SUCCESS — the dispatcher's two-path SIGINT contract."""
        with patch("forgelm.chat.run_chat", return_value=None):
            with patch("sys.argv", ["forgelm", "chat", "./model"]):
                with pytest.raises(SystemExit) as exc_info:
                    main()
        assert exc_info.value.code == EXIT_SUCCESS

    def test_chat_inflight_sigint_exits_training_error(self):
        """F-P7-OPUS-28: a KeyboardInterrupt during generation (not caught by
        _run_chat_cmd's ``except Exception``) bubbles to the dispatcher and
        exits EXIT_TRAINING_ERROR (2), NOT success. Pins the exact code so a
        regression that swaps it cannot pass."""
        with patch("forgelm.cli._run_chat_cmd", side_effect=KeyboardInterrupt):
            with patch("sys.argv", ["forgelm", "chat", "./model"]):
                with pytest.raises(SystemExit) as exc_info:
                    main()
        assert exc_info.value.code == EXIT_TRAINING_ERROR

    def test_chat_subcommand_registered(self, capsys):
        """forgelm chat --help must succeed and document model_path."""
        with patch("sys.argv", ["forgelm", "chat", "--help"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "model_path" in captured.out

    def test_chat_help_does_not_advertise_unshipped_safety(self, capsys):
        # F-P7-OPUS-14 regression: chat has no --safety flag and performs
        # no per-response safety screening; the help text must not promise
        # one (the user manual explicitly says the feature does not exist).
        with patch("sys.argv", ["forgelm", "chat", "--help"]):
            with pytest.raises(SystemExit):
                main()
        captured = capsys.readouterr()
        assert "safety" not in captured.out.lower()

    def test_dispatch_docstring_does_not_hardcode_stale_subcommand_subset(self):
        """F-P7-OPUS-40: the ``_dispatch_subcommand`` docstring used to
        enumerate 11 of the 18 routed subcommands as prose, drifting silently as
        new ones were added. It now points at the authoritative ``table``
        literal. Guard against re-introducing a partial hard-coded list by
        asserting subcommands present only in the newer cohort are NOT named in
        the docstring prose (they would only appear there as a stale partial
        enumeration)."""
        from forgelm.cli._dispatch import _dispatch_subcommand

        doc = _dispatch_subcommand.__doc__ or ""
        for stale_only_name in ("purge", "reverse-pii", "safety-eval", "verify-gguf"):
            assert stale_only_name not in doc, (
                f"_dispatch_subcommand docstring hard-codes {stale_only_name!r}; "
                "point at the table literal instead of enumerating subcommands"
            )

    def test_chat_manual_frontmatter_does_not_advertise_safety_routing(self):
        # F-P7-OPUS-39: the chat manual frontmatter description (rendered as the
        # SPA page summary) must not advertise 'safety routing' while --safety
        # is unshipped — it contradicts the same page's own disclaimer.
        from pathlib import Path

        repo_root = Path(__file__).resolve().parent.parent
        for lang in ("en", "tr"):
            page = repo_root / "docs" / "usermanuals" / lang / "deployment" / "chat.md"
            frontmatter = page.read_text(encoding="utf-8").split("---", 2)[1].lower()
            assert "safety routing" not in frontmatter
            assert "güvenlik routing" not in frontmatter


# ---------------------------------------------------------------------------
# _run_fit_check helper
# ---------------------------------------------------------------------------


class TestRunFitCheckHelper:
    def test_text_output_contains_verdict(self, capsys):
        from forgelm.fit_check import FitCheckResult

        mock_result = FitCheckResult(
            verdict="FITS",
            estimated_gb=7.5,
            available_gb=24.0,
            recommendations=[],
            breakdown={"base_model_gb": 4.5},
        )

        cfg = ForgeConfig(**_minimal_cfg_dict())
        with patch("forgelm.fit_check.estimate_vram", return_value=mock_result):
            _run_fit_check(cfg, "text")

        captured = capsys.readouterr()
        assert "FITS" in captured.out

    def test_json_output_structure(self, capsys):
        from forgelm.fit_check import FitCheckResult

        mock_result = FitCheckResult(
            verdict="OOM",
            estimated_gb=35.0,
            available_gb=12.0,
            recommendations=["Reduce batch size"],
            breakdown={"base_model_gb": 18.0},
        )

        cfg = ForgeConfig(**_minimal_cfg_dict())
        with patch("forgelm.fit_check.estimate_vram", return_value=mock_result):
            _run_fit_check(cfg, "json")

        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["verdict"] == "OOM"
        assert result["estimated_gb"] == pytest.approx(35.0)
        assert result["recommendations"] == ["Reduce batch size"]


# ---------------------------------------------------------------------------
# Subcommand routing (no training flow triggered)
# ---------------------------------------------------------------------------


class TestSubcommandRouting:
    def test_existing_flags_unchanged_after_subcommand_addition(self, tmp_path):
        """--dry-run must still work without interference from subparsers."""
        cfg_path = str(tmp_path / "config.yaml")
        with open(cfg_path, "w") as f:
            yaml.dump(_minimal_cfg_dict(), f)

        with patch("sys.argv", ["forgelm", "--config", cfg_path, "--dry-run"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == EXIT_SUCCESS

    def test_dispatcher_clamps_nonpublic_verify_audit_return(self):
        """F-P7-OPUS-29: a dispatcher returning a non-public code (e.g. a
        signal-derived 130) must be clamped to EXIT_TRAINING_ERROR at the
        dispatch seam, making the _exit_codes docstring invariant real."""
        with patch("forgelm.cli._run_verify_audit_cmd", MagicMock(return_value=130)):
            with patch("sys.argv", ["forgelm", "verify-audit", "/tmp/out"]):
                with pytest.raises(SystemExit) as exc_info:
                    main()
        assert exc_info.value.code == EXIT_TRAINING_ERROR

    def test_dispatcher_passes_through_public_verify_audit_return(self):
        """The clamp must not alter a legitimate public code."""
        with patch("forgelm.cli._run_verify_audit_cmd", MagicMock(return_value=EXIT_CONFIG_ERROR)):
            with patch("sys.argv", ["forgelm", "verify-audit", "/tmp/out"]):
                with pytest.raises(SystemExit) as exc_info:
                    main()
        assert exc_info.value.code == EXIT_CONFIG_ERROR

    def test_wizard_still_works(self):
        """--wizard flow must remain reachable via the dispatcher.

        Post-D2 (review-cycle 2 / 2026-05-09) the dispatcher consumes
        ``run_wizard_full`` and emits ``EXIT_SUCCESS`` when the operator
        produced + deferred (saved a YAML but answered "no" to "start
        training now?").  ``EXIT_WIZARD_CANCELLED = 5`` is now the
        exit code for genuine cancels (Ctrl-C, non-tty refusal,
        decline-to-save) — covered separately below.
        """
        from forgelm.wizard._orchestrator import WizardOutcome

        deferred = WizardOutcome(config_path="/tmp/saved.yaml", start_training=False)
        with patch("forgelm.wizard.run_wizard_full", MagicMock(return_value=deferred)):
            with patch("sys.argv", ["forgelm", "--wizard"]):
                with pytest.raises(SystemExit) as exc_info:
                    main()
        assert exc_info.value.code == EXIT_SUCCESS

    def test_wizard_cancelled_exits_5(self):
        """D2: a cancelled wizard exits ``EXIT_WIZARD_CANCELLED`` (5)."""
        from forgelm.cli._exit_codes import EXIT_WIZARD_CANCELLED
        from forgelm.wizard._orchestrator import WizardOutcome

        cancelled = WizardOutcome(config_path=None, start_training=False)
        with patch("forgelm.wizard.run_wizard_full", MagicMock(return_value=cancelled)):
            with patch("sys.argv", ["forgelm", "--wizard"]):
                with pytest.raises(SystemExit) as exc_info:
                    main()
        assert exc_info.value.code == EXIT_WIZARD_CANCELLED

    def test_wizard_start_from_threads_through_to_run_wizard_full(self, monkeypatch):
        """PR-D-B1 (PR-E review fix): the --wizard-start-from flag must reach
        run_wizard_full as the start_from kwarg.

        Pre-fix coverage stopped at the orchestrator boundary; a future
        rename of the argparse ``dest`` would silently regress to
        ``None`` (legacy behaviour) without any test catching it.  This
        test stubs ``run_wizard_full`` to capture its kwargs, builds an
        argparse Namespace via the real parser, and asserts the path
        flowed through parser → dispatcher → wizard.
        """
        from forgelm.wizard._orchestrator import WizardOutcome

        captured: dict = {}

        def _stub_run_wizard_full(*, start_from=None):
            captured["start_from"] = start_from
            return WizardOutcome(config_path=None, start_training=False)

        with patch("forgelm.wizard.run_wizard_full", _stub_run_wizard_full):
            with patch("sys.argv", ["forgelm", "--wizard", "--wizard-start-from", "/tmp/some-config.yaml"]):
                with pytest.raises(SystemExit):
                    main()
        assert captured["start_from"] == "/tmp/some-config.yaml", (
            "--wizard-start-from did not thread through to run_wizard_full"
        )

    def test_wizard_start_training_routes_through_dispatcher(self, monkeypatch, tmp_path, minimal_config):
        """E22-24 (review-cycle 3): the start_training=True branch must mutate
        ``args.config`` and let the trainer pipeline take over.

        The pre-cycle test only exercised the deferred + cancel paths;
        the dispatcher's ``args.config = outcome.config_path`` line was
        uncovered.  Stub the trainer so the test stays GPU-free + fast.
        """
        import yaml as _yaml

        from forgelm.wizard._orchestrator import WizardOutcome

        cfg_path = tmp_path / "wizard.yaml"
        config_data = minimal_config()
        cfg_path.write_text(_yaml.safe_dump(config_data), encoding="utf-8")
        # G3 (review-cycle 3): defensive hardening — verify the YAML
        # the test writes actually loads cleanly.  Without this assert,
        # a future change to ``minimal_config`` that produced an
        # invalid YAML would still pass this test (because the
        # trainer pipeline is mocked BEFORE validation runs in
        # ``_dispatch.main``), giving false-positive coverage.
        from forgelm.config import ForgeConfig

        ForgeConfig.model_validate(config_data)
        outcome = WizardOutcome(config_path=str(cfg_path), start_training=True)

        captured = {}

        # ``_run_training_pipeline`` is called as
        # ``_run_training_pipeline(config, args, json_output)`` from
        # ``forgelm/cli/_dispatch.py:232``.  Stub captures the args
        # object so the test can verify ``args.config`` was mutated.
        def _stub_pipeline(_config_obj, args, *_a, **_kw):
            captured["config"] = args.config
            sys.exit(EXIT_SUCCESS)

        with patch("forgelm.wizard.run_wizard_full", MagicMock(return_value=outcome)):
            monkeypatch.setattr("forgelm.cli._dispatch._run_training_pipeline", _stub_pipeline)
            with patch("sys.argv", ["forgelm", "--wizard"]):
                with pytest.raises(SystemExit) as exc_info:
                    main()
        # The dispatcher must have set args.config = outcome.config_path
        # and routed into the (stubbed) trainer pipeline.
        assert captured["config"] == str(cfg_path)
        assert exc_info.value.code == EXIT_SUCCESS
