# Dataset Audit Guide

`forgelm audit PATH` analyzes a JSONL dataset and produces a
`data_audit_report.json` covering quality, governance, PII, and (Phase 12
onwards) credential-leakage and heuristic-quality signals. Phase 11
shipped the underlying audit; Phase 11.5 (consolidated into `v0.5.0`) promoted
it from a top-level flag to a first-class subcommand and added
LSH-banded near-duplicate detection, streaming JSONL reading, PII
severity tiers, atomic on-disk write, and a verbose-by-default
truncation policy. **Phase 12 (consolidated into `v0.5.0`)** added an opt-in MinHash LSH
dedup method (`--dedup-method minhash`), a code/credential leakage scan
that runs always-on (`secrets_summary`), and a heuristic quality filter
(`--quality-filter`) — initially opt-in, **flipped to default-ON in v0.6.0
per Phase 15 Task 5**; operators wanting the pre-v0.6.0 opt-in semantics
pass the new `--no-quality-filter` companion.

The report feeds the EU AI Act Article 10 data governance artifact
automatically when present in the trainer's `output_dir`.

---

## Run it

```bash
# Single split (treated as 'train')
forgelm audit data/sft.jsonl --output ./audit/

# Multi-split: directory containing train.jsonl / validation.jsonl / test.jsonl
forgelm audit data/ --output ./audit/

# Show every split (including those with no findings)
forgelm audit data/ --verbose

# Tighter / wider simhash near-duplicate threshold
forgelm audit data/ --near-dup-threshold 5

# Phase 12: MinHash LSH dedup for >50K-row corpora (needs `[ingestion-scale]` extra)
pip install 'forgelm[ingestion-scale]'
forgelm audit data/large_corpus.jsonl --dedup-method minhash --jaccard-threshold 0.85

# Phase 12 + Phase 15: heuristic quality filter (Gopher/C4 style).
# DEFAULT-ON from v0.6.0; explicit flag is redundant but harmless.
# Pass --no-quality-filter to restore the pre-v0.6.0 opt-in semantics.
forgelm audit data/ --quality-filter

# Phase 17: split-level parallelism for multi-split corpora
forgelm audit data/ --workers 4
```

`--output` defaults to `./audit/`. The directory is created if missing;
the **full** `data_audit_report.json` is always written there. Stdout shows
a human-readable summary by default; pass `--output-format json` to get
a **summary** JSON envelope (top-level metrics + report path + notes) on
stdout — the full report still lives on disk under `--output`. CI/CD
consumers should slurp the file from `report_path` rather than parsing
the stdout summary when they need every detail.

No GPU required. No network calls. CPU-only.

---

## What you get

### Per-split metrics

```json
{
  "splits": {
    "train": {
      "sample_count": 1240,
      "columns": ["text"],
      "text_length": {"min": 32, "max": 4096, "mean": 1834.2, "p50": 1900, "p95": 3580},
      "null_or_empty_count": 3,
      "null_or_empty_rate": 0.0024,
      "languages_top3": [
        {"code": "tr", "count": 950},
        {"code": "en", "count": 240},
        {"code": "de", "count": 50}
      ],
      "simhash_distinct": 1180,
      "near_duplicate_pairs": 60,
      "pii_counts": {"email": 18, "phone": 4}
    }
  }
}
```

### Cross-split overlap

```json
{
  "cross_split_overlap": {
    "hamming_threshold": 3,
    "pairs": {
      "train__test": {
        "leaked_rows_in_train": 7,
        "leak_rate_train": 0.0056,
        "leaked_rows_in_test": 7,
        "leak_rate_test": 0.7
      }
    }
  }
}
```

The audit reports leak rate **in both directions** because they tell
different stories. With 1240 train rows and 10 test rows where 7 leak,
`leak_rate_train = 7/1240 = 0.56%` looks negligible but
`leak_rate_test = 7/10 = 70%` is the metric that actually destroys
benchmark fidelity. Always read the smaller-side rate — that is the
silent killer of test integrity.

### PII summary

```json
{
  "pii_summary": {
    "email": 18,
    "phone": 4,
    "credit_card": 1,
    "tr_id": 2
  }
}
```

Each row's text payload is scanned with regex; credit cards run through an
issuer-prefix + Luhn check, TR national IDs run through the TC Kimlik No
checksum. Other categories surface on regex shape alone — false positives
are intentional. Mask with `forgelm ingest --pii-mask` (or in your own
preprocessing) before publishing the dataset.

