# `forgelm verify-annex-iv` — Reference

> **Audience:** Compliance operators and CI gates verifying Annex IV technical-documentation artifacts before submission.
> **Mirror:** [verify_annex_iv_subcommand-tr.md](verify_annex_iv_subcommand-tr.md)

The `verify-annex-iv` subcommand reads an Annex IV technical-documentation JSON file, validates the nine required field categories per EU AI Act Annex IV §1-9, and recomputes the manifest hash to detect post-generation tampering. The CLI delegates to the library entry point `forgelm.verify.verify_annex_iv_artifact` (also exposed at the package root as `forgelm.verify_annex_iv_artifact`) and shares the canonicalisation routine `forgelm.compliance.compute_annex_iv_manifest_hash` with the writer in `forgelm.compliance.build_annex_iv_artifact` — so a legitimate artefact can never fail its own verifier on a writer/verifier byte drift.

## Synopsis

```text
forgelm verify-annex-iv [--pipeline] [--output-format {text,json}]
                        [-q] [--log-level {DEBUG,INFO,WARNING,ERROR}]
                        path
```

`path` (positional, required) — path to the Annex IV JSON artifact (typically `compliance/annex_iv_<run>.json` under the training output directory). With `--pipeline`, `path` is instead a pipeline run **directory** containing `compliance/pipeline_manifest.json`.

## Flags

| Flag | Default | Description |
|---|---|---|
| `--pipeline` | _off_ | Interpret `path` as a multi-stage pipeline run directory and verify the chain-level `pipeline_manifest.json` — its own content hash, chain integrity, stage-index ordering, `stopped_at` coherence, and a deep parse of every completed stage's Annex IV evidence. See [Pipeline mode](#pipeline-mode). |
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

The same three-way meaning holds in `--pipeline` mode and is the rule to reason from throughout this page:

- `6` — the verifier **compared something and it did not match**.
- `1` — the verifier **never got to compare anything**: absent input, unparseable input, or evidence that exists but that nothing attests to.
- `2` — a genuine runtime I/O failure on a reachable path.

## Pipeline mode

`forgelm verify-annex-iv --pipeline <run_dir>` reads `<run_dir>/compliance/pipeline_manifest.json` and verifies the multi-stage run as a whole. It is the chain-level aggregator over the per-stage verifier documented above: the pipeline manifest is the index, and each completed stage's Annex IV artefact is the evidence the index points at.

Verification runs in three layers, all in one pass:

1. **Structural + chain rules** — required top-level keys, stage-index monotonicity, `stopped_at` coherence, and chain integrity (for every stage with `input_source: chain`, the previous stage's `output_model` must equal this stage's `input_model`).
2. **The chain manifest's own content hash** — see below.
3. **Per-stage evidence deep parse** — see below.

### The chain manifest hash

`generate_pipeline_manifest` stamps `metadata.manifest_hash` at write time: a SHA-256 over the canonicalised manifest with the `metadata` block stripped, computed by the same `forgelm.compliance.compute_annex_iv_manifest_hash` routine the single-artefact path uses. Sharing the routine is deliberate — writer and verifier cannot drift byte-for-byte across two implementations.

**What it covers:** everything in the manifest except the `metadata` block. That includes the fields the structural and chain rules cannot see — provider metadata, per-stage `metrics`, `gate_decision`, `final_status`, and per-stage `error` strings. Before the hash existed, all of those could be edited after generation and the verifier would still report the manifest valid.

**What it deliberately does not cover:** the `metadata` block itself (it holds the hash, so including it would be circular), and the *contents* of the per-stage evidence files. The chain hash pins the index, not the documents the index references; those are covered by layer 3 and by their own per-artefact hashes.

**Threat model: this is an unkeyed digest, and it is not tamper-proofing.** `compute_annex_iv_manifest_hash` is a public function in `forgelm/compliance.py`. It takes no secret, and anyone with the package installed can call it. So the guarantee is exactly this:

