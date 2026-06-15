---
title: Interactive Chat
description: Sanity-check your fine-tuned model in a streaming REPL.
---

# Interactive Chat

`forgelm chat` opens a streaming REPL against any checkpoint — local, merged, or LoRA-adapter. It's the fastest way to sanity-check that fine-tuning produced the model you wanted.

## Quick example

```shell
$ forgelm chat ./checkpoints/customer-support
ForgeLM 0.5.5 — chat with checkpoints/customer-support
forgelm> how do I cancel my subscription?
You can cancel from Settings → Billing → Cancel subscription. Your access
continues until the end of the current billing period…

forgelm> /system You are a polite customer-support agent for a Turkish telecom.
[system prompt updated]

forgelm> aboneliği nasıl iptal ederim?
Aboneliğinizi iptal etmek için Ayarlar → Faturalandırma → Aboneliği İptal Et
yolunu izleyebilirsiniz...
```

## Slash commands

| Command | What it does |
|---|---|
| `/reset` | Clear conversation history. |
| `/save <path>` | Save the conversation to JSONL. |
| `/system <prompt>` | Set or update the system prompt. |
| `/temperature <value>` | Set sampling temperature (0.0 to 2.0). |
| `/help` | Show this list. |
| `/exit` or `Ctrl+D` | Exit. |

## Configuration

```yaml
chat:
  default_temperature: 0.7
  default_top_p: 0.9
  default_max_tokens: 1024
  default_system_prompt: "You are a helpful assistant."
  history_file: "~/.forgelm/chat-history"     # persisted across sessions
```

## Loading a checkpoint

`forgelm chat` accepts:

- A directory with adapter weights (LoRA): `./checkpoints/run/`
- A merged checkpoint directory: `./checkpoints/run/merged/`
- A HuggingFace model ID: `Qwen/Qwen2.5-7B-Instruct`
- A GGUF file: `./model.gguf` (uses llama.cpp under the hood)

For LoRA checkpoints, pass the base model as the positional argument and the adapter directory via `--adapter`:

```shell
$ forgelm chat "Qwen/Qwen2.5-7B" --adapter ./checkpoints/run/
```

## Safety routing

> Note: A built-in `--safety` flag on `forgelm chat` is planned for v0.6.0+ Pro CLI (see [Phase 13 roadmap on GitHub](https://github.com/HodeTech/ForgeLM/blob/main/docs/roadmap.md)). The current release does **not** support per-turn safety screening in the chat REPL: `forgelm chat` has no `--safety` flag and loads no YAML config (verifiable in the chat subcommand parser, `forgelm/cli/_parser.py`). The `evaluation.safety.enabled: true` config block is read only by the training pipeline's auto-revert safety gate (`forgelm/trainer.py`) and the standalone `forgelm safety-eval` subcommand — never by the chat REPL. The snippet below previews the planned `--safety` UX; it is NOT a runnable command today.

Under the planned `--safety` flag (preview only — see the note above), every prompt and response would be screened by Llama Guard:

```text
forgelm> [adversarial prompt]
[Llama Guard flagged S2 (non-violent crimes) — refusing]
I can't help with that. Try rephrasing the question.
```

Useful when probing the model for jailbreaks before deployment. Off by default for ordinary chat.

## Multi-turn handling

Conversation history is preserved within a session. The model sees the full chat (or as much as fits in `max_length`):

```text
forgelm> what's the capital of Türkiye?
Ankara.

forgelm> what's its population?
[uses prior context: "its" refers to Ankara]
About 5.7 million as of recent estimates...
```

To start fresh: `/reset`.

## Saving and replaying

```shell
forgelm> /save sessions/qa-1.jsonl
[saved 6 turns to sessions/qa-1.jsonl]
```

The saved JSONL is replayable by feeding it back through a fresh
session's prompt history (replay tooling is roadmapped for v0.6.0+ Pro CLI).

Sessions are useful for:
- Reproducing a bug you found during testing.
- Building a benchmark prompt set from interactive exploration.
- Comparing two model versions on the same conversation.

## Comparing two models

> Note: The `chat-compare` subcommand — an interactive, side-by-side chat-prompt comparison with judge adjudication — is planned for v0.6.0+ Pro CLI tier (see [Phase 13 roadmap on GitHub](https://github.com/HodeTech/ForgeLM/blob/main/docs/roadmap.md)). There is no equivalent today: `forgelm --benchmark-only` (see `forgelm/cli/_no_train_modes.py`) runs lm-evaluation-harness benchmarks on a **single** checkpoint at a time — it does not do side-by-side comparison or judge adjudication. To compare two checkpoints now, run `--benchmark-only` once per checkpoint, run the judge separately, and aggregate the results yourself. The snippet below previews the planned UX.

```shell
$ forgelm chat-compare ./checkpoints/v1 ./checkpoints/v2 --prompts data/probes.jsonl
                          v1 (helpful)    v2 (helpful)    judge winner
"How do I cancel..."     ✓ "Settings →"   ✓ "Settings →"  tie
"Reset password?"        ✓ "I can help"   ✓ "Click 'Forgot'"  v2
"Refund policy?"         ✗ vague          ✓ specific      v2
                                                          ───────
Win rate v2 vs v1: 0.62 (sig p=0.04)
```

## Common pitfalls

:::warn
**Treating `/temperature 0` as deterministic.** It's near-deterministic, but tiebreaks in argmax sampling can still produce minor variance. For exact reproducibility, set `seed:` in YAML.
:::

:::warn
**Long conversations exceeding context.** Once history exceeds `max_length`, ForgeLM drops oldest messages first. The conversation can lose continuity unexpectedly. Use `/reset` periodically for long testing sessions.
:::

:::tip
For automated probing of many prompts, use `forgelm batch-chat --prompts data/probes.jsonl --output responses.jsonl` instead of the interactive REPL. Same model, no manual typing.
:::

## See also

- [LLM-as-Judge](#/evaluation/judge) — automate the comparison.
- [Llama Guard Safety](#/evaluation/safety) — same Llama Guard model used here.
- [Deploy Targets](#/deployment/deploy-targets) — once you're happy, deploy.
