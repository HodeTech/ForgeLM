"""Tests for the CI failure-masking guard (tools/check_ci_failure_masking.py).

The guard exists because appending `|| true` to the validation job's
`node --test` command left every wiring test green: the only failure-masking
check looked at the three documented gauntlets, and only for the literal
`|| true`. These tests pin both directions — every masking shape is caught,
and comments, quoted prose, here-documents and the sanctioned scanner +
severity-helper pattern are not mistaken for one — and run the guard against
mutated copies of the real workflows and gauntlets.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_guard(monkeypatch):
    path = _REPO_ROOT / "tools" / "check_ci_failure_masking.py"
    spec = importlib.util.spec_from_file_location("check_ci_failure_masking", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "check_ci_failure_masking", module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def guard(monkeypatch):
    return _load_guard(monkeypatch)


def _findings(guard, script, options=None):
    findings, _required = guard.scan_script(script, "run", options or guard.GITHUB_DEFAULT)
    return findings


class TestMaskedRequiredCommands:
    """Each script runs a required command whose failure would not fail the step."""

    @pytest.mark.parametrize(
        "script,reason",
        [
            ("node --test tests/site/*.test.cjs || true", "is masked by `|| true`"),
            ("pytest -q --tb=short || true", "is masked by `|| true`"),
            ("pytest || :", "is masked by `|| :`"),
            ("pytest || exit 0", "is masked by `|| exit 0`"),
            ("pytest || exit 256", "is masked by `|| exit 256`"),
            ("pytest || echo 'tests failed'", "is masked by `|| echo 'tests failed'`"),
            ("pytest && echo ok || echo failed", "is masked by `|| echo failed`"),
            ("ruff check \\\n  . \\\n  || true", "is masked by `|| true`"),
            ("python3 tools/check_site_claims.py --strict || true", "is masked by"),
            ("python -m pytest || true", "is masked by"),
            ("python -mpytest || true", "is masked by"),
            ("python3 -I -m mypy --strict forgelm/__init__.py || true", "is masked by"),
            ("./tools/check_anchor_resolution.py --strict || true", "is masked by"),
            ("timeout 600 env CI=1 pytest || true", "is masked by"),
            ("uv run pytest || true", "is masked by"),
            ("forgelm --config config_template.yaml --dry-run || true", "is masked by"),
            ("python -m forgelm.cli --version || true", "is masked by"),
            ("{ pytest; echo done; } || true", "can fail without stopping the script"),
            ("( pytest ) || true", "is masked by `|| true`"),
            ("bash -c 'pytest || true'", "is masked by `|| true`"),
            ("bash <<'EOF'\npytest || true\nEOF", "is masked by `|| true`"),
        ],
    )
    def test_or_fallbacks_that_can_succeed(self, guard, script, reason):
        findings = _findings(guard, script)
        assert findings and reason in findings[0], findings
        assert not any("could not parse" in finding for finding in findings)

    @pytest.mark.parametrize(
        "script,reason",
        [
            ("pytest | tee pytest.log", "pipefail"),
            ("X=1 pytest | grep -q passed", "pipefail"),
            ("set +e\npytest\necho done", "errexit is off"),
            ("set +o errexit\npytest\necho done", "errexit is off"),
            ("node --test tests/site/*.test.cjs && echo ok\necho next", "errexit ignores"),
            ("[ -f setup.cfg ] && pytest\necho next", "errexit ignores"),
            ("! pytest", "negated"),
            ("pytest &\nwait", "background"),
            ("true || pytest", "only when the command before `||` fails"),
            ("if pytest; then echo ok; fi", "condition"),
            ('if [ -n "$CI" ]; then\n  pytest\nfi', "one branch"),
            ('case "$X" in\n  a) pytest ;;\nesac', "case"),
            ('while read -r f; do pytest "$f"; done < list.txt', "loop"),
            ("run_tests() { pytest; }\nrun_tests", "shell function"),
            ("out=$(pytest)", "command substitution"),
            ('echo "$(ruff check .)"', "command substitution"),
            ("find . -name '*.py' -exec ruff check {} \\;", "find -exec"),
            ("bash -c 'pytest; echo ok'", "errexit is off"),
        ],
    )
    def test_positions_that_lose_the_exit_status(self, guard, script, reason):
        findings = _findings(guard, script)
        assert findings and reason in findings[0], findings

    def test_a_for_loop_without_errexit_keeps_only_the_last_iteration(self, guard):
        script = 'for f in a b; do pytest "$f"; done'
        assert not _findings(guard, script)
        assert "final iteration" in _findings(guard, script, guard.ShellOptions(errexit=False, pipefail=False))[0]

    def test_a_masked_command_is_reported_once(self, guard):
        assert len(_findings(guard, "bash -c 'pytest || true' || true")) == 1
        assert len(_findings(guard, "a && { pytest || true; } && b")) == 1


class TestSafeShapes:
    """Required commands whose failure still fails the step, and prose that is not a command."""

    @pytest.mark.parametrize(
        "script",
        [
            "pytest -q --tb=short --cov-report=xml:coverage.xml",
            "pytest || exit 1",
            "pytest || exit",
            "pytest || false",
            "pytest || { echo '::error::tests failed'; exit 1; }",
            'pytest || { rc=$?; echo failed; exit "$rc"; }',
            "set -o pipefail\npytest | tee pytest.log",
            "echo start | pytest",
            "pytest 2>&1 > /tmp/pytest.log",
            "cd forgelm\npytest",
            "echo start\nnode --test tests/site/*.test.cjs && echo ok",
            'for tpl in a b; do\n  forgelm --config "/tmp/qs-$tpl.yaml" --dry-run\ndone',
            "bash -ec 'pytest; echo ok'",
            "find . -name '*.py' -exec ruff check {} +",
            "command -v pytest || pip install pytest",
            'x=$(mktemp)\ntrap \'rm -f "$x"\' EXIT\npytest --junitxml "$x"',
            # Not commands: comments, quoted prose, here-documents and `python -c` programs.
            "# pytest || true\necho hi",
            'echo "pytest || true" > notes.txt',
            "python3 - <<'PY'\nprint('pytest || true')\nPY\necho after",
            'python -c "\nimport x\nassert x, \'pytest || true\'\nprint(f\'{x}: {getattr(x, \\"v\\", \\"u\\")}\')\n"',
            # Failure-raising fallbacks on ordinary commands (ci.yml's License check, site-deploy).
            'test -f LICENSE || (echo "LICENSE file missing" && exit 1)',
            'test -f "site/js/${lang}.js" || { echo "missing ${lang}.js"; exit 1; }',
            # bash arrays and parameter expansion (nightly.yml's wheel smoke test).
            'set -euo pipefail\nwheels=(dist/forgelm-*.whl)\nif [ "${#wheels[@]}" -ne 1 ]; then\n'
            '  echo "found ${wheels[*]:-(none)}" >&2\n  exit 1\nfi\nforgelm quickstart --list | tee /tmp/q.txt',
        ],
    )
    def test_not_reported(self, guard, script):
        assert not _findings(guard, script)


class TestReviewedShapes:
    """Shapes from the guard's review, each checked against bash 5.2 before being pinned here."""

    @pytest.mark.parametrize(
        "script,reason",
        [
            # `$?` inside a group is the previous command's status, not the failure's.
            ("pytest || { echo failed; exit $?; }\necho after", "is masked by"),
            ("pytest || ( echo failed; exit $? )\necho after", "is masked by"),
            ("pytest || { echo flaky; exit 0; exit 1; }", "is masked by"),
            ('pytest || { [ "$X" = 1 ] && exit 0; exit 1; }', "is masked by"),
            ('pytest || exit "${ALLOW_FAILURE_RC:-0}"', "is masked by"),
            # Flag clusters that take an option name.
            ("bash -eo pipefail -c 'pytest || true'", "is masked by `|| true`"),
            ("bash --noprofile --norc -euo pipefail -c 'pytest || true'", "is masked by `|| true`"),
            ("echo 'pytest || true' | bash", "standard input"),
            # bash does not exit on a `{ … }` group's status when the failure inside it was ignored.
            ("{ ruff check . && ruff format --check .; }\necho done", "errexit ignores"),
            ("{ set +e; pytest; }\necho next", "errexit is off or ignored"),
            ("{ set -e; pytest; echo inner; } || exit 1\necho after", "errexit is off or ignored"),
            ("set +e && pytest\necho done", "errexit is off or ignored"),
            ("command set +e\npytest\necho done", "errexit is off or ignored"),
            ("builtin set +e\npytest\necho done", "errexit is off or ignored"),
            ("shopt -u -o errexit\npytest\necho done", "errexit is off or ignored"),
            ("eval 'pytest || true'", "is masked by `|| true`"),
            ("trap 'exit 0' EXIT\npytest", "EXIT/ERR trap"),
            ("trap 'rm -f /tmp/x; exit $X' ERR\npytest", "EXIT/ERR trap"),
            ("bash -c 'echo \"$(pytest)\"'", "command substitution"),
            ('echo "${X:-$(pytest)}"', "command substitution"),
            ('[[ -n "$(pytest)" ]] || echo empty', "command substitution"),
            ("cat <<EOF\n$(pytest)\nEOF", "command substitution"),
            ("find . -exec sh -c 'pytest || true' sh {} +", "is masked by `|| true`"),
            ('shopt -s nullglob\nfor f in tests/*.py; do pytest "$f"; done', "fixed word list"),
            ("for ((i=0; i<2; i++)); do pytest; done", "fixed word list"),
            # Wrappers the guard models: the pipe still needs pipefail.
            ("coverage run -m pytest | tee log", "pipefail"),
            ("stdbuf -oL pytest | tee log", "pipefail"),
            ("xvfb-run -a pytest | tee log", "pipefail"),
            ("uv run --with pytest-xdist pytest -n 4 | tee log", "pipefail"),
            ("env --chdir /tmp pytest | tee log", "pipefail"),
            # Wrappers and commands it cannot follow fail closed.
            ("$PY -m pytest | tee log", "names pytest"),
            ("retry pytest", "names pytest"),
            ("chrt 10 pytest", "names pytest"),
            ("pytest -q ${{ matrix.experimental && '|| true' || '' }}", "pastes shell syntax"),
            ("${{ matrix.test_cmd }}", "`${{ }}` expression"),
            ("${{ runner.temp }}/venv/bin/pytest || true", "is masked by"),
            # Second review round. `$?` in a trap action is the entry status only in its first command.
            ("trap 'echo bye; exit $?' EXIT\npytest", "after another command"),
            ("trap 'echo \"failed at $LINENO\"; exit $?' ERR\npytest", "after another command"),
            ("trap -- 'exit 0' EXIT\npytest", "EXIT/ERR trap"),
            ("trap 'exit 0' 0\npytest", "EXIT/ERR trap"),
            ("cleanup() { rm -f x; exit 0; }\ntrap cleanup EXIT\npytest", "EXIT/ERR trap"),
            ("helper() { exit 0; }\ncleanup() { helper; }\ntrap cleanup EXIT\npytest", "EXIT/ERR trap"),
            ("trap 'pytest' EXIT\necho hi", "inside an EXIT/ERR trap"),
            ("cleanup() { exit $?; }\ntrap 'echo hi; cleanup' EXIT\npytest", "after another command"),
            # Scripts, trap actions and `set` arguments taken from a variable.
            ("A='exit 0'\ntrap \"$A\" EXIT\npytest", "action comes from a variable"),
            ("CMD='pytest || true'\nbash -c \"$CMD\"", "taken from the variable `$CMD`"),
            ("CMD='pytest || true'\nbash <<< \"$CMD\"", "taken from the variable `$CMD`"),
            ("CMD='pytest || true'\neval \"$CMD\"", "taken from the variable `$CMD`"),
            ("OPT=+e\nset $OPT\npytest\necho done", "errexit is off or ignored"),
            ("for ((i=$(pytest); i<1; i++)); do :; done\necho done", "command substitution"),
            # Redefining a required command, or one the analysis relies on.
            ("node() { echo skipped; }\nnode --test tests/site/*.test.cjs", "redefines `node`"),
            ("function pytest { :; }\npytest tests/", "redefines `pytest`"),
            ("python3() { :; }\npython3 tools/check_site_claims.py --strict", "redefines `python3`"),
            ("alias node=true\nnode --test x", "as an alias"),
            ("hash -p /bin/true pytest\npytest", "as a hashed path"),
            ("exit() { :; }\npytest || exit 1", "redefines `exit`"),
            ("set() { :; }\nset -o pipefail\npytest | tee log", "redefines `set`"),
            ("command_not_found_handle() { return 0; }\npytest", "command_not_found_handle"),
            ("exit 0\npytest tests/", "never runs"),
            ("echo start\nexit\npytest tests/", "never runs"),
            ("exec true\npytest tests/", "never runs"),
            # Constructs the lexer used to hide.
            ("((pytest) || true)", "is masked by `|| true`"),
            ("echo $((pytest) || true)", "command substitution"),
            ("declare -a a=($(pytest))", "command substitution"),
            ("(( x = $(pytest) ))", "command substitution"),
            ("echo $(( $(pytest) ))", "command substitution"),
            ("out=`pytest`", "command substitution"),
            ("eval 'set +e'\npytest\necho done", "errexit is off or ignored"),
            ("shopt -uo errexit\npytest\necho done", "errexit is off or ignored"),
            ("set -o pipefail\nshopt -uo pipefail\npytest | tee log", "pipefail"),
            # Tool runners and wrappers that keep the program's status.
            ("uvx ruff check . | tee log", "pipefail"),
            ("uv tool run ruff check . | tee log", "pipefail"),
            ("pipx run ruff check . | tee log", "pipefail"),
            ("ionice -c 3 pytest | tee log", "pipefail"),
            ("taskset -c 0 pytest | tee log", "pipefail"),
            ("env -S 'pytest -q' | tee log", "pipefail"),
            # `|| true` is refused inside substitutions and trap actions too.
            ("v=$(git describe || true)\npytest", "discards its failure"),
            ("trap 'rm -f x || true' EXIT\npytest", "discards its failure"),
            # On ERR a bare `exit` uses the status of the command before it, and `set +e` lets the step go on.
            ("trap 'echo x; exit' ERR\npytest\necho done", "bare `exit` after another command"),
            ("f() { echo x; exit; }\ntrap f ERR\npytest\necho done", "bare `exit` after another command"),
            ("trap 'set +e' ERR\npytest\necho done", "turns errexit off"),
            ("trap 'set +o errexit' ERR\npytest\necho done", "turns errexit off"),
            # A captured status that is assigned again no longer holds the failure.
            ("trap 'rc=$?; rc=0; exit $rc' EXIT\npytest", "runs `exit $rc`"),
            ("pytest || { rc=$?; rc=0; exit $rc; }\necho done", "is masked by"),
            # Tool runners with a package spec, `python -m coverage run` and `conda run`.
            ("uvx ruff@0.8.0 check . | tee log", "pipefail"),
            ("uvx 'ruff>=0.8' check . | tee log", "pipefail"),
            ("pipx run ruff==0.8 check . | tee log", "pipefail"),
            ("python3 -m coverage run -m pytest | tee log", "pipefail"),
            ("conda run -n env pytest | tee log", "pipefail"),
            ("micromamba run -n env pytest | tee log", "pipefail"),
            ("ruff check . --exit-zero", "`--exit-zero`"),
            ("ruff check -e .", "`--exit-zero`"),
            # Substitutions in a compound command's redirections and in `case` patterns run too.
            ('while read -r l; do echo "$l"; done < <(ruff check .)\necho done', "command substitution"),
            ("while read -r x; do :; done <<EOF\n$(pytest)\nEOF\necho done", "command substitution"),
            ("case x in $(pytest)) echo ;; esac", "command substitution"),
            # noexec, a backgrounded `set`, and `$'` inside double quotes, which is not a quote there.
            ("set -n\npytest", "never runs"),
            ("set -o noexec\npytest", "never runs"),
            ("set +e\nset -e &\nwait\npytest\necho done", "errexit is off"),
            ('echo "$\'"\npytest || true\necho "\'"', "is masked by `|| true`"),
            # A script read from standard input: `source /dev/stdin`, and bash's lone `-`.
            ("source /dev/stdin <<'EOF'\npytest || true\nEOF", "is masked by `|| true`"),
            ("bash - <<'EOF'\npytest || true\nEOF", "is masked by `|| true`"),
            # An `if` whose failure branch can exit 0: after `! cmd`, `$?` is 0.
            ("if ! pytest; then exit $?; fi\necho done", "condition"),
            ("if ! pytest; then rc=$?; exit $rc; fi\necho done", "condition"),
            ("if ! pytest; then echo failed; fi\necho done", "condition"),
            ("if pytest; then :; else echo x; exit; fi\necho done", "condition"),
            ("if pytest; then :; elif true; then exit 1; fi", "condition"),
            # The command whose failure skips a required one is named.
            ("cd sub && pytest\necho done", "is skipped when `cd sub` fails"),
            # An `if` gate is accepted only when nothing else in it can leave early or run a required command.
            ("if pytest; then exit 0; else exit 1; fi\nruff check .", "condition"),
            ("if ! pytest; then exit 1; else echo ok; exit 0; fi\nruff check .", "condition"),
            ("cleanup() { rm -f x; exit 0; }\nif ! pytest; then cleanup; exit 1; fi\necho done", "condition"),
            ("if ! pytest; then { exec true; }; exit 1; fi\necho done", "condition"),
            ("if ! pytest; then exit 0; exit 1; fi\necho done", "condition"),
            ("if ! pytest; then exit 1 & fi\necho done", "condition"),
            ("if pytest; then :; else rc=$?; rc=0; exit $rc; fi\necho done", "condition"),
            ("if ! pytest; then exit 1; else ruff check . && echo ok; fi\necho done", "condition"),
            # A loop that can `continue` or `break` past its required command, or that iterates a variable.
            (
                'for f in nomatch/*.py; do [ -e "$f" ] || continue; pytest "$f"; done\necho done',
                "`continue` or `break`",
            ),
            ('for f in a b; do test -e "$f" || break; pytest "$f"; done\necho done', "`continue` or `break`"),
            ('for f in $FILES; do pytest "$f"; done\necho done', "fixed word list"),
            # ruff's one-letter flags combine; `exit 0`, `exec` and `set -n` stop the script from `eval` too.
            ("ruff check -ne .", "`--exit-zero`"),
            ("ruff check -qe .", "`--exit-zero`"),
            ("eval 'set -n'\npytest", "never runs"),
            # A captured status changed by arithmetic, a setter or a loop variable no longer holds the failure.
            ("trap 'rc=$?; : $((rc=0)); exit $rc' EXIT\npytest", "runs `exit $rc`"),
            ("trap 'rc=$?; let rc--; exit $rc' EXIT\npytest", "runs `exit $rc`"),
            ("trap 'rc=$?; printf -v rc %s 0; exit $rc' EXIT\npytest", "runs `exit $rc`"),
            ("trap 'rc=$?; for rc in 0; do :; done; exit $rc' EXIT\npytest", "runs `exit $rc`"),
            # A `'` inside a double-quoted array element is literal, so a later substitution still runs.
            ('msgs=("don\'t" "$(pytest || true)")\necho done', "command substitution"),
            # In a here-document `$'` is no quote, so a substitution after it still runs.
            ("cat <<EOF\nit costs $'5 $(pytest)\nEOF", "command substitution"),
        ],
    )
    def test_masking_shapes(self, guard, script, reason):
        findings = _findings(guard, script)
        assert any(reason in finding for finding in findings), findings

    @pytest.mark.parametrize(
        "script",
        [
            'pytest || { rc=$?; echo failed; exit "$rc"; }\necho after',
            "pytest || exit $?\necho after",
            "{ pytest && echo ok; } || false\necho after",
            "echo start\n{ ruff check . && ruff format --check .; }",
            "bash -euo pipefail -c 'pytest | tee /tmp/log'",
            "eval 'pytest'\necho after",
            "trap 'rm -f \"$x\"' EXIT\npytest",
            "trap 'exit $?' EXIT\npytest",
            "cat <<'EOF'\n$(pytest)\nEOF",
            "coverage run -m pytest",
            "xvfb-run -a pytest",
            "pip install pytest ruff\npython -m pip install mypy",
            "cd forgelm\nls forgelm",
            'for tpl in a b; do forgelm --config "$tpl" --dry-run; done 2>&1',
            "(( x = 1 << 2 ))\npytest",
            "case x in x) echo a ;& y) echo b ;; esac\npytest",
            "v=$(case x in x) echo y;; esac)\npytest",
            "( cd forgelm && pytest ) 2>&1",
            # Second review round: trap actions that keep the step's status.
            "trap 'rc=$?; echo bye; exit $rc' EXIT\npytest",
            "trap 'echo bye; exit' EXIT\npytest",
            "trap 'rm -f x || exit $?' EXIT\npytest",
            "trap 'exit 1' EXIT\npytest",
            "cleanup() { rm -f x; }\ntrap cleanup EXIT\npytest",
            "cleanup() { exit $?; }\ntrap cleanup EXIT\npytest",
            "cleanup() { rc=$?; rm -f x; exit $rc; }\ntrap cleanup EXIT\npytest",
            'eval "$(ssh-agent -s)"\npytest',
            # An exit that cannot pass, a conditional skip, and `exec` that only redirects.
            "exit 1\npytest",
            '[ -n "$SKIP" ] && exit 0\npytest',
            "exec 2>&1\npytest",
            "alias ll='ls -l'\npytest",
            # Shapes that used to be misread.
            "set -- +e\npytest\necho done",
            "time { pytest -q; }",
            "time (cd forgelm && pytest)",
            "x=$(echo case)\npytest",
            "v=$(git tag | grep case)\npytest",
            "for ((i=0; i<2; i++)); do echo $i; done\npytest",
            "a=('$(not run)')\npytest",
            "pipx run --spec ruff==0.5 ruff check .",
            "uv tool install ruff\nuvx ruff check .",
            # Commands that name a required program without running it.
            "grep -q ruff pyproject.toml\nmkdir -p reports/pytest\nrm -rf ruff\npytest",
            "${{ runner.temp }}/venv/bin/pip install x\npytest",
            # A downloaded installer, a syntax check and a virtualenv outside the repository.
            "curl -LsSf https://astral.sh/uv/install.sh | sh\npytest",
            "curl -fsSL https://example.com/install.sh | sh -s -- -y\npytest",
            "bash -n ci/check.sh\npytest",
            "source .venv/bin/activate\npytest",
            "source /opt/conda/etc/profile.d/conda.sh\npytest",
            # An `if` whose failure branch ends the step with a non-zero status.
            "if ! pytest; then exit 1; fi\necho done",
            "if ! ruff check .; then\n  echo '::error::lint failed'\n  exit 1\nfi\necho done",
            "if pytest; then echo ok; else exit 1; fi\necho done",
            "if pytest; then :; else exit; fi\necho done",
            "if pytest; then :; else rc=$?; echo failed; exit $rc; fi\necho done",
            # ERR and EXIT traps that keep the failing status.
            "trap 'exit' ERR\npytest\necho done",
            "trap 'rc=$?; echo x; exit $rc' ERR\npytest\necho done",
            "trap 'rc=$?; echo \"rc is $rc\"; exit $rc' EXIT\npytest",
            # A glob list runs at least once without nullglob; `{fd}>file` only opens a descriptor.
            'for f in tests/*.py; do pytest "$f"; done\necho done',
            "exec {fd}>log\npytest\necho done",
            # A tool's version or help, a byte-compile, an installer read through bash's `-` or `<(…)`.
            "pytest --version | head -1",
            "python3 -m pytest --version | head -1",
            "python3 -m py_compile tools/check_bandit.py",
            "curl -fsSL https://example.com/i.sh | sudo bash -\npytest",
            "bash <(curl -fsSL https://example.com/i.sh)\npytest",
            "uvx ruff@0.8.0 check .",
            "printf $'%s\\n' x\npytest",
            # A loop whose `exit 1` only reports, and an `if` gate whose passing branch only reports.
            'for t in a b; do forgelm --config "$t" --dry-run || exit 1; done',
            "if pytest; then echo '::notice::tests passed'; else exit 1; fi\necho done",
        ],
    )
    def test_shapes_that_propagate(self, guard, script):
        assert not _findings(guard, script)

    def test_compound_redirections_parse(self, guard):
        assert not _findings(guard, "{ ruff check .; pytest -q; } 2>&1 | tee ci.log", guard.ShellOptions(True, True))

    def test_a_script_file_from_the_repository_is_read(self, guard, tmp_path):
        (tmp_path / "ci").mkdir()
        (tmp_path / "ci" / "test.sh").write_text("pytest -q || true\n", encoding="utf-8")
        findings, required = guard.scan_script("bash ci/test.sh", "run", guard.GITHUB_DEFAULT, tmp_path)
        assert findings and "is masked by `|| true`" in findings[0] and required == ["pytest"]
        missing, _ = guard.scan_script("bash ci/missing.sh", "run", guard.GITHUB_DEFAULT, tmp_path)
        assert missing and "cannot find in the repository" in missing[0]

    @pytest.mark.parametrize("command", ["./ci/test.sh", "ci/test.sh", "source ci/test.sh", ". ci/test.sh"])
    def test_a_repository_script_run_or_sourced_is_read(self, guard, tmp_path, command):
        (tmp_path / "ci").mkdir()
        (tmp_path / "ci" / "test.sh").write_text("#!/usr/bin/env bash\npytest -q || true\n", encoding="utf-8")
        findings, required = guard.scan_script(command, "run", guard.GITHUB_DEFAULT, tmp_path)
        assert findings and "(ci/test.sh line 2)" in findings[0] and "is masked by" in findings[0]
        assert required == ["pytest"]

    def test_a_script_runs_with_its_shebang_options_and_a_sourced_one_with_ours(self, guard, tmp_path):
        (tmp_path / "ci").mkdir()
        (tmp_path / "ci" / "strict.sh").write_text("#!/bin/bash -e\npytest -q\necho done\n", encoding="utf-8")
        (tmp_path / "ci" / "env.sh").write_text("set +e\n", encoding="utf-8")
        (tmp_path / "ci" / "report.py").write_text("#!/usr/bin/env python3\nprint('pytest')\n", encoding="utf-8")

        def scan(script):
            return guard.scan_script(script, "run", guard.GITHUB_DEFAULT, tmp_path)[0]

        assert scan("./ci/strict.sh") == []
        assert "errexit is off" in scan("bash ci/strict.sh")[0]  # `bash FILE` ignores the shebang
        assert "errexit is off" in scan("source ci/env.sh\npytest\necho done")[0]
        assert scan("./ci/report.py") == []
        assert scan("bash -n ci/strict.sh") == []

    @pytest.mark.parametrize(
        "command",
        ['"$GITHUB_WORKSPACE/ci/test.sh"', '"${GITHUB_WORKSPACE}/ci/test.sh"', 'bash "$GITHUB_WORKSPACE/ci/test.sh"'],
    )
    def test_a_script_under_the_workspace_is_read_from_the_repository_root(self, guard, tmp_path, command):
        (tmp_path / "ci").mkdir()
        (tmp_path / "ci" / "test.sh").write_text("#!/bin/bash\npytest -q || true\n", encoding="utf-8")
        findings, required = guard.scan_script(command, "run", guard.GITHUB_DEFAULT, tmp_path, cwd="docs")
        assert findings and "(ci/test.sh line 2)" in findings[0] and "is masked by" in findings[0]
        assert required == ["pytest"]

    @pytest.mark.parametrize(
        "command", ["cd ci && ./test.sh", "./scripts/missing.sh", "source scripts/env.sh\npytest", "./x.bash"]
    )
    def test_a_repository_script_the_guard_cannot_find_is_a_finding(self, guard, tmp_path, command):
        (tmp_path / "ci").mkdir()
        (tmp_path / "ci" / "test.sh").write_text("#!/bin/bash\npytest -q\n", encoding="utf-8")
        findings, _ = guard.scan_script(command, "run", guard.GITHUB_DEFAULT, tmp_path)
        assert any("cannot find in the repository" in finding for finding in findings), findings

    @pytest.mark.parametrize(
        "command",
        [
            "curl -fsSL https://example.com/data | bash ci/test.sh",
            "bash ci/test.sh <(curl -fsSL https://example.com/data)",
        ],
    )
    def test_a_repository_script_fed_a_download_is_still_read(self, guard, tmp_path, command):
        (tmp_path / "ci").mkdir()
        (tmp_path / "ci" / "test.sh").write_text("pytest || true\n", encoding="utf-8")
        findings, _ = guard.scan_script(command, "run", guard.GITHUB_DEFAULT, tmp_path)
        assert findings and "(ci/test.sh line 1): `pytest` is masked by" in findings[0], findings

    @pytest.mark.parametrize("ending", ["set -n\n", "echo ready\nexit 0\n", "exec true\n"])
    def test_a_sourced_script_that_stops_the_shell_stops_the_step(self, guard, tmp_path, ending):
        (tmp_path / "ci").mkdir()
        (tmp_path / "ci" / "setup.sh").write_text(ending, encoding="utf-8")
        findings, _ = guard.scan_script("source ci/setup.sh\npytest", "run", guard.GITHUB_DEFAULT, tmp_path)
        assert findings and "never runs: `source ci/setup.sh`" in findings[0], findings

    def test_a_script_run_twice_is_parsed_once_and_reported_once(self, guard, tmp_path):
        (tmp_path / "ci").mkdir()
        (tmp_path / "ci" / "env.sh").write_text("v=$(git describe || true)\n", encoding="utf-8")
        findings, _ = guard.scan_script(
            "source ci/env.sh\nsource ci/env.sh\npytest", "run", guard.GITHUB_DEFAULT, tmp_path
        )
        assert len(findings) == 1 and "(ci/env.sh line 1)" in findings[0], findings

    def test_repeated_scans_agree(self, guard):
        """The analysis caches by id(); a tree freed mid-scan let its ids be reused and the verdicts drift."""
        script = "trap 'echo a' EXIT\ntrap 'bash -c \"pytest\"' EXIT\ntrap 'echo b' EXIT"
        results = {tuple(_findings(guard, script)) for _ in range(20)}
        assert len(results) == 1 and "inside an EXIT/ERR trap" in next(iter(results))[0], results

    @pytest.mark.parametrize("path", ["a" * 300 + ".sh", "a\x00b.sh", "loop/x.sh"])
    def test_an_unreadable_script_name_is_a_finding_not_a_crash(self, guard, tmp_path, path):
        (tmp_path / "loop").symlink_to(tmp_path / "loop")
        findings, _ = guard.scan_script(f"bash '{path}'", "run", guard.GITHUB_DEFAULT, tmp_path)
        assert findings and "cannot find in the repository" in findings[0]

    def test_deep_nesting_is_a_finding_not_a_crash(self, guard):
        script = "{ " * 400 + "pytest" + "; }" * 400
        findings = _findings(guard, script)
        assert findings and "nests too deeply" in findings[0]

    def test_unparseable_steps_fail_closed_only_when_they_name_a_required_command(self, guard):
        lenient = guard.scan_steps([{"run": "echo 'unterminated"}], "w", {"runs-on": "ubuntu-latest"}, None)[0]
        strict = guard.scan_steps([{"run": "pytest 'unterminated"}], "w", {"runs-on": "ubuntu-latest"}, None)[0]
        assert lenient == [] and strict and "could not parse" in strict[0]


