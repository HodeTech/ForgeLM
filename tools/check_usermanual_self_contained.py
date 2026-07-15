#!/usr/bin/env python3
"""Guard: user-manual pages must not link outside ``docs/usermanuals/``.

``docs/usermanuals/{en,tr}/`` is the source of truth for the static
user-manual viewer shipped on the marketing site (the SPA viewer in
``site/usermanual.html`` + ``site/js/guide.js`` consumes the JS bags
emitted by ``tools/build_usermanuals.py``).  The SPA only knows how to
resolve two kinds of in-content link:

- **SPA hash-router routes** of the form ``#/<section>/<page>`` — the
  viewer wires these to ``window.location.hash`` and re-renders.  The
  ``<section>/<page>`` pair MUST correspond to a real
  ``docs/usermanuals/<lang>/<section>/<page>.md`` file.
- **External HTTP(S) URLs** (``https://github.com/...`` etc.) — the
  browser opens them normally.

Anything else — repo-relative paths like ``../../../guides/foo.md``,
intra-manual relative paths like ``../concepts/choosing-trainer.md``,
or a SPA route that points at a non-existent page — renders as a
broken link inside the SPA (the browser tries to GET a path relative
to ``site/usermanual.html`` and 404s).

This guard walks every ``*.md`` under ``docs/usermanuals/`` and fails
on any link that would break in the SPA.  Pair with
``tools/check_anchor_resolution.py`` (which validates that the same
links resolve on disk) for the full link-health surface.

Exit codes (per ``tools/`` contract — NOT the public ``forgelm/`` set):

- ``0`` — every link is self-contained (or external).
- ``1`` — at least one broken / cross-directory link, or invalid args.

Usage::

    python3 tools/check_usermanual_self_contained.py
    python3 tools/check_usermanual_self_contained.py --strict
    python3 tools/check_usermanual_self_contained.py --quiet
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

# Match a Markdown inline link: ``[text](href)``.  Backtracking-free
# (negated char classes only) per docs/standards/regex.md.
_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")

# Hrefs we never validate (external destinations or pure within-page
# same-file anchors).
_SKIP_HREF_PATTERNS = (
    re.compile(r"^https?://"),  # external URL
    re.compile(r"^mailto:"),
    re.compile(r"^tel:"),
    re.compile(r"^javascript:"),
    # Pure same-file anchors (``#foo``) are resolved by the browser
    # within the currently rendered page — no cross-file concern.
    # Note: ``#/foo/bar`` is the SPA hash-router form and is validated
    # separately below, so the regex requires the char after ``#`` to
    # NOT be ``/``.
    re.compile(r"^#(?!/)"),
)

# SPA hash-router form recognised by site/js/guide.js — ``#/<section>/<page>``
# with an optional ``#<heading>`` suffix that the viewer appends via
# ``history.replaceState`` when the reader clicks a TOC entry (see
# site/js/guide.js:343).  Section/page slugs are kebab-case alphanumerics
# matching the directory naming under docs/usermanuals/<lang>/.
_SPA_ROUTE_RE = re.compile(
    r"^#/(?P<section>[a-z0-9][a-z0-9-]*)/(?P<page>[a-z0-9][a-z0-9-]*)"
    r"(?:#(?P<anchor>[A-Za-z0-9][A-Za-z0-9_.-]*))?$",
)


@dataclass(frozen=True)
class Link:
    """An inline Markdown link extracted from a user-manual page."""

    source: Path
    line: int
    text: str
    href: str


@dataclass(frozen=True)
class BrokenLink:
    """A link that would 404 in the SPA viewer, with the failure reason."""

    link: Link
    reason: str


def _is_skipped(href: str) -> bool:
    return any(pat.match(href) for pat in _SKIP_HREF_PATTERNS)


def _walk_manual_files(root: Path) -> Iterable[Path]:
    for path in sorted(p for p in root.rglob("*.md") if p.is_file()):
        yield path


def _extract_links(source: Path) -> Iterable[Link]:
    try:
        text = source.read_text(encoding="utf-8")
    except OSError:
        return
    in_code_block = False
    for line_no, line in enumerate(text.splitlines(), start=1):
        # Skip fenced code blocks — JSON / shell / yaml examples
        # legitimately mention ``docs/...`` paths as literal data,
        # not as clickable links.
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        for match in _LINK_RE.finditer(line):
            text_part = match.group(1)
            href_part = match.group(2).strip()
            if not href_part:
                continue
            # Markdown allows an optional title after the URL:
            # ``[text](url "title")`` or ``[text](url 'title')``.  Take
            # only the URL portion — the title is presentational and
            # would otherwise be appended to the href and fail validation.
            href_part = href_part.split(None, 1)[0]
            yield Link(source=source, line=line_no, text=text_part, href=href_part)


def _lang_root_of(source: Path, usermanuals_root: Path) -> Path | None:
    """Return ``docs/usermanuals/<lang>/`` containing ``source``, or None."""
    try:
        rel = source.relative_to(usermanuals_root)
    except ValueError:
        return None
    if not rel.parts:
        return None
    return usermanuals_root / rel.parts[0]


def _validate_spa_route(
    link: Link,
    href: str,
    usermanuals_root: Path,
) -> BrokenLink | None:
    """A ``#/<section>/<page>`` route must back onto a real manual file."""
    match = _SPA_ROUTE_RE.match(href)
    if match is None:
        return BrokenLink(
            link,
            f"hash-router route {href!r} is not in the canonical "
            "``#/<section>/<page>`` form recognised by the SPA viewer",
        )
    section = match.group("section")
    page = match.group("page")
    lang_root = _lang_root_of(link.source, usermanuals_root)
    if lang_root is None:
        # Shouldn't happen — we only call this for files under
        # docs/usermanuals/ — but degrade gracefully.
        return BrokenLink(link, "source file is not under docs/usermanuals/")
    target = lang_root / section / f"{page}.md"
    if not target.is_file():
        expected = f"docs/usermanuals/{lang_root.name}/{section}/{page}.md"
        return BrokenLink(
            link,
            f"SPA route {href!r} has no backing file at {expected}",
        )
    return None


