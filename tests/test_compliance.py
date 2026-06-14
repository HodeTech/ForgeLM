"""Unit tests for Phase 6: safety, judge, compliance, and resource tracking."""

import json
import os
from unittest import mock

import pytest

from forgelm.compliance import (
    _sanitize_md,
    compute_dataset_fingerprint,
    generate_data_governance_report,
    generate_training_manifest,
)
from forgelm.config import ForgeConfig, JudgeConfig, SafetyConfig
from forgelm.judge import JudgeResult
from forgelm.results import TrainResult
from forgelm.safety import SafetyResult

# --- Config models ---


class TestSafetyConfig:
    def test_defaults(self):
        s = SafetyConfig()
        assert s.enabled is False
        assert s.max_safety_regression == pytest.approx(0.05)

    def test_custom(self):
        s = SafetyConfig(enabled=True, classifier="custom/guard", max_safety_regression=0.1)
        assert s.enabled is True
        assert s.classifier == "custom/guard"


class TestJudgeConfig:
    def test_defaults(self):
        j = JudgeConfig()
        assert j.enabled is False
        assert j.judge_model == "gpt-4o"
        assert j.min_score == pytest.approx(5.0)

    def test_local_judge(self):
        j = JudgeConfig(enabled=True, judge_model="/local/judge", judge_api_key_env=None)
        assert j.judge_api_key_env is None


class TestEvaluationWithSafetyJudge:
    def test_eval_config_with_safety(self, minimal_config):
        cfg = ForgeConfig(
            **minimal_config(
                evaluation={
                    "auto_revert": True,
                    "safety": {"enabled": True, "test_prompts": "prompts.jsonl"},
                }
            )
        )
        assert cfg.evaluation.safety.enabled is True

    def test_eval_config_with_judge(self, minimal_config):
        cfg = ForgeConfig(
            **minimal_config(
                evaluation={
                    "llm_judge": {"enabled": True, "min_score": 7.0},
                }
            )
        )
        assert cfg.evaluation.llm_judge.min_score == pytest.approx(7.0)

    def test_eval_config_with_all(self, minimal_config):
        cfg = ForgeConfig(
            **minimal_config(
                evaluation={
                    "auto_revert": True,
                    "max_acceptable_loss": 2.0,
                    "benchmark": {"enabled": True, "tasks": ["arc_easy"]},
                    "safety": {"enabled": True},
                    "llm_judge": {"enabled": True},
                }
            )
        )
        assert cfg.evaluation.benchmark.enabled
        assert cfg.evaluation.safety.enabled
        assert cfg.evaluation.llm_judge.enabled


# --- Result dataclasses ---


class TestSafetyResult:
    def test_passed(self):
        r = SafetyResult(safe_ratio=0.95, total_count=100, unsafe_count=5, passed=True)
        assert r.passed is True

    def test_failed(self):
        r = SafetyResult(
            safe_ratio=0.80, total_count=100, unsafe_count=20, passed=False, failure_reason="Too many unsafe"
        )
        assert r.passed is False


class TestJudgeResult:
    def test_passed(self):
        r = JudgeResult(average_score=7.5, passed=True)
        assert r.passed is True

    def test_failed(self):
        r = JudgeResult(average_score=3.0, passed=False, failure_reason="Below threshold")
        assert r.passed is False


class TestTrainResultPhase6:
    def test_resource_usage(self):
        r = TrainResult(success=True, resource_usage={"gpu_hours": 2.4, "peak_vram_gb": 22.1})
        assert r.resource_usage["gpu_hours"] == pytest.approx(2.4)

    def test_safety_and_judge(self):
        r = TrainResult(success=True, safety_passed=True, judge_score=8.5)
        assert r.safety_passed is True
        assert r.judge_score == pytest.approx(8.5)


# --- Compliance ---


class TestDatasetFingerprint:
    def test_local_file(self, tmp_path):
        test_file = tmp_path / "data.jsonl"
        test_file.write_text('{"prompt": "hello"}\n')
        fp = compute_dataset_fingerprint(str(test_file))
        assert "sha256" in fp
        assert fp["size_bytes"] > 0

    def test_hub_dataset(self):
        with (
            mock.patch("forgelm.compliance._fingerprint_hf_metadata"),
            mock.patch("forgelm.compliance._fingerprint_hf_revision"),
        ):
            fp = compute_dataset_fingerprint("HuggingFaceH4/ultrachat_200k")
        assert fp["source"] == "huggingface_hub"
        assert fp["dataset_id"] == "HuggingFaceH4/ultrachat_200k"


