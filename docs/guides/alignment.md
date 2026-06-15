# Alignment & Post-Training Guide

ForgeLM supports the complete modern post-training stack: SFT → Preference Optimization → Reasoning RL. This guide explains when and how to use each method.

---

## Method Overview

| Method | `trainer_type` | Dataset Format | When to Use |
|--------|---------------|----------------|-------------|
| **SFT** | `"sft"` | System/User/Assistant or `messages` | Instruction tuning — teach the model *what* to say |
| **DPO** | `"dpo"` | `chosen` / `rejected` pairs | Preference alignment — teach *how* to say it better |
| **SimPO** | `"simpo"` | `chosen` / `rejected` pairs | Like DPO but no reference model (lower memory) |
| **KTO** | `"kto"` | `completion` + `label` (bool) | Binary feedback — only thumbs up/down available |
| **ORPO** | `"orpo"` | `chosen` / `rejected` pairs | SFT + alignment in one stage |
| **GRPO** | `"grpo"` | `prompt` only | Reasoning RL — model generates and self-improves |

---

## The Modern Post-Training Stack

Most production LLMs in 2026 follow this pipeline:

```
Base Model
    ↓
[Stage 1] SFT — instruction tuning on curated data
    ↓
[Stage 2] DPO/SimPO/KTO — preference alignment
    ↓
[Stage 3] GRPO — reasoning RL (optional, for math/code)
    ↓
Production Model
```

ForgeLM handles each stage as a separate `forgelm` run with different configs.

---

## Stage 1: Supervised Fine-Tuning (SFT)

**Goal:** Teach the model to follow instructions in your domain.

### Dataset Format

```json
{"System": "You are a legal assistant.", "User": "What is a tort?", "Assistant": "A tort is a civil wrong..."}
```

Or the modern `messages` format:

```json
{"messages": [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]}
```

### Config

```yaml
model:
  name_or_path: "meta-llama/Llama-3.1-8B-Instruct"
  load_in_4bit: true

lora:
  r: 16
  alpha: 32
  target_modules: ["q_proj", "k_proj", "v_proj", "o_proj"]

training:
  trainer_type: "sft"
  num_train_epochs: 3
  learning_rate: 2.0e-5
  per_device_train_batch_size: 4

data:
  dataset_name_or_path: "./data/sft_data.jsonl"
```

```bash
forgelm --config sft_config.yaml
```

---

## Stage 2: Preference Alignment

After SFT, align the model's responses with human preferences. Choose based on your data:

### DPO — Direct Preference Optimization

**Best for:** You have paired preference data (chosen vs rejected responses).

```json
{"prompt": "Explain recursion", "chosen": "Recursion is a technique where...", "rejected": "Recursion means doing something again..."}
```

```yaml
training:
  trainer_type: "dpo"
  dpo_beta: 0.1          # Temperature — lower = stronger preference signal
  learning_rate: 5.0e-6   # Lower LR than SFT
  num_train_epochs: 1     # 1-2 epochs usually sufficient

data:
  dataset_name_or_path: "./data/preferences.jsonl"
```

### SimPO — Simple Preference Optimization

**Best for:** Same data as DPO, but you want lower memory (no reference model needed).

SimPO outperforms DPO at 7B+ scale (+6.4 points on AlpacaEval 2).

```yaml
training:
  trainer_type: "simpo"
  simpo_beta: 2.0         # Scaling parameter
  simpo_gamma: 0.5        # Margin term
  learning_rate: 5.0e-6
```

### KTO — Kahneman-Tversky Optimization

**Best for:** You only have binary feedback (thumbs up/down), not paired preferences. More practical for production data collection.

```json
{"prompt": "What is Python?", "completion": "Python is a programming language...", "label": true}
{"prompt": "What is Python?", "completion": "Python is a snake.", "label": false}
```

```yaml
training:
  trainer_type: "kto"
  kto_beta: 0.1
  learning_rate: 5.0e-6

data:
  dataset_name_or_path: "./data/kto_feedback.jsonl"
```

### ORPO — Single-Stage SFT + Alignment

**Best for:** You want to combine SFT and alignment in one training run. Uses chosen/rejected data but also learns from the instruction format.

```yaml
training:
  trainer_type: "orpo"
  orpo_beta: 0.1
```

---

## Stage 3: Reasoning RL (GRPO)

**Best for:** Math, code, reasoning tasks where outputs can be verified. This is the method behind DeepSeek-R1.