def _validate_relative_path(
    link: Link,
    href: str,
    usermanuals_root: Path,
) -> BrokenLink | None:
    """Reject every repo-relative / intra-manual ``.md`` link.

    The SPA viewer (``site/js/guide.js``) does NOT intercept
    ``<a href="...md">`` clicks inside the rendered page body — they
    fire as plain browser navigation, which resolves the href against
    the viewer's own HTML URL (``site/guide.html``) and 404s for
    anything other than an external URL.  Two failure shapes:

    1. **Escapes the language root** (e.g. ``../../../guides/foo.md``,
       ``../../tr/training/sft.md``).  The target lives outside the
       manual altogether; the SPA can't render it.
    2. **Stays inside the language root** (e.g.
       ``../concepts/choosing-trainer.md``).  The disk file exists,
       but the SPA still doesn't intercept the click — same 404.

    Both forms must be replaced by either a SPA route
    ``#/<section>/<page>`` (preferred when an in-manual page covers
    the topic) or an absolute GitHub HTTPS URL.
    """
    # Strip optional ``#anchor`` — we don't validate per-target
    # anchors here (check_anchor_resolution.py owns that surface).
    path_part = href.split("#", 1)[0]
    if not path_part:
        return None  # pure anchor handled by the skip set above
    lang_root = _lang_root_of(link.source, usermanuals_root)
    if lang_root is None:
        return BrokenLink(link, "source file is not under docs/usermanuals/")
    # Resolve relative to the source file's directory.
    try:
        target = (link.source.parent / path_part).resolve()
    except OSError as exc:
        return BrokenLink(link, f"path resolve failed: {exc}")
    lang_root_resolved = lang_root.resolve()
    try:
        target.relative_to(lang_root_resolved)
    except ValueError:
        return BrokenLink(
            link,
            f"path {path_part!r} escapes docs/usermanuals/{lang_root.name}/ — "
            "user-manual pages must be self-contained; use a "
            "``#/<section>/<page>`` SPA route for in-manual targets or "
            "an absolute ``https://github.com/.../blob/main/...`` URL for "
            "files that live outside the manual",
        )
    if target.suffix.lower() == ".md":
        # Even when the target file exists under the lang root, an
        # intra-manual ``../section/page.md`` link is broken in the
        # SPA viewer — the click handler is never wired up for
        # ``.md`` hrefs.  Force the author onto the SPA route form.
        rel_to_lang = target.relative_to(lang_root_resolved)
        suggestion = f"#/{rel_to_lang.with_suffix('').as_posix()}"
        return BrokenLink(
            link,
            f"intra-manual relative ``.md`` path {path_part!r} would 404 in "
            "the SPA viewer (it does not intercept ``.md`` href clicks); "
            f"use the SPA route ``{suggestion}`` instead",
        )
    return None


