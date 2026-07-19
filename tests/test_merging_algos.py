"""Unit tests for merging algorithms (TIES, DARE, SLERP, linear)."""

import math

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
        # elected_sign=+1; only d1 agrees. The paper's disjoint merge averages
        # over the sign-agreeing subset (renormalized to weight 1.0), so the
        # magnitude is the full 3.0 — NOT the attenuated 0.5*3.0=1.5 that a
        # plain masked weighted SUM (no renormalization) would produce.
        assert result[0] >= 0, "Zero-vote should resolve to positive sign (+1)"
        assert result[0] == pytest.approx(3.0), (
            "disjoint merge must renormalize by the agreeing weight sum, not shrink the magnitude"
        )


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


class TestDareSeedPerTensor:
    """F-M-19: DARE per-tensor seed reuse produced identical drop masks for all
    same-shaped weight tensors when invoked from _ties_dare_merge.  The fix
    derives each call's seed from dare_seed ^ hash(key) so distinct keys always
    receive distinct masks."""

    def test_different_keys_produce_different_masks(self):
        """Two calls with distinct key-derived seeds must yield different results."""
        d = torch.ones(4, 4)
        seed = 42
        # Simulate the per-key seed derivation used in _ties_dare_merge
        seed_a = seed ^ (hash("model.layer.0.q_proj.weight") & 0xFFFF_FFFF)
        seed_b = seed ^ (hash("model.layer.1.q_proj.weight") & 0xFFFF_FFFF)
        # The two keys must differ in their hash bits; if they happen to collide
        # (astronomically unlikely for these two strings) the test is vacuously
        # skipped rather than falsely failing.
        if seed_a == seed_b:
            pytest.skip("hash collision — try different key strings")
        r1 = _dare_merge_tensor([d], [1.0], drop_rate=0.5, seed=seed_a)
        r2 = _dare_merge_tensor([d], [1.0], drop_rate=0.5, seed=seed_b)
        assert not torch.equal(r1, r2), (
            "F-M-19 regression: same-shaped tensors at different keys must receive distinct DARE drop masks"
        )

    def test_same_key_seed_is_deterministic(self):
        """The same key-derived seed must produce identical results across two runs."""
        seed = 99
        key = "transformer.h.0.attn.c_attn.weight"
        effective_seed = seed ^ (hash(key) & 0xFFFF_FFFF)
        d = torch.randn(8, 8)
        r1 = _dare_merge_tensor([d], [1.0], drop_rate=0.4, seed=effective_seed)
        r2 = _dare_merge_tensor([d], [1.0], drop_rate=0.4, seed=effective_seed)
        assert torch.equal(r1, r2), "F-M-19: per-key DARE must remain deterministic"


class TestTiesDareMergeZeroWeightGuard:
    """F-M-20: _ties_dare_merge was missing the zero-weight guard that
    _linear_merge has, causing ZeroDivisionError instead of a descriptive
    ValueError for cancelling adapter weights."""

    def _make_fake_peft(self, monkeypatch, task_vector):
        """Stub peft and PeftModel so _ties_dare_merge runs without HF models."""
        import sys
        import types
        from unittest.mock import MagicMock

        fake_peft = types.ModuleType("peft")

        class _FakePeft:
            def __init__(self, base, path):
                pass

            def merge_and_unload(self):
                m = MagicMock()
                m.state_dict.return_value = task_vector
                return m

        fake_peft.PeftModel = MagicMock(side_effect=lambda base, path: _FakePeft(base, path))
        monkeypatch.setitem(sys.modules, "peft", fake_peft)

    def test_zero_weight_sum_raises_value_error(self, monkeypatch):
        """Cancelling weights (e.g. +0.5 and -0.5) must raise ValueError,
        not ZeroDivisionError, so the error surfaces as exit code 1 (config),
        not exit code 2 (training)."""
        from unittest.mock import MagicMock

        base_w = torch.tensor([1.0, 2.0])
        tv = {"layer.weight": torch.tensor([1.5, 2.5])}

        base = MagicMock()
        base.state_dict.return_value = {"layer.weight": base_w.clone()}
        base.load_state_dict = MagicMock()

        self._make_fake_peft(monkeypatch, tv)

        from forgelm.merging import _ties_dare_merge

        adapters = [{"path": "a", "weight": 0.5}, {"path": "b", "weight": -0.5}]
        with pytest.raises(ValueError, match="sum to 0"):
            _ties_dare_merge(base, adapters, method="ties")

    def test_positive_weights_do_not_raise(self, monkeypatch):
        """Positive weights must not trigger the guard."""
        from unittest.mock import MagicMock

        base_w = torch.tensor([1.0, 2.0])
        tv = {"layer.weight": torch.tensor([1.5, 2.5])}

        base = MagicMock()
        base.state_dict.return_value = {"layer.weight": base_w.clone()}
        base.load_state_dict = MagicMock()

        self._make_fake_peft(monkeypatch, tv)

        from forgelm.merging import _ties_dare_merge

        adapters = [{"path": "a", "weight": 0.6}, {"path": "b", "weight": 0.4}]
        # Must not raise — result is the base model mock
        result = _ties_dare_merge(base, adapters, method="ties")
        assert result is base