### PII severity tiers (Phase 11.5)

The flat `pii_summary` map gives compliance reviewers no guidance on
*how bad* a finding is. Phase 11.5 adds a `pii_severity` block alongside:

```json
{
  "pii_severity": {
    "total": 25,
    "by_tier": {"critical": 1, "high": 2, "medium": 18, "low": 4},
    "by_type": {
      "credit_card": {"count": 1, "tier": "critical"},
      "tr_id":      {"count": 2, "tier": "high"},
      "email":      {"count": 18, "tier": "medium"},
      "phone":      {"count": 4, "tier": "low"}
    },
    "worst_tier": "critical"
  }
}
```

The tier table is consensus regulatory weighting (PCI-DSS for financial
identifiers; GDPR Art. 9 + ENISA for government IDs). The `critical`
tier now gates the command itself (next section); pipelines that also
want to stop at `high` should read `pii_severity.worst_tier` and refuse
to publish without explicit review.

### PII gate — critical tier only

`forgelm audit` exits **`3`** (evaluation-gate failure — the same code
the secrets gate uses, and the same one `forgelm safety-eval` uses when
a threshold is missed) when the scan finds `critical`-tier PII:
`credit_card` or `iban`. A card or account number in a training corpus
is memorised by the model and re-emitted at inference time, so an audit
that reports one and then exits `0` has told the pipeline everything is
fine.

```text
[ERROR] PII gate FAILED (critical): 1 critical-tier PII span(s) detected (credit_card=1).
These pass an issuer-prefix and checksum test, so they are indistinguishable from real card /
account numbers — and such a value in training data is memorised and re-emitted at inference
time. Mask it with `forgelm ingest --pii-mask`, or re-run `forgelm audit --allow-pii` to record
the findings without failing the pipeline. Exiting 3.
```

**Why only the `critical` tier.** `credit_card` and `iban` clear a
checksum before they are counted — a real issuer prefix plus Luhn for
cards, ISO 7064 mod-97 for IBANs — so a hit is indistinguishable from a
genuine card or account number. That is the property that makes a hard
gate defensible. That the card check also requires a real issuer prefix
(IIN), not Luhn alone, is what keeps it off IMEIs and order numbers —
Luhn is a mod-10 checksum that ~9.8% of random 16-digit runs pass and
every IMEI passes by construction. The sub-critical families
(`de_id`, `fr_ssn`, `us_ssn`, `email`, `phone`) are matched on regex
shape alone and *deliberately* over-report, because the audit's job is
to surface candidates for a human to judge. Gating on a deliberately
over-reporting signal fails clean corpora — a support-ticket dataset
full of legitimate example addresses would go red on every run — and
the first thing an operator does with a gate that cries wolf is switch
it off, which takes the trustworthy half down with it. Those families
are still detected, counted, tiered and written to the report; they
simply do not decide the exit code.

`tr_id` is the one family that clears a checksum (TC Kimlik No) without
gating: the gate reads the declared tier in `PII_SEVERITY`, where
`tr_id` sits at `high`. Checksum validation is a *precondition* for
gating, not a trigger — an invariant pinned by
`tests/test_audit_pii_gate.py::TestCriticalTierIsChecksumBacked`, so
promoting a shape-matched family to `critical` fails the suite instead
of silently arming the gate on a noisy signal.

`--allow-pii` records the findings without failing (exit `0` plus a
`SUPPRESSED` warning). Detection, the printed summary and the on-disk
report are unaffected — only the exit code is suppressed. It is a
command-line flag rather than a config field or an env var so a
reviewer reading the pipeline diff can see it. It is independent of
`--allow-secrets`: suppressing one leaves the other armed. Both gates
report before either exits the process, so a corpus carrying a leaked
key *and* a real card number surfaces both errors in a single run.

Under `--output-format json` the verdict is a `pii_gate` object beside
its `secrets_gate` sibling, and `success` is `false` when either gate
fails:

```json
{
  "pii_gate": {
    "status": "failed",
    "severity": "critical",
    "critical_total": 1,
    "critical_types": {"credit_card": 1},
    "advisory_total": 3,
    "advisory_types": {"email": 1, "phone": 1, "us_ssn": 1},
    "allow_pii": false
  }
}
```

`status` is `"suppressed"` (not `"passed"`) when findings were present
but `--allow-pii` was passed, so a CI dashboard can tell a clean corpus
apart from a waived one.

