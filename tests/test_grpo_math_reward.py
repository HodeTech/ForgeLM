"""Unit tests for the built-in GRPO math reward helpers.

These cover the pure-Python regex / normalization layer in ``forgelm.trainer``:

- ``_normalize_answer`` — strips units, punctuation, whitespace
- ``_answers_match`` — exact-string + numeric-tolerance comparison
- ``_math_reward_fn`` — the TRL-shaped callable that scores GRPO completions
- ``_dataset_has_gold_answers`` — probe used by the trainer wiring

The tests intentionally avoid importing torch / trl: the functions under test
are pure Python and must remain so (so the trainer can pass them to TRL's
GRPOTrainer across worker processes without pickling extra state).
"""

from __future__ import annotations

import pytest

from forgelm.trainer import (
    _answers_match,
    _dataset_has_gold_answers,
    _math_reward_fn,
    _normalize_answer,
)

# ---------------------------------------------------------------------------
# _normalize_answer
# ---------------------------------------------------------------------------


class TestNormalizeAnswer:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("15", "15"),
            ("  15  ", "15"),
            ("$15", "15"),
            ("15.", "15"),
            ("15%", "15"),
            ("40 m²", "40"),
            ("70 km/h", "70"),
            ("2 m/s", "2"),
            ("1500 mL", "1500"),
            ("150 liters", "150"),
            ("10 hours", "10"),
            ("9 cm", "9"),
            ("45 kg", "45"),
            ("12:15", "12:15"),  # time format passed through
            ("2/5", "2/5"),  # fraction format passed through
        ],
    )
    def test_strips_units_and_punctuation(self, raw, expected):
        assert _normalize_answer(raw) == expected

    def test_handles_none(self):
        assert _normalize_answer(None) == ""

    def test_handles_empty(self):
        assert _normalize_answer("") == ""

    def test_strips_compound_unit_first(self):
        # km/h must be stripped before km — otherwise "70 km/h" would leave "/h".
        assert _normalize_answer("70 km/h") == "70"

    def test_strip_tokens_order_container_before_contained(self):
        """Structural guard for the ``_REWARD_STRIP_TOKENS`` ordering invariant.

        ``_normalize_answer`` strips the first matching token, so any token that
        *contains* a shorter sibling at a boundary (e.g. "km/h" contains "km",
        "mL" contains "m") must be listed BEFORE that sibling — otherwise the
        contained token strips first and corrupts the answer (the "/h" failure
        that ``test_strips_compound_unit_first`` pins for the single km/h case).
        The example test only guards km/h; this covers every container/contained
        pair so a future insertion (e.g. "m" before "mL") fails here instead of
        silently regressing a unit that lacks its own example.
        """
        from forgelm.trainer import _REWARD_STRIP_TOKENS

        tokens = _REWARD_STRIP_TOKENS
        for i, contained in enumerate(tokens):
            for j, container in enumerate(tokens):
                if container == contained:
                    continue
                if container.endswith(contained) or container.startswith(contained):
                    assert j < i, (
                        f"_REWARD_STRIP_TOKENS ordering violation: container "
                        f"{container!r} (index {j}) must precede contained token "
                        f"{contained!r} (index {i}) so the compound unit strips first"
                    )

    @pytest.mark.parametrize(
        "raw,expected",
        [
            # Single-letter "m" only stripped at a digit/space boundary.
            ("them", "them"),  # alpha-prev → don't strip
            ("method", "method"),  # alpha-next → don't strip
            ("5 m", "5"),  # space-prev → strip
            ("5m", "5"),  # digit-prev → strip
            ("m 5", "5"),  # space-next → strip prefix
            ("m5", "5"),  # digit-next → strip prefix
            ("metric ton", "metric ton"),  # alpha-next → don't strip
        ],
    )
    def test_single_letter_m_requires_boundary(self, raw, expected):
        assert _normalize_answer(raw) == expected


# ---------------------------------------------------------------------------
# _answers_match
# ---------------------------------------------------------------------------


