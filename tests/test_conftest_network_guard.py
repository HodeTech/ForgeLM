"""Tests for the ``_block_network`` no-network guard in ``tests/conftest.py``.

``_block_network`` is an ``autouse`` fixture that converts testing.md rule 3
("no network in unit tests") from prose into an enforced tripwire: it
monkeypatches ``socket.socket.connect``/``connect_ex`` so any real,
non-loopback connection attempt from a unit test raises ``RuntimeError``
instead of hanging, flaking, or silently succeeding against a live service.

Nothing in the diff that introduced the guard exercised it directly — a
future refactor of ``conftest.py`` (e.g. changing the address-tuple
unpacking, or accidentally scoping the fixture wrong) could silently
disable it with no test failing to say so. These tests pin the guard's
three observable behaviours: it blocks non-loopback connections, it lets
loopback connections through, and ``@pytest.mark.allow_network`` opts a
test out of the block entirely.

Design note: none of these tests perform genuine outbound network I/O
(that would itself violate the very rule under test, and would make CI
flaky depending on runner network policy). The "blocked" case never
reaches the OS socket layer at all — the guard raises before any syscall.
The "loopback" case talks to a server this test process itself binds on
``127.0.0.1``. The "marker" case proves the guard skipped patching by
comparing ``socket.socket.connect`` against the real, pre-fixture
implementation captured at module import time (before any test-scoped
fixture has had a chance to run).
"""

from __future__ import annotations

import socket

import pytest

# Captured at collection time, before any function-scoped fixture (including
# ``_block_network``) has run for any test in the session. This is the
# ground-truth "unpatched" reference we compare against in
# ``test_allow_network_marker_skips_the_patch`` below.
_REAL_SOCKET_CONNECT = socket.socket.connect


class TestBlockedByDefault:
    """A unit test with no marker cannot open a real non-loopback connection."""

    def test_non_loopback_connect_raises_runtime_error(self):
        # 203.0.113.0/24 is TEST-NET-3 (RFC 5737) — reserved for documentation,
        # guaranteed non-loopback, and never actually dialled here: the guard
        # raises synchronously before any socket syscall happens.
        with pytest.raises(RuntimeError, match="Blocked a real network connection"):
            socket.create_connection(("203.0.113.1", 81), timeout=1)

    def test_blocked_error_message_names_the_escape_hatch(self):
        with pytest.raises(RuntimeError, match=r"@pytest\.mark\.allow_network"):
            socket.create_connection(("203.0.113.1", 81), timeout=1)

    def test_connect_ex_is_also_guarded(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            with pytest.raises(RuntimeError, match="Blocked a real network connection"):
                sock.connect_ex(("203.0.113.1", 81))
        finally:
            sock.close()


class TestLoopbackIsAllowed:
    """Loopback traffic is exempt from the guard even without a marker."""

    def test_loopback_connect_succeeds(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        try:
            # A TCP connect completes once queued in the listen backlog, so
            # no accept()-ing thread is needed for this to return cleanly.
            client = socket.create_connection(("127.0.0.1", port), timeout=1)
            client.close()
        finally:
            server.close()

    def test_localhost_hostname_is_treated_as_loopback(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        try:
            client = socket.create_connection(("localhost", port), timeout=1)
            client.close()
        finally:
            server.close()


class TestAllowNetworkMarker:
    """``@pytest.mark.allow_network`` opts a single test out of the guard."""

    @pytest.mark.allow_network
    def test_allow_network_marker_skips_the_patch(self):
        # If ``_block_network`` saw the marker and returned early (no
        # monkeypatch.setattr call), ``socket.socket.connect`` is still the
        # exact object captured before any fixture ran this session.
        assert socket.socket.connect is _REAL_SOCKET_CONNECT

    def test_unmarked_sibling_is_still_patched(self):
        # Sanity check that the identity comparison above is meaningful:
        # a normal (unmarked) test in the same module sees a *different*
        # (guard-wrapped) ``connect`` object.
        assert socket.socket.connect is not _REAL_SOCKET_CONNECT
