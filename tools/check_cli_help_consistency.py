#!/usr/bin/env python3
"""Wave 5 / Faz 30 Task J — CLI / docs help-consistency guard.

Detects documentation that cites ``forgelm <subcommand> --flag``
invocations where the cited flag (or the subcommand itself) does
NOT exist in the live argparse parser surface.  This is the same
class of bug ForgeLM has fixed several times in Wave 4/5 (the
``verify-audit`` ``--output-dir`` / ``--json`` ghost flags, the
``benchmark`` non-existent subcommand, the ``deploy --target
kserve`` invalid choice, the ``chat --top-p`` ghost, etc.).  A
mechanical guard prevents regression once the docs catch up.

Design choice (resilience over speed): subcommand discovery spawns
``python3 -m forgelm.cli --help`` via :mod:`subprocess` and parses
the ``usage:`` block of each subcommand's help output.  The
alternative — importing ``forgelm.cli._parser`` and walking
argparse ``_actions`` — is faster but couples the guard to the
internal parser layout, which has reorganised twice in Wave 4.
The subprocess form treats argparse's textual help as the
contract surface (which is what users see anyway) and is robust
to internal refactors.

Doc scanning rules:

- Walks ``docs/`` (default) + ``README.md`` for fenced code blocks
  tagged ``bash``, ``shell``, ``console``, ``sh``, or untagged
  fences whose first non-empty line starts with ``$ forgelm`` /
  ``forgelm``.
- For every line matching ``forgelm <sub> ...`` extracts the
  subcommand name + every ``--flag`` token (both ``--flag value``
  and ``--flag=value`` forms) + any value that follows a flag
  whose live parser declares a fixed choice set.
- For each occurrence checks: is ``<sub>`` a registered
  subcommand?  Is ``--flag`` in that subcommand's flag set?  When
  ``--flag`` carries a ``{a,b,c}`` choice list, is the value
  used valid?

False-positive heuristics (documented per the prompt's hard
requirement):

- **Forward references**: a code block is skipped when the
  surrounding ±3 prose lines contain a forward-reference token
  (``planned``, ``roadmap``, ``future``, ``not in v0.5.5``,
  ``(planned)``, ``# v0.6.0``, ``v0.6+`` etc.).  The Wave-4 lesson
  was that legitimate "ghost callouts" describing future work
  must not trip the gate.
- **Wrong / Don't / Anti-pattern blocks**: a fenced block is
  skipped when the immediately preceding non-blank prose line
  contains ``Wrong:`` / ``Don't:`` / ``Anti-pattern:`` /
  ``Legacy:`` / ``Yanlış:`` / ``Bad:``.  Demo blocks that
  intentionally show drift must not trip the gate.
- **analysis/ tree**: the gitignored research artefact tree
  is skipped by default, matching ``check_anchor_resolution``.

Exit codes (per ``tools/`` contract — NOT the public 0/1/2/3/4
surface that ``forgelm/`` honours):

- ``0`` — no drift (advisory mode default), or strict mode with
  zero findings.
- ``1`` — at least one drift finding (strict mode), or
  parser-discovery failure.

The tool defaults to **advisory mode** (no flag) for local iteration —
report drift, exit 0.  CI (``.github/workflows/ci.yml`` validate job)
already invokes this tool with ``--strict``, so any drift a
contributor sees locally without the flag will fail CI; run with
``--strict`` locally to see the same result CI will produce.

Usage::

    python3 tools/check_cli_help_consistency.py
    python3 tools/check_cli_help_consistency.py --strict
    python3 tools/check_cli_help_consistency.py --quiet
    python3 tools/check_cli_help_consistency.py --scope docs/guides

Closure-plan reference: Faz 30 Task J.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Sequence

# ---------------------------------------------------------------------------
# Parser-surface discovery
# ---------------------------------------------------------------------------

# Match a long flag.  We deliberately reject single-dash short flags
# at the doc-scanning layer because the project's docs use long forms
# almost exclusively, and short-form ambiguity (``-q`` is the universal
# quiet alias on every subcommand) is not what this guard is for.
_LONG_FLAG_RE = re.compile(r"--[A-Za-z][A-Za-z0-9-]*")

# Recognise ``--flag {a,b,c}`` blocks in argparse usage lines.  The
# choice list is a comma-separated set inside ``{...}`` with no
# embedded spaces (argparse formats it that way).
_FLAG_CHOICES_RE = re.compile(r"(--[A-Za-z][A-Za-z0-9-]*)\s+\{([^}]+)\}")


@dataclass(frozen=True)
class SubcommandSurface:
    """The argparse surface of one subcommand."""

    name: str
    flags: frozenset[str]
    choices: Mapping[str, frozenset[str]]


@dataclass(frozen=True)
class ParserSurface:
    """Full live parser surface — top-level flags + per-subcommand."""

    subcommands: Mapping[str, SubcommandSurface]
    top_level_flags: frozenset[str]
    top_level_choices: Mapping[str, frozenset[str]]


def _run_help(argv: Sequence[str]) -> str:
    """Spawn ``python3 -m forgelm.cli ...`` and return combined output.

    We capture both stdout and stderr because argparse routes the
    ``usage:`` line to stderr on subparser-error paths even when
    ``--help`` is the explicit ask on success paths.  The combined
    text is sufficient for our regex-based parse — we only need the
    flag tokens, not their semantic position.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "forgelm.cli", *argv],
        capture_output=True,
        text=True,
        check=False,
    )
    # ``--help`` exits 0; if the spawn returns non-zero with no body
    # at all, parser discovery has failed and the caller treats that
    # as exit 1.
    return (proc.stdout or "") + (proc.stderr or "")


