# Code Review Standard

> **Scope:** Every pull request to `HodeTech/ForgeLM`. Applies equally to external contributors and the maintainer.
> **Enforced by:** `.github/pull_request_template.md` checklist + CI gates + reviewer attention.

## The default state of a PR is "blocked"

A PR merges only after:

1. **Green CI** — all required checks pass.
2. **One approval** — from the maintainer or an authorized reviewer.
3. **Self-checklist complete** — every box in the PR template ticked honestly.
4. **No open review comments** marked as "changes requested."

No exceptions for "trivial" changes. Even typo fixes go through CI.

## PR template

The current template lives at [`.github/pull_request_template.md`](../../.github/pull_request_template.md). Every PR auto-fills it. Structure:

```markdown
## Summary
<one-sentence description>

## Changes
- bullet 1
- bullet 2

## Type
- [ ] Bug fix
- [ ] New feature
- [ ] Documentation
- [ ] Refactoring
- [ ] Test
- [ ] CI/CD

## Testing
- [ ] `pytest tests/` passes
- [ ] `ruff check .` passes
- [ ] `ruff format --check .` passes
- [ ] New tests added for new code
- [ ] `forgelm --config config_template.yaml --dry-run` works

## Checklist
- [ ] Code follows `docs/standards/coding.md`
- [ ] Documentation updated (if behaviour-visible)
- [ ] `-tr.md` mirrors updated (if user-facing docs changed)
- [ ] No new heavy deps (or added as optional extra)
- [ ] `config_template.yaml` updated if a new config field was added
- [ ] CHANGELOG entry added under `[Unreleased]`
```

**Rule:** every box must be ticked or explicitly crossed out (`~~[x]~~`) with a reason in the PR description. "Skipped because..." is acceptable. "Forgot" is not.

## What reviewers look for

In order of priority:

### 1. Does it match [architecture.md](architecture.md)?

- Right module for the concern?
- Is config flow respected? (YAML → Pydantic → passed down, not env-var-sniffing inside modules)
- Are new deps declared as extras?
- Does it grow a module past ~1000 lines without splitting?

### 2. Does it match [coding.md](coding.md)?

- Ruff passes (CI catches this; reviewer skims for it)
- Type hints on public functions
- `Optional[X]` not `X | None` (consistency)
- Google-style docstrings
- No silent-try-except (see [error-handling.md](error-handling.md))

### 3. Are the tests real?

- New public function has a happy-path + at least one error-path test
- No `pytest.skip` without justification
- No GPU dependency in unit tests
- `pragma: no cover` used only for documented exemptions
- If auto-revert / safety gate / exit code behaviour changed, is there a test that triggers it?

### 4. Is the observability correct?

- Every new decision gate emits an audit log event
- Every new exit code is documented in [error-handling.md](error-handling.md)
- No `print()` outside CLI JSON-output blocks
- No secrets in logs

### 5. Is the documentation correct?

- If a config field was added: updated in `docs/reference/configuration.md` and its TR mirror
- If CLI surface changed: updated in `docs/reference/usage.md` and TR mirror
- If a new guide is warranted: added under `docs/guides/`
- CHANGELOG entry written

### 6. Is the scope right?

- Does it do one thing? (A bug fix + a refactor + a new feature in one PR: split.)
- Are drive-by reformats included that obscure the real diff? (Reject those.)
- Is it a minimum viable implementation, or has scope crept?

### 7. Does any regex change pass [regex.md](regex.md)?

Trigger: any new or modified `re.compile` / `re.match` / `re.sub` / `re.findall` / `re.split` call. Phase 11/11.5/12 review cycles burned ~10 review iterations on regex correctness — this section exists to spend zero on the next one.

- No `[A-Za-z0-9_]` (use `\w`); no single-char classes `[ ]` / `[\.]`.
- Quantifiers bounded where the spec allows it (`{1,6}` not `+`).
- No two competing unbounded `*` / `+` / `*?` / `+?` over the same character class.
- No `.*?` + back-reference + `re.DOTALL` (replace with a state machine — see `forgelm/data_audit.py::_strip_code_fences`).
- `\s` is `[ \t\n\r\f\v]` — under `re.MULTILINE`, prefer `[ \t]` to keep newlines out of the way.
- Operator-controlled input → 10K-char pathological-input wall-clock benchmark must stay ≤ 10ms.
- Test fixtures with credential-shaped strings built from inert fragments (see `tests/test_data_audit_phase12.py::FAKE_AWS_KEY`).

