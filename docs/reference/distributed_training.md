# Distributed Training Guide (Multi-GPU)

ForgeLM supports multi-GPU distributed training via **DeepSpeed** and **PyTorch FSDP**. This allows training larger models (30B+ parameters) or significantly speeding up training across multiple GPUs.

## Prerequisites

```bash
# DeepSpeed
pip install forgelm[distributed]

# FSDP is built into PyTorch — no extra install needed
```

## Quick Start

### DeepSpeed ZeRO-2 (Recommended Starting Point)

```yaml
# my_config.yaml
distributed:
  strategy: "deepspeed"
  deepspeed_config: "zero2"
```

Launch with `torchrun`:
```bash
torchrun --nproc_per_node=4 -m forgelm.cli --config my_config.yaml
```

Or with `accelerate`:
```bash
accelerate launch --num_processes=4 -m forgelm.cli --config my_config.yaml
```

### FSDP Full Shard

```yaml
distributed:
  strategy: "fsdp"
  fsdp_strategy: "full_shard"
  fsdp_auto_wrap: true
```

```bash
torchrun --nproc_per_node=4 -m forgelm.cli --config my_config.yaml
```

---

## DeepSpeed Configuration

The repository ships three DeepSpeed presets under `configs/deepspeed/`. Set `deepspeed_config` to one of their names, or provide a path to your own JSON file.

**The preset names work only from a source checkout.** They resolve to `configs/deepspeed/<name>.json` beside the
installed package or in the current directory. A `pip install forgelm` wheel and the Docker image do not contain those
files, so there a preset name fails with a configuration error (exit 1). Copy the JSON files from the repository's
`configs/deepspeed/` folder, or write your own, and set `deepspeed_config` to its path.

