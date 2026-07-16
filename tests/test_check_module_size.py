"""Wave 2-9 / PR #29 — tools/check_module_size.py regression tests.

The module-size guard is the gate that prevents NEW drift past the
architecture-doc ~1000-LOC sub-package-split ceiling.  A regression
that silently under-reports (e.g. accidentally classifying every
module as grandfathered, or losing the strict-mode escalation) would
let drift accumulate undetected — exactly the failure mode the
guard was added to prevent.

Pinned contracts:

1. ``_count_code_lines`` skips blanks and pure-comment lines but
   counts everything else (including docstring text).
2. The grandfathered set captures the seven modules audited at
   PR #29 HEAD.
3. ``main()`` exits 0 in default mode at HEAD because every
   over-threshold module is grandfathered.
4. ``main()`` exits 0 in ``--strict`` mode at HEAD for the same
   reason.
5. Synthetic NEW drift in a non-grandfathered file triggers a fatal
   exit (1) in default mode when over the fail-threshold, and in
   strict mode when over the warn-threshold.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TOOL_PATH = _REPO_ROOT / "tools" / "check_module_size.py"


def _load_tool() -> object:
    """Import ``tools/check_module_size.py`` without polluting sys.path."""
    spec = importlib.util.spec_from_file_location("check_module_size", _TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_module_size"] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# §1 — _count_code_lines: blank / comment / code classification
# ---------------------------------------------------------------------------


class TestCountCodeLines:
    def test_excludes_blanks_and_pure_comments(self, tmp_path: Path):
        tool = _load_tool()
        sample = tmp_path / "sample.py"
        sample.write_text(
            "\n".join(
                [
                    "import os",  # 1 code line
                    "",  # blank
                    "# pure comment",  # comment-only
                    "def f():",  # 1 code line
                    "    return 1",  # 1 code line
                    "",  # blank
                    "    # indented comment",  # comment-only
                ]
            ),
            encoding="utf-8",
        )
        assert tool._count_code_lines(sample) == 3

    def test_counts_docstring_lines(self, tmp_path: Path):
        # Docstrings ARE counted (per module-docstring rationale: they
        # represent maintenance burden and excluding them would let
        # contributors silently grow a module by inflating prose).
        tool = _load_tool()
        sample = tmp_path / "sample.py"
        sample.write_text(
            "\n".join(
                [
                    "def f():",
                    '    """First line of docstring.',
                    "",  # blank inside docstring → still skipped
                    "    Second line of docstring.",
                    '    """',
                    "    return 1",
                ]
            ),
            encoding="utf-8",
        )
        # Lines counted: def f, """First..., Second..., """, return 1 = 5
        assert tool._count_code_lines(sample) == 5

    def test_inline_trailing_comment_counts_as_code(self, tmp_path: Path):
        tool = _load_tool()
        sample = tmp_path / "sample.py"
        sample.write_text("x = 1  # trailing comment\n", encoding="utf-8")
        assert tool._count_code_lines(sample) == 1

    def test_empty_file_is_zero(self, tmp_path: Path):
        tool = _load_tool()
        sample = tmp_path / "empty.py"
        sample.write_text("", encoding="utf-8")
        assert tool._count_code_lines(sample) == 0

    def test_only_comments_is_zero(self, tmp_path: Path):
        tool = _load_tool()
        sample = tmp_path / "comments.py"
        sample.write_text(
            "#!/usr/bin/env python3\n# header comment\n# more\n",
            encoding="utf-8",
        )
        assert tool._count_code_lines(sample) == 0


# ---------------------------------------------------------------------------
# §2 — _GRANDFATHERED_OVER_CEILING: PR #29 HEAD audit list
# ---------------------------------------------------------------------------


class TestGrandfatheredSet:
    def test_contains_expected_modules(self):
        tool = _load_tool()
        assert len(tool._GRANDFATHERED_OVER_CEILING) == 9

    def test_contains_expected_paths(self):
        tool = _load_tool()
        expected = {
            "forgelm/compliance.py",
            "forgelm/trainer.py",
            "forgelm/ingestion.py",
            "forgelm/cli/subcommands/_purge.py",
            "forgelm/config.py",
            "forgelm/cli/_parser.py",
            "forgelm/cli/subcommands/_doctor.py",
            # Phase 14 (v0.7.0): pipeline orchestrator — sub-package
            # split tracked for v0.7.x alongside the Phase 15 audit
            # split pattern.
            "forgelm/cli/_pipeline.py",
            # v0.9.1: crossed 1000 LOC with generation-based Llama-Guard
            # scoring; forgelm/safety/ sub-package split is the planned
            # next step.
            "forgelm/safety.py",
        }
        assert set(tool._GRANDFATHERED_OVER_CEILING) == expected

    def test_uses_posix_separators(self):
        # Cross-platform stability: the path keys must match the
        # POSIX form returned by ``Path.relative_to(...).as_posix()``.
        tool = _load_tool()
        for p in tool._GRANDFATHERED_OVER_CEILING:
            assert "\\" not in p
            assert p.startswith("forgelm/")


# ---------------------------------------------------------------------------
# §3 — main(): exit-code logic at HEAD + on synthetic drift
# ---------------------------------------------------------------------------


class TestMainAtHead:
    def test_default_mode_at_head_is_green(self, capsys):
        # At PR #29 HEAD every over-threshold module is grandfathered →
        # exit 0.  This is the canonical "no NEW drift" signal.
        tool = _load_tool()
        rc = tool.main([])
        assert rc == 0

    def test_strict_mode_at_head_is_green(self, capsys):
        # In strict mode the same is true: grandfathered modules are
        # exempt from escalation, so HEAD must still exit 0.
        tool = _load_tool()
        rc = tool.main(["--strict"])
        assert rc == 0

    def test_quiet_mode_suppresses_summary(self, capsys):
        tool = _load_tool()
        rc = tool.main(["--quiet"])
        assert rc == 0
        out = capsys.readouterr().out
        # Quiet mode must not emit the "Checked N modules" summary line
        # nor any per-grandfathered WARN line.
        assert "Checked" not in out
        assert "WARN" not in out


