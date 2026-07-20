"""Phase 34: ``forgelm doctor`` env-check subcommand.

Heavy on lightweight unit tests of individual probes (so a CI runner
without torch / GPU / network access can still exercise the doctor
surface), with one CLI subprocess smoke at the bottom.
"""

from __future__ import annotations

import json
import os
import sys
from collections import namedtuple
from unittest.mock import MagicMock, patch

import pytest

# ``sys.version_info`` is a named tuple (`.major`/`.minor`/`.micro`/...);
# patching with a plain tuple breaks the attribute access in the probe.
# Build a structurally-equivalent fake we can pass to ``patch``.
_FakeVersionInfo = namedtuple("_FakeVersionInfo", ["major", "minor", "micro", "releaselevel", "serial"])


def _make_version(major: int, minor: int, micro: int = 0) -> _FakeVersionInfo:
    return _FakeVersionInfo(major, minor, micro, "final", 0)


# ``shutil.disk_usage`` returns a ``_ntuple_diskusage`` named tuple
# (`.total` / `.used` / `.free`).  Mirror it for the disk-space tests.
_FakeDiskUsage = namedtuple("_FakeDiskUsage", ["total", "used", "free"])

# ---------------------------------------------------------------------------
# Individual probes
# ---------------------------------------------------------------------------


class TestForgelmInstallCheck:
    """``forgelm.install`` is the FIRST row in the check plan (see
    ``_build_check_plan``) — every other line of a bug report is
    ambiguous until the reader knows which copy of ForgeLM ran. It is
    always ``pass``; the probe *records* the install location rather
    than grading it. Because it now runs before every other probe, a
    crash here would poison the entire report, so "never raises" is
    tested explicitly below rather than assumed.
    """

    def test_shape_matches_checks_contract(self) -> None:
        """Pins the documented ``checks[]`` shape: name/status/detail/extras,
        with the three extras keys the json-output.md contract promises."""
        from forgelm.cli.subcommands._doctor import _check_forgelm_install

        result = _check_forgelm_install()
        assert result.name == "forgelm.install"
        assert result.status == "pass"
        assert isinstance(result.detail, str) and result.detail
        assert set(result.extras) == {"version", "location", "in_site_packages"}
        assert isinstance(result.extras["in_site_packages"], bool)

    def test_in_site_packages_true(self, monkeypatch) -> None:
        """A location under sysconfig's purelib/platlib must resolve True —
        the normal operator ``pip install forgelm`` case."""
        import sysconfig

        import forgelm
        from forgelm.cli.subcommands._doctor import _check_forgelm_install

        fake_purelib = "/opt/venv/lib/python3.11/site-packages"
        monkeypatch.setattr(forgelm, "__file__", fake_purelib + "/forgelm/__init__.py")
        monkeypatch.setattr(sysconfig, "get_paths", lambda: {"purelib": fake_purelib, "platlib": fake_purelib})
        result = _check_forgelm_install()
        assert result.extras["in_site_packages"] is True
        assert "inside site-packages" in result.detail
        assert result.status == "pass"

    def test_in_site_packages_false(self, monkeypatch) -> None:
        """A source-tree / editable-install location must resolve False —
        this distinction is the entire point of the probe (it exists
        because the contributor gauntlet was found validating a stale
        non-editable install instead of the working tree)."""
        import sysconfig

        import forgelm
        from forgelm.cli.subcommands._doctor import _check_forgelm_install

        monkeypatch.setattr(forgelm, "__file__", "/work/checkout/forgelm/__init__.py")
        monkeypatch.setattr(
            sysconfig,
            "get_paths",
            lambda: {
                "purelib": "/opt/venv/lib/python3.11/site-packages",
                "platlib": "/opt/venv/lib/python3.11/site-packages",
            },
        )
        result = _check_forgelm_install()
        assert result.extras["in_site_packages"] is False
        assert "outside site-packages" in result.detail
        assert result.status == "pass"

    def test_platlib_only_still_resolves_true(self, monkeypatch) -> None:
        """A platlib-only match (compiled-extension layout diverges from
        purelib on some platforms) must still count as in_site_packages —
        the probe checks both, not just purelib."""
        import sysconfig

        import forgelm
        from forgelm.cli.subcommands._doctor import _check_forgelm_install

        platlib = "/opt/venv/lib/python3.11/site-packages-platform"
        monkeypatch.setattr(forgelm, "__file__", platlib + "/forgelm/__init__.py")
        monkeypatch.setattr(sysconfig, "get_paths", lambda: {"purelib": "/somewhere/else", "platlib": platlib})
        result = _check_forgelm_install()
        assert result.extras["in_site_packages"] is True

    def test_version_and_location_reflect_imported_module_not_a_guess(self, monkeypatch, tmp_path) -> None:
        """version/location must be read off the already-imported ``forgelm``
        module object, never re-derived via ``importlib.metadata`` — a
        stray ``forgelm.egg-info`` in cwd would shadow the real dist-info
        and answer for a distribution that is not the one running.
        Proven by poisoning ``importlib.metadata.version`` with a wrong
        answer and confirming it never leaks into the result.

        ``location`` is pinned to a real decoy directory under ``tmp_path``
        rather than to a hardcoded ``"/work/checkout/forgelm"`` literal. The
        literal was a POSIX assumption: the probe calls ``os.path.realpath``,
        which on Windows absolutises a rootless path against the current
        drive, so the answer came back ``D:\\work\\checkout\\forgelm`` and the
        assertion failed on a probe that was behaving correctly. A real
        directory is platform-native on every runner and — crucially — is a
        directory the *actual* forgelm package does not live in, so the
        assertion still proves ``location`` is read off ``forgelm.__file__``
        rather than re-derived from the running package."""
        import importlib.metadata

        import forgelm
        from forgelm.cli.subcommands._doctor import _check_forgelm_install

        decoy_pkg = tmp_path / "work" / "checkout" / "forgelm"
        decoy_pkg.mkdir(parents=True)
        real_pkg_dir = os.path.dirname(os.path.realpath(forgelm.__file__))

        sentinel_version = "999.888.777-devpin"
        monkeypatch.setattr(forgelm, "__version__", sentinel_version)
        monkeypatch.setattr(forgelm, "__file__", str(decoy_pkg / "__init__.py"))
        monkeypatch.setattr(importlib.metadata, "version", lambda *_a, **_k: "0.0.1-WRONG-DISTRIBUTION")

        result = _check_forgelm_install()
        assert result.extras["version"] == sentinel_version
        assert sentinel_version in result.detail
        assert "0.0.1-WRONG-DISTRIBUTION" not in result.detail
        # realpath on both sides: macOS resolves /var -> /private/var, so the
        # fixture path and the probe's answer must be compared post-resolution.
        # This is normalisation, not relaxation — it is still an exact
        # directory-identity check against a decoy the real package is not in.
        assert result.extras["location"] == os.path.realpath(str(decoy_pkg))
        assert result.extras["location"] != real_pkg_dir, (
            "the decoy must not coincide with the real package directory, or the assertion proves nothing"
        )

    def test_missing_version_attr_falls_back_to_unknown(self, monkeypatch) -> None:
        import forgelm
        from forgelm.cli.subcommands._doctor import _check_forgelm_install

        monkeypatch.delattr(forgelm, "__version__", raising=False)
        result = _check_forgelm_install()
        assert result.extras["version"] == "unknown"
        assert result.status == "pass"

    def test_namespace_package_no_file_does_not_raise(self, monkeypatch) -> None:
        """A namespace package has no single ``__file__``; the probe must
        record that rather than raising from ``os.path.dirname(None)``."""
        import forgelm
        from forgelm.cli.subcommands._doctor import _check_forgelm_install

        monkeypatch.setattr(forgelm, "__file__", None)
        result = _check_forgelm_install()
        assert result.status == "pass"
        assert result.extras["location"] is None
        assert result.extras["in_site_packages"] is None

    def test_probe_never_raises_when_sysconfig_paths_lack_lib_keys(self, monkeypatch) -> None:
        """Regression pin: ``sysconfig.get_paths()`` returning a dict with
        neither ``purelib`` nor ``platlib`` (exotic build layouts) must
        not raise — ``in_site_packages`` gracefully resolves to False."""
        import sysconfig

        from forgelm.cli.subcommands._doctor import _check_forgelm_install

        monkeypatch.setattr(sysconfig, "get_paths", lambda: {})
        result = _check_forgelm_install()  # must not raise
        assert result.status == "pass"
        assert result.extras["in_site_packages"] is False

    def test_wired_first_in_real_run_all_checks_and_does_not_crash(self) -> None:
        """End-to-end, unmocked: the real probe runs first in the real
        plan and never surfaces ``extras.crashed`` — a crash here would
        poison the entire doctor report because it is the very first row."""
        from forgelm.cli.subcommands._doctor import _run_all_checks

        results = _run_all_checks(offline=True)
        assert results, "doctor plan must not be empty"
        first = results[0]
        assert first.name == "forgelm.install"
        assert first.extras.get("crashed") is not True
        assert first.status == "pass"


