"""Artifact verification primitives (Annex IV / GGUF / model integrity).

This module owns the *library* half of ForgeLM's verification toolbelt:
the functions integrators call from their own pipelines, and the
structured result types those functions return.

Why this module exists
----------------------

``verify_annex_iv_artifact``, ``verify_gguf`` and ``verify_integrity``
are declared **stable-tier** public API in :mod:`forgelm` (they appear in
``forgelm.__all__`` and carry ``"stable"`` in ``forgelm._STABILITY_TIERS``),
but their implementations used to live in
``forgelm/cli/subcommands/_verify_{annex_iv,gguf,integrity}.py`` — a
doubly-private location (a ``cli.subcommands`` package *and* an
underscore-prefixed module).  Two problems followed:

1. A library consumer calling a stable symbol dragged the whole CLI
   layer (argparse wiring, logging setup, sibling subcommands) into the
   import graph.
2. ``docs/standards/architecture.md`` §5 ("CLI is a thin shim") says CLI
   modules parse args, load config and dispatch — "business logic in any
   ``cli/`` module is a bug".  A SHA-256 re-hashing walk over a model
   directory is business logic.

The CLI subcommands are now thin wrappers: they parse arguments, call
into this module, format the result (text or JSON envelope) and map the
outcome onto the public exit-code contract.

``verify_audit_log`` deliberately stays in :mod:`forgelm.compliance`
-------------------------------------------------------------------

It is not moved here for symmetry, because the two are not symmetric:

- The three primitives in this module verify artefacts whose writers are
  *elsewhere* (a JSON document, a llama.cpp GGUF file, a directory of
  model weights).  ``verify_audit_log`` verifies ``compliance.py``'s own
  append-only on-disk format and must mirror
  :meth:`forgelm.compliance.AuditLogger.log_event`'s canonicalisation
  byte-for-byte.  Separating the writer from its verifier is exactly the
  drift hazard that F-W2B-05 fixed on the Annex IV side (a duplicated
  canonicalisation made legitimate artefacts fail their own verifier).
- ``architecture.md``'s module-ownership table already assigns the audit
  log to ``compliance.py``.
- There is no API benefit: ``forgelm.verify_audit_log`` already resolves
  from a public, non-CLI module, so the defect this extraction fixes
  does not apply to it.

Exit-code classification helpers
--------------------------------

Each verifier returns ``valid=False`` for two very different families of
reason, and CI/CD needs to tell them apart (see
``forgelm/cli/_exit_codes.py``):

- **The artefact was read fine but failed its integrity check** — hash
  mismatch, tampered manifest, checksum mismatch, corrupt GGUF metadata
  block.  This is a security event → ``EXIT_INTEGRITY_FAILURE`` (6).
- **The artefact could not be used as input at all** — required Annex IV
  fields never populated, manifest that is not a list, sidecar that is
  not a hex digest, a file that is not a GGUF.  This is operator-
  actionable → ``EXIT_CONFIG_ERROR`` (1).

The ``is_*_integrity_failure`` predicates below own that split.  They are
deliberately **structural** — they read the result's typed fields, never
its human-readable ``reason`` prose — so rewording an operator message
can never silently flip the exit-code contract (the same discipline
F-P4-OPUS-25 imposed on the pipeline-manifest routing token).

These predicates are internal surface: they are not listed in
``forgelm.__all__`` and carry no stability guarantee.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Dict, List, Tuple

# ---------------------------------------------------------------------------
# Annex IV artifact verification
# ---------------------------------------------------------------------------

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
        artifact = json.load(fh)

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
        actual = _compute_manifest_hash(artifact)
        if actual != expected:
            return VerifyAnnexIVResult(
                valid=False,
                reason="Manifest hash mismatch — artifact may have been modified after generation.",
                manifest_hash_actual=actual,
                manifest_hash_expected=expected,
            )
        return VerifyAnnexIVResult(
            valid=True,
            reason="All Annex IV §1-9 fields populated; manifest hash matches.",
            manifest_hash_actual=actual,
            manifest_hash_expected=expected,
        )

    # No manifest hash present — the field-completeness check is the
    # only signal we can give.  Pass with a note so the operator knows.
    return VerifyAnnexIVResult(
        valid=True,
        reason="All Annex IV §1-9 fields populated; no manifest_hash present so tampering detection skipped.",
    )


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


# ---------------------------------------------------------------------------
# GGUF integrity verification
# ---------------------------------------------------------------------------

_GGUF_MAGIC = b"GGUF"
_SIDECAR_SUFFIX = ".sha256"

# A SHA-256 sidecar must contain a 64-character hex digest.  Anything
# else (empty file, "TODO" placeholder, truncated paste, wrong-algorithm
# digest) is malformed; verify_gguf fails closed rather than silently
# accepting an unverifiable artefact.
_SHA256_HEX_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class VerifyGgufResult:
    """Structured GGUF verification result."""

    __slots__ = ("valid", "reason", "checks")

    def __init__(self, *, valid: bool, reason: str = "", checks: Dict[str, Any] | None = None) -> None:
        self.valid = valid
        self.reason = reason
        self.checks = dict(checks or {})

    def to_dict(self) -> Dict[str, Any]:
        return {"valid": self.valid, "reason": self.reason, "checks": dict(self.checks)}


def verify_gguf(path: str) -> VerifyGgufResult:
    """Library entry: verify a GGUF file's integrity.

    Three-layer check:

    1. **Magic header** — first 4 bytes must equal ``b"GGUF"``.  Anything
       else means the file is not a GGUF (operator likely passed the
       wrong path or a corrupted download).
    2. **Metadata block** (optional, when the ``gguf`` package is
       installed): parse the metadata + tensor descriptors via the
       upstream reader; a parse failure means either the file was
       truncated / corrupted, or the installed ``gguf`` release cannot
       read this file's format revision.
    3. **SHA-256 sidecar** (optional, when ``<path>.sha256`` exists):
       recompute the file hash and compare to the sidecar's contents.
       The forgelm exporter writes this sidecar by default; mismatch
       means the file was modified after export.

    A metadata-parse failure deliberately does **not** short-circuit the
    sidecar comparison (D1-07).  The sidecar is the stronger evidence: it
    can prove the file is byte-identical to what was exported, which
    settles the corruption-vs-parser-incompatibility ambiguity that the
    metadata error alone leaves open.  The two signals are therefore both
    collected and reported together, and
    :func:`is_gguf_integrity_failure` decides the verdict from the pair.

    Returns the structured result; raises :class:`OSError` for I/O
    failures so the dispatcher can surface them as ``EXIT_TRAINING_ERROR``.
    """
    checks: Dict[str, Any] = {
        "magic_ok": False,
        "metadata_parsed": False,
        "metadata_error": None,
        "sidecar_present": False,
        "sidecar_match": None,
    }
    with open(path, "rb") as fh:
        head = fh.read(len(_GGUF_MAGIC))
    if head != _GGUF_MAGIC:
        return VerifyGgufResult(
            valid=False,
            reason=f"Magic header mismatch: expected {_GGUF_MAGIC!r}, got {head!r}.  Not a GGUF file or corrupted.",
            checks=checks,
        )
    checks["magic_ok"] = True

    metadata_check = _maybe_parse_metadata(path)
    checks["metadata_parsed"] = metadata_check["parsed"]
    metadata_error = metadata_check.get("error")
    checks["metadata_error"] = metadata_error
    # NOTE: no early return here.  See the "does not short-circuit" note in
    # the docstring — the sidecar below can prove the bytes are exactly what
    # was exported, which downgrades this from "corrupted artefact" to
    # "your gguf package cannot read this file".
    if metadata_check.get("tensor_count") is not None:
        checks["tensor_count"] = metadata_check["tensor_count"]

    # SHA-256 sidecar (optional, but fail-closed on malformed contents).
    sidecar_path = path + _SIDECAR_SUFFIX
    if os.path.isfile(sidecar_path):
        checks["sidecar_present"] = True
        actual = _file_sha256(path)
        with open(sidecar_path, "r", encoding="utf-8") as fh:
            expected_text = fh.read().strip()
        # Sidecars are typically `<hex> *<filename>` (sha256sum format)
        # OR plain `<hex>`.  Take the first whitespace-separated token.
        expected = expected_text.split()[0] if expected_text else ""
        checks["sha256_actual"] = actual
        checks["sha256_expected"] = expected
        if not _SHA256_HEX_RE.match(expected):
            # Empty / non-hex / wrong-length sidecar.  Fail closed:
            # ignoring it would let a malformed sidecar masquerade as
            # "verified".  A genuinely-absent sidecar is the operator's
            # explicit choice (no file → no check); a *present but
            # malformed* sidecar is operator error we must surface.
            checks["sidecar_match"] = False
            return VerifyGgufResult(
                valid=False,
                reason=(
                    "Malformed SHA-256 sidecar: expected a 64-character hex digest, "
                    f"got {expected_text[:64]!r}.  Regenerate the sidecar (e.g. "
                    "`sha256sum model.gguf > model.gguf.sha256`) or remove it to "
                    "skip the check."
                ),
                checks=checks,
            )
        if actual != expected:
            checks["sidecar_match"] = False
            return VerifyGgufResult(
                valid=False,
                reason=f"SHA-256 sidecar mismatch — file modified after export.  Expected {expected[:16]}…, got {actual[:16]}….",
                checks=checks,
            )
        checks["sidecar_match"] = True

    if metadata_error:
        # Every comparison that *could* run has now run.  Report the parse
        # failure alongside what the sidecar established, so the operator
        # message says which of the two situations they are in.
        if checks["sidecar_match"]:
            detail = (
                "  The SHA-256 sidecar matches, so the file is byte-identical to what was "
                "exported — this is almost certainly a `gguf` package version that cannot "
                "read this file's format revision, not a corrupted artifact.  Upgrade `gguf` "
                "and re-run before treating it as a tampering event."
            )
        else:
            detail = (
                "  No SHA-256 sidecar was available to rule out corruption, so the file must "
                "be treated as truncated or damaged."
            )
        return VerifyGgufResult(
            valid=False,
            reason=f"GGUF metadata block could not be parsed: {metadata_error}.{detail}",
            checks=checks,
        )

    return VerifyGgufResult(
        valid=True,
        reason="GGUF magic OK"
        + (", metadata parsed" if checks["metadata_parsed"] else "")
        + (", SHA-256 sidecar match" if checks["sidecar_match"] else ""),
        checks=checks,
    )


def _maybe_parse_metadata(path: str) -> Dict[str, Any]:
    """Best-effort GGUF metadata parse via the optional ``gguf`` package.

    Returns ``{"parsed": bool, "error": str|None, "tensor_count": int|None}``.

    **Optional-dependency policy** (per ``CLAUDE.md`` and
    ``docs/standards/coding.md``): ``gguf`` is *not* a core ForgeLM
    dependency — operators using `verify-gguf` to spot-check exported
    artefacts on a minimal install legitimately do not have it.
    Absent ``gguf`` package = ``parsed=False``, ``error=None`` and the
    caller treats this as "metadata check skipped" (the magic-header
    + SHA-256-sidecar checks are the load-bearing integrity surface).
    Raising ``ImportError`` here would break the subcommand for the
    optional-extra-not-installed path and contradict the project
    standard.  Genuine corruption (file present but reader crashes)
    surfaces as a real ``error`` string and the caller fails closed.
    """
    try:
        from gguf import GGUFReader  # type: ignore[import-untyped]
    except ImportError:
        return {"parsed": False, "error": None, "tensor_count": None}
    try:
        reader = GGUFReader(path, "r")
        tensor_count = len(getattr(reader, "tensors", []) or [])
        return {"parsed": True, "error": None, "tensor_count": tensor_count}
    except Exception as exc:  # noqa: BLE001 — gguf has no clean exception hierarchy (struct.error, IndexError, ValueError, AttributeError, OSError). Catching BaseException would swallow KeyboardInterrupt/SystemExit, which we want to propagate. Acceptable per error-handling.md best-effort carve-out: verifier reports failure-path, never silent. # NOSONAR
        return {"parsed": False, "error": f"{exc.__class__.__name__}: {exc}", "tensor_count": None}


def _file_sha256(path: str) -> str:
    """Stream the file through SHA-256; never loads the whole file."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def is_gguf_integrity_failure(result: VerifyGgufResult) -> bool:
    """Return ``True`` when a GGUF result failed an **integrity** check.

    Walks the same three layers ``verify_gguf`` checks, in order, reading
    only the structured ``checks`` dict:

    - ``magic_ok`` false → the first four bytes are not ``b"GGUF"``.  The
      file is not a GGUF at all; the overwhelmingly common cause is the
      operator naming the wrong path.  **Input error → exit 1.**
    - sidecar present but ``sha256_expected`` is not a 64-char hex digest
      → the sidecar itself is unusable (empty, ``TODO``, truncated paste).
      Nothing was compared.  **Input error → exit 1.**
    - sidecar present with a well-formed digest that does not match →
      the file changed after export.  **Integrity → exit 6.**  This holds
      whether or not the metadata block also failed to parse: a checksum
      mismatch is the strongest evidence available and it dominates.
    - sidecar present with a well-formed digest that **matches**, yet the
      result is still invalid → the only remaining failure is a metadata
      parse error, and the checksum has just proven the file is
      byte-identical to what was exported.  Nothing was tampered with; the
      overwhelmingly likely cause is a ``gguf`` package too old for this
      file's format revision.  **Input error → exit 1** (D1-07: exit 6
      means "page the artefact owner", and a library-version mismatch must
      never trigger that).
    - magic OK, no usable sidecar, and still invalid → a GGUF whose
      metadata block could not be parsed, with nothing available to rule
      out corruption.  The artefact must be treated as structurally
      broken.  **Integrity → exit 6.**
    """
    if result.valid:
        return False
    checks = result.checks
    if not checks.get("magic_ok"):
        return False
    if checks.get("sidecar_present"):
        # ``sha256_expected`` is only absent when the sidecar branch never
        # ran, which ``sidecar_present`` already rules out; default to ""
        # so a hand-built result cannot raise here.
        if not _SHA256_HEX_RE.match(checks.get("sha256_expected") or ""):
            return False
        # Read the recorded comparison outcome rather than re-deriving it.
        # ``is not True`` rather than ``is False`` so an incomplete result
        # (``sidecar_match`` absent, which ``verify_gguf`` never produces
        # but a hand-built result could) fails *closed* onto the tamper
        # verdict.  Only a positively-recorded match — the checksum proving
        # the bytes are what was exported — earns the softer exit 1.
        return checks.get("sidecar_match") is not True
    return True


