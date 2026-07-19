---
title: GGUF Export
description: Quantise to GGUF for local inference with llama.cpp, Ollama, or LM Studio.
---

# GGUF Export

GGUF (GPT-Generated Unified Format) is the de facto file format for local CPU/GPU inference via `llama.cpp`. ForgeLM exports any fine-tuned checkpoint to GGUF with six quantisation levels and a SHA-256 manifest.

## Quick example

```shell
$ forgelm export ./checkpoints/customer-support \
    --output model.gguf \
    --quant q4_k_m
✓ merged LoRA into base
✓ converted to GGUF
✓ quantised to q4_k_m (4.1 GB → 4.1 GB)
✓ wrote model.gguf and model.gguf.sha256
```

The output is a single `.gguf` file plus a `.sha256` manifest.

## Quantisation levels

| Level | Size (7B base) | Quality | Use case |
|---|---|---|---|
| `f16` | 13 GB | Lossless | Quality benchmark; full-precision archive. |
| `q8_0` | 7.2 GB | Highest | Production where memory is plentiful. |
| `q5_k_m` | 4.8 GB | High | Sensible balance. |
| `q4_k_m` | 4.1 GB | Good | **Default for local inference.** |
| `q3_k_m` | 3.3 GB | Acceptable | Tight memory; some quality loss. |
| `q2_k` | 2.6 GB | Lower | Last-resort for edge devices; noticeable quality loss. |

`q4_k_m` is the sweet spot — fits easily on consumer hardware, minimal quality loss versus full precision.

GGUF export is always a separate, explicit `forgelm export` invocation — there is no config-driven auto-export toggle. `forgelm/config.py` has no `output:` block and no `gguf` field anywhere in the schema, so a training YAML cannot trigger export as a side effect of a passing eval gate; run `forgelm export` yourself once training finishes.

## Multi-quant export

`forgelm export --quant` accepts a single value per invocation (one
of `{q2_k, q3_k_m, q4_k_m, q5_k_m, q8_0, f16}`). To produce several
quantisations from one checkpoint, run the command once per quant:

```shell
$ for q in q4_k_m q5_k_m q8_0; do
    forgelm export ./checkpoints/run \
        --output "./gguf/model.${q}.gguf" \
        --quant "${q}"
  done
✓ wrote gguf/model.q4_k_m.gguf  (4.1 GB)
✓ wrote gguf/model.q5_k_m.gguf  (4.8 GB)
✓ wrote gguf/model.q8_0.gguf    (7.2 GB)
```

The manifest:

```json
{
  "base_model": "Qwen/Qwen2.5-7B-Instruct",
  "fine_tune_run_id": "abc123",
  "quants": {
    "q4_k_m": {"size_bytes": 4100000000, "sha256": "..."},
    "q5_k_m": {"size_bytes": 4800000000, "sha256": "..."},
    "q8_0":   {"size_bytes": 7200000000, "sha256": "..."}
  },
  "exported_at": "2026-04-29T15:00:00Z"
}
```

## Verifying GGUF integrity

```shell
$ forgelm verify-gguf model.q4_k_m.gguf
✓ valid GGUF magic
✓ vocab size matches base
✓ sha256 matches manifest
✓ tokenizer round-trip OK
```

This catches corruption from bad transfers, silent disk errors, and the occasional `llama.cpp` upstream incompatibility.

## Loading GGUF in popular tools

### Ollama

```shell
$ cat > Modelfile <<'EOF'
FROM ./model.q4_k_m.gguf
SYSTEM "You are a polite customer-support agent."
PARAMETER temperature 0.7
EOF
$ ollama create my-bot -f Modelfile
$ ollama run my-bot
```

### LM Studio

Drop the `.gguf` file into LM Studio's models directory; it appears in the picker.

### llama.cpp directly

```shell
$ ./main -m model.q4_k_m.gguf -p "Hello, how are you?" -n 256
```

## Direct conversion (no quantisation)

For the rare case where you want full-precision GGUF (e.g. for a quantisation-sensitive inference engine):

```shell
$ forgelm export ./checkpoints/run --output model.gguf --quant f16
```

The result is full-precision GGUF, ~13 GB for a 7B model.

## Common pitfalls

:::warn
**Exporting LoRA without merging.** `forgelm export` always merges LoRA adapters into the base before quantising. If you want adapter-only inference, you don't want GGUF — use `forgelm chat` against the adapter directly or load with PEFT.
:::

:::warn
**Tokeniser version mismatch.** GGUF embeds the tokeniser. If you change tokenisers post-training (rare), the GGUF won't load correctly in `llama.cpp`. Always export from the same checkpoint that was actually trained.
:::

:::warn
**Quality regression vs original.** Aggressive quants (q3, q2) can shift Llama Guard scores. `safety-eval` does not yet load GGUF files, so re-run it against the **pre-export HuggingFace checkpoint** before you quantise — and treat post-quant safety drift as a known limitation until GGUF safety-eval lands:

```shell
$ forgelm safety-eval --model ./output/final_model --probes data/safety-probes.jsonl
```

:::

:::tip
For HuggingFace Hub upload, ForgeLM's model card includes a "Use with Ollama" snippet referencing your GGUF file — copy-paste-ready.
:::

## See also

- [Deploy Targets](#/deployment/deploy-targets) — non-GGUF deployment options.
- [CLI Reference](#/reference/cli) — the full `forgelm export` flag surface.
- [Model Merging](#/deployment/model-merging) — combining adapters before export.
