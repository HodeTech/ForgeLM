"""Tests for forgelm.model — loading core, no GPU, no network (F-P8-C-02).

Before this package model.py had near-zero behavioural coverage: every test
that named it either asserted the export symbol (test_library_api.py), replaced
the whole module with a fake (the pipeline tests), or patched the function to
assert it is NOT called (test_no_train_modes.py). The real bodies of
``_resolve_bnb_compute_dtype`` (and its ``ValueError`` raise),
``_device_map_for``, ``_build_model_kwargs``, ``_load_tokenizer``, and
``_build_lora_config`` never executed, so a regression in dtype resolution,
device-map selection, quantization-kwargs gating, pad-token handling, or LoRA
target resolution would ship green.

These tests exercise those bodies directly. ``torch`` is the real (CPU)
import; ``torch.cuda.is_available`` is mocked so the GPU branches are reachable
on a laptop. transformers/peft entry points are mocked per the testing
standard (mock at the network/heavy-import boundary, use real config objects).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from forgelm import model as model_mod
from forgelm.config import ForgeConfig
from tests._helpers.factories import minimal_config


def _cfg(**overrides) -> ForgeConfig:
    return ForgeConfig(**minimal_config(**overrides))


class TestResolveBnbComputeDtype:
    """``_resolve_bnb_compute_dtype`` maps the YAML string to a torch dtype."""

    @pytest.mark.parametrize(
        "dtype_str,attr",
        [
            ("bf16", "bfloat16"),
            ("bfloat16", "bfloat16"),
            ("fp16", "float16"),
            ("float16", "float16"),
            ("fp32", "float32"),
            ("float32", "float32"),
        ],
    )
    def test_known_dtype_strings_resolve(self, dtype_str, attr):
        import torch

        assert model_mod._resolve_bnb_compute_dtype(dtype_str) is getattr(torch, attr)

    def test_raises_on_unsupported_dtype(self):
        with pytest.raises(ValueError, match="Unsupported bnb_4bit"):
            model_mod._resolve_bnb_compute_dtype("int4")

    def test_auto_resolves_without_raising(self):
        # 'auto' picks bf16/fp16 from cuda support — both are valid torch
        # dtypes; we only assert it does not raise (the cuda-support check is
        # mocked so the bf16 branch is deterministic on a CPU runner).
        import torch

        with patch("torch.cuda.is_bf16_supported", return_value=False):
            assert model_mod._resolve_bnb_compute_dtype("auto") is torch.float16


class TestDeviceMap:
    """``_device_map_for`` picks a device_map suited to the environment."""

    def test_distributed_returns_none(self):
        cfg = _cfg()
        assert model_mod._device_map_for(cfg, is_distributed=True) is None

    def test_four_bit_returns_auto(self):
        cfg = _cfg(model={"name_or_path": "org/model", "load_in_4bit": True})
        assert model_mod._device_map_for(cfg, is_distributed=False) == "auto"

    def test_single_gpu_pins_device_zero(self):
        cfg = _cfg(model={"name_or_path": "org/model", "load_in_4bit": False})
        with patch("torch.cuda.is_available", return_value=True):
            assert model_mod._device_map_for(cfg, is_distributed=False) == {"": 0}

    def test_cpu_only_returns_none(self):
        cfg = _cfg(model={"name_or_path": "org/model", "load_in_4bit": False})
        with patch("torch.cuda.is_available", return_value=False):
            assert model_mod._device_map_for(cfg, is_distributed=False) is None


class TestBuildModelKwargs:
    """``_build_model_kwargs`` gates the quantization config on CUDA presence."""

    def test_quantization_keys_gated_off_without_cuda(self):
        # load_in_4bit=True but CUDA unavailable → no quantization_config key,
        # and the model loads full precision (the loud-skip path).
        cfg = _cfg(model={"name_or_path": "org/model", "load_in_4bit": True})
        with patch("torch.cuda.is_available", return_value=False):
            kwargs = model_mod._build_model_kwargs(cfg, trust_remote_code=False)
        assert "quantization_config" not in kwargs

    def test_quantization_config_added_with_cuda(self):
        # Explicit bf16 dtype so ``_resolve_bnb_compute_dtype`` does not take
        # the 'auto' branch (which calls torch.cuda.is_bf16_supported and
        # asserts on CUDA-less builds).
        cfg = _cfg(model={"name_or_path": "org/model", "load_in_4bit": True, "bnb_4bit_compute_dtype": "bf16"})
        fake_bnb = MagicMock(name="BitsAndBytesConfig")
        with (
            patch("torch.cuda.is_available", return_value=True),
            patch.dict(
                "sys.modules",
                {"transformers": MagicMock(BitsAndBytesConfig=fake_bnb)},
            ),
        ):
            kwargs = model_mod._build_model_kwargs(cfg, trust_remote_code=False)
        assert "quantization_config" in kwargs
        fake_bnb.assert_called_once()

    def test_rope_and_sliding_window_passed_through(self):
        cfg = _cfg(
            model={"name_or_path": "org/model", "load_in_4bit": False},
            training={"rope_scaling": {"type": "linear", "factor": 2.0}, "sliding_window_attention": 4096},
        )
        with patch("torch.cuda.is_available", return_value=False):
            kwargs = model_mod._build_model_kwargs(cfg, trust_remote_code=False)
        assert kwargs["rope_scaling"] == {"type": "linear", "factor": 2.0}
        assert kwargs["sliding_window"] == 4096


class TestLoadTokenizer:
    """``_load_tokenizer`` ensures a pad_token and routes VLMs to AutoProcessor."""

    def test_pad_token_falls_back_to_eos(self):
        # SimpleNamespace (not MagicMock) so the ``getattr(tok, "tokenizer",
        # tok)`` inner-tokenizer probe falls back to ``tok`` itself rather than
        # auto-vivifying a ``.tokenizer`` child mock.
        fake_tok = SimpleNamespace(pad_token=None, eos_token="</s>")
        fake_transformers = MagicMock()
        fake_transformers.AutoTokenizer.from_pretrained.return_value = fake_tok
        cfg = _cfg()
        with patch.dict("sys.modules", {"transformers": fake_transformers}):
            out = model_mod._load_tokenizer(cfg, trust_remote_code=False)
        assert out is fake_tok
        assert fake_tok.pad_token == "</s>"

    def test_existing_pad_token_preserved(self):
        fake_tok = SimpleNamespace(pad_token="[PAD]", eos_token="</s>")
        fake_transformers = MagicMock()
        fake_transformers.AutoTokenizer.from_pretrained.return_value = fake_tok
        cfg = _cfg()
        with patch.dict("sys.modules", {"transformers": fake_transformers}):
            model_mod._load_tokenizer(cfg, trust_remote_code=False)
        assert fake_tok.pad_token == "[PAD]"


class TestBuildLoraConfig:
    """``_build_lora_config`` resolves the PEFT method and target modules."""

    def test_lora_target_modules_reach_peft(self):
        captured = {}

        def _fake_lora_config(**kwargs):
            captured.update(kwargs)
            return MagicMock(name="LoraConfig")

        cfg = _cfg(lora={"target_modules": ["q_proj", "k_proj"], "r": 8})
        with patch.dict("sys.modules", {"peft": MagicMock(LoraConfig=_fake_lora_config)}):
            model_mod._build_lora_config(cfg)
        assert captured["target_modules"] == ["q_proj", "k_proj"]
        assert captured["r"] == 8
        # default method 'lora' → no PiSSA init key
        assert "init_lora_weights" not in captured

    def test_pissa_sets_init_weights(self):
        captured = {}

        def _fake_lora_config(**kwargs):
            captured.update(kwargs)
            return MagicMock(name="LoraConfig")

        cfg = _cfg(lora={"method": "pissa"})
        with patch.dict("sys.modules", {"peft": MagicMock(LoraConfig=_fake_lora_config)}):
            model_mod._build_lora_config(cfg)
        assert captured["init_lora_weights"] == "pissa"


class TestResolvePeftFlags:
    """``_resolve_peft_flags`` is the single source of (use_dora, use_rslora, method)."""

    def test_method_dora_enables_use_dora(self):
        cfg = _cfg(lora={"method": "dora"})
        use_dora, use_rslora, method = model_mod._resolve_peft_flags(cfg)
        assert use_dora is True
        assert method == "dora"

    def test_method_rslora_enables_use_rslora(self):
        cfg = _cfg(lora={"method": "rslora"})
        use_dora, use_rslora, method = model_mod._resolve_peft_flags(cfg)
        assert use_rslora is True
        assert method == "rslora"


# ---------------------------------------------------------------------------
# Hub revision pinning + provenance recording
# ---------------------------------------------------------------------------
#
# The invariant these tests exist to protect: a commit SHA recorded as
# provenance must have come from the load that actually happened.  Several of
# them assert a *negative* (no independent ``HfApi`` query, no requested-ref
# echoed into ``revision_resolved``) because the defect they guard against is
# invisible in a passing happy path.

SHA_A = "0" * 39 + "a"
SHA_B = "1" * 39 + "b"


@pytest.fixture(autouse=True)
def _clear_revision_registry():
    """Keep the process-wide resolved-revision registry from leaking across tests."""
    model_mod._RESOLVED_MODEL_REVISIONS.clear()
    yield
    model_mod._RESOLVED_MODEL_REVISIONS.clear()


def _stub_resolver(monkeypatch, **record_overrides):
    """Replace ``compliance.resolve_model_revision`` with a canned record."""
    from forgelm import compliance as compliance_mod

    seen = {}

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
        record.update(record_overrides)
        return record

    monkeypatch.setattr(compliance_mod, "resolve_model_revision", _fake)
    return seen


class TestPrepareRevisionPin:
    """``prepare_revision_pin`` resolves first and hands the caller what to pin to."""

    def test_resolved_sha_is_the_pin(self, monkeypatch):
        _stub_resolver(monkeypatch, revision_resolved=SHA_A, resolution_source="resolved")
        pin, record = model_mod.prepare_revision_pin("org/model", role=model_mod.ROLE_BASE_MODEL)
        assert pin == SHA_A
        assert record["revision_resolved"] == SHA_A
        assert record["revision_pinned"] == SHA_A
        assert record["role"] == model_mod.ROLE_BASE_MODEL

    def test_unconfirmed_pin_loads_the_requested_ref_but_records_no_sha(self, monkeypatch):
        # The load must still honour the operator's pin; the *record* must not
        # claim a SHA nobody confirmed.  Echoing ``requested`` into
        # ``revision_resolved`` here is the fabricated-SHA defect.
        _stub_resolver(monkeypatch, resolution_source="pinned_unverified")
        pin, record = model_mod.prepare_revision_pin("org/model", role=model_mod.ROLE_BASE_MODEL, requested="my-branch")
        assert pin == "my-branch"
        assert record["revision_resolved"] is None
        assert record["revision_pinned"] == "my-branch"

    def test_unresolved_and_unpinned_yields_no_pin(self, monkeypatch):
        _stub_resolver(monkeypatch, resolution_source="unresolved")
        pin, record = model_mod.prepare_revision_pin("org/model", role=model_mod.ROLE_BASE_MODEL)
        assert pin is None
        assert record["revision_resolved"] is None
        assert record["revision_pinned"] is None

    def test_local_path_resolves_to_no_pin(self, monkeypatch):
        _stub_resolver(monkeypatch, resolution_source="local_path")
        pin, record = model_mod.prepare_revision_pin("/models/local", role=model_mod.ROLE_BASE_MODEL)
        assert pin is None
        assert record["resolution_source"] == "local_path"

    def test_offline_flag_reaches_the_resolver(self, monkeypatch):
        seen = _stub_resolver(monkeypatch, resolution_source="unresolved")
        model_mod.prepare_revision_pin("org/model", role=model_mod.ROLE_BASE_MODEL, offline=True)
        assert seen["offline"] is True

    def test_offline_env_var_forces_offline_resolution(self, monkeypatch):
        seen = _stub_resolver(monkeypatch, resolution_source="unresolved")
        monkeypatch.setenv("HF_HUB_OFFLINE", "1")
        model_mod.prepare_revision_pin("org/model", role=model_mod.ROLE_BASE_MODEL, offline=False)
        assert seen["offline"] is True

    def test_unpinned_hub_repo_is_warned_by_name(self, monkeypatch, caplog):
        _stub_resolver(monkeypatch, resolution_source="unresolved")
        with caplog.at_level("WARNING", logger="forgelm.model"):
            model_mod.prepare_revision_pin("org/model", role=model_mod.ROLE_BASE_MODEL)
        assert "org/model" in caplog.text
        assert "UNPINNED" in caplog.text

    def test_local_path_is_not_warned_as_unpinned(self, monkeypatch, caplog):
        # A directory on disk has no Hub commit; warning about it is noise that
        # trains operators to ignore the warning that matters.
        _stub_resolver(monkeypatch, resolution_source="local_path")
        with caplog.at_level("WARNING", logger="forgelm.model"):
            model_mod.prepare_revision_pin("/models/local", role=model_mod.ROLE_BASE_MODEL)
        assert "UNPINNED" not in caplog.text


class TestRevisionRegistry:
    """The registry is the only sanctioned source of recorded model provenance."""

    def test_record_then_read_round_trips(self):
        record = {
            "repo_id": "org/model",
            "role": model_mod.ROLE_BASE_MODEL,
            "revision_resolved": SHA_A,
            "revision_requested": None,
            "resolution_source": "resolved",
            "revision_pinned": SHA_A,
        }
        model_mod.record_loaded_revision(record)
        assert model_mod.get_loaded_model_revision("org/model")["revision_resolved"] == SHA_A

    def test_unrecorded_repo_reads_as_unknown(self):
        assert model_mod.get_loaded_model_revision("org/never-loaded") is None

    def test_roles_do_not_overwrite_each_other(self):
        # Llama-Guard can be both safety classifier and judge; only one of them
        # may be pinned, and neither may inherit the other's provenance.
        model_mod.record_loaded_revision(
            {"repo_id": "meta/guard", "role": model_mod.ROLE_BASE_MODEL, "revision_resolved": SHA_A}
        )
        model_mod.record_loaded_revision(
            {"repo_id": "meta/guard", "role": model_mod.ROLE_SAFETY_CLASSIFIER, "revision_resolved": SHA_B}
        )
        assert model_mod.get_loaded_model_revision("meta/guard")["revision_resolved"] == SHA_A
        assert (
            model_mod.get_loaded_model_revision("meta/guard", model_mod.ROLE_SAFETY_CLASSIFIER)["revision_resolved"]
            == SHA_B
        )

    def test_returned_record_is_a_copy(self):
        model_mod.record_loaded_revision(
            {"repo_id": "org/model", "role": model_mod.ROLE_BASE_MODEL, "revision_resolved": SHA_A}
        )
        model_mod.get_loaded_model_revision("org/model")["revision_resolved"] = "tampered"
        assert model_mod.get_loaded_model_revision("org/model")["revision_resolved"] == SHA_A

    def test_record_without_repo_or_role_is_dropped(self):
        model_mod.record_loaded_revision({"role": model_mod.ROLE_BASE_MODEL})
        model_mod.record_loaded_revision({"repo_id": "org/model"})
        assert model_mod._RESOLVED_MODEL_REVISIONS == {}


class TestRevisionReachesEveryLoadSite:
    """All base-model load sites take the *same* pin, or none of them do.

    Missing one leaves the tokenizer free to drift from the weights while the
    manifest still reports a pin — a config-claimed guarantee that is only
    partly true, which is worse than no claim.
    """

    def test_build_model_kwargs_carries_revision(self):
        cfg = _cfg()
        with patch("torch.cuda.is_available", return_value=False):
            kwargs = model_mod._build_model_kwargs(cfg, trust_remote_code=False, revision=SHA_A)
        assert kwargs["revision"] == SHA_A

    def test_build_model_kwargs_defaults_revision_to_none(self):
        cfg = _cfg()
        with patch("torch.cuda.is_available", return_value=False):
            kwargs = model_mod._build_model_kwargs(cfg, trust_remote_code=False)
        assert kwargs["revision"] is None

    def test_tokenizer_receives_revision(self):
        captured = {}

        def _from_pretrained(path, **kwargs):
            captured.update(kwargs)
            tok = MagicMock()
            tok.pad_token = "<pad>"
            return tok

        cfg = _cfg()
        fake = MagicMock()
        fake.AutoTokenizer.from_pretrained = _from_pretrained
        with patch.dict("sys.modules", {"transformers": fake}):
            model_mod._load_tokenizer(cfg, trust_remote_code=False, revision=SHA_A)
        assert captured["revision"] == SHA_A

    def test_processor_receives_revision(self):
        captured = {}

        def _from_pretrained(path, **kwargs):
            captured.update(kwargs)
            proc = MagicMock()
            proc.tokenizer.pad_token = "<pad>"
            return proc

        cfg = _cfg(model={"name_or_path": "org/model", "multimodal": {"enabled": True}})
        fake = MagicMock()
        fake.AutoProcessor.from_pretrained = _from_pretrained
        with patch.dict("sys.modules", {"transformers": fake}):
            model_mod._load_tokenizer(cfg, trust_remote_code=False, revision=SHA_A)
        assert captured["revision"] == SHA_A


class TestUnslothRevisionSupport:
    """unsloth is an optional extra: whether it honours ``revision`` is decided
    against the installed package, never assumed."""

    def test_named_revision_parameter_counts_as_support(self):
        class _Loader:
            @staticmethod
            def from_pretrained(model_name, revision=None):
                return None, None

        assert model_mod._unsloth_accepts_revision(_Loader) is True

    def test_bare_kwargs_does_not_count_as_support(self):
        # A kwarg that is accepted and silently dropped is indistinguishable
        # from one that is honoured — exactly the false-pin this rejects.
        class _Loader:
            @staticmethod
            def from_pretrained(model_name, **kwargs):
                return None, None

        assert model_mod._unsloth_accepts_revision(_Loader) is False

    def test_uninspectable_loader_does_not_count_as_support(self):
        class _Loader:
            from_pretrained = object()

        assert model_mod._unsloth_accepts_revision(_Loader) is False

    def test_pin_on_unsupported_unsloth_fails_closed(self, monkeypatch):
        _stub_resolver(monkeypatch, revision_resolved=SHA_A, resolution_source="pinned_resolved")

        class _Loader:
            @staticmethod
            def from_pretrained(model_name, **kwargs):
                raise AssertionError("load must not run when the pin cannot be honoured")

        cfg = _cfg(model={"name_or_path": "org/model", "backend": "unsloth", "revision": SHA_A})
        with patch.dict("sys.modules", {"unsloth": MagicMock(FastLanguageModel=_Loader)}):
            with pytest.raises(RuntimeError, match="no 'revision' parameter"):
                model_mod._load_unsloth(cfg)
        assert model_mod.get_loaded_model_revision("org/model") is None

    def test_unpinned_unsloth_load_proceeds_and_records_nothing(self, monkeypatch, caplog):
        class _Loader:
            @staticmethod
            def from_pretrained(model_name, **kwargs):
                return MagicMock(), MagicMock()

            @staticmethod
            def get_peft_model(model, **kwargs):
                return model

        cfg = _cfg(model={"name_or_path": "org/model", "backend": "unsloth"})
        with patch.dict("sys.modules", {"unsloth": MagicMock(FastLanguageModel=_Loader)}):
            with caplog.at_level("WARNING", logger="forgelm.model"):
                model_mod._load_unsloth(cfg)
        assert "UNPINNED" in caplog.text
        assert model_mod.get_loaded_model_revision("org/model") is None

    def test_supported_unsloth_pins_and_records(self, monkeypatch):
        _stub_resolver(monkeypatch, revision_resolved=SHA_A, resolution_source="pinned_resolved")
        captured = {}

        class _Loader:
            @staticmethod
            def from_pretrained(model_name, revision=None, **kwargs):
                captured["revision"] = revision
                return MagicMock(), MagicMock()

            @staticmethod
            def get_peft_model(model, **kwargs):
                return model

        cfg = _cfg(model={"name_or_path": "org/model", "backend": "unsloth", "revision": SHA_A})
        with patch.dict("sys.modules", {"unsloth": MagicMock(FastLanguageModel=_Loader)}):
            model_mod._load_unsloth(cfg)
        assert captured["revision"] == SHA_A
        assert model_mod.get_loaded_model_revision("org/model")["revision_resolved"] == SHA_A


class TestGetModelAndTokenizerPinsEverything:
    """End-to-end: one resolve, one pin, every load site, recorded only after.

    This is the test that would catch wiring ``revision`` into
    ``_build_model_kwargs`` alone and letting the tokenizer regress silently.
    """

    def _fake_transformers(self, captured):
        def _tok(path, **kwargs):
            captured["tokenizer"] = kwargs.get("revision")
            tok = MagicMock()
            tok.pad_token = "<pad>"
            tok.pad_token_id = 0
            return tok

        def _model(path, **kwargs):
            captured["model"] = kwargs.get("revision")
            m = MagicMock()
            m.config.pad_token_id = None
            return m

        fake = MagicMock()
        fake.AutoTokenizer.from_pretrained = _tok
        fake.AutoModelForCausalLM.from_pretrained = _model
        return fake

    def _fake_peft(self):
        fake = MagicMock()
        fake.get_peft_model = lambda model, cfg: model
        fake.prepare_model_for_kbit_training = lambda model: model
        return fake

    def test_tokenizer_and_weights_share_one_resolved_pin(self, monkeypatch):
        _stub_resolver(monkeypatch, revision_resolved=SHA_A, resolution_source="resolved")
        captured = {}
        cfg = _cfg()
        with patch("torch.cuda.is_available", return_value=False):
            with patch.dict(
                "sys.modules",
                {"transformers": self._fake_transformers(captured), "peft": self._fake_peft()},
            ):
                model_mod.get_model_and_tokenizer(cfg)
        assert captured["tokenizer"] == SHA_A
        assert captured["model"] == SHA_A
        assert model_mod.get_loaded_model_revision("org/model")["revision_resolved"] == SHA_A

    def test_nothing_is_recorded_when_the_weight_load_fails(self, monkeypatch):
        # Provenance for a load that never completed is the same class of
        # falsehood as a SHA the load never used.
        _stub_resolver(monkeypatch, revision_resolved=SHA_A, resolution_source="resolved")
        captured = {}
        fake_tf = self._fake_transformers(captured)

        def _boom(path, **kwargs):
            raise OSError("hub down")

        fake_tf.AutoModelForCausalLM.from_pretrained = _boom
        cfg = _cfg()
        with patch("torch.cuda.is_available", return_value=False):
            with patch.dict("sys.modules", {"transformers": fake_tf, "peft": self._fake_peft()}):
                with pytest.raises(OSError):
                    model_mod.get_model_and_tokenizer(cfg)
        assert model_mod.get_loaded_model_revision("org/model") is None

    def test_unresolvable_repo_loads_unpinned_and_records_nothing(self, monkeypatch):
        _stub_resolver(monkeypatch, resolution_source="unresolved")
        captured = {}
        cfg = _cfg()
        with patch("torch.cuda.is_available", return_value=False):
            with patch.dict(
                "sys.modules",
                {"transformers": self._fake_transformers(captured), "peft": self._fake_peft()},
            ):
                model_mod.get_model_and_tokenizer(cfg)
        assert captured["tokenizer"] is None
        assert captured["model"] is None
        rec = model_mod.get_loaded_model_revision("org/model")
        assert rec is not None and rec["revision_resolved"] is None


class TestModelCardRevisionSnippet:
    """The generated card's usage snippet reproduces the pin, or says nothing.

    A card inside an Annex IV bundle that tells a downstream reader
    ``from_pretrained("org/model")`` with no revision hands them the repo's
    default branch — the exact property the surrounding bundle claims to
    establish.  Lives here because the snippet's only input is
    ``forgelm.model``'s resolved-revision registry.
    """

    def test_kwarg_emitted_when_the_base_sha_is_known(self):
        from forgelm import model_card as card_mod

        model_mod.record_loaded_revision(
            {"repo_id": "org/model", "role": model_mod.ROLE_BASE_MODEL, "revision_resolved": SHA_A}
        )
        cfg = _cfg()
        assert card_mod._base_model_revision_arg(cfg) == f', revision="{SHA_A}"'

    def test_kwarg_omitted_when_unknown(self):
        # Never ``revision="None"``: a broken snippet teaches nothing and a
        # fabricated one teaches something false.
        from forgelm import model_card as card_mod

        assert card_mod._base_model_revision_arg(_cfg()) == ""

    def test_unverified_pin_emits_no_kwarg(self):
        # revision_resolved is None here — the operator's ref was never
        # confirmed, so the card must not present it as a commit.
        from forgelm import model_card as card_mod

        model_mod.record_loaded_revision(
            {
                "repo_id": "org/model",
                "role": model_mod.ROLE_BASE_MODEL,
                "revision_resolved": None,
                "revision_pinned": "my-branch",
            }
        )
        assert card_mod._base_model_revision_arg(_cfg()) == ""

    def test_rendered_card_contains_the_revision(self, tmp_path):
        from forgelm import model_card as card_mod

        model_mod.record_loaded_revision(
            {"repo_id": "org/model", "role": model_mod.ROLE_BASE_MODEL, "revision_resolved": SHA_A}
        )
        path = card_mod.generate_model_card(_cfg(), {}, str(tmp_path))
        body = open(path, encoding="utf-8").read()
        assert f'from_pretrained("org/model", revision="{SHA_A}")' in body

    def test_rendered_card_is_valid_python_when_unpinned(self, tmp_path):
        from forgelm import model_card as card_mod

        path = card_mod.generate_model_card(_cfg(), {}, str(tmp_path))
        body = open(path, encoding="utf-8").read()
        assert 'from_pretrained("org/model")' in body
        assert "revision=" not in body
