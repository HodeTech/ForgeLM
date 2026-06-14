"""Regression guard for F-P1-FAB-11 (H7).

``tools/regenerate_config_doc.py`` was cited as the "Phase 16
configuration-drift-detection control" in the QMS change-management
SOP (EN + TR), the ISO 27001 / SOC 2 design doc, and
``check_field_descriptions.py``'s own module docstring — but the file
has never existed in git history and no diff-guard for
``docs/reference/configuration.md`` was ever wired. An EU AI Act /
ISO 27001 / SOC 2 auditor following the SOP would find a documented
control that cannot be executed or evidenced.

H7 rewrote those four production-tree sites to describe the **real**
control (``tools/check_field_descriptions.py --strict`` +
manual doc review + ``check_bilingual_parity.py``). These tests pin
that the phantom citation does not creep back in and that the real
control file exists, per ``docs/standards/coding.md`` ("Every cited
path must exist in a CI check").

Scope note: this is deliberately a *targeted* test, not a blanket
"every cited ``tools/*.py`` path must exist" scanner. Other public-tree
files (``docs/design/library_api.md``, ``docs/roadmap/*``) carry
historical / forward-looking tool references that are out of scope for
H7; a blanket scanner belongs with the guard-apparatus work (H11).
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent

# The production tree as the public, version-controlled surface. The
# gitignored working-memory dirs (docs/analysis/, docs/marketing/) are
# excluded because their drafts legitimately discuss the phantom tool
# while diagnosing it.
_GITIGNORED_DOC_DIRS = (
    _REPO_ROOT / "docs" / "analysis",
    _REPO_ROOT / "docs" / "marketing",
)

# The four sites the fix touched, plus the CI workflow comment.
_PRODUCTION_SITES = (
    _REPO_ROOT / "tools" / "check_field_descriptions.py",
    _REPO_ROOT / "docs" / "qms" / "sop_change_management.md",
    _REPO_ROOT / "docs" / "qms" / "sop_change_management-tr.md",
    _REPO_ROOT / "docs" / "design" / "iso27001_soc2_alignment.md",
    _REPO_ROOT / ".github" / "workflows" / "ci.yml",
)


def _is_under_gitignored_doc_dir(path: Path) -> bool:
    return any(gitignored in path.parents for gitignored in _GITIGNORED_DOC_DIRS)


def test_check_field_descriptions_docstring_no_longer_cites_phantom_regenerator():
    """The scanner docstring must describe the real control, not a phantom companion."""
    text = (_REPO_ROOT / "tools" / "check_field_descriptions.py").read_text(encoding="utf-8")
    assert "regenerate_config_doc" not in text


def test_real_config_drift_control_tool_exists():
    """The control the docs now cite must be a real file on disk."""
    assert (_REPO_ROOT / "tools" / "check_field_descriptions.py").is_file()


def test_no_production_doc_cites_regenerate_config_doc_as_a_live_control():
    """No tracked doc/code may present ``regenerate_config_doc`` as an executable control.

    The SOP review-log rows (which explicitly record that the tool never
    existed) are the only sanctioned mentions; they appear in the
    append-only revision table, never in the §4.2 control description.
    """
    offenders: list[str] = []
    for path in (*_REPO_ROOT.glob("docs/**/*.md"), *_REPO_ROOT.glob("tools/*.py")):
        if _is_under_gitignored_doc_dir(path):
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if "regenerate_config_doc" not in line:
                continue
            # Permitted: the append-only SOP review-log row that records
            # the correction and states the tool never existed.
            if "never existed" in line or "hiç var olmadı" in line:
                continue
            rel = path.relative_to(_REPO_ROOT)
            offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert not offenders, "phantom regenerate_config_doc control citation(s) reintroduced:\n" + "\n".join(offenders)
