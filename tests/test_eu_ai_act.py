"""Unit tests for Phase 8: EU AI Act deep compliance features."""

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import yaml

from forgelm.compliance import (
    AuditLogger,
    compute_annex_iv_manifest_hash,
    export_compliance_artifacts,
    export_evidence_bundle,
    generate_deployer_instructions,
    generate_model_integrity,
    generate_training_manifest,
)
from forgelm.config import (
    ComplianceMetadataConfig,
    DataGovernanceConfig,
    ForgeConfig,
    RiskAssessmentConfig,
    load_config,
)

# --- Config Models ---


class TestComplianceMetadataConfig:
    def test_defaults(self):
        c = ComplianceMetadataConfig()
        assert c.provider_name == ""
        assert c.risk_classification == "minimal-risk"

    def test_full(self):
        c = ComplianceMetadataConfig(
            provider_name="Acme Corp",
            intended_purpose="Customer support chatbot",
            risk_classification="high-risk",
        )
        assert c.provider_name == "Acme Corp"
        assert c.risk_classification == "high-risk"


class TestRiskAssessmentConfig:
    def test_defaults(self):
        r = RiskAssessmentConfig()
        assert r.intended_use == ""
        assert r.risk_category == "minimal-risk"
        assert r.foreseeable_misuse == []

    def test_full(self):
        r = RiskAssessmentConfig(
            intended_use="Insurance claim processing",
            risk_category="high-risk",
            foreseeable_misuse=["Medical advice", "Legal advice"],
            mitigation_measures=["Human review required"],
            vulnerable_groups_considered=True,
        )
        assert r.risk_category == "high-risk"
        assert len(r.foreseeable_misuse) == 2


class TestDataGovernanceConfig:
    def test_defaults(self):
        d = DataGovernanceConfig()
        assert d.collection_method == ""
        assert d.personal_data_included is False

    def test_full(self):
        d = DataGovernanceConfig(
            collection_method="Manual curation",
            annotation_process="Two annotators, adjudication",
            known_biases="English-skewed",
            personal_data_included=True,
            dpia_completed=True,
        )
        assert d.personal_data_included is True


