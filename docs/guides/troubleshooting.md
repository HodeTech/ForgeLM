# Troubleshooting & FAQ

Common issues and solutions when using ForgeLM.

---

## Installation Issues

### `ModuleNotFoundError: No module named 'bitsandbytes'`

bitsandbytes (QLoRA) only works on Linux:

```bash
# Linux only
pip install forgelm[qlora]

# macOS/Windows: disable 4-bit quantization
# In your config:
model:
  load_in_4bit: false
```

### `ModuleNotFoundError: No module named 'unsloth'`

Unsloth only works on Linux:

```bash
# Linux only
pip install forgelm[unsloth]

# Other platforms: use transformers backend
model:
  backend: "transformers"
```

### `ImportError: lm-evaluation-harness is required`

```bash
pip install forgelm[eval]
```

---

## Training Issues

### CUDA Out of Memory (OOM)

**Solutions (in order of impact):**

1. **Enable 4-bit quantization** (if not already):
   ```yaml
   model:
     load_in_4bit: true
   ```

2. **Enable automatic OOM recovery** (ForgeLM retries with progressively smaller batch sizes):
   ```yaml
   training:
     per_device_train_batch_size: 8
     gradient_accumulation_steps: 2
     oom_recovery: true              # auto-halve batch on OOM
     oom_recovery_min_batch_size: 1  # stop at batch_size=1
   ```
   Effective batch size is preserved across retries. Each attempt is logged to the audit trail.

   > **Note on the retry trajectory.** A successful retry rebuilds the trainer with a fresh optimizer and LR scheduler (step 0) at the smaller batch size; it does not continue from the exact step where the OOM fired unless you launched the run with an explicit `--resume <checkpoint>` (in which case it rewinds to that checkpoint). The optimization trajectory therefore differs slightly from an uninterrupted run. This is expected — for bit-for-bit reproducibility, fix the batch size manually instead of relying on auto-recovery.

3. **Reduce batch size manually**:
   ```yaml
   training:
     per_device_train_batch_size: 1
     gradient_accumulation_steps: 8  # keep effective batch size
   ```

3. **Reduce max sequence length**:
   ```yaml
   model:
     max_length: 1024  # down from 2048
   ```

4. **Use DeepSpeed ZeRO-3 for large models**:
   ```yaml
   distributed:
     strategy: "deepspeed"
     deepspeed_config: "zero3_offload"
   ```

5. **Reduce LoRA rank**:
   ```yaml
   lora:
     r: 8  # down from 16
   ```

### Training Loss is NaN or Inf

**Causes:**
- Learning rate too high
- Batch size too small without gradient accumulation
- Mixed precision issues

**Solutions:**

```yaml
training:
  learning_rate: 1.0e-5  # reduce from 2e-5
  gradient_accumulation_steps: 4
```

If persists, disable mixed precision by ensuring `bf16: false` and `fp16: false` (these are ForgeLM defaults).

### Training is Very Slow

1. **Use Unsloth** (Linux, 2-5x speedup):
   ```yaml
   model:
     backend: "unsloth"
   ```

2. **Enable packing** (if your data supports it):
   ```yaml
   training:
     packing: true
   ```

3. **Use multiple GPUs**:
   ```yaml
   distributed:
     strategy: "deepspeed"
     deepspeed_config: "zero2"
   ```

### GaLore + Multi-GPU / Layerwise Incompatibility

GaLore's layerwise optimizer variant (`galore_adamw_layerwise`) is **not compatible** with multi-GPU training (DeepSpeed/FSDP). Use the standard GaLore optimizer instead:

```yaml
training:
  galore_enabled: true
  galore_optim: "galore_adamw_8bit"  # NOT "galore_adamw_layerwise"

# Do NOT combine layerwise GaLore with distributed training:
# distributed:
#   strategy: "deepspeed"  # will fail with layerwise GaLore
```

If you need both multi-GPU and GaLore, use `galore_adamw` or `galore_adamw_8bit`.

### Long-Context Training VRAM Issues

Long-context training (large `sliding_window_attention` or RoPE scaling) significantly increases VRAM usage. To mitigate:

1. **Reduce sliding window size**:
   ```yaml
   training:
     sliding_window_attention: 2048  # down from 4096
   ```

2. **Enable gradient checkpointing** (reduces VRAM at cost of speed):
   ```yaml
   training:
     gradient_checkpointing: true
   ```

3. **Use sequence packing** to reduce padding waste:
   ```yaml
   training:
     packing: true
   ```

4. **Combine with GaLore** for additional memory savings:
   ```yaml
   training:
     galore_enabled: true
     galore_rank: 64  # lower rank = less memory
   ```

### Synthetic Data API Timeout

If the teacher model API times out during `--generate-data`:

```yaml
synthetic:
  api_timeout: 120   # increase from default 60 seconds
  api_delay: 1.0     # seconds between API calls (rate limiting; default 0.5)
  max_new_tokens: 512 # cap teacher response size if it's hanging
```

`SyntheticConfig` does not surface dedicated retry / batch knobs in v0.5.5
— retries are handled at the HTTP-client layer, and batch size is fixed
at one prompt per API call. Phase 28+ backlog tracks adding explicit
retry-count and batched-call parameters.

For local teacher models, ensure sufficient GPU memory is available. Consider using a smaller teacher model or reducing `synthetic.max_new_tokens`.

### `ValueError: Unknown trainer_type`

Valid trainer types: `sft`, `orpo`, `dpo`, `simpo`, `kto`, `grpo`

```yaml
training:
  trainer_type: "sft"  # check spelling
```

### `KeyError: Dataset must contain 'chosen' and 'rejected' columns`

