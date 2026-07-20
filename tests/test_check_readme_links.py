"""Tests for tools/check_readme_links.py.

The guard exists because ``pyproject.toml`` ships ``README.md`` as the PyPI
long description with no URL rewriting, so the 38 relative links the README
carried before the v0.10.0 documentation pass were all dead on
``pypi.org/project/forgelm/`` — and no guard in the repo could see them.
``check_anchor_resolution.py`` walks ``docs/`` only,
``check_source_path_refs.py`` inspects backticked source paths rather than
Markdown hrefs, and ``check_doc_numerical_claims.py`` walks ``DOCS.rglob``.
The highest-traffic document in the project had zero link coverage.

Three layers, mirroring ``tests/test_check_source_path_refs.py``:

* **Unit** — the fence tracker, the footnote filter and the traversal check
  pinned against synthetic in-memory input, so they stay independent of the
  real, evolving README.
* **Enforcement** (:class:`TestEnforcement`) — ``main()`` driven against a
  temporary tree via ``--repo-root``, so every failure branch executes and
  its exit code is asserted. Without this layer the ``return 1`` surface is
  unreached: a live-repo-only suite is green by construction, which is the
  precise defect this guard was written to prevent it having.
* **Live repo** (:class:`TestRealRepo`) — the invariant CI relies on,
  ``main(["--strict"]) == 0``.

The false-positive controls get *positive* coverage too: a fenced block, an
in-document anchor, a footnote definition and a relative link on a
GitHub-only surface must NOT be reported. A guard that cries wolf gets
disabled, so its silence on legitimate input is as much a contract as its
noise on a dead link.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TOOL_PATH = _REPO_ROOT / "tools" / "check_readme_links.py"

_BLOB = "https://github.com/HodeTech/ForgeLM/blob/main"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, f"could not load {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


guard = _load(_TOOL_PATH, "_check_readme_links_under_test")


def _make_tree(tmp_path: Path, readme: str, contributing: str = "# Contributing\n") -> Path:
    """Write a minimal repo-shaped tree and return its root."""
    (tmp_path / "README.md").write_text(readme, encoding="utf-8")
    (tmp_path / "CONTRIBUTING.md").write_text(contributing, encoding="utf-8")
    (tmp_path / "docs" / "guides").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs" / "guides" / "real.md").write_text("# real\n", encoding="utf-8")
    (tmp_path / "LICENSE").write_text("Apache\n", encoding="utf-8")
    return tmp_path


# --------------------------------------------------------------------------
# Unit — the parsing helpers
# --------------------------------------------------------------------------


class TestIterHrefs:
    def test_finds_inline_links_and_images(self):
        found = guard._iter_hrefs("[a](https://x.example) and ![b](https://y.example/i.svg)\n")
        assert [href for _, _, href in found] == ["https://x.example", "https://y.example/i.svg"]

    def test_skips_fenced_blocks(self):
        text = "before [a](https://x.example)\n```bash\npip install 'forgelm[qlora]'\n```\nafter\n"
        assert [href for _, _, href in guard._iter_hrefs(text)] == ["https://x.example"]

    def test_tilde_fence_is_tracked_independently(self):
        text = "~~~\n[not-a-link](nope.md)\n~~~\n[real](https://x.example)\n"
        assert [href for _, _, href in guard._iter_hrefs(text)] == ["https://x.example"]

    def test_footnote_definition_is_not_a_link(self):
        assert guard._iter_hrefs("[^1]: `qlora` pins a Linux-only wheel.\n") == []

    def test_reference_style_definition_is_a_link(self):
        found = guard._iter_hrefs("[label]: https://x.example\n")
        assert found == [(1, "link-definition", "https://x.example")]

    def test_link_title_is_not_folded_into_the_href(self):
        found = guard._iter_hrefs('[a](https://x.example "the title")\n')
        assert [href for _, _, href in found] == ["https://x.example"]

    def test_line_numbers_are_one_indexed_and_survive_fences(self):
        text = "\n\n```\nx\n```\n[a](https://x.example)\n"
        assert guard._iter_hrefs(text)[0][0] == 6


class TestResolveViolation:
    def test_existing_path_is_clean(self, tmp_path):
        (tmp_path / "LICENSE").write_text("x", encoding="utf-8")
        assert guard._resolve_violation(tmp_path, "README.md", 1, "LICENSE", "href") is None

    def test_missing_path_is_reported(self, tmp_path):
        problem = guard._resolve_violation(tmp_path, "README.md", 7, "gone.md", "href")
        assert problem is not None and "does not exist" in problem and "README.md:7" in problem

    def test_traversal_outside_the_repo_is_reported(self, tmp_path):
        nested = tmp_path / "repo"
        nested.mkdir()
        problem = guard._resolve_violation(nested, "README.md", 3, "../secret.txt", "href")
        assert problem is not None and "escapes the repository" in problem


# --------------------------------------------------------------------------
# Enforcement — main() against a temporary tree
# --------------------------------------------------------------------------


class TestEnforcement:
    def test_clean_tree_exits_zero(self, tmp_path, capsys):
        root = _make_tree(tmp_path, f"# T\n\n[guide]({_BLOB}/docs/guides/real.md)\n")
        assert guard.main(["--repo-root", str(root)]) == 0
        assert "OK:" in capsys.readouterr().out

    def test_relative_link_in_readme_fails(self, tmp_path, capsys):
        root = _make_tree(tmp_path, "# T\n\n[guide](docs/guides/real.md)\n")
        assert guard.main(["--repo-root", str(root)]) == 1
        out = capsys.readouterr().out
        assert "not an absolute https URL" in out and "pypi.org" in out

    def test_relative_link_fails_even_when_the_target_exists(self, tmp_path):
        """The PyPI break is about form, not target — an existing file is no defence."""
        root = _make_tree(tmp_path, "# T\n\n[license](LICENSE)\n")
        assert (root / "LICENSE").exists()
        assert guard.main(["--repo-root", str(root)]) == 1

    def test_absolute_link_to_missing_path_fails(self, tmp_path, capsys):
        root = _make_tree(tmp_path, f"# T\n\n[gone]({_BLOB}/docs/guides/gone.md)\n")
        assert guard.main(["--repo-root", str(root)]) == 1
        assert "does not exist" in capsys.readouterr().out

    def test_offsite_https_link_is_not_resolved_on_disk(self, tmp_path):
        """Only github.com/HodeTech/ForgeLM blob URLs name in-repo paths."""
        root = _make_tree(tmp_path, "# T\n\n[pypi](https://pypi.org/project/forgelm/)\n")
        assert guard.main(["--repo-root", str(root)]) == 0

    def test_in_document_anchor_is_allowed(self, tmp_path):
        root = _make_tree(tmp_path, "# T\n\n[jump](#exit-codes)\n")
        assert guard.main(["--repo-root", str(root)]) == 0

    def test_mailto_is_allowed(self, tmp_path):
        root = _make_tree(tmp_path, "# T\n\n[mail](mailto:security@example.com)\n")
        assert guard.main(["--repo-root", str(root)]) == 0

    def test_fenced_relative_path_is_not_flagged(self, tmp_path):
        """A shell transcript is documentation of a command, not a hyperlink."""
        root = _make_tree(tmp_path, "# T\n\n```bash\ncat [a](docs/x.md)\n```\n")
        assert guard.main(["--repo-root", str(root)]) == 0

    def test_contributing_may_use_relative_links(self, tmp_path):
        """Rule 1 is README-only; CONTRIBUTING is read on GitHub."""
        root = _make_tree(tmp_path, "# T\n", contributing="# C\n\n[std](docs/guides/real.md)\n")
        assert guard.main(["--repo-root", str(root)]) == 0

    def test_contributing_relative_link_must_still_resolve(self, tmp_path, capsys):
        root = _make_tree(tmp_path, "# T\n", contributing="# C\n\n[std](docs/guides/gone.md)\n")
        assert guard.main(["--repo-root", str(root)]) == 1
        assert "does not exist" in capsys.readouterr().out

    def test_contributing_relative_link_with_anchor_resolves_on_the_file(self, tmp_path):
        root = _make_tree(tmp_path, "# T\n", contributing="# C\n\n[std](docs/guides/real.md#section)\n")
        assert guard.main(["--repo-root", str(root)]) == 0

    def test_contributing_rejects_insecure_http(self, tmp_path, capsys):
        root = _make_tree(tmp_path, "# T\n", contributing="# C\n\n[x](http://example.com)\n")
        assert guard.main(["--repo-root", str(root)]) == 1
        assert "insecure http://" in capsys.readouterr().out

    def test_missing_surface_is_reported_not_skipped(self, tmp_path, capsys):
        root = _make_tree(tmp_path, "# T\n")
        (root / "CONTRIBUTING.md").unlink()
        assert guard.main(["--repo-root", str(root)]) == 1
        assert "missing from the checkout" in capsys.readouterr().out

    def test_undecodable_surface_fails_closed(self, tmp_path, capsys):
        root = _make_tree(tmp_path, "# T\n")
        (root / "README.md").write_bytes(b"\xff\xfe not utf-8 \xff")
        assert guard.main(["--repo-root", str(root)]) == 1
        assert "could not read" in capsys.readouterr().out

    def test_strict_flag_changes_nothing(self, tmp_path):
        root = _make_tree(tmp_path, "# T\n\n[guide](docs/guides/real.md)\n")
        assert guard.main(["--repo-root", str(root)]) == 1
        assert guard.main(["--strict", "--repo-root", str(root)]) == 1

    def test_every_violation_is_reported_not_just_the_first(self, tmp_path, capsys):
        root = _make_tree(
            tmp_path,
            f"# T\n\n[a](one.md)\n[b](two.md)\n[c]({_BLOB}/docs/guides/gone.md)\n",
        )
        assert guard.main(["--repo-root", str(root)]) == 1
        assert capsys.readouterr().out.count("✗") == 3


# --------------------------------------------------------------------------
# Live repo — the invariant CI depends on
# --------------------------------------------------------------------------


class TestRealRepo:
    def test_repo_is_clean(self):
        assert guard.main(["--strict"]) == 0

    def test_readme_is_the_pypi_long_description(self):
        """Rule 1's premise. If this changes, the guard's rationale needs revisiting."""
        pyproject = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        assert 'readme = "README.md"' in pyproject

    def test_readme_carries_no_relative_links(self):
        """Belt-and-braces against the original defect, independent of main()."""
        text = (_REPO_ROOT / "README.md").read_text(encoding="utf-8")
        offenders = [
            href for _, _, href in guard._iter_hrefs(text) if not href.startswith(("https://", "#", "mailto:"))
        ]
        assert offenders == [], f"relative hrefs would 404 on PyPI: {offenders}"

    @pytest.mark.parametrize("surface", [name for name, _ in guard._SURFACES])
    def test_declared_surfaces_exist(self, surface):
        assert (_REPO_ROOT / surface).is_file()
