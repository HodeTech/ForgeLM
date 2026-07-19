---
title: Dataset Formats
description: JSONL formats for SFT, DPO, SimPO, KTO, ORPO, and GRPO — what each trainer expects.
---

# Dataset Formats

Every ForgeLM trainer expects a specific JSONL format. ForgeLM auto-detects the format from your file's first row, so you don't have to declare it explicitly — but you do have to produce the right shape.

## Quick reference

| Format | Used by | Required fields |
|---|---|---|
| `instructions` | SFT | `prompt`, `completion` |
| `messages` | SFT (multi-turn) | `messages: [{role, content}, …]` |
| `preference` | DPO, SimPO, ORPO | `prompt`, `chosen`, `rejected` |
| `binary` | KTO | `prompt`, `completion`, `label` |
| `reward` | GRPO | `prompt` (response generated at training time) |

## Instructions (single-turn SFT)

The simplest format — one prompt, one completion per row.

```json
{"prompt": "What is the capital of France?", "completion": "Paris."}
{"prompt": "Translate 'hello' to Turkish.", "completion": "Merhaba."}
```

Optional fields:
- `system` — system prompt prepended to the conversation.
- `metadata` — arbitrary dict; preserved in audit logs but not used at training time.

## Messages (multi-turn SFT)

The native HuggingFace chat format. Use this when conversations span multiple turns.

```json
{"messages": [
  {"role": "system", "content": "You are a polite customer-support agent."},
  {"role": "user", "content": "How do I cancel my subscription?"},
  {"role": "assistant", "content": "From Settings → Billing → Cancel subscription…"},
  {"role": "user", "content": "Will I be charged again?"},
  {"role": "assistant", "content": "No. Your access continues until the end of the billing period."}
]}
```

Roles: `system`, `user`, `assistant`. Tool-call roles (`tool`, `function`) are also supported when the chat template defines them.

:::tip
The chat template applied at training time comes from the model's tokeniser. ForgeLM uses `tokenizer.apply_chat_template()` so a model trained on Llama 3 chat format will be served correctly by Llama 3 chat clients without you doing anything special.
:::

## Preference (DPO / SimPO / ORPO)

Each row is a triplet: a prompt, a preferred response, a dispreferred response.

```json
{
  "prompt": "How do I cancel my subscription?",
  "chosen": "From Settings → Billing → Cancel subscription. Your access continues until the end of the billing period.",
  "rejected": "Just stop paying lol."
}
```

Optional:
- `system` — system prompt for both responses.
- `prompt_messages` — multi-turn prompt as an array (rare; use this when the prompt is itself a conversation).

The audit (`forgelm audit`) flags rows where `chosen == rejected` — a common bug in preference-collection pipelines.

## Binary (KTO)

Single response with a thumbs-up/down label. Simpler to collect than paired preferences when you have user feedback streams.

```json
{
  "prompt": "How do I cancel my subscription?",
  "completion": "Just stop paying lol.",
  "label": false
}
{
  "prompt": "How do I cancel my subscription?",
  "completion": "From Settings → Billing → Cancel subscription…",
  "label": true
}
```

Field meanings:
- `completion` → the model's response (the field name TRL's KTOTrainer expects; ForgeLM's `_detect_dataset_format` keys off `completion + label` to auto-route to KTO).
- `label: true` → desirable response (thumbs-up)
- `label: false` → undesirable response (thumbs-down)

KTO needs both classes — at minimum 5-10% of your data should be the minority class for stable training.

## Reward (GRPO)

GRPO doesn't ship completions — it generates them at training time. You provide prompts, ForgeLM samples responses, scores them with your reward function, and updates the policy.

```json
{"prompt": "Solve: 17 × 23 = ?", "ground_truth": "391"}
{"prompt": "Solve: 144 ÷ 12 = ?", "ground_truth": "12"}
```

The `ground_truth` field is opaque to ForgeLM — it's passed to your reward function:

```python
# my_reward.py
def reward(prompt: str, response: str, ground_truth: str) -> float:
    """Return a scalar in [-1, 1] (or any bounded range)."""
    answer = extract_number(response)  # your parsing logic
    if answer is None:
        return -0.5  # malformed
    return 1.0 if answer == int(ground_truth) else -1.0
```

In your YAML:

```yaml
training:
  trainer_type: "grpo"
  grpo_reward_model: "my_reward.reward"
```

There is no nested `training.grpo:` sub-block — `grpo_reward_model` (and the other `grpo_*` knobs) are flat fields directly on `training:`. See [GRPO](#/training/grpo) for the built-in format/length shaping rewards.

## Multi-dataset mixing

You can train on a mix of datasets with custom proportions via `data.extra_datasets` + `data.mix_ratio` — there is no top-level `datasets:` list:

```yaml
data:
  dataset_name_or_path: "data/policies.jsonl"     # primary dataset
  extra_datasets:
    - "data/general-qa.jsonl"
  mix_ratio: [0.7, 0.3]                           # one weight per dataset, primary first
```

Weights are per-dataset (not required to sum to 1.0 — they're normalised internally); each batch is sampled according to those proportions. See the [Configuration Reference `data:`](#/reference/configuration) section for the full field list.

## Auto-detection

If you don't specify `format:`, ForgeLM inspects the first non-empty row:

| Row contains | Detected as |
|---|---|
| `messages` array | `messages` |
| `chosen` and `rejected` | `preference` |
| `completion` and `label` (bool) | `binary` |
| `prompt` and `completion` | `instructions` |
| `prompt` only | `reward` |

:::warn
Auto-detection happens once per file. If your JSONL mixes formats (some `instructions` rows alongside `preference` rows), the loader will misroute the second-format rows. Use separate files and reference both via `data.dataset_name_or_path` + `data.extra_datasets`.
:::

## Validating your data

Always run `forgelm audit` before training:

```shell
$ forgelm audit data/preferences.jsonl
✓ format: preference (12,400 rows, 3 splits)
⚠ PII detected: 5 medium severity (see report)
⚠ 12 chosen-rejected identical rows — likely collection bug
✓ no cross-split leakage
```

See [Dataset Audit](#/data/audit) for full audit semantics.

## See also

- [Document Ingestion](#/data/ingestion) — convert PDF/DOCX/EPUB/Markdown into these JSONL formats.
- [Dataset Audit](#/data/audit) — run before training.
- [Choosing a Trainer](#/concepts/choosing-trainer) — match your data to the right trainer.
