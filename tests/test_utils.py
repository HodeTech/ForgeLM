"""Tests for forgelm/utils.py (M7 / F-P8-C-11).

The two public helpers in ``forgelm.utils`` never executed in the suite —
every caller either name-exported them via the lazy facade or replaced the
whole module with a lambda stub. ``manage_checkpoints`` does filesystem
deletion under the training ``output_dir`` (a destructive op a regression
could point at the wrong directory) and ``setup_authentication`` resolves
an HF token across env-var / two file-path fallbacks and swallows login
failure. These tests exercise the real raise/branch paths with ``login``
mocked at the module boundary and real filesystem fixtures under
``tmp_path`` — no network, no GPU, no real HF login.
"""

from __future__ import annotations

import tarfile
from pathlib import Path
from unittest.mock import patch

from forgelm import utils

# ---------------------------------------------------------------------------
# setup_authentication
# ---------------------------------------------------------------------------


class TestSetupAuthentication:
    def test_explicit_token_passed_to_login(self):
        with patch("forgelm.utils.login") as mock_login:
            utils.setup_authentication("hf_explicit")
        mock_login.assert_called_once_with(token="hf_explicit")

    def test_env_token_used_when_no_arg(self, monkeypatch):
        monkeypatch.setenv("HUGGINGFACE_TOKEN", "hf_from_env")
        with patch("forgelm.utils.login") as mock_login:
            utils.setup_authentication()
        mock_login.assert_called_once_with(token="hf_from_env")

    def test_file_store_fallback(self, monkeypatch, tmp_path):
        monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
        token_file = tmp_path / "token"
        token_file.write_text("hf_from_file\n", encoding="utf-8")
        # Point the token-path roster at our fixture file (modern path first).
        monkeypatch.setattr(utils, "_HF_TOKEN_PATHS", [str(token_file)])
        with patch("forgelm.utils.login") as mock_login:
            utils.setup_authentication()
        mock_login.assert_called_once_with(token="hf_from_file")

    def test_no_token_anywhere_warns_and_skips_login(self, monkeypatch, caplog):
        monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
        monkeypatch.setattr(utils, "_HF_TOKEN_PATHS", ["/nonexistent/forgelm-token"])
        with patch("forgelm.utils.login") as mock_login:
            with caplog.at_level("WARNING"):
                utils.setup_authentication()
        mock_login.assert_not_called()
        assert any("No Hugging Face token" in r.message for r in caplog.records)

    def test_login_failure_is_swallowed(self, caplog):
        # Auth failure is non-fatal (public-models-only runs proceed); the
        # helper must warn, not raise.
        with patch("forgelm.utils.login", side_effect=RuntimeError("network down")):
            with caplog.at_level("WARNING"):
                utils.setup_authentication("hf_token")  # must not raise
        assert any("authentication failed" in r.message for r in caplog.records)

    def test_unreadable_token_file_does_not_crash(self, monkeypatch, tmp_path):
        # F-P2-FAB-29: a PermissionError on the token store must be skipped (auth
        # is best-effort), not propagated as an exit-2 crash before any network
        # call. Simulate the unreadable file via an open() that raises
        # PermissionError, and provide a readable fallback path afterward.
        monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
        good_file = tmp_path / "token"
        good_file.write_text("hf_recovered\n", encoding="utf-8")
        monkeypatch.setattr(utils, "_HF_TOKEN_PATHS", ["/locked/token", str(good_file)])

        real_open = open

        def fake_open(path, *args, **kwargs):
            if path == "/locked/token":
                raise PermissionError(13, "Permission denied")
            return real_open(path, *args, **kwargs)

        with patch("builtins.open", side_effect=fake_open):
            with patch("forgelm.utils.login") as mock_login:
                utils.setup_authentication()  # must not raise
        # The locked path is skipped; the next readable path supplies the token.
        mock_login.assert_called_once_with(token="hf_recovered")


# ---------------------------------------------------------------------------
# manage_checkpoints
# ---------------------------------------------------------------------------


