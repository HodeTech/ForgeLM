"""Phase 12.5: optional Presidio ML-NER PII adapter.

Layered ON TOP of :func:`forgelm.data_audit.detect_pii` (regex). The two
detectors return disjoint category sets so the merged ``pii_summary``
shows both the structured-identifier signal (regex) and the
unstructured-identifier signal (ML) without double-counting the same
span.

Optional-deps gating: every runtime guard reads from
:mod:`forgelm.data_audit._optional` via attribute lookup (``_optional._HAS_PRESIDIO``)
rather than a value-time ``from . import _HAS_PRESIDIO``, so tests can
patch ``forgelm.data_audit._optional._HAS_PRESIDIO`` and the patched
boolean is observed by every callsite in this module.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, Dict

from . import _optional

logger = logging.getLogger("forgelm.data_audit")


# Conventional spaCy model name per language code. Covers the languages
# spaCy publishes a maintained ``*_core_news_lg`` / ``*_core_web_lg``
# model for; operators auditing in a language not in this map should
# pass ``"xx"`` for the multilingual fallback (and install
# ``xx_ent_wiki_sm``) or hand-build a Presidio ``NlpEngine`` and feed
# it to :func:`audit_dataset` programmatically. The map is small on
# purpose — auto-mapping more languages without verifying each model's
# NER quality risks silent under-detection in compliance-critical
# workflows.
_SPACY_MODEL_FOR_LANGUAGE: Dict[str, str] = {
    "en": "en_core_web_lg",
    "de": "de_core_news_lg",
    "es": "es_core_news_lg",
    "fr": "fr_core_news_lg",
    "it": "it_core_news_lg",
    "ja": "ja_core_news_lg",
    "ko": "ko_core_news_lg",
    "nl": "nl_core_news_lg",
    "pl": "pl_core_news_lg",
    "pt": "pt_core_news_lg",
    "ru": "ru_core_news_lg",
    "zh": "zh_core_web_lg",
    # Multilingual fallback — coarser NER but works for any Unicode
    # script; useful for languages without a dedicated spaCy model
    # (e.g. Turkish, Arabic at time of writing).
    "xx": "xx_ent_wiki_sm",
}


# A single, canonical install hint shared by the import-sentinel branch
# and the spaCy-model-missing branch.  Both failure modes look identical
# to the operator (``--pii-ml`` doesn't work; here is the recipe), so
# we keep the message in one place to avoid drift.
_PRESIDIO_INSTALL_HINT: str = (
    "Presidio PII ML detection requires the 'ingestion-pii-ml' extra "
    "AND a spaCy English NER model. Install with:\n"
    "  pip install 'forgelm[ingestion-pii-ml]'\n"
    "  python -m spacy download en_core_web_lg\n"
    "For non-English audits also install the matching spaCy model "
    "(e.g. 'python -m spacy download de_core_news_lg' for --pii-ml-language de)."
)


# Presidio analyzer is heavy to construct (loads spaCy + a NER model on
# first call), so we cache instances per-language. ``maxsize=8`` covers
# the realistic set of audit-language combinations a single process
# would request without unbounded growth; test code blows the cache via
# ``cache_clear()``.
@lru_cache(maxsize=8)
def _get_presidio_analyzer(language: str = "en") -> Any:
    """Return a cached :class:`presidio_analyzer.AnalyzerEngine` for ``language``.

    English uses Presidio's default constructor (loads ``en_core_web_lg``
    via spaCy). Non-English builds an :class:`NlpEngineProvider` keyed
    on the conventional spaCy model from
    :data:`_SPACY_MODEL_FOR_LANGUAGE` and instantiates the analyzer with
    ``supported_languages=[language]`` so the pre-flight in
    :func:`_require_presidio` sees the requested language as registered.

    Raises:
        ImportError: when the ``[ingestion-pii-ml]`` extra is missing —
            the actionable install hint surfaces via
            :func:`_require_presidio` so the operator never sees a deep
            ``OSError`` from spaCy mid-stream.
        ValueError: when ``language`` has no entry in
            :data:`_SPACY_MODEL_FOR_LANGUAGE`. Operators auditing in
            unsupported languages should use ``"xx"`` (multilingual
            fallback) or configure a custom Presidio analyzer
            programmatically.
    """
    if not _optional._HAS_PRESIDIO:
        raise ImportError(_PRESIDIO_INSTALL_HINT)
    if language == "en":
        return _optional._PresidioAnalyzer()
    model_name = _SPACY_MODEL_FOR_LANGUAGE.get(language)
    if model_name is None:
        raise ValueError(
            f"No default spaCy model registered for Presidio language {language!r}. "
            f"Supported language codes: {sorted(_SPACY_MODEL_FOR_LANGUAGE)}. "
            "For other languages, use 'xx' (multilingual fallback, install "
            "'xx_ent_wiki_sm') or configure a custom Presidio AnalyzerEngine "
            "via the Python API (see "
            "https://microsoft.github.io/presidio/analyzer/languages/)."
        )
    # Local import: NlpEngineProvider is part of presidio-analyzer, so
    # gating on ``_HAS_PRESIDIO`` above is sufficient. Importing here
    # rather than at module top keeps the audit module importable when
    # the optional extra is missing.
    from presidio_analyzer.nlp_engine import NlpEngineProvider  # type: ignore[import-not-found]

    nlp_configuration = {
        "nlp_engine_name": "spacy",
        "models": [{"lang_code": language, "model_name": model_name}],
    }
    nlp_engine = NlpEngineProvider(nlp_configuration=nlp_configuration).create_engine()
    return _optional._PresidioAnalyzer(
        nlp_engine=nlp_engine,
        supported_languages=[language],
    )


def _require_presidio(language: str = "en") -> None:
    """Raise a clear ImportError / ValueError when the optional ML-NER backend is unusable.

    Mirrors :func:`_require_datasketch` but extends the check to model
    availability: a fresh ``[ingestion-pii-ml]`` install does **not**
    transitively ship the spaCy NER model — that's a separate
    ``python -m spacy download en_core_web_lg`` step. Without the model
    the first per-row analyzer call would raise ``OSError`` deep inside
    spaCy and (because :func:`detect_pii_ml`'s last-ditch ``except``
    swallows per-row failures) the audit would silently report zero
    ML PII coverage. Pre-flighting the analyzer build here surfaces
    the missing-model case before any rows are scanned.

    When ``language`` is non-default, also verify the requested language
    is registered on the analyzer's supported list. Presidio's default
    ``AnalyzerEngine`` only loads English; ``--pii-ml --pii-ml-language tr``
    against a default install would otherwise return ``{}`` per row
    (``analyzer.analyze`` raises ``ValueError`` which
    :func:`detect_pii_ml` swallows). Failing fast with an actionable
    message is the only way the operator notices the misconfiguration.
    """
    if not _optional._HAS_PRESIDIO:
        raise ImportError(_PRESIDIO_INSTALL_HINT)
    try:
        analyzer = _get_presidio_analyzer(language)
    except OSError as exc:
        # spaCy raises ``OSError`` ("Can't find model 'en_core_web_lg'")
        # when the model package isn't on the import path. Re-raise as
        # ImportError so callers that already catch ImportError for the
        # extras-missing branch see the same exception class for both
        # failure modes.
        _get_presidio_analyzer.cache_clear()
        raise ImportError(
            f"Presidio analyzer build failed (likely missing spaCy NER model for "
            f"language {language!r}): {exc}\n{_PRESIDIO_INSTALL_HINT}"
        ) from exc
    # Defence in depth: ``_get_presidio_analyzer`` builds the analyzer
    # for the requested language so this should always pass, but a stub
    # / mock injected by tests or a future Presidio API change could
    # produce a mismatch — fail loud instead of letting per-row scans
    # silently return ``{}`` when the analyzer doesn't actually support
    # what was asked for.
    supported = getattr(analyzer, "supported_languages", None) or ["en"]
    if language not in supported:
        raise ValueError(
            f"Presidio analyzer has no NLP engine registered for language {language!r}. "
            f"Registered languages: {sorted(supported)}. "
            "Use one of the codes in forgelm.data_audit._SPACY_MODEL_FOR_LANGUAGE, "
            "use 'xx' for the multilingual fallback (install 'xx_ent_wiki_sm'), or "
            "configure a custom Presidio analyzer via the Python API (see "
            "https://microsoft.github.io/presidio/analyzer/languages/)."
        )


# Presidio canonicalises spaCy NER labels through its
# ``model_to_presidio_entity_mapping`` (PER/PERSON -> PERSON,
# LOC/GPE -> LOCATION, ORG -> ORGANIZATION, NORP -> NRP); what
# ``analyzer.analyze()`` emits as ``entity_type`` is the canonical
# Presidio name, never the raw spaCy tag. Keep this map keyed on
# canonical Presidio names only so a future maintainer doesn't read
# dead spaCy keys as live coverage.  ``NRP`` (nationality / religious /
# political group) is deliberately not mapped — it's a distinct privacy
# signal from ``location`` and Presidio's NRP precision is too low to
# grade as ``person`` without further work; revisit if compliance
# reviewers ask for it.
_PRESIDIO_ENTITY_MAP: Dict[str, str] = {
    "PERSON": "person",
    "ORGANIZATION": "organization",
    "LOCATION": "location",
}


def detect_pii_ml(text: Any, *, language: str = "en") -> Dict[str, int]:
    """ML-NER PII detector — counts ``person`` / ``organization`` / ``location`` spans.

    Layered ON TOP of :func:`forgelm.data_audit.detect_pii` (regex). The
    two detectors return disjoint category sets so the merged
    ``pii_summary`` shows both the structured-identifier signal (regex)
    and the unstructured-identifier signal (ML) without double-counting
    the same span.

    Args:
        text: Per-row payload; defensive against ``None`` / numbers / dicts.
        language: Presidio NLP-engine language code. Default ``"en"``.
            Set via :func:`audit_dataset`'s ``pii_ml_language`` parameter.
            The unsupported-language case is caught up-front by
            :func:`_require_presidio` (called from ``audit_dataset`` when
            ``enable_pii_ml`` is set), so the per-row ``ValueError``
            swallow below only fires for pathological inputs / transient
            engine state, not for misconfiguration.

    Returns an empty dict when:
    * ``text`` is not a non-empty string (defensive — callers pass JSONL
      payloads that may be ``None`` / numbers / dicts);
    * Presidio is not installed (the caller must opt in explicitly via
      :func:`_require_presidio` if they want a hard failure);
    * the analyzer raises a recoverable error on this row — pathological
      strings, transient NLP engine state, language-specific recogniser
      misses. We swallow narrowly-typed failures (``ValueError`` /
      ``RuntimeError``) and let everything else propagate so genuine
      bugs (``OSError`` on missing model, ``MemoryError``, ``KeyboardInterrupt``)
      stay visible. The pre-flight in :func:`_require_presidio` covers
      the missing-model and unsupported-language cases so neither
      reaches this path in the common run.
    """
    if not isinstance(text, str) or not text.strip():
        return {}
    if not _optional._HAS_PRESIDIO:
        return {}
    try:
        analyzer = _get_presidio_analyzer(language)
        results = analyzer.analyze(text=text, language=language)
    except (ValueError, RuntimeError) as exc:
        # Per-row resilience for the narrow class of failures Presidio
        # raises on bad input or transient engine state. ``OSError`` is
        # deliberately NOT caught here — that's the missing-spaCy-model
        # signal and ``_require_presidio``'s pre-flight should have
        # converted it to ImportError before any row was scanned. If
        # one slips through (e.g. lazy model load triggered later),
        # surfacing it loudly is the correct behaviour.
        #
        # Log at DEBUG (not WARNING): per-row failures can fire on
        # every row in pathological corpora, and warning-level spam
        # would drown out the audit's real findings. Operators
        # debugging "why is ML PII coverage zero" can rerun with
        # ``--log-level DEBUG`` to see the per-row exception trail.
        logger.debug(
            "detect_pii_ml: per-row Presidio failure (language=%s): %s",
            language,
            exc,
            exc_info=True,
        )
        return {}
    counts: Dict[str, int] = {}
    for finding in results:
        kind = _PRESIDIO_ENTITY_MAP.get(getattr(finding, "entity_type", ""))
        if kind:
            counts[kind] = counts.get(kind, 0) + 1
    return counts


__all__ = [
    "_SPACY_MODEL_FOR_LANGUAGE",
    "_PRESIDIO_INSTALL_HINT",
    "_PRESIDIO_ENTITY_MAP",
    "_get_presidio_analyzer",
    "_require_presidio",
    "detect_pii_ml",
]