# ---------------------------------------------------------------------------
# Model-directory integrity verification (EU AI Act Art. 15)
# ---------------------------------------------------------------------------

_MANIFEST_NAME = "model_integrity.json"


class VerifyIntegrityResult:
    """Structured result of a model-integrity verification.

    Mirrors the sibling verify-* result shapes so integrators get a
    uniform surface across the verification toolbelt.
    """

    __slots__ = ("valid", "reason", "changed", "removed", "added", "verified_count")

    def __init__(
        self,
        *,
        valid: bool,
        reason: str = "",
        changed: List[str] | None = None,
        removed: List[str] | None = None,
        added: List[str] | None = None,
        verified_count: int = 0,
    ) -> None:
        self.valid = valid
        self.reason = reason
        self.changed = list(changed or [])
        self.removed = list(removed or [])
        self.added = list(added or [])
        self.verified_count = verified_count

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid": self.valid,
            "reason": self.reason,
            "changed": list(self.changed),
            "removed": list(self.removed),
            "added": list(self.added),
            "verified_count": self.verified_count,
        }


def verify_integrity(model_dir: str) -> VerifyIntegrityResult:
    """Library entry: verify a model directory against its integrity manifest.

    Reads ``<model_dir>/model_integrity.json`` (produced by
    :func:`forgelm.compliance.generate_model_integrity`), recomputes the
    SHA-256 of every recorded artifact, and walks the directory to detect
    files that exist on disk but are absent from the manifest.

    The manifest itself (``model_integrity.json``) is excluded from the
    walk — it is generated after the model artifacts and is not one of
    the recorded hashes, so it would otherwise always surface as an
    "added" file.

    A manifest that records **no artifacts** is refused rather than passed
    (see :func:`is_model_integrity_failure` for the full rationale): zero
    recorded artifacts means zero comparisons, and reporting success for
    zero comparisons tells CI "these are the weights that were signed off"
    when nothing was examined at all.

    Returns the structured result; raises :class:`FileNotFoundError` when
    the manifest is missing, :class:`json.JSONDecodeError` when it is
    malformed, and :class:`OSError` for genuine I/O failures while
    re-hashing — the dispatcher maps each to its documented exit code.
    """
    from forgelm.compliance import hash_file

    manifest_path = os.path.join(model_dir, _MANIFEST_NAME)
    with open(manifest_path, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)

    # A non-object root (a JSON array, string or number) has no ``artifacts``
    # key to read.  ``manifest.get(...)`` on it used to be short-circuited to
    # ``[]``, which then reported "All 0 artifacts present" and exited 0 — a
    # document that is not a model_integrity.json at all verifying clean.
    if not isinstance(manifest, dict):
        return VerifyIntegrityResult(
            valid=False,
            reason=f"Manifest root is {type(manifest).__name__}, expected a JSON object.",
        )
    # Missing key vs. present-but-empty are *distinguishable* and reported
    # with different prose, because they point at different causes: a missing
    # key means the file is not a model_integrity.json (wrong document, or a
    # write that died before the key was emitted), while an empty list means
    # the generator ran over a directory it found nothing in.  They share one
    # verdict, though — neither can compare anything.
    if "artifacts" not in manifest:
        return VerifyIntegrityResult(
            valid=False,
            reason=(
                "Manifest has no 'artifacts' key — this is not a model_integrity.json "
                "document, so nothing could be verified.  Re-run the compliance export."
            ),
        )
    recorded = manifest["artifacts"]
    # A non-list ``artifacts`` container (null, a string, a mapping) is a
    # malformed manifest, not an empty one — silently coercing it to ``[]``
    # would report "All 0 artifacts present" and exit 0.  Refuse up front so
    # the dispatcher maps it to EXIT_CONFIG_ERROR, the same as a bad entry.
    if not isinstance(recorded, list):
        return VerifyIntegrityResult(
            valid=False,
            reason=f"Manifest 'artifacts' is not a list: {type(recorded).__name__}.",
        )
    # An artifact-less manifest attests to nothing.  This check must precede
    # the on-disk walk below: with an empty manifest every file present would
    # surface as "added" and route to EXIT_INTEGRITY_FAILURE (6), telling CI
    # the weights were tampered with when the manifest simply covers nothing.
    # ``generate_model_integrity`` emits exactly this shape when handed a path
    # that is not a directory, so an interrupted export or a mistyped
    # ``final_path`` produces an empty manifest through no adversarial action
    # — the non-adversarial case is precisely why this must fail loudly.
    if not recorded:
        return VerifyIntegrityResult(
            valid=False,
            reason=(
                "Manifest records 0 artifacts, so nothing was verified.  An empty "
                "manifest cannot attest to anything; regenerate it from a populated "
                "model directory (`forgelm export --compliance`, or "
                "`forgelm.compliance.generate_model_integrity`)."
            ),
        )
    # Normalise recorded paths to forward slashes so a Windows-generated
    # manifest ("subdir\\file") compares equal to the verifier's on-disk
    # relpath ("subdir/file") and does not false-positive as added/missing.
    recorded_rel = {
        entry["file"].replace("\\", "/")
        for entry in recorded
        if isinstance(entry, dict) and isinstance(entry.get("file"), str)
    }

    base = os.path.realpath(model_dir)

    changed: List[str] = []
    removed: List[str] = []
    verified = 0
    for entry in recorded:
        # A non-dict entry (``{"artifacts": ["model.safetensors"]}``) used to
        # be skipped silently, so a manifest whose every entry was malformed
        # walked the loop without a single hash and reported "All 0 recorded
        # artifact(s) present and unchanged" with exit 0 — the same fail-open
        # the empty-list guard above closes, reached by a different route.
        # Refuse it the way the non-string ``file`` branch below already does:
        # both are malformed manifests, and only one of them was being caught.
        if not isinstance(entry, dict):
            return VerifyIntegrityResult(
                valid=False,
                reason=f"Manifest entry is not an object: {entry!r}.",
            )
        rel_path = entry.get("file")
        # A non-string ``file`` (or a recorded path whose realpath escapes
        # model_dir, e.g. "../secret") is a malformed/hostile manifest, not
        # a recoverable mismatch — refuse rather than hashing an arbitrary
        # out-of-tree file or crashing in os.path.join with a TypeError.
        if not isinstance(rel_path, str):
            return VerifyIntegrityResult(
                valid=False,
                reason=f"Manifest entry has a non-string 'file' value: {rel_path!r}.",
            )
        abs_path = os.path.join(model_dir, rel_path.replace("\\", "/"))
        real = os.path.realpath(abs_path)
        try:
            contained = os.path.commonpath([real, base]) == base
        except ValueError:
            # Different drives (Windows) → no shared prefix; treat as escaping.
            contained = False
        if not contained:
            return VerifyIntegrityResult(
                valid=False,
                reason=f"Manifest entry path escapes the model directory: {rel_path!r}.",
            )
        if not os.path.isfile(abs_path):
            removed.append(rel_path)
            continue
        actual = hash_file(abs_path, rel_path)
        if actual["sha256"] != entry.get("sha256"):
            changed.append(rel_path)
        else:
            verified += 1

    # Files on disk not recorded in the manifest = added since generation.
    added: List[str] = []
    for root, _dirs, files in os.walk(model_dir):
        for filename in files:
            abs_path = os.path.join(root, filename)
            rel_path = os.path.relpath(abs_path, model_dir).replace(os.sep, "/")
            if rel_path == _MANIFEST_NAME:
                continue
            if rel_path not in recorded_rel:
                added.append(rel_path)

    if changed or removed or added:
        parts = []
        if changed:
            parts.append(f"{len(changed)} changed")
        if removed:
            parts.append(f"{len(removed)} removed")
        if added:
            parts.append(f"{len(added)} added")
        return VerifyIntegrityResult(
            valid=False,
            reason="Model artifacts do not match model_integrity.json: " + ", ".join(parts) + ".",
            changed=sorted(changed),
            removed=sorted(removed),
            added=sorted(added),
            verified_count=verified,
        )

    return VerifyIntegrityResult(
        valid=True,
        reason=f"All {verified} recorded artifact(s) present and unchanged.",
        verified_count=verified,
    )