def _resolve_link(link: Link, usermanuals_root: Path) -> BrokenLink | None:
    href = link.href
    if _is_skipped(href):
        return None
    if href.startswith("#"):
        # Anything starting with ``#`` other than a pure anchor (caught
        # by _is_skipped) is a SPA hash-router route.
        return _validate_spa_route(link, href, usermanuals_root)
    # Repo-relative path — must resolve under the same language root.
    return _validate_relative_path(link, href, usermanuals_root)


def _format_broken(broken: BrokenLink, repo_root: Path) -> str:
    try:
        rel = broken.link.source.resolve().relative_to(repo_root.resolve())
    except ValueError:
        rel = broken.link.source
    return f"{rel}:{broken.link.line}  [{broken.link.text}]({broken.link.href})  -> {broken.reason}"


def _build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Fail if any docs/usermanuals/ page links outside the manual "
            "tree.  The static site only renders SPA hash-router routes "
            "(``#/<section>/<page>``), external URLs, and same-file "
            "anchors; everything else 404s when the user clicks."
        ),
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="Repository root (default: parent of tools/).",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Strict mode: exit 1 on any broken link.  Default (no "
            "flag) is advisory: report to stdout but exit 0 — useful "
            "for local iteration.  CI (ci.yml validate job) already "
            "invokes this tool with --strict."
        ),
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress the OK summary on success.",
    )
    return parser


def _collect_broken(md_files: list[Path], usermanuals_root: Path) -> list[BrokenLink]:
    broken: list[BrokenLink] = []
    for md in md_files:
        for link in _extract_links(md):
            failure = _resolve_link(link, usermanuals_root)
            if failure is not None:
                broken.append(failure)
    return broken


def _report_broken(
    broken: list[BrokenLink],
    md_count: int,
    repo_root: Path,
    strict: bool,
) -> int:
    verdict = "FAIL" if strict else "WARN"
    print(f"{verdict}: cross-directory or unresolved user-manual links:")
    for entry in broken:
        print(f"  {_format_broken(entry, repo_root)}")
    print(
        f"\n{len(broken)} broken link(s) across {md_count} markdown file(s) under docs/usermanuals/.",
    )
    return 1 if strict else 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns 0 on success, 1 on any unresolved link."""
    args = _build_argparser().parse_args(argv)
    repo_root: Path = args.repo_root
    usermanuals_root = repo_root / "docs" / "usermanuals"
    if not usermanuals_root.is_dir():
        print(
            f"error: {usermanuals_root} does not exist or is not a directory.",
            file=sys.stderr,
        )
        return 1
    md_files = list(_walk_manual_files(usermanuals_root))
    broken = _collect_broken(md_files, usermanuals_root)
    if not broken:
        if not args.quiet:
            print(
                f"OK: {len(md_files)} user-manual page(s) checked; every link is self-contained or external.",
            )
        return 0
    return _report_broken(broken, len(md_files), repo_root, args.strict)


if __name__ == "__main__":
    sys.exit(main())
