"""``--fit-check``: estimate peak training VRAM from config without loading the model."""

from __future__ import annotations

from .._strict_json import dumps_strict
from ..config import ForgeConfig


def _run_fit_check(config: ForgeConfig, output_format: str) -> None:
    """Estimate peak training VRAM from config without loading the model."""
    from ..fit_check import estimate_vram, format_fit_check

    result = estimate_vram(config)

    if output_format == "json":
        print(
            dumps_strict(
                {
                    "verdict": result.verdict,
                    "estimated_gb": result.estimated_gb,
                    "available_gb": result.available_gb,
                    "hypothetical": result.hypothetical,
                    "breakdown": result.breakdown,
                    "recommendations": result.recommendations,
                },
                indent=2,
            )
        )
        return

    print(format_fit_check(result))
