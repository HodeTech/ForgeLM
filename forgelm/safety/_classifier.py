"""Safety-classifier selection, loading, revision pinning and load-failure audit.

Owns which guard checkpoint is loaded, through which mode
(``auto`` / ``classification`` / ``generation``), pinned at which Hub
revision, and what is written to the EU AI Act Article 15 audit trail when
the load fails or is refused.
"""

import logging
from typing import Any, Optional, Tuple

logger = logging.getLogger("forgelm.safety")


# Well-known generative Llama-Guard checkpoints (LlamaForCausalLM).  These emit
# their safety verdict as *generated text* ("safe" / "unsafe\nS<code>"), so they
# cannot be scored through the ``pipeline("text-classification")`` path — that
# path attaches a randomly-initialized sequence-classification head (see
# ``_reject_uninitialized_classifier_head``).  ForgeLM scores these checkpoints
# through generation-based Llama-Guard scoring instead (see
# ``_classify_responses_generative``); ``_resolve_classifier_mode`` routes any
# member of this set to the generation path under the default
# ``classifier_mode="auto"``, so the shipped default ``meta-llama/Llama-Guard-3-8B``
# works out of the box.  Membership drives two things: (1) auto-routing to the
# generation scorer, and (2) a fail-fast pre-flight when an operator explicitly
# forces ``classifier_mode="classification"`` on one of these — a genuine
# misconfiguration the text-classification pipeline can never score.  Compared
# case-insensitively against ``classifier_path``.
_GENERATION_ONLY_CLASSIFIERS: frozenset[str] = frozenset(
    {
        "meta-llama/llama-guard-3-8b",
        "meta-llama/llama-guard-3-8b-int8",
        "meta-llama/llama-guard-3-1b",
        "meta-llama/meta-llama-guard-2-8b",
        "meta-llama/llamaguard-7b",
    }
)


def _reject_generation_only_classifier(classifier_path: str) -> None:
    """Fail fast when ``classifier_mode="classification"`` forces the pipeline on a generative guard.

    The published Llama-Guard checkpoints — including the config default
    ``meta-llama/Llama-Guard-3-8B`` — are generative ``LlamaForCausalLM`` models
    with no trained sequence-classification head, so they can never produce a
    meaningful verdict through ``pipeline("text-classification")``.  ForgeLM DOES
    support them via generation-based scoring (``classifier_mode`` ``"auto"`` /
    ``"generation"``); this pre-flight fires only when an operator explicitly
    forces ``classifier_mode="classification"`` on one — a genuine
    misconfiguration — so the actionable error surfaces at eval start rather than
    after a multi-GB download and a full response-generation pass.

    Raises:
        RuntimeError: if ``classifier_path`` names a known generation-only guard.
    """
    if classifier_path.strip().lower() in _GENERATION_ONLY_CLASSIFIERS:
        raise RuntimeError(
            f"Safety classifier {classifier_path!r} is a generative Llama-Guard "
            "checkpoint (LlamaForCausalLM) and cannot be scored through ForgeLM's "
            "text-classification pipeline: it has no trained sequence-classification "
            "head, so every verdict would be meaningless (and with auto_revert on it "
            "would delete a good model). This checkpoint IS supported via "
            "generation-based scoring — set evaluation.safety.classifier_mode to "
            "'auto' (the default) or 'generation'. classifier_mode='classification' "
            "requires a checkpoint whose head carries 'safe'/'unsafe' labels."
        )


