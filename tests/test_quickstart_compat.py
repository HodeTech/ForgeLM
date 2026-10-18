"""Phase 10.5 quickstart compatibility tests.

Covers three independent improvements:

1. Windows ``cp1252`` stdout compatibility — selection reasons and the
   ``--list`` output must encode without raising ``UnicodeEncodeError``.
2. Multi-GPU VRAM probe — ``_detect_available_vram_gb`` must report the
   maximum total VRAM across all visible CUDA devices, not just the current
   one.
3. Quickstart audit event — ``run_quickstart`` must emit a structured
   ``quickstart.model_selection`` JSONL line beside the generated config so
   downstream tooling can replay the decision.
"""

from __future__ import annotations

import builtins
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from forgelm.quickstart import (
    TEMPLATES,
    _detect_available_vram_gb,
    auto_select_model,
    format_template_list,
    get_template,
    run_quickstart,
)

# ---------------------------------------------------------------------------
# 1. ASCII / cp1252 compatibility
# ---------------------------------------------------------------------------


class TestAsciiCompatibility:
    def test_selection_reason_is_pure_ascii(self):
        # Both the >= primary branch and the auto-downsize branch must emit
        # cp1252-encodable text. Probe each template's branches.
        for name in TEMPLATES:
            tpl = get_template(name)
            # Primary path: well above the threshold.
            _, reason_primary = auto_select_model(tpl, available_vram_gb=24.0)
            reason_primary.encode("cp1252")
            # Fallback paths: tiny GPU and "no GPU".
            _, reason_small = auto_select_model(tpl, available_vram_gb=2.0)
            reason_small.encode("cp1252")
            _, reason_none = auto_select_model(tpl, available_vram_gb=None)
            reason_none.encode("cp1252")

    def test_format_template_list_is_pure_ascii(self):
        output = format_template_list()
        # Will raise UnicodeEncodeError if any glyph falls outside cp1252.
        output.encode("cp1252")


# ---------------------------------------------------------------------------
# 2. Multi-GPU VRAM probe
# ---------------------------------------------------------------------------


class TestVramProbe:
    def test_detect_vram_returns_max_across_devices(self):
        fake_torch = MagicMock()
        fake_torch.cuda.is_available.return_value = True
        fake_torch.cuda.device_count.return_value = 2
        # mem_get_info() returns (free, total) per device.
        fake_torch.cuda.mem_get_info.side_effect = [
            (0, 8 * 1024**3),
            (0, 24 * 1024**3),
        ]
        # device(i) is used as a context manager.
        fake_torch.cuda.device.return_value.__enter__ = MagicMock(return_value=None)
        fake_torch.cuda.device.return_value.__exit__ = MagicMock(return_value=False)

        with patch.dict(sys.modules, {"torch": fake_torch}):
            result = _detect_available_vram_gb()

        assert result is not None
        assert abs(result - 24.0) < 0.01

    def test_detect_vram_no_cuda_returns_none(self):
        fake_torch = MagicMock()
        fake_torch.cuda.is_available.return_value = False

        with patch.dict(sys.modules, {"torch": fake_torch}):
            assert _detect_available_vram_gb() is None

    @pytest.mark.parametrize("backend", ["mps", "xpu"])
    def test_detect_vram_non_cuda_accelerator_returns_none(self, backend):
        fake_torch = MagicMock()
        fake_torch.cuda.is_available.return_value = False
        fake_torch.backends.mps.is_available.return_value = backend == "mps"
        fake_torch.xpu.is_available.return_value = backend == "xpu"

        with patch.dict(sys.modules, {"torch": fake_torch}):
            assert _detect_available_vram_gb() is None

    def test_detect_vram_probe_failure_returns_none(self, caplog):
        fake_torch = MagicMock()
        fake_torch.cuda.is_available.return_value = True
        fake_torch.cuda.device_count.return_value = 1
        fake_torch.cuda.mem_get_info.side_effect = RuntimeError("device unavailable")
        fake_torch.backends.mps.is_available.return_value = False
        fake_torch.xpu.is_available.return_value = False

        with caplog.at_level("DEBUG", logger="forgelm.quickstart"), patch.dict(sys.modules, {"torch": fake_torch}):
            assert _detect_available_vram_gb() is None

        assert "device unavailable" in caplog.text

    def test_detect_vram_device_count_zero_returns_none(self):
        fake_torch = MagicMock()
        fake_torch.cuda.is_available.return_value = True
        fake_torch.cuda.device_count.return_value = 0

        with patch.dict(sys.modules, {"torch": fake_torch}):
            assert _detect_available_vram_gb() is None

    def test_detect_vram_handles_torch_missing(self, monkeypatch):
        # Why monkeypatch.delitem (and not `sys.modules.pop`):  popping
        # torch without restoring it leaves the module re-import in a
        # half-initialised state for every later test in the same
        # pytest session — torch._C ends up unbound, and every
        # downstream `from trl import SFTConfig` then explodes with
        # `NameError: name '_C' is not defined`.  monkeypatch tracks
        # the deletion and restores the original torch on teardown.
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "torch":
                raise ImportError("no torch in this env")
            return real_import(name, *args, **kwargs)

        monkeypatch.delitem(sys.modules, "torch", raising=False)
        monkeypatch.setattr(builtins, "__import__", fake_import)
        assert _detect_available_vram_gb() is None


