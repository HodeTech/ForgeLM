"""Phase 22 / 2026-05-08: CLI wizard parity-with-web modernisation tests.

Covers the new helpers + step-machine plumbing introduced when the CLI
wizard was extended to close the parity gap with ``site/js/wizard.js``
(20 findings G1-G20 + 5 independent observations I1-I5 documented in
the PR #40 review thread):

- ``_parse_webhook_value`` — single-prompt webhook syntax with
  ``env:VAR_NAME`` prefix and HTTPS validation.
- ``_default_safety_probes_path`` — package-data resolution of the
  bundled probe set.
- ``_collect_trainer_hyperparameters`` — per-trainer fields gating.
- ``_check_navigation_token`` — back / reset sentinels.
- ``_apply_strict_tier_coercion`` — F-compliance-110 front-stop.
- ``_save_wizard_state`` / ``_load_wizard_state`` /
  ``_clear_wizard_state`` — XDG-aware persistence.
- ``_print_step_diff`` — terminal-friendly state diff.

No GPU / no network — every test stubs ``builtins.input`` for stdin
reads and uses a temp directory for persistence.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from forgelm import wizard
from forgelm.cli import _exit_codes as wizard_exit_codes

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _input_returning(*answers):
    """Build a side_effect for ``builtins.input`` that drains *answers*."""
    queue = list(answers)

    def _impl(_prompt_text=""):
        if not queue:
            raise AssertionError("input() called more times than answers provided")
        return queue.pop(0)

    return _impl


# ---------------------------------------------------------------------------
# Webhook URL parsing — Phase 22 / G15
# ---------------------------------------------------------------------------


class TestParseWebhookValue:
    def test_empty_returns_none(self):
        assert wizard._parse_webhook_value("") is None
        assert wizard._parse_webhook_value("   ") is None

    def test_https_url_accepted(self):
        section = wizard._parse_webhook_value("https://hooks.slack.com/services/T/B/X")
        assert section == {"url": "https://hooks.slack.com/services/T/B/X"}

    def test_env_prefix_emits_url_env(self):
        section = wizard._parse_webhook_value("env:SLACK_WEBHOOK_URL")
        assert section == {"url_env": "SLACK_WEBHOOK_URL"}

    def test_env_prefix_case_insensitive_keyword(self):
        # The ``env:`` keyword itself is matched case-insensitively so
        # operators copying values from the web wizard's YAML aren't
        # tripped up by case.
        section = wizard._parse_webhook_value("ENV:MY_WEBHOOK")
        assert section == {"url_env": "MY_WEBHOOK"}

    def test_env_prefix_empty_var_name_rejected(self):
        with pytest.raises(ValueError, match="non-empty variable name"):
            wizard._parse_webhook_value("env:")

    def test_env_prefix_lowercase_var_name_rejected(self):
        # POSIX env-var convention enforces uppercase letters; lower-
        # case names break ``${VAR}`` shell expansion in CI.
        with pytest.raises(ValueError, match="POSIX environment-variable name"):
            wizard._parse_webhook_value("env:lowercase_var")

    def test_bare_string_no_scheme_rejected(self):
        with pytest.raises(ValueError, match="not a valid URL"):
            wizard._parse_webhook_value("not-a-url")

    def test_unknown_scheme_rejected(self):
        with pytest.raises(ValueError, match="must use `https://`"):
            wizard._parse_webhook_value("ftp://example.com/hook")

    def test_http_warning_but_accepted(self, capsys):
        section = wizard._parse_webhook_value("http://hooks.example.com/x")
        assert section == {"url": "http://hooks.example.com/x"}
        captured = capsys.readouterr().out
        assert "uses HTTP, not HTTPS" in captured


# ---------------------------------------------------------------------------
# Safety probe path resolution — Phase 22 / G16
# ---------------------------------------------------------------------------


class TestDefaultSafetyProbesPath:
    def test_resolves_to_packaged_jsonl(self):
        # The bundled probe set lives at
        # ``forgelm/safety_prompts/default_probes.jsonl`` and is
        # shipped via ``[tool.setuptools.package-data]`` in
        # ``pyproject.toml`` (Phase 36).  The resolver must produce a
        # real on-disk path the trainer can ``open``.
        path = wizard._default_safety_probes_path()
        assert path.endswith("default_probes.jsonl")
        assert Path(path).is_file()

    def test_path_is_absolute_or_resolvable(self):
        # ``pkgutil.get_data`` would return bytes; the wizard prefers
        # ``importlib.resources.files`` so the operator gets a string
        # path they can hand to ``forgelm safety-eval --probes``.
        path = wizard._default_safety_probes_path()
        assert Path(path).resolve().is_file()


# ---------------------------------------------------------------------------
# Wizard defaults loader degrade-cleanly contract — F-P7-OPUS-22 / M5
# ---------------------------------------------------------------------------


class TestLoadDefaultsDegradation:
    """A present-but-corrupt _defaults.json must NOT crash the wizard at import."""

    def _patch_defaults_to(self, tmp_path: Path, content: str):
        """Return a patch context that makes ``_defaults.json`` yield *content*.

        ``_load_defaults`` does ``from importlib.resources import files``
        function-locally, so we patch the canonical ``importlib.resources.files``
        symbol the local import resolves to.
        """
        corrupt = tmp_path / "_defaults.json"
        corrupt.write_text(content, encoding="utf-8")

        class _FakeTraversable:
            def joinpath(self, _name):
                return corrupt

        return patch("importlib.resources.files", lambda _pkg: _FakeTraversable())

    def test_corrupt_defaults_json_falls_back_to_empty_dict(self, tmp_path: Path):
        # json.JSONDecodeError is a ValueError, NOT an OSError — the old
        # except tuple (ModuleNotFoundError, OSError) let it escape and crash
        # the whole wizard subsystem at module-import time. The loader must
        # now degrade to the hardcoded fallbacks exactly like the absent-file
        # case does.
        from forgelm.wizard._state import _load_defaults

        with self._patch_defaults_to(tmp_path, "{ corrupt json not valid "):
            result = _load_defaults()
        assert result == {}

    def test_corrupt_defaults_logs_warning(self, tmp_path: Path, caplog):
        import logging

        from forgelm.wizard._state import _load_defaults

        with caplog.at_level(logging.WARNING, logger="forgelm.wizard"):
            with self._patch_defaults_to(tmp_path, "{ corrupt "):
                _load_defaults()
        assert any("corrupt" in rec.message.lower() for rec in caplog.records)

    def test_wellformed_defaults_still_loads(self, tmp_path: Path):
        # Regression guard: the broadened except must not swallow a valid
        # file. A well-formed object still parses (sans ``//`` comment keys).
        from forgelm.wizard._state import _load_defaults

        with self._patch_defaults_to(tmp_path, '{"//comment": "x", "model": {"max_length": 4096}}'):
            result = _load_defaults()
        assert result == {"model": {"max_length": 4096}}


# ---------------------------------------------------------------------------
# Navigation tokens — Phase 22 / G3
# ---------------------------------------------------------------------------


class TestNavigationTokens:
    def test_back_token_raises_wizardback(self):
        for token in ("back", "b", "BACK", "  Back  "):
            with pytest.raises(wizard.WizardBack):
                wizard._check_navigation_token(token)

    def test_reset_token_raises_wizardreset(self):
        for token in ("reset", "r", "RESET", "  Reset  "):
            with pytest.raises(wizard.WizardReset):
                wizard._check_navigation_token(token)

    def test_cancel_token_raises_wizardcancel(self):
        # F-P7-OPUS-05: the welcome banner promises ``cancel`` / ``q``
        # exit cleanly "at any prompt", so the primitive prompt must
        # auto-raise ``WizardCancel``.  The BYOD prelude catches it
        # locally to preserve its "fall back to the full wizard"
        # semantics (see test_byod fall-back tests).
        for token in ("cancel", "c", "q", "quit", "CANCEL", "  Quit  "):
            with pytest.raises(wizard.WizardCancel):
                wizard._check_navigation_token(token)

    def test_empty_string_does_not_raise(self):
        wizard._check_navigation_token("")
        wizard._check_navigation_token("   ")

    def test_normal_input_does_not_raise(self):
        wizard._check_navigation_token("Llama-3.1-8B")
        wizard._check_navigation_token("yes")
        wizard._check_navigation_token("3")


# ---------------------------------------------------------------------------
# Trainer-specific hyperparameters — Phase 22 / G1
# ---------------------------------------------------------------------------


class TestCollectTrainerHyperparameters:
    def test_sft_returns_empty(self):
        # SFT has no per-trainer knobs in ``TrainingConfig``.
        with patch("builtins.input", side_effect=_input_returning()):
            result = wizard._collect_trainer_hyperparameters("sft")
        assert result == {}

    def test_dpo_returns_dpo_beta(self):
        with patch("builtins.input", side_effect=_input_returning("0.1")):
            result = wizard._collect_trainer_hyperparameters("dpo")
        assert result == {"dpo_beta": 0.1}

    def test_simpo_returns_beta_and_gamma(self):
        with patch("builtins.input", side_effect=_input_returning("2.0", "0.5")):
            result = wizard._collect_trainer_hyperparameters("simpo")
        assert result == {"simpo_beta": 2.0, "simpo_gamma": 0.5}

    def test_kto_returns_kto_beta(self):
        with patch("builtins.input", side_effect=_input_returning("0.1")):
            result = wizard._collect_trainer_hyperparameters("kto")
        assert result == {"kto_beta": 0.1}

    def test_orpo_returns_orpo_beta(self):
        with patch("builtins.input", side_effect=_input_returning("0.1")):
            result = wizard._collect_trainer_hyperparameters("orpo")
        assert result == {"orpo_beta": 0.1}

    def test_grpo_returns_full_block_with_reward_model(self):
        with patch(
            "builtins.input",
            side_effect=_input_returning("4", "512", "my_reward.score"),
        ):
            result = wizard._collect_trainer_hyperparameters("grpo")
        assert result == {
            "grpo_num_generations": 4,
            "grpo_max_completion_length": 512,
            "grpo_reward_model": "my_reward.score",
        }

    def test_grpo_omits_reward_model_when_blank(self):
        # Empty reward_model means "use the built-in shaper" — which
        # the schema represents as ``None``.  The wizard drops the key
        # entirely so the YAML doesn't carry an empty string the
        # validator would have to special-case.
        with patch("builtins.input", side_effect=_input_returning("4", "512", "")):
            result = wizard._collect_trainer_hyperparameters("grpo")
        assert result == {
            "grpo_num_generations": 4,
            "grpo_max_completion_length": 512,
        }
        assert "grpo_reward_model" not in result


# ---------------------------------------------------------------------------
# Strict-tier auto-coercion — Phase 22 / G8
# ---------------------------------------------------------------------------


class TestApplyStrictTierCoercion:
    def test_minimal_risk_no_op(self):
        config = {"evaluation": {"auto_revert": True}}
        wizard._apply_strict_tier_coercion(config, {"risk_classification": "minimal-risk"})
        # Should NOT have added safety + require_human_approval.
        assert "safety" not in config["evaluation"]
        assert "require_human_approval" not in config["evaluation"]

    def test_high_risk_enables_safety_and_human_approval(self, capsys):
        config: dict = {}
        wizard._apply_strict_tier_coercion(config, {"risk_classification": "high-risk"})
        assert config["evaluation"]["require_human_approval"] is True
        assert config["evaluation"]["safety"]["enabled"] is True
        assert config["evaluation"]["auto_revert"] is True
        captured = capsys.readouterr().out
        assert "Article 9" in captured
        assert "Article 14" in captured

    def test_unacceptable_also_triggers_coercion(self):
        config: dict = {}
        wizard._apply_strict_tier_coercion(config, {"risk_classification": "unacceptable"})
        assert config["evaluation"]["safety"]["enabled"] is True

    def test_does_not_overwrite_explicit_safety_block(self):
        # Operator already configured safety with track_categories=False;
        # coercion mustn't clobber their choices when safety is already
        # enabled.
        config = {"evaluation": {"safety": {"enabled": True, "track_categories": False}}}
        wizard._apply_strict_tier_coercion(config, {"risk_classification": "high-risk"})
        assert config["evaluation"]["safety"]["enabled"] is True
        assert config["evaluation"]["safety"]["track_categories"] is False


# ---------------------------------------------------------------------------
# Persistence — Phase 22 / G6
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_state_dir(tmp_path, monkeypatch):
    """Redirect the wizard state directory to a tmp_path."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    yield tmp_path / "forgelm"


