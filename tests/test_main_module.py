"""``python -m forgelm`` / ``python -m forgelm.cli`` entry-point tests.

ForgeLM has three ways in, and they must be indistinguishable to a user:

1. the ``forgelm`` console script (``[project.scripts]``),
2. ``python -m forgelm`` (``forgelm/__main__.py``),
3. ``python -m forgelm.cli`` (``forgelm/cli/__main__.py``), which the
   quickstart flow spawns as a subprocess.

All three call the same ``forgelm.cli.main``, so behaviour agrees by
construction — except for one thing that does *not*: ``sys.argv[0]``.
Under ``-m`` the interpreter sets it to the ``__main__.py`` file path, so
argparse would derive ``prog="__main__.py"`` and print ``usage:
__main__.py ...``, telling the operator to run a command that does not
exist. Both ``__main__.py`` files normalise ``sys.argv[0]`` to
``"forgelm"`` to prevent that.

Nothing pinned that normalisation before this module: deleting the line
changed every ``usage:`` and ``--help`` line of an entire entry point
while the suite stayed green.
"""

from __future__ import annotations

import difflib
import os
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent

# --help on the top-level parser builds every subparser; the CLI is
# documented as import-cheap (no torch at import time), but a generous
# ceiling keeps a pathological environment from hanging the suite.
_TIMEOUT_SECONDS = 120

# Both module entry points, exercised identically.
_MODULE_ENTRY_POINTS = ["forgelm", "forgelm.cli"]


def _console_script() -> str | None:
    """Locate the ``forgelm`` console script *of the running interpreter*.

    ``shutil.which`` alone is not enough: pytest is frequently invoked as
    ``.venv/bin/python -m pytest`` without the venv activated, so the
    venv's ``bin``/``Scripts`` directory is absent from ``PATH`` and the
    script the *running interpreter* installed would look missing.

    "Beside ``sys.executable``" is not enough either, and that was a POSIX
    assumption: it holds on Unix, where console scripts and ``python`` share
    ``<prefix>/bin``, but never on Windows, where ``python.exe`` sits in
    ``<prefix>`` and scripts go to ``<prefix>\\Scripts``. So on Windows the
    intended branch could not fire and every lookup fell through to a bare
    ``PATH`` search — which resolves *a* ``forgelm.exe``, with nothing tying
    it to ``sys.executable``. The comparison this module performs is only
    meaningful between two copies of the same install, so an unverified PATH
    hit is exactly the wrong answer to fall back to.

    ``sysconfig.get_path("scripts")`` is the portable question. Both the
    default and the per-user scheme are consulted (a ``pip install --user``
    puts the script in the latter), and ``PATH`` remains a last resort for
    layouts neither scheme describes.
    """
    script_dirs = [sysconfig.get_path("scripts")]
    user_scheme = f"{os.name}_user"
    if user_scheme in sysconfig.get_scheme_names():
        script_dirs.append(sysconfig.get_path("scripts", user_scheme))
    # Kept for the (Unix) case where the interpreter is not where its scheme
    # says it is — a relocated or symlinked venv.
    script_dirs.append(str(Path(sys.executable).parent))

    for directory in script_dirs:
        for name in ("forgelm", "forgelm.exe"):
            candidate = Path(directory) / name
            if candidate.is_file():
                return str(candidate)
    return shutil.which("forgelm")


def _run_module(module: str, *argv: str, cwd: str | None = None) -> subprocess.CompletedProcess:
    """Spawn ``<this interpreter> -m <module> <argv...>``.

    cwd defaults to the checkout so ``-m`` resolves the working tree
    rather than any installed copy — the same reasoning as
    ``tools/check_import_origin.py``. Callers that need to compare
    against the *installed* copy pass a neutral cwd instead.
    """
    return subprocess.run(  # nosec B603 — fixed argv, shell=False, interpreter is sys.executable.
        [sys.executable, "-m", module, *argv],
        capture_output=True,
        text=True,
        check=False,
        cwd=cwd or str(_REPO_ROOT),
        timeout=_TIMEOUT_SECONDS,
    )


