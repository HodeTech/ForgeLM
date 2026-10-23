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


class TestImplicitOutputDirWarning:
    """`--output-dir` defaults to the corpus directory; the user is told, because the memorisation check looks for
    `<output_dir>/final_model/` and a training run rarely writes its model next to the corpus (#102)."""

    def test_the_implicit_directory_is_named_in_a_warning(self, tmp_path: Path, caplog) -> None:
        import logging

        from forgelm.cli.subcommands._purge import _run_purge_cmd

        corpus = tmp_path / "data" / "train.jsonl"
        corpus.parent.mkdir()
        _seed_corpus(corpus, [{"id": "row-A", "text": "x"}])
        args = _build_args(row_id="row-A", corpus=str(corpus), output_dir=None)
        with caplog.at_level(logging.WARNING, logger="forgelm"):
            with pytest.raises(SystemExit) as ei:
                _run_purge_cmd(args, output_format="json")
        assert ei.value.code == 0
        warned = [r.getMessage() for r in caplog.records if "--output-dir not given" in r.getMessage()]
        assert len(warned) == 1
        assert str(corpus.parent) in warned[0] and os.path.join(str(corpus.parent), "final_model") in warned[0]

    def test_an_explicit_output_dir_is_silent(self, tmp_path: Path, caplog) -> None:
        import logging

        from forgelm.cli.subcommands._purge import _run_purge_cmd

        corpus = tmp_path / "train.jsonl"
        _seed_corpus(corpus, [{"id": "row-A", "text": "x"}])
        args = _build_args(row_id="row-A", corpus=str(corpus), output_dir=str(tmp_path))
        with caplog.at_level(logging.WARNING, logger="forgelm"):
            with pytest.raises(SystemExit):
                _run_purge_cmd(args, output_format="json")
        assert not [r for r in caplog.records if "--output-dir not given" in r.getMessage()]

    def test_no_memorisation_event_without_a_final_model_in_the_resolved_directory(self, tmp_path: Path) -> None:
        # The default directory is the corpus's; a model written elsewhere is invisible to the check. The warning
        # above is what tells the operator.
        from forgelm.cli.subcommands._purge import _run_purge_cmd

        corpus = tmp_path / "data" / "train.jsonl"
        corpus.parent.mkdir()
        _seed_corpus(corpus, [{"id": "row-A", "text": "x"}])
        (tmp_path / "training_out" / "final_model").mkdir(parents=True)
        args = _build_args(row_id="row-A", corpus=str(corpus), output_dir=None)
        with pytest.raises(SystemExit):
            _run_purge_cmd(args, output_format="json")
        events = _read_audit_events(corpus.parent / "audit_log.jsonl")
        assert "data.erasure_warning_memorisation" not in [e["event"] for e in events]


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

    def test_artefacts_kind_finds_nothing_in_the_standard_fixed_name_bundle(
        self, tmp_path: Path, minimal_config, capsys
    ) -> None:
        # CR-A10-002 / DOC-D1B05-005: the exporter writes fixed basenames that carry no run id, so
        # ``--kind artefacts`` cannot find the real bundle.  It fails safe (deletes nothing, exits 1).
        # The bundle is built by the real exporter, with the run id also written *inside* the files,
        # so the test still trips when the resolver learns to prove ownership from file contents
        # rather than from names.
        from forgelm.cli.subcommands._purge import _run_purge_cmd
        from forgelm.compliance import export_compliance_artifacts, generate_training_manifest
        from forgelm.config import ForgeConfig

        run_id = "fg-abc123def456"
        compliance_dir = tmp_path / "compliance"
        manifest = generate_training_manifest(ForgeConfig(**minimal_config()), metrics={"eval_loss": 0.5})
        manifest["run_id"] = run_id
        bundle = [Path(p).name for p in export_compliance_artifacts(manifest, str(compliance_dir))]
        (compliance_dir / "data_governance_report.json").write_text(f'{{"run_id": "{run_id}"}}', encoding="utf-8")
        bundle.append("data_governance_report.json")

        args = _build_args(run_id=run_id, kind="artefacts", output_dir=str(tmp_path))
        with pytest.raises(SystemExit) as ei:
            _run_purge_cmd(args, output_format="json")
        tripwire = (
            "`purge --kind artefacts` now finds the standard compliance bundle.  Delete this test and the "
            "'finds nothing in the standard bundle' warning in docs/guides/gdpr_erasure{,-tr}.md, "
            "docs/reference/purge_subcommand{,-tr}.md and docs/usermanuals/{en,tr}/compliance/gdpr-erasure.md, "
            "and update test_usermanual_reference_consistency.py::test_purge_docs_warn_that_artefacts_misses_the_standard_bundle"
        )
        assert ei.value.code == 1, tripwire
        assert all((compliance_dir / name).exists() for name in bundle), tripwire
        failed = [e for e in _read_audit_events(tmp_path / "audit_log.jsonl") if e["event"] == "data.erasure_failed"]
        assert [e["error_class"] for e in failed] == ["NoMatchingArtefacts"], tripwire
        # The operator sees why: the message and the audit event both say the bundle has fixed names.
        assert "fixed filenames" in capsys.readouterr().out
        assert "fixed filenames" in failed[0]["error_message"]

    def test_a_generic_run_id_matches_the_fixed_basenames_as_the_docs_warn(self, tmp_path: Path, capsys) -> None:
        # The docs say the standard bundle is not found for a real run id, but that a generic token of a fixed name
        # (``json``, ``report``) does match it and deletes it: the reason to always pass the full run id.  If the
        # resolver learns to prove ownership, update the caveat in the six documents together with this test.
        from forgelm.cli.subcommands._purge import _run_purge_cmd

        compliance_dir = tmp_path / "compliance"
        compliance_dir.mkdir()
        (compliance_dir / "compliance_report.json").write_text("{}")
        (compliance_dir / "training_manifest.yaml").write_text("{}")

        args = _build_args(run_id="json", kind="artefacts", output_dir=str(tmp_path), dry_run=True)
        with pytest.raises(SystemExit) as ei:
            _run_purge_cmd(args, output_format="json")
        assert ei.value.code == 0
        would_delete = json.loads(capsys.readouterr().out)["would_delete"]
        assert [Path(p).name for p in would_delete] == ["compliance_report.json"]

    def test_only_the_purge_reader_names_the_legacy_staging_marker(self) -> None:
        # The docs tell operators to remove an unmarked legacy ``final_model.staging/``
        # themselves because ForgeLM never writes ``staging_run_id``.  Every use of that
        # marker name in the package must be the read-only ownership check; a writer
        # anywhere would make the documented advice stale (update docs + this test).
        import ast

        forgelm_dir = Path(__file__).resolve().parent.parent / "forgelm"
        total = 0
        inside_reader = 0
        reader_calls: set[str] = set()
        for path in sorted(forgelm_dir.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            total += sum(1 for n in ast.walk(tree) if isinstance(n, ast.Constant) and n.value == "staging_run_id")
            for fn in (
                n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_legacy_staging_owned_by"
            ):
                inside_reader += sum(
                    1 for n in ast.walk(fn) if isinstance(n, ast.Constant) and n.value == "staging_run_id"
                )
                reader_calls |= {
                    n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "?")
                    for n in ast.walk(fn)
                    if isinstance(n, ast.Call)
                }
        assert total == inside_reader == 1, "the marker name must appear only in _legacy_staging_owned_by"
        # A name assembled from pieces ("staging_" + "run_id", f"staging_{key}") would dodge the literal count above.
        assembled = [
            f"{path.relative_to(forgelm_dir.parent)}:{n.lineno}"
            for path in sorted(forgelm_dir.rglob("*.py"))
            for n in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(n, (ast.JoinedStr, ast.BinOp))
            and any(isinstance(c, ast.Constant) and c.value == "staging_" for c in ast.walk(n))
        ]
        assert assembled == [], f"build the legacy marker name only inside the reader, never from pieces: {assembled}"
        assert reader_calls <= {"is_file", "read_text", "strip"}, (
            f"the marker reader must stay read-only: {reader_calls}"
        )


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

    def test_check_policy_retention_only_config_applies_its_horizon(self, tmp_path: Path, capsys) -> None:
        """#1054: a file holding only `retention:` is accepted, and its horizon is the one applied.

        The report is 60 days old: past the configured 30-day horizon, within the 90-day default,
        so a loader that fell back to the defaults would report nothing.
        """
        import time

        from forgelm.cli.subcommands._purge import _run_purge_cmd

        report = tmp_path / "data_audit_report.json"
        report.write_text("{}")
        sixty_days_ago = time.time() - 60 * 86400
        os.utime(report, (sixty_days_ago, sixty_days_ago))
        cfg = tmp_path / "retention-only.yaml"
        cfg.write_text("retention:\n  ephemeral_artefact_retention_days: 30\n")
        args = _build_args(check_policy=True, config=str(cfg), output_dir=str(tmp_path))
        with pytest.raises(SystemExit) as ei:
            _run_purge_cmd(args, output_format="json")
        assert ei.value.code == 0
        data = json.loads(capsys.readouterr().out)
        assert data["success"] is True
        assert data["count"] == 1
        assert data["violations"][0]["artefact_kind"] == "data_audit_report"
        assert data["violations"][0]["horizon_days"] == 30

    def test_check_policy_retention_only_null_block_has_nothing_to_enforce(self, tmp_path: Path, capsys) -> None:
        """#1054: `retention:` with no value behaves as in a full config: the "nothing to enforce" note, exit 0."""
        from forgelm.cli.subcommands._purge import _run_purge_cmd

        cfg = tmp_path / "retention-null.yaml"
        cfg.write_text("retention:\n")
        args = _build_args(check_policy=True, config=str(cfg), output_dir=str(tmp_path))
        with pytest.raises(SystemExit) as ei:
            _run_purge_cmd(args, output_format="json")
        assert ei.value.code == 0
        data = json.loads(capsys.readouterr().out)
        assert data["violations"] == []
        assert "count" not in data
        assert "nothing to enforce" in data["note"]

    @pytest.mark.parametrize(
        "body",
        [
            "retention:\n  ephemeral_artefact_retention_days: 30\nretension:\n  staging_ttl_days: 1\n",
            "retention:\n  ephemeral_artefact_retention_days: 30\ntraining:\n  num_train_epochs: 3\n",
        ],
        ids=["misspelt-top-level-key", "partial-training-config"],
    )
    def test_check_policy_other_top_level_keys_mean_a_full_config(self, tmp_path: Path, body: str) -> None:
        """#1054: only a file whose sole key is `retention` skips full validation; a typo is not ignored."""
        from forgelm.cli.subcommands._purge import _run_purge_cmd

        cfg = tmp_path / "config.yaml"
        cfg.write_text(body)
        args = _build_args(check_policy=True, config=str(cfg), output_dir=str(tmp_path))
        with pytest.raises(SystemExit) as ei:
            _run_purge_cmd(args, output_format="json")
        assert ei.value.code == 1

    def test_check_policy_retention_only_error_omits_raw_input(self, tmp_path: Path, capsys) -> None:
        """#1054: like a full config's, a retention-only validation error names the field but not the value."""
        from forgelm.cli.subcommands._purge import _run_purge_cmd

        cfg = tmp_path / "retention-secret.yaml"
        cfg.write_text("retention:\n  ephemeral_artefact_retention_days: fake-secret-value\n  api_token: fake-token\n")
        args = _build_args(check_policy=True, config=str(cfg), output_dir=str(tmp_path))
        with pytest.raises(SystemExit) as ei:
            _run_purge_cmd(args, output_format="json")
        assert ei.value.code == 1
        error = json.loads(capsys.readouterr().out)["error"]
        assert "ephemeral_artefact_retention_days" in error
        assert "api_token" in error
        assert "fake-secret-value" not in error
        assert "fake-token" not in error

    def test_check_policy_missing_config_exits_config_error(self, tmp_path: Path, capsys) -> None:
        """An explicit `--config` that does not exist exits 1 with the usual not-found message."""
        from forgelm.cli.subcommands._purge import _run_purge_cmd

        missing = tmp_path / "missing.yaml"
        args = _build_args(check_policy=True, config=str(missing), output_dir=str(tmp_path))
        with pytest.raises(SystemExit) as ei:
            _run_purge_cmd(args, output_format="json")
        assert ei.value.code == 1
        assert f"Configuration file not found: {missing}" in json.loads(capsys.readouterr().out)["error"]

    def test_check_policy_retention_only_invalid_exits_config_error(self, tmp_path: Path) -> None:
        """#1054: an invalid retention block in a retention-only file exits EXIT_CONFIG_ERROR (1)."""
        from forgelm.cli.subcommands._purge import _run_purge_cmd

        cfg = tmp_path / "retention-invalid.yaml"
        cfg.write_text("retention:\n  invalid_horizon_field: 30\n")
        args = _build_args(check_policy=True, config=str(cfg), output_dir=str(tmp_path))
        with pytest.raises(SystemExit) as ei:
            _run_purge_cmd(args, output_format="json")
        assert ei.value.code == 1


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
# Opus-review fix — warning events must carry the FULL `completed`-field set
# (docs/reference/audit_event_catalog.md: "All `completed` fields + X"), not
# just `request_fields`.  Regression for the payload-shape defect where
# `data.erasure_warning_*` events omitted bytes_freed / files_modified /
# pre_erasure_line_number / match_count.
# ---------------------------------------------------------------------------


