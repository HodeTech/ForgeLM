"""Automatic model card generation for fine-tuned models.

Generates a HuggingFace-compatible README.md (model card) with training
configuration, metrics, dataset info, and evaluation results.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger("forgelm.model_card")

# Substrings that mark a config field as secret-bearing. ``*_env`` fields hold
# the NAME of an env var (e.g. ``OPENAI_API_KEY``), not the secret itself, so
# they are excluded from redaction below.
_SECRET_KEY_TOKENS = ("api_key", "token", "secret", "password", "passwd", "credential")


def _is_secret_key(key: Any) -> bool:
    k = str(key).lower()
    if k.endswith("_env"):
        return False  # env-var-name reference, not the secret value
    return any(tok in k for tok in _SECRET_KEY_TOKENS)


# Characters that let an operator-controlled model/dataset/path string break
# out of its Markdown context (table cells, the H1 heading, the YAML
# front-matter, and the fenced Python usage block all embed these fields). We
# *strip* rather than backslash-escape because the same value is interpolated
# into a fenced code block and the YAML front-matter, where a literal backslash
# would either show verbatim or break the parser. Path-legal characters
# (``/ . - _``) are intentionally preserved so ``org/model`` stays intact —
# they carry no injection power in these inline contexts (F-P4-OPUS-30).
_MD_INJECTION_CHARS = "`|[]()<>\r\n*#"


def _neutralize_md_inline(text: Any) -> str:
    """Strip Markdown/link/table-injection characters from a config-derived field.

    Sibling discipline to ``compliance._sanitize_md`` (which escapes the
    Article-13 deployer instructions). The model card reuses each field across
    YAML front-matter, table cells, a heading, and a fenced code block, so this
    removes the injection-capable characters instead of backslash-escaping —
    keeping the value valid in every one of those contexts.
    """
    s = str(text)
    return "".join(ch for ch in s if ch not in _MD_INJECTION_CHARS).strip()


def _codefence_for(content: str) -> str:
    """Return a backtick fence guaranteed to safely wrap *content*.

    Per CommonMark, a fenced code block cannot be closed by a backtick run
    shorter than its own fence. Sizing the fence one backtick longer than the
    longest backtick run in *content* makes it impossible for an
    operator-supplied free-text config field (``risk_assessment.intended_use``,
    ``compliance.known_limitations``, ``data.governance.known_biases`` ...) to
    break out of the ``config`` YAML block, regardless of nesting depth or
    PyYAML's indentation behaviour. Minimum length is the conventional 3.

    This makes the fence-containment property an explicit, testable invariant
    rather than an emergent consequence of every free-text field happening to
    be nested (which a future top-level field or a lenient downstream Markdown
    parser could silently break).
    """
    longest = 0
    run = 0
    for ch in content:
        if ch == "`":
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    return "`" * max(3, longest + 1)


def _redact_secrets(value: Any) -> Any:
    """Recursively redact any dict value whose key signals a secret.

    Defence-in-depth over the model-card config dump: the nested
    ``model_dump`` redaction overrides (``SyntheticConfig.api_key``,
    ``AuthConfig.hf_token``) are BYPASSED when the parent ``ForgeConfig``
    serializes — Pydantic v2's parent ``model_dump`` recurses via its own
    serializer, not the child class's Python-level override — so a populated
    ``synthetic.api_key`` would otherwise land in ``README.md`` in plaintext
    (F-P1-FAB-01). This masks any residual secret regardless of which section
    it lives in, complementing the section-level ``exclude`` below.
    """
    if isinstance(value, dict):
        return {k: ("***REDACTED***" if _is_secret_key(k) and v else _redact_secrets(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_secrets(v) for v in value]
    return value


MODEL_CARD_TEMPLATE = """---
language:
- en
tags:
- forgelm
- fine-tuned
- lora
{extra_tags}
base_model: {base_model}
---

# {model_name}

