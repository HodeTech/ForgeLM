---
name: add-trainer-feature
description: Use this skill when adding a substantial end-to-end feature to ForgeLM's training pipeline — a new alignment method, a new evaluation gate, a new quantization backend, a new distributed scheme. Differs from add-config-field by touching multiple modules and requiring integration tests. Triggered by requests like "add support for algorithm X", "integrate library Y for training", "implement feature from roadmap Phase N".
---

# Skill: Add a Trainer-Level Feature

Features that span config + model + trainer + tests + docs. Most Phase 10-13 tasks fit this pattern.

## When to use

- New alignment method (like adding a 7th trainer type)
- New evaluation pipeline (new safety model, new benchmark suite)
- New PEFT method
- New backend (a future alternative to unsloth/transformers)
- New compliance artifact

Do **not** use for:
- Bug fixes (scope too small)
- Single config field additions → use `add-config-field`
- Documentation-only changes

## Required reading before acting

1. [docs/standards/architecture.md](../../../docs/standards/architecture.md) — module boundaries
2. [docs/standards/testing.md](../../../docs/standards/testing.md) — what tests CI demands
3. The **phase file** describing the feature (e.g., [docs/roadmap/completed-phases.md](../../../docs/roadmap/completed-phases.md))
4. Existing similar feature — read one similar trainer/module end-to-end before writing yours

## The scaffold

Every trainer-level feature touches roughly the same list of files. Tick them as you go:

### Code

- [ ] **`forgelm/config.py`** — new `BaseModel` subclass OR new fields on existing config
- [ ] **`forgelm/<new_or_existing>.py`** — the actual logic
- [ ] **`forgelm/trainer.py`** — wiring: config → module dispatch
- [ ] **`forgelm/cli/_parser.py`** — if new CLI flag needed (Phase 15 split the monolithic `forgelm/cli.py` into the `forgelm/cli/` package; flag definitions live in `_parser.py`, and any new subcommand wires under `forgelm/cli/subcommands/<name>.py` plus the dispatch table in `forgelm/cli/_dispatch.py`)
- [ ] **`forgelm/results.py`** — if new output fields needed
- [ ] **`forgelm/compliance.py`** — if this feature affects audit artifacts
- [ ] **`forgelm/webhook.py`** — if this feature adds a lifecycle event

### Dependencies

- [ ] **`pyproject.toml`** — new optional extra (e.g., `forgelm[new_feature]`) if heavy deps required
- [ ] **Import pattern** — follow the `try: import X except ImportError: raise with hint` pattern (see [architecture.md §3](../../../docs/standards/architecture.md#3-optional-dependencies-are-extras-never-silent-imports))

### Tests

- [ ] **`tests/test_<feature>.py`** — unit tests for the new module
- [ ] **`tests/test_config.py`** — validation tests for new config
- [ ] **`tests/test_integration.py`** — dry-run that exercises the feature
- [ ] **`tests/conftest.py`** — new fixture if reusable across tests

### Config

- [ ] **`config_template.yaml`** — example usage

### Docs

- [ ] **`docs/reference/configuration.md`** + **`-tr.md`** — YAML field reference
- [ ] **`docs/reference/usage.md`** + **`-tr.md`** — if CLI changed
- [ ] **`docs/guides/<topic>.md`** — new tutorial if feature is user-visible and non-obvious
- [ ] **`docs/roadmap/phase-N-*.md`** — tick off the completed task
- [ ] **`CHANGELOG.md`** — under `[Unreleased]` / `### Added`

## Worked example: adding a new trainer type

Say the task is "add IPO (Identity Preference Optimization) trainer." Here's the shape:

### 1. Config

```python
# forgelm/config.py
class TrainingConfig(BaseModel):
    trainer_type: Literal["sft", "dpo", "simpo", "kto", "orpo", "grpo", "ipo"] = "sft"
    ipo_beta: float = 0.1  # IPO-specific hyperparameter
```

### 2. Trainer wiring

```python
# forgelm/trainer.py
from trl import IPOTrainer  # optional import

TRAINER_REGISTRY = {
    "sft": _run_sft,
    ...
    "ipo": _run_ipo,
}

def _run_ipo(config, model, tokenizer, dataset):
    trainer = IPOTrainer(
        model=model,
        args=_build_training_args(config),
        train_dataset=dataset,
        beta=config.training.ipo_beta,
        ...
    )
    trainer.train()
    return trainer
```

### 3. Data format detection

```python
# forgelm/data.py — extend _detect_dataset_format
if "chosen" in first_row and "rejected" in first_row:
    return "preference_pairs"  # DPO, SimPO, IPO all use this
```

### 4. Tests

```python
# tests/test_alignment.py
class TestIPO:
    def test_config_accepts_ipo(self):
        cfg = ForgeConfig.model_validate(
            minimal_config(training={"trainer_type": "ipo", "ipo_beta": 0.1})
        )
        assert cfg.training.trainer_type == "ipo"

    def test_dry_run_with_ipo(self, tmp_path, monkeypatch):
        # mock dataset, run dry-run, assert trainer selected
        ...
```

### 5. Docs

- `configuration.md`: add `trainer_type: ipo` to the enum table + `ipo_beta` row
- `guides/alignment.md`: add "When to use IPO" section with 2-3 sentences
- `CHANGELOG.md`: `### Added — IPO trainer type (TRL-backed). See docs/guides/alignment.md.`

### 6. Phase tick

Open `docs/roadmap/phase-N-*.md` where this task sits, change `[ ]` to `[x]`.

## Integration checklist

Before opening the PR, run **all** of these:

```bash
# Lint + format
ruff check . && ruff format --check .

# Unit tests
pytest tests/ -v

# Full pipeline smoke
python3 -m forgelm --config config_template.yaml --dry-run
python3 -m forgelm --wizard  # if wizard is aware of new feature

# Optional extras install cleanly?
pip install -e '.[new_feature]'
```

## Pitfalls to avoid

- **Fat trainer.py.** If the new trainer logic is >100 lines, extract to a new `forgelm/<name>.py` module and have trainer.py just dispatch.
- **Global state.** Tempting for trainer-level features ("let me cache this tokenizer"). Don't. Thread state through the function signature.
- **Skipping the optional extra pattern.** A single missing `try: import X` with a helpful message is the difference between a kind error and an ugly traceback.
- **Not testing auto-revert interaction.** If the feature can fail an evaluation gate, test that auto-revert correctly fires.
- **Not updating the wizard.** If the feature is user-visible, `python3 -m forgelm --wizard` should surface it — collectors live in `forgelm/wizard/_collectors.py` and defaults in `forgelm/wizard/_defaults.json` (regenerate with `tools/generate_wizard_defaults.py`; `tools/check_wizard_defaults_sync.py` gates the pair).

## Related skills

- `add-config-field` — when scope is smaller
- `add-test` — focused test writing
- `sync-bilingual-docs` — after touching `docs/reference/*`
