"""Built-in GRPO reward functions used when no learned reward model is wired.

These rewards exist so the bundled ``grpo-math`` quickstart (and any other
prompt-only GRPO config that does not declare ``training.grpo_reward_model``)
can train without crashing inside ``trl.GRPOTrainer``, which requires the
``reward_funcs`` argument to be a callable / list of callables / HF model id.

Two complementary signals are provided:

* :func:`format_match_reward` — 1.0 when a completion ends with the literal
  pattern ``Answer: <value>`` (case-insensitive, optional trailing units and
  whitespace). 0.0 otherwise. This is what every prompt in the
  ``grpo-math`` template asks the model to produce.
* :func:`length_shaping_reward` — ``min(len(completion) / 200, 1.0)``. Gives
  the model a non-zero gradient signal during the first few thousand steps
  before format compliance kicks in; clipped at 1.0 so the model can't game
  it by producing arbitrarily long output.

The default :func:`combined_format_length_reward` is the linear combination
``0.8 * format + 0.2 * length`` — format dominates, length keeps the gradient
non-flat early in training.

All functions follow TRL's reward-callable contract: ``(completions: list[str],
**kwargs) -> list[float]``. They are kept at module level (no closures) so
``GRPOTrainer`` can pickle them across worker processes without dragging
trainer state into the spawn.

Reward range
------------

GRPOTrainer accepts a list of reward callables and **sums** their per-completion
outputs. ForgeLM's built-in wiring is additive, so the effective range depends
on whether the dataset carries gold answers:

* **Without ``gold_answer``** — only :func:`combined_format_length_reward` is
  registered, so total per-completion reward ∈ ``[0, 1.0]``.
* **With ``gold_answer``** — :func:`combined_format_length_reward` is paired
  with the math correctness reward (``forgelm.trainer._math_reward_fn``). The
  two are summed by TRL, so total per-completion reward ∈ ``[0, 2.0]``.

**Implication for tuning:** learning rate and KL coefficient (``beta``) tuned
against the 0–1 branch generally need to be revisited for the 0–2 branch — the
advantage magnitudes roughly double, which interacts with KL pull and clip
ranges. If a single training run mixes prompts with and without ``gold_answer``,
consider clamping the combined output or down-weighting the correctness reward
to keep the scale stable.

**Wiring pointers:** ``forgelm.trainer._dataset_has_gold_answers`` decides which
branch is used; ``forgelm.trainer._build_trainer`` (GRPO branch) is where the
reward callables are assembled and passed to ``trl.GRPOTrainer``.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Shared "Answer: <value>" patterns — single source of truth for the two GRPO
# reward signals so their answer-anchoring contracts can never drift again.
#
# There are deliberately TWO patterns with TWO distinct contracts. They are
# kept in this one module (which ``forgelm.trainer`` imports from) precisely so
# a future editor sees both side-by-side and cannot apply the wrong contract:
#
#   * :data:`ANSWER_EXTRACT_PATTERN` — *capturing*, *unanchored*. Used by
#     ``forgelm.trainer._math_reward_fn`` via ``finditer`` to pull the value of
#     the **last** ``Answer:`` marker (matching the end-anchored format gate
#     below). Capture group 1 is the value, sentence-boundary trimmed.
#   * :data:`_ANSWER_END_PATTERN` — *non-capturing*, ``\Z``-anchored. Used by
#     :func:`format_match_reward` as a boolean gate: does the (rstripped)
#     completion **end** with ``Answer: <value>``?
#
# Both must agree on *which* answer they grade — the final one. F-P2-FAB-06 /
# F-P3-FABLE-27: when ``_math_reward_fn`` graded the FIRST marker while the
# format gate scored the LAST, a self-correcting completion ("Answer: 5 …
# Answer: 7") earned format reward on its final answer but correctness reward
# on an earlier (often wrong) one — a reward-hacking surface. The extraction
# pattern is now last-anchored to close that gap.
# ---------------------------------------------------------------------------

# Capturing extraction pattern. Stops the captured value at the next sentence
# boundary so "Answer: 18. Because ..." does NOT swallow the trailing prose into
# the comparison string. The boundary is "[.!?] followed by whitespace OR EOL"
# — a bare "." between digits ("Answer: 1.5") is preserved because a decimal
# "." is not followed by whitespace or end-of-string.
#
# Implementation notes:
#   - First chunk: ``(?:[^\s.!?]|\.(?=\d))(?:(?!answer\s*:)[^.!?\n])*`` — must
#     start with a non-space that isn't sentence punctuation, OR a leading "."
#     immediately followed by a digit (so a bare-dot decimal "Answer: .5"
#     matches and the correctness reward agrees with the format gate's ``\S``
#     start — F-P2-FAB-31). Then any non-newline that isn't sentence
#     punctuation, refusing to cross into a *new* ``answer:`` marker. Covers
#     "18", "70 km/h", "$40", "12:15", "2/5", ".5".
#   - Optional repeats: ``[.!?](?!\s|$)(?:(?!answer\s*:)[^.!?\n])*`` — sentence
#     punctuation is allowed inside the capture *only* when not followed by
#     whitespace/EOL, keeping "1.5" / "3.14159" intact while still stopping at
#     "18. Because ...".
#   - The ``(?!answer\s*:)`` guard on every body character stops a single match
#     from swallowing a later same-line marker ("Answer: 5 Answer: 7"): without
#     it the greedy body absorbs " Answer: 7" and the LAST match still captures
#     "5 Answer: 7", grading the discarded candidate and re-opening the
#     reward-hacking divergence the LAST-marker rule closes (F-P2-FAB-06 /
#     F-P3-FABLE-27). The guard is a fixed-prefix lookahead, not a quantifier,
#     so the body stays linear-time (no ReDoS overlap).
#
# Callers take the LAST match (``finditer`` → ``[-1]``), not the first, so the
# graded answer is the completion's final one — across newlines *and* within a
# single line.
ANSWER_EXTRACT_PATTERN = re.compile(
    # First class drops `\n` because `\s` already covers it. The leading-dot
    # alternative ``\.(?=\d)`` admits ".5"-style decimals (F-P2-FAB-31). The
    # ``(?!answer\s*:)`` guard keeps each body run from crossing into the next
    # ``answer:`` marker so same-line multi-answer output grades the final one.
    r"answer\s*:\s*("
    r"(?:[^\s.!?]|\.(?=\d))"
    r"(?:(?!answer\s*:)[^.!?\n])*"
    r"(?:[.!?](?!\s|$)(?:(?!answer\s*:)[^.!?\n])*)*"
    r")",
    re.IGNORECASE,
)

# End-anchored boolean gate. Matches "Answer:" (any case) followed by at least
# one non-whitespace character followed by any non-newline content, anchored to
# end-of-string with ``\Z``.
#
# ReDoS note: the previous form ``\S[^\n]*?\s*\Z`` mixed a reluctant
# quantifier (``[^\n]*?``) with a tail (``\s*\Z``) whose character class
# overlapped — Python's regex engine backtracked O(n²) on long inputs that
# don't terminate with ``Answer: <value>``. The fix is two-part: callers
# strip trailing whitespace before matching (so the tail collapses to a
# bare ``\Z``), and the body uses a greedy ``[^\n]*`` whose end is fixed
# by ``\Z``. No quantifier overlap → linear-time matching.
#
# Contract: callers MUST ``rstrip()`` the completion before matching (so the
# ``\Z`` tail collapses cleanly). Do NOT swap this pattern in for
# :data:`ANSWER_EXTRACT_PATTERN` — this one is a boolean gate with no capture
# group and end-anchoring; the other extracts a value anywhere in the text.
_ANSWER_END_PATTERN = re.compile(
    r"answer\s*:\s*\S[^\n]*\Z",
    re.IGNORECASE,
)

# Length above which the shaping reward saturates at 1.0. 200 chars is the
# rough length of a single short worked solution in the grpo-math template.
_LENGTH_SATURATION_CHARS = 200

# Weights for the combined reward. Format dominates so the model converges on
# spec-compliant output; length keeps gradient signal non-zero early on.
_FORMAT_WEIGHT = 0.8
_LENGTH_WEIGHT = 0.2


def format_match_reward(completions: list[str], **kwargs) -> list[float]:
    """Return 1.0 per completion that ends with ``Answer: <value>``.

    The match is case-insensitive, allows trailing whitespace, and accepts an
    optional unit / suffix after the value (so ``Answer: 15 km/h`` and
    ``Answer: $40`` both score). A completion that doesn't contain the
    ``Answer:`` token at all, or has the token but no value after it, scores
    0.0.

    ``**kwargs`` is accepted (and ignored) so this matches TRL's per-sample
    column passthrough convention.
    """
    rewards: list[float] = []
    for completion in completions:
        if not completion:
            rewards.append(0.0)
            continue
        # rstrip() collapses the regex tail to a plain ``\Z`` so the
        # engine has no quantifier ambiguity (see _ANSWER_END_PATTERN
        # comment for the ReDoS background).
        rewards.append(1.0 if _ANSWER_END_PATTERN.search(completion.rstrip()) else 0.0)
    return rewards


def length_shaping_reward(completions: list[str], **kwargs) -> list[float]:
    """Return ``min(len(c) / 200, 1.0)`` per completion, clipped to [0, 1].

    Provides early-training gradient signal before the model learns the
    ``Answer:`` pattern. Saturates at 1.0 so it can't be gamed by producing
    an arbitrarily long completion.
    """
    rewards: list[float] = []
    for completion in completions:
        length = len(completion) if completion else 0
        rewards.append(min(length / _LENGTH_SATURATION_CHARS, 1.0))
    return rewards


def combined_format_length_reward(completions: list[str], **kwargs) -> list[float]:
    """``0.8 * format_match_reward + 0.2 * length_shaping_reward`` per item.

    This is the default fallback wired into :class:`forgelm.trainer.ForgeTrainer`
    when ``training.grpo_reward_model`` is not configured. Format dominates so
    the model converges on the spec'd output shape; length keeps the gradient
    non-zero during the first few thousand steps.
    """
    fmt = format_match_reward(completions, **kwargs)
    length = length_shaping_reward(completions, **kwargs)
    # strict=True: both lists derive from the same ``completions``, so a length
    # mismatch is a wiring regression that must raise immediately rather than
    # silently truncate (mirrors ``trainer._math_reward_fn``'s strict zip).
    return [_FORMAT_WEIGHT * f + _LENGTH_WEIGHT * lensc for f, lensc in zip(fmt, length, strict=True)]
