"""The Turkish docs keep out the spellings the owner's glossary decisions replaced.

docs/standards/localization.md records the decisions, and
tests/site/wizard_monitoring.test.cjs pins them in the site's Turkish strings.
This keeps their superseded spellings out of the Turkish docs, where they
otherwise come back with every copied paragraph. A decision that needs judgement
in general prose, such as *uyarı* for a warning, is left to review; only the
spellings that are wrong wherever they appear are pinned. Only the public
Turkish trees are read, by name.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DOCS = _REPO_ROOT / "docs"


def _turkish_docs() -> List[Path]:
    files = [*_DOCS.glob("*-tr.md")]  # roadmap-tr.md and product_strategy-tr.md; docs/roadmap/ has no mirrors
    for tree in ("guides", "reference", "qms"):
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
    (re.compile(r"\bEk IV\b"), "Annex IV keeps its name, as in 'Annex IV §N'"),
    (re.compile(r"piyasaya sunul", re.IGNORECASE), "placing on the market is 'piyasaya arz'"),
    (
        re.compile(r"\b(?:drift|eşik|maliyet|bütçe|webhook)\w* uyarı", re.IGNORECASE),
        "a drift, threshold or cost alert is 'alarm', and a webhook's message 'bildirim'",
    ),
    (
        re.compile(r"Yapay Zeka (?:Yasası|Kanunu)|Yapay Zekâ Kanunu|\bAB YZ\b"),
        "the Act is 'EU AI Act' or 'AB Yapay Zekâ Yasası'",
    ),
    (re.compile(r"2024/1689 sayılı (?!Tüzü)"), "Regulation (EU) 2024/1689 is '(AB) 2024/1689 sayılı Tüzük'"),
]


def test_the_turkish_trees_are_found():
    """A renamed or deleted tree must fail here, not silently drop out of the scan."""
    names = {path.relative_to(_DOCS).as_posix() for path in _turkish_docs()}
    for tree in ("guides/", "reference/", "qms/", "usermanuals/tr/", "roadmap-tr.md", "product_strategy-tr.md"):
        assert any(name.startswith(tree) for name in names), f"no Turkish page found under docs/{tree}"
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
