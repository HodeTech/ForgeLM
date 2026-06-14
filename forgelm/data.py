from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from transformers import PreTrainedTokenizer

logger = logging.getLogger("forgelm.data")

# Non-whitespace control characters (C0/C1 ranges, Unicode category ``Cc``)
# that ``clean_text`` strips before tokenisation — NUL, BEL, ESC, and the
# rest of the control set. The whitespace controls ``\t \n \x0b \x0c \r`` are
# deliberately excluded here because ``str.split()`` already collapses them.
_WHITESPACE_CONTROLS = frozenset("\t\n\x0b\x0c\r")
_CONTROL_CHAR_DELETIONS = {
    cp: None for cp in (*range(0x00, 0x20), *range(0x7F, 0xA0)) if chr(cp) not in _WHITESPACE_CONTROLS
}


def _detect_dataset_format(columns: list) -> dict:
    """Detect the most likely dataset format from column names.

    Advisory heuristic ONLY: returns the most likely format plus a suggested
    trainer for use in user-facing error messages. It performs NO validation —
    the authoritative schema gate is :func:`_validate_trainer_columns` (which
    raises ``KeyError`` on a missing column) plus TRL's own column checks at
    train time. Branch order encodes precedence (preference > binary-feedback >
    messages > prompt-only > instruction > text); the first matching branch
    wins, so do not rely on this to reject a malformed dataset.
    """
    if "chosen" in columns and "rejected" in columns:
        return {"description": "preference format (chosen/rejected)", "suggested_trainer": "dpo"}
    if "completion" in columns and "label" in columns:
        return {"description": "binary feedback format (completion/label)", "suggested_trainer": "kto"}
    if "messages" in columns:
        return {"description": "conversational format (messages list)", "suggested_trainer": "sft"}
    if "prompt" in columns and "chosen" not in columns:
        return {"description": "prompt-only format", "suggested_trainer": "grpo"}
    if any(c in columns for c in ("User", "instruction")) and any(
        c in columns for c in ("Assistant", "output", "response")
    ):
        return {"description": "instruction-tuning format (User/Assistant)", "suggested_trainer": "sft"}
    if "text" in columns:
        return {"description": "pre-formatted text column", "suggested_trainer": "sft"}
    return {"description": f"unknown format ({', '.join(columns[:5])})", "suggested_trainer": "sft"}


def clean_string(text: str, do_clean: bool) -> str:
    """Normalise a single string cell, optionally collapsing whitespace and
    stripping control characters.

    When ``do_clean`` is set (the ``DataConfig.clean_text`` field), this
    removes non-whitespace control characters (NUL, BEL, ESC, and the rest of
    the C0/C1 ``Cc`` set) and collapses runs of whitespace into single spaces
    — matching the field's documented "strip excessive whitespace + control
    characters" contract. ``str.split()`` alone only handles the *whitespace*
    control chars (``\\t \\n \\x0b \\x0c \\r``); NUL/BEL/ESC would otherwise
    pass into the tokeniser verbatim.

    Rejects non-string payloads loudly — symmetric with
    ``_process_messages_format`` (see its docstring). The previous behaviour
    coerced any non-string via ``str()`` (``{'a': 1}`` → ``"{'a': 1}"``,
    ``42`` → ``"42"``) and silently mapped ``None``/falsy values to ``""``,
    which baked schema bugs (a dict/int/None where a string was expected)
    straight into the training corpus with only a WARNING for ``None``. The
    text and User/Assistant formats route every cell through here, so a
    malformed row now fails the run at this chokepoint instead of training
    the model on Python ``repr`` strings or empty responses.

    Raises:
        ValueError: when ``text`` is not a ``str`` (including ``None``). The
            caller is responsible for omitting genuinely-absent optional
            cells (e.g. a missing system prompt) before reaching here.
    """
    if not isinstance(text, str):
        raise ValueError(
            f"Malformed dataset cell: expected a string, got {type(text).__name__}. "
            "Each text/User/Assistant cell must be a string; fix the offending "
            "row in your dataset (a null, number, or nested object where text "
            "was expected is a schema bug, not training data)."
        )
    if do_clean:
        return " ".join(text.translate(_CONTROL_CHAR_DELETIONS).split())
    return text