class TestForgeConfigCompliance:
    def test_compliance_in_config(self, minimal_config):
        # Wave 3 / Faz 28 (F-compliance-110): high-risk now requires
        # safety eval to be enabled (was a warning prior to v0.5.5).
        # Pair the risk_classification with an enabled safety block so
        # the config validates.
        cfg = ForgeConfig(
            **minimal_config(
                compliance={
                    "provider_name": "Test Corp",
                    "intended_purpose": "Testing",
                    "risk_classification": "high-risk",
                },
                evaluation={"safety": {"enabled": True}},
            )
        )
        assert cfg.compliance.provider_name == "Test Corp"

    def test_risk_assessment_in_config(self, minimal_config):
        cfg = ForgeConfig(
            **minimal_config(
                risk_assessment={
                    "intended_use": "Test use",
                    "risk_category": "limited-risk",
                }
            )
        )
        assert cfg.risk_assessment.risk_category == "limited-risk"

    def test_data_governance_in_config(self, minimal_config):
        cfg = ForgeConfig(
            **minimal_config(
                data={
                    "dataset_name_or_path": "org/dataset",
                    "governance": {"collection_method": "Web scraping"},
                }
            )
        )
        assert cfg.data.governance.collection_method == "Web scraping"

    def test_human_approval_in_eval(self, minimal_config):
        cfg = ForgeConfig(**minimal_config(evaluation={"require_human_approval": True}))
        assert cfg.evaluation.require_human_approval is True

    def test_high_risk_warnings(self, caplog, minimal_config):
        """High-risk classification with safety enabled emits the
        ``auto_revert`` recommendation (still a warning, not a raise —
        F-compliance-110 only escalates the *safety-disabled* branch).
        """
        import logging

        with caplog.at_level(logging.WARNING, logger="forgelm.config"):
            ForgeConfig(
                **minimal_config(
                    risk_assessment={"risk_category": "high-risk"},
                    # Faz 28 F-compliance-110: safety must be enabled
                    # for high-risk to load at all.  The auto_revert
                    # recommendation still fires as a warning since
                    # ``evaluation.auto_revert`` defaults to False.
                    evaluation={"safety": {"enabled": True}},
                )
            )
        assert "high-risk" in caplog.text
        assert "auto_revert" in caplog.text

    @pytest.mark.parametrize("tier", ["high-risk", "unacceptable"])
    def test_strict_tier_safety_disabled_raises_config_error(self, minimal_config, tier):
        """F-compliance-110 + F-W3T-02 regression: BOTH tiers in
        ``_STRICT_RISK_TIERS`` (high-risk + unacceptable) must surface as
        ``ConfigError`` when safety is disabled — pinning the failure leg
        for both Article 9 (high-risk) and Article 5 (unacceptable /
        prohibited) tiers.  EU AI Act risk-management evidence cannot be
        derived from a disabled safety eval; operators who genuinely
        want a sandboxed run must lower the risk_classification."""
        from forgelm.config import ConfigError

        with pytest.raises(ConfigError, match="evaluation.safety.enabled"):
            ForgeConfig(
                **minimal_config(
                    risk_assessment={"risk_category": tier},
                    # No safety block → safety disabled by default.
                )
            )

    @pytest.mark.parametrize(
        "ra,cm",
        [
            ("limited-risk", "high-risk"),
            ("limited-risk", "unacceptable"),
            ("high-risk", "limited-risk"),
            ("unacceptable", "limited-risk"),
        ],
    )
    def test_asymmetric_strict_tier_still_raises_when_safety_disabled(self, minimal_config, ra, cm):
        """F-W3FU-S-01 / F-W3FU-01 regression: ``risk_assessment.risk_category``
        and ``compliance.risk_classification`` are independent
        ``RiskTier`` Literals; Pydantic does not enforce equality.  An
        asymmetric YAML where ONE sibling is strict and the other is
        non-strict must still trip the F-compliance-110 gate — the
        cognitive-complexity refactor that absorbed Sonar python:S3776
        accidentally inverted this OR-across-fields semantics by
        single-label-resolution-then-strict-check, silently bypassing
        the gate when the ``risk_assessment``-first preference picked
        the non-strict sibling."""
        from forgelm.config import ConfigError

        with pytest.raises(ConfigError, match="evaluation.safety.enabled"):
            ForgeConfig(
                **minimal_config(
                    risk_assessment={"risk_category": ra},
                    compliance={
                        "provider_name": "Acme",
                        "system_name": "Bot",
                        "risk_classification": cm,
                    },
                )
            )

    def test_compliance_unacceptable_fires_article_5_banner_even_when_risk_assessment_says_high_risk(
        self, caplog, minimal_config
    ):
        """F-W3FU-01 regression: the Article 5 banner must fire whenever
        EITHER sibling marks the deployment ``unacceptable`` — the
        post-refactor single-label-resolution would have suppressed the
        banner when ``risk_assessment.risk_category="high-risk"`` won
        the preference ordering.  Both fields must contribute to the
        unacceptable check."""
        import logging

        with caplog.at_level(logging.WARNING, logger="forgelm.config"):
            ForgeConfig(
                **minimal_config(
                    risk_assessment={"risk_category": "high-risk"},
                    compliance={
                        "provider_name": "Acme",
                        "system_name": "Bot",
                        "risk_classification": "unacceptable",
                    },
                    evaluation={"safety": {"enabled": True}},
                )
            )
        assert "Article 5" in caplog.text
        assert "prohibited" in caplog.text

    def test_explicit_tier_disagreement_warns(self, caplog, minimal_config):
        """F-P1-FAB-31: when BOTH risk-tier siblings are explicitly set and
        disagree, ForgeLM must warn (compliance.py emits both into the Annex
        IV bundle, so a silent disagreement ships contradictory regulatory
        evidence).  Uses two non-strict tiers so the strict-gate ConfigError
        does not pre-empt the warning."""
        import logging

        # ``_warn_tier_disagreement`` dedupes by tier-pair across the process
        # (F-L-11) so an N-stage pipeline does not spam the warning. That makes
        # this assertion order-dependent: an earlier test that triggered the
        # same (limited-risk, minimal-risk) pair would suppress it here. Clear
        # the dedup set so the warning fires fresh (mirrors test_pipeline_config).
        import forgelm.config as _cfg_module

        _cfg_module._tier_disagreement_warned.clear()
        with caplog.at_level(logging.WARNING, logger="forgelm.config"):
            ForgeConfig(
                **minimal_config(
                    risk_assessment={"risk_category": "limited-risk"},
                    compliance={
                        "provider_name": "Acme",
                        "system_name": "Bot",
                        "risk_classification": "minimal-risk",
                    },
                )
            )
        assert any("Risk tiers disagree" in r.message for r in caplog.records)

    def test_single_tier_set_does_not_warn_disagreement(self, caplog, minimal_config):
        """F-P1-FAB-31: setting only one side must NOT warn — both fields
        default to ``minimal-risk`` on their sub-models, so root-level
        presence alone is not an explicit disagreement."""
        import logging

        with caplog.at_level(logging.WARNING, logger="forgelm.config"):
            ForgeConfig(**minimal_config(risk_assessment={"risk_category": "limited-risk"}))
        assert not any("Risk tiers disagree" in r.message for r in caplog.records)

    def test_unacceptable_risk_warnings(self, caplog, minimal_config):
        """``unacceptable`` (Article 5) must trip the strict gate AND emit
        the dedicated prohibited-practices warning on top of the auto_revert
        nudge.  Closes the runtime-propagation gap CodeRabbit flagged after
        the 3 → 5 RiskTier expansion."""
        import logging

        with caplog.at_level(logging.WARNING, logger="forgelm.config"):
            ForgeConfig(
                **minimal_config(
                    risk_assessment={"risk_category": "unacceptable"},
                    # Faz 28 F-compliance-110: safety required for
                    # unacceptable too — the strict gate covers both
                    # high-risk and unacceptable.
                    evaluation={"safety": {"enabled": True}},
                )
            )
        # Strict gate fires: same auto_revert nudge as high-risk.
        assert "unacceptable" in caplog.text
        assert "auto_revert" in caplog.text
        # Dedicated Article 5 prohibited-practices notice fires too.
        assert "Article 5" in caplog.text
        assert "prohibited" in caplog.text

    def test_minimal_risk_does_not_warn(self, caplog, minimal_config):
        """``minimal-risk`` and ``limited-risk`` must NOT trip the strict
        gate — keeps the friction off operators running unrestricted /
        transparency-tier systems."""
        import logging

        with caplog.at_level(logging.WARNING, logger="forgelm.config"):
            ForgeConfig(
                **minimal_config(
                    risk_assessment={"risk_category": "minimal-risk"},
                )
            )
        assert "auto_revert" not in caplog.text
        assert "Article 5" not in caplog.text

    def test_yaml_round_trip(self, tmp_path, minimal_config):
        data = minimal_config(
            compliance={"provider_name": "Acme", "intended_purpose": "Support"},
            risk_assessment={"intended_use": "Chat", "risk_category": "limited-risk"},
            data={
                "dataset_name_or_path": "org/ds",
                "governance": {"collection_method": "Manual"},
            },
        )
        cfg_path = str(tmp_path / "config.yaml")
        with open(cfg_path, "w") as f:
            yaml.dump(data, f)
        cfg = load_config(cfg_path)
        assert cfg.compliance.provider_name == "Acme"
        assert cfg.risk_assessment.risk_category == "limited-risk"
        assert cfg.data.governance.collection_method == "Manual"


