"""Every artefact ForgeLM publishes must be readable by a strict JSON parser.

CPython is permissive at both ends and the two permissions compound:
``json.dumps`` emits the bare tokens ``NaN`` / ``Infinity`` / ``-Infinity``,
which RFC 8259 has no literal for, and ``json.loads`` **accepts** them again.
So a test that writes an artefact and reads it back with ``json.loads``
passes — which is exactly why this shipped. Every assertion here reparses with
``parse_constant`` wired to raise, because Python's own loader is not an
oracle for the contract these files publish.
"""

from __future__ import annotations

import json
import math
import os
import tempfile

import pytest

from forgelm._strict_json import dumps_strict, sanitize_non_finite


def _reject(token: str) -> None:
    raise AssertionError(f"non-finite JSON token {token!r} reached the artefact")


def strict_loads(raw: str):
    """``json.loads`` that refuses what a real strict parser refuses."""
    return json.loads(raw, parse_constant=_reject)


class TestSanitizer:
    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
    def test_non_finite_becomes_a_string(self, bad):
        assert sanitize_non_finite(bad) == repr(bad)

    def test_nesting_is_walked(self):
        payload = {"a": [1.0, {"b": float("nan")}], "c": (float("inf"),)}
        assert sanitize_non_finite(payload) == {"a": [1.0, {"b": "nan"}], "c": ["inf"]}

    def test_finite_values_are_untouched(self):
        payload = {"a": 1.5, "b": [0, -3], "c": "nan", "d": None}
        assert sanitize_non_finite(payload) == payload

    def test_bool_is_not_treated_as_a_number(self):
        """``bool`` is an ``int`` subclass; a careless isinstance check breaks it."""
        assert sanitize_non_finite({"flag": True, "other": False}) == {"flag": True, "other": False}

    def test_ints_and_strings_are_untouched(self):
        assert sanitize_non_finite({"i": 42, "s": "nan", "n": None}) == {"i": 42, "s": "nan", "n": None}

    @pytest.mark.parametrize("dtype", ["float64", "float32"])
    @pytest.mark.parametrize("bad", ["nan", "inf", "-inf"])
    def test_numpy_scalars_normalise_to_the_same_representation(self, dtype, bad):
        """lm-eval returns NumPy scalars, which is where the finding came from.

        Two distinct traps. ``numpy.float64`` **is** a ``float`` subclass, so a
        bare ``isinstance(value, float)`` catches it — but ``repr()`` on it
        yields ``"np.float64(nan)"`` under NumPy 2, putting a
        NumPy-version-dependent string into a compliance artefact instead of
        the measurement. ``numpy.float32`` is **not** a ``float`` subclass at
        all and fell through the walk entirely, landing on whatever
        ``default=`` the caller happened to pass — correct only by accident.

        Every numeric type must produce one representation.
        """
        np = pytest.importorskip("numpy")

        value = getattr(np, dtype)(bad)
        assert sanitize_non_finite(value) == bad
        assert strict_loads(dumps_strict({"x": value})) == {"x": bad}

    def test_finite_numpy_values_keep_their_precision(self):
        """The sanitiser must not silently widen a finite measurement.

        Converting every numeric through ``float()`` would turn a ``float32``
        0.1 into 0.10000000149011612 in the artefact. Only the *non-finite*
        branch normalises; finite values pass through untouched and are
        serialised by ``json`` exactly as before.
        """
        np = pytest.importorskip("numpy")

        assert sanitize_non_finite(np.float64(0.1)) == np.float64(0.1)
        assert strict_loads(dumps_strict({"x": np.float64(0.1)})) == {"x": 0.1}

    @pytest.mark.parametrize(
        ("factory", "expected"),
        [
            ("float32", 0.85),
            ("float16", 0.1),
            ("int64", 3),
            ("uint8", 7),
            ("bool_", True),
        ],
    )
    def test_finite_numpy_scalars_stay_numbers_without_a_default(self, factory, expected):
        """A finite ``float32`` / ``int64`` / ``bool_`` must serialise as itself.

        The non-finite half of the NumPy problem was fixed first; the finite half
        was left raising ``TypeError`` from a writer with no ``default=``, and
        turning the number into a *string* in one with ``default=str``.
        """
        np = pytest.importorskip("numpy")

        value = getattr(np, factory)(expected)
        parsed = strict_loads(dumps_strict({"x": value}))["x"]
        assert parsed == expected
        assert type(parsed) is type(expected), "a number must not become a string"
        # And ``default=str`` must be irrelevant: the value is already native.
        assert strict_loads(dumps_strict({"x": value}, default=str))["x"] == expected

    def test_float32_is_not_widened_to_its_binary_expansion(self):
        """``float32(0.85)`` is 0.85 in the artefact, not 0.8500000238418579."""
        np = pytest.importorskip("numpy")

        assert dumps_strict({"x": np.float32(0.85)}) == '{"x": 0.85}'


