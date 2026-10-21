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

KVKK (Türkiye, Law 6698) sets similar principles in its Article 4 and gives data subjects access and erasure rights in Articles 11 and 7; it has no separate accountability principle, and its security duties sit in Article 12.

## How ForgeLM addresses each

### Data minimisation (Art. 5(1)(c))

PII masking at ingest replaces the identifier formats ForgeLM detects with a placeholder:

```shell
$ forgelm ingest ./policies/ --recursive --strategy markdown --pii-mask --output data/policies.jsonl
```

The detectors cover email, phone, credit card, IBAN, and the Turkish, German, French and US national-ID formats; every match becomes `[REDACTED]`. **Names, addresses, dates of birth, health data and other free text are not detected**, and the patterns are tuned to miss a borderline match rather than redact ordinary prose. Masking reduces the personal data in the JSONL; it does not remove identifiable subjects from it. To measure how many names and locations (and, for some language models, organisations) remain, run `forgelm audit --pii-ml`: it adds Presidio NER **detection**, which reports per-category counts for each split (not which rows or which names) and masks nothing. It needs the `ingestion-pii-ml` extra and a spaCy model (for a language without a dedicated model, such as Turkish, `--pii-ml-language xx` uses `xx_ent_wiki_sm`). To find a known name, search for it with `forgelm reverse-pii`. See [PII Masking](#/data/pii-masking) and [ML-NER PII](#/data/pii-ml).

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

`forgelm purge --check-policy --config config.yaml --output-dir <training.output_dir>` lists the artefacts in that directory that are past their horizon and always exits 0 (without `--output-dir` it scans the current directory, not `training.output_dir`). Nothing is deleted automatically and nothing is written to the audit log for it; deleting what is overdue is your storage layer's job. (`retention.enforce` is reserved: no command acts on it yet.)

## Data subject requests

Most-common request types and how ForgeLM helps:

### Right of access (Art. 15)

"What personal data do you hold about me?"

Search your training data for the person's identifier:

```shell
$ forgelm reverse-pii --query "ali@example.com" --output-dir ./outputs data/*.jsonl
No matches for identifier hash 3f2a9c1e5b7d4a60… (literal, plaintext mode) in 3 file(s).
```

The scan reports the file and line where the identifier appears (the snippets are raw corpus text, so do not redirect them to a persistent log); zero matches, as above, is a valid answer. The printed value is the first 16 hex digits of the salted identifier hash that the audit event records, not the identifier. `--output-dir` is where ForgeLM keeps the per-directory salt (`.forgelm_audit_salt`) and appends the `data.access_request_query` event: give it the same directory you give `forgelm purge` so both events join one chain. Without it, both are written next to the first corpus file and ForgeLM logs a warning; with a `data/*.jsonl` glob that puts `audit_log.jsonl` among your corpus files, so always pass `--output-dir`. It finds what is literally in the files you point it at: the match is case-sensitive and compares raw file text, so search spelling and case variants as well (`--type custom --query '(?i)…'`), and note that a corpus written with ASCII escapes such as `\u00e7` hides non-ASCII names from a literal query. Masking only removes the identifier formats it detects, so a name or an undetected variant can remain, and the raw source documents are not covered by a scan of the masked JSONL: search those separately. ForgeLM cannot tell you what a trained model remembers.

### Right to erasure (Art. 17)

"Delete my data."

If a person's data was in your training set:
1. Find the rows with `forgelm reverse-pii`, then remove them with `forgelm purge --row-id <id> --corpus <file> --output-dir ./outputs` (the same directory as `reverse-pii`; use your training output directory so purge can also warn about a trained `final_model/`); every erasure is written to the audit log. `--row-id` is matched against the row's `id` or `row_id` field and never against a line number, and `forgelm ingest` does not write either field, so give every row a stable id **before you train** (see below). A corpus without ids cannot be purged row by row.
2. Source documents may still hold it — drop them from your raw store and re-ingest if necessary.
3. The model may have memorised some details — see "model-level erasure" below.

To make rows addressable, give each JSONL row a unique `id` once, when the corpus is created, and keep it when the file is later filtered or re-ordered. Rows with a missing, null or empty `id` get a new one; a row whose `id` (or `row_id`, which `purge` also matches) another row already uses stops the script, because `forgelm purge --row-id` would see both rows as one match (it refuses by default, and with `--row-matches all` it would erase every match) and you have to decide which row keeps the id. The output is written to a temporary file and moved into place only after every row passed, so a failed run leaves an existing output file untouched:

```python
import json, os, uuid

SRC, DST = "data/train.jsonl", "data/train.with-ids.jsonl"
tmp, seen = DST + ".tmp", set()
try:
    with open(SRC, encoding="utf-8") as src, open(tmp, "w", encoding="utf-8") as dst:
        for line_no, line in enumerate(src, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("id") in (None, ""):
                row["id"] = str(uuid.uuid4())
            ids = {str(row[key]) for key in ("id", "row_id") if row.get(key) not in (None, "")}
            if ids & seen:
                raise SystemExit(f"line {line_no}: id {sorted(ids & seen)[0]!r} is already used by another row")
            seen |= ids
            dst.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(tmp, DST)  # reached only when every row passed
finally:
    if os.path.exists(tmp):
        os.remove(tmp)
```

Train from the file with ids. `forgelm reverse-pii` reports the file and line where the identifier occurs; read that row's `id` from the file and pass it to `forgelm purge --row-id`. Re-assigning ids after training breaks the link between the audit log and the corpus, so assign once.

### Model-level erasure

LLMs can memorise rare strings from training. Even with PII masking, removing all traces from a deployed model is hard, and under EDPB Opinion 28/2024 a model trained on personal data cannot always be considered anonymous: if you cannot show that yours is, an access or erasure request may reach the model itself, not only the corpus. ForgeLM's defences:

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