Fine-tuned with [ForgeLM](https://github.com/HodeTech/ForgeLM) — config-driven LLM fine-tuning toolkit.

## Training Details

| Parameter | Value |
|-----------|-------|
| Base Model | `{base_model}` |
| Backend | {backend} |
| Fine-Tuning Method | {method} |
| LoRA Rank (r) | {lora_r} |
| LoRA Alpha | {lora_alpha} |
| DoRA | {use_dora} |
| Target Modules | {target_modules} |
| Epochs | {epochs} |
| Batch Size | {batch_size} |
| Learning Rate | {learning_rate} |
| Max Sequence Length | {max_length} |
| Quantization | {quantization} |
| Dataset | `{dataset}` |
| Training Date | {date} |

## Metrics

{metrics_table}

{benchmark_section}

{safety_section}

## Configuration

This model was trained using the following ForgeLM YAML configuration:

{config_block}

## Usage

```python
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer

base_model = AutoModelForCausalLM.from_pretrained("{base_model}"{base_model_revision_arg})
model = PeftModel.from_pretrained(base_model, "{model_path}")
tokenizer = AutoTokenizer.from_pretrained("{model_path}")
```

---
*Generated by ForgeLM v{version}*
"""


def _build_metrics_table(metrics: Dict[str, float]) -> str:
    """Render the eval metrics dict as a markdown table (or a placeholder).

    Filters out namespaced keys (anything containing ``/``): benchmark/...,
    safety/..., resource/..., judge/... each have a dedicated section in
    the model card, so duplicating them here just clutters the metrics table.
    Only top-level training metrics like ``eval_loss`` survive.
    """
    if not metrics:
        return "*No metrics available.*"
    lines = ["| Metric | Value |", "|--------|-------|"]
    for key, value in sorted(metrics.items()):
        if "/" in key:
            continue
        lines.append(f"| {key} | {value:.4f} |")
    return "\n".join(lines)


def _build_benchmark_section(
    benchmark_scores: Optional[Dict[str, float]],
    benchmark_average: Optional[float],
) -> str:
    """Render the benchmark section, empty string when no benchmark was run."""
    if not benchmark_scores:
        return ""
    lines = ["## Benchmark Results", "", "| Task | Score |", "|------|-------|"]
    for task, score in sorted(benchmark_scores.items()):
        lines.append(f"| {task} | {score:.4f} |")
    if benchmark_average is not None:
        lines.append(f"| **Average** | **{benchmark_average:.4f}** |")
    return "\n".join(lines)


def _build_safety_section(
    safety_cfg: Any,
    safety_score: Optional[float],
    safety_categories: Optional[Dict[str, int]],
) -> str:
    """Render the safety section, empty string when safety eval was disabled."""
    if not (safety_cfg and safety_cfg.enabled):
        return ""
    lines = [
        "## Safety Evaluation",
        "",
        f"- **Scoring method:** {getattr(safety_cfg, 'scoring', 'binary')}",
        f"- **Classifier:** `{safety_cfg.classifier}`",
    ]
    if safety_score is not None:
        lines.append(f"- **Safety score:** {safety_score:.4f}")
    if safety_categories:
        lines.append("- **Harm categories detected:**")
        for cat, count in sorted(safety_categories.items()):
            lines.append(f"  - {cat}: {count}")
    return "\n".join(lines)


def _format_method(config: Any, trainer_type: str) -> str:
    """Compose the human-readable training-method string for the card."""
    method = "QLoRA (4-bit)" if config.model.load_in_4bit else "LoRA"
    if trainer_type != "sft":
        method += f" + {trainer_type.upper()}"
    if config.lora.use_dora:
        method += " + DoRA"
    return method


def _build_extra_tags(config: Any, trainer_type: str, safety_cfg: Any) -> str:
    """Compose the YAML-frontmatter tag list."""
    tags = []
    if config.lora.use_dora:
        tags.append("- dora")
    if config.model.load_in_4bit:
        tags.append("- qlora")
    if trainer_type != "sft":
        tags.append(f"- {trainer_type}")
    if safety_cfg and safety_cfg.enabled:
        tags.append("- safety-evaluated")
    return "\n".join(tags)


def _base_model_revision_arg(config: Any) -> str:
    """Render the ``revision=`` argument for the card's usage snippet.

    Returns ``', revision="<sha>"'`` when a base-model load in *this* process
    was pinned to a confirmed commit, otherwise ``""``.

    Why this is not cosmetic: a card shipped inside an Annex IV bundle that
    tells a downstream reader to ``from_pretrained("org/model")`` with no
    revision hands them the repo's default branch — which is exactly the
    reproducibility property the surrounding bundle claims to establish.  When
    the SHA is unknown the kwarg is omitted entirely rather than emitted as
    ``revision="None"``, because a broken snippet teaches the reader nothing
    and a fabricated one teaches them something false.
    """
    from .model import get_loaded_model_revision

    record = get_loaded_model_revision(config.model.name_or_path)
    sha = (record or {}).get("revision_resolved")
    if not sha:
        return ""
    return f', revision="{_neutralize_md_inline(sha)}"'


def generate_model_card(
    config: Any,
    metrics: Dict[str, float],
    final_path: str,
    benchmark_scores: Optional[Dict[str, float]] = None,
    benchmark_average: Optional[float] = None,
    safety_score: Optional[float] = None,
    safety_categories: Optional[Dict[str, int]] = None,
) -> str:
    """Generate a model card and save it to the final model directory.

    Returns the path to the generated model card.
    """
    import yaml

    from forgelm import __version__

    eval_cfg = getattr(config, "evaluation", None)
    safety_cfg = eval_cfg.safety if (eval_cfg and hasattr(eval_cfg, "safety") and eval_cfg.safety) else None
    trainer_type = getattr(config.training, "trainer_type", "sft")

    metrics_table = _build_metrics_table(metrics)
    benchmark_section = _build_benchmark_section(benchmark_scores, benchmark_average)
    safety_section = _build_safety_section(safety_cfg, safety_score, safety_categories)
    method = _format_method(config, trainer_type)
    extra_tags_str = _build_extra_tags(config, trainer_type, safety_cfg)

    # Serialize config to YAML. Two-layer secret defence:
    #   1. Drop whole secret-bearing / noisy sections (``synthetic`` added — its
    #      ``api_key`` was leaking into the card because the parent-serialization
    #      path bypasses SyntheticConfig.model_dump's redaction — F-P1-FAB-01).
    #   2. Recursively mask any residual secret-keyed value in what remains.
    config_dict = config.model_dump(exclude={"auth", "webhook", "monitoring", "synthetic"})
    config_dict = _redact_secrets(config_dict)
    config_yaml = yaml.dump(config_dict, default_flow_style=False, sort_keys=False)

    # Wrap the dump in a dynamically-sized fence so an operator-controlled
    # free-text field carrying its own backtick run cannot close the block
    # early and inject Markdown (headers, links) into a card frequently
    # published to a public HF Hub repo. See ``_codefence_for``.
    fence = _codefence_for(config_yaml)
    config_block = f"{fence}yaml\n{config_yaml.rstrip()}\n{fence}"

    # Operator-controlled free-text fields are neutralised before interpolation
    # so a crafted model/dataset name or path cannot inject links, break the
    # tables, or escape the YAML front-matter (parity with the deployer
    # instructions' _sanitize_md discipline — F-P4-OPUS-30).
    base_model = _neutralize_md_inline(config.model.name_or_path)
    dataset = _neutralize_md_inline(config.data.dataset_name_or_path)
    model_path = _neutralize_md_inline(final_path)
    model_name = _neutralize_md_inline(config.model.name_or_path.split("/")[-1]) + "_finetune"

    card = MODEL_CARD_TEMPLATE.format(
        model_name=model_name,
        base_model=base_model,
        backend=config.model.backend,
        method=method,
        lora_r=config.lora.r,
        lora_alpha=config.lora.alpha,
        use_dora=config.lora.use_dora,
        target_modules=", ".join(_neutralize_md_inline(m) for m in config.lora.target_modules),
        epochs=config.training.num_train_epochs,
        batch_size=config.training.per_device_train_batch_size,
        learning_rate=config.training.learning_rate,
        max_length=config.model.max_length,
        quantization="4-bit NF4" if config.model.load_in_4bit else "None",
        dataset=dataset,
        date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        metrics_table=metrics_table,
        benchmark_section=benchmark_section,
        safety_section=safety_section,
        config_block=config_block,
        model_path=model_path,
        base_model_revision_arg=_base_model_revision_arg(config),
        version=__version__,
        extra_tags=extra_tags_str,
    )

    card_path = os.path.join(final_path, "README.md")
    os.makedirs(final_path, exist_ok=True)
    with open(card_path, "w", encoding="utf-8") as f:
        f.write(card)

    logger.info("Model card saved to %s", card_path)
    return card_path
