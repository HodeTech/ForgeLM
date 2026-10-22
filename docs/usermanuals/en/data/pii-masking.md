---
title: PII Masking
description: Detect and redact emails, phones, credit cards, IBAN, and national IDs at ingest time.
---

# PII Masking

Personal data in your training set is a regulatory hazard (GDPR Article 5(1)(c) — data minimisation) and an operational hazard (the model memorises and emits it). ForgeLM's PII masker detects eight categories of PII and redacts them at ingest time, before chunks land in JSONL.

## What gets detected

| Category | Examples | How |
|---|---|---|
| **Email** | `alice@example.com` | RFC 5321-compatible regex |
| **Phone** | `+90 532 123 45 67`, `(555) 123-4567` | E.164-compatible patterns + locale variants |
| **Credit card** | `4111-1111-1111-1111` | Issuer prefix (Visa/MC/Amex/Discover/JCB/UnionPay) + Luhn check — not Luhn alone, which every IMEI passes |
| **IBAN** | `TR12 0006 4000 0011 2345 6789 01` | Country-aware checksum |
| **National ID — Turkey** | 11-digit TC kimlik | Two modulo-10 check digits (10th and 11th) |
| **National ID — Germany** | ID-card serial (Personalausweis), e.g. `T22000129` | Shape only: a letter, 7-8 digits and an optional check character; no checksum |
| **National ID — France** | NIR (social security number) | Shape only: sex, year, month, department, commune and order digits plus an optional two-digit key; the key is not verified |
| **US SSN** | `123-45-6789` | Format + reserved-block exclusions |

These eight are the complete set (`_PII_PATTERNS` in `forgelm/data_audit/_pii_regex.py`). **IP addresses are not detected** — earlier versions of this page listed an "IPv4 / IPv6 (off by default; opt in)" row, but no such pattern and no such opt-in exists. If your GDPR assessment treats IP addresses as personal data, you need a separate control for them.

## Quick example

At ingest time:

```shell
$ forgelm ingest ./policies/ \
    --recursive --strategy markdown \
    --pii-mask \
    --output data/policies.jsonl
✓ masked 18 PII matches across 12,240 chunks
```

After ingest, every match is replaced with a placeholder:

```text
Before: "Send your CV to ali@example.com or call +90 532 123 45 67."
After:  "Send your CV to [REDACTED] or call [REDACTED] 67."
```

The placeholder is consistent across the dataset, so a model can still learn that *something* redacted goes in that slot — just not the specific value.

## The placeholder emitted

:::warn
**There is one placeholder, not one per category.** Every detected span — email, phone, credit card, IBAN, TC kimlik, German ID-card serial, NIR, US SSN — is replaced with the single literal `[REDACTED]`. Earlier versions of this page published a nine-row table of per-category tags (`[EMAIL_REDACTED]`, `[PHONE_REDACTED]`, `[IP_REDACTED]`, …). None of those strings exist anywhere in the codebase. A downstream consumer that parses redaction tags to recover *which* category was masked cannot do so — that information survives only in the audit report's `pii_summary` counts.
:::

