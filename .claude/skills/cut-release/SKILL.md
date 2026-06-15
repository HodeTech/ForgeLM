---
name: cut-release
description: Use this skill when preparing a ForgeLM release. Walks through version bump, changelog finalization, tag, publish, and post-release sync per docs/standards/release.md. Triggered by requests like "cut v0.4.0", "release ForgeLM", "prepare the next version".
---

# Skill: Cut a ForgeLM Release

Follows [docs/standards/release.md](../../../docs/standards/release.md). This is a maintainer-scope ritual — only invoke when explicitly releasing.

## When to use

- User says "release vX.Y.Z" or "cut a release"
- Phase completion warrants a version bump
- A critical fix needs a patch release

Do **not** use for:
- Routine PR merges (those go to `[Unreleased]` in CHANGELOG; release happens separately)
- Documentation-only changes (no version bump)

## First: decide if we should release

Checklist before even starting:

- [ ] Is there meaningful new content since last release? If only internal refactors, skip and defer.
- [ ] Is `main` CI green? If not, fix first.
- [ ] Is it Tuesday through Thursday? Never release on Friday (weekend support pain).
- [ ] Do we have at least one rc tested if this is a minor release?

If any is "no," propose waiting rather than pushing ahead.

## Decide the version

From [release.md](../../../docs/standards/release.md):

| Change type | `__version__` bump | `__api_version__` bump |
|---|---|---|
| Breaking (removed flag, renamed YAML field, changed exit code) | MAJOR | — (see below) |
| New feature (new trainer, new CLI subcommand, new config field) | MINOR | — (see below) |
| Bug fixes, docs, internal refactor | PATCH | none |
| Stable library symbol added to `forgelm.__all__` (e.g. new `verify_*` function) | MINOR | MINOR |
| Stable library symbol removed or signature changed | MAJOR | MAJOR |

`__version__` (single source of truth: the `version =` line in [`pyproject.toml`](../../../pyproject.toml)) tracks the *CLI / YAML / behavioural* contract. `__api_version__` (single source of truth: [`forgelm/_version.py`](../../../forgelm/_version.py)) tracks the *Python library* contract — operators who pin against it for feature detection rely on this signal. The two version numbers are decoupled by design: a release that adds a new training paradigm bumps `__version__` MINOR while leaving `__api_version__` untouched (no new public symbol), and a release that adds a new lazy-import target bumps both.

Library consumers should compare `__api_version__` via `packaging.version.Version` rather than string `>=`, because lexicographic comparison breaks for two-digit minor / patch components (e.g. `"1.10.0" < "1.2.0"` as strings):

```python
from packaging.version import Version
import forgelm

assert Version(forgelm.__api_version__) >= Version("1.0.0")
```

## Pre-release checklist

Run before tagging:

### 1. Sync main

```bash
git checkout main
git pull
git status  # must be clean
```

### 2. Finalize CHANGELOG

Open [`CHANGELOG.md`](../../../CHANGELOG.md). Move items from `[Unreleased]` to a new version section:

```markdown
## [Unreleased]

## [0.4.0] — 2026-07-15

### Added
- **Inference module** (`forgelm.inference`) — ...

### Changed
- ...

### Fixed
- ...

### Breaking
- `forgelm generate` removed; use `forgelm chat` instead.
```

Rules:
- Date is today, ISO format.
- Only categories: Added / Changed / Deprecated / Removed / Fixed / Security / Breaking.
- Every entry is user-facing — dev-only refactors don't belong here.

### 3. Bump version

Edit the `version =` line in [`pyproject.toml`](../../../pyproject.toml):

```toml
version = "0.4.0"    # was "0.3.1rc1" or "0.4.0rc1"
```

If pre-release → release: drop `rcN` suffix. If new rc: `0.4.0rc1` → `0.4.0rc2`.

### 3.5. Bump `__api_version__` if applicable

**Question to answer:** did this release add, remove, or change the signature of a *stable-tier* library symbol (anything in `forgelm.__all__`)?

- [ ] **No** → leave [`forgelm/_version.py`](../../../forgelm/_version.py) `__api_version__` untouched. Most releases sit here (CLI features, YAML knobs, internal refactors).
- [ ] **Yes, added** → bump `__api_version__` MINOR. Library consumers can then pin `assert Version(forgelm.__api_version__) >= Version("X.Y.0")` (using `packaging.version.Version` — see top-of-skill block) for feature detection.
- [ ] **Yes, removed or signature changed** → bump `__api_version__` MAJOR. The next 0.x release MUST telegraph the break in CHANGELOG's "Breaking" section.

The canonical bump rule lives at the top of [`forgelm/_version.py`](../../../forgelm/_version.py); the matching policy paragraph is in [`docs/standards/release.md`](../../../docs/standards/release.md). `__version__` and `__api_version__` are intentionally decoupled — see the table at the top of this skill.

### 4. Docs alignment

