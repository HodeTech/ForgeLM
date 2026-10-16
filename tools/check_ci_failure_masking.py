#!/usr/bin/env python3
"""Refuse failure-masking wrappers around required CI commands.

`docs/standards/testing.md` forbids `|| true` in CI, and CLAUDE.md principle 6
forbids fake-green status, but only the three documented local gauntlets were
checked, and only for the literal `|| true`. Appending `|| true` to the
validation job's `node --test` command left every wiring test green, and so did
`|| :`, `|| exit 0`, `| tee` without `pipefail`, `set +e`, a step-level
`continue-on-error`, or `cmd && echo ok` followed by another line — bash's
errexit ignores a failure anywhere in an `&&` / `||` list except its last
command.

This guard parses every GitHub Actions `run:` block (workflows and composite
actions) and the three gauntlet blocks with the shell parser in
`tools/_shell_syntax.py`, so comments, quoted prose, `python -c` programs and
quoted here-documents are never mistaken for commands. For every *required*
command — `pytest`, `node --test`, `ruff`, `mypy`, the `forgelm` CLI and the
`tools/` guards, also behind wrappers such as `env`, `timeout`, `xvfb-run`,
`uv run`, `uvx ruff@0.8`, `conda run` or `coverage run` — it checks that a
non-zero exit still fails the step (or the gauntlet chain). It models errexit
and pipefail as bash applies them, including the contexts where errexit is
ignored (`&&` / `||` lists, `{ … }` groups placed there, conditions), `set` /
`shopt -o` changes, noexec, `bash -c`, `eval`, a script a shell reads from a
here-document, and the shell scripts a step runs or sources from the
repository (`bash ci/x.sh`, `./ci/x.sh`, `source ci/x.sh`,
`$GITHUB_WORKSPACE/ci/x.sh`, `$PWD/ci/x.sh`), from the step's
`working-directory:`. ci.yml and each gauntlet must still run `pytest`,
`node --test`, `ruff` and a `tools/` guard, so a scan that recognises nothing
cannot pass.

It accepts only the shapes it models and fails closed on the rest, so some
correct but indirect forms are rejected too: re-raising a status captured with
`set +e` or `|| rc=$?` later, `${PIPESTATUS[0]}`, `wait "$pid"`, `out=$(cmd)`, a
required command inside a `case` or an `if` branch, `while` / `until` loops,
`for` loops over a variable or with a `continue` / `break` before the required
command, shell functions, and unknown wrappers or commands taken from a `${{ }}`
expression. An `if` that tests a required command is accepted when the branch
its failure takes ends with a non-zero `exit` and nothing else in it can leave
early (`if ! cmd; then echo …; exit 1; fi`). Keep required commands plain; a
fallback may report, then must end with `exit 1` or re-raise a captured status
(`|| { rc=$?; …; exit "$rc"; }`), with nothing before that which can leave the
group. It also rejects `continue-on-error`, `if: false`, `if: failure()` or
`if: cancelled()` on a step or job that runs a required command, directly or
through local actions; `ruff --exit-zero` and `--fix-only`; an EXIT/ERR trap
that can exit 0, runs `exec` or runs a required command, an ERR trap that
turns errexit off, and a DEBUG or RETURN trap that can exit or turn errexit
off; a function, alias or `hash -p` that takes over a required command or one
this guard relies on (`exit`, `set`, a wrapper or a shell); a required command
that an `exit`, `exec` or `set -n` bash always reaches first keeps from running
(also behind `!` or `x=1 &&`, in a `{ … }` group, a function, or a sourced or
`eval`'d script); a repository script it cannot find, such as one named from
`$(dirname "$0")`, or that runs itself; and a `${{ }}` expression or matrix
value that pastes shell syntax.

It catches accidental masking, not deliberate evasion: whoever can edit a
workflow can delete its test step. Beyond it are a required command run under
another name (`$CMD`), by another program (`make`, `tox`, a Python script) or
on another machine or container, whose script it never reads
(`docker exec c sh -c '…'`, `ssh host '…'`); a script generated or downloaded
while the step runs (`eval "$(…)"`, `curl … | sh`); a program replaced on disk
or put first on `PATH`; a captured status changed by a script it cannot
follow; a DEBUG trap that skips commands under `extdebug`; a remote action;
any other condition, such as `if: always()` on a job that needs the tests,
`${{ 1 == 2 }}`, or an `if:` on a job that calls a reusable workflow; and
options other than `--exit-zero` and `--fix-only` that empty a check
(`pytest --collect-only`).

Independently of required commands, a `|| true` / `|| :` / `|| exit 0` fallback
is refused everywhere, command substitutions and trap actions included, except
the documented scanner + severity-helper pattern:
`bandit` or `pip-audit` writing a report, `|| true`, and immediately after it
`tools/check_bandit.py` / `tools/check_pip_audit.py` given that same report,
itself a required command. A helper that reads another file — or is missing —
does not gate the scanner, so the exception does not apply.

Exit codes follow the tools/ contract: 0 clean, 1 findings.
"""

from __future__ import annotations

import argparse
import posixpath
import re
import sys
from dataclasses import dataclass, replace
from itertools import islice
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple, Union

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    print(f"check_ci_failure_masking: PyYAML is not importable ({exc}); `pip install pyyaml`.", file=sys.stderr)
    sys.exit(1)

# The shell lexer and parser live in a sibling module, imported the way the other guards share code.
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from _shell_syntax import (  # type: ignore
        AndOr,
        Case,
        Command,
        Expression,
        Function,
        Group,
        If,
        Item,
        Loop,
        Pipeline,
        Script,
        ShellParseError,
        Simple,
        children,
        own_words,
        parse,
    )
except ImportError as exc:  # pragma: no cover
    print(f"check_ci_failure_masking: cannot import tools/_shell_syntax.py ({exc}).", file=sys.stderr)
    sys.exit(1)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_GAUNTLET_DOCS = ("CLAUDE.md", "AGENTS.md", "CONTRIBUTING.md")

# Same guard-name grammar as tests/test_guard_wiring.py's _GUARD_NAME_RE.
_GUARD_SCRIPT_RE = re.compile(r"(?:^|/)tools/(check_[a-z0-9_]{1,64}\.py|update_site_version\.py)$")
_PYTHON_RE = re.compile(r"python(?:3(?:\.[0-9]{1,2})?)?")
_STATUS_CAPTURE_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]{0,63})=\$\?")
# The name a tool runner's package spec starts with: `ruff@0.8.0`, `ruff>=0.8`, `ruff[extra]`.
_PACKAGE_RE = re.compile(r"[A-Za-z0-9._-]{1,128}")
# ruff's flags that make it exit 0 whatever it finds; its one-letter flags combine (`-ne`, `-qe`).
_RUFF_EXIT_ZERO_RE = re.compile(r"--exit-zero|--fix-only|-[nqsvw]{0,5}e[enqsvw]{0,5}")
# The required programs run by name, and those also run as `python -m <module>`.
_REQUIRED_PROGRAMS = ("pytest", "py.test", "ruff", "mypy", "forgelm")
_GATE_MODULES = ("pytest", "mypy", "ruff", "forgelm")
# Arguments that make a third-party tool print its help or version instead of checking anything.
# `forgelm --version` stays required: it is this repository's CLI smoke check.
_INFO_FLAGS = ("--help", "-h", "--version", "-V")
_INFO_KINDS = ("pytest", "ruff", "mypy", "node --test")
# Commands that may name a required program without running it (`pip install pytest`, `grep -q ruff x`).
_MENTION_OK = frozenset(
    ("pip", "pip3", "pipx", "conda", "mamba", "apt", "apt-get", "brew", "echo", "printf", "which", "type", "hash",
     "cd", "pushd", "popd", "ls", "cat", "grep", "egrep", "fgrep", "rg", "sed", "awk", "head", "tail", "wc", "sort",
     "uniq", "cut", "tr", "tee", "mkdir", "rm", "rmdir", "cp", "mv", "ln", "touch", "chmod", "chown", "stat", "file",
     "du", "test", "[", "export", "unset", "local", "declare", "typeset", "readonly", "read", "basename", "dirname",
     "realpath", "readlink", "diff", "cmp", "tar", "zip", "unzip", "curl", "wget", "git", "jq", "yq", "sha256sum",
     "shasum", "date", "sleep")
)  # fmt: skip
# `python -m <module>` commands that read the files they are given without running them.
_MENTION_OK_MODULES = ("pip", "pipx", "py_compile", "compileall", "tabnanny", "json.tool", "venv", "ensurepip")
# Each scanner and the severity helper that must read its report.
_SCANNER_HELPERS = {"bandit": "tools/check_bandit.py", "pip-audit": "tools/check_pip_audit.py"}
_SHELLS = ("bash", "sh", "dash", "ksh", "zsh")
# Where a shell reads a script from its standard input: `source /dev/stdin <<'EOF'`.
_STDIN_PATHS = ("/dev/stdin", "/dev/fd/0", "/proc/self/fd/0")
# A script path taken from the repository root, and the suffixes of a script this guard must be able to read.
_WORKSPACE_PREFIXES = ("$GITHUB_WORKSPACE/", "${GITHUB_WORKSPACE}/")
_SCRIPT_SUFFIXES = (".sh", ".bash")
# A script path taken from the working directory, and the start of one taken from the running script's own.
_CWD_PREFIXES = ("$PWD/", "${PWD}/", "$(pwd)/")
_SCRIPT_DIR_PREFIXES = ("$(dirname ", "$(cd ", "${0%/*}/", "${BASH_SOURCE")
# The fragment of each required command kind that ci.yml and every gauntlet must
# contain. A surface missing one has stopped running that check, or this guard
# no longer recognises it; either way a clean result would mean nothing.
_EXPECTED_KINDS = ("pytest", "node --test", "ruff", "tools/check_")

_NOOP_FALLBACKS = (("true",), (":",), ("/bin/true",), ("/usr/bin/true",), ("exit", "0"))

TERMINATES = "terminates"  # every failure of a required command inside ends the shell
STATUS = "status"  # some failure leaves a non-zero exit status that errexit acts on
RETURNS = "returns"  # a `{ … }` group's non-zero status, which errexit never acts on


# ── Analysis ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ShellOptions:
    """The shell state that decides whether a failing command ends the step.

    ``ignored`` marks a context where bash ignores errexit: every command of an
    ``&&`` / ``||`` list but the last, and everything inside a compound placed
    there. A ``set -e`` inside such a context does not switch errexit back on.
    """

    errexit: bool
    pipefail: bool
    ignored: bool = False

    @property
    def stops(self) -> bool:
        """Whether a failing command ends the script here."""
        return self.errexit and not self.ignored


GITHUB_DEFAULT = ShellOptions(errexit=True, pipefail=False)  # `bash -e {0}` when no shell is set
INTERACTIVE = ShellOptions(errexit=False, pipefail=False)  # a gauntlet pasted into a terminal


_WRAPPER_VALUE_OPTIONS = {
    "timeout": ("-s", "-k", "--signal", "--kill-after"),
    "nice": ("-n", "--adjustment"),
    "env": ("-u", "-C", "--unset", "--chdir"),
    "sudo": ("-u", "-g", "-C", "-h", "-p", "--user", "--group"),
    "xargs": ("-a", "-d", "-E", "-I", "-L", "-n", "-P", "-s"),
    "stdbuf": ("-i", "-o", "-e"),
    "ionice": ("-c", "-n", "-p", "-P", "-u", "--class", "--classdata"),
    "xvfb-run": ("-n", "-s", "-f", "-e", "-p", "-w", "--server-num", "--server-args", "--auth-file"),
}
_WRAPPERS = tuple(_WRAPPER_VALUE_OPTIONS) + ("time", "command", "builtin", "exec", "nohup", "setsid", "taskset")
# Wrappers whose first operand is not the command: `timeout DURATION cmd`, `taskset MASK cmd`.
_WRAPPER_OPERANDS = ("timeout", "taskset")
# `<runner> run CMD` propagates CMD's exit status; these are the run options that take a value.
_RUNNERS = ("uv", "poetry", "pipenv", "hatch", "pdm")
# `conda run [options] CMD` also propagates CMD's status.
_CONDA = ("conda", "mamba", "micromamba")
_CONDA_RUN_VALUE_OPTIONS = ("-n", "--name", "-p", "--prefix", "--cwd")
_RUNNER_VALUE_OPTIONS = (
    "-p", "--python", "-w", "--with", "--with-requirements", "--with-editable", "--project",
    "--directory", "--package", "--extra", "--group", "--env-file", "--index", "--index-url",
    "--extra-index-url", "-f", "--find-links", "--from", "--spec", "--pip-args",
)  # fmt: skip
# Commands a function, alias or `hash -p` must not take over: the required
# programs, every command this guard reads through or relies on, and bash's
# hook for a command it cannot find.
_PROTECTED = frozenset(
    _REQUIRED_PROGRAMS
    + ("node", "coverage", "find", "eval", "exit", "false", "set", "shopt", "command_not_found_handle")
    + tuple(_SCANNER_HELPERS) + _WRAPPERS + _RUNNERS + _CONDA + _SHELLS
)  # fmt: skip


