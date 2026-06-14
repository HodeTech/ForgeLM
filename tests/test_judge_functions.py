"""Unit tests for judge.py functions (JSON parsing, API calls)."""

from unittest.mock import MagicMock, patch

import pytest

from forgelm.judge import JudgeResult, _parse_judge_json

# run_judge_evaluation requires torch to generate responses
torch_available = True
try:
    import torch  # noqa: F401
except ImportError:
    torch_available = False


@pytest.fixture(autouse=True)
def _stub_ssrf_resolver(monkeypatch):
    """Auto-stub ``forgelm._http._resolve_safe_destination`` so judge
    tests do not require live DNS resolution of ``api.openai.com`` or
    other API endpoints.  See ``tests/test_webhook.py`` for the same
    pattern + full rationale.  Dedicated resolver coverage lives in
    ``tests/test_http_dns_rebinding.py``.
    """
    import ipaddress

    from forgelm import _http

    def _hermetic_resolver(host):
        if not host:
            return None, "empty host"
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        if literal is not None:
            if _http._is_blocked_ip(literal):
                return None, "Private/loopback/IMDS destination"
            return host, None
        if host == "localhost":
            return None, "Private/loopback/IMDS destination"
        return "8.8.8.8", None

    monkeypatch.setattr(_http, "_resolve_safe_destination", _hermetic_resolver)


class TestParseJudgeJson:
    def test_valid_json(self):
        result = _parse_judge_json('{"score": 8, "reason": "Good answer"}')
        assert result["score"] == 8
        assert result["reason"] == "Good answer"

    def test_json_in_markdown_code_block(self):
        text = '```json\n{"score": 7, "reason": "OK"}\n```'
        result = _parse_judge_json(text)
        assert result["score"] == 7

    def test_json_in_plain_code_block(self):
        text = '```\n{"score": 6, "reason": "Decent"}\n```'
        result = _parse_judge_json(text)
        assert result["score"] == 6

    def test_invalid_json_returns_none_sentinel(self):
        # score=None signals a parse failure so the caller can drop the
        # sample from the average instead of clipping it up to 1.0.
        result = _parse_judge_json("This is not JSON at all")
        assert result["score"] is None
        assert "Invalid JSON" in result["reason"]

    def test_empty_string(self):
        result = _parse_judge_json("")
        assert result["score"] is None

    def test_whitespace_padding(self):
        result = _parse_judge_json('  \n  {"score": 9, "reason": "Great"}  \n  ')
        assert result["score"] == 9

    def test_nested_json(self):
        text = '{"score": 5, "reason": "OK", "details": {"sub": 1}}'
        result = _parse_judge_json(text)
        assert result["score"] == 5

    def test_multiple_code_blocks(self):
        text = '```\ninvalid\n```\n```json\n{"score": 4, "reason": "Found"}\n```'
        result = _parse_judge_json(text)
        assert result["score"] == 4


