---
title: Human Approval Gate (Deployer)
description: Deployer-facing companion to Human Oversight — the `forgelm approve` / `reject` / `approvals` CLI gate for Article 14.
---

# Human Approval Gate (Deployer)

This page is the deployer-facing companion to [Human Oversight](#/compliance/human-oversight). The shorter Human Oversight page is the operator quick-reference; this page collects the wiring details — CI integration, segregation of duties, audit-evidence verification — that a deployer needs when standing up the gate end-to-end.

For the full walkthrough, see the [human approval gate deployer guide on GitHub](https://github.com/HodeTech/ForgeLM/blob/main/docs/guides/human_approval_gate.md). For per-flag references, see [`approve_subcommand.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/approve_subcommand.md) and [`approvals_subcommand.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/approvals_subcommand.md) on GitHub.

## When the gate fires

```yaml
evaluation:
  require_human_approval: true
```

> **Phantom-key note.** Earlier drafts named this `compliance.human_approval`. That key never shipped — `ComplianceMetadataConfig` is `extra="forbid"` and rejects it. The canonical activation key lives on `EvaluationConfig`. See [Human Oversight](#/compliance/human-oversight) for the full phantom-key list.

With that flag, every run consuming this config pauses **after** evaluation succeeds and **before** `final_model.staging/` is promoted to `final_model/`. The trainer:

- Writes `final_model.staging.<run_id>/` to disk.
- Appends `human_approval.required` to `audit_log.jsonl`.
- Fires `notify_awaiting_approval` on the configured webhook.
- Exits with code 4 (`EXIT_AWAITING_APPROVAL`).

A failing eval still exits 3 (`EXIT_EVAL_FAILURE`) and never reaches the gate.

> **Note:** When the operator generates the strict-tier config via `forgelm --wizard` and cancels (Ctrl-C, non-tty refusal, decline-to-save), the wizard exits 5 (`EXIT_WIZARD_CANCELLED`) — see [Exit Codes](#/reference/exit-codes) — and never reaches the trainer pipeline. CI pipelines that only treat exit 0 as "wizard finished cleanly" should branch separately on 5 to surface a "no config produced" message rather than treating it as a generic failure.

## CI wiring

Exit code 4 is a **pause**, not a **failure**. CI must be told this explicitly:

```yaml
# .github/workflows/train.yml (excerpt)
env:
  FORGELM_OPERATOR: gha:${{ github.repository }}:${{ github.workflow }}:run-${{ github.run_id }}
  FORGELM_AUDIT_SECRET: ${{ secrets.FORGELM_AUDIT_SECRET }}
steps:
  - id: train
    run: forgelm --config run.yaml
    continue-on-error: true     # exit 4 must not fail the build
  - if: ${{ steps.train.outcome == 'success' || steps.train.exit_code == 4 }}
    run: echo "::notice::Run paused awaiting human approval"
```

A separate gate-discovery job (or scheduled cron) calls `forgelm approvals --pending` to surface the queue:

```bash
pending=$(forgelm approvals --pending --output-dir ./outputs --output-format json | jq '.count')
if [ "$pending" -gt 0 ]; then
    echo "::warning::$pending approval(s) pending"
fi
```

## The reviewer's CLI surface

```bash
forgelm approvals --pending --output-dir <dir>           # list runs awaiting decision
forgelm approvals --show <run_id> --output-dir <dir>     # full chain + staging contents
forgelm approve  <run_id> --output-dir <dir> --comment "..."  # promote staging → final_model
forgelm reject   <run_id> --output-dir <dir> --comment "..."  # discard the staged model
```

`approve` and `reject` take a **positional `run_id`** (NOT `--run-id`). The `--comment` text is recorded in the chain — auditors will read it. `--output-dir` points at the training output directory containing `audit_log.jsonl` and `final_model.staging/`.

## Segregation of duties (Article 14 + ISO A.5.3 + SOC 2 CC1.5)

The approver's `FORGELM_OPERATOR` MUST differ from the trainer's. ForgeLM does not enforce this — it is a deployer-side IdP control — but the audit chain records both, so a violation is detectable post-hoc with the canonical `jq -rs` cookbook in the [`access_control.md` QMS template on GitHub](https://github.com/HodeTech/ForgeLM/blob/main/docs/qms/access_control.md) §6:

```bash
jq -rs '
    (map(select(.event == "training.started"))) as $trainers |
    map(select(.event == "human_approval.granted"))[] |
    . as $a |
    $trainers[] |
    select(.run_id == $a.run_id and .operator == $a.operator) |
    [.run_id, .operator] | @tsv
' ./outputs/audit_log.jsonl
```

Any rows printed are violations. A clean run prints nothing.

Pattern: CI runners use a machine-readable identity (`gha:Acme/pipelines:training:run-42`); human reviewers use their own identity (e-mail or LDAP user). Bake `FORGELM_OPERATOR` into the reviewer's shell profile or your IdP's environment-injection layer; do not rely on a manual `export`.

## Audit events emitted

Three events describe the gate's full lifecycle (see the [Audit Event Catalog on GitHub](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/audit_event_catalog.md)):

| Event | Emitted by | When |
|---|---|---|
| `human_approval.required` | trainer | Eval succeeded; gate paused. |
| `human_approval.granted` | `forgelm approve` | Reviewer approved; staging promoted to `final_model/`. |
| `human_approval.rejected` | `forgelm reject` | Reviewer rejected; staging preserved for forensic review. |

Each line carries `prev_hash` (SHA-256 of the previous line) and `_hmac` (when `FORGELM_AUDIT_SECRET` is set). `forgelm verify-audit ./outputs/audit_log.jsonl --require-hmac` validates the full chain.

## Verifying the gate's evidence

Auditors and self-reviewers walk the gate in three steps:

```bash
# 1. Chain integrity (HMAC-strict).
forgelm verify-audit ./outputs/audit_log.jsonl --require-hmac

# 2. Approval pairing — every required event has a matching terminal decision.
jq -rs '
    (map(select(.event == "human_approval.required")) | map(.run_id)) as $req |
    (map(select(.event | startswith("human_approval.")) | select(.event != "human_approval.required")) | map(.run_id)) as $dec |
    ($req - $dec) as $unmatched |
    if ($unmatched | length) == 0 then "OK: every required event has a decision."
    else "PENDING:\n" + ($unmatched | join("\n")) end
' ./outputs/audit_log.jsonl

# 3. Segregation of duties (the §6 cookbook above).
```

## Exit codes

| Code | Subcommand | Meaning |
|---|---|---|
| 0 | all | Decision recorded / listing succeeded / queue empty. |
| 1 | `approve` / `reject` | Audit log corrupted, no matching `required` event, prior terminal decision exists, `staging_path` escapes `output_dir`, `final_model/` already exists, staging missing, operator identity unresolvable. |
| 1 | `approvals` | Audit log corrupted, neither `--pending` nor `--show` supplied, unknown `run_id` on `--show`. |
| 2 | all | Runtime error (I/O, atomic-rename failure). |
| 4 | trainer | Pause signal that brings the operator here in the first place — not part of the approve/reject/approvals surface. |

## Common pitfalls

:::warn
**Treating exit 4 as a failure.** It is a controlled pause. CI must use `continue-on-error: true` (or your runner's equivalent) on the training step.
:::

:::warn
**Using `--run-id` for `approve` / `reject`.** Both subcommands take a **positional** `run_id`. `--run-id` is the trainer-side flag (and the `forgelm purge --run-id` flag); approve / reject did not adopt it.
:::

:::warn
**Sharing `FORGELM_OPERATOR` between trainer and approver.** The audit chain records both, so the violation is detectable, but it is still a violation. Run the §6 cookbook against every audit log before an audit observation period.
:::

:::tip
**Use `forgelm approvals --pending --output-format json` as the CI gate.** Don't gate the deploy step on the training step's exit code; gate it on the empty-queue check. That way a separate reviewer machine handles the decision without the CI runner needing reviewer credentials.
:::

## See also

- [Human Oversight](#/compliance/human-oversight) — operator quick-reference companion.
- [Audit Log](#/compliance/audit-log) — where the `human_approval.*` events are recorded.
- [Human approval gate deployer guide](https://github.com/HodeTech/ForgeLM/blob/main/docs/guides/human_approval_gate.md) — full deployer-flow walkthrough (GitHub source).
- [`approve_subcommand.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/approve_subcommand.md) — `approve` / `reject` per-flag reference (GitHub source).
- [`approvals_subcommand.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/approvals_subcommand.md) — `approvals` per-flag reference (GitHub source).
- [`access_control.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/qms/access_control.md) §6 — canonical segregation-of-duties cookbook (GitHub QMS template).