def _drop_options(argv: List[str], value_options: Tuple[str, ...]) -> List[str]:
    while argv and argv[0].startswith("-"):
        argv = argv[2:] if argv[0] in value_options else argv[1:]
    return argv


def _env_argv(args: List[str]) -> List[str]:
    """The command ``env`` runs: options and ``NAME=value`` dropped, ``-S STRING`` split into words."""
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in ("-S", "--split-string") and index + 1 < len(args):
            return args[index + 1].split() + args[index + 2 :]
        if arg.startswith("--split-string="):
            return arg.split("=", 1)[1].split() + args[index + 1 :]
        if arg.startswith("-S") and len(arg) > 2:
            return arg[2:].split() + args[index + 1 :]
        if not (arg.startswith("-") or "=" in arg):
            break
        index += 2 if arg in _WRAPPER_VALUE_OPTIONS["env"] else 1
    return args[index:]


def _argv(command: Simple) -> List[str]:
    """Words of *command* with transparent wrappers (``env``, ``timeout``, ``uv run``, ...) removed."""
    argv = [word.text for word in command.words]
    while argv:
        program = posixpath.basename(argv[0])
        if program in _RUNNERS and argv[1:2] == ["run"]:
            argv = _drop_options(argv[2:], _RUNNER_VALUE_OPTIONS)
            continue
        # Tool runners: `uvx ruff`, `uv tool run ruff`, `pipx run ruff`.
        tool_run = 1 if program == "uvx" else 0
        tool_run = 3 if program == "uv" and argv[1:3] == ["tool", "run"] else tool_run
        tool_run = 2 if program == "pipx" and argv[1:2] == ["run"] else tool_run
        if tool_run:
            argv = _drop_options(argv[tool_run:], _RUNNER_VALUE_OPTIONS)
            spec = _PACKAGE_RE.match(argv[0]) if argv and "/" not in argv[0] else None
            argv = [spec.group(0)] + argv[1:] if spec else argv  # `uvx ruff@0.8.0` runs `ruff`
            continue
        if program in _CONDA and argv[1:2] == ["run"]:
            argv = _drop_options(argv[2:], _CONDA_RUN_VALUE_OPTIONS)
            continue
        if program == "coverage" and argv[1:2] == ["run"]:
            return ["python"] + argv[2:]  # `coverage run` takes Python's `-m module` or `script.py`
        if program == "env":
            argv = _env_argv(argv[1:])
            continue
        if program not in _WRAPPERS:
            return argv
        if program == "command" and argv[1:2] in (["-v"], ["-V"]):
            return []  # a lookup: `command -v pytest` runs nothing
        argv = _drop_options(argv[1:], _WRAPPER_VALUE_OPTIONS.get(program, ()))
        if program in _WRAPPER_OPERANDS and argv:
            argv = argv[1:]  # the duration or the CPU mask
    return argv


def _python_target(args: List[str]) -> Tuple[str, str]:
    """``("module", name)``, ``("script", path)`` or ``("code", "")`` for a Python argv tail."""
    index = 0
    while index < len(args):
        arg = args[index]
        index += 1
        if not arg.startswith("-") or arg == "-":
            return ("script", arg) if arg != "-" else ("code", "")
        if arg.startswith("--"):
            continue
        # A cluster of one-letter flags: `-m`/`-c` end it, and `-X`/`-W` take a value.
        for position, letter in enumerate(arg[1:], start=2):
            rest = arg[position:]
            if letter == "m":
                return "module", rest or (args[index] if index < len(args) else "")
            if letter == "c":
                return "code", ""
            if letter in "XW":
                index += 0 if rest else 1
                break
    return "code", ""


def _gate_module(module: str) -> Optional[str]:
    root = module.split(".")[0]
    return root if root in _GATE_MODULES and (module == root or root == "forgelm") else None


def _after(argv: List[str], target: str) -> List[str]:
    """The arguments after Python's ``-m module`` or script operand *target*."""
    for index, arg in enumerate(argv[1:], start=1):
        if arg == target or (arg.startswith("-") and arg.endswith(target)):  # `-m pytest`, `-mpytest`
            return argv[index + 1 :]
    return []


def _required_kind(argv: List[str]) -> Tuple[Optional[str], List[str]]:
    """The kind of required command *argv* names, and the arguments that program receives."""
    if not argv:
        return None, []
    program = posixpath.basename(argv[0])
    guard = _GUARD_SCRIPT_RE.search(argv[0])
    if guard:
        return "tools/" + guard.group(1), argv[1:]
    if program in _REQUIRED_PROGRAMS:
        return ("pytest" if program == "py.test" else program), argv[1:]
    if program == "node" and "--test" in argv[1:]:
        return "node --test", argv[1:]
    if _PYTHON_RE.fullmatch(program):
        kind, target = _python_target(argv[1:])
        tail = _after(argv, target)
        if kind == "module" and target == "coverage" and tail[:1] == ["run"]:
            return _required_kind(["python"] + tail[1:])  # `python -m coverage run -m pytest`
        if kind == "module":
            return _gate_module(target), tail
        if kind == "script":
            guard = _GUARD_SCRIPT_RE.search(target)
            return ("tools/" + guard.group(1) if guard else None), tail
    return None, []


def _required_argv(argv: List[str]) -> Optional[str]:
    """The kind of required command *argv* runs; None when a tool only prints its help or version."""
    kind, tail = _required_kind(argv)
    return None if kind in _INFO_KINDS and any(arg in _INFO_FLAGS for arg in tail) else kind


def _find_exec(argv: List[str]) -> Optional[Tuple[List[str], str]]:
    """The command ``find -exec``/``-execdir`` runs, and its terminator (``;`` or ``+``)."""
    if not argv or posixpath.basename(argv[0]) != "find":
        return None
    for index, arg in enumerate(argv):
        if arg in ("-exec", "-execdir", "-ok", "-okdir"):
            for end in range(index + 1, len(argv)):
                if argv[end] in (";", "+"):
                    return argv[index + 1 : end], argv[end]
            return argv[index + 1 :], ""
    return None


def _required(command: Simple) -> Optional[str]:
    """The kind of required command *command* runs, or None."""
    argv = _argv(command)
    found = _find_exec(argv)
    return _required_argv(found[0] if found else argv)


def _recognised(command: Simple) -> bool:
    """Whether *command* runs a required program this guard models, if only for its version (`pytest -V`)."""
    argv = _argv(command)
    found = _find_exec(argv)
    return _required_kind(found[0] if found else argv)[0] is not None


def _names_only(argv: List[str]) -> bool:
    """Whether *argv* installs a package or byte-compiles a file, so naming `pytest` or a guard in it runs nothing."""
    program = posixpath.basename(argv[0])
    if _PYTHON_RE.fullmatch(program):
        kind, target = _python_target(argv[1:])
        return kind == "module" and target in _MENTION_OK_MODULES
    if program == "uv":
        return argv[1:2] in (["pip"], ["add"], ["tool"])
    return program == "poetry" and argv[1:2] in (["add"], ["install"])


def _mentioned(argv: List[str]) -> Optional[str]:
    """A required command *argv* seems to run although this guard does not model how.

    Any argument that is `pytest`, `ruff` or `mypy`, `node --test`, a ``tools/``
    guard path or ``-m <module>``: an unknown wrapper may put it anywhere
    (``ionice -c 3 pytest``). Not ``forgelm``, which is also this repository's
    package directory. Commands in ``_MENTION_OK`` never run their arguments.
    """
    for index, word in enumerate(argv[1:], start=1):
        program = posixpath.basename(word)
        if program in _REQUIRED_PROGRAMS and program != "forgelm":
            return program
        if program == "node" and "--test" in argv[index + 1 :]:
            return "node --test"
        guard = _GUARD_SCRIPT_RE.search(word)
        if guard:
            return "tools/" + guard.group(1)
        if word == "-m" and index + 1 < len(argv) and _gate_module(argv[index + 1]):
            return argv[index + 1]
    return None


def _scanner(command: Simple) -> Optional[str]:
    argv = _argv(command)
    if not argv:
        return None
    program = posixpath.basename(argv[0])
    if program in _SCANNER_HELPERS:
        return program
    if _PYTHON_RE.fullmatch(program):
        kind, target = _python_target(argv[1:])
        if kind == "module" and target in ("bandit", "pip_audit"):
            return target.replace("_", "-")
    return None


def _report_path(command: Simple) -> Optional[str]:
    """Where a scanner writes its report: ``-o``/``--output`` or a stdout redirection."""
    args = _argv(command)[1:]
    for index, arg in enumerate(args):
        if arg in ("-o", "--output") and index + 1 < len(args):
            return args[index + 1]
        if arg.startswith("--output="):
            return arg.split("=", 1)[1]
        if arg.startswith("-o") and len(arg) > 2 and not arg.startswith("--"):
            return arg[2:]
    for redirect in command.redirects:
        if redirect.op in (">", ">|") and redirect.fd in (None, "1"):
            return redirect.target.text
    return None


def _helper_args(command: Simple) -> List[str]:
    """The arguments a severity helper receives: its report must be one of them, whatever its options are."""
    argv = _argv(command)
    script = argv[0]
    if _PYTHON_RE.fullmatch(posixpath.basename(argv[0])):
        script = _python_target(argv[1:])[1]
    return argv[argv.index(script) + 1 :] if script in argv else []


def _apply_set(options: ShellOptions, args: List[str]) -> ShellOptions:
    """Fold ``set``-style flags (``-e``, ``+e``, ``-o pipefail``, ``-euo pipefail``) into *options*."""
    errexit, pipefail = options.errexit, options.pipefail
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in ("--", "-"):
            break  # the remaining words are positional parameters
        if "$" in arg:
            errexit = pipefail = False  # `set $OPTS`: assume the worst
            index += 1
            continue
        if arg[:1] in ("-", "+") and not arg.startswith("--") and len(arg) > 1:
            enable = arg[0] == "-"
            for letter in arg[1:]:
                if letter == "e":
                    errexit = enable
                elif letter == "o" and index + 1 < len(args):
                    index += 1
                    if args[index] == "errexit":
                        errexit = enable
                    elif args[index] == "pipefail":
                        pipefail = enable
        index += 1
    return replace(options, errexit=errexit, pipefail=pipefail)


def _option_args(command: Any) -> Optional[List[str]]:
    """``set``-style arguments when *command* changes shell options, else None.

    Covers ``set …`` (also through ``command`` / ``builtin``) and ``shopt -s|-u -o …``.
    """
    if not isinstance(command, Simple):
        return None
    argv = _argv(command)
    if argv[:1] == ["set"]:
        return argv[1:]
    flags = "".join(arg[1:] for arg in argv[1:] if arg.startswith("-")) if argv[:1] == ["shopt"] else ""
    if "o" in flags and ("s" in flags or "u" in flags):  # `shopt -u -o errexit`, `shopt -uo errexit`
        flag = "+o" if "u" in flags else "-o"
        return [part for name in argv[1:] if not name.startswith("-") for part in (flag, name)]
    return None


def _noexec(args: List[str]) -> bool:
    """Whether ``set``-style *args* turn noexec on (``-n``, ``-o noexec``), after which no command runs."""
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in ("--", "-"):
            return False
        if arg.startswith("-") and not arg.startswith("--") and "$" not in arg:
            for letter in arg[1:]:
                if letter == "n":
                    return True
                if letter == "o" and index + 1 < len(args):
                    index += 1
                    if args[index] == "noexec":
                        return True
        index += 1
    return False


def _set_args(item: Item) -> Optional[List[str]]:
    """The option arguments of an item that only changes shell options, else None.

    A backgrounded ``set`` runs in a subshell and changes nothing here, so it is not
    one; ``weakened`` still counts an option it turns off, to stay on the safe side.
    """
    pipelines = item.and_or.pipelines
    if item.background or len(pipelines) != 1 or len(pipelines[0].commands) != 1:
        return None
    return _option_args(pipelines[0].commands[0])


