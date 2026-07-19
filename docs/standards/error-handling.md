# Error Handling Standard

> **Scope:** All error paths in [`forgelm/`](../../forgelm/). CI/CD orchestrators depend on these rules to make decisions — violating them silently breaks pipelines downstream.
> **Enforced by:** Code review + CI tests under `tests/test_cli.py`, `tests/test_config.py`.

## Exit codes

Defined in [`forgelm/cli/_exit_codes.py`](../../forgelm/cli/_exit_codes.py):

```python
EXIT_SUCCESS = 0
EXIT_CONFIG_ERROR = 1
EXIT_TRAINING_ERROR = 2
EXIT_EVAL_FAILURE = 3
EXIT_AWAITING_APPROVAL = 4
EXIT_WIZARD_CANCELLED = 5
EXIT_INTEGRITY_FAILURE = 6
```

| Code | When | Who reads it |
|---|---|---|
| **0** | Training completed. With `auto_revert: true` (the compliance default for high-risk tiers) this also means every gate passed; with the **shipped default** `auto_revert: false` a failed benchmark/safety/judge gate is *recorded* (`benchmark`/`safety`/`judge` block in the JSON, `*_passed: false`) but does **not** change the exit code — parse those blocks, don't trust exit 0 alone | CI/CD success |
| **1** | Config validation failed (YAML schema, Pydantic error). Also used by the `verify-*` subcommands for an operator-actionable input error — the artefact could not be read at all, so nothing was ever compared (plus the one case where everything comparable *was* compared and came out clean; see the 1-vs-6 note below) | CI/CD "fail fast"; user fixes YAML or the verifier's target path |
| **2** | Training crashed or failed mid-run (OOM, CUDA error, unhandled exception). Also the `verify-*` subcommands' code for a genuine runtime I/O failure on a reachable path (permission denied mid-read, disk error) | CI/CD retry logic |
| **3** | Training completed but eval/safety/benchmark threshold failed, and auto-revert happened | CI/CD decision: do not deploy |
| **4** | Training + evals passed, but `require_human_approval: true` — staged, awaiting human sign-off | CI/CD pauses pipeline |
| **5** | Wizard cancelled before producing a config (operator decline, non-tty stdin refusal, Ctrl-C through prompts) | CI/CD distinguishes "wizard finished with a config" from "wizard never saved anything" |
| **6** | `verify-audit` / `verify-annex-iv` / `verify-gguf` / `verify-integrity` read an artefact successfully and its **integrity check failed** — a broken audit-log hash chain, an Annex IV manifest hash mismatch, a GGUF SHA-256 sidecar mismatch (or an unparsable metadata block with no matching sidecar to clear it), or model files that no longer match `model_integrity.json`. Split out of `EXIT_CONFIG_ERROR` (1) in the `forgelm/verify.py` verification-toolbelt closure because the two are different incidents with different owners: a mistyped path is an operator typo (1 — fix the command), whereas a hash that no longer matches is a security event (6 — page whoever owns the artefact). Both used to exit 1 | CI/CD: treat as "do not promote", route to whoever owns the artefact, not the pipeline author |

**The line between 1 and 6 for `verify-*` subcommands:** 6 means the verifier compared something and it did not match; 1 means the verifier never got to compare anything (bad path, malformed input, unreadable artefact) — or, in the third case below, that everything it *could* compare came out clean. Four deliberate judgement calls that stay on 1 even though they look tamper-adjacent:

