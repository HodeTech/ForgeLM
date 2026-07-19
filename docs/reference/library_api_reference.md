# ForgeLM Library API Reference

> **Audience:** ML platform engineers and integrators embedding ForgeLM in pipeline orchestrators (Airflow, Prefect, Dagster, Argo, Kubeflow) or invoking it from Jupyter notebooks. This page enumerates every public symbol re-exported from `forgelm`, classifies it by stability tier, and lists the signatures that downstream consumers may pin against.
>
> **Mirror:** [library_api_reference-tr.md](library_api_reference-tr.md)
>
> **Companion guide:** [`../guides/library_api.md`](../guides/library_api.md) — three end-to-end worked examples.
>
> **Design source:** [`../design/library_api.md`](../design/library_api.md) (Phase 18).

ForgeLM ships a Python library API alongside the `forgelm` console script. The library surface is declared in `forgelm/__init__.py` via `__all__`, lazy-resolved through PEP 562 `__getattr__`, and type-hinted under `TYPE_CHECKING` so downstream `mypy --strict` consumers see real signatures. `forgelm/py.typed` ships in the wheel as the PEP 561 marker.

## Stability tiers

Three tiers govern the semver weight of every public symbol. A consumer that pins to a specific tier knows what to expect from a `forgelm` upgrade.

### Stable

