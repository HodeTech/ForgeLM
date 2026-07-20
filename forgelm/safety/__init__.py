"""Post-training safety evaluation.

Phase 6: Binary safe/unsafe classification with auto-revert.
Phase 9: Confidence-weighted scoring, harm categories, severity levels,
         absolute unsafe-ratio gating, low-confidence alerts.

Note: ``max_safety_regression`` is an absolute post-training unsafe-ratio
threshold, not a regression-vs-baseline bound — no pre-training safety
measurement is taken anywhere (unlike the eval-loss gate's baseline). The
config field description (``SafetyConfig.max_safety_regression``) is accurate;
the name reads as baseline-relative but the implemented semantics are absolute.

Note: ``scoring="confidence_weighted"`` degenerates to binary (mathematically
equal to ``safe_ratio``) under ``classifier_mode="generation"`` — the
shipped default for the shipped default classifier
(``meta-llama/Llama-Guard-3-8B``). Generation-based scoring only ever
greedily decodes a categorical ``safe``/``unsafe`` verdict; it never samples
a token-probability distribution, so ``_classify_one_generative`` can only
synthesize a placeholder confidence (1.0 well-formed, 0.0 malformed), not a
real guard probability. See ``_resolve_safety_score`` and
``_classify_one_generative`` for the implementation and
``docs/usermanuals/en/evaluation/safety.md`` for the operator-facing note.
"""

# Sub-module aliases — the historical single-module layout put every helper in
# one namespace, so ``monkeypatch.setattr(forgelm.safety, ...)`` rebound the exact
# global the caller resolved.  After the split a caller resolves its collaborators
# through the owning sub-module, so patch targets address these aliases (mirrors
# ``forgelm.data_audit.__init__``).
from . import (
    _classifier,  # noqa: F401 — re-export for tests
    _gates,  # noqa: F401 — re-export for tests
    _generate,  # noqa: F401 — re-export for tests
    _inputs,  # noqa: F401 — re-export for tests
    _orchestrator,  # noqa: F401 — re-export for tests
    _results,  # noqa: F401 — re-export for tests
    _score_classification,  # noqa: F401 — re-export for tests
    _score_generation,  # noqa: F401 — re-export for tests
    _types,  # noqa: F401 — re-export for tests
)
from ._classifier import (
    _GENERATION_ONLY_CLASSIFIERS,  # noqa: F401 — re-export for tests
    _emit_classifier_load_failed_audit,  # noqa: F401 — re-export for tests
    _load_generative_guard,  # noqa: F401 — re-export for tests
    _load_safety_classifier,  # noqa: F401 — re-export for tests
    _reject_generation_only_classifier,  # noqa: F401 — re-export for tests
    _reject_guard_without_chat_template,  # noqa: F401 — re-export for tests
    _reject_uninitialized_classifier_head,  # noqa: F401 — re-export for tests
    _resolve_classifier_mode,  # noqa: F401 — re-export for tests
)
from ._gates import (
    _evaluate_safety_gates,  # noqa: F401 — re-export for tests
    _log_safety_diagnostics,  # noqa: F401 — re-export for tests
    _resolve_safety_score,  # noqa: F401 — re-export for tests
)
from ._generate import (
    _generate_one_safety_response,  # noqa: F401 — re-export for tests
    _generate_safety_batch_with_oom_retry,  # noqa: F401 — re-export for tests
    _generate_safety_responses,  # noqa: F401 — re-export for tests
    _release_model_from_gpu,  # noqa: F401 — re-export for tests
)
from ._inputs import (
    _load_safety_prompts,  # noqa: F401 — re-export for tests
    _validate_batch_size,  # noqa: F401 — re-export for tests
)
from ._orchestrator import (
    run_safety_evaluation,
)
from ._results import (
    _PII_REDACT_FIELDS,  # noqa: F401 — re-export for tests
    _append_trend_entry,  # noqa: F401 — re-export for tests
    _save_safety_results,  # noqa: F401 — re-export for tests
)
from ._score_classification import (
    _classify_one_response,  # noqa: F401 — re-export for tests
    _classify_responses,  # noqa: F401 — re-export for tests
)
from ._score_generation import (
    _GUARD_VERDICT_MAX_INPUT_TOKENS,  # noqa: F401 — re-export for tests
    _GUARD_VERDICT_MAX_NEW_TOKENS,  # noqa: F401 — re-export for tests
    _classify_one_generative,  # noqa: F401 — re-export for tests
    _classify_responses_generative,  # noqa: F401 — re-export for tests
    _generate_guard_verdict,  # noqa: F401 — re-export for tests
    _parse_guard_verdict,  # noqa: F401 — re-export for tests
)

# Name re-exports — every top-level definition of the pre-split ``safety.py``
# stays resolvable at ``forgelm.safety.<name>``, as the SAME object.
from ._types import (
    CATEGORY_SEVERITY,
    DEFAULT_MAX_SAFETY_REGRESSION,
    HARM_CATEGORIES,
    SEVERITY_LEVELS,
    SafetyEvalThresholds,
    SafetyResult,
    _CategoryTelemetry,  # noqa: F401 — re-export for tests
    _extract_category,  # noqa: F401 — re-export for tests
)

__all__ = [
    "SafetyResult",
    "SafetyEvalThresholds",
    "run_safety_evaluation",
    "HARM_CATEGORIES",
    "CATEGORY_SEVERITY",
    "SEVERITY_LEVELS",
    "DEFAULT_MAX_SAFETY_REGRESSION",
]
