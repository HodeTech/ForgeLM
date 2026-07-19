"""Centralized HTTP discipline for outbound HTTP calls (GET / HEAD / POST).

Extracted from ``webhook._post_payload`` to enforce SSRF guard, redirect
refusal, ``http://`` refusal, timeout floor, and secret-mask error reasons
across every outbound HTTP call site in the codebase (webhook, judge,
synthetic, doctor).  POST traffic goes through :func:`safe_post`; read-side
GET / HEAD traffic goes through :func:`safe_get` (added in Wave 2a Round-2
when ``forgelm doctor`` migrated off ``urllib.request.urlopen``).

Future call sites (telemetry, registry pings, etc.) MUST go through one of
those helpers rather than calling ``requests.{post,get,head}`` (or
``urllib.request.urlopen`` / ``httpx.*``) directly — the CI acceptance
gate ``lint-http-discipline`` greps ``forgelm/`` for those patterns and
fails on any hit outside ``forgelm/_http.py``:

    grep -rn "requests\\.\\(post\\|get\\|head\\)\\(" forgelm/ | grep -v _http\\.py
    grep -rn "urllib\\.request\\.urlopen\\(" forgelm/ | grep -v _http\\.py

both must stay empty.

Policy summary (each enforced before the network call):

* **Scheme** — ``https://`` required by default; ``http://`` rejected unless
  the caller passes ``allow_insecure_http=True`` (only the operator-blessed
  webhook path uses this; judge / synthetic always require TLS).
* **SSRF** — RFC1918, loopback, link-local (incl. cloud IMDS at
  ``169.254.169.254``), RFC 6598 Shared Address Space / Carrier-Grade-NAT
  (``100.64.0.0/10`` — incl. Alibaba Cloud ECS IMDS at ``100.100.100.200``,
  which the stdlib ``ipaddress`` predicates do *not* flag as private or
  reserved), reserved, and multicast destinations are blocked unless
  ``allow_private=True``. Hostnames are pre-resolved via
  ``socket.getaddrinfo`` so a DNS name pointing at a private IP also trips.
* **Timeout floor** — defaults to 10s; callers can pass ``min_timeout`` to
  lower the floor (the webhook path uses 1s to preserve historical
  behaviour). ``timeout=0`` / ``None`` is always rejected — ``requests``
  honours those as "block forever".
* **Redirects** — ``allow_redirects=False`` always. The SSRF guard runs
  against the *initial* hostname; following a 30x to a private IP would
  bypass it.
* **TLS** — ``verify=True`` by default; pass ``ca_bundle="/path/..."`` for a
  custom CA store (corporate MITM CA on regulated estates).
* **Header masking** — ``Authorization`` / ``X-API-Key`` values are redacted
  from the warning log emitted when the request raises.
"""

from __future__ import annotations

import ipaddress
import logging
import math
import re
import socket
from typing import Any, Dict, MutableMapping, Optional, Tuple
from urllib.parse import urlparse, urlunparse

import requests
from requests.structures import CaseInsensitiveDict

logger = logging.getLogger("forgelm._http")