class TestPythonVersionCheck:
    def test_python_310_warns(self) -> None:
        from forgelm.cli.subcommands._doctor import _check_python_version

        with patch("sys.version_info", _make_version(3, 10, 0)):
            result = _check_python_version()
        assert result.status == "warn"
        assert "3.11" in result.detail  # recommendation appears

    def test_python_311_passes(self) -> None:
        from forgelm.cli.subcommands._doctor import _check_python_version

        with patch("sys.version_info", _make_version(3, 11, 5)):
            result = _check_python_version()
        assert result.status == "pass"
        assert "3.11.5" in result.detail

    def test_python_312_passes(self) -> None:
        from forgelm.cli.subcommands._doctor import _check_python_version

        with patch("sys.version_info", _make_version(3, 12, 1)):
            result = _check_python_version()
        assert result.status == "pass"

    def test_python_39_fails(self) -> None:
        from forgelm.cli.subcommands._doctor import _check_python_version

        with patch("sys.version_info", _make_version(3, 9, 7)):
            result = _check_python_version()
        assert result.status == "fail"
        assert "3.10" in result.detail
        assert "below" in result.detail.lower()


class TestTorchCudaCheck:
    def test_no_torch_fails(self) -> None:
        """When torch is not importable doctor must surface a clear fail
        (not crash)."""
        import builtins

        from forgelm.cli.subcommands import _doctor

        original_import = builtins.__import__

        def _block_torch(name, *args, **kwargs):
            if name == "torch":
                raise ImportError("No module named 'torch'")
            return original_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", _block_torch):
            result = _doctor._check_torch_cuda()
        assert result.status == "fail"
        assert "torch" in result.detail.lower()

    def test_torch_cpu_only_warns(self) -> None:
        """CPU-only torch is supported but warned (training will be slow)."""
        from forgelm.cli.subcommands._doctor import _check_torch_cuda

        fake_torch = MagicMock()
        fake_torch.__version__ = "2.5.0"
        fake_torch.cuda.is_available.return_value = False
        fake_torch.version.cuda = None
        with patch.dict("sys.modules", {"torch": fake_torch}):
            result = _check_torch_cuda()
        assert result.status == "warn"
        assert "CPU-only" in result.detail or "cpu-only" in result.detail.lower()

    def test_torch_cuda_passes(self) -> None:
        from forgelm.cli.subcommands._doctor import _check_torch_cuda

        fake_torch = MagicMock()
        fake_torch.__version__ = "2.5.0"
        fake_torch.cuda.is_available.return_value = True
        fake_torch.version.cuda = "12.4"
        with patch.dict("sys.modules", {"torch": fake_torch}):
            result = _check_torch_cuda()
        assert result.status == "pass"
        assert "12.4" in result.detail


