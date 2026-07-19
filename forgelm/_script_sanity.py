"""Phase 15 Task 2 — Unicode / script sanity check for extracted text.

Runs **after** every per-file extraction. For a language hint (e.g.
``"tr"`` for Turkish), counts the ratio of code points that fall outside
the language's expected Unicode blocks (plus a small allow-list of
universal punctuation / digits / ASCII whitespace) and emits a single
operator-facing ``WARNING`` when the ratio crosses the configured
threshold.

Why this exists: pypdf's font-fallback failures (audit §1.2 / §1.3) and
mis-decoded TXT files produce text that *parses* but is full of
out-of-script garbage — the 2026-05-11 pilot measured ≈ 7 % corrupt-char
ratio on the front-matter pages of a Turkish PDF and 100 % U+085F bullet
glyphs on a chapter-level section. Both classes of failure would be
caught by a single Unicode-block sanity check.

Design rules:

* **No new heavy deps.** Block detection uses :func:`unicodedata.name`
  (stdlib) plus a small per-language allow-list. ``langdetect`` (already
  shipped under ``[ingestion]``) is not invoked here — the check runs
  whether or not a language was auto-detected, because the operator
  supplies the hint explicitly via ``--language-hint``.
* **Fail-soft.** The check never raises and never blocks ingestion;
  it logs a warning and surfaces structured metrics in ``notes_structured``
  so machine-driven pipelines can react without grepping log lines.
* **Calibrated default threshold.** The 2026-05-11 audit measured ~7 %
  corruption on the worst-case pages and 0 % on clean body. The
  documented placeholder of 0.5 % in the audit was intentionally
  aggressive; we ship a calibrated **1.5 %** default that catches the
  pilot's two-chunk front-matter corruption (~7 % ratio) and the
  bullet-glyph chapter (~3 % ratio over 17 chunks) while leaving room
  for legitimate mixed-script content (Turkish corpora with English
  code samples sit comfortably under 1 %).
* **Profile-aware.** Code points the normaliser is allowed to produce
  (per ``forgelm._pypdf_normalise.emitted_codepoints``) are considered
  in-script even when they fall outside the strict per-language block
  list — otherwise the warning would fire on every successful
  normalisation pass.

Public surface: one function (:func:`check_script_sanity`) and one
constant (:data:`DEFAULT_THRESHOLD`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Tuple

from ._pypdf_normalise import emitted_codepoints

logger = logging.getLogger("forgelm.ingestion.script_sanity")

# True Private-Use-Area ranges (BMP + the two supplementary planes) — code
# points Unicode guarantees will never be assigned a standard character, so
# any appearance is by construction an application-specific "custom glyph".
_PUA_RANGES: Tuple[Tuple[int, int], ...] = (
    (0xE000, 0xF8FF),  # BMP Private Use Area
    (0xF0000, 0xFFFFD),  # Supplementary Private Use Area-A
    (0x100000, 0x10FFFD),  # Supplementary Private Use Area-B
)

# Mandaic block — narrow, explicitly-justified addition (not a blanket
# "anything past U+0800" catch-all, which used to misclassify legitimate
# Devanagari/Thai/Georgian/Hangul/Hiragana/Katakana/CJK text as a "custom
# glyph"). The 2026-05-11 audit's corrupted bullet artefact was U+085F,
# inside this block.
_MANDAIC_RANGE: Tuple[int, int] = (0x0840, 0x085F)

DEFAULT_THRESHOLD: float = 0.015
"""Calibrated 1.5 % ratio — fires on the audit's two failure modes
(front-matter font corruption ≈ 7 %, bullet-glyph chapter ≈ 3 %) while
leaving room for legitimate mixed-script content (1 % envelope).
"""


# ---------------------------------------------------------------------------
# Per-language allow-lists
# ---------------------------------------------------------------------------

# Universal allow-list: ASCII printables (incl. punctuation and digits),
# whitespace, and a few common typographic glyphs that turn up in any
# corpus regardless of source language. Members are bare strings so the
# membership test is a hash lookup, not a range comparison.
_UNIVERSAL_ALLOW: FrozenSet[str] = frozenset(
    # ASCII printables 0x20–0x7E + tab / newline / CR.
    [chr(c) for c in range(0x20, 0x7F)]
    + ["\t", "\n", "\r", "\f", "\v"]
    # Typographic punctuation that survives any language.
    + [
        "‘",  # ‘ LEFT SINGLE QUOTE
        "’",  # ’ RIGHT SINGLE QUOTE
        "“",  # “ LEFT DOUBLE QUOTE
        "”",  # ” RIGHT DOUBLE QUOTE
        "–",  # – EN DASH
        "—",  # — EM DASH
        "…",  # … HORIZONTAL ELLIPSIS
        " ",  # NO-BREAK SPACE
        "•",  # • BULLET
        "«",  # « LEFT GUILLEMET
        "»",  # » RIGHT GUILLEMET
    ]
)


# Per-language allow-lists. Each entry is ``(ranges, extras)`` where:
#
#   * ``ranges`` is a tuple of ``(low_inclusive, high_inclusive)``
#     contiguous Unicode block boundaries.
#   * ``extras`` is a frozenset of discrete characters in the
#     **scattered** region — Latin-1 Supplement carries one Turkish
#     character (``Ç``) at U+00C7 and another (``ç``) at U+00E7
#     while ``ø``, ``Õ``, ``ú``, ``÷`` live in the same block but are
#     not Turkish. A wide range like ``(0x00A0, 0x024F)`` would silently
#     mark every Western European letter as in-script and let the
#     audit's font-fallback corruption pass unnoticed.
#
# A character is in-script iff its code point falls in any range OR
# appears in the language's ``extras`` set OR appears in
# ``_UNIVERSAL_ALLOW``.
_LANGUAGE_BLOCKS: Dict[str, Tuple[Tuple[Tuple[int, int], ...], FrozenSet[str]]] = {
    "tr": (
        (
            (0x0020, 0x007E),  # Basic Latin
            (0x2000, 0x206F),  # General Punctuation
        ),
        frozenset(
            # Turkish-specific letters in Latin-1 Supplement + Latin Extended-A.
            # Discrete listing prevents the audit's øÕú÷ artefacts (also in
            # Latin-1 Supplement) from masquerading as in-script.
            [
                "Ç",
                "ç",  # U+00C7 / U+00E7
                "Ö",
                "ö",  # U+00D6 / U+00F6
                "Ü",
                "ü",  # U+00DC / U+00FC
                "Ğ",
                "ğ",  # U+011E / U+011F
                "İ",
                "ı",  # U+0130 / U+0131
                "Ş",
                "ş",  # U+015E / U+015F
                "Â",
                "â",  # U+00C2 / U+00E2 — Ottoman/loanwords
                "Î",
                "î",  # U+00CE / U+00EE
                "Û",
                "û",  # U+00DB / U+00FB
            ]
        ),
    ),
    "en": (
        (
            (0x0020, 0x007E),
            (0x2000, 0x206F),
        ),
        frozenset(
            # English keeps a few diacritic loan-words (café, naïve, résumé)
            # without opening the door to every Latin-1 character.
            ["é", "É", "è", "ë", "ï", "ô", "ç", "à", "â", "ä", "ö", "ü", "ñ"]
        ),
    ),
    "de": (
        (
            (0x0020, 0x007E),
            (0x2000, 0x206F),
        ),
        frozenset(["Ä", "ä", "Ö", "ö", "Ü", "ü", "ß"]),
    ),
    "fr": (
        (
            (0x0020, 0x007E),
            (0x2000, 0x206F),
        ),
        frozenset(
            [
                "à",
                "â",
                "ä",
                "ç",
                "é",
                "è",
                "ê",
                "ë",
                "î",
                "ï",
                "ô",
                "ö",
                "ù",
                "û",
                "ü",
                "ÿ",
                "À",
                "Â",
                "Ä",
                "Ç",
                "É",
                "È",
                "Ê",
                "Ë",
                "Î",
                "Ï",
                "Ô",
                "Ö",
                "Ù",
                "Û",
                "Ü",
                "Ÿ",
            ]
        ),
    ),
    "es": (
        (
            (0x0020, 0x007E),
            (0x2000, 0x206F),
        ),
        frozenset(["á", "é", "í", "ó", "ú", "ñ", "ü", "Á", "É", "Í", "Ó", "Ú", "Ñ", "Ü", "¡", "¿"]),
    ),
    "it": (
        (
            (0x0020, 0x007E),
            (0x2000, 0x206F),
        ),
        frozenset(["à", "è", "é", "ì", "í", "ò", "ó", "ù", "À", "È", "É", "Ì", "Í", "Ò", "Ó", "Ù"]),
    ),
    "pt": (
        (
            (0x0020, 0x007E),
            (0x2000, 0x206F),
        ),
        frozenset(
            [
                "á",
                "à",
                "â",
                "ã",
                "ç",
                "é",
                "ê",
                "í",
                "ó",
                "ô",
                "õ",
                "ú",
                "Á",
                "À",
                "Â",
                "Ã",
                "Ç",
                "É",
                "Ê",
                "Í",
                "Ó",
                "Ô",
                "Õ",
                "Ú",
            ]
        ),
    ),
}
"""Per-language allow-lists.

