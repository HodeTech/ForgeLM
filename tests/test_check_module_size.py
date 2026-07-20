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
2. ``_DEFERRED_SPLITS`` captures the seven modules re-tracked on
   2026-07-20, each pinned to a measured LOC budget.
3. ``main()`` exits 0 in default mode at HEAD because every
   over-threshold module is deferred and within its budget.
4. ``main()`` exits 0 in ``--strict`` mode at HEAD for the same
   reason.
5. Synthetic NEW drift in a non-deferred file triggers a fatal
   exit (1) in default mode when over the fail-threshold, and in
   strict mode when over the warn-threshold.
6. **The budget ratchet.** A deferred *file* that grows past its
   recorded budget is fatal in every mode — this is the contract
   that replaced the old "defer to v0.6.x" WARN-only labelling,
   under which a module could drift from 1038 to 2147 LOC emitting
   nothing fatal.  A regression here would silently restore that.
7. **The scope of that ratchet is a file, not a concern.** A new
   under-ceiling sibling module is NEW drift held to the normal
   thresholds, never charged against the parent's budget.  Pinned
   so the guard's documented claim and its enforced invariant stay
   the same sentence.
8. **The ``budget_history`` contract.** Raising a budget above the
   entry's immutable ``deferred_at_loc`` without a written
   justification is fatal in every mode.
9. A deferred entry pointing at a missing file is fatal; an entry
   whose module fell back under the ceiling is stale and fatal
   under ``--strict``, with the 900-LOC hysteresis boundary pinned
   on both sides.
10. No deferral in the guard names a target version — the rot
    pattern this re-tracking removed.
