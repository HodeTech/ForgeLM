# QMS: Access Control

> Quality Management System guidance — [YOUR ORGANIZATION]
> ISO 27001:2022 references: A.5.15, A.5.16, A.5.17, A.5.18, A.8.2, A.8.5
> SOC 2 references: CC1.5, CC6.1, CC6.2, CC6.3, CC6.5, CC8.1

## 1. Purpose

Define how the deployer organises identities, credentials, and access
rights around ForgeLM's audit-trail attribution model.

**Core contract:** every event ForgeLM emits is stamped with a
`FORGELM_OPERATOR` identity. The audit chain is only as strong as
the identity-management substrate that issues the operator value.
Two operators sharing the same `FORGELM_OPERATOR` are
indistinguishable in the chain — that is a deployer-side identity
hygiene failure, not a ForgeLM defect.

## 2. Scope

The following identity-bearing surfaces:

| Surface | What carries identity |
|---|---|
| `FORGELM_OPERATOR` env var | Stamped on every audit-log entry |
| Approval gate (`forgelm approve` / `reject`) | Operator id at approval time |
| Reverse-PII / purge subcommands | Operator id on each Article 15 / 17 event |
| Webhook lifecycle events | Operator id propagated to webhook payload |
| CI runner identity | The CI runner's service-account credential is the operator's "real" identity bound to `FORGELM_OPERATOR` |

ForgeLM does NOT itself implement RBAC, MFA, or directory federation —
those live in the deployer's IdP (Okta, Azure AD, Google Workspace,
Keycloak). What ForgeLM does is bind every audit entry to whatever
string the deployer puts in `FORGELM_OPERATOR` so the IdP audit log
and ForgeLM audit log can be cross-referenced.

## 3. Operator identity contract

### 3.1 Resolution order

ForgeLM resolves the operator identity at audit-event emit time:

1. `FORGELM_OPERATOR` env var (preferred — explicit set).
2. `getpass.getuser() + '@' + socket.gethostname()` (POSIX username@hostname fallback).
3. `FORGELM_ALLOW_ANONYMOUS_OPERATOR=1` permits emission without
   identity (intended for short-lived test runs only; ConfigError
   otherwise).

### 3.2 Required form

Use a **machine-readable, namespaced identifier** rather than a free-
text human name:

| Pattern | Example | Why |
|---|---|---|
| `<environment>/<purpose>/<runner-id>` | `prod/training/gh-runner-42` | Maps to CI runner pool |
| `<email>` | `alice@acme.example` | OK for human-driven approval |
| `oidc:<issuer>:<subject>` | `oidc:gha:repo:Acme/forgelm-pipelines:ref:refs/heads/main` | OIDC-token-bound |

Anti-patterns:

- `root` / `cemil` / `admin` — ambiguous, unrotatable.
- Empty / `unknown` — defeats the audit attribution purpose.

### 3.3 Rotation when staff change

ForgeLM's audit chain is **immutable**. Old `FORGELM_OPERATOR` IDs
remain in the chain forever. When an operator leaves:

1. **Revoke their CI runner identity** (rotate the OIDC trust /
   delete the IAM role).
2. **Do NOT re-issue the same `FORGELM_OPERATOR` string** to a new
   person — give them a new identifier so future entries are not
   confused with departed-staff entries.
3. **Audit prior actions** by grepping the audit log for their
   identifier; this is your termination-day forensic record.

### 3.4 `FORGELM_AUDIT_SECRET` rotation

The HMAC-chain signing key is derived **per audit run** as
`SHA-256(FORGELM_AUDIT_SECRET ‖ run_id)` (concatenation, see
`AuditLogger.__init__` at `forgelm/compliance.py:164`). Note: the per-output-dir salt
written to `<output_dir>/.forgelm_audit_salt` is a **distinct
concern** — it salts identifier hashing inside `forgelm purge` /
`forgelm reverse-pii` events (`_purge._resolve_salt` /
`_purge._hash_target_id`) and does NOT participate in chain-key
derivation. Treat the secret as a Tier-1 credential:

- **Length:** 32+ random bytes (256 bits of entropy).
- **Substrate:** KMS / Vault / equivalent. Never in `.env` checked
  into VCS.
- **Rotation cadence:** **between output-dir lifecycles** — every
  entry's HMAC is bound to the secret live at emit time, so rotation
  must occur AFTER archiving the current `audit_log.jsonl` +
  `.manifest.json` pair. Mid-output-dir rotation breaks
  `forgelm verify-audit --require-hmac` for the mixed-secret span
  (by design — the verifier's contract is "every entry's HMAC keys
  the same secret"). Set the cadence to match how often you cut a
  fresh `<output_dir>` (per release / per quarter / per project),
  and rotate immediately on suspected compromise + roll a new
  output-dir at the same time.
- **Rotation procedure:** archive the prior `<output_dir>` to
  write-once storage, generate a fresh secret in KMS, point the
  next pipeline run at a NEW `<output_dir>`. The prior chain remains
  verifiable with the prior secret (audit-log integrity is
  per-output-dir; ForgeLM does NOT support key migration on the
  chain — by design).

## 4. CI runner identity binding

The recommended pattern when running ForgeLM from CI:

### 4.1 GitHub Actions

```yaml
jobs:
  train:
    runs-on: ubuntu-latest
    permissions:
      id-token: write   # OIDC
      contents: read
    env:
      FORGELM_OPERATOR: gha:${{ github.repository }}:${{ github.workflow }}:run-${{ github.run_id }}
      FORGELM_AUDIT_SECRET: ${{ secrets.FORGELM_AUDIT_SECRET }}
    steps:
      - uses: actions/checkout@v5
      - run: pip install forgelm
      - run: forgelm --config config.yaml
```

**Why this binding:** the run id is unique per workflow execution; an
auditor can correlate a single audit-log entry to the GitHub Actions
run page in seconds.

### 4.2 GitLab CI

```yaml
train:
  variables:
    FORGELM_OPERATOR: "gitlab:${CI_PROJECT_PATH}:${CI_PIPELINE_ID}:job-${CI_JOB_ID}"
  # Inject FORGELM_AUDIT_SECRET from the project's "Settings → CI/CD
  # → Variables" panel as a *masked + protected* variable sourced
  # from the deployer's secret manager (HashiCorp Vault, AWS Secrets
  # Manager, etc.); never paste the literal secret into .gitlab-ci.yml.
  script:
    - forgelm --config config.yaml
```

### 4.3 Jenkins

```groovy
pipeline {
  agent any
  environment {
    FORGELM_OPERATOR = "jenkins:${env.JOB_NAME}:build-${env.BUILD_ID}"
    FORGELM_AUDIT_SECRET = credentials('forgelm-audit-secret')
  }
  stages {
    stage('Train') { steps { sh 'forgelm --config config.yaml' } }
  }
}
```

## 5. OS-level isolation

Within a single training host, multiple pipelines should NOT share an
output directory:

- One Unix user per pipeline (`forgelm-prod`, `forgelm-staging`,
  `forgelm-research`).
- `chmod 0700 <output_dir>` on each pipeline's directory.
- Audit logs in `<output_dir>` inherit the dir's perm; do not chmod
  individually unless your KMS substrate requires read access.
- The `.forgelm_audit_salt` file is `0600` (owner-read only) per
  ForgeLM's atomic O_EXCL creation in `_resolve_salt`.

## 6. Approval gate identity separation

Article 14 staging requires the approving operator to be different
from the training operator (segregation of duties — ISO A.5.3, SOC 2
CC1.5):

```bash
# Job 1 — training (CI runner identity)
FORGELM_OPERATOR="gha:Acme/pipelines:training:run-42" \
    forgelm --config config.yaml

# Job 2 — approval (human reviewer identity)
FORGELM_OPERATOR="alice@acme.example" \
    forgelm approve <run-id> --output-dir <output_dir>
```

ForgeLM does not enforce that the two identities differ — that is a
deployer-side IdP control. The audit chain records both, so an
auditor can detect violations:

```bash
# 1. Verify the chain integrity first (positional log_path; no
#    --output-dir / --json flags exist on this subcommand by design).
forgelm verify-audit ./outputs/audit_log.jsonl --require-hmac

# 2. Single-pass jq: slurp the audit log, project trainers + approvals,
#    join in-memory.  Keeps operator identifiers out of any shared
#    /tmp/ file (which would land 0644 / world-readable on multi-tenant
#    hosts and leak emails the deployer might have set as
#    FORGELM_OPERATOR per §3.2).  Output: TSV rows of every approval
#    where the approver matches the run's trainer (segregation
#    violation).
jq -rs '
    (map(select(.event == "training.started"))) as $trainers |
    map(select(.event == "human_approval.granted"))[] |
    . as $a |
    $trainers[] |
    select(.run_id == $a.run_id and .operator == $a.operator) |
    [.run_id, .operator] | @tsv
' ./outputs/audit_log.jsonl
```

## 7. Webhook secret separation

Webhook URLs belong in env, never in YAML:

```yaml
webhook:
  url_env: SLACK_WEBHOOK_URL   # resolved from env
  timeout: 10
```

ForgeLM does **not** HMAC-sign webhook bodies — there is no
`webhook.secret_env` field on `WebhookConfig` (see
`forgelm/config.py::WebhookConfig`). Destination-side attribution falls to (a)
HTTPS + URL-secrecy via `webhook.url_env`, (b) the
`FORGELM_OPERATOR` identity carried inside the curated payload, and
(c) the receiving system's own bearer-token / signed-request
controls (Slack signing secret, Teams connector token).

The webhook wire-format event vocabulary (5 events) is
`training.start`, `training.success`, `training.failure`,
`training.reverted`, and `approval.required` — Phase 8. The
in-process notifier method names use the `notify_*` prefix
(`notify_start`, `notify_success`, `notify_failure`,
`notify_reverted`, `notify_awaiting_approval`) and dispatch to those
five wire events. Every payload carries the `FORGELM_OPERATOR`
identity so the receiving system (Slack, Teams, custom
incident-management) can attribute the notification.

## 8. Verification checklist

For a deployer auditor walking access-control evidence:

- [ ] Every CI runner pipeline sets `FORGELM_OPERATOR` from a
      machine-readable namespace.
- [ ] No two active pipelines share an `FORGELM_OPERATOR` value.
- [ ] `FORGELM_AUDIT_SECRET` lives in a KMS / Vault substrate, never
      in VCS or a plain `.env` file.
- [ ] KMS audit log shows `FORGELM_AUDIT_SECRET` rotation paired
      with a fresh `<output_dir>` provisioning event — every KMS
      rotation event must have a corresponding new
      `<output_dir>/audit_log.jsonl.manifest.json` genesis pin within the
      same KMS-event timestamp window. Rotations with no matching
      genesis pin (i.e. mid-output-dir rotations) break
      `forgelm verify-audit --require-hmac` for the cross-secret
      span.
- [ ] Approval-gate identity differs from training identity for every
      `human_approval.granted` event in the past 90 days
      (sample-audit, not exhaustive).
- [ ] `<output_dir>` permission masks: `0700` (dir),
      `.forgelm_audit_salt` `0600`.
- [ ] No `FORGELM_OPERATOR=root` / `=admin` / `=unknown` events in
      the chain.
- [ ] Webhook URLs are env-resolved (`url_env`); no plaintext URL in
      any committed YAML. (ForgeLM does not HMAC-sign webhook bodies,
      so there is no `secret_env` field — destination-side controls
      are the receiving system's responsibility.)

## 9. Review

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | [DATE] | [AUTHOR] | Initial version (Wave 4 / Faz 23) |
