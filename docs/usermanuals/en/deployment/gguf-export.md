---
title: GGUF Export
description: Quantise to GGUF for local inference with llama.cpp, Ollama, or LM Studio.
---

# GGUF Export

GGUF (GPT-Generated Unified Format) is the de facto file format for local CPU/GPU inference via `llama.cpp`. ForgeLM exports a fine-tuned checkpoint to GGUF and writes a SHA-256 sidecar next to the file. `f16` and `q8_0` come out of a single command; the K-quant levels (`q2_k`, `q3_k_m`, `q4_k_m`, `q5_k_m`) need one more `llama-quantize` step, described below.

## Quick example

```shell
$ forgelm export ./checkpoints/customer-support \
    --output model.gguf \
    --quant q8_0
Export complete: model.gguf (quant=q8_0, sha256=3f2a91c0b4d7…)
```

(Log output, abridged.) The result is `model.gguf` plus `model.gguf.sha256` next to it. The sidecar is in `sha256sum` format (`<digest> *model.gguf`), so both `sha256sum -c model.gguf.sha256` and [`forgelm verify-gguf`](#/deployment/verify-gguf) accept it. When the checkpoint directory already holds a `model_integrity.json`, ForgeLM also tries to record the digest there under `exported_artifacts` (best effort: if the file is not valid JSON or cannot be written, a warning is logged and the export still succeeds; `--no-integrity-update` skips this).

The sidecar detects corruption and accidental change. It sits beside the file, so it is **not** tamper evidence: whoever can replace the GGUF can replace the sidecar too. If you need that, sign the file or keep the digest where the file's writer cannot reach it.

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

### K-quants take one more step

The converter ForgeLM calls writes `f16` and `q8_0` directly. For a K-quant (`q2_k`, `q3_k_m`, `q4_k_m` — the default — and `q5_k_m`) `forgelm export` writes an intermediate `f16` GGUF, named like the output with its `.gguf` suffix replaced by `.f16.gguf` (`model.gguf` becomes `model.f16.gguf`; an output without the suffix gets `.f16.gguf` appended), with its own `.sha256`. It reports the substitution (`requested_quant`, `manual_step_required` and the exact `followup_command` in `--output-format json`) and logs the `llama-quantize` command that finishes the job:

```shell
$ forgelm export ./checkpoints/customer-support --output model.gguf --quant q4_k_m
Export complete: model.f16.gguf (quant=f16, sha256=9c1e47ab02d5…)
Manual quantization step required — run: llama-quantize model.f16.gguf model.gguf Q4_K_M
$ llama-quantize model.f16.gguf model.gguf Q4_K_M
$ sha256sum model.gguf > model.gguf.sha256
```

`llama-quantize` comes from llama.cpp; ForgeLM does not install it. `forgelm export` writes no sidecar for the quantised file, so the last command creates one and `forgelm verify-gguf model.gguf` has a digest to compare.

GGUF export is always a separate, explicit `forgelm export` invocation — there is no config-driven auto-export toggle. `forgelm/config.py` has no `output:` block and no `gguf` field anywhere in the schema, so a training YAML cannot trigger export as a side effect of a passing eval gate; run `forgelm export` yourself once training finishes.

## Multi-quant export

`forgelm export --quant` accepts a single value per invocation (one
of `{q2_k, q3_k_m, q4_k_m, q5_k_m, q8_0, f16}`). To produce several
quantisations from one checkpoint, run the command once per quant. For
the two levels the converter writes directly:

```shell
$ for q in q8_0 f16; do
    forgelm export ./checkpoints/run \
        --output "./gguf/model.${q}.gguf" \
        --quant "${q}"
  done
Export complete: ./gguf/model.q8_0.gguf (quant=q8_0, sha256=3f2a91c0b4d7…)
Export complete: ./gguf/model.f16.gguf (quant=f16, sha256=b81d05e6c392…)
```

Each run writes its own sidecar (`model.q8_0.gguf.sha256`, `model.f16.gguf.sha256`). For K-quants, run the loop with the K-quant names and finish each intermediate file with `llama-quantize`, as above.

When the checkpoint directory holds a `model_integrity.json`, each run tries to append an entry to its `exported_artifacts` list (best effort, as above):

```json
{
  "exported_artifacts": [
    {"file": "model.q8_0.gguf", "format": "gguf", "quant": "q8_0", "sha256": "…", "size_bytes": 7200000000},
    {"file": "model.f16.gguf", "format": "gguf", "quant": "f16", "sha256": "…", "size_bytes": 13000000000}
  ]
}
```

## Verifying GGUF integrity

```shell
$ forgelm verify-gguf model.q8_0.gguf
OK: model.q8_0.gguf
  GGUF magic OK, metadata parsed, SHA-256 sidecar match
    magic_ok: True
    metadata_parsed: True
    sidecar_present: True
    sidecar_match: True
    tensor_count: 291
    sha256_actual: a4c1f2…
    sha256_expected: a4c1f2…
```

This checks the GGUF magic header, parses the metadata block when the optional `gguf` package is installed, and compares the `<file>.sha256` sidecar when it exists. It does not compare the vocabulary or the tokenizer with the base model. It catches corruption from bad transfers, silent disk errors and a file edited after export; it does not show who produced the file. [Verify GGUF](#/deployment/verify-gguf) says what each layer proves.

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
**Forgetting `--adapter`.** `forgelm export` merges a LoRA adapter into the base model only when you pass `--adapter <adapter-dir>`; without it, the model path must already be a full model directory. If you want adapter-only inference, you don't want GGUF — use `forgelm chat` against the adapter directly or load with PEFT.
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