"""

from __future__ import annotations

import importlib.util
import re
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
# §2 — _DEFERRED_SPLITS: the 2026-07-20 re-tracked backlog
# ---------------------------------------------------------------------------


class TestDeferredSplits:
    def test_contains_expected_modules(self):
        tool = _load_tool()
        assert len(tool._DEFERRED_SPLITS) == 8

    def test_contains_expected_paths(self):
        tool = _load_tool()
        expected = {
            "forgelm/compliance.py",
            "forgelm/trainer.py",
            "forgelm/ingestion.py",
            "forgelm/cli/subcommands/_purge.py",
            "forgelm/config.py",
            "forgelm/cli/_parser.py",
            "forgelm/cli/_pipeline.py",
            "forgelm/verify.py",
            # NOTE: ``forgelm/safety.py`` (split into the ``forgelm/safety/``
            # sub-package) and ``forgelm/cli/subcommands/_doctor.py`` (trimmed
            # to 950 LOC, back under the ceiling) are deliberately absent.
        }
        assert set(tool._DEFERRED_SPLITS) == expected

    def test_uses_posix_separators(self):
        # Cross-platform stability: the path keys must match the
        # POSIX form returned by ``Path.relative_to(...).as_posix()``.
        tool = _load_tool()
        for p in tool._DEFERRED_SPLITS:
            assert "\\" not in p
            assert p.startswith("forgelm/")

    def test_every_entry_has_a_budget_and_a_reason(self):
        # The reason is what makes this an actionable backlog rather
        # than an exemption list; a budget-only entry would be the old
        # frozenset with extra steps.
        tool = _load_tool()
        for path, entry in tool._DEFERRED_SPLITS.items():
            assert entry.budget > tool._WARN_THRESHOLD, path
            assert len(entry.reason) > 40, path

    def test_no_entry_names_a_target_version(self):
        """The rot pattern this re-tracking removed.

        Every deferral used to be labelled "defer to v0.6.x split";
        that label was still printing at v0.9.1, three minors after the
        named cycle closed.  A budget makes no prediction and so cannot
        go stale — reintroducing a version literal here would restore
        the exact failure mode.
        """
        tool = _load_tool()
        version_pattern = re.compile(r"v\d+\.\d+\.?[\dx]*")
        for path, entry in tool._DEFERRED_SPLITS.items():
            assert not version_pattern.search(entry.reason), (
                f"{path} deferral names a version; record a budget and an entry in "
                f"docs/roadmap/risks-and-decisions.md instead — version literals rot."
            )

    def test_budgets_match_measured_loc_at_head(self):
        """Budgets are the real measurement, not a padded allowance.

        A budget set above the module's actual size would hand out
        silent growth headroom, which is precisely what the WARN-only
        policy did implicitly.
        """
        tool = _load_tool()
        for path, entry in tool._DEFERRED_SPLITS.items():
            measured = tool._count_code_lines(_REPO_ROOT / path)
            assert measured <= entry.budget, (
                f"{path} is {measured} LOC, over its {entry.budget} budget; "
                f"land the split or raise the budget with a budget_history note."
            )


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
    can fabricate a NEW (non-deferred) module that exceeds either
    the warn or fail threshold and verify the exit code.

    ``_DEFERRED_SPLITS`` is emptied for these tests: a synthetic tree
    contains none of the real deferred modules, so leaving the list
    populated would trip the dangling-entry check and mask the exit
    code actually under test.
    """

    @pytest.fixture
    def tool(self, monkeypatch):
        """The guard with an empty deferred list.

        ``_load_tool()`` re-executes the module on every call, so the
        patch must be applied to the very object the test drives —
        patching a separately-loaded copy would silently no-op.
        """
        loaded = _load_tool()
        monkeypatch.setattr(loaded, "_DEFERRED_SPLITS", {})
        return loaded

    def _make_synthetic_repo(self, tmp_path: Path, *, target_loc: int, name: str) -> Path:
        forgelm_dir = tmp_path / "forgelm"
        forgelm_dir.mkdir()
        # ``target_loc`` non-blank, non-comment lines.  Pad with a
        # trivial expression statement so each line is exactly 1 code
        # line under the metric.
        body = "\n".join(["x = 1"] * target_loc) + "\n"
        (forgelm_dir / name).write_text(body, encoding="utf-8")
        return tmp_path

    def test_new_over_fail_module_is_fatal_in_default_mode(self, tool, tmp_path: Path, capsys):
        repo = self._make_synthetic_repo(tmp_path, target_loc=1600, name="big_new_module.py")
        rc = tool.main(["--repo-root", str(repo)])
        assert rc == 1
        err = capsys.readouterr().err
        assert "FAIL" in err
        assert "big_new_module.py" in err

    def test_new_over_warn_module_is_advisory_in_default_mode(self, tool, tmp_path: Path, capsys):
        repo = self._make_synthetic_repo(tmp_path, target_loc=1100, name="medium_new_module.py")
        rc = tool.main(["--repo-root", str(repo)])
        # 1100 > warn (1000) but ≤ fail (1500), and not grandfathered:
        # advisory only — exit 0 — but still surfaces a WARN line.
        assert rc == 0
        captured = capsys.readouterr()
        assert "WARN" in captured.out
        assert "medium_new_module.py" in captured.out

    def test_new_over_warn_module_is_fatal_under_strict(self, tool, tmp_path: Path, capsys):
        repo = self._make_synthetic_repo(tmp_path, target_loc=1100, name="medium_new_module.py")
        rc = tool.main(["--repo-root", str(repo), "--strict"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "FAIL" in err
        assert "medium_new_module.py" in err

    def test_synthetic_under_threshold_is_clean(self, tool, tmp_path: Path, capsys):
        repo = self._make_synthetic_repo(tmp_path, target_loc=500, name="small_module.py")
        rc = tool.main(["--repo-root", str(repo), "--strict"])
        assert rc == 0

    def test_missing_forgelm_root_exits_one(self, tool, tmp_path: Path, capsys):
        # tmp_path has no forgelm/ subdir → guard reports and exits 1.
        rc = tool.main(["--repo-root", str(tmp_path)])
        assert rc == 1
        err = capsys.readouterr().err
        assert "forgelm/" in err


# ---------------------------------------------------------------------------
# §3b — The budget ratchet: deferred modules may not GROW
# ---------------------------------------------------------------------------


class TestDeferredBudgetRatchet:
    """The contract that replaced "defer to v0.6.x split".

    Under the old policy a deferred module emitted an unconditional
    WARN at any size, so ``compliance.py`` drifted 1502 → 2147 LOC
    across three minor releases without a single fatal signal.  These
    tests pin the replacement: over-budget growth is fatal in every
    mode, and the escape hatch is an explicit budget raise.
    """

    def _repo_with(self, tmp_path: Path, rel_path: str, loc: int) -> Path:
        target = tmp_path / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(["x = 1"] * loc) + "\n", encoding="utf-8")
        return tmp_path

    def _only_entry(
        self,
        tool,
        monkeypatch,
        rel_path: str,
        budget: int,
        *,
        deferred_at_loc: int | None = None,
        budget_history: tuple[str, ...] = (),
    ) -> None:
        """Narrow _DEFERRED_SPLITS to one synthetic entry for the test.

        ``deferred_at_loc`` defaults to ``budget`` — i.e. "deferred at
        this size, never raised" — so ratchet tests that do not care
        about the budget_history contract stay unaffected by it.
        """
        monkeypatch.setattr(
            tool,
            "_DEFERRED_SPLITS",
            {
                rel_path: tool._DeferredSplit(
                    budget=budget,
                    deferred_at_loc=budget if deferred_at_loc is None else deferred_at_loc,
                    reason="synthetic entry for tests",
                    budget_history=budget_history,
                )
            },
        )

    def test_growth_past_budget_is_fatal_in_default_mode(self, tmp_path: Path, monkeypatch, capsys):
        tool = _load_tool()
        rel = "forgelm/deferred_module.py"
        self._only_entry(tool, monkeypatch, rel, budget=1200)
        repo = self._repo_with(tmp_path, rel, loc=1201)
        rc = tool.main(["--repo-root", str(repo)])
        assert rc == 1, "one line of growth past budget must be fatal without --strict"
        err = capsys.readouterr().err
        assert "over its deferred-split budget" in err
        assert "1201" in err

    def test_growth_past_budget_is_fatal_in_strict_mode(self, tmp_path: Path, monkeypatch, capsys):
        tool = _load_tool()
        rel = "forgelm/deferred_module.py"
        self._only_entry(tool, monkeypatch, rel, budget=1200)
        repo = self._repo_with(tmp_path, rel, loc=1600)
        rc = tool.main(["--repo-root", str(repo), "--strict"])
        assert rc == 1
        assert "over its deferred-split budget" in capsys.readouterr().err

    def test_growth_past_budget_is_fatal_even_when_quiet(self, tmp_path: Path, monkeypatch, capsys):
        # --quiet suppresses advisory output, never a fatal verdict.
        tool = _load_tool()
        rel = "forgelm/deferred_module.py"
        self._only_entry(tool, monkeypatch, rel, budget=1200)
        repo = self._repo_with(tmp_path, rel, loc=1300)
        rc = tool.main(["--repo-root", str(repo), "--quiet"])
        assert rc == 1

    def test_at_budget_exactly_is_allowed(self, tmp_path: Path, monkeypatch, capsys):
        tool = _load_tool()
        rel = "forgelm/deferred_module.py"
        self._only_entry(tool, monkeypatch, rel, budget=1200)
        repo = self._repo_with(tmp_path, rel, loc=1200)
        rc = tool.main(["--repo-root", str(repo)])
        assert rc == 0
        assert "0 LOC headroom" in capsys.readouterr().out

    def test_raised_budget_is_the_escape_hatch(self, tmp_path: Path, monkeypatch, capsys):
        # The same 1300-LOC module that fails at budget 1200 passes once
        # the budget literal is explicitly raised — the escape hatch is a
        # reviewable line in a diff, not an implicit allowance.
        tool = _load_tool()
        rel = "forgelm/deferred_module.py"
        repo = self._repo_with(tmp_path, rel, loc=1300)
        self._only_entry(tool, monkeypatch, rel, budget=1200)
        assert tool.main(["--repo-root", str(repo)]) == 1
        self._only_entry(tool, monkeypatch, rel, budget=1300)
        assert tool.main(["--repo-root", str(repo)]) == 0

    def test_shrinking_below_budget_reports_headroom(self, tmp_path: Path, monkeypatch, capsys):
        """Pins ``headroom = budget - loc`` exactly, sign included.

        The assertion is a full-line equality rather than a substring
        check: ``"150 LOC headroom" in out`` also matches ``"-150 LOC
        headroom"``, so a sign-flipped ``measured.loc - entry.budget``
        survived this test until 2026-07-20.  A reversed-operand bug is
        the most likely mutation of this expression, which made it the
        one mutant the test most needed to catch.
        """
        tool = _load_tool()
        rel = "forgelm/deferred_module.py"
        self._only_entry(tool, monkeypatch, rel, budget=1200)
        repo = self._repo_with(tmp_path, rel, loc=1050)
        rc = tool.main(["--repo-root", str(repo)])
        assert rc == 0
        out = capsys.readouterr().out
        warn_lines = [ln for ln in out.splitlines() if ln.startswith("WARN:")]
        assert warn_lines == [
            f"WARN: {rel} = 1050 LOC (deferred split, budget 1200, 150 LOC headroom); synthetic entry for tests"
        ], out

    @pytest.mark.parametrize(
        ("loc", "budget", "expected_headroom"),
        [(1050, 1200, 150), (1100, 1200, 100), (1200, 1200, 0), (1001, 1500, 499)],
    )
    def test_headroom_arithmetic_across_several_points(
        self, tmp_path: Path, monkeypatch, capsys, loc: int, budget: int, expected_headroom: int
    ):
        # A single data point can be satisfied by a constant; several
        # cannot.  Each case also stays above the 900 stale threshold so
        # the WARN branch (not the STALE branch) is the one exercised.
        tool = _load_tool()
        rel = "forgelm/deferred_module.py"
        self._only_entry(tool, monkeypatch, rel, budget=budget)
        repo = self._repo_with(tmp_path, rel, loc=loc)
        assert tool.main(["--repo-root", str(repo)]) == 0
        out = capsys.readouterr().out
        assert f"budget {budget}, {expected_headroom} LOC headroom" in out, out

    def test_dangling_entry_is_fatal_in_every_mode(self, tmp_path: Path, monkeypatch, capsys):
        # A split landed (or a file moved) without updating the list.
        tool = _load_tool()
        self._only_entry(tool, monkeypatch, "forgelm/gone.py", budget=1200)
        (tmp_path / "forgelm").mkdir()
        (tmp_path / "forgelm" / "present.py").write_text("x = 1\n", encoding="utf-8")
        rc = tool.main(["--repo-root", str(tmp_path), "--quiet"])
        assert rc == 1
        assert "does not exist" in capsys.readouterr().err

    def test_stale_entry_is_advisory_by_default_and_fatal_under_strict(self, tmp_path: Path, monkeypatch, capsys):
        # Module paid off its debt: 800 LOC is below the 900 hysteresis
        # threshold, so the entry should be deleted.
        tool = _load_tool()
        rel = "forgelm/deferred_module.py"
        self._only_entry(tool, monkeypatch, rel, budget=1200)
        repo = self._repo_with(tmp_path, rel, loc=800)
        assert tool.main(["--repo-root", str(repo)]) == 0
        assert "STALE" in capsys.readouterr().out
        assert tool.main(["--repo-root", str(repo), "--strict"]) == 1
        assert "back under the ceiling" in capsys.readouterr().err

    def test_hysteresis_band_does_not_flap(self, tmp_path: Path, monkeypatch, capsys):
        # 950 LOC is under the 1000 ceiling but above the 900 stale
        # threshold: neither a size warning nor a stale report, so a
        # module oscillating around the ceiling cannot flip the build
        # between "over ceiling" and "delete this entry" on alternate
        # commits.
        tool = _load_tool()
        rel = "forgelm/deferred_module.py"
        self._only_entry(tool, monkeypatch, rel, budget=1200)
        repo = self._repo_with(tmp_path, rel, loc=950)
        assert tool.main(["--repo-root", str(repo), "--strict"]) == 0
        out = capsys.readouterr().out
        assert "STALE" not in out

    def test_stale_threshold_boundary_is_inclusive(self, tmp_path: Path, monkeypatch, capsys):
        """900 LOC — exactly ``_STALE_THRESHOLD`` — counts as stale.

        The band was previously probed only at 950 (not stale) and 800
        (stale), so ``measured.loc <= _STALE_THRESHOLD`` could be
        mutated to ``<`` and every test still passed.  The boundary is
        the whole point of a hysteresis constant, so it is pinned on
        both sides.
        """
        tool = _load_tool()
        rel = "forgelm/deferred_module.py"
        assert tool._STALE_THRESHOLD == 900
        self._only_entry(tool, monkeypatch, rel, budget=1200)
        repo = self._repo_with(tmp_path, rel, loc=tool._STALE_THRESHOLD)
        assert tool.main(["--repo-root", str(repo)]) == 0
        assert "STALE" in capsys.readouterr().out
        assert tool.main(["--repo-root", str(repo), "--strict"]) == 1
        assert "back under the ceiling" in capsys.readouterr().err

    def test_one_line_above_stale_threshold_is_not_stale(self, tmp_path: Path, monkeypatch, capsys):
        # The other side of the same boundary: 901 must stay a plain
        # WARN, so a threshold shifted up by one is caught too.
        tool = _load_tool()
        rel = "forgelm/deferred_module.py"
        self._only_entry(tool, monkeypatch, rel, budget=1200)
        repo = self._repo_with(tmp_path, rel, loc=tool._STALE_THRESHOLD + 1)
        assert tool.main(["--repo-root", str(repo), "--strict"]) == 0
        out = capsys.readouterr().out
        assert "STALE" not in out
        assert "299 LOC headroom" in out

    def test_deferred_module_is_excluded_from_band_classification(self, tmp_path: Path, monkeypatch, capsys):
        # A deferred module must not also be counted as NEW drift; it is
        # scored against its budget only.
        tool = _load_tool()
        rel = "forgelm/deferred_module.py"
        self._only_entry(tool, monkeypatch, rel, budget=1800)
        repo = self._repo_with(tmp_path, rel, loc=1700)
        rc = tool.main(["--repo-root", str(repo), "--strict"])
        assert rc == 0
        captured = capsys.readouterr()
        # 1700 LOC is over the 1500 fail-threshold, but the module is
        # deferred, so it must be scored against its 1800 budget and
        # counted in neither NEW band.
        assert "0 NEW over fail-threshold" in captured.out
        assert "0 NEW over warn-threshold" in captured.out
        assert "FAIL" not in captured.err
        assert "deferred split, budget 1800" in captured.out


# ---------------------------------------------------------------------------
# §3c — The budget_history contract: a raise must state its reason
# ---------------------------------------------------------------------------


class TestBudgetHistoryEnforcement:
    """``budget_history`` was documented as required and checked by nothing.

    The module docstring promised that raising a budget "makes every
    grant of extra headroom a reviewed line in a diff with a stated
    justification" — but the guard never looked at ``budget_history``,
    so a bare budget bump passed silently.  A documented requirement
    that nothing enforces is the exact failure mode this guard's own
    re-tracking exists to correct, so it is now enforced via the
    immutable ``deferred_at_loc`` field.
    """

    def _entry(self, tool, **kwargs):
        base = {"budget": 1200, "deferred_at_loc": 1200, "reason": "synthetic entry for tests"}
        base.update(kwargs)
        return {"forgelm/deferred_module.py": tool._DeferredSplit(**base)}

    def test_unraised_budget_needs_no_history(self):
        tool = _load_tool()
        assert tool._validate_entries(self._entry(tool)) is False

    def test_raised_budget_without_history_is_fatal(self, capsys):
        tool = _load_tool()
        fatal = tool._validate_entries(self._entry(tool, budget=1300, deferred_at_loc=1200))
        assert fatal is True
        err = capsys.readouterr().err
        assert "empty budget_history" in err
        assert "1300" in err and "1200" in err

    def test_raised_budget_with_history_passes(self, capsys):
        tool = _load_tool()
        entries = self._entry(
            tool,
            budget=1300,
            deferred_at_loc=1200,
            budget_history=("2026-08-01: +100 for the CVE-2026-1234 input-validation fix.",),
        )
        assert tool._validate_entries(entries) is False
        assert capsys.readouterr().err == ""

    def test_blank_history_note_is_fatal(self, capsys):
        # An empty string would otherwise satisfy "has a history entry"
        # while stating no justification at all.
        tool = _load_tool()
        fatal = tool._validate_entries(self._entry(tool, budget=1300, deferred_at_loc=1200, budget_history=("   ",)))
        assert fatal is True
        assert "blank budget_history note" in capsys.readouterr().err

    def test_lowered_budget_needs_no_history(self, capsys):
        # Trimming a module and lowering its budget is the good case;
        # demanding ceremony for it would tax the desired behaviour.
        tool = _load_tool()
        assert tool._validate_entries(self._entry(tool, budget=1100, deferred_at_loc=1200)) is False
        assert capsys.readouterr().err == ""

    def test_unjustified_raise_is_fatal_through_main_in_every_mode(self, tmp_path: Path, monkeypatch, capsys):
        # End-to-end: the entry is otherwise perfectly healthy — the
        # module is well under its budget — and the run is still fatal,
        # including under --quiet.
        tool = _load_tool()
        rel = "forgelm/deferred_module.py"
        monkeypatch.setattr(tool, "_DEFERRED_SPLITS", self._entry(tool, budget=1300, deferred_at_loc=1200))
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(["x = 1"] * 1050) + "\n", encoding="utf-8")
        for extra in ([], ["--strict"], ["--quiet"]):
            assert tool.main(["--repo-root", str(tmp_path), *extra]) == 1, extra
            assert "empty budget_history" in capsys.readouterr().err, extra

    def test_head_entries_satisfy_the_contract(self):
        # The shipped list must itself pass the rule it documents.
        tool = _load_tool()
        assert tool._validate_entries(tool._DEFERRED_SPLITS) is False

    def test_head_entries_record_deferral_measurement(self):
        """``deferred_at_loc`` is a measurement, not a padded allowance.

        None of the seven has been raised yet, so each must still equal
        its budget; a divergence appearing without a ``budget_history``
        note is what :func:`_validate_entries` catches.
        """
        tool = _load_tool()
        for path, entry in tool._DEFERRED_SPLITS.items():
            assert entry.deferred_at_loc > 0, path
            if not entry.budget_history:
                assert entry.budget == entry.deferred_at_loc, path


