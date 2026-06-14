# Release Standard

> **Scope:** Cutting a new ForgeLM version — tagging, changelog, PyPI publish, post-release sync.
> **Enforced by:** [`.github/workflows/publish.yml`](../../.github/workflows/publish.yml) + manual release ritual.

## Versioning

**Semantic versioning 2.0 (MAJOR.MINOR.PATCH).**

Current version lives in [`pyproject.toml`](../../pyproject.toml) line 7 (single source of truth — no `__version__` in code).

| Bump | Trigger |
|---|---|
| **MAJOR** (`1.0.0` → `2.0.0`) | Any breaking change: removed CLI flag, changed YAML schema with removed/renamed fields, changed exit code meaning, changed JSON output key names, changed module public API |
| **MINOR** (`0.3.0` → `0.4.0`) | New feature: new trainer type, new CLI command, new YAML field (additive), new compliance artifact, new extra |
| **PATCH** (`0.3.0` → `0.3.1`) | Bug fixes, docs, dependency version tweaks, internal refactor with no user-visible change |

### Pre-releases

Current version is `0.5.5` (per `pyproject.toml`) — pre-1.0; release-candidate suffixes (`0.5.5rc1`, `0.5.5rc2`, …) are used during the rc window.

- `0.4.0rc1`, `0.4.0rc2`, ... — for PyPI distribution while collecting feedback
- `0.4.0` — final release after rcN is stable

Pre-1.0 rule: every minor bump is potentially breaking. Users are warned in README. Post-1.0, SemVer is strict.

### `__api_version__` (Python library surface)

Library consumers — code that does `import forgelm` and pins against the public Python API listed in `forgelm.__all__` — read [`forgelm/_version.py`](../../forgelm/_version.py)'s `__api_version__` constant rather than the CLI version. The two are intentionally decoupled: a release that adds a new training paradigm bumps `__version__` MINOR while leaving `__api_version__` alone (no new public symbol), and a release that adds a new lazy-import target bumps both.

| `__api_version__` bump | Trigger |
|---|---|
| **MAJOR** | Stable library symbol removed, or its signature changed in a non-additive way (renamed param, removed param, narrowed return type). |
| **MINOR** | Stable library symbol added to `forgelm.__all__` (e.g. a new `verify_*` function or a new dataclass return type). |
| **PATCH** / none | CLI feature, YAML knob, internal refactor, or dependency tweak with no Python-API surface change. Most releases sit here. |

The `cut-release` skill enforces this contract via a checklist Step 3.5 ("Bump `__api_version__` if applicable") that runs after the `pyproject.toml` `__version__` bump. The canonical bump rule lives at the top of `forgelm/_version.py`.

Library consumers should parse `__api_version__` via `packaging.version.Version` (string `>=` comparison breaks for two-digit minor/patch components — e.g. `"1.10.0" < "1.2.0"` as strings):

```python
from packaging.version import Version
import forgelm

assert Version(forgelm.__api_version__) >= Version("1.1.0")  # feature detection
```

Bumping `__api_version__` MAJOR pre-1.0 still requires an explicit "Breaking" CHANGELOG entry per the cadence below.

## CHANGELOG

Maintained at [`CHANGELOG.md`](../../CHANGELOG.md), format from "Keep a Changelog":

```markdown
# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- ...

### Fixed
- ...

## [0.4.0] — 2026-07-15

### Added
- **Inference module** (`forgelm.inference`) — load, generate, logit stats, adaptive sampling. ([#42](https://github.com/...))
- **Chat CLI** — `forgelm chat <model_dir>` for terminal REPL with streaming.

### Changed
- Webhook timeout increased from 10s to 30s by default.

### Fixed
- Race condition in audit log hash chaining when two runs share an output dir.

### Breaking
- `forgelm generate` CLI subcommand removed; use `forgelm chat` instead.

## [0.3.1] — 2026-04-05

### Fixed
- ...
```

**Rules:**

1. **Every PR** adds an entry under `[Unreleased]` in the appropriate category.
2. **Categories are fixed:** Added / Changed / Deprecated / Removed / Fixed / Security / Breaking. No others.
3. **Breaking** section is a call-out even though SemVer also communicates it; be explicit.
4. **Link to PR or issue** only when the reader benefits (complex feature, contested decision). Not required for routine items.
5. **Write for users**, not for contributors. "Added inference module" is user-facing. "Refactored `trainer.py`" is not — don't include it.
6. **Audience doesn't care about version bumps of dev deps** (ruff, pytest). Don't add.

## Deprecation cadence

When a CLI flag, YAML field, JSON output key, or public function is removed,
it must first be deprecated for **at least one minor release** before the
removal lands. The cadence is non-negotiable so users running locked
versions in CI/CD pipelines get a release cycle to migrate.

