# `forgelm reverse-pii` — Subcommand Reference

> **Audience:** ForgeLM operators answering GDPR Article 15 right-of-access requests against training corpora, plus auditors verifying the resulting `data.access_request_query` audit row.
> **Mirror:** [reverse_pii_subcommand-tr.md](reverse_pii_subcommand-tr.md)

`forgelm reverse-pii` is the operator-facing implementation of the **GDPR Article 15 right of access** for ForgeLM training corpora (Phase 38). Where [`forgelm purge`](purge_subcommand.md) answers "delete my row", `reverse-pii` answers "find every line where my identifier appears".

## Synopsis

```text
forgelm reverse-pii --query VALUE
                    [--type {literal,email,phone,tr_id,us_ssn,iban,credit_card,custom}]
                    [--salt-source {per_dir,env_var}]
                    [--output-dir DIR] [--audit-dir DIR]
                    [--output-format {text,json}]
                    JSONL_GLOB [JSONL_GLOB ...]
```

| Argument / flag | Required | Description |
|---|---|---|
| `JSONL_GLOB` (positional, ≥1) | yes | One or more JSONL paths or glob patterns (`data/*.jsonl`, `corpora/**/train.jsonl`). Recursive `**` honoured. |
| `--query VALUE` | yes | Identifier to search for (e-mail, phone, ID, regex pattern, or pre-hashed digest). The value is hashed before audit emission, but is read in cleartext from the corpus while scanning. |
| `--type {...}` | no (default `literal`) | Identifier category. `literal` and the type-specific values (`email`, `phone`, `tr_id`, `us_ssn`, `iban`, `credit_card`) treat `--query` as a literal substring (`re.escape` applied) — the safe choice for Article 15 access requests where dots in e-mails must NOT match arbitrary characters. `custom` treats `--query` as an arbitrary Python regex (use with care; ReDoS-guarded by a per-file SIGALRM timeout on POSIX main-thread invocations). |
| `--salt-source {per_dir,env_var}` | no | Switch to **hash-mask scan**: `SHA256(salt + identifier)` is computed via the same per-output-dir salt `forgelm purge` uses, then searched in every JSONL line. Without this flag, the scan is a plaintext residual scan. |
| `--output-dir DIR` | no | Directory containing the per-output-dir salt file (`.forgelm_audit_salt`). The salt is read/created here for BOTH the audit-event hash (every invocation, plaintext or hash-mask mode) AND for hash-mask scanning when `--salt-source` is set. Defaults to the parent of the first resolved corpus file; an implicit fallback emits a WARNING naming the resolved dir so the operator can pin `--output-dir` for cross-tool correlation with `forgelm purge`. |
| `--audit-dir DIR` | no | Where to write the audit chain entries (default: same as `--output-dir`, matching `forgelm purge` so `forgelm verify-audit` correlates Article 17 + Article 15 events for the same subject in one chain). Explicit `--audit-dir` values fail loudly when unwritable rather than silently dropping the Article 15 forensic record. |

## Two scan modes

### Plaintext residual scan (default)

Detects mask leaks: the operator believed the corpus was masked at ingest, but a residual span slipped through.

```shell
$ forgelm reverse-pii --query "alice@example.com" --type email \
    --output-dir ./outputs data/*.jsonl
```

The scan reads each line as cleartext. The audit event still records the **hashed** `query_hash`; the cleartext is never persisted to the audit chain.

### Hash-mask scan (`--salt-source`)

For corpora masked through an **external** pipeline that embedded `SHA256(salt + identifier)` digests using the same per-output-dir salt that `forgelm purge` uses for its `target_id` field. ForgeLM does not ship a hash-replacement ingest strategy of its own; this mode is for operators who built one outside the toolkit using the purge salt as the shared secret.

```shell
$ forgelm reverse-pii --query "alice@example.com" --type email \
    --salt-source per_dir --output-dir ./outputs data/*.jsonl
```

`--salt-source env_var` requires `FORGELM_AUDIT_SECRET` to be set; `per_dir` reads the salt file at `<output_dir>/.forgelm_audit_salt`.

## Identifier types

| Type | Treatment |
|---|---|
| `literal` (default) | Literal substring (`re.escape` applied). Safe for e-mails where dots must NOT match arbitrary characters. |
| `email`, `phone`, `tr_id`, `us_ssn`, `iban`, `credit_card` | Same literal-substring treatment as `literal`. The type label is recorded in the audit row's `identifier_type` field for downstream filtering. |
| `custom` | Interprets `--query` as a Python regex. On POSIX main-thread invocations a 30s per-file SIGALRM budget guards against ReDoS hangs. **On Windows AND on POSIX worker threads the SIGALRM guard is a no-op** (signal handlers must be installed from the main thread); operators running `--type custom` from a worker thread or on Windows must vet their regex themselves. |