def _captured_status(item: Item) -> Optional[str]:
    """``rc`` when *item* is exactly ``rc=$?``, capturing the status of the command before it."""
    pipelines = item.and_or.pipelines
    if len(pipelines) != 1 or len(pipelines[0].commands) != 1:
        return None
    command = pipelines[0].commands[0]
    if isinstance(command, Simple) and not command.words and len(command.assignments) == 1:
        match = _STATUS_CAPTURE_RE.fullmatch(command.assignments[0].text)
        return match.group(1) if match else None
    return None


# Commands that set the variables named in their arguments: `read rc`, `local rc=0`, `printf -v rc …`, `getopts o rc`.
_VARIABLE_SETTERS = ("declare", "local", "export", "typeset", "readonly", "read", "unset", "let", "printf", "mapfile",
                     "readarray", "getopts", "wait")  # fmt: skip

# The script a command runs in this shell (``eval``, ``source``), for code that must look inside it.
Nested = Callable[[Simple], Optional[Script]]


def _no_nested(command: Simple) -> Optional[Script]:
    return None


def _assigned(nodes: List[Any], name: str, nested: Nested = _no_nested) -> bool:
    """Whether a command in *nodes* may change the variable *name*: ``rc=0``, ``read rc``, ``((rc++))``, ``for rc``,
    a nameref to it (``declare -n ref=rc``), also in a script *nested* runs in this shell (``eval rc=0``)."""
    arithmetic = re.compile(
        r"(?<![A-Za-z0-9_$])" + re.escape(name) + r" {0,8}(?:[-+*/%&|^]?=(?!=)|\+\+|--)|(?:\+\+|--)" + re.escape(name)
    )
    stack = list(nodes)
    seen: set = set()
    while stack:
        node = stack.pop()
        if isinstance(node, Simple):
            argv = _argv(node)  # `command read rc` and `builtin read rc` too
            names = [word.text.split("=", 1)[0].rstrip("+") for word in node.assignments]
            if argv[:1] and argv[0] in _VARIABLE_SETTERS:
                names += [arg.split("=", 1)[0].strip().rstrip("+") for arg in argv[1:]]  # `let "rc = 0"` too
                if any(arg.startswith("-") and "n" in arg for arg in argv[1:]):  # `declare -n ref=rc`
                    names += [arg.split("=", 1)[1] for arg in argv[1:] if "=" in arg]
            if name in (assigned.split("[", 1)[0] for assigned in names):
                return True
            if any(arithmetic.search(word.raw) for word in node.words if argv[:1] == ["let"] or "((" in word.raw):
                return True
            script = nested(node)
            if script is not None and id(script) not in seen:
                seen.add(id(script))
                stack.append(script)
        elif isinstance(node, Loop) and node.variable is not None and node.variable.text == name:
            return True
        elif isinstance(node, Expression) and any(arithmetic.search(word.raw) for word in node.words):
            return True
        stack.extend(children(node))
    return False


def _kept_capture(items: List[Item], nested: Nested = _no_nested) -> Optional[str]:
    """``rc`` when *items* start with ``rc=$?`` and nothing after it changes ``rc``."""
    captured = _captured_status(items[0]) if items else None
    return None if captured is None or _assigned(list(items[1:]), captured, nested) else captured


def _number(text: str) -> Optional[int]:
    """*text* as a number when it is one in bash: ASCII digits only (``exit ²`` is no exit code)."""
    return int(text) if text.isascii() and text.isdigit() else None


def _exit_kind(command: Simple, failed_status: bool, captured: Optional[str]) -> str:
    """``exits`` (leaves non-zero), ``fails`` (``false``) or ``succeeds`` (may leave 0) for one command.

    A bare ``exit`` / ``exit $?`` re-raises only when ``$?`` still holds the failed
    command's status (*failed_status*): right after ``||``. Inside a group it holds
    the previous command's status, so only a literal code or a status captured
    first (``rc=$?``, then ``exit "$rc"``) re-raises there.
    """
    argv = [word.text for word in command.words]
    if argv[:1] == ["exit"]:
        code = argv[1] if len(argv) > 1 else "$?"
        if code in ("$?", "${?}"):
            return "exits" if failed_status else "succeeds"
        number = _number(code)
        if number is not None:
            return "exits" if number % 256 else "succeeds"
        return "exits" if captured and code in ("$" + captured, "${" + captured + "}") else "succeeds"
    return "fails" if argv in (["false"], ["/bin/false"], ["/usr/bin/false"]) else "succeeds"


def _variable_only(text: str) -> bool:
    """Whether *text* is one parameter expansion (``$CMD``, ``${CMD}``), whose value this guard cannot see."""
    name = text.strip()
    if name.startswith("${") and name.endswith("}"):
        name = name[2:-1]
    elif name.startswith("$"):
        name = name[1:]
    else:
        return False
    return name.isidentifier() or _number(name) is not None or name in ("@", "*")


def _redefined(command: Simple) -> Tuple[List[str], str]:
    """The command names an ``alias`` or ``hash -p`` command redefines, and how."""
    argv = _argv(command)
    if argv[:1] == ["alias"]:
        return [arg.split("=", 1)[0] for arg in argv[1:] if "=" in arg], "an alias"
    if argv[:1] == ["hash"] and "-p" in argv:
        return argv[argv.index("-p") + 2 :], "a hashed path"
    return [], ""


def _constant_status(pipeline: Pipeline) -> Optional[bool]:
    """True when *pipeline* always succeeds (``x=1``, ``true``, ``:``), False when it always fails (``false``)."""
    command = pipeline.commands[0] if len(pipeline.commands) == 1 else None
    if not isinstance(command, Simple) or command.redirects:
        return None
    argv = [word.text for word in command.words]
    if not argv and not any(word.subs for word in command.assignments):
        status = True
    elif argv in (["true"], [":"], ["false"]):
        status = argv != ["false"]
    else:
        return None
    return status != pipeline.negated


def _trap_label(kind: str) -> str:
    return f"a {kind} trap" if kind in ("DEBUG", "RETURN") else "an EXIT/ERR trap"


def _reraising_exits(
    action: Script, entry_status: bool, bare_is_status: bool = False, nested: Nested = _no_nested
) -> set:
    """Ids of the ``exit`` commands in a trap action that leave with the status the trap was entered with.

    ``$?`` holds that status only in the action's first command (when
    *entry_status*); after ``||`` it holds the failed command's non-zero status;
    and ``rc=$?`` as the first command keeps it for a later ``exit "$rc"``, unless
    something assigns ``rc`` again. *bare_is_status* reads a bare ``exit`` as
    ``exit $?``, as bash does in an ERR trap.
    """
    found: set = set()
    if not action.items:
        return found
    status = ("$?", "${?}")
    captured = _kept_capture(action.items, nested) if entry_status else None
    if captured:
        status += ("$" + captured, "${" + captured + "}")
    first = action.items[0].and_or.pipelines[0].commands[0]
    stack: List[Any] = [action]
    while stack:
        node = stack.pop()
        if isinstance(node, Function) or (isinstance(node, Group) and node.subshell):
            continue
        if isinstance(node, Simple) and node.words and node.words[0].text == "exit":
            code = node.words[1].text if len(node.words) > 1 else ("$?" if bare_is_status else None)
            if code in status[2:] or (code in status[:2] and node is first and entry_status):
                found.add(id(node))
        if isinstance(node, AndOr):
            for position, operator in enumerate(node.operators):
                following = node.pipelines[position + 1].commands
                exit_words = following[0].words if len(following) == 1 and isinstance(following[0], Simple) else []
                texts = [word.text for word in exit_words] + (["$?"] if bare_is_status and len(exit_words) == 1 else [])
                if operator == "||" and texts[:1] == ["exit"] and len(texts) == 2 and texts[1] in status[:2]:
                    found.add(id(following[0]))
        stack.extend(children(node))
    return found


def _text(node: Any, limit: int = 90) -> str:
    """A short source-like rendering of a command, for messages."""
    if isinstance(node, Simple):
        text = " ".join([word.raw for word in node.assignments + node.words])
    elif isinstance(node, Pipeline):
        text = ("! " if node.negated else "") + " | ".join(_text(command, limit) for command in node.commands)
    elif isinstance(node, Group):
        text = "( … )" if node.subshell else "{ … }"
    elif isinstance(node, Function):
        text = node.name + "() { … }"
    elif isinstance(node, Loop):
        text = f"{node.kind} …; done"
    elif isinstance(node, If):
        text = "if …; fi"
    elif isinstance(node, Case):
        text = "case …; esac"
    elif isinstance(node, Expression):
        text = "[[ … ]]"
    else:
        text = type(node).__name__.strip("_").lower()
    text = text.replace("GITHUB_EXPRESSION", "${{ … }}")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _downloads(command: Any) -> bool:
    """Whether *command* is ``curl`` or ``wget``, the first half of an installer downloaded at run time."""
    program = _argv(command)[:1] if isinstance(command, Simple) else []
    return bool(program) and posixpath.basename(program[0]) in ("curl", "wget")


def _downloaded_operand(command: Simple, operand: str) -> bool:
    """Whether the script operand *operand* is a process substitution of ``curl`` / ``wget`` (``bash <(curl …)``)."""
    for word in command.words:
        if word.text == operand and word.raw.startswith("<(") and word.subs and word.subs[0].items:
            return _downloads(word.subs[0].items[0].and_or.pipelines[0].commands[0])
    return False


def _repository_path(path: str) -> bool:
    """Whether *path* names a file of the repository: relative, under ``$GITHUB_WORKSPACE``, or taken from the
    working directory or the running script's own (``"$PWD/ci/x.sh"``, ``"$(dirname "$0")/lib.sh"``)."""
    if path.startswith(_WORKSPACE_PREFIXES + _CWD_PREFIXES + _SCRIPT_DIR_PREFIXES):
        return True
    return not (posixpath.isabs(path) or path.startswith(("~", "$")))


_NOT_IN_REPOSITORY = (
    "which this guard cannot find in the repository; run a repository script by its path from the repository "
    "root, or set the step's `working-directory:`"
)


