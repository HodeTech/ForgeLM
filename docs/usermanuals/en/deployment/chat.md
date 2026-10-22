---
title: Interactive Chat
description: Sanity-check your fine-tuned model in a streaming REPL.
---

# Interactive Chat

`forgelm chat` opens a streaming REPL against any checkpoint — local, merged, or LoRA-adapter. It's the fastest way to sanity-check that fine-tuning produced the model you wanted.

## Quick example

```shell
$ forgelm chat ./checkpoints/customer-support
ForgeLM Chat  —  type your message and press Enter
Streaming : on  |  Temperature : 0.7
Commands  : /help  /reset  /save  /temperature  /system  /exit
────────────────────────────────────────────────────────

> how do I cancel my subscription?
Assistant: You can cancel from Settings → Billing → Cancel subscription. Your
access continues until the end of the current billing period…

> /system You are a polite customer-support agent for a Turkish telecom.
[System prompt updated]

> aboneliği nasıl iptal ederim?
Assistant: Aboneliğinizi iptal etmek için Ayarlar → Faturalandırma → Aboneliği
İptal Et yolunu izleyebilirsiniz...
```

With `rich` installed, the banner is drawn inside a panel. A new system prompt applies to the turns that follow; it does not rewrite the conversation so far.

## Slash commands

| Command | What it does |
|---|---|
| `/reset` | Clear conversation history. |
| `/save [path]` | Save the conversation to JSONL; without a path it writes `chat_history_<timestamp>.jsonl` in the current directory. |
| `/system [prompt]` | Set or update the system prompt; without an argument it shows the current one. |
| `/temperature <value>` | Set sampling temperature: above 0 and up to 2.0 (`0` is rejected). |
| `/help` or `/?` | Show this list. |
| `/exit`, `/quit` or `Ctrl+D` | Exit. |

## Configuration

`forgelm chat` reads no YAML config and has no `chat:` block: it is set with command-line flags and the slash commands above.

| Setting | Flag | Default |
|---|---|---|
| Adapter to merge before chatting | `--adapter DIR` (not supported with `--backend unsloth`) | none |
| Initial system prompt | `--system PROMPT` (change it later with `/system`) | none |
| Sampling temperature | `--temperature` (change it later with `/temperature`) | `0.7` |
| Maximum tokens per reply | `--max-new-tokens` | `512` |
| Streaming output | `--no-stream` turns it off | on |
| Quantized loading (CUDA with bitsandbytes only; ignored otherwise) | `--load-in-4bit` (NF4) or `--load-in-8bit`; with `--backend unsloth`, 4-bit wins if both are given and is the default when neither is | off |
| Model backend | `--backend transformers` or `--backend unsloth` | `transformers` |
| Run code bundled with the model | `--trust-remote-code` | off |

Top-p, top-k and the repetition penalty are fixed (0.9 / 50 / 1.1) and cannot be set. Chat always samples: there is no seed and no greedy mode. There is no history file that persists across sessions; write a conversation out with `/save`. The command-line flag accepts a temperature of `0.0`, but sampling rejects it, so every turn then fails with a `[Generation error: …]` line; use a value above 0.

## Loading a checkpoint

`forgelm chat` accepts:

- A directory with adapter weights (LoRA): `./checkpoints/run/`
- A merged checkpoint directory: `./checkpoints/run/merged/`
- A HuggingFace model ID: `Qwen/Qwen2.5-7B-Instruct`