Rules:

1. **Minimum overlap.** A deprecated surface stays present for at least one
   intervening minor (`v0.5.0` deprecate → `v0.6.0` still present →
   `v0.7.0` removable). Patch releases never remove anything.
2. **`DeprecationWarning` is mandatory** at the moment of deprecation —
   raised via `warnings.warn(..., DeprecationWarning, stacklevel=2)`. CLI
   flags additionally print a one-line stderr notice.
3. **Removal version stated up-front.** The deprecation message, the
   `--help` text, and the CHANGELOG `### Deprecated` entry must all name
   the version that will remove the surface.
4. **Tracking issue mandatory.** Every deprecation links to a GitHub issue
   that the removal PR closes. The link goes in the CHANGELOG entry.
5. **`### Removed` section required** in the CHANGELOG of the version that
   actually drops the surface, cross-referencing the deprecation entry.

**Worked example — `--data-audit` flag:** introduced in pre-Phase-11,
superseded by the `forgelm audit` subcommand in Phase 12. Deprecated in
v0.5.0 (`forgelm/cli/_dispatch.py:165-172` raises `DeprecationWarning`
and the surrounding handler at `_dispatch.py:154-205` writes an
append-only `cli.legacy_flag_invoked` audit-log event naming v0.8.0 as
the removal target; the Phase 15 CLI split moved this from the original
single-file `cli.py:1424-1428` location). The flag remains present and
functional through v0.6.x–v0.7.x — the original v0.7.0 removal was pushed
one minor out at the v0.7.0 cut to preserve the full one-minor warning
window — then is removed in v0.8.0 with a matching `### Removed` CHANGELOG
entry.

## Release checklist

Done manually by the maintainer (or a bot when automated):

### Before the release

1. [ ] All PRs targeted for this release are merged to `main`.
2. [ ] `main` CI is green, including nightly runs this week.
3. [ ] Move all `[Unreleased]` items into a new version section in `CHANGELOG.md` with today's date.
4. [ ] Bump `version` in `pyproject.toml` (e.g., `0.3.1rc1` → `0.4.0`).
5. [ ] If breaking changes: update README's "compatibility" section.
6. [ ] If new config fields: ensure `config_template.yaml` is current + TR mirrors match.
7. [ ] Commit: `chore: release v0.4.0` — single commit, no squash needed.
8. [ ] Tag: `git tag -s v0.4.0 -m "v0.4.0 — Post-Training Completion"` (GPG-signed).
9. [ ] `git push origin main v0.4.0`.

### Automated (by `publish.yml`)