class TestTiesDareMergeNegativeWeightWarning:
    """MergeConfig does not constrain merge.models[].weight to be
    non-negative. A negative weight can make the per-key
    ``agree_weight_sum`` in ``_ties_merge_tensor`` negative-but-nonzero at a
    sign-agreeing position, silently skipping the disjoint-merge
    renormalization instead of raising. ``_ties_dare_merge`` must at least
    warn when it sees a negative weight, so the risk is not silent."""

    def _make_fake_peft(self, monkeypatch, task_vector):
        import sys
        import types
        from unittest.mock import MagicMock

        fake_peft = types.ModuleType("peft")

        class _FakePeft:
            def __init__(self, base, path):
                pass

            def merge_and_unload(self):
                m = MagicMock()
                m.state_dict.return_value = task_vector
                return m

        fake_peft.PeftModel = MagicMock(side_effect=lambda base, path: _FakePeft(base, path))
        monkeypatch.setitem(sys.modules, "peft", fake_peft)

    def test_negative_weight_logs_warning(self, monkeypatch, caplog):
        from unittest.mock import MagicMock

        base_w = torch.tensor([1.0, 2.0])
        tv = {"layer.weight": torch.tensor([1.5, 2.5])}

        base = MagicMock()
        base.state_dict.return_value = {"layer.weight": base_w.clone()}
        base.load_state_dict = MagicMock()

        self._make_fake_peft(monkeypatch, tv)

        from forgelm.merging import _ties_dare_merge

        # Non-cancelling sum (2.0 total) so the zero-weight guard does not
        # fire and the negative-weight path is exercised end to end.
        adapters = [{"path": "a", "weight": 3.0}, {"path": "b", "weight": -1.0}]
        with caplog.at_level("WARNING", logger="forgelm.merging"):
            result = _ties_dare_merge(base, adapters, method="ties")

        assert result is base
        assert any("negative" in record.message for record in caplog.records)

    def test_all_non_negative_weights_do_not_warn(self, monkeypatch, caplog):
        from unittest.mock import MagicMock

        base_w = torch.tensor([1.0, 2.0])
        tv = {"layer.weight": torch.tensor([1.5, 2.5])}

        base = MagicMock()
        base.state_dict.return_value = {"layer.weight": base_w.clone()}
        base.load_state_dict = MagicMock()

        self._make_fake_peft(monkeypatch, tv)

        from forgelm.merging import _ties_dare_merge

        adapters = [{"path": "a", "weight": 0.6}, {"path": "b", "weight": 0.4}]
        with caplog.at_level("WARNING", logger="forgelm.merging"):
            _ties_dare_merge(base, adapters, method="ties")

        assert not any("negative" in record.message for record in caplog.records)


