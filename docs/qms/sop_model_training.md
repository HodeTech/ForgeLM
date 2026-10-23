# SOP: Model Training and Fine-Tuning

> Standard Operating Procedure — [YOUR ORGANIZATION]
> EU AI Act Reference: Article 17(1)(b)(c)(d)

## 1. Purpose

Define the standard procedure for fine-tuning language models, ensuring quality, safety, and compliance at every step.

## 2. Scope

Applies to all LLM fine-tuning activities using ForgeLM or equivalent tools.

## 3. Roles

| Role | Responsibility |
|------|---------------|
| **ML Engineer** | Prepares config, data, executes training |
| **ML Lead / Reviewer** | Reviews config and evaluation results, approves deployment |
| **Data Steward** | Validates data quality and governance compliance |
| **AI Officer** | Final approval for high-risk models |

## 4. Procedure

### 4.1 Pre-Training

- [ ] Define intended purpose and document in config (`compliance.intended_purpose`)
- [ ] Complete risk assessment (`risk_assessment:` section in YAML)
- [ ] Validate dataset quality (run data governance report)
- [ ] Review and approve training config (PR review by ML Lead)
- [ ] Dry-run validation: `forgelm --config job.yaml --dry-run`

### 4.2 Training Execution

- [ ] Run training: `forgelm --config job.yaml --output-format json`
- [ ] Monitor via webhook notifications or TensorBoard
- [ ] Training produces automated artifacts:
  - `audit_log.jsonl` — event trail
  - `compliance_report.json` — full audit record
  - `data_provenance.json` — dataset fingerprints

### 4.3 Post-Training Evaluation

Automated by ForgeLM:
- [ ] Loss-based evaluation (eval_loss vs baseline)
- [ ] Benchmark evaluation (lm-eval-harness tasks)
- [ ] Safety evaluation (Llama Guard classifier)
- [ ] LLM-as-Judge quality scoring

Manual:
- [ ] ML Lead reviews evaluation results
- [ ] If `require_human_approval: true`, ML Lead reviews `checkpoints/compliance/` and approves deployment
- [ ] Spot-check model outputs on 10+ representative prompts

### 4.4 Deployment Approval

- [ ] For **minimal-risk**: ML Lead approval sufficient
- [ ] For **limited-risk**: ML Lead + AI Officer approval
- [ ] For **high-risk**: ML Lead + AI Officer + Legal/Compliance review
- [ ] Deployer instructions (`deployer_instructions.md`) shared with deployment team
- [ ] Model integrity verified: `model_integrity.json` checksums match

### 4.5 Record Retention

- Compliance artifacts retained for the period your retention policy sets (for example, EU AI Act Article 18 asks providers of high-risk systems to keep technical documentation, quality-management documentation and the EU declaration of conformity for 10 years after the system is placed on the market or put into service; check what applies to you)
- Evidence bundle: `forgelm --config job.yaml --compliance-export ./archive/`
- Store in immutable/append-only storage if available

## 5. Exceptions

Any deviation from this SOP must be documented and approved by the AI Officer.

## 6. Review

This SOP is reviewed annually or when significant changes occur in the training pipeline.

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | [DATE] | [AUTHOR] | Initial version |