def _parse_usage_flags(help_text: str) -> tuple[frozenset[str], dict[str, frozenset[str]]]:
    """Extract ``--flag`` set + ``{a,b,c}`` choice map from a help block.

    Argparse formats the usage line(s) as ``[--flag VALUE]``,
    ``--flag {choice,choice}``, etc.  We scan the whole help body
    rather than just the usage block so subcommand surfaces like the
    options-list table also contribute (some subcommands have flags
    that wrap onto multiple usage lines).
    """
    flags = set(_LONG_FLAG_RE.findall(help_text))
    choices: dict[str, frozenset[str]] = {}
    for match in _FLAG_CHOICES_RE.finditer(help_text):
        flag = match.group(1)
        body = match.group(2)
        values = frozenset(s.strip() for s in body.split(",") if s.strip())
        if values:
            choices[flag] = values
    return frozenset(flags), choices


def _parse_subcommand_list(top_help: str) -> list[str]:
    """Return the list of registered subcommand names from top-level help.

    Argparse's ``COMMAND`` positional emits a block of the form::

        positional arguments:
          COMMAND
            chat                Interactive chat REPL ...
            export              Export a fine-tuned model ...

    We locate the ``COMMAND`` line and harvest the indented entries
    that follow until the indentation level drops back.  This is
    simpler than walking ``argparse._actions`` and is what the user
    sees on screen.
    """
    lines = top_help.splitlines()
    inside = False
    found: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not inside:
            if stripped == "COMMAND":
                inside = True
            continue
        # End of the COMMAND block: a new section header (e.g.
        # ``options:``) or a non-indented line.
        if not line.startswith("    "):
            break
        if not stripped:
            continue
        # First token on the line is the subcommand name.
        token = stripped.split(None, 1)[0]
        if token.startswith("-"):
            continue
        found.append(token)
    return found


def discover_parser_surface() -> ParserSurface:
    """Return the live parser surface, or raise on discovery failure."""
    top_help = _run_help(["--help"])
    if not top_help.strip():
        raise RuntimeError("forgelm CLI top-level --help produced no output")
    sub_names = _parse_subcommand_list(top_help)
    if not sub_names:
        raise RuntimeError("could not parse subcommand list from top-level help")
    top_flags, top_choices = _parse_usage_flags(top_help)

    surfaces: dict[str, SubcommandSurface] = {}
    for name in sub_names:
        sub_help = _run_help([name, "--help"])
        if not sub_help.strip():
            # Empty `--help` output is a parser-discovery failure,
            # not "this subcommand has no flags".  Surface it loudly
            # instead of silently dropping the subcommand from the
            # surface (which would falsely report every doc citing
            # the command as "subcommand not in parser surface").
            raise RuntimeError(
                f"forgelm {name} --help returned empty output — parser discovery "
                "failed; refusing to silently drop the subcommand."
            )
        flags, choices = _parse_usage_flags(sub_help)
        surfaces[name] = SubcommandSurface(name=name, flags=flags, choices=choices)
    return ParserSurface(
        subcommands=surfaces,
        top_level_flags=top_flags,
        top_level_choices=top_choices,
    )


