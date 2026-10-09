---
name: add-test
description: Use this skill when writing new tests for ForgeLM. Applies conventions from docs/standards/testing.md — fixture factory patterns, mock boundaries, no-GPU-no-network discipline, coverage floor. Triggered by requests like "add a test for X", "cover function Y", "write a test that triggers error Z".
---

# Skill: Write a Test for ForgeLM

Tests in ForgeLM are pragmatic: fast, deterministic, laptop-runnable. This skill encodes the conventions so new tests fit in and CI stays green.

## When to use

- Covering a new function, class, or CLI flag
- Adding regression tests for a fixed bug
- Extending test_config.py with a new validation check

Do **not** use for:
- Actually running tests (just run `pytest tests/`)
- Performance benchmarks (separate concern — not in `tests/`)

## Required reading before writing

1. [docs/standards/testing.md](../../../docs/standards/testing.md) — full rules
2. [tests/conftest.py](../../../tests/conftest.py) — the `minimal_config` factory
3. **A similar existing test file** — e.g., if you're writing `test_new_feature.py`, read `test_alignment.py` first

## Decide: which test file?

| Type of code being tested | Test file |
|---|---|
| Pydantic config field or validator | `test_config.py` |
| A new alignment trainer (DPO-style) | `test_alignment.py` |
| A full pipeline scenario | `test_integration.py` |
| CLI argument parsing, exit codes | `test_cli.py` or `test_cli_subcommands.py` |
| Compliance artifact generation | `test_compliance.py` or `test_eu_ai_act.py` |
| Safety evaluation | `test_safety_advanced.py` |
| Webhook behaviour | `test_webhook.py` |
| Something that doesn't fit | New `test_<module>.py` matching the source module |

Prefer extending an existing file over creating a new one — keeps the test layout predictable.

## Template

```python
"""Tests for forgelm.<module> — <short scope description>."""
import pytest
from forgelm.<module> import <public_api>
from forgelm.config import ForgeConfig
from tests.conftest import minimal_config


class TestPublicFunction:
    """Group related tests in a class for readability."""

    def test_happy_path(self, tmp_path):
        result = <public_api>(...)
        assert result.status == "ok"
        assert result.field == expected_value

    def test_raises_on_invalid_input(self):
        with pytest.raises(ValueError, match="specific error keyword"):
            <public_api>(bad_input)

    @pytest.mark.parametrize("value,expected", [
        ("a", "result_a"),
        ("b", "result_b"),
    ])
    def test_parametrized(self, value, expected):
        assert <public_api>(value) == expected
```

## Fixtures — use, don't reinvent

```python
# Minimal valid config:
cfg_dict = minimal_config()

# With overrides:
cfg_dict = minimal_config(training={"trainer_type": "dpo", "dpo_beta": 0.2})

# Parsed into Pydantic:
cfg = ForgeConfig.model_validate(cfg_dict)
```

Don't build config dicts from scratch. Don't add a new top-level fixture to `conftest.py` unless it's reused across ≥3 test files.

## Mocking rules

From [testing.md](../../../docs/standards/testing.md):

**Always mock:**
- Network: `requests`, `huggingface_hub` downloads, OpenAI/Anthropic APIs
- GPU: `torch.cuda.is_available`, `torch.cuda.get_device_name`
- Expensive imports: `unsloth`, `bitsandbytes`, `deepspeed`, `lm_eval` when they cause import errors

**Never mock:**
- Pydantic validation (use real config objects)
- File I/O under `tmp_path`
- YAML parsing
- Internal `forgelm.*` modules

### Mocking patterns

