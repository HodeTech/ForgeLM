# Getting Started with ForgeLM

> **Audience:** New ForgeLM operators — engineers, MLOps teams, and compliance-aware data scientists running their first fine-tune on a fresh host.
>
> This guide walks you from `pip install forgelm` to a green `forgelm doctor` to a first training run, with the diagnostic checkpoints regulated environments expect along the way.

## What it solves

You just installed ForgeLM. Now what?

The pain points this guide addresses, in order of how often they trip up first-time operators:

1. **Silent CUDA / extras misconfiguration.** A 30-minute training run that crashes 28 minutes in because `bitsandbytes` was the wrong version is the worst feedback loop in fine-tuning.
2. **`FORGELM_OPERATOR` not pinned in CI.** The audit log records `me@workstation` instead of a stable identity, breaking EU AI Act Article 12 record-keeping after the operator's machine reboots.
3. **Disk-full mid-training.** A 7B model + checkpoints + Llama Guard pre-cache eats 50+ GiB; running a long fine-tune with 10 GiB free on `/` is a guaranteed late failure.
4. **HuggingFace gated-model access.** Llama / Llama Guard need an HF token; "OSError: HuggingFace token not found" 28 minutes into the run is the classic.
5. **Wrong onboarding order.** Operators try to write YAML before they know which extras they have, then debug feature presence by reading exception tracebacks.

`forgelm doctor` exists to surface every one of these in two seconds rather than 30 minutes.

## Step-by-step

### Step 1 — Install ForgeLM

Pick the extras you'll actually use. The base install gives you the trainer, all six alignment paradigms, evaluation, safety scoring, compliance artefact generation, and the CLI:

```shell
$ pip install forgelm
```

For GPU training, add the extras you need (combine comma-separated; `pyproject.toml` does **not** define an `[all]` aggregate, by design):

```shell
$ pip install 'forgelm[qlora,eval,tracking,merging,export,ingestion]'
```

The exhaustive extras catalogue lives at [Installation manual page](../usermanuals/en/getting-started/installation.md).

### Step 2 — Run `forgelm doctor` (the canonical first command)

**This is the first thing you run after `pip install forgelm`.** It probes Python, torch + CUDA, GPU inventory, the optional extras you installed, HuggingFace Hub reachability, workspace disk space, and your audit identity:

```shell
$ forgelm doctor
forgelm doctor - environment check

  [+ pass] forgelm.install          ForgeLM 0.9.0 running from /home/me/.venv/lib/python3.11/site-packages/forgelm (inside site-packages).
  [+ pass] python.version           Python 3.11.4 (CPython).
  [+ pass] torch.cuda               torch 2.4.0 with CUDA 12.4.
  [+ pass] numpy.torch_abi          torch 2.4.0 + numpy 1.26.4 are ABI-compatible.
  [+ pass] gpu.inventory            1 GPU(s) — GPU0: NVIDIA RTX 4090 (24.0 GiB).
  [+ pass] extras.qlora             Discoverable, not import-tested (module bitsandbytes, purpose: 4-bit / 8-bit QLoRA training).
  [! warn] extras.tracking          Optional extra missing — install with: pip install 'forgelm[tracking]' (purpose: Weights & Biases experiment tracking).
  [+ pass] pypdf_normalise.turkish  Turkish glyph profile loaded: 5 single-char + 1 multi-char substitutions. Active profile: 'none'.
  [+ pass] hf_hub.reachable         HuggingFace Hub reachable at https://huggingface.co (HTTP 200).
  [+ pass] disk.workspace           Workspace /home/me/forgelm — 387.0 GiB free of 500.0 GiB.
  [! warn] operator.identity        FORGELM_OPERATOR not set; audit events will fall back to 'me@workstation'. Pin FORGELM_OPERATOR=<id> for CI / pipeline runs so the audit log identifies a stable identity.

Summary: 9 pass, 2 warn, 0 fail.
```

> Some `extras.*` rows are omitted above for brevity — every extra in `pyproject.toml`'s `[project.optional-dependencies]` gets its own row (nine as of this writing); run `forgelm doctor` locally to see the full list for your install.

**What each probe means:**

- `forgelm.install` — always `pass`; records which ForgeLM actually loaded (resolved package location, version, and whether it sits inside `site-packages`) so a bug report identifies exactly which code ran. First row deliberately — every other line is ambiguous until you know this.
- `python.version` — `fail` <3.10, `warn` 3.10.x, `pass` >=3.11.
- `torch.cuda` — `fail` if torch missing; `warn` if CPU-only (CPU runs are *supported* but slow); `pass` with CUDA visible.
- `numpy.torch_abi` — `fail` on the known torch<2.3 + numpy>=2 binary-ABI mismatch (`_ARRAY_API not found`); `pass` otherwise, including when either torch or numpy is absent (skipped, not penalized).
- `gpu.inventory` — per-device VRAM in GiB; needed to size LoRA rank / batch.
- `extras.<name>` — one row per installed (or missing) optional extra. The `warn` line carries the exact `pip install 'forgelm[<name>]'` hint, so install hints are always actionable. A `pass` means the module is discoverable, not that it necessarily imports cleanly.
- `pypdf_normalise.turkish` — `fail` if the Turkish glyph-normalisation table fails to load or produces no substitutions on its canonical fixture; `pass` otherwise.
- `hf_hub.reachable` — HEAD on `${HF_ENDPOINT}/api/models`. Catches captive portals, corp proxies, blocked egress before training discovers them.
- `disk.workspace` — `fail` <10 GiB, `warn` <50 GiB, `pass` otherwise.
- `operator.identity` — `pass` if `FORGELM_OPERATOR` is set, `warn` with the `getpass.getuser()@hostname` fallback, `fail` if neither resolves (unless `FORGELM_ALLOW_ANONYMOUS_OPERATOR=1` opt-in is set).

