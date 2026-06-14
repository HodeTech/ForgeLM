# ForgeLM Library API — Design

> **Scope:** Design specification for ForgeLM as a Python library (the
> `from forgelm import ...` import surface). Defines stability tiers,
> lazy-import discipline, semver scope (`__api_version__`), and the
> documentation + test strategy for the library API. Living spec —
> kept in sync with the implementation under `forgelm/__init__.py`,
> `forgelm/_version.py`, and `tests/test_library_api.py`.

**Status:** Implemented in v0.5.5. See `CHANGELOG.md` and
`docs/guides/library_api.md` for the user-facing surface.

---

## 0. Why this document exists

ForgeLM ships a CLI today (`forgelm <subcommand>`).  The same modules — `ForgeTrainer`, `audit_dataset`, `verify_audit_log`, the PII / secrets utilities, the webhook notifier — are also useful as a Python library, but the import surface, the type contract, the lazy-import discipline, the semver scope, the documentation and the test strategy were never explicitly designed for that use.  The result is a *de facto* library with no contract: `from forgelm import ForgeTrainer` works because of an existing `__getattr__` shim, but nothing in the codebase tells a downstream consumer **which** symbols are stable, **which** are experimental, and **which** are internal.

This design pins those answers down so Phase 19 can implement them without a second round of "what should we even do here?" discovery.

The decisions below are written so an engineer reading just this document can implement Phase 19 from scratch.  Where ForgeLM already does the right thing (e.g. `forgelm/__init__.py` already lazy-imports `ForgeTrainer` via `__getattr__`) the design records *that* fact and explains why it is the chosen pattern rather than rewriting it.

---

## 1. Current public surface (audit of `forgelm/__init__.py`)

`forgelm/__init__.py` currently exposes the following names through `__all__` + `__getattr__`:

| Name | Source module | Imported lazily? | Heavy deps unlocked when accessed |
|---|---|---|---|
| `__version__` | `importlib.metadata` | (eager — pure stdlib) | none |
| `load_config` | `forgelm.config` | eager | Pydantic (lightweight) |
| `ForgeConfig` | `forgelm.config` | eager | Pydantic |
| `ConfigError` | `forgelm.config` | eager | Pydantic |
| `prepare_dataset` | `forgelm.data` | lazy via `__getattr__` | `datasets`, `transformers` |
| `get_model_and_tokenizer` | `forgelm.model` | lazy | `torch`, `transformers`, `peft` |
| `ForgeTrainer` | `forgelm.trainer` | lazy | `torch`, `transformers`, `trl` |
| `TrainResult` | `forgelm.results` | lazy | (lightweight dataclass) |
| `run_benchmark` | `forgelm.benchmark` | lazy | `lm-eval` (optional `[eval]` extra) |
| `BenchmarkResult` | `forgelm.benchmark` | lazy | (lightweight dataclass) |
| `setup_authentication` | `forgelm.utils` | lazy | `huggingface_hub` |
| `manage_checkpoints` | `forgelm.utils` | lazy | (stdlib only) |
| `SyntheticDataGenerator` | `forgelm.synthetic` | lazy | `transformers` (when `teacher_backend == "local"`) |

Things that are **NOT** exposed today but are reachable through `forgelm.<submodule>.<name>`:

- `audit_dataset` — `forgelm.data_audit.audit_dataset` (a single-call entry point that already exists; only the package-root re-export is missing).
- `verify_audit_log` — `forgelm.compliance.verify_audit_log`.
- `AuditLogger` — `forgelm.compliance.AuditLogger`.
- The PII / secrets utility belt: `detect_pii`, `mask_pii`, `detect_secrets`, `mask_secrets`, `compute_simhash` — all under `forgelm.data_audit.*`.
- `WebhookNotifier` — `forgelm.webhook.WebhookNotifier`.

These are valid use cases.  An operator who wants to run a one-off audit from a Jupyter notebook should not have to learn the `forgelm.data_audit._orchestrator` import path; `from forgelm import audit_dataset` is the right shape.

---

## 2. Stability tiers

Not every public symbol carries the same semver weight.  The library API gets three tiers; downstream consumers need to know **which tier each name belongs to** so they can pin appropriately.

### 2.1 Stable (semver-protected)

A breaking change to any symbol below requires a **major version bump** (`v0.x.0 → v1.0.0` once we cut 1.0; until then we follow `0.MINOR.0` semantics where MINOR bumps signal breakages).  A non-breaking change requires a minor bump.

