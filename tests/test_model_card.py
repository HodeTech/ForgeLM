"""Unit tests for model card generation."""

import os

from forgelm.config import ForgeConfig
from tests._helpers.factories import minimal_config


def _card_config(**overrides):
    """Model-card-specific defaults (DoRA enabled, custom model name) on top
    of the shared ``minimal_config`` factory. Module-local because the
    surrounding tests assert on these specific values (e.g. ``"dora"`` tag in
    frontmatter, ``"org/test-model"`` substring in the rendered card).
    """
    data = minimal_config(
        model={"name_or_path": "org/test-model"},
        lora={"r": 16, "alpha": 32, "use_dora": True},
        training={"num_train_epochs": 3},
    )
    data.update(overrides)
    return data


class TestGenerateModelCard:
    def test_generates_readme(self, tmp_path):
        from forgelm.model_card import generate_model_card

        config = ForgeConfig(**_card_config())
        final_path = str(tmp_path / "model")
        card_path = generate_model_card(
            config=config,
            metrics={"eval_loss": 1.25, "train_loss": 0.8},
            final_path=final_path,
        )
        assert os.path.isfile(card_path)
        assert card_path.endswith("README.md")

        content = open(card_path).read()
        assert "org/test-model" in content
        assert "eval_loss" in content
        assert "ForgeLM" in content

    def test_includes_benchmark_section(self, tmp_path):
        from forgelm.model_card import generate_model_card

        config = ForgeConfig(**_card_config())
        final_path = str(tmp_path / "model")
        card_path = generate_model_card(
            config=config,
            metrics={"eval_loss": 0.5},
            final_path=final_path,
            benchmark_scores={"arc_easy": 0.72, "hellaswag": 0.55},
            benchmark_average=0.635,
        )
        content = open(card_path).read()
        assert "Benchmark" in content
        assert "arc_easy" in content
        assert "0.72" in content

    def test_no_benchmark_section_when_none(self, tmp_path):
        from forgelm.model_card import generate_model_card

        config = ForgeConfig(**_card_config())
        final_path = str(tmp_path / "model")
        card_path = generate_model_card(
            config=config,
            metrics={"eval_loss": 0.5},
            final_path=final_path,
        )
        content = open(card_path).read()
        assert "Benchmark Results" not in content

    def test_excludes_auth_from_config(self, tmp_path):
        from forgelm.model_card import generate_model_card

        config = ForgeConfig(**_card_config(auth={"hf_token": "hf_SECRET"}))
        final_path = str(tmp_path / "model")
        card_path = generate_model_card(
            config=config,
            metrics={},
            final_path=final_path,
        )
        content = open(card_path).read()
        assert "hf_SECRET" not in content

    def test_model_card_escapes_injection_in_base_model_and_dataset(self, tmp_path):
        """F-P4-OPUS-30: a crafted base_model / dataset string must not inject a
        Markdown link or break the card's tables. Injection characters are
        stripped from the interpolated fields, path-legal characters
        (``/ . -``) survive."""
        from forgelm.model_card import generate_model_card

        config = ForgeConfig(
            **_card_config(
                model={"name_or_path": "org/model](https://evil.example)#"},
                data={"dataset_name_or_path": "ds | extra-col | x"},
            )
        )
        final_path = str(tmp_path / "model")
        card_path = generate_model_card(config=config, metrics={"eval_loss": 0.5}, final_path=final_path)
        content = open(card_path).read()

        # Inspect only the interpolated regions (heading + Training-Details
        # table), NOT the fenced ```yaml config block where the raw value is
        # safely quoted inside a code fence and cannot form Markdown.
        heading = next(ln for ln in content.splitlines() if ln.startswith("# "))
        base_row = next(ln for ln in content.splitlines() if ln.startswith("| Base Model"))
        dataset_row = next(ln for ln in content.splitlines() if ln.startswith("| Dataset"))

        # No link-forming sequence reaches the heading or the table cells.
        for region in (heading, base_row, dataset_row):
            assert "](" not in region
            assert "(http" not in region
        # The injected pipes that would add phantom table columns are gone — a
        # normal 2-column row has exactly 3 pipes (its own borders).
        assert dataset_row.count("|") == 3
        # Path-legal text is preserved.
        assert "org/model" in base_row
        assert "extra-col" in dataset_row

    def test_dora_tag_in_frontmatter(self, tmp_path):
        from forgelm.model_card import generate_model_card

        config = ForgeConfig(**_card_config())
        final_path = str(tmp_path / "model")
        card_path = generate_model_card(
            config=config,
            metrics={},
            final_path=final_path,
        )
        content = open(card_path).read()
        assert "dora" in content.lower()

    def test_empty_metrics(self, tmp_path):
        from forgelm.model_card import generate_model_card

        config = ForgeConfig(**_card_config())
        final_path = str(tmp_path / "model")
        card_path = generate_model_card(
            config=config,
            metrics={},
            final_path=final_path,
        )
        assert os.path.isfile(card_path)

    def test_webhook_url_excluded_from_model_card(self, tmp_path):
        """Webhook URLs must not appear in the generated model card YAML config block."""
        from forgelm.model_card import generate_model_card

        secret_url = "https://hooks.slack.com/services/SECRET_TOKEN/MORE_SECRET"
        config = ForgeConfig(**_card_config(webhook={"url": secret_url}))
        final_path = str(tmp_path / "model")
        card_path = generate_model_card(
            config=config,
            metrics={"eval_loss": 0.5},
            final_path=final_path,
        )
        content = open(card_path).read()
        assert secret_url not in content, "Webhook URL must not appear in model card"
        assert "SECRET_TOKEN" not in content

    def test_synthetic_api_key_not_in_model_card(self, tmp_path):
        """C5/F-P1-FAB-01: a populated ``synthetic.api_key`` must NOT land in the
        rendered README. The nested ``SyntheticConfig.model_dump`` redaction is
        bypassed on the parent-serialization path, so the card relies on the
        section ``exclude`` + recursive secret masking instead."""
        from forgelm.model_card import generate_model_card

        config = ForgeConfig(
            **_card_config(
                synthetic={
                    "enabled": True,
                    "teacher_model": "gpt-4",
                    "api_key": "sk-SUPERSECRET-LEAK",
                    "seed_prompts": ["q"],
                }
            )
        )
        final_path = str(tmp_path / "model")
        card_path = generate_model_card(config=config, metrics={"eval_loss": 0.5}, final_path=final_path)
        content = open(card_path).read()
        assert "sk-SUPERSECRET-LEAK" not in content, "synthetic.api_key leaked into the model card"

    def test_target_modules_pipe_neutralized(self, tmp_path):
        """F-L-19: a target_modules entry containing '|' must not inject a phantom
        column into the Training Details table. A normal 2-column row has exactly
        3 pipe characters; an un-neutralized '|' inside the cell value produces 4."""
        from forgelm.model_card import generate_model_card

        config = ForgeConfig(
            **_card_config(
                lora={"r": 16, "alpha": 32, "use_dora": True, "target_modules": ["q_proj", "v_proj|evil"]},
            )
        )
        final_path = str(tmp_path / "model")
        card_path = generate_model_card(config=config, metrics={"eval_loss": 0.5}, final_path=final_path)
        content = open(card_path).read()

        target_row = next(ln for ln in content.splitlines() if ln.startswith("| Target Modules"))
        # A 2-column table row must have exactly 3 pipe characters.
        assert target_row.count("|") == 3, f"Phantom column injected via '|' in target_modules: {target_row!r}"
        # The safe portion of the module name is preserved.
        assert "v_projdevil" not in target_row or "v_proj" in target_row

    def test_residual_secret_keyed_value_is_masked(self, tmp_path):
        """Defence-in-depth: the recursive masker redacts any secret-keyed value
        in the serialized config while keeping ``*_env`` env-var-NAME references."""
        from forgelm.model_card import _redact_secrets

        out = _redact_secrets(
            {
                "evaluation": {"judge_api_key": "SECRET2", "judge_api_key_env": "OPENAI_API_KEY"},
                "nested": [{"password": "p4ss"}, {"note": "keep"}],
            }
        )
        assert out["evaluation"]["judge_api_key"] == "***REDACTED***"
        assert out["evaluation"]["judge_api_key_env"] == "OPENAI_API_KEY"  # env-name, not a secret
        assert out["nested"][0]["password"] == "***REDACTED***"
        assert out["nested"][1]["note"] == "keep"

    def test_readme_written_with_utf8_encoding(self, tmp_path, monkeypatch):
        """The model card README must be opened with ``encoding='utf-8'`` so a
        non-ASCII operator field cannot crash the write on a non-UTF-8-default
        host. Fails on the old default-encoding ``open(...,"w")``."""
        import builtins

        from forgelm.model_card import generate_model_card

        recorded = {}
        real_open = builtins.open

        def _spy_open(file, mode="r", *args, **kwargs):
            if str(file).endswith("README.md") and "w" in mode:
                recorded["encoding"] = kwargs.get("encoding")
            return real_open(file, mode, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", _spy_open)
        config = ForgeConfig(**_card_config(data={"dataset_name_or_path": "veri/çğşöü"}))
        generate_model_card(config=config, metrics={"eval_loss": 0.5}, final_path=str(tmp_path / "m"))

        assert recorded.get("encoding") == "utf-8"


class TestConfigYamlFenceContainment:
    """The config-dump YAML block must contain operator-controlled free-text
    fields regardless of nesting depth — an explicit, tested invariant rather
    than an emergent consequence of PyYAML's indentation."""

    def test_codefence_for_outgrows_longest_backtick_run(self):
        from forgelm.model_card import _codefence_for

        assert _codefence_for("no backticks here") == "```"  # minimum fence
        assert _codefence_for("a ``` b") == "````"  # 3-run -> 4-backtick fence
        assert _codefence_for("x ````` y") == "``````"  # 5-run -> 6-backtick fence

    def test_freetext_field_cannot_break_out_of_code_fence(self, tmp_path):
        """A crafted ``risk_assessment.intended_use`` carrying its own ```` ``` ````
        run must stay trapped inside the config fence. Fails on the old fixed
        3-backtick fence (3 > 3 is False), passes once the fence dynamically
        outgrows the payload's backtick run."""
        from forgelm.model_card import generate_model_card

        payload = "see docs\n```\n## Injected Heading\n```yaml\nmalicious: true"
        config = ForgeConfig(**_card_config(risk_assessment={"intended_use": payload}))
        card_path = generate_model_card(config=config, metrics={"eval_loss": 0.5}, final_path=str(tmp_path / "m"))
        lines = open(card_path, encoding="utf-8").read().splitlines()

        # Opening fence: a run of >=3 backticks immediately followed by "yaml".
        open_idx = next(i for i, ln in enumerate(lines) if ln.startswith("```") and ln.lstrip("`") == "yaml")
        fence = lines[open_idx][: len(lines[open_idx]) - len("yaml")]
        close_idx = next(i for i in range(open_idx + 1, len(lines)) if lines[i] == fence)
        body = lines[open_idx + 1 : close_idx]

        def _longest_backtick_run(text):
            longest = run = 0
            for ch in text:
                run = run + 1 if ch == "`" else 0
                longest = max(longest, run)
            return longest

        # The injected heading is trapped inside the fenced block ...
        assert any("## Injected Heading" in ln for ln in body)
        outside = lines[:open_idx] + lines[close_idx + 1 :]
        assert not any(ln.strip() == "## Injected Heading" for ln in outside)
        # ... because the outer fence is strictly longer than any backtick run it
        # wraps (3 > 3 would be False on the old fixed-3-backtick fence).
        assert len(fence) > _longest_backtick_run("\n".join(body))
