---
title: Language Detection
description: Per-row language identification — catch the "supposed to be Turkish but 12% slipped in as English" bug.
---

# Language Detection

Real-world datasets are often nominally one language but accidentally contain others — copy-pasted English documentation in a Turkish corpus, French legal boilerplate in a Spanish dataset. ForgeLM detects per-row language and reports the distribution at audit time.

## Quick example

```shell
$ forgelm audit data/medical-tr.jsonl
✓ language: 99.2% tr, 0.5% en, 0.3% other
   12 rows in 'en' (likely accidental)
   5 rows in 'mixed' (containing both languages)
```

The audit report breaks down per-row language and lists indices of outliers, so you can inspect:

```shell
$ jq '.language_outliers[:3]' audit/data_audit_report.json
[
  {"row": 1240, "detected": "en", "expected": "tr", "snippet": "Lorem ipsum dolor..."},
  {"row": 4521, "detected": "en", "expected": "tr", "snippet": "Patient should call..."},
  {"row": 9012, "detected": "mixed", "expected": "tr", "snippet": "Hasta günde 3 kez two..."}
]
```

## Detector

Uses `langdetect` (a pure-Python port of Google's CLD2). Supports 55+ languages out of the box. Performance: ~1ms per row, no GPU.

For very short rows (<50 characters), language detection becomes unreliable — ForgeLM marks those as `unknown` rather than guessing.

## Configuration

> **Note:** There is no `audit:` top-level block in the YAML config (`ForgeConfig` rejects unknown keys). Language detection is automatic and always-on in `forgelm audit`. The `--pii-ml-language` flag is available only for the Presidio ML-NER layer, not for the `langdetect`-based language distribution check itself.

If your dataset is intentionally multilingual, audit reports the distribution without needing any configuration — see the tip in [Common pitfalls](#common-pitfalls) below.

## Per-language Distribution Report

```json
{
  "language_distribution": {
    "tr": 0.992,
    "en": 0.005,
    "ar": 0.001,
    "unknown": 0.002
  },
  "language_outliers": 17,
  "expected": "tr"
}
```

## Common pitfalls

:::warn
**Detecting language on extracted PDF text.** PDF extraction sometimes preserves runs of English boilerplate ("Confidential", "Page 1 of N", "© 2026 Company") that throw off detection on otherwise-Turkish content. Pre-filter these in ingest.
:::

:::warn
**Setting `min_chars` too low.** Short rows produce noisy detection. Stay above 50; 100 is even safer for quality reports.
:::

:::tip
**Multi-language datasets.** If your dataset is intentionally multilingual (translation pairs, multilingual chat), don't set `expected` — let audit just report the distribution and flag rows that are mixed within a single document.
:::

## See also

- [Dataset Audit](#/data/audit) — runs language detection as part of standard audit.
- [Document Ingestion](#/data/ingestion) — chunks raw text; `forgelm audit --pii-ml-language LANG` is the language hint for the ML-NER PII layer (Phase 12.5).
