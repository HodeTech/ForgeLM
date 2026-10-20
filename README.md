<p align="center">
  <img src="https://raw.githubusercontent.com/HodeTech/ForgeLM/development/docs/assets/forgelm-mark.svg" alt="ForgeLM logo" width="72" height="72">
</p>

<h1 align="center">ForgeLM</h1>

<p align="center">
  <strong>Fine-tune LLMs. Keep the evidence.</strong><br>
  YAML-driven training, evaluation gates, and audit artifacts — from your terminal, Python, or CI.
</p>

<p align="center">
  <a href="https://pypi.org/project/forgelm/"><img src="https://img.shields.io/pypi/v/forgelm.svg" alt="PyPI version"></a>
  <a href="https://github.com/HodeTech/ForgeLM/actions/workflows/ci.yml"><img src="https://github.com/HodeTech/ForgeLM/actions/workflows/ci.yml/badge.svg?branch=development" alt="CI on development"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.10%E2%80%933.13-blue.svg" alt="Tested with Python 3.10–3.13"></a>
  <a href="https://github.com/HodeTech/ForgeLM/blob/main/LICENSE"><img src="https://img.shields.io/badge/code-Apache_2.0-blue.svg" alt="Code license: Apache 2.0"></a>
</p>

<p align="center">
  <a href="https://hodetech.github.io/ForgeLM/">Website</a> ·
  <a href="#try-it-without-a-gpu">Quick start</a> ·
  <a href="#documentation">Documentation</a> ·
  <a href="#contribute">Contribute</a> ·
  <a href="https://github.com/HodeTech/ForgeLM/blob/main/docs/guides/quickstart-tr.md">Türkçe</a>
</p>

ForgeLM turns a validated YAML configuration into a fine-tuning run, a saved model,
and an audit trail. Start with SFT, use preference or reasoning methods when you need
them, and opt into safety evaluation, human approval, and EU AI Act documentation.
The same configuration works locally and in a CI/CD pipeline.