class TestDumpsStrict:
    def test_output_survives_a_strict_parser(self):
        raw = dumps_strict({"a": float("nan"), "b": [float("inf")], "ok": 2.5})
        assert strict_loads(raw) == {"a": "nan", "b": ["inf"], "ok": 2.5}

    def test_the_tripwire_fires_on_anything_the_walk_misses(self):
        """The second pass is the point, and it must be ForgeLM's, not CPython's.

        The first version of this test asserted
        ``json.dumps(..., allow_nan=False)`` raises — a property of the
        standard library, true whether or not ``dumps_strict`` passes the flag.
        It therefore stayed green when ``allow_nan=False`` was missing from
        ``dumps_strict`` itself, which is exactly what happened: the flag was
        absent from the shipped bytes for a whole commit while this test
        reported success.

        A ``default=`` hook is the realistic way a non-finite value gets past
        the sanitiser — the walk never sees the object, the hook manufactures
        the float — so that is what this drives.
        """
        with pytest.raises(ValueError, match="not JSON compliant|Out of range"):
            dumps_strict({"x": object()}, default=lambda _o: float("nan"))

    def test_a_type_the_walk_cannot_reach_still_cannot_ship(self):
        """Sets are not recursed into; the tripwire must still stop them."""
        with pytest.raises((ValueError, TypeError)):
            dumps_strict({"x": {float("nan")}})


