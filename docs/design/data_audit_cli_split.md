# Split Design — `forgelm/data_audit.py` and `forgelm/cli.py`

> **Scope:** Design specification for the sub-package split of two
> historically-monolithic modules: `forgelm/data_audit.py` (was 3098
> lines) → `forgelm/data_audit/` package, and `forgelm/cli.py` (was
> 1756 lines) → `forgelm/cli/` package. Records the chosen module
> boundary, public-symbol re-export shape, and lazy-import contract
> rationale. Living spec — kept in sync with the implementation under
> `forgelm/data_audit/__init__.py` and `forgelm/cli/__init__.py`.

**Status:** Implemented in v0.5.5 (Phase 14 + Phase 15 splits). See
`CHANGELOG.md` for the user-facing summary.

---

## 0. Scope, methodology, ground truth

**Files surveyed (full reads):**
- `forgelm/data_audit.py` (3098 lines, end-to-end)
- `forgelm/cli.py` (1756 lines, end-to-end)
- `pyproject.toml` (entry point block)
- `docs/standards/architecture.md` (cohesion ceiling and split rule)
- All consumers under `tests/` and within `forgelm/` (cross-import grep)

**Architecture standard, line 71** (verbatim):

> If a module grows past ~1000 lines and has cohesive subsections, split into `module_name/` package, but keep the public API at `forgelm.module_name.X` so imports don't break.

`data_audit.py` (3098) is 3.10× the ceiling. `cli.py` (1756) is 1.76× the ceiling. Both qualify; the split mechanism is also pre-authorized by the standard.

**Public API contract (load-bearing) — actual external import points found in the repo:**

`forgelm.data_audit`:
- Tests import: `DEFAULT_NEAR_DUP_HAMMING`, `PII_TYPES`, `AuditReport`, `_is_credit_card`, `_is_tr_id`, `audit_dataset`, `compute_simhash`, `detect_pii`, `find_near_duplicates`, `hamming_distance`, `mask_pii`, `summarize_report`, `DEDUP_METHODS`, `DEFAULT_MINHASH_JACCARD`, `SECRET_TYPES`, `_row_quality_flags`, `detect_secrets`, `mask_secrets`, `compute_minhash`, `find_near_duplicates_minhash`, `_find_near_duplicates_brute`, `_count_leaked_rows`, `_read_jsonl_split`, `_token_digest`, `_strip_code_fences`, `_require_presidio`, `PII_ML_SEVERITY`, `_PRESIDIO_ENTITY_MAP`, `_get_presidio_analyzer`, `detect_pii_ml`, `_HAS_PRESIDIO` (patched), `_PresidioAnalyzer` (patched).
- In-package callers: `forgelm/ingestion.py` (`mask_pii`, `mask_secrets`), `forgelm/wizard.py` (`audit_dataset`, `summarize_report`).

`forgelm.cli`:
- Tests import: `EXIT_SUCCESS`, `EXIT_CONFIG_ERROR`, `EXIT_TRAINING_ERROR`, `EXIT_EVAL_FAILURE`, `EXIT_AWAITING_APPROVAL`, `_get_version`, `_resolve_resume_checkpoint`, `_run_dry_run`, `_setup_logging`, `_run_compliance_export`, `_run_fit_check`, `_run_data_audit`, `_output_result`, `_build_quickstart_inherited_flags`, `parse_args`, `main`.
- Public entry point: `forgelm.cli:main` registered in `pyproject.toml [project.scripts]`.

**Crucial finding:** Even underscore-prefixed helpers (`_run_data_audit`, `_token_digest`, `_strip_code_fences`, `_HAS_PRESIDIO`, `_PresidioAnalyzer`, `_get_presidio_analyzer`, `_PRESIDIO_ENTITY_MAP`, `_count_leaked_rows`, `_read_jsonl_split`, `_find_near_duplicates_brute`, `_row_quality_flags`, `_is_credit_card`, `_is_tr_id`) are reached from tests via `from forgelm.data_audit import …` and are also patched via `patch("forgelm.data_audit._HAS_PRESIDIO", …)`. **Re-export at the package level is not optional — it is mandatory for these private helpers as well, because `monkeypatch`/`unittest.mock.patch` requires the name to resolve at exactly that dotted path.** This pins the design: every symbol the tests reach must be addressable as `forgelm.data_audit.<name>` post-split, even if it physically lives in a sub-module.

---

## 1. `data_audit.py` — Detailed analysis

### 1.a Concern inventory

Line ranges are derived from the actual file just read.

