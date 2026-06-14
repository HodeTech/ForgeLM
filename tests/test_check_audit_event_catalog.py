"""Tests for tools/check_audit_event_catalog.py (W0/C7 / F-P8-C-01).

The audit-event catalog guard cross-checks every dotted audit event emitted in
``forgelm/`` against the canonical table in
``docs/reference/audit_event_catalog.md`` (in both directions). Before this
package it FAILED at HEAD, was wired into no workflow, and had no own test —
so six ``pipeline.*`` stage events drifted into the code uncatalogued with zero
CI tripwire. These tests pin: the happy path, the undocumented-event and
ghost-row failure modes, the config-path false-positive fix (F-P8-C-12), that
the real repo is in sync, and that the guard is wired into CI + the gauntlet.
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
