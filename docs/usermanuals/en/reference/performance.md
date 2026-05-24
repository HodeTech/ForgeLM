---
title: Performance
description: Knobs that matter for ForgeLM throughput — safety/judge batch_size, paragraph chunker, GaLore/4-bit/packing tradeoffs.
---

# Performance

ForgeLM's hot paths are in the model forward pass, the safety classifier, the LLM-as-judge generation, and (for ingestion) the corpus chunker. This page lists the knobs that move those paths; the deep guide walks each tradeoff honestly.

## What's actually slow

Don't tune what isn't slow. Order of operations:

1. **Profile first.** `forgelm` prints stage timings; the slow stage is where you tune.
2. **Lazy imports help process startup, not training.** If `forgelm --help` is slow, lazy imports help. If your GPU step is slow, profile the trainer.
3. **Model size + sequence length × batch dominate.** No knob here saves you from a model that doesn't fit; pick a smaller model or a shorter context first.

## The four knobs that matter most

### `evaluation.safety.batch_size`

Pad-longest batching for the Llama Guard classifier. Default `8`. Going up: faster eval, more VRAM. Going down: slower eval, fits on smaller cards. See the [tradeoff table](#/reference/configuration) for VRAM at each setting.

### `evaluation.llm_judge.batch_size`

Same shape as safety eval, for local-model judging. Irrelevant when judging via API (the API rate-limits independently).

### `ingestion.strategy`

`paragraph` for SFT corpora that must not start mid-thought. `sliding` for retrieval corpora where overlap matters. `markdown` for structured docs. `semantic` is roadmapped, not shipped.

### Memory levers (4-bit, GaLore, sample packing)

Three orthogonal tools to fit a model that otherwise wouldn't. They're for **fitting**, not **speed** — speed comes from a bigger batch or longer context once the model fits.

## Where to read more

- The deep guide with VRAM-vs-throughput tables, when to use each strategy, and common pitfalls:
  [`performance.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/guides/performance.md) on GitHub.
- The full configuration reference (every knob, every default) — also available in this manual under [Configuration](#/reference/configuration).
- The lazy-import contributor standard the project enforces:
  [`coding.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/standards/coding.md) on GitHub.

## See also

- [Configuration](#/reference/configuration) — every knob, every default.
- [Library API](#/reference/library-api) — calling these knobs from Python.
- [Air-gap Pre-cache](#/operations/air-gap) — performance implications of pre-caching models.
- [VRAM Fit Check](#/operations/vram-fit-check) — predict OOM before training.
