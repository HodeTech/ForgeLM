"""Tests for synthetic data generation pipeline."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from forgelm.config import ForgeConfig, load_config
from forgelm.synthetic import SyntheticDataGenerator, SyntheticResult


@pytest.fixture(autouse=True)
def _stub_ssrf_resolver(monkeypatch):
    """Auto-stub ``forgelm._http._resolve_safe_destination`` so synthetic
    teacher tests do not require live DNS resolution of API endpoints.
    See ``tests/test_webhook.py`` for the same pattern + full rationale.
    Dedicated resolver coverage lives in ``tests/test_http_dns_rebinding.py``.
    """
    import ipaddress

    from forgelm import _http

    def _hermetic_resolver(host):
        if not host:
            return None, "empty host"
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        if literal is not None:
            if _http._is_blocked_ip(literal):
                return None, "Private/loopback/IMDS destination"
            return host, None
        if host == "localhost":
            return None, "Private/loopback/IMDS destination"
        return "8.8.8.8", None

    monkeypatch.setattr(_http, "_resolve_safe_destination", _hermetic_resolver)


BASE = {
    "model": {"name_or_path": "test/model"},
    "lora": {"r": 16, "alpha": 32},
    "data": {"dataset_name_or_path": "test.jsonl"},
    "training": {"output_dir": "./out"},
}


def _config(**overrides):
    cfg = {**BASE}
    for key, val in overrides.items():
        cfg[key] = val
    return ForgeConfig(**cfg)


class TestSyntheticConfig:
    def test_disabled_by_default(self):
        config = _config()
        assert config.synthetic is None

    def test_enabled_config(self):
        config = _config(
            synthetic={
                "enabled": True,
                "teacher_model": "gpt-4",
                "teacher_backend": "api",
                "api_base": "https://api.openai.com/v1",
                "seed_prompts": ["What is AI?", "Explain ML."],
            }
        )
        assert config.synthetic.enabled is True
        assert config.synthetic.teacher_model == "gpt-4"
        assert config.synthetic.teacher_backend == "api"
        assert len(config.synthetic.seed_prompts) == 2

    def test_defaults(self):
        config = _config(synthetic={"enabled": True, "teacher_model": "gpt-4", "seed_prompts": ["q"]})
        assert config.synthetic.temperature == pytest.approx(0.7)
        assert config.synthetic.max_new_tokens == 1024
        assert config.synthetic.output_format == "messages"
        assert config.synthetic.api_delay == pytest.approx(0.5)
        assert config.synthetic.api_timeout == 60
        assert config.synthetic.output_file == "synthetic_data.jsonl"

    def test_local_backend(self):
        config = _config(
            synthetic={
                "enabled": True,
                "teacher_model": "meta-llama/Llama-3-8B",
                "teacher_backend": "local",
                "seed_prompts": ["q"],
            }
        )
        assert config.synthetic.teacher_backend == "local"

    def test_file_backend(self):
        config = _config(
            synthetic={
                "enabled": True,
                "teacher_model": "n/a",
                "teacher_backend": "file",
                "seed_file": "responses.jsonl",
            }
        )
        assert config.synthetic.teacher_backend == "file"


class TestSyntheticResult:
    def test_success_rate_zero(self):
        r = SyntheticResult(total_prompts=0)
        assert r.success_rate == pytest.approx(0.0)

    def test_success_rate_partial(self):
        r = SyntheticResult(total_prompts=10, successful=7, failed=3)
        assert r.success_rate == pytest.approx(0.7)

    def test_success_rate_full(self):
        r = SyntheticResult(total_prompts=5, successful=5, failed=0)
        assert r.success_rate == pytest.approx(1.0)


class TestSyntheticGenerator:
    def test_raises_if_not_enabled(self):
        config = _config()
        with pytest.raises(ValueError, match="not enabled"):
            SyntheticDataGenerator(config)

    def test_load_seed_prompts_inline(self):
        config = _config(
            synthetic={
                "enabled": True,
                "teacher_model": "test",
                "seed_prompts": ["prompt1", "prompt2", "prompt3"],
            }
        )
        gen = SyntheticDataGenerator(config)
        prompts = gen._load_seed_prompts()
        assert prompts == ["prompt1", "prompt2", "prompt3"]

    def test_load_seed_prompts_from_text_file(self, tmp_path):
        seed_file = tmp_path / "seeds.txt"
        seed_file.write_text("What is Python?\nExplain recursion.\n\nHow does TCP work?\n")

        config = _config(
            synthetic={
                "enabled": True,
                "teacher_model": "test",
                "seed_file": str(seed_file),
            }
        )
        gen = SyntheticDataGenerator(config)
        prompts = gen._load_seed_prompts()
        assert len(prompts) == 3
        assert "What is Python?" in prompts

    def test_load_seed_prompts_from_jsonl(self, tmp_path):
        seed_file = tmp_path / "seeds.jsonl"
        lines = [
            json.dumps({"prompt": "What is AI?"}),
            json.dumps({"prompt": "Explain ML."}),
        ]
        seed_file.write_text("\n".join(lines))

        config = _config(
            synthetic={
                "enabled": True,
                "teacher_model": "test",
                "seed_file": str(seed_file),
            }
        )
        gen = SyntheticDataGenerator(config)
        prompts = gen._load_seed_prompts()
        assert len(prompts) == 2
        assert prompts[0] == "What is AI?"

    def test_format_entry_messages(self):
        config = _config(
            synthetic={
                "enabled": True,
                "teacher_model": "test",
                "output_format": "messages",
                "system_prompt": "Be helpful.",
                "seed_prompts": ["q"],
            }
        )
        gen = SyntheticDataGenerator(config)
        entry = gen._format_entry("What is AI?", "AI is artificial intelligence.")
        assert "messages" in entry
        assert len(entry["messages"]) == 3  # system + user + assistant
        assert entry["messages"][0]["role"] == "system"
        assert entry["messages"][1]["content"] == "What is AI?"
        assert entry["messages"][2]["content"] == "AI is artificial intelligence."

    def test_format_entry_instruction(self):
        config = _config(
            synthetic={
                "enabled": True,
                "teacher_model": "test",
                "output_format": "instruction",
                "seed_prompts": ["q"],
            }
        )
        gen = SyntheticDataGenerator(config)
        entry = gen._format_entry("What is AI?", "AI is artificial intelligence.")
        assert entry == {"instruction": "What is AI?", "output": "AI is artificial intelligence."}

    def test_format_entry_chatml(self):
        config = _config(
            synthetic={
                "enabled": True,
                "teacher_model": "test",
                "output_format": "chatml",
                "seed_prompts": ["q"],
            }
        )
        gen = SyntheticDataGenerator(config)
        entry = gen._format_entry("Q?", "A.")
        # 'chatml' emits the legacy {User, Assistant} key layout,
        # NOT OpenAI <|im_start|> ChatML markup — and there must be no im_start.
        assert entry == {"User": "Q?", "Assistant": "A."}
        assert "<|im_start|>" not in json.dumps(entry)

    def test_chatml_naming_discrepancy_documented(self):
        """The schema description must warn that 'chatml' is the User/Assistant
        layout, not <|im_start|> markup, so the naming drift is not silent."""
        from forgelm.config import SyntheticConfig

        desc = SyntheticConfig.model_fields["output_format"].description
        assert "User, Assistant" in desc
        assert "im_start" in desc

    def test_file_backend_generate(self, tmp_path):
        """Test file-based teacher (pre-generated responses)."""
        seed_file = tmp_path / "seeds.jsonl"
        lines = [
            json.dumps({"prompt": "What is AI?", "response": "AI is artificial intelligence."}),
            json.dumps({"prompt": "What is ML?", "response": "ML is machine learning."}),
        ]
        seed_file.write_text("\n".join(lines))

        output_file = tmp_path / "output.jsonl"
        config = _config(
            synthetic={
                "enabled": True,
                "teacher_model": "n/a",
                "teacher_backend": "file",
                "seed_file": str(seed_file),
                "output_file": str(output_file),
                "output_format": "instruction",
            }
        )

        gen = SyntheticDataGenerator(config)
        result = gen.generate()

        assert result.total_prompts == 2
        assert result.successful == 2
        assert result.failed == 0
        assert os.path.isfile(str(output_file))

        with open(str(output_file)) as f:
            entries = [json.loads(line) for line in f]
        assert len(entries) == 2
        assert entries[0]["instruction"] == "What is AI?"
        assert entries[0]["output"] == "AI is artificial intelligence."

    def test_generate_flushes_incrementally_survives_mid_run_crash(self, tmp_path):
        """Entries must be flushed to disk as each prompt succeeds, not
        buffered in memory and written once at the end — a crash partway
        through a run must not lose the already-generated (and, for an API
        teacher, already-paid-for) rows written before the interruption."""
        seed_file = tmp_path / "seeds.jsonl"
        seed_file.write_text("\n".join(json.dumps({"prompt": f"Q{i}", "response": f"A{i}"}) for i in range(3)))
        output_file = tmp_path / "output.jsonl"
        config = _config(
            synthetic={
                "enabled": True,
                "teacher_model": "n/a",
                "teacher_backend": "file",
                "seed_file": str(seed_file),
                "output_file": str(output_file),
                "output_format": "instruction",
                "seed_prompts": [f"Q{i}" for i in range(3)],
            }
        )
        gen = SyntheticDataGenerator(config)

        # Simulate a crash after the 2nd successful entry: raise once
        # _generate_one has already appended two entries to the (real,
        # unmocked) open file handle, then verify those two rows survived.
        real_generate_one = gen._generate_one
        call_count = {"n": 0}

        def _crash_after_two(prompt, idx, result):
            call_count["n"] += 1
            if call_count["n"] > 2:
                raise RuntimeError("simulated crash")
            return real_generate_one(prompt, idx, result)

        gen._generate_one = _crash_after_two

        with pytest.raises(RuntimeError, match="simulated crash"):
            gen.generate()

        assert os.path.isfile(str(output_file))
        with open(str(output_file)) as f:
            entries = [json.loads(line) for line in f]
        # The first two entries (written+flushed before the crash) survive
        # on disk even though generate() never returned.
        assert len(entries) == 2
        assert entries[0]["instruction"] == "Q0"
        assert entries[1]["instruction"] == "Q1"

    def test_generate_no_output_file_when_zero_successes(self, tmp_path):
        """Preserves the pre-existing contract: a run with zero successful
        generations must not create the output file at all."""
        output_file = tmp_path / "output.jsonl"
        config = _config(
            synthetic={
                "enabled": True,
                "teacher_model": "n/a",
                "teacher_backend": "file",
                # No seed_file configured for the "file" backend responses ->
                # every prompt resolves to an empty response -> every
                # _generate_one call records a failure, not a success.
                "output_file": str(output_file),
                "seed_prompts": ["Q0", "Q1"],
            }
        )
        gen = SyntheticDataGenerator(config)
        result = gen.generate()

        assert result.successful == 0
        assert not os.path.exists(str(output_file))

    def test_empty_prompts_no_crash(self, tmp_path):
        # The config validator now rejects enabled+no-seeds, so reach the
        # zero-prompt runtime path via an empty seed file (still a valid config).
        seed_file = tmp_path / "empty_seeds.txt"
        seed_file.write_text("")
        config = _config(
            synthetic={
                "enabled": True,
                "teacher_model": "test",
                "seed_file": str(seed_file),
            }
        )
        gen = SyntheticDataGenerator(config)
        result = gen.generate()
        assert result.total_prompts == 0
        assert result.successful == 0

    def test_seed_loader_skips_non_dict_json_line(self, tmp_path):
        """A valid-JSON-but-non-object seed line (array, bare number, …) must
        not crash the loader with AttributeError; it falls back to treating
        the raw line as a plain-text prompt."""
        seed_file = tmp_path / "seeds.jsonl"
        seed_file.write_text(
            "\n".join(
                [
                    json.dumps({"prompt": "What is AI?"}),
                    "[1, 2, 3]",  # valid JSON array — has no .get
                    "42",  # valid JSON number
                    json.dumps({"prompt": "Explain ML."}),
                ]
            )
        )
        config = _config(synthetic={"enabled": True, "teacher_model": "test", "seed_file": str(seed_file)})
        gen = SyntheticDataGenerator(config)
        prompts = gen._load_seed_prompts()  # must not raise
        assert "What is AI?" in prompts
        assert "Explain ML." in prompts
        # The non-object lines survive as raw-text prompts rather than aborting.
        assert "[1, 2, 3]" in prompts
        assert "42" in prompts

    def test_seed_loader_bare_string_line_uses_parsed_value_not_quotes(self, tmp_path):
        """PR#63 review: a bare-string JSON seed line (``"prompt text"``) must
        append the parsed string itself, not the raw line with its JSON quotes —
        matching ``safety._load_safety_prompts``."""
        seed_file = tmp_path / "seeds.jsonl"
        seed_file.write_text(
            "\n".join(
                [
                    json.dumps("What is AI?"),  # bare quoted-string JSON line
                    "Explain ML.",  # plain text (not JSON)
                ]
            )
        )
        config = _config(synthetic={"enabled": True, "teacher_model": "test", "seed_file": str(seed_file)})
        gen = SyntheticDataGenerator(config)
        prompts = gen._load_seed_prompts()
        # The parsed value, without the JSON quotes that ``"..."`` would carry.
        assert "What is AI?" in prompts
        assert '"What is AI?"' not in prompts
        assert "Explain ML." in prompts

    def test_file_responses_skips_non_dict_json_line(self, tmp_path):
        """``_load_file_responses`` must count a non-object JSON line as
        malformed and skip it, honouring its 'loud about failures' docstring
        instead of crashing with AttributeError."""
        seed_file = tmp_path / "responses.jsonl"
        seed_file.write_text(
            "\n".join(
                [
                    json.dumps({"prompt": "Q1", "response": "A1"}),
                    "[1, 2, 3]",  # valid JSON, non-object
                    json.dumps({"prompt": "Q2", "response": "A2"}),
                ]
            )
        )
        config = _config(
            synthetic={
                "enabled": True,
                "teacher_model": "n/a",
                "teacher_backend": "file",
                "seed_file": str(seed_file),
            }
        )
        gen = SyntheticDataGenerator(config)
        responses = gen._load_file_responses()  # must not raise
        assert responses == {"Q1": "A1", "Q2": "A2"}

    def test_file_teacher_lookup_is_byte_exact_not_hashed(self, tmp_path):
        """The file-teacher stores responses keyed by the *exact prompt string*
        (no hashing, no whitespace normalisation). Pin that semantics: a
        byte-identical prompt resolves, but a whitespace-variant of the same
        prompt misses (returns the empty default) — which would not happen
        under a normalising/hash key."""
        seed_file = tmp_path / "responses.jsonl"
        seed_file.write_text(json.dumps({"prompt": "What is AI?", "response": "A intelligence."}))
        config = _config(
            synthetic={
                "enabled": True,
                "teacher_model": "n/a",
                "teacher_backend": "file",
                "seed_file": str(seed_file),
            }
        )
        gen = SyntheticDataGenerator(config)

        assert gen._call_file_teacher("What is AI?") == "A intelligence."
        # Trailing-space variant is a distinct key under byte-exact lookup.
        assert gen._call_file_teacher("What is AI? ") == ""


class TestSyntheticYaml:
    def test_yaml_round_trip(self, tmp_path):
        yaml_content = """