class _Analysis:
    """Walk one parsed script and record the ways a required command's failure can be lost.

    Several caches are keyed by ``id()``, which is unique only among live objects, so
    every tree the analysis parses stays referenced in ``_trees`` until it ends.
    """

    def __init__(self, where: str, root: Optional[Path], cwd: Optional[str] = ".", unit: str = "script") -> None:
        self.where = where
        self.root = root
        self.cwd = cwd  # the working directory relative to *root*; None when it is unknown
        self.unit = unit  # what a finding's line number counts in
        self.findings: List[str] = []
        self.script: Optional[Script] = None
        self._trees: List[Script] = []
        self._files: Dict[Path, Union[Script, str]] = {}  # repository scripts, parsed once (or the parse error)
        self._nested: Dict[int, Optional[Tuple[Script, Optional[ShellOptions], bool]]] = {}
        self._has: Dict[int, bool] = {}
        self._downloaded: set = set()  # shells reading an installer piped from curl / wget
        self._origin: Dict[int, str] = {}  # node id -> the repository script it was read from
        self._skipped: Dict[int, str] = {}  # `&&` list id -> the command whose failure skips its required one
        self._includers: Dict[
            str, Optional[str]
        ] = {}  # repository script -> the one that first ran it (None: the step)
        self._nullglob: Optional[bool] = None
        self._functions: Optional[Dict[str, Any]] = None

    def report(self, node: Any, reason: str) -> None:
        first = node.commands[0] if isinstance(node, Pipeline) else node
        self.findings.append(f"{self.where}{self.at(first)}: `{_text(node)}` {reason}")

    def at(self, node: Any) -> str:
        """`` (<script or file> line N)`` for *node*, or nothing when it has no line."""
        line = getattr(node, "line", None)
        return f" ({self._origin.get(id(node), self.unit)} line {line})" if line else ""

    def parse(self, text: str, line: int) -> Script:
        """Parse *text* and keep its tree alive for the analysis, so its ids stay unique."""
        script = parse(text, line)
        self._trees.append(script)
        return script

    def _label(self, script: Script, path: str) -> None:
        """Record that every command of *script* comes from the repository file *path*."""
        stack: List[Any] = [script]
        while stack:
            node = stack.pop()
            self._origin[id(node)] = path
            for word in own_words(node):
                stack.extend(word.subs)
            stack.extend(children(node))

    # -- what a node contains --------------------------------------------

    def nested(self, command: Simple) -> Optional[Tuple[Script, Optional[ShellOptions], bool]]:
        """The script a command runs: ``(script, options, current_shell)``.

        ``bash -c '…'``, ``sh <<'EOF'`` and ``bash ci/x.sh`` (read from the
        repository) run in a new shell with their own options; ``eval`` runs in
        the current shell, so its options are the caller's (None).
        """
        if id(command) not in self._nested:
            self._nested[id(command)] = self._nested_script(command)
        return self._nested[id(command)]

    def _nested_script(self, command: Simple) -> Optional[Tuple[Script, Optional[ShellOptions], bool]]:
        argv = _argv(command)
        found = _find_exec(argv)
        argv = found[0] if found else argv
        if not argv:
            return None
        program = posixpath.basename(argv[0])
        if program == "eval":
            return self._parse_nested(command, " ".join(argv[1:]), command.line, None, True)
        if program in ("source", ".") and len(argv) > 1:
            if argv[1] in _STDIN_PATHS:
                return self._stdin_script(command, None, current_shell=True)
            # Runs in this shell. A file outside the repository (`.venv/bin/activate`) is not ours to check.
            return self._script_file(command, argv[1], None, current_shell=True, missing_ok=True)
        if program not in _SHELLS:
            return self._executable(command, argv[0]) if "/" in argv[0] else None
        options, reads_command, reads_stdin, index = INTERACTIVE, False, False, 1
        while index < len(argv):
            arg = argv[index]
            if arg in ("--", "-"):  # bash reads a lone `-` like `--`: the options end
                index += 1
                break
            if arg in ("--version", "--help"):
                return None
            if arg.startswith("--"):
                index += 1
                continue
            if len(arg) < 2 or arg[0] not in "-+":
                break
            for letter in arg[1:]:
                if letter == "c":
                    reads_command = True
                elif letter == "s":
                    reads_stdin = True  # the operands are positional parameters
                elif letter == "n" and arg[0] == "-":
                    return None  # noexec: the shell only parses the script
                elif letter == "e":
                    options = replace(options, errexit=arg[0] == "-")
                elif letter in "oO":
                    index += 1  # the option name: `-o pipefail`, `-eo pipefail`, `+O shopt`
                    if letter == "o" and index < len(argv):
                        options = _apply_set(options, [arg[0] + "o", argv[index]])
            index += 1
        operands = argv[index:]
        if reads_command:
            return self._parse_nested(command, operands[0] if operands else "", command.line, options, False)
        if operands and not reads_stdin:
            if _downloaded_operand(command, operands[0]):
                return None  # `bash <(curl …)`: an installer downloaded while the step runs
            return self._script_file(command, operands[0], options)
        return self._stdin_script(command, options, current_shell=False)

    def _stdin_script(
        self, command: Simple, options: Optional[ShellOptions], current_shell: bool
    ) -> Optional[Tuple[Script, Optional[ShellOptions], bool]]:
        """The script a shell reads from standard input: a here-document or here-string it is given."""
        for redirect in command.redirects:
            if redirect.heredoc is not None:
                return self._parse_nested(command, redirect.heredoc, command.line + 1, options, current_shell)
            if redirect.op == "<<<":
                return self._parse_nested(command, redirect.target.text, command.line, options, current_shell)
        if id(command) in self._downloaded:
            return None  # an installer piped from curl / wget runs nothing of this repository
        self.report(command, "reads its script from standard input, which this guard cannot inspect; use `-c`")
        return None

    def _repository_file(self, path: str) -> Optional[Tuple[Path, str]]:
        """The resolved file and text of *path* when it is a readable file inside the scanned repository.

        A path under ``$GITHUB_WORKSPACE`` starts at the repository root, a relative
        one or one under ``$PWD`` at the step's working directory.
        """
        if self.root is None:
            return None
        if path.startswith(_WORKSPACE_PREFIXES):
            base, path = self.root, path.split("/", 1)[1]
        elif self.cwd is not None and path.startswith(_CWD_PREFIXES):
            base, path = self.root / self.cwd, path.split("/", 1)[1]
        elif self.cwd is None or posixpath.isabs(path) or path.startswith("$"):
            return None
        else:
            base = self.root / self.cwd
        try:
            file = (base / path).resolve()
            if not (file.is_relative_to(self.root.resolve()) and file.is_file()):
                return None
            return file, file.read_text(encoding="utf-8")
        except (OSError, RuntimeError, ValueError):
            return None  # a name too long, a NUL byte, a symlink loop, text that is not UTF-8

    def _script_file(
        self,
        command: Simple,
        path: str,
        options: Optional[ShellOptions],
        current_shell: bool = False,
        missing_ok: bool = False,
    ) -> Optional[Tuple[Script, Optional[ShellOptions], bool]]:
        """A script a shell runs (``bash ci/x.sh``) or sources (*missing_ok*: ``source .venv/bin/activate``)."""
        found = self._repository_file(path)
        if found is not None:
            if self._runs_itself(command, path, found[0]):
                return None
            return self._labelled(self._parse_file(command, found, options, current_shell), found[0])
        if not missing_ok or (path.endswith(_SCRIPT_SUFFIXES) and _repository_path(path)):
            self.report(command, f"runs {path}, {_NOT_IN_REPOSITORY}")
        return None

    def _runs_itself(self, command: Simple, path: str, file: Path) -> bool:
        """Report *command* when the script it runs is already running it (a script that sources itself)."""
        shown = _shown(file, self.root.resolve()) if self.root is not None else file.as_posix()
        running = self._origin.get(id(command))
        chain: set = set()
        while running is not None and running not in chain:
            if running == shown:
                self.report(command, f"runs {path} again from inside it, a loop this guard cannot follow")
                return True
            chain.add(running)
            running = self._includers.get(running)
        self._includers.setdefault(shown, self._origin.get(id(command)))
        return False

    def _executable(self, command: Simple, path: str) -> Optional[Tuple[Script, Optional[ShellOptions], bool]]:
        """A repository shell script run by its path (``./ci/test.sh``), with its shebang's options."""
        found = self._repository_file(path)
        if found is None:
            if path.endswith(_SCRIPT_SUFFIXES) and _repository_path(path):
                self.report(command, f"runs {path}, {_NOT_IN_REPOSITORY}")
            return None
        if self._runs_itself(command, path, found[0]):
            return None
        text = found[1]
        words = text.split("\n", 1)[0][2:].split() if text.startswith("#!") else ["sh"]
        if words[:1] and posixpath.basename(words[0]) == "env":
            words = _env_argv(words[1:])  # `#!/usr/bin/env bash`, `#!/usr/bin/env -S bash -e`
        if not words or posixpath.basename(words[0]) not in _SHELLS:
            return None  # a Python or other interpreter: nothing this guard can follow
        return self._labelled(self._parse_file(command, found, _apply_set(INTERACTIVE, words[1:]), False), found[0])

    def _parse_file(
        self, command: Simple, found: Tuple[Path, str], options: Optional[ShellOptions], current_shell: bool
    ) -> Optional[Tuple[Script, Optional[ShellOptions], bool]]:
        """A repository script's tree, parsed once however often the step runs or sources it."""
        file, text = found
        if file not in self._files:
            try:
                self._files[file] = self.parse(text, 1)
                self.mark_downloads(self._files[file])
            except ShellParseError as exc:
                self._files[file] = str(exc)
        tree = self._files[file]
        if isinstance(tree, str):
            self.report(command, f"runs a nested shell script this guard cannot parse ({tree})")
            return None
        return tree, options, current_shell

    def _labelled(
        self, nested: Optional[Tuple[Script, Optional[ShellOptions], bool]], file: Path
    ) -> Optional[Tuple[Script, Optional[ShellOptions], bool]]:
        """*nested*, its commands labelled with *file*'s path in the repository (``ci/x.sh``)."""
        if nested is not None and self.root is not None:
            self._label(nested[0], _shown(file, self.root.resolve()))
        return nested

    def mark_downloads(self, node: Any) -> None:
        """Remember shells fed by ``curl`` / ``wget``: a downloaded installer is no script of this repository."""
        stack = [node]
        while stack:
            current = stack.pop()
            if isinstance(current, Pipeline):
                for left, right in zip(current.commands, current.commands[1:]):
                    if _downloads(left):
                        self._downloaded.add(id(right))
            for word in own_words(current):
                stack.extend(word.subs)
            stack.extend(children(current))

    def _parse_nested(
        self, command: Simple, text: str, line: int, options: Optional[ShellOptions], current_shell: bool
    ) -> Optional[Tuple[Script, Optional[ShellOptions], bool]]:
        if _variable_only(text):
            self.report(
                command, f"runs a script taken from the variable `{text.strip()}`, which this guard cannot check"
            )
            return None
        try:
            script = self.parse(text, line)
            self.mark_downloads(script)
            return script, options, current_shell
        except ShellParseError as exc:
            self.report(command, f"runs a nested shell script this guard cannot parse ({exc})")
            return None

    def has_required(self, node: Any) -> bool:
        if id(node) not in self._has:
            if isinstance(node, Simple):
                nested = self.nested(node)
                found = bool(_required(node)) or (nested is not None and self.has_required(nested[0]))
            else:
                found = any(self.has_required(child) for child in children(node))
            self._has[id(node)] = found
        return self._has[id(node)]

    def first_required(self, node: Any) -> Any:
        if isinstance(node, Simple):
            return node
        for child in children(node):
            if self.has_required(child):
                return self.first_required(child)
        return node

    def walk(self, node: Any, seen: Optional[set] = None) -> Iterator[Any]:
        """Every node in *node*, command substitutions and nested scripts included; a shared script once."""
        seen = set() if seen is None else seen
        yield node
        for word in own_words(node):
            for sub in word.subs:
                yield from self.walk(sub, seen)
        if isinstance(node, Simple):
            nested = self.nested(node)
            if nested is not None and id(nested[0]) not in seen:
                seen.add(id(nested[0]))
                yield from self.walk(nested[0], seen)
        for child in children(node):
            yield from self.walk(child, seen)

    def literal_list(self, loop: Loop) -> bool:
        """Whether a `for` loop runs its body at least once: a fixed word list, with no variable or substitution.

        A glob that matches nothing stays a word, so the body still runs once, unless
        ``shopt -s nullglob`` (seen anywhere in the script) drops it.
        """
        if not loop.words or any(mark in word.raw for word in loop.words for mark in "$`"):
            return False
        return not (any(mark in word.raw for word in loop.words for mark in "*?[") and self.nullglob())

    def skips_iterations(self, body: Script) -> bool:
        """Whether a loop body can ``continue`` or ``break`` before its first required command runs."""
        for item in body.items:
            pipelines = item.and_or.pipelines
            first = next((index for index, pipeline in enumerate(pipelines) if self.has_required(pipeline)), None)
            if any(self.breaks_out(pipeline) for pipeline in pipelines[:first]):
                return True
            if first is not None:
                group = pipelines[first].commands[0]
                return isinstance(group, Group) and not group.subshell and self.skips_iterations(group.body)
        return False

    def breaks_out(self, node: Any, levels: int = 1, seen: frozenset = frozenset()) -> bool:
        """Whether *node* can run a ``continue`` or ``break`` that leaves the loop *levels* out, also in a
        function it calls or a script it runs in this shell (a nested loop's own ``continue`` does not)."""
        if isinstance(node, Simple):
            words = [word.text for word in node.words]
            if words[:1] in (["continue"], ["break"]):
                count = _number(words[1]) if len(words) > 1 else 1
                return count is None or count >= levels
            body = self.functions().get(words[0]) if words else None
            if body is not None and words[0] not in seen:
                return self.breaks_out(body, levels, seen | {words[0]})
            script = self._current_shell_script(node)
            return (
                script is not None and id(script) not in seen and self.breaks_out(script, levels, seen | {id(script)})
            )
        if isinstance(node, Function) or (isinstance(node, Group) and node.subshell):
            return False
        inner = levels + 1 if isinstance(node, Loop) else levels
        return any(self.breaks_out(child, inner, seen) for child in children(node))

    def functions(self) -> Dict[str, Any]:
        """The shell functions the script defines, by name: the body of the last definition of each."""
        if self._functions is None:
            nodes = self.walk(self.script) if self.script else iter(())
            self._functions = {node.name: node.body for node in nodes if isinstance(node, Function)}
        return self._functions

    def _current_shell_script(self, command: Simple) -> Optional[Script]:
        """The script *command* runs in this shell (``eval``, ``source``), if any."""
        nested = self.nested(command)
        return nested[0] if nested is not None and nested[2] else None

    def may_leave(self, node: Any, captured: Optional[str] = None, failing_exits: bool = False) -> bool:
        """Whether *node* can leave its block early: ``exit``, ``exec``, ``return``, ``break``, ``continue``,
        ``set -n``, or a call to a shell function, which may run any of them.

        With *failing_exits*, an ``exit`` that always leaves non-zero (``exit 1``, or
        ``exit "$rc"`` after ``rc=$?`` as *captured*) is no early way out.
        """
        for command in self.option_commands(node):
            if not isinstance(command, Simple) or not command.words:
                continue
            words = [word.text for word in command.words]
            if words[0] == "exit" and failing_exits and _exit_kind(command, False, captured) == "exits":
                continue
            if words[0] in ("exit", "return", "break", "continue") or (words[0] == "exec" and len(words) > 1):
                return True
            args = _option_args(command)
            if (args is not None and _noexec(args)) or words[0] in self.functions():
                return True
        return False

    def ends(self, item: Item, seen: frozenset = frozenset()) -> Optional[Simple]:
        """The command that always stops the script at *item* without failing it: an ``exit`` that can
        leave 0, an ``exec`` that replaces the shell, or ``set -n``.

        It counts wherever bash always reaches it: behind ``!``, after ``&&`` behind a
        command that always succeeds (``x=1 && exit``), after ``||`` behind ``false``, in
        a ``{ … }`` group, in a function the item calls, or in a script ``eval`` or
        ``source`` runs in this shell.
        """
        if item.background:
            return None
        and_or = item.and_or
        status: Optional[bool] = True
        for position, pipeline in enumerate(and_or.pipelines):
            after_failure = False
            if position:
                if status is None:
                    return None  # whether bash runs the rest depends on what the commands find
                operator = and_or.operators[position - 1]
                if status != (operator == "&&"):
                    continue  # bash skips it, and the list keeps its status
                after_failure = operator == "||"
            ended = self._ends_pipeline(pipeline, after_failure, seen)
            if ended is not None:
                return ended
            status = _constant_status(pipeline)
        return None

    def _ends_pipeline(self, pipeline: Pipeline, after_failure: bool, seen: frozenset) -> Optional[Simple]:
        if len(pipeline.commands) != 1:
            return None  # each command of a longer pipeline runs in a subshell
        command = pipeline.commands[0]
        if isinstance(command, Group) and not command.subshell:
            return next((ended for inner in command.body.items if (ended := self.ends(inner, seen))), None)
        if not isinstance(command, Simple) or not command.words:
            return None
        args = _option_args(command)
        program = command.words[0].text
        if args is not None and _noexec(args):
            return command
        if program == "exit":
            return command if _exit_kind(command, failed_status=after_failure, captured=None) == "succeeds" else None
        if program == "exec":
            return command if len(command.words) > 1 else None
        body = self.functions().get(program)
        if body is not None and program not in seen:
            call = Item(AndOr([Pipeline([body], False)], []), False)
            return command if self.ends(call, seen | {program}) else None
        script = self._current_shell_script(command)
        if script is not None and id(script) not in seen:
            return command if any(self.ends(inner, seen | {id(script)}) for inner in script.items) else None
        return None

    def nullglob(self) -> bool:
        """Whether the script may turn on ``nullglob``, under which a glob matching nothing disappears."""
        if self._nullglob is None:
            self._nullglob = self.script is not None and any(
                isinstance(node, Simple)
                and _argv(node)[:1] == ["shopt"]
                and "nullglob" in _argv(node)
                and any(arg.startswith("-") and "s" in arg for arg in _argv(node)[1:])
                for node in self.walk(self.script)
            )
        return self._nullglob

    def exits_in(self, node: Any) -> Iterator[Simple]:
        """The ``exit`` commands that would end the shell running *node*."""
        if isinstance(node, Simple):
            if node.words and node.words[0].text == "exit":
                yield node
        elif not isinstance(node, Function) and not (isinstance(node, Group) and node.subshell):
            for child in children(node):
                yield from self.exits_in(child)

    # -- does a failure reach the exit status? ---------------------------

    def scope(self, script: Script, options: ShellOptions, tail_counts: bool, tail_reason: str) -> Optional[str]:
        """Check a command list; return STATUS when its exit status carries a failure, else TERMINATES.

        Returns None when the list runs no required command, or when a problem
        inside it was already reported (so enclosing levels do not repeat it).
        """
        outcome: Optional[str] = None
        reported = len(self.findings)
        ended: Optional[Simple] = None
        for index, item in enumerate(script.items):
            last = index == len(script.items) - 1
            if ended is not None:
                if self.has_required(item.and_or):
                    self.report(
                        self.first_required(item.and_or),
                        f"never runs: `{_text(ended)}`{self.at(ended)} stops the script before it",
                    )
                continue
            ended = self.ends(item)
            set_args = _set_args(item)
            if set_args is not None:
                options = _apply_set(options, set_args)
                continue
            options = self.weakened(item, options)  # a `set +e` in the item may run before its command
            result = self.and_or(item.and_or, options)
            if result is None:
                continue
            culprit = self.first_required(item.and_or)
            if item.background:
                self.report(culprit, "runs in the background (`&`); run required commands in the foreground")
            elif result == STATUS and not (last and tail_counts):
                skipped_by = self._skipped.get(id(item.and_or))
                if last:
                    reason = tail_reason
                elif skipped_by:
                    reason = (
                        f"is skipped when `{skipped_by}` fails, and errexit ignores a failure before `&&`, so the "
                        "script goes on and a later command decides the exit status"
                    )
                else:
                    reason = self.lost_reason(options)
                self.report(culprit, reason)
            elif result == STATUS:
                outcome = STATUS
            else:
                outcome = outcome or TERMINATES
        return None if len(self.findings) > reported else outcome

    def lost_reason(self, options: ShellOptions) -> str:
        if options.stops:
            return (
                "can fail without stopping the script, because errexit ignores a failure inside an `&&` / `||` "
                "list or a `{ … }` group, while a later command decides the exit status"
            )
        return (
            "can fail without stopping the script: errexit is off or ignored here, so a later command "
            "decides the exit status"
        )

    def weakened(self, item: Item, options: ShellOptions) -> ShellOptions:
        """*options* with any ``set +e`` / ``set +o pipefail`` inside *item* applied, also through ``eval`` / ``source``."""
        for command in self.option_commands(item):
            args = _option_args(command)
            if args is not None:
                turned = _apply_set(ShellOptions(True, True), args)
                options = replace(
                    options, errexit=options.errexit and turned.errexit, pipefail=options.pipefail and turned.pipefail
                )
        return options

    def option_commands(self, node: Any) -> Iterator[Any]:
        """The commands of *node* that run in this shell, including those of ``eval`` and ``source``."""
        for command in self.commands_in(node):
            yield command
            nested = self.nested(command) if isinstance(command, Simple) else None
            if nested is not None and nested[2]:
                yield from self.option_commands(nested[0])

    def commands_in(self, node: Any) -> Iterator[Any]:
        for child in children(node):
            if isinstance(child, Group) and child.subshell:
                continue
            if not isinstance(child, (Script, Item, AndOr, Pipeline)):
                yield child
            yield from self.commands_in(child)

    def and_or(self, and_or: AndOr, options: ShellOptions) -> Optional[str]:
        count = len(and_or.pipelines)
        result: Optional[str] = None
        reported = len(self.findings)
        own_status, skipped_by = False, ""
        for position, pipeline in enumerate(and_or.pipelines):
            if not self.has_required(pipeline):
                continue
            # bash ignores errexit for every command of the list but the last, and inside compounds placed there.
            inner = replace(options, ignored=options.ignored or position != count - 1)
            outcome = self.pipeline(pipeline, inner)
            result = result or TERMINATES
            if outcome is None:
                continue
            culprit = self.first_required(pipeline)
            if position and and_or.operators[position - 1] == "||":
                self.report(culprit, "runs only when the command before `||` fails, so it is usually skipped")
                continue
            # Its own failure (unless that already ends the shell), and a predecessor's
            # failure that skips it: either must still fail the list.
            paths = [] if outcome == TERMINATES else [(self.after_failure(and_or, position), "")]
            if position:
                skipped = "is skipped when the command before `&&` fails, and "
                paths.append((self.after_failure(and_or, position - 1), skipped))
            for (kind, fallback, fallback_ran), prefix in paths:
                if kind == "masked":
                    self.report(culprit, f"{prefix}is masked by `|| {fallback}`, which can exit 0")
                    break
                # errexit acts on the list only when its last command failed in its own right: a
                # failing fallback, or this command - unless it is a `{ … }` group returning a
                # status errexit already ignored inside it.
                acts = options.stops and (fallback_ran or outcome != RETURNS)
                if kind == "terminates" or (kind == "errexit" and acts):
                    continue
                result = STATUS
                if prefix:
                    skipped_by = skipped_by or _text(and_or.pipelines[position - 1], 60)
                else:
                    own_status = True
        if result == STATUS and skipped_by and not own_status:
            self._skipped[id(and_or)] = skipped_by  # `cd x && pytest`: the message names `cd x`
        return None if len(self.findings) > reported else result

    def after_failure(self, and_or: AndOr, failed: int) -> Tuple[str, str, bool]:
        """Follow the list after pipeline *failed* exits non-zero.

        Returns ``(kind, fallback, fallback_ran)``. *kind* is ``masked`` (a
        fallback that can exit 0 ran), ``terminates`` (an ``exit`` ended the
        shell), ``errexit`` (the list's last command is the one that failed) or
        ``silent`` (only the list's exit status records the failure).
        """
        last, ran = failed, False
        for index in range(failed, len(and_or.operators)):
            if and_or.operators[index] == "&&":
                continue
            fallback = and_or.pipelines[index + 1]
            kind = self.fallback(fallback)
            if kind == "succeeds":
                return "masked", _text(fallback), True
            if kind == "exits":
                return "terminates", "", True
            last, ran = index + 1, True
        return ("errexit" if last == len(and_or.pipelines) - 1 else "silent"), "", ran

    def fallback(self, pipeline: Pipeline) -> str:
        """``exits`` (ends the shell non-zero), ``fails`` (exits non-zero) or ``succeeds`` (may exit 0)."""
        if pipeline.negated or len(pipeline.commands) != 1:
            return "succeeds"
        command = pipeline.commands[0]
        if isinstance(command, Simple):
            return _exit_kind(command, failed_status=True, captured=None)
        if not isinstance(command, Group) or not command.body.items:
            return "succeeds"
        items = command.body.items
        captured = _kept_capture(items, self._current_shell_script)
        last = items[-1].and_or
        tail = last.pipelines[-1]
        # Whatever can leave the group before its last command (`exit 0`, `eval 'exit 0'`, a function call,
        # `return`) may end it with 0; an `exit` that always leaves non-zero still fails the step.
        for node in items[:-1] + last.pipelines[:-1]:
            if self.may_leave(node, captured, failing_exits=True):
                return "succeeds"
        if any(operator != "&&" for operator in last.operators):
            return "succeeds"
        if tail.negated or len(tail.commands) != 1 or not isinstance(tail.commands[0], Simple):
            return "succeeds"
        alone = len(items) == 1 and len(last.pipelines) == 1
        kind = _exit_kind(tail.commands[0], failed_status=alone, captured=captured)
        if kind == "exits" and not command.subshell and len(last.pipelines) == 1:
            return "exits"
        return "succeeds" if kind == "succeeds" else "fails"

    def pipeline(self, pipeline: Pipeline, options: ShellOptions) -> Optional[str]:
        culprit = self.first_required(pipeline)
        if pipeline.negated:
            self.report(culprit, "is negated with `!`, so its failure counts as success")
            return None
        outcomes: List[str] = []
        for position, command in enumerate(pipeline.commands):
            if not self.has_required(command):
                continue
            if position < len(pipeline.commands) - 1 and not options.pipefail:
                self.report(
                    command, "feeds a pipe without `pipefail`, so the pipeline reports the last command's status"
                )
                return None
            outcome = self.command(command, options)
            if outcome is None:
                return None
            # Each element of a multi-command pipeline runs in a subshell: an exit ends only that
            # subshell, and errexit sees the pipeline's own status.
            outcomes.append(STATUS if len(pipeline.commands) > 1 else outcome)
        return next((kind for kind in (STATUS, RETURNS) if kind in outcomes), TERMINATES)

    def command(self, command: Command, options: ShellOptions) -> Optional[str]:
        if isinstance(command, Simple):
            found = _find_exec(_argv(command))
            if found and found[1] != "+":
                self.report(command, "runs under `find -exec … ;`, which ignores the command's exit status")
                return None
            if _required(command) == "ruff" and any(_RUFF_EXIT_ZERO_RE.fullmatch(arg) for arg in _argv(command)[1:]):
                flag = "--fix-only" if "--fix-only" in _argv(command) else "--exit-zero"
                self.report(command, f"is told to exit 0 whatever it finds (`{flag}`)")
                return None
            if _required(command):
                return STATUS
            nested = self.nested(command)
            if nested is None:
                return None
            script, nested_options, current_shell = nested
            outcome = self.scope(script, options if nested_options is None else nested_options, True, "")
            if outcome is None:
                return None
            # `eval` and a nested shell are simple commands: errexit acts on their status. Only an
            # exit inside `eval`, which runs in this shell, ends the step itself.
            return TERMINATES if current_shell and outcome == TERMINATES else STATUS
        if isinstance(command, Group):
            outcome = self.scope(command.body, options, True, "")
            if outcome is None:
                return None
            if command.subshell:
                return STATUS
            # A failure ignored inside a `{ … }` group leaves a status errexit does not act on.
            return TERMINATES if outcome == TERMINATES else RETURNS
        culprit = self.first_required(command)
        if isinstance(command, If):
            gate, why = self.gate(command, options)
            if gate is not None:
                return gate or None
            if any(self.has_required(condition) for condition in command.conditions):
                self.report(culprit, why)
            else:
                self.report(culprit, "runs on one branch of an `if` only; gate the whole step with `if:` instead")
        elif isinstance(command, Loop) and command.kind == "for" and self.skips_iterations(command.body):
            self.report(culprit, "runs inside a `for` loop whose `continue` or `break` can skip it on every pass")
        elif isinstance(command, Loop) and command.kind == "for" and not self.literal_list(command):
            self.report(culprit, "runs inside a `for` loop without a fixed word list, which may run it zero times")
        elif isinstance(command, Loop) and command.kind == "for":
            reason = (
                "is the last command of a `for` loop body and its failure does not stop the loop, so only "
                "the final iteration's status counts"
            )
            return TERMINATES if self.scope(command.body, options, False, reason) == TERMINATES else None
        elif isinstance(command, Loop):
            article = "an" if command.kind == "until" else "a"
            self.report(culprit, f"runs inside {article} `{command.kind}` loop, which may run it zero times")
        elif isinstance(command, Case):
            self.report(culprit, "runs on one `case` branch only")
        elif isinstance(command, Function):
            self.report(culprit, "is wrapped in a shell function; call required commands directly")
        return None

    def gate(self, command: If, options: ShellOptions) -> Tuple[Optional[str], str]:
        """``(TERMINATES, "")`` for ``if ! cmd; then …; exit 1; fi`` and ``if cmd; then …; else …; exit 1; fi``.

        The condition is one required command; the branch its failure takes ends the
        shell with a non-zero status (a literal, or in an ``else``, entered with the
        failure's status, ``exit``/``exit $?`` first or ``exit "$rc"`` after ``rc=$?``);
        no branch runs another required command. Returns ``("", "")`` when the command
        itself was reported, and for any other ``if`` None with the reason to report
        when its condition runs a required command.
        """
        why = (
            "is an `if`/`elif` condition, so the test consumes its exit status; end the branch its failure takes "
            "with `exit 1`, as in `if ! cmd; then …; exit 1; fi`"
        )
        if len(command.conditions) != 1:
            return None, why
        items = command.conditions[0].items
        if len(items) != 1 or items[0].background or len(items[0].and_or.pipelines) != 1:
            return None, why
        pipeline = items[0].and_or.pipelines[0]
        tested = pipeline.commands[0] if len(pipeline.commands) == 1 else None
        if not isinstance(tested, Simple) or not _required(tested):
            return None, why
        failing = 0 if pipeline.negated else 1  # `if ! cmd` fails into `then`, `if cmd` into `else`
        if any(self.has_required(body) for body in command.bodies):
            return None, "is an `if` condition whose branches run another required command; run that one on its own"
        if failing >= len(command.bodies):
            return None, why
        # An `exit 0` on the passing branch would end the step before the commands after the `if`.
        if any(self.may_leave(body) for index, body in enumerate(command.bodies) if index != failing):
            return None, (
                "is an `if` condition whose passing branch can leave the script early (`exit`, `return`, a function "
                "call), so the commands after the `if` may never run"
            )
        if not self.exits_failing(command.bodies[failing], status_is_failure=not pipeline.negated):
            return None, why
        return (TERMINATES if self.command(tested, options) is not None else ""), ""

    def exits_failing(self, body: Script, status_is_failure: bool) -> bool:
        """Whether *body* always ends the shell with a non-zero status (see ``gate``)."""
        if not body.items or any(self.may_leave(item) for item in body.items[:-1]):
            return False
        last = body.items[-1]
        tail = last.and_or.pipelines[0] if len(last.and_or.pipelines) == 1 else None
        if last.background or tail is None or tail.negated or len(tail.commands) != 1:
            return False
        exit_command = tail.commands[0]
        if not isinstance(exit_command, Simple) or [word.text for word in exit_command.words][:1] != ["exit"]:
            return False
        captured = _kept_capture(body.items, self._current_shell_script) if status_is_failure else None
        for item in body.items[:-1]:
            if any(_exit_kind(other, False, captured) == "succeeds" for other in self.exits_in(item)):
                return False
        alone = status_is_failure and len(body.items) == 1
        return _exit_kind(exit_command, failed_status=alone, captured=captured) == "exits"

    # -- `|| true` outside required commands, and the scanner exception ----

    def noop_fallbacks(self, script: Script, follower: Optional[Pipeline]) -> None:
        for index, item in enumerate(script.items):
            after = script.items[index + 1].and_or.pipelines[0] if index + 1 < len(script.items) else follower
            and_or = item.and_or
            for position, operator in enumerate(and_or.operators):
                fallback = and_or.pipelines[position + 1]
                if operator != "||" or not self.is_noop(fallback):
                    continue
                left = and_or.pipelines[: position + 1]
                if any(self.has_required(pipeline) for pipeline in left):
                    continue  # reported with the required command's own reason
                if len(and_or.pipelines) == 2 and self.scanner_exception(and_or, after):
                    continue
                self.report(
                    left[-1],
                    f"is followed by `|| {_text(fallback)}`, which discards its failure; the only sanctioned "
                    "`|| true` is a bandit/pip-audit scanner whose next command is its severity helper reading "
                    "the same report (docs/standards/testing.md)",
                )
            for position, pipeline in enumerate(and_or.pipelines):
                for command in pipeline.commands:
                    for word in own_words(command):
                        for sub_script in word.subs:
                            self.noop_fallbacks(sub_script, None)
                    if isinstance(command, Group):
                        chained = position < len(and_or.operators) and and_or.operators[position] == "&&"
                        last = position == len(and_or.operators)
                        self.noop_fallbacks(
                            command.body, and_or.pipelines[position + 1] if chained else after if last else None
                        )
                    elif isinstance(command, Simple):
                        nested = self.nested(command)
                        if nested is not None:
                            self.noop_fallbacks(nested[0], None)
                    else:
                        for child in children(command):
                            if isinstance(child, Script):
                                self.noop_fallbacks(child, None)
                            elif isinstance(child, Group):
                                self.noop_fallbacks(child.body, None)

    @staticmethod
    def is_noop(pipeline: Pipeline) -> bool:
        if pipeline.negated or len(pipeline.commands) != 1 or not isinstance(pipeline.commands[0], Simple):
            return False
        return tuple(word.text for word in pipeline.commands[0].words) in _NOOP_FALLBACKS

    def scanner_exception(self, and_or: AndOr, after: Optional[Pipeline]) -> bool:
        scanned = and_or.pipelines[0]
        if scanned.negated or len(scanned.commands) != 1 or not isinstance(scanned.commands[0], Simple):
            return False
        scanner_command = scanned.commands[0]
        scanner = _scanner(scanner_command)
        if scanner is None:
            return False
        fallback = [word.text for word in and_or.pipelines[1].commands[0].words]  # type: ignore[union-attr]
        helper = _SCANNER_HELPERS[scanner]
        report = _report_path(scanner_command)
        problem = None
        if fallback != ["true"]:
            problem = f"use exactly `|| true` before {helper}"
        elif report is None:
            problem = f"writes no report (`-o`/`--output` or `> FILE`) for {helper} to read"
        elif after is None or after.negated or len(after.commands) != 1 or not isinstance(after.commands[0], Simple):
            problem = f"is not immediately followed by `python3 {helper} {report}`"
        elif _required(after.commands[0]) != helper:
            problem = f"is followed by `{_text(after)}` instead of `python3 {helper} {report}`"
        elif report not in _helper_args(after.commands[0]):
            problem = f"writes {report} but {helper} reads {' '.join(_helper_args(after.commands[0])) or 'nothing'}"
        if problem:
            self.report(
                scanned,
                f"is followed by `|| true`, which is sanctioned only when the severity helper gates its report: {problem}",
            )
        return True

    # -- shapes this guard cannot follow -----------------------------------

    def substitutions(self, script: Script) -> None:
        for node in self.walk(script):
            for word in own_words(node):
                for sub in word.subs:
                    if self.has_required(sub):
                        self.report(
                            self.first_required(sub),
                            "runs inside a command substitution, which can lose its exit status (`local`, "
                            "`export`, an argument); run it as a command of its own",
                        )

    def traps(self, script: Script) -> None:
        for node in self.walk(script):
            argv = _argv(node) if isinstance(node, Simple) else []
            args = argv[2:] if argv[1:2] == ["--"] else argv[1:]
            if argv[:1] != ["trap"] or len(args) < 2 or args[0].startswith("-"):
                continue
            signals = {signal.upper() for signal in args[1:]}
            # EXIT and ERR decide how the step ends; DEBUG runs before each command, RETURN after a sourced script.
            kinds = ["ERR" if "ERR" in signals else "EXIT"] if signals & {"EXIT", "0", "SIGEXIT", "ERR"} else []
            kinds += sorted(signals & {"DEBUG", "RETURN"})
            if not kinds:
                continue
            label = _trap_label(kinds[0])
            if _variable_only(args[0]):
                self.report(node, f"sets {label} whose action comes from a variable, which this guard cannot check")
                continue
            try:
                action = self.parse(args[0], node.line)
            except ShellParseError as exc:
                self.report(node, f"sets {label} this guard cannot parse ({exc})")
                continue
            self.noop_fallbacks(action, None)
            self.substitutions(action)
            for kind in kinds:
                problem = self.trap_problem(action, self.functions(), frozenset(), kind in ("EXIT", "ERR"), kind)
                if problem:
                    self.report(node, problem)
                    break

    def trap_problem(
        self, handler: Any, functions: Dict[str, Any], seen: frozenset, entry_status: bool, kind: str = "EXIT"
    ) -> Optional[str]:
        """Why a trap action for *kind*, or a function it calls, can change a failing step's status.

        *entry_status* says whether ``$?`` still holds the status the trap was
        entered with when *handler* starts: true for an EXIT/ERR action itself and
        for a function it calls first. Except on EXIT, where it keeps the entry
        status, bash reads a bare ``exit`` as ``exit $?``, and turning errexit off
        keeps a failing command from stopping the step. DEBUG and RETURN run between
        the script's commands, so ``set -n`` there stops it.
        """
        label = _trap_label(kind)
        if self.has_required(handler):
            return f"runs a required command inside {label}, whose failure does not reach the step's status"
        if isinstance(handler, Group):
            if handler.subshell:
                return None  # an `exit` there ends only the subshell
            handler = handler.body
        if not isinstance(handler, Script):
            return None
        specific = "an ERR trap" if kind == "ERR" else label
        for command in self.option_commands(handler):
            args = _option_args(command)
            if kind != "EXIT" and args is not None and not _apply_set(ShellOptions(True, True), args).errexit:
                return f"sets {specific} that turns errexit off, so the failing command no longer stops the step"
            if kind in ("DEBUG", "RETURN") and args is not None and _noexec(args):
                return f"sets {label} that turns on noexec (`set -n`), so no later command runs"
            if isinstance(command, Simple) and len(command.words) > 1 and command.words[0].text == "exec":
                return f"sets {label} that runs `{_text(command, 40)}`, which replaces the shell and its exit status"
        reraises = _reraising_exits(handler, entry_status, kind != "EXIT", self._current_shell_script)
        for exit_command in self.exits_in(handler):
            argv = [word.text for word in exit_command.words]
            code = argv[1] if len(argv) > 1 else None
            number = _number(code) if code is not None else None
            # On EXIT a bare `exit` keeps the status the trap was entered with; a non-zero literal fails anyway.
            if id(exit_command) in reraises or (code is None and kind == "EXIT") or (number or 0) % 256:
                continue
            if code is None:
                where = "on ERR it exits" if kind == "ERR" else "it exits"
                return (
                    f"sets {specific} that runs a bare `exit` after another command; {where} with that command's "
                    "status instead of the step's"
                )
            if code in ("$?", "${?}"):
                return (
                    f"sets {label} that runs `exit {code}` after another command, so it exits with that "
                    "command's status instead of the step's"
                )
            return f"sets {label} that runs `exit {code}`, which can replace a failing step's status"
        first = handler.items[0].and_or.pipelines[0].commands[0] if handler.items else None
        for command in self.commands_in(handler):
            name = command.words[0].text if isinstance(command, Simple) and command.words else ""
            if name in functions and name not in seen:
                problem = self.trap_problem(
                    functions[name], functions, seen | {name}, entry_status and command is first, kind
                )
                if problem:
                    return problem
        return None

    def opaque_commands(self, script: Script) -> None:
        for node in self.walk(script):
            if not isinstance(node, Simple) or _recognised(node) or self.nested(node) is not None:
                continue
            argv = _argv(node)
            if not argv:
                continue
            if "GITHUB_EXPRESSION" in posixpath.basename(argv[0]):
                self.report(node, "runs a command taken from a `${{ }}` expression, which this guard cannot check")
            elif posixpath.basename(argv[0]) not in _MENTION_OK and not _names_only(argv):
                named = _mentioned(argv)
                if named:
                    self.report(
                        node,
                        f"names {named} but runs it through `{argv[0]}`, which this guard does not model; "
                        "run the required command directly",
                    )

    def redefinitions(self, script: Script) -> None:
        for node in self.walk(script):
            if isinstance(node, Function):
                names, how = [node.name], "a shell function"
            elif isinstance(node, Simple):
                names, how = _redefined(node)
            else:
                continue
            for name in names:
                if name == "command_not_found_handle":
                    self.report(
                        node, "defines bash's `command_not_found_handle`, which can turn a missing command into success"
                    )
                elif name in _PROTECTED or _PYTHON_RE.fullmatch(name):
                    self.report(node, f"redefines `{name}` as {how}, so a later `{name}` may not run the real command")


