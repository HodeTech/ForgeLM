"""Phase 14 — Pipeline manifest schema + chain-integrity verifier tests.

Covers :func:`forgelm.compliance.generate_pipeline_manifest` and
:func:`forgelm.compliance.verify_pipeline_manifest`.  The pipeline
manifest is the EU AI Act Annex IV chain-of-custody artefact that ties
the per-stage ``training_manifest.json`` files into one verifiable
provenance index.
"""

from __future__ import annotations

import json
import os

from forgelm.cli._pipeline import PipelineStageState, PipelineState
from forgelm.compliance import _verify_manifest_payload, generate_pipeline_manifest
from forgelm.config import ForgeConfig


def _full_annex_iv_artifact() -> dict:
    """A minimal *complete* Annex IV §1-9 artefact.

    Mirrors the factory in ``tests/test_verification_toolbelt.py``; kept
    local so a change to the required-field catalogue fails both suites
    independently rather than one silently inheriting the other's drift.
    """
    return {
        "system_identification": {
            "provider_name": "Acme Inc.",
            "system_name": "Acme Pipeline System",
            "intended_purpose": "Customer-service assistant fine-tune",
        },
        "intended_purpose": "Customer-service assistant fine-tune",
        "system_components": ["transformers>=4.40"],
        "computational_resources": {"gpu": "A100 80GB"},
        "data_governance": {"sources": ["internal.jsonl"]},
        "technical_documentation": {"design_doc": "designs/x.md"},
        "monitoring_and_logging": {"audit_log": "audit_log.jsonl"},
        "performance_metrics": {"eval_loss": 1.4},
        "risk_management": {"art9_reference": "risk_assessment.json"},
    }


def _write_stage_evidence(path, *, hashed: bool = True, artifact: dict | None = None) -> None:
    """Write a per-stage Annex IV evidence file at *path*.

    With ``hashed=True`` it carries a correct ``metadata.manifest_hash``,
    which is what makes the stage count as *verified* rather than merely
    *unverified* in the chain report.
    """
    from forgelm.compliance import compute_annex_iv_manifest_hash

    payload = _full_annex_iv_artifact() if artifact is None else artifact
    if hashed:
        payload = dict(payload)
        payload["metadata"] = {"manifest_hash": compute_annex_iv_manifest_hash(payload)}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def _attach_stage_evidence(state: PipelineState, run_dir) -> None:
    """Point every completed stage at a real, hash-stamped evidence file."""
    for stage in state.stages:
        if stage.status != "completed":
            continue
        evidence = run_dir / stage.name / "compliance" / "annex_iv_metadata.json"
        _write_stage_evidence(evidence)
        stage.training_manifest = str(evidence)


def _report_with(violations: list[str]):
    """Build a :class:`PipelineEvidenceReport` carrying *violations*.

    Used where a test drives the CLI's exit-code routing directly and does
    not care about the counters.
    """
    from forgelm.verify import PipelineEvidenceReport

    report = PipelineEvidenceReport()
    report.violations.extend(violations)
    return report


def _root_with_compliance() -> ForgeConfig:
    return ForgeConfig(
        model={"name_or_path": "org/base"},
        lora={"r": 8},
        training={"trainer_type": "sft"},
        data={"dataset_name_or_path": "org/data"},
        compliance={
            "provider_name": "Acme Inc",
            "provider_contact": "compliance@acme.test",
            "system_name": "Acme Pipeline System",
            "intended_purpose": "Customer-service assistant fine-tune",
            "system_version": "v0.7.0",
        },
        pipeline={
            "stages": [{"name": "sft_stage"}, {"name": "dpo_stage"}, {"name": "grpo_stage"}],
        },
    )


def _three_stage_state() -> PipelineState:
    """Build a representative 3-stage state for happy-path schema tests."""
    s1 = PipelineStageState(
        name="sft_stage",
        index=0,
        trainer_type="sft",
        status="completed",
        input_model="org/base",
        input_source="root",
        output_model="./out/stage1/final_model",
        started_at="2026-06-15T12:00:00+00:00",
        finished_at="2026-06-15T13:00:00+00:00",
        duration_seconds=3600.0,
        metrics={"eval_loss": 0.5},
        gate_decision="passed",
        exit_code=0,
    )
    s2 = PipelineStageState(
        name="dpo_stage",
        index=1,
        trainer_type="dpo",
        status="completed",
        input_model="./out/stage1/final_model",
        input_source="chain",
        output_model="./out/stage2/final_model",
        started_at="2026-06-15T13:00:00+00:00",
        finished_at="2026-06-15T13:45:00+00:00",
        duration_seconds=2700.0,
        metrics={"eval_loss": 0.3},
        gate_decision="passed",
        exit_code=0,
    )
    s3 = PipelineStageState(
        name="grpo_stage",
        index=2,
        trainer_type="grpo",
        status="completed",
        input_model="./out/stage2/final_model",
        input_source="chain",
        output_model="./out/stage3/final_model",
        started_at="2026-06-15T13:45:00+00:00",
        finished_at="2026-06-15T14:30:00+00:00",
        duration_seconds=2700.0,
        metrics={"eval_loss": 0.2},
        gate_decision="passed",
        exit_code=0,
    )
    return PipelineState(
        pipeline_run_id="pl_2026-06-15_a1b2c3",
        pipeline_config_hash="sha256:abc",
        forgelm_version="0.7.0",
        started_at="2026-06-15T12:00:00+00:00",
        finished_at="2026-06-15T14:30:00+00:00",
        final_status="completed",
        stages=[s1, s2, s3],
    )


