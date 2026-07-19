"""Phase 14 — CLI flag interaction + dispatcher tests.

Covers :func:`forgelm.cli._pipeline.run_pipeline_from_args`, the argparse
glue layer between the top-level parser and
:class:`forgelm.cli._pipeline.PipelineOrchestrator`.  Focus: flag
combinatorial rules + dispatch routing.  The orchestrator's own state
machine has dedicated coverage in ``tests/test_pipeline_orchestrator.py``;
this file pins the *boundary* between argparse and the orchestrator.
"""

from __future__ import annotations

import argparse
import json
import os
import types
from unittest.mock import MagicMock

import pytest

from forgelm.cli._exit_codes import EXIT_CONFIG_ERROR, EXIT_SUCCESS
from forgelm.cli._pipeline import run_pipeline_from_args
from forgelm.config import ForgeConfig
from forgelm.results import TrainResult


def _three_stage_cfg(tmp_path):
    return ForgeConfig(
        model={"name_or_path": "org/base"},
        lora={"r": 8},
        training={"trainer_type": "sft"},
        data={"dataset_name_or_path": "org/data"},
        pipeline={
            "output_dir": str(tmp_path / "pipeline_run"),
            "stages": [
                {
                    "name": "sft_stage",
                    "training": {"trainer_type": "sft", "output_dir": str(tmp_path / "stage1")},
                    "data": {"dataset_name_or_path": "org/sft"},
                },
                {
                    "name": "dpo_stage",
                    "training": {"trainer_type": "dpo", "output_dir": str(tmp_path / "stage2")},
                    "data": {"dataset_name_or_path": "org/dpo"},
                },
                {
                    "name": "grpo_stage",
                    "training": {"trainer_type": "grpo", "output_dir": str(tmp_path / "stage3")},
                    "data": {"dataset_name_or_path": "org/math"},
                },
            ],
        },
    )