_SHELL_SYNTAX = ("|", "&", ";", "\n", "`", "$(")
# `matrix.key` or `matrix['key']` inside an expression.
_MATRIX_REF_RE = re.compile(
    r"\bmatrix(?:\.([A-Za-z_][A-Za-z0-9_-]{0,63})|\[ {0,4}['\"]([A-Za-z_][A-Za-z0-9_-]{0,63})['\"] {0,4}\])", re.ASCII
)
# The scalars read from one matrix value: YAML aliases can nest a value exponentially.
_LEAF_BUDGET = 10_000


def _expressions(text: str) -> Iterator[str]:
    """The contents of every ``${{ }}`` expression in *text*."""
    position = 0
    while True:
        start = text.find("${{", position)
        end = text.find("}}", start + 3) if start >= 0 else -1
        if start < 0 or end < 0:
            return
        yield text[start + 3 : end].strip()
        position = end + 2


def _expression_injections(text: str) -> List[str]:
    """``${{ }}`` expressions whose string literals paste shell syntax into the script."""
    found: List[str] = []
    for expression in _expressions(text):
        literals = expression.split("'")[1::2]  # the text inside quotes; `''` only splits a literal in two
        if any(token in literal for literal in literals for token in _SHELL_SYNTAX):
            found.append(expression)
    return found