class TestNumpyTorchAbiCheck:
    """Catches the torch < 2.3 + NumPy >= 2 binary-ABI mismatch.

    The bug surfaces as the ``_ARRAY_API not found`` UserWarning emitted
    from torch's C++ tensor-numpy bridge.  Intel Mac (x86_64) hosts are
    the canonical victim — PyTorch Foundation no longer publishes
    torch >= 2.3 wheels for that platform, so pip caps at torch 2.2.x
    and any modern numpy on the box silently degrades the bridge.
    """

    def test_torch_22_with_numpy_2_fails(self) -> None:
        from forgelm.cli.subcommands._doctor import _check_numpy_torch_abi

        fake_torch = MagicMock()
        fake_torch.__version__ = "2.2.2"
        fake_numpy = MagicMock()
        fake_numpy.__version__ = "2.0.1"
        with patch.dict("sys.modules", {"torch": fake_torch, "numpy": fake_numpy}):
            result = _check_numpy_torch_abi()
        assert result.status == "fail"
        assert "2.2.2" in result.detail
        assert "2.0.1" in result.detail
        assert "numpy<2" in result.detail  # remediation hint
        assert result.extras["torch_version"] == "2.2.2"
        assert result.extras["numpy_version"] == "2.0.1"

    def test_torch_23_with_numpy_2_passes(self) -> None:
        from forgelm.cli.subcommands._doctor import _check_numpy_torch_abi

        fake_torch = MagicMock()
        fake_torch.__version__ = "2.3.0"
        fake_numpy = MagicMock()
        fake_numpy.__version__ = "2.0.1"
        with patch.dict("sys.modules", {"torch": fake_torch, "numpy": fake_numpy}):
            result = _check_numpy_torch_abi()
        assert result.status == "pass"
        assert "ABI-compatible" in result.detail

    def test_torch_22_with_numpy_1_passes(self) -> None:
        from forgelm.cli.subcommands._doctor import _check_numpy_torch_abi

        fake_torch = MagicMock()
        fake_torch.__version__ = "2.2.2"
        fake_numpy = MagicMock()
        fake_numpy.__version__ = "1.26.4"
        with patch.dict("sys.modules", {"torch": fake_torch, "numpy": fake_numpy}):
            result = _check_numpy_torch_abi()
        assert result.status == "pass"

    def test_no_numpy_skips_gracefully(self, monkeypatch) -> None:
        """numpy is an optional fast-path for the simhash backend; its
        absence is not a doctor failure.

        Uses ``monkeypatch.delitem`` so the popped ``sys.modules['numpy']``
        is auto-restored on test teardown — a plain ``sys.modules.pop``
        leaves numpy un-cached, and a later test that re-imports torch
        will partially re-initialise it (no ``torch._C`` binding) and
        every downstream ``from trl import SFTConfig`` then fails with
        ``NameError: name '_C' is not defined``.
        """
        import builtins

        from forgelm.cli.subcommands import _doctor

        fake_torch = MagicMock()
        fake_torch.__version__ = "2.2.2"
        original_import = builtins.__import__

        def _block_numpy(name, *args, **kwargs):
            if name == "numpy":
                raise ImportError("No module named 'numpy'")
            return original_import(name, *args, **kwargs)

        # Both swaps are tracked by monkeypatch so the original modules
        # come back automatically; nothing leaks into the next test.
        monkeypatch.setitem(sys.modules, "torch", fake_torch)
        monkeypatch.delitem(sys.modules, "numpy", raising=False)
        monkeypatch.setattr(builtins, "__import__", _block_numpy)
        result = _doctor._check_numpy_torch_abi()
        assert result.status == "pass"
        assert result.extras.get("skipped") is True
        assert result.extras.get("reason") == "numpy_missing"

    def test_no_torch_skips_gracefully(self, monkeypatch) -> None:
        """torch.cuda probe already surfaces a missing torch as fail; the
        ABI probe must not double-report.

        ``monkeypatch.delitem`` keeps ``sys.modules['torch']`` restored
        on teardown — see ``test_no_numpy_skips_gracefully`` for the
        full pollution-cascade rationale (a stranded torch entry
        breaks every subsequent TRL-loading test in the suite).
        """
        import builtins

        from forgelm.cli.subcommands import _doctor

        original_import = builtins.__import__

        def _block_torch(name, *args, **kwargs):
            if name == "torch":
                raise ImportError("No module named 'torch'")
            return original_import(name, *args, **kwargs)

        monkeypatch.delitem(sys.modules, "torch", raising=False)
        monkeypatch.setattr(builtins, "__import__", _block_torch)
        result = _doctor._check_numpy_torch_abi()
        assert result.status == "pass"
        assert result.extras.get("skipped") is True
        assert result.extras.get("reason") == "torch_missing"

    def test_handles_prerelease_version_strings(self) -> None:
        """Tolerate prerelease ('2.2.0a0') and local-version ('2.2.0+cpu')
        suffixes — common on dev installs."""
        from forgelm.cli.subcommands._doctor import _check_numpy_torch_abi

        fake_torch = MagicMock()
        fake_torch.__version__ = "2.2.0+cpu"
        fake_numpy = MagicMock()
        fake_numpy.__version__ = "2.0.0rc1"
        with patch.dict("sys.modules", {"torch": fake_torch, "numpy": fake_numpy}):
            result = _check_numpy_torch_abi()
        # 2.2 + 2.0 still triggers the fail path despite the version suffixes.
        assert result.status == "fail"


class TestGpuInventoryCheck:
    def test_no_torch_fails(self) -> None:
        import builtins

        from forgelm.cli.subcommands import _doctor

        original_import = builtins.__import__

        def _block_torch(name, *args, **kwargs):
            if name == "torch":
                raise ImportError("nope")
            return original_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", _block_torch):
            result = _doctor._check_gpu_inventory()
        assert result.status == "fail"

    def test_no_cuda_warns(self) -> None:
        from forgelm.cli.subcommands._doctor import _check_gpu_inventory

        fake_torch = MagicMock()
        fake_torch.cuda.is_available.return_value = False
        with patch.dict("sys.modules", {"torch": fake_torch}):
            result = _check_gpu_inventory()
        assert result.status == "warn"
        assert result.extras["device_count"] == 0

    def test_two_gpus_pass_with_inventory(self) -> None:
        from forgelm.cli.subcommands._doctor import _check_gpu_inventory

        fake_torch = MagicMock()
        fake_torch.cuda.is_available.return_value = True
        fake_torch.cuda.device_count.return_value = 2
        # 24 GiB and 80 GiB devices.
        props_24 = MagicMock(name="A10", total_memory=24 * (1024**3))
        props_80 = MagicMock(name="A100", total_memory=80 * (1024**3))
        # MagicMock attribute trick: ``name`` is special, set explicitly.
        props_24.name = "NVIDIA A10"
        props_80.name = "NVIDIA A100"
        fake_torch.cuda.get_device_properties.side_effect = [props_24, props_80]
        with patch.dict("sys.modules", {"torch": fake_torch}):
            result = _check_gpu_inventory()
        assert result.status == "pass"
        assert result.extras["device_count"] == 2
        assert len(result.extras["devices"]) == 2
        assert result.extras["devices"][0]["vram_gib"] == pytest.approx(24.0)
        assert result.extras["devices"][1]["vram_gib"] == pytest.approx(80.0)


class TestOptionalExtraCheck:
    def test_present_module_passes(self) -> None:
        from forgelm.cli.subcommands._doctor import _check_optional_extra

        # ``json`` is always installed in stdlib — use it as the
        # "definitely present" probe target.
        result = _check_optional_extra("fakextra", "json", "stdlib JSON")
        assert result.status == "pass"
        assert "json" in result.detail
        assert result.extras["installed"] is True

    def test_missing_module_warns_with_install_hint(self) -> None:
        from forgelm.cli.subcommands._doctor import _check_optional_extra

        result = _check_optional_extra("ghost", "definitely_not_installed_xyz123", "fake purpose")
        assert result.status == "warn"
        assert "pip install 'forgelm[ghost]'" in result.detail
        assert result.extras["installed"] is False

    def test_present_module_detail_does_not_over_promise_importability(self) -> None:
        """F-P7-OPUS-35: find_spec only proves discoverability, not that
        ``import`` succeeds — the pass detail must not claim "Installed"."""
        from forgelm.cli.subcommands._doctor import _check_optional_extra

        result = _check_optional_extra("fakextra", "json", "stdlib JSON")
        assert result.status == "pass"
        assert "not import-tested" in result.detail.lower()

    def test_broken_extra_not_misreported_as_absent(self, monkeypatch) -> None:
        """F-P7-OPUS-35: a find_spec that raises (e.g. a __spec__=None shim)
        must propagate as a crash, NOT be silently downgraded to a
        'not installed' warn."""
        import importlib.util

        from forgelm.cli.subcommands._doctor import _check_optional_extra

        def _raise(_module):
            raise ModuleNotFoundError("broken parent")

        monkeypatch.setattr(importlib.util, "find_spec", _raise)
        with pytest.raises(ModuleNotFoundError):
            _check_optional_extra("broken", "broken_mod", "broken purpose")