| Symbol | Why stable |
|---|---|
| `forgelm.ForgeTrainer` | Primary training entry point.  Downstream pipelines instantiate it. |
| `forgelm.ForgeTrainer.__init__(config: ForgeConfig)` | Constructor signature is the contract. |
| `forgelm.ForgeTrainer.train() -> TrainResult` | Return shape is the contract. |
| `forgelm.ForgeConfig` | Pydantic schema — every field is a published config knob. |
| `forgelm.load_config(path: str) -> ForgeConfig` | YAML loader, used in every CLI invocation. |
| `forgelm.audit_dataset(source: str, *, output_dir: Optional[str] = None, near_dup_threshold: int = 3, dedup_method: str = "simhash", minhash_jaccard: float = 0.85, minhash_num_perm: int = 128, enable_quality_filter: bool = False, enable_pii_ml: bool = False, pii_ml_language: str = "en", emit_croissant: bool = False, workers: int = 1) -> AuditReport` | Called from notebooks + CI gates.  Signature audited against live `forgelm/data_audit/_orchestrator.py:audit_dataset` (Phase 17 added `workers`). |
| `forgelm.verify_audit_log(path: str, *, hmac_secret: Optional[str] = None, require_hmac: bool = False) -> VerifyResult` | CI gate; `VerifyResult` + the `hmac_secret` parameter (audited from live `forgelm/compliance.py:verify_audit_log`) are part of the contract. |
| `forgelm.AuditLogger(output_dir: str, run_id: str = None)` | Used by integrators to emit Article 12 records from their own pipelines. |
| `forgelm.AuditLogger.log_event(event: str, **fields)` | Append-only contract. |
| `forgelm.detect_pii`, `forgelm.mask_pii`, `forgelm.detect_secrets`, `forgelm.mask_secrets` | Standalone PII / secrets utilities; no surrounding pipeline required. |
| `forgelm.TrainResult`, `forgelm.BenchmarkResult`, `forgelm.AuditReport`, `forgelm.VerifyResult` | Result dataclasses — fields are the contract. |
| `forgelm.ConfigError` | Exception type the wizard / CLI may catch from caller code. |
| `forgelm.__version__` | PEP 396 / 8 string. |

Stable symbols **are documented**, **are type-hinted**, **have at least one integration test**, and **carry a `# deprecated:` block + `DeprecationWarning` for at least one minor cycle before removal** (see `docs/standards/release.md` "Deprecation cadence").

### 2.2 Experimental (best-effort, may break in a minor release)

Symbols that are intentionally exposed but whose shape we expect to keep adjusting.  Documented as such; pinning to a specific minor version is the consumer's responsibility.

| Symbol | Why experimental |
|---|---|
| `forgelm.WebhookNotifier` | Integrators sometimes want webhook lifecycle events without the trainer; surface is correct but not yet ergonomic. |
| `forgelm.run_benchmark` | Wraps `lm-eval`; that surface is the unstable layer below us. |
| `forgelm.SyntheticDataGenerator` | API works but the `teacher_backend in {"api", "local", "file"}` switch will likely grow new modes. |
| `forgelm.compute_simhash`, `forgelm.compute_minhash` | Internal helpers we expose because notebooks find them useful, but the signature may collapse into a single `compute_signature(method=...)`. |
| `forgelm.setup_authentication` | Wrapper around `huggingface_hub.login`; will likely move to a `ForgeAuthContext` class. |
| `forgelm.prepare_dataset` | Returns a `datasets.Dataset`; the `datasets` minor changes pull the rug periodically. |
| `forgelm.get_model_and_tokenizer` | The `(model, tokenizer)` tuple form is convenient but the signature reaches into HF kwargs we cannot freeze. |

Experimental symbols are documented under `docs/reference/library_api.md#experimental` with an explicit "subject to change" callout.

### 2.3 Internal (private — `_`-prefixed)

Anything starting with an underscore at any level (`forgelm._http`, `forgelm.cli._parser`, `forgelm.data_audit._aggregator`, etc.) is internal.  Consumers that import these have implicitly opted out of the contract; we may change them in any release without notice.

**Internal classification rule of thumb (audit-friendly):** a name is *internal* iff it is **not** in `forgelm.__all__` **and** does not appear in `dir(forgelm)` after a fresh `import forgelm`.  Note that this is a property of the *package facade*, not of import-protection — `forgelm/_http.py` exists as a real submodule and `from forgelm import _http` succeeds at the language level, but it is internal because `_http` is absent from the public surface listings.

**Tier completeness (audited 2026-05-02).**  Every top-level Python module + sub-package has an explicit classification:

| Module / sub-package | Tier | Notes |
|---|---|---|
| `forgelm/__init__.py` re-exports listed in §2.1 / §2.2 | Stable / Experimental | per the per-symbol tables |
| `forgelm.config` | Stable | Pydantic schema is the contract; field changes follow deprecation cadence |
| `forgelm.trainer`, `forgelm.model`, `forgelm.data` | Stable (via re-export) | accessed through `forgelm.ForgeTrainer` / `forgelm.get_model_and_tokenizer` / `forgelm.prepare_dataset` |
| `forgelm.compliance` | Mixed: `verify_audit_log` + `AuditLogger` + `VerifyResult` Stable; rest Internal | re-exported names follow §2.1 |
| `forgelm.data_audit` (the package) | Mixed: `audit_dataset` + `AuditReport` + PII / secrets / simhash utilities Stable; everything under `_*.py` Internal | per-symbol re-exports follow §2.1 |
| `forgelm.benchmark` | Experimental | `run_benchmark` + `BenchmarkResult` re-exported |
| `forgelm.synthetic` | Experimental | `SyntheticDataGenerator` re-exported |
| `forgelm.webhook` | Experimental | `WebhookNotifier` re-exported (per §2.2 + §10 Q5) |
| `forgelm.utils` | Experimental | `setup_authentication`, `manage_checkpoints` re-exported |
| `forgelm.results` | Stable | `TrainResult` re-exported |
| `forgelm.safety` | **Internal today** (no re-export) | direct `forgelm.safety.run_safety_evaluation` reach-in is undocumented; Phase 36 promotes it to Stable via re-export when the standalone subcommand ships |
| `forgelm.judge` | **Internal today** (no re-export) | callers reach in via `forgelm.judge.run_judge_evaluation`; not stable until ranked into a future re-export |
| `forgelm.ingestion` (the module) | **Internal today** (no re-export) | `OptionalDependencyError` + `ingest_path` reachable via direct submodule import; promotion to Experimental is a Phase 19 follow-up decision |
| `forgelm.export`, `forgelm.deploy`, `forgelm.merging`, `forgelm.inference`, `forgelm.fit_check`, `forgelm.chat`, `forgelm.quickstart`, `forgelm.wizard`, `forgelm.model_card`, `forgelm.grpo_rewards` | **Internal** | CLI dispatchers; reach-in is unsupported. The CLI is the public surface for these features. |
| `forgelm.cli` (the package) | Internal | `forgelm` console script + `python -m forgelm.cli` are the public entries; the dispatchers themselves are internal even though tests reach in via `forgelm.cli._run_*_cmd` |
| `forgelm.templates` | Internal data | template registry; consumers should use `forgelm quickstart --list` |