def _leaves(value: Any, depth: int = 0) -> Iterator[str]:
    """The scalars inside a YAML value, as text, opening nested lists and mappings four levels deep."""
    if isinstance(value, (str, int, float, bool)):
        yield str(value)
    elif depth < 4 and isinstance(value, (list, dict)):
        for item in value.values() if isinstance(value, dict) else value:
            yield from _leaves(item, depth + 1)


def _matrix_values(job: dict) -> Dict[str, List[str]]:
    """Every value each ``strategy.matrix`` key can take, ``include`` rows too."""
    strategy = job.get("strategy")
    matrix = strategy.get("matrix") if isinstance(strategy, dict) else None
    values: Dict[str, List[str]] = {}
    if not isinstance(matrix, dict):
        return values
    include = matrix.get("include")
    rows = [row for row in include if isinstance(row, dict)] if isinstance(include, list) else []
    for key, value in matrix.items():
        if key not in ("include", "exclude") and isinstance(value, list):
            values.setdefault(str(key), []).extend(islice(_leaves(value), _LEAF_BUDGET))
    for row in rows:
        for key, value in row.items():
            values.setdefault(str(key), []).extend(islice(_leaves(value), _LEAF_BUDGET))
    return values


def _matrix_injections(text: str, job: dict) -> List[Tuple[str, str]]:
    """``(key, value)`` for each ``matrix.key`` the script pastes whose value holds shell syntax."""
    values = _matrix_values(job)
    found: List[Tuple[str, str]] = []
    for expression in _expressions(text):
        for key in (dotted or quoted for dotted, quoted in _MATRIX_REF_RE.findall(expression)):
            bad = next((value for value in values.get(key, []) if any(token in value for token in _SHELL_SYNTAX)), None)
            if bad is not None and (key, bad) not in found:
                found.append((key, bad))
    return found