class TestWarningPayloadCatalogCompleteness:
    def test_warning_event_carries_completed_field_set(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._purge import _run_purge_cmd

        (tmp_path / "final_model").mkdir()
        (tmp_path / "final_model.staging.fg-run1").mkdir()

        corpus = tmp_path / "train.jsonl"
        _seed_corpus(corpus, [{"id": "row-1", "text": "x"}])
        args = _build_args(row_id="row-1", corpus=str(corpus), output_dir=str(tmp_path))
        with pytest.raises(SystemExit):
            _run_purge_cmd(args, output_format="json")

        events = _read_audit_events(tmp_path / "audit_log.jsonl")
        completed = next(e for e in events if e["event"] == "data.erasure_completed")
        warn_evt = next(e for e in events if e["event"] == "data.erasure_warning_memorisation")
        for field in ("bytes_freed", "files_modified", "pre_erasure_line_number", "match_count"):
            assert field in warn_evt, f"warning event missing catalog-mandated completed field {field!r}"
            assert warn_evt[field] == completed[field]
        # The scoped extra must still be present alongside the completed set.
        assert "affected_run_ids" in warn_evt


# ---------------------------------------------------------------------------
# Memorisation artefacts (CR-A10-022, CR-A10-042).  Weights trained on an erased
# row live in the model directory, in the ``<final_model_dir>.staging.<run_id>``
# sibling a run awaiting human approval leaves, and in ``checkpoint-*``
# directories; the warning must find them under the layout the trainer really
# writes, with or without ``--config``, and for a non-default ``final_model_dir``.
# ---------------------------------------------------------------------------

_MISSING_RUN_ID_HINT = "unknown"
_TRAINING_CONFIG = """
model:
  name_or_path: gpt2
  backend: transformers
lora:
  r: 8
training:
  trainer_type: sft
  output_dir: '{output_dir}'
  final_model_dir: {final_model_dir}
  num_train_epochs: 1
data:
  dataset_name_or_path: train.jsonl
{extra}"""


def _write_training_config(
    tmp_path: Path, output_dir: Path, final_model_dir: str = "final_model", extra: str = ""
) -> Path:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        _TRAINING_CONFIG.format(output_dir=output_dir, final_model_dir=final_model_dir, extra=extra), encoding="utf-8"
    )
    return config_path