| # | Concern | Lines | Public symbols | Internal helpers | Imports / coupling |
|---|---|---|---|---|---|
| 1 | Optional-deps sentinel block (xxhash, numpy, datasketch, presidio) | 45–112 | — | `_HAS_XXHASH`, `_xxhash`, `_HAS_NUMPY`, `_np`, `_HAS_DATASKETCH`, `_MinHash`, `_MinHashLSH`, `_HAS_PRESIDIO`, `_PresidioAnalyzer` | stdlib only; tests patch `_HAS_PRESIDIO`, `_PresidioAnalyzer` |
| 2 | PII regex types + severity | 119–203 | `PII_TYPES`, `PII_SEVERITY`, `PII_SEVERITY_ORDER`, `PII_ML_SEVERITY`, `PII_ML_TYPES`, `DEFAULT_NEAR_DUP_HAMMING`, `DEFAULT_MINHASH_JACCARD`, `DEFAULT_MINHASH_NUM_PERM`, `DEDUP_METHODS` | `_TEXT_COLUMNS` | none |
| 3 | `AuditReport` dataclass | 206–236 | `AuditReport` | — | `dataclass`, `field` only |
| 4 | PII regex detector + masker | 239–392 | `detect_pii`, `mask_pii` | `_PII_PATTERNS`, `_is_credit_card`, `_is_tr_id`, `_validate_match` | uses Concern 2 (`PII_TYPES` indirectly) |
| 5 | Presidio ML adapter | 395–639 | `detect_pii_ml` | `_SPACY_MODEL_FOR_LANGUAGE`, `_get_presidio_analyzer` (lru_cache), `_PRESIDIO_INSTALL_HINT`, `_require_presidio`, `_PRESIDIO_ENTITY_MAP` | needs Concern 1 (`_HAS_PRESIDIO`, `_PresidioAnalyzer`); needs Concern 2 (`PII_ML_TYPES`) |
| 6 | Tokenizer + simhash core | 642–759 | `compute_simhash`, `hamming_distance` | `_TOKEN_PATTERN`, `_tokenize`, `_token_digest` (lru_cache), `_compute_simhash_numpy` | needs Concern 1 (`_HAS_XXHASH`, `_xxhash`, `_HAS_NUMPY`, `_np`) |
| 7 | Simhash LSH banding + near-dup pair finder | 762–890, 1957–2032 | `find_near_duplicates`, `_count_leaked_rows` | `_LSH_MIN_BAND_BITS`, `_band_count_for_threshold`, `_split_into_bands`, `_find_near_duplicates_brute`, `_bucket_pairs_within_threshold`, `_build_band_index`, `_count_leaked_brute`, `_source_row_leaks` | needs Concern 6 (`hamming_distance`) |
| 8 | MinHash LSH | 893–1114 | `compute_minhash`, `find_near_duplicates_minhash` | `_require_datasketch`, `_build_minhash_lsh`, `_emit_minhash_pair`, `_count_leaked_rows_minhash`, `_count_leaks_against_index`, `_count_leaked_rows_minhash_bidirectional` | needs Concern 1 (`_HAS_DATASKETCH`, `_MinHash`, `_MinHashLSH`); needs Concern 6 (`_tokenize`) |
| 9 | Language detection + length stats helpers | 1117–1147 | — | `_detect_language`, `_length_stats` | optional `langdetect` |
| 10 | Streaming length digest (reservoir) | 1150–1212 | — | `_LENGTH_RESERVOIR_SIZE`, `_LengthDigest` (class) | none |
| 11 | Secret regex detector + masker | 1215–1336 | `SECRET_TYPES`, `detect_secrets`, `mask_secrets` | `_SECRET_PATTERNS` | none |
| 12 | Quality filter (Gopher/C4 heuristics) | 1339–1551 | — | `_QUALITY_CHECKS`, `_WORD_PATTERN`, `_PUNCT_END_PATTERN`, `_is_code_fence_open`, `_is_code_fence_close`, `_strip_code_fences`, `_check_low_alpha_ratio`, `_check_low_punct_endings`, `_check_abnormal_mean_word_length`, `_check_short_paragraphs`, `_check_repeated_lines`, `_row_quality_flags` | none |
| 13 | Streaming JSONL reader + helpers | 1553–1622 | — | `_extract_text_payload`, `_read_jsonl_split` (generator), `_PROGRESS_INTERVAL`, `_LANG_SAMPLE_SIZE` | needs Concern 2 (`_TEXT_COLUMNS`) |
| 14 | Per-split streaming aggregator | 1624–1814 | — | `_StreamingAggregator` (dataclass), `_record_schema_for_dict`, `_record_dedup_signature`, `_record_dedup_sentinel`, `_record_text_metrics`, `_ingest_row`, `_populate_schema_block`, `_populate_optional_findings`, `_within_split_pairs` | needs Concerns 4, 5, 6, 8, 10, 11, 12 (heavy hub) |
| 15 | Per-split → info dict + lang top-3 | 1817–1955 | — | `_aggregator_to_info`, `_compute_top_languages`, `_audit_split` | needs Concerns 9, 10, 14 |
| 16 | Cross-split overlap | 2068–2122, 2035–2065 | — | `_cross_split_overlap`, `_pair_leak_payload` | needs Concerns 7, 8 |
| 17 | Split discovery / aliases | 2125–2210 | — | `_SPLIT_ALIASES`, `_scan_canonical_split_files`, `_scan_pseudo_split_files`, `_resolve_directory_splits`, `_resolve_input` | stdlib `pathlib` |
| 18 | Per-split processor + outcome | 2213–2309 | — | `_SplitOutcome` (dataclass), `_process_split` | needs Concern 15 |
| 19 | PII severity + summary notes | 2312–2406 | — | `_build_pii_severity`, `_pii_summary_notes`, `_cross_split_leak_notes` | needs Concern 2 |
| 20 | Secrets/quality summary notes | 2409–2502 | — | `_secrets_summary_notes`, `_quality_summary_notes`, `_fold_outcome_into_summary`, `_build_quality_summary` | — |
| 21 | Croissant 1.0 emitter | 2505–2693 | — | `_JSONLD_TYPE_KEY`, `_JSONLD_ID_KEY`, `_CROISSANT_CONTEXT`, `_build_croissant_metadata` | stdlib |
| 22 | `audit_dataset` orchestrator + atomic JSON writer | 2696–2960 | `audit_dataset` | `_build_near_duplicate_summary`, `_atomic_write_json` | hub of Concerns 5, 8, 17, 18, 19, 20, 21 |
| 23 | `summarize_report` text renderer | 2963–3098 | `summarize_report` | `_split_has_findings`, `_render_split_block`, `_render_pii_severity` | needs `AuditReport` only |

Total: 23 concerns. Master review's expected list (PII regex / Presidio / secrets / simhash / MinHash / streaming / quality / Croissant / length digest / lang sample) is fully covered; the file additionally hosts the orchestrator and the report renderer, both of which need their own home.

### 1.b Public API surface (full signatures)

These signatures must remain bit-identical at `forgelm.data_audit.<name>` after the split:

```python
# Constants
PII_TYPES: Tuple[str, ...]
PII_SEVERITY: Dict[str, str]
PII_SEVERITY_ORDER: Tuple[str, ...]
PII_ML_SEVERITY: Dict[str, str]
PII_ML_TYPES: Tuple[str, ...]
DEFAULT_NEAR_DUP_HAMMING: int
DEFAULT_MINHASH_JACCARD: float
DEFAULT_MINHASH_NUM_PERM: int
DEDUP_METHODS: Tuple[str, ...]
SECRET_TYPES: Tuple[str, ...]

# Dataclass
class AuditReport:
    generated_at: str
    source_path: str
    source_input: str
    total_samples: int
    splits: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    cross_split_overlap: Dict[str, Any] = field(default_factory=dict)
    pii_summary: Dict[str, int] = field(default_factory=dict)
    pii_severity: Dict[str, Any] = field(default_factory=dict)
    near_duplicate_summary: Dict[str, Any] = field(default_factory=dict)
    secrets_summary: Dict[str, int] = field(default_factory=dict)
    quality_summary: Dict[str, Any] = field(default_factory=dict)
    croissant: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

# Functions
def detect_pii(text: Any) -> Dict[str, int]: ...
def mask_pii(text: Any, replacement: str = "[REDACTED]", *, return_counts: bool = False) -> Any: ...
def detect_pii_ml(text: Any, *, language: str = "en") -> Dict[str, int]: ...
def detect_secrets(text: Any) -> Dict[str, int]: ...
def mask_secrets(text: Any, replacement: str = "[REDACTED-SECRET]", *, return_counts: bool = False) -> Any: ...
def compute_simhash(text: str, *, bits: int = 64) -> int: ...
def hamming_distance(a: int, b: int) -> int: ...
def find_near_duplicates(fingerprints: List[int], *, threshold: int = DEFAULT_NEAR_DUP_HAMMING, bits: int = 64) -> List[Tuple[int, int, int]]: ...
def compute_minhash(text: str, *, num_perm: int = DEFAULT_MINHASH_NUM_PERM) -> Optional[Any]: ...
def find_near_duplicates_minhash(minhashes: List[Optional[Any]], *, jaccard_threshold: float = DEFAULT_MINHASH_JACCARD, num_perm: int = DEFAULT_MINHASH_NUM_PERM) -> List[Tuple[int, int, float]]: ...
def audit_dataset(source: str, *, output_dir: Optional[str] = None, near_dup_threshold: int = DEFAULT_NEAR_DUP_HAMMING, dedup_method: str = "simhash", minhash_jaccard: float = DEFAULT_MINHASH_JACCARD, minhash_num_perm: int = DEFAULT_MINHASH_NUM_PERM, enable_quality_filter: bool = False, enable_pii_ml: bool = False, pii_ml_language: str = "en", emit_croissant: bool = False) -> AuditReport: ...
def summarize_report(report: AuditReport, *, verbose: bool = False) -> str: ...
```