- **`verify-gguf` magic-header mismatch.** A file whose first four bytes aren't `b"GGUF"` is not a GGUF at all — that is a file-type verdict (operator passed the wrong path), not a tamper verdict. Only a magic-OK file that then fails its metadata parse or SHA-256 sidecar check routes to 6.
- **`verify-integrity` manifest entry whose path escapes the model directory.** The verifier refuses to hash an out-of-tree path before it reads anything, so nothing was compared — the same "never got to compare" reasoning as a missing manifest, even though an escaping entry is the shape of an attack.
- **`verify-gguf` metadata-parse failure on a file whose SHA-256 sidecar matches.** A parse error alone is ambiguous: the file may be truncated, or the installed `gguf` package may simply be too old for its format revision. The parse failure therefore does not short-circuit the sidecar comparison, and a matching digest resolves the ambiguity — the bytes are provably identical to what was exported, so nothing was tampered with and the exit code downgrades from 6 to 1. Exit 6 means "page whoever owns the artefact"; a library-version incompatibility must never trigger that. The other two branches keep 6: **no sidecar** (nothing available to rule out corruption) and **mismatching sidecar** (the checksum disagreement is the stronger evidence and dominates the verdict).
- **`verify-audit` on a zero-entry log with no genesis manifest.** The log must still *fail* — reporting `OK: 0 entries verified` after comparing nothing is the fail-open this rule exists to prevent — but it fails as input, not as tampering, because with no manifest there is no baseline in existence to compare zero entries against. An attacker who deleted the log *and* its sidecar lands here, and so does a mistyped path; the one artefact that could tell them apart is the thing that is missing, so claiming tampering would be the mirror image of the `verify-gguf` magic-header case. The split is the whole point: the same empty log **does** exit 6 when a manifest survives to pin a first entry, because that manifest is a baseline and the comparison genuinely ran (see `_classify_empty_audit_log` in `forgelm/compliance.py`). An empty log is never a legitimate fresh-run state — `AuditLogger` writes the file and its manifest together on the first event, so a never-used log is absent, not empty.

The per-verifier classification is structural, not string-matched: each of `forgelm/verify.py`'s `is_*_integrity_failure` predicates reads the result's typed fields (never the human-readable `reason` prose) so a reworded operator message can never silently flip the exit code.

**Rules:**

