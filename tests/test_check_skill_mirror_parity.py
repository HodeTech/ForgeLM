"""Tests for tools/check_skill_mirror_parity.py.

The guard pins ``.claude/skills/<name>/`` against ``.agents/skills/<name>/``:
same skills, same files, identical content once the documented ``.claude/`` <->
``.agents/`` substitution allowlist is applied. The trees are the same document
shipped to two harnesses and maintained by hand, so a one-copy edit is silent —
the agent reading the un-edited copy simply keeps following the old procedure.

Three layers, mirroring ``tests/test_check_release_record_sync.py``:

* **Unit** — normalisation and detection logic pinned against synthetic
  fixtures, independent of the real (evolving) skill corpus.
* **Enforcement** (:class:`TestEnforcement`) — ``main()`` driven against a
  temporary tree via monkeypatched roots, so every *failure* branch runs and its
  exit code is asserted in both strict and advisory mode. A live-repo-only suite
  is green by construction: mutating ``return 1 if args.strict else 0`` to
  ``return 0`` would leave it fully passing.
* **Live repo** (:class:`TestRealRepo`) — the invariant CI relies on,
  ``main(["--strict", "--quiet"]) == 0``, plus the wiring assertions.

The load-bearing test in the unit layer is
:meth:`TestNormalise.test_substitution_cannot_swallow_a_content_difference`: an
allowlist that absorbed real wording differences would turn the whole guard into
decoration.

:meth:`TestNormalise.test_swapped_agent_name_within_one_file_is_the_accepted_hole`
pins a *known, documented* gap rather than a bug: the module docstring's
"accepted cost" section states plainly that a swapped pair of agent mentions
within one file is invisible to this guard, and this test is the mechanism
that keeps that admission honest — if it ever starts failing, the docstring
is now overstating the hole and must be tightened to match, not the other
way around.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TOOL_PATH = _REPO_ROOT / "tools" / "check_skill_mirror_parity.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("check_skill_mirror_parity", _TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_skill_mirror_parity"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture(scope="module")
def tool():
    return _load_tool()


_GREEN_SKILL = """---
name: demo
description: A demo skill.
---

# Skill: Demo

Follow the checklist in CLAUDE.md, then run the gauntlet.

