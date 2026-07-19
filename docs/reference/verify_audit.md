# `forgelm verify-audit` — Reference

> **Audience:** Operators and CI/CD pipelines wiring `forgelm verify-audit` into release gates.
> **Mirror:** [verify_audit-tr.md](verify_audit-tr.md)

The `verify-audit` subcommand validates the SHA-256 hash chain of a ForgeLM `audit_log.jsonl` produced under EU AI Act Article 12 record-keeping. When the operator's `FORGELM_AUDIT_SECRET` is set in the environment, per-line HMAC tags are also verified. The CLI is a thin dispatcher around the library entry point `forgelm.compliance.verify_audit_log` (returning `forgelm.compliance.VerifyResult`).

## Synopsis

```text
forgelm verify-audit [--hmac-secret-env VAR] [--require-hmac]
                     [-q] [--log-level {DEBUG,INFO,WARNING,ERROR}]
                     log_path
```

`log_path` (positional, required) — path to `audit_log.jsonl`. The genesis manifest sidecar (`<log_path>.manifest.json`) is auto-detected and cross-checked when present.

## Flags

| Flag | Default | Description |
|---|---|---|
| `--hmac-secret-env VAR` | `FORGELM_AUDIT_SECRET` | Name of the environment variable that holds the HMAC secret used at log-write time. When the variable is set, per-line `_hmac` tags are validated; when unset, only the SHA-256 chain is checked. |
| `--require-hmac` | `False` | Strict mode. Exit `1` if the configured env var is unset (a pre-flight option error — the verifier never ran). Exit `6` if any line lacks an `_hmac` field (the log was read and failed strict-mode verification — a chain-integrity failure). Use this in regulated CI pipelines where every entry must be HMAC-authenticated. |
| `-q`, `--quiet` | _off_ | Suppress INFO logs. |
| `--log-level {DEBUG,INFO,WARNING,ERROR}` | `INFO` | Set logging verbosity. |
| `-h`, `--help` | — | Show argparse help and exit. |

## Exit codes

| Code | Meaning |
|---|---|
| `0` | `EXIT_SUCCESS` — the SHA-256 chain (and HMAC tags, when verified) is intact end-to-end. |
| `1` | `EXIT_CONFIG_ERROR` — option/usage error: `--require-hmac` set with the configured env var unset, or the log path does not exist / is a directory / is unreadable for a caller-input reason. The verification never ran, so there is no integrity verdict. |
| `2` | `EXIT_TRAINING_ERROR` — the log exists but could not be read (permission denied, mid-read I/O failure). Retryable. |
| `6` | `EXIT_INTEGRITY_FAILURE` — the log was read and the chain does not verify: chain break, HMAC mismatch, genesis-manifest mismatch, an undecodable line, non-UTF-8 bytes inside the log, or (under `--require-hmac`) a line missing its `_hmac` field. This is the tampering signal, and it is the reason the code exists — previously a broken hash chain and a mistyped path both exited `1`, so a CI pipeline could not tell a security event from an operator typo. |

The codes are emitted by the dispatcher at `_run_verify_audit_cmd` (`forgelm/cli/subcommands/_verify_audit.py`), which probes the file first (`_probe_log_readable`) so a *read* failure routes to 1 or 2 before the verifier runs — every `valid=False` the library entry point returns after that probe succeeds is therefore a genuine integrity verdict (6). `forgelm.compliance.verify_audit_log` itself never raises for chain-level failures; it always returns a `VerifyResult(valid=False, reason=...)`, and only raises `OSError` for a file that becomes unreadable mid-verification.

## Audit events emitted

`forgelm verify-audit` is a **read-only verifier** and emits **no** entries to `audit_log.jsonl`. It only inspects the chain. The events that appear *inside* the log being verified are catalogued in [audit_event_catalog.md](audit_event_catalog.md) (see the Common envelope row for the `_hmac`, `prev_hash`, and `run_id` fields the verifier walks).

## Examples

### Chain-only validation (no secret in environment)

```shell
$ forgelm verify-audit checkpoints/run/compliance/audit_log.jsonl
OK: 87 entries verified
```

### HMAC-authenticated validation

```shell
$ export FORGELM_AUDIT_SECRET="$(cat /run/secrets/audit-secret)"
$ forgelm verify-audit checkpoints/run/compliance/audit_log.jsonl
OK: 87 entries verified (HMAC validated)
```

### Strict CI gate (enterprise audit profile)

```shell
$ FORGELM_AUDIT_SECRET="$(cat /run/secrets/audit-secret)" \
    forgelm verify-audit --require-hmac \
        checkpoints/run/compliance/audit_log.jsonl
OK: 87 entries verified (HMAC validated)
$ echo $?
0
```

If the secret env var is unset under `--require-hmac`, the command exits `1`:

```shell
$ forgelm verify-audit --require-hmac checkpoints/run/compliance/audit_log.jsonl
ERROR: --require-hmac specified but $FORGELM_AUDIT_SECRET is unset.
$ echo $?
1
```

### Custom secret-env name

For multi-tenant environments where each tenant carries its own secret variable:

```shell
$ TENANT_ACME_AUDIT_KEY="$(cat /run/secrets/acme-audit)" \
    forgelm verify-audit --hmac-secret-env TENANT_ACME_AUDIT_KEY \
        artifacts/acme/audit_log.jsonl
OK: 412 entries verified (HMAC validated)
```

### Tamper-detection failure

```shell
$ forgelm verify-audit checkpoints/run/compliance/audit_log.jsonl
FAIL at line 53: prev_hash mismatch — chain break suggests entry was inserted, removed, or reordered
$ echo $?
6
```

## See also

- [`audit_event_catalog.md`](audit_event_catalog.md) — events that appear *inside* the log this command verifies.
- [`verify_annex_iv_subcommand.md`](verify_annex_iv_subcommand.md) — companion verifier for the Annex IV technical-documentation artifact.
- [`verify_gguf_subcommand.md`](verify_gguf_subcommand.md) — companion verifier for exported GGUF model files.
- [Audit Log usermanual page](../usermanuals/en/compliance/audit-log.md) — operator-facing primer on the log itself.
- `forgelm.compliance.verify_audit_log` — the library entry point integrators call directly without going through the CLI.