class TestAnswersMatch:
    def test_exact_string_match(self):
        assert _answers_match("12:15", "12:15") is True

    def test_fraction_string_match(self):
        assert _answers_match("2/5", "2/5") is True

    def test_numeric_tolerance(self):
        assert _answers_match("1.5", "1.5000001") is True

    def test_numeric_inequality(self):
        assert _answers_match("15", "16") is False

    def test_numeric_string_normalized(self):
        # "15.0" and "15" represent the same number.
        assert _answers_match("15.0", "15") is True

    def test_non_numeric_mismatch(self):
        assert _answers_match("12:15", "13:00") is False

    def test_extracted_number_matches_gold_string(self):
        assert _answers_match("40", "40") is True

    def test_both_empty_does_not_match(self):
        # F-P2-FAB-32: two values that both normalize to "" (e.g. "$" and "%")
        # must NOT count as a match — empty-after-normalization is never an answer.
        assert _answers_match("", "") is False

    def test_one_side_empty_does_not_match(self):
        # A per-row gold hole that slips past _dataset_has_gold_answers must not
        # spuriously match a non-empty extraction.
        assert _answers_match("", "5") is False
        assert _answers_match("5", "") is False

    @pytest.mark.parametrize(
        "extracted,gold,expected",
        [
            ("5,050", "5050", True),  # F-P3-FABLE-51: GSM8K comma grouping
            ("1,234.5", "1234.5", True),  # grouped with a decimal tail
            ("12,5", "1234", False),  # European decimal — NOT de-grouped
            ("12,34", "1234", False),  # malformed grouping stays unequal
        ],
    )
    def test_comma_thousands_separator(self, extracted, gold, expected):
        assert _answers_match(extracted, gold) is expected


# ---------------------------------------------------------------------------
# _math_reward_fn
# ---------------------------------------------------------------------------


