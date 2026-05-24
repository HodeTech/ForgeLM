---
title: GDPR / KVKK
description: Data protection compliance — PII minimisation, data subject rights, and audit evidence.
---

# GDPR / KVKK

GDPR (Europe) and KVKK (Türkiye) impose data protection requirements on personal data used in training. ForgeLM's role: prevent personal data from entering training in the first place, document what gets ingested, and produce evidence for data subject requests.

## What GDPR / KVKK require for training data

| Principle | Article | What it means for training |
|---|---|---|
| **Lawfulness** | GDPR Art. 5(1)(a) | You must have a lawful basis for processing personal data. |
| **Purpose limitation** | GDPR Art. 5(1)(b) | Data collected for purpose A can't be used for unrelated purpose B. |
| **Data minimisation** | GDPR Art. 5(1)(c) | Don't collect or retain personal data beyond what's necessary. |
| **Accuracy** | GDPR Art. 5(1)(d) | Keep data accurate; correct or erase incorrect data. |
| **Storage limitation** | GDPR Art. 5(1)(e) | Don't retain longer than necessary. |
| **Integrity & confidentiality** | GDPR Art. 5(1)(f) | Protect against unauthorised access. |
| **Accountability** | GDPR Art. 5(2) | You must be able to demonstrate compliance. |

KVKK (Türkiye) mirrors these principles closely.

## How ForgeLM addresses each

### Data minimisation (Art. 5(1)(c))

PII masking at ingest replaces personal identifiers with placeholders:

```yaml
ingestion:
  pii_mask:
    enabled: true
    locale: "tr"
    categories: ["email", "phone", "iban", "id_tr"]
```

By the time data lands in JSONL, identifiable subjects are removed. See [PII Masking](#/data/pii-masking).

### Accountability (Art. 5(2))

Every audit produces `data_audit_report.json` documenting:

- Detected PII categories and counts (before masking).
- Source attribution (which document each row came from).
- Quality and language distribution.
- SHA-256 manifest for tamper-evidence.

These reports flow into the Annex IV bundle. When a regulator asks "what personal data was in your training set?", you have a structured answer.

### Storage limitation (Art. 5(1)(e))

ForgeLM doesn't retain raw user data — it produces JSONL artifacts you control. For automated retention enforcement:

```yaml
ingestion:
  retention:
    raw_documents:
      ttl_days: 90                       # auto-delete originals after N days
    audit_reports:
      ttl_days: 365
```

(The actual deletion is your storage layer's responsibility; ForgeLM just records the intended TTL in the audit log.)

## Data subject requests

Most-common request types and how ForgeLM helps:

### Right of access (Art. 15)

"What personal data do you hold about me?"

Run reverse-PII on your training data:

```shell
$ forgelm reverse-pii --query "ali@example.com" data/*.jsonl
No matches found in masked data.
```

Because PII was masked at ingest, no specific person's data is recoverable from the model. The audit report confirms this.

### Right to erasure (Art. 17)

"Delete my data."

If a person's data was in your training set:
1. The masked JSONL doesn't contain their identifying info — already minimised.
2. Source documents may still — drop them from your raw store and re-ingest if necessary.
3. The model may have memorised some details — see "model-level erasure" below.

### Model-level erasure

LLMs can memorise rare strings from training. Even with PII masking, removing all traces from a deployed model is hard. ForgeLM's defences:

- **Prevent memorisation:** PII masking, deduplication (memorised data is usually duplicated data).
- **Detect memorisation:** the audit step flags rows that overlap with known PII patterns.
- **Re-train as last resort:** if a specific subject's data leaked despite masking, re-train without that source.

For zero-tolerance scenarios (medical, legal), pair PII masking with manual review before training.

## DPIA (Data Protection Impact Assessment)

For high-risk processing, GDPR Art. 35 requires a DPIA. ForgeLM doesn't write your DPIA, but the audit bundle provides input data:

- Risk classification → from `compliance.risk_classification`.
- Personal data inventory → from `data_audit_report.json`.
- Mitigations applied → from `compliance.risk_assessment.mitigations`.
- Residual risks → from `compliance.risk_assessment.residual_risks`.

For DPIA work, pair the inputs above with the QMS [risk-treatment plan](https://github.com/HodeTech/ForgeLM/blob/main/docs/qms/risk_treatment_plan.md) and the [Statement of Applicability](https://github.com/HodeTech/ForgeLM/blob/main/docs/qms/statement_of_applicability.md) (GitHub QMS templates shipped with the toolkit). A dedicated DPIA template is on the roadmap; the risk-treatment plan covers the same ground for now.

## Configuration reference

```yaml
compliance:
  data_protection:
    framework: "GDPR"                          # GDPR | KVKK | both
    lawful_basis: "legitimate-interest"        # consent | contract | legal-obligation | ...
    purpose: "Customer-support assistant for X"
    data_controller: "Acme Corp"
    data_subjects: "telecom customers"
    retention_basis: "model lifecycle (~3 years) plus audit period"
    international_transfers:
      enabled: false                          # set true if training data crosses borders
      safeguards: "Standard Contractual Clauses 2021/914"
```

## Common pitfalls

:::warn
**Treating PII masking as DPIA-replacement.** Masking is a technical mitigation, not a legal assessment. A DPIA is a documented analysis of risks, mitigations, and residual harm — required separately for high-risk processing.
:::

:::warn
**Skipping data audit on internal data.** Internal data is the most common source of inadvertent personal data exposure (employee records leaking into customer-support training). Audit everything.
:::

:::warn
**International transfers.** If your training data crosses jurisdiction boundaries (Turkish data trained in EU, EU data trained in US), additional safeguards apply. Set `international_transfers.enabled: true` and document the safeguards.
:::

:::tip
For sectors like health and finance, consult a privacy specialist *before* the first training run. ForgeLM's defaults are sensible but they don't replace sector-specific legal review.
:::

## See also

- [PII Masking](#/data/pii-masking) — the technical implementation.
- [Compliance Overview](#/compliance/overview) — broader context.
- [Annex IV](#/compliance/annex-iv) — packaged compliance evidence.