class TestOrTrueOutsideRequiredCommands:
    def test_any_command_masked_with_or_true_is_reported(self, guard):
        findings = _findings(guard, "pip install foo || true")
        assert findings and "docs/standards/testing.md" in findings[0]

    def test_or_true_inside_a_nested_shell_is_reported(self, guard):
        findings = _findings(guard, "bash -c 'pip install foo || true'")
        assert len(findings) == 1 and "`pip install foo` is followed by `|| true`" in findings[0], findings

    @pytest.mark.parametrize(
        "script",
        [
            "bandit -c pyproject.toml -r forgelm/ -f json -o /tmp/bandit.json || true\n"
            "python3 tools/check_bandit.py /tmp/bandit.json",
            "pip-audit \\\n  --format json \\\n  --output /tmp/pip-audit.json \\\n  || true\n"
            "python3 tools/check_pip_audit.py /tmp/pip-audit.json \\\n  --ignores tools/pip_audit_ignores.yaml",
            "pip-audit --format json > /tmp/audit.json || true\n"
            "python3 tools/check_pip_audit.py --ignores tools/pip_audit_ignores.yaml /tmp/audit.json",
            "python3 -m bandit -r forgelm -f json -o /tmp/b.json || true\npython3 tools/check_bandit.py /tmp/b.json",
            # A new option on the helper, wherever it goes, does not hide the report it reads.
            "pip-audit -o /tmp/a.json || true\npython3 tools/check_pip_audit.py --fail-on high /tmp/a.json",
        ],
    )
    def test_the_scanner_and_severity_helper_pattern_is_sanctioned(self, guard, script):
        assert not _findings(guard, script)

    def test_the_gauntlet_brace_group_form_is_sanctioned(self, guard):
        script = (
            "B=$(mktemp) && trap 'rm -f \"$B\"' EXIT && \\\n"
            '  { bandit -c pyproject.toml -r forgelm/ -f json -o "$B" || true; } && \\\n'
            '  python3 tools/check_bandit.py "$B"'
        )
        assert not _findings(guard, script, guard.INTERACTIVE)

    @pytest.mark.parametrize(
        "script,problem",
        [
            ("bandit -r forgelm -f json -o /tmp/b.json || true", "not immediately followed"),
            (
                "bandit -r forgelm -f json -o /tmp/b.json || true\necho hi\npython3 tools/check_bandit.py /tmp/b.json",
                "followed by `echo hi`",
            ),
            (
                "bandit -r forgelm -f json -o /tmp/b.json || true\npython3 tools/check_bandit.py /tmp/other.json",
                "reads /tmp/other.json",
            ),
            (
                "bandit -r forgelm -f json -o /tmp/b.json || true\npython3 tools/check_pip_audit.py /tmp/b.json",
                "instead of",
            ),
            ("bandit -r forgelm -f json || true\npython3 tools/check_bandit.py /tmp/b.json", "writes no report"),
            ("bandit -r forgelm -f json -o /tmp/b.json || :\npython3 tools/check_bandit.py /tmp/b.json", "exactly"),
        ],
    )
    def test_the_exception_requires_the_helper_to_gate_the_same_report(self, guard, script, problem):
        findings = _findings(guard, script)
        assert findings and problem in findings[0], findings

    def test_a_required_command_never_qualifies_as_a_scanner(self, guard):
        findings = _findings(guard, "pytest -q || true\npython3 tools/check_bandit.py /tmp/b.json")
        assert findings and "is masked by `|| true`" in findings[0]