## Cross-tool digest correlation with `forgelm purge`

When `forgelm reverse-pii --query <value> --salt-source per_dir` and `forgelm purge --row-id <value>` run against the **same** `<output_dir>` (i.e. consume the same `.forgelm_audit_salt`), the `query_hash` field on `data.access_request_query` and the `target_id` field on `data.erasure_*` events are byte-identical. This lets a compliance reviewer correlate the Article 15 access request and the Article 17 erasure for the same data subject in a single audit chain, without the cleartext identifier ever leaving the operator's terminal. Pinned by `tests/test_reverse_pii.py::test_purge_target_id_matches_reverse_pii_query_hash_on_same_output_dir`.

The salt at `<output_dir>/.forgelm_audit_salt` is the **identifier-hash salt only** — it does NOT participate in the audit-chain HMAC key, which is derived independently as `SHA-256(FORGELM_AUDIT_SECRET ‖ run_id)` inside `forgelm.compliance.AuditLogger.__init__`.

## Audit events emitted

Every invocation emits exactly one event ([catalog row](audit_event_catalog.md#article-15--gdpr-right-of-access-phase-38--forgelm-reverse-pii)):

| Event | When emitted | Key payload |
|---|---|---|
| `data.access_request_query` | After the scan completes (or after a mid-scan I/O failure — with `error_class` / `error_message`). | `query_hash` (salted SHA-256 of raw identifier — never raw; reuses purge's per-output-dir salt), `identifier_type` ∈ `{literal, email, phone, tr_id, us_ssn, iban, credit_card, custom}`, `scan_mode` ∈ `{plaintext, hash}`, `salt_source` ∈ `{plaintext, per_dir, env_var}`, `files_scanned` (paths), `match_count`, optional `error_class` / `error_message` |

The chain row records the **hashed** identifier so the chain itself does not leak the subject's data.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | `EXIT_SUCCESS` — scan completed (the matches list may be empty — Article 15 explicitly accepts "no matches" as a valid answer). |
| 1 | `EXIT_CONFIG_ERROR` — empty `--query`, malformed `custom` regex, empty resolved glob, `--salt-source env_var` with `FORGELM_AUDIT_SECRET` unset. |
| 2 | `EXIT_TRAINING_ERROR` — mid-scan I/O failure, permission denied, ReDoS SIGALRM timeout, or **explicit `--audit-dir` unwritable** (the explicit form refuses loudly so the Article 15 forensic record is never silently dropped — `_maybe_audit_logger`). |

Codes 3 (`EXIT_EVAL_FAILURE`) and 4 (`EXIT_AWAITING_APPROVAL`) are not part of this subcommand's surface.

## JSON output envelope

With `--output-format json` the scan prints exactly one JSON object on stdout — emitted by `_run_reverse_pii_cmd`:

```json
{
  "success": true,
  "query_hash": "9f2c8b…",
  "identifier_type": "email",
  "scan_mode": "plaintext",
  "salt_source": "per_dir",
  "matches": [
    {
      "file": "data/train.jsonl",
      "line": 4119,
      "snippet": "…alice@example.com is the canonical contact…"
    }
  ],
  "files_scanned": [
    {"path": "data/train.jsonl", "match_count": 1},
    {"path": "data/validation.jsonl", "match_count": 0}
  ],
  "match_count": 1
}
```

Field notes:
- Each match is `{file, line, snippet}` (note: the keys are `file` not `path`, and `snippet` not `preview`).
- `files_scanned` is a **list of `{path, match_count}` objects** — not an integer count. Use `len(envelope["files_scanned"])` for the file count and the per-entry `match_count` for the per-file hit distribution.
- `query_hash` is the salted SHA-256 of the cleartext query. The cleartext is never echoed in the envelope nor written to the audit chain.
- `salt_source` ∈ `{plaintext, per_dir, env_var}` — `plaintext` here means "no hash-mask scan" (the default); `per_dir` / `env_var` reflect the `--salt-source` mode.

A failed scan emits the standard error envelope (`_output_error_and_exit`):

```json
{"success": false, "error": "Glob 'data/*.jsonl' resolved to zero files."}
```

## See also

- [`../guides/gdpr_erasure.md`](../guides/gdpr_erasure.md) §"Article 15 right-of-access" — deployer flow walkthrough.
- [`purge_subcommand.md`](purge_subcommand.md) — sibling Article 17 right-to-erasure tool; shares the per-output-dir salt for cross-tool digest correlation.
- [`audit_event_catalog.md`](audit_event_catalog.md) — full event vocabulary and envelope spec.
- [`../qms/access_control.md`](../qms/access_control.md) §3.4 — operator identity contract (the `FORGELM_OPERATOR` recorded on every access-request event).