class TestPersistence:
    def test_load_returns_none_when_no_snapshot(self, isolated_state_dir):
        assert wizard._load_wizard_state() is None

    def test_save_and_load_roundtrip(self, isolated_state_dir):
        snapshot = {
            "experience": "beginner",
            "use_case": "customer-support",
            "current_step": 3,
            "completed_steps": ["welcome", "use-case", "model"],
            "config": {"model": {"name_or_path": "Qwen/Qwen2.5-7B-Instruct"}},
        }
        wizard._save_wizard_state(snapshot)
        loaded = wizard._load_wizard_state()
        assert loaded == snapshot

    def test_clear_removes_snapshot(self, isolated_state_dir):
        wizard._save_wizard_state({"experience": "expert"})
        wizard._clear_wizard_state()
        assert wizard._load_wizard_state() is None

    def test_clear_is_idempotent(self, isolated_state_dir):
        # Clearing a non-existent snapshot must not raise — the wizard
        # calls ``_clear_wizard_state`` on successful completion even
        # when nothing was saved.
        wizard._clear_wizard_state()
        wizard._clear_wizard_state()  # still fine

    def test_load_returns_none_on_version_mismatch(self, isolated_state_dir):
        path = wizard._wizard_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("v: 99999\nexperience: expert\n", encoding="utf-8")
        assert wizard._load_wizard_state() is None

    def test_load_returns_none_on_corrupt_snapshot(self, isolated_state_dir):
        path = wizard._wizard_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Invalid YAML — must not raise; just silently miss.
        path.write_text("not: valid: yaml: at: all", encoding="utf-8")
        assert wizard._load_wizard_state() is None

    def test_state_path_honours_xdg_cache_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        assert wizard._wizard_state_path() == tmp_path / "forgelm" / "wizard_state.yaml"

    def test_state_path_falls_back_to_home_cache_when_xdg_unset(self, monkeypatch):
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        path = wizard._wizard_state_path()
        # ``Path.home() / ".cache"`` may differ across CI environments,
        # but the trailing components are stable.
        assert path.name == "wizard_state.yaml"
        assert path.parent.name == "forgelm"


# ---------------------------------------------------------------------------
# Step-diff preview — Phase 22 / G7
# ---------------------------------------------------------------------------


class TestStepDiff:
    def test_no_diff_emits_nothing(self, capsys):
        wizard._print_step_diff({"a": 1}, {"a": 1}, "step1")
        assert capsys.readouterr().out == ""

    def test_added_keys_marked_with_plus(self, capsys):
        wizard._print_step_diff({}, {"model": {"name_or_path": "X"}}, "model")
        out = capsys.readouterr().out
        assert "Step diff (model):" in out
        assert "+ model.name_or_path: 'X'" in out

    def test_changed_keys_marked_with_tilde(self, capsys):
        wizard._print_step_diff({"a": 1}, {"a": 2}, "params")
        out = capsys.readouterr().out
        assert "~ a: 1 → 2" in out

    def test_nested_dict_paths_dotted(self, capsys):
        wizard._print_step_diff(
            {"training": {"epochs": 3}},
            {"training": {"epochs": 3, "batch_size": 4}},
            "training-params",
        )
        out = capsys.readouterr().out
        assert "+ training.batch_size: 4" in out
        assert "training.epochs" not in out  # unchanged → not printed


# ---------------------------------------------------------------------------
# Webhook collector — Phase 22 / G15 (integration around ``_parse_webhook_value``)
# ---------------------------------------------------------------------------


class TestCollectWebhookConfig:
    def test_decline_returns_none(self):
        with patch("builtins.input", side_effect=_input_returning("n")):
            assert wizard._collect_webhook_config() is None

    def test_env_prefix_default_accepted(self):
        # The prompt default is ``env:FORGELM_WEBHOOK_URL`` so an empty
        # answer accepts the suggested env-var.  ``notify_on_start``
        # defaults to ``False`` (web-wizard parity) — start
        # notifications are noisy and most operators want only success
        # / failure pings.
        with patch("builtins.input", side_effect=_input_returning("y", "")):
            section = wizard._collect_webhook_config()
        assert section == {
            "url_env": "FORGELM_WEBHOOK_URL",
            "notify_on_start": False,
            "notify_on_success": True,
            "notify_on_failure": True,
        }

    def test_https_url_accepted(self):
        with patch(
            "builtins.input",
            side_effect=_input_returning("y", "https://hooks.slack.com/x"),
        ):
            section = wizard._collect_webhook_config()
        assert section is not None
        assert section["url"] == "https://hooks.slack.com/x"

    def test_invalid_url_reprompts(self, capsys):
        # First answer is malformed; second is a valid env: ref.
        with patch(
            "builtins.input",
            side_effect=_input_returning("y", "not-a-url", "env:MY_HOOK"),
        ):
            section = wizard._collect_webhook_config()
        assert section is not None
        assert section["url_env"] == "MY_HOOK"
        captured = capsys.readouterr().out
        assert "not a valid URL" in captured


# ---------------------------------------------------------------------------
# Use-case preset registry — Phase 22 / G12 + I4
# ---------------------------------------------------------------------------


class TestUseCasePresets:
    def test_presets_match_quickstart_template_keys(self):
        # I4: the web wizard's USE_CASE_PRESETS keys must adopt the
        # CLI quickstart TEMPLATES keys (single source of truth).  This
        # asserts the CLI side of the agreement: every TEMPLATES key
        # appears in ``_wizard_use_case_presets``.
        from forgelm.quickstart import TEMPLATES

        presets = wizard._wizard_use_case_presets()
        for key in TEMPLATES.keys():
            assert key in presets, f"Use-case preset for '{key}' missing"

    def test_custom_preset_present(self):
        # ``custom`` is a wizard-only escape hatch that doesn't seed
        # any defaults.
        presets = wizard._wizard_use_case_presets()
        assert wizard._MANUAL_USE_CASE in presets
        assert presets[wizard._MANUAL_USE_CASE]["model"] is None


# ---------------------------------------------------------------------------
# Strategy choice mapping — Phase 22 / G2
# ---------------------------------------------------------------------------


class TestStrategyChoices:
    def test_six_strategies_listed(self):
        # QLoRA / LoRA / DoRA / PiSSA / rsLoRA / GaLore — the full
        # ``LoraConfigModel.method`` Literal plus GaLore as a separate
        # axis.
        assert len(wizard._STRATEGY_CHOICES) == 6

    def test_methods_cover_full_lora_literal(self):
        methods = {choice.method for choice in wizard._STRATEGY_CHOICES if not choice.use_galore}
        assert methods == {"lora", "dora", "pissa", "rslora"}

    def test_galore_choice_is_singleton(self):
        galore_choices = [c for c in wizard._STRATEGY_CHOICES if c.use_galore]
        assert len(galore_choices) == 1


# ---------------------------------------------------------------------------
# GaLore optimizer variants — Phase 22 / G9
# ---------------------------------------------------------------------------


class TestGaloreOptimizers:
    def test_six_variants_listed(self):
        assert len(wizard._GALORE_OPTIMIZERS) == 6

    def test_includes_layerwise_siblings(self):
        # The original wizard listed only the three base variants.
        # Phase 22 surfaces all six, including ``_layerwise`` siblings
        # that drop peak VRAM further by recomputing per-layer.
        assert "galore_adamw_layerwise" in wizard._GALORE_OPTIMIZERS
        assert "galore_adamw_8bit_layerwise" in wizard._GALORE_OPTIMIZERS
        assert "galore_adafactor_layerwise" in wizard._GALORE_OPTIMIZERS


