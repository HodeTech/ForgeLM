"""Unit tests for forgelm.webhook module."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from forgelm.config import ForgeConfig
from forgelm.webhook import WebhookNotifier


@pytest.fixture(autouse=True)
def _stub_ssrf_resolver(monkeypatch):
    """Auto-stub ``forgelm._http._resolve_safe_destination`` so webhook
    tests do not require live DNS resolution of ``example.com``.

    The SSRF DNS-pinning path (issue #14) added a hostname → public-IP
    lookup before ``Session.post`` is called.  Without this stub the
    suite is host-environment dependent (passes online, fails in
    offline / sandbox CI runners with ``DNS resolution failed``).

    The stub mirrors the real policy decision for the inputs the
    existing SSRF-block tests use — IP literals are routed through
    ``_is_blocked_ip`` so RFC1918 / loopback / IMDS / multicast still
    raise, and the canonical ``localhost`` hostname is treated as
    loopback.  All other hostnames resolve to the public sentinel
    ``8.8.8.8``.  The dedicated coverage for the real resolver itself
    lives in ``tests/test_http_dns_rebinding.py``.
    """
    import ipaddress

    from forgelm import _http

    def _hermetic_resolver(host):
        if not host:
            return None, "empty host"
        # IP literal: keep the real policy decision intact.
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        if literal is not None:
            if _http._is_blocked_ip(literal):
                return None, "Private/loopback/IMDS destination"
            return host, None
        # Hostname: stub.  ``localhost`` is the one canonical case the
        # existing SSRF-block test asserts on; treat it as loopback.
        if host == "localhost":
            return None, "Private/loopback/IMDS destination"
        return "8.8.8.8", None

    monkeypatch.setattr(_http, "_resolve_safe_destination", _hermetic_resolver)


def _make_config(webhook_cfg=None):
    """Create a minimal ForgeConfig with optional webhook."""
    data = {
        "model": {"name_or_path": "org/model"},
        "lora": {},
        "training": {},
        "data": {"dataset_name_or_path": "org/dataset"},
    }
    if webhook_cfg:
        data["webhook"] = webhook_cfg
    return ForgeConfig(**data)


class TestWebhookNotifier:
    def test_no_webhook_config(self):
        """Notifier should silently do nothing when webhook is not configured."""
        config = _make_config()
        notifier = WebhookNotifier(config)
        # Should not raise
        notifier.notify_start(run_name="test")
        notifier.notify_success(run_name="test", metrics={"loss": 0.5})
        notifier.notify_failure(run_name="test", reason="error")

    def test_no_url(self):
        """Notifier should do nothing when webhook has no url."""
        config = _make_config({"notify_on_start": True})
        notifier = WebhookNotifier(config)
        notifier.notify_start(run_name="test")

    @patch("forgelm._http.requests.Session.post")
    def test_notify_start(self, mock_post):
        config = _make_config({"url": "https://example.com/hook"})
        notifier = WebhookNotifier(config)
        notifier.notify_start(run_name="my_model_finetune")

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        payload = json.loads(call_kwargs.kwargs.get("data") or call_kwargs[1]["data"])
        assert payload["event"] == "training.start"
        assert payload["status"] == "started"
        assert payload["run_name"] == "my_model_finetune"

    @patch("forgelm._http.requests.Session.post")
    def test_notify_success_with_metrics(self, mock_post):
        config = _make_config({"url": "https://example.com/hook"})
        notifier = WebhookNotifier(config)
        metrics = {"eval_loss": 1.25, "train_loss": 0.8}
        notifier.notify_success(run_name="test_run", metrics=metrics)

        call_kwargs = mock_post.call_args
        payload = json.loads(call_kwargs.kwargs.get("data") or call_kwargs[1]["data"])
        assert payload["event"] == "training.success"
        assert payload["metrics"]["eval_loss"] == pytest.approx(1.25)

    @patch("forgelm._http.requests.Session.post")
    def test_notify_failure_with_reason(self, mock_post):
        config = _make_config({"url": "https://example.com/hook"})
        notifier = WebhookNotifier(config)
        notifier.notify_failure(run_name="test_run", reason="OOM error")

        call_kwargs = mock_post.call_args
        payload = json.loads(call_kwargs.kwargs.get("data") or call_kwargs[1]["data"])
        assert payload["event"] == "training.failure"
        assert payload["reason"] == "OOM error"

    @patch("forgelm._http._resolve_safe_destination", return_value=("8.8.8.8", None))
    @patch("forgelm._http.requests.Session.post")
    def test_url_env_resolution(self, mock_post, _mock_resolve):
        """``url_env`` resolution must drive the request to env.example.com.

        Post-issue-#14 the URL passed to ``Session.post`` is rebuilt with the
        resolved IP literal, so we assert via the ``Host`` header (which
        carries the original hostname) instead of the URL itself.  DNS is
        mocked because ``env.example.com`` does not resolve in real life
        (IANA only publishes A records for ``example.com`` itself).
        """
        config = _make_config({"url_env": "TEST_WEBHOOK_URL"})
        notifier = WebhookNotifier(config)

        with patch.dict(os.environ, {"TEST_WEBHOOK_URL": "https://env.example.com/hook"}):
            notifier.notify_start(run_name="test")

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        headers = call_kwargs.kwargs.get("headers") or {}
        assert headers.get("Host") == "env.example.com", (
            f"Host header should reflect the resolved env-var hostname; got {headers!r}"
        )

    @patch("forgelm._http.requests.Session.post")
    def test_notify_on_start_disabled(self, mock_post):
        config = _make_config(
            {
                "url": "https://example.com/hook",
                "notify_on_start": False,
            }
        )
        notifier = WebhookNotifier(config)
        notifier.notify_start(run_name="test")
        mock_post.assert_not_called()

    @patch("forgelm._http.requests.Session.post")
    def test_timeout_handled_gracefully(self, mock_post):
        import requests as req

        mock_post.side_effect = req.exceptions.Timeout("timed out")
        config = _make_config({"url": "https://example.com/hook"})
        notifier = WebhookNotifier(config)
        # Should not raise
        notifier.notify_start(run_name="test")

    @patch("forgelm._http.requests.Session.post")
    def test_connection_error_handled_gracefully(self, mock_post):
        import requests as req

        mock_post.side_effect = req.exceptions.ConnectionError("refused")
        config = _make_config({"url": "https://example.com/hook"})
        notifier = WebhookNotifier(config)
        # Should not raise
        notifier.notify_failure(run_name="test", reason="test error")

    @patch("forgelm._http.requests.Session.post")
    def test_payload_has_slack_attachments(self, mock_post):
        config = _make_config({"url": "https://example.com/hook"})
        notifier = WebhookNotifier(config)
        notifier.notify_start(run_name="test")

        call_kwargs = mock_post.call_args
        payload = json.loads(call_kwargs.kwargs.get("data") or call_kwargs[1]["data"])
        assert "attachments" in payload
        assert len(payload["attachments"]) == 1
        assert "title" in payload["attachments"][0]

    @patch("forgelm._http.requests.Session.post")
    def test_http_5xx_logs_warning(self, mock_post, caplog):
        """Non-2xx HTTP responses must emit a WARNING and not raise."""
        import logging

        mock_response = MagicMock()
        mock_response.ok = False
        mock_response.status_code = 503
        mock_response.text = "Service Unavailable"
        mock_post.return_value = mock_response

        config = _make_config({"url": "https://example.com/hook"})
        notifier = WebhookNotifier(config)

        with caplog.at_level(logging.WARNING, logger="forgelm.webhook"):
            notifier.notify_start(run_name="test_run")

        assert any("503" in r.message or "HTTP" in r.message for r in caplog.records)

    @patch("forgelm._http.requests.Session.post")
    def test_http_4xx_logs_warning(self, mock_post, caplog):
        """HTTP 4xx response must emit a WARNING log and not raise."""
        import logging

        mock_response = MagicMock()
        mock_response.ok = False
        mock_response.status_code = 404
        mock_response.text = "Not Found"
        mock_post.return_value = mock_response

        config = _make_config({"url": "https://example.com/hook"})
        notifier = WebhookNotifier(config)

        with caplog.at_level(logging.WARNING, logger="forgelm.webhook"):
            notifier.notify_failure(run_name="test_run", reason="OOM")

        assert any("404" in r.message or "HTTP" in r.message for r in caplog.records)

    @patch("forgelm._http.requests.Session.post")
    def test_require_https_true_refuses_http_url(self, mock_post, caplog):
        """F-P5-OPUS-12: with ``webhook.require_https=True`` a plaintext
        ``http://`` URL is refused by the SSRF chokepoint (HttpSafetyError →
        logged WARNING, no POST attempted) instead of warned-and-sent."""
        import logging

        config = _make_config({"url": "http://hooks.internal/abc", "require_https": True})  # NOSONAR python:S5332
        notifier = WebhookNotifier(config)

        with caplog.at_level(logging.WARNING, logger="forgelm.webhook"):
            notifier.notify_start(run_name="test_run")  # must not raise

        mock_post.assert_not_called()  # POST never attempted
        assert any("Refusing to post webhook" in r.message for r in caplog.records)

    @patch("forgelm._http.requests.Session.post")
    def test_require_https_false_permits_http_url(self, mock_post):
        """F-P5-OPUS-12: the default (require_https=False) preserves the
        documented warn-then-send behaviour — the POST is still attempted."""
        mock_response = MagicMock()
        mock_response.ok = True
        mock_post.return_value = mock_response

        config = _make_config({"url": "http://hooks.internal/abc"})  # NOSONAR python:S5332
        notifier = WebhookNotifier(config)
        notifier.notify_start(run_name="test_run")

        mock_post.assert_called_once()  # cleartext delivery permitted by default

    @patch("forgelm._http.requests.Session.post")
    def test_generic_request_exception_logs_warning_not_error(self, mock_post, caplog):
        """F-P4-OPUS-31: a bare ``requests.RequestException`` is an EXPECTED
        transport failure — it must log at WARNING with no traceback and no
        'Unexpected' wording, not at ERROR via logger.exception."""
        import logging

        import requests as req

        mock_post.side_effect = req.RequestException("TLS reset")
        config = _make_config({"url": "https://example.com/hook"})
        notifier = WebhookNotifier(config)

        with caplog.at_level(logging.DEBUG, logger="forgelm.webhook"):
            notifier.notify_start(run_name="test_run")  # must not raise

        transport_recs = [r for r in caplog.records if "transport error" in r.message]
        assert transport_recs, "expected a 'transport error' WARNING record"
        assert all(r.levelno == logging.WARNING for r in transport_recs)
        # No ERROR-level record and no 'Unexpected' mislabelling / traceback.
        assert not any(r.levelno >= logging.ERROR for r in caplog.records)
        assert not any("Unexpected" in r.message for r in caplog.records)
        assert all(r.exc_info is None for r in transport_recs)  # no traceback attached

    @patch("forgelm._http.requests.Session.post")
    def test_missing_requests_toolbelt_does_not_crash_run(self, mock_post, monkeypatch, caplog):
        """A missing ``requests-toolbelt`` at runtime must not crash the run.

        ``_http._pinned_session('https')`` raises a bare ``ImportError`` (NOT a
        ``requests.RequestException``) when the HTTPS IP-pinning adapter is
        unavailable — a broken ``--no-deps`` / vendored / frozen venv.  Pre-fix
        that ImportError propagated straight out of ``notify_*`` and would crash
        an otherwise-successful training run at the final notification step,
        violating the module's "notify_* is never allowed to fail the run"
        contract.  ``_post_payload`` must now catch it and swallow with the same
        non-fatal WARNING semantics as the transport branches.
        """
        import logging

        from forgelm import _http

        # Simulate the broken-venv state: the HTTPS IP-pinning adapter sentinel
        # is None, so ``_pinned_session('https')`` raises ImportError before any
        # ``Session.post`` is attempted.
        monkeypatch.setattr(_http, "_PortStrippingSSLAdapter", None)

        config = _make_config({"url": "https://example.com/hook"})
        notifier = WebhookNotifier(config)

        with caplog.at_level(logging.WARNING, logger="forgelm.webhook"):
            notifier.notify_start(run_name="test_run")  # must NOT raise ImportError

        mock_post.assert_not_called()  # never reached the network layer
        assert any("dependency unavailable" in r.message for r in caplog.records), (
            "the ImportError must be logged as a non-fatal dependency-unavailable warning"
        )

    @patch("forgelm._http.requests.Session.post")
    def test_mixed_case_http_scheme_still_warns_unencrypted(self, mock_post, caplog):
        """A mixed-case ``HTTP://`` URL must still trigger the plaintext warning.

        ``urlparse`` lower-cases the scheme, so ``HTTP://`` routes through the
        exact same cleartext delivery path downstream — but the operator-facing
        'unencrypted' warning used a case-sensitive ``startswith('http://')``
        prefix and silently skipped it, reducing visibility into an unencrypted
        delivery.  The parsed lower-cased scheme check must fire it.
        """
        import logging

        mock_response = MagicMock()
        mock_response.ok = True
        mock_post.return_value = mock_response

        config = _make_config({"url": "HTTP://hooks.internal/abc"})  # NOSONAR python:S5332
        notifier = WebhookNotifier(config)

        with caplog.at_level(logging.WARNING, logger="forgelm.webhook"):
            notifier.notify_start(run_name="test_run")

        assert any("unencrypted" in r.message.lower() for r in caplog.records), (
            "mixed-case HTTP:// must still trigger the plaintext-delivery warning"
        )
        mock_post.assert_called_once()  # cleartext delivery still attempted by default


class TestMaskUrl:
    """F-P5-OPUS-06 / F-P5-OPUS-11 regression: ``WebhookNotifier._mask`` must
    strip the *entire* path (not just userinfo/query) so a custom receiver
    whose secret is the first path segment (``https://host/<TOKEN>``) does not
    leak into operator logs, and the masking policy is the single
    ``forgelm._http._mask_netloc`` chokepoint.
    """

    # NOSONAR test fixture — fragment-built so secret scanners don't flag it.
    _SECRET = "aZ9" + "SECRETTOKEN"  # noqa: S105

    def test_mask_strips_first_path_segment_for_custom_receiver(self):
        masked = WebhookNotifier._mask(f"https://hook.mycorp.internal/{self._SECRET}")
        assert self._SECRET not in masked
        assert masked == "https://hook.mycorp.internal"

    @pytest.mark.parametrize(
        "url",
        [
            "https://hooks.slack.com/services/T0/B0/SECRET",
            "https://discord.com/api/webhooks/123/SECRET",
            "https://outlook.office.com/webhook/abc/IncomingWebhook/SECRET",
            "https://hook.mycorp.internal/SECRET",
            "https://user:pass@hook.mycorp.internal/SECRET?sig=SECRET",
        ],
    )
    def test_mask_never_leaks_secret_across_receiver_shapes(self, url):
        masked = WebhookNotifier._mask(url)
        assert "SECRET" not in masked
        assert masked.startswith("https://")

    def test_mask_delegates_to_http_chokepoint(self):
        """The webhook masker must produce identical output to the single
        ``_http._mask_netloc`` chokepoint (no divergent second policy)."""
        from forgelm._http import _mask_netloc

        url = "https://user:pass@hooks.example.com/services/T0/B0/TOKEN?sig=x"
        assert WebhookNotifier._mask(url) == _mask_netloc(url)


class TestSafePostHttpDiscipline:
    """Direct unit tests for forgelm._http.safe_post.

    These cover the policy gates that every outbound HTTP call site relies
    on. The Phase 7 closure adds judge + synthetic + (existing) webhook to
    the call-site list; the gates must reject misconfigured URLs identically
    across all of them.

    NOTE for static analysers: the literals in this class deliberately
    include RFC1918 / loopback / IMDS / multicast IP addresses, plain
    ``http://`` URLs, and ``ftp://`` URLs. These are not security
    vulnerabilities — they are the inputs the test asserts the SSRF /
    scheme guard rejects. Removing them would erase the coverage of those
    rejections.
    """

    @pytest.mark.parametrize(
        "url",
        [
            "https://10.0.0.1/hook",  # NOSONAR RFC1918 (10/8) — SSRF guard fixture
            "https://172.16.0.5/hook",  # NOSONAR RFC1918 (172.16/12) — SSRF guard fixture
            "https://192.168.1.10/hook",  # NOSONAR RFC1918 (192.168/16) — SSRF guard fixture
            "https://127.0.0.1/hook",  # NOSONAR loopback — SSRF guard fixture
            "https://169.254.169.254/latest/meta-data/",  # NOSONAR AWS IMDS — SSRF guard fixture
            "https://224.0.0.1/multicast",  # NOSONAR multicast — SSRF guard fixture
        ],
    )
    def test_ssrf_block_private_ip(self, url):
        """Each private/loopback/IMDS/multicast destination must raise."""
        from forgelm._http import HttpSafetyError, safe_post

        with pytest.raises(HttpSafetyError, match="Private/loopback/IMDS"):
            safe_post(url, json={}, timeout=10.0)

    def test_ssrf_block_can_be_opted_out(self):
        """``allow_private=True`` bypasses the SSRF guard (operator opt-in).

        The opt-in path deliberately keeps the legacy ``requests.post``
        flow (no Session, no IP pinning) because internal DNS / split-
        horizon resolution is the right model for an operator-blessed
        in-cluster destination — see safe_post's ``allow_private`` branch.
        """
        from forgelm import _http

        with patch.object(_http.requests, "post") as mock_post:
            mock_post.return_value = MagicMock(ok=True, status_code=200)
            _http.safe_post(
                "https://10.0.0.1/hook",  # NOSONAR RFC1918 — SSRF opt-out fixture
                json={},
                timeout=10.0,
                allow_private=True,
            )
            mock_post.assert_called_once()

    def test_redirect_block(self):
        """allow_redirects=False is forwarded to requests.post."""
        from forgelm import _http

        with patch.object(_http.requests.Session, "post") as mock_post:
            mock_post.return_value = MagicMock(ok=True, status_code=200)
            _http.safe_post("https://example.com/hook", json={}, timeout=10.0)
            kwargs = mock_post.call_args.kwargs
            assert kwargs["allow_redirects"] is False

    def test_http_block(self):
        """Verify plain-HTTP URLs are rejected unless allow_insecure_http is set."""
        # http:// literals below are scheme-blocker fixtures; no insecure
        # outbound call is made — the test asserts the guard raises.
        from forgelm._http import HttpSafetyError, safe_post

        with pytest.raises(HttpSafetyError, match="http://"):  # NOSONAR python:S5332
            safe_post("http://example.com/hook", json={}, timeout=10.0)  # NOSONAR python:S5332

    def test_http_allowed_with_opt_in(self):
        """allow_insecure_http=True (used by webhook) lets http:// through."""
        # http:// literal is the opt-in fixture covering the webhook
        # back-compat path; the post is mocked, no real call is made.
        from forgelm import _http

        with patch.object(_http.requests.Session, "post") as mock_post:
            mock_post.return_value = MagicMock(ok=True, status_code=200)
            _http.safe_post(
                "http://example.com/hook",  # NOSONAR python:S5332
                json={},
                timeout=10.0,
                allow_insecure_http=True,
            )
            mock_post.assert_called_once()

    def test_unsupported_scheme(self):
        """Verify non-http(s) schemes (e.g., ftp, file) are rejected even with allow_insecure_http."""
        # The ftp:// literal below is a scheme-blocker fixture; no outbound
        # call is made — the test asserts the guard raises.
        from forgelm._http import HttpSafetyError, safe_post

        with pytest.raises(HttpSafetyError, match="Unsupported URL scheme"):
            safe_post(
                "ftp://example.com/hook",  # NOSONAR python:S5332
                json={},
                timeout=10.0,
                allow_insecure_http=True,
            )

    def test_timeout_floor_rejects_below_default(self):
        """timeout below the 10s default floor must raise."""
        from forgelm._http import HttpSafetyError, safe_post

        with pytest.raises(HttpSafetyError, match="Timeout below"):
            safe_post("https://example.com/hook", json={}, timeout=5.0)

    def test_timeout_zero_rejected_even_with_lower_floor(self):
        """timeout=0 is always rejected (requests treats it as 'no timeout')."""
        from forgelm._http import HttpSafetyError, safe_post

        with pytest.raises(HttpSafetyError, match="Timeout below"):
            safe_post(
                "https://example.com/hook",
                json={},
                timeout=0,
                min_timeout=1.0,
            )

    def test_timeout_floor_overridable(self):
        """Webhook passes min_timeout=1.0 to keep its historical floor."""
        from forgelm import _http

        with patch.object(_http.requests.Session, "post") as mock_post:
            mock_post.return_value = MagicMock(ok=True, status_code=200)
            _http.safe_post(
                "https://example.com/hook",
                json={},
                timeout=2.0,
                min_timeout=1.0,
            )
            mock_post.assert_called_once()

    def test_header_masking_on_error(self, caplog):
        """Authorization / X-API-Key values are redacted from the failure log."""
        import logging

        import requests as req

        from forgelm import _http

        # NOSONAR test fixture, fragment-built (rule python:S2068 hard-coded credential false-positive)
        bearer_token = "sk-" + "supersecret123"  # noqa: S105
        with patch.object(_http.requests.Session, "post") as mock_post:
            mock_post.side_effect = req.exceptions.ConnectionError(f"refused while sending Bearer {bearer_token}")
            with caplog.at_level(logging.WARNING, logger="forgelm._http"):
                with pytest.raises(req.exceptions.ConnectionError):
                    _http.safe_post(
                        "https://example.com/hook",
                        json={},
                        headers={"Authorization": f"Bearer {bearer_token}"},
                        timeout=10.0,
                    )

        # The bearer token must be masked from the warning log.
        log_text = " ".join(r.message for r in caplog.records)
        assert bearer_token not in log_text
        assert "[REDACTED]" in log_text

    def test_localhost_blocked_by_hostname(self):
        """'localhost' resolves to 127.0.0.1; SSRF guard must catch it."""
        from forgelm._http import HttpSafetyError, safe_post

        with pytest.raises(HttpSafetyError, match="Private/loopback"):
            safe_post("https://localhost/hook", json={}, timeout=10.0)


class TestLifecycleVocabulary:
    """Faz 8: notify_reverted + notify_awaiting_approval lifecycle events.

    These pin the wire-format of the two new payload events so dashboards
    that already filter on event="training.reverted" / "approval.required"
    don't silently break on a future refactor.
    """

    @patch("forgelm._http.requests.Session.post")
    def test_notify_reverted_payload(self, mock_post):
        """Auto-revert event must serialize as event=training.reverted with masked + truncated reason."""
        config = _make_config({"url": "https://example.com/hook"})
        notifier = WebhookNotifier(config)

        # Reason carries a Slack webhook secret + a long padding so we can
        # assert both the masking and the 2048-char truncation paths.
        # Token built fragment-by-fragment per docs/standards/regex.md Rule 7
        # so GitHub secret scanning + gitleaks don't flag the literal.
        leaky_token = "xoxb-" + "12345678901" + "-" + "1234567890123" + "-" + "AbCdEfGhIjKlMnOpQrStUvWx"
        long_pad = "X" * 3000
        reason = f"safety gate failed: {leaky_token} traceback: {long_pad}"

        notifier.notify_reverted(run_name="my_run", reason=reason)

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        payload = json.loads(call_kwargs.kwargs.get("data") or call_kwargs[1]["data"])

        assert payload["event"] == "training.reverted"
        assert payload["status"] == "reverted"
        assert payload["run_name"] == "my_run"
        assert leaky_token not in payload["reason"], "Slack token must be redacted"
        assert leaky_token not in payload["attachments"][0]["text"], (
            "Slack token must be redacted in attachment text too"
        )
        # Truncated to 2048 + "… (truncated)" marker.
        assert len(payload["reason"]) <= 2048 + len("… (truncated)")
        assert payload["reason"].endswith("… (truncated)")

    @patch("forgelm._http.requests.Session.post")
    def test_notify_reverted_distinct_from_failure(self, mock_post):
        """training.reverted must not collide with training.failure (dashboards rely on this split)."""
        config = _make_config({"url": "https://example.com/hook"})
        notifier = WebhookNotifier(config)

        notifier.notify_reverted(run_name="r", reason="judge below threshold")

        call_kwargs = mock_post.call_args
        payload = json.loads(call_kwargs.kwargs.get("data") or call_kwargs[1]["data"])

        assert payload["event"] == "training.reverted"
        assert payload["event"] != "training.failure"
        # Color must signal "reverted" (warning orange), not "failed" (red).
        assert payload["attachments"][0]["color"] == "#ff9900"

    @patch("forgelm._http.requests.Session.post")
    def test_notify_awaiting_approval_payload(self, mock_post):
        """Approval gate must serialize as event=approval.required with model_path included."""
        config = _make_config({"url": "https://example.com/hook"})
        notifier = WebhookNotifier(config)

        notifier.notify_awaiting_approval(
            run_name="my_run",
            model_path="/var/forgelm/runs/abc/final_model",  # NOSONAR — payload string fixture, no fs op
        )

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        payload = json.loads(call_kwargs.kwargs.get("data") or call_kwargs[1]["data"])

        assert payload["event"] == "approval.required"
        assert payload["status"] == "awaiting_approval"
        assert payload["run_name"] == "my_run"
        # NOSONAR — string literal, not a real filesystem operation
        assert payload["model_path"] == "/var/forgelm/runs/abc/final_model"
        assert "/var/forgelm/runs/abc/final_model" in payload["attachments"][0]["text"]

    @patch("forgelm._http.requests.Session.post")
    def test_notify_awaiting_approval_no_model_weights_in_payload(self, mock_post):
        """Security: approval payload must carry the staging path only, never weight bytes or tensor dumps."""
        config = _make_config({"url": "https://example.com/hook"})
        notifier = WebhookNotifier(config)

        notifier.notify_awaiting_approval(
            run_name="r",
            # Path string is sent as a webhook field; nothing is written to
            # the filesystem in this test, so the publicly-writable-tmp
            # concern (Sonar S5443) does not apply.  Use a project-relative
            # placeholder shape to keep the test honest about that.
            model_path="./outputs/run-r/final_model.staging",
        )

        call_kwargs = mock_post.call_args
        payload = json.loads(call_kwargs.kwargs.get("data") or call_kwargs[1]["data"])

        # Schema is fixed: event/run_name/status/metrics/reason/model_path/attachments.
        # No weight-shaped fields. Anything else means a future regression
        # snuck a sensitive blob into the wire format.
        allowed_keys = {"event", "run_name", "status", "metrics", "reason", "model_path", "attachments"}
        assert set(payload.keys()) == allowed_keys
        # Belt-and-braces: the canonical weight-blob field names must never
        # appear in the serialized payload.
        serialized = json.dumps(payload)
        for forbidden in ("state_dict", "model.safetensors", "pytorch_model.bin", "adapter_model"):
            assert forbidden not in serialized, f"Payload must not carry {forbidden!r}"

    def test_emitted_events_match_documented_vocabulary(self):
        """XP-05 / F-P4-OPUS-08,32: the set of wire events emitted by
        WebhookNotifier must equal the documented vocabulary (8 events). The
        docs previously claimed only 'five' while the code emits eight."""
        import pathlib
        import re

        repo = pathlib.Path(__file__).resolve().parent.parent
        src = (repo / "forgelm" / "webhook.py").read_text(encoding="utf-8")
        emitted = set(re.findall(r'event\s*=\s*"([^"]+)"', src))
        documented = {
            "training.start",
            "training.success",
            "training.failure",
            "training.reverted",
            "approval.required",
            "pipeline.started",
            "pipeline.completed",
            "pipeline.stage_reverted",
        }
        assert emitted == documented, f"webhook event vocabulary drifted: {emitted ^ documented}"
        assert len(emitted) == 8

    def test_doc_numerical_claims_guard_passes_for_webhook_events(self):
        """The check_doc_numerical_claims helper must agree with the doc copy:
        canonical webhook_events count == the number cited in the docs."""
        import pathlib
        import sys

        tools_dir = str(pathlib.Path(__file__).resolve().parent.parent / "tools")
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)
        import check_doc_numerical_claims as mod  # noqa: PLC0415

        assert mod.canonical_webhook_events() == 8
        # No 'webhook_events' mismatch should be reported by the guard.
        assert mod.main(["--quiet"]) == 0


