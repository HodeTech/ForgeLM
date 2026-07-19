# Performance Tuning Guide

> **Audience:** operators with a slow ForgeLM pipeline who want to know which knobs matter — and which don't. This guide is honest about *what* speeds up vs *what cost*; it does not promise a 10× from a one-line config change.
>
> **Companion reference:** [`../reference/configuration.md`](../reference/configuration.md) — every config field, including the batch-size knobs documented below.

ForgeLM's hot paths are not in the YAML parser, the audit logger, or the CLI dispatch — they're in the model forward pass, the safety classifier, the LLM-as-judge generation, and (for ingestion-heavy pipelines) the corpus chunker. This guide walks each of those, what knob exists, and what it costs in compute / memory / quality if you turn it up.

## Lazy torch import (what it costs not to break)

Importing `forgelm` does not import `torch`, `transformers`, `trl`, `datasets`, or any other heavy ML dep — by contract, pinned by `tests/test_library_api.py::test_lazy_import_no_torch`.

This is **import time**, not training throughput. The win lands in:

- `python -m forgelm.cli --help` returning in tens of milliseconds instead of multiple seconds.
- `forgelm doctor` (Phase 34) running on a host where torch is not yet installed.
- Lightweight CI runners (lint, dry-run, audit) skipping a 1-2 second torch load they do not need.
- Notebook authors who `from forgelm import detect_pii` for a one-off PII scan and never touch the trainer.

**It does not speed up training.** Once `ForgeTrainer.train()` is called, torch loads, the model loads, and the GPU initialises — same as if the import had been eager.

The standard is enforced by `docs/standards/coding.md` "Lazy import discipline": no module-top imports of `torch` in any file under `forgelm/`. Violations break CI:

```python
# CORRECT — heavy deps deferred to function bodies
def get_model_and_tokenizer(config):
    import torch                      # local import inside the function
    from transformers import AutoModelForCausalLM, AutoTokenizer
    ...

# WRONG — module-top heavy import; CI fails
import torch
from transformers import AutoModelForCausalLM
```

When adding a new module, if you find yourself reaching for `import torch` at the top of the file, ask whether the module's *purpose* requires torch to be loaded at all (most utilities don't).

## Safety-classifier batch_size