def _github_expressions(text: str, action_path: Optional[str] = None) -> str:
    """Replace each ``${{ … }}`` with a plain word: GitHub substitutes them before the shell runs.

    ``github.workspace`` becomes ``$GITHUB_WORKSPACE``, and in a composite action of
    this repository ``github.action_path`` becomes its directory there, so a script
    named through either is read like any other repository script.
    """
    out: List[str] = []
    position = 0
    while True:
        start = text.find("${{", position)
        end = text.find("}}", start + 3) if start >= 0 else -1
        if start < 0 or end < 0:
            out.append(text[position:])
            return "".join(out)
        expression = "".join(text[start + 3 : end].split())
        word = "GITHUB_EXPRESSION"
        if expression == "github.workspace":
            word = "$GITHUB_WORKSPACE"
        elif expression == "github.action_path" and action_path is not None:
            word = posixpath.normpath("$GITHUB_WORKSPACE/" + action_path)
        out.append(text[position:start] + word)
        position = end + 2


_REQUIRED_WORD_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(program) for program in _REQUIRED_PROGRAMS) + r")\b"
    r"|node {1,8}--test|tools[/\\](?:check_\w{1,64}|update_site_version)\.py",
    re.ASCII,
)


def scan_script(
    text: str,
    where: str,
    options: ShellOptions,
    root: Optional[Path] = None,
    strict_parse: bool = True,
    cwd: Optional[str] = ".",
    unit: str = "script",
    action_path: Optional[str] = None,
) -> Tuple[List[str], List[str]]:
    """Analyse one shell script; return ``(findings, required command kinds it names)``.

    A script that does not parse is a finding (fail closed) when *strict_parse*
    is set or the text names a required command. *cwd* is where relative paths
    start (None when unknown), *unit* what a finding's line number counts in, and
    *action_path* the directory of the composite action the script belongs to.
    """
    findings = [
        f"{where}: the expression `${{{{ {expression} }}}}` pastes shell syntax into the script, which this "
        "guard cannot check; pass the value through `env:` instead"
        for expression in _expression_injections(text)
    ]
    analysis = _Analysis(where, root, cwd, unit)
    try:
        script = analysis.parse(_github_expressions(text, action_path), 1)
        analysis.script = script
        analysis.mark_downloads(script)
        analysis.scope(script, options, True, "")
        analysis.noop_fallbacks(script, None)
        analysis.substitutions(script)
        analysis.traps(script)
        analysis.opaque_commands(script)
        analysis.redefinitions(script)
        required = [
            kind for kind in (_required(node) for node in analysis.walk(script) if isinstance(node, Simple)) if kind
        ]
    except ShellParseError as exc:
        if strict_parse or _REQUIRED_WORD_RE.search(text):
            findings.append(f"{where}: could not parse the shell script ({exc}); simplify it or extend this guard")
        return findings, []
    except RecursionError:
        return findings + [f"{where}: the script nests too deeply for this guard to analyse; simplify it"], []
    # A repository script that two commands run is analysed for each; report what it shows once.
    return list(dict.fromkeys(findings + analysis.findings)), required


# ── Workflows and gauntlets ──────────────────────────────────────────────


def _run_default(container: Any, key: str) -> Optional[str]:
    """``defaults.run.<key>`` (``shell`` or ``working-directory``) of a workflow or job, if it is set."""
    defaults = container.get("defaults") if isinstance(container, dict) else None
    run = defaults.get("run") if isinstance(defaults, dict) else None
    value = run.get(key) if isinstance(run, dict) else None
    return value if isinstance(value, str) else None


def _working_directory(value: Optional[str], action_path: Optional[str] = None) -> Optional[str]:
    """A step's working directory relative to the repository root, or None when it is hidden or outside."""
    if value is None:
        return "."
    value = _github_expressions(value, action_path)
    if value in ("$GITHUB_WORKSPACE", "${GITHUB_WORKSPACE}"):
        return "."
    if value.startswith(_WORKSPACE_PREFIXES):
        value = value.split("/", 1)[1]
    path = posixpath.normpath(value)
    hidden = "$" in path or "GITHUB_EXPRESSION" in path
    return None if hidden or posixpath.isabs(path) or path == ".." or path.startswith("../") else path


def _shell_options(shell: Optional[str], runner: str) -> Tuple[Optional[ShellOptions], str]:
    """The options a step's shell starts with, or None (with its name) when it cannot be modelled."""
    if shell is None:
        if runner != "posix":
            return None, "the default shell of a Windows or unresolved runner"
        return GITHUB_DEFAULT, "bash -e"
    spelling = " ".join(str(shell).split())
    if spelling == "bash":
        return ShellOptions(errexit=True, pipefail=True), spelling  # bash --noprofile --norc -eo pipefail {0}
    if spelling == "sh":
        return ShellOptions(errexit=True, pipefail=False), spelling  # sh -e {0}
    parts = spelling.split()
    if parts and posixpath.basename(parts[0]) in ("bash", "sh", "dash") and "{0}" in parts:
        return _apply_set(INTERACTIVE, parts[1:]), spelling
    return None, f"`shell: {spelling}`"


def _runner(job: dict) -> str:
    """``posix``, or ``other`` when runs-on may name a Windows runner or cannot be resolved."""
    runs_on = job.get("runs-on")
    labels = runs_on if isinstance(runs_on, list) else [runs_on]
    strategy = job.get("strategy")
    matrix = strategy.get("matrix") if isinstance(strategy, dict) else None
    matrix = matrix if isinstance(matrix, dict) else {}
    include = matrix.get("include") if isinstance(matrix.get("include"), list) else []
    resolved: List[str] = []
    for label in labels:
        if not isinstance(label, str):
            return "other"
        match = re.fullmatch(r"\$\{\{ ?matrix\.([\w-]{1,64}) ?\}\}", label.strip(), re.ASCII)
        if match and isinstance(matrix.get(match.group(1)), list):
            key = match.group(1)
            resolved += islice(_leaves(matrix[key]), _LEAF_BUDGET)
            for row in include:
                resolved += islice(_leaves(row[key]), _LEAF_BUDGET) if isinstance(row, dict) and key in row else []
        elif "${{" in label:
            return "other"
        else:
            resolved.append(label)
    return "other" if any("windows" in label.lower() for label in resolved) else "posix"


def _expression_text(value: str) -> str:
    """*value* without whitespace and without one ``${{ }}`` around it: ``${{ failure() }}`` is ``failure()``."""
    text = "".join(value.split())
    return text[3:-2] if text.startswith("${{") and text.endswith("}}") else text


def _truthy_flag(value: Any) -> bool:
    return value is True or (isinstance(value, str) and _expression_text(value) not in ("", "false"))


# Conditions under which a step or job never runs while the run passes.
_NEVER_ON_SUCCESS = {
    "false": "keeps {kind} from ever running",
    "failure()": "runs {kind} only after an earlier step has failed",
    "!success()": "runs {kind} only after an earlier step has failed",
    "cancelled()": "runs {kind} only when the run is cancelled",
}