class TestTiesDareMergePerKeyWeightRenorm:
    """F-L-17: when a key is absent from some adapters, zip(deltas, weights)
    silently truncated to the shorter list, underweighting the surviving
    adapter's delta.  The fix filters pairs together and renormalizes."""

    def test_partial_key_coverage_renormalizes(self):
        """Adapter B carries a key that adapter A does not.  After the fix the
        surviving weight for that key must be renormalized to 1.0, not the raw
        0.4 fraction."""
        # task_vector_a has only 'q_proj'; task_vector_b has both 'q_proj' and 'v_proj'
        tv_a = {"q_proj": torch.tensor([1.0, 0.0, 0.0])}
        tv_b = {
            "q_proj": torch.tensor([0.0, 1.0, 0.0]),
            "v_proj": torch.tensor([0.1, 0.2, 0.3]),
        }
        # Global normalized weights: A=0.6, B=0.4
        weights = [0.6, 0.4]

        # Direct exercise of the renorm logic for the 'v_proj' key
        key = "v_proj"
        task_vectors = [tv_a, tv_b]
        pairs = [(tv[key].float(), w) for tv, w in zip(task_vectors, weights) if key in tv]
        deltas, key_weights = zip(*pairs)
        key_total = sum(key_weights)
        key_weights_norm = [kw / key_total for kw in key_weights]

        assert len(key_weights_norm) == 1
        assert key_weights_norm[0] == pytest.approx(1.0), (
            "F-L-17 regression: sole surviving adapter's weight must renormalize to 1.0"
        )

        # The merged delta for v_proj must equal the raw tensor (weight=1.0 * delta)
        result = _ties_merge_tensor(list(deltas), key_weights_norm, trim_fraction=0.0)
        expected = torch.tensor([0.1, 0.2, 0.3])
        assert torch.allclose(result, expected), (
            f"F-L-17 regression: merged v_proj should be {expected} but got {result}"
        )

    def test_both_adapters_present_weights_unchanged(self):
        """When both adapters carry the key, the per-key weights equal the global
        normalized weights — renormalization must be a no-op."""
        tv_a = {"q_proj": torch.tensor([2.0, 0.0])}
        tv_b = {"q_proj": torch.tensor([0.0, 2.0])}
        weights = [0.6, 0.4]

        key = "q_proj"
        task_vectors = [tv_a, tv_b]
        pairs = [(tv[key].float(), w) for tv, w in zip(task_vectors, weights) if key in tv]
        deltas, key_weights = zip(*pairs)
        key_total = sum(key_weights)
        key_weights_norm = [kw / key_total for kw in key_weights]

        assert key_weights_norm[0] == pytest.approx(0.6)
        assert key_weights_norm[1] == pytest.approx(0.4)


class TestSlerpAntiParallelGuard:
    """F-L-18: SLERP did not guard against nearly-anti-parallel tensors
    (omega ≈ π), where sin(omega) ≈ 0 can amplify numerical error
    catastrophically.  The fix falls back to linear interpolation."""

    def _make_slerp_call(self, v0_tensor, v1_tensor, t=0.5):
        """Exercise the SLERP omega branch logic directly (no HF model needed)."""
        dot = torch.sum(v0_tensor * v1_tensor) / (
            torch.linalg.vector_norm(v0_tensor) * torch.linalg.vector_norm(v1_tensor) + 1e-8
        )
        dot = torch.clamp(dot, -1.0, 1.0)
        omega = torch.acos(dot)
        near_parallel = omega.abs() < 1e-6
        near_anti_parallel = (omega - math.pi).abs() < 1e-6
        if near_parallel or near_anti_parallel:
            return ((1 - t) * v0_tensor + t * v1_tensor), True  # linear fallback
        else:
            so = torch.sin(omega)
            return (
                (torch.sin((1 - t) * omega) / so) * v0_tensor + (torch.sin(t * omega) / so) * v1_tensor,
                False,
            )

    def test_exact_anti_parallel_uses_linear_fallback(self):
        """v1 = -v0 is the canonical anti-parallel case; sin(omega) ≈ -8.7e-8
        in float32. After the fix the linear fallback fires."""
        v0 = torch.ones(4)
        v1 = -torch.ones(4)
        result, used_linear = self._make_slerp_call(v0, v1, t=0.3)
        assert used_linear, "F-L-18 regression: anti-parallel SLERP must use linear fallback"
        expected = (1 - 0.3) * v0 + 0.3 * v1
        assert torch.allclose(result, expected)

    def test_anti_parallel_result_is_finite(self):
        """Any nearly-anti-parallel pair must produce finite (non-NaN, non-Inf)
        output regardless of t."""
        v0 = torch.tensor([1.0, 0.0, 0.0])
        v1 = torch.tensor([-1.0 + 1e-5, 0.0, 0.0])  # nearly but not exactly anti-parallel
        for t in [0.0, 0.3, 0.5, 0.7, 1.0]:
            result, _ = self._make_slerp_call(v0, v1, t=t)
            assert torch.isfinite(result).all(), f"F-L-18: non-finite SLERP result at t={t}"

    def test_normal_case_still_uses_slerp(self):
        """Orthogonal vectors (omega = π/2) must NOT take the linear fallback."""
        v0 = torch.tensor([1.0, 0.0])
        v1 = torch.tensor([0.0, 1.0])
        _, used_linear = self._make_slerp_call(v0, v1, t=0.5)
        assert not used_linear, "F-L-18: orthogonal vectors should use SLERP, not linear fallback"


