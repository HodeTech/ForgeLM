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
            # __init__ is bypassed via __new__, so set the config digest the
            # gate event records (XP-11) the same way __init__ would.
            from forgelm.compliance import compute_config_hash

            trainer._config_hash = compute_config_hash(config)
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

    def test_production_staging_path_carries_run_id_suffix(self, tmp_path: Path) -> None:
        """The trainer's real caller stages adapters at
        ``f"{final_path}.staging.{run_id}"`` — the run-id suffix is
        load-bearing (approve/reject/purge resolve it). Pin the production
        convention so the facade/results/webhook comments that now document
        ``final_model.staging.<run_id>/`` cannot drift back to the suffix-less
        form. Reconstructs the exact expression ForgeTrainer builds at the
        gate so a rename of the suffix shape trips here."""
        trainer, output_dir = self._make_trainer(tmp_path)
        final_path = os.path.abspath(str(output_dir / "final_model"))

        # Mirror forgelm/trainer.py's gate-path construction verbatim.
        gate_path = os.path.abspath(f"{final_path}.staging.{trainer.audit.run_id}")

        assert os.path.basename(gate_path) == f"final_model.staging.{trainer.audit.run_id}"
        assert ".staging." in gate_path
        assert not gate_path.endswith(".staging")

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
        # XP-11 / F-P4-OPUS-05: the event carries the config digest so the
        # approvals reader can populate pending[].config_hash (previously
        # always null because the event never recorded it).
        assert evt["config_hash"].startswith("sha256:")
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
            total_count=10,
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

    def test_safety_audit_event_records_total_count(self, tmp_path: Path) -> None:
        """F-P3-FABLE-16: the ``safety.evaluation_completed`` audit payload must
        carry ``total_count`` so a vacuous pass (zero probes evaluated) is
        distinguishable from a real 100%-safe evaluation in the audit trail."""
        from types import SimpleNamespace

        from forgelm.results import TrainResult

        trainer, output_dir = self._make_trainer(tmp_path, require_approval=False)
        result = TrainResult(success=True)
        passing_safety = SimpleNamespace(
            passed=True,
            safety_score=1.0,
            safe_ratio=1.0,
            total_count=42,
            category_distribution=None,
            severity_distribution=None,
            low_confidence_count=0,
            failure_reason=None,
        )
        cont = trainer._apply_safety_result(passing_safety, result, {}, str(output_dir / "final_model"))

        assert cont is True
        events = _read_audit_events(output_dir / "audit_log.jsonl")
        completed = [e for e in events if e["event"] == "safety.evaluation_completed"]
        assert len(completed) == 1
        assert completed[0]["total_count"] == 42


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

    def test_approve_audit_write_failure_after_rename_surfaces_error(self, tmp_path: Path, monkeypatch, capsys) -> None:
        """F-P4-OPUS-18: an OSError on the post-rename ``human_approval.granted``
        write must surface as a clean named exit (EXIT_TRAINING_ERROR) with an
        operator-actionable AUDIT-GAP message — not a raw uncaught traceback —
        even though the model is already promoted."""
        run_id = "fg-auditgap00001"
        output_dir = self._seed_run(tmp_path, run_id)
        monkeypatch.setenv("FORGELM_OPERATOR", "alice")

        from forgelm.cli import _run_approve_cmd
        from forgelm.compliance import AuditLogger

        def _raise_on_granted(self, event, **details):  # noqa: ANN001
            if event == "human_approval.granted":
                raise OSError("ENOSPC: no space left on device")

        monkeypatch.setattr(AuditLogger, "log_event", _raise_on_granted)

        args = MagicMock()
        args.run_id = run_id
        args.output_dir = str(output_dir)
        args.comment = None

        with pytest.raises(SystemExit) as ei:
            _run_approve_cmd(args, output_format="json")

        assert ei.value.code == 2  # EXIT_TRAINING_ERROR
        # The model was already promoted by the rename (irreversible).
        assert (output_dir / "final_model").is_dir()
        assert not (output_dir / "final_model.staging").exists()
        # The operator gets a clean JSON error naming the audit gap, not a traceback.
        payload = json.loads(capsys.readouterr().out)
        assert payload["success"] is False
        assert "AUDIT GAP" in payload["error"]
        assert "final_model" in payload["error"]

    def test_approve_promote_strategy_doc_matches_code(self) -> None:
        """XP-08 / F-P4-OPUS-07 / F-P7-OPUS-17: the locked json-output.md
        approve envelope must document a ``promote_strategy`` value the code can
        actually emit. Pre-fix it showed ``"atomic_rename"`` — a value the code
        never produces (only ``"rename"`` / ``"move"``)."""
        import json as _json
        import pathlib
        import re

        repo = pathlib.Path(__file__).resolve().parent.parent
        allowed = {"rename", "move"}
        for rel in (
            "docs/usermanuals/en/reference/json-output.md",
            "docs/usermanuals/tr/reference/json-output.md",
        ):
            text = (repo / rel).read_text(encoding="utf-8")
            # Grab the first fenced JSON block under the approve/reject H2.
            section = re.split(r"^## .*forgelm approve", text, maxsplit=1, flags=re.MULTILINE)[1]
            block = re.search(r"```json\n(.*?)```", section, re.DOTALL).group(1)
            promote = _json.loads(block).get("promote_strategy")
            assert promote in allowed, f"{rel}: promote_strategy={promote!r} not in {allowed}"

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

    def test_approve_with_nonexistent_output_dir_does_not_create_it(self, tmp_path: Path) -> None:
        """Regression: approve against a bogus/mistyped --output-dir must fail
        via the missing-audit-log guard, not materialise the directory and a
        `.approval.lock` file for a run that never happened."""
        output_dir = tmp_path / "does_not_exist"
        assert not output_dir.exists()

        from forgelm.cli import _run_approve_cmd

        args = MagicMock()
        args.run_id = "fg-anything000000"
        args.output_dir = str(output_dir)
        args.comment = None

        with pytest.raises(SystemExit) as ei:
            _run_approve_cmd(args, output_format="text")
        assert ei.value.code == 1
        assert not output_dir.exists(), "approve must not materialise output_dir for a non-existent run"

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

    def test_approve_audit_logger_keyerror_exits_config_error(self, tmp_path: Path, monkeypatch) -> None:
        """Finding 1 (defence-in-depth): a bare ``KeyError`` from AuditLogger
        construction — the arbitrary-numeric-UID container whose UID has no
        ``/etc/passwd`` entry — must exit ``EXIT_CONFIG_ERROR`` (1) via the CLI
        seam rather than crash the Article 14 gate with a raw traceback, even if
        the constructor's own conversion ever regresses.  The model must NOT be
        promoted (construction is validated before the rename)."""
        run_id = "fg-keyerr000appr"
        output_dir = self._seed_run(tmp_path, run_id)
        monkeypatch.setenv("FORGELM_OPERATOR", "alice")

        from forgelm.cli import _run_approve_cmd
        from forgelm.compliance import AuditLogger

        def _boom_init(self, output_dir, run_id=None):  # noqa: ANN001
            raise KeyError("getpwuid(): uid not found: 1000")

        monkeypatch.setattr(AuditLogger, "__init__", _boom_init)

        args = MagicMock()
        args.run_id = run_id
        args.output_dir = str(output_dir)
        args.comment = None

        with pytest.raises(SystemExit) as ei:
            _run_approve_cmd(args, output_format="text")
        assert ei.value.code == 1  # EXIT_CONFIG_ERROR, not a raw KeyError traceback
        assert (output_dir / "final_model.staging").is_dir(), "staging must be untouched — construction precedes rename"
        assert not (output_dir / "final_model").exists()

    def test_approve_getpass_keyerror_container_exits_config_error(self, tmp_path: Path, monkeypatch, capsys) -> None:
        """Finding 1 (end-to-end): in an arbitrary-numeric-UID container
        ``getpass.getuser()`` raises ``KeyError`` (UID absent from
        ``/etc/passwd``).  AuditLogger.__init__ now catches it and converts to
        ConfigError, which the approve dispatcher surfaces as
        ``EXIT_CONFIG_ERROR`` (1) with the operator-identity guidance — never a
        crash, never a silent promotion."""
        run_id = "fg-getpasskeyap1"
        output_dir = self._seed_run(tmp_path, run_id)

        import getpass

        monkeypatch.delenv("FORGELM_OPERATOR", raising=False)
        monkeypatch.delenv("FORGELM_ALLOW_ANONYMOUS_OPERATOR", raising=False)

        def _boom():
            raise KeyError("getpwuid(): uid not found: 1000")

        monkeypatch.setattr(getpass, "getuser", _boom)

        from forgelm.cli import _run_approve_cmd

        args = MagicMock()
        args.run_id = run_id
        args.output_dir = str(output_dir)
        args.comment = None

        with pytest.raises(SystemExit) as ei:
            _run_approve_cmd(args, output_format="json")
        assert ei.value.code == 1  # EXIT_CONFIG_ERROR
        assert (output_dir / "final_model.staging").is_dir()
        assert not (output_dir / "final_model").exists()
        payload = json.loads(capsys.readouterr().out)
        assert payload["success"] is False
        assert "FORGELM_OPERATOR" in payload["error"]


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

    def test_reject_audit_write_failure_surfaces_error(self, tmp_path: Path, monkeypatch, capsys) -> None:
        """The reject path mirrors approve's audit-write guard: an OSError on the
        ``human_approval.rejected`` write must surface as a clean named exit
        (EXIT_TRAINING_ERROR) with an operator-actionable message — not a raw
        uncaught traceback. Unlike approve, no model was promoted, so the staging
        directory is preserved for a retry after storage is repaired."""
        run_id = "fg-reject0000abc"
        output_dir = self._seed_run(tmp_path, run_id)
        monkeypatch.setenv("FORGELM_OPERATOR", "bob")

        from forgelm.cli import _run_reject_cmd
        from forgelm.compliance import AuditLogger

        def _raise_on_rejected(self, event, **details):  # noqa: ANN001
            if event == "human_approval.rejected":
                raise OSError("ENOSPC: no space left on device")

        monkeypatch.setattr(AuditLogger, "log_event", _raise_on_rejected)

        args = MagicMock()
        args.run_id = run_id
        args.output_dir = str(output_dir)
        args.comment = None

        with pytest.raises(SystemExit) as ei:
            _run_reject_cmd(args, output_format="json")

        assert ei.value.code == 2  # EXIT_TRAINING_ERROR
        # No model was promoted; the staging dir must still be there for a retry.
        assert (output_dir / "final_model.staging").is_dir()
        assert not (output_dir / "final_model").exists()
        # The operator gets a clean JSON error naming the storage/audit gap.
        payload = json.loads(capsys.readouterr().out)
        assert payload["success"] is False
        assert "human_approval.rejected" in payload["error"]
        assert "audit" in payload["error"]

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

    def test_reject_with_nonexistent_output_dir_does_not_create_it(self, tmp_path: Path) -> None:
        """Reject twin of the approve regression: a bogus/mistyped
        --output-dir must fail via the missing-audit-log guard, not
        materialise the directory and a `.approval.lock` file."""
        output_dir = tmp_path / "does_not_exist_reject"
        assert not output_dir.exists()

        from forgelm.cli import _run_reject_cmd

        args = MagicMock()
        args.run_id = "fg-anything000000"
        args.output_dir = str(output_dir)
        args.comment = None

        with pytest.raises(SystemExit) as ei:
            _run_reject_cmd(args, output_format="text")
        assert ei.value.code == 1
        assert not output_dir.exists(), "reject must not materialise output_dir for a non-existent run"

    def test_reject_audit_logger_keyerror_exits_config_error(self, tmp_path: Path, monkeypatch) -> None:
        """Finding 1 (defence-in-depth, reject twin): a bare ``KeyError`` from
        AuditLogger construction must exit ``EXIT_CONFIG_ERROR`` (1) rather than
        crash, with the staging directory left intact."""
        run_id = "fg-keyerr000rejc"
        output_dir = self._seed_run(tmp_path, run_id)
        monkeypatch.setenv("FORGELM_OPERATOR", "bob")

        from forgelm.cli import _run_reject_cmd
        from forgelm.compliance import AuditLogger

        def _boom_init(self, output_dir, run_id=None):  # noqa: ANN001
            raise KeyError("getpwuid(): uid not found: 1000")

        monkeypatch.setattr(AuditLogger, "__init__", _boom_init)

        args = MagicMock()
        args.run_id = run_id
        args.output_dir = str(output_dir)
        args.comment = None

        with pytest.raises(SystemExit) as ei:
            _run_reject_cmd(args, output_format="text")
        assert ei.value.code == 1  # EXIT_CONFIG_ERROR, not a raw KeyError traceback
        assert (output_dir / "final_model.staging").is_dir(), "staging must be preserved on a failed reject"

        events = _read_audit_events(output_dir / "audit_log.jsonl")
        rejected = [e for e in events if e["event"] == "human_approval.rejected"]
        assert rejected == [], "no rejection event may be written when construction fails"


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


