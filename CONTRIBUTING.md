# Contributing to ForgeLM

Thanks for your interest in contributing! ForgeLM is an open-source project and we welcome contributions of all kinds.

## Your first pull request

1. Pick an open, unassigned [`good first issue`][good-first-issue].
   Comment `/assign` to claim it and confirm the problem on `development`; see [Working on an issue](#working-on-an-issue).
2. [Fork and clone](#1-fork--clone), then [create a branch from `development`](#3-create-a-branch).
   Use Python **3.10, 3.11, 3.12 or 3.13** — CI tests all four versions. Create a virtual environment
   with that interpreter (replace `python3.13` below if you selected another supported version),
   activate it, and install the development extra:

   ```bash
   python3.13 -m venv .venv
   source .venv/bin/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
   python -m pip install -e ".[dev]"
   ```

3. Make the change and, for a code fix, add a regression test that reproduces the issue;
   follow the [testing standard](docs/standards/testing.md).
   Documentation changes update the English page and its Turkish mirror together.
   If you do not write Turkish, open the pull request with the English change and say
   so in the description — a maintainer will complete the mirror for you.
4. Run fast checks before pushing (replace the example test module with the one you touched):

   ```bash
   ruff format .
   ruff check .
   pytest tests/test_config.py --no-cov
   python3 -m forgelm --config config_template.yaml --dry-run
   ```

   `--no-cov` keeps a focused test run from applying the whole-suite coverage floor.
   When documentation changes, also run:

   ```bash
   python3 tools/check_bilingual_parity.py --strict
   python3 tools/check_bilingual_code_blocks.py --strict
   python3 tools/check_tr_links_prefer_mirror.py --strict
   python3 tools/check_anchor_resolution.py --strict
   python3 tools/check_readme_links.py --strict
   python3 tools/check_cli_help_consistency.py --strict
   ```

   CI runs the complete guard set and full test suite, including coverage; you do not have to run
   the [full local gauntlet](#4-make-your-changes) before every push. `tests/test_guard_wiring.py`
   checks that CI and the documented gauntlets contain the same guards and flags.
5. Push to your fork and [open the PR against `development`](#5-submit-a-pr), using the PR template
   and `Fixes #N` when it resolves the whole issue (`Refs #N` for a partial fix).
6. A new contributor's first workflow run may wait for a maintainer to approve it; this is normal
   [GitHub workflow approval](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/approve-runs-from-forks).

[good-first-issue]: https://github.com/HodeTech/ForgeLM/issues?q=is%3Aissue+is%3Aopen+no%3Aassignee+label%3A%22good+first+issue%22

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
- **The [ForgeLM Project Plan](https://github.com/orgs/HodeTech/projects/5) is where the work is planned.** It shows every issue with its priority, release target and iteration, in several views: *Backlog* (table), *Board* (by status), *Current iteration* and *Roadmap* (timeline), plus filtered views such as *Front door*, *Small tasks*, *Epics*, *Docs & TR mirror*, *Roadmap — Phase 16 & deferred* and *Blocked*. Use it to choose what to work on; you never need to edit it — maintainers keep it current, and a card moves automatically when its issue is closed.

### Planned work lives here too

Every piece of planned or deferred work has an issue and a project card, not only the defects: the remaining steps of [Phase 16](docs/roadmap/phase-16-trust-surface-hardening.md) are sub-issues of [#903](https://github.com/HodeTech/ForgeLM/issues/903), and the deferred items recorded in [risks-and-decisions.md](docs/roadmap/risks-and-decisions.md) are sub-issues of [#929](https://github.com/HodeTech/ForgeLM/issues/929). Each Phase 16 step, and each deferred row that has an issue, links that issue directly from the roadmap entry. New issues land in the project's *Intake — unplanned* view until a maintainer plans them. If you notice work that your pull request is not going to do, open an issue for it instead of leaving a `TODO` or a roadmap line. Every item is an issue; which issues appear on the front door is decided by labels and views, never by closing them.

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
| `source: roadmap` | Planned work recorded in [`docs/roadmap/`](docs/roadmap/): a roadmap step or a deferred item |
| `phase: 16` | Part of [Phase 16](docs/roadmap/phase-16-trust-surface-hardening.md): one of its remaining steps, one of its two prerequisite standards, or the phase epic. Deferred items are not part of the phase and do not carry it |
| `deferred` | Waits for a condition recorded in the issue; check that it holds before starting. A deferred item is labelled `source: roadmap` and `deferred` |
| `blocked` | Waits for another issue, listed under *Blocked by* in the sidebar. Set and removed automatically, never by hand; do not start it — see [Dependencies between issues](#dependencies-between-issues) |
| `dependency-override` | On a pull request: a maintainer accepts that it works on a blocked issue |
| `epic` | A parent issue that groups one theme or roadmap phase; its sub-issues are the work |

In the project, the **Wave**, **Severity**, **Theme**, **Kind**, **Area**, **Size** (effort: XS–XL) and **Iteration** fields let you filter and group the same issues. **Milestones** are the waves: waves 0–2 carry a target date at the end of their last iteration, and waves 3–4 are open-ended backlog.

Useful starting points:

- The project's [*Front door* view](https://github.com/orgs/HodeTech/projects/5/views/16) — open, unassigned `good first issue`s with their size and area, the place to start on a first contribution
- [Open, unassigned wave 0–1 issues](https://github.com/HodeTech/ForgeLM/issues?q=is%3Aissue+is%3Aopen+no%3Aassignee+label%3A%22wave%3A+0%22%2C%22wave%3A+1%22) — the highest-priority work
- [Documentation issues](https://github.com/HodeTech/ForgeLM/issues?q=is%3Aissue+is%3Aopen+label%3Adocumentation+no%3Aassignee) — often a good first contribution
- The project's *Backlog* view, filtered by an **Area** you know or by **Size** `XS`/`S`

### Working on an issue

1. **Claim it first.** Start a comment on the issue with `/assign` and, once GitHub confirms it, the issue is assigned to you, so nobody duplicates the work. If the automation cannot complete, its reply says so and a maintainer assigns the issue by hand. This works on an open issue labelled `good first issue` that nobody holds yet. A contributor can hold at most 2 open issues at a time; the maintainers listed in [MAINTAINERS.md](MAINTAINERS.md) are exempt. Comment `/unassign` to give one back. A few rules keep it predictable:
   - It is first come, first served. A later `/assign` never changes an assignment that exists, whoever wrote it and however it was made. Only a maintainer can reassign an issue, in the issue's sidebar, and a maintainer's decision always stands.
   - `/assign` and `/unassign` act on the person who writes them, and the command must be the first thing in the comment; they cannot name someone else. If two people claim an issue in the same second, the maintainer's claim wins, and between two contributors one claim is released again and its author is told so.
   - Anything the automation does not cover stays a maintainer's call: an issue labelled `help wanted` without `good first issue` (grouped sweeps are taken one finding ID at a time, see below), an issue labelled `needs-triage`, `deferred`, `security`, `epic` or `phase: 16`, and a request for more than 2 open issues. Comment on the issue, say what you would take on and mention a maintainer ([MAINTAINERS.md](MAINTAINERS.md)). A plain comment such as "I would like to work on this" is read by a maintainer too, who assigns it by hand.
   - Skip issues labelled `needs-triage`, and check the recorded condition of an issue labelled `deferred` before you start.
   - Skip issues labelled `blocked` until the label is gone: they wait for another issue, and `/assign` refuses them whatever their other labels (see [Dependencies between issues](#dependencies-between-issues)).
2. **Re-check against `development`.** Issues from the review link code at the commit they were found on; the links stay valid, but line numbers may have moved. Confirm the problem still exists on `development` before fixing it.
3. **Follow the acceptance criteria** in the issue. In particular, a code fix carries a regression test that reproduces the described failure, and a documentation fix updates the English page and its Turkish mirror (`-tr.md`, or the `tr/` tree for user manuals) in the same pull request.
4. **Reference the issue in your pull request.** Use `Fixes #123` when the PR resolves the whole issue, and `Refs #123` when it resolves only part of it.

**Grouped issues.** Some issues collect several related findings as a checklist, for example a module sweep or one document's corrections. You do not need to fix the whole list: claim the finding IDs you will handle in a comment, mention those IDs in your pull request, and use `Refs #…` so the issue stays open until every item is ticked.

**Roadmap steps.** A Phase 16 step issue is claimed as a whole and worked in step order: each step lands as one root-cause family with its own review rounds, and its issue lists the review issues it closes.

**Finding IDs.** Issues created from the review name their findings with stable IDs — `CR-…` for code, `DOC-…` for documentation (including the Turkish mirror), `TECH-…` for dependency and ecosystem currency. Quote the ID in commits and pull requests so the finding can be traced.

### Dependencies between issues

Some issues can only be done after another one: each Phase 16 step waits for the step before it, a deferred item may wait for the step that changes the same file, and a review issue whose every finding is a unit of a Phase 16 step waits for that step, whose pull request fixes it. Such a dependency is recorded as a **Blocked by** link in the issue's sidebar (*Relationships*), never only in prose.

- **An issue with an open blocker is labelled `blocked`.** Do not start on it and do not open a pull request for it: `/assign` refuses it, and the **Issue dependencies** pull-request check fails a pull request whose description links it with `Fixes`, `Closes`, `Resolves` or `Refs #…`.
- **The label is automatic.** Nobody sets or removes it by hand. The *Issue dependencies* workflow adds it while a blocker is open and removes it once every blocker is closed as completed — as soon as that blocker is closed, or within an hour of a link being added or removed. When it removes the label it says so in a comment on the issue. Within the same hour it also re-runs the check of every open pull request whose result no longer matches, so a red check turns green once its blockers are completed, and a green one turns red if a linked issue becomes blocked.
- **A blocker closed as not planned or as a duplicate unblocks nothing**, because the work the issue waits for was not done. The issue stays `blocked`, gets `needs-triage`, and a maintainer re-plans it: removes the link, or links the issue that replaces the blocker.
- **A pull request may close the blocker as well.** When it closes every open blocker of the issues it links, the check passes. It counts a blocker as closed only in the form GitHub closes on merge — the keyword, a space and the issue, once per issue (`Fixes #12, fixes #13`) — so `Fixes#12`, `Fixes: #12` or the second issue of `Fixes #12, #13` leave the blocker open. A maintainer listed in [MAINTAINERS.md](MAINTAINERS.md) can also accept a dependency deliberately with the `dependency-override` label; the label set by anyone else is ignored.
- **Grouped issues are not blocked as a whole** when only some of their findings belong to a Phase 16 step. Those findings say so in the checklist (`KNOWN-OPEN … (S8 open)`): leave them to the step and take the others.

The project's *Blocked* view lists every blocked issue; *Front door* and *Small tasks* leave them out.

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

Use the [first-PR checklist](#your-first-pull-request) for fast local feedback.
The full validation gauntlet below is available when you want to reproduce
the complete local guard set; CI runs it regardless of which checks you run
locally. A local pass covers your environment, while CI also tests the
supported Python matrix:

<!-- gauntlet:begin -->
```bash
python3 tools/check_import_origin.py --strict && \
  ruff format . && ruff check . && pytest tests/ && \
  node --test tests/site/*.test.cjs && \
  python3 -m forgelm --config config_template.yaml --dry-run && \
  python3 -m mypy --strict --follow-imports=silent forgelm/__init__.py forgelm/_version.py tests/typing/public_surface_probe.py && \
  python3 tools/check_field_descriptions.py --strict forgelm/config.py && \
  python3 tools/check_http_discipline.py && \
  python3 tools/check_no_mutation_artifacts.py --strict && \
  python3 tools/check_strict_json_writers.py --strict && \
  python3 tools/check_ci_failure_masking.py --strict && \
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

All 32 must pass — the exact set `.github/workflows/ci.yml` runs.
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

**Before a pull request can merge**, branch protection on `development` (and on `main`) requires three things: the six CI checks — `lint`, `test (3.10)`, `test (3.11)`, `test (3.12)`, `test (3.13)` and `validate` — passing; the branch up to date with its target (use *Update branch*); and every review conversation resolved, including the comments of the automated reviewers. The **Issue dependencies** check ([Dependencies between issues](#dependencies-between-issues)) runs on every pull request too, but it is not yet among the required checks: a maintainer adds it to the branch protection of `development` once it has run on a pull request after this change (tracked in [#1034](https://github.com/HodeTech/ForgeLM/issues/1034)). Until then a red result is a signal, not a merge block.

Maintainers **squash contributor PRs into `development` by default**. Releases into
`main` and synchronization PRs from `main` back into `development` use **merge
commits**, preserving the shared history. Rebase-and-merge is disabled; contributors
do not need to rewrite their commits during review. See the
[merge policy](docs/standards/code-review.md#merging).

## Development Setup

### Project Structure

ForgeLM is a single-package layout: a mix of single-file modules and five
focused sub-packages (`forgelm/cli/` post-Phase-15 split,
`forgelm/data_audit/` post-Phase-14 split, `forgelm/wizard/` from Phase 22,
`forgelm/safety/` from the post-v0.9.1 split and `forgelm/verify/` from
Phase 16 S1) under `forgelm/`, 131 test files
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
- **No secrets.** Never commit tokens, API keys, or credentials. Use env vars. Secret scanning with push protection is enabled, so a push that contains a recognised token is blocked.

### Config Changes

If you add a new config field (the [`add-config-field`](.claude/skills/add-config-field/SKILL.md) checklist has the details):

1. Add it to the right Pydantic model in `forgelm/config.py` as `Field(default=..., description=...)` — a field without `description=` fails `tools/check_field_descriptions.py --strict` — and constrain its domain (`ge=` / `le=`, and `allow_inf_nan=False` for any float used as a threshold, weight or ratio)
2. Read it where it is consumed, as `config.<section>.<field>` — never from an environment variable
3. Add it to `config_template.yaml`, commented with an example
4. Document it in the [Configuration Reference](docs/reference/configuration.md) and its Turkish mirror [`configuration-tr.md`](docs/reference/configuration-tr.md) in the same pull request
5. Add a test in `tests/test_config.py`
6. Add a CHANGELOG entry under `[Unreleased]` if users can observe the change

### Adding a New Trainer Type

1. Add the type to the `Literal[...]` on `TrainingConfig.trainer_type` in `config.py`
2. Add trainer-specific parameters to `TrainingConfig`
3. Add the TRL config builder in `trainer.py:_get_training_args_for_type()`
4. Add the trainer construction in `trainer.py:_build_trainer()` (the plain TRL trainers go through `_build_simple_trl_trainer()`)
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

Look for issues labeled [`good first issue`](https://github.com/HodeTech/ForgeLM/labels/good%20first%20issue): small, well-specified tasks that need no deep knowledge of the codebase.

Small, self-contained tasks also include single findings in a `wave: 3` or `wave: 4` grouped issue (claim one finding ID at a time) and [documentation issues](https://github.com/HodeTech/ForgeLM/issues?q=is%3Aissue+is%3Aopen+label%3Adocumentation+no%3Aassignee); in the [project](https://github.com/orgs/HodeTech/projects/5), filter by **Size** `XS` or `S`.

## Questions?

- **GitHub Discussions** — [Ask a question](https://github.com/HodeTech/ForgeLM/discussions)
- **Issues** — [Report a bug or request a feature](https://github.com/HodeTech/ForgeLM/issues)

## License

Code contributions are licensed under the [Apache License 2.0](LICENSE).
Bundled seed datasets use **CC-BY-SA 4.0**; dataset contributions must follow the
attribution and licensing requirements in [the template dataset license guide](forgelm/templates/LICENSES.md).