class TestParsing:
    @pytest.mark.parametrize("script", ["echo 'unterminated", "if true; then pytest", "python3 - <<'PY'\nprint(1)\n"])
    def test_an_unparseable_script_is_a_finding(self, guard, script):
        findings = _findings(guard, script)
        assert findings and "could not parse" in findings[0]

    def test_github_expressions_are_replaced_before_parsing(self, guard):
        assert not _findings(guard, "python tools/generate_sbom.py > sbom-${{ matrix.os }}.json")


def _workflow(steps, job_extra="", top="", runs_on="ubuntu-latest"):
    return f"name: t\n{top}on: push\njobs:\n  j:\n    runs-on: {runs_on}\n{job_extra}    steps:\n{steps}"


class TestWorkflows:
    def test_step_continue_on_error_on_a_required_command(self, guard):
        text = _workflow("      - name: tests\n        continue-on-error: true\n        run: pytest\n")
        findings, _ = guard.scan_workflow(text, "w.yml")
        assert findings and "continue-on-error" in findings[0]

    def test_job_continue_on_error_and_expressions_count(self, guard):
        text = _workflow("      - run: pytest\n", job_extra="    continue-on-error: ${{ matrix.experimental }}\n")
        findings, _ = guard.scan_workflow(text, "w.yml")
        assert findings and "continue-on-error" in findings[0]

    def test_continue_on_error_on_an_ordinary_step_is_allowed(self, guard):
        text = _workflow("      - continue-on-error: true\n        run: echo upload-best-effort\n")
        assert guard.scan_workflow(text, "w.yml")[0] == []

    def test_explicit_bash_turns_on_pipefail(self, guard):
        assert guard.scan_workflow(_workflow("      - shell: bash\n        run: pytest | tee log\n"), "w.yml")[0] == []
        findings = guard.scan_workflow(_workflow("      - run: pytest | tee log\n"), "w.yml")[0]
        assert len(findings) == 1 and "(`run:` block line 1): `pytest` feeds a pipe" in findings[0], findings

    def test_workflow_and_job_defaults_apply(self, guard):
        top = "defaults:\n  run:\n    shell: bash\n"
        assert guard.scan_workflow(_workflow("      - run: pytest | tee log\n", top=top), "w.yml")[0] == []

    def test_a_custom_shell_without_errexit(self, guard):
        text = _workflow("      - shell: bash {0}\n        run: |\n          pytest\n          echo done\n")
        findings, _ = guard.scan_workflow(text, "w.yml")
        assert findings and "errexit is off" in findings[0]

    def test_a_required_command_under_an_unmodelled_shell(self, guard):
        text = _workflow("      - shell: pwsh\n        run: pytest\n")
        findings, _ = guard.scan_workflow(text, "w.yml")
        assert findings and "shell: pwsh" in findings[0]

    def test_the_windows_default_shell_needs_an_explicit_bash(self, guard):
        matrix = "    strategy:\n      matrix:\n        os: [ubuntu-latest, windows-latest]\n"
        default = _workflow("      - run: pytest\n", job_extra=matrix, runs_on="${{ matrix.os }}")
        explicit = _workflow("      - shell: bash\n        run: pytest\n", job_extra=matrix, runs_on="${{ matrix.os }}")
        assert "the default shell of a Windows or unresolved runner" in guard.scan_workflow(default, "w.yml")[0][0]
        assert guard.scan_workflow(explicit, "w.yml")[0] == []

    def test_malformed_yaml_and_shapes_are_findings(self, guard):
        assert "could not parse the YAML" in guard.scan_workflow("jobs: [unclosed", "w.yml")[0][0]
        assert "`jobs` is not a mapping" in guard.scan_workflow("jobs: [a, b]", "w.yml")[0][0]
        assert "has no list of steps" in guard.scan_workflow("jobs:\n  j: 3\n", "w.yml")[0][0]

    def test_the_windows_default_shell_covers_every_required_command(self, guard):
        text = _workflow(
            "      - run: |\n          python tools/update_site_version.py --check\n          echo done\n",
            runs_on="windows-latest",
        )
        assert (
            "runs a required command under the default shell of a Windows" in guard.scan_workflow(text, "w.yml")[0][0]
        )

    def test_composite_action_steps_are_scanned(self, guard):
        text = "name: a\nruns:\n  using: composite\n  steps:\n    - shell: bash\n      run: pytest || true\n"
        findings = guard.scan_workflow(text, "action.yml")[0]
        assert findings == [
            "action.yml: composite action, step '#1' (`run:` block line 1): `pytest` is masked by `|| true`, which "
            "can exit 0"
        ]

    def test_job_defaults_override_workflow_defaults(self, guard):
        def scan(top_shell, job_shell):
            top = f"defaults:\n  run:\n    shell: {top_shell}\n"
            job = f"    defaults:\n      run:\n        shell: {job_shell}\n"
            return guard.scan_workflow(_workflow("      - run: pytest | tee log\n", job_extra=job, top=top), "w.yml")[0]

        assert "pipefail" in scan("bash", "sh")[0]
        assert scan("sh", "bash") == []

    def test_shell_sh_keeps_errexit_but_has_no_pipefail(self, guard):
        assert guard.scan_workflow(_workflow("      - shell: sh\n        run: pytest\n"), "w.yml")[0] == []
        piped = _workflow("      - shell: sh\n        run: pytest | tee log\n")
        assert "pipefail" in guard.scan_workflow(piped, "w.yml")[0][0]

    def test_a_windows_runner_added_by_matrix_include(self, guard):
        matrix = "    strategy:\n      matrix:\n        os: [ubuntu-latest]\n        include:\n          - os: windows-latest\n"
        text = _workflow("      - run: pytest\n", job_extra=matrix, runs_on="${{ matrix.os }}")
        assert "Windows" in guard.scan_workflow(text, "w.yml")[0][0]

    def test_a_disabled_step_or_job_that_runs_a_required_command_is_a_finding(self, guard):
        step = _workflow("      - if: false\n        run: pytest || true\n")
        job = _workflow("      - run: pytest\n", job_extra="    if: ${{ false }}\n")
        prose = _workflow("      - if: false\n        run: echo 'not a check'\n")
        findings, required = guard.scan_workflow(step, "w.yml")
        assert required == [] and len(findings) == 1 and "`if: false` keeps pytest from ever running" in findings[0]
        findings, required = guard.scan_workflow(job, "w.yml")
        assert required == [] and len(findings) == 1 and "job 'j': `if: false` keeps pytest" in findings[0]
        assert guard.scan_workflow(prose, "w.yml") == ([], [])

    def test_a_matrix_value_that_pastes_shell_syntax(self, guard):
        matrix = "    strategy:\n      matrix:\n        include:\n          - extra: '|| true'\n"
        findings, _ = guard.scan_workflow(
            _workflow("      - run: pytest -q ${{ matrix.extra }}\n", job_extra=matrix), "w.yml"
        )
        assert findings and "matrix.extra" in findings[0] and "'|| true'" in findings[0]

    def test_working_directory_and_the_workspace_expression_find_repository_scripts(self, guard, tmp_path):
        (tmp_path / "ci").mkdir()
        (tmp_path / "ci" / "t.sh").write_text("#!/bin/bash\npytest || true\n", encoding="utf-8")
        for steps, job_extra in (
            ("      - working-directory: ci\n        run: ./t.sh\n", ""),
            ("      - run: ./t.sh\n", "    defaults:\n      run:\n        working-directory: ci\n"),
            ("      - run: ${{ github.workspace }}/ci/t.sh\n", ""),
            ("      - working-directory: ${{ github.workspace }}/ci\n        run: ./t.sh\n", ""),
        ):
            findings, _ = guard.scan_workflow(_workflow(steps, job_extra=job_extra), "w.yml", tmp_path)
            assert len(findings) == 1 and "(ci/t.sh line 2): `pytest` is masked by" in findings[0], (steps, findings)
        hidden = _workflow("      - working-directory: ${{ inputs.dir }}\n        run: ./t.sh\n")
        assert "cannot find in the repository" in guard.scan_workflow(hidden, "w.yml", tmp_path)[0][0]

    def test_a_matrix_value_referenced_with_brackets(self, guard):
        matrix = "    strategy:\n      matrix:\n        args: ['-q', '-q || true']\n"
        findings, _ = guard.scan_workflow(
            _workflow("      - run: pytest ${{ matrix['args'] }}\n", job_extra=matrix), "w.yml"
        )
        assert findings and "matrix.args" in findings[0] and "'-q || true'" in findings[0]

    def test_a_matrix_built_from_nested_aliases_is_read_within_a_budget(self, guard):
        # 100 scalars, then three levels of 100 aliases each: 10**8 values within the depth limit.
        anchors = "x1: &x1 [" + ", ".join(["ubuntu-latest"] * 100) + "]\n"
        anchors += "".join(f"x{i}: &x{i} [" + ", ".join([f"*x{i - 1}"] * 100) + "]\n" for i in range(2, 5))
        text = anchors + _workflow(
            "      - run: pytest\n",
            job_extra="    strategy:\n      matrix:\n        os: *x4\n",
            runs_on="${{ matrix.os }}",
        ).replace("name: t\n", "")
        started = time.monotonic()
        assert guard.scan_workflow(text, "w.yml")[0] == []
        assert time.monotonic() - started < 5, "matrix values must be read within a budget"

    def test_yaml_values_that_break_the_loader_are_findings(self, guard):
        assert "could not parse the YAML" in guard.scan_workflow("on: push\nx: 2023-13-45\n", "w.yml")[0][0]
        assert "could not parse the YAML" in guard.scan_workflow("[" * 3000, "w.yml")[0][0]


