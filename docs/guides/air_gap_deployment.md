# Air-Gap Deployment Cookbook

> **Audience:** Operators deploying ForgeLM to a restricted-egress host — defence, healthcare, certain financial sectors, classified-network customer environments. Anyone whose security policy refuses outbound HTTPS during training.
>
> This is a deep deployer cookbook. Mirrors the depth of the [GDPR erasure guide](gdpr_erasure.md) and [ISO/SOC 2 deployer guide](iso_soc2_deployer_guide.md) — every command shown has been verified against the implementation in [`forgelm/cli/subcommands/_cache.py`](../../forgelm/cli/subcommands/_cache.py) and [`forgelm/cli/subcommands/_doctor.py`](../../forgelm/cli/subcommands/_doctor.py).

## What it solves

Three operator pains the air-gap workflow addresses:

1. **HuggingFace Hub egress blocked.** The trainer needs model weights, tokenizers, configs, and (when safety eval is enabled) Llama Guard. None of these can be downloaded at training time on the air-gapped host.
2. **lm-evaluation-harness datasets blocked.** `lm-eval` defers dataset downloads until the first invocation; on the air-gapped host that becomes a runtime crash, not a config error.
3. **No way to verify the bundle survived transfer intact.** Operators copy 30 GiB of cache via USB / scp / removable media, then discover one shard is missing 90 minutes into the first training run.

`forgelm cache-models`, `forgelm cache-tasks`, and `forgelm doctor --offline` are the three pieces that make this workflow auditable end-to-end.

## The two-host workflow at a glance

```text
┌─────────────────────────────────┐       ┌────────────────────────────────┐
│ Connected staging host          │       │ Air-gapped target host         │
│                                 │       │                                │
│ 1. forgelm cache-models ...     │       │ 4. forgelm doctor --offline    │
│ 2. forgelm cache-tasks ...      │       │ 5. forgelm --offline           │
│                                 │       │      --config configs/run.yaml │
│ 3. tar / rsync the bundle ─────────────────►                             │
└─────────────────────────────────┘       └────────────────────────────────┘
```

Every step emits structured audit events (`cache.populate_*`); the bundle is the artefact, the audit chain is the evidence.

## Step-by-step

### Step 1 — On the connected host: cache models

```shell
$ export HF_HUB_CACHE="$PWD/airgap-bundle/hub"
$ export FORGELM_OPERATOR="staging:bundle-202605"

$ forgelm cache-models \
    --model "Qwen/Qwen2.5-7B-Instruct" \
    --safety "meta-llama/Llama-Guard-3-8B"
Cached 2 model(s); 28415.32 MiB total under .../airgap-bundle/hub.
  - Qwen/Qwen2.5-7B-Instruct: 14207.66 MiB (412.8s)
  - meta-llama/Llama-Guard-3-8B: 14207.66 MiB (398.2s)
```

What lives where:

| Bundle subtree | Populated by | Reads at training time |
|---|---|---|
| `airgap-bundle/hub/` | `cache-models` (HF Hub snapshots) | `transformers.AutoModel.from_pretrained` via `huggingface_hub` |
| `airgap-bundle/datasets/` | `cache-tasks` (parquet shards) | `datasets.load_dataset` |

The two are **separate caches** with **separate env vars** (`HF_HUB_CACHE` vs `HF_DATASETS_CACHE`). Setting `HF_HUB_CACHE` does NOT redirect dataset downloads, and vice versa. Setting `HF_HOME` redirects both via the `hub/` and `datasets/` sub-directories.

> **Note on `--output`.** You can pass `--output ./airgap-bundle/hub` instead of setting `HF_HUB_CACHE` env var. ForgeLM will warn that this diverges from the env-resolved location that `forgelm doctor --offline` and the trainer will read — set the env var to match before transfer (the warning text gives the exact `HF_HUB_CACHE=...` or `HF_DATASETS_CACHE=...` line). The env-var-first approach is recommended because the *same* configuration used at staging is used on the air-gapped host.

