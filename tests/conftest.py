"""Shared test fixtures and utilities for ForgeLM tests."""

import ipaddress
import os
import socket

import pytest

# Re-export the canonical factory so legacy imports continue to work.
# New tests should prefer the ``minimal_config`` pytest fixture below.
# We use the fully qualified ``tests._helpers`` path so the import resolves
# under both ``--import-mode=prepend`` (pytest default) and the recommended
# ``--import-mode=importlib``. The fixture itself is re-exposed via
# ``tests.conftest`` for callers that prefer the dotted path.
from tests._helpers.factories import minimal_config  # noqa: F401  (re-export)


@pytest.fixture(name="minimal_config")
def _factory_fixture():
    """Provide the ``minimal_config`` factory to tests via fixture injection.

    The fixture returns the *factory itself*, not a pre-built dict, so tests
    can call ``minimal_config(training={"trainer_type": "dpo"})`` to build
    customized configs without re-importing the helper.
    """
    from tests._helpers.factories import minimal_config as _factory

    return _factory


def pytest_configure(config):
    """Register custom markers so ``--strict-markers`` runs don't warn."""
    config.addinivalue_line(
        "markers",
        "allow_network: opt a test out of the no-network guard (loopback is always allowed).",
    )
    config.addinivalue_line(
        "markers",
        "real_fingerprint: run the real HF-Hub dataset-fingerprint helpers "
        "(network dependency stubbed inside the test) instead of the default no-op stubs.",
    )


# Only true loopback targets are exempt. ``0.0.0.0`` is deliberately NOT
# allowed: it is the unspecified/wildcard address, not loopback, and letting it
# through would widen the guard for no test that needs it.
_NETWORK_ALLOWED_HOSTS = frozenset({"localhost"})


def _is_loopback_address(address):
    """True for loopback / local socket targets the no-network guard permits."""
    host = address[0] if isinstance(address, (tuple, list)) and address else address
    if not isinstance(host, str):
        return False
    if host in _NETWORK_ALLOWED_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@pytest.fixture(autouse=True)
def _block_network(request, monkeypatch):
    """Fail fast if a unit test opens a real (non-loopback) network connection.

    ForgeLM's unit suite must never touch the network (``testing.md``: no
    network, no GPU). This guard patches the socket connect paths so an
    accidental real call — to the HF Hub, a webhook endpoint, or a judge API —
    surfaces as an immediate, clear error instead of a slow, flaky, or
    offline-only failure. Loopback (127.0.0.0/8, ``::1``, ``localhost``) and
    ``AF_UNIX`` sockets stay allowed so tests that spin up a local server keep
    working; mark a test ``@pytest.mark.allow_network`` to opt out entirely.

    Scope: this patches ``socket.socket.connect``/``connect_ex`` — the TCP
    connect surface that ``requests``/``urllib3``/``httpx`` use. It is not a
    full egress sandbox: bare DNS resolution (``getaddrinfo``), raw UDP, and
    subprocess network calls are not intercepted. It is a fail-fast tripwire
    for the common accidental-HTTP-call case, not a security boundary.
    """
    if request.node.get_closest_marker("allow_network"):
        return

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def _guarded(real):
        def _inner(self, address, *args, **kwargs):
            if getattr(self, "family", None) == getattr(socket, "AF_UNIX", object()):
                return real(self, address, *args, **kwargs)
            if _is_loopback_address(address):
                return real(self, address, *args, **kwargs)
            raise RuntimeError(
                f"Blocked a real network connection to {address!r} from a unit test "
                f"({request.node.nodeid}). Mock the network call, or mark the test "
                "@pytest.mark.allow_network if a live connection is genuinely required."
            )

        return _inner

    monkeypatch.setattr(socket.socket, "connect", _guarded(real_connect))
    monkeypatch.setattr(socket.socket, "connect_ex", _guarded(real_connect_ex))


@pytest.fixture(autouse=True)
def _stub_hf_dataset_fingerprint(request, monkeypatch):
    """Keep dataset fingerprinting offline by default.

    ``compute_dataset_fingerprint`` treats any non-file path as a Hugging Face
    Hub id and calls ``load_dataset_builder`` / ``HfApi().dataset_info`` on it —
    real network I/O. The shared ``minimal_config`` factory uses a hub-style
    ``org/dataset`` id, so every test that generates a training manifest would
    otherwise reach out to the Hub (slow, flaky, offline-failing — and now
    blocked by ``_block_network``). Stub the two Hub-fingerprint helpers to
    no-ops by default; mark a test ``@pytest.mark.real_fingerprint`` to exercise
    the real code path (with its network dependency stubbed inside the test).
    """
    if request.node.get_closest_marker("real_fingerprint"):
        return
    import forgelm.compliance as _compliance

    monkeypatch.setattr(_compliance, "_fingerprint_hf_metadata", lambda dataset_path, fingerprint: None)
    monkeypatch.setattr(_compliance, "_fingerprint_hf_revision", lambda dataset_path, fingerprint: None)


@pytest.fixture(autouse=True)
def _pin_audit_operator(monkeypatch):
    """Pin a deterministic operator identity for the entire test session.

    Closure plan Faz 3 makes ``AuditLogger.__init__`` raise ``ConfigError``
    when no operator can be derived. Most tests instantiate ``AuditLogger``
    indirectly (training manifests, governance reports). To keep them green
    on minimal CI runners — where ``$USER`` may be unset and getpass may
    fail under sandboxed users — we pin ``FORGELM_OPERATOR`` here.

    Tests that exercise the resolution logic itself (the
    ``TestAuditLoggerOperatorIdentity`` class) explicitly clear this
    via ``monkeypatch.delenv`` inside the test body.
    """
    monkeypatch.setenv("FORGELM_OPERATOR", os.environ.get("FORGELM_OPERATOR") or "test-operator")


@pytest.fixture(autouse=True)
def _isolate_wizard_state(request, tmp_path_factory, monkeypatch):
    """B10 — keep wizard XDG state out of the developer's real ``~/.cache``.

    Any test under ``tests/test_wizard_*`` (or anything that ends up
    invoking ``forgelm.wizard._save_wizard_state`` indirectly) writes
    a YAML to ``$XDG_CACHE_HOME/forgelm/wizard_state.yaml``. Without
    isolation a contributor running ``pytest`` would have their real
    in-flight wizard snapshot clobbered.

    We redirect ``XDG_CACHE_HOME`` to a per-test tmp dir for every
    wizard-flavoured test file. The ``test_wizard_phase22`` module
    already has its own ``isolated_state_dir`` fixture; this one is
    additive — they coexist because they both monkeypatch the same
    env var, and the more-specific fixture takes precedence on the
    tests that explicitly request it.
    """
    test_path = str(getattr(request.node, "fspath", "") or "")
    if "test_wizard_" not in test_path and "test_phase12_5" not in test_path:
        return
    isolated = tmp_path_factory.mktemp("wizard_xdg_isolated")
    monkeypatch.setenv("XDG_CACHE_HOME", str(isolated))
