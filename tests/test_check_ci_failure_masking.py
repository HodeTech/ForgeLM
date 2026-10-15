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
import shutil
import sys
from pathlib import Path

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
        "script",
        [
            "node --test tests/site/*.test.cjs || true",
            "pytest -q --tb=short || true",
            "pytest || :",
            "pytest || exit 0",
            "pytest || exit 256",
            "pytest || echo 'tests failed'",
            "pytest && echo ok || echo failed",
            "ruff check \\\n  . \\\n  || true",
            "python3 tools/check_site_claims.py --strict || true",
            "python -m pytest || true",
            "python -mpytest || true",
            "python3 -I -m mypy --strict forgelm/__init__.py || true",
            "./tools/check_anchor_resolution.py --strict || true",
            "timeout 600 env CI=1 pytest || true",
            "uv run pytest || true",
            "forgelm --config config_template.yaml --dry-run || true",
            "{ pytest; echo done; } || true",
            "( pytest ) || true",
            "bash -c 'pytest || true'",
            "bash <<'EOF'\npytest || true\nEOF",
        ],
    )
    def test_or_fallbacks_that_can_succeed(self, guard, script):
        assert _findings(guard, script)

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


class TestOrTrueOutsideRequiredCommands:
    def test_any_command_masked_with_or_true_is_reported(self, guard):
        findings = _findings(guard, "pip install foo || true")
        assert findings and "docs/standards/testing.md" in findings[0]

    def test_or_true_inside_a_nested_shell_is_reported(self, guard):
        assert _findings(guard, "bash -c 'pip install foo || true'")

    @pytest.mark.parametrize(
        "script",
        [
            "bandit -c pyproject.toml -r forgelm/ -f json -o /tmp/bandit.json || true\n"
            "python3 tools/check_bandit.py /tmp/bandit.json",
            "pip-audit \\\n  --format json \\\n  --output /tmp/pip-audit.json \\\n  || true\n"
            "python3 tools/check_pip_audit.py /tmp/pip-audit.json \\\n  --ignores tools/pip_audit_ignores.yaml",
            "pip-audit --format json > /tmp/audit.json || true\n"
            "python3 tools/check_pip_audit.py --ignores tools/pip_audit_ignores.yaml /tmp/audit.json",
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
        assert guard.scan_workflow(_workflow("      - run: pytest | tee log\n"), "w.yml")[0]

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
        assert guard.scan_workflow(default, "w.yml")[0]
        assert guard.scan_workflow(explicit, "w.yml")[0] == []

    def test_composite_action_steps_are_scanned(self, guard):
        text = "name: a\nruns:\n  using: composite\n  steps:\n    - shell: bash\n      run: pytest || true\n"
        assert guard.scan_workflow(text, "action.yml")[0]


class TestGauntlets:
    def test_the_bash_fence_between_the_markers_is_extracted(self, guard):
        doc = "x\n<!-- gauntlet:begin -->\n```bash\npytest tests/\n```\n<!-- gauntlet:end -->\n"
        assert guard.gauntlet_script(doc).strip() == "pytest tests/"
        assert guard.gauntlet_script("no markers") is None

    @pytest.mark.parametrize(
        "script",
        [
            "ruff check . && \\\n  { pytest tests/ || true; } && \\\n  node --test tests/site/*.test.cjs",
            "ruff check . && pytest tests/ || :",
            "ruff check . && pytest tests/ || exit 0",
            "ruff check .; pytest tests/",
        ],
    )
    def test_masked_gauntlets_are_reported(self, guard, script):
        assert _findings(guard, script, guard.INTERACTIVE)


def _copy_repo_surfaces(tmp_path: Path) -> Path:
    """Copy the scanned surfaces of the real repository into *tmp_path*."""
    for path in (_REPO_ROOT / ".github").rglob("*.y*ml"):
        target = tmp_path / path.relative_to(_REPO_ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(path, target)
    for doc in ("CLAUDE.md", "AGENTS.md", "CONTRIBUTING.md"):
        shutil.copy(_REPO_ROOT / doc, tmp_path / doc)
    return tmp_path


def _mutate(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert text.count(old) == 1, f"mutation anchor not unique in {path.name}: {old!r}"
    path.write_text(text.replace(old, new), encoding="utf-8")


class TestRealRepository:
    def test_the_checkout_is_clean(self, guard, capsys):
        assert guard.main(["--root", str(_REPO_ROOT)]) == 0
        assert "propagate failure" in capsys.readouterr().out

    def test_the_issue_reproduction_is_caught(self, guard, tmp_path, capsys):
        """Appending `|| true` to the validation job's Node test command (issue #951)."""
        root = _copy_repo_surfaces(tmp_path)
        _mutate(
            root / ".github/workflows/ci.yml",
            "run: node --test tests/site/*.test.cjs\n",
            "run: node --test tests/site/*.test.cjs || true\n",
        )
        assert guard.main(["--root", str(root)]) == 1
        assert "node --test tests/site/*.test.cjs" in capsys.readouterr().out

    @pytest.mark.parametrize(
        "path,old,new",
        [
            (
                ".github/workflows/ci.yml",
                "run: pytest -q --tb=short --cov-report=xml:coverage.xml\n",
                "run: pytest -q --tb=short --cov-report=xml:coverage.xml | tee pytest.log\n",
            ),
            (".github/workflows/ci.yml", "run: ruff check .\n", "run: ruff check . || exit 0\n"),
            (
                ".github/workflows/ci.yml",
                "run: python3 tools/check_site_claims.py --strict\n",
                "continue-on-error: true\n        run: python3 tools/check_site_claims.py --strict\n",
            ),
            (
                ".github/workflows/ci.yml",
                "          python3 tools/check_bandit.py /tmp/bandit.json\n",
                "          python3 tools/check_bandit.py /tmp/elsewhere.json\n",
            ),
            (
                ".github/workflows/nightly.yml",
                "        run: pytest tests/ -q --tb=short\n\n      - name: CLI smoke test",
                "        run: |\n          set +e\n          pytest tests/ -q --tb=short\n          echo done\n\n"
                "      - name: CLI smoke test",
            ),
        ],
        ids=["pipe-without-pipefail", "or-exit-0", "continue-on-error", "helper-reads-another-report", "set-plus-e"],
    )
    def test_workflow_mutations_are_caught(self, guard, tmp_path, path, old, new):
        root = _copy_repo_surfaces(tmp_path)
        _mutate(root / path, old, new)
        assert guard.main(["--root", str(root), "--quiet"]) == 1

    def test_a_gauntlet_mutation_applied_to_all_three_documents_is_caught(self, guard, tmp_path):
        """The meta-test compares the three blocks; a consistent mutation needs this guard."""
        root = _copy_repo_surfaces(tmp_path)
        for doc in ("CLAUDE.md", "AGENTS.md", "CONTRIBUTING.md"):
            _mutate(
                root / doc, "ruff check . && pytest tests/ && \\", "ruff check . && { pytest tests/ || true; } && \\"
            )
        assert guard.main(["--root", str(root), "--quiet"]) == 1

    def test_a_scan_that_recognises_no_required_command_fails(self, guard, tmp_path, capsys):
        root = _copy_repo_surfaces(tmp_path)
        (root / ".github/workflows/ci.yml").write_text("name: t\non: push\njobs: {}\n", encoding="utf-8")
        assert guard.main(["--root", str(root)]) == 1
        assert "the scan is broken" in capsys.readouterr().out
