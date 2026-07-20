"""Phase 36 — `forgelm verify-annex-iv` + `safety-eval` + `verify-gguf`.

Tests run torch-free for the verification subcommands; safety-eval is
exercised at the dispatcher / argument-parsing layer (the underlying
generation path requires torch + a real model and is covered by the
existing safety_evaluation tests, which we do not duplicate here).
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
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
        from forgelm.verify import verify_annex_iv_artifact

        artifact = _full_annex_iv_artifact()
        artifact["intended_purpose"] = ""  # operator left placeholder
        path = tmp_path / "annex_iv.json"
        path.write_text(json.dumps(artifact))

        result = verify_annex_iv_artifact(str(path))
        assert result.valid is False
        assert "intended_purpose" in result.missing_fields

    def test_manifest_hash_match_passes(self, tmp_path: Path) -> None:
        from forgelm.verify import _compute_manifest_hash, verify_annex_iv_artifact

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

    def test_manifest_hash_mismatch_exits_integrity_failure(self, tmp_path: Path, capsys) -> None:
        """A recomputed manifest hash that disagrees with the recorded one is
        tampering, not operator input → exit 6.

        This asserted exit 1 before ``EXIT_INTEGRITY_FAILURE`` existed, which
        is exactly the defect: a *modified compliance artefact* and a *typo in
        the path* were indistinguishable to a CI pipeline."""
        from forgelm.cli._exit_codes import EXIT_INTEGRITY_FAILURE
        from forgelm.cli.subcommands._verify_annex_iv import _run_verify_annex_iv_cmd

        artifact = _full_annex_iv_artifact()
        artifact["metadata"] = {"manifest_hash": "0" * 64}  # bogus
        path = tmp_path / "annex_iv.json"
        path.write_text(json.dumps(artifact))

        args = _build_args(path=str(path))
        with pytest.raises(SystemExit) as ei:
            _run_verify_annex_iv_cmd(args, output_format="json")
        assert ei.value.code == EXIT_INTEGRITY_FAILURE
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
        from forgelm.compliance import build_annex_iv_artifact
        from forgelm.verify import verify_annex_iv_artifact

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

        from forgelm.compliance import build_annex_iv_artifact
        from forgelm.verify import verify_annex_iv_artifact

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
    :func:`forgelm.verify._maybe_parse_metadata` to return a benign
    "parsed=False" result via the :func:`_stub_metadata_parse` helper
    below.
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

    Patches ``forgelm.verify`` (where ``verify_gguf`` now resolves the
    helper from) rather than the CLI subcommand module, which only
    re-exports the public entry point.
    """
    from forgelm import verify as _verify_mod

    monkeypatch.setattr(
        _verify_mod,
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

    def test_sha256_sidecar_mismatch_exits_integrity_failure(self, tmp_path: Path, capsys, monkeypatch) -> None:
        """A well-formed sidecar digest that does not match the file means the
        GGUF was modified after export → exit 6.

        Asserted exit 1 before ``EXIT_INTEGRITY_FAILURE`` existed, conflating
        "this artefact was tampered with" with "you typed the wrong path"."""
        _stub_metadata_parse(monkeypatch)
        from forgelm.cli._exit_codes import EXIT_INTEGRITY_FAILURE
        from forgelm.cli.subcommands._verify_gguf import _run_verify_gguf_cmd

        path = tmp_path / "model.gguf"
        _make_minimal_gguf(path)
        (tmp_path / "model.gguf.sha256").write_text("0" * 64 + "  model.gguf\n")

        args = _build_args(path=str(path))
        with pytest.raises(SystemExit) as ei:
            _run_verify_gguf_cmd(args, output_format="json")
        assert ei.value.code == EXIT_INTEGRITY_FAILURE
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
        from forgelm.compliance import build_annex_iv_artifact
        from forgelm.verify import verify_annex_iv_artifact

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
        from forgelm.compliance import _manifest_json_default, compute_annex_iv_manifest_hash

        artifact = {"system_components": {"training_parameters": {"target_modules": {"q_proj", "v_proj", "k_proj"}}}}
        on_disk = json.loads(json.dumps(artifact, default=_manifest_json_default))
        assert compute_annex_iv_manifest_hash(artifact) == compute_annex_iv_manifest_hash(on_disk)

    def test_set_serialises_deterministically_regardless_of_insertion_order(self) -> None:
        """A ``set`` of LoRA target_modules must hash identically no matter
        what iteration order PYTHONHASHSEED imposes — a bare ``default=str``
        emits members in hash-randomised order, so two processes produced
        different manifest hashes and a false-tampering verdict
        (F-P4-OPUS-16)."""
        from forgelm.compliance import compute_annex_iv_manifest_hash

        modules = {"q_proj", "v_proj", "k_proj", "o_proj"}
        artifact_a = {"system_components": {"target_modules": set(modules)}}
        # A frozenset and a differently-constructed set with the same members
        # stand in for the divergent iteration orders two PYTHONHASHSEED
        # processes would see; the on-disk shape must be byte-identical.
        artifact_b = {"system_components": {"target_modules": frozenset(reversed(list(modules)))}}
        assert compute_annex_iv_manifest_hash(artifact_a) == compute_annex_iv_manifest_hash(artifact_b)

    def test_manifest_default_emits_sorted_list_for_sets(self) -> None:
        from forgelm.compliance import _manifest_json_default

        assert _manifest_json_default({"b", "a", "c"}) == ["a", "b", "c"]
        assert _manifest_json_default(frozenset({"z", "y"})) == ["y", "z"]


# ---------------------------------------------------------------------------
# F-P4-OPUS-17 — §1 completeness gate must inspect nested identity fields.
# ---------------------------------------------------------------------------


class TestAnnexIvComparisonCountIsNotInputDetermined:
    """Why ``verify_annex_iv_artifact`` is *not* the fail-open its three
    siblings were, asserted rather than argued in a comment.

    The fail-open class is "the number of comparisons is determined by the
    input, that number is zero, and the verifier reports success".  It bit
    ``verify-integrity`` (``artifacts: []`` → 0 hashes → pass) and
    ``verify-audit`` (0 lines → 0 chain links → pass).  Annex IV is
    structurally immune: its checklist is the module-level
    ``_ANNEX_IV_REQUIRED_FIELDS`` tuple, so every artefact is measured
    against the same 9 fields plus 3 §1 sub-fields no matter what it
    contains.  An input cannot shrink the checklist to nothing, and the
    degenerate roots that would bypass it are rejected up front.

    This test guards that property, so a future refactor that derived the
    field list from the artefact (``for key in artifact``) would fail here
    instead of shipping the same bug a fourth time.
    """

    @pytest.mark.parametrize("root", [{}, [], "", 0, None, {"metadata": {}}, {"artifacts": []}])
    def test_no_input_shape_passes_with_zero_comparisons(self, tmp_path: Path, root) -> None:
        from forgelm.verify import verify_annex_iv_artifact

        path = tmp_path / "annex_iv.json"
        path.write_text(json.dumps(root))
        result = verify_annex_iv_artifact(str(path))
        assert result.valid is False, f"{root!r} verified clean"

    def test_checklist_is_static_not_derived_from_the_artifact(self, tmp_path: Path) -> None:
        """An empty object and a fully-populated-but-blank object are both
        measured against the *same* checklist length."""
        from forgelm.verify import _ANNEX_IV_REQUIRED_FIELDS, verify_annex_iv_artifact

        path = tmp_path / "annex_iv.json"
        path.write_text(json.dumps({}))
        result = verify_annex_iv_artifact(str(path))
        # Every catalogued field is reported missing — the checklist did not
        # shrink to match the (empty) input.
        assert len(result.missing_fields) == len(_ANNEX_IV_REQUIRED_FIELDS)

    def test_hashless_pass_discloses_the_skipped_check(self, tmp_path: Path) -> None:
        """The one branch that *does* skip a comparison — an artefact with no
        ``metadata.manifest_hash``, so tamper detection cannot run — still
        makes all 12 field comparisons, and says in ``reason`` that the hash
        check was skipped.

        That disclosure is precisely what the fail-open cases lacked: they
        reported an unqualified success for work they had not done.  A
        reduced-strength pass that names its own limitation is a different
        thing from a vacuous one, so this branch is deliberately left as-is.
        """
        from forgelm.verify import verify_annex_iv_artifact

        artifact = _full_annex_iv_artifact()
        artifact.pop("metadata", None)
        path = tmp_path / "annex_iv.json"
        path.write_text(json.dumps(artifact))

        result = verify_annex_iv_artifact(str(path))
        assert result.valid is True
        assert "skipped" in result.reason.lower()
        assert result.manifest_hash_expected == ""


class TestAnnexIvSystemIdentificationCompleteness:
    """A ``system_identification`` dict whose identity-critical sub-fields
    (provider_name, system_name, intended_purpose) are blank must be
    rejected — the container-only check let it pass as 'populated'
    (F-P4-OPUS-17)."""

    def test_blank_provider_and_system_name_rejected(self, tmp_path: Path) -> None:
        from forgelm.verify import verify_annex_iv_artifact

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

    @pytest.mark.parametrize("bad_value", ["ForgeLM-test", ["provider", "system"], 42])
    def test_non_dict_system_identification_rejected(self, tmp_path: Path, bad_value) -> None:
        """A non-dict ``system_identification`` passes the bare populated
        check but cannot carry the §1 identity sub-fields, so the old
        ``isinstance(dict)`` guard silently skipped the identity gate.  It
        must be rejected as missing instead."""
        from forgelm.verify import verify_annex_iv_artifact

        artifact = _full_annex_iv_artifact()
        artifact["system_identification"] = bad_value
        path = tmp_path / "annex_iv.json"
        path.write_text(json.dumps(artifact))

        result = verify_annex_iv_artifact(str(path))
        assert result.valid is False
        assert "system_identification" in result.missing_fields


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
        from forgelm.verify import verify_integrity

        model_dir = tmp_path / "final_model"
        _write_model_with_integrity(model_dir)
        result = verify_integrity(str(model_dir))
        assert result.valid is True
        assert result.verified_count == 2

    def test_changed_artifact_byte_detected_and_exits_integrity_failure(self, tmp_path: Path, capsys) -> None:
        """A recorded artifact whose bytes changed after the manifest was
        written → exit 6.

        Asserted exit 1 before ``EXIT_INTEGRITY_FAILURE`` existed: swapped
        model weights and a mistyped directory produced the same exit code,
        so no CI gate could page on the former."""
        from forgelm.cli._exit_codes import EXIT_INTEGRITY_FAILURE
        from forgelm.cli.subcommands._verify_integrity import _run_verify_integrity_cmd

        model_dir = tmp_path / "final_model"
        _write_model_with_integrity(model_dir)
        # Mutate one recorded artifact after the manifest was written.
        (model_dir / "model.safetensors").write_bytes(b"weights-TAMPERED")

        args = _build_args(path=str(model_dir))
        with pytest.raises(SystemExit) as ei:
            _run_verify_integrity_cmd(args, output_format="json")
        assert ei.value.code == EXIT_INTEGRITY_FAILURE
        payload = json.loads(capsys.readouterr().out)
        assert payload["valid"] is False
        assert "model.safetensors" in payload["changed"]

    def test_added_file_detected(self, tmp_path: Path) -> None:
        from forgelm.verify import verify_integrity

        model_dir = tmp_path / "final_model"
        _write_model_with_integrity(model_dir)
        (model_dir / "rogue.bin").write_bytes(b"unexpected")

        result = verify_integrity(str(model_dir))
        assert result.valid is False
        assert "rogue.bin" in result.added

    def test_removed_artifact_detected(self, tmp_path: Path) -> None:
        from forgelm.verify import verify_integrity

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

    def test_path_is_file_not_dir_exits_config_error(self, tmp_path: Path, capsys) -> None:
        """A regular-file argument makes open(<file>/model_integrity.json)
        raise NotADirectoryError; that is caller input (wrong argument) and
        must map to exit 1, not the generic-OSError exit 2."""
        from forgelm.cli.subcommands._verify_integrity import _run_verify_integrity_cmd

        not_a_dir = tmp_path / "model.bin"
        not_a_dir.write_bytes(b"weights")
        args = _build_args(path=str(not_a_dir))
        with pytest.raises(SystemExit) as ei:
            _run_verify_integrity_cmd(args, output_format="json")
        assert ei.value.code == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["success"] is False

    def test_non_string_file_entry_rejected(self, tmp_path: Path) -> None:
        from forgelm.verify import verify_integrity

        model_dir = tmp_path / "final_model"
        model_dir.mkdir()
        (model_dir / "model_integrity.json").write_text(
            json.dumps({"artifacts": [{"file": 123, "sha256": "deadbeef"}]})
        )
        result = verify_integrity(str(model_dir))
        assert result.valid is False
        assert "non-string" in result.reason

    @pytest.mark.parametrize("bad_artifacts", [None, "oops", {"file": "x"}, 42])
    def test_non_list_artifacts_container_rejected(self, tmp_path: Path, bad_artifacts) -> None:
        """A non-list ``artifacts`` value (null, string, mapping, int) used to
        crash the recorded-entry comprehension with a TypeError — bypassing the
        exit-code contract.  It must be refused as a malformed manifest, not
        silently treated as zero artifacts (which would pass with exit 0)."""
        from forgelm.verify import verify_integrity

        model_dir = tmp_path / "final_model"
        model_dir.mkdir()
        (model_dir / "model_integrity.json").write_text(json.dumps({"artifacts": bad_artifacts}))
        result = verify_integrity(str(model_dir))
        assert result.valid is False
        assert "artifacts" in result.reason
        assert "list" in result.reason.lower()

    def test_non_list_artifacts_container_exits_config_error(self, tmp_path: Path, capsys) -> None:
        """The dispatcher maps the refused non-list manifest to exit 1, the
        documented EXIT_CONFIG_ERROR — never an uncaught TypeError traceback."""
        from forgelm.cli._exit_codes import EXIT_CONFIG_ERROR
        from forgelm.cli.subcommands._verify_integrity import _run_verify_integrity_cmd

        model_dir = tmp_path / "final_model"
        model_dir.mkdir()
        (model_dir / "model_integrity.json").write_text(json.dumps({"artifacts": None}))
        args = _build_args(path=str(model_dir))
        with pytest.raises(SystemExit) as ei:
            _run_verify_integrity_cmd(args, output_format="json")
        assert ei.value.code == EXIT_CONFIG_ERROR
        payload = json.loads(capsys.readouterr().out)
        assert payload["valid"] is False
        assert "artifacts" in payload["reason"]

    def test_empty_artifacts_list_rejected(self, tmp_path: Path) -> None:
        """``{"artifacts": []}`` compares nothing, so it cannot verify anything.

        Pre-fix this returned ``valid=True`` with ``verified_count=0`` and the
        CLI printed "All 0 recorded artifact(s) present and unchanged" on exit
        0 — the code a release gate reads as "these are the signed-off bytes".
        """
        from forgelm.verify import verify_integrity

        model_dir = tmp_path / "final_model"
        model_dir.mkdir()
        (model_dir / "model_integrity.json").write_text(json.dumps({"artifacts": []}))

        result = verify_integrity(str(model_dir))
        assert result.valid is False
        assert result.verified_count == 0
        assert "0 artifacts" in result.reason
        # No artifact-level verdict: nothing was compared, so the diff lists
        # must stay empty and is_model_integrity_failure must route this to 1.
        assert result.changed == []
        assert result.removed == []
        assert result.added == []

    def test_empty_artifacts_list_exits_config_error(self, tmp_path: Path, capsys) -> None:
        """The reproduction from the review, end-to-end through the dispatcher."""
        from forgelm.cli._exit_codes import EXIT_CONFIG_ERROR
        from forgelm.cli.subcommands._verify_integrity import _run_verify_integrity_cmd

        model_dir = tmp_path / "final_model"
        model_dir.mkdir()
        (model_dir / "model_integrity.json").write_text(json.dumps({"artifacts": []}))

        with pytest.raises(SystemExit) as ei:
            _run_verify_integrity_cmd(_build_args(path=str(model_dir)), output_format="json")
        assert ei.value.code == EXIT_CONFIG_ERROR
        payload = json.loads(capsys.readouterr().out)
        assert payload["success"] is False
        assert payload["valid"] is False

    def test_empty_manifest_over_populated_dir_is_config_error_not_integrity(self, tmp_path: Path) -> None:
        """An empty manifest beside real weights must exit 1, never 6.

        The emptiness guard has to fire *before* the on-disk walk: otherwise
        every file present surfaces as ``added`` and routes to
        EXIT_INTEGRITY_FAILURE, telling CI the weights were tampered with when
        the manifest simply covers nothing.
        """
        from forgelm.cli._exit_codes import EXIT_CONFIG_ERROR
        from forgelm.cli.subcommands._verify_integrity import _run_verify_integrity_cmd

        model_dir = tmp_path / "final_model"
        model_dir.mkdir()
        (model_dir / "model.safetensors").write_bytes(b"weights-v1")
        (model_dir / "model_integrity.json").write_text(json.dumps({"artifacts": []}))

        with pytest.raises(SystemExit) as ei:
            _run_verify_integrity_cmd(_build_args(path=str(model_dir)), output_format="json")
        assert ei.value.code == EXIT_CONFIG_ERROR

    def test_generated_manifest_for_absent_dir_does_not_self_verify(self, tmp_path: Path) -> None:
        """The non-adversarial path that motivates the guard.

        ``generate_model_integrity`` returns ``artifacts: []`` when handed a
        path that is not a directory (interrupted export, mistyped
        ``final_path``).  Writing that manifest into a real directory must not
        then produce a clean verification.
        """
        from forgelm.compliance import generate_model_integrity
        from forgelm.verify import verify_integrity

        integrity = generate_model_integrity(str(tmp_path / "never_written"))
        assert integrity["artifacts"] == []

        model_dir = tmp_path / "final_model"
        model_dir.mkdir()
        (model_dir / "model_integrity.json").write_text(json.dumps(integrity))

        assert verify_integrity(str(model_dir)).valid is False

    def test_missing_artifacts_key_rejected(self, tmp_path: Path) -> None:
        """A missing key is reported differently from an empty list — different
        cause (not a model_integrity.json vs. a generator that found nothing) —
        but shares the verdict, because neither can compare anything."""
        from forgelm.verify import verify_integrity

        model_dir = tmp_path / "final_model"
        model_dir.mkdir()
        (model_dir / "model_integrity.json").write_text(json.dumps({"verified_at": "2026-01-01"}))

        result = verify_integrity(str(model_dir))
        assert result.valid is False
        assert "no 'artifacts' key" in result.reason

    @pytest.mark.parametrize("root", [[], ["model.safetensors"], "manifest", 42])
    def test_non_object_manifest_root_rejected(self, tmp_path: Path, root) -> None:
        """A non-object root has no ``artifacts`` key to read; ``.get`` was
        short-circuited to ``[]``, so a JSON array masquerading as a manifest
        verified clean on exit 0."""
        from forgelm.verify import verify_integrity

        model_dir = tmp_path / "final_model"
        model_dir.mkdir()
        (model_dir / "model_integrity.json").write_text(json.dumps(root))

        result = verify_integrity(str(model_dir))
        assert result.valid is False
        assert "root" in result.reason

    def test_non_object_artifact_entry_rejected(self, tmp_path: Path) -> None:
        """Non-dict entries used to be skipped silently, so a manifest whose
        every entry was malformed hashed nothing and still exited 0."""
        from forgelm.verify import verify_integrity

        model_dir = tmp_path / "final_model"
        model_dir.mkdir()
        (model_dir / "model_integrity.json").write_text(json.dumps({"artifacts": ["model.safetensors"]}))

        result = verify_integrity(str(model_dir))
        assert result.valid is False
        assert "not an object" in result.reason

    def test_path_traversal_entry_rejected(self, tmp_path: Path) -> None:
        """A manifest entry whose path escapes the model dir (``../secret``)
        must be refused rather than hashing an arbitrary out-of-tree file."""
        from forgelm.verify import verify_integrity

        secret = tmp_path / "secret.txt"
        secret.write_text("top-secret")
        model_dir = tmp_path / "final_model"
        model_dir.mkdir()
        (model_dir / "model_integrity.json").write_text(
            json.dumps({"artifacts": [{"file": "../secret.txt", "sha256": "deadbeef"}]})
        )
        result = verify_integrity(str(model_dir))
        assert result.valid is False
        assert "escapes" in result.reason

    def test_windows_style_manifest_paths_verify_cross_platform(self, tmp_path: Path) -> None:
        """A manifest generated on Windows records ``subdir\\file``; verifying
        it on a POSIX host must not false-positive the file as removed/added
        — both sides normalise separators to forward slashes."""
        from forgelm.compliance import hash_file
        from forgelm.verify import verify_integrity

        model_dir = tmp_path / "final_model"
        sub = model_dir / "weights"
        sub.mkdir(parents=True)
        payload = b"shard-0"
        (sub / "shard0.bin").write_bytes(payload)
        # Hand-craft a manifest with a Windows-style backslash separator.
        hashed = hash_file(str(sub / "shard0.bin"), "weights\\shard0.bin")
        (model_dir / "model_integrity.json").write_text(json.dumps({"artifacts": [hashed]}))

        result = verify_integrity(str(model_dir))
        assert result.valid is True, result.reason
        assert result.removed == []
        assert result.added == []
        assert result.verified_count == 1

    def test_io_error_during_hashing_exits_training_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A genuine runtime I/O failure (PermissionError) during hashing must
        map to EXIT_TRAINING_ERROR (exit 2), not EXIT_CONFIG_ERROR (exit 1).

        Locks the OSError branch at _run_verify_integrity_cmd:243-251 which
        was previously untested (F-M-25).
        """
        from forgelm.cli._exit_codes import EXIT_TRAINING_ERROR
        from forgelm.cli.subcommands._verify_integrity import _run_verify_integrity_cmd

        model_dir = tmp_path / "final_model"
        _write_model_with_integrity(model_dir)

        # Patch the now-public hash_file at the point it is imported inside
        # verify_integrity() — the lazy import resolves from forgelm.compliance,
        # so we patch it there so every caller in this process sees the stub.
        monkeypatch.setattr(
            "forgelm.compliance.hash_file",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("denied")),
        )

        args = _build_args(path=str(model_dir))
        with pytest.raises(SystemExit) as ei:
            _run_verify_integrity_cmd(args, output_format="json")
        assert ei.value.code == EXIT_TRAINING_ERROR


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


# ---------------------------------------------------------------------------
# Wave 2 review — _audit_log_reader.py UTF-8-corruption handling
# ---------------------------------------------------------------------------


class TestAuditLogReaderUtf8Corruption:
    """A non-UTF-8 line must be a controlled, per-line integrity failure
    (skip + count in non-strict mode, ``AuditLogParseError`` in strict
    mode) — not an uncaught ``UnicodeDecodeError`` crashing the generator.
    Pre-fix, ``iter_audit_events`` opened the file in text mode
    (``encoding="utf-8"``) so a corrupted line raised ``UnicodeDecodeError``
    straight out of the ``for`` loop, uncaught by anything in this module
    or by ``_approve.py``'s ``except AuditLogParseError`` decision-guard
    handlers."""

    def test_non_strict_skips_non_utf8_line_and_continues(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._audit_log_reader import iter_audit_events

        path = tmp_path / "audit_log.jsonl"
        path.write_bytes(b'{"event": "a", "run_id": "r1"}\n\xff\xfe not valid utf-8\n{"event": "b", "run_id": "r1"}\n')

        events = [event for _line_no, event in iter_audit_events(str(path), strict=False)]
        assert events == [
            {"event": "a", "run_id": "r1"},
            {"event": "b", "run_id": "r1"},
        ]

    def test_non_strict_logs_skip_count_for_non_utf8_line(self, tmp_path: Path, caplog) -> None:
        from forgelm.cli.subcommands._audit_log_reader import iter_audit_events

        path = tmp_path / "audit_log.jsonl"
        path.write_bytes(b'{"event": "a", "run_id": "r1"}\n\xff\xfe not valid utf-8\n')

        with caplog.at_level("WARNING", logger="forgelm.cli.audit_log_reader"):
            list(iter_audit_events(str(path), strict=False))
        assert any("Skipped 1 malformed line" in rec.message for rec in caplog.records)

    def test_strict_raises_audit_log_parse_error_not_unicode_decode_error(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._audit_log_reader import (
            AuditLogParseError,
            iter_audit_events,
        )

        path = tmp_path / "audit_log.jsonl"
        path.write_bytes(b'{"event": "a", "run_id": "r1"}\n\xff\xfe not valid utf-8\n')

        with pytest.raises(AuditLogParseError) as ei:
            list(iter_audit_events(str(path), strict=True))
        assert ei.value.line_number == 2
        assert "utf-8" in ei.value.reason.lower()
        # AuditLogParseError IS a ValueError but must not itself be a
        # UnicodeDecodeError — approve.py's guards only catch the former.
        assert not isinstance(ei.value, UnicodeDecodeError)

    def test_find_latest_event_for_run_surfaces_controlled_error_on_corruption(self, tmp_path: Path) -> None:
        """The approve / reject decision-guard entry point (strict=True by
        default) must raise the same controlled AuditLogParseError, since
        it is what _approve.py's ``except AuditLogParseError`` handlers
        catch to produce an actionable operator message instead of a bare
        traceback."""
        from forgelm.cli.subcommands._audit_log_reader import (
            AuditLogParseError,
            find_latest_event_for_run,
        )

        path = tmp_path / "audit_log.jsonl"
        path.write_bytes(b"\xff\xfe corrupted line\n")

        with pytest.raises(AuditLogParseError):
            find_latest_event_for_run(str(path), run_id="r1", matches=lambda _e: True)


# ---------------------------------------------------------------------------
# Wave 2 review — `forgelm verify-audit` JSON output support
# ---------------------------------------------------------------------------


class TestVerifyAuditJsonOutput:
    """Pre-fix, ``_run_verify_audit_cmd`` never read ``output_format`` and
    only ever printed plain text, silently ignoring a top-level
    ``--output-format json`` flag and breaking the documented JSON
    contract at ``docs/usermanuals/en/reference/json-output.md``."""

    def _write_valid_chain(self, tmp_path: Path, *, n_events: int = 2) -> Path:
        from forgelm.compliance import AuditLogger

        logger = AuditLogger(str(tmp_path))
        for i in range(n_events):
            logger.log_event(f"test.event.{i}")
        return tmp_path / "audit_log.jsonl"

    def test_valid_chain_emits_documented_json_envelope(self, tmp_path: Path, capsys, monkeypatch) -> None:
        from forgelm.cli._exit_codes import EXIT_SUCCESS
        from forgelm.cli.subcommands._verify_audit import _run_verify_audit_cmd

        monkeypatch.delenv("FORGELM_AUDIT_SECRET", raising=False)
        log_path = self._write_valid_chain(tmp_path)

        args = _build_args(
            log_path=str(log_path),
            hmac_secret_env="FORGELM_AUDIT_SECRET",
            require_hmac=False,
            output_format="json",
        )
        exit_code = _run_verify_audit_cmd(args)
        assert exit_code == EXIT_SUCCESS

        out = capsys.readouterr().out
        payload = json.loads(out)
        # Exact shape per docs/usermanuals/en/reference/json-output.md's
        # "forgelm verify-audit" section.
        assert payload == {
            "success": True,
            "valid": True,
            "entries_count": 2,
            "hmac_verified": None,  # no --hmac-secret-env value configured
            "errors": [],
        }
        assert out.startswith("{\n"), "JSON envelope should use indent=2 like sibling verify-* subcommands"

    def test_hmac_verified_true_when_secret_configured_and_chain_valid(
        self, tmp_path: Path, capsys, monkeypatch
    ) -> None:
        from forgelm.cli.subcommands._verify_audit import _run_verify_audit_cmd

        monkeypatch.setenv("FORGELM_AUDIT_SECRET", "x" * 40)
        log_path = self._write_valid_chain(tmp_path, n_events=1)

        args = _build_args(
            log_path=str(log_path),
            hmac_secret_env="FORGELM_AUDIT_SECRET",
            require_hmac=False,
            output_format="json",
        )
        _run_verify_audit_cmd(args)
        payload = json.loads(capsys.readouterr().out)
        assert payload["hmac_verified"] is True

    def test_tampered_chain_reports_errors_list_and_integrity_failure_exit(
        self, tmp_path: Path, capsys, monkeypatch
    ) -> None:
        """A broken SHA-256 hash chain → exit 6.

        Asserted ``EXIT_CONFIG_ERROR`` before ``EXIT_INTEGRITY_FAILURE``
        existed.  The audit log is an EU AI Act Art. 12 append-only record;
        a chain break is the single strongest tampering signal ForgeLM emits,
        and it shared an exit code with "the path you gave me is wrong"."""
        from forgelm.cli._exit_codes import EXIT_INTEGRITY_FAILURE
        from forgelm.cli.subcommands._verify_audit import _run_verify_audit_cmd

        monkeypatch.delenv("FORGELM_AUDIT_SECRET", raising=False)
        log_path = self._write_valid_chain(tmp_path)

        lines = log_path.read_text(encoding="utf-8").splitlines()
        entry = json.loads(lines[1])
        entry["prev_hash"] = "0" * 64  # break the chain at line 2
        lines[1] = json.dumps(entry)
        log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        args = _build_args(
            log_path=str(log_path),
            hmac_secret_env="FORGELM_AUDIT_SECRET",
            require_hmac=False,
            output_format="json",
        )
        exit_code = _run_verify_audit_cmd(args)
        assert exit_code == EXIT_INTEGRITY_FAILURE

        payload = json.loads(capsys.readouterr().out)
        assert payload["success"] is False
        assert payload["valid"] is False
        assert payload["entries_count"] == 2
        assert len(payload["errors"]) == 1
        assert "line 2" in payload["errors"][0]

    def test_missing_log_file_emits_json_error_envelope(self, tmp_path: Path, capsys) -> None:
        from forgelm.cli._exit_codes import EXIT_CONFIG_ERROR
        from forgelm.cli.subcommands._verify_audit import _run_verify_audit_cmd

        args = _build_args(
            log_path=str(tmp_path / "missing.jsonl"),
            hmac_secret_env="FORGELM_AUDIT_SECRET",
            require_hmac=False,
            output_format="json",
        )
        exit_code = _run_verify_audit_cmd(args)
        assert exit_code == EXIT_CONFIG_ERROR

        payload = json.loads(capsys.readouterr().out)
        assert payload["success"] is False
        assert "audit log not found" in payload["error"]

    def test_text_mode_output_unchanged_when_output_format_absent(self, tmp_path: Path, capsys) -> None:
        """Backward-compat: an ``args`` namespace with no ``output_format``
        attribute at all (the shape produced by the verify-audit subparser
        today, since it registers no ``--output-format`` flag) must still
        print the original plain-text line, not JSON."""
        from forgelm.cli._exit_codes import EXIT_SUCCESS
        from forgelm.cli.subcommands._verify_audit import _run_verify_audit_cmd

        log_path = self._write_valid_chain(tmp_path, n_events=1)
        args = _build_args(
            log_path=str(log_path),
            hmac_secret_env="FORGELM_AUDIT_SECRET",
            require_hmac=False,
        )
        exit_code = _run_verify_audit_cmd(args)
        assert exit_code == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert out.startswith("OK: 1 entries verified")


# ---------------------------------------------------------------------------
# Wave 2 review — verify-annex-iv UnicodeDecodeError + JSON indent hygiene
# ---------------------------------------------------------------------------


class TestVerifyAnnexIvUtf8CorruptionAndJsonIndent:
    def test_non_utf8_file_exits_config_error_not_traceback(self, tmp_path: Path, capsys) -> None:
        from forgelm.cli.subcommands._verify_annex_iv import _run_verify_annex_iv_cmd

        path = tmp_path / "annex_iv.json"
        path.write_bytes(b'{"system_identification": {\xff\xfe not valid utf-8')

        args = _build_args(path=str(path))
        with pytest.raises(SystemExit) as ei:
            _run_verify_annex_iv_cmd(args, output_format="json")
        assert ei.value.code == 1

        payload = json.loads(capsys.readouterr().out)
        assert payload["success"] is False
        assert "utf-8" in payload["error"].lower()

    def test_non_utf8_file_text_mode_does_not_raise(self, tmp_path: Path, capsys) -> None:
        from forgelm.cli.subcommands._verify_annex_iv import _run_verify_annex_iv_cmd

        path = tmp_path / "annex_iv.json"
        path.write_bytes(b"\xff\xfe not valid utf-8 at all")

        args = _build_args(path=str(path))
        with pytest.raises(SystemExit) as ei:
            _run_verify_annex_iv_cmd(args, output_format="text")
        assert ei.value.code == 1

    def test_error_envelope_uses_indent_two_like_success_envelope(self, tmp_path: Path, capsys) -> None:
        """Finding 4: the error envelope was emitted with no indent while
        the success envelope (and sibling verify-gguf/verify-integrity
        copies of this helper) use indent=2 — assert the branches now
        agree."""
        from forgelm.cli.subcommands._verify_annex_iv import _run_verify_annex_iv_cmd

        args = _build_args(path=str(tmp_path / "missing.json"))
        with pytest.raises(SystemExit):
            _run_verify_annex_iv_cmd(args, output_format="json")
        out = capsys.readouterr().out
        assert out.startswith("{\n"), "error envelope must be indent=2 like the success envelope"


# ---------------------------------------------------------------------------
# Wave 2 review — verify-gguf / verify-integrity UnicodeDecodeError handling
# ---------------------------------------------------------------------------


class TestVerifyGgufUtf8Corruption:
    """A non-UTF-8 ``<model>.gguf.sha256`` sidecar must map to the
    documented ``EXIT_CONFIG_ERROR (1)`` with the JSON error envelope, not
    crash with a raw traceback.  Pre-fix, ``_run_verify_gguf_cmd``'s except
    chain caught only ``(FileNotFoundError, IsADirectoryError)`` and
    ``OSError``; ``UnicodeDecodeError`` (a ``ValueError`` subclass) from the
    sidecar text read escaped uncaught."""

    def test_non_utf8_sidecar_exits_config_error_json_envelope(self, tmp_path: Path, capsys, monkeypatch) -> None:
        _stub_metadata_parse(monkeypatch)
        from forgelm.cli.subcommands._verify_gguf import _run_verify_gguf_cmd

        path = tmp_path / "model.gguf"
        _make_minimal_gguf(path)
        # Sidecar with invalid UTF-8 bytes (disk corruption / binary paste).
        (tmp_path / "model.gguf.sha256").write_bytes(b"\xff\xfe not valid utf-8")

        args = _build_args(path=str(path))
        with pytest.raises(SystemExit) as ei:
            _run_verify_gguf_cmd(args, output_format="json")
        assert ei.value.code == 1

        out = capsys.readouterr().out
        payload = json.loads(out)
        assert payload["success"] is False
        assert "utf-8" in payload["error"].lower()
        assert out.startswith("{\n"), "error envelope must be indent=2 like the result envelope"

    def test_non_utf8_sidecar_text_mode_does_not_traceback(self, tmp_path: Path, monkeypatch) -> None:
        _stub_metadata_parse(monkeypatch)
        from forgelm.cli.subcommands._verify_gguf import _run_verify_gguf_cmd

        path = tmp_path / "model.gguf"
        _make_minimal_gguf(path)
        (tmp_path / "model.gguf.sha256").write_bytes(b"\xff\xfe")

        args = _build_args(path=str(path))
        with pytest.raises(SystemExit) as ei:
            _run_verify_gguf_cmd(args, output_format="text")
        assert ei.value.code == 1


class TestVerifyIntegrityUtf8Corruption:
    """A non-UTF-8 ``model_integrity.json`` must map to the documented
    ``EXIT_CONFIG_ERROR (1)`` with the JSON error envelope, not crash with a
    raw traceback.  Pre-fix, ``_run_verify_integrity_cmd``'s except chain
    handled ``FileNotFoundError`` / ``JSONDecodeError`` / ``IsADirectoryError``
    / ``NotADirectoryError`` / ``OSError`` but not ``UnicodeDecodeError``
    (a ``ValueError`` subclass) from the manifest text read."""

    def test_non_utf8_manifest_exits_config_error_json_envelope(self, tmp_path: Path, capsys) -> None:
        from forgelm.cli.subcommands._verify_integrity import _run_verify_integrity_cmd

        model_dir = tmp_path / "final_model"
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / "model.safetensors").write_bytes(b"weights-v1")
        # Corrupt manifest: valid JSON prefix followed by invalid UTF-8 bytes
        # so the failure is the decode, not json parsing.
        (model_dir / "model_integrity.json").write_bytes(b'{"artifacts": [\xff\xfe]}')

        args = _build_args(path=str(model_dir))
        with pytest.raises(SystemExit) as ei:
            _run_verify_integrity_cmd(args, output_format="json")
        assert ei.value.code == 1

        out = capsys.readouterr().out
        payload = json.loads(out)
        assert payload["success"] is False
        assert "utf-8" in payload["error"].lower()
        assert out.startswith("{\n"), "error envelope must be indent=2 like the result envelope"

    def test_non_utf8_manifest_text_mode_does_not_traceback(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._verify_integrity import _run_verify_integrity_cmd

        model_dir = tmp_path / "final_model"
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / "model_integrity.json").write_bytes(b"\xff\xfe not valid utf-8")

        args = _build_args(path=str(model_dir))
        with pytest.raises(SystemExit) as ei:
            _run_verify_integrity_cmd(args, output_format="text")
        assert ei.value.code == 1


# ---------------------------------------------------------------------------
# EXIT_INTEGRITY_FAILURE (6) — "a tampered artifact and a mistyped path are
# not the same incident".
# ---------------------------------------------------------------------------


class TestExitIntegrityFailureContract:
    """The constant itself is part of the public CLI surface."""

    def test_value_is_six(self) -> None:
        from forgelm.cli._exit_codes import EXIT_INTEGRITY_FAILURE

        assert EXIT_INTEGRITY_FAILURE == 6

    def test_listed_in_public_exit_codes(self) -> None:
        """Membership is load-bearing: ``_clamp_exit_code`` coerces anything
        outside ``_PUBLIC_EXIT_CODES`` to ``EXIT_TRAINING_ERROR``, so dropping
        the row would silently rewrite every 6 into a 2 at the dispatch seam."""
        from forgelm.cli._exit_codes import _PUBLIC_EXIT_CODES, EXIT_INTEGRITY_FAILURE

        assert EXIT_INTEGRITY_FAILURE in _PUBLIC_EXIT_CODES

    def test_clamp_passes_six_through_unchanged(self) -> None:
        from forgelm.cli._exit_codes import (
            EXIT_INTEGRITY_FAILURE,
            EXIT_TRAINING_ERROR,
            _clamp_exit_code,
        )

        assert _clamp_exit_code(EXIT_INTEGRITY_FAILURE) == EXIT_INTEGRITY_FAILURE
        assert _clamp_exit_code(EXIT_INTEGRITY_FAILURE) != EXIT_TRAINING_ERROR

    def test_clamp_still_coerces_non_public_codes(self) -> None:
        """Widening the contract to 6 must not accidentally let 130 through."""
        from forgelm.cli._exit_codes import EXIT_TRAINING_ERROR, _clamp_exit_code

        assert _clamp_exit_code(130) == EXIT_TRAINING_ERROR
        assert _clamp_exit_code(7) == EXIT_TRAINING_ERROR

    def test_cli_facade_re_exports_the_constant(self) -> None:
        import forgelm.cli as _cli

        assert _cli.EXIT_INTEGRITY_FAILURE == 6
        assert "EXIT_INTEGRITY_FAILURE" in _cli.__all__


class TestIntegrityFailurePredicates:
    """The three ``is_*_integrity_failure`` predicates own the 1-vs-6 split.

    They are asserted directly (not only through the dispatchers) because they
    are the single point where a future regression could re-merge the two
    incident classes, and because each reads *structured* result fields — a
    reworded ``reason`` string must never move an artefact between exit codes.
    """

    def test_annex_iv_hash_mismatch_is_integrity_failure(self) -> None:
        from forgelm.verify import VerifyAnnexIVResult, is_annex_iv_integrity_failure

        result = VerifyAnnexIVResult(
            valid=False,
            reason="Manifest hash mismatch",
            manifest_hash_actual="a" * 64,
            manifest_hash_expected="b" * 64,
        )
        assert is_annex_iv_integrity_failure(result) is True

    def test_annex_iv_missing_fields_is_not_integrity_failure(self) -> None:
        """An artefact the operator never finished populating is an input
        error — nothing was tampered with."""
        from forgelm.verify import VerifyAnnexIVResult, is_annex_iv_integrity_failure

        result = VerifyAnnexIVResult(valid=False, reason="Missing", missing_fields=["risk_management"])
        assert is_annex_iv_integrity_failure(result) is False

    def test_annex_iv_valid_result_is_not_integrity_failure(self) -> None:
        from forgelm.verify import VerifyAnnexIVResult, is_annex_iv_integrity_failure

        result = VerifyAnnexIVResult(
            valid=True,
            manifest_hash_actual="a" * 64,
            manifest_hash_expected="a" * 64,
        )
        assert is_annex_iv_integrity_failure(result) is False

    def test_annex_iv_non_object_root_is_not_integrity_failure(self) -> None:
        """A JSON root that is a list/string never carried a hash to compare."""
        from forgelm.verify import VerifyAnnexIVResult, is_annex_iv_integrity_failure

        result = VerifyAnnexIVResult(valid=False, reason="Artifact root is list, expected JSON object.")
        assert is_annex_iv_integrity_failure(result) is False

    @pytest.mark.parametrize(
        "checks,expected",
        [
            # Not a GGUF at all → operator pointed at the wrong file.
            ({"magic_ok": False}, False),
            # Real GGUF, metadata block unparseable → corrupted stream.
            ({"magic_ok": True, "sidecar_present": False}, True),
            # Sidecar present but not a hex digest → unusable sidecar.
            ({"magic_ok": True, "sidecar_present": True, "sha256_expected": "TODO"}, False),
            ({"magic_ok": True, "sidecar_present": True, "sha256_expected": ""}, False),
            # Well-formed digest that did not match → modified after export.
            ({"magic_ok": True, "sidecar_present": True, "sha256_expected": "a" * 64}, True),
        ],
    )
    def test_gguf_predicate_walks_the_three_layers(self, checks: dict, expected: bool) -> None:
        from forgelm.verify import VerifyGgufResult, is_gguf_integrity_failure

        assert is_gguf_integrity_failure(VerifyGgufResult(valid=False, checks=checks)) is expected

    def test_gguf_valid_result_is_not_integrity_failure(self) -> None:
        from forgelm.verify import VerifyGgufResult, is_gguf_integrity_failure

        result = VerifyGgufResult(valid=True, checks={"magic_ok": True, "sidecar_match": True})
        assert is_gguf_integrity_failure(result) is False

    @pytest.mark.parametrize(
        "kwargs,expected",
        [
            ({"changed": ["model.safetensors"]}, True),
            ({"removed": ["config.json"]}, True),
            ({"added": ["rogue.bin"]}, True),
            # Manifest unusable → no artifact was ever compared.
            ({}, False),
        ],
    )
    def test_model_integrity_predicate(self, kwargs: dict, expected: bool) -> None:
        from forgelm.verify import VerifyIntegrityResult, is_model_integrity_failure

        result = VerifyIntegrityResult(valid=False, reason="…", **kwargs)
        assert is_model_integrity_failure(result) is expected

    def test_model_integrity_valid_result_is_not_integrity_failure(self) -> None:
        from forgelm.verify import VerifyIntegrityResult, is_model_integrity_failure

        assert is_model_integrity_failure(VerifyIntegrityResult(valid=True, verified_count=2)) is False


class TestTamperVsTypoAreDistinguishable:
    """One pair per ``verify-*`` subcommand: the tampered artifact and the
    mistyped path must produce *different* exit codes.

    Asserting the inequality alongside the absolute values is deliberate — it
    is the property the whole change exists to provide, and it fails loudly if
    someone routes 6 back to 1.
    """

    def test_verify_annex_iv(self, tmp_path: Path) -> None:
        from forgelm.cli._exit_codes import EXIT_CONFIG_ERROR, EXIT_INTEGRITY_FAILURE
        from forgelm.cli.subcommands._verify_annex_iv import _run_verify_annex_iv_cmd

        tampered = tmp_path / "annex_iv.json"
        artifact = _full_annex_iv_artifact()
        artifact["metadata"] = {"manifest_hash": "0" * 64}
        tampered.write_text(json.dumps(artifact))

        with pytest.raises(SystemExit) as tamper:
            _run_verify_annex_iv_cmd(_build_args(path=str(tampered)), output_format="json")
        with pytest.raises(SystemExit) as typo:
            _run_verify_annex_iv_cmd(_build_args(path=str(tmp_path / "typo.json")), output_format="json")

        assert tamper.value.code == EXIT_INTEGRITY_FAILURE
        assert typo.value.code == EXIT_CONFIG_ERROR
        assert tamper.value.code != typo.value.code

    def test_verify_annex_iv_pipeline_mode(self, tmp_path: Path) -> None:
        from forgelm.cli._exit_codes import EXIT_CONFIG_ERROR, EXIT_INTEGRITY_FAILURE
        from forgelm.cli.subcommands._verify_annex_iv import _run_verify_annex_iv_cmd

        run_dir = tmp_path / "run"
        (run_dir / "compliance").mkdir(parents=True)
        # A manifest that parses but whose stage chain was rewritten.
        (run_dir / "compliance" / "pipeline_manifest.json").write_text(
            json.dumps(
                {
                    "forgelm_version": "0.9.0",
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
                            "input_model": "tampered/path",
                            "output_model": "./s1/out",
                        },
                    ],
                }
            )
        )

        with pytest.raises(SystemExit) as tamper:
            _run_verify_annex_iv_cmd(_build_args(path=str(run_dir), pipeline=True), output_format="json")
        with pytest.raises(SystemExit) as typo:
            _run_verify_annex_iv_cmd(
                _build_args(path=str(tmp_path / "no-such-run"), pipeline=True), output_format="json"
            )

        assert tamper.value.code == EXIT_INTEGRITY_FAILURE
        assert typo.value.code == EXIT_CONFIG_ERROR
        assert tamper.value.code != typo.value.code

    def test_verify_gguf(self, tmp_path: Path, monkeypatch) -> None:
        _stub_metadata_parse(monkeypatch)
        from forgelm.cli._exit_codes import EXIT_CONFIG_ERROR, EXIT_INTEGRITY_FAILURE
        from forgelm.cli.subcommands._verify_gguf import _run_verify_gguf_cmd

        model = tmp_path / "model.gguf"
        _make_minimal_gguf(model)
        (tmp_path / "model.gguf.sha256").write_text("0" * 64 + "  model.gguf\n")

        with pytest.raises(SystemExit) as tamper:
            _run_verify_gguf_cmd(_build_args(path=str(model)), output_format="json")
        with pytest.raises(SystemExit) as typo:
            _run_verify_gguf_cmd(_build_args(path=str(tmp_path / "typo.gguf")), output_format="json")

        assert tamper.value.code == EXIT_INTEGRITY_FAILURE
        assert typo.value.code == EXIT_CONFIG_ERROR
        assert tamper.value.code != typo.value.code

    def test_verify_integrity(self, tmp_path: Path) -> None:
        from forgelm.cli._exit_codes import EXIT_CONFIG_ERROR, EXIT_INTEGRITY_FAILURE
        from forgelm.cli.subcommands._verify_integrity import _run_verify_integrity_cmd

        model_dir = tmp_path / "final_model"
        _write_model_with_integrity(model_dir)
        (model_dir / "model.safetensors").write_bytes(b"weights-TAMPERED")

        with pytest.raises(SystemExit) as tamper:
            _run_verify_integrity_cmd(_build_args(path=str(model_dir)), output_format="json")
        with pytest.raises(SystemExit) as typo:
            _run_verify_integrity_cmd(_build_args(path=str(tmp_path / "typo")), output_format="json")

        assert tamper.value.code == EXIT_INTEGRITY_FAILURE
        assert typo.value.code == EXIT_CONFIG_ERROR
        assert tamper.value.code != typo.value.code

    def test_verify_audit(self, tmp_path: Path, monkeypatch) -> None:
        from forgelm.cli._exit_codes import EXIT_CONFIG_ERROR, EXIT_INTEGRITY_FAILURE
        from forgelm.cli.subcommands._verify_audit import _run_verify_audit_cmd
        from forgelm.compliance import AuditLogger

        monkeypatch.delenv("FORGELM_AUDIT_SECRET", raising=False)
        audit_logger = AuditLogger(str(tmp_path))
        audit_logger.log_event("test.event.0")
        audit_logger.log_event("test.event.1")
        log_path = tmp_path / "audit_log.jsonl"

        lines = log_path.read_text(encoding="utf-8").splitlines()
        entry = json.loads(lines[1])
        entry["prev_hash"] = "0" * 64  # break the chain
        lines[1] = json.dumps(entry)
        log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        def _args(path):
            return _build_args(
                log_path=str(path),
                hmac_secret_env="FORGELM_AUDIT_SECRET",
                require_hmac=False,
                output_format="json",
            )

        tamper_code = _run_verify_audit_cmd(_args(log_path))
        typo_code = _run_verify_audit_cmd(_args(tmp_path / "typo.jsonl"))

        assert tamper_code == EXIT_INTEGRITY_FAILURE
        assert typo_code == EXIT_CONFIG_ERROR
        assert tamper_code != typo_code


class TestExitOneStaysOneForGenuineInputErrors:
    """The other half of the split: exits that were 1 *for the right reason*
    must not be swept up into 6 by an over-broad classifier."""

    def test_annex_iv_missing_required_field_stays_config_error(self, tmp_path: Path) -> None:
        from forgelm.cli._exit_codes import EXIT_CONFIG_ERROR
        from forgelm.cli.subcommands._verify_annex_iv import _run_verify_annex_iv_cmd

        artifact = _full_annex_iv_artifact()
        del artifact["risk_management"]
        path = tmp_path / "annex_iv.json"
        path.write_text(json.dumps(artifact))

        with pytest.raises(SystemExit) as ei:
            _run_verify_annex_iv_cmd(_build_args(path=str(path)), output_format="json")
        assert ei.value.code == EXIT_CONFIG_ERROR

    def test_gguf_wrong_magic_stays_config_error(self, tmp_path: Path) -> None:
        """A file that is not a GGUF is a wrong-path verdict, not a tamper
        verdict — there is no artefact of ours to have been modified."""
        from forgelm.cli._exit_codes import EXIT_CONFIG_ERROR
        from forgelm.cli.subcommands._verify_gguf import _run_verify_gguf_cmd

        path = tmp_path / "model.gguf"
        _make_minimal_gguf(path, magic=b"NOPE")
        with pytest.raises(SystemExit) as ei:
            _run_verify_gguf_cmd(_build_args(path=str(path)), output_format="json")
        assert ei.value.code == EXIT_CONFIG_ERROR

    def test_gguf_malformed_sidecar_stays_config_error(self, tmp_path: Path, monkeypatch) -> None:
        """A ``TODO`` placeholder sidecar means nothing was compared."""
        _stub_metadata_parse(monkeypatch)
        from forgelm.cli._exit_codes import EXIT_CONFIG_ERROR
        from forgelm.cli.subcommands._verify_gguf import _run_verify_gguf_cmd

        path = tmp_path / "model.gguf"
        _make_minimal_gguf(path)
        (tmp_path / "model.gguf.sha256").write_text("TODO\n")
        with pytest.raises(SystemExit) as ei:
            _run_verify_gguf_cmd(_build_args(path=str(path)), output_format="json")
        assert ei.value.code == EXIT_CONFIG_ERROR

    def test_gguf_corrupt_metadata_block_is_integrity_failure(self, tmp_path: Path, monkeypatch) -> None:
        """Magic passed, so the file *is* a GGUF; an unparseable metadata
        block is a corrupted/truncated artefact → 6."""
        from forgelm import verify as _verify_mod
        from forgelm.cli._exit_codes import EXIT_INTEGRITY_FAILURE
        from forgelm.cli.subcommands._verify_gguf import _run_verify_gguf_cmd

        monkeypatch.setattr(
            _verify_mod,
            "_maybe_parse_metadata",
            lambda _p: {"parsed": False, "error": "struct.error: unpack requires 8 bytes", "tensor_count": None},
        )
        path = tmp_path / "model.gguf"
        _make_minimal_gguf(path)
        with pytest.raises(SystemExit) as ei:
            _run_verify_gguf_cmd(_build_args(path=str(path)), output_format="json")
        assert ei.value.code == EXIT_INTEGRITY_FAILURE

    @pytest.mark.parametrize(
        "manifest",
        [
            {"artifacts": None},
            {"artifacts": []},
            {"verified_at": "2026-01-01"},
            {"artifacts": ["model.safetensors"]},
            {"artifacts": [{"file": 123, "sha256": "deadbeef"}]},
            {"artifacts": [{"file": "../escape.txt", "sha256": "deadbeef"}]},
        ],
    )
    def test_integrity_unusable_manifest_stays_config_error(self, tmp_path: Path, manifest: dict) -> None:
        """A manifest the verifier cannot use (non-list container, empty
        container, absent container, non-object entry, non-string entry, entry
        escaping the model dir) returns before any artifact is hashed.  There
        is no artifact-level verdict, so reporting 6 would tell CI the weights
        were tampered with when they were never examined."""
        from forgelm.cli._exit_codes import EXIT_CONFIG_ERROR
        from forgelm.cli.subcommands._verify_integrity import _run_verify_integrity_cmd

        model_dir = tmp_path / "final_model"
        model_dir.mkdir()
        (model_dir / "model_integrity.json").write_text(json.dumps(manifest))
        with pytest.raises(SystemExit) as ei:
            _run_verify_integrity_cmd(_build_args(path=str(model_dir)), output_format="json")
        assert ei.value.code == EXIT_CONFIG_ERROR

    def test_verify_audit_require_hmac_without_secret_stays_config_error(self, tmp_path: Path, monkeypatch) -> None:
        from forgelm.cli._exit_codes import EXIT_CONFIG_ERROR
        from forgelm.cli.subcommands._verify_audit import _run_verify_audit_cmd
        from forgelm.compliance import AuditLogger

        monkeypatch.delenv("FORGELM_AUDIT_SECRET", raising=False)
        AuditLogger(str(tmp_path)).log_event("e0")
        args = _build_args(
            log_path=str(tmp_path / "audit_log.jsonl"),
            hmac_secret_env="FORGELM_AUDIT_SECRET",
            require_hmac=True,
            output_format="json",
        )
        assert _run_verify_audit_cmd(args) == EXIT_CONFIG_ERROR

    def test_verify_audit_unreadable_log_maps_to_training_error(self, tmp_path: Path, monkeypatch) -> None:
        """Permission denied on an existing log is a retryable infrastructure
        problem (2), not a tampering signal (6) and not a typo (1)."""
        from forgelm.cli._exit_codes import EXIT_TRAINING_ERROR
        from forgelm.cli.subcommands import _verify_audit as _mod

        log_path = tmp_path / "audit_log.jsonl"
        log_path.write_text("{}\n", encoding="utf-8")

        def _denied(*_args, **_kwargs):
            raise PermissionError("denied")

        # Shadow the builtin in the module's own namespace so the probe's
        # ``open`` call raises deterministically on every platform / uid.
        monkeypatch.setattr(_mod, "open", _denied, raising=False)

        args = _build_args(
            log_path=str(log_path),
            hmac_secret_env="FORGELM_AUDIT_SECRET",
            require_hmac=False,
            output_format="json",
        )
        assert _mod._run_verify_audit_cmd(args) == EXIT_TRAINING_ERROR

    def test_verify_audit_non_utf8_log_is_integrity_failure(self, tmp_path: Path, monkeypatch) -> None:
        """Non-UTF-8 bytes inside an append-only Art. 12 record are corruption
        of the log itself — the file opened fine, so this is 6, not 1."""
        from forgelm.cli._exit_codes import EXIT_INTEGRITY_FAILURE
        from forgelm.cli.subcommands._verify_audit import _run_verify_audit_cmd

        monkeypatch.delenv("FORGELM_AUDIT_SECRET", raising=False)
        log_path = tmp_path / "audit_log.jsonl"
        log_path.write_bytes(b'{"event": "a"}\n\xff\xfe not utf-8\n')

        args = _build_args(
            log_path=str(log_path),
            hmac_secret_env="FORGELM_AUDIT_SECRET",
            require_hmac=False,
            output_format="json",
        )
        assert _run_verify_audit_cmd(args) == EXIT_INTEGRITY_FAILURE


# ---------------------------------------------------------------------------
# forgelm/verify.py extraction — the stable symbols must keep working from
# ``forgelm`` directly, and must no longer live behind the CLI package.
# ---------------------------------------------------------------------------


class TestVerifyModuleExtraction:
    _SYMBOLS = (
        "verify_annex_iv_artifact",
        "VerifyAnnexIVResult",
        "verify_gguf",
        "VerifyGgufResult",
        "verify_integrity",
        "VerifyIntegrityResult",
    )

    @pytest.mark.parametrize("name", _SYMBOLS)
    def test_symbol_importable_from_package_root(self, name: str) -> None:
        import forgelm

        assert hasattr(forgelm, name), f"forgelm.{name} must resolve via the lazy facade"
        assert name in forgelm.__all__

    @pytest.mark.parametrize("name", _SYMBOLS)
    def test_facade_resolves_to_forgelm_verify(self, name: str) -> None:
        """Identity, not just presence: the facade must hand back the object
        defined in ``forgelm.verify``, so a stale lazy-import row pointing at
        the old ``cli.subcommands`` module fails here."""
        import forgelm
        import forgelm.verify as _verify_mod

        assert getattr(forgelm, name) is getattr(_verify_mod, name)

    @pytest.mark.parametrize("name", _SYMBOLS)
    def test_lazy_symbol_table_points_at_forgelm_verify(self, name: str) -> None:
        import forgelm

        module_path, attr = forgelm._LAZY_SYMBOLS[name]
        assert module_path == "forgelm.verify"
        assert attr == name

    @pytest.mark.parametrize("name", _SYMBOLS)
    def test_cli_subcommand_modules_still_re_export_the_same_objects(self, name: str) -> None:
        """The CLI subcommands are thin wrappers now, but their historic
        attribute surface (which ``forgelm.cli`` re-exports and existing tests
        import) must still resolve — and to the *same* object."""
        import forgelm.verify as _verify_mod
        from forgelm import cli as _cli

        assert getattr(_cli, name) is getattr(_verify_mod, name)

    def test_verify_integrity_behaves_identically_through_the_facade(self, tmp_path: Path) -> None:
        """Behavioural equivalence, not just import equivalence."""
        import forgelm

        model_dir = tmp_path / "final_model"
        _write_model_with_integrity(model_dir)

        ok = forgelm.verify_integrity(str(model_dir))
        assert ok.valid is True
        assert ok.verified_count == 2
        assert isinstance(ok, forgelm.VerifyIntegrityResult)

        (model_dir / "model.safetensors").write_bytes(b"weights-TAMPERED")
        tampered = forgelm.verify_integrity(str(model_dir))
        assert tampered.valid is False
        assert "model.safetensors" in tampered.changed

    def test_verify_gguf_behaves_identically_through_the_facade(self, tmp_path: Path, monkeypatch) -> None:
        _stub_metadata_parse(monkeypatch)
        import forgelm

        path = tmp_path / "model.gguf"
        _make_minimal_gguf(path)
        result = forgelm.verify_gguf(str(path))
        assert result.valid is True
        assert result.checks["magic_ok"] is True
        assert isinstance(result, forgelm.VerifyGgufResult)

    def test_verify_annex_iv_behaves_identically_through_the_facade(self, tmp_path: Path) -> None:
        import forgelm

        path = tmp_path / "annex_iv.json"
        path.write_text(json.dumps(_full_annex_iv_artifact()))
        result = forgelm.verify_annex_iv_artifact(str(path))
        assert result.valid is True
        assert result.missing_fields == []
        assert isinstance(result, forgelm.VerifyAnnexIVResult)

    def test_verify_audit_log_deliberately_stayed_in_compliance(self) -> None:
        """Documented decision, pinned so a later "tidy-up" cannot move it
        without someone re-reading the rationale in ``forgelm/verify.py``'s
        module docstring: the audit-log verifier must sit next to
        ``AuditLogger``, whose on-disk canonicalisation it mirrors byte-for-byte.
        """
        import forgelm

        assert forgelm._LAZY_SYMBOLS["verify_audit_log"] == ("forgelm.compliance", "verify_audit_log")
        assert forgelm._LAZY_SYMBOLS["VerifyResult"] == ("forgelm.compliance", "VerifyResult")

    def test_verify_module_size_is_governed_by_the_module_size_guard(self, monkeypatch) -> None:
        """architecture.md sets a ~1000 code-line sub-package-split trigger.

        ``forgelm/verify.py`` crossed it on 2026-07-20 wiring the audit-log
        corroboration outcome into ``PipelineEvidenceReport``, and took a
        deferred-split entry in ``tools/check_module_size.py`` rather than a
        rushed split (the split moves the exit-code routing tokens that the
        CLI and these tests both pin).  A bare ``< 1000`` here would now be a
        second, *silently divergent* budget for the same file: this asserts
        against the guard's recorded budget so there is exactly one number,
        and growth past it still fails — in the guard, where the raise
        requires a reviewed ``budget_history`` note.
        """
        from pathlib import Path as _Path

        tool = _load_module_size_tool(monkeypatch)
        entry = tool._DEFERRED_SPLITS["forgelm/verify.py"]
        loc = tool._count_code_lines(_Path(forgelm_verify_path()))
        assert loc <= entry.budget, (
            f"forgelm/verify.py is {loc} code lines, past its recorded budget of {entry.budget} — "
            "split it, or raise the budget in tools/check_module_size.py with a budget_history note"
        )


def _load_module_size_tool(monkeypatch):
    """Import ``tools/check_module_size.py`` as a module.

    The module MUST be registered in ``sys.modules`` under its own name before
    ``exec_module``: ``@dataclass`` resolves string annotations through
    ``sys.modules[cls.__module__]``, which is ``None`` for an unregistered
    file-loaded module.  ``monkeypatch.setitem`` rather than a bare assignment
    so the entry is removed at teardown — a stray ``check_module_size`` left
    in ``sys.modules`` is exactly the cross-test pollution
    ``tools/check_no_unguarded_sys_modules_pop.py`` exists to prevent.
    """
    import importlib.util
    import sys as _sys
    from pathlib import Path as _Path

    path = _Path(__file__).resolve().parents[1] / "tools" / "check_module_size.py"
    spec = importlib.util.spec_from_file_location("check_module_size", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(_sys.modules, "check_module_size", module)
    spec.loader.exec_module(module)
    return module


def forgelm_verify_path() -> str:
    import forgelm.verify as _verify_mod

    return _verify_mod.__file__ or ""


# ---------------------------------------------------------------------------
# verify-audit: the structural predicate, the readability probe, and the
# single exit-decision point (F-4 / D1-09 / T-02 / F1 / F2 / T-05).
# ---------------------------------------------------------------------------


def _write_chained_log(tmp_path: Path, monkeypatch) -> Path:
    """Write a two-entry audit log with a broken hash chain on line 2."""
    from forgelm.compliance import AuditLogger

    monkeypatch.delenv("FORGELM_AUDIT_SECRET", raising=False)
    audit_logger = AuditLogger(str(tmp_path))
    audit_logger.log_event("test.event.0")
    audit_logger.log_event("test.event.1")
    log_path = tmp_path / "audit_log.jsonl"

    lines = log_path.read_text(encoding="utf-8").splitlines()
    entry = json.loads(lines[1])
    entry["prev_hash"] = "0" * 64
    lines[1] = json.dumps(entry)
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return log_path


def _audit_args(path, output_format: str = "json"):
    return _build_args(
        log_path=str(path),
        hmac_secret_env="FORGELM_AUDIT_SECRET",
        require_hmac=False,
        output_format=output_format,
    )


class TestAuditIntegrityPredicate:
    """``verify-audit`` was the one verifier with no structural predicate: it
    blanket-mapped every ``valid=False`` to exit 6 and leaned entirely on the
    readability probe having pre-caught the non-integrity cases.  It now has
    ``is_audit_integrity_failure``, keyed off the ``AUDIT_FAILURE_*``
    classification, consistent with its three siblings (F-4 / D1-09)."""

    @pytest.mark.parametrize(
        "kind_attr,expected",
        [
            ("AUDIT_FAILURE_INTEGRITY", True),
            ("AUDIT_FAILURE_ENCODING", True),
            ("AUDIT_FAILURE_NOT_FOUND", False),
            ("AUDIT_FAILURE_UNREADABLE", False),
            ("AUDIT_FAILURE_USAGE", False),
        ],
    )
    def test_predicate_splits_on_failure_kind(self, kind_attr: str, expected: bool) -> None:
        from forgelm import compliance as _compliance
        from forgelm.verify import is_audit_integrity_failure

        assert is_audit_integrity_failure(getattr(_compliance, kind_attr)) is expected

    def test_passing_verification_is_never_an_integrity_failure(self) -> None:
        """A clean run classifies as ``None``, which must not route to 6."""
        from forgelm.verify import is_audit_integrity_failure

        assert is_audit_integrity_failure(None) is False

    def test_classification_stays_off_the_public_result_type(self) -> None:
        """``VerifyResult`` is stable-tier public API and its field roster is
        pinned by ``tests/_data/api_signatures_<ver>.json``.  The routing token
        rides beside the result precisely so this internal need does not spend
        an ``__api_version__`` bump — assert the surface really is unchanged."""
        import dataclasses

        from forgelm.compliance import VerifyResult

        assert [f.name for f in dataclasses.fields(VerifyResult)] == [
            "valid",
            "entries_count",
            "first_invalid_index",
            "reason",
        ]

    def test_untagged_failure_defaults_to_integrity(self, tmp_path: Path, monkeypatch) -> None:
        """Every chain-walk / HMAC / manifest failure is tagged in one place
        rather than at each ``return``; the default must keep them on 6."""
        from forgelm.compliance import AUDIT_FAILURE_INTEGRITY, _verify_audit_log_classified

        log_path = _write_chained_log(tmp_path, monkeypatch)
        result, kind = _verify_audit_log_classified(str(log_path))
        assert result.valid is False
        assert kind == AUDIT_FAILURE_INTEGRITY

    def test_passing_run_is_classified_none(self, tmp_path: Path, monkeypatch) -> None:
        from forgelm.compliance import AuditLogger, _verify_audit_log_classified

        monkeypatch.delenv("FORGELM_AUDIT_SECRET", raising=False)
        AuditLogger(str(tmp_path)).log_event("e0")
        result, kind = _verify_audit_log_classified(str(tmp_path / "audit_log.jsonl"))
        assert result.valid is True
        assert kind is None

    def test_missing_log_is_classified_not_found(self, tmp_path: Path) -> None:
        from forgelm.compliance import AUDIT_FAILURE_NOT_FOUND, _verify_audit_log_classified

        _result, kind = _verify_audit_log_classified(str(tmp_path / "nope.jsonl"))
        assert kind == AUDIT_FAILURE_NOT_FOUND

    def test_usage_error_is_classified_usage_not_integrity(self, tmp_path: Path, monkeypatch) -> None:
        """``require_hmac`` without a secret never looked at the chain."""
        from forgelm.compliance import (
            AUDIT_FAILURE_USAGE,
            AuditLogger,
            _verify_audit_log_classified,
        )

        monkeypatch.delenv("FORGELM_AUDIT_SECRET", raising=False)
        AuditLogger(str(tmp_path)).log_event("e0")
        result, kind = _verify_audit_log_classified(str(tmp_path / "audit_log.jsonl"), require_hmac=True)
        assert result.valid is False
        assert kind == AUDIT_FAILURE_USAGE

    def test_public_wrapper_still_returns_the_bare_result(self, tmp_path: Path, monkeypatch) -> None:
        """``verify_audit_log`` keeps its documented contract: one
        ``VerifyResult``, no tuple, for every existing library caller."""
        from forgelm.compliance import VerifyResult, verify_audit_log

        log_path = _write_chained_log(tmp_path, monkeypatch)
        result = verify_audit_log(str(log_path))
        assert isinstance(result, VerifyResult)
        assert result.valid is False


class TestVerifyAuditExitCodeIsDecidedOnce:
    """Both output branches used to decide the exit code independently, and
    every test asserting a chain break returns 6 passed ``output_format="json"``
    — so the default *text* branch had no coverage at all and could be mutated
    from 6 to 1 with the whole suite still green (T-02)."""

    @pytest.mark.parametrize("output_format", ["text", "json"])
    def test_chain_break_exits_integrity_failure_in_both_formats(
        self, tmp_path: Path, monkeypatch, output_format: str
    ) -> None:
        from forgelm.cli._exit_codes import EXIT_INTEGRITY_FAILURE
        from forgelm.cli.subcommands._verify_audit import _run_verify_audit_cmd

        log_path = _write_chained_log(tmp_path, monkeypatch)
        assert _run_verify_audit_cmd(_audit_args(log_path, output_format)) == EXIT_INTEGRITY_FAILURE

    @pytest.mark.parametrize("output_format", ["text", "json"])
    def test_missing_log_exits_config_error_in_both_formats(
        self, tmp_path: Path, monkeypatch, output_format: str
    ) -> None:
        from forgelm.cli._exit_codes import EXIT_CONFIG_ERROR
        from forgelm.cli.subcommands._verify_audit import _run_verify_audit_cmd

        monkeypatch.delenv("FORGELM_AUDIT_SECRET", raising=False)
        args = _audit_args(tmp_path / "typo.jsonl", output_format)
        assert _run_verify_audit_cmd(args) == EXIT_CONFIG_ERROR

    @pytest.mark.parametrize("output_format", ["text", "json"])
    def test_clean_log_exits_success_in_both_formats(self, tmp_path: Path, monkeypatch, output_format: str) -> None:
        from forgelm.cli._exit_codes import EXIT_SUCCESS
        from forgelm.cli.subcommands._verify_audit import _run_verify_audit_cmd
        from forgelm.compliance import AuditLogger

        monkeypatch.delenv("FORGELM_AUDIT_SECRET", raising=False)
        AuditLogger(str(tmp_path)).log_event("e0")
        args = _audit_args(tmp_path / "audit_log.jsonl", output_format)
        assert _run_verify_audit_cmd(args) == EXIT_SUCCESS

    @pytest.mark.parametrize("output_format", ["text", "json"])
    def test_empty_log_without_manifest_exits_config_error_not_success(
        self, tmp_path: Path, monkeypatch, output_format: str
    ) -> None:
        """The fifth fail-open: a zero-entry log used to print
        "OK: 0 entries verified" and exit 0 in both formats.

        Exit 0 is what an operator's CI reads as "the Article 12 audit log
        is intact", and it was being returned after zero comparisons.  It is
        now 1 — the same code an absent log returns, because "there is no
        audit log to verify here" is the same operator situation.
        """
        from forgelm.cli._exit_codes import EXIT_CONFIG_ERROR
        from forgelm.cli.subcommands._verify_audit import _run_verify_audit_cmd

        monkeypatch.delenv("FORGELM_AUDIT_SECRET", raising=False)
        log_path = tmp_path / "audit_log.jsonl"
        log_path.touch()
        assert _run_verify_audit_cmd(_audit_args(log_path, output_format)) == EXIT_CONFIG_ERROR

    def test_empty_log_message_names_the_situation(self, tmp_path: Path, monkeypatch, capsys) -> None:
        """Failing is half the fix; the operator has to be told *which* empty
        they have.  The message must distinguish "truncated with a manifest
        pinning what is gone" from "blank with nothing to compare against"."""
        from forgelm.cli.subcommands._verify_audit import _run_verify_audit_cmd

        monkeypatch.delenv("FORGELM_AUDIT_SECRET", raising=False)
        log_path = tmp_path / "audit_log.jsonl"
        log_path.touch()
        _run_verify_audit_cmd(_audit_args(log_path, "text"))
        err = capsys.readouterr().err
        assert "0 entries" in err
        assert "manifest" in err
        # And it must not be mistakable for the success line.
        assert "OK:" not in err

    def test_truncated_log_with_manifest_still_exits_integrity_failure(self, tmp_path: Path, monkeypatch) -> None:
        """Non-regression for F-P4-OPUS-01, which the empty-log change had to
        route around rather than through.

        A manifest pinning a real first entry *is* a baseline, so zero entries
        against it is a comparison that ran and failed → 6, not the 1 its
        manifest-less sibling gets.  Both are ``valid=False``; the whole point
        of the split is that they are not the same event.
        """
        from forgelm.cli._exit_codes import EXIT_INTEGRITY_FAILURE
        from forgelm.cli.subcommands._verify_audit import _run_verify_audit_cmd
        from forgelm.compliance import AUDIT_FAILURE_INTEGRITY, AuditLogger, _verify_audit_log_classified

        monkeypatch.delenv("FORGELM_AUDIT_SECRET", raising=False)
        AuditLogger(str(tmp_path)).log_event("e0")
        log_path = tmp_path / "audit_log.jsonl"
        assert (tmp_path / "audit_log.jsonl.manifest.json").is_file()
        with open(log_path, "w", encoding="utf-8"):  # truncate, leave manifest
            pass

        _result, kind = _verify_audit_log_classified(str(log_path))
        assert kind == AUDIT_FAILURE_INTEGRITY
        assert _run_verify_audit_cmd(_audit_args(log_path, "text")) == EXIT_INTEGRITY_FAILURE

    def test_the_two_output_formats_cannot_disagree(self, tmp_path: Path, monkeypatch) -> None:
        """The property the duplication violated, asserted directly."""
        from forgelm.cli.subcommands._verify_audit import _run_verify_audit_cmd

        log_path = _write_chained_log(tmp_path, monkeypatch)
        assert _run_verify_audit_cmd(_audit_args(log_path, "text")) == _run_verify_audit_cmd(
            _audit_args(log_path, "json")
        )

    def test_mid_read_io_failure_exits_training_error_not_integrity(self, tmp_path: Path, monkeypatch) -> None:
        """The probe reads byte 1; it says nothing about byte 10,000,000 (F2).
        The guarantee therefore has to live in the verdict, not the probe: an
        ``OSError`` raised *inside* the verifier comes back tagged
        ``AUDIT_FAILURE_UNREADABLE`` and routes to 2, not to a chain break."""
        from forgelm.cli._exit_codes import EXIT_TRAINING_ERROR
        from forgelm.cli.subcommands._verify_audit import _run_verify_audit_cmd
        from forgelm.compliance import AuditLogger

        monkeypatch.delenv("FORGELM_AUDIT_SECRET", raising=False)
        AuditLogger(str(tmp_path)).log_event("e0")
        log_path = tmp_path / "audit_log.jsonl"

        def _fail_mid_read(*_args, **_kwargs):
            raise OSError("Input/output error")

        # Shadow ``open`` in the *verifier's* namespace only, so the CLI
        # probe still succeeds and the failure genuinely originates inside
        # the verifier — which is the case the docstring claims to cover.
        import forgelm.compliance as _compliance

        monkeypatch.setattr(_compliance, "open", _fail_mid_read, raising=False)

        assert _run_verify_audit_cmd(_audit_args(log_path)) == EXIT_TRAINING_ERROR


class TestVerifyAuditProbeRejectsNonRegularFiles:
    """``open()`` succeeds on FIFOs, character devices and sockets, so the
    open-and-read-one-byte probe let them through: a FIFO blocked forever and
    ``/dev/zero`` reached the verifier, which reported "not found" — mapped, by
    the old blanket rule, to exit 6 (F1)."""

    @pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFOs are POSIX-only")
    def test_fifo_exits_config_error_without_hanging(self, tmp_path: Path, monkeypatch) -> None:
        from forgelm.cli._exit_codes import EXIT_CONFIG_ERROR
        from forgelm.cli.subcommands._verify_audit import _run_verify_audit_cmd

        monkeypatch.delenv("FORGELM_AUDIT_SECRET", raising=False)
        fifo = tmp_path / "audit_log.jsonl"
        os.mkfifo(fifo)

        # A hang is worse than a wrong exit code: an unattended CI job waits
        # for a writer that never comes.  SIGALRM turns "blocks forever" into
        # a deterministic failure instead of a stuck test run.
        def _timed_out(*_a):
            raise AssertionError("verify-audit blocked on a FIFO — the probe opened a non-regular file")

        previous = signal.signal(signal.SIGALRM, _timed_out)
        signal.alarm(5)
        try:
            assert _run_verify_audit_cmd(_audit_args(fifo)) == EXIT_CONFIG_ERROR
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous)

    @pytest.mark.skipif(not os.path.exists("/dev/zero"), reason="requires /dev/zero")
    def test_character_device_is_not_reported_as_tampering(self, monkeypatch) -> None:
        from forgelm.cli._exit_codes import EXIT_CONFIG_ERROR, EXIT_INTEGRITY_FAILURE
        from forgelm.cli.subcommands._verify_audit import _run_verify_audit_cmd

        monkeypatch.delenv("FORGELM_AUDIT_SECRET", raising=False)
        code = _run_verify_audit_cmd(_audit_args("/dev/zero", "text"))
        assert code == EXIT_CONFIG_ERROR
        assert code != EXIT_INTEGRITY_FAILURE

    def test_library_verifier_also_refuses_a_fifo(self, tmp_path: Path) -> None:
        """Defence in depth: the CLI probe is not the only caller.  The
        library path must not block either — ``_read_audit_log_lines``'
        ``os.path.isfile`` guard is what stops it, so pin that it is."""
        if not hasattr(os, "mkfifo"):
            pytest.skip("FIFOs are POSIX-only")
        from forgelm.compliance import AUDIT_FAILURE_NOT_FOUND, _verify_audit_log_classified

        fifo = tmp_path / "audit_log.jsonl"
        os.mkfifo(fifo)

        def _timed_out(*_a):
            raise AssertionError("verify_audit_log blocked on a FIFO")

        previous = signal.signal(signal.SIGALRM, _timed_out)
        signal.alarm(5)
        try:
            _result, kind = _verify_audit_log_classified(str(fifo))
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous)
        assert kind == AUDIT_FAILURE_NOT_FOUND