# ---------------------------------------------------------------------------
# §3d — Scope of the ratchet: per FILE, not per concern
# ---------------------------------------------------------------------------


def test_new_sibling_file_is_not_charged_against_a_deferred_budget(tmp_path: Path, monkeypatch, capsys):
    """Pins the documented limit of the ratchet, so the claim stays true.

    The guard's unit of enforcement is a file; the ``reason`` field's
    unit of concern is not.  Moving 800 LOC of ``trainer.py`` concern
    into a new ``_trainer_overflow.py`` sibling is therefore clean —
    no FAIL, no WARN, "0 NEW over warn-threshold".

    This is deliberate: every ``reason`` names the sibling files a
    split should produce, so charging new siblings against the parent's
    budget would penalise the exact refactor the backlog asks for.  The
    test exists because the docstring used to claim the broader
    invariant ("a deferred module may not grow") that the guard does
    not check — if that scope ever does change, this test is the line
    that has to change with it.
    """
    tool = _load_tool()
    monkeypatch.setattr(
        tool,
        "_DEFERRED_SPLITS",
        {
            "forgelm/trainer.py": tool._DeferredSplit(
                budget=1432, deferred_at_loc=1432, reason="synthetic entry for tests"
            )
        },
    )
    forgelm = tmp_path / "forgelm"
    forgelm.mkdir()
    (forgelm / "trainer.py").write_text("\n".join(["x = 1"] * 1432) + "\n", encoding="utf-8")
    (forgelm / "_trainer_overflow.py").write_text("\n".join(["x = 1"] * 803) + "\n", encoding="utf-8")

    assert tool.main(["--repo-root", str(tmp_path), "--strict"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "0 NEW over warn-threshold" in captured.out
    assert "_trainer_overflow" not in captured.out


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
# §5 — Coupling sanity: the seven deferred modules really exist
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
        "forgelm/cli/_pipeline.py",
    ],
)
def test_deferred_module_exists_in_tree(rel_path: str):
    """Each deferred entry must point at a real source file.

    If a future split removes one of these files, ``_DEFERRED_SPLITS``
    must be updated in the same commit; this test is the canary that
    flags the inconsistency.  ``main()`` enforces the same rule at
    runtime, but the failure message here names the file directly.
    """
    assert (_REPO_ROOT / rel_path).is_file(), (
        f"deferred entry {rel_path!r} does not exist; update _DEFERRED_SPLITS when a split lands."
    )