# Optional dependency — ``requests_toolbelt`` is needed only for the IP-pinning
# HTTPS path.  Importing once at module level (instead of inside
# ``_pinned_session`` on every call) avoids creating a new class object per
# invocation and makes cross-session ``isinstance`` checks stable.
try:
    from requests.adapters import HTTPAdapter as _HTTPAdapter
    from requests_toolbelt.adapters.host_header_ssl import HostHeaderSSLAdapter as _HostHeaderSSLAdapter

    class _PortStrippingSSLAdapter(_HostHeaderSSLAdapter):
        """``HostHeaderSSLAdapter`` subclass that strips the port (and IPv6
        brackets) from the ``Host`` header before handing the hostname to
        urllib3's ``assert_hostname`` / ``server_hostname`` pool kwargs.

        urllib3's ``_dnsname_match`` does **not** strip the port, so passing
        ``host:port`` verbatim would cause TLS certificate validation to fail
        for non-standard-port HTTPS endpoints.  The wire ``Host`` header (with
        the port) is left intact so the server receives the correct authority.
        """

        def send(self, request, **kwargs):  # type: ignore[override]
            host_header = None
            for header in request.headers:
                if header.lower() == "host":
                    host_header = request.headers[header]
                    break
            if host_header:
                # Set assert_hostname AND server_hostname to the port-stripped
                # host: assert_hostname makes urllib3 match the cert SAN,
                # server_hostname makes the TLS handshake send the bare host as
                # SNI (delegating to HTTPAdapter.send below skips the parent's
                # own SNI derivation, so urllib3 would otherwise SNI the
                # port-bearing host / IP literal and fail verification on
                # endpoints like https://host:8443).  The wire Host header
                # (with the port) is left intact for the server.
                bare_host = _assert_hostname_from_host_header(host_header)
                self.poolmanager.connection_pool_kw["assert_hostname"] = bare_host
                self.poolmanager.connection_pool_kw["server_hostname"] = bare_host
            else:
                self.poolmanager.connection_pool_kw.pop("assert_hostname", None)
                self.poolmanager.connection_pool_kw.pop("server_hostname", None)
            # Bypass the parent's own (port-bearing) assert_hostname derivation
            # by delegating straight to HTTPAdapter.send.
            return _HTTPAdapter.send(self, request, **kwargs)

except ImportError:  # pragma: no cover — requests_toolbelt installed in CI
    _PortStrippingSSLAdapter = None  # type: ignore[assignment,misc]


# Exported surface of the SSRF-guarded HTTP chokepoint. ``safe_post`` /
# ``safe_get`` / ``HttpSafetyError`` are the primary API. ``_mask_netloc`` and
# ``_mask_secrets_in_text`` keep their leading underscore (they are low-level
# redaction helpers, not a stable contract) but are listed here as INTENTIONAL
# cross-module exports: ``webhook.py`` and ``cli/subcommands/_purge.py`` reuse
# them so log/audit redaction stays consolidated on this single chokepoint
# (F-L-07 / F-L-21). A rename must update those call sites — this list is the
# contract that makes that dependency explicit.
__all__ = [
    "HttpSafetyError",
    "safe_post",
    "safe_get",
    "_mask_netloc",
    "_mask_secrets_in_text",
]


class HttpSafetyError(Exception):
    """Policy-level rejection of an outbound HTTP request.

    Distinct from :class:`requests.RequestException` so callers can tell a
    refused-by-policy URL (operator misconfiguration — surface to the user)
    apart from a transport failure (network blip — log + continue).
    """


_MASK_HEADER_NAMES = frozenset({"authorization", "x-api-key", "proxy-authorization"})

# RFC 6598 Shared Address Space (Carrier-Grade-NAT).  The stdlib
# ``ipaddress`` predicates do NOT classify this /10 as private, reserved,
# link-local, or multicast, yet it is where cloud providers park internal
# metadata endpoints — Alibaba Cloud ECS's IMDS listens at 100.100.100.200,
# squarely inside this block — so it MUST be blocked explicitly alongside
# the ranges the stdlib predicates already cover.
_CGNAT_SHARED_ADDRESS_SPACE = ipaddress.ip_network("100.64.0.0/10")


def _is_cgnat_shared_address(ip: ipaddress._BaseAddress) -> bool:
    """Return ``True`` when *ip* falls in RFC 6598 Shared Address Space.

    Handles the IPv4-mapped IPv6 form (``::ffff:100.100.100.200``) by
    testing the embedded IPv4 address.  ``in`` against a
    differently-versioned (pure-IPv6) address safely returns ``False``.

    In the real request path (:func:`_is_blocked_ip`, which ``or``-chains
    ``ip.is_private`` before this helper), an IPv4-mapped IPv6 CGNAT literal
    is already caught upstream — the stdlib's private-network classification
    covers the entire ``::ffff:0:0/96`` mapped range unconditionally, so
    ``ip.is_private`` short-circuits before this function is ever reached
    for a mapped literal. The ``ipv4_mapped`` handling here is
    defense-in-depth: it protects direct callers of this helper (as the
    unit tests exercise) and keeps the result correct if
    ``_is_blocked_ip``'s ``or``-chain order is ever changed — it is not
    the primary mechanism preventing the bypass in the current request
    path.
    """
    candidate = getattr(ip, "ipv4_mapped", None) or ip
    return candidate in _CGNAT_SHARED_ADDRESS_SPACE


