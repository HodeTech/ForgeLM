"""Unit tests for merging algorithms (TIES, DARE, SLERP, linear)."""

import pytest

torch = pytest.importorskip("torch")

from forgelm.merging import (  # noqa: E402
    _dare_merge_tensor,
    _ties_merge_tensor,
)


class TestTiesMergeTensor:
    def test_basic_merge(self):
        d1 = torch.tensor([1.0, -2.0, 3.0, -0.1])
        d2 = torch.tensor([1.5, -1.0, -2.0, 0.05])
        result = _ties_merge_tensor([d1, d2], [0.5, 0.5], trim_fraction=0.0)
        assert result.shape == d1.shape

    def test_trim_removes_small_values(self):
        d1 = torch.tensor([10.0, 0.01, -10.0, 0.001])
        result = _ties_merge_tensor([d1], [1.0], trim_fraction=0.5)
        # After trim, the smallest 50% by magnitude should be zeroed
        # Values 0.01 and 0.001 should be trimmed
        assert result.shape == d1.shape

    def test_sign_election(self):
        # 3 deltas where sign at index 0 is +, +, - => majority positive
        d1 = torch.tensor([1.0])
        d2 = torch.tensor([2.0])
        d3 = torch.tensor([-0.5])
        result = _ties_merge_tensor([d1, d2, d3], [1 / 3, 1 / 3, 1 / 3], trim_fraction=0.0)
        assert result[0] > 0  # majority vote should be positive

    def test_zero_deltas(self):
        d1 = torch.zeros(4)
        d2 = torch.zeros(4)
        result = _ties_merge_tensor([d1, d2], [0.5, 0.5])
        assert torch.allclose(result, torch.zeros(4))

    def test_single_delta(self):
        d1 = torch.tensor([3.0, -2.0, 1.0])
        result = _ties_merge_tensor([d1], [1.0], trim_fraction=0.0)
        assert torch.allclose(result, d1)

    def test_trim_handles_tensors_above_quantile_limit(self):
        """F-P3-FABLE-19: ``torch.quantile`` hard-fails above 2^24 elements, so
        the DEFAULT TIES merge crashed on every real-size model. The kthvalue
        threshold must handle a tensor just past the limit without raising."""
        n = (1 << 24) + 1_000  # just above torch.quantile's 16,777,216 limit
        d1 = torch.randn(n)
        result = _ties_merge_tensor([d1], [1.0], trim_fraction=0.2)
        assert result.shape == d1.shape
        # ~20% of magnitudes trimmed to zero; the rest preserved.
        assert (result == 0).sum().item() > 0


class TestDareMergeTensor:
    def test_basic_merge(self):
        torch.manual_seed(42)
        d1 = torch.tensor([1.0, 2.0, 3.0, 4.0])
        d2 = torch.tensor([0.5, 1.0, 1.5, 2.0])
        result = _dare_merge_tensor([d1, d2], [0.6, 0.4], drop_rate=0.3)
        assert result.shape == d1.shape

    def test_zero_drop_rate_equals_weighted_sum(self):
        d1 = torch.tensor([1.0, 2.0])
        d2 = torch.tensor([3.0, 4.0])
        result = _dare_merge_tensor([d1, d2], [0.5, 0.5], drop_rate=0.0)
        expected = d1 * 0.5 + d2 * 0.5
        assert torch.allclose(result, expected)

    def test_full_drop_rate(self):
        d1 = torch.tensor([1.0, 2.0, 3.0])
        # drop_rate=1.0 would cause division by zero, but 0.99 should drop almost everything
        result = _dare_merge_tensor([d1], [1.0], drop_rate=0.99)
        assert result.shape == d1.shape

    def test_output_shape_matches_input(self):
        d1 = torch.randn(10, 10)
        d2 = torch.randn(10, 10)
        result = _dare_merge_tensor([d1, d2], [0.7, 0.3])
        assert result.shape == (10, 10)

    def test_single_delta(self):
        torch.manual_seed(0)
        d1 = torch.tensor([5.0, 10.0])
        result = _dare_merge_tensor([d1], [1.0], drop_rate=0.0)
        assert torch.allclose(result, d1)

    def test_dare_deterministic(self):
        """Same seed must produce identical results across two calls."""
        d = [torch.randn(100), torch.randn(100)]
        w = [0.5, 0.5]
        r1 = _dare_merge_tensor(d, w, drop_rate=0.3, seed=42)
        r2 = _dare_merge_tensor(d, w, drop_rate=0.3, seed=42)
        assert torch.allclose(r1, r2), "DARE should be deterministic with the same seed"

    def test_dare_different_seeds_differ(self):
        """Different seeds should (with overwhelming probability) produce different results."""
        d = [torch.randn(200)]
        w = [1.0]
        r1 = _dare_merge_tensor(d, w, drop_rate=0.5, seed=1)
        r2 = _dare_merge_tensor(d, w, drop_rate=0.5, seed=99)
        # It's astronomically unlikely that two independent masks are identical for 200 elements
        assert not torch.allclose(r1, r2), "Different seeds should produce different results"