## Comment etiquette

For reviewers:

1. **Specific over abstract.** "`line 47`: the docstring contradicts the type hint — fix one" beats "docstrings need attention."
2. **Suggest code when you can.** GitHub's suggestion syntax is cheap and concrete.
3. **Distinguish blocking from optional.** Prefix optional: "Nit:", "Style:", "FYI:". Blocking comments don't need a prefix.
4. **Praise specific things, not the whole PR.** "The hash chain in `AuditLogger._append()` is clean" > "LGTM!".
5. **Say why.** Every blocking comment includes a reason. "Change this to X" with no reason = bad feedback.

For PR authors:

1. **Don't debate on tone.** If a reviewer asks a question, answer with code or explanation.
2. **Acknowledge each comment.** Thread-level "Done" / "Won't do — reason" for every thread.
3. **Don't resolve threads you didn't open.** The reviewer closes their own.
4. **One new commit per round of feedback.** Don't force-push during review (unless requested) — it destroys diff-per-comment history.

## Merging

**Default: squash merge.**

- Keeps history linear and readable.
- Commit message = PR title. Edit it to be good *before* clicking merge.
- Trailers: `Co-authored-by:` for multi-author PRs, `Fixes #N` for issue closure.

**Exception: merge commit** for:

- Multi-commit PRs where each commit is a meaningful, reviewed unit (rare).
- Dependabot bulk updates.

Don't rebase-and-merge. Don't disable CI checks. Don't bypass branch protection.

## Reviewing your own PR before requesting review

Do this *first*. It saves reviewer time:

1. **Read the diff top to bottom in GitHub's UI.** Not `git diff` locally — GitHub's web UI is what reviewers see, and the rendering reveals issues the CLI hides.
2. **Check every `TODO`, `FIXME`, `XXX`.** Did you mean to leave them? Add owner + issue link or remove.
3. **Check every `import`.** Unused? Out of order?
4. **Click the test files.** Does each new test actually assert something?
5. **Click the doc diffs.** Does it render correctly? Do links work?

If you find issues at this stage, push fixes before requesting review. Don't make the reviewer find them.

## Handling pushback

Sometimes a reviewer is wrong. Sometimes you are. The default assumption is **"reviewer is right"** — if you disagree:

1. State the specific disagreement in a comment.
2. Link to a standard, prior art, or concrete reason.
3. If unresolved, escalate to a third reviewer or discuss in an issue.

The goal is the codebase, not the ego. A PR that gets blocked from merging isn't a failure; a bad change that gets merged is.

## Anti-patterns (from maintainer experience + external analyses)

Referencing the internal "what we don't build" strategy doc (public summary under "What ForgeLM is not" in the root [`CLAUDE.md`](../../CLAUDE.md)) and analyses of adjacent projects:

| Anti-pattern | Why rejected | Fix |
|---|---|---|
| Disabling CI with `\|\| true` or `continue-on-error` | Masks failures; fake safety | Fix the underlying issue |
| `--amend` + force-push after reviewer started commenting | Destroys review history | New commits only until merge |
| Merging "docs-only" PRs that reference not-yet-written code | Creates permanent drift | Docs follow code, same PR or later |
| Bundled refactor + feature PRs | Hides logic changes in formatting noise | Split into separate PRs |
| "I'll add tests next PR" | Tests never come | Block merge until tests in same PR |
| Review approval from LLM-only with no human reading | No real review | Human approval required, even if LLM helped |
| Marking a stub/placeholder "Done" | False progress | Use `NotImplementedError` with issue link; mark phase "Planned" |
| Renaming without updating docs | Silent doc rot | `grep -r <old_name>` before pushing |

## Finding-ID naming convention