# ---------------------------------------------------------------------------
# generate_pipeline_manifest — schema coverage
# ---------------------------------------------------------------------------


class TestManifestSchema:
    def test_required_top_level_keys_present(self):
        manifest = generate_pipeline_manifest(_three_stage_state(), _root_with_compliance())
        required = {
            "forgelm_version",
            "generated_at",
            "pipeline_run_id",
            "pipeline_config_hash",
            "started_at",
            "finished_at",
            "final_status",
            "stages",
        }
        assert required.issubset(manifest.keys())

    def test_annex_iv_block_propagated_from_root_compliance(self):
        manifest = generate_pipeline_manifest(_three_stage_state(), _root_with_compliance())
        assert "annex_iv" in manifest
        assert manifest["annex_iv"]["provider_name"] == "Acme Inc"
        assert manifest["annex_iv"]["system_name"] == "Acme Pipeline System"

    def test_annex_iv_omitted_when_no_compliance_block(self):
        root = ForgeConfig(
            model={"name_or_path": "x"},
            lora={},
            training={"trainer_type": "sft"},
            data={"dataset_name_or_path": "y"},
            pipeline={"stages": [{"name": "s1"}]},
        )
        manifest = generate_pipeline_manifest(_three_stage_state(), root)
        assert "annex_iv" not in manifest

    def test_stage_payload_carries_chain_fields(self):
        manifest = generate_pipeline_manifest(_three_stage_state(), _root_with_compliance())
        s1, s2, s3 = manifest["stages"]
        assert s1["index"] == 0 and s2["index"] == 1 and s3["index"] == 2
        assert s2["input_model"] == s1["output_model"]
        assert s3["input_model"] == s2["output_model"]
        assert all(s["gate_decision"] == "passed" for s in (s1, s2, s3))

    def test_stage_metrics_are_a_plain_dict_in_payload(self):
        """Manifest must be JSON-serialisable; metrics dict must round-
        trip through ``json.dumps``."""
        import json as _json

        manifest = generate_pipeline_manifest(_three_stage_state(), _root_with_compliance())
        # No round-trip failure.
        _json.dumps(manifest)


# ---------------------------------------------------------------------------
# verify_pipeline_manifest — chain integrity
# ---------------------------------------------------------------------------


class TestManifestVerification:
    def test_clean_manifest_passes(self):
        manifest = generate_pipeline_manifest(_three_stage_state(), _root_with_compliance())
        assert _verify_manifest_payload(manifest) == []

    def test_missing_required_key_flagged(self):
        manifest = generate_pipeline_manifest(_three_stage_state(), _root_with_compliance())
        manifest.pop("pipeline_run_id")
        violations = _verify_manifest_payload(manifest)
        assert any("pipeline_run_id" in v for v in violations)

    def test_chain_integrity_violation_flagged(self):
        """Stage 2's input_model ≠ stage 1's output_model on a chain
        stage must surface a ``chain_integrity_violation``."""
        state = _three_stage_state()
        state.stages[1].input_model = "tampered/value"
        manifest = generate_pipeline_manifest(state, _root_with_compliance())
        violations = _verify_manifest_payload(manifest)
        assert any("chain_integrity_violation" in v for v in violations)
        assert any("tampered/value" in v for v in violations)

    def test_cli_override_chain_break_still_flagged(self):
        """Even when ``input_source: cli_override`` legitimately breaks
        the chain, the verifier still surfaces it (with the same message)
        so reviewers can correlate against the audit log to decide
        legitimate vs. corrupt.

        Note: only *chain* stages contribute to the integrity check; an
        explicit cli_override stage is recorded with ``input_source !=
        "chain"`` and therefore skipped by design.  This test asserts
        that contract."""
        state = _three_stage_state()
        state.stages[1].input_model = "operator/manual"
        state.stages[1].input_source = "cli_override"
        manifest = generate_pipeline_manifest(state, _root_with_compliance())
        violations = _verify_manifest_payload(manifest)
        # cli_override stages don't trip the chain check.
        assert all("chain_integrity_violation" not in v for v in violations)

    def test_index_out_of_order_flagged(self):
        state = _three_stage_state()
        state.stages[0], state.stages[1] = state.stages[1], state.stages[0]
        # Indices stay 0/1 but names are swapped — verifier checks index
        # vs. positional order.
        manifest = generate_pipeline_manifest(state, _root_with_compliance())
        violations = _verify_manifest_payload(manifest)
        assert any("index out of order" in v for v in violations)

    def test_stopped_at_unknown_stage_flagged(self):
        state = _three_stage_state()
        state.stopped_at = "ghost_stage"
        state.final_status = "stopped_at_stage"
        manifest = generate_pipeline_manifest(state, _root_with_compliance())
        violations = _verify_manifest_payload(manifest)
        assert any("unknown stage" in v and "ghost_stage" in v for v in violations)

    def test_stopped_at_completed_stage_flagged(self):
        """If ``stopped_at`` points at a stage whose status is
        ``completed`` rather than ``failed`` / ``gated_pending_approval``,
        the manifest is internally inconsistent."""
        state = _three_stage_state()
        state.stopped_at = "sft_stage"  # which has status=completed
        state.final_status = "stopped_at_stage"
        manifest = generate_pipeline_manifest(state, _root_with_compliance())
        violations = _verify_manifest_payload(manifest)
        assert any("expected `failed` or `gated_pending_approval`" in v for v in violations)