def _is_blocked_ip(ip: ipaddress._BaseAddress) -> bool:
    """Single source of truth for the SSRF private-range policy.

    Encapsulates the set of address kinds that ForgeLM's SSRF guard
    refuses to send outbound payloads to: RFC1918 private space, the
    loopback range, link-local (incl. cloud IMDS at 169.254.169.254),
    RFC 6598 Shared Address Space / CGNAT (100.64.0.0/10, incl. Alibaba
    Cloud ECS IMDS at 100.100.100.200), the IETF reserved buckets, and
    multicast.  Used by both :func:`_is_private_destination` (legacy
    yes/no predicate) and :func:`_resolve_safe_destination`
    (DNS-rebinding-safe resolver) so the policy cannot drift between the
    two call sites.
    """
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or _is_cgnat_shared_address(ip)
    )


def _is_private_destination(host: str) -> bool:
    """Return ``True`` when *host* resolves to a non-public-internet IP.

    DNS pre-resolution catches hostnames that happen to point at RFC1918 /
    link-local / loopback addresses, so a ``http://internal.corp/`` URL is
    rejected even when no IP literal is present in the URL itself.

    Re-exported from :mod:`forgelm.webhook` (where it originated) for
    backwards compatibility — existing tests / external consumers continue
    to import the symbol from the webhook module.
    """
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        return _is_blocked_ip(ip)
    try:
        addrinfo = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError):
        # DNS failure → not classified as private; let `requests` produce
        # its natural ConnectionError downstream so the operator sees the
        # real "could not resolve host" message instead of an SSRF-shaped
        # refusal that hides the typo.
        return False
    for _family, _type, _proto, _canon, sockaddr in addrinfo:
        try:
            resolved = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            continue
        if _is_blocked_ip(resolved):
            return True
    return False


# ---------------------------------------------------------------------------
# Issue #14 — DNS-rebinding-safe destination resolver
# ---------------------------------------------------------------------------
#
# ``_is_private_destination`` above pre-resolves the host and answers a
# yes/no question, but the actual ``requests.post`` call then does its OWN
# DNS lookup at connect time.  Between those two lookups an attacker-
# controlled DNS server can flip the answer (TTL=0 + rebinding to
# 127.0.0.1 / 169.254.169.254), letting the payload + bearer token leak
# to a private destination after passing the guard.  The resolver below
# closes that window by returning a concrete public IP literal that the
# call site uses to build the URL — requests then connects to that IP
# without a second DNS round-trip.


def _resolve_safe_destination(host: str) -> Tuple[Optional[str], Optional[str]]:
    """Resolve *host* to a single public IP literal.

    Returns ``(ip, None)`` when the host is a public destination, or
    ``(None, reason)`` when the destination is blocked (private/loopback/
    IMDS/multicast IP, empty host, or DNS resolution failure).

    The caller substitutes the returned IP literal into the request URL
    so ``requests`` does not re-resolve the hostname — this is the fix
    for the DNS-rebinding TOCTOU described in issue #14.  The original
    hostname must be propagated through the ``Host`` header (and SNI for
    HTTPS) by the caller; this helper only owns the resolve+validate
    step.

    The picked IP is deterministic (first public address returned by
    ``getaddrinfo``) so the test harness can mock the resolver without
    flakiness.  When the host is already an IP literal, it is returned
    as-is after the same private-range check.
    """
    if not host:
        return None, "empty host"

    # IP literal short-circuit — no DNS needed, same private-range filter
    # applied via the shared ``_is_blocked_ip`` helper so the IP-literal
    # path and the DNS path cannot disagree.
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if _is_blocked_ip(literal):
            return None, "Private/loopback/IMDS destination"
        return host, None

    # Hostname path — one resolve, validate every returned address.  A
    # mixed result (some public, some private) is treated as blocked
    # because a follow-up connect could land on the private one.
    try:
        addrinfo = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError) as e:
        return None, f"DNS resolution failed: {e}"

    public_ip: Optional[str] = None
    for _family, _type, _proto, _canon, sockaddr in addrinfo:
        candidate = sockaddr[0]
        try:
            resolved = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if _is_blocked_ip(resolved):
            return None, "Private/loopback/IMDS destination"
        if public_ip is None:
            public_ip = candidate
    if public_ip is None:
        return None, "no public IP resolved"
    return public_ip, None


