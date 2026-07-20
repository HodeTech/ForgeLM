# Programmatic ForgeLM Use

> **Audience:** ML engineers embedding ForgeLM in pipeline orchestrators (Airflow, Prefect, Dagster, Argo, Kubeflow), or working from notebooks where the CLI's process boundary is in the way.
>
> **Companion reference:** [`../reference/library_api_reference.md`](../reference/library_api_reference.md) — every public symbol, signature, and stability tier.
>
> **Design source:** [`../design/library_api.md`](../design/library_api.md) (Phase 18).

ForgeLM ships **two equally first-class entry points**: the `forgelm` console script (and `python -m forgelm.cli`) for shell pipelines, and the Python library API documented here for programmatic use. The CLI is the wrapper; the library is the engine. Anything the CLI does, the library can do — minus the exit-code mapping and the structured logging setup.

## When to use library API vs CLI

Choose the **CLI** when:

- You're shipping a Bash / GitHub Actions / GitLab CI pipeline. The exit-code contract (0/1/2/3/4/5/6) is the integration surface.
- You want the structured logging + JSON envelopes ForgeLM emits to stdout out of the box.
- You're running on infrastructure where one process per stage is the operational unit (most CI runners, most Argo pipelines).

Choose the **library API** when:

- You're orchestrating from Python: Airflow operators, Prefect tasks, Dagster ops, custom orchestrators.
- You need to compose multiple ForgeLM operations in a single Python process (audit → train → verify → notify) and the per-stage subprocess overhead matters.
- You're parameterising configurations programmatically (in-memory grid search, Bayesian sweeps) where round-tripping through YAML is friction.
- You want typed exceptions (`ConfigError`, `RuntimeError`, `OSError`) instead of exit codes.
- You're calling from a Jupyter notebook for interactive exploration.

A common hybrid: use the library API to produce + audit data and the CLI for the GPU training step, because the CLI's exit-code + auto-revert path is what your CI deployment gates against.

## Quick start

Install with the same wheel either entry point uses:

```bash
pip install 'forgelm[ingestion]'
# or for the security tooling:
pip install 'forgelm[ingestion,security]'
```

Smoke test that the lazy-import contract holds:

```python
import sys
import forgelm

# Importing the package does NOT pull torch — by contract.
assert "torch" not in sys.modules

# But the public surface is fully discoverable for autocomplete.
print("ForgeTrainer" in dir(forgelm))   # True
print(forgelm.__version__)              # e.g. "0.7.0"
print(forgelm.__api_version__)          # e.g. "1.0.0"
```

A runnable notebook (`notebooks/library_api_example.ipynb`) ships with the wheel and walks the same three patterns this page covers — see the design doc Phase 19 task #13 for its provenance.

## Lifecycle: load config → train → evaluate → emit audit events

The canonical library-mode pipeline mirrors the CLI's stage ordering. Each stage is a single `forgelm.<symbol>` call.

```python
import logging
import os
from forgelm import (
    AuditLogger,
    ForgeTrainer,
    audit_dataset,
    load_config,
    verify_audit_log,
)

# Library hygiene: configure the consumer logger explicitly.
# import forgelm does NOT call logging.basicConfig().
logging.basicConfig(level=logging.INFO)
logging.getLogger("forgelm").setLevel(logging.INFO)

# Operator identity is required (or set FORGELM_ALLOW_ANONYMOUS_OPERATOR=1
# for short-lived test runs only — see Common pitfalls).
os.environ.setdefault("FORGELM_OPERATOR", "airflow:dag-train:run-${RUN_ID}")

# 1. Load + validate config (raises ConfigError on invalid YAML).
config = load_config("configs/run.yaml")

# 2. Audit the corpus before training. The same gate the CLI's
#    `forgelm audit` subcommand walks.
report = audit_dataset(
    config.data.dataset_name_or_path,
    output_dir=config.training.output_dir,
    enable_pii_ml=True,
)
if report.near_duplicate_summary["pairs"] > 50 or report.pii_summary:
    raise SystemExit("data quality gate failed; fix before training")

# 3. Train. Heavy deps (torch, trl, transformers) load only on .train().
trainer = ForgeTrainer(config)
result = trainer.train()

# 4. Verify the audit chain after the run finishes — independent of
#    success/failure. A reverted run still leaves a valid chain.
output_dir = config.training.output_dir          # canonical: `training.output_dir`
verification = verify_audit_log(
    f"{output_dir}/audit_log.jsonl",             # audit_log.jsonl is at output_dir root, not under compliance/
    require_hmac=bool(os.environ.get("FORGELM_AUDIT_SECRET")),
)
if not verification.valid:
    raise SystemExit(f"audit chain broken: {verification.reason}")

# 5. Emit your own pipeline-orchestrator-specific event into the same
#    audit chain so the auditor can correlate ForgeLM run → orchestrator run.
#    `AuditLogger.run_id` is auto-generated when omitted; reuse the same
#    output_dir and the new event appends to the existing chain.
logger = AuditLogger(output_dir=output_dir)
logger.log_event(
    "training.completed",
    orchestrator="airflow",
    dag_id="train",
    run_id=os.environ["RUN_ID"],
    outcome="success" if result.success else "reverted",
)
```