**Test-touched private symbols that MUST also be re-exported** (because tests do `from forgelm.data_audit import _name` or `patch("forgelm.data_audit._name", …)`):

`_is_credit_card`, `_is_tr_id`, `_find_near_duplicates_brute`, `_count_leaked_rows`, `_read_jsonl_split`, `_token_digest`, `_strip_code_fences`, `_row_quality_flags`, `_require_presidio`, `_PRESIDIO_ENTITY_MAP`, `_get_presidio_analyzer`, `_HAS_PRESIDIO`, `_PresidioAnalyzer`.

### 1.c Internal cross-coupling

Coupling counted as "concern A imports/calls a symbol that lives in concern B".

**Lowest-coupling (split first):**
- Concern 11 (secrets) — depends on nothing inside the module.
- Concern 4 (PII regex) — depends on nothing inside the module.
- Concern 21 (Croissant) — depends on nothing inside the module.
- Concern 10 (length digest) — depends on nothing inside the module.
- Concern 9 (lang) — depends on nothing inside the module.
- Concern 13 (JSONL reader) — depends only on `_TEXT_COLUMNS` constant.

**Highest-coupling (split last / via re-exports only):**
- Concern 22 (`audit_dataset` orchestrator) — depends on 9 other concerns.
- Concern 14 (`_StreamingAggregator`) — depends on 7 other concerns.
- Concern 6 (simhash) — depends on optional-deps sentinel and is itself a dependency of Concerns 7, 8, 14.

### 1.d Proposed package layout

```text
forgelm/data_audit/
    __init__.py          # facade — re-exports the entire public + test-patched surface
    _optional.py         # optional-deps sentinels (xxhash / numpy / datasketch / presidio handles)
    _types.py            # AuditReport, constants, severity tables, _TEXT_COLUMNS
    _pii_regex.py        # _PII_PATTERNS, _is_credit_card, _is_tr_id, _validate_match,
                         #   detect_pii, mask_pii
    _pii_ml.py           # _SPACY_MODEL_FOR_LANGUAGE, _PRESIDIO_INSTALL_HINT,
                         #   _PRESIDIO_ENTITY_MAP, _get_presidio_analyzer (lru_cache),
                         #   _require_presidio, detect_pii_ml
    _secrets.py          # _SECRET_PATTERNS, detect_secrets, mask_secrets
    _simhash.py          # _TOKEN_PATTERN, _tokenize, _token_digest (lru_cache),
                         #   _compute_simhash_numpy, compute_simhash, hamming_distance,
                         #   _band_count_for_threshold, _split_into_bands,
                         #   _find_near_duplicates_brute, _bucket_pairs_within_threshold,
                         #   _build_band_index, _LSH_MIN_BAND_BITS, find_near_duplicates,
                         #   _count_leaked_brute, _source_row_leaks, _count_leaked_rows
    _minhash.py          # _require_datasketch, compute_minhash, _build_minhash_lsh,
                         #   _emit_minhash_pair, find_near_duplicates_minhash,
                         #   _count_leaked_rows_minhash, _count_leaks_against_index,
                         #   _count_leaked_rows_minhash_bidirectional
    _quality.py          # _QUALITY_CHECKS, _WORD_PATTERN, _PUNCT_END_PATTERN,
                         #   _is_code_fence_open/_close, _strip_code_fences,
                         #   _check_*, _row_quality_flags
    _streaming.py        # _LengthDigest, _LENGTH_RESERVOIR_SIZE, _read_jsonl_split,
                         #   _extract_text_payload, _detect_language, _length_stats,
                         #   _PROGRESS_INTERVAL, _LANG_SAMPLE_SIZE, _compute_top_languages
    _aggregator.py       # _StreamingAggregator + all _record_*/_ingest_row/_populate_*/
                         #   _within_split_pairs/_aggregator_to_info/_audit_split
    _splits.py           # _SPLIT_ALIASES, _scan_canonical_split_files,
                         #   _scan_pseudo_split_files, _resolve_directory_splits,
                         #   _resolve_input, _SplitOutcome, _process_split
    _summary.py          # _build_pii_severity, _pii_summary_notes, _cross_split_leak_notes,
                         #   _secrets_summary_notes, _quality_summary_notes,
                         #   _fold_outcome_into_summary, _build_quality_summary,
                         #   _build_near_duplicate_summary, _split_has_findings,
                         #   _render_split_block, _render_pii_severity, summarize_report
    _croissant.py        # _JSONLD_*, _CROISSANT_CONTEXT, _build_croissant_metadata
    _orchestrator.py     # _cross_split_overlap, _pair_leak_payload, _atomic_write_json,
                         #   audit_dataset
```

| File | Target lines | Concern coverage | Public? |
|---|---|---|---|
| `__init__.py` | 80–120 | Re-export facade only | yes |
| `_optional.py` | 60 | C1 | private (test-patched) |
| `_types.py` | 80 | C2 + C3 | partly public (`AuditReport`, constants) |
| `_pii_regex.py` | 160 | C4 | partly public (`detect_pii`, `mask_pii`) |
| `_pii_ml.py` | 240 | C5 | partly public (`detect_pii_ml`) |
| `_secrets.py` | 130 | C11 | partly public (`SECRET_TYPES`, `detect_secrets`, `mask_secrets`) |
| `_simhash.py` | 290 | C6 + C7 | partly public (`compute_simhash`, `hamming_distance`, `find_near_duplicates`) |
| `_minhash.py` | 230 | C8 | partly public (`compute_minhash`, `find_near_duplicates_minhash`) |
| `_quality.py` | 220 | C12 | private (test-patched: `_strip_code_fences`, `_row_quality_flags`) |
| `_streaming.py` | 130 | C9 + C10 + C13 | private (test-patched: `_read_jsonl_split`) |
| `_aggregator.py` | 340 | C14 + C15 | private |
| `_splits.py` | 180 | C17 + C18 | private |
| `_summary.py` | 350 | C19 + C20 + C23 | partly public (`summarize_report`) |
| `_croissant.py` | 200 | C21 | private |
| `_orchestrator.py` | 280 | C16 + C22 | partly public (`audit_dataset`) |