def _build_pinned_url(parsed_url, ip: str) -> str:
    """Rebuild *parsed_url* with the hostname replaced by *ip*.

    Preserves scheme, port, path, query, and fragment.  IPv6 literals
    are bracketed per RFC 3986 (``https://[2001:db8::1]:443/path``).
    Userinfo is intentionally dropped — webhook URLs may carry a token
    in the path but never in userinfo, and stripping it removes one
    avenue for accidental credential exposure in logs.
    """
    try:
        is_ipv6 = ipaddress.ip_address(ip).version == 6
    except ValueError:
        is_ipv6 = False
    netloc = f"[{ip}]" if is_ipv6 else ip
    if parsed_url.port:
        netloc = f"{netloc}:{parsed_url.port}"
    return urlunparse(
        (
            parsed_url.scheme,
            netloc,
            parsed_url.path,
            parsed_url.params,
            parsed_url.query,
            parsed_url.fragment,
        )
    )


def _assert_hostname_from_host_header(host_header: str) -> str:
    """Strip the port (and IPv6 brackets) from a ``Host`` header value.

    The request-line ``Host`` header carries ``host:port`` for a
    non-standard port per RFC 7230 § 5.4, but urllib3's certificate
    ``assert_hostname`` matcher (``_dnsname_match``) does **not** strip
    the port — it compares the full ``host:port`` string against the
    cert SAN and fails (``_dnsname_match('h.example.com',
    'h.example.com:8443') is False``).  So the value handed to the TLS
    layer must be the bare hostname, with the port removed and any IPv6
    brackets unwrapped (urllib3 matches against the bracket-less form).

    Introduced when the issue-#14 IP-pinning routed HTTPS through
    ``HostHeaderSSLAdapter``, which reuses the ``Host`` header verbatim as
    ``assert_hostname`` — breaking TLS for any correctly configured HTTPS
    endpoint on a non-standard port.
    """
    # ``urlparse`` needs a scheme + ``//`` to populate ``hostname``/``port``;
    # synthesise an authority-only URL so it splits ``host:port`` and IPv6
    # brackets for us rather than re-implementing the bracket/port grammar.
    parsed = urlparse(f"//{host_header}")
    # ``hostname`` is already lower-cased and bracket-stripped by urlparse;
    # fall back to the raw value if parsing yields nothing (malformed input
    # should still produce a deterministic, non-empty assert value).
    return parsed.hostname or host_header


def _pinned_session(scheme: str) -> requests.Session:
    """Return a ``requests.Session`` configured for IP-literal connections.

    For HTTPS, mounts a port-stripping subclass of
    ``requests_toolbelt.adapters.host_header_ssl.HostHeaderSSLAdapter``
    so the SNI handshake and certificate validation are performed
    against the original hostname (passed in the ``Host`` header) rather
    than the IP literal in the URL — and against the *bare* hostname,
    with any non-standard port stripped, so TLS verification succeeds on
    endpoints like ``https://host:8443``.
    """
    session = requests.Session()
    if scheme == "https":
        if _PortStrippingSSLAdapter is None:  # pragma: no cover
            raise ImportError(
                "requests-toolbelt is required for HTTPS IP-pinning; install it with: pip install 'forgelm[webhook]'"
            )
        session.mount("https://", _PortStrippingSSLAdapter())
    return session


def _mask_netloc(url: str) -> str:
    """Return ``scheme://host`` with userinfo / path / query stripped.

    Used in policy-rejection error messages and warning logs so we never
    echo the secret-bearing tail of a Slack / Teams / Discord webhook URL
    (which carries credentials in the path) into operator-visible output.
    """
    try:
        parts = urlparse(url)
    except (ValueError, TypeError):
        return "<unparseable-url>"
    if not parts.scheme or not parts.netloc:
        return "<malformed-url>"
    return f"{parts.scheme}://{parts.hostname or 'unknown-host'}"


