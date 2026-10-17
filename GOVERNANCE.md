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

Three principles shape every decision:

1. **The standards are the rulebook.** `docs/standards/` holds the engineering
   standards, `docs/roadmap/` holds what ships next, and the
   [CHANGELOG.md](CHANGELOG.md) records what shipped. A decision that changes
   behaviour is written into one of them; a decision that exists only in a chat
   is not a decision.
2. **Reliability before features.** A capability ships with tests, docs and CI
   coverage, or it does not ship. See
   [docs/standards/testing.md](docs/standards/testing.md).
3. **Public reasoning.** Non-trivial proposals, breaking changes and anything
   that changes a documented contract are discussed in an **issue** before a
   pull request is opened, so the reasoning is reviewable and linkable.

## Roles

| Role | What they do | How it is granted |
|---|---|---|
| **Contributor** | Reports bugs, opens pull requests, reviews others' changes, improves docs | Anyone; no appointment |
| **Reviewer** | Reviews pull requests in an area and confirms findings | A maintainer says so in an issue, after several helpful reviews |
| **Maintainer** | Sets direction, merges to `development`, cuts releases, holds the standards | A maintainer invites them (see below) |

Everyone contributing is bound by the
[Code of Conduct](CODE_OF_CONDUCT.md) and asked to follow the
[code-review standard](docs/standards/code-review.md).

## How decisions are made

- **Routine changes** — bug fixes, documentation and mirror updates, dependency
  bumps — are decided in the pull request. One maintainer approval merges them.
- **Non-trivial changes** — a new config field, a new module, a new optional
  dependency, a change to an exit code, the audit-event vocabulary or a
  documented guarantee — start as an issue. Work that has no issue may be asked
  to open one first, so the design is agreed before code is written. If the
  change is larger than one issue, it becomes a roadmap phase under
  [docs/roadmap/](docs/roadmap/).
- **Breaking changes** to the YAML schema or the CLI contract require a
  deprecation period. Removing a YAML field is a MAJOR change; the target
  release is single-sourced in `forgelm/config.py` and the
  [release standard](docs/standards/release.md) governs the sequence.
- **Disagreement** is resolved by discussion on the issue. If a consensus does
  not form, the maintainer decides and records the reasoning in the issue and,
  when it sets a lasting constraint, in
  [docs/roadmap/risks-and-decisions.md](docs/roadmap/risks-and-decisions.md).
- **Security reports** do not follow this path. They go through a private
  advisory as [SECURITY.md](SECURITY.md) describes.

## Becoming a maintainer

There is no exam and no vote. A **reviewer** becomes a **maintainer** when a
current maintainer invites them, and that invitation is normally offered when
all of the following hold:

- they have landed a handful of pull requests across more than one area;
- they have reviewed other people's pull requests with useful, specific
  feedback, and respected the standards while doing so;
- they have shown judgement on the edge cases — an error path, a documented
  contract, a supply-chain or compliance question;
- they are willing to be reachable, and to triage issues and review pull
  requests on a predictable cadence rather than only when they feel like it.

Area ownership follows the same idea: `MAINTAINERS.md` names who owns which
area, and `.github/CODEOWNERS` routes reviews there. A maintainer who becomes
unavailable is asked whether they want to step back; the project prefers an
accurate list over a flattering one, and stepping back is not held against
anyone.

## Releases

Releases are cut from `main` by a maintainer, following
[docs/standards/release.md](docs/standards/release.md). `development` is the
integration branch and the default branch; changes land there first and reach
`main` through a release pull request. Only maintainers merge to `main` and tag
releases.

## Changing this document

This document is a normal file in the repository: propose a change with a pull
request, and explain the reasoning in the pull request description.

## Attribution

The shape of this document — a written role ladder, area ownership, and the
"decide in an issue, record in the standards" rule — follows common practice in
established open-source projects; it is adapted, not copied, to ForgeLM's
config-driven and evidence-first way of working.