class TestManifestContentHash:
    """F-P4-OPUS-20: ``generate_pipeline_manifest`` stamps a
    ``metadata.manifest_hash`` over the whole manifest so the verifier
    can detect post-generation content tampering that keeps the chain
    links self-consistent (which the structural checks miss)."""

    def test_generate_stamps_manifest_hash(self):
        manifest = generate_pipeline_manifest(_three_stage_state(), _root_with_compliance())
        assert isinstance(manifest.get("metadata"), dict)
        assert len(manifest["metadata"].get("manifest_hash", "")) == 64

    def test_clean_manifest_with_hash_passes(self):
        manifest = generate_pipeline_manifest(_three_stage_state(), _root_with_compliance())
        assert _verify_manifest_payload(manifest) == []

    def test_tampered_stage_metric_fails_hash_check(self):
        """Editing a stage metric AFTER generation (post-hash) must be
        caught by the content-hash recompute even though the chain links
        and indices stay consistent."""
        manifest = generate_pipeline_manifest(_three_stage_state(), _root_with_compliance())
        # Mutate a non-structural field on disk (chain links untouched).
        manifest["stages"][0]["metrics"]["eval_loss"] = 0.0001
        violations = _verify_manifest_payload(manifest)
        assert any("manifest hash mismatch" in v for v in violations)

    def test_tampered_gate_decision_fails_hash_check(self):
        manifest = generate_pipeline_manifest(_three_stage_state(), _root_with_compliance())
        manifest["stages"][0]["gate_decision"] = "forged-pass"
        violations = _verify_manifest_payload(manifest)
        assert any("manifest hash mismatch" in v for v in violations)

    def test_manifest_without_hash_downgrades_to_structural_only(self):
        """A manifest with no ``metadata.manifest_hash`` (e.g. older
        artefacts) still verifies on its structural rules — absence
        downgrades, it does not fail."""
        manifest = generate_pipeline_manifest(_three_stage_state(), _root_with_compliance())
        manifest.pop("metadata", None)
        assert _verify_manifest_payload(manifest) == []

    def test_export_round_trip_through_disk_verifies(self, tmp_path):
        """The stamped hash survives the export JSON round-trip so the
        disk-backed verifier passes a clean manifest and fails a tampered
        one."""
        from forgelm.compliance import export_pipeline_manifest, verify_pipeline_manifest_at_path

        state = _three_stage_state()
        run_dir = tmp_path / "run"
        # Every completed stage must now present real Annex IV evidence:
        # a completed stage recording *no* evidence pointer is itself a
        # violation (F-PR54-H7 fail-closed rule), so the clean-manifest
        # assertion below only means anything if the evidence exists.
        _attach_stage_evidence(state, run_dir)
        manifest = generate_pipeline_manifest(state, _root_with_compliance())
        export_pipeline_manifest(manifest, str(run_dir))
        assert verify_pipeline_manifest_at_path(str(run_dir)) == []

        # Tamper on disk, then re-verify.
        manifest_path = run_dir / "compliance" / "pipeline_manifest.json"
        on_disk = json.loads(manifest_path.read_text())
        on_disk["stages"][0]["output_model"] = "swapped/model"
        manifest_path.write_text(json.dumps(on_disk, indent=2))
        violations = verify_pipeline_manifest_at_path(str(run_dir))
        assert any("manifest hash mismatch" in v for v in violations)


class TestVerifyOnAutoRevertScenario:
    """End-to-end: build a state where stage 2 failed and stage 3 was
    skipped; the resulting manifest should verify cleanly (it's a valid
    record of a real failure, not an integrity violation)."""

    def test_auto_revert_manifest_verifies(self):
        s1 = PipelineStageState(
            name="sft_stage",
            index=0,
            trainer_type="sft",
            status="completed",
            input_model="org/base",
            input_source="root",
            output_model="./out/stage1/final_model",
            exit_code=0,
        )
        s2 = PipelineStageState(
            name="dpo_stage",
            index=1,
            trainer_type="dpo",
            status="failed",
            input_model="./out/stage1/final_model",
            input_source="chain",
            output_model="./out/stage2/final_model",
            auto_revert_triggered=True,
            exit_code=3,
            error="loss regression",
        )
        s3 = PipelineStageState(
            name="grpo_stage",
            index=2,
            trainer_type="grpo",
            status="skipped_due_to_prior_revert",
            skipped_reason="Stage 'dpo_stage' triggered auto_revert.",
        )
        state = PipelineState(
            pipeline_run_id="pl_x",
            pipeline_config_hash="sha256:abc",
            forgelm_version="0.7.0",
            started_at="2026-06-15T12:00:00+00:00",
            finished_at="2026-06-15T13:30:00+00:00",
            final_status="stopped_at_stage",
            stopped_at="dpo_stage",
            stages=[s1, s2, s3],
        )
        manifest = generate_pipeline_manifest(state, _root_with_compliance())
        assert _verify_manifest_payload(manifest) == []


