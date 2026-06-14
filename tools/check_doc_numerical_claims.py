#!/usr/bin/env python3
"""Wave 6 / Faz 31 — numerical-drift detector for docs claims.

Inventories canonical counts from code/configs and diffs against
numerical claims in user-facing markdown. Catches the drift family
flagged by the 2026-05-07 docs audit: secret-family count, trainer
count, quickstart-template count, webhook event count.

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

Plan reference: 2026-05-07 docs audit §10 (CI gate proposals) gate #5.
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
}


def _to_int(s: str) -> Optional[int]:
    """Convert ``"5"`` or ``"five"`` to ``5``. Return None if not a count."""
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
    """Return every mismatch that ``pattern`` finds on a single line."""
    found: List[Mismatch] = []
    for match in pattern.finditer(line):
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

    # Each rule binds a phrase shape to one of the canonical scrapes
    # above. Phrases anchor on the *qualifier* (e.g. "webhook" before
    # "events") so generic numbers don't false-positive: "9 secret
    # families" matches; "9 prompts" doesn't; "Six events" without a
    # webhook/wire-format qualifier doesn't either.
    rules: List[Tuple[re.Pattern[str], str]] = [
        # "9 secret families", "nine secret families", "9 secret patterns"
        (
            re.compile(
                r"\b(?P<count>\d+|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+secret\s+(?:families|patterns)",
                re.IGNORECASE,
            ),
            "secret_families",
        ),
        # "6 trainer types", "six trainers". Anchor on standalone
        # numeric/word counts to avoid matching e.g. "Phase 6" or
        # version numbers.
        (
            re.compile(
                r"(?<!\.)(?<!\d)\b(?P<count>\d+|two|three|four|five|six|seven|eight|nine|ten)\s+trainer(?:\s+type)?s\b",
                re.IGNORECASE,
            ),
            "trainer_types",
        ),
        # "5 (first-class )?quickstart templates" — require either
        # "quickstart" or "first-class" as qualifier so generic
        # "0 templates" / "Wave 0 templates" doesn't match.
        (
            re.compile(
                r"\b(?P<count>\d+|two|three|four|five|six|seven)\s+(?:first-class\s+|quickstart\s+|bundled\s+)templates",
                re.IGNORECASE,
            ),
            "templates",
        ),
        # "5 webhook events", "five wire-format events" — qualifier
        # MUST be one of webhook / wire-format so audit-event /
        # erasure-event counts don't false-positive.
        (
            re.compile(
                r"\b(?P<count>\d+|two|three|four|five|six)\s+(?:wire-format|webhook|lifecycle)\s+events?\b",
                re.IGNORECASE,
            ),
            "webhook_events",
        ),
    ]

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