class TestDoctorDocProbeList:
    def test_json_output_doc_lists_pypdf_normalise_probe(self) -> None:
        """F-P7-OPUS-34: every stable probe name the live plan always emits
        must appear in the json-output.md probe-name list (EN + TR)."""
        from pathlib import Path

        repo_root = Path(__file__).resolve().parent.parent
        for lang in ("en", "tr"):
            doc = repo_root / "docs" / "usermanuals" / lang / "reference" / "json-output.md"
            text = doc.read_text(encoding="utf-8")
            assert "pypdf_normalise.turkish" in text, f"{lang} json-output.md omits pypdf_normalise.turkish"

    def test_json_output_doc_lists_forgelm_install_probe(self) -> None:
        """The forgelm.install probe (now the FIRST row of every plan) must
        appear in the json-output.md probe-name list (EN + TR) — the doc
        promises "new probes append rather than rename" and this is the
        additive case that promise covers."""
        from pathlib import Path

        repo_root = Path(__file__).resolve().parent.parent
        for lang in ("en", "tr"):
            doc = repo_root / "docs" / "usermanuals" / lang / "reference" / "json-output.md"
            text = doc.read_text(encoding="utf-8")
            assert "forgelm.install" in text, f"{lang} json-output.md omits forgelm.install"


class TestHfHubReachableCheck:
    """Probe verifies the HF Hub is reachable.

    Wave 2a Round-2 (F-XPR-02-01): the probe was migrated from raw
    ``urllib.request.urlopen`` to :func:`forgelm._http.safe_get` so it
    inherits the project HTTP discipline (SSRF guard, scheme policy,
    timeout floor, secret-mask).  Tests now monkeypatch ``safe_get`` at
    its module location.
    """

    def test_unreachable_warns_not_fails(self) -> None:
        """A network outage must NOT flip the gate to fail; doctor exists
        precisely to surface that fact."""
        import requests as _requests

        from forgelm.cli.subcommands._doctor import _check_hf_hub_reachable

        with patch(
            "forgelm._http.safe_get",
            side_effect=_requests.ConnectionError("DNS lookup failed"),
        ):
            result = _check_hf_hub_reachable(timeout_seconds=5.0)
        assert result.status == "warn"
        assert result.extras["reachable"] is False

    def test_200_response_passes(self) -> None:
        from forgelm.cli.subcommands._doctor import _check_hf_hub_reachable

        fake_response = MagicMock()
        fake_response.status_code = 200
        with patch("forgelm._http.safe_get", return_value=fake_response):
            result = _check_hf_hub_reachable(timeout_seconds=5.0)
        assert result.status == "pass"
        assert result.extras["status_code"] == 200

    def test_http_discipline_rejection_fails(self) -> None:
        """Wave 2a Round-2 F-XPR-02-01: when the HTTP discipline rejects
        the URL (e.g. http:// without opt-in, private IP without opt-in),
        the probe should emit ``fail`` with an actionable detail —
        operator misconfigured something the policy blocks."""
        from forgelm._http import HttpSafetyError
        from forgelm.cli.subcommands._doctor import _check_hf_hub_reachable

        with patch(
            "forgelm._http.safe_get",
            side_effect=HttpSafetyError("Private/loopback/IMDS destination blocked: host=10.0.0.1"),
        ):
            result = _check_hf_hub_reachable(timeout_seconds=5.0)
        assert result.status == "fail"
        assert result.extras["reachable"] is False
        assert "Private" in result.extras["error"] or "blocked" in result.extras["error"]

    def test_hf_hub_probe_uses_safe_get_layer(self) -> None:
        """Wave 2a Round-2 F-XPR-02-01: regression-pin that the doctor
        probe routes through forgelm._http.safe_get rather than calling
        urllib / requests directly.  Catches a future refactor that
        reverts to undisciplined HTTP."""
        from forgelm.cli.subcommands._doctor import _check_hf_hub_reachable

        fake_response = MagicMock()
        fake_response.status_code = 200
        with patch("forgelm._http.safe_get", return_value=fake_response) as spy:
            _check_hf_hub_reachable(timeout_seconds=5.0)
        assert spy.call_count == 1
        call = spy.call_args
        # Method must be HEAD by default (no body download); UA header set.
        assert call.kwargs["method"] == "HEAD"
        assert "User-Agent" in call.kwargs["headers"]


class TestHfCacheOfflineCheck:
    """Wave 2a Round-1 review (gemini bot): HF cache resolution honours
    HF_HUB_CACHE > HF_HOME/hub > ~/.cache/huggingface/hub.  Tests must
    set up the *correct* cache dir layout (HF_HOME/hub subdirectory)
    or use HF_HUB_CACHE directly."""

    def test_missing_cache_warns(self, tmp_path, monkeypatch) -> None:
        from forgelm.cli.subcommands._doctor import _check_hf_cache_offline

        monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "nonexistent_cache"))
        monkeypatch.delenv("HF_HOME", raising=False)
        result = _check_hf_cache_offline()
        assert result.status == "warn"
        assert result.extras["exists"] is False

    def test_populated_cache_passes_via_hf_hub_cache(self, tmp_path, monkeypatch) -> None:
        from forgelm.cli.subcommands._doctor import _check_hf_cache_offline

        cache_dir = tmp_path / "hub_cache"
        cache_dir.mkdir()
        (cache_dir / "model_blob").write_bytes(b"x" * 1024)
        monkeypatch.setenv("HF_HUB_CACHE", str(cache_dir))
        monkeypatch.delenv("HF_HOME", raising=False)
        result = _check_hf_cache_offline()
        assert result.status == "pass"
        assert result.extras["file_count"] == 1
        assert result.extras["size_gib"] >= 0

    def test_populated_cache_passes_via_hf_home_hub_subdir(self, tmp_path, monkeypatch) -> None:
        """gemini bot fix: HF_HOME → ``HF_HOME/hub`` (sub-directory)."""
        from forgelm.cli.subcommands._doctor import _check_hf_cache_offline

        hf_home = tmp_path / "hf_home"
        hub_dir = hf_home / "hub"
        hub_dir.mkdir(parents=True)
        (hub_dir / "model_blob").write_bytes(b"x" * 1024)
        monkeypatch.delenv("HF_HUB_CACHE", raising=False)
        monkeypatch.setenv("HF_HOME", str(hf_home))
        result = _check_hf_cache_offline()
        assert result.status == "pass"
        assert "hub" in result.extras["cache_dir"]

    def test_empty_cache_dir_warns(self, tmp_path, monkeypatch) -> None:
        from forgelm.cli.subcommands._doctor import _check_hf_cache_offline

        cache_dir = tmp_path / "hf_cache_empty"
        cache_dir.mkdir()
        monkeypatch.setenv("HF_HUB_CACHE", str(cache_dir))
        monkeypatch.delenv("HF_HOME", raising=False)
        result = _check_hf_cache_offline()
        assert result.status == "warn"
        assert result.extras["file_count"] == 0

    def test_unreadable_files_surface_as_warn_not_pass(self, tmp_path, monkeypatch) -> None:
        """F-34-OSE: previously OSError on getsize was swallowed silently
        and the doctor reported a clean ``pass`` with a misleading total.
        After the fix, any unreadable file flips the verdict to ``warn``
        and surfaces ``unreadable_count`` so the operator sees the issue.
        """
        import os

        from forgelm.cli.subcommands import _doctor

        cache_dir = tmp_path / "hub_cache"
        cache_dir.mkdir()
        # One readable, one unreadable.
        (cache_dir / "readable_blob").write_bytes(b"x" * 256)
        (cache_dir / "unreadable_blob").write_bytes(b"y" * 256)
        monkeypatch.setenv("HF_HUB_CACHE", str(cache_dir))
        monkeypatch.delenv("HF_HOME", raising=False)

        original_getsize = os.path.getsize

        def _fake_getsize(path: str) -> int:
            if path.endswith("unreadable_blob"):
                raise OSError("simulated permission denied")
            return original_getsize(path)

        monkeypatch.setattr(os.path, "getsize", _fake_getsize)
        result = _doctor._check_hf_cache_offline()
        assert result.status == "warn", f"unreadable file in cache must downgrade verdict to warn, got {result.status}"
        assert result.extras["unreadable_count"] == 1
        assert result.extras["file_count"] == 1  # only the readable one counted
        assert "unreadable" in result.detail.lower(), (
            f"detail must surface unreadable count to the operator, got: {result.detail!r}"
        )


