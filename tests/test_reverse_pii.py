"""Phase 38 — `forgelm reverse-pii` GDPR Article 15 right-of-access.

Tests run torch-free + extra-free.  All scenarios use synthetic JSONL
fixtures so the suite stays deterministic + offline.

Test coverage maps to closure-plan §Faz 38 acceptance:

1. Plaintext residual scan — identifier verbatim found / not found
   in glob expansion.
2. Hash-mask scan — SHA256(salt + identifier) recomputed via the
   same per-output-dir salt as `forgelm purge`; matches the masked
   corpus.
3. Audit-event emission — `data.access_request_query` written;
   identifier hashed (NEVER raw) and salted (Wave 3 absorption
   F-W3-PS-01: salt-free SHA-256 of low-entropy identifiers is
   brute-forcible from a wordlist; the audit hash now reuses the
   per-output-dir salt purge already uses).
4. Glob expansion — multiple files scanned in deterministic order;
   per-file match count surfaced.
5. Failure paths — empty query / unparseable custom regex / empty
   glob expansion → exit code 1; mid-scan I/O failure → exit 2.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import unittest.mock as _mock
from pathlib import Path
from types import SimpleNamespace

import pytest


def _seed_corpus(corpus_path: Path, rows: list[dict]) -> None:
    corpus_path.parent.mkdir(parents=True, exist_ok=True)
    with open(corpus_path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _read_audit_events(audit_log_path: Path) -> list[dict]:
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
    query: str | None = None,
    type: str | None = "literal",
    salt_source: str | None = None,
    files: list[str] | None = None,
    output_dir: str | None = None,
    audit_dir: str | None = None,
) -> SimpleNamespace:
    """Strict argparse-shaped namespace; misspelled attrs raise."""
    return SimpleNamespace(
        query=query,
        type=type,
        salt_source=salt_source,
        files=files,
        output_dir=output_dir,
        audit_dir=audit_dir,
    )


@pytest.fixture(autouse=True)
def _set_operator_env(monkeypatch):
    """Every test runs with a deterministic FORGELM_OPERATOR so AuditLogger
    does not refuse to start on shared CI runners.  Also clears any
    inherited FORGELM_AUDIT_SECRET from a prior test in the same
    invocation so per_dir / env_var modes are predictable."""
    monkeypatch.setenv("FORGELM_OPERATOR", "test-operator@reverse-pii-test")
    monkeypatch.delenv("FORGELM_AUDIT_SECRET", raising=False)


# ---------------------------------------------------------------------------
# §1 Test — Plaintext residual scan: verbatim match found
# ---------------------------------------------------------------------------


class TestPlaintextResidualMatch:
    def test_email_residual_found_with_line_and_snippet(self, tmp_path: Path, capsys) -> None:
        from forgelm.cli.subcommands._reverse_pii import _run_reverse_pii_cmd

        corpus = tmp_path / "train.jsonl"
        _seed_corpus(
            corpus,
            [
                {"id": "row-A", "text": "Hello, my name is Alice"},
                {"id": "row-B", "text": "Contact me at alice@example.com please"},
                {"id": "row-C", "text": "Carol said hi"},
            ],
        )
        args = _build_args(
            query="alice@example.com",
            type="email",
            files=[str(corpus)],
            output_dir=str(tmp_path),
        )
        with pytest.raises(SystemExit) as ei:
            _run_reverse_pii_cmd(args, output_format="json")
        assert ei.value.code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["success"] is True
        assert payload["match_count"] == 1
        assert payload["scan_mode"] == "plaintext"
        assert payload["identifier_type"] == "email"
        # Single match is on row-B (line 2, 1-based).
        match = payload["matches"][0]
        assert match["file"] == os.path.abspath(str(corpus))
        assert match["line"] == 2
        assert "alice@example.com" in match["snippet"]
        # files_scanned reports the per-file match count too.
        assert payload["files_scanned"] == [{"path": os.path.abspath(str(corpus)), "match_count": 1}]

    def test_no_residual_found_returns_empty_match_list(self, tmp_path: Path, capsys) -> None:
        from forgelm.cli.subcommands._reverse_pii import _run_reverse_pii_cmd

        corpus = tmp_path / "train.jsonl"
        _seed_corpus(corpus, [{"id": "row-A", "text": "no PII here"}])
        args = _build_args(
            query="someone-else@example.com",
            type="email",
            files=[str(corpus)],
            output_dir=str(tmp_path),
        )
        with pytest.raises(SystemExit) as ei:
            _run_reverse_pii_cmd(args, output_format="json")
        assert ei.value.code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["match_count"] == 0
        assert payload["matches"] == []
        # files_scanned still records that we walked the file.
        assert len(payload["files_scanned"]) == 1
        assert payload["files_scanned"][0]["match_count"] == 0

    def test_default_type_is_literal_not_regex(self, tmp_path: Path, capsys) -> None:
        """F-W3-02 regression: the default --type must NOT interpret the
        query as a regex.  ``alice@example.com`` (with literal dot) must
        not match ``alice@exampleXcom`` (where ``.`` would be wildcarded
        under the previous default of ``custom``)."""
        from forgelm.cli.subcommands._reverse_pii import _run_reverse_pii_cmd

        corpus = tmp_path / "train.jsonl"
        _seed_corpus(corpus, [{"id": "row-A", "text": "contact alice@exampleXcom"}])
        # No --type → default = "literal".
        args = _build_args(
            query="alice@example.com",
            type=None,
            files=[str(corpus)],
            output_dir=str(tmp_path),
        )
        with pytest.raises(SystemExit) as ei:
            _run_reverse_pii_cmd(args, output_format="json")
        assert ei.value.code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["match_count"] == 0, "literal default must not regex-match wildcards"
        assert payload["identifier_type"] == "literal"


# ---------------------------------------------------------------------------
# §2 Test — Hash-mask scan: SHA256(salt + identifier) found in masked corpus
# ---------------------------------------------------------------------------


class TestHashMaskScan:
    def test_per_dir_hash_matches_masked_row(self, tmp_path: Path, capsys) -> None:
        from forgelm.cli.subcommands._purge import _hash_target_id, _resolve_salt
        from forgelm.cli.subcommands._reverse_pii import _run_reverse_pii_cmd

        # Pre-create the salt file; compute the digest the operator
        # would have written to the masked corpus.
        salt, source = _resolve_salt(str(tmp_path))
        assert source == "per_dir"
        masked_digest = _hash_target_id("alice@example.com", salt)

        corpus = tmp_path / "masked.jsonl"
        _seed_corpus(
            corpus,
            [
                {"id": "row-A", "text": "first record, no PII"},
                {"id": "row-B", "text": f"hashed identifier embedded: {masked_digest}"},
                {"id": "row-C", "text": "control row"},
            ],
        )
        args = _build_args(
            query="alice@example.com",
            type="email",
            salt_source="per_dir",
            files=[str(corpus)],
            output_dir=str(tmp_path),
        )
        with pytest.raises(SystemExit) as ei:
            _run_reverse_pii_cmd(args, output_format="json")
        assert ei.value.code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["scan_mode"] == "hash"
        assert payload["match_count"] == 1
        # The match snippet contains the hash digest, NOT the raw email.
        snippet = payload["matches"][0]["snippet"]
        assert masked_digest in snippet
        assert "alice@example.com" not in snippet

    def test_env_var_salt_source_requires_secret_env(self, tmp_path: Path, capsys, monkeypatch) -> None:
        """``--salt-source env_var`` without ``FORGELM_AUDIT_SECRET``
        must refuse rather than silently scan with the wrong salt."""
        from forgelm.cli.subcommands._reverse_pii import _run_reverse_pii_cmd

        # Explicitly clear (autouse fixture also does this; belt + braces).
        monkeypatch.delenv("FORGELM_AUDIT_SECRET", raising=False)
        corpus = tmp_path / "masked.jsonl"
        _seed_corpus(corpus, [{"id": "row-A", "text": "x"}])
        args = _build_args(
            query="alice@example.com",
            type="email",
            salt_source="env_var",
            files=[str(corpus)],
            output_dir=str(tmp_path),
        )
        with pytest.raises(SystemExit) as ei:
            _run_reverse_pii_cmd(args, output_format="json")
        assert ei.value.code == 1, "env_var without FORGELM_AUDIT_SECRET must surface as EXIT_CONFIG_ERROR"

    def test_implicit_output_dir_with_salt_source_warns_about_correlation_risk(self, tmp_path: Path, caplog) -> None:
        """Wave 3 follow-up: when ``--salt-source`` is set but
        ``--output-dir`` is not, the dispatcher falls back to the corpus
        parent for salt-file resolution.  That silently breaks
        cross-tool correlation with any ``forgelm purge`` invocation
        that supplied an explicit ``--output-dir``.  The fallback
        must surface a WARNING naming the resolved dir so the
        operator can pin it or accept the consequence."""
        import logging

        from forgelm.cli.subcommands._reverse_pii import _run_reverse_pii_cmd

        # Place the corpus in a subdirectory so the fallback
        # ``output_dir`` is a real, distinct path the warning can name.
        corpus_dir = tmp_path / "data"
        corpus_dir.mkdir()
        corpus = corpus_dir / "masked.jsonl"
        _seed_corpus(corpus, [{"id": "row-A", "text": "x"}])
        args = _build_args(
            query="alice@example.com",
            type="email",
            salt_source="per_dir",
            files=[str(corpus)],
            output_dir=None,  # implicit — falls back to corpus_dir
        )
        with caplog.at_level(logging.WARNING, logger="forgelm.cli"):
            with pytest.raises(SystemExit) as ei:
                _run_reverse_pii_cmd(args, output_format="json")
        assert ei.value.code == 0
        warnings = [
            r
            for r in caplog.records
            if r.levelname == "WARNING" and "reverse-pii" in r.message and "cross-tool" in r.message.lower()
        ]
        assert warnings, "implicit --output-dir + --salt-source must surface a cross-tool correlation warning"
        # The salt file landed in the corpus dir per the warning text.
        assert (corpus_dir / ".forgelm_audit_salt").exists()

    def test_plaintext_implicit_output_dir_also_warns_about_salt_file(self, tmp_path: Path, caplog) -> None:
        """F-W3FU-06 (priv) regression: the salt-file side effect is
        present in BOTH plaintext and hash-mask modes (the audit hash
        always uses the per-output-dir salt per F-W3-PS-01).  An
        operator running plaintext-mode reverse-pii with an implicit
        ``--output-dir`` must therefore ALSO get the cross-tool
        correlation warning — the previous absorption scoped the
        warning to hash-mask mode only, leaving plaintext-mode runs
        silently creating ``.forgelm_audit_salt`` in the corpus
        parent dir without warning."""
        import logging

        from forgelm.cli.subcommands._reverse_pii import _run_reverse_pii_cmd

        corpus_dir = tmp_path / "data"
        corpus_dir.mkdir()
        corpus = corpus_dir / "train.jsonl"
        _seed_corpus(corpus, [{"id": "row-A", "text": "x"}])
        args = _build_args(
            query="alice@example.com",
            type="email",
            salt_source=None,  # plaintext mode
            files=[str(corpus)],
            output_dir=None,
        )
        with caplog.at_level(logging.WARNING, logger="forgelm.cli"):
            with pytest.raises(SystemExit):
                _run_reverse_pii_cmd(args, output_format="json")
        warnings = [
            r
            for r in caplog.records
            if r.levelname == "WARNING" and "reverse-pii" in r.message and "cross-tool" in r.message.lower()
        ]
        assert warnings, (
            "plaintext mode with implicit --output-dir must still warn — the "
            "salt file side effect is present regardless of scan mode"
        )

    def test_per_dir_salt_source_honored_when_env_var_set(self, tmp_path: Path, capsys, monkeypatch) -> None:
        """Wave-3-followup duplicate-finding regression: explicit
        ``--salt-source=per_dir`` must be HONORED even when
        ``FORGELM_AUDIT_SECRET`` is exported in the shell environment.

        The previous absorption refused this case (because
        ``_resolve_salt`` XOR'd the env var into the salt and the
        post-resolution mismatch check fired), but the operator's
        explicit ``per_dir`` choice should override an unrelated env
        var.  The audit event records ``salt_source = "per_dir"`` so a
        forensic reviewer sees the choice was respected.
        """
        from forgelm.cli.subcommands._purge import _hash_target_id, _read_persistent_salt
        from forgelm.cli.subcommands._reverse_pii import _run_reverse_pii_cmd

        monkeypatch.setenv("FORGELM_AUDIT_SECRET", "leftover-from-shell")

        # Compute the per-dir-only digest the operator would have
        # written into the masked corpus (no env XOR).
        salt = _read_persistent_salt(str(tmp_path))
        masked_digest = _hash_target_id("alice@example.com", salt)

        corpus = tmp_path / "masked.jsonl"
        _seed_corpus(
            corpus,
            [
                {"id": "row-A", "text": f"hashed identifier embedded: {masked_digest}"},
            ],
        )
        args = _build_args(
            query="alice@example.com",
            type="email",
            salt_source="per_dir",
            files=[str(corpus)],
            output_dir=str(tmp_path),
        )
        with pytest.raises(SystemExit) as ei:
            _run_reverse_pii_cmd(args, output_format="json")
        assert ei.value.code == 0, "explicit --salt-source=per_dir must be honored, not refused"
        payload = json.loads(capsys.readouterr().out)
        assert payload["scan_mode"] == "hash"
        assert payload["salt_source"] == "per_dir"
        assert payload["match_count"] == 1


# ---------------------------------------------------------------------------
# §3 Test — Audit event emitted; identifier hashed (NEVER raw)
# ---------------------------------------------------------------------------


class TestAuditEventDoesNotLeakIdentifier:
    def test_access_request_event_carries_hashed_query_only(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._purge import _hash_target_id, _resolve_salt
        from forgelm.cli.subcommands._reverse_pii import _run_reverse_pii_cmd

        audit_dir = tmp_path / "audit"
        audit_dir.mkdir()
        corpus = tmp_path / "train.jsonl"
        _seed_corpus(corpus, [{"id": "row-A", "text": "alice@example.com is here"}])

        args = _build_args(
            query="alice@example.com",
            type="email",
            files=[str(corpus)],
            output_dir=str(tmp_path),
            audit_dir=str(audit_dir),
        )
        with pytest.raises(SystemExit) as ei:
            _run_reverse_pii_cmd(args, output_format="json")
        assert ei.value.code == 0

        events = _read_audit_events(audit_dir / "audit_log.jsonl")
        access_events = [e for e in events if e["event"] == "data.access_request_query"]
        assert len(access_events) == 1
        evt = access_events[0]
        # F-W3-PS-01 absorption: the hash is salted with the per-output-dir
        # salt purge uses for target_id; an unsalted SHA-256 must NOT be
        # present (otherwise a wordlist attack against the audit log
        # would recover the subject's identifier).
        salt, _ = _resolve_salt(str(tmp_path))
        expected_hash = _hash_target_id("alice@example.com", salt)
        unsalted = hashlib.sha256(b"alice@example.com").hexdigest()
        assert evt["query_hash"] == expected_hash
        assert evt["query_hash"] != unsalted, (
            "audit query_hash must be salted; otherwise a wordlist attack "
            "recovers the subject's identifier from the chain"
        )
        # Critical: raw identifier appears NOWHERE in the event payload.
        as_str = json.dumps(evt)
        assert "alice@example.com" not in as_str, f"audit event leaked raw identifier:\n{as_str}"
        assert evt["identifier_type"] == "email"
        assert evt["scan_mode"] == "plaintext"
        assert evt["match_count"] == 1
        # F-W3-PS-07 absorption: salt_source recorded in every event.
        assert evt["salt_source"] in {"plaintext", "per_dir", "env_var"}

    def test_purge_target_id_matches_reverse_pii_query_hash_on_same_output_dir(self, tmp_path: Path) -> None:
        """F-W3-PS-09 absorption: the audit chain must let a compliance
        reviewer correlate Article 17 (purge) and Article 15 (reverse-pii)
        events for the same subject in the same ``output_dir``.  The
        digests are equal iff both subcommands use the same per-output-dir
        salt — which they now do."""
        from forgelm.cli.subcommands._purge import _hash_target_id, _resolve_salt
        from forgelm.cli.subcommands._reverse_pii import _hash_for_audit

        salt, _ = _resolve_salt(str(tmp_path))
        purge_target_id = _hash_target_id("alice@example.com", salt)
        reverse_pii_query_hash = _hash_for_audit("alice@example.com", salt)
        assert purge_target_id == reverse_pii_query_hash, (
            "cross-tool audit correlation requires both subcommands to salt with the same per-output-dir salt"
        )


# ---------------------------------------------------------------------------
# §4 Test — Glob expansion: multiple files, deterministic ordering
# ---------------------------------------------------------------------------


class TestGlobExpansion:
    def test_glob_pattern_expands_to_multiple_files_with_per_file_counts(self, tmp_path: Path, capsys) -> None:
        from forgelm.cli.subcommands._reverse_pii import _run_reverse_pii_cmd

        # Plant three corpora; identifier appears in two of them.
        for name, rows in [
            ("a.jsonl", [{"id": "1", "text": "alice@example.com here"}]),
            ("b.jsonl", [{"id": "1", "text": "no PII"}]),
            ("c.jsonl", [{"id": "1", "text": "alice@example.com again"}]),
        ]:
            _seed_corpus(tmp_path / name, rows)

        args = _build_args(
            query="alice@example.com",
            type="email",
            files=[str(tmp_path / "*.jsonl")],
            output_dir=str(tmp_path),
        )
        with pytest.raises(SystemExit) as ei:
            _run_reverse_pii_cmd(args, output_format="json")
        assert ei.value.code == 0
        payload = json.loads(capsys.readouterr().out)
        # 3 files scanned; 2 matches total (a + c each contribute 1).
        assert len(payload["files_scanned"]) == 3
        assert payload["match_count"] == 2
        per_file = {fs["path"]: fs["match_count"] for fs in payload["files_scanned"]}
        assert per_file[os.path.abspath(str(tmp_path / "a.jsonl"))] == 1
        assert per_file[os.path.abspath(str(tmp_path / "b.jsonl"))] == 0
        assert per_file[os.path.abspath(str(tmp_path / "c.jsonl"))] == 1

    def test_recursive_glob_descends_into_subdirs(self, tmp_path: Path, capsys) -> None:
        from forgelm.cli.subcommands._reverse_pii import _run_reverse_pii_cmd

        nested = tmp_path / "year-2026" / "q2"
        nested.mkdir(parents=True)
        _seed_corpus(nested / "train.jsonl", [{"id": "1", "text": "ali@example.com here"}])

        args = _build_args(
            query="ali@example.com",
            type="email",
            files=[str(tmp_path / "**" / "train.jsonl")],
            output_dir=str(tmp_path),
        )
        with pytest.raises(SystemExit) as ei:
            _run_reverse_pii_cmd(args, output_format="json")
        assert ei.value.code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["match_count"] == 1

    def test_overlapping_globs_deduped_to_one_scan(self, tmp_path: Path, capsys) -> None:
        """F-W3T-12 regression: two globs that match the same file must
        not double-count matches."""
        from forgelm.cli.subcommands._reverse_pii import _run_reverse_pii_cmd

        _seed_corpus(tmp_path / "train.jsonl", [{"id": "1", "text": "alice@example.com"}])
        args = _build_args(
            query="alice@example.com",
            type="email",
            files=[str(tmp_path / "*.jsonl"), str(tmp_path / "train*.jsonl")],
            output_dir=str(tmp_path),
        )
        with pytest.raises(SystemExit) as ei:
            _run_reverse_pii_cmd(args, output_format="json")
        assert ei.value.code == 0
        payload = json.loads(capsys.readouterr().out)
        assert len(payload["files_scanned"]) == 1
        assert payload["match_count"] == 1


# ---------------------------------------------------------------------------
# §5 Test — Failure paths
# ---------------------------------------------------------------------------


class TestFailurePaths:
    def test_empty_query_exits_config_error(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._reverse_pii import _run_reverse_pii_cmd

        corpus = tmp_path / "train.jsonl"
        _seed_corpus(corpus, [{"id": "1", "text": "x"}])
        args = _build_args(query="   ", files=[str(corpus)], output_dir=str(tmp_path))
        with pytest.raises(SystemExit) as ei:
            _run_reverse_pii_cmd(args, output_format="json")
        assert ei.value.code == 1

    def test_unparseable_custom_regex_exits_config_error_without_salt_side_effect(self, tmp_path: Path) -> None:
        """F-W3-08 regression: a config error (unparseable regex) must
        not leave a salt file behind on disk.  Validation runs before
        any filesystem side effect."""
        from forgelm.cli.subcommands._reverse_pii import _run_reverse_pii_cmd

        corpus = tmp_path / "train.jsonl"
        _seed_corpus(corpus, [{"id": "1", "text": "x"}])
        # Unbalanced bracket; re.compile will raise.
        args = _build_args(
            query="[invalid",
            type="custom",
            salt_source="per_dir",  # would trigger salt creation if validation order was wrong
            files=[str(corpus)],
            output_dir=str(tmp_path),
        )
        with pytest.raises(SystemExit) as ei:
            _run_reverse_pii_cmd(args, output_format="json")
        assert ei.value.code == 1
        assert not (tmp_path / ".forgelm_audit_salt").exists(), "config error must not create a salt file on disk"

    def test_invalid_custom_regex_error_does_not_echo_raw_query(self, capsys) -> None:
        """F-P5-OPUS-17: a malformed --type custom regex must NOT echo the raw
        query into the error envelope (in --output json it lands in a
        redirectable durable record). The engine error is still surfaced so the
        operator can fix the pattern, but the subject's identifier is not."""
        import json as _json

        from forgelm.cli.subcommands._reverse_pii import _build_search_pattern

        raw_query = "alice(@secret-domain.example"  # unbalanced paren → re.error
        with pytest.raises(SystemExit) as ei:
            _build_search_pattern(raw_query, "custom", "json")
        assert ei.value.code == 1

        payload = _json.loads(capsys.readouterr().out)
        assert payload["success"] is False
        # The raw identifier must not appear anywhere in the error string.
        assert raw_query not in payload["error"]
        assert "secret-domain" not in payload["error"]
        # The regex-engine diagnostic is still present so the operator can fix it.
        assert "not a valid regular expression" in payload["error"]

    def test_invalid_custom_regex_error_does_not_echo_re_error_text(self, capsys) -> None:
        """F-P5-OPUS-17 (PR#63 review): re.error.msg / str(exc) reproduce
        pattern-derived fragments — an invalid group name embeds the subject's
        identifier in the engine diagnostic itself. The error envelope must not
        carry that text, only a generic message + numeric position."""
        import json as _json

        from forgelm.cli.subcommands._reverse_pii import _build_search_pattern

        # An invalid group name surfaces the identifier inside re.error.msg:
        #   "bad character in group name 'alice@example.com' at position 4"
        secret = "alice@example.com"
        raw_query = f"(?P<{secret}>x)"
        with pytest.raises(SystemExit) as ei:
            _build_search_pattern(raw_query, "custom", "json")
        assert ei.value.code == 1

        payload = _json.loads(capsys.readouterr().out)
        assert payload["success"] is False
        # Neither the raw query nor the leaked group-name fragment may appear.
        assert raw_query not in payload["error"]
        assert secret not in payload["error"]
        assert "group name" not in payload["error"]
        # Operator still gets an actionable, identifier-free message.
        assert "not a valid regular expression" in payload["error"]

    def test_unknown_identifier_type_rejected(self, tmp_path: Path) -> None:
        # The argparse layer also catches this, but the dispatcher
        # validates as belt-and-suspenders for direct library callers.
        from forgelm.cli.subcommands._reverse_pii import _run_reverse_pii_cmd

        corpus = tmp_path / "train.jsonl"
        _seed_corpus(corpus, [{"id": "1", "text": "x"}])
        args = _build_args(query="x", type="bogus_type", files=[str(corpus)], output_dir=str(tmp_path))
        with pytest.raises(SystemExit) as ei:
            _run_reverse_pii_cmd(args, output_format="json")
        assert ei.value.code == 1

    def test_empty_glob_exits_config_error(self, tmp_path: Path, capsys) -> None:
        from forgelm.cli.subcommands._reverse_pii import _run_reverse_pii_cmd

        args = _build_args(
            query="alice@example.com",
            type="email",
            files=[str(tmp_path / "nonexistent" / "*.jsonl")],
            output_dir=str(tmp_path),
        )
        with pytest.raises(SystemExit) as ei:
            _run_reverse_pii_cmd(args, output_format="json")
        assert ei.value.code == 1

    def test_directory_argument_diagnoses_glob_form(self, tmp_path: Path, capsys) -> None:
        """F-W3-12 regression: passing a directory must produce a
        targeted diagnostic naming the glob form, not a generic empty-glob
        error.  Closes the UX paper-cut where the operator typed `data/`
        intending `data/*.jsonl`."""
        from forgelm.cli.subcommands._reverse_pii import _run_reverse_pii_cmd

        # Seed a file inside the directory so glob expansion finds the
        # directory but not its contents (positional is the directory
        # itself, not a glob).
        _seed_corpus(tmp_path / "child.jsonl", [{"id": "1", "text": "x"}])
        args = _build_args(
            query="x",
            type="email",
            files=[str(tmp_path)],
            output_dir=str(tmp_path),
        )
        with pytest.raises(SystemExit) as ei:
            _run_reverse_pii_cmd(args, output_format="json")
        assert ei.value.code == 1
        payload = json.loads(capsys.readouterr().out)
        assert "directory" in payload["error"].lower()
        assert ".jsonl" in payload["error"]

    def test_no_files_argument_exits_config_error(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._reverse_pii import _run_reverse_pii_cmd

        args = _build_args(query="x", type="email", files=[], output_dir=str(tmp_path))
        with pytest.raises(SystemExit) as ei:
            _run_reverse_pii_cmd(args, output_format="json")
        assert ei.value.code == 1

    def test_mid_scan_io_failure_writes_failed_audit_event_without_leaking_identifier(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """F-W3T-01 regression: the failure-path audit event must carry
        the same no-leak invariant as the success path."""
        from forgelm.cli.subcommands import _reverse_pii

        corpus = tmp_path / "train.jsonl"
        _seed_corpus(corpus, [{"id": "1", "text": "alice@example.com"}])
        audit_dir = tmp_path / "audit"
        audit_dir.mkdir()

        # Spy on _scan_file → raise OSError on the second call (the
        # second file gets opened and the read fails mid-line).
        original_scan = _reverse_pii._scan_file
        call_count = [0]

        def _flaky_scan(path: str, pattern):
            call_count[0] += 1
            if call_count[0] >= 2:
                raise OSError("simulated mid-scan I/O failure")
            return original_scan(path, pattern)

        monkeypatch.setattr(_reverse_pii, "_scan_file", _flaky_scan)

        # Two files so the second invocation exercises the failure path.
        corpus2 = tmp_path / "train2.jsonl"
        _seed_corpus(corpus2, [{"id": "2", "text": "bob@example.com"}])

        args = _build_args(
            query="alice@example.com",
            type="email",
            files=[str(corpus), str(corpus2)],
            output_dir=str(tmp_path),
            audit_dir=str(audit_dir),
        )
        with pytest.raises(SystemExit) as ei:
            _reverse_pii._run_reverse_pii_cmd(args, output_format="json")
        assert ei.value.code == 2

        events = _read_audit_events(audit_dir / "audit_log.jsonl")
        access_events = [e for e in events if e["event"] == "data.access_request_query"]
        assert len(access_events) == 1
        evt = access_events[0]
        assert evt["error_class"] == "OSError"
        assert "simulated mid-scan I/O failure" in evt["error_message"]
        # F-W3FU-T-04 / F-W3FU-04: failure-path event must carry the same
        # no-leak invariant AND the same positive-shape (salted hash)
        # contract as the success path.  Substring-only check is too weak
        # — a future refactor that base64-encoded the query into the event
        # under a different field name would slip past it.
        from forgelm.cli.subcommands._purge import _resolve_salt
        from forgelm.cli.subcommands._reverse_pii import _hash_for_audit

        salt, _ = _resolve_salt(str(tmp_path))
        expected_hash = _hash_for_audit("alice@example.com", salt)
        assert evt["query_hash"] == expected_hash, (
            "failure-path event must record the salted hash of the query (positive shape)"
        )
        as_str = json.dumps(evt)
        assert "alice@example.com" not in as_str, f"failure-path audit event leaked raw identifier:\n{as_str}"
        # Defence-in-depth: no encoded form of the identifier in the event.
        import base64

        raw = b"alice@example.com"
        for encoded in (raw.hex(), raw.hex().upper(), base64.b64encode(raw).decode()):
            assert encoded not in as_str, f"event leaked encoded identifier ({encoded!r})"

    def test_malformed_utf8_corpus_exits_runtime_error_with_audit_event_and_no_leak(self, tmp_path: Path) -> None:
        """F-W3-04 + F-W3FU-T-03 regression: a UnicodeDecodeError mid-scan
        must surface as ``EXIT_TRAINING_ERROR=2`` with a failure-flavoured
        audit event AND that event must carry the same no-leak invariant
        as the OSError sibling test (a future change that wrapped
        ``str(exc)`` with corpus byte context could otherwise leak a
        high-entropy identifier into the chain)."""
        from forgelm.cli.subcommands._purge import _resolve_salt
        from forgelm.cli.subcommands._reverse_pii import _hash_for_audit, _run_reverse_pii_cmd

        corpus = tmp_path / "bad.jsonl"
        # Valid first line, then a multi-byte rune fragment.
        corpus.write_bytes(b'{"id":1,"text":"valid"}\n\xff\xfe garbage\n')
        audit_dir = tmp_path / "audit"
        audit_dir.mkdir()

        args = _build_args(
            query="alice@example.com",  # high-entropy identifier; pinned by no-leak assertion below
            type="email",
            files=[str(corpus)],
            output_dir=str(tmp_path),
            audit_dir=str(audit_dir),
        )
        with pytest.raises(SystemExit) as ei:
            _run_reverse_pii_cmd(args, output_format="json")
        assert ei.value.code == 2

        events = _read_audit_events(audit_dir / "audit_log.jsonl")
        access_events = [e for e in events if e["event"] == "data.access_request_query"]
        assert len(access_events) == 1
        evt = access_events[0]
        # The fixture plants ``\xff\xfe`` after a newline, which produces
        # ``UnicodeDecodeError`` deterministically when Python's text-mode
        # iterator decodes the second line.  Pin the exact class so a
        # future refactor that converted it to a generic OSError before
        # logging would surface here.
        assert evt["error_class"] == "UnicodeDecodeError"
        # F-W3FU-T-03: same no-leak invariant as the OSError leg.
        as_str = json.dumps(evt)
        assert "alice@example.com" not in as_str, f"UnicodeDecodeError audit event leaked raw identifier:\n{as_str}"
        # Positive-shape: digest is the salted hash purge would compute.
        salt, _ = _resolve_salt(str(tmp_path))
        expected_hash = _hash_for_audit("alice@example.com", salt)
        assert evt["query_hash"] == expected_hash

    def test_explicit_audit_dir_unwritable_fails_closed(self, tmp_path: Path, monkeypatch) -> None:
        """F-W3-01 / F-W3-PS-02 regression: when --audit-dir is explicit
        and AuditLogger init crashes with a non-ConfigError, the run
        must refuse with EXIT_TRAINING_ERROR rather than silently
        proceeding without an Article 15 forensic record."""
        from forgelm.cli.subcommands._reverse_pii import _run_reverse_pii_cmd
        from forgelm.compliance import AuditLogger

        corpus = tmp_path / "train.jsonl"
        _seed_corpus(corpus, [{"id": "1", "text": "x"}])

        def _crashing_init(self, *_args, **_kw):
            raise OSError("simulated audit-init failure (read-only volume)")

        monkeypatch.setattr(AuditLogger, "__init__", _crashing_init)
        args = _build_args(
            query="x",
            type="email",
            files=[str(corpus)],
            output_dir=str(tmp_path),
            audit_dir=str(tmp_path / "audit"),
        )
        with pytest.raises(SystemExit) as ei:
            _run_reverse_pii_cmd(args, output_format="json")
        assert ei.value.code == 2, (
            "explicit --audit-dir + audit init OSError must fail closed; "
            "silently dropping the chain entry breaches the Article 15 contract"
        )


# ---------------------------------------------------------------------------
# §6 Test — Snippet truncation defends unbounded log spam AND preserves match
# ---------------------------------------------------------------------------


class TestSnippetTruncation:
    def test_long_line_centred_on_match_preserves_identifier(self, tmp_path: Path, capsys) -> None:
        """F-W3-03 regression: snippet truncation must centre on the
        match span so the operator can verify the hit.  The previous
        head+tail strategy dropped the matched span on long lines."""
        from forgelm.cli.subcommands._reverse_pii import _SNIPPET_MAX_CHARS, _run_reverse_pii_cmd

        # Long line with the match buried in the middle.
        prefix = "x" * 200
        suffix = "y" * 200
        corpus = tmp_path / "train.jsonl"
        with open(corpus, "w", encoding="utf-8") as fh:
            fh.write(f"{prefix} alice@example.com {suffix}\n")

        args = _build_args(
            query="alice@example.com",
            type="email",
            files=[str(corpus)],
            output_dir=str(tmp_path),
        )
        with pytest.raises(SystemExit) as ei:
            _run_reverse_pii_cmd(args, output_format="json")
        assert ei.value.code == 0
        payload = json.loads(capsys.readouterr().out)
        snippet = payload["matches"][0]["snippet"]
        assert len(snippet) <= _SNIPPET_MAX_CHARS
        # Ellipsis marks where context was elided.
        assert "…" in snippet
        # Critical: the identifier itself must survive truncation
        # (otherwise the operator cannot verify the hit).
        assert "alice@example.com" in snippet, (
            "centred truncation must preserve the matched span — the whole point is operator verification"
        )

    def test_match_span_wider_than_budget_still_bounded(self) -> None:
        """F-W3FU-02 / F-W3FU-T-02 regression: when a ``--type custom``
        greedy regex matches a span longer than ``_SNIPPET_MAX_CHARS``,
        the previous centring math floored ``ctx`` at 0 and returned
        the whole match span, breaching the documented cap.  The
        degenerate case must still respect the cap."""
        from forgelm.cli.subcommands._reverse_pii import _SNIPPET_MAX_CHARS, _truncate_snippet

        line = "before " + ("A" * 250) + " after"
        # The whole "AAAA..." run is the match — wider than the budget.
        match_start = line.index("A")
        match_end = match_start + 250
        out = _truncate_snippet(line, (match_start, match_end))
        # Allow up to 2 extra chars for head + tail "…" markers.
        assert len(out) <= _SNIPPET_MAX_CHARS + 2, (
            f"degenerate match-len > budget case must still respect the cap; got {len(out)}"
        )
        # Both ends must show ellipses since context was elided on both sides.
        assert out.startswith("…")
        assert out.endswith("…")

    def test_truncation_respects_multibyte_utf8(self, tmp_path: Path, capsys) -> None:
        """F-W3T-05 regression: code-point slicing must not produce
        broken UTF-8 sequences when the line mixes CJK + emoji."""
        from forgelm.cli.subcommands._reverse_pii import _SNIPPET_MAX_CHARS, _truncate_snippet

        # Mix CJK + emoji; bytes per char vary 1/3/4.
        line = ("前置テキスト" * 30) + " alice@example.com " + ("🚀後続" * 30)
        # Locate the match span by hand for the unit test.
        start = line.index("alice@example.com")
        end = start + len("alice@example.com")
        snippet = _truncate_snippet(line, (start, end))
        assert len(snippet) <= _SNIPPET_MAX_CHARS + 2  # head + tail "…"
        # Round-trip through utf-8 must produce the same string (no
        # broken surrogates from a slice landing mid-rune).
        round_tripped = snippet.encode("utf-8").decode("utf-8")
        assert round_tripped == snippet
        # The matched span survives the truncation.
        assert "alice@example.com" in snippet


# ---------------------------------------------------------------------------
# §6b Test — POSIX SIGALRM ReDoS guard (F-W3FU-T-01)
# ---------------------------------------------------------------------------


class TestReDoSGuard:
    """Wave-3-followup: pin the SIGALRM-based ReDoS guard contracts.

    Three invariants:
    1. A pathological ``--type custom`` regex terminates within the
       budget and surfaces as ``EXIT_TRAINING_ERROR=2`` with a
       failure-flavoured audit event (no hang).
    2. The wrapper restores any outer alarm budget after returning.
    3. On non-main threads (where ``signal.signal`` raises) the guard
       is a no-op rather than a crash; the scan still runs.
    """

    @pytest.mark.skipif(os.name != "posix", reason="SIGALRM is POSIX-only")
    def test_pathological_custom_regex_terminates_within_budget(self, tmp_path: Path, monkeypatch) -> None:
        from forgelm.cli.subcommands import _reverse_pii

        # Cut the budget hard so the test is fast; the contract is
        # "timeout fires", not "exact 30s".
        monkeypatch.setattr(_reverse_pii, "_CUSTOM_REGEX_TIMEOUT_S", 1)
        # Plant a corpus line that triggers catastrophic backtracking
        # for ``(a+)+$`` against a long mismatching tail.
        corpus = tmp_path / "evil.jsonl"
        corpus.write_text("a" * 40 + "X" + "\n", encoding="utf-8")
        audit_dir = tmp_path / "audit"
        audit_dir.mkdir()

        args = _build_args(
            query=r"(a+)+$",
            type="custom",
            files=[str(corpus)],
            output_dir=str(tmp_path),
            audit_dir=str(audit_dir),
        )
        import time

        started = time.monotonic()
        with pytest.raises(SystemExit) as ei:
            _reverse_pii._run_reverse_pii_cmd(args, output_format="json")
        elapsed = time.monotonic() - started
        # The 1s budget plus dispatcher overhead must not stretch past 10s.
        assert elapsed < 10, f"ReDoS guard did not fire fast enough; elapsed={elapsed:.2f}s"
        assert ei.value.code == 2

        events = _read_audit_events(audit_dir / "audit_log.jsonl")
        access_events = [e for e in events if e["event"] == "data.access_request_query"]
        assert len(access_events) == 1
        evt = access_events[0]
        assert evt["error_class"] == "OSError"
        assert "ReDoS" in evt["error_message"] or "exceeded" in evt["error_message"]

    @pytest.mark.skipif(os.name != "posix", reason="SIGALRM is POSIX-only")
    def test_alarm_wrapper_restores_outer_alarm_budget(self, tmp_path: Path) -> None:
        """F-W3FU-03: a previously-scheduled outer SIGALRM budget must
        survive the per-file wrapper.  The wrapper's old shape called
        ``signal.alarm(0)`` in ``finally``, permanently cancelling the
        outer alarm; the fix captures and re-arms it.

        Wave-3-followup tightening: also pin the budget-decrement
        contract.  A naive re-arm with ``previous_alarm_remaining``
        (without subtracting the elapsed wall-clock) silently EXTENDS
        the outer caller's deadline by however long ``_scan_file``
        ran.  We measure ``time.monotonic()`` either side of the
        wrapper, derive an upper bound from
        ``previous_budget - ceil(elapsed)`` (clamped to ≥1 — the
        production code's clamp), and assert the re-armed alarm is
        within that bound.  A regression that drops the elapsed
        subtraction would push ``remaining`` above the bound.
        """
        import math
        import re as _re
        import signal as _signal
        import time

        from forgelm.cli.subcommands._reverse_pii import _scan_file_with_alarm

        corpus = tmp_path / "x.jsonl"
        corpus.write_text('{"id":1,"text":"x"}\n', encoding="utf-8")
        pattern = _re.compile("x")

        previous_budget = 60
        previous_handler = _signal.signal(_signal.SIGALRM, lambda *_: None)
        try:
            _signal.alarm(previous_budget)  # outer budget
            started = time.monotonic()
            _scan_file_with_alarm(str(corpus), pattern)
            elapsed = time.monotonic() - started
            remaining = _signal.alarm(0)

            # The wrapper's contract: re-arm with
            # ``max(int(ceil(previous - elapsed)), 1)``.  ``remaining``
            # must be > 0 (outer budget preserved) AND not exceed the
            # production clamp (otherwise the outer caller's deadline
            # was extended by however long the inner scan ran).
            expected_max = max(int(math.ceil(previous_budget - elapsed)), 1)
            assert remaining > 0, (
                "outer alarm budget must survive the wrapper — F-W3FU-03 "
                "regression: signal.alarm(0) in finally was cancelling the outer alarm"
            )
            assert remaining <= expected_max, (
                f"alarm wrapper extended the outer budget — remaining={remaining}s "
                f"but expected_max={expected_max}s after {elapsed:.3f}s of inner scan; "
                "Wave-3-followup tightening: the re-arm must subtract the wall-clock spent inside the wrapper"
            )
        finally:
            _signal.alarm(0)
            _signal.signal(_signal.SIGALRM, previous_handler)

    @pytest.mark.skipif(os.name != "posix", reason="SIGALRM is POSIX-only")
    def test_redos_guard_no_op_on_worker_thread(self, tmp_path: Path) -> None:
        """F-W3FU-04: ``signal.signal`` raises ``ValueError`` from a
        non-main thread; the guard must skip gracefully rather than
        crash the whole scan."""
        import re as _re
        import threading as _threading

        from forgelm.cli.subcommands._reverse_pii import _scan_files_with_redos_guard

        corpus = tmp_path / "x.jsonl"
        corpus.write_text('{"id":1,"text":"abc"}\n', encoding="utf-8")
        pattern = _re.compile("abc")

        results: list = []
        errors: list = []

        def _worker() -> None:
            try:
                results.append(_scan_files_with_redos_guard([str(corpus)], pattern, identifier_type="custom"))
            except Exception as exc:  # pragma: no cover — fails the test below
                errors.append(exc)

        thread = _threading.Thread(target=_worker)
        thread.start()
        thread.join()
        assert not errors, f"worker thread crashed instead of skipping the alarm guard: {errors}"
        assert len(results) == 1
        # files_scanned is the second tuple element; not checked in this test
        # because the contract being pinned is "the worker thread did not
        # crash on signal.signal", not the per-file accounting shape.
        matches, _ = results[0]
        assert len(matches) == 1


# ---------------------------------------------------------------------------
# §7 Test — Facade re-exports + parser wiring + dispatch
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Finding 3 — JSON-mode output must surface the same raw-PII caution the text
# branch prints (parity), to stderr so the stdout JSON stream stays clean.
# ---------------------------------------------------------------------------


class TestJsonModeRawPiiWarning:
    def test_json_mode_with_matches_warns_about_raw_pii_on_stderr(self, tmp_path: Path, caplog) -> None:
        import logging

        from forgelm.cli.subcommands._reverse_pii import _run_reverse_pii_cmd

        corpus = tmp_path / "train.jsonl"
        _seed_corpus(corpus, [{"id": "row-B", "text": "Contact me at alice@example.com now"}])
        args = _build_args(
            query="alice@example.com",
            type="email",
            files=[str(corpus)],
            output_dir=str(tmp_path),
        )
        with caplog.at_level(logging.WARNING, logger="forgelm.cli"):
            with pytest.raises(SystemExit) as ei:
                _run_reverse_pii_cmd(args, output_format="json")
        assert ei.value.code == 0
        warnings = [
            r
            for r in caplog.records
            if r.levelname == "WARNING" and "reverse-pii" in r.message and "pii" in r.message.lower()
        ]
        assert warnings, "JSON mode with matches must warn about raw PII in snippets (parity with text mode)"

    def test_json_mode_zero_matches_does_not_warn_about_raw_pii(self, tmp_path: Path, caplog) -> None:
        import logging

        from forgelm.cli.subcommands._reverse_pii import _run_reverse_pii_cmd

        corpus = tmp_path / "train.jsonl"
        _seed_corpus(corpus, [{"id": "row-A", "text": "nothing sensitive here"}])
        args = _build_args(
            query="absent@example.com",
            type="email",
            files=[str(corpus)],
            output_dir=str(tmp_path),
        )
        with caplog.at_level(logging.WARNING, logger="forgelm.cli"):
            with pytest.raises(SystemExit) as ei:
                _run_reverse_pii_cmd(args, output_format="json")
        assert ei.value.code == 0
        pii_warnings = [r for r in caplog.records if r.levelname == "WARNING" and "raw corpus" in r.message.lower()]
        assert not pii_warnings, "zero matches must not emit the raw-PII caution"


# ---------------------------------------------------------------------------
# Finding 6 — the failure-path audit ``error_message`` must be masked (secret
# + PII) before it enters the append-only chain, matching purge's sibling
# field, not merely length-bounded.
# ---------------------------------------------------------------------------


class TestAuditErrorMessageMasked:
    def test_mid_scan_failure_error_message_masks_pii(self, tmp_path: Path, monkeypatch) -> None:
        from forgelm.cli.subcommands import _reverse_pii

        corpus = tmp_path / "train.jsonl"
        _seed_corpus(corpus, [{"id": "1", "text": "x"}])
        audit_dir = tmp_path / "audit"
        audit_dir.mkdir()

        leaked = "victim@example.com"

        def _boom(path: str, pattern):
            # A downstream read error whose message quotes corpus content.
            raise OSError(f"decode failed near record for {leaked} in corpus")

        monkeypatch.setattr(_reverse_pii, "_scan_file", _boom)

        args = _build_args(
            query="alice@example.com",
            type="email",
            files=[str(corpus)],
            output_dir=str(tmp_path),
            audit_dir=str(audit_dir),
        )
        with pytest.raises(SystemExit) as ei:
            _reverse_pii._run_reverse_pii_cmd(args, output_format="json")
        assert ei.value.code == 2

        events = _read_audit_events(audit_dir / "audit_log.jsonl")
        evt = next(e for e in events if e["event"] == "data.access_request_query")
        assert evt["error_class"] == "OSError"
        # PII echoed by the exception must be masked before the chain, exactly
        # like purge's _sanitise_audit_error_message.
        assert leaked not in evt["error_message"], "PII in exception message must be masked before entering the chain"
        assert leaked not in (audit_dir / "audit_log.jsonl").read_text()


class TestReversePiiFacade:
    def test_facade_re_exports_dispatcher_and_helpers(self) -> None:
        """F-W3-10 + F-W3FU-T-11 regression: facade must re-export every
        name in ``_reverse_pii.__all__`` AND each re-export must be a
        live callable.  The previous ``hasattr`` check would have passed
        even if a re-export was set to ``None`` — strengthen to pin the
        functional contract, not just the name presence."""
        from forgelm import cli as _cli_facade
        from forgelm.cli.subcommands import _reverse_pii as _mod

        for name in _mod.__all__:
            attr = getattr(_cli_facade, name, None)
            assert callable(attr), f"forgelm.cli.{name} must be a callable re-export, got {attr!r}"

    def test_parser_registers_reverse_pii_subcommand(self) -> None:
        """`forgelm reverse-pii --help` must succeed without --config."""
        from forgelm.cli._parser import parse_args

        # parse_args reads sys.argv; mock it.
        with _mock.patch.object(sys, "argv", ["forgelm", "reverse-pii", "--help"]):
            with pytest.raises(SystemExit) as ei:
                parse_args()
            # argparse exits 0 on --help.
            assert ei.value.code == 0

    def test_parser_accepts_minimum_arguments(self, tmp_path: Path) -> None:
        from forgelm.cli._parser import parse_args

        corpus = tmp_path / "train.jsonl"
        corpus.write_text("{}\n")
        with _mock.patch.object(
            sys,
            "argv",
            ["forgelm", "reverse-pii", "--query", "alice@example.com", str(corpus)],
        ):
            args = parse_args()
        assert args.command == "reverse-pii"
        assert args.query == "alice@example.com"
        assert args.files == [str(corpus)]
        # Defaults round-trip — Wave 3 absorption: default --type is now
        # ``literal`` (was ``custom``); F-W3-02 fix.
        assert args.type == "literal"
        assert args.salt_source is None

    def test_dispatch_table_registers_reverse_pii(self) -> None:
        """Dispatch routing only — patching the function under test is
        intentional; the contract is "command name routes to the
        registered handler", not "the handler does the right thing"
        (which is covered by the rest of this file)."""
        from forgelm.cli._dispatch import _dispatch_subcommand

        # Exercise the dispatch via a monkeypatch on the facade so we
        # don't need to actually run the scanner.
        called = {}

        def _fake_dispatch(args, output_format):
            called["command"] = "reverse-pii"
            called["query"] = getattr(args, "query", None)
            sys.exit(0)

        from forgelm import cli as _cli_facade

        original = _cli_facade._run_reverse_pii_cmd
        _cli_facade._run_reverse_pii_cmd = _fake_dispatch
        try:
            args = _build_args(query="x", type="email", files=["dummy"])
            args.command = "reverse-pii"
            args.output_format = "json"
            with pytest.raises(SystemExit) as ei:
                _dispatch_subcommand("reverse-pii", args)
            assert ei.value.code == 0
            assert called == {"command": "reverse-pii", "query": "x"}
        finally:
            _cli_facade._run_reverse_pii_cmd = original
