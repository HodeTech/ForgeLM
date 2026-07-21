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


def _rule(label: str):
    for pattern, rule_label, _scope in guard.build_rules():
        if rule_label == label:
            return pattern
    raise AssertionError(f"{label} rule missing from build_rules()")


def _webhook_rule():
    return _rule("webhook_events")


def _reads(label: str, line: str):
    """Return the integer the *label* rule reads from a line, or None."""
    pattern = _rule(label)
    scan = guard._strip_emphasis(line)
    match = pattern.search(scan)
    return guard._to_int(match.group("count")) if match else None


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


class TestTestModuleAndGuardRules:
    """The two counts added after the README rewrite: derived from a glob so a
    literal that a commit falsifies fails the build (F1 from the Opus review)."""

    def test_canonical_test_modules_matches_the_glob(self):
        assert guard.canonical_test_modules() == len(list((guard.REPO_ROOT / "tests").glob("test_*.py")))

    def test_canonical_ci_guards_matches_the_glob(self):
        assert guard.canonical_ci_guards() == len(list((guard.REPO_ROOT / "tools").glob("check_*.py")))

    def test_test_module_claim_is_read(self):
        assert _reads("test_modules", "backed by 124 test modules and 29 CI guards") == 124

    def test_ci_guard_claim_is_read(self):
        assert _reads("ci_guards", "backed by 124 test modules and 29 CI guards") == 29

    def test_generic_count_without_the_qualifier_does_not_match(self):
        assert _reads("test_modules", "124 rows were processed") is None
        assert _reads("ci_guards", "29 files changed") is None

    def test_both_rules_are_scoped_to_toplevel(self):
        # A historical "+4 CI guards this wave" line in docs/roadmap/ is correct
        # as written; the rule must not rewrite it to the current total, so it
        # only enforces on README.
        scopes = {label: scope for _p, label, scope in guard.build_rules()}
        assert scopes["test_modules"] == "toplevel"
        assert scopes["ci_guards"] == "toplevel"

    def test_toplevel_scope_excludes_docs_roadmap(self):
        paths = guard._scanned_docs("toplevel")
        assert all("roadmap" not in str(p) for p in paths)
        assert any(p.name == "README.md" for p in paths)


class TestGuardPassesOnRepo:
    def test_repo_docs_have_no_numerical_drift(self):
        # The shipped docs are correct (webhook_events == 8 everywhere);
        # the hardened guard must NOT start false-failing on them.
        assert guard.main([]) == 0
