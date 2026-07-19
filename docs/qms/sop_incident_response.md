# SOP: Incident Response for AI Models

> Standard Operating Procedure — [YOUR ORGANIZATION]
> EU AI Act Reference: Article 17(1)(h)(i)
> ISO 27001:2022: A.5.24, A.5.25, A.5.26, A.5.27, A.6.8, A.8.15, A.8.16
> SOC 2: CC4.2, CC7.3, CC7.4, CC7.5, CC9.2

## 1. Purpose

Define the procedure for handling safety incidents, model failures,
**security incidents**, and corrective actions for deployed
fine-tuned models. Wave 4 / Faz 23 expansion: §4 covers the security-
incident playbook (audit-chain integrity, credential leak, supply-
chain CVE, webhook target compromise, GDPR DSARs) alongside the
existing AI-safety incident flow.

## 2. Incident Classification

| Severity | Definition | Response Time | Example |
|----------|-----------|--------------|---------|
| **Critical** | Model produces harmful, discriminatory, or dangerous output | Immediate (< 1 hour) | Safety classifier failure, harmful content generation |
| **High** | Model produces incorrect output affecting business decisions | < 4 hours | Wrong policy information, incorrect financial data |
| **Medium** | Model quality degradation detected | < 24 hours | Accuracy drop below threshold, increased hallucination |
| **Low** | Minor quality issue, cosmetic | < 1 week | Formatting errors, occasional irrelevant responses |

## 3. Incident Response Procedure

### 3.1 Detection

Incidents may be detected by:
- Runtime monitoring alerts (if `monitoring.alert_on_drift: true`)
- User/deployer reports
- Periodic quality audits
- ForgeLM webhook failure notifications

### 3.2 Immediate Actions

**For Critical/High:**
1. [ ] **Stop**: Remove model from production or switch to fallback
2. [ ] **Document**: Record incident details (input, output, timestamp, impact)
3. [ ] **Notify**: Alert AI Officer and affected stakeholders
4. [ ] **Preserve**: Save model artifacts and logs for investigation

### 3.3 Investigation

1. [ ] Reproduce the issue with the reported input
2. [ ] Check `audit_log.jsonl` for training run details
3. [ ] Review `safety_results.json` from the original training
4. [ ] Compare model behavior against baseline
5. [ ] Identify root cause (data issue, training issue, deployment issue)

### 3.4 Corrective Action

| Root Cause | Action |
|-----------|--------|
| Training data issue | Fix data → retrain → re-evaluate → redeploy |
| Safety regression | Revert to previous model version |
| Configuration error | Fix config → retrain with corrected parameters |
| Deployment error | Fix deployment, model is fine |

### 3.5 Post-Incident

1. [ ] Document root cause and resolution
2. [ ] Update risk assessment if new risks identified
3. [ ] Update safety test prompts to cover the incident scenario
4. [ ] Review and update this SOP if needed
5. [ ] For EU AI Act: report serious incidents to relevant authority within **15 days**

## 4. Security incidents — Wave 4 / Faz 23 expansion

The Wave 4 ISO 27001 / SOC 2 alignment closure adds the security-
incident playbook below. AI-safety incidents (§§1–3) and security
incidents both flow through this SOP; the differentiator is the
detection event class.

### 4.1 Audit-chain integrity violation

**Trigger:** `forgelm verify-audit` exits `6` — `EXIT_INTEGRITY_FAILURE`
(chain hash mismatch, manifest sidecar truncation, HMAC signature
mismatch). Exit `1` (the verifier never ran: missing log path, or
`--require-hmac` without the secret) and exit `2` (runtime I/O failure)
are operator / environment faults, not security incidents — correct them
and re-run before opening this playbook.

**Severity:** Critical.

**Runbook:**

1. [ ] **Isolate** the affected `<output_dir>` — `chmod 0500` on the
       directory to prevent further writes.
