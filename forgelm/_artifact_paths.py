"""Keep deletable model bundles separate from mutable run records."""

import os
from pathlib import Path, PureWindowsPath
from typing import Any, Optional

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
    "use '/' separators without trailing '/' or '/.'; no '..', symlinks, reserved run paths, or checkpoint-* roots; "
    "keep enabled evaluation.benchmark.output_dir separate from the model directory"
)


def _validate_layout(output_dir: str, value: str, benchmark_output_dir: Optional[str] = None) -> str:
    """Validate without creating directories or rewriting the requested path.

    Args:
        output_dir: Run root containing mutable audit and compliance records.
        value: Relative model-directory value from config or a runtime check.
        benchmark_output_dir: Enabled benchmark's custom destination, relative to the process working directory.

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
        if benchmark_output_dir:
            reserved_paths.append(Path(benchmark_output_dir))
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


def _benchmark_output_dir(config: Any) -> Optional[str]:
    """Reserve custom benchmark evidence only when the benchmark is enabled."""
    benchmark = config.evaluation.benchmark if config.evaluation else None
    return benchmark.output_dir if benchmark and benchmark.enabled else None


def _validate_config_layout(config: Any) -> Any:
    """Check sibling evaluation destinations after the full config is validated."""
    _validate_layout(config.training.output_dir, config.training.final_model_dir, _benchmark_output_dir(config))
    return config


def _final_model_path(output_dir: str, config: Any) -> str:
    """Recheck mutable config/filesystem state before trainer initialization/save."""
    from .config import ConfigError

    try:
        return _validate_layout(output_dir, config.training.final_model_dir, _benchmark_output_dir(config))
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc


def _check_model_destination(output_dir: str, final_path: str, config: Any) -> None:
    """Recheck the actual final/staging destination before writing or reverting."""
    from .config import ConfigError

    try:
        # relpath answers with the platform separator, '\' on Windows, while the layout check takes the config's
        # '/' form. On POSIX a backslash is part of a name; the resolve comparison below still rejects that path.
        relative = os.path.relpath(final_path, output_dir).replace("\\", "/")
        expected = _validate_layout(output_dir, relative, _benchmark_output_dir(config))
        # relpath is lexical; do not let normalization conceal a symlink/..
        # path whose actual destination differs from the checked directory.
        if Path(final_path).resolve() != Path(expected).resolve():
            raise ValueError(_POLICY)
    except (ValueError, OSError, RuntimeError) as exc:
        raise ConfigError(f"{_POLICY}; rejected destination {final_path!r}: {exc}") from exc