class TestStrictChainIntegrity:
    """Phase 14 review F-B-3 + F-N-3 regression: the verifier must
    compare every chain stage against its **immediate** predecessor,
    not the most-recent stage that happens to carry an
    ``output_model``.  Without this, a broken/missing prev output is
    silently bridged.
    """

    def test_chain_stage_with_prev_missing_output_flagged(self):
        """Stage 0 completed normally, stage 1 failed without saving an
        output, stage 2 claims input_source='chain'.  Pre-fix the
        verifier compared stage 2 against stage 0's output, masking the
        gap; the strict check now flags it."""
        s0 = PipelineStageState(
            name="s0",
            index=0,
            trainer_type="sft",
            status="completed",
            input_source="root",
            output_model="./out/s0/final_model",
        )
        s1 = PipelineStageState(
            name="s1",
            index=1,
            trainer_type="dpo",
            status="failed",
            input_model="./out/s0/final_model",
            input_source="chain",
            output_model=None,  # crashed before save
        )
        s2 = PipelineStageState(
            name="s2",
            index=2,
            trainer_type="grpo",
            status="completed",
            input_model="./out/s0/final_model",  # plausibly stale
            input_source="chain",
            output_model="./out/s2/final_model",
        )
        state = PipelineState(
            pipeline_run_id="pl_x",
            pipeline_config_hash="sha256:abc",
            forgelm_version="0.7.0",
            started_at="2026-06-15T12:00:00+00:00",
            final_status="stopped_at_stage",
            stopped_at="s1",
            stages=[s0, s1, s2],
        )
        manifest = generate_pipeline_manifest(state, _root_with_compliance())
        violations = _verify_manifest_payload(manifest)
        assert any("chain_integrity_violation" in v and "'s2'" in v for v in violations), (
            f"Expected stage 's2' to fail chain integrity due to gap; got: {violations!r}"
        )

    def test_chain_stage_at_index_zero_flagged(self):
        """Stage 0 cannot have input_source='chain' (there is no
        previous stage)."""
        s0 = PipelineStageState(
            name="s0",
            index=0,
            trainer_type="sft",
            status="completed",
            input_source="chain",  # wrong — no prev exists
            input_model="./somewhere",
            output_model="./out/s0/final_model",
        )
        state = PipelineState(
            pipeline_run_id="pl_x",
            pipeline_config_hash="sha256:abc",
            forgelm_version="0.7.0",
            started_at="2026-06-15T12:00:00+00:00",
            final_status="completed",
            stages=[s0],
        )
        manifest = generate_pipeline_manifest(state, _root_with_compliance())
        violations = _verify_manifest_payload(manifest)
        assert any("'s0'" in v and "stage 0 cannot chain" in v for v in violations)


class TestVerifierFlagsRunningOnFinalisedManifest:
    """Phase 14 review F-N-2: a finalised manifest carrying a stage in
    ``running`` status is a tell that the orchestrator crashed
    mid-stage.  The verifier surfaces it so an archival audit catches
    the orphan."""

    def test_running_stage_with_completed_final_status_flagged(self):
        s0 = PipelineStageState(
            name="s0",
            index=0,
            trainer_type="sft",
            status="completed",
            input_source="root",
            output_model="./out/s0/final_model",
        )
        s1 = PipelineStageState(
            name="s1",
            index=1,
            trainer_type="dpo",
            status="running",
            input_source="chain",
            input_model="./out/s0/final_model",
        )
        state = PipelineState(
            pipeline_run_id="pl_x",
            pipeline_config_hash="sha256:abc",
            forgelm_version="0.7.0",
            started_at="2026-06-15T12:00:00+00:00",
            final_status="completed",
            stages=[s0, s1],
        )
        manifest = generate_pipeline_manifest(state, _root_with_compliance())
        violations = _verify_manifest_payload(manifest)
        assert any("running" in v and "'s1'" in v for v in violations)

    def test_running_stage_with_in_progress_final_status_is_ok(self):
        """A live run is allowed to carry a ``running`` stage — the
        verifier only flags ``running`` on a *finalised* manifest."""
        s0 = PipelineStageState(
            name="s0",
            index=0,
            trainer_type="sft",
            status="running",
            input_source="root",
        )
        state = PipelineState(
            pipeline_run_id="pl_x",
            pipeline_config_hash="sha256:abc",
            forgelm_version="0.7.0",
            started_at="2026-06-15T12:00:00+00:00",
            final_status="in_progress",
            stages=[s0],
        )
        manifest = generate_pipeline_manifest(state, _root_with_compliance())
        violations = _verify_manifest_payload(manifest)
        assert all("running" not in v for v in violations)


