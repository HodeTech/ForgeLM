---
title: ML-NER PII (Presidio)
description: Optional Microsoft Presidio adapter that adds person / organization / location detection on top of the regex PII layer.
---

# ML-NER PII Detection — `--pii-ml`

The default [PII Masking](#/data/pii-masking) layer is regex-anchored and covers the *structured* identifiers GDPR Article 10 cares about (email, phone, IBAN, credit card, national IDs). Presidio NER adds the *unstructured* identifiers regex inherently misses: person names, organization names, geographic locations.

This is an **opt-in** layer. The default audit + ingest behaviour is unchanged when `--pii-ml` is not passed.

## When to use it

Reach for `--pii-ml` when:

- The corpus is free-form prose (interviews, customer letters, internal communication) where names aren't formatted as structured fields.
- Your compliance reviewer specifically asks about person/org/location coverage in addition to identifiers.
- You're auditing a multilingual corpus (Presidio supports localisation; see [Language Selection](#language-selection) below).

Don't reach for it when:

- The corpus is already heavily structured (CSV-shaped JSON, API logs); the regex layer's recall is already where you want it.
- You're running on a tight CPU budget — Presidio is materially slower than regex (NER forward pass per row).
- You're operating in an air-gapped environment without a pre-staged spaCy NER model — see [Air-Gap Operation](#/operations/air-gap).

## Two-step install

`presidio-analyzer` does **not** transitively ship a spaCy NER model. The install is two lines:

```shell
$ pip install 'forgelm[ingestion-pii-ml]'
$ python -m spacy download en_core_web_lg
```

Without the spaCy model, `forgelm audit --pii-ml` raises a typed `ImportError` **before any rows are scanned** — pre-flight checking is a deliberate design choice (see [Why pre-flight matters](#why-pre-flight-matters)).

## Quick example

```shell
$ forgelm audit data/customer-letters.jsonl --output ./audit/ --pii-ml
✓ format: instructions (8,400 rows)
⚠ PII: 12 emails, 3 phone, 1 IBAN (regex layer)
⚠ PII (ML): 47 person, 18 organization, 9 location (Presidio)
   worst tier: medium
```

The Presidio findings merge into the same `pii_summary` and `pii_severity` blocks under disjoint category names, so the regex baseline stays visible alongside the ML signal:

```json
{
  "pii_summary": {
    "email": 12,
    "phone": 3,
    "person": 47,
    "organization": 18,
    "location": 9
  },
  "pii_severity": {
    "by_tier": {"critical": 0, "high": 0, "medium": 59, "low": 30},
    "worst_tier": "medium"
  }
}
```

## Severity tiers

The new categories sit under a dedicated table in `forgelm.data_audit.PII_ML_SEVERITY`:

| Category | Tier | Reason |
|---|---|---|
| `person` | medium | A name attached to other context can re-identify; alone it's weaker than a national ID. |
| `organization` | low | Public-record entities; leakage is closer to "this person works at X" than "this is X's home address." |
| `location` | low | Same logic — geographic strings are usually de-identification-resistant on their own. |

These tiers sit **deliberately below** the regex `critical`/`high` floors (credit cards, national IDs). NER false-positive rates are materially higher than regex-anchored detection, so a "person" finding shouldn't gate a deployment the way a "credit_card" finding does.

## Language selection

Pass `--pii-ml-language` to audit non-English corpora. ForgeLM auto-builds a Presidio `AnalyzerEngine` for the requested language when you pass a code from the supported map (English defaults plus `de`, `es`, `fr`, `it`, `ja`, `ko`, `nl`, `pl`, `pt`, `ru`, `zh`); install the conventional spaCy model first:

```bash
python -m spacy download de_core_news_lg
forgelm audit data/german-corpus.jsonl --pii-ml --pii-ml-language de
```

For languages without a maintained spaCy model (Turkish, Arabic, etc.), use the multilingual fallback `xx`:

```bash
python -m spacy download xx_ent_wiki_sm
forgelm audit data/turkish-corpus.jsonl --pii-ml --pii-ml-language xx
```

The pre-flight (`_require_presidio(language=...)`) validates both the spaCy model availability and the language registration before any rows are scanned; misconfiguration aborts with an actionable error instead of silently returning zero findings.

## Why pre-flight matters

`forgelm.data_audit._require_presidio()` checks **both** the import sentinel (extra installed?) **and** the analyzer build (spaCy model present?) before any rows are scanned. Earlier prototypes only checked the import; that produced a particularly bad failure mode:

1. The audit launched, started scanning rows.
2. The first per-row Presidio call raised `OSError("Can't find model 'en_core_web_lg'")`.
3. `detect_pii_ml`'s per-row exception handler swallowed the error.
4. **Every** subsequent row also returned zero ML findings.
5. The audit finished green with no diagnostic — a critical compliance blind spot for an opt-in detector that operators expected to run.

The current pre-flight closes that gap by surfacing missing-model failures up front with the install recipe.

## Programmatic API

```python
from forgelm.data_audit import detect_pii_ml, _require_presidio

# Hard pre-flight; raises ImportError with the install recipe if missing.
_require_presidio()

text = "Alice Williams visits Acme Corp's Berlin office on Monday."
counts = detect_pii_ml(text, language="en")
print(counts)
# {'person': 1, 'organization': 1, 'location': 1}
```

The function returns an empty dict for non-string input, missing extras, or transient analyzer errors (a single bad row never blocks the audit). Hard failures (missing spaCy model, language not registered) are caught up front by `_require_presidio(language=...)` and raised before any rows are scanned — call it explicitly when you bypass `audit_dataset` and want the same pre-flight guarantees.

## What's NOT in this layer

- **DATE / TIME / NUMBER detection.** Presidio supports more entity types than the three ForgeLM maps; the others (DATE, NRP, CRYPTO, IP_ADDRESS, …) are not currently mapped because their privacy semantics are different. Open an issue if your compliance flow needs them.
- **PII *masking* via Presidio.** The current adapter is detection-only — for masking, the regex `--pii-mask` flag still owns ingest-side rewriting. Presidio's anonymizer module is a separate dependency and not wired up in v0.7.0.

## Common pitfalls

:::warn
**Treating ML-NER as a hard gate.** False-positive rates are materially higher than regex-anchored detection. Use Presidio findings as a *signal* to investigate, not as auto-revert criteria.
:::

:::warn
**Running `--pii-ml` on a non-English corpus without `--pii-ml-language`.** The default English NER returns near-zero findings on Turkish, German, Chinese text — and the audit honestly reports zero. Set the language explicitly.
:::

:::tip
**Air-gapped deployments:** Pre-stage the spaCy model on the target host (`python -m spacy download en_core_web_lg` from a mirror) and verify with `python -m spacy validate`. The pre-flight check passes once the model is on the import path.
:::

## See also

- [PII Masking](#/data/pii-masking) — the always-on regex layer.
- [Dataset Audit](#/data/audit) — where `--pii-ml` is invoked.
- [GDPR / KVKK](#/compliance/gdpr) — regulatory context.
- [Air-Gap Operation](#/operations/air-gap) — pre-staging the spaCy model.