# --- Audit Logger ---


class TestAuditLogger:
    def test_creates_log_file(self, tmp_path):
        audit = AuditLogger(str(tmp_path))
        audit.log_event("test.event", detail="hello")

        log_path = os.path.join(str(tmp_path), "audit_log.jsonl")
        assert os.path.isfile(log_path)

        with open(log_path) as f:
            entry = json.loads(f.readline())
        assert entry["event"] == "test.event"
        assert entry["detail"] == "hello"
        assert "run_id" in entry
        assert "timestamp" in entry

    def test_multiple_events(self, tmp_path):
        audit = AuditLogger(str(tmp_path))
        audit.log_event("event.one")
        audit.log_event("event.two")
        audit.log_event("event.three")

        with open(os.path.join(str(tmp_path), "audit_log.jsonl")) as f:
            lines = f.readlines()
        assert len(lines) == 3

    def test_consistent_run_id(self, tmp_path):
        audit = AuditLogger(str(tmp_path), run_id="test-run-123")
        audit.log_event("event.a")
        audit.log_event("event.b")

        with open(os.path.join(str(tmp_path), "audit_log.jsonl")) as f:
            entries = [json.loads(line) for line in f]
        assert all(e["run_id"] == "test-run-123" for e in entries)


# --- Model Integrity ---


class TestModelIntegrity:
    def test_generates_checksums(self, tmp_path):
        model_dir = tmp_path / "model"
        model_dir.mkdir()
        (model_dir / "weights.bin").write_bytes(b"fake model weights")
        (model_dir / "config.json").write_text('{"key": "value"}')

        integrity = generate_model_integrity(str(model_dir))
        assert len(integrity["artifacts"]) == 2
        assert all("sha256" in a for a in integrity["artifacts"])
        assert all("size_bytes" in a for a in integrity["artifacts"])

    def test_empty_directory(self, tmp_path):
        model_dir = tmp_path / "empty_model"
        model_dir.mkdir()
        integrity = generate_model_integrity(str(model_dir))
        assert integrity["artifacts"] == []

    @pytest.mark.skipif(not hasattr(os, "symlink"), reason="platform without os.symlink support")
    def test_symlinked_file_out_of_tree_is_skipped_not_hashed(self, tmp_path):
        """F-P5-OPUS-08: a symlinked file pointing outside the model tree
        must NOT be hashed/recorded as a model artifact — only the real
        in-tree file appears, and the escape is logged under
        ``skipped_symlinks``."""
        secret_dir = tmp_path / "secret"
        secret_dir.mkdir()
        secret = secret_dir / "id_rsa"
        secret.write_bytes(b"EXTERNAL SECRET")

        model_dir = tmp_path / "model"
        model_dir.mkdir()
        (model_dir / "real.bin").write_bytes(b"weights")
        # Symlinked file pointing OUT of the model tree.
        try:
            os.symlink(secret, model_dir / "leaked.bin")
            # Symlinked DIR pointing out of the tree (os.walk must not recurse).
            os.symlink(secret_dir, model_dir / "leakeddir")
        except (OSError, NotImplementedError):
            pytest.skip("symlink creation not permitted on this platform")

        import hashlib

        external_hash = hashlib.sha256(b"EXTERNAL SECRET").hexdigest()

        integrity = generate_model_integrity(str(model_dir))
        files = {a["file"] for a in integrity["artifacts"]}
        hashes = {a["sha256"] for a in integrity["artifacts"]}

        assert "real.bin" in files
        assert "leaked.bin" not in files, "symlinked file escaped the containment check"
        assert external_hash not in hashes, "external file's content was hashed into the bundle"
        # The omission must be auditable, not silent.
        assert "leaked.bin" in integrity.get("skipped_symlinks", [])


# --- Deployer Instructions ---