class TestPublishedArtefactsAreStrict:
    def test_audit_log_lines_are_strict_json(self):
        """The Art. 12 artefact. One bad line makes the record unreadable."""
        from forgelm.compliance import AuditLogger

        with tempfile.TemporaryDirectory() as tmp:
            audit = AuditLogger(tmp)
            audit.log_event(
                "evaluation.loss_gate_completed",
                passed=True,
                max_acceptable_loss=float("nan"),
                observed=float("inf"),
                nested={"deep": [float("-inf")]},
            )
            path = os.path.join(tmp, "audit_log.jsonl")
            for line in open(path, encoding="utf-8").read().splitlines():
                if line.strip():
                    strict_loads(line)

    def test_audit_hmac_covers_the_bytes_that_reach_disk(self, monkeypatch):
        """Sanitise-then-HMAC, not HMAC-then-sanitise.

        Computing the tag over the raw entry would authenticate a line that
        was never written, so ``verify-audit --require-hmac`` would report
        tampering on a file ForgeLM itself produced.
        """
        from forgelm.compliance import AuditLogger, verify_audit_log

        secret = "test-secret-for-strict-json"
        with tempfile.TemporaryDirectory() as tmp:
            # monkeypatch restores the process's own value (or absence) after the test
            monkeypatch.setenv("FORGELM_AUDIT_SECRET", secret)
            audit = AuditLogger(tmp)
            audit.log_event("evaluation.loss_gate_completed", score=float("nan"))
            monkeypatch.delenv("FORGELM_AUDIT_SECRET")  # verification must rely on the explicit secret only
            result = verify_audit_log(
                os.path.join(tmp, "audit_log.jsonl"),
                hmac_secret=secret,
                require_hmac=True,
            )
        assert result.valid, f"HMAC verification failed on a self-produced log: {result.reason}"

    def test_annex_iv_manifest_hash_covers_the_bytes_that_reach_disk(self, tmp_path):
        """A diverged run must not make its own Annex IV artefact fail verification.

        The writer turns ``nan`` into the string ``"nan"``; the stamped
        ``manifest_hash`` was computed over the raw float, which canonicalises
        as the bare token ``NaN``. The verifier re-hashes what is on disk, so an
        untouched artefact reported "modified after generation" — exit 6, the
        code operators alarm on — for exactly the runs an auditor most needs to
        read.
        """
        from forgelm.compliance import export_compliance_artifacts
        from forgelm.verify import verify_annex_iv_artifact

        manifest = {
            "forgelm_version": "0",
            "generated_at": "now",
            "config_hash": "sha256:abc",
            "model_lineage": {"base_model": "m", "adapter_method": "LoRA r=8"},
            "training_parameters": {"trainer_type": "sft", "epochs": 1},
            "data_provenance": {"primary_dataset": "ds"},
            "evaluation_results": {"metrics": {"eval_loss": float("nan")}},
            "annex_iv": {
                "provider_name": "Acme",
                "system_name": "Bot",
                "intended_purpose": "QA",
                "system_version": "1.0",
                "risk_classification": "minimal-risk",
            },
        }
        out = str(tmp_path / "compliance")
        export_compliance_artifacts(manifest, out)

        annex_path = os.path.join(out, "annex_iv_metadata.json")
        with open(annex_path, encoding="utf-8") as fh:
            on_disk = strict_loads(fh.read())
        assert on_disk["performance_metrics"]["eval_loss"] == "nan", "premise: the non-finite value was written"

        result = verify_annex_iv_artifact(annex_path)
        assert result.valid, f"untouched artefact with a non-finite metric failed its own verifier: {result.reason}"

    def test_benchmark_results_json_is_strict(self):
        from forgelm.benchmark import _save_benchmark_json

        with tempfile.TemporaryDirectory() as tmp:
            _save_benchmark_json(tmp, ["t"], {"t": float("nan")}, float("nan"), False, None, None)
            raw = open(os.path.join(tmp, "benchmark_results.json"), encoding="utf-8").read()
        assert strict_loads(raw)["average_score"] == "nan"

    def test_the_benchmark_artefact_says_why_it_failed(self):
        from forgelm.benchmark import _save_benchmark_json

        with tempfile.TemporaryDirectory() as tmp:
            _save_benchmark_json(tmp, ["t"], {"t": 0.2}, 0.2, False, None, None, "Average 0.2 is below minimum 0.5")
            parsed = strict_loads(open(os.path.join(tmp, "benchmark_results.json"), encoding="utf-8").read())
        assert parsed["passed"] is False and parsed["failure_reason"] == "Average 0.2 is below minimum 0.5"

    def test_a_finite_run_is_unchanged(self):
        """The sanitiser must not perturb the ordinary path."""
        from forgelm.benchmark import _save_benchmark_json

        with tempfile.TemporaryDirectory() as tmp:
            _save_benchmark_json(tmp, ["t"], {"t": 0.75}, 0.75, True, 5, None)
            parsed = strict_loads(open(os.path.join(tmp, "benchmark_results.json"), encoding="utf-8").read())
        assert parsed["scores"] == {"t": 0.75}
        assert parsed["average_score"] == 0.75
        assert math.isclose(parsed["average_score"], 0.75)


