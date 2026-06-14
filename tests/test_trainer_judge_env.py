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


def test_trainer_init_fails_fast_on_unset_judge_env(tmp_path, monkeypatch):
    """Review fix: the judge_api_key_env check runs at construction (preflight),
    failing BEFORE the training loop rather than at the post-train judge stage."""
    from forgelm.config import ConfigError, ForgeConfig
    from forgelm.trainer import ForgeTrainer

    monkeypatch.delenv("FORGELM_TEST_JUDGE_KEY", raising=False)
    config = ForgeConfig(
        **{
            "model": {"name_or_path": "org/model"},
            "lora": {},
            "training": {"output_dir": str(tmp_path)},
            "data": {"dataset_name_or_path": "org/dataset"},
            "evaluation": {
                "llm_judge": {"enabled": True, "judge_model": "gpt-4o", "judge_api_key_env": "FORGELM_TEST_JUDGE_KEY"}
            },
        }
    )
    with pytest.raises(ConfigError, match="FORGELM_TEST_JUDGE_KEY"):
        ForgeTrainer(model=MagicMock(), tokenizer=MagicMock(), config=config, dataset={"train": [{"text": "x"}]})


def test_set_judge_api_key_env_runs_judge(tmp_path, monkeypatch):
    """Sanity: when the env var IS set, the judge runs in API mode as configured."""
    monkeypatch.setenv("FORGELM_TEST_JUDGE_KEY", "sk-test-key")
    trainer = _seed_judge_trainer(tmp_path, "FORGELM_TEST_JUDGE_KEY")

    with patch("forgelm.judge.run_judge_evaluation", return_value=MagicMock()) as mock_run:
        trainer._run_judge_if_configured()
        mock_run.assert_called_once()
        assert mock_run.call_args.kwargs["judge_api_key"] == "sk-test-key"


def test_training_cli_maps_configerror_to_exit_1(tmp_path, monkeypatch):
    """C7-review: a ConfigError from the trainer (e.g. unset judge_api_key_env)
    must exit with EXIT_CONFIG_ERROR (1), not the generic EXIT_TRAINING_ERROR (2)."""
    from forgelm.cli._exit_codes import EXIT_CONFIG_ERROR
    from forgelm.cli._training import _run_training_pipeline
    from forgelm.config import ConfigError, ForgeConfig

    config = ForgeConfig(
        **{
            "model": {"name_or_path": "org/model"},
            "lora": {},
            "training": {"output_dir": str(tmp_path)},
            "data": {"dataset_name_or_path": "org/dataset"},
        }
    )

    class _RaisingTrainer:
        def __init__(self, **kwargs):
            pass

        def train(self, resume_from_checkpoint=None):
            raise ConfigError("judge_api_key_env names an unset variable")

    monkeypatch.setattr("forgelm.model.get_model_and_tokenizer", lambda c: (MagicMock(), MagicMock()))
    monkeypatch.setattr("forgelm.data.prepare_dataset", lambda c, t: {"train": [{"text": "x"}]})
    monkeypatch.setattr("forgelm.utils.setup_authentication", lambda token: None)
    monkeypatch.setattr("forgelm.trainer.ForgeTrainer", _RaisingTrainer)

    args = MagicMock()
    args.resume = None
    args.output_format = "text"
    with pytest.raises(SystemExit) as exc:
        _run_training_pipeline(config, args, json_output=False)
    assert exc.value.code == EXIT_CONFIG_ERROR