class TestFinalizedModelIntegrity:
    """A fresh model bundle must verify, including its Article 13 document."""

    @staticmethod
    def _finalize(tmp_path, monkeypatch, minimal_config, *, require_approval=False, final_model_dir="final_model"):
        from forgelm.results import TrainResult
        from forgelm.trainer import ForgeTrainer

        monkeypatch.setenv("FORGELM_OPERATOR", "test-operator")
        monkeypatch.delenv("FORGELM_AUDIT_SECRET", raising=False)
        source = tmp_path / "tiny.jsonl"
        source.write_text('{"text": "Local training row."}\n', encoding="utf-8")
        output_dir = tmp_path / "output"
        config = ForgeConfig(
            **minimal_config(
                training={"output_dir": str(output_dir), "final_model_dir": final_model_dir},
                data={"dataset_name_or_path": str(source)},
                evaluation={"auto_revert": False, "require_human_approval": require_approval},
            )
        )
        trainer = ForgeTrainer.__new__(ForgeTrainer)
        trainer.config = config
        trainer.checkpoint_dir = str(output_dir)
        trainer.dataset = {"train": [{"text": "Local training row."}]}
        trainer.audit = AuditLogger(str(output_dir), run_id="fg-finalization-test")
        trainer.audit.log_event("pipeline.initialized", trainer_type="sft")
        prefix = Path(trainer.audit.log_path).read_bytes()
        trainer.notifier = MagicMock()
        trainer.run_name = "finalization-test"
        name = f"{final_model_dir}.staging.fg-finalization-test" if require_approval else final_model_dir
        model_dir = output_dir / name
        model_dir.mkdir(parents=True)
        # Stand in for the external model/tokenizer save; all artifact writers are real.
        (model_dir / "adapter_model.safetensors").write_bytes(b"local adapter bytes")
        (model_dir / "tokenizer_config.json").write_text("{}", encoding="utf-8")
        result = TrainResult(success=True, metrics={"train_loss": 0.5}, final_model_path=str(model_dir))
        trainer._finalize_artifacts(str(model_dir), result.metrics, result)
        return trainer, model_dir, prefix, result

    @pytest.mark.parametrize("require_approval", [False, True], ids=["final", "staged"])
    @pytest.mark.parametrize("final_model_dir", ["final_model", "models/final"])
    def test_fresh_bundle_verifies_and_keeps_audit_append_only(
        self, tmp_path, monkeypatch, minimal_config, capsys, require_approval, final_model_dir
    ):
        from forgelm.cli.subcommands._approve import _atomic_rename_or_move
        from forgelm.cli.subcommands._verify_integrity import _run_verify_integrity_cmd
        from forgelm.compliance import verify_audit_log
        from forgelm.verify import verify_integrity

        trainer, model_dir, prefix, result = self._finalize(
            tmp_path, monkeypatch, minimal_config, require_approval=require_approval, final_model_dir=final_model_dir
        )
        manifest = json.loads((model_dir / "model_integrity.json").read_text(encoding="utf-8"))
        assert {entry["file"] for entry in manifest["artifacts"]} == {
            "adapter_model.safetensors",
            "tokenizer_config.json",
            "README.md",
            "deployer_instructions.md",
        }
        audit_path = Path(trainer.audit.log_path)
        audit_before_verification = audit_path.read_bytes()
        assert audit_before_verification.startswith(prefix)
        with pytest.raises(SystemExit) as exc:
            _run_verify_integrity_cmd(SimpleNamespace(path=str(model_dir)), output_format="json")
        assert exc.value.code == 0
        envelope = json.loads(capsys.readouterr().out)
        assert envelope["valid"] is True
        assert envelope["verified_count"] == 4
        assert envelope["changed"] == envelope["removed"] == envelope["added"] == []
        assert audit_path.read_bytes() == audit_before_verification

        if require_approval:
            bundle = {path.name: path.read_bytes() for path in model_dir.iterdir()}
            # model_path records the writer's generation location, not a deployment pointer.
            assert json.loads(bundle["model_integrity.json"])["model_path"] == str(model_dir)
            assert trainer._handle_human_approval_gate(str(model_dir), result, already_saved=True)
            assert result.awaiting_approval is True
            promoted = Path(trainer.config.training.output_dir) / final_model_dir
            assert _atomic_rename_or_move(str(model_dir), str(promoted)) == "rename"
            assert {path.name: path.read_bytes() for path in promoted.iterdir()} == bundle
            assert verify_integrity(str(promoted)).valid is True
            with pytest.raises(SystemExit) as promoted_exit:
                _run_verify_integrity_cmd(SimpleNamespace(path=str(promoted)), output_format="json")
            assert promoted_exit.value.code == 0
            promoted_envelope = json.loads(capsys.readouterr().out)
            assert promoted_envelope["valid"] is True
            assert promoted_envelope["path"] == str(promoted)
        else:
            trainer.audit.log_event("pipeline.completed", success=True)
            assert verify_integrity(str(model_dir)).valid is True

        assert audit_path.read_bytes().startswith(audit_before_verification)
        events = [json.loads(line) for line in audit_path.read_bytes().splitlines()]
        integrity_events = [event for event in events if event["event"] == "model.integrity_verified"]
        assert len(integrity_events) == 1
        assert integrity_events[0]["artifacts"] == 4
        assert verify_audit_log(str(audit_path)).valid is True

    @pytest.mark.parametrize("staged", [False, True])
    def test_real_revert_preserves_run_records_and_siblings(self, tmp_path, monkeypatch, minimal_config, staged):
        from forgelm.compliance import verify_audit_log

        trainer, model_dir, _, _ = self._finalize(
            tmp_path, monkeypatch, minimal_config, require_approval=staged, final_model_dir="models/final"
        )
        sibling = model_dir.parent / "keep"
        sibling.mkdir()
        sentinel = sibling / "sentinel.txt"
        sentinel.write_bytes(b"keep sibling")
        audit_path = Path(trainer.audit.log_path)
        prefix = audit_path.read_bytes()
        compliance = {p: p.read_bytes() for p in (tmp_path / "output/compliance").iterdir()}
        assert trainer._revert_model(str(model_dir), "rejected", source="threshold") is True
        assert not model_dir.exists()
        assert sentinel.read_bytes() == b"keep sibling"
        assert {p: p.read_bytes() for p in compliance} == compliance
        assert audit_path.read_bytes().startswith(prefix)
        assert verify_audit_log(str(audit_path)).valid is True

    @pytest.mark.parametrize("operation", ["save_final_model", "_revert_model", "_finalize_artifacts"])
    @pytest.mark.parametrize("destination", [".", "compliance", "../outside", "alias", "checkpoint-1"])
    def test_runtime_guard_preserves_real_audit_before_write_or_delete(
        self, tmp_path, monkeypatch, minimal_config, operation, destination
    ):
        from forgelm.compliance import verify_audit_log
        from forgelm.config import ConfigError

        trainer, _, _, _ = self._finalize(tmp_path, monkeypatch, minimal_config)
        output = Path(trainer.config.training.output_dir)
        outside = tmp_path / "outside"
        outside.mkdir()
        sentinel = outside / "sentinel.txt"
        sentinel.write_bytes(b"keep outside")
        (output / "alias").symlink_to(outside, target_is_directory=True)
        audit_path = Path(trainer.audit.log_path)
        prefix = audit_path.read_bytes()
        files = {p: p.read_bytes() for p in output.rglob("*") if p.is_file()}
        args = {
            "save_final_model": (str(output / destination),),
            "_revert_model": (str(output / destination), "bad"),
            "_finalize_artifacts": (str(output / destination), {}, None),
        }[operation]
        with pytest.raises(ConfigError, match="final_model_dir"):
            getattr(trainer, operation)(*args)
        assert {p: p.read_bytes() for p in output.rglob("*") if p.is_file()} == files
        assert sentinel.read_bytes() == b"keep outside"
        assert audit_path.read_bytes() == prefix
        assert verify_audit_log(str(audit_path)).valid is True

    def test_trainer_rechecks_symlink_created_after_config_validation(self, tmp_path, minimal_config):
        from forgelm.config import ConfigError
        from forgelm.trainer import ForgeTrainer

        output = tmp_path / "output"
        config = ForgeConfig(**minimal_config(training={"output_dir": str(output)}))
        output.mkdir()
        (output / "final_model").symlink_to(output, target_is_directory=True)
        with pytest.raises(ConfigError, match="final_model_dir"):
            ForgeTrainer(None, None, config, {})
        assert not (output / "audit_log.jsonl").exists()

    @pytest.mark.parametrize("staged", [False, True])
    @pytest.mark.parametrize("operation", ["save_final_model", "_revert_model", "_finalize_artifacts"])
    def test_runtime_benchmark_overlap_preserves_evidence(
        self, tmp_path, monkeypatch, minimal_config, staged, operation
    ):
        from forgelm.config import BenchmarkConfig, ConfigError

        trainer, model_dir, _, _ = self._finalize(tmp_path, monkeypatch, minimal_config, require_approval=staged)
        trainer.trainer = MagicMock()
        trainer.tokenizer = MagicMock()
        benchmark = model_dir / "metrics"
        benchmark.mkdir()
        evidence = benchmark / "benchmark_results.json"
        evidence.write_bytes(b'{"passed": false}')
        trainer.config.evaluation.benchmark = BenchmarkConfig(
            enabled=True, tasks=["arc_easy"], output_dir=str(benchmark)
        )
        output = Path(trainer.checkpoint_dir)
        before = {p: p.read_bytes() for p in output.rglob("*") if p.is_file()}
        args = {
            "save_final_model": (str(model_dir),),
            "_revert_model": (str(model_dir), "rejected"),
            "_finalize_artifacts": (str(model_dir), {}, None),
        }[operation]
        with pytest.raises(ConfigError, match=r"benchmark\.output_dir"):
            getattr(trainer, operation)(*args)
        assert {p: p.read_bytes() for p in output.rglob("*") if p.is_file()} == before

    @pytest.mark.parametrize("change", ["changed", "removed", "added"])
    def test_finalized_bundle_still_reports_integrity_failures(
        self, tmp_path, monkeypatch, minimal_config, capsys, change
    ):
        from forgelm.cli.subcommands._verify_integrity import _run_verify_integrity_cmd

        _, model_dir, _, _ = self._finalize(tmp_path, monkeypatch, minimal_config)
        document = model_dir / "deployer_instructions.md"
        if change == "changed":
            document.write_text(document.read_text(encoding="utf-8") + "\nChanged.\n", encoding="utf-8")
        elif change == "removed":
            document.unlink()
        else:
            (model_dir / "unexpected.txt").write_text("Added.\n", encoding="utf-8")
        with pytest.raises(SystemExit) as exc:
            _run_verify_integrity_cmd(SimpleNamespace(path=str(model_dir)), output_format="json")
        assert exc.value.code == 6
        envelope = json.loads(capsys.readouterr().out)
        assert envelope["valid"] is False
        for category in ("changed", "removed", "added"):
            expected = ["unexpected.txt" if change == "added" else "deployer_instructions.md"]
            assert envelope[category] == (expected if category == change else [])