On tag push matching `v*` the workflow chains three jobs (full description
under [Release prep workflow](#release-prep-workflow) below):

1. **`build`** — produce wheel + sdist, verify with `twine check`, upload
   as the `dist` artifact.
2. **`cross-os-tests`** — 3 OS × 4 Python = 12 combos install the packaged
   wheel (not editable), run pytest, generate a per-combo CycloneDX SBOM.
3. **`publish`** — OIDC trusted publishing to PyPI; only runs after every
   matrix combo is green.

### After the release

1. [ ] Verify `pip install forgelm==0.4.0` works in a clean venv.
2. [ ] Verify the Docker image builds with the new version.
3. [ ] Announce: Discord `#announcements`, Twitter, LinkedIn — template in the internal content strategy doc, "New Release" section.
4. [ ] Open a new `[Unreleased]` section in `CHANGELOG.md` for the next cycle.
5. [ ] Bump `pyproject.toml` version to next pre-release (`0.4.1rc1`).
6. [ ] Update the internal marketing strategy roadmap metrics row.

## Release prep workflow

The release pipeline is a single GitHub Actions workflow,
[`.github/workflows/publish.yml`](../../.github/workflows/publish.yml). It
fires when a tag matching `v*` is pushed (e.g. `v0.5.0`, `v0.5.1rc1`) and
chains three jobs whose `needs:` dependencies form a strict DAG.

### Trigger

```yaml
on:
  push:
    tags: ['v*']
```

Pushing the tag is the contract — no manual `workflow_dispatch`, no
release-event listener. The same procedure works whether you tag locally
and `git push --tags` or cut the tag from the GitHub Releases UI.

### Job 1 — `build` (`ubuntu-latest`, Python 3.11)

Runs `python -m build` to produce both `dist/*.whl` and `dist/*.tar.gz`,
then `twine check dist/*` to validate metadata, then uploads `dist/` as a
workflow artifact named `dist`. Subsequent jobs download this same
artifact rather than rebuilding, so every matrix combo and the publish
step exercise byte-identical files.

### Job 2 — `cross-os-tests` (matrix: 3 OS × 4 Python = 12 combos)

`needs: build`. Strategy:

```yaml
fail-fast: false
matrix:
  os: [ubuntu-latest, macos-latest, windows-latest]
  python: ['3.10', '3.11', '3.12', '3.13']
```

`fail-fast: false` is deliberate — one Python version blowing up on one
OS must not abort the other 11 combos, otherwise the matrix loses its job
as a breadth probe.

Each combo:

1. Downloads the `dist` artifact.
2. Installs the wheel via `python -m pip install dist/*.whl` — **packaged
   wheel, not editable**. This is the load-bearing detail. An editable
   install (`pip install -e .`) does not exercise wheel build, package
   data inclusion, console-script generation, or cross-OS path handling.
   By installing the same wheel that PyPI users will pull we guarantee
   that what passes here is what they get.
3. On Linux only, additionally installs `forgelm[qlora,ingestion,eval]`
   to cover the heaviest extras path. `qlora` pulls `bitsandbytes`, which
   only ships Linux wheels — we never claim Windows / macOS support for
   that extra.
4. Runs `pytest tests/ -q --ignore=tests/test_cost_estimation.py`. The
   cost-estimation test is excluded because its pricing fixture drifts on
   a different cadence than the release matrix; a stale fixture would
   break the chain for reasons unrelated to packaging health.
5. Generates a CycloneDX 1.5 SBOM via `python tools/generate_sbom.py`,
   redirected to `sbom-${{ matrix.os }}-py${{ matrix.python }}.json`.
6. Uploads each SBOM as its own artifact — 12 per release tag, retained
   alongside the workflow run for downstream supply-chain audits.

All steps that may run on Windows declare `shell: bash` so the same
script fragments work on Linux, macOS, and Windows runners (Windows
defaults to PowerShell otherwise).

### Job 3 — `publish` (`ubuntu-latest`, environment: `pypi`)

`needs: cross-os-tests`. Downloads the `dist` artifact and hands it to
[`pypa/gh-action-pypi-publish@release/v1`](https://github.com/pypa/gh-action-pypi-publish).
Authentication is OIDC trusted publishing — no PyPI API token is stored;
GitHub Actions mints a short-lived token scoped to the `pypi` environment.
The job sets `permissions: { id-token: write, contents: read }`; nothing
else is granted.

The `pypi` GitHub environment is configured in repository settings to
require manual approval for production tags if extra control is wanted;
the workflow itself does not gate beyond the `needs:` chain.

### Failure semantics

The `needs:` chain is the safety net:

- If `build` fails → neither `cross-os-tests` nor `publish` runs.
- If **any** `cross-os-tests` combo fails (with `fail-fast: false`, the
  other 11 still run so the failure surface is visible) → `publish` does
  not run. Nothing reaches PyPI.
- The tag remains in git either way; re-running the workflow after a fix
  is the recovery path, not deleting + recreating the tag.

This is by design: a release that ships only on Linux/3.11 because the
3.13 wheel was broken would silently degrade users on the un-tested
combos. Packaging hygiene is gated, not advisory.

## Release cadence

Current target:

- **Minor** (`0.N.0`) — every 2-3 months, aligned with phase completion:
  - `v0.4.0` → Phase 10 done (Post-Training Completion)
  - `v0.5.0` → Phases 11 + 12 done (Ingestion + Quickstart)
  - `v0.5.5` → Phase 12.6 closure cycle done (38 fazlar / 5 waves bundled)
  - `v0.6.0` → Phase 14 done (Pipeline Chains)
  - `v0.6.0-pro` → Phase 13 done (Pro CLI; gated release)
- **Patch** (`0.N.M`) — as needed; typically within 1 week of a bug report for critical issues
- **Pre-release** (`rcN`) — at least one rc before every minor, kept on PyPI for 1-2 weeks

**Don't release on Fridays.** If something breaks, weekend support is painful. Tuesday-Thursday only unless it's a critical hotfix.

## v0.5.5 release sequence (Phase 12.6 closure cycle)

The closure-cycle bundle is the largest single release in ForgeLM history (38 fazlar / ~52 PRs across 5 integration waves). The release commit follows the same `cut-release` skill flow used for every minor, but the `[0.5.5]` CHANGELOG section is exceptionally long and the cross-OS matrix is mandatory before publish:

1. **`pyproject.toml`** — bump `version = "0.5.1rc1"` → `"0.5.5"` (single source of truth).
2. **`forgelm/_version.py`** — review whether `__api_version__` needs a MINOR bump for the new Library API symbols (`ForgeTrainer`, `run_audit`, `verify_*`, `gdpr_purge`, `reverse_pii_query`, ...) added across Wave 2b + 3. Per the `__api_version__` rules at the top of this standard: yes, every new public symbol added to `forgelm.__all__` since the previous tag is a MINOR bump.
3. **`CHANGELOG.md`** — move all `[Unreleased]` entries into a new `[0.5.5] — YYYY-MM-DD` section. Cross-reference each entry to its faz number (e.g. "Library API — Wave 2b / Faz 19") so reviewers can map back to the [completed-phases.md](../roadmap/completed-phases.md) inventory.
4. **Tag** — `git tag -s v0.5.5 -m "v0.5.5 — Closure Cycle Bundle"`.
5. **Push** — `git push origin main v0.5.5`. The tag push is the contract; `publish.yml` fires automatically.
6. **Wait for matrix** — the cross-OS matrix runs 12 combos (3 OS × 4 Python). Each combo emits a per-(OS, Python) CycloneDX 1.5 SBOM (12 SBOM artifacts per release tag) — that is the full supply-chain output of the release pipeline. `pip-audit` is **not** run by `publish.yml`; vulnerability gating runs daily on `main` via [`.github/workflows/nightly.yml`](../../.github/workflows/nightly.yml) (the `pip-audit-gate` job calling `tools/check_pip_audit.py`), so every commit reaching a tag has already been audited within the last 24 hours. Total matrix runtime ~25-40 minutes.
7. **PyPI publish runs only after every combo is green** — OIDC trusted publishing, no API token in CI.

`publish.yml` is **tag-triggered only** (`on: push: tags: ['v*']`) — between releases, the wheel-build / cross-OS install path is exercised daily by nightly's `wheel-install-smoke` job, so the release pipeline does not need (and should not gain) a redundant scheduled trigger. Future maintainers tempted to add `schedule:` to `publish.yml` should extend the nightly smoke instead.

Post-release sequence is identical to other minor releases (verify install, Docker build, announce, open new `[Unreleased]` section, bump to next pre-release).

The [`cut-release` skill](../../.claude/skills/cut-release/SKILL.md) walks the maintainer through the entire sequence step-by-step.

## Branching

Trunk-based:

- `main` is always releasable.
- Feature branches short-lived, merged back via PR.
- **No release branches** (`release/v0.4`). If a hotfix is needed for an old version that has diverged, create a branch at that tag and cherry-pick — rare.

Historical reason: this repo is one maintainer + small contributor pool. Branch ceremony doesn't pay off.

## Hotfixes

When a critical bug is found post-release:

1. [ ] Create a fix branch from the affected tag: `git checkout -b hotfix/0.4.1 v0.4.0`.
2. [ ] Fix + test + update CHANGELOG (add a `[0.4.1]` section).
3. [ ] Merge to `main` first.
4. [ ] Cherry-pick or merge into the hotfix branch.
5. [ ] Tag `v0.4.1`, push. Automation handles the rest.
6. [ ] Announce on Discord + a pinned GitHub issue if security-related.

## What constitutes "breaking"

Often debated. Explicit list:

| Change | Breaking? |
|---|---|
| Removing a CLI flag | Yes |
| Renaming a CLI flag without alias | Yes |
| Adding a CLI flag | No |
| Changing default value of a CLI flag | Yes (unless strictly safer) |
| Removing a YAML field | Yes |
| Renaming a YAML field without alias | Yes |
| Adding a required YAML field | Yes |
| Adding an optional YAML field | No |
| Changing an exit code's meaning | Yes |
| Adding a new exit code | No |
| Changing a JSON output key name | Yes |
| Adding a JSON output key | No |
| Changing module import path | Yes |
| Changing a public function signature | Yes |
| Making a previously supported Python version unsupported | Yes |
| Making a previously optional extra now required | Yes |

When in doubt, treat as breaking. Users bumping minors without reading notes is common.

## Security releases

If a security issue is reported (see `SECURITY.md` if present):

1. Do **not** discuss publicly until fixed.
2. Coordinate with reporter on embargo dates.
3. Release as a patch (or minor if requires new feature).
4. Add `### Security` section in CHANGELOG for that version.
5. Post-release: file a GitHub Security Advisory referencing the CVE if assigned.

## Version in code

Programmatic access to version via:

```python
from importlib.metadata import version
forgelm_version = version("forgelm")
```

Both `forgelm.__version__` (re-exported from `forgelm/_version.py`, which itself reads `importlib.metadata` at install time) and `importlib.metadata.version("forgelm")` work; they return the same string. `pyproject.toml` remains the single source of truth — the dunder is a thin wrapper around the metadata lookup, alongside the decoupled `forgelm.__api_version__` for Library API contract pinning.

## Related

- [CHANGELOG.md](../../CHANGELOG.md) — the changelog itself
- [pyproject.toml](../../pyproject.toml) — version field, build config
- [.github/workflows/publish.yml](../../.github/workflows/publish.yml) — automation
- [code-review.md](code-review.md) — PR flow feeding releases
