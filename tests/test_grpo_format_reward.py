"""Tests for the built-in GRPO format / length shaping reward fallback.

Covers:

* :func:`forgelm.grpo_rewards.format_match_reward` — pattern matching
* :func:`forgelm.grpo_rewards.length_shaping_reward` — clipping / saturation
* :func:`forgelm.grpo_rewards.combined_format_length_reward` — weights
* The wiring inside :class:`forgelm.trainer.ForgeTrainer._build_trainer` that
  selects the format reward when ``grpo_reward_model`` is absent, and the
  classifier-based callable when it is present.

The trainer-wiring tests mirror the import-availability gating used in
``tests/test_grpo_reward.py`` so this file is safe on environments where
``trl.GRPOTrainer`` cannot be loaded (older / mismatched torch + trl pairs).
"""

from unittest.mock import MagicMock, patch

import pytest

from forgelm.grpo_rewards import (
    combined_format_length_reward,
    format_match_reward,
    length_shaping_reward,
)

# ---------------------------------------------------------------------------
# format_match_reward
# ---------------------------------------------------------------------------


def test_format_reward_matches_answer_pattern():
    """First completion ends with `Answer: <value>` → 1.0; second has no marker → 0.0."""
    completions = [
        "Solving: 4*15+5=65. Answer: 15",
        "I think the answer is 15",
    ]
    rewards = format_match_reward(completions)
    assert rewards[0] == pytest.approx(1.0)
    assert rewards[1] == pytest.approx(0.0)


def test_format_reward_handles_units():
    """Trailing units after the value still count as a format match."""
    completions = [
        "After computing distance/time: Answer: 15 km/h",
        "Final price: Answer: $40",
        "Total area: Answer: 40 m²",
    ]
    rewards = format_match_reward(completions)
    assert rewards == [1.0, 1.0, 1.0]


def test_format_reward_case_insensitive_and_trailing_whitespace():
    """The matcher is case-insensitive and tolerates trailing whitespace / newlines."""
    completions = [
        "ANSWER: 7\n",
        "answer:   42   ",
        "Answer:",  # no value → must NOT match
    ]
    rewards = format_match_reward(completions)
    assert rewards == [1.0, 1.0, 0.0]


def test_format_reward_handles_empty_and_none():
    """Empty / None / whitespace-only completions score 0.0 without raising."""
    rewards = format_match_reward(["", None, "    "])
    assert rewards == [0.0, 0.0, 0.0]


# ---------------------------------------------------------------------------
# length_shaping_reward
# ---------------------------------------------------------------------------


def test_length_shaping_caps_at_one():
    """Empty completion → 0.0; a 500-char completion saturates at 1.0."""
    completions = ["", "x" * 500]
    rewards = length_shaping_reward(completions)
    assert rewards[0] == pytest.approx(0.0)
    assert rewards[1] == pytest.approx(1.0)


def test_length_shaping_linear_below_saturation():
    """Below 200 chars the reward grows linearly with length."""
    completions = ["x" * 100]
    rewards = length_shaping_reward(completions)
    assert rewards[0] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# combined_format_length_reward
# ---------------------------------------------------------------------------


def test_combined_reward_weights():
    """0.8 * format + 0.2 * length, computed for a known input."""
    # 100 chars, no `Answer:` → format 0, length 100/200 = 0.5
    # combined = 0.8 * 0 + 0.2 * 0.5 = 0.1
    no_format = "x" * 100
    # 100 chars total ending with the marker → format 1, length 0.5
    # combined = 0.8 * 1 + 0.2 * 0.5 = 0.9
    with_format = ("y" * 89) + " Answer: 42"  # len == 100
    assert len(with_format) == 100  # guard against future edits

    rewards = combined_format_length_reward([no_format, with_format])
    assert rewards[0] == pytest.approx(0.1)
    assert rewards[1] == pytest.approx(0.9)


def test_combined_reward_strict_zip_raises_on_length_mismatch(monkeypatch):
    """A wiring regression that desyncs the two sub-rewards must raise loudly.

    The combine step zips ``format`` and ``length`` with ``strict=True`` so a
    future edit that makes one sub-reward return a wrong-length list fails fast
    instead of silently truncating to the shorter one (mirrors the strict zip
    in ``trainer._math_reward_fn``). F-P2-FAB-42.
    """
    monkeypatch.setattr(
        "forgelm.grpo_rewards.format_match_reward",
        lambda completions, **kwargs: [1.0],  # one element regardless of input
    )
    with pytest.raises(ValueError, match="zip"):
        combined_format_length_reward(["Answer: 1", "Answer: 2"])


# ---------------------------------------------------------------------------
# Trainer wiring — gated on torch + trl availability (mirrors test_grpo_reward).
# ---------------------------------------------------------------------------

torch_available = True
try:
    import torch  # noqa: F401
except ImportError:
    torch_available = False