### Step 2 — On the connected host: cache lm-eval tasks

```shell
$ export HF_DATASETS_CACHE="$PWD/airgap-bundle/datasets"

$ forgelm cache-tasks --tasks "hellaswag,arc_easy,truthfulqa,mmlu"
Cached 4 of 4 task(s) under .../airgap-bundle/datasets.
  - hellaswag: ok
  - arc_easy: ok
  - truthfulqa: ok
  - mmlu: ok
```

Requires the `[eval]` extra (`pip install 'forgelm[eval]'`). A missing import is reported as `EXIT_CONFIG_ERROR` (operator-actionable) with an explicit install hint.

`cache-tasks` stamps `HF_DATASETS_CACHE` to the resolved cache directory inside a `try/finally` so the underlying `datasets` library writes parquet shards in the right subtree — without this, a CSV mismatch between the runtime's `HF_DATASETS_CACHE` and the operator's `--output` would silently drop shards in `~/.cache/huggingface/datasets`.

### Step 3 — Bundle and transfer

```shell
$ ls -la airgap-bundle/
hub/         # populated by cache-models
datasets/    # populated by cache-tasks

$ tar --create --gzip --file airgap-bundle.tar.gz airgap-bundle/
$ sha256sum airgap-bundle.tar.gz > airgap-bundle.tar.gz.sha256
```

Transfer `airgap-bundle.tar.gz` and `airgap-bundle.tar.gz.sha256` together. The hash is your tamper-evidence between staging host and target host. Verify on the target:

```shell
$ sha256sum -c airgap-bundle.tar.gz.sha256
airgap-bundle.tar.gz: OK
$ tar --extract --gzip --file airgap-bundle.tar.gz
```

### Step 4 — On the air-gapped host: validate with `forgelm doctor --offline`

```shell
$ export HF_HUB_CACHE="$PWD/airgap-bundle/hub"
$ export HF_DATASETS_CACHE="$PWD/airgap-bundle/datasets"
$ export HF_HUB_OFFLINE=1
$ export TRANSFORMERS_OFFLINE=1
$ export HF_DATASETS_OFFLINE=1

$ forgelm doctor --offline
forgelm doctor - environment check

  [+ pass] forgelm.install          ForgeLM 0.9.0 running from /opt/airgap-venv/lib/python3.11/site-packages/forgelm (inside site-packages).
  [+ pass] python.version           Python 3.11.4 (CPython).
  [+ pass] torch.cuda               torch 2.4.0 with CUDA 12.4.
  [+ pass] numpy.torch_abi          torch 2.4.0 + numpy 1.26.4 are ABI-compatible.
  [+ pass] gpu.inventory            1 GPU(s) — GPU0: NVIDIA A100 (80.0 GiB).
  [+ pass] extras.qlora             Discoverable, not import-tested (module bitsandbytes, purpose: 4-bit / 8-bit QLoRA training).
  [+ pass] extras.eval              Discoverable, not import-tested (module lm_eval, purpose: lm-evaluation-harness benchmark scoring).
  [+ pass] pypdf_normalise.turkish  Turkish glyph profile loaded: 5 single-char + 1 multi-char substitutions. Active profile: 'none'.
  [+ pass] hf_hub.offline_cache     HF cache at .../airgap-bundle/hub: 27.7 GiB across 142 file(s). HF_HUB_OFFLINE=1.
  [+ pass] disk.workspace           Workspace /opt/airgap — 412.0 GiB free of 500.0 GiB.
  [+ pass] operator.identity        FORGELM_OPERATOR set to 'airgap-prod'; audit events will carry this identity.

Summary: 11 pass, 0 warn, 0 fail.
```

> Some `extras.*` rows are omitted above for brevity — every extra in `pyproject.toml`'s `[project.optional-dependencies]` gets its own row (nine as of this writing).

