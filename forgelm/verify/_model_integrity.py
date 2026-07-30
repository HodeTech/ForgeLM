"""Model-directory integrity verification (EU AI Act Art. 15).

Owns :class:`VerifyIntegrityResult`, the walk that re-hashes a trained model
directory against its ``model_integrity.json`` manifest, and
:func:`is_model_integrity_failure` — the structural predicate that routes a
mismatch to exit ``6`` and an unusable manifest to exit ``1``.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List

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