grpo_patchable = False
if torch_available:
    try:
        import trl  # noqa: F401

        trl.GRPOTrainer  # noqa: B018 — trigger lazy loader
        grpo_patchable = True
    except (ImportError, AttributeError, RuntimeError):
        grpo_patchable = False


def _make_grpo_config(reward_model_path, output_dir):
    """Build a minimal ForgeConfig for a GRPO run."""
    from forgelm.config import ForgeConfig

    data = {
        "model": {"name_or_path": "org/model"},
        "lora": {},
        "training": {
            "trainer_type": "grpo",
            "output_dir": str(output_dir),
        },
        "data": {"dataset_name_or_path": "org/dataset"},
    }
    if reward_model_path is not None:
        data["training"]["grpo_reward_model"] = reward_model_path
    return ForgeConfig(**data)


def _stub_trainer(config, dataset, tmp_path):
    """Build a ForgeTrainer skeleton that bypasses the heavy ``__init__``."""
    from forgelm.trainer import ForgeTrainer

    trainer = ForgeTrainer.__new__(ForgeTrainer)
    trainer.model = MagicMock()
    trainer.tokenizer = MagicMock()
    trainer.config = config
    trainer.dataset = dataset
    trainer.checkpoint_dir = str(tmp_path)
    trainer.run_name = "grpo_format_test"
    trainer.notifier = MagicMock()
    trainer.audit = MagicMock()
    return trainer


@pytest.mark.skipif(not torch_available, reason="torch not installed")
@pytest.mark.skipif(
    not grpo_patchable,
    reason="trl.GRPOTrainer not importable in this environment (torch/trl version mismatch)",
)
def test_reward_func_used_when_no_classifier(tmp_path):
    """Without grpo_reward_model, the trainer wires a list of callable reward funcs."""
    config = _make_grpo_config(reward_model_path=None, output_dir=tmp_path)
    trainer = _stub_trainer(config, dataset={"train": list(range(4))}, tmp_path=tmp_path)

    captured: dict = {}

    def fake_grpo_trainer(**kwargs):
        captured.update(kwargs)
        return MagicMock()

    with (
        patch("trl.GRPOTrainer", side_effect=fake_grpo_trainer),
        patch("trl.GRPOConfig", return_value=MagicMock()),
    ):
        trainer._build_trainer(callbacks=[])

    reward_funcs = captured.get("reward_funcs")
    assert reward_funcs is not None, "reward_funcs must always be set on GRPOTrainer"
    assert isinstance(reward_funcs, list)
    assert len(reward_funcs) >= 1
    assert callable(reward_funcs[0])

    # The fallback callable must accept the TRL-shaped contract and return floats.
    sample = reward_funcs[0](["Solve: 2+2. Answer: 4"])
    assert isinstance(sample, list)
    assert all(isinstance(v, float) for v in sample)


@pytest.mark.skipif(not torch_available, reason="torch not installed")
@pytest.mark.skipif(
    not grpo_patchable,
    reason="trl.GRPOTrainer not importable in this environment (torch/trl version mismatch)",
)
def test_reward_func_uses_classifier_when_configured(tmp_path):
    """With grpo_reward_model set, the wired callable closes over the loaded reward model."""
    import torch as _torch

    config = _make_grpo_config(reward_model_path="org/reward-model", output_dir=tmp_path)
    trainer = _stub_trainer(config, dataset={"train": list(range(4))}, tmp_path=tmp_path)

    mock_rw_model = MagicMock()
    mock_rw_model.device = "cpu"
    mock_rw_model.return_value.logits = _torch.tensor([[0.7], [0.2]])

    mock_rw_tok = MagicMock()
    mock_rw_tok.return_value = {
        "input_ids": _torch.zeros((2, 8), dtype=_torch.long),
        "attention_mask": _torch.ones((2, 8), dtype=_torch.long),
    }

    captured: dict = {}

    def fake_grpo_trainer(**kwargs):
        captured.update(kwargs)
        return MagicMock()

    with (
        patch("trl.GRPOTrainer", side_effect=fake_grpo_trainer),
        patch("trl.GRPOConfig", return_value=MagicMock()),
        patch(
            "transformers.AutoModelForSequenceClassification.from_pretrained",
            return_value=mock_rw_model,
        ),
        patch("transformers.AutoTokenizer.from_pretrained", return_value=mock_rw_tok),
    ):
        trainer._build_trainer(callbacks=[])

    reward_funcs = captured.get("reward_funcs")
    assert reward_funcs is not None
    assert callable(reward_funcs[0])

    # The closure must reference the loaded reward model — calling it should
    # invoke our mock_rw_model exactly once.
    out = reward_funcs[0](["hello", "world"])
    assert isinstance(out, list)
    assert len(out) == 2
    assert mock_rw_model.called, "classifier-backed reward closure must call the loaded reward model"
