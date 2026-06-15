"""Unit tests for forgelm.export module."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from unittest.mock import MagicMock, patch

from forgelm.export import (
    SUPPORTED_FORMATS,
    SUPPORTED_QUANTS,
    ExportResult,
    _sha256_file,
    _update_integrity_manifest,
    export_model,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_gguf_in_formats(self):
        assert "gguf" in SUPPORTED_FORMATS

    def test_quants_present(self):
        for q in ("q2_k", "q3_k_m", "q4_k_m", "q5_k_m", "q8_0", "f16"):
            assert q in SUPPORTED_QUANTS

    def test_export_result_dataclass(self):
        r = ExportResult(success=True, output_path="/out.gguf", quant="q4_k_m")
        assert r.success is True
        assert r.sha256 is None
        assert r.error is None


# ---------------------------------------------------------------------------
# _sha256_file
# ---------------------------------------------------------------------------


class TestSha256File:
    def test_correct_digest(self, tmp_path):
        p = tmp_path / "test.bin"
        p.write_bytes(b"hello world")
        expected = hashlib.sha256(b"hello world").hexdigest()
        assert _sha256_file(str(p)) == expected

    def test_empty_file(self, tmp_path):
        p = tmp_path / "empty.bin"
        p.write_bytes(b"")
        expected = hashlib.sha256(b"").hexdigest()
        assert _sha256_file(str(p)) == expected

    def test_large_file_consistent(self, tmp_path):
        data = b"X" * (200 * 1024)  # 200 KB — forces chunked read
        p = tmp_path / "large.bin"
        p.write_bytes(data)
        expected = hashlib.sha256(data).hexdigest()
        assert _sha256_file(str(p)) == expected


# ---------------------------------------------------------------------------
# _update_integrity_manifest
# ---------------------------------------------------------------------------


class TestUpdateIntegrityManifest:
    def test_updates_existing_manifest(self, tmp_path):
        integrity_path = tmp_path / "model_integrity.json"
        integrity_path.write_text(json.dumps({"verified_at": "2026-01-01", "artifacts": []}))

        result = ExportResult(
            success=True,
            output_path=str(tmp_path / "model.gguf"),
            format="gguf",
            quant="q4_k_m",
            sha256="abc123",
            size_bytes=1024,
        )
        _update_integrity_manifest(str(tmp_path), result)

        with open(str(integrity_path)) as f:
            data = json.load(f)

        assert len(data["exported_artifacts"]) == 1
        artifact = data["exported_artifacts"][0]
        assert artifact["sha256"] == "abc123"
        assert artifact["quant"] == "q4_k_m"

    def test_no_error_when_manifest_missing(self, tmp_path):
        result = ExportResult(success=True, output_path=str(tmp_path / "model.gguf"), sha256="abc")
        # Should not raise even though model_integrity.json doesn't exist
        _update_integrity_manifest(str(tmp_path), result)

    def test_appends_multiple_artifacts(self, tmp_path):
        integrity_path = tmp_path / "model_integrity.json"
        integrity_path.write_text(json.dumps({"exported_artifacts": [{"sha256": "first"}]}))

        result = ExportResult(success=True, output_path="/m.gguf", sha256="second", quant="q8_0")
        _update_integrity_manifest(str(tmp_path), result)

        with open(str(integrity_path)) as f:
            data = json.load(f)
        assert len(data["exported_artifacts"]) == 2


# ---------------------------------------------------------------------------
# export_model — mocked converter
# ---------------------------------------------------------------------------


class TestExportModel:
    def _mock_successful_conversion(self, tmp_path, content=b"mock gguf data"):
        """Return a mock that simulates successful subprocess conversion."""
        output_path = str(tmp_path / "model.gguf")

        def fake_run(cmd, **kwargs):
            # Write to whatever path the converter was invoked with so K-quant
            # rerouting (output.gguf → output.f16.gguf) is handled transparently.
            actual = cmd[cmd.index("--outfile") + 1] if "--outfile" in cmd else output_path
            with open(actual, "wb") as f:
                f.write(content)
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            result.stdout = "Conversion successful"
            return result

        return output_path, fake_run

    def test_unsupported_format_returns_failure(self, tmp_path):
        result = export_model(str(tmp_path / "model"), str(tmp_path / "out.xyz"), output_format="xyz")
        assert result.success is False
        assert "xyz" in result.error
        assert result.error_kind == "config"

    def test_unsupported_quant_returns_failure(self, tmp_path):
        result = export_model(str(tmp_path / "model"), str(tmp_path / "out.gguf"), quant="q99_k")
        assert result.success is False
        assert "q99_k" in result.error
        assert result.error_kind == "config"

    def test_missing_llama_cpp_returns_failure(self, tmp_path):
        with patch.dict(sys.modules, {"llama_cpp": None}):
            result = export_model(str(tmp_path / "model"), str(tmp_path / "out.gguf"))
        assert result.success is False
        assert "forgelm[export]" in result.error
        assert result.error_kind == "runtime"

    def test_successful_export_returns_sha256(self, tmp_path):
        output_path, fake_run = self._mock_successful_conversion(tmp_path)
        converter_path = str(tmp_path / "convert_hf_to_gguf.py")
        open(converter_path, "w").close()  # empty placeholder

        llama_cpp_stub = MagicMock()
        llama_cpp_stub.__file__ = str(tmp_path / "llama_cpp" / "__init__.py")
        # Put converter next to the package
        os.makedirs(str(tmp_path / "llama_cpp"), exist_ok=True)
        converter_in_pkg = str(tmp_path / "llama_cpp" / "convert_hf_to_gguf.py")
        open(converter_in_pkg, "w").close()

        with patch.dict(sys.modules, {"llama_cpp": llama_cpp_stub}):
            with patch("subprocess.run", side_effect=fake_run):
                result = export_model(
                    str(tmp_path / "model"),
                    output_path,
                    quant="q8_0",
                    update_integrity=False,
                )

        assert result.success is True
        assert result.sha256 is not None
        assert len(result.sha256) == 64  # SHA-256 hex digest
        assert result.size_bytes > 0
        assert result.quant == "q8_0"

    def test_kquant_produces_f16_intermediate(self, tmp_path):
        """K-quant requests must yield result.quant='f16' and a .f16.gguf path
        so the integrity manifest never claims a SHA-256 it can't back."""
        output_path, fake_run = self._mock_successful_conversion(tmp_path)
        llama_cpp_stub = MagicMock()
        os.makedirs(str(tmp_path / "llama_cpp"), exist_ok=True)
        llama_cpp_stub.__file__ = str(tmp_path / "llama_cpp" / "__init__.py")
        open(str(tmp_path / "llama_cpp" / "convert_hf_to_gguf.py"), "w").close()

        with patch.dict(sys.modules, {"llama_cpp": llama_cpp_stub}):
            with patch("subprocess.run", side_effect=fake_run):
                result = export_model(
                    str(tmp_path / "model"),
                    output_path,
                    quant="q4_k_m",
                    update_integrity=False,
                )

        assert result.success is True
        # Actual file written must reflect f16, not q4_k_m
        assert result.quant == "f16"
        assert result.output_path.endswith(".f16.gguf")
        assert os.path.isfile(result.output_path)

    def test_converter_exit_nonzero_returns_failure(self, tmp_path):
        llama_cpp_stub = MagicMock()
        os.makedirs(str(tmp_path / "llama_cpp"), exist_ok=True)
        llama_cpp_stub.__file__ = str(tmp_path / "llama_cpp" / "__init__.py")
        open(str(tmp_path / "llama_cpp" / "convert_hf_to_gguf.py"), "w").close()

        def failing_run(cmd, **kwargs):
            m = MagicMock()
            m.returncode = 1
            m.stderr = "CUDA error"
            m.stdout = ""
            return m

        with patch.dict(sys.modules, {"llama_cpp": llama_cpp_stub}):
            with patch("subprocess.run", side_effect=failing_run):
                result = export_model(str(tmp_path / "model"), str(tmp_path / "out.gguf"))

        assert result.success is False
        assert "1" in result.error  # exit code in message

    def test_converter_not_found_in_package(self, tmp_path):
        llama_cpp_stub = MagicMock()
        os.makedirs(str(tmp_path / "llama_cpp"), exist_ok=True)
        llama_cpp_stub.__file__ = str(tmp_path / "llama_cpp" / "__init__.py")
        # Do NOT create convert_hf_to_gguf.py — simulate missing script

        with patch.dict(sys.modules, {"llama_cpp": llama_cpp_stub}):
            result = export_model(str(tmp_path / "model"), str(tmp_path / "out.gguf"))

        assert result.success is False
        assert "not found" in result.error.lower() or "0.2.90" in result.error

    def test_integrity_manifest_updated_on_success(self, tmp_path):
        output_path = str(tmp_path / "model.gguf")
        model_dir = str(tmp_path / "model")
        os.makedirs(model_dir)

        # Create model_integrity.json
        integrity_path = os.path.join(model_dir, "model_integrity.json")
        with open(integrity_path, "w") as f:
            json.dump({"artifacts": []}, f)

        llama_cpp_stub = MagicMock()
        pkg_dir = str(tmp_path / "llama_cpp")
        os.makedirs(pkg_dir, exist_ok=True)
        llama_cpp_stub.__file__ = os.path.join(pkg_dir, "__init__.py")
        open(os.path.join(pkg_dir, "convert_hf_to_gguf.py"), "w").close()

        def fake_run(cmd, **kwargs):
            actual = cmd[cmd.index("--outfile") + 1] if "--outfile" in cmd else output_path
            with open(actual, "wb") as f:
                f.write(b"gguf data")
            m = MagicMock()
            m.returncode = 0
            m.stderr = ""
            return m

        with patch.dict(sys.modules, {"llama_cpp": llama_cpp_stub}):
            with patch("subprocess.run", side_effect=fake_run):
                result = export_model(model_dir, output_path, quant="q8_0", update_integrity=True)

        assert result.success is True
        with open(integrity_path) as f:
            data = json.load(f)
        assert len(data["exported_artifacts"]) == 1
        assert data["exported_artifacts"][0]["sha256"] == result.sha256

    def test_malformed_gguf_converter_env_var_is_config_error(self, tmp_path):
        """FORGELM_GGUF_CONVERTER pointing to a non-.py path must produce error_kind='config'."""
        env = {"FORGELM_GGUF_CONVERTER": "/tmp/convert.sh"}
        with patch.dict(os.environ, env, clear=False):
            result = export_model(str(tmp_path / "model"), str(tmp_path / "out.gguf"))
        assert result.success is False
        assert result.error_kind == "config"
        assert ".py" in result.error

    def test_missing_converter_script_in_package_is_runtime_error(self, tmp_path):
        """FileNotFoundError from _find_converter_script (missing .py) must produce error_kind='runtime'."""
        llama_cpp_stub = MagicMock()
        pkg_dir = str(tmp_path / "llama_cpp")
        os.makedirs(pkg_dir, exist_ok=True)
        llama_cpp_stub.__file__ = os.path.join(pkg_dir, "__init__.py")
        # Do NOT create convert_hf_to_gguf.py so _find_converter_script raises FileNotFoundError.

        with patch.dict(sys.modules, {"llama_cpp": llama_cpp_stub}):
            result = export_model(str(tmp_path / "model"), str(tmp_path / "out.gguf"))

        assert result.success is False
        assert result.error_kind == "runtime"

    def test_all_supported_quants_accepted(self, tmp_path):
        """Every quant in SUPPORTED_QUANTS must pass format/quant validation."""
        llama_cpp_stub = MagicMock()
        pkg_dir = str(tmp_path / "llama_cpp")
        os.makedirs(pkg_dir, exist_ok=True)
        llama_cpp_stub.__file__ = os.path.join(pkg_dir, "__init__.py")

        output_gguf = str(tmp_path / "model.gguf")

        def fake_run(cmd, **kwargs):
            actual = cmd[cmd.index("--outfile") + 1] if "--outfile" in cmd else output_gguf
            with open(actual, "wb") as f:
                f.write(b"data")
            m = MagicMock()
            m.returncode = 0
            m.stderr = ""
            return m

        open(os.path.join(pkg_dir, "convert_hf_to_gguf.py"), "w").close()

        for quant in SUPPORTED_QUANTS:
            with patch.dict(sys.modules, {"llama_cpp": llama_cpp_stub}):
                with patch("subprocess.run", side_effect=fake_run):
                    result = export_model(str(tmp_path), output_gguf, quant=quant, update_integrity=False)
            # Quant validation must not reject any SUPPORTED_QUANTS entry
            assert "Unsupported quantisation" not in (result.error or ""), (
                f"Export failed quant validation for {quant}: {result.error}"
            )
