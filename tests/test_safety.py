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
        monkeypatch.setattr(safety_mod, "_generate_safety_responses", lambda *a, **k: ["ok"])
        monkeypatch.setattr(safety_mod, "_release_model_from_gpu", lambda *a, **k: None)

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

        monkeypatch.setattr(safety_mod, "_classify_responses_generative", _fake_generative)
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

        monkeypatch.setattr(safety_mod, "_load_safety_classifier", _fake_load)
        monkeypatch.setattr(
            safety_mod,
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

        monkeypatch.setattr(safety_mod, "_load_safety_classifier", _fake_load)
        monkeypatch.setattr(
            safety_mod,
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
