"""Unit tests for CLI subcommands (--merge, --compliance-export, --benchmark-only)."""

import json
import os
from unittest.mock import patch

import pytest
import yaml

from forgelm.cli import (
    EXIT_CONFIG_ERROR,
    EXIT_SUCCESS,
    _run_compliance_export,
    main,
)
from forgelm.config import ForgeConfig


class TestComplianceExportCLI:
    def test_compliance_export_creates_files(self, tmp_path, minimal_config):
        config = ForgeConfig(**minimal_config())
        output_dir = str(tmp_path / "compliance")
        _run_compliance_export(config, output_dir, "text")

        assert os.path.isfile(os.path.join(output_dir, "compliance_report.json"))
        assert os.path.isfile(os.path.join(output_dir, "training_manifest.yaml"))
        assert os.path.isfile(os.path.join(output_dir, "data_provenance.json"))

    def test_compliance_export_json_output(self, tmp_path, capsys, minimal_config):
        config = ForgeConfig(**minimal_config())
        output_dir = str(tmp_path / "compliance")
        _run_compliance_export(config, output_dir, "json")

        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["success"] is True
        assert len(result["files"]) == 3

    def test_compliance_export_via_main(self, tmp_path, minimal_config):
        cfg_path = str(tmp_path / "config.yaml")
        output_dir = str(tmp_path / "audit")
        with open(cfg_path, "w") as f:
            yaml.dump(minimal_config(), f)

        with patch("sys.argv", ["forgelm", "--config", cfg_path, "--compliance-export", output_dir]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == EXIT_SUCCESS

        assert os.path.isdir(output_dir)


class TestMergeCLI:
    def test_merge_without_config_exits(self, tmp_path, minimal_config):
        cfg_path = str(tmp_path / "config.yaml")
        with open(cfg_path, "w") as f:
            yaml.dump(minimal_config(), f)

        with patch("sys.argv", ["forgelm", "--config", cfg_path, "--merge"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == EXIT_CONFIG_ERROR

    def test_merge_with_disabled_config_exits(self, tmp_path, minimal_config):
        cfg_path = str(tmp_path / "config.yaml")
        data = minimal_config(merge={"enabled": False})
        with open(cfg_path, "w") as f:
            yaml.dump(data, f)

        with patch("sys.argv", ["forgelm", "--config", cfg_path, "--merge"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == EXIT_CONFIG_ERROR


class TestAuditSubcommand:
    """Phase 11.5: `forgelm audit PATH` subcommand + legacy `--data-audit` alias."""

    def _make_jsonl(self, path, rows):
        with open(path, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")

    def test_audit_subcommand_writes_report(self, tmp_path):
        data_path = tmp_path / "data.jsonl"
        self._make_jsonl(data_path, [{"text": "alpha"}, {"text": "beta"}])
        out_dir = tmp_path / "audit"

        with patch(
            "sys.argv",
            ["forgelm", "audit", str(data_path), "--output", str(out_dir)],
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == EXIT_SUCCESS

        assert (out_dir / "data_audit_report.json").is_file()

    def test_audit_subcommand_json_envelope(self, tmp_path, capsys):
        data_path = tmp_path / "data.jsonl"
        self._make_jsonl(data_path, [{"text": "alpha"}])
        out_dir = tmp_path / "audit"

        with patch(
            "sys.argv",
            [
                "forgelm",
                "audit",
                str(data_path),
                "--output",
                str(out_dir),
                "--output-format",
                "json",
            ],
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == EXIT_SUCCESS

        envelope = json.loads(capsys.readouterr().out)
        assert envelope["success"] is True
        assert "pii_severity" in envelope
        assert envelope["report_path"].endswith("data_audit_report.json")

    def test_audit_dispatch_quality_filter_defaults_on_when_attr_absent(self):
        """F-P7-OPUS-26: the dispatcher's getattr fallback must mirror the
        parser's documented default-ON (v0.6.0+).  A Namespace without a
        ``quality_filter`` attribute (the shape a future ``argparse.SUPPRESS``
        switch would produce) must still pass ``enable_quality_filter=True``."""
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from forgelm.cli.subcommands._audit import _run_audit_cmd

        args = SimpleNamespace(input_path="data.jsonl")  # no quality_filter attr
        fake = MagicMock()
        with patch("forgelm.cli._run_data_audit", fake):
            _run_audit_cmd(args, "text")
        assert fake.called, "_run_data_audit was never invoked; patch target may have drifted"
        assert fake.call_args.kwargs["enable_quality_filter"] is True

    def test_legacy_data_audit_flag_removed(self, tmp_path):
        """The legacy ``forgelm --data-audit PATH`` flag was removed in v0.8.0;
        operators use the ``forgelm audit PATH`` subcommand instead. argparse
        now rejects the flag at the CLI boundary (exit 2)."""
        data_path = tmp_path / "data.jsonl"
        self._make_jsonl(data_path, [{"text": "x"}])
        with patch("sys.argv", ["forgelm", "--data-audit", str(data_path)]):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 2

    def test_audit_quality_filter_flag(self, tmp_path):
        # Phase 12: --quality-filter populates quality_summary.
        data_path = tmp_path / "data.jsonl"
        self._make_jsonl(
            data_path,
            [{"text": "1234567890 !@#$%^&*()"}, {"text": "fine prose passes the heuristics."}],
        )
        out_dir = tmp_path / "audit"

        with patch(
            "sys.argv",
            ["forgelm", "audit", str(data_path), "--output", str(out_dir), "--quality-filter"],
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == EXIT_SUCCESS

        with open(out_dir / "data_audit_report.json", encoding="utf-8") as fh:
            report = json.load(fh)
        assert "quality_summary" in report
        assert report["quality_summary"].get("samples_flagged", 0) >= 1

    def test_audit_rejects_invalid_jaccard_threshold(self, tmp_path):
        # Phase 12: --jaccard-threshold enforces [0.0, 1.0] at parse-time.
        data_path = tmp_path / "data.jsonl"
        self._make_jsonl(data_path, [{"text": "alpha"}])

        with patch(
            "sys.argv",
            ["forgelm", "audit", str(data_path), "--jaccard-threshold", "1.5"],
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            # argparse error → exit code 2 (its standard convention).
            assert exc_info.value.code == 2


class TestNumericFlagValidators:
    """F-P7-OPUS-27: deploy/chat/safety-eval bounded numeric flags must fail
    fast at the CLI boundary (parity with the audit/ingest validators)."""

    @pytest.mark.parametrize(
        "argv",
        [
            ["forgelm", "deploy", "m", "--target", "vllm", "--gpu-memory-utilization", "9.5"],
            ["forgelm", "deploy", "m", "--target", "tgi", "--port", "-1"],
            ["forgelm", "deploy", "m", "--target", "vllm", "--max-length", "-5"],
            ["forgelm", "chat", "m", "--temperature", "-3"],
            ["forgelm", "chat", "m", "--max-new-tokens", "-10"],
            ["forgelm", "safety-eval", "--model", "m", "--default-probes", "--max-new-tokens", "-1"],
            # NaN/inf bypass the < / > bounds comparisons; isfinite() must reject them.
            ["forgelm", "chat", "m", "--temperature", "nan"],
            ["forgelm", "chat", "m", "--temperature", "inf"],
            ["forgelm", "deploy", "m", "--target", "vllm", "--gpu-memory-utilization", "nan"],
            ["forgelm", "deploy", "m", "--target", "vllm", "--gpu-memory-utilization", "inf"],
        ],
    )
    def test_out_of_range_numeric_flag_rejected_at_parse_time(self, argv):
        with patch("sys.argv", argv):
            with pytest.raises(SystemExit) as exc_info:
                main()
        # argparse usage error → exit 2 (conventional across the CLI).
        assert exc_info.value.code == 2

    @pytest.mark.parametrize(
        "argv",
        [
            ["forgelm", "deploy", "m", "--target", "vllm", "--gpu-memory-utilization", "0.9"],
            ["forgelm", "chat", "m", "--temperature", "1.5"],
        ],
    )
    def test_in_range_numeric_flag_parses(self, argv):
        from forgelm.cli._parser import parse_args

        with patch("sys.argv", argv):
            # Must not raise SystemExit at parse time for in-range values,
            # and the flag value must actually be stored in the Namespace.
            ns = parse_args()

        if "--gpu-memory-utilization" in argv:
            assert ns.gpu_memory_utilization == pytest.approx(0.9)
        if "--temperature" in argv:
            assert ns.temperature == pytest.approx(1.5)


class TestPipelineDispatchClamping:
    """F-L-05: _dispatch_pipeline_mode must clamp non-public exit codes via
    _clamp_exit_code before passing them to sys.exit."""

    def test_dispatcher_clamps_nonpublic_pipeline_return(self, tmp_path, minimal_config):
        """A pipeline run_pipeline_from_args returning a signal-derived code
        (e.g. 130 = 128+SIGINT) must be clamped to EXIT_TRAINING_ERROR (2)
        at the dispatch seam — mirroring the verify-audit clamping guarantee
        (test_dispatcher_clamps_nonpublic_verify_audit_return in test_cli_phase10.py)."""
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from forgelm.cli._dispatch import _dispatch_pipeline_mode
        from forgelm.cli._exit_codes import EXIT_TRAINING_ERROR

        # Write a real (but minimal) YAML file so the open() inside
        # _dispatch_pipeline_mode succeeds.
        cfg_path = tmp_path / "pipeline.yaml"
        cfg_path.write_text("pipeline:\n  stages: []\n")

        config = MagicMock()
        args = SimpleNamespace(config=str(cfg_path))

        with patch(
            "forgelm.cli._pipeline.run_pipeline_from_args",
            MagicMock(return_value=130),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _dispatch_pipeline_mode(config, args)

        assert exc_info.value.code == EXIT_TRAINING_ERROR