class TestTrainingManifest:
    def test_generate_manifest(self, minimal_config):
        cfg = ForgeConfig(**minimal_config())
        manifest = generate_training_manifest(cfg, metrics={"eval_loss": 0.5})
        assert manifest["model_lineage"]["base_model"] == "org/model"
        assert manifest["training_parameters"]["trainer_type"] == "sft"
        assert manifest["data_provenance"]["primary_dataset"] == "org/dataset"
        assert manifest["evaluation_results"]["metrics"]["eval_loss"] == pytest.approx(0.5)

    def test_manifest_with_resource_usage(self, minimal_config):
        cfg = ForgeConfig(**minimal_config())
        manifest = generate_training_manifest(
            cfg,
            metrics={"eval_loss": 0.5},
            resource_usage={"gpu_hours": 1.5, "peak_vram_gb": 16.0},
        )
        assert manifest["resource_usage"]["gpu_hours"] == pytest.approx(1.5)


class TestComplianceExport:
    def test_export_creates_files(self, tmp_path, minimal_config):
        from forgelm.compliance import export_compliance_artifacts

        cfg = ForgeConfig(**minimal_config())
        manifest = generate_training_manifest(cfg, metrics={"eval_loss": 0.5})
        output_dir = str(tmp_path / "compliance")
        files = export_compliance_artifacts(manifest, output_dir)
        assert len(files) == 3
        assert all(os.path.isfile(f) for f in files)
        # Verify JSON is valid
        with open(files[0]) as f:
            data = json.load(f)
        assert "model_lineage" in data


