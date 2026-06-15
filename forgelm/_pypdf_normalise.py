"""Phase 15 Task 3 — language-specific glyph-fallback normalisation for pypdf.

Some PDFs use Type-1 PostScript fonts whose ``Encoding`` dictionary maps
non-ASCII characters via custom glyph names that pypdf's CMap fallback
cannot resolve. pypdf produces what it can — typically Latin-1 placeholder
glyphs that look nothing like the source character. The 2026-05-11
ingestion-reliability audit measured this on a real-world Turkish-language
formal-publication PDF: Turkish characters surfaced as ``ø Õ ú ÷ ࡟`` etc.,
with ~7 % corrupt-char ratio on the front-matter pages and a chapter-level
section using ``U+085F`` (Mandaic punctuation) for bullet points.

This module ships a small, **language-specific** lookup table that maps
the measured corrupt glyphs back to their correct Turkish characters,
plus a profile dispatcher so future phases can drop in additional
profiles (Arabic, CJK) without re-shaping the API.

Design rules:

* **Pure data, no I/O.** The table is a module-level constant so it is
  trivially testable and adds no import-time cost.
* **Single-pass replacement.** :func:`apply_profile` uses one ``str``
  scan per profile so even a megabyte-sized chunk normalises in linear
  time. The previous draft used a regex-alternation; the dict approach
  is simpler and avoids the cost of building a giant pattern.
* **Order matters.** A handful of the measured artefacts are
  *multi-character* sequences (e.g. ``ö`` followed by a trailing space
  for ``Ğ``). The applier handles those before the single-char passes
  so a single-char substitution does not break the multi-char hit.
* **Opt-out per profile.** Callers pass ``profile="none"`` to disable
  normalisation entirely; the function returns the input unchanged.

The public surface is intentionally minimal — one function and one
``PROFILES`` map. The doctor diagnostic (Phase 15 task 3 acceptance)
loads :data:`PROFILES` and reports ``pypdf_normalise.<profile>: pass``
so an operator can verify the table is intact without running an ingest.
"""

from __future__ import annotations

from typing import Dict, FrozenSet, Tuple

# ---------------------------------------------------------------------------
# Turkish profile — derived from the 2026-05-11 audit measurements
# ---------------------------------------------------------------------------

# Multi-character sequences are applied FIRST so a single-char substitution
# below cannot prematurely consume one of their leading characters. Each
# entry is ``(source_sequence, target_codepoint)``. The trailing space in
# ``"ö "`` (the audit-measured ``Ğ`` artefact) is intentional — pypdf emits
# the corrupt glyph with a stray space because the source font's advance
# width is over-allocated. Stripping the space would re-glue ``Ğ`` to the
# following character and silently break tokenisation.
_TURKISH_MULTI: Tuple[Tuple[str, str], ...] = (
    ("ö ", "Ğ"),  # U+011E LATIN CAPITAL LETTER G WITH BREVE
)

# Single-character substitutions. Kept as a plain ``dict`` so callers can
# inspect the size / membership in a single keyword (``in``). The values
# are bare Python str (not literal escapes) so a future reader can read
# what the table actually contains in their editor.
_TURKISH_SINGLE: Dict[str, str] = {
    "ø": "İ",  # U+0130 LATIN CAPITAL LETTER I WITH DOT ABOVE
    "Õ": "ı",  # U+0131 LATIN SMALL LETTER DOTLESS I
    "ú": "ş",  # U+015F LATIN SMALL LETTER S WITH CEDILLA
    "÷": "ğ",  # U+011F LATIN SMALL LETTER G WITH BREVE
    "࡟": "•",  # U+085F MANDAIC PUNCTUATION → U+2022 BULLET (custom bullet glyph)
}

# Code points the profile is allowed to *emit*. Surface-exposed so the
# script-sanity check (Phase 15 task 2) can mask its output against the
# normaliser's expected vocabulary — i.e. a chunk that still carries a
# normalised Turkish char counts as "in-script" even though that char
# came from a glyph-fallback corruption.
_TURKISH_EMITTED: FrozenSet[str] = frozenset(list(_TURKISH_SINGLE.values()) + [target for _, target in _TURKISH_MULTI])


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


PROFILES: Tuple[str, ...] = ("turkish", "none")
"""Supported normalisation profiles.

``turkish`` is the only language-specific profile shipped today; future
work may add ``arabic`` / ``cjk`` once the failure-mode evidence is
collected. ``none`` is an explicit no-op so a CLI flag can disable the
behaviour without special-casing ``profile is None`` at every call site.
"""