## Common patterns

### Construct a config from a dict (no YAML)

`ForgeConfig` is a Pydantic model. Build it directly when you need parametric sweeps:

```python
from forgelm import ForgeConfig, ConfigError

base = {
    "model": {"name": "TinyLlama/TinyLlama-1.1B-Chat-v1.0"},
    "dataset": {"path": "data/train.jsonl", "format": "alpaca"},
    "training": {"trainer_type": "sft", "num_epochs": 1},
}

for lr in (1e-5, 2e-5, 5e-5):
    payload = {**base, "training": {**base["training"], "learning_rate": lr}}
    try:
        config = ForgeConfig(**payload)
    except ConfigError as exc:
        print(f"sweep cell rejected: {exc}")
        continue
    # ... feed into ForgeTrainer ...
```

### Standalone PII / secrets scrubbing inside a custom transform

The PII / secrets utilities are independent of the trainer. Use them as plain Python helpers:

```python
from forgelm import detect_pii, detect_secrets, mask_pii, mask_secrets

def scrub(record: dict) -> dict:
    text = record["text"]
    if detect_secrets(text):
        text = mask_secrets(text)
    if detect_pii(text):
        text = mask_pii(text)
    return {**record, "text": text}

scrubbed = [scrub(r) for r in raw_records]
```

All four take a single string and no language or locale argument — the patterns are one flat set with no locale dimension. `detect_pii` and `detect_secrets` return `Dict[str, int]` count maps (falsy when nothing was found, which is what the `if` above relies on); `mask_pii` replaces with `[REDACTED]` and `mask_secrets` with `[REDACTED-SECRET]`, both overridable via `replacement=`. Pass `return_counts=True` to either masker for a `(masked_text, counts)` tuple.

### Run the audit gate as an Airflow PythonOperator

```python
from airflow.operators.python import PythonOperator

def audit_corpus(**ctx):
    from forgelm import audit_dataset
    report = audit_dataset(
        "/data/customer-support-v3.jsonl",
        output_dir=f"/audit/{ctx['run_id']}",
        emit_croissant=True,
        workers=4,
    )
    if report.near_duplicate_summary["pairs"] > 100:
        raise ValueError(f"too many duplicates: {report.near_duplicate_summary['pairs']}")
    return {"samples": report.total_samples, "duplicates": report.near_duplicate_summary["pairs"]}

audit_task = PythonOperator(
    task_id="audit_corpus",
    python_callable=audit_corpus,
    provide_context=True,
)
```

### Verify a release artefact bundle in CI

```python
from forgelm import verify_annex_iv_artifact, verify_audit_log, verify_gguf

bundle_path = "outputs/v0.5.5/compliance/annex_iv_metadata.json"   # JSON manifest, not a ZIP
gguf_path = "outputs/v0.5.5/model.q4_K_M.gguf"
log_path = "outputs/v0.5.5/audit_log.jsonl"

bundle_check = verify_annex_iv_artifact(bundle_path)
gguf_check = verify_gguf(gguf_path)
log_check = verify_audit_log(log_path, require_hmac=True)

failures = [
    (name, check.reason)
    for name, check in (("bundle", bundle_check), ("gguf", gguf_check), ("audit", log_check))
    if not check.valid
]
if failures:
    raise SystemExit(f"release verification failed: {failures}")
```