1. Never use numbers outside this set. If you invent a new failure class, add it here with a name and update this table first.
2. Every `sys.exit(N)` must use a named constant. `sys.exit(1)` literal is a review red flag.
3. Exit codes are part of the public contract. Changing the meaning of a code is a breaking change — bump major version; adding a new code is additive per [release.md](release.md)'s "What constitutes breaking" table. When a new code narrows an *existing* one — as `EXIT_INTEGRITY_FAILURE` (6) narrowed the `verify-*` subcommands' use of `EXIT_CONFIG_ERROR` (1) — a caller that asserted the old code on the now-moved cases sees a real behaviour change even though no code changed meaning; the CHANGELOG entry must name that caller explicitly (see `CHANGELOG.md`'s `[Unreleased]` `Added`/`Changed` pair for the worked example).
4. Always log before exiting (see [logging-observability.md](logging-observability.md)).

## Exception types

Custom exceptions are **deliberately few**. One class per coarse-grained failure domain:

```python
# forgelm/config.py
class ConfigError(Exception):
    """Raised when configuration validation fails."""
```

**Rules for adding a new exception class:**

- The class must be catchable by a specific `except` site that does something different. If you'd catch it and do the same thing as `except Exception`, you don't need the class.
- Name ends with `Error` (`ConfigError`, not `ConfigException`).
- Docstring states exactly when it's raised.
- Lives in the module that owns the domain (`ConfigError` in `config.py`, not a separate `exceptions.py`).

## Raise vs exit

| Situation | Do |
|---|---|
| Inside `config.py`, `trainer.py`, `model.py`, etc. | **Raise.** Let the caller decide. |
| Inside `cli.py` dispatch | **Log + `sys.exit(N)`.** CLI is the top level. |
| Inside tests | **Assert.** Tests are tests. |
| Optional dep missing | **Raise `ImportError`** with install hint. See [architecture.md](architecture.md#3-optional-dependencies-are-extras-never-silent-imports). |

**Never `sys.exit()` from a non-CLI module.** That hides the error from callers, tests, and the library use case.

## Validation errors from Pydantic

`load_config(path)` wraps Pydantic `ValidationError` and `yaml.YAMLError`
into `ConfigError`; `FileNotFoundError` propagates unwrapped. Mirror the real
CLI handler in `forgelm/cli/_config_load.py`:

```python
from forgelm.config import ConfigError, load_config

try:
    config = load_config(args.config)
except ConfigError as e:
    logger.error("Configuration error: %s", e)
    sys.exit(EXIT_CONFIG_ERROR)
except FileNotFoundError:
    logger.error("Config file not found: %s", args.config)
    sys.exit(EXIT_CONFIG_ERROR)
```

**Do not** bare-catch `Exception` at the CLI level. Known failure modes get dedicated branches; unknown failures should bubble up with a traceback (that's a bug we want to see).

## try/except patterns

### Acceptable

```python
# Narrow, documented, caller-specific recovery:
try:
    import wandb
except ImportError as e:
    raise ImportError(
        "W&B tracking requires the 'tracking' extra. "
        "Install with: pip install 'forgelm[tracking]'"
    ) from e
```

```python
# Converting a third-party exception to a domain exception:
try:
    response = requests.post(webhook_url, json=payload, timeout=10)
    response.raise_for_status()
except requests.RequestException as e:
    logger.warning("Webhook delivery failed: %s", e)
    # Webhook failures never abort training.
```

### Rejected

```python
# ❌ Silent swallowing
try:
    do_critical_thing()
except Exception:
    pass

# ❌ Bare except (also caught by ruff B/E9)
try:
    x()
except:
    ...

# ❌ Catch and rewrap without context
try:
    risky()
except Exception as e:
    raise RuntimeError("something failed")  # lost: from e
```

**Rule:** If you `except`, you must either (a) recover with a known-good fallback, (b) log and re-raise with `from e`, or (c) convert to a domain exception with `from e`. "Log and swallow" is a bug unless the failure is explicitly non-fatal (webhooks, cleanup).

## Best-effort artefact carve-out

The default rule above is "narrow class or don't catch." There is exactly one sanctioned escape hatch, and it has a precise scope.

### When the carve-out applies

The only sanctioned form for keeping `except Exception:` is

```python
except Exception as e:  # noqa: BLE001 — best-effort: <one-line reason>
```

and "best-effort" has a single specific meaning here:

> An **outer** error path is already responsible for the primary failure.
> This catch protects a **secondary side effect** — audit log emission, webhook delivery, cleanup of advisory artefacts (model card, integrity checksum, governance report, trend file, deployer instructions) — from masking the primary failure.

If you cannot point at the outer error handler that owns the primary failure, you do **not** have a best-effort path; you have an unknown failure mode that should be diagnosed and either narrowed or surfaced.

### Mandatory hygiene for every BLE001 site

1. The `# noqa: BLE001` comment carries a one-line rationale that names the artefact and explains why a wider class is genuinely infeasible.
2. The handler logs at `WARNING` (or `ERROR` for outage events) so the failure shows up in the run log even though the run continues.
3. A surrounding error path or audit event records the primary failure independently — the BLE001 catch is the secondary, not the primary, surface.
4. **Never** use BLE001 to dodge thinking about the failure modes. If the narrow tuple is `(OSError, ValueError, TypeError)`, write that — only fall back to BLE001 when the protected operation crosses a third-party library surface that documents a wide error tail (HF Hub repository errors, Pydantic mixed validation/runtime errors, etc.).

### Forbidden forms

The bare `except:` form is **forbidden** everywhere, no exceptions. It catches `KeyboardInterrupt` and `SystemExit` and routinely masks `Ctrl-C` during long training runs. Ruff `E722` enforces this in CI.

`except Exception: pass` (no log, no rationale, no re-raise) is **forbidden**. The BLE001 carve-out exists so the deliberate cases are visible; silent swallowing is what the carve-out replaces.

**Named `except KeyboardInterrupt:` is allowed at top-level CLI dispatch sites** (and `except (KeyboardInterrupt, SystemExit):` likewise) for graceful Ctrl-C handling — emit a "interrupted by user" log line, run any cheap cleanup, and exit with a non-zero code. Library modules under `forgelm/` (everything outside `forgelm/cli/`) **must not** catch `KeyboardInterrupt` — let it propagate so a long-running trainer can be aborted from the CLI seam.

### Examples

**Good — narrow class first:**

```python
try:
    with open(trend_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
except (OSError, TypeError, ValueError) as e:
    # OSError: filesystem (permissions, full disk).
    # TypeError/ValueError: json.dumps on unexpected entry shape.
    # Trend logging is non-fatal — a missing entry must not abort the
    # safety pass that already concluded successfully.
    logger.warning("Failed to write safety trend entry: %s", e)
```

**Good — best-effort BLE001 with rationale:**

```python
try:
    info = HfApi().dataset_info(dataset_path)
except Exception as e:  # noqa: BLE001 — best-effort revision pin; HF Hub surface raises a wide error tail (HfHubHTTPError, RepositoryNotFoundError, RevisionNotFoundError, OSError, ValueError) and enumerating them couples this module to huggingface_hub internals.
    logger.debug("HF Hub revision pin skipped for '%s': %s", dataset_path, e)
    _mark_revision_unresolved(fingerprint, f"{type(e).__name__}: {e}")
    return
```

Note what the failure path writes, not just that it is caught: it records
a *marker* saying the lookup was attempted and failed. "We looked and
could not tell" and "we never looked" are different statements to an
auditor, and best-effort code that leaves the artefact silent makes them
indistinguishable. Best-effort means the run continues, not that the
record is allowed to be vague.

**Bad — silent swallow with no rationale:**

```python
try:
    do_critical_thing()
except Exception:  # ❌ no narrow class, no BLE001, no rationale, no log
    pass
```

**Bad — BLE001 used to dodge thinking:**

```python
try:
    config = load_config(path)
except Exception as e:  # noqa: BLE001 — "just in case"  ❌
    logger.warning("Config load failed: %s", e)
    config = ForgeConfig()
```

The protected operation is config validation. `load_config` raises `ConfigError` (wrapping Pydantic `ValidationError` and `yaml.YAMLError`) and lets `FileNotFoundError` propagate. That is a precise tuple — write it.

## User-facing error messages

The audience is an engineer reading a CLI terminal in the dark. Messages must be:

1. **Specific about what.** "YAML file is invalid" — no. "`training.trainer_type` must be one of sft/dpo/simpo/kto/orpo/grpo, got 'spo'" — yes.
2. **Actionable.** State what the user should do. Include the config key, the expected value range, or the command to run.
3. **Not apologetic.** "Oops!" and "Sorry, but" — delete.
4. **Plain English, not jargon.** "CUDA OOM at layer 12" is fine; "Tensor RANK-42 exception in autograd graph" is not.

Template:

```
<what failed> : <key/location> : <why> : <how to fix>

Example:
  Configuration error : training.trainer_type : value 'spo' not recognized :
  must be one of [sft, dpo, simpo, kto, orpo, grpo]. See docs/reference/configuration.md.
```

## Auto-revert

When evaluation gates fail after training (`safety.py`, `benchmark.py`), `trainer.py` deletes the trained artifacts and exits with `EXIT_EVAL_FAILURE` (3). This is a feature, not an error:

- The audit log entry explaining **why** the model was reverted must be written before cleanup.
- The model card must **not** be generated for reverted runs.
- The webhook notification must fire with `status: "failed"` and the reason.

Reverting is a deliberate gate, not a panic. Treat it that way.

## What errors look like in JSON output

When `--output-format json` is set, errors still go to stdout as a single JSON object.  **Shipped envelope (canonical, used by every CLI subcommand as of v0.5.5):**

```json
{
  "success": false,
  "error": "training.trainer_type must be one of [sft, dpo, simpo, kto, orpo, grpo], got 'spo'"
}
```

The 2-key shape is intentionally minimal so every subcommand can emit it without coupling to a richer error model: `success: false` is the unambiguous CI gate signal (paired with the non-zero exit code from `$?`); `error` is the operator-actionable message.

**Optional richer fields** that subcommands MAY add when they have the information at hand (none required, but consumers can rely on them being absent rather than wrong-typed):

| Field | Type | When to emit |
|---|---|---|
| `exit_code` | int | When the dispatcher knows the exit code at JSON-emit time and wants to save consumers from reading `$?` separately. |
| `error_type` | str | Exception class name (`ConfigError`, `OSError`, etc.) for callers that want to branch on category. |
| `details` | object | Field-level error data (e.g. `{"field": "training.trainer_type", "value": "spo"}`). |

Human-friendly logs still go to **stderr**. Pipeline consumers read stdout. Never mix the two.

### Success envelope

Each subcommand's success envelope wraps the result in a per-command collection key (`checks` for doctor, `pending` / `chain` for approvals, etc.).  The full per-subcommand schema lives in [`docs/usermanuals/en/reference/json-output.md`](../usermanuals/en/reference/json-output.md) (+ TR mirror) — that page is the locked contract per `release.md` ("Changed JSON output key names → MAJOR bump").  Adding a new subcommand without updating that page is a documentation-drift defect.

## Testing error paths

Every custom exception and every non-zero exit path must have a test. See [testing.md](testing.md) for structure. Pattern:

```python
def test_invalid_trainer_type_raises_config_error(tmp_path):
    config_path = tmp_path / "bad.yaml"
    config_path.write_text("training:\n  trainer_type: spo\n...")

    result = subprocess.run(
        ["forgelm", "--config", str(config_path), "--dry-run"],
        capture_output=True, text=True
    )
    assert result.returncode == 1  # EXIT_CONFIG_ERROR
    assert "trainer_type" in result.stderr
```