class TestVerifyAuditProbeRationaleIsPinned:
    """The two refinements in ``_probe_log_readable`` that carry explicit
    docstring rationale but were pinned by no test (T-05)."""

    def test_directory_verdict_is_platform_uniform(self, tmp_path: Path, monkeypatch, capsys) -> None:
        """POSIX raises ``IsADirectoryError`` opening a directory; Windows
        raises ``PermissionError``, which the ``OSError`` branch would map to
        exit 2.  The verdict is decided from ``st_mode`` instead, so the answer
        must be 1 even when ``open`` behaves the Windows way.

        Asserts the *message* too, not only the code: the generic
        non-regular-file branch below already yields exit 1 for a directory, so
        code alone would not notice the dedicated branch disappearing — and
        "path is a directory" is the actionable half of the answer."""
        from forgelm.cli._exit_codes import EXIT_CONFIG_ERROR
        from forgelm.cli.subcommands import _verify_audit as _mod

        monkeypatch.delenv("FORGELM_AUDIT_SECRET", raising=False)
        a_directory = tmp_path / "logs"
        a_directory.mkdir()

        def _windows_style(*_args, **_kwargs):
            raise PermissionError("Permission denied")

        monkeypatch.setattr(_mod, "open", _windows_style, raising=False)
        assert _mod._run_verify_audit_cmd(_audit_args(a_directory)) == EXIT_CONFIG_ERROR
        assert "path is a directory" in json.loads(capsys.readouterr().out)["error"]

    def test_specific_oserror_subclasses_are_caught_before_the_catch_all(self, tmp_path: Path, monkeypatch) -> None:
        """``FileNotFoundError`` and ``NotADirectoryError`` are ``OSError``
        subclasses: if the generic branch were ordered first it would swallow
        them and a mistyped path would exit 2 (retry me) instead of 1 (fix your
        command).  Ordering is behaviour, so it gets a test."""
        from forgelm.cli._exit_codes import EXIT_CONFIG_ERROR, EXIT_TRAINING_ERROR
        from forgelm.cli.subcommands import _verify_audit as _mod

        monkeypatch.delenv("FORGELM_AUDIT_SECRET", raising=False)
        a_file = tmp_path / "audit_log.jsonl"
        a_file.write_text("{}\n", encoding="utf-8")

        # A path component that is a file, not a directory → NotADirectoryError.
        nested = a_file / "audit_log.jsonl"
        assert _mod._run_verify_audit_cmd(_audit_args(nested)) == EXIT_CONFIG_ERROR
        assert _mod._run_verify_audit_cmd(_audit_args(tmp_path / "absent.jsonl")) == EXIT_CONFIG_ERROR

        # …while a genuine reachability failure still reaches the catch-all.
        def _denied(*_args, **_kwargs):
            raise PermissionError("denied")

        monkeypatch.setattr(_mod.os, "stat", _denied)
        assert _mod._run_verify_audit_cmd(_audit_args(a_file)) == EXIT_TRAINING_ERROR


