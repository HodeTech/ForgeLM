"""Phase 12.5: Google Croissant 1.0 dataset-card emission.

Croissant is a JSON-LD vocabulary built on top of schema.org;
mlcommons.org/croissant describes the canonical context. We emit a
minimum-viable subset that's valid against the spec — tools that consume
Croissant (HuggingFace's dataset-cards, Croissant validator, MLCommons
reference loaders) can parse the block without modification, while the
rest of the audit JSON stays untouched. The card lives under the new
``croissant`` key on the :class:`AuditReport` dataclass; callers that
don't want it never see it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List


def _slug(value: str, fallback: str = "unknown") -> str:
    """Return a JSON-LD-safe identifier derived from *value*.

    Replaces path separators, dots (except extension dots confuse consumers),
    whitespace, and any non-word character with ``_``, collapses consecutive
    underscores, strips leading/trailing underscores, and falls back to
    *fallback* when the result would be empty.  The ASCII flag ensures Unicode
    punctuation is always replaced rather than silently passed through.
    """
    slugged = re.sub(r"[^\w]", "_", value, flags=re.ASCII)
    slugged = re.sub(r"_+", "_", slugged).strip("_")
    return slugged or fallback


# JSON-LD reserved keywords used as dict keys in the Croissant card body.
# The W3C JSON-LD 1.1 spec fixes these tokens — they're vocabulary
# constants, not arbitrary strings, but Sonar's S1192 (string-literals-
# duplicated) flags the repeated literal use across the metadata
# builder. Hoisting them here keeps the rule satisfied without
# obscuring that we're emitting standardized JSON-LD framing.
_JSONLD_TYPE_KEY: str = "@type"
_JSONLD_ID_KEY: str = "@id"

_CROISSANT_CONTEXT: Dict[str, Any] = {
    "@language": "en",
    "@vocab": "https://schema.org/",
    "sc": "https://schema.org/",
    # ``cr:`` is the canonical Croissant 1.0 JSON-LD namespace IRI as
    # defined by the mlcommons.org/croissant spec — an RDF identifier,
    # not a network endpoint. Strict consumers (mlcroissant validator,
    # MLCommons reference loaders) compare this string lexically; using
    # ``https://`` here would diverge from the spec-canonical form and
    # break exact-match consumers. The S5332 hotspot is a false positive
    # for this dual-purpose URI.
    "cr": "http://mlcommons.org/croissant/",  # NOSONAR python:S5332 — JSON-LD namespace IRI, not a fetch URL
    "data": {_JSONLD_ID_KEY: "cr:data", _JSONLD_TYPE_KEY: "@json"},
    "dataType": {_JSONLD_ID_KEY: "cr:dataType", _JSONLD_TYPE_KEY: "@vocab"},
    "extract": "cr:extract",
    "field": "cr:field",
    "fileObject": "cr:fileObject",
    "fileProperty": "cr:fileProperty",
    "format": "cr:format",
    "includes": "cr:includes",
    "isLiveDataset": "cr:isLiveDataset",
    "jsonPath": "cr:jsonPath",
    "key": "cr:key",
    "parentField": "cr:parentField",
    "path": "cr:path",
    "recordSet": "cr:recordSet",
    "references": "cr:references",
    "regex": "cr:regex",
    "repeated": "cr:repeated",
    "replace": "cr:replace",
    "separator": "cr:separator",
    "source": "cr:source",
    "subField": "cr:subField",
    "transform": "cr:transform",
}


def _build_croissant_metadata(
    *,
    source_path: str,
    source_input: str,
    generated_at: str,
    total_samples: int,
    splits_info: Dict[str, Dict[str, Any]],
    splits_paths: Dict[str, Path],
) -> Dict[str, Any]:
    """Render a minimum-viable Croissant 1.0 dataset card for the audited corpus.

    The card exposes:
    * dataset-level identity (name, description, version, datePublished),
    * one ``cr:FileObject`` per JSONL split (so a Croissant consumer can
      locate the underlying files),
    * one ``cr:RecordSet`` per split with the columns the audit detected
      under ``splits.<name>.columns`` mapped to ``cr:Field`` entries.

    The mapping intentionally stays additive — Croissant supports many
    optional fields (citeAs, license, keywords, sameAs, etc.) that the
    audit does not have first-class evidence for. Operators that want to
    publish the card to HuggingFace / MLCommons can hand-edit those
    fields without re-running the audit.
    """
    # Derive a human-readable name from the source path. ``Path.stem`` for
    # a JSONL ("policies.jsonl" -> "policies"); for a directory we use the
    # directory name. Fall back to ``source_input`` so HF Hub IDs survive
    # in the card even though the audit is filesystem-only today.
    src = Path(source_path)
    if src.is_file():
        name = src.stem
    elif src.is_dir():
        name = src.name
    else:
        name = source_input or "dataset"

    distribution: List[Dict[str, Any]] = []
    record_sets: List[Dict[str, Any]] = []
    for split_name, info in splits_info.items():
        sample_count = int(info.get("sample_count", 0))
        # Derive ``file_id`` from the real source filename (basename
        # only, never the absolute path) so single-file audits like
        # ``policies.jsonl`` don't show up as ``train.jsonl`` in the
        # generated card and alias layouts (``dev.jsonl`` -> split
        # ``validation``) keep their on-disk filename. Falls back to
        # the canonical ``{split}.jsonl`` shape when no path is
        # registered — defensive for callers that bypass
        # ``_resolve_input``.
        split_path = splits_paths.get(split_name)
        raw_file_id = split_path.name if split_path else f"{split_name}.jsonl"
        file_id = _slug(raw_file_id, fallback=f"{_slug(split_name)}.jsonl")
        split_id = _slug(split_name, fallback="split")
        # ``contentUrl`` deliberately uses the *raw* filename (relative,
        # not the absolute filesystem path) so cards published to HuggingFace
        # / MLCommons do not leak the auditor's local layout. The ``@id``
        # uses the slugged form for JSON-LD validity; operators hand-edit
        # ``contentUrl`` at publish time (HF Hub URL, S3 path, etc.) as they
        # do ``license`` / ``citeAs``.
        distribution.append(
            {
                _JSONLD_TYPE_KEY: "cr:FileObject",
                _JSONLD_ID_KEY: file_id,
                "name": raw_file_id,
                "contentUrl": raw_file_id,
                "encodingFormat": "application/jsonlines",
                "description": f"Split {split_name!r}: {sample_count} sample(s).",
            }
        )

        # Map the audit's detected columns to ``cr:Field`` entries.
        # ``columns`` is a list of strings (the keys present in the JSONL
        # rows). When the column is one of the canonical text-payload
        # columns we type it as ``sc:Text``; everything else is ``sc:Text``
        # too (the audit doesn't track per-column dtypes — that's a
        # consumer-side concern).
        # ``@id`` uses the slugged column name for JSON-LD validity; ``name``
        # preserves the original so consumers can correlate back to the data.
        # _slug is many-to-one (e.g. "a.b" and "a-b" both collapse to "a_b");
        # without dedup two distinct columns would emit identical ``@id``s and
        # the resulting JSON-LD would fail schema validation.  Track per-base
        # counts and append a numeric suffix on the second+ occurrence so each
        # field keeps a unique identifier while the first one stays clean.
        columns = info.get("columns") or []
        slug_counts: Dict[str, int] = {}
        unique_slugs: List[str] = []
        for column in columns:
            base = _slug(column, fallback="field")
            seen = slug_counts.get(base, 0)
            unique_slugs.append(base if seen == 0 else f"{base}-{seen}")
            slug_counts[base] = seen + 1
        fields = [
            {
                _JSONLD_TYPE_KEY: "cr:Field",
                _JSONLD_ID_KEY: f"{split_id}/{unique_slug}",
                "name": column,
                "dataType": "sc:Text",
                "source": {
                    "fileObject": {_JSONLD_ID_KEY: file_id},
                    "extract": {"jsonPath": "$['" + column.replace("\\", "\\\\").replace("'", "\\'") + "']"},
                },
            }
            for column, unique_slug in zip(columns, unique_slugs)
        ]
        record_sets.append(
            {
                _JSONLD_TYPE_KEY: "cr:RecordSet",
                _JSONLD_ID_KEY: split_id,
                "name": split_name,
                "field": fields,
                "description": f"Records from split {split_name!r}.",
            }
        )

    # ``url`` carries the basename of ``source_input`` so we never
    # publish an auditor's absolute filesystem path
    # (``/Users/...`` / ``/home/builder/...``) into a card that may be
    # shipped to HuggingFace / MLCommons. ``Path.name`` gives the
    # relative form for both files (``policies.jsonl``) and directories
    # (``data/`` -> ``data``); when ``source_input`` is empty we fall
    # back to the dataset name. Operators that want a real public URL
    # (HF Hub, S3) hand-edit at publish time, the same way they do
    # ``license`` / ``citeAs``.
    # ``version`` (``sc:version``) describes the *dataset* version,
    # not the Croissant vocabulary version (vocab conformance is
    # declared via ``conformsTo``). The audit doesn't have first-class
    # evidence for dataset version, so the field is omitted; operators
    # that publish the card hand-edit ``version`` like they do
    # ``license`` / ``citeAs``.
    url_safe = _slug(Path(source_input).name if source_input else name, fallback=_slug(name, fallback="dataset"))
    return {
        "@context": dict(_CROISSANT_CONTEXT),
        _JSONLD_TYPE_KEY: "sc:Dataset",
        # ``conformsTo`` declares the Croissant vocabulary version the
        # card adheres to. Like ``cr:`` above, this is a JSON-LD URI
        # identifier (RDF reference), not a network endpoint — strict
        # consumers exact-match the canonical spec form. Same S5332
        # false-positive rationale as ``cr:`` in ``_CROISSANT_CONTEXT``.
        "conformsTo": "http://mlcommons.org/croissant/1.0",  # NOSONAR python:S5332 — JSON-LD identifier
        "name": name,
        "description": (
            "ForgeLM audit-generated dataset card. "
            f"{total_samples} sample(s) across {len(splits_info)} split(s). "
            "Inline counts/quality/PII summaries live in the parent "
            "data_audit_report.json under the canonical audit keys."
        ),
        "url": url_safe,
        "datePublished": generated_at,
        "distribution": distribution,
        "recordSet": record_sets,
    }


__all__ = [
    "_JSONLD_TYPE_KEY",
    "_JSONLD_ID_KEY",
    "_CROISSANT_CONTEXT",
    "_build_croissant_metadata",
]
