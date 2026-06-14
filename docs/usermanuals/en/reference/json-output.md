---
title: JSON output schemas
description: The locked --output-format json envelope shape for every forgelm subcommand. CI consumers depend on these field names.
---

# JSON output schemas

Every `forgelm` subcommand that supports `--output-format json` produces a stable JSON envelope on stdout. Field names + nesting are part of the public CLI contract per the [release standard on GitHub](https://github.com/HodeTech/ForgeLM/blob/main/docs/standards/release.md): renaming a key is a MAJOR-version break.

This page is the canonical reference. CI/CD pipelines that parse `forgelm` output should pin against the shapes documented here.

## Common conventions

- **stdout vs stderr.** The JSON envelope goes to **stdout**. Human-friendly logs (info / warning / error) go to **stderr**. Pipe `forgelm ... --output-format json | jq .` and read your operator-facing messages from `2>` separately.
- **Top-level wrapper.** Every envelope starts with `"success": true | false`. Consumers can branch on this single key before parsing the rest.
- **Error envelope.** When `success: false`, the envelope carries `"error": "<message>"` (string). Optional richer fields (`exit_code`, `error_type`, `details`) MAY be present per the [error-handling standard on GitHub](https://github.com/HodeTech/ForgeLM/blob/main/docs/standards/error-handling.md). Consumers that need certainty on these fields should also check the process exit code via `$?`.
- **Exit codes.** See [Exit Codes](#/reference/exit-codes). `success: false` always pairs with a non-zero exit. `success: true` pairs with exit `0` in the normal case, with **one exception**: a training run halted at the Article 14 human-approval gate emits `success: true` **and** `awaiting_approval: true` while exiting `4` (the model is staged, not yet promoted). Consumers that must distinguish "done" from "awaiting human approval" should read the `awaiting_approval` discriminator (or check `$?`), not `success` alone.

## `forgelm doctor`

Environment check. See [Doctor command](#/getting-started/first-run).

**Success envelope** (`forgelm doctor [--offline] --output-format json`):

```json
{
  "success": true,
  "checks": [
    {
      "name": "python.version",
      "status": "pass",
      "detail": "Python 3.11.7 (CPython).",
      "extras": {"version": "3.11.7", "implementation": "CPython"}
    }
  ],
  "summary": {"pass": 8, "warn": 1, "fail": 0, "crashed": 0}
}
```

| Key | Type | Notes |
|---|---|---|
| `success` | bool | `true` when no probe `fail` AND no probe crash; `false` otherwise. |
| `checks` | list[object] | One entry per probe in execution order. Probe names are stable (e.g. `python.version`, `torch.cuda`, `numpy.torch_abi`, `gpu.inventory`, `extras.qlora`, `hf_hub.reachable`, `hf_hub.offline_cache`, `disk.workspace`, `operator.identity`). |
| `checks[].name` | str | Probe name. Stable across versions; new probes append rather than rename. |
| `checks[].status` | str | One of `pass`, `warn`, `fail`. A probe that raised surfaces as `status: "fail"` with `extras.crashed: true`; the crash is also counted in `summary.crashed`. |
| `checks[].detail` | str | Operator-facing one-line description of the result. |
| `checks[].extras` | object | Probe-specific structured data. Per-probe keys are documented in `_doctor.py` docstrings; consumers should treat unknown keys as forward-compatible. |
| `summary` | object | Counts of each status across `checks`. Sum equals `len(checks)`. |

**Exit code mapping:** `0` = all probes `pass` or `warn`; `1` = at least one `fail`; `2` = at least one `crashed` (probe raised; subsequent probes still ran).

## `forgelm --dry-run` (config validation)

`forgelm --config <yaml> --dry-run --output-format json` validates the config and summarizes what training *would* do **without** loading the heavy stack or touching the GPU. It is the most common CI pre-flight invocation. On a valid config it emits the following envelope on **stdout** and exit code `0`:

```json
{
  "success": true,
  "status": "valid",
  "model": "org/model",
  "backend": "auto",
  "load_in_4bit": false,
  "trust_remote_code": false,
  "dora": false,
  "lora_rank": 16,
  "lora_alpha": 32,
  "dataset": "org/dataset",
  "epochs": 3,
  "batch_size": 1,
  "output_dir": "/work/output/final_model",
  "offline": false,
  "distributed": null,
  "rope_scaling": null,
  "neftune_noise_alpha": null,
  "webhook_configured": false,
  "galore_enabled": false,
  "galore_optim": null,
  "galore_rank": null,
  "auto_revert": false,
  "safety_enabled": false,
  "safety_scoring": null,
  "compliance_configured": false,
  "risk_classification": null
}
```

| Key | Type | Notes |
|---|---|---|
| `success` | bool | Always `true` on this path — a dry run only reaches stdout once the config validated. An invalid config exits `1` with the 2-key error envelope (`success: false`) instead. |
| `status` | str | Stable token `"valid"`, retained for backward compatibility with pre-0.7.1 consumers. New consumers should branch on `success`. |
| `model` / `backend` / `dataset` | str | Resolved model id, model backend, and dataset id from the config. |
| `output_dir` | str | The effective final-model path (`training.output_dir` joined with `training.final_model_dir`). |
| `load_in_4bit`, `trust_remote_code`, `dora`, `offline` | bool | Effective values of the corresponding config flags. |
| `lora_rank`, `lora_alpha`, `epochs`, `batch_size` | int | Effective training/LoRA hyper-parameters. |
| `distributed` | str \| null | `distributed.strategy`, or `null` when distributed training is not configured. |
| `rope_scaling`, `neftune_noise_alpha` | object \| float \| null | Effective long-context / NEFTune settings, `null` when unset. |
| `webhook_configured` | bool | `true` when a webhook URL (literal or `url_env`) is configured. |
| `galore_enabled`, `galore_optim`, `galore_rank` | bool / str / int | GaLore optimizer summary; the latter two are `null` when GaLore is off. |
| `auto_revert`, `safety_enabled`, `safety_scoring` | bool / bool / str | Evaluation-gate summary (`safety_scoring` is `null` when safety eval is off). |
| `compliance_configured`, `risk_classification` | bool / str | EU AI Act compliance summary; `risk_classification` is `null` when compliance is not configured. |

**Exit code mapping:** a valid config exits `0` (`EXIT_SUCCESS`); an invalid config exits `1` (`EXIT_CONFIG_ERROR`) with the standard error envelope.

## `forgelm` (training) — preflight abort envelope

The training pipeline (`forgelm --config <yaml> --output-format json`) runs a torch/NumPy ABI sanity check before importing the heavy stack. On a healthy environment the preflight is silent and training proceeds normally; on the known Intel Mac NumPy 2 / torch 2.2 mismatch the preflight aborts with the following envelope on **stdout** and exit code `2`:

```json
{
  "success": false,
  "error": "numpy_torch_abi_mismatch",
  "torch_version": "2.2.2",
  "numpy_version": "2.4.4",
  "remediation": "torch 2.2.2 (compiled against NumPy 1.x) is paired with numpy 2.4.4. ... Fix with: pip install 'numpy<2' ..."
}
```

| Key | Type | Notes |
|---|---|---|
| `success` | bool | Always `false` on this path; healthy preflight emits nothing and the pipeline continues. |
| `error` | str | Stable token `"numpy_torch_abi_mismatch"`. CI consumers can branch on this exact value. |
| `torch_version` | str | The version string `torch.__version__` reported at preflight time. |
| `numpy_version` | str | The version string `numpy.__version__` reported at preflight time. |
| `remediation` | str | Human-readable fix instructions ending with the exact `pip install 'numpy<2'` command. Identical text to the `forgelm doctor` `numpy.torch_abi` probe's `detail` field — same single source of truth. |

**Exit code mapping:** preflight abort exits `2` (`EXIT_TRAINING_ERROR`, the runtime-error class). The same `forgelm doctor` `numpy.torch_abi` probe would have surfaced this as `status: "fail"` ahead of time; the preflight is the second line of defense for operators who skipped `doctor` and ran training directly.

## `forgelm` (training) — result envelope

When training runs to completion the pipeline emits a result envelope on **stdout**. The same envelope shape covers all three outcomes; the `success` + `awaiting_approval` + `reverted` keys discriminate between them.

**Success envelope** (model promoted; exit `0`):

```json
{
  "success": true,
  "metrics": {"eval_loss": 0.42, "benchmark/average": 0.78},
  "final_model_path": "/work/output/final_model",
  "reverted": false,
  "awaiting_approval": false,
  "run_id": "fg-abc123def456",
  "config_hash": "sha256:..."
}
```

**Awaiting-approval envelope** (`evaluation.require_human_approval: true`; model **staged**, not yet promoted; exit `4`):

```json
{
  "success": true,
  "metrics": {"eval_loss": 0.42},
  "final_model_path": "/work/output/final_model.staging.fg-abc123def456",
  "reverted": false,
  "awaiting_approval": true,
  "staging_path": "/work/output/final_model.staging.fg-abc123def456"
}
```

**Reverted envelope** (a post-train gate failed under `auto_revert: true`; artefacts deleted; exit `3`):

```json
{
  "success": false,
  "metrics": {"eval_loss": 0.91},
  "final_model_path": "/work/output/final_model",
  "reverted": true,
  "awaiting_approval": false
}
```

| Key | Type | Notes |
|---|---|---|
| `success` | bool | `true` when the run completed (including a staged, awaiting-approval run). `false` when a gate auto-reverted the model. |
| `metrics` | object | Numeric training/eval/gate metrics (e.g. `eval_loss`, `benchmark/average`, `safety/safe_ratio`). |
| `final_model_path` | str | Where the model artefacts live. For an awaiting-approval run this is the **staging** directory until `forgelm approve` promotes it. |
| `reverted` | bool | `true` iff a gate (eval-loss / benchmark / safety / judge) auto-reverted the model. Mutually exclusive with `awaiting_approval`. |
| `awaiting_approval` | bool | **Discriminator.** `true` iff the run halted at the Article 14 human-approval gate (exit `4`). A reverted run is always `false` here. |
| `staging_path` | str | Present only when `awaiting_approval` is `true`; the on-disk staging dir to pass to `forgelm approve <run_id>` / `forgelm reject <run_id>`. |
| `run_id` | str | Run identifier — correlates the run with its `audit_log.jsonl` and any approval gate. Present whenever the trainer produced the result. |
| `config_hash` | str | `sha256:` digest of the validated config that produced the run (reproducibility anchor). Present whenever the trainer produced the result. |

Optional sub-blocks (`benchmark`, `resource_usage`, `estimated_cost_usd`, `safety`, `judge`) are added only when those evaluations ran.

**Exit code mapping:** `0` = success (promoted); `3` = `EXIT_EVAL_FAILURE` (a gate reverted the model — `reverted: true`); `4` = `EXIT_AWAITING_APPROVAL` (`awaiting_approval: true`, staged pending human sign-off). A reverted run is **never** reported as awaiting approval.

## `forgelm approvals --pending`

Lists pending Article 14 approval requests, newest-first.

```json
{
  "success": true,
  "pending": [
    {
      "run_id": "fg-abc123def456",
      "staging_path": "/work/output/final_model.staging.fg-abc123def456",
      "staging_exists": true,
      "requested_at": "2026-05-04T12:34:56+00:00",
      "age_seconds": 3600.5,
      "metrics": {"safety_score": 0.91, "benchmark.hellaswag": 0.78},
      "config_hash": "sha256:...",
      "reason": "post-train safety eval below threshold"
    }
  ],
  "count": 1
}
```

| Key | Type | Notes |
|---|---|---|
| `success` | bool | `true` (always — empty list is success). |
| `pending` | list[object] | Newest-first; `count == len(pending)`. Empty list = no pending approvals. |
| `pending[].run_id` | str \| null | Run identifier from the audit event. |
| `pending[].staging_path` | str \| null | Resolved staging directory (`null` when audit event lacks `staging_path` and no canonical fallback exists). Path-traversal guarded — paths outside `--output-dir` are rejected at resolution time. |
| `pending[].staging_exists` | bool | `True` iff `staging_path` resolves to an existing directory. |
| `pending[].requested_at` | str \| null | ISO-8601 timestamp from the audit event. |
| `pending[].age_seconds` | number \| null | Seconds since `requested_at`; `null` when timestamp is unparseable. |
| `pending[].metrics` | object | Free-form per-run metrics from the audit event. |
| `pending[].config_hash` | str \| null | Config fingerprint, when known. |
| `pending[].reason` | str \| null | Operator-supplied reason from the audit event. |
| `count` | int | Pending count; equal to `len(pending)`. |

## `forgelm approvals --show RUN_ID`

Inspect the full approval-gate audit chain + staging contents for one run.

```json
{
  "success": true,
  "run_id": "fg-abc123def456",
  "status": "pending",
  "chain": [
    {"event": "human_approval.required", "run_id": "fg-abc123def456", "timestamp": "2026-05-04T12:34:56+00:00", "...": "..."}
  ],
  "staging_contents": ["adapter_config.json", "adapter_model.safetensors", "tokenizer.json"]
}
```

| Key | Type | Notes |
|---|---|---|
| `success` | bool | `true` when the run was found; `false` (with `error`) when `RUN_ID` has no audit events. |
| `run_id` | str | Echoed input `RUN_ID`. |
| `status` | str | One of `pending`, `granted`, `rejected`, `unknown`. Latest-wins semantics: a re-staged run after a prior decision shows `pending`. |
| `chain` | list[object] | Every approval-gate audit event for `run_id`, in append order. |
| `staging_contents` | list[str] | Sorted file/directory names at `<output_dir>/final_model.staging.<run_id>` (or canonical fallback). Empty when staging missing or unreadable. |

## `forgelm audit`

Pre-train data audit. Full report; key fields shown.

```json
{
  "success": true,
  "report_path": "audit/data_audit_report.json",
  "splits": {"train": {"sample_count": 100, "...": "..."}, "...": {}},
  "pii_summary": {"total_findings": 0, "by_kind": {}},
  "secrets_summary": {"total_findings": 0, "by_kind": {}},
  "cross_split_overlap": {"pairs": {}},
  "leakage": {"...": "..."},
  "quality_summary": {"samples_evaluated": 100, "samples_flagged": 3, "by_check": {"...": "..."}},
  "near_duplicates": {"...": "..."},
  "languages_top3": [{"code": "en", "count": 87}],
  "generated_at": "2026-05-04T12:34:56+00:00",
  "warnings": []
}
```

> **Phase 15 (v0.6.0) note:** `quality_summary` is populated by default from v0.6.0+ because the audit's `--quality-filter` flag was flipped to default-on. Pass `--no-quality-filter` to skip; the field is `{}` in that case.

The full schema is in the in-manual [Data Audit](#/data/audit) page. Pin against `report_path` (where the on-disk JSON lives) + `success` for CI gates.

## `forgelm ingest`

Document ingestion stdout envelope. Phase 15 added additive fields under
`notes_structured`; the pre-Phase-15 keys are unchanged.

```json
{
  "success": true,
  "output_path": "data/curated.jsonl",
  "chunk_count": 245,
  "files_processed": 12,
  "files_skipped": 0,
  "total_chars": 824513,
  "format_counts": {".pdf": 4, ".docx": 6, ".md": 2},
  "pii_redaction_counts": {"email": 3, "phone": 1},
  "secrets_redaction_counts": {},
  "pdf_header_footer_lines_stripped": 18,
  "pdf_paragraph_packed_lines_stripped": 4,
  "script_sanity_triggered": 1,
  "strip_pattern_substitutions": 12,
  "urls_handled": 7,
  "frontmatter_pages_dropped": 3,
  "notes": ["..."],
  "notes_structured": {
    "files_processed": 12,
    "files_skipped": 0,
    "chunk_count": 245,
    "total_chars": 824513,
    "strategy": "paragraph",
    "format_counts": {".pdf": 4, ".docx": 6, ".md": 2},
    "pdf_header_footer_lines_stripped": 18,
    "pdf_paragraph_packed_lines_stripped": 4,
    "script_sanity_summary": {"files_checked": 12, "files_triggered": 1, "...": "..."},
    "frontmatter_pages_dropped": [0, 1, 11],
    "strip_pattern_substitutions": 12,
    "urls_handled": 7,
    "quality_presignal": {"samples_evaluated": 245, "samples_flagged": 17, "by_check": {"alpha_ratio": 5, "weird_chars": 1, "repeated_lines": 11}}
  }
}
```

The Phase 15 additive fields are documented at
[Document Ingestion](#/data/ingestion). Pre-Phase-15 consumers reading
`output_path` / `chunk_count` / `notes` keep working unchanged.

## `forgelm verify-audit`

Audit-log chain integrity check.

```json
{
  "success": true,
  "valid": true,
  "entries_count": 87,
  "hmac_verified": true,
  "errors": []
}
```

| Key | Type | Notes |
|---|---|---|
| `success` | bool | `true` ⟺ `valid: true`. |
| `valid` | bool | `false` if any prev_hash mismatch / monotonicity break / seq gap. |
| `entries_count` | int | Number of well-formed audit lines. |
| `hmac_verified` | bool \| null | `true` when `--hmac-secret` matches every `hmac` field; `false` on mismatch; `null` when chain has no HMAC fields. |
| `errors` | list[str] | One human-readable line per detected problem. |

## `forgelm approve` / `forgelm reject`

```json
{
  "success": true,
  "run_id": "fg-abc123def456",
  "approver": "alice@example.com@workstation-7",
  "final_model_path": "/work/output/final_model",
  "promote_strategy": "rename"
}
```

`promote_strategy` is `"rename"` (same-device atomic `os.rename`) or `"move"` (cross-device `shutil.move` fallback).

`approve` exits `0` on success; `reject` exits `0` after recording the rejection (the staging dir is preserved for forensics). `success: false` with `error` on unknown `run_id` / config error.

## `forgelm purge`

Three-mode dispatcher: `--row-id`, `--run-id`, or `--check-policy`. Wave 2b Phase 21 — GDPR Article 17 right-to-erasure.

**Row-erasure success envelope — wet run** (`forgelm purge --row-id ROW --corpus PATH`):

```json
{
  "success": true,
  "mode": "row",
  "dry_run": false,
  "row_id_hash": "abc123...64-hex",
  "salt_source": "per_dir",
  "corpus_path": "/work/train.jsonl",
  "matches": 1,
  "first_line": 42,
  "bytes_freed": 142,
  "warnings": []
}
```

**Row-erasure success envelope — dry run:** identical shape minus `bytes_freed` (the rewrite is skipped); `warnings: []` and `dry_run: true`.

**Run-erasure success envelope — wet run** (`--run-id RUN --kind {staging,artefacts}`):

```json
{
  "success": true,
  "mode": "run",
  "kind": "staging",
  "dry_run": false,
  "run_id": "fg-abc123",
  "deleted": ["/work/output/final_model.staging.fg-abc123"],
  "bytes_freed": 1048576
}
```

**Run-erasure success envelope — dry run:** swap `deleted` for `would_delete` (same list-of-paths shape); omit `bytes_freed`.

**Check-policy success envelope — retention block configured** (`--check-policy [--config PATH]`):

```json
{
  "success": true,
  "violations": [
    {
      "artefact_kind": "staging_dir[fg-abc123]",
      "path": "/work/output/final_model.staging.fg-abc123",
      "age_days": 14.7,
      "horizon_days": 7,
      "age_source": "audit"
    }
  ],
  "count": 1
}
```

**Check-policy success envelope — no retention block** (operator's config omits the `retention:` block, or no `--config` was supplied): the envelope drops `count` and adds a `note` field explaining the no-op:

```json
{
  "success": true,
  "violations": [],
  "note": "No `retention:` block in the loaded config; nothing to enforce.  See `docs/guides/gdpr_erasure.md` for the schema."
}
```

| Key | Type | Notes |
|---|---|---|
| `success` | bool | `true` for a successful operation; `false` (with `error`) on config / runtime failure. |
| `mode` (row/run) | str | Discriminator; `"row"` or `"run"`. Absent on `--check-policy`. |
| `kind` (run) | str | `"staging"` or `"artefacts"`. Run mode only. |
| `dry_run` | bool | Mirrors the `--dry-run` flag. Present in row + run envelopes; absent in `--check-policy` (the mode is read-only by definition). |
| `row_id_hash` | str | 64-character lowercase hex SHA-256 digest of `salt + raw_value`. Cleartext value never appears in the envelope. **Note:** the digest is emitted as plain hex (no `sha256:` prefix) so consumers can `==`-compare against `hashlib.sha256(...).hexdigest()` directly. |
| `salt_source` | str | `"per_dir"` or `"env_var"` per `FORGELM_AUDIT_SECRET` toggle. |
| `corpus_path` | str | Absolute path to the JSONL corpus the operator passed via `--corpus`. Row mode only. |
| `matches` | int | Count of rows that matched the `--row-id`. Row mode only. |
| `first_line` | int | 1-based line number of the first matching row, captured *before* the rewrite. Row mode only. |
| `bytes_freed` | int | Bytes reclaimed by the deletion. Present on wet runs (row + run); absent on dry runs. |
| `warnings` | list[str] | Names of `data.erasure_warning_*` audit events emitted alongside the row erasure (memorisation / synthetic-data / external-copies). Row mode only. |
| `would_delete` (run dry) / `deleted` (run wet) | list[str] | Run mode: paths the dispatcher targeted for removal. Different key on dry vs wet so consumers can branch on shape. |
| `violations` | list[object] | `--check-policy` only. `artefact_kind` is one of `audit_log`, `staging_dir`, `staging_dir[<run_id>]`, `compliance_bundle`, `data_audit_report`, `raw_documents[...]`. `age_source` ∈ `{audit, mtime}`. |
| `count` | int | `--check-policy` only when a `retention:` block is configured; equals `len(violations)`. Absent in the no-retention-block branch (which adds `note` instead). |
| `note` | str | `--check-policy` only when no `retention:` block is configured. Operator-facing one-liner pointing at the GDPR guide. |

**Exit code mapping:** `0` = success or successful policy report; `1` = config error (unknown row, missing corpus, conflicting flags, malformed `--check-policy --config`); `2` = runtime error (I/O, atomic rename failure).

## `forgelm reverse-pii`

Wave 3 Phase 38 — GDPR Article 15 right-of-access companion to `forgelm purge`. Walks JSONL corpora and reports every line where the supplied identifier appears.

**Success envelope:**

```json
{
  "success": true,
  "query_hash": "abc...64-hex",
  "identifier_type": "email",
  "scan_mode": "plaintext",
  "salt_source": "per_dir",
  "matches": [
    {
      "file": "/work/data/train.jsonl",
      "line": 42,
      "snippet": "…Contact: alice@example.com regarding the appointment…"
    }
  ],
  "files_scanned": [
    {"path": "/work/data/train.jsonl", "match_count": 1}
  ],
  "match_count": 1
}
```

| Key | Type | Notes |
|---|---|---|
| `query_hash` | str | 64-character lowercase hex SHA-256 digest of `salt + raw --query`, where `salt` is the per-output-dir audit salt that `forgelm purge` also uses for `target_id`. The raw value never appears in the envelope; cross-tool correlation with purge's `target_id` works out of the box. |
| `identifier_type` | str | Echo of `--type` ∈ `{literal, email, phone, tr_id, us_ssn, iban, credit_card, custom}`. Default is `literal` — the query is matched as a literal substring. |
| `scan_mode` | str | `"plaintext"` (default — verbatim search; mask-leak detection) or `"hash"` (`--salt-source` set; SHA-256 of `salt + query` searched in the corpus). |
| `salt_source` | str | Which salt-resolution path was used: `"plaintext"` (no `--salt-source`), `"per_dir"` (per-output-dir salt file only), or `"env_var"` (XOR with `FORGELM_AUDIT_SECRET`). Mirrored in the audit event so a compliance reviewer can tell whether two digests were salted the same way. |
| `matches` | list[object] | One entry per line that matched. `snippet` is centred on the matched span and capped at 160 chars (`…` separator); the matched identifier is always preserved within the window. |
| `files_scanned` | list[object] | Per-file match counts, in glob-resolution order. The list is the source of truth for which paths the scan walked. |
| `match_count` | int | Equals `len(matches)`; convenience for CI gates that branch on "any match." |

**Exit code mapping:** `0` = scan completed (matches list may be empty); `1` = config error (empty `--query`, malformed regex on `--type custom`, empty glob expansion, `--salt-source env_var` without `FORGELM_AUDIT_SECRET` — or `--salt-source per_dir` with `FORGELM_AUDIT_SECRET` set); `2` = runtime error (mid-scan I/O failure, malformed UTF-8 corpus, custom-regex ReDoS timeout, audit-init failure when `--audit-dir` was explicit).

## `forgelm cache-models`

Wave 2b Phase 35 — air-gap workflow blocker. Pre-populates the HuggingFace Hub cache.

**Success envelope** (`forgelm cache-models --model M [--safety S] [--output DIR]`):

```json
{
  "success": true,
  "models": [
    {
      "name": "meta-llama/Llama-3.2-3B",
      "cached_path": "/work/hf_cache/models--meta-llama--Llama-3.2-3B",
      "size_bytes": 3221225472,
      "size_mb": 3072.0,
      "duration_s": 142.7
    }
  ],
  "total_size_mb": 3072.0,
  "cache_dir": "/work/hf_cache"
}
```

| Key | Type | Notes |
|---|---|---|
| `models` | list[object] | One entry per `--model`; `--safety` (when supplied) appears as the last entry. |
| `models[].cached_path` | str | Path returned by `huggingface_hub.snapshot_download` (operator's `--output` or env-resolved `HF_HUB_CACHE`). |
| `total_size_mb` | float | Sum of every `models[].size_mb`. |
| `cache_dir` | str | Operator's `--output`, or env-resolved (`HF_HUB_CACHE > HF_HOME/hub > ~/.cache/huggingface/hub`). |

**Exit code mapping:** `0` = every model cached; `1` = config error (no `--model`, malformed name); `2` = runtime error (Hub failure, disk-full, broken environment / missing core dep).

## `forgelm cache-tasks`

Wave 2b Phase 35 — pre-populates the lm-evaluation-harness task dataset cache. Requires the `[eval]` extra.

**Success envelope** (`forgelm cache-tasks --tasks CSV [--output DIR]`):

```json
{
  "success": true,
  "tasks": [
    {"name": "hellaswag", "cached": true, "error": null},
    {"name": "arc_easy", "cached": true, "error": null}
  ],
  "cache_dir": "/work/datasets_cache"
}
```

| Key | Type | Notes |
|---|---|---|
| `tasks` | list[object] | One entry per task; `cached: false` with a non-null `error` is per-task best-effort (the batch continues). |
| `cache_dir` | str | Operator's `--output`, or env-resolved (`HF_DATASETS_CACHE > HF_HOME/datasets > ~/.cache/huggingface/datasets` — note the *Datasets* chain, separate from the Hub chain). |

**Exit code mapping:** `0` = enumeration succeeded (per-task download failures are reported in `tasks[].error` but do not fail the batch); `1` = config error (empty `--tasks`, unknown task, missing `[eval]` extra); `2` = runtime error (broken environment / mid-batch failure raised by the datasets layer).

## `forgelm safety-eval`

Wave 2b Phase 36 — standalone safety evaluation against a model checkpoint.

**Success envelope** (`forgelm safety-eval --model M {--probes JSONL | --default-probes}`):

```json
{
  "success": true,
  "model": "/work/final_model",
  "classifier": "meta-llama/Llama-Guard-3-8B",
  "probes": "/work/probes.jsonl",
  "output_dir": "/work/eval",
  "passed": true,
  "safety_score": 0.93,
  "safe_ratio": 0.95,
  "category_distribution": {"S1": 0, "S2": 1, "S3": 0},
  "failure_reason": null
}
```

| Key | Type | Notes |
|---|---|---|
| `success` | bool | Mirrors `passed`. `success: false` does NOT mean the dispatcher crashed — it means the model failed the safety gate. |
| `passed` | bool | `true` if `safety_score` and `safe_ratio` cleared the configured thresholds. |
| `safety_score` | float \| null | Aggregate score from `forgelm.safety.run_safety_evaluation`. |
| `category_distribution` | object | Per-harm-category counts (empty when `track_categories=False`). |
| `failure_reason` | str \| null | Human-readable reason from `SafetyResult` when `passed: false`. |

**Exit code mapping:** `0` = thresholds passed; `1` = config error (missing `--model`, conflicting probes flags, GGUF model path); `2` = runtime error (model load failure, classifier load failure, broken environment); `3` = `EXIT_EVAL_FAILURE` — evaluation completed but the safety gate said no (operator-actionable: re-train or re-classify).

## `forgelm verify-annex-iv`

Wave 2b Phase 36 — EU AI Act Annex IV §1-9 artefact integrity check.

**Success envelope** (`forgelm verify-annex-iv PATH`):

```json
{
  "success": true,
  "path": "/work/output/compliance/annex_iv_metadata.json",
  "valid": true,
  "missing_fields": [],
  "manifest_hash_actual": "abcd1234...",
  "manifest_hash_expected": "abcd1234...",
  "manifest_hash_present": true,
  "reason": ""
}
```

| Key | Type | Notes |
|---|---|---|
| `valid` | bool | `true` when all 9 §1-9 fields are present AND (when `metadata.manifest_hash` is present) the recomputed hash matches. |
| `missing_fields` | list[str] | Names of any `_ANNEX_IV_REQUIRED_FIELDS` that are absent / empty. |
| `manifest_hash_actual` | str \| null | Recomputed canonical SHA-256 of the artefact-minus-metadata. |
| `manifest_hash_expected` | str \| null | Value extracted from the artefact's `metadata.manifest_hash` field. |
| `manifest_hash_present` | bool | `false` when the artefact carries no hash (older exports — verifier passes with a warning). |
| `reason` | str | Empty on `valid: true`; one-line failure description otherwise. |

**Exit code mapping:** `0` = `valid: true`; `1` = `valid: false` (missing fields or hash mismatch — auditor-facing rejection); `2` = runtime error (file not found, unreadable, malformed JSON).

## `forgelm verify-gguf`

Wave 2b Phase 36 — GGUF model file integrity check.

**Success envelope** (`forgelm verify-gguf PATH`):

```json
{
  "success": true,
  "path": "/work/exports/model.q4_k_m.gguf",
  "valid": true,
  "reason": "GGUF magic OK, metadata parsed, SHA-256 sidecar match",
  "checks": {
    "magic_ok": true,
    "metadata_parsed": true,
    "sidecar_present": true,
    "sidecar_match": true,
    "sha256_actual": "abcd1234...",
    "sha256_expected": "abcd1234..."
  }
}
```

| Key | Type | Notes |
|---|---|---|
| `valid` | bool | `true` when the magic header passes AND any *attempted* check (metadata block, SHA-256 sidecar) succeeded. A skipped check (e.g. `metadata_parsed: false` because the optional `gguf` package is absent) does NOT force `valid: false` — only an *attempted-and-failed* check does. |
| `checks.magic_ok` | bool | First 4 bytes equal `b"GGUF"`. |
| `checks.metadata_parsed` | bool | `true` when the `gguf` metadata block was successfully parsed; `false` when the block is corrupted **OR** when the optional `gguf` package is absent / skipped. A `false` value alone does NOT force `valid: false` — corruption sets `reason` and rejects, but the absent-package case leaves `valid` unaffected. |
| `checks.sidecar_present` | bool | `true` when `<path>.sha256` exists. |
| `checks.sidecar_match` | bool \| null | `true` on byte-for-byte match; `false` on mismatch or malformed sidecar; `null` when no sidecar. A *malformed* sidecar (empty / non-hex / wrong-length) fails closed. |
| `reason` | str | One-line summary; carries the failure detail on `valid: false`. |

**Exit code mapping:** `0` = `valid: true`; `1` = `valid: false` (magic mismatch, metadata block *corrupted*, SHA-256 mismatch, malformed sidecar); `2` = runtime error (file not found, unreadable). The optional-`gguf`-package-missing path stays at `valid: true` + exit `0` (operator's "metadata check skipped" — the magic header + SHA-256 sidecar checks remain the load-bearing integrity surface).

## `forgelm export`

GGUF / merged-weights export for the post-training handoff.

**Envelope** (`forgelm export MODEL --output OUT.gguf`):

```json
{
  "success": true,
  "output_path": "/work/exports/model.q4_k_m.gguf",
  "format": "gguf",
  "quant": "q4_k_m",
  "sha256": "abcd1234...",
  "size_bytes": 4815162342,
  "error": null
}
```

| Key | Type | Notes |
|---|---|---|
| `output_path` | str \| null | Path to the written artefact; `null` on failure. |
| `format` | str \| null | Export format (e.g. `gguf`, `merged`). |
| `quant` | str \| null | Quantisation level used (single value — `--quant` takes one level per invocation). |
| `sha256` | str \| null | SHA-256 over the written artefact; `null` on failure. |
| `size_bytes` | int \| null | Artefact size in bytes; `null` on failure. |
| `error` | str \| null | `null` on success; operator-facing message on failure. Branch on `success`, not on `error` presence. |

**Exit code mapping:** `0` = export succeeded; `2` = runtime failure (conversion error, missing `[export]` extra, unreachable model path).

## `forgelm deploy`

Deployment-config generation for Ollama / vLLM / TGI / HF Endpoints.

**Envelope** (`forgelm deploy MODEL --target ollama --output Modelfile`):

```json
{
  "success": true,
  "target": "ollama",
  "output_path": "/work/deploy/Modelfile",
  "error": null
}
```

| Key | Type | Notes |
|---|---|---|
| `target` | str \| null | Deployment target (`ollama`, `vllm`, `tgi`, `hf-endpoints`). |
| `output_path` | str \| null | Path to the generated config; `null` on failure. |
| `error` | str \| null | `null` on success; operator-facing message on failure. Branch on `success`, not on `error` presence. |

**Exit code mapping:** `0` = config generated; `2` = runtime failure (unsupported target, unwritable output path, model load error).

## Adding a new subcommand

A new subcommand that supports `--output-format json` MUST land with:

1. The envelope documented in this page (EN + TR mirror).
2. A test in `tests/test_json_envelope_contract.py` (or a per-subcommand test file) that pins the exact set of top-level keys.
3. The per-collection key follows the convention "results live under a key named after the subcommand's primary noun" (so `doctor` → `checks`, `approvals --pending` → `pending`, etc.).

Renaming a key after merge is a MAJOR-version bump per the [release standard on GitHub](https://github.com/HodeTech/ForgeLM/blob/main/docs/standards/release.md).

## See also

- [Exit Codes](#/reference/exit-codes) — the contract `success: bool` aligns with.
- [`error-handling.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/standards/error-handling.md) — the error envelope contract (GitHub source).
- [`release.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/standards/release.md) — when JSON renames count as breaking (GitHub source).