DEFAULT_PROFILE: str = "none"
"""Default profile applied at chunk-write time.

Phase 15 round-1 review (C-2): the original default of ``turkish``
silently rewrote legitimate non-Turkish letters (``Bjørk`` → ``BjİrkĞ``,
``Õrö`` collapse) because the normaliser fires regardless of the
operator's language hint. The fix is to default to ``none`` and let
the CLI dispatcher derive the right profile from ``--language-hint``:
``--language-hint tr`` auto-selects ``turkish`` unless
``--normalise-profile`` is set explicitly; every other hint stays on
``none``. Operators on a Turkish corpus continue to get the benefit
without having to know the flag's name; everyone else is safe by
default.
"""


def apply_profile(text: str, profile: str = DEFAULT_PROFILE) -> str:
    """Apply the normalisation profile to ``text``.

    Returns the input unchanged when ``profile == "none"`` or when the
    profile name is not recognised (the latter is **not** an error — a
    forward-compatible caller might pass a profile name that older
    ForgeLM releases do not know yet, and we silently fall through
    rather than crash a long ingestion run). Operators who want strict
    profile validation should check :data:`PROFILES` membership at the
    CLI boundary first.

    The single-char substitutions preserve every other code point
    byte-for-byte. The ``turkish`` profile additionally carries one
    **multi-character** rule (:data:`_TURKISH_MULTI`: ``"ö " → "Ğ"``)
    that is a 2→1, space-consuming transform — it is therefore **not**
    1:1 and is lossy on a legitimate ``"ö "`` sequence (a token ending
    in ``ö`` before a space is merged with the next token). That is an
    accepted trade-off for the glyph-fallback recovery the profile
    exists to perform; it only fires on the ``--language-hint tr`` opt-in
    path (:data:`DEFAULT_PROFILE` is ``"none"``). The transform is **not**
    1:1 reversible, but it **is** deterministic per profile: this is the
    determinism contract documented in
    ``docs/reference/data_ingestion_architecture.md`` — re-running
    ingestion against the same source with the same profile must
    produce a byte-identical JSONL.
    """
    if profile == "none" or not text:
        return text
    if profile == "turkish":
        return _apply_turkish(text)
    # Unknown profile — silently no-op for forward compatibility.
    return text


def _apply_turkish(text: str) -> str:
    """Turkish-specific applier. Multi-char passes precede single-char ones."""
    out = text
    for src, dst in _TURKISH_MULTI:
        out = out.replace(src, dst)
    # ``str.translate`` is the fastest way to apply a bag of single-character
    # substitutions in CPython — it visits each source char exactly once and
    # the lookup is constant-time.
    out = out.translate(str.maketrans(_TURKISH_SINGLE))
    return out


def count_substitutions(text: str, profile: str = DEFAULT_PROFILE) -> int:
    """Return the **exact** number of substitutions ``apply_profile`` would
    perform on ``text``.

    Phase 15 round-3 review: the previous heuristic in ``ingestion.py``
    zipped the pre- and post-normalisation strings character-by-character
    and added an absolute length delta. That over-counted when a
    multi-char rule (``"ö " → "Ğ"``) shortened the text by one character
    AND the surrounding chars happened to differ, AND it under-counted
    when consecutive substitutions cancelled in the zip view. The exact
    count is cheap to compute via ``text.count(src)`` for each rule
    (multi-char rules counted first because their substitution consumes
    one of the single-char triggers as a prefix — same precedence as
    :func:`apply_profile`).
    """
    if profile == "none" or not text:
        return 0
    if profile != "turkish":
        return 0
    total = 0
    remaining = text
    for src, _dst in _TURKISH_MULTI:
        hits = remaining.count(src)
        if hits:
            total += hits
            remaining = remaining.replace(src, "")
    for src in _TURKISH_SINGLE:
        total += remaining.count(src)
    return total


def profile_summary(profile: str) -> Dict[str, int]:
    """Return ``{"multi": N, "single": M}`` for the named profile.

    Doctor uses this to render the diagnostic row without exposing the
    raw tables (which would clutter the JSON envelope on every probe).
    Returns ``{"multi": 0, "single": 0}`` for ``"none"`` or unknown
    profile names so callers can rely on the schema being identical.
    """
    if profile == "turkish":
        return {"multi": len(_TURKISH_MULTI), "single": len(_TURKISH_SINGLE)}
    return {"multi": 0, "single": 0}


def emitted_codepoints(profile: str) -> FrozenSet[str]:
    """Return the set of code points the profile may emit.

    Used by the script-sanity check (Phase 15 task 2) so a chunk whose
    only "unexpected" chars are the targets the normaliser produced is
    not flagged as font-corrupt. Empty frozenset for ``"none"`` /
    unknown profiles.
    """
    if profile == "turkish":
        return _TURKISH_EMITTED
    return frozenset()


__all__ = [
    "PROFILES",
    "DEFAULT_PROFILE",
    "apply_profile",
    "count_substitutions",
    "profile_summary",
    "emitted_codepoints",
]