class TestHfEndpointResolution:
    """Wave 2a Round-1 (gemini bot): HF_ENDPOINT must be respected for
    self-hosted mirrors / enterprise installs."""

    def test_default_endpoint_when_unset(self, monkeypatch) -> None:
        from forgelm.cli.subcommands._doctor import _resolve_hf_endpoint

        monkeypatch.delenv("HF_ENDPOINT", raising=False)
        assert _resolve_hf_endpoint() == "https://huggingface.co"

    def test_env_var_override(self, monkeypatch) -> None:
        from forgelm.cli.subcommands._doctor import _resolve_hf_endpoint

        monkeypatch.setenv("HF_ENDPOINT", "https://internal-mirror.example/")
        assert _resolve_hf_endpoint() == "https://internal-mirror.example"


class TestOperatorIdentityAnonymousOptIn:
    """Wave 2a Round-1 (qodo bot): respect FORGELM_ALLOW_ANONYMOUS_OPERATOR
    like AuditLogger.__init__ does — no-username + opt-in => warn (not fail)."""

    def test_no_username_with_opt_in_warns(self, monkeypatch) -> None:
        from forgelm.cli.subcommands._doctor import _check_operator_identity

        monkeypatch.delenv("FORGELM_OPERATOR", raising=False)
        monkeypatch.setenv("FORGELM_ALLOW_ANONYMOUS_OPERATOR", "1")
        with patch("getpass.getuser", side_effect=OSError("no user")):
            result = _check_operator_identity()
        assert result.status == "warn"
        assert "anonymous" in result.detail.lower()
        assert result.extras["source"] == "anonymous_opt_in"

    def test_no_username_without_opt_in_fails(self, monkeypatch) -> None:
        from forgelm.cli.subcommands._doctor import _check_operator_identity

        monkeypatch.delenv("FORGELM_OPERATOR", raising=False)
        monkeypatch.delenv("FORGELM_ALLOW_ANONYMOUS_OPERATOR", raising=False)
        with patch("getpass.getuser", side_effect=OSError("no user")):
            result = _check_operator_identity()
        assert result.status == "fail"


class TestSecretEnvMasking:
    """Wave 2a Round-1 (F-27-05): secret env-var values must not echo
    into doctor output."""

    def test_mask_helper_redacts_secret_names(self) -> None:
        from forgelm.cli.subcommands._doctor import _mask_env_value_for_audit

        masked = _mask_env_value_for_audit("FORGELM_AUDIT_SECRET", "super-secret-key-32-chars-long-x")
        assert "super-secret" not in masked
        assert "<set" in masked

    def test_mask_helper_passes_through_non_secret_names(self) -> None:
        from forgelm.cli.subcommands._doctor import _mask_env_value_for_audit

        passthrough = _mask_env_value_for_audit("FORGELM_OPERATOR", "alice")
        assert passthrough == "alice"


class TestDiskSpaceCheck:
    def test_plenty_passes(self, tmp_path) -> None:
        from forgelm.cli.subcommands._doctor import _check_disk_space

        result = _check_disk_space(str(tmp_path))
        # On a CI runner free space typically > 50 GiB; ensure at least
        # one of the three valid statuses comes back.
        assert result.status in ("pass", "warn", "fail")
        assert result.extras["free_gib"] >= 0

    def test_low_disk_fails(self, tmp_path) -> None:
        from forgelm.cli.subcommands import _doctor

        # Build a fake disk_usage that reports 5 GiB free.
        fake_usage = _FakeDiskUsage(
            total=1000 * (1024**3),
            used=950 * (1024**3),
            free=5 * (1024**3),
        )
        with patch("shutil.disk_usage", return_value=fake_usage):
            result = _doctor._check_disk_space(str(tmp_path))
        assert result.status == "fail"

    def test_warn_threshold(self, tmp_path) -> None:
        from forgelm.cli.subcommands import _doctor

        # 30 GiB free → warn (between 10 and 50).
        fake_usage = _FakeDiskUsage(
            total=1000 * (1024**3),
            used=970 * (1024**3),
            free=30 * (1024**3),
        )
        with patch("shutil.disk_usage", return_value=fake_usage):
            result = _doctor._check_disk_space(str(tmp_path))
        assert result.status == "warn"


class TestOperatorIdentityCheck:
    def test_explicit_env_passes(self, monkeypatch) -> None:
        from forgelm.cli.subcommands._doctor import _check_operator_identity

        monkeypatch.setenv("FORGELM_OPERATOR", "ci-pipeline-prod")
        result = _check_operator_identity()
        assert result.status == "pass"
        assert "ci-pipeline-prod" in result.detail

    def test_missing_env_warns_with_fallback(self, monkeypatch) -> None:
        from forgelm.cli.subcommands._doctor import _check_operator_identity

        monkeypatch.delenv("FORGELM_OPERATOR", raising=False)
        result = _check_operator_identity()
        # On a normal dev workstation getpass.getuser() resolves so we
        # get warn (not fail).
        assert result.status in ("warn", "fail")
        assert "FORGELM_OPERATOR" in result.detail


# ---------------------------------------------------------------------------
# Renderers + exit-code mapping
# ---------------------------------------------------------------------------


def _make_results(*statuses: str):
    from forgelm.cli.subcommands._doctor import _CheckResult

    return [_CheckResult(name=f"test.{i}", status=s, detail=f"d{i}") for i, s in enumerate(statuses)]


class TestExitCodeMapping:
    def test_all_pass_returns_zero(self) -> None:
        from forgelm.cli.subcommands._doctor import _resolve_exit_code

        assert _resolve_exit_code(_make_results("pass", "pass", "pass")) == 0

    def test_warn_only_returns_zero(self) -> None:
        from forgelm.cli.subcommands._doctor import _resolve_exit_code

        # Warns are operator-actionable but do not flip the gate.
        assert _resolve_exit_code(_make_results("pass", "warn", "warn")) == 0

    def test_fail_returns_one(self) -> None:
        from forgelm.cli.subcommands._doctor import _resolve_exit_code

        assert _resolve_exit_code(_make_results("pass", "fail", "warn")) == 1

    def test_crashed_probe_returns_two(self) -> None:
        from forgelm.cli.subcommands._doctor import _CheckResult, _resolve_exit_code

        crashed = _CheckResult(
            name="crash.probe",
            status="fail",
            detail="boom",
            extras={"crashed": True, "error_class": "RuntimeError"},
        )
        assert _resolve_exit_code([crashed]) == 2


