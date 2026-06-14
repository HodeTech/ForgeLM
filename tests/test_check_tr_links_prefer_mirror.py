"""Tests for tools/check_tr_links_prefer_mirror.py (H11 / F-P8-C-04).

The guard fails when a ``*-tr.md`` page links the un-suffixed EN sibling even
though a ``<stem>-tr.md`` mirror exists (a Turkish reader silently routed to
English). Before this package no guard caught it: anchor-resolution only proves
the link resolves, parity only diffs heading spines.

These tests pin: a leaking link fails, the ``**Ayna:**`` backlink is exempt, a
link with no TR mirror is allowed, an already-TR link is clean, anchor fragments
survive the ``--fix`` rewrite, the live docs tree is clean (the 62→0 sweep), and
the guard is wired into CI + the gauntlet.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TOOL_PATH = _REPO_ROOT / "tools" / "check_tr_links_prefer_mirror.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("check_tr_links_prefer_mirror", _TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture(scope="module")
def tool():
    return _load_tool()


def _make_pair(tmp_path: Path, body_tr: str, *, mirror_exists: bool = True) -> Path:
    """Write a ``page-tr.md`` plus, optionally, its ``target-tr.md`` mirror.

    Returns the ``page-tr.md`` path. The link target used in tests is
    ``target.md`` (EN sibling); its TR mirror ``target-tr.md`` is created iff
    *mirror_exists*.
    """
    docs = tmp_path / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "target.md").write_text("# Target EN\n", encoding="utf-8")
    if mirror_exists:
        (docs / "target-tr.md").write_text("# Hedef TR\n", encoding="utf-8")
    page = docs / "page-tr.md"
    page.write_text(body_tr, encoding="utf-8")
    return page


def test_leaking_link_is_flagged(tool, tmp_path):
    page = _make_pair(tmp_path, "Bkz. [Hedef](target.md) sayfası.\n")
    assert tool._scan_file(page)


def test_ayna_backlink_is_exempt(tool, tmp_path):
    page = _make_pair(tmp_path, "> **Ayna:** [target.md](target.md)\n")
    assert tool._scan_file(page) == []


def test_no_mirror_link_is_allowed(tool, tmp_path):
    page = _make_pair(tmp_path, "Bkz. [Hedef](target.md).\n", mirror_exists=False)
    assert tool._scan_file(page) == []


def test_already_tr_link_is_clean(tool, tmp_path):
    page = _make_pair(tmp_path, "Bkz. [Hedef](target-tr.md).\n")
    assert tool._scan_file(page) == []


def test_external_and_anchor_links_ignored(tool, tmp_path):
    page = _make_pair(
        tmp_path,
        "Bkz. [HF](https://huggingface.co) ve [üst](#baslik).\n",
    )
    assert tool._scan_file(page) == []


def test_fix_preserves_anchor_fragment(tool, tmp_path):
    page = _make_pair(tmp_path, "Bkz. [Hedef](target.md#bolum-1).\n")
    assert tool._fix_file(page) == 1
    assert "target-tr.md#bolum-1" in page.read_text(encoding="utf-8")
    # idempotent: a second pass finds nothing to fix.
    assert tool._fix_file(page) == 0


def test_fix_leaves_ayna_backlink_untouched(tool, tmp_path):
    page = _make_pair(tmp_path, "> **Ayna:** [target.md](target.md)\n")
    assert tool._fix_file(page) == 0
    assert "(target.md)" in page.read_text(encoding="utf-8")


def test_real_docs_tree_is_clean(tool):
    """The live docs/ tree was swept to zero leaks in this package — this is the
    tripwire ci.yml runs on every PR."""
    assert tool.main(["--quiet"]) == 0


def test_guard_wired_into_ci():
    ci = (_REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "check_tr_links_prefer_mirror.py" in ci


def test_guard_wired_into_gauntlet():
    for doc in ("CLAUDE.md", "AGENTS.md"):
        text = (_REPO_ROOT / doc).read_text(encoding="utf-8")
        assert "check_tr_links_prefer_mirror.py" in text, f"{doc} gauntlet missing the TR-links guard"
