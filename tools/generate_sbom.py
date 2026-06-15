#!/usr/bin/env python3
"""Emit a minimal CycloneDX 1.5 SBOM for the active Python environment.

Reads the installed package set via ``pip list --format=json`` and renders a
CycloneDX 1.5 JSON document on stdout. Pure stdlib, cross-platform — used by
the ``publish.yml`` cross-OS matrix to attach a per-(os, python-version) SBOM
artifact to every tagged release.

This is intentionally a hand-rolled emitter rather than the optional
``cyclonedx-bom`` PyPI package: keeping the dependency footprint at zero means
the SBOM step can never silently degrade an otherwise-green matrix combo just
because a transitive dep failed to wheel-build on (say) Python 3.13/Windows.

Output schema reference: https://cyclonedx.org/docs/1.5/json/

Usage:
    python tools/generate_sbom.py > sbom.json
"""

from __future__ import annotations

import datetime as _dt
import json
import platform
import re
import subprocess
import sys
import uuid
from importlib.metadata import PackageNotFoundError, version
from typing import Any


def _installed_packages() -> list[dict[str, str]]:
    """Return ``pip list --format=json`` output as a list of dicts."""
    try:
        result = subprocess.run(  # NOSONAR python:S4823; nosec B603,B607 — all args are literals / controlled
            [sys.executable, "-m", "pip", "list", "--format=json", "--disable-pip-version-check"],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        detail = f" — stderr: {stderr}" if stderr else ""
        raise RuntimeError(f"pip list failed: {exc}{detail}") from exc
    except subprocess.TimeoutExpired as exc:
        stdout = (exc.stdout or "").strip()
        stderr = (exc.stderr or "").strip()
        raise RuntimeError(f"pip list timed out: {exc}; stdout={stdout!r}; stderr={stderr!r}") from exc
    except FileNotFoundError as exc:
        raise RuntimeError(f"pip list failed: {exc}") from exc
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"pip list returned invalid JSON: {exc}") from exc
    if not isinstance(payload, list) or not all(isinstance(p, dict) for p in payload):
        raise RuntimeError(
            f"pip list returned unexpected payload shape (expected list of dicts, "
            f"got {type(payload).__name__}): {payload!r:.200}"
        )
    return payload


def _purl(name: str, ver: str) -> str:
    """Build a Package URL (purl) for a PyPI package.

    Package name is normalized per PEP 503 (runs of [-_.] → '-', lowercased)
    so ``My.Package`` and ``my-package`` produce identical purls.
    """
    # PEP 503 canonical form: collapse any run of [-_.] to a single dash,
    # then lowercase — equivalent to packaging.utils.canonicalize_name().
    normalized = re.sub(r"[-_.]+", "-", name).lower()
    return f"pkg:pypi/{normalized}@{ver}"


def _component(pkg: dict[str, str]) -> dict[str, Any]:
    name = pkg.get("name", "")
    ver = pkg.get("version", "")
    if not name or not ver:
        raise ValueError(f"Package entry is missing required 'name' or 'version' field: {pkg!r}")
    return {
        "type": "library",
        "name": name,
        "version": ver,
        "purl": _purl(name, ver),
        "bom-ref": _purl(name, ver),
    }


def _forgelm_version() -> str:
    try:
        return version("forgelm")
    except PackageNotFoundError:
        return "0.0.0+dev"


def build_sbom() -> dict[str, Any]:
    """Construct the CycloneDX 1.5 SBOM document."""
    now = _dt.datetime.now(tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    forgelm_ver = _forgelm_version()
    # Canonical sort by ``bom-ref`` (PEP 503-normalized purl, unique) so the
    # emitted component order is stable regardless of pip version / install
    # order. Without this, ``pip list`` ordering can differ across pip majors,
    # making the documented "re-emit from the git tag and diff" auditor flow
    # report spurious ordering-only diffs (F-P5-OPUS-18).
    components = sorted(
        (_component(p) for p in _installed_packages()),
        key=lambda c: c["bom-ref"],
    )
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{uuid.uuid4()}",
        "version": 1,
        "metadata": {
            "timestamp": now,
            # CycloneDX 1.5 introduces the object-shaped ``tools`` payload
            # (``{"components": [...]}``).  The plain-array form is the
            # 1.4-and-earlier shape, kept readable by 1.5 consumers but flagged
            # as deprecated.  Emit the 1.5-native form so downstream tooling
            # (Dependency-Track ≥4.10 etc.) does not log a deprecation warning.
            "tools": {
                "components": [
                    {
                        "type": "application",
                        "author": "ForgeLM",
                        "name": "generate_sbom.py",
                        "version": "1.0.0",
                    }
                ]
            },
            "component": {
                "type": "application",
                "name": "forgelm",
                "version": forgelm_ver,
                "purl": _purl("forgelm", forgelm_ver),
                "bom-ref": _purl("forgelm", forgelm_ver),
            },
            "properties": [
                {"name": "python:version", "value": platform.python_version()},
                {"name": "python:implementation", "value": platform.python_implementation()},
                {"name": "platform:system", "value": platform.system()},
                {"name": "platform:release", "value": platform.release()},
                {"name": "platform:machine", "value": platform.machine()},
            ],
        },
        "components": components,
    }


_EXIT_SUCCESS = 0
_EXIT_CONFIG_ERROR = 1  # invalid input / missing data
_EXIT_RUNTIME_ERROR = 2  # subprocess / I/O / JSON parse failure


def main() -> int:
    try:
        sbom = build_sbom()
    except ValueError as exc:
        print(f"error: invalid input — {exc}", file=sys.stderr)
        return _EXIT_CONFIG_ERROR
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return _EXIT_RUNTIME_ERROR
    # ``BrokenPipeError`` (an ``OSError`` subclass) fires when the consumer of
    # our stdout closes the pipe early — e.g. ``generate_sbom.py | head``.
    # Without an explicit catch, Python turns that into an uncaught exception
    # at interpreter shutdown and exits non-zero, breaking the documented
    # exit-code contract for downstream automation.  Other ``OSError``
    # variants (ENOSPC on a redirect target, write-after-close on a teed pipe)
    # land in the same bucket — operator-actionable I/O failure → runtime err.
    try:
        # ``ensure_ascii=False`` keeps any non-ASCII package metadata (author
        # names, descriptions) readable rather than ``\uXXXX``-escaped, matching
        # the project's other JSON emitters (doctor / cache).
        json.dump(sbom, sys.stdout, indent=2, sort_keys=False, ensure_ascii=False)
        sys.stdout.write("\n")
    except OSError as exc:
        # ``stderr`` is the right channel here: stdout is the SBOM payload and
        # we just established it is unwritable.
        print(f"error: failed to write SBOM to stdout — {exc}", file=sys.stderr)
        return _EXIT_RUNTIME_ERROR
    return _EXIT_SUCCESS


if __name__ == "__main__":
    raise SystemExit(main())
