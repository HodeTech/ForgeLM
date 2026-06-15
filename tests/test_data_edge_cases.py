"""Unit tests for data.py edge cases (multimodal, mix_ratio zero weight)."""

import pytest
import yaml

from forgelm.config import ForgeConfig


class TestMultimodalConfig:
    def test_multimodal_enabled_in_config(self, minimal_config):
        cfg = ForgeConfig(
            **minimal_config(
                model={
                    "name_or_path": "org/vlm-model",
                    "multimodal": {"enabled": True, "image_column": "img", "text_column": "caption"},
                }
            )
        )
        assert cfg.model.multimodal.enabled is True
        assert cfg.model.multimodal.image_column == "img"

    def test_multimodal_disabled_by_default(self, minimal_config):
        cfg = ForgeConfig(**minimal_config())
        assert cfg.model.multimodal is None

    def test_multimodal_config_from_yaml(self, tmp_path, minimal_config):
        from forgelm.config import load_config

        data = minimal_config(
            model={
                "name_or_path": "org/vlm",
                "multimodal": {"enabled": True},
            }
        )
        cfg_path = str(tmp_path / "config.yaml")
        with open(cfg_path, "w") as f:
            yaml.dump(data, f)
        cfg = load_config(cfg_path)
        assert cfg.model.multimodal.enabled is True


class TestMixRatioEdgeCases:
    def test_zero_weight_config_raises(self, minimal_config):
        """mix_ratio with all zeros must be rejected — meaningless sampling weights."""
        with pytest.raises(Exception, match="mix_ratio values cannot all be zero"):
            ForgeConfig(
                **minimal_config(
                    data={
                        "dataset_name_or_path": "org/dataset",
                        "extra_datasets": ["org/extra"],
                        "mix_ratio": [0.0, 0.0],
                    }
                )
            )

    def test_single_dataset_no_extra(self, minimal_config):
        cfg = ForgeConfig(**minimal_config(data={"dataset_name_or_path": "org/dataset"}))
        assert cfg.data.extra_datasets is None
        assert cfg.data.mix_ratio is None

    def test_merge_extra_datasets_length_mismatch_raises_not_silent(self, monkeypatch):
        """A mix_ratio whose length disagrees with the dataset count used to
        silently fall back to uniform mixing (re-weighting to a mixture the
        caller never asked for).  It must now raise loudly."""
        from forgelm import data as data_mod

        monkeypatch.setattr(data_mod, "_load_single_dataset", lambda path: {"train": [path]})
        primary = {"train": ["primary"]}
        with pytest.raises(ValueError, match="does not match dataset count"):
            # 1 primary + 1 extra = 2 datasets, but only 1 weight given.
            data_mod._merge_extra_datasets(primary, ["org/extra"], mix_ratio=[1.0])


class TestGrpoRewardModelConfig:
    def test_default_none(self, minimal_config):
        cfg = ForgeConfig(**minimal_config(training={"trainer_type": "grpo"}))
        assert cfg.training.grpo_reward_model is None

    def test_custom_reward_model(self, minimal_config):
        cfg = ForgeConfig(
            **minimal_config(
                training={
                    "trainer_type": "grpo",
                    "grpo_reward_model": "org/reward-model",
                }
            )
        )
        assert cfg.training.grpo_reward_model == "org/reward-model"

    def test_grpo_config_from_yaml(self, tmp_path, minimal_config):
        from forgelm.config import load_config

        data = minimal_config(
            training={
                "trainer_type": "grpo",
                "grpo_reward_model": "org/reward",
                "grpo_num_generations": 8,
            }
        )
        cfg_path = str(tmp_path / "config.yaml")
        with open(cfg_path, "w") as f:
            yaml.dump(data, f)
        cfg = load_config(cfg_path)
        assert cfg.training.grpo_reward_model == "org/reward"
        assert cfg.training.grpo_num_generations == 8


