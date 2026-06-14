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

    def test_compress_writes_archive_and_keeps_originals(self, tmp_path, monkeypatch):
        self._make_tree(tmp_path)
        # The compress branch writes the tar.gz to CWD; isolate it under tmp.
        monkeypatch.chdir(tmp_path)
        utils.manage_checkpoints(str(tmp_path), action="compress")
        archives = list(tmp_path.glob("checkpoints_*.tar.gz"))
        assert len(archives) == 1
        assert tarfile.is_tarfile(archives[0])
        # Originals remain after compression.
        assert (tmp_path / "checkpoint-100").is_dir()

    def test_unknown_action_warns_and_preserves(self, tmp_path, caplog):
        self._make_tree(tmp_path)
        with caplog.at_level("WARNING"):
            utils.manage_checkpoints(str(tmp_path), action="bogus")
        assert (tmp_path / "checkpoint-100").is_dir()
        assert any("Unknown checkpoint action" in r.message for r in caplog.records)
