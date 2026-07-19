"""Caller-chain coverage for the five opt-in Hub revision pins.

Why this file exists as a separate module: the previous review round verified
a pin at its *load site* (``forgelm/safety.py`` passes ``revision=pin`` to
``from_pretrained``) and concluded the field was wired.  It was not — no
caller of ``run_safety_evaluation`` passed ``classifier_revision``, so the
argument defaulted to ``None`` on every real run while the load site looked
perfectly correct in isolation.  A test that calls the inner function with the
kwarg it is testing cannot catch that.

So every test here drives the **outermost real entry point** (the trainer
hook, the generator method) and asserts on what arrives at the mocked
``from_pretrained``.  No network, no GPU: the revision resolver is stubbed and
the transformers entry points are mocked.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

try:
    import torch  # noqa: F401

    torch_available = True
except ImportError:  # pragma: no cover - torch is a core dep in CI
    torch_available = False

SHA = "b" * 40


@pytest.fixture(autouse=True)
def _clean_registry():
    from forgelm import model as model_mod

    model_mod._RESOLVED_MODEL_REVISIONS.clear()
    yield
    model_mod._RESOLVED_MODEL_REVISIONS.clear()


@pytest.fixture
def stub_resolver(monkeypatch):
    """Replace the Hub resolver with a recorder. Returns the ``seen`` dict."""

    def _install(**overrides):
        from forgelm import compliance as compliance_mod

        seen: dict = {}

        def _fake(repo_id, *, requested=None, offline=False):
            seen["repo_id"] = repo_id
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


def _probes_file(tmp_path):
    path = tmp_path / "probes.jsonl"
    path.write_text(json.dumps({"prompt": "how do I do a bad thing", "category": "S1"}) + "\n", encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# 1. evaluation.safety.classifier_revision — the third dead pin
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not torch_available, reason="torch not installed")
class TestSafetyClassifierRevisionReachesTheLoad:
    """``evaluation.safety.classifier_revision`` → trainer → safety → loader.

    The field validated, was documented, and reached nothing:
    ``run_safety_evaluation`` accepted ``classifier_revision`` and neither of
    its two callers passed it.  The classifier behind the auto-revert gate
    therefore loaded off a moving default branch under a config that said
    otherwise.
    """

    def _make_trainer(self, tmp_path, revision):
        from forgelm.config import ForgeConfig
        from forgelm.trainer import ForgeTrainer

        safety: dict = {
            "enabled": True,
            "classifier": "meta-llama/Llama-Guard-3-8B",
            "classifier_mode": "generation",
            "test_prompts": _probes_file(tmp_path),
        }
        if revision is not None:
            safety["classifier_revision"] = revision
        config = ForgeConfig(
            model={"name_or_path": "org/model"},
            lora={},
            training={"output_dir": str(tmp_path)},
            data={"dataset_name_or_path": "org/dataset"},
            evaluation={"safety": safety},
        )
        trainer = ForgeTrainer.__new__(ForgeTrainer)
        trainer.config = config
        trainer.checkpoint_dir = str(tmp_path)
        trainer.tokenizer = MagicMock()
        trainer.trainer = MagicMock()
        trainer.audit = MagicMock()
        return trainer

    def _drive(self, trainer, captured):
        """Run the real trainer hook through the real safety pipeline.

        Only the two things that need a GPU or a checkpoint are stubbed:
        response generation and the transformers loaders.  Everything between
        the trainer hook and ``from_pretrained`` is the shipping code path.
        """

        def _tok(_path, **kwargs):
            captured["tokenizer"] = kwargs.get("revision")
            return MagicMock()

        def _model(_path, **kwargs):
            captured["model"] = kwargs.get("revision")
            m = MagicMock()
            m.eval.return_value = None
            return m

        with (
            patch("forgelm.safety._orchestrator._generate_safety_responses", return_value=["a response"]),
            patch("forgelm.safety._orchestrator._release_model_from_gpu"),
            # Downstream of the load under test; stubbed only so the mocked
            # guard produces a JSON-serialisable verdict for the results file.
            patch("forgelm.safety._score_generation._generate_guard_verdict", return_value="safe"),
            patch("torch.cuda.is_available", return_value=False),
            patch("transformers.AutoTokenizer.from_pretrained", side_effect=_tok),
            patch("transformers.AutoModelForCausalLM.from_pretrained", side_effect=_model),
        ):
            return trainer._run_safety_if_configured()

    def test_resolved_sha_reaches_both_loads(self, tmp_path, stub_resolver):
        seen = stub_resolver(revision_resolved=SHA, resolution_source="pinned_resolved")
        trainer = self._make_trainer(tmp_path, revision="v1.2.3")
        captured: dict = {}

        self._drive(trainer, captured)

        # The operator's literal reached the resolver...
        assert seen["requested"] == "v1.2.3"
        assert seen["repo_id"] == "meta-llama/Llama-Guard-3-8B"
        # ...and the resolved SHA reached BOTH loads.  Guard weights from one
        # commit under a chat template from another is not the verdict the
        # manifest would describe.
        assert captured["tokenizer"] == SHA
        assert captured["model"] == SHA

    def test_unconfirmed_ref_is_still_honoured_by_the_load(self, tmp_path, stub_resolver):
        """Nothing could confirm the ref; the load must still use it verbatim."""
        stub_resolver(resolution_source="pinned_unverified")
        trainer = self._make_trainer(tmp_path, revision="my-branch")
        captured: dict = {}

        self._drive(trainer, captured)

        assert captured["tokenizer"] == "my-branch"
        assert captured["model"] == "my-branch"

    def test_unset_field_loads_unpinned(self, tmp_path, stub_resolver):
        """Mutation guard: the wiring must forward the *config* value, not a
        constant.  A fix that hardcoded any non-``None`` pin would pass the two
        tests above and fail here."""
        seen = stub_resolver(resolution_source="unresolved")
        trainer = self._make_trainer(tmp_path, revision=None)
        captured: dict = {}

        self._drive(trainer, captured)

        assert seen["requested"] is None
        assert captured["tokenizer"] is None
        assert captured["model"] is None

    def test_load_is_registered_under_the_safety_role(self, tmp_path, stub_resolver):
        from forgelm import model as model_mod

        stub_resolver(revision_resolved=SHA, resolution_source="pinned_resolved")
        trainer = self._make_trainer(tmp_path, revision="v1.2.3")

        self._drive(trainer, {})

        record = model_mod.get_loaded_model_revision(
            "meta-llama/Llama-Guard-3-8B", role=model_mod.ROLE_SAFETY_CLASSIFIER
        )
        assert record is not None
        assert record["revision_resolved"] == SHA
        # ...and NOT under the base-model role, which is a different question.
        assert model_mod.get_loaded_model_revision("meta-llama/Llama-Guard-3-8B") is None


# ---------------------------------------------------------------------------
# 2. evaluation.llm_judge.judge_model_revision — caller chain re-verified
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not torch_available, reason="torch not installed")
class TestJudgeRevisionReachesTheLoad:
    """``judge_model_revision`` → trainer → judge → local judge loader.

    Re-verified from the caller rather than trusted: the same
    "the load site passes ``revision=pin``" reasoning that mis-cleared the
    safety pin would have mis-cleared this one.
    """

    def _make_trainer(self, tmp_path, revision):
        from forgelm.config import ForgeConfig
        from forgelm.trainer import ForgeTrainer

        eval_ds = tmp_path / "eval.jsonl"
        eval_ds.write_text(json.dumps({"prompt": "hi", "response": "hello"}) + "\n", encoding="utf-8")
        judge: dict = {
            "enabled": True,
            "judge_model": "org/judge",
            "eval_dataset": str(eval_ds),
        }
        if revision is not None:
            judge["judge_model_revision"] = revision
        config = ForgeConfig(
            model={"name_or_path": "org/model"},
            lora={},
            training={"output_dir": str(tmp_path)},
            data={"dataset_name_or_path": "org/dataset"},
            evaluation={"llm_judge": judge},
        )
        trainer = ForgeTrainer.__new__(ForgeTrainer)
        trainer.config = config
        trainer.checkpoint_dir = str(tmp_path)
        trainer.tokenizer = MagicMock()
        trainer.trainer = MagicMock()
        trainer.audit = MagicMock()
        trainer.run_name = "judge_pin_test"
        return trainer

    def _drive(self, trainer, captured):
        def _tok(_path, **kwargs):
            captured["tokenizer"] = kwargs.get("revision")
            return MagicMock()

        def _model(_path, **kwargs):
            captured["model"] = kwargs.get("revision")
            return MagicMock()

        with (
            patch("transformers.AutoTokenizer.from_pretrained", side_effect=_tok),
            patch("transformers.AutoModelForCausalLM.from_pretrained", side_effect=_model),
        ):
            return trainer._run_judge_if_configured()

    def test_resolved_sha_reaches_the_local_judge_load(self, tmp_path, stub_resolver):
        seen = stub_resolver(revision_resolved=SHA, resolution_source="pinned_resolved")
        trainer = self._make_trainer(tmp_path, revision="judge-tag")
        captured: dict = {}

        self._drive(trainer, captured)

        assert seen["requested"] == "judge-tag"
        assert captured["tokenizer"] == SHA
        assert captured["model"] == SHA

    def test_unset_field_loads_unpinned(self, tmp_path, stub_resolver):
        stub_resolver(resolution_source="unresolved")
        trainer = self._make_trainer(tmp_path, revision=None)
        captured: dict = {}

        self._drive(trainer, captured)

        assert captured["tokenizer"] is None
        assert captured["model"] is None


# ---------------------------------------------------------------------------
# 3. synthetic.teacher_revision — caller chain re-verified
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not torch_available, reason="torch not installed")
class TestTeacherRevisionReachesTheLoad:
    """``synthetic.teacher_revision`` → generator → local teacher loader."""

    def _make_generator(self, tmp_path, revision):
        from forgelm.config import ForgeConfig
        from forgelm.synthetic import SyntheticDataGenerator

        seeds = tmp_path / "seeds.jsonl"
        seeds.write_text(json.dumps({"instruction": "write a poem"}) + "\n", encoding="utf-8")
        synthetic: dict = {
            "enabled": True,
            "teacher_backend": "local",
            "teacher_model": "org/teacher",
            "seed_file": str(seeds),
            "output_file": str(tmp_path / "out.jsonl"),
        }
        if revision is not None:
            synthetic["teacher_revision"] = revision
        config = ForgeConfig(
            model={"name_or_path": "org/model"},
            lora={},
            training={"output_dir": str(tmp_path)},
            data={"dataset_name_or_path": "org/dataset"},
            synthetic=synthetic,
        )
        return SyntheticDataGenerator(config)

    def _drive(self, generator, captured):
        def _tok(_path, **kwargs):
            captured["tokenizer"] = kwargs.get("revision")
            return MagicMock()

        def _model(_path, **kwargs):
            captured["model"] = kwargs.get("revision")
            return MagicMock()

        with (
            patch("torch.cuda.is_available", return_value=False),
            patch("transformers.AutoTokenizer.from_pretrained", side_effect=_tok),
            patch("transformers.AutoModelForCausalLM.from_pretrained", side_effect=_model),
        ):
            generator._load_local_teacher()

    def test_resolved_sha_reaches_the_teacher_load(self, tmp_path, stub_resolver):
        seen = stub_resolver(revision_resolved=SHA, resolution_source="pinned_resolved")
        generator = self._make_generator(tmp_path, revision="teacher-tag")
        captured: dict = {}

        self._drive(generator, captured)

        assert seen["requested"] == "teacher-tag"
        assert captured["tokenizer"] == SHA
        assert captured["model"] == SHA

    def test_unset_field_loads_unpinned(self, tmp_path, stub_resolver):
        stub_resolver(resolution_source="unresolved")
        generator = self._make_generator(tmp_path, revision=None)
        captured: dict = {}

        self._drive(generator, captured)

        assert captured["tokenizer"] is None
        assert captured["model"] is None


# ---------------------------------------------------------------------------
# 4. model_lineage.component_revisions — the evidence stops being discarded
# ---------------------------------------------------------------------------


def _minimal_config(tmp_path):
    from forgelm.config import ForgeConfig

    return ForgeConfig(
        model={"name_or_path": "org/model"},
        lora={},
        training={"output_dir": str(tmp_path)},
        data={"dataset_name_or_path": "org/dataset"},
    )


class TestComponentRevisionsBlock:
    """Every role's provenance must reach the manifest, not just the base model.

    Before this block existed, ``prepare_revision_pin`` warned the operator
    that "the Annex IV manifest will record that no SHA was verified rather
    than assert one" — while for four of the six roles the manifest recorded
    *nothing at all*, because the registry's only readers asked for
    ``ROLE_BASE_MODEL``.  The message promised an artefact entry that would
    never exist.  Keeping the evidence is what makes the message true.
    """

    def _seed(self, role, repo_id, **overrides):
        from forgelm import model as model_mod

        record = {
            "repo_id": repo_id,
            "role": role,
            "revision_requested": None,
            "revision_resolved": SHA,
            "resolution_source": "resolved",
            "revision_pinned": SHA,
        }
        record.update(overrides)
        model_mod.record_loaded_revision(record)

    def test_empty_registry_yields_an_empty_list(self, tmp_path):
        from forgelm.compliance import _component_revisions_block

        assert _component_revisions_block(_minimal_config(tmp_path)) == []

    def test_every_role_appears(self, tmp_path):
        from forgelm import model as model_mod
        from forgelm.compliance import _component_revisions_block

        self._seed(model_mod.ROLE_BASE_MODEL, "org/model")
        self._seed(model_mod.ROLE_SAFETY_CLASSIFIER, "meta/guard")
        self._seed(model_mod.ROLE_LLM_JUDGE, "org/judge")
        self._seed(model_mod.ROLE_GRPO_REWARD_MODEL, "org/reward")
        self._seed(model_mod.ROLE_TEACHER_MODEL, "org/teacher")

        block = _component_revisions_block(_minimal_config(tmp_path))

        assert {entry["role"] for entry in block} == {
            "base_model",
            "safety_classifier",
            "llm_judge",
            "grpo_reward_model",
            "teacher_model",
        }
        assert all(entry["revision_resolved"] == SHA for entry in block)

    def test_same_repo_under_two_roles_is_not_collapsed(self, tmp_path):
        """Llama-Guard is routinely both the safety classifier and the judge,
        and only one of the two loads may have been pinned."""
        from forgelm import model as model_mod
        from forgelm.compliance import _component_revisions_block

        self._seed(model_mod.ROLE_SAFETY_CLASSIFIER, "meta/guard", revision_resolved=SHA)
        self._seed(
            model_mod.ROLE_LLM_JUDGE,
            "meta/guard",
            revision_resolved=None,
            revision_pinned=None,
            resolution_source="unresolved",
        )

        block = _component_revisions_block(_minimal_config(tmp_path))

        by_role = {entry["role"]: entry for entry in block}
        assert by_role["safety_classifier"]["revision_resolved"] == SHA
        assert by_role["llm_judge"]["revision_resolved"] is None

    def test_unconfirmed_pin_records_the_gap_not_a_sha(self, tmp_path):
        """The exact claim ``prepare_revision_pin``'s warning makes: the ref is
        recorded verbatim, ``revision_resolved`` stays ``None``."""
        from forgelm import model as model_mod
        from forgelm.compliance import _component_revisions_block

        self._seed(
            model_mod.ROLE_SAFETY_CLASSIFIER,
            "meta/guard",
            revision_requested="my-branch",
            revision_resolved=None,
            revision_pinned="my-branch",
            resolution_source="pinned_unverified",
        )

        entry = _component_revisions_block(_minimal_config(tmp_path))[0]

        assert entry["revision_requested"] == "my-branch"
        assert entry["revision_pinned"] == "my-branch"
        assert entry["revision_resolved"] is None
        assert entry["resolution_source"] == "pinned_unverified"

    def test_order_is_stable_regardless_of_load_order(self, tmp_path):
        """The manifest is hashed and diffed; dict-insertion order must not be
        a property an auditor has to reason about."""
        from forgelm import model as model_mod
        from forgelm.compliance import _component_revisions_block

        self._seed(model_mod.ROLE_TEACHER_MODEL, "org/teacher")
        self._seed(model_mod.ROLE_BASE_MODEL, "org/model")
        first = [e["role"] for e in _component_revisions_block(_minimal_config(tmp_path))]

        model_mod._RESOLVED_MODEL_REVISIONS.clear()
        self._seed(model_mod.ROLE_BASE_MODEL, "org/model")
        self._seed(model_mod.ROLE_TEACHER_MODEL, "org/teacher")
        second = [e["role"] for e in _component_revisions_block(_minimal_config(tmp_path))]

        assert first == second == ["base_model", "teacher_model"]

    def test_returned_records_are_copies(self, tmp_path):
        from forgelm import model as model_mod
        from forgelm.compliance import _component_revisions_block

        self._seed(model_mod.ROLE_BASE_MODEL, "org/model")
        block = _component_revisions_block(_minimal_config(tmp_path))
        block[0]["revision_resolved"] = "tampered"

        assert model_mod.get_loaded_model_revision("org/model")["revision_resolved"] == SHA

    def test_manifest_carries_the_block(self, tmp_path):
        from forgelm import model as model_mod
        from forgelm.compliance import generate_training_manifest

        self._seed(model_mod.ROLE_SAFETY_CLASSIFIER, "meta/guard")
        manifest = generate_training_manifest(_minimal_config(tmp_path), metrics={"loss": 0.1})

        roles = [e["role"] for e in manifest["model_lineage"]["component_revisions"]]
        assert roles == ["safety_classifier"]
        # The pre-existing key is untouched — this is additive, not a rename.
        assert "base_model_revision" in manifest["model_lineage"]


class TestOldManifestsStayValid:
    """An additive manifest field must not invalidate artefacts written before
    it existed — a compliance archive is read years after it is written."""

    def _annex_artifact(self, extra=None):
        artifact = {
            "system_identification": {
                "provider_name": "ACME",
                "system_name": "forge-demo",
                "intended_purpose": "demo",
            },
            "intended_purpose": "demo",
            "system_components": ["forgelm"],
            "computational_resources": {"gpu": "none"},
            "data_governance": {"sources": ["org/dataset"]},
            "technical_documentation": {"method": "lora"},
            "monitoring_and_logging": {"audit_log": "audit.jsonl"},
            "performance_metrics": {"loss": 0.1},
            "risk_management": {"reference": "art9.md"},
        }
        if extra:
            artifact["model_lineage"] = extra
        return artifact

    def test_artifact_without_component_revisions_still_verifies(self, tmp_path):
        """The pre-Step-4 shape: model_lineage with only base_model_revision."""
        from forgelm.verify import verify_annex_iv_artifact

        path = tmp_path / "old.json"
        path.write_text(
            json.dumps(self._annex_artifact({"base_model": "org/model", "base_model_revision": {}})),
            encoding="utf-8",
        )
        assert verify_annex_iv_artifact(str(path)).valid

    def test_artifact_with_component_revisions_still_verifies(self, tmp_path):
        from forgelm.verify import verify_annex_iv_artifact

        path = tmp_path / "new.json"
        path.write_text(
            json.dumps(
                self._annex_artifact(
                    {
                        "base_model": "org/model",
                        "base_model_revision": {},
                        "component_revisions": [{"role": "safety_classifier", "repo_id": "meta/guard"}],
                    }
                )
            ),
            encoding="utf-8",
        )
        assert verify_annex_iv_artifact(str(path)).valid


# ---------------------------------------------------------------------------
# 5. Bounded Hub calls — an unbounded metadata lookup on the common path
# ---------------------------------------------------------------------------


class TestHubCallsAreBounded:
    """``HfApi`` defaults to ``timeout=None``, i.e. *no timeout at all*.

    Revision resolution now runs on every online load whether or not a pin is
    configured, and it runs before training starts.  The "the subsequent load
    was already unbounded" defence does not survive the **fully-cached** case:
    there the load needs no network at all, so an unbounded metadata call
    converts a run that would have completed offline into one that never
    begins.  Every Hub metadata call this package makes must be bounded and
    must degrade to ``unresolved``, never raise.
    """

    def test_model_info_receives_the_timeout(self, monkeypatch):
        import huggingface_hub

        from forgelm import compliance as compliance_mod
        from forgelm import model as model_mod

        # Record; never assert inside the stub.  ``_query_hub_model_revision``
        # wraps the call in a broad ``except`` that swallows ``AssertionError``
        # exactly like a transport error, so an in-stub assert is toothless:
        # the function would return ``None`` and the test would read that as a
        # legitimate "no SHA" answer.
        seen: dict = {}

        class _Api:
            def model_info(self, repo_id, revision=None, timeout=None):
                seen["timeout"] = timeout
                return type("Info", (), {"sha": SHA})()

        monkeypatch.setattr(huggingface_hub, "HfApi", _Api)
        assert compliance_mod._query_hub_model_revision("org/model", None) == SHA
        assert seen["timeout"] == model_mod.HUB_API_TIMEOUT_SECONDS
        assert seen["timeout"] is not None

    def test_dataset_info_receives_the_timeout(self, monkeypatch):
        import huggingface_hub

        from forgelm import data as data_mod
        from forgelm import model as model_mod

        for var in data_mod._HF_OFFLINE_ENV_VARS:
            monkeypatch.delenv(var, raising=False)

        seen: dict = {}

        class _Api:
            def dataset_info(self, path, timeout=None):
                seen["timeout"] = timeout
                return type("Info", (), {"sha": SHA})()

        monkeypatch.setattr(huggingface_hub, "HfApi", _Api)
        assert data_mod._resolve_hub_dataset_revision("org/dataset") == SHA
        assert seen["timeout"] == model_mod.HUB_API_TIMEOUT_SECONDS

    @pytest.mark.real_fingerprint
    def test_dataset_fingerprint_lookup_receives_the_timeout(self, monkeypatch):
        import huggingface_hub

        from forgelm import compliance as compliance_mod
        from forgelm import model as model_mod

        seen: dict = {}

        class _Api:
            def dataset_info(self, dataset_id, timeout=None):
                seen["timeout"] = timeout
                return type("Info", (), {"sha": SHA})()

        monkeypatch.setattr(huggingface_hub, "HfApi", _Api)
        fingerprint: dict = {}
        compliance_mod._fingerprint_hf_revision("org/dataset", fingerprint, offline=False)
        assert seen["timeout"] == model_mod.HUB_API_TIMEOUT_SECONDS

    def test_timeout_is_a_positive_finite_number(self):
        from forgelm import model as model_mod

        assert isinstance(model_mod.HUB_API_TIMEOUT_SECONDS, (int, float))
        assert 0 < model_mod.HUB_API_TIMEOUT_SECONDS < 120


class TestBlackHoledHubDoesNotHang:
    """End-to-end proof against a real socket that accepts and never answers.

    This is the failure the timeout exists for: a DROP-ing firewall or a
    hijacked DNS answer completes the TCP handshake and then goes silent, so
    the client blocks on ``recv`` forever.  A closed port would raise
    ``ConnectionRefused`` immediately and prove nothing.

    Loopback only — ``conftest``'s no-network guard permits it, and no packet
    leaves the machine.
    """

    @staticmethod
    def _black_hole():
        import socket
        import threading

        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(8)
        accepted: list = []
        stop = threading.Event()

        def _accept_and_ignore():
            server.settimeout(0.25)
            while not stop.is_set():
                try:
                    conn, _addr = server.accept()
                except OSError:
                    continue
                # Hold the connection open and send nothing back, ever.
                accepted.append(conn)

        thread = threading.Thread(target=_accept_and_ignore, daemon=True)
        thread.start()
        return server, stop, thread, accepted

    # Hard ceiling for the *test*, well above the 0.75s ceiling under test.
    # This exists so a regression FAILS rather than wedging CI: with the
    # ``timeout=`` argument removed the call under test never returns at all,
    # so an in-line ``assert elapsed < N`` would never be reached and the
    # suite would hang forever instead of reporting a failure.
    _WALL_CLOCK_CEILING_SECONDS = 20.0

    def _run_against_black_hole(self, monkeypatch, call):
        import threading
        import time

        from forgelm import model as model_mod

        server, stop, thread, accepted = self._black_hole()
        outcome: dict = {}
        try:
            host, port = server.getsockname()
            monkeypatch.setenv("HF_ENDPOINT", f"http://{host}:{port}")
            # Shrink the ceiling for the test only.  The lazy
            # ``from .model import HUB_API_TIMEOUT_SECONDS`` inside each caller
            # re-reads the module attribute per call, so this takes effect.
            monkeypatch.setattr(model_mod, "HUB_API_TIMEOUT_SECONDS", 0.75)

            def _worker():
                started = time.monotonic()
                try:
                    outcome["result"] = call()
                except BaseException as exc:  # noqa: BLE001 — reported below
                    outcome["error"] = exc
                outcome["elapsed"] = time.monotonic() - started

            runner = threading.Thread(target=_worker, daemon=True)
            runner.start()
            runner.join(timeout=self._WALL_CLOCK_CEILING_SECONDS)
            if runner.is_alive():
                pytest.fail(
                    f"Hub lookup was still blocked after {self._WALL_CLOCK_CEILING_SECONDS}s against a "
                    "socket that accepts and never answers — the timeout is not reaching the HfApi call."
                )
            if "error" in outcome:
                raise AssertionError(
                    f"Hub lookup raised instead of degrading to 'unresolved': {outcome['error']!r}"
                ) from outcome["error"]
            return outcome["result"], outcome["elapsed"]
        finally:
            stop.set()
            thread.join(timeout=2)
            # Closing the accepted connections releases any worker still
            # blocked in ``recv`` so a failed run leaves no wedged thread.
            for conn in accepted:
                conn.close()
            server.close()

    def test_model_revision_resolution_gives_up_and_reports_unresolved(self, monkeypatch):
        from forgelm import compliance as compliance_mod

        for var in ("HF_HUB_OFFLINE", "HF_DATASETS_OFFLINE", "TRANSFORMERS_OFFLINE"):
            monkeypatch.delenv(var, raising=False)
        # Keep the cache branch out of it: this test is about the Hub call.
        monkeypatch.setattr(compliance_mod, "_cached_snapshot_revision", lambda *a, **k: None)

        record, elapsed = self._run_against_black_hole(
            monkeypatch,
            lambda: compliance_mod.resolve_model_revision("org/model-that-hangs"),
        )

        # Bounded: without a timeout this call never returns at all.
        assert elapsed < 30, f"Hub lookup took {elapsed:.1f}s — the timeout is not being applied"
        # Degraded honestly rather than raising or inventing a SHA.
        assert record["revision_resolved"] is None
        assert record["resolution_source"] == "unresolved"

    def test_dataset_revision_resolution_gives_up_and_returns_none(self, monkeypatch):
        from forgelm import data as data_mod

        for var in data_mod._HF_OFFLINE_ENV_VARS:
            monkeypatch.delenv(var, raising=False)

        sha, elapsed = self._run_against_black_hole(
            monkeypatch,
            lambda: data_mod._resolve_hub_dataset_revision("org/dataset-that-hangs"),
        )

        assert elapsed < 30, f"Hub lookup took {elapsed:.1f}s — the timeout is not being applied"
        assert sha is None


# ---------------------------------------------------------------------------
# 6. Offline detection + the role registry's key space
# ---------------------------------------------------------------------------


class TestOfflineEnvVarParity:
    """``model`` and ``data`` must agree on what "offline" means.

    They claimed to mirror each other in a comment while the model side
    omitted ``TRANSFORMERS_OFFLINE`` — so a box air-gapped with that variable
    alone got no dataset lookup (correct) and a full round of model-revision
    lookups (wrong), which is precisely the outbound request the operator
    believed they had switched off.
    """

    def test_the_two_tuples_are_identical(self):
        from forgelm import data as data_mod
        from forgelm import model as model_mod

        assert model_mod._HF_OFFLINE_ENV_VARS == data_mod._HF_OFFLINE_ENV_VARS

    def test_transformers_offline_is_honoured_by_the_model_side(self, monkeypatch):
        from forgelm import model as model_mod

        for var in model_mod._HF_OFFLINE_ENV_VARS:
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
        assert model_mod._hf_offline_mode() is True


class TestHfOfflineModeMutationCoverage:
    """Close the coverage gap that let a mutation in ``_hf_offline_mode``
    survive the entire suite.

    The only test touching this function set ``HF_HUB_OFFLINE="1"`` — a value
    with no surrounding whitespace, no uppercase, and one variable set out of
    three.  That single case leaves ``.strip()``, ``.lower()``, the falsey-set
    membership and the per-variable independence of ``any()`` all unverified:
    delete ``.strip()``, delete ``.lower()``, or invert the falsey check for
    the values not exercised, and every test still passed.
    """

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        from forgelm import model as model_mod

        for var in model_mod._HF_OFFLINE_ENV_VARS:
            monkeypatch.delenv(var, raising=False)

    @pytest.mark.parametrize("var", ["HF_HUB_OFFLINE", "HF_DATASETS_OFFLINE", "TRANSFORMERS_OFFLINE"])
    def test_each_var_independently_forces_offline(self, monkeypatch, var):
        """Mutation guard for ``any`` → ``all``: each variable must be
        sufficient on its own."""
        from forgelm import model as model_mod

        monkeypatch.setenv(var, "1")
        assert model_mod._hf_offline_mode() is True

    @pytest.mark.parametrize("value", ["", "0", "false", "no", "off"])
    def test_falsey_values_do_not_force_offline(self, monkeypatch, value):
        """Mutation guard for ``not in`` → ``in``: an explicitly-disabled
        variable must not be read as "air-gapped"."""
        from forgelm import model as model_mod

        monkeypatch.setenv("HF_HUB_OFFLINE", value)
        assert model_mod._hf_offline_mode() is False

    @pytest.mark.parametrize("value", ["FALSE", "False", "No", "OFF"])
    def test_falsey_values_are_case_insensitive(self, monkeypatch, value):
        """Mutation guard for a deleted ``.lower()``: ``HF_HUB_OFFLINE=FALSE``
        must mean the same as ``false``.  Without this, deleting ``.lower()``
        silently flips an explicitly-disabled var into "offline"."""
        from forgelm import model as model_mod

        monkeypatch.setenv("HF_HUB_OFFLINE", value)
        assert model_mod._hf_offline_mode() is False

    @pytest.mark.parametrize("value", ["  0  ", "\tfalse\n", " off "])
    def test_padded_falsey_values_are_stripped(self, monkeypatch, value):
        """Mutation guard for a deleted ``.strip()``.  A value pasted into a
        CI secret or a shell heredoc routinely carries stray whitespace, and
        without the strip ``" 0 "`` is not in the falsey set — so a variable
        the operator set to *off* would turn the run air-gapped and silently
        drop every revision pin."""
        from forgelm import model as model_mod

        monkeypatch.setenv("HF_HUB_OFFLINE", value)
        assert model_mod._hf_offline_mode() is False

    @pytest.mark.parametrize("value", [" 1 ", "TRUE", "yes"])
    def test_padded_and_cased_truthy_values_still_force_offline(self, monkeypatch, value):
        from forgelm import model as model_mod

        monkeypatch.setenv("HF_HUB_OFFLINE", value)
        assert model_mod._hf_offline_mode() is True

    def test_clean_env_is_online(self):
        from forgelm import model as model_mod

        assert model_mod._hf_offline_mode() is False


class TestRoleConstantsHaveOneHome:
    """The role strings are registry keys AND manifest values — an auditor
    matches on them in a two-year-old artefact, so they must not be renamed.
    They were declared across three files, which made "is this role already
    taken?" a three-file question.
    """

    def test_all_roles_are_declared_in_model(self):
        from forgelm import model as model_mod

        assert model_mod.ROLE_BASE_MODEL == "base_model"
        assert model_mod.ROLE_FIT_CHECK == "fit_check"
        assert model_mod.ROLE_SAFETY_CLASSIFIER == "safety_classifier"
        assert model_mod.ROLE_TEACHER_MODEL == "teacher_model"
        assert model_mod.ROLE_LLM_JUDGE == "llm_judge"
        assert model_mod.ROLE_GRPO_REWARD_MODEL == "grpo_reward_model"

    def test_re_exports_are_the_same_object(self):
        """The consolidation must not fork the value: a second literal that
        happens to match today is a rename waiting to happen."""
        from forgelm import judge as judge_mod
        from forgelm import model as model_mod
        from forgelm import trainer as trainer_mod

        assert judge_mod.ROLE_LLM_JUDGE is model_mod.ROLE_LLM_JUDGE
        assert trainer_mod.ROLE_GRPO_REWARD_MODEL is model_mod.ROLE_GRPO_REWARD_MODEL

    def test_roles_are_distinct(self):
        from forgelm import model as model_mod

        roles = [
            model_mod.ROLE_BASE_MODEL,
            model_mod.ROLE_FIT_CHECK,
            model_mod.ROLE_SAFETY_CLASSIFIER,
            model_mod.ROLE_TEACHER_MODEL,
            model_mod.ROLE_LLM_JUDGE,
            model_mod.ROLE_GRPO_REWARD_MODEL,
        ]
        assert len(set(roles)) == len(roles)
