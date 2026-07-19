---
title: Exit Codes
description: ForgeLM's exit-code contract — the public API for CI/CD pipelines.
---

# Exit Codes

ForgeLM's exit codes are a public contract. CI/CD pipelines, schedulers, and dashboards depend on them. They will not silently change between releases.

## The contract

| Exit | Constant | Meaning | Typical CI action |
|---|---|---|---|
| **0** | `EXIT_SUCCESS` | Run completed and the checkpoint was promoted. With `evaluation.auto_revert: true` every gate also passed; with the shipped default `auto_revert: false` a failed benchmark/safety/judge gate is **recorded in the JSON output but does not change the exit code** — see ["What exit 0 actually guarantees"](#what-exit-0-actually-guarantees) below. | Continue pipeline (parse gate blocks if `auto_revert` is off) |
| **1** | `EXIT_CONFIG_ERROR` | YAML invalid, file missing, env var unset, or argument malformed. | Fail fast |
| **2** | `EXIT_TRAINING_ERROR` | Training-time runtime error (any unhandled exception that isn't a config or eval-gate failure: data load, OOM, NaN loss, I/O failure, mid-stream audit-iteration OSError). | Investigate; surface logs |
| **3** | `EXIT_EVAL_FAILURE` | A benchmark/safety/judge gate failed **and** the model was auto-reverted (requires `evaluation.auto_revert: true`). With `auto_revert: false` a failed gate does not produce exit 3 — the run exits 0 with the failure recorded in the JSON gate blocks. | Investigate; do NOT promote |
| **4** | `EXIT_AWAITING_APPROVAL` | `evaluation.require_human_approval: true` blocking. | Hold pipeline; trigger reviewer |
| **5** | `EXIT_WIZARD_CANCELLED` | `forgelm --wizard` exited without producing a YAML — Ctrl-C, non-tty stdin refusal, or operator declined to save. Distinct from `EXIT_SUCCESS` so CI can tell "wizard finished" from "wizard never wrote anything". | Treat as no-op; surface message; do NOT continue with stale config |
| **6** | `EXIT_INTEGRITY_FAILURE` | `verify-audit` / `verify-annex-iv` / `verify-gguf` / `verify-integrity` read the target artefact successfully and its **integrity check failed** — a broken audit-log hash chain, an Annex IV manifest hash mismatch, a GGUF metadata/SHA-256 sidecar mismatch, or model files that no longer match `model_integrity.json`. Scoped to the four `verify-*` subcommands only; no other command emits it. | Treat as a security event, not a config fix — page whoever owns the artefact, do not retry |

These seven integers are the entire public contract — see [`forgelm/cli/_exit_codes.py`](https://github.com/HodeTech/ForgeLM/blob/main/forgelm/cli/_exit_codes.py) for the canonical definition. Any other non-zero value (including signal-derived 128+N codes) is clamped to `EXIT_TRAINING_ERROR` (2) before the process exits.

**Reading `verify-*` exit codes: 1 vs. 6.** For the four `verify-*` subcommands specifically, `EXIT_CONFIG_ERROR` (1) and `EXIT_INTEGRITY_FAILURE` (6) split on one question: did the verifier get far enough to compare anything? A missing file, a malformed manifest, or a magic-header mismatch (the file isn't a GGUF at all) means the verifier never compared — exit 1. A recomputed hash, chain link, or manifest entry that disagrees with what was recorded means the verifier compared and the artefact failed — exit 6. Two cases are deliberately on the 1 side even though they look tamper-adjacent: a GGUF magic-header mismatch (file-type verdict, not a tamper verdict) and a `verify-integrity` manifest entry whose path escapes the model directory (the verifier refuses to hash an out-of-tree path before reading anything, so nothing was compared). See each verifier's own exit-code table (linked from [CLI Reference](#/reference/cli)) for the full per-code breakdown.

## Mapping to CI patterns

### GitHub Actions

```yaml
- name: Train
  id: train
  run: forgelm --config configs/run.yaml
  continue-on-error: true

- name: Block on regression
  if: steps.train.outcome == 'failure' && steps.train.conclusion == 'failure'
  run: |
    if [ "${{ steps.train.outputs.exit-code }}" = "3" ]; then
      echo "::error::Regression detected — see audit log"
      exit 1
    fi
```

For most pipelines, the simpler pattern is fine:

```yaml
- name: Train
  run: forgelm --config configs/run.yaml
  # Any non-zero exit fails the step. The artifact upload step still runs (if: always()).
```

### GitLab CI

```yaml
train:
  script:
    - forgelm --config configs/run.yaml
  allow_failure:
    exit_codes: [4]                    # exit 4 (waiting for approval) doesn't fail CI
```

### Jenkins

```groovy
stage('Train') {
  steps {
    script {
      def status = sh(script: 'forgelm --config configs/run.yaml', returnStatus: true)
      if (status == 4) {
        currentBuild.result = 'UNSTABLE'   // hold for approval
      } else if (status != 0) {
        error "Training failed with exit code ${status}"
      }
    }
  }
}
```

## When to use each exit code

| Situation | What ForgeLM exits with |
|---|---|
| YAML has typo (e.g. `learnng_rate`) | 1 |
| `${HF_TOKEN}` set in YAML but env var missing | 1 |
| `--config` points to non-existent file | 1 |
| Final loss is NaN / OOM / I/O failure mid-training | 2 |
| `forgelm verify-audit` chain break or HMAC mismatch | 6 (the log was read and the chain doesn't verify — an option error, e.g. `--require-hmac` without a secret, or a missing log file, stays 1; see the in-manual [Verify Audit](#/compliance/verify-audit) page) |
| `forgelm verify-audit` on a log that exists but holds **zero entries** | 6 if a genesis manifest pins a first entry (truncated to empty — a comparison ran and failed); 1 if no manifest exists (no baseline, so nothing could be compared). Never 0 — an empty log is never a valid fresh-run state |
| `forgelm verify-gguf` / `verify-annex-iv` / `verify-integrity` — artefact read, hash/manifest disagrees | 6 |
| `forgelm verify-*` — path missing, unreadable, or malformed input | 1 |
| DPO run, Llama Guard S5 regressed beyond tolerance | 3 with `evaluation.auto_revert: true`; 0 (recorded in JSON gate blocks) with the shipped default `false` |
| Benchmark hellaswag dropped below floor | 3 with `evaluation.auto_revert: true`; 0 (recorded in JSON gate blocks) with the shipped default `false` |
| `evaluation.require_human_approval: true` and no approval signed | 4 |
| User Ctrl+C (signal-derived 128+N) | 2 (clamped) |

## Programmatic determination

The exit code itself is the contract — read it via `$?` (POSIX shells), `%ERRORLEVEL%` (cmd), `$LASTEXITCODE` (PowerShell), or the equivalent in your CI runner's expression language (e.g. `steps.<id>.outputs.exit-code` in GitHub Actions, `returnStatus: true` in Jenkins). For richer postmortem context (regressed categories, restored checkpoint path, etc.), parse the structured `audit_log.jsonl` event written under the run's output directory rather than relying on a sidecar.

## What "exit 0" actually guarantees

A run that exits 0 has:
- Validated config without errors.
- Loaded the model and dataset.
- Completed all configured training steps.
- Written the model card.
- Written the Annex IV bundle (if configured).
- Written manifest.json with SHA-256 over all artifacts.
- Optionally: written GGUF, deployment config.
- Closed the audit log with `pipeline.completed` (canonical event name).

**Gates and exit 0.** Whether a *passed* benchmark/safety/judge gate is part of the exit-0 guarantee depends on `evaluation.auto_revert`:

- With `evaluation.auto_revert: true` (the EU AI Act high-risk default), a failed gate auto-reverts the model and exits **3** — so exit 0 *does* mean every configured gate passed.
- With the shipped default `evaluation.auto_revert: false`, a failed gate is **recorded** (the `benchmark` / `safety` / `judge` block in the JSON output carries `*_passed: false`) but the model is still promoted and the run exits **0**. Read those JSON blocks; do not infer gate success from exit 0 alone.

There is no "partial success" exit code by design — turn on `auto_revert` if you want a failing gate to change the exit code.

## Compatibility guarantee

Exit codes 0-6 are stable across versions. New codes may be added (7, 8, ...) but existing ones won't change semantics. CI pipelines pinned to the contract above will continue working across ForgeLM upgrades.

`EXIT_INTEGRITY_FAILURE` (6) is additive, not a semantics change: it narrows a subset of cases that previously exited 1 on the four `verify-*` subcommands only. A pipeline that asserted `exit code == 1` to catch a `verify-*` integrity failure needs updating to check `== 6` as well; a pipeline that only branches on `!= 0`, or that runs `verify-*` under `set -e`, is unaffected — both 1 and 6 remain non-zero and still fail the step.

## See also

- [CI/CD Pipelines](#/operations/cicd) — patterns that use this contract.
- [CLI Reference](#/reference/cli) — every command that emits these codes.
- [Auto-Revert](#/evaluation/auto-revert) — produces exit 3.
- [Human Oversight](#/compliance/human-oversight) — produces exit 4.