```python
# Using pytest monkeypatch:
def test_no_gpu_path(monkeypatch):
    monkeypatch.setattr("torch.cuda.is_available", lambda: False)
    result = model.load(cfg)
    assert result.device == "cpu"

# Using mock.patch:
from unittest.mock import patch, MagicMock

# Patch the extracted helper, NOT `transformers.pipeline`.
# `forgelm/safety/_classifier.py` imports `pipeline` lazily inside the
# function, and `transformers` is a lazy module: `mock.patch("transformers.
# pipeline")` resolves only when the module happens to be import-cached, which
# makes the test order-sensitive across pytest sessions and, on a miss, runs
# the real loader — a multi-GB download inside a unit test. The repo hit this;
# see the docstring at `tests/test_safety_advanced.py::TestClassifierConversationFormat`.
def test_safety_eval_returns_score(monkeypatch):
    import forgelm.safety._classifier as classifier_mod

    monkeypatch.setattr(
        classifier_mod,
        "_load_safety_classifier",
        lambda *a, **kw: MagicMock(return_value=[{"label": "safe", "score": 0.98}]),
    )
    result = run_safety_evaluation(...)
    assert result.safety_score > 0.9


# When the seam under test IS the loader, stub the module instead of the
# attribute — this is what `tests/test_safety.py` does:
def test_revision_reaches_the_pipeline():
    import forgelm.safety._classifier as classifier_mod

    fake_transformers = MagicMock()
    fake_transformers.pipeline = _recording_pipeline()
    with patch.dict("sys.modules", {"transformers": fake_transformers}):
        classifier_mod._load_safety_classifier("acme/harm-classifier", None, SHA)

# Patching outbound HTTP:
from unittest.mock import patch

# `forgelm/webhook.py` does NOT call `requests.post` — every outbound request
# goes through the SSRF-guarded chokepoint `forgelm._http.safe_post`, imported
# at module load. Patch the name where it is *used*, not where it is defined:
# `forgelm.webhook.safe_post`, never `forgelm._http.safe_post`.
def test_webhook_failure_doesnt_raise(caplog):
    with patch("forgelm.webhook.safe_post") as mock_post:
        mock_post.side_effect = requests.RequestException("boom")
        notifier.send(...)  # must not raise
    assert "webhook delivery failed" in caplog.text.lower()
```

> **Mock at the name the code resolves at call time.** Both corrections above
> are the same mistake: a patch target that no longer intercepts anything, in
> a test that still passes — green, and measuring nothing. When a module moves
> or an import is rerouted through a chokepoint, every patch string aimed at
> the old path becomes a no-op.
>
> Three specific traps this repository has actually hit:
>
> 1. **Lazy third-party modules.** `transformers` resolves `pipeline` through
>    its own `__getattr__`, so `patch("transformers.pipeline")` works or does
>    not depending on import order. Patch a ForgeLM seam instead.
> 2. **Chokepoint reroutes.** `forgelm/webhook.py` imports `safe_post` at
>    module load, so the live name is `forgelm.webhook.safe_post`. Patching
>    `forgelm._http.safe_post` rebinds a name webhook.py no longer reads.
> 3. **Package facades.** After a `module.py` → `module/` split, patching a
>    name the `__init__` re-exports does not touch the reference the owning
>    submodule resolves. ForgeLM keeps private helpers **off** its facades
>    precisely so this raises `AttributeError` rather than passing silently —
>    see `forgelm/verify/__init__.py`. Target
>    `forgelm.verify._pipeline_evidence._read_capped_json`, not
>    `forgelm.verify._read_capped_json`.
>
> `mock.patch` raises `AttributeError` for a missing attribute, so a target
> that is merely *wrong* fails loudly. The dangerous ones are targets that
> still resolve — and `raising=False` / `create=True`, which suppress even the
> loud case. Never reach for either without a written reason.

## Assertions

Prefer specific over general:

| ❌ Weak | ✅ Specific |
|---|---|
| `assert result` | `assert result.status == "success"` |
| `assert len(items) > 0` | `assert len(items) == 3` |
| `assert "error" in output` | `assert "trainer_type must be one of" in output` |

`pytest.raises(match="...")` is better than just `pytest.raises` — catches regressions where the error type stays but the message drifts.

## Testing error paths

From [error-handling.md](../../../docs/standards/error-handling.md):

```python
def test_invalid_trainer_type_exit_code(tmp_path, monkeypatch):
    config_path = tmp_path / "bad.yaml"
    config_path.write_text("training:\n  trainer_type: 'nonexistent'\n...")
    # Use subprocess to get real exit code:
    result = subprocess.run(
        [sys.executable, "-m", "forgelm", "--config", str(config_path), "--dry-run"],
        capture_output=True, text=True
    )
    assert result.returncode == 1  # EXIT_CONFIG_ERROR
    assert "trainer_type" in result.stderr
```

Or in-process with `capsys`:

```python
def test_cli_config_error_exits_1(capsys):
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--config", "bad.yaml", "--dry-run"])
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "config" in captured.err.lower()
```

## Coverage

- Every new public function needs ≥1 test covering the success path.
- Every `raise` statement and every `sys.exit(!=0)` needs ≥1 test triggering it.
- `pragma: no cover` only for:
  - `if __name__ == "__main__":` blocks
  - `except ImportError:` fallbacks for optional deps
  - Documented platform-specific branches

Current coverage floor is **40%** (enforced via [`pyproject.toml`](../../../pyproject.toml) `[tool.pytest.ini_options].addopts → --cov-fail-under=40`). Treat it as a floor not a target — you're usually well above. If you ever quote the number elsewhere in docs or skills, source it from `pyproject.toml` to prevent drift.

## Before opening the PR

```bash
# Your new tests pass:
pytest tests/test_<your_file>.py -v

# No coverage regression:
pytest --cov=forgelm --cov-report=term-missing tests/

# Linter happy:
ruff check tests/test_<your_file>.py
ruff format --check tests/test_<your_file>.py
```

## Pitfalls

- **`pytest.skip("TODO")`** — tracks nothing. Use `pytest.mark.xfail(reason="...", strict=False)` with a linked issue.
- **Sleep-based waits** — `time.sleep(2)` makes tests flaky. Mock time instead.
- **Module-level state in tests** — `shared_state = []` at test-file scope causes cross-test pollution.
- **Testing the mock** — if your assertion is "mock was called with X", make sure the real-world contract actually requires X. Otherwise you're testing the mock, not the code.
- **Session-scoped fixtures with mutable state** — pollution across test runs. Scope to `function` unless the fixture is immutable.

## Related skills

- `add-config-field` — when the thing you're testing is a config field
- `add-trainer-feature` — when the thing you're testing is a larger feature
- `review-pr` — run its checklist before requesting review
