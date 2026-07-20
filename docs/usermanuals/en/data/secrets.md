---
title: Secrets Scrubbing
description: Detect and redact AWS keys, GitHub PATs, JWTs, PEM blocks, and other credentials from training data.
---

# Secrets Scrubbing

Code repositories, support tickets, and operational logs leak credentials. Once those credentials end up in a training set and the model is deployed, anyone who chats with the model can extract them. Secrets scrubbing prevents this at ingest.

## What gets detected

The bundled detector ships **9 secret families** under `_SECRET_PATTERNS` (`forgelm/data_audit/_secrets.py::_SECRET_PATTERNS`):

| Pattern key | Anchor |
|---|---|
| `aws_access_key` | `AKIA` / `ASIA` + 16 uppercase alphanum |
| `github_token` | `ghp_*`, `gho_*`, `ghu_*`, `ghs_*`, `ghr_*`, `github_pat_*` (single combined family) |
| `slack_token` | `xox[baprs]-*` |
| `openai_api_key` | `sk-*` and `sk-proj-*` |
| `google_api_key` | `AIza` + 35 chars |
| `jwt` | Three-segment base64url with canonical JWT header keys (defends against `eyJ.eyJ.X`-shaped prose false positives) |
| `openssh_private_key` | `BEGIN OPENSSH/RSA/DSA/EC PRIVATE KEY` … `END …` (full PEM envelope) |
| `pgp_private_key` | `BEGIN PGP PRIVATE KEY BLOCK` … `END …` |
| `azure_storage_key` | `DefaultEndpointsProtocol=…AccountKey=…` |

All matches are replaced with the literal string `[REDACTED-SECRET]` by `mask_secrets()` (`forgelm/data_audit/_secrets.py::mask_secrets`). The detector does **not** ship per-vendor patterns for Anthropic, Stripe, SendGrid, or Twilio today — operators with those traffic types extend the regex set out-of-tree (Phase 28+ backlog tracks shipping them as opt-in extras).

## Quick example

```shell
$ forgelm ingest ./support-tickets/ \
    --recursive \
    --secrets-mask \
    --output data/tickets.jsonl
✓ masked 47 secrets:
    aws_access_key:       12
    github_token:          8
    jwt:                  18
    openssh_private_key:   2
    openai_api_key:        7
```

## What "PEM block" means

PEM private keys span multiple lines:

```text
-----BEGIN RSA PRIVATE KEY-----
MIIEpAIBAAKCAQEA1+...
...
-----END RSA PRIVATE KEY-----
```

ForgeLM's PEM detector (`openssh_private_key` family — also covers RSA / DSA / EC envelopes) matches the entire block (BEGIN to END), not just the marker line. Like every other family, the whole block is replaced with `[REDACTED-SECRET]` — there is no per-family token (`mask_secrets()` ships a single `replacement="[REDACTED-SECRET]"` constant; `forgelm/data_audit/_secrets.py::mask_secrets`). This avoids the common bug of detecting the BEGIN line but leaving the key body in the JSONL.

## Audit-only mode

```shell
$ forgelm audit data/tickets.jsonl
Data audit summary
  Source        : /srv/corpora/tickets.jsonl
  Total samples : 8400
  Splits        : train
  └─ train: n=8400
     length  min=44 max=2317 mean=311.5 p95=980
     languages (top-3): en=8400
     secrets         : aws_access_key=12, jwt=18
  Secrets        : CRITICAL — 30 flagged (aws_access_key=12, jwt=18)

Report written to: audit/data_audit_report.json
```

The secrets scan is always on — it cannot be disabled from the CLI surface (a credential leak in training data is never something the operator should be able to wave away).

A critical-severity finding **exits `3`**, so a CI pipeline fails fast:

```text
[ERROR] Secrets gate FAILED (critical): 1 credential/secret span(s) detected (aws_access_key=1).
Do not train on this corpus — a credential in training data is memorised and re-emitted at
inference time. Scrub it with `forgelm ingest --secrets-mask`, or re-run
`forgelm audit --allow-secrets` to record the findings without failing the pipeline. Exiting 3.
```

Verified: a corpus containing `AKIAIOSFODNN7EXAMPLE` exits `3`; the same corpus with `--allow-secrets` exits `0` and logs a `SUPPRESSED` warning instead.

:::warn
**This gate did not always fire.** Until recently `forgelm audit` printed `Secrets : CRITICAL — N flagged` and exited `0`, so any credential-leak gate wired up on the strength of this page's old "exits non-zero" promise was silently dead. If you built one before this release, re-run it against a corpus with a known dummy credential and confirm you now get exit `3` — and re-audit any corpus that passed the dead gate.
:::

