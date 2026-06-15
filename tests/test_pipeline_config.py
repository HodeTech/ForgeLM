"""Phase 14 — Pydantic schema + inheritance-merge tests.

Covers:

- :class:`forgelm.config.PipelineStage` field validation (name pattern,
  ``extra="forbid"`` rejecting pipeline-only sections).
- :class:`forgelm.config.PipelineConfig` (minimum-1-stage, unique-name
  validator).
- :func:`forgelm.config.merge_pipeline_stage_config` — the section-
  wholesale inheritance rule + auto-chain priority order documented in
  ``docs/roadmap/phase-14-pipeline-chains.md`` Task 2.
- Backward compatibility: a config without a ``pipeline:`` block produces
  ``config.pipeline is None``; an existing single-stage config is
  byte-identical to v0.6.0 after the schema change.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from forgelm.config import (
    ForgeConfig,
    PipelineConfig,
    PipelineStage,
    merge_pipeline_stage_config,
)


def _root_cfg(**overrides):
    """Build a minimal valid root ForgeConfig with sensible defaults."""
    base = {
        "model": {"name_or_path": "org/base"},
        "lora": {"r": 8, "alpha": 16},
        "training": {"trainer_type": "sft", "num_train_epochs": 3, "learning_rate": 2e-5},
        "data": {"dataset_name_or_path": "org/sft_data"},
    }
    base.update(overrides)
    return ForgeConfig(**base)


# ---------------------------------------------------------------------------
# PipelineStage — name + per-section validation
# ---------------------------------------------------------------------------


class TestPipelineStageName:
    @pytest.mark.parametrize(
        "name",
        ["sft_stage", "stage_1", "s", "a" * 32, "abc_def_123"],
    )
    def test_valid_names_accepted(self, name):
        stage = PipelineStage(name=name)
        assert stage.name == name

    @pytest.mark.parametrize(
        "name",
        [
            "",  # empty
            "a" * 33,  # > 32 chars
            "Stage1",  # uppercase
            "stage-1",  # hyphen
            "stage 1",  # space
            "stage.1",  # dot
            "stage/1",  # slash
            "stage@1",  # at-sign
            "stage_!",  # punctuation
        ],
    )
    def test_invalid_names_rejected(self, name):
        with pytest.raises(ValidationError):
            PipelineStage(name=name)

    def test_name_is_required(self):
        with pytest.raises(ValidationError):
            PipelineStage()


class TestPipelineStageExtraForbid:
    """Pipeline-only sections (distributed / webhook / compliance / etc.)
    must not appear inside a stage.  ``extra="forbid"`` makes Pydantic
    reject them with the offending field name in the error.  This is the
    primary defence against operators putting root-only config inside a
    stage by mistake.
    """

    @pytest.mark.parametrize(
        "forbidden_section",
        [
            "distributed",
            "webhook",
            "compliance",
            "risk_assessment",
            "monitoring",
            "retention",
            "synthetic",
            "merge",
            "auth",
            "pipeline",  # no nested pipelines
        ],
    )
    def test_pipeline_only_section_rejected_in_stage(self, forbidden_section):
        with pytest.raises(ValidationError) as exc_info:
            PipelineStage(name="s1", **{forbidden_section: {}})
        # The forbidden section name appears in the error so the operator
        # knows which key to remove.
        assert forbidden_section in str(exc_info.value)


class TestPipelineStageOverrides:
    """All allowed override slots accept their corresponding config block."""

    def test_all_override_slots_default_to_none(self):
        stage = PipelineStage(name="s1")
        assert stage.model is None
        assert stage.lora is None
        assert stage.training is None
        assert stage.data is None
        assert stage.evaluation is None

    def test_model_block_override(self):
        stage = PipelineStage(name="s1", model={"name_or_path": "org/other"})
        assert stage.model is not None
        assert stage.model.name_or_path == "org/other"

    def test_lora_block_override(self):
        stage = PipelineStage(name="s1", lora={"r": 32, "alpha": 64})
        assert stage.lora is not None
        assert stage.lora.r == 32

    def test_training_block_override_requires_trainer_type(self):
        """``trainer_type`` is required by the existing ``TrainingConfig``
        schema; a stage's training block must therefore supply it.  This
        is the Phase 14 spec's "each stage explicitly states its
        alignment paradigm" rule, enforced via Pydantic's existing
        validator rather than a duplicate check."""
        with pytest.raises(ValidationError):
            PipelineStage(name="s1", training={"num_train_epochs": 1})

    def test_data_block_override(self):
        stage = PipelineStage(name="s1", data={"dataset_name_or_path": "org/dpo_prefs"})
        assert stage.data is not None
        assert stage.data.dataset_name_or_path == "org/dpo_prefs"

    def test_evaluation_block_override(self):
        stage = PipelineStage(
            name="s1",
            evaluation={"auto_revert": True, "max_acceptable_loss": 2.0},
        )
        assert stage.evaluation is not None
        assert stage.evaluation.auto_revert is True


# ---------------------------------------------------------------------------
# PipelineConfig — list validators
# ---------------------------------------------------------------------------


class TestPipelineConfig:
    def test_minimum_one_stage_required(self):
        with pytest.raises(ValidationError):
            PipelineConfig(stages=[])

    def test_single_stage_pipeline_accepted(self):
        """A 1-stage pipeline is technically valid (the spec only forbids
        empty pipelines).  Whether operators *should* declare one is a
        documentation matter, not a schema matter."""
        pl = PipelineConfig(stages=[PipelineStage(name="only")])
        assert len(pl.stages) == 1

    def test_multi_stage_pipeline(self):
        pl = PipelineConfig(
            stages=[
                PipelineStage(name="sft_stage"),
                PipelineStage(name="dpo_stage"),
                PipelineStage(name="grpo_stage"),
            ]
        )
        assert [s.name for s in pl.stages] == ["sft_stage", "dpo_stage", "grpo_stage"]

    def test_duplicate_stage_names_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            PipelineConfig(
                stages=[
                    PipelineStage(name="dup"),
                    PipelineStage(name="other"),
                    PipelineStage(name="dup"),
                ]
            )
        assert "Duplicate" in str(exc_info.value)
        assert "dup" in str(exc_info.value)

    def test_extra_keys_rejected(self):
        """``extra="forbid"`` blocks typos like ``stagess`` from being
        silently accepted as ignored fields."""
        with pytest.raises(ValidationError):
            PipelineConfig(stages=[PipelineStage(name="s1")], stagess=[])


# ---------------------------------------------------------------------------
# ForgeConfig.pipeline wiring + backward compatibility
# ---------------------------------------------------------------------------


class TestForgeConfigPipelineField:
    def test_pipeline_defaults_to_none(self):
        cfg = _root_cfg()
        assert cfg.pipeline is None

    def test_pipeline_populated_from_yaml_dict(self):
        cfg = _root_cfg(pipeline={"stages": [{"name": "s1"}, {"name": "s2"}]})
        assert cfg.pipeline is not None
        assert len(cfg.pipeline.stages) == 2

    def test_pipeline_section_round_trips_through_model_dump(self):
        cfg = _root_cfg(pipeline={"stages": [{"name": "s1", "training": {"trainer_type": "dpo"}}]})
        dumped = cfg.model_dump(exclude_none=True)
        assert "pipeline" in dumped
        assert dumped["pipeline"]["stages"][0]["name"] == "s1"

    def test_single_stage_config_byte_identical_without_pipeline(self):
        """A pre-Phase-14 single-stage config (no ``pipeline:`` key)
        must produce a ``ForgeConfig`` indistinguishable from v0.6.0
        for the trainer's purposes — the ``pipeline`` field defaults to
        None and is excluded from ``model_dump(exclude_none=True)``."""
        cfg = _root_cfg()
        dumped = cfg.model_dump(exclude_none=True)
        assert "pipeline" not in dumped


# ---------------------------------------------------------------------------
# merge_pipeline_stage_config — section-wholesale + auto-chain priority
# ---------------------------------------------------------------------------


class TestMergeSectionWholesale:
    def test_stage_with_no_overrides_inherits_root_entirely(self):
        root = _root_cfg()
        stage = PipelineStage(name="s0")
        merged = merge_pipeline_stage_config(root, stage, prev_output_model=None)
        assert merged.model.name_or_path == root.model.name_or_path
        assert merged.lora.r == root.lora.r
        assert merged.training.trainer_type == root.training.trainer_type
        assert merged.training.num_train_epochs == root.training.num_train_epochs
        assert merged.data.dataset_name_or_path == root.data.dataset_name_or_path

    def test_stage_lora_block_wholesale_replaces_root(self):
        root = _root_cfg()
        stage = PipelineStage(name="s1", lora={"r": 64, "alpha": 128})
        merged = merge_pipeline_stage_config(root, stage, prev_output_model="./prev/model")
        # The stage's lora block fully replaces — fields the stage didn't
        # mention fall back to ``LoraConfigModel`` defaults, NOT to the
        # root's ``lora`` block's values.
        assert merged.lora.r == 64
        assert merged.lora.alpha == 128

    def test_stage_training_block_wholesale_replaces_root(self):
        root = _root_cfg()
        stage = PipelineStage(name="s1", training={"trainer_type": "dpo", "num_train_epochs": 1})
        merged = merge_pipeline_stage_config(root, stage, prev_output_model="./prev/model")
        assert merged.training.trainer_type == "dpo"
        assert merged.training.num_train_epochs == 1

    def test_stage_data_block_wholesale_replaces_root(self):
        root = _root_cfg()
        stage = PipelineStage(name="s1", data={"dataset_name_or_path": "org/dpo_prefs"})
        merged = merge_pipeline_stage_config(root, stage, prev_output_model="./prev/model")
        assert merged.data.dataset_name_or_path == "org/dpo_prefs"

    def test_stage_evaluation_block_wholesale_replaces_root(self):
        root = _root_cfg(evaluation={"auto_revert": False})
        stage = PipelineStage(name="s1", evaluation={"auto_revert": True, "max_acceptable_loss": 1.5})
        merged = merge_pipeline_stage_config(root, stage, prev_output_model="./prev/model")
        assert merged.evaluation is not None
        assert merged.evaluation.auto_revert is True
        assert merged.evaluation.max_acceptable_loss == pytest.approx(1.5)

    def test_pipeline_section_stripped_from_merged_config(self):
        """The orchestrator hands the merged ForgeConfig to a single-stage
        ``ForgeTrainer`` that has no awareness of pipelines.  The
        ``pipeline`` block must be absent from the merged config so the
        trainer's lifecycle is byte-identical to a v0.6.0 single-stage
        run."""
        root = _root_cfg(pipeline={"stages": [{"name": "s0"}, {"name": "s1"}]})
        stage = PipelineStage(name="s0")
        merged = merge_pipeline_stage_config(root, stage, prev_output_model=None)
        assert merged.pipeline is None


class TestMergeAutoChainPriorityOrder:
    """The auto-chain resolution rule has four priority levels.  These
    tests pin every level so a future refactor cannot silently change
    the order — operators rely on the documented behaviour for
    ``--input-model`` to actually override an in-config ``model:`` block.
    """

    def test_priority_1_input_model_override_wins_over_everything(self):
        root = _root_cfg()
        stage = PipelineStage(name="s1", model={"name_or_path": "stage/value"})
        merged = merge_pipeline_stage_config(
            root,
            stage,
            prev_output_model="./prev/model",
            input_model_override="cli/override",
        )
        assert merged.model.name_or_path == "cli/override"

    def test_priority_2_explicit_stage_model_disables_auto_chain(self):
        root = _root_cfg()
        stage = PipelineStage(name="s1", model={"name_or_path": "stage/explicit"})
        merged = merge_pipeline_stage_config(root, stage, prev_output_model="./prev/model")
        assert merged.model.name_or_path == "stage/explicit"

    def test_priority_3_auto_chain_to_prev_output_when_no_overrides(self):
        root = _root_cfg()
        stage = PipelineStage(name="s1")
        merged = merge_pipeline_stage_config(root, stage, prev_output_model="./prev/final")
        assert merged.model.name_or_path == "./prev/final"

    def test_priority_4_stage_zero_inherits_root_model(self):
        """Stage 0 of a pipeline (or any stage launched standalone with
        ``--stage <name>`` without a prior output) reads the root's
        ``model.name_or_path`` unchanged."""
        root = _root_cfg()
        stage = PipelineStage(name="s0")
        merged = merge_pipeline_stage_config(root, stage, prev_output_model=None)
        assert merged.model.name_or_path == root.model.name_or_path

    def test_input_model_override_beats_stage_zero_root_value(self):
        """The CLI escape hatch (``--input-model``) must work even on the
        first stage — operators using ``--stage <first_stage>
        --input-model <path>`` to re-run with a different base model."""
        root = _root_cfg()
        stage = PipelineStage(name="s0")
        merged = merge_pipeline_stage_config(
            root,
            stage,
            prev_output_model=None,
            input_model_override="cli/override",
        )
        assert merged.model.name_or_path == "cli/override"


class TestMergePreservesRootOnlyBlocks:
    """The pipeline-level config sections (distributed, webhook,
    compliance, etc.) cannot be overridden per stage by design.  After
    merge, those blocks must come through from the root verbatim.
    """

    def test_root_webhook_survives_merge(self):
        root = _root_cfg(webhook={"url": "https://example.com/hook"})
        stage = PipelineStage(name="s1", training={"trainer_type": "dpo"})
        merged = merge_pipeline_stage_config(root, stage, prev_output_model="./prev/model")
        assert merged.webhook is not None
        assert merged.webhook.url == "https://example.com/hook"

    def test_root_compliance_metadata_survives_merge(self):
        root = _root_cfg(
            compliance={
                "provider_name": "Acme Corp",
                "provider_contact": "compliance@acme.test",
                "system_name": "Pipeline Demo",
                "intended_purpose": "Internal eval",
            }
        )
        stage = PipelineStage(name="s1", training={"trainer_type": "dpo"})
        merged = merge_pipeline_stage_config(root, stage, prev_output_model="./prev/model")
        assert merged.compliance is not None
        assert merged.compliance.provider_name == "Acme Corp"


# ---------------------------------------------------------------------------
# Round-trip: the merge must not re-materialise unset defaults (F-P1-FAB-03)
# ---------------------------------------------------------------------------


class TestMergeRoundTripDefaults:
    """``merge_pipeline_stage_config`` re-validates a dumped root config.

    The dump must exclude *unset* defaults: otherwise an ``evaluation`` block
    dumps the deprecated ``staging_ttl_days=7`` (a field the operator never
    wrote), which on re-validation counts as ``model_fields_set`` and falsely
    raises ``ConfigError`` against a canonical ``retention.staging_ttl_days``.
    """

    def test_merge_preserves_canonical_retention_with_evaluation_block(self):
        root = _root_cfg(
            evaluation={"auto_revert": True},
            retention={"staging_ttl_days": 30},
            pipeline={"stages": [{"name": "dpo_stage", "training": {"trainer_type": "dpo"}}]},
        )
        # No ConfigError, and the canonical retention horizon survives.
        merged = merge_pipeline_stage_config(root, root.pipeline.stages[0])
        assert merged.retention is not None
        assert merged.retention.staging_ttl_days == 30

    def test_merge_does_not_synthesize_retention_or_deprecation_warning(self, recwarn):
        root = _root_cfg(
            evaluation={"auto_revert": True},
            pipeline={"stages": [{"name": "s", "training": {"trainer_type": "dpo"}}]},
        )
        merged = merge_pipeline_stage_config(root, root.pipeline.stages[0])
        # Operator declared no retention block; the round-trip must not invent one.
        assert merged.retention is None
        assert not [w for w in recwarn.list if issubclass(w.category, DeprecationWarning)]

    def test_merge_preserves_canonical_retention_with_stage_evaluation_override(self):
        # Same hazard as above but the ``evaluation`` block lives on the *stage*,
        # exercising the stage-override dump (not the root dump).  Without
        # ``exclude_unset=True`` on the stage dump, the override materialises the
        # default ``staging_ttl_days=7`` and falsely conflicts with the canonical
        # ``retention.staging_ttl_days=30`` (F-P1-FAB-03).
        root = _root_cfg(
            retention={"staging_ttl_days": 30},
            pipeline={"stages": [{"name": "dpo_stage", "evaluation": {"auto_revert": True}}]},
        )
        # No ConfigError, the canonical retention horizon survives, and the
        # stage's explicit override is applied.
        merged = merge_pipeline_stage_config(root, root.pipeline.stages[0])
        assert merged.retention is not None
        assert merged.retention.staging_ttl_days == 30
        assert merged.evaluation is not None
        assert merged.evaluation.auto_revert is True
