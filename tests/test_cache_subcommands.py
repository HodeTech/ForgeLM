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

    def test_cache_models_json_preserves_unicode_path(self, capsys) -> None:
        """F-P7-OPUS-42: the success JSON now passes ``ensure_ascii=False``
        (matching the doctor renderer), so a Unicode ``cache_dir`` renders as
        the literal path rather than ``\\uXXXX`` escapes."""
        from forgelm.cli.subcommands._cache import _emit_cache_success

        payload = {"cache_dir": "/work/önbellek", "models": [], "total_size_mb": 0}
        _emit_cache_success("json", payload, kind="models")

        out = capsys.readouterr().out
        assert "/work/önbellek" in out  # literal, un-escaped
        assert "\\u" not in out
        assert json.loads(out)["cache_dir"] == "/work/önbellek"

    def test_cache_models_follows_symlink_farm_to_blob_store(self, tmp_path: Path, capsys) -> None:
        """F1 (HIGH) regression: ``snapshot_download`` returns the
        ``snapshots/<commit>/`` directory whose entries are symlinks into a
        sibling ``blobs/`` store holding the real content-addressed bytes (the
        default POSIX HF cache layout). ``_walk_directory_size`` must follow
        those symlinks to the real blob; the previous ``skip symlinks`` walk
        reported ~0 for every downloaded model."""
        from forgelm.cli.subcommands import _cache

        blob_size = 1024 * 1024  # 1 MiB, unmistakably non-zero

        def _fake_snapshot_download(repo_id: str, cache_dir: str) -> str:
            repo_root = Path(cache_dir) / ("models--" + repo_id.replace("/", "--"))
            blobs = repo_root / "blobs"
            snapshot = repo_root / "snapshots" / "deadbeefdeadbeef"
            blobs.mkdir(parents=True, exist_ok=True)
            snapshot.mkdir(parents=True, exist_ok=True)
            blob_file = blobs / "sha256-abc123"
            blob_file.write_bytes(b"x" * blob_size)
            # snapshot entry is a symlink into the blob store (POSIX HF layout).
            os.symlink(blob_file, snapshot / "model.safetensors")
            # snapshot_download returns the snapshot dir, NOT the repo root.
            return str(snapshot)

        with patch.dict(
            "sys.modules",
            {"huggingface_hub": MagicMock(snapshot_download=_fake_snapshot_download)},
        ):
            args = _build_args(
                model=["org/model"],
                output=str(tmp_path / "hf_cache"),
                audit_dir=str(tmp_path / "audit"),
            )
            with pytest.raises(SystemExit) as ei:
                _cache._run_cache_models_cmd(args, output_format="json")
            assert ei.value.code == 0

        payload = json.loads(capsys.readouterr().out)
        assert payload["models"][0]["size_bytes"] == blob_size, (
            "symlink farm must be followed to the real blob bytes, not skipped (which reported 0)"
        )

    def test_walk_directory_size_follows_symlinks_and_dedups(self, tmp_path: Path) -> None:
        """Direct unit: two snapshot entries symlinked to the SAME blob are
        counted once (dedup by resolved path); a broken symlink is ignored."""
        from forgelm.cli.subcommands._cache import _walk_directory_size

        blobs = tmp_path / "blobs"
        snapshot = tmp_path / "snapshots" / "rev"
        blobs.mkdir(parents=True)
        snapshot.mkdir(parents=True)
        blob = blobs / "blob-1"
        blob.write_bytes(b"y" * 2048)
        # Two snapshot revisions both symlink the same blob → count once.
        os.symlink(blob, snapshot / "weights-a.safetensors")
        os.symlink(blob, snapshot / "weights-b.safetensors")
        # A broken symlink must not raise or contribute.
        os.symlink(blobs / "does-not-exist", snapshot / "dangling")

        assert _walk_directory_size(str(snapshot)) == 2048

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

    def test_cache_tasks_text_summary_no_bare_none_for_missing_dataset(self, capsys, tmp_path) -> None:
        """F8: a task with no downloadable dataset returns
        ``{'cached': False, 'error': None}``. The text renderer must not print
        the bare literal ``warn (None)`` (``dict.get(key, default)`` only
        substitutes when the key is *absent*, not when its value is None)."""
        from forgelm.cli.subcommands._cache import _emit_cache_success

        payload = {
            "tasks": [{"name": "weird_task", "cached": False, "error": None}],
            "cache_dir": str(tmp_path / "cache"),
        }
        _emit_cache_success("text", payload, kind="tasks")

        out = capsys.readouterr().out
        assert "warn (None)" not in out
        assert "no downloadable dataset attribute" in out

    def test_cache_tasks_text_summary_surfaces_real_error(self, capsys, tmp_path) -> None:
        """When a real per-task error IS recorded it must be shown verbatim,
        not replaced by the no-dataset fallback message."""
        from forgelm.cli.subcommands._cache import _emit_cache_success

        payload = {
            "tasks": [{"name": "boom_task", "cached": False, "error": "RuntimeError: decode failed"}],
            "cache_dir": str(tmp_path / "cache"),
        }
        _emit_cache_success("text", payload, kind="tasks")

        out = capsys.readouterr().out
        assert "RuntimeError: decode failed" in out

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
        assert proc.returncode == 0, proc.stderr
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