def is_model_integrity_failure(result: VerifyIntegrityResult) -> bool:
    """Return ``True`` when a model directory **disagrees with a usable manifest**.

    The line is "could the verifier compare anything?":

    - **Integrity failure** (``True`` → exit 6): the manifest parsed, the
      walk ran, and at least one artifact came back ``changed`` /
      ``removed`` / ``added``.  The deployed weights are not the weights
      that were signed off.
    - **Input error** (``False`` → exit 1): the manifest itself is
      unusable — the root is not a JSON object, there is no ``artifacts``
      key, ``artifacts`` is not a list, ``artifacts`` is **empty**, an
      entry is not an object, an entry's ``file`` is not a string, or an
      entry's path escapes the model directory.  Each of these returns
      before any artifact is hashed, so there is no artifact-level verdict
      to report; the operator has to fix or regenerate the manifest.

    The empty-manifest case is the one that had to be *added* rather than
    merely classified.  ``artifacts: []`` is structurally valid JSON, so
    the verifier used to walk it happily, compare nothing, and return
    ``valid=True`` with ``verified_count=0`` — printing "All 0 recorded
    artifact(s) present and unchanged" and exiting 0, the code CI reads as
    "these are the weights that were signed off".  The threat model is not
    adversarial (an attacker able to rewrite the manifest could recompute
    hashes for tampered weights instead; ``model_integrity.json`` is not
    itself signed).  It is the *non-adversarial* case that bites: a partial
    write, an interrupted export, or ``generate_model_integrity`` pointed
    at a path that is not a directory all yield an artifact-less manifest,
    and the operator is then told a check happened that did not.

    The path-escape case is deliberately on the *input* side even though
    an escaping entry is the shape of an attack: what the verifier is
    reporting is "I refused to hash an out-of-tree file", not "your
    weights changed", and the message is directly operator-actionable.
    Routing it to 6 would tell a CI pipeline the model was tampered with
    when the model was never examined.
    """
    return not result.valid and bool(result.changed or result.removed or result.added)