# ---------------------------------------------------------------------------
# Doc scanning
# ---------------------------------------------------------------------------

# Code-fence opener: ``` optionally followed by an info string (lang).
_FENCE_RE = re.compile(r"^(\s*)```([A-Za-z0-9_+-]*)\s*$")

# Code-fence closer: same indent, ```.
_FENCE_CLOSE_RE = re.compile(r"^(\s*)```\s*$")

_BASH_LANG_TAGS: frozenset[str] = frozenset({"bash", "shell", "sh", "console", ""})

# Forward-reference markers that whitelist a code block as
# "documenting planned future work".  Lower-cased before match.
#
# The markers are anchored to whole-phrase forms — bare substrings
# like "planned" or "future" are deliberately omitted because they
# match unrelated prose ("planned attacks", "future work" as a
# generic pointer, "roadmap-driven design") and would silently
# disable the gate for any code block within the ±3-line window.
# Each token below either includes context ("is planned", "in the
# roadmap"), is a versioned hard tag ("v0.6.0+"), or is an explicit
# parenthetical ("(planned)").
_FORWARD_REF_MARKERS: tuple[str, ...] = (
    "is planned",
    "are planned",
    "planned for",
    "planning to",
    "(planned)",
    "in the roadmap",
    "see roadmap",
    "see the roadmap",
    "future release",
    "future version",
    "in a future",
    "not in v0.5",
    "not in v0.5.5",
    "# v0.6.0",
    "v0.6+",
    "v0.6.0+",
    "to be implemented",
    "not yet implemented",
    # Turkish equivalents — bilingual-doc fixture parity.
    "planlanan",
    "yol haritası",
    "yol haritasında",
    "ileride",
)

# "Wrong / Don't / Anti-pattern" tags — when the immediately-preceding
# non-blank prose line contains one of these (case-insensitive), the
# code block is treated as an intentional bad-example demonstration.
_ANTI_PATTERN_MARKERS: tuple[str, ...] = (
    "wrong:",
    "don't:",
    "do not:",
    "anti-pattern:",
    "antipattern:",
    "legacy:",
    "yanlış:",
    "bad:",
    "incorrect:",
)

# Default doc roots: every public docs subtree + README.
_DEFAULT_DOC_ROOTS: tuple[str, ...] = (
    "docs",
    "README.md",
)


@dataclass(frozen=True)
class Invocation:
    """One ``forgelm <sub> ...`` line extracted from a doc."""

    source: Path
    line: int
    raw: str
    subcommand: str
    flags: tuple[str, ...]
    flag_values: Mapping[str, str | None] = field(default_factory=dict)


@dataclass(frozen=True)
class DriftFinding:
    invocation: Invocation
    bad_token: str
    reason: str


def _read_lines(path: Path) -> list[str]:
    # Fail-loud on unreadable files: silently returning [] would
    # hide real drift in strict CI by excluding the file from the
    # scan.  The caller can catch OSError if it has a deliberate
    # tolerance policy; the default is loud.
    return path.read_text(encoding="utf-8").splitlines()


def _is_anti_pattern_context(prev_prose_line: str | None) -> bool:
    if not prev_prose_line:
        return False
    lowered = prev_prose_line.lower()
    return any(marker in lowered for marker in _ANTI_PATTERN_MARKERS)


