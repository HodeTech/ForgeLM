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


# HF `datasets.load_dataset` builder name for each extension ForgeLM
# accepts for a local file. Any extension not in this map (``.txt``,
# ``.tsv``, ``.xlsx``, ``.arrow``, a typo like ``.jsom``, ...) must be
# rejected here rather than passed through to ``load_dataset`` — an
# unrecognised string is not treated as "invalid builder" by the
# ``datasets`` library, it is treated as a Hugging Face Hub *dataset id*,
# which triggers an outbound network lookup even for a fully local,
# offline-intended run and then fails with a generic "repository not
# found" error instead of an actionable message.
_SUPPORTED_DATASET_EXTENSIONS: Dict[str, str] = {
    "json": "json",
    "jsonl": "json",
    "csv": "csv",
    "parquet": "parquet",
}


# Hub commit SHAs that a *completed* ``load_dataset`` call in this process was
# explicitly pinned to, keyed by the dataset path exactly as it appears in the
# config.  This is the ONLY sanctioned source for the ``hf_revision`` field of
# an Annex IV training manifest: ``forgelm.compliance._fingerprint_hf_revision``
# reads it so the recorded SHA is provably the corpus that was trained on,
# rather than whatever the repo's default branch happened to point at when the
# manifest was written.  Entries are written *after* the load returns, never
# before, so a failed load leaves no claim behind.
_RESOLVED_DATASET_REVISIONS: Dict[str, str] = {}

# Env vars that force HF into offline mode.  Read from the environment (not
# from ``huggingface_hub.constants``) because ``forgelm.cli._config_load``
# sets them at CLI start-up from ``model.offline``, which can happen after
# ``huggingface_hub`` has already been imported and frozen its constants.
#
# ``TRANSFORMERS_OFFLINE`` is honoured alongside the two Hub/datasets vars even
# though nothing here loads ``transformers``: all three express one operator
# intent — *this process must not reach the network* — and the cost of reading
# it too broadly is at worst a missing revision pin, which is honest.  The cost
# of reading it too narrowly is an outbound request from a run the operator
# believed was air-gapped.  ``forgelm.model._HF_OFFLINE_ENV_VARS`` is a sibling
# copy of this tuple and still omits ``TRANSFORMERS_OFFLINE``; the divergence is
# safe in this direction (the dataset path is now strictly more conservative
# than the model path, never less) but the two should be reunited when
# ``model.py`` is next touched.
_HF_OFFLINE_ENV_VARS = ("HF_HUB_OFFLINE", "HF_DATASETS_OFFLINE", "TRANSFORMERS_OFFLINE")
_FALSEY_ENV_VALUES = frozenset({"", "0", "false", "no", "off"})


def _hf_offline_mode() -> bool:
    """True when the environment forbids outbound Hugging Face Hub traffic.

    This is the *ambient* signal only.  It is a fallback, never the primary
    input: every caller that can be told the operator's intent directly takes
    an explicit ``offline`` argument and ORs it with this.  Relying on the
    environment alone made offline-correctness depend on some earlier caller
    having exported a variable — true for ``forgelm`` CLI runs (see
    ``forgelm.cli._config_load._apply_offline_flag``) and false for anyone
    using this package as a library, which is a supported entry point.
    """
    return any(os.environ.get(var, "").strip().lower() not in _FALSEY_ENV_VALUES for var in _HF_OFFLINE_ENV_VARS)


def config_offline(config: Any) -> bool:
    """Read the operator's ``model.offline`` intent off a validated config.

    Tolerates hand-rolled/partial config objects (``getattr`` chain) because
    the provenance path must never be the thing that raises on a caller who
    supplied a duck-typed config.
    """
    return bool(getattr(getattr(config, "model", None), "offline", False))


def _is_commit_sha(value: Any) -> bool:
    """True only for a canonical 40-character lowercase-hex Hub commit SHA.

    Deliberately a ``str`` predicate rather than a compiled pattern: a fixed
    40-char hex test needs no regex, so this adds nothing for
    ``docs/standards/regex.md`` to police.  Anything that is not a real
    commit SHA — a branch name, a tag, ``"main"``, an empty string — must
    never reach a provenance record as if it were one.

    Intentionally duplicated as ``forgelm.compliance._is_commit_sha``.
    ``compliance`` already imports ``data`` (to read the resolved-revision
    registry), so sharing it would either invert the module dependency
    direction from ``architecture.md``'s graph or need a third module —
    which is what the accepted plan's ``forgelm/_hub_revision.py`` would
    have been.  Three lines of fixed-width hex test is the cheaper trade;
    keep the two in lockstep if either changes.
    """
    return isinstance(value, str) and len(value) == 40 and all(c in "0123456789abcdef" for c in value)


