"""Wave 5 / Faz 30 Task J — tools/check_cli_help_consistency.py regression tests.

The CLI/doc help-consistency checker is the mechanical guard that
catches the drift class fixed three times this Wave (GH-008, GH-011,
GH-016, GH-018, GH-020, F-W4-01, F-W4-PS-02).  A regression that
silently under-reports drift would let the same class of bug slip
through future PRs.

Pinned contracts:

1. Real, valid invocation → exits 0 in both modes.
2. Doc citing ghost flags on a real subcommand → exits 1 strict;
   every ghost flag flagged.
3. Doc citing a non-existent subcommand → exits 1 strict.
4. Doc citing an invalid choice on a real flag → exits 1 strict.
5. Forward-reference whitelist (``planned`` etc. in ±3-line window)
   → exits 0 even on otherwise-flaggable invocations.
6. Anti-pattern preface (``Wrong:`` etc. on previous prose line)
   → exits 0 even on otherwise-flaggable invocations.
7. ``--quiet`` suppresses the OK summary.

All fixtures run in ``tmp_path`` against the LIVE installed
``forgelm`` parser surface (subprocess discovery), per the task
prompt — we do not mock the parser, the whole point is to verify
the live surface end-to-end.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TOOLS = _REPO_ROOT / "tools"
_TOOL_PATH = _TOOLS / "check_cli_help_consistency.py"


def _load_tool_module() -> object:
    """Load the guard module from path without leaking tools/ onto sys.path."""
    spec = importlib.util.spec_from_file_location("check_cli_help_consistency", _TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_cli_help_consistency"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def tool() -> object:
    return _load_tool_module()


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# §1 — Happy path: real invocations pass
# ---------------------------------------------------------------------------


class TestValidInvocations:
    def test_real_verify_audit_invocation_clean(self, tmp_path: Path, tool, capsys):
        scope = tmp_path / "docs"
        _write(
            scope / "guide.md",
            (
                "# Guide\n\n"
                "Verify the audit chain:\n\n"
                "```bash\n"
                "forgelm verify-audit ./outputs/audit_log.jsonl --require-hmac\n"
                "```\n"
            ),
        )
        rc = tool.main(["--repo-root", str(tmp_path), "--scope", "docs"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "OK" in out

    def test_real_deploy_invocation_clean(self, tmp_path: Path, tool, capsys):
        scope = tmp_path / "docs"
        _write(
            scope / "guide.md",
            ("# Deploy\n\n```bash\nforgelm deploy ./model --target ollama --output ./Modelfile\n```\n"),
        )
        rc = tool.main(["--repo-root", str(tmp_path), "--scope", "docs", "--strict"])
        assert rc == 0


# ---------------------------------------------------------------------------
# §2 — Drift detection: ghost flags on a real subcommand
# ---------------------------------------------------------------------------


class TestGhostFlagDrift:
    def test_verify_audit_ghost_flags_strict_fails(self, tmp_path: Path, tool, capsys):
        # F-W4-01 regression: the doc cited --output-dir / --json on
        # verify-audit, which has neither.  Must fail strict and flag
        # both bad tokens.
        scope = tmp_path / "docs"
        _write(
            scope / "guide.md",
            ("# Guide\n\n```bash\nforgelm verify-audit ./outputs/audit_log.jsonl --output-dir ./outputs --json\n```\n"),
        )
        rc = tool.main(["--repo-root", str(tmp_path), "--scope", "docs", "--strict"])
        assert rc == 1
        out = capsys.readouterr().out
        assert "--output-dir" in out
        assert "--json" in out
        assert "verify-audit" in out

    def test_chat_ghost_top_p_strict_fails(self, tmp_path: Path, tool, capsys):
        # GH-019 regression: ``--top-p`` is not in chat parser.
        scope = tmp_path / "docs"
        _write(
            scope / "guide.md",
            ("# Chat\n\n```bash\nforgelm chat ./model --top-p 0.9\n```\n"),
        )
        rc = tool.main(["--repo-root", str(tmp_path), "--scope", "docs", "--strict"])
        assert rc == 1
        out = capsys.readouterr().out
        assert "--top-p" in out

    def test_advisory_mode_reports_but_exits_zero(self, tmp_path: Path, tool, capsys):
        scope = tmp_path / "docs"
        _write(
            scope / "guide.md",
            ("```bash\nforgelm chat ./model --top-p 0.9\n```\n"),
        )
        rc = tool.main(["--repo-root", str(tmp_path), "--scope", "docs"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "WARN" in out
        assert "--top-p" in out


# ---------------------------------------------------------------------------
# §3 — Subcommand-level drift
# ---------------------------------------------------------------------------


class TestSubcommandDrift:
    def test_unknown_subcommand_strict_fails(self, tmp_path: Path, tool, capsys):
        # GH-011 regression: ``benchmark`` is NOT a registered
        # subcommand (benchmark-only is a top-level flag, not a
        # subcommand).
        scope = tmp_path / "docs"
        _write(
            scope / "guide.md",
            ("```bash\nforgelm benchmark --model meta-llama/Llama-3-8B\n```\n"),
        )
        rc = tool.main(["--repo-root", str(tmp_path), "--scope", "docs", "--strict"])
        assert rc == 1
        out = capsys.readouterr().out
        assert "benchmark" in out
        assert "subcommand" in out


# ---------------------------------------------------------------------------
# §4 — Choice-set drift
# ---------------------------------------------------------------------------


class TestChoiceDrift:
    def test_deploy_target_kserve_strict_fails(self, tmp_path: Path, tool, capsys):
        # GH-018 regression: --target choices are
        # {ollama,vllm,tgi,hf-endpoints}; ``kserve`` is invalid.
        scope = tmp_path / "docs"
        _write(
            scope / "guide.md",
            ("```bash\nforgelm deploy ./model --target kserve\n```\n"),
        )
        rc = tool.main(["--repo-root", str(tmp_path), "--scope", "docs", "--strict"])
        assert rc == 1
        out = capsys.readouterr().out
        assert "kserve" in out
        assert "choices" in out

    def test_shell_variable_reference_skips_choice_validation(self, tmp_path: Path, tool, capsys):
        # ``--quant "${q}"`` inside a `for q in ...` loop is a real
        # documentation pattern (multi-quant export). The value is
        # operator-supplied at runtime; treating it as drift would
        # force docs to drop the educational loop form.
        scope = tmp_path / "docs"
        _write(
            scope / "guide.md",
            (
                "```bash\n"
                "for q in q4_k_m q5_k_m q8_0; do\n"
                '  forgelm export ./model --output ./out/${q}.gguf --quant "${q}"\n'
                "done\n"
                "```\n"
            ),
        )
        rc = tool.main(["--repo-root", str(tmp_path), "--scope", "docs", "--strict"])
        assert rc == 0, "shell variable references must not trigger choice drift"

    def test_multiline_continuation_logical_line_stitching(self, tmp_path: Path, tool, capsys):
        # gemini-code-assist HIGH absorption regression: a multi-line
        # ``\``-continued command must be evaluated as one logical
        # invocation so flags on the continuation lines are caught.
        scope = tmp_path / "docs"
        _write(
            scope / "guide.md",
            ("```bash\n$ forgelm deploy ./model \\\n    --target kserve \\\n    --output ./out.yaml\n```\n"),
        )
        rc = tool.main(["--repo-root", str(tmp_path), "--scope", "docs", "--strict"])
        assert rc == 1, "multi-line continuation must surface --target kserve as choice drift"
        out = capsys.readouterr().out
        assert "kserve" in out


# ---------------------------------------------------------------------------
# §4b — Prose comma-list claim on single-value choice flags (F-P7-OPUS-13)
# ---------------------------------------------------------------------------


class TestProseChoiceListClaim:
    def test_comma_separate_quant_prose_strict_fails(self, tmp_path: Path, tool, capsys):
        # F-P7-OPUS-13 regression: prose telling operators to
        # "comma-separate --quant" is drift — argparse choices reject
        # the comma form.
        scope = tmp_path / "docs"
        _write(
            scope / "guide.md",
            ("# Export\n\nComma-separate `--quant` for multiple levels in one command.\n"),
        )
        rc = tool.main(["--repo-root", str(tmp_path), "--scope", "docs", "--strict"])
        assert rc == 1
        out = capsys.readouterr().out
        assert "--quant" in out
        assert "single-value choices flag" in out

    def test_comma_separate_quant_prose_tr_strict_fails(self, tmp_path: Path, tool, capsys):
        scope = tmp_path / "docs"
        _write(
            scope / "guide.md",
            ("# Export\n\nTek komutta çoklu seviye için `--quant`'ı virgülle ayırın.\n"),
        )
        rc = tool.main(["--repo-root", str(tmp_path), "--scope", "docs", "--strict"])
        assert rc == 1
        out = capsys.readouterr().out
        assert "--quant" in out

    def test_comma_separate_inside_code_fence_not_flagged(self, tmp_path: Path, tool, capsys):
        # The claim must be PROSE — a comma-list claim mention inside a
        # fenced code block is data, not an operator instruction.
        scope = tmp_path / "docs"
        _write(
            scope / "guide.md",
            ("# Export\n\n```text\n# comma-separate --quant (sample output)\n```\n"),
        )
        rc = tool.main(["--repo-root", str(tmp_path), "--scope", "docs", "--strict"])
        assert rc == 0

    def test_comma_separate_non_choice_flag_not_flagged(self, tmp_path: Path, tool, capsys):
        # ``--tasks`` legitimately takes a comma-separated CSV; it is NOT
        # a choices flag, so a comma-list claim about it is correct.
        scope = tmp_path / "docs"
        _write(
            scope / "guide.md",
            ("# Cache\n\nComma-separate `--tasks` to cache several lm-eval datasets at once.\n"),
        )
        rc = tool.main(["--repo-root", str(tmp_path), "--scope", "docs", "--strict"])
        assert rc == 0


# ---------------------------------------------------------------------------
# §5 — False-positive heuristics
# ---------------------------------------------------------------------------


class TestForwardReferenceWhitelist:
    def test_planned_marker_within_three_lines_skips_block(self, tmp_path: Path, tool, capsys):
        # ``--top-p`` would otherwise be flagged on chat; the
        # forward-reference marker on the immediately-preceding
        # line legitimises the block.
        scope = tmp_path / "docs"
        _write(
            scope / "guide.md",
            ("# Chat\n\n> Note: --top-p is planned for v0.6.0+\n\n```bash\nforgelm chat ./model --top-p 0.9\n```\n"),
        )
        rc = tool.main(["--repo-root", str(tmp_path), "--scope", "docs", "--strict"])
        assert rc == 0

    def test_roadmap_marker_after_block_also_skips(self, tmp_path: Path, tool, capsys):
        # Forward-reference marker on the line immediately after the
        # block (still within ±3) — the heuristic looks at both
        # sides of the window.
        scope = tmp_path / "docs"
        _write(
            scope / "guide.md",
            ("# Chat\n\n```bash\nforgelm chat ./model --top-p 0.9\n```\n\n> See roadmap for the --top-p timeline.\n"),
        )
        rc = tool.main(["--repo-root", str(tmp_path), "--scope", "docs", "--strict"])
        assert rc == 0

    def test_bare_planned_substring_in_unrelated_prose_does_not_skip(self, tmp_path: Path, tool, capsys):
        # F-W5-01 absorption: bare ``planned`` (or ``future`` /
        # ``roadmap``) inside unrelated prose must NOT whitelist
        # surrounding code-block drift.  The marker tuple is anchored
        # to whole-phrase forms for exactly this reason.
        scope = tmp_path / "docs"
        _write(
            scope / "guide.md",
            (
                "# Chat\n\n"
                "Useful when planned attacks are evaluated:\n\n"
                "```bash\nforgelm chat ./model --top-p 0.9\n```\n"
            ),
        )
        rc = tool.main(["--repo-root", str(tmp_path), "--scope", "docs", "--strict"])
        assert rc == 1, "bare 'planned' must NOT whitelist drift"
        out = capsys.readouterr().out
        assert "--top-p" in out

    def test_forward_ref_outside_three_line_window_does_not_skip(self, tmp_path: Path, tool, capsys):
        # F-W5-TR-01 absorption: a forward-reference marker more than
        # ±3 lines from the block must NOT taint that block.  Buries
        # the marker 8 lines before the fence.
        scope = tmp_path / "docs"
        body = (
            "# Chat\n\n"
            "> Note: --top-p is planned for v0.6.0+\n\n" + "\n" * 8 + "```bash\nforgelm chat ./model --top-p 0.9\n```\n"
        )
        _write(scope / "guide.md", body)
        rc = tool.main(["--repo-root", str(tmp_path), "--scope", "docs", "--strict"])
        assert rc == 1, ">±3-line forward-ref window must NOT skip the block"

    def test_planlanan_marker_skips_block(self, tmp_path: Path, tool):
        # Round 5 absorption (test-rigor F-W5R5-02): pin the Turkish
        # bilingual-doc marker `planlanan` so a regression that drops
        # it from `_FORWARD_REF_MARKERS` fails at unit-test runtime
        # rather than only at the live `--strict` gate (where the
        # failure surface is "16 invocations now flagged" — slow to
        # debug).
        scope = tmp_path / "docs"
        _write(
            scope / "rehber-tr.md",
            (
                "# Sohbet\n\n> Not: --top-p planlanan v0.6.0+ özelliği.\n\n"
                "```bash\nforgelm chat ./model --top-p 0.9\n```\n"
            ),
        )
        rc = tool.main(["--repo-root", str(tmp_path), "--scope", "docs", "--strict"])
        assert rc == 0

    def test_yol_haritasi_marker_skips_block(self, tmp_path: Path, tool):
        # TR mirror of the EN `see roadmap` marker.
        scope = tmp_path / "docs"
        _write(
            scope / "rehber-tr.md",
            (
                "# Sohbet\n\n```bash\nforgelm chat ./model --top-p 0.9\n```\n"
                "\n> Not: yol haritası bu bayrağı v0.6.0+ için planlıyor.\n"
            ),
        )
        rc = tool.main(["--repo-root", str(tmp_path), "--scope", "docs", "--strict"])
        assert rc == 0

    def test_ileride_marker_skips_block(self, tmp_path: Path, tool):
        # TR forward-temporal marker.
        scope = tmp_path / "docs"
        _write(
            scope / "rehber-tr.md",
            (
                "# Sohbet\n\n> Not: --top-p ileride v0.6.0+ ile gelecek.\n\n"
                "```bash\nforgelm chat ./model --top-p 0.9\n```\n"
            ),
        )
        rc = tool.main(["--repo-root", str(tmp_path), "--scope", "docs", "--strict"])
        assert rc == 0


class TestAntiPatternWhitelist:
    def test_wrong_tagged_block_skipped(self, tmp_path: Path, tool, capsys):
        scope = tmp_path / "docs"
        _write(
            scope / "guide.md",
            ("# Chat\n\nWrong:\n```bash\nforgelm chat ./model --top-p 0.9\n```\n"),
        )
        rc = tool.main(["--repo-root", str(tmp_path), "--scope", "docs", "--strict"])
        assert rc == 0

    def test_yanlis_tagged_block_skipped(self, tmp_path: Path, tool, capsys):
        # Turkish anti-pattern marker.
        scope = tmp_path / "docs"
        _write(
            scope / "guide.md",
            ("# Sohbet\n\nYanlış:\n```bash\nforgelm chat ./model --top-p 0.9\n```\n"),
        )
        rc = tool.main(["--repo-root", str(tmp_path), "--scope", "docs", "--strict"])
        assert rc == 0

    def test_wrong_block_does_not_taint_subsequent_block(self, tmp_path: Path, tool, capsys):
        # F-W5-TR-01 absorption: a ``Wrong:`` marker scopes to ONE
        # block. The next block in the same doc must still be scanned
        # — a regression to a sticky "wrong-block mode" would silently
        # whitelist every later real invocation.
        #
        # Round 5 absorption (test-rigor F-W5R5-03): also pin that the
        # FIRST block IS skipped — the count-based assertion below
        # would catch a regression that broke the anti-pattern
        # whitelist entirely (every block scanned, --top-p reported
        # twice instead of once).
        scope = tmp_path / "docs"
        _write(
            scope / "guide.md",
            (
                "# Chat\n\nWrong:\n"
                "```bash\nforgelm chat ./model --top-p 0.9\n```\n"
                "\nCorrect form:\n\n"
                "```bash\nforgelm chat ./model --top-p 0.9\n```\n"
            ),
        )
        rc = tool.main(["--repo-root", str(tmp_path), "--scope", "docs", "--strict"])
        assert rc == 1, "Wrong: marker must scope to one block; next block must scan"
        out = capsys.readouterr().out
        assert "--top-p" in out
        # Wrong:-block scope: the first block must NOT contribute a
        # finding; only the second block reports drift.  The tool's
        # summary line counts findings (one per drifted invocation),
        # so a regression where the Wrong-marker stopped scoping (both
        # blocks scanned) would show "2 drift finding" here.
        assert "1 drift finding" in out, (
            "Wrong: must scope to one block — first block must NOT be "
            f"reported; expected exactly 1 drift finding, got: {out!r}"
        )


# ---------------------------------------------------------------------------
# §6 — CLI surface: --quiet, --scope, --analysis exclusion
# ---------------------------------------------------------------------------


class TestCLISurface:
    def test_quiet_suppresses_ok_summary(self, tmp_path: Path, tool, capsys):
        scope = tmp_path / "docs"
        _write(
            scope / "guide.md",
            ("```bash\nforgelm verify-audit ./outputs/audit_log.jsonl\n```\n"),
        )
        rc = tool.main(["--repo-root", str(tmp_path), "--scope", "docs", "--quiet"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "OK" not in out

    def test_analysis_tree_excluded_by_default(self, tmp_path: Path, tool, capsys):
        scope = tmp_path / "docs"
        # Plant the ghost-flag invocation under analysis/ — should
        # be excluded by default and thus NOT flag.
        _write(
            scope / "analysis" / "review.md",
            ("```bash\nforgelm chat ./model --top-p 0.9\n```\n"),
        )
        rc = tool.main(["--repo-root", str(tmp_path), "--scope", "docs", "--strict"])
        assert rc == 0


# ---------------------------------------------------------------------------
# §7 — Parser-surface discovery sanity
# ---------------------------------------------------------------------------


class TestParserSurfaceDiscovery:
    def test_discovery_yields_known_subcommands(self, tool):
        surface = tool.discover_parser_surface()
        names = set(surface.subcommands.keys())
        # Every subcommand cited in the cli.md table must be present
        # in the live surface.
        for expected in (
            "verify-audit",
            "deploy",
            "chat",
            "audit",
            "ingest",
            "approve",
            "reject",
            "purge",
            "safety-eval",
            "verify-gguf",
            "verify-annex-iv",
        ):
            assert expected in names, f"expected subcommand {expected!r} missing from live parser surface"

    def test_deploy_target_choices_recovered(self, tool):
        surface = tool.discover_parser_surface()
        deploy = surface.subcommands["deploy"]
        assert "--target" in deploy.flags
        assert "--target" in deploy.choices
        assert "ollama" in deploy.choices["--target"]
        assert "kserve" not in deploy.choices["--target"]