def _has_forward_reference_nearby(lines: Sequence[str], block_start: int, block_end: int) -> bool:
    """Return True iff the ±3-line window around the block carries a fwd-ref marker.

    ``block_start`` / ``block_end`` are 0-indexed line numbers that
    bound the fenced code block (the fence lines themselves).  We
    scan the 3 lines before the opening fence and the 3 lines after
    the closing fence for any forward-reference token.
    """
    window_start = max(0, block_start - 3)
    window_end = min(len(lines), block_end + 4)
    for idx in range(window_start, window_end):
        if block_start <= idx <= block_end:
            continue
        lowered = lines[idx].lower()
        if any(marker in lowered for marker in _FORWARD_REF_MARKERS):
            return True
    return False


def _previous_prose_line(lines: Sequence[str], fence_idx: int) -> str | None:
    """Return the most recent non-blank line above ``fence_idx``, or None."""
    for idx in range(fence_idx - 1, -1, -1):
        candidate = lines[idx].strip()
        if candidate:
            return candidate
    return None


def _is_forgelm_invocation_line(stripped: str) -> bool:
    """Return True for a shell-block line that starts a ``forgelm`` invocation.

    Recognised forms:

    - ``forgelm sub ...``
    - ``$ forgelm sub ...``
    - ``> forgelm sub ...`` (some doc styles use ``>`` for prompt)
    - ``# forgelm sub ...`` (comment-tagged demo lines are filtered
      by the anti-pattern heuristic, not here)

    We deliberately ignore the leading shell prompt / continuation
    marker but require the ``forgelm`` token to be the first
    non-prompt token.
    """
    candidate = stripped
    for prefix in ("$", ">"):
        if candidate.startswith(prefix):
            candidate = candidate[1:].lstrip()
            break
    return candidate.startswith("forgelm ") or candidate == "forgelm"


def _strip_prompt(line: str) -> str:
    stripped = line.strip()
    for prefix in ("$ ", "> ", "$\t", ">\t"):
        if stripped.startswith(prefix):
            return stripped[len(prefix) :].lstrip()
    if stripped in ("$", ">"):
        return ""
    return stripped


def _tokenise_invocation(line: str) -> list[str]:
    """Split a ``forgelm ...`` line into shell-ish tokens.

    We strip trailing line-continuations (``\\``) and any inline
    comment (``# ...``) before tokenising on whitespace.  This is a
    minimal shlex-substitute — we do NOT need quote-aware parsing
    because we only care about flag tokens (which never contain
    spaces) and the value that follows them.

    Each token is then trimmed of stray punctuation that would
    otherwise pollute the flag set: balanced parens / brackets that
    enclose a single flag (``(--default-probes)`` in argparse-synopsis
    docs), trailing commas (list separators in prose), and the
    pipe character (alternative-form synopsis).  Strict shell would
    not recognise these as comments, but in our doc-blocks they're
    typographic decoration around live flags.
    """
    body = line
    if "#" in body:
        # Strip everything from the first ``#`` to end-of-line, but
        # only when the ``#`` is preceded by whitespace — otherwise
        # an inline ``#`` in a flag value (rare) would be lost.
        cut = body.find(" #")
        if cut != -1:
            body = body[:cut]
    body = body.rstrip("\\").strip()
    cleaned: list[str] = []
    for raw in body.split():
        # Strip both prose decoration ([](),|) AND quotation marks
        # since ``split()`` is not quote-aware: a doc that writes
        # ``--target "ollama"`` would otherwise carry the quote into
        # the token and fail validation against the parser's choice
        # set.  Including ``"`` and ``'`` in the strip set lets the
        # tool reach the underlying value.
        token = raw.strip("()[],|\"'")
        if token:
            cleaned.append(token)
    return cleaned


def _is_synopsis_line(line: str) -> bool:
    """Return True iff this looks like an argparse synopsis, not an invocation.

    Synopsis lines surround optional flags with ``[...]`` brackets and
    sometimes mix alternation with ``(...|...)`` groupings — e.g. the
    doctor reference doc renders ``forgelm doctor [--offline]
    [--output-format {text,json}]`` and the safety-eval reference
    shows ``forgelm safety-eval --model PATH (--probes JSONL |
    --default-probes)``.  Treating those as real invocations would
    flood the report with apparent drift; the user-facing output
    of ``--help`` already validates them, and they're documented
    syntax anyway.

    Heuristic:

    - " [" + "]" pair is a strong synopsis tell — argparse usage
      strings always wrap optional flags as ``--flag [VALUE]`` or
      ``[--flag {a,b}]`` with a leading space before the bracket.
      Requiring the space prefix avoids a false-negative skip on
      legitimate invocations using shell-glob brackets like
      ``data/[0-9]*.jsonl`` (those put the bracket flush against
      the path, no leading space).
    - alternation ``|`` paired with ``(`` (the safety-eval form).
    """
    if " [" in line and "]" in line:
        return True
    return "|" in line and "(" in line


