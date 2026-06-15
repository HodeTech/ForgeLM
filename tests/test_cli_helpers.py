"""Unit tests for small CLI helper extractions in :mod:`forgelm.cli`.

Covers :func:`forgelm.cli._build_quickstart_inherited_flags` (Phase 10.5
nitpick: argv duplication between train and chat subprocess invocations)
and the module-level GRPO answer regex hoist in :mod:`forgelm.trainer`.
"""

from __future__ import annotations

import argparse
import re

from forgelm.cli import _build_quickstart_inherited_flags
from forgelm.grpo_rewards import ANSWER_EXTRACT_PATTERN
from forgelm.trainer import _math_reward_fn


def _ns(**kwargs) -> argparse.Namespace:
    return argparse.Namespace(**kwargs)


def test_inherited_flags_default_args_returns_empty_lists() -> None:
    args = _ns()
    train_flags, chat_flags = _build_quickstart_inherited_flags(args)
    assert train_flags == []
    assert chat_flags == []


def test_inherited_flags_propagates_quiet_to_both() -> None:
    args = _ns(quiet=True)
    train_flags, chat_flags = _build_quickstart_inherited_flags(args)
    assert "--quiet" in train_flags
    assert "--quiet" in chat_flags


def test_inherited_flags_propagates_log_level_to_both() -> None:
    args = _ns(log_level="DEBUG")
    train_flags, chat_flags = _build_quickstart_inherited_flags(args)
    # Assert the flag/value pair appears contiguously in both lists.
    assert "--log-level" in train_flags
    assert train_flags[train_flags.index("--log-level") + 1] == "DEBUG"
    assert "--log-level" in chat_flags
    assert chat_flags[chat_flags.index("--log-level") + 1] == "DEBUG"


def test_inherited_flags_propagates_offline_to_both() -> None:
    args = _ns(offline=True)
    train_flags, chat_flags = _build_quickstart_inherited_flags(args)
    assert "--offline" in train_flags
    assert "--offline" in chat_flags


def test_inherited_flags_output_format_not_forwarded() -> None:
    # --output-format json must never be forwarded to the training subprocess.
    # The quickstart parent owns the JSON envelope; forwarding would produce two
    # top-level JSON objects on stdout, making the stream unparseable.
    args = _ns(output_format="json")
    train_flags, chat_flags = _build_quickstart_inherited_flags(args)
    assert "--output-format" not in train_flags
    assert "json" not in train_flags
    assert "--output-format" not in chat_flags
    assert "json" not in chat_flags


def test_inherited_flags_combined() -> None:
    args = _ns(
        quiet=True,
        log_level="INFO",
        offline=True,
        output_format="json",
    )
    train_flags, chat_flags = _build_quickstart_inherited_flags(args)
    assert train_flags == ["--quiet", "--log-level", "INFO", "--offline"]
    assert chat_flags == ["--quiet", "--log-level", "INFO", "--offline"]


def test_math_reward_uses_module_level_pattern() -> None:
    """The shared extraction constant must be a compiled regex with the I flag.

    The pattern is the single source of truth in :mod:`forgelm.grpo_rewards`
    (``ANSWER_EXTRACT_PATTERN``); ``forgelm.trainer._math_reward_fn`` imports
    and applies it so the correctness reward and the format gate cannot drift.
    """
    assert isinstance(ANSWER_EXTRACT_PATTERN, re.Pattern)
    assert ANSWER_EXTRACT_PATTERN.flags & re.IGNORECASE
    rewards = _math_reward_fn(["Answer: 7"], gold_answer=["7"])
    assert rewards == [1.0]