def _disabled(value: Any) -> Optional[str]:
    """How an ``if:`` keeps a step or job from running while the run passes (``false``, ``failure()``), or None."""
    if value is False:
        return _NEVER_ON_SUCCESS["false"]
    return _NEVER_ON_SUCCESS.get(_expression_text(value)) if isinstance(value, str) else None


def _condition(value: Any) -> str:
    return "false" if value is False else _expression_text(str(value))


def scan_steps(
    steps: List[Any],
    where: str,
    job: dict,
    defaults: Optional[str],
    root: Optional[Path] = None,
    local_actions: Optional[Dict[str, List[str]]] = None,
    default_directory: Optional[str] = None,
    action_path: Optional[str] = None,
) -> Tuple[List[str], List[str]]:
    """Check every ``run:`` step of one job (or composite action).

    *defaults* and *default_directory* are the workflow's ``defaults.run``;
    *local_actions* maps each local action to what it runs; *action_path* is the
    directory of the composite action these steps belong to.
    """
    findings: List[str] = []
    required: List[str] = []
    job_defaults = _run_default(job, "shell")
    job_directory = _run_default(job, "working-directory") or default_directory
    runner = _runner(job) if "runs-on" in job else "posix"
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            findings.append(f"{where}: step #{index + 1} is not a mapping")
            continue
        name = step.get("name") or step.get("id") or f"#{index + 1}"
        label = f"{where}, step {name!r}"
        uses = step.get("uses")
        local = isinstance(uses, str) and uses.startswith("./")
        runs = (local_actions or {}).get(posixpath.normpath(uses[2:] or ".")) if local else None
        run = step.get("run") if isinstance(step.get("run"), str) else None
        never = _disabled(step.get("if"))
        if never:
            kinds = runs or (scan_script(run, label, GITHUB_DEFAULT, root, strict_parse=False)[1] if run else [])
            if kinds:
                findings.append(
                    f"{label}: `if: {_condition(step.get('if'))}` {never.format(kind=kinds[0])}; remove the "
                    "condition or the step"
                )
            continue
        masked = _truthy_flag(step.get("continue-on-error")) or _truthy_flag(job.get("continue-on-error"))
        if local:
            if runs:
                required += runs  # it runs them, so they count for the workflow or action that uses it
            if runs and masked:
                findings.append(
                    f"{label}: `continue-on-error` lets {uses}, which runs {runs[0]}, fail the job silently"
                )
            continue
        if run is None:
            continue
        shell = step.get("shell") if isinstance(step.get("shell"), str) else None
        options, spelling = _shell_options(shell or job_defaults or defaults, runner)
        if options is None:
            if _REQUIRED_WORD_RE.search(run):
                findings.append(
                    f"{label}: runs a required command under {spelling}; use `shell: bash` so its exit status is "
                    "checked"
                )
            continue
        directory = step.get("working-directory") if isinstance(step.get("working-directory"), str) else None
        cwd = _working_directory(directory or job_directory, action_path)
        step_findings, step_required = scan_script(
            run, label, options, root, strict_parse=False, cwd=cwd, unit="`run:` block", action_path=action_path
        )
        findings += [
            f"{label}: `${{{{ matrix.{key} }}}}` pastes {value!r} into the script, which this guard cannot check; "
            "pass the value through `env:` instead"
            for key, value in _matrix_injections(run, job)
        ]
        findings += step_findings
        required += step_required
        if step_required and masked:
            findings.append(f"{label}: `continue-on-error` lets {step_required[0]} fail without failing the job")
    return findings, required


def scan_workflow(
    text: str,
    name: str,
    root: Optional[Path] = None,
    local_actions: Optional[Dict[str, List[str]]] = None,
    action_path: Optional[str] = None,
) -> Tuple[List[str], List[str]]:
    """Check one workflow or composite-action file; return ``(findings, required command kinds)``."""
    try:
        doc = yaml.safe_load(text)
    except (yaml.YAMLError, ValueError, RecursionError) as exc:
        return [f"{name}: could not parse the YAML ({exc.__class__.__name__}: {exc})"], []
    if doc is None:
        return [], []
    if not isinstance(doc, dict):
        return [f"{name}: the file is not a YAML mapping"], []
    findings: List[str] = []
    required: List[str] = []
    jobs = doc.get("jobs") or {}
    if not isinstance(jobs, dict):
        return [f"{name}: `jobs` is not a mapping"], []
    shell, directory = _run_default(doc, "shell"), _run_default(doc, "working-directory")
    for job_id, job in jobs.items():
        steps = job.get("steps") or [] if isinstance(job, dict) else None
        if not isinstance(steps, list):
            findings.append(f"{name}: job {job_id!r} has no list of steps")
            continue
        found, kinds = scan_steps(steps, f"{name}: job {job_id!r}", job, shell, root, local_actions, directory)
        never = _disabled(job.get("if"))
        if never:
            if kinds:
                findings.append(
                    f"{name}: job {job_id!r}: `if: {_condition(job.get('if'))}` {never.format(kind=kinds[0])}; "
                    "remove the condition or the job"
                )
            continue
        findings += found
        required += kinds
    runs = doc.get("runs")
    if isinstance(runs, dict) and isinstance(runs.get("steps"), list):
        found, kinds = scan_steps(
            runs["steps"], f"{name}: composite action", {}, None, root, local_actions, None, action_path
        )
        findings += found
        required += kinds
    return findings, required


def gauntlet_script(text: str) -> Optional[str]:
    """The shell inside a document's ``<!-- gauntlet:begin -->`` bash fence, if any.

    Fences are whole lines, as in Markdown, so three backticks inside a command or
    a comment do not end the block early.
    """
    begin, end = text.find("<!-- gauntlet:begin -->"), text.find("<!-- gauntlet:end -->")
    if begin < 0 or end < begin:
        return None
    lines = text[begin:end].split("\n")
    opening = next((index for index, line in enumerate(lines) if line.strip() == "```bash"), None)
    if opening is None:
        return None
    for index in range(opening + 1, len(lines)):
        if lines[index].strip() == "```":
            return "\n".join(lines[opening + 1 : index]) + "\n"
    return None


def _missing_kinds(where: str, required: List[str]) -> List[str]:
    missing = [kind for kind in _EXPECTED_KINDS if not any(item.startswith(kind) for item in required)]
    if not missing:
        return []
    return [
        f"{where}: no {', '.join(missing)} invocation recognised; either it no longer runs one, or this guard "
        "no longer recognises it"
    ]


def _shown(path: Path, root: Path) -> str:
    """*path* relative to *root*, with forward slashes on every platform."""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _read(path: Path, root: Path, findings: List[str]) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:  # ValueError: not UTF-8
        findings.append(f"{_shown(path, root)}: could not be read ({exc.__class__.__name__}), so it was not checked")
        return None


def _local_uses(text: str) -> List[str]:
    """The local actions (``uses: ./path``) a workflow or composite action runs, as repository paths."""
    try:
        doc = yaml.safe_load(text)
    except (yaml.YAMLError, ValueError, RecursionError):
        return []  # reported when the file itself is scanned
    steps: List[Any] = []
    if isinstance(doc, dict):
        jobs = doc.get("jobs")
        for job in jobs.values() if isinstance(jobs, dict) else []:
            steps += job.get("steps") or [] if isinstance(job, dict) and isinstance(job.get("steps"), list) else []
        runs = doc.get("runs")
        steps += runs.get("steps") or [] if isinstance(runs, dict) and isinstance(runs.get("steps"), list) else []
    uses = [step.get("uses") for step in steps if isinstance(step, dict)]
    return [posixpath.normpath(use[2:] or ".") for use in uses if isinstance(use, str) and use.startswith("./")]


def _local_actions(root: Path, texts: List[str], findings: List[str]) -> List[Path]:
    """Every composite action under ``.github`` plus each one a ``uses: ./path`` names, wherever it lives."""
    actions = set((root / ".github").glob("**/action.y*ml"))
    pending = [key for text in texts for key in _local_uses(text)]
    seen: set = set()
    while pending:
        key = pending.pop()
        if key in seen:
            continue
        seen.add(key)
        try:
            directory = (root / key).resolve()
            files = [directory / name for name in ("action.yml", "action.yaml") if (directory / name).is_file()]
            inside = directory.is_relative_to(root)
        except (OSError, RuntimeError, ValueError):  # a name too long, a NUL byte, a symlink loop
            files, inside = [], False
        if not inside or not files:
            findings.append(f"`uses: ./{key}` names no action.yml or action.yaml in this repository")
            continue
        actions.add(files[0])
        text = _read(files[0], root, findings)
        pending += _local_uses(text) if text is not None else []
    return sorted(actions)


def _action_kinds(action_texts: Dict[Path, Optional[str]], root: Path) -> Dict[str, List[str]]:
    """What each local action runs, by its directory, the local actions it uses included.

    A ``continue-on-error`` on a step that uses one is caught however deep the
    required command sits. The kinds only grow, so one pass per nesting level settles them.
    """
    kinds: Dict[str, List[str]] = {}
    for _ in range(len(action_texts) + 1):
        resolved = {
            _shown(path.parent, root): list(
                dict.fromkeys(scan_workflow(text, path.name, root, kinds, _shown(path.parent, root))[1])
            )
            for path, text in action_texts.items()
            if text is not None
        }
        if resolved == kinds:
            break
        kinds = resolved
    return kinds


def scan_repo(root: Path) -> Tuple[List[str], int, int]:
    """Scan *root*'s workflows, composite actions and gauntlets; return ``(findings, surfaces, required)``."""
    findings: List[str] = []
    try:
        root = root.resolve()  # one spelling for every path below, whatever `--root` was
    except (OSError, RuntimeError):
        pass
    workflow_dir = root / ".github" / "workflows"
    workflows = sorted([*workflow_dir.glob("*.yml"), *workflow_dir.glob("*.yaml")])
    if not workflows:
        findings.append(f"no workflows under {_shown(workflow_dir, root)}; a scan of nothing would pass")
    elif not any(path.stem == "ci" for path in workflows):
        findings.append(
            f"no ci.yml under {_shown(workflow_dir, root)}; this guard checks that the validation workflow still "
            f"runs {', '.join(_EXPECTED_KINDS)}"
        )
    texts = {path: _read(path, root, findings) for path in workflows}
    actions = _local_actions(root, [text for text in texts.values() if text is not None], findings)
    action_texts = {path: _read(path, root, findings) for path in actions}
    local_actions = _action_kinds(action_texts, root)
    total = 0
    for path, text in [*action_texts.items(), *texts.items()]:
        if text is None:
            continue
        action_path = _shown(path.parent, root) if path in action_texts else None
        found, required = scan_workflow(text, _shown(path, root), root, local_actions, action_path)
        findings += found
        total += len(required)
        if path in texts and path.stem == "ci":
            findings += _missing_kinds(path.name, required)
    for doc in _GAUNTLET_DOCS:
        text = _read(root / doc, root, findings)
        script = gauntlet_script(text) if text is not None else None
        if text is not None and script is None:
            findings.append(f"{doc}: no ```bash fence between the gauntlet:begin/end markers")
        if script is None:
            continue
        found, required = scan_script(script, f"{doc} gauntlet", INTERACTIVE, root, unit="gauntlet")
        findings += found + _missing_kinds(f"{doc} gauntlet", required)
        total += len(required)
    return findings, len(workflows) + len(actions) + len(_GAUNTLET_DOCS), total


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--strict", action="store_true", help="Accepted for gauntlet symmetry; this guard is always fatal."
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress the success summary.")
    parser.add_argument(
        "--root", type=Path, default=_REPO_ROOT, help="Repository root to scan (default: this checkout)."
    )
    args = parser.parse_args(argv)

    findings, surfaces, required = scan_repo(args.root)
    if findings:
        print("FAIL: a required CI command can fail without failing its step or gauntlet:")
        for finding in findings:
            print(f"  ✗ {finding}")
        print(
            "\nRun required commands as plain commands. A fallback must end with `exit 1`, or capture the "
            'status first: `|| { rc=$?; echo …; exit "$rc"; }`. `|| true` is reserved for bandit/pip-audit '
            "followed by their severity helper (docs/standards/testing.md, '`|| true` discipline')."
        )
        return 1
    if not args.quiet:
        print(
            f"OK: {required} required command invocation(s) across {surfaces} workflow/gauntlet surface(s) "
            "propagate failure."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