def _validate_forgelm_invocation_tokens(tokens: Sequence[str]) -> str | None:
    """Return the subcommand iff ``tokens`` is a well-formed ``forgelm <sub>``
    sequence; ``None`` otherwise."""
    if len(tokens) < 2 or tokens[0] != "forgelm":
        return None
    sub = tokens[1]
    # If the second token is itself a flag (e.g. ``forgelm --version``),
    # it is a top-level form, not a subcommand invocation. Today's
    # contract focuses on subcommand drift, so we ignore these.
    if sub.startswith("-"):
        return None
    return sub


def _consume_flag_token(
    tokens: Sequence[str],
    idx: int,
    flags: list[str],
    flag_values: dict[str, str | None],
) -> int:
    """Consume one flag token (possibly with its value) and return the
    advance offset for the outer cursor.  Caller adds the returned offset
    to ``idx``."""
    tok = tokens[idx]
    # ``--flag=value`` form — single-token capture.
    if "=" in tok:
        flag, _, value = tok.partition("=")
        flags.append(flag)
        flag_values[flag] = value
        return 1
    flags.append(tok)
    # Peek the next token to capture a value (if any). Only treat the
    # next token as a NEW flag when it starts with ``--`` (the long-flag
    # prefix used in this project). A single ``-`` prefix could be a
    # short flag (rare; shipped tools don't ship short forms) OR a
    # negative numeric value like ``--temperature -1.0``; we'd rather
    # consume the value than mis-classify it as a flag.
    if idx + 1 < len(tokens) and not tokens[idx + 1].startswith("--"):
        flag_values[tok] = tokens[idx + 1]
        return 2
    flag_values[tok] = None
    return 1


def _parse_invocation_flags(tokens: Sequence[str]) -> tuple[list[str], dict[str, str | None]]:
    """Walk ``tokens`` from index 2 onwards and return ``(flags,
    flag_values)`` for every ``--flag`` (and ``--flag=value`` /
    ``--flag value``) form encountered.  Positional args are skipped."""
    flags: list[str] = []
    flag_values: dict[str, str | None] = {}
    idx = 2
    while idx < len(tokens):
        if tokens[idx].startswith("--"):
            idx += _consume_flag_token(tokens, idx, flags, flag_values)
        else:
            idx += 1
    return flags, flag_values


def _extract_invocation(source: Path, line_no: int, line: str) -> Invocation | None:
    """Extract a structured ``forgelm`` invocation from one shell-block line."""
    payload = _strip_prompt(line)
    if not payload.startswith("forgelm"):
        return None
    if _is_synopsis_line(payload):
        return None
    tokens = _tokenise_invocation(payload)
    sub = _validate_forgelm_invocation_tokens(tokens)
    if sub is None:
        return None
    flags, flag_values = _parse_invocation_flags(tokens)
    return Invocation(
        source=source,
        line=line_no,
        raw=payload,
        subcommand=sub,
        flags=tuple(flags),
        flag_values=flag_values,
    )


def _find_fence_close(lines: Sequence[str], open_idx: int) -> int | None:
    """Return the closing-fence index for the fence opened at ``open_idx``,
    or ``None`` if the fence is unterminated (caller bails out)."""
    close_idx = open_idx + 1
    while close_idx < len(lines) and not _FENCE_CLOSE_RE.match(lines[close_idx]):
        close_idx += 1
    if close_idx >= len(lines):
        return None
    return close_idx


