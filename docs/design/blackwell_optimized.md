# Design: Nvidia Blackwell (GB10) Optimizations

> **Status: Not yet implemented (Phase 28+ backlog).** This document
> captures the *proposed* Blackwell support surface; nothing in
> `forgelm/` ships any of the items below today. `expandable_segments`
> wiring, the `scripts/setup_for_blackwell.sh` setup script, the
> auto-detect path in `forgelm/model.py`, and the bf16 + sequence-packing
> auto-enable flow are all pending. Operators on Blackwell hardware
> currently configure `expandable_segments` via the upstream
> `PYTORCH_ALLOC_CONF` env var directly and set `training.bf16: true` /
> `training.packing: true` by hand.

## Overview
Specific optimizations for the latest Nvidia Blackwell architecture (notably GB10 with 128GB unified RAM) to maximize throughput and stability.

## Key Optimizations
1.  **Memory Management**:
    -   Expose `expandable_segments` in the `PYTORCH_ALLOC_CONF` via `config.yaml`.
    -   This prevents memory fragmentation during long-sequence training on unified memory architectures.
2.  **Environment Setup**:
    -   `scripts/setup_for_blackwell.sh`: A script that installs PyTorch nightly builds and CUDA 13.0 dependencies specifically for Blackwell.
3.  **Hyperparameter Tuning**:
    -   Automatically enable `bf16` and `sequence packing` when Blackwell is detected, as these are first-class citizens in this architecture.

## Implementation Details
- Update `forgelm/utils.py` to include a helper for environment variable management.
- Update `forgelm/model.py` to auto-detect hardware capabilities and warn if sub-optimal flags are set.