class TestCacheTasksVerdictMatrix:
    """`cache-tasks` must not report success for a cache it did not populate.

    ``_prepare_one_task`` converts every per-task exception into a
    ``cached=False`` row, which makes the ``except`` wrapping the loop
    structurally dead — the loop body cannot raise. So the aggregating caller
    emitted ``cache.populate_tasks_completed``, ``success: true`` and exit 0
    for a batch in which every download had failed.

    That matters because of what this command is *for*. The air-gap guide
    tells operators to gate on ``jq -e '.success'`` before transferring the
    bundle to a restricted host. A green gate over an empty dataset cache
    ships the archive anyway, and the failure resurfaces days later on the
    air-gapped machine as an inscrutable training error, with no network to
    diagnose it.

    Decision C-5: a partial batch is a hard failure, not a warning.
    """

    def _run(self, tmp_path, outcomes, output_format="json"):
        """Drive the real dispatcher with a task dict whose datasets behave per `outcomes`.

        `outcomes` maps task name -> None (downloads fine) or an Exception to
        raise from ``download_and_prepare``.
        """
        from forgelm.cli.subcommands import _cache

        task_dict = {}
        for name, outcome in outcomes.items():
            dataset = NonCallableMagicMock()
            if outcome is not None:
                dataset.download_and_prepare.side_effect = outcome
            task = NonCallableMagicMock()
            task.dataset = dataset
            task_dict[name] = task

        fake_tasks = MagicMock()
        fake_tasks.get_task_dict = MagicMock(return_value=task_dict)
        with patch.dict("sys.modules", {"lm_eval": MagicMock(), "lm_eval.tasks": fake_tasks}):
            args = _build_args(
                tasks=",".join(outcomes),
                output=str(tmp_path / "cache"),
                audit_dir=str(tmp_path / "audit"),
            )
            with pytest.raises(SystemExit) as ei:
                _cache._run_cache_tasks_cmd(args, output_format=output_format)
            return ei.value.code

    @staticmethod
    def _events(tmp_path):
        log = tmp_path / "audit" / "audit_log.jsonl"
        if not log.exists():
            return []
        return [json.loads(line)["event"] for line in log.read_text().splitlines() if line.strip()]

    def test_zero_of_n_exits_two(self, tmp_path, capsys):
        code = self._run(tmp_path, {"a": OSError("network unreachable"), "b": OSError("network unreachable")})
        assert code == 2, "a batch that staged nothing cannot exit 0"
        payload = json.loads(capsys.readouterr().out)
        assert payload["success"] is False

    def test_zero_of_n_logs_failed_not_completed(self, tmp_path, capsys):
        self._run(tmp_path, {"a": OSError("boom"), "b": OSError("boom")})
        capsys.readouterr()
        events = self._events(tmp_path)
        assert "cache.populate_tasks_failed" in events
        assert "cache.populate_tasks_completed" not in events, (
            "recording a failed download as `completed` in an append-only log is an Art. 12 evidence defect"
        )

    def test_nothing_staged_logs_the_documented_payload_keys(self, tmp_path, capsys):
        """The catalog documents two shapes for ``_failed``; each must be the one actually emitted.

        ``check_audit_event_catalog.py`` compares event *names* only, so a payload that
        drifted from its documented keys was invisible to it — and the log is append-only.
        """
        self._run(tmp_path, {"a": OSError("boom"), "b": OSError("boom")})
        capsys.readouterr()
        log = tmp_path / "audit" / "audit_log.jsonl"
        failed = [
            e
            for e in (json.loads(line) for line in log.read_text().splitlines() if line.strip())
            if e["event"] == "cache.populate_tasks_failed"
        ]
        assert len(failed) == 1
        assert {"tasks", "cache_dir", "tasks_cached", "tasks_failed", "tasks_unavailable"} <= set(failed[0])
        assert failed[0]["tasks_cached"] == [] and sorted(failed[0]["tasks_failed"]) == ["a", "b"]

    def test_an_unknown_task_name_logs_the_documented_payload_keys(self, tmp_path, capsys):
        from forgelm.cli.subcommands import _cache

        fake_tasks = MagicMock()
        fake_tasks.get_task_dict = MagicMock(side_effect=KeyError("nope"))
        with patch.dict("sys.modules", {"lm_eval": MagicMock(), "lm_eval.tasks": fake_tasks}):
            args = _build_args(tasks="nope", output=str(tmp_path / "cache"), audit_dir=str(tmp_path / "audit"))
            with pytest.raises(SystemExit):
                _cache._run_cache_tasks_cmd(args, output_format="json")
        capsys.readouterr()
        log = tmp_path / "audit" / "audit_log.jsonl"
        failed = [
            e
            for e in (json.loads(line) for line in log.read_text().splitlines() if line.strip())
            if e["event"] == "cache.populate_tasks_failed"
        ]
        assert {"tasks_completed", "error_class", "error_message"} <= set(failed[0])

    def test_partial_batch_exits_two(self, tmp_path, capsys):
        code = self._run(tmp_path, {"ok": None, "bad": OSError("403 from the hub")})
        assert code == 2
        payload = json.loads(capsys.readouterr().out)
        assert payload["success"] is False

    def test_partial_batch_logs_its_own_event(self, tmp_path, capsys):
        """`_partial` is distinct from `_failed` on purpose.

        "Nothing landed" and "some landed, the bundle on disk is incomplete"
        are different situations for whoever reads the log afterwards — the
        second produces a transferable-looking archive that is missing data.
        """
        self._run(tmp_path, {"ok": None, "bad": OSError("403")})
        capsys.readouterr()
        events = self._events(tmp_path)
        assert "cache.populate_tasks_partial" in events
        assert "cache.populate_tasks_completed" not in events

    def test_the_failure_envelope_still_names_the_tasks(self, tmp_path, capsys):
        """A JSON consumer must learn *which* tasks are missing, not just that some are."""
        self._run(tmp_path, {"ok": None, "bad": OSError("403 from the hub")})
        payload = json.loads(capsys.readouterr().out)
        by_name = {t["name"]: t for t in payload["tasks"]}
        assert by_name["ok"]["cached"] is True
        assert by_name["bad"]["cached"] is False
        assert "403 from the hub" in by_name["bad"]["error"]
        assert "403 from the hub" in payload["error"]

    def test_full_success_is_unchanged(self, tmp_path, capsys):
        """The negative control — a healthy batch must still exit 0."""
        code = self._run(tmp_path, {"a": None, "b": None})
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["success"] is True
        assert all(t["cached"] for t in payload["tasks"])
        assert "cache.populate_tasks_completed" in self._events(tmp_path)

    def test_text_mode_prints_the_same_per_task_detail(self, tmp_path, capsys):
        """Text-mode operators must not get strictly less than `--json` does."""
        code = self._run(tmp_path, {"ok": None, "bad": OSError("403 from the hub")}, output_format="text")
        assert code == 2
        combined = capsys.readouterr()
        out = combined.out + combined.err
        assert "ok: ok" in out
        assert "bad: FAILED" in out

    def test_json_failure_emits_exactly_one_document(self, tmp_path, capsys):
        """The air-gap guide pipes stdout into `jq`; two documents break it."""
        self._run(tmp_path, {"ok": None, "bad": OSError("403")})
        out = capsys.readouterr().out
        json.loads(out)  # raises if a second document was appended

    def test_a_task_exposing_no_dataset_is_not_a_silent_pass(self, tmp_path, capsys):
        """`cached=False, error=None` still means the bundle is incomplete.

        Nothing raised — lm-eval simply exposed no downloadable dataset for
        the task — but for the one purpose this command serves, an archive
        that must be complete before it travels, an absent dataset is missing
        data just the same. It is named separately in the message so the
        operator can tell lm-eval surface drift from a network failure.
        """
        from forgelm.cli.subcommands import _cache

        task = NonCallableMagicMock()
        task.dataset = None
        fake_tasks = MagicMock()
        fake_tasks.get_task_dict = MagicMock(return_value={"weird": task})
        with patch.dict("sys.modules", {"lm_eval": MagicMock(), "lm_eval.tasks": fake_tasks}):
            args = _build_args(tasks="weird", output=str(tmp_path / "cache"), audit_dir=str(tmp_path / "audit"))
            with pytest.raises(SystemExit) as ei:
                _cache._run_cache_tasks_cmd(args, output_format="json")
        assert ei.value.code == 2
        payload = json.loads(capsys.readouterr().out)
        assert payload["success"] is False
        assert "no downloadable dataset exposed by lm-eval" in payload["error"]
        assert "weird" in payload["error"]