class TestDeployerInstructions:
    def test_generates_document(self, tmp_path, minimal_config):
        config = ForgeConfig(
            **minimal_config(
                compliance={"provider_name": "TestCo", "intended_purpose": "Customer support"},
            )
        )
        final_path = str(tmp_path / "model")
        doc_path = generate_deployer_instructions(config, {"eval_loss": 0.5}, final_path)
        assert os.path.isfile(doc_path)

        content = open(doc_path).read()
        assert "TestCo" in content
        assert "Customer support" in content
        # Metric names go through _sanitize_md, which CommonMark-escapes the
        # underscore. Stripping backslashes recovers the human-readable form
        # for the test (renderers do the same when displaying the document).
        assert "eval_loss" in content.replace("\\", "")

    def test_without_compliance_config(self, tmp_path, minimal_config):
        config = ForgeConfig(**minimal_config())
        final_path = str(tmp_path / "model")
        doc_path = generate_deployer_instructions(config, {}, final_path)
        assert os.path.isfile(doc_path)


# --- Evidence Bundle ---


class TestEvidenceBundle:
    def test_creates_zip(self, tmp_path):
        compliance_dir = tmp_path / "compliance"
        compliance_dir.mkdir()
        (compliance_dir / "report.json").write_text('{"test": true}')
        (compliance_dir / "manifest.yaml").write_text("test: true")

        bundle_path = str(tmp_path / "bundle.zip")
        result = export_evidence_bundle(str(compliance_dir), bundle_path)
        assert os.path.isfile(result)

        import zipfile

        with zipfile.ZipFile(bundle_path) as zf:
            names = zf.namelist()
        assert len(names) == 2

    def test_failure_midway_does_not_publish_torn_zip(self, tmp_path, monkeypatch):
        """F-P4-OPUS-33 / XP-12: an interrupted ZIP build must not leave a
        torn archive at the auditor-facing path (tmp+rename discipline)."""
        import zipfile

        compliance_dir = tmp_path / "compliance"
        compliance_dir.mkdir()
        (compliance_dir / "a.json").write_text("{}")
        (compliance_dir / "b.json").write_text("{}")

        bundle_path = str(tmp_path / "bundle.zip")

        real_write = zipfile.ZipFile.write
        state = {"n": 0}

        def flaky_write(self, *args, **kwargs):
            state["n"] += 1
            if state["n"] == 2:
                raise OSError("disk full")
            return real_write(self, *args, **kwargs)

        monkeypatch.setattr(zipfile.ZipFile, "write", flaky_write)
        with pytest.raises(OSError):
            export_evidence_bundle(str(compliance_dir), bundle_path)
        assert not os.path.exists(bundle_path), "no torn ZIP at the published path"
        assert not os.path.exists(bundle_path + ".tmp"), "no leftover tmp"


