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
| `--require-hmac` | `False` | Strict mode. Exit `1` if the configured env var is unset, and exit `1` if any line lacks an `_hmac` field. Use this in regulated CI pipelines where every entry must be HMAC-authenticated. (See "Exit codes" below — `EXIT_CONFIG_ERROR=1` currently covers both option errors and integrity failures; a dedicated `EXIT_INTEGRITY_FAILURE` constant is deferred to the post-v0.7.0 backlog.) |
| `-q`, `--quiet` | _off_ | Suppress INFO logs. |
| `--log-level {DEBUG,INFO,WARNING,ERROR}` | `INFO` | Set logging verbosity. |
| `-h`, `--help` | — | Show argparse help and exit. |

## Exit codes

| Code | Meaning |
|---|---|
| `0` | `EXIT_SUCCESS` — the SHA-256 chain (and HMAC tags, when verified) is intact end-to-end. |
| `1` | `EXIT_CONFIG_ERROR` — covers **both** option/usage errors (missing log file, `--require-hmac` set with the env var unset, JSON decode error on a malformed line) **and** integrity failures (chain break, HMAC mismatch, manifest mismatch, missing `_hmac` line under `--require-hmac`). |

> **Future deprecation note.** A dedicated `EXIT_VALIDATION_ERROR` / `EXIT_INTEGRITY_FAILURE` constant is on the post-v0.7.0 backlog. Until then, treat any non-zero exit from `verify-audit` as "do not promote this build" in CI gates — the dispatcher does not currently distinguish option errors from integrity failures by code. The `_verify_audit.py` docstring (`_run_verify_audit_cmd` docstring) holds the authoritative contract.

The codes are emitted by the dispatcher at `_run_verify_audit_cmd` (`forgelm/cli/subcommands/_verify_audit.py`).

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
1
```

## See also

- [`audit_event_catalog.md`](audit_event_catalog.md) — events that appear *inside* the log this command verifies.
- [`verify_annex_iv_subcommand.md`](verify_annex_iv_subcommand.md) — companion verifier for the Annex IV technical-documentation artifact.
- [`verify_gguf_subcommand.md`](verify_gguf_subcommand.md) — companion verifier for exported GGUF model files.
- [Audit Log usermanual page](../usermanuals/en/compliance/audit-log.md) — operator-facing primer on the log itself.
- `forgelm.compliance.verify_audit_log` — the library entry point integrators call directly without going through the CLI.
