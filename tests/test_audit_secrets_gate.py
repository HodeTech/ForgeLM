"""``forgelm audit`` critical-secrets exit gate.

Regression cover for the dead credential-leak gate: ``forgelm audit`` used
to print ``Secrets : CRITICAL — N flagged`` and exit ``0``, so every CI
pipeline wired up per the docs ("a `critical` severity exits non-zero so a
CI pipeline fails fast") had a gate that could never fire.

The contract pinned here:

* a critical-severity secrets finding exits ``EXIT_EVAL_FAILURE`` (3);
* a clean corpus still exits ``0``;
* lower-signal findings (PII, quality flags, near-duplicates) do NOT gate;
* ``--allow-secrets`` suppresses the exit code only — never the detection,
  the report, or the operator-facing message;
* the JSON envelope's ``success`` agrees with the exit code, and the
  additive ``secrets_gate`` block explains it.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from forgelm.cli import EXIT_EVAL_FAILURE, _run_data_audit
from forgelm.data_audit import secrets_gate_verdict

# Synthetic, non-live credentials shaped to match the anchored patterns in
# ``forgelm/data_audit/_secrets.py``.
_AWS_KEY = "AKIA" + "IOSFODNN7EXAMPLE"
_OPENAI_KEY = "sk-" + "abcdefghijklmnopqrstuvwxyz0123456789"


def _write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _dirty(path):
    _write_jsonl(
        path,
        [
            {"text": f"export AWS_ACCESS_KEY_ID={_AWS_KEY}"},
            {"text": f"client = OpenAI(api_key='{_OPENAI_KEY}')"},
        ],
    )


def _clean(path):
    _write_jsonl(path, [{"text": "The capital of France is Paris."}, {"text": "Water boils at 100C."}])


class TestSecretsGateExitCode:
    def test_critical_secrets_exit_eval_failure(self, tmp_path):
        """Two live-shaped credentials must fail the run with exit 3."""
        path = tmp_path / "dirty.jsonl"
        _dirty(path)
        with pytest.raises(SystemExit) as exc_info:
            _run_data_audit(str(path), str(tmp_path / "audit"), "text")
        assert exc_info.value.code == EXIT_EVAL_FAILURE

    def test_clean_corpus_still_exits_zero(self, tmp_path):
        """No findings — the worker must return normally (dispatcher exits 0)."""
        path = tmp_path / "clean.jsonl"
        _clean(path)
        _run_data_audit(str(path), str(tmp_path / "audit"), "text")  # no SystemExit

    def test_report_is_written_before_the_gate_fires(self, tmp_path):
        """A gated run still leaves the operator a complete report to triage."""
        path = tmp_path / "dirty.jsonl"
        _dirty(path)
        out_dir = tmp_path / "audit"
        with pytest.raises(SystemExit):
            _run_data_audit(str(path), str(out_dir), "text")
        report = json.loads((out_dir / "data_audit_report.json").read_text(encoding="utf-8"))
        assert report["secrets_summary"], "report must record the findings that caused the gate"

    def test_message_names_the_finding_types_and_the_remedy(self, tmp_path, caplog):
        path = tmp_path / "dirty.jsonl"
        _dirty(path)
        with caplog.at_level("ERROR"):
            with pytest.raises(SystemExit):
                _run_data_audit(str(path), str(tmp_path / "audit"), "text")
        message = caplog.text
        assert "aws_access_key=1" in message
        assert "openai_api_key=1" in message
        assert "--secrets-mask" in message, "operator must be told how to scrub"
        assert "--allow-secrets" in message, "operator must be told the escape hatch"


class TestGateIsSecretsOnly:
    """Blast-radius guard: only critical secrets gate. Nothing else may
    start failing pipelines that pass today."""

    def test_pii_findings_do_not_gate(self, tmp_path):
        path = tmp_path / "pii.jsonl"
        _write_jsonl(
            path,
            [
                {"text": "Contact me at alice@example.com or +1 555 010 1234."},
                {"text": "My card is 4111 1111 1111 1111 and I live in Ankara."},
            ],
        )
        _run_data_audit(str(path), str(tmp_path / "audit"), "text")  # no SystemExit

    def test_quality_and_near_duplicate_findings_do_not_gate(self, tmp_path):
        path = tmp_path / "dupes.jsonl"
        _write_jsonl(path, [{"text": "alpha"}, {"text": "alpha"}, {"text": "b"}])
        _run_data_audit(
            str(path),
            str(tmp_path / "audit"),
            "text",
            enable_quality_filter=True,
        )  # no SystemExit


class TestAllowSecretsOptOut:
    def test_allow_secrets_suppresses_the_exit_only(self, tmp_path, caplog):
        path = tmp_path / "dirty.jsonl"
        _dirty(path)
        out_dir = tmp_path / "audit"
        with caplog.at_level("WARNING"):
            _run_data_audit(str(path), str(out_dir), "text", allow_secrets=True)  # no SystemExit
        assert "SUPPRESSED" in caplog.text, "suppression must be loud, not silent"
        report = json.loads((out_dir / "data_audit_report.json").read_text(encoding="utf-8"))
        assert report["secrets_summary"], "detection must be unaffected by the opt-out"

    def test_gate_is_on_by_default_at_the_dispatch_seam(self):
        """A Namespace without ``allow_secrets`` (the shape any older caller or
        an ``argparse.SUPPRESS`` switch would produce) must gate, not skip."""
        from forgelm.cli.subcommands._audit import _run_audit_cmd

        fake = MagicMock()
        with patch("forgelm.cli._run_data_audit", fake):
            _run_audit_cmd(SimpleNamespace(input_path="data.jsonl"), "text")
        assert fake.call_args.kwargs["allow_secrets"] is False

    def test_flag_is_wired_through_the_parser(self):
        from forgelm.cli._parser import parse_args

        with patch("sys.argv", ["forgelm", "audit", "data.jsonl", "--allow-secrets"]):
            assert parse_args().allow_secrets is True
        with patch("sys.argv", ["forgelm", "audit", "data.jsonl"]):
            assert parse_args().allow_secrets is False


class TestSecretsGateJsonEnvelope:
    def test_failed_gate_sets_success_false(self, tmp_path, capsys):
        path = tmp_path / "dirty.jsonl"
        _dirty(path)
        with pytest.raises(SystemExit) as exc_info:
            _run_data_audit(str(path), str(tmp_path / "audit"), "json")
        assert exc_info.value.code == EXIT_EVAL_FAILURE
        envelope = json.loads(capsys.readouterr().out)
        assert envelope["success"] is False, "success must never disagree with the exit code"
        assert envelope["secrets_gate"]["status"] == "failed"
        assert envelope["secrets_gate"]["severity"] == "critical"
        assert envelope["secrets_gate"]["critical_total"] == 2
        # Pre-existing keys stay put — the block is additive.
        assert envelope["secrets_summary"]
        assert "pii_severity" in envelope

    def test_clean_gate_keeps_success_true(self, tmp_path, capsys):
        path = tmp_path / "clean.jsonl"
        _clean(path)
        _run_data_audit(str(path), str(tmp_path / "audit"), "json")
        envelope = json.loads(capsys.readouterr().out)
        assert envelope["success"] is True
        assert envelope["secrets_gate"]["status"] == "passed"
        assert envelope["secrets_gate"]["severity"] is None

    def test_suppressed_gate_is_distinguishable_from_clean(self, tmp_path, capsys):
        """``--allow-secrets`` exits 0, so the envelope is the only place a
        reviewer can tell "nothing found" from "found and waved through"."""
        path = tmp_path / "dirty.jsonl"
        _dirty(path)
        _run_data_audit(str(path), str(tmp_path / "audit"), "json", allow_secrets=True)
        envelope = json.loads(capsys.readouterr().out)
        assert envelope["success"] is True
        assert envelope["secrets_gate"]["status"] == "suppressed"
        assert envelope["secrets_gate"]["allow_secrets"] is True
        assert envelope["secrets_gate"]["critical_total"] == 2


class TestSecretsGateVerdict:
    """The pure classifier — library callers (wizard BYOD, notebooks) can
    reuse it without inheriting the CLI's exit behaviour."""

    def test_empty_summary_passes(self):
        assert secrets_gate_verdict({})["failed"] is False
        assert secrets_gate_verdict({})["severity"] is None

    def test_zero_counts_never_gate(self):
        """A forward-compatible detector reporting "scanned, found nothing"
        must not fail a pipeline."""
        verdict = secrets_gate_verdict({"aws_access_key": 0, "jwt": 0})
        assert verdict["failed"] is False
        assert verdict["critical_total"] == 0
        assert verdict["critical_types"] == {}

    def test_any_positive_count_is_critical(self):
        verdict = secrets_gate_verdict({"jwt": 3, "github_token": 0, "slack_token": 1})
        assert verdict["failed"] is True
        assert verdict["severity"] == "critical"
        assert verdict["critical_total"] == 4
        assert verdict["critical_types"] == {"jwt": 3, "slack_token": 1}
