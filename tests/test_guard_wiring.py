"""Meta-test: the guard apparatus is self-enforcing (H11 / XP-13, F-P8-C-07).

The full-project review found the guard inventory had dead arms: of 19
``tools/check_*.py`` guards, ten were referenced in NO workflow (three of them
failing at HEAD), and three gauntlet-listed guards ran only if a developer
executed the CLAUDE.md self-review block by hand. A guard that never runs in CI
is dead enforcement infrastructure — exactly why W0/W1 drift reached HEAD.

This meta-test makes the apparatus self-checking:

1. Every ``tools/check_*.py`` is referenced by ≥1 workflow OR explicitly
   allowlisted here with a written rationale (so a new unwired guard fails CI
   unless its owner consciously defers it).
2. Every guard named in the CLAUDE.md self-review gauntlet is also wired into a
   workflow (no gauntlet-only enforcement — CI is the enforcement boundary).
3. The CLAUDE.md and AGENTS.md gauntlets stay in lockstep.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TOOLS = _REPO_ROOT / "tools"
_WORKFLOWS = _REPO_ROOT / ".github" / "workflows"

# Guards intentionally NOT wired into any workflow yet. Each entry MUST carry a
# rationale pointing at the finding/work-package that owns the deferral. A guard
# is removed from this set the moment it is wired (then rule 1 enforces it).
_UNWIRED_ALLOWLIST: dict[str, str] = {
    "check_doc_numerical_claims.py": (
        "Owned by W1/H5 (F-P8-C-06): wiring lands with the webhook 5->8 doc-drift "
        "fix so the newly-wired gate goes green in the same PR."
    ),
    "check_notebook_pins.py": (
        "Owned by W2/M7 (F-P8-C-09): fails at HEAD on stale notebook pins "
        "(forgelm==0.5.7 vs released 0.7.x); wiring lands with the pin bump."
    ),
}


def _all_guards() -> list[str]:
    return sorted(p.name for p in _TOOLS.glob("check_*.py"))


def _workflow_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(_WORKFLOWS.glob("*.yml")))


def _gauntlet_guards(doc: Path) -> set[str]:
    """Return the tools/*.py guard names invoked in the doc's gauntlet block."""
    text = doc.read_text(encoding="utf-8")
    return set(re.findall(r"tools/(check_[a-z_]+\.py|update_site_version\.py)", text))


def test_guard_count_is_nineteen_or_more():
    """Inventory sanity — the review corrected the stale '23 guards' claim to
    the real count. The floor catches an accidental guard deletion."""
    guards = _all_guards()
    assert len(guards) >= 19, f"expected >=19 check_*.py guards, found {len(guards)}: {guards}"


def test_every_guard_is_wired_or_allowlisted():
    wf = _workflow_text()
    unwired = [g for g in _all_guards() if g not in wf and g not in _UNWIRED_ALLOWLIST]
    assert not unwired, (
        f"these guards are wired into no workflow and not allowlisted: {unwired}. "
        "Wire each into ci.yml (or nightly.yml) or add it to _UNWIRED_ALLOWLIST with a rationale."
    )


def test_allowlist_entries_still_exist():
    """An allowlisted guard that was deleted leaves a stale rationale — flag it."""
    missing = [g for g in _UNWIRED_ALLOWLIST if not (_TOOLS / g).exists()]
    assert not missing, f"_UNWIRED_ALLOWLIST names non-existent guard(s): {missing}"


def test_allowlisted_guards_are_actually_unwired():
    """A guard that got wired must be removed from the allowlist (so the
    deferral note can't silently rot into a lie)."""
    wf = _workflow_text()
    wired_but_allowlisted = [g for g in _UNWIRED_ALLOWLIST if g in wf]
    assert not wired_but_allowlisted, (
        f"these guards are wired AND allowlisted as unwired: {wired_but_allowlisted}. "
        "Remove them from _UNWIRED_ALLOWLIST."
    )


def test_every_gauntlet_guard_is_wired_into_ci():
    """No gauntlet-only enforcement: every guard in the CLAUDE.md self-review
    block must also run in a workflow (CI is the enforcement boundary)."""
    wf = _workflow_text()
    gauntlet = _gauntlet_guards(_REPO_ROOT / "CLAUDE.md")
    gauntlet_only = [g for g in gauntlet if g not in wf]
    assert not gauntlet_only, f"gauntlet guards enforced only by human discipline: {gauntlet_only}"


def test_claude_and_agents_gauntlets_match():
    """The .agents/ mirror (AGENTS.md) must list the same gauntlet guards as
    CLAUDE.md ([[project_agents_md_mirror]])."""
    assert _gauntlet_guards(_REPO_ROOT / "CLAUDE.md") == _gauntlet_guards(_REPO_ROOT / "AGENTS.md")
