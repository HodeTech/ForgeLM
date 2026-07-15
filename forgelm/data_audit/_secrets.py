"""Phase 12: code/credentials leakage tagger.

Anchored prefix-matching regex set covering the credential families that
mainstream secret scanners agree on (AWS, GitHub, Slack, OpenAI, Google,
JWTs, OpenSSH/PGP private-key blocks, Azure storage). Always-on at audit
time — credentials in training data get memorised by the fine-tuned model
and re-emitted at inference time, which is the single most damaging
documentation-drift class for an SFT corpus.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Tuple

# Regex set used to detect credentials/secrets in a text payload. Keep
# patterns narrow — false positives in this category waste the operator's
# attention and (more importantly) erode trust in the audit. Each pattern
# is anchored on the canonical prefix the secret format publishes; we do
# NOT try to match generic high-entropy strings here because dedicated
# scanners do that better and we prefer to keep the regex coverage tight.
SECRET_TYPES: Tuple[str, ...] = (
    "aws_access_key",
    "github_token",
    "slack_token",
    "openai_api_key",
    "google_api_key",
    "jwt",
    "openssh_private_key",
    "pgp_private_key",
    "azure_storage_key",
)


# Upper bound on the character span between a private-key BEGIN and END marker.
# The previous patterns used an UNBOUNDED lazy ``.*?`` under ``re.DOTALL``:
# every BEGIN with no matching END forced a full lazy scan to end-of-string,
# which is O(n^2) on a row carrying many banner-like ``-----BEGIN … PRIVATE
# KEY-----`` substrings (a scraped doc, a corrupted PEM dump, one crafted
# training row) — measured at ~18.9 s on a 500 KB adversarial row, a DoS on
# the ``forgelm audit`` / ``ingest`` hot path (regex.md "ReDoS exposure
# budget"). Bounding the lazy span makes each BEGIN cost O(N) instead of O(n),
# so the scan is linear in row length. 8192 comfortably covers the largest
# realistic PEM/PGP body (RSA-8192 base64 ≈ 6.4 KB); larger key blocks are a
# recall miss, never a stall. Linearity verified at
# tests/test_data_audit_phase12.py::TestSecretsPrivateKeyReDoS.
_PRIVATE_KEY_BODY_MAX_CHARS: int = 8192


_SECRET_PATTERNS: Dict[str, re.Pattern] = {
    # AWS access key IDs follow AKIA / ASIA + 16 uppercase alphanum.
    "aws_access_key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    # GitHub fine-grained / classic PAT prefixes (see GitHub token-format docs).
    # ``re.ASCII`` because GitHub tokens are strictly ASCII — Python's default
    # ``\w`` is Unicode-aware, which would otherwise let non-ASCII chars leak
    # into the match universe (regex.md rule 1).
    "github_token": re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr|github_pat)_\w{20,}", flags=re.ASCII),
    # Slack bot / user / app / config tokens.
    "slack_token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    # OpenAI API keys (legacy ``sk-...`` and project-scoped ``sk-proj-...``).
    # ``[\w-]`` + ``re.ASCII`` keeps ``\w`` ASCII-bounded.
    "openai_api_key": re.compile(r"\bsk-(?:proj-)?[\w-]{20,}\b", flags=re.ASCII),
    # Google API keys (Maps, Cloud, etc.).
    "google_api_key": re.compile(r"\bAIza[\w-]{35}\b", flags=re.ASCII),
    # JSON Web Tokens — header.payload.sig. We anchor the header segment
    # on the base64url prefix of the canonical JWT header keys (``alg`` /
    # ``typ`` / ``kid`` / ``cty`` / ``enc``) and require minimum lengths
    # on payload + signature, so generic ``eyJ.eyJ.X``-shaped strings in
    # prose don't false-positive. This still catches >99 % of real JWTs.
    # ``re.ASCII`` because base64url is ASCII-only.
    "jwt": re.compile(
        r"\beyJ(?:hbGc|0eXA|raWQ|jdHk|lbmM|hcGk)[\w-]{10,}"
        r"\.eyJ[\w-]{10,}"
        r"\.[\w-]{15,}\b",
        flags=re.ASCII,
    ),
    # Private-key blocks — full PEM/PGP envelope (BEGIN through END inclusive)
    # so ``mask_secrets`` redacts the entire block, not just the header line.
    # The literal block markers below are spelled with concatenation to keep
    # naive secret-scanners on the source tree from misreading the regex
    # itself as a leaked private key.
    # ``.{0,N}?`` (bounded lazy span) rather than ``.*?`` — see
    # ``_PRIVATE_KEY_BODY_MAX_CHARS`` for the ReDoS rationale.
    "openssh_private_key": re.compile(
        r"-----" + r"BEGIN " + r"(?:OPENSSH|RSA|DSA|EC) PRIVATE KEY-----"
        r".{0," + str(_PRIVATE_KEY_BODY_MAX_CHARS) + r"}?"
        r"-----" + r"END " + r"(?:OPENSSH|RSA|DSA|EC) PRIVATE KEY-----",
        re.DOTALL,
    ),
    "pgp_private_key": re.compile(
        r"-----" + r"BEGIN " + r"PGP PRIVATE KEY BLOCK-----"
        r".{0," + str(_PRIVATE_KEY_BODY_MAX_CHARS) + r"}?"
        r"-----" + r"END " + r"PGP PRIVATE KEY BLOCK-----",
        re.DOTALL,
    ),
    # Azure storage account keys are 88-char base64; we narrow on the
    # common ``DefaultEndpointsProtocol`` connection-string context.
    "azure_storage_key": re.compile(
        r"DefaultEndpointsProtocol=https?;AccountName=[A-Za-z0-9]+;AccountKey=[A-Za-z0-9+/=]{20,}"
    ),
}


def detect_secrets(text: Any) -> Dict[str, int]:
    """Return ``{secret_type: count}`` for credentials/keys leaked in ``text``.

    Uses :data:`_SECRET_PATTERNS` (anchored regexes). The audit calls this
    once per row payload; the regex set is intentionally narrow
    (prefix-anchored) to keep false positives low.
    """
    counts: Dict[str, int] = {}
    if not text or not isinstance(text, str):
        return counts
    for kind, pattern in _SECRET_PATTERNS.items():
        hits = pattern.findall(text)
        if hits:
            counts[kind] = len(hits)
    return counts


def mask_secrets(
    text: Any,
    replacement: str = "[REDACTED-SECRET]",
    *,
    return_counts: bool = False,
) -> Any:
    """Return ``text`` with detected secret spans replaced by ``replacement``.

    Mirrors :func:`mask_pii`'s API surface (``return_counts`` for the
    truthful per-type tally). Non-string input passes through. Used by
    ``forgelm ingest --secrets-mask`` to scrub credentials before chunks
    land in the JSONL — fine-tuning a model on a corpus that includes
    real API keys causes them to be memorised at training time.
    """
    if not text or not isinstance(text, str):
        return (text, {}) if return_counts else text
    counts: Dict[str, int] = {}
    out = text
    for kind, pattern in _SECRET_PATTERNS.items():

        def _replace(match: re.Match, _t: str = kind) -> str:
            counts[_t] = counts.get(_t, 0) + 1
            return replacement

        out = pattern.sub(_replace, out)
    return (out, counts) if return_counts else out


__all__ = ["SECRET_TYPES", "_SECRET_PATTERNS", "detect_secrets", "mask_secrets"]
