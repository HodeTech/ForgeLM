---
title: Dataset Audit
description: Catch PII, secrets, leakage, near-duplicates, and quality issues before they ruin your training run.
---

# Dataset Audit

`forgelm audit` is a CPU-only, streaming pre-flight check for your training data. It catches the bugs that make models fail safety review, leak secrets in production, or memorise the test set. Run it before every training job.

```mermaid
flowchart LR
    A[JSONL data] --> B[forgelm audit]
    B --> C[PII scan]
    B --> D[Secrets scan]
    B --> E[Near-dup scan]
    B --> F[Quality filter]
    B --> G[Language detect]
    B --> H[Cross-split leak check]
    C & D & E & F & G & H --> I[data_audit_report.json]
    classDef io fill:#1c2030,stroke:#0ea5e9,color:#e6e7ec
    classDef check fill:#161a24,stroke:#f97316,color:#e6e7ec
    class A,B,I io
    class C,D,E,F,G,H check
```

## Quick example

```shell
$ forgelm audit data/preferences.jsonl --output ./audit/
✓ format: preference (12,400 rows, 3 splits)
⚠ PII: 12 emails, 3 phone, 1 IBAN (medium severity, see report)
✓ secrets: 0 detected
⚠ near-duplicate pairs: 47 (LSH-banded simhash, threshold 3)
✗ chosen-rejected identical: 12 rows (likely collection bug)
✓ language: 99.2% Turkish, 0.8% English
✓ no cross-split leakage

audit complete — see ./audit/data_audit_report.json
```

Exit codes from `forgelm audit`:

| Exit | Meaning |
|---|---|
| `0` | Audit completed (regardless of findings — read the report). |
| `1` | IO error (the input path does not exist or cannot be read). |
| `2` | Import error (a required optional extra is missing). |

