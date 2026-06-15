"""Own-tests for tools/check_doc_numerical_claims.py (F-P8-C-27).

The webhook-events count-claim regex previously matched only an
unwrapped English number word immediately followed by whitespace + the
qualifier, so it silently missed:

* bold-wrapped counts — ``**five** webhook events`` (trailing ``**``
  defeated the ``\\s+`` after the number);
* Turkish number words — the TR mirrors phrase the same counts as
  ``beş`` / ``sekiz`` / ..., absent from ``_NUM_WORDS_TO_INT``.

These tests pin the hardened behaviour so a future regex regression that
re-narrows the guard fails loudly.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

import check_doc_numerical_claims as guard  # noqa: E402


def _webhook_rule():
    for pattern, label in guard.build_rules():
        if label == "webhook_events":
            return pattern
    raise AssertionError("webhook_events rule missing from build_rules()")


def _claimed(line: str):
    """Return the integer the webhook rule reads from a line, or None."""
    pattern = _webhook_rule()
    scan = guard._strip_emphasis(line)
    match = pattern.search(scan)
    return guard._to_int(match.group("count")) if match else None


class TestEmphasisStripping:
    def test_bold_wrapped_count_is_read(self):
        # F-P8-C-27 (a): trailing ``**`` used to defeat the regex.
        assert _claimed("ForgeLM emits exactly **five** webhook events") == 5

    def test_underscore_emphasis_count_is_read(self):
        assert _claimed("the only __six__ webhook events") == 6

    def test_plain_count_still_read(self):
        assert _claimed("six webhook events") == 6


class TestTurkishNumberWords:
    @pytest.mark.parametrize("word,value", [("beş", 5), ("sekiz", 8), ("üç", 3)])
    def test_turkish_words_resolve(self, word, value):
        assert guard._to_int(word) == value

    def test_turkish_bold_webhook_claim_with_possessive_suffix(self):
        # Matches the live TR mirror phrasing: "**sekiz** webhook event'i".
        assert _claimed("ForgeLM tam **sekiz** webhook event'i yayar") == 8

    def test_turkish_olay_phrasing(self):
        # The alternate Turkish qualifier "N webhook olayı".
        assert _claimed("beş webhook olayı listelenir") == 5


class TestNoFalsePositive:
    def test_unqualified_number_does_not_match(self):
        # A bare count without the webhook/wire-format/lifecycle qualifier
        # must not be read as a webhook-event claim.
        assert _claimed("six prompts were evaluated") is None

    def test_audit_events_not_matched_as_webhook(self):
        assert _claimed("there are eight audit events") is None


class TestGuardPassesOnRepo:
    def test_repo_docs_have_no_numerical_drift(self):
        # The shipped docs are correct (webhook_events == 8 everywhere);
        # the hardened guard must NOT start false-failing on them.
        assert guard.main([]) == 0