**Pattern precedence is documented.** `_PII_PATTERNS` iteration order
governs both detection priority and mask precedence — most specific
patterns (`email`, `iban`, `credit_card`, national IDs) are scanned
first, then the noisier `phone` pattern. When a span could match two
categories, the first / narrower one wins and the span is replaced
before the next pattern sees it. Phone is intentionally anchored to
`+CC` or `(area)` formats so bare digit runs (timestamps, log line
numbers, ISO dates) do not flag.

### Near-duplicate detection

64-bit simhash over case-folded word tokens, paired with Hamming distance
≤ 3 (the cutoff the simhash paper uses for the canonical web-page-dedup
deployment, ≈95% similarity at this width). Exposes both:

- **Within-split** pairs: `near_duplicate_pairs` per split.
- **Cross-split** leakage: above.

Phase 11.5 swapped the underlying scan to **LSH banding**: pigeonhole
chooses `bands = threshold + 1`, candidate pairs are exactly the rows
that collide in any band-bucket, and the Hamming check only runs on
candidates. Recall stays exact at the default threshold; cost drops from
`O(n²)` to roughly `O(n × k)` (the `_count_leaked_rows` cross-split
helper uses the same banded shape). The brute-force path remains as the
fallback when the threshold is high enough that bands shrink below
4 bits — `find_near_duplicates` returns the same result either way.

Phase 11.5 also made the simhash backend pluggable:

- **xxhash.xxh3_64** drives the per-token digest when the optional
  `xxhash` dep is installed (now part of `forgelm[ingestion]`). The
  Python-level speedup is modest — a local microbenchmark on Apple
  Silicon / Python 3.11 measured ~1.3× on the raw per-digest cost and
  ~1.05× end-to-end inside `compute_simhash` (the `lru_cache` below
  absorbs most repeats). The "4-10×" figure xxhash advertises refers
  to C-level pure-hash benchmarks; Python wrapping levels the playing
  field. The optional dep is mostly forward-compat / parity with other
  simhash implementations, not a throughput win.
- **BLAKE2b** is the fallback so a bare install still works.
- A module-scope `lru_cache(maxsize=10_000)` memoises the digest at the
  token level — Zipfian token frequency means the cache covers most of
  a corpus's traffic with a small footprint, which is where most of
  the real wall-clock improvement comes from.

### MinHash LSH dedup (Phase 12)

For corpora above ~50K rows, simhash + LSH banding starts to feel its
edge: the band-bucket fan-out grows and false-positive checks dominate
the wall clock. Phase 12 adds an opt-in **MinHash LSH** path via the
optional `[ingestion-scale]` extra (the `datasketch` package). Surface:

```bash
pip install 'forgelm[ingestion-scale]'
forgelm audit data/large_corpus.jsonl \
  --dedup-method minhash --jaccard-threshold 0.85
```

```python
from forgelm.data_audit import audit_dataset
audit_dataset("data/large_corpus.jsonl",
              dedup_method="minhash", minhash_jaccard=0.85)
```

The two methods are **not interchangeable on identical thresholds** —
simhash at Hamming distance ≤ 3 ≈ MinHash at Jaccard ≥ 0.85 in
practice, but the underlying definitions of "similar" differ. MinHash
is approximate (permutation noise; default `num_perm=128`), so the same
pair can be flagged at slightly different similarity scores between
runs of MinHash itself when `num_perm` changes. Pin `num_perm` if you
need cross-run determinism. The audit JSON gains a
`near_duplicate_summary.method` field that records which path ran and
a `near_duplicate_summary.pairs_per_split` mapping that mirrors the
per-split pair counts. Older consumers reading the per-split count
directly — `splits.<name>.near_duplicate_pairs` (e.g.
`jq '.splits.train.near_duplicate_pairs' data_audit_report.json`) —
continue to work unchanged; the new summary block is purely additive.

### Code / secret leakage tagger (Phase 12, always-on)

Audit now scans every row for credentials and tokens that should never
have entered an SFT corpus — fine-tuning on text that contains a real
API key memorises that key in the model. The detector uses a narrow
prefix-anchored regex set (false-positive rate intentionally low) and
emits a `secrets_summary` block alongside `pii_summary`:

```json
{
  "secrets_summary": {
    "aws_access_key": 1,
    "github_token": 2,
    "openai_api_key": 1
  }
}
```