def _reject_uninitialized_classifier_head(classifier: Any, classifier_path: str) -> None:
    """Refuse a causal-LM checkpoint loaded as a text-classification head.

    The shipped default ``meta-llama/Llama-Guard-3-8B`` is a generative
    ``LlamaForCausalLM`` whose safety verdict is produced as *generated text*
    (``safe`` / ``unsafe\\nS<code>``).  Loading it through
    ``pipeline("text-classification")`` instantiates a
    ``...ForSequenceClassification`` whose score head is **absent from the
    checkpoint and randomly initialized** — every label becomes
    ``LABEL_0``/``LABEL_1``, ``is_safe`` is False for every response, the gate
    always fails (and with auto-revert deletes a good model), and the
    advertised S1–S14 harm-category parsing can never see a Llama-Guard label.

    ForgeLM's safety pass is label-driven (it reads ``safe``/``unsafe`` text
    classification labels), so it requires a checkpoint that actually carries
    a *trained* sequence-classification head with safe/unsafe label names.
    Detect the causal-LM-as-classifier mismatch at load time and refuse with
    an actionable error instead of silently producing garbage verdicts
    (F-P3-FABLE-17).
    """
    model = getattr(classifier, "model", None)
    config = getattr(model, "config", None)
    if config is None:
        return
    architectures = getattr(config, "architectures", None) or []
    # If the checkpoint was *authored* for causal LM (its config.architectures
    # names a ...ForCausalLM / generative class), the score head loaded by the
    # text-classification pipeline is newly-initialized — not a real harm
    # classifier.
    causal_lm = any(arch.endswith("ForCausalLM") or arch.endswith("LMHeadModel") for arch in architectures)
    # A genuine harm classifier names safe/unsafe (or S-code) labels; a
    # placeholder head only exposes the default LABEL_N vocabulary
    # (LABEL_0/LABEL_1/LABEL_2/...).
    id2label = getattr(config, "id2label", {}) or {}
    labels = {str(v).lower() for v in id2label.values()}
    # An empty id2label (absent from config, or explicitly {}) is at least as
    # suspicious as an all-LABEL_N vocabulary: both signal a randomly-initialized
    # classification head rather than a trained harm classifier.  The previous
    # ``bool(labels) and all(...)`` short-circuited to False on the empty set,
    # silently bypassing the guard for causal-LM checkpoints with no id2label at
    # all (F-M-21).
    placeholder_labels = not labels or all(
        lbl.startswith("label_") and lbl[len("label_") :].isdigit() for lbl in labels
    )
    if causal_lm and placeholder_labels:
        raise RuntimeError(
            f"Safety classifier {classifier_path!r} is a causal language model "
            f"(architectures={architectures}) loaded as a text-classification head; "
            "its classification head is randomly initialized "
            f"(labels={sorted(labels)}), so every verdict would be meaningless. "
            "Provide a checkpoint with a trained sequence-classification head whose "
            "labels include 'safe'/'unsafe' (e.g. a fine-tuned harm classifier), or "
            "score a generative Llama-Guard checkpoint with "
            "evaluation.safety.classifier_mode='auto'/'generation'."
        )


def _emit_classifier_load_failed_audit(audit_logger: Any, classifier_path: str, reason: str) -> None:
    """Best-effort Article 15 record-keeping for a safety-classifier outage.

    A failure to load — or a fail-fast rejection of — the safety classifier is a
    safety-gate outage, so surface it in the append-only audit trail, not only in
    process logs (F-compliance-120). Shared by ``_load_safety_classifier`` and the
    ``run_safety_evaluation`` top pre-flight so both failure paths audit
    identically. Best-effort: an audit failure here must never mask the primary
    classifier error the caller is handling.
    """
    if audit_logger is None:
        return
    try:
        audit_logger.log_event(
            "audit.classifier_load_failed",
            classifier=classifier_path,
            reason=str(reason)[:500],
        )
    except Exception as audit_exc:  # noqa: BLE001 — best-effort: audit emission must not mask the primary classifier failure.
        logger.warning("Failed to emit classifier_load_failed audit event: %s", audit_exc)


