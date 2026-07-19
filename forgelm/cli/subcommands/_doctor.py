"""``forgelm doctor`` env-check subcommand (Phase 34).

The first command an operator should run after installation.  Probes the
environment ForgeLM expects (Python, torch, CUDA, GPU, optional extras,
HF Hub reachability, disk space, audit-log identity) and emits a
structured pass / warn / fail report — text by default, JSON envelope
when ``--output-format json`` is set.

Design goals (closure plan §9.5 Phase 34):

- **Self-contained.**  Probes use stdlib only at module load.  Heavy
  deps (torch, huggingface_hub) are imported lazily inside the
  individual check functions so ``forgelm doctor`` can run on a
  brand-new machine where torch is not yet installed without crashing.
- **Honest pass / warn / fail.**  ``warn`` is reserved for "operator
  may want to address this" (e.g. optional extra missing); ``fail``
  is reserved for "ForgeLM cannot work this way" (e.g. Python version
  too old).  Air-gap (``--offline``) skips the HF Hub network probe
  rather than reporting it as a failure.
- **Public exit-code contract.**  0 when every check passes, 1 when at
  least one fails (config-error class), 2 when a probe itself crashed
  (runtime-error class — operator-actionable bug, not config).

Note on env-var reads (CLAUDE.md "config-driven runtime" rule):
``forgelm doctor`` deliberately reads ``FORGELM_OPERATOR``,
``FORGELM_ALLOW_ANONYMOUS_OPERATOR``, ``HF_ENDPOINT``, ``HF_HUB_CACHE``,
``HF_HOME``, and ``HF_HUB_OFFLINE`` / ``TRANSFORMERS_OFFLINE`` directly
from ``os.environ`` rather than from validated YAML.  This is *not* a
violation of the config-driven principle — those env vars are read
verbatim by downstream code (``forgelm/compliance.py::AuditLogger`` for
the FORGELM_* identity vars; ``huggingface_hub`` upstream for the HF_*
vars).  Doctor's job is to predict what training will see; if doctor
read them from YAML while the training-time code read them from env,
doctor would silently lie.  The broader question of moving the audit
identity to YAML is a Phase 20 / RetentionConfig design topic, not a
doctor bug — flagging this here so future review bots do not refile it.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Tuple

from .._abi_check import (
    ABI_BROKEN,
    ABI_OK,
    ABI_SKIPPED_NUMPY,
    ABI_SKIPPED_TORCH,
    compute_numpy_torch_abi_status,
    format_abi_remediation,
)
from .._exit_codes import EXIT_CONFIG_ERROR, EXIT_SUCCESS, EXIT_TRAINING_ERROR
from .._logging import logger

# Status vocabulary.  Centralised so a future rename (e.g. "warn" ->
# "warning") cannot drift across the renderers and the JSON contract.
_STATUS_PASS = "pass"  # nosec B105 — status token, not a password (Codacy false positive)
_STATUS_WARN = "warn"
_STATUS_FAIL = "fail"

# Probe name vocabulary.  Centralised the same way the status tokens are,
# so renaming a probe (e.g. ``operator.identity`` → ``audit.operator``) is
# a single-line edit and downstream JSON consumers can grep for these
# constants rather than scattered string literals.  Wave 2a Round-2 nit.
_PROBE_FORGELM_INSTALL = "forgelm.install"
_PROBE_PYTHON_VERSION = "python.version"
_PROBE_TORCH_INSTALLED = "torch.installed"
_PROBE_TORCH_CUDA = "torch.cuda"
_PROBE_NUMPY_TORCH_ABI = "numpy.torch_abi"
_PROBE_GPU_INVENTORY = "gpu.inventory"
_PROBE_HF_HUB_REACHABLE = "hf_hub.reachable"
_PROBE_HF_HUB_OFFLINE_CACHE = "hf_hub.offline_cache"
_PROBE_DISK_WORKSPACE = "disk.workspace"
_PROBE_OPERATOR_IDENTITY = "operator.identity"
# Phase 15 Task 3 — pypdf font-fallback normalisation profile loaded?
_PROBE_PYPDF_NORMALISE_TURKISH = "pypdf_normalise.turkish"

# Optional extras advertised in pyproject.toml [project.optional-dependencies].
# Each entry is ``(extra_name, importable_module, human_purpose_blurb)``.
# ``importable_module`` is the package the extra installs that we probe via
# ``importlib.util.find_spec`` — a successful spec resolve means the extra is
# present.  Doctor never *imports* the module (avoids triggering torch /
# transformers / spacy heavy load on a healthy probe).
#
# Wave 2a Round-1 review (qodo bot): extras names audited 2026-05-02 against
# pyproject.toml.  The original list used aspirational names ("deepspeed",
# "evaluation", "wandb", "mergekit") that did not match the published extras
# ("distributed", "eval", "tracking", "merging") — the install-hint a
# missing-extra warn produced was unactionable because the suggested
# ``pip install 'forgelm[deepspeed]'`` would 404.  Now aligned with the
# actual pyproject names.
_OPTIONAL_EXTRAS: Tuple[Tuple[str, str, str], ...] = (
    ("qlora", "bitsandbytes", "4-bit / 8-bit QLoRA training"),
    ("unsloth", "unsloth", "Unsloth-accelerated training (Linux GPUs only)"),
    ("distributed", "deepspeed", "DeepSpeed ZeRO + offload distributed training"),
    ("eval", "lm_eval", "lm-evaluation-harness benchmark scoring"),
    ("tracking", "wandb", "Weights & Biases experiment tracking"),
    # NOTE: model merging is implemented natively (peft + torch core deps); the
    # `merging` extra is a no-op and intentionally has no probe row here.
    ("export", "llama_cpp", "GGUF export via llama-cpp-python (Linux + macOS only)"),
    ("ingestion", "pypdf", "PDF / DOCX / EPUB ingestion"),
    ("ingestion-pii-ml", "presidio_analyzer", "Presidio ML-NER PII detection in audit"),
    ("ingestion-scale", "datasketch", "MinHash LSH for >50K-row dedup"),
)

# Disk-space thresholds for the workspace check.  Tuned for "enough room
# to download a 7B-parameter model + run one fine-tune" — ~50 GB safe,
# 10-50 GB warn, <10 GB fail.
_DISK_FAIL_GB = 10.0
_DISK_WARN_GB = 50.0


@dataclass
class _CheckResult:
    """Outcome of one diagnostic probe.

    ``status`` is one of pass / warn / fail.  ``name`` is operator-
    facing; ``detail`` carries the actual finding (Python version
    string, GPU count, missing extra name, etc.) so the operator can
    act without re-running the probe themselves.
    """

    name: str
    status: str
    detail: str
    # Optional structured fields that downstream tooling may want
    # (CI gate that filters by category, dashboard widget, etc.).
    extras: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _check_forgelm_install() -> _CheckResult:
    """Report *which* ForgeLM this process is actually running.

    Doctor's job is to predict what training will see, and the first
    thing training sees is the package itself.  A console script's
    ``sys.path[0]`` is its own ``bin`` directory, so an operator can
    easily be running a ``site-packages`` copy that is nothing like the
    source tree they are looking at — the failure mode is silent, and it
    makes a bug report unactionable because the maintainer cannot tell
    which code produced the traceback.  Reporting the resolved package
    directory alongside the version turns "it fails on 0.9.0" into a
    fact rather than an assumption.

    Both facts are read from the **imported module** rather than from
    distribution metadata.  ``importlib.metadata`` resolves by scanning
    ``sys.path`` in order, so a leftover ``forgelm.egg-info`` in the cwd
    shadows the real ``dist-info`` and answers for a distribution that is
    not the one running — reporting an authoritative-sounding install
    kind derived from the wrong metadata directory would be worse than
    reporting nothing.  ``forgelm.__file__`` cannot lie: it is where the
    interpreter actually loaded the code from.

    ``in_site_packages`` is likewise observed, not inferred: the resolved
    path is tested against ``sysconfig``'s ``purelib`` / ``platlib``.
    False means the operator is running a source tree (a checkout, or an
    editable install pointing at one) — the fact worth knowing when a
    traceback does not match the released source.

    Always ``pass`` — every location here is legitimate (site-packages is
    the normal operator case, a source tree the normal contributor case).
    The probe exists to *record* which one is active, not to grade it.
    """
    import sysconfig

    import forgelm

    version = getattr(forgelm, "__version__", "unknown")
    if not forgelm.__file__:
        # Namespace-package shape: no single file backs the module.
        return _CheckResult(
            name=_PROBE_FORGELM_INSTALL,
            status=_STATUS_PASS,
            detail=f"ForgeLM {version} — package location unresolvable (namespace package?).",
            extras={"version": version, "location": None, "in_site_packages": None},
        )

    location = os.path.dirname(os.path.realpath(forgelm.__file__))
    lib_dirs = [
        os.path.realpath(path)
        for path in (sysconfig.get_paths().get("purelib"), sysconfig.get_paths().get("platlib"))
        if path
    ]
    in_site_packages = any(location == lib or location.startswith(lib + os.sep) for lib in lib_dirs)
    where = "inside site-packages" if in_site_packages else "outside site-packages (source tree or editable install)"

    return _CheckResult(
        name=_PROBE_FORGELM_INSTALL,
        status=_STATUS_PASS,
        detail=f"ForgeLM {version} running from {location} ({where}).",
        extras={
            "version": version,
            "location": location,
            "in_site_packages": in_site_packages,
        },
    )


def _check_python_version() -> _CheckResult:
    """Pin Python to the supported window.

    ForgeLM declares ``python_requires=">=3.10"`` in pyproject.toml.  We
    treat 3.10 as the floor (fail below), 3.11+ as the recommended
    surface (pass above), 3.10.x as warn-with-explanation (still works
    but operators should be tracking 3.11 / 3.12).
    """
    version = sys.version_info
    label = f"{version.major}.{version.minor}.{version.micro}"
    # Compare on the (major, minor) prefix only.  ``sys.version_info`` is
    # a 5-tuple; comparing it directly to ``(3, 10)`` works in CPython
    # today but is harder to reason about (the trailing slots compare
    # ``int`` vs absent).  Wave 2a Round-2 review F-34-VERSIONCMP: pin to
    # a 2-tuple slice so the intent is explicit and future tuple-shape
    # changes upstream cannot break the comparison.
    version_pair = (version.major, version.minor)
    if version_pair < (3, 10):
        return _CheckResult(
            name=_PROBE_PYTHON_VERSION,
            status=_STATUS_FAIL,
            detail=f"Python {label} is below the supported floor (>=3.10). Upgrade Python to >=3.11.",
            extras={"version": label, "minimum": "3.10", "recommended": "3.11"},
        )
    if version_pair < (3, 11):
        return _CheckResult(
            name=_PROBE_PYTHON_VERSION,
            status=_STATUS_WARN,
            detail=f"Python {label} is supported but >=3.11 is recommended for performance + typing improvements.",
            extras={"version": label, "minimum": "3.10", "recommended": "3.11"},
        )
    return _CheckResult(
        name=_PROBE_PYTHON_VERSION,
        status=_STATUS_PASS,
        detail=f"Python {label} ({platform.python_implementation()}).",
        extras={"version": label, "implementation": platform.python_implementation()},
    )


def _check_torch_cuda() -> _CheckResult:
    """Detect torch + CUDA availability.

    CPU-only is a *legitimate* setup (small experiments, CI smoke,
    inference-only deployments) so a missing CUDA does not fail —
    it warns so the operator notices that a GPU run will fail later.
    """
    try:
        import torch
    except ImportError:
        return _CheckResult(
            name=_PROBE_TORCH_INSTALLED,
            status=_STATUS_FAIL,
            detail="torch is not installed. Install with: pip install 'forgelm'.",
            extras={"installed": False},
        )
    cuda_available = bool(torch.cuda.is_available())
    cuda_version = getattr(torch.version, "cuda", None) if cuda_available else None
    if not cuda_available:
        return _CheckResult(
            name=_PROBE_TORCH_CUDA,
            status=_STATUS_WARN,
            detail=(
                f"torch {torch.__version__} installed but CUDA is unavailable. "
                "CPU-only runs are supported but training will be very slow; "
                "expect to use this only for tiny experiments or smoke tests."
            ),
            extras={
                "torch_version": torch.__version__,
                "cuda_available": False,
            },
        )
    return _CheckResult(
        name=_PROBE_TORCH_CUDA,
        status=_STATUS_PASS,
        detail=f"torch {torch.__version__} with CUDA {cuda_version}.",
        extras={
            "torch_version": torch.__version__,
            "cuda_available": True,
            "cuda_version": cuda_version,
        },
    )


def _check_numpy_torch_abi() -> _CheckResult:
    """Detect a torch <-> numpy binary-ABI mismatch.

    Thin doctor-side wrapper over
    :func:`forgelm.cli._abi_check.compute_numpy_torch_abi_status`; the
    same helper is also called as a fail-fast preflight from
    :mod:`forgelm.cli._training` so an operator who skips ``forgelm
    doctor`` and jumps straight to training still gets the actionable
    remediation message rather than a cryptic
    ``NameError: name '_C' is not defined`` from torch mid-run.

    See :mod:`forgelm.cli._abi_check` for the underlying logic + the
    full stderr-suppression caveat (torch's C++ ``_ARRAY_API not
    found`` line is outside Python's warnings machinery).
    """
    status, torch_version, numpy_version = compute_numpy_torch_abi_status()

    if status == ABI_SKIPPED_TORCH:
        # The dedicated torch.cuda probe already surfaces a missing
        # torch as fail; do not double-report here.
        return _CheckResult(
            name=_PROBE_NUMPY_TORCH_ABI,
            status=_STATUS_PASS,
            detail="torch is not installed; ABI compatibility check skipped.",
            extras={"skipped": True, "reason": "torch_missing"},
        )
    if status == ABI_SKIPPED_NUMPY:
        # numpy is an *optional* fast-path for the simhash backend
        # (see forgelm/data_audit/_optional.py::_HAS_NUMPY) — its
        # absence is not a doctor failure.
        return _CheckResult(
            name=_PROBE_NUMPY_TORCH_ABI,
            status=_STATUS_PASS,
            detail="numpy is not installed; ABI compatibility check skipped.",
            extras={"skipped": True, "reason": "numpy_missing"},
        )

    extras: Dict[str, Any] = {
        "torch_version": torch_version,
        "numpy_version": numpy_version,
    }
    if status == ABI_BROKEN:
        # ``format_abi_remediation`` is the canonical fix-instructions
        # string — shared so the training preflight emits the same hint.
        return _CheckResult(
            name=_PROBE_NUMPY_TORCH_ABI,
            status=_STATUS_FAIL,
            detail=format_abi_remediation(torch_version, numpy_version),
            extras=extras,
        )

    if status != ABI_OK:
        # The four ABI_* sentinels are exhaustive today; a fifth would
        # have to land in ``_abi_check.py`` and be added to the dispatch
        # above.  Raise (don't ``assert``) so a future drift still
        # surfaces under ``python -O`` — bare asserts are stripped at
        # optimization level 1.
        raise RuntimeError(f"unexpected ABI status {status!r} from compute_numpy_torch_abi_status()")
    return _CheckResult(
        name=_PROBE_NUMPY_TORCH_ABI,
        status=_STATUS_PASS,
        detail=f"torch {torch_version} + numpy {numpy_version} are ABI-compatible.",
        extras=extras,
    )


def _check_gpu_inventory() -> _CheckResult:
    """Enumerate visible GPUs + per-device VRAM.

    Skipped (returns ``warn``) when CUDA is unavailable — no point
    enumerating zero devices.
    """
    try:
        import torch
    except ImportError:
        return _CheckResult(
            name=_PROBE_GPU_INVENTORY,
            status=_STATUS_FAIL,
            detail="torch is not installed.",
            extras={"installed": False},
        )
    if not torch.cuda.is_available():
        return _CheckResult(
            name=_PROBE_GPU_INVENTORY,
            status=_STATUS_WARN,
            detail="No CUDA devices visible; CPU-only mode.",
            extras={"device_count": 0},
        )
    count = torch.cuda.device_count()
    devices: List[Dict[str, Any]] = []
    for idx in range(count):
        try:
            props = torch.cuda.get_device_properties(idx)
        except (RuntimeError, AssertionError) as exc:  # pragma: no cover — defensive
            devices.append(
                {
                    "index": idx,
                    "error": f"could not query device {idx}: {exc}",
                }
            )
            continue
        # ``total_memory`` is bytes; convert to GiB for the operator-
        # facing label and keep raw bytes in the structured payload.
        vram_gib = round(props.total_memory / (1024**3), 1)
        devices.append(
            {
                "index": idx,
                "name": props.name,
                "vram_gib": vram_gib,
                "vram_bytes": props.total_memory,
            }
        )
    if count == 0:
        return _CheckResult(
            name=_PROBE_GPU_INVENTORY,
            status=_STATUS_WARN,
            detail="CUDA reports no visible devices.",
            extras={"device_count": 0},
        )
    summary = ", ".join(f"GPU{d['index']}: {d.get('name', '?')} ({d.get('vram_gib', '?')} GiB)" for d in devices)
    return _CheckResult(
        name=_PROBE_GPU_INVENTORY,
        status=_STATUS_PASS,
        detail=f"{count} GPU(s) — {summary}.",
        extras={"device_count": count, "devices": devices},
    )


def _check_optional_extra(extra: str, module: str, purpose: str) -> _CheckResult:
    """Probe one optional extra without triggering its heavy import side effects."""
    import importlib.util

    spec = importlib.util.find_spec(module)
    if spec is None:
        return _CheckResult(
            name=f"extras.{extra}",
            status=_STATUS_WARN,
            detail=f"Optional extra missing — install with: pip install 'forgelm[{extra}]' (purpose: {purpose}).",
            extras={"extra": extra, "module": module, "installed": False},
        )
    return _CheckResult(
        name=f"extras.{extra}",
        status=_STATUS_PASS,
        # ``find_spec`` confirms the module is discoverable but does NOT execute
        # it — a present-but-import-broken extra (e.g. bitsandbytes whose CUDA
        # .so fails to load) still resolves a spec.  Word the detail as
        # "discoverable" so the pass does not over-promise importability
        # (F-P7-OPUS-35).
        detail=f"Discoverable, not import-tested (module {module}, purpose: {purpose}).",
        extras={"extra": extra, "module": module, "installed": True},
    )


# Default Hub endpoint; overridable via the standard HF_ENDPOINT env var.
# Mirrors huggingface_hub's own resolution path so an operator with a
# self-hosted mirror gets the right probe target.
_DEFAULT_HF_ENDPOINT = "https://huggingface.co"


def _resolve_hf_endpoint() -> str:
    """Resolve the HuggingFace Hub endpoint via the standard env var.

    huggingface_hub honours ``HF_ENDPOINT`` for self-hosted mirrors and
    enterprise installs (e.g. internal Datasaur / KServe-fronted Hub).
    Hard-coding ``huggingface.co`` would produce a false warning on
    those deployments — the corp proxy might block public huggingface.co
    while the internal mirror is happily serving requests.
    """
    return (os.environ.get("HF_ENDPOINT") or _DEFAULT_HF_ENDPOINT).rstrip("/")


def _check_hf_hub_reachable(timeout_seconds: float = 5.0) -> _CheckResult:
    """HEAD ``${HF_ENDPOINT}/api/models`` to verify the Hub is reachable.

    Skipped by the caller in ``--offline`` mode.  Treats a connection
    failure as a *warning* rather than a fail because a transient outage
    (operator's wifi, captive portal, corp proxy down) shouldn't refuse
    a doctor run that may exist precisely to surface that fact.

    Wave 2a Round-1 review:

    - F-27-04: dropped the ``socket.timeout`` ``except`` branch.
      ``socket.timeout`` is an alias for ``TimeoutError`` since Python
      3.10 (the project floor), and ``urllib.request.urlopen`` wraps a
      timeout as ``URLError`` anyway — the branch was dead.
    - bot (gemini): now resolves the endpoint via ``_resolve_hf_endpoint``
      so ``HF_ENDPOINT=https://internal-mirror.example`` is respected
      (mirrors how ``huggingface_hub`` resolves it).
    - Wave 2a Round-2 (F-XPR-02-01): migrated from raw
      ``urllib.request.urlopen`` to :func:`forgelm._http.safe_get` so
      the probe inherits the project-wide HTTP discipline (SSRF guard,
      scheme policy, timeout floor, secret-mask error path,
      redirect-refusal).  An air-gapped mirror at a private IP requires
      ``allow_private=True`` — that gate is not crossed here because an
      operator with a private HF endpoint should run ``--offline`` (which
      swaps to the cache probe) rather than have doctor punch through to
      a metadata service.
    """
    import requests as _requests  # only for the exception type

    from forgelm._http import HttpSafetyError, safe_get

    endpoint = _resolve_hf_endpoint()
    probe_url = f"{endpoint}/api/models"
    headers = {"User-Agent": "forgelm-doctor/Phase-34"}

    try:
        response = safe_get(
            probe_url,
            headers=headers,
            timeout=timeout_seconds,
            method="HEAD",
        )
        status_code = response.status_code
    except HttpSafetyError as exc:
        # Policy rejection — surface as a fail, not a warn: the operator
        # configured something the discipline blocks (e.g. http:// endpoint
        # or private IP without --offline).
        return _CheckResult(
            name=_PROBE_HF_HUB_REACHABLE,
            status=_STATUS_FAIL,
            detail=(
                f"HuggingFace Hub probe rejected by HTTP discipline: {exc}. "
                "Set HF_ENDPOINT to a public https:// URL, or pass --offline "
                "to inspect the local cache instead."
            ),
            extras={"reachable": False, "endpoint": endpoint, "error": str(exc)},
        )
    except _requests.RequestException as exc:
        # Transport / TLS / network failure — caught and warned (same
        # behaviour as the previous urllib URLError branch).
        return _CheckResult(
            name=_PROBE_HF_HUB_REACHABLE,
            status=_STATUS_WARN,
            detail=f"Could not reach HuggingFace Hub at {probe_url}: {exc}. Check network / proxy / HF_ENDPOINT.",
            extras={"reachable": False, "endpoint": endpoint, "error": str(exc)},
        )

    if 200 <= status_code < 400:
        return _CheckResult(
            name=_PROBE_HF_HUB_REACHABLE,
            status=_STATUS_PASS,
            detail=f"HuggingFace Hub reachable at {endpoint} (HTTP {status_code}).",
            extras={"reachable": True, "endpoint": endpoint, "status_code": status_code},
        )
    if status_code == 405:
        # Some corp proxies block HEAD; retry with GET as a courtesy.
        try:
            response = safe_get(probe_url, headers=headers, timeout=timeout_seconds, method="GET")
            status_code = response.status_code
            if 200 <= status_code < 400:
                return _CheckResult(
                    name=_PROBE_HF_HUB_REACHABLE,
                    status=_STATUS_PASS,
                    detail=f"HuggingFace Hub reachable at {endpoint} (HTTP {status_code}; GET fallback after HEAD 405).",
                    extras={
                        "reachable": True,
                        "endpoint": endpoint,
                        "status_code": status_code,
                        "fallback_method": "GET",
                    },
                )
        except _requests.RequestException:  # pragma: no cover — second-attempt failure
            pass
    return _CheckResult(
        name=_PROBE_HF_HUB_REACHABLE,
        status=_STATUS_WARN,
        detail=f"HuggingFace Hub at {endpoint} returned HTTP {status_code}.",
        extras={"reachable": False, "endpoint": endpoint, "status_code": status_code},
    )


# Cache-walk safety cap.  Real HF caches reach 50+ GiB on long-lived
# workstations; an unbounded walk on NFS-mounted caches takes 30+s.
# Phase 34 doesn't need an exact size — "is there *something* cached?"
# is enough.  We cap depth + file count and report whether the cap fired.
_HF_CACHE_WALK_DEPTH = 4
_HF_CACHE_WALK_FILE_LIMIT = 5_000


def _resolve_hf_cache_dir() -> str:
    """Resolve the HF Hub cache directory honouring the standard env vars.

    Priority (matches huggingface_hub's own resolution):

    1. ``HF_HUB_CACHE`` — newer dedicated env var, wins outright.
    2. ``HF_HOME/hub`` — the *hub* sub-directory (huggingface_hub
       partitions cache by purpose; ``HF_HOME`` sets the *parent*, not
       the hub cache directly).  Wave 2a Round-1 review (gemini bot)
       fix: the original implementation pointed at ``HF_HOME``
       directly which is wrong — that directory contains the hub +
       datasets + spaces sub-trees, not just the hub blobs.
    3. ``~/.cache/huggingface/hub`` — the documented default.
    """
    hf_hub_cache = os.environ.get("HF_HUB_CACHE")
    if hf_hub_cache:
        return hf_hub_cache
    hf_home = os.environ.get("HF_HOME")
    if hf_home:
        return os.path.join(hf_home, "hub")
    return os.path.expanduser("~/.cache/huggingface/hub")


def _accumulate_files_in_dir(
    root: str,
    files: List[str],
    *,
    file_count: int,
    total_bytes: int,
    unreadable_count: int,
) -> Tuple[int, int, int, bool]:
    """Sum regular-file sizes in a single directory under the file cap.

    Returns ``(new_file_count, new_total_bytes, new_unreadable_count,
    cap_hit)``.  ``cap_hit=True`` means the file-count cap fired during
    this directory and the caller should stop descending further.  Pulled
    out of :func:`_walk_hf_cache_bounded` so each function stays under
    SonarCloud's S3776 cognitive-complexity ceiling.
    """
    cap_hit = False
    for filename in files:
        if file_count >= _HF_CACHE_WALK_FILE_LIMIT:
            cap_hit = True
            break
        full = os.path.join(root, filename)
        if os.path.islink(full):
            continue
        try:
            total_bytes += os.path.getsize(full)
            file_count += 1
        except OSError:
            unreadable_count += 1
    return file_count, total_bytes, unreadable_count, cap_hit


def _walk_hf_cache_bounded(cache_dir_abs: str) -> Tuple[int, int, int, int, bool]:
    """Bounded ``os.walk`` over the HF cache.  Returns ``(file_count,
    total_bytes, unreadable_count, walk_errors, walk_truncated)``.

    Wave 2a Round-2 nit: extracted from :func:`_check_hf_cache_offline`
    so the cache-status check function can read top-down (resolve dir →
    walk → render result) without the per-file accounting being inlined.
    Sums regular-file sizes only; symlinks in the HF cache point at the
    blob store and would be double-counted.  Depth + file caps keep the
    walk bounded on NFS-mounted 50 GiB+ caches so the doctor probe stays
    snappy.  ``unreadable_count`` is surfaced so the renderer can warn
    when permissions broke part of the scan (F-34-OSE).  Per-directory
    accounting lives in :func:`_accumulate_files_in_dir` so each helper
    stays under SonarCloud's S3776 cognitive-complexity ceiling.

    Wave 2a Round-5 (F-R5-04): ``os.walk`` defaults to silently
    *swallowing* ``os.scandir`` errors — a chmod-broken cache root or
    sub-directory simply vanishes from the walk, so a fully-unreadable
    cache reports ``file_count=0, unreadable_count=0`` and falls into
    the empty-cache branch.  The ``onerror`` callback now bumps
    ``walk_errors`` so the caller can distinguish "cache is empty" from
    "cache exists but the operator cannot read it" — the latter is a
    *fail*, not a warn.
    """
    file_count = 0
    total_bytes = 0
    unreadable_count = 0
    walk_errors = 0
    walk_truncated = False

    def _on_walk_error(exc: OSError) -> None:
        nonlocal walk_errors
        walk_errors += 1
        # ``OSError.filename`` may legitimately be ``None`` on some
        # platforms (Windows / WSL / FUSE) when the OSError originates
        # from a level that did not associate a filename.  ``getattr(...,
        # "?")`` only catches the missing-attribute case; ``or "?"``
        # additionally collapses ``None`` so the operator-facing log
        # never shows the unhelpful literal ``None``.
        filename = getattr(exc, "filename", None) or "?"
        logger.warning("Doctor HF cache walk hit %s on %s", exc.__class__.__name__, filename)

    base_depth = cache_dir_abs.rstrip(os.sep).count(os.sep)
    for root, dirs, files in os.walk(cache_dir_abs, onerror=_on_walk_error):
        if (root.count(os.sep) - base_depth) > _HF_CACHE_WALK_DEPTH:
            dirs[:] = []
            walk_truncated = True
            continue
        file_count, total_bytes, unreadable_count, cap_hit = _accumulate_files_in_dir(
            root,
            files,
            file_count=file_count,
            total_bytes=total_bytes,
            unreadable_count=unreadable_count,
        )
        if cap_hit:
            walk_truncated = True
            break
    return file_count, total_bytes, unreadable_count, walk_errors, walk_truncated


def _check_hf_cache_offline() -> _CheckResult:
    """Inspect the local HF cache (``--offline`` mode replacement for the Hub probe).

    A populated cache means an air-gapped run can satisfy
    ``local_files_only=True`` without any network access.  An empty cache
    is a warning — the operator may need to run ``forgelm cache-models``
    (Phase 35) before training.

    Wave 2a Round-1 review fixes:

    - bot (gemini): cache directory resolution now honours
      ``HF_HUB_CACHE`` and the ``hub`` sub-directory of ``HF_HOME``
      (was: pointed at ``HF_HOME`` itself, which is the parent of
      multiple cache trees).
    - F-27-03: walk is depth-capped + file-capped so an NFS-mounted
      50+ GiB cache doesn't take 30+s; cap-hit is recorded in extras
      so downstream tooling can detect partial-scan results.
    """
    cache_dir = _resolve_hf_cache_dir()
    if not os.path.isdir(cache_dir):
        return _CheckResult(
            name=_PROBE_HF_HUB_OFFLINE_CACHE,
            status=_STATUS_WARN,
            detail=(
                f"HF cache not found at {cache_dir}. Air-gapped runs need pre-cached models — "
                "see `forgelm cache-models` (Phase 35) once available, or set HF_HUB_CACHE / "
                "HF_HOME (with /hub subdirectory) and pre-populate."
            ),
            extras={"cache_dir": cache_dir, "exists": False},
        )
    cache_dir_abs = os.path.abspath(cache_dir)
    file_count, total_bytes, unreadable_count, walk_errors, walk_truncated = _walk_hf_cache_bounded(cache_dir_abs)
    cache_gib = round(total_bytes / (1024**3), 2)
    offline_env = os.environ.get("HF_HUB_OFFLINE")
    truncation_note = " (scan truncated at depth/file cap)" if walk_truncated else ""
    unreadable_note = f" [{unreadable_count} file(s) unreadable, totals are partial]" if unreadable_count else ""
    walk_error_note = f" [{walk_errors} directory walk error(s) — check chmod / mount]" if walk_errors else ""
    detail = (
        f"HF cache at {cache_dir}: {cache_gib} GiB across {file_count} file(s)"
        f"{truncation_note}{unreadable_note}{walk_error_note}. "
        f"HF_HUB_OFFLINE={offline_env or 'unset'}."
    )
    # Status policy:
    #   fail — the operator cannot READ the cache (walk_errors > 0 AND
    #     no files visible).  Air-gapped run will fail later with a
    #     misleading missing-model error if we let this masquerade as
    #     "empty cache". (F-R5-04)
    #   warn — files visible but partial (walk_errors > 0 but some
    #     files were sized OK), or fully readable but empty cache, or
    #     individual files unreadable.  All operator-actionable but
    #     do not block the doctor verdict.
    #   pass — every file visible + sized.
    if walk_errors and file_count == 0:
        status = _STATUS_FAIL
    elif file_count == 0:
        status = _STATUS_WARN
    elif unreadable_count or walk_errors:
        status = _STATUS_WARN
    else:
        status = _STATUS_PASS
    return _CheckResult(
        name=_PROBE_HF_HUB_OFFLINE_CACHE,
        status=status,
        detail=detail,
        extras={
            "cache_dir": cache_dir,
            "exists": True,
            "size_gib": cache_gib,
            "file_count": file_count,
            "unreadable_count": unreadable_count,
            "walk_errors": walk_errors,
            "walk_truncated": walk_truncated,
            "hf_hub_offline_env": offline_env,
        },
    )


def _check_disk_space(path: str = ".") -> _CheckResult:
    """Workspace free-space probe.

    Disk space is a frequent source of late-failing training runs (a
    fine-tune that crashes mid-checkpoint because the workspace is
    full).  ``shutil.disk_usage`` works cross-platform and is in the
    stdlib so this probe runs without any optional dep.
    """
    try:
        usage = shutil.disk_usage(path)
    except OSError as exc:  # pragma: no cover — defensive (permission denied)
        return _CheckResult(
            name=_PROBE_DISK_WORKSPACE,
            status=_STATUS_FAIL,
            detail=f"Could not query disk usage at {path!r}: {exc}.",
            extras={"path": os.path.abspath(path), "error": str(exc)},
        )
    free_gib = round(usage.free / (1024**3), 1)
    total_gib = round(usage.total / (1024**3), 1)
    if free_gib < _DISK_FAIL_GB:
        status = _STATUS_FAIL
    elif free_gib < _DISK_WARN_GB:
        status = _STATUS_WARN
    else:
        status = _STATUS_PASS
    return _CheckResult(
        name=_PROBE_DISK_WORKSPACE,
        status=status,
        detail=f"Workspace {os.path.abspath(path)} — {free_gib} GiB free of {total_gib} GiB.",
        extras={
            "path": os.path.abspath(path),
            "free_gib": free_gib,
            "total_gib": total_gib,
            "fail_threshold_gib": _DISK_FAIL_GB,
            "warn_threshold_gib": _DISK_WARN_GB,
        },
    )


# Env-var names that may carry secret material — never echo their *values*
# in the doctor output even when the operator runs --output-format json
# and pipes to a CI log.  Pattern matches AuditLogger's own discipline.
_DOCTOR_SECRET_ENV_NAMES: frozenset[str] = frozenset(
    {
        "FORGELM_AUDIT_SECRET",  # HMAC key for audit-log signing
        "HF_TOKEN",  # HuggingFace Hub auth token
        "HUGGING_FACE_HUB_TOKEN",  # legacy alias of HF_TOKEN
        "HUGGINGFACE_TOKEN",  # alternative spelling read by forgelm/utils.py
        "FORGELM_RESUME_TOKEN",  # reserved name — masked defensively even if unused today
        # Defence-in-depth: third-party API keys ForgeLM accepts via YAML
        # interpolation (auth.openai_api_key etc.).  No probe surfaces them
        # today, but pre-listing them means a future probe that adds env
        # visibility (e.g. an "auth provenance" probe) cannot accidentally
        # leak them in --output-format json.
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "WANDB_API_KEY",
        "COHERE_API_KEY",
    }
)


def _mask_env_value_for_audit(name: str, value: str) -> str:
    """Mask values for env vars whose name implies secret material.

    Wave 2a Round-1 review (F-27-05): probes echo env-var values
    verbatim into ``detail`` + ``extras`` today, which would leak any
    future probe surfacing ``HF_TOKEN`` or ``FORGELM_AUDIT_SECRET``.
    Centralised mask so the policy lives in one place.  ``FORGELM_OPERATOR``
    is *not* in the mask list — it is operator identity, not a secret —
    but the discipline for adding new probes is "if the env-var name
    contains TOKEN / SECRET / KEY / PASSWORD, add it to
    _DOCTOR_SECRET_ENV_NAMES first, then surface it".
    """
    if name in _DOCTOR_SECRET_ENV_NAMES:
        # Show length only so the operator can confirm "yes, set",
        # without exposing the value.
        return f"<set, {len(value)} chars>"
    return value


def _check_operator_identity() -> _CheckResult:
    """Article 12 record-keeping reminder.

    ``FORGELM_OPERATOR`` set explicitly is the recommended path for
    CI / pipelines (so audit events carry a meaningful identity).
    Unset is not a failure — the AuditLogger falls back to
    ``getuser()@host`` which is fine on a developer workstation —
    but warn so a CI deployer is reminded to pin it.

    Wave 2a Round-1 review (qodo bot): the unresolved-username branch
    now respects the ``FORGELM_ALLOW_ANONYMOUS_OPERATOR=1`` opt-in the
    same way ``AuditLogger.__init__`` does.  When that env var is set,
    the doctor returns ``warn`` (anonymous identity OK by operator
    choice) instead of ``fail``; when it is not set, ``fail`` stands
    because ``AuditLogger`` itself would refuse to start.
    """
    explicit = os.environ.get("FORGELM_OPERATOR")
    if explicit:
        masked = _mask_env_value_for_audit("FORGELM_OPERATOR", explicit)
        return _CheckResult(
            name=_PROBE_OPERATOR_IDENTITY,
            status=_STATUS_PASS,
            detail=f"FORGELM_OPERATOR set to {masked!r}; audit events will carry this identity.",
            extras={"FORGELM_OPERATOR": masked, "source": "env"},
        )
    # Try to resolve the fallback identity AuditLogger would use so
    # the operator sees what their audit events would look like.
    try:
        import getpass

        username = getpass.getuser()
    except (KeyError, OSError, ImportError):
        username = None
    hostname = socket.gethostname() or "unknown-host"
    if username:
        fallback = f"{username}@{hostname}"
        return _CheckResult(
            name=_PROBE_OPERATOR_IDENTITY,
            status=_STATUS_WARN,
            detail=(
                f"FORGELM_OPERATOR not set; audit events will fall back to {fallback!r}. "
                "Pin FORGELM_OPERATOR=<id> for CI / pipeline runs so the audit log identifies a stable identity."
            ),
            extras={"FORGELM_OPERATOR": None, "fallback": fallback, "source": "getpass"},
        )
    # No username AND no FORGELM_OPERATOR: AuditLogger refuses to start
    # unless the operator explicitly opted in to anonymous audit
    # entries.  Mirror that opt-in here so doctor's verdict matches the
    # actual training-time behaviour (warn-OK-but-suboptimal vs hard-fail).
    allow_anonymous = os.environ.get("FORGELM_ALLOW_ANONYMOUS_OPERATOR") == "1"
    if allow_anonymous:
        anon = f"anonymous@{hostname}"
        return _CheckResult(
            name=_PROBE_OPERATOR_IDENTITY,
            status=_STATUS_WARN,
            detail=(
                f"FORGELM_OPERATOR not set, getpass.getuser() unavailable, but "
                f"FORGELM_ALLOW_ANONYMOUS_OPERATOR=1 is set; audit events will record {anon!r}. "
                "Not recommended for EU AI Act Article 12 record-keeping; pin FORGELM_OPERATOR=<id> when possible."
            ),
            extras={
                "FORGELM_OPERATOR": None,
                "fallback": anon,
                "source": "anonymous_opt_in",
            },
        )
    return _CheckResult(
        name=_PROBE_OPERATOR_IDENTITY,
        status=_STATUS_FAIL,
        detail=(
            "FORGELM_OPERATOR not set AND getpass.getuser() could not resolve a username. "
            "Set FORGELM_OPERATOR=<id> (or FORGELM_ALLOW_ANONYMOUS_OPERATOR=1 to opt in to "
            "anonymous audit entries — not recommended for EU AI Act Article 12)."
        ),
        extras={"FORGELM_OPERATOR": None, "fallback": None},
    )


# ---------------------------------------------------------------------------
# Phase 15 Task 3 — pypdf font-fallback normalisation probe
# ---------------------------------------------------------------------------


def _check_pypdf_normalise_turkish() -> _CheckResult:
    """Verify the Turkish glyph-normalisation profile loads + applies cleanly.

    Phase 15 Task 3 acceptance criterion: ``forgelm doctor`` must report
    a ``pypdf_normalise.turkish: pass`` row so an operator can confirm
    the table loaded correctly without running a test ingest. The probe
    runs through one round-trip sample (corrupt glyphs → normalised
    output) and fails loudly if the table is missing or the dispatcher
    no-ops on the active profile.
    """
    try:
        from ..._pypdf_normalise import (
            DEFAULT_PROFILE,
            apply_profile,
            profile_summary,
        )
    except ImportError as exc:
        return _CheckResult(
            name=_PROBE_PYPDF_NORMALISE_TURKISH,
            status=_STATUS_FAIL,
            detail=(
                "forgelm._pypdf_normalise import failed — Phase 15 normalisation "
                f"profile is unreachable: {exc.__class__.__name__}: {exc}."
            ),
            extras={"profile": "turkish", "loaded": False},
        )
    summary = profile_summary("turkish")
    sample_input = "ø Õ ú ÷ ࡟"
    sample_output = apply_profile(sample_input, "turkish")
    if sample_output == sample_input:
        return _CheckResult(
            name=_PROBE_PYPDF_NORMALISE_TURKISH,
            status=_STATUS_FAIL,
            detail=(
                "Turkish glyph-normalisation profile loaded but produced no substitutions "
                "on the canonical fixture (`ø Õ ú ÷ ࡟`). The table may have been emptied "
                "or the dispatcher is no-opping; re-install forgelm or file a bug."
            ),
            extras={"profile": "turkish", "expected_substitutions": True, "actual_output": sample_output},
        )
    return _CheckResult(
        name=_PROBE_PYPDF_NORMALISE_TURKISH,
        status=_STATUS_PASS,
        detail=(
            f"Turkish glyph profile loaded: {summary['single']} single-char + "
            f"{summary['multi']} multi-char substitutions. Active profile: {DEFAULT_PROFILE!r}."
        ),
        extras={
            "profile": "turkish",
            "single_substitutions": summary["single"],
            "multi_substitutions": summary["multi"],
            "default_profile": DEFAULT_PROFILE,
        },
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


# Order matters for the text rendering — keep it grouped (env first,
# hardware next, optional extras, then network, then operator identity).
def _build_check_plan(*, offline: bool) -> List[Tuple[str, Callable[[], _CheckResult]]]:
    """Return the ordered list of ``(label, callable)`` probe entries.

    The label is only used in error reporting if a probe itself crashes;
    each ``_CheckResult`` carries its own ``name`` for the renderer.
    """
    plan: List[Tuple[str, Callable[[], _CheckResult]]] = [
        # First row deliberately: every other line of a bug report is
        # ambiguous until the reader knows which copy of ForgeLM ran.
        (_PROBE_FORGELM_INSTALL, _check_forgelm_install),
        (_PROBE_PYTHON_VERSION, _check_python_version),
        (_PROBE_TORCH_CUDA, _check_torch_cuda),
        (_PROBE_NUMPY_TORCH_ABI, _check_numpy_torch_abi),
        (_PROBE_GPU_INVENTORY, _check_gpu_inventory),
    ]
    for extra_name, module, purpose in _OPTIONAL_EXTRAS:
        plan.append((f"extras.{extra_name}", _make_extra_probe(extra_name, module, purpose)))
    # Phase 15 Task 3: pypdf font-fallback normalisation probe sits next to
    # the [ingestion] extras probe so operators can correlate "the extra is
    # missing" with "...so the profile cannot apply" in a single glance.
    plan.append((_PROBE_PYPDF_NORMALISE_TURKISH, _check_pypdf_normalise_turkish))
    if offline:
        plan.append((_PROBE_HF_HUB_OFFLINE_CACHE, _check_hf_cache_offline))
    else:
        plan.append((_PROBE_HF_HUB_REACHABLE, _check_hf_hub_reachable))
    plan.append((_PROBE_DISK_WORKSPACE, _check_disk_space))
    plan.append((_PROBE_OPERATOR_IDENTITY, _check_operator_identity))
    return plan


def _make_extra_probe(extra: str, module: str, purpose: str) -> Callable[[], _CheckResult]:
    """Build a zero-arg closure that probes one optional extra.

    Defined as a top-level helper rather than a lambda so the closure
    binding is explicit and the probe is easy to unit-test by name.
    """

    def _probe() -> _CheckResult:
        return _check_optional_extra(extra, module, purpose)

    return _probe


def _run_all_checks(*, offline: bool) -> List[_CheckResult]:
    """Execute every probe in order, catching crashes per-probe.

    A probe that raises an unexpected exception is converted into a
    ``fail`` result so the rest of the report still renders — partial
    diagnostics are more useful than zero diagnostics when something is
    broken.
    """
    results: List[_CheckResult] = []
    for label, probe in _build_check_plan(offline=offline):
        try:
            results.append(probe())
        except Exception as exc:  # noqa: BLE001 — best-effort: per-probe catch keeps the rest of the report rendering when one diagnostic crashes; converting to fail lets the operator see the broken probe name + traceback class instead of silently dropping it.
            logger.exception("Doctor probe %r raised: %s", label, exc)
            results.append(
                _CheckResult(
                    name=label,
                    status=_STATUS_FAIL,
                    detail=f"Probe crashed: {exc.__class__.__name__}: {exc}",
                    extras={"crashed": True, "error_class": exc.__class__.__name__},
                )
            )
    return results


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


# Wave 2a Round-2 review F-34-ASCII: the renderer docstring promises
# "Plain ASCII" output for redirected logs / non-UTF8 terminals, but the
# original glyphs (✓ U+2713, ✗ U+2717) are Unicode and would raise
# UnicodeEncodeError on a strict ASCII locale (PYTHONIOENCODING=ascii or
# a CI runner with C.US-ASCII).  Replaced with single-byte ASCII so the
# output matches the documented contract.  ``+`` / ``!`` / ``x`` keep
# the visual asymmetry (`x` for fail reads as "no" at a glance).
_STATUS_GLYPHS: Dict[str, str] = {
    _STATUS_PASS: "+",
    _STATUS_WARN: "!",
    _STATUS_FAIL: "x",
}


def _render_text(results: List[_CheckResult]) -> str:
    """Render a tabular text report.

    Plain ASCII so the output stays readable in CI logs, redirected
    files, and SSH sessions without colour support.  One line per check
    plus a summary footer.
    """
    pass_count = sum(1 for r in results if r.status == _STATUS_PASS)
    warn_count = sum(1 for r in results if r.status == _STATUS_WARN)
    fail_count = sum(1 for r in results if r.status == _STATUS_FAIL)

    # F-34-ASCII: header was "forgelm doctor — environment check" with an
    # em-dash (U+2014).  Replaced with a plain ASCII hyphen so the
    # docstring's "plain ASCII" promise holds for redirected logs / strict
    # ASCII locales.
    lines: List[str] = ["forgelm doctor - environment check", ""]
    name_width = max(len(r.name) for r in results) if results else 0
    for result in results:
        glyph = _STATUS_GLYPHS.get(result.status, "?")
        lines.append(f"  [{glyph} {result.status}] {result.name:<{name_width}}  {result.detail}")
    lines.append("")
    lines.append(f"Summary: {pass_count} pass, {warn_count} warn, {fail_count} fail.")
    return "\n".join(lines)


def _render_json(results: List[_CheckResult]) -> str:
    """Render the JSON envelope.

    Shape mirrors other ForgeLM subcommand contracts:
    ``{"success": bool, "checks": [...], "summary": {"pass": N, "warn": N, "fail": N, "crashed": N}}``.
    ``success`` is True iff no check failed AND no probe crashed (warns are
    operator-actionable but do not flip the contract; crashes count as
    failures *and* surface separately so consumers can distinguish a
    misconfigured environment from a doctor bug).  Locked schema lives in
    ``docs/usermanuals/en/reference/json-output.md``.
    """
    payload_checks = [
        {
            "name": r.name,
            "status": r.status,
            "detail": r.detail,
            "extras": r.extras,
        }
        for r in results
    ]
    crashed_count = sum(1 for r in results if r.extras.get("crashed") is True)
    summary = {
        "pass": sum(1 for r in results if r.status == _STATUS_PASS),
        "warn": sum(1 for r in results if r.status == _STATUS_WARN),
        # `fail` includes the crashed count (a crashed probe surfaces as
        # status="fail" + extras.crashed=True, see _run_all_checks).
        "fail": sum(1 for r in results if r.status == _STATUS_FAIL),
        "crashed": crashed_count,
    }
    envelope = {
        "success": summary["fail"] == 0,
        "checks": payload_checks,
        "summary": summary,
    }
    # ``default=str`` (Wave 2a Round-5 F-R5-06): ``extras`` is typed
    # ``Dict[str, Any]``; today every probe surfaces only JSON-native
    # types but a future probe author dropping a ``Path`` / ``datetime``
    # / ``bytes`` value would otherwise crash with ``TypeError`` and
    # exit with a Python traceback to stderr instead of the documented
    # JSON envelope.  ``default=str`` defangs the contract violation by
    # falling back to the value's ``__str__`` repr.
    # ``ensure_ascii=False`` (PR #29 F-34-01): a Turkish operator name,
    # Unicode cache path, or localized error message in ``detail`` /
    # ``extras`` would otherwise render as ``\uXXXX`` escape sequences,
    # making the JSON unreadable for operators and downstream parsers.
    return json.dumps(envelope, indent=2, default=str, ensure_ascii=False)


def _resolve_exit_code(results: List[_CheckResult]) -> int:
    """Map the result list to one of the public exit codes.

    Contract:
    - 0 (EXIT_SUCCESS) — every check passed (warn included; warns are
      operator-actionable but do not block)
    - 1 (EXIT_CONFIG_ERROR) — at least one check returned ``fail`` (a
      misconfiguration the operator can correct)
    - 2 (EXIT_TRAINING_ERROR) — a probe itself crashed (an unexpected
      doctor bug, surfaced as runtime-error class so CI/CD retry logic
      treats it the same way as other runtime failures)
    """
    crashed = any(r.extras.get("crashed") for r in results)
    if crashed:
        return EXIT_TRAINING_ERROR
    has_fail = any(r.status == _STATUS_FAIL for r in results)
    return EXIT_CONFIG_ERROR if has_fail else EXIT_SUCCESS


def _run_doctor_cmd(args, output_format: str) -> None:
    """Top-level dispatcher for ``forgelm doctor``.

    Reads ``args.offline`` (argparse default ``False``) and emits either
    the text report or the JSON envelope.  Exits with the public
    contract code resolved by :func:`_resolve_exit_code`.

    Also honours the standard HuggingFace airgap environment variables —
    ``HF_HUB_OFFLINE=1``, ``TRANSFORMERS_OFFLINE=1``, or
    ``HF_DATASETS_OFFLINE=1`` — so an air-gapped operator who already has
    any of them set in their shell does not need to remember to also
    pass ``--offline``.  ``HF_DATASETS_OFFLINE`` mirrors what
    ``forgelm/cli/_config_load.py::_apply_offline_flag`` already sets at
    training time (PR #29 F-XPR-01); without it doctor would probe the
    network on a host that the training pipeline correctly treats as
    offline.  An operator who explicitly passes ``--offline`` always
    gets offline behaviour regardless of env.
    """
    offline = bool(getattr(args, "offline", False))
    if not offline:
        # Standard HF airgap signals — empty string and "0" do NOT
        # activate offline mode (the documented HF behaviour).
        for env_name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE"):
            value = os.environ.get(env_name)
            if value and value not in ("0", "false", "False"):
                offline = True
                break
    results = _run_all_checks(offline=offline)
    if output_format == "json":
        print(_render_json(results))
    else:
        print(_render_text(results))
    sys.exit(_resolve_exit_code(results))


# Re-exports for tests / monkeypatch — kept stable.
__all__ = [
    "_CheckResult",
    "_check_forgelm_install",
    "_check_python_version",
    "_check_torch_cuda",
    "_check_numpy_torch_abi",
    "_check_gpu_inventory",
    "_check_optional_extra",
    "_check_hf_hub_reachable",
    "_check_hf_cache_offline",
    "_check_disk_space",
    "_check_operator_identity",
    "_build_check_plan",
    "_run_all_checks",
    "_render_text",
    "_render_json",
    "_resolve_exit_code",
    "_run_doctor_cmd",
    "_OPTIONAL_EXTRAS",
]