GRPO generates multiple responses per prompt, scores them, and reinforces better ones — no human preference data needed.

```json
{"prompt": "Solve: What is 15% of 240?", "gold_answer": "36"}
```

```yaml
training:
  trainer_type: "grpo"
  grpo_num_generations: 4              # Generate 4 responses per prompt
  grpo_max_completion_length: 512      # Max tokens per completion (legacy alias `grpo_max_new_tokens` still accepted)
  grpo_reward_model: null    # See "Reward selection" below.
  learning_rate: 1.0e-6      # Very low LR for RL stability
  num_train_epochs: 1

data:
  dataset_name_or_path: "./data/math_prompts.jsonl"
```

### Reward selection

GRPO needs a reward signal. ForgeLM wires reward callables additively (TRL sums multiple reward funcs into a single scalar):

1. **`grpo_reward_model` set** — Loads the HF sequence-classification model at that path and uses its scalar output as the only reward signal. The built-in rewards below are bypassed; the operator opted into a learned reward.
2. **No `grpo_reward_model`** — A baseline reward is always wired:
   - **`combined_format_length_reward`** (`forgelm/grpo_rewards.py`) — `0.8 × format_match + 0.2 × length_shaping`. The format component returns 1.0 when the generation ends with `Answer: <value>` (case-insensitive, units allowed); the length component returns `min(len(completion) / 200, 1.0)` so early training has a non-flat gradient even before format compliance kicks in.
   - **`_math_reward_fn`** (`forgelm/trainer.py`) — appended only when the dataset has a `gold_answer` field. Captures the value after the **last** `Answer:` marker (so a self-correcting generation is graded on the answer it actually concludes with, consistent with the end-anchored format reward), strips common units (`$`, `%`, `km/h`, `m²`, `liters`, …), and compares to `gold_answer` with exact-string match first, then numeric tolerance (1e-6). Returns `1.0` for a correct answer, `0.0` otherwise.

The bundled `forgelm quickstart grpo-math` template ships with `gold_answer` populated, so the model gets both format teaching AND correctness teaching out of the box. To use a real reward model on top of grpo-math, set `grpo_reward_model` and the built-in rewards are bypassed.

For your own dataset: the format+length baseline applies regardless. Add a `gold_answer` field per row to also get the correctness signal — the prompt's expected output format is `Answer: <value>` (with optional units that get stripped).

> **Note:** GRPO requires a reward function or verifiable reward. For math, correctness of the answer is the reward. For general text, you may need a reward model.

---

## Choosing the Right Method

```
Do you have paired preferences (chosen/rejected)?
├── Yes → Is memory a concern?
│   ├── Yes → SimPO
│   └── No → DPO
├── No → Do you have binary feedback (good/bad)?
│   ├── Yes → KTO
│   └── No → Do you have verifiable rewards (math/code)?
│       ├── Yes → GRPO
│       └── No → Just use SFT
```

ForgeLM's `--wizard` mode helps you choose:
```bash
forgelm --wizard
# Step 4 asks: "Choose your training objective"
# Shows format requirements for each method
```

---

## Multi-Stage Pipeline Example

```bash
# Stage 1: SFT
forgelm --config configs/stage1_sft.yaml

# Stage 2: DPO (uses the SFT model as base)
# In stage2_dpo.yaml, set:
#   model.name_or_path: "./checkpoints_sft/final_model"
forgelm --config configs/stage2_dpo.yaml

# Stage 3: GRPO (uses the DPO model as base)
forgelm --config configs/stage3_grpo.yaml
```

> **Planned (Phase 14 — pipeline chains):** A `pipeline:` config key will define multi-stage training chains in a single YAML file, eliminating manual config juggling between stages. Tracking issue links the v0.6.0+ release window.

---

## Tips

- **Learning rate**: SFT uses 1e-5 to 3e-5. Alignment methods use 5e-7 to 5e-6. GRPO uses 1e-6 or lower.
- **Epochs**: SFT typically needs 2-3 epochs. Alignment methods usually need 1-2 epochs. More is not better.
- **Data quality > data quantity**: 1,000 high-quality preference pairs often outperform 50,000 noisy ones.
- **Always evaluate**: Use `auto_revert: true` with `max_acceptable_loss` to catch quality regressions.
- **Scale matters**: Research (arxiv 2603.19335) shows algorithm rankings are scale-dependent — SimPO is best at 7B but DPO may be better at 1.5B.
