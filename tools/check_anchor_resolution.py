#!/usr/bin/env python3
"""Wave 4 / Faz 26 — markdown anchor + relative-link resolution guard.

Walks every Markdown file under ``docs/`` and validates that every
inline link of the form ``[text](path)`` resolves:

- **Repo-relative paths** (``../qms/foo.md``, ``forgelm/x.py``) must
  point at an existing file in the working tree.
- **Markdown anchor links** (``other.md#section-title``) must point
  at a header that exists in the target file.
- **External URLs** (``https://...``, ``http://...``) are skipped —
  out of scope for offline checks.
- **SPA hash-router fragments** (``#/reference/json-output``) are
  skipped — those resolve at site-render time, not in source.
- **Pure anchors** (``#section-title`` with no path) must resolve to
  a header in the SAME file.

Exit codes (per ``tools/`` contract — NOT the public 0/1/2/3/4
surface that ``forgelm/`` honours):

- ``0`` — every link resolves cleanly.
- ``1`` — at least one broken link or invalid argument.

Usage::

    python3 tools/check_anchor_resolution.py
    python3 tools/check_anchor_resolution.py --strict   # alias of default; exits 1 on drift
    python3 tools/check_anchor_resolution.py --quiet    # silent on success

Closure-plan reference: Faz 26 task #5
(``tools/check_anchor_resolution.py`` — markdown anchor'ları
resolve edilebilir mi diff).
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

# Match a Markdown inline link: ``[text](href)``.  The href may
# contain spaces (escaped as ``%20`` typically, but we accept raw
# here too).  We deliberately do NOT match reference-style links
# (`[text][ref]` + `[ref]: href`) — they are rare in this project
# and add parsing complexity for marginal coverage.
#
# Backtracking-free: ``\[([^\]]*)\]\(([^)]*)\)`` uses negated
# character classes only — no nested quantifiers.
_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")

# ATX heading parsing is fully procedural — see ``_parse_atx_heading``
# below.  We do NOT use a regex here because Sonar's python:S5852
# heuristic flags any pattern with two adjacent variable-length
# quantifiers (e.g. ``^(#{1,6}) +(.+)$``) as a polynomial-runtime
# ReDoS risk, even when the leading quantifier is bounded.  The
# procedural form has worst-case linear runtime in line length and
# leaves no regex surface for the heuristic to misclassify.

# Markdown image syntax (``![alt](src)``) reuses the link form but is
# checked the same way — every ``src`` must resolve.

# Skip patterns: anything matching these is treated as out-of-scope.
_SKIP_HREF_PATTERNS = (
    re.compile(r"^https?://"),  # external URL
    re.compile(r"^mailto:"),  # email
    re.compile(r"^#/"),  # SPA hash-router (user-manual viewer)
    re.compile(r"^tel:"),  # phone
    re.compile(r"^javascript:"),
)


@dataclass(frozen=True)
class Link:
    source: Path
    line: int
    text: str
    href: str


@dataclass(frozen=True)
class BrokenLink:
    link: Link
    reason: str


def _parse_atx_heading(line: str) -> str | None:
    """Return the heading body for an ATX heading line, or None.

    An ATX heading is 1-6 leading ``#`` characters followed by at
    least one whitespace character and then the body.  GFM also
    permits an optional trailing run of ``#`` decorators which we
    strip after the leading-hashes scan.

    Implemented as a single linear pass to avoid the ReDoS risk
    heuristic that flags adjacent variable-length quantifiers in
    regex form (Sonar python:S5852).

    Known divergence from CommonMark §4.2 (intentional): indented
    headings (1-3 leading spaces) are NOT recognised — project docs
    must place ``#`` at column 0.  The pre-rewrite regex form was
    column-anchored too, so this preserves Wave 4 behaviour.

    Edge case (F-W4FU-TR-05): ``## ##`` (all-hash heading) trims to
    an empty body after closing-hash strip; reject rather than
    register the empty slug as an anchor.
    """
    if not line or line[0] != "#":
        return None
    hash_count = 0
    while hash_count < len(line) and line[hash_count] == "#":
        hash_count += 1
    if hash_count == 0 or hash_count > 6:
        return None
    if hash_count >= len(line) or line[hash_count] != " ":
        return None
    body = line[hash_count + 1 :].strip()
    if not body:
        return None
    body = _normalise_heading_body(body)
    if not body:
        return None
    return body


def _normalise_heading_body(body: str) -> str:
    """Strip trailing whitespace + GFM closing-hash run from a heading body.

    ATX headings allow an optional sequence of trailing ``#`` characters
    (e.g. ``# title #``); GFM treats the closing run as decoration and
    the slug is derived from the body alone.  Done procedurally to
    avoid the multi-``\\s*`` regex pattern that would trigger Sonar's
    python:S5852 ReDoS heuristic.
    """
    trimmed = body.rstrip()
    while trimmed.endswith("#"):
        trimmed = trimmed[:-1].rstrip()
    return trimmed


def _slugify_heading(text: str) -> str:
    """GitHub-flavoured Markdown heading-anchor algorithm.

    GFM lowercases, strips most punctuation, replaces each whitespace
    character with a single dash, and preserves alphanumerics + dashes
    + underscores.  Crucially, GFM does **not** collapse runs of
    consecutive dashes: ``## Foo - Bar`` renders the anchor
    ``foo---bar`` (the surviving literal dash plus the two
    space-derived dashes), and ``## A  B`` (two spaces) renders
    ``a--b``.  An earlier version collapsed ``\\s+`` → one dash and ran
    a ``re.sub(r"-+", "-", …)`` pass, which diverged from GitHub and
    both green-lit links that 404 on github.com and rejected correct
    GFM-slug links (F-P8-C-14).  We now mirror GFM faithfully: one dash
    per whitespace char, no dash-run collapse.
    """
    slug = text.lower().strip()
    # Strip everything except alphanumeric, space, dash, underscore.
    slug = re.sub(r"[^\w\s-]", "", slug, flags=re.UNICODE)
    # Replace each whitespace character with a single dash (GFM does not
    # collapse a run of whitespace into one dash).
    slug = re.sub(r"\s", "-", slug)
    # Trim leading/trailing dashes (GFM keeps interior dash runs intact).
    return slug.strip("-")


def _extract_anchors(target: Path) -> set[str]:
    """Return the set of GFM-style header anchors defined in ``target``."""
    if not target.is_file():
        return set()
    anchors: set[str] = set()
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return set()
    for line in text.splitlines():
        body = _parse_atx_heading(line)
        if body is None:
            continue
        anchors.add(_slugify_heading(body))
    return anchors


def _walk_markdown_files(root: Path, excluded_dirs: tuple[Path, ...]) -> Iterable[Path]:
    """Yield every ``*.md`` under ``root``, sorted for determinism.

    Files under any directory in ``excluded_dirs`` (resolved-form
    comparison) are skipped — by default we strip gitignored working-
    memory directories so the checker validates only public docs.
    """
    excluded = tuple(p.resolve() for p in excluded_dirs)
    for path in sorted(p for p in root.rglob("*.md") if p.is_file()):
        resolved = path.resolve()
        if any(_is_under(resolved, ex) for ex in excluded):
            continue
        yield path


def _is_under(child: Path, ancestor: Path) -> bool:
    """Return True iff ``child`` is contained in ``ancestor`` (inclusive)."""
    try:
        child.relative_to(ancestor)
    except ValueError:
        return False
    return True


def _extract_links(source: Path) -> Iterable[Link]:
    try:
        text = source.read_text(encoding="utf-8")
    except OSError:
        return
    for line_no, line in enumerate(text.splitlines(), start=1):
        for match in _LINK_RE.finditer(line):
            text_part = match.group(1)
            href_part = match.group(2).strip()
            if not href_part:
                continue
            yield Link(source=source, line=line_no, text=text_part, href=href_part)


def _is_skipped(href: str) -> bool:
    return any(pat.match(href) for pat in _SKIP_HREF_PATTERNS)


def _split_path_anchor(href: str) -> tuple[str, str]:
    """Split ``path#anchor`` into ``(path, anchor)``.  Either may be empty."""
    if "#" not in href:
        return href, ""
    path, _, anchor = href.partition("#")
    return path, anchor


def _resolve_pure_anchor(link: Link, anchor_part: str) -> BrokenLink | None:
    """Pure ``#section`` link — target is the same file."""
    if not anchor_part:
        return BrokenLink(link, "empty href")
    anchors = _extract_anchors(link.source)
    if anchor_part not in anchors:
        return BrokenLink(link, f"anchor #{anchor_part!r} not found in {link.source.name}")
    return None


def _locate_target(link: Link, path_part: str, repo_root: Path) -> Path | BrokenLink:
    """Resolve a repo-relative path against the source's parent or repo root.

    Both candidate paths are constrained to live under ``repo_root`` —
    a `../../../etc/passwd`-style traversal that resolves outside the
    tree is treated as broken rather than silently followed.
    """
    repo_root_resolved = repo_root.resolve()

    def _under_repo_root(candidate: Path) -> bool:
        try:
            candidate.relative_to(repo_root_resolved)
        except ValueError:
            return False
        return True

    target = (link.source.parent / path_part).resolve()
    if target.exists() and _under_repo_root(target):
        return target
    # Fall back: maybe the path is repo-root-relative (legacy refs).
    alt = (repo_root / path_part).resolve()
    if alt.exists() and _under_repo_root(alt):
        return alt
    return BrokenLink(link, f"target file not found: {path_part!r}")


def _resolve_anchor_against_target(link: Link, target: Path, path_part: str, anchor_part: str) -> BrokenLink | None:
    """Validate ``anchor_part`` against the resolved target file."""
    if target.suffix.lower() != ".md":
        # Code-file anchors (e.g. forgelm/x.py#L42) are stale-line
        # references that drift on refactor.  Fail-loud for the
        # ``#L<digits>`` form, ignore other code-anchor forms (Sphinx
        # ``?`` queries, language-specific fragment schemes).
        if re.fullmatch(r"L\d+(?:-L\d+)?", anchor_part):
            return BrokenLink(
                link,
                f"line-number anchor #{anchor_part!r} on code file is brittle (use symbol references)",
            )
        return None
    anchors = _extract_anchors(target)
    if anchor_part not in anchors:
        return BrokenLink(link, f"anchor #{anchor_part!r} not found in {path_part!r}")
    return None


def _resolve_link(link: Link, repo_root: Path) -> BrokenLink | None:
    """Return ``None`` if the link resolves; otherwise a ``BrokenLink``."""
    href = link.href
    if _is_skipped(href):
        return None
    path_part, anchor_part = _split_path_anchor(href)
    if not path_part:
        return _resolve_pure_anchor(link, anchor_part)
    target_or_broken = _locate_target(link, path_part, repo_root)
    if isinstance(target_or_broken, BrokenLink):
        return target_or_broken
    if not anchor_part:
        return None
    return _resolve_anchor_against_target(link, target_or_broken, path_part, anchor_part)


def _format_broken(broken: BrokenLink) -> str:
    rel = broken.link.source
    return f"{rel}:{broken.link.line}  [{broken.link.text}]({broken.link.href})  → {broken.reason}"


def _build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate markdown anchor + relative-path links under docs/.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="Repository root (default: parent of tools/).",
    )
    parser.add_argument(
        "--scope",
        type=str,
        default="docs",
        help="Subdirectory under repo-root to scan (default: docs).",
    )
    parser.add_argument(
        "--exclude",
        type=str,
        action="append",
        default=None,
        help=(
            "Directory (relative to scope-dir) to skip.  Repeatable.  "
            "Default exclude list strips ``analysis/`` only — gitignored "
            "working-memory directories carry local-only path references "
            "and are excluded by default.  Passing ``--exclude`` REPLACES "
            "the default; empty values are ignored (default still applies), "
            "so use a non-matching path (e.g. ``--exclude __none__``) when "
            "the goal is to scan everything."
        ),
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Strict mode: exit 1 on any broken link.  Default (no "
            "flag) is advisory: report broken links to stdout but "
            "exit 0 — useful for local iteration.  CI (ci.yml "
            "validate job) already invokes this tool with --strict."
        ),
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress the OK summary on success.",
    )
    return parser