model:
  name_or_path: "test/model"
lora:
  r: 16
  alpha: 32
data:
  dataset_name_or_path: "test.jsonl"
training:
  output_dir: "./out"
synthetic:
  enabled: true
  teacher_model: "gpt-4o"
  teacher_backend: "api"
  api_base: "https://api.openai.com/v1"
  api_key_env: "OPENAI_API_KEY"
  temperature: 0.5
  output_format: "messages"
  seed_prompts:
    - "What is AI?"
"""
        config_file = tmp_path / "synth.yaml"
        config_file.write_text(yaml_content)
        config = load_config(str(config_file))

        assert config.synthetic.enabled is True
        assert config.synthetic.teacher_model == "gpt-4o"
        assert config.synthetic.temperature == pytest.approx(0.5)

    def test_config_template_still_valid(self):
        config = load_config("config_template.yaml")
        assert config.synthetic is None


class TestSyntheticUsesSafePost:
    """Phase 7: synthetic._call_api_teacher must route through forgelm._http.safe_post.

    Same rationale as the judge equivalent — every outbound HTTP call site
    in the codebase shares one policy gate. Synthetic data generation hits
    OpenAI-compatible APIs with a bearer token; SSRF / scheme / redirect /
    timeout discipline must apply here too.
    """

    def test_imports_safe_post(self):
        """synthetic._call_api_teacher must use safe_post."""
        import inspect

        from forgelm import synthetic

        src = inspect.getsource(synthetic.SyntheticDataGenerator._call_api_teacher)
        assert "safe_post" in src, "synthetic._call_api_teacher must use safe_post"

    @patch("forgelm._http.requests.Session.post")
    def test_synthetic_call_goes_through_safe_post(self, mock_post):
        """A successful API teacher call routes through safe_post → requests.post."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"choices": [{"message": {"content": "synthetic response"}}]}
        mock_response.raise_for_status = MagicMock()
        mock_response.ok = True
        mock_post.return_value = mock_response

        config = _config(
            synthetic={
                "enabled": True,
                "teacher_model": "gpt-4",
                "teacher_backend": "api",
                "api_base": "https://api.openai.com/v1",
                "api_timeout": 30,
                "seed_prompts": ["What is AI?"],
            }
        )
        gen = SyntheticDataGenerator(config)
        response = gen._call_api_teacher("What is AI?")

        assert response == "synthetic response"
        mock_post.assert_called_once()
        kwargs = mock_post.call_args.kwargs
        # safe_post forwards allow_redirects=False
        assert kwargs.get("allow_redirects") is False

    @patch("forgelm._http.requests.Session.post")
    def test_synthetic_ssrf_block_for_private_api_base(self, mock_post):
        """A private-IP api_base must be rejected before any network call."""
        from forgelm._http import HttpSafetyError

        config = _config(
            synthetic={
                "enabled": True,
                "teacher_model": "gpt-4",
                "teacher_backend": "api",
                "api_base": "https://10.0.0.5/v1",  # NOSONAR RFC1918 — SSRF guard fixture (intentional)
                "api_timeout": 30,
                "seed_prompts": ["x"],
            }
        )
        gen = SyntheticDataGenerator(config)

        with pytest.raises(HttpSafetyError, match="Private/loopback"):
            gen._call_api_teacher("x")

        mock_post.assert_not_called()