1. Do the thing.
2. Verify the thing.
"""


class TestSubstitutionTable:
    def test_table_is_the_documented_three(self, tool):
        pairs = {(s.claude, s.agents) for s in tool.SUBSTITUTIONS}
        assert pairs == {(".claude/", ".agents/"), ("CLAUDE.md", "AGENTS.md"), ("Claude", "Codex")}

    def test_canonical_tokens_are_unique(self, tool):
        canonicals = [s.canonical for s in tool.SUBSTITUTIONS]
        assert len(set(canonicals)) == len(canonicals)

    def test_canonical_tokens_are_nul_delimited(self, tool):
        # NUL-delimited so no authored SKILL.md can spell one by accident and
        # forge a match between two genuinely different files. This only
        # checks the tokens' *shape*; it says nothing about whether any real
        # file actually contains one -- see
        # ``test_canonical_tokens_cannot_occur_in_real_markdown`` below for
        # the test that scans the live corpus and earns that name.
        for substitution in tool.SUBSTITUTIONS:
            assert substitution.canonical.startswith("\x00")
            assert substitution.canonical.endswith("\x00")


class TestNormalise:
    def test_both_spellings_collapse_to_one_token(self, tool):
        claude = tool.normalise("see .claude/skills/x/SKILL.md", tree="claude")
        agents = tool.normalise("see .agents/skills/x/SKILL.md", tree="agents")
        assert claude == agents

    def test_agent_name_collapses(self, tool):
        assert tool.normalise("including Claude as reviewer", tree="claude") == tool.normalise(
            "including Codex as reviewer", tree="agents"
        )

    def test_instruction_doc_collapses(self, tool):
        assert tool.normalise("read CLAUDE.md first", tree="claude") == tool.normalise(
            "read AGENTS.md first", tree="agents"
        )

    def test_doc_name_is_not_eaten_by_the_bare_agent_name(self, tool):
        # `CLAUDE.md` must be consumed whole; if the bare-name rule ran first it
        # would leave a `.md` fragment and stop matching `AGENTS.md`.
        assert tool.normalise("CLAUDE.md", tree="claude") == tool.normalise("AGENTS.md", tree="agents")
        assert ".md" not in tool.normalise("CLAUDE.md", tree="claude")

    def test_prose_naming_both_trees_normalises_identically(self, tool):
        # The reason this is a normalisation and not a directional rewrite:
        # agent-neutral prose naming both roots is a sentence both copies carry
        # verbatim, and a directional rule would rewrite it on one side only.
        line = "the mirror under .claude/ or .agents/"
        assert tool.normalise(line, tree="claude") == tool.normalise(line, tree="agents")

    def test_substitution_cannot_swallow_a_content_difference(self, tool):
        # THE load-bearing property. The allowlist may only ever collapse the
        # three documented spellings — never a difference in wording. If this
        # ever passes as equal, the guard has become decoration.
        claude = tool.normalise("Claude runs step 4.5 before the tag.", tree="claude")
        agents = tool.normalise("Codex runs step 4.5 after the tag.", tree="agents")
        assert claude != agents

    def test_substitution_cannot_swallow_a_reordering(self, tool):
        claude = tool.normalise("1. bump\n2. tag\n", tree="claude")
        agents = tool.normalise("1. tag\n2. bump\n", tree="agents")
        assert claude != agents

    def test_identical_text_normalises_identically(self, tool):
        assert tool.normalise(_GREEN_SKILL, tree="claude") == tool.normalise(_GREEN_SKILL, tree="agents")

    def test_unknown_tree_is_a_programming_error(self, tool):
        with pytest.raises(ValueError, match="tree must be"):
            tool.normalise("text", tree="gemini")

    def test_swapped_agent_name_within_one_file_is_the_accepted_hole(self, tool):
        # Pins the module docstring's "accepted cost" claim at its true width:
        # NOT just "a whole copy consistently uses the other tree's spelling"
        # (the old, narrower framing) but ANY per-occurrence choice of which
        # allowlisted spelling appears where. A single swapped pair of agent
        # mentions inside an otherwise-correct file is invisible, because
        # normalise() has no notion of position -- only of the token sequence
        # left after collapsing. Real content here assigns the draft/review
        # split to opposite harnesses between the two copies; the guard
        # cannot see that. If this test ever starts asserting inequality, the
        # hole has closed and the docstring must be tightened to match -- not
        # the other way around.
        claude_text = "Ask Claude to draft the plan, then have Codex review it before merging."
        agents_text = "Ask Codex to draft the plan, then have Claude review it before merging."
        assert tool.normalise(claude_text, tree="claude") == tool.normalise(agents_text, tree="agents")

    def test_longer_spelling_within_a_rule_is_tried_first(self, tool, monkeypatch):
        # normalise() sorts each rule's two spellings by length (longest
        # first) specifically so a shorter spelling cannot shadow a longer
        # one that contains it. None of the three shipped rules actually
        # overlap this way today (see the SUBSTITUTIONS live-usage comment),
        # so this constructs a synthetic rule where the claude spelling is a
        # strict prefix of the agents spelling and proves the ordering
        # matters: if the sort were removed (spellings tried in declaration
        # order: "ab" then "abc"), replacing "ab" first would leave a
        # dangling "c" instead of fully consuming "abc".
        overlapping_rule = tool.Substitution(canonical="\x00OVERLAP\x00", claude="ab", agents="abc")
        monkeypatch.setattr(tool, "SUBSTITUTIONS", (overlapping_rule,))
        assert tool.normalise("abc", tree="agents") == "\x00OVERLAP\x00"


class _FakeTree:
    """A pair of temporary skill roots with one green, mirrored skill."""

    def __init__(self, tool, tmp_path, monkeypatch):
        self.claude_root = tmp_path / ".claude" / "skills"
        self.agents_root = tmp_path / ".agents" / "skills"
        monkeypatch.setattr(tool, "CLAUDE_SKILLS_ROOT", self.claude_root)
        monkeypatch.setattr(tool, "AGENTS_SKILLS_ROOT", self.agents_root)
        self.write()

    def write(self, *, claude=None, agents=None):
        """(Re)create both roots with a single `demo` skill in each."""
        for root, text in ((self.claude_root, claude), (self.agents_root, agents)):
            directory = root / "demo"
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "SKILL.md").write_text(_GREEN_SKILL if text is None else text, encoding="utf-8")

    def add_file(self, *, root: str, relative: str, text: str = "extra\n"):
        base = self.claude_root if root == "claude" else self.agents_root
        path = base / "demo" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def add_skill(self, name: str, *, root: str):
        base = self.claude_root if root == "claude" else self.agents_root
        directory = base / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "SKILL.md").write_text(_GREEN_SKILL, encoding="utf-8")


@pytest.fixture
def tree(tool, tmp_path, monkeypatch):
    return _FakeTree(tool, tmp_path, monkeypatch)


class TestDiscoverSkills:
    def test_union_of_both_roots_sorted(self, tool, tree):
        tree.add_skill("alpha", root="claude")
        tree.add_skill("zeta", root="agents")
        assert [pair.name for pair in tool.discover_skills()] == ["alpha", "demo", "zeta"]

    def test_presence_flags_track_each_root(self, tool, tree):
        tree.add_skill("alpha", root="claude")
        pair = next(p for p in tool.discover_skills() if p.name == "alpha")
        assert (pair.in_claude, pair.in_agents) == (True, False)


class TestListFiles:
    def test_nested_files_are_included_as_posix_paths(self, tool, tree):
        tree.add_file(root="claude", relative="references/extra.md")
        assert tool.list_files(tree.claude_root / "demo") == ["SKILL.md", "references/extra.md"]

    def test_missing_directory_is_empty_not_an_error(self, tool, tmp_path):
        assert tool.list_files(tmp_path / "nope") == []


class TestCompareFile:
    def _pair(self, tmp_path, claude_text, agents_text):
        claude = tmp_path / "c.md"
        agents = tmp_path / "a.md"
        claude.write_text(claude_text, encoding="utf-8")
        agents.write_text(agents_text, encoding="utf-8")
        return claude, agents

    def test_identical_files_match(self, tool, tmp_path):
        assert tool.compare_file(*self._pair(tmp_path, _GREEN_SKILL, _GREEN_SKILL)) is None

    def test_allowlisted_difference_matches(self, tool, tmp_path):
        paths = self._pair(tmp_path, "read CLAUDE.md\n", "read AGENTS.md\n")
        assert tool.compare_file(*paths) is None

    def test_real_difference_is_reported(self, tool, tmp_path):
        diff = tool.compare_file(*self._pair(tmp_path, "step one\n", "step two\n"))
        assert diff is not None
        assert any("step two" in line for line in diff)

    def test_diff_shows_original_text_not_placeholders(self, tool, tmp_path):
        # The operator edits the real files; a diff full of \x00 tokens would be
        # unreadable. Only the match decision uses normalised text.
        diff = tool.compare_file(*self._pair(tmp_path, "CLAUDE.md alpha\n", "AGENTS.md beta\n"))
        assert diff is not None
        joined = "\n".join(diff)
        assert "CLAUDE.md" in joined and "AGENTS.md" in joined
        assert "\x00" not in joined

    def test_matching_binary_files_pass(self, tool, tmp_path):
        claude = tmp_path / "c.bin"
        agents = tmp_path / "a.bin"
        claude.write_bytes(b"\xff\xfe\x00logo")
        agents.write_bytes(b"\xff\xfe\x00logo")
        assert tool.compare_file(claude, agents) is None

    def test_differing_binary_files_fail(self, tool, tmp_path):
        # Substitution is meaningless on bytes, but silently skipping an
        # undecodable file would be a hole in the guard.
        claude = tmp_path / "c.bin"
        agents = tmp_path / "a.bin"
        claude.write_bytes(b"\xff\xfe\x00one")
        agents.write_bytes(b"\xff\xfe\x00twotwo")
        diff = tool.compare_file(claude, agents)
        assert diff is not None and "binary content differs" in diff[0]

    def test_pre_existing_canonical_token_is_rejected_defensively(self, tool, tmp_path):
        # The match/no-match decision assumes no real file already spells out
        # a reserved placeholder verbatim. That assumption is checked, not
        # just trusted: a file that already contains one raises instead of
        # silently feeding into normalise() and potentially forging a match.
        claude = tmp_path / "c.md"
        agents = tmp_path / "a.md"
        claude.write_text(f"normal text {tool.SUBSTITUTIONS[0].canonical} more text\n", encoding="utf-8")
        agents.write_text("normal text more text\n", encoding="utf-8")
        with pytest.raises(ValueError, match="reserved token"):
            tool.compare_file(claude, agents)


class TestEnforcement:
    """Every failure branch: exit 1 under ``--strict``, exit 0 advisory."""

    def _fails(self, tool, capsys, fragment):
        strict = tool.main(["--strict"])
        out = capsys.readouterr().out
        advisory = tool.main([])
        capsys.readouterr()
        assert (strict, advisory) == (1, 0), out
        assert fragment in out, out
        return out

    def test_green_tree_passes(self, tool, tree, capsys):
        assert tool.main(["--strict"]) == 0
        assert "OK:" in capsys.readouterr().out

    def test_allowlisted_difference_passes(self, tool, tree, capsys):
        tree.write(
            claude=_GREEN_SKILL + "\nAsk Claude to read .claude/skills/demo/SKILL.md.\n",
            agents=_GREEN_SKILL + "\nAsk Codex to read .agents/skills/demo/SKILL.md.\n",
        )
        assert tool.main(["--strict"]) == 0

    def test_allowlisted_agent_doc_difference_passes(self, tool, tree, capsys):
        # AGENT_DOC (CLAUDE.md <-> AGENTS.md) is currently unused by any real
        # skill file -- see the SUBSTITUTIONS live-usage comment in the tool
        # -- so unlike SKILL_ROOT and AGENT_NAME above it had no exercise
        # through the full main() pipeline. This closes that gap: without
        # this row in the allowlist, the two lines below would report as
        # drift.
        tree.write(
            claude=_GREEN_SKILL + "\nRead CLAUDE.md before starting.\n",
            agents=_GREEN_SKILL + "\nRead AGENTS.md before starting.\n",
        )
        assert tool.main(["--strict"]) == 0

    def test_content_drift_fails(self, tool, tree, capsys):
        tree.write(agents=_GREEN_SKILL.replace("2. Verify the thing.", "2. Skip verification."))
        out = self._fails(tool, capsys, "differ beyond the substitution allowlist")
        assert "Skip verification." in out

    def test_content_drift_survives_the_allowlist(self, tool, tree, capsys):
        # A one-copy edit dressed in allowlisted vocabulary must still fail —
        # otherwise the allowlist is a bypass rather than an exemption.
        tree.write(
            claude="Claude tags the release, then writes the record.\n",
            agents="Codex writes the record, then tags the release.\n",
        )
        self._fails(tool, capsys, "differ beyond the substitution allowlist")

    def test_skill_present_under_one_root_only_fails(self, tool, tree, capsys):
        tree.add_skill("orphan", root="claude")
        out = self._fails(tool, capsys, "exist under only one root")
        assert "orphan" in out

    def test_skill_present_under_agents_only_fails(self, tool, tree, capsys):
        tree.add_skill("orphan", root="agents")
        out = self._fails(tool, capsys, "exist under only one root")
        assert "orphan" in out

    def test_extra_file_in_one_copy_fails(self, tool, tree, capsys):
        # The same one-copy edit, one directory deeper.
        tree.add_file(root="claude", relative="references/extra.md")
        out = self._fails(tool, capsys, "hold different files under the two roots")
        assert "references/extra.md" in out

    def test_missing_entrypoint_fails(self, tool, tree, capsys):
        (tree.agents_root / "demo" / "SKILL.md").unlink()
        self._fails(tool, capsys, f"have no {tool.SKILL_ENTRYPOINT}")

    def test_failure_report_names_the_allowlist(self, tool, tree, capsys):
        tree.write(agents=_GREEN_SKILL.replace("1. Do the thing.", "1. Do something else."))
        out = self._fails(tool, capsys, "Only these spellings may differ")
        assert ".claude/" in out and ".agents/" in out

    @pytest.mark.parametrize("attribute", ["CLAUDE_SKILLS_ROOT", "AGENTS_SKILLS_ROOT"])
    def test_missing_root_exits_1_even_without_strict(self, tool, tree, monkeypatch, tmp_path, attribute, capsys):
        # A guard that cannot read its inputs must never report success — a
        # broken invocation is not drift to iterate on locally.
        monkeypatch.setattr(tool, attribute, tmp_path / "absent")
        assert tool.main([]) == 1
        assert tool.main(["--strict"]) == 1
        assert "not found" in capsys.readouterr().err

    def test_quiet_suppresses_the_success_summary(self, tool, tree, capsys):
        assert tool.main(["--strict", "--quiet"]) == 0
        assert capsys.readouterr().out == ""

    def test_quiet_does_not_suppress_failures(self, tool, tree, capsys):
        tree.write(agents=_GREEN_SKILL.replace("1. Do the thing.", "1. Do another thing."))
        assert tool.main(["--strict", "--quiet"]) == 1
        assert "FAIL:" in capsys.readouterr().out


class TestRealRepo:
    """The live invariant CI depends on."""

    def test_repo_is_clean(self, tool, capsys):
        assert tool.main(["--strict", "--quiet"]) == 0, capsys.readouterr().out

    def test_both_roots_hold_the_same_skill_names(self, tool):
        claude = {p.name for p in tool.CLAUDE_SKILLS_ROOT.iterdir() if p.is_dir()}
        agents = {p.name for p in tool.AGENTS_SKILLS_ROOT.iterdir() if p.is_dir()}
        assert claude == agents

    def test_canonical_tokens_cannot_occur_in_real_markdown(self, tool):
        # This is the test that earns the name: it actually scans both live
        # skill trees for the reserved NUL-delimited placeholder bytes,
        # rather than only asserting the tokens' shape (see
        # ``TestSubstitutionTable.test_canonical_tokens_are_nul_delimited``
        # for that narrower check). If this ever fails, a real file spells
        # out a reserved token and ``compare_file`` will now raise on it
        # (``_reject_pre_existing_canonical_tokens``) rather than silently
        # risking a forged match/mismatch.
        for root in (tool.CLAUDE_SKILLS_ROOT, tool.AGENTS_SKILLS_ROOT):
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    continue  # binary content; canonical tokens are text-only
                for substitution in tool.SUBSTITUTIONS:
                    assert substitution.canonical not in text, (path, substitution.canonical)

    def test_guard_wired_into_ci(self):
        # Assert the exact invocation, not just the filename: a step that runs
        # the guard without --strict reports drift and still exits 0, which is
        # the fake-green failure mode the guard exists to prevent.
        ci = (_REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        assert "python3 tools/check_skill_mirror_parity.py --strict" in ci

    @pytest.mark.parametrize("doc", ["CLAUDE.md", "AGENTS.md", "CONTRIBUTING.md"])
    def test_guard_listed_in_the_gauntlet(self, doc):
        text = (_REPO_ROOT / doc).read_text(encoding="utf-8")
        assert "python3 tools/check_skill_mirror_parity.py --strict" in text

    @pytest.mark.parametrize("mirror", [".claude", ".agents"])
    def test_cut_release_skill_orders_the_record_before_the_tag(self, mirror):
        # The reordering this guard was written alongside: the roadmap record
        # belongs to the pre-release checklist, ahead of "### 6. Commit + tag".
        skill = (_REPO_ROOT / mirror / "skills" / "cut-release" / "SKILL.md").read_text(encoding="utf-8")
        record = skill.index("### 4.5. Write the release record")
        tag = skill.index("### 6. Commit + tag")
        assert record < tag
