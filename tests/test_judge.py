"""Hub revision pinning for the LLM judge (forgelm.judge).

The judge produces the score the auto-revert gate compares against
``min_score``, so an upstream re-tune of the judge moves the pass/fail line
with no config diff to point at — two runs of the same YAML can promote and
block the same model.  ``evaluation.llm_judge.judge_model_revision`` validated
and documented but reached no loader until this module's contract landed.

These tests assert the pin reaches *both* loads (weights and tokenizer must
not diverge), that provenance is recorded only after a load succeeds, and that
it is recorded under the judge role rather than polluting the base-model
lineage.

The broader judge behaviour lives in ``tests/test_judge_functions.py``; this
module is scoped to the revision contract.

No network, no GPU: transformers entry points are mocked at the import
boundary and the revision resolver is stubbed, per docs/standards/testing.md.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from forgelm import judge as judge_mod
from forgelm import model as model_mod

SHA = "0" * 39 + "b"


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
            seen["offline"] = offline
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


def _fake_transformers(captured, fail_model=False):
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


class TestLocalJudgePin:
    """``_load_local_judge`` pins the judge weights and its tokenizer alike."""

    def test_resolved_sha_reaches_both_loads(self, stub_resolver):
        stub_resolver(revision_resolved=SHA, resolution_source="pinned_resolved")
        captured: dict = {}
        with patch.dict("sys.modules", {"transformers": _fake_transformers(captured)}):
            judge_mod._load_local_judge("org/judge", SHA)
        assert captured["tokenizer"] == SHA
        assert captured["model"] == SHA

    def test_configured_revision_is_what_gets_resolved(self, stub_resolver):
        seen = stub_resolver(revision_resolved=SHA, resolution_source="pinned_resolved")
        captured: dict = {}
        with patch.dict("sys.modules", {"transformers": _fake_transformers(captured)}):
            judge_mod._load_local_judge("org/judge", "v1.0")
        assert seen["requested"] == "v1.0"

    def test_unconfirmed_pin_is_still_honoured_by_the_load(self, stub_resolver):
        """No SHA could be confirmed, but the operator's literal must still
        reach ``revision=`` — otherwise the load silently ignores the pin."""
        stub_resolver(resolution_source="pinned_unverified")
        captured: dict = {}
        with patch.dict("sys.modules", {"transformers": _fake_transformers(captured)}):
            judge_mod._load_local_judge("org/judge", "v1.0")
        assert captured["tokenizer"] == "v1.0"
        assert captured["model"] == "v1.0"

    def test_unpinned_load_is_unchanged(self, stub_resolver):
        stub_resolver(resolution_source="unresolved")
        captured: dict = {}
        with patch.dict("sys.modules", {"transformers": _fake_transformers(captured)}):
            judge_mod._load_local_judge("org/judge")
        assert captured["tokenizer"] is None
        assert captured["model"] is None

    def test_successful_load_is_recorded_under_the_judge_role(self, stub_resolver):
        stub_resolver(revision_resolved=SHA, resolution_source="pinned_resolved")
        with patch.dict("sys.modules", {"transformers": _fake_transformers({})}):
            judge_mod._load_local_judge("org/judge", SHA)
        record = model_mod.get_loaded_model_revision("org/judge", judge_mod.ROLE_LLM_JUDGE)
        assert record["revision_resolved"] == SHA
        # Never under base_model: a judge contributed no weights to the
        # fine-tuned model and must not appear in its lineage.
        assert model_mod.get_loaded_model_revision("org/judge") is None

    def test_nothing_recorded_when_the_load_fails(self, stub_resolver):
        stub_resolver(revision_resolved=SHA, resolution_source="pinned_resolved")
        with patch.dict("sys.modules", {"transformers": _fake_transformers({}, fail_model=True)}):
            with pytest.raises(OSError):
                judge_mod._load_local_judge("org/judge", SHA)
        assert model_mod.get_loaded_model_revision("org/judge", judge_mod.ROLE_LLM_JUDGE) is None

    def test_judge_role_does_not_collide_with_the_safety_classifier(self, stub_resolver):
        """Llama-Guard is routinely both the safety classifier and the local
        judge in one run.  A shared role would let one load's record overwrite
        the other's — including an unpinned one overwriting a pinned one."""
        stub_resolver(revision_resolved=SHA, resolution_source="pinned_resolved")
        with patch.dict("sys.modules", {"transformers": _fake_transformers({})}):
            judge_mod._load_local_judge("meta/guard", SHA)
        assert model_mod.get_loaded_model_revision("meta/guard", judge_mod.ROLE_LLM_JUDGE) is not None
        assert model_mod.get_loaded_model_revision("meta/guard", model_mod.ROLE_SAFETY_CLASSIFIER) is None


class TestRunJudgeEvaluationPassesRevision:
    """The public entry point must forward the configured pin to the loader.

    A pin that stops at the API boundary is exactly the failure this wiring
    fixes; asserting only on ``_load_local_judge`` would not catch it.
    """

    def test_revision_reaches_the_loader(self, tmp_path):
        eval_file = tmp_path / "eval.jsonl"
        eval_file.write_text('{"prompt": "hi"}\n', encoding="utf-8")
        seen: dict = {}

        def _fake_load(judge_model, judge_model_revision=None):
            seen["model"] = judge_model
            seen["revision"] = judge_model_revision
            raise OSError("stop here — the load itself is not under test")

        with patch.object(judge_mod, "_load_local_judge", _fake_load):
            result = judge_mod.run_judge_evaluation(
                model=MagicMock(),
                tokenizer=MagicMock(),
                eval_dataset_path=str(eval_file),
                judge_model="org/judge",
                judge_model_revision=SHA,
            )
        assert seen == {"model": "org/judge", "revision": SHA}
        # The load failure is still surfaced, not swallowed.
        assert result.passed is False
