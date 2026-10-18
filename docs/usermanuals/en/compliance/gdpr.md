---
title: GDPR / KVKK
description: Data protection compliance — PII minimisation, data subject rights, and audit evidence.
---

# GDPR / KVKK

GDPR (Europe) and KVKK (Türkiye) impose data protection requirements on personal data used in training. ForgeLM's role is narrower than keeping personal data out of training: it masks the identifier formats it can detect, reports what an audit found, and gives you commands for access and erasure requests. It does not decide your lawful basis, it does not find names or other free-text personal data, and it does not make a trained model anonymous.

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

PII masking at ingest replaces the identifier formats ForgeLM detects with a placeholder:

```shell
$ forgelm ingest ./policies/ --recursive --strategy markdown --pii-mask --output data/policies.jsonl
```

The detectors cover email, phone, credit card, IBAN, and the Turkish, German, French and US national-ID formats; every match becomes `[REDACTED]`. **Names, addresses, dates of birth, health data and other free text are not detected**, and the patterns are tuned to miss a borderline match rather than redact ordinary prose. Masking reduces the personal data in the JSONL; it does not remove identifiable subjects from it. See [PII Masking](#/data/pii-masking).

### Accountability (Art. 5(2))

Every `forgelm audit` run writes `data_audit_report.json`. It records:

- Detected PII and secret categories with their counts, for the data you audited. Audit the unmasked source to see what a masker has to catch; audit the masked output to see what got through.
- Near-duplicate, quality and cross-split-overlap summaries, and an optional Croissant card.
- The audit's own metadata: when it ran, and the path and literal input it was given.

The report does not attribute rows to source documents, carries no hash manifest, and is a plain JSON file: it is not itself written to the audit log. Erasure and access requests (`forgelm purge`, `forgelm reverse-pii`) are, in the hash-chained log that `forgelm verify-audit` checks. When the report sits in `training.output_dir`, it feeds the Article 10 data-governance section of the Annex IV bundle, so a regulator's "what personal data was in your training set?" has a structured answer: what the detectors found, not a statement that none exists.

### Storage limitation (Art. 5(1)(e))

ForgeLM doesn't retain raw user data — it produces JSONL artifacts you control. Retention horizons can be declared and are **reported, not enforced**:

```yaml
retention:
  raw_documents_retention_days: 90
  ephemeral_artefact_retention_days: 365
```

`forgelm purge --check-policy --config config.yaml` lists artefacts that are past their horizon and always exits 0. Nothing is deleted automatically and nothing is written to the audit log for it; deleting what is overdue is your storage layer's job. (`retention.enforce` is reserved: no command acts on it yet.)

## Data subject requests

Most-common request types and how ForgeLM helps:

### Right of access (Art. 15)

"What personal data do you hold about me?"

Search your training data for the person's identifier:

```shell
$ forgelm reverse-pii --query "ali@example.com" data/*.jsonl
```

The scan reports the lines where the identifier appears; zero matches is a valid answer. It finds what is literally in the files you point it at. Masking only removes the identifier formats it detects, so a name or an undetected variant can remain, and the raw source documents are not covered by a scan of the masked JSONL: search those separately. ForgeLM cannot tell you what a trained model remembers.

### Right to erasure (Art. 17)

"Delete my data."

If a person's data was in your training set:
1. Find the rows with `forgelm reverse-pii`, then remove them with `forgelm purge --row-id <id> --corpus <file>`; every erasure is written to the audit log.
2. Source documents may still hold it — drop them from your raw store and re-ingest if necessary.
3. The model may have memorised some details — see "model-level erasure" below.

### Model-level erasure

LLMs can memorise rare strings from training. Even with PII masking, removing all traces from a deployed model is hard. ForgeLM's defences:

- **Reduce what goes in:** PII masking and deduplication (memorised data is usually duplicated data).
- **Memorisation is not measured:** ForgeLM does not test whether a trained model reproduces a given string.
- **Re-train as last resort:** if a specific subject's data leaked despite masking, re-train without that source.

For zero-tolerance scenarios (medical, legal), pair PII masking with manual review before training.

## DPIA (Data Protection Impact Assessment)

For high-risk processing, GDPR Art. 35 requires a DPIA. ForgeLM doesn't write your DPIA, but the audit bundle provides input data:

- Risk classification → from `compliance.risk_classification`.
- Personal data inventory → from `data_audit_report.json`.
- Mitigations applied → from `risk_assessment.mitigation_measures`.
- Foreseeable misuse / residual risks → from `risk_assessment.foreseeable_misuse` (free-text list; a dedicated residual_risks field does not exist in the schema).

For DPIA work, pair the inputs above with the QMS [risk-treatment plan](https://github.com/HodeTech/ForgeLM/blob/main/docs/qms/risk_treatment_plan.md) and the [Statement of Applicability](https://github.com/HodeTech/ForgeLM/blob/main/docs/qms/statement_of_applicability.md) (GitHub QMS templates shipped with the toolkit). A dedicated DPIA template is on the roadmap; the risk-treatment plan covers the same ground for now.

## Configuration reference

ForgeLM has no YAML surface for GDPR legal metadata (lawful basis, data controller, etc.) — these attributes belong in your organisation's legal documentation and DPIA, not in `forgelm/config.py`. The `ComplianceMetadataConfig` schema (`compliance:` top-level block) accepts only: `provider_name`, `provider_contact`, `system_name`, `intended_purpose`, `known_limitations`, `system_version`, and `risk_classification`. A `compliance.data_protection` sub-block does not exist and will be rejected by Pydantic at startup (`extra="forbid"`).

## Common pitfalls

:::warn
**Treating PII masking as DPIA-replacement.** Masking is a technical mitigation, not a legal assessment. A DPIA is a documented analysis of risks, mitigations, and residual harm — required separately for high-risk processing.
:::

:::warn
**Skipping data audit on internal data.** Internal data is the most common source of inadvertent personal data exposure (employee records leaking into customer-support training). Audit everything.
:::

:::warn
**International transfers.** If your training data crosses jurisdiction boundaries (Turkish data trained in EU, EU data trained in US), additional safeguards apply. ForgeLM has no setting for transfers; record the safeguards in your DPIA.
:::

:::tip
For sectors like health and finance, consult a privacy specialist *before* the first training run. ForgeLM's defaults are sensible but they don't replace sector-specific legal review.
:::

## See also

- [PII Masking](#/data/pii-masking) — the technical implementation.
- [Compliance Overview](#/compliance/overview) — broader context.
- [Annex IV](#/compliance/annex-iv) — packaged compliance evidence.
