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

Package layout
--------------

The single ``verify.py`` module crossed the 1000-LOC ceiling on 2026-07-20
and was split here.  ``tools/check_module_size.py``'s deferral entry named
the reason it had to be its own diff: the split moves the exit-code routing
tokens that both the CLI and the tests pin, so it is done **behaviour-neutral
and alone**, before any verdict semantics change.

- :mod:`._annex_iv` — Annex IV §1-9 completeness + manifest hash
- :mod:`._pipeline_evidence` — the chain-level per-stage evidence walk
- :mod:`._gguf` — GGUF magic / metadata / sidecar
- :mod:`._model_integrity` — Art. 15 model-directory re-hash
- :mod:`._audit_log` — audit-log failure classification
- :mod:`._io_safety` — the size-capped, fail-closed JSON read the chain verifiers share

**What the facade carries, and why not more.**  Every *public* name the old
module exported is re-exported below — the three stable-tier verifiers, their
result dataclasses, the four ``is_*_integrity_failure`` predicates, the
pipeline-evidence entry points and the report constants — so
``from forgelm.verify import X`` is unchanged for every caller of the public
surface.

Private helpers and constants deliberately stay on their owning submodule and
are **not** re-exported.  That is not tidiness; it is what keeps a whole class
of silent test failure from existing.  Rebinding a name *here* does not touch
the reference the owning submodule resolves at call time, so a facade-level
patch of, say, ``_read_capped_json`` would leave the real function running
while the test reported success — a test that passes while exercising nothing,
the exact failure mode ``docs/standards/testing.md`` and the ``add-test``
skill warn about.  Because the name is absent from this module,
``mock.patch("forgelm.verify._read_capped_json", …)`` and
``monkeypatch.setattr`` now raise ``AttributeError`` instead: the trap
disarms itself, and the correct target —
``forgelm.verify._pipeline_evidence._read_capped_json`` — is the only one that
works.  ``tests/test_verification_toolbelt.py`` pins that behaviour.
"""

from __future__ import annotations

from ._annex_iv import (  # noqa: F401
    VerifyAnnexIVResult,
    is_annex_iv_integrity_failure,
    verify_annex_iv_artifact,
    verify_annex_iv_payload,
)
from ._audit_log import (  # noqa: F401
    is_audit_integrity_failure,
)
from ._gguf import (  # noqa: F401
    VerifyGgufResult,
    is_gguf_integrity_failure,
    verify_gguf,
)
from ._model_integrity import (  # noqa: F401
    VerifyIntegrityResult,
    is_model_integrity_failure,
    verify_integrity,
)
from ._pipeline_evidence import (  # noqa: F401
    EVIDENCE_IO_ERROR,
    EVIDENCE_UNVERIFIED,
    EVIDENCE_VERIFIED,
    EVIDENCE_VIOLATION,
    HASH_STATE_ABSENT,
    HASH_STATE_MISMATCH,
    HASH_STATE_VERIFIED,
    PIPELINE_MANIFEST_MAX_BYTES,
    STAGE_EVIDENCE_MAX_BYTES,
    PipelineEvidenceReport,
    verify_pipeline_manifest_report,
    verify_pipeline_stage_evidence,
)

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