class TestCacheTasksEmptyEnumeration:
    """A task dict that enumerates to nothing must fail, not report success.

    ``TestCacheTasksVerdictMatrix`` closes the fold *after* the loop. This is the
    layer before it: ``{}`` and ``{group: {}}`` produce no leaf tasks, the loop
    never runs, ``uncached`` is empty, and the command emitted
    ``cache.populate_tasks_completed`` / ``success: true`` / exit 0 for a cache it
    never touched. lm-eval has reshaped ``get_task_dict`` across releases, so an
    empty result is the drift this command must not read as a pass.
    """

    def _run(self, tmp_path, task_dict, tasks="anything"):
        from forgelm.cli.subcommands import _cache

        fake_tasks = MagicMock()
        fake_tasks.get_task_dict = MagicMock(return_value=task_dict)
        with patch.dict("sys.modules", {"lm_eval": MagicMock(), "lm_eval.tasks": fake_tasks}):
            args = _build_args(tasks=tasks, output=str(tmp_path / "cache"), audit_dir=str(tmp_path / "audit"))
            with pytest.raises(SystemExit) as ei:
                _cache._run_cache_tasks_cmd(args, output_format="json")
        return ei.value.code

    @staticmethod
    def _events(tmp_path):
        log = tmp_path / "audit" / "audit_log.jsonl"
        return [json.loads(line) for line in log.read_text().splitlines() if line.strip()] if log.exists() else []

    @pytest.mark.parametrize(
        "task_dict", [{}, {"a_group": {}}, {"outer": {"inner": {}}}], ids=["empty", "empty-group", "nested"]
    )
    def test_nothing_enumerated_exits_two(self, tmp_path, capsys, task_dict):
        assert self._run(tmp_path, task_dict) == 2
        payload = json.loads(capsys.readouterr().out)
        assert payload["success"] is False
        assert "no runnable task" in payload["error"]

    def test_nothing_enumerated_logs_failed_with_the_documented_payload(self, tmp_path, capsys):
        self._run(tmp_path, {})
        capsys.readouterr()
        events = self._events(tmp_path)
        assert "cache.populate_tasks_completed" not in [e["event"] for e in events]
        failed = [e for e in events if e["event"] == "cache.populate_tasks_failed"]
        assert len(failed) == 1
        # The audit catalog documents these keys for this event; a consumer parsing the log relies on them.
        assert {"tasks", "cache_dir", "tasks_completed", "error_class", "error_message"} <= set(failed[0])
        assert failed[0]["tasks_completed"] == []
        assert failed[0]["error_class"] == "NoTasksResolved"

    def test_an_enumeration_that_raises_is_an_envelope_not_a_traceback(self, tmp_path, capsys, monkeypatch):
        """``_leaf_tasks`` ran outside any ``try``: a walk failure was a raw traceback and exit 1.

        Exit 1 is the *config* code, so a lm-eval surface change would have read to a
        pipeline as an operator typo, with no JSON envelope and no audit event.
        """
        from forgelm.cli.subcommands import _cache

        def boom(_task_dict):
            raise AttributeError("'NoneType' object has no attribute 'items'")

        monkeypatch.setattr(_cache, "_leaf_tasks", boom)
        assert self._run(tmp_path, {"x": object()}) == 2
        payload = json.loads(capsys.readouterr().out)
        assert payload["success"] is False
        assert "has no attribute 'items'" in payload["error"]
        failed = [e for e in self._events(tmp_path) if e["event"] == "cache.populate_tasks_failed"]
        assert failed and failed[0]["error_class"] == "AttributeError"

    def test_a_non_empty_enumeration_is_unaffected(self, tmp_path, capsys):
        """The negative control: the new check must not reject a healthy single task."""
        task = NonCallableMagicMock()
        task.dataset = NonCallableMagicMock()
        assert self._run(tmp_path, {"ok": task}, tasks="ok") == 0
        assert json.loads(capsys.readouterr().out)["success"] is True


