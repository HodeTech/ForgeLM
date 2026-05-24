---
title: Library API
description: Use ForgeLM as a Python library — public symbols, stability tiers, and lazy-import contract.
---

# Library API

ForgeLM ships a Python library API alongside the `forgelm` console script. Anything the CLI does, the library can do — minus the exit-code mapping. The full reference lives in the docs tree; this page is the navigation entry point.

## Two equally first-class entry points

```python
# Library — programmatic use.
from forgelm import ForgeTrainer, audit_dataset, verify_audit_log, load_config
```

```bash
# CLI — shell pipelines + CI/CD.
forgelm --config configs/run.yaml
```

Choose the library API when you're orchestrating from Python (Airflow, Prefect, Dagster) or working from notebooks. Choose the CLI when you're shipping a CI pipeline that depends on the exit-code contract.

## Stability tiers

Three tiers govern semver weight:

- **Stable** — semver-protected. Includes `ForgeTrainer`, `ForgeConfig`, `load_config`, `audit_dataset`, `verify_audit_log`, `AuditLogger`, the PII / secrets utilities, and the verification toolbelt.
- **Experimental** — best-effort, may change in a minor release. Includes `WebhookNotifier`, `run_benchmark`, `SyntheticDataGenerator`, `compute_simhash`, `setup_authentication`.
- **Internal** — anything not in `forgelm.__all__`. No stability guarantee.

## Lazy-import contract

```python
import sys
import forgelm

assert "torch" not in sys.modules     # pinned by tests/test_library_api.py
```

`import forgelm` does not pull `torch`, `transformers`, `trl`, or `datasets`. Heavy deps load only when a symbol that needs them is **called** (not just referenced).

## Where to read more

- The complete reference, with every signature and worked examples, ships with the toolkit on GitHub:
  [`library_api_reference.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/library_api_reference.md).
- The deep guide, with three end-to-end pipeline patterns and common pitfalls:
  [`library_api.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/guides/library_api.md) on GitHub.
- The Phase 18 design rationale (stability tiers, type contract, deprecation cadence):
  [`library_api.md` design note](https://github.com/HodeTech/ForgeLM/blob/main/docs/design/library_api.md) on GitHub.

## See also

- [Configuration](#/reference/configuration) — `ForgeConfig` field reference.
- [Audit Event Catalog](#/compliance/audit-log) — events `AuditLogger.log_event` accepts.
- [CI/CD Pipelines](#/operations/cicd) — the CLI counterpart.
