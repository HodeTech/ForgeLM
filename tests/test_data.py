"""Tests for forgelm.data — format processing + column validation (F-P8-C-03).

Before this package no test_data.py existed: test_data_edge_cases.py, despite
its name, mostly tests config and only touches ``_detect_dataset_format`` /
``_ensure_validation_split``. The transformation core — ``clean_string``,
``_process_messages_format``, ``_process_user_assistant_format``,
``_validate_trainer_columns`` (the user's FIRST failure surface for a malformed
JSONL), ``_apply_mix_ratio``, ``_merge_extra_datasets`` — had zero behavioural
coverage; ``prepare_dataset`` was replaced by a lambda in both pipeline tests.

These tests run the real bodies (no network, no GPU) so a regression that
swallows a malformed-row error, mis-detects format, or drops the configured
mix_ratio fails CI. Uses real ``datasets.Dataset`` fixtures per the testing
standard (do not mock forgelm-internal logic that has a fast real impl).
"""

from __future__ import annotations

import pytest

from forgelm import data as data_mod


class TestCleanString:
    def test_collapses_whitespace_when_enabled(self):
        assert data_mod.clean_string("  a   b\tc ", do_clean=True) == "a b c"

    def test_preserves_when_disabled(self):
        assert data_mod.clean_string("  a   b ", do_clean=False) == "  a   b "

    @pytest.mark.parametrize("bad", [None, 42, {"a": 1}, ["x"]])
    def test_non_string_payload_raises(self, bad):
        # Symmetric with _process_messages_format: a dict/int/None where a
        # string was expected is a schema bug, not training data.
        with pytest.raises(ValueError, match="expected a string"):
            data_mod.clean_string(bad, do_clean=True)


class TestDetectDatasetFormat:
    @pytest.mark.parametrize(
        "columns,trainer",
        [
            (["chosen", "rejected"], "dpo"),
            (["completion", "label"], "kto"),
            (["messages"], "sft"),
            (["prompt"], "grpo"),
            (["instruction", "output"], "sft"),
            (["text"], "sft"),
        ],
    )
    def test_format_detection(self, columns, trainer):
        assert data_mod._detect_dataset_format(columns)["suggested_trainer"] == trainer

    def test_unknown_columns_default_to_sft(self):
        out = data_mod._detect_dataset_format(["foo", "bar"])
        assert out["suggested_trainer"] == "sft"
        assert "unknown format" in out["description"]


