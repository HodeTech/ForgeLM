"""Phase 36 — `forgelm verify-annex-iv` + `safety-eval` + `verify-gguf`.

Tests run torch-free for the verification subcommands; safety-eval is
exercised at the dispatcher / argument-parsing layer (the underlying
generation path requires torch + a real model and is covered by the
existing safety_evaluation tests, which we do not duplicate here).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def _build_args(**kwargs) -> SimpleNamespace:
    return SimpleNamespace(**kwargs)


# ---------------------------------------------------------------------------
# verify-annex-iv
# ---------------------------------------------------------------------------


def _full_annex_iv_artifact() -> dict:
    """Build a minimal valid Annex IV artifact."""
    return {
        "system_identification": {
            "name": "ForgeLM-test",
            "version": "0.5.5",
            "provider": "Acme",
            # Identity-critical §1 sub-fields the verifier now requires
            # to be non-empty (F-P4-OPUS-17).
            "provider_name": "Acme Inc.",
            "system_name": "ForgeLM-test",
            "intended_purpose": "Customer-support fine-tuning research baseline",
        },
        "intended_purpose": "Customer-support fine-tuning research baseline",
        "system_components": ["transformers>=4.40", "trl>=0.18"],
        "computational_resources": {"gpu": "A100 80GB", "training_hours": 4.5},
        "data_governance": {"sources": ["internal-tickets-2024.jsonl"], "validation": "stratified holdout"},
        "technical_documentation": {"design_doc": "designs/customer-support.md"},
        "monitoring_and_logging": {"audit_log": "audit_log.jsonl", "post_market_review": "quarterly"},
        "performance_metrics": {"eval_loss": 1.4, "safety_score": 0.92},
        "risk_management": {"art9_reference": "risk_assessment.json"},
    }


class TestVerifyAnnexIv:
    def test_complete_artifact_passes(self, tmp_path: Path, capsys) -> None:
        from forgelm.cli.subcommands._verify_annex_iv import _run_verify_annex_iv_cmd

        path = tmp_path / "annex_iv.json"
        path.write_text(json.dumps(_full_annex_iv_artifact()))

        args = _build_args(path=str(path))
        with pytest.raises(SystemExit) as ei:
            _run_verify_annex_iv_cmd(args, output_format="json")
        assert ei.value.code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["valid"] is True
        assert payload["missing_fields"] == []

    def test_missing_required_field_fails_with_exit_one(self, tmp_path: Path, capsys) -> None:
        from forgelm.cli.subcommands._verify_annex_iv import _run_verify_annex_iv_cmd

        artifact = _full_annex_iv_artifact()
        del artifact["risk_management"]
        path = tmp_path / "annex_iv.json"
        path.write_text(json.dumps(artifact))

        args = _build_args(path=str(path))
        with pytest.raises(SystemExit) as ei:
            _run_verify_annex_iv_cmd(args, output_format="json")
        assert ei.value.code == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["valid"] is False
        assert "risk_management" in payload["missing_fields"]

    def test_empty_required_field_treated_as_missing(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._verify_annex_iv import verify_annex_iv_artifact

        artifact = _full_annex_iv_artifact()
        artifact["intended_purpose"] = ""  # operator left placeholder
        path = tmp_path / "annex_iv.json"
        path.write_text(json.dumps(artifact))

        result = verify_annex_iv_artifact(str(path))
        assert result.valid is False
        assert "intended_purpose" in result.missing_fields

    def test_manifest_hash_match_passes(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._verify_annex_iv import (
            _compute_manifest_hash,
            verify_annex_iv_artifact,
        )

        artifact = _full_annex_iv_artifact()
        # Two-step: compute over the artifact-without-hash, then write
        # the artifact WITH the hash and verify it matches.
        artifact["metadata"] = {}
        artifact["metadata"]["manifest_hash"] = _compute_manifest_hash(artifact)
        path = tmp_path / "annex_iv.json"
        path.write_text(json.dumps(artifact))

        result = verify_annex_iv_artifact(str(path))
        assert result.valid is True
        assert result.manifest_hash_actual == result.manifest_hash_expected

    def test_manifest_hash_mismatch_fails(self, tmp_path: Path, capsys) -> None:
        from forgelm.cli.subcommands._verify_annex_iv import _run_verify_annex_iv_cmd

        artifact = _full_annex_iv_artifact()
        artifact["metadata"] = {"manifest_hash": "0" * 64}  # bogus
        path = tmp_path / "annex_iv.json"
        path.write_text(json.dumps(artifact))

        args = _build_args(path=str(path))
        with pytest.raises(SystemExit) as ei:
            _run_verify_annex_iv_cmd(args, output_format="json")
        assert ei.value.code == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["valid"] is False
        assert "manifest hash" in payload["reason"].lower()

    def test_missing_path_argument_exits_config_error(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._verify_annex_iv import _run_verify_annex_iv_cmd

        args = _build_args(path=None)
        with pytest.raises(SystemExit) as ei:
            _run_verify_annex_iv_cmd(args, output_format="json")
        assert ei.value.code == 1

    def test_file_not_found_exits_config_error(self, tmp_path: Path) -> None:
        # Round 5 absorption: file-not-found is a CALLER-input error
        # (the operator typed a wrong path), so the dispatcher emits
        # EXIT_CONFIG_ERROR (=1), not EXIT_TRAINING_ERROR (=2). Real
        # I/O failures on an existing file remain exit 2.
        from forgelm.cli.subcommands._verify_annex_iv import _run_verify_annex_iv_cmd

        args = _build_args(path=str(tmp_path / "missing.json"))
        with pytest.raises(SystemExit) as ei:
            _run_verify_annex_iv_cmd(args, output_format="json")
        assert ei.value.code == 1

    def test_malformed_json_exits_config_error(self, tmp_path: Path) -> None:
        # Round 5 absorption: malformed JSON is a caller-input
        # validation error (the artefact is reachable but unparseable),
        # so the dispatcher emits EXIT_CONFIG_ERROR (=1).
        from forgelm.cli.subcommands._verify_annex_iv import _run_verify_annex_iv_cmd

        path = tmp_path / "annex_iv.json"
        path.write_text("not even json {")
        args = _build_args(path=str(path))
        with pytest.raises(SystemExit) as ei:
            _run_verify_annex_iv_cmd(args, output_format="json")
        assert ei.value.code == 1

    def test_module_docstring_exit_codes_match_implementation(self) -> None:
        """F-P4-OPUS-06 (XP-18): the module docstring's exit-code table must
        agree with the dispatcher. Pre-fix, the docstring claimed exit 2 for
        'file not found / malformed JSON' while the code returns exit 1 for
        both (only a genuine runtime I/O failure on an existing file maps to
        2). Assert the '2 —' line no longer lists those caller-input cases."""
        import forgelm.cli.subcommands._verify_annex_iv as mod

        doc = mod.__doc__ or ""
        two_line = next((ln for ln in doc.splitlines() if ln.lstrip().startswith("- 2 —")), "")
        assert two_line, "exit-code 2 row missing from module docstring"
        assert "file not found" not in two_line.lower()
        assert "malformed json" not in two_line.lower()
        # The exit-1 row owns the caller-input failures.
        assert "- 1 —" in doc and "malformed JSON" in doc

    def test_writer_round_trip_passes_verifier(self, tmp_path: Path) -> None:
        """F-W2B-01 + F-W2B-05 regression: a freshly-generated Annex IV
        artefact must pass its own verifier (writer + verifier shape +
        manifest hash all line up byte-for-byte)."""
        from forgelm.cli.subcommands._verify_annex_iv import verify_annex_iv_artifact
        from forgelm.compliance import build_annex_iv_artifact

        # Synthetic manifest mirroring what generate_training_manifest
        # would produce against a real ForgeConfig.  Only the keys the
        # §1-9 layout consults need to be populated.
        manifest = {
            "forgelm_version": "0.5.5+test",
            "generated_at": "2026-05-04T12:00:00+00:00",
            "model_lineage": {"base_model": "gpt2", "backend": "transformers"},
            "training_parameters": {"trainer_type": "sft", "epochs": 1},
            "data_provenance": {"primary_dataset": "train.jsonl", "fingerprint": "sha256:abc"},
            "evaluation_results": {"metrics": {"eval_loss": 1.4}},
            "annex_iv": {
                "provider_name": "Acme Compliance Ltd",
                "provider_contact": "compliance@acme.example",
                "system_name": "ForgeLM-test",
                "intended_purpose": "Customer-support fine-tuning research baseline",
                "known_limitations": "Tested on EN only",
                "system_version": "0.5.5",
                "risk_classification": "minimal-risk",
            },
            "risk_assessment": {"intended_use": "Internal QA assistant", "art9_reference": "RA-001"},
        }
        artifact = build_annex_iv_artifact(manifest)
        assert artifact is not None, "writer must produce an artefact when annex_iv block is populated"

        # Write + read round-trip to mirror the on-disk path the operator
        # would invoke verify-annex-iv against.
        path = tmp_path / "annex_iv_metadata.json"
        path.write_text(json.dumps(artifact, indent=2, default=str))
        result = verify_annex_iv_artifact(str(path))
        assert result.valid is True, f"writer output must verify: {result.reason}"
        assert result.missing_fields == []
        # Tampering detection must have fired (manifest_hash present + matched).
        assert result.manifest_hash_actual == result.manifest_hash_expected
        assert result.manifest_hash_actual != ""

    def test_writer_emits_manifest_hash_that_verifier_rejects_tampered(self, tmp_path: Path) -> None:
        """F-W2B-05 regression: tampering-detection branch must actually fire.
        Mutate one field after writing; assert verifier rejects."""
        import json as _json

        from forgelm.cli.subcommands._verify_annex_iv import verify_annex_iv_artifact
        from forgelm.compliance import build_annex_iv_artifact

        manifest = {
            "forgelm_version": "0.5.5+test",
            "model_lineage": {"base_model": "gpt2"},
            "training_parameters": {"trainer_type": "sft"},
            "data_provenance": {"primary_dataset": "train.jsonl"},
            "evaluation_results": {"metrics": {"eval_loss": 1.0}},
            "annex_iv": {
                "provider_name": "Acme",
                "provider_contact": "x@y",
                "system_name": "S",
                "intended_purpose": "P",
                "known_limitations": "",
                "system_version": "1",
                "risk_classification": "minimal-risk",
            },
            "risk_assessment": {"art9_reference": "RA-001"},
        }
        artifact = build_annex_iv_artifact(manifest)
        # Tamper with a populated field after the writer stamped the hash.
        artifact["intended_purpose"] = "MALICIOUSLY MODIFIED"
        path = tmp_path / "annex_iv_metadata.json"
        path.write_text(_json.dumps(artifact, indent=2, default=str))
        result = verify_annex_iv_artifact(str(path))
        assert result.valid is False
        assert "manifest hash" in result.reason.lower()


# ---------------------------------------------------------------------------
# verify-gguf
# ---------------------------------------------------------------------------


def _make_minimal_gguf(path: Path, *, magic: bytes = b"GGUF", payload_size: int = 256) -> None:
    """Write a minimal GGUF-shaped file (magic + zero-padded payload).

    The file is *not* a real GGUF — it has the correct 4-byte magic
    header but the rest is zero-padded.  When the optional ``gguf``
    package is installed in the test env, ``GGUFReader`` would refuse
    to parse the metadata block; success-path tests therefore patch
    :func:`forgelm.cli.subcommands._verify_gguf._maybe_parse_metadata`
    to return a benign "parsed=False" result via the
    :func:`_stub_metadata_parse` helper below.
    """
    path.write_bytes(magic + b"\x00" * payload_size)


def _stub_metadata_parse(monkeypatch) -> None:
    """Patch the metadata parse to a benign no-op.

    The minimal GGUF fixture (magic + zero padding) does NOT carry a
    real metadata block; the genuine ``gguf.GGUFReader`` would surface
    that as an error and trip the success-path tests when the optional
    ``gguf`` extra is installed.  Production code path is covered
    elsewhere (the ``corrupted_magic_fails`` test still exercises the
    real magic-header check).
    """
    from forgelm.cli.subcommands import _verify_gguf

    monkeypatch.setattr(
        _verify_gguf,
        "_maybe_parse_metadata",
        lambda _path: {"parsed": False, "error": None, "tensor_count": None},
    )


class TestVerifyGguf:
    def test_valid_magic_passes(self, tmp_path: Path, capsys, monkeypatch) -> None:
        _stub_metadata_parse(monkeypatch)
        from forgelm.cli.subcommands._verify_gguf import _run_verify_gguf_cmd

        path = tmp_path / "model.gguf"
        _make_minimal_gguf(path)

        args = _build_args(path=str(path))
        with pytest.raises(SystemExit) as ei:
            _run_verify_gguf_cmd(args, output_format="json")
        assert ei.value.code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["valid"] is True
        assert payload["checks"]["magic_ok"] is True

    def test_corrupted_magic_fails_with_exit_one(self, tmp_path: Path, capsys) -> None:
        from forgelm.cli.subcommands._verify_gguf import _run_verify_gguf_cmd

        # No metadata-stub here: the magic check fires *before* the
        # metadata branch, so the corrupted-magic path is identical
        # whether or not gguf is installed.
        path = tmp_path / "model.gguf"
        _make_minimal_gguf(path, magic=b"NOPE")

        args = _build_args(path=str(path))
        with pytest.raises(SystemExit) as ei:
            _run_verify_gguf_cmd(args, output_format="json")
        assert ei.value.code == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["valid"] is False
        assert "magic" in payload["reason"].lower()

    def test_sha256_sidecar_match_passes(self, tmp_path: Path, capsys, monkeypatch) -> None:
        _stub_metadata_parse(monkeypatch)
        from forgelm.cli.subcommands._verify_gguf import _run_verify_gguf_cmd

        path = tmp_path / "model.gguf"
        _make_minimal_gguf(path)
        # Compute real SHA-256 of the file we wrote; write sidecar.
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        (tmp_path / "model.gguf.sha256").write_text(f"{actual}  model.gguf\n")

        args = _build_args(path=str(path))
        with pytest.raises(SystemExit) as ei:
            _run_verify_gguf_cmd(args, output_format="json")
        assert ei.value.code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["checks"]["sidecar_present"] is True
        assert payload["checks"]["sidecar_match"] is True

    def test_sha256_sidecar_mismatch_fails_with_exit_one(self, tmp_path: Path, capsys, monkeypatch) -> None:
        _stub_metadata_parse(monkeypatch)
        from forgelm.cli.subcommands._verify_gguf import _run_verify_gguf_cmd

        path = tmp_path / "model.gguf"
        _make_minimal_gguf(path)
        (tmp_path / "model.gguf.sha256").write_text("0" * 64 + "  model.gguf\n")

        args = _build_args(path=str(path))
        with pytest.raises(SystemExit) as ei:
            _run_verify_gguf_cmd(args, output_format="json")
        assert ei.value.code == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["valid"] is False
        assert "sha-256" in payload["reason"].lower() or "sha256" in payload["reason"].lower()

    @pytest.mark.parametrize(
        "sidecar_text,expected_substring",
        [
            ("", "malformed sha-256"),  # empty
            ("not-a-hash\n", "malformed sha-256"),  # garbage
            ("abcdef\n", "malformed sha-256"),  # too short
            ("z" * 64 + "\n", "malformed sha-256"),  # right length, wrong charset
        ],
    )
    def test_malformed_sidecar_fails_closed(
        self, tmp_path: Path, capsys, monkeypatch, sidecar_text: str, expected_substring: str
    ) -> None:
        """A present but malformed SHA-256 sidecar must surface as a
        verification *failure* (operator error), not silently accept
        the artefact as 'verified'."""
        _stub_metadata_parse(monkeypatch)
        from forgelm.cli.subcommands._verify_gguf import _run_verify_gguf_cmd

        path = tmp_path / "model.gguf"
        _make_minimal_gguf(path)
        (tmp_path / "model.gguf.sha256").write_text(sidecar_text)

        args = _build_args(path=str(path))
        with pytest.raises(SystemExit) as ei:
            _run_verify_gguf_cmd(args, output_format="json")
        assert ei.value.code == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["valid"] is False
        assert expected_substring in payload["reason"].lower()

    def test_missing_path_exits_config_error(self) -> None:
        from forgelm.cli.subcommands._verify_gguf import _run_verify_gguf_cmd

        args = _build_args(path=None)
        with pytest.raises(SystemExit) as ei:
            _run_verify_gguf_cmd(args, output_format="json")
        assert ei.value.code == 1

    def test_file_not_found_exits_config_error(self, tmp_path: Path) -> None:
        # Round 5 absorption: file-not-found is a CALLER-input error.
        # The dispatcher emits EXIT_CONFIG_ERROR (=1); EXIT_TRAINING_ERROR
        # (=2) is reserved for genuine I/O failures on an existing file.
        from forgelm.cli.subcommands._verify_gguf import _run_verify_gguf_cmd

        args = _build_args(path=str(tmp_path / "missing.gguf"))
        with pytest.raises(SystemExit) as ei:
            _run_verify_gguf_cmd(args, output_format="json")
        assert ei.value.code == 1

    def test_input_error_json_uses_same_indent_as_result(self, tmp_path: Path, capsys) -> None:
        """F-P7-OPUS-43: the input-error envelope must use the same ``indent=2``
        shape as the result envelope so a single subcommand does not emit two
        different whitespace contracts on stdout."""
        from forgelm.cli.subcommands._verify_gguf import _run_verify_gguf_cmd

        args = _build_args(path=str(tmp_path / "missing.gguf"))
        with pytest.raises(SystemExit):
            _run_verify_gguf_cmd(args, output_format="json")
        out = capsys.readouterr().out.strip()
        payload = json.loads(out)
        assert payload["success"] is False
        # indent=2 -> pretty-printed multi-line, not a compact single line.
        assert "\n" in out
        assert out.startswith("{\n")


# ---------------------------------------------------------------------------
# safety-eval (dispatcher-layer only — generation path is covered elsewhere)
# ---------------------------------------------------------------------------


class TestSafetyEvalDispatcher:
    def test_missing_model_exits_config_error(self, tmp_path: Path, capsys) -> None:
        from forgelm.cli.subcommands._safety_eval import _run_safety_eval_cmd

        args = _build_args(
            model=None,
            classifier=None,
            probes=None,
            default_probes=False,
            output_dir=str(tmp_path),
            max_new_tokens=128,
        )
        with pytest.raises(SystemExit) as ei:
            _run_safety_eval_cmd(args, output_format="json")
        assert ei.value.code == 1

    def test_neither_probes_nor_default_probes_exits_config_error(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._safety_eval import _run_safety_eval_cmd

        args = _build_args(
            model="gpt2",
            classifier=None,
            probes=None,
            default_probes=False,
            output_dir=str(tmp_path),
            max_new_tokens=128,
        )
        with pytest.raises(SystemExit) as ei:
            _run_safety_eval_cmd(args, output_format="json")
        assert ei.value.code == 1

    def test_both_probes_and_default_probes_rejected(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._safety_eval import _resolve_probes_path

        probes = tmp_path / "probes.jsonl"
        probes.write_text('{"prompt": "x"}\n')
        args = _build_args(probes=str(probes), default_probes=True)
        with pytest.raises(SystemExit) as ei:
            _resolve_probes_path(args, output_format="json")
        assert ei.value.code == 1

    def test_default_probes_resolves_to_bundled_file(self) -> None:
        from forgelm.cli.subcommands._safety_eval import _DEFAULT_PROBES_RELPATH, _resolve_probes_path

        args = _build_args(probes=None, default_probes=True)
        path = _resolve_probes_path(args, output_format="json")
        assert path == _DEFAULT_PROBES_RELPATH
        # And the bundled file exists + has at least 50 entries.
        with open(path, "r", encoding="utf-8") as fh:
            count = sum(1 for line in fh if line.strip())
        assert count >= 50, f"bundled default-probes should have >=50 prompts, got {count}"

    def test_explicit_probes_path_accepted(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._safety_eval import _resolve_probes_path

        probes = tmp_path / "probes.jsonl"
        probes.write_text('{"prompt": "x"}\n')
        args = _build_args(probes=str(probes), default_probes=False)
        assert _resolve_probes_path(args, output_format="json") == str(probes)

    def test_explicit_probes_missing_exits_config_error(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._safety_eval import _resolve_probes_path

        args = _build_args(probes=str(tmp_path / "nonexistent.jsonl"), default_probes=False)
        with pytest.raises(SystemExit) as ei:
            _resolve_probes_path(args, output_format="json")
        assert ei.value.code == 1

    def test_failed_safety_gate_exits_eval_failure(self, tmp_path: Path, monkeypatch) -> None:
        """F-36-T-01: Round-5 absorption switched the non-passing safety
        branch from ``EXIT_CONFIG_ERROR`` (1) to ``EXIT_EVAL_FAILURE`` (3)
        so regulated CI can distinguish "the gate said no" (3 → re-train)
        from "the run never started" (1 → fix YAML).  Without this test,
        a regression to ``sys.exit(EXIT_CONFIG_ERROR if not passed else
        EXIT_SUCCESS)`` would silently pass CI."""
        from forgelm.cli.subcommands import _safety_eval

        stub_result = SimpleNamespace(
            passed=False,
            safety_score=0.4,
            safe_ratio=0.5,
            category_distribution={},
            failure_reason="threshold-exceeded",
        )
        # Short-circuit the model + classifier load so we don't need torch.
        monkeypatch.setattr(_safety_eval, "_load_model_for_safety", lambda *a, **kw: (object(), object()))
        monkeypatch.setattr("forgelm.safety.run_safety_evaluation", lambda **kw: stub_result)

        probes = tmp_path / "probes.jsonl"
        probes.write_text('{"prompt": "x"}\n')
        args = _build_args(
            model="gpt2",
            classifier=None,
            probes=str(probes),
            default_probes=False,
            output_dir=str(tmp_path),
            max_new_tokens=8,
        )
        with pytest.raises(SystemExit) as ei:
            _safety_eval._run_safety_eval_cmd(args, output_format="json")
        assert ei.value.code == 3, (
            f"safety-eval must exit EXIT_EVAL_FAILURE (3) on safety-gate non-pass, got {ei.value.code}"
        )

    def test_classifier_load_failure_exits_training_error(self, tmp_path: Path, monkeypatch) -> None:
        """F-P3-FABLE-12: a classifier that never loaded is a runtime problem
        (exit 2), not a threshold failure (exit 3).  run_safety_evaluation
        flags this with ``evaluation_completed=False``."""
        from forgelm.cli.subcommands import _safety_eval

        stub_result = SimpleNamespace(
            passed=False,
            evaluation_completed=False,
            safety_score=None,
            safe_ratio=1.0,
            category_distribution={},
            failure_reason="Classifier load failed: boom",
        )
        monkeypatch.setattr(_safety_eval, "_load_model_for_safety", lambda *a, **kw: (object(), object()))
        monkeypatch.setattr("forgelm.safety.run_safety_evaluation", lambda **kw: stub_result)

        probes = tmp_path / "probes.jsonl"
        probes.write_text('{"prompt": "x"}\n')
        args = _build_args(
            model="gpt2",
            classifier="./nonexistent",
            probes=str(probes),
            default_probes=False,
            output_dir=str(tmp_path),
            max_new_tokens=8,
        )
        with pytest.raises(SystemExit) as ei:
            _safety_eval._run_safety_eval_cmd(args, output_format="json")
        assert ei.value.code == 2, (
            f"safety-eval must exit EXIT_TRAINING_ERROR (2) when the classifier never loaded, got {ei.value.code}"
        )

    def test_standalone_enables_track_categories_and_audit_logger(self, tmp_path: Path, monkeypatch) -> None:
        """F-P3-FABLE-13/12: the standalone path must enable category tracking
        (so the documented breakdown is reachable) and wire an AuditLogger (so
        the documented classifier_load_failed event can fire)."""
        from forgelm.cli.subcommands import _safety_eval

        captured: dict = {}

        def fake_run(**kw):
            captured.update(kw)
            return SimpleNamespace(
                passed=True,
                evaluation_completed=True,
                safety_score=0.99,
                safe_ratio=0.99,
                category_distribution={"violent_crimes": 1},
                failure_reason=None,
            )

        # Pin a deterministic operator so AuditLogger construction never trips
        # the anonymous-operator refusal in a CI runner with no login user.
        monkeypatch.setenv("FORGELM_OPERATOR", "ci@test")
        monkeypatch.setattr(_safety_eval, "_load_model_for_safety", lambda *a, **kw: (object(), object()))
        monkeypatch.setattr("forgelm.safety.run_safety_evaluation", fake_run)

        probes = tmp_path / "probes.jsonl"
        probes.write_text('{"prompt": "x"}\n')
        args = _build_args(
            model="gpt2",
            classifier=None,
            probes=str(probes),
            default_probes=False,
            output_dir=str(tmp_path),
            max_new_tokens=8,
        )
        with pytest.raises(SystemExit) as ei:
            _safety_eval._run_safety_eval_cmd(args, output_format="json")
        assert ei.value.code == 0
        assert captured["thresholds"].track_categories is True
        assert captured["audit_logger"] is not None


# ---------------------------------------------------------------------------
# Wave 2b final-review absorption — F-36-03 parametrised tampering test
# over all 9 §1-9 fields plus provider_metadata.
# ---------------------------------------------------------------------------


def _round_trip_manifest_for_tampering() -> dict:
    """Minimal manifest that ``build_annex_iv_artifact`` can synthesise into
    a complete §1-9 artifact.  Extracted so the parametrised test does
    not duplicate the fixture inline for every parameter."""
    return {
        "forgelm_version": "0.5.5+test",
        "model_lineage": {"base_model": "gpt2"},
        "training_parameters": {"trainer_type": "sft"},
        "data_provenance": {"primary_dataset": "train.jsonl"},
        "evaluation_results": {"metrics": {"eval_loss": 1.0}},
        "annex_iv": {
            "provider_name": "Acme",
            "provider_contact": "x@y",
            "system_name": "S",
            "intended_purpose": "P",
            "known_limitations": "",
            "system_version": "1",
            "risk_classification": "minimal-risk",
        },
        "risk_assessment": {"art9_reference": "RA-001"},
    }


class TestAnnexIvTamperingAcrossAllFields:
    """F-36-03: the existing tampering regression mutates only
    ``intended_purpose``.  This parametrised version walks every §1-9
    canonical field plus the operator-friendly ``provider_metadata``
    mirror so a regression that excluded a sub-block from the
    canonicalisation would be caught."""

    @pytest.mark.parametrize(
        "field_to_tamper",
        [
            "system_identification",
            "intended_purpose",
            "system_components",
            "computational_resources",
            "data_governance",
            "technical_documentation",
            "monitoring_and_logging",
            "performance_metrics",
            "risk_management",
            "provider_metadata",
        ],
    )
    def test_writer_verifier_rejects_tampering_in_any_field(self, tmp_path: Path, field_to_tamper: str) -> None:
        from forgelm.cli.subcommands._verify_annex_iv import verify_annex_iv_artifact
        from forgelm.compliance import build_annex_iv_artifact

        artifact = build_annex_iv_artifact(_round_trip_manifest_for_tampering())
        assert artifact is not None, "writer must produce an artifact for the test fixture"
        # Mutate the field after the writer stamped the hash.  For
        # ``system_identification`` we mutate a value *inside* the dict
        # while keeping the identity-critical sub-fields populated, so the
        # hash-mismatch branch fires rather than the §1 completeness gate
        # (F-P4-OPUS-17) — the test's intent is to prove the hash covers
        # this sub-block, not the completeness check.
        if field_to_tamper == "system_identification":
            artifact[field_to_tamper] = {
                "provider_name": "Acme",
                "system_name": "S",
                "intended_purpose": "P",
                "provider_contact": "tampered-by-test",
            }
        else:
            artifact[field_to_tamper] = {"sentinel": "tampered-by-test"}
        path = tmp_path / "annex_iv_metadata.json"
        path.write_text(json.dumps(artifact, indent=2, default=str))
        result = verify_annex_iv_artifact(str(path))
        assert result.valid is False, (
            f"tampering with {field_to_tamper!r} must be rejected by the verifier; "
            f"a passing result here means the hash skips this sub-block."
        )
        assert "manifest hash" in result.reason.lower(), (
            f"verifier must cite 'manifest hash' in the reason for {field_to_tamper!r} tampering; got {result.reason!r}"
        )


# ---------------------------------------------------------------------------
# F-P4-OPUS-16 — writer/verifier hash stability for non-JSON-native inputs.
# ---------------------------------------------------------------------------


class TestAnnexIvHashStableForNonNativeTypes:
    """The writer hashes the in-memory dict; the verifier hashes the
    JSON-round-tripped dict.  For non-JSON-native content (integer dict
    keys, sets) these used to canonicalise differently, producing a
    false-tampering verdict on a legitimate artefact (F-P4-OPUS-16)."""

    def test_integer_keyed_metrics_round_trip_stable(self) -> None:
        from forgelm.compliance import compute_annex_iv_manifest_hash

        artifact = {"performance_metrics": {1: "a", 2: "b", 10: "c"}}
        # Writer hashes the in-memory dict; verifier hashes what landed
        # on disk after ``default=str`` stringified the integer keys.
        on_disk = json.loads(json.dumps(artifact, default=str))
        assert compute_annex_iv_manifest_hash(artifact) == compute_annex_iv_manifest_hash(on_disk)

    def test_set_valued_field_round_trip_stable(self) -> None:
        from forgelm.compliance import compute_annex_iv_manifest_hash

        artifact = {"system_components": {"training_parameters": {"target_modules": {"q_proj", "v_proj", "k_proj"}}}}
        on_disk = json.loads(json.dumps(artifact, default=str))
        assert compute_annex_iv_manifest_hash(artifact) == compute_annex_iv_manifest_hash(on_disk)


# ---------------------------------------------------------------------------
# F-P4-OPUS-17 — §1 completeness gate must inspect nested identity fields.
# ---------------------------------------------------------------------------


class TestAnnexIvSystemIdentificationCompleteness:
    """A ``system_identification`` dict whose identity-critical sub-fields
    (provider_name, system_name, intended_purpose) are blank must be
    rejected — the container-only check let it pass as 'populated'
    (F-P4-OPUS-17)."""

    def test_blank_provider_and_system_name_rejected(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._verify_annex_iv import verify_annex_iv_artifact

        artifact = _full_annex_iv_artifact()
        # Top-level fields stay populated; only the §1 identity sub-fields
        # are blanked, which the old container-length check ignored.
        artifact["system_identification"] = {
            "provider_name": "",
            "provider_contact": "",
            "system_name": "",
            "system_version": "",
            "intended_purpose": "",
            "risk_classification": "minimal-risk",
        }
        path = tmp_path / "annex_iv.json"
        path.write_text(json.dumps(artifact))

        result = verify_annex_iv_artifact(str(path))
        assert result.valid is False
        assert "system_identification.provider_name" in result.missing_fields
        assert "system_identification.system_name" in result.missing_fields

    def test_writer_skips_artifact_when_identity_subfields_all_blank(self) -> None:
        from forgelm.compliance import build_annex_iv_artifact

        manifest = _round_trip_manifest_for_tampering()
        manifest["annex_iv"]["provider_name"] = ""
        manifest["annex_iv"]["system_name"] = ""
        manifest["annex_iv"]["intended_purpose"] = ""
        # Writer must skip (return None) rather than emit a §1 stub the
        # verifier would then have to catch.
        assert build_annex_iv_artifact(manifest) is None


# ---------------------------------------------------------------------------
# F-P4-OPUS-14 — verify-integrity (Article 15 consuming verifier).
# ---------------------------------------------------------------------------


def _write_model_with_integrity(model_dir: Path) -> dict:
    """Create a model dir + matching model_integrity.json and return it."""
    from forgelm.compliance import generate_model_integrity

    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "model.safetensors").write_bytes(b"weights-v1")
    (model_dir / "config.json").write_text('{"a": 1}')
    integrity = generate_model_integrity(str(model_dir))
    (model_dir / "model_integrity.json").write_text(json.dumps(integrity, indent=2))
    return integrity


class TestVerifyIntegrity:
    def test_unmodified_model_passes(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._verify_integrity import verify_integrity

        model_dir = tmp_path / "final_model"
        _write_model_with_integrity(model_dir)
        result = verify_integrity(str(model_dir))
        assert result.valid is True
        assert result.verified_count == 2

    def test_changed_artifact_byte_detected_and_exits_one(self, tmp_path: Path, capsys) -> None:
        from forgelm.cli.subcommands._verify_integrity import _run_verify_integrity_cmd

        model_dir = tmp_path / "final_model"
        _write_model_with_integrity(model_dir)
        # Mutate one recorded artifact after the manifest was written.
        (model_dir / "model.safetensors").write_bytes(b"weights-TAMPERED")

        args = _build_args(path=str(model_dir))
        with pytest.raises(SystemExit) as ei:
            _run_verify_integrity_cmd(args, output_format="json")
        assert ei.value.code == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["valid"] is False
        assert "model.safetensors" in payload["changed"]

    def test_added_file_detected(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._verify_integrity import verify_integrity

        model_dir = tmp_path / "final_model"
        _write_model_with_integrity(model_dir)
        (model_dir / "rogue.bin").write_bytes(b"unexpected")

        result = verify_integrity(str(model_dir))
        assert result.valid is False
        assert "rogue.bin" in result.added

    def test_removed_artifact_detected(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._verify_integrity import verify_integrity

        model_dir = tmp_path / "final_model"
        _write_model_with_integrity(model_dir)
        (model_dir / "config.json").unlink()

        result = verify_integrity(str(model_dir))
        assert result.valid is False
        assert "config.json" in result.removed

    def test_missing_manifest_exits_config_error(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._verify_integrity import _run_verify_integrity_cmd

        model_dir = tmp_path / "final_model"
        model_dir.mkdir()
        args = _build_args(path=str(model_dir))
        with pytest.raises(SystemExit) as ei:
            _run_verify_integrity_cmd(args, output_format="json")
        assert ei.value.code == 1

    def test_missing_path_argument_exits_config_error(self) -> None:
        from forgelm.cli.subcommands._verify_integrity import _run_verify_integrity_cmd

        args = _build_args(path=None)
        with pytest.raises(SystemExit) as ei:
            _run_verify_integrity_cmd(args, output_format="json")
        assert ei.value.code == 1


# ---------------------------------------------------------------------------
# Library API exposure
# ---------------------------------------------------------------------------


class TestVerificationToolbeltFacade:
    def test_facade_re_exports_all_three_subcommands(self) -> None:
        from forgelm import cli as _cli_facade

        for name in (
            "_run_verify_annex_iv_cmd",
            "_run_safety_eval_cmd",
            "_run_verify_gguf_cmd",
            "_run_verify_integrity_cmd",
            "verify_annex_iv_artifact",
            "verify_gguf",
            "verify_integrity",
            "VerifyAnnexIVResult",
            "VerifyGgufResult",
            "VerifyIntegrityResult",
        ):
            assert hasattr(_cli_facade, name), f"forgelm.cli must re-export {name!r}"