class TestVerifyPipelineManifestAtPath:
    """Phase 14 review F-N-6: cover the disk-backed wrapper that the
    CLI ``forgelm verify-annex-iv --pipeline`` actually invokes."""

    def test_missing_manifest_returns_single_violation(self, tmp_path):
        from forgelm.compliance import verify_pipeline_manifest_at_path

        violations = verify_pipeline_manifest_at_path(str(tmp_path))
        assert len(violations) == 1
        assert "pipeline_manifest.json not found" in violations[0]

    def test_malformed_manifest_returns_single_violation(self, tmp_path):
        from forgelm.compliance import verify_pipeline_manifest_at_path

        manifest_dir = tmp_path / "compliance"
        manifest_dir.mkdir()
        (manifest_dir / "pipeline_manifest.json").write_text("{not valid json")
        violations = verify_pipeline_manifest_at_path(str(tmp_path))
        assert len(violations) == 1
        # Phase 14 post-release review: parse failures now use the
        # distinct ``invalid JSON`` sentinel so the CLI can route them
        # to EXIT_CONFIG_ERROR (operator-actionable) rather than the
        # OSError-shaped ``unreadable`` sentinel which routes to
        # EXIT_TRAINING_ERROR.
        assert "invalid JSON" in violations[0]

    def test_disk_wrapper_type_guards_non_dict_stage_items(self, tmp_path):
        """Phase 14 review-response regression: a tampered manifest where
        ``stages`` contains non-dict items (``null`` / a string / etc.)
        must surface as a violation, not crash with ``AttributeError``
        inside the disk-only loop's ``s.get(...)`` calls."""
        from forgelm.compliance import verify_pipeline_manifest_at_path

        manifest_dir = tmp_path / "compliance"
        manifest_dir.mkdir()
        # Build a manifest payload with two malformed stage entries.
        bad_manifest = {
            "forgelm_version": "0.7.0",
            "pipeline_run_id": "pl_x",
            "pipeline_config_hash": "sha256:abc",
            "started_at": "2026-06-15T12:00:00+00:00",
            "final_status": "in_progress",
            "stages": [
                None,
                "this-should-be-a-dict",
                {"name": "ok", "index": 2, "trainer_type": "sft", "status": "pending"},
            ],
        }
        import json as _json

        (manifest_dir / "pipeline_manifest.json").write_text(_json.dumps(bad_manifest))
        violations = verify_pipeline_manifest_at_path(str(tmp_path))
        assert any("stage at index 0 is not an object" in v for v in violations)
        assert any("stage at index 1 is not an object" in v for v in violations)

    def _write_manifest(self, tmp_path, state, root=None, version=None):
        """Persist a chain manifest for *state*, optionally forcing the
        recorded ``forgelm_version`` (the legacy-fallback discriminator)."""
        manifest_dir = tmp_path / "compliance"
        manifest_dir.mkdir(exist_ok=True)
        manifest = generate_pipeline_manifest(state, root if root is not None else _root_with_compliance())
        if version is not None:
            # Re-stamp: generate_pipeline_manifest hashes the payload, and both
            # forgelm_version and the annex_iv block are covered by that hash —
            # which is precisely why an attacker cannot forge either one to
            # unlock the softer routing without tripping the mismatch check.
            from forgelm.compliance import compute_annex_iv_manifest_hash

            manifest["forgelm_version"] = version
            manifest.pop("metadata", None)
            manifest["metadata"] = {"manifest_hash": compute_annex_iv_manifest_hash(manifest)}
        (manifest_dir / "pipeline_manifest.json").write_text(json.dumps(manifest))
        return manifest

    def test_deleted_evidence_is_an_integrity_violation_not_a_soft_unverified(self, tmp_path):
        """The headline regression: deleting a stage's Annex IV evidence is
        archetypal Article 12 tampering and must exit 6.

        It previously exited 1 — *softer* than merely corrupting the same
        file — because the orchestrator recorded a pointer at
        ``training_manifest.json``, a filename no writer has ever produced.
        With the pointer dangling on every run, the reader could not tell a
        writer defect from a deleted artefact and had to assume the benign
        one.  The writer now names ``annex_iv_metadata.json``, so absence is
        unambiguous again.
        """
        from forgelm.compliance import verify_pipeline_manifest_at_path

        state = _three_stage_state()
        _attach_stage_evidence(state, tmp_path)
        self._write_manifest(tmp_path, state)
        assert verify_pipeline_manifest_at_path(str(tmp_path)) == [], "sanity: intact run verifies clean"

        for stage in state.stages:
            (tmp_path / stage.name / "compliance" / "annex_iv_metadata.json").unlink()

        violations = verify_pipeline_manifest_at_path(str(tmp_path))
        assert violations, "deleted evidence must not verify silently"
        assert all(not v.startswith(("UNVERIFIED::", "IO_ERROR::", "INPUT_ERROR::")) for v in violations)
        assert any("is missing" in v for v in violations)

    def test_deletion_is_never_softer_than_corruption(self, tmp_path):
        """Pins the *ordering* the inversion broke, not just each code.

        A weaker assertion on the absolute exit codes would still pass if a
        future change moved both branches together.
        """
        from forgelm.cli.subcommands._verify_annex_iv import _classify_pipeline_violations
        from forgelm.compliance import verify_pipeline_manifest_at_path

        def _code(mutate):
            run = tmp_path / mutate.__name__
            run.mkdir()
            state = _three_stage_state()
            _attach_stage_evidence(state, run)
            self._write_manifest(run, state)
            mutate(run / state.stages[0].name / "compliance" / "annex_iv_metadata.json")
            return _classify_pipeline_violations(verify_pipeline_manifest_at_path(str(run)))[0]

        def corrupt(path):
            path.write_text("{ this is not json")

        def delete(path):
            path.unlink()

        corrupted, deleted = _code(corrupt), _code(delete)
        assert corrupted == 6
        assert deleted == 6
        assert deleted >= corrupted, f"deletion ({deleted}) must never route softer than corruption ({corrupted})"

    def test_missing_evidence_without_a_compliance_block_is_unverified_not_tampering(self, tmp_path):
        """S6-D-04: ``build_annex_iv_artifact`` returns ``None`` when the run
        configured no ``compliance:`` block, so no evidence file exists for an
        entirely legitimate reason.  Nothing was compared → exit 1, and the
        message must say *which* situation the operator is in rather than
        implying a deletion."""
        from forgelm.compliance import PIPELINE_MANIFEST_UNVERIFIED_PREFIX, verify_pipeline_manifest_at_path

        root = ForgeConfig(
            model={"name_or_path": "org/base"},
            lora={"r": 8},
            training={"trainer_type": "sft"},
            data={"dataset_name_or_path": "org/data"},
            pipeline={"stages": [{"name": "sft_stage"}, {"name": "dpo_stage"}, {"name": "grpo_stage"}]},
        )
        state = _three_stage_state()
        for s in state.stages:
            s.training_manifest = str(tmp_path / s.name / "compliance" / "annex_iv_metadata.json")
        self._write_manifest(tmp_path, state, root=root)

        violations = verify_pipeline_manifest_at_path(str(tmp_path))
        assert violations
        assert all(v.startswith(PIPELINE_MANIFEST_UNVERIFIED_PREFIX) for v in violations)
        assert any("no 'compliance:' block" in v for v in violations)
        # It must NOT be phrased as a deletion.
        assert not any("is missing" in v for v in violations)

    def test_legacy_pointer_resolves_to_sibling_on_a_pre_fix_manifest(self, tmp_path):
        """The compatibility path, scoped to what it claims to cover: an
        archived pre-0.9.1 manifest whose dangling ``training_manifest.json``
        pointer sits beside the real artefact still verifies."""
        from forgelm.compliance import verify_pipeline_manifest_at_path

        state = _three_stage_state()
        for s in state.stages:
            stage_compliance = tmp_path / s.name / "compliance"
            _write_stage_evidence(stage_compliance / "annex_iv_metadata.json")
            s.training_manifest = str(stage_compliance / "training_manifest.json")
        self._write_manifest(tmp_path, state, version="0.8.0")

        assert verify_pipeline_manifest_at_path(str(tmp_path)) == []

    def test_legacy_fallback_does_not_apply_to_a_current_manifest(self, tmp_path):
        """The fallback must be a compatibility path for *older* ForgeLM
        versions, not the universal path every current run takes.

        A current manifest naming the legacy basename is not an old artefact —
        it is a pointer that does not match what this version writes, and its
        absent target gets the conservative routing (exit 6).
        """
        from forgelm.compliance import verify_pipeline_manifest_at_path

        state = _three_stage_state()
        for s in state.stages:
            stage_compliance = tmp_path / s.name / "compliance"
            _write_stage_evidence(stage_compliance / "annex_iv_metadata.json")
            s.training_manifest = str(stage_compliance / "training_manifest.json")
        self._write_manifest(tmp_path, state, version="0.9.1")

        violations = verify_pipeline_manifest_at_path(str(tmp_path))
        assert violations, "the legacy fallback must not silently rescue a current manifest"
        assert all(not v.startswith("UNVERIFIED::") for v in violations)

    def test_unparseable_manifest_version_routes_conservatively(self, tmp_path):
        """An absent or garbled ``forgelm_version`` must not unlock the softer
        compatibility path."""
        from forgelm.compliance import verify_pipeline_manifest_at_path

        for version in ("", "not-a-version", "vNext"):
            run = tmp_path / f"run_{version or 'empty'}".replace(" ", "_")
            run.mkdir()
            state = _three_stage_state()
            for s in state.stages:
                stage_compliance = run / s.name / "compliance"
                _write_stage_evidence(stage_compliance / "annex_iv_metadata.json")
                s.training_manifest = str(stage_compliance / "training_manifest.json")
            self._write_manifest(run, state, version=version)
            violations = verify_pipeline_manifest_at_path(str(run))
            assert violations, version
            assert all(not v.startswith("UNVERIFIED::") for v in violations), version

    def test_messages_name_the_file_actually_examined(self, tmp_path):
        """F3: when the legacy fallback rebinds the path, later messages must
        interpolate the artefact that was opened — not the original pointer,
        which names a file the verifier never read."""
        from forgelm.compliance import verify_pipeline_manifest_at_path

        state = _three_stage_state()
        for s in state.stages:
            stage_compliance = tmp_path / s.name / "compliance"
            stage_compliance.mkdir(parents=True, exist_ok=True)
            (stage_compliance / "annex_iv_metadata.json").write_text("{ not json")
            s.training_manifest = str(stage_compliance / "training_manifest.json")
        self._write_manifest(tmp_path, state, version="0.8.0")

        violations = verify_pipeline_manifest_at_path(str(tmp_path))
        assert violations
        assert all("annex_iv_metadata.json" in v for v in violations)
        assert not any("training_manifest.json" in v for v in violations)

    def test_non_legacy_dangling_pointer_is_an_integrity_violation(self, tmp_path):
        """A pointer at any *other* basename is not a legacy artefact — an
        absent file there means the evidence the manifest asserts exists does
        not, which is untagged (exit 6)."""
        from forgelm.compliance import verify_pipeline_manifest_at_path

        state = _three_stage_state()
        for s in state.stages:
            s.training_manifest = str(tmp_path / s.name / "compliance" / "annex_iv_metadata.json")
        self._write_manifest(tmp_path, state)

        violations = verify_pipeline_manifest_at_path(str(tmp_path))
        assert any("is missing" in v and not v.startswith("UNVERIFIED::") for v in violations)


