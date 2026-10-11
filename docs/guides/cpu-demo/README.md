# Try ForgeLM without a GPU

> Validate a configuration, audit eight synthetic rows, and verify a recorded audit-log chain on CPU.
> **Mirror:** [Türkçe](README-tr.md)

## Run the checks

Use the [contributor setup](../../../CONTRIBUTING.md#your-first-pull-request) to install the checkout.
Copy the sample to a temporary directory so generated reports do not change the recorded files:

```bash
# From the repository root, with ForgeLM installed in the active environment:
FORGELM_DEMO_DIR=$(mktemp -d)
cp -R docs/guides/cpu-demo/. "$FORGELM_DEMO_DIR/"
cd "$FORGELM_DEMO_DIR"
python3 -m forgelm --config config.yaml --dry-run --output-format json
python3 -m forgelm audit tiny.jsonl --output audit --output-format json
python3 -m forgelm verify-audit recorded-output/audit_log.jsonl
```

All three commands exited `0` in the recorded environment. They took approximately 0.18, 0.54, and 0.14 seconds
respectively after installation; these are observations on one machine, not performance promises.
The dry-run validates YAML without loading the model or downloading its weights. The audit runs local detectors;
`verify-audit` reads the included log and sidecar. These checks do not train a model.

## Recorded files

| File | What it contains |
|---|---|
| [config.yaml](config.yaml) | Exact configuration used for the source training run and recorded dry-run |
| [tiny.jsonl](tiny.jsonl) | Eight original, synthetic question-and-answer rows |
| [dry-run.json](dry-run.json) | Unedited stdout from the first command |
| [audit-summary.json](audit-summary.json) | Unedited JSON stdout from the second command |
| [verify-audit.txt](verify-audit.txt) | Unedited stdout from the third command |
| [terminal.txt](terminal.txt) | The third command recorded with `bash -ex`, including its shell trace |
| [audit_log.jsonl](recorded-output/audit_log.jsonl) | Original seven-entry log from the source CPU training run |
| [audit_log.jsonl.manifest.json](recorded-output/audit_log.jsonl.manifest.json) | Original genesis sidecar, kept beside the log |

The README and website embed this exact terminal recording:

```text
+ python3 -m forgelm verify-audit recorded-output/audit_log.jsonl
OK: 7 entries verified
```

The JSON audit summary is included; the full generated report is omitted because it contains an absolute local
source path. Re-running the audit creates that full report in the temporary directory. No generated output was
edited to remove paths, and no model weights are redistributed.

## Recording provenance

Recorded on **11 October 2026**, using ForgeLM **0.11.1rc1**, checkout `61d95a9` (runtime source `4917e7a`),
Python 3.13.14, PyTorch 2.12.1, Transformers 5.13.0, and TRL 1.7.1 on CPU with no CUDA GPU.
The log originated from a real two-step SFT run using `sshleifer/tiny-gpt2`, pinned to Hub revision
`5f91d94bd9cd7190a9f3216ff93cd1dd95f2c7be`. Its source command, run from this sample's working directory, was:

```bash
FORGELM_OPERATOR=cpu-demo python3 -m forgelm --config config.yaml --output-format json
```

That training command exited `0` and changed adapter weights. It required a small model download;
the three checks above do not. The training command is recorded here for provenance, not as a verified
end-to-end model-delivery example. The model bundle and compliance reports are not part of this sample.

## Limits of the evidence

- The tiny synthetic corpus and training metrics are not evidence of model quality, safety, or legal compliance.
- The audit summary reports no detected PII or secrets; regex detectors can miss sensitive content.
  Its quality score describes dataset heuristics, not model performance.
- `verify-audit` checks this unkeyed log's chain and genesis sidecar. It does not authenticate the author,
  validate the meaning of events, or verify model files. No audit secret was used.
- The source training run logged `model.integrity_verified`, but the final bundle's separate
  `verify-integrity` check exited `6`: `deployer_instructions.md` was written after the integrity manifest.
  This sample preserves that event unchanged. A successful log check must not be read as a successful model check.
  This recording predates the finalization-order fix for [#370](https://github.com/HodeTech/ForgeLM/issues/370):
  corrected finalization writes the instructions before the manifest. The captured exit `6` is a historical result.
- The source run also logged `compliance.governance_section_missing` for its absent data audit report.
  The later standalone audit shown here does not retroactively change that training run.
- Training with `--output-format json` also emitted library output before its JSON envelope in this environment.
  The source training stdout is therefore not included as a machine-readable result.

## Related

- [Quick Start](../quickstart.md) — training setup and hardware requirements.
- [Data Audit](../data_audit.md) — detector behavior and audit gates.
- [Audit verification](../../reference/verify_audit.md) — chain, sidecar, and HMAC limits.
- [Model integrity verification](../../reference/verify_integrity_subcommand.md) — the separate model-file check.