class TestProgName:
    """``sys.argv[0] = "forgelm"`` in the ``__main__.py`` files."""

    @pytest.mark.parametrize("module", _MODULE_ENTRY_POINTS)
    def test_help_usage_line_says_forgelm(self, module):
        proc = _run_module(module, "--help")
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.startswith("usage: forgelm"), (
            f"python -m {module} --help opened with {proc.stdout.splitlines()[:1]!r}; "
            f"the sys.argv[0] normalisation in {module.replace('.', '/')}/__main__.py is missing"
        )

    @pytest.mark.parametrize("module", _MODULE_ENTRY_POINTS)
    def test_help_never_leaks_the_module_filename(self, module):
        """``__main__.py`` must not appear anywhere in help output.

        The usage line is not the only place argparse interpolates
        ``prog`` — subcommand usage, epilogs and error messages do too.
        """
        proc = _run_module(module, "--help")
        assert "__main__.py" not in proc.stdout + proc.stderr

    @pytest.mark.parametrize("module", _MODULE_ENTRY_POINTS)
    def test_error_usage_line_also_says_forgelm(self, module):
        """The path that actually matters: argparse's error usage line.

        A user who mistypes a flag gets this, and it must name a command
        they can retype.
        """
        proc = _run_module(module, "--definitely-not-a-flag")
        assert proc.returncode != 0
        combined = proc.stdout + proc.stderr
        assert "usage: forgelm" in combined, combined
        assert "__main__.py" not in combined

    def test_both_module_entry_points_print_identical_help(self):
        """``forgelm`` and ``forgelm.cli`` are one CLI, not two.

        Guards against a future divergence in either ``__main__.py`` —
        the pre-existing state this test was added alongside, where only
        one of the two normalised ``prog``.
        """
        top = _run_module("forgelm", "--help")
        pkg = _run_module("forgelm.cli", "--help")
        assert top.returncode == 0 and pkg.returncode == 0
        assert top.stdout == pkg.stdout

    @pytest.mark.skipif(
        _console_script() is None,
        reason="console script not found (forgelm not installed in this environment)",
    )
    def test_module_help_matches_the_console_script(self, tmp_path):
        """The reason the normalisation exists, stated as an assertion.

        ``python -m forgelm --help`` must be byte-for-byte what
        ``forgelm --help`` prints; docs quote one and users run the other.

        Both sides run from a neutral cwd so both resolve the *same*
        copy of forgelm. Anchoring ``-m`` at the checkout instead would
        compare the working tree against the installed package, and a
        non-editable install (which is what the release matrix has)
        would make this a version-skew test rather than a prog test.

        One launcher-name normalisation is applied before comparing, and
        only one: an occurrence of ``forgelm.exe`` in the console script's
        output collapses to ``forgelm``. Windows console scripts are
        ``.exe`` launchers, so ``prog`` can legitimately carry the suffix
        depending on how the wrapper rewrites ``sys.argv[0]``; that is a
        launcher-naming detail, not the divergence this test guards. The
        substitution is a no-op on POSIX — no ``.exe`` string can appear —
        so the comparison there stays byte-for-byte, and it cannot mask any
        other difference, because it rewrites nothing else.
        """
        script = subprocess.run(  # nosec B603 — path resolved locally, fixed argv, shell=False.
            [_console_script(), "--help"],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(tmp_path),
            timeout=_TIMEOUT_SECONDS,
        )
        assert script.returncode == 0, script.stderr

        module = _run_module("forgelm", "--help", cwd=str(tmp_path))
        assert module.returncode == 0, module.stderr

        expected = script.stdout.replace("forgelm.exe", "forgelm")
        if module.stdout != expected:
            # A bare `assert a == b` on two screens of help text is unreadable
            # in a CI log, and this test can only fail on a platform the author
            # is not sitting at. Name the difference instead of printing both.
            diff = "\n".join(
                difflib.unified_diff(
                    expected.splitlines(),
                    module.stdout.splitlines(),
                    fromfile=f"console script ({_console_script()})",
                    tofile=f"python -m forgelm ({sys.executable})",
                    lineterm="",
                )
            )
            raise AssertionError(f"console-script help and module help diverged on {sys.platform}:\n{diff}")


class TestExitCodes:
    """``sys.exit(...)`` reaches the shell from the module form."""

    @pytest.mark.parametrize("module", _MODULE_ENTRY_POINTS)
    def test_config_error_is_not_reported_as_success(self, module, tmp_path):
        """A missing config file must not exit 0.

        ``forgelm.cli.main`` terminates via ``sys.exit`` internally, so
        this passes whether or not the wrapper adds its own ``sys.exit``;
        it is here so that a future ``main()`` returning an int instead
        of exiting cannot silently turn every module-form failure into a
        success for CI pipelines that key off the exit code.
        """
        missing = tmp_path / "nope.yaml"
        proc = _run_module(module, "--config", str(missing))
        assert proc.returncode != 0, proc.stdout + proc.stderr