def _load_single_dataset(path: str):
    """Load a single dataset from a local file or HF Hub."""
    from datasets import load_dataset

    if os.path.isfile(path):
        _, ext_with_dot = os.path.splitext(path)
        ext = ext_with_dot.lstrip(".").lower()
        if not ext:
            raise ValueError(
                f"Cannot determine file format for '{path}': no file extension found. "
                "Rename the file with a supported extension: .json, .jsonl, .csv, or .parquet."
            )
        if ext == "jsonl":
            ext = "json"
        return load_dataset(ext, data_files=path)
    return load_dataset(path)


def _process_text_format(examples: dict, clean_text: bool, add_eos: bool, eos_token: str) -> dict:
    """Pre-formatted text column (e.g., openassistant-guanaco)."""
    texts = []
    for idx, t in enumerate(examples["text"]):
        try:
            t = clean_string(t, clean_text)
        except ValueError as e:
            raise ValueError(f"Malformed text-format row at index {idx}: {e}") from e
        if add_eos and t and eos_token and not t.endswith(eos_token):
            t += eos_token
        texts.append(t)
    return {"text": texts}


def _process_messages_format(examples: dict, add_eos: bool, eos_token: str) -> dict:
    """Modern conversational format (messages column).

    Raises ``ValueError`` on a malformed row so the trainer fails loud
    rather than silently producing empty training strings — the previous
    behaviour was to catch ``Exception`` and substitute ``""``, which
    masked schema bugs (missing ``role`` / ``content`` keys, non-string
    payloads) until the model trained on a corpus of empty rows.
    """
    texts = []
    for idx, msg_list in enumerate(examples["messages"]):
        try:
            # apply_chat_template is not available here (no tokenizer reference);
            # use fallback formatting — callers that need chat templates should
            # pass a tokenizer-aware processor instead.
            chunks: List[str] = []
            for m in msg_list:
                role = m.get("role")
                content = m.get("content")
                # f-strings silently coerce non-string content via __str__ /
                # __format__, which would mask a schema bug (e.g. content
                # accidentally a dict / int) all the way through training.
                # Validate explicitly so the row is rejected loudly here.
                if not isinstance(role, str):
                    raise ValueError(
                        f"Malformed messages-format row at index {idx}: "
                        f"'role' must be a string, got {type(role).__name__}."
                    )
                if not isinstance(content, str):
                    raise ValueError(
                        f"Malformed messages-format row at index {idx}: "
                        f"'content' must be a string, got {type(content).__name__}."
                    )
                chunks.append(f"[{role.upper()}]\n{content}\n")
            # An empty ``messages`` list (``[]``) produces no chunks, so the
            # formatted text would be ``""`` (or a bare eos_token) — the exact
            # "corpus of empty rows" failure the loud-raise rewrite exists to
            # prevent. Reject it loudly instead of appending a blank sample.
            if not chunks:
                raise ValueError(
                    f"Malformed messages-format row at index {idx}: "
                    "'messages' list is empty (no role/content turns to format)."
                )
            formatted_text = "".join(chunks)
            if add_eos and eos_token:
                formatted_text += eos_token
        except (KeyError, TypeError, AttributeError) as e:
            # KeyError: missing 'role' or 'content'; TypeError: msg_list not
            # iterable / m not subscriptable; AttributeError: role not str.
            # Each is a real schema bug — surface it with row index so the
            # operator can locate the broken record in their JSONL.
            raise ValueError(
                f"Malformed messages-format row at index {idx}: {e}. "
                "Each row's 'messages' column must be a list of "
                "{'role': str, 'content': str} dicts."
            ) from e
        texts.append(formatted_text)
    return {"text": texts}


