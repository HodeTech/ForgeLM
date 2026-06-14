"""Regression tests for ``forgelm quickstart`` subprocess wiring.

These tests cover three defects fixed in :func:`forgelm.cli._run_quickstart_cmd`:

1. ``--offline`` was not propagated to the child training / chat subprocesses.
2. ``--output-format json`` was forwarded to the training and chat subprocesses,
   producing two top-level JSON objects on stdout (unparseable by any JSON consumer).
   The quickstart parent now owns the JSON envelope; neither subprocess receives it.
3. The chat subprocess return code was silently swallowed.

Pattern (re-usable by other agents):

* ``patch("forgelm.quickstart._detect_available_vram_gb", return_value=24.0)``
  to stop quickstart from probing real GPUs.
* ``patch("subprocess.run", new=recorder)`` where ``recorder`` is a small
  callable that captures ``argv`` and returns a stub with ``returncode``.
  ``subprocess`` is imported lazily inside ``_run_quickstart_cmd`` so patching
  the canonical module reference works.
* ``patch("forgelm.cli._load_quickstart_train_paths", ...)`` plus a temp dir
  for ``final_model_dir`` so the ``.is_dir()`` check passes without an actual
  trained checkpoint.
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

import pytest


class _RunRecorder:
    """Captures every ``subprocess.run`` call's argv list.

    Returning an object with a configurable ``returncode`` lets a single
    instance stand in for both the training run and the chat run.
    """

    def __init__(self, returncodes: list[int] | None = None) -> None:
        # Default: training succeeds, chat succeeds.
        self.returncodes = returncodes if returncodes is not None else [0, 0]
        self.calls: list[list[str]] = []

    def __call__(self, argv, *args, **kwargs):  # noqa: D401 — match subprocess.run
        self.calls.append(list(argv))
        rc = self.returncodes[len(self.calls) - 1] if len(self.calls) - 1 < len(self.returncodes) else 0

        class _Completed:
            returncode = rc

        return _Completed()


@pytest.fixture
def fake_train_paths(tmp_path: Path):
    """Patch the YAML reader so the chat block sees an existing model dir."""
    final_dir = tmp_path / "checkpoints" / "final_model"
    final_dir.mkdir(parents=True)

    with patch(
        "forgelm.cli._load_quickstart_train_paths",
        return_value=(str(tmp_path / "checkpoints"), "final_model"),
    ):
        yield final_dir


def _invoke_main(argv: list[str], recorder: _RunRecorder) -> SystemExit:
    """Run ``forgelm.cli.main`` with subprocess and VRAM probing patched out.

    Returns the captured :class:`SystemExit` so callers can assert on
    ``exc.code``.
    """
    from forgelm.cli import main

    with (
        patch("forgelm.quickstart._detect_available_vram_gb", return_value=24.0),
        patch("subprocess.run", new=recorder),
        patch("sys.argv", argv),
    ):
        with pytest.raises(SystemExit) as exc_info:
            main()
    return exc_info.value


# ---------------------------------------------------------------------------
# 1. --offline propagation
# ---------------------------------------------------------------------------


class TestOfflinePropagation:
    def test_offline_forwarded_to_train_subprocess(self, tmp_path, fake_train_paths):
        """--offline on the parent must reach the child training subprocess."""
        config_out = tmp_path / "cfg.yaml"
        recorder = _RunRecorder()
        argv = [
            "forgelm",
            "--offline",
            "quickstart",
            "customer-support",
            "--output",
            str(config_out),
        ]

        exc = _invoke_main(argv, recorder)
        assert exc.code == 0
        assert len(recorder.calls) == 2, "expected train + chat subprocess calls"
        train_argv, chat_argv = recorder.calls
        assert "--offline" in train_argv, f"--offline missing from train argv: {train_argv}"
        # Bonus: --offline must also propagate to the chat subprocess so the
        # auto-launched REPL doesn't re-enable network access.
        assert "--offline" in chat_argv, f"--offline missing from chat argv: {chat_argv}"

    def test_offline_absent_when_flag_not_set(self, tmp_path, fake_train_paths):
        config_out = tmp_path / "cfg.yaml"
        recorder = _RunRecorder()
        argv = [
            "forgelm",
            "quickstart",
            "customer-support",
            "--output",
            str(config_out),
        ]

        _invoke_main(argv, recorder)
        for call_argv in recorder.calls:
            assert "--offline" not in call_argv


# ---------------------------------------------------------------------------
# 2. --output-format json must NOT leak into the interactive chat subprocess
# ---------------------------------------------------------------------------


class TestOutputFormatNotForwardedToSubprocesses:
    def test_json_mode_runs_train_only_without_forwarding_format(self, tmp_path, fake_train_paths):
        """In JSON mode only the training subprocess runs, and the format flag is not forwarded.

        The quickstart parent owns the JSON envelope. Forwarding ``--output-format
        json`` to the training child would produce two top-level JSON objects on
        stdout, making the stream unparseable. The interactive chat REPL is
        suppressed entirely in JSON mode (F-P7-OPUS-25) — its human prose would
        otherwise interleave with the envelope on stdout — so only the train
        subprocess is spawned.
        """
        config_out = tmp_path / "cfg.yaml"
        recorder = _RunRecorder()
        argv = [
            "forgelm",
            "quickstart",
            "customer-support",
            "--output",
            str(config_out),
            "--output-format",
            "json",
        ]

        _invoke_main(argv, recorder)
        # Chat is suppressed in JSON mode → only the train subprocess runs.
        assert len(recorder.calls) == 1
        (train_argv,) = recorder.calls

        assert "--output-format" not in train_argv
        assert "json" not in train_argv


# ---------------------------------------------------------------------------
# 3. chat subprocess non-zero exit code must be surfaced via logging
# ---------------------------------------------------------------------------


class TestChatExitCodeNotSwallowed:
    def test_chat_crash_logs_warning_but_still_exits_zero(self, tmp_path, fake_train_paths, caplog):
        """Crashed chat REPL must not be invisible. Training still succeeded."""
        config_out = tmp_path / "cfg.yaml"
        # Train: 0 (success). Chat: 2 (crash).
        recorder = _RunRecorder(returncodes=[0, 2])
        argv = [
            "forgelm",
            "quickstart",
            "customer-support",
            "--output",
            str(config_out),
        ]

        with caplog.at_level(logging.WARNING, logger="forgelm.cli"):
            exc = _invoke_main(argv, recorder)

        # Training run succeeded → quickstart exits 0 even though chat crashed.
        assert exc.code == 0
        # And the operator gets a visible warning about the chat crash.
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("Chat subprocess exited with code 2" in r.getMessage() for r in warnings), (
            f"Expected chat-exit warning, got: {[r.getMessage() for r in warnings]}"
        )

    def test_chat_sigint_is_silent(self, tmp_path, fake_train_paths, caplog):
        """Code 130 (Ctrl-C from the REPL) is the normal exit path → no warning."""
        config_out = tmp_path / "cfg.yaml"
        recorder = _RunRecorder(returncodes=[0, 130])
        argv = [
            "forgelm",
            "quickstart",
            "customer-support",
            "--output",
            str(config_out),
        ]

        with caplog.at_level(logging.WARNING, logger="forgelm.cli"):
            exc = _invoke_main(argv, recorder)

        assert exc.code == 0
        chat_warnings = [r for r in caplog.records if "Chat subprocess exited" in r.getMessage()]
        assert chat_warnings == [], "SIGINT (130) must not raise a warning"

    def test_chat_clean_exit_is_silent(self, tmp_path, fake_train_paths, caplog):
        """Code 0 is the happy path → no warning."""
        config_out = tmp_path / "cfg.yaml"
        recorder = _RunRecorder(returncodes=[0, 0])
        argv = [
            "forgelm",
            "quickstart",
            "customer-support",
            "--output",
            str(config_out),
        ]

        with caplog.at_level(logging.WARNING, logger="forgelm.cli"):
            exc = _invoke_main(argv, recorder)

        assert exc.code == 0
        chat_warnings = [r for r in caplog.records if "Chat subprocess exited" in r.getMessage()]
        assert chat_warnings == []


# ---------------------------------------------------------------------------
# 4. Train rc must be clamped to ForgeLM's documented exit-code contract
# ---------------------------------------------------------------------------


class TestTrainExitCodeClamping:
    def test_signal_derived_rc_clamped_to_training_error(self, tmp_path, fake_train_paths):
        """rc=139 (SIGSEGV) must surface as EXIT_TRAINING_ERROR (2), not 139."""
        config_out = tmp_path / "cfg.yaml"
        # Train crashed with a signal-derived rc; chat would never run.
        recorder = _RunRecorder(returncodes=[139])
        argv = [
            "forgelm",
            "quickstart",
            "customer-support",
            "--output",
            str(config_out),
        ]

        exc = _invoke_main(argv, recorder)
        # 139 is outside (0,1,2,3,4) → clamped to EXIT_TRAINING_ERROR (2).
        assert exc.code == 2
        # Only one subprocess call: training crashed before chat could fire.
        assert len(recorder.calls) == 1

    def test_documented_rc_propagates_unchanged(self, tmp_path, fake_train_paths):
        """rc=3 (EXIT_EVAL_FAILURE) is part of the contract and must propagate as-is."""
        config_out = tmp_path / "cfg.yaml"
        recorder = _RunRecorder(returncodes=[3])
        argv = [
            "forgelm",
            "quickstart",
            "customer-support",
            "--output",
            str(config_out),
        ]

        exc = _invoke_main(argv, recorder)
        assert exc.code == 3
        assert len(recorder.calls) == 1
