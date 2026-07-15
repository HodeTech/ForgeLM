# ForgeLM Engineering Standards

> **What this is:** A normative description of how code, docs, and process work in ForgeLM. Not aspirational — extracted from the actual state of [`pyproject.toml`](../../pyproject.toml), [`forgelm/`](../../forgelm/), [`tests/`](../../tests/), and [`.github/`](../../.github/).
>
> **Who this is for:** Contributors (human or AI) making changes. Read the relevant standard *before* the first commit of a PR.
>
> **How to change a standard:** PR to this directory. Every rule here cites a concrete file — changing the rule requires either updating the cited code first or discussing why the cited code should change.

## The standards

| Standard | Scope | Read before |
|---|---|---|
| [coding.md](coding.md) | Python style, type hints, docstrings, naming | Any `.py` change |
| [architecture.md](architecture.md) | Module boundaries, config flow, optional deps | New modules, refactors |
| [regex.md](regex.md) | Regex hygiene, ReDoS exposure, fixture fragmentation | Any `re.compile` / `re.sub` change |
| [error-handling.md](error-handling.md) | Exceptions, exit codes, user-facing messages | Any error path work |
| [logging-observability.md](logging-observability.md) | Loggers, structured events, webhooks, audit log | Anything that emits output |
| [testing.md](testing.md) | Test layout, fixtures, coverage, CI gates | Every feature PR |
| [documentation.md](documentation.md) | Markdown structure, docstrings, mermaid, link hygiene | Any doc change |
| [localization.md](localization.md) | TR/EN mirror rules, CLI language policy | User-facing strings |
| [code-review.md](code-review.md) | PR template, review checklist, merge criteria | Every PR |
| [release.md](release.md) | Semver, CHANGELOG, PyPI publish, version bumps | Release time only |

## Reading order for new contributors

If you're new to the codebase, read them in this order — each one takes 5-10 minutes:

1. [architecture.md](architecture.md) — mental model of how modules fit
2. [coding.md](coding.md) — what your Python should look like
3. [testing.md](testing.md) — what CI will demand
4. [error-handling.md](error-handling.md) + [logging-observability.md](logging-observability.md) — how failures and signals flow
5. [documentation.md](documentation.md) + [localization.md](localization.md) — for the docs side
6. [code-review.md](code-review.md) — last stop before opening the PR
7. [release.md](release.md) — only when cutting a version

## Meta-rules for standards themselves

These apply to this directory:

1. **Every rule must cite real code.** No rule may say "the style is X" without pointing to a file that demonstrates X. If the codebase and the rule disagree, one of them is wrong — the rule does not automatically win.
2. **Keep each standard skimmable — soft ceiling ~400 lines.** A standard you can't skim in 5-10 minutes gets ignored. Push deep examples to [guides/](../guides/) and link. This is a soft cap, not a hard gate: several standards (`release.md`, `documentation.md`) carry operationally load-bearing detail (worked release sequences, CI guard inventories) that would fragment badly if force-split just to hit a line count. If a standard meaningfully exceeds ~400 lines, that's a prompt to look for a section that belongs in `guides/` instead — not an automatic rule violation.
3. **Prefer imperative, testable statements.** "Log errors at `ERROR` level before `sys.exit()`" is better than "log errors appropriately."
4. **Update with code.** A PR that violates a standard must either (a) get rejected, (b) fix the violation, or (c) update the standard in the same PR with reasoning.

## Relationship to other docs

- **[../guides/](../guides/)** — Tutorial-style documents for end users. Different audience: users of the library, not contributors.
- **[../reference/](../reference/)** — API/config reference material for end users.
- **[../qms/](../qms/)** — Quality management SOPs aimed at regulated-industry deployers (EU AI Act Art. 17). Partially overlaps with this directory but is organizational, not technical.
- **[../../CONTRIBUTING.md](../../CONTRIBUTING.md)** — Entry-level contributor guide; should summarize and link to standards here.
- **[../../CLAUDE.md](../../CLAUDE.md)** — AI-agent-specific guidance. References standards but is not a replacement.
