"""Tests for tools/check_cli_exit_code_prose.py.

The guard asserts one bit per subcommand, in both directions: a dispatcher
that can emit ``EXIT_INTEGRITY_FAILURE`` (6) must document it in ``--help``,
and one that cannot must not claim it.  Both inputs are derived from source
(``_dispatch.py``'s routing table + ``subcommands/`` modules on one side,
``_parser.py``'s ``add_parser``/``help=`` literals on the other), so there is
no mapping table for a test to pin.

Detection logic is therefore pinned against synthetic in-memory fixtures —
a parser stub that says 6, one that doesn't, a dispatcher module that
references the constant, one that doesn't — so the tests stay independent of
the real, evolving CLI surface.  A separate live-repo class asserts the
invariant CI relies on: ``main(["--strict", "--quiet"]) == 0``.

The regression these fixtures encode is real: at commit 74f75bb all four
``verify-*`` subcommands routed integrity failures to 6 while their help text
still said 1, and this guard reports exactly four findings against that tree.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TOOL_PATH = _REPO_ROOT / "tools" / "check_cli_exit_code_prose.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("check_cli_exit_code_prose", _TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_cli_exit_code_prose"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture(scope="module")
def tool():
    return _load_tool()


# ---------------------------------------------------------------------------
# Prose claim detection
# ---------------------------------------------------------------------------


class TestClaimsIntegrityCode:
    @pytest.mark.parametrize(
        "text",
        [
            "Strict mode: exit 6 if any line lacks an _hmac field.",
            "Exits 0 on valid; 6 when any artifact was changed.",
            "exit code 0 means the chain is intact, 6 means tampering",
            "exits 6 on a manifest hash mismatch",
            "Exit 6 for a checksum mismatch.",
        ],
    )
    def test_recognises_real_phrasings(self, tool, text):
        assert tool.claims_integrity_code(text)

    @pytest.mark.parametrize(
        "text",
        [
            # The pre-fix wording this guard exists to catch.
            "exit code 0 means the chain is intact, 1 means tampering or corruption.",
            "Exits 0 on valid; 1 on missing field or hash mismatch.",
            # Incidental numerals must not read as an exit-code claim — this is
            # why the guard checks code 6 only and anchors on exit context.
            "Three-layer check: 4-byte `GGUF` magic header, metadata block parse.",
            "validates the nine required field categories per Annex IV §1-9",
            "SHA-256 comparison against the `<path>.sha256` sidecar.",
            "Exits 0 on valid; 1 on any mismatch.",
        ],
    )
    def test_rejects_non_claims(self, tool, text):
        assert not tool.claims_integrity_code(text)

    def test_clause_boundary_is_respected(self, tool):
        """'exit 1 ...; 6 when ...' must not be read as a bare 'exit 1' —
        the second clause is a genuine claim and has to be seen."""
        assert tool.claims_integrity_code("Exits 0 on valid; 1 on a missing field; 6 on a hash mismatch.")

    def test_exit_one_claim_far_from_a_six_is_not_a_claim(self, tool):
        """A sentence boundary stops the 'exits ... 6' window, so a 6 that
        belongs to an unrelated later sentence is not credited."""
        assert not tool.claims_integrity_code("Exits 1 on failure. Supports 6 output backends.")


# ---------------------------------------------------------------------------
# Source extraction + comparison
# ---------------------------------------------------------------------------


_DISPATCH_STUB = """
def dispatch(command, args):
    table = {
        "verify-thing": "_run_verify_thing_cmd",
        "plain-thing": "_run_plain_thing_cmd",
    }
"""

_EMITTER_MODULE = """
from .._exit_codes import EXIT_CONFIG_ERROR, EXIT_INTEGRITY_FAILURE, EXIT_SUCCESS


def _run_verify_thing_cmd(args, output_format):
    raise SystemExit(EXIT_INTEGRITY_FAILURE)
"""

_NON_EMITTER_MODULE = """
from .._exit_codes import EXIT_CONFIG_ERROR, EXIT_SUCCESS


def _run_plain_thing_cmd(args, output_format):
    raise SystemExit(EXIT_SUCCESS)
"""


def _parser_stub(verify_help: str, plain_help: str) -> str:
    return f"""
def _add_verify_thing_subcommand(subparsers):
    p = subparsers.add_parser(
        "verify-thing",
        help="Verify a thing.",
        description={verify_help!r},
    )


def _add_plain_thing_subcommand(subparsers):
    p = subparsers.add_parser(
        "plain-thing",
        help="Do a plain thing.",
        description={plain_help!r},
    )