def _purge_one_row(tmp_path: Path, *, config: Path | None = None, output_dir: Path | None = None) -> list[dict]:
    """Erase ``row-1`` from a one-row corpus and return the audit events the erasure wrote.

    Without *output_dir* the corpus directory is used, as it is when the operator omits ``--output-dir``.
    """
    from forgelm.cli.subcommands._purge import _run_purge_cmd

    corpus = tmp_path / "data" / "train.jsonl"
    _seed_corpus(corpus, [{"id": "row-1", "text": "x"}])
    args = _build_args(
        row_id="row-1",
        corpus=str(corpus),
        output_dir=str(output_dir) if output_dir else None,
        config=str(config) if config else None,
    )
    with pytest.raises(SystemExit) as ei:
        _run_purge_cmd(args, output_format="json")
    assert ei.value.code == 0
    return _read_audit_events((output_dir or corpus.parent) / "audit_log.jsonl")


def _memorisation_event(events: list[dict]) -> dict | None:
    return next((e for e in events if e["event"] == "data.erasure_warning_memorisation"), None)


class TestMemorisationArtefactDetection:
    def test_checkpoints_under_the_configured_output_dir_fire_the_warning(self, tmp_path: Path) -> None:
        training_out = tmp_path / "training_out"
        (training_out / "checkpoint-100").mkdir(parents=True)
        (training_out / "checkpoint-200").mkdir()
        config = _write_training_config(tmp_path, training_out)

        warning = _memorisation_event(_purge_one_row(tmp_path, config=config, output_dir=tmp_path / "audit"))

        assert warning is not None
        assert warning["memorisation_artefact_paths"] == [
            str(training_out / "checkpoint-100"),
            str(training_out / "checkpoint-200"),
        ]
        assert warning["affected_run_ids"][0].startswith(_MISSING_RUN_ID_HINT)

    def test_default_invocation_finds_the_model_through_the_config(self, tmp_path: Path) -> None:
        """No ``--output-dir`` (so the corpus directory is used) and no checkpoints left: the model under the
        configured ``training.output_dir`` must still be found."""
        training_out = tmp_path / "checkpoints"
        (training_out / "final_model").mkdir(parents=True)
        config = _write_training_config(tmp_path, training_out)

        warning = _memorisation_event(_purge_one_row(tmp_path, config=config))

        assert warning is not None
        assert warning["memorisation_artefact_paths"] == [str(training_out / "final_model")]

    def test_custom_final_model_dir_is_found(self, tmp_path: Path) -> None:
        training_out = tmp_path / "ck"
        (training_out / "my_model").mkdir(parents=True)
        config = _write_training_config(tmp_path, training_out, final_model_dir="my_model")

        warning = _memorisation_event(_purge_one_row(tmp_path, config=config, output_dir=training_out))

        assert warning is not None
        assert warning["memorisation_artefact_paths"] == [str(training_out / "my_model")]

    def test_nested_final_model_dir_and_its_staging_sibling_are_found(self, tmp_path: Path) -> None:
        training_out = tmp_path / "ck"
        (training_out / "models" / "final").mkdir(parents=True)
        (training_out / "models" / "final.staging.fg-run1").mkdir()
        config = _write_training_config(tmp_path, training_out, final_model_dir="models/final")

        warning = _memorisation_event(_purge_one_row(tmp_path, config=config, output_dir=tmp_path / "audit"))

        assert warning is not None
        assert warning["memorisation_artefact_paths"] == [
            str(training_out / "models" / "final"),
            str(training_out / "models" / "final.staging.fg-run1"),
        ]
        assert warning["affected_run_ids"] == ["fg-run1"]

    @pytest.mark.parametrize("model_dir", ["final_model", "my_model"])
    def test_staging_directory_of_a_run_awaiting_approval_is_found(self, tmp_path: Path, model_dir: str) -> None:
        """The run exits 4 holding only ``<final_model_dir>.staging.<run_id>`` — no model directory, no checkpoint."""
        training_out = tmp_path / "ck"
        (training_out / f"{model_dir}.staging.fg-run1").mkdir(parents=True)
        config = _write_training_config(tmp_path, training_out, final_model_dir=model_dir)

        warning = _memorisation_event(_purge_one_row(tmp_path, config=config, output_dir=tmp_path / "audit"))

        assert warning is not None
        assert warning["memorisation_artefact_paths"] == [str(training_out / f"{model_dir}.staging.fg-run1")]
        assert warning["affected_run_ids"] == ["fg-run1"]

    def test_unscoped_legacy_staging_directory_is_found(self, tmp_path: Path) -> None:
        (tmp_path / "ck" / "final_model.staging").mkdir(parents=True)

        warning = _memorisation_event(_purge_one_row(tmp_path, output_dir=tmp_path / "ck"))

        assert warning is not None
        assert warning["memorisation_artefact_paths"] == [str(tmp_path / "ck" / "final_model.staging")]

    def test_lookalike_names_are_not_reported(self, tmp_path: Path) -> None:
        training_out = tmp_path / "ck"
        for name in ("final_model.stagingX", "final_model_old", "checkpoint", "my-checkpoint-1", "x.staging.fg-run1"):
            (training_out / name).mkdir(parents=True, exist_ok=True)
        (training_out / "checkpoint-9").write_text("a file, not a checkpoint directory")

        assert _memorisation_event(_purge_one_row(tmp_path, output_dir=training_out)) is None

    def test_without_config_the_output_dir_is_scanned_for_checkpoints_and_staging(self, tmp_path: Path) -> None:
        """``forgelm purge --row-id alice --corpus data/train.jsonl --output-dir out`` with no ``--config``."""
        out = tmp_path / "out"
        (out / "checkpoint-1").mkdir(parents=True)
        (out / "checkpoint-2").mkdir()
        (out / "final_model.staging.fg-run1").mkdir()

        warning = _memorisation_event(_purge_one_row(tmp_path, output_dir=out))

        assert warning is not None
        assert warning["memorisation_artefact_paths"] == [
            str(out / "checkpoint-1"),
            str(out / "checkpoint-2"),
            str(out / "final_model.staging.fg-run1"),
        ]
        assert warning["affected_run_ids"] == ["fg-run1"]

    def test_without_config_artefacts_outside_the_output_dir_stay_invisible(self, tmp_path: Path) -> None:
        (tmp_path / "training_out" / "checkpoint-100").mkdir(parents=True)

        assert _memorisation_event(_purge_one_row(tmp_path, output_dir=tmp_path / "audit")) is None

    def test_no_artefacts_no_warning_with_config(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)  # the merge block's default ``./merged_model`` is relative to the cwd
        training_out = tmp_path / "training_out"
        training_out.mkdir()
        config = _write_training_config(tmp_path, training_out, extra="merge:\n  enabled: false\n")

        assert _memorisation_event(_purge_one_row(tmp_path, config=config, output_dir=tmp_path / "audit")) is None

    def test_missing_training_directory_is_silent(self, tmp_path: Path, caplog) -> None:
        """A config whose run never started has no directory to scan: no warning and no log noise."""
        config = _write_training_config(tmp_path, tmp_path / "never_trained")

        with caplog.at_level("WARNING"):
            events = _purge_one_row(tmp_path, config=config, output_dir=tmp_path / "audit")

        assert _memorisation_event(events) is None
        assert not [r for r in caplog.records if "could not scan" in r.getMessage()]

    def test_pipeline_stage_layouts_are_scanned(self, tmp_path: Path) -> None:
        stage_one, stage_two = tmp_path / "stage_one", tmp_path / "stage_two"
        (stage_one / "stage_one_model").mkdir(parents=True)
        (stage_two / "checkpoint-5").mkdir(parents=True)
        pipeline = (
            "pipeline:\n"
            f"  output_dir: '{tmp_path / 'pipeline_run'}'\n"
            "  stages:\n"
            "    - name: sft\n"
            "      training:\n"
            "        trainer_type: sft\n"
            f"        output_dir: '{stage_one}'\n"
            "        final_model_dir: stage_one_model\n"
            "    - name: dpo\n"
            "      training:\n"
            "        trainer_type: dpo\n"
            f"        output_dir: '{stage_two}'\n"
        )
        config = _write_training_config(tmp_path, tmp_path / "root_unused", extra=pipeline)

        warning = _memorisation_event(_purge_one_row(tmp_path, config=config, output_dir=tmp_path / "audit"))

        assert warning is not None
        assert warning["memorisation_artefact_paths"] == [
            str(stage_one / "stage_one_model"),
            str(stage_two / "checkpoint-5"),
        ]

    def test_merge_output_dir_is_reported_when_the_merge_ran(self, tmp_path: Path) -> None:
        merged = tmp_path / "merged_model"
        merged.mkdir()
        merge = (
            "merge:\n  enabled: true\n  method: linear\n  models:\n    - path: ./a\n      weight: 0.5\n"
            f"    - path: ./b\n      weight: 0.5\n  output_dir: '{merged}'\n"
        )
        config = _write_training_config(tmp_path, tmp_path / "never_trained", extra=merge)

        warning = _memorisation_event(_purge_one_row(tmp_path, config=config, output_dir=tmp_path / "audit"))

        assert warning is not None
        assert warning["memorisation_artefact_paths"] == [str(merged)]

    def test_the_same_directory_reached_two_ways_is_listed_once(self, tmp_path: Path) -> None:
        """``--output-dir`` is the training directory the config also names."""
        training_out = tmp_path / "ck"
        (training_out / "final_model").mkdir(parents=True)
        (training_out / "checkpoint-1").mkdir()
        config = _write_training_config(tmp_path, training_out)

        warning = _memorisation_event(_purge_one_row(tmp_path, config=config, output_dir=training_out))

        assert warning is not None
        assert warning["memorisation_artefact_paths"] == [
            str(training_out / "checkpoint-1"),
            str(training_out / "final_model"),
        ]

    def test_symlinked_checkpoint_is_reported_where_it_is_named(self, tmp_path: Path) -> None:
        """Checkpoints are often symlinked to a scratch volume; the weights are still there, so the warning
        lists the link instead of dropping it."""
        scratch = tmp_path / "scratch_volume" / "checkpoint-100"
        scratch.mkdir(parents=True)
        training_out = tmp_path / "ck"
        training_out.mkdir()
        try:
            (training_out / "checkpoint-100").symlink_to(scratch, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("symlink creation not permitted on this platform")

        warning = _memorisation_event(_purge_one_row(tmp_path, output_dir=training_out))

        assert warning is not None
        assert warning["memorisation_artefact_paths"] == [str(training_out / "checkpoint-100")]

    def test_unreadable_directory_is_logged_and_the_erasure_still_completes(
        self, tmp_path: Path, monkeypatch, caplog
    ) -> None:
        """A directory that cannot be listed must not abort a purge whose row is already gone, and must not read
        as 'nothing found': the operator is told the check is incomplete."""
        training_out = tmp_path / "ck"
        (training_out / "checkpoint-1").mkdir(parents=True)
        literal = tmp_path / "out" / "final_model"
        literal.mkdir(parents=True)
        config = _write_training_config(tmp_path, training_out)
        real_scandir = os.scandir

        def flaky_scandir(path="."):
            if os.fspath(path) == str(training_out):
                raise PermissionError(errno.EACCES, "Permission denied", os.fspath(path))
            return real_scandir(path)

        monkeypatch.setattr("forgelm.cli.subcommands._purge.os.scandir", flaky_scandir)

        with caplog.at_level("WARNING"):
            events = _purge_one_row(tmp_path, config=config, output_dir=tmp_path / "out")

        assert "data.erasure_completed" in [e["event"] for e in events]
        assert [r for r in caplog.records if "could not scan" in r.getMessage() and str(training_out) in r.getMessage()]
        warning = _memorisation_event(events)
        assert warning is not None
        assert warning["memorisation_artefact_paths"] == [str(literal)]

    def test_unloadable_config_falls_back_to_the_output_dir_scan(self, tmp_path: Path, caplog) -> None:
        bad_config = tmp_path / "broken.yaml"
        bad_config.write_text("training: [unterminated", encoding="utf-8")
        (tmp_path / "out" / "final_model").mkdir(parents=True)

        with caplog.at_level("WARNING"):
            warning = _memorisation_event(_purge_one_row(tmp_path, config=bad_config, output_dir=tmp_path / "out"))

        assert warning is not None
        assert warning["memorisation_artefact_paths"] == [str(tmp_path / "out" / "final_model")]
        assert [r for r in caplog.records if "could not be loaded" in r.getMessage()]

    def test_output_dir_is_paired_with_the_configs_custom_model_dir(self, tmp_path: Path, monkeypatch) -> None:
        """The config names ``final_model_dir: my_model`` but a ``training.output_dir`` that is not reachable from
        here (a relative path, or a config copied between hosts); the operator points ``--output-dir`` at the
        directory that really holds ``my_model/``."""
        monkeypatch.chdir(tmp_path)
        real_out = tmp_path / "ck2"
        (real_out / "my_model").mkdir(parents=True)
        config = _write_training_config(tmp_path, tmp_path / "not_here", final_model_dir="my_model")

        warning = _memorisation_event(_purge_one_row(tmp_path, config=config, output_dir=real_out))

        assert warning is not None
        assert warning["memorisation_artefact_paths"] == [str(real_out / "my_model")]

    def test_non_utf8_audit_log_in_a_training_directory_does_not_abort_the_erasure(self, tmp_path: Path) -> None:
        """The run-id lookup reads audit logs outside ``--output-dir`` after the row is already gone: a log that
        cannot be decoded must leave the chain closed, not stranded at ``data.erasure_requested``."""
        training_out = tmp_path / "ck"
        (training_out / "final_model").mkdir(parents=True)
        (training_out / "audit_log.jsonl").write_bytes(b'{"event": "x"}\n\xff\xfe not utf-8\n')
        config = _write_training_config(tmp_path, training_out)

        events = _purge_one_row(tmp_path, config=config, output_dir=tmp_path / "audit")

        assert [e["event"] for e in events][:2] == ["data.erasure_requested", "data.erasure_completed"]
        warning = _memorisation_event(events)
        assert warning is not None
        assert warning["affected_run_ids"][0].startswith(_MISSING_RUN_ID_HINT)

    def test_unexaminable_entry_does_not_hide_its_siblings(self, tmp_path: Path) -> None:
        """A symlink loop named ``checkpoint-1`` cannot be stat'ed; the real ``checkpoint-2`` beside it is still
        reported."""
        training_out = tmp_path / "ck"
        (training_out / "checkpoint-2").mkdir(parents=True)
        try:
            (training_out / "checkpoint-1").symlink_to(training_out / "checkpoint-1", target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("symlink creation not permitted on this platform")

        warning = _memorisation_event(_purge_one_row(tmp_path, output_dir=training_out))

        assert warning is not None
        assert warning["memorisation_artefact_paths"] == [str(training_out / "checkpoint-2")]

    def test_the_staging_name_the_scan_looks_for_is_the_one_the_trainer_writes(self) -> None:
        """Drift guard: ``ForgeTrainer`` stages a run next to its model directory.  If this literal changes, update
        ``_scan_training_tree``, ``_staging_targets_for_run`` and ``_discover_per_run_staging_horizons`` with it."""
        import forgelm.trainer

        source = Path(forgelm.trainer.__file__).read_text(encoding="utf-8")

        assert 'f"{final_path}.staging.{self.audit.run_id}"' in source

    def test_granted_approval_in_the_training_audit_log_names_the_run(self, tmp_path: Path) -> None:
        """After ``forgelm approve`` the staging suffix is gone; the run's own audit log, under the configured
        training directory, still names it."""
        training_out = tmp_path / "ck"
        (training_out / "final_model").mkdir(parents=True)
        (training_out / "audit_log.jsonl").write_text(
            json.dumps({"event": "human_approval.granted", "run_id": "fg-granted"}) + "\n", encoding="utf-8"
        )
        config = _write_training_config(tmp_path, training_out)

        warning = _memorisation_event(_purge_one_row(tmp_path, config=config, output_dir=tmp_path / "audit"))

        assert warning is not None
        assert warning["affected_run_ids"] == ["fg-granted"]

    @pytest.mark.parametrize("model_dir", ["final_model", "my_model"])
    def test_staging_naming_is_shared_by_the_scan_the_run_purge_and_the_retention_report(
        self, tmp_path: Path, model_dir: str
    ) -> None:
        """One staging name, three consumers: if any of them drifts from ``<final_model_dir>.staging.<run_id>``
        (what ``ForgeTrainer`` writes) a run's staging directory is reported by one and invisible to another."""
        from forgelm.cli.subcommands._purge import (
            _discover_per_run_staging_horizons,
            _scan_memorisation_artefacts,
            _staging_targets_for_run,
        )

        staging = tmp_path / f"{model_dir}.staging.fg-run1"
        staging.mkdir()
        config = SimpleNamespace(training=SimpleNamespace(output_dir=str(tmp_path), final_model_dir=model_dir))

        assert _scan_memorisation_artefacts(str(tmp_path / "elsewhere"), config) == [str(staging)]
        assert _staging_targets_for_run(tmp_path, "fg-run1", [model_dir]) == [staging]
        assert [d[1] for d in _discover_per_run_staging_horizons(str(tmp_path), 7, [model_dir])] == [str(staging)]


class TestRunScopedStagingNamesFollowTheConfig:
    def test_run_id_erases_the_staging_directory_of_a_custom_final_model_dir(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._purge import _run_purge_cmd

        out = tmp_path / "ck"
        staging = out / "my_model.staging.fg-x"
        staging.mkdir(parents=True)
        (staging / "adapter_model.safetensors").write_text("w")
        config = _write_training_config(tmp_path, out, final_model_dir="my_model")

        args = _build_args(run_id="fg-x", kind="staging", output_dir=str(out), config=str(config))
        with pytest.raises(SystemExit) as ei:
            _run_purge_cmd(args, output_format="json")

        assert ei.value.code == 0
        assert not staging.exists()

    def test_without_the_config_the_custom_staging_directory_is_not_found_and_is_left_alone(
        self, tmp_path: Path
    ) -> None:
        from forgelm.cli.subcommands._purge import _run_purge_cmd

        out = tmp_path / "ck"
        staging = out / "my_model.staging.fg-x"
        staging.mkdir(parents=True)

        args = _build_args(run_id="fg-x", kind="staging", output_dir=str(out))
        with pytest.raises(SystemExit) as ei:
            _run_purge_cmd(args, output_format="json")

        assert ei.value.code == 1
        assert staging.exists()
        failed = next(e for e in _read_audit_events(out / "audit_log.jsonl") if e["event"] == "data.erasure_failed")
        assert failed["error_class"] == "NoMatchingArtefacts"


class TestRunScopedErasureWithAnUnreadableConfig:
    def test_non_utf8_config_falls_back_to_the_default_name_and_closes_the_chain(self, tmp_path: Path, caplog) -> None:
        """The config is read before ``data.erasure_requested`` is logged and a file that cannot be decoded is
        'cannot be loaded', like any other: the purge continues with the default staging name."""
        from forgelm.cli.subcommands._purge import _run_purge_cmd

        out = tmp_path / "ck"
        staging = out / "final_model.staging.fg-x"
        staging.mkdir(parents=True)
        bad_config = tmp_path / "binary.yaml"
        bad_config.write_bytes(b"training:\n  output_dir: \xff\xfe\x00\n")

        args = _build_args(run_id="fg-x", kind="staging", output_dir=str(out), config=str(bad_config))
        with caplog.at_level("WARNING"), pytest.raises(SystemExit) as ei:
            _run_purge_cmd(args, output_format="json")

        assert ei.value.code == 0
        assert not staging.exists()
        assert [e["event"] for e in _read_audit_events(out / "audit_log.jsonl")] == [
            "data.erasure_requested",
            "data.erasure_completed",
        ]
        assert [r for r in caplog.records if "could not be loaded" in r.getMessage()]

    def test_artefacts_kind_does_not_load_the_config(self, tmp_path: Path, caplog) -> None:
        """Only ``--kind staging`` needs ``final_model_dir``; ``--kind artefacts`` must not warn about a config it
        never uses."""
        from forgelm.cli.subcommands._purge import _run_purge_cmd

        out = tmp_path / "ck"
        (out / "compliance").mkdir(parents=True)
        (out / "compliance" / "report_fg-x.json").write_text("{}")
        bad_config = tmp_path / "binary.yaml"
        bad_config.write_bytes(b"\xff\xfe\x00")

        args = _build_args(run_id="fg-x", kind="artefacts", output_dir=str(out), config=str(bad_config))
        with caplog.at_level("WARNING"), pytest.raises(SystemExit) as ei:
            _run_purge_cmd(args, output_format="json")

        assert ei.value.code == 0
        assert not [r for r in caplog.records if "could not be loaded" in r.getMessage()]


class TestRetentionReportFollowsTheConfig:
    @staticmethod
    def _age(path: Path, days: int) -> None:
        old = path.stat().st_mtime - days * 86400
        os.utime(path, (old, old))

    def test_check_policy_reports_staging_next_to_a_custom_final_model_dir(self, tmp_path: Path, capsys) -> None:
        from forgelm.cli.subcommands._purge import _run_purge_cmd

        out = tmp_path / "ck"
        staging = out / "my_model.staging.fg-old"
        staging.mkdir(parents=True)
        self._age(staging, 30)
        config = _write_training_config(
            tmp_path, out, final_model_dir="my_model", extra="retention:\n  staging_ttl_days: 7\n"
        )

        args = _build_args(check_policy=True, config=str(config), output_dir=str(out))
        with pytest.raises(SystemExit) as ei:
            _run_purge_cmd(args, output_format="json")

        assert ei.value.code == 0
        violations = json.loads(capsys.readouterr().out)["violations"]
        assert [(v["artefact_kind"], v["path"]) for v in violations] == [("staging_dir[fg-old]", str(staging))]

    def test_check_policy_with_a_retention_only_file_reports_the_default_staging_name(
        self, tmp_path: Path, capsys
    ) -> None:
        """A retention-only file names no ``training.final_model_dir``, so only ``final_model.staging*`` is known."""
        from forgelm.cli.subcommands._purge import _run_purge_cmd

        out = tmp_path / "ck"
        default_staging = out / "final_model.staging.fg-old"
        custom_staging = out / "my_model.staging.fg-old"
        for staging in (default_staging, custom_staging):
            staging.mkdir(parents=True)
            self._age(staging, 30)
        config = tmp_path / "retention.yaml"
        config.write_text("retention:\n  staging_ttl_days: 7\n", encoding="utf-8")

        args = _build_args(check_policy=True, config=str(config), output_dir=str(out))
        with pytest.raises(SystemExit):
            _run_purge_cmd(args, output_format="json")

        violations = json.loads(capsys.readouterr().out)["violations"]
        assert [v["path"] for v in violations] == [str(default_staging)]

    def test_checkpoints_are_outside_every_retention_horizon(self, tmp_path: Path) -> None:
        """No ``retention:`` field covers ``checkpoint-*``; the docs say so and this pins it."""
        from forgelm.cli.subcommands._purge import _scan_retention_violations
        from forgelm.config import RetentionConfig

        checkpoint = tmp_path / "checkpoint-1"
        staging = tmp_path / "final_model.staging.fg-old"
        for artefact in (checkpoint, staging):
            artefact.mkdir()
            self._age(artefact, 400)
        retention = RetentionConfig(
            audit_log_retention_days=1,
            staging_ttl_days=1,
            ephemeral_artefact_retention_days=1,
            raw_documents_retention_days=1,
        )

        # The aged staging directory beside it proves the scan ran: only the checkpoint is left out.
        assert [v["path"] for v in _scan_retention_violations(retention, str(tmp_path))] == [str(staging)]

    def test_check_policy_follows_pipeline_stage_model_names(self, tmp_path: Path, capsys) -> None:
        from forgelm.cli.subcommands._purge import _run_purge_cmd

        out = tmp_path / "ck"
        staging = out / "stage_one_model.staging.fg-old"
        staging.mkdir(parents=True)
        self._age(staging, 30)
        extra = (
            "retention:\n  staging_ttl_days: 7\n"
            "pipeline:\n"
            f"  output_dir: '{tmp_path / 'pipeline_run'}'\n"
            "  stages:\n"
            "    - name: sft\n"
            "      training:\n"
            "        trainer_type: sft\n"
            f"        output_dir: '{out}'\n"
            "        final_model_dir: stage_one_model\n"
        )
        config = _write_training_config(tmp_path, out, extra=extra)

        args = _build_args(check_policy=True, config=str(config), output_dir=str(out))
        with pytest.raises(SystemExit):
            _run_purge_cmd(args, output_format="json")

        violations = json.loads(capsys.readouterr().out)["violations"]
        assert [v["path"] for v in violations] == [str(staging)]

    def test_retention_loader_returns_the_full_config_only_for_a_full_file(self, tmp_path: Path) -> None:
        from forgelm.cli._config_load import _load_retention_and_config

        full = _write_training_config(tmp_path, tmp_path / "ck", extra="retention:\n  staging_ttl_days: 7\n")
        only = tmp_path / "retention.yaml"
        only.write_text("retention:\n  staging_ttl_days: 9\n", encoding="utf-8")

        full_retention, full_config = _load_retention_and_config(str(full))
        only_retention, only_config = _load_retention_and_config(str(only))

        assert full_retention is not None and full_retention.staging_ttl_days == 7
        assert full_config is not None and full_config.training.output_dir == str(tmp_path / "ck")
        assert only_retention is not None and only_retention.staging_ttl_days == 9
        assert only_config is None
        assert _load_retention_and_config(None) == (None, None)


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
