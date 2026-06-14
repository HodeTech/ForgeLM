"""
GGUF export for post-training model distribution.

Wraps llama-cpp-python's ``convert_hf_to_gguf.py`` conversion script to
produce quantised GGUF files that can be served by Ollama, llama.cpp, and
compatible runtimes.

Optional dependency: ``pip install forgelm[export]``

Usage (programmatic):
    from forgelm.export import export_model
    result = export_model("./outputs/final_model", "./model.gguf", quant="q4_k_m")

Usage (CLI):
    forgelm export ./outputs/final_model --format gguf --quant q4_k_m --output model.gguf
"""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess  # noqa: S404  # nosec B404 — see _run_converter for the safe-usage rationale
import sys
from dataclasses import dataclass
from typing import List, Optional, Tuple

logger = logging.getLogger("forgelm.export")

SUPPORTED_FORMATS = frozenset({"gguf"})
SUPPORTED_QUANTS = frozenset({"q2_k", "q3_k_m", "q4_k_m", "q5_k_m", "q8_0", "f16"})

# Name of the HF→GGUF conversion script bundled with llama-cpp-python.
_CONVERTER_SCRIPT = "convert_hf_to_gguf.py"

# Quant types supported directly by convert_hf_to_gguf.py via --outtype.
# K-quants (q2_k, q3_k_m, q4_k_m, q5_k_m) require a two-step process:
# 1. HF → f16 GGUF via convert_hf_to_gguf.py  (done here)
# 2. f16 → k-quant via `llama-quantize`        (not automated; user must run manually)
# When a K-quant is requested, we produce f16 and emit a warning.
_OUTTYPE_MAP = {
    "f16": "f16",
    "q8_0": "q8_0",
    # K-quants: convert_hf_to_gguf.py only supports f16/q8_0 directly; K-quants need llama-quantize
    "q2_k": "f16",
    "q3_k_m": "f16",
    "q4_k_m": "f16",
    "q5_k_m": "f16",
}

# K-quants that need a manual second quantization step
_K_QUANTS = frozenset({"q2_k", "q3_k_m", "q4_k_m", "q5_k_m"})


@dataclass
class ExportResult:
    """Result of an ``export_model`` call."""

    success: bool
    output_path: Optional[str] = None
    format: str = "gguf"
    quant: Optional[str] = None
    sha256: Optional[str] = None
    size_bytes: Optional[int] = None
    error: Optional[str] = None
    # "config" for operator-input failures (bad format/quant, malformed
    # FORGELM_GGUF_CONVERTER) → EXIT_CONFIG_ERROR; "runtime" for converter /
    # merge / infra failures → EXIT_TRAINING_ERROR.  Ignored when success.
    error_kind: str = "runtime"


# ---------------------------------------------------------------------------
# Converter discovery
# ---------------------------------------------------------------------------


def _find_converter_script() -> str:
    """Locate llama-cpp-python's HF → GGUF conversion script.

    Checks the ``FORGELM_GGUF_CONVERTER`` environment variable first, allowing
    users to pin a specific converter script (useful when the bundled version is
    outdated or when using a standalone llama.cpp build).

    Raises:
        ImportError: When llama-cpp-python is not installed.
        FileNotFoundError: When the conversion script cannot be found.
    """
    env_override = os.environ.get("FORGELM_GGUF_CONVERTER")
    if env_override:
        # Validate: must be an existing .py file. The override is operator-
        # controlled; a non-.py path would be passed as cmd[1] to the Python
        # interpreter — accepting arbitrary executables here would let any
        # process that can write env vars escalate privileges.
        # Use casefold() so case-variations like CONVERTER.PY are accepted on
        # case-insensitive filesystems (Windows, macOS HFS+).
        if not env_override.casefold().endswith(".py"):
            raise ValueError(
                f"FORGELM_GGUF_CONVERTER must point to a Python (.py) script, "
                f"got: '{env_override}'. The converter is always a Python file; "
                "arbitrary executables are not accepted."
            )
        if os.path.isfile(env_override):
            logger.warning(
                "GGUF converter overridden via FORGELM_GGUF_CONVERTER: %s — ensure this path is from a trusted source.",
                env_override,
            )
            return env_override
        raise FileNotFoundError(f"FORGELM_GGUF_CONVERTER is set to '{env_override}' but the file does not exist.")

    try:
        import llama_cpp
    except ImportError as e:
        raise ImportError(
            "llama-cpp-python is required for GGUF export.  Install the export extra: pip install 'forgelm[export]'"
        ) from e

    pkg_root = os.path.dirname(os.path.abspath(llama_cpp.__file__))

    candidates = [
        os.path.join(pkg_root, _CONVERTER_SCRIPT),
        os.path.join(pkg_root, "scripts", _CONVERTER_SCRIPT),
        # Some versions place it one level up
        os.path.join(os.path.dirname(pkg_root), _CONVERTER_SCRIPT),
    ]

    for path in candidates:
        if os.path.isfile(path):
            logger.debug("Found converter at %s", path)
            return path

    raise FileNotFoundError(
        f"{_CONVERTER_SCRIPT} not found inside the llama-cpp-python installation.  "
        "Try: pip install 'llama-cpp-python>=0.2.90'  "
        f"Or set FORGELM_GGUF_CONVERTER=/path/to/{_CONVERTER_SCRIPT} to use a custom script."
    )


