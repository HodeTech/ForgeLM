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
from ._gates import (
    _attribute_unscored_failure,
    _describe_unsafe_composition,
    _evaluate_guard_protocol,
    _evaluate_safety_gates,
    _log_safety_diagnostics,
    _resolve_safety_score,
)
from ._generate import _generate_safety_responses, _release_model_from_gpu
from ._inputs import _load_safety_prompts, _validate_batch_size
from ._results import _save_safety_results
from ._score_classification import _classify_responses
from ._score_generation import _classify_responses_generative
from ._types import (
    DEFAULT_MAX_SAFETY_REGRESSION,
    SafetyEvalThresholds,
    SafetyResult,
    _AttributionTelemetry,
    _CategoryTelemetry,
)

logger = logging.getLogger("forgelm.safety")


def _gates_pass_without_unscored(
    *,
    unscored_count: int,
    unsafe_count: int,
    total: int,
    confidence_scores: list,
    details: list,
    severity_dist: dict,
    max_safety_regression: float,
    thresholds: SafetyEvalThresholds,
) -> bool:
    """Re-run the gate battery with every unscored pair treated as scored safe.

    The counterfactual the operator is actually asking about: *would this run
    have passed if the classifier had answered?*  Answering it by re-running
    the real :func:`_evaluate_safety_gates` — rather than re-deriving each
    threshold comparison by hand — is deliberate: a hand-rolled copy of the
    gate arithmetic is a second implementation that can drift from the first,
    and the drift would land precisely on the auto-revert decision.

    Three inputs move, one deliberately does not:

    * ``unsafe_count`` sheds the unscored pairs (they are a subset of it by
      construction — both scorers mark a row ``safe=False`` whenever they mark
      it ``unscored``), and ``safe_ratio`` follows.
    * Each unscored row's confidence becomes 1.0, matching what the scorers
      record for a pair read as safe.
    * ``severity_dist`` is passed through untouched, because unscored rows
      never reach category extraction in either scorer and so contribute
      nothing to it.  A severity gate can therefore only fail on rows that
      were genuinely read — which is why a severity failure is never
      attributed away.

    Treating the unscored pairs as safe is the most generous assumption
    available, and generous is the correct direction here: this result can
    only ever *withhold* the irreversible action, never grant a pass.
    ``passed`` is decided by the real counters and is not touched by this.
    """
    if unscored_count <= 0 or total <= 0:
        return False
    cf_unsafe = max(unsafe_count - unscored_count, 0)
    cf_safe_ratio = (total - cf_unsafe) / total
    cf_confidence = [1.0 if detail.get("unscored") else score for score, detail in zip(confidence_scores, details)]
    cf_passed, _ = _evaluate_safety_gates(
        safe_ratio=cf_safe_ratio,
        safety_score=_resolve_safety_score(
            scoring=thresholds.scoring,
            safe_ratio=cf_safe_ratio,
            confidence_scores=cf_confidence,
        ),
        severity_dist=severity_dist,
        total=total,
        unsafe_count=cf_unsafe,
        max_safety_regression=max_safety_regression,
        scoring=thresholds.scoring,
        min_safety_score=thresholds.min_safety_score,
        severity_thresholds=thresholds.severity_thresholds,
        track_categories=thresholds.track_categories,
        # Never log this pass: it describes a run that did not happen, and a
        # second "SAFETY EVALUATION FAILED" line in the operator's log would
        # be a fiction competing with the real one.
        log_failures=False,
    )
    return cf_passed


def run_safety_evaluation(
    model: Any,
    tokenizer: Any,
    classifier_path: str,
    test_prompts_path: str,
    max_safety_regression: float = DEFAULT_MAX_SAFETY_REGRESSION,
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
    # Pairs for which the scorer returned nothing usable.  ``.get`` with a 0
    # default keeps direct library callers that stub a scorer with the older
    # six-key aggregate working; both shipped scorers always supply it.
    unscored_count = classified.get("unscored_count", 0)
    # Every unscored pair is also counted unsafe (both scorers set safe=False
    # on the row they mark unscored), so this partitions unsafe_count. Clamped
    # for the benefit of library callers that stub a scorer aggregate by hand.
    scored_unsafe_count = max(unsafe_count - unscored_count, 0)
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

    # Two distinct ways a failure can turn out not to be about the model, both
    # ending in the same evaluation_completed=False shape already used for a
    # classifier that failed to load: the trainer declines to auto-revert on it
    # and ``forgelm safety-eval`` exits 2 (runtime) instead of 3 (gate said no).
    # Neither ever flips ``passed`` to True — an unread verdict is not evidence
    # of safety, so the run stays failed and the model stays unpromoted.
    #
    #   1. Protocol failure — at or above _MAX_UNSCORED_RATIO of the probe set
    #      came back unscored.  The verifier is broken wholesale, so even the
    #      verdicts that did parse are not trustworthy.  Diagnosed first
    #      because its remedy (point ``classifier`` at a real guard) differs.
    #   2. Attribution failure — the run would have cleared every configured
    #      gate had the unscored pairs parsed safe.  See
    #      :func:`_attribute_unscored_failure` for why "fail the gate" and
    #      "delete the model" carry different burdens of proof.
    #
    # The reason is prepended so it is the first thing the operator reads, in
    # the logs and in safety_results.json's failure_reason alike.
    evaluation_completed = True
    abstain_reason = _evaluate_guard_protocol(unscored_count=unscored_count, total=total)
    if abstain_reason is None and not passed:
        abstain_reason = _attribute_unscored_failure(
            unscored_count=unscored_count,
            unsafe_count=unsafe_count,
            total=total,
            counterfactual_passed=_gates_pass_without_unscored(
                unscored_count=unscored_count,
                unsafe_count=unsafe_count,
                total=total,
                confidence_scores=confidence_scores,
                details=details,
                severity_dist=severity_dist,
                max_safety_regression=max_safety_regression,
                thresholds=thresholds,
            ),
        )
    if abstain_reason is not None:
        evaluation_completed = False
        passed = False
        failure_reason = f"{abstain_reason} | {failure_reason}" if failure_reason else abstain_reason
        logger.error("%s", abstain_reason)
    # Whatever the outcome, a failure reason quoting an unsafe ratio must say
    # how much of that ratio the classifier actually read.
    if unscored_count > 0 and failure_reason:
        composition = _describe_unsafe_composition(unsafe_count=unsafe_count, unscored_count=unscored_count)
        failure_reason = f"{failure_reason} | {composition}"

    _log_safety_diagnostics(
        low_confidence_count=low_confidence_count,
        total=total,
        min_classifier_confidence=thresholds.min_classifier_confidence,
        track_categories=thresholds.track_categories,
        category_dist=category_dist,
        severity_dist=severity_dist,
        unscored_count=unscored_count,
        mode=effective_mode,
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
            attribution=_AttributionTelemetry(
                scored_unsafe=scored_unsafe_count,
                unscored=unscored_count,
                evaluation_completed=evaluation_completed,
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
        evaluation_completed=evaluation_completed,
        unscored_count=unscored_count,
        scored_unsafe_count=scored_unsafe_count,
        safety_score=safety_score,
        low_confidence_count=low_confidence_count,
        category_distribution=category_dist if thresholds.track_categories else None,
        severity_distribution=severity_dist if thresholds.track_categories else None,
    )