def _stitch_block_lines(lines: Sequence[str], block_start: int, block_end: int) -> Iterable[tuple[int, str]]:
    """Yield ``(line_no, line)`` for the interior of one code block.

    Re-stitches ``\\``-continued multi-line commands so each logical
    command surfaces as one tuple rather than N lines where the first
    carries the command name and the rest carry the flags in isolation.
    The reported ``line_no`` is the 1-indexed start line of the logical
    command — that's where a reviewer expects the file-line citation
    to land.
    """
    current = ""
    start_line_no = 0
    for inner_idx in range(block_start + 1, block_end):
        raw = lines[inner_idx]
        stripped = raw.rstrip()
        if not current:
            start_line_no = inner_idx + 1
        if stripped.endswith("\\"):
            # Strip the trailing backslash + preserve one space so
            # adjacent tokens don't fuse together.
            current += stripped[:-1] + " "
            continue
        current += raw
        yield (start_line_no, current)
        current = ""
    if current:
        # Unterminated continuation at block end — yield what we have
        # so the line is not silently dropped.
        yield (start_line_no, current)


def _iter_code_block_lines(lines: Sequence[str]) -> Iterable[tuple[int, str]]:
    """Yield ``(line_no, line)`` tuples for lines inside qualifying code blocks.

    Qualifying = lang tag in :data:`_BASH_LANG_TAGS` AND the block is not
    flagged as forward-reference / anti-pattern.  Continuation handling
    + interior iteration are delegated to :func:`_stitch_block_lines`.
    """
    idx = 0
    while idx < len(lines):
        match = _FENCE_RE.match(lines[idx])
        if not match:
            idx += 1
            continue
        close_idx = _find_fence_close(lines, idx)
        if close_idx is None:
            return
        lang = match.group(2).lower()
        if not _should_skip_block(lines, idx, close_idx, lang):
            yield from _stitch_block_lines(lines, idx, close_idx)
        idx = close_idx + 1


def _should_skip_block(lines: Sequence[str], block_start: int, block_end: int, lang: str) -> bool:
    """Return True iff this code block must NOT be scanned for drift.

    Skip rules:

    1. Lang tag not in the bash/shell/console family AND not empty.
       (Empty lang tags are accepted only when the first body line
       starts with ``forgelm`` / ``$ forgelm``.)
    2. Forward-reference marker in the ±3-line window around the
       block.
    3. Anti-pattern marker on the immediately-preceding prose line.
    """
    if lang not in _BASH_LANG_TAGS:
        return True
    if lang == "":
        # Untagged fence: only scan if the first non-empty body
        # line is a forgelm invocation; this avoids treating
        # arbitrary text fences (YAML examples, JSON) as shell.
        first_body = ""
        for body_line in lines[block_start + 1 : block_end]:
            if body_line.strip():
                first_body = body_line.strip()
                break
        if not _is_forgelm_invocation_line(first_body):
            return True
    if _has_forward_reference_nearby(lines, block_start, block_end):
        return True
    prev_prose = _previous_prose_line(lines, block_start)
    if _is_anti_pattern_context(prev_prose):
        return True
    return False


def _scan_doc(source: Path) -> Iterable[Invocation]:
    """Yield every ``forgelm <sub> ...`` invocation found in ``source``."""
    lines = _read_lines(source)
    if not lines:
        return
    for line_no, line in _iter_code_block_lines(lines):
        if "forgelm" not in line:
            continue
        invocation = _extract_invocation(source, line_no, line)
        if invocation is not None:
            yield invocation


# ---------------------------------------------------------------------------
# Prose-claim scanning — single-value choice flags advertised as comma-lists
# ---------------------------------------------------------------------------

# Prose that claims an operator can pass a comma-separated *list* to a flag.
# argparse ``choices`` flags accept exactly one value, so "comma-separate
# ``--quant``" is documentation drift: the comma form is rejected with an
# ``invalid choice`` usage error (F-P7-OPUS-13).  Bilingual: the TR mirror
# says "``--quant``'ı virgülle ayır...".  Both forms reference a ``--flag``
# token on the same line, so we anchor on the flag token's presence inside
# a comma-list claim rather than NLP-parsing the sentence.
_COMMA_LIST_CLAIM_RE = re.compile(
    r"(?:comma[- ]separate(?:d)?|virgülle ayır)",
    flags=re.IGNORECASE,
)