Multi-round reviews — whether a single reviewer's iteration log, a parallel
multi-agent review, or a wave-scoped audit — accumulate dozens of discrete
findings. Stable IDs let later rounds, follow-up PRs, and CHANGELOG
entries cross-reference them without ambiguity. The historical waves used
divergent schemes (`F-Wave1-NN`, `F-R5-NN`, `F-W5-S5`/`F-W5-PRIV-NN`,
`F-PR29-AX-NN`); going forward we standardize on **two** canonical forms
so contributors don't have to reverse-engineer per-review naming.

**Canonical schemes:**

| Scheme | Use when | Example |
|---|---|---|
| `F-PR<NN>-A<X>-<NN>` | A single PR's review fans out across multiple parallel agents or reviewers, each with its own scope. `<NN>` is the PR number, `<X>` is the agent letter (A1…An, or A7/A8 for sub-team), trailing `<NN>` is the zero-padded finding index within that agent's scope. | `F-PR29-A7-01` (PR #29, agent A7, finding 01) |
| `F-W<N>-<topic>-<NN>` | A wave-scoped audit independent of any single PR. `<N>` is the wave number, `<topic>` is a short slug (`PRIV`, `SUPPLY`, `DOCS`, `S5` for SonarCloud, etc.), trailing `<NN>` is the index within that topic. | `F-W5-PRIV-03` (Wave 5 privacy audit, finding 03) |

**Severity is appended via inline label, not embedded in the ID** —
`F-PR29-A7-01 HIGH — release.md claims pip-audit at release time` —
so the ID itself stays stable across re-triage rounds even when severity
shifts.

**Historical IDs are grandfathered.** Don't rewrite `F-Wave1-NN` or
`F-R5-NN` references in old CHANGELOG / commit / docs entries; just
follow the canonical schemes for new reviews.

**Cross-reference rules:**

1. The first appearance of a finding ID in any review document must be
   followed by a one-line summary so readers don't have to grep elsewhere.
2. Absorption commits (the PR that lands the fix) cite the ID in the
   commit message body — `Absorbs F-PR29-A7-01` — not the title.
3. CHANGELOG entries cite IDs only when the finding warranted a public
   call-out (security, behaviour change); routine doc-drift fixes don't
   need an ID in the changelog.

## Quick self-review command

Before pushing:

```bash
# Formatter + linter + all tests + dry-run + 3 doc CI guards
ruff format . && ruff check . && pytest tests/ && \
forgelm --config config_template.yaml --dry-run && \
python3 tools/check_bilingual_parity.py --strict && \
python3 tools/check_anchor_resolution.py --strict && \
python3 tools/check_cli_help_consistency.py --strict
```

The first four are the historical gauntlet (`ruff` + `pytest` + `--dry-run`).
The three doc guards landed across Waves 3-5 (Faz 24, 26, 30J): bilingual
EN/TR spine parity, markdown anchor resolution, and CLI ↔ docs help-text
parity. Every PR runs all seven checks in CI; passing locally means CI will
too.

If that passes and the PR template is honest, you're ready.

## Pre-commit hooks (optional)

The repository ships a [`.pre-commit-config.yaml`](../../.pre-commit-config.yaml)
that mirrors the lint and format steps already run in CI, plus a
**local-only** `gitleaks` hook for credential scanning. Pre-commit hooks
are an **optional ergonomic optimization** for local development —
contributors who run `pre-commit install` get faster feedback (and the
gitleaks hook), but the hooks are **not** part of the PR enforcement
contract. CI is the single enforcement boundary: every required check
(`ruff check`, `ruff format --check`, the test matrix, the validate job,
the doc-parity guards) runs on every push and pull request regardless of
whether the author installed the hooks locally. Skipping a hook with
`SKIP=...` only suppresses the local run; it never bypasses CI.

**`gitleaks` is pre-commit-only.** It lives in `.pre-commit-config.yaml`
(`gitleaks/gitleaks` v8.18.0) and is **not** wired into any GitHub Actions
workflow — there is no `gitleaks` required check on the PR page.
Operators who want secret scanning on their local commits install it via
`pre-commit install`; CI relies on reviewer attention plus the
`tools/check_pip_audit.py` and SBOM gates for supply-chain coverage. If a
secret-scanning CI gate is ever added, update this section and the
required-check list together.