**Known issue ([#410](https://github.com/HodeTech/ForgeLM/issues/410)):** the `zero2` preset, which is also used when
`deepspeed_config` is unset, sets `allgather_bucket_size` to `"auto"`, a value transformers' DeepSpeed integration does
not fill in, so a ZeRO-2 run with it aborts. Use your own ZeRO-2 JSON until that is fixed.

### Presets

| Preset | ZeRO Stage | Offload | Best For |
|--------|-----------|---------|----------|
| `zero2` | 2 | No | 7B-13B models on 2-4 GPUs |
| `zero3` | 3 | No | 13B-30B models, parameter sharding across GPUs |
| `zero3_offload` | 3 | optimizer state + parameters → CPU | 30B-70B models, limited VRAM, CPU RAM available |

### Usage

```yaml
distributed:
  strategy: "deepspeed"
  deepspeed_config: "./my_ds_config.json"  # Path to your own JSON (works on every install)
  # deepspeed_config: "zero2"       # Preset name: source checkout only
```

### ZeRO Stage Comparison

**ZeRO-2** (Optimizer + Gradient Partitioning):
- Partitions optimizer states and gradients across GPUs
- Each GPU holds a full copy of model parameters
- Good balance of memory savings and communication overhead
- Works well with QLoRA (4-bit)

**ZeRO-3** (Full Parameter Partitioning):
- Partitions everything: optimizer, gradients, AND parameters
- No single GPU needs to hold the full model
- Highest memory savings — can train much larger models
- Higher communication overhead
- **Warning**: Known compatibility issues with QLoRA (4-bit quantization)

**ZeRO-3 + CPU Offload**:
- Same as ZeRO-3, plus offloads optimizer and parameters to CPU RAM
- Maximizes GPU memory savings at the cost of training speed
- Useful when GPU VRAM is the bottleneck but CPU RAM is abundant

### Custom DeepSpeed Config

Create your own JSON file. Use `"auto"` values to let HuggingFace Trainer resolve them from your ForgeLM YAML config:

```json
{
  "zero_optimization": {
    "stage": 2,
    "overlap_comm": true,
    "contiguous_gradients": true
  },
  "train_batch_size": "auto",
  "train_micro_batch_size_per_gpu": "auto",
  "gradient_accumulation_steps": "auto",
  "gradient_clipping": "auto"
}
```

---

## FSDP Configuration

PyTorch Fully Sharded Data Parallel (FSDP) is an alternative to DeepSpeed that's built into PyTorch.

### Strategies

| Strategy | Description | Memory Savings |
|----------|-------------|----------------|
| `full_shard` | Shard parameters, gradients, and optimizer states | Highest |
| `shard_grad_op` | Shard gradients and optimizer states only | Medium |
| `hybrid_shard` | Full shard within nodes, replicate across nodes | Multi-node |
| `no_shard` | Standard DDP (no sharding) | None |

### Usage

```yaml
distributed:
  strategy: "fsdp"
  fsdp_strategy: "full_shard"
  fsdp_auto_wrap: true          # Auto-wrap transformer layers
  fsdp_offload: false           # Offload parameters AND gradients (between forward/backward) to CPU
  fsdp_backward_prefetch: "backward_pre"  # Prefetch strategy
  fsdp_state_dict_type: "FULL_STATE_DICT" # State dict handling
```

### When to Choose FSDP over DeepSpeed

- **FSDP**: Native PyTorch, no extra dependency, simpler setup, good for most use cases
- **DeepSpeed**: More features (ZeRO-Infinity, NVMe offload), better optimization for very large models, more battle-tested for 70B+ parameter training

---

## Compatibility Notes

### QLoRA + Distributed

| Combination | Status | Notes |
|------------|--------|-------|
| QLoRA + ZeRO-2 | Works | Recommended for multi-GPU QLoRA |
| QLoRA + ZeRO-3 | Unstable | Known issues with bitsandbytes + parameter sharding |
| QLoRA + FSDP full_shard | Experimental | May require specific PEFT/FSDP integration flags |
| QLoRA + FSDP shard_grad_op | Works | Similar to ZeRO-2 level sharding |

### Backend Compatibility

| Backend | Multi-GPU | Notes |
|---------|-----------|-------|
| `transformers` | Yes | Full DeepSpeed and FSDP support |
| `unsloth` | No | Single-GPU only — ForgeLM will warn if distributed config is set |

### LoRA + Distributed

LoRA/DoRA adapters work well with both DeepSpeed and FSDP. The adapters are small enough that the overhead of sharding them is minimal, while the frozen base model parameters benefit significantly from sharding.

---

## Multi-Node Training

For training across multiple machines, use `torchrun` with node configuration:

```bash
# Node 0 (master)
torchrun \
  --nproc_per_node=4 \
  --nnodes=2 \
  --node_rank=0 \
  --master_addr=192.168.1.100 \
  --master_port=29500 \
  -m forgelm.cli --config my_config.yaml

# Node 1
torchrun \
  --nproc_per_node=4 \
  --nnodes=2 \
  --node_rank=1 \
  --master_addr=192.168.1.100 \
  --master_port=29500 \
  -m forgelm.cli --config my_config.yaml
```

---

## Docker + Multi-GPU

```bash
docker run --gpus all \
  -v $(pwd)/my_config.yaml:/workspace/config.yaml \
  --shm-size=16g \
  forgelm:full \
  torchrun --nproc_per_node=4 -m forgelm.cli --config /workspace/config.yaml
```

> **Note**: `--shm-size=16g` is important for multi-GPU training. PyTorch uses shared memory for inter-process communication, and the default Docker shared memory (64MB) is insufficient.

---

## Troubleshooting

### "CUDA out of memory"
- Try ZeRO-3 or ZeRO-3 with CPU offload
- Reduce `per_device_train_batch_size`
- Increase `gradient_accumulation_steps` to maintain effective batch size

### "NCCL error" or "timeout"
- Ensure all GPUs are visible: `nvidia-smi`
- Check `NCCL_DEBUG=INFO` environment variable for diagnostics
- Increase timeout: `export NCCL_TIMEOUT=1800`

### "DeepSpeed not found"
```bash
pip install forgelm[distributed]
```

### Slow training with ZeRO-3
- ZeRO-3 has higher communication overhead — this is expected
- Consider ZeRO-2 if the model fits in GPU memory
- Enable `overlap_comm: true` in DeepSpeed config (default in presets)