class TestRenderers:
    def test_text_renders_summary_line(self) -> None:
        from forgelm.cli.subcommands._doctor import _render_text

        results = _make_results("pass", "warn", "fail")
        out = _render_text(results)
        assert "1 pass" in out
        assert "1 warn" in out
        assert "1 fail" in out
        # Each check is rendered.
        assert "test.0" in out and "test.1" in out and "test.2" in out

    def test_json_envelope_shape(self) -> None:
        from forgelm.cli.subcommands._doctor import _render_json

        results = _make_results("pass", "fail")
        payload = json.loads(_render_json(results))
        assert payload["success"] is False  # has a fail
        assert payload["summary"] == {"pass": 1, "warn": 0, "fail": 1, "crashed": 0}
        assert len(payload["checks"]) == 2

    def test_json_success_true_when_only_passes_and_warns(self) -> None:
        from forgelm.cli.subcommands._doctor import _render_json

        results = _make_results("pass", "warn", "pass")
        payload = json.loads(_render_json(results))
        assert payload["success"] is True

    def test_text_output_is_pure_ascii(self) -> None:
        """F-34-ASCII: the docstring promises plain ASCII for redirected
        logs and non-UTF8 terminals.  Previously used ✓ / ✗ (Unicode)
        which would crash with UnicodeEncodeError on PYTHONIOENCODING=ascii.
        Pinning the contract: every byte of the rendered text must encode
        cleanly as ASCII.
        """
        from forgelm.cli.subcommands._doctor import _render_text

        results = _make_results("pass", "warn", "fail")
        out = _render_text(results)
        # If a Unicode glyph leaks back in, this raises UnicodeEncodeError
        # exactly the way an ASCII-locale terminal would.
        out.encode("ascii")  # must not raise
        # And the glyphs are the documented ASCII tokens.
        assert "[+ pass]" in out
        assert "[! warn]" in out
        assert "[x fail]" in out


# ---------------------------------------------------------------------------
# Crash isolation
# ---------------------------------------------------------------------------


class TestProbeCrashIsolation:
    def test_one_crashing_probe_does_not_abort_the_run(self, monkeypatch) -> None:
        """If one probe raises an unexpected exception, the rest must
        still execute and the failed probe must be converted to a fail
        result with a ``crashed`` marker."""
        from forgelm.cli.subcommands import _doctor

        def _boom() -> _doctor._CheckResult:
            raise RuntimeError("synthetic crash")

        # Sandwich pattern (Wave 2a Round-2 F-TEST-34-01): a [ok, crash]
        # pair would silently pass even if the dispatcher aborted on the
        # crash, because nothing comes after it.  Putting an `ok_after`
        # probe at the end is what actually proves "the crash did not
        # truncate the rest of the plan".
        def _fake_plan(*, offline: bool):
            return [
                ("ok_before", lambda: _doctor._CheckResult(name="ok_before", status="pass", detail="a")),
                ("middle.crash", _boom),
                ("ok_after", lambda: _doctor._CheckResult(name="ok_after", status="pass", detail="b")),
            ]

        monkeypatch.setattr(_doctor, "_build_check_plan", _fake_plan)
        results = _doctor._run_all_checks(offline=False)
        assert [r.name for r in results] == ["ok_before", "middle.crash", "ok_after"]
        ok_before = next(r for r in results if r.name == "ok_before")
        ok_after = next(r for r in results if r.name == "ok_after")
        crash_result = next(r for r in results if r.name == "middle.crash")
        assert ok_before.status == "pass"
        assert ok_after.status == "pass"
        assert crash_result.status == "fail"
        assert crash_result.extras.get("crashed") is True
        assert "RuntimeError" in crash_result.detail


class TestDoctorDocConsistency:
    """F-P7-OPUS-09 / -11 — doctor's runtime surface matches the docs."""

    def test_json_status_vocabulary_matches_docs(self, monkeypatch) -> None:
        # F-P7-OPUS-09: a crashed probe must surface as status "fail" with
        # extras.crashed (never a "crashed" status token), matching the
        # corrected json-output.md status enum {pass, warn, fail}.
        from forgelm.cli.subcommands import _doctor

        def _boom() -> _doctor._CheckResult:
            raise RuntimeError("synthetic crash")

        def _fake_plan(*, offline: bool):
            return [("middle.crash", _boom)]

        monkeypatch.setattr(_doctor, "_build_check_plan", _fake_plan)
        results = _doctor._run_all_checks(offline=False)
        assert all(r.status in {"pass", "warn", "fail"} for r in results)
        crash = next(r for r in results if r.name == "middle.crash")
        assert crash.status == "fail"
        assert crash.extras.get("crashed") is True

        # The locked contract page must not list "crashed" as one of the
        # checks[].status enum values.  Mentioning extras.crashed /
        # summary.crashed in the same row is fine (those are real keys) —
        # only the status-value enumeration must drop "crashed".
        from pathlib import Path

        repo_root = Path(__file__).resolve().parent.parent
        for lang in ("en", "tr"):
            doc = repo_root / "docs" / "usermanuals" / lang / "reference" / "json-output.md"
            text = doc.read_text(encoding="utf-8")
            status_line = next(line for line in text.splitlines() if "`checks[].status`" in line)
            # The status enum is the list of backtick-quoted single tokens
            # like `pass`, `warn`, `fail` — `crashed` must not appear as one.
            assert "`crashed`" not in status_line, f"{lang} json-output.md still lists `crashed` as a status value"

    def test_cli_doc_doctor_summary_names_only_real_probes(self) -> None:
        # F-P7-OPUS-11: cli.md's doctor summary previously claimed an
        # "audit-secret configuration" probe that does not exist.  Assert
        # the (corrected) summary references no probe absent from the plan.
        from pathlib import Path

        from forgelm.cli.subcommands._doctor import _build_check_plan

        plan_names = {name for name, _ in _build_check_plan(offline=False)}
        # The summary is prose, so we check the specific phantom phrase the
        # finding called out is gone (there is no `audit.secret` probe).
        assert not any(name.startswith("audit") or "secret" in name for name in plan_names)
        repo_root = Path(__file__).resolve().parent.parent
        for lang in ("en", "tr"):
            doc = repo_root / "docs" / "usermanuals" / lang / "reference" / "cli.md"
            text = doc.read_text(encoding="utf-8").lower()
            assert "audit-secret configuration" not in text
            assert "audit-secret yapılandırma" not in text


# ---------------------------------------------------------------------------
# Plan composition
# ---------------------------------------------------------------------------


class TestCheckPlan:
    def test_offline_uses_cache_probe_not_hub_probe(self) -> None:
        from forgelm.cli.subcommands._doctor import _build_check_plan

        plan_offline = _build_check_plan(offline=True)
        plan_online = _build_check_plan(offline=False)
        names_offline = [name for name, _ in plan_offline]
        names_online = [name for name, _ in plan_online]
        assert "hf_hub.offline_cache" in names_offline
        assert "hf_hub.reachable" not in names_offline
        assert "hf_hub.reachable" in names_online
        assert "hf_hub.offline_cache" not in names_online

    def test_extras_in_plan(self) -> None:
        """All optional extras advertised in pyproject.toml are probed."""
        from forgelm.cli.subcommands._doctor import _OPTIONAL_EXTRAS, _build_check_plan

        plan = _build_check_plan(offline=False)
        names = {name for name, _ in plan}
        for extra, _module, _purpose in _OPTIONAL_EXTRAS:
            assert f"extras.{extra}" in names

    def test_forgelm_install_is_first_in_both_modes(self) -> None:
        """``forgelm.install`` is deliberately first: every other line of a
        bug report is ambiguous until the reader knows which copy of
        ForgeLM ran. Pinned in both --offline and online plans."""
        from forgelm.cli.subcommands._doctor import _build_check_plan

        for offline in (True, False):
            plan = _build_check_plan(offline=offline)
            assert plan[0][0] == "forgelm.install", f"offline={offline}"