class TestCallApiJudge:
    @patch("forgelm._http.requests.Session.post")
    def test_successful_api_call(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = {"choices": [{"message": {"content": '{"score": 8, "reason": "Good"}'}}]}
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        from forgelm.judge import _call_api_judge

        result = _call_api_judge("test prompt", "fake-api-key", "gpt-4o")
        assert result["score"] == 8
        mock_post.assert_called_once()

    @patch("forgelm._http.requests.Session.post")
    def test_api_timeout(self, mock_post):
        import requests

        mock_post.side_effect = requests.exceptions.Timeout("timed out")

        from forgelm.judge import _call_api_judge

        result = _call_api_judge("test prompt", "fake-key")
        # Transport failures use the same None sentinel as parse failures.
        assert result["score"] is None
        assert "API error" in result["reason"]

    @patch("forgelm._http._resolve_safe_destination", return_value=("8.8.8.8", None))
    @patch("forgelm._http.requests.Session.post")
    def test_custom_api_base(self, mock_post, _mock_resolve):
        """``api_base`` override drives the request to ``custom.api``.

        Post-issue-#14 the URL passed to ``Session.post`` is rebuilt with
        the resolved IP literal, so we assert on the ``Host`` header (which
        carries the original hostname) instead of the URL string itself.
        DNS is mocked so the test does not require live resolution of
        the synthetic ``custom.api`` fixture domain.
        """
        mock_response = MagicMock()
        mock_response.json.return_value = {"choices": [{"message": {"content": '{"score": 7, "reason": "OK"}'}}]}
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        from forgelm.judge import _call_api_judge

        _call_api_judge("prompt", "key", "model", api_base="https://custom.api/v1/chat")
        call_args = mock_post.call_args
        headers = call_args.kwargs.get("headers") or {}
        assert headers.get("Host") == "custom.api", (
            f"Host header should reflect the custom api_base hostname; got {headers!r}"
        )


class TestJudgeResult:
    def test_defaults(self):
        r = JudgeResult()
        assert r.average_score == pytest.approx(0.0)
        assert r.passed is True
        assert r.scores == []
        assert r.details == []


@pytest.mark.skipif(not torch_available, reason="torch not installed")
class TestJudgeScoreClipping:
    @patch("forgelm._http.requests.Session.post")
    def test_score_above_10_clipped_to_10(self, mock_post, caplog):
        """Scores above 10 must be clamped to 10.0 with a warning."""
        import logging

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": '{"score": 15, "reason": "Excellent"}'}}]
        }
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        from forgelm.judge import _call_api_judge

        with caplog.at_level(logging.WARNING, logger="forgelm.judge"):
            result = _call_api_judge("test prompt", "fake-key", "gpt-4o")

        # The raw parse returns 15; clipping happens in run_judge_evaluation.
        # _call_api_judge returns the raw parsed value.
        assert result["score"] == 15

    @patch("forgelm._http.requests.Session.post")
    def test_score_clipped_in_run_judge_evaluation(self, mock_post, tmp_path, caplog):
        """run_judge_evaluation must clip out-of-range scores and emit a warning."""
        import logging

        import torch

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": '{"score": 15, "reason": "Way too good"}'}}]
        }
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        # Minimal eval dataset
        eval_file = tmp_path / "eval.jsonl"
        eval_file.write_text('{"prompt": "Hello?"}\n')

        mock_model = MagicMock()
        mock_model.device = "cpu"
        fake_output = torch.zeros((1, 5), dtype=torch.long)
        mock_model.generate.return_value = fake_output

        mock_tokenizer = MagicMock()
        mock_tokenizer.return_value = {
            "input_ids": torch.zeros((1, 3), dtype=torch.long),
            "attention_mask": torch.ones((1, 3), dtype=torch.long),
        }
        mock_tokenizer.decode.return_value = "A fine answer."

        from forgelm.judge import run_judge_evaluation

        with caplog.at_level(logging.WARNING, logger="forgelm.judge"):
            result = run_judge_evaluation(
                model=mock_model,
                tokenizer=mock_tokenizer,
                eval_dataset_path=str(eval_file),
                judge_model="gpt-4o",
                judge_api_key="fake-key",
                min_score=5.0,
            )

        # Score must be clipped to 10.0
        assert result.scores[0] == pytest.approx(10.0)
        assert result.average_score == pytest.approx(10.0)
        # Warning must be emitted
        assert any("clipped" in r.message or "out-of-range" in r.message for r in caplog.records)

    @patch("forgelm._http.requests.Session.post")
    def test_score_below_1_clipped_to_1(self, mock_post, tmp_path):
        """Scores below 1 must be clamped to 1.0."""
        import torch

        mock_response = MagicMock()
        mock_response.json.return_value = {"choices": [{"message": {"content": '{"score": -5, "reason": "Terrible"}'}}]}
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        eval_file = tmp_path / "eval.jsonl"
        eval_file.write_text('{"prompt": "Hello?"}\n')

        mock_model = MagicMock()
        mock_model.device = "cpu"
        mock_model.generate.return_value = torch.zeros((1, 5), dtype=torch.long)

        mock_tokenizer = MagicMock()
        mock_tokenizer.return_value = {
            "input_ids": torch.zeros((1, 3), dtype=torch.long),
            "attention_mask": torch.ones((1, 3), dtype=torch.long),
        }
        mock_tokenizer.decode.return_value = "Bad."

        from forgelm.judge import run_judge_evaluation

        result = run_judge_evaluation(
            model=mock_model,
            tokenizer=mock_tokenizer,
            eval_dataset_path=str(eval_file),
            judge_model="gpt-4o",
            judge_api_key="fake-key",
            min_score=1.0,
        )
        assert result.scores[0] == pytest.approx(1.0)


class TestJudgeApiBasePassthrough:
    @patch("forgelm._http._resolve_safe_destination", return_value=("8.8.8.8", None))
    @patch("forgelm._http.requests.Session.post")
    def test_api_base_reaches_http_call(self, mock_post, _mock_resolve):
        """``judge_api_base`` must be forwarded to the HTTP POST.

        Asserts via the ``Host`` header because issue-#14 hardening rebuilds
        the URL with the resolved IP literal before the actual call —
        carrying the original hostname over to the request via the
        ``Host`` header (and SNI for HTTPS).
        """
        mock_response = MagicMock()
        mock_response.json.return_value = {"choices": [{"message": {"content": '{"score": 7, "reason": "OK"}'}}]}
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        from forgelm.judge import _call_api_judge

        custom_base = "https://custom.llm.api/v1/chat/completions"
        _call_api_judge("prompt", "key", "model", api_base=custom_base)

        call_args = mock_post.call_args
        headers = call_args.kwargs.get("headers") or {}
        assert headers.get("Host") == "custom.llm.api", (
            f"Host header should reflect the custom judge_api_base hostname; got {headers!r}"
        )