# ---------------------------------------------------------------------------
# Schema-default parity — Phase 22 / G11
# ---------------------------------------------------------------------------


class TestSchemaDefaultParity:
    def test_lora_r_default_matches_schema(self):
        # G11: the v0.5.5 wizard set DEFAULT_LORA_R = 16 while the
        # schema default in ``LoraConfigModel.r`` was 8.  Phase 22
        # aligns the wizard with the schema so an operator who accepts
        # every prompt produces a YAML byte-equivalent to
        # ``ForgeConfig()``.
        from forgelm.config import LoraConfigModel

        assert wizard.DEFAULT_LORA_R == LoraConfigModel.model_fields["r"].default

    def test_lora_alpha_default_is_2x_r(self):
        # Adapter convention: alpha = 2 * r.
        assert wizard.DEFAULT_LORA_ALPHA == 2 * wizard.DEFAULT_LORA_R

    def test_default_epochs_matches_schema(self):
        from forgelm.config import TrainingConfig

        assert wizard.DEFAULT_EPOCHS == TrainingConfig.model_fields["num_train_epochs"].default

    def test_default_batch_size_matches_schema(self):
        from forgelm.config import TrainingConfig

        assert wizard.DEFAULT_BATCH_SIZE == TrainingConfig.model_fields["per_device_train_batch_size"].default

    def test_default_lr_matches_schema(self):
        from forgelm.config import TrainingConfig

        assert wizard.DEFAULT_LR == TrainingConfig.model_fields["learning_rate"].default


# ---------------------------------------------------------------------------
# Orchestrator step-machine — Phase 22 / G3 + G7 + I3
#
# The orchestrator was the lowest-coverage module after the Phase 22
# split (14 % per the post-merge review).  These tests exercise the
# step driver directly with stub steps so we don't need to mock 9
# real ``_collect_*`` helpers.
# ---------------------------------------------------------------------------


class TestStepMachineDriver:
    """Exercise ``_drive_wizard_steps`` directly with stub steps."""

    def _make_steps(self, runners):
        """Build a tuple of ``_StepDef``-shaped objects for *runners*."""
        return tuple(
            wizard._orchestrator._StepDef(label=f"step-{i}", runner=runner) for i, runner in enumerate(runners)
        )

    def test_runs_every_step_in_order(self, isolated_state_dir, monkeypatch):
        order = []

        def make(label):
            def runner(state):
                order.append(label)
                state.config[label] = True

            return runner

        steps = self._make_steps([make("a"), make("b"), make("c")])
        monkeypatch.setattr(wizard._orchestrator, "_STEPS", steps)

        state = wizard._orchestrator._drive_wizard_steps(wizard._WizardState())
        assert order == ["a", "b", "c"]
        assert state.completed_steps == ["step-0", "step-1", "step-2"]
        assert state.current_step == 3

    def test_back_restores_prev_config(self, isolated_state_dir, monkeypatch):
        # Step 1 mutates state.config; step 2 mutates it then raises
        # WizardBack.  Mutations from step 2 must NOT leak back into
        # step 1 — that's the whole point of the prev_config snapshot.
        attempts = {"step-1": 0}

        def step0(state):
            state.config.setdefault("model", {})["name"] = "from-step-0"

        # NOTE: the back-out path is exercised via ``step1_combined``
        # below (line ~570).  An earlier draft kept a separate
        # ``step1_first`` here for symmetry with ``step0`` /
        # ``step0_after_back``, but the combined version is the one
        # the test actually wires into ``_make_steps`` so the standalone
        # ``step1_first`` was dead code (SonarCloud python:S5603).

        def step0_after_back(state):
            # Re-running step 0 — the leaked key from step 1's first
            # attempt MUST be gone.
            assert "leaked" not in state.config

        # Stitch: step 0 runs twice (first then after back); step 1
        # runs once (raises back), then once more silently.
        runs = {"step-0": 0}

        def step0_combined(state):
            runs["step-0"] += 1
            if runs["step-0"] == 1:
                step0(state)
            else:
                step0_after_back(state)

        def step1_combined(state):
            attempts["step-1"] += 1
            if attempts["step-1"] == 1:
                state.config["leaked"] = "should-not-survive"
                raise wizard.WizardBack

        steps = self._make_steps([step0_combined, step1_combined])
        monkeypatch.setattr(wizard._orchestrator, "_STEPS", steps)

        state = wizard._orchestrator._drive_wizard_steps(wizard._WizardState())
        assert "leaked" not in state.config
        assert state.config["model"]["name"] == "from-step-0"

    def test_back_at_first_step_is_no_op(self, isolated_state_dir, monkeypatch, capsys):
        attempts = {"calls": 0}

        def step0(state):
            attempts["calls"] += 1
            if attempts["calls"] == 1:
                raise wizard.WizardBack
            # Second call: succeed normally.
            state.config["done"] = True

        steps = self._make_steps([step0])
        monkeypatch.setattr(wizard._orchestrator, "_STEPS", steps)

        wizard._orchestrator._drive_wizard_steps(wizard._WizardState())
        captured = capsys.readouterr().out
        assert "Already at the first step" in captured

    def test_reset_re_loops_with_fresh_state(self, isolated_state_dir, monkeypatch):
        # WizardReset MUST cause the driver to start over with a fresh
        # _WizardState — returning early would let _run_full_wizard
        # treat the reset as a completed run and try to save an empty
        # config.
        attempts = {"step-0": 0, "step-1": 0}

        def step0(state):
            attempts["step-0"] += 1
            state.config["k0"] = attempts["step-0"]

        def step1(state):
            attempts["step-1"] += 1
            if attempts["step-1"] == 1:
                raise wizard.WizardReset

        steps = self._make_steps([step0, step1])
        monkeypatch.setattr(wizard._orchestrator, "_STEPS", steps)

        state = wizard._orchestrator._drive_wizard_steps(wizard._WizardState())
        # step-0 ran twice (first run + after reset), step-1 ran twice
        # too (first raised reset, second completed cleanly).
        assert attempts["step-0"] == 2
        assert attempts["step-1"] == 2
        # State after the reset should reflect the second (fresh) run,
        # not the first.
        assert state.config["k0"] == 2

    def test_persists_after_each_completed_step(self, isolated_state_dir, monkeypatch):
        def step0(state):
            state.config["a"] = 1

        def step1(state):
            # Snapshot must already include step 0's mutation when we
            # arrive here — the orchestrator persists eagerly.
            saved = wizard._load_wizard_state()
            assert saved is not None
            assert saved["config"]["a"] == 1
            state.config["b"] = 2

        steps = self._make_steps([step0, step1])
        monkeypatch.setattr(wizard._orchestrator, "_STEPS", steps)

        wizard._orchestrator._drive_wizard_steps(wizard._WizardState())
        saved = wizard._load_wizard_state()
        assert saved is not None
        assert saved["config"] == {"a": 1, "b": 2}
        assert saved["completed_steps"] == ["step-0", "step-1"]


class TestMaybeResumeState:
    def test_returns_fresh_state_when_no_snapshot(self, isolated_state_dir):
        state = wizard._orchestrator._maybe_resume_state()
        assert state.current_step == 0
        assert state.config == {}
        assert state.completed_steps == []

    def test_resumes_from_saved_snapshot(self, isolated_state_dir):
        wizard._save_wizard_state(
            {
                "experience": "beginner",
                "use_case": "domain-expert",
                "current_step": 2,
                "completed_steps": ["welcome", "use-case"],
                "config": {"model": {"name_or_path": "x"}},
            }
        )
        with patch("builtins.input", side_effect=_input_returning("y")):  # accept resume
            state = wizard._orchestrator._maybe_resume_state()
        assert state.current_step == 2
        assert state.completed_steps == ["welcome", "use-case"]
        assert state.config == {"model": {"name_or_path": "x"}}
        assert state.experience == "beginner"

    def test_decline_resume_clears_snapshot(self, isolated_state_dir):
        wizard._save_wizard_state(
            {
                "experience": "expert",
                "use_case": "custom",
                "current_step": 1,
                "completed_steps": ["welcome"],
                "config": {"a": 1},
            }
        )
        with patch("builtins.input", side_effect=_input_returning("n")):  # decline
            state = wizard._orchestrator._maybe_resume_state()
        assert state.current_step == 0
        assert state.config == {}
        # Snapshot was cleared so a future load returns None.
        assert wizard._load_wizard_state() is None


class TestStepWelcome:
    def test_welcome_sets_backend_hint(self, capsys):
        # Stubbing _detect_hardware to return "no GPU" makes the
        # outcome deterministic across CI runners that may or may not
        # see CUDA.
        state = wizard._WizardState()
        with (
            patch("builtins.input", side_effect=_input_returning("n")),  # not first-time
            patch.object(
                wizard._orchestrator,
                "_detect_hardware",
                return_value={
                    "gpu_available": False,
                    "gpu_name": None,
                    "vram_gb": None,
                    "cuda_version": None,
                },
            ),
        ):
            wizard._orchestrator._step_welcome(state)
        assert state.experience == "expert"
        assert state.config["model"]["backend"] == "transformers"
        captured = capsys.readouterr().out
        assert "No GPU detected" in captured

    def test_welcome_beginner_branch(self):
        state = wizard._WizardState()
        with (
            patch("builtins.input", side_effect=_input_returning("y")),  # first-time
            patch.object(
                wizard._orchestrator,
                "_detect_hardware",
                return_value={
                    "gpu_available": False,
                    "gpu_name": None,
                    "vram_gb": None,
                    "cuda_version": None,
                },
            ),
        ):
            wizard._orchestrator._step_welcome(state)
        assert state.experience == "beginner"


# ---------------------------------------------------------------------------
# Phase 22 review-cycle 2 — new behaviour pinned by tests
# ---------------------------------------------------------------------------


