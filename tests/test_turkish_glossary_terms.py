"""The Turkish docs follow the owner's glossary decisions in docs/standards/localization.md.

The site's Turkish strings are pinned by tests/site/wizard_monitoring.test.cjs; this
pins the same decisions across the Turkish docs, where the superseded terms
otherwise come back with every copied paragraph. Only the public Turkish trees
are read, by name.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DOCS = _REPO_ROOT / "docs"


def _turkish_docs() -> List[Path]:
    files = [*_DOCS.glob("*-tr.md")]
    for tree in ("guides", "reference", "qms", "roadmap"):
        files += (_DOCS / tree).rglob("*-tr.md")  # docs/guides/cpu-demo/README-tr.md too
    files += (_DOCS / "usermanuals" / "tr").rglob("*.md")
    return sorted(files)


# Each superseded spelling, and the decision that replaced it.
_SUPERSEDED = [
    (re.compile(r"pazar sonrası", re.IGNORECASE), "post-market monitoring is 'piyasaya arz sonrası izleme'"),
    (re.compile(r"Annex IV,? (?:madde|point)s? \d", re.IGNORECASE), "an Annex IV point is 'Annex IV §N'"),
    (re.compile(r"aktarım sistemi", re.IGNORECASE), "a metrics exporter is 'exporter'"),
    (re.compile(r"trend takib", re.IGNORECASE), "trend tracking is 'trend izleme', the user manual's term"),
    (re.compile("’"), "a suffix takes the straight apostrophe (')"),
]


def test_the_turkish_trees_are_found():
    names = {path.relative_to(_DOCS).as_posix() for path in _turkish_docs()}
    for expected in (
        "qms/sop_incident_response-tr.md",
        "usermanuals/tr/operations/gpu-cost.md",
        "guides/cpu-demo/README-tr.md",
    ):
        assert expected in names


@pytest.mark.parametrize("pattern,decision", _SUPERSEDED, ids=[decision for _, decision in _SUPERSEDED])
def test_no_superseded_term_remains(pattern, decision):
    hits = [
        f"{path.relative_to(_REPO_ROOT).as_posix()}:{number}: {line.strip()[:80]}"
        for path in _turkish_docs()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if pattern.search(line)
    ]
    assert hits == [], f"{decision}:\n" + "\n".join(hits)