class TestComplianceExportAtomicity:
    @staticmethod
    def _manifest():
        return {
            "forgelm_version": "0",
            "generated_at": "now",
            "config_hash": "sha256:abc",
            "model_lineage": {"base_model": "m", "adapter_method": "LoRA r=8"},
            "training_parameters": {"trainer_type": "sft", "epochs": 1},
            "data_provenance": {"primary_dataset": "ds"},
            "evaluation_results": {"metrics": {"eval_loss": 0.5}},
            "risk_assessment": {"intended_use": "x"},
            # annex_iv block present → build_annex_iv_artifact emits the
            # load-bearing annex_iv_metadata.json (5th artifact).
            "annex_iv": {
                "provider_name": "Acme",
                "system_name": "Bot",
                "intended_purpose": "QA",
                "system_version": "1.0",
                "risk_classification": "minimal-risk",
            },
        }

    def test_partial_write_failure_leaves_no_torn_bundle(self, tmp_path, monkeypatch):
        """F-P4-OPUS-10 / XP-12: a mid-export I/O failure must not leave a
        partial Annex IV bundle at the published dir — staging + all-or-nothing
        promotion means the published dir contains the complete set or none."""
        out = str(tmp_path / "compliance")

        real_open = open
        state = {"n": 0}

        def flaky_open(file, mode="r", *args, **kwargs):
            if "w" in mode:
                state["n"] += 1
                if state["n"] == 3:
                    raise OSError("disk full")
            return real_open(file, mode, *args, **kwargs)

        monkeypatch.setattr("builtins.open", flaky_open)
        with pytest.raises(OSError):
            export_compliance_artifacts(self._manifest(), out)

        # The published dir may exist (mkdir runs first) but must contain NO
        # partially-written artefact — never a strict subset.
        published = sorted(os.listdir(out)) if os.path.isdir(out) else []
        assert published == [], f"torn bundle published: {published!r}"

    def test_rollback_failures_are_logged_not_swallowed(self, tmp_path, monkeypatch, caplog):
        """The promotion rollback path must surface its own remove/replace
        failures instead of silently ``pass``-ing them, while still
        propagating the original promotion OSError unchanged. A swallowed
        rollback error can hide a partially restored bundle from auditors."""
        import logging as _logging

        from forgelm import compliance as _compliance

        out = str(tmp_path / "compliance")
        os.makedirs(out)
        # Pre-existing targets so promotion backs each up before overwriting,
        # which exercises the backup-restore rollback loop.
        for name in ("compliance_report.json", "training_manifest.yaml"):
            with open(os.path.join(out, name), "w") as f:
                f.write("{}")

        real_replace = os.replace

        def flaky_replace(src, dst, *args, **kwargs):
            # A promote call moves a file OUT of the staging dir into place;
            # fail the SECOND such promote so the first file is already in
            # ``promoted`` and the rollback loops have work to do.
            if os.path.basename(os.path.dirname(src)).startswith(".export-tmp"):
                flaky_replace.promotes += 1
                if flaky_replace.promotes == 2:
                    raise OSError("disk full during promotion")
            return real_replace(src, dst, *args, **kwargs)

        flaky_replace.promotes = 0

        def boom_remove(path, *args, **kwargs):
            raise OSError("remove blocked during rollback")

        monkeypatch.setattr(_compliance.os, "replace", flaky_replace)
        monkeypatch.setattr(_compliance.os, "remove", boom_remove)

        with caplog.at_level(_logging.ERROR, logger=_compliance.logger.name):
            with pytest.raises(OSError):
                export_compliance_artifacts(self._manifest(), out)

        assert any(
            "Compliance rollback encountered errors" in r.message and r.levelno == _logging.ERROR
            for r in caplog.records
        ), "rollback failure was swallowed instead of logged at ERROR"

    def test_successful_export_promotes_all_artifacts(self, tmp_path):
        out = str(tmp_path / "compliance")
        files = export_compliance_artifacts(self._manifest(), out)
        names = sorted(os.path.basename(p) for p in files)
        assert "compliance_report.json" in names
        assert "annex_iv_metadata.json" in names
        # No staging dir survives.
        leftovers = [n for n in os.listdir(out) if n.startswith(".export-tmp")]
        assert leftovers == []