# ---------------------------------------------------------------------------
# Adapter merge helper
# ---------------------------------------------------------------------------


def _merge_adapter(model_path: str, adapter_path: str, merged_dir: str) -> None:
    """Merge a PEFT LoRA adapter into the base model and save to *merged_dir*.

    Forces ``torch_dtype=float16`` for the merge: GGUF f16 conversion will cast
    to fp16 anyway, and bf16 math on CPU silently falls back to fp32 (much
    slower for large models).
    """
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    logger.info("Merging adapter %s into base model %s ...", adapter_path, model_path)
    # ``trust_remote_code=False`` is the secure default (Phase 7 acceptance):
    # the merge step is part of the GGUF export pipeline; loading the base
    # model must not execute repo-bundled code.  Operators with a custom
    # architecture should fork and pre-convert before exporting.
    tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=False)
    model = AutoModelForCausalLM.from_pretrained(
        model_path, torch_dtype=torch.float16, device_map="cpu", trust_remote_code=False
    )
    model = PeftModel.from_pretrained(model, adapter_path)
    model = model.merge_and_unload()

    os.makedirs(merged_dir, exist_ok=True)
    model.save_pretrained(merged_dir, safe_serialization=True)
    tokenizer.save_pretrained(merged_dir)
    logger.info("Merged model saved to %s", merged_dir)


# ---------------------------------------------------------------------------
# SHA-256 helper
# ---------------------------------------------------------------------------