class TestProcessMessagesFormat:
    def test_valid_rows_formatted(self):
        examples = {"messages": [[{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]]}
        out = data_mod._process_messages_format(examples, add_eos=True, eos_token="</s>")
        assert out["text"][0] == "[USER]\nhi\n[ASSISTANT]\nyo\n</s>"

    def test_non_string_content_raises(self):
        examples = {"messages": [[{"role": "user", "content": {"oops": 1}}]]}
        with pytest.raises(ValueError, match="row at index 0"):
            data_mod._process_messages_format(examples, add_eos=False, eos_token="")

    def test_missing_role_key_raises(self):
        examples = {"messages": [[{"content": "hi"}]]}
        with pytest.raises(ValueError, match="row at index 0"):
            data_mod._process_messages_format(examples, add_eos=False, eos_token="")


class TestProcessUserAssistantFormat:
    def test_missing_assistant_column_raises_keyerror(self):
        examples = {"User": ["hi"]}
        with pytest.raises(KeyError, match="Assistant"):
            data_mod._process_user_assistant_format(examples, clean_text=True, add_eos=False, eos_token="")

    def test_valid_rows_formatted(self):
        examples = {"User": ["hi"], "Assistant": ["yo"]}
        out = data_mod._process_user_assistant_format(examples, clean_text=True, add_eos=False, eos_token="")
        assert out["text"][0] == "[USER]\nhi\n[ASSISTANT]\nyo"

    def test_malformed_cell_raises_with_index(self):
        examples = {"User": ["hi", 42], "Assistant": ["yo", "ok"]}
        with pytest.raises(ValueError, match="row at index 1"):
            data_mod._process_user_assistant_format(examples, clean_text=True, add_eos=False, eos_token="")

    def test_mismatched_column_lengths_raises_actionable_error(self):
        # Regression: zip(..., strict=True) used to raise its generic
        # "zip() argument N is longer/shorter than argument M" message from
        # inside the `for` statement's implicit next() call — outside the
        # try/except that wraps every other malformed-row shape in this
        # function. A length mismatch must now surface the module's own
        # actionable message instead.
        examples = {"User": ["hi", "there"], "Assistant": ["yo"]}
        with pytest.raises(ValueError, match="mismatched lengths"):
            data_mod._process_user_assistant_format(examples, clean_text=True, add_eos=False, eos_token="")


class TestValidateTrainerColumns:
    def test_dpo_missing_chosen_rejected_raises(self):
        fmt = data_mod._detect_dataset_format(["text"])
        with pytest.raises(KeyError, match="DPO trainer requires"):
            data_mod._validate_trainer_columns("dpo", ["text"], fmt, has_chosen_rejected=False, has_kto_format=False)

    def test_kto_missing_columns_raises(self):
        fmt = data_mod._detect_dataset_format(["text"])
        with pytest.raises(KeyError, match="KTO trainer requires"):
            data_mod._validate_trainer_columns("kto", ["text"], fmt, has_chosen_rejected=False, has_kto_format=False)

    def test_grpo_missing_prompt_raises(self):
        fmt = data_mod._detect_dataset_format(["text"])
        with pytest.raises(KeyError, match="GRPO trainer requires a"):
            data_mod._validate_trainer_columns("grpo", ["text"], fmt, has_chosen_rejected=False, has_kto_format=False)

    def test_valid_dpo_schema_passes(self):
        fmt = data_mod._detect_dataset_format(["chosen", "rejected"])
        # No raise = OK.
        data_mod._validate_trainer_columns(
            "dpo", ["prompt", "chosen", "rejected"], fmt, has_chosen_rejected=True, has_kto_format=False
        )

    def test_sft_never_validates_preference_columns(self):
        fmt = data_mod._detect_dataset_format(["text"])
        data_mod._validate_trainer_columns("sft", ["text"], fmt, has_chosen_rejected=False, has_kto_format=False)


class TestMixRatio:
    def _ds(self, n):
        from datasets import Dataset

        return Dataset.from_list([{"text": f"row{i}"} for i in range(n)])

    def test_ratio_honoured(self):
        # 100% weight on the first dataset, 0% on the second → second sampled to
        # zero rows (int(max_size * 0) == 0).
        train = [self._ds(10), self._ds(10)]
        out = data_mod._apply_mix_ratio(train, [1, 0])
        assert len(out[0]) == 10
        assert len(out[1]) == 0

    def test_zero_total_weight_falls_back_to_uniform(self, caplog):
        import logging

        train = [self._ds(4), self._ds(4)]
        with caplog.at_level(logging.WARNING, logger="forgelm.data"):
            out = data_mod._apply_mix_ratio(train, [0, 0])
        assert out is train  # returned unchanged
        assert any("sum to 0" in r.message for r in caplog.records)


class TestMergeExtraDatasets:
    def _dd(self, n):
        from datasets import Dataset, DatasetDict

        return DatasetDict({"train": Dataset.from_list([{"text": f"r{i}"} for i in range(n)])})

    def test_mix_ratio_length_mismatch_raises(self):
        primary = self._dd(4)
        # extra_paths empty → all_train has 1 entry, but mix_ratio has 2 → loud raise.
        with pytest.raises(ValueError, match="does not match dataset count"):
            data_mod._merge_extra_datasets(primary, extra_paths=[], mix_ratio=[1, 1])

    def test_no_extra_concatenates_primary_only(self):
        primary = self._dd(3)
        merged = data_mod._merge_extra_datasets(primary, extra_paths=[], mix_ratio=None)
        assert len(merged["train"]) == 3


class TestEnsureValidationSplit:
    def _dd(self, n):
        from datasets import Dataset, DatasetDict

        return DatasetDict({"train": Dataset.from_list([{"text": f"r{i}"} for i in range(n)])})

    def test_creates_validation_when_absent(self):
        out = data_mod._ensure_validation_split(self._dd(50))
        assert "validation" in out
        assert len(out["validation"]) > 0

    def test_single_row_skips_split(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING, logger="forgelm.data"):
            out = data_mod._ensure_validation_split(self._dd(1))
        assert "validation" not in out
        assert any("cannot create a validation split" in r.message for r in caplog.records)

    def test_native_test_split_is_popped_not_aliased(self):
        # Regression: aliasing (dataset["validation"] = dataset["test"])
        # without removing "test" left the DatasetDict with three keys
        # (train/test/validation), causing every downstream per-split loop
        # (_shuffle_and_passthrough, _format_sft_dataset) to process the
        # same rows twice. The native "test" split must be popped, not
        # merely referenced under a second key.
        from datasets import Dataset, DatasetDict

        ds = DatasetDict(
            {
                "train": Dataset.from_list([{"text": f"r{i}"} for i in range(5)]),
                "test": Dataset.from_list([{"text": "t0"}, {"text": "t1"}]),
            }
        )
        out = data_mod._ensure_validation_split(ds)
        assert set(out.keys()) == {"train", "validation"}
        assert len(out["validation"]) == 2


class TestLoadSingleDataset:
    """_load_single_dataset had zero test coverage before this suite —
    the extension-whitelist gate must fail fast, before ever reaching HF
    ``load_dataset``, so an unsupported local extension can't be
    misinterpreted as a Hub dataset id (triggering a surprise network call
    in an otherwise fully local/offline run)."""

    def test_no_extension_raises_actionable_error(self, tmp_path):
        path = tmp_path / "dataset"
        path.write_text("{}")
        with pytest.raises(ValueError, match="no file extension found"):
            data_mod._load_single_dataset(str(path))

    def test_unsupported_extension_raises_before_load_dataset(self, tmp_path, monkeypatch):
        import datasets

        called = False

        def _fail_if_called(*args, **kwargs):
            nonlocal called
            called = True
            raise AssertionError("load_dataset must not be called for an unsupported extension")

        monkeypatch.setattr(datasets, "load_dataset", _fail_if_called)

        path = tmp_path / "dataset.txt"
        path.write_text("hello")
        with pytest.raises(ValueError, match=r"unsupported extension '\.txt'"):
            data_mod._load_single_dataset(str(path))
        assert not called

    def test_unsupported_extension_message_lists_supported_formats(self, tmp_path):
        path = tmp_path / "dataset.tsv"
        path.write_text("a\tb")
        with pytest.raises(ValueError, match=r"\.json, \.jsonl, \.csv, or \.parquet"):
            data_mod._load_single_dataset(str(path))

    @pytest.mark.parametrize(
        "suffix,expected_builder",
        [
            (".json", "json"),
            (".jsonl", "json"),
            (".csv", "csv"),
            (".parquet", "parquet"),
        ],
    )
    def test_supported_extension_dispatches_to_correct_builder(self, tmp_path, monkeypatch, suffix, expected_builder):
        import datasets

        seen = {}

        def _fake_load_dataset(builder, data_files=None):
            seen["builder"] = builder
            seen["data_files"] = data_files
            return "sentinel-dataset"

        monkeypatch.setattr(datasets, "load_dataset", _fake_load_dataset)

        path = tmp_path / f"dataset{suffix}"
        path.write_text("placeholder")
        result = data_mod._load_single_dataset(str(path))

        assert result == "sentinel-dataset"
        assert seen["builder"] == expected_builder
        assert seen["data_files"] == str(path)
