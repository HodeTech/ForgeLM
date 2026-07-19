#!/usr/bin/env python3
"""Deprecation removal-target drift guard.

The drift class this prevents
-----------------------------
``docs/standards/release.md`` (§"Deprecation cadence", rule 3) makes the
removal version part of the deprecation contract: *"The deprecation
message, the ``--help`` text, and the CHANGELOG ``### Deprecated`` entry
must all name the version that will remove the surface."*

For the three deprecated YAML fields — ``lora.use_dora``,
``lora.use_rslora`` and ``training.sample_packing`` — that promised
version was a **hardcoded literal duplicated across ~20 sites** (runtime
``ValueError`` / ``DeprecationWarning`` strings, Pydantic ``description=``
text, ``config_template.yaml`` comments, EN+TR reference docs, EN+TR user
manuals, tests).  Nothing cross-checked them, and the promise rotted
twice: ``v0.9.0`` -> ``v0.10.0`` -> ``v1.0.0``.  Each retarget left
stragglers behind, so operators reading two different pages of the same
release were told two different removal versions.

Two failure modes follow from that, and this guard closes both:

1. **Divergent claims.** Any file in the public tree that names a removal
   version for one of these fields must name the *canonical* one.  The
   canonical value is
   :data:`forgelm.config.DEPRECATION_REMOVAL_VERSION` — read straight out
   of ``forgelm/config.py`` by AST (mirroring how
   ``tools/check_field_descriptions.py`` walks the same file), so the
   guard has exactly one source of truth and no literal of its own.
2. **A promise that has already come due.** Removing a YAML field is a
   MAJOR change per ``release.md`` ("What constitutes 'breaking'"), so
   the target must stay strictly ahead of the shipping version.  If
   ``pyproject.toml``'s version ever reaches or passes the canonical
   target while the fields are still present, every message in the
   product is retroactively false.  The guard fails on
   ``canonical <= pyproject version``, which forces the removal PR (or a
   deliberate retarget) instead of letting the release ship a lie.

What counts as a claim
----------------------
A line is a *claim* when it carries removal language (``remov*`` in
English, ``kaldır*`` in Turkish) **and** a ``vN.N.N`` token, **and** one
of the deprecated field names appears on that line or within
:data:`_FIELD_WINDOW` lines of it.  The field-proximity requirement is
what keeps the guard from flagging unrelated version prose; the
same-line requirement for (removal language + version) is what keeps it
from pairing a removal sentence with a version from a neighbouring
paragraph.

Scope: ``forgelm/**/*.py``, ``config_template.yaml``, ``docs/**/*.md``,
``tests/**/*.py``.  Deliberately excluded:

* ``CHANGELOG.md`` — an append-only historical record; past entries
  legitimately name the version that was promised at the time.
* ``docs/analysis/`` and ``docs/marketing/`` — gitignored working memory
  (see ``docs/standards/documentation.md`` "Working-memory directories").
* This guard and its own test — both must quote non-canonical versions as
  data to describe and exercise the rule.

A line containing ``deprecation-target-ok`` is skipped, for the rare
deliberate historical statement inside an in-scope file.

Exit codes (per the ``tools/`` contract — NOT the public 0/1/2/3/4/5
surface that ``forgelm/`` honours):

- ``0`` — every removal-version claim matches the canonical target and
  the target is still in the future.
- ``1`` — at least one divergent claim, or the target is already due
  (strict mode); or the guard could not resolve its inputs.

CI wiring: runs in ``.github/workflows/ci.yml``'s ``validate`` job with
``--strict``, and is listed in the ``CLAUDE.md`` / ``AGENTS.md`` /
``CONTRIBUTING.md`` self-review gauntlet.

Usage::

    python3 tools/check_deprecation_targets.py
    python3 tools/check_deprecation_targets.py --strict
    python3 tools/check_deprecation_targets.py --quiet
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence

try:
    import tomllib  # Python 3.11+ stdlib.
except ModuleNotFoundError:  # pragma: no cover — 3.10 path
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError as exc:  # pragma: no cover — defensive
        raise SystemExit(
            "check_deprecation_targets: tomllib (Python 3.11+) is unavailable and "
            "the tomli backport is not installed. Run this guard on Python 3.11+ "
            "(or 'pip install tomli')."
        ) from exc

from packaging.version import InvalidVersion, Version

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "forgelm" / "config.py"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"

#: Name of the module constant in ``forgelm/config.py`` that owns the target.
CANONICAL_CONSTANT = "DEPRECATION_REMOVAL_VERSION"

#: Deprecated YAML fields whose removal target this guard tracks.  Adding a
#: newly-deprecated field here immediately puts its docs under the same
#: single-source-of-truth rule.
DEPRECATED_FIELDS = ("use_dora", "use_rslora", "sample_packing")

#: How many lines away from a removal sentence a field name may sit and still
#: be considered "attached" to it.  Markdown tables and YAML comments keep the
#: two together on one line; prose occasionally wraps them onto adjacent lines.
_FIELD_WINDOW = 2

#: Opt-out marker for a deliberate historical statement inside an in-scope file.
_IGNORE_MARKER = "deprecation-target-ok"

# Per docs/standards/regex.md: explicit character classes, bounded quantifiers,
# no two unbounded quantifiers competing for the same characters.
_FIELD_RE = re.compile(r"(?:" + r"|".join(DEPRECATED_FIELDS) + r")")
# English "remove/removed/removal" + Turkish "kaldır/kaldırılır/kaldırıldı".
# Substring match rather than \b-anchored: \b against Turkish 'ı' depends on
# the Unicode word-char universe (regex.md rule 1) and buys nothing here.
_REMOVAL_RE = re.compile(r"remov|kaldır|kaldir", re.IGNORECASE)
# vMAJOR.MINOR.PATCH with an optional rcN suffix, e.g. v1.0.0, v0.10.0, v1.0.0rc1.
_VERSION_RE = re.compile(r"\bv\d{1,3}\.\d{1,3}\.\d{1,3}(?:rc\d{1,3})?\b")

#: Files scanned for claims.  Kept explicit (rather than "everything") so the
#: guard's blast radius is reviewable.
_SCAN_GLOBS = (
    ("forgelm", "**/*.py"),
    ("docs", "**/*.md"),
    ("tests", "**/*.py"),
)
_SCAN_FILES = ("config_template.yaml",)

#: Paths never scanned — see the module docstring for the rationale of each.
_EXCLUDED_DIRS = (
    Path("docs") / "analysis",
    Path("docs") / "marketing",
)
_EXCLUDED_FILES = (
    Path("CHANGELOG.md"),
    Path("tools") / "check_deprecation_targets.py",
    Path("tests") / "test_check_deprecation_targets.py",
)


@dataclass(frozen=True)
class VersionClaim:
    """One removal-version claim attached to a deprecated field."""

    path: Path
    line: int
    version: str
    excerpt: str


def read_canonical_version(config_path: Path = CONFIG_PATH) -> str:
    """Return ``DEPRECATION_REMOVAL_VERSION`` from ``config_path``.

    AST-parsed rather than imported, so resolving the canonical target
    pulls in no runtime dependencies (same approach as
    ``tools/check_field_descriptions.py``).

    Raises:
        SystemExit: when the constant is absent or is not a string literal.
    """
    tree = ast.parse(config_path.read_text(encoding="utf-8"), filename=str(config_path))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == CANONICAL_CONSTANT:
                value = node.value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    return value.value
                raise SystemExit(
                    f"check_deprecation_targets: {CANONICAL_CONSTANT} in {config_path} "
                    "must be a plain string literal so it can be read without importing."
                )
    raise SystemExit(f"check_deprecation_targets: {CANONICAL_CONSTANT} not found in {config_path}.")


def read_package_version(pyproject_path: Path = PYPROJECT_PATH) -> str:
    """Return ``project.version`` from ``pyproject.toml`` (the shipping version)."""
    with pyproject_path.open("rb") as fh:
        data = tomllib.load(fh)
    try:
        return str(data["project"]["version"])
    except KeyError as exc:  # pragma: no cover — a malformed pyproject breaks the build first
        raise SystemExit(f"check_deprecation_targets: missing project.version in {pyproject_path}") from exc


def target_is_still_in_the_future(canonical: str, package_version: str) -> bool:
    """Return True when ``canonical`` is strictly ahead of ``package_version``.

    Compared with :class:`packaging.version.Version`, never as strings —
    ``"v1.10.0" < "v1.2.0"`` lexically (release.md §``__api_version__``).
    """
    try:
        return Version(canonical.lstrip("v")) > Version(package_version)
    except InvalidVersion as exc:
        raise SystemExit(
            f"check_deprecation_targets: cannot compare versions "
            f"(canonical={canonical!r}, package={package_version!r}): {exc}"
        ) from exc


def scan_text(text: str, path: Path, window: int = _FIELD_WINDOW) -> List[VersionClaim]:
    """Return every removal-version claim in ``text`` attached to a deprecated field.

    See the module docstring for what "attached" means.  Returns *all*
    claims, canonical or not — the caller decides which diverge, which
    keeps this function directly assertable from tests.
    """
    lines = text.splitlines()
    field_lines = {i for i, line in enumerate(lines) if _FIELD_RE.search(line)}
    claims: List[VersionClaim] = []
    for index, line in enumerate(lines):
        if _IGNORE_MARKER in line:
            continue
        if not _REMOVAL_RE.search(line):
            continue
        if not any(j in field_lines for j in range(index - window, index + window + 1)):
            continue
        for match in _VERSION_RE.finditer(line):
            claims.append(
                VersionClaim(path=path, line=index + 1, version=match.group(0), excerpt=line.strip()),
            )
    return claims


def _is_excluded(rel_path: Path) -> bool:
    if rel_path in _EXCLUDED_FILES:
        return True
    return any(excluded in rel_path.parents for excluded in _EXCLUDED_DIRS)


def iter_target_files(repo_root: Path = REPO_ROOT) -> List[Path]:
    """Return every in-scope file (absolute, sorted, exclusions applied)."""
    found: List[Path] = []
    for name in _SCAN_FILES:
        candidate = repo_root / name
        if candidate.is_file() and not _is_excluded(Path(name)):
            found.append(candidate)
    for directory, pattern in _SCAN_GLOBS:
        root = repo_root / directory
        if not root.is_dir():
            continue
        for candidate in root.glob(pattern):
            if not candidate.is_file():
                continue
            if _is_excluded(candidate.relative_to(repo_root)):
                continue
            found.append(candidate)
    return sorted(set(found))


def collect_claims(repo_root: Path = REPO_ROOT) -> List[VersionClaim]:
    """Scan every in-scope file and return all removal-version claims found."""
    claims: List[VersionClaim] = []
    for path in iter_target_files(repo_root):
        claims.extend(scan_text(path.read_text(encoding="utf-8"), path))
    return claims


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Verify every removal-version claim for the deprecated YAML fields "
            f"({', '.join(DEPRECATED_FIELDS)}) names forgelm.config."
            f"{CANONICAL_CONSTANT}, and that the target has not already come due."
        ),
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Strict mode: exit 1 on any divergent claim or on an already-due "
            "target.  Default (no flag) is advisory: report to stdout but exit "
            "0 — useful for local iteration."
        ),
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress success summary.")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_arg_parser().parse_args(argv)

    if not CONFIG_PATH.is_file():
        print(f"check_deprecation_targets: {CONFIG_PATH} not found.", file=sys.stderr)
        return 1
    if not PYPROJECT_PATH.is_file():
        print(f"check_deprecation_targets: {PYPROJECT_PATH} not found.", file=sys.stderr)
        return 1

    canonical = read_canonical_version()
    package_version = read_package_version()
    failed = False

    if not target_is_still_in_the_future(canonical, package_version):
        failed = True
        print(
            f"FAIL: the deprecation removal target {canonical} is not ahead of the "
            f"shipping version {package_version} — every '{canonical}' promise in the "
            "product is now retroactively false."
        )
        print(
            f"  Either land the removal PR (dropping {', '.join(DEPRECATED_FIELDS)}) or "
            f"retarget forgelm/config.py::{CANONICAL_CONSTANT} to a later MAJOR "
            "(removing a YAML field is MAJOR — docs/standards/release.md)."
        )

    claims = collect_claims()
    divergent = [c for c in claims if c.version != canonical]
    if divergent:
        failed = True
        print(
            f"FAIL: {len(divergent)} removal-version claim(s) disagree with "
            f"forgelm/config.py::{CANONICAL_CONSTANT} ({canonical})."
        )
        for claim in divergent:
            rel = claim.path.relative_to(REPO_ROOT) if claim.path.is_absolute() else claim.path
            print(f"  {rel}:{claim.line}  claims {claim.version}, canonical is {canonical}")
            print(f"      {claim.excerpt}")

    if failed:
        return 1 if args.strict else 0

    if not args.quiet:
        print(
            f"OK: {len(claims)} removal-version claim(s) across "
            f"{len(iter_target_files())} scanned file(s) all name {canonical}; "
            f"target is still ahead of the shipping version {package_version}."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
