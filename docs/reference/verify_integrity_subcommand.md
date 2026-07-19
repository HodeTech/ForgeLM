# `forgelm verify-integrity` — Reference

> **Audience:** Compliance operators and CI gates verifying that a trained model directory still matches the SHA-256 manifest recorded at training time (EU AI Act Article 15).
> **Mirror:** [verify_integrity_subcommand-tr.md](verify_integrity_subcommand-tr.md)

The `verify-integrity` subcommand is the consuming counterpart to the Article 15 `model_integrity.json` manifest. The compliance export writes a SHA-256 hash of every file in the model directory; `verify-integrity` reads that manifest back, recomputes each file's SHA-256, and reports any artifact that was **changed**, **removed**, or **added** since the manifest was generated. The CLI delegates to the library entry point `forgelm.verify.verify_integrity` (also exposed at the package root as `forgelm.verify_integrity`) and returns a structured `VerifyIntegrityResult`.

## Synopsis

```text
forgelm verify-integrity [--output-format {text,json}]
                         [-q] [--log-level {DEBUG,INFO,WARNING,ERROR}]
                         path
```

`path` (positional, required) — path to the model directory containing `model_integrity.json`.

## Flags

| Flag | Default | Description |
|---|---|---|
| `--output-format {text,json}` | `text` | `text` (default) prints `OK:` / `FAIL:` plus the per-file breakdown; `json` prints the full `VerifyIntegrityResult` envelope (`{"success", "valid", "reason", "changed", "removed", "added", "verified_count", "path"}`). |
| `-q`, `--quiet` | _off_ | Suppress INFO logs. |
| `--log-level {DEBUG,INFO,WARNING,ERROR}` | `INFO` | Set logging verbosity. |
| `-h`, `--help` | — | Show argparse help and exit. |

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Every recorded artifact is present and its SHA-256 is unchanged, and no unexpected extra files exist in the directory. |
| `1` | Caller / input error: path missing, `model_integrity.json` not found or not a regular file, malformed JSON, not valid UTF-8, a non-list `artifacts` container, OR a manifest entry whose `file` value is non-string or whose path escapes the model directory. Each of these returns before any artifact is hashed — the manifest could not be used, so nothing was ever compared. |
| `2` | Genuine runtime I/O failure on a reachable path — read errors, permission denied mid-walk, etc. The path was accessible but became unreadable during verification. |
| `6` | Integrity failure: the manifest parsed and the walk ran, but at least one artifact came back changed, removed, or added. The deployed weights are not the weights that were signed off. |

The codes are emitted by `forgelm/cli/subcommands/_verify_integrity.py::_run_verify_integrity_cmd`, which routes on the structural (never string-matched) predicate `forgelm.verify.is_model_integrity_failure`. **Judgement call:** a manifest entry whose path escapes the model directory stays on `1`, not `6`, even though an escaping entry is the shape of an attack — the verifier refuses to hash an out-of-tree path *before* reading anything, so nothing was compared; the report is "I refused to hash this", not "your weights changed". Public-contract semantics are pinned in `docs/standards/error-handling.md`.

## What is checked

| Check | Failure mode |
|---|---|
| **Manifest is usable** | `artifacts` is not a list, an entry's `file` is not a string, or an entry's path escapes the model directory → exit `1`. Nothing below this row runs when this fails. |
| **Recorded artifact present** | A file listed in `model_integrity.json` that no longer exists on disk → `removed`, exit `6`. |
| **Recorded artifact unchanged** | A file whose recomputed SHA-256 differs from the manifest → `changed`, exit `6`. |
| **No extra files** | A file on disk that is not in the manifest → `added`, exit `6`. The manifest itself (`model_integrity.json`) is excluded from this walk because it is written after the model artifacts. |

## Audit events emitted

`forgelm verify-integrity` is a **read-only verifier** and emits **no** entries to `audit_log.jsonl`. The events that signal integrity-manifest *production* (not verification) ride the run-level training events; see [audit_event_catalog.md](audit_event_catalog.md).

## Examples

### Text output (default)

```shell
$ forgelm verify-integrity checkpoints/run/final_model
OK: checkpoints/run/final_model
  All 7 recorded artifact(s) present and unchanged.
```

### JSON output (CI consumers)

```shell
$ forgelm verify-integrity --output-format json \
    checkpoints/run/final_model
{
  "success": true,
  "valid": true,
  "reason": "All 7 recorded artifact(s) present and unchanged.",
  "changed": [],
  "removed": [],
  "added": [],
  "verified_count": 7,
  "path": "/abs/path/checkpoints/run/final_model"
}
```

### Failure: a weights file was modified after training

```shell
$ forgelm verify-integrity checkpoints/run/final_model
FAIL: checkpoints/run/final_model
  Model artifacts do not match model_integrity.json: 1 changed.
    changed: model.safetensors
$ echo $?
6
```

### Failure: missing manifest

```shell
$ forgelm verify-integrity checkpoints/run/final_model
Integrity manifest not found: expected 'checkpoints/run/final_model/model_integrity.json' (FileNotFoundError).
$ echo $?
1
```

## See also

- [`audit_event_catalog.md`](audit_event_catalog.md) — canonical event vocabulary.
- [`verify_gguf_subcommand.md`](verify_gguf_subcommand.md) — companion verifier for exported GGUF files.
- [`verify_annex_iv_subcommand.md`](verify_annex_iv_subcommand.md) — companion verifier for the Annex IV technical-documentation artifact.
- `forgelm.verify.verify_integrity` (also `forgelm.verify_integrity`) — the library entry point integrators call directly without going through the CLI.