`forgelm audit` does not exit non-zero based on the *severity* of what it finds. To gate CI on specific findings, pipe the JSON report through `jq` (see [What's in the report](#whats-in-the-report) below).

## What audit checks

### PII (personally identifiable information)

Detects emails, phone numbers, credit card numbers (Luhn-validated), IBAN, and national IDs (TR, DE, FR, US-SSN). Tags rows by severity. See [PII Masking](#/data/pii-masking).

### Secrets

Detects AWS keys, GitHub Personal Access Tokens, Slack tokens, OpenAI keys, Google API keys, JWTs, full PEM private-key blocks, Azure storage strings. See [Secrets Scrubbing](#/data/secrets).

### Near-duplicate detection

Two algorithms:
- **LSH-banded simhash** (default) — exact recall, fast, good for <50K rows.
- **MinHash LSH** — approximate, scales to millions of rows.

See [Deduplication](#/data/deduplication) for the trade-offs.

### Quality filter

Heuristics borrowed from Gopher, C4, RefinedWeb research. Flags rows with low alpha ratio, abnormal word lengths, repeated lines, or short paragraphs. Conservative — never silently drops rows. See [Quality Filter](#/data/quality-filter).

### Language detection

Uses `langdetect` to identify the dominant language per row. Reports top-3 across the dataset. Catches the "supposed to be Turkish but 12% slipped in as English" class of bugs. See [Language Detection](#/data/language-detection).

### Cross-split leakage

Compares train vs validation vs test rows for exact and near-duplicate matches. The single most expensive evaluation bug — leakage means your reported metrics are inflated. Audit refuses to certify a leaky split. See [Cross-Split Leakage](#/data/leakage).

### Format-specific checks

For **preference** datasets, audit also flags:
- Rows where `chosen == rejected` (collection bug)
- Rows where `chosen` is shorter than `rejected` by more than 10× (likely accidental swap)
- Empty `chosen` or `rejected`

For **binary** (KTO) datasets:
- Severe class imbalance (>99/1)
- Empty responses
- Non-boolean labels

## CLI flags

Authoritative source: `forgelm/cli/_parser.py::_add_audit_subcommand`.

| Flag | Description |
|---|---|
| `input_path` (positional) | JSONL file or directory of split JSONL files (`train.jsonl`, `validation.jsonl`, `test.jsonl`). |
| `--output DIR` | Output directory for `data_audit_report.json` (default `./audit/`). |
| `--verbose` | Show every split in the text summary, including zero-finding splits. JSON output is unaffected. |
| `--near-dup-threshold N` | Hamming-distance cutoff for simhash near-duplicate detection (default 3 ≈ 95% similarity). Ignored under `--dedup-method=minhash`. |
| `--dedup-method {simhash,minhash}` | Near-duplicate algorithm. `simhash` is the default (Phase 11.5 path); `minhash` opts into LSH-banded MinHash via the optional `forgelm[ingestion-scale]` extra (datasketch). |
| `--jaccard-threshold X` | Jaccard threshold for `--dedup-method=minhash` (default 0.85). Ignored when `--dedup-method=simhash`. |
| `--quality-filter` / `--no-quality-filter` | Heuristic quality checks (mean word length, alphabetic-character ratio, end-of-line punctuation ratio, repeated-line ratio, short-paragraph ratio). **Default ON from v0.6.0** (Phase 15 Task 5); pre-v0.6.0 the flag was opt-in. Pass `--no-quality-filter` to skip. Populates `quality_summary` in the report. See [Quality Filter](#/data/quality-filter). |
| `--croissant` | Emit a [Google Croissant 1.0](https://mlcommons.org/croissant/) dataset card under the report's `croissant` key. See [Croissant 1.0 Dataset Card](#/data/croissant-card). |
| `--pii-ml` | Layer Presidio's ML-NER PII detection on top of the regex detector. Requires the optional `forgelm[ingestion-pii-ml]` extra **and** a spaCy NER model (e.g. `python -m spacy download en_core_web_lg`). See [ML-NER PII (Presidio)](#/data/pii-ml). |
| `--pii-ml-language LANG` | spaCy NLP language code for `--pii-ml` (default `en`). Set to e.g. `tr` on a Turkish corpus AND make sure the matching spaCy model is installed. |
| `--workers N` | Worker processes for the split-level pipeline (default 1, sequential). Set to 2-4 on multi-split corpora for near-linear speedup. The audit JSON is byte-identical across worker counts (determinism contract). |
| `--output-format {text,json}` | Stdout renderer. The `json` mode prints a machine-readable summary; `text` is the default human-readable form. |

> **Removed flags (never shipped).** Earlier drafts of this page documented `--strict`, `--dedup-algo`, `--dedup-threshold`, `--skip-pii`, `--skip-secrets`, `--skip-quality`, `--skip-leakage`, `--sample-rate`, `--remove-duplicates`, `--remove-cross-split-overlap`, `--output-clean`, `--show-leakage`, `--minhash-jaccard`, `--minhash-num-perm`, and `--add-row-ids`. None of these exist in the parser. Use the canonical names above; if you need an audit-as-gate behaviour ("warnings → exit non-zero"), wrap the `--output-format json` envelope with your own `jq`-based gate in CI.

## What's in the report

`data_audit_report.json` is structured for both human reading and CI integration:

```json
{
  "format": "preference",
  "row_count": 12400,
  "splits": {"train": 10000, "val": 1200, "test": 1200},
  "pii_summary": {
    "email": 12,
    "phone": 3,
    "iban": 1,
    "severity": "medium"
  },
  "secrets_summary": {"total": 0},
  "near_duplicate_pairs": 47,
  "cross_split_overlap": 0,
  "quality_summary": {
    "samples_flagged": 36,
    "samples_evaluated": 12400,
    "by_check": {
      "low_alpha_ratio": 4,
      "low_punct_endings": 8,
      "abnormal_mean_word_length": 12,
      "short_paragraphs": 5,
      "repeated_lines": 7
    },
    "overall_quality_score": 0.997
  },
  "language_distribution": {"tr": 0.992, "en": 0.008}
}
```

CI integrations parse individual counts to gate merges. There is no `--strict` flag (see "Removed flags" above) and no `verdict` field in the report — wrap the JSON envelope with `jq`:

```yaml
# .github/workflows/data.yml
- name: Audit data
  run: |
    forgelm audit data/train.jsonl --output-format json > audit.json
    jq -e '.cross_split_overlap == 0 and .pii_summary.severity != "high"' audit.json
```

## Common pitfalls

:::warn
**Skipping audit on "trusted" data.** Even data from your own production logs can have surprises — a recent rotation of API keys leaks into telemetry, a GDPR-deletion request creates dangling IDs. Audit defends against your own future mistakes.
:::

:::warn
**Trying to use `--sample-rate`.** This flag never shipped (see "Removed flags" above). The audit always runs over the entire corpus; for million-row corpora, parallelise with `--workers N` instead. For <10K rows, the full audit takes seconds — no sampling needed.
:::

:::tip
**Save audit reports per-version.** Commit `data_audit_report.json` to git alongside your dataset version. Future audits can diff against the historical report and tell you "we had 12 PII flags last time, now we have 47 — what changed in the data pipeline?"
:::

## See also

- [PII Masking](#/data/pii-masking), [Secrets Scrubbing](#/data/secrets), [Deduplication](#/data/deduplication) — individual checks in detail.
- [Annex IV](#/compliance/annex-iv) — how audit reports flow into compliance artifacts.
- [Configuration Reference](#/reference/configuration) — `compliance.data_audit_artifact` field.
