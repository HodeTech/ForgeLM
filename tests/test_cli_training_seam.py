"""CLI training-seam exit-code mapping tests (F-P7-OPUS-20 / M5).

``forgelm.cli._training._run_training_pipeline`` is the single-stage CLI
entry point that maps a :class:`forgelm.results.TrainResult` (or an
exception from ``ForgeTrainer.train()``) to the public exit-code contract:

* ``success=True`` + ``awaiting_approval=True`` -> EXIT_AWAITING_APPROVAL (4)
* ``success=True``                              -> EXIT_SUCCESS (0)
* ``success=False``                             -> EXIT_EVAL_FAILURE (3)
* ``train()`` raises (non-ConfigError)          -> EXIT_TRAINING_ERROR (2)
* ``train()`` raises ConfigError                -> EXIT_CONFIG_ERROR (1)

Prior to this module the only CLI test that reached this seam stubbed the
whole function out (tests/test_cli_phase10.py), so a regression flipping the
awaiting-approval branch or letting the BLE001 catch swallow without exiting
non-zero would have shipped green. These tests drive a real ``TrainResult``
(and a real raise) through the seam with the heavy ML boundaries mocked — no
GPU, no network, no model download.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from forgelm.cli import _training
from forgelm.cli._exit_codes import (
    EXIT_AWAITING_APPROVAL,
    EXIT_CONFIG_ERROR,
    EXIT_EVAL_FAILURE,
    EXIT_SUCCESS,
    EXIT_TRAINING_ERROR,
)
from forgelm.config import ConfigError
from forgelm.results import TrainResult


def _make_config():
    """Minimal config stand-in carrying only what the seam reads."""
    return SimpleNamespace(
        model=SimpleNamespace(offline=True),
        auth=None,
        training=SimpleNamespace(output_dir="/tmp/forgelm-test-out"),
    )


def _make_args(output_format="text"):
    return SimpleNamespace(resume=None, output_format=output_format)


class _FakeTrainer:
    """Stand-in for ForgeTrainer that returns a preset result or raises."""

    def __init__(self, result=None, raises=None, **_kwargs):
        self._result = result
        self._raises = raises

    def train(self, resume_from_checkpoint=None):
        if self._raises is not None:
            raise self._raises
        return self._result


@pytest.fixture
def _patched_seam(monkeypatch):
    """Patch every heavy boundary the seam imports so only the mapping runs."""
    # The ABI preflight imports torch — neutralise it.
    monkeypatch.setattr(_training, "_preflight_numpy_torch_abi", lambda _json: None)
    # The seam imports these names function-locally from their source modules,
    # so patch them where they are defined.
    monkeypatch.setattr("forgelm.model.get_model_and_tokenizer", lambda _config: (object(), object()))
    monkeypatch.setattr("forgelm.data.prepare_dataset", lambda _config, _tok: object())
    monkeypatch.setattr("forgelm.utils.setup_authentication", lambda _token: None)
    monkeypatch.setattr("forgelm.utils.manage_checkpoints", lambda _dir, action="keep": None)
    return monkeypatch


def _run_with_trainer(monkeypatch, trainer, output_format="text"):
    monkeypatch.setattr("forgelm.trainer.ForgeTrainer", lambda **kw: trainer)
    with pytest.raises(SystemExit) as exc_info:
        _training._run_training_pipeline(_make_config(), _make_args(output_format), output_format == "json")
    return exc_info.value.code


def test_pipeline_exit_code_when_awaiting_approval_is_exit_awaiting_approval(_patched_seam):
    result = TrainResult(success=True, awaiting_approval=True, staging_path="/tmp/x.staging")
    code = _run_with_trainer(_patched_seam, _FakeTrainer(result=result))
    assert code == EXIT_AWAITING_APPROVAL


def test_pipeline_exit_code_when_success_no_approval_is_exit_success(_patched_seam):
    result = TrainResult(success=True, awaiting_approval=False)
    code = _run_with_trainer(_patched_seam, _FakeTrainer(result=result))
    assert code == EXIT_SUCCESS


def test_pipeline_exit_code_when_reverted_is_exit_eval_failure(_patched_seam):
    # A failed/auto-reverted gate: success=False must route to exit 3 even
    # if the run was configured for human approval.
    result = TrainResult(success=False, reverted=True, awaiting_approval=False)
    code = _run_with_trainer(_patched_seam, _FakeTrainer(result=result))
    assert code == EXIT_EVAL_FAILURE


def test_pipeline_exit_code_when_train_raises_is_exit_training_error(_patched_seam):
    code = _run_with_trainer(_patched_seam, _FakeTrainer(raises=RuntimeError("CUDA OOM")))
    assert code == EXIT_TRAINING_ERROR


def test_pipeline_exit_code_when_train_raises_config_error_is_exit_config_error(_patched_seam):
    code = _run_with_trainer(_patched_seam, _FakeTrainer(raises=ConfigError("judge_api_key_env unset")))
    assert code == EXIT_CONFIG_ERROR


def test_pipeline_json_mode_emits_failure_envelope_on_crash(_patched_seam, capsys):
    # The BLE001 catch must convert a train() crash into a structured JSON
    # envelope on stdout (not a Python traceback that breaks JSON parsers).
    code = _run_with_trainer(_patched_seam, _FakeTrainer(raises=RuntimeError("boom")), output_format="json")
    assert code == EXIT_TRAINING_ERROR
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["success"] is False
    assert "boom" in envelope["error"]
