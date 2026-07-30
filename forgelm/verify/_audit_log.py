"""Audit-log failure classification.

Owns the predicate that splits an audit-log verification failure into a
tamper verdict (exit 6) and an input verdict (exit 1).  The verifier
itself stays in :mod:`forgelm.compliance`, which owns the append-only
on-disk format and its canonicalisation — see the package docstring.
"""

from __future__ import annotations

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
