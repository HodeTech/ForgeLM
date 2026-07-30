"""Hub revision pinning for the safety classifier (forgelm.safety).

The safety classifier decides the auto-revert verdict.  An upstream re-tune
moves the pass/fail line with no config diff to point at, so two runs of the
same YAML can promote and block the same model.  These tests assert the pin
reaches *both* scoring paths' loads, and that provenance is recorded only
after a load succeeds.

The broader safety-evaluation behaviour lives in ``tests/test_safety_advanced.py``;
this module is scoped to the revision contract.

No network, no GPU: transformers entry points are mocked at the import
boundary and the revision resolver is stubbed, per docs/standards/testing.md.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from forgelm import model as model_mod
from forgelm import safety as safety_mod

SHA = "0" * 39 + "a"


@pytest.fixture(autouse=True)
def _clean_registry():
    model_mod._RESOLVED_MODEL_REVISIONS.clear()
    yield
    model_mod._RESOLVED_MODEL_REVISIONS.clear()


@pytest.fixture
def stub_resolver(monkeypatch):
    """Stub ``resolve_model_revision`` so no Hub traffic is possible."""

    def _install(**overrides):
        from forgelm import compliance as compliance_mod

        seen = {}

        def _fake(repo_id, *, requested=None, offline=False):
            seen["requested"] = requested
            record = {
                "repo_id": repo_id,
                "revision_requested": requested,
                "revision_resolved": None,
                "resolution_source": "unresolved",
            }
            record.update(overrides)
            return record

        monkeypatch.setattr(compliance_mod, "resolve_model_revision", _fake)
        return seen

    return _install


class TestGenerativeGuardPin:
    """``_load_generative_guard`` pins the guard weights and its tokenizer alike."""

    def _fake_transformers(self, captured, fail_model=False):
        def _tok(path, **kwargs):
            captured["tokenizer"] = kwargs.get("revision")
            return MagicMock()

        def _model(path, **kwargs):
            captured["model"] = kwargs.get("revision")
            if fail_model:
                raise OSError("hub down")
            return MagicMock()

        fake = MagicMock()
        fake.AutoTokenizer.from_pretrained = _tok
        fake.AutoModelForCausalLM.from_pretrained = _model
        return fake

    def test_resolved_sha_reaches_both_loads(self, stub_resolver):
        stub_resolver(revision_resolved=SHA, resolution_source="pinned_resolved")
        captured = {}
        with patch("torch.cuda.is_available", return_value=False):
            with patch.dict("sys.modules", {"transformers": self._fake_transformers(captured)}):
                safety_mod._load_generative_guard("meta/guard", None, SHA)
        assert captured["tokenizer"] == SHA
        assert captured["model"] == SHA

    def test_configured_revision_is_what_gets_resolved(self, stub_resolver):
        seen = stub_resolver(revision_resolved=SHA, resolution_source="pinned_resolved")
        captured = {}
        with patch("torch.cuda.is_available", return_value=False):
            with patch.dict("sys.modules", {"transformers": self._fake_transformers(captured)}):
                safety_mod._load_generative_guard("meta/guard", None, "v1.0")
        assert seen["requested"] == "v1.0"

    def test_unpinned_load_is_unchanged(self, stub_resolver):
        stub_resolver(resolution_source="unresolved")
        captured = {}
        with patch("torch.cuda.is_available", return_value=False):
            with patch.dict("sys.modules", {"transformers": self._fake_transformers(captured)}):
                safety_mod._load_generative_guard("meta/guard", None)
        assert captured["tokenizer"] is None
        assert captured["model"] is None

    def test_provenance_recorded_only_after_a_successful_load(self, stub_resolver):
        stub_resolver(revision_resolved=SHA, resolution_source="pinned_resolved")
        captured = {}
        with patch("torch.cuda.is_available", return_value=False):
            with patch.dict("sys.modules", {"transformers": self._fake_transformers(captured, fail_model=True)}):
                with pytest.raises(RuntimeError):
                    safety_mod._load_generative_guard("meta/guard", None, SHA)
        assert model_mod.get_loaded_model_revision("meta/guard", model_mod.ROLE_SAFETY_CLASSIFIER) is None

    def test_successful_load_is_recorded_under_the_classifier_role(self, stub_resolver):
        stub_resolver(revision_resolved=SHA, resolution_source="pinned_resolved")
        with patch("torch.cuda.is_available", return_value=False):
            with patch.dict("sys.modules", {"transformers": self._fake_transformers({})}):
                safety_mod._load_generative_guard("meta/guard", None, SHA)
        record = model_mod.get_loaded_model_revision("meta/guard", model_mod.ROLE_SAFETY_CLASSIFIER)
        assert record["revision_resolved"] == SHA
        # Never under base_model: a classifier contributed no weights to the
        # fine-tuned model and must not appear in its lineage.
        assert model_mod.get_loaded_model_revision("meta/guard") is None


class TestGuardChatTemplatePreflight:
    """``_reject_guard_without_chat_template`` — defence-in-depth at guard load.

    Generation-based scoring builds every moderation prompt with
    ``tokenizer.apply_chat_template``.  With no template that call raises on
    every pair, each failure is swallowed into ``""``, and ``""`` parses as a
    malformed fail-closed verdict — so the run completes reporting 100% unsafe
    and (with ``auto_revert`` on) deletes a model that may be fine, with
    nothing in the operator's output naming the cause.  The pre-flight turns
    that into one actionable error.

    Both directions are asserted: it fires on a positively template-less
    tokenizer, and it stays silent on every legitimate tokenizer shape.
    """

    def _tokenizer(self, **attrs):
        """Tokenizer double with exactly the attributes named, nothing else.

        A ``MagicMock`` auto-creates every attribute as truthy, which would
        mask a check that fires too eagerly — so the negative cases need a
        real object with a controlled attribute surface.  Attributes are set on
        the *instance*, not the class, so a callable stays a plain function
        instead of becoming a bound method that swallows an argument.
        """
        tok = type("_Tok", (), {})()
        for name, value in attrs.items():
            setattr(tok, name, value)
        return tok

    # --- fires closed -------------------------------------------------------

    @pytest.mark.parametrize("empty", [None, "", {}])
    def test_absent_template_is_refused(self, empty):
        tok = self._tokenizer(chat_template=empty)
        with pytest.raises(RuntimeError) as ei:
            safety_mod._reject_guard_without_chat_template(tok, "acme/not-a-guard")
        msg = str(ei.value)
        assert "chat template" in msg
        # Actionable per error-handling.md: names the config key to change.
        assert "classifier_mode" in msg
        assert "acme/not-a-guard" in msg

    def test_getter_reporting_no_template_is_refused(self):
        def _raise():
            raise ValueError("This tokenizer does not have a chat template")

        tok = self._tokenizer(chat_template=None, get_chat_template=_raise)
        with pytest.raises(RuntimeError):
            safety_mod._reject_guard_without_chat_template(tok, "acme/not-a-guard")

    def test_refusal_reaches_the_loader_as_runtime_error_with_audit(self, stub_resolver):
        """The pre-flight must ride the loader's existing failure contract:
        Article 15 ``audit.classifier_load_failed`` event, then RuntimeError."""
        stub_resolver(revision_resolved=SHA, resolution_source="pinned_resolved")
        events = []

        class _Audit:
            def log_event(self, name, **kw):
                events.append((name, kw))

        def _tok(path, **kwargs):
            return type("_Tok", (), {"chat_template": None})()

        fake = MagicMock()
        fake.AutoTokenizer.from_pretrained = _tok
        fake.AutoModelForCausalLM.from_pretrained = lambda *a, **k: pytest.fail(
            "weights must not be downloaded after the tokenizer pre-flight refuses"
        )
        with patch("torch.cuda.is_available", return_value=False):
            with patch.dict("sys.modules", {"transformers": fake}):
                with pytest.raises(RuntimeError, match="chat template"):
                    safety_mod._load_generative_guard("acme/not-a-guard", _Audit(), SHA)
        assert [n for n, _ in events] == ["audit.classifier_load_failed"]
        # Provenance must NOT be recorded for a guard that was refused.
        assert model_mod.get_loaded_model_revision("acme/not-a-guard", model_mod.ROLE_SAFETY_CLASSIFIER) is None

    # --- stays silent -------------------------------------------------------

    def test_real_template_passes(self):
        tok = self._tokenizer(chat_template="{% for m in messages %}{{ m.content }}{% endfor %}")
        safety_mod._reject_guard_without_chat_template(tok, "meta-llama/Llama-Guard-3-8B")

    def test_template_supplied_only_via_getter_passes(self):
        tok = self._tokenizer(chat_template=None, get_chat_template=lambda: "{{ messages }}")
        safety_mod._reject_guard_without_chat_template(tok, "meta-llama/Llama-Guard-3-8B")

    def test_structurally_unqueryable_getter_abstains(self):
        """A getter that fails on its *signature* has not answered "no template".

        Treating a TypeError as a negative answer would refuse any tokenizer
        whose ``get_chat_template`` takes a required argument — a false alarm
        on a load that would have worked. "We could not ask" != "the answer
        was no".
        """

        def _needs_an_argument(which):  # pragma: no cover - never called successfully
            return "{{ messages }}"

        tok = self._tokenizer(chat_template=None, get_chat_template=_needs_an_argument)
        safety_mod._reject_guard_without_chat_template(tok, "acme/custom-guard")

    def test_tokenizer_exposing_neither_api_abstains(self):
        """A tokenizer with neither attribute is undetermined, not template-less.

        Custom/stubbed tokenizers whose ``apply_chat_template`` works fine live
        here; refusing them would be a false alarm on a legitimate load.
        """
        safety_mod._reject_guard_without_chat_template(self._tokenizer(), "acme/custom-guard")

    def test_magicmock_tokenizer_is_not_refused(self):
        """Guards the whole existing test suite: every other generative-guard
        test passes a ``MagicMock`` tokenizer.  If the pre-flight fired on
        those, this fix would have traded a false-PASS for a false-FAIL across
        the suite — exactly the near-miss this cycle keeps producing."""
        safety_mod._reject_guard_without_chat_template(MagicMock(), "meta-llama/Llama-Guard-3-8B")


class TestClassificationPipelinePin:
    """``_load_safety_classifier`` pins the ``text-classification`` pipeline."""

    def _fake_pipeline(self, captured):
        def _pipeline(task, **kwargs):
            captured["revision"] = kwargs.get("revision")
            clf = MagicMock()
            clf.model.config.architectures = ["BertForSequenceClassification"]
            clf.model.config.id2label = {0: "safe", 1: "unsafe"}
            return clf

        fake = MagicMock()
        fake.pipeline = _pipeline
        return fake

    def test_revision_reaches_the_pipeline(self, stub_resolver):
        stub_resolver(revision_resolved=SHA, resolution_source="pinned_resolved")
        captured = {}
        with patch.dict("sys.modules", {"transformers": self._fake_pipeline(captured)}):
            safety_mod._load_safety_classifier("acme/harm-classifier", None, SHA)
        assert captured["revision"] == SHA

    def test_unpinned_pipeline_load_is_unchanged(self, stub_resolver):
        stub_resolver(resolution_source="unresolved")
        captured = {}
        with patch.dict("sys.modules", {"transformers": self._fake_pipeline(captured)}):
            safety_mod._load_safety_classifier("acme/harm-classifier", None)
        assert captured["revision"] is None


class TestRunSafetyEvaluationThreadsTheRevision:
    """The public entry point forwards ``classifier_revision`` to whichever
    scoring path runs — dropping it on either branch leaves the gate unpinned
    while the config claims otherwise."""

    def _probes(self, tmp_path):
        import json

        probes = tmp_path / "probes.jsonl"
        probes.write_text(json.dumps({"prompt": "hi"}) + "\n")
        return str(probes)

    def _neutralize(self, monkeypatch):
        monkeypatch.setattr(safety_mod._orchestrator, "_generate_safety_responses", lambda *a, **k: ["ok"])
        monkeypatch.setattr(safety_mod._orchestrator, "_release_model_from_gpu", lambda *a, **k: None)

    def test_generation_path_receives_the_revision(self, tmp_path, monkeypatch):
        self._neutralize(monkeypatch)
        seen = {}

        def _fake_generative(path, prompts, responses, thresholds, audit, revision=None):
            seen["revision"] = revision
            return {
                "unsafe_count": 0,
                "low_confidence_count": 0,
                "confidence_scores": [1.0],
                "category_dist": {},
                "severity_dist": {level: 0 for level in safety_mod.SEVERITY_LEVELS},
                "details": [],
            }

        monkeypatch.setattr(safety_mod._orchestrator, "_classify_responses_generative", _fake_generative)
        safety_mod.run_safety_evaluation(
            model=MagicMock(),
            tokenizer=MagicMock(),
            classifier_path="meta-llama/Llama-Guard-3-8B",
            test_prompts_path=self._probes(tmp_path),
            output_dir=str(tmp_path / "out"),
            classifier_revision=SHA,
        )
        assert seen["revision"] == SHA

    def test_classification_path_receives_the_revision(self, tmp_path, monkeypatch):
        self._neutralize(monkeypatch)
        seen = {}

        def _fake_load(path, audit, revision=None):
            seen["revision"] = revision
            return MagicMock()

        monkeypatch.setattr(safety_mod._orchestrator, "_load_safety_classifier", _fake_load)
        monkeypatch.setattr(
            safety_mod._orchestrator,
            "_classify_responses",
            lambda *a, **k: {
                "unsafe_count": 0,
                "low_confidence_count": 0,
                "confidence_scores": [1.0],
                "category_dist": {},
                "severity_dist": {level: 0 for level in safety_mod.SEVERITY_LEVELS},
                "details": [],
            },
        )
        safety_mod.run_safety_evaluation(
            model=MagicMock(),
            tokenizer=MagicMock(),
            classifier_path="acme/harm-classifier",
            test_prompts_path=self._probes(tmp_path),
            output_dir=str(tmp_path / "out"),
            classifier_revision=SHA,
        )
        assert seen["revision"] == SHA

    def test_default_is_unpinned_so_existing_callers_are_unaffected(self, tmp_path, monkeypatch):
        self._neutralize(monkeypatch)
        seen = {}

        def _fake_load(path, audit, revision=None):
            seen["revision"] = revision
            return MagicMock()

        monkeypatch.setattr(safety_mod._orchestrator, "_load_safety_classifier", _fake_load)
        monkeypatch.setattr(
            safety_mod._orchestrator,
            "_classify_responses",
            lambda *a, **k: {
                "unsafe_count": 0,
                "low_confidence_count": 0,
                "confidence_scores": [1.0],
                "category_dist": {},
                "severity_dist": {level: 0 for level in safety_mod.SEVERITY_LEVELS},
                "details": [],
            },
        )
        safety_mod.run_safety_evaluation(
            model=MagicMock(),
            tokenizer=MagicMock(),
            classifier_path="acme/harm-classifier",
            test_prompts_path=self._probes(tmp_path),
            output_dir=str(tmp_path / "out"),
        )
        assert seen["revision"] is None


class TestPublicApiThresholdValidation:
    """`run_safety_evaluation` is public, and its gate parameters bypass Pydantic.

    The training path reaches the safety gate through a validated
    `ForgeConfig`, but `from forgelm.safety import run_safety_evaluation` is a
    stable-tier import and its thresholds arrive as a plain
    `SafetyEvalThresholds` dataclass with no `__post_init__`. Every one of
    those numbers is compared with `<` or `>`, and every such comparison
    against `nan` is False — so an unvalidated threshold does not merely
    misbehave, it makes the gate report PASS for a run that scored 100%
    unsafe, which is the verdict `auto_revert` acts on.

    `severity_thresholds` was unguarded on *both* routes: `Dict[str, float]`
    carries Pydantic's default `allow_inf_nan=True` on the values, so
    `{"critical": .nan}` was reachable from ordinary YAML.
    """

    @staticmethod
    def _validate(max_regression=0.05, **kw):
        from forgelm.safety import SafetyEvalThresholds
        from forgelm.safety._inputs import _validate_thresholds

        _validate_thresholds(max_regression, SafetyEvalThresholds(**kw) if kw else SafetyEvalThresholds())

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), 1.5, -0.1])
    def test_max_safety_regression_must_be_a_finite_rate(self, bad):
        with pytest.raises(ValueError, match="max_safety_regression"):
            self._validate(max_regression=bad)

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), 2.0, -0.5])
    def test_min_safety_score_must_be_a_finite_rate(self, bad):
        with pytest.raises(ValueError, match="min_safety_score"):
            self._validate(min_safety_score=bad)

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), 3.0])
    def test_min_classifier_confidence_must_be_a_finite_rate(self, bad):
        with pytest.raises(ValueError, match="min_classifier_confidence"):
            self._validate(min_classifier_confidence=bad)

    def test_severity_threshold_values_must_be_finite(self):
        with pytest.raises(ValueError, match="severity_thresholds"):
            self._validate(severity_thresholds={"critical": float("nan")})

    def test_severity_threshold_keys_must_be_known_levels(self):
        with pytest.raises(ValueError, match="not a known severity level"):
            self._validate(severity_thresholds={"catastrophic": 0.5})

    def test_scoring_must_be_a_known_mode(self):
        with pytest.raises(ValueError, match="scoring"):
            self._validate(scoring="vibes")

    def test_realistic_configuration_is_accepted(self):
        """The guard must not fire on the ordinary path."""
        self._validate()
        self._validate(
            min_safety_score=0.9,
            scoring="confidence_weighted",
            severity_thresholds={"critical": 0.0, "high": 0.02},
        )

    def test_yaml_route_also_rejects_a_non_finite_severity_threshold(self):
        """The one field that was unguarded on both routes."""
        from pydantic import ValidationError

        from forgelm.config import SafetyConfig

        with pytest.raises(ValidationError, match="finite_number|finite number"):
            SafetyConfig(enabled=True, track_categories=True, severity_thresholds={"critical": float("nan")})


class TestSafetyGateFailsClosedOnNonFiniteMeasurements:
    """The threshold side was guarded in S2b; this is the measured side.

    Every gate in `_evaluate_safety_gates` is a `<` or `>` against a
    measurement, and every such comparison against `nan` is False — so one
    non-finite value does not weaken a gate, it disarms **all** of them at
    once. Measured before the fix: an identical 10/10-unsafe run returns
    `passed=False` with finite inputs and `passed=True` with NaN ones.

    Reachable, not theoretical: `safety_score` is a mean over the classifier's
    own softmax outputs, and a fp16 or quantised guard head can emit NaN. This
    is the verdict `auto_revert` acts on and that `forgelm safety-eval` maps
    to exit 0 versus 3, so it fails rather than abstaining — "the gate could
    not be evaluated" must never read as "the model is safe".
    """

    @staticmethod
    def _gate(**overrides):
        from forgelm.safety._gates import _evaluate_safety_gates

        kwargs = dict(
            safe_ratio=1.0,
            safety_score=1.0,
            severity_dist={},
            total=10,
            unsafe_count=0,
            max_safety_regression=0.05,
            scoring="binary",
            min_safety_score=None,
            severity_thresholds=None,
            track_categories=False,
            log_failures=False,
        )
        kwargs.update(overrides)
        return _evaluate_safety_gates(**kwargs)

    @pytest.mark.parametrize("field", ["safe_ratio", "safety_score", "max_safety_regression"])
    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
    def test_a_non_finite_measurement_fails_the_gate(self, field, bad):
        passed, reason = self._gate(**{field: bad})
        assert passed is False
        assert reason and field in reason
        assert "not a finite number" in reason

    def test_the_worst_case_no_longer_passes(self):
        """The exact reproduction: 10/10 unsafe, NaN measurements, strict floor."""
        passed, reason = self._gate(
            safe_ratio=float("nan"),
            safety_score=float("nan"),
            unsafe_count=10,
            scoring="confidence_weighted",
            min_safety_score=0.999,
        )
        assert passed is False, "a 100%-unsafe run must never report a pass"
        assert reason

    def test_a_non_finite_threshold_also_fails(self):
        passed, reason = self._gate(scoring="confidence_weighted", min_safety_score=float("nan"))
        assert passed is False
        assert "min_safety_score" in reason

    def test_a_clean_run_still_passes(self):
        """The guard must not fire on the ordinary path."""
        passed, reason = self._gate()
        assert passed is True
        assert reason is None

    def test_a_genuinely_unsafe_run_still_fails_for_its_own_reason(self):
        """The pre-existing verdict must not be swallowed by the new branch."""
        passed, reason = self._gate(
            safe_ratio=0.0,
            safety_score=0.0,
            unsafe_count=10,
            scoring="confidence_weighted",
            min_safety_score=0.999,
        )
        assert passed is False
        assert "Unsafe ratio" in reason
        assert "not a finite number" not in reason


class TestLowConfidenceDiagnosticFailsClosed:
    """`confidence < min_classifier_confidence` is False for NaN.

    So a classifier emitting non-finite softmax scores reported **zero**
    low-confidence rows — the diagnostic whose whole job is to say "do not
    trust this verdict" was disarmed by exactly the condition it exists to
    detect.
    """

    @staticmethod
    def _classify(confidence):
        from unittest.mock import MagicMock

        from forgelm.safety._score_classification import _classify_one_response

        classifier = MagicMock(return_value=[{"label": "safe", "score": confidence}])
        return _classify_one_response(
            classifier=classifier,
            prompt="p",
            response="r",
            min_classifier_confidence=0.7,
            track_categories=False,
            category_dist={},
            severity_dist={},
        )

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
    def test_non_finite_confidence_is_low_confidence(self, bad):
        assert self._classify(bad).get("low_confidence") is True

    def test_a_confident_row_is_not_flagged(self):
        assert "low_confidence" not in self._classify(0.98)

    def test_a_genuinely_low_row_is_still_flagged(self):
        assert self._classify(0.4).get("low_confidence") is True


class TestGenerationFailureCannotCertifyAModel:
    """A run whose model responses were never generated must not report a pass.

    This is the end-to-end assertion the package was missing.  Existing tests
    cover a *classifier* that crashes; there was no equivalent for the model
    under test failing to answer, and that direction was the dangerous one.
    A failed generation became ``""``, which enters scoring as an ordinary
    empty assistant turn — and an empty reply to an adversarial probe is what
    a well-aligned model does, so both scorers judge it benign.  The run
    reported ``unscored_count=0``, ``evaluation_completed=True``,
    ``passed=True`` and exit 0: a safety certificate for a model that was
    never asked a question.

    The classifier is deliberately stubbed to answer ``safe`` for everything.
    A test whose classifier says "unsafe" would pass for the wrong reason —
    the point is that the *verdict path is not consulted at all* for a pair
    that has no response.
    """

    def _probes(self, tmp_path, count=4):
        import json

        probes = tmp_path / "probes.jsonl"
        probes.write_text("".join(json.dumps({"prompt": f"probe {i}"}) + "\n" for i in range(count)))
        return str(probes)

    def _always_safe_classifier(self):
        classifier = MagicMock()
        classifier.return_value = [{"label": "safe", "score": 0.99}]
        return classifier

    def _model_that_always_fails(self):
        """A model whose every ``generate`` raises — a dead GPU, in effect."""
        model = MagicMock()
        model.device = "cpu"
        model.generate.side_effect = RuntimeError("CUDA error: device-side assert triggered")
        return model

    def _tokenizer(self):
        tokenizer = MagicMock()
        tokenizer.pad_token = "<pad>"
        tokenizer.eos_token = "<eos>"
        tokenizer.padding_side = "right"
        tokenizer.return_value = {"input_ids": MagicMock()}
        return tokenizer

    def _run(self, tmp_path, monkeypatch, count=4):
        monkeypatch.setattr(safety_mod._orchestrator, "_release_model_from_gpu", lambda *a, **k: None)
        monkeypatch.setattr(
            safety_mod._orchestrator,
            "_load_safety_classifier",
            lambda *a, **k: self._always_safe_classifier(),
        )
        return safety_mod.run_safety_evaluation(
            model=self._model_that_always_fails(),
            tokenizer=self._tokenizer(),
            classifier_path="acme/harm-classifier",
            test_prompts_path=self._probes(tmp_path, count),
            output_dir=str(tmp_path / "out"),
            classifier_mode="classification",
        )

    def test_every_generation_failing_does_not_pass(self, tmp_path, monkeypatch):
        result = self._run(tmp_path, monkeypatch)
        assert result.passed is False, "a run in which no response was ever generated cannot certify the model as safe"

    def test_it_reports_an_evaluation_that_did_not_happen(self, tmp_path, monkeypatch):
        """Not merely `passed=False` — the distinction drives auto-revert.

        `evaluation_completed=False` is what stops the trainer from deleting a
        model on the strength of a measurement that was never taken, and what
        routes `forgelm safety-eval` to exit 2 (infrastructure) instead of
        exit 3 (the gate said no).  Reporting a 100%-unsafe *verdict* here
        would be the mirror-image defect: a dead GPU would delete a good model.
        """
        result = self._run(tmp_path, monkeypatch)
        assert result.evaluation_completed is False
        assert result.failure_reason
        assert "no usable verdict" in result.failure_reason

    def test_every_pair_is_counted_unscored(self, tmp_path, monkeypatch):
        result = self._run(tmp_path, monkeypatch, count=4)
        assert result.unscored_count == 4
        assert result.total_count == 4

    def test_the_classifier_is_never_consulted(self, tmp_path, monkeypatch):
        """Judging a placeholder produces a confident verdict about nothing."""
        classifier = self._always_safe_classifier()
        monkeypatch.setattr(safety_mod._orchestrator, "_release_model_from_gpu", lambda *a, **k: None)
        monkeypatch.setattr(safety_mod._orchestrator, "_load_safety_classifier", lambda *a, **k: classifier)
        safety_mod.run_safety_evaluation(
            model=self._model_that_always_fails(),
            tokenizer=self._tokenizer(),
            classifier_path="acme/harm-classifier",
            test_prompts_path=self._probes(tmp_path),
            output_dir=str(tmp_path / "out"),
            classifier_mode="classification",
        )
        classifier.assert_not_called()

    def test_a_genuinely_empty_response_is_still_scored(self, tmp_path, monkeypatch):
        """The negative control, and the reason a sentinel type was needed.

        A model that *answers* with an empty string has told us something
        real — arguably the ideal answer to an adversarial probe.  It must
        still be scored normally, not swept into the unscored bucket, or the
        fix would trade a false PASS for a false "cannot evaluate" on every
        well-behaved model.
        """
        from forgelm.safety._types import GeneratedResponse

        monkeypatch.setattr(safety_mod._orchestrator, "_release_model_from_gpu", lambda *a, **k: None)
        monkeypatch.setattr(
            safety_mod._orchestrator,
            "_load_safety_classifier",
            lambda *a, **k: self._always_safe_classifier(),
        )
        monkeypatch.setattr(
            safety_mod._orchestrator,
            "_generate_safety_responses",
            lambda *a, **k: [GeneratedResponse(text="") for _ in range(4)],
        )
        result = safety_mod.run_safety_evaluation(
            model=MagicMock(),
            tokenizer=self._tokenizer(),
            classifier_path="acme/harm-classifier",
            test_prompts_path=self._probes(tmp_path),
            output_dir=str(tmp_path / "out"),
            classifier_mode="classification",
        )
        assert result.unscored_count == 0
        assert result.evaluation_completed is True
        assert result.passed is True
