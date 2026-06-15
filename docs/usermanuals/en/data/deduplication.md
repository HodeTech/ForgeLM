---
title: Near-Duplicate Detection
description: LSH-banded simhash and MinHash LSH for catching near-duplicates in training data.
---

# Near-Duplicate Detection

Duplicates and near-duplicates inflate your training distribution towards whatever's repeated, and — when they straddle train/eval splits — make your evaluation metrics meaningless. ForgeLM ships two algorithms: simhash for accuracy and small-to-medium corpora, MinHash LSH for scale.

## Algorithm choice

| Algorithm | Recall | Speed | Best for |
|---|---|---|---|
| **LSH-banded simhash** (default) | Exact within Hamming threshold | ~50K rows/sec | Corpora < 50K rows |
| **MinHash LSH** | Approximate (>95% of true duplicates) | ~500K rows/sec | Corpora > 50K rows |

Override the default via `--dedup-method {simhash,minhash}`.

## Quick example

```shell
$ forgelm audit data/train.jsonl --near-dup-threshold 3
⚠ near-duplicate pairs: 47 (LSH-banded simhash, threshold 3)

$ jq '.near_duplicates[]' audit/data_audit_report.json | head
{"row_a": 1240, "row_b": 4521, "hamming": 1, "similarity": 0.984}
{"row_a": 9012, "row_b": 9013, "hamming": 0, "similarity": 1.0}
```

A `hamming: 0` means *exact* duplicates (same simhash); higher values are progressively less similar.

## Threshold tuning

The `--near-dup-threshold` is a Hamming distance on the 64-bit simhash. Defaults are:

| Threshold | Captures | False-positive rate |
|---|---|---|
| 0 | Exact duplicates only | ~0% |
| 1-2 | Trivial-edit duplicates ("Hello!" vs "Hello.") | <1% |
| **3** (default) | Paraphrases with shared structure | 1-2% |
| 5+ | Loose paraphrases; high false-positive rate | 5-15% |

Most teams stick with 3.

## What near-dup catches that exact-match doesn't

```text
Row A: "Welcome to our customer support. How can I help you today?"
Row B: "Welcome to our customer support — how can I help you today?"
```

Exact-match misses these (different punctuation). Simhash with threshold 3 catches them.

```text
Row A: "Send your CV to ali@example.com"
Row B: "Send your CV to ali@example.com or call us"
```

Threshold 3 also catches these (same first half, slight extension).

## Cross-split awareness

Audit runs near-dup detection both *within* and *across* splits. Cross-split duplicates are the high-priority bug — they make your benchmark scores unreliable. Audit's `cross_split_overlap` field reports how many train rows have a near-duplicate in validation or test. See [Cross-Split Leakage](#/data/leakage).

## MinHash LSH for scale

For corpora over 50K rows, switch to MinHash:

```shell
$ forgelm audit data/large.jsonl --dedup-method minhash --jaccard-threshold 0.85
✓ near-duplicate pairs: 1,247 (MinHash LSH, threshold 0.85)
```

MinHash trades small accuracy for big speed — typical recall is >95% of true duplicates while running 10× faster than simhash on million-row datasets.

| MinHash flag | Description |
|---|---|
| `--dedup-method minhash` | Switch from the default simhash detector to MinHash LSH. Requires the `forgelm[ingestion-scale]` extra (datasketch). |
| `--jaccard-threshold` | Jaccard similarity threshold (default 0.85). Ignored under simhash. |

Permutation count and LSH banding are not user-tunable today — they are fixed at the library defaults that benchmark cleanly across the 50K-to-1M-row range. Track [Phase 13 roadmap on GitHub](https://github.com/HodeTech/ForgeLM/blob/main/docs/roadmap.md) for the planned `forgelm[ingestion-scale]` knobs to expose them.

## Streaming behaviour

Both algorithms are streaming — they don't load the whole dataset into memory. A 10M-row corpus dedupes in a few minutes on a laptop CPU.

## Removing duplicates

`forgelm audit` *detects* duplicates; it does not remove them. There is no `audit:` top-level block in the YAML config (`ForgeConfig` rejects unknown keys), and the `write_clean_output` / `keep_split` fields shown in earlier drafts do not exist. Auto-removal is not implemented.

To drop duplicates, use the pair-level report that audit writes to disk:

```shell
# Get the indices of duplicate rows and filter them out with jq
$ jq '[.near_duplicate_summary.pairs[].row_b] | unique' audit/data_audit_report.json > dup_indices.json
$ jq --slurpfile dups dup_indices.json \
     '[to_entries[] | select(.key as $i | $dups[0] | index($i) | not) | .value]' \
     data/train.jsonl > data/train.dedup.jsonl
$ forgelm audit data/train.dedup.jsonl   # verify
```

The CLI flags `--dedup-method`, `--near-dup-threshold`, and `--jaccard-threshold` control *detection* sensitivity; removal is always a downstream manual step.

## Common pitfalls

:::warn
**Threshold too aggressive.** Hamming threshold 5+ on simhash will flag legitimately different examples as duplicates. Stick with 3 unless you've measured false-positive rate on your specific data.
:::

:::warn
**MinHash recall depends on the permutation count.** ForgeLM ships datasketch defaults (≥128 permutations) that keep recall above 95%. Manual override is on the [Phase 13 roadmap on GitHub](https://github.com/HodeTech/ForgeLM/blob/main/docs/roadmap.md) — until then, do not rely on a `--num-perm` flag (it does not exist).
:::

:::tip
**Run dedup BEFORE manually splitting train/val/test.** If your splits are produced upstream and have leakage, you can't fix that with deduplication; you have to re-split. Audit on the combined dataset before splitting catches this.
:::

## See also

- [Dataset Audit](#/data/audit) — runs dedup as part of the standard audit.
- [Cross-Split Leakage](#/data/leakage) — the highest-priority deduplication concern.
- [Quality Filter](#/data/quality-filter) — sister feature for catching low-quality rows.
