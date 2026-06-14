"""Phase 35 — `forgelm cache-models` + `forgelm cache-tasks`.

Tests use mocked `huggingface_hub.snapshot_download` and
`lm_eval.tasks.get_task_dict` so the suite stays network-free + extra-
free; the CI matrix can run them without ever touching the Hub.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, NonCallableMagicMock, patch

import pytest


def _build_args(
    *,
    model: list[str] | None = None,
    safety: str | None = None,
    output: str | None = None,
    audit_dir: str | None = None,
    tasks: str | None = None,
) -> SimpleNamespace:
    """Strict argparse-shaped namespace; misspelled attrs raise."""
    return SimpleNamespace(
        model=model,
        safety=safety,
        output=output,
        audit_dir=audit_dir,
        tasks=tasks,
    )


@pytest.fixture(autouse=True)
def _set_operator_env(monkeypatch):
    monkeypatch.setenv("FORGELM_OPERATOR", "test-operator@cache-test")
    # Wave 2b final-review F-35-T-03: ``_run_cache_tasks_cmd`` deliberately
    # mutates the process-global ``HF_DATASETS_CACHE`` so the underlying
    # ``datasets`` library lands parquet shards in the operator's
    # resolved cache_dir.  Without this delenv the env stamp would leak
    # into other tests run in the same pytest invocation (test-order
    # dependent flakes when pytest-randomly is enabled).
    monkeypatch.delenv("HF_DATASETS_CACHE", raising=False)


# ---------------------------------------------------------------------------
# cache-models
# ---------------------------------------------------------------------------


class TestCacheModels:
    def test_cache_models_downloads_each_named_model(self, tmp_path: Path, capsys) -> None:
        from forgelm.cli.subcommands import _cache

        # Mock snapshot_download → produce a fake cached path with a
        # tiny file so _walk_directory_size has something to report.
        def _fake_snapshot_download(repo_id: str, cache_dir: str) -> str:
            cached = Path(cache_dir) / repo_id.replace("/", "--")
            cached.mkdir(parents=True, exist_ok=True)
            (cached / "config.json").write_text('{"r": 8}')
            (cached / "model.safetensors").write_bytes(b"x" * 4096)
            return str(cached)

        with patch.dict(
            "sys.modules",
            {"huggingface_hub": MagicMock(snapshot_download=_fake_snapshot_download)},
        ):
            args = _build_args(
                model=["meta-llama/Llama-3.2-3B"],
                output=str(tmp_path / "hf_cache"),
                audit_dir=str(tmp_path / "audit"),
            )
            with pytest.raises(SystemExit) as ei:
                _cache._run_cache_models_cmd(args, output_format="json")
            assert ei.value.code == 0

        payload = json.loads(capsys.readouterr().out)
        assert payload["success"] is True
        assert len(payload["models"]) == 1
        assert payload["models"][0]["name"] == "meta-llama/Llama-3.2-3B"
        # File is 4 KiB → ~0.004 MiB → rounds to 0.0 in size_mb display.
        # Assert the underlying byte count instead.
        assert payload["models"][0]["size_bytes"] >= 4096

    def test_cache_models_with_safety_appends_classifier(self, tmp_path: Path, capsys) -> None:
        from forgelm.cli.subcommands import _cache

        def _fake_snapshot_download(repo_id: str, cache_dir: str) -> str:
            cached = Path(cache_dir) / repo_id.replace("/", "--")
            cached.mkdir(parents=True, exist_ok=True)
            (cached / "weights.bin").write_bytes(b"x" * 1024)
            return str(cached)

        with patch.dict(
            "sys.modules",
            {"huggingface_hub": MagicMock(snapshot_download=_fake_snapshot_download)},
        ):
            args = _build_args(
                model=["base/model"],
                safety="meta-llama/Llama-Guard-3-8B",
                output=str(tmp_path / "hf_cache"),
                audit_dir=str(tmp_path / "audit"),
            )
            with pytest.raises(SystemExit) as ei:
                _cache._run_cache_models_cmd(args, output_format="json")
            assert ei.value.code == 0
        payload = json.loads(capsys.readouterr().out)
        names = {m["name"] for m in payload["models"]}
        assert names == {"base/model", "meta-llama/Llama-Guard-3-8B"}

    def test_cache_models_no_args_exits_config_error(self, tmp_path: Path, capsys) -> None:
        from forgelm.cli.subcommands._cache import _run_cache_models_cmd

        args = _build_args(output=str(tmp_path / "hf_cache"))
        with pytest.raises(SystemExit) as ei:
            _run_cache_models_cmd(args, output_format="json")
        assert ei.value.code == 1

    def test_cache_models_empty_model_name_rejected(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._cache import _run_cache_models_cmd

        args = _build_args(model=["   "], output=str(tmp_path / "hf_cache"))
        with pytest.raises(SystemExit) as ei:
            _run_cache_models_cmd(args, output_format="json")
        assert ei.value.code == 1

    def test_cache_models_emits_audit_chain(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands import _cache

        def _fake_snapshot_download(repo_id: str, cache_dir: str) -> str:
            cached = Path(cache_dir) / repo_id.replace("/", "--")
            cached.mkdir(parents=True, exist_ok=True)
            (cached / "f").write_bytes(b"x")
            return str(cached)

        audit_dir = tmp_path / "audit"
        audit_dir.mkdir()
        with patch.dict(
            "sys.modules",
            {"huggingface_hub": MagicMock(snapshot_download=_fake_snapshot_download)},
        ):
            args = _build_args(
                model=["base/model"],
                output=str(tmp_path / "hf_cache"),
                audit_dir=str(audit_dir),
            )
            with pytest.raises(SystemExit) as ei:
                _cache._run_cache_models_cmd(args, output_format="json")
            # Anchor the audit-chain assertions below: the chain is only
            # interesting when the run actually succeeded.  Without this
            # the test would silently pass on a code-1 / code-2 run that
            # also happened to write the request event.
            assert ei.value.code == 0

        log = audit_dir / "audit_log.jsonl"
        events = []
        with open(log, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        names = [e["event"] for e in events]
        assert "cache.populate_models_requested" in names
        assert "cache.populate_models_completed" in names
        assert names.index("cache.populate_models_requested") < names.index("cache.populate_models_completed")

    def test_cache_models_hub_failure_emits_failed_event(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands import _cache

        def _failing_snapshot_download(repo_id: str, cache_dir: str) -> str:
            raise ConnectionError("HF Hub unreachable")

        audit_dir = tmp_path / "audit"
        audit_dir.mkdir()
        with patch.dict(
            "sys.modules",
            {"huggingface_hub": MagicMock(snapshot_download=_failing_snapshot_download)},
        ):
            args = _build_args(
                model=["base/model"],
                output=str(tmp_path / "hf_cache"),
                audit_dir=str(audit_dir),
            )
            with pytest.raises(SystemExit) as ei:
                _cache._run_cache_models_cmd(args, output_format="json")
            assert ei.value.code == 2  # runtime error class

        # Failed event recorded.
        log_text = (audit_dir / "audit_log.jsonl").read_text()
        assert "cache.populate_models_failed" in log_text


# ---------------------------------------------------------------------------
# cache-tasks
# ---------------------------------------------------------------------------


class TestCacheTasks:
    def test_cache_tasks_missing_extra_emits_install_hint(self, tmp_path: Path, capsys, monkeypatch) -> None:
        # Two-pronged isolation: pop any prior `lm_eval` entries from
        # sys.modules so the `import lm_eval` statement inside
        # `_run_cache_tasks_cmd` actually goes through the patched
        # __import__ (otherwise Python short-circuits via the cached
        # entry and our install-hint path never fires); AND patch
        # builtins.__import__ to refuse fresh lm_eval imports.  Both
        # are required because some other test in this run may have
        # already pulled lm_eval into sys.modules.
        import builtins
        import sys as _sys

        from forgelm.cli.subcommands._cache import _run_cache_tasks_cmd

        orig_import = builtins.__import__

        def _block_lm_eval(name, *args, **kwargs):
            if name == "lm_eval" or name.startswith("lm_eval."):
                raise ImportError("No module named 'lm_eval'")
            return orig_import(name, *args, **kwargs)

        # Wipe any preloaded lm_eval entries (only this test cares).
        # The list comprehension materialises the keys snapshot before
        # `monkeypatch.delitem` mutates `_sys.modules`, so we don't need
        # the redundant `list()` wrapper around `_sys.modules`.
        for cached in [k for k in _sys.modules if k == "lm_eval" or k.startswith("lm_eval.")]:
            monkeypatch.delitem(_sys.modules, cached, raising=False)

        with patch.object(builtins, "__import__", _block_lm_eval):
            args = _build_args(tasks="hellaswag", output=str(tmp_path / "cache"))
            with pytest.raises(SystemExit) as ei:
                _run_cache_tasks_cmd(args, output_format="json")
            assert ei.value.code == 1
        payload = json.loads(capsys.readouterr().out)
        assert "lm-eval" in payload["error"]
        assert "forgelm[eval]" in payload["error"]

    def test_cache_tasks_empty_tasks_rejected(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._cache import _run_cache_tasks_cmd

        args = _build_args(tasks="", output=str(tmp_path / "cache"))
        with pytest.raises(SystemExit) as ei:
            _run_cache_tasks_cmd(args, output_format="json")
        assert ei.value.code == 1

    def test_cache_tasks_with_mocked_lm_eval_succeeds(self, tmp_path: Path, capsys) -> None:
        from forgelm.cli.subcommands import _cache

        # Mock lm_eval surface.  Use NonCallableMagicMock for the dataset
        # — production code distinguishes "task.dataset is a method that
        # returns a dataset" (callable) from "task.dataset IS the dataset"
        # (not callable) and the test needs the latter shape.
        fake_dataset = NonCallableMagicMock()

        class _FakeTask:
            dataset = fake_dataset

        fake_lm_eval = MagicMock()
        fake_lm_eval_tasks = MagicMock()
        fake_lm_eval_tasks.get_task_dict = MagicMock(return_value={"hellaswag": _FakeTask()})

        with patch.dict(
            "sys.modules",
            {
                "lm_eval": fake_lm_eval,
                "lm_eval.tasks": fake_lm_eval_tasks,
            },
        ):
            args = _build_args(
                tasks="hellaswag",
                output=str(tmp_path / "cache"),
                audit_dir=str(tmp_path / "audit"),
            )
            with pytest.raises(SystemExit) as ei:
                _cache._run_cache_tasks_cmd(args, output_format="json")
            assert ei.value.code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["success"] is True
        names = {t["name"] for t in payload["tasks"]}
        assert names == {"hellaswag"}
        # And download_and_prepare was actually called.
        fake_dataset.download_and_prepare.assert_called_once()

    def test_cache_tasks_unknown_task_name_exits_config_error(self, tmp_path: Path, capsys) -> None:
        from forgelm.cli.subcommands import _cache

        fake_lm_eval = MagicMock()
        fake_lm_eval_tasks = MagicMock()
        fake_lm_eval_tasks.get_task_dict = MagicMock(side_effect=KeyError("bogus_task"))

        with patch.dict(
            "sys.modules",
            {
                "lm_eval": fake_lm_eval,
                "lm_eval.tasks": fake_lm_eval_tasks,
            },
        ):
            args = _build_args(tasks="bogus_task", output=str(tmp_path / "cache"))
            with pytest.raises(SystemExit) as ei:
                _cache._run_cache_tasks_cmd(args, output_format="json")
            assert ei.value.code == 1
        payload = json.loads(capsys.readouterr().out)
        assert "bogus_task" in payload["error"]

    def test_cache_tasks_output_help_matches_datasets_resolver(self) -> None:
        # F-P7-OPUS-10: cache-tasks resolves + writes via the Datasets
        # cache chain (HF_DATASETS_CACHE), not the Hub chain.  The --output
        # help text must advertise the Datasets chain so an air-gap
        # operator sets the right env var.
        import subprocess
        import sys as _sys

        proc = subprocess.run(
            [_sys.executable, "-m", "forgelm.cli", "cache-tasks", "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        help_text = proc.stdout + proc.stderr
        assert "HF_DATASETS_CACHE" in help_text
        assert "datasets" in help_text
        assert "HF_HUB_CACHE" not in help_text


# ---------------------------------------------------------------------------
# Cache-dir resolution helper
# ---------------------------------------------------------------------------


class TestCacheDirResolution:
    def test_explicit_output_wins(self, monkeypatch, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._cache import _resolve_cache_dir

        monkeypatch.setenv("HF_HUB_CACHE", "/should/not/win")
        target = str(tmp_path / "explicit")
        assert _resolve_cache_dir(target) == os.path.abspath(target)

    def test_hf_hub_cache_env_wins_over_hf_home(self, monkeypatch, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._cache import _resolve_cache_dir

        hub_cache = str(tmp_path / "hub_cache")
        hf_home = str(tmp_path / "hf_home")
        monkeypatch.setenv("HF_HUB_CACHE", hub_cache)
        monkeypatch.setenv("HF_HOME", hf_home)
        assert _resolve_cache_dir(None) == hub_cache

    def test_hf_home_appends_hub_subdir(self, monkeypatch, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._cache import _resolve_cache_dir

        hf_home = str(tmp_path / "hf_home")
        monkeypatch.delenv("HF_HUB_CACHE", raising=False)
        monkeypatch.setenv("HF_HOME", hf_home)
        assert _resolve_cache_dir(None) == os.path.join(hf_home, "hub")


# ---------------------------------------------------------------------------
# Wave 2b final-review absorption — F-35-T-01 / F-35-T-02 / F-35-T-03
# regressions for absorption-tightened contracts that landed without
# dedicated test coverage.
# ---------------------------------------------------------------------------


class TestDatasetsCacheDirResolution:
    """F-35-T-01: Round-5-followup split the Hub cache resolver
    (``_resolve_env_cache_dir``) from the Datasets cache resolver
    (``_resolve_env_datasets_cache_dir``) because HF treats hub
    snapshots and dataset Arrow shards as separate caches.  Without
    these tests, a future "DRY" PR that re-conflated the two would
    silently put parquet shards under ``hub/`` instead of ``datasets/``,
    defeating the air-gap workflow."""

    def test_explicit_output_wins_for_datasets_resolver(self, monkeypatch, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._cache import _resolve_datasets_cache_dir

        monkeypatch.setenv("HF_DATASETS_CACHE", "/should/not/win")
        target = str(tmp_path / "explicit")
        assert _resolve_datasets_cache_dir(target) == os.path.abspath(target)

    def test_hf_datasets_cache_env_wins_over_hf_home(self, monkeypatch, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._cache import _resolve_datasets_cache_dir

        ds_cache = str(tmp_path / "ds_cache")
        hf_home = str(tmp_path / "hf_home")
        monkeypatch.setenv("HF_DATASETS_CACHE", ds_cache)
        monkeypatch.setenv("HF_HOME", hf_home)
        assert _resolve_datasets_cache_dir(None) == ds_cache

    def test_hf_home_appends_datasets_subdir(self, monkeypatch, tmp_path: Path) -> None:
        from forgelm.cli.subcommands._cache import _resolve_datasets_cache_dir

        hf_home = str(tmp_path / "hf_home")
        monkeypatch.delenv("HF_DATASETS_CACHE", raising=False)
        monkeypatch.setenv("HF_HOME", hf_home)
        assert _resolve_datasets_cache_dir(None) == os.path.join(hf_home, "datasets")

    def test_hf_datasets_cache_env_does_not_affect_hub_resolver(self, monkeypatch) -> None:
        """Cross-bleed defence: setting only ``HF_DATASETS_CACHE`` must
        leave the Hub resolver untouched (it falls back to its own default)."""
        from forgelm.cli.subcommands._cache import _resolve_cache_dir

        monkeypatch.setenv("HF_DATASETS_CACHE", "/datasets/only")
        monkeypatch.delenv("HF_HUB_CACHE", raising=False)
        monkeypatch.delenv("HF_HOME", raising=False)
        assert _resolve_cache_dir(None).endswith("huggingface/hub"), "HF_DATASETS_CACHE leaked into the Hub resolver"

    def test_hf_hub_cache_env_does_not_affect_datasets_resolver(self, monkeypatch) -> None:
        """Inverse cross-bleed defence: setting only ``HF_HUB_CACHE`` must
        leave the Datasets resolver untouched."""
        from forgelm.cli.subcommands._cache import _resolve_datasets_cache_dir

        monkeypatch.setenv("HF_HUB_CACHE", "/hub/only")
        monkeypatch.delenv("HF_DATASETS_CACHE", raising=False)
        monkeypatch.delenv("HF_HOME", raising=False)
        assert _resolve_datasets_cache_dir(None).endswith("huggingface/datasets"), (
            "HF_HUB_CACHE leaked into the Datasets resolver"
        )


class TestCacheTasksEnvStamp:
    """F-35-T-03: Round-5 added ``os.environ["HF_DATASETS_CACHE"] = cache_dir``
    inside ``_run_cache_tasks_cmd`` precisely so the JSON envelope's
    ``cache_dir`` claim and the on-disk parquet location stay in sync.
    Without these tests, a regression that removed the stamp (or
    pointed it at the wrong path) would defeat the air-gap workflow's
    primary value."""

    def test_cache_tasks_stamps_hf_datasets_cache_env(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands import _cache

        captured_env: list[str | None] = []

        class _StampSpyTask:
            @property
            def dataset(self):
                # Capture the env at the moment the task's dataset is
                # being prepared — that's when the stamp must be in place.
                captured_env.append(os.environ.get("HF_DATASETS_CACHE"))
                return NonCallableMagicMock()

        fake_lm_eval = MagicMock()
        fake_lm_eval_tasks = MagicMock()
        fake_lm_eval_tasks.get_task_dict = MagicMock(return_value={"hellaswag": _StampSpyTask()})

        cache_dir = str(tmp_path / "cache")
        with patch.dict(
            "sys.modules",
            {"lm_eval": fake_lm_eval, "lm_eval.tasks": fake_lm_eval_tasks},
        ):
            args = _build_args(tasks="hellaswag", output=cache_dir, audit_dir=str(tmp_path / "audit"))
            with pytest.raises(SystemExit) as ei:
                _cache._run_cache_tasks_cmd(args, output_format="json")
            assert ei.value.code == 0

        assert captured_env, "_StampSpyTask.dataset was never accessed; spy missed the stamp"
        # The env var was set to the resolved cache_dir at the moment
        # download_and_prepare ran.  os.path.abspath collapses any
        # relative-path normalisation difference.
        assert captured_env[0] == os.path.abspath(cache_dir), (
            f"HF_DATASETS_CACHE was {captured_env[0]!r} at task-prepare time; expected {os.path.abspath(cache_dir)!r}"
        )

    def test_cache_tasks_does_not_leak_hf_datasets_cache_env(self, tmp_path: Path) -> None:
        """F-35-01 / F-35-T-03 leak-defence: the try/finally restore in
        ``_run_cache_tasks_cmd`` must leave ``HF_DATASETS_CACHE`` in the
        same state it found.  Without the restore, a long-lived process
        (Jupyter session, library caller, pytest run) would see the
        stamped value persist after the call returned."""
        from forgelm.cli.subcommands import _cache

        # Sanity: the autouse fixture has already del'd the env var.
        assert "HF_DATASETS_CACHE" not in os.environ

        fake_lm_eval = MagicMock()
        fake_lm_eval_tasks = MagicMock()
        fake_lm_eval_tasks.get_task_dict = MagicMock(
            return_value={"hellaswag": type("T", (), {"dataset": NonCallableMagicMock()})()}
        )
        with patch.dict("sys.modules", {"lm_eval": fake_lm_eval, "lm_eval.tasks": fake_lm_eval_tasks}):
            args = _build_args(tasks="hellaswag", output=str(tmp_path / "cache"), audit_dir=str(tmp_path / "a"))
            with pytest.raises(SystemExit) as ei:
                _cache._run_cache_tasks_cmd(args, output_format="json")
            # Anchor on the happy path: the leak-defence claim is only
            # meaningful if the dispatcher actually completed normally.
            # Without this assertion a code-1 / code-2 early-exit (which
            # never reaches the env-stamp restore) would silently pass.
            assert ei.value.code == 0

        # Post-call: env must be back to "not set" (the prior state).
        assert "HF_DATASETS_CACHE" not in os.environ, (
            "HF_DATASETS_CACHE leaked into the parent environment after _run_cache_tasks_cmd"
        )

    def test_cache_tasks_restores_prior_hf_datasets_cache_env(self, tmp_path: Path, monkeypatch) -> None:
        """If ``HF_DATASETS_CACHE`` was already set when the command
        ran, the restore must put the original value back, not delete
        it."""
        from forgelm.cli.subcommands import _cache

        original = "/operator/preset/cache"
        monkeypatch.setenv("HF_DATASETS_CACHE", original)

        fake_lm_eval = MagicMock()
        fake_lm_eval_tasks = MagicMock()
        fake_lm_eval_tasks.get_task_dict = MagicMock(
            return_value={"hellaswag": type("T", (), {"dataset": NonCallableMagicMock()})()}
        )
        with patch.dict("sys.modules", {"lm_eval": fake_lm_eval, "lm_eval.tasks": fake_lm_eval_tasks}):
            args = _build_args(tasks="hellaswag", output=str(tmp_path / "cache"), audit_dir=str(tmp_path / "a"))
            with pytest.raises(SystemExit) as ei:
                _cache._run_cache_tasks_cmd(args, output_format="json")
            assert ei.value.code == 0  # only meaningful if the run actually succeeded

        assert os.environ.get("HF_DATASETS_CACHE") == original, (
            f"HF_DATASETS_CACHE was not restored to its prior value; got {os.environ.get('HF_DATASETS_CACHE')!r}"
        )


class TestCacheModelsMidBatchFailure:
    """F-35-T-02: when one model in a multi-model batch fails, the
    surviving audit chain must record ``models_completed`` correctly so
    an operator can scan the log and see what made it.  A future
    "swallow per-model errors and continue" refactor would silently
    skip the failed model and exit 0; this test pins the fail-fast
    contract."""

    def test_cache_models_mid_batch_failure_reports_completed_subset(self, tmp_path: Path) -> None:
        from forgelm.cli.subcommands import _cache

        attempted: list[str] = []

        def _flaky_snapshot_download(repo_id: str, cache_dir: str) -> str:
            attempted.append(repo_id)
            if repo_id == "B":
                raise ConnectionError("HF flake on B")
            cached = Path(cache_dir) / repo_id
            cached.mkdir(parents=True, exist_ok=True)
            (cached / "f").write_bytes(b"x" * 16)
            return str(cached)

        audit_dir = tmp_path / "audit"
        audit_dir.mkdir()
        with patch.dict(
            "sys.modules",
            {"huggingface_hub": MagicMock(snapshot_download=_flaky_snapshot_download)},
        ):
            args = _build_args(
                model=["A", "B", "C"],
                output=str(tmp_path / "hub"),
                audit_dir=str(audit_dir),
            )
            with pytest.raises(SystemExit) as ei:
                _cache._run_cache_models_cmd(args, output_format="json")
            assert ei.value.code == 2

        # Fail-fast: third model must NOT have been attempted after B failed.
        assert attempted == ["A", "B"], f"third model must NOT have been attempted after B failed; got {attempted!r}"
        # Audit chain records the completed subset (only "A").
        log = audit_dir / "audit_log.jsonl"
        events: list[dict] = []
        with open(log, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        failed = next(e for e in events if e["event"] == "cache.populate_models_failed")
        assert failed["models_completed"] == ["A"]


# ---------------------------------------------------------------------------
# Facade re-exports
# ---------------------------------------------------------------------------


class TestCacheFacadeReExports:
    def test_cache_helpers_reachable_via_cli_facade(self) -> None:
        from forgelm import cli as _cli_facade

        for name in (
            "_run_cache_models_cmd",
            "_run_cache_tasks_cmd",
            "_resolve_cache_dir",
            "_validate_model_name",
            "_walk_directory_size",
        ):
            assert hasattr(_cli_facade, name), f"forgelm.cli must re-export {name!r}"