class TestWebhookPersistRoundTrip:
    """Regression coverage for the approve/reject AttributeError crash (XP-03).

    The compliance manifest persisted a ``_WEBHOOK_PERSIST_FIELDS`` set that had
    drifted from the real ``WebhookConfig`` schema (six fields that don't exist,
    the real ``url``/``notify_on_start``/``timeout`` dropped).  When
    ``forgelm approve``/``reject`` rebuilt a notifier from that JSON via a
    ``SimpleNamespace``, ``_resolve_url`` read ``self.config.url`` and raised
    ``AttributeError`` — *after* the model was already promoted and the granted
    audit event committed, violating the public exit-code contract.
    """

    def test_persist_fields_match_schema_minus_url(self):
        """Single source of truth: the persisted set is exactly the live
        schema minus the secret-bearing ``url``.  Any drift re-arms the crash."""
        from forgelm.compliance import _WEBHOOK_PERSIST_FIELDS
        from forgelm.config import WebhookConfig

        assert set(_WEBHOOK_PERSIST_FIELDS) == set(WebhookConfig.model_fields) - {"url"}
        assert "url" not in _WEBHOOK_PERSIST_FIELDS, "secret-bearing url must never be persisted"

    def test_resolve_url_tolerates_namespace_without_url(self):
        """The crash root: a rebuilt config namespace need not carry ``url``.
        ``_resolve_url`` must return None, not raise AttributeError."""
        import types

        from forgelm.compliance import _WEBHOOK_PERSIST_FIELDS

        ns = types.SimpleNamespace(**{k: None for k in _WEBHOOK_PERSIST_FIELDS})
        assert not hasattr(ns, "url")

        class _Carrier:
            webhook = ns

        notifier = WebhookNotifier(_Carrier())
        assert notifier._resolve_url() is None  # must not raise

    def test_manifest_persists_url_env_not_url(self):
        """A configured inline ``url`` secret must not reach the manifest;
        ``url_env`` (the env-indirection channel) must survive the round-trip."""
        from forgelm.compliance import _WEBHOOK_PERSIST_FIELDS, generate_training_manifest

        config = _make_config({"url": "https://hooks.example.com/SECRET/TOKEN", "url_env": "FORGELM_TEST_HOOK"})
        manifest = generate_training_manifest(config, metrics={"loss": 1.0})

        webhook_config = manifest["webhook_config"]
        assert set(webhook_config) == set(_WEBHOOK_PERSIST_FIELDS)
        assert "url" not in webhook_config
        assert "SECRET" not in json.dumps(webhook_config), "inline url secret leaked into manifest"
        assert webhook_config["url_env"] == "FORGELM_TEST_HOOK"

    def test_approval_notifier_rebuild_does_not_crash(self, tmp_path):
        """End-to-end production path: persist a manifest for a webhook-configured
        run, rebuild the notifier via the real ``_build_approval_notifier``, and
        fire the success notification.  Pre-fix this raised AttributeError."""
        import forgelm.cli as cli_facade
        from forgelm.compliance import generate_training_manifest

        # url_env points at an UNSET env var → resolves to None → clean no-op,
        # so no real POST is attempted; the regression is the rebuild itself.
        config = _make_config({"url_env": "FORGELM_TEST_HOOK_UNSET", "notify_on_success": True})
        manifest = generate_training_manifest(config, metrics={"loss": 1.0})

        compliance_dir = tmp_path / "compliance"
        compliance_dir.mkdir()
        (compliance_dir / "compliance_report.json").write_text(json.dumps(manifest), encoding="utf-8")

        notifier = cli_facade._build_approval_notifier(str(tmp_path))
        # Must not raise AttributeError on the rebuilt SimpleNamespace.
        notifier.notify_success(run_name="approved", metrics={"loss": 1.0})