class TestMathRewardFn:
    def test_correct_answer_scores_one(self):
        completions = ["Step 1: 12-3-2 = 7. Answer: 7"]
        rewards = _math_reward_fn(completions, gold_answer=["7"])
        assert rewards == [1.0]

    def test_wrong_answer_scores_zero(self):
        completions = ["Step 1: I think the answer is 8. Answer: 8"]
        rewards = _math_reward_fn(completions, gold_answer=["7"])
        assert rewards == [0.0]

    def test_answer_with_unit_matches(self):
        # "$15" should normalize to "15" and match gold "15".
        completions = ["Cost is base + km*rate = 3 + 12 = 15. Answer: $15"]
        rewards = _math_reward_fn(completions, gold_answer=["15"])
        assert rewards == [1.0]

    def test_fraction_answer_matches(self):
        completions = ["P = 4/(4+6) = 4/10. Answer: 2/5"]
        rewards = _math_reward_fn(completions, gold_answer=["2/5"])
        assert rewards == [1.0]

    def test_time_answer_matches(self):
        completions = ["9:30 + 2:45 = 12:15. Answer: 12:15"]
        rewards = _math_reward_fn(completions, gold_answer=["12:15"])
        assert rewards == [1.0]

    def test_float_tolerance_accepts_close_value(self):
        completions = ["Answer: 2.0000000001"]
        rewards = _math_reward_fn(completions, gold_answer=["2"])
        assert rewards == [1.0]

    def test_missing_answer_marker_scores_zero(self):
        # No "Answer:" prefix anywhere in the completion.
        completions = ["The result is seven."]
        rewards = _math_reward_fn(completions, gold_answer=["7"])
        assert rewards == [0.0]

    def test_case_insensitive_marker(self):
        completions = ["working...\nANSWER: 7"]
        rewards = _math_reward_fn(completions, gold_answer=["7"])
        assert rewards == [1.0]

    def test_multiple_completions(self):
        completions = ["Answer: 7", "Answer: 8", "no marker here"]
        rewards = _math_reward_fn(completions, gold_answer=["7", "8", "9"])
        assert rewards == [1.0, 1.0, 0.0]

    def test_empty_completion_scores_zero(self):
        rewards = _math_reward_fn([""], gold_answer=["7"])
        assert rewards == [0.0]

    def test_none_completion_scores_zero(self):
        # Defensive: a None slot in the batch must not crash the reward fn.
        rewards = _math_reward_fn([None], gold_answer=["7"])
        assert rewards == [0.0]

    def test_returns_floats(self):
        rewards = _math_reward_fn(["Answer: 7"], gold_answer=["7"])
        assert all(isinstance(r, float) for r in rewards)

    def test_missing_gold_answer_kwarg_returns_zeros(self):
        """No gold_answer column → all-zero rewards (defensive; should not happen in practice)."""
        rewards = _math_reward_fn(["Answer: 7", "Answer: 8"])
        assert rewards == [0.0, 0.0]

    def test_missing_gold_answer_kwarg_warns_once(self, caplog):
        """The golds-None fallback must surface a single WARNING so an
        inert-but-wired correctness reward is visible in the run log, instead of
        silently contributing 0.0 every batch (F-P3-FABLE-50)."""
        import logging

        # The warn-once flag is a function attribute; reset it so this test is
        # order-independent and the assertion sees the first-call WARNING.
        if hasattr(_math_reward_fn, "_warned_no_golds"):
            del _math_reward_fn._warned_no_golds
        try:
            with caplog.at_level(logging.WARNING, logger="forgelm.trainer"):
                _math_reward_fn(["Answer: 7"])
                _math_reward_fn(["Answer: 8"])
            warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "gold_answer" in r.getMessage()]
            assert len(warnings) == 1, "expected exactly one warn-once record across repeated calls"
        finally:
            if hasattr(_math_reward_fn, "_warned_no_golds"):
                del _math_reward_fn._warned_no_golds

    def test_mismatched_lengths_raises(self):
        """Wiring regression: completions and gold_answer must have the same length."""
        with pytest.raises(ValueError, match="zip"):
            _math_reward_fn(["Answer: 7", "Answer: 8"], gold_answer=["7"])

    @pytest.mark.parametrize(
        "gold,completion,expected",
        [
            (0, "Answer: 0", 1.0),  # int zero — must match
            (0.0, "Answer: 0", 1.0),  # float zero
            (False, "Answer: False", 1.0),  # bool stringified
            (42, "Answer: 42", 1.0),  # plain int
            (3.14, "Answer: 3.14", 1.0),  # plain float
        ],
    )
    def test_non_string_gold_answer_does_not_crash(self, gold, completion, expected):
        """gold_answer values may be int/float/bool when carried by HF Datasets;
        the reward fn must stringify them rather than crashing on .strip()."""
        rewards = _math_reward_fn([completion], gold_answer=[gold])
        assert rewards == [expected]

    def test_reward_extraction_ignores_trailing_prose(self):
        """Trailing prose after the answer must not poison the comparison.

        Models commonly produce mid-sentence answers like
        "Answer: 18. Bu doğru." A greedy regex would capture
        "18. Bu doğru" → fail to normalize → reward 0.0 even though the
        numeric answer is correct. The anchored regex must stop at the
        sentence boundary.
        """
        completions = [
            "The work is 30-12=18. Answer: 18. Bu doğru.",
            "Answer: 18",
        ]
        rewards = _math_reward_fn(completions, gold_answer=["18", "18"])
        assert rewards == [1.0, 1.0]

    def test_reward_extraction_handles_units_with_trailing_prose(self):
        """Units inside the captured value must survive, prose must not."""
        completions = ["Computing: 70 km/h. Answer: 70 km/h. Final."]
        rewards = _math_reward_fn(completions, gold_answer=["70"])
        assert rewards == [1.0]

    def test_reward_extraction_handles_punctuation_after_value(self):
        """A trailing exclamation/question mark must not be captured."""
        completions = ["Answer: 7!"]
        rewards = _math_reward_fn(completions, gold_answer=["7"])
        assert rewards == [1.0]

    def test_reward_extraction_preserves_decimal_values(self):
        """Decimal values must NOT be split at the internal period.

        Regression: an earlier sentence-boundary regex treated every "." as a
        boundary, so "Answer: 1.5" captured "1" and mismatched gold "1.5".
        The fix uses a punctuation-followed-by-whitespace lookahead so bare
        periods between digits stay inside the capture.
        """
        completions = [
            "Answer: 1.5",
            "Reasoning here. Answer: 1.5. Bu doğru.",
            "Answer: 3.14159",
        ]
        rewards = _math_reward_fn(completions, gold_answer=["1.5", "1.5", "3.14159"])
        assert rewards == [1.0, 1.0, 1.0]

    def test_reward_grades_last_answer_occurrence_correct_final(self):
        """A self-correcting completion is graded on its FINAL answer.

        Regression (F-P2-FAB-06 / F-P3-FABLE-27): the old leftmost ``.search``
        graded the FIRST ``Answer:`` marker while the format reward is
        end-anchored. A completion that proposes then revises
        ("Answer: 5 … Answer: 7") must be graded against its final answer (7),
        not the discarded candidate (5).
        """
        completion = "Answer: 5.\nWait, I made an error. Answer: 7"
        # Gold is the final answer → reward 1.0.
        assert _math_reward_fn([completion], gold_answer=["7"]) == [1.0]
        # Gold is the discarded earlier candidate → reward 0.0 (no longer a
        # reward-hack: mentioning the gold in an early clause must not score).
        assert _math_reward_fn([completion], gold_answer=["5"]) == [0.0]

    def test_reward_last_occurrence_with_trailing_prose(self):
        """The last marker is graded even when trailing prose follows it."""
        completion = "Candidate Answer: 50, which is wrong. Answer: 42. Done."
        assert _math_reward_fn([completion], gold_answer=["42"]) == [1.0]
        assert _math_reward_fn([completion], gold_answer=["50"]) == [0.0]

    def test_reward_leading_dot_decimal_extracted(self):
        """A bare-dot decimal "Answer: .5" earns correctness reward.

        Regression (F-P2-FAB-31): the extraction pattern's first-char class
        excluded "." so ".5" didn't match at all → reward 0.0, while the format
        gate's ``\\S`` start accepted it → 1.0 (asymmetric reward). The leading
        ``\\.(?=\\d)`` alternative now admits the decimal so both signals agree.
        """
        assert _math_reward_fn(["Answer: .5"], gold_answer=["0.5"]) == [1.0]
        assert _math_reward_fn(["Answer: .5"], gold_answer=[".5"]) == [1.0]
        # A lone "." (not followed by a digit) must still not match.
        assert _math_reward_fn(["Answer: ."], gold_answer=["0"]) == [0.0]

    def test_reward_comma_thousands_separator_matches(self):
        """GSM8K comma-grouped large numbers match comma-free golds.

        Regression (F-P3-FABLE-51): "Answer: 5,050" against gold "5050" scored
        0.0 because float("5,050") raised. The grouped-number de-grouping in
        ``_parse_number`` now matches the canonical GSM8K rendering.
        """
        completions = ["The sum is 5050. Answer: 5,050"]
        assert _math_reward_fn(completions, gold_answer=["5050"]) == [1.0]

    def test_reward_unit_only_completion_does_not_match(self):
        """A degenerate unit-only completion never scores against a unit-only gold.

        Regression (F-P2-FAB-32): "Answer: $" normalizes to "" and would have
        matched gold "%" (also "") → false 1.0.
        """
        assert _math_reward_fn(["Answer: $"], gold_answer=["%"]) == [0.0]

    def test_reward_and_format_gate_agree_on_final_answer(self):
        """Cross-module consistency: correctness and format rewards grade the
        same (final) answer for a self-correcting completion.

        Both signals are summed by TRL; if they disagreed about which answer a
        completion gives, a completion could earn full format reward on its
        final answer while the correctness reward credited an earlier one. This
        pins that they now agree on the end-anchored final answer.
        """
        from forgelm.grpo_rewards import format_match_reward

        completion = "Answer: 5.\nWait. Answer: 7"
        # Format gate: end-anchored → matches the final "Answer: 7" → 1.0.
        assert format_match_reward([completion]) == [1.0]
        # Correctness against the actual final answer → 1.0 (agrees with gate).
        assert _math_reward_fn([completion], gold_answer=["7"]) == [1.0]

    def test_extract_pattern_linear_on_pathological_input(self):
        """The leading-dot alternative adds no ReDoS surface (regex.md budget).

        ``\\.(?=\\d)`` is a fixed single-char lookahead, not a quantifier, so
        scaling stays linear. Measure the median search time at growing input
        sizes; doubling the input must not super-linearly blow up.
        """
        import re as _re
        import time

        from forgelm.grpo_rewards import ANSWER_EXTRACT_PATTERN

        def _median_ms(n: int) -> float:
            # No "Answer:" → worst case: engine scans the whole non-matching line.
            payload = "Answer:" + ("." * n)
            samples = []
            for _ in range(5):
                t0 = time.perf_counter()
                ANSWER_EXTRACT_PATTERN.search(payload)
                samples.append((time.perf_counter() - t0) * 1000)
            return sorted(samples)[2]

        # Safety floor: linear-time scanning stays well under 100 ms at 10K.
        assert _median_ms(10_000) < 100.0
        assert isinstance(ANSWER_EXTRACT_PATTERN, _re.Pattern)