def _mask_secrets_in_text(text: str, headers: Optional[MutableMapping[str, str]]) -> str:
    """Redact known secret-bearing header values from *text*.

    ``requests`` exception strings sometimes include the request URL or
    header dump; we strip ``Authorization`` / ``X-API-Key`` / proxy auth
    values before logging so a transport-layer error doesn't leak the
    bearer token into the trainer's stderr.
    """
    if not text or not headers:
        return text
    masked = text
    for name, value in headers.items():
        if not value or not isinstance(value, str):
            continue
        if name.lower() in _MASK_HEADER_NAMES:
            masked = masked.replace(value, "[REDACTED]")
    return masked


# ``requests``/``urllib3`` transport-error strings embed the request URL as a
# bare, scheme-less path token, e.g.
#   "HTTPSConnectionPool(host='8.8.8.8', port=443): Max retries exceeded with
#    url: /services/T0/B0/SECRETTOKEN (Caused by ...)"
# Slack / Teams / Discord / custom webhook URLs carry their bearer token in
# that path, so the path must be stripped before the exception string is
# logged.  The IP-pinning already host-masks the ``host=`` field, but the
# ``url:`` path leaks the secret.  Matched class is ``[^\s)]`` (stops at the
# first whitespace or the closing paren ``requests`` appends) and the
# quantifier is bounded to a generous-but-finite 4096 chars — no two
# competing unbounded quantifiers, so this is ReDoS-safe per regex.md §3/§4.
_URL_PATH_TOKEN_RE = re.compile(r"(url:\s*)([/?#][^\s)]{0,4096})")


def _redact_url_paths_in_text(text: str, url: str) -> str:
    """Strip secret-bearing URL paths/query/userinfo from a transport error.

    Two redaction passes, both belt-and-suspenders so a leak survives only
    if *both* miss:

    1. **Exact known-URL pass** — replace the path/query/userinfo of the
       *actual* request URL (the one ``safe_post`` / ``safe_get`` was called
       with) wherever it appears verbatim in *text*, collapsing it to the
       host-masked form from :func:`_mask_netloc`.  This is regex-free and
       cannot over- or under-match.
    2. **Generic ``url:`` token pass** — ``requests`` reports the URL as a
       scheme-less path after ``url:``; strip any such path token to
       ``url: [REDACTED-PATH]``.  Catches the dominant ``Max retries
       exceeded with url: /services/.../TOKEN`` shape even when the IP-pinned
       target URL (not the original) is what landed in the exception string.
    """
    if not text:
        return text
    masked = text
    # Pass 1 — exact known-URL substring (path-bearing forms only; a bare
    # ``scheme://host`` carries no secret and is left intact for signal).
    try:
        parts = urlparse(url)
    except (ValueError, TypeError):
        parts = None
    if parts is not None and parts.scheme and parts.netloc:
        host_only = f"{parts.scheme}://{parts.hostname or 'unknown-host'}"
        # Replace the full URL (path + query + fragment + userinfo) first so
        # the longest, most-specific form is collapsed before the shorter
        # path-only token pass runs.
        if parts.path or parts.query or parts.params or parts.fragment or "@" in parts.netloc:
            masked = masked.replace(url, host_only)
            # Also the scheme-less path tail on its own (urllib3 strips the
            # authority and logs only the path component).
            path_tail = urlunparse(("", "", parts.path, parts.params, parts.query, parts.fragment))
            # Skip replacement for very short path tails (< 8 chars) such as
            # ``/v1``, ``/api``, ``/t`` — low secret entropy and extremely
            # common in non-secret contexts.  Pass 2 (the generic ``url:``
            # token regex) already catches the urllib3 ``url: /path`` form
            # that matters most, so this skip loses nothing for real secrets.
            if path_tail and path_tail != "/" and len(path_tail) >= 8:
                masked = masked.replace(path_tail, "[REDACTED-PATH]")
    # Pass 2 — generic ``url: <path>`` token (covers the IP-pinned target
    # URL and any path the exact-match pass did not catch).
    masked = _URL_PATH_TOKEN_RE.sub(r"\1[REDACTED-PATH]", masked)
    return masked


