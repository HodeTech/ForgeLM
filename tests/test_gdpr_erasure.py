"""Phase 21 — GDPR Article 17 erasure (`forgelm purge`).

Mirrors the design spec at
``docs/design/gdpr_erasure.md`` §7
which enumerates the 11 tests Phase 21 must ship.  Tests run torch-free
and use synthetic JSONL fixtures so every CI matrix combo exercises the
full surface.
"""

from __future__ import annotations

import errno
import json
import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _seed_corpus(corpus_path: Path, rows: list[dict]) -> None:
    corpus_path.parent.mkdir(parents=True, exist_ok=True)
    with open(corpus_path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _read_audit_events(audit_log_path: Path) -> list[dict]:
    """Parse audit_log.jsonl events; skip blank lines."""
    events: list[dict] = []
    with open(audit_log_path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            events.append(json.loads(line))
    return events


def _build_args(
    *,
    row_id: str | None = None,
    corpus: str | None = None,
    run_id: str | None = None,
    kind: str | None = None,
    check_policy: bool = False,
    output_dir: str | None = None,
    config: str | None = None,
    justification: str | None = None,
    dry_run: bool = False,
    row_matches: str = "one",
) -> SimpleNamespace:
    """Strict argparse-shaped namespace; misspelled attrs raise."""
    return SimpleNamespace(
        row_id=row_id,
        corpus=corpus,
        run_id=run_id,
        kind=kind,
        check_policy=check_policy,
        output_dir=output_dir,
        config=config,
        justification=justification,
        dry_run=dry_run,
        row_matches=row_matches,
    )


@pytest.fixture(autouse=True)
def _set_operator_env(monkeypatch):
    """Every test runs with a deterministic FORGELM_OPERATOR so AuditLogger
    does not refuse to start on shared CI runners."""
    monkeypatch.setenv("FORGELM_OPERATOR", "test-operator@gdpr-test")
    monkeypatch.delenv("FORGELM_AUDIT_SECRET", raising=False)


# ---------------------------------------------------------------------------
# §7 Test 1 — Row erasure: JSONL row removed + audit events emitted in order
# ---------------------------------------------------------------------------


class TestRowErasure:
    def test_row_erasure_removes_matching_row_only(self, tmp_path: Path, capsys) -> None:
        from forgelm.cli.subcommands._purge import _run_purge_cmd

        corpus = tmp_path / "train.jsonl"
        _seed_corpus(
            corpus,
            [
                {"id": "row-A", "text": "Alice's data"},
                {"id": "row-B", "text": "Bob's data"},
                {"id": "row-C", "text": "Carol's data"},
            ],
        )

        args = _build_args(row_id="row-B", corpus=str(corpus), output_dir=str(tmp_path))
        with pytest.raises(SystemExit) as ei:
            _run_purge_cmd(args, output_format="json")
        assert ei.value.code == 0

        # Corpus now has only A + C in original order.
        with open(corpus, "r", encoding="utf-8") as fh:
            remaining = [json.loads(line) for line in fh if line.strip()]
        assert [r["id"] for r in remaining] == ["row-A", "row-C"]

    def test_row_erasure_emits_request_then_completed_in_order(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._purge import _run_purge_cmd

        corpus = tmp_path / "train.jsonl"
        _seed_corpus(corpus, [{"id": "row-X", "text": "to erase"}])

        args = _build_args(row_id="row-X", corpus=str(corpus), output_dir=str(tmp_path))
        with pytest.raises(SystemExit):
            _run_purge_cmd(args, output_format="json")

        events = _read_audit_events(tmp_path / "audit_log.jsonl")
        names = [e["event"] for e in events]
        assert "data.erasure_requested" in names
        assert "data.erasure_completed" in names
        # Order: request must come BEFORE completed (design §4.4).
        assert names.index("data.erasure_requested") < names.index("data.erasure_completed")

    def test_row_erasure_target_id_is_hashed_not_cleartext(self, tmp_path: Path) -> None:
        """Design §5.4: target_id in row mode is SHA-256(salt + value);
        the raw row id must NEVER appear in the audit chain."""
        from forgelm.cli.subcommands._purge import _run_purge_cmd

        corpus = tmp_path / "train.jsonl"
        raw_id = "ali@example.com"  # PII-shaped row id
        _seed_corpus(corpus, [{"id": raw_id, "text": "subject data"}])

        args = _build_args(row_id=raw_id, corpus=str(corpus), output_dir=str(tmp_path))
        with pytest.raises(SystemExit):
            _run_purge_cmd(args, output_format="json")

        events = _read_audit_events(tmp_path / "audit_log.jsonl")
        request_evt = next(e for e in events if e["event"] == "data.erasure_requested")
        assert request_evt["target_id"] != raw_id, "raw row id leaked into audit chain"
        assert len(request_evt["target_id"]) == 64, "target_id should be hex SHA-256 (64 chars)"
        # And the raw email must not appear ANYWHERE in the chain.
        full_log_text = (tmp_path / "audit_log.jsonl").read_text()
        assert raw_id not in full_log_text


# ---------------------------------------------------------------------------
# §7 Test 2 — Salt persistence + salt_source
# ---------------------------------------------------------------------------


class TestSaltPersistence:
    def test_salt_file_created_on_first_use_with_mode_0600(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._purge import _resolve_salt

        salt, source = _resolve_salt(str(tmp_path))
        salt_path = tmp_path / ".forgelm_audit_salt"
        assert salt_path.is_file()
        assert len(salt) == 16
        assert source == "per_dir"
        # Mode check is POSIX-only: Windows ``os.chmod`` only honours the
        # read-only bit (``stat.S_IREAD`` / ``stat.S_IWRITE``) and ignores
        # group / other bits — the resulting mode reads back as 0o666 on
        # NTFS regardless of what mode the call requested, so the POSIX
        # 0o600 contract cannot be enforced at the filesystem level.
        # ACL-based hardening (the equivalent on Windows) is operator-side
        # via the deploy guide, not in scope for this unit test.
        if sys.platform == "win32":
            return
        mode = stat.S_IMODE(salt_path.stat().st_mode)
        assert mode == 0o600, f"salt file mode should be 0o600, got {oct(mode)}"

    def test_salt_persistent_across_invocations(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._purge import _resolve_salt

        salt1, _ = _resolve_salt(str(tmp_path))
        salt2, _ = _resolve_salt(str(tmp_path))
        assert salt1 == salt2, "per-output-dir salt must be stable across calls"

    def test_salt_source_env_var_when_secret_set(self, tmp_path: Path, monkeypatch) -> None:
        from forgelm.cli.subcommands._purge import _resolve_salt

        monkeypatch.setenv("FORGELM_AUDIT_SECRET", "supersecret-prod-key-2026-05")
        salt, source = _resolve_salt(str(tmp_path))
        assert source == "env_var"
        assert len(salt) == 16

    def test_salt_changes_with_env_var_toggle(self, tmp_path: Path, monkeypatch) -> None:
        """Phase 20 design F-R5-05: env-var toggle IS a hash discontinuity;
        the salt_source field on every event makes that visible."""
        from forgelm.cli.subcommands._purge import _resolve_salt

        salt_no_env, source_no_env = _resolve_salt(str(tmp_path))
        monkeypatch.setenv("FORGELM_AUDIT_SECRET", "abc123")
        salt_env, source_env = _resolve_salt(str(tmp_path))
        assert source_no_env == "per_dir"
        assert source_env == "env_var"
        assert salt_no_env != salt_env, "env var must alter the resolved salt"


# ---------------------------------------------------------------------------
# §7 Test 3 — Run-scoped staging deletion
# ---------------------------------------------------------------------------


class TestStagingDeletion:
    def test_staging_kind_removes_staging_directory(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._purge import _run_purge_cmd

        run_id = "fg-stagingrun01"
        staging = tmp_path / f"final_model.staging.{run_id}"
        staging.mkdir(parents=True)
        (staging / "adapter_config.json").write_text('{"r": 8}')
        (staging / "weights.bin").write_bytes(b"x" * 1024)

        args = _build_args(run_id=run_id, kind="staging", output_dir=str(tmp_path))
        with pytest.raises(SystemExit) as ei:
            _run_purge_cmd(args, output_format="json")
        assert ei.value.code == 0
        assert not staging.exists()


# ---------------------------------------------------------------------------
# §7 Test 4 — Run-scoped artefact deletion
# ---------------------------------------------------------------------------


class TestArtefactDeletion:
    def test_artefacts_kind_removes_compliance_bundle_for_run(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._purge import _run_purge_cmd

        run_id = "fg-artefactsrun"
        compliance_dir = tmp_path / "compliance"
        compliance_dir.mkdir(parents=True)
        (compliance_dir / f"compliance_{run_id}.json").write_text("{}")
        (compliance_dir / f"annex_iv_{run_id}.json").write_text("{}")
        (compliance_dir / "compliance_other-run.json").write_text("{}")  # different run

        args = _build_args(run_id=run_id, kind="artefacts", output_dir=str(tmp_path))
        with pytest.raises(SystemExit) as ei:
            _run_purge_cmd(args, output_format="json")
        assert ei.value.code == 0
        assert not (compliance_dir / f"compliance_{run_id}.json").exists()
        assert not (compliance_dir / f"annex_iv_{run_id}.json").exists()
        # Other run's bundle is untouched.
        assert (compliance_dir / "compliance_other-run.json").exists()


# ---------------------------------------------------------------------------
# §7 Test 5 — Audit chain post-erasure still verifies
# ---------------------------------------------------------------------------


class TestAuditChainIntegrity:
    def test_chain_verifies_post_erasure(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._purge import _run_purge_cmd

        # Pre-seed the chain with a non-erasure event so the genesis +
        # erasure events both have to chain correctly.
        from forgelm.compliance import AuditLogger, verify_audit_log

        AuditLogger(str(tmp_path)).log_event("training.started", run_label="pre-erasure")

        corpus = tmp_path / "train.jsonl"
        _seed_corpus(corpus, [{"id": "row-1", "text": "subject"}])
        args = _build_args(row_id="row-1", corpus=str(corpus), output_dir=str(tmp_path))
        with pytest.raises(SystemExit):
            _run_purge_cmd(args, output_format="json")

        result = verify_audit_log(str(tmp_path / "audit_log.jsonl"))
        assert result.valid, f"chain must verify post-erasure; got: {result}"


# ---------------------------------------------------------------------------
# §7 Test 6 — `--check-policy` reports violations correctly
# ---------------------------------------------------------------------------


class TestCheckPolicy:
    def test_check_policy_with_no_retention_block_returns_zero_with_note(self, tmp_path: Path, capsys) -> None:
        from forgelm.cli.subcommands._purge import _run_purge_cmd

        # Create a minimal config WITHOUT retention block.
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            """
model:
  name_or_path: gpt2
  backend: transformers
lora:
  r: 8
training:
  trainer_type: sft
  output_dir: ./out
  num_train_epochs: 1
data:
  dataset_name_or_path: train.jsonl
"""
        )
        args = _build_args(check_policy=True, config=str(config_path), output_dir=str(tmp_path))
        with pytest.raises(SystemExit) as ei:
            _run_purge_cmd(args, output_format="json")
        assert ei.value.code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["success"] is True
        assert payload["violations"] == []

    def test_check_policy_reports_overstayed_artefact(self, tmp_path: Path, capsys, monkeypatch) -> None:
        from forgelm.cli.subcommands._purge import _scan_retention_violations
        from forgelm.config import RetentionConfig

        # Synthetic overstayed artefact: ephemeral horizon = 30 days,
        # data audit report mtime = 60 days ago → 1 violation.
        report = tmp_path / "data_audit_report.json"
        report.write_text("{}")
        sixty_days_ago = report.stat().st_mtime - 60 * 86400
        os.utime(report, (sixty_days_ago, sixty_days_ago))

        retention = RetentionConfig(ephemeral_artefact_retention_days=30)
        violations = _scan_retention_violations(retention, str(tmp_path))
        kinds = [v["artefact_kind"] for v in violations]
        assert "data_audit_report" in kinds
        # Age source = mtime fallback (no audit log present).
        rep_violation = next(v for v in violations if v["artefact_kind"] == "data_audit_report")
        assert rep_violation["age_source"] == "mtime"
        assert rep_violation["age_days"] >= 30

    def test_check_policy_always_exits_zero(self, tmp_path: Path) -> None:
        """Design §10 Q5: `--check-policy` is a report, not a gate.
        Exit code is always 0 regardless of violation count."""
        from forgelm.cli.subcommands._purge import _run_purge_cmd

        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            """
model:
  name_or_path: gpt2
  backend: transformers
lora:
  r: 8
training:
  trainer_type: sft
  output_dir: ./out
  num_train_epochs: 1
data:
  dataset_name_or_path: train.jsonl
retention:
  ephemeral_artefact_retention_days: 1
"""
        )
        # Plant an overstayed artefact.
        report = tmp_path / "data_audit_report.json"
        report.write_text("{}")
        ago = report.stat().st_mtime - 30 * 86400
        os.utime(report, (ago, ago))

        args = _build_args(check_policy=True, config=str(config_path), output_dir=str(tmp_path))
        with pytest.raises(SystemExit) as ei:
            _run_purge_cmd(args, output_format="json")
        assert ei.value.code == 0, "check-policy must exit 0 even with violations (report-not-gate)"


# ---------------------------------------------------------------------------
# §7 Test 7 — Atomic concurrency
# ---------------------------------------------------------------------------


class TestAtomicity:
    def test_atomic_rewrite_fsyncs_before_rename(self, tmp_path: Path, monkeypatch) -> None:
        """F-W2B-03 regression: data blocks must be flushed to disk
        BEFORE the namespace swap.  Without fsync, a power-loss between
        the rename and the page-cache flush leaves the corpus pointing
        at the new file with its data blocks unwritten (zero bytes after
        reboot)."""
        from forgelm.cli.subcommands import _purge

        corpus = tmp_path / "train.jsonl"
        _seed_corpus(
            corpus,
            [
                {"id": "row-1", "text": "keep"},
                {"id": "row-2", "text": "drop"},
                {"id": "row-3", "text": "keep"},
            ],
        )

        fsync_calls: list[int] = []
        replace_call_position: list[int] = []
        original_fsync = os.fsync
        original_replace = os.replace

        def _spy_fsync(fd: int) -> None:
            fsync_calls.append(fd)
            original_fsync(fd)

        def _spy_replace(src, dst):
            replace_call_position.append(len(fsync_calls))
            return original_replace(src, dst)

        monkeypatch.setattr(os, "fsync", _spy_fsync)
        monkeypatch.setattr(os, "replace", _spy_replace)

        _purge._atomic_rewrite_dropping_lines(str(corpus), [2])

        assert fsync_calls, "_atomic_rewrite_dropping_lines must call os.fsync at least once"
        assert replace_call_position[0] >= 1, "os.replace was called before os.fsync — data blocks may not be on disk"

    def test_atomic_rewrite_leaves_no_partial_file_on_io_failure(self, tmp_path: Path, monkeypatch) -> None:
        from forgelm.cli.subcommands import _purge

        corpus = tmp_path / "train.jsonl"
        _seed_corpus(
            corpus,
            [
                {"id": "row-1", "text": "keep"},
                {"id": "row-2", "text": "keep"},
                {"id": "row-3", "text": "drop"},
            ],
        )
        original_content = corpus.read_text()

        # Inject an OSError mid-rewrite via os.replace patching.
        original_replace = os.replace

        def _failing_replace(src, dst):
            if str(dst) == str(corpus):
                raise OSError("simulated atomic-rename failure")
            return original_replace(src, dst)

        monkeypatch.setattr(os, "replace", _failing_replace)

        with pytest.raises(OSError, match="simulated atomic-rename failure"):
            _purge._atomic_rewrite_dropping_lines(str(corpus), [3])

        # Corpus must be UNCHANGED (atomic = all-or-nothing).
        assert corpus.read_text() == original_content


# ---------------------------------------------------------------------------
# §7 Test 8 — Unknown row-id / run-id → clear error message
# ---------------------------------------------------------------------------


class TestUnknownTargetErrors:
    def test_unknown_row_id_emits_failed_event_then_exits_one(self, tmp_path: Path, capsys) -> None:
        from forgelm.cli.subcommands._purge import _run_purge_cmd

        corpus = tmp_path / "train.jsonl"
        _seed_corpus(corpus, [{"id": "row-A"}, {"id": "row-B"}])
        args = _build_args(row_id="row-NOPE", corpus=str(corpus), output_dir=str(tmp_path))

        with pytest.raises(SystemExit) as ei:
            _run_purge_cmd(args, output_format="json")
        assert ei.value.code == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["success"] is False
        # Round 4 absorption: error payload uses a redacted id_hash
        # short form so the JSON envelope never echoes the raw row_id
        # (which is potentially PII). The raw value MUST be absent and
        # the hash-prefix marker MUST be present.
        # Round 6 absorption: pin the EXACT token shape (12 lowercase
        # hex + U+2026 ellipsis) and assert the SAME token appears in
        # both the JSON payload AND the audit event — a regression that
        # cropped the prefix to a different length, or computed a
        # different hash for the audit-log emit, would silently slip
        # through a substring-only check.
        import re

        TOKEN_RE = re.compile(r"<id_hash:([0-9a-f]{12})…>")
        assert "row-NOPE" not in payload["error"]
        match = TOKEN_RE.search(payload["error"])
        assert match is not None, f"redaction token shape broken: {payload['error']!r}"
        payload_hash = match.group(1)

        events = _read_audit_events(tmp_path / "audit_log.jsonl")
        names = [e["event"] for e in events]
        assert "data.erasure_requested" in names
        assert "data.erasure_failed" in names
        # NOT data.erasure_completed.
        assert "data.erasure_completed" not in names
        # And the audit event's `error_message` field must use the same
        # redacted form, not the raw row_id.
        failed = next(e for e in events if e["event"] == "data.erasure_failed")
        assert "row-NOPE" not in failed.get("error_message", "")
        audit_match = TOKEN_RE.search(failed.get("error_message", ""))
        assert audit_match is not None, f"audit error_message redaction shape broken: {failed.get('error_message')!r}"
        # Cross-tool correlation: stdout payload + audit event must
        # carry identical hash prefix (both derive from
        # _hash_target_id(args.row_id, salt)).
        assert payload_hash == audit_match.group(1), (
            f"hash prefix mismatch — stdout {payload_hash!r} vs audit {audit_match.group(1)!r}"
        )

    def test_unknown_run_id_artefacts_emits_failed_event(self, tmp_path: Path, capsys) -> None:
        from forgelm.cli.subcommands._purge import _run_purge_cmd

        # Empty output dir; no compliance bundle for this run.
        args = _build_args(run_id="fg-nonexistent", kind="artefacts", output_dir=str(tmp_path))
        with pytest.raises(SystemExit) as ei:
            _run_purge_cmd(args, output_format="json")
        assert ei.value.code == 1


# ---------------------------------------------------------------------------
# §7 Test 9 — Multi-row policy: --row-matches
# ---------------------------------------------------------------------------


class TestMultiRowPolicy:
    def test_multi_match_one_mode_refuses(self, tmp_path: Path, capsys) -> None:
        from forgelm.cli.subcommands._purge import _run_purge_cmd

        corpus = tmp_path / "train.jsonl"
        # The seed `id` deliberately looks PII-shaped so the redaction
        # contract is exercised against a realistic threat model
        # (corpus row ids that ARE personal data, e.g. emails).
        _seed_corpus(
            corpus,
            [
                {"id": "alice@example.com", "text": "first"},
                {"id": "alice@example.com", "text": "second"},
            ],
        )
        args = _build_args(
            row_id="alice@example.com",
            corpus=str(corpus),
            output_dir=str(tmp_path),
            row_matches="one",
        )
        with pytest.raises(SystemExit) as ei:
            _run_purge_cmd(args, output_format="json")
        assert ei.value.code == 1
        payload = json.loads(capsys.readouterr().out)
        assert "matched" in payload["error"].lower()
        # Round 5 absorption: the multi-match refusal path must apply
        # the same `<id_hash:{first12}…>` redaction as the no-match
        # path. A regression that re-introduces raw `args.row_id` in
        # just the multi-match leg would otherwise ship undetected
        # because the no-match leg has its own test.
        # Round 6 absorption: pin the EXACT token shape (12 lowercase
        # hex + U+2026 ellipsis) and assert the SAME token appears in
        # both the JSON payload AND the audit event.
        import re

        TOKEN_RE = re.compile(r"<id_hash:([0-9a-f]{12})…>")
        assert "alice@example.com" not in payload["error"]
        match = TOKEN_RE.search(payload["error"])
        assert match is not None, f"redaction token shape broken: {payload['error']!r}"
        payload_hash = match.group(1)
        events = _read_audit_events(tmp_path / "audit_log.jsonl")
        failed = next(e for e in events if e["event"] == "data.erasure_failed")
        assert failed.get("error_class") == "MultiMatchRefused"
        assert "alice@example.com" not in failed.get("error_message", "")
        audit_match = TOKEN_RE.search(failed.get("error_message", ""))
        assert audit_match is not None, f"audit error_message redaction shape broken: {failed.get('error_message')!r}"
        # Cross-tool correlation: same hash prefix in both surfaces.
        assert payload_hash == audit_match.group(1), (
            f"hash prefix mismatch — stdout {payload_hash!r} vs audit {audit_match.group(1)!r}"
        )

    def test_multi_match_all_mode_deletes_every_match(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._purge import _run_purge_cmd

        corpus = tmp_path / "train.jsonl"
        _seed_corpus(
            corpus,
            [
                {"id": "shared-id", "text": "first"},
                {"id": "keep-me"},
                {"id": "shared-id", "text": "second"},
            ],
        )
        args = _build_args(
            row_id="shared-id",
            corpus=str(corpus),
            output_dir=str(tmp_path),
            row_matches="all",
        )
        with pytest.raises(SystemExit) as ei:
            _run_purge_cmd(args, output_format="json")
        assert ei.value.code == 0
        with open(corpus, "r", encoding="utf-8") as fh:
            remaining = [json.loads(line) for line in fh if line.strip()]
        assert remaining == [{"id": "keep-me"}]


# ---------------------------------------------------------------------------
# §7 Test 10 — --dry-run preserves disk; emits chain
# ---------------------------------------------------------------------------


class TestDryRun:
    def test_dry_run_does_not_modify_corpus(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._purge import _run_purge_cmd

        corpus = tmp_path / "train.jsonl"
        _seed_corpus(corpus, [{"id": "row-X", "text": "subject"}])
        original = corpus.read_text()

        args = _build_args(row_id="row-X", corpus=str(corpus), output_dir=str(tmp_path), dry_run=True)
        with pytest.raises(SystemExit):
            _run_purge_cmd(args, output_format="json")

        assert corpus.read_text() == original

        # But the chain still records intent.
        events = _read_audit_events(tmp_path / "audit_log.jsonl")
        names = [e["event"] for e in events]
        assert "data.erasure_requested" in names
        assert "data.erasure_completed" in names  # marked dry_run=True
        completed = next(e for e in events if e["event"] == "data.erasure_completed")
        assert completed.get("dry_run") is True


# ---------------------------------------------------------------------------
# §7 Test 11 — Warning events fire alongside completed
# ---------------------------------------------------------------------------


class TestWarningEvents:
    def test_memorisation_warning_fires_when_final_model_exists(self, tmp_path: Path) -> None:
        """Plant a `final_model.staging.<run_id>` directory; row erasure
        should emit data.erasure_warning_memorisation."""
        from forgelm.cli.subcommands._purge import _run_purge_cmd

        # Plant a final_model dir AND a staging dir with a discoverable
        # run id so the warning includes affected_run_ids.
        final = tmp_path / "final_model"
        final.mkdir()
        staging = tmp_path / "final_model.staging.fg-pastrun"
        staging.mkdir()

        corpus = tmp_path / "train.jsonl"
        _seed_corpus(corpus, [{"id": "row-Y", "text": "memorised"}])
        args = _build_args(row_id="row-Y", corpus=str(corpus), output_dir=str(tmp_path))
        with pytest.raises(SystemExit):
            _run_purge_cmd(args, output_format="json")

        events = _read_audit_events(tmp_path / "audit_log.jsonl")
        names = [e["event"] for e in events]
        assert "data.erasure_warning_memorisation" in names
        warn_evt = next(e for e in events if e["event"] == "data.erasure_warning_memorisation")
        assert warn_evt.get("affected_run_ids") == ["fg-pastrun"]

    def test_synthetic_data_warning_fires_when_synthetic_files_exist(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._purge import _run_purge_cmd

        (tmp_path / "synthetic_data.jsonl").write_text("{}\n")

        corpus = tmp_path / "train.jsonl"
        _seed_corpus(corpus, [{"id": "row-Z"}])
        args = _build_args(row_id="row-Z", corpus=str(corpus), output_dir=str(tmp_path))
        with pytest.raises(SystemExit):
            _run_purge_cmd(args, output_format="json")

        events = _read_audit_events(tmp_path / "audit_log.jsonl")
        assert "data.erasure_warning_synthetic_data_present" in [e["event"] for e in events]


# ---------------------------------------------------------------------------
# Defensive: dispatcher rejects mutually-exclusive flag combinations
# ---------------------------------------------------------------------------


class TestDispatcherDefensive:
    def test_check_policy_with_row_id_is_rejected(self, tmp_path: Path, capsys) -> None:
        from forgelm.cli.subcommands._purge import _run_purge_cmd

        args = _build_args(check_policy=True, row_id="x", output_dir=str(tmp_path))
        with pytest.raises(SystemExit) as ei:
            _run_purge_cmd(args, output_format="json")
        assert ei.value.code == 1

    def test_row_id_and_run_id_together_rejected(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._purge import _run_purge_cmd

        args = _build_args(row_id="x", run_id="y", output_dir=str(tmp_path))
        with pytest.raises(SystemExit) as ei:
            _run_purge_cmd(args, output_format="json")
        assert ei.value.code == 1

    def test_no_mode_at_all_rejected(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._purge import _run_purge_cmd

        args = _build_args(output_dir=str(tmp_path))
        with pytest.raises(SystemExit) as ei:
            _run_purge_cmd(args, output_format="json")
        assert ei.value.code == 1


# ---------------------------------------------------------------------------
# Deprecation: evaluation.staging_ttl_days alias-forward
# ---------------------------------------------------------------------------


class TestStagingTtlDeprecation:
    def test_both_set_with_different_values_raises_config_error(self, tmp_path: Path) -> None:
        from forgelm.config import ConfigError, load_config

        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            """
model:
  name_or_path: gpt2
  backend: transformers
lora:
  r: 8
training:
  trainer_type: sft
  output_dir: ./out
  num_train_epochs: 1
data:
  dataset_name_or_path: train.jsonl
evaluation:
  staging_ttl_days: 14
retention:
  staging_ttl_days: 30
"""
        )
        with pytest.raises(ConfigError, match="staging_ttl_days"):
            load_config(str(config_path))

    def test_canonical_only_no_warning(self, tmp_path: Path, recwarn) -> None:
        from forgelm.config import load_config

        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            """
model:
  name_or_path: gpt2
  backend: transformers
lora:
  r: 8
training:
  trainer_type: sft
  output_dir: ./out
  num_train_epochs: 1
data:
  dataset_name_or_path: train.jsonl
retention:
  staging_ttl_days: 14
"""
        )
        cfg = load_config(str(config_path))
        assert cfg.retention is not None
        assert cfg.retention.staging_ttl_days == 14
        # No DeprecationWarning under the canonical path.
        deprecation_warnings = [w for w in recwarn.list if issubclass(w.category, DeprecationWarning)]
        assert not deprecation_warnings, (
            f"unexpected deprecation warnings: {[str(w.message) for w in deprecation_warnings]}"
        )

    def test_canonical_block_overrides_when_legacy_omitted_from_yaml(self, tmp_path: Path, recwarn) -> None:
        """F-W2B-02 regression: operator follows the documented migration
        path (delete the deprecated key, add the canonical block).
        Pydantic re-fills `evaluation.staging_ttl_days = 7` from default,
        but the reconciler must NOT treat that as "operator set legacy
        explicitly" — it must consult `model_fields_set` and prefer the
        canonical value silently."""
        from forgelm.config import load_config

        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            """
model:
  name_or_path: gpt2
  backend: transformers
lora:
  r: 8
training:
  trainer_type: sft
  output_dir: ./out
  num_train_epochs: 1
data:
  dataset_name_or_path: train.jsonl
evaluation:
  require_human_approval: false
retention:
  staging_ttl_days: 14
"""
        )
        cfg = load_config(str(config_path))
        assert cfg.retention is not None
        assert cfg.retention.staging_ttl_days == 14
        # Critical: no DeprecationWarning, no ConfigError — operator
        # only kept `evaluation.require_human_approval` (an unrelated
        # field), and the deprecated `staging_ttl_days` was deleted.
        deprecation_warnings = [w for w in recwarn.list if issubclass(w.category, DeprecationWarning)]
        assert not deprecation_warnings, (
            f"unexpected deprecation warnings on the documented migration path: "
            f"{[str(w.message) for w in deprecation_warnings]}"
        )


class TestSaltTruncation:
    """F-21-T-04: a corrupted/truncated salt file must surface as OSError
    rather than silently producing a weak hash.  The branch exists at
    `_purge.py:_resolve_salt` precisely so the chain cannot be salted with
    fewer than 16 bytes; without this regression test, a future "more
    graceful" handler that pads short salt to 16 bytes would silently
    weaken every `target_id` and the suite would stay green."""

    def test_truncated_salt_file_raises(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._purge import _resolve_salt

        salt_path = tmp_path / ".forgelm_audit_salt"
        salt_path.write_bytes(b"x" * 8)  # 8 < 16 → truncated
        with pytest.raises(OSError, match="shorter than"):
            _resolve_salt(str(tmp_path))


class TestArtefactPrefixMatcher:
    """F-21-T-01 / F-21-03: `_filename_contains_run_id` token-boundary
    guard must not delete sibling runs whose id is a prefix-superstring
    of the target.  Round-1 absorption introduced the helper; without
    this test, a "simpler" `if run_id in fname` regression would silently
    delete other runs' compliance bundles."""

    def test_run_id_prefix_does_not_match_longer_id(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._purge import _run_purge_cmd

        compliance = tmp_path / "compliance"
        compliance.mkdir()
        (compliance / "compliance_fg-abc.json").write_text("{}")
        # Bystander whose run_id is a SUPERSTRING of the target.
        (compliance / "compliance_fg-abc1234.json").write_text("{}")

        args = _build_args(run_id="fg-abc", kind="artefacts", output_dir=str(tmp_path))
        with pytest.raises(SystemExit) as ei:
            _run_purge_cmd(args, output_format="json")
        assert ei.value.code == 0
        assert not (compliance / "compliance_fg-abc.json").exists()
        # Critical: the longer-run-id bundle is preserved.
        assert (compliance / "compliance_fg-abc1234.json").exists()


class TestAuditAgeLookup:
    """F-21-T-02: the per-`run_id` audit-age lookup added in Round-5
    (F-W2B-PURGE) must discriminate ages across runs, fall back to
    genesis (not mtime) for orphaned staging dirs, and honour the
    append-only first-write invariant for retried run_ids."""

    def _write_audit_log(self, path: Path, events: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            for ev in events:
                fh.write(json.dumps(ev) + "\n")

    def test_per_run_age_discriminates_across_run_ids(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._purge import _build_audit_age_lookup

        # Two runs, ten days apart.
        ten_days_ago = "2026-04-15T00:00:00Z"
        one_day_ago = "2026-04-24T00:00:00Z"
        log = tmp_path / "audit_log.jsonl"
        self._write_audit_log(
            log,
            [
                {"timestamp": ten_days_ago, "run_id": "fg-old", "event": "training.started"},
                {"timestamp": one_day_ago, "run_id": "fg-new", "event": "training.started"},
            ],
        )
        # Pin `now` to 2026-04-25T00:00:00Z so the deltas are exact.
        from datetime import datetime, timezone

        now = datetime(2026, 4, 25, tzinfo=timezone.utc).timestamp()
        ages = _build_audit_age_lookup(str(log), now)
        # Both runs registered; old run is older than new run.
        assert "fg-old" in ages and "fg-new" in ages
        assert ages["fg-old"] > ages["fg-new"]
        # Genesis age tracks the *first* event (fg-old's, ten days ago).
        assert ages[None] == ages["fg-old"]

    def test_orphaned_staging_falls_back_to_genesis_not_mtime(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._purge import _build_audit_age_lookup, _resolve_artefact_age

        log = tmp_path / "audit_log.jsonl"
        self._write_audit_log(
            log,
            [{"timestamp": "2026-04-01T00:00:00Z", "run_id": "fg-known", "event": "training.started"}],
        )
        from datetime import datetime, timezone

        now = datetime(2026, 4, 25, tzinfo=timezone.utc).timestamp()
        ages = _build_audit_age_lookup(str(log), now)
        # An orphaned staging dir whose run_id is NOT in the log:
        # must fall back to genesis (source = "audit"), not to mtime.
        orphan = tmp_path / "final_model.staging.fg-orphan"
        orphan.mkdir()
        age, source = _resolve_artefact_age(str(orphan), ages, "fg-orphan", now)
        assert source == "audit", "orphaned run_id must fall back to genesis (audit), not mtime"
        assert age == ages[None]

    def test_retried_run_id_honours_first_timestamp(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._purge import _build_audit_age_lookup

        log = tmp_path / "audit_log.jsonl"
        self._write_audit_log(
            log,
            [
                {"timestamp": "2026-04-01T00:00:00Z", "run_id": "fg-retry", "event": "training.started"},
                {"timestamp": "2026-04-20T00:00:00Z", "run_id": "fg-retry", "event": "training.restarted"},
            ],
        )
        from datetime import datetime, timezone

        now = datetime(2026, 4, 25, tzinfo=timezone.utc).timestamp()
        ages = _build_audit_age_lookup(str(log), now)
        # Append-only invariant: the FIRST timestamp wins.  Age must be
        # ≥ 24 days (since the first write on 2026-04-01), not ~5 days
        # (since the second write on 2026-04-20).
        assert ages["fg-retry"] >= 24 * 86400


class TestCheckPolicyStrictLoad:
    """F-21-T-03 / F-21-02: Round-2 absorption made `--check-policy`
    strictly load the supplied `--config` so a malformed YAML / Pydantic
    schema error exits `EXIT_CONFIG_ERROR` rather than silently
    degrading to a "no retention block, exit 0" report.  Without these
    tests, a regression to the silent-degrade behaviour would not be
    caught by CI."""

    def test_check_policy_with_unparseable_yaml_exits_config_error(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._purge import _run_purge_cmd

        cfg = tmp_path / "bad.yaml"
        cfg.write_text("model: { unclosed_brace ")
        args = _build_args(check_policy=True, config=str(cfg), output_dir=str(tmp_path))
        with pytest.raises(SystemExit) as ei:
            _run_purge_cmd(args, output_format="json")
        assert ei.value.code == 1, "malformed YAML must exit EXIT_CONFIG_ERROR (1), not silently exit 0"

    def test_check_policy_with_pydantic_validation_error_exits_config_error(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._purge import _run_purge_cmd

        cfg = tmp_path / "bad.yaml"
        # `training.trainer_type: spo` is an enum-violation Pydantic catches.
        cfg.write_text(
            """
model:
  name_or_path: gpt2
  backend: transformers
lora:
  r: 8
training:
  trainer_type: spo
  output_dir: ./out
  num_train_epochs: 1
data:
  dataset_name_or_path: train.jsonl
"""
        )
        args = _build_args(check_policy=True, config=str(cfg), output_dir=str(tmp_path))
        with pytest.raises(SystemExit) as ei:
            _run_purge_cmd(args, output_format="json")
        assert ei.value.code == 1

    def test_check_policy_no_config_succeeds_with_zero(self, tmp_path: Path) -> None:
        """F-21-T-07: `--check-policy` with no `--config` must still exit 0
        (no retention block to enforce → empty violations + note)."""
        from forgelm.cli.subcommands._purge import _run_purge_cmd

        args = _build_args(check_policy=True, config=None, output_dir=str(tmp_path))
        with pytest.raises(SystemExit) as ei:
            _run_purge_cmd(args, output_format="json")
        assert ei.value.code == 0


class TestAtomicityFsyncFdPinning:
    """F-21-T-06: the existing `test_atomic_rewrite_fsyncs_before_rename`
    asserts that fsync is called before replace, but not WHICH fd was
    fsynced.  A regression that fsynced the parent dir (or any other fd)
    instead of the temp file would still satisfy the ordering check.
    This test pins the fsynced fd to the one we use for the temp file."""

    def test_atomic_rewrite_fsyncs_temp_file_fd_specifically(self, tmp_path: Path, monkeypatch) -> None:
        from forgelm.cli.subcommands import _purge

        corpus = tmp_path / "train.jsonl"
        _seed_corpus(
            corpus,
            [
                {"id": "row-1", "text": "keep"},
                {"id": "row-2", "text": "drop"},
            ],
        )

        captured_temp_fds: list[int] = []
        original_mkstemp = __import__("tempfile").mkstemp

        def _spy_mkstemp(*args, **kwargs):
            fd, path = original_mkstemp(*args, **kwargs)
            captured_temp_fds.append(fd)
            return fd, path

        monkeypatch.setattr("tempfile.mkstemp", _spy_mkstemp)

        # Also spy on os.fsync to record the fd it was called with.
        fsync_calls: list[int] = []
        original_fsync = os.fsync

        def _spy_fsync(fd: int) -> None:
            fsync_calls.append(fd)
            original_fsync(fd)

        monkeypatch.setattr(os, "fsync", _spy_fsync)

        _purge._atomic_rewrite_dropping_lines(str(corpus), [2])

        # The temp-file fd must appear in the fsync call list — not just
        # *any* fd.  Without this, a regression that fsyncs the parent
        # dir's fd instead would still pass the ordering test.
        assert captured_temp_fds, "tempfile.mkstemp was never called"
        assert any(fd in fsync_calls for fd in captured_temp_fds), (
            f"os.fsync was not called on any temp-file fd; fsync targets={fsync_calls}, "
            f"temp_fds={captured_temp_fds}.  Data blocks may not be flushed."
        )


# ---------------------------------------------------------------------------
# §7 Test matrix row 9 — Run-scoped erasure path-traversal boundary
# (F-P5-OPUS-02): the design promises a realpath+commonpath check mirroring
# _approve._staging_path_inside_output_dir, so a ``..``-bearing --run-id
# cannot rmtree a directory outside output_dir.
# ---------------------------------------------------------------------------


class TestRunIdPathTraversal:
    @staticmethod
    def _stage_traversal(output_dir: Path, victim: Path) -> str:
        """Build the on-disk shape that makes a ``..``-bearing --run-id
        resolve to *victim* (outside *output_dir*) while still passing the
        resolver's ``.exists()`` gate.

        ``_staging_targets_for_run`` builds
        ``output_dir / f"final_model.staging.{run_id}"`` and only appends the
        target when ``Path.exists()`` is True.  For run_id='../../../victim'
        the first path component is the literal ``final_model.staging...``
        directory; creating it lets the OS walk
        ``final_model.staging.../../../victim`` through to *victim*, whose
        realpath escapes ``output_dir``.  Returns the malicious run_id.
        """
        run_id = "../../../victim"
        # First component of f"final_model.staging.{run_id}" is the literal
        # dir "final_model.staging..." — create it so the relative walk
        # resolves to the sibling victim directory.
        (output_dir / "final_model.staging...").mkdir(parents=True, exist_ok=True)
        return run_id

    def test_run_id_path_traversal_rejected_staging(self, tmp_path: Path, capsys) -> None:
        from forgelm.cli.subcommands._purge import _run_purge_run_id

        # output_dir is a child of tmp_path; the victim lives outside it.
        output_dir = tmp_path / "run1"
        output_dir.mkdir()
        victim = tmp_path / "victim"
        victim.mkdir()
        (victim / "data.bin").write_bytes(b"x" * 64)

        run_id = self._stage_traversal(output_dir, victim)
        args = _build_args(run_id=run_id, kind="staging", output_dir=str(output_dir))

        with pytest.raises(SystemExit) as ei:
            _run_purge_run_id(args, output_format="json")

        # EXIT_CONFIG_ERROR (1) — refused, not a runtime deletion failure.
        assert ei.value.code == 1
        # The victim directory and its contents are untouched.
        assert victim.exists()
        assert (victim / "data.bin").exists()

        payload = json.loads(capsys.readouterr().out)
        assert payload["success"] is False
        assert "traversal" in payload["error"].lower() or "outside" in payload["error"].lower()

    def test_run_id_path_traversal_emits_failed_not_completed(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._purge import _run_purge_run_id

        output_dir = tmp_path / "run1"
        output_dir.mkdir()
        victim = tmp_path / "victim"
        victim.mkdir()

        run_id = self._stage_traversal(output_dir, victim)
        args = _build_args(run_id=run_id, kind="staging", output_dir=str(output_dir))
        with pytest.raises(SystemExit):
            _run_purge_run_id(args, output_format="json")

        events = _read_audit_events(output_dir / "audit_log.jsonl")
        names = [e["event"] for e in events]
        assert "data.erasure_requested" in names
        assert "data.erasure_failed" in names
        assert "data.erasure_completed" not in names
        failed = next(e for e in events if e["event"] == "data.erasure_failed")
        assert failed["error_class"] == "PathTraversalRefused"

    def test_path_traversal_failed_error_message_masked_and_bounded(self, tmp_path: Path) -> None:
        """The ``PathTraversalRefused`` ``error_message`` embeds the refused
        resolved path(s); route it through the same sanitiser so a
        PII-shaped victim-dir name (or an overlong path) never lands raw in
        the append-only chain (F-P5-OPUS-07, design gdpr_erasure.md §6).
        """
        from forgelm.cli.subcommands import _purge
        from forgelm.cli.subcommands._purge import _run_purge_run_id

        output_dir = tmp_path / "run1"
        output_dir.mkdir()
        # Victim dir name carries an email-shaped PII marker plus a long tail
        # (kept under the 255-char filename limit) so the resolved escaping
        # path is both PII-bearing and over the audit-message length cap.
        leaked = "ali@example.com"
        victim_name = f"victim-{leaked}-" + ("z" * 200)
        victim = tmp_path / victim_name
        try:
            victim.mkdir()
        except OSError as e:
            if e.errno == errno.ENAMETOOLONG:
                pytest.skip(f"filesystem filename limit too short for this test ({len(victim_name)} chars)")
            raise
        (output_dir / "final_model.staging...").mkdir(parents=True, exist_ok=True)

        run_id = f"../../../{victim_name}"
        args = _build_args(run_id=run_id, kind="staging", output_dir=str(output_dir))
        with pytest.raises(SystemExit):
            _run_purge_run_id(args, output_format="json")

        events = _read_audit_events(output_dir / "audit_log.jsonl")
        failed = next(e for e in events if e["event"] == "data.erasure_failed")
        assert failed["error_class"] == "PathTraversalRefused"
        # (a) PII marker masked out of the persisted error_message (the field
        # this finding routes through the sanitiser; ``target_id`` carries the
        # caller-supplied run_id verbatim by design and is out of scope here).
        assert leaked not in failed["error_message"]
        # (b) Length-bounded by the shared cap.
        assert len(failed["error_message"]) <= _purge._AUDIT_ERROR_MESSAGE_MAX + len("…[truncated]")

    def test_path_inside_output_dir_accepts_legitimate_target(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._purge import _path_inside_output_dir

        output_dir = tmp_path / "run1"
        output_dir.mkdir()
        inside = output_dir / "final_model.staging.fg-ok"
        inside.mkdir()
        assert _path_inside_output_dir(inside, str(output_dir)) is True

    def test_path_inside_output_dir_rejects_escape(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._purge import _path_inside_output_dir

        output_dir = tmp_path / "run1"
        output_dir.mkdir()
        outside = tmp_path / "victim"
        outside.mkdir()
        assert _path_inside_output_dir(outside, str(output_dir)) is False


# ---------------------------------------------------------------------------
# Facade re-exports (test that public surface resolves)
# ---------------------------------------------------------------------------


class TestFacadeReExports:
    def test_purge_helpers_reachable_via_cli_facade(self) -> None:
        from forgelm import cli as _cli_facade

        for name in (
            "_run_purge_cmd",
            "_run_purge_row_id",
            "_run_purge_run_id",
            "_run_purge_check_policy",
            "_resolve_salt",
            "_hash_target_id",
            "_find_matching_rows",
            "_atomic_rewrite_dropping_lines",
            "_scan_retention_violations",
        ):
            assert hasattr(_cli_facade, name), f"forgelm.cli must re-export {name!r}"


# ---------------------------------------------------------------------------
# F-P5-OPUS-07 — error_message masked + PII-redacted + length-bounded before
# it enters the append-only audit chain (design gdpr_erasure.md §6).
# ---------------------------------------------------------------------------


class TestErasureFailedErrorMessageSanitised:
    def test_sanitise_helper_redacts_email_and_bounds_length(self) -> None:
        from forgelm.cli.subcommands._purge import (
            _AUDIT_ERROR_MESSAGE_MAX,
            _sanitise_audit_error_message,
        )

        raw = "rewrite failed near row {'email': 'ali@example.com'} " + ("x" * 500)
        out = _sanitise_audit_error_message(raw)
        assert "ali@example.com" not in out, "email must be masked by the PII regex pass"
        assert len(out) <= _AUDIT_ERROR_MESSAGE_MAX + len("…[truncated]")

    def test_row_erasure_failure_error_message_masked_and_bounded(self, tmp_path: Path, monkeypatch) -> None:
        """Atomic-rewrite OSError whose message embeds an email + a long
        body must NOT land that PII in the audit chain, and must be capped.
        """
        from forgelm.cli.subcommands import _purge
        from forgelm.cli.subcommands._purge import _run_purge_cmd

        corpus = tmp_path / "train.jsonl"
        _seed_corpus(corpus, [{"id": "row-Z", "text": "subject data"}])

        leaked = "ali@example.com"
        long_tail = "Y" * 600

        def _boom(*_a, **_k):
            raise OSError(f"write failed at row containing {leaked} :: {long_tail}")

        monkeypatch.setattr(_purge, "_atomic_rewrite_dropping_lines", _boom)

        args = _build_args(row_id="row-Z", corpus=str(corpus), output_dir=str(tmp_path))
        with pytest.raises(SystemExit) as ei:
            _run_purge_cmd(args, output_format="json")
        assert ei.value.code == _purge.EXIT_TRAINING_ERROR

        events = _read_audit_events(tmp_path / "audit_log.jsonl")
        failed = next(e for e in events if e["event"] == "data.erasure_failed")
        assert failed["error_class"] == "OSError"
        # PII masked.
        assert leaked not in failed["error_message"]
        # Bounded (not the full 600-char tail).
        assert len(failed["error_message"]) <= _purge._AUDIT_ERROR_MESSAGE_MAX + len("…[truncated]")
        # And the raw email never appears ANYWHERE in the persisted chain.
        assert leaked not in (tmp_path / "audit_log.jsonl").read_text()


# ---------------------------------------------------------------------------
# Finding 1 (CRITICAL) — non-UTF-8 corpus must close the audit chain with
# data.erasure_failed and exit EXIT_TRAINING_ERROR, never leave a dangling
# data.erasure_requested (Art. 12 record-keeping integrity).
# ---------------------------------------------------------------------------


class TestNonUtf8Corpus:
    # ``\xff`` is never valid in a UTF-8 stream, so text-mode iteration of a
    # line carrying it raises UnicodeDecodeError deterministically.
    _BAD_CORPUS = b'{"id": "row-1", "text": "valid ascii"}\n{"id": "row-2", "text": "legacy \xff byte"}\n'

    def test_non_utf8_corpus_closes_chain_with_failed_and_exits_training_error(self, tmp_path: Path, capsys) -> None:
        from forgelm.cli.subcommands import _purge
        from forgelm.cli.subcommands._purge import _run_purge_cmd

        corpus = tmp_path / "train.jsonl"
        corpus.write_bytes(self._BAD_CORPUS)

        args = _build_args(row_id="row-1", corpus=str(corpus), output_dir=str(tmp_path))
        with pytest.raises(SystemExit) as ei:
            _run_purge_cmd(args, output_format="json")
        # A DOCUMENTED exit code (2), not Python's uncaught-exception default.
        assert ei.value.code == _purge.EXIT_TRAINING_ERROR

        payload = json.loads(capsys.readouterr().out)
        assert payload["success"] is False

        # The audit chain is CLOSED: request → failed, never a dangling request.
        events = _read_audit_events(tmp_path / "audit_log.jsonl")
        names = [e["event"] for e in events]
        assert "data.erasure_requested" in names
        assert "data.erasure_failed" in names
        assert "data.erasure_completed" not in names
        assert names.index("data.erasure_requested") < names.index("data.erasure_failed")
        failed = next(e for e in events if e["event"] == "data.erasure_failed")
        assert failed["error_class"] == "UnicodeDecodeError"

        # Fail-closed: the corpus is left byte-for-byte unchanged.
        assert corpus.read_bytes() == self._BAD_CORPUS

    def test_atomic_rewrite_cleans_temp_file_on_non_utf8(self, tmp_path: Path) -> None:
        """The widened ``(OSError, UnicodeDecodeError)`` handler in
        ``_atomic_rewrite_dropping_lines`` must clean up the mkstemp temp
        file when the source turns out to carry non-UTF-8 bytes mid-read —
        UnicodeDecodeError is NOT an OSError, so the narrow handler would
        have orphaned the temp file."""
        from forgelm.cli.subcommands._purge import _atomic_rewrite_dropping_lines

        corpus = tmp_path / "train.jsonl"
        corpus.write_bytes(self._BAD_CORPUS)

        with pytest.raises(UnicodeDecodeError):
            _atomic_rewrite_dropping_lines(str(corpus), [1])

        leftovers = list(tmp_path.glob(".forgelm_purge_*.tmp"))
        assert leftovers == [], f"orphaned temp file(s) after decode failure: {leftovers}"
        # Source corpus untouched.
        assert corpus.read_bytes() == self._BAD_CORPUS


# ---------------------------------------------------------------------------
# Finding 2 (MEDIUM) — a --config load failure that silences the
# external-copies warning must be visible (WARNING), not swallowed at DEBUG.
# ---------------------------------------------------------------------------


class TestConfigLoadWarningVisibility:
    def test_bad_config_in_row_mode_logs_warning_not_debug(self, tmp_path: Path, caplog) -> None:
        import logging

        from forgelm.cli.subcommands._purge import _run_purge_cmd

        corpus = tmp_path / "train.jsonl"
        _seed_corpus(corpus, [{"id": "row-1", "text": "x"}])
        # A non-existent --config → FileNotFoundError (OSError) → best-effort
        # fallback.  Before the fix this logged at DEBUG (invisible under the
        # INFO default AND the WARNING-forced JSON mode).
        missing_config = tmp_path / "does_not_exist.yaml"
        args = _build_args(
            row_id="row-1",
            corpus=str(corpus),
            output_dir=str(tmp_path),
            config=str(missing_config),
        )
        with caplog.at_level(logging.WARNING, logger="forgelm.cli"):
            with pytest.raises(SystemExit) as ei:
                _run_purge_cmd(args, output_format="json")
        # The erasure still succeeds config-free.
        assert ei.value.code == 0
        warnings = [r for r in caplog.records if r.levelname == "WARNING" and "config" in r.message.lower()]
        assert warnings, (
            "a --config load failure must surface at WARNING so an operator who explicitly "
            "passed --config sees that external-copies warning detection was disabled"
        )


# ---------------------------------------------------------------------------
# Finding 4 (MEDIUM) — warning events must carry ONLY their catalog-scoped
# extras; a shared mutable dict cross-contaminated payloads.
# ---------------------------------------------------------------------------


class TestWarningPayloadIsolation:
    def test_memorisation_and_synthetic_warnings_do_not_cross_contaminate(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._purge import _run_purge_cmd

        # Trigger BOTH the memorisation and synthetic-data warnings in one run.
        (tmp_path / "final_model").mkdir()
        (tmp_path / "final_model.staging.fg-run1").mkdir()
        (tmp_path / "synthetic_data.jsonl").write_text("{}\n")

        corpus = tmp_path / "train.jsonl"
        _seed_corpus(corpus, [{"id": "row-1", "text": "x"}])
        args = _build_args(row_id="row-1", corpus=str(corpus), output_dir=str(tmp_path))
        with pytest.raises(SystemExit):
            _run_purge_cmd(args, output_format="json")

        events = _read_audit_events(tmp_path / "audit_log.jsonl")
        mem = next(e for e in events if e["event"] == "data.erasure_warning_memorisation")
        syn = next(e for e in events if e["event"] == "data.erasure_warning_synthetic_data_present")
        # Each event carries ONLY its own catalog-scoped field.
        assert "affected_run_ids" in mem
        assert "synthetic_files" not in mem, "memorisation event leaked the synthetic-data-scoped field"
        assert "synthetic_files" in syn
        assert "affected_run_ids" not in syn, "synthetic-data event leaked the memorisation-scoped field"


# ---------------------------------------------------------------------------
# Finding 5 (LOW) — run-scoped erasure records the PLAIN run_id as target_id
# (design §5.3); the module docstring now documents this asymmetry.
# ---------------------------------------------------------------------------


class TestRunModeTargetIdPlain:
    def test_run_mode_target_id_is_plain_run_id_not_hashed(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._purge import _run_purge_cmd

        run_id = "fg-plainrun01"
        staging = tmp_path / f"final_model.staging.{run_id}"
        staging.mkdir()
        (staging / "w.bin").write_bytes(b"x" * 32)

        args = _build_args(run_id=run_id, kind="staging", output_dir=str(tmp_path))
        with pytest.raises(SystemExit) as ei:
            _run_purge_cmd(args, output_format="json")
        assert ei.value.code == 0

        events = _read_audit_events(tmp_path / "audit_log.jsonl")
        req = next(e for e in events if e["event"] == "data.erasure_requested")
        # Run mode keeps target_id in the clear (operational id, not subject
        # input); a future "hash everything" change would break cross-tool
        # correlation and surface here.
        assert req["target_id"] == run_id


# ---------------------------------------------------------------------------
# Finding 7 (LOW) — --dry-run intentionally creates output_dir + the
# persistent salt file + the audit-intent record (documented side effects).
# ---------------------------------------------------------------------------


class TestDryRunSideEffects:
    def test_dry_run_creates_salt_and_audit_intent_by_design(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._purge import _run_purge_cmd

        corpus = tmp_path / "train.jsonl"
        _seed_corpus(corpus, [{"id": "row-X", "text": "x"}])
        args = _build_args(row_id="row-X", corpus=str(corpus), output_dir=str(tmp_path), dry_run=True)
        with pytest.raises(SystemExit):
            _run_purge_cmd(args, output_format="json")

        # The salt file + audit-intent record are documented dry-run side
        # effects (the chain must record the previewed erasure with its
        # hashed target_id, which requires the persistent salt).
        assert (tmp_path / ".forgelm_audit_salt").is_file()
        assert (tmp_path / "audit_log.jsonl").is_file()
        events = _read_audit_events(tmp_path / "audit_log.jsonl")
        names = [e["event"] for e in events]
        assert "data.erasure_requested" in names
        assert "data.erasure_completed" in names
