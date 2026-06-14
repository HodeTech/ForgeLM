"""CI guard — every outbound HTTP call must route through forgelm/_http.py.

``forgelm/_http.py`` is the single SSRF-guarded chokepoint
(``safe_post`` / ``safe_get``).  Any module that reaches for ``requests``
/ ``urllib`` / ``httpx`` directly bypasses that guard.

This is the promotion of the former inline ``ci.yml`` grep to a tested
tool (F-P8-C-19).  The inline grep matched only direct dotted calls
(``requests.get(``); it missed:

- ``requests.Session().get(...)`` / a bound ``s = requests.Session(); s.get(...)``
- aliased imports ``from requests import get`` / ``from urllib.request import urlopen``
- whitespace before the paren — ``requests.get (url)``
- ``httpx.Client().post(...)``

Failure modes the guard flags (all matched on logical lines, so a call
split across physical lines by an open ``(`` is rejoined first):

- ``requests.<verb>(`` / ``requests.Session(`` / ``httpx.<verb>(`` /
  ``httpx.Client(`` / ``httpx.AsyncClient(``
- ``urllib.request.urlopen(`` / a bare ``urlopen(`` (caught via the
  aliased-import detector below)
- ``from requests import get|post|...`` / ``from urllib.request import urlopen``
  / ``from httpx import get|post|...`` — aliased entry points that escape a
  call-site regex.

What the guard does NOT flag:

- anything in ``forgelm/_http.py`` itself — it is the sanctioned wrapper.
- comment-only lines documenting the rule.
- ``import requests`` / ``import httpx`` on their own — importing the
  module is fine; only *calling* it (or aliasing a callable out of it) is
  the violation, so a module may ``import requests`` and pass it to
  ``_http`` helpers.

Run via::

    python3 tools/check_http_discipline.py

Exit codes (per ``tools/`` contract — NOT the public 0/1/2/3/4 surface):

- ``0`` — clean
- ``1`` — at least one undisciplined HTTP call / aliased import found
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Tuple

_REPO_ROOT = Path(__file__).resolve().parent.parent

# The wrapper module itself is the one place direct calls are allowed.
_ALLOWLISTED = {(_REPO_ROOT / "forgelm" / "_http.py").resolve()}

# HTTP verbs / client factories whose direct invocation bypasses _http.
_REQUESTS_CALLABLES = "get|post|put|delete|patch|request|head|options|Session"
_HTTPX_CALLABLES = "get|post|put|delete|patch|request|head|options|stream|Client|AsyncClient"

_PATTERNS = [
    # ``requests.get(`` / ``requests.Session (`` — ``\s*`` tolerates a
    # space before the paren that the old grep's literal ``(`` missed.
    re.compile(r"\brequests\.(?:" + _REQUESTS_CALLABLES + r")\s*\("),
    # ``httpx.post(`` / ``httpx.Client(`` / ``httpx.AsyncClient(``
    re.compile(r"\bhttpx\.(?:" + _HTTPX_CALLABLES + r")\s*\("),
    # ``urllib.request.urlopen(`` / ``urllib.request.Request(``
    re.compile(r"\burllib\.request\.(?:urlopen|Request|urlretrieve)\s*\("),
    # Aliased imports — pull a callable out of the module, escaping any
    # call-site regex: ``from requests import get`` /
    # ``from urllib.request import urlopen`` / ``from httpx import post``.
    re.compile(r"\bfrom\s+requests\s+import\s+(?:.*\b(?:" + _REQUESTS_CALLABLES + r")\b)"),
    re.compile(r"\bfrom\s+urllib\.request\s+import\s+(?:.*\b(?:urlopen|Request|urlretrieve)\b)"),
    re.compile(r"\bfrom\s+httpx\s+import\s+(?:.*\b(?:" + _HTTPX_CALLABLES + r")\b)"),
]


def _logical_lines(text: str):
    """Yield ``(start_line_no, joined_text)`` rejoining statements split
    across physical lines by an open ``(`` / ``[`` so a call broken over
    two lines is still matched as one logical line."""
    depth = 0
    start_no = 0
    buffer: List[str] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if depth == 0:
            start_no = line_no
            buffer = []
        buffer.append(line)
        depth += line.count("(") + line.count("[") - line.count(")") - line.count("]")
        if depth < 0:
            depth = 0
        if depth == 0:
            yield start_no, " ".join(part.strip() for part in buffer)
    if buffer and depth != 0:
        yield start_no, " ".join(part.strip() for part in buffer)


def scan_text(text: str) -> List[Tuple[int, str]]:
    """Return ``(line_number, logical_line)`` for every undisciplined hit."""
    findings: List[Tuple[int, str]] = []
    for line_no, logical in _logical_lines(text):
        stripped = logical.lstrip()
        if stripped.startswith("#"):
            continue
        for pattern in _PATTERNS:
            if pattern.search(logical):
                findings.append((line_no, logical.strip()))
                break
    return findings


def _scan_file(path: Path) -> List[Tuple[int, str]]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    return scan_text(text)


def _candidate_files() -> List[Path]:
    """Every ``.py`` file under forgelm/, minus the allowlisted wrapper."""
    root = _REPO_ROOT / "forgelm"
    files: List[Path] = []
    if not root.exists():
        return files
    for path in root.rglob("*.py"):
        if path.resolve() in _ALLOWLISTED:
            continue
        files.append(path)
    return files


def main() -> int:
    all_findings: List[Tuple[Path, int, str]] = []
    for path in _candidate_files():
        for line_no, raw in _scan_file(path):
            all_findings.append((path, line_no, raw))

    if not all_findings:
        scanned = len(_candidate_files())
        print(
            f"OK: {scanned} Python file(s) under forgelm/ (excluding _http.py) "
            "route HTTP through forgelm._http.safe_post / safe_get."
        )
        return 0

    print("FAIL: undisciplined HTTP call(s) found outside forgelm/_http.py:\n")
    for path, line_no, raw in all_findings:
        rel = path.relative_to(_REPO_ROOT)
        print(f"  {rel}:{line_no}")
        print(f"    > {raw}")
    print(
        "\nRoute through forgelm._http.safe_post / safe_get instead.\n"
        "See docs/standards/architecture.md 'HTTP discipline' section."
    )
    return 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