If a downstream consumer needs a name currently marked Internal in this table, the right path is to file an issue requesting promotion (and, ideally, supplying a use case + signature stability rationale).  We do **not** silently honour reach-ins as load-bearing.

The CLI dispatchers themselves (`_run_chat_cmd`, `_run_audit_cmd`, etc.) are internal — the public CLI is the binary entry, not these helpers.  Tests reach in via `forgelm.cli._run_*_cmd` for monkeypatch convenience; that does not promote them to the public API.

---

## 3. Type contract — `py.typed` + PEP 561

ForgeLM ships `forgelm/py.typed` (zero-byte marker) so installers / `mypy` / IDEs know to use the inline type hints.  Without the marker, downstream `mypy --strict` runs treat every `forgelm.*` import as `Any` and lose all signal.

### 3.1 Coverage targets

| Surface | Type-hint coverage required |
|---|---|
| Stable symbols (§2.1) | 100% — every parameter + return.  `mypy --strict forgelm/__init__.py` clean. |
| Experimental symbols (§2.2) | 100% on the public signature; internal helpers may stay loose. |
| Internal symbols (§2.3) | Best-effort.  Don't gate CI on internal mypy errors. |

### 3.2 Enforcement

A new CI step runs `mypy --strict --follow-imports=silent forgelm/__init__.py forgelm/_version.py` (note: only the public re-exports, not the entire codebase — `--strict` on `forgelm/trainer.py` would require typing the entire `transformers` / `trl` surface, which is not our project to fix).

Failures block the PR.  This is a tighter gate than the existing `ruff check` because type-hint regressions are silent at runtime but break IDE completion.

### 3.3 Forward-reference patterns

A few public symbols return objects from heavy dependencies (`datasets.Dataset`, `transformers.PreTrainedModel`).  We use:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datasets import Dataset
    from transformers import PreTrainedModel, PreTrainedTokenizerBase

def get_model_and_tokenizer(
    config: ForgeConfig,
) -> "tuple[PreTrainedModel, PreTrainedTokenizerBase]": ...
```

The string-quoted return type lets us declare the contract without forcing `transformers` to be imported at module load.

---

## 4. Lazy-import discipline

### 4.1 The invariant: `import forgelm` does not load torch

The single most important lazy-import rule:

> Importing `forgelm` (or any of its public re-export modules) **must not** import `torch`, `transformers`, `trl`, `datasets`, or any other heavy ML dep.  Heavy deps are loaded only when the consumer accesses a symbol that genuinely needs them.

This rule exists because:
- CI runners that only check our config (`ruff`, the lightweight `pytest` subset) don't need `torch`.
- The CLI entry point `python -m forgelm.cli --help` must respond instantly.
- `forgelm doctor` (Phase 34) is meant to run on a brand-new machine *before* `torch` is even installed, so it cannot crash on `import forgelm`.

**Today (audited 2026-05-02):** the invariant holds by inspection but is *not* pinned by an existing regression test.  `tests/test_lazy_imports.py` only covers `import forgelm.trainer` and `import forgelm.model`; bare `import forgelm` is verified manually but lacks a CI gate.  **Phase 19 task #7 closes this gap** by shipping `tests/test_library_api.py::test_lazy_import_no_torch` (subprocess `python -c "import forgelm; assert 'torch' not in sys.modules"`) so a future eager-import regression in `forgelm/__init__.py` fails CI.

### 4.2 Implementation pattern: `__getattr__` per facade

`forgelm/__init__.py` uses `__getattr__` to defer imports until the symbol is actually accessed.  Phase 19 extends the same pattern to every name listed in §2.1 + §2.2.  Three shapes work together:

1. A **`TYPE_CHECKING` import block** so `mypy --strict` (and IDE indexers) see the *real* signatures rather than `Any`.  Without this, the "100 % typed stable surface" claim in §3.2 is vacuously true at the type-checker layer because `__getattr__` returns `Any`.
2. A **lookup table + `__getattr__`** that performs the on-demand import.  Resolved attributes are bound back onto the module so subsequent accesses skip `__getattr__` entirely (Python's documented behaviour: `__getattr__` is consulted only when the attribute is *missing*; once bound it is a normal attribute access).
3. A `__dir__` companion that lists the same names so `dir(forgelm)` and IDE auto-complete return the full public surface before any lazy access has been made.

```python
# forgelm/__init__.py — Phase 19 sketch

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # These imports run only under mypy / IDE static analysis; never at runtime.
    from .compliance import AuditLogger, VerifyResult, verify_audit_log
    from .data_audit import (
        AuditReport,
        audit_dataset,
        compute_simhash,
        detect_pii,
        detect_secrets,
        mask_pii,
        mask_secrets,
    )
    from .webhook import WebhookNotifier
    # ... existing entries (ForgeTrainer, get_model_and_tokenizer, ...)