def test_split_modules_are_no_longer_deferred():
    """``forgelm/safety/`` and ``_doctor.py`` must stay off the list.

    Both were carried as over-ceiling entries after they had stopped
    being over-ceiling — ``safety.py`` because the split landed, and
    ``_doctor.py`` because it was trimmed to 950 LOC and nobody
    re-measured.  Re-adding either without a real size problem would
    resume paying interest on a settled debt.
    """
    tool = _load_tool()
    assert "forgelm/safety.py" not in tool._DEFERRED_SPLITS
    assert not (_REPO_ROOT / "forgelm" / "safety.py").is_file()
    assert "forgelm/cli/subcommands/_doctor.py" not in tool._DEFERRED_SPLITS


def test_emitted_output_never_names_a_target_version(capsys):
    """No emitted line promises a version-numbered split.

    Scoped to *output* rather than source text: the module docstring
    deliberately recounts the "defer to v0.6.x" history so the reason
    for the budget design survives, and that prose must not be
    mistaken for a live promise.  What matters is that a developer
    reading a size warning is never told the split is coming in some
    named release — that sentence was true once and then quietly was
    not, for three minor releases.
    """
    tool = _load_tool()
    tool.main([])
    captured = capsys.readouterr()
    emitted = captured.out + captured.err
    assert emitted.strip(), "guard produced no output to inspect"
    assert not re.search(r"v\d+\.\d+\.?[\dx]*", emitted), (
        f"guard output names a version target; deferrals carry budgets, not due dates: {emitted!r}"
    )
