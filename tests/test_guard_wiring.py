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
3. The CLAUDE.md, AGENTS.md and CONTRIBUTING.md gauntlets stay in lockstep with
   each other **and** with ci.yml, in BOTH directions.

Rule 3 used to run one way only — every gauntlet guard had to be in a workflow,
but nothing required every workflow guard to be in a gauntlet. So the guard set
grew and the documented gauntlets did not: at the time this test was made
bidirectional, ci.yml ran 29 guards while CLAUDE.md and AGENTS.md listed 19 and
CONTRIBUTING.md listed 18. A developer running the documented block locally and
seeing it green had no signal that ten CI gates had not been evaluated. This is
the second occurrence of exactly this defect — an earlier remediation package
closed the same drift between CONTRIBUTING.md and CI, and it reopened as soon as
new guards landed, because the one-directional check could not see it.

CONTRIBUTING.md is included deliberately: it is pinned by nothing else in the
suite, which is why it drifted one guard further than the two rulebooks.
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
    # All previously-deferred guards are now wired:
    #   * check_doc_numerical_claims.py — wired into ci.yml once H5's webhook
    #     5->8 doc-drift fix made it green (F-P8-C-06).
    #   * check_notebook_pins.py — wired by M7 alongside the 0.7.0 pin bump
    #     (F-P8-C-09).
    # Add an entry here ONLY with a rationale if a new guard is intentionally
    # deferred; test_allowlisted_guards_are_actually_unwired keeps it honest.
}


def _all_guards() -> list[str]:
    return sorted(p.name for p in _TOOLS.glob("check_*.py"))


def _workflow_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(_WORKFLOWS.glob("*.yml")))


_GAUNTLET_DOCS = ("CLAUDE.md", "AGENTS.md", "CONTRIBUTING.md")


def _gauntlet_guards(doc: Path) -> set[str]:
    """Return the tools/*.py guard names invoked in the doc's gauntlet block.

    Scoped to the fenced block that starts at the import-origin guard rather
    than the whole file: prose elsewhere in these documents discusses guards by
    name, and counting those would let a *mention* satisfy a rule about what the
    reader is told to *run*.
    """
    text = doc.read_text(encoding="utf-8")
    match = re.search(r"(python3? tools/check_import_origin\.py --strict.*?)```", text, re.S)
    assert match, f"{doc.name} has no gauntlet block starting at check_import_origin.py"
    return set(re.findall(r"tools/(check_[a-z0-9_]+\.py|update_site_version\.py)", match.group(1)))


def _ci_guards() -> set[str]:
    """Guards `.github/workflows/ci.yml` actually invokes.

    ci.yml, not every workflow: the gauntlet's contract is "what a PR must
    pass". `check_pip_audit.py` runs only in nightly.yml because it needs a
    fresh scan report, and requiring it locally would make the documented block
    unrunnable.
    """
    ci = (_WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    return set(re.findall(r"tools/(check_[a-z0-9_]+\.py|update_site_version\.py)", ci))


def test_guard_inventory_is_derived_not_asserted():
    """No hand-written floor.

    This used to assert `len(guards) >= 19`, which is satisfied by any number
    at or above the count on the day it was written and therefore says nothing
    about drift. The real invariant is that the three documented gauntlets and
    ci.yml name the same set — asserted below — so all this needs to do is
    prove the inventory is non-empty and reachable.
    """
    guards = _all_guards()
    assert guards, "no tools/check_*.py guards found — the inventory scan is broken"
    assert _ci_guards(), "ci.yml invokes no guards — the CI scan is broken"


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


def test_all_three_gauntlets_match_each_other():
    """CLAUDE.md, AGENTS.md and CONTRIBUTING.md must publish one gauntlet.

    AGENTS.md is CLAUDE.md's mirror for a second harness; CONTRIBUTING.md is
    the human-facing copy. Three hand-maintained restatements of one command
    block is exactly the shape that rots, so they are compared to each other
    rather than each to CI alone.
    """
    sets = {doc: _gauntlet_guards(_REPO_ROOT / doc) for doc in _GAUNTLET_DOCS}
    reference = sets["CLAUDE.md"]
    for doc, got in sets.items():
        assert got == reference, (
            f"{doc}'s gauntlet diverges from CLAUDE.md's — "
            f"only in {doc}: {sorted(got - reference)}; missing from {doc}: {sorted(reference - got)}"
        )


def test_every_ci_guard_appears_in_every_gauntlet():
    """The reverse direction, which is the one that was missing.

    A guard wired into ci.yml but absent from the documented block means a
    developer can run the whole published self-review, see green, and still be
    failed by CI on a gate they were never told to run.
    """
    ci = _ci_guards()
    for doc in _GAUNTLET_DOCS:
        missing = sorted(ci - _gauntlet_guards(_REPO_ROOT / doc))
        assert not missing, (
            f"{doc}'s gauntlet omits {len(missing)} guard(s) that ci.yml runs: {missing}. "
            "Add them to the block — a local run that cannot fail where CI fails is not a self-review."
        )


def test_no_gauntlet_invents_a_guard_ci_does_not_run():
    """The forward direction, tightened to ci.yml specifically.

    `test_every_gauntlet_guard_is_wired_into_ci` accepts any workflow, so a
    nightly-only guard would satisfy it while still being unrunnable as part of
    a pre-push check. This pins the gauntlet to the PR gate.
    """
    ci = _ci_guards()
    for doc in _GAUNTLET_DOCS:
        extra = sorted(_gauntlet_guards(_REPO_ROOT / doc) - ci)
        assert not extra, (
            f"{doc}'s gauntlet lists {extra}, which ci.yml does not run — "
            "either wire it into ci.yml or drop it from the documented block."
        )
