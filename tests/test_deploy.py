"""Unit tests for forgelm.deploy module."""

from __future__ import annotations

import json
import os

import yaml

from forgelm.deploy import (
    SUPPORTED_TARGETS,
    DeployResult,
    _hf_endpoints_json,
    _ollama_modelfile,
    _tgi_compose,
    _vllm_config,
    generate_deploy_config,
)

# ---------------------------------------------------------------------------
# Constant / structure tests
# ---------------------------------------------------------------------------


class TestConstants:
    def test_supported_targets(self):
        assert "ollama" in SUPPORTED_TARGETS
        assert "vllm" in SUPPORTED_TARGETS
        assert "tgi" in SUPPORTED_TARGETS
        assert "hf-endpoints" in SUPPORTED_TARGETS

    def test_deploy_result_dataclass(self, tmp_path):
        r = DeployResult(success=True, target="ollama", output_path=str(tmp_path / "Modelfile"))
        assert r.success is True
        assert r.error is None


# ---------------------------------------------------------------------------
# Per-target generator tests (no file I/O)
# ---------------------------------------------------------------------------


class TestOllamaModelfile:
    def test_contains_from_directive(self):
        content = _ollama_modelfile("/path/to/model", None, 4096, 0.7, 50, 0.9)
        assert "FROM /path/to/model" in content

    def test_contains_parameters(self):
        content = _ollama_modelfile("/path/to/model", None, 4096, 0.7, 50, 0.9)
        assert "PARAMETER temperature 0.7" in content
        assert "PARAMETER top_k 50" in content
        assert "PARAMETER top_p 0.9" in content
        assert "PARAMETER num_ctx 4096" in content

    def test_system_prompt_included(self):
        content = _ollama_modelfile("/model", "Be helpful.", 2048, 0.8, 40, 0.95)
        assert "SYSTEM" in content
        assert "Be helpful." in content

    def test_system_prompt_with_quotes_escaped(self):
        content = _ollama_modelfile("/model", 'Say "hello"', 2048, 0.7, 50, 0.9)
        assert '\\"hello\\"' in content

    def test_no_system_when_none(self):
        content = _ollama_modelfile("/model", None, 2048, 0.7, 50, 0.9)
        assert "SYSTEM" not in content

    def test_newline_terminated(self):
        content = _ollama_modelfile("/model", None, 2048, 0.7, 50, 0.9)
        assert content.endswith("\n")


class TestVllmConfig:
    def test_is_valid_yaml(self):
        content = _vllm_config("/model", 4096, False, 0.9, "bfloat16")
        # Should parse without error (comment lines may be at end)
        parsed = yaml.safe_load(content.split("\n# ")[0])
        assert isinstance(parsed, dict)

    def test_contains_model_field(self):
        content = _vllm_config("/model", 4096, False, 0.9, "bfloat16")
        assert "model: /model" in content

    def test_gpu_memory_utilization_present(self):
        content = _vllm_config("/model", 4096, False, 0.85, "bfloat16")
        assert "0.85" in content

    def test_trust_remote_code(self):
        content = _vllm_config("/model", 4096, True, 0.9, "bfloat16")
        assert "true" in content.lower()


class TestTgiCompose:
    def test_is_valid_yaml(self):
        content = _tgi_compose("/model", 2048, 4096, 8080)
        # Skip header comment lines
        body = "\n".join(l for l in content.splitlines() if not l.startswith("#"))
        parsed = yaml.safe_load(body)
        assert isinstance(parsed, dict)
        assert "services" in parsed

    def test_contains_model_path(self):
        # ``_tgi_compose`` runs the input path through ``os.path.abspath``
        # before mounting it into the container; on POSIX the input is
        # already absolute and round-trips verbatim, on Windows the
        # string is normalised to a drive-anchored path
        # (``/my/model`` → ``D:\my\model``).  Assert that the abspath
        # form is what gets baked into the compose file so the test
        # passes on every cross-OS publish-matrix combo.
        model_path = "/my/model"
        content = _tgi_compose(model_path, 2048, 4096, 8080)
        assert os.path.abspath(model_path) in content

    def test_port_appears_in_config(self):
        content = _tgi_compose("/model", 2048, 4096, 9090)
        assert "9090" in content

    def test_max_tokens_in_command(self):
        content = _tgi_compose("/model", 1024, 2048, 8080)
        assert "max-input-length 1024" in content
        assert "max-total-tokens 2048" in content