class TestStepDiffStripsInternalMeta:
    """B-NEW-1 — ``_print_step_diff`` must NOT leak ``_wizard_meta.*`` keys."""

    def test_strategy_step_diff_omits_wizard_meta(self, isolated_state_dir, monkeypatch, capsys):
        # Arrange: stub a step that mutates both a real config key and
        # the internal _wizard_meta scratch namespace (mirroring what
        # _step_strategy does at orchestrator.py:265).
        def step_with_meta(state):
            state.config.setdefault("model", {})["name_or_path"] = "real-value"
            state.config.setdefault("_wizard_meta", {})["use_galore"] = True

        steps = (wizard._orchestrator._StepDef(label="meta-step", runner=step_with_meta),)
        monkeypatch.setattr(wizard._orchestrator, "_STEPS", steps)

        wizard._orchestrator._drive_wizard_steps(wizard._WizardState())
        captured = capsys.readouterr().out
        assert "model.name_or_path" in captured
        # The leaked key was the bug — pin its absence.
        assert "_wizard_meta" not in captured


class TestComplianceDoesNotOverwriteEarlierGovernance:
    """B-NEW-2 — _step_compliance must skip governance re-prompt under non-strict tier."""

    def test_skip_when_already_populated_and_non_strict(self, isolated_state_dir, capsys):
        state = wizard._WizardState()
        state.config.setdefault("data", {})["governance"] = {
            "collection_method": "from-step-6",
            "annotation_process": "from-step-6",
            "known_biases": "from-step-6",
            "personal_data_included": False,
            "dpia_completed": False,
        }
        with patch(
            "builtins.input",
            side_effect=_input_returning(
                "y",  # configure compliance metadata
                "",  # provider_name
                "",  # provider_contact
                "",  # system_name
                "",  # intended_purpose
                "",  # known_limitations
                "v0.1.0",  # system_version
                "2",  # risk_classification = minimal-risk (non-strict)
                "n",  # decline article 9 risk_assessment
                "n",  # decline retention
                "n",  # decline monitoring
            ),
        ):
            wizard._orchestrator._step_compliance(state)
        captured = capsys.readouterr().out
        assert "Article 10 data.governance already populated" in captured
        # The earlier governance answers must survive untouched.
        assert state.config["data"]["governance"]["collection_method"] == "from-step-6"


class TestAtomicStateWrite:
    """B-NEW-3 — _save_wizard_state must use temp+rename, not direct write."""

    def test_save_state_lands_atomically(self, isolated_state_dir, monkeypatch):
        # Sanity: snapshot exists after a clean save.
        wizard._save_wizard_state({"experience": "expert"})
        assert wizard._wizard_state_path().is_file()

    def test_failed_save_does_not_leave_partial_file(self, isolated_state_dir, monkeypatch):
        # Force os.replace to fail mid-flight; the target file must
        # remain absent rather than a half-written YAML.
        from forgelm.wizard import _state as _state_mod

        fake_replace_calls = []

        def _broken_replace(src, dst):
            fake_replace_calls.append((src, dst))
            raise OSError("simulated atomic rename failure")

        monkeypatch.setattr(_state_mod.os, "replace", _broken_replace)
        wizard._save_wizard_state({"experience": "expert"})
        assert fake_replace_calls, "os.replace should have been invoked"
        # The target file must NOT exist (write was best-effort, the
        # atomic rename never landed).
        assert not wizard._wizard_state_path().exists()


class TestWebhookSSRFPreflight:
    """A1 — _parse_webhook_value rejects loopback / RFC1918 hostnames."""

    def test_loopback_rejected(self):
        with pytest.raises(ValueError, match="private / loopback"):
            wizard._parse_webhook_value("https://127.0.0.1/hook")

    def test_link_local_imds_rejected(self):
        with pytest.raises(ValueError, match="private / loopback"):
            wizard._parse_webhook_value("https://169.254.169.254/latest/meta-data")

    def test_rfc1918_rejected(self):
        with pytest.raises(ValueError, match="private / loopback"):
            wizard._parse_webhook_value("https://10.0.0.5/x")

    def test_public_url_still_accepted(self):
        section = wizard._parse_webhook_value("https://hooks.slack.com/services/T/B/X")
        assert section == {"url": "https://hooks.slack.com/services/T/B/X"}

    def test_env_prefix_does_not_resolve(self):
        # ``env:VAR`` short-circuits before SSRF checks because the URL
        # is unresolved at config time.
        section = wizard._parse_webhook_value("env:SLACK_WEBHOOK_URL")
        assert section == {"url_env": "SLACK_WEBHOOK_URL"}


class TestUniqueFilenamePrompt:
    """B2 — overwrite confirmation + auto-suffix on existing files."""

    def test_returns_default_when_target_absent(self, tmp_path, monkeypatch):
        target = str(tmp_path / "fresh.yaml")
        with patch("builtins.input", side_effect=_input_returning(target)):
            result = wizard._prompt_unique_filename("Save as", "default.yaml")
        assert result == target

    def test_overwrite_confirmation_yes(self, tmp_path, monkeypatch):
        target = tmp_path / "exists.yaml"
        target.write_text("old\n", encoding="utf-8")
        with patch("builtins.input", side_effect=_input_returning(str(target), "y")):
            result = wizard._prompt_unique_filename("Save as", "default.yaml")
        assert result == str(target)

    def test_overwrite_declined_uses_next_free(self, tmp_path):
        target = tmp_path / "exists.yaml"
        target.write_text("old\n", encoding="utf-8")
        with patch("builtins.input", side_effect=_input_returning(str(target), "n")):
            result = wizard._prompt_unique_filename("Save as", "default.yaml")
        assert result == str(tmp_path / "exists_2.yaml")

    def test_next_free_filename_increments(self, tmp_path):
        (tmp_path / "x.yaml").write_text("a", encoding="utf-8")
        (tmp_path / "x_2.yaml").write_text("b", encoding="utf-8")
        result = wizard._next_free_filename(str(tmp_path / "x.yaml"))
        assert result == str(tmp_path / "x_3.yaml")

    # PR #57 Gemini review absorption — case-insensitive extension
    # check + tilde expansion.  Pre-fix, ``my_config.YAML`` got a
    # second ``.yaml`` appended (producing ``my_config.YAML.yaml``)
    # and ``~/cfg.yaml`` flowed through unexpanded so
    # ``_atomic_yaml_write`` would later create a literal ``~``
    # directory in the wizard's CWD instead of writing under HOME.

    def test_uppercase_yaml_extension_not_doubled(self, tmp_path):
        """``.YAML`` (any case) counts as a yaml extension — no double-``.yaml``."""
        target = tmp_path / "config.YAML"
        target.write_text("old\n", encoding="utf-8")
        with patch("builtins.input", side_effect=_input_returning(str(target), "y")):
            result = wizard._prompt_unique_filename("Save as", "default.yaml")
        assert result == str(target), "case-insensitive ext check must not append a second .yaml"

    def test_uppercase_yml_extension_not_doubled(self, tmp_path):
        """``.YML`` (any case) counts as a yaml extension — no double-``.yaml``."""
        target = tmp_path / "config.YML"
        target.write_text("old\n", encoding="utf-8")
        with patch("builtins.input", side_effect=_input_returning(str(target), "y")):
            result = wizard._prompt_unique_filename("Save as", "default.yaml")
        assert result == str(target), "case-insensitive ext check must accept .YML too"

    def test_tilde_path_expands_to_home(self, tmp_path, monkeypatch):
        """``~/foo.yaml`` resolves to ``$HOME/foo.yaml`` before the existence check."""
        monkeypatch.setenv("HOME", str(tmp_path))
        # POSIX Path.expanduser reads HOME on every platform we support;
        # Windows reads USERPROFILE.  Cover the Windows env var too so the
        # test runs deterministically on the cross-OS publish matrix.
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        with patch("builtins.input", side_effect=_input_returning("~/myconf.yaml")):
            result = wizard._prompt_unique_filename("Save as", "default.yaml")
        assert "~" not in result, "tilde must be expanded, not passed through literally"
        # Resolve both sides because macOS prefixes /private to /var/* tmp paths.
        from pathlib import Path as _P

        assert _P(result).resolve() == (_P(str(tmp_path)) / "myconf.yaml").resolve()


class TestNonTtyRefusal:
    """B3 — wizard refuses to launch when stdin is not a TTY."""

    def test_run_wizard_full_returns_cancelled_outcome_on_non_tty(self, capsys, monkeypatch):
        import sys as _sys

        # Simulate piped stdin: ``isatty`` returns False.
        monkeypatch.setattr(_sys.stdin, "isatty", lambda: False)
        outcome = wizard.run_wizard_full()
        assert outcome.cancelled is True
        assert outcome.config_path is None
        assert outcome.start_training is False
        captured = capsys.readouterr().out
        assert "stdin is not a TTY" in captured
        # Must point operators at the deterministic alternative.
        assert "forgelm quickstart" in captured


class TestValidateGeneratedConfig:
    """B1/E1 — validate-on-exit catches schema violations."""

    def test_valid_yaml_passes(self, tmp_path, capsys, minimal_config):
        cfg = tmp_path / "valid.yaml"
        import yaml as _yaml

        cfg.write_text(_yaml.safe_dump(minimal_config()), encoding="utf-8")
        wizard._validate_generated_config(str(cfg))
        out = capsys.readouterr().out
        assert "Schema validation passed" in out

    def test_invalid_yaml_surfaces_error(self, tmp_path, capsys):
        cfg = tmp_path / "broken.yaml"
        # Empty config — schema requires model + data + training.
        cfg.write_text("training: {}\n", encoding="utf-8")
        wizard._validate_generated_config(str(cfg))
        out = capsys.readouterr().out
        assert "failed schema validation" in out


