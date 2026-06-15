---
title: PII Masking
description: Detect and redact emails, phones, credit cards, IBAN, and national IDs at ingest time.
---

# PII Masking

Personal data in your training set is a regulatory hazard (GDPR Article 5(1)(c) — data minimisation) and an operational hazard (the model memorises and emits it). ForgeLM's PII masker detects nine categories of PII and redacts them at ingest time, before chunks land in JSONL.

## What gets detected

| Category | Examples | How |
|---|---|---|
| **Email** | `alice@example.com` | RFC 5321-compatible regex |
| **Phone** | `+90 532 123 45 67`, `(555) 123-4567` | E.164-compatible patterns + locale variants |
| **Credit card** | `4111-1111-1111-1111` | Visa/MC/Amex/Discover patterns + Luhn check (no false-positives on lookalikes) |
| **IBAN** | `TR12 0006 4000 0011 2345 6789 01` | Country-aware checksum |
| **National ID — Turkey** | 11-digit TC kimlik | Modulo-10 + modulo-11 checksums |
| **National ID — Germany** | Steuer-ID | Format + checksum |
| **National ID — France** | NIR (social security) | Format + key validation |
| **US SSN** | `123-45-6789` | Format + reserved-block exclusions |
| **IPv4 / IPv6** | `192.168.1.1`, `2001:db8::1` | Standard regex (off by default; opt in) |

## Quick example

At ingest time:

```shell
$ forgelm ingest ./policies/ \
    --recursive --strategy markdown \
    --pii-mask \
    --output data/policies.jsonl
✓ masked 18 PII matches across 12,240 chunks
```

After ingest, every match is replaced with a tagged placeholder:

```text
Before: "Send your CV to ali@example.com or call +90 532 555 7890."
After:  "Send your CV to [EMAIL_REDACTED] or call [PHONE_REDACTED]."
```

The placeholder is consistent across the dataset, so a model can still learn that *some* email goes in that slot — just not the specific one.

## Tags emitted

| Tag | Replaces |
|---|---|
| `[EMAIL_REDACTED]` | Email addresses |
| `[PHONE_REDACTED]` | Phone numbers |
| `[CREDITCARD_REDACTED]` | Credit card numbers (Luhn-validated) |
| `[IBAN_REDACTED]` | IBANs |
| `[ID_TR_REDACTED]` | TC kimlik numbers |
| `[ID_DE_REDACTED]` | Steuer-IDs |
| `[ID_FR_REDACTED]` | NIR numbers |
| `[SSN_REDACTED]` | US SSNs |
| `[IP_REDACTED]` | IP addresses |

## Conservative-by-design

The PII regexes are deliberately tuned for **low false-positive rate**. They prefer to miss a borderline match (false negative) than to redact a non-PII string in your prose (false positive). Reasons:

1. False positives silently corrupt your data — replacing legitimate words with `[EMAIL_REDACTED]` ruins examples.
2. The audit step catches what masking missed; you can decide per-row whether to fix or drop.
3. Aggressive regexes have caused real-world ML pipeline outages (the Phase 11.5 incident is documented in the contributor [regex standard on GitHub](https://github.com/HodeTech/ForgeLM/blob/main/docs/standards/regex.md)).

If you need stricter detection — for instance, a high-stakes legal corpus — pair the masker with a manual review step. Don't push the regexes harder.

## Audit-only mode

To detect without modifying:

```shell
$ forgelm audit data/policies.jsonl
⚠ PII: 18 emails, 4 phone, 2 IBAN (medium severity)
```

The audit report lists row indices and offsets, so you can inspect specific cases.

## Locales

| Locale | Phone | National ID | Notes |
|---|---|---|---|
| TR (default) | E.164 + Turkish formats | TC kimlik | Most heavily tuned. |
| DE | E.164 + German formats | Steuer-ID | |
| FR | E.164 + French formats | NIR | |
| US | E.164 + (xxx) xxx-xxxx | SSN with reserved-block exclusion | |
| Global | E.164 only | none | Fallback for unknown locales. |

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

> **Note:** There is no `ingestion:` top-level block in the YAML config (`ForgeConfig` rejects unknown keys). Locale and category selection for the regex PII layer are available only via the programmatic API — `mask_pii(text, locale="de")` — or via CLI flags such as `--pii-ml-language de` for the Presidio ML-NER pass.

## Programmatic API

For pipelines that need PII detection outside ingest:

```python
from forgelm.data_audit import detect_pii, mask_pii

text = "Email: ali@example.com, Phone: +90 532 555 7890"
hits = detect_pii(text, locale="tr")
print(hits)
# [{'category': 'email', 'span': (7, 22), 'value': 'ali@example.com'},
#  {'category': 'phone', 'span': (31, 47), 'value': '+90 532 555 7890'}]

masked = mask_pii(text, locale="tr")
print(masked)
# Email: [EMAIL_REDACTED], Phone: [PHONE_REDACTED]
```

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
