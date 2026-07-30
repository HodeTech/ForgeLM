"""Per-stage Annex IV evidence verification for multi-stage pipelines.

Owns the chain-level walk: resolving each stage's evidence pointer,
reading it under a byte cap, deep-parsing it through
:mod:`._annex_iv`, and folding the per-stage dispositions into a
:class:`PipelineEvidenceReport` with its census and routing token.

This is the module that actually opens files, so the size caps, the
``_read_capped_json`` helper and the path-escape checks live here — not
on the package facade.  Tests that need to force an I/O failure must
patch ``forgelm.verify._pipeline_evidence.<name>``; patching the
re-export on the package would rebind a name this module never reads.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from typing import Any, Dict, List, Tuple

from ._annex_iv import is_annex_iv_integrity_failure, verify_annex_iv_payload

# A per-stage Annex IV artefact is a small JSON document (single-digit kB in
# practice).  Anything past this cap is refused *unread* rather than parsed:
# json.load on an attacker-supplied multi-GB file is an OOM, and a verifier
# that can be killed by its own input is not a verifier.  Refusing is a
# violation, not a pass — fail closed.
STAGE_EVIDENCE_MAX_BYTES = 8 * 1024 * 1024

# The same cap, for the same reason, on the chain manifest itself.  It was
# previously ``json.load``ed with no cap at all, which contradicted the
# rationale written directly above it: a 600 MB pipeline_manifest.json reaches
# roughly 3.6 GB peak RSS because CPython's parser materialises the whole
# object graph.  The number is not tuned to the manifest's real size (one row
# per stage; single-digit kB for any pipeline a human would configure) — it is
# deliberately the *same* 8 MiB as the stage cap so there is one number to
# reason about, and it sits several orders of magnitude above any legitimate
# manifest while staying far below a size that can exhaust memory.  Unlike the
# stage cap this routes to INPUT_ERROR (exit 1), not a violation: an oversized
# manifest is refused *unread*, so nothing was ever compared, and the
# exit-code contract reserves 6 for comparisons that were made and failed.
PIPELINE_MANIFEST_MAX_BYTES = 8 * 1024 * 1024

# json.load recurses once per nesting level, so a deeply-nested document blows
# the interpreter stack long before it approaches the byte caps above — a
# ~100 KB file of 50 000 nested arrays is enough.  RecursionError is not an
# OSError and not a ValueError, so it escaped every existing handler and killed
# the verifier with a raw traceback, no stdout and no JSON envelope.  Both the
# stage artefact and the chain manifest catch it explicitly.
# Mirrors ``forgelm.compliance.ANNEX_IV_ARTEFACT_BASENAME``.  Duplicated as a
# literal rather than imported because ``compliance`` imports this module and a
# module-level import back would close the cycle; every cross-module reference
# in this file is deliberately function-local for the same reason.
# ``tests/test_pipeline_compliance.py`` pins the two to the same value and to
# what ``export_compliance_artifacts`` actually writes, so the duplication
# cannot drift the way the orchestrator's pointer did.
_ANNEX_IV_EVIDENCE_BASENAME = "annex_iv_metadata.json"

# Pointers written by ForgeLM < 0.9.1 name ``training_manifest.json``, a
# filename no ForgeLM version has ever written (``export_compliance_artifacts``
# emits ``training_manifest.yaml`` and ``annex_iv_metadata.json``).  0.9.1
# repointed the writer at the real artefact; see
# ``forgelm/cli/_pipeline.py``.  Archived manifests from before that fix still
# carry the dangling pointer, so the reader resolves it to the sibling that
# actually holds the payload — but *only* for manifests old enough to have
# been written by the broken writer.  On a current manifest that basename is
# not a legacy artefact, it is a pointer that does not match what this version
# writes, and it gets the conservative routing.
_LEGACY_EVIDENCE_BASENAME = "training_manifest.json"
_LEGACY_POINTER_FIXED_IN = (0, 9, 1)

# Leading numeric release components of a ``forgelm_version`` string
# ("0.9.1rc1" -> (0, 9, 1); "0.8.0" -> (0, 8, 0)).  A version that does not
# match is treated as *not* legacy, which is the conservative direction: it
# routes a missing artefact to a violation rather than to the softer
# compatibility path.
#
# Only the leading *release triple* is compared, so a pre-release, dev, post
# or local segment of X.Y.Z is X.Y.Z and is never "older" than X.Y.Z.  This is
# deliberate and load-bearing: ``packaging.Version("0.9.1rc1") < "0.9.1"`` is
# ``True``, so a Version-based gate would hand every manifest written by an
# rc build the compatibility path meant only for archived releases.
#
# ``[0-9]`` and ``re.ASCII`` rather than ``\d``: ``\d`` is Unicode-aware, so
# Arabic-Indic digits ("٠.٩.٠") parsed and were classified
# legacy.  A version string is ASCII by construction; anything else is
# unparseable and takes the conservative branch.
_VERSION_RE = re.compile(r"^\s*([0-9]+)\.([0-9]+)\.([0-9]+)", re.ASCII)

# ``forgelm/_version.py``'s sentinel for a raw-source checkout that was never
# pip-installed.  It parses to (0, 0, 0) and would therefore be classified as
# the *oldest possible* release — precisely backwards, since it denotes a
# current working tree, not an archived pre-0.9.1 run.  Carved out explicitly
# so an uninstalled checkout does not unlock the softer path.
_DEV_VERSION_SENTINEL = "0.0.0+dev"

# Per-stage outcome tokens.  Callers route on these, never on message prose.
EVIDENCE_VERIFIED = "verified"
EVIDENCE_UNVERIFIED = "unverified"
EVIDENCE_VIOLATION = "violation"
EVIDENCE_IO_ERROR = "io_error"

# Chain-level manifest hash states, mirrored into the CLI JSON envelope.
HASH_STATE_VERIFIED = "verified"
HASH_STATE_MISMATCH = "mismatch"
HASH_STATE_ABSENT = "absent"

# Every not-completed status a stage can legitimately hold, mapped to why it
# was not deep-parsed.  Published per stage so each manifest row appears in the
# report with a stated reason instead of being silently omitted.
_DISPOSITION_BY_STATUS = {
    "pending": "not_applicable:pending",
    "running": "not_applicable:running",
    "failed": "not_applicable:failed",
    "gated_pending_approval": "not_applicable:gated",
    "skipped_by_filter": "not_applicable:filtered",
    "skipped_due_to_prior_revert": "not_applicable:chain_broken",
}

# The complete, closed set of status tokens any ForgeLM version has ever
# written — the six above plus "completed".  Derived rather than restated so
# the two cannot drift apart.  Mirrors
# ``forgelm.cli._pipeline.StageStatusLiteral``; ``tests/`` pins them together
# (a module-level import back would close an import cycle, as everywhere else
# in this file).
#
# Anything outside this set is a violation, not a stage to skip.  A verifier
# that silently ignores statuses it does not recognise lets an adversary
# *choose* a token to be ignored by: flip "completed" to "skipped" — a value no
# writer emits — and the stage, with its now-deleted evidence, drops out of the
# report entirely.
_KNOWN_STAGE_STATUSES = frozenset(_DISPOSITION_BY_STATUS) | {"completed"}

# Dispositions for stages that were examined or refused.
_DISPOSITION_UNKNOWN_STATUS = "violation:unrecognised_status"
_DISPOSITION_PASSED_NOT_COMPLETED = "violation:gate_passed_but_not_completed"


class PipelineEvidenceReport:
    """Aggregate verdict over a pipeline manifest's per-stage evidence.

    ``violations`` carries the routing-token-prefixed strings the CLI maps
    to exit codes.  The counters exist so the envelope can state how much
    was actually examined: a chain verifier that reports success having
    verified nothing is precisely the defect this class of check keeps
    reproducing, and the only defence is publishing the count.
    """

    __slots__ = (
        "violations",
        "stages_examined",
        "evidence_verified",
        "evidence_unverified",
        "hash_state",
        "stages",
        "audit_corroboration",
    )

    def __init__(self, *, hash_state: str = HASH_STATE_ABSENT) -> None:
        self.violations: List[str] = []
        self.stages_examined = 0
        self.evidence_verified = 0
        self.evidence_unverified = 0
        self.hash_state = hash_state
        # Tier 3: the manifest's own hash is unkeyed, so it proves nothing
        # against an adversary who can write the manifest.  This is the
        # cross-check against the one keyed artefact in the system.  ``None``
        # on the pre-flight paths, where no manifest was ever parsed.
        self.audit_corroboration: Dict[str, Any] | None = None
        # One row per stage the manifest carries, each with a disposition.
        # ``stages_examined`` alone cannot distinguish "this chain had one
        # stage" from "this chain had two and one was made to disappear";
        # the published total and per-status census can.  Both are derived
        # from this list in ``to_dict`` so there is a single source of truth.
        self.stages: List[Dict[str, Any]] = []

    def record_stage(self, *, name: Any, index: int, status: Any, disposition: str) -> None:
        """Record one manifest stage row and why it was or was not examined."""
        key = status if isinstance(status, str) and status else f"<{type(status).__name__}>"
        label = name if isinstance(name, str) else "<unnamed>"
        self.stages.append({"index": index, "name": label, "status": key, "disposition": disposition})

    def to_dict(self) -> Dict[str, Any]:
        census = Counter(row["status"] for row in self.stages)
        return {
            "stages_total": len(self.stages),
            "stages_examined": self.stages_examined,
            "evidence_verified": self.evidence_verified,
            "evidence_unverified": self.evidence_unverified,
            "hash_state": self.hash_state,
            "status_census": dict(sorted(census.items())),
            "stage_dispositions": list(self.stages),
            "audit_corroboration": self.audit_corroboration,
        }


# A zero-byte artefact would otherwise surface as a generic JSONDecodeError
# ("Expecting value: line 1 column 1").  It gets its own exception because
# "the evidence file is empty" is a materially different — and far more
# legible — finding than "the JSON is malformed".
class _EmptyFileError(Exception):
    """Raised by :func:`_read_capped_json` for a zero-byte file."""


class _OversizeError(Exception):
    """Raised past the byte cap; carries the size so callers can name it."""

    def __init__(self, size: int) -> None:
        super().__init__(f"{size} bytes")
        self.size = size


# The size check MUST come from ``os.fstat`` on the open descriptor, not from
# a separate ``os.path.getsize`` — the rule
# ``compliance.compute_dataset_fingerprint`` already follows.  Under
# stat-then-open the file that was measured and the file that is read are two
# different observations: a payload can be small at the stat and arbitrarily
# large at the read, so the cap that exists to stop the verifier being killed
# by its own input is bypassed outright.  Stating the open descriptor closes
# that window — the bytes measured are the bytes read.
#
# The bounded ``read(cap + 1)`` is a second, independent guard: it rejects an
# over-cap payload even when fstat under-reports (a growing file, or a path
# where st_size is not authoritative).  The fstat is the correctness fix; the
# bounded read is what makes the cap true regardless of what the descriptor
# claims about itself.
#
# Binary mode so that read counts *bytes*.  In text mode ``read(n)`` counts
# decoded characters, so a multibyte UTF-8 payload could satisfy a character
# budget while carrying several times the byte cap.  The explicit decode
# leaves behaviour unchanged: invalid UTF-8 still raises UnicodeDecodeError,
# which every caller already routes.
def _read_capped_json(path: str, cap: int) -> Any:
    """Open *path* once, enforce *cap* on that same handle, then parse.

    Raises :class:`_OversizeError` past the cap and :class:`_EmptyFileError`
    on a zero-byte file; otherwise propagates what ``json.load`` would.
    """
    with open(path, "rb") as fh:
        size = os.fstat(fh.fileno()).st_size
        if size > cap:
            raise _OversizeError(size)
        raw = fh.read(cap + 1)
        if len(raw) > cap:
            raise _OversizeError(len(raw))
    if not raw:
        raise _EmptyFileError()
    return json.loads(raw.decode("utf-8"))


def _resolve_stage_evidence_path(pointer: str, pipeline_dir: str) -> Tuple[str, str]:
    """Resolve a stage evidence pointer to a readable path.

    Returns ``(path, problem)``; exactly one is non-empty.  Rejects the
    ambiguous shapes rather than reading through them:

    - A **relative** pointer resolves against *pipeline_dir* and must stay
      inside it.  ``../../etc/passwd`` escapes and is refused.  Absolute
      pointers are allowed unconditionally because a stage's
      ``training.output_dir`` is config-declared and legitimately lives
      outside the pipeline tree (``forgelm/cli/_pipeline.py`` resolves stage
      outputs under each stage's own ``training.output_dir``, not under
      ``pipeline.output_dir``), so containment is not a rule we can impose
      on them without breaking valid configs.
    - A **symlink** is refused where a regular file is expected: the
      evidence would be whatever the link points at when the verifier runs,
      which is not a property of the archived run.
    - A **directory** is refused for the same reason ``open()`` would fail,
      but with a verdict instead of a traceback.

    The relative branch deliberately runs *three* checks rather than one.
    It previously called ``os.path.realpath`` on the joined pointer and
    checked containment on the result, which resolved symlinks before
    anything looked at them — so ``os.path.islink(candidate)`` below could
    never be true for a relative pointer, and the documented symlink
    refusal was dead code.  A relative pointer at a symlink was silently
    followed.  Now: lexical containment rejects ``../`` escapes without
    resolving anything, the symlink refusal sees the *unresolved* path so
    it actually bites, and a final realpath containment check catches an
    escape smuggled through a symlinked parent directory (which lexical
    normalisation alone cannot see).
    """
    if not os.path.isabs(pointer):
        base = os.path.abspath(pipeline_dir)
        candidate = os.path.normpath(os.path.join(base, pointer))
        if candidate != base and not candidate.startswith(base + os.sep):
            return "", f"evidence path {pointer!r} escapes the pipeline directory"
        real_base = os.path.realpath(base)
        resolved = os.path.realpath(candidate)
        if resolved != real_base and not resolved.startswith(real_base + os.sep):
            return "", f"evidence path {pointer!r} escapes the pipeline directory via a symlinked parent"
    else:
        candidate = pointer

    if os.path.islink(candidate):
        return "", f"evidence path {pointer!r} is a symlink, not a regular file"
    if os.path.isdir(candidate):
        return "", f"evidence path {pointer!r} is a directory, not a regular file"
    return candidate, ""


def _annex_iv_was_configured(manifest: Dict[str, Any] | None) -> bool:
    """Did the run's config carry an Annex IV block at all?

    Mirrors :func:`forgelm.compliance.build_annex_iv_artifact`'s two ``None``
    conditions against the *chain* manifest, which embeds the same
    operator-supplied block via ``_provider_metadata_from_config``: the key is
    present only when a ``compliance:`` block was configured, and the writer
    additionally skips the artefact when all three §1 identity fields are
    blank.  When this returns ``False`` no per-stage evidence file was ever
    produced, for an entirely legitimate reason — the operator did not ask for
    compliance artefacts.  That is not tampering and must not be reported as
    such; it is also not the same situation as evidence that *should* exist and
    does not, which is why the two are routed separately below.
    """
    if not isinstance(manifest, dict):
        return True  # Unknown provenance: assume evidence was expected.
    block = manifest.get("annex_iv")
    if not isinstance(block, dict):
        return False
    return any(str(block.get(key, "")).strip() for key in ("provider_name", "system_name", "intended_purpose"))


def _manifest_predates_pointer_fix(manifest: Dict[str, Any] | None) -> bool:
    """Was *manifest* written by a ForgeLM old enough to emit the dangling
    ``training_manifest.json`` pointer?

    Gates the legacy-basename fallback so it is what it claims to be — a
    compatibility path for archived pre-0.9.1 runs — rather than the universal
    path every current run took while the writer was still emitting a phantom
    filename.  An absent or unparseable ``forgelm_version`` returns ``False``:
    the conservative direction, since it routes a missing artefact to a
    violation rather than to the softer compatibility path.
    """
    if not isinstance(manifest, dict):
        return False
    raw = str(manifest.get("forgelm_version") or "").strip()
    if raw == _DEV_VERSION_SENTINEL:
        # A raw-source checkout is a *current* build whose version string
        # merely sorts lowest.  Not legacy.
        return False
    match = _VERSION_RE.match(raw)
    if not match:
        return False
    return tuple(int(part) for part in match.groups()) < _LEGACY_POINTER_FIXED_IN


def _missing_evidence_outcome(label: str, manifest: Dict[str, Any] | None) -> Tuple[str, str]:
    """Route a per-stage artefact that is not on disk.

    Two genuinely different situations, and conflating them is what inverted
    the tamper signal:

    - The run configured no ``compliance:`` block, so no artefact was ever
      written.  Nothing is missing; there was never anything to miss.
      UNVERIFIED (exit 1) — the verifier never got to compare anything.
    - The run configured Annex IV metadata, so the artefact was written and is
      now gone.  Deleting a stage's Annex IV evidence is the archetypal
      Article 12 tampering and is *more* severe than corrupting it.
      VIOLATION (exit 6).
    """
    if not _annex_iv_was_configured(manifest):
        return (
            EVIDENCE_UNVERIFIED,
            f"no Annex IV evidence exists at {label!r} because the run configured no 'compliance:' block — "
            "nothing was produced to verify",
        )
    return (
        EVIDENCE_VIOLATION,
        f"evidence at {label!r} is missing — the run configured Annex IV metadata, so this artefact was written",
    )


def _verify_stage_evidence(
    pointer: Any,
    pipeline_dir: str,
    manifest: Dict[str, Any] | None = None,
) -> Tuple[str, str]:
    """Deep-verify one completed stage's Annex IV evidence.

    Returns ``(outcome, message)`` where *outcome* is one of the
    ``EVIDENCE_*`` tokens.  Every ambiguous on-disk state fails closed;
    see this module's docstring and
    ``docs/standards/error-handling.md`` for the exit-code contract.

    Depth is exactly one level — the per-stage artefact is verified, but
    nothing it references is followed — so an adversarial manifest cannot
    drive unbounded recursion.
    """
    if not pointer or not isinstance(pointer, str):
        return EVIDENCE_VIOLATION, "stage claims status 'completed' but records no evidence path"

    path, problem = _resolve_stage_evidence_path(pointer, pipeline_dir)
    if problem:
        return EVIDENCE_VIOLATION, problem

    # Every message below names the file that was actually examined.  When the
    # legacy fallback rebinds ``path``, interpolating the original pointer
    # would tell the operator about a file the verifier never opened (F3).
    label = pointer

    if not os.path.isfile(path):
        # Compatibility path for archived pre-0.9.1 runs only: those recorded
        # a ``training_manifest.json`` pointer that no writer ever satisfied,
        # so resolve it to the sibling that actually carries the payload.
        # Current manifests fall straight through to the missing-evidence
        # routing, where a deleted artefact is the violation it is.
        if os.path.basename(path) == _LEGACY_EVIDENCE_BASENAME and _manifest_predates_pointer_fix(manifest):
            sibling = os.path.join(os.path.dirname(path), _ANNEX_IV_EVIDENCE_BASENAME)
            if os.path.isfile(sibling) and not os.path.islink(sibling):
                path = sibling
                label = sibling
            else:
                return _missing_evidence_outcome(label, manifest)
        else:
            return _missing_evidence_outcome(label, manifest)

    # Size cap and parse share ONE open descriptor: a stat on one handle
    # followed by an open of another measures a file that need not be the
    # file that gets read.  See ``_read_capped_json``.
    try:
        artifact = _read_capped_json(path, STAGE_EVIDENCE_MAX_BYTES)
    except _OversizeError as exc:
        return (
            EVIDENCE_VIOLATION,
            f"evidence at {label!r} is {exc.size} bytes, over the {STAGE_EVIDENCE_MAX_BYTES}-byte cap — refused unread",
        )
    except _EmptyFileError:
        return EVIDENCE_VIOLATION, f"evidence at {label!r} is zero bytes"
    except json.JSONDecodeError as exc:
        return EVIDENCE_VIOLATION, f"evidence at {label!r} is not valid JSON: {exc.msg} (line {exc.lineno})"
    except UnicodeDecodeError as exc:
        return EVIDENCE_VIOLATION, f"evidence at {label!r} is not valid UTF-8: {exc}"
    except RecursionError:
        # A deeply-nested document exhausts the interpreter stack inside
        # json.loads well under the byte cap (~100 KB of nested arrays is
        # enough), and RecursionError is neither an OSError nor a ValueError,
        # so it escaped every handler and killed the verifier with a raw
        # traceback and no envelope.  Same class as unparseable JSON: the
        # artefact was reached and is not usable evidence.
        return (
            EVIDENCE_VIOLATION,
            f"evidence at {label!r} is nested too deeply to parse — refused rather than crashing the verifier",
        )
    except OSError as exc:
        return EVIDENCE_IO_ERROR, f"evidence at {label!r} is unreadable: {exc}"

    # Pure, already-parsed payload: raises none of the I/O or decode errors
    # handled above, so it needs no handlers of its own.
    result = verify_annex_iv_payload(artifact)

    if not result.valid:
        # Deliberate divergence from the standalone verifier: an
        # incomplete-fields artefact verified on its own exits 1, but as
        # chain evidence it exits 6.  At chain level the pipeline manifest
        # *asserts* this stage completed with valid evidence; that assertion
        # was compared against the artefact and it did not hold.
        if is_annex_iv_integrity_failure(result):
            return EVIDENCE_VIOLATION, f"evidence at {label!r} failed tamper detection: {result.reason}"
        detail = f" (missing: {', '.join(result.missing_fields)})" if result.missing_fields else ""
        return EVIDENCE_VIOLATION, f"evidence at {label!r} is unusable: {result.reason}{detail}"

    if not result.manifest_hash_expected:
        return (
            EVIDENCE_UNVERIFIED,
            f"evidence at {label!r} is complete but carries no manifest_hash — tampering could not be checked",
        )
    return EVIDENCE_VERIFIED, ""


def verify_pipeline_stage_evidence(manifest: Dict[str, Any], pipeline_dir: str) -> PipelineEvidenceReport:
    """Chain-level aggregator over every completed stage's evidence.

    Called by :func:`forgelm.compliance.verify_pipeline_manifest_at_path`
    for the disk-bound half of ``forgelm verify-annex-iv --pipeline``.  The
    in-memory structural verifier cannot see the filesystem; this can.
    """
    from forgelm.compliance import (
        PIPELINE_MANIFEST_IO_ERROR_PREFIX,
        PIPELINE_MANIFEST_UNVERIFIED_PREFIX,
    )

    metadata = manifest.get("metadata") if isinstance(manifest.get("metadata"), dict) else None
    expected_hash = metadata.get("manifest_hash") if metadata else None
    if not expected_hash:
        hash_state = HASH_STATE_ABSENT
    else:
        from forgelm.compliance import compute_annex_iv_manifest_hash

        hash_state = (
            HASH_STATE_VERIFIED if compute_annex_iv_manifest_hash(manifest) == expected_hash else HASH_STATE_MISMATCH
        )
    report = PipelineEvidenceReport(hash_state=hash_state)

    raw_stages = manifest.get("stages")
    if not isinstance(raw_stages, list):
        # The in-memory verifier already flagged this shape; nothing on disk
        # to add.
        return report

    for idx, stage in enumerate(raw_stages):
        if not isinstance(stage, dict):
            report.violations.append(f"stage at index {idx} is not an object (got {type(stage).__name__})")
            report.record_stage(name=None, index=idx, status=stage, disposition="violation:not_an_object")
            continue

        status = stage.get("status")
        name = stage.get("name", "<unnamed>")

        if status != "completed":
            # Rule 1 — an unrecognised status token is a violation, never a
            # stage to skip.  The seven literals in _KNOWN_STAGE_STATUSES are
            # the complete closed set; no legitimate manifest can trip this.
            disposition = _DISPOSITION_BY_STATUS.get(status) if isinstance(status, str) else None
            # The isinstance guard is load-bearing, not defensive noise: a JSON
            # manifest can carry a list or dict here, and ``x in frozenset``
            # raises TypeError on an unhashable x — crashing the verifier with
            # a raw traceback and no envelope, which is the failure mode the
            # caps and parse guards exist to prevent.
            if not isinstance(status, str) or status not in _KNOWN_STAGE_STATUSES:
                report.violations.append(
                    f"Stage {name!r}: unrecognised status {status!r} — not one of the "
                    f"{len(_KNOWN_STAGE_STATUSES)} values any ForgeLM version writes; the stage "
                    "was not examined and its evidence, if any, was not verified"
                )
                disposition = _DISPOSITION_UNKNOWN_STATUS
            # Rule 2 — ``gate_decision == "passed"`` is written on exactly one
            # code path, alongside ``status = "completed"``
            # (``forgelm/cli/_pipeline.py``).  A stage carrying the passed gate
            # without the completed status is internally inconsistent: the
            # status was changed after the fact.  Checked independently of
            # Rule 1 so a downgrade to a *recognised* status is caught too.
            #
            # Deliberately NOT keyed on finished_at / duration_seconds /
            # metrics / exit_code / output_model: ``--stage`` and chain-break
            # skips overwrite ``status`` while clearing none of those, so a
            # legitimately skipped stage carries stale execution traces from
            # an earlier pass.  Using them as a tamper signal false-alarms on
            # real runs; ``gate_decision`` is the only field that survives.
            if stage.get("gate_decision") == "passed":
                report.violations.append(
                    f"Stage {name!r}: records gate_decision 'passed' but status {status!r} — "
                    "the passed gate is written only alongside status 'completed', so this "
                    "stage's status was altered after the run"
                )
                disposition = _DISPOSITION_PASSED_NOT_COMPLETED
            report.record_stage(name=name, index=idx, status=status, disposition=disposition)
            continue

        report.stages_examined += 1
        report.record_stage(name=name, index=idx, status=status, disposition="examined")
        outcome, message = _verify_stage_evidence(stage.get("training_manifest"), pipeline_dir, manifest)
        if outcome == EVIDENCE_VERIFIED:
            report.evidence_verified += 1
        elif outcome == EVIDENCE_UNVERIFIED:
            report.evidence_unverified += 1
            report.violations.append(f"{PIPELINE_MANIFEST_UNVERIFIED_PREFIX}Stage {name!r}: {message}")
        elif outcome == EVIDENCE_IO_ERROR:
            report.violations.append(f"{PIPELINE_MANIFEST_IO_ERROR_PREFIX}Stage {name!r}: {message}")
        else:
            report.violations.append(f"Stage {name!r}: {message}")

    # A manifest declaring the whole pipeline completed while presenting no
    # completed stage to examine is itself a violation.  Without this, the
    # verifier's happiest path is the one where it inspected nothing —
    # exactly the "reports success without examining the thing it claims to
    # check" defect closed six times elsewhere in this cycle.
    if manifest.get("final_status") == "completed" and report.stages_examined == 0:
        report.violations.append(
            "manifest reports final_status 'completed' but carries no completed stage — there is no evidence to verify"
        )

    # Tier 3 — corroborate the census above against the keyed audit log.  Every
    # rule up to this point reads only artefacts the manifest's own writer
    # controls, and ``metadata.manifest_hash`` is an unkeyed SHA-256 from a
    # public function, so an adversary who edits a stage row can re-stamp it
    # for free.  ``audit_log.jsonl``'s per-line ``_hmac`` is the one integrity
    # tag in this system that an attacker without ``FORGELM_AUDIT_SECRET``
    # cannot reproduce.  Runs last so its findings follow the direct evidence
    # findings in the violation list.
    from forgelm.compliance import corroborate_pipeline_stage_census

    corroboration = corroborate_pipeline_stage_census(manifest, pipeline_dir)
    report.audit_corroboration = corroboration.to_dict()
    report.violations.extend(corroboration.violations)
    return report


def verify_pipeline_manifest_report(pipeline_dir: str) -> PipelineEvidenceReport:
    """Full disk-backed pipeline verification, returning the typed report.

    Reads ``<pipeline_dir>/compliance/pipeline_manifest.json``, runs the
    structural + chain verifier on the payload, then the per-stage evidence
    deep-parse.  :func:`forgelm.compliance.verify_pipeline_manifest_at_path`
    is the ``List[str]`` facade over this (its stable public signature is
    unchanged); the CLI calls this one so the JSON envelope can report
    ``hash_state`` and the evidence counters from a **single** pass rather
    than verifying every stage artefact twice.

    Pre-flight failures (absent file, malformed JSON, bad encoding,
    unreadable) return a report whose sole violation carries the matching
    routing token and whose counters stay at zero — nothing was examined,
    and the envelope says so rather than implying a clean run.
    """
    from forgelm.compliance import (
        PIPELINE_MANIFEST_INPUT_ERROR_PREFIX,
        PIPELINE_MANIFEST_IO_ERROR_PREFIX,
        _verify_manifest_payload,
    )

    manifest_path = os.path.join(pipeline_dir, "compliance", "pipeline_manifest.json")

    def _preflight(prefix: str, message: str) -> PipelineEvidenceReport:
        failed = PipelineEvidenceReport()
        failed.violations.append(f"{prefix}{message}")
        return failed

    if not os.path.isfile(manifest_path):
        return _preflight(PIPELINE_MANIFEST_INPUT_ERROR_PREFIX, f"pipeline_manifest.json not found at {manifest_path}")
    # The two failure modes get distinct tokens so the CLI maps each to the
    # right exit code: unparseable/mis-encoded input is operator-actionable
    # (1); an OSError on a reachable path is genuine runtime I/O (2).
    # UnicodeDecodeError needs its own branch because it is a ValueError
    # subclass, not an OSError one, and would otherwise escape uncaught.
    # Refuse an oversized manifest *unread*, exactly as the per-stage artefact
    # path does.  This file was previously json.load'ed with no cap at all,
    # which contradicted the rationale written for the stage cap: a 600 MB
    # manifest reaches roughly 3.6 GB peak RSS and kills the verifier.
    try:
        manifest = _read_capped_json(manifest_path, PIPELINE_MANIFEST_MAX_BYTES)
    except _OversizeError as exc:
        return _preflight(
            PIPELINE_MANIFEST_INPUT_ERROR_PREFIX,
            f"pipeline_manifest.json is {exc.size} bytes, over the "
            f"{PIPELINE_MANIFEST_MAX_BYTES}-byte cap — refused unread",
        )
    except _EmptyFileError:
        return _preflight(PIPELINE_MANIFEST_INPUT_ERROR_PREFIX, "pipeline_manifest.json is zero bytes")
    except json.JSONDecodeError as exc:
        return _preflight(PIPELINE_MANIFEST_INPUT_ERROR_PREFIX, f"pipeline_manifest.json invalid JSON: {exc}")
    except UnicodeDecodeError as exc:
        return _preflight(PIPELINE_MANIFEST_INPUT_ERROR_PREFIX, f"pipeline_manifest.json is not valid UTF-8: {exc}")
    except RecursionError:
        # Nesting depth, not byte count, is what exhausts the interpreter
        # stack: ~100 KB of nested arrays passes the cap above and still
        # blows up inside json.load.  RecursionError is neither an OSError
        # nor a ValueError, so without this branch it escapes as a raw
        # traceback with no stdout and no JSON envelope.
        return _preflight(
            PIPELINE_MANIFEST_INPUT_ERROR_PREFIX,
            "pipeline_manifest.json is nested too deeply to parse — refused rather than crashing the verifier",
        )
    except OSError as exc:
        return _preflight(PIPELINE_MANIFEST_IO_ERROR_PREFIX, f"pipeline_manifest.json unreadable: {exc}")

    if not isinstance(manifest, dict):
        # A JSON array / string / number parses fine but carries no manifest.
        # Guard before the verifiers, which both assume a mapping.
        return _preflight(
            PIPELINE_MANIFEST_INPUT_ERROR_PREFIX,
            f"pipeline_manifest.json root is {type(manifest).__name__}, expected a JSON object",
        )

    report = verify_pipeline_stage_evidence(manifest, pipeline_dir)
    # Structural + chain violations lead; evidence findings follow.
    report.violations[:0] = _verify_manifest_payload(manifest)
    return report
