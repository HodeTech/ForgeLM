"""``forgelm safety-eval`` — standalone safety evaluation subcommand.

Phase 36 closure of GH-005.  Wraps the existing
:func:`forgelm.safety.run_safety_evaluation` library function so an
operator can evaluate a fine-tuned model's safety profile without
having to wire it through a full training-config YAML.

Use cases:

- A pre-deployment safety check on a third-party model the operator
  is about to deploy.
- A post-incident re-evaluation of a previously-shipped model when
  the harm-classifier has been updated.
- A scheduled regression check in a release pipeline (the trainer
  pre-flight already covers training-time evaluation; this is the
  release-time counterpart).

Exit codes:

- 0 — evaluation completed; safety thresholds passed.
- 1 — config error reached by the dispatcher (GGUF model path, probes
  file missing/unreadable).
- 2 — argparse usage error (missing ``--model`` or conflicting
  ``--probes``/``--default-probes`` — both are argparse-required, so
  they are rejected with exit 2 *before* the dispatcher runs) OR a
  runtime error (model load failure, classifier load failure, missing
  optional dep).  The in-dispatcher missing-model / conflicting-probes
  guards remain as defence-in-depth for direct library callers.
- 3 — evaluation completed but safety thresholds exceeded
  (operator-actionable; the model failed the gate).  Maps to
  ``EXIT_EVAL_FAILURE`` so a regulated CI pipeline can branch on
  "evaluation failed" vs "config rejected the run before evaluation".
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, NoReturn

from .._exit_codes import EXIT_CONFIG_ERROR, EXIT_EVAL_FAILURE, EXIT_SUCCESS, EXIT_TRAINING_ERROR
from .._logging import logger

# Path to the bundled default-probes file; resolved relative to this
# module so the file ships inside the wheel.
_DEFAULT_PROBES_RELPATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "safety_prompts",
    "default_probes.jsonl",
)


def _output_error_and_exit(output_format: str, msg: str, exit_code: int) -> NoReturn:
    if output_format == "json":
        print(json.dumps({"success": False, "error": msg}))
    else:
        logger.error(msg)
    sys.exit(exit_code)


def _resolve_probes_path(args, output_format: str) -> str:
    """Pick the probes file: explicit ``--probes`` > ``--default-probes`` flag."""
    explicit = getattr(args, "probes", None)
    use_default = getattr(args, "default_probes", False)
    if explicit and use_default:
        _output_error_and_exit(
            output_format,
            "--probes and --default-probes are mutually exclusive.",
            EXIT_CONFIG_ERROR,
        )
    if explicit:
        if not os.path.isfile(explicit):
            _output_error_and_exit(
                output_format,
                f"Probes file not found: {explicit!r}.",
                EXIT_CONFIG_ERROR,
            )
        return explicit
    if use_default:
        if not os.path.isfile(_DEFAULT_PROBES_RELPATH):
            _output_error_and_exit(
                output_format,
                f"Bundled default probes file is missing: {_DEFAULT_PROBES_RELPATH!r}.  "
                "Reinstall the package or supply --probes <path>.",
                EXIT_TRAINING_ERROR,
            )
        return _DEFAULT_PROBES_RELPATH
    _output_error_and_exit(
        output_format,
        "One of --probes <jsonl> or --default-probes is required.",
        EXIT_CONFIG_ERROR,
    )


def _load_model_for_safety(model_path: str, output_format: str):
    """Load the model + tokenizer for evaluation.

    Routes through the existing :mod:`forgelm.model` loader for
    ``.safetensors`` / HF Hub paths and through :mod:`forgelm.inference`
    for ``.gguf`` (the standalone subcommand mirrors what training-time
    safety evaluation does).
    """
    # GGUF safety-eval is not yet wired.  ``forgelm.inference`` exposes
    # :func:`load_model` / :func:`generate` for HF + Unsloth backends but
    # has no ``load_gguf_model`` entry point — historically the GGUF
    # loading path was llama-cpp-python's own ``Llama(...)`` constructor
    # (see ``forgelm/inference.py``).  Wave 2b Round-2 review caught this
    # as a phantom branch (the late ``from forgelm.inference import
    # load_gguf_model`` would always raise ImportError, and the outer
    # message misleadingly suggested an extras install would fix it).
    # Until Phase 36+ adds a real GGUF safety-eval path, refuse the
    # request explicitly so an operator who passed `*.gguf` sees an
    # honest "not supported" rather than a confusing install-hint.
    if model_path.endswith(".gguf"):
        _output_error_and_exit(
            output_format,
            "safety-eval does not yet support GGUF model loading.  Convert the GGUF "
            "back to a HuggingFace checkpoint (or run safety-eval against the pre-export "
            "HF model) and retry.  Tracking issue: GGUF safety-eval support is planned for "
            "the Phase 36+ extension that lands a `forgelm.inference.load_gguf_model` shim.",
            EXIT_CONFIG_ERROR,
        )

    # Default path: HF / local-checkpoint loader.  Delegate to the
    # canonical inference primitive ``forgelm.inference.load_model``
    # rather than calling ``AutoTokenizer.from_pretrained`` /
    # ``AutoModelForCausalLM.from_pretrained`` directly:
    #
    # - ``architecture.md`` Principle 1 — CLI subcommands are thin
    #   shims; model-loading logic belongs to a core primitive.
    # - ``inference.load_model`` already enforces
    #   ``trust_remote_code=False`` as the default, mirrors the
    #   ``forgelm chat`` / ``forgelm export`` loaders, and centralises
    #   ``device_map="auto"`` placement + ``eval()`` mode setup.
    # - Future Phase 36+ work that wires ``inference.load_gguf_model``
    #   for the GGUF safety-eval path will land in the same primitive,
    #   so this dispatcher gets the upgrade for free.
    #
    # Defence-in-depth (Faz 7 §13 acceptance) is satisfied by the
    # explicit ``trust_remote_code=False`` kwarg below — never trust
    # the primitive's default, gate it at every call site.
    #
    # Security boundary: ``trust_remote_code=False`` on the HF loader is
    # the active defence here, not the path string.  Operator-controlled
    # paths still cannot execute attacker-supplied Python (which is what
    # ``trust_remote_code=True`` enables).  HF rejects directories
    # without ``config.json``, so non-existent or malformed paths fail
    # at load time with a clear error — explicit path validation here
    # would be defense-in-depth that the security model does not need.
    try:
        from forgelm.inference import load_model

        return load_model(model_path, trust_remote_code=False)
    except ImportError as exc:
        # F-36-02: transformers is a *core* ForgeLM dependency, not an
        # optional extra.  Its ImportError is "your environment is
        # broken", which maps to EXIT_TRAINING_ERROR (runtime-class)
        # rather than EXIT_CONFIG_ERROR (operator's YAML is wrong).
        # Regulated CI pipelines that retry on code 2 and fix-and-fail
        # on code 1 then route the broken-env case to the right branch.
        _output_error_and_exit(
            output_format,
            (
                f"transformers (a core ForgeLM dependency) failed to import: {exc}.  "
                "This usually means the active virtualenv / container is missing the "
                "package or has a broken install.  Verify your environment "
                "(`python -c 'import transformers; print(transformers.__version__)'`) "
                "and reinstall with `pip install transformers` or "
                "`pip install --force-reinstall forgelm`."
            ),
            EXIT_TRAINING_ERROR,
        )
    except Exception as exc:  # noqa: BLE001 — broad surface from HF loaders (FileNotFoundError, OSError, ValueError, KeyError, etc.). # NOSONAR
        _output_error_and_exit(
            output_format,
            f"Failed to load model {model_path!r}: {exc}",
            EXIT_TRAINING_ERROR,
        )


def _run_safety_eval_cmd(args, output_format: str) -> None:
    """Top-level dispatcher for ``forgelm safety-eval``."""
    model_path = getattr(args, "model", None)
    if not model_path:
        _output_error_and_exit(
            output_format,
            "safety-eval requires --model <path-or-hub-id>.",
            EXIT_CONFIG_ERROR,
        )

    # The default guard ``meta-llama/Llama-Guard-3-8B`` is a generative
    # Llama-Guard checkpoint; run_safety_evaluation's default classifier_mode
    # ("auto") scores it via generation-based Llama-Guard scoring, so the
    # standalone default now works out of the box (a custom --classifier with a
    # trained safe/unsafe head is still scored through the text-classification
    # pipeline under auto-mode).
    classifier_path = getattr(args, "classifier", None) or "meta-llama/Llama-Guard-3-8B"
    probes_path = _resolve_probes_path(args, output_format)
    output_dir = getattr(args, "output_dir", None) or os.getcwd()
    max_new_tokens = int(getattr(args, "max_new_tokens", 512) or 512)

    try:
        from forgelm.safety import SafetyEvalThresholds, run_safety_evaluation
    except ImportError as exc:
        _output_error_and_exit(
            output_format,
            f"forgelm.safety unavailable; this should never happen on a healthy install.  ImportError: {exc}",
            EXIT_TRAINING_ERROR,
        )

    # Wire an AuditLogger so the documented Article-15
    # ``audit.classifier_load_failed`` record actually fires on this surface
    # (F-P3-FABLE-12): without it run_safety_evaluation's emission guard
    # (`if audit_logger is not None`) always skipped and no audit log was ever
    # written to --output-dir.
    audit_logger = _build_audit_logger(output_dir)

    # Enable category tracking on the standalone path so the documented
    # per-category breakdown the parser/docs advertise is actually reachable
    # (it was always {} because thresholds defaulted to track_categories=False
    # — F-P3-FABLE-13).
    thresholds = SafetyEvalThresholds(track_categories=True)

    model, tokenizer = _load_model_for_safety(model_path, output_format)

    try:
        result = run_safety_evaluation(
            model=model,
            tokenizer=tokenizer,
            classifier_path=classifier_path,
            test_prompts_path=probes_path,
            max_new_tokens=max_new_tokens,
            output_dir=output_dir,
            thresholds=thresholds,
            audit_logger=audit_logger,
        )
    except Exception as exc:  # noqa: BLE001 — broad surface: classifier load failure, OOM, generation crash all funnel into one operator-facing failure path. # NOSONAR
        _output_error_and_exit(
            output_format,
            f"safety-eval crashed during evaluation: {exc.__class__.__name__}: {exc}",
            EXIT_TRAINING_ERROR,
        )

    payload = _build_safety_eval_payload(
        result,
        model_path=model_path,
        classifier_path=classifier_path,
        probes_path=probes_path,
        output_dir=output_dir,
    )
    _emit_safety_result(payload, output_format)
    # F-P3-FABLE-12: a classifier that never loaded (or a probes file with no
    # usable prompts) is *not* a threshold failure — it's a runtime/config
    # problem.  run_safety_evaluation flags those returns with
    # ``evaluation_completed=False``; route them to EXIT_TRAINING_ERROR (2) so
    # a regulated CI pipeline that retries on 2 (broken env) and blocks-deploy
    # on 3 (gate said no) branches correctly rather than treating a transient
    # HF-Hub outage as "the model failed the safety gate".
    if not getattr(result, "evaluation_completed", True):
        sys.exit(EXIT_TRAINING_ERROR)
    # Wave 2b Round-5 review F-W2B-SAFETY: a model that completes
    # evaluation but fails the safety gate is an *evaluation failure*
    # (operator-actionable: retrain or re-classify), not a config
    # error.  Map the non-passing branch to EXIT_EVAL_FAILURE so
    # regulated CI pipelines can distinguish "the gate said no" from
    # "the run never started because the config was rejected".
    sys.exit(EXIT_SUCCESS if payload["passed"] else EXIT_EVAL_FAILURE)


def _build_audit_logger(output_dir: str) -> Any:
    """Construct an AuditLogger for the standalone safety-eval surface.

    Returns ``None`` (degrading to no audit emission) only if AuditLogger
    construction itself fails — e.g. the operator-identity policy refuses an
    anonymous run.  A missing audit log must not abort the safety evaluation
    itself, but the failure is surfaced loudly rather than swallowed silently.
    """
    try:
        from forgelm.compliance import AuditLogger

        return AuditLogger(output_dir)
    except Exception as exc:  # noqa: BLE001 — best-effort: audit logging is a secondary record-keeping side effect; the safety evaluation is the primary operation and must still run. AuditLogger construction crosses the operator-identity policy (raises RuntimeError) and filesystem surface (OSError).
        logger.warning("safety-eval: audit logging disabled (AuditLogger init failed): %s", exc)
        return None


def _build_safety_eval_payload(
    result: Any,
    *,
    model_path: str,
    classifier_path: str,
    probes_path: str,
    output_dir: str,
) -> Dict[str, Any]:
    """Project a :class:`forgelm.safety.SafetyResult` onto the JSON envelope shape.

    Wave 2b Round-2 nit: extracted from :func:`_run_safety_eval_cmd`
    so the dispatcher reads top-down and the renderer (also extracted)
    is unit-testable in isolation.

    ``forgelm.safety.SafetyResult`` exposes the per-category breakdown
    under ``category_distribution`` (an ``Optional[Dict[str, int]]``
    that is None when the operator did not opt into
    ``track_categories``).  The earlier ``harm_categories`` field name
    was wrong (Wave 2b Round-1 fix); the helper preserves the corrected
    accessor.
    """
    return {
        "model": model_path,
        "classifier": classifier_path,
        "probes": probes_path,
        "output_dir": output_dir,
        "passed": getattr(result, "passed", False),
        "safety_score": getattr(result, "safety_score", None),
        "safe_ratio": getattr(result, "safe_ratio", None),
        "category_distribution": dict(getattr(result, "category_distribution", None) or {}),
        "failure_reason": getattr(result, "failure_reason", None),
    }


def _emit_safety_result(payload: Dict[str, Any], output_format: str) -> None:
    """Render the safety-eval payload as JSON envelope or human text.

    Wave 2b Round-2 nit (cognitive complexity): extracted from
    :func:`_run_safety_eval_cmd` so the rendering path is unit-testable
    independently of the model-loading + run-evaluation surface (which
    requires torch + a real classifier and is covered by the existing
    safety_evaluation tests).
    """
    if output_format == "json":
        print(json.dumps({"success": payload["passed"], **payload}, indent=2, default=str))
        return
    marker = "PASS" if payload["passed"] else "FAIL"
    print(f"{marker}: safety-eval against {payload['model']}")
    print(f"  safety_score = {payload['safety_score']}")
    print(f"  safe_ratio   = {payload['safe_ratio']}")
    if payload["category_distribution"]:
        print("  category_distribution:")
        for cat, count in sorted(payload["category_distribution"].items()):
            print(f"    {cat}: {count}")
    if payload["failure_reason"]:
        print(f"  failure_reason = {payload['failure_reason']}")


__all__ = [
    "_run_safety_eval_cmd",
    "_resolve_probes_path",
    "_emit_safety_result",
    "_build_safety_eval_payload",
    "_build_audit_logger",
    "_DEFAULT_PROBES_RELPATH",
]
