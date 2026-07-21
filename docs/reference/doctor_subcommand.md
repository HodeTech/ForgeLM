# `forgelm doctor` Reference

> **Mirror:** [doctor_subcommand-tr.md](doctor_subcommand-tr.md)
>
> The first command an operator runs after installation. Probes Python, torch + CUDA, GPU inventory, optional ForgeLM extras, HuggingFace Hub reachability, workspace disk space, and the `FORGELM_OPERATOR` audit-identity hint, then emits a tabular text report or a structured JSON envelope.

## Synopsis

```shell
forgelm doctor [--offline] [--output-format {text,json}] [-q] [--log-level {DEBUG,INFO,WARNING,ERROR}]
```

Implementation: [`forgelm/cli/subcommands/_doctor.py`](../../forgelm/cli/subcommands/_doctor.py).

## Flags

| Flag | Type | Default | Description |
|---|---|---|---|
| `--offline` | bool | `false` | Skip the HuggingFace Hub network probe. Inspect the local cache instead (precedence: `HF_HUB_CACHE > HF_HOME/hub > ~/.cache/huggingface/hub`). Implicitly true when `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, or `HF_DATASETS_OFFLINE=1` is set. |
| `--output-format` | `text` \| `json` | `text` | Renderer. `json` emits the locked envelope `{"success": bool, "checks": [...], "summary": {pass, warn, fail, crashed}}`. |
| `-q`, `--quiet` | bool | `false` | Suppress INFO logs. |
| `--log-level` | `DEBUG`/`INFO`/`WARNING`/`ERROR` | `INFO` | Logging verbosity. |

## Probes

| Probe `name` | Status policy | What it checks |
|---|---|---|
| `forgelm.install` | `pass` | Which ForgeLM actually ran: resolved package directory, version, and whether it sits inside site-packages. Runs first so a pasted bug report leads with the code under discussion. |
| `python.version` | `fail` <3.10, `warn` 3.10.x, `pass` >=3.11 | Pin to the supported window. |
| `torch.installed` / `torch.cuda` | `fail` if torch missing; `warn` if CPU-only; `pass` if CUDA visible | torch + CUDA availability. |
| `numpy.torch_abi` | `fail` on an ABI mismatch, `pass` otherwise (including when torch is absent, where the check is skipped) | Whether the installed torch and numpy are ABI-compatible. |
| `gpu.inventory` | `pass` with per-device VRAM, `warn` if no CUDA | Visible GPUs and per-device VRAM in GiB. |
| `extras.<name>` | `pass` if importable, `warn` with install hint otherwise | One row per optional extra: `qlora`, `unsloth`, `distributed`, `eval`, `tracking`, `tracking-mlflow`, `export`, `ingestion`, `ingestion-pii-ml`, `ingestion-scale`. The `merging` extra deliberately has **no** row — model merging is implemented natively on core deps, so that extra is a no-op. |
| `pypdf_normalise.turkish` | `pass` if the normalisation behaves, `warn` otherwise | Font-fallback normalisation for Turkish text during PDF ingestion; sits next to the `extras.ingestion` row so the two can be read together. |
| `hf_hub.reachable` (online) | `pass` 2xx/3xx, `warn` transport error, `fail` SSRF policy reject | HEAD `${HF_ENDPOINT}/api/models` with 5s timeout via `forgelm._http.safe_get`. |
| `hf_hub.offline_cache` (`--offline`) | `pass` files visible, `warn` empty / partially unreadable, `fail` no files visible AND walk errors | Bounded scan (depth 4, 5000-file cap) of the resolved Hub cache. |
| `disk.workspace` | `fail` <10 GiB, `warn` <50 GiB, `pass` otherwise | `shutil.disk_usage(".")`. |
| `operator.identity` | `pass` if `FORGELM_OPERATOR` set, `warn` if `getpass` fallback, `fail` if neither (unless `FORGELM_ALLOW_ANONYMOUS_OPERATOR=1`) | Predicts what `AuditLogger` will record. |

The optional-extras list lives in [`forgelm/cli/subcommands/_doctor.py::_OPTIONAL_EXTRAS`](../../forgelm/cli/subcommands/_doctor.py).

## Secret-mask discipline

Env-var values whose names match the secret list at [`_DOCTOR_SECRET_ENV_NAMES`](../../forgelm/cli/subcommands/_doctor.py) (`FORGELM_AUDIT_SECRET`, `HF_TOKEN`, `HUGGING_FACE_HUB_TOKEN`, `HUGGINGFACE_TOKEN`, `FORGELM_RESUME_TOKEN`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `WANDB_API_KEY`, `COHERE_API_KEY`) are rendered as `<set, N chars>` in both `detail` and `extras` so a piped `--output-format json` cannot leak them into a CI log. `FORGELM_OPERATOR` is operator identity, not a secret, and is shown verbatim.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Every check passed. `warn` rows do not flip this — they are operator-actionable but do not block. |
| `1` | At least one probe returned `fail` (config-error class — operator can correct). |
| `2` | A probe itself crashed (runtime-error class — doctor bug or operator-environment surprise). |

Defined in [`forgelm/cli/_exit_codes.py`](../../forgelm/cli/_exit_codes.py): `EXIT_SUCCESS=0`, `EXIT_CONFIG_ERROR=1`, `EXIT_TRAINING_ERROR=2`. Pipelines that retry on transient errors should branch on `2` (re-run) vs `1` (fix-and-fail).

## Audit events emitted

`forgelm doctor` is a **read-only diagnostic** and emits no audit events. It does not touch `audit_log.jsonl` and does not require `FORGELM_OPERATOR` to run; the `operator.identity` probe is a *prediction* of what `AuditLogger` would record, not an actual write.

## JSON envelope shape

```json
{
  "success": true,
  "checks": [
    {
      "name": "python.version",
      "status": "pass",
      "detail": "Python 3.11.4 (CPython).",
      "extras": {"version": "3.11.4", "implementation": "CPython"}
    }
  ],
  "summary": {"pass": 9, "warn": 2, "fail": 0, "crashed": 0}
}
```

`success` is `true` iff `summary.fail == 0`. `extras` is JSON-encoded with `ensure_ascii=False` so a Unicode operator name or cache path renders verbatim, and with `default=str` so a future probe surfacing a `Path`/`datetime` value does not crash the renderer. The full schema is locked in [`docs/usermanuals/en/reference/json-output.md`](../usermanuals/en/reference/json-output.md).

## Examples

### First-run smoke check

```shell
$ forgelm doctor
forgelm doctor - environment check

  [+ pass] python.version          Python 3.11.4 (CPython).
  [+ pass] torch.cuda              torch 2.4.0 with CUDA 12.4.
  [+ pass] gpu.inventory           1 GPU(s) - GPU0: NVIDIA RTX 4090 (24.0 GiB).
  [+ pass] extras.qlora            Installed (module bitsandbytes, purpose: 4-bit / 8-bit QLoRA training).
  [! warn] extras.tracking         Optional extra missing - install with: pip install 'forgelm[tracking]' (purpose: Weights & Biases experiment tracking).
  [+ pass] hf_hub.reachable        HuggingFace Hub reachable at https://huggingface.co (HTTP 200).
  [+ pass] disk.workspace          Workspace /home/me/forgelm - 387.0 GiB free of 500.0 GiB.
  [! warn] operator.identity       FORGELM_OPERATOR not set; audit events will fall back to 'me@workstation'. Pin FORGELM_OPERATOR=<id> for CI / pipeline runs so the audit log identifies a stable identity.