def _resolve_excludes(scope_dir: Path, exclude_arg: list[str] | None) -> tuple[Path, ...]:
    # Default exclude list — `analysis/` is a gitignored working-memory
    # directory with local-only path references that drift on every
    # PR-cycle audit.  Excluded by default so the checker validates
    # only public docs; subdirectories under it are caught transitively.
    #
    # Passing --exclude REPLACES the default rather than augmenting it
    # (call sites that need additional excludes pass the full list,
    # including ``analysis``); this matches the help-text contract.
    #
    # Empty / whitespace-only entries are filtered (an `--exclude ""`
    # would otherwise resolve to ``scope_dir`` itself and silently
    # exclude the entire tree).  When all entries filter out, fall
    # back to the default rather than scanning everything — the
    # opt-out for "scan everything" is `--exclude doesnotexist` or
    # any other path that won't match.
    if exclude_arg is None:
        specs: tuple[str, ...] = ("analysis",)
    else:
        cleaned = tuple(s for s in (entry.strip() for entry in exclude_arg) if s)
        specs = cleaned if cleaned else ("analysis",)
    return tuple((scope_dir / spec).resolve() for spec in specs)


def _collect_broken(md_files: list[Path], repo_root: Path) -> list[BrokenLink]:
    broken: list[BrokenLink] = []
    for md in md_files:
        for link in _extract_links(md):
            failure = _resolve_link(link, repo_root)
            if failure is not None:
                broken.append(failure)
    return broken


def _report_broken(broken: list[BrokenLink], md_count: int, scope: str, strict: bool) -> int:
    verdict = "FAIL" if strict else "WARN"
    print(f"{verdict}: broken anchor / relative-link references:")
    for entry in broken:
        print(f"  {_format_broken(entry)}")
    print(f"\n{len(broken)} broken link(s) across {md_count} markdown file(s) under {scope}/.")
    return 1 if strict else 0


def main(argv: list[str] | None = None) -> int:
    args = _build_argparser().parse_args(argv)

    repo_root = args.repo_root.resolve()
    scope_dir = (repo_root / args.scope).resolve()
    if not scope_dir.is_dir():
        print(f"error: scope directory not found: {scope_dir}", file=sys.stderr)
        return 1

    excluded_dirs = _resolve_excludes(scope_dir, args.exclude)
    md_files = list(_walk_markdown_files(scope_dir, excluded_dirs))
    broken = _collect_broken(md_files, repo_root)

    if broken:
        return _report_broken(broken, len(md_files), args.scope, args.strict)

    if not args.quiet:
        print(f"OK: {len(md_files)} markdown file(s) under {args.scope}/ have all anchors + relative links resolved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