class TestWizardOutcomeContract:
    """D2 — WizardOutcome's cancelled flag distinguishes the three exit paths."""

    def test_cancelled_when_no_path(self):
        outcome = wizard.WizardOutcome(config_path=None, start_training=False)
        assert outcome.cancelled is True

    def test_not_cancelled_when_saved_and_deferred(self):
        outcome = wizard.WizardOutcome(config_path="/tmp/x.yaml", start_training=False)
        assert outcome.cancelled is False
        assert outcome.start_training is False

    def test_not_cancelled_when_saved_and_starting(self):
        outcome = wizard.WizardOutcome(config_path="/tmp/x.yaml", start_training=True)
        assert outcome.cancelled is False
        assert outcome.start_training is True


class TestPreflightChecklist:
    """E4 — _print_preflight_checklist names the three operator-actionable signals."""

    def test_minimal_config_prints_checklist(self, capsys, monkeypatch):
        monkeypatch.setattr(
            wizard._orchestrator,
            "_detect_hardware",
            lambda: {"gpu_available": False, "gpu_name": None, "vram_gb": None, "cuda_version": None},
        )
        wizard._print_preflight_checklist(
            {
                "model": {"name_or_path": "x", "load_in_4bit": True},
                "data": {"dataset_name_or_path": "tatsu-lab/alpaca"},
                "compliance": {"risk_classification": "high-risk"},
                "evaluation": {"safety": {"enabled": True}},
            }
        )
        out = capsys.readouterr().out
        assert "Pre-flight checklist" in out
        assert "GPU" in out
        assert "Dataset" in out
        assert "high-risk" in out
        assert "safety eval enabled" in out


class TestMonitoringEnvPrefix:
    """P9 — monitoring collector accepts env:VAR_NAME like the webhook collector."""

    def test_env_prefix_routes_to_endpoint_env(self):
        with patch(
            "builtins.input",
            side_effect=_input_returning(
                "y",  # configure post-market monitoring
                "env:DATADOG_URL",  # endpoint
                "1",  # metrics_export = none
                "y",  # alert_on_drift
                "24",  # check_interval_hours
            ),
        ):
            result = wizard._collect_monitoring()
        assert result["endpoint_env"] == "DATADOG_URL"
        assert "endpoint" not in result

    def test_literal_url_routes_to_endpoint(self):
        with patch(
            "builtins.input",
            side_effect=_input_returning("y", "https://prom.example.com/push", "1", "y", "24"),
        ):
            result = wizard._collect_monitoring()
        assert result["endpoint"] == "https://prom.example.com/push"
        assert "endpoint_env" not in result


class TestSafetyFieldUnion:
    """P1/P18 — safety collector emits the union of CLI + web fields."""

    def test_binary_scoring_includes_classifier_and_max_regression(self):
        # Prompt order in _collect_safety_config:
        #   1. enable yes/no
        #   2. _prompt_choice (scoring mode)  — Python computes scoring_mode BEFORE the dict literal
        #   3. _prompt (classifier)          — first member of the dict literal
        #   4. _prompt_float (max_safety_regression)
        #   5. _prompt_yes_no (track categories)
        with patch(
            "builtins.input",
            side_effect=_input_returning(
                "y",  # enable safety eval
                "1",  # binary scoring
                "",  # classifier (use default)
                "0.05",  # max_safety_regression
                "n",  # don't track categories
            ),
        ):
            section = wizard._collect_safety_config()
        assert section["enabled"] is True
        assert section["classifier"] == "meta-llama/Llama-Guard-3-8B"
        assert section["max_safety_regression"] == pytest.approx(0.05)
        assert section["scoring"] == "binary"

    def test_confidence_weighted_includes_min_confidence(self):
        # Same prompt order as binary, plus the extra
        # min_classifier_confidence prompt before track_categories.
        with patch(
            "builtins.input",
            side_effect=_input_returning(
                "y",  # enable
                "2",  # confidence_weighted
                "",  # classifier (default)
                "0.05",  # max_safety_regression
                "0.7",  # min_classifier_confidence
                "n",  # don't track categories
            ),
        ):
            section = wizard._collect_safety_config()
        assert section["scoring"] == "confidence_weighted"
        assert section["min_classifier_confidence"] == pytest.approx(0.7)
        assert section["min_safety_score"] == pytest.approx(0.85)


class TestJudgeMinScoreSchemaParity:
    """P2 — judge collector default min_score now matches schema (5.0)."""

    def test_default_min_score_is_5_0(self):
        with patch(
            "builtins.input",
            side_effect=_input_returning("y", "gpt-4o-mini", "OPENAI_API_KEY", ""),
        ):
            section = wizard._collect_judge()
        assert section["min_score"] == pytest.approx(5.0)


class TestQLoraQuantFlagsEmitted:
    """P16 — strategy step emits bnb_4bit_* when load_in_4bit=True."""

    def test_qlora_flags_present(self, isolated_state_dir):
        state = wizard._WizardState()
        with patch(
            "builtins.input",
            side_effect=_input_returning(
                "1",  # strategy = QLoRA (load_in_4bit=True)
                "1",  # target_modules = standard
                "8",  # lora_r
                "16",  # lora_alpha
            ),
        ):
            wizard._orchestrator._step_strategy(state)
        assert state.config["model"]["load_in_4bit"] is True
        assert state.config["model"]["bnb_4bit_quant_type"] == "nf4"
        assert state.config["model"]["bnb_4bit_compute_dtype"] == "auto"

    def test_lora_no_qlora_flags(self, isolated_state_dir):
        state = wizard._WizardState()
        with patch(
            "builtins.input",
            side_effect=_input_returning(
                "2",  # strategy = LoRA (load_in_4bit=False)
                "1",
                "8",
                "16",
            ),
        ):
            wizard._orchestrator._step_strategy(state)
        assert state.config["model"]["load_in_4bit"] is False
        assert "bnb_4bit_quant_type" not in state.config["model"]


class TestWebhookConfigDefaultsParity:
    """P14 — auto_revert.max_acceptable_loss carries default 2.0 like web wizard."""

    def test_auto_revert_emits_default_2_0_when_blank(self, isolated_state_dir):
        state = wizard._WizardState()
        with patch(
            "builtins.input",
            side_effect=_input_returning(
                "y",  # enable auto-revert
                "",  # max_loss blank → use default
                "n",  # decline safety eval (default_enabled=False here)
                "n",  # decline benchmark
                "n",  # decline judge
                "n",  # decline webhook
                "n",  # decline synthetic
            ),
        ):
            wizard._orchestrator._step_evaluation(state)
        assert state.config["evaluation"]["max_acceptable_loss"] == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# Phase 22 review-cycle 3 — additional fixes
# ---------------------------------------------------------------------------


class TestAtomicWriteTempCleanup:
    """A3 — temp file must be cleaned up when os.replace fails."""

    def test_replace_failure_unlinks_temp_file(self, isolated_state_dir, monkeypatch):
        from forgelm.wizard import _state as _state_mod

        def _broken_replace(src, dst):
            raise OSError("EXDEV simulated")

        monkeypatch.setattr(_state_mod.os, "replace", _broken_replace)
        wizard._save_wizard_state({"experience": "expert"})
        # Sweep the state directory for any leftover temp files. The
        # naming convention is ``.wizard_state.<random>.tmp``; a
        # successful cleanup leaves zero matches.
        state_dir = wizard._wizard_state_path().parent
        leftovers = list(state_dir.glob(".wizard_state.*.tmp"))
        assert leftovers == [], f"Temp file leak: {leftovers}"

    def test_dump_failure_also_cleans_up(self, isolated_state_dir, monkeypatch):
        # If yaml.safe_dump itself raises before os.replace runs, the
        # finally-branch defensive sweep must still unlink the temp.
        import yaml as _yaml

        from forgelm.wizard import _state as _state_mod

        def _broken_dump(data, stream, **kw):
            stream.write("partial-write")  # force a temp file artifact
            raise _yaml.YAMLError("simulated dump failure")

        monkeypatch.setattr(_state_mod.yaml, "safe_dump", _broken_dump)
        try:
            wizard._save_wizard_state({"experience": "expert"})
        except _yaml.YAMLError:
            pass  # outer except OSError won't catch YAMLError; finally still runs
        state_dir = wizard._wizard_state_path().parent
        leftovers = list(state_dir.glob(".wizard_state.*.tmp"))
        assert leftovers == [], f"Temp file leak after dump failure: {leftovers}"


class TestStrictTierAnnouncedOnce:
    """A4 — _apply_strict_tier_coercion prints its notice only once per run."""

    def test_two_calls_print_once(self, capsys):
        config = {}
        compliance = {"risk_classification": "high-risk"}
        wizard._apply_strict_tier_coercion(config, compliance)
        first_out = capsys.readouterr().out
        assert "Auto-enabling both" in first_out

        # Second call (mirrors the orchestrator's compliance-step +
        # evaluation-step double invocation).
        wizard._apply_strict_tier_coercion(config, compliance)
        second_out = capsys.readouterr().out
        assert "Auto-enabling both" not in second_out
        # And the strict-tier behaviour is still enforced.
        assert config["evaluation"]["require_human_approval"] is True

    def test_meta_flag_stripped_before_save(self):
        config = {"_wizard_meta": {"strict_tier_announced": True}, "model": {"name_or_path": "x"}}
        cleaned = wizard._strip_internal_meta(config)
        assert "_wizard_meta" not in cleaned
        assert cleaned["model"]["name_or_path"] == "x"


