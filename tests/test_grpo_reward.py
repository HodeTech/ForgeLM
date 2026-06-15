"""Regression tests for GRPO reward model callable wrapping.

Verifies that when grpo_reward_model is set, _build_trainer constructs a
Python callable (not a plain string) and that the callable has the expected
interface: accepts a list of strings, returns a list of floats.
"""

from unittest.mock import MagicMock, patch

import pytest

# ForgeTrainer requires torch — skip all tests if not available
torch_available = True
try:
    import torch  # noqa: F401
except ImportError:
    torch_available = False

# These tests patch ``trl.GRPOTrainer``; mock.patch resolves the original
# attribute via ``getattr`` before swapping. Newer ``trl`` releases lazy-load
# the GRPO module, and the lazy import fails on torch versions that don't
# expose ``torch.distributed.fsdp.FSDPModule`` (a moving target across
# torch <-> trl pairings). When that happens the patch itself raises
# AttributeError before our test code even runs — skip cleanly so nightly
# matrix runs against bleeding-edge deps don't false-fail.
grpo_patchable = False
if torch_available:
    try:
        import trl  # noqa: F401

        trl.GRPOTrainer  # noqa: B018 — touch attr to trigger trl's lazy loader
        grpo_patchable = True
    except (ImportError, AttributeError, RuntimeError):
        grpo_patchable = False


def _make_grpo_config(reward_model_path, output_dir=None):
    """Build a minimal ForgeConfig with grpo trainer and a reward model path."""
    from forgelm.config import ForgeConfig

    data = {
        "model": {"name_or_path": "org/model"},
        "lora": {},
        "training": {
            "trainer_type": "grpo",
            "grpo_reward_model": reward_model_path,
            "output_dir": output_dir or "./checkpoints",
        },
        "data": {"dataset_name_or_path": "org/dataset"},
    }
    return ForgeConfig(**data)


