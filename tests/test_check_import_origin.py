"""tools/check_import_origin.py regression tests.

The guard asserts the precondition every other gauntlet step rests on:
that ``import forgelm`` resolves to the working tree rather than a
shadowing ``site-packages`` install. It exists because a non-editable
install turned the gauntlet's ``--dry-run`` step into a check that
validated a weeks-old package and reported success.

Pinned contracts:

1. A path inside ``<repo>/forgelm`` passes (exit 0).
2. A path outside it fails (exit 1) and names BOTH paths, so the
   operator can see what shadowed what.
3. An unimportable ``forgelm`` fails (exit 1) rather than passing
   vacuously — the guard must never report OK on zero evidence.
4. Success output names the resolved path (a guard that says "OK"
   without naming its subject is the defect class it was written
   to catch).
5. ``--quiet`` silences success only; failures always print.
6. ``--strict`` is accepted and behaves as the default.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TOOL_PATH = _REPO_ROOT / "tools" / "check_import_origin.py"


def _load_tool() -> object:
    spec = importlib.util.spec_from_file_location("check_import_origin", _TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_import_origin"] = module
    spec.loader.exec_module(module)
    return module


class TestVerdict:
    """The pure decision function — no dependency on how the machine
    running these tests happens to have ForgeLM installed."""

    def test_path_inside_checkout_passes(self, tmp_path):
        tool = _load_tool()
        resolved = str(tmp_path / "forgelm" / "__init__.py")
        code, message = tool._verdict(resolved, "", tmp_path)
        assert code == 0
        assert "resolves inside this checkout" in message

    def test_nested_submodule_path_passes(self, tmp_path):
        """A resolved path deeper in the package still counts as inside."""
        tool = _load_tool()
        resolved = str(tmp_path / "forgelm" / "cli" / "__init__.py")
        code, _ = tool._verdict(resolved, "", tmp_path)
        assert code == 0

    def test_path_outside_checkout_fails(self, tmp_path):
        tool = _load_tool()
        stale = tmp_path / "site-packages" / "forgelm" / "__init__.py"
        code, message = tool._verdict(str(stale), "", tmp_path)
        assert code == 1
        # Both sides of the mismatch must be named — the whole point is
        # telling the operator which copy shadowed which.
        assert str(stale.resolve()) in message
        assert str((tmp_path / "forgelm").resolve()) in message
        assert "pip install -e" in message

    def test_sibling_prefix_path_is_not_treated_as_inside(self, tmp_path):
        """``<root>/forgelm-stale`` must not satisfy ``<root>/forgelm`` —
        a naive ``str.startswith`` check would pass it."""
        tool = _load_tool()
        impostor = tmp_path / "forgelm-stale" / "__init__.py"
        code, _ = tool._verdict(str(impostor), "", tmp_path)
        assert code == 1

    def test_unimportable_forgelm_fails_loudly(self, tmp_path):
        """Never report OK on zero evidence — the defect class this guard
        was written to catch."""
        tool = _load_tool()
        code, message = tool._verdict(None, "ModuleNotFoundError: No module named 'forgelm'", tmp_path)
        assert code == 1
        assert "ModuleNotFoundError" in message
        assert "pip install -e" in message

    def test_unimportable_with_no_diagnostic_still_fails(self, tmp_path):
        tool = _load_tool()
        code, message = tool._verdict(None, "", tmp_path)
        assert code == 1
        assert "<no output>" in message


class TestProbe:
    def test_probe_reports_none_when_import_fails(self, tmp_path):
        """A broken interpreter path must surface as (None, diagnostic),
        not as a crash or a silent pass."""
        tool = _load_tool()
        resolved, diagnostic = tool._probe_forgelm_location(python_exe=str(tmp_path / "definitely-not-an-interpreter"))
        assert resolved is None
        assert diagnostic  # the failure reason is carried, not swallowed

    def test_probe_finds_forgelm_in_this_environment(self):
        """End-to-end: the dev environment these tests run in must have an
        editable install, which is exactly what the guard asserts."""
        tool = _load_tool()
        resolved, diagnostic = tool._probe_forgelm_location()
        assert resolved is not None, f"forgelm not importable from a neutral cwd: {diagnostic}"
        assert Path(resolved).name == "__init__.py"


class TestCli:
    def test_exit_zero_in_this_checkout(self, capsys):
        tool = _load_tool()
        assert tool.main([]) == 0
        out = capsys.readouterr().out
        # Success must name its subject, not just say OK.
        assert str((_REPO_ROOT / "forgelm").resolve()) in out

    def test_quiet_suppresses_success_output(self, capsys):
        tool = _load_tool()
        assert tool.main(["--quiet"]) == 0
        assert capsys.readouterr().out == ""

    def test_strict_is_alias_of_default(self, capsys):
        tool = _load_tool()
        assert tool.main(["--strict"]) == 0
        assert "resolves inside this checkout" in capsys.readouterr().out

    def test_failure_goes_to_stderr(self, capsys, monkeypatch):
        """Failures must reach stderr so a CI log separates them from the
        success chatter of neighbouring guards."""
        tool = _load_tool()
        monkeypatch.setattr(
            tool,
            "_probe_forgelm_location",
            lambda *a, **k: ("/somewhere/else/forgelm/__init__.py", ""),
        )
        assert tool.main([]) == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "resolves OUTSIDE this checkout" in captured.err