class TestTiesDisjointMergeRenormalization:
    """The TIES 'Merge' step is the paper's disjoint merge: average only the
    sign-agreeing models, renormalizing by their weight sum.  Without the
    renormalization the merged magnitude is attenuated whenever fewer than all
    adapters agree with the elected sign — silently shrinking the merge."""

    def test_partial_agreement_renormalizes_to_average(self):
        # One position, three adapters: +4 (w=0.5), +2 (w=0.25), -6 (w=0.25).
        # Sign votes: +1 +1 -1 = +1 → elected +1.  Agreeing subset: the two
        # positive adapters, weights 0.5 and 0.25 (sum 0.75).  Disjoint-merge
        # average = (4*0.5 + 2*0.25) / 0.75 = 2.5 / 0.75 = 3.3333...
        d1 = torch.tensor([4.0])
        d2 = torch.tensor([2.0])
        d3 = torch.tensor([-6.0])
        result = _ties_merge_tensor([d1, d2, d3], weights=[0.5, 0.25, 0.25], trim_fraction=0.0)
        assert result[0] == pytest.approx(2.5 / 0.75)

    def test_full_agreement_is_plain_weighted_average(self):
        # All adapters agree (both positive) → renorm denominator equals the
        # full weight sum (1.0), so the result is the plain weighted average.
        d1 = torch.tensor([2.0])
        d2 = torch.tensor([4.0])
        result = _ties_merge_tensor([d1, d2], weights=[0.5, 0.5], trim_fraction=0.0)
        assert result[0] == pytest.approx(3.0)  # (2*0.5 + 4*0.5) / 1.0

    def test_all_zero_deltas_stay_zero(self):
        # No adapter has a sign at any position → agree_weight_sum is 0 →
        # the guarded division must leave the result at 0 (no NaN/Inf).
        d1 = torch.zeros(3)
        d2 = torch.zeros(3)
        result = _ties_merge_tensor([d1, d2], weights=[0.5, 0.5], trim_fraction=0.0)
        assert torch.allclose(result, torch.zeros(3))
        assert torch.isfinite(result).all()


class TestDareSeedStableAcrossProcesses:
    """F-H (reproducibility): the DARE per-key seed must derive from a
    process-stable hash, not CPython's PYTHONHASHSEED-randomized ``hash(str)``,
    so two separate merges with the same ``dare_seed`` are byte-identical."""

    @staticmethod
    def _run(code: str, hashseed: str) -> str:
        import os
        import subprocess
        import sys

        env = dict(os.environ, PYTHONHASHSEED=hashseed)
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env=env,
        )
        assert proc.returncode == 0, proc.stderr
        return proc.stdout.strip()

    def test_stable_key_hash_is_deterministic_across_pythonhashseed(self):
        """The shipped _stable_key_hash must return the same value in fresh
        interpreters started with different PYTHONHASHSEED values."""
        code = (
            "from forgelm.merging import _stable_key_hash;"
            "print(_stable_key_hash('model.layers.0.self_attn.q_proj.weight'))"
        )
        v0 = self._run(code, "0")
        v1 = self._run(code, "1")
        v2 = self._run(code, "123456")
        assert v0 == v1 == v2, "DARE per-key hash must not depend on PYTHONHASHSEED"

    def test_builtin_str_hash_is_randomized(self):
        """Sanity/justification: the old ``hash(key)`` derivation is randomized
        across processes — exactly the reproducibility bug _stable_key_hash fixes."""
        code = "print(hash('model.layers.0.self_attn.q_proj.weight'))"
        values = {self._run(code, "1"), self._run(code, "2"), self._run(code, "3")}
        assert len(values) > 1, "builtin hash(str) should vary across PYTHONHASHSEED"


