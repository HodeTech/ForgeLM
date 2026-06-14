"""Unit tests for Phase 8: EU AI Act deep compliance features."""

import json
import os

import pytest
import yaml

from forgelm.compliance import (
    AuditLogger,
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
