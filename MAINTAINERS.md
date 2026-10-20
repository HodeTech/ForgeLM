# ForgeLM Maintainers

> **Who maintains ForgeLM, what each of them owns, and how to reach them.**
> See [GOVERNANCE.md](GOVERNANCE.md) for how the roles work and how to take one
> on, and [CONTRIBUTING.md](CONTRIBUTING.md) for how to contribute.

## Current maintainers

| GitHub | Areas | Reachable for |
|---|---|---|
| [@cemililik](https://github.com/cemililik) | All areas — the repository's `area:` label set (cli, compliance, config, data-audit, dev-tooling, eval-gates, gdpr-approval, ingestion, integrations, model-data, onboarding, operator-cmds, packaging-ci, pipeline, post-training, site, trainer, verify) | Triage, design questions, reviews, releases |

The project has a single maintainer today. When a co-maintainer takes
responsibility for an area, they are added here and to
[`.github/CODEOWNERS`](.github/CODEOWNERS), which routes reviews for that area to
them. The "Areas" column mirrors the repository's `area:` labels, so an issue
labelled `area: site` and this table name the same area.

This table is also the list of who may complete triage (see [Triage](#triage)).
Adding a maintainer here means adding the login to `MAINTAINER_LOGINS` in
`tools/assign_on_comment.py` and `tools/triage_gate.py`; the tests fail until the
three agree.

## How to reach a maintainer

- **A bug or a feature request** — open an
  [issue](https://github.com/HodeTech/ForgeLM/issues/new/choose).
- **A question** — start a
  [discussion](https://github.com/HodeTech/ForgeLM/discussions); questions are not
  filed on the public issue tracker. Asking in public means the answer is
  searchable for the next person.
- **A security vulnerability** — never in public. Use the repository's
  **Security** tab, as [SECURITY.md](SECURITY.md) describes.
- **Anything covered by the Code of Conduct** — follow the enforcement section of
  the [Code of Conduct](CODE_OF_CONDUCT.md#enforcement).

## Review cadence

Issues are triaged and pull requests are reviewed on a best-effort basis, with an
initial-reply target of **72 hours** — the same window [SECURITY.md](SECURITY.md)
commits to for a vulnerability report, so the project has one number rather than
two. An unanswered first pull request is the most expensive thing a young project
can ship, so a **first-time contributor**'s pull request is answered first. If a
pull request has not been reviewed for a while, a polite nudge on it is welcome;
it is not rude.

## Triage

Every new issue starts with the `needs-triage` label: the issue forms add it, and
the *Triage gate* workflow adds it to any issue created another way, whoever
opened it. Removing the label is a maintainer's sign-off, and only a
maintainer's removal stands — the workflow puts the label back after anyone
else's. Contributors are told not to work on an issue that still carries it
([CONTRIBUTING.md](CONTRIBUTING.md#issue-triage)), so the queue is the project's
front door. The routine:

1. **Find the queue:** [open issues labelled `needs-triage`](https://github.com/HodeTech/ForgeLM/issues?q=is%3Aissue+is%3Aopen+label%3Aneeds-triage).
2. **Check the report against `development`.** Is it still true? A duplicate?
   Already tracked (`backlog: known`) or already fixed? Content that should not be
   public — a vulnerability, a secret, personal data — is edited out and moved to a
   private advisory first ([SECURITY.md](SECURITY.md)).
3. **Decide, and record the decision on the issue.** Accept it (add the type,
   `area:`, `severity:` and `wave:` labels, and set the project card's fields), ask
   the reporter for what is missing (the label stays), or close it as `duplicate`,
   `invalid` or `wontfix` with the reason.
4. **Remove `needs-triage` last,** when the issue carries its classification or is
   closed. An open issue without the label is one a maintainer has triaged.

A pull request that links an issue still carrying the label waits for this step
rather than for a review.

## Contributors

This file names maintainers and area owners only; it is not the contributor list.
Everyone whose work is merged appears in the repository's commit history and on
its GitHub contributors page, and non-maintainer contributions are credited in
the release notes.
