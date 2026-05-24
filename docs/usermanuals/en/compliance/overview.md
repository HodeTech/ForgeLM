---
title: Compliance Overview
description: How ForgeLM maps EU AI Act, GDPR, and ISO 27001 requirements onto its training pipeline.
---

# Compliance Overview

ForgeLM was built for teams that have to defend their training pipeline to a regulator, not just a CTO. Every successful (or failed) run produces a structured evidence bundle that maps cleanly onto EU AI Act Articles 9-17, GDPR Article 5, and ISO 27001 control objectives.

```mermaid
flowchart TD
    Run[Training run] --> Audit[Data audit]
    Run --> Train[Train + checkpoints]
    Run --> Eval[Eval + safety]
    Run --> Doc[Auto model card]

    Audit --> A10[Article 10<br/>Data governance]
    Train --> A12[Article 12<br/>Audit log]
    Eval --> A9[Article 9<br/>Risk management]
    Eval --> A15[Article 15<br/>Accuracy/robustness]
    Doc --> A13[Article 13<br/>Transparency]
    Audit --> A11[Article 11<br/>Annex IV bundle]
    Train --> A11
    Eval --> A11

    classDef artifact fill:#1c2030,stroke:#0ea5e9,color:#e6e7ec
    classDef article fill:#161a24,stroke:#f97316,color:#e6e7ec
    classDef start fill:#1c2030,stroke:#22c55e,color:#e6e7ec
    class Run start
    class Audit,Train,Eval,Doc artifact
    class A9,A10,A11,A12,A13,A15 article
```

## What gets produced

Any run with a populated `compliance:` block (specifically `compliance.risk_classification` plus `compliance.provider_name` / `compliance.intended_purpose`) emits the Article 11 technical documentation alongside the standard top-level audit log:

```text
checkpoints/run/
├── audit_log.jsonl                ← Article 12 — append-only event log (top-level)
├── audit_log.jsonl.manifest.json  ← genesis-pin sidecar (truncate-evidence)
└── compliance/
    ├── annex_iv_metadata.json     ← Article 11 — technical documentation (paired with `forgelm verify-annex-iv`)
    ├── data_audit_report.json     ← Article 10 — data governance evidence
    ├── safety_report.json         ← Article 9 + 15 — risk + safety assessment (when `evaluation.safety.enabled`)
    ├── benchmark_results.json     ← Article 15 — accuracy (when `evaluation.benchmark.enabled`)
    └── README.md                  ← Article 13 — HuggingFace-compatible model card
```

There is **no** `compliance.annex_iv: true` knob — Annex IV emission is driven by the presence of `compliance.risk_classification` and a populated `compliance.provider_*` / `compliance.intended_purpose`. Likewise, ForgeLM does **not** generate a `conformity_declaration.md` — Article 16 conformity is the deployer's signed deliverable, not a code artefact. (`forgelm verify-annex-iv <path>/annex_iv_metadata.json` cross-checks the Annex IV bundle against the audit log, but it does not author a conformity declaration.)

## Articles ForgeLM addresses

| Article | Topic | How ForgeLM addresses it |
|---|---|---|
| **9** | Risk management | Auto-revert + threshold gates + trend tracking. |
| **10** | Data governance | `forgelm audit` produces governance evidence per dataset. |
| **11** | Technical documentation | `annex_iv_metadata.json` is a populated Annex IV. |
| **12** | Record-keeping | Append-only `audit_log.jsonl` covering training start, eval gates, revert decisions. |
| **13** | Transparency | Auto-generated model card listing capabilities, limitations, training summary. |
| **14** | Human oversight | Optional `evaluation.require_human_approval: true` blocks promotion until a human signs off. |
| **15** | Accuracy & robustness | Benchmark gates + safety eval + cybersecurity (PII / secrets at ingest). |
| **16-17** | Conformity & QMS | Declaration scaffold + QMS SOPs shipped alongside the toolkit. |

For the full mapping with code references, see the [Compliance summary on GitHub](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/compliance_summary.md).

## What ForgeLM doesn't claim

:::warn
ForgeLM **generates** Annex IV-style technical documentation. It does **not** certify your system as a high-risk AI system under the AI Act — that's a notified-body or self-assessment activity, outside any toolkit's scope.

The audit log is append-only by convention and SHA-256-anchored. Real tamper-evidence requires shipping the log to a separate write-once store (S3 Object Lock, ledger DB). The toolkit produces the artefact; chain-of-custody is your operational responsibility.

The PII/secrets regex sets are conservative by design — they prefer false-negatives over false-positives. For high-stakes corpora, pair with manual review before training.
:::

## Enabling compliance artifacts

Set in your YAML:

```yaml
compliance:
  provider_name: "Acme Corp"
  provider_contact: "compliance@acme.example"
  system_name: "TR Telecom Support Assistant"
  intended_purpose: "Customer-support assistant for Turkish telecom"
  known_limitations: "Not for medical, legal, or financial advice."
  system_version: "v1.0.0"
  risk_classification: "high-risk"    # one of: unknown | minimal-risk | limited-risk | high-risk | unacceptable

evaluation:
  require_human_approval: true        # optional Article 14 gate (NOT compliance.human_approval)
```

There is no `compliance.annex_iv`, `compliance.data_audit_artifact`, `compliance.human_approval`, `compliance.deployment_geographies`, or `compliance.responsible_party` field — those are phantom keys earlier drafts of this page invented. The canonical schema is `ComplianceMetadataConfig` in `forgelm/config.py`. To pin data-audit evidence, run `forgelm audit <corpus>` against the same `--output-dir` as the trainer; ForgeLM picks up the `data_audit_report.json` automatically.

Every field from `compliance:` flows into `annex_iv_metadata.json`. Required fields are validated at config load — a missing `intended_purpose` fails `--dry-run`.

## What goes into Annex IV

The Annex IV artifact has eight sections, all populated automatically:

1. **General description** — model name, intended purpose, deployment geography.
2. **Detailed system description** — base model, training paradigm, dataset summary.
3. **Monitoring** — eval thresholds, auto-revert triggers, trend tracking.
4. **Risk management** — risk classification, mitigations, residual risks.
5. **Lifecycle** — training date, version, references to source data.
6. **Standards** — listed compliance frameworks (EU AI Act, GDPR, ISO 27001).
7. **Declaration of conformity** — scaffold; final declaration requires human signature.
8. **Post-market monitoring plan** — pointer to the deployed surveillance config.

See [Annex IV](#/compliance/annex-iv) for the full schema.

## Operational responsibilities (you, not ForgeLM)

The toolkit produces evidence; the people produce certification. Your team is responsible for:

- Reviewing the audit bundle after every run that's bound for production.
- Shipping the audit log to a write-once store for tamper-evidence.
- Conducting the conformity assessment with a notified body where required.
- Maintaining post-market monitoring once the model is deployed.
- Handling data subject requests (GDPR Articles 15-22).

ForgeLM's QMS SOPs in `docs/qms/` cover the operational side — release process, incident response, data-source onboarding.

## See also

- [Annex IV](#/compliance/annex-iv) — full Article 11 artifact spec.
- [Audit Log](#/compliance/audit-log) — Article 12 event log.
- [Human Oversight](#/compliance/human-oversight) — Article 14 gate.
- [Model Card](#/compliance/model-card) — Article 13 transparency.
- [GDPR / KVKK](#/compliance/gdpr) — data protection.