def _scan_prose_for_choice_list_claims(source: Path, surface: ParserSurface) -> Iterable[DriftFinding]:
    """Flag prose that advertises a comma-list for a single-value choice flag.

    Only fires when (a) a line carries a comma-list claim phrase AND (b)
    names a ``--flag`` token that the live parser surface declares with a
    fixed ``choices`` set (which argparse forbids combining).  Code fences
    are skipped — this scans prose, where the marketing-style "do X in one
    command" claims live.  Conservative by design: a single choice-flag
    token on a comma-list line is the only trigger, keeping false positives
    near zero.
    """
    lines = _read_lines(source)
    if not lines:
        return
    choice_flags: set[str] = set()
    for sub in surface.subcommands.values():
        choice_flags.update(sub.choices.keys())
    choice_flags.update(surface.top_level_choices.keys())
    if not choice_flags:
        return
    in_fence = False
    for idx, raw in enumerate(lines):
        if _FENCE_RE.match(raw) or _FENCE_CLOSE_RE.match(raw):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not _COMMA_LIST_CLAIM_RE.search(raw):
            continue
        for flag in _LONG_FLAG_RE.findall(raw):
            if flag in choice_flags:
                yield DriftFinding(
                    invocation=Invocation(
                        source=source,
                        line=idx + 1,
                        raw=raw.strip(),
                        subcommand="(prose)",
                        flags=(flag,),
                    ),
                    bad_token=flag,
                    reason=(
                        f"prose advertises a comma-separated list for {flag!r}, but it is a "
                        "single-value choices flag (argparse rejects the comma form)"
                    ),
                )


# ---------------------------------------------------------------------------
# Drift evaluation
# ---------------------------------------------------------------------------


def _evaluate(invocation: Invocation, surface: ParserSurface) -> list[DriftFinding]:
    """Return drift findings (possibly empty) for one invocation."""
    sub = invocation.subcommand
    if sub not in surface.subcommands:
        return [
            DriftFinding(
                invocation=invocation,
                bad_token=sub,
                reason=f"subcommand {sub!r} not in parser surface",
            )
        ]
    sub_surface = surface.subcommands[sub]
    findings: list[DriftFinding] = []
    for flag in invocation.flags:
        if flag not in sub_surface.flags:
            findings.append(
                DriftFinding(
                    invocation=invocation,
                    bad_token=flag,
                    reason=f"flag {flag!r} not in parser surface for subcommand {sub!r}",
                )
            )
            continue
        # Choice-set drift: the flag exists, but the value cited is
        # not in its declared choice list.
        if flag in sub_surface.choices:
            value = invocation.flag_values.get(flag)
            if value is None:
                # Doc didn't pin a concrete value — silent.
                continue
            if _is_shell_variable_reference(value):
                # ``--flag "${VAR}"`` / ``--flag $VAR`` — the value
                # is operator-supplied at runtime; we cannot
                # statically validate it against the choice set.
                # Treating these as drift would force docs to
                # ``--flag q4_k_m`` style snippets that lose the
                # educational "loop over the choices" pattern.
                continue
            allowed = sub_surface.choices[flag]
            if value not in allowed:
                allowed_fmt = "{" + ",".join(sorted(allowed)) + "}"
                findings.append(
                    DriftFinding(
                        invocation=invocation,
                        bad_token=value,
                        reason=(f"flag {flag!r} choices include {allowed_fmt} but doc uses {value!r}"),
                    )
                )
    return findings


def _is_shell_variable_reference(value: str) -> bool:
    """Return True iff ``value`` is a shell variable reference.

    Recognises ``$VAR``, ``${VAR}``, ``$1`` (positional), and the
    common quoted forms that survive ``_tokenise_invocation``'s
    quote stripping.  Used by ``_evaluate`` to skip choice-set
    validation when the doc legitimately defers the value to a
    shell-loop variable.
    """
    return value.startswith("$")


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


