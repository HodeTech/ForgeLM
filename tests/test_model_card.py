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
            **_card_config(synthetic={"enabled": True, "teacher_model": "gpt-4", "api_key": "sk-SUPERSECRET-LEAK"})
        )
        final_path = str(tmp_path / "model")
        card_path = generate_model_card(config=config, metrics={"eval_loss": 0.5}, final_path=final_path)
        content = open(card_path).read()
        assert "sk-SUPERSECRET-LEAK" not in content, "synthetic.api_key leaked into the model card"

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