def safe_post(
    url: str,
    *,
    json: Any = None,
    data: Any = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 10.0,
    verify: Any = True,
    ca_bundle: Optional[str] = None,
    allow_insecure_http: bool = False,
    allow_private: bool = False,
    min_timeout: float = 10.0,
) -> requests.Response:
    """Disciplined outbound POST; raises early on policy violation.

    Args:
        url: Target URL. Must be ``http://`` or ``https://``.
        json: Body serialized as JSON by ``requests``. Mutually exclusive
            with ``data``; passing both is a caller bug, not enforced here.
        data: Pre-serialized body (e.g. ``json.dumps(payload)`` for the
            webhook path that already encodes its own payload).
        headers: Outbound headers. ``Authorization`` / ``X-API-Key`` values
            are masked in the failure log.
        timeout: Per-request timeout in seconds. Must be ``>= min_timeout``.
        verify: Forwarded as ``requests``'s ``verify=`` argument; passing
            ``False`` is allowed but discouraged. ``ca_bundle`` overrides.
        ca_bundle: Path to a custom CA bundle. When non-empty, takes
            precedence over ``verify``.
        allow_insecure_http: Set ``True`` only for paths where the operator
            has explicitly opted into ``http://`` (currently: webhook with a
            documented warning at the call site). Judge and synthetic must
            never set this — they handle bearer tokens.
        allow_private: Set ``True`` to bypass the SSRF guard. Required for
            in-cluster Slack proxies / on-prem monitoring sinks.
        min_timeout: Lower bound for ``timeout``. Defaults to ``10.0``;
            ``webhook._post_payload`` passes ``1.0`` to keep its historical
            behaviour without forcing every webhook user to bump their
            timeout setting.

    Returns:
        The :class:`requests.Response` from the underlying call. The caller
        is responsible for inspecting ``response.ok`` / ``status_code``.

    Raises:
        HttpSafetyError: On policy violation — ``http://`` without opt-in,
            unsupported scheme, sub-floor timeout, or private destination
            without opt-in.
        requests.RequestException: On transport / TLS / network failure.
            Headers are masked in the warning log before the re-raise.
    """
    parsed = urlparse(url)

    # Scheme policy.
    if parsed.scheme == "http":
        if not allow_insecure_http:
            # Rejection message names the blocked scheme so operators see
            # why the call failed; this guard rejects http://, not uses it.
            raise HttpSafetyError(
                f"http:// blocked (use https://); url={_mask_netloc(url)}"  # NOSONAR python:S5332
            )
    elif parsed.scheme != "https":
        raise HttpSafetyError(f"Unsupported URL scheme {parsed.scheme!r}; only http(s) allowed.")

    # Timeout floor — `requests` treats 0 / None as "no timeout" which can
    # hang the trainer on a dead endpoint.  Validated BEFORE the SSRF
    # resolve so a local policy error (timeout=0, sub-floor value) is
    # rejected without a DNS round-trip; this also keeps unit tests
    # that exercise the timeout-floor branch hermetic.  ``math.isfinite``
    # rejects NaN and ±Infinity: NaN defeats every ``<`` comparison (all
    # False) and Infinity reproduces the same "block forever" hang as
    # timeout=0, so both must be refused alongside the lower-bound check.
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout < min_timeout:
        raise HttpSafetyError(f"Timeout below {min_timeout}s floor: timeout={timeout!r}")

    # SSRF guard — issue #14: resolve once to a public IP literal so the
    # connect-time DNS lookup cannot flip the verdict (DNS rebinding /
    # TOCTOU).  When ``allow_private=True`` the operator has explicitly
    # opted into an internal/in-cluster destination; the legacy URL is
    # used as-is so internal DNS / split-horizon resolution still works.
    host = parsed.hostname or ""
    target_url = url
    # ``CaseInsensitiveDict`` so a caller-supplied ``host`` / ``HOST`` /
    # ``Host`` all collapse to the same key — a plain ``dict`` would
    # preserve the caller's casing and let ``setdefault("Host", ...)``
    # silently add a *second* Host header to the request, producing a
    # duplicate on the wire (RFC 7230 § 5.4 forbids this).
    request_headers: MutableMapping[str, str] = CaseInsensitiveDict(headers or {})
    if not allow_private:
        pinned_ip, block_reason = _resolve_safe_destination(host)
        if block_reason:
            raise HttpSafetyError(f"{block_reason} blocked: host={host or '<empty>'}")
        target_url = _build_pinned_url(parsed, pinned_ip)
        # ``Host`` is case-insensitive in HTTP/1.1; let an explicit
        # caller override (any casing) win over the auto-derived value.
        # Use ``netloc`` (with any userinfo stripped) rather than bare
        # ``hostname`` so non-standard ports stay attached and IPv6
        # literals remain bracketed per RFC 7230 § 5.4 — bare
        # ``hostname`` would emit ``Host: example.com`` for a request to
        # ``https://example.com:8443/`` and silently break virtual-hosted
        # endpoints that switch on the authority-form.
        if "Host" not in request_headers:
            request_headers["Host"] = parsed.netloc.rsplit("@", 1)[-1]

    # Resolve TLS verify setting. ca_bundle (when set) wins over verify.
    verify_param: Any = ca_bundle if ca_bundle else verify

    try:
        if allow_private:
            # Legacy path: operator opted into a private destination;
            # internal DNS / split-horizon resolution is the right model.
            return requests.post(
                url,
                json=json,
                data=data,
                headers=request_headers,
                timeout=timeout,
                verify=verify_param,
                # Redirect-following would bypass the up-front SSRF check —
                # a 30x to 169.254.169.254 from an attacker-controlled host
                # would otherwise leak the request payload to IMDS.
                allow_redirects=False,
            )
        with _pinned_session(parsed.scheme) as session:
            return session.post(
                target_url,
                json=json,
                data=data,
                headers=request_headers,
                timeout=timeout,
                verify=verify_param,
                allow_redirects=False,
            )
    except requests.RequestException as exc:
        # Mask the *outbound* header set (which includes the auto-set
        # ``Host`` and any caller secrets) — not the raw ``headers``
        # parameter, which may be ``None`` or stale relative to what
        # actually went on the wire.  Then strip the URL path/query —
        # ``requests`` embeds the request path in its transport-error
        # string and webhook URLs carry their bearer token there.
        masked_reason = _redact_url_paths_in_text(_mask_secrets_in_text(str(exc), request_headers), url)
        logger.warning(
            "safe_post failed url=%s reason=%s",
            _mask_netloc(url),
            masked_reason[:200],
        )
        raise