# ---------------------------------------------------------------------------
# Top-level dispatcher
# ---------------------------------------------------------------------------


class TestDispatcher:
    def test_text_output_prints_summary(self, capsys) -> None:
        from forgelm.cli.subcommands._doctor import _run_doctor_cmd

        args = MagicMock()
        args.offline = True  # avoids the network probe
        with pytest.raises(SystemExit):
            _run_doctor_cmd(args, output_format="text")
        out = capsys.readouterr().out
        assert "Summary:" in out
        assert "forgelm doctor" in out

    def test_json_output_emits_envelope(self, capsys) -> None:
        from forgelm.cli.subcommands._doctor import _run_doctor_cmd

        args = MagicMock()
        args.offline = True
        with pytest.raises(SystemExit):
            _run_doctor_cmd(args, output_format="json")
        payload = json.loads(capsys.readouterr().out)
        assert "success" in payload
        assert "checks" in payload
        assert "summary" in payload
        assert all(set(c) >= {"name", "status", "detail", "extras"} for c in payload["checks"])

    def test_dispatcher_exits_with_resolved_code(self, capsys) -> None:
        from forgelm.cli.subcommands import _doctor

        # Force every probe to pass so exit code is 0.
        def _all_pass_plan(*, offline: bool):
            return [("only.probe", lambda: _doctor._CheckResult(name="only.probe", status="pass", detail="ok"))]

        with patch.object(_doctor, "_build_check_plan", _all_pass_plan):
            args = MagicMock()
            args.offline = False
            with pytest.raises(SystemExit) as ei:
                _doctor._run_doctor_cmd(args, output_format="text")
        assert ei.value.code == 0


# ---------------------------------------------------------------------------
# CLI subprocess smoke
# ---------------------------------------------------------------------------


