"""Phase 15 Wave 2 Task 11 unit tests — operator strip-pattern + ReDoS guard."""

from __future__ import annotations

import re

import pytest

from forgelm._strip_pattern import (
    DEFAULT_TIMEOUT_S,
    StripPatternError,
    apply_strip_patterns,
    compile_strip_patterns,
    validate_strip_pattern,
)


class TestValidateStripPattern:
    def test_accepts_simple_pattern(self):
        assert validate_strip_pattern(r"^WATERMARK \d+$") == r"^WATERMARK \d+$"

    def test_rejects_empty(self):
        with pytest.raises(StripPatternError, match="non-empty"):
            validate_strip_pattern("")

    def test_rejects_invalid_regex_syntax(self):
        with pytest.raises(StripPatternError, match="not a valid"):
            validate_strip_pattern("[unclosed")

    def test_rejects_nested_unbounded_quantifier(self):
        # Classic ReDoS shape: (a+)+ — nested unbounded quantifier.
        with pytest.raises(StripPatternError, match="ReDoS"):
            validate_strip_pattern("(a+)+b")

    @pytest.mark.parametrize(
        "pattern",
        [
            r"(\w+)+x",  # round-2 (S-1): escape-shape nested unbounded.
            r"(\d+)+x",
            r"(\s+)+x",
            r"^(\w+\s+)+x",  # classic textbook ReDoS.
            r"([abc]+)+d",  # character-class nested unbounded.
        ],
    )
    def test_rejects_escape_and_class_nested_unbounded(self, pattern):
        with pytest.raises(StripPatternError, match="ReDoS"):
            validate_strip_pattern(pattern)

    @pytest.mark.parametrize(
        "pattern",
        [
            r"((a+))+b",  # F-P6-OPUS-06: double-group wrapping defeated the walk.
            r"((\w+))+x",
            r"(((a+)))+b",  # triple-wrapped; flag must still propagate up.
            r"((a+))*b",  # outer ``*`` is just as catastrophic as ``+``.
        ],
    )
    def test_rejects_redundantly_wrapped_nested_unbounded(self, pattern):
        # F-P6-OPUS-06: wrapping the inner quantifier in extra groups used to
        # reset the parent's unbounded flag, so ``((a+))+b`` slipped past the
        # validator and backtracked exponentially. The flag now propagates up
        # through redundant grouping parens.
        with pytest.raises(StripPatternError, match="ReDoS"):
            validate_strip_pattern(pattern)

    @pytest.mark.parametrize(
        "pattern",
        [
            r"^WATERMARK \d+$",
            r"a{1,100}b",
            r"\d{1,5}",
            r"(\d+)b",  # single unbounded inside group, no outer quantifier.
            r"[A-Z]+",  # standalone unbounded, no nesting.
            r"(a+b)+c",  # group ends with bounded literal, outer quantifier OK.
            r"((a+)b)+c",  # F-P6-OPUS-06: inner bounded tail (``b``) resets flag.
            r"(a*b)+c",  # required separator anchors each iteration; not ReDoS.
            r"\b(?:https?|ftp)://[A-Z0-9]+",
        ],
    )
    def test_accepts_safe_patterns(self, pattern):
        # Sanity check: the new forward-walking validator must NOT
        # false-positive on patterns whose last group atom is bounded
        # or which carry only a single unbounded quantifier overall.
        assert validate_strip_pattern(pattern) == pattern

    def test_rejects_dotall_lazy_with_backref(self):
        # rule 6: .*? + back-reference under DOTALL is the S5852 shape.
        with pytest.raises(StripPatternError, match="DOTALL"):
            validate_strip_pattern(r"(?s)(a).*?\1")

    def test_rejects_overlong_pattern(self):
        # 3 KB pattern exceeds the 2 KB safety bound.
        with pytest.raises(StripPatternError, match="too long"):
            validate_strip_pattern("a" * 3000)

    def test_accepts_anchored_negated_class(self):
        # The canonical "strip header line" pattern shape.
        assert validate_strip_pattern(r"^[A-Z ]+CONFIDENTIAL[A-Z ]*$")

    def test_alternation_branch_redos_accepted_and_docstring_documents_gap(self):
        # F-L-04: ``(a+|b)+c`` is a catastrophically exponential ReDoS shape
        # (confirmed O(2^n) in review) but the validator accepts it because it
        # tracks only the *last atom per group*.  The last atom inside the group
        # is the literal ``b`` (bounded), so the outer ``+`` bypasses the reject.
        # This test locks two things at once:
        #   1. validate_strip_pattern must NOT raise — changing the validator to
        #      accidentally reject this shape would break the test.
        #   2. The _check_unbounded_quantifier_sequence docstring MUST explicitly
        #      call out the alternation-branch gap so operators are not misled
        #      into thinking the shape is caught.
        assert validate_strip_pattern(r"(a+|b)+c") == r"(a+|b)+c"

        from forgelm._strip_pattern import _check_unbounded_quantifier_sequence

        doc = _check_unbounded_quantifier_sequence.__doc__ or ""
        assert "alternation" in doc.lower(), (
            "_check_unbounded_quantifier_sequence docstring must document the "
            "alternation-branch ReDoS gap (F-L-04). Add text mentioning "
            "'Alternation-branch variants … are not caught here' to the Scope section."
        )


