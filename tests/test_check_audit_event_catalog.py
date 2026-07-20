"""Tests for tools/check_audit_event_catalog.py (W0/C7 / F-P8-C-01).

The audit-event catalog guard cross-checks every dotted audit event emitted in
``forgelm/`` against the canonical table in
``docs/reference/audit_event_catalog.md`` (in both directions). Before this
package it FAILED at HEAD, was wired into no workflow, and had no own test —
so six ``pipeline.*`` stage events drifted into the code uncatalogued with zero
CI tripwire. These tests pin: the happy path, the undocumented-event and
ghost-row failure modes, the config-path false-positive fix (F-P8-C-12), that
the real repo is in sync, and that the guard is wired into CI + the gauntlet.

A later round found the guard itself committing the defect it polices. Its
code-scan and catalog-scan regexes were built from one hardcoded
``_EVENT_NAMESPACES`` tuple, and that tuple omitted ``evaluation`` — so the
live Article 12 event ``evaluation.loss_gate_completed`` was invisible to
*both* sides at once. Symmetric blindness reads as agreement: zero found on
each side, guard green, and neither renaming the event in code nor deleting
its catalog row would have tripped anything. The namespace list was deleted
rather than extended (a hand-maintained list feeding a drift detector is the
same defect one level up), and the tests below pin the deletion, the empty-scan
tripwire, the loud-failure asymmetry of the one remaining list, and the
honesty of the docstring and success line.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_TOOL_PATH = _PROJECT_ROOT / "tools" / "check_audit_event_catalog.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("check_audit_event_catalog", _TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture(scope="module")
def tool():
    return _load_tool()


def _make_root(tmp_path: Path, source: str) -> Path:
    root = tmp_path / "forgelm"
    root.mkdir()
    (root / "mod.py").write_text(source, encoding="utf-8")
    return root


def _make_catalog(tmp_path: Path, event_names) -> Path:
    rows = "\n".join(f"| `{name}` | when emitted | payload | 12 |" for name in event_names)
    catalog = tmp_path / "audit_event_catalog.md"
    catalog.write_text(
        "# Catalog\n\n| Event | When | Payload | Article |\n|---|---|---|---|\n" + rows + "\n",
        encoding="utf-8",
    )
    return catalog


def _run(tool, root: Path, catalog: Path) -> int:
    return tool.main(["--forgelm-root", str(root), "--catalog", str(catalog), "--quiet"])


def test_in_sync_passes(tool, tmp_path):
    """Emitted events ≡ catalog rows → exit 0."""
    root = _make_root(tmp_path, 'def f():\n    log_event("training.started")\n')
    catalog = _make_catalog(tmp_path, ["training.started"])
    assert _run(tool, root, catalog) == 0


def test_undocumented_event_fails(tool, tmp_path):
    """An event emitted in code but absent from the catalog → exit 1."""
    root = _make_root(
        tmp_path,
        'def f():\n    log_event("training.started")\n    self._audit_event("pipeline.stage_gated", x=1)\n',
    )
    catalog = _make_catalog(tmp_path, ["training.started"])  # stage_gated missing
    assert _run(tool, root, catalog) == 1


def test_ghost_catalog_row_fails(tool, tmp_path):
    """A catalog row no code path emits ("ghost row") → exit 1."""
    root = _make_root(tmp_path, 'def f():\n    log_event("training.started")\n')
    catalog = _make_catalog(tmp_path, ["training.started", "pipeline.never_emitted"])
    assert _run(tool, root, catalog) == 1


def test_config_path_literal_not_flagged(tool, tmp_path):
    """F-P8-C-12 regression: a dotted config-path literal inside an error
    message (``'training.output_dir'``) shares an event namespace but is NOT an
    emission, so it must not be counted as an undocumented event."""
    root = _make_root(
        tmp_path,
        "def f():\n"
        '    log_event("training.started")\n'
        "    raise ValueError(\"each stage needs a unique 'training.output_dir' value.\")\n",
    )
    catalog = _make_catalog(tmp_path, ["training.started"])  # output_dir intentionally absent
    assert _run(tool, root, catalog) == 0


def test_constant_declaration_is_detected(tool, tmp_path):
    """Events emitted via constant indirection (``_EVT_X = "ns.name"``) are
    detected at the declaration site, so an undocumented constant fails."""
    root = _make_root(tmp_path, '_EVT_REVERT = "model.reverted"\n')
    catalog = _make_catalog(tmp_path, ["training.started"])  # model.reverted missing
    assert _run(tool, root, catalog) == 1


def test_event_keyword_form_is_detected(tool, tmp_path):
    """The webhook ``event="ns.name"`` keyword form is detected."""
    root = _make_root(tmp_path, 'def f():\n    self._send(event="training.start")\n')
    catalog = _make_catalog(tmp_path, ["training.started"])  # training.start missing
    assert _run(tool, root, catalog) == 1


def test_real_repo_catalog_in_sync(tool):
    """The shipped forgelm/ tree and catalog must be in sync — this is the
    live tripwire C7 wired into CI."""
    code = tool.main(
        [
            "--forgelm-root",
            str(_PROJECT_ROOT / "forgelm"),
            "--catalog",
            str(_PROJECT_ROOT / "docs" / "reference" / "audit_event_catalog.md"),
            "--quiet",
        ]
    )
    assert code == 0, "audit-event catalog drifted from forgelm/ — run tools/check_audit_event_catalog.py"


def test_novel_namespace_is_visible_on_both_sides(tool, tmp_path):
    """The eighth instance of "a check that reports success without examining
    its subject".

    Both regexes used to be built from one hardcoded ``_EVENT_NAMESPACES``
    tuple that omitted ``evaluation``. ``evaluation.loss_gate_completed`` was
    therefore invisible to the code scan AND the catalog scan simultaneously —
    zero found on each side, so the sides "agreed" and the guard printed OK.
    Renaming the event in code or deleting its catalog row changed nothing.

    A namespace no list has ever heard of must be reconciled like any other.
    """
    # Code emits it, catalog does not → must FAIL (code-only).
    root = _make_root(tmp_path, 'def f():\n    log_event("wholly_novel_ns.something_happened")\n')
    catalog = _make_catalog(tmp_path, ["training.started"])
    assert _run(tool, root, catalog) == 1

    # Catalog has it, code does not → must FAIL (ghost row).
    other = tmp_path / "b"
    other.mkdir()
    root2 = _make_root(other, 'def f():\n    log_event("training.started")\n')
    catalog2 = _make_catalog(other, ["training.started", "wholly_novel_ns.something_happened"])
    assert _run(tool, root2, catalog2) == 1


def test_evaluation_namespace_reconciles_in_real_repo(tool):
    """Regression pin for the specific event the hardcoded tuple hid.

    ``evaluation.loss_gate_completed`` is a live Article 12 event: declared in
    ``forgelm/trainer.py`` and documented in the catalog. Both sides must now
    see it. Asserting on the real tree (not a fixture) is deliberate — the
    fixture-only version of this test would have passed against the old guard.
    """
    emitted = {name for name, _ in tool.emitted_events(_PROJECT_ROOT / "forgelm")}
    catalogued = tool.catalogued_events(_PROJECT_ROOT / "docs" / "reference" / "audit_event_catalog.md")
    assert "evaluation.loss_gate_completed" in emitted
    assert "evaluation.loss_gate_completed" in catalogued


def test_no_hardcoded_namespace_list(tool):
    """The fix is the *deletion* of the namespace whitelist, not an extra
    entry in it. A future edit that reintroduces a hand-maintained namespace
    list feeding both regexes recreates the exact blind spot, so fail here.
    """
    assert not hasattr(tool, "_EVENT_NAMESPACES"), (
        "a hardcoded namespace list is back — it feeds both the code-scan and "
        "catalog-scan regexes, so anything it omits is invisible to both sides "
        "and the guard stays green. Match any dotted name instead."
    )


def test_empty_scan_fails_instead_of_reporting_ok(tool, tmp_path):
    """Two empty sets reconcile perfectly. A guard that prints OK over a tree
    it never read is the defect this whole cycle keeps finding, so an empty
    code side or an empty catalog side is a hard failure, not a pass."""
    empty_root = tmp_path / "forgelm"
    empty_root.mkdir()
    empty_catalog = _make_catalog(tmp_path, [])
    # No .py files at all.
    assert _run(tool, empty_root, empty_catalog) == 1
    # A .py file that emits nothing (regex broke / tree moved).
    (empty_root / "mod.py").write_text("x = 1\n", encoding="utf-8")
    assert _run(tool, empty_root, empty_catalog) == 1
    # Code emits, but the catalog table format stopped parsing.
    (empty_root / "mod.py").write_text('log_event("training.started")\n', encoding="utf-8")
    assert _run(tool, empty_root, empty_catalog) == 1


def test_filename_exclusion_failure_is_loud_not_silent(tool, tmp_path):
    """``_NON_EVENT_SECOND_SEGMENTS`` is the last hand-maintained list in the
    guard. It is applied to the code side only, never the catalog side, so a
    wrong entry cannot blind both halves at once: the swallowed event's catalog
    row becomes an unmatched ghost and the run fails. Pin that asymmetry."""
    # A genuine event is present on both sides so the empty-scan tripwire
    # cannot fire and mask the result — this test must fail for the ghost-row
    # reason specifically, not because nothing was found.
    root = _make_root(
        tmp_path,
        'def f():\n    log_event("training.started")\n    log_event("data.jsonl")\n',
    )
    catalog = _make_catalog(tmp_path, ["training.started", "data.jsonl"])
    assert _run(tool, root, catalog) == 1, (
        "the filename exclusion was applied to the catalog side too, so a bad "
        "entry in it would silently hide an event from both sides — exactly "
        "the symmetric blindness that let the evaluation.* namespace vanish"
    )


def test_success_line_names_what_it_examined(tool, capsys):
    """A guard that says OK without naming its subject cannot be audited. The
    success line must report the files scanned and both artefact paths, and
    must disclose the two audit-shaped logs it does not cover."""
    tool.main(
        [
            "--forgelm-root",
            str(_PROJECT_ROOT / "forgelm"),
            "--catalog",
            str(_PROJECT_ROOT / "docs" / "reference" / "audit_event_catalog.md"),
        ]
    )
    out = capsys.readouterr().out
    assert "*.py file(s)" in out, "success line does not say how many files it read"
    assert "table row(s)" in out
    assert "both directions" in out
    assert "quickstart_audit.jsonl" in out, "success line hides a known blind spot"
    assert "safety_trend.jsonl" in out, "success line hides a known blind spot"


def test_docstring_does_not_overclaim(tool):
    """The docstring used to promise it inventories "every dotted-namespace
    audit event emitted by forgelm/" — a claim it could not support, and which
    was false for ``evaluation.*`` at the time it was written."""
    doc = tool.__doc__ or ""
    assert "every dotted-namespace audit event emitted" not in doc
    assert "quickstart" in doc, "docstring must name what it does not examine"
    assert "safety_trend" in doc, "docstring must name what it does not examine"


def test_guard_wired_into_ci():
    """Meta-assertion: the guard is actually invoked by CI (it was previously
    in no workflow — the zero-detection half of F-P8-C-01)."""
    ci = (_PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "check_audit_event_catalog.py" in ci


def test_guard_wired_into_gauntlet():
    """Meta-assertion: the guard is in the CLAUDE.md self-review gauntlet (and
    its AGENTS.md mirror)."""
    for doc in ("CLAUDE.md", "AGENTS.md"):
        text = (_PROJECT_ROOT / doc).read_text(encoding="utf-8")
        assert "check_audit_event_catalog.py" in text, f"{doc} gauntlet missing the audit-catalog guard"
