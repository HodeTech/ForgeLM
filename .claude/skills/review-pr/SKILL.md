---
name: review-pr
description: Use this skill when reviewing a pull request to ForgeLM, whether your own (self-review before requesting review) or someone else's. Applies the code-review standard, catches anti-patterns, and produces actionable feedback. Triggered by requests like "review this PR", "check if PR #N is ready", "self-review my changes before opening PR".
---

# Skill: Review a ForgeLM Pull Request

Following [docs/standards/code-review.md](../../../docs/standards/code-review.md). This skill is equally useful for self-review (run it before requesting review) and peer review.

## When to use

- Before opening a PR (self-review pass)
- When asked to review someone else's PR
- When a PR has been sitting with review comments and you want to check what's left

Do **not** use for:
- Making the changes yourself (that's a different task)
- Deciding whether to merge (human maintainer call)

## The seven-question review

Go through these **in order**. If any answer is "no" or "unclear," block the PR. Question 7 fires only when a regex changes; the other six apply to every PR.

### 1. Does it match the architecture?

Check against [docs/standards/architecture.md](../../../docs/standards/architecture.md):

- [ ] Right module for the concern? (`config.py` doesn't do training, `trainer.py` doesn't parse args)
- [ ] Config-driven? (no env-var sniffing for behaviour, no CLI flags added in isolation)
- [ ] Heavy deps declared as extras in `pyproject.toml`?
- [ ] No silent import fallbacks (`try: import X except: X = None`)?
- [ ] No new module-level mutable state?

### 2. Does it match the coding standard?

Check against [docs/standards/coding.md](../../../docs/standards/coding.md):

- [ ] `ruff check` + `ruff format --check` pass (CI catches this — confirm CI green)
- [ ] Type hints on public functions
- [ ] `Optional[X]` not `X | None` (codebase consistency)
- [ ] Google-style docstrings on public/non-trivial functions
- [ ] No wildcard imports, no re-exports via `__init__.py` without reason
- [ ] Comments only where *why* is non-obvious

### 3. Are error paths correct?

Check against [docs/standards/error-handling.md](../../../docs/standards/error-handling.md):

- [ ] New exit codes (if any) use named constants and appear in the exit-code table
- [ ] Custom exceptions only if there's a distinct `except` handler
- [ ] `sys.exit()` only in `cli.py`, not in library modules
- [ ] No bare `except:`, no `except Exception: pass`
- [ ] User-facing error messages are specific + actionable + not apologetic
- [ ] Auto-revert path writes audit log before cleanup

### 4. Is observability correct?

Check against [docs/standards/logging-observability.md](../../../docs/standards/logging-observability.md):

- [ ] Module has its own logger (`logger = logging.getLogger("forgelm.X")`)
- [ ] No `print()` outside `cli.py` JSON-output blocks
- [ ] Every `sys.exit(!=0)` preceded by `logger.error(...)`
- [ ] New decision gates (safety/benchmark/judge) emit audit events
- [ ] Webhook failures wrapped in try/except, never abort training
- [ ] No secrets logged (tokens, API keys, webhook URLs, raw user prompts)

### 5. Are tests real?

Check against [docs/standards/testing.md](../../../docs/standards/testing.md):

- [ ] New public function → ≥1 happy-path test
- [ ] New exit code or exception → ≥1 test triggering it
- [ ] No GPU required in unit tests
- [ ] No network calls in unit tests (use mocks)
- [ ] No `pytest.skip` without `pytest.mark.xfail(reason=..., ...)` + issue link
- [ ] Assertions are specific (check values, not just truthiness)
- [ ] `pragma: no cover` only for documented exempt patterns

### 6. Is documentation correct?

Check against [docs/standards/documentation.md](../../../docs/standards/documentation.md) + [localization.md](../../../docs/standards/localization.md):

- [ ] If new config field: `docs/reference/configuration.md` + `-tr.md` updated
- [ ] If CLI changed: `docs/reference/usage.md` + `-tr.md` updated
- [ ] If new guide needed: under `docs/guides/`
- [ ] CHANGELOG entry under `[Unreleased]` in the right category
- [ ] `config_template.yaml` updated if new required config field
- [ ] No broken links (relative paths resolve)
- [ ] Bilingual mirrors structurally aligned (H2 count + order match)

### 7. Does any new / modified regex pass [regex.md](../../../docs/standards/regex.md)?

**Trigger:** any change to `re.compile`, `re.match`, `re.sub`, `re.findall`, `re.split`. Phase 11/11.5/12 review cycles burned ~10 iterations on regex correctness — this skill exists to spend zero on the next PR.

Run `git diff --unified=0 origin/main..HEAD -- '*.py' | grep -E '^\+.*re\.(compile|match|search|sub|findall|split|fullmatch)'` to surface the deltas, then for each:

- [ ] No `[A-Za-z0-9_]` (Sonar `python:S6353`); use `\w`.
- [ ] No single-char character classes `[ ]`, `[\.]`, `[\\]` (Sonar `python:S6328`); use the bare character.
- [ ] Quantifiers bounded where the spec allows it (`{1,6}` for ATX heading depth, not `+`).
- [ ] **No two unbounded `*` / `+` / `*?` / `+?` competing for the same character class.** This is the #1 ReDoS shape we keep hitting (`[ \t]+(.+?)[ \t]*$` → 100ms at n=2000). Anchor on `\S` at body boundaries: `[ \t]+(\S(?:[^\n]*\S)?)[ \t]*$`.
- [ ] No `.*?` + back-reference + `re.DOTALL` (Sonar `python:S5852`); replace with a state machine — see [`forgelm/data_audit/_quality.py::_strip_code_fences`](../../../forgelm/data_audit/_quality.py) and [`forgelm/ingestion.py::_markdown_sections`](../../../forgelm/ingestion.py).
- [ ] `\s` under `re.MULTILINE` → prefer `[ \t]` (no newline ambiguity).
- [ ] Operator-controlled input → 10K-char pathological-input wall-clock benchmark stays ≤ 10ms.
- [ ] Test fixtures with credential-shaped strings built from inert fragments (see `FAKE_AWS_KEY` / `FAKE_GH_TOKEN` in `tests/test_data_audit_phase12.py`).
- [ ] PEM / PGP block markers inside regex source split via concatenation (`r"-----" + r"BEGIN " + r"..."`) so repo-wide secret scanners don't false-positive.

If the regex runs on operator-controlled input, **paste the pathological-input benchmark output into the PR description** so reviewers see the linearity proof.

## Scope and hygiene

Beyond the six questions:

- [ ] **One concern per PR.** Bug fix + refactor + feature bundled → split.
- [ ] **No drive-by reformats** obscuring the real diff.
- [ ] **Commit messages informative** — "wip" / "stuff" / "fix things" is a rejection signal.
- [ ] **No `TODO` / `FIXME` without owner + issue link.**
- [ ] **No files outside the PR's stated scope.** Accidentally-committed local configs, `.DS_Store`, editor files — flag for removal.

## How to write feedback

For reviewers (including Claude as reviewer):

### Specific > abstract

❌ "Docstrings need improvement"
✅ "`trainer.py:142` — the docstring says 'trains the model' but the function also saves a checkpoint; please state the full contract"

### Suggest concrete changes

Use GitHub's suggestion blocks:

~~~markdown
```suggestion
def train_with_revert(config: ForgeConfig, ...) -> TrainResult:
    """Train and conditionally revert on safety regression.

    Args:
        config: Validated run configuration.

    Returns:
        TrainResult with metrics and revert status.
    """
```
~~~

### Distinguish blocking from optional

- Blocking: state the standard violated, link to it.
- Optional: prefix with `Nit:`, `Style:`, or `FYI:`.

### Separate questions from demands

- `Q: why is this `Optional` — can the caller guarantee non-None?`
- `Blocking: this needs to use `Optional[int]` to match codebase convention`

## Self-review workflow

Before clicking "Create PR":

1. **Read the diff in GitHub's web UI** (not just `git diff`) — rendering reveals issues the CLI hides.
2. **Grep for new `TODO` / `FIXME` / `XXX`** — add owner + issue link or remove.
3. **Grep for `print(` in non-CLI code** — remove or justify.
4. **Click through each test file** — does each new test actually assert something?
5. **Click the doc diffs** — renders correctly? Links work?
6. **Run the one-liner**:

   ```bash
   ruff format . && ruff check . && pytest tests/ && \
     forgelm --config config_template.yaml --dry-run
   ```

7. **Read your PR description** — does it state the one concern clearly?

## Escalation

When you as reviewer disagree with the author:

1. State the specific disagreement.
2. Link to a standard or prior art.
3. If unresolved after a round, request a third reviewer or move to an issue.

Default stance: **a blocked PR is better than a bad merge.**

## Pitfalls

- **Over-reviewing** style fixes that ruff will catch on next push. Don't waste author's time on auto-fixable stuff.
- **Rubber-stamping.** "LGTM!" without going through the six questions is worse than no review.
- **Scope creep in review.** "While you're in here, also fix X" — X is a separate PR.
- **Accepting "I'll fix it in a follow-up."** Follow-ups slip. Require fixes in this PR unless genuinely outside scope.
- **Ignoring CI warnings.** Yellow status = broken status. Check.

## Related skills

- `add-config-field`, `add-trainer-feature`, `add-test`, `sync-bilingual-docs` — the skills whose output this reviews
- `cut-release` — what happens to merged PRs eventually
