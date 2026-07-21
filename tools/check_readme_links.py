"""CI guard — the top-level README must survive PyPI rendering.

``pyproject.toml`` sets ``readme = "README.md"`` and configures no
long-description URL rewriting, so PyPI serves the file byte-for-byte on
``pypi.org/project/forgelm/``.  A *relative* href that resolves correctly
on GitHub (``docs/guides/quickstart.md``) is therefore resolved against
``pypi.org`` there and 404s — for exactly the audience that arrived by
running ``pip install forgelm``.

Before this guard existed the README carried 38 relative links, every one
of them dead on the PyPI project page, and **nothing in CI could see it**:

- ``tools/check_anchor_resolution.py`` defaults to ``--root docs`` and so
  never opens ``README.md`` (it reports "OK: 259 markdown file(s) under
  docs/" and means it);
- ``tools/check_source_path_refs.py`` does scan ``README.md``, but only
  for backticked *source* paths such as ``forgelm/trainer.py`` — never for
  Markdown hrefs;
- ``tools/check_doc_numerical_claims.py`` scanned ``docs/`` only when this
  guard was written (it now also checks README *counts*, but never *links*).

The project's single highest-traffic document was outside the coverage of
every guard that would have kept it honest.  That is why it accumulated
fourteen false claims while ``docs/`` stayed comparatively clean.

Two rules, both offline
-----------------------
1. **Absolute-https rule**, applied only to surfaces rendered off GitHub
   (today: ``README.md``).  Every Markdown link and image must use an
   absolute ``https://`` href.  Pure in-document anchors (``#section``)
   are allowed: they resolve wherever the document is served.

2. **Resolve-on-disk rule**, applied to every scanned surface.  Any href
   naming a path inside this repository — either
   ``https://github.com/HodeTech/ForgeLM/{blob,tree}/main/<path>`` or, on
   a GitHub-only surface, a plain relative path — must point at something
   that exists in this checkout.  This is what makes rule 1 safe to
   enforce: rewriting a link to an absolute URL would otherwise trade a
   link that 404s on PyPI for one that 404s everywhere, and no network
   access is needed to tell the difference.

Rule 1 is deliberately **not** applied to ``CONTRIBUTING.md``.  That file
is read on GitHub, where relative links are correct and idiomatic;
demanding absolute URLs there would be noise, and a guard that fires on
correct input gets disabled.  It still gets rule 2, which is the half no
other guard performs for it.

Run via::

    python tools/check_readme_links.py [--strict] [--repo-root DIR]

``--strict`` is accepted for symmetry with the other guards in this
directory; the checks are unconditional, so it changes nothing.
``--repo-root`` exists so the enforcement tests can drive ``main()``
against a temporary tree instead of monkeypatching module state.

Exit codes (per the ``tools/`` contract — NOT the public 0/1/2/3/4/5/6
surface that ``forgelm/`` honours):

- ``0`` — clean
- ``1`` — at least one violation, or a scanned surface could not be read
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import List, Optional, Tuple

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Top-level surfaces, mapped to whether the absolute-https rule applies.
#
# ``True`` means the file is rendered somewhere other than GitHub and a
# relative href would break there.  Only ``README.md`` qualifies today:
# ``pyproject.toml`` names it as the PyPI long description, and nothing
# else in the repo is shipped to a third-party renderer.
#
# ``False`` means the file is read on GitHub, where relative links are
# correct.  These surfaces still get the resolve-on-disk rule, which is
# the half no other guard performs for them: ``check_anchor_resolution.py``
# validates link targets under ``docs/`` only, so a broken relative link in
# ``CONTRIBUTING.md`` is invisible to CI without this entry.
_SURFACES: Tuple[Tuple[str, bool], ...] = (
    ("README.md", True),
    ("CONTRIBUTING.md", False),
)

_REPO_BLOB_PREFIX_RE = re.compile(r"^https://github\.com/HodeTech/ForgeLM/(?:blob|tree)/main/(?P<path>[^#?]+)")

# Markdown inline links and images: ``[text](href)`` / ``![alt](href)``.
# The href stops at whitespace or the closing paren so that titles
# (``[x](url "title")``) are not folded into the URL.
_LINK_RE = re.compile(r"!?\[(?P<text>[^\]]*)\]\((?P<href>[^)\s]+)")

# Reference-style link definitions: ``[label]: href``.
_LINK_DEF_RE = re.compile(r"^\s{0,3}\[(?P<label>[^\]]+)\]:\s*(?P<href>\S+)")

# Raw HTML links/images inside Markdown.  GitHub and PyPI both render inline
# HTML, so an ``<a href=…>`` or ``<img src=…>`` reaches a reader exactly like a
# Markdown link and rots exactly the same way — but ``_LINK_RE`` would never see
# it.  Matched here so both routes go through the same two rules.  The value is
# captured out of single or double quotes; an unquoted attribute is not valid
# HTML for a URL and is left for a stricter linter.
_HTML_ATTR_RE = re.compile(
    r"<(?:a|img|source|link)\b[^>]*?\b(?:href|src)\s*=\s*[\"'](?P<href>[^\"']+)[\"']", re.IGNORECASE
)

# Footnote definitions (``[^1]: text``) are prose, not links.  They match
# _LINK_DEF_RE's shape, so they are filtered by label rather than by
# weakening that pattern.
_FOOTNOTE_LABEL_RE = re.compile(r"^\^")

# Schemes that are legitimate in a rendered document even though they are
# not ``https``.  Anything else must be argued for here rather than waved
# through by loosening the check.
_ALLOWED_NON_HTTPS_PREFIXES: Tuple[str, ...] = ("mailto:",)


def _iter_hrefs(text: str) -> List[Tuple[int, str, str]]:
    """Yield ``(line_no, kind, href)`` for every link in *text*.

    Fenced code blocks are skipped: a ``pip install`` snippet or a sample
    config may legitimately contain bracket-paren sequences that are not
    links, and a shell transcript showing a relative path is documentation
    of a command, not a hyperlink the renderer will resolve.
    """
    found: List[Tuple[int, str, str]] = []
    in_fence = False
    fence_marker = ""
    for line_no, line in enumerate(text.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            if not in_fence:
                in_fence, fence_marker = True, marker
            elif marker == fence_marker:
                in_fence, fence_marker = False, ""
            continue
        if in_fence:
            continue
        for match in _HTML_ATTR_RE.finditer(line):
            found.append((line_no, "html-attr", match.group("href")))
        def_match = _LINK_DEF_RE.match(line)
        if def_match and not _FOOTNOTE_LABEL_RE.match(def_match.group("label")):
            found.append((line_no, "link-definition", def_match.group("href")))
            continue
        for match in _LINK_RE.finditer(line):
            found.append((line_no, "inline-link", match.group("href")))
    return found


def _resolve_violation(repo_root: Path, rel_path: str, line_no: int, target_path: str, href: str) -> Optional[str]:
    """Return a violation string when *target_path* does not resolve, else None.

    *target_path* is repo-relative.  Traversal outside the repository is
    reported rather than silently normalised, so a ``../..`` link cannot
    quietly pass by pointing at something on the maintainer's disk.
    """
    target = (repo_root / target_path).resolve()
    try:
        target.relative_to(repo_root.resolve())
    except ValueError:
        return f"{rel_path}:{line_no}  link escapes the repository: {href!r}"
    if not target.exists():
        return f"{rel_path}:{line_no}  link points at a path that does not exist: {target_path!r}"
    return None


def check_surface(repo_root: Path, rel_path: str, require_absolute: bool) -> List[str]:
    """Return human-readable violation strings for one surface."""
    path = repo_root / rel_path
    violations: List[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        # Fail closed: an unreadable surface is reported, never skipped.
        # A guard that goes green because it could not open its subject is
        # the exact defect this file was written to prevent.
        return [f"{rel_path}: could not read ({exc.__class__.__name__}: {exc})"]

    for line_no, kind, href in _iter_hrefs(text):
        if href.startswith("#"):
            continue  # in-document anchor — resolves wherever it is served
        if href.startswith(_ALLOWED_NON_HTTPS_PREFIXES):
            continue
        if not href.startswith("https://"):
            if require_absolute:
                violations.append(
                    f"{rel_path}:{line_no}  {kind} is not an absolute https URL: {href!r}\n"
                    f"      PyPI renders this file verbatim, so a relative href resolves "
                    f"against pypi.org and 404s."
                )
                continue
            if href.startswith("http://"):
                violations.append(f"{rel_path}:{line_no}  insecure http:// link: {href!r}")
                continue
            # Relative link on a GitHub-only surface: correct in form, so
            # check the one thing that can still be wrong — the target.
            problem = _resolve_violation(repo_root, rel_path, line_no, href.split("#", 1)[0], href)
            if problem:
                violations.append(problem)
            continue
        blob = _REPO_BLOB_PREFIX_RE.match(href)
        if blob:
            problem = _resolve_violation(repo_root, rel_path, line_no, blob.group("path"), href)
            if problem:
                violations.append(problem)
    return violations


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Accepted for symmetry with the other tools/ guards; the checks are unconditional.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=_REPO_ROOT,
        help="Directory to scan (default: the repository this file lives in).",
    )
    args = parser.parse_args(argv)
    repo_root: Path = args.repo_root

    all_violations: List[str] = []
    scanned = 0
    links_seen = 0
    for rel_path, require_absolute in _SURFACES:
        surface = repo_root / rel_path
        if not surface.exists():
            all_violations.append(f"{rel_path}: expected surface is missing from the checkout")
            continue
        scanned += 1
        try:
            links_seen += len(_iter_hrefs(surface.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError):
            pass  # check_surface reports the read failure with its cause
        all_violations.extend(check_surface(repo_root, rel_path, require_absolute))

    if all_violations:
        for violation in all_violations:
            print(f"  ✗ {violation}")
        print(
            f"\n{len(all_violations)} link violation(s) across {scanned} top-level surface(s).\n"
            "Fix: on README.md use absolute https://github.com/HodeTech/ForgeLM/blob/main/<path>\n"
            "URLs for in-repo targets; on every surface make sure each target exists.\n"
            "Rationale (PyPI renders README.md verbatim, so relative hrefs resolve against\n"
            "pypi.org) is in this file's module docstring."
        )
        return 1

    print(f"OK: {links_seen} link(s) across {scanned} top-level surface(s) are absolute and resolve.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
