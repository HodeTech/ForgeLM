"""Phase 37: ``forgelm approvals`` listing + show subcommand.

Mirrors the ``test_human_approval_gate.py`` style — synthetic JSONL audit
log fixtures so the tests stay torch-free and run on every CI matrix combo.
"""

from __future__ import annotations

import json

# ---------------------------------------------------------------------------
# Helpers (mirror tests/test_human_approval_gate.py)
# ---------------------------------------------------------------------------
# Wave 2a Round-2 F-TEST-37-03: monotonically-increasing default timestamp
# so any sort-order assertion in the suite has a real signal to compare
# against (the prior single-hardcoded-value made every sort test vacuous).
# Tests that need a specific timestamp pass it via the ``timestamp`` kwarg.
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

_DEFAULT_TS_BASE = datetime(2026, 4, 30, 12, 0, tzinfo=timezone.utc)
_NEXT_DEFAULT_TS = [_DEFAULT_TS_BASE]


def _next_default_ts() -> str:
    """Return the next monotonic ISO-8601 timestamp for fixture events."""
    _NEXT_DEFAULT_TS[0] += timedelta(seconds=1)
    return _NEXT_DEFAULT_TS[0].isoformat()


def _write_event(audit_path: Path, event: str, run_id: str, **fields: object) -> None:
    """Append a synthetic event to ``audit_path``.

    Produces the same JSONL shape AuditLogger writes: one event per line,
    a ``timestamp`` / ``operator`` / ``event`` / ``run_id`` core, and
    whatever extra fields the caller passes (``staging_path``, ``metrics``,
    ``approver``, ``comment`` etc.).

    ``timestamp`` defaults to a monotonically-increasing value per call
    (Wave 2a Round-2 F-TEST-37-03 fix).  Pass ``timestamp="..."`` in
    ``**fields`` to override.
    """
    if "timestamp" not in fields:
        fields = {**fields, "timestamp": _next_default_ts()}
    entry = {
        "operator": "tester",
        "event": event,
        "run_id": run_id,
        "prev_hash": "genesis",
    }
    entry.update(fields)
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with open(audit_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def _seed_run(
    output_dir: Path,
    run_id: str,
    *,
    decision: str | None = None,
    create_staging: bool = True,
) -> Path:
    """Create a run layout: staging dir + ``human_approval.required`` event.

    When ``decision`` is given (``"granted"`` / ``"rejected"``), append the
    matching terminal event so the run is no longer pending.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    staging_dir = output_dir / "final_model.staging"
    if create_staging:
        staging_dir.mkdir(exist_ok=True)
        (staging_dir / "adapter_config.json").write_text('{"r": 8}', encoding="utf-8")
    audit_path = output_dir / "audit_log.jsonl"
    _write_event(
        audit_path,
        "human_approval.required",
        run_id,
        staging_path=str(staging_dir),
        gate="final_model",
        reason="require_human_approval=true",
        metrics={"eval_loss": 0.42},
    )
    if decision == "granted":
        _write_event(
            audit_path,
            "human_approval.granted",
            run_id,
            approver="alice",
            comment="LGTM",
            promote_strategy="rename",
        )
    elif decision == "rejected":
        _write_event(
            audit_path,
            "human_approval.rejected",
            run_id,
            approver="bob",
            comment="regression",
            staging_path=str(staging_dir),
        )
    return staging_dir


def _build_args(output_dir: Path, *, pending: bool = False, show: str | None = None) -> SimpleNamespace:
    """Build a strict argparse-shaped namespace.

    Wave 2a Round-2 nit: was a ``MagicMock`` which silently allowed
    misspelled or missing CLI attributes (``args.pendng`` would have
    returned a Mock instead of failing the test).  ``SimpleNamespace``
    raises ``AttributeError`` on any access the harness did not declare,
    so a future refactor that reads a new attribute lights up here
    instead of silently passing.
    """
    return SimpleNamespace(pending=pending, show=show, output_dir=str(output_dir))


# ---------------------------------------------------------------------------
# --pending mode
# ---------------------------------------------------------------------------


class TestApprovalsPending:
    def test_empty_audit_log_returns_empty_list(self, tmp_path: Path, capsys) -> None:
        output_dir = tmp_path / "run"
        output_dir.mkdir()
        # No audit_log.jsonl on disk → friendly empty path, exit 0.
        from forgelm.cli import _run_approvals_cmd

        with pytest.raises(SystemExit) as ei:
            _run_approvals_cmd(_build_args(output_dir, pending=True), output_format="text")
        assert ei.value.code == 0
        captured = capsys.readouterr().out
        assert "nothing to list" in captured.lower() or "no pending" in captured.lower()

    def test_three_runs_one_decided_two_pending(self, tmp_path: Path, capsys) -> None:
        # Three runs, one already granted → listing returns the other two.
        output_dir = tmp_path / "run"
        _seed_run(output_dir, "fg-aaaa00000000")
        _seed_run(output_dir, "fg-bbbb00000000", decision="granted")
        _seed_run(output_dir, "fg-cccc00000000")

        from forgelm.cli import _run_approvals_cmd

        with pytest.raises(SystemExit) as ei:
            _run_approvals_cmd(_build_args(output_dir, pending=True), output_format="json")
        assert ei.value.code == 0

        payload = json.loads(capsys.readouterr().out)
        assert payload["success"] is True
        assert payload["count"] == 2
        run_ids = {summary["run_id"] for summary in payload["pending"]}
        assert run_ids == {"fg-aaaa00000000", "fg-cccc00000000"}

    def test_rejected_run_also_excluded_from_pending(self, tmp_path: Path, capsys) -> None:
        # Reject (like granted) is a terminal decision; rejected runs must
        # not appear in --pending even though their staging dir lingers.
        output_dir = tmp_path / "run"
        _seed_run(output_dir, "fg-rejected0000", decision="rejected")
        _seed_run(output_dir, "fg-pending00000")

        from forgelm.cli import _run_approvals_cmd

        with pytest.raises(SystemExit) as ei:
            _run_approvals_cmd(_build_args(output_dir, pending=True), output_format="json")
        assert ei.value.code == 0

        payload = json.loads(capsys.readouterr().out)
        assert payload["count"] == 1
        assert payload["pending"][0]["run_id"] == "fg-pending00000"

    def test_pending_summary_carries_metrics_and_staging_path(self, tmp_path: Path, capsys) -> None:
        output_dir = tmp_path / "run"
        staging_dir = _seed_run(output_dir, "fg-zzzz00000000")

        from forgelm.cli import _run_approvals_cmd

        with pytest.raises(SystemExit):
            _run_approvals_cmd(_build_args(output_dir, pending=True), output_format="json")
        payload = json.loads(capsys.readouterr().out)
        summary = payload["pending"][0]
        assert summary["staging_path"] == str(staging_dir)
        assert summary["staging_exists"] is True
        assert summary["metrics"] == {"eval_loss": 0.42}
        assert summary["reason"] == "require_human_approval=true"

    def test_pending_summary_includes_config_hash(self, tmp_path: Path, capsys) -> None:
        """XP-11 / F-P4-OPUS-05: when the human_approval.required event records
        a config_hash, the pending summary surfaces it. Pre-fix the event never
        carried config_hash so pending[].config_hash was unconditionally null."""
        output_dir = tmp_path / "run"
        output_dir.mkdir(parents=True, exist_ok=True)
        staging_dir = output_dir / "final_model.staging"
        staging_dir.mkdir(exist_ok=True)
        (staging_dir / "adapter_config.json").write_text('{"r": 8}', encoding="utf-8")
        _write_event(
            output_dir / "audit_log.jsonl",
            "human_approval.required",
            "fg-hash00000000",
            staging_path=str(staging_dir),
            gate="final_model",
            reason="require_human_approval=true",
            metrics={"eval_loss": 0.42},
            config_hash="sha256:deadbeef",
        )

        from forgelm.cli import _run_approvals_cmd

        with pytest.raises(SystemExit):
            _run_approvals_cmd(_build_args(output_dir, pending=True), output_format="json")
        payload = json.loads(capsys.readouterr().out)
        assert payload["pending"][0]["config_hash"] == "sha256:deadbeef"

    def test_text_output_renders_table(self, tmp_path: Path, capsys) -> None:
        output_dir = tmp_path / "run"
        _seed_run(output_dir, "fg-xyz000000000")

        from forgelm.cli import _run_approvals_cmd

        with pytest.raises(SystemExit):
            _run_approvals_cmd(_build_args(output_dir, pending=True), output_format="text")
        out = capsys.readouterr().out
        # Table headers + run id present.
        assert "RUN_ID" in out
        assert "fg-xyz000000000" in out
        # The "present" / "MISSING" staging column is rendered.
        assert "present" in out


# ---------------------------------------------------------------------------
# --show mode
# ---------------------------------------------------------------------------


class TestApprovalsShow:
    def test_show_pending_run_emits_full_chain(self, tmp_path: Path, capsys) -> None:
        output_dir = tmp_path / "run"
        _seed_run(output_dir, "fg-pp0000000000")

        from forgelm.cli import _run_approvals_cmd

        with pytest.raises(SystemExit) as ei:
            _run_approvals_cmd(_build_args(output_dir, show="fg-pp0000000000"), output_format="json")
        assert ei.value.code == 0

        payload = json.loads(capsys.readouterr().out)
        assert payload["success"] is True
        assert payload["status"] == "pending"
        assert len(payload["chain"]) == 1
        assert payload["chain"][0]["event"] == "human_approval.required"
        # Staging dir was seeded, so contents include adapter_config.json.
        assert "adapter_config.json" in payload["staging_contents"]

    def test_show_granted_run_status_granted(self, tmp_path: Path, capsys) -> None:
        output_dir = tmp_path / "run"
        _seed_run(output_dir, "fg-gr0000000000", decision="granted")

        from forgelm.cli import _run_approvals_cmd

        with pytest.raises(SystemExit) as ei:
            _run_approvals_cmd(_build_args(output_dir, show="fg-gr0000000000"), output_format="json")
        assert ei.value.code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["status"] == "granted"
        # Two events: required + granted.
        assert len(payload["chain"]) == 2
        assert payload["chain"][-1]["event"] == "human_approval.granted"

    def test_show_unknown_run_id_exits_1(self, tmp_path: Path, capsys) -> None:
        output_dir = tmp_path / "run"
        _seed_run(output_dir, "fg-real00000000")

        from forgelm.cli import _run_approvals_cmd

        with pytest.raises(SystemExit) as ei:
            _run_approvals_cmd(_build_args(output_dir, show="fg-fake00000000"), output_format="text")
        assert ei.value.code == 1
        # Error message names the missing run id.
        # (Captured in stderr via the logger; capsys captures stdout for the
        # JSON path but the text path goes through ``logger.error``; assert
        # via caplog instead in a dedicated test below.)

    def test_show_no_audit_log_exits_1(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "run"
        output_dir.mkdir()
        # No audit_log.jsonl on disk; --show cannot operate.
        from forgelm.cli import _run_approvals_cmd

        with pytest.raises(SystemExit) as ei:
            _run_approvals_cmd(_build_args(output_dir, show="fg-anything0000"), output_format="text")
        assert ei.value.code == 1

    def test_show_text_renders_timeline(self, tmp_path: Path, capsys) -> None:
        output_dir = tmp_path / "run"
        _seed_run(output_dir, "fg-tt0000000000", decision="granted")

        from forgelm.cli import _run_approvals_cmd

        with pytest.raises(SystemExit):
            _run_approvals_cmd(_build_args(output_dir, show="fg-tt0000000000"), output_format="text")
        out = capsys.readouterr().out
        assert "fg-tt0000000000" in out
        assert "human_approval.required" in out
        assert "human_approval.granted" in out
        assert "by alice" in out  # approver rendered


# ---------------------------------------------------------------------------
# Subcommand registration smoke
# ---------------------------------------------------------------------------


class TestApprovalsRegistration:
    def test_approvals_subcommand_registered(self) -> None:
        """``forgelm approvals --help`` must succeed (subparser exists)."""
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, "-m", "forgelm.cli", "approvals", "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        # Both modes are advertised in the help text.
        assert "--pending" in result.stdout
        assert "--show" in result.stdout
        assert "--output-dir" in result.stdout


# ---------------------------------------------------------------------------
# Robustness against malformed audit log lines
# ---------------------------------------------------------------------------


class TestApprovalsMalformedLines:
    def test_skipped_lines_do_not_hide_pending_runs(self, tmp_path: Path, capsys) -> None:
        # Simulate a corrupt audit log: a real `required` event followed by
        # a malformed line and a JSON list (non-dict root).  The pending
        # listing must still surface the legitimate run.
        output_dir = tmp_path / "run"
        output_dir.mkdir()
        audit_path = output_dir / "audit_log.jsonl"
        _write_event(
            audit_path,
            "human_approval.required",
            "fg-real00000000",
            staging_path=str(output_dir / "final_model.staging"),
        )
        with open(audit_path, "a", encoding="utf-8") as fh:
            fh.write("not even json\n")
            fh.write('["array", "root"]\n')
        # Create the staging dir so staging_exists is True (defensive — not
        # required for the test, just realistic).
        (output_dir / "final_model.staging").mkdir()

        from forgelm.cli import _run_approvals_cmd

        with pytest.raises(SystemExit):
            _run_approvals_cmd(_build_args(output_dir, pending=True), output_format="json")
        payload = json.loads(capsys.readouterr().out)
        assert payload["count"] == 1
        assert payload["pending"][0]["run_id"] == "fg-real00000000"


# ---------------------------------------------------------------------------
# Defensive: dispatcher rejects neither-mode args
# ---------------------------------------------------------------------------


class TestApprovalsDispatcherDefensive:
    def test_neither_pending_nor_show_exits_1(self, tmp_path: Path) -> None:
        """argparse normally enforces the mutex; this exercises the
        defensive double-check inside the dispatcher in case a future
        refactor drops the parser-level guard."""
        output_dir = tmp_path / "run"
        output_dir.mkdir()
        args = _build_args(output_dir, pending=False, show=None)

        from forgelm.cli import _run_approvals_cmd

        with pytest.raises(SystemExit) as ei:
            _run_approvals_cmd(args, output_format="text")
        assert ei.value.code == 1


# ---------------------------------------------------------------------------
# Patch-based test for monkeypatch resolution discipline
# ---------------------------------------------------------------------------


class TestApprovalsMonkeyPatchSurface:
    def test_helper_reachable_via_facade(self) -> None:
        """``forgelm.cli._collect_pending_runs`` must resolve so tests can
        ``patch("forgelm.cli._collect_pending_runs", ...)`` if they want."""
        from forgelm import cli as _cli_facade

        assert callable(_cli_facade._collect_pending_runs)
        assert callable(_cli_facade._collect_run_audit_chain)
        assert callable(_cli_facade._run_approvals_cmd)


@pytest.mark.usefixtures("caplog")
class TestApprovalsErrorMessages:
    def test_unknown_run_logs_actionable_error(self, tmp_path: Path, caplog) -> None:
        import logging

        output_dir = tmp_path / "run"
        _seed_run(output_dir, "fg-real00000000")

        from forgelm.cli import _run_approvals_cmd

        with caplog.at_level(logging.ERROR, logger="forgelm.cli"):
            with patch("forgelm.cli._collect_pending_runs"):  # ensure not called by --show
                with pytest.raises(SystemExit) as ei:
                    _run_approvals_cmd(_build_args(output_dir, show="fg-fake00000000"), output_format="text")
        assert ei.value.code == 1
        assert "fg-fake00000000" in caplog.text


# ---------------------------------------------------------------------------
# Wave 2a Round-1 review fixes — security + bot-suggested coverage
# ---------------------------------------------------------------------------


class TestApprovalsPathTraversalGuard:
    """F-25-01: tampered audit log with staging_path outside output_dir
    must not produce a directory-listing oracle via _staging_contents."""

    def test_show_refuses_external_staging_path(self, tmp_path: Path, capsys) -> None:
        output_dir = tmp_path / "run"
        output_dir.mkdir()
        # Malicious audit event: staging_path points outside output_dir.
        _write_event(
            output_dir / "audit_log.jsonl",
            "human_approval.required",
            "fg-tampered00000",
            staging_path="/etc",  # would expose /etc listing if guard absent
        )
        # Provide a benign fallback so the test can verify the listing
        # is empty (not /etc/...).
        from forgelm.cli import _run_approvals_cmd

        with pytest.raises(SystemExit):
            _run_approvals_cmd(_build_args(output_dir, show="fg-tampered00000"), output_format="json")
        payload = json.loads(capsys.readouterr().out)
        # The dispatcher must NOT have leaked /etc entries into staging_contents.
        assert all("etc" not in entry for entry in payload["staging_contents"]), (
            f"path-traversal guard failed: leaked /etc entries: {payload['staging_contents']}"
        )

    def test_pending_refuses_external_staging_path(self, tmp_path: Path, capsys) -> None:
        output_dir = tmp_path / "run"
        output_dir.mkdir()
        _write_event(
            output_dir / "audit_log.jsonl",
            "human_approval.required",
            "fg-tampered00000",
            staging_path="/etc",
        )
        from forgelm.cli import _run_approvals_cmd

        with pytest.raises(SystemExit):
            _run_approvals_cmd(_build_args(output_dir, pending=True), output_format="json")
        payload = json.loads(capsys.readouterr().out)
        # staging_path field falls back to None / canonical when the
        # declared one is rejected; staging_exists must be False.
        summary = payload["pending"][0]
        assert summary["staging_exists"] is False
        # staging_path is either None (no fallback found) or the
        # canonical final_model.staging — never the rejected /etc.
        assert summary["staging_path"] != "/etc"


class TestApprovalsLatestWinsRunIdReuse:
    """F-25-03: latest-wins semantics for re-staged runs.

    A run that was rejected and then re-staged with the same run_id
    (a second human_approval.required event) must show as PENDING
    again; the older terminal decision is no longer operative."""

    def test_re_staged_run_shows_as_pending(self, tmp_path: Path, capsys) -> None:
        output_dir = tmp_path / "run"
        _seed_run(output_dir, "fg-restaged00000", decision="rejected")
        # Now re-stage: append another human_approval.required AFTER the
        # rejection.  Latest-wins → pending again.
        _write_event(
            output_dir / "audit_log.jsonl",
            "human_approval.required",
            "fg-restaged00000",
            staging_path=str(output_dir / "final_model.staging"),
            metrics={"eval_loss": 0.30},
        )
        from forgelm.cli import _run_approvals_cmd

        with pytest.raises(SystemExit):
            _run_approvals_cmd(_build_args(output_dir, pending=True), output_format="json")
        payload = json.loads(capsys.readouterr().out)
        run_ids = {summary["run_id"] for summary in payload["pending"]}
        assert "fg-restaged00000" in run_ids


class TestApprovalsShowRejectedAndMissingStaging:
    """Bot-suggested coverage: rejected --show + MISSING staging table."""

    def test_show_rejected_run_status_rejected(self, tmp_path: Path, capsys) -> None:
        output_dir = tmp_path / "run"
        _seed_run(output_dir, "fg-rj0000000000", decision="rejected")

        from forgelm.cli import _run_approvals_cmd

        with pytest.raises(SystemExit) as ei:
            _run_approvals_cmd(_build_args(output_dir, show="fg-rj0000000000"), output_format="json")
        assert ei.value.code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["status"] == "rejected"
        # Two events: required + rejected.
        assert len(payload["chain"]) == 2
        assert payload["chain"][-1]["event"] == "human_approval.rejected"

    def test_pending_table_renders_missing_when_staging_absent(self, tmp_path: Path, capsys) -> None:
        # Seed without creating the staging directory.
        output_dir = tmp_path / "run"
        _seed_run(output_dir, "fg-nostaging000", create_staging=False)

        from forgelm.cli import _run_approvals_cmd

        with pytest.raises(SystemExit):
            _run_approvals_cmd(_build_args(output_dir, pending=True), output_format="text")
        out = capsys.readouterr().out
        assert "MISSING" in out

    def test_pending_empty_audit_log_json_envelope(self, tmp_path: Path, capsys) -> None:
        output_dir = tmp_path / "run"
        output_dir.mkdir()
        from forgelm.cli import _run_approvals_cmd

        with pytest.raises(SystemExit) as ei:
            _run_approvals_cmd(_build_args(output_dir, pending=True), output_format="json")
        assert ei.value.code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload == {"success": True, "pending": [], "count": 0}

    def test_show_unknown_run_id_json_envelope(self, tmp_path: Path, capsys) -> None:
        output_dir = tmp_path / "run"
        _seed_run(output_dir, "fg-real00000000")

        from forgelm.cli import _run_approvals_cmd

        with pytest.raises(SystemExit) as ei:
            _run_approvals_cmd(_build_args(output_dir, show="fg-fake00000000"), output_format="json")
        assert ei.value.code == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["success"] is False
        assert "fg-fake00000000" in payload["error"]

    def test_show_robust_to_malformed_lines(self, tmp_path: Path, capsys) -> None:
        # --show must skip malformed lines the same way --pending does.
        output_dir = tmp_path / "run"
        _seed_run(output_dir, "fg-real00000000")
        with open(output_dir / "audit_log.jsonl", "a", encoding="utf-8") as fh:
            fh.write("not even json\n")
            fh.write('["array", "root"]\n')

        from forgelm.cli import _run_approvals_cmd

        with pytest.raises(SystemExit) as ei:
            _run_approvals_cmd(_build_args(output_dir, show="fg-real00000000"), output_format="json")
        assert ei.value.code == 0
        payload = json.loads(capsys.readouterr().out)
        # Real human_approval.required event is still surfaced.
        assert any(e.get("event") == "human_approval.required" for e in payload["chain"])

    def test_show_corrupted_decision_line_flags_corruption_not_silent_pending(self, tmp_path: Path, capsys) -> None:
        """F-P4-OPUS-29: a corrupted ``human_approval.granted`` line must not be
        silently dropped and rendered as a benign "pending". ``--show`` flags
        the corruption (matching the strict approve/reject guard's verdict on
        the same log) instead of an authoritative status."""
        output_dir = tmp_path / "run"
        _seed_run(output_dir, "fg-corrupt00000", decision="granted")
        audit_path = output_dir / "audit_log.jsonl"
        # Corrupt the granted line (last line) -> lenient parser skips it.
        lines = audit_path.read_text(encoding="utf-8").splitlines()
        assert "human_approval.granted" in lines[-1]
        lines[-1] = "{this is no longer valid json"
        audit_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        from forgelm.cli import _run_approvals_cmd

        with pytest.raises(SystemExit) as ei:
            _run_approvals_cmd(_build_args(output_dir, show="fg-corrupt00000"), output_format="json")
        assert ei.value.code == 0
        payload = json.loads(capsys.readouterr().out)
        # Lenient parse would report "pending"; the corruption must be surfaced
        # so that status is not silently authoritative.
        assert payload.get("corrupted") is True
        assert "corrupted at line" in payload.get("corruption_detail", "")


# ---------------------------------------------------------------------------
# Wave 2a Round-2 hardening: sort order, latest-wins, path-traversal,
# format helpers, classify-chain re-stage semantics.
# ---------------------------------------------------------------------------


class TestApprovalsPendingSortOrder:
    """F-TEST-37-02: pending list is sorted newest-first."""

    def test_pending_sorted_newest_first(self, tmp_path: Path, capsys) -> None:
        from forgelm.cli.subcommands._approvals import _run_approvals_cmd

        output_dir = tmp_path / "run"
        output_dir.mkdir()
        # Three runs with monotonically-increasing default timestamps.
        # Default _next_default_ts is monotonically incrementing across
        # _write_event calls — so writing in order [old, mid, new] produces
        # ascending timestamps and the pending list should reverse to
        # [new, mid, old].
        _seed_run(output_dir, "fg-old0000000000")
        _seed_run(output_dir, "fg-mid0000000000")
        _seed_run(output_dir, "fg-new0000000000")
        with pytest.raises(SystemExit):
            _run_approvals_cmd(_build_args(output_dir, pending=True), output_format="json")
        payload = json.loads(capsys.readouterr().out)
        run_ids = [s["run_id"] for s in payload["pending"]]
        assert run_ids == ["fg-new0000000000", "fg-mid0000000000", "fg-old0000000000"], (
            f"pending should be newest-first, got {run_ids!r}"
        )


class TestFormatAge:
    """F-TEST-37-06: _format_age bucket rendering."""

    @pytest.mark.parametrize(
        "seconds,expected",
        [
            (None, "unknown"),
            (5, "5s"),
            (59, "59s"),
            (60, "1m"),
            (90, "1m"),
            (3599, "59m"),
            (3600, "1h"),
            (7200, "2h"),
            (7260, "2h 1m"),
            (86399, "23h 59m"),
            (86400, "1d"),
            (90000, "1d"),
        ],
    )
    def test_buckets(self, seconds, expected) -> None:
        from forgelm.cli.subcommands._approvals import _format_age

        assert _format_age(seconds) == expected


class TestClassifyChainLatestWins:
    """F-37-01 + user inline: _classify_chain agrees with _collect_pending_runs."""

    def test_re_stage_after_grant_returns_pending(self, tmp_path: Path) -> None:
        """A run that was granted, then re-staged for a second review,
        must classify as ``pending`` — not ``granted``.  The previous
        implementation only looked at decisions and would silently
        report a stale ``granted`` even though the operator's pending
        list correctly surfaced the run."""
        from forgelm.cli.subcommands._approvals import (
            _classify_chain,
            _collect_run_audit_chain,
        )

        output_dir = tmp_path / "run"
        output_dir.mkdir()
        audit = output_dir / "audit_log.jsonl"
        _write_event(audit, "human_approval.required", "fg-x", staging_path=str(output_dir / "final_model.staging"))
        _write_event(audit, "human_approval.granted", "fg-x", approver="alice")
        _write_event(audit, "human_approval.required", "fg-x", staging_path=str(output_dir / "final_model.staging.2"))
        chain = _collect_run_audit_chain(str(audit), "fg-x")
        assert _classify_chain(chain) == "pending"

    def test_re_stage_after_reject_returns_pending(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._approvals import (
            _classify_chain,
            _collect_run_audit_chain,
        )

        output_dir = tmp_path / "run"
        output_dir.mkdir()
        audit = output_dir / "audit_log.jsonl"
        _write_event(audit, "human_approval.required", "fg-x")
        _write_event(audit, "human_approval.rejected", "fg-x", approver="alice")
        _write_event(audit, "human_approval.required", "fg-x")
        chain = _collect_run_audit_chain(str(audit), "fg-x")
        assert _classify_chain(chain) == "pending"

    def test_required_then_granted_returns_granted(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._approvals import (
            _classify_chain,
            _collect_run_audit_chain,
        )

        output_dir = tmp_path / "run"
        output_dir.mkdir()
        audit = output_dir / "audit_log.jsonl"
        _write_event(audit, "human_approval.required", "fg-x")
        _write_event(audit, "human_approval.granted", "fg-x", approver="alice")
        chain = _collect_run_audit_chain(str(audit), "fg-x")
        assert _classify_chain(chain) == "granted"

    def test_only_required_returns_pending(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._approvals import (
            _classify_chain,
            _collect_run_audit_chain,
        )

        output_dir = tmp_path / "run"
        output_dir.mkdir()
        audit = output_dir / "audit_log.jsonl"
        _write_event(audit, "human_approval.required", "fg-x")
        chain = _collect_run_audit_chain(str(audit), "fg-x")
        assert _classify_chain(chain) == "pending"

    def test_empty_chain_returns_unknown(self) -> None:
        from forgelm.cli.subcommands._approvals import _classify_chain

        assert _classify_chain([]) == "unknown"


class TestStagingPathTraversalSandwich:
    """F-TEST-37-01: path-traversal guard is the load-bearing protection.

    The previous test could NOT distinguish a working guard from a no-op
    because the canonical fallback dir didn't exist — so an empty
    staging_contents could mean either "guard fired and fell back to
    nothing" OR "guard was a no-op and the listdir returned nothing".
    The fix: create the fallback dir with a sentinel file so the test
    SEES the difference (guard fires → sentinel surfaces; guard no-ops
    → /etc contents leak).  Also assert the warning was logged.
    """

    def test_show_refuses_external_staging_path_with_caplog(self, tmp_path: Path, capsys, caplog) -> None:
        import logging

        from forgelm.cli.subcommands._approvals import _run_approvals_cmd

        output_dir = tmp_path / "run"
        output_dir.mkdir()
        # Create the canonical fallback with a sentinel so a working
        # guard surfaces ["fallback_marker.txt"] (NOT /etc contents).
        fallback = output_dir / "final_model.staging"
        fallback.mkdir()
        (fallback / "fallback_marker.txt").write_text("ok")
        audit = output_dir / "audit_log.jsonl"
        # Plant an attacker-controlled staging_path pointing outside output_dir.
        _write_event(
            audit,
            "human_approval.required",
            "fg-attack0000000",
            staging_path="/etc",
        )

        with caplog.at_level(logging.WARNING):
            with pytest.raises(SystemExit) as ei:
                _run_approvals_cmd(
                    _build_args(output_dir, show="fg-attack0000000"),
                    output_format="json",
                )
        assert ei.value.code == 0
        payload = json.loads(capsys.readouterr().out)
        # Sentinel proves the fallback path was used (not /etc contents).
        assert payload["staging_contents"] == ["fallback_marker.txt"]
        # Warning proves the guard actually fired.
        assert any("Refusing staging_path" in r.message for r in caplog.records), (
            f"path-traversal guard must log a warning; got: {[r.message for r in caplog.records]}"
        )
        assert any("/etc" in r.message for r in caplog.records)


class TestShowUsesLatestRequired:
    """User inline: _run_approvals_show should pick latest required, not first.

    Previously the code did ``next((e for e in chain if ... required), {})``
    which returned the FIRST required event.  After a re-stage with a fresh
    staging directory, --show would surface the STALE original directory.
    The fix walks reversed(chain).
    """

    def test_show_re_staged_run_surfaces_latest_staging_dir(self, tmp_path: Path, capsys) -> None:
        from forgelm.cli.subcommands._approvals import _run_approvals_cmd

        output_dir = tmp_path / "run"
        output_dir.mkdir()
        old_staging = output_dir / "final_model.staging.old"
        old_staging.mkdir()
        (old_staging / "old_marker.txt").write_text("v1")
        new_staging = output_dir / "final_model.staging.new"
        new_staging.mkdir()
        (new_staging / "new_marker.txt").write_text("v2")
        audit = output_dir / "audit_log.jsonl"
        _write_event(
            audit,
            "human_approval.required",
            "fg-restage000000",
            staging_path=str(old_staging),
        )
        _write_event(audit, "human_approval.rejected", "fg-restage000000", approver="alice")
        _write_event(
            audit,
            "human_approval.required",
            "fg-restage000000",
            staging_path=str(new_staging),
        )

        with pytest.raises(SystemExit) as ei:
            _run_approvals_cmd(
                _build_args(output_dir, show="fg-restage000000"),
                output_format="json",
            )
        assert ei.value.code == 0
        payload = json.loads(capsys.readouterr().out)
        # Latest staging dir wins (the re-stage), not the original.
        assert payload["staging_contents"] == ["new_marker.txt"], (
            f"--show must surface the LATEST staging dir, got {payload['staging_contents']}"
        )
        # And classify_chain agrees (latest required > latest decision = pending).
        assert payload["status"] == "pending"


class TestAuditLogReaderStrictMode:
    """User inline + Phase D2: strict mode raises AuditLogParseError on malformed lines."""

    def test_strict_mode_raises_on_malformed_json(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._audit_log_reader import (
            AuditLogParseError,
            iter_audit_events,
        )

        audit_path = tmp_path / "audit_log.jsonl"
        audit_path.write_text("not json at all\n", encoding="utf-8")

        with pytest.raises(AuditLogParseError) as exc_info:
            list(iter_audit_events(str(audit_path), strict=True))
        assert exc_info.value.line_number == 1
        assert str(audit_path) in str(exc_info.value)

    def test_strict_mode_raises_on_non_dict_root(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._audit_log_reader import (
            AuditLogParseError,
            iter_audit_events,
        )

        audit_path = tmp_path / "audit_log.jsonl"
        audit_path.write_text('["list", "not", "dict"]\n', encoding="utf-8")

        with pytest.raises(AuditLogParseError) as exc_info:
            list(iter_audit_events(str(audit_path), strict=True))
        assert exc_info.value.line_number == 1
        assert "not dict" in str(exc_info.value) or "list" in str(exc_info.value)

    def test_lenient_mode_skips_silently(self, tmp_path: Path, caplog) -> None:
        import logging

        from forgelm.cli.subcommands._audit_log_reader import iter_audit_events

        audit_path = tmp_path / "audit_log.jsonl"
        audit_path.write_text(
            'not json\n{"event": "ok", "run_id": "fg-x"}\n["list"]\n',
            encoding="utf-8",
        )
        with caplog.at_level(logging.WARNING):
            events = list(iter_audit_events(str(audit_path), strict=False))
        assert len(events) == 1
        # Summary warning fires for the 2 skipped lines.
        assert any("Skipped 2 malformed line" in r.message for r in caplog.records)


class TestApproveStrictModeOnCorruptLog:
    """User inline + Phase D2: approve / reject use strict mode and surface
    a clear error rather than silently skipping a corrupted decision record."""

    def test_approve_aborts_on_corrupted_audit_log(self, tmp_path: Path, capsys) -> None:
        """A malformed line in the audit log must produce
        EXIT_CONFIG_ERROR with an actionable message — NOT silently skip
        the line and let the operator double-grant."""

        from forgelm.cli.subcommands._approve import _run_approve_cmd

        output_dir = tmp_path / "run"
        output_dir.mkdir()
        audit = output_dir / "audit_log.jsonl"
        # Real required event followed by a corrupt line — strict mode
        # should fail-fast on the corruption rather than skip past it.
        _write_event(audit, "human_approval.required", "fg-x", staging_path=str(output_dir / "final_model.staging"))
        with open(audit, "a", encoding="utf-8") as fh:
            fh.write("CORRUPTED LINE NOT JSON\n")

        args = MagicMock()
        args.run_id = "fg-x"
        args.output_dir = str(output_dir)
        args.comment = None
        with pytest.raises(SystemExit) as ei:
            _run_approve_cmd(args, output_format="json")
        # EXIT_CONFIG_ERROR (1) — operator must repair log, not retry.
        assert ei.value.code == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["success"] is False
        assert "corrupted" in payload["error"].lower()
        assert "line" in payload["error"].lower()


class TestApprovalsTimestampTypeSafety:
    """F-37-TS-TYPE: tampered / hand-rolled audit log carrying a non-string
    timestamp (e.g. epoch int) must not crash --pending sort.

    The previous sort key was ``e.get("timestamp") or ""`` which only
    replaces *falsy* values; an int timestamp would crash sorted() with
    TypeError when compared against a real ISO-8601 string from another
    event.
    """

    def test_pending_with_int_timestamp_does_not_crash(self, tmp_path: Path, capsys) -> None:
        from forgelm.cli.subcommands._approvals import _run_approvals_cmd

        output_dir = tmp_path / "run"
        output_dir.mkdir()
        audit = output_dir / "audit_log.jsonl"
        # Run A: legitimate ISO-8601 string timestamp.
        _write_event(
            audit,
            "human_approval.required",
            "fg-string0000000",
            staging_path=str(output_dir / "final_model.staging"),
        )
        # Run B: hand-rolled / tampered audit event with int timestamp.
        _write_event(
            audit,
            "human_approval.required",
            "fg-numeric000000",
            staging_path=str(output_dir / "final_model.staging.b"),
            timestamp=1730500000,  # epoch int — not a string
        )
        # Run C: non-string non-int (list) — also must not crash.
        _write_event(
            audit,
            "human_approval.required",
            "fg-listts0000000",
            staging_path=str(output_dir / "final_model.staging.c"),
            timestamp=["bogus", "shape"],
        )
        with pytest.raises(SystemExit) as ei:
            _run_approvals_cmd(_build_args(output_dir, pending=True), output_format="json")
        assert ei.value.code == 0
        payload = json.loads(capsys.readouterr().out)
        run_ids = {s["run_id"] for s in payload["pending"]}
        # All three runs must surface — the type-safe sort key must not
        # have hidden any of them.
        assert run_ids == {"fg-string0000000", "fg-numeric000000", "fg-listts0000000"}, (
            f"non-string timestamp must not hide pending runs; got {run_ids!r}"
        )

    def test_safe_timestamp_key_returns_empty_for_non_strings(self) -> None:
        from forgelm.cli.subcommands._approvals import _safe_timestamp_key

        assert _safe_timestamp_key({"timestamp": "2026-01-01T00:00:00+00:00"}) == "2026-01-01T00:00:00+00:00"
        assert _safe_timestamp_key({"timestamp": 1730500000}) == ""
        assert _safe_timestamp_key({"timestamp": None}) == ""
        assert _safe_timestamp_key({"timestamp": ["bogus"]}) == ""
        assert _safe_timestamp_key({}) == ""


class TestApprovalsAuditLogUnreadable:
    """Wave 2a Round-2 review (CodeRabbit): a permission-denied audit log
    must surface a clear EXIT_CONFIG_ERROR rather than masquerade as
    "no pending approvals" / "no events for this run".

    ``iter_audit_events`` swallows OSError-on-open + logs at ERROR + yields
    nothing.  Without the explicit os.access readability check in
    ``_run_approvals_list_pending`` and ``_run_approvals_show``, an
    operator with a chmod-broken audit log would see a healthy-looking
    empty result and miss a real pending decision.
    """

    def test_pending_exits_config_error_when_audit_log_unreadable(self, tmp_path: Path, capsys, monkeypatch) -> None:
        import os

        from forgelm.cli.subcommands._approvals import _run_approvals_cmd

        output_dir = tmp_path / "run"
        _seed_run(output_dir, "fg-real00000000")
        audit_path = output_dir / "audit_log.jsonl"
        # Simulate an unreadable file via os.access monkeypatch (chmod 000
        # behaves differently in CI containers; the access predicate is
        # what the code under test actually consults).
        original_access = os.access
        monkeypatch.setattr(
            os,
            "access",
            lambda path, mode: False if str(path) == str(audit_path) else original_access(path, mode),
        )

        with pytest.raises(SystemExit) as ei:
            _run_approvals_cmd(_build_args(output_dir, pending=True), output_format="json")
        assert ei.value.code == 1, "unreadable audit log must EXIT_CONFIG_ERROR (1), not silently exit 0"
        payload = json.loads(capsys.readouterr().out)
        assert payload["success"] is False
        assert "not readable" in payload["error"].lower()
        assert "audit_log.jsonl" in payload["error"]

    def test_show_exits_config_error_when_audit_log_unreadable(self, tmp_path: Path, capsys, monkeypatch) -> None:
        import os

        from forgelm.cli.subcommands._approvals import _run_approvals_cmd

        output_dir = tmp_path / "run"
        _seed_run(output_dir, "fg-real00000000")
        audit_path = output_dir / "audit_log.jsonl"
        original_access = os.access
        monkeypatch.setattr(
            os,
            "access",
            lambda path, mode: False if str(path) == str(audit_path) else original_access(path, mode),
        )

        with pytest.raises(SystemExit) as ei:
            _run_approvals_cmd(_build_args(output_dir, show="fg-real00000000"), output_format="json")
        assert ei.value.code == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["success"] is False
        assert "not readable" in payload["error"].lower()