@pytest.mark.skipif(not torch_available, reason="torch not installed")
@pytest.mark.skipif(
    not grpo_patchable,
    reason="trl.GRPOTrainer not importable in this environment (torch/trl version mismatch)",
)
class TestGrpoRewardCallable:
    def test_reward_funcs_is_callable_list(self, tmp_path):
        """When grpo_reward_model is configured, reward_funcs must be a list of callables."""
        from forgelm.trainer import ForgeTrainer

        config = _make_grpo_config("org/reward-model", output_dir=str(tmp_path))

        model = MagicMock()
        tokenizer = MagicMock()
        dataset = {"train": list(range(10))}

        # Build trainer skeleton without heavy init
        trainer = ForgeTrainer.__new__(ForgeTrainer)
        trainer.model = model
        trainer.tokenizer = tokenizer
        trainer.config = config
        trainer.dataset = dataset
        trainer.checkpoint_dir = str(tmp_path)
        trainer.run_name = "grpo_test"
        trainer.notifier = MagicMock()
        trainer.audit = MagicMock()

        # Mock out the heavy GRPO Trainer construction
        mock_grpo_trainer = MagicMock()
        captured_kwargs = {}

        def fake_grpo_trainer(**kwargs):
            captured_kwargs.update(kwargs)
            return mock_grpo_trainer

        mock_rw_model = MagicMock()
        mock_rw_model.device = "cpu"
        # Return logits shaped (N, 1)
        import torch

        mock_rw_model.return_value.logits = torch.tensor([[0.9], [0.2], [0.7]])

        mock_rw_tokenizer = MagicMock()
        mock_rw_tokenizer.return_value = {
            "input_ids": torch.zeros((3, 10), dtype=torch.long),
            "attention_mask": torch.ones((3, 10), dtype=torch.long),
        }

        with (
            # Patch in the lazy-import source modules.  ``forgelm/trainer.py``
            # defers ``from trl import SFTTrainer/SFTConfig`` to method bodies
            # (closure-plan F-performance-101) so they are NOT module-level
            # attributes on ``forgelm.trainer`` — patching that path raises
            # ``AttributeError: module 'forgelm.trainer' does not have the
            # attribute 'SFTTrainer'``.  Patch the upstream modules instead;
            # the lazy ``from trl import …`` resolves the patched symbol.
            patch("trl.SFTTrainer"),
            patch("trl.SFTConfig"),
            patch("trl.GRPOTrainer", side_effect=fake_grpo_trainer),
            patch("trl.GRPOConfig", return_value=MagicMock()),
            patch(
                "transformers.AutoModelForSequenceClassification.from_pretrained",
                return_value=mock_rw_model,
            ),
            patch(
                "transformers.AutoTokenizer.from_pretrained",
                return_value=mock_rw_tokenizer,
            ),
        ):
            trainer._build_trainer(callbacks=[])

        reward_funcs = captured_kwargs.get("reward_funcs")
        assert reward_funcs is not None, "reward_funcs was not passed to GRPOTrainer"
        assert isinstance(reward_funcs, list), "reward_funcs should be a list"
        assert len(reward_funcs) == 1, "reward_funcs should have exactly one element"
        assert callable(reward_funcs[0]), "reward_funcs[0] must be callable"

    def test_reward_callable_returns_float_list(self, tmp_path):
        """The reward callable must accept a list of strings and return a list of floats."""
        import torch

        from forgelm.trainer import ForgeTrainer

        config = _make_grpo_config("org/reward-model", output_dir=str(tmp_path))

        trainer = ForgeTrainer.__new__(ForgeTrainer)
        trainer.model = MagicMock()
        trainer.tokenizer = MagicMock()
        trainer.config = config
        trainer.dataset = {"train": list(range(10))}
        trainer.checkpoint_dir = str(tmp_path)
        trainer.run_name = "grpo_test"
        trainer.notifier = MagicMock()
        trainer.audit = MagicMock()

        mock_rw_model = MagicMock()
        mock_rw_model.device = "cpu"
        # Simulate reward model returning logits for 2 completions
        fake_logits = torch.tensor([[1.5], [-0.3]])
        mock_rw_model.return_value.logits = fake_logits

        mock_rw_tokenizer = MagicMock()
        # Tokenizer returns a dict-like object with .items()
        fake_encoded = {
            "input_ids": torch.zeros((2, 10), dtype=torch.long),
            "attention_mask": torch.ones((2, 10), dtype=torch.long),
        }
        mock_rw_tokenizer.return_value = fake_encoded

        captured_kwargs = {}

        def fake_grpo_trainer(**kwargs):
            captured_kwargs.update(kwargs)
            return MagicMock()

        with (
            patch("trl.GRPOTrainer", side_effect=fake_grpo_trainer),
            patch("trl.GRPOConfig", return_value=MagicMock()),
            patch(
                "transformers.AutoModelForSequenceClassification.from_pretrained",
                return_value=mock_rw_model,
            ),
            patch(
                "transformers.AutoTokenizer.from_pretrained",
                return_value=mock_rw_tokenizer,
            ),
        ):
            trainer._build_trainer(callbacks=[])

        reward_fn = captured_kwargs["reward_funcs"][0]

        completions = ["Hello world", "This is a test"]
        result = reward_fn(completions)

        assert isinstance(result, list), "reward callable must return a list"
        assert len(result) == 2, "result length must match number of completions"
        assert all(isinstance(v, float) for v in result), "all reward values must be floats"

    def test_no_reward_model_no_gold_answers_uses_combined_fallback(self, tmp_path):
        """No reward model AND no gold_answer → fallback is combined_format_length_reward only."""
        from forgelm.config import ForgeConfig
        from forgelm.grpo_rewards import combined_format_length_reward
        from forgelm.trainer import ForgeTrainer

        config_data = {
            "model": {"name_or_path": "org/model"},
            "lora": {},
            "training": {
                "trainer_type": "grpo",
                "output_dir": str(tmp_path),
            },
            "data": {"dataset_name_or_path": "org/dataset"},
        }
        config = ForgeConfig(**config_data)

        trainer = ForgeTrainer.__new__(ForgeTrainer)
        trainer.model = MagicMock()
        trainer.tokenizer = MagicMock()
        trainer.config = config
        # Plain ints — no `gold_answer` field anywhere.
        trainer.dataset = {"train": list(range(10))}
        trainer.checkpoint_dir = str(tmp_path)
        trainer.run_name = "grpo_no_reward"
        trainer.notifier = MagicMock()
        trainer.audit = MagicMock()

        captured_kwargs: dict = {}

        def fake_grpo_trainer(**kwargs):
            captured_kwargs.update(kwargs)
            return MagicMock()

        with (
            patch("trl.GRPOTrainer", side_effect=fake_grpo_trainer),
            patch("trl.GRPOConfig", return_value=MagicMock()),
        ):
            trainer._build_trainer(callbacks=[])

        reward_funcs = captured_kwargs.get("reward_funcs")
        assert reward_funcs == [combined_format_length_reward], (
            "Without reward model and without gold_answer, only the combined "
            "format+length shaping reward should be wired (no correctness signal available)."
        )

    def test_gold_answer_dataset_wires_combined_plus_math_reward(self, tmp_path):
        """Dataset with gold_answer + no reward model → combined fallback PLUS math reward (additive)."""
        from forgelm.config import ForgeConfig
        from forgelm.grpo_rewards import combined_format_length_reward
        from forgelm.trainer import ForgeTrainer, _math_reward_fn

        config_data = {
            "model": {"name_or_path": "org/model"},
            "lora": {},
            "training": {
                "trainer_type": "grpo",
                "output_dir": str(tmp_path),
            },
            "data": {"dataset_name_or_path": "org/dataset"},
        }
        config = ForgeConfig(**config_data)

        trainer = ForgeTrainer.__new__(ForgeTrainer)
        trainer.model = MagicMock()
        trainer.tokenizer = MagicMock()
        trainer.config = config
        trainer.dataset = {
            "train": [
                {"prompt": "What is 2+2?", "gold_answer": "4"},
                {"prompt": "What is 3+5?", "gold_answer": "8"},
            ]
        }
        trainer.checkpoint_dir = str(tmp_path)
        trainer.run_name = "grpo_math_reward"
        trainer.notifier = MagicMock()
        trainer.audit = MagicMock()

        captured_kwargs: dict = {}

        def fake_grpo_trainer(**kwargs):
            captured_kwargs.update(kwargs)
            return MagicMock()

        with (
            patch("trl.GRPOTrainer", side_effect=fake_grpo_trainer),
            patch("trl.GRPOConfig", return_value=MagicMock()),
        ):
            trainer._build_trainer(callbacks=[])

        reward_funcs = captured_kwargs.get("reward_funcs")
        assert reward_funcs == [combined_format_length_reward, _math_reward_fn], (
            "When gold_answer is present and no reward model is configured, both the "
            "combined format+length reward (always-on baseline) AND the gold_answer "
            "correctness reward must be wired additively."
        )


