"""Tests for GPU cost estimation feature."""

import pytest

from forgelm.config import ForgeConfig, TrainingConfig
from forgelm.results import TrainResult
from tests.conftest import minimal_config

# F-P8-C-20: this module depends on a snapshot pricing fixture that drifts
# on a different cadence than the release matrix, so the publish workflow
# excludes it via `-m 'not fixture_drift'`. The marker is the single
# source of truth for that exclusion (previously a brittle --ignore path).
pytestmark = pytest.mark.fixture_drift


class TestCostConfig:
    def test_default_none(self):
        tc = TrainingConfig()
        assert tc.gpu_cost_per_hour is None

    def test_custom_cost(self):
        tc = TrainingConfig(gpu_cost_per_hour=3.50)
        assert tc.gpu_cost_per_hour == pytest.approx(3.50)

    def test_in_full_config(self):
        cfg = ForgeConfig(**minimal_config(training={"gpu_cost_per_hour": 2.00}))
        assert cfg.training.gpu_cost_per_hour == pytest.approx(2.00)

    def test_config_template_still_parses(self):
        from forgelm.config import load_config

        cfg = load_config("config_template.yaml")
        assert cfg.training.gpu_cost_per_hour is None


class TestTrainResultCost:
    def test_default_none(self):
        r = TrainResult(success=True)
        assert r.estimated_cost_usd is None

    def test_with_cost(self):
        r = TrainResult(success=True, estimated_cost_usd=0.1234)
        assert r.estimated_cost_usd == pytest.approx(0.1234)


class TestGpuPricing:
    """Test the GPU pricing lookup logic without requiring GPU hardware."""

    def test_known_gpus_have_prices(self):
        """Import the pricing dict and verify key GPUs are present."""
        # Import the class to access pricing
        pytest.importorskip("torch")
        from forgelm.trainer import ForgeTrainer

        pricing = ForgeTrainer._GPU_PRICING
        assert "Tesla T4" in pricing
        assert "NVIDIA A100-SXM4-80GB" in pricing
        assert "NVIDIA H100 80GB HBM3" in pricing

    def test_prices_are_positive(self):
        pytest.importorskip("torch")
        from forgelm.trainer import ForgeTrainer

        for gpu, price in ForgeTrainer._GPU_PRICING.items():
            assert price > 0, f"{gpu} has non-positive price: {price}"

    def test_price_ordering_reasonable(self):
        """Sanity check: H100 should cost more than T4."""
        pytest.importorskip("torch")
        from forgelm.trainer import ForgeTrainer

        pricing = ForgeTrainer._GPU_PRICING
        assert pricing["NVIDIA H100 80GB HBM3"] > pricing["Tesla T4"]
        assert pricing["NVIDIA A100-SXM4-80GB"] > pricing["Tesla T4"]


class TestCostInJsonOutput:
    def test_json_output_includes_cost(self):
        """Verify _output_result includes cost when present."""
        import io
        import json
        import sys

        from forgelm.cli import _output_result

        r = TrainResult(
            success=True,
            estimated_cost_usd=0.5678,
            resource_usage={"gpu_hours": 0.162, "estimated_cost_usd": 0.5678},
        )

        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            _output_result(r, "json")
        finally:
            sys.stdout = old_stdout

        output = json.loads(captured.getvalue())
        assert output["estimated_cost_usd"] == pytest.approx(0.5678)
        assert output["resource_usage"]["gpu_hours"] == pytest.approx(0.162)

    def test_json_output_omits_cost_when_none(self):
        import io
        import json
        import sys

        from forgelm.cli import _output_result

        r = TrainResult(success=True)

        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            _output_result(r, "json")
        finally:
            sys.stdout = old_stdout

        output = json.loads(captured.getvalue())
        assert "estimated_cost_usd" not in output


class TestApprovalEnvelope:
    """XP-02 / P2-FAB-14: the training JSON envelope must carry an
    ``awaiting_approval`` discriminator so a consumer can tell 'staged, pending
    human sign-off' (exit 4) apart from an ordinary success (exit 0)."""

    @staticmethod
    def _envelope(result):
        import io
        import json
        import sys

        from forgelm.cli import _output_result

        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            _output_result(result, "json")
        finally:
            sys.stdout = old_stdout
        return json.loads(captured.getvalue())

    def test_ordinary_success_envelope_not_awaiting(self):
        out = self._envelope(TrainResult(success=True, final_model_path="/work/out/final_model"))
        assert out["success"] is True
        assert out["awaiting_approval"] is False
        assert "staging_path" not in out

    def test_awaiting_approval_envelope_carries_discriminator_and_staging(self):
        staging = "/work/out/final_model.staging.fg-abc"
        out = self._envelope(
            TrainResult(success=True, awaiting_approval=True, staging_path=staging, final_model_path=staging)
        )
        assert out["success"] is True
        assert out["awaiting_approval"] is True
        assert out["staging_path"] == staging

    def test_reverted_envelope_not_awaiting_and_no_staging(self):
        out = self._envelope(TrainResult(success=False, reverted=True))
        assert out["success"] is False
        assert out["reverted"] is True
        assert out["awaiting_approval"] is False
        assert "staging_path" not in out

    def test_envelope_includes_run_id_and_config_hash_when_populated(self):
        """XP-11 / F-P4-OPUS-15: logging-observability.md rule 2 requires the
        JSON run output to carry run_id + config_hash. The trainer stamps both
        onto TrainResult before _output_result emits the envelope."""
        out = self._envelope(TrainResult(success=True, run_id="fg-abc123def456", config_hash="sha256:cafef00d"))
        assert out["run_id"] == "fg-abc123def456"
        assert out["config_hash"] == "sha256:cafef00d"

    def test_envelope_omits_run_id_and_config_hash_when_absent(self):
        """A hand-built TrainResult (library callers) has no run_id/config_hash;
        the keys are omitted rather than emitted as nulls."""
        out = self._envelope(TrainResult(success=True))
        assert "run_id" not in out
        assert "config_hash" not in out
