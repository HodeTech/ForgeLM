# Contributing to ForgeLM

Thanks for your interest in contributing! ForgeLM is an open-source project and we welcome contributions of all kinds.

## Ways to Contribute

- **Bug reports** — found something broken? [Open a bug report](https://github.com/HodeTech/ForgeLM/issues/new?template=bug_report.yml)
- **Feature requests** — have an idea? [Open a feature request](https://github.com/HodeTech/ForgeLM/issues/new?template=feature_request.yml)
- **Code** — fix a bug, add a feature, improve tests
- **Documentation** — fix typos, improve guides, add examples
- **Notebooks** — add Colab notebooks for new use cases
- **Config templates** — share training configs that worked well for you
- **Planned work** — pick an open task from the [ForgeLM Project Plan](https://github.com/orgs/HodeTech/projects/5) (see below)

## Finding Work: Issues and the Project Board

Work is tracked in two places that play different roles:

- **Issues are where the work happens.** Each issue is one unit of work: it describes the problem, links the code, and lists acceptance criteria. Discussion, claiming and pull-request links all happen on the issue.
- **The [ForgeLM Project Plan](https://github.com/orgs/HodeTech/projects/5) is where the work is planned.** It shows every issue with its priority, release target and iteration, in several views: *Backlog* (table), *Board* (by status), *Current iteration* and *Roadmap* (timeline), plus filtered views such as *Small tasks*, *Epics*, *Docs & TR mirror* and *Roadmap — Phase 16 & deferred*. Use it to choose what to work on; you never need to edit it — maintainers keep it current, and a card moves automatically when its issue is closed.

### Planned work lives here too

Every piece of planned or deferred work has an issue and a project card, not only the defects: the remaining steps of [Phase 16](docs/roadmap/phase-16-trust-surface-hardening.md) are sub-issues of [#903](https://github.com/HodeTech/ForgeLM/issues/903), and the deferred items recorded in [risks-and-decisions.md](docs/roadmap/risks-and-decisions.md) are sub-issues of [#929](https://github.com/HodeTech/ForgeLM/issues/929). New issues land in the project's *Intake — unplanned* view until a maintainer plans them. If you notice work that your pull request is not going to do, open an issue for it instead of leaving a `TODO` or a roadmap line.

### How the backlog is organised

Issues carry labels, and the project adds planning fields on top of them.

| Label | Meaning |
|---|---|
| `severity: critical` · `high` · `medium` · `low` · `info` | How serious the defect is: a broken security or safety contract, a wrong behaviour or false claim users rely on, a limited defect, a minor inaccuracy, or a hardening suggestion |
| `urgency: now` · `next-release` · `watch` | For dependency and ecosystem items, how soon they need action |
| `wave: 0` … `wave: 4` | Roadmap wave: 0 is due in the next release, 1–2 in the releases after it, 3 is medium-severity cleanup per module, 4 is low/info work done whenever the module is touched. Milestones name the wave; the release number is set when the release is cut |
| `area: <name>` | The code area, e.g. `area: cli`, `area: eval-gates`, `area: data-audit` |
| `bug` · `documentation` · `i18n` · `dependencies` | The kind of change: code, English docs, the Turkish mirror, or a dependency/ecosystem update |
| `backlog: known` | Already described in [`docs/roadmap/`](docs/roadmap/); the issue links the roadmap entry |
| `security` | Security-relevant; read [Reporting Security Vulnerabilities](#reporting-security-vulnerabilities) before discussing details |
| `needs-triage` | Not triaged yet — a new report, or a finding reviewers disagreed on; wait for a maintainer to confirm and plan it before working on it |
| `source: review-2026-10` | Found by the October 2026 code and documentation review |
| `source: review-2026-09` | Found by the September 2026 independent review |
| `source: roadmap` · `phase: 16` | Planned work recorded in [`docs/roadmap/`](docs/roadmap/): a roadmap step or a deferred item |
| `deferred` | Waits for a condition recorded in the issue; check that it holds before starting |
| `epic` | A parent issue that groups one theme or roadmap phase; its sub-issues are the work |

In the project, the **Wave**, **Severity**, **Theme**, **Kind**, **Area**, **Size** (effort: XS–XL) and **Iteration** fields let you filter and group the same issues. **Milestones** map waves to releases.

Useful starting points:

- [Open, unassigned wave 0–1 issues](https://github.com/HodeTech/ForgeLM/issues?q=is%3Aissue+is%3Aopen+no%3Aassignee+label%3A%22wave%3A+0%22%2C%22wave%3A+1%22) — the highest-priority work
- [Documentation issues](https://github.com/HodeTech/ForgeLM/issues?q=is%3Aissue+is%3Aopen+label%3Adocumentation+no%3Aassignee) — often a good first contribution
- The project's *Backlog* view, filtered by an **Area** you know or by **Size** `XS`/`S`

### Working on an issue

1. **Claim it first.** Comment on the issue that you would like to work on it, so a maintainer can assign it to you and nobody duplicates the work. Skip issues labelled `needs-triage`, and check the recorded condition of an issue labelled `deferred` before you start.
2. **Re-check against `development`.** Issues from the review link code at the commit they were found on; the links stay valid, but line numbers may have moved. Confirm the problem still exists on `development` before fixing it.
3. **Follow the acceptance criteria** in the issue. In particular, a code fix carries a regression test that reproduces the described failure, and a documentation fix updates the English page and its Turkish mirror (`-tr.md`, or the `tr/` tree for user manuals) in the same pull request.
4. **Reference the issue in your pull request.** Use `Fixes #123` when the PR resolves the whole issue, and `Refs #123` when it resolves only part of it.

**Grouped issues.** Some issues collect several related findings as a checklist, for example a module sweep or one document's corrections. You do not need to fix the whole list: claim the finding IDs you will handle in a comment, mention those IDs in your pull request, and use `Refs #…` so the issue stays open until every item is ticked.

**Roadmap steps.** A Phase 16 step issue is claimed as a whole and worked in step order: each step lands as one root-cause family with its own review rounds, and its issue lists the review issues it closes.

**Finding IDs.** Issues created from the review name their findings with stable IDs — `CR-…` for code, `DOC-…` for documentation (including the Turkish mirror), `TECH-…` for dependency and ecosystem currency. Quote the ID in commits and pull requests so the finding can be traced.

## Reporting Security Vulnerabilities

Please **do not open a public issue** for a security vulnerability, and do not post exploit details in comments. Report it privately through GitHub: on the repository's **Security** tab choose **Report a vulnerability**. [`SECURITY.md`](SECURITY.md) explains what to include, what counts as a vulnerability, and how disclosure works. Maintainers track confirmed vulnerabilities in private security advisories and publish them after a fixed release is available, so some planned security work is intentionally not visible in the public issue list.

## Quick Start for Code Contributors

**Branches.** `development` is the default and integration branch: every change lands there first, and it is where you branch from. `main` holds released code only — maintainers merge `development` into `main` through a release pull request and tag the release there. Open your pull request against `development`; CI runs the same checks on both branches.

### 1. Fork & Clone

```bash
git clone https://github.com/YOUR_USERNAME/ForgeLM.git
cd ForgeLM
git remote add upstream https://github.com/HodeTech/ForgeLM.git
```

### 2. Install (dev mode)

```bash
python3 -m pip install -e ".[dev]"
```

> **Note on the `[dev]` extras + coverage gate.** The `pytest` invocation
> in `pyproject.toml` carries `--cov-fail-under=40`. The `[dev]` extras
> are required to reach that floor — installing `pip install -e .` (no
> extras) trips the gate because optional-dep test paths can't run.
> Always use `pip install -e ".[dev]"` for contributor work. The floor
> itself is intentional ([`docs/standards/testing.md`](docs/standards/testing.md));
> do not lower it.

### 3. Create a branch

```bash
git fetch upstream
git checkout -b feat/my-feature upstream/development
```

Branch naming: `feat/`, `fix/`, `docs/`, `test/`, `chore/` + short description.

### 4. Make your changes

Edit the code, then run the full validation gauntlet (every guard CI also
enforces — passing locally means CI will too):

<!-- gauntlet:begin -->
```bash
python3 tools/check_import_origin.py --strict && \
  ruff format . && ruff check . && pytest tests/ && \
  python3 -m forgelm --config config_template.yaml --dry-run && \
  python3 -m mypy --strict --follow-imports=silent forgelm/__init__.py forgelm/_version.py tests/typing/public_surface_probe.py && \
  python3 tools/check_field_descriptions.py --strict forgelm/config.py && \
  python3 tools/check_http_discipline.py && \
  python3 tools/check_no_mutation_artifacts.py --strict && \
  python3 tools/check_strict_json_writers.py --strict && \
  python3 tools/check_bilingual_parity.py --strict && \
  python3 tools/check_bilingual_code_blocks.py --strict && \
  python3 tools/check_anchor_resolution.py --strict && \
  python3 tools/check_cli_help_consistency.py --strict && \
  python3 tools/check_cli_exit_code_prose.py --strict && \
  python3 tools/check_wizard_defaults_sync.py && \
  python3 tools/check_no_analysis_refs.py && \
  python3 tools/check_no_unguarded_sys_modules_pop.py && \
  python3 tools/check_audit_event_catalog.py --strict && \
  python3 tools/check_tr_links_prefer_mirror.py --strict && \
  python3 tools/check_usermanual_self_contained.py --strict && \
  python3 tools/check_notebook_pins.py --strict && \
  python3 tools/check_usermanual_schema_drift.py --strict && \
  python3 tools/check_yaml_snippets.py --strict && \
  python3 tools/check_deprecation_targets.py --strict && \
  python3 tools/check_release_record_sync.py --strict && \
  python3 tools/check_skill_mirror_parity.py --strict && \
  python3 tools/check_source_path_refs.py --strict && \
  python3 tools/check_readme_links.py --strict && \
  python3 tools/check_library_api_doc.py --strict && \
  python3 tools/check_doc_numerical_claims.py --strict && \
  python3 tools/check_site_claims.py --strict && \
  python3 tools/check_site_chrome_parity.py && \
  python3 tools/check_module_size.py --strict && \
  python3 tools/update_site_version.py --check && \
  BANDIT_JSON=$(mktemp) && trap 'rm -f "$BANDIT_JSON"' EXIT && \
  { bandit -c pyproject.toml -r forgelm/ -f json -o "$BANDIT_JSON" || true; } && \
  python3 tools/check_bandit.py "$BANDIT_JSON"
```
<!-- gauntlet:end -->

**Do not "simplify" `python3 -m forgelm` back to `forgelm`.** A console
script's `sys.path[0]` is its own `bin/` directory, never the cwd, so
`forgelm …` imports whatever is installed in site-packages; a stale
non-editable install made that step validate a weeks-old package while
reporting success on an unrelated working tree. `-m` puts the cwd first on
`sys.path`, so it runs the checkout. The import-origin guard leads the
chain for the same reason and must stay first: it asserts the premise
every later step depends on — that the `forgelm` being imported is the one
you just edited — and `-m` alone does not cover the `tools/check_*.py`
guards that import `forgelm` with `sys.path[0] == tools/`.

All 31 must pass — the exact set `.github/workflows/ci.yml` runs.
`tests/test_guard_wiring.py` compares the two inventories in **both**
directions across this file, `CLAUDE.md` and `AGENTS.md`, so wiring a new
guard into CI without listing it here fails the build. The four after the
import-origin guard are the historical "self-review"
command from [`docs/standards/code-review.md`](docs/standards/code-review.md).
The rest are doc/schema/audit-log guards that landed across Waves 3-5 and
later review cycles and run on every PR via `.github/workflows/`; running
them locally before pushing avoids CI round-trips. See
[`CLAUDE.md`](CLAUDE.md#how-to-work-on-a-task) for what each guard checks —
keep this list and that one in sync if either changes.

### 5. Submit a PR

Push your branch to your fork and open a Pull Request against `development` (the default base). Link the issue it addresses (`Fixes #123`, or `Refs #123` for part of a grouped issue) and quote any finding IDs — see [Working on an issue](#working-on-an-issue).

## Development Setup

### Project Structure

ForgeLM is a single-package layout: a mix of single-file modules and five
focused sub-packages (`forgelm/cli/` post-Phase-15 split,
`forgelm/data_audit/` post-Phase-14 split, `forgelm/wizard/` from Phase 22,
`forgelm/safety/` from the post-v0.9.1 split and `forgelm/verify/` from
Phase 16 S1) under `forgelm/`, 128 test files
under `tests/` (collected-test count grows over time — run
`pytest --collect-only -q` for current), plus `configs/`, `docs/`, `tools/`
(CI guards), and `notebooks/`. For the authoritative module-by-module map
(purpose, public surface, dependency arrows), see
[`docs/reference/architecture.md`](docs/reference/architecture.md).

### Running Tests

```bash
# All tests
pytest tests/ -v

# Specific test file
pytest tests/test_config.py -v

# With coverage
pytest tests/ --cov=forgelm --cov-report=term-missing
```

Some tests require `torch` and are skipped when it's not installed. This is expected in lightweight dev environments.

### Code Style

We use [ruff](https://docs.astral.sh/ruff/) for linting and formatting:

```bash
# Check
ruff check .
ruff format --check .

# Auto-fix
ruff check --fix .
ruff format .
```

Configuration is in `pyproject.toml` under `[tool.ruff]`.

### Pre-commit hooks (optional)

ForgeLM ships a [`.pre-commit-config.yaml`](.pre-commit-config.yaml) that mirrors
the CI checks (`ruff`, `ruff-format`, `gitleaks`, plus a few hygiene hooks for
trailing whitespace, EOF newlines, and YAML/TOML syntax). The hooks are an
**optional ergonomic optimization** — CI enforces the same checks on every PR,
so installing them locally is purely about getting feedback before you push.

To opt in:

```bash
pip install pre-commit
pre-commit install
```

Run all hooks against the whole tree at any time:

```bash
pre-commit run --all-files
```

If a hook ever flags a known-good fixture (e.g. a credential-shaped test
string the gitleaks hook can't tell apart from a real secret), skip just that
hook for the commit:

```bash
SKIP=gitleaks git commit -m "test: add credential-shape fixture"
```

CI remains the enforcement boundary; skipping a hook locally never bypasses CI.

## Guidelines

### Code

- **Keep it simple.** ForgeLM's strength is simplicity. Don't add complexity unless necessary.
- **Config-driven.** New features should be configurable via YAML. No hardcoded behavior.
- **Optional dependencies.** Heavy dependencies go in optional groups: `pip install forgelm[feature]`.
- **Tests required.** Every new feature or bugfix needs a test. Keep coverage growing.
- **Ruff clean.** CI will reject code that doesn't pass `ruff check`.
- **No secrets.** Never commit tokens, API keys, or credentials. Use env vars.

### Config Changes

If you add a new config field:

1. Add the field to the Pydantic model in `config.py`
2. Add it to `config_template.yaml` (commented with example)
3. Update the [Configuration Guide](docs/reference/configuration.md) if it's user-facing
4. Add a test in `tests/test_config.py`

### Adding a New Trainer Type

1. Add the type to the `Literal[...]` on `TrainingConfig.trainer_type` in `config.py`
2. Add trainer-specific parameters to `TrainingConfig`
3. Add the TRL config builder in `trainer.py:_get_training_args_for_type()`
4. Add the trainer initialization in `trainer.py:train()`
5. Add dataset format detection in `data.py`
6. Update the trainer-specific prompts in `forgelm/wizard/_collectors.py` (and `forgelm/wizard/_defaults.json` if the new type needs its own defaults)
7. Add tests in `tests/test_alignment.py`
8. Add a notebook in `notebooks/`

### Commit Messages

We follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add KTO trainer support
fix: handle NaN eval_loss in auto-revert
docs: add GRPO notebook example
test: add merging algorithm tests
chore: update CI to Python 3.13
style: apply ruff format
```

## First-Time Contributors

Look for issues labeled [`good first issue`](https://github.com/HodeTech/ForgeLM/labels/good%20first%20issue). These are designed to be approachable for newcomers.

Small, self-contained tasks also include single findings in a `wave: 3` or `wave: 4` grouped issue (claim one finding ID at a time) and [documentation issues](https://github.com/HodeTech/ForgeLM/issues?q=is%3Aissue+is%3Aopen+label%3Adocumentation+no%3Aassignee); in the [project](https://github.com/orgs/HodeTech/projects/5), filter by **Size** `XS` or `S`.

## Questions?

- **GitHub Discussions** — [Ask a question](https://github.com/HodeTech/ForgeLM/discussions)
- **Issues** — [Report a bug or request a feature](https://github.com/HodeTech/ForgeLM/issues)

## License

By contributing, you agree that your contributions will be licensed under the [Apache License 2.0](LICENSE).