# ---------------------------------------------------------------------------
# 3. Quickstart audit event
# ---------------------------------------------------------------------------


REQUIRED_AUDIT_KEYS = {
    "timestamp",
    "event_type",
    "template",
    "template_primary_model",
    "template_fallback_model",
    "template_min_vram_for_primary_gb",
    "available_vram_gb",
    "chosen_model",
    "selection_reason",
    "model_override_used",
    "dataset_override_used",
    "dry_run",
}


def _read_audit_lines(audit_path: Path):
    return [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]


class TestAuditLog:
    def test_audit_event_written_on_quickstart_run(self, tmp_path):
        out = tmp_path / "configs" / "x" / "config.yaml"
        run_quickstart(
            "customer-support",
            output_path=str(out),
            available_vram_gb=24.0,
        )
        audit_path = tmp_path / "configs" / "x" / "quickstart_audit.jsonl"
        assert audit_path.is_file()
        events = _read_audit_lines(audit_path)
        assert len(events) == 1
        event = events[0]
        assert REQUIRED_AUDIT_KEYS.issubset(event.keys())
        assert event["event_type"] == "quickstart.model_selection"
        assert event["template"] == "customer-support"
        assert event["available_vram_gb"] == pytest.approx(24.0)
        assert event["model_override_used"] is False
        assert event["dataset_override_used"] is False
        assert event["dry_run"] is False

    def test_audit_event_written_for_dry_run(self, tmp_path):
        out = tmp_path / "configs" / "y" / "config.yaml"
        run_quickstart(
            "customer-support",
            output_path=str(out),
            available_vram_gb=24.0,
            dry_run=True,
        )
        audit_path = tmp_path / "configs" / "y" / "quickstart_audit.jsonl"
        assert audit_path.is_file()
        events = _read_audit_lines(audit_path)
        assert len(events) == 1
        assert events[0]["dry_run"] is True

    def test_audit_event_appends_across_runs(self, tmp_path):
        parent = tmp_path / "configs" / "shared"
        run_quickstart(
            "customer-support",
            output_path=str(parent / "config-a.yaml"),
            available_vram_gb=24.0,
            dry_run=True,
        )
        run_quickstart(
            "code-assistant",
            output_path=str(parent / "config-b.yaml"),
            available_vram_gb=24.0,
            dry_run=True,
        )
        audit_path = parent / "quickstart_audit.jsonl"
        events = _read_audit_lines(audit_path)
        assert len(events) == 2
        assert events[0]["template"] == "customer-support"
        assert events[1]["template"] == "code-assistant"

    def test_audit_event_failure_does_not_block(self, tmp_path):
        out = tmp_path / "configs" / "z" / "config.yaml"
        real_open = builtins.open

        def fake_open(file, mode="r", *args, **kwargs):
            # Only intercept the audit-log write; everything else (config
            # write, dataset copy, template read) must work normally.
            if "quickstart_audit.jsonl" in str(file) and "a" in mode:
                raise OSError("simulated disk-full")
            return real_open(file, mode, *args, **kwargs)

        with patch.object(builtins, "open", side_effect=fake_open):
            with patch("forgelm.quickstart.logger") as mock_logger:
                result = run_quickstart(
                    "customer-support",
                    output_path=str(out),
                    available_vram_gb=24.0,
                    dry_run=True,
                )

        # run_quickstart still produces a result.
        assert result.config_path == out
        # Warning was logged (not error, not raise).
        warning_calls = [c for c in mock_logger.warning.call_args_list if "audit" in str(c).lower()]
        assert warning_calls, "expected a warning log entry about the audit-write failure"

        # And the audit file was not created (or is empty) due to the failure.
        audit_path = tmp_path / "configs" / "z" / "quickstart_audit.jsonl"
        if audit_path.exists():
            assert audit_path.read_text(encoding="utf-8") == ""


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