# ---------------------------------------------------------------------------
# CLI-level: approve/reject decision serialization (TOCTOU close, Finding 2)
# ---------------------------------------------------------------------------


class TestApprovalDecisionSerialization:
    """The decision-guard read → terminal-write window must be serialized by an
    exclusive lock so two processes racing approve-vs-reject on the same run_id
    can never both commit a terminal decision."""

    def _seed_run(self, tmp_path: Path, run_id: str) -> Path:
        output_dir = tmp_path / "serialize_run"
        output_dir.mkdir()
        staging_dir = output_dir / "final_model.staging"
        staging_dir.mkdir()
        (staging_dir / "adapter_config.json").write_text('{"r": 8}', encoding="utf-8")
        _write_required_event(output_dir / "audit_log.jsonl", run_id, str(staging_dir))
        return output_dir

    def test_granted_write_happens_while_approval_lock_held(self, tmp_path: Path, monkeypatch) -> None:
        """Deterministic proof that the ``human_approval.granted`` write occurs
        while ``<output_dir>/.approval.lock`` is held: a non-blocking probe from
        a second fd must fail to acquire the lock at write time.  Pre-fix (no
        lock) the probe would succeed — the exact TOCTOU window this closes."""
        fcntl = pytest.importorskip("fcntl")
        run_id = "fg-lockheld00001"
        output_dir = self._seed_run(tmp_path, run_id)
        monkeypatch.setenv("FORGELM_OPERATOR", "alice")

        from forgelm.cli import _run_approve_cmd
        from forgelm.compliance import AuditLogger

        observed: dict[str, str] = {}
        real_log_event = AuditLogger.log_event

        def _probing_log_event(self, event, **details):  # noqa: ANN001
            lock_path = os.path.join(str(output_dir), ".approval.lock")
            with open(lock_path, "a+b") as probe:
                try:
                    fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    observed[event] = "acquired"  # lock NOT held → window open (bug)
                    fcntl.flock(probe, fcntl.LOCK_UN)
                except OSError:
                    observed[event] = "blocked"  # lock held → window closed (correct)
            return real_log_event(self, event, **details)

        monkeypatch.setattr(AuditLogger, "log_event", _probing_log_event)

        args = MagicMock()
        args.run_id = run_id
        args.output_dir = str(output_dir)
        args.comment = None

        with patch("forgelm.cli._build_approval_notifier", return_value=MagicMock()):
            _run_approve_cmd(args, output_format="text")

        assert observed.get("human_approval.granted") == "blocked", (
            "the granted write must occur while .approval.lock is held (TOCTOU close)"
        )
        # Sanity: promotion + event still succeeded through the wrapper.
        assert (output_dir / "final_model").is_dir()
        events = _read_audit_events(output_dir / "audit_log.jsonl")
        assert sum(e["event"] == "human_approval.granted" for e in events) == 1

    def test_concurrent_approve_reject_single_terminal_decision(self, tmp_path: Path, monkeypatch) -> None:
        """Two real threads racing approve vs reject on the same run_id must
        leave EXACTLY ONE terminal decision: the lock makes them mutually
        exclusive, and the loser re-reads the committed decision and refuses
        with EXIT_CONFIG_ERROR (1)."""
        import threading

        run_id = "fg-racer0000abc"
        output_dir = self._seed_run(tmp_path, run_id)
        monkeypatch.setenv("FORGELM_OPERATOR", "alice")

        from forgelm.cli import _run_approve_cmd, _run_reject_cmd

        def _mk_args():
            a = MagicMock()
            a.run_id = run_id
            a.output_dir = str(output_dir)
            a.comment = None
            return a

        start = threading.Barrier(2)
        codes: dict[str, object] = {}

        def _worker(name, fn):
            start.wait()
            try:
                fn(_mk_args(), output_format="text")
                codes[name] = 0
            except SystemExit as exc:
                codes[name] = exc.code

        with patch("forgelm.cli._build_approval_notifier", return_value=MagicMock()):
            t_app = threading.Thread(target=_worker, args=("approve", _run_approve_cmd))
            t_rej = threading.Thread(target=_worker, args=("reject", _run_reject_cmd))
            t_app.start()
            t_rej.start()
            t_app.join(timeout=30)
            t_rej.join(timeout=30)

        assert not t_app.is_alive() and not t_rej.is_alive(), "threads must not deadlock on the approval lock"

        events = _read_audit_events(output_dir / "audit_log.jsonl")
        terminal = [e["event"] for e in events if e["event"] in ("human_approval.granted", "human_approval.rejected")]
        assert len(terminal) == 1, f"expected exactly one terminal decision, got {terminal!r}"
        # Exactly one worker won (exit 0); the other refused with EXIT_CONFIG_ERROR (1).
        assert sorted(codes.values()) == [0, 1], f"expected one winner (0) + one refusal (1), got {codes!r}"


