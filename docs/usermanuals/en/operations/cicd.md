---
title: CI/CD Pipelines
description: Wire ForgeLM into GitHub Actions, GitLab CI, or Jenkins with predictable exit codes.
---

# CI/CD Pipelines

ForgeLM is designed to slot cleanly into a CI/CD pipeline step. Every command has a predictable exit code, every output is structured (JSON or JSONL), and every gate either passes or fails — no in-between. The same exit-code contract applies whether you launch a run from a terminal, a notebook, or an orchestrator.

## The exit-code contract

| Exit | Meaning | What CI should do |
|---|---|---|
| `0` | Success | Promote artifacts |
| `1` | Configuration error | Fail; fix YAML before retry |
| `2` | Training error | Fail; investigate crash / OOM / unhandled exception |
| `3` | Evaluation gate failed — with `auto_revert: true` the artefacts were deleted | Fail; investigate regression |
| `4` | Awaiting human approval | Hold pipeline; trigger reviewer notification |
| `5` | Wizard cancelled — no config was produced | Not an error; nothing to promote |
| `6` | Integrity failure — an artefact was read and its hash comparison disagreed | Fail loudly; page the artefact owner. Do **not** promote or retry blindly |

The public range is `0`–`6`. If your pipeline's `case` statement was written against an older 0–4 table, add branches for `5` and `6` — `6` in particular is the security-relevant one ("this artefact was modified after sign-off") and should never fall through to a generic default. See [Exit Codes](#/reference/exit-codes) for the full contract.

## GitHub Actions

A complete pipeline:

```yaml
# .github/workflows/train.yml
name: Train and evaluate

on:
  push:
    branches: [main]
    paths:
      - "configs/**"
      - "data/**.jsonl"
  workflow_dispatch:

jobs:
  audit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - uses: actions/setup-python@v6
        with: { python-version: "3.11" }
      - run: pip install 'forgelm[ingestion]'
      - name: Audit data
        run: forgelm audit data/train.jsonl --output-format json | jq -e '.verdict != "errors" and .pii_summary.severity != "high"'

  train:
    needs: audit
    runs-on: gpu-runner                    # self-hosted with CUDA
    steps:
      - uses: actions/checkout@v5
      - run: pip install forgelm
      - name: Validate config
        run: forgelm --config configs/run.yaml --dry-run
      - name: Fit-check VRAM
        run: forgelm --config configs/run.yaml --fit-check
      - name: Train
        run: forgelm --config configs/run.yaml
      - name: Upload artifacts
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: training-artifacts
          path: checkpoints/run/
```

Note the `if: always()` on artifact upload — even on failure, the audit log and partial artifacts are useful.

## GitLab CI

```yaml
# .gitlab-ci.yml
stages:
  - audit
  - train
  - deploy

audit:
  stage: audit
  image: python:3.11
  script:
    - pip install 'forgelm[ingestion]'
    - forgelm audit data/train.jsonl --output-format json | jq -e '.verdict != "errors" and .pii_summary.severity != "high"'

train:
  stage: train
  tags: [gpu]
  script:
    - pip install forgelm
    - forgelm --config configs/run.yaml --dry-run
    - forgelm --config configs/run.yaml --fit-check
    - forgelm --config configs/run.yaml
  artifacts:
    paths:
      - checkpoints/run/
    when: always

deploy:
  stage: deploy
  tags: [gpu]
  needs: [train]
  script:
    # Verify the audit chain before promoting (catches truncate / HMAC tamper).
    - forgelm verify-audit checkpoints/run/audit_log.jsonl --require-hmac
    # If `evaluation.require_human_approval` is on, the trainer exits 4 — promote here:
    #   forgelm approve <run_id> --output-dir checkpoints/run --comment "approved-by-CI"
    # Otherwise, ship `final_model/` with your existing deployment tooling.
    - ./scripts/promote-to-prod.sh checkpoints/run/final_model
  when: manual                          # gate manual promotion to prod
```

## Jenkins

```groovy
pipeline {
  agent any
  stages {
    stage('Audit') {
      steps {
        sh '''forgelm audit data/train.jsonl --output-format json | jq -e '.verdict != "errors" and .pii_summary.severity != "high"' '''
      }
    }
    stage('Train') {
      agent { label 'gpu' }
      steps {
        sh 'forgelm --config configs/run.yaml --dry-run'
        sh 'forgelm --config configs/run.yaml --fit-check'
        sh 'forgelm --config configs/run.yaml'
      }
      post {
        always {
          archiveArtifacts artifacts: 'checkpoints/run/**'
        }
      }
    }
  }
}
```

## JSON output mode

For programmatic parsing of run outputs, use `--output-format json`:

```shell
$ forgelm --config configs/run.yaml --output-format json | jq '.verdict'
"success"
```

Every event ForgeLM logs to stderr is also emitted as a JSON object on stdout, ready to pipe into your log aggregator or dashboard.

## Caching models

Model downloads are the slowest part of a fresh CI run. Cache them:

```yaml
# GitHub Actions
- uses: actions/cache@v4
  with:
    path: ~/.cache/huggingface
    key: hf-${{ runner.os }}-${{ hashFiles('configs/**.yaml') }}
```

For multi-runner setups, point the cache at shared storage (S3, NFS) so all runners share it.

## Concurrency control

Most projects run training serially, not in parallel — multiple concurrent jobs on the same GPU lead to OOM. Use concurrency:

```yaml
# GitHub Actions
concurrency:
  group: training-${{ github.ref }}
  cancel-in-progress: false              # don't cancel; queue
```

## Self-hosted GPU runners

For GitHub Actions with self-hosted GPUs:

1. Install the GitHub runner on the GPU host.
2. Tag it: `self-hosted, linux, x64, gpu`.
3. Reference in your job: `runs-on: [self-hosted, gpu]`.

ForgeLM ships a `Dockerfile.runner` reference for setting up the runner with CUDA, Python, and pre-installed extras.

## Common pitfalls

:::warn
**Suppressing exit codes.** `forgelm ... || true` defeats the entire purpose of the gate contract. If you genuinely need to keep going, branch on the exit code instead.
:::

:::warn
**Running audit and train on the same runner.** Audit is CPU-only; training needs a GPU. Run audit on a cheap runner first, then train on the GPU runner only if audit passes. Saves GPU time when data has bugs.
:::

:::warn
**No artifact upload on failure.** When a run fails, the audit log is the most valuable evidence. Always set `if: always()` (GitHub) / `when: always` (GitLab) on artifact uploads.
:::

:::tip
For overnight training runs, configure auto-revert + Slack webhook. You'll either wake up to a promoted model or a clear incident report — never a "dunno, broke at 3am" mystery.
:::

## See also

- [Exit Codes](#/reference/exit-codes) — the contract CI relies on.
- [Webhooks](#/operations/webhooks) — Slack/Teams notifications.
- [Auto-Revert](#/evaluation/auto-revert) — what triggers exit 3.
