---
title: Model Card
description: Auto-generated transparency documentation — Article 13.
---

# Model Card

EU AI Act Article 13 requires transparency: deployed AI systems must be accompanied by clear documentation of capabilities, limitations, intended use, and training data. ForgeLM generates a HuggingFace-compatible `README.md` model card after every successful run.

## What ForgeLM auto-populates

| Section | Source |
|---|---|
| **Model details** | `model.name_or_path`, training paradigm, version. |
| **Intended use** | `compliance.intended_purpose`. |
| **Out-of-scope use** | `compliance.risk_assessment.foreseeable_misuse`. |
| **Training data** | Audit summaries from each `datasets:` entry. |
| **Training procedure** | YAML config snapshot. |
| **Evaluation** | `benchmark_results.json` summary. |
| **Safety** | `safety_report.json` summary. |
| **Limitations** | `compliance.risk_assessment.residual_risks`. |
| **Citation** | Cites teacher model if synthetic data was used. |
| **License** | From `compliance.license` field. |

The output is a Markdown file at `checkpoints/run/README.md` ready to upload to HuggingFace Hub.

## Sample output

```markdown
# Acme Customer Support v1.2.0

Customer-support assistant fine-tuned from Qwen2.5-7B-Instruct using ForgeLM 0.7.0.

## Model details

- **Base model:** Qwen/Qwen2.5-7B-Instruct
- **Fine-tuning paradigm:** SFT → DPO
- **Parameter-efficient method:** QLoRA (rank 16, alpha 32, DoRA enabled)
- **Trained:** 2026-04-29
- **Languages:** Turkish, English
- **License:** Apache 2.0

## Intended use

Multilingual customer-support assistant for telecom. Deployed within
authenticated user sessions to answer billing, plan, and technical
support questions.

## Out-of-scope use

This model is **not** intended for:
- Customer impersonation in social engineering attacks.
- Generation of fraudulent invoices.
- Use outside Turkish/English language pairs.
- Use without authentication or rate limiting.

## Training data

- 12,400 preference rows (`data/preferences.jsonl`)
  - Audit verdict: warnings (12 PII medium-severity flags, masked at ingest)
  - Cross-split overlap: 0
  - Language distribution: 99.2% TR, 0.5% EN

## Training procedure

Full configuration in `config_snapshot.yaml`. Highlights:

- Trainer: `dpo`
- Beta: 0.1
- Learning rate: 5e-6
- Epochs: 1
- Batch size: 2 (effective 32 with accumulation)

## Evaluation

| Task | Score | Floor | Verdict |
|---|---|---|---|
| hellaswag | 0.617 | 0.55 | pass |
| truthfulqa | 0.482 | 0.45 | pass |
| arc_easy | 0.74 | 0.70 | pass |

## Safety

Llama Guard 3 8B scoring across S1–S14:

- All blocked categories (S1, S2, S5, S10) within 0.05 of pre-train baseline.
- No category at high severity.

Full report in `artifacts/safety_report.json`.

## Limitations

- Adversarial jailbreaks against system prompt may occasionally succeed.
- Performance degrades on dialogue turns longer than 4096 tokens.
- Model was trained on Turkish-English bilingual data only — no support
  for other languages.

## Compliance

- EU AI Act: Annex IV technical documentation in `artifacts/annex_iv_metadata.json`
- GDPR: PII masked at ingest; no training data retains identifiable subjects
- Audit log: `artifacts/audit_log.jsonl`

For commercial use, see `LICENSE`.

## Citation

If you use this model, please cite:

```
@misc{acme2026,
  title  = {Acme Customer Support v1.2.0},
  author = {Acme Corp},
  year   = {2026},
  note   = {Fine-tuned with ForgeLM 0.7.0}
}
```
```

## Configuration

Model card generation is automatic after every successful run — there is no YAML knob to enable or disable it. There is no `output:` top-level config block in ForgeLM; adding one will be rejected by Pydantic validation (`extra="forbid"`). If you need to customise the card template, check the current schema in `forgelm/config.py` for the actual field name before documenting it.

## Manual additions

The default model card covers what ForgeLM can auto-determine. For manual additions (acknowledgements, custom warnings), append a `## Notes` section to the generated `README.md` after the run. The audit log treats this as a `model_card_amended` event.

## Common pitfalls

:::warn
**Stale model cards from previous runs.** Each run overwrites `README.md`. If you've manually edited the previous version, those edits are lost. For amendments that should persist across runs, add them to the `compliance.notes` YAML field instead.
:::

:::warn
**Forgetting `compliance.license`.** Without it, the auto-generated card shows "License: not specified", which fails most internal review processes. Set the license explicitly.
:::

:::tip
For HuggingFace Hub publication, ForgeLM's model card uses HuggingFace's standard front-matter format — `language:`, `license:`, `tags:` etc. — so it renders correctly on the Hub UI.
:::

## See also

- [Annex IV](#/compliance/annex-iv) — the Article 11 sibling.
- [Compliance Overview](#/compliance/overview) — context.
- [Configuration Reference](#/reference/configuration) — `compliance.*` fields used as model card inputs.