def _format_user_assistant_row(
    sys_text: str, user_text: str, asst_text: str, clean_text: bool, add_eos: bool, eos_token: str
) -> str:
    """Render a single (System?, User, Assistant) row into a flat training string."""
    sys_clean = clean_string(sys_text, clean_text) if sys_text else ""
    user_clean = clean_string(user_text, clean_text)
    asst_clean = clean_string(asst_text, clean_text)
    sys_part = f"[SYSTEM]\n{sys_clean}\n" if sys_text else ""
    formatted_text = sys_part + f"[USER]\n{user_clean}\n[ASSISTANT]\n{asst_clean}"
    if add_eos and eos_token:
        formatted_text += eos_token
    return formatted_text


def _process_user_assistant_format(examples: dict, clean_text: bool, add_eos: bool, eos_token: str) -> dict:
    """Legacy User/Assistant or instruction/output column layout."""
    has_system = "System" in examples
    has_user = "User" in examples or "instruction" in examples
    has_assistant = "Assistant" in examples or "output" in examples or "response" in examples

    # Distinguish "wrong schema" (raise) from "empty batch" (return empty list).
    # Truthiness on the column list would conflate the two.
    if not has_user or not has_assistant:
        fmt = _detect_dataset_format(list(examples.keys()))
        raise KeyError(
            f"Dataset must contain 'User'/'instruction' and 'Assistant'/'output' columns, "
            f"or a pre-formatted 'text' column. "
            f"Found: {list(examples.keys())}. "
            f"Detected format: {fmt['description']}. "
            f"Suggested trainer: {fmt['suggested_trainer']}"
        )

    user_texts = examples.get("User", examples.get("instruction", []))
    asst_texts = examples.get("Assistant", examples.get("output", examples.get("response", [])))
    sys_texts = examples["System"] if has_system else [""] * len(user_texts)
    texts = []
    for idx, (s, u, a) in enumerate(zip(sys_texts, user_texts, asst_texts, strict=True)):
        try:
            texts.append(_format_user_assistant_row(s, u, a, clean_text, add_eos, eos_token))
        except ValueError as e:
            raise ValueError(f"Malformed User/Assistant-format row at index {idx}: {e}") from e
    return {"text": texts}


def _make_batch_processor(clean_text: bool, add_eos: bool, eos_token: str):
    """
    Returns a multiprocessing-safe batch processor.
    Uses primitives only — avoids pickle issues with closures over complex objects.
    """

    def process_batch(examples):
        if "text" in examples and "User" not in examples and "messages" not in examples:
            return _process_text_format(examples, clean_text, add_eos, eos_token)
        if "messages" in examples:
            return _process_messages_format(examples, add_eos, eos_token)
        return _process_user_assistant_format(examples, clean_text, add_eos, eos_token)

    return process_batch


_PREFERENCE_TRAINERS = {"dpo", "simpo", "orpo"}


def _apply_mix_ratio(all_train: list, mix_ratio: list) -> list:
    """Re-sample the per-dataset training splits according to *mix_ratio* weights."""
    total_weight = sum(mix_ratio)
    if total_weight == 0:
        logger.warning("mix_ratio weights sum to 0. Using uniform mixing.")
        return all_train
    normalized = [w / total_weight for w in mix_ratio]
    max_dataset_size = max(len(ds) for ds in all_train)
    sampled = []
    for ds, ratio in zip(all_train, normalized):
        n_samples = min(int(max_dataset_size * ratio), len(ds))
        sampled.append(ds.shuffle(seed=42).select(range(n_samples)))
    logger.info("Applied mix ratios: %s", mix_ratio)
    return sampled