GGUF files are not loaded by `forgelm chat`: the command loads Hugging Face checkpoints only. To try a GGUF export, run it in Ollama or llama.cpp (see [Deploy Targets](#/deployment/deploy-targets)).

For LoRA checkpoints, pass the base model as the positional argument and the adapter directory via `--adapter`:

```shell
$ forgelm chat "Qwen/Qwen2.5-7B" --adapter ./checkpoints/run/
```

## Safety routing

> Note: A built-in `--safety` flag on `forgelm chat` is planned for a future Pro CLI release (see [Phase 13 roadmap on GitHub](https://github.com/HodeTech/ForgeLM/blob/main/docs/roadmap.md)). The current release does **not** support per-turn safety screening in the chat REPL: `forgelm chat` has no `--safety` flag and loads no YAML config (verifiable in the chat subcommand parser, `forgelm/cli/_parser.py`). The `evaluation.safety.enabled: true` config block is read only by the training pipeline's auto-revert safety gate (`forgelm/trainer.py`) and the standalone `forgelm safety-eval` subcommand — never by the chat REPL. The snippet below previews the planned `--safety` UX; it is NOT a runnable command today.

Under the planned `--safety` flag (preview only — see the note above), every prompt and response would be screened by Llama Guard:

```text
> [adversarial prompt]
[Llama Guard flagged S2 (non-violent crimes) — refusing]
I can't help with that. Try rephrasing the question.
```

Useful when probing the model for jailbreaks before deployment. Off by default for ordinary chat.

## Multi-turn handling

Conversation history is preserved within a session. With every prompt the model receives the system prompt and the last 50 question-and-answer pairs; older pairs stay in memory (and in a `/save` file) but are no longer sent. The limit counts pairs, not tokens:

```text
> what's the capital of Türkiye?
Assistant: Ankara.

> what's its population?
Assistant: About 5.7 million as of recent estimates...
```

The second answer is only possible because the first turn is sent along with it ("its" refers to Ankara). To start fresh: `/reset`.

## Saving and replaying

```shell
> /save sessions/qa-1.jsonl
[History saved → sessions/qa-1.jsonl (7 messages)]
```

The file holds one `{"role": ..., "content": ...}` message per line: the system prompt first (when one is set), then every user and assistant message of the session. The system prompt written is the one in force when you save, even if you changed it mid-conversation. There is no command to load a saved file back into a session (replay tooling is roadmapped for a future Pro CLI release).

Sessions are useful for:
- Reproducing a bug you found during testing.
- Building a benchmark prompt set from interactive exploration.
- Comparing two model versions on the same conversation.

## Comparing two models

> Note: The `chat-compare` subcommand — an interactive, side-by-side chat-prompt comparison with judge adjudication — is planned for a future Pro CLI tier release (see [Phase 13 roadmap on GitHub](https://github.com/HodeTech/ForgeLM/blob/main/docs/roadmap.md)). There is no equivalent today: `forgelm --benchmark-only` (see `forgelm/cli/_no_train_modes.py`) runs lm-evaluation-harness benchmarks on a **single** checkpoint at a time — it does not do side-by-side comparison or judge adjudication. To compare two checkpoints now, run `--benchmark-only` once per checkpoint, run the judge separately, and aggregate the results yourself. The snippet below previews the planned UX.

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
**Treating chat as reproducible.** Chat always samples, and it has no seed and no greedy mode (`/temperature 0` is rejected), so the same prompt can give different answers. Use it to explore; keep a `/save` transcript of what the model actually said, and use the evaluation commands for repeatable checks.
:::

:::warn
**Long conversations exceeding context.** Only the last 50 pairs are sent, but nothing counts tokens: a long system prompt, long answers or a small-context model can still overflow, and the turn then fails with a `[Generation error: …]` line (the turn is not added to the history). Use `/reset` periodically for long testing sessions.
:::

:::tip
For automated probing of many prompts, use `forgelm safety-eval` for batch safety screening, or script a loop that feeds each prompt to `forgelm chat` and collects the output. A dedicated batch-chat subcommand is not available in the current release.
:::

## See also

- [LLM-as-Judge](#/evaluation/judge) — automate the comparison.
- [Llama Guard Safety](#/evaluation/safety) — same Llama Guard model used here.
- [Deploy Targets](#/deployment/deploy-targets) — once you're happy, deploy.