class TestNoTrackingIDsInSource:
    """Coding-standard guard: inline comments must not embed internal review
    tracking labels (e.g. F-P6-OPUS-08, F-P3-FABLE-62).  This test fails if
    any such ID is re-introduced into synthetic.py, ensuring compliance with
    docs/standards/coding.md §Comments which prohibits PR/issue/fix references
    in source code."""

    def test_synthetic_py_has_no_review_tracking_ids(self):
        import inspect
        import re

        from forgelm import synthetic

        source = inspect.getsource(synthetic)
        # Pattern: F-P<digit(s)>-<LETTERS>-<digit(s)>  (e.g. F-P6-OPUS-08)
        tracking_id_re = re.compile(r"\bF-P\d+-[A-Z]+-\d+\b")
        matches = tracking_id_re.findall(source)
        assert not matches, (
            f"synthetic.py contains internal review tracking ID(s) in inline "
            f"comments, violating coding.md §Comments: {matches!r}.  "
            f"Strip the ID prefix and keep only the explanatory rationale."
        )


# ---------------------------------------------------------------------------
# Revision pinning for the local teacher model
# ---------------------------------------------------------------------------


class TestLocalTeacherRevisionPin:
    """``synthetic.teacher_revision`` reaches both teacher loads.

    The teacher's generations *become* the training corpus, so an unpinned
    teacher is an Article 10 data-provenance gap, not merely a reproducibility
    inconvenience.
    """

    _SHA = "0" * 39 + "a"

    @pytest.fixture(autouse=True)
    def _clean_registry(self):
        from forgelm import model as model_mod

        model_mod._RESOLVED_MODEL_REVISIONS.clear()
        yield
        model_mod._RESOLVED_MODEL_REVISIONS.clear()

    @pytest.fixture
    def stub_resolver(self, monkeypatch):
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

    def _generator(self, minimal_config, **synth_overrides):
        synthetic = {
            "enabled": True,
            "teacher_backend": "local",
            "teacher_model": "org/teacher",
            "seed_prompts": ["hello"],
        }
        synthetic.update(synth_overrides)
        cfg = ForgeConfig(**minimal_config(synthetic=synthetic))
        return SyntheticDataGenerator(cfg)

    def _fake_transformers(self, captured, fail_model=False):
        def _tok(path, **kwargs):
            captured["tokenizer"] = kwargs.get("revision")
            return MagicMock()

        def _model(path, **kwargs):
            captured["model"] = kwargs.get("revision")
            if fail_model:
                raise OSError("hub down")
            return MagicMock()

        stub = MagicMock()
        stub.AutoTokenizer.from_pretrained = _tok
        stub.AutoModelForCausalLM.from_pretrained = _model
        return stub

    def test_resolved_sha_reaches_both_loads(self, minimal_config, stub_resolver):
        stub_resolver(revision_resolved=self._SHA, resolution_source="pinned_resolved")
        captured = {}
        gen = self._generator(minimal_config, teacher_revision=self._SHA)
        with patch("torch.cuda.is_available", return_value=False):
            with patch.dict("sys.modules", {"transformers": self._fake_transformers(captured)}):
                gen._load_local_teacher()
        assert captured["tokenizer"] == self._SHA
        assert captured["model"] == self._SHA

    def test_configured_revision_is_what_gets_resolved(self, minimal_config, stub_resolver):
        seen = stub_resolver(revision_resolved=self._SHA, resolution_source="pinned_resolved")
        gen = self._generator(minimal_config, teacher_revision="v2")
        with patch("torch.cuda.is_available", return_value=False):
            with patch.dict("sys.modules", {"transformers": self._fake_transformers({})}):
                gen._load_local_teacher()
        assert seen["requested"] == "v2"

    def test_unpinned_teacher_load_is_unchanged(self, minimal_config, stub_resolver):
        stub_resolver(resolution_source="unresolved")
        captured = {}
        gen = self._generator(minimal_config)
        with patch("torch.cuda.is_available", return_value=False):
            with patch.dict("sys.modules", {"transformers": self._fake_transformers(captured)}):
                gen._load_local_teacher()
        assert captured["tokenizer"] is None
        assert captured["model"] is None

    def test_recorded_under_the_teacher_role_only(self, minimal_config, stub_resolver):
        from forgelm import model as model_mod

        stub_resolver(revision_resolved=self._SHA, resolution_source="pinned_resolved")
        gen = self._generator(minimal_config, teacher_revision=self._SHA)
        with patch("torch.cuda.is_available", return_value=False):
            with patch.dict("sys.modules", {"transformers": self._fake_transformers({})}):
                gen._load_local_teacher()
        assert (
            model_mod.get_loaded_model_revision("org/teacher", model_mod.ROLE_TEACHER_MODEL)["revision_resolved"]
            == self._SHA
        )
        assert model_mod.get_loaded_model_revision("org/teacher") is None

    def test_nothing_recorded_when_the_load_fails(self, minimal_config, stub_resolver):
        from forgelm import model as model_mod

        stub_resolver(revision_resolved=self._SHA, resolution_source="pinned_resolved")
        gen = self._generator(minimal_config, teacher_revision=self._SHA)
        with patch("torch.cuda.is_available", return_value=False):
            with patch.dict("sys.modules", {"transformers": self._fake_transformers({}, fail_model=True)}):
                with pytest.raises(OSError):
                    gen._load_local_teacher()
        assert model_mod.get_loaded_model_revision("org/teacher", model_mod.ROLE_TEACHER_MODEL) is None

    def test_model_offline_flag_reaches_the_resolver(self, minimal_config, stub_resolver):
        seen = stub_resolver(resolution_source="unresolved")
        cfg = ForgeConfig(
            **minimal_config(
                model={"name_or_path": "org/model", "offline": True},
                synthetic={
                    "enabled": True,
                    "teacher_backend": "local",
                    "teacher_model": "org/teacher",
                    "seed_prompts": ["hello"],
                },
            )
        )
        gen = SyntheticDataGenerator(cfg)
        with patch("torch.cuda.is_available", return_value=False):
            with patch.dict("sys.modules", {"transformers": self._fake_transformers({})}):
                gen._load_local_teacher()
        assert seen["offline"] is True
