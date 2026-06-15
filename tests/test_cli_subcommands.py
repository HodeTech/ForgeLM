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
        assert fake.call_args.kwargs["enable_quality_filter"] is True

    def test_legacy_data_audit_flag_still_works(self, tmp_path):
        data_path = tmp_path / "data.jsonl"
        self._make_jsonl(data_path, [{"text": "legacy alias still routes here"}])
        out_dir = tmp_path / "audit"

        with patch(
            "sys.argv",
            ["forgelm", "--data-audit", str(data_path), "--output", str(out_dir)],
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == EXIT_SUCCESS

        # Same on-disk product as the subcommand path.
        assert (out_dir / "data_audit_report.json").is_file()

    def test_legacy_data_audit_flag_runs_quality_filter_like_subcommand(self, tmp_path):
        # F-P7-OPUS-01 regression: the legacy `--data-audit` alias claims
        # "same behaviour, same output" as `forgelm audit`.  The subcommand
        # defaults --quality-filter ON (v0.6.0+), so the legacy flag must
        # also populate quality_summary — otherwise the two invocations emit
        # divergent EU AI Act Art. 10 governance artifacts.
        data_path = tmp_path / "data.jsonl"
        self._make_jsonl(
            data_path,
            [{"text": "1234567890 !@#$%^&*()"}, {"text": "fine prose passes the heuristics."}],
        )
        out_dir = tmp_path / "audit"

        with patch(
            "sys.argv",
            ["forgelm", "--data-audit", str(data_path), "--output", str(out_dir)],
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == EXIT_SUCCESS

        with open(out_dir / "data_audit_report.json", encoding="utf-8") as fh:
            report = json.load(fh)
        # Quality filter is ON by default for the subcommand; the legacy
        # alias must match (populated quality_summary, not absent/empty).
        assert report.get("quality_summary"), "legacy alias must run the quality filter like the subcommand"

    def test_legacy_data_audit_flag_emits_deprecation_warning_and_audit_event(self, tmp_path):
        """Phase 13 (Faz 13) — `--data-audit` is a documented deprecation path.

        Verifies three contract points the deprecation must satisfy:
          1. A real ``DeprecationWarning`` fires (so `python -Wd` / CI
             warning gates surface it).
          2. The append-only audit log records `cli.legacy_flag_invoked`
             with the documented payload (flag / replacement / version).
          3. The deprecated invocation still completes successfully — the
             warning is informational, never aborts the run.
        """
        data_path = tmp_path / "data.jsonl"
        self._make_jsonl(data_path, [{"text": "deprecation contract probe"}])
        out_dir = tmp_path / "audit"

        with patch(
            "sys.argv",
            ["forgelm", "--data-audit", str(data_path), "--output", str(out_dir)],
        ):
            with pytest.warns(DeprecationWarning, match=r"--data-audit"):
                with pytest.raises(SystemExit) as exc_info:
                    main()
            # Contract point #3: deprecated path must still exit 0.
            assert exc_info.value.code == EXIT_SUCCESS

        # Contract point #2: audit log carries the legacy-flag breadcrumb.
        audit_log_path = out_dir / "audit_log.jsonl"
        assert audit_log_path.is_file(), "legacy flag should produce an audit_log.jsonl entry"
        events = [json.loads(line) for line in audit_log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        legacy_events = [e for e in events if e.get("event") == "cli.legacy_flag_invoked"]
        assert len(legacy_events) == 1, f"expected exactly one legacy-flag event, got {legacy_events}"
        evt = legacy_events[0]
        assert evt["flag"] == "--data-audit"
        assert evt["replacement"] == "forgelm audit"
        # v0.7.0 cut-release moved the removal target out one minor to
        # v0.8.0 (preserves the one-minor warning window per
        # docs/standards/release.md#deprecation-cadence).
        assert evt["version"] == "v0.8.0 removal"

        # Sanity: the deprecated path still produced the report — the
        # warning is purely informational.
        assert (out_dir / "data_audit_report.json").is_file()

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
            # Must not raise SystemExit at parse time for in-range values.
            parse_args()