- [ ] If a new CLI flag was added: [`docs/reference/usage.md`](../../../docs/reference/usage.md) + `-tr.md` reflect it
- [ ] If a new config field was added: [`docs/reference/configuration.md`](../../../docs/reference/configuration.md) + `-tr.md` reflect it
- [ ] If a new guide was written: [`docs/guides/`](../../../docs/guides/) has it
- [ ] [`config_template.yaml`](../../../config_template.yaml) exercises all new fields (CI dry-run uses it)
- [ ] [`README.md`](../../../README.md) feature list still accurate
- [ ] **Run `python3 tools/update_site_version.py`** so the 15+ version literals across `site/*.html` (JSON-LD `softwareVersion`, hero badge, `pip install` snippets) and `site/js/translations.js` (`home.hero.badge` × 6 locales) re-derive from CHANGELOG's latest released header. The companion `--check` guard runs in the repo gauntlet so the next PR fails if you forget.

### 5. Local verification

```bash
ruff check . && ruff format --check .
pytest tests/ -v
pytest --cov=forgelm --cov-fail-under=40
forgelm --config config_template.yaml --dry-run

# Fresh install smoke:
python -m pip install -e .
forgelm --version   # should print the new version
```

All four must pass. The `--cov-fail-under=40` value tracks the canonical
floor in [`pyproject.toml`](../../../pyproject.toml) under
`[tool.pytest.ini_options].addopts`; if you change it here without
updating pyproject (or vice versa) you will hit a CI failure mid-release.

### 6. Commit + tag

```bash
git add -A
git commit -m "chore: release v0.4.0"
git tag -s v0.4.0 -m "v0.4.0 — Post-Training Completion"
git push origin main v0.4.0
```

GPG-signed tag (`-s`) is required for trusted PyPI publishing.

## Automated publish

[`publish.yml`](../../../.github/workflows/publish.yml) runs on tag push matching `v*`:

1. Build wheel + sdist
2. Verify with twine
3. Cross-OS / cross-Python install matrix smoke (3 OS x 4 Python = 12) plus SBOM generation
4. Publish to PyPI (OIDC trusted publishing)

The workflow does **not** create a GitHub Release — it stops at PyPI
publish. If a GitHub Release is desired, create it manually from the tag
using `gh release create vX.Y.Z --notes-from-tag` after the publish
workflow completes.

**Wait ~5-10 minutes**, then verify:

```bash
pip install forgelm==0.4.0 --force-reinstall
forgelm --version
```

If the install fails or returns the wrong version, investigate the workflow run. Common issues: tag mismatch with pyproject, build artifact missing, PyPI trust not configured.

## Post-release

### 1. Announce

Use this template (the maintainer keeps a longer canonical version in
local working memory; the spirit is the same):

```
📦 ForgeLM v0.4.0 released!

Highlights:
- Inference module + chat CLI
- GGUF export
- VRAM fit advisor

Full changelog: https://github.com/HodeTech/ForgeLM/blob/main/CHANGELOG.md
Upgrade: pip install --upgrade forgelm
```

Post to (ordered by priority):
- Discord `#announcements`
- Twitter/X
- LinkedIn
- (Optional) Dev.to blog post for major features

### 2. Reset to next pre-release

```bash
# Edit pyproject.toml: version = "0.4.1rc1"
# Edit CHANGELOG.md: add new ## [Unreleased] at top
git commit -m "chore: bump to 0.4.1rc1 (next dev cycle)"
git push
```

### 3. Update marketing roadmap metrics

The maintainer keeps a marketing roadmap in local working memory
(gitignored).  Add a metrics row for the release month there as part
of the release-day checklist; nothing on the public surface needs to
change.

### 4. Close the phase (if applicable)

If this release completes a phase, update [`docs/roadmap.md`](../../../docs/roadmap.md):
- Move phase from "Planned" row to archived
- Append detail to [`docs/roadmap/completed-phases.md`](../../../docs/roadmap/completed-phases.md)
- Update [`docs/roadmap/releases.md`](../../../docs/roadmap/releases.md)

## Hotfix release variant

If a critical bug is found post-release:

```bash
git checkout -b hotfix/0.4.1 v0.4.0
# fix + test
# update CHANGELOG with [0.4.1] section
# bump pyproject.toml to 0.4.1
git commit -m "fix: <bug>"
git push origin hotfix/0.4.1

# Create PR to main, review, merge
# Then tag from main HEAD:
git checkout main && git pull
git tag -s v0.4.1 -m "v0.4.1 — hotfix for <bug>"
git push origin v0.4.1
```

Follow up in announcements with "⚠️ Security/Stability fix — please upgrade" if warranted.

## Pitfalls

- **Releasing with `[Unreleased]` items that don't belong.** Before bumping, read every line in `[Unreleased]` and decide: does this ship? If not, move to a later section or remove.
- **Tagging before pushing commit.** The tag references a commit — if you tag locally and forget to push the commit, CI builds the wrong code.
- **Force-pushing a tag.** Tags are immutable from a user's perspective. If you need to correct a tag, delete locally + remote, push new — but PyPI publishes are one-way. If you've already published, the only fix is a new version.
- **Skipping the rc phase on breaking releases.** At least one rc should be tested for minor versions that add substantial features. Patch releases can skip rc.
- **Not updating `[Unreleased]` after release.** Next PR author will wonder where to add their entry.

## Related skills

- `review-pr` — for the PRs that feed the release
- `sync-bilingual-docs` — for doc alignment in step 4