class TestCompileStripPatterns:
    def test_compile_returns_raw_plus_compiled_pairs(self):
        pairs = compile_strip_patterns([r"^WATERMARK$", r"^FOOTER$"])
        assert len(pairs) == 2
        assert all(isinstance(p[1], re.Pattern) for p in pairs)
        assert pairs[0][0] == r"^WATERMARK$"

    def test_compile_propagates_validation_error(self):
        with pytest.raises(StripPatternError):
            compile_strip_patterns([r"(a+)+b"])


class TestApplyStripPatterns:
    def test_apply_strips_matching_lines(self):
        patterns = compile_strip_patterns([r"^WATERMARK \d+$"])
        text = "Body\nWATERMARK 1\nMore body.\nWATERMARK 2\nFinal."
        out, subs = apply_strip_patterns(text, patterns)
        assert subs == 2
        assert "WATERMARK" not in out

    def test_apply_no_patterns_returns_input_unchanged(self):
        text = "Body text"
        assert apply_strip_patterns(text, [])[0] == "Body text"

    def test_apply_empty_text_returns_zero(self):
        patterns = compile_strip_patterns([r"^X$"])
        out, subs = apply_strip_patterns("", patterns)
        assert out == ""
        assert subs == 0

    def test_apply_chains_patterns_in_order(self):
        patterns = compile_strip_patterns([r"^A$", r"^B$"])
        text = "A\nB\nKeep\nA\nB"
        out, subs = apply_strip_patterns(text, patterns)
        assert subs == 4
        assert "Keep" in out

    def test_default_timeout_constant_documented(self):
        # Operator-facing constant — 5s is the documented default in
        # `forgelm/_strip_pattern.py` + CLI help text.
        assert DEFAULT_TIMEOUT_S == 5

    def test_apply_rejects_zero_timeout(self):
        # Round-2 (S-1): zero / negative timeout_s used to silently
        # disable the alarm; now rejected with ValueError so operator
        # misconfiguration is loud.
        patterns = compile_strip_patterns([r"^X$"])
        with pytest.raises(ValueError, match="positive int"):
            apply_strip_patterns("X", patterns, timeout_s=0)

    def test_apply_rejects_negative_timeout(self):
        patterns = compile_strip_patterns([r"^X$"])
        with pytest.raises(ValueError, match="positive int"):
            apply_strip_patterns("X", patterns, timeout_s=-5)

    def test_apply_accepts_none_timeout(self):
        patterns = compile_strip_patterns([r"^X$"])
        # ``None`` is the explicit "no guard" signal; must keep working.
        out, _ = apply_strip_patterns("X\nbody", patterns, timeout_s=None)
        assert "X" not in out


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
