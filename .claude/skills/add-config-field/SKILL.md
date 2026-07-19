---
name: add-config-field
description: Use this skill when adding a new YAML configuration field to ForgeLM. Handles Pydantic model update, cross-field validation, config_template.yaml sync, bilingual docs, and tests. Triggered by requests like "add a new config option for X", "expose Y as a YAML field", "make Z configurable".
---

# Skill: Add a New Config Field

ForgeLM is strictly config-driven. Every new runtime behaviour becomes a YAML field passing through Pydantic validation to the consumer. This skill walks through all the places you must touch so nothing drifts.

## When to use

- User wants a new YAML option (e.g., `training.new_flag: true`)
- A feature is currently hardcoded and needs to be exposed
- A new optional dependency needs its own config section

Do **not** use for:
- Changing an existing field's default (that's a different, more risky change)
- Internal refactors that don't affect user-visible YAML

## Required reading before acting

1. [docs/standards/architecture.md](../../../docs/standards/architecture.md) — config flow principle
2. [docs/standards/coding.md](../../../docs/standards/coding.md) — Pydantic conventions
3. [forgelm/config.py](../../../forgelm/config.py) — existing patterns to match

## Steps

### 1. Pick the right config class

Look at [forgelm/config.py](../../../forgelm/config.py). Choose:

- `ModelConfig` — for model-related fields (name, quantization, MoE)
- `LoraConfigModel` — for LoRA/DoRA/PiSSA/rsLoRA parameters
- `TrainingConfig` — for training hyperparameters, trainer types, algorithmic flags
- `DataConfig` — for dataset paths, format, preprocessing
- `EvaluationConfig` / `SafetyConfig` / `JudgeConfig` — for eval pipeline
- `ComplianceConfig` — for EU AI Act artifacts
- `WebhookConfig`, `TrackingConfig`, etc. — for integrations
- `ForgeConfig` (root) — only if genuinely cross-cutting

If none fit, the field may belong to a **new** config class — see architecture.md §1.

### 2. Add the field

```python
# forgelm/config.py
class TrainingConfig(BaseModel):
    ...
    # existing fields

    new_flag: Optional[bool] = None
    """If set, enable the X behaviour. Default: inherit from model's default."""
```

Rules:
- Use `Optional[T] = None` for truly optional fields; `T = default_value` for always-set fields with safe defaults.
- Use `Literal["a", "b"]` for enums, not `str`.
- Field order: existing fields first, new field at a logical group boundary.
- One-line docstring directly below the field (Pydantic doesn't use these, but humans and docs do).

### 3. Validation

If the field has invariants, add `field_validator` or `model_validator`:

```python
@field_validator("new_flag")
@classmethod
def _validate_new_flag(cls, v: Optional[bool], info) -> Optional[bool]:
    if v and info.data.get("trainer_type") != "grpo":
        raise ValueError("new_flag is only valid for trainer_type='grpo'")
    return v
```

Error messages follow [error-handling.md](../../../docs/standards/error-handling.md) — specific, actionable.

### 4. Wire it to the consumer

Find the module that will read the field:

- Training-loop flags → [forgelm/trainer.py](../../../forgelm/trainer.py)
- Model-loading flags → [forgelm/model.py](../../../forgelm/model.py)
- Data flags → [forgelm/data.py](../../../forgelm/data.py)
- Safety flags → [forgelm/safety/_orchestrator.py](../../../forgelm/safety/_orchestrator.py)

Access via `config.training.new_flag` — never import from environment or global state.

### 5. Update `config_template.yaml`

The repo ships [config_template.yaml](../../../config_template.yaml) as the canonical example. Add your field with a comment:

```yaml
training:
  trainer_type: sft
  ...
  # If set, enables X. See docs/reference/configuration.md#new_flag
  new_flag: false
```

### 6. Document the field

Both mirrors:

- [docs/reference/configuration.md](../../../docs/reference/configuration.md) — add to the appropriate section with an example
- [docs/reference/configuration-tr.md](../../../docs/reference/configuration-tr.md) — matching Turkish section

Follow [localization.md](../../../docs/standards/localization.md) for the TR mirror.

### 7. Write a test

Create or extend [tests/test_config.py](../../../tests/test_config.py):

```python
def test_new_flag_defaults_to_none():
    cfg = ForgeConfig.model_validate(minimal_config())
    assert cfg.training.new_flag is None

def test_new_flag_requires_grpo_trainer():
    with pytest.raises(ValidationError, match="trainer_type='grpo'"):
        ForgeConfig.model_validate(
            minimal_config(training={"new_flag": True, "trainer_type": "sft"})
        )
```

At least one happy-path + one error-path test.

### 8. Update CHANGELOG

In [CHANGELOG.md](../../../CHANGELOG.md), under `[Unreleased]` / `### Added`:

```
- **New config field**: `training.new_flag` — enables X for GRPO trainer. See docs/reference/configuration.md#new_flag.
```

## Verification before PR

```bash
pytest tests/test_config.py -v
ruff check forgelm/config.py
forgelm --config config_template.yaml --dry-run
```

All three must pass.

## Pitfalls to avoid

- **Adding `new_flag` without validation.** Pydantic accepts any value of the declared type — if there are interactions with other fields, *you* must validate them.
- **Skipping the TR mirror.** The PR gets rejected. Same change, same PR.
- **Breaking backward compatibility with a default change.** If users have `training:` blocks that worked before and would fail now, you need a major bump. Consider adding as opt-in instead.
- **Forgetting `config_template.yaml`.** The CI dry-run uses this; if your field is required, the template must include it.
- **Logging the field value.** If it could contain secrets (tokens, paths), sanitize per [logging-observability.md](../../../docs/standards/logging-observability.md).

## Related skills

- `add-trainer-feature` — if the field controls end-to-end behaviour, not just a knob
- `sync-bilingual-docs` — run after step 6 to verify TR/EN parity