Key checks:

- `forgelm.install` is always first: it records which ForgeLM binary is actually running (resolved location, version, inside/outside `site-packages`) so a bug report from the air-gapped host is unambiguous about which build produced it.
- The `hf_hub.offline_cache` probe replaces `hf_hub.reachable` whenever `--offline` is passed OR `HF_HUB_OFFLINE`/`TRANSFORMERS_OFFLINE`/`HF_DATASETS_OFFLINE` is set in the environment. ForgeLM honours all three.
- The cache scan is bounded: depth 4, file-count 5000. A truncated scan reports `walk_truncated=true` in `extras` so partial-scan results are explicit (not silent).
- An unreadable cache root (chmod broken, mount detached) reports `fail` rather than `warn` so a misconfigured target host is caught before training starts.

If the doctor reports a `fail`, fix it before kicking off training — the air-gapped operator's wall clock is too expensive to debug on.

### Step 5 — Train with `--offline`

```shell
$ forgelm --config configs/run.yaml --offline
```

The `--offline` flag enforces `local_files_only=True` end-to-end. Any model reference that was not pre-cached fails fast with a clear error pointing at the missing snapshot — not a confusing `HTTPError` 90 minutes in.

## CI workflow integration

Both staging and target hosts can run their respective steps from CI:

```yaml
# Staging-side GitHub Actions workflow
- name: Pre-cache HF Hub models
  run: |
    forgelm cache-models \
      --model "${{ env.BASE_MODEL }}" \
      --safety "meta-llama/Llama-Guard-3-8B" \
      --output-format json -q | tee cache-models.json
    jq -e '.success' cache-models.json

- name: Pre-cache lm-eval tasks
  run: |
    forgelm cache-tasks --tasks "${{ env.EVAL_TASKS }}" \
      --output-format json -q | tee cache-tasks.json
    jq -e '.success' cache-tasks.json

- name: Bundle
  run: tar --create --gzip --file airgap-bundle.tar.gz airgap-bundle/
```

```yaml
# Air-gapped target-side workflow (runs on a runner with no internet)
- name: Validate environment
  run: forgelm doctor --offline --output-format json -q | jq -e '.success'

- name: Train
  run: forgelm --config configs/run.yaml --offline
```