class TestCacheTasksGroups:
    """A group task (``mmlu``, ``truthfulqa``) is staged through its leaf tasks.

    ``get_task_dict`` keys a group by a ``ConfigurableGroup`` object and maps it
    to a nested dict of subtasks. Treating that pair as one task staged nothing
    and put a non-string "name" into the envelope, the audit event and the
    failure message — where ``", ".join`` raised instead of exiting ``2``.
    """

    class _Group:
        """Stands in for lm-eval's ``ConfigurableGroup``: hashable, not a string."""

        group_name = "mmlu"

    @staticmethod
    def _task(dataset):
        task = NonCallableMagicMock()
        task.dataset = dataset
        return task

    def _run(self, tmp_path, task_dict):
        from forgelm.cli.subcommands import _cache

        fake_tasks = MagicMock()
        fake_tasks.get_task_dict = MagicMock(return_value=task_dict)
        with patch.dict("sys.modules", {"lm_eval": MagicMock(), "lm_eval.tasks": fake_tasks}):
            args = _build_args(
                tasks="mmlu,hellaswag", output=str(tmp_path / "cache"), audit_dir=str(tmp_path / "audit")
            )
            with pytest.raises(SystemExit) as ei:
                _cache._run_cache_tasks_cmd(args, output_format="json")
        return ei.value.code

    def test_a_nested_group_is_staged_through_its_leaf_tasks(self, tmp_path, capsys):
        datasets = {name: NonCallableMagicMock() for name in ("mmlu_anatomy", "mmlu_law", "hellaswag")}
        nested = {
            self._Group(): {
                "mmlu_anatomy": self._task(datasets["mmlu_anatomy"]),
                "mmlu_law": self._task(datasets["mmlu_law"]),
            },
            "hellaswag": self._task(datasets["hellaswag"]),
        }
        assert self._run(tmp_path, nested) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["success"] is True
        assert sorted(t["name"] for t in payload["tasks"]) == ["hellaswag", "mmlu_anatomy", "mmlu_law"]
        for dataset in datasets.values():
            dataset.download_and_prepare.assert_called_once()

    def test_an_unavailable_subtask_keeps_the_exit_two_envelope_with_string_names(self, tmp_path, capsys):
        nested = {self._Group(): {"mmlu_anatomy": self._task(NonCallableMagicMock()), "mmlu_law": self._task(None)}}
        assert self._run(tmp_path, nested) == 2
        payload = json.loads(capsys.readouterr().out)
        assert payload["success"] is False
        assert all(isinstance(t["name"], str) for t in payload["tasks"])
        assert "mmlu_law" in payload["error"]
        log = (tmp_path / "audit" / "audit_log.jsonl").read_text().splitlines()
        partial = next(json.loads(line) for line in log if "cache.populate_tasks_partial" in line)
        assert partial["tasks_unavailable"] == ["mmlu_law"]
        assert partial["tasks_cached"] == ["mmlu_anatomy"]