class TestMainOnSyntheticDrift:
    """Drive ``main()`` against a tmp_path with a synthetic forgelm/ tree.

    The ``--repo-root`` knob lets the guard scan a tmp tree, so tests
    can fabricate a NEW (non-grandfathered) module that exceeds either
    the warn or fail threshold and verify the exit code.
    """

    def _make_synthetic_repo(self, tmp_path: Path, *, target_loc: int, name: str) -> Path:
        forgelm_dir = tmp_path / "forgelm"
        forgelm_dir.mkdir()
        # ``target_loc`` non-blank, non-comment lines.  Pad with a
        # trivial expression statement so each line is exactly 1 code
        # line under the metric.
        body = "\n".join(["x = 1"] * target_loc) + "\n"
        (forgelm_dir / name).write_text(body, encoding="utf-8")
        return tmp_path

    def test_new_over_fail_module_is_fatal_in_default_mode(self, tmp_path: Path, capsys):
        tool = _load_tool()
        repo = self._make_synthetic_repo(tmp_path, target_loc=1600, name="big_new_module.py")
        rc = tool.main(["--repo-root", str(repo)])
        assert rc == 1
        err = capsys.readouterr().err
        assert "FAIL" in err
        assert "big_new_module.py" in err

    def test_new_over_warn_module_is_advisory_in_default_mode(self, tmp_path: Path, capsys):
        tool = _load_tool()
        repo = self._make_synthetic_repo(tmp_path, target_loc=1100, name="medium_new_module.py")
        rc = tool.main(["--repo-root", str(repo)])
        # 1100 > warn (1000) but ≤ fail (1500), and not grandfathered:
        # advisory only — exit 0 — but still surfaces a WARN line.
        assert rc == 0
        captured = capsys.readouterr()
        assert "WARN" in captured.out
        assert "medium_new_module.py" in captured.out

    def test_new_over_warn_module_is_fatal_under_strict(self, tmp_path: Path, capsys):
        tool = _load_tool()
        repo = self._make_synthetic_repo(tmp_path, target_loc=1100, name="medium_new_module.py")
        rc = tool.main(["--repo-root", str(repo), "--strict"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "FAIL" in err
        assert "medium_new_module.py" in err

    def test_synthetic_under_threshold_is_clean(self, tmp_path: Path, capsys):
        tool = _load_tool()
        repo = self._make_synthetic_repo(tmp_path, target_loc=500, name="small_module.py")
        rc = tool.main(["--repo-root", str(repo), "--strict"])
        assert rc == 0

    def test_missing_forgelm_root_exits_one(self, tmp_path: Path, capsys):
        tool = _load_tool()
        # tmp_path has no forgelm/ subdir → guard reports and exits 1.
        rc = tool.main(["--repo-root", str(tmp_path)])
        assert rc == 1
        err = capsys.readouterr().err
        assert "forgelm/" in err


# ---------------------------------------------------------------------------
# §4 — Module walker: cache exclusion + sort stability
# ---------------------------------------------------------------------------


class TestWalkForgelm:
    def test_skips_pycache_directories(self, tmp_path: Path):
        tool = _load_tool()
        forgelm = tmp_path / "forgelm"
        (forgelm / "__pycache__").mkdir(parents=True)
        (forgelm / "__pycache__" / "stale.cpython-312.py").write_text("x = 1\n", encoding="utf-8")
        (forgelm / "real.py").write_text("y = 2\n", encoding="utf-8")
        result = tool._walk_forgelm(forgelm)
        names = [p.name for p in result]
        assert "real.py" in names
        assert "stale.cpython-312.py" not in names

    def test_returns_sorted_paths(self, tmp_path: Path):
        tool = _load_tool()
        forgelm = tmp_path / "forgelm"
        forgelm.mkdir()
        for name in ["zeta.py", "alpha.py", "mu.py"]:
            (forgelm / name).write_text("z = 0\n", encoding="utf-8")
        result = [p.name for p in tool._walk_forgelm(forgelm)]
        assert result == sorted(result)


# ---------------------------------------------------------------------------
# §5 — Coupling sanity: the seven grandfathered modules really exist
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rel_path",
    [
        "forgelm/compliance.py",
        "forgelm/trainer.py",
        "forgelm/ingestion.py",
        "forgelm/cli/subcommands/_purge.py",
        "forgelm/config.py",
        "forgelm/cli/_parser.py",
        "forgelm/cli/subcommands/_doctor.py",
        # Phase 14 (v0.7.0): pipeline orchestrator — must stay in
        # lockstep with ``_GRANDFATHERED_OVER_CEILING`` in
        # ``tools/check_module_size.py``.
        "forgelm/cli/_pipeline.py",
        # v0.9.1: safety.py (generation-based Llama-Guard scoring).
        "forgelm/safety.py",
    ],
)
def test_grandfathered_module_exists_in_tree(rel_path: str):
    """Each grandfathered entry must point at a real source file.

    If a future split removes one of these files, the guard's
    grandfathered set must be updated in the same commit; this test
    is the canary that flags the inconsistency.
    """
    assert (_REPO_ROOT / rel_path).is_file(), (
        f"grandfathered entry {rel_path!r} does not exist; update _GRANDFATHERED_OVER_CEILING when a split lands."
    )