# ---------------------------------------------------------------------------
# _dataset_has_gold_answers
# ---------------------------------------------------------------------------


class TestDatasetHasGoldAnswers:
    def test_dict_rows_with_gold_answer(self):
        ds = {"train": [{"prompt": "x", "gold_answer": "5"}]}
        assert _dataset_has_gold_answers(ds) is True

    def test_dict_rows_without_gold_answer(self):
        ds = {"train": [{"prompt": "x"}]}
        assert _dataset_has_gold_answers(ds) is False

    def test_empty_gold_answer_treated_as_missing(self):
        # "" is treated as "schema placeholder, no real label" — same as
        # missing — so the trainer doesn't try to score against an empty target.
        ds = {"train": [{"prompt": "x", "gold_answer": ""}]}
        assert _dataset_has_gold_answers(ds) is False

    def test_none_gold_answer_treated_as_missing(self):
        ds = {"train": [{"prompt": "x", "gold_answer": None}]}
        assert _dataset_has_gold_answers(ds) is False

    def test_zero_int_gold_answer_treated_as_present(self):
        # "0" is a perfectly valid math answer (e.g., "What is 5 - 5?"). Earlier
        # presence check used bool(...) which would falsely drop integer zero.
        ds = {"train": [{"prompt": "x", "gold_answer": 0}]}
        assert _dataset_has_gold_answers(ds) is True

    def test_zero_float_gold_answer_treated_as_present(self):
        ds = {"train": [{"prompt": "x", "gold_answer": 0.0}]}
        assert _dataset_has_gold_answers(ds) is True

    def test_false_gold_answer_treated_as_present(self):
        # Boolean labels are an unusual but legal shape; presence wins.
        ds = {"train": [{"prompt": "x", "gold_answer": False}]}
        assert _dataset_has_gold_answers(ds) is True

    def test_no_train_split(self):
        assert _dataset_has_gold_answers({}) is False

    def test_empty_train_split(self):
        assert _dataset_has_gold_answers({"train": []}) is False

    def test_non_dict_dataset(self):
        assert _dataset_has_gold_answers([]) is False

    def test_hf_dataset_via_column_names(self):
        # Simulate a HuggingFace Dataset that doesn't allow dict-style row
        # access but exposes column_names. Not iterable either → presence-only
        # fallback returns True (F-P2-FAB-33 fallback path).
        class FakeHFDataset:
            def __init__(self, cols):
                self.column_names = cols

            def __len__(self):
                return 1

            def __getitem__(self, _):
                # Force the column_names code path.
                raise IndexError

        ds = {"train": FakeHFDataset(["prompt", "gold_answer"])}
        assert _dataset_has_gold_answers(ds) is True

    def test_iterable_dataset_with_placeholder_gold_treated_as_missing(self):
        # F-P2-FAB-33: a streaming/iterable wrapper whose row access raises but
        # which is iterable must have its first-row VALUE probed — a
        # placeholder-only column (all None) is not real ground truth.
        class FakeIterable:
            def __init__(self, cols, rows):
                self.column_names = cols
                self._rows = rows

            def __len__(self):
                return len(self._rows)

            def __getitem__(self, _):
                raise IndexError

            def __iter__(self):
                return iter(self._rows)

        placeholder = {"train": FakeIterable(["prompt", "gold_answer"], [{"gold_answer": None}])}
        assert _dataset_has_gold_answers(placeholder) is False

    def test_iterable_dataset_with_real_gold_value(self):
        # The same iterable wrapper with a genuine value probes True.
        class FakeIterable:
            def __init__(self, cols, rows):
                self.column_names = cols
                self._rows = rows

            def __len__(self):
                return len(self._rows)

            def __getitem__(self, _):
                raise IndexError

            def __iter__(self):
                return iter(self._rows)

        real = {"train": FakeIterable(["prompt", "gold_answer"], [{"gold_answer": "42"}])}
        assert _dataset_has_gold_answers(real) is True