def _merge_extra_datasets(primary_dataset, extra_paths: list, mix_ratio: Optional[list]):
    """Concatenate primary + extra dataset training splits, optionally weighted."""
    from datasets import DatasetDict, concatenate_datasets

    all_train = [primary_dataset["train"]]
    for i, extra_path in enumerate(extra_paths):
        logger.info("Loading extra dataset [%d]: %s", i + 1, extra_path)
        extra_ds = _load_single_dataset(extra_path)
        all_train.append(extra_ds["train"])

    if mix_ratio:
        if len(mix_ratio) != len(all_train):
            # DataConfig._validate_mix_ratio_length guarantees this at config
            # time; reaching here means a non-config caller passed a mismatch.
            # Raise loudly rather than silently re-weighting to a mixture the
            # caller never asked for.
            raise ValueError(f"mix_ratio length ({len(mix_ratio)}) does not match dataset count ({len(all_train)}).")
        all_train = _apply_mix_ratio(all_train, mix_ratio)

    merged_train = concatenate_datasets(all_train)
    logger.info("Merged %d datasets into %d training samples.", len(all_train), len(merged_train))
    dataset = DatasetDict({"train": merged_train})
    if "validation" in primary_dataset:
        dataset["validation"] = primary_dataset["validation"]
    return dataset


def _ensure_validation_split(dataset):
    """Make sure ``dataset['validation']`` exists, deriving it from train if needed."""
    from datasets import DatasetDict

    if "validation" in dataset:
        return dataset
    if "test" in dataset:
        dataset["validation"] = dataset["test"]
        return dataset
    dataset_size = len(dataset["train"])
    if dataset_size < 2:
        logger.warning(
            "Training set has only %d sample(s) — cannot create a validation split. "
            "Evaluation metrics will be unavailable for this run.",
            dataset_size,
        )
        return dataset
    test_size = min(0.1, 2000 / max(dataset_size, 1))
    test_size = max(test_size, 0.01)
    logger.info(
        "No validation split found. Auto-splitting: %.1f%% (%d samples) for validation.",
        test_size * 100,
        int(dataset_size * test_size),
    )
    split_dataset = dataset["train"].train_test_split(test_size=test_size, seed=42)
    return DatasetDict({"train": split_dataset["train"], "validation": split_dataset["test"]})


def _validate_trainer_columns(
    trainer_type: str,
    sample_columns: list,
    detected_format: dict,
    has_chosen_rejected: bool,
    has_kto_format: bool,
) -> None:
    """Raise KeyError when the loaded dataset doesn't match the trainer's expected schema."""
    if trainer_type in _PREFERENCE_TRAINERS and not has_chosen_rejected:
        raise KeyError(
            f"{trainer_type.upper()} trainer requires 'chosen' and 'rejected' columns, "
            f"but found: {', '.join(sample_columns)}.\n\n"
            f"Your dataset looks like: {detected_format['description']}\n"
            f'Suggested: Use trainer_type: "{detected_format["suggested_trainer"]}" instead, '
            f"or convert your data to preference format (prompt + chosen + rejected)."
        )
    if trainer_type == "kto" and not has_kto_format:
        raise KeyError(
            f"KTO trainer requires 'completion' and 'label' (boolean) columns, "
            f"but found: {', '.join(sample_columns)}.\n\n"
            f"Your dataset looks like: {detected_format['description']}\n"
            f'Suggested: Use trainer_type: "{detected_format["suggested_trainer"]}" instead, '
            f"or convert your data to KTO format (prompt + completion + label)."
        )
    if trainer_type == "grpo" and "prompt" not in sample_columns:
        raise KeyError(
            f"GRPO trainer requires a 'prompt' column, "
            f"but found: {', '.join(sample_columns)}.\n\n"
            f"Your dataset looks like: {detected_format['description']}\n"
            f'Suggested: Use trainer_type: "{detected_format["suggested_trainer"]}" instead, '
            f"or convert your data to prompt-only format."
        )


def _shuffle_and_passthrough(dataset, shuffle: bool) -> Dict[str, Any]:
    """Return splits as-is — for trainers that need raw columns.

    Only the ``train`` split is shuffled when ``shuffle=True``; validation
    and test splits are preserved in their original order so evaluation is
    reproducible across runs and metrics line up sample-by-sample.
    """
    out: Dict[str, Any] = {}
    for split in dataset:
        current = dataset[split]
        if shuffle and split == "train":
            current = current.shuffle(seed=42)
        out[split] = current
    return out