2. [ ] **Preserve evidence** — copy `audit_log.jsonl`, the
       genesis-manifest sidecar `audit_log.jsonl.manifest.json`,
       and `<output_dir>/.forgelm_audit_salt` to a write-once
       forensic substrate (S3 Object Lock, Azure Immutable Blob).
       (ForgeLM emits no per-line `.sha256` sidecar; the chain
       integrity proof lives inside each line's `_hmac` and
       `prev_hash` fields plus the genesis manifest.)
3. [ ] **Identify the last trusted entry** — run
       `forgelm verify-audit ./outputs/audit_log.jsonl --require-hmac --output-format json 2>&1 | tee verify.log`;
       the verifier stops at the first failure and reports the
       offending line number (`first_invalid_index`). **Read the exit
       code before you read anything else:** `6` is the integrity
       verdict — the log was read and it does not verify, so this is a
       genuine incident. `1` means the verifier never got as far as
       comparing anything (log path missing, or `--require-hmac` with
       the secret env var unset in this shell) and `2` means a runtime
       I/O failure; both are your setup, not evidence of tampering —
       fix and re-run before escalating. If you need the precise
       boundary, bisect manually with
       `head -n N audit_log.jsonl > tmp.jsonl` and re-run
       `verify-audit` against `tmp.jsonl` until the largest N that
       exits `0` is found — treating any non-zero code as "chain broken
       at N" will converge on the wrong boundary the moment a `1`
       (unexported secret) enters the loop. Everything up to that line
       is forensically trusted; everything after must be considered
       tainted. Note that the truncated `tmp.jsonl` carries no genesis
       manifest sidecar, so the bisect proves chain continuity only —
       the manifest cross-check runs against the original log in the
       step above.
4. [ ] **Notify** the AI Officer + Security team + DPO (if any
       PII-bearing event was after the bad line).
5. [ ] **Decide** whether to retain the tainted-tail entries as
       evidence (recommended) or roll back.
6. [ ] **Audit** the IdP for unauthorised write access to the
       `<output_dir>` substrate during the suspect time window.

### 4.2 Credential leak detected

**Trigger:** `forgelm audit` `_SECRET_PATTERNS` regex matches a
credential in the training corpus or a webhook log; OR an
external CVE / breach disclosure cites a token that was used.

**Severity:** Critical.

**Runbook:**

1. [ ] **Rotate the leaked credential immediately** at the issuing
       authority (HF Hub token, GitHub PAT, Slack webhook, OpenAI
       API key, AWS access key, etc.).
2. [ ] **Run `forgelm purge --row-id <leaked-row>`** against every
       corpus that includes the leaked credential row.
3. [ ] **Flag the run as memorisation-tainted** — the
       `data.erasure_warning_memorisation` event documents this.
4. [ ] **Document the rotation** in your KMS audit log; tie back to
       the ForgeLM `data.erasure_completed` event timestamp.
5. [ ] **Re-train from scratch** for high-risk deployments.
6. [ ] **Update the training-data-onboarding checklist** to require
       `forgelm audit <corpus>` pre-flight (the secrets scan is
       always-on; surface `secrets_summary` from the report and
       block the run on non-zero matches).

### 4.3 Supply-chain CVE flagged

**Trigger:** `pip-audit` nightly fails high-severity (Wave 4 / Faz
23 introduced this gate); OR a CVE advisory drops on a dependency
ForgeLM uses.

**Severity:** High.

**Runbook:**

1. [ ] **Pin to a safe version** in `pyproject.toml`.
2. [ ] **Rebuild the SBOM** for the new pinned set
       (`tools/generate_sbom.py`).
3. [ ] **Regenerate downstream artefacts** — re-run dependent
       training pipelines that consumed the affected dep.
4. [ ] **Notify deployers** if a model already shipped with the
       affected dep in its training-time env (`compliance_report.json`
       lists the env).
5. [ ] **File a tracking ticket** with the CVE id + the SBOM diff +
       the affected runs (use the audit log's `config_hash` (per-run manifest sidecar field)
       to identify them).

### 4.4 Webhook target compromised

**Trigger:** Slack / Teams / custom-webhook recipient confirms a
breach; OR `safe_post` `_mask` shows redacted Authorization headers
that an attacker may have observed.

**Severity:** High.

**Runbook:**

