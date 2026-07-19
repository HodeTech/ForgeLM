# `forgelm verify-annex-iv` — Reference

> **Audience:** Compliance operators and CI gates verifying Annex IV technical-documentation artifacts before submission.
> **Mirror:** [verify_annex_iv_subcommand-tr.md](verify_annex_iv_subcommand-tr.md)

The `verify-annex-iv` subcommand reads an Annex IV technical-documentation JSON file, validates the nine required field categories per EU AI Act Annex IV §1-9, and recomputes the manifest hash to detect post-generation tampering. The CLI delegates to the library entry point `forgelm.verify.verify_annex_iv_artifact` (also exposed at the package root as `forgelm.verify_annex_iv_artifact`) and shares the canonicalisation routine `forgelm.compliance.compute_annex_iv_manifest_hash` with the writer in `forgelm.compliance.build_annex_iv_artifact` — so a legitimate artefact can never fail its own verifier on a writer/verifier byte drift.

## Synopsis

```text
forgelm verify-annex-iv [--output-format {text,json}]
                        [-q] [--log-level {DEBUG,INFO,WARNING,ERROR}]
                        path
```

`path` (positional, required) — path to the Annex IV JSON artifact (typically `compliance/annex_iv_<run>.json` under the training output directory).

## Flags

| Flag | Default | Description |
|---|---|---|
| `--output-format {text,json}` | `text` | `text` (default) prints `OK:` / `FAIL:` plus the per-section reason and any missing-field bullets; `json` prints the full `VerifyAnnexIVResult` envelope (`{"success", "valid", "reason", "missing_fields", "manifest_hash_actual", "manifest_hash_expected", "path"}`). |
| `-q`, `--quiet` | _off_ | Suppress INFO logs. |
| `--log-level {DEBUG,INFO,WARNING,ERROR}` | `INFO` | Set logging verbosity. |
| `-h`, `--help` | — | Show argparse help and exit. |

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Every required Annex IV §1-9 field is populated AND (when present) the `metadata.manifest_hash` matches the recomputed hash. |
| `1` | Caller / input error: file not found / not a regular file; malformed JSON; not valid UTF-8; root is not a JSON object; OR a validation failure — a required field is missing / empty (the artifact was never fully populated). Operator-actionable: the artifact is not Annex IV compliant as-is, and no manifest-hash comparison was ever performed. |
| `2` | Genuine runtime I/O failure on an existing file — read errors, permission denied mid-read, etc. The path was accessible to `os.path.isfile` but became unreadable during verification. |
| `6` | Integrity failure: every required §1-9 field is populated, the artifact carries a `metadata.manifest_hash`, and the recomputed hash disagrees with it. The document was edited after generation. |

The codes are emitted by `forgelm/cli/subcommands/_verify_annex_iv.py::_run_verify_annex_iv_cmd`, which routes on the structural (never string-matched) predicate `forgelm.verify.is_annex_iv_integrity_failure` — required-field gaps are always `1`, a manifest-hash disagreement on an otherwise-complete artefact is always `6`, keyed off the result's typed fields so a reworded `reason` string can never flip the exit code. Public-contract semantics are pinned in `docs/standards/error-handling.md`.

## Required Annex IV fields

The verifier walks a static catalog (`_ANNEX_IV_REQUIRED_FIELDS`) so a future schema addition is one row in the tuple, not a code edit at every call site. A field counts as "missing" when the key is absent OR the value is `None`, an empty string, an empty list, or an empty dict (operator likely forgot to populate it from the auto-generation template).

| Top-level key | Annex IV section |
|---|---|
| `system_identification` | §1 — system identification (name, version, provider, intended_purpose). |
| `intended_purpose` | §1 — intended purpose statement. |
| `system_components` | §2 — software / hardware components + supplier list. |
| `computational_resources` | §2(g) — compute resources used during training. |
| `data_governance` | §2(d) — data sources, governance, validation methodology. |
| `technical_documentation` | §3-5 — design + development methodology. |
| `monitoring_and_logging` | §6 — post-market monitoring + audit-log presence. |
| `performance_metrics` | §7 — accuracy / robustness / cybersecurity metrics. |
| `risk_management` | §9 — risk management system reference (Article 9 alignment). |

## Audit events emitted

`forgelm verify-annex-iv` is a **read-only verifier** and emits **no** entries to `audit_log.jsonl`. The events that signal Annex IV *production* (not verification) — `compliance.artifacts_exported` — are catalogued in [audit_event_catalog.md](audit_event_catalog.md) under the Article 11 + Annex IV section. Operators who want a verify-time record can call this subcommand from CI and persist the JSON output alongside the artifact bundle.

## Examples

### Text output (default)

```shell
$ forgelm verify-annex-iv checkpoints/run/compliance/annex_iv.json
OK: checkpoints/run/compliance/annex_iv.json
  All Annex IV §1-9 fields populated; manifest hash matches.
```

### JSON output (CI consumers)

```shell
$ forgelm verify-annex-iv --output-format json \
    checkpoints/run/compliance/annex_iv.json
{
  "success": true,
  "valid": true,
  "reason": "All Annex IV §1-9 fields populated; manifest hash matches.",
  "missing_fields": [],
  "manifest_hash_actual": "sha256:abcdef…",
  "manifest_hash_expected": "sha256:abcdef…",
  "path": "/abs/path/checkpoints/run/compliance/annex_iv.json"
}
```

### Failure: missing required fields

```shell
$ forgelm verify-annex-iv checkpoints/run/compliance/annex_iv.json
FAIL: checkpoints/run/compliance/annex_iv.json
  Missing or empty required Annex IV field(s): risk_management, performance_metrics.
    - missing: risk_management
    - missing: performance_metrics
$ echo $?
1
```

### Failure: tamper detection

```shell
$ forgelm verify-annex-iv checkpoints/run/compliance/annex_iv.json
FAIL: checkpoints/run/compliance/annex_iv.json
  Manifest hash mismatch — artifact may have been modified after generation.
$ echo $?
6
```

### Failure: malformed JSON

```shell
$ forgelm verify-annex-iv compliance/annex_iv.json
ERROR: Annex IV artifact at 'compliance/annex_iv.json' is not valid JSON: Expecting value (line 1).
$ echo $?
1
```

## See also

- [`audit_event_catalog.md`](audit_event_catalog.md) — `compliance.artifacts_exported` (Article 11 + Annex IV) and the rest of the canonical event vocabulary.
- [`verify_audit.md`](verify_audit.md) — companion verifier for `audit_log.jsonl`.
- [`verify_gguf_subcommand.md`](verify_gguf_subcommand.md) — companion verifier for exported GGUF artefacts.
- [Annex IV usermanual page](../usermanuals/en/compliance/annex-iv.md) — operator-facing primer that includes a full quick-start example.
- `forgelm.compliance.build_annex_iv_artifact` and `forgelm.compliance.compute_annex_iv_manifest_hash` — the writer-side counterparts to this verifier.
