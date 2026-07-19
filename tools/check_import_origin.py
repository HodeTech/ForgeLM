#!/usr/bin/env python3
"""CI guard — ``import forgelm`` must resolve to *this* checkout.

Every other guard in the gauntlet checks one artefact. This one checks
the **precondition** all of them rest on: that the ``forgelm`` a process
imports is the working tree the contributor just edited, and not some
other copy installed into ``site-packages``.

Why it exists
-------------

A console script's ``sys.path[0]`` is the script's own ``bin`` /
``Scripts`` directory — never the current working directory. So
``forgelm --config config_template.yaml --dry-run`` imports whatever
``forgelm`` is *installed*. With a non-editable install in the venv, that
gauntlet step validated a package built weeks earlier and reported
success while the tree held entirely different code — a green check that
proved nothing. The gauntlet now invokes ``python -m forgelm`` instead
(``-m`` puts the cwd first on ``sys.path``), but that only repairs the
one step: ``python3 tools/check_yaml_snippets.py``,
``check_library_api_doc.py`` and ``check_doc_numerical_claims.py`` all
run with ``sys.path[0] == tools/`` and import ``forgelm`` straight from
``site-packages``, so a stale install still silently poisons them.

Rather than rewrite each call site, this guard asserts the environment
invariant once, at the head of the gauntlet: an editable install of this
checkout is active. When it holds, every entry point — console script,
``-m``, ``tools/*.py``, notebooks, a bare ``python -c`` — agrees.

What it checks
--------------

Spawns ``<this interpreter> -c "import forgelm"`` with the working
directory set **outside** the repository, so the cwd entry on
``sys.path`` cannot mask a missing install, then compares the resolved
``forgelm.__file__`` against ``<repo root>/forgelm``. This deliberately
models the least-favourable path ordering (the one the console script
and the ``tools/`` guards get), not the most-favourable one.

The resolved path is printed on success as well as on failure: a guard
that reports "OK" without naming what it examined is the same defect
class it was written to catch.

Run via::

    python3 tools/check_import_origin.py
    python3 tools/check_import_origin.py --strict   # alias of default
    python3 tools/check_import_origin.py --quiet    # silent on success

Exit codes (per ``tools/`` contract — NOT the public 0/1/2/3/4/5/6
surface that ``forgelm/`` honours):

- ``0`` — ``forgelm`` resolves inside this checkout.
- ``1`` — it resolves elsewhere, or is not importable at all.
"""

from __future__ import annotations

import argparse
import subprocess  # nosec B404 — fixed argv, no shell; spawns this same interpreter.
import sys
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

_REPO_ROOT = Path(__file__).resolve().parent.parent

# ``import forgelm`` is documented as cheap (no torch / transformers at
# import time — see forgelm/__init__.py's lazy-import discipline), so this
# ceiling is generous. It exists only so a pathological sitecustomize or a
# stalled network filesystem cannot hang the head of the gauntlet.
_PROBE_TIMEOUT_SECONDS = 60

_REMEDIATION = (
    "Install this checkout in editable mode so every entry point resolves it:\n"
    "    pip install -e '.[dev]'\n"
    "Until then the gauntlet validates the installed copy, not your edits."
)


def _probe_forgelm_location(python_exe: Optional[str] = None) -> Tuple[Optional[str], str]:
    """Resolve ``forgelm.__file__`` from a neutral working directory.

    Runs the import in a subprocess whose cwd is a throwaway temp
    directory, so the implicit cwd entry on ``sys.path`` cannot make a
    missing install look present. This is the same path ordering the
    ``forgelm`` console script and the ``tools/*.py`` guards see.

    Args:
        python_exe: Interpreter to probe with. Defaults to the running one.

    Returns:
        ``(resolved_path, diagnostic)``. ``resolved_path`` is ``None``
        when ``forgelm`` could not be imported at all; ``diagnostic``
        then carries the child's stderr so the failure is actionable
        rather than merely reported.
    """
    exe = python_exe or sys.executable
    try:
        with tempfile.TemporaryDirectory() as neutral_cwd:
            proc = subprocess.run(  # nosec B603 — fixed argv, shell=False, interpreter is sys.executable.
                [exe, "-c", "import forgelm; print(forgelm.__file__)"],
                capture_output=True,
                text=True,
                cwd=neutral_cwd,
                check=False,
                timeout=_PROBE_TIMEOUT_SECONDS,
            )
    except (OSError, subprocess.SubprocessError) as exc:
        # The interpreter could not be launched (missing / not executable)
        # or the import hung past the timeout. Both are "we could not
        # establish where forgelm lives", which must fail the guard rather
        # than crash it — a traceback here would read as a broken guard
        # instead of a broken environment.
        return None, f"{exc.__class__.__name__}: {exc}"
    if proc.returncode != 0:
        return None, (proc.stderr or proc.stdout or "").strip()
    location = proc.stdout.strip()
    if not location:
        return None, "the import succeeded but forgelm.__file__ was empty (namespace package?)"
    return location, ""


def _verdict(resolved: Optional[str], diagnostic: str, repo_root: Path) -> Tuple[int, str]:
    """Decide pass/fail for a resolved ``forgelm`` location.

    Pure so the decision is testable without depending on how the
    machine running the tests happens to have ForgeLM installed.

    Args:
        resolved: ``forgelm.__file__`` as reported by the probe, or
            ``None`` when the import failed.
        diagnostic: Child-process stderr, used only when ``resolved`` is
            ``None``.
        repo_root: Directory that must contain the resolved path.

    Returns:
        ``(exit_code, message)`` — ``0`` iff the resolved path lies
        inside ``repo_root``.
    """
    expected = (repo_root / "forgelm").resolve()
    if resolved is None:
        return 1, (
            f"FAIL: 'import forgelm' failed outside the repository.\n"
            f"  expected package : {expected}\n"
            f"  import error     : {diagnostic or '<no output>'}\n\n{_REMEDIATION}"
        )
    actual = Path(resolved).resolve()
    if actual.is_relative_to(expected):
        return 0, f"OK: 'import forgelm' resolves inside this checkout — {actual}"
    return 1, (
        f"FAIL: 'import forgelm' resolves OUTSIDE this checkout.\n"
        f"  resolved to      : {actual}\n"
        f"  expected inside  : {expected}\n\n"
        f"Every gauntlet step that imports forgelm — the dry-run, tools/check_yaml_snippets.py,\n"
        f"tools/check_library_api_doc.py, tools/check_doc_numerical_claims.py — is validating\n"
        f"that other copy, so a green result says nothing about your working tree.\n\n{_REMEDIATION}"
    )


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify that 'import forgelm' resolves to this checkout, not a shadowing install.",
    )
    parser.add_argument("--strict", action="store_true", help="Alias of default; exits 1 on drift.")
    parser.add_argument("--quiet", action="store_true", help="Suppress success summary.")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    resolved, diagnostic = _probe_forgelm_location()
    code, message = _verdict(resolved, diagnostic, _REPO_ROOT)
    if code == 0:
        if not args.quiet:
            print(message)
    else:
        print(message, file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