# Single source of truth: name → (submodule, attribute).  Shared with __dir__.
_LAZY_SYMBOLS: dict[str, tuple[str, str]] = {
    "audit_dataset": (".data_audit", "audit_dataset"),
    "verify_audit_log": (".compliance", "verify_audit_log"),
    "AuditLogger": (".compliance", "AuditLogger"),
    "VerifyResult": (".compliance", "VerifyResult"),
    "AuditReport": (".data_audit", "AuditReport"),
    "WebhookNotifier": (".webhook", "WebhookNotifier"),
    "detect_pii": (".data_audit", "detect_pii"),
    "mask_pii": (".data_audit", "mask_pii"),
    "detect_secrets": (".data_audit", "detect_secrets"),
    "mask_secrets": (".data_audit", "mask_secrets"),
    "compute_simhash": (".data_audit", "compute_simhash"),
    # ... existing ForgeTrainer / get_model_and_tokenizer / ... entries
}


def __getattr__(name: str):
    target = _LAZY_SYMBOLS.get(name)
    if target is None:
        raise AttributeError(f"module 'forgelm' has no attribute {name!r}")
    import importlib
    module = importlib.import_module(target[0], package=__name__)
    value = getattr(module, target[1])
    # Bind back onto the module so subsequent accesses bypass __getattr__
    # entirely.  Python's data model guarantees __getattr__ is only consulted
    # for missing attributes; a real attribute set wins forever after.
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals().keys(), *_LAZY_SYMBOLS.keys()})
```

### 4.3 CLI facade lazy migration: `forgelm/cli/__init__.py`

The CLI facade currently has ~200 lines of eager `from .submodule import ...` calls (23 import statements pulling 50+ names; verified 2026-05-04 against `forgelm/cli/__init__.py`).  That makes `import forgelm.cli` heavyweight even though most of those subcommand dispatchers are not needed for any single command.

**Decision:** Phase 19 ports the same `__getattr__` + `__dir__` pattern to `forgelm/cli/__init__.py`.  A symbol → submodule lookup table replaces the eager imports.  The shape:

```python
# forgelm/cli/__init__.py — sketch for Phase 19

# Names that tests / monkeypatch / external pipelines reach via
# `forgelm.cli._run_<cmd>_cmd`.  Maps the public attribute name
# to its source submodule (relative to forgelm.cli).
_SYMBOL_TO_MODULE: dict[str, str] = {
    "main": "._dispatch",
    "_dispatch_subcommand": "._dispatch",
    "parse_args": "._parser",
    "_run_chat_cmd": ".subcommands._chat",
    "_run_audit_cmd": ".subcommands._audit",
    "_run_approve_cmd": ".subcommands._approve",
    # ... all currently-eager imports listed here ...
}

def __getattr__(name: str):
    target_module = _SYMBOL_TO_MODULE.get(name)
    if target_module is None:
        raise AttributeError(f"module 'forgelm.cli' has no attribute {name!r}")
    import importlib
    submodule = importlib.import_module(target_module, package=__name__)
    return getattr(submodule, name)

def __dir__() -> list[str]:
    return sorted(_SYMBOL_TO_MODULE)