Your dataset format doesn't match the trainer type:

| Trainer | Required Columns |
|---------|-----------------|
| `sft` | `User`/`instruction` + `Assistant`/`output`, or `messages` |
| `dpo`, `simpo`, `orpo` | `chosen` + `rejected` |
| `kto` | `completion` + `label` (boolean) |
| `grpo` | `prompt` |

---

## Configuration Issues

### How to Validate Config Without Training

```bash
forgelm --config my_config.yaml --dry-run
```

### Config Validation Error: Field Type Mismatch

ForgeLM uses Pydantic v2 for validation. Error messages show the exact field:

```text
Configuration validation failed: 1 validation error for ForgeConfig
training -> learning_rate
  Input should be a valid number [type=float_parsing, input_value='not_a_number']
```

Fix the YAML value to match the expected type.

### Unknown Fields in Config (v0.3.1rc1+)

ForgeLM now **rejects unknown fields** in YAML configs — all sub-models enforce strict validation (`extra="forbid"`). Typos or unsupported fields raise a clear error:

```text
ConfigError: Configuration validation failed: 1 validation error for ForgeConfig
training.lerning_rate
  Extra inputs are not permitted [type=extra_forbidden, input_value=2e-5]
```

This is intentional: silent typos (like `lerning_rate` instead of `learning_rate`) previously caused training to run with wrong defaults. Now they fail fast with a clear message.

**To fix:** Check the error message for the exact field path (e.g., `training.lerning_rate`) and correct the field name.

**To see all valid fields:** Run `forgelm --config job.yaml --dry-run` which lists all resolved parameter values.

### Deprecated LoRA Method Syntax

The boolean flags `lora.use_dora` and `lora.use_rslora` are deprecated. Use `lora.method` instead:

```yaml
# New (recommended)
lora:
  method: "dora"      # or "rslora", "pissa", "lora"

# Deprecated (still works, emits warning, auto-normalizes)
lora:
  use_dora: true      # deprecated — auto-sets method: "dora"
  use_rslora: true    # deprecated — auto-sets method: "rslora"
```

### `mix_ratio` Validation Error

```text
ConfigError: mix_ratio values must be non-negative
ConfigError: mix_ratio values cannot all be zero
```

`mix_ratio` controls the sampling ratio for multi-dataset training. It must have non-negative values and cannot be all zeros:

```yaml
data:
  dataset_name_or_path: "org/primary-dataset"
  extra_datasets: ["org/secondary-dataset"]
  mix_ratio: [0.7, 0.3]  # 70% primary, 30% secondary
  # mix_ratio: [-0.5, 1.0]  # negative values not allowed
  # mix_ratio: [0.0, 0.0]   # all zeros not allowed
```

---

## Evaluation Issues

### Auto-Revert Keeps Deleting My Model

Your evaluation thresholds may be too strict:

```yaml
evaluation:
  auto_revert: true
  max_acceptable_loss: 2.0  # increase this if models keep being reverted
```

Or disable auto-revert and check manually:

```yaml
evaluation:
  auto_revert: false
```

### Benchmark Scores Are Zero

- Check that `lm-eval` is installed: `pip install forgelm[eval]`
- Check that benchmark tasks are valid: `arc_easy`, `hellaswag`, `mmlu`, etc.
- Try with `limit: 10` first for quick testing:
  ```yaml
  evaluation:
    benchmark:
      enabled: true
      tasks: ["arc_easy"]
      limit: 10
  ```

---

## Multi-GPU Issues

### NCCL Errors

```bash
# Increase timeout
export NCCL_TIMEOUT=1800

# Debug NCCL
export NCCL_DEBUG=INFO

# Check GPUs are visible
nvidia-smi
```

### DeepSpeed Config Not Found

```text
FileNotFoundError: DeepSpeed preset 'zero2' not found
```

Ensure you're running from the ForgeLM project root, or use an absolute path:

```yaml
distributed:
  deepspeed_config: "/path/to/ForgeLM/configs/deepspeed/zero2.json"
```

### QLoRA + ZeRO-3 Issues

QLoRA (4-bit) with DeepSpeed ZeRO-3 has known compatibility issues. Use ZeRO-2 instead:

```yaml
distributed:
  strategy: "deepspeed"
  deepspeed_config: "zero2"  # not zero3
```

---

## Docker Issues

### GPU Not Detected in Docker

```bash
# Ensure NVIDIA Container Toolkit is installed
nvidia-smi  # should work on host

# Run with GPU support
docker run --gpus all forgelm --version
```

### Shared Memory Error in Multi-GPU Docker

```bash
docker run --gpus all --shm-size=16g ...
```

---

## Exit Codes

| Code | Meaning | Action |
|------|---------|--------|
| `0` | Success | Model is ready |
| `1` | Config error | Fix your YAML |
| `2` | Training error | Check GPU, memory, dependencies |
| `3` | Evaluation failure | Model quality below threshold — adjust thresholds or improve data |
| `4` | Awaiting approval | Human review required — run `forgelm approvals --show <run_id> --output-dir <dir>` to inspect the staging directory, then `forgelm approve <run_id> --output-dir <dir>` to promote or `forgelm reject <run_id> --output-dir <dir>` to discard. The staging path is `<output_dir>/final_model.staging.<run_id>/`. |

---

## Getting Help

- **GitHub Issues**: [github.com/HodeTech/ForgeLM/issues](https://github.com/HodeTech/ForgeLM/issues)
- **Dry-run debug**: `forgelm --config job.yaml --dry-run --log-level DEBUG`
- **JSON diagnostics**: `forgelm --config job.yaml --output-format json 2>error.log`