1. [ ] **Rotate the webhook URL and destination-side bearer token**
       immediately (URL is resolved via `webhook.url_env` from your
       secret manager; ForgeLM does not currently HMAC-sign webhook
       bodies).
2. [ ] **Walk the audit chain** to confirm the attacker did not splice
       events into the recipient: filter
       `audit_log.jsonl` to the core lifecycle events
       (`jq 'select(.event | test("^(training\\.|pipeline\\.|human_approval\\.)"))'`)
       and confirm each lifecycle event in the `run_id` window matches
       the expected sequence. Note: webhook wire-event names
       (`training.start`, `training.success`, etc.) differ from
       audit-log event names (`training.started`, `pipeline.completed`,
       etc.) — only audit-log events appear in `audit_log.jsonl`;
       `notify_*` are internal method names never written to the log.
       Mismatched timestamps or unexpected entries for a `run_id` are
       the splice signal.
3. [ ] **Check `safe_post` error logs** for masked Authorization
       headers post-rotation — confirm the attacker no longer holds
       a valid token.
4. [ ] **Audit the receiving system's** logs for unexpected actions
       triggered by spliced events (Slack channel posts, Teams
       cards, Jira tickets).

### 4.5 GDPR Article 15 (right of access) request

**Trigger:** Data subject submits an access request via deployer's
DSAR portal or contact form.

**Severity:** Medium (regulatory deadline 30 days).

**Runbook:**

1. [ ] **Verify subject identity** per deployer's DSAR procedure.
2. [ ] **Run** `forgelm reverse-pii --query <verified-identifier>
       --type <category> data/*.jsonl --output-dir <run-dir>`
3. [ ] **Hash-mask scan** for any corpus that ForgeLM masked
       (`forgelm reverse-pii --query <id> --salt-source per_dir
       --output-dir <run-dir>`).
4. [ ] **Compose response letter** per DSAR template; cite the run's
       `data.access_request_query` audit-event id.
5. [ ] **Retain** the response letter alongside the audit chain.

### 4.6 GDPR Article 17 (right to erasure) request

**Trigger:** Subject submits an erasure request.

**Severity:** Medium (regulatory deadline 30 days; some EU member
states require shorter response).

**Runbook:**

1. [ ] **Verify identity** per deployer's DSAR procedure.
2. [ ] **Run** `forgelm purge --row-id <verified-id> --corpus
       data/<file>.jsonl --output-dir <run-dir>`.
3. [ ] **Check** for `data.erasure_warning_memorisation` event —
       if a model trained on the row has a `final_model/` artefact,
       memorisation residual risk applies.
4. [ ] **Communicate the memorisation caveat** to the subject in
       the response letter (template: "we deleted the row but a
       previously-trained model may have memorised it; we will
       retrain or apply additional safeguards as appropriate").
5. [ ] **For high-stakes deployments** (financial advice, medical
       triage): retrain from scratch.
6. [ ] **Retain** the audit-event chain proving completion + warnings
       fired.

## 5. Serious Incident Reporting (EU AI Act)

Under Article 73, providers must report serious incidents to market surveillance authorities. A "serious incident" includes:
- Death or serious damage to health
- Serious infringement of fundamental rights
- Serious disruption to critical infrastructure

**Report to:** National market surveillance authority of the affected EU member state
**Timeline:** Within 15 days of becoming aware

## 6. Review

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | [DATE] | [AUTHOR] | Initial version |
| 1.1 | 2026-05-05 | Wave 4 / Faz 23 | Added §4 security-incident playbook (audit-chain integrity, credential leak, supply-chain CVE, webhook compromise, GDPR Art. 15/17 DSARs); ISO 27001:2022 + SOC 2 control mapping in header |
| 1.2 | 2026-07-19 | `EXIT_INTEGRITY_FAILURE` cycle | §4.1 retargeted onto `EXIT_INTEGRITY_FAILURE` (6): the trigger is now exit `6`, not "non-zero", and the last-trusted-entry bisect distinguishes `6` (integrity verdict) from `1` (verifier never ran) / `2` (I/O failure) so a setup fault cannot be mistaken for a tamper boundary |
