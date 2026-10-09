# Security Policy

> **How to report a security vulnerability in ForgeLM privately, and what happens after you do.**
> For reporters, users and contributors. Ordinary bugs go to the [issue tracker](https://github.com/HodeTech/ForgeLM/issues).

## Reporting a Vulnerability

Please **do not open a public issue, discussion or pull request** for a security vulnerability, and do not post exploit details anywhere public.

Report it privately through GitHub instead: open the repository's **Security** tab and choose **Report a vulnerability**, or go directly to [github.com/HodeTech/ForgeLM/security/advisories/new](https://github.com/HodeTech/ForgeLM/security/advisories/new). Only you and the maintainers can see the report.

A useful report includes:

- the ForgeLM version (`forgelm --version`) or commit you tested;
- the affected component — CLI subcommand, module, or documented recipe;
- the steps or a minimal proof of concept that reproduce the problem;
- the impact: which documented guarantee is defeated, or what an attacker gains;
- a suggested fix, if you have one.

## What Counts as a Vulnerability

ForgeLM makes security and compliance promises, so a vulnerability is anything that lets someone defeat them. Examples:

- a tampered artefact or audit log that a `verify-*` command accepts (exit `0` where exit `6` is the documented tampering signal — see [`docs/reference/verify_audit.md`](docs/reference/verify_audit.md));
- a safety, quality, approval or data-audit gate that passes what it is documented to block;
- secrets or personal data that leak past the audit, masking or erasure features;
- code execution, unbounded resource use or crashes caused by an untrusted model, dataset or document file;
- unintended network egress, or a request that bypasses the SSRF-guarded HTTP layer;
- file operations that reach outside the configured output or data paths.

A defect with no security impact — a wrong result, a crash on a malformed config you wrote yourself, a documentation error — is an ordinary bug: please use the issue tracker.

A vulnerability in a third-party dependency should be reported to that project. If ForgeLM's declared version range or its own code makes a known dependency vulnerability reachable, report it here; [`docs/reference/supply_chain_security.md`](docs/reference/supply_chain_security.md) describes how dependencies and releases are secured.

## Supported Versions

Security fixes are made on `main` and shipped in the next release. Upgrade to the latest release to receive them; older releases are not patched separately.

## What Happens Next

1. A maintainer reviews the report in the private advisory and may ask you questions there.
2. Confirmed vulnerabilities are fixed privately, using the advisory's temporary private fork when needed.
3. The advisory is published together with the release that contains the fix, crediting you unless you prefer to stay anonymous.

Maintainers also track security findings from their own reviews in private advisories, so some planned security work is intentionally not visible in the public issue list until it is fixed.
