#!/usr/bin/env python3
"""Wave 6 / Faz 31 — bilingual code-block + YAML-key parity guard.

Companion to ``tools/check_bilingual_parity.py`` (which only checks
H2/H3/H4 heading spine equivalence). This tool extends the parity
contract to *content* by checking, for every bilingual EN/TR pair:

1. **Code-block count parity** — same number of fenced ``` blocks
   in EN and TR. The audit's S1 / S3 patterns (TR collapsing
   multi-line YAML to flow style or merging two JSON blocks into
   one) surface as a code-block-count mismatch.
2. **YAML-key parity** — for every fenced ``yaml`` block at the same
   ordinal position in EN and TR, the **set of top-level keys** must
   match. Identifier translation (`yardımseverlik` vs `helpfulness`)
   surfaces here.

The pair registry is reused from ``tools/check_bilingual_parity.py``
(``_PAIRS``).

Exit codes (per ``tools/`` contract — NOT the public 0/1/2/3/4 surface
that ``forgelm/`` honours):

- ``0`` — every pair has matching fenced-block counts and matching
  per-block YAML-key sets.
- ``1`` — at least one pair diverges.

Usage::

    python3 tools/check_bilingual_code_blocks.py
    python3 tools/check_bilingual_code_blocks.py --strict   # alias of default
    python3 tools/check_bilingual_code_blocks.py --quiet    # silent on success

Rationale: bilingual docs must carry identical code blocks (commands /
config are English-only and must not diverge between the EN and TR
mirrors) — see ``docs/standards/localization.md``.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Set, Tuple

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    print(f"check_bilingual_code_blocks: PyYAML not importable ({exc}); skipping.", file=sys.stderr)
    sys.exit(0)

# Reuse the canonical pair registry from the sibling guard so a single
# source-of-truth governs which pairs are checked.
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from check_bilingual_parity import _PAIRS  # type: ignore
except ImportError as exc:  # pragma: no cover
    print(f"check_bilingual_code_blocks: cannot import _PAIRS ({exc}).", file=sys.stderr)
    sys.exit(1)


REPO_ROOT = Path(__file__).resolve().parent.parent

_FENCE_OPEN_RE = re.compile(r"^```(?P<lang>[a-zA-Z0-9_+-]*)\s*$")
_FENCE_CLOSE_RE = re.compile(r"^```\s*$")


@dataclass(frozen=True)
class Block:
    """One fenced code block extracted from a markdown file."""

    lang: str  # ``"yaml"``, ``"json"``, ``"shell"``, … or ``""``
    line_start: int  # 1-based line number of the opening fence
    body: str


def extract_blocks(path: Path) -> List[Block]:
    """Return every fenced block in ``path``."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    out: List[Block] = []
    i = 0
    while i < len(lines):
        m = _FENCE_OPEN_RE.match(lines[i])
        if m:
            start = i
            j = i + 1
            while j < len(lines) and not _FENCE_CLOSE_RE.match(lines[j]):
                j += 1
            if j < len(lines):
                body = "\n".join(lines[start + 1 : j])
                out.append(Block(lang=m.group("lang") or "", line_start=start + 1, body=body))
            i = j + 1
        else:
            i += 1
    return out


def yaml_top_level_keys(body: str) -> Optional[Set[str]]:
    """Return the set of top-level keys in a YAML block, or None if the
    block doesn't parse as a mapping. Uses ``yaml.safe_load`` and
    silently treats parse errors as "skip" — this guard is about parity,
    not validity (``check_yaml_snippets.py`` covers validity).
    """
    try:
        parsed = yaml.safe_load(body)
    except yaml.YAMLError:
        return None
    if not isinstance(parsed, dict):
        return None
    return set(parsed.keys())


def _yaml_key_diff_line(idx: int, en_blk, tr_blk) -> Optional[str]:
    """Return a diff line for one YAML block pair, or None when keys match.

    Returns None when either side fails to parse as a mapping (parity
    isn't meaningful) or when the top-level key sets are equal.
    """
    en_keys = yaml_top_level_keys(en_blk.body)
    tr_keys = yaml_top_level_keys(tr_blk.body)
    if en_keys is None or tr_keys is None:
        return None
    if en_keys == tr_keys:
        return None
    only_en = sorted(en_keys - tr_keys)
    only_tr = sorted(tr_keys - en_keys)
    pieces: List[str] = []
    if only_en:
        pieces.append(f"EN-only={only_en}")
    if only_tr:
        pieces.append(f"TR-only={only_tr}")
    return (
        f"  block #{idx + 1} (yaml): top-level keys diverge ({', '.join(pieces)}) "
        f"at EN:{en_blk.line_start} / TR:{tr_blk.line_start}"
    )


def _block_pair_diff(idx: int, en_blk, tr_blk) -> Optional[str]:
    """Return a diff line for one fenced-block pair, or None when aligned."""
    if en_blk.lang != tr_blk.lang:
        return (
            f"  block #{idx + 1}: lang differs (EN={en_blk.lang or '(unset)'}, "
            f"TR={tr_blk.lang or '(unset)'}) at EN:{en_blk.line_start} / TR:{tr_blk.line_start}"
        )
    if en_blk.lang.lower() in ("yaml", "yml"):
        return _yaml_key_diff_line(idx, en_blk, tr_blk)
    return None


def _pair_diff(en_path: Path, tr_path: Path) -> List[str]:
    """Return a list of human-readable diff lines for one pair."""
    en_blocks = extract_blocks(en_path)
    tr_blocks = extract_blocks(tr_path)
    if len(en_blocks) != len(tr_blocks):
        return [
            f"  fenced-block count: EN={len(en_blocks)}, TR={len(tr_blocks)} "
            f"(EN {en_path.name} has {len(en_blocks)} ``` blocks, "
            f"TR mirror has {len(tr_blocks)})"
        ]
    diff: List[str] = []
    for idx, (en_blk, tr_blk) in enumerate(zip(en_blocks, tr_blocks)):
        line = _block_pair_diff(idx, en_blk, tr_blk)
        if line is not None:
            diff.append(line)
    return diff


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify bilingual EN/TR doc pairs have matching fenced "
            "code-block counts AND, for yaml blocks at the same ordinal "
            "position, matching top-level keys."
        ),
    )
    parser.add_argument("--strict", action="store_true", help="Alias of default; exits 1 on drift.")
    parser.add_argument("--quiet", action="store_true", help="Suppress success summary.")
    args = parser.parse_args(argv)

    failures: List[Tuple[Path, Path, List[str]]] = []
    for en_rel, tr_rel in _PAIRS:
        en_path = REPO_ROOT / en_rel
        tr_path = REPO_ROOT / tr_rel
        if not (en_path.exists() and tr_path.exists()):
            print(
                f"check_bilingual_code_blocks: pair missing — {en_rel} / {tr_rel}.",
                file=sys.stderr,
            )
            return 1
        diff = _pair_diff(en_path, tr_path)
        if diff:
            failures.append((en_path, tr_path, diff))

    if failures:
        print(f"FAIL: {len(failures)} bilingual pair(s) have code-block / YAML-key drift.")
        for en_path, tr_path, diff in failures:
            print(f"\n  {en_path.name} ↔ {tr_path.name}:")
            for line in diff:
                print(line)
        return 1

    if not args.quiet:
        print(
            f"OK: all {len(_PAIRS)} bilingual pair(s) have matching fenced-block "
            f"counts AND matching YAML-key sets per block."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