class TestStateMigrationSkeleton:
    """F3 — state-version migration registry handles future bumps cleanly."""

    def test_unknown_version_returns_none(self, isolated_state_dir):
        from forgelm.wizard import _state as _state_mod

        # No migrators registered for v=0 (or any version older than
        # the current _STATE_VERSION); _load should return None.
        path = wizard._wizard_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("v: 0\nexperience: expert\n", encoding="utf-8")
        assert _state_mod._load_wizard_state() is None

    def test_newer_version_silently_ignored(self, isolated_state_dir):
        from forgelm.wizard import _state as _state_mod

        path = wizard._wizard_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # _STATE_VERSION = 1 currently — pretend the file is from v=99.
        path.write_text("v: 99\nexperience: expert\n", encoding="utf-8")
        assert _state_mod._load_wizard_state() is None

    def test_migrator_chain_runs(self, isolated_state_dir, monkeypatch):
        from forgelm.wizard import _state as _state_mod

        # Simulate a future v=1 → v=2 migrator path.  Patch the registry
        # + STATE_VERSION temporarily.
        def _migrate_v1_to_v2(snapshot):
            snapshot = dict(snapshot)
            snapshot["v"] = 2
            snapshot["new_field"] = "added-by-migrator"
            return snapshot

        monkeypatch.setitem(_state_mod._STATE_MIGRATIONS, 1, _migrate_v1_to_v2)
        monkeypatch.setattr(_state_mod, "_STATE_VERSION", 2)

        path = wizard._wizard_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("v: 1\nexperience: expert\n", encoding="utf-8")
        loaded = _state_mod._load_wizard_state()
        assert loaded is not None
        assert loaded["experience"] == "expert"
        assert loaded["new_field"] == "added-by-migrator"

    def test_migrator_that_does_not_advance_version_aborts(self, isolated_state_dir, monkeypatch):
        from forgelm.wizard import _state as _state_mod

        def _bad_migrator(snapshot):
            # Returns the same version — infinite loop risk; should
            # abort with a warning instead.
            return snapshot

        monkeypatch.setitem(_state_mod._STATE_MIGRATIONS, 1, _bad_migrator)
        monkeypatch.setattr(_state_mod, "_STATE_VERSION", 2)

        path = wizard._wizard_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("v: 1\nexperience: expert\n", encoding="utf-8")
        assert _state_mod._load_wizard_state() is None


class TestHardwareCacheReuse:
    """C16 — _detect_hardware result is cached on _WizardState."""

    def test_first_call_populates_cache(self):
        state = wizard._WizardState()
        assert state.hardware is None
        result = wizard._orchestrator._cached_hardware(state)
        assert state.hardware is result
        assert "gpu_available" in result

    def test_second_call_returns_same_object(self, monkeypatch):
        state = wizard._WizardState()
        call_count = {"n": 0}

        def _stub():
            call_count["n"] += 1
            return {"gpu_available": False, "gpu_name": None, "vram_gb": None, "cuda_version": None}

        monkeypatch.setattr(wizard._orchestrator, "_detect_hardware", _stub)
        first = wizard._orchestrator._cached_hardware(state)
        second = wizard._orchestrator._cached_hardware(state)
        assert first is second
        # _detect_hardware should have run exactly once across both calls.
        assert call_count["n"] == 1

    def test_hardware_excluded_from_persistence(self, isolated_state_dir, monkeypatch):
        # Persisted snapshot must NOT include the hardware cache field
        # (it's per-run, repr=False, compare=False on the dataclass).
        state = wizard._WizardState()
        state.hardware = {"gpu_available": True, "gpu_name": "test-gpu", "vram_gb": 24, "cuda_version": "12.1"}
        wizard._persist_state(state)
        loaded = wizard._load_wizard_state()
        assert loaded is not None
        assert "hardware" not in loaded


class TestValidateGeneratedConfigLogger:
    """G30 — _validate_generated_config emits a structured WARNING on failure."""

    def test_failure_logs_warning(self, tmp_path, caplog):
        cfg = tmp_path / "broken.yaml"
        cfg.write_text("training: {}\n", encoding="utf-8")
        with caplog.at_level("WARNING", logger="forgelm.wizard"):
            wizard._validate_generated_config(str(cfg))
        # At least one WARNING record from the wizard logger.
        warning_records = [r for r in caplog.records if r.levelname == "WARNING"]
        assert any("failed schema validation" in r.message for r in warning_records)


# ---------------------------------------------------------------------------
# F1 (review-cycle 3) — schema-driven defaults SOT
# ---------------------------------------------------------------------------


class TestSchemaDrivenDefaultsSOT:
    """F1 — wizard defaults come from the shipped JSON, in lockstep with schema."""

    def test_module_constants_match_schema_via_json(self):
        from forgelm.config import LoraConfigModel, ModelConfig, TrainingConfig

        # The module-level DEFAULT_* constants must equal the schema
        # defaults today.  This is the contract the JSON enforces.
        assert wizard.DEFAULT_LORA_R == LoraConfigModel.model_fields["r"].default
        assert wizard.DEFAULT_LORA_ALPHA == LoraConfigModel.model_fields["alpha"].default
        assert wizard.DEFAULT_DROPOUT == LoraConfigModel.model_fields["dropout"].default
        assert wizard.DEFAULT_EPOCHS == TrainingConfig.model_fields["num_train_epochs"].default
        assert wizard.DEFAULT_BATCH_SIZE == TrainingConfig.model_fields["per_device_train_batch_size"].default
        assert wizard.DEFAULT_LR == TrainingConfig.model_fields["learning_rate"].default
        assert wizard.DEFAULT_MAX_LENGTH == ModelConfig.model_fields["max_length"].default

    def test_defaults_json_shipped_and_loadable(self):
        # The JSON must be importable as package data.
        from importlib.resources import files

        path = files("forgelm.wizard").joinpath("_defaults.json")
        assert path.is_file()
        # Sanity: required sections + at least the schema-flagged fields.
        import json

        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        assert isinstance(data, dict)
        assert "lora" in data and "r" in data["lora"]
        assert "training" in data and "num_train_epochs" in data["training"]
        assert "model" in data and "max_length" in data["model"]

    def test_default_helper_returns_fallback_when_section_missing(self):
        from forgelm.wizard import _state as _state_mod

        # Use the live module-level helper but against a dict that
        # lacks the requested section/key.
        assert _state_mod._default("nonexistent_section", "key", 42) == 42
        assert _state_mod._default("lora", "nonexistent_key", "fallback") == "fallback"

    def test_load_defaults_returns_empty_on_missing_file(self, monkeypatch):
        # Simulate a slim install where the JSON is absent — _load_defaults
        # must return ``{}`` so the hardcoded fallbacks below activate
        # rather than raising at import time.
        from forgelm.wizard import _state as _state_mod

        class _NoFile:
            def joinpath(self, _):
                raise FileNotFoundError("simulated missing JSON")

        monkeypatch.setattr("importlib.resources.files", lambda _pkg: _NoFile())
        result = _state_mod._load_defaults()
        assert result == {}


# ---------------------------------------------------------------------------
# E3 (PR-D) — ``--wizard-start-from <yaml>`` idempotent re-run
# ---------------------------------------------------------------------------


class TestStartFromYAMLLoad:
    """E3 — ``_load_initial_state_from_yaml`` validates + pre-populates state."""

    def test_valid_yaml_populates_state(self, tmp_path, minimal_config):
        import yaml as _yaml

        cfg = tmp_path / "existing.yaml"
        config_data = minimal_config(training={"trainer_type": "dpo", "dpo_beta": 0.2})
        cfg.write_text(_yaml.safe_dump(config_data), encoding="utf-8")

        state = wizard._orchestrator._load_initial_state_from_yaml(str(cfg))
        assert state.experience == "expert"
        assert state.use_case == wizard._MANUAL_USE_CASE
        assert state.config["training"]["trainer_type"] == "dpo"
        assert state.config["training"]["dpo_beta"] == pytest.approx(0.2)
        # ``copy.deepcopy`` invariant — mutating returned state must
        # not bleed into the source dict.
        state.config["mutated"] = "by-test"
        assert "mutated" not in config_data

    def test_missing_path_raises_filenotfound(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="--wizard-start-from"):
            wizard._orchestrator._load_initial_state_from_yaml(str(tmp_path / "nope.yaml"))

    def test_invalid_yaml_raises_value_error(self, tmp_path):
        cfg = tmp_path / "bad.yaml"
        cfg.write_text(":\n  - this is not valid yaml mapping at top level\n", encoding="utf-8")
        with pytest.raises(ValueError, match="failed to parse|root must be a mapping"):
            wizard._orchestrator._load_initial_state_from_yaml(str(cfg))

    def test_yaml_root_not_mapping_raises(self, tmp_path):
        cfg = tmp_path / "list.yaml"
        cfg.write_text("- one\n- two\n", encoding="utf-8")
        with pytest.raises(ValueError, match="root must be a mapping"):
            wizard._orchestrator._load_initial_state_from_yaml(str(cfg))

    def test_schema_invalid_raises(self, tmp_path):
        cfg = tmp_path / "bad-schema.yaml"
        # Missing ``model``, ``data``, ``training`` — ForgeConfig will reject.
        cfg.write_text("training: {}\n", encoding="utf-8")
        with pytest.raises(ValueError, match="failed schema validation"):
            wizard._orchestrator._load_initial_state_from_yaml(str(cfg))


class TestRunWizardFullStartFrom:
    """E3 — run_wizard_full(start_from=...) skips quickstart, surfaces error cleanly."""

    def test_missing_start_from_returns_cancelled_outcome(self, tmp_path, capsys, monkeypatch):
        # When the YAML doesn't exist the wizard should NOT prompt; it
        # should surface the FileNotFoundError as a printed warning and
        # return a cancelled outcome (no save flow, no quickstart).
        import sys as _sys

        monkeypatch.setattr(_sys.stdin, "isatty", lambda: True)
        outcome = wizard.run_wizard_full(start_from=str(tmp_path / "absent.yaml"))
        assert outcome.cancelled is True
        captured = capsys.readouterr().out
        assert "--wizard-start-from path does not exist" in captured

    def test_invalid_yaml_returns_cancelled_outcome(self, tmp_path, capsys, monkeypatch):
        import sys as _sys

        cfg = tmp_path / "broken.yaml"
        cfg.write_text("training: {}\n", encoding="utf-8")
        monkeypatch.setattr(_sys.stdin, "isatty", lambda: True)
        outcome = wizard.run_wizard_full(start_from=str(cfg))
        assert outcome.cancelled is True
        captured = capsys.readouterr().out
        assert "failed schema validation" in captured