| Detects | Does not detect |
|---|---|
| Accidental corruption — truncated writes, a mangled copy, a botched archive restore. | Anyone who can write the manifest file. They edit the field, re-run the public function, and write the new digest back. |
| Casual or careless edits — a field "corrected" by hand after the fact, without realising a digest covers it. | A deliberate, informed forgery of any covered field, including `forgelm_version` and the `annex_iv` block. |
| Drift between the manifest and the run it describes. | Deletion or replacement of the whole manifest together with its evidence tree. |

`hash_state: verified` therefore means *"this manifest is internally consistent"*, **not** *"nobody altered this manifest"*. An unkeyed hash cannot distinguish the writer from an attacker, because both can compute it.

**Contrast with the audit log, which *is* keyed.** When `FORGELM_AUDIT_SECRET` is set, each audit-log line carries an `_hmac` tag computed with a key derived as `sha256(secret + run_id)` (`forgelm/compliance.py`), and `forgelm verify-audit --require-hmac` refuses a log that is missing or fails those tags. Forging that requires the secret, not merely the code. The pipeline manifest hash has **no equivalent** — there is no `--require-hmac` for `verify-annex-iv`.

Operationally, this means the manifest's integrity rests on the integrity of the storage holding it. If the archive is the compliance record of record, put it somewhere with its own access control, write-once storage, or an external signature — do not treat `hash_state: verified` as a substitute. See [`docs/guides/safety_compliance.md`](../guides/safety_compliance.md) for the audit-log HMAC setup.

**Valid and verified are different states, and the CLI says which one you got.** Three outcomes exist, reported as `hash_state` in the JSON envelope:

| `hash_state` | What it means | Exit code | Text output |
|---|---|---|---|
| `verified` | A `manifest_hash` was present and the recomputed digest matched it. Nothing was edited after generation. | `0` (absent other findings) | `OK: … (hash verified, N stage artefact(s))` |
| `absent` | No `manifest_hash` in the manifest — an archive written before the stamp existed. The structural and chain rules passed, but nothing attested to the non-chain fields. | `0` (absent other findings) | `OK (UNVERIFIED): … — no manifest_hash; tampering not checked` |
| `mismatch` | A `manifest_hash` was present and the recomputed digest disagrees. The manifest was modified after generation. | `6` | `FAIL: …` plus a `manifest hash mismatch` violation |

The `absent` case is the one an operator must not misread. It exits `0` because a pre-hash archive is not evidence of tampering and refusing it would retroactively invalidate every manifest written before the stamp shipped. It is **not** a clean bill of health: no comparison happened. Distinguish the two by the `OK (UNVERIFIED)` prefix in text mode, or by `hash_state` in JSON mode — never by the exit code alone, and never by re-running and seeing `0`.

### Per-stage evidence deep parse

For every stage whose `status` is `completed`, the verifier resolves the stage's evidence pointer and parses the artefact it names. Previously this was an `os.path.isfile` existence check, so a zero-byte, malformed, or tampered artefact passed.

**What `completed` now guarantees.** For each completed stage, the evidence file was located, read, parsed as JSON, confirmed to be a JSON object, checked to hold every required Annex IV §1-9 field, and — when it carries a `manifest_hash` — hash-verified against its own contents. A stage counted in `evidence_verified` has had all six of those hold.

The check fails closed. Each of the following is a violation (exit `6`):

| Condition | Rationale |
|---|---|
| Completed stage records no evidence pointer at all | The manifest asserts a completed stage; an assertion with no evidence is not verifiable. |
| A relative pointer that escapes the pipeline directory | `../../../etc/hosts` is not a stage artefact. Absolute pointers are allowed unconditionally, because a stage's `training.output_dir` is config-declared and legitimately lives outside the pipeline tree. |
| The pointer is a symlink | The evidence would be whatever the link resolves to at verification time, which is not a property of the archived run. |
| The pointer is a directory | Refused with a verdict instead of a traceback. |
| Zero bytes | An empty file is not evidence. |
| Larger than 8 MiB | Refused **unread**. A verifier that can be OOM-killed by its own input is not a verifier. |
| Malformed JSON, or not valid UTF-8 | Cannot be parsed, so cannot be compared. |
| Root is not a JSON object | Nothing to check the required fields against. |
| A required Annex IV field is missing or empty | Note the deliberate divergence: verified standalone, an incomplete artefact exits `1`; as chain evidence it exits `6`, because the pipeline manifest *asserts* this stage completed with valid evidence and that assertion was compared and did not hold. |
| The artefact's own `manifest_hash` disagrees with its contents | Tamper detection on the evidence itself. |