Semver-protected. A breaking change to any signature below requires a major version bump (`__api_version__` MAJOR.MINOR.PATCH — see [Versioning and deprecation policy](#versioning-and-deprecation-policy)). New optional parameters with defaults are non-breaking; renamed required parameters or removed return-shape fields are breaking.

Stable symbols are documented here, are 100% type-hinted, have at least one integration test under `tests/test_library_api.py`, and follow the deprecation cadence (deprecate in `N`, keep working in `N+1`, remove in `N+2`).

### Experimental

Best-effort. The shape may change in a minor release without a major bump. Operator copy at the call site flags lifecycle. Pin to a specific minor version if you depend on the current shape.

### Internal

Anything not in `forgelm.__all__` and not listed in the [Public symbols](#public-symbols) tables. Reach-ins (`from forgelm._http import ...`, `forgelm.cli._run_audit_cmd`) work at the language level but carry **zero** stability guarantee. File an issue requesting promotion if your pipeline depends on an internal symbol.

## Public symbols

Tables grouped by concern. Every cell is a real attribute on the live `forgelm` package after `import forgelm`.

### Versioning

| Symbol | Tier | Type | Description |
|---|---|---|---|
| `forgelm.__version__` | Stable | `str` | PEP 396/8 release version, derived from `importlib.metadata` (single source of truth = `pyproject.toml`). |
| `forgelm.__api_version__` | Stable | `str` | Three-segment semver library-API version (`"MAJOR.MINOR.PATCH"`, e.g. `"1.0.0"`). Bumped per the rules in `forgelm/_version.py`: MAJOR for removed/signature-changed stable symbols, MINOR for new stable symbols, PATCH for implementation-only changes. Use for feature detection in downstream code. |

### Configuration

| Symbol | Tier | Signature | Description |
|---|---|---|---|
| `forgelm.load_config` | Stable | `(path: str) -> ForgeConfig` | Parse a YAML file into a validated `ForgeConfig`. Raises `ConfigError` on validation failure. |
| `forgelm.ForgeConfig` | Stable | Pydantic `BaseModel` | Root config schema. Construct directly via `ForgeConfig(**dict_payload)` for in-memory parametric sweeps. |
| `forgelm.ConfigError` | Stable | `Exception` subclass | Raised by `load_config` and `ForgeConfig(**dict)` on validation failure. CLI dispatchers catch it and exit with code 1. |

### Training

| Symbol | Tier | Signature | Description |
|---|---|---|---|
| `forgelm.ForgeTrainer` | Stable | `ForgeTrainer(config: ForgeConfig)` | Primary training entry point. Wraps TRL `SFTTrainer` / `DPOTrainer` / `KTOTrainer` / `ORPOTrainer` / `GRPOTrainer` selection. |
| `forgelm.ForgeTrainer.train` | Stable | `train() -> TrainResult` | Run the configured fine-tune. Returns `TrainResult.success` / `metrics` / `final_model_path`. Heavy deps (`torch`, `transformers`, `trl`) load only when this method is called. |
| `forgelm.TrainResult` | Stable | `dataclass` | Result of `ForgeTrainer.train()`. Canonical fields (per `forgelm/results.py`): `success: bool`, `metrics: Dict[str, float]`, `final_model_path: Optional[str]`, `reverted: bool`, `error: Optional[str]`, `benchmark_scores`, `benchmark_average`, `benchmark_passed`, `safety_passed`, `safety_score`, `safety_categories`, `safety_severity`, `safety_low_confidence`, `judge_score`, `judge_details`, `estimated_cost_usd`, `staging_path`, `resource_usage`. |

### Data preparation

| Symbol | Tier | Signature | Description |
|---|---|---|---|
| `forgelm.prepare_dataset` | Experimental | `prepare_dataset(config: ForgeConfig, tokenizer: PreTrainedTokenizer) -> Dict[str, Any]` | Loads + format-detects + tokenises the configured dataset. Returns a splits dict (e.g. `{"train": ..., "validation": ...}`). The `datasets` minor surface drifts periodically, hence Experimental. |
| `forgelm.get_model_and_tokenizer` | Experimental | `get_model_and_tokenizer(config: ForgeConfig) -> tuple[PreTrainedModel, PreTrainedTokenizerBase]` | Loads HF model + tokenizer with the configured PEFT / quantization setup. |

### Data audit + PII / secrets / dedup

| Symbol | Tier | Signature | Description |
|---|---|---|---|
| `forgelm.audit_dataset` | Stable | `audit_dataset(source: str, *, output_dir: str \| None = None, near_dup_threshold: int = 3, dedup_method: str = "simhash", minhash_jaccard: float = 0.85, minhash_num_perm: int = 128, enable_quality_filter: bool = False, enable_pii_ml: bool = False, pii_ml_language: str = "en", emit_croissant: bool = False, workers: int = 1) -> AuditReport` | One-call data-audit entry point. Suitable for notebooks and CI gates. |
| `forgelm.AuditReport` | Stable | `dataclass` | Result of `audit_dataset`. Canonical fields (per `forgelm/data_audit/_types.py`): `generated_at`, `source_path`, `source_input`, `total_samples`, `splits`, `cross_split_overlap` (a dict-shaped row-level field, accessed by key), `pii_summary` (`Dict[str, int]`), `pii_severity`, `near_duplicate_summary` (key `pairs_per_split`, not `pairs`), `secrets_summary` (`Dict[str, int]`), `quality_summary`, `croissant`, `notes`. |
| `forgelm.detect_pii` | Stable | `detect_pii(text: str) -> Dict[str, int]` | Standalone PII detector. Returns a category → count dict (`{"email": 3, "phone": 1, ...}`). No `language` kwarg; the regex set is language-agnostic. |
| `forgelm.mask_pii` | Stable | `mask_pii(text: str, replacement: str = "[REDACTED]", *, return_counts: bool = False) -> str \| Tuple[str, Dict[str, int]]` | Mask detected PII spans in place. With `return_counts=True`, returns `(masked_text, counts_dict)`. |
| `forgelm.detect_secrets` | Stable | `detect_secrets(text: str) -> Dict[str, int]` | Standalone credential / API-key detector (AWS / GitHub / Slack / OpenAI / Google / JWT / private-key / Azure storage). Returns a family → count dict. |
| `forgelm.mask_secrets` | Stable | `mask_secrets(text: str) -> str` | Mask detected secrets in place. |
| `forgelm.compute_simhash` | Experimental | `compute_simhash(text: str) -> int` | 64-bit SimHash signature. Surface may collapse into a unified `compute_signature(method=...)` in a future release. |
| `forgelm.compute_minhash` | Experimental | `compute_minhash(text: str, *, num_perm: int = 128) -> Optional[Any]` | MinHash LSH signature object (returns `None` when the optional `datasketch` library is missing). Used internally by the `--dedup-method=minhash` audit path; the returned object is library-private — operators consume it via `audit_dataset(...)` rather than directly. Same Experimental tier as `compute_simhash`; both may collapse into a unified `compute_signature(method=...)` API. |

### Compliance + audit log

| Symbol | Tier | Signature | Description |
|---|---|---|---|
| `forgelm.AuditLogger` | Stable | `AuditLogger(output_dir: str, run_id: str \| None = None)` | Append-only Article 12 audit logger. POSIX uses `fcntl.flock`; on Windows there is **no** cross-process lock (the advisory flock helper is a no-op) — do not share an `output_dir` across concurrent processes on Windows. Each forked child must construct its own instance. |
| `forgelm.AuditLogger.log_event` | Stable | `log_event(event: str, **fields) -> None` | Append a structured event. The event vocabulary is documented in [`audit_event_catalog.md`](audit_event_catalog.md). |
| `forgelm.verify_audit_log` | Stable | `verify_audit_log(path: str, *, hmac_secret: str \| None = None, require_hmac: bool = False) -> VerifyResult` | Walk the SHA-256 hash chain. Returns `VerifyResult(valid=False, reason=...)` for chain failures (not an exception); raises `OSError` only for unreadable files. |
| `forgelm.VerifyResult` | Stable | `dataclass` | Canonical fields (per `forgelm/compliance.py:VerifyResult`): `valid: bool`, `entries_count: int`, `first_invalid_index: Optional[int]`, `reason: Optional[str]`. |

### Verification toolbelt (Phase 36)

Implemented in `forgelm/verify.py` (moved out of the `forgelm/cli/subcommands/_verify_*` modules that originally housed them — the CLI subcommands are now thin wrappers around this module). The import paths below are unchanged either way; `forgelm.verify_annex_iv_artifact` resolves the same symbol before and after the move, so this was an internal refactor, not an API change. Library callers who want the same 1-vs-6 exit-code classification the `verify-*` CLI subcommands use can import the paired `is_annex_iv_integrity_failure` / `is_gguf_integrity_failure` / `is_model_integrity_failure` predicates from `forgelm.verify` directly — they are internal (not in `forgelm.__all__`) but stable enough in practice that the CLI itself depends on them for the split documented in [`error-handling.md`](../standards/error-handling.md#exit-codes).

| Symbol | Tier | Signature | Description |
|---|---|---|---|
| `forgelm.verify_annex_iv_artifact` | Stable | `verify_annex_iv_artifact(path: str) -> VerifyAnnexIVResult` | Validate an Annex IV technical-documentation bundle (manifest + model card + audit log + governance report). |
| `forgelm.VerifyAnnexIVResult` | Stable | `dataclass-like` | Canonical attributes: `valid: bool`, `reason: Optional[str]`, `missing_fields: List[str]`, `manifest_hash_actual: Optional[str]`, `manifest_hash_expected: Optional[str]`. (Note: `verify_annex_iv_artifact(path)` accepts a JSON manifest path, not a ZIP — the function calls `json.load` on `path`.) |
| `forgelm.verify_gguf` | Stable | `verify_gguf(path: str) -> VerifyGgufResult` | Validate a GGUF export (header + tensor catalogue + tokenizer block). |
| `forgelm.VerifyGgufResult` | Stable | `dataclass-like` | Canonical attributes: `valid: bool`, `reason: Optional[str]`, `checks: Dict[str, Any]` (everything else — header / tensor catalogue / tokenizer block — lives inside `checks`). |
| `forgelm.verify_integrity` | Stable | `verify_integrity(model_dir: str) -> VerifyIntegrityResult` | Verify a model directory against its `model_integrity.json` SHA-256 manifest (Article 15): reports changed / removed / added artifacts. |
| `forgelm.VerifyIntegrityResult` | Stable | `dataclass-like` | Canonical attributes: `valid: bool`, `reason: Optional[str]`, `changed: List[str]`, `removed: List[str]`, `added: List[str]`, `verified_count: int`. |

### Benchmark + synthetic data

| Symbol | Tier | Signature | Description |
|---|---|---|---|
| `forgelm.run_benchmark` | Experimental | `run_benchmark(model, tokenizer, tasks: List[str], num_fewshot: Optional[int] = None, batch_size: str = "auto", limit: Optional[int] = None, output_dir: Optional[str] = None, min_score: Optional[float] = None) -> BenchmarkResult` | Wraps `lm-eval-harness`. Requires the `[eval]` extra. |
| `forgelm.BenchmarkResult` | Experimental | `dataclass-like` | Canonical fields: `scores: Dict[str, float]`, `average_score: float`, `passed: bool`, `failure_reason: Optional[str]`, `raw_results: Dict[str, Any]`. |
| `forgelm.SyntheticDataGenerator` | Experimental | `SyntheticDataGenerator(config: ForgeConfig)` | Teacher-distillation generator. The `teacher_backend in {"api", "local", "file"}` switch will likely grow new modes. |

### Auxiliary

| Symbol | Tier | Signature | Description |
|---|---|---|---|
| `forgelm.WebhookNotifier` | Experimental | `WebhookNotifier(config: ForgeConfig)` | Slack / Teams / generic-HTTP lifecycle notifications. Constructor schema may grow ISO/SOC 2 fields in a future release. |
| `forgelm.setup_authentication` | Experimental | `setup_authentication(token: Optional[str] = None) -> None` | Wrapper around `huggingface_hub.login`. Reads `HUGGINGFACE_TOKEN` env var when `token` is `None`. |
| `forgelm.manage_checkpoints` | Experimental | `manage_checkpoints(checkpoint_dir: str, action: str = "keep") -> None` | Apply checkpoint-retention behaviour against an output directory. `action` controls retain/prune semantics. |

## Idiomatic usage examples

Worked snippets covering the most common library-mode entry points. All imports below resolve directly via `from forgelm import ...`.

### 1. Audit a corpus from a notebook

```python
from forgelm import audit_dataset

report = audit_dataset(
    "data/customer_support.jsonl",
    output_dir="audit_out",
    enable_pii_ml=True,
    pii_ml_language="en",
    emit_croissant=True,
)

print(f"samples: {report.total_samples}")
pairs = report.near_duplicate_summary.get('pairs_per_split', {})
print(f"near-duplicates: {pairs}")
print(f"pii summary: {report.pii_summary}")
# cross_split_overlap is dict[str, Any], access by key
print(f"split overlap pairs: {report.cross_split_overlap.get('pairs', {})}")
```

### 2. Verify an audit log in CI

```python
from forgelm import verify_audit_log

result = verify_audit_log(
    "outputs/run-001/audit_log.jsonl",
    hmac_secret=None,
    require_hmac=True,
)

if not result.valid:
    raise SystemExit(f"audit chain broken: {result.reason}")

print(f"verified {result.entries_count} entries")
```

### 3. Train end-to-end (pure Python, no YAML)

```python
from forgelm import ForgeConfig, ForgeTrainer

config = ForgeConfig(
    model={"name_or_path": "TinyLlama/TinyLlama-1.1B-Chat-v1.0"},
    lora={"r": 8, "alpha": 16, "target_modules": ["q_proj", "v_proj"]},
    data={"dataset_name_or_path": "data/train.jsonl"},
    training={
        "trainer_type": "sft",
        "num_train_epochs": 1,
        "per_device_train_batch_size": 1,
        "output_dir": "./checkpoints/quick",
    },
)

trainer = ForgeTrainer(config)
result = trainer.train()

print(f"success={result.success}  output={result.final_model_path}")
if not result.success and result.reverted:
    print(f"reverted; reason: {result.error}")
```

The keys above are the **only** required ones; everything else falls back to `forgelm/config.py` defaults. `model.name_or_path`, the `lora:` block, `data.dataset_name_or_path`, and `training.{trainer_type, output_dir}` are required by the Pydantic schema; `num_epochs` / `batch_size` are not the canonical names and would raise `ValidationError`.

### 4. Emit Article 12 audit events from your own pipeline

```python
import os
from forgelm import AuditLogger

os.environ.setdefault("FORGELM_OPERATOR", "airflow:dag-1234:run-5678")

logger = AuditLogger(output_dir="outputs/dag-1234")
logger.log_event(
    "training.started",
    trainer_type="sft",
    model="meta-llama/Llama-3.1-8B-Instruct",
    dataset="acme/customer-support-v3",
)
# ... your pipeline runs ...
logger.log_event(
    "pipeline.completed",
    exit_code=0,
    duration_seconds=4218.7,
    success=True,
    metrics_summary={"eval_loss": 0.43, "rouge_l": 0.61},
)
```

### 5. PII / secrets detection on free-form input

```python
from forgelm import detect_pii, detect_secrets, mask_pii, mask_secrets

text = "Contact alice@example.com or use AKIAIOSFODNN7EXAMPLE for the call."

pii = detect_pii(text)
secrets = detect_secrets(text)

print(f"pii: {pii}")
print(f"secrets: {secrets}")

masked = mask_secrets(mask_pii(text))
print(masked)
```

## Lazy-import discipline

Importing the package facade is **cheap by contract**: `import forgelm` does **not** load `torch`, `transformers`, `trl`, `datasets`, `peft`, or any other heavy ML dependency. Only `importlib.metadata` and a tiny module-level state dict are touched.

Heavy attributes resolve on first access via PEP 562 `__getattr__`:

```python
import sys
import forgelm

assert "torch" not in sys.modules           # contract — pinned in CI

_ = forgelm.ForgeTrainer                    # imports forgelm.trainer, but
assert "torch" not in sys.modules           # forgelm.trainer also defers torch

trainer = forgelm.ForgeTrainer(config)      # constructor still cheap
result = trainer.train()                    # NOW torch loads
```

This invariant exists because lightweight CI runners, `forgelm doctor`, and `python -m forgelm.cli --help` must respond instantly. `tests/test_library_api.py::test_lazy_import_no_torch` regression-pins it.

## Concurrency

| Symbol | Multi-threaded? | Fork-safe after construction? |
|---|---|---|
| `ForgeTrainer.train()` | No — TRL holds GPU state | No |
| `audit_dataset()` | Yes — each call is self-contained | Yes |
| `AuditLogger.log_event()` | Yes on POSIX — `flock(LOCK_EX)`; on Windows there is no cross-process lock (no-op), so do not share an `output_dir` across processes | Construct a fresh logger per child; sharing handles across forks is unsupported |
| `verify_audit_log()` | Yes — read-only | Yes |
| `WebhookNotifier.notify_*()` | Yes — each call opens its own `requests` session | Yes |

## Error handling at the library boundary

CLI dispatchers map exceptions to public exit codes (0/1/2/3/4). **Library callers do not see exit codes** — typed exceptions propagate.

| Symbol | Errors propagated as |
|---|---|
| `ForgeTrainer.train()` | `ConfigError` (validation), `RuntimeError` (CUDA / training-loop), `OSError` (I/O) |
| `audit_dataset()` | `ValueError` (invalid args), `OSError` (I/O), `OptionalDependencyError` (missing extra) |
| `verify_audit_log()` | Returns `VerifyResult(valid=False, reason=...)` for chain failures; raises `OSError` only for unreadable files |
| `AuditLogger.log_event()` | `OSError` on write failure (caller decides retry vs abort) |

Library code never calls `sys.exit`. Every exit-code mapping lives in CLI dispatchers.

## Logging in library mode

`import forgelm` does **not** call `logging.basicConfig()`. Configure the consumer logger explicitly:

```python
import logging
logging.getLogger("forgelm").setLevel(logging.WARNING)  # quiet by default in libraries
```

The CLI does its own setup in `forgelm.cli._setup_logging`; the library leaves it to the caller (PEP 8 / `logging` HOWTO library hygiene).

## Versioning and deprecation policy

Two independent version strings:

| Variable | Bumps when... | Read by... |
|---|---|---|
| `forgelm.__version__` | Every release (CLI fix, library fix, doc-only release) | Downstream pinning, audit manifest stamp |
| `forgelm.__api_version__` | A stable-tier signature changes | Downstream feature detection |

`__api_version__` is a three-segment semver string (`"1.0.0"` at v0.5.5 — first publication of the formal Phase 19 contract). Bump rules live in `forgelm/_version.py`: MAJOR on removed / signature-changed stable symbols, MINOR on new stable symbols, PATCH on implementation-only changes that don't touch the public surface.

**Deprecation cadence** (per `docs/standards/release.md`):

1. Mark the old symbol with a `DeprecationWarning` in release `N`. The warning must include the replacement symbol name and the planned-removal version.
2. Keep it working in release `N+1`.
3. Remove in release `N+2`.

A breaking change to a stable signature without following the cadence is a release-process bug.

## See also

- [`../guides/library_api.md`](../guides/library_api.md) — three end-to-end worked examples.
- [`audit_event_catalog.md`](audit_event_catalog.md) — full event vocabulary `AuditLogger.log_event` accepts.
- [`configuration.md`](configuration.md) — `ForgeConfig` field reference.
- [`../design/library_api.md`](../design/library_api.md) — Phase 18 design + 16-row Phase 19 task plan.
- [`../standards/release.md`](../standards/release.md) — deprecation cadence and release process.