The default is `[REDACTED]` (`mask_pii`'s `replacement` parameter). The parallel secrets masker uses `[REDACTED-SECRET]` — see [Secrets Scrubbing](#/data/secrets). Note in the example above that the phone pattern does not always consume the full number; verify masking against your own formats before relying on it.

## Conservative-by-design

The PII regexes are deliberately tuned for **low false-positive rate**. They prefer to miss a borderline match (false negative) than to redact a non-PII string in your prose (false positive). Reasons:

1. False positives silently corrupt your data — replacing legitimate words with `[REDACTED]` ruins examples.
2. Running `forgelm audit` over the masked output re-applies the same regexes, so it confirms those patterns no longer match; it cannot find identifiers the patterns do not describe (names, for example — `--pii-ml` counts those).
3. Aggressive regexes have caused real-world ML pipeline outages (the Phase 11.5 incident is documented in the contributor [regex standard on GitHub](https://github.com/HodeTech/ForgeLM/blob/main/docs/standards/regex.md)).

If you need stricter detection — for instance, a high-stakes legal corpus — pair the masker with a manual review step. Don't push the regexes harder.

## Audit-only mode

To detect without modifying:

```shell
$ forgelm audit data/policies.jsonl
Data audit summary
  Source        : data/policies.jsonl
  Total samples : 12240
  Splits        : train
  └─ train: n=12240
     length  min=24 max=1180 mean=212.4 p95=640
     languages (top-3): en=12240
     PII             : email=18, phone=4
  PII severity   : worst tier = medium
     by tier      : medium=18, low=4
```

The audit report carries counts per category and split, not row indices or offsets. To find the rows that hold a specific identifier, search for it with `forgelm reverse-pii`.

## Regional formats

| Format | What it matches | Checksum-validated |
|---|---|---|
| Phone (all countries) | One pattern: a `+` country prefix, or a parenthesised area code | No |
| TR national ID | 11 digits (TC kimlik) | Yes: two modulo-10 check digits (10th and 11th) |
| DE national ID | ID-card serial: a letter, 7-8 digits, an optional check character | No |
| FR national ID | NIR: 13 characters (digits, or `2A` / `2B` for Corsica) with an optional two-digit key | No |
| US SSN | `NNN-NN-NNNN`, reserved blocks excluded | No |

Only credit cards, IBANs and TC kimlik numbers are checksum-validated; the other families are matched by shape only, so treat a finding as a candidate to review rather than a confirmed identifier.

The regex-based PII layer triggered by `forgelm ingest --pii-mask`
(or the audit equivalent) detects all patterns in the table above
without a locale flag. For a Presidio ML-NER pass with an explicit
language hint, use the audit subcommand:

```shell
$ forgelm audit ./data/*.jsonl --output ./out/ --pii-ml --pii-ml-language de
```

For a Presidio ML-NER pass with a locale hint, pass `--pii-ml-language` to the CLI:

```shell
$ forgelm ingest ./corpus/ --pii-mask --output out.jsonl
```

> **Note:** There is no `ingestion:` top-level block in the YAML config (`ForgeConfig` rejects unknown keys), and there is no locale or category selection for the regex PII layer anywhere — not in YAML, not on the CLI, and not in the programmatic API. `_PII_PATTERNS` is a single flat dict with no locale dimension; every pattern is always active. The `--pii-ml-language` flag applies **only** to the optional Presidio ML-NER pass, not to the regex layer.

## Programmatic API

For pipelines that need PII detection outside ingest. Both functions take a single string:

```python
from forgelm.data_audit import detect_pii, mask_pii

text = "Email: ali@example.com, Phone: +90 532 123 45 67"
print(detect_pii(text))
# {'email': 1, 'phone': 1}

print(mask_pii(text))
# Email: [REDACTED], Phone: [REDACTED] 67
```

Signatures: `detect_pii(text) -> Dict[str, int]` and `mask_pii(text, replacement='[REDACTED]', *, return_counts=False)`.

:::warn
**There is no `locale=` keyword.** `detect_pii(text, locale="tr")` raises `TypeError: detect_pii() got an unexpected keyword argument 'locale'`, and so does `mask_pii`. Earlier versions of this page documented both, along with a list-of-spans return shape (`[{'category': ..., 'span': ..., 'value': ...}]`) and per-category placeholders (`[EMAIL_REDACTED]` / `[PHONE_REDACTED]`). The real return is a flat `{kind: count}` map, and masking uses **one uniform placeholder** — `[REDACTED]` by default, overridable via `replacement=`. Row-level span extraction is not available.
:::

The eight detected pattern kinds are `credit_card`, `de_id`, `email`, `fr_ssn`, `iban`, `phone`, `tr_id`, `us_ssn`.

## Common pitfalls

:::warn
**Relying on PII masking for compliance certification.** PII masking is a defence-in-depth measure, not a certification. For a high-stakes corpus (legal, medical), pair masking with a manual review step. ForgeLM ships an `audit` mode that flags PII without modifying so you can review.
:::

:::warn
**Custom PII categories without testing.** The repo's `regex.md` standard documents 8 hard rules for adding new patterns. Skipping the testing checklist is how false-positive bugs ship.
:::

## See also

- [Dataset Audit](#/data/audit) — runs PII detection without modifying data.
- [ML-NER PII (Presidio)](#/data/pii-ml) — optional opt-in layer for unstructured identifiers (person / organization / location) that the regex layer can't catch.
- [Combined Masking](#/data/all-mask) — `--all-mask` shorthand for running PII + secrets masking in the right order.
- [Secrets Scrubbing](#/data/secrets) — sister feature for credentials.
- [GDPR / KVKK](#/compliance/gdpr) — regulatory context.
