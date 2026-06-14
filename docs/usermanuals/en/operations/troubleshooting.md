---
title: Troubleshooting
description: Common ForgeLM errors and their fixes.
---

# Troubleshooting

This page lists the most common ForgeLM errors and the fixes that work. For anything not listed, run `forgelm doctor` first — it catches 80% of environment problems.

## Installation issues

### `bitsandbytes` import error on macOS

```text
ImportError: bitsandbytes/libbitsandbytes_cpu.so: cannot find ...
```

**Cause:** `bitsandbytes` doesn't support Metal/MPS. macOS hosts can't run 4-bit quantised training (QLoRA).

**Fix:** Either run on Linux with CUDA, or train at full precision (set `model.load_in_4bit: false`).

### `undefined symbol: __cudaRegisterFatBinaryEnd`

**Cause:** PyTorch and CUDA toolkit version mismatch.

**Fix:** Reinstall PyTorch matching your CUDA version:

```shell
$ pip uninstall torch torchvision torchaudio
$ pip install torch --index-url https://download.pytorch.org/whl/cu121   # match your CUDA
```

### `OSError: HuggingFace token not found`

**Cause:** Some models (Llama, Llama Guard) are gated and require an auth token.

**Fix:** Run `huggingface-cli login`, or set `HF_TOKEN` environment variable. ForgeLM picks up either.

## Training issues

### Loss goes to NaN

```text
[2026-04-29 14:18:55] training_step_complete loss=nan
```

**Causes (most common first):**
1. Learning rate too high (especially with full FT — try 1e-5).
2. Gradient overflow at very low precision (fp16). Switch to bfloat16: `model.bnb_4bit_compute_dtype: "bfloat16"`.
3. Bad data row with extreme tokens (run `forgelm audit` and check for quality flags).
4. Custom reward function returning Inf/NaN (GRPO only).

### Loss decreases on train, increases on eval

**Cause:** Overfitting, especially with small datasets and many epochs.

**Fix:**
- Reduce `epochs` (start with 1-3, not 10).
- Add `neftune_noise_alpha: 5.0` for embedding regularisation.
- Lower learning rate.
- Add more diverse training data.

### Training crashes at step 0

**Cause:** Almost always a configuration problem caught by `--dry-run`.

**Fix:** Always run `forgelm --config X.yaml --dry-run` before training. The 90% of "training crashed" reports trace back to skipped dry-runs.

### Training is much slower than expected

**Causes (most common first):**
1. Not using Unsloth on a supported model (`model.use_unsloth: true` for Qwen / Llama / Mistral).
2. `packing: false` on instruction data (enabling it gives 30-50% throughput).
3. CPU offload accidentally enabled (`distributed.cpu_offload: false`).
4. Mixed-precision misconfigured (use `bfloat16`, not `float16`).
5. Disk I/O bound — your dataset is on slow storage.

`nvidia-smi` during training: GPU utilization should be >85%. If it's <50%, you're CPU- or I/O-bound, not GPU-bound.

## OOM errors

### `CUDA out of memory` mid-training

**Cause:** Activation memory burst from a particularly long sequence.

**Fix:**
- Run `--fit-check` to confirm peak estimate.
- Reduce `max_length` if data has long outliers.
- Enable `packing: true` (uniformises sequence length).
- Lower `batch_size` and increase `gradient_accumulation_steps` (same effective batch, less peak).

### OOM at evaluation, not training

**Cause:** Eval often runs without sliding-window or packing — peak can exceed training peak.

**Fix:** Set evaluation `max_length` lower than training:

```yaml
evaluation:
  max_length: 4096      # train at 32K, eval at 4K
```

## Data issues

### `audit refuses to certify a leaky split`

**Cause:** Train rows also appear in val or test (`cross_split_overlap > 0`).

**Fix:** Re-split your data, ensuring no document spans multiple splits. See [Cross-Split Leakage](#/data/leakage).

### Audit reports too many quality flags

**Cause:** Quality filter is conservative-by-default but can over-flag for code or symbol-heavy data.

**Fix:** Disable specific checks in YAML:

```yaml
audit:
  quality_filter:
    skip: ["min_alpha_ratio", "max_bullet_ratio"]
```

### Format auto-detection wrong

**Cause:** First row of JSONL doesn't match the rest.

**Fix:** Set format explicitly: `format: "preference"` (or whichever).

## Eval / safety issues

### Llama Guard always reports "high severity"

**Cause:** Probe set contains adversarial inputs the base model already failed on. Llama Guard correctly flags both base and fine-tuned outputs.

**Fix:** This is correct behaviour. The check that matters is *regression vs baseline*, not absolute scores. Make sure `evaluation.safety.baseline` points at the pre-train baseline.

### Benchmark scores wildly different from public results

**Cause:** Likely `num_fewshot` mismatch with the published leaderboard convention.

**Fix:** Check the canonical setting for each task (e.g. MMLU is canonically 5-shot) and match it.

## Compliance issues

### `audit_log.jsonl chain hash invalid`

**Cause:** The audit log was modified (or the file system corrupted it).

**Fix:** Don't modify the log. If a corruption happened, the original artifacts are no longer trustworthy — re-run training from a clean state.

### Annex IV missing required fields

**Cause:** Required `compliance:` fields not set in YAML.

**Fix:** Run `forgelm verify-annex-iv path/to/annex_iv_metadata.json` for a list of missing fields (the canonical filename ForgeLM writes — see [Annex IV](#/compliance/annex-iv)).

## Webhooks not firing

**Causes:**
- Webhook URL points at a private IP and `webhook.allow_private` is false.
- TLS certificate validation fails.
- Endpoint returns 4xx (silent in logs by default).

**Fix:** Check `audit_log.jsonl` for `webhook_failed` events; they include the response status and body.

## Where to file a bug

If `forgelm doctor` says everything's fine but the problem persists, gather:

1. The exact `forgelm` command that fails.
2. The full error output.
3. Your `config.yaml` (with secrets redacted).
4. Output of `forgelm doctor`.

Open an issue: <https://github.com/HodeTech/ForgeLM/issues>. The maintainer team triages within 48 hours.

## See also

- [`forgelm doctor`](#/getting-started/installation) — first-line diagnostic.
- [VRAM Fit-Check](#/operations/vram-fit-check) — pre-flight memory check.
- [Configuration Reference](#/reference/configuration) — every YAML field.
