"""Issue #14 regression tests — DNS-rebinding TOCTOU hardening for the
webhook / judge / synthetic SSRF guard.

Pre-fix: ``_is_private_destination()`` ran a DNS lookup, then
``requests.post()`` ran ANOTHER DNS lookup at connect time.  An
attacker-controlled DNS server with TTL=0 could return a public IP on
the first lookup (passing the guard) and a private IP on the second
(when ``requests`` connected), leaking the payload + bearer token to a
private destination.

Post-fix: ``_resolve_safe_destination()`` resolves the hostname once and
the call site reuses the returned IP literal in the URL.  The original
hostname is propagated via the ``Host`` header and (for HTTPS) the SNI
extension of ``requests_toolbelt.adapters.host_header_ssl.
HostHeaderSSLAdapter``, so virtual-hosting endpoints and certificate
validation still work.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestResolveSafeDestination:
    """Unit-level coverage of the resolver helper."""

    def test_public_hostname_returns_first_public_ip(self):
        from forgelm import _http

        # Two public-A addrinfo entries.
        with patch.object(
            _http.socket,
            "getaddrinfo",
            return_value=[
                (0, 0, 0, "", ("8.8.8.8", 0)),
                (0, 0, 0, "", ("8.8.4.4", 0)),
            ],
        ):
            ip, err = _http._resolve_safe_destination("hooks.example.com")
        assert err is None
        assert ip == "8.8.8.8"

    def test_private_ip_in_resolution_blocks(self):
        """Even one private answer in addrinfo flips the verdict to blocked."""
        from forgelm import _http

        with patch.object(
            _http.socket,
            "getaddrinfo",
            return_value=[
                (0, 0, 0, "", ("8.8.8.8", 0)),
                (0, 0, 0, "", ("10.0.0.1", 0)),  # NOSONAR RFC1918 — guard fixture
            ],
        ):
            ip, err = _http._resolve_safe_destination("attacker.example.com")
        assert ip is None
        assert "Private" in err and "IMDS" in err

    def test_dns_failure_blocks_with_reason(self):
        from forgelm import _http

        with patch.object(_http.socket, "getaddrinfo", side_effect=_http.socket.gaierror("nodename")):
            ip, err = _http._resolve_safe_destination("no-such.example.com")
        assert ip is None
        assert err.startswith("DNS resolution failed")

    def test_empty_host_blocks(self):
        from forgelm._http import _resolve_safe_destination

        ip, err = _resolve_safe_destination("")
        assert ip is None
        assert err == "empty host"

    def test_public_ip_literal_passes_through(self):
        from forgelm._http import _resolve_safe_destination

        ip, err = _resolve_safe_destination("8.8.8.8")
        assert err is None
        assert ip == "8.8.8.8"

    def test_private_ip_literal_blocks(self):
        from forgelm._http import _resolve_safe_destination

        ip, err = _resolve_safe_destination("169.254.169.254")  # NOSONAR AWS IMDS — guard fixture
        assert ip is None
        assert "Private" in err


class TestDnsRebindingClosed:
    """Behavioural test: a TOCTOU-style rebinding cannot leak the payload.

    Simulates a DNS server that returns a public IP on the first lookup
    (the guard's call) and a private IP on a hypothetical second lookup
    (what the old code path would have invoked from inside requests).
    The hardened path must call ``getaddrinfo`` exactly once and pin the
    public IP into the outbound URL.
    """

    def test_getaddrinfo_called_exactly_once_and_pins_public_ip(self):
        from forgelm import _http

        # First call: public. If the code ever called a second time
        # (the TOCTOU window), it would get the IMDS address.  The
        # assertion below asserts the second call never happens.
        responses = iter(
            [
                [(0, 0, 0, "", ("8.8.8.8", 0))],  # 1st call — public
                [(0, 0, 0, "", ("169.254.169.254", 0))],  # 2nd call — would be IMDS  # NOSONAR
            ]
        )

        def fake_resolve(*_args, **_kwargs):
            try:
                return next(responses)
            except StopIteration:
                pytest.fail("getaddrinfo called more than once — DNS rebinding window is still open")

        with (
            patch.object(_http.socket, "getaddrinfo", side_effect=fake_resolve) as resolve_mock,
            patch.object(_http.requests.Session, "post") as mock_post,
        ):
            mock_post.return_value = MagicMock(ok=True, status_code=200)
            _http.safe_post("https://hooks.example.com/abc", json={}, timeout=10.0)

        assert resolve_mock.call_count == 1, (
            f"DNS rebinding fix requires exactly one resolve per safe_post call, got {resolve_mock.call_count}"
        )
        # The URL handed to Session.post must be the IP literal, not the hostname.
        called_url = mock_post.call_args.args[0] if mock_post.call_args.args else mock_post.call_args.kwargs.get("url")
        assert called_url == "https://8.8.8.8/abc", (
            f"safe_post must rebuild the URL with the resolved public IP literal; got {called_url!r}"
        )

    def test_host_header_preserved_after_ip_pin(self):
        """The original hostname must travel via ``Host`` header so the
        upstream virtual host receives the right request line."""
        from forgelm import _http

        with (
            patch.object(_http.socket, "getaddrinfo", return_value=[(0, 0, 0, "", ("8.8.8.8", 0))]),
            patch.object(_http.requests.Session, "post") as mock_post,
        ):
            mock_post.return_value = MagicMock(ok=True, status_code=200)
            _http.safe_post(
                "https://hooks.example.com/abc",
                json={},
                headers={"Authorization": "Bearer secret"},  # noqa: S105 — test fixture
                timeout=10.0,
            )

        headers = mock_post.call_args.kwargs["headers"]
        assert headers["Host"] == "hooks.example.com"
        # The opt-in auth header must be preserved alongside Host.
        assert headers["Authorization"] == "Bearer secret"

    def test_https_uses_host_header_ssl_adapter(self):
        """For HTTPS the Session must mount HostHeaderSSLAdapter so SNI
        and cert validation are performed against the original hostname,
        not the IP literal in the URL."""
        from forgelm import _http

        with patch.object(_http.socket, "getaddrinfo", return_value=[(0, 0, 0, "", ("8.8.8.8", 0))]):
            session = _http._pinned_session("https")
        # Ensure an https://-mounted adapter exists and is the HostHeaderSSLAdapter.
        adapter = session.get_adapter("https://example.com/")
        from requests_toolbelt.adapters.host_header_ssl import HostHeaderSSLAdapter

        assert isinstance(adapter, HostHeaderSSLAdapter)

    def test_http_session_does_not_mount_ssl_adapter(self):
        """The HTTP branch (operator opt-in via allow_insecure_http) must
        not need the SSL adapter — no SNI involved."""
        from forgelm import _http

        session = _http._pinned_session(
            "http"
        )  # NOSONAR python:S5332 — scheme fixture for the SSL-adapter dispatch branch; no outbound call.
        # Default HTTPAdapter, not HostHeaderSSLAdapter, on the http:// prefix.
        adapter = session.get_adapter(
            "http://example.com/"
        )  # NOSONAR python:S5332 — adapter lookup fixture; no network egress.
        from requests_toolbelt.adapters.host_header_ssl import HostHeaderSSLAdapter

        assert not isinstance(adapter, HostHeaderSSLAdapter)

    def test_allow_private_bypasses_pinning(self):
        """``allow_private=True`` is the documented in-cluster/internal
        destination opt-in; the legacy ``requests.post`` flow runs so
        internal DNS / split-horizon resolution still works."""
        from forgelm import _http

        with (
            patch.object(_http.socket, "getaddrinfo") as resolve_mock,
            patch.object(_http.requests, "post") as mock_post,
            patch.object(_http.requests.Session, "post") as session_post_mock,
        ):
            mock_post.return_value = MagicMock(ok=True, status_code=200)
            _http.safe_post(
                "https://internal.corp.local/hook",
                json={},
                timeout=10.0,
                allow_private=True,
            )

        resolve_mock.assert_not_called()  # No DNS pre-resolve in the opt-in path
        session_post_mock.assert_not_called()  # No Session.post either
        mock_post.assert_called_once()  # Legacy requests.post path


class TestIpv6PinningBuildsBracketedUrl:
    """IPv6 IP literals must be bracketed in the rebuilt URL per RFC 3986."""

    def test_ipv6_url_is_bracketed(self):
        from forgelm import _http

        with (
            patch.object(_http.socket, "getaddrinfo", return_value=[(0, 0, 0, "", ("2606:4700:4700::1111", 0))]),
            patch.object(_http.requests.Session, "post") as mock_post,
        ):
            mock_post.return_value = MagicMock(ok=True, status_code=200)
            _http.safe_post("https://v6.example.com/abc", json={}, timeout=10.0)

        called_url = mock_post.call_args.args[0]
        assert called_url == "https://[2606:4700:4700::1111]/abc"


class TestSafeGetPinning:
    """``safe_get`` mirrors ``safe_post`` for the same hardening contract."""

    def test_safe_get_pins_url_and_sets_host_header(self):
        from forgelm import _http

        with (
            patch.object(_http.socket, "getaddrinfo", return_value=[(0, 0, 0, "", ("8.8.8.8", 0))]),
            patch.object(_http.requests.Session, "request") as mock_request,
        ):
            mock_request.return_value = MagicMock(ok=True, status_code=200)
            _http.safe_get("https://hub.example.com/api/models", timeout=10.0)

        # Session.request("GET", url, ...)
        method = mock_request.call_args.args[0]
        url = mock_request.call_args.args[1]
        headers = mock_request.call_args.kwargs["headers"]

        assert method == "GET"
        assert url == "https://8.8.8.8/api/models"
        assert headers["Host"] == "hub.example.com"

    def test_safe_get_allow_private_bypasses_pinning(self):
        """Mirror of safe_post's allow_private bypass for the read-side
        helper — issue #14 review feedback (sourcery).  ``allow_private=True``
        must keep the legacy ``requests.request`` flow (no Session, no
        IP pinning) so internal DNS / split-horizon resolution still
        resolves cluster-local registries.
        """
        from forgelm import _http

        with (
            patch.object(_http.socket, "getaddrinfo") as resolve_mock,
            patch.object(_http.requests, "request") as legacy_request,
            patch.object(_http.requests.Session, "request") as session_request,
        ):
            legacy_request.return_value = MagicMock(ok=True, status_code=200)
            _http.safe_get(
                "https://internal.registry.local/v1/models",
                timeout=10.0,
                allow_private=True,
            )

        resolve_mock.assert_not_called()
        session_request.assert_not_called()
        legacy_request.assert_called_once()


class TestRfc7230HostHeader:
    """Issue #14 review feedback (gemini): RFC 7230 § 5.4 requires the
    ``Host`` header to match the request-target authority — including
    non-standard ports and bracketed IPv6 literals.  Bare ``hostname``
    drops both, so we use ``netloc`` (with any userinfo stripped)
    instead.
    """

    def test_host_header_preserves_non_standard_port(self):
        from forgelm import _http

        with (
            patch.object(_http.socket, "getaddrinfo", return_value=[(0, 0, 0, "", ("8.8.8.8", 0))]),
            patch.object(_http.requests.Session, "post") as mock_post,
        ):
            mock_post.return_value = MagicMock(ok=True, status_code=200)
            _http.safe_post("https://hooks.example.com:8443/abc", json={}, timeout=10.0)

        headers = mock_post.call_args.kwargs["headers"]
        assert headers["Host"] == "hooks.example.com:8443", (
            f"Non-standard port must remain in the Host header per RFC 7230 § 5.4; got {headers['Host']!r}"
        )

    def test_host_header_brackets_ipv6_literal(self):
        from forgelm import _http

        # Hostname-resolved-to-IPv6 case is exercised by the URL builder
        # test below; here we cover the IPv6-literal-in-URL form which
        # the operator may supply when bypassing DNS entirely.
        with patch.object(_http.requests.Session, "post") as mock_post:
            mock_post.return_value = MagicMock(ok=True, status_code=200)
            _http.safe_post("https://[2606:4700:4700::1111]/abc", json={}, timeout=10.0)

        headers = mock_post.call_args.kwargs["headers"]
        assert headers["Host"] == "[2606:4700:4700::1111]", (
            f"IPv6 literal in Host header must stay bracketed per RFC 7230 § 5.4; got {headers['Host']!r}"
        )

    def test_host_header_strips_userinfo(self):
        """If the URL carries ``user:pass@host`` userinfo, the Host
        header must NOT echo it — that would leak the credential into
        every outbound request, plus RFC 7230 § 5.4 explicitly forbids
        userinfo in ``Host``.
        """
        from forgelm import _http

        with (
            patch.object(_http.socket, "getaddrinfo", return_value=[(0, 0, 0, "", ("8.8.8.8", 0))]),
            patch.object(_http.requests.Session, "post") as mock_post,
        ):
            mock_post.return_value = MagicMock(ok=True, status_code=200)
            _http.safe_post(
                "https://user:pass@hooks.example.com/abc",  # noqa: S105 — userinfo fixture
                json={},
                timeout=10.0,
            )

        headers = mock_post.call_args.kwargs["headers"]
        assert "@" not in headers["Host"]
        assert headers["Host"] == "hooks.example.com"

    def test_explicit_host_header_not_overridden(self):
        """When the caller passes an explicit ``Host`` header (e.g. for
        a reverse proxy / hostname-spoofing test setup), ``safe_post``
        must respect it via ``setdefault`` and not overwrite — issue
        #14 review feedback (sourcery).
        """
        from forgelm import _http

        with (
            patch.object(_http.socket, "getaddrinfo", return_value=[(0, 0, 0, "", ("8.8.8.8", 0))]),
            patch.object(_http.requests.Session, "post") as mock_post,
        ):
            mock_post.return_value = MagicMock(ok=True, status_code=200)
            _http.safe_post(
                "https://hooks.example.com/abc",
                json={},
                headers={"Host": "operator-supplied.example.com"},
                timeout=10.0,
            )

        headers = mock_post.call_args.kwargs["headers"]
        assert headers["Host"] == "operator-supplied.example.com", (
            "Caller's explicit Host header must take precedence over the auto-set value"
        )

    def test_caller_host_header_with_different_casing_does_not_duplicate(self):
        """Mixed-casing caller header (e.g. ``{"host": ...}``) must not
        produce two Host entries on the wire.  Issue #14 review-round-2
        (codex): a plain ``dict(headers or {})`` preserves the caller's
        casing, so ``setdefault("Host", ...)`` would silently add a
        second header alongside the caller's ``"host"`` key — invalid
        per RFC 7230 § 5.4 and a wire-format ambiguity (``requests``
        dedupes via its own ``CaseInsensitiveDict`` but the outbound
        argument we send must already be single-valued so we can
        observe it in this test).
        """
        from forgelm import _http

        with (
            patch.object(_http.socket, "getaddrinfo", return_value=[(0, 0, 0, "", ("8.8.8.8", 0))]),
            patch.object(_http.requests.Session, "post") as mock_post,
        ):
            mock_post.return_value = MagicMock(ok=True, status_code=200)
            _http.safe_post(
                "https://hooks.example.com/abc",
                json={},
                # NB: lowercase ``host``.  A naive ``dict`` would let
                # ``setdefault("Host", auto_value)`` add a second header
                # because dict keys are case-sensitive.
                headers={"host": "operator-supplied.example.com"},
                timeout=10.0,
            )

        headers = mock_post.call_args.kwargs["headers"]
        # Exactly one Host entry — case-insensitively — must exist.
        host_keys = [k for k in headers.keys() if k.lower() == "host"]
        assert len(host_keys) == 1, f"Expected exactly one Host header, got {host_keys}"
        # The caller's explicit value must win.
        assert headers["Host"] == "operator-supplied.example.com"
        assert headers["host"] == "operator-supplied.example.com"


class TestAssertHostnameStripsPort:
    """F-P5-OPUS-05: the pinned HTTPS adapter must hand urllib3 a
    *port-stripped* hostname for certificate ``assert_hostname``.

    The RFC 7230 fix keeps ``host:port`` in the on-the-wire ``Host``
    header (see :class:`TestRfc7230HostHeader`), but urllib3's
    ``_dnsname_match`` does NOT strip the port — it compares the full
    ``host:port`` string against the cert SAN and fails.  These tests
    drive the *real* ``_PortStrippingAdapter.send`` path (not a mocked
    ``Session.post``) and assert the value pushed into
    ``connection_pool_kw['assert_hostname']`` is the bare host.
    """

    @pytest.mark.parametrize(
        ("host_header", "expected"),
        [
            ("hooks.example.com:8443", "hooks.example.com"),
            ("hooks.example.com", "hooks.example.com"),
            ("[2001:db8::1]:8443", "2001:db8::1"),
            ("[2001:db8::1]", "2001:db8::1"),
            ("8.8.8.8:443", "8.8.8.8"),
        ],
    )
    def test_assert_hostname_helper_strips_port(self, host_header, expected):
        from forgelm import _http

        assert _http._assert_hostname_from_host_header(host_header) == expected

    def test_dnsname_match_rejects_port_bearing_assert_hostname(self):
        """Locks the root cause: urllib3's matcher fails on ``host:port``
        but passes on the bare host — so stripping the port is what makes
        TLS succeed for a non-standard-port endpoint.
        """
        from urllib3.util.ssl_match_hostname import _dnsname_match

        assert _dnsname_match("hooks.example.com", "hooks.example.com") is True
        assert _dnsname_match("hooks.example.com", "hooks.example.com:8443") is False

    def test_pinned_adapter_sets_port_stripped_assert_hostname(self):
        """End-to-end through the real adapter: a request to a
        non-standard-port HTTPS URL must leave urllib3's
        ``assert_hostname`` *and* ``server_hostname`` set to the bare
        host, never ``host:port``. ``assert_hostname`` makes urllib3
        match the cert SAN; ``server_hostname`` makes the TLS handshake
        send the bare host as SNI (delegating to ``HTTPAdapter.send``
        skips the parent's SNI derivation, so urllib3 would otherwise SNI
        the port-bearing host / IP literal and fail verification).
        """
        from requests import PreparedRequest
        from requests.adapters import HTTPAdapter

        from forgelm import _http

        session = _http._pinned_session("https")
        adapter = session.get_adapter("https://x")

        req = PreparedRequest()
        # Mimic the pinned URL (IP literal) + RFC 7230 Host header (port kept).
        req.prepare(
            method="POST",
            url="https://8.8.8.8:8443/abc",
            headers={"Host": "hooks.example.com:8443"},
        )

        captured = {}

        def _fake_http_send(self, request, **kwargs):
            kw = self.poolmanager.connection_pool_kw
            captured["assert_hostname"] = kw.get("assert_hostname")
            captured["server_hostname"] = kw.get("server_hostname")
            return MagicMock(status_code=200)

        with patch.object(HTTPAdapter, "send", _fake_http_send):
            adapter.send(req)

        assert captured["assert_hostname"] == "hooks.example.com", (
            "assert_hostname must be the port-stripped host so urllib3 matches the cert SAN; "
            f"got {captured['assert_hostname']!r}"
        )
        assert captured["server_hostname"] == "hooks.example.com", (
            "server_hostname must be the port-stripped host so the TLS handshake sends the "
            f"bare host as SNI; got {captured['server_hostname']!r}"
        )

    def test_pinned_adapter_clears_hostnames_without_host_header(self):
        """A request with no Host header must leave neither
        ``assert_hostname`` nor ``server_hostname`` lingering in the pool
        kwargs — a stale value from a prior request would otherwise SNI /
        verify the wrong host on the reused session.
        """
        from requests import PreparedRequest
        from requests.adapters import HTTPAdapter

        from forgelm import _http

        session = _http._pinned_session("https")
        adapter = session.get_adapter("https://x")
        # Seed stale values as if a previous request had set them.
        adapter.poolmanager.connection_pool_kw["assert_hostname"] = "stale.example.com"
        adapter.poolmanager.connection_pool_kw["server_hostname"] = "stale.example.com"

        req = PreparedRequest()
        req.prepare(method="GET", url="https://8.8.8.8/abc", headers={})
        # Drop the auto-added Host header so the cleanup branch runs.
        for header in list(req.headers):
            if header.lower() == "host":
                del req.headers[header]

        captured = {}

        def _fake_http_send(self, request, **kwargs):
            kw = self.poolmanager.connection_pool_kw
            captured["assert_hostname"] = kw.get("assert_hostname")
            captured["server_hostname"] = kw.get("server_hostname")
            return MagicMock(status_code=200)

        with patch.object(HTTPAdapter, "send", _fake_http_send):
            adapter.send(req)

        assert captured["assert_hostname"] is None
        assert captured["server_hostname"] is None


class TestNoPublicIpResolvedBranch:
    """Issue #14 review feedback (sourcery): cover the
    ``"no public IP resolved"`` branch where ``getaddrinfo`` returns
    only entries the resolver cannot parse as plain IP literals
    (e.g. zone-id-suffixed IPv6 link-locals).
    """

    def test_only_unparseable_records_yields_no_public_ip(self):
        from forgelm import _http

        # All entries fail ``ipaddress.ip_address()`` — malformed IP
        # strings of both v4 and v6 shape.  None of them is private
        # (we never parse them) but none is public either, so the
        # resolver must report "no public IP resolved" rather than
        # silently letting an empty candidate set through.
        with patch.object(
            _http.socket,
            "getaddrinfo",
            return_value=[
                (0, 0, 0, "", ("not-an-ip", 0)),
                (0, 0, 0, "", ("999.999.999.999", 0)),
            ],
        ):
            ip, err = _http._resolve_safe_destination("weird.example.com")

        assert ip is None
        assert err == "no public IP resolved"


class TestTransportErrorUrlRedaction:
    """F-P5-OPUS-01 regression: the transport-failure WARNING log must not
    leak the secret-bearing URL path that ``requests``/``urllib3`` embed in
    their exception strings.  Slack/Teams/Discord/custom webhook URLs carry
    the bearer token in the path, so ``reason=`` (which is built from
    ``str(exc)``) must have any path/query stripped before logging.
    """

    # NOSONAR test fixture — fragment-built so secret scanners don't flag it.
    _SECRET = "SUPER" + "SECRET" + "WEBHOOKTOKEN"  # noqa: S105

    def _slack_connection_error(self):
        import requests as req

        # Mirrors the real urllib3 ConnectionError message shape: the host
        # is reported (already masked elsewhere) but the ``url:`` path —
        # which carries the token — is embedded verbatim.
        return req.exceptions.ConnectionError(
            f"HTTPSConnectionPool(host='8.8.8.8', port=443): Max retries "
            f"exceeded with url: /services/T00000/B00000/{self._SECRET} "
            f"(Caused by NewConnectionError(...))"
        )

    def test_safe_post_transport_error_does_not_leak_url_path(self, caplog):
        import logging

        import requests as req

        from forgelm import _http

        url = f"https://hooks.slack.com/services/T00000/B00000/{self._SECRET}"
        with (
            patch.object(_http.socket, "getaddrinfo", return_value=[(0, 0, 0, "", ("8.8.8.8", 0))]),
            patch.object(_http.requests.Session, "post", side_effect=self._slack_connection_error()),
        ):
            with caplog.at_level(logging.WARNING, logger="forgelm._http"):
                with pytest.raises(req.exceptions.ConnectionError):
                    _http.safe_post(url, json={}, timeout=10.0)

        log_text = " ".join(r.message for r in caplog.records)
        assert self._SECRET not in log_text
        assert "[REDACTED-PATH]" in log_text

    def test_safe_get_transport_error_does_not_leak_url_path(self, caplog):
        import logging

        import requests as req

        from forgelm import _http

        url = f"https://hooks.slack.com/services/T00000/B00000/{self._SECRET}"
        with (
            patch.object(_http.socket, "getaddrinfo", return_value=[(0, 0, 0, "", ("8.8.8.8", 0))]),
            patch.object(_http.requests.Session, "request", side_effect=self._slack_connection_error()),
        ):
            with caplog.at_level(logging.WARNING, logger="forgelm._http"):
                with pytest.raises(req.exceptions.ConnectionError):
                    _http.safe_get(url, timeout=10.0)

        log_text = " ".join(r.message for r in caplog.records)
        assert self._SECRET not in log_text
        assert "[REDACTED-PATH]" in log_text

    def test_safe_get_head_transport_error_does_not_leak_url_path(self, caplog):
        import logging

        import requests as req

        from forgelm import _http

        url = f"https://hooks.slack.com/services/T00000/B00000/{self._SECRET}"
        with (
            patch.object(_http.socket, "getaddrinfo", return_value=[(0, 0, 0, "", ("8.8.8.8", 0))]),
            patch.object(_http.requests.Session, "request", side_effect=self._slack_connection_error()),
        ):
            with caplog.at_level(logging.WARNING, logger="forgelm._http"):
                with pytest.raises(req.exceptions.ConnectionError):
                    _http.safe_get(url, timeout=10.0, method="HEAD")

        log_text = " ".join(r.message for r in caplog.records)
        assert self._SECRET not in log_text
        assert "[REDACTED-PATH]" in log_text

    def test_redactor_strips_full_url_when_embedded_verbatim(self):
        from forgelm import _http

        url = f"https://example.com/{self._SECRET}?sig=abc"
        text = f"connection refused to {url} retrying"
        masked = _http._redact_url_paths_in_text(text, url)
        assert self._SECRET not in masked
        # The host signal is preserved so operators still see the destination.
        assert "example.com" in masked

    def test_redactor_leaves_scheme_host_only_url_intact(self):
        from forgelm import _http

        # No path/query → nothing secret to strip → unchanged.
        text = "failed talking to https://hooks.slack.com (timeout)"
        masked = _http._redact_url_paths_in_text(text, "https://hooks.slack.com")
        assert "https://hooks.slack.com" in masked


class TestShortPathTailNoOverRedaction:
    """F-M-08 regression: Pass 1 must NOT replace short path tokens like
    ``/v1`` or ``/api`` (len < 8) because they are common in non-secret
    contexts — an unbounded replace would erase diagnostic signal from
    unrelated URLs in the same exception string.  Pass 2 (regex on
    ``url: <path>``) still catches the urllib3 form correctly.
    """

    def test_short_path_tail_not_over_redacted_in_unrelated_context(self):
        from forgelm import _http

        url = "https://api.example.com/v1"
        # Exception string that mentions /v1 in an unrelated context.
        text = "ConnectionError: failed at /v1 via /v1/api/endpoint"
        masked = _http._redact_url_paths_in_text(text, url)
        # Pass 1 must NOT have replaced /v1 everywhere (short token guard).
        # The text should retain diagnostic context — not turn both /v1 occurrences
        # into [REDACTED-PATH].
        assert masked.count("[REDACTED-PATH]") == 0 or "/v1/api/endpoint" in masked or "failed at /v1" in masked

    def test_short_path_tail_still_caught_by_pass2_url_form(self):
        """When urllib3 emits ``url: /v1`` in its error string, Pass 2 must
        still redact it even though Pass 1 now skips short tails."""
        from forgelm import _http

        url = "https://api.example.com/v1"
        text = "Max retries exceeded with url: /v1 (Caused by NewConnectionError)"
        masked = _http._redact_url_paths_in_text(text, url)
        assert "[REDACTED-PATH]" in masked
        assert "/v1" not in masked.replace("[REDACTED-PATH]", "")

    def test_long_path_tail_still_redacted_by_pass1(self):
        """Path tails >= 8 chars (real secrets) must still be removed.

        The full URL replacement (Pass 1, step 1) consumes the whole URL,
        leaving only the host-masked form.  Either way, the secret must not
        appear in the output.
        """
        from forgelm import _http

        url = "https://hooks.slack.com/services/T00000/SECRETTOKEN"
        # Embed the path tail without the full URL to exercise the path-tail
        # replacement branch specifically (Pass 1, step 2).
        path_tail = "/services/T00000/SECRETTOKEN"
        text = f"connection refused, path was {path_tail}"
        masked = _http._redact_url_paths_in_text(text, url)
        assert "SECRETTOKEN" not in masked
        assert "[REDACTED-PATH]" in masked


class TestQueryOnlyPathRedaction:
    """F-N-04 regression: Pass 2 regex must now match query-only tokens
    (``url: ?api_key=SECRET``) and fragment-only tokens, not only paths
    that start with ``/``.
    """

    def test_query_only_url_token_redacted(self):
        from forgelm import _http

        text = "Max retries exceeded with url: ?api_key=SUPERSECRET (Caused by)"
        masked = _http._redact_url_paths_in_text(text, "https://api.example.com?api_key=SUPERSECRET")
        assert "SUPERSECRET" not in masked
        assert "[REDACTED-PATH]" in masked

    def test_fragment_only_url_token_redacted(self):
        from forgelm import _http

        # Fragment in url: token — now matched by the extended [/?#] prefix.
        text = "Max retries exceeded with url: #section=SECRET (Caused by)"
        masked = _http._redact_url_paths_in_text(text, "https://api.example.com#section=SECRET")
        assert "SECRET" not in masked
        assert "[REDACTED-PATH]" in masked

    def test_slash_prefix_still_works(self):
        """Existing slash-prefixed pass-2 behaviour must be unaffected."""
        from forgelm import _http

        text = "Max retries exceeded with url: /services/TOKEN (Caused by)"
        masked = _http._redact_url_paths_in_text(text, "https://hooks.slack.com/services/TOKEN")
        assert "TOKEN" not in masked
        assert "[REDACTED-PATH]" in masked


class TestPortStrippingAdapterModuleScope:
    """F-N-05 regression: ``_PortStrippingSSLAdapter`` must be a single
    class object defined at module scope so that cross-session isinstance
    checks are stable and no per-call class creation overhead occurs.
    """

    def test_adapter_class_is_same_object_across_sessions(self):
        from forgelm import _http

        with (
            patch.object(_http.socket, "getaddrinfo", return_value=[(0, 0, 0, "", ("8.8.8.8", 0))]),
        ):
            s1 = _http._pinned_session("https")
            s2 = _http._pinned_session("https")

        a1 = s1.get_adapter("https://x")
        a2 = s2.get_adapter("https://x")
        # Both adapters must be instances of the SAME class (module-level definition).
        assert type(a1) is type(a2), (
            f"Adapters from different sessions must share the same class; "
            f"got id(type(a1))={id(type(a1))} vs id(type(a2))={id(type(a2))}"
        )

    def test_adapter_is_instance_of_module_level_class(self):
        """Explicit isinstance check against the module-level name must work."""
        from forgelm import _http

        session = _http._pinned_session("https")
        adapter = session.get_adapter("https://x")
        assert _http._PortStrippingSSLAdapter is not None
        assert isinstance(adapter, _http._PortStrippingSSLAdapter)

    def test_cross_session_isinstance_check(self):
        """isinstance(adapter_from_s1, type(adapter_from_s2)) must return True."""
        from forgelm import _http

        s1 = _http._pinned_session("https")
        s2 = _http._pinned_session("https")
        a1 = s1.get_adapter("https://x")
        a2 = s2.get_adapter("https://x")
        assert isinstance(a1, type(a2))
        assert isinstance(a2, type(a1))
