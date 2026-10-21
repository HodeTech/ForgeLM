# `forgelm purge` — Subcommand Reference

> **Audience:** ForgeLM operators honouring GDPR Article 17 erasure requests against training corpora and run-scoped artefacts, plus auditors verifying the resulting `data.erasure_*` audit chain.
> **Mirror:** [purge_subcommand-tr.md](purge_subcommand-tr.md)

`forgelm purge` is the operator-facing implementation of the **GDPR Article 17 right-to-erasure** for ForgeLM training corpora and run artefacts (Phase 21). It atomically erases a corpus row; with `--run-id` it deletes a run's staging directory (`--kind staging`) or compliance files whose names embed the run id (`--kind artefacts`, see below); and it records every step in the tamper-evident `audit_log.jsonl` chain.

For the deployer-flow walkthrough (DSAR ticket → CLI invocation → verification), see [`../guides/gdpr_erasure.md`](../guides/gdpr_erasure.md). This page is the per-flag, per-event reference.

## Synopsis

```text
forgelm purge --row-id ID --corpus PATH [--row-matches {one,all}]
              [--output-dir DIR] [--justification TEXT] [--dry-run]
              [--output-format {text,json}]

forgelm purge --run-id RUN_ID --kind {staging,artefacts}
              --output-dir DIR [--justification TEXT] [--dry-run]
              [--output-format {text,json}]

forgelm purge --check-policy --config PATH [--output-dir DIR]
              [--output-format {text,json}]
```

The three modes are mutually exclusive (`argparse` enforces a single mode group).

## Modes

### Corpus-row erasure (`--row-id`)

Erases a single row identified by its `id` (or `row_id`) field from a JSONL training corpus. Implemented in `forgelm.cli.subcommands._purge._run_purge_row_id`.

| Flag | Required | Description |
|---|---|---|
| `--row-id ID` | yes | Identifier to erase. Hashed before audit emission via `forgelm.cli.subcommands._purge._hash_target_id`. |
| `--corpus PATH` | yes | Single JSONL file. Directory mode is rejected — operators loop in their own script. |
| `--row-matches {one,all}` | no (default `one`) | `one` refuses on >=2 matches; `all` deletes every match (operator confirms intent). |
| `--output-dir DIR` | no | Defaults to the parent of `--corpus`. The per-output-dir salt at `<output_dir>/.forgelm_audit_salt` is read here for `target_id` hashing. The implicit fallback emits a WARNING naming the resolved dir and the `final_model/` the memorisation check will look for, so the operator can pin `--output-dir` to the training output directory and for cross-tool correlation with `forgelm reverse-pii`. |
| `--justification TEXT` | no | Operator-supplied reason recorded on every erasure event. Reference your internal ticket id; do not paste subject identifiers. |
| `--dry-run` | no | Preview the deletion + emit the audit chain (with `dry_run=true`) without modifying disk. |

**Atomic write contract.** The corpus is rewritten via a sibling temp file + `os.replace`, so an interrupted purge leaves either the full pre-erasure file or the full post-erasure file — never a partial state.

**Line-number fallback is rejected** (design §4.2). ForgeLM does not currently provide an id-population helper (`forgelm audit --add-row-ids` is on the Phase 28 backlog); operators with id-less corpora must pre-populate ids with an operator-side script before invoking `forgelm purge --row-id`.

### Run-scoped artefact erasure (`--run-id` + `--kind`)

| Kind | Target |
|---|---|
| `staging` | `<output_dir>/final_model.staging.<run_id>/`, and the legacy `final_model.staging/` only when it contains a `staging_run_id` file whose content equals `<run_id>`. ForgeLM does not write that marker, so remove an unmarked legacy staging directory yourself. |
| `artefacts` | Any file directly under `<output_dir>/compliance/` whose name contains `<run_id>` bounded by `_`, `-`, `.` or the start/end of the name (not only `.json`). |

> **`--kind artefacts` does not find the standard compliance bundle.** The trainer and exporter write fixed basenames (`compliance_report.json`, `training_manifest.yaml`, `data_provenance.json`, `data_governance_report.json`, and `risk_assessment.json` / `annex_iv_metadata.json` when configured), none of which contains a run id. On a normal output directory the command therefore finds nothing, emits `data.erasure_failed` (`NoMatchingArtefacts`) and exits 1; it deletes nothing. It acts only on files you have named with the run id yourself. Until the resolver is fixed, remove the bundle through your storage layer.