# --- Training Manifest with Annex IV ---


class TestManifestAnnexIV:
    @pytest.mark.parametrize("env_value", [None, "https://secret.example.com/private"])
    @pytest.mark.parametrize(
        "endpoint_fields,expected",
        [
            ({}, None),
            ({"endpoint": "https://monitor.example.com"}, "https://monitor.example.com"),
            ({"endpoint_env": "MON_URL"}, "$MON_URL"),
            ({"endpoint": "https://monitor.example.com", "endpoint_env": "MON_URL"}, "$MON_URL"),
            ({"endpoint_env": ""}, None),
        ],
    )
    def test_monitoring_declaration_survives_export(
        self, tmp_path, minimal_config, monkeypatch, endpoint_fields, expected, env_value
    ):
        """A plan is labelled as metadata and never resolves endpoint secrets."""
        if env_value is None:
            monkeypatch.delenv("MON_URL", raising=False)
        else:
            monkeypatch.setenv("MON_URL", env_value)
        config = ForgeConfig(
            **minimal_config(
                compliance={"provider_name": "Corp", "system_name": "Bot", "intended_purpose": "Testing"},
                monitoring={
                    "enabled": True,
                    "metrics_export": "prometheus",
                    "alert_on_drift": True,
                    "check_interval_hours": 12,
                    **endpoint_fields,
                },
            )
        )
        manifest = generate_training_manifest(config, {"eval_loss": 0.5})
        declaration = {
            "declarative_only": True,
            "endpoint": expected,
            "metrics_export": "prometheus",
            "alert_on_drift": True,
            "check_interval_hours": 12,
        }
        assert manifest["monitoring"] == declaration
        export_compliance_artifacts(manifest, str(tmp_path))
        report = json.loads((tmp_path / "compliance_report.json").read_text(encoding="utf-8"))
        annex = json.loads((tmp_path / "annex_iv_metadata.json").read_text(encoding="utf-8"))
        assert report["monitoring"] == declaration
        assert annex["monitoring_and_logging"] == declaration
        assert "secret.example.com" not in json.dumps(report)
        assert "secret.example.com" not in json.dumps(annex)
        assert annex["metadata"]["manifest_hash"] == compute_annex_iv_manifest_hash(annex)

    @pytest.mark.parametrize("monitoring", [None, {"enabled": False, "metrics_export": "datadog"}])
    def test_disabled_monitoring_is_not_declared(self, minimal_config, monitoring):
        config = ForgeConfig(**minimal_config(monitoring=monitoring))
        assert "monitoring" not in generate_training_manifest(config, {})

    def test_includes_annex_iv(self, minimal_config):
        config = ForgeConfig(
            **minimal_config(
                compliance={"provider_name": "Corp", "system_name": "Bot", "risk_classification": "high-risk"},
                # Faz 28 F-compliance-110: high-risk requires safety enabled.
                evaluation={"safety": {"enabled": True}},
            )
        )
        manifest = generate_training_manifest(config, {"eval_loss": 0.5})
        assert "annex_iv" in manifest
        assert manifest["annex_iv"]["provider_name"] == "Corp"
        assert manifest["annex_iv"]["risk_classification"] == "high-risk"

    def test_includes_risk_assessment(self, minimal_config):
        config = ForgeConfig(
            **minimal_config(
                risk_assessment={"intended_use": "Chat", "risk_category": "high-risk"},
                # Faz 28 F-compliance-110: high-risk requires safety enabled.
                evaluation={"safety": {"enabled": True}},
            )
        )
        manifest = generate_training_manifest(config, {})
        assert "risk_assessment" in manifest
        assert manifest["risk_assessment"]["risk_category"] == "high-risk"

    def test_without_compliance(self, minimal_config):
        config = ForgeConfig(**minimal_config())
        manifest = generate_training_manifest(config, {})
        assert "annex_iv" not in manifest
        assert "risk_assessment" not in manifest

    def test_manifest_includes_config_hash(self, minimal_config):
        """XP-11 / F-P4-OPUS-13: the single-stage manifest must carry a
        ``config_hash`` binding it to the config that produced the run, like
        the multi-stage pipeline's ``pipeline_config_hash``."""
        config = ForgeConfig(**minimal_config())
        manifest = generate_training_manifest(config, {})
        assert manifest["config_hash"].startswith("sha256:")

    def test_manifest_config_hash_changes_on_config_edit(self, minimal_config):
        """A post-training config mutation produces a different digest, so an
        Annex IV manifest reconstructed from an edited config is detectable."""
        config = ForgeConfig(**minimal_config())
        before = generate_training_manifest(config, {})["config_hash"]
        config.training.learning_rate = config.training.learning_rate * 10 + 1.0
        after = generate_training_manifest(config, {})["config_hash"]
        assert before != after

    def test_manifest_includes_run_id_when_supplied(self, minimal_config):
        """XP-11: a supplied ``run_id`` is recorded; omitted by default so
        library callers that don't have one are unaffected."""
        config = ForgeConfig(**minimal_config())
        assert "run_id" not in generate_training_manifest(config, {})
        with_run = generate_training_manifest(config, {}, run_id="fg-test123")
        assert with_run["run_id"] == "fg-test123"