class TestExtraPayloadAllowlist:
    """F-PR54-M11: ``_send(**extra)`` is screened by an explicit allowlist.

    ``**extra`` is an unbounded funnel into an outbound HTTP body. Every
    caller today is orchestrator-internal, so this is hygiene rather than a
    live leak — the guarded case is a *future* caller threading user- or
    config-derived text through ``_send`` and having it land on a
    third-party receiver.
    """

    # Keyword parameters ``_send`` declares explicitly; anything else a
    # ``notify_*`` method passes lands in ``**extra``.
    @staticmethod
    def _declared_send_params():
        import inspect

        return {
            name
            for name, param in inspect.signature(WebhookNotifier._send).parameters.items()
            if param.kind is inspect.Parameter.KEYWORD_ONLY
        }

    def _record_send(self, monkeypatch):
        """Replace ``_send`` with a recorder and return the captured-call list."""
        calls = []

        def _recorder(_self, **kwargs):
            calls.append(kwargs)

        monkeypatch.setattr(WebhookNotifier, "_send", _recorder)
        return calls

    def _drive_every_notifier(self, monkeypatch):
        """Call every shipped ``notify_*`` method once; return captured kwargs."""
        calls = self._record_send(monkeypatch)
        config = _make_config(
            {
                "url": "https://example.com/hook",
                "notify_on_start": True,
                "notify_on_success": True,
                "notify_on_failure": True,
            }
        )
        n = WebhookNotifier(config)
        n.notify_start(run_name="r")
        n.notify_success(run_name="r", metrics={"loss": 0.5})
        n.notify_failure(run_name="r", reason="boom")
        n.notify_reverted(run_name="r", reason="judge said no")
        n.notify_awaiting_approval(run_name="r", model_path="/tmp/staging")
        n.notify_pipeline_started(run_id="r", stage_count=3)
        n.notify_pipeline_completed(run_id="r", final_status="completed", stopped_at=None)
        n.notify_pipeline_reverted(run_id="r", stage_name="sft-warmup", reason="revert")
        assert len(calls) == 8, "a notify_* method did not reach _send"
        return calls

    def test_allowlist_exactly_matches_keys_the_notifiers_pass(self, monkeypatch):
        """The whole point of the allowlist: it must neither drop a field a
        shipped notifier sends, nor carry a dead entry.

        This is the CI-time loudness that justifies WARN-and-drop at runtime
        (see ``_screen_extras``). A contributor who adds an extra kwarg to a
        ``notify_*`` method without registering it fails here rather than
        silently losing the field in an operator's Slack channel.
        """
        from forgelm.webhook import _ALLOWED_EXTRA_PAYLOAD_KEYS

        declared = self._declared_send_params()
        passed = set()
        for kwargs in self._drive_every_notifier(monkeypatch):
            passed |= set(kwargs) - declared

        assert passed == set(_ALLOWED_EXTRA_PAYLOAD_KEYS), (
            "webhook extra-payload allowlist drifted from what the notifiers pass: "
            f"{passed ^ set(_ALLOWED_EXTRA_PAYLOAD_KEYS)}"
        )

    @patch("forgelm._http.requests.Session.post")
    def test_allowlisted_extras_reach_the_wire(self, mock_post):
        """Screening must not regress the Phase 14 fix — the registered
        pipeline fields still land in the serialized payload."""
        config = _make_config({"url": "https://example.com/hook", "notify_on_failure": True})
        notifier = WebhookNotifier(config)
        notifier.notify_pipeline_reverted(run_id="p1", stage_name="dpo-align", reason="gate failed")

        payload = json.loads(mock_post.call_args.kwargs["data"])
        assert payload["event"] == "pipeline.stage_reverted"
        assert payload["stage_name"] == "dpo-align"

    @patch("forgelm._http.requests.Session.post")
    def test_unregistered_key_is_dropped_and_warned(self, mock_post, caplog):
        """An unregistered extra never reaches the wire, and the drop is loud.

        WARN-and-drop rather than raise: ``notify_*`` is a best-effort side
        channel that must never abort a completed run. The drop is not a
        silent failure — ``error-handling.md`` requires it be logged, and
        this asserts that it is.
        """
        config = _make_config({"url": "https://example.com/hook"})
        notifier = WebhookNotifier(config)

        with caplog.at_level("WARNING", logger="forgelm.webhook"):
            notifier._send(
                event="training.start",
                run_name="r",
                status="started",
                title="t",
                text="x",
                customer_email="alice@example.com",
            )

        payload = json.loads(mock_post.call_args.kwargs["data"])
        assert "customer_email" not in payload
        assert "alice@example.com" not in json.dumps(payload)
        assert "customer_email" in caplog.text
        assert "allowlist" in caplog.text

    @patch("forgelm._http.requests.Session.post")
    def test_unregistered_key_does_not_abort_the_notification(self, mock_post):
        """The rest of the payload still ships — a rogue field degrades the
        notification, it does not cancel it, and it does not raise."""
        config = _make_config({"url": "https://example.com/hook"})
        notifier = WebhookNotifier(config)
        notifier._send(
            event="training.success",
            run_name="r",
            status="succeeded",
            title="t",
            text="x",
            bogus="drop me",
        )
        mock_post.assert_called_once()
        payload = json.loads(mock_post.call_args.kwargs["data"])
        assert payload["event"] == "training.success"
        assert payload["run_name"] == "r"

    @patch("forgelm._http.requests.Session.post")
    def test_non_scalar_allowlisted_value_is_dropped_not_serialized(self, mock_post):
        """A registered key carrying a non-JSON-scalar must not crash the
        notifier: ``json.dumps`` would raise inside ``_post_payload``, which
        propagates out of ``notify_*`` and kills an otherwise-successful run."""
        config = _make_config({"url": "https://example.com/hook"})
        notifier = WebhookNotifier(config)
        notifier._send(
            event="pipeline.completed",
            run_name="r",
            status="completed",
            title="t",
            text="x",
            stopped_at=object(),
        )
        mock_post.assert_called_once()
        payload = json.loads(mock_post.call_args.kwargs["data"])
        assert "stopped_at" not in payload

    @patch("forgelm._http.requests.Session.post")
    def test_none_valued_extra_is_preserved(self, mock_post):
        """``stopped_at=None`` is meaningful (pipeline finished cleanly) and
        must survive the type screen rather than being filtered out."""
        config = _make_config({"url": "https://example.com/hook", "notify_on_success": True})
        notifier = WebhookNotifier(config)
        notifier.notify_pipeline_completed(run_id="p", final_status="completed", stopped_at=None)

        payload = json.loads(mock_post.call_args.kwargs["data"])
        assert "stopped_at" in payload
        assert payload["stopped_at"] is None

    @patch("forgelm._http.requests.Session.post")
    def test_allowlisted_string_values_are_secret_masked(self, mock_post):
        """Stage names come from operator YAML, so an allowlisted string is
        config-derived text on an outbound wire — same category as ``reason``.
        The allowlist must cooperate with the existing masking, not bypass it.
        """
        config = _make_config({"url": "https://example.com/hook", "notify_on_failure": True})
        notifier = WebhookNotifier(config)
        token = "ghp_" + "A" * 36
        notifier.notify_pipeline_reverted(run_id="p", stage_name=f"stage-{token}", reason="r")

        # Assert on the raw serialized body, not just the ``stage_name`` field:
        # the first version of this fix masked the field but interpolated the
        # RAW stage name into the Slack ``attachments[0].text`` prose, so the
        # token shipped anyway. Masking is applied to the assembled payload.
        assert token not in mock_post.call_args.kwargs["data"], "secret-shaped stage name left the process unmasked"
        payload = json.loads(mock_post.call_args.kwargs["data"])
        assert "[REDACTED-SECRET]" in payload["stage_name"]
        assert "[REDACTED-SECRET]" in payload["attachments"][0]["text"]

    @patch("forgelm._http.requests.Session.post")
    def test_run_name_and_attachment_prose_are_masked(self, mock_post):
        """``run_name`` is config-derived and is interpolated into the
        attachment title/text by every notifier — it must be masked in all
        three places, not only in the top-level field."""
        config = _make_config({"url": "https://example.com/hook", "notify_on_start": True})
        notifier = WebhookNotifier(config)
        token = "ghp_" + "B" * 36
        notifier.notify_start(run_name=f"run-{token}")

        body = mock_post.call_args.kwargs["data"]
        assert token not in body
        payload = json.loads(body)
        assert "[REDACTED-SECRET]" in payload["run_name"]
        assert "[REDACTED-SECRET]" in payload["attachments"][0]["title"]

    @patch("forgelm._http.requests.Session.post")
    def test_routing_fields_are_exempt_from_masking(self, mock_post):
        """``event`` / ``status`` / ``color`` are closed-set code literals that
        receivers route on; the masking sweep must leave them byte-identical."""
        config = _make_config({"url": "https://example.com/hook", "notify_on_failure": True})
        notifier = WebhookNotifier(config)
        notifier.notify_reverted(run_name="r", reason="gate failed")

        payload = json.loads(mock_post.call_args.kwargs["data"])
        assert payload["event"] == "training.reverted"
        assert payload["status"] == "reverted"
        assert payload["attachments"][0]["color"] == "#ff9900"

    @patch("forgelm._http.requests.Session.post")
    def test_masker_unavailable_redacts_rather_than_ships_raw(self, mock_post, monkeypatch):
        """If ``data_audit`` cannot be imported we refuse to ship un-scrubbed
        text — the pre-existing stance for ``reason``, now applied to every
        free-text field. Routing fields must survive so the ping still
        correlates."""
        import builtins

        real_import = builtins.__import__

        def _no_data_audit(name, *args, **kwargs):
            if name.endswith("data_audit") or ".data_audit" in name:
                raise ImportError("simulated: data_audit unavailable")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _no_data_audit)
        config = _make_config({"url": "https://example.com/hook", "notify_on_start": True})
        notifier = WebhookNotifier(config)
        notifier.notify_start(run_name="secret-bearing-run")

        payload = json.loads(mock_post.call_args.kwargs["data"])
        assert "secret-bearing-run" not in json.dumps(payload)
        assert payload["run_name"] == "[REDACTED — secrets masker unavailable]"
        assert payload["event"] == "training.start"
        assert payload["status"] == "started"

    @patch("forgelm._http.requests.Session.post")
    def test_screening_happens_before_transport_not_after(self, mock_post):
        """Defence in depth: assert on the bytes handed to the HTTP layer, not
        on an intermediate dict, so a future refactor that screens a copy while
        posting the original is caught."""
        config = _make_config({"url": "https://example.com/hook"})
        notifier = WebhookNotifier(config)
        notifier._send(
            event="training.start",
            run_name="r",
            status="started",
            title="t",
            text="x",
            internal_api_key="sk-live-should-never-ship",
        )
        assert "sk-live-should-never-ship" not in mock_post.call_args.kwargs["data"]