class TestStepHonorsExistingValues:
    """E3 — per-step prompts default to existing values when state pre-populated."""

    def test_strategy_step_uses_existing_lora(self, isolated_state_dir):
        # Pre-populate state with non-default LoRA values; verify the
        # strategy step uses them as prompt defaults.  Since
        # ``_step_strategy`` calls ``_prompt_int(question, existing_lora.get('r', ...))``,
        # an empty operator answer (just Enter) preserves the existing
        # value — that's the contract we test here.
        state = wizard._WizardState(config={"lora": {"r": 64, "alpha": 128}})
        with patch(
            "builtins.input",
            side_effect=_input_returning(
                "1",  # strategy = QLoRA
                "1",  # target_modules = standard
                "",  # accept default for lora_r (= existing 64)
                "",  # accept default for lora_alpha (= existing 128)
            ),
        ):
            wizard._orchestrator._step_strategy(state)
        assert state.config["lora"]["r"] == 64
        assert state.config["lora"]["alpha"] == 128

    def test_training_params_step_uses_existing_values(self, isolated_state_dir):
        state = wizard._WizardState(
            config={
                "training": {"num_train_epochs": 7, "per_device_train_batch_size": 16, "output_dir": "./my-runs"},
                "model": {"max_length": 4096},
            }
        )
        with patch(
            "builtins.input",
            side_effect=_input_returning(
                "",  # epochs (= 7)
                "",  # batch_size (= 16)
                "",  # max_length (= 4096; will trigger RoPE scaling prompt for >4096)
                "",  # output_dir (= './my-runs')
                "n",  # decline NEFTune
                "n",  # decline OOM recovery
            ),
        ):
            wizard._orchestrator._step_training_params(state)
        assert state.config["training"]["num_train_epochs"] == 7
        assert state.config["training"]["per_device_train_batch_size"] == 16
        assert state.config["model"]["max_length"] == 4096
        assert state.config["training"]["output_dir"] == "./my-runs"

    def test_dataset_step_offers_keep_existing(self, isolated_state_dir, capsys):
        state = wizard._WizardState(config={"data": {"dataset_name_or_path": "tatsu-lab/alpaca"}})
        with patch(
            "builtins.input",
            side_effect=_input_returning(
                "y",  # keep existing dataset
                "n",  # decline governance
            ),
        ):
            wizard._orchestrator._step_dataset(state)
        captured = capsys.readouterr().out
        assert "Existing dataset:" in captured
        assert state.config["data"]["dataset_name_or_path"] == "tatsu-lab/alpaca"


# ---------------------------------------------------------------------------
# PR-E (review-cycle 4) — regression coverage for PR-D contract violations
# (A1-A7, B3, B5, B6) discovered in independent code review.
# ---------------------------------------------------------------------------


class TestPRDA1StrategyHonorsExisting:
    """PR-D-A1 — _step_strategy preserves method/target_modules/dropout/bias on Enter."""

    def test_dora_method_preserved(self, isolated_state_dir):
        # Operator started from a YAML with method=dora.  Pressing
        # Enter at every prompt MUST keep method=dora — pre-fix the
        # default index was hardcoded to QLoRA.
        state = wizard._WizardState(
            config={
                "model": {"load_in_4bit": True},
                "lora": {"method": "dora", "r": 16, "alpha": 32, "dropout": 0.05, "bias": "lora_only"},
            }
        )
        with patch(
            "builtins.input",
            side_effect=_input_returning(
                "",  # strategy = use default (now derived from existing.method=dora)
                "",  # target_modules = use default (existing target_modules absent → standard)
                "",  # lora_r (= 16)
                "",  # lora_alpha (= 32)
            ),
        ):
            wizard._orchestrator._step_strategy(state)
        # Prompt-derived fields use the operator-confirmed value (which
        # is the existing one when Enter was pressed).
        assert state.config["lora"]["method"] == "dora"
        # Non-prompted fields preserved via setdefault.
        assert state.config["lora"]["dropout"] == pytest.approx(0.05)
        assert state.config["lora"]["bias"] == "lora_only"

    def test_target_modules_extended_preserved(self, isolated_state_dir):
        state = wizard._WizardState(
            config={
                "lora": {
                    "method": "lora",
                    "r": 8,
                    "alpha": 16,
                    "target_modules": ["q_proj", "k_proj", "v_proj", "o_proj"],
                }
            }
        )
        with patch(
            "builtins.input",
            side_effect=_input_returning(
                "",  # strategy
                "",  # target_modules — should detect "extended" from list and default to it
                "",  # lora_r
                "",  # lora_alpha
            ),
        ):
            wizard._orchestrator._step_strategy(state)
        # The 4-module list maps to TARGET_MODULE_PRESETS["extended"];
        # canonicalised back via the preset lookup.
        assert set(state.config["lora"]["target_modules"]) == {"q_proj", "k_proj", "v_proj", "o_proj"}

    def test_galore_strategy_preserved(self, isolated_state_dir):
        state = wizard._WizardState(
            config={
                "model": {"load_in_4bit": False},
                "lora": {"method": "lora", "r": 64, "alpha": 128},
                "training": {"galore_enabled": True},
            }
        )
        with patch(
            "builtins.input",
            side_effect=_input_returning("", "", "", ""),
        ):
            wizard._orchestrator._step_strategy(state)
        assert state.config["training"]["galore_enabled"] is True
        # GaLore branch leaves load_in_4bit=False (its strategy choice).
        assert state.config["model"]["load_in_4bit"] is False


class TestPRDA2EvaluationHonorsExisting:
    """PR-D-A2 — _step_evaluation preserves benchmark/llm_judge/safety on Enter."""

    def test_existing_benchmark_and_judge_preserved_on_enter(self, isolated_state_dir):
        # Operator iterates from a YAML with benchmark + judge already
        # configured.  Pressing Enter through each gate prompt — whose
        # default reflects the prior ``enabled`` state — keeps the
        # block intact; inner prompts also default to the loaded values
        # so a bare-Enter rerun produces a YAML byte-equivalent to the
        # input.  Pin the keep-on-Enter contract.
        state = wizard._WizardState(
            config={
                "evaluation": {
                    "auto_revert": True,
                    "max_acceptable_loss": 1.5,
                    "benchmark": {"enabled": True, "tasks": ["mmlu", "arc_easy"], "min_score": 0.6},
                    "llm_judge": {"enabled": True, "judge_model": "gpt-4o", "min_score": 7.0},
                }
            }
        )
        with patch(
            "builtins.input",
            side_effect=_input_returning(
                "",  # auto-revert gate (default=True — existing has it)
                "",  # max_acceptable_loss (existing 1.5)
                "n",  # safety eval (no existing block — default-False)
                "",  # benchmark gate (default=True — existing.tasks set)
                "",  # benchmark tasks (default "mmlu, arc_easy")
                "",  # benchmark min_score (default 0.6)
                "",  # judge gate (default=True — existing.judge_model set)
                "",  # judge_model (default "gpt-4o")
                "",  # judge_api_key_env (default "OPENAI_API_KEY")
                "",  # judge min_score (default 7.0)
                "n",  # webhook
                "n",  # synthetic
            ),
        ):
            wizard._orchestrator._step_evaluation(state)
        assert state.config["evaluation"]["benchmark"]["tasks"] == ["mmlu", "arc_easy"]
        assert state.config["evaluation"]["benchmark"]["min_score"] == pytest.approx(0.6)
        assert state.config["evaluation"]["llm_judge"]["judge_model"] == "gpt-4o"
        assert state.config["evaluation"]["llm_judge"]["min_score"] == pytest.approx(7.0)

    def test_existing_benchmark_and_judge_dropped_on_explicit_no(self, isolated_state_dir):
        # Operator explicitly disables previously-enabled gates on a
        # rerun.  The previous "decline = preserve" semantics made it
        # impossible to turn a feature OFF via the wizard; a typed "n"
        # now drops the block from the rebuild.  Pin the explicit-
        # disable contract.
        state = wizard._WizardState(
            config={
                "evaluation": {
                    "benchmark": {"enabled": True, "tasks": ["mmlu"]},
                    "llm_judge": {"enabled": True, "judge_model": "gpt-4o"},
                }
            }
        )
        with patch(
            "builtins.input",
            side_effect=_input_returning(
                "n",  # auto-revert
                "n",  # safety
                "n",  # benchmark — explicit decline
                "n",  # judge — explicit decline
                "n",  # webhook
                "n",  # synthetic
            ),
        ):
            wizard._orchestrator._step_evaluation(state)
        assert "benchmark" not in state.config["evaluation"]
        assert "llm_judge" not in state.config["evaluation"]

    def test_auto_revert_default_reflects_existing(self, isolated_state_dir, capsys):
        state = wizard._WizardState(config={"evaluation": {"auto_revert": True, "max_acceptable_loss": 1.5}})
        with patch(
            "builtins.input",
            side_effect=_input_returning(
                "n",  # answer "no" to auto-revert (override the True default)
                "n",  # safety
                "n",  # benchmark
                "n",  # judge
                "n",  # webhook
                "n",  # synthetic
            ),
        ):
            wizard._orchestrator._step_evaluation(state)
        # Operator explicitly answered "no" to the auto-revert prompt
        # whose default reflected the existing ``True`` value.  Pin the
        # explicit-disable contract: a "no" must override the loaded
        # state, leaving auto_revert disabled (False) on the rebuild.
        assert state.config["evaluation"].get("auto_revert") is False


class TestPRDA3UseCaseSkipsWhenExisting:
    """PR-D-A3 — _step_use_case skips the use-case prompt under start-from path."""

    def test_existing_model_and_trainer_trigger_skip(self, isolated_state_dir, capsys):
        state = wizard._WizardState(
            use_case=wizard._MANUAL_USE_CASE,
            config={
                "model": {"name_or_path": "Qwen/Qwen2.5-7B-Instruct"},
                "training": {"trainer_type": "dpo"},
            },
        )
        # No input expected — function should early-return without prompting.
        with patch("builtins.input", side_effect=_input_returning()):
            wizard._orchestrator._step_use_case(state)
        captured = capsys.readouterr().out
        assert "Existing model / trainer choices detected" in captured
        # Existing values must remain unchanged.
        assert state.config["model"]["name_or_path"] == "Qwen/Qwen2.5-7B-Instruct"
        assert state.config["training"]["trainer_type"] == "dpo"