### Drive a webhook notification without the trainer

```python
from forgelm import WebhookNotifier, load_config

config = load_config("configs/notification-only.yaml")
notifier = WebhookNotifier(config)
notifier.notify_start(run_name="manual-smoke-2026-05-06")
```

## Common pitfalls

These are the recurring shapes of "the library worked locally but my pipeline broke" support tickets.

### Forgetting to pin `FORGELM_OPERATOR`

The audit chain attributes every event to `$FORGELM_OPERATOR`. In CI, set a namespaced identifier per run:

```python
import os
os.environ["FORGELM_OPERATOR"] = (
    f"airflow:{os.environ['AIRFLOW_DAG_ID']}:{os.environ['AIRFLOW_RUN_ID']}"
)
```

If the variable is unset and `FORGELM_ALLOW_ANONYMOUS_OPERATOR` is **not** `1`, the run aborts loudly. That's by design — anonymous events are an ISO 27001 A.6.4 + A.6.5 audit finding.

### Calling `logging.basicConfig` twice

The library does **not** call `logging.basicConfig()`. Your application does. If you wrap a CLI run inside library code, the CLI's `_setup_logging` will not collide because the library never touches the root logger configuration.

### Treating `verify_audit_log` failures as exceptions

`verify_audit_log` returns `VerifyResult(valid=False, reason=...)` for chain failures. Only `OSError` propagates (unreadable file). Branch on `result.valid`, do not wrap in `try / except`.

### Sharing `AuditLogger` across forks

`AuditLogger` uses POSIX `fcntl.flock`; on Windows there is **no** cross-process lock (the advisory flock helper is a no-op). Sharing the file handle across `os.fork()` children is unsupported — each child must construct its own logger pointing at the same `output_dir`. On POSIX the chain stays consistent because all writes acquire the lock; on Windows, do not run concurrent processes against the same `output_dir` (use a distinct `output_dir` per run).

### Re-importing on a hot path

The lazy-import resolver writes resolved attributes back to `globals()` (PEP 562 idiomatic). Subsequent accesses skip the resolver. Don't `importlib.reload(forgelm)` inside a hot loop — it tears down the cache and forces every heavy dep to re-import.

### Mixing CLI subprocess + library calls

The library API and the CLI share the same wheel, the same audit log, and the same lock semantics. You can mix them in one pipeline (library audits the data, CLI runs training, library verifies the chain), but they must point at the **same output directory** for the chain to be coherent. Two output directories = two chains, no cross-correlation.

### Pinning to an `__api_version__` you didn't read

`__api_version__` only bumps when a stable-tier signature changes. Patch releases that fix CLI bugs do not change `__api_version__`. Pin against `__api_version__` for feature detection; pin against `__version__` for environment reproducibility.

### Promoting an experimental symbol in your contract

`forgelm.WebhookNotifier`, `forgelm.SyntheticDataGenerator`, `forgelm.run_benchmark`, `forgelm.compute_simhash` are explicitly Experimental. If your operator runbook depends on the current shape, pin to the current minor version of `forgelm` and read the [reference doc](../reference/library_api_reference.md) every release cycle.

## See also

- [`../reference/library_api_reference.md`](../reference/library_api_reference.md) — full symbol reference.
- [`../reference/audit_event_catalog.md`](../reference/audit_event_catalog.md) — every event `AuditLogger.log_event` accepts.
- [`../reference/configuration.md`](../reference/configuration.md) — `ForgeConfig` field-by-field reference.
- [`cicd_pipeline.md`](cicd_pipeline.md) — the CLI counterpart of this guide.
- [`iso_soc2_deployer_guide.md`](iso_soc2_deployer_guide.md) — audit-floor cookbook (library callers see the same artefacts).
- [`../design/library_api.md`](../design/library_api.md) — Phase 18 design.