**Exit codes** (CI/CD contract): `0` = all checks pass, `1` = at least one `fail` (config-error class), `2` = a probe itself crashed (runtime-error class).

For the full flag reference see [`docs/reference/doctor_subcommand.md`](../reference/doctor_subcommand.md).

### Step 3 — Pin `FORGELM_OPERATOR` for CI / pipeline runs

The `operator.identity` warning above is the most common one. On a developer workstation the `getpass.getuser()@hostname` fallback is fine; on a CI runner you want a stable identity:

```shell
$ export FORGELM_OPERATOR="gha:Acme/repo:training:run-${GITHUB_RUN_ID}"
$ forgelm doctor   # now [+ pass] operator.identity
```

This identity is stamped into every audit-log entry the trainer emits — it's what an EU AI Act Article 12 reviewer reads to attribute model provenance. See [`docs/qms/access_control.md`](../qms/access_control.md) for the recommended namespacing scheme.

### Step 4 — Hand off the gated-model authentication

Llama / Gemma / Llama Guard require an HF token. Set it via the standard env var:

```shell
$ export HF_TOKEN="hf_xxxxx"   # or huggingface-cli login
```

`HF_TOKEN`, `HUGGING_FACE_HUB_TOKEN`, `HUGGINGFACE_TOKEN` are all read; the value is masked as `<set, N chars>` in any `forgelm doctor --output-format json` output (see secret-mask discipline in [`doctor_subcommand.md`](../reference/doctor_subcommand.md#secret-mask-discipline)).

### Step 5 — Generate a config and validate it

```shell
$ forgelm quickstart customer-support
$ forgelm --config configs/quickstart-customer-support.yaml --dry-run
```

`--dry-run` parses the YAML, checks every referenced file, downloads model metadata (no weights), and reports problems before consuming a GPU second. Walk-through in [Your First Run](../usermanuals/en/getting-started/first-run.md).

### Step 6 — Train

```shell
$ forgelm --config configs/quickstart-customer-support.yaml
```

If anything fails in the first 30 seconds, re-run `forgelm doctor` first — most early failures are a probe that turned `warn` into `fail` since the last check (typically: another process filled the workspace, or a network change broke the corp proxy).

### Step 7 — Verify with a JSON envelope (optional, for CI)

```shell
$ forgelm doctor --output-format json -q | jq '.success'
true
```

The JSON envelope shape is locked: `{"success": bool, "checks": [...], "summary": {pass, warn, fail, crashed}}`. Schema lives in [`docs/usermanuals/en/reference/json-output.md`](../usermanuals/en/reference/json-output.md).

## Common pitfalls

### "I get `extras.qlora` warn even after `pip install forgelm[qlora]`"

The macOS / Apple Silicon case: `bitsandbytes` does not currently support Metal/MPS, so the import fails silently and doctor reports the extra as missing. ForgeLM falls back to full-precision training automatically. For 4-bit QLoRA you need a Linux host with a CUDA GPU.

### "`hf_hub.reachable` reports `fail` instead of `warn`"

That's the SSRF discipline rejecting the probe — typically an `http://` (not `https://`) endpoint or a private-IP `HF_ENDPOINT`. Either:

- Set `HF_ENDPOINT` to a public `https://` URL, or
- Run `forgelm doctor --offline` to skip the network probe entirely (the cache probe replaces it).

### "Doctor says `fail` for `operator.identity`"

This means `FORGELM_OPERATOR` is unset AND `getpass.getuser()` could not resolve a username AND `FORGELM_ALLOW_ANONYMOUS_OPERATOR=1` is **not** set. `AuditLogger` itself would refuse to start in this state — pin `FORGELM_OPERATOR=<id>` (recommended) or set `FORGELM_ALLOW_ANONYMOUS_OPERATOR=1` (not recommended for Article 12 record-keeping; only appropriate for sandboxes / smoke tests).

### "I'm air-gapped — does this guide still apply?"

Yes — but with `forgelm doctor --offline` instead of the network probe. Everything else (extras, GPU, disk, operator identity) is identical. See [Air-gap deployment](air_gap_deployment.md) for the full air-gap operator workflow.

### "Doctor passes but `forgelm --dry-run` fails"

`forgelm doctor` validates the *environment*; `--dry-run` validates the *config*. They are complementary, not duplicates. A green doctor + a red `--dry-run` typically means a missing input file, a typo'd model name, or a Pydantic validation error. The `--dry-run` output points at the offending YAML field directly.

## See also

- [`docs/reference/doctor_subcommand.md`](../reference/doctor_subcommand.md) — full `forgelm doctor` flag + probe reference.
- [`docs/reference/cache_subcommands.md`](../reference/cache_subcommands.md) — `forgelm cache-models` / `cache-tasks` for air-gap pre-fetch.
- [`docs/reference/safety_eval_subcommand.md`](../reference/safety_eval_subcommand.md) — `forgelm safety-eval` for standalone safety classifier runs.
- [Installation manual](../usermanuals/en/getting-started/installation.md) — exhaustive extras catalogue.
- [Your First Run](../usermanuals/en/getting-started/first-run.md) — full training walkthrough after the doctor is green.
- [Air-gap deployment](air_gap_deployment.md) — for restricted-egress environments.
- [Troubleshooting manual](../usermanuals/en/operations/troubleshooting.md) — when the doctor says fail.
