"""Tests for SFT ``SFTConfig`` kwarg construction across trl versions.

Regression guard: trl 0.13 renamed the sequence-length cap from
``max_seq_length`` to ``max_length`` on ``SFTConfig`` and removed the old
name. Passing the old kwarg raises ``TypeError`` at trainer args build
time and aborts training on every modern trl install.

``pyproject.toml`` pins ``trl>=1.0.0,<2.0.0`` (all expose ``max_length``),
so the trainer drives ``max_length`` directly but verifies it at runtime
and hard-fails if a future trl removes or hides the parameter.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _make_sft_config(tmp_path):
    from forgelm.config import ForgeConfig

    return ForgeConfig(
        **{
            "model": {"name_or_path": "org/model", "max_length": 2048},
            "lora": {},
            "training": {
                "trainer_type": "sft",
                "output_dir": str(tmp_path),
            },
            "data": {"dataset_name_or_path": "org/dataset"},
        }
    )


def _seed_trainer(tmp_path):
    """Build a bare trainer skeleton without invoking heavy init."""
    from forgelm.trainer import ForgeTrainer

    config = _make_sft_config(tmp_path)
    trainer = ForgeTrainer.__new__(ForgeTrainer)
    trainer.model = MagicMock()
    trainer.tokenizer = MagicMock()
    trainer.config = config
    trainer.dataset = {"train": list(range(10))}
    trainer.checkpoint_dir = str(tmp_path)
    trainer.run_name = "sft_kwarg_test"
    trainer.notifier = MagicMock()
    trainer.audit = MagicMock()
    return trainer


def test_sft_config_uses_max_length_on_modern_trl(tmp_path):
    """trl 0.13+ removed ``max_seq_length``; the trainer must use
    ``max_length`` instead."""

    captured_kwargs: dict = {}

    class FakeModernSFTConfig:
        def __init__(self, *, max_length=None, packing=False, dataset_text_field=None, **other):
            captured_kwargs["max_length"] = max_length
            captured_kwargs["packing"] = packing
            captured_kwargs["dataset_text_field"] = dataset_text_field
            captured_kwargs.update(other)

    trainer = _seed_trainer(tmp_path)

    with patch("trl.SFTConfig", FakeModernSFTConfig):
        trainer._get_training_args_for_type()

    assert captured_kwargs["max_length"] == 2048, (
        "Modern trl `SFTConfig(max_length=...)` must receive the model's "
        f"max_length (got: {captured_kwargs.get('max_length')!r})"
    )
    assert "max_seq_length" not in captured_kwargs, (
        "Legacy `max_seq_length` kwarg must NOT be passed to a modern SFTConfig — trl 0.13+ raises TypeError on it."
    )
    assert captured_kwargs["dataset_text_field"] == "text"


def test_sft_config_passes_packing_and_dataset_text_field(tmp_path):
    """Regression: ``packing`` and ``dataset_text_field`` must keep being
    propagated alongside the sequence-length cap."""

    captured_kwargs: dict = {}

    class FakeSFTConfig:
        def __init__(self, *, max_length=None, packing=False, dataset_text_field=None, **other):
            captured_kwargs["max_length"] = max_length
            captured_kwargs["packing"] = packing
            captured_kwargs["dataset_text_field"] = dataset_text_field

    trainer = _seed_trainer(tmp_path)
    trainer.config.training.packing = True

    with patch("trl.SFTConfig", FakeSFTConfig):
        trainer._get_training_args_for_type()

    assert captured_kwargs["packing"] is True
    assert captured_kwargs["dataset_text_field"] == "text"


def test_sample_packing_forwards_to_packing_in_trl_kwargs(tmp_path):
    """Deprecated ``sample_packing: true`` must no longer be a silent no-op:
    it forwards to ``packing`` at config time, so the TRL SFT kwargs reflect
    it (F-P1-FAB-15)."""
    from forgelm.config import ForgeConfig

    captured_kwargs: dict = {}

    class FakeSFTConfig:
        def __init__(self, *, max_length=None, packing=False, dataset_text_field=None, **other):
            captured_kwargs["packing"] = packing

    config = ForgeConfig(
        **{
            "model": {"name_or_path": "org/model", "max_length": 2048},
            "lora": {},
            "training": {"trainer_type": "sft", "output_dir": str(tmp_path), "sample_packing": True},
            "data": {"dataset_name_or_path": "org/dataset"},
        }
    )
    # The deprecated flag was forwarded at validation time.
    assert config.training.packing is True

    trainer = _seed_trainer(tmp_path)
    trainer.config = config
    with patch("trl.SFTConfig", FakeSFTConfig):
        trainer._get_training_args_for_type()

    assert captured_kwargs["packing"] is True


def test_sft_config_prefers_max_length_when_both_present(tmp_path):
    """Transitional alias release: if a trl version exposes BOTH
    ``max_length`` and ``max_seq_length`` (deprecated-alias window), the
    trainer must prefer the modern ``max_length`` and never duplicate the
    cap onto the legacy kwarg."""

    captured_kwargs: dict = {}

    class FakeBothSFTConfig:
        def __init__(
            self,
            *,
            max_length=None,
            max_seq_length=None,
            packing=False,
            dataset_text_field=None,
            **other,
        ):
            captured_kwargs["max_length"] = max_length
            captured_kwargs["max_seq_length"] = max_seq_length
            captured_kwargs["packing"] = packing
            captured_kwargs["dataset_text_field"] = dataset_text_field

    trainer = _seed_trainer(tmp_path)

    with patch("trl.SFTConfig", FakeBothSFTConfig):
        trainer._get_training_args_for_type()

    assert captured_kwargs["max_length"] == 2048, (
        "When both names are exposed, the trainer must drive the modern "
        f"`max_length` kwarg (got: {captured_kwargs.get('max_length')!r})."
    )
    assert captured_kwargs["max_seq_length"] is None, (
        "Legacy `max_seq_length` must NOT be set when `max_length` is also "
        "available — passing both risks the *Config raising a duplicate-spec error."
    )


def test_sft_config_raises_when_neither_seqlen_param_exposed(tmp_path):
    """Future-proofing: if trl renames the sequence-length kwarg yet
    again (or hides it behind ``**kwargs``), the trainer must raise
    rather than silently drop ``model.max_length``. Silent loss of an
    explicit YAML setting violates the 'no silent failures' rule in
    docs/standards/error-handling.md."""

    class FakeFutureSFTConfig:
        def __init__(self, *, packing=False, dataset_text_field=None, **other):
            # Note: no `max_length` and no `max_seq_length` as named
            # parameters — they would be swallowed into **other if passed.
            pass

    trainer = _seed_trainer(tmp_path)

    with patch("trl.SFTConfig", FakeFutureSFTConfig):
        with pytest.raises(ValueError, match="max_length.*max_seq_length"):
            trainer._get_training_args_for_type()


def test_sft_config_raises_when_seqlen_only_in_kwargs(tmp_path):
    """``inspect.signature(SFTConfig).parameters`` only surfaces *named*
    parameters; if a future trl release moves ``max_length`` behind a
    catch-all ``**kwargs`` (or aliases it via a decorator), the named-
    parameter detection will miss it and the trainer must hard-fail
    rather than letting TRL pick its default sequence-length cap."""

    class FakeKwargsOnlySFTConfig:
        def __init__(self, **kwargs):
            # `max_length` is only accepted via **kwargs — not as a named
            # parameter. ``inspect.signature(...).parameters`` will
            # expose the VAR_KEYWORD entry but neither
            # "max_length" nor "max_seq_length" by name.
            pass

    trainer = _seed_trainer(tmp_path)

    with patch("trl.SFTConfig", FakeKwargsOnlySFTConfig):
        with pytest.raises(ValueError, match="max_length.*max_seq_length"):
            trainer._get_training_args_for_type()