Secrets are **not** the only finding that gates any more: `forgelm audit` has a sibling **PII gate** that exits `3` on critical-tier PII (`credit_card`, `iban` — the checksum-validated categories), suppressed by its own `--allow-pii` flag. The two are independent — passing one leaves the other armed — and both report before either exits, so a corpus carrying a leaked key *and* a real card number shows both errors in one run. Sub-critical PII (national IDs, email, phone), cross-split leakage, near-duplicates and quality flags are still reported at exit `0` however severe — gate on those with `jq` over the JSON envelope. The exit-code table and the reasoning for the PII gate's narrow scope are in [Dataset Audit](#/data/audit).

## Programmatic API

Both functions take a single string and are re-exported from `forgelm.data_audit`. `detect_secrets` returns a **count map**, not spans — there is no row-level span or value surface.

```python
from forgelm.data_audit import detect_secrets, mask_secrets

text = "Use this key: AKIAIOSFODNN7EXAMPLE for the bucket."
print(detect_secrets(text))
# {'aws_access_key': 1}

print(mask_secrets(text))
# Use this key: [REDACTED-SECRET] for the bucket.
```

Signatures: `detect_secrets(text) -> Dict[str, int]` and `mask_secrets(text, replacement='[REDACTED-SECRET]', *, return_counts=False)`. Pass `return_counts=True` to get a `(masked_text, counts)` tuple. Because the return is a count map, you cannot recover *where* in the row a credential appeared — plan reviews around per-row iteration in your own code.

## How detection actually works

`detect_secrets` is a plain loop of `pattern.findall(text)` over nine prefix-anchored regexes, returning one count per family (`forgelm/data_audit/_secrets.py`). Precision comes entirely from how narrow those anchors are — `aws_access_key` requires the literal `AKIA` prefix, `jwt` requires a `eyJ`-prefixed three-segment structure, `github_token` requires `ghp_`/`gho_`/etc. The module deliberately does **not** match generic high-entropy strings, which is the main source of noise in "git-secrets"-style tools.

The nine families are `aws_access_key`, `azure_storage_key`, `github_token`, `google_api_key`, `jwt`, `openai_api_key`, `openssh_private_key`, `pgp_private_key`, `slack_token`. Anthropic / Stripe / SendGrid / Twilio patterns are not shipped; operators with that traffic profile extend `_SECRET_PATTERNS` out-of-tree.

:::warn
**There is no entropy check, no context window, and no test/example exclusion list.** Earlier versions of this page described all three. None exist in the code, and the practical consequence runs the opposite way to what that text implied: dummy values **are** detected. `detect_secrets("Use this key: AKIAIOSFODNN7EXAMPLE")` returns `{'aws_access_key': 1}` — the canonical AWS documentation placeholder fires, with no `aws` token anywhere nearby. Expect your fixtures, test data and documentation samples to light up, and triage them by hand.
:::

For a high-stakes audit (e.g. a legal disclosure scan), `forgelm audit` records findings under `secrets_summary` (one count per pattern family). Walk that map for any count > 0 so a human can confirm which hits are live credentials and which are placeholders — the tool cannot make that distinction for you.

## Configuration

The secrets scanner is **always-on inside `forgelm audit`** — it has no enable/disable knob and no per-family allow/deny list. Mask-on-emit is controlled by the `secrets_mask: bool` argument on `audit_dataset()` (and the `--secrets-mask` flag on `forgelm ingest`); the replacement string is the single fixed `[REDACTED-SECRET]` constant inside `mask_secrets()`. There is no `ingestion.secrets_mask:` YAML block, no `enabled` / `tag_by_category` / `strict` / `categories` sub-fields — those names appeared in earlier doc drafts but never shipped. To extend or restrict the family set, fork `forgelm/data_audit/_secrets.py::_SECRET_PATTERNS`.

## Common pitfalls

:::warn
**Disabling secrets-mask for "trusted internal" data.** Internal logs are the most common source of credential leaks. The cost of running the masker is essentially zero; the cost of a leaked AWS key in a deployed model is unbounded.
:::

:::warn
**Custom regex without entropy checks.** The biggest cause of secrets-detection false positives is regex-only patterns matching documentation examples. Always pair regex with entropy or context checks.
:::

:::tip
For corpora that legitimately contain certificates / tokens (security training datasets, CTF content), there is no CLI escape hatch — the secrets scan is intentionally always-on (no `--no-secrets` / `--skip-secrets` flag exists, and `forgelm audit` runs the scan unconditionally on every invocation; see the [Audit-only mode](#audit-only-mode) section above for the underlying scan-mode semantics). Mark the rows in your corpus's data-governance manifest as `legitimate_secret_content: true` so a downstream reviewer sees the rationale; `forgelm audit` still flags them, but the reviewer dismisses the flag with the manifest line as evidence.
:::

## See also

- [PII Masking](#/data/pii-masking) — sister feature for personal data.
- [Dataset Audit](#/data/audit) — covers secrets detection in audit-only mode.
- [Document Ingestion](#/data/ingestion) — where secrets-mask is invoked.
