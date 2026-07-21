"""Tests for the critical-tier PII gate on ``forgelm audit``.

Sibling of ``tests/test_audit_secrets_gate.py``.  The secrets gate closed a
dead credential-leak check in v0.10.0; this one closes the same shape one
tier down: ``forgelm audit`` detected a real, checksum-valid credit-card
number or IBAN in a training corpus, printed it, and exited ``0``.

The gate is deliberately narrower than the secrets one, and the boundary is
the contract under test here:

* ``credit_card`` and ``iban`` are ``critical`` in
  :data:`forgelm.data_audit.PII_SEVERITY` **and** clear a checksum (Luhn /
  ISO 7064 mod-97), so a hit is a real value rather than a lookalike. These
  gate.
* ``us_ssn``, ``fr_ssn``, ``de_id``, ``tr_id``, ``email`` and ``phone`` are
  sub-critical. Most are matched on regex shape alone and *deliberately*
  over-report. These are reported and never gate — a gate that fires on a
  clean corpus is a gate somebody switches off, taking the trustworthy half
  with it.

:class:`TestCriticalTierIsChecksumBacked` pins the invariant that makes the
gate safe: promoting a shape-matched family to ``critical`` must fail the
suite rather than silently arming the gate on a noisy signal.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from forgelm.cli import EXIT_EVAL_FAILURE, _run_data_audit
from forgelm.data_audit import pii_gate_verdict
from forgelm.data_audit._pii_regex import CHECKSUM_VALIDATED_PII_TYPES
from forgelm.data_audit._types import PII_ML_SEVERITY, PII_SEVERITY

# 4111 1111 1111 1111 is the canonical Visa test number and passes Luhn.
_LUHN_VALID_CARD = "4111 1111 1111 1111"
# Real-shaped IBAN with a correct mod-97 checksum (GB Nat West test value).
_VALID_IBAN = "GB82 WEST 1234 5698 7654 32"


def _write_jsonl(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def _audit(tmp_path, rows, **kwargs):
    path = tmp_path / "corpus.jsonl"
    _write_jsonl(path, rows)
    return _run_data_audit(str(path), str(tmp_path / "audit"), kwargs.pop("fmt", "text"), **kwargs)


# --------------------------------------------------------------------------
# The verdict builder — pure, no exits
# --------------------------------------------------------------------------


class TestPiiGateVerdict:
    def test_empty_summary_passes(self):
        verdict = pii_gate_verdict({})
        assert verdict["failed"] is False
        assert verdict["severity"] is None
        assert verdict["critical_total"] == 0

    def test_none_summary_passes(self):
        """Defensive: a detector that reports nothing must not crash the gate."""
        assert pii_gate_verdict(None)["failed"] is False

    def test_zero_counts_are_ignored(self):
        """A 'scanned, found nothing' key must never fail a pipeline."""
        assert pii_gate_verdict({"credit_card": 0, "iban": 0})["failed"] is False

    def test_critical_category_fails(self):
        verdict = pii_gate_verdict({"credit_card": 2})
        assert verdict["failed"] is True
        assert verdict["severity"] == "critical"
        assert verdict["critical_total"] == 2
        assert verdict["critical_types"] == {"credit_card": 2}

    def test_iban_is_critical_too(self):
        assert pii_gate_verdict({"iban": 1})["failed"] is True

    @pytest.mark.parametrize("kind", ["email", "phone", "us_ssn", "fr_ssn", "de_id", "tr_id"])
    def test_sub_critical_categories_do_not_fail(self, kind):
        verdict = pii_gate_verdict({kind: 99})
        assert verdict["failed"] is False
        assert verdict["critical_total"] == 0
        assert verdict["advisory_types"] == {kind: 99}

    def test_sub_critical_findings_are_still_reported(self):
        """Not gating is not the same as not counting."""
        verdict = pii_gate_verdict({"credit_card": 1, "email": 5, "phone": 3})
        assert verdict["failed"] is True
        assert verdict["critical_types"] == {"credit_card": 1}
        assert verdict["advisory_total"] == 8
        assert verdict["advisory_types"] == {"email": 5, "phone": 3}

    def test_unknown_category_does_not_gate(self):
        """Forward-compat: a family added to the detector but not to
        PII_SEVERITY must not silently inherit the gate."""
        assert pii_gate_verdict({"future_category": 7})["failed"] is False


class TestCriticalTierIsChecksumBacked:
    """The invariant that makes gating on this tier defensible."""

    def test_every_critical_family_clears_a_checksum(self):
        # The merged table is what the gate and the severity report both read, so
        # the invariant must hold across BOTH the regex tier and the ML-NER tier —
        # an ML category promoted to critical (person/org/location) is shape-matched
        # too and would arm the gate on a noisy signal exactly as a regex one would.
        merged = {**PII_SEVERITY, **PII_ML_SEVERITY}
        critical = {k for k, tier in merged.items() if tier == "critical"}
        assert critical, "no critical tier declared — the gate would be dead"
        unvalidated = critical - CHECKSUM_VALIDATED_PII_TYPES
        assert not unvalidated, (
            f"{unvalidated} are critical-tier but shape-matched only. Shape-matched detectors "
            "deliberately over-report, so gating on them fails clean corpora. Either add a "
            "checksum validator or drop the family below 'critical'."
        )

    def test_the_gate_reads_the_declared_tier_not_a_private_copy(self):
        """Changing PII_SEVERITY must move the gate with it."""
        with patch.dict(PII_SEVERITY, {"email": "critical"}):
            assert pii_gate_verdict({"email": 1})["failed"] is True
        assert pii_gate_verdict({"email": 1})["failed"] is False

    def test_the_gate_honours_an_upgraded_ml_category(self):
        """The report and the exit code must agree when an ML tier is raised.

        Before the merged-table fix, `_build_pii_severity` reported worst_tier
        'critical' for an upgraded ML category while `pii_gate_verdict` stayed
        green — verdict and exit code disagreeing on the same finding.
        """
        with patch.dict(PII_ML_SEVERITY, {"person": "critical"}):
            assert pii_gate_verdict({"person": 1})["failed"] is True
        assert pii_gate_verdict({"person": 1})["failed"] is False


# --------------------------------------------------------------------------
# End-to-end through the CLI worker
# --------------------------------------------------------------------------


class TestPiiGateExitCode:
    def test_valid_card_exits_eval_failure(self, tmp_path):
        with pytest.raises(SystemExit) as exc:
            _audit(tmp_path, [{"text": f"My card is {_LUHN_VALID_CARD} thanks"}])
        assert exc.value.code == EXIT_EVAL_FAILURE

    def test_valid_iban_exits_eval_failure(self, tmp_path):
        with pytest.raises(SystemExit) as exc:
            _audit(tmp_path, [{"text": f"Transfer to {_VALID_IBAN} today"}])
        assert exc.value.code == EXIT_EVAL_FAILURE

    def test_clean_corpus_exits_zero(self, tmp_path):
        _audit(tmp_path, [{"text": "a normal training sample about cooking"}])  # no SystemExit

    def test_sub_critical_pii_exits_zero(self, tmp_path):
        """The false-positive control — this is what keeps the gate usable."""
        _audit(
            tmp_path,
            [
                {"text": "Contact alice@example.com or +1 555 010 1234"},
                {"text": "SSN 123-45-6789"},
            ],
        )  # no SystemExit

    def test_luhn_valid_non_cards_do_not_gate(self, tmp_path):
        """The regression that motivated the issuer-prefix requirement.

        A corpus of device IMEIs, ISBNs, git SHAs, order numbers and UUIDs
        contains no card and no IBAN. A bare-Luhn card check fired on the IMEIs
        (Luhn is their own check digit) and failed the build. The gate must
        stay silent here.
        """
        _audit(
            tmp_path,
            [
                {"text": "IMEI 490154203237518, invoice INV-2024-0001234567."},
                {"text": "ISBN 978-0-306-40615-7; commit a1b2c3d4e5f6789012345678901234567890abcd"},
                {"text": "uuid 550e8400-e29b-41d4-a716-446655440000, part no 1800000000000008"},
            ],
        )  # no SystemExit

    def test_report_is_written_before_the_gate_fires(self, tmp_path):
        out_dir = tmp_path / "audit"
        with pytest.raises(SystemExit):
            _audit(tmp_path, [{"text": f"card {_LUHN_VALID_CARD}"}])
        assert (out_dir / "data_audit_report.json").is_file()

    def test_message_names_the_category_and_the_remedy(self, tmp_path, caplog):
        with caplog.at_level("ERROR"):
            with pytest.raises(SystemExit):
                _audit(tmp_path, [{"text": f"card {_LUHN_VALID_CARD}"}])
        text = caplog.text
        assert "credit_card=1" in text
        assert "--pii-mask" in text
        assert "--allow-pii" in text


class TestAllowPiiOptOut:
    def test_allow_pii_suppresses_the_exit_only(self, tmp_path, caplog):
        out_dir = tmp_path / "audit"
        with caplog.at_level("WARNING"):
            _audit(tmp_path, [{"text": f"card {_LUHN_VALID_CARD}"}], allow_pii=True)  # no SystemExit
        assert "SUPPRESSED" in caplog.text
        report = json.loads((out_dir / "data_audit_report.json").read_text(encoding="utf-8"))
        assert report["pii_summary"].get("credit_card") == 1, "detection must be unaffected"

    def test_gate_is_on_by_default_at_the_dispatch_seam(self):
        """A default of True anywhere in the chain would silently disarm the gate."""
        import inspect

        sig = inspect.signature(_run_data_audit)
        assert sig.parameters["allow_pii"].default is False

    def test_flag_is_wired_through_the_parser(self):
        from forgelm.cli._parser import parse_args

        with patch("sys.argv", ["forgelm", "audit", "data.jsonl", "--allow-pii"]):
            assert parse_args().allow_pii is True
        with patch("sys.argv", ["forgelm", "audit", "data.jsonl"]):
            assert parse_args().allow_pii is False

    def test_flag_reaches_the_worker_from_the_dispatch_seam(self):
        """A missing getattr default would silently drop the flag."""
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from forgelm.cli.subcommands._audit import _run_audit_cmd

        fake = MagicMock()
        with patch("forgelm.cli._run_data_audit", fake):
            _run_audit_cmd(SimpleNamespace(input_path="data.jsonl", allow_pii=True), "text")
        assert fake.call_args.kwargs["allow_pii"] is True

    def test_allow_pii_does_not_suppress_the_secrets_gate(self, tmp_path):
        """The two escape hatches are independent."""
        with pytest.raises(SystemExit) as exc:
            _audit(
                tmp_path,
                [{"text": "key AKIAIOSFODNN7EXAMPLE here"}],
                allow_pii=True,
            )
        assert exc.value.code == EXIT_EVAL_FAILURE


class TestBothGatesReport:
    def test_a_corpus_failing_both_gates_reports_both(self, tmp_path, caplog):
        """An operator who only learns about one will fix it, re-run, and meet the other."""
        with caplog.at_level("ERROR"):
            with pytest.raises(SystemExit) as exc:
                _audit(
                    tmp_path,
                    [{"text": f"key AKIAIOSFODNN7EXAMPLE and card {_LUHN_VALID_CARD}"}],
                )
        assert exc.value.code == EXIT_EVAL_FAILURE
        assert "PII gate FAILED" in caplog.text
        assert "Secrets gate FAILED" in caplog.text


class TestPiiGateJsonEnvelope:
    def test_failed_gate_sets_success_false(self, tmp_path, capsys):
        with pytest.raises(SystemExit):
            _audit(tmp_path, [{"text": f"card {_LUHN_VALID_CARD}"}], fmt="json")
        payload = json.loads(capsys.readouterr().out)
        assert payload["success"] is False
        assert payload["pii_gate"]["status"] == "failed"
        assert payload["pii_gate"]["critical_types"] == {"credit_card": 1}

    def test_suppressed_status_is_distinct_from_passed(self, tmp_path, capsys):
        _audit(tmp_path, [{"text": f"card {_LUHN_VALID_CARD}"}], fmt="json", allow_pii=True)
        payload = json.loads(capsys.readouterr().out)
        assert payload["success"] is True
        assert payload["pii_gate"]["status"] == "suppressed"
        assert payload["pii_gate"]["allow_pii"] is True

    def test_clean_corpus_reports_passed(self, tmp_path, capsys):
        _audit(tmp_path, [{"text": "nothing to see"}], fmt="json")
        payload = json.loads(capsys.readouterr().out)
        assert payload["pii_gate"]["status"] == "passed"
        assert payload["pii_gate"]["severity"] is None

    def test_advisory_counts_surface_in_the_envelope(self, tmp_path, capsys):
        _audit(tmp_path, [{"text": "mail alice@example.com"}], fmt="json")
        payload = json.loads(capsys.readouterr().out)
        assert payload["pii_gate"]["advisory_types"].get("email") == 1
        assert payload["pii_gate"]["status"] == "passed"