class TestWebhookTimeoutConfig:
    def test_default_timeout(self):
        from forgelm.config import WebhookConfig

        # Default raised from 5s → 10s in v0.5.5 (Faz 28 / F-compliance-106).
        # Slack/Teams gateway latency spikes regularly cross 5s in
        # production, and a webhook timeout silently degrades the audit
        # chain (webhook delivery is best-effort).  10s leaves head-room
        # without blocking training-pipeline forward progress.
        w = WebhookConfig()
        assert w.timeout == 10

    def test_custom_timeout(self):
        from forgelm.config import WebhookConfig

        w = WebhookConfig(timeout=15)
        assert w.timeout == 15

    def test_timeout_in_full_config(self, minimal_config):
        cfg = ForgeConfig(**minimal_config(webhook={"url": "https://example.com", "timeout": 10}))
        assert cfg.webhook.timeout == 10


class TestCleanStringRejectsNonString:
    """XP-15 (F-P6-OPUS-01/15): ``clean_string`` must reject non-string
    payloads loudly instead of coercing dict/list/int via ``str()`` or
    mapping ``None``/falsy to ``""`` — symmetric with the messages path
    (``_process_messages_format``), which raises on the same shapes. The
    old behaviour silently baked Python ``repr`` strings and empty
    responses into the training corpus."""

    @pytest.mark.parametrize("payload", [{"nested": "obj"}, ["a", "b"], 42, None, 3.14, True])
    @pytest.mark.parametrize("do_clean", [True, False])
    def test_clean_string_non_string_payload_raises(self, payload, do_clean):
        from forgelm.data import clean_string

        with pytest.raises(ValueError, match="expected a string"):
            clean_string(payload, do_clean)

    @pytest.mark.parametrize("do_clean", [True, False])
    def test_clean_string_valid_string_passes(self, do_clean):
        from forgelm.data import clean_string

        assert clean_string("hello   world", do_clean) == ("hello world" if do_clean else "hello   world")

    def test_clean_string_empty_string_passes(self):
        from forgelm.data import clean_string

        # A genuinely-empty (but valid) string is preserved, not rejected.
        assert clean_string("", True) == ""
        assert clean_string("", False) == ""

    @pytest.mark.parametrize("payload", [{"nested": "obj"}, ["a"], 42, None])
    def test_format_user_assistant_row_non_string_assistant_raises(self, payload):
        from forgelm.data import _format_user_assistant_row

        with pytest.raises(ValueError, match="expected a string"):
            _format_user_assistant_row("", "Q", payload, True, False, "")

    @pytest.mark.parametrize("payload", [0, False, 0.0, [], {}, 42, {"nested": "obj"}, None])
    def test_format_user_assistant_row_non_string_system_raises(self, payload):
        """The System cell must be validated symmetrically with User/Assistant:
        a truthiness gate let falsy non-strings (``0``/``False``/``0.0``/``[]``/
        ``{}``) silently bypass ``clean_string`` and be treated as "missing".
        Only ``""`` means "no system prompt"; everything else must fail loudly."""
        from forgelm.data import _format_user_assistant_row

        with pytest.raises(ValueError, match="expected a string"):
            _format_user_assistant_row(payload, "Q", "A", True, False, "")

    def test_format_user_assistant_row_empty_system_omits_block(self):
        """``sys_text == ""`` is the absent-system path synthesised by
        ``_process_user_assistant_format`` (``[""] * len``); it must still
        render no ``[SYSTEM]`` block rather than being rejected."""
        from forgelm.data import _format_user_assistant_row

        out = _format_user_assistant_row("", "Q", "A", True, False, "")
        assert "[SYSTEM]" not in out

    @pytest.mark.parametrize("ctrl_sys", ["\x00", "\x01\x02\x03", "\x7f\x80", "\x00\x00\x00"])
    def test_format_user_assistant_row_control_char_system_clean_omits_block(self, ctrl_sys):
        """When ``sys_text`` is entirely control characters and ``clean_text=True``,
        ``clean_string`` reduces it to ``""``.  The render gate must use the
        post-cleaning value (``sys_clean``) so no empty ``[SYSTEM]`` block is
        injected — regression guard for F-M-16."""
        from forgelm.data import _format_user_assistant_row

        out = _format_user_assistant_row(ctrl_sys, "Q", "A", True, False, "")
        assert "[SYSTEM]" not in out, f"Empty [SYSTEM] block injected for sys_text={ctrl_sys!r}: {out!r}"

    @pytest.mark.parametrize("ctrl_sys", ["\x00", "\x01\x02\x03"])
    def test_format_user_assistant_row_control_char_system_no_clean_emits_block(self, ctrl_sys):
        """When ``clean_text=False`` the NUL/control chars are kept verbatim and
        the ``[SYSTEM]`` block must still appear (the char is real content)."""
        from forgelm.data import _format_user_assistant_row

        out = _format_user_assistant_row(ctrl_sys, "Q", "A", False, False, "")
        assert "[SYSTEM]" in out, f"[SYSTEM] block missing for unclean sys_text={ctrl_sys!r}: {out!r}"

    @pytest.mark.parametrize("payload", [{"nested": "obj"}, 42, None])
    def test_messages_and_user_assistant_paths_reject_same_payload(self, payload):
        """Parity: the same non-string content shape that the messages
        path rejects must also be rejected by the User/Assistant path."""
        from forgelm.data import _format_user_assistant_row, _process_messages_format

        with pytest.raises(ValueError):
            _process_messages_format(
                {"messages": [[{"role": "user", "content": payload}]]}, add_eos=False, eos_token=""
            )
        with pytest.raises(ValueError):
            _format_user_assistant_row("", "Q", payload, True, False, "")

    def test_process_text_format_non_string_row_raises_with_index(self):
        from forgelm.data import _process_text_format

        with pytest.raises(ValueError, match="index 1"):
            _process_text_format({"text": ["ok", 42]}, clean_text=True, add_eos=False, eos_token="")

    def test_process_user_assistant_format_non_string_row_raises_with_index(self):
        from forgelm.data import _process_user_assistant_format

        with pytest.raises(ValueError, match="index 0"):
            _process_user_assistant_format(
                {"User": ["Q"], "Assistant": [None]}, clean_text=True, add_eos=False, eos_token=""
            )

    def test_process_text_format_valid_rows_still_formatted(self):
        from forgelm.data import _process_text_format

        out = _process_text_format({"text": ["a  b", "c"]}, clean_text=True, add_eos=False, eos_token="")
        assert out == {"text": ["a b", "c"]}

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("hello\x00world\x07test\x1bend", "helloworldtestend"),  # NUL/BEL/ESC stripped
            ("a\x9bb", "ab"),  # C1 control (CSI) stripped
            ("a\x0bb\x0cc", "a b c"),  # whitespace controls collapsed
            ("plain text", "plain text"),  # untouched body
        ],
    )
    def test_clean_string_strips_control_chars_when_cleaning(self, raw, expected):
        """F-P6-OPUS-04: ``clean_text`` is documented as stripping control
        characters, but the old ``" ".join(text.split())`` only collapsed the
        *whitespace* controls — NUL/BEL/ESC and the rest of the C0/C1 ``Cc``
        set passed verbatim into the tokeniser. They must now be removed."""
        from forgelm.data import clean_string

        assert clean_string(raw, True) == expected

    def test_clean_string_preserves_control_chars_when_not_cleaning(self):
        """With ``do_clean=False`` the cell is passed through verbatim (the
        opt-out contract), so control characters are intentionally preserved."""
        from forgelm.data import clean_string

        assert clean_string("a\x00b", False) == "a\x00b"