Only a handful of European languages are seeded here because they were
the most concrete real-world need at Phase-15 close. Adding ``ar`` /
``zh`` / ``ja`` / ``ko`` is a one-line edit but is intentionally deferred
to Phase 16+ once the wave-1 regression fixtures are in place to anchor
the calibration — claims about CJK / Arabic ratios without measured
fixtures would be marketing-grade, not engineering-grade.
"""

SUPPORTED_LANGUAGES: Tuple[str, ...] = tuple(sorted(_LANGUAGE_BLOCKS.keys()))


@dataclass(frozen=True)
class ScriptSanityReport:
    """Outcome of one ``check_script_sanity`` invocation.

    All fields are part of the public ``notes_structured`` schema; once
    surfaced under ``script_sanity_summary`` they are stable across
    releases. Adding fields is allowed; renaming or removing fields is
    a breaking change.
    """

    file_path: str
    language_hint: str
    total_chars: int
    out_of_script_chars: int
    ratio: float
    threshold: float
    triggered: bool
    # Per-codepoint counts for the worst offenders (top 8 by count). Useful
    # both for the warning text and for downstream regression tests that
    # need to assert the exact glyph caught the sanity check.
    top_offenders: Tuple[Tuple[str, int], ...] = field(default_factory=tuple)
    cause_hint: str = ""


def _is_in_script(
    char: str,
    ranges: Tuple[Tuple[int, int], ...],
    language_extras: FrozenSet[str],
    profile_extras: FrozenSet[str],
) -> bool:
    """Return ``True`` iff ``char`` is in the language ranges or any allow-list."""
    if char in _UNIVERSAL_ALLOW or char in language_extras or char in profile_extras:
        return True
    cp = ord(char)
    for low, high in ranges:
        if low <= cp <= high:
            return True
    return False


def _classify_cause(top_offenders: Tuple[Tuple[str, int], ...]) -> str:
    """Heuristic cause-of-corruption guess for the warning message.

    Looks at the most-frequent offending code points and matches against
    three well-known patterns:

    * **Font fallback** — Latin-1 region (U+0080..U+00FF) but matching
      the Turkish-glyph-fallback profile from
      :mod:`forgelm._pypdf_normalise`. Operators see "looks like
      pypdf font fallback — run ``forgelm doctor`` to confirm the
      normaliser table loaded, then re-ingest".
    * **Mojibake** — replacement characters (U+FFFD) dominating. Often
      means the source file was read with the wrong encoding.
    * **Custom bullet / private use** — code points in true PUA blocks
      (:data:`_PUA_RANGES`) or the Mandaic block (:data:`_MANDAIC_RANGE`,
      the audit's U+085F bullet artefact). Operators see "custom-glyph
      bullet detected — consider a normalisation profile or
      pre-process the source". Deliberately narrow: it must NOT fire on
      legitimate non-Latin scripts (Devanagari, Thai, Georgian, Hangul,
      Hiragana/Katakana, CJK Unified Ideographs, ...) that a document
      quoting or embedding foreign-script text can legitimately contain —
      those are "different script", not "corrupted glyph", and get no
      cause hint here (the ratio itself still flags them as out-of-script).

    Returns an empty string when no heuristic fires; the warning still
    emits but without a cause guess.
    """
    if not top_offenders:
        return ""
    # Look at the top three only — anything below that is unlikely to be
    # a single systematic failure mode.
    head = [glyph for glyph, _ in top_offenders[:3]]
    replacement_count = sum(1 for g in head if g == "�")
    if replacement_count:
        return "mojibake (replacement chars U+FFFD dominate)"
    latin1_extras = sum(1 for g in head if 0x80 <= ord(g) <= 0xFF)
    if latin1_extras >= 2:
        return "pypdf font fallback (Latin-1 substitutes)"
    pua_or_mandaic = sum(
        1
        for g in head
        if any(low <= ord(g) <= high for low, high in _PUA_RANGES) or _MANDAIC_RANGE[0] <= ord(g) <= _MANDAIC_RANGE[1]
    )
    if pua_or_mandaic:
        return "custom glyph / private-use block"
    return ""


def check_script_sanity(
    text: str,
    *,
    language_hint: str,
    file_path: str,
    threshold: float = DEFAULT_THRESHOLD,
    profile: str = "turkish",
    logger_override: Optional[logging.Logger] = None,
) -> ScriptSanityReport:
    """Compute the out-of-script ratio for ``text`` and warn when over threshold.

    The check is **fail-soft** — empty / whitespace-only input, an
    unknown ``language_hint``, or a zero-character corpus all return an
    untriggered :class:`ScriptSanityReport` without logging. Callers can
    therefore call this on every file without guards.

    Args:
        text: Extracted text from one file.
        language_hint: BCP-47-ish language code (``"tr"``, ``"en"``, ...).
            Unknown values disable the check; the report is returned with
            ``triggered=False`` so callers can detect the no-op case.
        file_path: Display path for the warning message (the report
            carries this verbatim so the operator can act on a specific
            file without re-deriving it from log lines).
        threshold: Out-of-script ratio above which the warning fires.
            Default :data:`DEFAULT_THRESHOLD`.
        profile: Glyph-normalisation profile active for this ingestion
            run. Code points the profile is allowed to emit are treated
            as in-script even if they fall outside the language ranges —
            this prevents the warning from firing on every successful
            normalisation pass.
        logger_override: Test hook so callers can pin the warning
            destination without monkeypatching the module logger.

    Returns:
        A :class:`ScriptSanityReport`. ``triggered`` is true iff a
        warning was emitted; ``ratio`` is always populated so callers
        can roll into structured notes regardless of trigger state.
    """
    use_logger = logger_override or logger
    entry = _LANGUAGE_BLOCKS.get(language_hint)
    if entry is None or not text:
        # Unknown language hint or empty text: produce an untriggered report
        # so the caller can still record the fact that the check ran.
        return ScriptSanityReport(
            file_path=file_path,
            language_hint=language_hint,
            total_chars=len(text),
            out_of_script_chars=0,
            ratio=0.0,
            threshold=threshold,
            triggered=False,
        )

    ranges, language_extras = entry
    profile_extras = emitted_codepoints(profile)
    out_of_script: Dict[str, int] = {}
    total = 0
    for char in text:
        if char.isspace():
            continue  # whitespace is neutral; don't count it toward the denominator
        total += 1
        if not _is_in_script(char, ranges, language_extras, profile_extras):
            out_of_script[char] = out_of_script.get(char, 0) + 1

    if total == 0:
        return ScriptSanityReport(
            file_path=file_path,
            language_hint=language_hint,
            total_chars=len(text),
            out_of_script_chars=0,
            ratio=0.0,
            threshold=threshold,
            triggered=False,
        )

    offending = sum(out_of_script.values())
    ratio = offending / total
    top_offenders = tuple(sorted(out_of_script.items(), key=lambda kv: kv[1], reverse=True)[:8])
    cause_hint = _classify_cause(top_offenders)

    triggered = ratio > threshold
    if triggered:
        sample = ", ".join(f"{glyph!r}={count}" for glyph, count in top_offenders[:5])
        cause_suffix = f" — likely cause: {cause_hint}" if cause_hint else ""
        use_logger.warning(
            "Script-sanity warning for '%s' (language_hint=%s): "
            "%.2f%% of non-whitespace chars fall outside the expected blocks "
            "(threshold=%.2f%%). Top offenders: %s%s",
            file_path,
            language_hint,
            ratio * 100,
            threshold * 100,
            sample,
            cause_suffix,
        )

    return ScriptSanityReport(
        file_path=file_path,
        language_hint=language_hint,
        total_chars=len(text),
        out_of_script_chars=offending,
        ratio=ratio,
        threshold=threshold,
        triggered=triggered,
        top_offenders=top_offenders,
        cause_hint=cause_hint,
    )


def report_to_structured(reports: List[ScriptSanityReport]) -> Dict[str, object]:
    """Aggregate per-file reports into the ``notes_structured`` block.

    Keeps the on-disk schema tight: only triggered reports show up under
    ``per_file`` so a clean 5000-file corpus does not produce a 5000-row
    JSON payload. Aggregate totals always emit so consumers can rely on
    the keys being present.
    """
    triggered = [r for r in reports if r.triggered]
    aggregate = {
        "files_checked": len(reports),
        "files_triggered": len(triggered),
        "max_ratio": max((r.ratio for r in reports), default=0.0),
        "threshold": reports[0].threshold if reports else DEFAULT_THRESHOLD,
    }
    if triggered:
        aggregate["per_file"] = [
            {
                "file_path": r.file_path,
                "ratio": r.ratio,
                "language_hint": r.language_hint,
                "cause_hint": r.cause_hint,
                "top_offenders": [{"glyph": glyph, "count": count} for glyph, count in r.top_offenders],
            }
            for r in triggered
        ]
    return aggregate


__all__ = [
    "DEFAULT_THRESHOLD",
    "SUPPORTED_LANGUAGES",
    "ScriptSanityReport",
    "check_script_sanity",
    "report_to_structured",
]
