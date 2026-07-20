"""Dataset quality and governance audit — feeds EU AI Act Article 10 reporting.

Phase 11 (Data Audit) — analyzes a JSONL dataset and produces a
``data_audit_report.json`` covering:

* Sample count per split + column schema
* Text length distribution (min / max / mean / p50 / p95)
* Top-3 language detection (best-effort; ``langdetect`` optional)
* Near-duplicate rate via 64-bit simhash + Hamming distance (default) or
  ``datasketch`` MinHash LSH (opt-in via ``[ingestion-scale]``)
* Cross-split overlap (train <-> validation <-> test) — guards against
  silent train-test leakage that destroys benchmark fidelity
* Null / empty rate per text-bearing column
* PII flag counts via regex (emails, phones, credit cards, IBAN,
  national IDs for TR / DE / FR / US-SSN) plus optional Presidio ML-NER
  for unstructured identifiers (person / organization / location)
* Credential / secret leakage (AWS, GitHub, Slack, OpenAI, etc.)
* Optional Gopher / C4 / RefinedWeb-style heuristic quality filter
* Optional Google Croissant 1.0 dataset-card emission

Public API: re-exported below from focused sub-modules. The package layout
matches the cohesion ceiling in :doc:`docs/standards/architecture` — each
sub-module owns one concern, with this facade preserving the historical
``forgelm.data_audit.X`` import surface so external callers (and the
test suite, which patches private names by dotted path) keep working.

The same PII/secret helpers (``mask_pii`` / ``mask_secrets``) are reused
by :mod:`forgelm.ingestion` for the optional ``--pii-mask`` /
``--secrets-mask`` flags.
"""

from __future__ import annotations

# Sub-module aliases — exposed so tests and consumers that previously
# reached for ``forgelm.data_audit._optional`` (via the package)
# continue to resolve cleanly.
from . import (
    _aggregator,  # noqa: F401 — re-export for tests
    _croissant,  # noqa: F401 — re-export for tests
    _minhash,  # noqa: F401 — re-export for tests
    _optional,  # noqa: F401 — re-export for tests
    _orchestrator,  # noqa: F401 — re-export for tests
    _pii_ml,  # noqa: F401 — re-export for tests
    _pii_regex,  # noqa: F401 — re-export for tests
    _quality,  # noqa: F401 — re-export for tests
    _secrets,  # noqa: F401 — re-export for tests
    _simhash,  # noqa: F401 — re-export for tests
    _splits,  # noqa: F401 — re-export for tests
    _streaming,  # noqa: F401 — re-export for tests
    _summary,  # noqa: F401 — re-export for tests
    _types,  # noqa: F401 — re-export for tests
)

# MinHash LSH (optional ``datasketch`` extra).
from ._minhash import (
    _require_datasketch,  # noqa: F401 — re-export for tests
    compute_minhash,
    find_near_duplicates_minhash,
)

# Optional-deps sentinels — tests patch these names by dotted path.
# The runtime guards inside ``_pii_ml.py`` / ``_minhash.py`` /
# ``_simhash.py`` read via attribute lookup (``_optional._HAS_X``) so
# the live module value drives behaviour. The package-level rebind
# below preserves the historical import path for static reads.
from ._optional import (
    _HAS_DATASKETCH,  # noqa: F401 — re-export for tests
    _HAS_NUMPY,  # noqa: F401 — re-export for tests
    _HAS_PRESIDIO,  # noqa: F401 — re-export for tests
    _HAS_XXHASH,  # noqa: F401 — re-export for tests
    _MinHash,  # noqa: F401 — re-export for tests
    _MinHashLSH,  # noqa: F401 — re-export for tests
    _np,  # noqa: F401 — re-export for tests
    _PresidioAnalyzer,  # noqa: F401 — re-export for tests
    _xxhash,  # noqa: F401 — re-export for tests
)

# Orchestrator (audit_dataset) + summary renderer.
from ._orchestrator import (
    _atomic_write_json,  # noqa: F401 — re-export for tests
    audit_dataset,
)

# PII ML (Presidio) — public detector plus the require/preflight + entity map.
from ._pii_ml import (
    _PRESIDIO_ENTITY_MAP,  # noqa: F401 — re-export for tests
    _get_presidio_analyzer,  # noqa: F401 — re-export for tests
    _require_presidio,  # noqa: F401 — re-export for tests
    detect_pii_ml,
)

# PII regex — public detector + masker plus the test-touched validators.
from ._pii_regex import (
    _is_credit_card,  # noqa: F401 — re-export for tests
    _is_tr_id,  # noqa: F401 — re-export for tests
    detect_pii,
    mask_pii,
)

# Quality + streaming primitives reached by tests.
from ._quality import (
    _row_quality_flags,  # noqa: F401 — re-export for tests
    _strip_code_fences,  # noqa: F401 — re-export for tests
)

# Secrets / credential leakage detection.
from ._secrets import (
    SECRET_TYPES,
    detect_secrets,
    mask_secrets,
)

# Simhash + LSH banding + cross-split simhash leakage.
from ._simhash import (
    _count_leaked_rows,  # noqa: F401 — re-export for tests
    _find_near_duplicates_brute,  # noqa: F401 — re-export for tests
    _token_digest,  # noqa: F401 — re-export for tests
    compute_simhash,
    find_near_duplicates,
    hamming_distance,
)
from ._streaming import _read_jsonl_split  # noqa: F401 — re-export for tests

# Summary renderer.
from ._summary import (
    _build_pii_severity,  # noqa: F401 — re-export for tests
    secrets_gate_verdict,
    summarize_report,
)

# Public types and constants.
from ._types import (
    DEDUP_METHODS,
    DEFAULT_MINHASH_JACCARD,
    DEFAULT_MINHASH_NUM_PERM,
    DEFAULT_NEAR_DUP_HAMMING,
    PII_ML_SEVERITY,
    PII_ML_TYPES,
    PII_SEVERITY,
    PII_SEVERITY_ORDER,
    PII_TYPES,
    AuditReport,
)

__all__ = [
    # Public types
    "AuditReport",
    # Constants
    "PII_TYPES",
    "PII_SEVERITY",
    "PII_SEVERITY_ORDER",
    "PII_ML_SEVERITY",
    "PII_ML_TYPES",
    "DEFAULT_NEAR_DUP_HAMMING",
    "DEFAULT_MINHASH_JACCARD",
    "DEFAULT_MINHASH_NUM_PERM",
    "DEDUP_METHODS",
    "SECRET_TYPES",
    # Public functions
    "detect_pii",
    "mask_pii",
    "detect_pii_ml",
    "detect_secrets",
    "mask_secrets",
    "compute_simhash",
    "hamming_distance",
    "find_near_duplicates",
    "compute_minhash",
    "find_near_duplicates_minhash",
    "audit_dataset",
    "summarize_report",
    "secrets_gate_verdict",
]