class TestProcessMessagesFormat:
    """F-P6-OPUS-02 / -05: pin the deliberate loud-raise behaviour of
    ``_process_messages_format`` (the previous swallow-and-substitute-``''``
    behaviour trained the model on a corpus of empty rows). Covers the happy
    path plus every malformed shape, including the empty ``messages`` list
    that used to slip through and emit a blank training string."""

    def test_messages_format_happy_path_formats_role_content(self):
        from forgelm.data import _process_messages_format

        out = _process_messages_format(
            {"messages": [[{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]]},
            add_eos=False,
            eos_token="",
        )
        assert out == {"text": ["[USER]\nhi\n[ASSISTANT]\nyo\n"]}

    def test_messages_format_empty_list_raises_with_index(self):
        from forgelm.data import _process_messages_format

        with pytest.raises(ValueError, match="index 0.*empty"):
            _process_messages_format({"messages": [[]]}, add_eos=False, eos_token="</s>")

    def test_messages_format_empty_list_raises_even_with_eos(self):
        # add_eos must not paper over an empty row with a bare eos_token.
        from forgelm.data import _process_messages_format

        with pytest.raises(ValueError, match="empty"):
            _process_messages_format({"messages": [[]]}, add_eos=True, eos_token="</s>")

    def test_messages_format_mixed_batch_empty_row_raises(self):
        from forgelm.data import _process_messages_format

        with pytest.raises(ValueError, match="index 1"):
            _process_messages_format(
                {"messages": [[{"role": "user", "content": "hi"}], []]},
                add_eos=False,
                eos_token="",
            )

    def test_messages_format_non_str_role_raises(self):
        from forgelm.data import _process_messages_format

        with pytest.raises(ValueError, match="role"):
            _process_messages_format({"messages": [[{"role": 1, "content": "hi"}]]}, add_eos=False, eos_token="")

    def test_messages_format_non_str_content_raises(self):
        from forgelm.data import _process_messages_format

        with pytest.raises(ValueError, match="content"):
            _process_messages_format(
                {"messages": [[{"role": "user", "content": {"x": 1}}]]}, add_eos=False, eos_token=""
            )

    def test_messages_format_non_iterable_msg_list_raises_with_index(self):
        from forgelm.data import _process_messages_format

        with pytest.raises(ValueError, match="index 0"):
            _process_messages_format({"messages": [42]}, add_eos=False, eos_token="")

    def test_messages_format_non_dict_message_raises_with_index(self):
        from forgelm.data import _process_messages_format

        with pytest.raises(ValueError, match="index 0"):
            _process_messages_format({"messages": [["not-a-dict"]]}, add_eos=False, eos_token="")


class TestEnsureValidationSplit:
    """P1-2 regression: ``train_test_split`` on a <2-row dataset raises
    ``ValueError`` because 10% of 1 truncates to 0 test rows.  The guard
    must skip the split and return the dataset unchanged."""

    def test_single_row_dataset_skips_split_and_warns(self, caplog):
        import logging

        from datasets import Dataset, DatasetDict

        from forgelm.data import _ensure_validation_split

        ds = DatasetDict({"train": Dataset.from_list([{"text": "only sample"}])})

        with caplog.at_level(logging.WARNING, logger="forgelm.data"):
            result = _ensure_validation_split(ds)

        assert "validation" not in result, (
            "1-row dataset must not produce a validation split — HF train_test_split crashes on 0-row splits"
        )
        assert len(result["train"]) == 1
        assert any("only 1 sample" in r.getMessage() for r in caplog.records), (
            "Expected a warning that the validation split was skipped"
        )

    def test_empty_dataset_skips_split(self):
        from datasets import Dataset, DatasetDict

        from forgelm.data import _ensure_validation_split

        ds = DatasetDict({"train": Dataset.from_list([])})
        result = _ensure_validation_split(ds)
        assert "validation" not in result

    def test_normal_dataset_still_splits(self):
        from datasets import Dataset, DatasetDict

        from forgelm.data import _ensure_validation_split

        ds = DatasetDict({"train": Dataset.from_list([{"text": f"sample {i}"} for i in range(100)])})
        result = _ensure_validation_split(ds)
        assert "validation" in result, "Datasets large enough must still be split for backwards compatibility"
        assert len(result["train"]) + len(result["validation"]) == 100
