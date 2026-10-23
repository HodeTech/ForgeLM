## Summary

<!-- What does this PR do? One sentence. -->

## Related issues

<!-- `Fixes #123` closes the issue when this PR is merged. Use `Refs #123` instead when the PR
     resolves only part of it, e.g. some findings of a grouped issue. Quote any finding IDs
     (CR-…, DOC-…, TECH-…) you address. Write "None" if there is no issue.
     Put one keyword before each issue (`Fixes #12, fixes #13`): GitHub closes only the first of
     `Fixes #12, #13`. The "Issue dependencies" check fails while an issue this PR links (title,
     description, commits or the Development sidebar) is blocked, unless GitHub lists its blockers
     as closed by this PR too, or a maintainer adds `dependency-override`. Mention an issue without
     a keyword if this PR does not work on it. -->
Fixes #

## Changes

<!-- Bullet list of changes -->
-
-

## Type

<!-- Check one -->
- [ ] Bug fix
- [ ] New feature
- [ ] Documentation
- [ ] Refactoring / code quality
- [ ] Test coverage
- [ ] CI / infrastructure

## Testing

<!-- How was this tested? -->
- [ ] `pytest tests/` passes
- [ ] `ruff check .` passes
- [ ] `ruff format --check .` passes
- [ ] New tests added for changed code
- [ ] `python -m forgelm --config config_template.yaml --dry-run` works
      (the `python -m` form is deliberate: a console script's `sys.path[0]` is
      its own `bin/` directory, so plain `forgelm …` validates whatever is
      installed in site-packages rather than your working tree)

## Checklist

- [ ] My code follows the project's style (ruff formatted) and `docs/standards/coding.md`
- [ ] I've updated documentation if needed — and its Turkish mirror (`-tr.md`, or the `tr/` tree for user manuals) in the same PR
- [ ] I've added tests for new functionality
- [ ] No new dependencies added (or added as optional: `pip install forgelm[...]`)
- [ ] Config template (`config_template.yaml`) updated if new config fields added
- [ ] CHANGELOG entry under `[Unreleased]` if a user or library consumer can observe the change
- [ ] The issue I link has been triaged (it does not carry `needs-triage`), or this PR links no issue —
      see [Issue triage](https://github.com/HodeTech/ForgeLM/blob/development/CONTRIBUTING.md#issue-triage)