@pytest.mark.skipif(not torch_available, reason="ForgeTrainer requires torch")
class TestGrpoClassifierTrustRemoteCode:
    """Phase 7 (M-202): the GRPO classifier reward must load HF artifacts with
    ``trust_remote_code=False``.

    A reward model is operator-supplied configuration, but we should not
    silently execute arbitrary repo code at load time — a malicious or
    typo-grabbed Hub repo could otherwise run code in the trainer process
    on every GRPO step setup.
    """

    def test_classifier_reward_passes_trust_remote_code_false(self):
        """Both AutoTokenizer.from_pretrained and AutoModelForSequenceClassification.from_pretrained
        in ``ForgeTrainer._build_classifier_reward`` must pass ``trust_remote_code=False``."""
        import inspect
        import re

        from forgelm.trainer import ForgeTrainer

        src = inspect.getsource(ForgeTrainer._build_classifier_reward)
        # Strip docstring + comments so "trust_remote_code=False" hits in
        # those don't false-positive the assertion.
        code_only = re.sub(r"#[^\n]*", "", src)
        code_only = re.sub(r'""".*?"""', "", code_only, flags=re.DOTALL)

        # Two from_pretrained calls; both must carry trust_remote_code=False.
        assert code_only.count("from_pretrained") == 2, (
            "Test guard: expected exactly two from_pretrained calls in "
            "_build_classifier_reward. If the implementation grew, update this test."
        )
        assert code_only.count("trust_remote_code=False") == 2, (
            "Both from_pretrained calls in _build_classifier_reward must pass "
            "trust_remote_code=False (M-202). Found:\n" + code_only
        )