**Missing evidence routes on whether the run configured Annex IV at all.** An earlier revision of this page described a missing artefact as unconditionally UNVERIFIED / exit `1`. That was a reader-side-only behaviour and it has been superseded — it made *deleted* evidence (archetypal Article 12 tampering) exit softer than *corrupted* evidence. The shipped routing reads the chain manifest's `annex_iv` block, the same source the writer uses to decide whether to emit anything:

| Run configured a `compliance:` block? | Verdict | Exit |
|---|---|---|
| Yes — the artefact was written and is now gone | **VIOLATION** | `6` |
| No — nothing was ever produced, so nothing is missing | **UNVERIFIED** | `1` |

The **legacy-pointer fallback** is version-gated. ForgeLM before `0.9.1` recorded a `training_manifest.json` pointer that no writer has ever satisfied (`export_compliance_artifacts` emits `training_manifest.yaml` and `annex_iv_metadata.json`); `0.9.1` repointed the writer at the real artefact. For a manifest whose `forgelm_version` parses below `0.9.1`, the verifier resolves that legacy basename to its `annex_iv_metadata.json` sibling and verifies it normally. On a current manifest the basename is not a legacy artefact — it is a pointer that disagrees with what this version writes — and it gets the routing in the table above. An absent or unparseable `forgelm_version` is treated as *not* legacy, the conservative direction. Note that only the leading numeric release components are compared, so a pre-release such as `0.9.1rc1` counts as `0.9.1` and does **not** unlock the compatibility path.

One condition still reports **UNVERIFIED** (exit `1`) rather than a violation, because it means the verifier never got to compare rather than a comparison failing:

- The artefact is structurally complete but carries no `manifest_hash`. Tampering could not be checked.

**Only stages whose `status` is exactly `completed` are deep-parsed — but no stage is omitted from the report.** Because the chain hash is unkeyed, an adversary who can write the archive could otherwise delete a stage's evidence, flip its status away from `completed`, recompute the digest with the public function, and drop the stage out of the report entirely. Three rules narrow that:

| Rule | Effect |
|---|---|
| **Every stage row is reported.** `stages_total` counts all of them, `status_census` counts them by status token, and `stage_dispositions` gives each one a stated reason for not being deep-parsed (`not_applicable:filtered`, `not_applicable:gated`, …). | A downgraded stage no longer vanishes; it appears with `stages_examined < stages_total`. |
| **An unrecognised status is a violation** (exit `6`), never a stage to skip. The seven tokens any ForgeLM version writes are a closed set. | An attacker cannot invent a token to be ignored by. |
| **`gate_decision: "passed"` with a status other than `completed` is a violation** (exit `6`). That gate value is written on exactly one code path, alongside `status = "completed"`. | Catches a downgrade to a *recognised* token on any stage that ran a gate. |

**Residual gap, stated rather than glossed.** A stage that completed *without* a `gate_decision` can still be downgraded to a recognised non-completed status by someone who can write the manifest. It is no longer silently dropped — it is counted in `stages_total` and named in `status_census` / `stage_dispositions` — but it produces no violation, so exit `0` is still possible. Only `gate_decision` is used as the tamper signal: `finished_at`, `metrics`, `exit_code` and `output_model` all survive a legitimate `--stage` or chain-break skip, so keying on them would false-alarm on real runs. Closing this needs an authenticated manifest, not a further reader-side check. **Treat the storage as the trust boundary, and gate CI on the counters rather than on the exit code** — see below.

A stat failure on a path that passed the existence check reports IO_ERROR (exit `2`).

**One more rule closes the empty-set hole:** a manifest claiming `final_status: completed` while carrying no completed stage is itself a violation (exit `6`). Without it, the verifier's happiest path would be the one where it inspected nothing.