# ---------------------------------------------------------------------------
# verify-gguf: a metadata-parse error must not outrank the checksum (D1-07)
# ---------------------------------------------------------------------------


def _stub_metadata_error(monkeypatch, message: str = "struct.error: unpack requires 8 bytes") -> None:
    from forgelm import verify as _verify_mod

    monkeypatch.setattr(
        _verify_mod,
        "_maybe_parse_metadata",
        lambda _p: {"parsed": False, "error": message, "tensor_count": None},
    )


class TestGgufMetadataErrorDoesNotOutrankTheChecksum:
    """The metadata-parse error used to return *before* the SHA-256 sidecar
    comparison, so a ``gguf`` package too old to read a file's format revision
    was reported as an integrity failure — exit 6, "page the artefact owner" —
    even when the checksum proved the file was byte-identical to what was
    exported (D1-07)."""

    def test_matching_sidecar_downgrades_a_parse_error_to_config_error(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        from forgelm.cli._exit_codes import EXIT_CONFIG_ERROR, EXIT_INTEGRITY_FAILURE
        from forgelm.cli.subcommands._verify_gguf import _run_verify_gguf_cmd

        _stub_metadata_error(monkeypatch)
        path = tmp_path / "model.gguf"
        _make_minimal_gguf(path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        (tmp_path / "model.gguf.sha256").write_text(f"{digest}  model.gguf\n")

        with pytest.raises(SystemExit) as ei:
            _run_verify_gguf_cmd(_build_args(path=str(path)), output_format="json")
        assert ei.value.code == EXIT_CONFIG_ERROR
        assert ei.value.code != EXIT_INTEGRITY_FAILURE

        payload = json.loads(capsys.readouterr().out)
        # The comparison must actually have run — the whole point is that the
        # verifier no longer returns before reaching it.
        assert payload["checks"]["sidecar_match"] is True
        assert payload["checks"]["metadata_error"]
        assert "byte-identical" in payload["reason"]

    def test_mismatching_sidecar_keeps_a_parse_error_at_integrity_failure(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """The mirror case: reordering must not let a genuinely corrupt file
        slip to a softer code.  A checksum that disagrees is the strongest
        evidence available and it dominates."""
        from forgelm.cli._exit_codes import EXIT_INTEGRITY_FAILURE
        from forgelm.cli.subcommands._verify_gguf import _run_verify_gguf_cmd

        _stub_metadata_error(monkeypatch)
        path = tmp_path / "model.gguf"
        _make_minimal_gguf(path)
        (tmp_path / "model.gguf.sha256").write_text("0" * 64 + "  model.gguf\n")

        with pytest.raises(SystemExit) as ei:
            _run_verify_gguf_cmd(_build_args(path=str(path)), output_format="json")
        assert ei.value.code == EXIT_INTEGRITY_FAILURE
        assert "sha-256" in json.loads(capsys.readouterr().out)["reason"].lower()

    def test_no_sidecar_keeps_a_parse_error_at_integrity_failure(self, tmp_path: Path, monkeypatch) -> None:
        """Nothing available to rule out corruption → still a tamper verdict."""
        from forgelm.cli._exit_codes import EXIT_INTEGRITY_FAILURE
        from forgelm.cli.subcommands._verify_gguf import _run_verify_gguf_cmd

        _stub_metadata_error(monkeypatch)
        path = tmp_path / "model.gguf"
        _make_minimal_gguf(path)

        with pytest.raises(SystemExit) as ei:
            _run_verify_gguf_cmd(_build_args(path=str(path)), output_format="json")
        assert ei.value.code == EXIT_INTEGRITY_FAILURE

    def test_malformed_sidecar_keeps_a_parse_error_at_config_error(self, tmp_path: Path, monkeypatch) -> None:
        """A ``TODO`` sidecar compared nothing, so it cannot rescue *or* damn
        the file; the unusable-sidecar verdict (1) stands."""
        from forgelm.cli._exit_codes import EXIT_CONFIG_ERROR
        from forgelm.cli.subcommands._verify_gguf import _run_verify_gguf_cmd

        _stub_metadata_error(monkeypatch)
        path = tmp_path / "model.gguf"
        _make_minimal_gguf(path)
        (tmp_path / "model.gguf.sha256").write_text("TODO\n")

        with pytest.raises(SystemExit) as ei:
            _run_verify_gguf_cmd(_build_args(path=str(path)), output_format="json")
        assert ei.value.code == EXIT_CONFIG_ERROR

    def test_predicate_requires_a_positively_recorded_match(self) -> None:
        """Fail closed: only a recorded ``sidecar_match is True`` earns the
        softer code.  An incomplete result stays on the tamper verdict."""
        from forgelm.verify import VerifyGgufResult, is_gguf_integrity_failure

        base = {"magic_ok": True, "sidecar_present": True, "sha256_expected": "a" * 64}
        assert is_gguf_integrity_failure(VerifyGgufResult(valid=False, checks={**base, "sidecar_match": True})) is False
        assert is_gguf_integrity_failure(VerifyGgufResult(valid=False, checks={**base, "sidecar_match": False})) is True
        assert is_gguf_integrity_failure(VerifyGgufResult(valid=False, checks=base)) is True


# ---------------------------------------------------------------------------
# verify-annex-iv --pipeline: a non-UTF-8 manifest (D1-08)
# ---------------------------------------------------------------------------


class TestPipelineManifestNonUtf8:
    """``verify_pipeline_manifest_at_path`` caught ``json.JSONDecodeError`` and
    ``OSError`` but not ``UnicodeDecodeError`` — a ``ValueError`` subclass that
    is neither — and ``_run_pipeline_mode`` guarded only ``OSError``.  All three
    sibling single-artefact paths carry an explicit branch, so a non-UTF-8
    pipeline manifest escaped as an unhandled traceback (D1-08)."""

    @staticmethod
    def _write_binary_manifest(tmp_path: Path) -> Path:
        run_dir = tmp_path / "run"
        (run_dir / "compliance").mkdir(parents=True)
        (run_dir / "compliance" / "pipeline_manifest.json").write_bytes(b'{"stages": \xff\xfe}')
        return run_dir

    def test_library_returns_a_tagged_input_error_violation(self, tmp_path: Path) -> None:
        from forgelm.compliance import (
            PIPELINE_MANIFEST_INPUT_ERROR_PREFIX,
            verify_pipeline_manifest_at_path,
        )

        run_dir = self._write_binary_manifest(tmp_path)
        violations = verify_pipeline_manifest_at_path(str(run_dir))
        assert len(violations) == 1
        assert violations[0].startswith(PIPELINE_MANIFEST_INPUT_ERROR_PREFIX)
        assert "UTF-8" in violations[0]

    def test_cli_exits_config_error_rather_than_tracebacking(self, tmp_path: Path, capsys) -> None:
        from forgelm.cli._exit_codes import EXIT_CONFIG_ERROR
        from forgelm.cli.subcommands._verify_annex_iv import _run_verify_annex_iv_cmd

        run_dir = self._write_binary_manifest(tmp_path)
        with pytest.raises(SystemExit) as ei:
            _run_verify_annex_iv_cmd(_build_args(path=str(run_dir), pipeline=True), output_format="json")
        assert ei.value.code == EXIT_CONFIG_ERROR
        payload = json.loads(capsys.readouterr().out)
        assert payload["success"] is False
        # The routing token is internal and must never reach the operator.
        assert not any(v.startswith("INPUT_ERROR::") for v in payload["violations"])

    def test_dispatcher_guard_also_covers_a_bubbled_unicode_error(self, tmp_path: Path, monkeypatch, capsys) -> None:
        """The defensive ``try`` around the verifier: if a future change there
        lets a ``UnicodeDecodeError`` bubble, it must still land on 1, not on a
        raw traceback and not on the ``OSError`` branch's exit 2.

        The seam is ``forgelm.verify.verify_pipeline_manifest_report`` — what
        ``_run_pipeline_mode`` actually imports and calls.  This test used to
        patch ``forgelm.compliance.verify_pipeline_manifest_at_path``, which
        the CLI stopped calling when the pipeline path was repointed, so the
        patch intercepted nothing and the assertion was satisfied by the
        *unpatched* missing-manifest path — which also exits 1.  Deleting the
        entire ``except UnicodeDecodeError`` branch left it green.  Two guards
        keep it honest now: the stub records that it was reached, and the
        payload assertion pins this branch's own message instead of an exit
        code several paths share.
        """
        from forgelm.cli._exit_codes import EXIT_CONFIG_ERROR
        from forgelm.cli.subcommands import _verify_annex_iv as _mod

        called: list[str] = []

        def _bubble(_path):
            called.append(_path)
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

        import forgelm.verify as _verify

        monkeypatch.setattr(_verify, "verify_pipeline_manifest_report", _bubble)

        with pytest.raises(SystemExit) as ei:
            _mod._run_verify_annex_iv_cmd(_build_args(path=str(tmp_path), pipeline=True), output_format="json")
        assert called, "the patched seam was never reached — the test would be vacuous"
        assert ei.value.code == EXIT_CONFIG_ERROR
        payload = json.loads(capsys.readouterr().out)
        assert payload["success"] is False
        assert any("not valid UTF-8" in v for v in payload["violations"])


# ---------------------------------------------------------------------------
# Phase 14.5 / F-PR54-H7 — per-stage pipeline evidence deep-parse
# ---------------------------------------------------------------------------


def _hashed_annex_iv_artifact() -> dict:
    """A complete Annex IV artefact carrying a correct manifest hash."""
    from forgelm.compliance import compute_annex_iv_manifest_hash

    artifact = _full_annex_iv_artifact()
    artifact["metadata"] = {"manifest_hash": compute_annex_iv_manifest_hash(artifact)}
    return artifact


def _evidence_violations(report) -> list:
    """Violations excluding the Tier 3 audit-log corroboration findings.

    These fixtures write a manifest with no ``audit_log.jsonl`` beside it,
    which is a legitimate state (an operator who never enabled compliance) and
    correctly reports ``unattested`` — never a clean corroboration.  Tests
    whose subject is the *per-stage evidence* rules assert on the remainder;
    the corroboration outcomes have their own coverage in
    ``TestAuditLogCorroboration``.

    Filters on the machine-stable
    :data:`forgelm.compliance.AUDIT_CORROBORATION_MARKER`, never on prose.
    """
    from forgelm.compliance import AUDIT_CORROBORATION_MARKER

    return [v for v in report.violations if AUDIT_CORROBORATION_MARKER not in v]


def _manifest_pointing_at(pointer, *, final_status: str = "completed") -> dict:
    """A one-stage pipeline manifest whose completed stage points at *pointer*."""
    return {
        "forgelm_version": "0.9.1",
        "pipeline_run_id": "pl_test",
        "pipeline_config_hash": "sha256:abc",
        "started_at": "2026-07-20T12:00:00+00:00",
        "final_status": final_status,
        "stages": [
            {
                "name": "s0",
                "index": 0,
                "trainer_type": "sft",
                "status": "completed",
                "input_source": "root",
                "output_model": "./s0/out",
                "training_manifest": pointer,
            }
        ],
    }


class TestPipelineStageEvidenceDeepParse:
    """Every ambiguous on-disk shape of a per-stage Annex IV artefact must
    fail closed.

    Before this, the chain verifier's per-stage check was ``os.path.isfile``
    only: a zero-byte, truncated, or tampered artefact passed while the
    verifier reported the run OK.  That is the "reports success without
    examining the thing it claims to check" defect class, and these tests
    pin each branch of the fix so it cannot regress into a silent pass.
    """

    def _evidence(self, tmp_path: Path, pointer):
        from forgelm.verify import verify_pipeline_stage_evidence

        return verify_pipeline_stage_evidence(_manifest_pointing_at(pointer), str(tmp_path))

    def test_complete_hashed_artifact_verifies(self, tmp_path: Path) -> None:
        target = tmp_path / "s0" / "compliance" / "annex_iv_metadata.json"
        target.parent.mkdir(parents=True)
        target.write_text(json.dumps(_hashed_annex_iv_artifact()))
        report = self._evidence(tmp_path, str(target))
        assert _evidence_violations(report) == []
        assert report.stages_examined == 1
        assert report.evidence_verified == 1

    def test_zero_byte_evidence_is_a_violation(self, tmp_path: Path) -> None:
        target = tmp_path / "annex_iv_metadata.json"
        target.write_text("")
        report = self._evidence(tmp_path, str(target))
        assert any("zero bytes" in v for v in report.violations)
        assert report.evidence_verified == 0

    def test_truncated_json_is_a_violation(self, tmp_path: Path) -> None:
        target = tmp_path / "annex_iv_metadata.json"
        target.write_text('{"system_identification": {"provider_name": "Ac')
        report = self._evidence(tmp_path, str(target))
        assert any("not valid JSON" in v for v in report.violations)

    def test_non_utf8_evidence_is_a_violation(self, tmp_path: Path) -> None:
        target = tmp_path / "annex_iv_metadata.json"
        target.write_bytes(b"\xff\xfe\x00{invalid")
        report = self._evidence(tmp_path, str(target))
        assert any("not valid UTF-8" in v or "not valid JSON" in v for v in report.violations)

    @pytest.mark.parametrize("root", [[], "a string", 42, None])
    def test_valid_json_that_is_not_an_object_is_a_violation(self, tmp_path: Path, root) -> None:
        target = tmp_path / "annex_iv_metadata.json"
        target.write_text(json.dumps(root))
        report = self._evidence(tmp_path, str(target))
        assert report.violations, f"root {root!r} passed the evidence check"
        assert report.evidence_verified == 0

    def test_object_missing_required_fields_is_a_violation(self, tmp_path: Path) -> None:
        """Deliberate divergence from the standalone verifier: incomplete
        fields exit 1 on their own, but 6 as chain evidence — the pipeline
        manifest asserted this stage completed with valid evidence, and that
        assertion was compared and failed."""
        target = tmp_path / "annex_iv_metadata.json"
        target.write_text(json.dumps({"system_identification": {}}))
        report = self._evidence(tmp_path, str(target))
        assert any("unusable" in v for v in report.violations)
        # Untagged ⇒ routes to EXIT_INTEGRITY_FAILURE (6).
        assert all(
            not v.startswith(("UNVERIFIED::", "INPUT_ERROR::", "IO_ERROR::")) for v in _evidence_violations(report)
        )

    def test_tampered_evidence_is_flagged_as_tampering(self, tmp_path: Path) -> None:
        target = tmp_path / "annex_iv_metadata.json"
        artifact = _hashed_annex_iv_artifact()
        artifact["performance_metrics"] = {"eval_loss": 0.0001}  # edited post-hash
        target.write_text(json.dumps(artifact))
        report = self._evidence(tmp_path, str(target))
        assert any("failed tamper detection" in v for v in report.violations)

    def test_unhashed_but_complete_evidence_is_unverified_not_verified(self, tmp_path: Path) -> None:
        """The distinction the brief requires: valid is not verified."""
        from forgelm.compliance import PIPELINE_MANIFEST_UNVERIFIED_PREFIX

        target = tmp_path / "annex_iv_metadata.json"
        target.write_text(json.dumps(_full_annex_iv_artifact()))  # no metadata.manifest_hash
        report = self._evidence(tmp_path, str(target))
        assert report.evidence_verified == 0
        assert report.evidence_unverified == 1
        assert all(v.startswith(PIPELINE_MANIFEST_UNVERIFIED_PREFIX) for v in report.violations)

    def test_relative_pointer_escaping_the_pipeline_dir_is_refused(self, tmp_path: Path) -> None:
        outside = tmp_path.parent / "outside_annex_iv.json"
        outside.write_text(json.dumps(_hashed_annex_iv_artifact()))
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        report = self._evidence(run_dir, "../../outside_annex_iv.json")
        assert any("escapes the pipeline directory" in v for v in report.violations)

    def test_symlink_evidence_is_refused(self, tmp_path: Path) -> None:
        """The evidence would be whatever the link resolves to at verify
        time, which is not a property of the archived run."""
        real = tmp_path / "real_annex_iv.json"
        real.write_text(json.dumps(_hashed_annex_iv_artifact()))
        link = tmp_path / "annex_iv_metadata.json"
        link.symlink_to(real)
        report = self._evidence(tmp_path, str(link))
        assert any("symlink" in v for v in report.violations)
        assert report.evidence_verified == 0

    def test_directory_where_a_file_is_expected_is_refused(self, tmp_path: Path) -> None:
        target = tmp_path / "annex_iv_metadata.json"
        target.mkdir()
        report = self._evidence(tmp_path, str(target))
        assert any("is a directory" in v for v in report.violations)

    def test_oversize_evidence_is_refused_unread(self, tmp_path: Path, monkeypatch) -> None:
        """A verifier that its own input can OOM is not a verifier."""
        import forgelm.verify as verify_mod

        monkeypatch.setattr(verify_mod, "STAGE_EVIDENCE_MAX_BYTES", 16)
        target = tmp_path / "annex_iv_metadata.json"
        target.write_text(json.dumps(_hashed_annex_iv_artifact()))
        report = self._evidence(tmp_path, str(target))
        assert any("refused unread" in v for v in report.violations)

    def test_completed_stage_with_no_evidence_pointer_is_a_violation(self, tmp_path: Path) -> None:
        for pointer in (None, "", 0, [], {}):
            report = self._evidence(tmp_path, pointer)
            assert any("records no evidence path" in v for v in report.violations), pointer

    def test_unreadable_evidence_routes_to_io_error(self, tmp_path: Path, monkeypatch) -> None:
        from forgelm.compliance import PIPELINE_MANIFEST_IO_ERROR_PREFIX

        target = tmp_path / "annex_iv_metadata.json"
        target.write_text(json.dumps(_hashed_annex_iv_artifact()))

        def _boom(path: str, cap: int):
            raise OSError("EIO: device failure")

        # Patch the read seam, not the verdict function.  The stage evidence
        # read moved into ``_read_capped_json`` so the byte cap and the parse
        # share one descriptor (a stat on one handle followed by an open of
        # another does not bound what actually gets read).  The OSError
        # routing under test is unchanged; only where the I/O happens moved.
        monkeypatch.setattr("forgelm.verify._read_capped_json", _boom)
        report = self._evidence(tmp_path, str(target))
        assert any(v.startswith(PIPELINE_MANIFEST_IO_ERROR_PREFIX) for v in report.violations)

    def test_non_completed_stages_are_not_examined(self, tmp_path: Path) -> None:
        """Only completed stages assert evidence; a skipped or failed stage
        legitimately has none."""
        from forgelm.verify import verify_pipeline_stage_evidence

        manifest = _manifest_pointing_at(None, final_status="failed")
        manifest["stages"][0]["status"] = "skipped_by_filter"
        report = verify_pipeline_stage_evidence(manifest, str(tmp_path))
        assert _evidence_violations(report) == []
        assert report.stages_examined == 0

    def test_completed_pipeline_with_no_completed_stage_is_a_violation(self, tmp_path: Path) -> None:
        """The class defect, inverted: a verifier whose happiest path is the
        one where it examined nothing.  A manifest claiming the whole run
        completed while presenting no completed stage must not verify clean.
        """
        from forgelm.verify import verify_pipeline_stage_evidence

        manifest = _manifest_pointing_at(None)
        manifest["stages"] = []
        report = verify_pipeline_stage_evidence(manifest, str(tmp_path))
        assert report.stages_examined == 0
        assert any("no completed stage" in v for v in report.violations)


def _two_stage_chain(tmp_path: Path) -> str:
    """A clean, hash-stamped two-stage chain on disk.  Returns the manifest path.

    The shape the destroyed-evidence reproduction needs: more than one stage,
    so that removing one still leaves a completed stage behind and the
    "final_status completed but nothing examined" backstop does not fire.
    """
    from forgelm.compliance import compute_annex_iv_manifest_hash

    stages = []
    for idx, name in enumerate(("sft_stage", "dpo_stage")):
        evidence = tmp_path / name / "compliance" / "annex_iv_metadata.json"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(json.dumps(_hashed_annex_iv_artifact()))
        stages.append(
            {
                "name": name,
                "index": idx,
                "trainer_type": "sft" if idx == 0 else "dpo",
                "status": "completed",
                "input_model": "org/base" if idx == 0 else "./out/s1/final_model",
                "input_source": "root" if idx == 0 else "chain",
                "output_model": f"./out/s{idx + 1}/final_model",
                "started_at": "2026-07-20T12:00:00+00:00",
                "finished_at": "2026-07-20T13:00:00+00:00",
                "duration_seconds": 3600.0,
                "metrics": {"eval_loss": 0.5},
                "gate_decision": "passed",
                "exit_code": 0,
                "training_manifest": str(evidence),
            }
        )
    manifest = {
        "forgelm_version": "0.9.1",
        "pipeline_run_id": "pl_test",
        "pipeline_config_hash": "sha256:abc",
        "started_at": "2026-07-20T12:00:00+00:00",
        "finished_at": "2026-07-20T13:00:00+00:00",
        "final_status": "completed",
        "stages": stages,
        "annex_iv": {
            "provider_name": "Acme Inc",
            "system_name": "Acme Pipeline System",
            "intended_purpose": "Customer-service assistant fine-tune",
        },
    }
    manifest["metadata"] = {"manifest_hash": compute_annex_iv_manifest_hash(manifest)}
    compliance_dir = tmp_path / "compliance"
    compliance_dir.mkdir(parents=True, exist_ok=True)
    path = compliance_dir / "pipeline_manifest.json"
    path.write_text(json.dumps(manifest))
    return str(path)


def _restamp(manifest_path: str, manifest: dict) -> None:
    """Re-stamp ``metadata.manifest_hash`` with the *public* hash function.

    This is the whole point of the reproduction: the digest is unkeyed and
    ``compute_annex_iv_manifest_hash`` requires no secret, so anyone who can
    write the manifest can also make the stamp agree with their edit.
    """
    from forgelm.compliance import compute_annex_iv_manifest_hash

    manifest.pop("metadata", None)
    manifest["metadata"] = {"manifest_hash": compute_annex_iv_manifest_hash(manifest)}
    Path(manifest_path).write_text(json.dumps(manifest))


class TestDestroyedStageEvidenceCannotVanish:
    """A stage whose evidence was destroyed must not drop out of the report.

    The chain manifest's ``metadata.manifest_hash`` is an UNKEYED SHA-256
    produced by the public ``compute_annex_iv_manifest_hash``, so re-stamping
    after an edit is free.  The per-stage deep parse examined only stages whose
    status was literally ``"completed"``, which handed an adversary a way to
    choose the token they would be ignored by:

        1. delete a stage's annex_iv_metadata.json
        2. flip that stage's status from "completed" to "skipped"
        3. recompute manifest_hash with the public function

    The stage then vanished from the report entirely — not flagged, not
    mentioned — while the chain and hash checks passed.  The single-stage
    variant was caught by the "final_status completed with no completed stage"
    backstop; the multi-stage case was not.
    """

    def test_clean_two_stage_chain_verifies(self, tmp_path: Path) -> None:
        """Baseline: the untampered chain is genuinely clean."""
        from forgelm.verify import verify_pipeline_manifest_report

        _two_stage_chain(tmp_path)
        report = verify_pipeline_manifest_report(str(tmp_path))
        assert _evidence_violations(report) == []
        assert report.hash_state == "verified"
        assert report.to_dict()["stages_total"] == 2
        assert report.stages_examined == 2
        assert report.evidence_verified == 2

    def test_destroyed_evidence_plus_status_flip_is_reported(self, tmp_path: Path) -> None:
        """The exact reproduction, end to end."""
        from forgelm.verify import verify_pipeline_manifest_report

        manifest_path = _two_stage_chain(tmp_path)
        manifest = json.loads(Path(manifest_path).read_text())

        Path(manifest["stages"][0]["training_manifest"]).unlink()  # 1. destroy
        manifest["stages"][0]["status"] = "skipped"  # 2. flip
        _restamp(manifest_path, manifest)  # 3. re-stamp

        report = verify_pipeline_manifest_report(str(tmp_path))
        payload = report.to_dict()

        # The unkeyed hash still agrees — that is exactly why the hash alone
        # cannot carry this weight, and why the status rules must.
        assert report.hash_state == "verified"
        # The tampered stage is reported, not silently skipped.
        assert report.violations != []
        assert any("sft_stage" in v and "unrecognised status" in v for v in report.violations)
        assert any("sft_stage" in v and "gate_decision 'passed'" in v for v in report.violations)
        # And it is still *listed*, with a disposition, so the census cannot
        # quietly shrink from two stages to one.
        assert payload["stages_total"] == 2
        assert payload["status_census"] == {"completed": 1, "skipped": 1}
        assert [row["name"] for row in payload["stage_dispositions"]] == ["sft_stage", "dpo_stage"]
        assert payload["stage_dispositions"][0]["disposition"].startswith("violation:")

    def test_status_flip_to_a_recognised_status_is_still_caught(self, tmp_path: Path) -> None:
        """Rule 1 alone would miss a downgrade to a *legitimate* token.

        ``skipped_by_filter`` is a real status, so the closed-enum check stays
        silent; ``gate_decision == "passed"`` is what gives it away.
        """
        from forgelm.verify import verify_pipeline_manifest_report

        manifest_path = _two_stage_chain(tmp_path)
        manifest = json.loads(Path(manifest_path).read_text())
        Path(manifest["stages"][0]["training_manifest"]).unlink()
        manifest["stages"][0]["status"] = "skipped_by_filter"
        _restamp(manifest_path, manifest)

        report = verify_pipeline_manifest_report(str(tmp_path))
        assert not any("unrecognised status" in v for v in report.violations)
        assert any("sft_stage" in v and "gate_decision 'passed'" in v for v in report.violations)

    @pytest.mark.parametrize("token", ["skipped", "SKIPPED", "completed ", "", "done", "ok"])
    def test_unrecognised_status_tokens_are_violations(self, tmp_path: Path, token: str) -> None:
        from forgelm.verify import verify_pipeline_stage_evidence

        manifest = _manifest_pointing_at(None)
        manifest["stages"][0]["status"] = token
        report = verify_pipeline_stage_evidence(manifest, str(tmp_path))
        assert any("unrecognised status" in v for v in report.violations), token

    @pytest.mark.parametrize("token", [None, 42, True, [], {}])
    def test_non_string_status_is_a_violation(self, tmp_path: Path, token) -> None:
        """A missing or non-string status must not be silently skipped."""
        from forgelm.verify import verify_pipeline_stage_evidence

        manifest = _manifest_pointing_at(None)
        manifest["stages"][0]["status"] = token
        report = verify_pipeline_stage_evidence(manifest, str(tmp_path))
        assert any("unrecognised status" in v for v in report.violations), token

    def test_every_stage_appears_in_the_census(self, tmp_path: Path) -> None:
        """Publish the count: every manifest row is listed with a disposition."""
        from forgelm.verify import verify_pipeline_stage_evidence

        manifest = _manifest_pointing_at(None, final_status="failed")
        manifest["stages"] = [
            dict(manifest["stages"][0], name="a", index=0, status="pending", gate_decision=None),
            dict(manifest["stages"][0], name="b", index=1, status="failed", gate_decision="failed"),
            dict(manifest["stages"][0], name="c", index=2, status="skipped_by_filter", gate_decision=None),
        ]
        payload = verify_pipeline_stage_evidence(manifest, str(tmp_path)).to_dict()
        assert payload["stages_total"] == 3
        assert payload["status_census"] == {"pending": 1, "failed": 1, "skipped_by_filter": 1}
        assert [row["disposition"] for row in payload["stage_dispositions"]] == [
            "not_applicable:pending",
            "not_applicable:failed",
            "not_applicable:filtered",
        ]


class TestLegitimateSkipsStayClean:
    """The false alarm this fix must NOT reintroduce.

    ``_pipeline.py`` overwrites ``status`` and ``skipped_reason`` on a
    ``--stage`` filter or a chain break and clears *nothing* else, so a stage
    that genuinely ran in an earlier pass keeps its ``finished_at``,
    ``duration_seconds``, ``metrics``, ``exit_code``, ``error`` and sometimes
    ``training_manifest`` while its status reads skipped.  Those fields are
    therefore unusable as tamper signals: keying on "a not-completed stage
    carrying execution traces" would fire on real runs.  ``gate_decision`` is
    the only field that survives the overwrite, which is why it is the only
    one the rules use.
    """

    def _stale_stage(self, tmp_path: Path, **overrides) -> dict:
        """A stage carrying every execution trace a prior pass leaves behind."""
        evidence = tmp_path / "prior" / "annex_iv_metadata.json"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(json.dumps(_hashed_annex_iv_artifact()))
        stage = {
            "name": "carried",
            "index": 0,
            "trainer_type": "sft",
            "status": "skipped_by_filter",
            "input_source": "root",
            "output_model": "./prior/final_model",
            "started_at": "2026-07-20T10:00:00+00:00",
            "finished_at": "2026-07-20T11:00:00+00:00",
            "duration_seconds": 3600.0,
            "metrics": {"eval_loss": 0.4},
            "exit_code": 0,
            "training_manifest": str(evidence),
            "skipped_reason": "--stage 'other' was specified; only that stage runs.",
        }
        stage.update(overrides)
        return stage

    def _report(self, tmp_path: Path, stage: dict):
        from forgelm.verify import verify_pipeline_stage_evidence

        manifest = _manifest_pointing_at(None, final_status="stopped_at_stage")
        manifest["stages"] = [stage]
        return verify_pipeline_stage_evidence(manifest, str(tmp_path))

    def test_stage_filter_skip_with_stale_execution_traces_is_clean(self, tmp_path: Path) -> None:
        """An operator-configured ``--stage`` skip must not be flagged."""
        stage = self._stale_stage(tmp_path, gate_decision=None)
        report = self._report(tmp_path, stage)
        assert _evidence_violations(report) == []
        assert report.to_dict()["stage_dispositions"][0]["disposition"] == "not_applicable:filtered"

    def test_resume_chain_break_with_stale_execution_traces_is_clean(self, tmp_path: Path) -> None:
        """``--resume-from`` after a chain break carries the same stale fields."""
        stage = self._stale_stage(
            tmp_path,
            status="skipped_due_to_prior_revert",
            gate_decision=None,
            skipped_reason="stage 'earlier' reverted; downstream stages skipped.",
        )
        report = self._report(tmp_path, stage)
        assert _evidence_violations(report) == []
        assert report.to_dict()["stage_dispositions"][0]["disposition"] == "not_applicable:chain_broken"

    def test_carried_over_failed_gate_decision_is_clean(self, tmp_path: Path) -> None:
        """A skip may inherit ``gate_decision='failed'`` from a prior pass.

        Only ``"passed"`` is a tamper signal — it is written on exactly one
        code path, alongside ``status = "completed"``.
        """
        stage = self._stale_stage(tmp_path, gate_decision="failed")
        assert _evidence_violations(self._report(tmp_path, stage)) == []

    @pytest.mark.parametrize(
        "status,gate,disposition",
        [
            ("pending", None, "not_applicable:pending"),
            ("failed", "failed", "not_applicable:failed"),
            ("gated_pending_approval", None, "not_applicable:gated"),
            ("skipped_by_filter", None, "not_applicable:filtered"),
            ("skipped_due_to_prior_revert", None, "not_applicable:chain_broken"),
        ],
    )
    def test_every_legitimate_not_completed_status_is_clean(
        self, tmp_path: Path, status: str, gate, disposition: str
    ) -> None:
        stage = self._stale_stage(tmp_path, status=status, gate_decision=gate)
        report = self._report(tmp_path, stage)
        assert _evidence_violations(report) == [], status
        assert report.to_dict()["stage_dispositions"][0]["disposition"] == disposition

    def test_status_enum_matches_the_orchestrator(self) -> None:
        """The verifier's closed set must equal the writer's Literal.

        A module-level import back into ``forgelm.verify`` would close an
        import cycle, so the two are pinned by this test instead.
        """
        import typing

        from forgelm.cli._pipeline import StageStatusLiteral
        from forgelm.verify import _KNOWN_STAGE_STATUSES

        assert set(typing.get_args(StageStatusLiteral)) == set(_KNOWN_STAGE_STATUSES)


class TestLegacyPointerVersionGate:
    """The pre-0.9.1 compatibility path must be gated on real release order.

    Only the leading numeric release triple is compared: a pre-release, dev,
    post or local segment of X.Y.Z *is* X.Y.Z and is never "older".  A
    ``packaging.Version`` comparison gets this wrong — ``Version("0.9.1rc1") <
    Version("0.9.1")`` is ``True`` — which would hand every manifest an rc
    build writes the softer path meant only for archived runs.
    """

    def _predates(self, version) -> bool:
        from forgelm.verify import _manifest_predates_pointer_fix

        return _manifest_predates_pointer_fix({"forgelm_version": version})

    @pytest.mark.parametrize("version", ["0.9.0", "0.8.0", "0.1.0", "0.0.1"])
    def test_genuinely_older_versions_are_legacy(self, version: str) -> None:
        assert self._predates(version) is True

    @pytest.mark.parametrize(
        "version",
        ["0.9.1", "0.9.1rc1", "0.9.1.dev3", "0.9.1.post1", "0.9.1+local", "0.9.1-rc1"],
    )
    def test_the_fix_version_and_its_prereleases_are_not_legacy(self, version: str) -> None:
        """An rc of the fix version carries the fix; it is not an old manifest."""
        assert self._predates(version) is False

    @pytest.mark.parametrize("version", ["0.10.0", "1.0.0", "0.9.2", "10.0.0"])
    def test_future_versions_are_not_legacy(self, version: str) -> None:
        """Tuple comparison, not string comparison: "0.10.0" > "0.9.1"."""
        assert self._predates(version) is False

    @pytest.mark.parametrize("version", [None, "", "garbage", "v0.9.0", "0.9", "0.9.x", 42, [], {}])
    def test_unparseable_versions_are_not_legacy(self, version) -> None:
        """Conservative direction: routes a missing artefact to a violation
        rather than to the softer compatibility path."""
        assert self._predates(version) is False

    def test_dev_sentinel_is_not_legacy(self) -> None:
        """``0.0.0+dev`` is ``_version.py``'s raw-source-checkout sentinel.

        It parses to (0, 0, 0) — the oldest possible release — but it denotes a
        *current* working tree, not an archived run.  Without the carve-out
        every manifest written from an uninstalled checkout unlocked the
        compatibility path.
        """
        from forgelm._version import __version__

        assert self._predates("0.0.0+dev") is False
        # Whatever this build stamps must not be classified legacy either.
        assert self._predates(__version__) is False

    @pytest.mark.parametrize("version", ["٠.٩.٠", "۰.۹.۰"])
    def test_non_ascii_digits_do_not_parse(self, version: str) -> None:
        """``\\d`` is Unicode-aware, so Arabic-Indic digits parsed and were
        classified legacy.  The pattern is ``[0-9]`` under ``re.ASCII``."""
        assert self._predates(version) is False

    def test_missing_key_is_not_legacy(self) -> None:
        from forgelm.verify import _manifest_predates_pointer_fix

        assert _manifest_predates_pointer_fix({}) is False
        assert _manifest_predates_pointer_fix(None) is False


class TestCappedReadIsFailClosed:
    """The byte cap must bind the handle that is actually read.

    Both size caps were stat-then-open: ``os.path.getsize`` on one handle,
    ``open()`` on another.  A file that changes size between the two defeats
    the cap entirely — a payload several times over the limit parsed to a
    clean pass.  ``compliance.compute_dataset_fingerprint`` already carries
    the rule this now follows: the stat must come from the open fd.
    """

    def _write(self, tmp_path: Path, name: str, payload: str) -> str:
        target = tmp_path / name
        target.write_text(payload, encoding="utf-8")
        return str(target)

    def test_under_cap_parses(self, tmp_path: Path) -> None:
        from forgelm.verify import _read_capped_json

        path = self._write(tmp_path, "ok.json", json.dumps({"a": 1}))
        assert _read_capped_json(path, 1024) == {"a": 1}

    def test_over_cap_is_refused_by_the_fstat_guard(self, tmp_path: Path) -> None:
        from forgelm.verify import _OversizeError, _read_capped_json

        path = self._write(tmp_path, "big.json", json.dumps({"pad": "x" * 4096}))
        with pytest.raises(_OversizeError):
            _read_capped_json(path, 1024)

    def test_under_reported_size_is_still_refused(self, tmp_path: Path, monkeypatch) -> None:
        """A lying ``fstat`` — what a file growing after the stat looks like —
        must not get past the bounded read."""
        from forgelm.verify import _OversizeError, _read_capped_json

        path = self._write(tmp_path, "grow.json", json.dumps({"pad": "x" * 4096}))

        class _Small:
            st_size = 12

        monkeypatch.setattr("forgelm.verify.os.fstat", lambda fd: _Small())
        with pytest.raises(_OversizeError):
            _read_capped_json(path, 1024)

    def test_multibyte_payload_is_measured_in_bytes(self, tmp_path: Path, monkeypatch) -> None:
        """The bounded read is binary: in text mode ``read(n)`` counts decoded
        characters, so a multibyte payload could carry several times the byte
        cap while satisfying a character budget."""
        from forgelm.verify import _OversizeError, _read_capped_json

        path = self._write(tmp_path, "multi.json", json.dumps({"pad": "é" * 2048}))

        class _Small:
            st_size = 12

        monkeypatch.setattr("forgelm.verify.os.fstat", lambda fd: _Small())
        with pytest.raises(_OversizeError):
            _read_capped_json(path, 1024)

    def test_size_check_reads_the_open_descriptor_not_the_path(self, tmp_path: Path, monkeypatch) -> None:
        """Pins the mechanism, because the verdict alone cannot.

        With the bounded read in place, reverting ``os.fstat(fh.fileno())`` to
        ``os.path.getsize(path)`` still ends in a refusal, so no black-box
        assertion distinguishes them.  What differs is *which file* is
        measured: ``getsize`` re-resolves the path, so the bytes measured need
        not be the bytes read.  This asserts the stat is taken from the
        descriptor, which is the property the fix exists to hold.
        """
        from forgelm.verify import _read_capped_json

        calls: list = []
        real_fstat = os.fstat
        monkeypatch.setattr("forgelm.verify.os.fstat", lambda fd: (calls.append(fd), real_fstat(fd))[1])
        path = self._write(tmp_path, "ok.json", json.dumps({"a": 1}))
        assert _read_capped_json(path, 1024) == {"a": 1}
        assert calls, "size check did not consult the open descriptor"

    def test_zero_byte_file_gets_its_own_error(self, tmp_path: Path) -> None:
        from forgelm.verify import _EmptyFileError, _read_capped_json

        path = self._write(tmp_path, "empty.json", "")
        with pytest.raises(_EmptyFileError):
            _read_capped_json(path, 1024)

    def test_gguf_sidecar_read_is_bounded(self, tmp_path: Path, monkeypatch) -> None:
        """A ``.sha256`` sidecar must not be slurped whole.

        Only the first whitespace-separated token is ever used, but the read
        was unbounded — and a sidecar is written by whoever can write the
        artefact's directory.  Same "verifier killed by its own input"
        exposure the byte caps close.  The verdict is unchanged either way
        (a huge junk sidecar fails the hex check in both), so this pins the
        bound itself: the digest is still found when megabytes of padding
        follow it.
        """
        from forgelm.verify import _SIDECAR_MAX_CHARS, verify_gguf

        gguf = tmp_path / "model.gguf"
        gguf.write_bytes(b"GGUF" + b"\x00" * 64)
        digest = hashlib.sha256(gguf.read_bytes()).hexdigest()
        # Valid digest first, then far more padding than the bound allows.
        (tmp_path / "model.gguf.sha256").write_text(f"{digest} *model.gguf\n" + "#" * (_SIDECAR_MAX_CHARS * 4))

        import builtins

        sidecar_reads: list = []
        real_open = builtins.open

        def _spy_open(file, *args, **kwargs):
            handle = real_open(file, *args, **kwargs)
            if str(file).endswith(".sha256"):
                real_read = handle.read

                def _read(n=-1):
                    sidecar_reads.append(n)
                    return real_read(n)

                handle.read = _read  # type: ignore[method-assign]
            return handle

        monkeypatch.setattr("forgelm.verify.open", _spy_open, raising=False)
        result = verify_gguf(str(gguf))

        assert result.checks["sidecar_match"] is True
        assert sidecar_reads, "sidecar was never read"
        assert all(n == _SIDECAR_MAX_CHARS for n in sidecar_reads), (
            f"sidecar read was unbounded: read({sidecar_reads}) — only the first token is used, "
            "so an arbitrarily large sidecar must not be pulled into memory"
        )

    def test_oversized_stage_evidence_is_a_violation(self, tmp_path: Path) -> None:
        """End to end: the cap still routes to a violation, not a clean pass."""
        from forgelm.verify import STAGE_EVIDENCE_MAX_BYTES, verify_pipeline_stage_evidence

        target = tmp_path / "s0" / "compliance" / "annex_iv_metadata.json"
        target.parent.mkdir(parents=True)
        target.write_text(json.dumps({"pad": "x" * (STAGE_EVIDENCE_MAX_BYTES + 64)}))
        report = verify_pipeline_stage_evidence(_manifest_pointing_at(str(target)), str(tmp_path))
        assert any("refused unread" in v for v in report.violations)


class TestPipelineManifestHashState:
    """``hash_state`` separates *valid* from *verified* on the chain manifest.

    A pre-v0.8.0 archived manifest carries no ``metadata.manifest_hash``: its
    structural and chain rules still pass, but nothing attested to its
    non-chain fields.  Reporting that as a plain OK overclaims.
    """

    def _write(self, tmp_path: Path, manifest: dict) -> None:
        compliance_dir = tmp_path / "compliance"
        compliance_dir.mkdir(parents=True, exist_ok=True)
        (compliance_dir / "pipeline_manifest.json").write_text(json.dumps(manifest))

    def _stage_evidence(self, tmp_path: Path) -> str:
        target = tmp_path / "s0" / "compliance" / "annex_iv_metadata.json"
        target.parent.mkdir(parents=True)
        target.write_text(json.dumps(_hashed_annex_iv_artifact()))
        return str(target)

    def test_absent_hash_reports_absent_and_still_verifies_structurally(self, tmp_path: Path) -> None:
        from forgelm.verify import verify_pipeline_manifest_report

        manifest = _manifest_pointing_at(self._stage_evidence(tmp_path))
        self._write(tmp_path, manifest)
        report = verify_pipeline_manifest_report(str(tmp_path))
        assert _evidence_violations(report) == []  # stays VALID
        assert report.hash_state == "absent"  # but not VERIFIED

    def test_matching_hash_reports_verified(self, tmp_path: Path) -> None:
        from forgelm.compliance import compute_annex_iv_manifest_hash
        from forgelm.verify import verify_pipeline_manifest_report

        manifest = _manifest_pointing_at(self._stage_evidence(tmp_path))
        manifest["metadata"] = {"manifest_hash": compute_annex_iv_manifest_hash(manifest)}
        self._write(tmp_path, manifest)
        report = verify_pipeline_manifest_report(str(tmp_path))
        assert _evidence_violations(report) == []
        assert report.hash_state == "verified"

    def test_edited_non_chain_field_reports_mismatch(self, tmp_path: Path) -> None:
        from forgelm.compliance import compute_annex_iv_manifest_hash
        from forgelm.verify import verify_pipeline_manifest_report

        manifest = _manifest_pointing_at(self._stage_evidence(tmp_path))
        manifest["metadata"] = {"manifest_hash": compute_annex_iv_manifest_hash(manifest)}
        manifest["final_status"] = "completed_with_edits"  # non-chain field
        self._write(tmp_path, manifest)
        report = verify_pipeline_manifest_report(str(tmp_path))
        assert report.hash_state == "mismatch"
        assert any("manifest hash mismatch" in v for v in report.violations)

    def test_non_object_manifest_root_is_an_input_error(self, tmp_path: Path) -> None:
        from forgelm.compliance import PIPELINE_MANIFEST_INPUT_ERROR_PREFIX
        from forgelm.verify import verify_pipeline_manifest_report

        compliance_dir = tmp_path / "compliance"
        compliance_dir.mkdir()
        (compliance_dir / "pipeline_manifest.json").write_text("[1, 2, 3]")
        report = verify_pipeline_manifest_report(str(tmp_path))
        assert all(v.startswith(PIPELINE_MANIFEST_INPUT_ERROR_PREFIX) for v in report.violations)
        assert report.stages_examined == 0


class TestVerifierParserHardening:
    """A verifier that can be killed by its own input is not a verifier.

    Deep-parsing every per-stage evidence file is what newly exposed these
    paths, so the regressions are this step's to own.
    """

    @staticmethod
    def _deep_json(path: Path, depth: int = 50_000) -> None:
        """A document that is small in bytes but ruinous in nesting depth."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[" * depth + "1" + "]" * depth)

    def _configured_manifest(self) -> dict:
        manifest = _manifest_pointing_at(None)
        manifest["annex_iv"] = {
            "provider_name": "Acme Inc.",
            "system_name": "ForgeLM-test",
            "intended_purpose": "baseline",
        }
        return manifest

    def test_deeply_nested_stage_evidence_is_refused_not_a_traceback(self, tmp_path: Path) -> None:
        """S6-D-01: ~100 KB of nested arrays — twenty times *under* the 8 MiB
        cap — raised an uncaught RecursionError that killed the verifier with a
        raw traceback, no stdout and no JSON envelope.  RecursionError is
        neither an OSError nor a ValueError, so every existing handler missed
        it.  It must route like any other unparseable artefact."""
        from forgelm.verify import EVIDENCE_VIOLATION, STAGE_EVIDENCE_MAX_BYTES, _verify_stage_evidence

        target = tmp_path / "s0" / "compliance" / "annex_iv_metadata.json"
        self._deep_json(target)
        assert target.stat().st_size < STAGE_EVIDENCE_MAX_BYTES, "fixture must sit under the byte cap"

        outcome, message = _verify_stage_evidence(str(target), str(tmp_path), self._configured_manifest())
        assert outcome == EVIDENCE_VIOLATION
        assert "nested too deeply" in message

    def test_deeply_nested_chain_manifest_is_refused_not_a_traceback(self, tmp_path: Path) -> None:
        """Same defect one level up, on the manifest the verifier reads first."""
        from forgelm.compliance import PIPELINE_MANIFEST_INPUT_ERROR_PREFIX
        from forgelm.verify import verify_pipeline_manifest_report

        self._deep_json(tmp_path / "compliance" / "pipeline_manifest.json")
        report = verify_pipeline_manifest_report(str(tmp_path))
        assert report.violations
        assert all(v.startswith(PIPELINE_MANIFEST_INPUT_ERROR_PREFIX) for v in report.violations)
        assert any("nested too deeply" in v for v in report.violations)
        assert report.stages_examined == 0

    def test_oversized_chain_manifest_is_refused_unread(self, tmp_path: Path, monkeypatch) -> None:
        """S6-D-02: the chain manifest was ``json.load``ed with no size cap at
        all, directly contradicting the rationale written for the stage-level
        cap.  A 600 MB manifest reaches ~3.6 GB peak RSS.

        The cap is patched down rather than writing a real 8 MiB fixture — the
        branch under test is the comparison, not the number.
        """
        import forgelm.verify as verify_mod
        from forgelm.compliance import PIPELINE_MANIFEST_INPUT_ERROR_PREFIX
        from forgelm.verify import verify_pipeline_manifest_report

        monkeypatch.setattr(verify_mod, "PIPELINE_MANIFEST_MAX_BYTES", 16)
        manifest_path = tmp_path / "compliance" / "pipeline_manifest.json"
        manifest_path.parent.mkdir(parents=True)
        manifest_path.write_text(json.dumps(_manifest_pointing_at(None)))

        report = verify_pipeline_manifest_report(str(tmp_path))
        assert report.violations
        assert all(v.startswith(PIPELINE_MANIFEST_INPUT_ERROR_PREFIX) for v in report.violations)
        assert any("refused unread" in v for v in report.violations)

    def test_the_cap_does_not_fire_on_a_normal_manifest(self, tmp_path: Path) -> None:
        """The other half of the cap: a real manifest is orders of magnitude
        below it and must verify untouched."""
        from forgelm.verify import PIPELINE_MANIFEST_MAX_BYTES, verify_pipeline_manifest_report

        evidence = tmp_path / "s0" / "compliance" / "annex_iv_metadata.json"
        evidence.parent.mkdir(parents=True)
        evidence.write_text(json.dumps(_hashed_annex_iv_artifact()))
        manifest_path = tmp_path / "compliance" / "pipeline_manifest.json"
        manifest_path.parent.mkdir(parents=True)
        manifest_path.write_text(json.dumps(_manifest_pointing_at(str(evidence))))

        assert manifest_path.stat().st_size < PIPELINE_MANIFEST_MAX_BYTES / 1000
        assert _evidence_violations(verify_pipeline_manifest_report(str(tmp_path))) == []


class TestStageEvidencePathContainment:
    """F4 / S6-D-03: the containment check and the symlink refusal must both
    actually bite.

    ``_resolve_stage_evidence_path`` called ``os.path.realpath`` on the joined
    relative pointer and checked containment on the *result*, which resolved
    symlinks before anything looked at them — so ``os.path.islink`` could never
    be true for a relative pointer and the documented symlink refusal was dead
    code.  A relative pointer at a symlink was silently followed.
    """

    def test_relative_symlink_is_refused(self, tmp_path: Path) -> None:
        from forgelm.verify import _resolve_stage_evidence_path

        real = tmp_path / "c" / "real.json"
        real.parent.mkdir(parents=True)
        real.write_text("{}")
        (tmp_path / "c" / "link.json").symlink_to(real)

        path, problem = _resolve_stage_evidence_path("c/link.json", str(tmp_path))
        assert path == ""
        assert "symlink" in problem

    def test_absolute_symlink_is_still_refused(self, tmp_path: Path) -> None:
        """The branch that already worked must keep working."""
        from forgelm.verify import _resolve_stage_evidence_path

        real = tmp_path / "real.json"
        real.write_text("{}")
        link = tmp_path / "link.json"
        link.symlink_to(real)

        path, problem = _resolve_stage_evidence_path(str(link), str(tmp_path))
        assert path == ""
        assert "symlink" in problem

    def test_relative_escape_is_refused(self, tmp_path: Path) -> None:
        from forgelm.verify import _resolve_stage_evidence_path

        path, problem = _resolve_stage_evidence_path("../../etc/passwd", str(tmp_path))
        assert path == ""
        assert "escapes the pipeline directory" in problem

    def test_escape_through_a_symlinked_parent_is_refused(self, tmp_path: Path) -> None:
        """Lexical normalisation alone cannot see this one: every component is
        innocent until the symlinked directory is resolved."""
        from forgelm.verify import _resolve_stage_evidence_path

        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "evil.json").write_text("{}")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "sneaky").symlink_to(outside, target_is_directory=True)

        path, problem = _resolve_stage_evidence_path("sneaky/evil.json", str(run_dir))
        assert path == ""
        assert "escapes the pipeline directory" in problem

    def test_a_plain_relative_pointer_still_resolves(self, tmp_path: Path) -> None:
        """The refusals must not swallow the legitimate case."""
        from forgelm.verify import _resolve_stage_evidence_path

        real = tmp_path / "s0" / "compliance" / "annex_iv_metadata.json"
        real.parent.mkdir(parents=True)
        real.write_text("{}")

        path, problem = _resolve_stage_evidence_path("s0/compliance/annex_iv_metadata.json", str(tmp_path))
        assert problem == ""
        assert os.path.realpath(path) == os.path.realpath(str(real))


class TestPipelineViolationPrecedence:
    """A weaker finding must never mask a stronger one.

    The shipped order returned on the tagged prefixes before the untagged
    ones, so a single unreadable or unhashed stage artefact downgraded a
    genuine tamper finding reported in the same run from 6 to 2 or 1.
    """

    def _classify(self, violations: list[str]) -> int:
        from forgelm.cli.subcommands._verify_annex_iv import _classify_pipeline_violations

        return _classify_pipeline_violations(violations)[0]

    def test_integrity_beats_io_input_and_unverified(self) -> None:
        assert (
            self._classify(
                [
                    "IO_ERROR::stage artefact unreadable",
                    "INPUT_ERROR::manifest not found",
                    "UNVERIFIED::stage carries no manifest_hash",
                    "manifest hash mismatch — modified after generation",
                ]
            )
            == 6
        )

    def test_io_beats_input_and_unverified(self) -> None:
        assert self._classify(["UNVERIFIED::no hash", "INPUT_ERROR::bad json", "IO_ERROR::disk"]) == 2

    def test_unverified_alone_is_exit_one(self) -> None:
        assert self._classify(["UNVERIFIED::stage carries no manifest_hash"]) == 1

    def test_no_violations_is_exit_zero(self) -> None:
        assert self._classify([]) == 0

    def test_tokens_are_stripped_from_display(self) -> None:
        from forgelm.cli.subcommands._verify_annex_iv import _classify_pipeline_violations

        _, display = _classify_pipeline_violations(["UNVERIFIED::no hash", "IO_ERROR::disk"])
        assert display == ["no hash", "disk"]


# ---------------------------------------------------------------------------
# Tier 3 — audit-log corroboration of the manifest's stage census
#
# The chain manifest's ``metadata.manifest_hash`` is an UNKEYED SHA-256 from a
# public function, so an attacker who can write the manifest re-stamps it for
# free.  The reproduction that motivated this tier:
#
#     status = "skipped_by_filter" + gate_decision REMOVED + evidence deleted
#     + manifest re-stamped  ->  ZERO VIOLATIONS, EXIT 0
#
# ``audit_log.jsonl``'s per-line ``_hmac`` is the one keyed integrity tag in
# the system.  These tests pin all three outcomes, and — the harder half —
# pin that every legitimate not-completed path stays clean.  The verifier this
# area replaced cried tamper on clean runs; a false alarm here is as much a
# defect as a missed attack.
# ---------------------------------------------------------------------------


_CORROBORATION_SECRET = "s" * 40


def _corroboration_stage(name: str, index: int, status: str, gate, evidence, prev_output) -> dict:
    return {
        "name": name,
        "index": index,
        "trainer_type": "sft",
        "status": status,
        "input_model": prev_output or "org/base",
        "input_source": "chain" if prev_output else "root",
        "output_model": f"./out/{name}/final_model" if status == "completed" else None,
        "started_at": "2026-07-20T10:00:00+00:00",
        "finished_at": "2026-07-20T11:00:00+00:00",
        "duration_seconds": 3600.0,
        "training_manifest": evidence,
        "metrics": {},
        "gate_decision": gate,
        "auto_revert_triggered": False,
        "skipped_reason": None,
        "exit_code": 0,
        "error": None,
    }


def _stamp_manifest(root: Path, manifest: dict) -> dict:
    """(Re-)stamp *manifest*'s unkeyed hash and write it — the attacker's move."""
    from forgelm.compliance import compute_annex_iv_manifest_hash

    manifest.pop("metadata", None)
    manifest["metadata"] = {"manifest_hash": compute_annex_iv_manifest_hash(manifest)}
    target = root / "compliance" / "pipeline_manifest.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, indent=2))
    return manifest


def _build_corroboration_run(
    root: Path,
    monkeypatch,
    *,
    stages,
    events,
    run_id: str = "fg-abc123",
    final_status: str = "completed",
    writer_secret: str = _CORROBORATION_SECRET,
) -> dict:
    """Write a manifest plus a real ``AuditLogger``-written log beside it.

    *stages* is ``[(name, status, gate_decision, has_evidence), ...]``;
    *events* is ``[(run_id, event, stage_name_or_None, gate_or_None), ...]``
    replayed through the real writer so the hash chain and ``_hmac`` tags are
    produced by the production code path, never hand-rolled.
    """
    from forgelm.compliance import AuditLogger

    rows = []
    prev_output = None
    for idx, (name, status, gate, has_evidence) in enumerate(stages):
        evidence = None
        if has_evidence:
            path = root / name / "compliance" / "annex_iv_metadata.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(_hashed_annex_iv_artifact()))
            evidence = str(path)
        row = _corroboration_stage(name, idx, status, gate, evidence, prev_output)
        rows.append(row)
        # Only the IMMEDIATELY preceding stage's output can be chained from —
        # carrying the last non-null output past a failed stage trips the
        # manifest's own chain-integrity rule.
        prev_output = row["output_model"]

    manifest = {
        "forgelm_version": "1.0.0",
        "generated_at": "2026-07-20T11:05:00+00:00",
        "pipeline_run_id": run_id,
        "pipeline_config_hash": "sha256:abc",
        "started_at": "2026-07-20T10:00:00+00:00",
        "finished_at": "2026-07-20T11:00:00+00:00",
        "final_status": final_status,
        "stopped_at": None,
        "stages": rows,
        "annex_iv": {
            "provider_name": "Acme Inc",
            "system_name": "Acme Pipeline System",
            "intended_purpose": "Customer-service assistant fine-tune",
        },
    }
    _stamp_manifest(root, manifest)

    monkeypatch.setenv("FORGELM_OPERATOR", "tester")
    if writer_secret:
        monkeypatch.setenv("FORGELM_AUDIT_SECRET", writer_secret)
    else:
        monkeypatch.delenv("FORGELM_AUDIT_SECRET", raising=False)
    loggers: dict = {}
    for rid, event, stage_name, gate in events:
        logger = loggers.get(rid)
        if logger is None:
            logger = loggers[rid] = AuditLogger(str(root), run_id=rid)
        payload = {"pipeline_run_id": rid}
        if stage_name is not None:
            payload["stage_name"] = stage_name
        if gate is not None:
            payload["gate_decision"] = gate
        logger.log_event(event, **payload)
    return manifest


def _corroborate(root: Path, monkeypatch, *, verifier_secret: str = _CORROBORATION_SECRET):
    from forgelm.verify import verify_pipeline_manifest_report

    if verifier_secret:
        monkeypatch.setenv("FORGELM_AUDIT_SECRET", verifier_secret)
    else:
        monkeypatch.delenv("FORGELM_AUDIT_SECRET", raising=False)
    return verify_pipeline_manifest_report(str(root))


def _started(run_id, name):
    return (run_id, "pipeline.stage_started", name, None)


def _passed(run_id, name):
    return (run_id, "pipeline.stage_completed", name, "passed")


def _finished(run_id="fg-abc123"):
    return (run_id, "pipeline.completed", None, None)


_CLEAN_TWO_STAGE = dict(
    stages=[("sft", "completed", "passed", True), ("dpo", "completed", "passed", True)],
    events=[
        _started("fg-abc123", "sft"),
        _passed("fg-abc123", "sft"),
        _started("fg-abc123", "dpo"),
        _passed("fg-abc123", "dpo"),
        _finished(),
    ],
)


class TestAuditCorroborationCorroborated:
    """The keyed log was read, authenticated, and agrees.  Exit 0."""

    def test_clean_two_stage_run_is_corroborated(self, tmp_path: Path, monkeypatch) -> None:
        _build_corroboration_run(tmp_path, monkeypatch, **_CLEAN_TWO_STAGE)
        report = _corroborate(tmp_path, monkeypatch)
        assert report.violations == []
        assert report.audit_corroboration == {
            "outcome": "corroborated",
            "reason": "stage_census_agrees",
            "events_examined": 4,
            "stages_asserted": 2,
        }

    def test_outcome_is_published_in_the_cli_envelope(self, tmp_path: Path, monkeypatch) -> None:
        """A verdict nobody can see is not a verdict.  The three-valued
        outcome must reach the JSON envelope, not just the report object."""
        _build_corroboration_run(tmp_path, monkeypatch, **_CLEAN_TWO_STAGE)
        payload = _corroborate(tmp_path, monkeypatch).to_dict()
        assert payload["audit_corroboration"]["outcome"] == "corroborated"
        assert payload["stages_total"] == 2
        assert [row["disposition"] for row in payload["stage_dispositions"]] == ["examined", "examined"]


class TestAuditCorroborationLegitimateRunsStayClean:
    """The trap.  A legitimately not-completed stage must NOT be a violation.

    The verifier this area replaced cried tamper on clean runs, and an earlier
    design pass for this very tier proposed a discriminator that false-alarmed
    on ``--stage``.  Every legitimate not-completed path is enumerated here.
    """

    def test_stage_filter_rerun_gets_a_fresh_run_id(self, tmp_path: Path, monkeypatch) -> None:
        """``--stage dpo`` after a full run: ``_init_state_preserving`` carries
        the prior ``completed`` rows into a manifest stamped with a NEW
        ``pipeline_run_id``, so the new run's log rightly says nothing about
        them.  The rule is one-directional (log => manifest) precisely so this
        does not fire."""
        _build_corroboration_run(
            tmp_path,
            monkeypatch,
            run_id="fg-run2",
            stages=[("sft", "completed", "passed", True), ("dpo", "completed", "passed", True)],
            events=[
                _started("fg-run1", "sft"),
                _passed("fg-run1", "sft"),
                _started("fg-run1", "dpo"),
                _passed("fg-run1", "dpo"),
                _finished("fg-run1"),
                _started("fg-run2", "dpo"),
                _passed("fg-run2", "dpo"),
                _finished("fg-run2"),
            ],
        )
        report = _corroborate(tmp_path, monkeypatch)
        assert report.violations == []
        assert report.audit_corroboration["outcome"] == "corroborated"

    def test_stage_filter_on_a_fresh_directory(self, tmp_path: Path, monkeypatch) -> None:
        """``--stage dpo`` with no prior run: sft is stamped
        ``skipped_by_filter`` and never emitted a passed event."""
        _build_corroboration_run(
            tmp_path,
            monkeypatch,
            stages=[("sft", "skipped_by_filter", None, False), ("dpo", "completed", "passed", True)],
            events=[_started("fg-abc123", "dpo"), _passed("fg-abc123", "dpo"), _finished()],
        )
        assert _corroborate(tmp_path, monkeypatch).violations == []

    def test_chain_break_downgrades_downstream_stages(self, tmp_path: Path, monkeypatch) -> None:
        """A failed stage stops the chain; downstream stages become
        ``skipped_due_to_prior_revert`` having emitted nothing."""
        _build_corroboration_run(
            tmp_path,
            monkeypatch,
            final_status="stopped_at_stage",
            stages=[
                ("sft", "completed", "passed", True),
                ("dpo", "failed", "failed", False),
                ("kto", "skipped_due_to_prior_revert", None, False),
            ],
            events=[
                _started("fg-abc123", "sft"),
                _passed("fg-abc123", "sft"),
                _started("fg-abc123", "dpo"),
                ("fg-abc123", "pipeline.stage_completed", "dpo", "failed"),
                _finished(),
            ],
        )
        assert _corroborate(tmp_path, monkeypatch).violations == []

    def test_gated_stage_emits_stage_gated_not_stage_completed(self, tmp_path: Path, monkeypatch) -> None:
        _build_corroboration_run(
            tmp_path,
            monkeypatch,
            final_status="gated_pending_approval",
            stages=[("sft", "completed", "passed", True), ("dpo", "gated_pending_approval", "approval_pending", False)],
            events=[
                _started("fg-abc123", "sft"),
                _passed("fg-abc123", "sft"),
                _started("fg-abc123", "dpo"),
                ("fg-abc123", "pipeline.stage_gated", "dpo", "approval_pending"),
            ],
        )
        assert _corroborate(tmp_path, monkeypatch).violations == []

    def test_pending_and_running_stages(self, tmp_path: Path, monkeypatch) -> None:
        _build_corroboration_run(
            tmp_path,
            monkeypatch,
            final_status="in_progress",
            stages=[
                ("sft", "completed", "passed", True),
                ("dpo", "running", None, False),
                ("kto", "pending", None, False),
            ],
            events=[
                _started("fg-abc123", "sft"),
                _passed("fg-abc123", "sft"),
                _started("fg-abc123", "dpo"),
            ],
        )
        assert _corroborate(tmp_path, monkeypatch).violations == []

    def test_auto_reverted_stage(self, tmp_path: Path, monkeypatch) -> None:
        _build_corroboration_run(
            tmp_path,
            monkeypatch,
            final_status="stopped_at_stage",
            stages=[("sft", "failed", "failed", False)],
            events=[
                _started("fg-abc123", "sft"),
                ("fg-abc123", "pipeline.stage_reverted", "sft", "failed"),
                _finished(),
            ],
        )
        report = _corroborate(tmp_path, monkeypatch)
        assert report.violations == []
        assert report.audit_corroboration["reason"] == "no_passed_stage_assertions"
        assert report.audit_corroboration["stages_asserted"] == 0

    def test_resume_reruns_a_passed_stage_which_then_fails(self, tmp_path: Path, monkeypatch) -> None:
        """THE false alarm to avoid.

        ``--resume-from`` reuses the SAME ``pipeline_run_id`` and appends to
        the SAME log.  Re-running stage 0, which then fails, downgrades the
        previously-passed stage 1 to ``skipped_due_to_prior_revert`` — while
        the log still holds both stages' stale "passed" under that run id.
        Re-start supersession is what retires those assertions.
        """
        _build_corroboration_run(
            tmp_path,
            monkeypatch,
            final_status="stopped_at_stage",
            stages=[("sft", "failed", "failed", False), ("dpo", "skipped_due_to_prior_revert", None, False)],
            events=[
                _started("fg-abc123", "sft"),
                _passed("fg-abc123", "sft"),
                _started("fg-abc123", "dpo"),
                _passed("fg-abc123", "dpo"),
                _finished(),
                _started("fg-abc123", "sft"),
                ("fg-abc123", "pipeline.stage_completed", "sft", "failed"),
                _finished(),
            ],
        )
        report = _corroborate(tmp_path, monkeypatch)
        assert report.violations == []
        assert report.audit_corroboration["stages_asserted"] == 0

    def test_resume_reruns_a_passed_stage_which_is_then_gated(self, tmp_path: Path, monkeypatch) -> None:
        _build_corroboration_run(
            tmp_path,
            monkeypatch,
            final_status="gated_pending_approval",
            stages=[("sft", "gated_pending_approval", "approval_pending", False), ("dpo", "pending", None, False)],
            events=[
                _started("fg-abc123", "sft"),
                _passed("fg-abc123", "sft"),
                _started("fg-abc123", "dpo"),
                _passed("fg-abc123", "dpo"),
                _finished(),
                _started("fg-abc123", "sft"),
                ("fg-abc123", "pipeline.stage_gated", "sft", "approval_pending"),
            ],
        )
        assert _corroborate(tmp_path, monkeypatch).violations == []

    def test_resume_reruns_a_passed_stage_and_is_killed_mid_run(self, tmp_path: Path, monkeypatch) -> None:
        """No terminal event for the new attempt at all: the ``stage_started``
        alone must retire the stale assertion, which is why it counts."""
        _build_corroboration_run(
            tmp_path,
            monkeypatch,
            final_status="in_progress",
            stages=[("sft", "running", None, False), ("dpo", "completed", "passed", True)],
            events=[
                _started("fg-abc123", "sft"),
                _passed("fg-abc123", "sft"),
                _started("fg-abc123", "dpo"),
                _passed("fg-abc123", "dpo"),
                _finished(),
                _started("fg-abc123", "sft"),
            ],
        )
        assert _corroborate(tmp_path, monkeypatch).violations == []

    def test_a_dropped_stage_started_still_retires_a_stale_assertion(self, tmp_path: Path, monkeypatch) -> None:
        """The last-word rule, isolated from re-start supersession.

        Audit emission is best-effort (``_audit_event`` logs a warning and
        continues on failure), so a re-run's ``pipeline.stage_started`` can be
        missing from the log while its terminal event landed.  Supersession
        cannot fire without that started event; the stage's own last terminal
        event must still retire the stale "passed", or a legitimately gated
        re-run reads as tampering.
        """
        _build_corroboration_run(
            tmp_path,
            monkeypatch,
            final_status="gated_pending_approval",
            stages=[("sft", "gated_pending_approval", "approval_pending", False)],
            events=[
                _started("fg-abc123", "sft"),
                _passed("fg-abc123", "sft"),
                _finished(),
                # No stage_started for the second attempt — emission was dropped.
                ("fg-abc123", "pipeline.stage_gated", "sft", "approval_pending"),
            ],
        )
        report = _corroborate(tmp_path, monkeypatch)
        assert report.violations == []
        assert report.audit_corroboration["stages_asserted"] == 0

    def test_resume_reruns_a_mid_chain_stage_and_everything_passes(self, tmp_path: Path, monkeypatch) -> None:
        """Supersession must not be so eager that it retires the assertions a
        successful resume legitimately re-establishes."""
        _build_corroboration_run(
            tmp_path,
            monkeypatch,
            stages=[
                ("sft", "completed", "passed", True),
                ("dpo", "completed", "passed", True),
                ("kto", "completed", "passed", True),
            ],
            events=[
                _started("fg-abc123", "sft"),
                _passed("fg-abc123", "sft"),
                _started("fg-abc123", "dpo"),
                ("fg-abc123", "pipeline.stage_completed", "dpo", "failed"),
                _finished(),
                _started("fg-abc123", "dpo"),
                _passed("fg-abc123", "dpo"),
                _started("fg-abc123", "kto"),
                _passed("fg-abc123", "kto"),
                _finished(),
            ],
        )
        report = _corroborate(tmp_path, monkeypatch)
        assert report.violations == []
        assert report.audit_corroboration["stages_asserted"] == 3


class TestAuditCorroborationContradicted:
    """Compared and did not match.  VIOLATION, exit 6."""

    def _exit_code(self, report) -> int:
        from forgelm.cli.subcommands._verify_annex_iv import _classify_pipeline_violations

        return _classify_pipeline_violations(report.violations)[0]

    def test_the_reproduction_status_downgraded_and_re_stamped(self, tmp_path: Path, monkeypatch) -> None:
        """The open bypass, closed.

        ``status="skipped_by_filter"`` + ``gate_decision`` REMOVED + evidence
        deleted + manifest re-stamped used to exit 0 with zero violations.
        """
        manifest = _build_corroboration_run(tmp_path, monkeypatch, **_CLEAN_TWO_STAGE)
        manifest["stages"][1]["status"] = "skipped_by_filter"
        manifest["stages"][1].pop("gate_decision")
        os.remove(manifest["stages"][1]["training_manifest"])
        _stamp_manifest(tmp_path, manifest)

        report = _corroborate(tmp_path, monkeypatch)
        assert report.hash_state == "verified", "the unkeyed re-stamp still 'passes' — that is the point"
        assert report.audit_corroboration["outcome"] == "contradicted"
        assert report.audit_corroboration["reason"] == "stage_census_mismatch"
        assert any("'dpo'" in v and "status was altered" in v for v in report.violations)
        assert self._exit_code(report) == 6

    def test_stage_row_deleted_outright(self, tmp_path: Path, monkeypatch) -> None:
        manifest = _build_corroboration_run(tmp_path, monkeypatch, **_CLEAN_TWO_STAGE)
        os.remove(manifest["stages"][1]["training_manifest"])
        manifest["stages"].pop(1)
        _stamp_manifest(tmp_path, manifest)

        report = _corroborate(tmp_path, monkeypatch)
        assert report.audit_corroboration["outcome"] == "contradicted"
        assert any("'dpo'" in v and "no stage of that name" in v for v in report.violations)
        assert self._exit_code(report) == 6

    def test_a_deleted_log_line_breaks_the_hash_chain(self, tmp_path: Path, monkeypatch) -> None:
        _build_corroboration_run(tmp_path, monkeypatch, **_CLEAN_TWO_STAGE)
        log = tmp_path / "audit_log.jsonl"
        lines = log.read_text().splitlines(keepends=True)
        log.write_text("".join(lines[:1] + lines[2:]))

        report = _corroborate(tmp_path, monkeypatch)
        assert report.audit_corroboration["reason"] == "audit_log_integrity"
        assert self._exit_code(report) == 6

    def test_an_edited_log_line_fails_its_hmac(self, tmp_path: Path, monkeypatch) -> None:
        """The keyed guarantee: an attacker without the secret cannot re-sign."""
        _build_corroboration_run(tmp_path, monkeypatch, **_CLEAN_TWO_STAGE)
        log = tmp_path / "audit_log.jsonl"
        lines = log.read_text().splitlines()
        entry = json.loads(lines[1])
        entry["stage_name"] = "somewhere-else"
        lines[1] = json.dumps(entry)
        log.write_text("\n".join(lines) + "\n")

        report = _corroborate(tmp_path, monkeypatch)
        assert report.audit_corroboration["reason"] == "audit_log_integrity"
        assert self._exit_code(report) == 6

    def test_non_utf8_bytes_in_our_own_article_12_record(self, tmp_path: Path, monkeypatch) -> None:
        """We wrote the log as UTF-8; non-UTF-8 inside it means it was
        corrupted after we wrote it — an integrity verdict, not exit 1."""
        _build_corroboration_run(tmp_path, monkeypatch, **_CLEAN_TWO_STAGE)
        with open(tmp_path / "audit_log.jsonl", "ab") as fh:
            fh.write(b"\xff\xfe not utf-8\n")

        report = _corroborate(tmp_path, monkeypatch)
        assert report.audit_corroboration["reason"] == "audit_log_encoding"
        assert self._exit_code(report) == 6

    def test_a_log_line_that_is_valid_json_but_not_an_object(self, tmp_path: Path, monkeypatch) -> None:
        """``123`` and ``"text"`` parse fine and have no ``.get``.

        Without the shape guard the very next line — the ``_hmac`` presence
        check — raises ``TypeError`` and kills the verifier with a raw
        traceback and no envelope, which is the failure mode every cap and
        parse guard in this module exists to prevent.
        """
        _build_corroboration_run(tmp_path, monkeypatch, **_CLEAN_TWO_STAGE)
        with open(tmp_path / "audit_log.jsonl", "a") as fh:
            fh.write("123\n")

        report = _corroborate(tmp_path, monkeypatch)
        assert report.audit_corroboration["reason"] == "audit_log_malformed"
        assert any("not a JSON object" in v for v in report.violations)

    def test_a_malformed_log_line(self, tmp_path: Path, monkeypatch) -> None:
        _build_corroboration_run(tmp_path, monkeypatch, **_CLEAN_TWO_STAGE)
        with open(tmp_path / "audit_log.jsonl", "a") as fh:
            fh.write("{not json\n")

        report = _corroborate(tmp_path, monkeypatch)
        assert report.audit_corroboration["reason"] == "audit_log_malformed"
        assert self._exit_code(report) == 6


class TestAuditCorroborationUnattested:
    """Nothing attested to the manifest.  UNVERIFIED (exit 1), never a pass.

    This is the whole point of the three-valued outcome.  Reporting any of
    these as a clean corroboration would be the eighth instance of "a check
    that reports success without examining the thing it claims to check" — in
    the very code written to close the seventh.
    """

    def _exit_code(self, report) -> int:
        from forgelm.cli.subcommands._verify_annex_iv import _classify_pipeline_violations

        return _classify_pipeline_violations(report.violations)[0]

    def _assert_unattested(self, report, reason: str) -> None:
        assert report.audit_corroboration["outcome"] == "unattested"
        assert report.audit_corroboration["reason"] == reason
        assert report.audit_corroboration["stages_asserted"] == 0
        assert self._exit_code(report) == 1

    def test_no_audit_log_at_all_is_not_a_violation(self, tmp_path: Path, monkeypatch) -> None:
        """An operator who never enabled compliance must not become a tamper
        finding — but must not be certified clean either."""
        _build_corroboration_run(tmp_path, monkeypatch, stages=_CLEAN_TWO_STAGE["stages"], events=[])
        report = _corroborate(tmp_path, monkeypatch)
        self._assert_unattested(report, "audit_log_absent")

    def test_no_secret_configured_at_verify_time(self, tmp_path: Path, monkeypatch) -> None:
        _build_corroboration_run(tmp_path, monkeypatch, **_CLEAN_TWO_STAGE)
        report = _corroborate(tmp_path, monkeypatch, verifier_secret="")
        self._assert_unattested(report, "no_audit_secret")

    def test_log_written_by_an_unkeyed_writer_carries_no_hmac(self, tmp_path: Path, monkeypatch) -> None:
        _build_corroboration_run(tmp_path, monkeypatch, writer_secret="", **_CLEAN_TWO_STAGE)
        report = _corroborate(tmp_path, monkeypatch)
        self._assert_unattested(report, "audit_log_unsigned")

    def test_log_carries_no_event_for_this_run_id(self, tmp_path: Path, monkeypatch) -> None:
        _build_corroboration_run(tmp_path, monkeypatch, run_id="fg-elsewhere", **_CLEAN_TWO_STAGE)
        report = _corroborate(tmp_path, monkeypatch)
        self._assert_unattested(report, "no_events_for_run_id")

    def test_zero_byte_log(self, tmp_path: Path, monkeypatch) -> None:
        _build_corroboration_run(tmp_path, monkeypatch, stages=_CLEAN_TWO_STAGE["stages"], events=[])
        (tmp_path / "audit_log.jsonl").write_text("")
        report = _corroborate(tmp_path, monkeypatch)
        self._assert_unattested(report, "audit_log_empty")

    def test_manifest_without_a_run_id(self, tmp_path: Path, monkeypatch) -> None:
        manifest = _build_corroboration_run(tmp_path, monkeypatch, **_CLEAN_TWO_STAGE)
        manifest.pop("pipeline_run_id")
        _stamp_manifest(tmp_path, manifest)
        report = _corroborate(tmp_path, monkeypatch)
        assert report.audit_corroboration["reason"] == "manifest_has_no_run_id"

    def test_log_tail_truncated_to_erase_a_stage(self, tmp_path: Path, monkeypatch) -> None:
        """The hash chain links each line to its predecessor, so deleting from
        the MIDDLE is visible and deleting the TAIL is not.  Requiring this
        run's ``pipeline.completed`` whenever the manifest claims a terminal
        status turns the truncation attack from exit 0 into exit 1."""
        manifest = _build_corroboration_run(tmp_path, monkeypatch, **_CLEAN_TWO_STAGE)
        log = tmp_path / "audit_log.jsonl"
        lines = log.read_text().splitlines(keepends=True)
        log.write_text("".join(lines[:-2]))  # drop dpo's passed event + pipeline.completed
        manifest["stages"][1]["status"] = "skipped_by_filter"
        manifest["stages"][1].pop("gate_decision")
        os.remove(manifest["stages"][1]["training_manifest"])
        _stamp_manifest(tmp_path, manifest)

        report = _corroborate(tmp_path, monkeypatch)
        self._assert_unattested(report, "run_terminal_event_absent")

    def test_oversize_log_is_refused_unread(self, tmp_path: Path, monkeypatch) -> None:
        from forgelm.compliance import AUDIT_LOG_CORROBORATION_MAX_BYTES

        _build_corroboration_run(tmp_path, monkeypatch, **_CLEAN_TWO_STAGE)
        with open(tmp_path / "audit_log.jsonl", "a") as fh:
            fh.write("x" * (AUDIT_LOG_CORROBORATION_MAX_BYTES + 1))
        report = _corroborate(tmp_path, monkeypatch)
        self._assert_unattested(report, "audit_log_oversize")

    def test_a_mid_read_io_failure_routes_to_exit_2(self, tmp_path: Path, monkeypatch) -> None:
        """Exit-code contract: 6 = compared and did not match, 1 = never got to
        compare, 2 = runtime I/O.  An unreadable log is the third."""
        _build_corroboration_run(tmp_path, monkeypatch, **_CLEAN_TWO_STAGE)
        real_open = open

        def _boom(path, *args, **kwargs):
            if str(path).endswith("audit_log.jsonl"):
                raise OSError("simulated mid-read failure")
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr("builtins.open", _boom)
        report = _corroborate(tmp_path, monkeypatch)
        assert report.audit_corroboration["reason"] == "audit_log_unreadable"
        assert self._exit_code(report) == 2


class TestAuditLogReaderCap:
    """``_read_audit_log_lines``'s opt-in byte cap."""

    def test_verify_audit_log_is_uncapped_by_default(self, tmp_path: Path, monkeypatch) -> None:
        """The cap applies where an untrusted directory is read as a side
        effect of verifying something else — never to ``verify-audit``, whose
        whole job is to read the operator's own log."""
        from forgelm.compliance import _read_audit_log_lines

        log = tmp_path / "audit_log.jsonl"
        log.write_text('{"a": 1}\n' * 200)
        failure, lines = _read_audit_log_lines(str(log))
        assert failure is None
        assert len(lines) == 200

    def test_the_size_is_taken_from_the_open_descriptor(self, tmp_path: Path, monkeypatch) -> None:
        """Not a separate ``os.path.getsize``: under stat-then-open the file
        measured and the file read are two different observations, so the cap
        is bypassed outright.  ``os.fstat`` is what makes it true."""
        import os as _os

        from forgelm.compliance import AUDIT_FAILURE_OVERSIZE, _read_audit_log_lines

        log = tmp_path / "audit_log.jsonl"
        log.write_text('{"a": 1}\n' * 200)

        def _forbidden(*args, **kwargs):  # pragma: no cover - must never run
            raise AssertionError("the cap must be enforced via os.fstat on the open descriptor")

        monkeypatch.setattr(_os.path, "getsize", _forbidden)
        failure, lines = _read_audit_log_lines(str(log), max_bytes=10)
        assert failure is not None
        assert failure[1] == AUDIT_FAILURE_OVERSIZE
        assert lines == [], "an over-cap log must be refused UNREAD"

    def test_the_fstat_alone_refuses_an_over_cap_log(self, tmp_path: Path, monkeypatch) -> None:
        """The descriptor stat must refuse on its own.

        Isolated from the streaming guard below by making only the *stat* say
        the file is over cap: the bytes on disk are tiny, so nothing else can
        trip.  Without the fstat branch the file would stream through clean.
        """
        from forgelm.compliance import AUDIT_FAILURE_OVERSIZE, _read_audit_log_lines

        log = tmp_path / "audit_log.jsonl"
        log.write_text('{"a": 1}\n')
        _fake_fstat_size(monkeypatch, target=str(log), size=10_000)

        failure, lines = _read_audit_log_lines(str(log), max_bytes=100)
        assert failure is not None and failure[1] == AUDIT_FAILURE_OVERSIZE
        assert lines == []

    def test_the_streaming_guard_catches_a_log_that_grew_after_open(self, tmp_path: Path, monkeypatch) -> None:
        """The fstat bounds the file only as it was at open time.

        Isolated from the fstat branch by making the stat under-report: a log
        being appended to by a live writer passes the stat and would then be
        streamed without bound.  The running character budget is what keeps
        the cap true regardless of what the descriptor claims about itself.
        """
        from forgelm.compliance import AUDIT_FAILURE_OVERSIZE, _read_audit_log_lines

        log = tmp_path / "audit_log.jsonl"
        log.write_text('{"a": 1}\n' * 200)
        _fake_fstat_size(monkeypatch, target=str(log), size=1)

        failure, lines = _read_audit_log_lines(str(log), max_bytes=50)
        assert failure is not None and failure[1] == AUDIT_FAILURE_OVERSIZE
        assert lines == []


def _fake_fstat_size(monkeypatch, *, target: str, size: int) -> None:
    """Make ``os.fstat`` report *size* for the descriptor opened on *target*.

    Every other descriptor keeps its real stat, so pytest's own I/O is
    untouched.  Patching ``os.fstat`` rather than the file is the only way to
    drive the two byte-cap guards apart: on a real file both fire, and each
    then masks the other's absence.
    """
    import os as _os

    real_fstat = _os.fstat
    target_stat = _os.stat(target)
    identity = (target_stat.st_dev, target_stat.st_ino)

    class _Stat:
        def __init__(self, wrapped, st_size):
            self._wrapped = wrapped
            self.st_size = st_size

        def __getattr__(self, name):
            return getattr(self._wrapped, name)

    def _fake(fd):
        wrapped = real_fstat(fd)
        if (wrapped.st_dev, wrapped.st_ino) == identity:
            return _Stat(wrapped, size)
        return wrapped

    monkeypatch.setattr(_os, "fstat", _fake)