# ---------------------------------------------------------------------------
# F-L-15: ANSWER_EXTRACT_PATTERN same-line multi-marker trailing-whitespace
# ---------------------------------------------------------------------------


class TestAnswerExtractPatternSameLineMultiMarker:
    """Regression suite for F-L-15.

    When multiple ``Answer:`` markers appear on the same line the body of
    non-last captures may include trailing whitespace.  ``_normalize_answer``
    strips it at comparison time so rewards are always correct.  These tests
    lock both the raw-capture behaviour and the normalised result.
    """

    def test_non_last_capture_may_have_trailing_space(self):
        """First match in 'Answer: 5 Answer: 7' has a trailing space in group(1).

        This is the documented behaviour (F-L-15): the ``(?!answer\\s*:)`` guard
        fires at the 'A' of the second marker, leaving the preceding space
        inside the first capture body.  Reverting the comment-only fix should
        not break this test; reverting normalization would.
        """
        from forgelm.grpo_rewards import ANSWER_EXTRACT_PATTERN

        matches = list(ANSWER_EXTRACT_PATTERN.finditer("Answer: 5 Answer: 7"))
        assert len(matches) == 2, "Expected exactly two markers on the same line"
        # Raw group(1) for the first (non-last) match includes trailing space.
        assert matches[0].group(1) == "5 ", f"Expected '5 ' (with trailing space), got {matches[0].group(1)!r}"
        # Last match is clean.
        assert matches[-1].group(1) == "7"

    def test_normalize_answer_strips_trailing_space_from_non_last_capture(self):
        """_normalize_answer('5 ') == '5': trailing space is stripped so reward is correct."""
        from forgelm.trainer import _normalize_answer

        assert _normalize_answer("5 ") == "5"
        assert _normalize_answer("7") == "7"

    def test_format_and_math_reward_agree_on_same_line_final_answer(self):
        """Cross-signal consistency for same-line multi-marker case (F-L-15).

        Mirrors ``test_reward_and_format_gate_agree_on_final_answer`` in
        test_grpo_math_reward.py but exercises the same-line path (no newline
        between the two markers).

        format_match_reward uses the end-anchored ``_ANSWER_END_PATTERN`` which
        sees only the trailing ``Answer: 7`` and returns 1.0.
        _math_reward_fn uses ``ANSWER_EXTRACT_PATTERN`` finditer → [-1], also
        grading ``7``.  Both must agree.
        """
        from forgelm.grpo_rewards import format_match_reward
        from forgelm.trainer import _math_reward_fn

        completion = "Answer: 5 Answer: 7"
        # Format gate grabs the end-anchored final Answer: 7 → 1.0.
        assert format_match_reward([completion]) == [1.0]
        # Math reward grades the last extracted answer (7) against gold "7" → 1.0.
        assert _math_reward_fn([completion], gold_answer=["7"]) == [1.0]
        # Sanity: if gold is the wrong (first) answer, math reward must be 0.0.
        assert _math_reward_fn([completion], gold_answer=["5"]) == [0.0]


# ---------------------------------------------------------------------------
# F-L-16: format_match_reward accepts Answer:\n<value>
# ---------------------------------------------------------------------------


class TestFormatMatchRewardNewlineTolerance:
    """Regression suite for F-L-16.

    ``_ANSWER_END_PATTERN`` uses ``\\s*`` after the colon, and ``\\s`` includes
    ``\\n``, so ``Answer:\\n42`` is accepted.  The docstring now documents this.
    """

    def test_newline_between_colon_and_value_scores_one(self):
        """'Answer:\\n42' must return 1.0 — newline after colon is tolerated."""
        from forgelm.grpo_rewards import format_match_reward

        assert format_match_reward(["Answer:\n42"]) == [1.0]

    def test_standard_form_still_scores_one(self):
        """Regression guard: 'Answer: 42' (no newline) must still return 1.0."""
        from forgelm.grpo_rewards import format_match_reward

        assert format_match_reward(["Answer: 42"]) == [1.0]

    def test_missing_value_after_newline_scores_zero(self):
        """'Answer:\\n' with no value must return 0.0 (\\S requires at least one char)."""
        from forgelm.grpo_rewards import format_match_reward

        assert format_match_reward(["Answer:\n"]) == [0.0]