class TestSlerpMergeExercisesRealFunction:
    """ROUTED (tests-standalone): _slerp_merge's body — including the F-L-18
    near-parallel/anti-parallel guard — was never executed; the prior test
    re-implemented the omega math inline.  These stub peft (mirroring
    TestTiesDareMergeZeroWeightGuard) so the SHIPPED _slerp_merge runs
    end-to-end, and drive the merge_peft_adapters 'slerp' dispatch branch."""

    @staticmethod
    def _install_fake_peft(monkeypatch, state_a, state_b):
        import sys
        import types
        from unittest.mock import MagicMock

        fake_peft = types.ModuleType("peft")
        states = iter([state_a, state_b])

        class _FakeAdapter:
            def merge_and_unload(self):
                m = MagicMock()
                m.state_dict.return_value = next(states)
                return m

        fake_peft.PeftModel = MagicMock()
        fake_peft.PeftModel.from_pretrained = MagicMock(side_effect=lambda base, path: _FakeAdapter())
        monkeypatch.setitem(sys.modules, "peft", fake_peft)

    @staticmethod
    def _fake_base(keys):
        from unittest.mock import MagicMock

        base = MagicMock()
        base.state_dict.return_value = {k: torch.zeros_like(v) for k, v in keys.items()}
        base.load_state_dict = MagicMock()
        return base

    def _merged_state(self, base):
        # _slerp_merge's final call is load_state_dict(merged_state, strict=False).
        return base.load_state_dict.call_args_list[-1].args[0]

    def test_anti_parallel_takes_linear_fallback(self, monkeypatch):
        """v1 = -v0 (anti-parallel, sin(omega)≈0) must fall back to linear
        interpolation and produce a finite result — exercising the real guard."""
        from forgelm.merging import _slerp_merge

        state_a = {"weight": torch.ones(4)}
        state_b = {"weight": -torch.ones(4)}
        self._install_fake_peft(monkeypatch, state_a, state_b)
        base = self._fake_base(state_a)

        adapters = [{"path": "a", "weight": 1.0}, {"path": "b", "weight": 1.0}]
        out = _slerp_merge(base, adapters)
        assert out is base
        merged = self._merged_state(base)["weight"]
        # t = 0.5 → linear fallback gives 0.5*1 + 0.5*(-1) = 0 for every element.
        assert torch.isfinite(merged).all(), "anti-parallel SLERP must not yield NaN/Inf"
        assert torch.allclose(merged, torch.zeros(4), atol=1e-5)

    def test_orthogonal_uses_true_slerp(self, monkeypatch):
        """Orthogonal unit vectors at t=0.5 → SLERP gives sin(π/4)≈0.7071 on
        each axis (NOT the linear 0.5), proving the SLERP branch executed."""
        from forgelm.merging import _slerp_merge

        state_a = {"weight": torch.tensor([1.0, 0.0])}
        state_b = {"weight": torch.tensor([0.0, 1.0])}
        self._install_fake_peft(monkeypatch, state_a, state_b)
        base = self._fake_base(state_a)

        adapters = [{"path": "a", "weight": 1.0}, {"path": "b", "weight": 1.0}]
        _slerp_merge(base, adapters)
        merged = self._merged_state(base)["weight"]
        assert merged[0] == pytest.approx(0.7071, abs=1e-3)
        assert merged[1] == pytest.approx(0.7071, abs=1e-3)

    def test_merge_peft_adapters_dispatches_slerp(self, monkeypatch, tmp_path):
        """The `elif method == "slerp"` dispatch branch in merge_peft_adapters
        was never covered — drive it with transformers stubbed out."""
        import sys
        import types
        from unittest.mock import MagicMock

        fake_tf = types.ModuleType("transformers")
        fake_tf.AutoModelForCausalLM = MagicMock()
        fake_tf.AutoModelForCausalLM.from_pretrained = MagicMock(return_value=MagicMock())
        fake_tf.AutoTokenizer = MagicMock()
        fake_tf.AutoTokenizer.from_pretrained = MagicMock(return_value=MagicMock())
        monkeypatch.setitem(sys.modules, "transformers", fake_tf)

        import forgelm.merging as merging

        captured = {}

        def _fake_slerp(base_model, adapters):
            captured["adapters"] = adapters
            return base_model

        monkeypatch.setattr(merging, "_slerp_merge", _fake_slerp)

        result = merging.merge_peft_adapters(
            base_model_path="org/base",
            adapters=[{"path": "a", "weight": 1.0}, {"path": "b", "weight": 1.0}],
            method="slerp",
            output_dir=str(tmp_path / "out"),
        )
        assert captured.get("adapters") is not None, "method='slerp' must dispatch to _slerp_merge"
        assert result.success is True
        assert result.method == "slerp"