def safe_get(
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 10.0,
    verify: Any = True,
    ca_bundle: Optional[str] = None,
    allow_insecure_http: bool = False,
    allow_private: bool = False,
    min_timeout: float = 5.0,
    method: str = "GET",
) -> requests.Response:
    """Disciplined outbound GET / HEAD; raises early on policy violation.

    Mirrors :func:`safe_post`'s policy contract (scheme / SSRF / timeout
    floor / redirect refusal / TLS verify / header secret-masking) for
    read-side calls.  Used by ``forgelm doctor`` for the HuggingFace Hub
    reachability probe and by any future probe / telemetry / registry
    ping that needs an outbound GET or HEAD.

    Args:
        url: Target URL. Must be ``http://`` or ``https://``.
        headers: Outbound headers. ``Authorization`` / ``X-API-Key`` values
            are masked in the failure log.
        timeout: Per-request timeout in seconds. Must be ``>= min_timeout``.
        verify: Forwarded as ``requests``'s ``verify=`` argument.
        ca_bundle: Path to a custom CA bundle. When non-empty, takes
            precedence over ``verify``.
        allow_insecure_http: Set ``True`` only for paths where the operator
            has explicitly opted into ``http://``.
        allow_private: Set ``True`` to bypass the SSRF guard. Required for
            in-cluster mirrors / on-prem registry endpoints.
        min_timeout: Lower bound for ``timeout``. Defaults to ``5.0``
            (read probes are typically cheaper than POST bodies).
        method: ``"GET"`` (default) or ``"HEAD"``. The doctor's HF Hub
            probe uses HEAD to skip body download.

    Returns:
        The :class:`requests.Response` from the underlying call. The caller
        is responsible for inspecting ``response.ok`` / ``status_code``.

    Raises:
        HttpSafetyError: On policy violation — ``http://`` without opt-in,
            unsupported scheme, sub-floor timeout, private destination
            without opt-in, or unsupported method.
        requests.RequestException: On transport / TLS / network failure.
            Headers are masked in the warning log before the re-raise.
    """
    parsed = urlparse(url)

    # Scheme policy.
    if parsed.scheme == "http":
        if not allow_insecure_http:
            # The literal scheme tokens are split so the SonarCloud S5332
            # "use https" rule does not trip on the rejection message —
            # this branch *enforces* that rule, surfacing the error in
            # operator-readable form rather than violating it.
            _scheme_blocked = "http" + "://"  # noqa: S608 — see comment above
            _scheme_safe = "https" + "://"  # noqa: S608 — see comment above
            raise HttpSafetyError(f"{_scheme_blocked} blocked (use {_scheme_safe}); url={_mask_netloc(url)}")
    elif parsed.scheme != "https":
        raise HttpSafetyError(f"Unsupported URL scheme {parsed.scheme!r}; only http(s) allowed.")

    # Cheap local-policy checks first (timeout floor + method allowlist)
    # so a misconfigured caller is rejected without a DNS round-trip;
    # keeps unit tests for these branches hermetic.

    # Timeout floor.  ``math.isfinite`` rejects NaN / ±Infinity — see the
    # matching guard in ``safe_post`` for the "block forever" rationale.
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout < min_timeout:
        raise HttpSafetyError(f"Timeout below {min_timeout}s floor: timeout={timeout!r}")

    # Method policy — only GET / HEAD allowed (read-side helper).
    method_upper = method.upper()
    if method_upper not in ("GET", "HEAD"):
        raise HttpSafetyError(f"safe_get only supports GET / HEAD, got {method!r}.")

    # SSRF guard — see safe_post for the issue-#14 DNS-rebinding rationale.
    host = parsed.hostname or ""
    target_url = url
    # CaseInsensitiveDict — see safe_post for the duplicate-Host-header
    # rationale (caller may pass "host"/"HOST"/"Host" in any casing).
    request_headers: MutableMapping[str, str] = CaseInsensitiveDict(headers or {})
    if not allow_private:
        pinned_ip, block_reason = _resolve_safe_destination(host)
        if block_reason:
            raise HttpSafetyError(f"{block_reason} blocked: host={host or '<empty>'}")
        target_url = _build_pinned_url(parsed, pinned_ip)
        # See safe_post: use netloc (sans userinfo) so non-standard
        # ports and IPv6 brackets survive into the Host header per
        # RFC 7230 § 5.4; case-insensitive containment honours an
        # explicit caller override in any casing.
        if "Host" not in request_headers:
            request_headers["Host"] = parsed.netloc.rsplit("@", 1)[-1]

    verify_param: Any = ca_bundle if ca_bundle else verify

    try:
        if allow_private:
            return requests.request(
                method_upper,
                url,
                headers=request_headers,
                timeout=timeout,
                verify=verify_param,
                allow_redirects=False,
            )
        with _pinned_session(parsed.scheme) as session:
            return session.request(
                method_upper,
                target_url,
                headers=request_headers,
                timeout=timeout,
                verify=verify_param,
                allow_redirects=False,
            )
    except requests.RequestException as exc:
        # Mask the outbound header set, not the caller's possibly-None
        # ``headers`` parameter — see safe_post for the same rationale.
        # Strip the URL path/query too: ``requests`` embeds the request
        # path in its transport-error string.
        masked_reason = _redact_url_paths_in_text(_mask_secrets_in_text(str(exc), request_headers), url)
        logger.warning(
            "safe_get failed url=%s method=%s reason=%s",
            _mask_netloc(url),
            method_upper,
            masked_reason[:200],
        )
        raise