Total ≈ 2870 source lines across 15 modules. **Every file lands well below the 1000-line ceiling and most below 350**, with the only borderline case being `_aggregator.py` at ~340.

Naming convention: every module starts with `_` because the package itself is `forgelm.data_audit`, and the public face is the package, not its internals.

### 1.e Public-API preservation strategy (the `__init__.py` contract)

```python
# forgelm/data_audit/__init__.py — designed shape
"""Public re-exports — preserves the pre-split ``forgelm.data_audit`` surface."""
from __future__ import annotations

# Public types and constants
from ._types import (
    AuditReport,
    PII_TYPES, PII_SEVERITY, PII_SEVERITY_ORDER,
    PII_ML_SEVERITY, PII_ML_TYPES,
    DEFAULT_NEAR_DUP_HAMMING, DEFAULT_MINHASH_JACCARD, DEFAULT_MINHASH_NUM_PERM,
    DEDUP_METHODS,
)
# PII regex
from ._pii_regex import (
    detect_pii, mask_pii,
    _is_credit_card, _is_tr_id,    # tests
)
# PII ML (Presidio)
from ._pii_ml import (
    detect_pii_ml,
    _require_presidio, _get_presidio_analyzer, _PRESIDIO_ENTITY_MAP,  # tests
)
# Optional-deps sentinels — tests patch these by dotted path
from ._optional import _HAS_PRESIDIO, _PresidioAnalyzer  # noqa: F401
# Secrets
from ._secrets import SECRET_TYPES, detect_secrets, mask_secrets
# Simhash + LSH + cross-split
from ._simhash import (
    compute_simhash, hamming_distance, find_near_duplicates,
    _find_near_duplicates_brute, _count_leaked_rows, _token_digest,  # tests
)
# MinHash
from ._minhash import compute_minhash, find_near_duplicates_minhash
# Quality + streaming primitives reached by tests
from ._quality import _row_quality_flags, _strip_code_fences  # tests
from ._streaming import _read_jsonl_split  # tests
# Orchestrator + summary
from ._orchestrator import audit_dataset
from ._summary import summarize_report

__all__ = [
    "AuditReport", "PII_TYPES", "PII_SEVERITY", "PII_SEVERITY_ORDER",
    "PII_ML_SEVERITY", "PII_ML_TYPES", "DEFAULT_NEAR_DUP_HAMMING",
    "DEFAULT_MINHASH_JACCARD", "DEFAULT_MINHASH_NUM_PERM", "DEDUP_METHODS",
    "SECRET_TYPES",
    "detect_pii", "mask_pii", "detect_pii_ml", "detect_secrets", "mask_secrets",
    "compute_simhash", "hamming_distance", "find_near_duplicates",
    "compute_minhash", "find_near_duplicates_minhash",
    "audit_dataset", "summarize_report",
]
```

**Why each underscore-prefixed re-export is required:**
- Tests use `from forgelm.data_audit import _is_credit_card, _is_tr_id` (test_data_audit.py:13–26).
- Tests use `patch("forgelm.data_audit._HAS_PRESIDIO", False)` and `patch("forgelm.data_audit._PresidioAnalyzer", _BoomAnalyzer)` (test_phase12_5.py:278, 334). For `patch` to work, the symbol must be addressable at exactly that dotted path.