Branch CI on the documented exit codes — see [`docs/reference/cache_subcommands.md#exit-codes`](../reference/cache_subcommands.md#exit-codes) and [`docs/reference/doctor_subcommand.md#exit-codes`](../reference/doctor_subcommand.md#exit-codes). Code `2` (runtime error) is retriable; code `1` (config error) is fix-and-fail.

## Audit trail

Every cache step writes to `audit_log.jsonl` in the resolved cache directory (override with `--audit-dir`). The full event vocabulary:

| Event | Emitter | Article | Trigger |
|---|---|---|---|
| `cache.populate_models_requested` | `cache-models` | 12 | Invocation begins. |
| `cache.populate_models_completed` | `cache-models` | 12 | Every model downloaded successfully. |
| `cache.populate_models_failed` | `cache-models` | 12 | One or more downloads failed mid-batch (carries `models_completed=[...]` so the operator knows what *did* land). |
| `cache.populate_tasks_requested` | `cache-tasks` | 12 | Invocation begins. |
| `cache.populate_tasks_completed` | `cache-tasks` | 12 | Every task dataset prepared successfully. |
| `cache.populate_tasks_failed` | `cache-tasks` | 12 | Unknown task name OR dataset download failure mid-batch. |

Audit-logger construction is best-effort: a connected staging machine without `FORGELM_OPERATOR` set sees a debug-level note and the run continues without auditing — the cache subcommands' value is in the on-disk artefacts. Pin `FORGELM_OPERATOR` on the staging host if your compliance program requires evidence of *who* staged the bundle.

## Common pitfalls

### "I set `--output` but `forgelm doctor --offline` reports an empty cache"

`--output` is a one-shot download target; `forgelm doctor --offline` and the trainer both read the env-var chain (`HF_HUB_CACHE > HF_HOME/hub > ~/.cache/huggingface/hub`). The cache subcommand emits a warning when the two diverge — read it. Either drop `--output` (lets the env-var chain win) or set `HF_HUB_CACHE=$(realpath your-output-dir)` to match.

### "Doctor passes but the trainer crashes with `local_files_only=True` not finding a model"

The model name in your YAML doesn't match the snapshot in the cache exactly. HF Hub IDs are case-sensitive (`meta-llama/Llama-Guard-3-8B` ≠ `meta-llama/llama-guard-3-8b`). Also: gated models require the same `HF_TOKEN` to be set during `cache-models` as during the actual training run; `forgelm doctor --offline` validates the **presence** of the snapshot, not the **license**.

### "FORGELM_CACHE_DIR doesn't seem to do anything"

Because it does not exist. ForgeLM does **not** define a `FORGELM_CACHE_DIR` env var (this was deliberately rejected as ghost-feature drift item GH-025). The canonical env vars are the standard HuggingFace ones: `HF_HUB_CACHE`, `HF_DATASETS_CACHE`, `HF_HOME`. Use those.

### "Datasets aren't found even though `HF_DATASETS_CACHE` is set"

Did you also set `HF_DATASETS_OFFLINE=1`? Without it, `datasets` may silently try to phone home and fall back to the local cache — and on a strict-egress network the *attempt* is itself a compliance violation, even though the data was retrieved locally.

### "I want to cache an entire org's models in one go"

That's not a supported flag (and is a footgun — most ForgeLM workflows only need a base + Llama Guard + maybe a teacher for synthetic data). Repeat `--model` instead, one Hub ID per flag:

```shell
$ forgelm cache-models \
    --model "meta-llama/Llama-3.2-3B" \
    --model "meta-llama/Llama-3.2-3B-Instruct" \
    --safety "meta-llama/Llama-Guard-3-8B"
```

### "My CI runner has no GPU; can I still cache?"

Yes. `cache-models` and `cache-tasks` only need network + disk + Python. `forgelm doctor` runs without a GPU (the `gpu.inventory` probe reports `warn` on CPU-only hosts, which doesn't block the verdict). The air-gapped target host needs the GPU; the staging host doesn't.

### "Do I need to bundle the Python packages too?"

Often, yes. The cache subcommands handle HF artefacts; Python wheels (`forgelm` + extras) are a separate problem. Use `pip download 'forgelm[eval]' -d ./airgap-bundle/wheels` (or `pip download 'forgelm[distributed]' -d ./airgap-bundle/wheels`) on the staging host and `pip install --no-index --find-links ./airgap-bundle/wheels 'forgelm[eval]'` (or `'forgelm[distributed]'`) on the target. Quote the extra-spec to keep zsh / bash from glob-expanding the brackets.

## See also

- [`docs/reference/cache_subcommands.md`](../reference/cache_subcommands.md) — full flag + exit-code + audit-event reference for `cache-models` and `cache-tasks`.
- [`docs/reference/doctor_subcommand.md`](../reference/doctor_subcommand.md) — full reference for `forgelm doctor --offline`.
- [Air-gap manual page](../usermanuals/en/operations/air-gap.md) — operator-facing summary that links here.
- [Getting Started](getting-started.md) — onboarding walkthrough whose air-gap variant is this guide.
- [`docs/reference/audit_event_catalog.md`](../reference/audit_event_catalog.md) §Air-gap pre-cache — full event vocabulary.
- [`docs/qms/access_control.md`](../qms/access_control.md) — recommended `FORGELM_OPERATOR` namespace scheme for staging hosts.
- [Enterprise deployment](enterprise_deployment.md) — adjacent operator playbook for hardened deployments.