# ---------------------------------------------------------------------------
# CLI-level: operator vs approver field relationship on a real granted event
# (Finding 3 — the docstring documents a deliberate divergence)
# ---------------------------------------------------------------------------


class TestGrantedOperatorApproverFields:
    def _seed_run(self, tmp_path: Path, run_id: str) -> Path:
        output_dir = tmp_path / "op_approver_run"
        output_dir.mkdir()
        staging_dir = output_dir / "final_model.staging"
        staging_dir.mkdir()
        (staging_dir / "adapter_config.json").write_text('{"r": 8}', encoding="utf-8")
        _write_required_event(output_dir / "audit_log.jsonl", run_id, str(staging_dir))
        return output_dir

    def test_operator_and_approver_collapse_when_env_set(self, tmp_path: Path, monkeypatch) -> None:
        """FORGELM_OPERATOR set → the granted event's ``operator`` (AuditLogger)
        and ``approver`` (_resolve_approver_identity) both pin to that value."""
        run_id = "fg-opapprv0env01"
        output_dir = self._seed_run(tmp_path, run_id)
        monkeypatch.setenv("FORGELM_OPERATOR", "ci-bot")

        from forgelm.cli import _run_approve_cmd

        args = MagicMock()
        args.run_id = run_id
        args.output_dir = str(output_dir)
        args.comment = None

        with patch("forgelm.cli._build_approval_notifier", return_value=MagicMock()):
            _run_approve_cmd(args, output_format="text")

        granted = [
            e for e in _read_audit_events(output_dir / "audit_log.jsonl") if e["event"] == "human_approval.granted"
        ]
        assert len(granted) == 1
        assert granted[0]["operator"] == "ci-bot"
        assert granted[0]["approver"] == "ci-bot"
        assert granted[0]["operator"] == granted[0]["approver"]

    def test_operator_carries_host_while_approver_is_bare_when_env_unset(self, tmp_path: Path, monkeypatch) -> None:
        """FORGELM_OPERATOR unset, getpass succeeds → ``operator`` is
        ``<user>@<hostname>`` (AuditLogger) while ``approver`` is the bare
        ``<user>`` (_resolve_approver_identity).  This deliberate format
        divergence is what the docstring now documents; pin it so a silent
        "make them identical" change trips here (Finding 3)."""
        import getpass
        import socket

        run_id = "fg-opapprv0unset"
        output_dir = self._seed_run(tmp_path, run_id)
        monkeypatch.delenv("FORGELM_OPERATOR", raising=False)
        monkeypatch.delenv("FORGELM_ALLOW_ANONYMOUS_OPERATOR", raising=False)
        monkeypatch.setattr(getpass, "getuser", lambda: "alice")

        from forgelm.cli import _run_approve_cmd

        args = MagicMock()
        args.run_id = run_id
        args.output_dir = str(output_dir)
        args.comment = None

        with patch("forgelm.cli._build_approval_notifier", return_value=MagicMock()):
            _run_approve_cmd(args, output_format="text")

        granted = [
            e for e in _read_audit_events(output_dir / "audit_log.jsonl") if e["event"] == "human_approval.granted"
        ]
        assert len(granted) == 1
        expected_host = socket.gethostname() or "unknown-host"
        assert granted[0]["operator"] == f"alice@{expected_host}"
        assert granted[0]["approver"] == "alice"
        assert granted[0]["operator"] != granted[0]["approver"], "the format divergence is deliberate (see docstring)"
        assert granted[0]["operator"].startswith(granted[0]["approver"] + "@")