def _ns(**overrides):
    """Build an argparse.Namespace with the orchestrator-relevant fields."""
    defaults = {
        "stage": None,
        "resume_from": None,
        "force_resume": False,
        "input_model": None,
        "output_format": "text",
        "dry_run": False,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _install_fake_trainer(monkeypatch, results):
    iterator = iter(results)
    instantiated_configs: list = []

    class _FakeForgeTrainer:
        def __init__(self, *, model, tokenizer, config, dataset):
            instantiated_configs.append(config)
            os.makedirs(os.path.join(config.training.output_dir, "final_model"), exist_ok=True)
            self.config = config

        def train(self, resume_from_checkpoint=None):
            try:
                return next(iterator)
            except StopIteration:
                return TrainResult(success=True)

    fake_trainer = types.ModuleType("forgelm.trainer")
    fake_trainer.ForgeTrainer = _FakeForgeTrainer
    monkeypatch.setitem(__import__("sys").modules, "forgelm.trainer", fake_trainer)

    fake_model = types.ModuleType("forgelm.model")
    fake_model.get_model_and_tokenizer = lambda config: (MagicMock(), MagicMock())
    monkeypatch.setitem(__import__("sys").modules, "forgelm.model", fake_model)

    fake_data = types.ModuleType("forgelm.data")
    fake_data.prepare_dataset = lambda config, tokenizer: {"train": [{"text": "x"}]}
    monkeypatch.setitem(__import__("sys").modules, "forgelm.data", fake_data)

    fake_utils = types.ModuleType("forgelm.utils")
    fake_utils.setup_authentication = lambda token: None
    monkeypatch.setitem(__import__("sys").modules, "forgelm.utils", fake_utils)

    return instantiated_configs


class TestFlagInteractionGuards:
    """Argparse alone cannot express "X and Y are mutually exclusive
    *only when both are non-None*"; the dispatcher enforces it."""

    def test_stage_and_resume_from_mutually_exclusive(self, tmp_path):
        cfg = _three_stage_cfg(tmp_path)
        args = _ns(stage="sft_stage", resume_from="dpo_stage")
        code = run_pipeline_from_args(cfg, b"yaml", args)
        assert code == EXIT_CONFIG_ERROR

    def test_input_model_requires_stage(self, tmp_path):
        cfg = _three_stage_cfg(tmp_path)
        args = _ns(input_model="some/path")
        code = run_pipeline_from_args(cfg, b"yaml", args)
        assert code == EXIT_CONFIG_ERROR

    def test_empty_input_model_normalised_to_none(self, tmp_path, monkeypatch):
        """Phase 14 review F-F-2 regression: ``--input-model ""``
        (empty string) used to slip past the dispatcher's truthy
        check and propagate through ``merge_pipeline_stage_config``
        (``is not None`` branch), silently overwriting the auto-chained
        model path with the empty string.  The orchestrator now
        normalises a falsy ``--input-model`` value to ``None`` before
        dispatch so it never reaches the merge helper.
        """
        cfg = _three_stage_cfg(tmp_path)
        # Pre-create stage 1's output so the chain check on stage 2 is
        # not the failure point — the test asserts the empty override
        # is normalised, not that the chain is broken.
        (tmp_path / "stage1" / "final_model").mkdir(parents=True, exist_ok=True)
        configs_seen = _install_fake_trainer(monkeypatch, [TrainResult(success=True)])
        args = _ns(stage="dpo_stage", input_model="")
        code = run_pipeline_from_args(cfg, b"yaml", args)
        assert code == EXIT_SUCCESS
        # Defensive guard (Sonar python:S1244 / S6395 Critical,
        # post-release review): assert the trainer was instantiated
        # before reaching for ``configs_seen[0]`` below.  If the stage
        # silently exits ``EXIT_SUCCESS`` *without* invoking
        # ``_FakeForgeTrainer`` (e.g. a future dispatch-order change
        # short-circuits the run), the failure should surface as a
        # clear "no stage ran" message rather than as an opaque
        # IndexError on the next line.
        assert configs_seen, (
            "stage 'dpo_stage' should have instantiated the fake trainer "
            "exactly once; got an empty configs_seen list — the orchestrator "
            "exited EXIT_SUCCESS without invoking _FakeForgeTrainer."
        )
        # The stage's input_model must come from the auto-chain (stage
        # 1's on-disk final_model), NOT from the empty string override.
        assert configs_seen[0].model.name_or_path == str(tmp_path / "stage1" / "final_model")

    def test_no_train_single_stage_flags_rejected_on_pipeline_config(self, tmp_path):
        """Phase 14 post-release review BLOCKER 1: pipeline dispatch
        routes BEFORE ``_maybe_run_no_train_mode`` (F-B-1 fix on PR
        #53), so without an explicit reject ``--fit-check`` /
        ``--merge`` / ``--generate-data`` / ``--compliance-export`` /
        ``--benchmark-only`` would silently trigger a full pipeline
        training run instead of the single-stage operation the
        operator asked for.  Each unsupported flag must EXIT_CONFIG_ERROR."""
        cfg = _three_stage_cfg(tmp_path)
        for flag, value in [
            ("fit_check", True),
            ("merge", "./some/checkpoint"),
            ("generate_data", "./data.jsonl"),
            ("compliance_export", "./out"),
            ("benchmark_only", "mmlu"),
        ]:
            args = _ns(**{flag: value})
            code = run_pipeline_from_args(cfg, b"yaml", args)
            assert code == EXIT_CONFIG_ERROR, f"flag {flag!r} should be rejected on pipeline config"

    def test_force_resume_without_resume_from_is_noop(self, tmp_path, monkeypatch):
        """``--force-resume`` without ``--resume-from`` is meaningless
        but not an error — the orchestrator simply ignores it on a
        fresh run.  We pin this so a future overzealous validator
        doesn't start rejecting harmless flag combinations."""
        cfg = _three_stage_cfg(tmp_path)
        _install_fake_trainer(
            monkeypatch, [TrainResult(success=True), TrainResult(success=True), TrainResult(success=True)]
        )
        args = _ns(force_resume=True)
        code = run_pipeline_from_args(cfg, b"yaml", args)
        assert code == EXIT_SUCCESS


class TestDispatchToDryRun:
    def test_dry_run_routes_to_orchestrator_dry_run(self, tmp_path, monkeypatch):
        """``--dry-run`` must take the dry-run branch even when other
        pipeline flags are set — keeps operators from accidentally
        kicking off a real run when they intended to validate."""
        cfg = _three_stage_cfg(tmp_path)
        _install_fake_trainer(monkeypatch, [])
        args = _ns(dry_run=True)
        code = run_pipeline_from_args(cfg, b"yaml", args)
        assert code == EXIT_SUCCESS
        # No state / manifest written by dry-run.
        assert not os.path.exists(tmp_path / "pipeline_run" / "pipeline_state.json")


class TestDispatchToRun:
    def test_full_run_routes_to_orchestrator_run(self, tmp_path, monkeypatch):
        cfg = _three_stage_cfg(tmp_path)
        _install_fake_trainer(monkeypatch, [TrainResult(success=True)] * 3)
        args = _ns()
        code = run_pipeline_from_args(cfg, b"yaml", args)
        assert code == EXIT_SUCCESS
        assert os.path.exists(tmp_path / "pipeline_run" / "pipeline_state.json")

    def test_stage_filter_propagates_to_orchestrator(self, tmp_path, monkeypatch):
        cfg = _three_stage_cfg(tmp_path)
        _install_fake_trainer(monkeypatch, [TrainResult(success=True)])
        args = _ns(stage="sft_stage")
        code = run_pipeline_from_args(cfg, b"yaml", args)
        assert code == EXIT_SUCCESS

    def test_unknown_stage_filter_rejected_via_orchestrator(self, tmp_path, monkeypatch):
        cfg = _three_stage_cfg(tmp_path)
        _install_fake_trainer(monkeypatch, [])
        args = _ns(stage="ghost")
        code = run_pipeline_from_args(cfg, b"yaml", args)
        assert code == EXIT_CONFIG_ERROR


class TestTopLevelDispatchOrdering:
    """Regression for the Phase 14 review F-B-1: the single-stage
    ``--dry-run`` path inside ``_maybe_run_no_train_mode`` MUST NOT run
    before the orchestrator dispatch when the YAML carries a
    ``pipeline:`` block.  Otherwise ``forgelm --config pipeline.yaml
    --dry-run`` falls through to the legacy single-stage dry-run summary
    and the documented per-stage chain-integrity validation never fires.

    Exercises the actual top-level ``main()`` (not just
    ``run_pipeline_from_args``) by writing a YAML to disk and invoking
    the CLI in-process via the installed entry point.
    """

    def test_pipeline_dry_run_routes_to_orchestrator_not_legacy(self, tmp_path, monkeypatch, caplog):
        import logging
        import sys as _sys

        import yaml

        yaml_path = tmp_path / "pipeline.yaml"
        yaml_path.write_text(
            yaml.safe_dump(
                {
                    "model": {"name_or_path": "org/base"},
                    "lora": {},
                    "training": {"trainer_type": "sft"},
                    "data": {"dataset_name_or_path": "org/data"},
                    "pipeline": {
                        "output_dir": str(tmp_path / "pipeline_run"),
                        "stages": [
                            {
                                "name": "sft_stage",
                                "training": {"trainer_type": "sft", "output_dir": str(tmp_path / "s1")},
                                "data": {"dataset_name_or_path": "org/sft"},
                            },
                            {
                                "name": "dpo_stage",
                                "training": {"trainer_type": "dpo", "output_dir": str(tmp_path / "s2")},
                                "data": {"dataset_name_or_path": "org/dpo"},
                            },
                        ],
                    },
                }
            )
        )

        from forgelm.cli._dispatch import main as cli_main

        monkeypatch.setattr(_sys, "argv", ["forgelm", "--config", str(yaml_path), "--dry-run"])
        with caplog.at_level(logging.INFO, logger="forgelm.pipeline"):
            with pytest.raises(SystemExit) as exc_info:
                cli_main()
        # The orchestrator's dry-run exits 0 on a clean validation; the
        # legacy single-stage dry-run would also exit 0 here, so the
        # discriminator below is the orchestrator's INFO log line —
        # ``Pipeline dry-run OK: <n> stage(s) validated`` is emitted
        # only by ``PipelineOrchestrator.dry_run``.  The legacy
        # single-stage dry-run path lives at ``forgelm.cli._dry_run`` and
        # emits a different log surface.
        assert exc_info.value.code == 0
        orchestrator_log_emitted = any("pipeline dry-run ok" in r.message.lower() for r in caplog.records)
        assert orchestrator_log_emitted, (
            "Dispatcher routed --dry-run through the legacy single-stage path "
            "instead of the pipeline orchestrator (Phase 14 F-B-1 regression). "
            f"Captured records: {[r.message for r in caplog.records]!r}"
        )

    def test_pipeline_branch_runs_before_no_train_modes_on_pipeline_config(self, tmp_path, monkeypatch):
        """Structural assertion: importing ``_dispatch`` and following
        the code path on a ``config.pipeline is not None`` config must
        invoke ``run_pipeline_from_args`` strictly before
        ``_maybe_run_no_train_mode``.  Sentinels track the call order."""
        import yaml

        from forgelm.cli import _dispatch

        yaml_path = tmp_path / "pipeline.yaml"
        yaml_path.write_text(
            yaml.safe_dump(
                {
                    "model": {"name_or_path": "org/base"},
                    "lora": {},
                    "training": {"trainer_type": "sft"},
                    "data": {"dataset_name_or_path": "org/data"},
                    "pipeline": {
                        "output_dir": str(tmp_path / "pipeline_run"),
                        "stages": [
                            {
                                "name": "sft_stage",
                                "training": {"trainer_type": "sft", "output_dir": str(tmp_path / "s1")},
                                "data": {"dataset_name_or_path": "org/sft"},
                            },
                        ],
                    },
                }
            )
        )

        call_order: list[str] = []

        def _fake_maybe_no_train(config, args):
            call_order.append("no_train_mode")

        def _fake_run_pipeline(config, yaml_bytes, args):
            call_order.append("pipeline_dispatch")
            return 0

        monkeypatch.setattr(_dispatch, "_maybe_run_no_train_mode", _fake_maybe_no_train)
        import forgelm.cli._pipeline as _pipeline_mod

        monkeypatch.setattr(_pipeline_mod, "run_pipeline_from_args", _fake_run_pipeline)
        # The dispatcher imports run_pipeline_from_args lazily inside
        # main() — patch the function on the source module so the lazy
        # import resolves to our fake.
        import sys

        monkeypatch.setattr(sys, "argv", ["forgelm", "--config", str(yaml_path)])
        with pytest.raises(SystemExit):
            _dispatch.main()
        # Pipeline dispatch must fire; no_train_mode must NOT have run
        # before it.
        assert call_order == ["pipeline_dispatch"], (
            f"Expected pipeline_dispatch first (and only), got {call_order!r}.  "
            f"Phase 14 F-B-1 regression: legacy no-train path executed before "
            f"the pipeline branch on a pipeline config."
        )

    def test_pipeline_only_flags_rejected_on_non_pipeline_config(self, tmp_path, monkeypatch):
        """Phase 14 post-release review HIGH 5: pipeline-only flags
        (``--stage`` / ``--resume-from`` / ``--force-resume`` /
        ``--input-model``) must be rejected with EXIT_CONFIG_ERROR
        when the config has no ``pipeline:`` block.  Pre-fix they were
        silently ignored, surprising operators who expected the flag
        to be load-bearing."""
        import sys

        import yaml

        from forgelm.cli import _dispatch

        # Single-stage config — no pipeline: block.
        yaml_path = tmp_path / "single.yaml"
        yaml_path.write_text(
            yaml.safe_dump(
                {
                    "model": {"name_or_path": "org/base"},
                    "lora": {"r": 8},
                    "training": {"trainer_type": "sft", "output_dir": str(tmp_path / "out")},
                    "data": {"dataset_name_or_path": "org/data"},
                }
            )
        )

        for flag in ("--stage", "--resume-from", "--input-model"):
            monkeypatch.setattr(
                sys,
                "argv",
                ["forgelm", "--config", str(yaml_path), flag, "dpo_stage"],
            )
            with pytest.raises(SystemExit) as exc_info:
                _dispatch.main()
            assert exc_info.value.code == EXIT_CONFIG_ERROR, (
                f"flag {flag!r} should exit EXIT_CONFIG_ERROR on a non-pipeline config; got exit={exc_info.value.code}"
            )

    def test_pipeline_only_flags_rejected_emits_json_envelope(self, tmp_path, monkeypatch, capsys):
        """HIGH finding 1 (cli-pipeline wave 2): with ``--output-format
        json``, the pipeline-only-flag rejection must still emit the
        2-key ``{"success": false, "error": ...}`` stdout envelope —
        matching every other error path in ``_main_inner`` (see
        ``_load_config_or_exit`` / the ``--config is required`` branch).
        Pre-fix this branch only called ``logger.error`` + ``sys.exit``,
        leaving stdout empty on a non-zero exit and breaking any
        consumer that unconditionally parses stdout as JSON."""
        import sys

        import yaml

        from forgelm.cli import _dispatch

        # Single-stage config — no pipeline: block.
        yaml_path = tmp_path / "single.yaml"
        yaml_path.write_text(
            yaml.safe_dump(
                {
                    "model": {"name_or_path": "org/base"},
                    "lora": {"r": 8},
                    "training": {"trainer_type": "sft", "output_dir": str(tmp_path / "out")},
                    "data": {"dataset_name_or_path": "org/data"},
                }
            )
        )

        monkeypatch.setattr(
            sys,
            "argv",
            ["forgelm", "--config", str(yaml_path), "--stage", "dpo_stage", "--output-format", "json"],
        )
        with pytest.raises(SystemExit) as exc_info:
            _dispatch.main()
        assert exc_info.value.code == EXIT_CONFIG_ERROR

        captured = capsys.readouterr()
        envelope = json.loads(captured.out)
        assert envelope["success"] is False
        assert "--stage" in envelope["error"]
        assert "pipeline:" in envelope["error"]

    def test_dispatch_pipeline_mode_yaml_reread_oserror_emits_json_envelope(self, tmp_path, capsys):
        """LOW finding 4 (cli-pipeline wave 2): ``_dispatch_pipeline_mode``
        re-reads ``args.config`` in binary mode (separately from the
        earlier ``load_config()`` parse) purely to hash the raw bytes for
        audit purposes. If that re-read fails with ``OSError``, JSON mode
        must still emit the ``{"success": false, "error": ...}`` envelope
        instead of leaving stdout empty on a non-zero exit — the same
        contract violation as finding 1, via a narrower (TOCTOU-style)
        trigger window. Opening a directory in ``"rb"`` mode raises
        ``IsADirectoryError`` (an ``OSError`` subclass) without needing to
        race a real file deletion."""
        from forgelm.cli._dispatch import _dispatch_pipeline_mode

        cfg = _three_stage_cfg(tmp_path)
        bad_config_path = tmp_path / "config_is_actually_a_directory"
        bad_config_path.mkdir()
        args = _ns(config=str(bad_config_path))

        with pytest.raises(SystemExit) as exc_info:
            _dispatch_pipeline_mode(cfg, args, True)
        assert exc_info.value.code == EXIT_CONFIG_ERROR

        captured = capsys.readouterr()
        envelope = json.loads(captured.out)
        assert envelope["success"] is False
        assert "error" in envelope

    def test_dispatch_pipeline_mode_yaml_reread_oserror_text_mode_no_stdout(self, tmp_path, capsys):
        """Companion to the JSON-mode test above: in text mode (the
        default), the same failure must NOT print anything to stdout —
        the envelope is JSON-mode-only, matching ``_load_config_or_exit``
        and the ``--config is required`` branch a few lines above this
        one in ``_main_inner``."""
        from forgelm.cli._dispatch import _dispatch_pipeline_mode

        cfg = _three_stage_cfg(tmp_path)
        bad_config_path = tmp_path / "config_is_actually_a_directory"
        bad_config_path.mkdir()
        args = _ns(config=str(bad_config_path))

        with pytest.raises(SystemExit) as exc_info:
            _dispatch_pipeline_mode(cfg, args, False)
        assert exc_info.value.code == EXIT_CONFIG_ERROR

        captured = capsys.readouterr()
        assert captured.out == ""
