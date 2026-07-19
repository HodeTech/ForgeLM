---
title: Configuration Reference
description: Every YAML field ForgeLM understands, with types, defaults, and notes.
---

# Configuration Reference

This is the canonical reference for every YAML field ForgeLM accepts. The schema is enforced by Pydantic; running `forgelm --config X.yaml --dry-run` validates your file against it.

The top-level config has 15 blocks:

```yaml
# INVALID: structure overview only — each block's real fields are documented
# below; the {...} placeholders are not a runnable config.
model:           {...}
lora:            {...}
training:        {...}
data:            {...}
auth:            {...}
evaluation:      {...}
webhook:         {...}
distributed:     {...}
merge:           {...}
compliance:      {...}
risk_assessment: {...}
monitoring:      {...}
synthetic:       {...}
retention:       {...}
pipeline:        {...}
```

> **Note:** `galore` fields are flat sub-fields inside `training:` (prefixed `galore_*`), not a separate top-level block. See the [`training:`](#training) section below.

## `model:`

```yaml
model:
  name_or_path: "Qwen/Qwen2.5-7B-Instruct"   # HF id or local path (required)
  trust_remote_code: false                    # only set true if you trust the model's repo
  max_length: 4096                            # context for training
  load_in_4bit: false                         # QLoRA toggle (NF4/FP4 only — no separate 8-bit toggle)
  backend: "transformers"                     # transformers | unsloth (Linux + CUDA only, 2-5× speedup)
  bnb_4bit_quant_type: "nf4"                  # nf4 | fp4
  bnb_4bit_compute_dtype: "bfloat16"          # auto | bfloat16 | float16 | float32 (bf16/fp16/fp32 aliases accepted)
  bnb_4bit_use_double_quant: true             # bitsandbytes double-quantisation (small extra VRAM win)
  offline: false                              # air-gapped mode: refuse HF Hub network calls
  revision: "0e9e39f249a16976918f6564b8830bc894c89659"  # pin base model + tokenizer to a Hub commit
```

`revision` accepts a 40-hex Hub commit SHA (the only value that actually pins) or a branch/tag such as `main` — a ref is accepted but ForgeLM warns, because upstream can repoint it. It pins the tokenizer, the VLM processor, the weights, and the `--fit-check` config probe at the same commit, and the resolved SHA is recorded in the Annex IV bundle. Setting it against a local directory warns and does nothing: a path on disk has no Hub commit. Four sibling pin fields exist, and all four are **honoured**: `synthetic.teacher_revision`, `evaluation.llm_judge.judge_model_revision`, `training.grpo_reward_model_revision` and `evaluation.safety.classifier_revision` each resolve to a commit SHA that is passed to every `from_pretrained` for that repo, tokenizer and model alike, so the two can never come from different commits. When no SHA can be confirmed the literal is still passed verbatim and a `WARNING` says nothing was verified. `classifier_revision` reached no loader until this release — the harm classifier behind the auto-revert gate loaded off a moving default branch regardless of the config — and it applies to the training-time safety gate only: standalone `forgelm safety-eval` takes no config and loads its classifier unpinned. All four pins now reach a compliance artefact: the Annex IV bundle carries `model_lineage.component_revisions` alongside the unchanged `model_lineage.base_model_revision`.

`ModelConfig` has no `load_in_8bit`, `use_unsloth`, `attention_implementation`, or `torch_dtype` field — `extra="forbid"` rejects all four at `--dry-run`. There is no separate 8-bit toggle (`load_in_4bit` is the only quantisation switch); the Unsloth backend is selected with `backend: "unsloth"`, not a boolean flag; ForgeLM has no attention-implementation selector; and compute dtype is set via `bnb_4bit_compute_dtype`, not a standalone `torch_dtype` field. `rope_scaling` and the sliding-window override are `TrainingConfig` fields (`training.rope_scaling`, `training.sliding_window_attention`), not `ModelConfig` fields — see [`training:`](#training) below and [Long-Context Fine-Tuning](#/training/long-context) for the concept.

## `lora:`

```yaml
lora:
  r: 16                                       # rank — see [LoRA](#/training/lora)
  alpha: 32
  dropout: 0.05
  bias: "none"                                # none | all | lora_only
  method: "lora"                              # lora | dora | pissa | rslora
  target_modules: ["q_proj", "k_proj", "v_proj", "o_proj"]
  use_dora: false                             # deprecated boolean shortcut for method: "dora"; scheduled for removal in v1.0.0
  use_rslora: false                           # deprecated boolean shortcut for method: "rslora"; scheduled for removal in v1.0.0
```

`LoraConfigModel` has no `modules_to_save` or `use_pissa` field — `extra="forbid"` rejects both at `--dry-run`. PiSSA initialisation is selected with `method: "pissa"` (there is no boolean toggle); `use_dora` / `use_rslora` are deprecated boolean shortcuts for `method: "dora"` / `method: "rslora"`, scheduled for removal in v1.0.0 — setting both at once, or setting one against a contradictory explicit `method:`, is a config error.

## `data:`

```yaml
data:
  dataset_name_or_path: "data/train.jsonl"    # HF Hub id, local JSONL path, or dir of JSONL (required)
  extra_datasets:                             # additional datasets to mix in alongside the primary
    - "org/extra_dataset"
  mix_ratio: [0.8, 0.2]                       # one weight per dataset (primary + extras); uniform if omitted
  shuffle: true                               # shuffle the merged corpus before splitting train/validation
  clean_text: true                            # strip excess whitespace + control characters
  add_eos: true                               # append EOS token so generation knows where to stop
  governance:                                 # Article 10 data-governance metadata (optional)
    collection_method: ""
    annotation_process: ""
    known_biases: ""
    personal_data_included: false
    dpia_completed: false
```

`data:` is a **single object**, not a list — there is exactly one primary dataset (`dataset_name_or_path`), and any additional datasets are mixed in via `extra_datasets` + `mix_ratio` (one weight per dataset, primary first). Format is auto-detected per file; supported shapes are `instructions`, `messages`, `preference`, `binary`, `reward` — see [Dataset Formats](#/concepts/data-formats).

## `training:`

```yaml
training:
  output_dir: "./checkpoints"                 # checkpoints + audit log + compliance bundle land here
  final_model_dir: "final_model"              # subdirectory of output_dir for the promoted model
  merge_adapters: false                       # merge LoRA adapters into the base model when SFT finishes
  trainer_type: "sft"                         # sft | dpo | simpo | kto | orpo | grpo
  max_steps: -1                               # -1 = use num_train_epochs; a positive value overrides epochs
  num_train_epochs: 3
  per_device_train_batch_size: 4
  gradient_accumulation_steps: 2
  learning_rate: 2.0e-5
  warmup_ratio: 0.1
  weight_decay: 0.01
  eval_steps: 200
  save_steps: 200
  save_total_limit: 3
  early_stopping_patience: 3                  # stop after N evals with no validation-loss improvement
  packing: false                              # sequence packing (SFT)
  rope_scaling: null                          # dict — long-context RoPE scaling, e.g. {type: "yarn", factor: 4.0}; see [Long-Context](#/training/long-context)
  sliding_window_attention: null              # int — override the model's sliding-window size (e.g. 4096 for Mistral); null = model default
  neftune_noise_alpha: null                   # float — embedding-noise regularisation (e.g. 5.0)
  report_to: "tensorboard"                    # tensorboard | wandb | mlflow | none
  run_name: null                              # auto-generated when null

  # Alignment-method parameters — flat fields on `training:`, not nested
  # per-trainer sub-blocks. Only the fields matching `trainer_type` are read.
  dpo_beta: 0.1                               # DPO temperature
  simpo_gamma: 0.5                            # SimPO margin term
  simpo_beta: 2.0                             # SimPO scaling
  kto_beta: 0.1                               # KTO loss parameter
  orpo_beta: 0.1                              # ORPO odds-ratio weight
  grpo_num_generations: 4                     # GRPO: responses generated per prompt
  grpo_max_completion_length: 512             # GRPO: max tokens per completion (legacy alias `grpo_max_new_tokens` accepted)
  grpo_reward_model: null                     # GRPO: HF path for reward scoring; null = built-in format/length shaping
```

There is no nested `training.dpo:` / `training.simpo:` / `training.kto:` / `training.orpo:` / `training.grpo:` sub-block — `TrainingConfig` rejects unknown keys (`extra="forbid"`), so a nested block fails `--dry-run` with a config error. Every alignment-method parameter, `rope_scaling` / `sliding_window_attention` (shown above — these are `TrainingConfig` fields, not `ModelConfig` fields, see the [`model:`](#model) note above), and NEFTune are flat fields directly on `training:`. The GaLore `galore_*` optimizer knobs, `oom_recovery` / `oom_recovery_min_batch_size`, the deprecated `sample_packing` alias for `packing`, and `gpu_cost_per_hour` are also flat `training:` fields, not shown in the abbreviated example above — see [GaLore](#/training/galore) and [YAML Templates](#/reference/yaml-templates) for full per-trainer worked examples.

## `evaluation:`

```yaml
evaluation:
  auto_revert: false                          # restore the pre-training model on quality regression
  max_acceptable_loss: null                   # float — hard cap on validation loss; requires auto_revert: true
  baseline_loss: null                         # float — auto-computed when a validation split exists
  require_human_approval: false               # Article 14: pause the pipeline for human review (exit 4)
  benchmark:
    enabled: false
    tasks: []                                 # e.g. ["arc_easy", "hellaswag", "mmlu"]; required when enabled
    num_fewshot: null                         # null = task's documented default
    batch_size: "auto"                        # "auto" or an integer string
    limit: null                               # cap samples per task for quick checks
    output_dir: null                          # null = the training output_dir
    min_score: null                           # scalar float floor across averaged tasks
  safety:
    enabled: false
    classifier: "meta-llama/Llama-Guard-3-8B"  # default works out of the box via generation-based scoring
    classifier_mode: "auto"                   # auto | classification | generation — see [Llama Guard Safety](#/evaluation/safety)
    test_prompts: "safety_prompts.jsonl"
    max_safety_regression: 0.05               # absolute post-training unsafe-ratio ceiling — see [Llama Guard Safety](#/evaluation/safety)
    scoring: "binary"                         # binary | confidence_weighted
    min_safety_score: null                    # used only when scoring: confidence_weighted
    min_classifier_confidence: 0.7
    track_categories: false
    severity_thresholds: null                 # dict, e.g. {critical: 0, high: 0.01, medium: 0.05}
    batch_size: 8
    include_eval_samples: false                # persist raw prompt/response text; off by default (privacy)
  llm_judge:
    enabled: false
    judge_model: "gpt-4o"                     # plain string — API model name or local model path
    judge_api_key_env: null                   # env var carrying the judge API key; null = local judge model
    judge_api_base: null                      # override the judge API base URL
    eval_dataset: "eval_prompts.jsonl"
    min_score: 5.0                            # 1.0-10.0 scale
    batch_size: 8
    include_eval_samples: false                # persist raw prompt/response/reason text; off by default (privacy)
```

## `synthetic:`

```yaml
synthetic:
  enabled: false
  teacher_model: "gpt-4o"                     # HF Hub id or API model name (e.g. gpt-4, meta-llama/Llama-3-70B)
  teacher_backend: "api"                      # api | local | file
  api_base: "https://api.openai.com/v1"       # API endpoint; consulted for teacher_backend: "api"
  api_key_env: "OPENAI_API_KEY"               # env var carrying the API key — prefer over inline api_key
  api_delay: 0.5                              # seconds between API calls (rate limiting)
  api_timeout: 60                             # per-call timeout in seconds
  seed_file: "data/seeds.jsonl"               # one prompt per line, or JSONL — alternative to seed_prompts
  seed_prompts: []                            # inline seed prompts (alternative to seed_file)
  system_prompt: ""                           # prepended on every teacher call
  max_new_tokens: 1024
  temperature: 0.7
  output_file: "synthetic_data.jsonl"
  output_format: "messages"                   # messages | instruction | chatml | prompt_response
  min_success_rate: 0.0                       # min fraction of seeds that must yield a usable example
  sanity_failure_rate: 0.2                    # failure rate above which a WARNING is logged (warn-only)
```

There is no nested `synthetic.teacher:` sub-block and no `rate_limit:` block — `teacher_model`, `teacher_backend`, `api_base`, `api_delay`, and `api_timeout` are flat fields on `synthetic:` directly. Prefer `api_key_env` (an environment-variable name) over the inline `api_key` field to avoid committing secrets. ForgeLM's YAML loader has no `${VAR}` interpolation mechanism anywhere — `*_env` fields name an environment variable that is read directly via `os.environ`, never substituted into a string.

Under `teacher_backend: "local"` you may also set `teacher_revision` to pin the teacher checkpoint to a Hub commit SHA or ref; the teacher's generations become training data, so an unpinned teacher is a data-provenance gap. It is rejected outright with `teacher_backend: "api"` or `"file"`, which never load from the Hub.

## `merge:`

```yaml
merge:
  enabled: false
  method: "ties"                              # ties | dare | slerp | linear
  models:                                     # at least two entries required when enabled
    - path: "./checkpoints/run1/final_model"
      weight: 0.7
    - path: "./checkpoints/run2/final_model"
      weight: 0.3
  output_dir: "./merged_model"
  ties_trim_fraction: 0.2                     # TIES: fraction of smallest deltas trimmed (0.0-1.0); only used when method: ties
  dare_drop_rate: 0.3                         # DARE: probability each delta is dropped (0.0-1.0); only used when method: dare
  dare_seed: 42                               # DARE: RNG seed for the random drop mask
```

The merge field is `method` (not `algorithm`), and there is no `dare_ties` choice, no `base_model` field, no nested `parameters:` block (`threshold` / `density` / `t`), and no nested `output:` block — the output path is the flat `output_dir` field, with no separate `model_card` toggle.

## `distributed:`

```yaml
distributed:
  strategy: null                              # null (single-GPU, no distributed wrapping) | deepspeed | fsdp
  deepspeed_config: null                      # path to a DeepSpeed JSON, or preset name: zero2 | zero3 | zero3_offload
  fsdp_strategy: "full_shard"                 # full_shard | shard_grad_op | no_shard | hybrid_shard
  fsdp_auto_wrap: true                        # auto-wrap transformer layers (recommended)
  fsdp_offload: false                         # offload FSDP parameters to CPU between forward/backward
  fsdp_backward_prefetch: "backward_pre"      # backward_pre | backward_post
  fsdp_state_dict_type: "FULL_STATE_DICT"     # FULL_STATE_DICT | SHARDED_STATE_DICT
```

`DistributedConfig` has no `zero_stage`, `cpu_offload`, `nvme_offload_path`, `fsdp_auto_wrap_policy`, or `fsdp_offload_params` field, and `strategy: "single"` does not validate — `extra="forbid"` rejects the phantom fields, and `strategy` is a `Literal["deepspeed", "fsdp"]` that otherwise only accepts `null` (the default; no distributed wrapping). DeepSpeed's ZeRO stage and CPU/NVMe offload are selected together through `deepspeed_config` — either a filesystem path to a DeepSpeed JSON, or one of the built-in preset names `zero2`, `zero3`, `zero3_offload` — not through separate `zero_stage` / `cpu_offload` / `nvme_offload_path` fields. FSDP's auto-wrap and parameter-offload toggles are the plain booleans `fsdp_auto_wrap` and `fsdp_offload`, not a wrap-policy string or a `_params`-suffixed field. `gradient_accumulation_steps` is a `training:` field (see [`training:`](#training) above), not a `distributed:` field — it is not duplicated here.

## `compliance:`

```yaml
compliance:
  provider_name: ""                           # Annex IV §1: legal-entity name of the system provider
  provider_contact: ""                        # Annex IV §1: provider's regulatory point of contact
  system_name: ""                             # Annex IV §1: human-readable system name
  intended_purpose: ""                        # Annex IV §1: declared intended purpose (free-text)
  known_limitations: ""                       # Annex IV §3: documented system limitations
  system_version: ""                          # Annex IV §1: operator-supplied version string
  risk_classification: "minimal-risk"         # unknown | minimal-risk | limited-risk | high-risk | unacceptable
```

`ComplianceMetadataConfig` has exactly these **seven flat fields**. There is no `annex_iv`, `data_audit_artifact`, `human_approval`, `deployment_geographies`, `responsible_party`, `version`, `standards`, `notes`, nor nested `risk_assessment:` / `data_protection:` / `audit_log:` / `approval:` / `post_market_plan` / `license` sub-fields under `compliance:` — `extra="forbid"` rejects all of them at `--dry-run`. Concretely:

- The Annex IV bundle is generated automatically once `compliance:` is present and `risk_classification` resolves to `high-risk` or `unacceptable` — there is no separate `annex_iv: true` toggle.
- Human-approval gating is `evaluation.require_human_approval: true` (see [`evaluation:`](#evaluation) above), not `compliance.human_approval`.
- Article 9 risk data (foreseeable misuse, mitigation measures) lives on the separate top-level [`risk_assessment:`](#risk_assessment) block below, not nested under `compliance:`.
- The append-only audit log always writes to `<training.output_dir>/audit_log.jsonl` — there is no `compliance.audit_log.path` override, and no `${output.dir}`-style interpolation anywhere in ForgeLM's YAML loader.

## `webhook:`

```yaml
webhook:
  url: null                                   # Slack / Teams / Discord / custom; prefer url_env
  url_env: null                               # env var carrying the webhook URL
  notify_on_start: true
  notify_on_success: true
  notify_on_failure: true
  timeout: 10                                 # HTTP request timeout in seconds
  allow_private_destinations: false           # SSRF opt-in for in-cluster endpoints
  require_https: false                        # refuse plaintext http:// URLs when true
  tls_ca_bundle: null                         # custom CA bundle path (corporate MITM)
```

## `risk_assessment:`

```yaml
risk_assessment:
  intended_use: ""                            # Article 9(2)(a): intended purpose (free-text)
  foreseeable_misuse: []                      # Article 9(2)(b): misuse scenarios list
  risk_category: "minimal-risk"              # unknown | minimal-risk | limited-risk | high-risk | unacceptable
  mitigation_measures: []                    # Article 9(2)(c): mitigation steps
  vulnerable_groups_considered: false        # Article 9(2)(b): impact on vulnerable groups
```

## `monitoring:`

```yaml
monitoring:
  enabled: false                              # Enable Article 12 post-market monitoring
  endpoint: ""                               # Monitoring webhook URL (Prometheus / Datadog / custom)
  endpoint_env: null                          # env var overriding endpoint
  metrics_export: "none"                     # none | prometheus | datadog | custom_webhook
  alert_on_drift: true                       # webhook alert on drift-detector regression
  check_interval_hours: 24                   # monitoring cadence in hours
```

## `retention:`

```yaml
retention:
  audit_log_retention_days: 1825             # 5 years default (Article 5(1)(e))
  staging_ttl_days: 7                        # days to retain staging model after forgelm reject
  ephemeral_artefact_retention_days: 90      # compliance bundles, audit reports
  raw_documents_retention_days: 90           # ingested PDF/DOCX/EPUB/TXT/Markdown
  enforce: "log_only"                        # log_only | warn_on_excess | block_on_excess
```

## `pipeline:`

```yaml
pipeline:
  output_dir: "./pipeline_run"               # pipeline-level output directory
  stages:                                     # ordered list of training stages (min 1)
    - name: "sft"
      training: { trainer_type: "sft", num_train_epochs: 3 }
    - name: "dpo"
      training: { trainer_type: "dpo", num_train_epochs: 1, dpo_beta: 0.1 }
```

## `auth:`

```yaml
auth:
  hf_token: null                              # HuggingFace Hub token; auto-redacted from logs/manifests
```

`AuthConfig` has exactly one field, `hf_token` — there is no `openai_api_key` or `anthropic_api_key` field (synthetic-data / judge API keys are configured separately via `synthetic.api_key_env` / `evaluation.llm_judge.judge_api_key_env`). When `auth.hf_token` is left `null`, ForgeLM falls back to the `HUGGINGFACE_TOKEN` environment variable automatically — there is no `${VAR}` interpolation syntax in ForgeLM's YAML loader.

## `deployment:`

There is no `deployment:` top-level YAML key — `ForgeConfig` rejects unknown keys (`extra="forbid"`), so adding one to your training config raises `ConfigError` at load time. Deployment knobs are exposed as `forgelm deploy` CLI flags instead. The live target choices are `--target {ollama,vllm,tgi,hf-endpoints}`; see the [Deploy targets page](#/deployment/deploy-targets) and the [CLI reference](#/reference/cli) for the full surface.

> **Not yet scheduled:** A YAML-backed `deployment:` section has been deferred past v0.7.0. Until it ships, treat any "deployment:" YAML you find in third-party templates as informational; only the `forgelm deploy` flags are authoritative.

## See also

- [CLI Reference](#/reference/cli) — flags that complement YAML fields.
- [YAML Templates](#/reference/yaml-templates) — full working examples.
- [Configuration Overview (concepts)](#/concepts/alignment-overview) — what these fields mean conceptually.