```

**Critical: monkeypatch resolution.**  Tests do `patch("forgelm.cli._run_chat_cmd", ...)`.  Python's `unittest.mock.patch` resolves the dotted path by `getattr(forgelm.cli, "_run_chat_cmd")`, which triggers `__getattr__`, which imports the submodule and returns the real callable.  `mock.patch` then **rebinds the name on `forgelm.cli`** for the duration of the test.  This works because `__getattr__` is only consulted for **missing** attributes — once a name is set on the module, `__getattr__` is bypassed.  The pattern is monkeypatch-safe.

**Honest scoping.**  The win is **not** "the entire CLI dispatch path is now lazy".  `forgelm/cli/_dispatch.py` itself eagerly imports `_config_load`, `_logging`, `_no_train_modes`, `_parser`, `_training`, `_wizard`; any actual CLI invocation walks that subset of submodules during `parse_args`.  The lazy facade saves load cost for the **narrow case where a test reaches `forgelm.cli._run_<cmd>_cmd` directly** without first invoking `parse_args`/`main`, plus the cosmetic improvement of `import forgelm.cli` no longer pulling all 9 subcommand dispatchers when only one is needed.  Phase 19 frames this as **hygiene**, not a measured cold-import speed-up; if the operator wants the latter, a separate benchmark + scoping pass goes into a future phase.

We add a regression test (`tests/test_cli_lazy_imports.py`) that:
1. Imports `forgelm.cli` and asserts `"forgelm.cli.subcommands._chat" not in sys.modules`.
2. Accesses `forgelm.cli._run_chat_cmd` and asserts the submodule **is** now in `sys.modules`.
3. Re-imports `forgelm.cli` after `monkeypatch.setattr("forgelm.cli._run_chat_cmd", lambda *a, **kw: ...)`, asserts the patch is honoured.

---

## 5. Versioned API contract

### 5.1 Two version strings

> **Status: Implementation diverged from this section.** The design doc
> as drafted (Phase 18) committed to a 2-segment `__api_version__` (e.g.
> `"0.5"`).  After drafting, [`docs/standards/release.md`](../standards/release.md)
> §"`__api_version__` (Python library surface)" (lines 27-39) codified
> the canonical shape as **3-segment semver** (e.g. `"1.0.0"`) with
> explicit MAJOR / MINOR / PATCH bump rules, and the implementation in
> [`forgelm/_version.py`](../../forgelm/_version.py) lines 32-39
> follows the codified standard (`__api_version__ = "1.0.0"` at v0.5.5).
> Defer to `release.md` for the public contract; the 2-segment shape
> below is preserved here for historical context only.

ForgeLM today has a single `__version__` derived from `importlib.metadata`.  Phase 19 introduces a clean separation:

| Variable | Source | Bumps when... | Read by... |
|---|---|---|---|
| `forgelm.__version__` | `importlib.metadata` (single source of truth = `pyproject.toml`) | every release | downstream pinning, audit manifest stamp |
| `forgelm._version.__api_version__` | hand-maintained string in `forgelm/_version.py` | a stable-tier symbol's signature changes | downstream feature-detection |

`__api_version__` exists because the CLI surface and the library surface evolve at different speeds.  A patch release that fixes a CLI typo doesn't change the Python-importable contract; both bump `__version__` but only the latter bumps `__api_version__`.  Consumers that pin against the library API can look at `__api_version__` and know whether a `forgelm` upgrade is safe.

The convention (as originally drafted — superseded by `release.md`'s 3-segment semver, see Status callout above):

```text
__api_version__ = "MAJOR.MINOR"   # e.g. "0.5"
```

The original rationale was that patch-level changes to the library are by definition non-breaking, so no consumer needs to detect them.  `release.md` later argued the opposite: keeping a `PATCH` slot lets consumers `packaging.version.Version`-compare reliably and gives implementation tweaks a parking spot without burning a MINOR.  The 3-segment shape won.

### 5.2 Breaking-change detection (in-repo snapshot test)

Breaking-change detection runs as an **in-repo snapshot test** rather than the venv-diffing `tools/check_api_compat.py` script that earlier drafts of this section sketched (that standalone tool was never shipped).  `tests/test_library_api.py::TestApiSignatureSnapshot` records every public symbol's signature (callables), field roster (dataclasses), or `model_fields` keys (Pydantic models) in `tests/_data/api_signatures_<__api_version__>.json`, keyed to the current `__api_version__`.  The test fails whenever the live surface differs from the snapshot for that version.

Workflow: a genuinely-needed signature change makes the test red; the contributor then bumps `__api_version__` per the §5.1 rules (MAJOR for a removed/signature-changed **stable** symbol, MINOR for an addition) and regenerates the snapshot file under the new version key, so the bump and the surface change land in the same commit.  This is a blocking gate (the test is part of the suite) — stronger than the "notification only" posture the original sketch proposed, and it needs no temp-venv install of a prior release.

### 5.3 Deprecation cadence

The existing rule (`docs/standards/release.md` "Deprecation cadence") applies unchanged:

- Mark the old symbol with a `DeprecationWarning` in release `N`.
- Keep it working in release `N+1`.
- Remove in release `N+2`.

The `DeprecationWarning` message must include the replacement symbol name and the planned-removal version (e.g. `"v0.7.0"`).

---

## 6. Integration test strategy

### 6.1 Test surface

A new test module `tests/test_library_api.py` covers the **stable** surface end-to-end.  Each test exercises one user journey, not one symbol:

| Test | Journey |
|---|---|
| `test_train_then_chat_pure_python` | `from forgelm import ForgeTrainer, ForgeConfig`; build a tiny config in-memory (no YAML); train 1 step on a fixture dataset; assert `TrainResult.success`. |
| `test_audit_dataset_one_shot` | `from forgelm import audit_dataset`; pass a 100-row JSONL; assert the returned `AuditReport.total_samples == 100` and `cross_split_overlap["pairs"] == {}` (note: `cross_split_overlap` is a `Dict[str, Any]` per `forgelm/data_audit/_types.py:122`, accessed by key not attribute). |
| `test_verify_audit_log_valid_chain` | `from forgelm import verify_audit_log`; emit two events via `AuditLogger`; assert the returned `VerifyResult.valid is True`. |
| `test_pii_utilities_standalone` | `from forgelm import detect_pii, mask_pii`; assert detection + masking on a fixture string. |
| `test_audit_dataset_with_croissant` | Calls `audit_dataset(..., emit_croissant=True)` and asserts the `report.croissant` block has the expected JSON-LD shape. |
| `test_lazy_import_no_torch` | Subprocess: `import forgelm; assert "torch" not in sys.modules`. |
| `test_lazy_import_attribute_access_metadata_only` | Subprocess: `import forgelm; _ = forgelm.ForgeTrainer; assert "torch" not in sys.modules`.  Attribute access through the package facade resolves the class object via `__getattr__` → `from .trainer import ForgeTrainer`; `forgelm/trainer.py` defers `import torch` to function bodies (per `tests/test_lazy_imports.py`), so merely *referencing* the class must NOT pull torch.  Torch is loaded only when the class is *used* (instantiated + `train()` called), which is exercised by `test_train_then_chat_pure_python` above. |
| `test_dir_lists_stable_symbols` | `assert "ForgeTrainer" in dir(forgelm)` etc. for every name in §2.1. |
| `test_internal_symbols_excluded_from_public_surface` | `assert "_http" not in dir(forgelm)` AND `assert "_http" not in forgelm.__all__`.  We do **not** assert `pytest.raises(AttributeError)` here: `forgelm/_http.py` is a real submodule, so the moment any other test does `from forgelm import _http` (or `import forgelm._http`) the attribute is set on the package object as a side-effect and a `raises` test would silently flip.  The contract we actually want is "internal = not in `__all__` / not in `dir()`", not "import-protected". |
| `test_config_from_dict` | `from forgelm import ForgeConfig`; build via `ForgeConfig(**dict_payload)`; assert validation errors raise on bad input; assert the round-tripped object matches a YAML-loaded equivalent.  Pins the `**dict_payload` path called out in §8.2. |

### 6.2 Heavy-dep gating

Tests that need `torch` skip when `torch` is unavailable using the existing pattern (`pytest.importorskip("torch")`).  CI runs the full suite on the matrix combos that have torch installed; the lightweight runners run only the `mark_lightweight` subset.

### 6.3 No CLI subprocess

`test_library_api.py` deliberately avoids `subprocess.run([sys.executable, "-m", "forgelm.cli", ...])`.  The whole point is to exercise the library surface; CLI smoke tests already live in `test_cli.py` etc.

---

## 7. Documentation surface

| Doc | Role | Owner phase |
|---|---|---|
| `docs/reference/library_api.md` (new) | Reference: every stable + experimental symbol with signature + one-paragraph description | Phase 19 |
| `docs/reference/library_api-tr.md` (new) | TR mirror | Phase 24 (bilingual sweep) — Phase 19 may ship the EN side first |
| `docs/guides/library_api.md` (new) | Tutorial: "Use ForgeLM from a notebook" with three end-to-end examples (audit, verify-audit, train-and-chat) | Phase 19 |
| `notebooks/library_api_example.ipynb` (new) | Runnable notebook covering the same three journeys | Phase 19 |
| README.md | One-paragraph "ForgeLM as a Python library" section + link to `docs/guides/library_api.md` | Phase 19 |
| CHANGELOG.md | `[Unreleased]` "Library API" section listing new symbols | Phase 19 |
| `docs/standards/release.md` | Already covers deprecation cadence; add a "Library API" sub-section pointing at this design | Phase 19 |

The reference doc is hand-written, not Pydantic-auto-gen — Pydantic gen covers the config schema (Phase 16), not the library API.

---

## 8. Cross-cutting concerns

### 8.1 Logging

Today every module under `forgelm/` calls `logging.getLogger("forgelm.<sub>")`.  When ForgeLM is used as a library, the consumer's logger config takes over — they may (a) want our log records, (b) want them muted, (c) want them in JSON.

Phase 19 ships:

- A documented "library mode" snippet in `docs/guides/library_api.md`:
  ```python
  import logging
  logging.getLogger("forgelm").setLevel(logging.WARNING)  # quiet by default in libraries
  ```
- No automatic logging configuration on `import forgelm`.  We do **not** call `logging.basicConfig()` from any module-level code.  The CLI does its own setup in `forgelm.cli._setup_logging`; the library leaves it to the caller.

This is the "library hygiene" rule from PEP 8 / `logging` HOWTO; nothing new to invent.

### 8.2 Config from dict (not just YAML)

Today `forgelm.load_config(path)` reads YAML.  A library user with an in-memory config (e.g. parametric sweep) wants:

```python
from forgelm import ForgeConfig
config = ForgeConfig(**{"model": {...}, "training": {...}, ...})
```

`ForgeConfig` is a Pydantic model so this already works — `ForgeConfig(**dict)` raises typed validation errors on bad input.  Phase 19 documents this path explicitly in `library_api.md#config-from-dict` and adds a `tests/test_library_api.py::test_config_from_dict` regression.

