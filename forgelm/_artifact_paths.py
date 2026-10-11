"""Keep deletable model bundles separate from mutable run records."""

import os
from pathlib import Path, PureWindowsPath

from pydantic import ValidationInfo

_RESERVED_ROOTS = frozenset(
    {
        "audit_log.jsonl",
        "audit_log.jsonl.manifest.json",
        "data_audit_report.json",
        "compliance",
        "benchmark",
        "safety",
        "judge",
        "runs",
    }
)
_POLICY = (
    "training.final_model_dir must name an isolated relative subdirectory of training.output_dir; "
    "use '/' separators without trailing '/' or '/.'; no '..', symlinks, reserved run paths, or checkpoint-* roots"
)


def _validate_layout(output_dir: str, value: str) -> str:
    """Validate without creating directories or rewriting the requested path.

    Args:
        output_dir: Run root containing mutable audit and compliance records.
        value: Relative model-directory value from config or a runtime check.

    Returns:
        The joined path, retaining the operator's original spelling.

    Raises:
        ValueError: The model directory escapes or overlaps shared run paths,
            contains a symlink/file component, or cannot be resolved.
    """
    relative = Path(value)
    if (
        not value.strip()
        or value.endswith(("/", "/."))
        or "\x00" in value
        or "\\" in value
        or relative.is_absolute()
        or PureWindowsPath(value).drive
        or ".." in relative.parts
    ):
        raise ValueError(_POLICY)
    try:
        root = Path(output_dir).resolve()
        candidate = (root / relative).resolve()
        if candidate == root or root not in candidate.parents:
            raise ValueError(_POLICY)
        first = relative.parts[0].casefold()
        if first in _RESERVED_ROOTS or first.startswith("checkpoint-"):
            raise ValueError(_POLICY)
        cursor = root
        for part in relative.parts:
            cursor /= part
            if cursor.is_symlink() or (cursor.exists() and not cursor.is_dir()):
                raise ValueError(_POLICY)
        # A reserved directory can itself be an alias into an otherwise valid
        # model tree. Reject both overlap directions before hashing or deletion.
        reserved_paths = [root / name for name in _RESERVED_ROOTS] + list(root.glob("checkpoint-*"))
        for reserved_path in reserved_paths:
            reserved = reserved_path.resolve()
            if candidate == reserved or candidate in reserved.parents or reserved in candidate.parents:
                raise ValueError(_POLICY)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"{_POLICY}; cannot resolve the destination: {exc}") from exc
    return os.path.join(output_dir, value)


def _validate_config_directory(value: str, info: ValidationInfo) -> str:
    """Use a field validator so sibling data is available on Pydantic 2.0 too."""
    if "output_dir" in info.data:
        _validate_layout(info.data["output_dir"], value)
    return value


def _final_model_path(output_dir: str, final_model_dir: str) -> str:
    """Recheck mutable config/filesystem state before trainer initialization/save."""
    from .config import ConfigError

    try:
        return _validate_layout(output_dir, final_model_dir)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc


def _check_model_destination(output_dir: str, final_path: str) -> None:
    """Recheck the actual final/staging destination before writing or reverting."""
    from .config import ConfigError

    try:
        relative = os.path.relpath(final_path, output_dir)
        expected = _validate_layout(output_dir, relative)
        # relpath is lexical; do not let normalization conceal a symlink/..
        # path whose actual destination differs from the checked directory.
        if Path(final_path).resolve() != Path(expected).resolve():
            raise ValueError(_POLICY)
    except (ValueError, OSError, RuntimeError) as exc:
        raise ConfigError(f"{_POLICY}; rejected destination {final_path!r}: {exc}") from exc
