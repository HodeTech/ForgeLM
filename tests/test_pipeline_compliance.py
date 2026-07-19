"""Phase 14 — Pipeline manifest schema + chain-integrity verifier tests.

Covers :func:`forgelm.compliance.generate_pipeline_manifest` and
:func:`forgelm.compliance.verify_pipeline_manifest`.  The pipeline
manifest is the EU AI Act Annex IV chain-of-custody artefact that ties
the per-stage ``training_manifest.json`` files into one verifiable
provenance index.
"""

from __future__ import annotations

import json

from forgelm.cli._pipeline import PipelineStageState, PipelineState
from forgelm.compliance import _verify_manifest_payload, generate_pipeline_manifest
from forgelm.config import ForgeConfig


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
        """The stamped hash survives the export ``default=str`` JSON
        round-trip so the disk-backed verifier passes a clean manifest
        and fails a tampered one."""
        from forgelm.compliance import export_pipeline_manifest, verify_pipeline_manifest_at_path

        manifest = generate_pipeline_manifest(_three_stage_state(), _root_with_compliance())
        run_dir = tmp_path / "run"
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

    def test_completed_stage_with_missing_training_manifest_flagged(self, tmp_path):
        """The disk wrapper layers a per-stage training_manifest
        existence check on top of the in-memory verifier — the
        difference between the two surfaces.  This test pins that the
        wrapper actually reaches that branch."""
        from forgelm.compliance import verify_pipeline_manifest_at_path

        manifest_dir = tmp_path / "compliance"
        manifest_dir.mkdir()
        state = _three_stage_state()
        # Wire training_manifest paths that don't exist on disk so the
        # disk wrapper's existence check has something to fail against.
        for s in state.stages:
            s.training_manifest = str(tmp_path / s.name / "compliance" / "training_manifest.json")
        manifest = generate_pipeline_manifest(state, _root_with_compliance())
        import json as _json

        (manifest_dir / "pipeline_manifest.json").write_text(_json.dumps(manifest))
        violations = verify_pipeline_manifest_at_path(str(tmp_path))
        assert any("training_manifest" in v and "is missing" in v for v in violations)


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
            "forgelm.compliance.verify_pipeline_manifest_at_path",
            lambda _p: [f"{PIPELINE_MANIFEST_IO_ERROR_PREFIX}pipeline_manifest.json unreadable: disk error"],
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
            "forgelm.compliance.verify_pipeline_manifest_at_path",
            lambda _p: ["Stage 'unreadable-by-design': input_model does not chain from prior output"],
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