class TestDoctorCLISmoke:
    def test_doctor_subcommand_registered(self) -> None:
        """`forgelm doctor --help` exits 0 and advertises --offline."""
        import subprocess

        result = subprocess.run(
            [sys.executable, "-m", "forgelm.cli", "doctor", "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert "--offline" in result.stdout
        # Common-flag inheritance: --output-format / --quiet / --log-level.
        assert "--output-format" in result.stdout

    def test_doctor_offline_runs_end_to_end(self) -> None:
        """`forgelm doctor --offline --output-format json` produces a valid
        JSON envelope and exits with one of the public exit codes.

        Smoke-level scope (Wave 2a Round-2 F-TEST-34-02): the runtime
        environment is unconstrained (CI runners may lack a populated HF
        cache, may set FORGELM_OPERATOR or not, etc.), so this test
        only pins (a) the JSON envelope shape and (b) that the exit
        code is one of the documented contract values.  Strict per-
        scenario assertions live in :class:`TestDispatcherStrictExit`
        below, where the plan is monkeypatched."""
        import subprocess

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "forgelm.cli",
                "doctor",
                "--offline",
                "--output-format",
                "json",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        # Public exit codes: 0 (all pass+warn) / 1 (some fail) / 2 (probe crashed).
        assert result.returncode in (0, 1, 2), result.stderr
        # The JSON envelope is on stdout regardless.
        payload = json.loads(result.stdout)
        # Contract: top-level keys are stable; consumers read these names.
        assert set(payload.keys()) == {"success", "checks", "summary"}
        assert isinstance(payload["success"], bool)
        assert isinstance(payload["checks"], list)
        assert set(payload["summary"].keys()) == {"pass", "warn", "fail", "crashed"}
        # success: bool aligns with exit code per docs/standards/error-handling.md
        if payload["success"]:
            assert result.returncode == 0
        else:
            assert result.returncode in (1, 2)


class TestDispatcherStrictExit:
    """Strict per-scenario exit code assertions (monkeypatched plan)."""

    def test_all_pass_exits_zero(self, capsys, monkeypatch) -> None:
        from forgelm.cli.subcommands import _doctor

        def _all_pass_plan(*, offline: bool):
            return [("a.pass", lambda: _doctor._CheckResult(name="a.pass", status="pass", detail="ok"))]

        monkeypatch.setattr(_doctor, "_build_check_plan", _all_pass_plan)
        args = MagicMock()
        args.offline = True
        with pytest.raises(SystemExit) as exc_info:
            _doctor._run_doctor_cmd(args, output_format="json")
        assert exc_info.value.code == 0

    def test_any_fail_exits_one(self, capsys, monkeypatch) -> None:
        from forgelm.cli.subcommands import _doctor

        def _has_fail_plan(*, offline: bool):
            return [
                ("a.pass", lambda: _doctor._CheckResult(name="a.pass", status="pass", detail="ok")),
                ("b.fail", lambda: _doctor._CheckResult(name="b.fail", status="fail", detail="bad")),
            ]

        monkeypatch.setattr(_doctor, "_build_check_plan", _has_fail_plan)
        args = MagicMock()
        args.offline = True
        with pytest.raises(SystemExit) as exc_info:
            _doctor._run_doctor_cmd(args, output_format="json")
        assert exc_info.value.code == 1

    def test_any_crashed_exits_two(self, capsys, monkeypatch) -> None:
        from forgelm.cli.subcommands import _doctor

        def _boom() -> _doctor._CheckResult:
            raise RuntimeError("boom")

        def _has_crash_plan(*, offline: bool):
            return [
                ("a.pass", lambda: _doctor._CheckResult(name="a.pass", status="pass", detail="ok")),
                ("b.crash", _boom),
            ]

        monkeypatch.setattr(_doctor, "_build_check_plan", _has_crash_plan)
        args = MagicMock()
        args.offline = True
        with pytest.raises(SystemExit) as exc_info:
            _doctor._run_doctor_cmd(args, output_format="json")
        assert exc_info.value.code == 2

    def test_secrets_never_appear_in_json_envelope(self, monkeypatch, capsys) -> None:
        """Wave 2a Round-2 F-TEST-34-03: end-to-end secret-masking proof.

        Sets a sentinel value for HF_TOKEN and confirms it never surfaces
        anywhere in the JSON envelope, even though FORGELM_OPERATOR (a
        non-secret env) is allowed to surface its value.  Pins the
        masking discipline against the *full dispatcher path*, not just
        the helper function in isolation."""
        from forgelm.cli.subcommands import _doctor

        sentinel = "ghs_test_token_DO_NOT_LEAK_42"
        monkeypatch.setenv("HF_TOKEN", sentinel)

        # Use a minimal plan that includes the operator-identity probe
        # (which is the one most likely to surface env values).
        def _identity_only_plan(*, offline: bool):
            return [("operator.identity", _doctor._check_operator_identity)]

        monkeypatch.setattr(_doctor, "_build_check_plan", _identity_only_plan)
        args = MagicMock()
        args.offline = True
        with pytest.raises(SystemExit):
            _doctor._run_doctor_cmd(args, output_format="json")
        captured = capsys.readouterr().out
        assert sentinel not in captured, "HF_TOKEN value must be masked in JSON envelope"

    def test_offline_inferred_from_hf_hub_offline_env(self, capsys, monkeypatch) -> None:
        """Wave 2a Round-2 F-XPR-07-01: HF_HUB_OFFLINE=1 should imply --offline.

        Without an explicit --offline flag, the dispatcher resolves the
        offline mode from HF_HUB_OFFLINE / TRANSFORMERS_OFFLINE.  This
        spares air-gapped operators from having to pass --offline on
        every doctor invocation when their shell already has the standard
        HF airgap envs set."""
        from forgelm.cli.subcommands import _doctor

        captured_offline = []

        def _spy_plan(*, offline: bool):
            captured_offline.append(offline)
            return [("a.pass", lambda: _doctor._CheckResult(name="a.pass", status="pass", detail="ok"))]

        monkeypatch.setattr(_doctor, "_build_check_plan", _spy_plan)
        monkeypatch.setenv("HF_HUB_OFFLINE", "1")
        # Argparse default is offline=False but env should flip it.
        args = MagicMock()
        args.offline = False
        with pytest.raises(SystemExit):
            _doctor._run_doctor_cmd(args, output_format="json")
        assert captured_offline == [True], "HF_HUB_OFFLINE=1 must promote dispatcher to offline mode"


class TestHfCacheWalkBoundaries:
    """Wave 2a Round-2 F-TEST-34-04: depth + file-count cap boundaries."""

    def test_walk_truncated_at_file_cap(self, tmp_path, monkeypatch) -> None:
        from forgelm.cli.subcommands._doctor import (
            _HF_CACHE_WALK_FILE_LIMIT,
            _check_hf_cache_offline,
        )

        cache = tmp_path / "cache"
        cache.mkdir()
        for i in range(_HF_CACHE_WALK_FILE_LIMIT + 50):
            (cache / f"f{i:06d}").write_bytes(b"x")
        monkeypatch.setenv("HF_HUB_CACHE", str(cache))
        monkeypatch.delenv("HF_HOME", raising=False)
        result = _check_hf_cache_offline()
        # The walk should have hit the file cap and flagged truncation.
        assert result.extras.get("walk_truncated") is True
        assert result.extras.get("file_count") == _HF_CACHE_WALK_FILE_LIMIT

    def test_walk_not_truncated_at_exactly_file_cap(self, tmp_path, monkeypatch) -> None:
        from forgelm.cli.subcommands._doctor import (
            _HF_CACHE_WALK_FILE_LIMIT,
            _check_hf_cache_offline,
        )

        cache = tmp_path / "cache"
        cache.mkdir()
        for i in range(_HF_CACHE_WALK_FILE_LIMIT):
            (cache / f"f{i:06d}").write_bytes(b"x")
        monkeypatch.setenv("HF_HUB_CACHE", str(cache))
        monkeypatch.delenv("HF_HOME", raising=False)
        result = _check_hf_cache_offline()
        # Exactly at cap: walked clean, no truncation.
        assert result.extras.get("walk_truncated") is False
        assert result.extras.get("file_count") == _HF_CACHE_WALK_FILE_LIMIT

    def test_walk_truncated_at_depth_cap(self, tmp_path, monkeypatch) -> None:
        from forgelm.cli.subcommands._doctor import (
            _HF_CACHE_WALK_DEPTH,
            _check_hf_cache_offline,
        )

        cache = tmp_path / "cache"
        # Build a tree deeper than the cap with a file at the bottom; the
        # bottom file is below the depth cap so it should NOT be counted.
        deep = cache.joinpath(*[f"d{i}" for i in range(_HF_CACHE_WALK_DEPTH + 2)])
        deep.mkdir(parents=True)
        (deep / "blob").write_bytes(b"x")
        monkeypatch.setenv("HF_HUB_CACHE", str(cache))
        monkeypatch.delenv("HF_HOME", raising=False)
        result = _check_hf_cache_offline()
        # The walk should report truncation since a non-empty subtree was
        # below the cap.
        assert result.extras.get("walk_truncated") is True


class TestDoctorSecretEnvNames:
    """Wave 2a Round-2 F-34-02: secret-env mask covers third-party tokens."""

    @pytest.mark.parametrize(
        "name",
        [
            "FORGELM_AUDIT_SECRET",
            "HF_TOKEN",
            "HUGGING_FACE_HUB_TOKEN",
            "HUGGINGFACE_TOKEN",
            "FORGELM_RESUME_TOKEN",
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "WANDB_API_KEY",
            "COHERE_API_KEY",
        ],
    )
    def test_known_secret_env_names_are_masked(self, name: str) -> None:
        from forgelm.cli.subcommands._doctor import _DOCTOR_SECRET_ENV_NAMES

        assert name in _DOCTOR_SECRET_ENV_NAMES


# ---------------------------------------------------------------------------
# Facade re-exports
# ---------------------------------------------------------------------------


class TestFacadeReExports:
    def test_doctor_helpers_reachable_via_facade(self) -> None:
        """Tests / monkeypatches reach doctor helpers via ``forgelm.cli``."""
        from forgelm import cli as _cli_facade

        for name in (
            "_run_doctor_cmd",
            "_run_all_checks",
            "_render_json",
            "_render_text",
            "_resolve_exit_code",
            "_check_forgelm_install",
            "_check_python_version",
            "_check_torch_cuda",
            "_check_operator_identity",
        ):
            assert hasattr(_cli_facade, name), f"forgelm.cli must re-export {name!r}"


class TestPypdfNormaliseDoctorProbe:
    """Phase 15 Task 3 / round-2 N-3 — dedicated coverage for the new probe."""

    def test_pass_when_table_round_trips_canonical_fixture(self):
        from forgelm.cli.subcommands._doctor import _check_pypdf_normalise_turkish

        result = _check_pypdf_normalise_turkish()
        assert result.status == "pass"
        assert result.name == "pypdf_normalise.turkish"
        assert result.extras.get("single_substitutions", 0) >= 5
        assert result.extras.get("default_profile") in ("none", "turkish")

    def test_fail_when_profile_silently_no_ops(self, monkeypatch):
        """If a future refactor breaks the dispatcher, the probe fails loudly."""
        import forgelm.cli.subcommands._doctor as doctor_mod

        # Patch the dispatcher used by the probe to no-op on the fixture so
        # the probe's "did the table actually rewrite?" guard fires.
        def fake_apply_profile(text, _profile):
            return text

        # The probe re-imports apply_profile inside its body — patch the
        # underlying module so the late import resolves to our shim.
        import forgelm._pypdf_normalise as norm_mod

        monkeypatch.setattr(norm_mod, "apply_profile", fake_apply_profile)
        result = doctor_mod._check_pypdf_normalise_turkish()
        assert result.status == "fail"
        assert "no substitutions" in result.detail.lower()
