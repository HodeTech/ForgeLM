import logging
import os
import shutil
import tarfile
import time
import uuid
from typing import Optional

from huggingface_hub import login

logger = logging.getLogger("forgelm.utils")

# HF token paths in priority order (modern XDG path first, then legacy)
_HF_TOKEN_PATHS = [
    os.path.expanduser("~/.cache/huggingface/token"),
    os.path.expanduser("~/.huggingface/token"),
]


def setup_authentication(token: Optional[str] = None) -> None:
    """Configures Hugging Face authentication."""
    hf_token = token or os.getenv("HUGGINGFACE_TOKEN")

    if not hf_token:
        # Fallback to local token store if nothing provided
        for token_path in _HF_TOKEN_PATHS:
            try:
                with open(token_path, "r") as f:
                    hf_token = f.read().strip()
                if hf_token:
                    break
            except (OSError, UnicodeDecodeError) as e:
                # Auth is best-effort (login failure → WARNING + continue), so a
                # missing/unreadable/garbled token file must not abort the run.
                # OSError covers FileNotFoundError, PermissionError, and
                # IsADirectoryError; UnicodeDecodeError covers a binary file at
                # the token path (F-P2-FAB-29).
                logger.debug("Could not read HF token from %s: %s", token_path, e)
                continue

        if not hf_token:
            logger.warning("No Hugging Face token found. Some models/datasets might not load.")
            return

    logger.info("Authenticating with Hugging Face...")
    try:
        login(token=hf_token)
        logger.info("Hugging Face authentication successful.")
    except Exception as e:  # noqa: BLE001 — best-effort: huggingface_hub login surfaces network errors (ConnectionError/Timeout), API errors (HTTPError, repo-permission), config errors (LocalEntryNotFoundError), and credential errors (ValueError on malformed token); auth failure is non-fatal so a public-models-only run can still proceed.  # NOSONAR
        logger.warning(
            "Hugging Face authentication failed: %s. Private models and gated datasets may not be accessible.",
            e,
        )


def manage_checkpoints(checkpoint_dir: str, action: str = "keep") -> None:
    """Handles logic for deleting or compressing checkpoints post-training.

    Actions:
        keep: No-op (default safety behavior — checkpoints remain as-is)
        delete: Remove every ``checkpoint-*`` subdirectory (not the output dir)
        compress: Create a tar.gz archive next to the dir and keep originals

    Reachability (F-P2-FAB-28): the production training path
    (``forgelm/cli/_training.py``) always calls this with ``action="keep"`` —
    there is intentionally no YAML field or CLI flag that selects ``delete`` /
    ``compress``, so an unattended run never destroys or rewrites checkpoints
    by config. ``delete`` / ``compress`` are exposed only through the
    Experimental library API (``from forgelm import manage_checkpoints``) for
    callers who manage retention explicitly in a notebook or script; see
    ``docs/reference/library_api_reference.md``. They are behaviour-tested in
    ``tests/test_utils.py`` so the latent bugs that survived behind the dead
    config path stay fixed.
    """
    if not os.path.exists(checkpoint_dir):
        return

    if action == "keep":
        logger.debug("Keeping checkpoints in %s (no cleanup).", checkpoint_dir)
    elif action == "delete":
        # Only remove checkpoint-* subdirectories, not the entire output dir.
        # Deletion is this branch's primary job, so failures are NOT swallowed:
        # each is logged at WARNING and counted separately (F-P2-FAB-26).
        deleted = 0
        failed = 0
        for entry in os.listdir(checkpoint_dir):
            entry_path = os.path.join(checkpoint_dir, entry)
            if os.path.isdir(entry_path) and entry.startswith("checkpoint-"):
                try:
                    shutil.rmtree(entry_path)
                    deleted += 1
                except OSError as e:
                    failed += 1
                    logger.warning("Failed to delete checkpoint %s: %s", entry_path, e)
        logger.info("Deleted %d checkpoint directories in %s.", deleted, checkpoint_dir)
        if failed:
            logger.warning("%d checkpoint directories in %s could not be deleted.", failed, checkpoint_dir)
    elif action == "compress":
        # Use UUID suffix to prevent archive name collisions. Anchor the archive
        # next to the checkpoint tree (not the process CWD) so a CI step or a
        # caller in a different working dir doesn't scatter it outside the
        # training output (F-P2-FAB-27).
        archive_name = f"checkpoints_{int(time.time())}_{uuid.uuid4().hex[:6]}.tar.gz"
        parent = os.path.dirname(os.path.abspath(checkpoint_dir)) or "."
        archive_path = os.path.join(parent, archive_name)
        logger.info("Compressing checkpoints to %s...", archive_path)
        try:
            with tarfile.open(archive_path, "w:gz") as tar:
                tar.add(checkpoint_dir, arcname=os.path.basename(checkpoint_dir))
        except OSError:
            # Don't leave a torn .tar.gz behind on failure.
            if os.path.exists(archive_path):
                try:
                    os.unlink(archive_path)
                except OSError as cleanup_err:
                    logger.warning("Could not remove partial archive %s: %s", archive_path, cleanup_err)
            raise
        logger.info("Compression complete.")
    else:
        logger.warning("Unknown checkpoint action: '%s'. Keeping checkpoints.", action)
