# ForgeLM Governance

> **How decisions are made in ForgeLM, who makes them, and how you can take on
> more responsibility over time.** For how to contribute, see
> [CONTRIBUTING.md](CONTRIBUTING.md); for behaviour expectations, see
> [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## Current model

ForgeLM is **maintainer-led**. The project has a small group of maintainers who
are accountable for the code, the standards and the releases — today, one
(see [MAINTAINERS.md](MAINTAINERS.md)). There is no foundation and no steering
committee; the goal of this document is not to describe a large organisation but
to make the path into one explicit.

The project's product principles — reliability before features, enterprise
differentiation over feature parity, every capability config-driven and testable
— are recorded in [`docs/roadmap.md`](docs/roadmap.md) and are not restated here.
How decisions get made rests on two rules:

1. **The standards are the rulebook.** `docs/standards/` holds the engineering
   standards, `docs/roadmap/` holds what ships next, and the
   [CHANGELOG.md](CHANGELOG.md) records what shipped. A decision that changes
   behaviour is written into one of them; a decision that exists only in a chat
   is not a decision.
2. **Public reasoning.** Non-trivial proposals, breaking changes and anything
   that changes a documented contract are discussed in an **issue** before a
   pull request is opened, so the reasoning is reviewable and linkable.

## Roles

| Role | What they do | How it is granted |
|---|---|---|
| **Contributor** | Reports bugs, opens pull requests, reviews others' changes, improves docs | Anyone; no appointment |
| **Maintainer** | Sets direction, merges to `development`, cuts releases, holds the standards | A maintainer invites them (see below) |

Everyone contributing is bound by the
[Code of Conduct](CODE_OF_CONDUCT.md) and asked to follow the
[code-review standard](docs/standards/code-review.md).

## How decisions are made

- **Routine changes** — bug fixes, documentation and mirror updates, dependency
  bumps — are decided in the pull request. A pull request merges once the
  required status checks pass, every review conversation is resolved, and a
  maintainer has reviewed it. The exact gate is a branch-protection setting, not
  this document. While the project has one maintainer, that maintainer merges
  their own pull requests under the same conditions, which is what
  [docs/standards/code-review.md](docs/standards/code-review.md) records.
- **Non-trivial changes** — a new config field, a new module, a new optional
  dependency, a change to an exit code, the audit-event vocabulary or a
  documented guarantee — start as an issue. Work that has no issue may be asked
  to open one first, so the design is agreed before code is written. If the
  change is larger than one issue, it becomes a roadmap phase under
  [docs/roadmap/](docs/roadmap/).
- **Breaking changes** to the YAML schema or the CLI contract require a
  deprecation period. Removing a YAML field is a MAJOR change; the release a
  removal lands in is named by a single constant,
  `DEPRECATION_REMOVAL_VERSION` in `forgelm/config.py`, and the
  [release standard](docs/standards/release.md) governs the sequence.
- **Disagreement** is resolved by discussion on the issue. If a consensus does
  not form, the maintainer decides and records the reasoning in the issue and,
  when it sets a lasting constraint, in
  [docs/roadmap/risks-and-decisions.md](docs/roadmap/risks-and-decisions.md).
- **Security reports** do not follow this path. They go through a private
  advisory as [SECURITY.md](SECURITY.md) describes.

## Becoming a maintainer

There is no exam and no vote — and no promise. A maintainer role is offered when
the project needs a second maintainer and someone has earned it, not
automatically after a number of merged pull requests. The invitation is normally
made when all of the following hold:

- they have landed a handful of pull requests across more than one area;
- they have reviewed other people's pull requests with useful, specific
  feedback, and respected the standards while doing so;
- they have shown judgement on the edge cases — an error path, a documented
  contract, a supply-chain or compliance question;
- they are willing to be reachable, and to triage issues and review pull
  requests on a predictable cadence rather than only when they feel like it.

Area ownership follows the same idea: `MAINTAINERS.md` names who owns which area,
and `.github/CODEOWNERS` routes reviews there. The list is kept accurate rather
than flattering, and a maintainer who needs to step back can say so; that is not
held against them.

## Access and continuity

GitHub permissions are granted per role, and the smallest permission that does
the job is the default:

| Role | Repository permission | Why |
|---|---|---|
| Contributor | None — fork and pull request | Every contribution arrives as a pull request, so no repository access is needed |
| Maintainer | *Write* | Can merge to `development`, push branches and manage issues |
| Owner | *Admin* | Also carries branch protection, the tag ruleset and the release workflow's environment approval |

*Admin* is not the next rung of the contribution ladder — it is the ability to
change the rules the other roles work under, so it stays with the project owner
and moves only on a deliberate decision, with two-factor authentication required
on any account that holds it.

**Continuity: a known gap, recorded rather than promised.** There is one owner
today, and nothing in the release path is asynchronous — the same single account
holds the tag ruleset and approves the release workflow's `pypi` environment. An
unplanned absence therefore stops merges, releases and security advisories at
once. The fix is a second organisation owner (or an organisation-level owner with
the release environment's approval); it is not scheduled, and this paragraph is
what says so.

## Releases

Releases are cut from `main` by a maintainer, following
[docs/standards/release.md](docs/standards/release.md). `development` is the
integration branch and the default branch; changes land there first and reach
`main` through a release pull request. Only maintainers merge to `main` and tag
releases.

## Changing this document

This document is a normal file in the repository: propose a change with a pull
request, and explain the reasoning in the pull request description.