The Llama Guard safety evaluator generates classifications for every test prompt in `evaluation.safety.test_prompts`. Generation is batched at `evaluation.safety.batch_size` prompts at a time using pad-longest (so short prompts in a batch don't stall behind long ones).

Live signatures: `forgelm/safety/_generate.py::_generate_safety_responses` and `forgelm/safety/_orchestrator.py::run_safety_evaluation`.

```yaml
evaluation:
  safety:
    enabled: true
    classifier: "meta-llama/Llama-Guard-3-8B"
    test_prompts: "configs/safety_prompts/general_safety.jsonl"
    batch_size: 8                      # default
    max_safety_regression: 0.05
```

### What it costs to turn up

| `batch_size` | VRAM (Llama Guard 3 8B, fp16) | Throughput | Risk |
|---|---|---|---|
| 1 | ~16 GB | 1× (baseline) | None — safest fallback |
| 4 | ~17 GB | ~3.2× | Pad waste on heterogeneous prompt lengths |
| 8 (default) | ~18 GB | ~5.5× | Acceptable for most consumer + workstation GPUs |
| 16 | ~21 GB | ~9× | OOM on 24 GB cards if prompts > ~1.5 k tokens |
| 32 | ~28 GB | ~14× | OOM unless you have an A100 80 GB or H100 |

The numbers above are illustrative — measured on Llama Guard 3 8B with the bundled `general_safety.jsonl`; your prompt distribution + hardware will differ. Measure your tail-prompt length first.

### When to turn it down

- You're sharing the GPU with the trainer (concurrent eval) → use 1 or 2.
- Your prompt distribution has long-tail prompts (some at 2 k tokens, most at 200) → the pad-longest cost outweighs the parallelism win; use 4.
- You're on a 12 GB card → start at 1, raise only if `nvidia-smi` shows headroom.

### When to turn it up

- You have a dedicated 24 GB+ card for safety eval → 16 is usually fine.
- Your prompts are length-homogeneous (all < 512 tokens) → 32 may fit on 24 GB.

The library API boundary check rejects non-positive integers explicitly:

```python
# forgelm/safety/_inputs.py::_validate_batch_size
if not isinstance(batch_size, int) or batch_size < 1:
    raise ValueError(f"batch_size must be a positive integer (got {batch_size!r})")
```

That's deliberate — `batch_size: 0` or `batch_size: -1` are config typos, not zero-evaluation modes.

## LLM-as-judge batch_size

The LLM-as-judge evaluator (`forgelm/judge.py`) batches local-model judging the same way safety eval does. For API-backed judging (OpenAI, Anthropic), the parameter is irrelevant because the API rate-limits independently.

```yaml
evaluation:
  llm_judge:
    enabled: true
    judge_model: "Qwen/Qwen2.5-7B-Instruct"   # local model
    eval_dataset: "eval_prompts.jsonl"
    min_score: 7.0
    batch_size: 8                              # default
```

Same VRAM tradeoffs as safety eval; same library-API boundary check (`forgelm/judge.py`):

```python
if not isinstance(batch_size, int) or batch_size < 1:
    raise ValueError(f"batch_size must be a positive integer (got {batch_size!r})")
```

If the judge model is the same family as the safety classifier, you can usually use the same `batch_size`. If the judge is bigger (e.g. Qwen2.5-72B for high-stakes evals), drop to 1-2.

## Paragraph chunker (when to use vs sliding)

ForgeLM ingestion (`forgelm/ingestion.py`) supports four chunking strategies. The `paragraph` strategy is the one most operators reach for after running `sliding` and noticing chunks split mid-thought.

Ingestion is configured via CLI flags, not a YAML block — there is no top-level `ingestion:` key in `ForgeConfig`. The chunker selection happens at `forgelm ingest` invocation time:

```shell
$ forgelm ingest INPUT_PATH \
    --output data/policies.jsonl \
    --strategy paragraph        # sliding | paragraph | markdown | semantic
    --chunk-size 1024           # soft cap for paragraph; hard cap for sliding
    --overlap 128               # sliding only; ignored by paragraph
```

### Performance characteristics

| Strategy | Pass count | Memory | Output count | When to use |
|---|---|---|---|---|
| `sliding` | 1 | O(chunk_size) | high (overlapping windows) | Long-context retrieval where window-level search matters more than semantic boundaries |
| `paragraph` | 1 | O(longest_paragraph) | medium (one chunk per paragraph cluster) | SFT corpora where examples must not start mid-sentence |
| `markdown` | 1 | O(longest_section) | low (one chunk per heading) | Structured technical docs where heading-breadcrumbs matter |
| `semantic` | n/a — `NotImplementedError` | n/a | n/a | Roadmapped follow-up phase; embedding-model cost not yet justified |

### Paragraph-chunker invariant

The greedy paragraph packer (`_chunk_paragraph` in `forgelm/ingestion.py`) **never splits a paragraph mid-sentence**. Paragraphs longer than `chunk_size` are emitted whole — `chunk_size` becomes a soft cap, not a hard cap. This is by design: an SFT example that starts mid-thought trains the model to start mid-thought.

The cost: chunks are not uniform length. If your downstream pipeline assumes "every chunk is exactly 1024 tokens," use `sliding` instead.

### When `paragraph` is faster than `sliding`

- Your input has well-separated paragraphs (`\n\n` separators). `paragraph` runs one greedy pass; `sliding` runs `len(text) / (chunk_size - overlap)` window emissions.
- You don't need overlapping context. Overlap doubles your output count without doubling information.

### When `sliding` is faster than `paragraph`

- Your input is a single long paragraph (no `\n\n`). `paragraph` emits one chunk equal to the full input; `sliding` emits a manageable list.
- You're feeding a retrieval index where window-level recall matters and overlap is the point.

## GaLore + 4-bit + sample packing tradeoffs

Three orthogonal memory levers operators reach for when their model + sequence length doesn't fit. Honest trade-off table:

| Lever | Memory saved | Speed change | Quality change | When NOT to use |
|---|---|---|---|---|
| **4-bit quant** (NF4 via `bitsandbytes`) | ~75% on weights | ~5-15% slower forward | Small accuracy drop on most tasks; visible on math / code | Math + code SFT — measure first |
| **GaLore** (low-rank gradient projection) | ~30-50% on optimizer state | ~10-20% slower per step | Roughly preserved with rank ≥ 256 on most tasks | Tiny models where the projection matrix dominates the gradient |
| **Sample packing** (concatenate short samples) | ~0 (memory-neutral) | ~30-50% faster on short-sample corpora | None if attention masks are correct; data leak if attention masks are missing | When attention masking is unreliable; verify with `forgelm audit` |

**Combining them.** 4-bit + GaLore is the workstation-builder's stack: an 8B model on a 24 GB card with 32k context. Sample packing layers on top whichever quant + optimizer choice you made — it's an input-pipeline change, not a model change.

**The dishonest claim to avoid.** "Enable 4-bit and your training runs 4× faster." 4-bit makes models *fit*; it does not make them *fast*. Speed comes from a bigger batch or a longer context once the model fits.

## Common pitfalls

### "I enabled lazy imports and my training is the same speed"

Lazy imports affect process startup, not training throughput. If your CI or notebook startup feels slow, lazy imports help; if your training step feels slow, profile the trainer (gradient accumulation, dataloader workers, tensor core utilisation) instead.

### "I increased `safety.batch_size` to 64 and now my eval OOMs"

The safety classifier holds activations for the full batch. 64 × ~2 k tokens × ~32 layers × hidden-size is a lot of activation memory. Drop back to 8, or measure the actual OOM threshold on your hardware before pinning a config in CI.

### "I used `paragraph` chunking and my chunks are huge"

That's the contract. Paragraphs longer than `chunk_size` are emitted whole. If you need a hard cap, switch to `sliding` (and accept the mid-sentence splits) or run a pre-pass that splits paragraphs at sentence boundaries.

### "GaLore made my training slower"

GaLore trades memory for compute. The projection matrix multiplication is real overhead. The win is fitting a model that otherwise wouldn't fit; if your model already fits, GaLore is a regression.

### "I'm batching the audit / verify / library calls in a tight loop and CPU is pegged"

`audit_dataset` accepts a `workers` parameter — use it for parallel I/O over multiple corpora. `verify_audit_log` is single-threaded by design (the SHA-256 chain is inherently sequential); if you have many logs to verify, parallelise across processes, not within one verify call.

### "Profiling says my hot path is the dataloader"

Check `dataloader_num_workers` (in your training config) and the format-detection cost of `prepare_dataset`. JSONL is slower than parquet for large corpora; v0.5.5 only emits text/JSON via `forgelm audit --output-format {text,json}`, so convert one-off with `python -c "import pandas as pd; pd.read_json('audit.jsonl', lines=True).to_parquet('audit.parquet')"` (or any equivalent `pandas`/`pyarrow` step).

## See also

- [`../reference/configuration.md`](../reference/configuration.md) — `evaluation.safety.batch_size`, `evaluation.llm_judge.batch_size`, `ingestion.strategy`, and every other knob.
- [`library_api.md`](library_api.md) — calling these knobs from Python.
- [`../standards/coding.md`](../standards/coding.md) — the lazy-import standard ForgeLM enforces.
- [`ingestion.md`](ingestion.md) — the chunker in user-facing depth.
- [`alignment.md`](alignment.md) — when GaLore + 4-bit + packing are appropriate.
