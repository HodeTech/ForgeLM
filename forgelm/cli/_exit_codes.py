"""ForgeLM CLI exit-code contract.

These integer codes are part of the public CLI surface — CI/CD pipelines
branch on them. Any other value (e.g. signal-derived 128+N codes) is
clamped to :data:`EXIT_TRAINING_ERROR` before propagating.
"""

from __future__ import annotations

EXIT_SUCCESS = 0
EXIT_CONFIG_ERROR = 1
EXIT_TRAINING_ERROR = 2
EXIT_EVAL_FAILURE = 3
EXIT_AWAITING_APPROVAL = 4
# 5: operator cancelled the wizard before producing a config (e.g.
# Ctrl-C, declined to save, non-tty stdin refusal).  Distinct from
# ``EXIT_SUCCESS`` so CI can tell "wizard finished with a config" apart
# from "wizard never saved anything".  Picked 5 (the next free integer
# in the public 0-4 contract) rather than 130 (signal-derived) because
# clean cancels through `cancel`/`q` aren't signal-driven.
EXIT_WIZARD_CANCELLED = 5
# 6: an artefact was located and read successfully, and its *integrity
# check failed* — an Annex IV manifest hash mismatch, a broken audit-log
# hash chain, a tampered pipeline manifest, a GGUF SHA-256 sidecar
# mismatch, or model files that no longer match ``model_integrity.json``.
#
# Split out of ``EXIT_CONFIG_ERROR`` because the two are different
# incidents with different owners: a mistyped path is an operator typo
# (1 — fix the command), whereas a hash that no longer matches is a
# security event (6 — page whoever owns the artefact).  Both used to exit
# 1, so a CI pipeline could not tell "you pointed at the wrong file" from
# "this model was modified after sign-off".
#
# Only the ``verify-*`` subcommands emit this code, and only after the
# artefact has been read: an unreadable/missing/malformed artefact stays
# on 1 (caller input) or 2 (runtime I/O) as before.  The per-verifier
# classification lives in ``forgelm/verify.py``'s ``is_*_integrity_failure``
# predicates so the split is structural, not string-matched.
EXIT_INTEGRITY_FAILURE = 6

_PUBLIC_EXIT_CODES = frozenset(
    {
        EXIT_SUCCESS,
        EXIT_CONFIG_ERROR,
        EXIT_TRAINING_ERROR,
        EXIT_EVAL_FAILURE,
        EXIT_AWAITING_APPROVAL,
        EXIT_WIZARD_CANCELLED,
        EXIT_INTEGRITY_FAILURE,
    }
)


def _clamp_exit_code(code: int) -> int:
    """Map any non-public exit code to :data:`EXIT_TRAINING_ERROR`.

    Enforces the module-docstring invariant at the dispatch seam: a
    dispatcher that returns a computed or signal-derived code (128+N) is
    coerced to the runtime-error code rather than leaking verbatim to the
    shell and breaking CI consumers that branch only on the 0-6 contract.
    """
    return code if code in _PUBLIC_EXIT_CODES else EXIT_TRAINING_ERROR