# ---------------------------------------------------------------------------
# Audit-log verification classification
# ---------------------------------------------------------------------------
#
# The verifier itself stays in ``forgelm.compliance`` (see the module
# docstring above for why).  Only the *classification* predicate lives
# here, next to its three siblings, so the four verify-* subcommands read
# their exit-code decision from one place.


def is_audit_integrity_failure(failure_kind: str | None) -> bool:
    """Return ``True`` when an audit-log failure is a **tamper** verdict.

    Completes the set alongside :func:`is_annex_iv_integrity_failure`,
    :func:`is_gguf_integrity_failure` and :func:`is_model_integrity_failure`.
    ``verify-audit`` previously had no predicate at all and blanket-mapped
    every ``valid=False`` to exit 6, leaning entirely on the CLI's
    readability probe having pre-caught the non-integrity cases — so a
    failure mode the probe missed (a character device, whose ``open``
    succeeds and whose verdict is "not found") was reported to CI as
    tampering (F-4 / D1-09).

    - **Integrity failure** (``True`` → exit 6): the log was located and
      read end-to-end, and the SHA-256 chain, an HMAC tag, the genesis
      manifest, or the UTF-8 encoding of the record itself did not hold up.
    - **Input / runtime error** (``False`` → exit 1 or 2): there was no log
      at that path, the log was there but held **zero entries** with no
      genesis manifest to say what it should have held, the option
      combination was impossible, or the read failed part-way.  Nothing
      was compared.

    The zero-entry case sits on the input side for the same reason the
    artifact-less manifest does in :func:`is_model_integrity_failure`: with
    no manifest there is no baseline in existence, so the verifier never
    got to compare anything.  A zero-entry log *with* a manifest pinning a
    real first entry is the opposite — that comparison ran and failed — and
    classifies as :data:`~forgelm.compliance.AUDIT_FAILURE_INTEGRITY`
    (exit 6).  Both are ``valid=False``; only one is tampering.

    Takes the ``AUDIT_FAILURE_*`` token from
    :func:`forgelm.compliance._verify_audit_log_classified` (``None`` for a
    passing verification) rather than a result object, because that
    classification deliberately does not live on the stable-tier
    :class:`~forgelm.compliance.VerifyResult` — see the constants for why.
    The three sibling predicates read their results' typed fields for the
    same reason this one reads a token: never operator-facing ``reason``
    prose, so rewording a message cannot move a verdict between exit codes.
    """
    from forgelm.compliance import AUDIT_FAILURE_ENCODING, AUDIT_FAILURE_INTEGRITY

    return failure_kind in (AUDIT_FAILURE_INTEGRITY, AUDIT_FAILURE_ENCODING)


__all__ = [
    "VerifyAnnexIVResult",
    "VerifyGgufResult",
    "VerifyIntegrityResult",
    "is_annex_iv_integrity_failure",
    "is_audit_integrity_failure",
    "is_gguf_integrity_failure",
    "is_model_integrity_failure",
    "verify_annex_iv_artifact",
    "verify_gguf",
    "verify_integrity",
]