### 8.3 Error handling at the library boundary

CLI dispatchers catch broad exceptions, log a structured envelope, and `sys.exit` with one of the public exit codes (0/1/2/3/4).  Library callers expect typed exceptions to propagate:

| Library symbol | Errors propagated as |
|---|---|
| `ForgeTrainer.train()` | `ConfigError` (validation), `RuntimeError` (CUDA / training-loop), `OSError` (I/O), all preserving original traceback. |
| `audit_dataset()` | `ValueError` (invalid args), `OSError` (I/O), `OptionalDependencyError` (missing extra). |
| `verify_audit_log()` | Returns `VerifyResult(valid=False, reason=...)` for chain failures (not an exception); raises `OSError` only for unreadable files. |
| `AuditLogger.log_event()` | `OSError` on write failure (caller decides whether to retry or abort). |

The rule: **library code does not call `sys.exit`**.  Every exit-code mapping lives in CLI dispatchers; library users see the typed exception and decide the disposition themselves.

### 8.4 Thread / process safety

| Symbol | Safe to call from multiple threads? | Safe to fork after construction? |
|---|---|---|
| `ForgeTrainer.train()` | No — TRL trainer holds GPU state. | No. |
| `audit_dataset()` | Yes (multiple corpora in parallel threads); each call is self-contained. | Yes. |
| `AuditLogger.log_event()` | Yes — uses `fcntl.flock` on POSIX (Wave 1).  On Windows there is no cross-process lock (the advisory flock helper is a no-op); do not share an `output_dir` across concurrent processes on Windows. | Each forked child should construct its own `AuditLogger`; sharing the file handle across forks is unsupported. |
| `verify_audit_log()` | Yes — read-only. | Yes. |
| `WebhookNotifier.notify_*()` | Yes — each call opens its own `requests` session. | Yes. |

