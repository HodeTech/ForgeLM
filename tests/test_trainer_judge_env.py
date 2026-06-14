"""F-P3-FABLE-18: a configured judge API-key env var that is unset must fail
loud, never silently flip the judge to local mode.

If ``judge_api_key_env`` names an API judge but the variable is unset,
``judge.py`` would treat ``api_key=None`` as "local" and try to load
``judge_model`` from the Hub — a different evaluator than configured, and with
``auto_revert=true`` a failed local load deletes the trained adapters over a
misdiagnosed env-var problem. The trainer must raise a ConfigError naming the
variable BEFORE invoking the judge (so auto-revert never fires).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _seed_judge_trainer(tmp_path, api_key_env):
    from forgelm.config import ForgeConfig
    from forgelm.trainer import ForgeTrainer

    config = ForgeConfig(
        **{
            "model": {"name_or_path": "org/model"},
            "lora": {},
            "training": {"output_dir": str(tmp_path)},
            "data": {"dataset_name_or_path": "org/dataset"},
            "evaluation": {
                "auto_revert": True,
                "llm_judge": {
                    "enabled": True,
                    "judge_model": "gpt-4o",
                    "judge_api_key_env": api_key_env,
                    "eval_dataset": str(tmp_path / "eval.jsonl"),
                },
            },
        }
    )
    trainer = ForgeTrainer.__new__(ForgeTrainer)
    trainer.config = config
    trainer.tokenizer = MagicMock()
    trainer.trainer = MagicMock()
    trainer.checkpoint_dir = str(tmp_path)
    trainer.run_name = "judge_env_test"
    return trainer


def test_unset_judge_api_key_env_raises_not_local_flip(tmp_path, monkeypatch):
    from forgelm.config import ConfigError

    monkeypatch.delenv("FORGELM_TEST_JUDGE_KEY", raising=False)
    trainer = _seed_judge_trainer(tmp_path, "FORGELM_TEST_JUDGE_KEY")

    with patch("forgelm.judge.run_judge_evaluation") as mock_run:
        with pytest.raises(ConfigError, match="FORGELM_TEST_JUDGE_KEY"):
            trainer._run_judge_if_configured()
        mock_run.assert_not_called()  # never falls through to (local) judge eval


def test_set_judge_api_key_env_runs_judge(tmp_path, monkeypatch):
    """Sanity: when the env var IS set, the judge runs in API mode as configured."""
    monkeypatch.setenv("FORGELM_TEST_JUDGE_KEY", "sk-test-key")
    trainer = _seed_judge_trainer(tmp_path, "FORGELM_TEST_JUDGE_KEY")

    with patch("forgelm.judge.run_judge_evaluation", return_value=MagicMock()) as mock_run:
        trainer._run_judge_if_configured()
        mock_run.assert_called_once()
        assert mock_run.call_args.kwargs["judge_api_key"] == "sk-test-key"