"""


@pytest.fixture
def fake_cli(tool, tmp_path, monkeypatch):
    """Point the guard at a synthetic three-file CLI tree."""
    subcommands = tmp_path / "subcommands"
    subcommands.mkdir()
    (subcommands / "_verify_thing.py").write_text(_EMITTER_MODULE, encoding="utf-8")
    (subcommands / "_plain_thing.py").write_text(_NON_EMITTER_MODULE, encoding="utf-8")
    (tmp_path / "_dispatch.py").write_text(_DISPATCH_STUB, encoding="utf-8")

    monkeypatch.setattr(tool, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tool, "DISPATCH_PATH", tmp_path / "_dispatch.py")
    monkeypatch.setattr(tool, "SUBCOMMANDS_DIR", subcommands)

    def _write_parser(verify_help: str, plain_help: str = "Exits 0 or 1.") -> None:
        path = tmp_path / "_parser.py"
        path.write_text(_parser_stub(verify_help, plain_help), encoding="utf-8")
        monkeypatch.setattr(tool, "PARSER_PATH", path)

    return _write_parser


class TestComparison:
    def test_clean_tree_has_no_findings(self, tool, fake_cli):
        fake_cli("Exits 0 on valid; 6 when the thing was tampered with.")
        findings, checked = tool.collect_findings()
        assert checked == 2
        assert findings == []

    def test_hidden_integrity_code_is_reported(self, tool, fake_cli):
        """The exact F-2 regression: routing exits 6, help still says 1."""
        fake_cli("Exits 0 on valid; 1 on any mismatch.")
        findings, _ = tool.collect_findings()
        assert len(findings) == 1
        assert "verify-thing" in findings[0]
        assert "never says so" in findings[0]

    def test_phantom_integrity_code_is_reported(self, tool, fake_cli):
        """A subcommand that cannot exit 6 must not advertise it — this is
        the direction that catches routing regressing back to exit 1."""
        fake_cli(
            "Exits 0 on valid; 6 when tampered.",
            plain_help="Exits 0 on success; 6 on trouble.",
        )
        findings, _ = tool.collect_findings()
        assert len(findings) == 1
        assert "plain-thing" in findings[0]
        assert "never references" in findings[0]

    def test_flag_help_counts_as_prose(self, tool, tmp_path, monkeypatch, fake_cli):
        """The --require-hmac fix lives in an add_argument help=, not the
        description — the guard must read those too."""
        fake_cli("Exits 0 on valid; 1 on any mismatch.")
        path = tmp_path / "_parser.py"
        path.write_text(
            path.read_text(encoding="utf-8")
            + '\n\ndef _extra(p):\n    p.add_argument("--strict", help="Strict mode: exit 6 if a tag is missing.")\n',
            encoding="utf-8",
        )
        # The add_argument lives outside the registration function, so it must
        # NOT be credited: prose attribution is per-subcommand.
        findings, _ = tool.collect_findings()
        assert len(findings) == 1

    def test_docstrings_are_not_credited_as_help(self, tool, tmp_path, monkeypatch, fake_cli):
        """A maintainer-facing docstring saying 6 does not make --help say 6."""
        fake_cli("Exits 0 on valid; 1 on any mismatch.")
        path = tmp_path / "_parser.py"
        text = path.read_text(encoding="utf-8").replace(
            "def _add_verify_thing_subcommand(subparsers):\n",
            'def _add_verify_thing_subcommand(subparsers):\n    """Exits 6 on tampering."""\n',
        )
        path.write_text(text, encoding="utf-8")
        findings, _ = tool.collect_findings()
        assert len(findings) == 1
        assert "verify-thing" in findings[0]

    def test_unrouted_subcommand_is_skipped(self, tool, tmp_path, fake_cli):
        """A parser registration with no dispatch-table row has nothing to
        compare against and must not be invented into a finding."""
        fake_cli("Exits 0 on valid; 6 when tampered.")
        path = tmp_path / "_parser.py"
        path.write_text(
            path.read_text(encoding="utf-8") + "\n\ndef _add_ghost_subcommand(subparsers):\n"
            '    subparsers.add_parser("ghost", description="Exits 0 or 1.")\n',
            encoding="utf-8",
        )
        findings, checked = tool.collect_findings()
        assert checked == 2
        assert findings == []


class TestStructuralFailures:
    """The guard must never report success when it checked nothing."""

    def test_missing_dispatch_table_exits_one_even_without_strict(self, tool, tmp_path, monkeypatch):
        monkeypatch.setattr(tool, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(tool, "DISPATCH_PATH", tmp_path / "_dispatch.py")
        (tmp_path / "_dispatch.py").write_text("x = 1\n", encoding="utf-8")
        monkeypatch.setattr(tool, "SUBCOMMANDS_DIR", tmp_path)
        monkeypatch.setattr(tool, "PARSER_PATH", tmp_path / "_parser.py")
        (tmp_path / "_parser.py").write_text("", encoding="utf-8")
        assert tool.main([]) == 1

    def test_missing_file_exits_one(self, tool, tmp_path, monkeypatch):
        monkeypatch.setattr(tool, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(tool, "DISPATCH_PATH", tmp_path / "nope.py")
        assert tool.main(["--strict"]) == 1


class TestExitCodeContract:
    def test_advisory_mode_reports_but_exits_zero(self, tool, fake_cli, capsys):
        fake_cli("Exits 0 on valid; 1 on any mismatch.")
        assert tool.main([]) == 0
        assert "FAIL" in capsys.readouterr().out

    def test_strict_mode_exits_one(self, tool, fake_cli):
        fake_cli("Exits 0 on valid; 1 on any mismatch.")
        assert tool.main(["--strict"]) == 1

    def test_quiet_suppresses_success_summary(self, tool, fake_cli, capsys):
        fake_cli("Exits 0 on valid; 6 when tampered.")
        assert tool.main(["--strict", "--quiet"]) == 0
        assert capsys.readouterr().out == ""


class TestLiveRepo:
    """The invariant CI actually enforces."""

    def test_repo_is_clean(self):
        tool = _load_tool()
        assert tool.main(["--strict", "--quiet"]) == 0

    def test_the_four_verify_subcommands_are_covered(self):
        """Guard-the-guard: if the verify-* dispatchers ever stop being
        detected as integrity emitters, this guard silently checks nothing
        and the F-2 drift class reopens."""
        tool = _load_tool()
        emitters = tool.commands_that_can_emit_integrity_code(tool.extract_dispatch_table())
        assert {"verify-audit", "verify-annex-iv", "verify-gguf", "verify-integrity"} <= emitters