class TestHfEndpointsJson:
    def test_is_valid_json(self):
        content = _hf_endpoints_json("/model", "text-generation", "x2", "nvidia-a10g", "us-east-1", "pytorch")
        parsed = json.loads(content)
        assert isinstance(parsed, dict)

    def test_model_repository(self):
        content = _hf_endpoints_json("/my/model", "text-generation", "x2", "nvidia-a10g", "us-east-1", "pytorch")
        parsed = json.loads(content)
        assert parsed["model"]["repository"] == "/my/model"

    def test_framework_present(self):
        content = _hf_endpoints_json("/model", "text-generation", "x2", "nvidia-a10g", "us-east-1", "pytorch")
        parsed = json.loads(content)
        assert parsed["model"]["framework"] == "pytorch"

    def test_region_in_cloud(self):
        content = _hf_endpoints_json("/model", "text-generation", "x2", "nvidia-a10g", "eu-west-1", "pytorch")
        parsed = json.loads(content)
        assert parsed["cloud"]["region"] == "eu-west-1"


# ---------------------------------------------------------------------------
# generate_deploy_config integration tests (with file I/O)
# ---------------------------------------------------------------------------


class TestGenerateDeployConfig:
    def test_ollama_writes_file(self, tmp_path):
        model_dir = tmp_path / "model"
        model_dir.mkdir()
        out = str(tmp_path / "Modelfile")
        result = generate_deploy_config(str(model_dir), "ollama", out)

        assert result.success is True
        assert result.target == "ollama"
        assert result.output_path == out
        assert os.path.isfile(out)

    def test_ollama_file_content_valid(self, tmp_path):
        model_dir = tmp_path / "model"
        model_dir.mkdir()
        out = str(tmp_path / "Modelfile")
        generate_deploy_config(str(model_dir), "ollama", out, system_prompt="Be brief.")

        with open(out) as f:
            content = f.read()
        assert f"FROM {model_dir}" in content
        assert "Be brief." in content

    def test_vllm_writes_file(self, tmp_path):
        out = str(tmp_path / "vllm.yaml")
        # vllm accepts HF Hub IDs; no local-path requirement
        result = generate_deploy_config("/model", "vllm", out)

        assert result.success is True
        assert os.path.isfile(out)

    def test_tgi_writes_file(self, tmp_path):
        model_dir = tmp_path / "model"
        model_dir.mkdir()
        out = str(tmp_path / "docker-compose.yaml")
        result = generate_deploy_config(str(model_dir), "tgi", out, port=8080)

        assert result.success is True
        assert os.path.isfile(out)

    def test_hf_endpoints_writes_file(self, tmp_path):
        out = str(tmp_path / "endpoint.json")
        # hf-endpoints expects HF Hub repository IDs; no local-path requirement
        result = generate_deploy_config("/model", "hf-endpoints", out)

        assert result.success is True
        assert os.path.isfile(out)
        with open(out) as f:
            parsed = json.load(f)
        assert "model" in parsed

    def test_default_filename_used_when_output_none(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "model").mkdir()
        result = generate_deploy_config("./model", "ollama")
        assert result.success is True
        assert result.output_path == "Modelfile"
        assert os.path.isfile("Modelfile")

    def test_unsupported_target_returns_failure(self):
        result = generate_deploy_config("/model", "nonexistent_runtime")
        assert result.success is False
        assert "nonexistent_runtime" in result.error

    def test_tgi_rejects_non_local_path(self, tmp_path):
        out = str(tmp_path / "docker-compose.yaml")
        # An HF Hub ID like "meta-llama/Llama-3-8B" must not silently produce
        # a config that tries to mount a non-existent volume at deploy time.
        result = generate_deploy_config("meta-llama/Llama-3-8B", "tgi", out)
        assert result.success is False
        assert "local model directory" in result.error.lower()

    def test_ollama_rejects_non_local_path(self, tmp_path):
        out = str(tmp_path / "Modelfile")
        result = generate_deploy_config("nonexistent/model-id", "ollama", out)
        assert result.success is False
        assert "local model directory" in result.error.lower()

    def test_result_contains_content(self, tmp_path):
        model_dir = tmp_path / "model"
        model_dir.mkdir()
        out = str(tmp_path / "Modelfile")
        result = generate_deploy_config(str(model_dir), "ollama", out)

        assert result.content is not None
        assert f"FROM {model_dir}" in result.content

    def test_target_case_insensitive(self, tmp_path):
        model_dir = tmp_path / "model"
        model_dir.mkdir()
        out = str(tmp_path / "Modelfile")
        result = generate_deploy_config(str(model_dir), "Ollama", out)
        assert result.success is True

    def test_vllm_trust_remote_code_propagated(self, tmp_path):
        out = str(tmp_path / "vllm.yaml")
        generate_deploy_config("/model", "vllm", out, trust_remote_code=True)

        with open(out) as f:
            content = f.read()
        assert "true" in content.lower()

    def test_json_roundtrip_for_hf_endpoints(self, tmp_path):
        from forgelm.deploy import HFEndpointsOptions

        out = str(tmp_path / "ep.json")
        generate_deploy_config(
            "./model",
            "hf-endpoints",
            out,
            hf_endpoints=HFEndpointsOptions(instance_type="nvidia-a100", region="eu-west-3"),
        )
        with open(out) as f:
            parsed = json.load(f)
        assert parsed["compute"]["instanceType"] == "nvidia-a100"
        assert parsed["cloud"]["region"] == "eu-west-3"