class TestComplianceExportAuditTrail:
    """F-P4-OPUS-11 / XP-12: a failed/torn compliance export must leave an
    append-only audit trace, and the rollup 'exported' event must fire even
    when the secondary Article-10 governance report fails."""

    def _make_trainer(self, tmp_path, minimal_config):
        from unittest.mock import MagicMock

        from forgelm.compliance import AuditLogger, compute_config_hash
        from forgelm.trainer import ForgeTrainer

        output_dir = tmp_path / "out"
        output_dir.mkdir()
        config = ForgeConfig(**minimal_config())

        with mock.patch("forgelm.trainer.WebhookNotifier"):
            trainer = ForgeTrainer.__new__(ForgeTrainer)
        trainer.config = config
        trainer.dataset = {"train": ["dummy"]}
        trainer.checkpoint_dir = str(output_dir)
        trainer.notifier = MagicMock()
        trainer.audit = AuditLogger(str(output_dir))
        trainer._config_hash = compute_config_hash(config)
        trainer._original_batch_size = config.training.per_device_train_batch_size
        trainer._original_grad_accum = config.training.gradient_accumulation_steps
        return trainer, output_dir

    def _events(self, output_dir):
        with open(output_dir / "audit_log.jsonl", encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def test_compliance_export_failure_emits_audit_event(self, tmp_path, minimal_config):
        trainer, output_dir = self._make_trainer(tmp_path, minimal_config)
        result = TrainResult(success=True, metrics={"eval_loss": 0.5})

        with mock.patch(
            "forgelm.compliance.export_compliance_artifacts",
            side_effect=OSError("disk full"),
        ):
            # Best-effort: the outer catch must swallow the error but record it.
            trainer._export_compliance_if_needed({"eval_loss": 0.5}, result)

        events = {e["event"] for e in self._events(output_dir)}
        assert "compliance.artifacts_export_failed" in events
        assert "compliance.artifacts_exported" not in events

    def test_artifacts_exported_event_fires_even_when_governance_fails(self, tmp_path, minimal_config):
        trainer, output_dir = self._make_trainer(tmp_path, minimal_config)
        result = TrainResult(success=True, metrics={"eval_loss": 0.5})

        with mock.patch(
            "forgelm.compliance.generate_data_governance_report",
            side_effect=ValueError("schema drift"),
        ):
            trainer._export_compliance_if_needed({"eval_loss": 0.5}, result)

        events = [e for e in self._events(output_dir)]
        kinds = {e["event"] for e in events}
        assert "compliance.governance_failed" in kinds
        # The Article-11 manifest export succeeded → its rollup must be logged
        # even though the secondary Article-10 governance report failed.
        exported = [e for e in events if e["event"] == "compliance.artifacts_exported"]
        assert len(exported) == 1
        assert exported[0]["governance_ok"] is False


class TestAuditLoggerWindowsLockDocClaim:
    """XP-09 / F-P4-OPUS-02 / F-P5-OPUS-03: the docs must NOT claim the
    Windows AuditLogger uses ``msvcrt.locking`` while no such implementation
    exists. The Windows flock helper is a documented no-op."""

    def test_code_has_no_msvcrt_lock_implementation(self):
        import pathlib

        forgelm_dir = pathlib.Path(__file__).resolve().parent.parent / "forgelm"
        hits = [p for p in forgelm_dir.rglob("*.py") if "msvcrt" in p.read_text(encoding="utf-8")]
        assert hits == [], f"msvcrt referenced in {hits} — implement the Windows lock or keep the no-op"

    def test_docs_do_not_promise_msvcrt_locking(self):
        import pathlib

        repo = pathlib.Path(__file__).resolve().parent.parent
        offenders = []
        for md in (repo / "docs").rglob("*.md"):
            # The gitignored analysis/ working memory quotes the old (buggy)
            # text verbatim and is not a public doc surface.
            if "analysis/" in md.as_posix():
                continue
            if "msvcrt.locking" in md.read_text(encoding="utf-8"):
                offenders.append(md.relative_to(repo).as_posix())
        assert offenders == [], (
            "docs still claim AuditLogger uses msvcrt.locking on Windows, but the "
            f"code has no such implementation: {offenders}"
        )


# --- AuditLogger hash chain ---


class TestAuditLoggerHashChain:
    def test_restores_hash_chain_on_second_instance(self, tmp_path):
        """A second AuditLogger pointing at the same directory must continue
        the hash chain from the last entry, not reset to 'genesis'."""
        from forgelm.compliance import AuditLogger

        log1 = AuditLogger(str(tmp_path))
        log1.log_event("test.event", key="value")
        hash_after_first_event = log1._prev_hash

        log2 = AuditLogger(str(tmp_path))
        # Must NOT reset to "genesis" — should read from the existing file
        assert log2._prev_hash != "genesis", "Second AuditLogger instance must not reset the hash chain to 'genesis'"
        # The second instance's starting hash is the hash of the last written line,
        # which matches what log1 computed after writing.
        assert log2._prev_hash == hash_after_first_event

    def test_genesis_hash_on_fresh_dir(self, tmp_path):
        """First-ever AuditLogger on a fresh directory starts at 'genesis'."""
        from forgelm.compliance import AuditLogger

        log = AuditLogger(str(tmp_path / "newdir"))
        assert log._prev_hash == "genesis"

    def test_hash_advances_after_each_event(self, tmp_path):
        """Each new log event must advance _prev_hash to a new value."""
        from forgelm.compliance import AuditLogger

        log = AuditLogger(str(tmp_path))
        h0 = log._prev_hash
        log.log_event("event.one")
        h1 = log._prev_hash
        log.log_event("event.two")
        h2 = log._prev_hash

        assert h0 != h1
        assert h1 != h2

    def test_second_writer_reread_under_lock_does_not_fork_chain(self, tmp_path):
        """Two loggers sharing one log file must not fork the chain.

        Writer B captures its in-memory ``_prev_hash`` at __init__ time
        (before writer A appends).  If ``log_event`` appended against that
        stale value instead of re-reading the chain head under the lock,
        the chain would silently fork and ``verify_audit_log`` would fail.
        Regression guard for the re-read-under-lock guarantee
        (F-P4-OPUS-12).
        """
        from forgelm.compliance import AuditLogger, verify_audit_log

        log_path = str(tmp_path / "audit_log.jsonl")

        writer_a = AuditLogger(str(tmp_path))
        writer_b = AuditLogger(str(tmp_path))  # captures _prev_hash == "genesis"

        writer_a.log_event("a.first")
        # B's cached _prev_hash is now stale ("genesis"); the under-lock
        # re-read must override it so B chains onto A's entry.
        writer_b.log_event("b.second")

        result = verify_audit_log(log_path)
        assert result.valid is True, f"Chain forked despite under-lock re-read: {result.reason}"
        assert result.entries_count == 2


class TestAuditLoggerGenesisManifest:
    def test_write_after_truncation_with_stale_manifest_logs_integrity_error(self, tmp_path, caplog):
        """A truncate-to-empty-then-write must surface an AUDIT INTEGRITY error.

        After one event the genesis manifest pins the first-entry hash.
        Truncating the log to empty and constructing a fresh logger that
        writes again must emit the write-time ``AUDIT INTEGRITY`` ERROR
        (the sole write-time truncation detector). Regression guard for
        ``_check_genesis_manifest`` (F-P4-OPUS-12).
        """
        from forgelm.compliance import AuditLogger

        log_path = tmp_path / "audit_log.jsonl"

        AuditLogger(str(tmp_path)).log_event("first.event")
        assert (tmp_path / "audit_log.jsonl.manifest.json").is_file()

        # Truncate the log to empty, leaving the manifest in place.
        log_path.write_text("", encoding="utf-8")

        with caplog.at_level("ERROR", logger="forgelm.compliance"):
            AuditLogger(str(tmp_path)).log_event("second.event")

        assert any("AUDIT INTEGRITY" in rec.message for rec in caplog.records), (
            "write-time stale-genesis-manifest path did not log an AUDIT INTEGRITY error"
        )


# --- _sanitize_md ---


class TestSanitizeMd:
    def test_escapes_pipe(self):
        result = _sanitize_md("hello | world")
        assert "\\|" in result

    def test_strips_newlines(self):
        result = _sanitize_md("line1\nline2")
        assert "\n" not in result

    def test_strips_carriage_returns(self):
        result = _sanitize_md("line1\r\nline2")
        assert "\r" not in result

    def test_empty_string_returns_not_specified(self):
        result = _sanitize_md("")
        assert result == "Not specified"

    def test_none_returns_not_specified(self):
        result = _sanitize_md(None)
        assert result == "Not specified"

    def test_normal_text_unchanged(self):
        result = _sanitize_md("Hello world")
        assert result == "Hello world"

    def test_multiple_pipes_all_escaped(self):
        result = _sanitize_md("a | b | c")
        assert result.count("\\|") == 2


class TestGovernanceAuditInlining:
    """Bug 6: Article 10 governance auto-inlines data_audit_report.json
    from training output_dir; missing-file path emits a clear hint."""

    def test_inlines_audit_when_present(self, tmp_path, minimal_config):
        config = ForgeConfig(**minimal_config(training={"output_dir": str(tmp_path)}))
        audit_payload = {
            "generated_at": "2026-04-27T00:00:00Z",
            "total_samples": 42,
            "pii_summary": {"email": 1},
        }
        with open(tmp_path / "data_audit_report.json", "w", encoding="utf-8") as fh:
            json.dump(audit_payload, fh)

        report = generate_data_governance_report(config, dataset={})
        assert report["data_audit"] == audit_payload

    def test_warns_when_audit_corrupt(self, tmp_path, caplog, minimal_config):
        config = ForgeConfig(**minimal_config(training={"output_dir": str(tmp_path)}))
        # Malformed JSON should NOT abort governance generation; the
        # report carries no data_audit key + a warning is logged.
        (tmp_path / "data_audit_report.json").write_text("{not valid json", encoding="utf-8")
        with caplog.at_level("WARNING", logger="forgelm.compliance"):
            report = generate_data_governance_report(config, dataset={})
        assert "data_audit" not in report
        assert any("Could not inline" in r.message for r in caplog.records)

    def test_warning_log_when_audit_missing(self, tmp_path, caplog, minimal_config):
        # The audit CLI defaults to ./audit/ but the trainer's
        # output_dir is typically ./checkpoints/ — without alignment
        # the inlining silently no-ops.
        #
        # Wave 3 / Faz 28 (F-compliance-111): escalated from INFO to
        # WARNING.  A missing data_audit_report.json is a real Article
        # 10 compliance gap (the governance bundle ships without its
        # data-quality section); INFO-level logs are easy to miss in
        # production tail-grep.
        config = ForgeConfig(**minimal_config(training={"output_dir": str(tmp_path)}))
        with caplog.at_level("WARNING", logger="forgelm.compliance"):
            report = generate_data_governance_report(config, dataset={})
        assert "data_audit" not in report
        warn_msgs = [r.message for r in caplog.records if r.levelname == "WARNING"]
        # Phase 11.5: hint moved from `forgelm --data-audit` (legacy) to the
        # new `forgelm audit` subcommand. Accept either spelling so this test
        # survives the deprecation window, but require the actionable command
        # is named.
        assert any(
            "No data_audit_report.json" in m and ("forgelm audit" in m or "forgelm --data-audit" in m)
            for m in warn_msgs
        )


# ---------------------------------------------------------------------------
# Closure plan Faz 3: operator identity + audit forensics
# ---------------------------------------------------------------------------


def _raise(exc):
    """Helper: raise *exc* — used as a lambda body in monkeypatch fixtures.

    The Pythonic one-liner ``(_ for _ in ()).throw(exc)`` works but trips
    Sonar's "replace comprehension with constructor call" rule (false
    positive on a generator-throw idiom). Wrapping in a named function
    keeps both Sonar and ``ruff`` happy.
    """
    raise exc


class TestAuditLoggerOperatorIdentity:
    """F-compliance-102: ``operator="unknown"`` is no longer a silent fallback."""

    def test_operator_from_forgelm_operator_env(self, tmp_path, monkeypatch):
        """Explicit ``FORGELM_OPERATOR`` wins over every other source."""
        from forgelm.compliance import AuditLogger

        monkeypatch.setenv("FORGELM_OPERATOR", "ci-bot@github-actions")
        log = AuditLogger(str(tmp_path))
        assert log.operator == "ci-bot@github-actions"

    def test_operator_from_getpass_and_hostname(self, tmp_path, monkeypatch):
        """Without ``FORGELM_OPERATOR``, derive ``user@host`` from getpass."""
        from forgelm import compliance

        monkeypatch.delenv("FORGELM_OPERATOR", raising=False)
        monkeypatch.setattr(compliance.getpass, "getuser", lambda: "alice")
        monkeypatch.setattr(compliance.socket, "gethostname", lambda: "workstation-1")

        log = compliance.AuditLogger(str(tmp_path))
        assert log.operator == "alice@workstation-1"

    def test_operator_raises_when_no_identity_no_flag(self, tmp_path, monkeypatch):
        """No env var + getpass failure + no opt-in = ConfigError, not 'unknown'."""
        from forgelm import compliance

        monkeypatch.delenv("FORGELM_OPERATOR", raising=False)
        monkeypatch.delenv("FORGELM_ALLOW_ANONYMOUS_OPERATOR", raising=False)

        def _boom():
            raise OSError("no LOGNAME / USER / pwd entry")

        monkeypatch.setattr(compliance.getpass, "getuser", _boom)
        with pytest.raises(compliance.ConfigError, match="Operator identity unavailable"):
            compliance.AuditLogger(str(tmp_path))

    def test_operator_anonymous_with_flag(self, tmp_path, monkeypatch):
        """Explicit opt-in via FORGELM_ALLOW_ANONYMOUS_OPERATOR=1 -> anonymous@host."""
        from forgelm import compliance

        monkeypatch.delenv("FORGELM_OPERATOR", raising=False)
        monkeypatch.setenv("FORGELM_ALLOW_ANONYMOUS_OPERATOR", "1")
        monkeypatch.setattr(compliance.getpass, "getuser", lambda: _raise(OSError("no user")))
        monkeypatch.setattr(compliance.socket, "gethostname", lambda: "sandbox-host")

        log = compliance.AuditLogger(str(tmp_path))
        assert log.operator == "anonymous@sandbox-host"

    def test_no_unknown_fallback_in_default_path(self, tmp_path, monkeypatch):
        """Belt-and-braces: the literal string 'unknown' must never become
        the operator when the resolution chain succeeds."""
        from forgelm import compliance

        monkeypatch.delenv("FORGELM_OPERATOR", raising=False)
        monkeypatch.setattr(compliance.getpass, "getuser", lambda: "real-user")
        monkeypatch.setattr(compliance.socket, "gethostname", lambda: "real-host")

        log = compliance.AuditLogger(str(tmp_path))
        assert log.operator == "real-user@real-host"
        assert log.operator != "unknown"


class TestAuditLoggerFsync:
    """F-compliance-114: log_event must fsync after flush so chain advance is durable."""

    def test_log_event_calls_fsync(self, tmp_path, monkeypatch):
        from forgelm.compliance import AuditLogger

        log = AuditLogger(str(tmp_path))

        with mock.patch("forgelm.compliance.os.fsync") as mock_fsync:
            log.log_event("test.event", key="value")

        assert mock_fsync.called, "log_event() must invoke os.fsync after flushing the audit line"
        # Called exactly once per event (not per flush call elsewhere in the
        # process); the file descriptor argument is an int from f.fileno().
        assert mock_fsync.call_count == 1
        (fileno_arg,), _ = mock_fsync.call_args
        assert isinstance(fileno_arg, int)


class TestSafetyClassifierLoadFailureAudit:
    """F-compliance-120: classifier load failure surfaces as an audit event."""

    def test_classifier_load_failure_emits_audit_event(self, tmp_path, monkeypatch):
        # We exercise the failure path inside ``run_safety_evaluation`` directly
        # by stubbing the in-function ``transformers.pipeline`` import to raise.
        # No real model / tokenizer / GPU is touched.
        pytest.importorskip("torch")  # safety module imports torch lazily
        import sys
        import types

        from forgelm import safety
        from forgelm.compliance import AuditLogger  # noqa: I001

        # Inject a fake ``transformers`` module so ``from transformers import
        # pipeline`` inside run_safety_evaluation returns our raising stub.
        fake_transformers = types.ModuleType("transformers")
        fake_transformers.pipeline = lambda *a, **kw: _raise(RuntimeError("classifier checkpoint corrupt"))
        monkeypatch.setitem(sys.modules, "transformers", fake_transformers)

        # Stub out generation + GPU release so the function reaches the
        # classifier-load branch without needing a real model.
        monkeypatch.setattr(safety, "_generate_safety_responses", lambda *a, **k: ["resp"])
        monkeypatch.setattr(safety, "_release_model_from_gpu", lambda m: None)

        prompts_path = tmp_path / "prompts.jsonl"
        prompts_path.write_text(json.dumps({"prompt": "hi"}) + "\n")

        audit = AuditLogger(str(tmp_path))
        result = safety.run_safety_evaluation(
            model=mock.Mock(),
            tokenizer=mock.Mock(),
            classifier_path="meta-llama/Llama-Guard-3-8B",
            test_prompts_path=str(prompts_path),
            audit_logger=audit,
        )

        assert result.passed is False
        # Read the audit log and verify the event landed with the expected payload.
        with open(audit.log_path, "r", encoding="utf-8") as fh:
            lines = [json.loads(line) for line in fh if line.strip()]
        events = [entry["event"] for entry in lines]
        assert "audit.classifier_load_failed" in events
        load_failed = next(e for e in lines if e["event"] == "audit.classifier_load_failed")
        assert load_failed["classifier"] == "meta-llama/Llama-Guard-3-8B"
        assert "classifier checkpoint corrupt" in load_failed["reason"]


class TestHFRevisionPin:
    """F-compliance-117: dataset fingerprint pins HF Hub revision SHA."""

    def test_hf_revision_pinned_in_fingerprint(self, monkeypatch):
        # Simulate ``huggingface_hub.HfApi().dataset_info`` returning a
        # commit-pinned info object. We patch the import target so the
        # in-function ``from huggingface_hub import HfApi`` resolves here.
        import sys
        import types

        from forgelm import compliance

        class _FakeInfo:
            sha = "abc123def456" + "0" * 28  # plausible-looking 40-char SHA

        class _FakeHfApi:
            def dataset_info(self, dataset_id):
                return _FakeInfo()

        fake_module = types.ModuleType("huggingface_hub")
        fake_module.HfApi = _FakeHfApi
        monkeypatch.setitem(sys.modules, "huggingface_hub", fake_module)

        # Also stub ``load_dataset_builder`` so the version-fetch arm does
        # not hit the network or fail noisily.
        fake_datasets = types.ModuleType("datasets")

        class _FakeBuilder:
            class info:
                version = None
                description = None
                download_size = None

        fake_datasets.load_dataset_builder = lambda path: _FakeBuilder()
        monkeypatch.setitem(sys.modules, "datasets", fake_datasets)

        fp = compliance.compute_dataset_fingerprint("HuggingFaceH4/ultrachat_200k")

        assert fp["source"] == "huggingface_hub"
        assert fp["dataset_id"] == "HuggingFaceH4/ultrachat_200k"
        assert fp["hf_revision"] == _FakeInfo.sha


# ---------------------------------------------------------------------------
# Closure plan Faz 6: verify_audit_log library function + verify-audit CLI
# ---------------------------------------------------------------------------


class TestVerifyAuditLog:
    """Closure plan Faz 6: ``forgelm.compliance.verify_audit_log`` library
    function and its ``forgelm verify-audit`` CLI counterpart.

    Each test exercises the real :class:`AuditLogger` as the writer so
    these are integration-style — any drift between the writer's
    canonicalisation and the verifier would surface here immediately.
    """

    @staticmethod
    def _build_log(tmp_path, *, secret: str = "", events: int = 3):
        """Write a fresh audit log under *tmp_path* and return its path.

        AuditLogger reads ``FORGELM_AUDIT_SECRET`` at ``__init__`` time, so
        we toggle the env var around the constructor call. ``try/finally``
        guarantees the env var is restored even if AuditLogger or
        ``log_event`` raises — without this guard a failed test could leak
        ``FORGELM_AUDIT_SECRET=...`` into adjacent tests and silently
        change their HMAC behaviour.
        """
        from forgelm.compliance import AuditLogger

        prior = os.environ.get("FORGELM_AUDIT_SECRET")
        if secret:
            os.environ["FORGELM_AUDIT_SECRET"] = secret
        else:
            os.environ.pop("FORGELM_AUDIT_SECRET", None)

        try:
            logger = AuditLogger(str(tmp_path))
            for i in range(events):
                logger.log_event(f"event.{i}", index=i, payload={"step": i})
            return logger.log_path
        finally:
            # Restore the prior state — pop if it wasn't set, otherwise
            # restore the original value.
            if prior is None:
                os.environ.pop("FORGELM_AUDIT_SECRET", None)
            else:
                os.environ["FORGELM_AUDIT_SECRET"] = prior

    def test_verify_audit_valid_chain(self, tmp_path):
        from forgelm.compliance import verify_audit_log

        log_path = self._build_log(tmp_path, events=5)
        result = verify_audit_log(log_path)
        assert result.valid is True
        assert result.entries_count == 5
        assert result.first_invalid_index is None
        assert result.reason is None

    def test_verify_audit_tampered_line(self, tmp_path):
        """Modify one entry's payload after the fact; chain must break at
        the *next* line (whose prev_hash no longer matches the rewritten
        line's SHA-256)."""
        from forgelm.compliance import verify_audit_log

        log_path = self._build_log(tmp_path, events=4)
        with open(log_path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()

        # Tamper with line 2 (index 1): re-encode with a flipped value.
        entry = json.loads(lines[1])
        entry["payload"] = {"step": 99999}
        lines[1] = json.dumps(entry, default=str) + "\n"
        with open(log_path, "w", encoding="utf-8") as fh:
            fh.writelines(lines)

        result = verify_audit_log(log_path)
        assert result.valid is False
        # The tamper changes line 2's hash, so the *first* observable
        # break is at line 3 — its prev_hash no longer matches.
        assert result.first_invalid_index == 3
        assert "chain broken" in (result.reason or "")

    def test_verify_audit_truncated_chain(self, tmp_path):
        """Delete the genesis line: the manifest sidecar still pins the
        original first_entry_sha256, so verification surfaces the
        truncation as a manifest mismatch."""
        from forgelm.compliance import verify_audit_log

        log_path = self._build_log(tmp_path, events=4)
        with open(log_path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()

        # Drop the first line (truncate-from-head simulates an attacker
        # who removed the genesis entry to hide an event).
        with open(log_path, "w", encoding="utf-8") as fh:
            fh.writelines(lines[1:])

        result = verify_audit_log(log_path)
        assert result.valid is False
        # Either the chain breaks at line 1 (prev_hash mismatch — the new
        # first line carries the *old* line-1 hash, not "genesis") OR the
        # manifest cross-check fires. Both indicate truncation; assert on
        # the line index rather than the message text to stay robust.
        assert result.first_invalid_index == 1
        assert result.reason is not None

    def test_verify_audit_truncated_to_empty_fails(self, tmp_path):
        """C6/F-P4-OPUS-01: truncating the log to ZERO entries while the genesis
        manifest pins a real first entry must FAIL verification.  Pre-fix
        ``verify_audit_log`` early-returned ``valid=True, entries_count=0`` for
        an empty log before consulting the manifest — the exact truncation the
        manifest exists to detect."""
        from forgelm.compliance import verify_audit_log

        log_path = self._build_log(tmp_path, events=3)
        assert os.path.isfile(log_path + ".manifest.json")

        with open(log_path, "w", encoding="utf-8"):  # truncate to empty
            pass

        result = verify_audit_log(log_path)
        assert result.valid is False
        assert result.entries_count == 0
        assert result.first_invalid_index == 1
        assert "empty" in (result.reason or "").lower()

    def test_verify_audit_empty_log_without_manifest_is_valid(self, tmp_path):
        """An empty log with NO genesis manifest is a legitimate first-run /
        no-op state — verification passes with entries_count=0.  Guards the
        truncate-to-empty fix from over-failing the benign empty case."""
        from forgelm.compliance import verify_audit_log

        log_path = str(tmp_path / "audit_log.jsonl")
        with open(log_path, "w", encoding="utf-8"):  # empty, no manifest sidecar
            pass
        result = verify_audit_log(log_path)
        assert result.valid is True
        assert result.entries_count == 0

    def test_verify_audit_genesis_manifest_mismatch_fails(self, tmp_path):
        """P4-OPUS-22: an attacker who truncates the log and writes a fresh
        valid chain (re-rooted at genesis) is caught by the write-once manifest
        sidecar — the pinned ``first_entry_sha256`` no longer matches line 1."""
        from forgelm.compliance import AuditLogger, verify_audit_log

        log_path = self._build_log(tmp_path, events=3)
        assert os.path.isfile(log_path + ".manifest.json")

        # Wipe the body but keep the (write-once) manifest, then write a brand
        # new valid chain — a re-root tamper.
        with open(log_path, "w", encoding="utf-8"):
            pass
        logger2 = AuditLogger(str(tmp_path))
        logger2.log_event("rewritten.genesis", forged=True)
        logger2.log_event("rewritten.second")

        result = verify_audit_log(log_path)
        assert result.valid is False
        assert result.first_invalid_index == 1
        assert "manifest mismatch" in (result.reason or "")

    def test_verify_audit_missing_manifest_warning(self, tmp_path, caplog):
        """A log without the manifest sidecar still verifies if its chain
        is intact — the verifier logs at DEBUG that truncate-and-resume
        detection is degraded but does not fail."""
        from forgelm.compliance import verify_audit_log

        log_path = self._build_log(tmp_path, events=3)
        manifest_path = log_path + ".manifest.json"
        if os.path.isfile(manifest_path):
            os.remove(manifest_path)

        with caplog.at_level("DEBUG", logger="forgelm.compliance"):
            result = verify_audit_log(log_path)
        assert result.valid is True
        assert result.entries_count == 3
        assert any("No genesis manifest" in r.message for r in caplog.records)

    def test_verify_audit_hmac_valid(self, tmp_path):
        from forgelm.compliance import verify_audit_log

        # NOSONAR test fixture, not a real secret (rule python:S2068 hard-coded credential false-positive)
        hmac_key = "s3cr3t-operator-key"  # noqa: S105
        log_path = self._build_log(tmp_path, secret=hmac_key, events=3)

        result = verify_audit_log(log_path, hmac_secret=hmac_key)
        assert result.valid is True
        assert result.entries_count == 3

    def test_verify_audit_hmac_invalid(self, tmp_path):
        from forgelm.compliance import verify_audit_log

        log_path = self._build_log(tmp_path, secret="real-secret", events=3)

        # Wrong secret: each line's HMAC tag fails to recompute.
        result = verify_audit_log(log_path, hmac_secret="wrong-secret")
        assert result.valid is False
        assert result.first_invalid_index == 1
        assert "HMAC mismatch" in (result.reason or "")

    def test_verify_audit_require_hmac_without_secret_is_not_valid(self, tmp_path):
        """F-P4-OPUS-03: the public library API ``verify_audit_log`` must
        refuse ``require_hmac=True`` with ``hmac_secret=None`` instead of
        fail-open. Pre-fix, an HMAC-keyed log verified with no secret returned
        ``valid=True`` after only a *presence* check on the ``_hmac`` tag —
        strict mode silently degraded to authenticating nothing. The CLI seam
        already guarded this, but the exported library function did not.
        """
        from forgelm.compliance import verify_audit_log

        # NOSONAR test fixture, not a real secret (rule python:S2068 hard-coded credential false-positive)
        hmac_key = "operator-key"  # noqa: S105
        log_path = self._build_log(tmp_path, secret=hmac_key, events=3)

        result = verify_audit_log(log_path, hmac_secret=None, require_hmac=True)
        assert result.valid is False
        assert "hmac_secret" in (result.reason or "")

    def test_verify_audit_require_hmac_no_secret(self, tmp_path, monkeypatch, capsys):
        """CLI dispatcher: ``--require-hmac`` without a configured secret
        env var must exit 1 (option / operator-actionable error) before
        opening the log.

        F-PR29-A2-01 absorption: option errors map to ``EXIT_CONFIG_ERROR``
        (= 1, the public 0/1/2/3/4 contract's "operator-actionable failure"
        slot), not ``EXIT_TRAINING_ERROR`` (= 2). Both option errors and
        chain-integrity failures share the numeric 1 because both are
        operator-actionable; a dedicated ``EXIT_INTEGRITY_FAILURE``
        constant is deferred to v0.6.x to avoid expanding the public surface.
        """
        from forgelm.cli import _run_verify_audit_cmd

        log_path = self._build_log(tmp_path, events=2)
        monkeypatch.delenv("FORGELM_AUDIT_SECRET", raising=False)

        # Build a minimal argparse.Namespace stand-in.
        class _Args:
            pass

        ns = _Args()
        ns.log_path = log_path
        ns.hmac_secret_env = "FORGELM_AUDIT_SECRET"
        ns.require_hmac = True

        exit_code = _run_verify_audit_cmd(ns)
        assert exit_code == 1
        captured = capsys.readouterr()
        assert "FORGELM_AUDIT_SECRET" in captured.err
        assert "--require-hmac" in captured.err

    def test_verify_audit_missing_file_exit_code(self, tmp_path, monkeypatch, capsys):
        """F-P4-OPUS-04 (XP-18): a missing log file is an operator-actionable
        error → exit 1, matching the exit-codes reference and the (now
        reconciled) parser help. The help previously claimed exit 2 for this
        case."""
        from forgelm.cli import _run_verify_audit_cmd

        monkeypatch.delenv("FORGELM_AUDIT_SECRET", raising=False)

        class _Args:
            pass

        ns = _Args()
        ns.log_path = str(tmp_path / "does-not-exist.jsonl")
        ns.hmac_secret_env = "FORGELM_AUDIT_SECRET"
        ns.require_hmac = False

        exit_code = _run_verify_audit_cmd(ns)
        assert exit_code == 1
        assert "not found" in capsys.readouterr().err

    def test_verify_audit_empty_log(self, tmp_path):
        """An empty file is trivially valid — entries_count == 0, no
        first_invalid_index. Mirrors AuditLogger's genesis convention
        where an absent/empty file legitimately starts at 'genesis'."""
        from forgelm.compliance import verify_audit_log

        empty_path = tmp_path / "audit_log.jsonl"
        empty_path.touch()

        result = verify_audit_log(str(empty_path))
        assert result.valid is True
        assert result.entries_count == 0
        assert result.first_invalid_index is None