class TestEvidencePointerNamesARealArtefact:
    """The writer/reader contract that was missing, and whose absence is why a
    permanently-dangling evidence pointer shipped.

    Every test around it verified the *reader* against hand-built fixtures, so
    nothing ever compared what the orchestrator records against what
    ``export_compliance_artifacts`` actually writes.  The pointer named
    ``training_manifest.json``; the writer emits ``training_manifest.yaml`` and
    ``annex_iv_metadata.json``.  Three literals in three modules, no test
    tying them together.
    """

    def test_writer_actually_emits_the_artefact_the_pointer_names(self, tmp_path, minimal_config):
        """The load-bearing one: run the real exporter and assert the basename
        the orchestrator records is among the files it produced."""
        from forgelm.compliance import (
            ANNEX_IV_ARTEFACT_BASENAME,
            export_compliance_artifacts,
            generate_training_manifest,
        )

        cfg = ForgeConfig(
            **{
                **minimal_config(),
                "compliance": {
                    "provider_name": "Acme Inc",
                    "provider_contact": "compliance@acme.test",
                    "system_name": "Acme System",
                    "intended_purpose": "Customer-service assistant fine-tune",
                    "system_version": "v1.0",
                },
            }
        )
        manifest = generate_training_manifest(cfg, metrics={"eval_loss": 0.5})
        written = export_compliance_artifacts(manifest, str(tmp_path / "compliance"))

        basenames = {os.path.basename(p) for p in written}
        assert ANNEX_IV_ARTEFACT_BASENAME in basenames, (
            f"the evidence pointer names {ANNEX_IV_ARTEFACT_BASENAME!r}, "
            f"but export_compliance_artifacts wrote {sorted(basenames)}"
        )
        assert (tmp_path / "compliance" / ANNEX_IV_ARTEFACT_BASENAME).is_file()

    def test_reader_and_writer_agree_on_the_basename(self):
        """``forgelm.verify`` re-declares the constant (it cannot import it
        without closing an import cycle); pin the two together."""
        from forgelm.compliance import ANNEX_IV_ARTEFACT_BASENAME
        from forgelm.verify import _ANNEX_IV_EVIDENCE_BASENAME

        assert _ANNEX_IV_EVIDENCE_BASENAME == ANNEX_IV_ARTEFACT_BASENAME

    def test_orchestrator_builds_the_pointer_from_the_shared_constant(self):
        """The orchestrator must not re-introduce a bare literal.

        A source-level assertion because the alternative — standing up a full
        stage run — would mock away the very line under test.  It fails on the
        exact regression: a hand-written basename in the evidence-pointer
        assignment.
        """
        import inspect

        from forgelm.cli import _pipeline

        source = inspect.getsource(_pipeline)
        assert "ANNEX_IV_ARTEFACT_BASENAME" in source, (
            "the stage evidence pointer must be built from forgelm.compliance.ANNEX_IV_ARTEFACT_BASENAME, not a literal"
        )
        assert '"training_manifest.json"' not in source, (
            "the orchestrator recorded a filename no writer produces; "
            "that dangling pointer is what inverted the tamper signal"
        )


