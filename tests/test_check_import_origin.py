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
7. The probe runs from a neutral cwd, so a ``forgelm`` directory that
   merely happens to sit in the caller's working directory cannot be
   mistaken for an install.

A note on ``@pytest.mark.requires_editable_install`` below: three cases
here assert ``main(...) == 0``, which is a statement about the *machine*,
not about the tool — it holds only when ``pip install -e .`` put this
checkout on ``sys.path``. ``.github/workflows/publish.yml`` installs the
built wheel non-editably on purpose, so there the guard correctly exits
1 and those three would fail for precisely the reason the guard was
written. The marker deselects them there. The verdict contract itself is
pinned by :class:`TestVerdict` as a pure function, which runs everywhere,
so nothing is actually left uncovered.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

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
        """End-to-end: forgelm must be importable from a neutral cwd.

        True for an editable install *and* for a wheel install, so this
        needs no marker — it only asserts importability, not location.
        """
        tool = _load_tool()
        resolved, diagnostic = tool._probe_forgelm_location()
        assert resolved is not None, f"forgelm not importable from a neutral cwd: {diagnostic}"
        assert Path(resolved).name == "__init__.py"

    def test_probe_ignores_a_forgelm_directory_in_the_callers_cwd(self, tmp_path, monkeypatch):
        """The ``cwd=<temp dir>`` kwarg is the whole mechanism, so pin it.

        ``python -c`` puts the process's working directory first on
        ``sys.path``. If the probe inherited the caller's cwd, any stray
        ``./forgelm/`` — a scratch copy, a half-finished worktree, or in
        the extreme a checkout with no install at all — would satisfy the
        import and the guard would report a shadowing install as healthy.
        Running from a throwaway directory is what models the *console
        script's* least-favourable path ordering instead.

        Delete ``cwd=neutral_cwd`` from ``_probe_forgelm_location`` and
        this test fails: the decoy below is what gets imported.
        """
        tool = _load_tool()
        decoy = tmp_path / "forgelm"
        decoy.mkdir()
        (decoy / "__init__.py").write_text("", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        resolved, diagnostic = tool._probe_forgelm_location()

        assert resolved is not None, f"probe could not import forgelm at all: {diagnostic}"
        assert Path(resolved).resolve() != (decoy / "__init__.py").resolve(), (
            "the probe imported a forgelm/ from its caller's cwd — the neutral-cwd "
            "guarantee in _probe_forgelm_location is gone, and the guard can no "
            "longer tell a real install from a directory that happens to be there"
        )


class TestCli:
    """CLI surface.

    The three ``== 0`` cases carry ``@pytest.mark.requires_editable_install``
    (see the module docstring); they are marked one by one rather than at
    class level so ``test_failure_goes_to_stderr``, which stubs the probe
    and is therefore environment-independent, keeps running in the release
    matrix.
    """

    @pytest.mark.requires_editable_install
    def test_exit_zero_in_this_checkout(self, capsys):
        tool = _load_tool()
        code = tool.main([])
        captured = capsys.readouterr()
        assert code == 0, f"guard rejected this environment:\n{captured.err}"
        # Success must name its subject, not just say OK.
        assert str((_REPO_ROOT / "forgelm").resolve()) in captured.out

    @pytest.mark.requires_editable_install
    def test_quiet_suppresses_success_output(self, capsys):
        tool = _load_tool()
        code = tool.main(["--quiet"])
        captured = capsys.readouterr()
        assert code == 0, f"guard rejected this environment:\n{captured.err}"
        assert captured.out == ""

    @pytest.mark.requires_editable_install
    def test_strict_is_alias_of_default(self, capsys):
        tool = _load_tool()
        code = tool.main(["--strict"])
        captured = capsys.readouterr()
        assert code == 0, f"guard rejected this environment:\n{captured.err}"
        assert "resolves inside this checkout" in captured.out

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