`--kind logs` is **intentionally absent**: the audit log is append-only and the tool never deletes it. Whether entries that name the data subject may be kept is the controller's decision, and the controller documents why: the entries are still necessary for their purpose, or an Article 17(3) exception applies. Article 17(3)(b), for example, applies only to the extent a specific Union or Member State legal obligation requires the processing, and you have to be able to name that obligation.

### Retention-policy report (`--check-policy`)

Read-only scan. Walks `<output_dir>`, compares each artefact's age (canonical: the run's first audit event for a `final_model.staging.<run_id>/` directory, else the audit log's genesis `timestamp`; fallback: filesystem `mtime` with `age_source=mtime`) against the loaded config's `retention:` horizons, and emits a structured violation list.

**Successful policy reports always exit 0** (report-not-gate semantic per design §10 Q5). Config-load failures exit `EXIT_CONFIG_ERROR` (1) — an explicit `--config` that is missing, unreadable, or fails Pydantic validation surfaces as a non-zero so the operator does not mistake "loader failed" for "no violations". Operators wiring a CI gate use `--output-format json` and pipe to `jq -e '.success and (.violations | length == 0)'` (exit 1 while violations exist) themselves.

## Salt resolution

The per-output-dir salt at `<output_dir>/.forgelm_audit_salt` is created on first use (mode `0600`, atomic O_EXCL write) by `forgelm.cli.subcommands._purge._read_persistent_salt`. `forgelm.cli.subcommands._purge._resolve_salt` returns `(salt_bytes, salt_source)`:

- `salt_source = "per_dir"` — `FORGELM_AUDIT_SECRET` is unset; the persistent salt is used verbatim.
- `salt_source = "env_var"` — `FORGELM_AUDIT_SECRET` is set; the first 16 bytes are XOR'd with the persistent salt.

**This XOR feeds identifier hashing only.** The audit-chain HMAC key is derived independently as `SHA-256(FORGELM_AUDIT_SECRET ‖ run_id)` inside `forgelm.compliance.AuditLogger.__init__`. The two primitives are intentionally separate; rotating `FORGELM_AUDIT_SECRET` therefore rotates both, but inspecting either does not reveal the other.

## Cross-tool digest correlation with `forgelm reverse-pii`

When `forgelm purge --row-id <value>` and `forgelm reverse-pii --query <value> --salt-source per_dir` run against the **same** `<output_dir>` (i.e. consume the same `.forgelm_audit_salt`) and `FORGELM_AUDIT_SECRET` was unset for the purge (`salt_source=per_dir`; when it was set, the purge XORs it into the salt and records `salt_source=env_var`, so query with `--salt-source env_var` and the same secret instead), the `target_id` field on `data.erasure_*` events and the `query_hash` field on `data.access_request_query` events are byte-identical. A compliance reviewer can therefore correlate Article 17 erasures and Article 15 access requests for the same data subject without ever seeing the cleartext identifier. Pinned by `tests/test_reverse_pii.py::test_purge_target_id_matches_reverse_pii_query_hash_on_same_output_dir`.

## Audit events emitted

All six events ride the common envelope from [`audit_event_catalog.md`](audit_event_catalog.md). The catalog rows are reproduced here for convenience.

