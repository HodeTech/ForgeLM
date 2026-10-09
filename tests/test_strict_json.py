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

    def test_a_finite_run_is_unchanged(self):
        """The sanitiser must not perturb the ordinary path."""
        from forgelm.benchmark import _save_benchmark_json

        with tempfile.TemporaryDirectory() as tmp:
            _save_benchmark_json(tmp, ["t"], {"t": 0.75}, 0.75, True, 5, None)
            parsed = strict_loads(open(os.path.join(tmp, "benchmark_results.json"), encoding="utf-8").read())
        assert parsed["scores"] == {"t": 0.75}
        assert parsed["average_score"] == 0.75
        assert math.isclose(parsed["average_score"], 0.75)


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
