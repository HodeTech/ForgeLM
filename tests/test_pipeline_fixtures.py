"""Wire the ``tests/fixtures/pipeline/*.yaml`` suite into real assertions.

F-P2-FAB-40: ``docs/guides/pipeline.md`` presents this fixture suite as the
reviewer-facing surface that is "byte-comparable to a golden manifest", but
until this module every fixture was referenced by zero tests — a documentation
claim backed by files nothing loaded. Each fixture encodes a specific
pipeline-config scenario; this module loads them and pins the scenario each one
is named for, so a drift in the loader or the fixtures surfaces in CI.
"""

import os

import pytest

from forgelm.cli._pipeline import _compute_pipeline_config_hash
from forgelm.config import ConfigError, load_config

_FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "pipeline")


def _fixture_path(name: str) -> str:
    return os.path.join(_FIXTURE_DIR, name)


def _read_bytes(name: str) -> bytes:
    with open(_fixture_path(name), "rb") as f:
        return f.read()


# Valid fixtures with the stage count documented in their header comment.
_VALID_FIXTURES = [
    ("minimal_3_stage.yaml", 3),
    ("inheritance_matrix.yaml", 4),
    ("auto_revert_at_stage_2.yaml", 3),
    ("gated_pending_approval.yaml", 2),
    ("stale_state_resume_v1.yaml", 2),
    ("stale_state_resume_v2.yaml", 2),
]


class TestPipelineFixtureSuite:
    @pytest.mark.parametrize("name, expected_stages", _VALID_FIXTURES)
    def test_valid_fixture_loads_with_expected_stage_count(self, name, expected_stages):
        cfg = load_config(_fixture_path(name))
        assert cfg.pipeline is not None, f"{name} must declare a pipeline block"
        assert len(cfg.pipeline.stages) == expected_stages

    def test_every_fixture_is_referenced(self):
        """Guard against the original FAB-40 regression: a new fixture added to
        the suite must be wired into this module, not left orphaned."""
        on_disk = {f for f in os.listdir(_FIXTURE_DIR) if f.endswith(".yaml")}
        referenced = {name for name, _ in _VALID_FIXTURES} | {"invalid_distributed_per_stage.yaml"}
        assert on_disk == referenced, (
            f"Fixtures not wired into a test: {on_disk - referenced}; referenced-but-missing: {referenced - on_disk}"
        )

    def test_invalid_distributed_per_stage_raises_config_error(self):
        """A per-stage ``distributed:`` block is pipeline-level-only and must be
        rejected at load time (mapped to EXIT_CONFIG_ERROR at the CLI)."""
        with pytest.raises(ConfigError):
            load_config(_fixture_path("invalid_distributed_per_stage.yaml"))

    def test_stale_state_pair_yields_distinct_config_hashes(self):
        """The v1/v2 pair differs in one numeric value, so the byte-level config
        hash must differ — this is what arms the ``--resume-from`` stale-state
        guard the fixtures are named for."""
        h1 = _compute_pipeline_config_hash(_read_bytes("stale_state_resume_v1.yaml"))
        h2 = _compute_pipeline_config_hash(_read_bytes("stale_state_resume_v2.yaml"))
        assert h1 != h2
        assert h1.startswith("sha256:") and h2.startswith("sha256:")

    def test_gated_fixture_declares_human_approval(self):
        """The gated fixture's first stage must actually request approval — the
        property the orchestrator's exit-4 behaviour depends on."""
        cfg = load_config(_fixture_path("gated_pending_approval.yaml"))
        first = cfg.pipeline.stages[0]
        assert first.evaluation is not None
        assert first.evaluation.require_human_approval is True