def _looks_like_hub_dataset_id(path: str) -> bool:
    """True when ``path`` is a plain Hub repo id (``name`` or ``org/name``).

    Mirrors the predicate ``datasets.load.dataset_module_factory`` uses to
    decide that a string is a Hub id: relative, at most one ``/``, no URL
    scheme, and not something that exists on the local filesystem.  Kept
    conservative on purpose — a false negative only costs us a revision
    record, whereas a false positive would send a local directory path to
    ``HfApi``.
    """
    return bool(
        path
        and not os.path.exists(path)
        and "://" not in path
        and not path.startswith(("/", "~", "."))
        and path.count("/") <= 1
    )


def _resolve_hub_dataset_revision(path: str, *, offline: bool = False) -> Optional[str]:
    """Resolve ``path``'s current Hub commit SHA *before* it is loaded.

    The caller must pass the returned SHA straight into ``load_dataset`` as
    ``revision=``.  That ordering — resolve, then pin the load to what was
    resolved — is what makes the recorded revision truthful: the SHA is not
    an independent guess about what the load *probably* used, it is the SHA
    the load was told to use.

    Why this is necessary at all: ``datasets`` 5.x resolves the commit hash
    internally (``load.py`` ``dataset_module_factory``) and hands it to the
    builder as part of ``base_path``, but the builder is discarded and the
    ``Dataset`` / ``DatasetDict`` that ``load_dataset`` returns exposes no
    commit hash anywhere on its public surface.  ``DatasetInfo`` has only
    ``download_checksums``, which is not populated unless
    ``DownloadManager.record_checksums`` is enabled (it defaults to
    ``False``).  So the resolved revision genuinely cannot be read back off
    the loaded object, and pinning the load forward is the only honest way
    to know it.

    Best-effort throughout: every failure returns ``None``, the caller then
    loads exactly as it did before this function existed, and the manifest
    records the absence rather than a SHA nobody verified.

    ``offline=True`` short-circuits **before any import**, so an air-gapped
    run makes no attempt at network I/O.  Pass it explicitly from
    ``model.offline`` (:func:`config_offline`); the ambient
    :func:`_hf_offline_mode` env check is OR-ed in as a fallback but must not
    be relied on as the only guard.  Before this argument existed the sole
    protection was ``forgelm.cli._config_load._apply_offline_flag`` exporting
    ``HF_HUB_OFFLINE`` early in a CLI run — so a library consumer who set
    ``model.offline: true`` and called into this module directly got twelve
    outbound connection attempts and no warning.
    """
    if offline or _hf_offline_mode():
        logger.debug("Dataset revision resolution skipped for '%s' — offline mode.", path)
        return None

    try:
        from huggingface_hub import HfApi
    except ImportError as e:
        logger.debug("Dataset revision resolution skipped for '%s' — huggingface_hub not installed: %s", path, e)
        return None

    try:
        sha = getattr(HfApi().dataset_info(path), "sha", None)
    except Exception as e:  # noqa: BLE001 — best-effort revision resolution; the HF Hub client surface raises a wide error tail (HfHubHTTPError, RepositoryNotFoundError, RevisionNotFoundError, GatedRepoError, plus the transport OSError/ValueError family) and enumerating it would couple data.py to huggingface_hub internals. The load below proceeds unpinned either way, so this never fails a run.
        logger.debug("Dataset revision resolution failed for '%s': %s", path, e)
        return None

    if not _is_commit_sha(sha):
        logger.debug("HF Hub returned no usable commit SHA for dataset '%s' (got %r).", path, sha)
        return None
    return sha


def get_loaded_dataset_revision(path: str) -> Optional[str]:
    """Return the Hub commit SHA a completed load in this process was pinned to.

    ``None`` means no ``load_dataset`` call in this process pinned ``path``
    to a verified commit — the dataset is local, the resolution was skipped
    (offline, no ``huggingface_hub``), the Hub was unreachable, or nothing
    has been loaded yet (e.g. ``forgelm compliance-only``, which writes a
    manifest without ever touching the corpus).  Callers must treat ``None``
    as "unknown" and must not substitute any other value for it.
    """
    return _RESOLVED_DATASET_REVISIONS.get(path)