Summary: 6 pass, 2 warn, 0 fail.
```

### Offline (air-gap) verification

```shell
$ HF_HUB_OFFLINE=1 forgelm doctor --offline
```

`hf_hub.reachable` is replaced with `hf_hub.offline_cache`. A populated cache reports its size, file count, and `HF_HUB_OFFLINE` value; an empty cache emits `warn` with a pointer to [`cache_subcommands.md`](cache_subcommands.md).

### CI gate (JSON)

```shell
$ forgelm doctor --output-format json -q | jq '.summary'
{
  "pass": 6,
  "warn": 2,
  "fail": 0,
  "crashed": 0
}
$ forgelm doctor --output-format json -q | jq '.success'
true
```

### Custom HuggingFace endpoint

```shell
$ HF_ENDPOINT=https://hub.internal.example.com forgelm doctor
```

`_resolve_hf_endpoint` honours `HF_ENDPOINT`, mirroring the `huggingface_hub` library so corp-mirror operators do not get false warnings.

## See also

- [Getting Started guide](../guides/getting-started.md) — onboarding walkthrough that calls `forgelm doctor` first.
- [`cache_subcommands.md`](cache_subcommands.md) — the air-gap pre-cache subcommand pair `forgelm doctor --offline` validates.
- [Air-gap deployment guide](../guides/air_gap_deployment.md) — full air-gap operator workflow.
- [`audit_event_catalog.md`](audit_event_catalog.md) — full audit-event catalog (doctor itself emits none).
- [Installation manual page](../usermanuals/en/getting-started/installation.md) — `pip install forgelm[<extras>]` reference.
- [JSON output schema](../usermanuals/en/reference/json-output.md) — locked envelope contract.
