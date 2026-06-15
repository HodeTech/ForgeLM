#!/usr/bin/env python3
"""Wave 6 / Faz 31 — numerical-drift detector for docs claims.

Inventories canonical counts from code/configs and diffs against
numerical claims in user-facing markdown. Catches a known drift family:
secret-family count, trainer count, quickstart-template count, webhook
event count — each scraped from its canonical source so a doc claim that
disagrees fails the gate.

Each check has the form: scrape a known integer from canonical source
(`forgelm/...py` AST or `forgelm/templates/` directory listing), then
search docs for the exact phrase shape it usually appears as, and
report any mismatch.

Exit codes (per ``tools/`` contract — NOT the public 0/1/2/3/4 surface
that ``forgelm/`` honours):

- ``0`` — every numerical claim matches its canonical source.
- ``1`` — at least one claim diverges.

Usage::

    python3 tools/check_doc_numerical_claims.py
    python3 tools/check_doc_numerical_claims.py --strict   # alias of default
    python3 tools/check_doc_numerical_claims.py --quiet    # silent on success
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
FORGELM = REPO_ROOT / "forgelm"
DOCS = REPO_ROOT / "docs"
TEMPLATES = REPO_ROOT / "forgelm" / "templates"


@dataclass(frozen=True)
class Mismatch:
    """One numerical claim in docs that disagrees with the canonical source."""

    canonical_label: str
    canonical_value: int
    found_value: int
    file: Path
    line: int
    snippet: str


def _secret_patterns_dict_node(node: ast.AST) -> Optional[ast.Dict]:
    """Return the ``ast.Dict`` literal assigned to ``_SECRET_PATTERNS``, else None.

    Handles both annotated (``_SECRET_PATTERNS: Dict[str, ...] = {...}``)
    and plain (``_SECRET_PATTERNS = {...}``) assignment shapes.
    """
    if isinstance(node, ast.AnnAssign):
        target = node.target
        if isinstance(target, ast.Name) and target.id == "_SECRET_PATTERNS" and isinstance(node.value, ast.Dict):
            return node.value
        return None
    if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
        for t in node.targets:
            if isinstance(t, ast.Name) and t.id == "_SECRET_PATTERNS":
                return node.value
    return None


def canonical_secret_families() -> int:
    """Read ``_SECRET_PATTERNS`` from forgelm/data_audit/_secrets.py and
    return the number of families it ships with.
    """
    src = (FORGELM / "data_audit" / "_secrets.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        dict_node = _secret_patterns_dict_node(node)
        if dict_node is not None:
            return len(dict_node.keys)
    raise RuntimeError("Could not find _SECRET_PATTERNS in _secrets.py.")


def canonical_trainer_types() -> int:
    """Count Literal[...] members of ``trainer_type`` in ForgeConfig."""
    src = (FORGELM / "config.py").read_text(encoding="utf-8")
    # Look for: trainer_type: Literal["sft", "orpo", "dpo", "simpo", "kto", "grpo"]
    match = re.search(
        r"trainer_type:\s*Literal\[(?P<members>[^\]]+)\]",
        src,
    )
    if not match:
        raise RuntimeError("Could not find trainer_type Literal in config.py.")
    return len(re.findall(r'"[a-z]+"', match.group("members")))


def canonical_templates() -> int:
    """Count subdirectories under ``forgelm/templates/`` that contain a
    ``config.yaml`` (i.e. real template directories, not the
    ``__pycache__`` / ``__init__.py`` siblings).
    """
    return sum(1 for d in TEMPLATES.iterdir() if d.is_dir() and (d / "config.yaml").exists())


def canonical_webhook_events() -> int:
    """Count distinct ``event="..."`` strings in forgelm/webhook.py.

    The eight canonical events are the five single-stage lifecycle events —
    training.{start, success, failure, reverted}, approval.required — plus the
    three-event ``pipeline.*`` family (pipeline.{started, completed,
    stage_reverted}) the multi-stage orchestrator emits alongside them.
    """
    src = (FORGELM / "webhook.py").read_text(encoding="utf-8")
    events = set(re.findall(r'event\s*=\s*"([^"]+)"', src))
    return len(events)


_NUM_WORDS_TO_INT = {
    # English
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    # Turkish (F-P8-C-27): the TR mirrors phrase the same counts as
    # `beş`/`sekiz`/... so the guard must read them too, otherwise an
    # EN/TR fact divergence passes the gate.
    "iki": 2,
    "üç": 3,
    "dört": 4,
    "beş": 5,
    "altı": 6,
    "yedi": 7,
    "sekiz": 8,
    "dokuz": 9,
    "on": 10,
}

# Count words as a regex alternation, reused across rules so a new word
# (e.g. a Turkish addition above) only has to be added in one place.
_NUM_WORD_ALT = "|".join(sorted(_NUM_WORDS_TO_INT, key=len, reverse=True))

# Markdown emphasis markers wrapping a count — ``**five**`` / ``__beş__``.
# Stripped before matching so an emphasised number is still read
# (F-P8-C-27: the previous regexes required whitespace right after the
# number, so a trailing ``**`` defeated them).
_EMPHASIS_RE = re.compile(r"(\*{1,3}|_{1,3})")


def _strip_emphasis(line: str) -> str:
    """Remove Markdown bold/italic markers so ``**five**`` reads as ``five``."""
    return _EMPHASIS_RE.sub("", line)


def _to_int(s: str) -> Optional[int]:
    """Convert ``"5"`` or ``"five"``/``"beş"`` to ``5``. None if not a count."""
    s = s.strip().lower()
    if s.isdigit():
        return int(s)
    return _NUM_WORDS_TO_INT.get(s)


def _is_indexable_doc(path: Path) -> bool:
    """Skip research / marketing artefacts; only enforce on user-facing docs."""
    s = str(path)
    return "/analysis/" not in s and "/marketing/" not in s


def _scan_line_for_mismatches(
    pattern: re.Pattern[str],
    canonical_value: int,
    label: str,
    path: Path,
    line_idx: int,
    line: str,
) -> List[Mismatch]:
    """Return every mismatch that ``pattern`` finds on a single line.

    The line is emphasis-stripped before matching so ``**five**`` /
    ``__beş__`` are read; the reported snippet keeps the original text.
    """
    found: List[Mismatch] = []
    scan_line = _strip_emphasis(line)
    for match in pattern.finditer(scan_line):
        claimed = _to_int(match.group("count"))
        if claimed is None or claimed == canonical_value:
            continue
        found.append(
            Mismatch(
                canonical_label=label,
                canonical_value=canonical_value,
                found_value=claimed,
                file=path,
                line=line_idx,
                snippet=line.strip()[:120],
            )
        )
    return found


def search_doc_claims(pattern: re.Pattern[str], canonical_value: int, label: str) -> List[Mismatch]:
    """Scan all docs for a claim matching ``pattern``; report any whose
    captured number disagrees with ``canonical_value``.
    """
    out: List[Mismatch] = []
    for path in sorted(DOCS.rglob("*.md")):
        if not _is_indexable_doc(path):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line_idx, line in enumerate(text.splitlines(), 1):
            out.extend(_scan_line_for_mismatches(pattern, canonical_value, label, path, line_idx, line))
    return out


def build_rules() -> List[Tuple[re.Pattern[str], str]]:
    """Build the ``(pattern, canonical_label)`` scan rules.

    Module-level (not buried in ``main``) so the regexes are unit-testable
    against synthetic claim strings without invoking the full doc scan
    (F-P8-C-27 regression coverage).

    Each rule binds a phrase shape to one of the canonical scrapes.
    Phrases anchor on the *qualifier* (e.g. "webhook" before "events") so
    generic numbers don't false-positive: "9 secret families" matches;
    "9 prompts" doesn't; "Six events" without a webhook/wire-format
    qualifier doesn't either. Lines are emphasis-stripped before matching
    (see :func:`_scan_line_for_mismatches`), so ``**five**`` reads as
    ``five``.
    """
    return [
        # "9 secret families", "nine secret families", "9 secret patterns"
        (
            re.compile(
                rf"\b(?P<count>\d+|{_NUM_WORD_ALT})\s+secret\s+(?:families|patterns)",
                re.IGNORECASE,
            ),
            "secret_families",
        ),
        # "6 trainer types", "six trainers". Anchor on standalone
        # numeric/word counts to avoid matching e.g. "Phase 6" or
        # version numbers.
        (
            re.compile(
                rf"(?<!\.)(?<!\d)\b(?P<count>\d+|{_NUM_WORD_ALT})\s+trainer(?:\s+type)?s\b",
                re.IGNORECASE,
            ),
            "trainer_types",
        ),
        # "5 (first-class )?quickstart templates" — require either
        # "quickstart" or "first-class" as qualifier so generic
        # "0 templates" / "Wave 0 templates" doesn't match.
        (
            re.compile(
                rf"\b(?P<count>\d+|{_NUM_WORD_ALT})\s+(?:first-class\s+|quickstart\s+|bundled\s+)templates",
                re.IGNORECASE,
            ),
            "templates",
        ),
        # "5 webhook events", "**five** wire-format events",
        # "**sekiz** webhook event'i" — qualifier MUST be one of
        # webhook / wire-format / lifecycle so audit-event / erasure-event
        # counts don't false-positive. The count alternation includes the
        # Turkish number words and the line is emphasis-stripped upstream,
        # so bold-wrapped EN and TR phrasings are both caught (F-P8-C-27).
        # ``event'?i?`` tolerates the Turkish possessive suffix
        # (``event'i``) and a missing plural ``s``; ``olay(ı|lar)`` covers
        # the alternate Turkish phrasing "N webhook olayı".
        (
            re.compile(
                rf"\b(?P<count>\d+|{_NUM_WORD_ALT})\s+(?:wire-format|webhook|lifecycle)\s+"
                r"(?:event(?:'?[is]|s)?|olay(?:ı|lar)?)\b",
                re.IGNORECASE,
            ),
            "webhook_events",
        ),
    ]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Scan docs/ for numerical claims that disagree with canonical "
            "code/config sources (secret families, trainer types, "
            "templates, webhook events)."
        ),
    )
    parser.add_argument("--strict", action="store_true", help="Alias of default; exits 1 on drift.")
    parser.add_argument("--quiet", action="store_true", help="Suppress success summary.")
    args = parser.parse_args(argv)

    canonical: Dict[str, int] = {
        "secret_families": canonical_secret_families(),
        "trainer_types": canonical_trainer_types(),
        "templates": canonical_templates(),
        "webhook_events": canonical_webhook_events(),
    }

    rules = build_rules()

    mismatches: List[Mismatch] = []
    for pattern, label in rules:
        mismatches.extend(search_doc_claims(pattern, canonical[label], label))

    if mismatches:
        print(f"FAIL: {len(mismatches)} numerical claim(s) disagree with canonical source.")
        for m in mismatches:
            rel = m.file.relative_to(REPO_ROOT) if m.file.is_relative_to(REPO_ROOT) else m.file
            print(f"\n  {rel}:{m.line}  [{m.canonical_label}: canonical={m.canonical_value}, found={m.found_value}]")
            print(f"    {m.snippet}")
        return 1

    if not args.quiet:
        scrapes = ", ".join(f"{k}={v}" for k, v in canonical.items())
        print(f"OK: every numerical doc claim matches canonical scrapes ({scrapes}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