# ---------------------------------------------------------------------------
# error_kind classification + CLI exit-code routing (F-P7-OPUS-22 / M5)
# ---------------------------------------------------------------------------


class _DeployArgs:
    """Minimal stand-in for the argparse Namespace ``_run_deploy_cmd`` reads."""

    def __init__(self, model_path, target, output):
        self.model_path = model_path
        self.target = target
        self.output = output
        self.system = None
        self.max_length = 2048
        self.trust_remote_code = False
        self.gpu_memory_utilization = 0.9
        self.port = 8080
        self.vendor = "aws"


class TestDeployErrorKind:
    """A non-directory model_path / unsupported target is a caller-input error."""

    def test_non_directory_model_path_tagged_input(self, tmp_path):
        result = generate_deploy_config(str(tmp_path / "no_such_dir"), "ollama", str(tmp_path / "Modelfile"))
        assert result.success is False
        assert result.error_kind == "input"

    def test_unsupported_target_tagged_input(self):
        result = generate_deploy_config("/model", "nonexistent_runtime")
        assert result.success is False
        assert result.error_kind == "input"

    def test_success_leaves_error_kind_none(self, tmp_path):
        model_dir = tmp_path / "model"
        model_dir.mkdir()
        result = generate_deploy_config(str(model_dir), "ollama", str(tmp_path / "Modelfile"))
        assert result.success is True
        assert result.error_kind is None


class TestDeployDispatcherExitCodes:
    """``_run_deploy_cmd`` routes input errors to exit 1, runtime errors to exit 2."""

    def test_deploy_non_directory_model_path_exits_config_error(self, tmp_path, capsys):
        import pytest

        from forgelm.cli._exit_codes import EXIT_CONFIG_ERROR
        from forgelm.cli.subcommands._deploy import _run_deploy_cmd

        args = _DeployArgs(str(tmp_path / "missing"), "ollama", str(tmp_path / "Modelfile"))
        with pytest.raises(SystemExit) as exc_info:
            _run_deploy_cmd(args, "json")
        assert exc_info.value.code == EXIT_CONFIG_ERROR
        envelope = json.loads(capsys.readouterr().out)
        assert envelope["success"] is False
        assert "not a directory" in envelope["error"]

    def test_deploy_runtime_failure_exits_training_error(self, tmp_path, monkeypatch):
        import pytest

        import forgelm.deploy as deploy_mod
        from forgelm.cli._exit_codes import EXIT_TRAINING_ERROR
        from forgelm.cli.subcommands._deploy import _run_deploy_cmd

        # Force a runtime failure inside generate_deploy_config so the
        # dispatcher sees error_kind="runtime" and maps it to exit 2 — the
        # complement of the input-error path above. _run_deploy_cmd imports
        # the symbol from forgelm.deploy at call time, so patch it there.
        def _boom(**_kwargs):
            return DeployResult(success=False, target="ollama", error="disk full", error_kind="runtime")

        monkeypatch.setattr(deploy_mod, "generate_deploy_config", _boom)
        args = _DeployArgs(str(tmp_path), "ollama", str(tmp_path / "Modelfile"))
        with pytest.raises(SystemExit) as exc_info:
            _run_deploy_cmd(args, "text")
        assert exc_info.value.code == EXIT_TRAINING_ERROR
