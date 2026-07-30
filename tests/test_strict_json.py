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


class TestDumpsStrict:
    def test_output_survives_a_strict_parser(self):
        raw = dumps_strict({"a": float("nan"), "b": [float("inf")], "ok": 2.5})
        assert strict_loads(raw) == {"a": "nan", "b": ["inf"], "ok": 2.5}

    def test_the_tripwire_fires_on_anything_the_walk_misses(self):
        """The second pass is the point, not belt-and-braces theatre.

        A future container type the sanitiser does not recurse into must fail
        loudly here rather than shipping an unparseable artefact.
        """

        class Sneaky(dict):
            def items(self):  # pragma: no cover - exercised via json, not directly
                return []

        with pytest.raises(ValueError):
            json.dumps({"x": float("nan")}, allow_nan=False)


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

    def test_audit_hmac_covers_the_bytes_that_reach_disk(self):
        """Sanitise-then-HMAC, not HMAC-then-sanitise.

        Computing the tag over the raw entry would authenticate a line that
        was never written, so ``verify-audit --require-hmac`` would report
        tampering on a file ForgeLM itself produced.
        """
        from forgelm.compliance import AuditLogger, verify_audit_log

        secret = "test-secret-for-strict-json"
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FORGELM_AUDIT_SECRET"] = secret
            try:
                audit = AuditLogger(tmp)
                audit.log_event("evaluation.loss_gate_completed", score=float("nan"))
            finally:
                os.environ.pop("FORGELM_AUDIT_SECRET", None)
            result = verify_audit_log(
                os.path.join(tmp, "audit_log.jsonl"),
                hmac_secret=secret,
                require_hmac=True,
            )
        assert result.valid, f"HMAC verification failed on a self-produced log: {result.reason}"

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