def _load_safety_classifier(classifier_path: str, audit_logger: Any, classifier_revision: Optional[str] = None) -> Any:
    """Load the HF text-classification pipeline; emit Article 15 audit on failure.

    Returns the classifier or raises a ``RuntimeError`` whose message is
    the original load failure. ``trust_remote_code=False`` is pinned so a
    future Transformers default flip can't silently start running
    classifier-side custom code on the production safety pass.

    ``classifier_revision`` is ``evaluation.safety.classifier_revision``.  The
    classifier decides the auto-revert verdict, so an unpinned upstream
    re-tune moves the pass/fail line with no config diff to point at.
    """
    from transformers import pipeline

    from ..model import ROLE_SAFETY_CLASSIFIER, prepare_revision_pin, record_loaded_revision

    try:
        # Reject known generation-only guards before the multi-GB download, so a
        # direct caller of this helper (bypassing run_safety_evaluation's own
        # pre-flight) still fails fast — and the audit event below still fires.
        _reject_generation_only_classifier(classifier_path)
        pin, revision_record = prepare_revision_pin(
            classifier_path, role=ROLE_SAFETY_CLASSIFIER, requested=classifier_revision
        )
        classifier = pipeline(
            "text-classification",
            model=classifier_path,
            device_map="auto",
            trust_remote_code=False,
            revision=pin,
        )
        _reject_uninitialized_classifier_head(classifier, classifier_path)
        record_loaded_revision(revision_record)
        return classifier
    except Exception as e:  # noqa: BLE001 — best-effort: HF pipeline surface raises a wide error tail (OSError/ValueError/RuntimeError/HFValidationError/repo errors); we re-raise as RuntimeError below so the caller still sees the failure.
        logger.exception("Failed to load safety classifier")
        # Closure plan Faz 3 (F-compliance-120): emit a record-keeping event
        # so safety classifier outages are visible in the EU AI Act Article 15
        # (Model Integrity) audit trail, not only in process logs.
        _emit_classifier_load_failed_audit(audit_logger, classifier_path, str(e))
        raise RuntimeError(str(e)) from e


def _resolve_classifier_mode(classifier_mode: str, classifier_path: str) -> str:
    """Resolve the effective scoring path: ``"generation"`` or ``"classification"``.

    ``"generation"`` / ``"classification"`` are honoured verbatim.  ``"auto"``
    (and any unrecognised value, for the direct-library-caller case that bypasses
    the ``SafetyConfig`` ``Literal``) picks generation for a known generative
    Llama-Guard checkpoint (membership in :data:`_GENERATION_ONLY_CLASSIFIERS`)
    and text-classification for everything else.
    """
    mode = (classifier_mode or "auto").strip().lower()
    if mode in ("generation", "classification"):
        return mode
    if classifier_path.strip().lower() in _GENERATION_ONLY_CLASSIFIERS:
        return "generation"
    return "classification"


def _load_generative_guard(
    classifier_path: str, audit_logger: Any, classifier_revision: Optional[str] = None
) -> Tuple[Any, Any]:
    """Load a generative Llama-Guard checkpoint (``AutoModelForCausalLM`` + tokenizer).

    Mirrors :func:`_load_safety_classifier`'s failure contract: on any load error
    emit the Article 15 ``audit.classifier_load_failed`` event and re-raise as
    ``RuntimeError`` so the caller returns the same infrastructure-failure shape.
    ``trust_remote_code=False`` is pinned so the production safety pass never runs
    checkpoint-side custom code.

    Guard and tokenizer share one revision pin: a verdict produced by weights
    from one commit and a chat template from another is not the verdict the
    manifest would describe.
    """
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    from ..model import ROLE_SAFETY_CLASSIFIER, prepare_revision_pin, record_loaded_revision

    try:
        pin, revision_record = prepare_revision_pin(
            classifier_path, role=ROLE_SAFETY_CLASSIFIER, requested=classifier_revision
        )
        tokenizer = AutoTokenizer.from_pretrained(classifier_path, trust_remote_code=False, revision=pin)
        model = AutoModelForCausalLM.from_pretrained(
            classifier_path,
            # ``dtype`` is the transformers-5 name for the former ``torch_dtype``.
            dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
            device_map="auto" if torch.cuda.is_available() else None,
            trust_remote_code=False,
            revision=pin,
        )
        model.eval()
        record_loaded_revision(revision_record)
        return model, tokenizer
    except Exception as e:  # noqa: BLE001 — best-effort: HF loader surface raises a wide error tail (OSError/ValueError/RuntimeError/HFValidationError/repo errors); we re-raise as RuntimeError below so the caller still sees the failure.
        logger.exception("Failed to load generative safety guard")
        _emit_classifier_load_failed_audit(audit_logger, classifier_path, str(e))
        raise RuntimeError(str(e)) from e
