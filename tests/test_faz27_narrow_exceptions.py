"""Faz 27 — proves the narrowed exception classes are correct.

For every site narrowed in Faz 27 we raise the specific exception that the
new ``except`` clause now catches and assert the documented fallback runs.
If a future change broadens or removes the catch, the test will hard-fail
because the unhandled exception will propagate out of the call site.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# data_audit/_streaming.py — _detect_language narrowed to LangDetectException
# ---------------------------------------------------------------------------


class TestDetectLanguageNarrowing:
    """The bare ``except Exception`` was replaced with the langdetect-specific
    exception so genuine bugs (TypeError on a non-str caller) propagate."""

    def test_short_input_returns_unknown(self):
        from forgelm.data_audit._streaming import _detect_language

        assert _detect_language("hi") == "unknown"

    def test_empty_input_returns_unknown(self):
        from forgelm.data_audit._streaming import _detect_language

        assert _detect_language("") == "unknown"

    def test_lang_detect_exception_is_swallowed(self):
        from forgelm.data_audit._streaming import _detect_language

        # Pure-symbol payloads make langdetect raise LangDetectException
        # because no language features can be extracted.
        result = _detect_language("!@#$%^&*()" * 10)
        assert result == "unknown"

    def test_english_payload_returns_en(self):
        # ``_detect_language`` returns the literal ``"unknown"`` constant
        # without ``langdetect`` installed (see ``forgelm/data_audit/_streaming.py``
        # — ImportError fallback).  CI matrix runs ``[dev]`` only and does
        # not pull the optional ``[ingestion]`` extra that ships ``langdetect``,
        # so the happy-path assertion below would never reach ``"en"`` in CI.
        # Skip cleanly when the optional extra is absent so the test runs
        # locally (where ``[ingestion]`` is typically installed) without
        # false-failing in matrix builds.
        langdetect = pytest.importorskip(
            "langdetect",
            reason=(
                "_detect_language is constant 'unknown' without the optional "
                "[ingestion] extra; this happy-path assertion only meaningful "
                "when langdetect is installed."
            ),
        )
        # Sanity: the importorskip target must expose the function our
        # downstream consumer (forgelm.data_audit._streaming._detect_language)
        # actually uses.  Touching the attribute also consumes the binding
        # so SonarCloud S1481 (unused local) does not fire on the
        # documentation-bound name.
        assert hasattr(langdetect, "detect"), "langdetect must expose .detect()"

        from forgelm.data_audit._streaming import _detect_language

        assert _detect_language("Hello world this is a sentence in English language here.") == "en"


# ---------------------------------------------------------------------------
# safety.py — narrowed sites
# ---------------------------------------------------------------------------


class TestGenerateOneSafetyResponseNarrowing:
    """``_generate_one_safety_response`` now catches a narrow tuple. We
    confirm RuntimeError (CUDA OOM proxy) and ValueError both yield ``""``
    rather than crashing the whole batch."""

    def _setup(self, side_effect):
        from forgelm import safety

        tokenizer = MagicMock()
        tokenizer.return_value = MagicMock(items=lambda: [("input_ids", MagicMock())])
        model = MagicMock()
        model.device = "cpu"
        model.generate.side_effect = side_effect
        return safety, model, tokenizer

    def test_runtime_error_returns_empty_string(self):
        safety, model, tok = self._setup(RuntimeError("CUDA OOM proxy"))
        assert safety._generate_one_safety_response(model, tok, "prompt", 32) == ""

    def test_value_error_returns_empty_string(self):
        safety, model, tok = self._setup(ValueError("bad shape"))
        assert safety._generate_one_safety_response(model, tok, "prompt", 32) == ""

    def test_index_error_returns_empty_string(self):
        safety, model, tok = self._setup(IndexError("oversize"))
        assert safety._generate_one_safety_response(model, tok, "prompt", 32) == ""


class TestClassifyResponsesNarrowing:
    def test_runtime_error_surfaced_as_error_label(self):
        from forgelm.safety import _classify_responses

        # Classifier raises RuntimeError on the first call; loop must
        # absorb it into a detail row instead of aborting.
        classifier = MagicMock(side_effect=RuntimeError("driver crashed"))
        result = _classify_responses(
            classifier=classifier,
            prompts=["p1"],
            responses=["r1"],
            track_categories=False,
            min_classifier_confidence=0.7,
        )
        assert result["unsafe_count"] == 1
        assert result["details"][0]["label"] == "error"
        assert "driver crashed" in result["details"][0]["classifier_error"]

    def test_key_error_surfaced_as_error_label(self):
        from forgelm.safety import _classify_responses

        # Result-shape drift: classifier returns dict missing 'label'/'score'.
        classifier = MagicMock(return_value=[{}])  # KeyError on result[0]['label']
        result = _classify_responses(
            classifier=classifier,
            prompts=["p1"],
            responses=["r1"],
            track_categories=False,
            min_classifier_confidence=0.7,
        )
        assert result["details"][0]["label"] == "error"


class TestAppendTrendEntryNarrowing:
    def test_oserror_during_write_is_swallowed(self, tmp_path, caplog):
        import logging

        from forgelm.safety import _append_trend_entry

        # Point at a path under a non-existent parent so open() raises OSError.
        bad_dir = tmp_path / "does_not_exist" / "deeper"
        with caplog.at_level(logging.WARNING, logger="forgelm.safety"):
            _append_trend_entry(str(bad_dir), 0.9, 0.95, True)
        assert "Failed to write safety trend entry" in caplog.text


# ---------------------------------------------------------------------------
# trainer.py — best-effort artefact paths
# ---------------------------------------------------------------------------


class TestTrainerArtefactNarrowing:
    """Confirm the model-card / integrity / deployer-instructions catches
    fall through on the documented narrow exception types and still log a
    warning rather than crashing the surrounding training pipeline."""

    def _make_trainer_stub(self):
        # Avoid importing the real Trainer (needs torch + trl). We only
        # need the bound methods, which we invoke via the unbound function.
        return MagicMock()

    def test_model_card_oserror_is_swallowed(self, tmp_path, monkeypatch, caplog):
        import logging

        from forgelm import trainer as trainer_mod

        def boom(**_kwargs):
            raise OSError("disk full")

        monkeypatch.setattr("forgelm.model_card.generate_model_card", boom)

        stub = MagicMock()
        stub.config = MagicMock()
        result = MagicMock(benchmark_scores=None, benchmark_average=None, safety_score=None, safety_categories=None)
        with caplog.at_level(logging.WARNING, logger="forgelm.trainer"):
            trainer_mod.ForgeTrainer._generate_model_card(stub, str(tmp_path), {}, result)
        assert "Failed to generate model card" in caplog.text

    def test_model_card_keyerror_is_swallowed(self, tmp_path, monkeypatch, caplog):
        import logging

        from forgelm import trainer as trainer_mod

        def boom(**_kwargs):
            raise KeyError("metric_name")

        monkeypatch.setattr("forgelm.model_card.generate_model_card", boom)
        stub = MagicMock()
        stub.config = MagicMock()
        result = MagicMock(benchmark_scores=None, benchmark_average=None, safety_score=None, safety_categories=None)
        with caplog.at_level(logging.WARNING, logger="forgelm.trainer"):
            trainer_mod.ForgeTrainer._generate_model_card(stub, str(tmp_path), {}, result)
        assert "Failed to generate model card" in caplog.text

    def test_deployer_instructions_typeerror_is_swallowed(self, tmp_path, monkeypatch, caplog):
        import logging

        from forgelm import trainer as trainer_mod

        def boom(*_a, **_kw):
            raise TypeError("template type drift")

        monkeypatch.setattr("forgelm.compliance.generate_deployer_instructions", boom)
        stub = MagicMock()
        stub.config = MagicMock()
        with caplog.at_level(logging.WARNING, logger="forgelm.trainer"):
            trainer_mod.ForgeTrainer._generate_deployer_instructions(stub, str(tmp_path), {})
        assert "Failed to generate deployer instructions" in caplog.text

    def test_resource_usage_runtimeerror_is_swallowed(self, monkeypatch, caplog):
        import logging

        from forgelm import trainer as trainer_mod

        # Make _collect_gpu_info raise on the resource-collection path.
        stub = MagicMock()
        stub._collect_gpu_info = MagicMock(side_effect=RuntimeError("torch.cuda not init"))
        with caplog.at_level(logging.WARNING, logger="forgelm.trainer"):
            usage = trainer_mod.ForgeTrainer._collect_resource_usage(stub)
        assert usage is None
        assert "Failed to collect resource usage" in caplog.text


# ---------------------------------------------------------------------------
# judge.py — narrowed sites
# ---------------------------------------------------------------------------


class TestCallApiJudgeNarrowing:
    # Patch the ``safe_post`` chokepoint, NOT ``forgelm._http.requests.post``.
    # safe_post's default (SSRF-pinned) path issues the request via
    # ``_pinned_session().post`` — it only falls back to ``requests.post`` on
    # the ``allow_private`` legacy path — so a ``requests.post`` patch leaks
    # through to a real DNS + HTTPS call to the default judge endpoint (which
    # then returns a real 401, raising JudgeAuthError). Stubbing safe_post is
    # both hermetic and the correct seam: it exercises exactly _call_api_judge's
    # response/exception narrowing while implicitly asserting the judge routes
    # outbound HTTP through the chokepoint.
    @patch("forgelm._http.safe_post")
    def test_request_exception_returns_none_score(self, mock_safe_post):
        import requests

        from forgelm.judge import _call_api_judge

        mock_safe_post.side_effect = requests.exceptions.ConnectionError("conn refused")
        result = _call_api_judge("p", "key")
        assert result["score"] is None
        assert "API error" in result["reason"]

    @patch("forgelm._http.safe_post")
    def test_keyerror_on_choices_returns_none_score(self, mock_safe_post):
        from forgelm.judge import _call_api_judge

        # Provider returns an unexpected envelope so dict access on
        # 'choices'/'message'/'content' raises KeyError.
        mock_response = MagicMock()
        mock_response.json.return_value = {"unexpected": "envelope"}
        mock_response.raise_for_status = MagicMock()
        mock_safe_post.return_value = mock_response

        result = _call_api_judge("p", "key")
        assert result["score"] is None
        assert "API error" in result["reason"]

    @patch("forgelm._http.safe_post")
    def test_indexerror_on_empty_choices_returns_none_score(self, mock_safe_post):
        from forgelm.judge import _call_api_judge

        mock_response = MagicMock()
        mock_response.json.return_value = {"choices": []}
        mock_response.raise_for_status = MagicMock()
        mock_safe_post.return_value = mock_response

        result = _call_api_judge("p", "key")
        assert result["score"] is None


# ---------------------------------------------------------------------------
# compliance.py — _build_text_length_stats
# ---------------------------------------------------------------------------


class TestBuildTextLengthStatsNarrowing:
    def test_keyerror_on_column_drop_returns_none(self):
        from forgelm.compliance import _build_text_length_stats

        class _Split:
            column_names = ["text"]

            def __getitem__(self, key):
                raise KeyError(key)

            def __len__(self):
                return 1

        assert _build_text_length_stats(_Split(), "train") is None

    def test_oserror_on_lazy_load_returns_none(self):
        from forgelm.compliance import _build_text_length_stats

        class _Split:
            column_names = ["text"]

            def __getitem__(self, key):
                raise OSError("arrow shard unreachable")

            def __len__(self):
                return 1

        assert _build_text_length_stats(_Split(), "train") is None

    def test_typeerror_on_non_iterable_returns_none(self):
        from forgelm.compliance import _build_text_length_stats

        split = MagicMock()
        split.column_names = ["text"]
        # Returning a non-iterable so the generator expression raises TypeError.
        split.__getitem__ = lambda self, key: 42  # type: ignore[assignment,misc]

        # Force __getitem__ on the type so MagicMock calls go through.
        class _Split:
            column_names = ["text"]

            def __getitem__(self, key):
                return 42  # not iterable

            def __len__(self):
                return 1

        assert _build_text_length_stats(_Split(), "train") is None


# ---------------------------------------------------------------------------
# Streaming JSONL — orthogonal sanity checks
# ---------------------------------------------------------------------------


class TestStreamingNoSilentExceptRegression:
    """Locks in that the broad ``except Exception`` was actually removed
    from the language detector — TypeError on a non-str input must
    propagate (it's a programming bug, not a langdetect signal)."""

    def test_non_string_input_raises_typeerror(self):
        from forgelm.data_audit._streaming import _detect_language

        with pytest.raises((TypeError, AttributeError)):
            _detect_language(12345)  # type: ignore[arg-type]  # NOSONAR — intentional wrong-type test


# ---------------------------------------------------------------------------
# Integration — append trend entry with valid path still writes
# ---------------------------------------------------------------------------


class TestTrendEntryHappyPath:
    def test_valid_directory_writes_entry(self, tmp_path):
        from forgelm.safety import _append_trend_entry

        _append_trend_entry(str(tmp_path), 0.9, 0.95, True)
        contents = (tmp_path / "safety_trend.jsonl").read_text().strip().splitlines()
        entry = json.loads(contents[0])
        assert entry["passed"] is True
