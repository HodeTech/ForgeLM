# SOP: Change Management for AI Models

> Standard Operating Procedure — [YOUR ORGANIZATION]
> EU AI Act Reference: Article 17(1)(b)(c)
> ISO 27001:2022: A.5.36, A.8.9, A.8.32
> SOC 2: CC8.1, CC3.4, CC5.3

## 1. Purpose

Define the procedure for managing changes to fine-tuned models, training configurations, and evaluation criteria.

## 2. Change Categories

| Category | Examples | Approval Required |
|----------|---------|------------------|
| **Major** | New base model, new trainer type, risk category change | AI Officer + ML Lead |
| **Minor** | Hyperparameter tuning, LoRA rank change, dataset update | ML Lead |
| **Patch** | Bug fix, config formatting, documentation | ML Engineer (self-approve) |

## 3. Change Procedure

### 3.1 Propose

1. Create a branch: `git checkout -b change/description`
2. Modify the training config (YAML)
3. Document the change rationale in the PR description
4. Run dry-run: `forgelm --config new_config.yaml --dry-run`

### 3.2 Review

1. Open a Pull Request against `main`
2. PR must include:
   - [ ] Config diff showing what changed
   - [ ] Rationale for the change
   - [ ] Expected impact on model quality/safety
   - [ ] For **Major** changes: updated risk assessment
3. Reviewer checks:
   - [ ] Config validates (`--dry-run` passes)
   - [ ] Safety evaluation enabled (for high-risk models)
   - [ ] No secrets or tokens in config

### 3.3 Execute

1. Merge PR (requires approval per branch protection rules)
2. CI/CD pipeline triggers training: `forgelm --config job.yaml`
3. ForgeLM auto-generates compliance artifacts for the new run
4. New `audit_log.jsonl` entries trace the change

### 3.4 Validate

1. Compare new model metrics against previous version
2. Review benchmark scores, safety scores, judge scores
3. If `require_human_approval: true`: explicit approval before deployment

### 3.5 Rollback

If the new model is worse:
- ForgeLM `auto_revert` handles automatic rollback during training
- For post-deployment rollback:
  1. Redeploy previous model version
  2. Document rollback in incident log
  3. Investigate root cause

## 4. CI gates as the formal change-control mechanism

Wave 4 / Faz 23 expansion: this section formalises which CI gates
constitute the deployer-facing change-control evidence. ISO A.8.32
demands "changes to information processing facilities and systems
shall be subject to change management procedures"; the table below
documents the procedure for ForgeLM-instrumented deployers.

| Gate | Tool | Failure mode | ISO control |
|---|---|---|---|
| Lint clean | `ruff check .` + `ruff format --check .` | Style / syntax issues | A.8.28 |
| Pydantic field-description guard | `tools/check_field_descriptions.py --strict forgelm/config.py` | Operator-facing config drift | A.5.36 |
| HTTP discipline | `grep`-based regex guard in `ci.yml` | Undisciplined `requests.*` / `urllib.*` / `httpx.*` call | A.8.20 |
| Unit + integration tests | `pytest -q` | Regression on documented contract | A.8.29 |
| Coverage floor | `--cov-fail-under=40` (`pyproject.toml`) | Uncovered new code | A.8.29 |
| CLI dry-run | `forgelm --config config_template.yaml --dry-run` | Config schema break | A.8.9 |
| Site-as-tested-surface | `tools/check_site_claims.py --strict` | Marketing claim drift from code | A.5.31 |
| Bilingual H2/H3/H4 parity | `tools/check_bilingual_parity.py --strict` | EN/TR docs structural drift | A.5.36 |
| Bandit static-security (Wave 4) | `bandit -c pyproject.toml -r forgelm/` | High-severity finding | A.8.26, A.8.28 |
| pip-audit nightly (Wave 4) | `pip-audit --format json` | High-severity transitive CVE | A.8.8, A.8.21 |
| SBOM determinism (Wave 4) | `pytest tests/test_supply_chain_security.py` | SBOM content drift | A.5.21, A.8.8 |

A change cannot merge to `main` (or `development` for Wave-style
flows) until every gate passes. The PR review template (`.github/
PULL_REQUEST_TEMPLATE.md`) MUST require an explicit checkbox
confirming each gate green.

### 4.1 Approval gate as Change Advisory Board (CAB) substitute

For changes that produce a new training run (vs. configuration-only
changes), the **Article 14 staging gate** is the deployer's
in-pipeline CAB:

1. CI training job lands the model in `<output_dir>/final_model.staging.<run_id>/`.
2. `human_approval.required` audit event fires.
3. A reviewer (NOT the trainer) runs `forgelm approve <run_id> --output-dir <output_dir>`
   (note: `run_id` is positional — `--run-id` is not a flag).
4. `human_approval.granted` audit event fires; model promotes to
   `<output_dir>/final_model/` via atomic rename.
5. If rejected, `human_approval.rejected` event fires; model stays
   in `final_model.staging.<run_id>/` for forensic review until
   retention expires (`retention.staging_ttl_days` in the config).

This gives a deployer SOC 2 CC8.1 evidence: every model promotion is
attributed, dual-controlled, and forensically recorded.

### 4.2 Configuration drift detection

`tools/check_field_descriptions.py --strict` (Phase 16) runs on every
CI build (`ci.yml`) and exits non-zero if any Pydantic field in
`forgelm/config.py` lacks a `description=` argument. The operator-facing
`docs/reference/configuration.md` (and its `-tr.md` mirror) are
maintained by hand and reviewed against the schema; `tools/check_bilingual_parity.py --strict`
enforces EN↔TR structural parity. There is no autogenerator — the guard
guarantees that every schema field carries authoritative description text,
and change review checks that the reference doc mirrors it.

This closes the "doc drift" failure mode where the schema evolves
but the operator-facing doc lags. ISO A.5.36 cites this as a
mandatory mechanism.

### 4.3 SBOM drift detection

`tools/generate_sbom.py` (Wave 2 era) produces a deterministic
CycloneDX 1.5 SBOM per (OS × Python-version) on every release tag
(`publish.yml`). Wave 4 adds a determinism contract test
(`tests/test_supply_chain_security.py::TestGenerateSbomDeterministic`).

Drift detection: a release's SBOM is reproducible from its `git tag`.
An auditor can re-emit it on demand and diff against the artefact
attached to the GitHub release.

## 5. Version Tracking

Every training run produces:
- `compliance_report.json` with ForgeLM version, config hash, timestamps
- `model_integrity.json` with artifact SHA-256 checksums
- `audit_log.jsonl` with full event history

Use these to trace any model back to its exact training configuration and data.

## 6. Review

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | [DATE] | [AUTHOR] | Initial version |
| 1.1 | 2026-05-05 | Wave 4 / Faz 23 | Added §4 CI-gates-as-change-control table (11 gates × ISO controls); §4.1 Article 14 approval gate as CAB substitute; §4.2 config-drift detection via `check_field_descriptions.py` + manual doc review; §4.3 SBOM drift detection via `generate_sbom.py` + determinism test |
| 1.2 | 2026-06-14 | Wave 1 / H7 | Corrected §4.2: the config-drift control is `check_field_descriptions.py --strict` + manual doc review + bilingual-parity guard; the previously cited `regenerate_config_doc.py` autogenerator never existed |