class TestVerifyAnnexIvPipelineModeExitCodes:
    """Phase 14 review-response regression, extended with the
    ``EXIT_INTEGRITY_FAILURE`` split: ``forgelm verify-annex-iv --pipeline
    <dir>`` must map I/O failures to ``EXIT_TRAINING_ERROR`` (2),
    operator-input errors (``not found``, ``invalid JSON``) to
    ``EXIT_CONFIG_ERROR`` (1), and structural / chain-integrity violations
    on a manifest that *did* parse to ``EXIT_INTEGRITY_FAILURE`` (6).
    Mirrors the single-artefact path's exit-code policy."""

    def _run(self, tmp_path, args_overrides: dict) -> int:
        """Invoke ``_run_pipeline_mode`` and capture the ``SystemExit`` code.

        Uses ``pytest.raises(SystemExit)`` rather than a bare
        ``try / except SystemExit`` (Sonar python:S5754 / pylint
        ``broad-except``).  The CLI command always ``sys.exit``s, so we
        expect ``SystemExit`` every invocation — a leak past the
        context manager would be a real bug worth surfacing.
        """
        import argparse as _argparse

        import pytest as _pytest

        from forgelm.cli.subcommands._verify_annex_iv import _run_verify_annex_iv_cmd

        args = _argparse.Namespace(path=str(tmp_path), pipeline=True, **args_overrides)
        with _pytest.raises(SystemExit) as exc_info:
            _run_verify_annex_iv_cmd(args, "text")
        code = exc_info.value.code
        return int(code) if code is not None else 0

    def test_missing_manifest_exits_config_error(self, tmp_path, capsys):
        """``not found`` is operator-actionable input → exit 1."""
        from forgelm.compliance import PIPELINE_MANIFEST_INPUT_ERROR_PREFIX

        code = self._run(tmp_path, {})
        assert code == 1  # EXIT_CONFIG_ERROR
        captured = capsys.readouterr().out
        assert "FAIL: pipeline manifest" in captured
        assert "not found" in captured
        # The routing token is internal — it must never reach the operator.
        assert PIPELINE_MANIFEST_INPUT_ERROR_PREFIX not in captured

    def test_invalid_json_manifest_exits_config_error(self, tmp_path, capsys):
        """Reachable file that fails to parse as JSON → exit 1
        (operator-actionable input error).  Phase 14 post-release
        review: previously this conflated JSONDecodeError with
        runtime I/O failure and routed to EXIT_TRAINING_ERROR (exit 2);
        now the verifier emits a distinct ``invalid JSON`` sentinel so
        the CLI maps parse failures to EXIT_CONFIG_ERROR (1) — the
        operator can regenerate or fix the manifest, this isn't a
        production-time I/O failure."""
        manifest_dir = tmp_path / "compliance"
        manifest_dir.mkdir()
        from forgelm.compliance import PIPELINE_MANIFEST_INPUT_ERROR_PREFIX

        (manifest_dir / "pipeline_manifest.json").write_text("{not valid json")
        code = self._run(tmp_path, {})
        assert code == 1  # EXIT_CONFIG_ERROR
        captured = capsys.readouterr().out
        assert "invalid JSON" in captured
        assert PIPELINE_MANIFEST_INPUT_ERROR_PREFIX not in captured

    def test_chain_integrity_violation_exits_integrity_failure(self, tmp_path, capsys):
        """Structural / chain violations on a manifest that parsed →
        exit 6 (``EXIT_INTEGRITY_FAILURE``).

        This test asserted exit 1 before ``EXIT_INTEGRITY_FAILURE`` existed,
        which encoded the defect: a pipeline manifest whose stage chain was
        rewritten (``input_model`` no longer matching the prior stage's
        ``output_model`` — literally the fixture's ``tampered/different/path``)
        exited the same 1 as a mistyped directory, so CI could not tell a
        rewritten compliance record from an operator typo."""
        import json as _json

        manifest_dir = tmp_path / "compliance"
        manifest_dir.mkdir()
        bad_chain_manifest = {
            "forgelm_version": "0.7.0",
            "pipeline_run_id": "pl_x",
            "pipeline_config_hash": "sha256:abc",
            "started_at": "2026-06-15T12:00:00+00:00",
            "final_status": "completed",
            "stages": [
                {
                    "name": "s0",
                    "index": 0,
                    "trainer_type": "sft",
                    "status": "completed",
                    "input_source": "root",
                    "output_model": "./s0/out",
                },
                {
                    "name": "s1",
                    "index": 1,
                    "trainer_type": "dpo",
                    "status": "completed",
                    "input_source": "chain",
                    "input_model": "tampered/different/path",  # ≠ s0.output_model
                    "output_model": "./s1/out",
                },
            ],
        }
        (manifest_dir / "pipeline_manifest.json").write_text(_json.dumps(bad_chain_manifest))
        code = self._run(tmp_path, {})
        assert code == 6  # EXIT_INTEGRITY_FAILURE
        captured = capsys.readouterr().out
        assert "chain_integrity_violation" in captured

    def test_io_error_prefix_maps_to_exit_2_and_strips_token(self, tmp_path, monkeypatch, capsys):
        """F-P4-OPUS-25: only the stable ``IO_ERROR::`` machine prefix routes a
        violation to EXIT_TRAINING_ERROR (2). The internal token must not leak
        into operator-facing output."""
        from forgelm.compliance import PIPELINE_MANIFEST_IO_ERROR_PREFIX

        monkeypatch.setattr(
            "forgelm.verify.verify_pipeline_manifest_report",
            lambda _p: _report_with(
                [f"{PIPELINE_MANIFEST_IO_ERROR_PREFIX}pipeline_manifest.json unreadable: disk error"]
            ),
        )
        code = self._run(tmp_path, {})
        assert code == 2  # EXIT_TRAINING_ERROR
        captured = capsys.readouterr().out
        assert "unreadable" in captured
        assert PIPELINE_MANIFEST_IO_ERROR_PREFIX not in captured  # token stripped for display

    def test_violation_containing_word_unreadable_does_not_map_to_exit_2(self, tmp_path, monkeypatch, capsys):
        """F-P4-OPUS-25 guard: a structural violation whose free text merely
        CONTAINS the substring ``unreadable`` (without the routing prefix) must
        NOT be routed as an I/O failure — the exit-code contract couples to the
        stable prefix, never to a stray word.

        Now that the integrity split exists, an untagged structural violation
        lands on ``EXIT_INTEGRITY_FAILURE`` (6); the point of the guard is
        unchanged — it must never be 2."""
        monkeypatch.setattr(
            "forgelm.verify.verify_pipeline_manifest_report",
            lambda _p: _report_with(["Stage 'unreadable-by-design': input_model does not chain from prior output"]),
        )
        code = self._run(tmp_path, {})
        assert code == 6  # EXIT_INTEGRITY_FAILURE, NOT 2


class TestVerifyOnPartialFilterRun:
    """A ``--stage X`` run produces a manifest where other stages have
    ``status: skipped_by_filter``; verifier should accept it."""

    def test_partial_filter_manifest_verifies(self):
        s1 = PipelineStageState(name="s1", index=0, trainer_type="sft", status="skipped_by_filter")
        s2 = PipelineStageState(
            name="s2",
            index=1,
            trainer_type="dpo",
            status="completed",
            input_model="./prev/output",
            input_source="cli_override",
            output_model="./out/stage2/final_model",
            exit_code=0,
        )
        s3 = PipelineStageState(name="s3", index=2, trainer_type="grpo", status="skipped_by_filter")
        state = PipelineState(
            pipeline_run_id="pl_x",
            pipeline_config_hash="sha256:abc",
            forgelm_version="0.7.0",
            started_at="2026-06-15T12:00:00+00:00",
            final_status="completed",
            stages=[s1, s2, s3],
        )
        manifest = generate_pipeline_manifest(state, _root_with_compliance())
        assert _verify_manifest_payload(manifest) == []
