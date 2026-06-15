---
title: Quality Filter
description: Heuristic filters from Gopher, C4, and RefinedWeb for catching low-quality training rows.
---

# Quality Filter

Not all rows in your training data are equally useful. Boilerplate, OCR errors, repeated lines, and pure-symbol noise dilute the signal. ForgeLM's quality filter applies heuristics drawn from the Gopher, C4, and RefinedWeb research lineages — conservatively, so it never silently drops rows.

## What gets flagged

| Heuristic | What it catches |
|---|---|
| **Low alpha ratio** | `<55%` alphabetic characters — usually code dumps, log spam, or pure symbols. |
| **Abnormal mean word length** | Words averaging `<3` or `>10` characters — often OCR garbage or URL-only rows. |
| **Repeated line ratio** | Rows where `>30%` of lines are duplicated — boilerplate or extraction artifacts. |
| **Short content** | Total length below a configurable minimum — often empty after extraction. |
| **Bullet-only rows** | Rows where `>90%` of lines start with bullet markers — usually extracted nav menus. |
| **Symbol density** | Excessive `_-=#*` density — usually rendered tables or pre-formatted text. |

Each row gets a `quality_flags` list in the audit report. The filter never automatically drops; it's your call.

## Quick example

```shell
$ forgelm audit data/ingested.jsonl
⚠ quality flags:
   short_response: 24
   repeated_lines: 12
   abnormal_word_length: 6
   bullet_only: 3
```

Audit *flags* low-quality rows but does not delete them. `forgelm audit` only reports; it never drops rows or writes a cleaned JSONL. To remove flagged rows, pipe the audit JSON through `jq` as a downstream manual step and re-run `forgelm audit` to verify the result.

> **Note:** There is no `audit:` top-level block in the YAML config (`ForgeConfig` rejects unknown keys). The `drop_flagged` and `write_clean_output` fields shown in earlier drafts do not exist; auto-drop-and-write-clean is not implemented. Use `--no-quality-filter` to skip the quality checks entirely.

```shell
# v0.6.0+: quality-filter is DEFAULT-ON; the explicit flag is harmless.
$ forgelm audit data/ingested.jsonl
✓ wrote audit/data_audit_report.json (quality_summary: 45 / 12,400 flagged)

# Pre-v0.6.0 (or to be explicit), pass the flag:
$ forgelm audit data/ingested.jsonl --quality-filter

# Opt out of the new default if your CI gates depend on opt-in semantics:
$ forgelm audit data/ingested.jsonl --no-quality-filter
```

## Tuning thresholds

Quality-filter threshold configuration is not exposed as YAML fields in the current release — thresholds are fixed at the heuristic defaults listed below. The CLI flags `--quality-filter` / `--no-quality-filter` control whether the filter runs; there is no per-threshold override flag.

| Heuristic | Default |
|---|---|
| `min_alpha_ratio` | 0.55 |
| `min_mean_word_length` | 3 |
| `max_mean_word_length` | 10 |
| `max_repeated_line_ratio` | 0.30 |
| `min_content_length` | 50 characters |
| `max_bullet_ratio` | 0.90 |

For corpora that legitimately violate one of these (e.g. code-heavy datasets violate alpha ratio), use `--no-quality-filter` to skip the filter entirely for that run.

## Conservative-by-default

The thresholds are tuned to *flag, not drop*. The reasons:

1. Domain mismatch — a quality filter tuned on web crawls misjudges medical or legal text.
2. Silent dropping is invisible to the user. Better to surface flags and let the human decide.
3. Audit reports are compared across dataset versions; a sudden change in flag counts is informative.

If you want stricter filtering — for instance, on a public web crawl going into pre-training — pair the filter with a manual review of edge cases.

## Programmatic API

```python
from forgelm.data_audit import score_quality

text = "= = = = = = = =\n* * *\n[no content]"
flags = score_quality(text)
print(flags)
# {'low_alpha_ratio': True, 'symbol_density': True, 'short_content': True}
```

## Common pitfalls

:::warn
**Dropping rows without review.** When using `jq` to filter flagged rows from the audit report, pipe carefully — removals are silent. Always run `forgelm audit` on the result to confirm the cleaned dataset passes.
:::

:::warn
**Filtering code datasets with default thresholds.** Code has more symbols and shorter mean word length than prose. Either disable the affected checks or use code-specific thresholds.
:::

## See also

- [Dataset Audit](#/data/audit) — runs the quality filter as part of standard audit.
- [Document Ingestion](#/data/ingestion) — most quality issues originate at extraction time.