Documented under `docs/reference/library_api.md#concurrency`.  Not enforced via locks beyond what's already in the modules; the documented contract is the contract.

---

## 9. Phase 19 task plan (what implementation actually does)

This section is the implementation spec for the next phase.  It is intentionally precise so a Phase 19 contributor doesn't have to re-derive any of the above.

| # | Task | Files | Acceptance |
|---|---|---|---|
| 1 | Add `forgelm/py.typed` (zero-byte marker) | `forgelm/py.typed` (new) | From a fresh venv, `pip install dist/forgelm-*.whl && mypy --strict <test_file_importing_forgelm.py>` does NOT report `missing library stubs or py.typed marker`. |
| 2 | Create the `__api_version__` home and re-export from `forgelm/__init__.py`.  *(As implemented: lives in `forgelm/_version.py` with 3-segment semver `"1.0.0"` per `docs/standards/release.md` lines 27-39 — see §5.1 Status callout.  The original draft of this row called for `forgelm/_version.py` with `"0.5"`, both superseded.)* | `forgelm/_version.py`, `forgelm/__init__.py` | Both `from forgelm._version import __api_version__` and `from forgelm import __api_version__` work. |
| 3 | Extend `forgelm/__init__.py` `__getattr__` with the §4.2 entries (`audit_dataset`, `verify_audit_log`, `AuditLogger`, `VerifyResult`, `AuditReport`, `WebhookNotifier`, `detect_pii`, `mask_pii`, `detect_secrets`, `mask_secrets`, `compute_simhash`) | `forgelm/__init__.py` | Each name is reachable; `import forgelm` does not import torch (see §4.1 regression test). |
| 4 | Add `__all__` entries for every name introduced in §2.1 + §2.2 | `forgelm/__init__.py` | `dir(forgelm)` lists them. |
| 5 | Add `__dir__()` returning the union of `globals()` keys and `_LAZY_SYMBOLS` keys (per §4.2 sketch — superset of `__all__` so a name in the lazy table that the developer forgot to add to `__all__` still surfaces in IDE auto-complete) | `forgelm/__init__.py` | `dir(forgelm)` is a superset of `forgelm.__all__` and a superset of `_LAZY_SYMBOLS.keys()`. |
| 6 | Port `forgelm/cli/__init__.py` to the lazy `__getattr__` + `_SYMBOL_TO_MODULE` pattern (§4.3) | `forgelm/cli/__init__.py` | `tests/test_cli_lazy_imports.py` passes. |
| 7 | Add `tests/test_library_api.py` with the §6.1 cases | `tests/test_library_api.py` (new) | All 10 tests green (9 user-journey + `test_config_from_dict`). |
| 8 | Add `tests/test_cli_lazy_imports.py` for the §4.3 invariant | `tests/test_cli_lazy_imports.py` (new) | `import forgelm.cli` does not trigger any `forgelm.cli.subcommands.*` import; lazy access does. |
| 9 | Run `mypy --strict --follow-imports=silent forgelm/__init__.py forgelm/_version.py` and fix every error | (any module touched by §3) | `mypy` clean on the public surface. |
| 10 | Add the CI step from §3.2 | `.github/workflows/ci.yml` | Step appears, fails on a deliberate type regression in a sandbox commit. |
| 11 | Write `docs/reference/library_api.md` covering every §2.1 + §2.2 symbol | `docs/reference/library_api.md` (new) | Each name has a signature + 1-paragraph blurb. |
| 12 | Write `docs/guides/library_api.md` with three end-to-end examples (audit, verify-audit, train-and-chat) | `docs/guides/library_api.md` (new) | Tutorial runnable in a notebook. |
| 13 | Add `notebooks/library_api_example.ipynb` covering the same three examples | `notebooks/library_api_example.ipynb` (new) | Runs to completion under `nbval` smoke. |
| 14 | Update `README.md` with a "ForgeLM as a Python library" section | `README.md` | Section + link to the guide. |
| 15 | Update `CHANGELOG.md [Unreleased]` with the new symbol list | `CHANGELOG.md` | Entry under "Library API". |
| 16 | Update `docs/standards/release.md` with a "Library API" sub-section pointing here | `docs/standards/release.md` | Sub-section + cross-link. |

