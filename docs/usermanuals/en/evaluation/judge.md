---
title: LLM-as-Judge
description: Quality scoring on a held-out prompt set using a stronger judge model — single-rubric average score with a configurable minimum.
---

# LLM-as-Judge

Standard benchmarks measure narrow capabilities; they don't capture "is this response actually good?". LLM-as-judge fills that gap by using a stronger model (or a local instruction-tuned LLM) to score the trained model's outputs on a held-out prompt set. ForgeLM's judge is a single-rubric average-score gate — the judge assigns a 1-10 score per (prompt, completion) pair and the run fails if the mean score drops below a configured floor.

## When to use

| Use LLM-as-judge when... | Use benchmarks when... |
|---|---|
| Output quality is subjective (helpful, polite, on-brand). | The task has a verifiable answer. |
| You don't have ground truth. | You have ground truth. |
| You're tracking qualitative regressions across runs. | You're tracking absolute capability. |
| Cost is acceptable (~$1-5 per 1K judgements with GPT-4o). | You need free, local eval. |

## Quick example

```yaml
evaluation:
  llm_judge:                            # block name is `llm_judge`, not `judge`
    enabled: true
    judge_model: "gpt-4o-mini"          # or local path, e.g. "./judges/Qwen2.5-72B-Instruct"
    judge_api_key_env: OPENAI_API_KEY   # null = local model (no API call)
    judge_api_base: null                # override for Azure OpenAI / vLLM-compatible gateway
    eval_dataset: "data/eval-prompts.jsonl"
    min_score: 6.5                      # mean score floor (1-10 scale); revert below this
    batch_size: 8                       # (prompt, completion) pairs scored per round; 1 disables batching
```

## Eval-dataset format

`eval_dataset` is a JSONL file. Each line is a single prompt the judge scores against the trained model's response:

```jsonl
{"prompt": "Explain mitosis to a 10-year-old."}
{"prompt": "Refactor this Python list comprehension into a for-loop: [x*2 for x in nums]"}
```

ForgeLM generates the trained model's completion for each prompt and asks the judge: "Score this response on a 1-10 scale for helpfulness and correctness." The mean across the dataset is the run's `judge_score`.

## Output

`<output_dir>/judge_report.json`:

```json
{
  "judge_model": "gpt-4o-mini",
  "eval_dataset": "data/eval-prompts.jsonl",
  "n_prompts": 200,
  "mean_score": 7.4,
  "min_score_threshold": 6.5,
  "passed": true,
  "per_prompt": [
    {"prompt_id": 0, "score": 8, "explanation": "..."},
    {"prompt_id": 1, "score": 6, "explanation": "..."}
  ]
}
```

When `mean_score < min_score`, the trainer treats it as an evaluation regression: if `auto_revert: true`, the model is reverted and the run exits `3` (`EXIT_EVAL_FAILURE`); with the shipped default `auto_revert: false` the failure is recorded in the audit log and the JSON `judge` block but the model is still promoted and the run exits `0`. Set `auto_revert: true` if a failed judge gate should change the exit code.

## Judge-model choice

| Judge | Cost / 1K judgements | Quality |
|---|---|---|
| `gpt-4o` (set `judge_api_key_env: OPENAI_API_KEY`) | ~$5 | Highest. Default for production. |
| `gpt-4o-mini` | ~$1 | 90% of gpt-4o quality. Recommended cost-balanced default. |
| `claude-haiku-4` (set `judge_api_base: https://api.anthropic.com/v1` + correct env var) | ~$1.50 | Comparable to gpt-4o-mini. |
| Local path (e.g. `./judges/Qwen2.5-72B-Instruct`, `judge_api_key_env: null`) | $0 (your GPU time) | Reasonable; weaker on subtle judgement calls. |

The judge is a single configurable model — there is no built-in pairwise / ELO / multi-criteria rubric pipeline. To do pairwise A/B comparison across two trained models, run two separate trainer invocations against the same eval dataset and compare the resulting `mean_score` values; ForgeLM does not orchestrate the pairwise call internally.

## Cost controls

ForgeLM does not enforce a runtime USD budget. Manage cost externally:

- **Limit `eval_dataset` size.** Each prompt = one judge API call. 200 prompts × $0.005 (gpt-4o-mini) ≈ $1 per run.
- **Use a local judge for iteration.** Pin a 70B-class instruction-tuned model on your own GPU for nightly runs; reserve API judges for the release-gate run.
- **Provider-side rate limiting.** Set throughput caps in your OpenAI/Anthropic dashboard rather than in `forgelm` config.

## Common pitfalls

:::warn
**Using the same model as judge and student.** A 7B model judging another 7B's outputs won't catch subtle quality issues. Use a stronger judge — for a 7B trained model, an instruction-tuned 70B+ or an API-class judge.
:::

:::warn
**Tiny `eval_dataset`.** Scoring 20 prompts is statistical noise. Use 200+ for a meaningful mean score; for a release gate, 1000+ is better.
:::

:::warn
**Forgetting `judge_api_key_env`.** When `judge_model` is an API model name (e.g. `gpt-4o-mini`) and `judge_api_key_env` is unset, ForgeLM falls back to local-model loading and tries to download `gpt-4o-mini` from HF Hub, which fails noisily. Set the env-var name explicitly when the judge is an API.
:::

:::tip
**Pair judge with benchmarks.** A model that wins on judge but regresses on benchmarks is overfitting to whatever the judge prefers. Both signals matter.
:::

## See also

- [Benchmark Integration](#/evaluation/benchmarks) — quantitative eval companion.
- [Synthetic Data](#/data/synthetic-data) — uses a similar `api_base` / `api_key_env` envelope for the teacher model.
- [Auto-Revert](#/evaluation/auto-revert) — judge mean-score is one of the four guard families.