def _sha256_file(path: str) -> str:
    """Compute the SHA-256 digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Compliance integration
# ---------------------------------------------------------------------------


def _update_integrity_manifest(model_dir: str, export_result: ExportResult) -> None:
    """Append the exported artifact to model_integrity.json if it exists."""
    import json

    integrity_path = os.path.join(model_dir, "model_integrity.json")
    if not os.path.isfile(integrity_path):
        return

    try:
        with open(integrity_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        artifact = {
            "file": os.path.basename(export_result.output_path or ""),
            "format": export_result.format,
            "quant": export_result.quant,
            "sha256": export_result.sha256,
            "size_bytes": export_result.size_bytes,
        }
        data.setdefault("exported_artifacts", []).append(artifact)

        with open(integrity_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        logger.info("model_integrity.json updated with exported artifact.")
    except (OSError, TypeError, ValueError) as e:
        # OSError: filesystem write / read failure on integrity_path.
        # TypeError: ``json.dump`` rejecting an unserialisable artefact field.
        # ValueError: corrupt or partial existing manifest (covers
        # ``json.JSONDecodeError`` since it subclasses ``ValueError``).
        # Updating the manifest is non-fatal: the artefact itself was already
        # exported successfully and its on-disk SHA-256 is recoverable.
        logger.debug("Could not update model_integrity.json: %s", e)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _validate_export_request(fmt: str, quant: str) -> Optional[ExportResult]:
    """Reject unsupported format/quant up front; return None when both are valid."""
    if fmt not in SUPPORTED_FORMATS:
        return ExportResult(
            success=False,
            format=fmt,
            error=f"Unsupported format '{fmt}'. Supported: {', '.join(sorted(SUPPORTED_FORMATS))}",
            error_kind="config",
        )
    if quant not in SUPPORTED_QUANTS:
        return ExportResult(
            success=False,
            quant=quant,
            error=f"Unsupported quantisation '{quant}'. Supported: {', '.join(sorted(SUPPORTED_QUANTS))}",
            error_kind="config",
        )
    return None


def _resolve_kquant_path(quant: str, output_path: str) -> Tuple[str, str]:
    """K-quants need a separate llama-quantize step.

    Returns ``(actual_quant, actual_output_path)``: when *quant* is a K-quant we
    redirect the converter output to a ``.f16.gguf`` sibling so the integrity
    manifest never records a SHA-256 against a file that doesn't match the
    requested quant. Non-K-quants pass through unchanged.
    """
    if quant not in _K_QUANTS:
        return quant, output_path
    if output_path.endswith(".gguf"):
        actual_output_path = output_path[: -len(".gguf")] + ".f16.gguf"
    else:
        actual_output_path = output_path + ".f16.gguf"
    logger.warning(
        "K-quant '%s' is not supported directly by %s. "
        "Producing an intermediate f16 GGUF at %s instead. "
        "To get a %s GGUF, run `llama-quantize %s %s %s` afterward.",
        quant,
        _CONVERTER_SCRIPT,
        actual_output_path,
        quant,
        actual_output_path,
        output_path,
        quant.upper(),
    )
    return "f16", actual_output_path


def _build_converter_command(
    converter: str, source_path: str, actual_output_path: str, requested_quant: str, extra_args: Optional[List[str]]
) -> List[str]:
    """Compose the argv for the bundled HF→GGUF converter."""
    cmd: List[str] = [
        sys.executable,
        converter,
        source_path,
        "--outfile",
        actual_output_path,
        "--outtype",
        _OUTTYPE_MAP[requested_quant],
    ]
    if extra_args:
        cmd.extend(extra_args)
    return cmd


def _run_converter(cmd: List[str], fmt: str, actual_quant: str) -> Optional[ExportResult]:
    """Run the converter and return an ExportResult on failure (None on success)."""
    logger.info("Running GGUF conversion: %s", " ".join(cmd))
    # Bandit B603 / ruff S603: cmd[0] is sys.executable (absolute), cmd[1] comes
    # from _find_converter_script (resolved against an installed package or an
    # FORGELM_GGUF_CONVERTER env-var the user supplied), and the remaining
    # argv entries are paths the caller already controls. No shell=True, no
    # user-supplied executable. The suppression covers the multi-line call.
    try:
        proc = subprocess.run(  # noqa: S603, S607  # nosec B603 B607
            cmd,  # noqa: S603  # nosec B603
            capture_output=True,
            text=True,
            check=False,
            timeout=3600,
        )
    except subprocess.TimeoutExpired:
        return ExportResult(
            success=False,
            format=fmt,
            quant=actual_quant,
            error="GGUF conversion timed out after 3600 seconds.",
        )
    except Exception as e:  # noqa: BLE001 — best-effort: subprocess.run for the GGUF converter (llama.cpp / convert-hf-to-gguf.py) crosses OSError (binary missing), CalledProcessError (non-zero exit before check=False catches it), and the converter's own deep-stack errors; ExportResult(success=False) is the documented public contract for the export pipeline.  # NOSONAR
        return ExportResult(success=False, format=fmt, quant=actual_quant, error=str(e))

    if proc.returncode == 0:
        return None

    full_stderr = (proc.stderr or "").strip()
    full_stdout = (proc.stdout or "").strip()
    if full_stderr:
        logger.error("GGUF converter stderr:\n%s", full_stderr)
    if full_stdout:
        logger.error("GGUF converter stdout:\n%s", full_stdout)
    logger.error("GGUF conversion failed (exit %d)", proc.returncode)
    error_detail = full_stderr or full_stdout or "unknown error"
    return ExportResult(
        success=False,
        format=fmt,
        quant=actual_quant,
        error=f"Converter exited with code {proc.returncode}: {error_detail[:500]}",
    )


def _cleanup_merged_dir(merged_dir: Optional[str]) -> None:
    """Remove the temporary adapter-merge directory if one was created."""
    if not (merged_dir and os.path.isdir(merged_dir)):
        return
    import shutil

    shutil.rmtree(merged_dir, ignore_errors=True)
    logger.debug("Cleaned up temporary merged directory: %s", merged_dir)


def export_model(
    model_path: str,
    output_path: str,
    *,
    output_format: str = "gguf",
    quant: str = "q4_k_m",
    adapter: Optional[str] = None,
    update_integrity: bool = True,
    extra_args: Optional[List[str]] = None,
) -> ExportResult:
    """Export a HuggingFace model to GGUF format.

    If *adapter* is provided the adapter is first merged into the base model
    (saved to a temporary ``_merged`` sibling directory) before conversion.

    Args:
        model_path: Path to a saved HuggingFace model directory.
        output_path: Destination ``.gguf`` file path.
        output_format: Export format. Only ``"gguf"`` is currently supported.
            (Renamed from ``format=`` to avoid shadowing the ``format`` builtin.)
        quant: Quantisation type.  One of
            ``q2_k, q3_k_m, q4_k_m, q5_k_m, q8_0, f16``.
        adapter: Optional PEFT adapter directory to merge before export.
        update_integrity: When ``True``, appends the exported artifact's
            SHA-256 to ``model_integrity.json`` in the model directory.
        extra_args: Additional CLI arguments forwarded to the converter script.

    Returns:
        :class:`ExportResult` with SHA-256, file size, and output path.
    """
    fmt = output_format.lower()
    quant = quant.lower()

    rejection = _validate_export_request(fmt, quant)
    if rejection is not None:
        return rejection

    try:
        converter = _find_converter_script()
    except ValueError as e:
        # A malformed FORGELM_GGUF_CONVERTER (non-.py path) is operator input,
        # so it is a config error (exit 1), not a runtime failure (F-P7-OPUS-36).
        return ExportResult(success=False, format=fmt, quant=quant, error=str(e), error_kind="config")
    except (ImportError, FileNotFoundError) as e:
        # Missing llama-cpp / converter script is an environment/infra failure.
        return ExportResult(success=False, format=fmt, quant=quant, error=str(e))

    # Merge adapter if requested
    source_path = model_path
    merged_dir: Optional[str] = None
    if adapter:
        merged_dir = model_path.rstrip("/\\") + "_merged_for_export"
        try:
            _merge_adapter(model_path, adapter, merged_dir)
            source_path = merged_dir
        except Exception as e:  # noqa: BLE001 — best-effort: _merge_adapter loads HF base + PEFT adapter and writes the merged dir; failure surface includes OSError (disk/path), RuntimeError (CUDA/dtype), KeyError (missing adapter config), and HF/PEFT-internal errors.  ExportResult(success=False) is the documented hard-failure contract.  # NOSONAR
            return ExportResult(success=False, format=fmt, quant=quant, error=f"Adapter merge failed: {e}")

    actual_quant, actual_output_path = _resolve_kquant_path(quant, output_path)
    cmd = _build_converter_command(converter, source_path, actual_output_path, quant, extra_args)

    try:
        failure = _run_converter(cmd, fmt, actual_quant)
        if failure is not None:
            return failure
    finally:
        _cleanup_merged_dir(merged_dir)

    if not os.path.isfile(actual_output_path):
        return ExportResult(
            success=False,
            format=fmt,
            quant=actual_quant,
            error=f"Conversion appeared to succeed but output file not found: {actual_output_path}",
        )

    # Compute artifact stats — reflect the file actually written, not the requested quant
    digest = _sha256_file(actual_output_path)
    size_bytes = os.path.getsize(actual_output_path)

    result = ExportResult(
        success=True,
        output_path=actual_output_path,
        format=fmt,
        quant=actual_quant,
        sha256=digest,
        size_bytes=size_bytes,
    )

    logger.info(
        "GGUF export complete: %s (%.1f GB, quant=%s, sha256=%s…)",
        actual_output_path,
        size_bytes / (1024**3),
        actual_quant,
        digest[:12],
    )

    if update_integrity:
        _update_integrity_manifest(model_path, result)

    return result
