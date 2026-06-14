"""F-P3-FABLE-03: ``model.max_length`` must reach every TRL trainer config.

Only the SFT branch used to pass the sequence-length cap; the preference
trainers (DPO / ORPO / SimPO / KTO) silently fell back to TRL's own 512/1024
``max_length`` defaults while the config field and the Article 11 compliance
manifest both claimed ``model.max_length`` applied — silent truncation plus a
false statement in an audit artefact. GRPO must honour it as the prompt cap.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _seed_trainer(tmp_path, trainer_type: str, max_length: int = 4096):
    from forgelm.config import ForgeConfig
    from forgelm.trainer import ForgeTrainer

    config = ForgeConfig(
        **{
            "model": {"name_or_path": "org/model", "max_length": max_length},
            "lora": {},
            "training": {"trainer_type": trainer_type, "output_dir": str(tmp_path)},
            "data": {"dataset_name_or_path": "org/dataset"},
        }
    )
    trainer = ForgeTrainer.__new__(ForgeTrainer)
    trainer.model = MagicMock()
    trainer.tokenizer = MagicMock()
    trainer.config = config
    trainer.dataset = {"train": list(range(10))}
    trainer.checkpoint_dir = str(tmp_path)
    trainer.run_name = "max_length_test"
    trainer.notifier = MagicMock()
    trainer.audit = MagicMock()
    return trainer


@pytest.mark.parametrize(
    ("trainer_type", "config_attr"),
    [("dpo", "DPOConfig"), ("orpo", "ORPOConfig"), ("kto", "KTOConfig"), ("simpo", "CPOConfig")],
)
def test_preference_trainer_passes_max_length(tmp_path, trainer_type, config_attr):
    """Each preference trainer config must receive ``model.max_length``."""
    import trl

    # Some torch/trl/vllm pairings can't import a given trainer's config in this
    # environment (lazy-module RuntimeError); patch.object's getattr probe would
    # trigger it. Skip those — the cap-application logic is identical per config.
    try:
        getattr(trl, config_attr)
    except (ImportError, AttributeError, RuntimeError) as exc:
        pytest.skip(f"trl.{config_attr} not importable here: {exc}")

    captured: dict = {}

    class FakeConfig:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    trainer = _seed_trainer(tmp_path, trainer_type)
    with patch.object(trl, config_attr, FakeConfig):
        trainer._get_training_args_for_type()

    assert captured.get("max_length") == 4096, (
        f"{config_attr} must receive model.max_length=4096; got {captured.get('max_length')!r}"
    )


def test_grpo_passes_max_length_as_prompt_cap(tmp_path):
    """GRPO has no single ``max_length``; ``model.max_length`` must reach the
    ``max_prompt_length`` prompt cap so prompts aren't truncated at TRL's 512
    default."""
    import trl

    captured: dict = {}

    class FakeGRPOConfig:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    trainer = _seed_trainer(tmp_path, "grpo")
    with patch.object(trl, "GRPOConfig", FakeGRPOConfig):
        trainer._get_training_args_for_type()

    assert captured.get("max_prompt_length") == 4096, (
        f"GRPOConfig must receive max_prompt_length=model.max_length; got {captured.get('max_prompt_length')!r}"
    )
