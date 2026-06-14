"""Training result dataclass — importable without heavy ML dependencies."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class TrainResult:
    """Result of a ForgeLM training run."""

    success: bool
    metrics: Dict[str, float] = field(default_factory=dict)
    final_model_path: Optional[str] = None
    reverted: bool = False
    error: Optional[str] = None
    benchmark_scores: Optional[Dict[str, float]] = None
    benchmark_average: Optional[float] = None
    benchmark_passed: Optional[bool] = None
    resource_usage: Optional[Dict[str, Any]] = None
    # Safety evaluation (Phase 9)
    safety_passed: Optional[bool] = None
    safety_score: Optional[float] = None
    safety_categories: Optional[Dict[str, int]] = None
    safety_severity: Optional[Dict[str, int]] = None
    safety_low_confidence: int = 0
    # Judge evaluation
    judge_score: Optional[float] = None
    judge_details: Optional[List[Dict[str, Any]]] = None
    # Cost estimation
    estimated_cost_usd: Optional[float] = None
    # Article 14 — human approval gate. Populated when
    # ``evaluation.require_human_approval=true`` so the saved adapters land in
    # ``<final_model_dir>.staging.<run_id>/`` instead of ``<final_model_dir>/``. The
    # canonical ``final_model/`` directory only appears after
    # ``forgelm approve <run_id>`` promotes the staging artefacts.
    staging_path: Optional[str] = None
    # Discriminator: ``True`` only when the human-approval gate genuinely fired
    # and the model is staged pending sign-off (always alongside
    # ``success=True``). This is the field the CLI/pipeline route on to choose
    # exit 4 (awaiting approval) vs exit 3 (auto-reverted) — a reverted stage
    # deletes its staging dir and must NEVER be reported as awaiting approval.
    # ``staging_path`` alone is not a safe discriminator (it can survive a
    # revert); ``awaiting_approval`` is the authoritative one.
    awaiting_approval: bool = False
    # Reproducibility anchors surfaced in the JSON run-output envelope so a
    # CI/CD consumer can correlate the run with its audit_log.jsonl (``run_id``)
    # and confirm the config that produced it (``config_hash``). Mandated by
    # logging-observability.md "Structured JSON output" rule 2 (XP-11 /
    # F-P4-OPUS-15). Optional so library callers constructing TrainResult by
    # hand are unaffected.
    run_id: Optional[str] = None
    config_hash: Optional[str] = None