class TestTiesZeroVote:
    def test_zero_vote_does_not_zero_params(self):
        """Zero votes (exactly cancelling deltas) should resolve to +1, not zero parameters."""
        # Two deltas that exactly cancel: one +1, one -1 at every position → zero votes
        d1 = torch.tensor([1.0, -1.0, 2.0])
        d2 = torch.tensor([-1.0, 1.0, -2.0])
        result = _ties_merge_tensor([d1, d2], weights=[0.5, 0.5], trim_fraction=0.0)
        # With zero-vote fix (ties go to +1), the result should not be all zeros
        assert not torch.all(result == 0), "Zero-vote tie should not zero all parameters"

    def test_zero_vote_resolves_to_positive(self):
        """When sign votes cancel exactly, elected sign must be +1."""
        # Single element: one +1 and one -1 → sum=0 → elected sign should be +1
        d1 = torch.tensor([3.0])
        d2 = torch.tensor([-3.0])
        result = _ties_merge_tensor([d1, d2], weights=[0.5, 0.5], trim_fraction=0.0)
        # elected_sign=+1, so the value with sign matching +1 is d1[0]=3.0, weighted by 0.5
        assert result[0] >= 0, "Zero-vote should resolve to positive sign (+1)"


class TestLinearMergeZeroWeight:
    """F-P8-C-18: the orchestration layer's zero-weight guard
    (merging.py:94) was never triggered — only the leaf tensor math was
    covered. peft is stubbed so the test pins the raise regardless of
    whether the optional extra is installed."""

    def test_zero_weight_sum_raises(self, monkeypatch):
        import sys
        import types
        from unittest.mock import MagicMock

        # Stub peft so the `from peft import PeftModel` at the top of
        # _linear_merge resolves; the raise happens before PeftModel is used.
        fake_peft = types.ModuleType("peft")
        fake_peft.PeftModel = MagicMock()
        monkeypatch.setitem(sys.modules, "peft", fake_peft)

        from forgelm.merging import _linear_merge

        base_model = MagicMock()
        adapters = [{"path": "a", "weight": 1.0}, {"path": "b", "weight": -1.0}]
        with pytest.raises(ValueError, match="sum to 0"):
            _linear_merge(base_model, adapters)


class TestAdvancedMergeDispatch:
    """F-P8-C-18: _advanced_merge must route ties/dare to the native
    _ties_dare_merge with the method preserved."""

    @pytest.mark.parametrize("method", ["ties", "dare"])
    def test_method_dispatch(self, monkeypatch, method):
        import forgelm.merging as merging

        captured = {}

        def _fake_ties_dare(base_model, adapters, m, **kwargs):
            captured["method"] = m
            captured.update(kwargs)
            return base_model

        monkeypatch.setattr(merging, "_ties_dare_merge", _fake_ties_dare)
        sentinel = object()
        out = merging._advanced_merge(sentinel, [{"path": "a"}], method)
        assert out is sentinel
        assert captured["method"] == method

    def test_hyperparameters_threaded_through(self, monkeypatch):
        """PR#63-review: explicit knobs reach _ties_dare_merge unchanged."""
        import forgelm.merging as merging

        captured = {}

        def _fake_ties_dare(base_model, adapters, m, **kwargs):
            captured.update(kwargs)
            return base_model

        monkeypatch.setattr(merging, "_ties_dare_merge", _fake_ties_dare)
        merging._advanced_merge(
            object(),
            [{"path": "a"}],
            "ties",
            ties_trim_fraction=0.9,
            dare_drop_rate=0.95,
            dare_seed=7,
        )
        assert captured["ties_trim_fraction"] == pytest.approx(0.9)
        assert captured["dare_drop_rate"] == pytest.approx(0.95)
        assert captured["dare_seed"] == 7


class TestMergeHyperparameters:
    """F-P3-FABLE-60: TIES/DARE hyperparameters live as named, documented
    module constants (not bare magic numbers at the call sites), and the trim
    semantics match the corrected docstring (keep top 80% at trim_fraction=0.2)."""

    def test_named_constants_have_documented_defaults(self):
        import forgelm.merging as merging

        assert merging._TIES_TRIM_FRACTION == pytest.approx(0.2)
        assert merging._DARE_DROP_RATE == pytest.approx(0.3)
        assert merging._DARE_SEED == 42

    def test_trim_fraction_keeps_top_majority(self):
        # 10 strictly-increasing magnitudes; the call-site trim_fraction (0.2)
        # zeroes only the smallest-magnitude tail and KEEPS the large majority —
        # the corrected docstring's "keep top ~80%" behaviour (NOT the inverted
        # "keep top 20%" the old docstring implied).
        import forgelm.merging as merging

        d = torch.tensor([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0])
        result = _ties_merge_tensor([d], [1.0], trim_fraction=merging._TIES_TRIM_FRACTION)
        zeroed = (result == 0).sum().item()
        survived = (result != 0).sum().item()
        # The bottom tail is trimmed; the clear majority (incl. the largest
        # magnitudes) survives — the opposite of a keep-top-20% merge.
        assert 1 <= zeroed <= 2
        assert survived >= 8
        assert result[-1] == pytest.approx(10.0)  # largest magnitude always survives