Coverage: AWS access keys (`AKIA…` / `ASIA…`), GitHub PATs (`ghp_`,
`gho_`, `ghs_`, `ghu_`, `ghr_`, `github_pat_`), Slack tokens (`xox[baprs]-`),
OpenAI API keys (`sk-…` and project-scoped `sk-proj-…`), Google API
keys (`AIza…`), JSON Web Tokens (anchored on the `eyJ`-encoded JSON
header), OpenSSH / RSA / DSA / EC / PGP private-key blocks (full
`BEGIN…END` envelope — `mask_secrets()` redacts the entire key block,
not just the header line), and Azure storage connection strings.

Operator-side, two paths to scrub these out before training:

```bash
# Pre-process: rewrite the JSONL after the fact via the helper API
python -c "from forgelm.data_audit import mask_secrets; \
  print(mask_secrets(open('data.jsonl').read()))" > data_clean.jsonl

# Or scrub during ingest (Phase 12; before chunks ever land in JSONL)
forgelm ingest ./policies/ --recursive --output data/policies.jsonl --secrets-mask
```

Optional / forward-compatibility: the `[ingestion-secrets]` extra
declares a `detect-secrets>=1.5.0` dependency that is **reserved for a
follow-up release**. As of v0.5.0 the audit's
`forgelm.data_audit.detect_secrets()` relies solely on the regex set
above; installing the extra today does not change audit behaviour. The
extra exists so operators who pin `forgelm[ingestion-secrets]` in
their requirements file are forward-compatible when the integration
lands.

### Heuristic quality filter (Phase 12, opt-in)

`forgelm audit --quality-filter` runs Gopher / C4 / RefinedWeb-style
heuristics per row and surfaces a `quality_summary` block:

```json
{
  "quality_summary": {
    "samples_flagged": 47,
    "by_check": {
      "low_alpha_ratio": 12,
      "low_punct_endings": 8,
      "abnormal_mean_word_length": 3,
      "short_paragraphs": 27,
      "repeated_lines": 5
    },
    "overall_quality_score": 0.94
  }
}
```

Checks (all conservative; no row is silently dropped):

- `low_alpha_ratio` — < 70 % of non-whitespace chars are letters.
- `low_punct_endings` — < 50 % of non-empty lines end with punctuation.
- `abnormal_mean_word_length` — outside the 3-12 char window.
- `short_paragraphs` — > 50 % of `\n\n`-separated blocks have < 5 words.
- `repeated_lines` — top-3 actually-repeating lines (count ≥ 2) cover
  > 30 % of all non-empty lines. Catches boilerplate (headers, footers,
  repeated disclaimers) that bloats training without adding signal.

