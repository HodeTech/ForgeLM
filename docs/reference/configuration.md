# Configuration Guide

ForgeLM uses YAML files for all configuration — declarative, version-controllable, and CI/CD-ready.

See `config_template.yaml` for a complete annotated example.

---

## `model`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name_or_path` | string | *required* | HuggingFace model ID or local path |
| `max_length` | int | `2048` | Maximum context length |
| `load_in_4bit` | bool | `true` | Enable QLoRA 4-bit NF4 quantization |
| `backend` | string | `"transformers"` | `"transformers"` or `"unsloth"` (2-5x faster, Linux only) |
| `trust_remote_code` | bool | `false` | Allow custom code from model repos. **Security risk** — only enable for models that require it |
| `offline` | bool | `false` | Air-gapped mode: no HF Hub calls. Models/datasets must be local |
| `revision` | string | `null` | Pin the base model + tokenizer to an HF Hub commit SHA (40-hex) or a branch/tag. **Honoured today.** See [Hub revision pinning](#hub-revision-pinning) |
| `bnb_4bit_use_double_quant` | bool | `true` | Double quantization for extra VRAM savings |
| `bnb_4bit_quant_type` | string | `"nf4"` | Quantization type (`"nf4"` or `"fp4"`) |
| `bnb_4bit_compute_dtype` | string | `"auto"` | Compute dtype: `"auto"`, `"bfloat16"`, `"float16"`, `"float32"` (each of the last three also accepts the short alias `"bf16"`, `"fp16"`, `"fp32"`) |

#### `model.moe` (Optional — MoE models)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `quantize_experts` | bool | `false` | Quantize inactive expert weights to int8 for VRAM savings |
| `experts_to_train` | string | `"all"` | `"all"` or comma-separated expert indices (e.g., `"0,1,2"`) |

#### `model.multimodal` (Optional — VLM models)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable vision-language model fine-tuning |
| `image_column` | string | `"image"` | Column name for image paths/URLs in dataset |
| `text_column` | string | `"text"` | Column name for text/captions |

---

## `lora`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `r` | int | `8` | LoRA rank. Higher = more parameters |
| `alpha` | int | `16` | LoRA scaling factor |
| `dropout` | float | `0.1` | Dropout probability |
| `bias` | string | `"none"` | `"none"`, `"all"`, or `"lora_only"` |
| `method` | string | `"lora"` | PEFT method: `"lora"`, `"dora"`, `"pissa"`, `"rslora"` |
| `use_dora` | bool | `false` | **Deprecated** boolean shortcut for `method: "dora"`; will be removed in v1.0.0. Setting `true` forwards to `method: "dora"` with a `DeprecationWarning`. Use `method` instead. |
| `use_rslora` | bool | `false` | **Deprecated** boolean shortcut for `method: "rslora"` (recommended for r>64); will be removed in v1.0.0. Setting `true` forwards to `method: "rslora"` with a `DeprecationWarning`. Use `method` instead. |
| `target_modules` | list | `["q_proj", "v_proj"]` | Model modules to apply LoRA |
| `task_type` | string | `"CAUSAL_LM"` | Task type for PEFT |

> `use_dora` and `use_rslora` are mutually exclusive, and each conflicts with an explicitly-set `method` that names a different PEFT method (e.g. `use_dora: true` with `method: "rslora"`) — either combination raises `ConfigError` (exit 1) at config-load time. Set `method` directly instead of the deprecated boolean flags.
>
> Removal will land in v1.0.0, not sooner, because removing a YAML field is a
> MAJOR change under the versioning policy — see
> [`docs/standards/release.md`](../standards/release.md#what-constitutes-breaking).

---

## `training`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `output_dir` | string | `"./checkpoints"` | Checkpoint save directory |
| `final_model_dir` | string | `"final_model"` | Subdirectory for final artifacts |
| `merge_adapters` | bool | `false` | Merge adapters into base model before saving |
| `trainer_type` | string | `"sft"` | `"sft"`, `"dpo"`, `"simpo"`, `"kto"`, `"orpo"`, `"grpo"` |
| `max_steps` | int | `-1` | Hard step cap. `-1` = use `num_train_epochs`; a positive value overrides epochs. |
| `num_train_epochs` | int | `3` | Number of training epochs (only consulted when `max_steps == -1`). |
| `per_device_train_batch_size` | int | `4` | Batch size per GPU |
| `gradient_accumulation_steps` | int | `2` | Steps to accumulate before backward pass |
| `learning_rate` | float | `2e-5` | Learning rate (lower for alignment: 5e-6) |
| `warmup_ratio` | float | `0.1` | Warmup proportion |
| `weight_decay` | float | `0.01` | AdamW weight decay |
| `eval_steps` | int | `200` | Evaluate every N steps |
| `save_steps` | int | `200` | Save checkpoint every N steps |
| `save_total_limit` | int | `3` | Max checkpoints to keep |
| `early_stopping_patience` | int | `3` | Stop after N evals without validation-loss improvement (active only when a validation split exists). |
| `packing` | bool | `false` | Sequence packing (SFT only) |
| `report_to` | string | `"tensorboard"` | `"tensorboard"`, `"wandb"`, `"mlflow"`, `"none"` |
| `run_name` | string | `null` | W&B/MLflow run name (auto-generated if null) |

#### OOM Recovery

Automatically halves `per_device_train_batch_size` and doubles `gradient_accumulation_steps`
on CUDA out-of-memory errors, preserving the effective batch size. Retries until the minimum
batch size is reached.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `oom_recovery` | bool | `false` | Retry training with smaller batch size on CUDA OOM |
| `oom_recovery_min_batch_size` | int | `1` | Stop retrying when batch size reaches this value |

**Example:**

```yaml
training:
  per_device_train_batch_size: 8
  gradient_accumulation_steps: 2
  oom_recovery: true
  oom_recovery_min_batch_size: 1  # try down to batch_size=1 before failing
```

Effective batch size (`per_device_train_batch_size × gradient_accumulation_steps`) is preserved
across retries. Each retry attempt is logged to the audit trail.

#### GaLore (Optimizer-Level Memory Optimization)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `galore_enabled` | bool | `false` | Enable GaLore gradient low-rank projection |
| `galore_optim` | string | `"galore_adamw"` | GaLore optimizer variant. One of: `"galore_adamw"`, `"galore_adamw_8bit"`, `"galore_adafactor"`, `"galore_adamw_layerwise"`, `"galore_adamw_8bit_layerwise"`, `"galore_adafactor_layerwise"`. `_8bit` halves optimizer-state VRAM; `_layerwise` cuts peak VRAM by recomputing per-layer. |
| `galore_rank` | int | `128` | Rank for gradient projection |
| `galore_update_proj_gap` | int | `200` | Steps between projection updates |
| `galore_scale` | float | `0.25` | GaLore scaling factor |
| `galore_proj_type` | string | `"std"` | Projection type: `"std"`, `"reverse_std"`, `"right"`, `"left"`, `"full"` |
| `galore_target_modules` | `Optional[List[str]]` | `null` | Module-name regex patterns GaLore is applied to. `null` falls back to `[r".*.attn.*", r".*.mlp.*"]` (attention + MLP layers). |

#### Long-Context Training

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `rope_scaling` | `Optional[Dict[str, Any]]` | `null` | RoPE scaling method dict (`{"type": "linear", "factor": 2.0}` etc.). Supported types: `"linear"`, `"dynamic"`, `"yarn"`, `"longrope"`. |
| `neftune_noise_alpha` | float | `null` | NEFTune noise injection alpha (e.g., `5.0`) |
| `sliding_window_attention` | int | `null` | Sliding window attention size in tokens |
| `sample_packing` | bool | `false` | **Deprecated** alias for `packing` (TRL exposes a single packing knob). Setting `true` forwards to `packing: true` with a `DeprecationWarning`; will be removed in v1.0.0. Use `packing` instead. |

> Removal will land in v1.0.0, not sooner, because removing a YAML field is a
> MAJOR change under the versioning policy — see
> [`docs/standards/release.md`](../standards/release.md#what-constitutes-breaking).

#### GPU Cost Estimation

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `gpu_cost_per_hour` | float | `null` | Custom GPU cost rate (USD/hour). Auto-detected from GPU model if null |

#### Alignment Parameters

| Field | Type | Default | Used By |
|-------|------|---------|---------|
| `dpo_beta` | float | `0.1` | DPO temperature |
| `simpo_gamma` | float | `0.5` | SimPO margin term |
| `simpo_beta` | float | `2.0` | SimPO scaling |
| `kto_beta` | float | `0.1` | KTO loss parameter |
| `orpo_beta` | float | `0.1` | ORPO odds ratio weight |
| `grpo_num_generations` | int | `4` | GRPO: responses per prompt |
| `grpo_max_completion_length` | int | `512` | GRPO: max tokens per completion (legacy alias `grpo_max_new_tokens` accepted) |
| `grpo_reward_model` | string | `null` | GRPO: reward model path (HF or local) |
| `grpo_reward_model_revision` | string | `null` | Pin the GRPO reward model to an HF Hub commit SHA or ref. Rejected without `grpo_reward_model`. **Honoured today** — pins the reward tokenizer and the sequence-classification model at the same commit. See [Hub revision pinning](#hub-revision-pinning) |

---

## `data`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `dataset_name_or_path` | string | *required* | HF dataset ID or local JSONL path |
| `extra_datasets` | list | `null` | Additional datasets to mix in |
| `mix_ratio` | list | `null` | Weight per dataset (e.g., `[0.7, 0.3]`) |
| `shuffle` | bool | `true` | Shuffle training data |
| `clean_text` | bool | `true` | Strip extra whitespace |
| `add_eos` | bool | `true` | Add EOS token to sequences |

#### `data.governance` (Optional — EU AI Act Art. 10)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `collection_method` | string | `""` | How data was collected |
| `annotation_process` | string | `""` | Annotation methodology |
| `known_biases` | string | `""` | Known dataset biases |
| `personal_data_included` | bool | `false` | Contains personal data |
| `dpia_completed` | bool | `false` | Data Protection Impact Assessment done |

---

## `evaluation` (Optional)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `auto_revert` | bool | `false` | Delete model if evaluation fails |
| `max_acceptable_loss` | float | `null` | Hard ceiling for eval_loss |
| `baseline_loss` | float | `null` | Computed automatically if null |
| `require_human_approval` | bool | `false` | Pause for human review (exit code 4) |

#### `evaluation.benchmark` (Optional)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable lm-eval-harness benchmarks |
| `tasks` | list | `[]` | Task names (e.g., `["arc_easy", "hellaswag"]`) |
| `num_fewshot` | int | `null` | Few-shot examples (task default) |
| `batch_size` | string | `"auto"` | Evaluation batch size |
| `limit` | int | `null` | Samples per task (for quick checks) |
| `output_dir` | string | `null` | Where to write the benchmark results JSON. `null` = the training `output_dir`. |
| `min_score` | float | `null` | Minimum average accuracy |

> `enabled: true` requires at least one entry in `tasks` — an enabled benchmark gate with no tasks is rejected at config-load time.

#### `evaluation.safety` (Optional)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable safety classifier evaluation |
| `classifier` | string | `"meta-llama/Llama-Guard-3-8B"` | Safety classifier model. The shipped default works out of the box: under `classifier_mode: auto` it is scored via generation-based Llama-Guard scoring |
| `classifier_mode` | string | `"auto"` | How the classifier is scored: `auto` (generation for a known generative Llama-Guard checkpoint, `text-classification` otherwise), `classification` (force the pipeline — needs a trained `safe`/`unsafe` head), or `generation` (force generation-based Llama-Guard scoring) |
| `classifier_revision` | string | `null` | Pin the harm classifier to an HF Hub commit SHA or ref. **Honoured today by the training-loop safety gate** — pins the classifier tokenizer and weights at the same commit. Standalone `forgelm safety-eval` takes no config and still loads its classifier unpinned. See [Hub revision pinning](#hub-revision-pinning) |
| `test_prompts` | string | `"safety_prompts.jsonl"` | Adversarial test prompts file. Built-in sets in `configs/safety_prompts/` |
| `max_safety_regression` | float | `0.05` | Max allowed unsafe ratio (binary gate) |
| `scoring` | string | `"binary"` | Scoring mode: `"binary"` or `"confidence_weighted"` |
| `min_safety_score` | float | `null` | Weighted score threshold (0.0-1.0). Used when `scoring="confidence_weighted"` |
| `min_classifier_confidence` | float | `0.7` | Flag responses below this confidence for manual review |
| `track_categories` | bool | `false` | Parse Llama Guard S1-S14 harm categories |
| `severity_thresholds` | dict | `null` | Per-severity limits: `{"critical": 0, "high": 0.01, "medium": 0.05}` |
| `batch_size` | int | `8` | Batched generation size for safety evaluation. `1` disables batching; raise for throughput on large VRAM, lower to reduce OOM risk on small VRAM. |
| `include_eval_samples` | bool | `false` | Persist raw `prompt` / `response` strings to `safety_results.json`. **Off by default** for GDPR / EU AI Act Art. 10 privacy — adversarial prompts and responses may surface sensitive content. Opt in only for debugging. |

#### `evaluation.llm_judge` (Optional)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable LLM-as-Judge scoring |
| `judge_model` | string | `"gpt-4o"` | Judge model (API or local path) |
| `judge_api_key_env` | string | `null` | Env var name for API key (null = local) |
| `judge_api_base` | string | `null` | Override the judge API base URL (Azure OpenAI, self-hosted vLLM, OpenAI-compatible gateway, e.g. `https://api.together.xyz/v1`). When unset, the SDK default endpoint is used. |
| `judge_model_revision` | string | `null` | Pin a **local** judge model to an HF Hub commit SHA or ref. Rejected alongside `judge_api_key_env` (the API judge loads nothing). **Honoured today** — pins the judge tokenizer and weights at the same commit. See [Hub revision pinning](#hub-revision-pinning) |
| `eval_dataset` | string | `"eval_prompts.jsonl"` | Evaluation prompts file |
| `min_score` | float | `5.0` | Minimum average score (1-10) |
| `batch_size` | int | `8` | Number of (prompt, completion) pairs scored per LLM-judge round. `1` disables batching. |
| `include_eval_samples` | bool | `false` | Persist raw eval `prompt`, `response`, and judge `reason` strings to `judge_results.json`. **Off by default** for GDPR / EU AI Act Art. 10 privacy — judge reasoning can quote PII from the eval set. Opt in only for debugging. |

> **Judge input truncation:** when building each scoring prompt the judge
> sees at most the first **500 characters of the eval prompt** and the first
> **1000 characters of the model response**. This keeps the judge prompt
> bounded (and the API path cheap); it is below a typical `max_new_tokens`
> generation budget, so very long answers are judged on a leading fragment.
> ForgeLM logs a one-time `WARNING` when a row is actually trimmed. The limits
> are fixed (not yet config-driven) — keep this in mind when tuning `min_score`
> for long-form fine-tunes.
>
> **Removed:** `evaluation.staging_ttl_days` was superseded by
> [`retention.staging_ttl_days`](#retention-optional--gdpr-article-17-erasure-horizons)
> and was removed in v0.8.0. Use `retention.staging_ttl_days`; YAML files that
> still set the legacy key will fail config-load with `EXIT_CONFIG_ERROR`.

---

## `retention` (Optional — GDPR Article 17 erasure horizons)

Defines maximum retention horizons for compliance, training, and evaluation
artefacts. Horizons honour GDPR Article 5(1)(e) "storage limitation" and
Article 17 "right to erasure" deadlines. The `enforce` knob switches between
log-only, warning, and hard-block modes so a regulated CI gate cannot
silently extend the retention horizon by re-using a stale workspace.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `audit_log_retention_days` | int | `1825` (~5 years) | Days to retain `audit_log.jsonl` before flagging it as overdue under Article 5(1)(e). Set to `0` to retain indefinitely (Article 17(3)(b) defence). |
| `staging_ttl_days` | int | `7` | Days to retain `final_model.staging.<run_id>/` after a `forgelm reject` decision before scheduled cleanup. Set to `0` to retain indefinitely. Replaces the removed `evaluation.staging_ttl_days` (removed in v0.8.0). |
| `ephemeral_artefact_retention_days` | int | `90` | Days to retain compliance bundles, data audit reports, and other run-scoped derived artefacts. Set to `0` to retain indefinitely. |
| `raw_documents_retention_days` | int | `90` | Days to retain ingested raw documents (PDF / DOCX / EPUB / TXT / Markdown) under the operator's ingestion-output directory. Set to `0` to retain indefinitely. |
| `enforce` | string | `"log_only"` | Policy enforcement mode: `"log_only"` (audit-log only), `"warn_on_excess"` (structured stderr warning), `"block_on_excess"` (abort trainer pre-flight with `EXIT_EVAL_FAILURE` = 3). |

> **Removed:** `evaluation.staging_ttl_days` (deprecated as of v0.5.5) was
> removed in v0.8.0. `retention.staging_ttl_days` is now the only accepted form.
> YAML files that still set the legacy key will fail config-load with `EXIT_CONFIG_ERROR`.

---

## `webhook` (Optional)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `url` | string | `null` | Webhook destination URL |
| `url_env` | string | `null` | Env var name containing URL |
| `notify_on_start` | bool | `true` | Notify on training start |
| `notify_on_success` | bool | `true` | Notify on success |
| `notify_on_failure` | bool | `true` | Notify on failure |
| `timeout` | int | `10` | HTTP request timeout (seconds). Clamped to ≥ 1s by the notifier. Default raised to 10s in v0.5.5 (was 5s) — Slack/Teams gateway latency spikes regularly cross 5s in production, and a webhook timeout silently degrades the audit chain (webhook failure is best-effort). |
| `allow_private_destinations` | bool | `false` | Opt in to webhooks pointing at RFC1918 / loopback / link-local hosts (in-cluster Slack proxy, on-prem Teams gateway). Defaults to public-internet only — SSRF guard |
| `require_https` | bool | `false` | TLS-only enforcement. `true` refuses a plaintext `http://` URL (the SSRF chokepoint raises; the POST is skipped) instead of warn-and-send. Default `false` preserves warn-then-send |
| `tls_ca_bundle` | string | `null` | Path to a custom CA bundle forwarded to `requests` as `verify=` (e.g. corporate MITM CA). When unset, `certifi`'s bundled store is used |

---

## `distributed` (Optional)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `strategy` | string | `null` | `"deepspeed"` or `"fsdp"` (null = single GPU) |
| `deepspeed_config` | string | `null` | Preset (`"zero2"`, `"zero3"`, `"zero3_offload"`) or JSON path |
| `fsdp_strategy` | string | `"full_shard"` | `"full_shard"`, `"shard_grad_op"`, `"hybrid_shard"`, `"no_shard"` |
| `fsdp_auto_wrap` | bool | `true` | Auto-wrap transformer layers |
| `fsdp_offload` | bool | `false` | Offload parameters to CPU |
| `fsdp_backward_prefetch` | string | `"backward_pre"` | `"backward_pre"` or `"backward_post"` |
| `fsdp_state_dict_type` | string | `"FULL_STATE_DICT"` | `"FULL_STATE_DICT"` or `"SHARDED_STATE_DICT"` |

---

## `merge` (Optional)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable model merging |
| `method` | string | `"ties"` | `"ties"`, `"dare"`, `"slerp"`, `"linear"` |
| `models` | list | `[]` | List of `{path, weight}` dicts |
| `output_dir` | string | `"./merged_model"` | Output directory |
| `ties_trim_fraction` | float | `0.2` | TIES: fraction (0.0–1.0) of smallest-magnitude deltas trimmed per task. Only consulted when `method` is `ties`. |
| `dare_drop_rate` | float | `0.3` | DARE: probability (0.0–1.0) each delta is randomly dropped before rescaling. Only consulted when `method` is `dare`. |
| `dare_seed` | int | `42` | DARE: RNG seed for the random drop mask, so a merge is reproducible run-to-run. |

> `enabled: true` requires at least two entries in `models`, each with a `path` key — a merge with fewer than two source models (or an entry missing `path`) is rejected at config-load time.

> **TIES/DARE default hyperparameters are intentionally conservative.** ForgeLM's
> native `ties` merge trims the bottom **20%** of weights by magnitude (keeps
> the top 80%); the `dare` merge uses `drop_rate=0.3` with a fixed seed. These
> defaults are intentionally more conservative than the published TIES (keep
> top ~20%) and DARE (`drop_rate` 0.9+) defaults — they retain more signal so a
> two-adapter merge is less destructive out of the box, but the result will
> differ from a paper-faithful merge. Operators who need the published sparsity
> regimes can raise `ties_trim_fraction` / `dare_drop_rate` (or merge with an
> external tool such as mergekit).

---

## `compliance` (Optional — EU AI Act Art. 11 + Annex IV)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `provider_name` | string | `""` | Organization name |
| `provider_contact` | string | `""` | Contact email |
| `system_name` | string | `""` | AI system name |
| `intended_purpose` | string | `""` | What the model is for |
| `known_limitations` | string | `""` | What it should not be used for |
| `system_version` | string | `""` | Version identifier |
| `risk_classification` | string | `"minimal-risk"` | One of the 5 EU AI Act `RiskTier` values: `"unknown"` (pre-classification placeholder), `"minimal-risk"`, `"limited-risk"`, `"high-risk"` (Article 6 — full Annex IV documentation), `"unacceptable"` (Article 5 prohibited practice — emits a startup banner). |

> **Hard gate:** setting `risk_classification` (or the sibling `risk_assessment.risk_category` below) to `"high-risk"` or `"unacceptable"` **requires** [`evaluation.safety.enabled: true`](#evaluationsafety-optional). Omitting it raises `ConfigError` (exit 1) at config-load / `--dry-run` time — EU AI Act Article 9 risk-management evidence cannot be derived from a disabled safety eval.

---

## `risk_assessment` (Optional — EU AI Act Art. 9)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `intended_use` | string | `""` | Intended use description |
| `foreseeable_misuse` | list | `[]` | List of misuse scenarios |
| `risk_category` | string | `"minimal-risk"` | Same 5 `RiskTier` values as `compliance.risk_classification`: `"unknown"`, `"minimal-risk"`, `"limited-risk"`, `"high-risk"`, `"unacceptable"`. Drives auto-revert thresholds and Annex IV gating. |
| `mitigation_measures` | list | `[]` | Risk mitigation measures |
| `vulnerable_groups_considered` | bool | `false` | Impact on vulnerable groups assessed |

> **Hard gate:** same as [`compliance.risk_classification`](#compliance-optional--eu-ai-act-art-11--annex-iv) above — setting `risk_category` to `"high-risk"` or `"unacceptable"` requires `evaluation.safety.enabled: true`, or config-load raises `ConfigError` (exit 1). The gate ORs across both fields: either one in a strict tier triggers it.

---

## `monitoring` (Optional — EU AI Act Art. 12+17)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable monitoring hooks |
| `endpoint` | string | `""` | Monitoring webhook URL |
| `endpoint_env` | string | `null` | Env var name for endpoint |
| `metrics_export` | string | `"none"` | `"none"`, `"prometheus"`, `"datadog"`, `"custom_webhook"` |
| `alert_on_drift` | bool | `true` | Alert on model drift |
| `check_interval_hours` | int | `24` | Monitoring check interval |

---

## `synthetic` (Optional — Synthetic Data Generation)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable teacher → student synthetic-data generation. |
| `teacher_model` | string | `""` | HF Hub ID or API model name (e.g. `gpt-4o`, `meta-llama/Llama-3-70B`). |
| `teacher_backend` | string | `"api"` | One of `"api"` (OpenAI/Anthropic-compatible), `"local"` (HF in-process), `"file"` (read pre-generated JSONL). |
| `teacher_revision` | string | `null` | Pin the local teacher model to an HF Hub commit SHA or ref. Only valid with `teacher_backend: local` — rejected otherwise. **Honoured today.** See [Hub revision pinning](#hub-revision-pinning). |
| `api_base` | string | `""` | API endpoint, e.g. `https://api.openai.com/v1` or self-hosted vLLM gateway. |
| `api_key` | `Optional[str]` | `null` | Inline API key. Prefer `api_key_env` to avoid committing secrets — when set inline, the value is `***REDACTED***` in serialized config. |
| `api_key_env` | `Optional[str]` | `null` | Env var name carrying the API key (e.g. `OPENAI_API_KEY`). |
| `api_delay` | float | `0.5` | Seconds between teacher calls (rate limiting). |
| `api_timeout` | int | `60` | Per-call API timeout in seconds. |
| `seed_file` | string | `""` | Path to seed prompts file (JSONL or plain text, one prompt per line). |
| `seed_prompts` | `List[str]` | `[]` | Inline seed prompts (alternative to `seed_file`). |
| `system_prompt` | string | `""` | System prompt prepended on every teacher call. |
| `max_new_tokens` | int | `1024` | Max tokens per teacher response. |
| `temperature` | float | `0.7` | Sampling temperature passed to the teacher. |
| `output_file` | string | `"synthetic_data.jsonl"` | Output JSONL file path. |
| `output_format` | string | `"messages"` | One of `"messages"` (chat-style array), `"instruction"` (Alpaca-style), `"chatml"`, `"prompt_response"`. **`chatml` emits ForgeLM's legacy `{User, Assistant}` key layout — NOT OpenAI `<\|im_start\|>` ChatML markup.** Use `messages` for a portable chat format. |
| `min_success_rate` | float | `0.0` | Minimum fraction (0.0–1.0) of seed prompts that must yield a usable example for `forgelm --generate-data` to exit 0. Default `0.0` keeps the legacy "any non-zero yield succeeds" behaviour; raise it so a CI pipeline does not proceed on a near-empty dataset. |
| `sanity_failure_rate` | float | `0.2` | Failure-rate (0.0–1.0) above which `forgelm --generate-data` logs a `WARNING` that the dataset may be small or skewed — independent of `min_success_rate` (which gates the exit code). Default `0.2` warns when more than 20% of prompts fail. |

---

## `auth` (Optional)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `hf_token` | string | `null` | HuggingFace token (prefer `HUGGINGFACE_TOKEN` env var) |

---

## `pipeline` (Optional — Multi-Stage Training Chains, Phase 14)

Chains 2+ training stages (typically SFT → DPO → GRPO) into one config-driven run with auto-chaining, per-stage gates, crash-safe resume, and a chain-level Annex IV manifest.  When omitted, ForgeLM behaves byte-identically to a v0.6.0 single-stage run; the orchestrator module is not imported.  Full operator walkthrough: [Multi-Stage Pipelines guide](../guides/pipeline.md).

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `output_dir` | string | `"./pipeline_run"` | Root directory for chain-level artefacts: `pipeline_state.json`, `compliance/pipeline_manifest.json`, and the pipeline-scoped `audit_log.jsonl`.  Per-stage trainer artefacts continue to live under each stage's own `training.output_dir`. |
| `stages` | `List[PipelineStage]` | *required* (≥ 1 stage) | Ordered list of stages.  Each stage's `model.name_or_path` is auto-set to the previous stage's `training.output_dir/final_model` unless the stage supplies an explicit `model:` block. |

### `pipeline.stages[].*` — PipelineStage fields

A `PipelineStage` is a per-stage override layered onto the root config.  Section-wholesale inheritance: omitting a block inherits root's wholesale; supplying a block REPLACES root's wholesale (no deep-merge).

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | string | — (required) | Stage identifier matching `^[a-z0-9_]{1,32}$`.  Unique within the pipeline.  Used as the identifier in `--stage <name>`, `--resume-from <name>`, audit-log payloads, and per-stage manifest entries. |
| `model` | `Optional[ModelConfig]` | `null` | Per-stage override of the root `model:` block.  When `null`, auto-chains from the previous stage's `final_model` (or root for stage 0).  When set, disables the auto-chain for that stage (operator escape hatch). |
| `lora` | `Optional[LoraConfigModel]` | `null` | Per-stage LoRA config.  Inherits root wholesale when `null`. |
| `training` | `Optional[TrainingConfig]` | `null` | Per-stage training config.  Inherits root wholesale when `null`.  **When supplied, `trainer_type` MUST be set explicitly** — every stage records its alignment paradigm in the manifest for audit clarity. |
| `data` | `Optional[DataConfig]` | `null` | Per-stage data config.  Inherits root wholesale when `null`; per-stage override is the norm because each stage typically consumes a different dataset (SFT/DPO/preference/etc.). |
| `evaluation` | `Optional[EvaluationConfig]` | `null` | Per-stage gates (loss thresholds, `auto_revert`, safety, judge, human-approval).  Each stage may independently configure its gate. |

Root-only sections — **rejected at the stage level** with `EXIT_CONFIG_ERROR (1)`: `distributed`, `webhook`, `compliance`, `risk_assessment`, `monitoring`, `retention`, `synthetic`, `merge`, `auth`.  These are pipeline-level concerns (distributed strategy stays consistent across the run; compliance metadata covers the whole chain; etc.).

### Example

```yaml
# Root defaults — inherited by stages that omit a block.
model: { name_or_path: "meta-llama/Llama-3-8B" }
lora: { r: 8, alpha: 16 }
training: { trainer_type: "sft", output_dir: "./placeholder" }
data: { dataset_name_or_path: "./placeholder.jsonl" }

pipeline:
  output_dir: "./pipeline_run"
  stages:
    - name: sft_stage
      training: { trainer_type: "sft", output_dir: "./pipeline_run/stage1_sft" }
      data: { dataset_name_or_path: "./data/sft.jsonl" }
    - name: dpo_stage
      training: { trainer_type: "dpo", output_dir: "./pipeline_run/stage2_dpo", dpo_beta: 0.1 }
      data: { dataset_name_or_path: "./data/preferences.jsonl" }
    - name: grpo_stage
      training: { trainer_type: "grpo", output_dir: "./pipeline_run/stage3_grpo" }
      data: { dataset_name_or_path: "./data/math_prompts.jsonl" }
```

### CLI surface

| Flag | Effect |
|------|--------|
| `--stage <name>` | Run only the named stage in isolation (audit / re-run scenarios).  Auto-chains from the previous stage's on-disk output. |
| `--resume-from <name>` | Resume from the named stage onward; already-completed (or human-approved gated) stages with on-disk output are skipped. |
| `--force-resume` | Accept a `pipeline_config_hash` mismatch on resume (logged + audited via `pipeline.force_resume`).  Stage topology mismatch (count / names / order) is refused even with this flag. |
| `--input-model <path>` | Operator escape hatch — overrides the auto-chained model for the `--stage` target.  Audit-logged with `input_source: cli_override`. |
| `--dry-run` | Validates every stage's merged config + cross-stage chain integrity + `training.output_dir` collision check before any GPU is allocated; collects all errors before exiting. |

The `--fit-check`, `--merge`, `--generate-data`, `--compliance-export`, `--benchmark-only` flags are single-stage operations and are rejected at dispatch time when a `pipeline:` block is present — drop the `pipeline:` block or remove the flag.

### Verifier

```bash
forgelm verify-annex-iv --pipeline <pipeline.output_dir>
```

Validates the chain-level manifest's structural fields, chain-integrity (every stage with `input_source: chain` matches its immediate predecessor's `output_model`), per-stage `training_manifest.json` existence, and `stopped_at` / running-status consistency.  Exit `0` on clean manifest, `1` on config / chain violation, `2` on runtime I/O failure.

---

## Hub revision pinning

Five optional fields pin a Hugging Face Hub repo to a specific commit so a run
can be reproduced byte-for-byte, and so the Annex IV bundle can say *which*
upstream artefact was used rather than only naming the repo.

| Field | Pins | Honoured today? |
|-------|------|-----------------|
| `model.revision` | Base model + tokenizer (and the VLM processor, and the `--fit-check` config probe) | **Yes** |
| `synthetic.teacher_revision` | Local teacher model + its tokenizer (`teacher_backend: local`) | **Yes** |
| `evaluation.llm_judge.judge_model_revision` | Local judge model + its tokenizer | **Yes** |
| `training.grpo_reward_model_revision` | GRPO reward model + its tokenizer | **Yes** |
| `evaluation.safety.classifier_revision` | Harm classifier | **Yes**, in the training-loop safety gate — see the scope note below |

`evaluation.safety.classifier_revision` reached no loader until this release: it
passed validation and sat in your YAML while the harm classifier behind the
auto-revert gate loaded off a moving default branch regardless. The
training-loop gate now honours it and records the resolved commit under
`model_lineage.component_revisions`.

One scope limit remains. Standalone `forgelm safety-eval` takes no `--config`
and has no `--classifier-revision` flag, so its classifier load is unpinned and
logs an UNPINNED warning naming the repo. A safety verdict produced by that
subcommand is not pinned evidence; one produced by the training-time gate is.

For each honoured field the value is resolved to a commit SHA first, and that
exact SHA is passed as `revision=` to **every** `from_pretrained` for that repo
— tokenizer and model alike — so the two can never come from different commits.

`judge_model_revision` pins the **local** judge only; the schema rejects it
alongside `judge_api_key_env`, because an API judge is loaded by the provider,
not from the Hub. `grpo_reward_model_revision` pins the GRPO reward tokenizer
and sequence-classification model, and the schema rejects it without
`grpo_reward_model`.

When a SHA cannot be confirmed — offline, no `huggingface_hub`, an unreachable
or gated repo — the operator's literal (a tag, a branch, a short SHA) is still
passed to `revision=` verbatim, so the pin is never silently dropped, and a
`WARNING` says that no SHA was verified. Leaving either field unset is unchanged
behaviour and logs a `WARNING` that the load is unpinned and that the run is not
byte-reproducible from the config alone.

Why these two matter beyond tidiness: the reward model **is** the objective GRPO
optimises against, so an unpinned upstream re-tune changes what the run was
trained to do — a stronger claim than the base-model pin, not a weaker one. The
judge's score feeds the auto-revert `min_score` gate, so an unpinned judge means
two runs of identical YAML can promote and block the same model.

### What counts as a pin

A **40-hex commit SHA** is the only value that actually pins. Upper- and
lower-case are both accepted and the value is stored verbatim — ForgeLM never
normalises or case-folds it.

A **branch, tag, or ref** (`main`, `v1.0`, `refs/pr/7`) is accepted, but it is
not a pin: upstream can repoint it at any time, so two runs of the same YAML can
load different bytes. ForgeLM logs a `WARNING` saying exactly that, and records
the ref verbatim in the provenance block beside whatever commit it resolved to,
so the artefact stays honest even when the config is not. There is no
enforcement flag in this release.

### Rejected at validation (exit `1`, fires under `--dry-run`, no network)

- A revision literal that is empty, contains whitespace, contains a control
  character, starts with `-`, or exceeds 255 characters.
- `evaluation.llm_judge.judge_model_revision` together with `judge_api_key_env`
  — the API judge never loads a local model.
- `synthetic.teacher_revision` with `teacher_backend` of `api` or `file` — only
  `local` loads from the Hub.
- `training.grpo_reward_model_revision` without `training.grpo_reward_model` —
  the pin names no repository, and the trainer would fall back to the built-in
  format/length shaping reward.

### Warned, not rejected

- A non-40-hex revision (see "What counts as a pin" above).
- `model.revision` set while `model.name_or_path` is an **existing local
  directory**. A path on disk carries no Hub commit, so the pin cannot be
  honoured and the loaded bytes are whatever is on disk. This warns rather than
  fails because the check depends on whether that directory exists on the
  machine running validation — raising would make one YAML pass in CI and fail
  on the training host. `model_integrity.json` plus `forgelm verify-integrity`
  remain the identity story for local weights.

### How the pin is chosen

Before each pinnable load ForgeLM resolves the repo's commit, then pins the load
to what it resolved. What reaches `revision=` is:

1. A confirmed 40-hex commit SHA, when one could be resolved — including when
   the configured value was a branch or tag, in which case the load is pinned to
   the specific commit that ref pointed at.
2. Otherwise the configured value verbatim, so an explicit pin is always
   honoured even when nothing could confirm it.
3. Otherwise nothing — the historical unpinned behaviour, unchanged.

Resolution is best-effort and never fails a run. `model.offline: true` (or
`HF_HUB_OFFLINE` / `HF_DATASETS_OFFLINE` / `TRANSFORMERS_OFFLINE`)
short-circuits before any Hub client is imported: no network attempt is made,
and the commit-addressed local cache answers instead. All three env vars now
suppress model-revision lookups as well as dataset lookups; `TRANSFORMERS_OFFLINE`
previously suppressed only the dataset side. A local directory is never resolved
and never pinned.

Every Hub metadata lookup is bounded at **10 seconds**. These calls previously
had no timeout at all, so a firewall that drops packets silently could hang a
run indefinitely before training started — including on a fully-cached machine
where the load itself needed no network. On timeout the run continues and the
provenance record degrades to `unresolved`; it never fails the run.

### The `unsloth` backend

`model.backend: unsloth` is an optional extra, so whether
`FastLanguageModel.from_pretrained` accepts a `revision` argument is decided at
runtime by inspecting its signature. A bare `**kwargs` deliberately does *not*
count — a kwarg that is accepted and then dropped is indistinguishable from one
that is honoured.

- Named `revision` parameter present → the pin is applied and recorded exactly
  as on the transformers backend.
- Absent **and** `model.revision` is set → the run fails with a `RuntimeError`
  before any weights load (CLI exit `2`). The message names three remedies:
  upgrade unsloth, switch to `model.backend: transformers`, or remove
  `model.revision` to load the default branch knowingly. It fails rather than
  proceeding because an operator holding a manifest that asserts a pin the run
  never applied is worse off than one with no pin at all.
- Absent and no pin set → the load proceeds as before, with a `WARNING` that it
  is unpinned and that no model revision will be recorded.

### Where the record lands

The resolved base-model revision is written to the `model_lineage` block of
**`compliance_report.json`** — under `base_model_revision`, and again as a
`base_model` entry in the sibling `component_revisions` list that also carries
the safety classifier, LLM judge, GRPO reward model and synthetic teacher — and
dataset revisions to
**`data_provenance.json`** — both inside the Annex IV bundle. Note that the
flattened `training_manifest.yaml` sidecar carries neither: it is a summary
projection (`base_model`, `adapter_method`, `trainer_type`, `dataset`, `epochs`,
`final_metrics`) and has no `model_lineage` or `data_provenance` block at all.
See [`compliance_summary.md`](compliance_summary.md#annex-iv-bundle-provenance-fields)
for the field-by-field meaning of every `resolution_source` and
`hf_revision_source` value.

**Every pinned load reaches an artefact.** Alongside
`model_lineage.base_model_revision` — unchanged, and still the base model's
dedicated block — the manifest carries `model_lineage.component_revisions`, a
list with one entry per completed pinned load in that process. Six role names
are contract and never change: `base_model`, `safety_classifier`, `llm_judge`,
`grpo_reward_model`, `teacher_model`, `fit_check`. The base model appears in
both places, from one registry entry, so the two can never disagree.

Two things the list does **not** say. `component_revisions: []` means no pinned
load completed in this process — `forgelm compliance-only`, an all-local-path
config, or a manifest written before any load — and is *not* a statement that no
pins were configured. A null `revision_resolved` means no SHA could be
confirmed; the run may still have been pinned to a ref, which `revision_pinned`
records verbatim.

`fit_check` is reserved, not yet emitted: `model.revision` *is* forwarded to the
VRAM-estimate `AutoConfig` probe, so that load is pinned, but the probe
registers no provenance and no `fit_check` entry ever appears.

### Known gaps in this release

- `--dry-run` does not report pin status; an operator learns which repos are
  unpinned from load-time warnings, i.e. after the run starts. `--dry-run` also
  deliberately never verifies that pins are *fetchable* — its contract is
  validation without heavy dependencies or Hub reachability. A pipeline that runs
  `--dry-run` but not `forgelm doctor` can get a green validation followed by a
  load-time failure on an unfetchable pin.
- `forgelm cache-models` has no `--revision` flag, so an air-gapped workflow
  stages the repo's default branch. A pinned run on the disconnected host will
  then miss its snapshot.
- `export`, `inference` and `merging` are unpinned by design: they load local
  artefacts this run produced, and a directory has no Hub commit.
- Merge-source models (`merge.models[]`) cannot be pinned.
- `forgelm safety-eval` takes no `--config` and has no `--classifier-revision`
  flag, so its classifier load is unpinned regardless of
  `evaluation.safety.classifier_revision` and logs an UNPINNED warning naming
  the repo. Only the training-time safety gate honours that field.
- The `fit_check` role is reserved but never emitted — the VRAM probe is pinned
  by `model.revision` yet registers no provenance.
