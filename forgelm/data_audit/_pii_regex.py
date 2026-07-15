"""Regex-based PII detector + masker.

Prefix-anchored / shape-anchored patterns covering the GDPR-mandated
structured identifiers (email, phone, IBAN, credit card, national IDs).
These are the categories every audit *must* surface; the optional
Presidio ML-NER adapter (:mod:`forgelm.data_audit._pii_ml`) layers on
top to pick up unstructured identifiers (person names, organizations,
locations) which regex inherently misses.
"""

from __future__ import annotations

import re
from typing import Any, Dict

# ---------------------------------------------------------------------------
# PII regex — module level so they're compiled once
# ---------------------------------------------------------------------------


# Pattern dict iteration order = scan / mask precedence. Keep most specific
# patterns first so a span that could match two categories is attributed to
# the narrower one (e.g. an SSN is also a digit run; we want it flagged as
# us_ssn, not as phone). When the same span matches multiple patterns during
# masking, the FIRST pattern in this dict wins and the span is replaced
# before the next pattern sees it — that's the documented "first match wins"
# semantics referenced in :func:`mask_pii`.
#
# Intentional Unicode (regex.md rule 1): the national-ID / phone / credit-card
# patterns use bare ``\d``, which is Unicode-aware by default and so also
# matches Arabic-Indic, fullwidth, Devanagari, etc. digit forms. This is
# deliberate — it raises recall on internationalised digit forms and matches
# the audit's documented over-report posture; the validators ``int()`` /
# ``str.isdigit()`` in this module are Unicode-safe too. Do NOT copy these
# patterns into an ASCII-only credential context (the ``_secrets.py`` patterns
# carry ``re.ASCII`` for exactly that reason).
_PII_PATTERNS: Dict[str, re.Pattern] = {
    # ASCII class: RFC 5321 constrains email local-part and domain to US-ASCII
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,}\b"),
    # IBAN: 2-letter country + 2 check digits + 11-30 alphanumerics. The body
    # allows an optional single space before each character so the ISO 13616
    # print grouping ("TR33 0006 1001 5478 …") — how IBANs actually appear on
    # invoices / statements / email — is detected, not only the compact form.
    # ``(?: ?[A-Z0-9])`` (no single-char class per regex.md rule 2); the space
    # and the alphanumeric are disjoint so the two never compete, and the
    # {11,30} bound (regex.md rule 3) keeps backtracking bounded — linearity
    # verified at tests/test_data_audit.py::TestPiiRegexLinearity.
    "iban": re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]){11,30}\b"),
    # Credit cards captured first within the digit-run categories, then
    # Luhn-validated (see _is_credit_card). Greedy ``*`` instead of ``*?``:
    # both match the same set of strings here (``\b`` end-anchor forces a
    # full match) but the greedy form avoids unnecessary engine backtracking.
    "credit_card": re.compile(r"\b(?:\d[ -]*){13,19}\b"),
    "us_ssn": re.compile(r"\b(?!000|666|9\d{2})\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b"),
    # Non-capturing groups (?:...) throughout: capturing groups make
    # ``pattern.findall`` return a tuple of only the captured spans instead of
    # the full match, which would corrupt ``detect_pii``'s payload (and any
    # future checksum validation) for this one pattern. Every other entry in
    # this dict is group-free for the same reason.
    "fr_ssn": re.compile(r"\b[12]\d{2}(?:0[1-9]|1[0-2])(?:2[AB]|\d{2})\d{3}\d{3}(?:\d{2})?\b"),
    "tr_id": re.compile(r"\b\d{11}\b"),  # TR national ID is 11 digits, see _is_tr_id
    # German Personalausweis serial: leading letter, then 7-8 digits, then
    # optional alphanumeric check char. Tighter than the previous
    # ``[A-Z0-9]{9,10}`` which collided with IATA codes / UUID fragments /
    # API-key fragments.
    "de_id": re.compile(r"\b[A-Z]\d{7,8}[A-Z0-9]?\b"),
    # Phone numbers — the noisiest pattern in production. Anchored to either
    # an international prefix ('+') or a parenthesized area code so that
    # bare digit runs (timestamps, log line numbers, ISO dates, ID codes)
    # don't trip false positives. Use ingestion --pii-mask to redact at write
    # time; keep audit's recall slightly lower than the other categories to
    # avoid audit fatigue.
    # Every quantifier is bounded and each optional ``[\s.-]?`` sits over a
    # character class disjoint from the adjacent ``\d`` run, so no two
    # quantifiers compete for the same characters (regex.md rule 4). ReDoS
    # linearity verified at tests/test_data_audit.py::TestPiiRegexLinearity.
    "phone": re.compile(
        r"(?<!\w)"
        r"(?:"
        r"\+\d{1,3}[\s.-]?\d{2,4}[\s.-]?\d{2,4}[\s.-]?\d{0,4}"  # +CC area#-#-#
        r"|"
        r"\(\d{2,4}\)[\s.-]?\d{2,4}[\s.-]?\d{2,4}"  # (area) #-#
        r")"
        r"(?!\w)"
    ),
}