**Critical pivot point:** `patch` mutates the package-level binding, but the module that *reads* the binding (`_pii_ml.py`'s `if not _HAS_PRESIDIO:` guard) imports it from `._optional`, so it would see the unpatched value. To fix this:

(a) **Recommended:** `_pii_ml.py` references the sentinel via a runtime attribute lookup: `from . import _optional` then `if not _optional._HAS_PRESIDIO:`. Tests update their patch paths to `patch("forgelm.data_audit._optional._HAS_PRESIDIO", …)` in lockstep with the PR.

(b) Alternative: leave the package-level `from ._optional import _HAS_PRESIDIO` re-export AND have `_pii_ml.py` do `from forgelm.data_audit import _HAS_PRESIDIO` — guaranteed circular import.

**Decision:** Path (a). The split PR rewrites the affected test patches.

### 1.f Migration sequence (data_audit)

| PR | Concerns moved | Public/private symbols moved | Test impact | Effort | Risk |
|---|---|---|---|---|---|
| **D-1** Extract `_streaming.py` + `_quality.py` + `_secrets.py` + `_pii_regex.py` (independent leaf concerns) | C9, C10, C11, C12, C13, C4 | `detect_pii`, `mask_pii`, `_is_credit_card`, `_is_tr_id`, `SECRET_TYPES`, `detect_secrets`, `mask_secrets`, `_strip_code_fences`, `_row_quality_flags`, `_read_jsonl_split` | New `forgelm/data_audit/__init__.py` re-exports everything; `data_audit.py` shrinks to ~2400 lines and imports back via `from ._pii_regex import …`. Existing tests unchanged. | 1 day | Low. Pure mechanical move. |
| **D-2** Extract `_optional.py` + `_types.py` + `_simhash.py` + `_minhash.py` | C1, C2, C3, C6, C7, C8 | `AuditReport`, `compute_simhash`, `hamming_distance`, `find_near_duplicates`, `compute_minhash`, `find_near_duplicates_minhash`, `_token_digest`, `_find_near_duplicates_brute`, `_count_leaked_rows`, `PII_TYPES`, `DEDUP_METHODS`, `DEFAULT_*` constants | All test patches involving `_HAS_XXHASH`/`_HAS_NUMPY`/`_HAS_DATASKETCH` rewired to `forgelm.data_audit._optional._HAS_*`. **Update test files in the same PR.** | 1.5 days | Medium. lru_cache on `_token_digest` must be preserved (single global cache, not per-module). Numpy fast-path stays bit-identical. |
| **D-3** Extract `_pii_ml.py` (Presidio) | C5 | `detect_pii_ml`, `_get_presidio_analyzer`, `_require_presidio`, `_PRESIDIO_ENTITY_MAP`, `PII_ML_SEVERITY`, `PII_ML_TYPES` | All `patch("forgelm.data_audit._HAS_PRESIDIO", …)` calls in `test_phase12_5.py` rewritten to `patch("forgelm.data_audit._optional._HAS_PRESIDIO", …)`. lru_cache instances preserved; cache_clear teardown preserved. | 1 day | Medium-high. |
| **D-4** Extract `_aggregator.py` + `_splits.py` + `_croissant.py` + `_summary.py` | C14, C15, C17, C18, C19, C20, C21, C23 | `summarize_report`, `_StreamingAggregator`, `_SplitOutcome`, `_process_split`, `_resolve_input`, `_build_croissant_metadata`, `_build_pii_severity`, all `_*_summary_notes`, `_render_*` | No test changes. | 1 day | Low. |
| **D-5** Convert `data_audit.py` → `data_audit/_orchestrator.py`, finalise `__init__.py` | C16, C22 | `audit_dataset`, `_atomic_write_json`, `_cross_split_overlap`, `_pair_leak_payload` | New import-stability test (Section 1.g). | 0.5 day | Low — at this point `data_audit.py` is only the orchestrator + re-exports. |

Total effort: ~5 person-days. PRs D-1 and D-4 can run in parallel (no overlap). D-2 must precede D-3. D-5 must come last.

### 1.g Regression test strategy

**New file** `tests/test_data_audit_import_stability.py`:

```python
"""Pin every external import path so a future split refactor cannot break them."""
PUBLIC = [
    "AuditReport", "PII_TYPES", "PII_SEVERITY", "PII_SEVERITY_ORDER",
    "PII_ML_SEVERITY", "PII_ML_TYPES", "DEFAULT_NEAR_DUP_HAMMING",
    "DEFAULT_MINHASH_JACCARD", "DEFAULT_MINHASH_NUM_PERM", "DEDUP_METHODS",
    "SECRET_TYPES",
    "detect_pii", "mask_pii", "detect_pii_ml", "detect_secrets", "mask_secrets",
    "compute_simhash", "hamming_distance", "find_near_duplicates",
    "compute_minhash", "find_near_duplicates_minhash",
    "audit_dataset", "summarize_report",
]
PRIVATE_TEST_TOUCHED = [
    "_is_credit_card", "_is_tr_id", "_token_digest",
    "_find_near_duplicates_brute", "_count_leaked_rows",
    "_read_jsonl_split", "_strip_code_fences", "_row_quality_flags",
    "_require_presidio", "_get_presidio_analyzer", "_PRESIDIO_ENTITY_MAP",
]

def test_public_api_resolves():
    import forgelm.data_audit as m
    for name in PUBLIC + PRIVATE_TEST_TOUCHED:
        assert hasattr(m, name), f"forgelm.data_audit.{name} missing post-split"

def test_token_digest_lru_cache_singleton():
    import forgelm.data_audit as m
    from forgelm.data_audit import _simhash
    assert m._token_digest is _simhash._token_digest, \
        "lru_cache duplicated; cache_clear() will hit wrong instance"

def test_patchable_optional_sentinels_resolve():
    from forgelm.data_audit import _optional  # noqa: F401
    from forgelm.data_audit._optional import _HAS_PRESIDIO  # noqa: F401
```

The 47 existing test modules already enforce 800+ behavioural assertions that pin the audit's output JSON shape. **No existing test needs to change in PR D-1, D-4, or D-5.** PR D-2 and D-3 each rewrite ≤8 `patch(...)` strings in `test_phase12_5.py`/`test_data_audit.py` (concerted in the same PR).

### 1.h Risk register (data_audit)

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R-1 | `lru_cache` instance gets duplicated when `_token_digest` is moved to `_simhash.py` and re-imported from `__init__.py`; tests that exercise `_token_digest.cache_clear()` invalidate the wrong cache | Medium | Medium — slow tests + flaky cache hit-rate | The package-level re-export must be `from ._simhash import _token_digest` (binding alias, same object). Add an assertion in the import-stability test: `assert forgelm.data_audit._token_digest is forgelm.data_audit._simhash._token_digest`. |
| R-2 | `_HAS_PRESIDIO` patched at `forgelm.data_audit._HAS_PRESIDIO` no longer reaches the runtime check inside `_pii_ml.py` (it reads a stale snapshot via `from ._optional import _HAS_PRESIDIO`) | High if not handled | High — silent test-mode regressions | (a) Rewrite the runtime check to `from . import _optional` + `_optional._HAS_PRESIDIO` (attribute lookup picks up the patched value); (b) update test patches to the new dotted path. Both sides ship in the same PR (D-3). |
| R-3 | Circular import: `_pii_ml.py` ↔ `_optional.py` | Low | High | `_optional.py` imports stdlib only; no `data_audit.*` imports allowed. |
| R-4 | `_get_presidio_analyzer.cache_clear()` called in `_require_presidio` references the wrong `_get_presidio_analyzer` after it moves modules | Medium | Medium | Both functions live in `_pii_ml.py`; co-location removes the problem. |
| R-5 | Test fixture path `tests/test_data_audit*.py` enumerates expected public names; missing one breaks discovery | Low | Low | Import-stability test enumerates them explicitly. |
| R-6 | numpy fast-path silently changes precision after the move | Low | High | Pure relocation; existing fingerprint property tests catch drift. |
| R-7 | `from .data_audit import …` consumers (`forgelm/ingestion.py`, `forgelm/wizard.py`) break | Low | Critical | Package `__init__.py` re-exports → `from .data_audit import mask_pii` continues to resolve. |
| R-8 | `__all__` not declared on the new `__init__.py` → `import *` regressions | Low | Low | Add explicit `__all__` in `__init__.py`. |
| R-9 | `logger = logging.getLogger("forgelm.data_audit")` (line 42) used across files; sub-module loggers might break log filtering | Medium | Low | Sub-modules use `logging.getLogger(__name__)` which inherits via standard hierarchy. |
| R-10 | The `from forgelm import data_audit as audit_mod` pattern (test_data_audit.py:455, 595, 619) breaks | Low | Medium | Re-exports + import-stability test catch this. |

---

## 2. `cli.py` — Detailed analysis

### 2.a Concern inventory

| # | Concern | Lines | Public/exported | Internal helpers |
|---|---|---|---|---|
| 1 | Module header + exit codes | 1–33 | `EXIT_SUCCESS`, `EXIT_CONFIG_ERROR`, `EXIT_TRAINING_ERROR`, `EXIT_EVAL_FAILURE`, `EXIT_AWAITING_APPROVAL` | `_CLI_MODULE`, `_PUBLIC_EXIT_CODES`, `logger` |
| 2 | argparse type guards + version + logging setup | 36–98 | `_get_version`, `_setup_logging` | `_non_negative_int`, `_add_common_subparser_flags` |
| 3 | Subcommand registrar — `chat` | 101–128 | — | `_add_chat_subcommand` |
| 4 | Subcommand registrar — `export` | 131–162 | — | `_add_export_subcommand` |
| 5 | Subcommand registrar — `deploy` | 165–199 | — | `_add_deploy_subcommand` |
| 6 | Subcommand registrar — `quickstart` | 202–251 | — | `_add_quickstart_subcommand` |
| 7 | Subcommand registrar — `ingest` | 254–374 | — | `_add_ingest_subcommand` |
| 8 | Float type + audit registrar | 377–513 | — | `_non_negative_float`, `_add_audit_subcommand` |
| 9 | `parse_args` (root + dispatcher) | 516–636 | `parse_args` | — |
| 10 | Dry-run / fit-check / benchmark-only / merge / generate-data / compliance-export | 639–870 | `_run_dry_run`, `_run_fit_check`, `_run_compliance_export` | `_galore_dry_run_fields`, `_evaluation_dry_run_fields`, `_compliance_dry_run_fields`, `_build_dry_run_result`, `_run_benchmark_only`, `_run_merge`, `_run_generate_data` |
| 11 | Resume checkpoint + result envelope + log helpers | 872–999 | `_resolve_resume_checkpoint`, `_output_result` | `_build_result_json_envelope`, `_log_result_status`, `_log_cost_summary`, `_log_benchmark_summary` |
| 12 | Subcommand dispatchers — `chat`, `export`, `deploy` | 1001–1104 | — | `_run_chat_cmd`, `_run_export_cmd`, `_run_deploy_cmd` |
| 13 | Quickstart dispatcher (multi-step) | 1107–1318 | — | `_build_quickstart_inherited_flags`, `_emit_quickstart_list`, `_emit_quickstart_result`, `_run_quickstart_train_subprocess`, `_run_quickstart_chat_subprocess`, `_run_quickstart_train_then_chat`, `_run_quickstart_cmd`, `_load_quickstart_train_paths` |
| 14 | Ingest dispatcher | 1321–1391 | — | `_run_ingest_cmd` |
| 15 | Audit dispatcher | 1394–1526 | `_run_data_audit` | `_run_audit_cmd` |
| 16 | Top-level dispatcher + main() | 1529–1755 | `main` | `_dispatch_subcommand`, `_maybe_run_wizard`, `_load_config_or_exit`, `_apply_offline_flag`, `_maybe_run_no_train_mode`, `_report_training_error`, `_run_training_pipeline` |

### 2.b Public API surface

```python
# Constants
EXIT_SUCCESS: int = 0
EXIT_CONFIG_ERROR: int = 1
EXIT_TRAINING_ERROR: int = 2
EXIT_EVAL_FAILURE: int = 3
EXIT_AWAITING_APPROVAL: int = 4

# Functions
def parse_args() -> argparse.Namespace: ...
def main() -> None: ...

# Test-touched helpers (must remain importable at forgelm.cli.<name>)
_get_version, _setup_logging, _resolve_resume_checkpoint, _output_result,
_run_dry_run, _run_fit_check, _run_compliance_export, _run_data_audit,
_build_quickstart_inherited_flags
```

Entry point: `forgelm = "forgelm.cli:main"` in `pyproject.toml [project.scripts]`. **`forgelm.cli` must remain importable as a name and `main` must resolve at `forgelm.cli:main`.**

### 2.c Cross-coupling

Coupling is shallow — registrars and dispatchers form parallel chains both rooted at `parse_args`/`main`. Excellent split candidate.

### 2.d Proposed package layout

```text
forgelm/cli/
    __init__.py             # re-exports main + exit codes + the test-touched helpers
    __main__.py             # python -m forgelm.cli entry point (NEW — load-bearing for quickstart subprocess)
    _exit_codes.py          # EXIT_*, _PUBLIC_EXIT_CODES
    _argparse_types.py      # _non_negative_int, _non_negative_float, _add_common_subparser_flags
    _logging.py             # _setup_logging, _get_version, _CLI_MODULE
    _parser.py              # parse_args + epilog + every _add_*_subcommand registrar
    _dry_run.py             # _galore_*, _evaluation_*, _compliance_*, _build_dry_run_result, _run_dry_run
    _fit_check.py           # _run_fit_check
    _result.py              # _build_result_json_envelope, _log_result_status, _log_cost_summary,
                            #   _log_benchmark_summary, _output_result
    _resume.py              # _resolve_resume_checkpoint
    _no_train_modes.py      # _run_benchmark_only, _run_merge, _run_generate_data,
                            #   _run_compliance_export, _maybe_run_no_train_mode
    _wizard.py              # _maybe_run_wizard
    _config_load.py         # _load_config_or_exit, _apply_offline_flag
    _training.py            # _report_training_error, _run_training_pipeline
    subcommands/
        __init__.py         # empty
        _chat.py            # _run_chat_cmd
        _export.py          # _run_export_cmd
        _deploy.py          # _run_deploy_cmd
        _quickstart.py      # all 8 quickstart helpers
        _ingest.py          # _run_ingest_cmd
        _audit.py           # _run_data_audit, _run_audit_cmd
    _dispatch.py            # _dispatch_subcommand, main()
```

| File | Target lines |
|---|---|
| `__init__.py` | 50 |
| `__main__.py` | 5 |
| `_exit_codes.py` | 20 |
| `_argparse_types.py` | 60 |
| `_logging.py` | 40 |
| `_parser.py` | 580 |
| `_dry_run.py` | 90 |
| `_fit_check.py` | 30 |
| `_result.py` | 100 |
| `_resume.py` | 30 |
| `_no_train_modes.py` | 240 |
| `_wizard.py` | 20 |
| `_config_load.py` | 60 |
| `_training.py` | 80 |
| `subcommands/_chat.py` | 30 |
| `subcommands/_export.py` | 50 |
| `subcommands/_deploy.py` | 50 |
| `subcommands/_quickstart.py` | 220 |
| `subcommands/_ingest.py` | 80 |
| `subcommands/_audit.py` | 140 |
| `_dispatch.py` | 90 |

Total ≈ 2090. Largest file (`_parser.py`) at ~580 lines is acceptable: argparse registrar code is verbose but cohesive.

**Why `_parser.py` keeps all registrars:** the registrars are short (15–120 lines each), they share `_add_common_subparser_flags`, and `parse_args()` instantiates the parser once and adds them all. Fragmenting registrars into 6 separate files would force `_parser.py` to import each one and would not improve cohesion.

### 2.e Public-API preservation strategy

```python
# forgelm/cli/__init__.py — designed shape
"""Public re-exports for the ``forgelm`` CLI."""
from ._exit_codes import (
    EXIT_SUCCESS, EXIT_CONFIG_ERROR, EXIT_TRAINING_ERROR,
    EXIT_EVAL_FAILURE, EXIT_AWAITING_APPROVAL,
)
from ._logging import _setup_logging, _get_version
from ._parser import parse_args
from ._dry_run import _run_dry_run
from ._fit_check import _run_fit_check
from ._result import _output_result
from ._resume import _resolve_resume_checkpoint
from ._no_train_modes import _run_compliance_export
from .subcommands._audit import _run_data_audit
from .subcommands._quickstart import _build_quickstart_inherited_flags
from ._dispatch import main

__all__ = [
    "EXIT_SUCCESS", "EXIT_CONFIG_ERROR", "EXIT_TRAINING_ERROR",
    "EXIT_EVAL_FAILURE", "EXIT_AWAITING_APPROVAL",
    "parse_args", "main",
    "_get_version", "_setup_logging",
    "_resolve_resume_checkpoint", "_run_dry_run", "_run_fit_check",
    "_run_compliance_export", "_run_data_audit", "_output_result",
    "_build_quickstart_inherited_flags",
]
```

```python
# forgelm/cli/__main__.py — load-bearing for python -m forgelm.cli
from forgelm.cli import main

if __name__ == "__main__":
    main()
```

**Why `__main__.py` is load-bearing:** today `forgelm/cli.py` has `if __name__ == "__main__": main()` at its tail (line 1755). `python -m forgelm.cli` works because `cli.py` IS the module. After the split, `forgelm.cli` is a package; running `python -m forgelm.cli` then needs `forgelm/cli/__main__.py`. This is critical because `_run_quickstart_train_subprocess` (line 1191 in current `cli.py`) does `subprocess.run([sys.executable, "-m", _CLI_MODULE, ...])` where `_CLI_MODULE = "forgelm.cli"`. **Without `__main__.py`, the quickstart subprocess flow breaks silently.**

`pyproject.toml [project.scripts]` reads `forgelm = "forgelm.cli:main"`. Python resolves this against the `forgelm.cli` package's `__init__` namespace, where `main` is re-exported. **No pyproject change required.**

### 2.f Migration sequence (cli)

| PR + files extracted | Test impact | Effort | Risk |
|---|---|---|---|
| **C-1** Extract `_exit_codes.py`, `_logging.py`, `_argparse_types.py`, `_resume.py`, `_result.py` | Tests: no change (re-exports cover them). | 0.5 day | Very low |
| **C-2** Extract `_parser.py` (move all registrars + `parse_args`) | Tests: `--help` snapshot test (new — Section 4) MUST go green; `test_smoke.py::parse_args` import keeps working through `__init__.py`. | 1 day | Medium — registrars must be moved verbatim including help-string text to keep `--help` byte-identical. |
| **C-3** Extract `_dry_run.py`, `_fit_check.py`, `_no_train_modes.py`, `_wizard.py`, `_config_load.py`, `_training.py` | Tests: `_run_dry_run`, `_run_fit_check`, `_run_compliance_export` must still resolve at `forgelm.cli`. Re-exports cover this. | 0.75 day | Low |
| **C-4** Create `subcommands/` package and extract `_chat.py`, `_export.py`, `_deploy.py`, `_ingest.py` | No test changes. | 0.75 day | Low |
| **C-5** Extract `subcommands/_quickstart.py` and `subcommands/_audit.py` | Tests: re-exported through `__init__.py`. | 0.75 day | Medium — quickstart subprocess test (`test_quickstart_subprocess.py`) re-runs `python -m forgelm.cli`; the `__main__` shim must be added. |
| **C-6** Add `_dispatch.py` (final `main()` + `_dispatch_subcommand`), wire `__init__.py` final shape, add `forgelm/cli/__main__.py` | New: `tests/test_cli_import_stability.py`. | 0.5 day | Low |

Total: ~4.25 person-days. C-1 can run in parallel with **D-1** (no overlap). C-2 must precede C-4 and C-5. C-6 is last.

### 2.g CLI-specific acceptance signals

- `forgelm --help` byte-identical to v0.5.0.
- `forgelm audit --help`, `forgelm ingest --help`, `forgelm quickstart --help`, `forgelm chat --help`, `forgelm export --help`, `forgelm deploy --help` byte-identical.
- `forgelm --version` returns the installed version unchanged.
- `forgelm --config config_template.yaml --dry-run` exits 0.
- `python -m forgelm.cli --help` byte-identical (validates `__main__.py`).
- `forgelm --data-audit path/` (legacy alias) still emits the deprecation warning and runs.
- Exit codes 0/1/2/3/4 unchanged on every error path covered by `tests/test_cli*.py` (the data-audit CLI split did not introduce a new code; `EXIT_WIZARD_CANCELLED = 5` was added separately in review-cycle 2 for the interactive wizard surface and is not reachable from `forgelm audit`).

### 2.h Lazy-import discipline (orthogonal but pinned by C-4 / C-5)

The current file already defers heavy imports — `from .chat import run_chat` is inside `_run_chat_cmd` (line 1004), `from .data_audit import audit_dataset` is inside `_run_data_audit` (line 1416), `from .data import prepare_dataset` is inside `_run_training_pipeline` (line 1666). **The split must preserve these inline imports as-is.** Specifically:

- `subcommands/_chat.py`: keep `from ..chat import run_chat` inline inside `_run_chat_cmd` (NOT at module top).
- `subcommands/_audit.py`: keep `from ..data_audit import audit_dataset, summarize_report, DEFAULT_*` inline inside `_run_data_audit`.
- `_training.py`: keep `from ..data import prepare_dataset`, `from ..model import get_model_and_tokenizer`, `from ..trainer import ForgeTrainer` inline.
- `_no_train_modes.py`: `_run_benchmark_only` already imports `.benchmark` and `.model` inline; preserve that.

A pre-commit linter that fails on top-level torch imports inside `forgelm/cli/**` would be the natural enforcement.

### 2.i Risk register (cli)

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R-11 | `python -m forgelm.cli` breaks because `cli/` is now a package without `__main__.py` | High if not handled | Critical — quickstart subprocess flow dies silently | Ship `forgelm/cli/__main__.py` in PR C-6. Add a smoke test invoking `subprocess.run([sys.executable, "-m", "forgelm.cli", "--help"])`. |
| R-12 | `forgelm` console script breaks because `main` no longer resolves at `forgelm.cli:main` | Low — re-export handles it | Critical | Verified: `forgelm.cli` package's `__init__.py` re-exports `main`. Pin via the import-stability test. |
| R-13 | `--help` text shifts because some registrar moves change argparse's argument-add order | Medium | Low — surface only | Move registrars verbatim. Snapshot test compares `forgelm --help`, every `forgelm <sub> --help`. |
| R-14 | argparse `default=argparse.SUPPRESS` semantics change subtly | Low | Medium | Move registrars verbatim, no logic edits in C-2. |
| R-15 | `_PUBLIC_EXIT_CODES` referenced in `_run_quickstart_train_subprocess`; after split the import must come from `_exit_codes.py` | Low | Low | Same-PR fix: `subcommands/_quickstart.py` does `from .._exit_codes import _PUBLIC_EXIT_CODES`. |
| R-16 | Tests doing `with patch("forgelm.cli._run_data_audit", …)` succeed only if `_run_data_audit` is bound at the package level | Low | Low | Re-export covers the import. |
| R-17 | Heavy imports leak into `forgelm.cli.__init__` because `__init__.py` eagerly resolves every dispatcher (`from .subcommands._audit import _run_data_audit`), which in turn would import `audit_dataset` if the dispatcher's lazy-import discipline is not maintained | Medium | High — `forgelm --help` becomes slow | The `_run_data_audit` function definition does not require `audit_dataset` to be importable at definition time. `subcommands/_audit.py` must keep the `audit_dataset` import inside the function body. |
| R-18 | Logger naming drifts: today `logger = logging.getLogger(_CLI_MODULE)` where `_CLI_MODULE = "forgelm.cli"`. After split each new submodule should keep using the same logger | Low | Low | All sub-modules use `logger = logging.getLogger("forgelm.cli")` explicitly. |
| R-19 | The deprecated `--data-audit` flag is parsed by the **top-level** parser but dispatched by `main()` after `_dispatch_subcommand` returns. Preserving this ordering is essential | Medium | Medium | The diff for `_dispatch.py` carries `main()` verbatim, only updating relative imports. Pin with a regression test that calls `main(["--data-audit", "path"])`. |

---

## 3. Combined PR sequence (data_audit + cli)

Eleven PRs total — five parallelisable, six sequential.

```text
Week 1 (parallel where possible):
    Day 1: D-1 (data_audit leaf concerns)        || C-1 (cli leaf concerns)
    Day 2: D-2 (simhash/minhash + types)         || C-2 (cli parser package extraction)
    Day 3: D-3 (Presidio + test patch updates)
    Day 4: D-4 (aggregator/croissant/summary)    || C-3 (cli no-train mode helpers)
    Day 5: D-5 (orchestrator + finalize)         || C-4 (cli subcommands chat/export/deploy/ingest)

Week 2:
    Day 6: C-5 (cli quickstart + audit subcommands)
    Day 7: C-6 (cli __main__.py + final dispatch + import-stability test)
    Day 8: integration test pass + release candidate
```

**Parallel-safe pairs:** {D-1, C-1}, {D-2, C-2}, {D-4, C-3}, {D-5, C-4}.

**Sequential constraints:**
- D-2 must precede D-3 (Presidio uses `_optional`).
- D-1, D-2, D-3, D-4 must precede D-5.
- C-2 must precede C-4, C-5.
- C-1 through C-5 all precede C-6.

**Recommended branch strategy:** stack each PR onto the previous one. Use `git rebase --onto` if main moves. Do NOT batch into a single "big bang" PR — that erases the bisectability that motivates the split.

Alternative: collapse D-1+D-4 into a single 1.5-day "low-coupling extraction" PR, and collapse C-1+C-3 into a single 1-day "leaf helpers" PR; this brings the count to 9 without losing reviewability.

---

## 4. Acceptance criteria (per PR)

A PR in either series is "accepted" when ALL of the following hold:

1. **Existing tests green:** `pytest tests/` exits 0 across all 47 modules and 800+ tests. No `xfail` introduced.
2. **Coverage gate:** `pytest --cov=forgelm` reports `total ≥ 40 %`.
3. **`--help` byte-identical:** `forgelm --help`, `forgelm audit --help`, `forgelm ingest --help`, `forgelm quickstart --help`, `forgelm chat --help`, `forgelm export --help`, `forgelm deploy --help` all match the v0.5.0 snapshot. The CI gate captures the strings to a fixture and `diff`s.
4. **Import-stability test green:** every PUBLIC name and every PRIVATE-test-touched name resolves at its canonical dotted path.
5. **Patch-path test green:** `patch("forgelm.data_audit._optional._HAS_PRESIDIO", False)` and any other rewired patch paths fire.
6. **`forgelm --version` works.**
7. **`forgelm --config config_template.yaml --dry-run` exits 0** with the same JSON envelope shape as v0.5.0.
8. **`python -m forgelm.cli --help` works** (validates `__main__.py` once C-6 lands).
9. **Quickstart subprocess flow works:** `_run_quickstart_train_subprocess` spawns `python -m forgelm.cli ...` successfully — covered by `test_quickstart_subprocess.py`.
10. **ruff format + ruff check clean.**
11. **No new top-level imports of torch / transformers / unsloth / deepspeed / lm_eval inside `forgelm/cli/**` or `forgelm/data_audit/**`.**
12. **Logger names unchanged.**
13. **Public exit codes preserved:** `EXIT_SUCCESS == 0`, `EXIT_CONFIG_ERROR == 1`, `EXIT_TRAINING_ERROR == 2`, `EXIT_EVAL_FAILURE == 3`, `EXIT_AWAITING_APPROVAL == 4`, `EXIT_WIZARD_CANCELLED == 5`, `_PUBLIC_EXIT_CODES == frozenset({0,1,2,3,4,5})`.

---

## 5. Effort and risk roll-up

| Series | PRs | Total effort | Cumulative risk profile |
|---|---|---|---|
| `data_audit` (D-1 → D-5) | 5 | ~5.0 days | Mid (R-1, R-2, R-7 are real but each tractable) |
| `cli` (C-1 → C-6) | 6 | ~4.25 days | Low-mid (R-11 is the one critical item) |
| Integration + release validation | — | ~1.0 day | Low |
| **Combined** | **11** | **~10.25 person-days** | **Mid overall**; bisectable per-PR. |

If a single engineer does the work end-to-end with no parallelisation, plan ~14 calendar days including review cycles and CI iteration. With two engineers running parallel-safe pairs, ~7–8 calendar days.

---

## 6. Out-of-scope (called out explicitly)

These are NOT addressed by the split design:

- **Performance changes:** the simhash numpy fast-path, the Presidio lru_cache, and the streaming JSONL reader stay unmodified. Phase 12 perf work is separate.
- **Behaviour changes:** no flag added, no flag removed, no exit code repurposed. The `--data-audit` deprecation alias stays exactly as-is until v0.7.0.
- **`verify-audit` subcommand (master review F-compliance-103):** treated as a feature PR landing on top of the split, not a prerequisite. The `subcommands/` package layout reserves the file slot but does not implement it.
- **Documentation:** bilingual `docs/reference/` updates would only be needed if the import path changed. Re-exports keep `forgelm.data_audit` and `forgelm.cli` byte-compatible. The `docs/standards/architecture.md` table (line 94) keeps listing `data_audit.py` until the split lands; PR D-5 updates the line to read `data_audit/ (package)`.

---

*End of split design — 202604300906.*