**Precedence.** When several findings coexist, integrity wins: any untagged violation routes to `6` even when an unreadable (`2`) or unattested (`1`) finding was reported in the same run. A weaker finding must never mask a stronger one.

### Pipeline-mode JSON envelope

`--pipeline --output-format json` emits the counters that make "OK" unambiguous:

| Key | Type | Meaning |
|---|---|---|
| `success` | bool | `true` when the violation list is empty. Does **not** imply `hash_state == "verified"`. |
| `mode` | string | Always `"pipeline"` in this mode. |
| `path` | string | Absolute path of the run directory. |
| `violations` | array of string | Human-readable findings, with internal routing tokens stripped. |
| `stages_total` | int | Every stage row the manifest carries, regardless of status. |
| `stages_examined` | int | Completed stages the evidence layer looked at. |
| `status_census` | object | Every stage counted by its `status` token, sorted. A non-object stage row is counted under `<type>`. |
| `stage_dispositions` | array of object | One row per stage — `name`, `index`, `status`, and a `disposition` stating why it was or was not deep-parsed. |
| `evidence_verified` | int | Of those, how many passed every check including their own hash. |
| `evidence_unverified` | int | Of those, how many were reached but unattested. |
| `hash_state` | string | `verified` / `absent` / `mismatch` — the chain manifest's own hash. |

A CI gate that wants "verified, not merely valid" should assert `hash_state == "verified"` and `evidence_verified == stages_examined`, not just exit `0`. Assert `stages_examined` against the stage count the pipeline config declares — not against `> 0`, and not against `stages_total`, since both are read from the same manifest an attacker would have edited. That external expectation is what closes the residual gap above: a stage downgraded out of `completed` shows up as `stages_examined` falling short of the count you expected, and `status_census` names the token it was downgraded to. And remember that `hash_state == "verified"` attests internal consistency, not authenticity — see the threat model above.

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

### Pipeline mode: verified vs merely valid

```shell
$ forgelm verify-annex-iv --pipeline ./pipeline_run
OK: pipeline manifest at ./pipeline_run (hash verified, 3 stage artefact(s))
$ echo $?
0
```

The same command against an archive written before the hash stamp existed:

```shell
$ forgelm verify-annex-iv --pipeline ./archived_run_2026_03
OK (UNVERIFIED): pipeline manifest at ./archived_run_2026_03 — no manifest_hash; tampering not checked
$ echo $?
0
```

Both exit `0`; only the second one leaves tampering unchecked.

### Pipeline mode: rotten per-stage evidence

```shell
$ forgelm verify-annex-iv --pipeline ./pipeline_run
FAIL: pipeline manifest at ./pipeline_run
  - Stage 'dpo-preference': evidence at './pipeline_run/dpo/compliance/training_manifest.json' is zero bytes
$ echo $?
6
```

### Pipeline mode: JSON envelope for a CI gate

```shell
$ forgelm verify-annex-iv --pipeline --output-format json ./pipeline_run
{
  "success": true,
  "mode": "pipeline",
  "path": "/abs/path/pipeline_run",
  "violations": [],
  "stages_examined": 3,
  "evidence_verified": 3,
  "evidence_unverified": 0,
  "hash_state": "verified"
}
```

## See also

- [`audit_event_catalog.md`](audit_event_catalog.md) — `compliance.artifacts_exported` (Article 11 + Annex IV) and the rest of the canonical event vocabulary.
- [`webhook_schema.md`](webhook_schema.md) — the webhook event vocabulary, including the three `pipeline.*` events a multi-stage run emits while producing the manifest this command verifies.
- [`../guides/pipeline.md`](../guides/pipeline.md) — operator guide to multi-stage pipeline runs.
- [`verify_audit.md`](verify_audit.md) — companion verifier for `audit_log.jsonl`.
- [`verify_gguf_subcommand.md`](verify_gguf_subcommand.md) — companion verifier for exported GGUF artefacts.
- [Annex IV usermanual page](../usermanuals/en/compliance/annex-iv.md) — operator-facing primer that includes a full quick-start example.
- `forgelm.compliance.build_annex_iv_artifact` and `forgelm.compliance.compute_annex_iv_manifest_hash` — the writer-side counterparts to this verifier.