class TestLegacyNonFiniteArtefactStillVerifies:
    """An artefact stamped before non-finite values were written as strings is not "modified".

    The strict encoding hashes ``"nan"``; a release before it hashed the bare ``NaN``
    token and wrote that token to disk. Verifying such a file with only the new digest
    reports "modified after generation" — exit 6, the code operators alarm on — for an
    untouched file. Accepting the legacy digest weakens nothing (it binds the same
    content); a real edit must still match neither.
    """

    @staticmethod
    def _legacy_artefact(tmp_path):
        """What the pre-strict writer produced: ``NaN`` on disk, digest over the raw float."""
        from forgelm.compliance import _manifest_json_default, build_annex_iv_artifact, compute_annex_iv_manifest_hash

        manifest = {
            "forgelm_version": "0",
            "generated_at": "now",
            "config_hash": "sha256:abc",
            "model_lineage": {"base_model": "m"},
            "training_parameters": {"trainer_type": "sft", "epochs": 1},
            "data_provenance": {"primary_dataset": "ds"},
            "evaluation_results": {"metrics": {"eval_loss": float("nan")}},
            "annex_iv": {
                "provider_name": "Acme",
                "system_name": "Bot",
                "intended_purpose": "QA",
                "system_version": "1.0",
                "risk_classification": "minimal-risk",
            },
        }
        artefact = build_annex_iv_artifact(manifest)
        artefact["metadata"] = {"manifest_hash": compute_annex_iv_manifest_hash(artefact, legacy_non_finite=True)}
        path = tmp_path / "annex_iv_metadata.json"
        path.write_text(json.dumps(artefact, indent=2, default=_manifest_json_default), encoding="utf-8")
        assert "NaN" in path.read_text(encoding="utf-8"), "premise: the legacy file carries the bare token"
        return path

    def test_an_untouched_legacy_artefact_verifies_and_says_so(self, tmp_path):
        from forgelm.verify import verify_annex_iv_artifact

        result = verify_annex_iv_artifact(str(self._legacy_artefact(tmp_path)))
        assert result.valid, result.reason
        assert "re-export" in result.reason

    def test_a_tampered_legacy_artefact_still_fails(self, tmp_path):
        from forgelm.verify import verify_annex_iv_artifact

        path = self._legacy_artefact(tmp_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        data["system_identification"]["provider_name"] = "Mallory"
        path.write_text(json.dumps(data), encoding="utf-8")
        result = verify_annex_iv_artifact(str(path))
        assert not result.valid and "hash mismatch" in result.reason.lower()

    def test_a_fresh_strict_artefact_is_not_reported_as_legacy(self, tmp_path):
        from forgelm.compliance import export_compliance_artifacts
        from forgelm.verify import verify_annex_iv_artifact

        manifest = {
            "forgelm_version": "0",
            "generated_at": "now",
            "config_hash": "sha256:abc",
            "model_lineage": {"base_model": "m", "adapter_method": "LoRA r=8"},
            "training_parameters": {"trainer_type": "sft", "epochs": 1},
            "data_provenance": {"primary_dataset": "ds"},
            "evaluation_results": {"metrics": {"eval_loss": float("nan")}},
            "annex_iv": {"provider_name": "Acme", "system_name": "Bot", "intended_purpose": "QA"},
        }
        export_compliance_artifacts(manifest, str(tmp_path / "out"))
        result = verify_annex_iv_artifact(str(tmp_path / "out" / "annex_iv_metadata.json"))
        assert result.valid and "re-export" not in result.reason

    def test_a_legacy_pipeline_manifest_also_verifies(self):
        from forgelm.compliance import (
            _verify_manifest_payload,
            compute_annex_iv_manifest_hash,
            generate_pipeline_manifest,
        )
        from tests.test_pipeline_compliance import _root_with_compliance, _three_stage_state

        state = _three_stage_state()
        state.stages[1].metrics["eval_loss"] = float("nan")
        manifest = generate_pipeline_manifest(state, _root_with_compliance())
        manifest["metadata"] = {"manifest_hash": compute_annex_iv_manifest_hash(manifest, legacy_non_finite=True)}
        assert _verify_manifest_payload(manifest) == []
        manifest["stages"][0]["metrics"]["eval_loss"] = 0.0001  # a real edit
        assert any("manifest hash mismatch" in v for v in _verify_manifest_payload(manifest))


class TestFailureEnvelopeCarriesTheReason:
    """`success: false` with no `error` tells automation nothing.

    `TrainResult.error` already carried the gate's computed reason — the
    loss-gate threshold breach, the benchmark verdict, the safety failure —
    and `_build_result_json_envelope` simply never read it. Every gate exits
    3, so the exit code cannot say which one fired either; the cause existed
    only in log text a JSON consumer does not parse.
    """

    @staticmethod
    def _envelope(**kw):
        from forgelm.cli._result import _build_result_json_envelope
        from forgelm.results import TrainResult

        return _build_result_json_envelope(TrainResult(**kw))

    def test_failure_carries_the_producer_reason(self):
        env = self._envelope(success=False, metrics={}, error="eval_loss 3.2000 exceeded max 2.0000")
        assert env["success"] is False
        assert env["error"] == "eval_loss 3.2000 exceeded max 2.0000"

    def test_failure_without_a_reason_says_so_rather_than_omitting_the_key(self):
        """The silent shape is what made this invisible; it must not recur.

        Omitting `error` when no producer set one would leave the envelope
        byte-identical to the bug, so an unattributed failure is stated
        explicitly and points at the audit trail.
        """
        env = self._envelope(success=False, metrics={})
        assert "error" in env
        assert "no failure reason was recorded" in env["error"]
        assert "audit_log.jsonl" in env["error"]

    def test_success_envelope_shape_is_unchanged(self):
        """No permanent `"error": null` on the happy path."""
        env = self._envelope(success=True, metrics={"eval_loss": 1.0}, final_model_path="/m")
        assert "error" not in env

    def test_the_failure_envelope_is_strict_json(self):
        """The two S2 halves compose: a reason, and bytes a strict parser reads."""
        from forgelm._strict_json import dumps_strict

        env = self._envelope(success=False, metrics={"eval_loss": float("nan")}, error="training diverged")
        parsed = strict_loads(dumps_strict(env, default=str))
        assert parsed["error"] == "training diverged"
        assert parsed["metrics"]["eval_loss"] == "nan"


class TestJudgeVerdictIsVisible:
    """A failed judge gate must read as one, everywhere the other gates do.

    The result envelope carried ``judge: {average_score}`` and nothing else, while
    ``benchmark`` and ``safety`` both carry ``passed``. With ``auto_revert: false``
    (the shipped default) a judge gate that failed on a sliver of valid scores left
    ``success: true`` beside a healthy-looking average — nothing in the envelope said
    the gate had not been satisfied.
    """

    def test_the_envelope_carries_the_verdict(self):
        from forgelm.cli._result import _build_result_json_envelope
        from forgelm.results import TrainResult

        failed = _build_result_json_envelope(TrainResult(success=True, metrics={}, judge_score=9.0, judge_passed=False))
        assert failed["judge"] == {"average_score": 9.0, "passed": False}
        passed = _build_result_json_envelope(TrainResult(success=True, metrics={}, judge_score=8.0, judge_passed=True))
        assert passed["judge"]["passed"] is True

    def test_no_judge_run_adds_no_block(self):
        from forgelm.cli._result import _build_result_json_envelope
        from forgelm.results import TrainResult

        assert "judge" not in _build_result_json_envelope(TrainResult(success=True, metrics={}))

    def test_the_trainer_records_the_verdict_and_audits_the_reason(self, tmp_path):
        from types import SimpleNamespace

        from forgelm.results import TrainResult

        trainer = TestRevertClaimsOnlyWhatWasDeleted._trainer(tmp_path, auto_revert=False)
        judge = SimpleNamespace(
            passed=False,
            average_score=9.0,
            details=[],
            failure_reason="Insufficient valid judge evidence: only 1/200",
        )
        result = TrainResult(success=True, metrics={})
        assert trainer._apply_judge_result(judge, result, {}, str(tmp_path / "model")) is True  # kept: auto_revert off
        assert result.judge_passed is False
        call = next(c for c in trainer.audit.log_event.call_args_list if c.args[0] == "judge.evaluation_completed")
        assert call.kwargs["passed"] is False
        assert "Insufficient valid judge evidence" in call.kwargs["failure_reason"]

    def test_the_artefact_says_why_it_failed(self, tmp_path):
        from forgelm.judge import _save_judge_results

        _save_judge_results(str(tmp_path), 9.0, 8.0, False, 200, [], failure_reason="Insufficient valid judge evidence")
        written = strict_loads((tmp_path / "judge_results.json").read_text(encoding="utf-8"))
        assert written["passed"] is False
        assert written["failure_reason"] == "Insufficient valid judge evidence"

    def test_a_passing_artefact_has_a_null_reason(self, tmp_path):
        from forgelm.judge import _save_judge_results

        _save_judge_results(str(tmp_path), 9.0, 8.0, True, 200, [])
        assert strict_loads((tmp_path / "judge_results.json").read_text(encoding="utf-8"))["failure_reason"] is None


class TestRevertedFlagIsDerivedNotAssumed:
    """`reverted: true` must mean artefacts were deleted.

    The gate-failure `TrainResult` hardcoded `reverted=True`, which is false on
    every path that fails without deleting anything — `auto_revert: false`
    (detection-only, the *shipped default*) and the invalid-threshold branch.
    An envelope claiming a revert that did not happen sends an operator
    looking for artefacts that are still on disk, and an auditor reading the
    audit trail to the wrong conclusion.

    The same path also left `error` unset, so S2d's "no failure reason was
    recorded" fallback fired for a failure whose cause is known exactly.
    """

    @staticmethod
    def _trainer(auto_revert):
        from unittest.mock import MagicMock, patch

        from forgelm.config import ForgeConfig
        from forgelm.trainer import ForgeTrainer

        config = ForgeConfig(
            model={"name_or_path": "org/m"},
            lora={},
            training={"output_dir": "/tmp/test_reverted_flag"},
            data={"dataset_name_or_path": "org/d"},
            evaluation={"auto_revert": auto_revert},
        )
        config.evaluation.max_acceptable_loss = float("nan")
        with patch("forgelm.trainer.WebhookNotifier"):
            trainer = ForgeTrainer.__new__(ForgeTrainer)
            trainer.config = config
            trainer.dataset = {"train": ["x"], "validation": ["x"]}
            trainer.checkpoint_dir = "/tmp/test_reverted_flag"
            trainer.run_name = "r"
            trainer.notifier = MagicMock()
            trainer.audit = MagicMock()
        return trainer

    def test_detection_only_failure_does_not_claim_a_revert(self, tmp_path):
        final = tmp_path / "model"
        final.mkdir()
        trainer = self._trainer(auto_revert=False)
        assert trainer.execute_evaluation_checks(str(final), {"eval_loss": 1.0}) is False
        # Direct attribute access, not ``getattr(..., default)``: the gate sets
        # the flag on every invocation, and an absent attribute must fail loudly.
        assert trainer._loss_gate_reverted is False, "nothing was deleted, so nothing may claim it was"
        assert final.exists()

    def test_a_real_revert_sets_the_flag(self, tmp_path):
        """Runs the real ``_revert_model``: the flag is the trainer's, not the test's."""
        final = tmp_path / "model"
        final.mkdir()
        trainer = self._trainer(auto_revert=True)
        assert trainer.execute_evaluation_checks(str(final), {"eval_loss": 1.0}) is False
        assert trainer._loss_gate_reverted is True
        assert not final.exists(), "the flag claims a deletion that did not happen"

    def test_the_failure_reason_survives_a_non_reverting_failure(self):
        """Without this the envelope falls back to "no reason recorded"."""
        trainer = self._trainer(auto_revert=False)
        trainer.execute_evaluation_checks("/tmp/nonexistent", {"eval_loss": 1.0})
        reason = getattr(trainer, "_last_revert_reason", None)
        assert reason and "max_acceptable_loss" in reason
        assert "not a finite number" in reason


class TestRevertClaimsOnlyWhatWasDeleted:
    """`reverted: true` and "Artifacts discarded" must follow the delete, not the intent.

    `_revert_model` set the flag and wrote the audit event *before* `shutil.rmtree`,
    whose `OSError` was then only logged. A busy file or a permissions error therefore
    produced `reverted: true`, `final_model_path: null` and a webhook announcing the
    artefacts were discarded — over a model still sitting on disk. The audit log is
    append-only, so the earlier `model.reverted` cannot be taken back; a second
    `model.revert_failed` record is the only honest way to correct it.
    """

    @staticmethod
    def _trainer(tmp_path, auto_revert=True):
        from unittest.mock import MagicMock, patch

        from forgelm.config import ForgeConfig
        from forgelm.trainer import ForgeTrainer

        config = ForgeConfig(
            model={"name_or_path": "org/m"},
            lora={},
            training={"output_dir": str(tmp_path)},
            data={"dataset_name_or_path": "org/d"},
            evaluation={"auto_revert": auto_revert},
        )
        config.evaluation.max_acceptable_loss = float("nan")  # forces the gate to fail without a model
        with patch("forgelm.trainer.WebhookNotifier"):
            trainer = ForgeTrainer.__new__(ForgeTrainer)
            trainer.config = config
            trainer.dataset = {"train": ["x"], "validation": ["x"]}
            trainer.checkpoint_dir = str(tmp_path)
            trainer.run_name = "r"
            trainer.notifier = MagicMock()
            trainer.audit = MagicMock()
            trainer.trainer = MagicMock()
            trainer.trainer.evaluate.return_value = {"eval_loss": 1.0}
        return trainer

    @staticmethod
    def _failing_rmtree(*_a, **_k):
        raise OSError("device or resource busy")

    @staticmethod
    def _events(trainer):
        return [call.args[0] for call in trainer.audit.log_event.call_args_list]

    def test_a_failed_delete_is_not_reported_as_a_revert(self, tmp_path):
        from unittest.mock import patch

        final = tmp_path / "model"
        final.mkdir()
        trainer = self._trainer(tmp_path)
        with patch("forgelm.trainer.shutil.rmtree", side_effect=self._failing_rmtree):
            assert trainer.execute_evaluation_checks(str(final), {"eval_loss": 1.0}) is False
        assert trainer._loss_gate_reverted is False
        assert final.exists()
        trainer.notifier.notify_reverted.assert_not_called()  # "Artifacts discarded." would be false
        trainer.notifier.notify_failure.assert_called_once()
        assert "could NOT delete" in trainer.notifier.notify_failure.call_args.kwargs["reason"]

    def test_a_failed_delete_leaves_a_retraction_in_the_audit_log(self, tmp_path):
        from unittest.mock import patch

        final = tmp_path / "model"
        final.mkdir()
        trainer = self._trainer(tmp_path)
        with patch("forgelm.trainer.shutil.rmtree", side_effect=self._failing_rmtree):
            trainer.execute_evaluation_checks(str(final), {"eval_loss": 1.0})
        events = self._events(trainer)
        # The first record is deliberately written before the delete; the second corrects it.
        assert events.index("model.reverted") < events.index("model.revert_failed")
        failed = next(c for c in trainer.audit.log_event.call_args_list if c.args[0] == "model.revert_failed")
        assert failed.kwargs["path"] == str(final) and failed.kwargs["error_class"] == "OSError"

    def test_a_successful_delete_still_reports_a_revert(self, tmp_path):
        """The negative control: the retraction path must not fire on the ordinary one."""
        final = tmp_path / "model"
        final.mkdir()
        trainer = self._trainer(tmp_path)
        assert trainer.execute_evaluation_checks(str(final), {"eval_loss": 1.0}) is False
        assert trainer._loss_gate_reverted is True and not final.exists()
        assert "model.revert_failed" not in self._events(trainer)
        trainer.notifier.notify_reverted.assert_called_once()
        trainer.notifier.notify_failure.assert_not_called()

    @pytest.mark.parametrize("gate", ["benchmark", "safety", "judge"])
    def test_the_other_gates_keep_the_paths_when_nothing_was_deleted(self, tmp_path, gate):
        from types import SimpleNamespace
        from unittest.mock import patch

        from forgelm.results import TrainResult

        final = tmp_path / "model"
        final.mkdir()
        trainer = self._trainer(tmp_path)
        verdict = {"passed": False, "failure_reason": f"{gate} gate failed", "average_score": 0.1}
        apply, result = {
            "benchmark": (trainer._apply_benchmark_result, SimpleNamespace(scores={}, **verdict)),
            "judge": (trainer._apply_judge_result, SimpleNamespace(details=[], **verdict)),
            "safety": (
                trainer._apply_safety_result,
                SimpleNamespace(
                    safe_ratio=0.0,
                    safety_score=0.0,
                    unsafe_count=10,
                    total_count=10,
                    category_distribution={},
                    severity_distribution={},
                    low_confidence_count=0,
                    evaluation_completed=True,
                    **verdict,
                ),
            ),
        }[gate]
        train_result = TrainResult(success=True, metrics={}, final_model_path=str(final))
        with patch("forgelm.trainer.shutil.rmtree", side_effect=self._failing_rmtree):
            assert apply(result, train_result, {}, str(final)) is False
        assert train_result.success is False
        assert train_result.reverted is False
        assert train_result.final_model_path == str(final), "the artefacts are on disk; the envelope must say where"
        assert "could not delete" in train_result.error

    def test_the_other_gates_still_clear_the_paths_after_a_real_delete(self, tmp_path):
        from types import SimpleNamespace

        from forgelm.results import TrainResult

        final = tmp_path / "model"
        final.mkdir()
        trainer = self._trainer(tmp_path)
        judge = SimpleNamespace(passed=False, failure_reason="below min", average_score=1.0, details=[])
        train_result = TrainResult(success=True, metrics={}, final_model_path=str(final))
        assert trainer._apply_judge_result(judge, train_result, {}, str(final)) is False
        assert train_result.reverted is True and train_result.final_model_path is None and not final.exists()

    def test_a_gate_failure_without_auto_revert_keeps_final_model_path(self, tmp_path):
        """The model is intact on disk, so the envelope must not report ``final_model_path: null``."""
        from types import SimpleNamespace
        from unittest.mock import patch

        trainer = self._trainer(tmp_path, auto_revert=False)
        with (
            patch.object(trainer, "_measure_baseline_loss"),
            patch.object(trainer, "_run_with_oom_recovery", return_value=SimpleNamespace(metrics={})),
            patch.object(trainer, "save_final_model"),
        ):
            result = trainer._run_training_pipeline(None)
        assert result.success is False and result.reverted is False
        assert result.final_model_path == str(tmp_path / "final_model")
