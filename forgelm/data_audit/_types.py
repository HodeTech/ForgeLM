"""Public types and constant tables shared across the data_audit package.

Keeping these in one place gives every sub-module a single import site
for the audit's vocabulary (PII categories, severity tiers, dedup method
names, near-duplicate defaults) and gives the package facade a clean
re-export source.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


PII_TYPES: Tuple[str, ...] = ("email", "phone", "credit_card", "iban", "tr_id", "de_id", "fr_ssn", "us_ssn")


# Phase 11.5: PII severity grading. A flat ``{type: count}`` map gives zero
# guidance to a compliance reviewer staring at the audit JSON — they have to
# remember by hand that ``credit_card`` is materially worse than ``phone``.
# The tiers below encode the consensus regulatory weighting (PCI-DSS for
# financial; GDPR Art. 9 + ENISA for identifiers) so the audit surfaces a
# one-glance verdict via :data:`PII_SEVERITY`. Tweak the table when local
# law disagrees — the audit honours whatever map is loaded at call time.
PII_SEVERITY: Dict[str, str] = {
    # Financial / fully reversible identity theft -> highest weight.
    "credit_card": "critical",
    "iban": "critical",
    # Government-issued identifiers -> high. Tied to a specific person and
    # often re-used across systems; leakage is materially harder to undo
    # than a phone or email.
    "tr_id": "high",
    "de_id": "high",
    "fr_ssn": "high",
    "us_ssn": "high",
    # Direct contact identifiers -> medium. Routinely collected, but
    # leakage enables phishing / social engineering at scale.
    "email": "medium",
    # Phone numbers -> low. Anchored regex (see ``_PII_PATTERNS``) keeps
    # recall conservative; many spans are operational metadata that is
    # not actually personally identifying.
    "phone": "low",
}


PII_SEVERITY_ORDER: Tuple[str, ...] = ("critical", "high", "medium", "low")
"""Display order — most-severe first so the operator-facing summary leads
with the worst-case findings rather than burying them behind low tiers."""


# Phase 12.5: severity for the ML-NER detector (Presidio adapter).  These
# categories sit alongside the regex set and feed the same pii_severity
# block, which is why they share the ``critical/high/medium/low`` tier
# vocabulary.  Entries are deliberately on the lower end of the tier
# table because NER false-positive rates are materially higher than the
# regex-anchored detectors (a model name, a city, or an organization
# string can be flagged with no privacy impact); compliance reviewers
# can still upgrade the tier locally if their corpus warrants it.
PII_ML_SEVERITY: Dict[str, str] = {
    "person": "medium",
    "organization": "low",
    "location": "low",
}


PII_ML_TYPES: Tuple[str, ...] = tuple(PII_ML_SEVERITY.keys())
"""Canonical category names for ML-detected PII.  Disjoint from
:data:`PII_TYPES` (regex categories) so the two detectors can run side
by side without double-counting the same span."""


# Columns we treat as text payloads when computing length / language / dedup.
# Order matters: first match wins per row.
_TEXT_COLUMNS: Tuple[str, ...] = ("text", "content", "completion", "prompt")


# Instruction-tuning / chat shapes the rest of the codebase emits
# (``forgelm.synthetic.SyntheticDataGenerator._format_entry`` and the loaders
# in ``forgelm.data``). Each tuple is ``(user_half, assistant_half)``; when
# BOTH keys are present on a row we join the halves so the audit scans the
# teacher-generated response — the text most likely to carry memorised
# PII/credentials — rather than dropping it (F-P6-OPUS-07). Without this the
# ``instruction``/``chatml`` synthetic shapes extract to ``""`` (counted as
# ``null_or_empty``, never scanned) and ``prompt_response`` loses its
# ``response`` half.
_INSTRUCTION_PAIRS: Tuple[Tuple[str, str], ...] = (
    ("instruction", "output"),
    ("User", "Assistant"),
    ("prompt", "response"),
)


# Default Hamming-distance threshold for "near-duplicate" via 64-bit simhash.
# 3 bits ~ ~95% similarity at 64-bit width — same threshold the simhash paper
# uses for the canonical web-page-dedup deployment.
DEFAULT_NEAR_DUP_HAMMING: int = 3


# Phase 12: defaults for the optional MinHash LSH dedup path.
DEFAULT_MINHASH_JACCARD: float = 0.85
"""Jaccard-similarity threshold for MinHash LSH. ``0.85`` mirrors the
``threshold=3 -> ~95 %`` simhash default in spirit — the two approaches
report the same relative class of near-duplicates on real corpora,
within MinHash's permutation noise."""

DEFAULT_MINHASH_NUM_PERM: int = 128
"""Permutation count for ``datasketch.MinHash``. ``128`` is the
``datasketch`` default and the standard balance: enough hash functions
for stable Jaccard estimates, low enough that per-row cost stays in
the same ballpark as simhash."""

DEDUP_METHODS: Tuple[str, ...] = ("simhash", "minhash")
"""Allowed values for ``audit_dataset(..., dedup_method=...)`` and
``forgelm audit --dedup-method ...``. ``simhash`` is the default and
the only method that does not require an optional dependency."""


@dataclass
class AuditReport:
    """Structured audit outcome — JSON-serializable via :func:`asdict`.

    Both :attr:`source_path` (absolute, for traceability) and
    :attr:`source_input` (the literal string the operator passed in) are
    captured: absolute paths are useful for forensic correlation but
    leak the auditor's local filesystem layout, so consumers that need
    portability across machines should prefer :attr:`source_input`.
    """

    generated_at: str
    source_path: str
    source_input: str
    total_samples: int
    splits: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    cross_split_overlap: Dict[str, Any] = field(default_factory=dict)
    pii_summary: Dict[str, int] = field(default_factory=dict)
    pii_severity: Dict[str, Any] = field(default_factory=dict)
    near_duplicate_summary: Dict[str, Any] = field(default_factory=dict)
    # Phase 12: aggregate counts for code/credential leakage and (opt-in)
    # heuristic quality flags. Both are additive — older consumers that
    # ignored them keep working byte-identically; the fields stay empty
    # dicts on default audits with no findings.
    secrets_summary: Dict[str, int] = field(default_factory=dict)
    quality_summary: Dict[str, Any] = field(default_factory=dict)
    # Phase 12.5: optional Google Croissant 1.0 dataset card. Empty dict
    # by default so existing consumers see byte-identical output; set
    # via ``audit_dataset(..., emit_croissant=True)`` or ``--croissant``.
    croissant: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)


__all__ = [
    "PII_TYPES",
    "PII_SEVERITY",
    "PII_SEVERITY_ORDER",
    "PII_ML_SEVERITY",
    "PII_ML_TYPES",
    "_TEXT_COLUMNS",
    "_INSTRUCTION_PAIRS",
    "DEFAULT_NEAR_DUP_HAMMING",
    "DEFAULT_MINHASH_JACCARD",
    "DEFAULT_MINHASH_NUM_PERM",
    "DEDUP_METHODS",
    "AuditReport",
]