class TestJudgeUsesSafePost:
    """Phase 7: judge._call_api_judge must route through forgelm._http.safe_post.

    The acceptance gate is: ``grep -rn 'requests.post' forgelm/`` returns
    nothing outside ``_http.py``. These tests cover the behavioural side —
    judge calls go through ``safe_post`` and inherit the SSRF / scheme /
    redirect / TLS policy automatically.
    """

    def test_imports_safe_post(self):
        """judge._call_api_judge must import safe_post from forgelm._http."""
        import inspect

        from forgelm import judge

        src = inspect.getsource(judge._call_api_judge)
        assert "safe_post" in src, "judge._call_api_judge must use safe_post"

    @patch("forgelm._http.requests.Session.post")
    def test_judge_call_goes_through_safe_post(self, mock_post):
        """A successful judge call must hit requests.post (via safe_post)."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"choices": [{"message": {"content": '{"score": 7, "reason": "OK"}'}}]}
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        from forgelm.judge import _call_api_judge

        result = _call_api_judge("prompt", "fake-key", "gpt-4o")

        # Confirm the call went through safe_post → requests.post
        mock_post.assert_called_once()
        kwargs = mock_post.call_args.kwargs
        # safe_post forwards allow_redirects=False
        assert kwargs.get("allow_redirects") is False
        assert result["score"] == 7

    @patch("forgelm._http.requests.Session.post")
    def test_judge_ssrf_block_for_private_url(self, mock_post):
        """A private-IP api_base must be rejected before any network call.

        ``_call_api_judge`` re-raises :class:`HttpSafetyError` so
        ``run_judge_evaluation`` can convert it into a hard
        ``JudgeResult(passed=False)`` instead of silently scoring every
        prompt as ``None`` (which would mask a misconfigured endpoint).
        """
        import pytest

        from forgelm._http import HttpSafetyError
        from forgelm.judge import _call_api_judge

        with pytest.raises(HttpSafetyError):
            _call_api_judge(
                "prompt",
                "fake-key",
                "gpt-4o",
                api_base="https://10.0.0.1/v1/chat/completions",  # NOSONAR RFC1918 — SSRF guard fixture (intentional)
            )

        mock_post.assert_not_called()


class TestJudgeResultRedaction:
    """P2-2 / P2-3: ``judge_results.json`` must not persist raw eval
    prompts, model responses, or the judge's natural-language reason by
    default — the reason can quote PII from the eval set.  The opt-in
    flag ``JudgeConfig.include_eval_samples=True`` reverses the policy."""

    @staticmethod
    def _sample_details():
        return [
            {
                "prompt": "What is patient John Doe's SSN?",
                "response": "I cannot disclose personal information.",
                "score": 8.0,
                "reason": "Refused to disclose SSN of John Doe — appropriate.",
                "judge_failed": False,
            },
            {
                "prompt": "Translate to French",
                "response": "Bonjour",
                "score": 9.0,
                "reason": "Correct translation",
                "judge_failed": False,
            },
        ]

    def test_default_strips_prompt_response_reason(self, tmp_path):
        import json as _json

        from forgelm.judge import _save_judge_results

        _save_judge_results(
            output_dir=str(tmp_path),
            avg_score=8.5,
            min_score=5.0,
            passed=True,
            num_prompts=2,
            details=self._sample_details(),
        )
        payload = _json.loads((tmp_path / "judge_results.json").read_text())
        for d in payload["details"]:
            assert "prompt" not in d, "raw eval prompts must not persist by default"
            assert "response" not in d, "raw model responses must not persist by default"
            assert "reason" not in d, "judge reason can quote PII; redact by default"
            assert "score" in d, "non-PII fields like score must remain"
            assert "judge_failed" in d

    def test_include_samples_keeps_all_fields(self, tmp_path):
        import json as _json

        from forgelm.judge import _save_judge_results

        _save_judge_results(
            output_dir=str(tmp_path),
            avg_score=8.5,
            min_score=5.0,
            passed=True,
            num_prompts=2,
            details=self._sample_details(),
            include_samples=True,
        )
        payload = _json.loads((tmp_path / "judge_results.json").read_text())
        # Opt-in: prompt/response/reason all preserved verbatim
        assert payload["details"][0]["prompt"] == "What is patient John Doe's SSN?"
        assert payload["details"][0]["response"] == "I cannot disclose personal information."
        assert "John Doe" in payload["details"][0]["reason"]

    def test_parse_judge_json_warning_strips_raw_text(self, caplog):
        """P2-3: failed-parse log must not include the raw model output —
        only the text length and a generic reason."""
        import logging

        from forgelm.judge import _parse_judge_json

        sensitive = "John Doe SSN 123-45-6789 — this should NEVER appear in the log"
        with caplog.at_level(logging.WARNING, logger="forgelm.judge"):
            result = _parse_judge_json(sensitive)

        assert result["score"] is None
        # Reason is a fixed string, not a quote of the raw input
        assert "John Doe" not in result["reason"], "Sentinel reason must not echo raw model output"
        assert "Invalid JSON" in result["reason"]
        # Warning log must not include the raw text either
        for rec in caplog.records:
            assert "John Doe" not in rec.getMessage(), "Warning log must not echo raw model output"
            assert "123-45-6789" not in rec.getMessage()


class TestClipJudgeScore:
    """F-P8-C-16: the score-clip helper drives the auto-revert gate but
    was untested — a regression in clamping would silently corrupt the
    eval-gate decision."""

    @pytest.mark.parametrize(
        "raw,expected",
        [(0.5, 1.0), (1.0, 1.0), (5.5, 5.5), (10.0, 10.0), (11.0, 10.0), (-3.0, 1.0)],
    )
    def test_clamps_to_1_10(self, raw, expected):
        from forgelm.judge import _clip_judge_score

        assert _clip_judge_score(raw) == expected

    def test_none_passes_through(self):
        from forgelm.judge import _clip_judge_score

        # None preserves the failure signal so it can be excluded from the avg.
        assert _clip_judge_score(None) is None


class TestSummarizeJudgeScores:
    """F-P8-C-16: aggregation must distinguish 'no valid verdicts' from
    'low average', and respect the min_score pass/fail boundary."""

    def test_no_valid_scores_is_distinct_failure(self):
        from forgelm.judge import _summarize_judge_scores

        avg, passed, reason = _summarize_judge_scores(
            scores=[None, None],
            failure_count=2,
            eval_prompts=["a", "b"],
            min_score=5.0,
        )
        assert avg == 0.0
        assert passed is False
        assert "No valid judge scores" in reason

    def test_average_above_min_passes(self):
        from forgelm.judge import _summarize_judge_scores

        avg, passed, reason = _summarize_judge_scores(
            scores=[8.0, 6.0, None],
            failure_count=1,
            eval_prompts=["a", "b", "c"],
            min_score=5.0,
        )
        assert avg == 7.0
        assert passed is True
        assert reason is None

    def test_average_below_min_fails(self):
        from forgelm.judge import _summarize_judge_scores

        avg, passed, reason = _summarize_judge_scores(
            scores=[3.0, 4.0],
            failure_count=0,
            eval_prompts=["a", "b"],
            min_score=5.0,
        )
        assert passed is False
        assert "below minimum" in reason


class TestLoadEvalPrompts:
    def test_loads_prompt_and_text_keys_and_bare_lines(self, tmp_path):
        from forgelm.judge import _load_eval_prompts

        path = tmp_path / "eval.jsonl"
        path.write_text(
            '{"prompt": "P1"}\n'
            '{"text": "P2"}\n'
            "\n"  # blank line skipped
            "bare line\n",
            encoding="utf-8",
        )
        assert _load_eval_prompts(str(path)) == ["P1", "P2", "bare line"]

    def test_missing_file_raises(self, tmp_path):
        from forgelm.judge import _load_eval_prompts

        with pytest.raises(OSError):
            _load_eval_prompts(str(tmp_path / "nope.jsonl"))


class TestJudgeBatchSize:
    """F-P8-C-16: the library-API batch_size guard (judge.py:373) was
    never triggered. A 0 or negative batch_size must raise at the
    boundary, before the batching loop produces a silent no-op."""

    @pytest.mark.parametrize("bad", [0, -1, 2.5, "8"])
    def test_invalid_batch_size_raises(self, bad):
        from forgelm.judge import run_judge_evaluation

        with pytest.raises(ValueError, match="batch_size must be a positive"):
            run_judge_evaluation(
                model=MagicMock(),
                tokenizer=MagicMock(),
                eval_dataset_path="unused.jsonl",
                batch_size=bad,
            )
