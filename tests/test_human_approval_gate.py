"""Faz 9: Article 14 human-approval gate (staging directory + approve/reject).

Covers the trio of behaviours the gate guarantees:

1. ``ForgeTrainer._handle_human_approval_gate`` saves the model to
   ``final_model.staging/`` rather than ``final_model/``, emits the
   ``human_approval.required`` audit event with ``staging_path`` + ``run_id``,
   and calls ``notify_awaiting_approval`` on the webhook notifier.
2. ``forgelm approve <run_id>`` atomically renames the staging dir,
   emits ``human_approval.granted``, and calls ``notify_success``.
3. ``forgelm reject <run_id>`` leaves the staging dir in place,
   emits ``human_approval.rejected``, and calls ``notify_failure``.

Stale-staging detection (mismatched run_id, missing required event,
missing staging dir) and concurrent approve attempts are also asserted.

The trainer-level tests skip if ``torch`` is unavailable. The CLI-level
tests do not need torch; they exercise the audit/staging paths directly.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

torch_available = True
try:
    import torch  # noqa: F401
except ImportError:
    torch_available = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_required_event(audit_path: Path, run_id: str, staging_path: str) -> None:
    """Append a synthetic ``human_approval.required`` line to *audit_path*.

    Mirrors the trainer's payload — keeps the CLI-level tests independent of
    the trainer fixtures while still exercising the same JSONL parser.
    """
    entry = {
        "timestamp": "2026-04-30T12:00:00+00:00",
        "run_id": run_id,
        "operator": "tester",
        "event": "human_approval.required",
        "prev_hash": "genesis",
        "gate": "final_model",
        "reason": "require_human_approval=true",
        "metrics": {"eval_loss": 0.42},
        "staging_path": staging_path,
    }
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with open(audit_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def _read_audit_events(audit_path: Path) -> list[dict]:
    if not audit_path.exists():
        return []
    events = []
    with open(audit_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            events.append(json.loads(line))
    return events


# ---------------------------------------------------------------------------
# Trainer-level: gate fires → staging dir, NOT final_model
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not torch_available, reason="torch not installed")
class TestHumanApprovalGateTrainer:
    def _make_trainer(self, tmp_path: Path, *, require_approval: bool = True):
        """Build a ForgeTrainer whose heavy collaborators are mocked."""
        from forgelm.compliance import AuditLogger
        from forgelm.config import ForgeConfig
        from forgelm.trainer import ForgeTrainer

        output_dir = tmp_path / "out"
        output_dir.mkdir()

        config = ForgeConfig(
            **{
                "model": {"name_or_path": "org/model"},
                "lora": {},
                "training": {"output_dir": str(output_dir)},
                "data": {"dataset_name_or_path": "org/dataset"},
                "evaluation": {"require_human_approval": require_approval},
            }
        )

        with patch("forgelm.trainer.WebhookNotifier"):
            trainer = ForgeTrainer.__new__(ForgeTrainer)
            trainer.config = config
            trainer.dataset = {"train": ["dummy"]}
            trainer.checkpoint_dir = str(output_dir)
            trainer.run_name = "test_finetune"
            trainer.notifier = MagicMock()
            trainer.audit = AuditLogger(str(output_dir))
            # Mock save_final_model so it just creates the directory + a
            # marker file, no torch/peft involvement.
            trainer.save_final_model = MagicMock(side_effect=self._fake_save)

        return trainer, output_dir

    @staticmethod
    def _fake_save(path: str) -> None:
        os.makedirs(path, exist_ok=True)
        Path(path, "adapter_config.json").write_text('{"r": 8}', encoding="utf-8")

    def test_gate_writes_to_staging_not_final(self, tmp_path: Path) -> None:
        trainer, output_dir = self._make_trainer(tmp_path)
        from forgelm.results import TrainResult

        result = TrainResult(success=True, metrics={"eval_loss": 0.42})
        final_path = str(output_dir / "final_model")
        staging_path = final_path + ".staging"

        # Caller path: staging save happens upstream in the pipeline; the
        # gate handler fires with already_saved=True.
        trainer.save_final_model(staging_path)
        gate_fired = trainer._handle_human_approval_gate(staging_path, result, already_saved=True)

        assert gate_fired is True
        assert (output_dir / "final_model.staging").is_dir()
        assert not (output_dir / "final_model").exists(), "final_model must NOT exist when gate is active"
        assert (output_dir / "final_model.staging" / "adapter_config.json").is_file()
        assert result.staging_path == staging_path
        assert result.success is True

    def test_gate_emits_human_approval_required_event(self, tmp_path: Path) -> None:
        trainer, output_dir = self._make_trainer(tmp_path)
        from forgelm.results import TrainResult

        result = TrainResult(success=True, metrics={"eval_loss": 0.42})
        staging_path = str(output_dir / "final_model.staging")
        trainer.save_final_model(staging_path)
        trainer._handle_human_approval_gate(staging_path, result, already_saved=True)

        events = _read_audit_events(output_dir / "audit_log.jsonl")
        required = [e for e in events if e["event"] == "human_approval.required"]
        assert len(required) == 1, f"expected exactly one human_approval.required event, got {events!r}"
        evt = required[0]
        assert evt["staging_path"] == staging_path
        assert evt["run_id"] == trainer.audit.run_id
        assert evt["gate"] == "final_model"
        assert evt["reason"] == "require_human_approval=true"
        assert evt["metrics"] == {"eval_loss": 0.42}

    def test_gate_calls_notify_awaiting_approval(self, tmp_path: Path) -> None:
        trainer, output_dir = self._make_trainer(tmp_path)
        from forgelm.results import TrainResult

        result = TrainResult(success=True)
        staging_path = str(output_dir / "final_model.staging")
        trainer.save_final_model(staging_path)
        trainer._handle_human_approval_gate(staging_path, result, already_saved=True)

        trainer.notifier.notify_awaiting_approval.assert_called_once_with(
            run_name="test_finetune", model_path=staging_path
        )

    def test_gate_disabled_returns_false(self, tmp_path: Path) -> None:
        trainer, output_dir = self._make_trainer(tmp_path, require_approval=False)
        from forgelm.results import TrainResult

        result = TrainResult(success=True)
        gate_fired = trainer._handle_human_approval_gate(str(output_dir / "final_model"), result)
        assert gate_fired is False
        trainer.notifier.notify_awaiting_approval.assert_not_called()

    def test_gate_sets_awaiting_approval_discriminator(self, tmp_path: Path) -> None:
        """XP-02: the gate sets the authoritative ``awaiting_approval`` flag so
        the CLI / pipeline route to exit 4 on the result state — not the bare
        config flag and not ``bool(staging_path)`` (which can survive a revert)."""
        trainer, output_dir = self._make_trainer(tmp_path)
        from forgelm.results import TrainResult

        result = TrainResult(success=True)
        assert result.awaiting_approval is False  # default
        staging_path = str(output_dir / "final_model.staging")
        trainer.save_final_model(staging_path)
        trainer._handle_human_approval_gate(staging_path, result, already_saved=True)

        assert result.awaiting_approval is True
        assert result.staging_path == staging_path
        assert result.success is True

    def test_gate_revert_clears_staging_path(self, tmp_path: Path) -> None:
        """XP-02 root cause: a post-train gate that auto-reverts must clear
        ``staging_path`` (the revert deletes that dir) and leave
        ``awaiting_approval`` False, so the run is reported as reverted
        (exit 3), never awaiting approval (exit 4)."""
        from types import SimpleNamespace

        from forgelm.results import TrainResult

        trainer, output_dir = self._make_trainer(tmp_path)
        trainer.config.evaluation.auto_revert = True
        trainer._revert_model = MagicMock()  # don't actually rmtree

        # The pipeline eagerly sets staging_path + final_model_path before the
        # post-train gates run (so they can evaluate on-disk artefacts); a
        # reverting gate must undo all of them. Seed awaiting_approval=True so the
        # test verifies the flag is actively cleared, not merely still False.
        staging_path = str(output_dir / "final_model.staging.fg-x")
        result = TrainResult(
            success=True, staging_path=staging_path, final_model_path=staging_path, awaiting_approval=True
        )

        failing_safety = SimpleNamespace(
            passed=False,
            safety_score=0.2,
            safe_ratio=0.2,
            category_distribution={"violence": 3},
            severity_distribution={"high": 3},
            low_confidence_count=0,
            failure_reason="safety gate failed",
        )
        cont = trainer._apply_safety_result(failing_safety, result, {}, staging_path)

        assert cont is False
        assert result.success is False
        assert result.reverted is True
        assert result.staging_path is None
        assert result.final_model_path is None, "revert deletes the model — final_model_path must be cleared"
        assert result.awaiting_approval is False, "a reverted run is never awaiting approval"
        trainer._revert_model.assert_called_once()


# ---------------------------------------------------------------------------
# CLI-level: forgelm approve happy path + failure modes
# ---------------------------------------------------------------------------


class TestForgelmApprove:
    def _seed_run(self, tmp_path: Path, run_id: str = "fg-test123abc456") -> Path:
        """Write a staging dir + audit log entry mimicking a halted run."""
        output_dir = tmp_path / "approval_run"
        output_dir.mkdir()
        staging_dir = output_dir / "final_model.staging"
        staging_dir.mkdir()
        (staging_dir / "adapter_config.json").write_text('{"r": 8}', encoding="utf-8")
        _write_required_event(output_dir / "audit_log.jsonl", run_id, str(staging_dir))
        return output_dir

    def test_approve_atomically_renames_staging(self, tmp_path: Path, monkeypatch) -> None:
        run_id = "fg-test123abc456"
        output_dir = self._seed_run(tmp_path, run_id)

        monkeypatch.setenv("FORGELM_OPERATOR", "alice")

        from forgelm.cli import _run_approve_cmd

        args = MagicMock()
        args.run_id = run_id
        args.output_dir = str(output_dir)
        args.comment = "looks good"

        with patch("forgelm.cli._build_approval_notifier") as build_notifier:
            notifier = MagicMock()
            build_notifier.return_value = notifier
            _run_approve_cmd(args, output_format="text")

        assert (output_dir / "final_model").is_dir()
        assert not (output_dir / "final_model.staging").exists()
        assert (output_dir / "final_model" / "adapter_config.json").is_file()

        events = _read_audit_events(output_dir / "audit_log.jsonl")
        granted = [e for e in events if e["event"] == "human_approval.granted"]
        assert len(granted) == 1
        evt = granted[0]
        assert evt["run_id"] == run_id
        assert evt["approver"] == "alice"
        assert evt["comment"] == "looks good"
        assert evt["promote_strategy"] in ("rename", "move")

        notifier.notify_success.assert_called_once()
        kwargs = notifier.notify_success.call_args.kwargs
        assert kwargs["run_name"] == "approval_run"
        assert kwargs["metrics"] == {}

    def test_approve_with_stale_run_id_errors_without_renaming(self, tmp_path: Path) -> None:
        run_id = "fg-real000aaa111"
        output_dir = self._seed_run(tmp_path, run_id)

        from forgelm.cli import _run_approve_cmd

        args = MagicMock()
        args.run_id = "fg-stale999zzz888"  # mismatched
        args.output_dir = str(output_dir)
        args.comment = None

        with pytest.raises(SystemExit) as ei:
            _run_approve_cmd(args, output_format="text")
        # CLI exits 1 (config error) on stale staging — see EXIT_CONFIG_ERROR.
        assert ei.value.code == 1
        assert (output_dir / "final_model.staging").is_dir(), "staging dir must NOT be touched on stale run_id"
        assert not (output_dir / "final_model").exists()

    def test_approve_without_required_event_errors(self, tmp_path: Path) -> None:
        # Set up staging dir but no audit log → no human_approval.required.
        output_dir = tmp_path / "missing_event_run"
        output_dir.mkdir()
        (output_dir / "final_model.staging").mkdir()
        # touch an empty audit log so the path exists but has no events
        (output_dir / "audit_log.jsonl").write_text("", encoding="utf-8")

        from forgelm.cli import _run_approve_cmd

        args = MagicMock()
        args.run_id = "fg-anything000000"
        args.output_dir = str(output_dir)
        args.comment = None

        with pytest.raises(SystemExit) as ei:
            _run_approve_cmd(args, output_format="text")
        assert ei.value.code == 1

    def test_approve_without_staging_dir_errors(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "no_staging_run"
        output_dir.mkdir()

        from forgelm.cli import _run_approve_cmd

        args = MagicMock()
        args.run_id = "fg-doesnt00matter"
        args.output_dir = str(output_dir)
        args.comment = None

        with pytest.raises(SystemExit) as ei:
            _run_approve_cmd(args, output_format="text")
        assert ei.value.code == 1

    def test_approve_concurrent_second_call_fails(self, tmp_path: Path, monkeypatch) -> None:
        """Second approve on the same staging dir hits the missing-staging guard."""
        run_id = "fg-concurrentrace"
        output_dir = self._seed_run(tmp_path, run_id)
        monkeypatch.setenv("FORGELM_OPERATOR", "alice")

        from forgelm.cli import _run_approve_cmd

        args = MagicMock()
        args.run_id = run_id
        args.output_dir = str(output_dir)
        args.comment = None

        with patch("forgelm.cli._build_approval_notifier", return_value=MagicMock()):
            _run_approve_cmd(args, output_format="text")

        # Staging is gone; final exists. Re-running approve must fail.
        with pytest.raises(SystemExit) as ei:
            _run_approve_cmd(args, output_format="text")
        assert ei.value.code == 1

    def test_approve_resolves_metrics_from_manifest(self, tmp_path: Path, monkeypatch) -> None:
        import yaml

        run_id = "fg-metrics00000aa"
        output_dir = self._seed_run(tmp_path, run_id)
        compliance_dir = output_dir / "compliance"
        compliance_dir.mkdir()
        (compliance_dir / "training_manifest.yaml").write_text(
            yaml.safe_dump({"final_metrics": {"eval_loss": 0.42, "accuracy": 0.95}}),
            encoding="utf-8",
        )
        monkeypatch.setenv("FORGELM_OPERATOR", "alice")

        from forgelm.cli import _run_approve_cmd

        args = MagicMock()
        args.run_id = run_id
        args.output_dir = str(output_dir)
        args.comment = None

        with patch("forgelm.cli._build_approval_notifier") as build_notifier:
            notifier = MagicMock()
            build_notifier.return_value = notifier
            _run_approve_cmd(args, output_format="text")

        kwargs = notifier.notify_success.call_args.kwargs
        assert kwargs["metrics"] == {"eval_loss": 0.42, "accuracy": 0.95}


# ---------------------------------------------------------------------------
# CLI-level: forgelm reject
# ---------------------------------------------------------------------------


class TestForgelmReject:
    def _seed_run(self, tmp_path: Path, run_id: str = "fg-reject0000abc") -> Path:
        output_dir = tmp_path / "reject_run"
        output_dir.mkdir()
        staging_dir = output_dir / "final_model.staging"
        staging_dir.mkdir()
        (staging_dir / "adapter_config.json").write_text('{"r": 8}', encoding="utf-8")
        _write_required_event(output_dir / "audit_log.jsonl", run_id, str(staging_dir))
        return output_dir

    def test_reject_preserves_staging_directory(self, tmp_path: Path, monkeypatch) -> None:
        run_id = "fg-reject0000abc"
        output_dir = self._seed_run(tmp_path, run_id)
        monkeypatch.setenv("FORGELM_OPERATOR", "bob")

        from forgelm.cli import _run_reject_cmd

        args = MagicMock()
        args.run_id = run_id
        args.output_dir = str(output_dir)
        args.comment = "regression on safety-eval"

        with patch("forgelm.cli._build_approval_notifier") as build_notifier:
            notifier = MagicMock()
            build_notifier.return_value = notifier
            _run_reject_cmd(args, output_format="text")

        assert (output_dir / "final_model.staging").is_dir(), "staging dir must be preserved on reject"
        assert (output_dir / "final_model.staging" / "adapter_config.json").is_file()
        assert not (output_dir / "final_model").exists()

        events = _read_audit_events(output_dir / "audit_log.jsonl")
        rejected = [e for e in events if e["event"] == "human_approval.rejected"]
        assert len(rejected) == 1
        evt = rejected[0]
        assert evt["run_id"] == run_id
        assert evt["approver"] == "bob"
        assert evt["comment"] == "regression on safety-eval"
        assert evt["staging_path"].endswith("final_model.staging")

        notifier.notify_failure.assert_called_once()
        kwargs = notifier.notify_failure.call_args.kwargs
        assert kwargs["run_name"] == "reject_run"
        assert "human_approval.rejected" in kwargs["reason"]

    def test_reject_without_staging_errors(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "no_staging_reject"
        output_dir.mkdir()

        from forgelm.cli import _run_reject_cmd

        args = MagicMock()
        args.run_id = "fg-anything"
        args.output_dir = str(output_dir)
        args.comment = None

        with pytest.raises(SystemExit) as ei:
            _run_reject_cmd(args, output_format="text")
        assert ei.value.code == 1


# ---------------------------------------------------------------------------
# CLI-level: terminal-decision idempotency guard (approve/reject after a prior
# decision must refuse via _find_human_approval_decision_event regardless of
# whether the staging directory still exists).
# ---------------------------------------------------------------------------


class TestDoubleDecisionGuard:
    """Cover ``_find_human_approval_decision_event`` regression scenarios.

    The earlier ``test_approve_concurrent_second_call_fails`` only exercises
    the missing-staging guard.  The decision-event guard is the only thing
    standing between an operator and *re-deciding* a run whose staging dir
    survived a prior reject (the dir is preserved on reject by design).
    """

    def _seed_run(self, tmp_path: Path, run_id: str) -> Path:
        output_dir = tmp_path / "decision_guard_run"
        output_dir.mkdir()
        staging_dir = output_dir / "final_model.staging"
        staging_dir.mkdir()
        (staging_dir / "adapter_config.json").write_text('{"r": 8}', encoding="utf-8")
        _write_required_event(output_dir / "audit_log.jsonl", run_id, str(staging_dir))
        return output_dir

    def test_approve_after_reject_blocked_by_decision_guard(self, tmp_path: Path, monkeypatch) -> None:
        """Reject preserves staging; a follow-up approve must hit the decision guard, not silently succeed."""
        run_id = "fg-rejected00abc"
        output_dir = self._seed_run(tmp_path, run_id)
        monkeypatch.setenv("FORGELM_OPERATOR", "alice")

        from forgelm.cli import _run_approve_cmd, _run_reject_cmd

        args = MagicMock()
        args.run_id = run_id
        args.output_dir = str(output_dir)
        args.comment = None

        with patch("forgelm.cli._build_approval_notifier", return_value=MagicMock()):
            _run_reject_cmd(args, output_format="text")

        # Sanity: staging dir is preserved (reject's documented behaviour).
        assert (output_dir / "final_model.staging").is_dir()

        # Approve attempt now must fail via the decision-event guard, not the
        # missing-staging guard (the staging dir is still there).
        with patch("forgelm.cli._build_approval_notifier", return_value=MagicMock()):
            with pytest.raises(SystemExit) as ei:
                _run_approve_cmd(args, output_format="text")
        assert ei.value.code == 1

        events = _read_audit_events(output_dir / "audit_log.jsonl")
        granted = [e for e in events if e["event"] == "human_approval.granted"]
        assert granted == [], "approve must not write a granted event after a prior rejection"

    def test_reject_after_approve_blocked_by_decision_guard(self, tmp_path: Path, monkeypatch) -> None:
        """Approve removes staging; a follow-up reject must hit the decision guard before missing-staging."""
        run_id = "fg-approved0abc"
        output_dir = self._seed_run(tmp_path, run_id)
        monkeypatch.setenv("FORGELM_OPERATOR", "alice")

        from forgelm.cli import _run_approve_cmd, _run_reject_cmd

        args = MagicMock()
        args.run_id = run_id
        args.output_dir = str(output_dir)
        args.comment = None

        with patch("forgelm.cli._build_approval_notifier", return_value=MagicMock()):
            _run_approve_cmd(args, output_format="text")

        # Sanity: approve promoted staging → final.
        assert (output_dir / "final_model").is_dir()
        assert not (output_dir / "final_model.staging").exists()

        # Re-instate the staging dir so the decision-event guard is the one
        # that fires (otherwise the missing-staging guard would shadow it).
        (output_dir / "final_model.staging").mkdir()

        with patch("forgelm.cli._build_approval_notifier", return_value=MagicMock()):
            with pytest.raises(SystemExit) as ei:
                _run_reject_cmd(args, output_format="text")
        assert ei.value.code == 1

        events = _read_audit_events(output_dir / "audit_log.jsonl")
        rejected = [e for e in events if e["event"] == "human_approval.rejected"]
        assert rejected == [], "reject must not write a rejected event after a prior approval"

    def test_double_reject_blocked_by_decision_guard(self, tmp_path: Path, monkeypatch) -> None:
        """Two rejects on the same run: only the first must persist a rejection event."""
        run_id = "fg-doublereject"
        output_dir = self._seed_run(tmp_path, run_id)
        monkeypatch.setenv("FORGELM_OPERATOR", "alice")

        from forgelm.cli import _run_reject_cmd

        args = MagicMock()
        args.run_id = run_id
        args.output_dir = str(output_dir)
        args.comment = None

        with patch("forgelm.cli._build_approval_notifier", return_value=MagicMock()):
            _run_reject_cmd(args, output_format="text")

        # Staging is preserved by reject, so the decision-event guard is the
        # only thing blocking a second rejection.
        with patch("forgelm.cli._build_approval_notifier", return_value=MagicMock()):
            with pytest.raises(SystemExit) as ei:
                _run_reject_cmd(args, output_format="text")
        assert ei.value.code == 1

        events = _read_audit_events(output_dir / "audit_log.jsonl")
        rejected = [e for e in events if e["event"] == "human_approval.rejected"]
        assert len(rejected) == 1, "second reject must not append another rejection event"


# ---------------------------------------------------------------------------
# CLI-level: subcommand registration smoke + EXIT_AWAITING_APPROVAL contract
# ---------------------------------------------------------------------------


class TestApproveRejectRegistration:
    def test_approve_subcommand_registered(self) -> None:
        """`forgelm approve --help` must succeed (i.e. the subparser exists)."""
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, "-m", "forgelm.cli", "approve", "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert "run_id" in result.stdout
        assert "--output-dir" in result.stdout

    def test_reject_subcommand_registered(self) -> None:
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, "-m", "forgelm.cli", "reject", "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert "run_id" in result.stdout
        assert "--output-dir" in result.stdout


class TestExitAwaitingApprovalContract:
    """The CLI must exit with code 4 (EXIT_AWAITING_APPROVAL) when the gate fires."""

    def test_exit_code_constant_unchanged(self) -> None:
        from forgelm.cli import EXIT_AWAITING_APPROVAL

        # Public CLI contract — see docs/standards/error-handling.md.
        assert EXIT_AWAITING_APPROVAL == 4


class TestApproverIdentityPolicy:
    """P2-1 regression: ``_resolve_approver_identity`` must mirror
    :class:`forgelm.compliance.AuditLogger`'s policy — never silently
    return a literal ``"anonymous"`` string without the explicit
    ``FORGELM_ALLOW_ANONYMOUS_OPERATOR=1`` opt-in.  Article 12 record-
    keeping requires an attributable identity by default."""

    def test_forgelm_operator_takes_priority(self, monkeypatch) -> None:
        from forgelm.cli.subcommands._approve import _resolve_approver_identity

        monkeypatch.setenv("FORGELM_OPERATOR", "ci-bot@github-actions")
        assert _resolve_approver_identity() == "ci-bot@github-actions"

    def test_falls_back_to_getpass_username(self, monkeypatch) -> None:
        import getpass

        from forgelm.cli.subcommands import _approve

        monkeypatch.delenv("FORGELM_OPERATOR", raising=False)
        monkeypatch.setattr(getpass, "getuser", lambda: "alice")
        assert _approve._resolve_approver_identity() == "alice"

    def test_exits_when_no_identity_and_no_opt_in(self, monkeypatch) -> None:
        """No env var + getpass failure + no opt-in flag must abort with
        EXIT_CONFIG_ERROR rather than silently writing "anonymous"."""
        import getpass

        from forgelm.cli._exit_codes import EXIT_CONFIG_ERROR
        from forgelm.cli.subcommands import _approve

        monkeypatch.delenv("FORGELM_OPERATOR", raising=False)
        monkeypatch.delenv("FORGELM_ALLOW_ANONYMOUS_OPERATOR", raising=False)

        def _boom():
            raise OSError("no LOGNAME / USER / pwd entry")

        monkeypatch.setattr(getpass, "getuser", _boom)
        with pytest.raises(SystemExit) as exc_info:
            _approve._resolve_approver_identity()
        assert exc_info.value.code == EXIT_CONFIG_ERROR

    def test_anonymous_only_with_explicit_opt_in(self, monkeypatch) -> None:
        """Explicit ``FORGELM_ALLOW_ANONYMOUS_OPERATOR=1`` opts in to
        ``anonymous@<hostname>`` — never the bare ``"anonymous"`` literal."""
        import getpass
        import socket

        from forgelm.cli.subcommands import _approve

        monkeypatch.delenv("FORGELM_OPERATOR", raising=False)
        monkeypatch.setenv("FORGELM_ALLOW_ANONYMOUS_OPERATOR", "1")

        def _boom():
            raise OSError("no LOGNAME / USER / pwd entry")

        monkeypatch.setattr(getpass, "getuser", _boom)
        monkeypatch.setattr(socket, "gethostname", lambda: "sandbox-host")

        result = _approve._resolve_approver_identity()
        assert result == "anonymous@sandbox-host", f"With opt-in flag we expect 'anonymous@<hostname>', got {result!r}"
        assert result != "anonymous", "The bare 'anonymous' literal is the pre-fix behaviour and must not return"