class TestGauntlets:
    def test_the_bash_fence_between_the_markers_is_extracted(self, guard):
        doc = "x\n<!-- gauntlet:begin -->\n```bash\npytest tests/\n```\n<!-- gauntlet:end -->\n"
        assert guard.gauntlet_script(doc).strip() == "pytest tests/"
        assert guard.gauntlet_script("no markers") is None

    def test_backticks_inside_a_line_do_not_end_the_block(self, guard):
        doc = (
            "<!-- gauntlet:begin -->\n   ```bash\n   pytest tests/ && \\\n     # see ```docs``` \\\n"
            "     ruff check . | cat\n   ```\n<!-- gauntlet:end -->\n"
        )
        assert "ruff check . | cat" in guard.gauntlet_script(doc)
        findings = _findings(guard, guard.gauntlet_script(doc), guard.INTERACTIVE)
        assert any("(script line 3): `ruff check .` feeds a pipe" in finding for finding in findings), findings

    def test_the_first_closing_fence_ends_the_block(self, guard):
        doc = (
            "<!-- gauntlet:begin -->\n```bash\npytest tests/\n```\nprose\n```\nnot shell\n```\n<!-- gauntlet:end -->\n"
        )
        assert guard.gauntlet_script(doc).strip() == "pytest tests/"

    @pytest.mark.parametrize(
        "script,reason",
        [
            (
                "ruff check . && \\\n  { pytest tests/ || true; } && \\\n  node --test tests/site/*.test.cjs",
                "`pytest tests/` is masked by `|| true`",
            ),
            ("ruff check . && pytest tests/ || :", "`pytest tests/` is masked by `|| :`"),
            ("ruff check . && pytest tests/ || exit 0", "`pytest tests/` is masked by `|| exit 0`"),
            ("ruff check .; pytest tests/", "`ruff check .` can fail without stopping the script"),
        ],
    )
    def test_masked_gauntlets_are_reported(self, guard, script, reason):
        assert any(reason in finding for finding in _findings(guard, script, guard.INTERACTIVE))