@pytest.mark.real_fingerprint
class TestManifestDatasetRevisionProvenance:
    """Art. 10: the manifest must say how strongly its corpus SHA is evidenced.

    The manifest previously carried a bare ``hf_revision`` obtained from an
    ``HfApi().dataset_info`` call made at manifest time, with no link to the
    ``load_dataset`` that produced the training corpus. A reviewer could not
    tell a verified pin from the repo's current default-branch head.
    """

    @staticmethod
    def _stub_metadata(monkeypatch):
        from forgelm import compliance

        monkeypatch.setattr(compliance, "_fingerprint_hf_metadata", lambda dataset_path, fingerprint: None)

    def test_loaded_revision_reaches_the_manifest_labelled(self, minimal_config, monkeypatch):
        from forgelm import compliance
        from forgelm import data as data_mod

        self._stub_metadata(monkeypatch)
        config = ForgeConfig(**minimal_config())
        sha = "1" * 40
        monkeypatch.setattr(
            data_mod,
            "_RESOLVED_DATASET_REVISIONS",
            {config.data.dataset_name_or_path: sha},
        )

        fp = generate_training_manifest(config, {})["data_provenance"]["fingerprint"]
        assert fp["hf_revision"] == sha
        assert fp["hf_revision_source"] == compliance.REVISION_SOURCE_LOADED

    def test_unloaded_corpus_is_never_claimed_as_verified(self, minimal_config, monkeypatch):
        import huggingface_hub

        from forgelm import compliance
        from forgelm import data as data_mod

        self._stub_metadata(monkeypatch)
        monkeypatch.setattr(data_mod, "_RESOLVED_DATASET_REVISIONS", {})

        class _Api:
            def dataset_info(self, dataset_id):
                raise OSError("hub unreachable")

        monkeypatch.setattr(huggingface_hub, "HfApi", _Api)

        config = ForgeConfig(**minimal_config())
        fp = generate_training_manifest(config, {})["data_provenance"]["fingerprint"]
        assert "hf_revision" not in fp
        assert fp["hf_revision_source"] == compliance.REVISION_SOURCE_UNRESOLVED


# ---------------------------------------------------------------------------
# G05 regression tests
# ---------------------------------------------------------------------------


class TestAnnexIVSetSerialisation:
    """F-H-05 regression: annex_iv_metadata.json must be written with
    _manifest_json_default so sets/frozensets serialise as sorted lists,
    matching the hash computed at build time."""

    @staticmethod
    def _manifest_with_set_field():
        """Return a training manifest whose training_parameters carries a Python
        set, simulating a library caller that passes de-duplicated target_modules
        as a set (the documented public-library entry point example)."""
        return {
            "forgelm_version": "0",
            "generated_at": "now",
            "config_hash": "sha256:abc",
            "model_lineage": {"base_model": "m", "adapter_method": "LoRA r=8"},
            "training_parameters": {
                "trainer_type": "sft",
                "epochs": 1,
                # Python set: PYTHONHASHSEED-dependent str() order before fix.
                "target_modules": {"q_proj", "v_proj", "k_proj"},
            },
            "data_provenance": {"primary_dataset": "ds"},
            "evaluation_results": {"metrics": {"eval_loss": 0.5}},
            "annex_iv": {
                "provider_name": "Acme",
                "system_name": "Bot",
                "intended_purpose": "QA",
                "system_version": "1.0",
                "risk_classification": "minimal-risk",
            },
        }

    def test_set_valued_export_verifies_valid(self, tmp_path):
        """export_compliance_artifacts with a set-typed field must produce an
        annex_iv_metadata.json whose manifest_hash passes verify_annex_iv_artifact.

        Before F-H-05, json.dump(..., default=str) serialised the set as the
        opaque string "{'q_proj', 'k_proj', 'v_proj'}" (PYTHONHASHSEED order)
        while compute_annex_iv_manifest_hash normalised it to ["k_proj",
        "q_proj", "v_proj"] — a false-tampering verdict on every verify run."""
        from forgelm.verify import verify_annex_iv_artifact

        out = str(tmp_path / "compliance")
        export_compliance_artifacts(self._manifest_with_set_field(), out)

        annex_path = os.path.join(out, "annex_iv_metadata.json")
        assert os.path.isfile(annex_path), "annex_iv_metadata.json was not written"

        result = verify_annex_iv_artifact(annex_path)
        assert result.valid, (
            f"verify_annex_iv_artifact returned invalid after export with set-typed field "
            f"(F-H-05 regression): {result.reason!r}"
        )

    def test_set_on_disk_is_sorted_list_not_str(self, tmp_path):
        """The on-disk target_modules value must be a JSON array (sorted list),
        not the str(set) opaque string.  A string would pass JSON parsing but
        make the hash non-reproducible across PYTHONHASHSEED values."""
        out = str(tmp_path / "compliance")
        export_compliance_artifacts(self._manifest_with_set_field(), out)

        annex_path = os.path.join(out, "annex_iv_metadata.json")
        with open(annex_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)

        target_modules = data.get("system_components", {}).get("training_parameters", {}).get("target_modules")
        assert isinstance(target_modules, list), (
            f"target_modules must be a JSON array on disk, got {type(target_modules).__name__!r}: {target_modules!r}"
        )
        # Sorted deterministically — same order regardless of PYTHONHASHSEED.
        assert target_modules == sorted(target_modules)
