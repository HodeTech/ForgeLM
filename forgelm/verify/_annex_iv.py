"""Annex IV technical-documentation artefact verification (EU AI Act).

Owns :class:`VerifyAnnexIVResult`, the §1-9 required-field table, the
canonical manifest-hash recomputation and the integrity-vs-input
classification predicate.  Imported by :mod:`._pipeline_evidence`, which
runs this verifier once per completed pipeline stage.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

# EU AI Act Annex IV §1-9 — the nine required categories every
# high-risk-system technical-documentation file must carry.  We map
# each category to the JSON keys we expect at the top level of the
# artifact (a small subset matches `compliance.py`'s emit shape).
#
# NOTE: this is a *minimum* set; richer artefacts may add more keys.
# The check fails when a required key is missing OR when its value is
# the empty string / empty dict / empty list (operator likely forgot
# to populate it from the auto-generation template).
#
# Identity-critical §1 sub-fields that must themselves be non-empty.
# Without this the top-level container check is satisfied by a
# ``system_identification`` dict whose every value is a blank
# placeholder — an Annex IV file with no provider identity and no
# system name would pass the completeness gate (F-P4-OPUS-17).
_SYSTEM_IDENTIFICATION_REQUIRED_SUBKEYS: Tuple[str, ...] = (
    "provider_name",
    "system_name",
    "intended_purpose",
)
_ANNEX_IV_REQUIRED_FIELDS: Tuple[Tuple[str, str], ...] = (
    ("system_identification", "Annex IV §1 — system identification (name, version, provider, intended_purpose)"),
    ("intended_purpose", "Annex IV §1 — intended purpose statement"),
    ("system_components", "Annex IV §2 — software / hardware components + supplier list"),
    ("computational_resources", "Annex IV §2(g) — compute resources used during training"),
    ("data_governance", "Annex IV §2(d) — data sources, governance, validation methodology"),
    ("technical_documentation", "Annex IV §3-5 — design + development methodology"),
    ("monitoring_and_logging", "Annex IV §6 — post-market monitoring + audit-log presence"),
    ("performance_metrics", "Annex IV §7 — accuracy / robustness / cybersecurity metrics"),
    ("risk_management", "Annex IV §9 — risk management system reference (Art. 9 alignment)"),
)


class VerifyAnnexIVResult:
    """Structured result of an Annex IV artifact verification.

    Mirrors ``forgelm.compliance.VerifyResult`` (used by verify-audit)
    so integrators get a uniform shape across the verification toolbelt.
    """

    __slots__ = ("valid", "reason", "missing_fields", "manifest_hash_actual", "manifest_hash_expected")

    def __init__(
        self,
        *,
        valid: bool,
        reason: str = "",
        missing_fields: List[str] | None = None,
        manifest_hash_actual: str = "",
        manifest_hash_expected: str = "",
    ) -> None:
        self.valid = valid
        self.reason = reason
        self.missing_fields = list(missing_fields or [])
        self.manifest_hash_actual = manifest_hash_actual
        self.manifest_hash_expected = manifest_hash_expected

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid": self.valid,
            "reason": self.reason,
            "missing_fields": list(self.missing_fields),
            "manifest_hash_actual": self.manifest_hash_actual,
            "manifest_hash_expected": self.manifest_hash_expected,
        }


def _is_field_populated(value: Any) -> bool:
    """Return ``True`` when the operator clearly populated the field.

    Empty string / empty list / empty dict / ``None`` count as "the
    operator forgot" (a placeholder still in the auto-generation
    template), not "the operator chose to leave it empty".
    """
    if value is None:
        return False
    if isinstance(value, (str, list, dict)) and len(value) == 0:
        return False
    return True


def verify_annex_iv_artifact(path: str) -> VerifyAnnexIVResult:
    """Library entry: verify an Annex IV JSON file's completeness + manifest hash.

    Used by ``forgelm verify-annex-iv`` and exposed for integrators via
    the package facade.  Returns a structured result; never raises on
    documented failure modes (the caller decides which exit code the
    result class maps to).  Raises :class:`OSError` for genuine I/O
    failures on an existing file (dispatcher → ``EXIT_TRAINING_ERROR``)
    and :class:`json.JSONDecodeError` for parse failures (dispatcher →
    ``EXIT_CONFIG_ERROR`` since malformed JSON is a caller-input error).
    """
    with open(path, "r", encoding="utf-8") as fh:
        # Uncapped, deliberately and temporarily: a byte cap here changes a
        # verdict (a large-but-valid artefact would start failing), and S1 is
        # behaviour-neutral by contract. ``_io_safety._read_capped_json`` is the
        # primitive to switch to; tracked as F-W20260729-VERIFY-UNCAPPED-READ
        # against S5 in docs/roadmap/risks-and-decisions.md.
        artifact = json.load(fh)
    return verify_annex_iv_payload(artifact)


# Split out of ``verify_annex_iv_artifact`` so a caller holding an already-open
# file can reuse the verdict logic without a second ``open()``.  The chain
# verifier needs exactly that: it enforces a byte cap, and a cap that stats one
# handle then opens another is not a cap at all.  See ``_read_capped_json``.
def verify_annex_iv_payload(artifact: Any) -> VerifyAnnexIVResult:
    """Verify an already-parsed Annex IV artefact."""
    if not isinstance(artifact, dict):
        return VerifyAnnexIVResult(
            valid=False,
            reason=f"Artifact root is {type(artifact).__name__}, expected JSON object.",
        )

    # Required fields: walk the static catalog so a future schema
    # addition is one row in the tuple, not a code edit at every
    # call site.
    missing: List[str] = []
    for key, _description in _ANNEX_IV_REQUIRED_FIELDS:
        if not _is_field_populated(artifact.get(key)):
            missing.append(key)
    # Deepen §1: the system_identification container is non-empty as long
    # as it carries the 6 fixed keys, but a dict of all-blank placeholders
    # is exactly "the operator forgot".  Require the identity-critical
    # sub-fields to be populated too (F-P4-OPUS-17).
    sys_ident = artifact.get("system_identification")
    if "system_identification" not in missing:
        if not isinstance(sys_ident, dict):
            # A non-dict value (string, list, number) passes the bare
            # populated-check above but cannot carry the §1 identity
            # sub-fields — it bypasses the whole identity gate.  Reject it
            # rather than silently skipping the sub-field checks.
            missing.append("system_identification")
        else:
            for subkey in _SYSTEM_IDENTIFICATION_REQUIRED_SUBKEYS:
                if not _is_field_populated(sys_ident.get(subkey)):
                    missing.append(f"system_identification.{subkey}")
    if missing:
        return VerifyAnnexIVResult(
            valid=False,
            reason=f"Missing or empty required Annex IV field(s): {', '.join(missing)}.",
            missing_fields=missing,
        )

    # Manifest hash recompute (tampering detection).  When the artifact
    # carries `metadata.manifest_hash` we recompute SHA-256 over the
    # canonical-JSON representation of the artifact MINUS the metadata
    # block (which itself contains the hash) and compare.
    metadata = artifact.get("metadata") if isinstance(artifact.get("metadata"), dict) else None
    expected = metadata.get("manifest_hash") if metadata else None
    if expected:
        matches, actual, legacy = _match_manifest_hash(artifact, expected)
        if not matches:
            return VerifyAnnexIVResult(
                valid=False,
                reason="Manifest hash mismatch — artifact may have been modified after generation.",
                manifest_hash_actual=actual,
                manifest_hash_expected=expected,
            )
        return VerifyAnnexIVResult(
            valid=True,
            reason="All Annex IV §1-9 fields populated; manifest hash matches."
            + (
                " (Stamped before non-finite values were written as strings; re-export to refresh the digest.)"
                if legacy
                else ""
            ),
            manifest_hash_actual=actual,
            manifest_hash_expected=expected,
        )

    # No manifest hash present — the field-completeness check is the
    # only signal we can give.  Pass with a note so the operator knows.
    return VerifyAnnexIVResult(
        valid=True,
        reason="All Annex IV §1-9 fields populated; no manifest_hash present so tampering detection skipped.",
    )


def _match_manifest_hash(artifact: Dict[str, Any], expected: str) -> "tuple[bool, str, bool]":
    """``(matches, digest, matched_legacy_encoding)`` — see ``match_annex_iv_manifest_hash``."""
    from forgelm.compliance import match_annex_iv_manifest_hash

    return match_annex_iv_manifest_hash(artifact, expected)


def _compute_manifest_hash(artifact: Dict[str, Any]) -> str:
    """Recompute the manifest hash the same way ``compliance.py`` writes it.

    Delegates to :func:`forgelm.compliance.compute_annex_iv_manifest_hash`
    so the writer + verifier canonicalisation cannot drift byte-for-byte.
    Wave 2b Round-4 review F-W2B-05 fix: the previous local
    implementation duplicated the canonicalisation logic; if the writer
    ever changed (added a new metadata key, switched separators, etc.)
    legitimate artefacts would fail their own verifier.
    """
    from forgelm.compliance import compute_annex_iv_manifest_hash

    return compute_annex_iv_manifest_hash(artifact)


def is_annex_iv_integrity_failure(result: VerifyAnnexIVResult) -> bool:
    """Return ``True`` when an Annex IV result failed **tamper detection**.

    Distinguishes the two ways ``verify_annex_iv_artifact`` reports
    ``valid=False``:

    - **Integrity failure** (``True`` → exit 6): every required §1-9
      field was populated, the artefact carried a ``metadata.manifest_hash``,
      and the recomputed hash disagreed with it.  The document was edited
      after generation.
    - **Input error** (``False`` → exit 1): required fields missing or
      still holding template placeholders, or a root that is not a JSON
      object.  The operator has to go and populate the artefact; nothing
      was tampered with.

    Keyed off the typed fields (``missing_fields``, the two hash strings)
    rather than ``reason`` prose so rewording an operator message cannot
    move an artefact between exit codes.
    """
    return (
        not result.valid
        and not result.missing_fields
        and bool(result.manifest_hash_expected)
        and result.manifest_hash_actual != result.manifest_hash_expected
    )
