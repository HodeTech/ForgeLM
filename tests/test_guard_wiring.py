"""Meta-test: the guard apparatus is self-enforcing (H11 / XP-13, F-P8-C-07).

The full-project review found the guard inventory had dead arms: of 19
``tools/check_*.py`` guards, ten were referenced in NO workflow (three of them
failing at HEAD), and three gauntlet-listed guards ran only if a developer
executed the CLAUDE.md self-review block by hand. A guard that never runs in CI
is dead enforcement infrastructure — exactly why W0/W1 drift reached HEAD.

This meta-test makes the apparatus self-checking:

1. Every ``tools/check_*.py`` is referenced by >=1 workflow OR explicitly
   allowlisted here with a written rationale (so a new unwired guard fails CI
   unless its owner consciously defers it).
2. The three documented gauntlets (``CLAUDE.md``, ``AGENTS.md``,
   ``CONTRIBUTING.md``) and ``ci.yml`` name the same guards, **with the same
   flags**, in BOTH directions.
3. The gauntlets' non-guard commands — ruff, pytest, the dry-run, the mypy
   gate — stay in lockstep across the three documents and with CI too.
4. The published block cannot report success on a red tree.

**Why each of those is stated the way it is**, because every one was green
while the invariant it now checks was violated:

- Rule 2 used to run one way only. The guard set grew and the documented
  gauntlets did not: ci.yml ran 29 guards while CLAUDE.md and AGENTS.md listed
  19 and CONTRIBUTING.md 18. A developer running the published block and
  seeing green had no signal that ten CI gates were never evaluated. This is
  the second occurrence — an earlier package closed the same drift between
  CONTRIBUTING.md and CI, and it reopened the moment new guards landed.
- "With the same flags" is not pedantry. Dropping ``--strict`` from
  ``check_module_size.py`` leaves the guard *present* in both inventories while
  silently converting a fatal over-budget module into a ``WARN:`` line and
  exit 0. Comparing basenames could not see that.
- Rule 3 exists because comparing only ``tools/`` paths left ``pytest tests/``,
  ``ruff check .``, the ``--dry-run`` and the mypy gate unpinned: they could be
  deleted from one document, or from CI, with every test still passing.
- Rule 4 exists because the block briefly shipped with a trailing ``|| true``
  that bound to the whole ``&&`` chain, so a failing ``pytest`` produced exit 0.
- Workflow scanning parses YAML and reads ``run:`` scalars rather than grepping
  the file, because a guard named in a **comment** counted as "wired". Commenting
  out a ``run:`` line while leaving its ``- name:`` and comment block intact was
  a green mutation.

``CONTRIBUTING.md`` is included deliberately: it is pinned by nothing else in
the suite, which is why it drifted one guard further than the two rulebooks.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TOOLS = _REPO_ROOT / "tools"
_WORKFLOWS = _REPO_ROOT / ".github" / "workflows"

_GAUNTLET_DOCS = ("CLAUDE.md", "AGENTS.md", "CONTRIBUTING.md")

# One definition of a guard name, used by every helper below. ``_all_guards()``
# once globbed ``check_*.py`` while the parity regexes matched
# ``check_[a-z0-9_]+``, so a guard named with an upper-case letter would sit in
# the inventory and be invisible to every comparison.
_GUARD_NAME_RE = re.compile(r"(check_[a-z0-9_]+\.py|update_site_version\.py)")

# Guards intentionally NOT wired into any workflow yet. Each entry MUST carry a
# rationale pointing at the finding/work-package that owns the deferral. A guard
# is removed from this set the moment it is wired (then rule 1 enforces it).
_UNWIRED_ALLOWLIST: dict[str, str] = {}

# Guards that run in nightly.yml but deliberately NOT in ci.yml, and therefore
# not in the documented gauntlet either. The discriminator is **network
# access**, not scan freshness: the gauntlet happily runs the structurally
# identical ``bandit … -o <file>`` + ``check_bandit.py <file>`` pair, because
# bandit is a local AST walk. ``pip-audit`` queries a remote advisory database,
# so putting it in a pre-push block would make the documented self-review
# require the network.
_NIGHTLY_ONLY: dict[str, str] = {
    "check_pip_audit.py": (
        "Grades a pip-audit report. pip-audit queries a remote advisory DB, so this "
        "cannot sit in a pre-push gauntlet that must work offline. Runs in nightly.yml; "
        "Phase 16 S12 additionally gates publish.yml on it against the tagged SHA."
    ),
}


def _all_guards() -> list[str]:
    return sorted(p.name for p in _TOOLS.glob("check_*.py"))


def _run_scalars(workflow: Path) -> list[str]:
    """Every ``run:`` script in a workflow, with shell comments stripped.

    Parses the YAML rather than grepping the text. A guard named only in a
    ``- name:`` label or a comment block above the step is NOT wired, and the
    text scan could not tell the difference — commenting out a ``run:`` line
    while leaving the surrounding prose intact left this file green.
    """
    doc = yaml.safe_load(workflow.read_text(encoding="utf-8")) or {}
    scripts: list[str] = []
    for job in (doc.get("jobs") or {}).values():
        for step in (job or {}).get("steps") or []:
            run = (step or {}).get("run")
            if isinstance(run, str):
                scripts.append(re.sub(r"(?<!\S)#.*$", "", run, flags=re.M))
    return scripts


def _invocations(text: str) -> set[tuple[str, tuple[str, ...]]]:
    """``(guard name, sorted flags)`` pairs invoked in a shell script.

    Flags, not just names: a gauntlet that runs ``check_module_size.py``
    without ``--strict`` prints ``WARN:`` and exits 0 where CI fails.
    """
    found: set[tuple[str, tuple[str, ...]]] = set()
    for line in text.splitlines():
        for match in re.finditer(r"tools/" + _GUARD_NAME_RE.pattern, line):
            tail = line[match.end() :]
            tail = re.split(r"&&|\|\||;|\\\s*$", tail)[0]
            flags = tuple(sorted(token for token in tail.split() if token.startswith("-")))
            found.add((match.group(1), flags))
    return found


def _ci_guards() -> set[tuple[str, tuple[str, ...]]]:
    return _invocations("\n".join(_run_scalars(_WORKFLOWS / "ci.yml")))


def _workflow_guard_names() -> set[str]:
    names: set[str] = set()
    for workflow in sorted(_WORKFLOWS.glob("*.yml")):
        for name, _flags in _invocations("\n".join(_run_scalars(workflow))):
            names.add(name)
    return names


def _gauntlet_block(doc: Path) -> str:
    """The fenced self-review block, delimited by explicit HTML markers.

    Anchoring on the first ``check_import_origin.py`` match inside a ``bash``
    fence latched onto whichever fence came first in the file — in
    CONTRIBUTING.md a ``git clone`` block — reporting "omits 28 guard(s)" for a
    document that listed all 29.
    """
    blocks = re.findall(
        r"<!-- gauntlet:begin -->(.*?)<!-- gauntlet:end -->",
        doc.read_text(encoding="utf-8"),
        re.S,
    )
    assert len(blocks) == 1, f"{doc.name} must contain exactly one gauntlet:begin/end block, found {len(blocks)}"
    block = blocks[0]
    assert "```bash" in block, (
        f"{doc.name}'s gauntlet markers contain no ```bash fence. Prose between the markers would "
        "satisfy every parity assertion below while leaving the reader nothing to run."
    )
    # Strip shell comments, exactly as _run_scalars does for workflow YAML.
    # Without this a commented-out `# example: python3 tools/check_x.py` line
    # counts as a real invocation, so the two sides of the comparison were
    # using different definitions of "invoked".
    return re.sub(r"(?<!\S)#.*$", "", block, flags=re.M)


def _gauntlet_guards(doc: Path) -> set[tuple[str, tuple[str, ...]]]:
    return _invocations(_gauntlet_block(doc))


def _gauntlet_commands(doc: Path) -> list[str]:
    """Normalised command list from a gauntlet block, guards included.

    Normalisation makes the three documents comparable despite their different
    indentation, and makes ``python3 -m mypy`` (what a contributor runs, so the
    checkout's interpreter is used) comparable with ci.yml's bare ``mypy``.
    """
    body = _gauntlet_block(doc)
    body = body.split("```bash", 1)[-1].rsplit("```", 1)[0]
    body = body.replace("\\\n", " ")
    commands: list[str] = []
    for raw in re.split(r"&&|;", body):
        command = " ".join(raw.split())
        if not command:
            continue
        command = command.replace("python3 -m mypy", "mypy").replace("python3 ", "python ")
        commands.append(command)
    return commands


def test_guard_inventory_is_derived_not_asserted():
    """No hand-written floor.

    This used to assert ``len(guards) >= 19``, which is satisfied by any number
    at or above the count on the day it was written and therefore says nothing
    about drift. The real invariant is that the three documented gauntlets and
    ci.yml name the same set, asserted below, so all this needs to do is prove
    both inventories are non-empty and reachable.
    """
    assert _all_guards(), "no tools/check_*.py guards found — the inventory scan is broken"
    assert _ci_guards(), "ci.yml invokes no guards — the YAML scan is broken"


def test_every_guard_name_matches_the_shared_pattern():
    """One regex defines a guard name for every helper here.

    A guard whose filename the parity regex cannot match would sit in the
    inventory and be structurally invisible to every comparison below — wired
    into CI, absent from all three documents, and green.
    """
    unmatched = [g for g in _all_guards() if not _GUARD_NAME_RE.fullmatch(g)]
    assert not unmatched, (
        f"guard filename(s) the parity regex cannot see: {unmatched}. "
        "Rename them to lower-case/underscore, or widen _GUARD_NAME_RE — but never leave "
        "a guard the comparisons below silently skip."
    )


def test_every_guard_is_wired_or_allowlisted():
    wired = _workflow_guard_names()
    unwired = [g for g in _all_guards() if g not in wired and g not in _UNWIRED_ALLOWLIST]
    assert not unwired, (
        f"these guards are wired into no workflow and not allowlisted: {unwired}. "
        "Wire each into ci.yml (or nightly.yml) or add it to _UNWIRED_ALLOWLIST with a rationale."
    )


def test_allowlist_entries_still_exist():
    """An allowlisted guard that was deleted leaves a stale rationale — flag it."""
    missing = [g for g in _UNWIRED_ALLOWLIST if not (_TOOLS / g).exists()]
    assert not missing, f"_UNWIRED_ALLOWLIST names non-existent guard(s): {missing}"


def test_allowlist_rationales_are_non_empty():
    """The allowlist's own header requires a rationale; nothing read one.

    ``{"check_x.py": ""}`` satisfied every other test, which made "MUST carry a
    rationale" an unenforced documented requirement — the exact class this file
    exists to kill.
    """
    blank = [g for g, why in _UNWIRED_ALLOWLIST.items() if not why.strip()]
    assert not blank, f"_UNWIRED_ALLOWLIST entries with no written rationale: {blank}"


def test_allowlisted_guards_are_actually_unwired():
    """A guard that got wired must be removed from the allowlist (so the
    deferral note can't silently rot into a lie)."""
    wired = _workflow_guard_names()
    wired_but_allowlisted = [g for g in _UNWIRED_ALLOWLIST if g in wired]
    assert not wired_but_allowlisted, (
        f"these guards are wired AND allowlisted as unwired: {wired_but_allowlisted}. "
        "Remove them from _UNWIRED_ALLOWLIST."
    )


def test_nightly_only_guards_are_declared_and_honest():
    """The ci.yml/gauntlet exclusion is data with a reason, not a docstring aside."""
    ci_names = {name for name, _ in _ci_guards()}
    nightly_names = {name for name, _ in _invocations("\n".join(_run_scalars(_WORKFLOWS / "nightly.yml")))}
    for guard, why in _NIGHTLY_ONLY.items():
        assert (_TOOLS / guard).exists(), f"_NIGHTLY_ONLY names a non-existent guard: {guard}"
        assert why.strip(), f"_NIGHTLY_ONLY entry {guard} has no written rationale"
        assert guard in nightly_names, f"{guard} is declared nightly-only but nightly.yml does not run it"
        assert guard not in ci_names, (
            f"{guard} is declared nightly-only but ci.yml runs it — "
            "remove the _NIGHTLY_ONLY entry so the gauntlet parity tests cover it"
        )


def test_all_three_gauntlets_match_each_other():
    """CLAUDE.md, AGENTS.md and CONTRIBUTING.md must publish one gauntlet.

    Compared as full command lists, not guard sets: three hand-maintained
    restatements of one command block is exactly the shape that rots, and the
    non-guard steps (ruff, pytest, the dry-run, mypy) are the ones no other
    test covers.
    """
    lists = {doc: _gauntlet_commands(_REPO_ROOT / doc) for doc in _GAUNTLET_DOCS}
    reference = lists["CLAUDE.md"]
    for doc, got in lists.items():
        assert got == reference, (
            f"{doc}'s gauntlet diverges from CLAUDE.md's.\n"
            f"  only in {doc}: {[c for c in got if c not in reference]}\n"
            f"  missing from {doc}: {[c for c in reference if c not in got]}"
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
            f"{doc}'s gauntlet omits {len(missing)} ci.yml invocation(s): {missing}. "
            "Add them to the block — a local run that cannot fail where CI fails is not a "
            "self-review. The comparison includes flags: a guard listed without --strict counts "
            "as missing."
        )


def test_no_gauntlet_invents_a_guard_ci_does_not_run():
    """The forward direction, pinned to ci.yml specifically.

    Accepting any workflow would let a nightly-only guard satisfy this while
    being unrunnable as part of a pre-push check.
    """
    ci = _ci_guards()
    for doc in _GAUNTLET_DOCS:
        extra = sorted(_gauntlet_guards(_REPO_ROOT / doc) - ci)
        assert not extra, (
            f"{doc}'s gauntlet lists {extra}, which ci.yml does not run with those flags — "
            "either wire it into ci.yml or correct the documented block."
        )


def test_gauntlet_non_guard_steps_are_wired_into_ci():
    """ruff, pytest, the dry-run and the type gate are CI steps too.

    Comparing only ``tools/`` paths left these unpinned in both directions:
    deleting ``pytest tests/`` from a gauntlet, or the mypy step from ci.yml,
    was a green mutation.
    """
    ci_text = " ".join(" ".join(script.split()) for script in _run_scalars(_WORKFLOWS / "ci.yml"))
    required = (
        "ruff check .",
        "ruff format",
        "pytest",
        "--config config_template.yaml --dry-run",
        # The FULL mypy argv, including the probe. A fragment stopping one
        # argument short was the bug: deleting ` tests/typing/public_surface_probe.py`
        # from ci.yml left every test green while restoring the exact blind spot
        # the probe was created to close.
        "mypy --strict --follow-imports=silent forgelm/__init__.py forgelm/_version.py"
        " tests/typing/public_surface_probe.py",
    )
    commands = " \n".join(_gauntlet_commands(_REPO_ROOT / "CLAUDE.md"))
    for fragment in required:
        assert fragment in commands, f"the documented gauntlet no longer runs {fragment!r}"
        assert fragment in ci_text, f"ci.yml no longer runs {fragment!r}, but the gauntlet still tells contributors to"


def test_import_origin_guard_leads_every_gauntlet():
    """All three documents state this rule in prose; nothing enforced it.

    The guard asserts the premise every later step depends on — that the
    ``forgelm`` being imported is the checkout, not a stale site-packages copy.
    Demoting it below ``pytest`` was green.
    """
    for doc in _GAUNTLET_DOCS:
        first = _gauntlet_commands(_REPO_ROOT / doc)[0]
        assert "check_import_origin.py" in first, (
            f"{doc}'s gauntlet starts with {first!r}; every one of these documents states that the "
            "import-origin guard must lead, because it validates the premise of every later step."
        )


def test_no_gauntlet_short_circuits_on_failure():
    """A published self-review that reports success on a red tree is worse than none.

    ``&&`` and ``||`` are equal-precedence and left-associative in POSIX shell,
    so a trailing ``|| true`` binds to the ENTIRE preceding chain, and a ``;``
    then hands the block's status to whatever follows. The gauntlet briefly
    shipped in that shape: a failing ``pytest tests/`` produced exit 0. That is
    the ``|| true`` fake-green CLAUDE.md principle 6 outlaws, in the very
    documents that state the principle.

    ``|| true`` stays legitimate for a scanner that exits non-zero on any
    finding (bandit) when a tiering helper owns the policy — but only inside a
    ``{ …; }`` group, so the escape cannot leak to its neighbours.
    """
    for doc in _GAUNTLET_DOCS:
        # Deliberately NOT joining line continuations: the defect is per-line
        # shell syntax, and joining first turns the whole block into one line.
        body = _gauntlet_block(_REPO_ROOT / doc).split("```bash", 1)[-1].rsplit("```", 1)[0]
        assert ";" not in body.replace("; }", ""), (
            f"{doc}'s gauntlet uses a ';' separator outside a brace group. Every step must be "
            "joined with '&&' or the chain stops reporting the first failure."
        )
        for line in body.splitlines():
            if "|| true" not in line:
                continue
            stripped = line.strip().rstrip("\\").strip().removesuffix("&&").strip()
            assert stripped.startswith("{") and stripped.endswith("}") and "; }" in stripped, (
                f"{doc}'s gauntlet has a bare '|| true' in {stripped!r}. It binds to the whole "
                "preceding chain and swallows every earlier failure — wrap it as '{ cmd || true; }'."
            )


def test_gauntlet_prose_count_matches_the_inventory():
    """The count above each block is hand-written; derive it, do not trust it.

    Adding a 30th guard would otherwise leave three documents claiming 29 while
    correctly listing 30. Stated as a digit rather than a spelled-out word so
    this test needs no int-to-English table — a lookup table needing its own
    manual edit is the artefact class the rest of this file exists to remove.
    """
    count = len({name for name, _ in _ci_guards()})
    for doc in _GAUNTLET_DOCS:
        text = (_REPO_ROOT / doc).read_text(encoding="utf-8")
        match = re.search(r"All (\d+) must pass", text)
        assert match, (
            f"{doc} no longer states 'All <n> must pass' above its gauntlet — that sentence is what "
            "tells a reader how many gates they are running."
        )
        assert int(match.group(1)) == count, (
            f"{doc} says 'All {match.group(1)} must pass' but ci.yml runs {count} guards"
        )