| Event | When emitted | Key payload |
|---|---|---|
| `data.erasure_requested` | First step of any `--row-id` / `--run-id` invocation, BEFORE any deletion. `--check-policy` is read-only and emits no events. | `target_kind` ∈ `{row, staging, artefacts}`, `target_id` (hashed in row mode), `salt_source` (row mode), `corpus_path` (row), `output_dir` (run), `justification`, `dry_run` |
| `data.erasure_completed` | Successful deletion finished. | All `requested` fields + `bytes_freed`, `files_modified`, `pre_erasure_line_number` (row mode), `match_count` (row mode) |
| `data.erasure_failed` | Disk operation raised, OR no matching row/run, OR multi-row policy refused on ambiguity. | All `requested` fields + `error_class`, `error_message` |
| `data.erasure_warning_memorisation` | Row erasure × a `final_model/` directory exists directly under `--output-dir` (literal name; `--output-dir` defaults to the corpus's directory, so pass your training output directory). ForgeLM does not determine which runs consumed the corpus. | All `completed` fields + `affected_run_ids` |
| `data.erasure_warning_synthetic_data_present` | Row erasure × `synthetic_data*.jsonl` exists in `output_dir`. | All `completed` fields + `synthetic_files` |
| `data.erasure_warning_external_copies` | **Row mode only, when `--config` is given.** Loaded config has a non-empty `webhook` block; downstream consumers may have received notices. | All `completed` fields + `webhook_targets` (redacted URLs) |

`forgelm verify-audit <output_dir>/audit_log.jsonl` continues to validate the chain after any number of erasure events — the tool appends new events, never rewrites old ones.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success, or a successful `--check-policy` report (report-not-gate semantic). |
| 1 | Config error: unknown `--row-id`, no staging directory / compliance file matching `--run-id` (including `--kind artefacts` against the standard fixed-name bundle), missing `--corpus`, mutually-exclusive flag combination, or `--check-policy --config <path>` that is missing / unreadable / fails Pydantic validation. |
| 2 | Runtime error: I/O failure, permission denied, atomic-rename failure. |

`--check-policy` never returns code 3 or 4. Codes 3 (`EXIT_EVAL_FAILURE`) and 4 (`EXIT_AWAITING_APPROVAL`) are reserved for the training pipeline and are not part of this subcommand's surface.

## JSON output envelope

With `--output-format json` every invocation prints exactly one JSON object on stdout. The shape varies by mode — three envelopes in total.

**Row mode (success)** — emitted by `_perform_row_erasure_and_audit`:

```json
{
  "mode": "row",
  "dry_run": false,
  "salt_source": "per_dir",
  "corpus_path": "/abs/path/data/train.jsonl",
  "matches": 1,
  "first_line": 142,
  "bytes_freed": 482,
  "warnings": []
}
```

For `--dry-run` invocations the same envelope appears with `dry_run: true` and without `bytes_freed` (the rewrite never happens).

**Run mode (success)** — emitted by `_run_purge_run_id`:

```json
{
  "mode": "run",
  "kind": "staging",
  "dry_run": false,
  "run_id": "fg-abc123def456",
  "deleted": ["/abs/path/outputs/run42/final_model.staging.fg-abc123def456"],
  "bytes_freed": 102400000
}
```

`deleted` is a **list** of absolute paths actually removed (or, on `--dry-run`, the `would_delete` key carries the same shape and `deleted` is absent).

**`--check-policy` mode** — emitted by `_run_purge_check_policy`:

```json
{
  "success": true,
  "violations": [
    {
      "artefact_kind": "ephemeral_artefact",
      "path": "/abs/path/outputs/run42/compliance/data_audit_report.json",
      "age_days": 121.4,
      "horizon_days": 90,
      "age_source": "audit"
    }
  ],
  "count": 1
}
```

`age_source` ∈ `{audit, mtime}` — `audit` is the canonical age (run's first audit event timestamp); `mtime` is the filesystem fallback flagged for the operator's awareness.

**Failure envelope (any mode)** — `_output_error_and_exit`:

```json
{
  "success": false,
  "error": "Row id 'ali@example.com' not found in 'data/train.jsonl'."
}
```

Note the asymmetry: row/run success envelopes do **not** carry an explicit `success` field (the `mode` discriminator is the success signal); `--check-policy` and the failure envelope do. Operators wiring CI gates should branch on the `mode` key (success path) or `success: false` (failure path), not on the presence/absence of `success: true`.

## See also

- [`../guides/gdpr_erasure.md`](../guides/gdpr_erasure.md) — deployer-flow walkthrough (DSAR ticket → CLI → verify chain).
- [`reverse_pii_subcommand.md`](reverse_pii_subcommand.md) — sibling Article 15 right-of-access tool.
- [`audit_event_catalog.md`](audit_event_catalog.md) — full event vocabulary with envelope spec.
- [`../qms/access_control.md`](../qms/access_control.md) §3.4 — operator identity contract (the `FORGELM_OPERATOR` recorded on every erasure event).