def _walk_docs(scope: Path) -> Iterable[Path]:
    """Yield Markdown / README files under ``scope``, deterministic order.

    ``scope`` may be a single file (e.g. ``README.md``) or a
    directory.  Two subtrees are excluded by default:

    - ``analysis/`` — gitignored research artefacts (matches the
      anchor-checker's research-tree exclusion).
    - ``marketing/`` — gitignored internal strategy docs (will not
      be visible to most contributors and reference future state).
    """
    if scope.is_file():
        if scope.suffix.lower() == ".md":
            yield scope
        return
    if not scope.is_dir():
        return
    excluded = tuple((scope / spec).resolve() for spec in ("analysis", "marketing"))
    for path in sorted(p for p in scope.rglob("*.md") if p.is_file()):
        resolved = path.resolve()
        skip = False
        for ex in excluded:
            try:
                resolved.relative_to(ex)
            except ValueError:
                continue
            skip = True
            break
        if skip:
            continue
        yield path


def _format_finding(finding: DriftFinding) -> str:
    inv = finding.invocation
    return f"{inv.source}:{inv.line}  forgelm {inv.subcommand} ... {finding.bad_token}  → {finding.reason}"


def _build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate that every forgelm CLI invocation cited in docs/ "
            "matches the live argparse parser surface (Faz 30 Task J)."
        ),
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
        action="append",
        default=None,
        help=("Path under repo-root to scan (file or directory).  Repeatable.  Default: docs/ tree + README.md."),
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Strict mode: exit 1 on any drift finding.  Default (no "
            "flag) is advisory: report drift to stdout but exit 0 — "
            "useful for local iteration.  CI (ci.yml validate job) "
            "already invokes this tool with --strict."
        ),
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress the OK summary on success.",
    )
    return parser


def _resolve_scopes(repo_root: Path, scope_args: list[str] | None) -> list[Path]:
    if scope_args is None:
        return [(repo_root / spec).resolve() for spec in _DEFAULT_DOC_ROOTS]
    return [(repo_root / spec).resolve() for spec in scope_args]


def _collect_invocations(scopes: Sequence[Path]) -> list[Invocation]:
    invocations: list[Invocation] = []
    for scope in scopes:
        for doc in _walk_docs(scope):
            invocations.extend(_scan_doc(doc))
    return invocations


def _collect_findings(invocations: Sequence[Invocation], surface: ParserSurface) -> list[DriftFinding]:
    findings: list[DriftFinding] = []
    for inv in invocations:
        findings.extend(_evaluate(inv, surface))
    return findings


def _collect_prose_findings(scopes: Sequence[Path], surface: ParserSurface) -> list[DriftFinding]:
    """Scan doc prose for comma-list claims on single-value choice flags."""
    findings: list[DriftFinding] = []
    for scope in scopes:
        for doc in _walk_docs(scope):
            findings.extend(_scan_prose_for_choice_list_claims(doc, surface))
    return findings


def _doc_count(findings: Sequence[DriftFinding]) -> int:
    return len({f.invocation.source for f in findings})


def _report_findings(findings: Sequence[DriftFinding], strict: bool) -> int:
    verdict = "FAIL" if strict else "WARN"
    print(f"{verdict}: CLI / doc help-consistency drift:")
    for finding in findings:
        print(f"  {_format_finding(finding)}")
    print(f"\n{len(findings)} drift finding(s) across {_doc_count(findings)} doc(s).")
    return 1 if strict else 0


def main(argv: list[str] | None = None) -> int:
    args = _build_argparser().parse_args(argv)

    repo_root = args.repo_root.resolve()
    scopes = _resolve_scopes(repo_root, args.scope)

    try:
        surface = discover_parser_surface()
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(f"error: parser-surface discovery failed: {exc}", file=sys.stderr)
        return 1

    invocations = _collect_invocations(scopes)
    findings = _collect_findings(invocations, surface)
    findings.extend(_collect_prose_findings(scopes, surface))

    if findings:
        return _report_findings(findings, args.strict)

    if not args.quiet:
        scope_label = ", ".join(
            str(s.relative_to(repo_root)) if s.is_relative_to(repo_root) else str(s) for s in scopes
        )
        print(f"OK: {len(invocations)} forgelm invocation(s) across {scope_label} all match the live parser surface.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