def _load_single_dataset(path: str, *, offline: bool = False):
    """Load a single dataset from a local file or HF Hub.

    Hub loads are pinned to a freshly resolved commit SHA where possible so
    the Annex IV manifest can record the corpus that was actually read; see
    :func:`_resolve_hub_dataset_revision`.  When resolution is unavailable
    the call falls back to the historical unpinned form.

    ``offline`` carries ``model.offline`` down from the caller so the
    revision resolution never reaches the network in an air-gapped run even
    when no ``HF_*_OFFLINE`` env var was exported.  It governs *our* lookup
    only; ``datasets.load_dataset`` reads the env vars itself.
    """
    from datasets import load_dataset

    if os.path.isfile(path):
        _, ext_with_dot = os.path.splitext(path)
        ext = ext_with_dot.lstrip(".").lower()
        builder = _SUPPORTED_DATASET_EXTENSIONS.get(ext)
        if builder is None:
            reason = "no file extension found" if not ext else f"unsupported extension '.{ext}'"
            raise ValueError(
                f"Cannot determine file format for '{path}': {reason}. "
                "Rename the file with a supported extension: .json, .jsonl, .csv, or .parquet."
            )
        return load_dataset(builder, data_files=path)

    if _looks_like_hub_dataset_id(path):
        revision = _resolve_hub_dataset_revision(path, offline=offline)
        if revision is not None:
            # A failure here is NOT swallowed into an unpinned retry: the SHA
            # came from the Hub moments ago, so an error means a real problem
            # (gated repo, transport failure) and a silent unpinned re-load
            # would be exactly the "loaded something else, recorded this"
            # defect this pinning exists to remove.
            dataset = load_dataset(path, revision=revision)
            _RESOLVED_DATASET_REVISIONS[path] = revision
            logger.info("Loaded dataset '%s' pinned to Hub revision %s.", path, revision)
            return dataset

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
    # Only ``""`` means "no system prompt" (synthesised by
    # ``_process_user_assistant_format`` as ``[""] * len``). Every other value —
    # including falsy non-strings (``0``/``False``/``[]``) and ``None`` — is a
    # schema bug that must fail loudly through ``clean_string``, symmetric with
    # the user/assistant cells. A truthiness gate would let those slip past.
    has_system = sys_text != ""
    sys_clean = clean_string(sys_text, clean_text) if has_system else ""
    user_clean = clean_string(user_text, clean_text)
    asst_clean = clean_string(asst_text, clean_text)
    sys_part = f"[SYSTEM]\n{sys_clean}\n" if sys_clean else ""
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

    # Pre-check lengths so a mismatch raises the module's own actionable
    # message. Without this, zip(..., strict=True) raises from inside the
    # `for` statement's implicit next() call — outside the try/except below
    # — surfacing Python's generic "zip() argument N is longer/shorter"
    # message instead of the row-indexed framing used for every other
    # malformed shape in this function.
    if not (len(sys_texts) == len(user_texts) == len(asst_texts)):
        raise ValueError(
            "Malformed User/Assistant-format batch: 'System'/'User'/'Assistant' columns have mismatched "
            f"lengths (System={len(sys_texts)}, User={len(user_texts)}, Assistant={len(asst_texts)})."
        )

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


def _merge_extra_datasets(primary_dataset, extra_paths: list, mix_ratio: Optional[list], *, offline: bool = False):
    """Concatenate primary + extra dataset training splits, optionally weighted.

    ``offline`` is threaded to each extra load for the same reason as the
    primary (see :func:`_load_single_dataset`): an extra corpus is no less
    capable of dialling the Hub than the primary one, and a partial
    air-gap is not an air-gap.
    """
    from datasets import DatasetDict, concatenate_datasets

    # Validated *before* anything is loaded.  The count is known from the
    # arguments alone (1 primary + N extras), and rejecting the mixture after
    # downloading the datasets it rejects is pure waste.
    if mix_ratio and len(mix_ratio) != len(extra_paths) + 1:
        # DataConfig._validate_mix_ratio_length guarantees this at config
        # time; reaching here means a non-config caller passed a mismatch.
        # Raise loudly rather than silently re-weighting to a mixture the
        # caller never asked for.
        raise ValueError(f"mix_ratio length ({len(mix_ratio)}) does not match dataset count ({len(extra_paths) + 1}).")

    all_train = [primary_dataset["train"]]
    for i, extra_path in enumerate(extra_paths):
        logger.info("Loading extra dataset [%d]: %s", i + 1, extra_path)
        extra_ds = _load_single_dataset(extra_path, offline=offline)
        all_train.append(extra_ds["train"])

    if mix_ratio:
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
        # Pop (not alias) so the returned DatasetDict carries exactly one
        # copy of these rows. Aliasing left both "test" and "validation"
        # keys pointing at the same underlying Dataset, and every
        # downstream per-split loop (_shuffle_and_passthrough,
        # _format_sft_dataset) iterates every key in the DatasetDict —
        # doubling shuffle/format/tokenize cost for no functional benefit,
        # since trainer.py only ever reads "train" and "validation".
        dataset["validation"] = dataset.pop("test")
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
    # The operator's air-gap intent travels as an argument from here down, so
    # it does not depend on an env var some earlier caller may or may not have
    # exported (see ``_hf_offline_mode``).
    offline = config_offline(config)
    primary_dataset = _load_single_dataset(config.data.dataset_name_or_path, offline=offline)
    dataset = primary_dataset

    extra_datasets = getattr(config.data, "extra_datasets", None)
    if extra_datasets:
        dataset = _merge_extra_datasets(
            primary_dataset,
            extra_datasets,
            getattr(config.data, "mix_ratio", None),
            offline=offline,
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
