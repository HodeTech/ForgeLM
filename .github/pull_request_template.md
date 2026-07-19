## Summary

<!-- What does this PR do? One sentence. -->

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

- [ ] My code follows the project's style (ruff formatted)
- [ ] I've updated documentation if needed
- [ ] I've added tests for new functionality
- [ ] No new dependencies added (or added as optional: `pip install forgelm[...]`)
- [ ] Config template (`config_template.yaml`) updated if new config fields added