def _is_credit_card(candidate: str) -> bool:
    digits = [int(c) for c in candidate if c.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    # Luhn check.
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _is_tr_id(candidate: str) -> bool:
    """Validate TR national ID (TC Kimlik No) by its checksum rules."""
    if len(candidate) != 11 or not candidate.isdigit():
        return False
    digits = [int(c) for c in candidate]
    if digits[0] == 0:
        return False
    odd_sum = digits[0] + digits[2] + digits[4] + digits[6] + digits[8]
    even_sum = digits[1] + digits[3] + digits[5] + digits[7]
    if (odd_sum * 7 - even_sum) % 10 != digits[9]:
        return False
    return sum(digits[:10]) % 10 == digits[10]


def _validate_match(pii_type: str, match: str) -> bool:
    if pii_type == "credit_card":
        return _is_credit_card(match)
    if pii_type == "tr_id":
        return _is_tr_id(match)
    return True


def detect_pii(text: Any) -> Dict[str, int]:
    """Return a ``{pii_type: count}`` map for the given string.

    The signature is intentionally ``Any`` — the audit calls this with
    arbitrary JSONL row payloads and we explicitly want a defensive empty
    return for ``None`` / numbers / lists rather than a TypeError. String
    callers see no behavioural difference; static-checker friction goes
    away.

    Validation: credit cards run through Luhn; TR national IDs run through
    the TC Kimlik No checksum. Other categories use regex shape only — false
    positives are intentional (the audit is meant to over-report and let the
    operator decide).
    """
    counts: Dict[str, int] = {}
    if not text or not isinstance(text, str):
        return counts
    for pii_type, pattern in _PII_PATTERNS.items():
        for match in pattern.findall(text):
            payload = match if isinstance(match, str) else " ".join(p for p in match if p)
            if not payload:
                continue
            if not _validate_match(pii_type, payload):
                continue
            counts[pii_type] = counts.get(pii_type, 0) + 1
    return counts


def mask_pii(
    text: Any,
    replacement: str = "[REDACTED]",
    *,
    return_counts: bool = False,
) -> Any:
    """Return ``text`` with every detected PII span replaced by ``replacement``.

    Like :func:`detect_pii`, the input type is ``Any`` so callers passing
    arbitrary JSONL payloads get a defensive passthrough on non-strings
    rather than a TypeError. ``None`` returns ``None``; ints / lists / etc.
    are returned unchanged.

    Pattern precedence is the dict order in :data:`_PII_PATTERNS` — most
    specific patterns first (email, IBAN, credit card, national IDs) so a
    span that would match multiple categories is attributed to the narrower
    one. Phone is scanned LAST and is anchored to ``+CC`` or ``(area)``
    formats so bare digit runs (timestamps, IDs, dates) do not collide.

    Args:
        text: Input string. Non-string values are returned unchanged.
        replacement: String to substitute in for each detected span.
        return_counts: When True, return ``(masked_text, counts_dict)`` where
            ``counts_dict[pii_type]`` is the number of spans actually replaced
            by THIS pattern in this call. Multi-pattern overlap is reported
            only once per span (the first / most specific pattern wins, the
            same way mask_pii rewrites the text). Default ``False`` keeps
            backwards compat for the 1-arg form.
    """
    if not text or not isinstance(text, str):
        return (text, {}) if return_counts else text
    counts: Dict[str, int] = {}
    out = text
    for pii_type, pattern in _PII_PATTERNS.items():

        def _replace(match: re.Match, _t: str = pii_type) -> str:
            if _validate_match(_t, match.group(0)):
                counts[_t] = counts.get(_t, 0) + 1
                return replacement
            return match.group(0)

        out = pattern.sub(_replace, out)
    return (out, counts) if return_counts else out


__all__ = [
    "_PII_PATTERNS",
    "_is_credit_card",
    "_is_tr_id",
    "_validate_match",
    "detect_pii",
    "mask_pii",
]