def _passthrough_for_trainer(trainer_type: str, dataset, shuffle: bool) -> Optional[Dict[str, Any]]:
    """If trainer takes raw preference/KTO/GRPO columns, return splits as-is; else None."""
    if trainer_type in _PREFERENCE_TRAINERS:
        logger.info("Detected preference dataset (chosen/rejected) for %s training.", trainer_type.upper())
        return _shuffle_and_passthrough(dataset, shuffle)
    if trainer_type == "kto":
        logger.info("Detected KTO dataset (completion/label) for KTO training.")
        return _shuffle_and_passthrough(dataset, shuffle)
    if trainer_type == "grpo":
        logger.info("Detected prompt dataset for GRPO training.")
        return _shuffle_and_passthrough(dataset, shuffle)
    return None


def _passthrough_multimodal(config: Any, dataset, sample_columns: list) -> Optional[Dict[str, Any]]:
    """Multimodal VLM datasets pass through unchanged so the VLM processor can run."""
    mm_cfg = getattr(config.model, "multimodal", None)
    if not (mm_cfg and mm_cfg.enabled):
        return None
    image_col = mm_cfg.image_column
    text_col = mm_cfg.text_column
    if image_col not in sample_columns:
        raise KeyError(
            f"Multimodal mode enabled but image column '{image_col}' not found. "
            f"Found columns: {', '.join(sample_columns)}. "
            f"Set model.multimodal.image_column to match your dataset."
        )
    logger.info(
        "Multimodal VLM dataset detected (image='%s', text='%s'). Passing through for VLM processor handling.",
        image_col,
        text_col,
    )
    return _shuffle_and_passthrough(dataset, config.data.shuffle)


def _format_sft_dataset(dataset, processor, shuffle: bool) -> Dict[str, Any]:
    """Apply the SFT chat-template formatter across all splits.

    Only the ``train`` split is shuffled — keeping validation/test order
    stable preserves reproducible eval metrics across runs.
    """
    logger.info("Formatting dataset with Chat Templates...")
    processed: Dict[str, Any] = {}
    for split in dataset:
        current = dataset[split]
        if shuffle and split == "train":
            current = current.shuffle(seed=42)
        processed[split] = current.map(
            processor,
            batched=True,
            remove_columns=current.column_names,
            num_proc=min(os.cpu_count() or 1, 8),
            desc=f"Formatting {split} split",
        )
    return processed


def prepare_dataset(config: Any, tokenizer: PreTrainedTokenizer) -> Dict[str, Any]:
    """Loads and tokenizes the dataset based on ForgeConfig."""
    logger.info("Loading dataset from %s...", config.data.dataset_name_or_path)
    primary_dataset = _load_single_dataset(config.data.dataset_name_or_path)
    dataset = primary_dataset

    extra_datasets = getattr(config.data, "extra_datasets", None)
    if extra_datasets:
        dataset = _merge_extra_datasets(
            primary_dataset,
            extra_datasets,
            getattr(config.data, "mix_ratio", None),
        )

    dataset = _ensure_validation_split(dataset)

    sample_columns = dataset["train"].column_names if "train" in dataset else []

    multimodal = _passthrough_multimodal(config, dataset, sample_columns)
    if multimodal is not None:
        return multimodal

    trainer_type = getattr(config.training, "trainer_type", "sft")
    has_chosen_rejected = "chosen" in sample_columns and "rejected" in sample_columns
    has_kto_format = "completion" in sample_columns and "label" in sample_columns
    detected_format = _detect_dataset_format(sample_columns)
    _validate_trainer_columns(trainer_type, sample_columns, detected_format, has_chosen_rejected, has_kto_format)

    raw = _passthrough_for_trainer(trainer_type, dataset, config.data.shuffle)
    if raw is not None:
        return raw

    processor = _make_batch_processor(
        clean_text=config.data.clean_text,
        add_eos=config.data.add_eos,
        eos_token=tokenizer.eos_token or "",
    )
    return _format_sft_dataset(dataset, processor, config.data.shuffle)
