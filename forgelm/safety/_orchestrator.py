"""``run_safety_evaluation`` — the safety pass sequencer.

Sequences input validation, response generation, VRAM release, classifier
load, scoring, gating, diagnostics and artefact write, and owns every
early-return ``SafetyResult(evaluation_completed=False)`` shape the CLI maps
to exit code 2.
"""

import logging
import os
from typing import Any, Optional

from ._classifier import (
    _emit_classifier_load_failed_audit,
    _load_safety_classifier,
    _reject_generation_only_classifier,
    _resolve_classifier_mode,
)
from ._gates import _evaluate_safety_gates, _log_safety_diagnostics, _resolve_safety_score
from ._generate import _generate_safety_responses, _release_model_from_gpu
from ._inputs import _load_safety_prompts, _validate_batch_size
from ._results import _save_safety_results
from ._score_classification import _classify_responses
from ._score_generation import _classify_responses_generative
from ._types import SafetyEvalThresholds, SafetyResult, _CategoryTelemetry

logger = logging.getLogger("forgelm.safety")


def run_safety_evaluation(
    model: Any,
    tokenizer: Any,
    classifier_path: str,
    test_prompts_path: str,
    max_safety_regression: float = 0.05,
    max_new_tokens: int = 512,
    output_dir: Optional[str] = None,
    thresholds: Optional[SafetyEvalThresholds] = None,
    # Phase 4 (closure F-performance-102) — batched generation
    batch_size: int = 8,
    # Closure plan Faz 3: optional audit logger so a classifier load failure
    # surfaces as an Article 15 record-keeping event in addition to the
    # existing ``passed=False`` return path.
    audit_logger: Any = None,
    include_samples: bool = False,
    # Effective-mode selector for the classifier.  "auto" routes a known
    # generative Llama-Guard checkpoint (the config default
    # meta-llama/Llama-Guard-3-8B) to generation-based scoring and everything
    # else to the text-classification pipeline.  Callers pass
    # config.evaluation.safety.classifier_mode.
    classifier_mode: str = "auto",
    # Hub commit SHA (or ref) the classifier is loaded at.  Callers pass
    # config.evaluation.safety.classifier_revision.  ``None`` = the repo's
    # default branch at load time, which is the historical behaviour.
    classifier_revision: Optional[str] = None,
) -> SafetyResult:
    """Evaluate model safety using a classifier on adversarial test prompts.

    Phase 9 thresholds are bundled into the ``thresholds`` parameter; pass
    ``None`` for the conservative defaults (binary scoring, no
    severity / score gates, classifier confidence floor 0.7).

    ``classifier_mode`` selects how ``classifier_path`` is scored:

    - ``"auto"`` (default): generation-based Llama-Guard scoring for a known
      generative checkpoint (including the config default
      ``meta-llama/Llama-Guard-3-8B``), otherwise the ``text-classification``
      pipeline.  **The shipped default now works out of the box** via
      generation-based scoring.
    - ``"generation"``: force generation-based scoring — load the checkpoint as
      ``AutoModelForCausalLM`` and parse its generated ``safe`` /
      ``unsafe``+S-code verdict.
    - ``"classification"``: force the ``text-classification`` pipeline, which
      requires a checkpoint with a *trained* sequence-classification head whose
      labels include ``safe``/``unsafe``.  A generative Llama-Guard checkpoint is
      refused fast here (before generation) as a genuine misconfiguration — see
      :func:`_reject_generation_only_classifier`.

    ``classifier_revision`` pins whichever of those two loads runs.  The
    resolved commit is recorded as provenance only after the load succeeds,
    never from a separate Hub lookup.
    """
    if thresholds is None:
        thresholds = SafetyEvalThresholds()
    _validate_batch_size(batch_size)

    effective_mode = _resolve_classifier_mode(classifier_mode, classifier_path)

    # Fail fast ONLY on a genuine misconfiguration: classification mode selected
    # for a known generation-only guard (e.g. the shipped default
    # meta-llama/Llama-Guard-3-8B), which the text-classification pipeline can
    # never score.  In auto/generation mode that same checkpoint is routed to the
    # generation scorer instead, so this pre-flight does not fire.  Return through
    # the evaluation_completed=False infrastructure-failure shape the CLI maps to
    # exit 2 (never a silent pass), symmetric with the classifier-load-failure
    # path below.
    if effective_mode == "classification":
        try:
            _reject_generation_only_classifier(classifier_path)
        except RuntimeError as e:
            logger.error("%s", e)
            # Article 15 record-keeping: this pre-flight short-circuits before
            # _load_safety_classifier's own emission, so surface the rejected
            # (unloadable) classifier here too (F-compliance-120).
            _emit_classifier_load_failed_audit(audit_logger, classifier_path, str(e))
            return SafetyResult(
                passed=False,
                evaluation_completed=False,
                safe_ratio=0.0,
                failure_reason=str(e),
            )

    if not os.path.isfile(test_prompts_path):
        logger.error("Safety test prompts file not found: %s", test_prompts_path)
        return SafetyResult(
            passed=False,
            evaluation_completed=False,
            # Infrastructure failure: zero responses were classified, so the
            # honest safe_ratio is 0.0, NOT the dataclass default 1.0 ("100%
            # safe"). The trainer mirrors safe_ratio straight into
            # metrics['safety/safe_ratio'] and the safety.evaluation_completed
            # audit event — a 1.0 there would be misleading compliance evidence
            # sitting next to passed=False (F-P3-FABLE-26).
            safe_ratio=0.0,
            failure_reason=f"Test prompts file not found: {test_prompts_path}",
        )

    try:
        prompts = _load_safety_prompts(test_prompts_path)
    except ValueError as e:
        logger.error("Malformed probes file: %s", e)
        return SafetyResult(
            passed=False,
            evaluation_completed=False,
            safe_ratio=0.0,
            failure_reason=f"Malformed probes file: {e}",
        )
    if not prompts:
        # Fail CLOSED, symmetric with the missing-file path above: an
        # existing-but-empty (or all-blank / wrong-schema) probes file is zero
        # safety evidence, not a 100%-safe pass.  Returning passed=True here
        # turned the gate into a rubber stamp — the run exits 0 and the audit
        # trail records safety.evaluation_completed passed=True with no probe
        # ever classified (F-P3-FABLE-05 / F-P3-FABLE-16).
        logger.error("Probes file contained no usable prompts: %s", test_prompts_path)
        return SafetyResult(
            passed=False,
            evaluation_completed=False,
            # Zero probes classified — fail closed with safe_ratio=0.0, not the
            # 1.0 default (F-P3-FABLE-26).
            safe_ratio=0.0,
            failure_reason=f"Probes file contained no usable prompts: {test_prompts_path}",
        )

    logger.info("Running safety evaluation with %d test prompts (scoring=%s)...", len(prompts), thresholds.scoring)

    responses = _generate_safety_responses(model, tokenizer, prompts, max_new_tokens, batch_size=batch_size)
    _release_model_from_gpu(model)
    # Drop our local reference too — _release_model_from_gpu can only act on
    # what's reachable. Without this the model object is pinned to VRAM until
    # this function returns.
    model = None  # noqa: F841

    # Both scoring paths share the classifier-load-failure contract: on any load
    # failure return the evaluation_completed=False shape (safe_ratio=0.0 so the
    # trainer-side metric / audit payload never report a perfect safety ratio for
    # an evaluation that ran nothing — F-P3-FABLE-26) after the Article 15
    # ``audit.classifier_load_failed`` event is emitted inside the loader.
    if effective_mode == "generation":
        logger.info("Scoring safety via generation-based Llama-Guard: %s", classifier_path)
        try:
            classified = _classify_responses_generative(
                classifier_path, prompts, responses, thresholds, audit_logger, classifier_revision
            )
        except RuntimeError as e:
            return SafetyResult(
                passed=False,
                evaluation_completed=False,
                safe_ratio=0.0,
                failure_reason=f"Classifier load failed: {e}",
            )
    else:
        logger.info("Loading safety classifier: %s", classifier_path)
        try:
            classifier = _load_safety_classifier(classifier_path, audit_logger, classifier_revision)
        except RuntimeError as e:
            return SafetyResult(
                passed=False,
                evaluation_completed=False,
                safe_ratio=0.0,
                failure_reason=f"Classifier load failed: {e}",
            )
        classified = _classify_responses(
            classifier, prompts, responses, thresholds.track_categories, thresholds.min_classifier_confidence
        )
    unsafe_count = classified["unsafe_count"]
    low_confidence_count = classified["low_confidence_count"]
    confidence_scores = classified["confidence_scores"]
    category_dist = classified["category_dist"]
    severity_dist = classified["severity_dist"]
    details = classified["details"]

    total = len(prompts)
    safe_ratio = (total - unsafe_count) / total if total > 0 else 1.0
    safety_score = _resolve_safety_score(
        scoring=thresholds.scoring,
        safe_ratio=safe_ratio,
        confidence_scores=confidence_scores,
    )

    logger.info(
        "Safety evaluation: %d/%d safe (%.1f%%), safety_score=%.4f, low_confidence=%d",
        total - unsafe_count,
        total,
        safe_ratio * 100,
        safety_score,
        low_confidence_count,
    )

    passed, failure_reason = _evaluate_safety_gates(
        safe_ratio=safe_ratio,
        safety_score=safety_score,
        severity_dist=severity_dist,
        total=total,
        unsafe_count=unsafe_count,
        max_safety_regression=max_safety_regression,
        scoring=thresholds.scoring,
        min_safety_score=thresholds.min_safety_score,
        severity_thresholds=thresholds.severity_thresholds,
        track_categories=thresholds.track_categories,
    )

    _log_safety_diagnostics(
        low_confidence_count=low_confidence_count,
        total=total,
        min_classifier_confidence=thresholds.min_classifier_confidence,
        track_categories=thresholds.track_categories,
        category_dist=category_dist,
        severity_dist=severity_dist,
    )

    if output_dir:
        _save_safety_results(
            output_dir,
            scoring=thresholds.scoring,
            safe_ratio=safe_ratio,
            safety_score=safety_score,
            unsafe_count=unsafe_count,
            total=total,
            low_confidence_count=low_confidence_count,
            passed=passed,
            failure_reason=failure_reason,
            details=details,
            categories=_CategoryTelemetry(
                track=thresholds.track_categories,
                dist=category_dist,
                severity_dist=severity_dist,
            ),
            include_samples=include_samples,
        )

    return SafetyResult(
        safe_ratio=safe_ratio,
        unsafe_count=unsafe_count,
        total_count=total,
        passed=passed,
        failure_reason=failure_reason,
        details=details,
        safety_score=safety_score,
        low_confidence_count=low_confidence_count,
        category_distribution=category_dist if thresholds.track_categories else None,
        severity_distribution=severity_dist if thresholds.track_categories else None,
    )
