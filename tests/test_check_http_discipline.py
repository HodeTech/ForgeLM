"""Own-tests for tools/check_http_discipline.py (F-P8-C-19).

The previous HTTP-discipline gate was an inline ci.yml grep with no test
and several false negatives. These tests pin the bypass forms the grep
missed so a future regex regression that re-narrows the guard fails
loudly, and confirm the real ``forgelm/`` tree stays clean.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

import check_http_discipline as guard  # noqa: E402


class TestCaughtViolations:
    @pytest.mark.parametrize(
        "snippet",
        [
            "x = requests.get(url)",
            "x = requests.post(url, json=body)",
            # Bypass forms the old grep missed:
            "s = requests.Session()",
            "x = requests.Session().get(url)",
            "x = requests.get (url)",  # whitespace before paren
            "x = httpx.post(url)",
            "client = httpx.Client()",
            "client = httpx.AsyncClient()",
            "x = urllib.request.urlopen(url)",
            "req = urllib.request.Request(url)",
            # Aliased imports — escape any call-site-only regex:
            "from requests import get",
            "from requests import get as fetch",
            "from urllib.request import urlopen",
            "from httpx import post",
        ],
    )
    def test_violation_is_flagged(self, snippet):
        assert guard.scan_text(snippet), f"should flag: {snippet!r}"

    def test_multiline_split_call_is_flagged(self):
        # A call split across physical lines must still be caught after the
        # logical-line rejoin.
        text = "x = requests.get(\n    url,\n    timeout=5,\n)\n"
        assert guard.scan_text(text)


class TestNotFlagged:
    @pytest.mark.parametrize(
        "snippet",
        [
            "import requests",  # bare import is fine; only calls/aliases bite
            "import httpx",
            "# requests.get(url)  <- documented in a comment",
            "from forgelm._http import safe_post, safe_get",
            "safe_post(url, json=body)",
            "logger.info('use requests.get only via _http')",  # prose in a string
        ],
    )
    def test_clean_line_not_flagged(self, snippet):
        assert guard.scan_text(snippet) == []


class TestRepoStaysClean:
    def test_forgelm_tree_is_disciplined(self):
        # _http.py is allowlisted; every other module must route through it.
        assert guard.main() == 0

    def test_http_wrapper_is_allowlisted(self):
        http_py = Path(guard.__file__).resolve().parent.parent / "forgelm" / "_http.py"
        assert http_py.resolve() in guard._ALLOWLISTED
        assert http_py not in guard._candidate_files()


class TestWiredIntoCi:
    def test_guard_referenced_in_ci_workflow(self):
        ci = Path(guard.__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"
        assert "tools/check_http_discipline.py" in ci.read_text(encoding="utf-8")