Markdown fenced code blocks (```` ``` ```` and ``~~~``) are stripped
before applying these heuristics — code legitimately has low alpha
ratio, missing end-of-line punctuation, and short paragraphs, so
applying prose checks to fenced code would produce false flags on
legitimate code-instruction SFT corpora. Pure-code rows surface zero
flags rather than being flagged on shape grounds.

ML-based quality classifiers (fastText / DeBERTa style) are deliberately
**out of scope**; a deterministic regex/length/structure pipeline keeps
the audit reproducible (Annex IV requirement) and bare-install-friendly.

---

### ML-NER PII adapter — `--pii-ml` (Phase 12.5, opt-in)

The default regex detector covers the structured identifiers EU AI Act
Article 10 cares about (email, phone, IBAN, credit card, national IDs).
Phase 12.5 adds an opt-in **Presidio** ([microsoft/presidio](https://github.com/microsoft/presidio))
adapter that layers ML-NER on top — adding the unstructured-identifier
categories regex inherently misses: `person`, `organization`, `location`.

```bash
pip install 'forgelm[ingestion-pii-ml]'
forgelm audit data/ --output ./audit/ --pii-ml
```

The new categories merge into the existing `pii_summary` and
`pii_severity` blocks under disjoint names so the regex baseline stays
visible alongside the ML signal:

```json
{
  "pii_summary": {
    "email": 12,
    "phone": 3,
    "person": 47,         // ← Presidio
    "organization": 18,   // ← Presidio
    "location": 9         // ← Presidio
  },
  "pii_severity": {
    "by_tier": {"critical": 0, "high": 0, "medium": 59, "low": 30},
    "by_type": {
      "email": {"count": 12, "tier": "medium"},
      "person": {"count": 47, "tier": "medium"},
      "organization": {"count": 18, "tier": "low"}
    },
    "worst_tier": "medium"
  }
}
```

Severity assignment lives in `forgelm.data_audit.PII_ML_SEVERITY`:
`person → medium`, `organization → low`, `location → low`. NER
false-positive rates are materially higher than regex-anchored
detection, so the ML tiers sit deliberately below the regex
`critical`/`high` floors — review the per-row spans before treating an
ML finding as a hard gate.

**Two-step install.** `presidio-analyzer` does **not** transitively
ship a spaCy NER model — that's a separate one-time download:

```bash
pip install 'forgelm[ingestion-pii-ml]'
python -m spacy download en_core_web_lg   # ~ 50 MB; required
```

Without the model, `forgelm audit --pii-ml` raises an `ImportError`
with the same install hint **before** any rows are scanned (the
pre-flight check in `forgelm.data_audit._require_presidio` catches
spaCy's `OSError` and re-raises it as the typed install error). This
fail-loud behaviour exists because earlier prototypes silently scored
zero ML findings on a missing model — a critical compliance blind
spot for an opt-in detector that operators expect to actually run.

For non-English corpora, install the matching spaCy model and pass
the language code:

```bash
python -m spacy download xx_ent_wiki_sm        # multilingual, smaller
forgelm audit data/ --pii-ml --pii-ml-language xx
```

The adapter is opt-in only; without `--pii-ml` the audit stays on the
zero-extra-deps regex path.

---

### Croissant 1.0 dataset card — `--croissant` (Phase 12.5, opt-in)

`--croissant` populates a new top-level `croissant` block in
`data_audit_report.json` with a [Google Croissant 1.0](https://mlcommons.org/croissant/)
dataset card (`@type: sc:Dataset`). The card is conformant with the
canonical `mlcommons.org/croissant/1.0` context, so Croissant-aware
consumers (HuggingFace dataset cards, MLCommons reference loaders, the
Croissant validator) can parse the block without modification.

```bash
forgelm audit data/ --output ./audit/ --croissant
```

The card carries:

* dataset-level identity (`name`, `description`, `datePublished`,
  `url`) — `version` is intentionally omitted because the audit has
  no first-class evidence for dataset version; operators that publish
  the card hand-edit `version` like they do `license` / `citeAs`,
* one `cr:FileObject` per JSONL split (so a Croissant consumer can
  locate the underlying files),
* one `cr:RecordSet` per split with `cr:Field` entries derived from
  the audit's column-detection layer.

The block is empty when the flag is off — existing consumers see
byte-equivalent output, the same precedent set by `secrets_summary` /
`quality_summary`. Operators that want to publish the card to
HuggingFace / MLCommons can hand-edit the additional Croissant fields
the audit doesn't have first-class evidence for (`license`, `citeAs`,
`keywords`) without re-running the audit.

---

## Layout requirements

| Input shape | What you get |
|---|---|
| `*.jsonl` file | Single split named `train` |
| `dir/` containing any of `train.jsonl`, `validation.jsonl`, `test.jsonl` | Each present file becomes its own split |
| `dir/` containing common aliases (`dev`, `val`, `valid`, `eval`, `holdout`) | Folded onto canonical split names — `dev.jsonl` → `validation`, `eval.jsonl` → `test`, etc. |
| `dir/` containing only non-canonical `*.jsonl` | Pseudo-split fallback: each `*.jsonl` becomes its own split AND a warning is emitted that cross-split leakage analysis is meaningless without a real partition |

The auditor reads the first text-bearing column it finds, in this priority:
`text` → `content` → `completion` → `prompt`. For `messages`-format chat
data, the role-tagged content is concatenated.

**Schema drift is surfaced.** Heterogeneous JSONL (rows with optional fields)
is allowed — the column schema is the union of keys across rows; any column
that appears after row 0 is reported under `schema_drift_columns` so
operators can decide whether the drift is intentional.

---

## Article 10 governance integration

When `data_audit_report.json` exists in the trainer's `training.output_dir`
at training time, [`generate_data_governance_report`](../../forgelm/compliance.py)
inlines its findings under the `data_audit` key of the governance artifact.
Your compliance bundle becomes a single self-contained document rather than
a pointer to a separate file.

The recommended workflow:

```bash
# Audit first — surfaces issues before you commit to a long training run
forgelm audit data/policies.jsonl --output ./checkpoints/policy-run/

# Train (governance artifact will inline the audit)
forgelm --config configs/policy-run.yaml
```

---

## CLI reference

```text
forgelm audit PATH \
  [--output DIR] \
  [--verbose] \
  [--near-dup-threshold N] \
  [--dedup-method {simhash,minhash}] \
  [--jaccard-threshold X] \
  [--quality-filter | --no-quality-filter] \
  [--pii-ml] \
  [--pii-ml-language LANG] \
  [--croissant] \
  [--workers N] \
  [--allow-secrets] \
  [--allow-pii] \
  [--output-format {text,json}] \
  [--quiet | --log-level {DEBUG,INFO,WARNING,ERROR}]
```

`PATH` may be a `.jsonl` file or a directory. `--output` defaults to
`./audit/`. `--verbose` shows every split in the human summary even when
it has zero findings (default folds clean splits into one tail line so
multi-split audits stay short — has no effect on the on-disk JSON
report). `--near-dup-threshold N` overrides the default simhash
Hamming-distance cutoff of 3 (≈95 % similarity); ignored when
`--dedup-method=minhash`. `--dedup-method` (Phase 12) selects the
near-duplicate engine — `simhash` (default) or `minhash` (needs
`[ingestion-scale]` extra; `--jaccard-threshold` controls the cutoff,
default 0.85). `--quality-filter` (Phase 12, **default-ON from v0.6.0
per Phase 15 Task 5**) runs the heuristic quality scoring;
`--no-quality-filter` opts out for operators wanting the pre-v0.6.0
opt-in semantics. The credential/secrets scan is **always on** — there
is no flag to disable it. `--allow-secrets` and `--allow-pii` suppress
the *exit code* of the two gates (credentials, and critical-tier PII)
without touching detection or reporting; they are independent of each
other. See [PII gate — critical tier only](#pii-gate--critical-tier-only)
above.

> **Note:** This matches the behavior summarised at the top of this guide:
> `--output-format json` writes a small envelope (success flag, top-level
> metrics, report path) to stdout. The full `data_audit_report.json` is
> always written to `--output` via `tempfile.NamedTemporaryFile` +
> `os.replace` — Phase 11.5 hardening so a crashed audit can never leave
> a half-written report on disk.

`--workers N` (Phase 17) opts into split-level parallelism: each split
runs in its own worker process inside a `multiprocessing.Pool`.  Default
is `1` (sequential, byte-identical to the pre-Phase-17 path).  Speed-up
scales with the number of *splits*, not with row count — a single-split
corpus ignores values >1.  Typical multi-split runs (`train` /
`validation` / `test`) see a near-linear speed-up at `--workers 3`.

The merge step that builds the final report stays single-threaded so
the on-disk JSON is **byte-identical** across worker counts (the only
field that varies is `generated_at`, the wall-clock timestamp).  This
invariant is pinned by `tests/test_data_audit_workers.py` so a CI gate
that compares the audit-report hash across runs continues to work
when an operator switches from `--workers 1` to `--workers 4`.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Audit failed: ... not found or empty` | Path doesn't exist or has no `.jsonl` | Verify the path; pass a file or a `train.jsonl` directory layout |
| `"unknown (install forgelm[ingestion])"` in language stats | `langdetect` not installed | `pip install 'forgelm[ingestion]'` |
| Cross-split leakage flags 100% of rows | All splits contain identical content | Re-shuffle; you probably copied the same JSONL into every split |
| `near_duplicate_pairs` very large on a real corpus | Genuinely high near-duplication (boilerplate / repeated headers / dataset quality issue) — Phase 11.5 LSH banding already replaces the old O(n²) scan with O(n × k), so a large pair count is signal, not algorithmic noise | Tighten with `--near-dup-threshold 1` or 0 to keep only very-close matches; for PDFs, header/footer dedup runs automatically (see ingestion guide); inspect a few flagged pairs and decide whether to dedupe or accept |

---

## Programmatic API

```python
from dataclasses import asdict
from forgelm.data_audit import audit_dataset

report = audit_dataset("data/sft.jsonl", output_dir="./audit/")
print(report.total_samples, report.pii_summary)

# Or serialize manually:
import json
json.dump(asdict(report), open("custom_path.json", "w"), indent=2)
```

`AuditReport` is a plain dataclass — `dataclasses.asdict()` gives you a
JSON-ready dict. The PII regex helpers (`detect_pii`, `mask_pii`) and the
simhash function (`compute_simhash`) are also part of the public API.