class TestPRDA4WelcomeBackendSetdefault:
    """PR-D-A4 — _step_welcome respects existing model.backend."""

    def test_existing_backend_survives(self, monkeypatch):
        state = wizard._WizardState(config={"model": {"backend": "transformers"}})
        # Force the "Linux + GPU" branch which would otherwise suggest unsloth.
        monkeypatch.setattr(wizard._orchestrator, "_PLATFORM", "linux")
        monkeypatch.setattr(
            wizard._orchestrator,
            "_detect_hardware",
            lambda: {"gpu_available": True, "gpu_name": "test", "vram_gb": 24, "cuda_version": "12.1"},
        )
        with patch("builtins.input", side_effect=_input_returning("n")):
            wizard._orchestrator._step_welcome(state)
        # Pre-fix this would have been clobbered to "unsloth".
        assert state.config["model"]["backend"] == "transformers"


class TestPRDA5ImportErrorBypass:
    """PR-D-A5 — schema validation runs even when the import path is split."""

    def test_validation_runs_when_config_imports_fine(self, tmp_path, minimal_config):
        # Verify that an end-to-end load + validate completes cleanly
        # for a valid YAML — the split try/except shouldn't have
        # changed happy-path behaviour.
        import yaml as _yaml

        cfg = tmp_path / "valid.yaml"
        cfg.write_text(_yaml.safe_dump(minimal_config()), encoding="utf-8")
        state = wizard._orchestrator._load_initial_state_from_yaml(str(cfg))
        assert state.use_case == wizard._MANUAL_USE_CASE


class TestPRDA6ResumeStateWarning:
    """PR-D-A6 — start_from path warns about an existing resume snapshot."""

    def test_warns_when_snapshot_exists(self, tmp_path, minimal_config, capsys, monkeypatch):
        import sys as _sys

        import yaml as _yaml

        # Seed a resume snapshot via the public API.
        wizard._save_wizard_state(
            {
                "experience": "expert",
                "use_case": "custom",
                "current_step": 3,
                "completed_steps": ["welcome", "use-case", "model"],
                "config": {"model": {"name_or_path": "x"}},
            }
        )
        # Now supply a start_from that triggers the warning.
        cfg = tmp_path / "iter.yaml"
        cfg.write_text(_yaml.safe_dump(minimal_config()), encoding="utf-8")
        monkeypatch.setattr(_sys.stdin, "isatty", lambda: True)
        # Mock _drive_wizard_steps to short-circuit so the test doesn't
        # walk the full wizard.
        monkeypatch.setattr(
            wizard._orchestrator,
            "_drive_wizard_steps",
            lambda s: s,
        )
        # Provide enough inputs for the post-drive save flow.
        with patch(
            "builtins.input",
            side_effect=_input_returning(str(cfg), "y", "n"),
        ):
            wizard.run_wizard_full(start_from=str(cfg))
        captured = capsys.readouterr().out
        assert "In-progress wizard snapshot detected" in captured


class TestPRDB3StartFromWithoutWizardWarns:
    """PR-D-B3 — --wizard-start-from without --wizard prints a warning."""

    def test_warning_emitted(self, capsys):
        from forgelm.cli._wizard import _maybe_run_wizard

        class _Args:
            wizard = False
            wizard_start_from = "/some/path.yaml"

        _maybe_run_wizard(_Args())
        captured = capsys.readouterr().out
        assert "--wizard-start-from has no effect without --wizard" in captured


class TestPRDB5GovernanceHonorsExisting:
    """PR-D-B5 — _collect_data_governance defaults to existing free-text values."""

    def test_non_strict_existing_values_preserved(self):
        existing = {
            "collection_method": "manual review",
            "annotation_process": "internal team",
            "known_biases": "geographic skew",
            "personal_data_included": True,
            "dpia_completed": True,
        }
        with patch(
            "builtins.input",
            side_effect=_input_returning(
                "y",  # configure governance? (default now True since existing populated)
                "",  # collection_method (= existing)
                "",  # annotation_process (= existing)
                "",  # known_biases (= existing)
                "",  # personal_data_included (= existing True)
                "",  # dpia_completed (= existing True)
            ),
        ):
            result = wizard._collect_data_governance(mandatory=False, existing=existing)
        assert result["collection_method"] == "manual review"
        assert result["annotation_process"] == "internal team"
        assert result["known_biases"] == "geographic skew"
        assert result["personal_data_included"] is True
        assert result["dpia_completed"] is True


class TestPRDB6ExpanduserCanonicalisation:
    """PR-D-B6 — start_from path is canonicalised with ~ expansion at entry."""

    def test_canonical_helper_expands_tilde(self):
        result = wizard._orchestrator._canonical_start_from("~/configs/my_config.yaml")
        assert "~" not in result
        # The expanded path must be absolute, but the leading-slash
        # check is POSIX-only — Windows absolute paths look like
        # ``C:\Users\foo\configs\...`` (no leading slash).  Use
        # ``os.path.isabs`` for cross-platform absoluteness.
        assert os.path.isabs(result)


class TestWizardCancelToken:
    """F-P7-OPUS-05 — `cancel`/`q` honoured at every prompt, exits cleanly."""

    def test_cancel_token_in_step_machine_returns_cancelled_outcome(self, monkeypatch, isolated_state_dir):
        # Force a TTY so the full-wizard path is taken, decline the
        # quickstart prelude, then type `cancel` at the very first
        # step-machine prompt.  The welcome banner promises this exits
        # cleanly; assert a cancelled outcome rather than a corrupted config.
        import sys as _sys

        monkeypatch.setattr(_sys.stdin, "isatty", lambda: True)
        with patch(
            "builtins.input",
            side_effect=_input_returning(
                "n",  # decline the quickstart-template prelude
                "cancel",  # first step-machine prompt → WizardCancel
            ),
        ):
            outcome = wizard.run_wizard_full()
        assert outcome.cancelled is True
        assert outcome.config_path is None

    def test_cancel_token_in_byod_prelude_falls_back_to_full_wizard(self):
        # The BYOD prelude still treats `cancel` as "fall back to the full
        # wizard" (its long-standing contextual meaning), proving the new
        # WizardCancel raise does not break that flow.
        with patch(
            "builtins.input",
            side_effect=_input_returning("y", "domain-expert", "cancel"),
        ):
            result = wizard._maybe_run_quickstart_template()
        assert result is None


class TestWizardPreludeNavigation:
    """F-P7-OPUS-08 — back/reset/cancel in the prelude never crash the CLI."""

    def test_back_in_quickstart_prelude_does_not_crash(self, monkeypatch, isolated_state_dir):
        import sys as _sys

        monkeypatch.setattr(_sys.stdin, "isatty", lambda: True)
        # Accept the prelude offer, then type `back` at the template-pick
        # prompt.  Pre-fix this raised an UNCAUGHT WizardBack (exit 1
        # traceback); now it falls through to the full wizard.  Feed a
        # follow-up `cancel` so the full wizard terminates deterministically.
        with patch(
            "builtins.input",
            side_effect=_input_returning("y", "back", "n", "cancel"),
        ):
            outcome = wizard.run_wizard_full()
        # Must be a clean WizardOutcome, never a raised WizardBack/Reset.
        assert isinstance(outcome, wizard.WizardOutcome)

    def test_reset_in_quickstart_prelude_does_not_crash(self, monkeypatch, isolated_state_dir):
        import sys as _sys

        monkeypatch.setattr(_sys.stdin, "isatty", lambda: True)
        with patch(
            "builtins.input",
            side_effect=_input_returning("y", "reset", "n", "cancel"),
        ):
            outcome = wizard.run_wizard_full()
        assert isinstance(outcome, wizard.WizardOutcome)


class TestWizardJsonModeSeam:
    """F-P7-OPUS-06 / -07 — wizard CLI seam honours --output-format json."""

    def _args(self, **overrides):
        defaults = {
            "wizard": False,
            "wizard_start_from": None,
            "config": None,
            "output_format": "text",
        }
        defaults.update(overrides)
        return type("Args", (), defaults)()

    def test_wizard_json_mode_emits_envelope_and_exits_cancelled(self, capsys):
        from forgelm.cli._wizard import _maybe_run_wizard

        with pytest.raises(SystemExit) as exc:
            _maybe_run_wizard(self._args(wizard=True, output_format="json"))
        assert exc.value.code == wizard_exit_codes.EXIT_WIZARD_CANCELLED
        out = capsys.readouterr()
        # Every line of stdout must be valid JSON with success=False.
        payload = json.loads(out.out)
        assert payload["success"] is False
        assert "json" in payload["error"].lower()
        # The interactive prompts/banner must NOT have leaked to stdout.
        assert "Welcome" not in out.out

    def test_wizard_start_from_warning_goes_to_stderr_in_json_mode(self, capsys):
        from forgelm.cli._wizard import _maybe_run_wizard

        # --wizard-start-from without --wizard, in JSON mode: the guidance
        # warning must land on stderr so it never precedes the dispatcher's
        # JSON envelope on stdout (F-P7-OPUS-07).
        _maybe_run_wizard(self._args(wizard=False, wizard_start_from="x.yaml", output_format="json"))
        out = capsys.readouterr()
        assert out.out == ""  # nothing on the data stream
        assert "no effect without --wizard" in out.err

    def test_wizard_start_from_warning_goes_to_stdout_in_text_mode(self, capsys):
        from forgelm.cli._wizard import _maybe_run_wizard

        _maybe_run_wizard(self._args(wizard=False, wizard_start_from="x.yaml", output_format="text"))
        out = capsys.readouterr()
        assert "no effect without --wizard" in out.out