class TestManageCheckpoints:
    def _make_tree(self, root: Path) -> None:
        (root / "checkpoint-100").mkdir(parents=True)
        (root / "checkpoint-200").mkdir()
        (root / "final_model").mkdir()
        (root / "checkpoint-100" / "weights.bin").write_text("x", encoding="utf-8")
        (root / "final_model" / "config.json").write_text("{}", encoding="utf-8")

    def test_missing_dir_is_noop(self, tmp_path):
        # Must not raise on a path that does not exist.
        utils.manage_checkpoints(str(tmp_path / "does-not-exist"), action="delete")

    def test_keep_preserves_everything(self, tmp_path):
        self._make_tree(tmp_path)
        utils.manage_checkpoints(str(tmp_path), action="keep")
        assert (tmp_path / "checkpoint-100").is_dir()
        assert (tmp_path / "checkpoint-200").is_dir()
        assert (tmp_path / "final_model").is_dir()

    def test_delete_removes_only_checkpoint_dirs(self, tmp_path):
        self._make_tree(tmp_path)
        utils.manage_checkpoints(str(tmp_path), action="delete")
        # checkpoint-* dirs gone…
        assert not (tmp_path / "checkpoint-100").exists()
        assert not (tmp_path / "checkpoint-200").exists()
        # …but the promoted model and other content survive (no data loss).
        assert (tmp_path / "final_model" / "config.json").is_file()

    def test_compress_writes_archive_next_to_checkpoint_dir(self, tmp_path, monkeypatch):
        # F-P2-FAB-27: the archive is anchored next to the checkpoint dir, NOT
        # the process CWD. Run from an unrelated CWD and assert the tarball
        # lands in the checkpoint dir's parent regardless.
        ckpt = tmp_path / "run" / "output"
        self._make_tree(ckpt)
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        utils.manage_checkpoints(str(ckpt), action="compress")
        # Archive is in the checkpoint dir's parent (tmp_path/run), not CWD.
        assert not list(elsewhere.glob("checkpoints_*.tar.gz"))
        archives = list((tmp_path / "run").glob("checkpoints_*.tar.gz"))
        assert len(archives) == 1
        assert tarfile.is_tarfile(archives[0])
        # Originals remain after compression.
        assert (ckpt / "checkpoint-100").is_dir()

    def test_compress_failure_removes_partial_archive(self, tmp_path, monkeypatch):
        # F-P2-FAB-27: a failure mid-archive must not leave a torn .tar.gz.
        ckpt = tmp_path / "run" / "output"
        self._make_tree(ckpt)

        def boom(*args, **kwargs):
            raise OSError("disk full")

        # Let the archive file get created, then fail inside the with-block so
        # the cleanup path (os.unlink of the partial) is exercised.
        import tarfile as _tarfile

        real_open = _tarfile.open

        class FailingTar:
            def __init__(self, path):
                self._fh = real_open(path, "w:gz")

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self._fh.close()
                return False

            def add(self, *args, **kwargs):
                raise OSError("disk full")

        monkeypatch.setattr(utils.tarfile, "open", lambda path, mode: FailingTar(path))
        import pytest

        with pytest.raises(OSError, match="disk full"):
            utils.manage_checkpoints(str(ckpt), action="compress")
        # No partial archive left behind.
        assert not list((tmp_path / "run").glob("checkpoints_*.tar.gz"))

    def test_delete_failure_is_counted_not_swallowed(self, tmp_path, monkeypatch, caplog):
        # F-P2-FAB-26: a deletion failure must be logged at WARNING and excluded
        # from the "Deleted N" count, not silently counted as a success.
        self._make_tree(tmp_path)

        real_rmtree = utils.shutil.rmtree

        def selective_rmtree(path, *args, **kwargs):
            if path.endswith("checkpoint-100"):
                raise OSError("permission denied")
            return real_rmtree(path, *args, **kwargs)

        monkeypatch.setattr(utils.shutil, "rmtree", selective_rmtree)
        with caplog.at_level("INFO"):
            utils.manage_checkpoints(str(tmp_path), action="delete")
        # The failing dir survives; the other is gone.
        assert (tmp_path / "checkpoint-100").is_dir()
        assert not (tmp_path / "checkpoint-200").exists()
        # Failure surfaced at WARNING, not silently swallowed; the success count
        # excludes the failed dir (was 2 with ignore_errors=True).
        assert any("could not be deleted" in r.message for r in caplog.records)
        assert any("Deleted 1 checkpoint" in r.message for r in caplog.records)

    def test_unknown_action_warns_and_preserves(self, tmp_path, caplog):
        self._make_tree(tmp_path)
        with caplog.at_level("WARNING"):
            utils.manage_checkpoints(str(tmp_path), action="bogus")
        assert (tmp_path / "checkpoint-100").is_dir()
        assert any("Unknown checkpoint action" in r.message for r in caplog.records)