**Here to contribute?** Pick an [unassigned good first issue](https://github.com/HodeTech/ForgeLM/issues?q=is%3Aissue%20is%3Aopen%20label%3A%22good%20first%20issue%22%20no%3Aassignee%20-label%3Ablocked)
and follow [your first pull request](https://github.com/HodeTech/ForgeLM/blob/development/CONTRIBUTING.md#your-first-pull-request).
Documentation, Turkish translations, examples, and tests are welcome alongside code.

## Why ForgeLM

| You need to… | ForgeLM provides |
|---|---|
| Keep training reproducible | Validated YAML, model revision pins, and a typed [Python API](https://github.com/HodeTech/ForgeLM/blob/main/docs/guides/library_api.md). |
| Move beyond a single SFT run | **SFT · DPO · SimPO · KTO · ORPO · GRPO**, plus sequential [pipelines](https://github.com/HodeTech/ForgeLM/blob/main/docs/guides/pipeline.md) such as SFT → DPO → GRPO. |
| Check data before training | Dataset audits for PII, secrets, duplicates, leakage, and quality; document-to-JSONL ingestion with masking. |
| Make promotion conditional | Opt-in loss, benchmark, safety, and judge gates; human-approval staging and stable CLI exit codes. |
| Keep reviewable evidence | Hash-chained audit logs, model-integrity manifests, model cards, and optional compliance exports. |
| Use your own infrastructure | LoRA/QLoRA, optional Unsloth and distributed backends, plus export and serving configuration generation. |

ForgeLM fine-tunes existing Hugging Face models. Serving is handed off to tools such
as Ollama, vLLM, TGI, or llama.cpp. See the [architecture](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/architecture.md)
for how the pieces fit together.
ORPO and SimPO may use experimental TRL APIs; see
[alignment compatibility](https://github.com/HodeTech/ForgeLM/blob/main/docs/guides/alignment.md).

## Try it without a GPU

Use **Python 3.10–3.13** in a virtual environment. Install the core package, then
generate a starter configuration and a tiny customer-support dataset:

```bash
python -m pip install forgelm
python -m forgelm quickstart customer-support --output configs/support.yaml --dry-run
```

This writes `configs/support.yaml`, `configs/customer-support.jsonl`, and a
quickstart audit record. **The generation step downloads no model and starts no
training.** Use a fresh output directory when repeating it, so your edited dataset
is not overwritten. The bundled data is a demonstration seed, not a production corpus.

Validate the generated YAML and audit the seed dataset:

```bash
python -m forgelm --config configs/support.yaml --dry-run
python -m forgelm audit ./configs/customer-support.jsonl
```

List other templates with `python -m forgelm quickstart --list`, or generate a
configuration interactively with `python -m forgelm --wizard`.
For a recorded CPU walkthrough of configuration validation, dataset auditing, and
audit-log verification, see the [CPU sample and its limitations](https://github.com/HodeTech/ForgeLM/blob/development/docs/guides/cpu-demo/README.md).

### When you are ready to train

Review the generated model, dataset, and output paths first. The bundled templates
enable 4-bit loading: on **Linux + CUDA**, install the QLoRA extra, estimate memory,
and start training explicitly:

```bash
python -m pip install "forgelm[qlora]"
python -m forgelm --config configs/support.yaml --fit-check
python -m forgelm --config configs/support.yaml
```

The memory estimate is guidance, not a guarantee that a run will fit. For Apple
Silicon or another unquantized setup, set `model.load_in_4bit: false` and choose a
model that fits your hardware. Intel Macs are unsupported by the required PyTorch
versions. See [installation and platform support](https://github.com/HodeTech/ForgeLM/blob/main/docs/usermanuals/en/getting-started/installation.md)
for optional backends and extras.

## One configuration, a reviewable workflow

The generated YAML is yours to edit. For example, this standalone SFT configuration
uses the seed dataset created above and saves to `./checkpoints/customer-support`:

```yaml
model:
  name_or_path: "Qwen/Qwen2.5-7B-Instruct"
  load_in_4bit: true

data:
  dataset_name_or_path: "./configs/customer-support.jsonl"

training:
  trainer_type: "sft"
  output_dir: "./checkpoints/customer-support"
  num_train_epochs: 3

lora:
  r: 8
  alpha: 16
```

Configuration keys and cross-field constraints are validated before training.
Add safety, compliance, or a multi-stage pipeline when your workflow needs them:
[configuration reference](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/configuration.md) ·
[alignment methods](https://github.com/HodeTech/ForgeLM/blob/main/docs/guides/alignment.md) ·
[pipeline guide](https://github.com/HodeTech/ForgeLM/blob/main/docs/guides/pipeline.md).

**Data → configure → train → evaluate → review → export.**
Audit your own corpus before committing GPU time. After training, use
`python -m forgelm chat ./checkpoints/customer-support/final_model`
to inspect the result, then follow the [post-training examples](https://github.com/HodeTech/ForgeLM/tree/main/notebooks)
for export and deployment. K-quant GGUF export also requires a separate llama.cpp
quantization step; see the [CLI reference](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/usage.md).

## Evidence and gates

| Artifact or control | What it does |
|---|---|
| Saved model and model card | Records the trained output and its configuration. |
| Model-integrity manifest | Detects changed, missing, or added files with `verify-integrity`. |
| Append-only audit log | Records decisions in a hash chain, checked with `verify-audit`. |
| Optional evaluation gates | Apply configured loss, benchmark, safety, or judge thresholds. |
| Optional human approval | Stages a model for review before promotion; the CLI exits `4`. |
| Optional compliance export | Produces Annex IV metadata, data-governance reports, and training provenance. |

CLI exit codes let your pipeline distinguish configuration errors (`1`), runtime
errors (`2`), evaluation failures (`3`), pending approval (`4`), wizard cancellation
(`5`), and integrity mismatches (`6`); success is `0`. See the
[CLI contract](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/usage.md).

Safety evaluation, compliance export, human approval, and auto-revert are opt-in.
With `evaluation.auto_revert: true`, a failed evaluation can delete the saved model
directory; review the [gate behavior](https://github.com/HodeTech/ForgeLM/blob/main/docs/guides/safety_compliance.md)
before enabling it. Evidence artifacts support a review process; they do not
certify model safety or legal compliance. For adversarial tamper-evidence, configure
`FORGELM_AUDIT_SECRET` and protect storage — an unkeyed hash alone is insufficient.

## Documentation

| Start here | Go deeper |
|---|---|
| [Quick start](https://github.com/HodeTech/ForgeLM/blob/main/docs/guides/quickstart.md) | [Configuration reference](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/configuration.md) |
| [Dataset auditing](https://github.com/HodeTech/ForgeLM/blob/main/docs/guides/data_audit.md) | [Safety and compliance](https://github.com/HodeTech/ForgeLM/blob/main/docs/guides/safety_compliance.md) |
| [Document ingestion](https://github.com/HodeTech/ForgeLM/blob/main/docs/guides/ingestion.md) | [Enterprise deployment](https://github.com/HodeTech/ForgeLM/blob/main/docs/guides/enterprise_deployment.md) |
| [Python API](https://github.com/HodeTech/ForgeLM/blob/main/docs/guides/library_api.md) | [CI/CD integration](https://github.com/HodeTech/ForgeLM/blob/main/docs/guides/cicd_pipeline.md) |
| [Troubleshooting](https://github.com/HodeTech/ForgeLM/blob/main/docs/guides/troubleshooting.md) | [Public roadmap](https://github.com/HodeTech/ForgeLM/blob/main/docs/roadmap.md) |

**Türkçe:** [Hızlı başlangıç](https://github.com/HodeTech/ForgeLM/blob/main/docs/guides/quickstart-tr.md) ·
[Yapılandırma](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/configuration-tr.md) ·
[Veri denetimi](https://github.com/HodeTech/ForgeLM/blob/main/docs/guides/data_audit-tr.md) ·
[Güvenlik ve uyumluluk](https://github.com/HodeTech/ForgeLM/blob/main/docs/guides/safety_compliance-tr.md).

**Learn in a notebook:** [SFT fine-tuning in Colab](https://colab.research.google.com/github/HodeTech/ForgeLM/blob/main/notebooks/quickstart_sft.ipynb) ·
[GRPO reasoning in Colab](https://colab.research.google.com/github/HodeTech/ForgeLM/blob/main/notebooks/grpo_reasoning.ipynb) ·
[all notebooks](https://github.com/HodeTech/ForgeLM/tree/main/notebooks).
Model access, dependencies, and hardware requirements are documented in each notebook.

## Contribute

Help make fine-tuning easier to reproduce and review. You can improve a guide,
translate an example, reproduce a bug, add a regression test, or work on a planned
feature. Start small; you do not need a GPU to run the unit tests or documentation checks.

1. Start at the [Front door](https://github.com/orgs/HodeTech/projects/5/views/16): the open, unassigned `good first issue`s
   with their size and area. You can also [search by label](https://github.com/HodeTech/ForgeLM/issues?q=is%3Aissue%20is%3Aopen%20label%3A%22good%20first%20issue%22%20no%3Aassignee%20-label%3Ablocked).
   The rest of the planned work is in the [project plan](https://github.com/orgs/HodeTech/projects/5).
2. Follow [your first pull request](https://github.com/HodeTech/ForgeLM/blob/development/CONTRIBUTING.md#your-first-pull-request)
   to set up the environment and run the relevant checks.
3. Open a PR against **`development`** and link its issue. Keep English/Turkish
   documentation mirrors together where they exist.

[Report a bug](https://github.com/HodeTech/ForgeLM/issues/new?template=bug_report.yml) ·
[Suggest a feature](https://github.com/HodeTech/ForgeLM/issues/new?template=feature_request.yml) ·
[Engineering standards](https://github.com/HodeTech/ForgeLM/tree/development/docs/standards).
Report vulnerabilities privately using [SECURITY.md](https://github.com/HodeTech/ForgeLM/blob/main/SECURITY.md).

Who decides what, and who owns which area: [GOVERNANCE.md](https://github.com/HodeTech/ForgeLM/blob/development/GOVERNANCE.md) ·
[MAINTAINERS.md](https://github.com/HodeTech/ForgeLM/blob/development/MAINTAINERS.md).
To cite ForgeLM, use GitHub's **Cite this repository** button or
[`CITATION.cff`](https://github.com/HodeTech/ForgeLM/blob/development/CITATION.cff).

## License

**Code:** [Apache 2.0](https://github.com/HodeTech/ForgeLM/blob/main/LICENSE).
**Bundled seed datasets:** [CC-BY-SA 4.0](https://github.com/HodeTech/ForgeLM/blob/main/forgelm/templates/LICENSES.md),
with attribution and share-alike requirements. Models and datasets you bring retain
their respective licenses; review them before training or distributing artifacts.