def _copy_repo_surfaces(guard, tmp_path: Path) -> Path:
    """Copy the scanned surfaces of the real repository into *tmp_path*.

    The copy must scan exactly like the checkout before a test mutates it, or a
    finding the copy alone causes (a repository script a workflow runs but the
    copy lacks) would satisfy a test that expects the mutation to be caught.
    """
    for path in (_REPO_ROOT / ".github").rglob("*.y*ml"):
        target = tmp_path / path.relative_to(_REPO_ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(path, target)
    for doc in ("CLAUDE.md", "AGENTS.md", "CONTRIBUTING.md"):
        shutil.copy(_REPO_ROOT / doc, tmp_path / doc)
    findings, _surfaces, required = guard.scan_repo(tmp_path)
    assert findings == [] and required == guard.scan_repo(_REPO_ROOT)[2], (
        f"the copy scans differently from the checkout; copy what the workflows read: {findings}"
    )
    return tmp_path


def _mutate(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert text.count(old) == 1, f"mutation anchor not unique in {path.name}: {old!r}"
    path.write_text(text.replace(old, new), encoding="utf-8")


class TestRealRepository:
    def test_the_checkout_is_clean(self, guard, capsys):
        assert guard.main(["--root", str(_REPO_ROOT)]) == 0
        assert "propagate failure" in capsys.readouterr().out

    def test_the_gauntlet_flags_are_accepted(self, guard, capsys):
        assert guard.main(["--strict", "--quiet", "--root", str(_REPO_ROOT)]) == 0
        assert capsys.readouterr().out == ""

    def test_the_issue_reproduction_is_caught(self, guard, tmp_path, capsys):
        """Appending `|| true` to the validation job's Node test command (issue #951)."""
        root = _copy_repo_surfaces(guard, tmp_path)
        _mutate(
            root / ".github/workflows/ci.yml",
            "run: node --test tests/site/*.test.cjs\n",
            "run: node --test tests/site/*.test.cjs || true\n",
        )
        assert guard.main(["--root", str(root)]) == 1
        assert "node --test tests/site/*.test.cjs" in capsys.readouterr().out

    @pytest.mark.parametrize(
        "path,old,new,expected",
        [
            (
                ".github/workflows/ci.yml",
                "run: pytest -q --tb=short --cov-report=xml:coverage.xml\n",
                "run: pytest -q --tb=short --cov-report=xml:coverage.xml | tee pytest.log\n",
                "feeds a pipe without `pipefail`",
            ),
            (".github/workflows/ci.yml", "run: ruff check .\n", "run: ruff check . || exit 0\n", "`|| exit 0`"),
            (
                ".github/workflows/ci.yml",
                "run: python3 tools/check_site_claims.py --strict\n",
                "continue-on-error: true\n        run: python3 tools/check_site_claims.py --strict\n",
                "`continue-on-error` lets tools/check_site_claims.py fail",
            ),
            (
                ".github/workflows/ci.yml",
                "          python3 tools/check_bandit.py /tmp/bandit.json\n",
                "          python3 tools/check_bandit.py /tmp/elsewhere.json\n",
                "reads /tmp/elsewhere.json",
            ),
            (
                ".github/workflows/nightly.yml",
                "        run: pytest tests/ -q --tb=short\n\n      - name: CLI smoke test",
                "        run: |\n          set +e\n          pytest tests/ -q --tb=short\n          echo done\n\n"
                "      - name: CLI smoke test",
                "errexit is off",
            ),
        ],
        ids=["pipe-without-pipefail", "or-exit-0", "continue-on-error", "helper-reads-another-report", "set-plus-e"],
    )
    def test_workflow_mutations_are_caught(self, guard, tmp_path, capsys, path, old, new, expected):
        root = _copy_repo_surfaces(guard, tmp_path)
        _mutate(root / path, old, new)
        assert guard.main(["--root", str(root)]) == 1
        assert expected in capsys.readouterr().out

    def test_a_gauntlet_mutation_applied_to_all_three_documents_is_caught(self, guard, tmp_path, capsys):
        """The meta-test compares the three blocks; a consistent mutation needs this guard."""
        root = _copy_repo_surfaces(guard, tmp_path)
        for doc in ("CLAUDE.md", "AGENTS.md", "CONTRIBUTING.md"):
            _mutate(
                root / doc, "ruff check . && pytest tests/ && \\", "ruff check . && { pytest tests/ || true; } && \\"
            )
        assert guard.main(["--root", str(root)]) == 1
        findings = capsys.readouterr().out.splitlines()
        for doc in ("CLAUDE.md", "AGENTS.md", "CONTRIBUTING.md"):
            assert any(
                f"{doc} gauntlet" in finding and "`pytest tests/` is masked by `|| true`" in finding
                for finding in findings
            ), f"{doc}: {findings}"

    def test_yaml_workflows_and_local_actions_are_scanned(self, guard, tmp_path, capsys):
        root = _copy_repo_surfaces(guard, tmp_path)
        (root / ".github/workflows/extra.yaml").write_text(
            "on: push\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n      - run: pytest -q || true\n",
            encoding="utf-8",
        )
        assert guard.main(["--root", str(root)]) == 1
        assert "extra.yaml" in capsys.readouterr().out
        (root / ".github/workflows/extra.yaml").unlink()
        action = root / ".github/actions/tests"
        action.mkdir(parents=True)
        (action / "action.yml").write_text(
            "runs:\n  using: composite\n  steps:\n    - shell: bash\n      run: pytest -q\n", encoding="utf-8"
        )
        (root / ".github/workflows/uses.yml").write_text(
            "on: push\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n"
            "      - uses: ./.github/actions/tests\n        continue-on-error: true\n",
            encoding="utf-8",
        )
        assert guard.main(["--root", str(root)]) == 1
        assert "`continue-on-error` lets ./.github/actions/tests, which runs pytest" in capsys.readouterr().out

    def test_a_masked_only_invocation_is_not_also_reported_as_missing(self, guard, tmp_path, capsys):
        root = _copy_repo_surfaces(guard, tmp_path)
        _mutate(
            root / ".github/workflows/ci.yml",
            "run: pytest -q --tb=short --cov-report=xml:coverage.xml\n",
            "run: pytest -q --tb=short --cov-report=xml:coverage.xml || true\n",
        )
        assert guard.main(["--root", str(root)]) == 1
        assert "invocation recognised" not in capsys.readouterr().out

    def test_a_scan_that_recognises_no_required_command_fails(self, guard, tmp_path, capsys):
        root = _copy_repo_surfaces(guard, tmp_path)
        (root / ".github/workflows/ci.yml").write_text("name: t\non: push\njobs: {}\n", encoding="utf-8")
        assert guard.main(["--root", str(root)]) == 1
        assert "ci.yml: no pytest, node --test, ruff, tools/check_ invocation recognised" in capsys.readouterr().out

    def test_a_renamed_ci_workflow_keeps_its_inventory_check(self, guard, tmp_path, capsys):
        root = _copy_repo_surfaces(guard, tmp_path)
        (root / ".github/workflows/ci.yml").unlink()
        (root / ".github/workflows/ci.yaml").write_text("name: t\non: push\njobs: {}\n", encoding="utf-8")
        assert guard.main(["--root", str(root)]) == 1
        assert "ci.yaml: no pytest" in capsys.readouterr().out

    def test_a_ci_workflow_renamed_to_anything_else_is_a_finding(self, guard, tmp_path, capsys):
        root = _copy_repo_surfaces(guard, tmp_path)
        (root / ".github/workflows/ci.yml").rename(root / ".github/workflows/tests.yml")
        assert guard.main(["--root", str(root)]) == 1
        assert "no ci.yml under .github/workflows" in capsys.readouterr().out

    def test_a_gauntlet_that_stops_running_a_kind_is_a_finding(self, guard, tmp_path, capsys):
        root = _copy_repo_surfaces(guard, tmp_path)
        _mutate(root / "CLAUDE.md", "ruff check . && pytest tests/ && \\", "ruff check . && \\")
        assert guard.main(["--root", str(root)]) == 1
        assert "CLAUDE.md gauntlet: no pytest invocation recognised" in capsys.readouterr().out

    def test_a_repository_without_workflows_is_a_finding(self, guard, tmp_path, capsys):
        for doc in ("CLAUDE.md", "AGENTS.md", "CONTRIBUTING.md"):
            shutil.copy(_REPO_ROOT / doc, tmp_path / doc)
        assert guard.main(["--root", str(tmp_path)]) == 1
        assert "a scan of nothing would pass" in capsys.readouterr().out

    def test_unreadable_and_missing_surfaces_are_findings_not_crashes(self, guard, tmp_path, capsys):
        root = _copy_repo_surfaces(guard, tmp_path)
        (root / ".github/workflows/broken.yml").write_bytes(b"on: push\n\xff\xfe\n")
        (root / "AGENTS.md").unlink()
        assert guard.main(["--root", str(root)]) == 1
        out = capsys.readouterr().out
        assert "broken.yml: could not be read (UnicodeDecodeError)" in out
        assert "AGENTS.md: could not be read (FileNotFoundError)" in out

    def test_local_actions_are_scanned_wherever_they_live(self, guard, tmp_path, capsys):
        masked = "runs:\n  using: composite\n  steps:\n    - shell: bash\n      run: pytest -q || true\n"
        root = _copy_repo_surfaces(guard, tmp_path)
        nested = root / ".github/actions/group/tests"
        nested.mkdir(parents=True)
        (nested / "action.yml").write_text(masked, encoding="utf-8")
        assert guard.main(["--root", str(root)]) == 1
        assert ".github/actions/group/tests/action.yml" in capsys.readouterr().out

        root = _copy_repo_surfaces(guard, tmp_path / "outside")
        (root / "ci-action").mkdir()
        (root / "ci-action/action.yml").write_text(masked, encoding="utf-8")
        (root / ".github/workflows/uses.yml").write_text(
            "on: push\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: ./ci-action\n"
            "      - uses: ./missing-action\n",
            encoding="utf-8",
        )
        assert guard.main(["--root", str(root)]) == 1
        out = capsys.readouterr().out
        assert "ci-action/action.yml" in out and "`uses: ./missing-action` names no action.yml" in out

    @pytest.mark.parametrize("uses", ["./acts/outer", "./acts/../acts/inner/"])
    def test_continue_on_error_is_caught_through_nested_and_unusual_action_paths(self, guard, tmp_path, capsys, uses):
        root = _copy_repo_surfaces(guard, tmp_path)
        for name, steps in (
            ("outer", "    - uses: ./acts/inner\n"),
            ("inner", "    - shell: bash\n      run: pytest\n"),
        ):
            (root / "acts" / name).mkdir(parents=True)
            (root / "acts" / name / "action.yml").write_text(f"runs:\n  using: composite\n  steps:\n{steps}", "utf-8")
        (root / ".github/workflows/uses.yml").write_text(
            f"on: push\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: {uses}\n"
            "        continue-on-error: true\n",
            encoding="utf-8",
        )
        assert guard.main(["--root", str(root)]) == 1
        assert f"`continue-on-error` lets {uses}, which runs pytest, fail the job silently" in capsys.readouterr().out

    def test_a_composite_action_reads_scripts_through_its_action_path(self, guard, tmp_path, capsys):
        root = _copy_repo_surfaces(guard, tmp_path)
        action = root / ".github/actions/tests"
        action.mkdir(parents=True)
        (action / "run.sh").write_text("#!/bin/bash\npytest || true\n", encoding="utf-8")
        (action / "action.yml").write_text(
            "runs:\n  using: composite\n  steps:\n    - shell: bash\n      run: ${{ github.action_path }}/run.sh\n",
            encoding="utf-8",
        )
        assert guard.main(["--root", str(root)]) == 1
        assert "(.github/actions/tests/run.sh line 2): `pytest` is masked by" in capsys.readouterr().out
        (action / "action.yml").write_text(
            "runs:\n  using: composite\n  steps:\n    - shell: bash\n"
            "      working-directory: ${{ github.action_path }}\n      run: ./run.sh\n",
            encoding="utf-8",
        )
        assert guard.main(["--root", str(root)]) == 1
        assert "(.github/actions/tests/run.sh line 2): `pytest` is masked by" in capsys.readouterr().out

    @pytest.mark.parametrize("spelling", ["relative", "symlink"])
    def test_a_root_given_another_way_scans_the_same(self, guard, tmp_path, capsys, monkeypatch, spelling):
        root = _copy_repo_surfaces(guard, tmp_path / "repo")
        (root / "acts").mkdir()
        (root / "acts/action.yml").write_text(
            "runs:\n  using: composite\n  steps:\n    - run: echo hi\n      shell: bash\n", "utf-8"
        )
        (root / ".github/workflows/uses.yml").write_text(
            "on: push\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: ./acts\n", encoding="utf-8"
        )
        if spelling == "symlink":
            (tmp_path / "link").symlink_to(root)
            given = str(tmp_path / "link")
        else:
            monkeypatch.chdir(tmp_path)
            given = "repo"
        assert guard.main(["--root", given]) == 0, capsys.readouterr().out


def _bash5() -> Optional[str]:
    """A bash of version 5 or newer on Linux or macOS, as on GitHub's Linux runners; None otherwise.

    Windows' bash (Git Bash) runs in another environment, so the comparison stays on POSIX systems.
    """
    path = shutil.which("bash") if sys.platform != "win32" else None
    if path is None:
        return None
    try:
        version = subprocess.run([path, "-c", "echo ${BASH_VERSINFO[0]}"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return path if version.stdout.strip().isdigit() and int(version.stdout) >= 5 else None


_BASH = _bash5()

# Shapes where the guard's verdict must equal bash's. Each required program is a
# stub that exits 1, and ci/t.sh masks `pytest`. A masked shape leaves the step
# with status 0 and the guard reports the expected text; any other shape exits
# with the stub's 1 and the guard reports nothing.
_AGAINST_BASH = [
    ("pytest || true", "is masked by `|| true`"),
    ("pytest || exit 1", None),
    ("pytest | tee /dev/null", "feeds a pipe without `pipefail`"),
    ("set -o pipefail\npytest | tee /dev/null", None),
    ("pytest && echo ok\necho next", "can fail without stopping the script"),
    ("{ pytest; echo done; } || true", "can fail without stopping the script"),
    ('pytest || { rc=$?; echo failed; exit "$rc"; }', None),
    ("pytest || { echo failed; exit $?; }\necho after", "is masked by"),
    ("trap 'echo bye; exit $?' EXIT\npytest", "after another command"),
    ("trap 'rc=$?; echo bye; exit $rc' EXIT\npytest", None),
    ("trap 'echo bye; exit' EXIT\npytest", None),
    ("trap -- 'exit 0' EXIT\npytest", "runs `exit 0`"),
    ("cleanup() { rm -f x; exit 0; }\ntrap cleanup EXIT\npytest", "runs `exit 0`"),
    ("pytest() { :; }\npytest", "redefines `pytest`"),
    ("exit 0\npytest", "never runs"),
    ("((pytest) || true)", "is masked by `|| true`"),
    ("eval 'set +e'\npytest\necho done", "errexit is off"),
    ("shopt -uo errexit\npytest\necho done", "errexit is off"),
    ("set -- +e\npytest\necho done", None),
    ("time { pytest; }", None),
    ("x=$(echo case)\npytest", None),
    ("cleanup() { exit $?; }\ntrap cleanup EXIT\npytest", None),
    ("cleanup() { exit $?; }\ntrap 'echo hi; cleanup' EXIT\npytest", "after another command"),
    ("OPT=+e\nset $OPT\npytest\necho done", "errexit is off"),
    ("CMD='pytest || true'\neval \"$CMD\"", "taken from the variable"),
    ("for ((i=$(pytest); i<1; i++)); do :; done\necho done", "command substitution"),
    ("! pytest", "negated"),
    ("pytest &\nwait", "background"),
    ("set +e\npytest\necho done", "errexit is off"),
    ("if pytest; then echo ok; fi", "condition"),
    # ERR traps: a bare `exit` there uses the last command's status, and `set +e` lets the step go on.
    ("trap 'echo x; exit' ERR\npytest\necho done", "bare `exit` after another command"),
    ("trap 'set +e' ERR\npytest\necho done", "turns errexit off"),
    ("trap 'exit' ERR\npytest\necho done", None),
    ("trap 'rc=$?; echo x; exit $rc' ERR\npytest\necho done", None),
    ("trap 'rc=$?; rc=0; exit $rc' EXIT\npytest", "runs `exit $rc`"),
    # `if` gates, loops over globs, descriptors, noexec, background `set`, quoting.
    ("if ! pytest; then exit 1; fi\necho done", None),
    ("if pytest; then :; else echo failed; exit 1; fi\necho done", None),
    ("if ! pytest; then exit $?; fi\necho done", "condition"),
    ('for f in nomatch/*.py; do pytest "$f"; done\necho done', None),
    ('shopt -s nullglob\nfor f in nomatch/*.py; do pytest "$f"; done\necho done', "fixed word list"),
    ("exec {fd}>log\npytest\necho done", None),
    ("set -n\npytest", "never runs"),
    ("set +e\nset -e &\nwait\npytest\necho done", "errexit is off"),
    ('echo "$\'"\npytest || true\necho "\'"', "is masked by `|| true`"),
    ("cd nosuchdir && pytest\necho done", "is skipped when `cd nosuchdir` fails"),
    ('for f in nomatch/*.py; do [ -e "$f" ] || continue; pytest "$f"; done\necho done', "`continue` or `break`"),
    ("cleanup() { rm -f x; exit 0; }\nif ! pytest; then cleanup; exit 1; fi\necho done", "condition"),
    ("trap 'rc=$?; : $((rc=0)); exit $rc' EXIT\npytest", "runs `exit $rc`"),
    ("eval 'set -n'\npytest", "never runs"),
    ('for t in a b; do pytest "$t" || exit 1; done', None),
    ('msgs=("don\'t" "$(pytest || true)")\necho done', "command substitution"),
    # Runners, process substitutions, standard input, and scripts under the workspace.
    ("uvx ruff@0.8.0 check . | tee /dev/null", "feeds a pipe without `pipefail`"),
    ("micromamba run -n env pytest | tee /dev/null", "feeds a pipe without `pipefail`"),
    ("while read -r l; do :; done < <(pytest)\necho done", "command substitution"),
    ("source /dev/stdin <<'EOF'\npytest || true\nEOF", "is masked by `|| true`"),
    ("bash - <<'EOF'\npytest || true\nEOF", "is masked by `|| true`"),
    ('"$GITHUB_WORKSPACE/ci/t.sh"', "(ci/t.sh line 2): `pytest` is masked"),
    ("cd ci && ./t.sh", "cannot find in the repository"),
]


@pytest.mark.skipif(_BASH is None, reason="needs bash 5 or newer on Linux or macOS, as on GitHub's Linux runners")
@pytest.mark.parametrize("script,expected", _AGAINST_BASH)
def test_verdicts_match_bash(guard, tmp_path, script, expected):
    """Run each shape under `bash -e`, GitHub's default step shell, with failing stubs for the required programs."""
    stubs = tmp_path / "bin"
    stubs.mkdir()
    for program in ("pytest", "ruff", "uvx", "micromamba"):
        (stubs / program).write_text("#!/bin/sh\nexit 1\n", encoding="utf-8", newline="\n")
        (stubs / program).chmod(0o755)
    (tmp_path / "ci").mkdir()
    (tmp_path / "ci" / "t.sh").write_text("#!/bin/bash\npytest || true\n", encoding="utf-8", newline="\n")
    (tmp_path / "ci" / "t.sh").chmod(0o755)
    step = tmp_path / "step.sh"
    step.write_text(script + "\n", encoding="utf-8", newline="\n")
    env = {
        "PATH": f"{stubs}{os.pathsep}{os.environ.get('PATH', '')}",
        "HOME": str(tmp_path),
        "GITHUB_WORKSPACE": str(tmp_path),
    }
    result = subprocess.run(
        [_BASH, "--noprofile", "--norc", "-e", str(step)], cwd=tmp_path, env=env, capture_output=True, timeout=30
    )
    findings = guard.scan_script(script, "run", guard.GITHUB_DEFAULT, tmp_path)[0]
    if expected is None:
        assert result.returncode == 1, (script, result.returncode, result.stderr)
        assert findings == [], findings
    else:
        assert result.returncode == 0, (script, result.returncode, result.stderr)
        assert any(expected in finding for finding in findings), findings


@pytest.mark.skipif(
    not (sys.platform.startswith("linux") and os.environ.get("CI")), reason="only CI's Linux runners must have bash 5"
)
def test_the_bash_comparison_runs_in_ci():
    """Without bash 5 the comparison above skips silently; on GitHub's Linux runners that would hide it."""
    assert _BASH is not None
