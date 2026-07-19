# Contributing to ForgeLM

Thanks for your interest in contributing! ForgeLM is an open-source project and we welcome contributions of all kinds.

## Ways to Contribute

- **Bug reports** — found something broken? [Open a bug report](https://github.com/HodeTech/ForgeLM/issues/new?template=bug_report.yml)
- **Feature requests** — have an idea? [Open a feature request](https://github.com/HodeTech/ForgeLM/issues/new?template=feature_request.yml)
- **Code** — fix a bug, add a feature, improve tests
- **Documentation** — fix typos, improve guides, add examples
- **Notebooks** — add Colab notebooks for new use cases
- **Config templates** — share training configs that worked well for you

## Quick Start for Code Contributors

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
git checkout -b feat/my-feature upstream/main
```

Branch naming: `feat/`, `fix/`, `docs/`, `test/`, `chore/` + short description.

### 4. Make your changes

Edit the code, then run the full validation gauntlet (every guard CI also
enforces — passing locally means CI will too):

```bash
python3 tools/check_import_origin.py --strict && \
  ruff format . && ruff check . && pytest tests/ && \
  python3 -m forgelm --config config_template.yaml --dry-run && \
  python3 tools/check_bilingual_parity.py --strict && \
  python3 tools/check_anchor_resolution.py --strict && \
  python3 tools/check_cli_help_consistency.py --strict && \
  python3 tools/check_wizard_defaults_sync.py && \
  python3 tools/check_no_analysis_refs.py && \
  python3 tools/check_no_unguarded_sys_modules_pop.py && \
  python3 tools/check_audit_event_catalog.py --strict && \
  python3 tools/check_tr_links_prefer_mirror.py --strict && \
  python3 tools/check_usermanual_self_contained.py --strict && \
  python3 tools/check_notebook_pins.py --strict && \
  python3 tools/check_usermanual_schema_drift.py --strict && \
  python3 tools/check_deprecation_targets.py --strict && \
  python3 tools/check_release_record_sync.py --strict && \
  python3 tools/check_skill_mirror_parity.py --strict && \
  python3 tools/update_site_version.py --check
```

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

All twenty must pass. The first four are the historical "self-review"
command from [`docs/standards/code-review.md`](docs/standards/code-review.md).
The rest are doc/schema/audit-log guards that landed across Waves 3-5 and
later review cycles and run on every PR via `.github/workflows/`; running
them locally before pushing avoids CI round-trips. See
[`CLAUDE.md`](CLAUDE.md#how-to-work-on-a-task) for what each guard checks —
keep this list and that one in sync if either changes.

### 5. Submit a PR

Push your branch and open a Pull Request against `main`.

## Development Setup

### Project Structure

ForgeLM is a single-package layout: a mix of single-file modules and two
focused sub-packages (`forgelm/cli/` post-Phase-15 split and
`forgelm/data_audit/` post-Phase-14 split) under `forgelm/`, ~70 test files
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

## Questions?

- **GitHub Discussions** — [Ask a question](https://github.com/HodeTech/ForgeLM/discussions)
- **Issues** — [Report a bug or request a feature](https://github.com/HodeTech/ForgeLM/issues)

## License

By contributing, you agree that your contributions will be licensed under the [Apache License 2.0](LICENSE).