**Phase 19 acceptance:**
- All 16 tasks above land in a single PR.
- `pytest tests/test_library_api.py tests/test_cli_lazy_imports.py` green on fresh checkout.
- `pytest tests/` overall passes (existing 1010+ tests not regressed).
- `mypy --strict --follow-imports=silent forgelm/__init__.py forgelm/_version.py` clean.
- `ruff format . && ruff check .` clean.
- README + CHANGELOG + guides up-to-date.

---

## 10. Open questions for review

These are the places where reasonable people would disagree.  Resolved before Phase 19 starts.

### Q1. Should `forgelm.api` exist at all, or just live in `forgelm/__init__.py`?

**Decision:** create `forgelm/_version.py`.  Two reasons: (a) it gives `__api_version__` a clean home so `__init__.py` doesn't have to grow a constants block; (b) downstream consumers can `from forgelm._version import __api_version__` without triggering any heavy imports.  `forgelm/__init__.py` itself stays the canonical entry point — `from forgelm import ForgeTrainer` keeps working — but the version gets its own module.

### Q2. Why not auto-generate `library_api.md` from docstrings?

**Decision:** hand-write it.  Auto-generation drifts when symbols move modules; the doc has to outlive a refactor.  Pydantic schema → `configuration.md` is auto-gen because the schema is the source; library API surface has no equivalent single source.  Hand-written + a `tools/check_api_doc_completeness.py` script that flags any `__all__` entry missing from the doc is the better trade-off.

### Q3. Should `mypy --strict` run on the entire codebase?

**Decision:** no.  Strict-typing the entire project would require us to also strictly-type the `transformers` / `trl` / `lm_eval` interactions, which we don't own.  Phase 19 strict-types only the public surface (§3.2).  The rest of the codebase keeps the existing convention (function signatures typed, internals loose).

### Q4. Should `__api_version__` follow `__version__` automatically?

**Decision:** no — they are deliberately separate.  Bumping `__version__` for a CLI bug fix does not bump the library contract.  Hand-maintaining `__api_version__` is the price for being able to claim a stable library API.  The release skill (`cut-release`) gets a checklist line: "Did this release change a stable-tier symbol's signature?  If yes, bump `__api_version__` too."

### Q5. Should we expose `WebhookNotifier` as stable or experimental?

**Decision:** experimental.  The class works but its constructor takes a `config` object whose schema we are still moving (Phase 23 ISO/SOC 2 will likely add fields).  Experimental + clear "subject to change" callout buys us a release cycle to get the shape right before promoting to stable in v0.6.x.

---

## 11. Out of scope (deliberately)

Things this design **does not** do, with the reason:

| Out of scope | Why |
|---|---|
| Automatic `__api_version__` bumping in CI | Manual decision is the whole point — see Q4. |
| `forgelm.serve(...)` (HTTP wrapper) | Different product (Pro CLI dashboard); see [Phase 13 Pro CLI plan](../roadmap/phase-13-pro-cli.md). |
| `mypy --strict` on the whole codebase | See Q3. |
| Automatic `library_api.md` generation | See Q2. |
| Async variants (`async def train(...)`) | Trainer is fundamentally synchronous (TRL holds GPU state).  An async wrapper would be a different module. |
| Per-symbol `__deprecated__` decorator | We use the `DeprecationWarning` + comment pattern; a decorator would be over-engineered for a handful of deprecations per cycle. |

---

## 12. Implementation status

The design above shipped end-to-end in v0.5.5:

- Stable / Experimental / Internal tiers (§2): pinned in `forgelm/__init__.py::__all__` + `_LAZY_SYMBOLS`.
- Type-hint surface (§3): `py.typed` marker ships in the wheel; public functions carry annotations.
- Lazy-import discipline (§4): `forgelm/__init__.py::__getattr__` resolves on first access; `forgelm/cli/__init__.py` mirrors the pattern; regression test corpus at `tests/test_lazy_imports.py` covers `forgelm.trainer`, `model`, `data`, `benchmark`, `safety`, `inference`, `synthetic`, `judge` (no eager torch / transformers / trl / datasets).
- Two version strings (§5): `__version__` (runtime, `importlib.metadata`-derived) + `__api_version__` (semver `1.0.0`); both live in `forgelm/_version.py`.
- Integration tests (§6): `tests/test_library_api.py` covers public-surface invariants — stable-symbol roster matches `forgelm.__all__`, lazy-import discipline (subprocess assertion that `import forgelm` does not pull `torch` / `transformers` / `trl`), `__getattr__` resolution + caching, `dir(forgelm)` shape, `py.typed` marker presence, and `__api_version__` contract — paired with `tests/test_lazy_imports.py` for the per-submodule lazy-load regression corpus. End-to-end train-and-chat user journeys are exercised by the per-trainer test modules (`tests/test_trainer*.py`, `tests/test_chat.py`), not by `test_library_api.py`.
- Docs (§7): `docs/reference/library_api_reference{,-tr}.md`, `docs/guides/library_api{,-tr}.md`, `docs/usermanuals/{en,tr}/reference/library-api.md`, README "Library API" section.
- Errors + concurrency (§8): `ConfigError`, `OptionalDependencyError`, `HttpSafetyError`; `AuditLogger.log_event` is multiprocess-safe on POSIX via `fcntl.flock` (no cross-process lock on Windows — no-op).
