---
title: Document Ingestion
description: Convert PDF, DOCX, EPUB, TXT, and Markdown into SFT-ready JSONL with one command.
---

# Document Ingestion

Most fine-tuning datasets don't start as JSONL — they start as PDFs, contracts, EPUBs, or messy Markdown notes. `forgelm ingest` walks your input directory, extracts text in a format-aware way, and emits SFT-ready JSONL.

```mermaid
flowchart LR
    A[Input dir<br/>PDF/DOCX/EPUB/MD] --> B[Format detect]
    B --> C[Extract text<br/>preserve structure]
    C --> D[Chunk by tokens<br/>or by Markdown]
    D --> E[Apply masking<br/>PII + secrets]
    E --> F[Output JSONL]
    classDef io fill:#1c2030,stroke:#0ea5e9,color:#e6e7ec
    classDef step fill:#161a24,stroke:#f97316,color:#e6e7ec
    class A,F io
    class B,C,D,E step
```

## Quick example

```shell
$ forgelm ingest ./policies/ \
    --recursive \
    --strategy markdown \
    --chunk-tokens 1024 \
    --all-mask \
    --output data/policies.jsonl
✓ scanned 47 files (12 PDF, 8 DOCX, 27 MD)
✓ extracted 12,240 chunks (avg 743 tokens)
✓ masked 18 PII matches, 0 secret matches
✓ wrote data/policies.jsonl (8.2 MB)
```

`--all-mask` is the documented shorthand for `--secrets-mask --pii-mask`
in the right order. See [Combined Masking](#/data/all-mask) for the
full behaviour and set-union semantics.

## Supported formats

| Format | Extractor | Notes |
|---|---|---|
| **PDF** | `pypdf` | Header/footer dedup, table extraction (best-effort). |
| **DOCX** | `python-docx` | Tables emit as Markdown tables; preserves headings. |
| **EPUB** | `ebooklib` | Strips navigation/index; preserves chapter structure. |
| **TXT** | built-in | Treated as one document; chunked by `--chunk-tokens`. |
| **Markdown** | built-in | Markdown-aware splitter respects heading hierarchy. |

Install the ingestion extras: `pip install 'forgelm[ingestion]'`. See [Installation](#/getting-started/installation).

## Chunking strategies

The shipped `--strategy` choices are `sliding`, `paragraph`, and
`markdown`. (`tokens` and `sentence` were earlier-design names that
never landed in the parser.)

| Strategy | Behaviour | Best for |
|---|---|---|
| `sliding` | Sliding window with `--chunk-tokens` cap + `--overlap-tokens` overlap; falls back to character mode when no tokenizer is available. | Plain text, mixed content. |
| `markdown` | Heading-aware splitter that respects `#`/`##`/`###` boundaries and keeps fenced code blocks atomic. | Documentation, structured corpora. |
| `paragraph` | One chunk per paragraph; non-overlapping by design (pass `--overlap 0` or omit it). | Books, prose. |

`semantic` is reserved for a follow-up phase — it raises
`NotImplementedError` today and is hidden from the CLI surface to
avoid runtime crashes.

## Output formats

`forgelm ingest` emits raw chunks (`{"text": "..."}` JSONL). There is no `--format` flag in v0.7.0 — the only choice is `--output-format {text,json}` for the **summary report** (chunk count, format breakdown, dropped-row reasons), not for the chunk records themselves, which are always raw `text` JSONL. Operators that want synthetic-prompt or Q&A datasets layer that as a downstream step (see [Synthetic Data](#/data/synthetic-data)) against the raw JSONL this command produces:

```json
{"text": "Section 4.2: All payment processing must comply with PCI-DSS standards…", "metadata": {"source": "policy.pdf", "chunk": 17}}
```

## CLI flags

Run `forgelm ingest --help` for the authoritative list. The flags
that appear most often:

| Flag | Description |
|---|---|
| `--output FILE` | Destination JSONL file (parent dirs are created). Required. |
| `--recursive` | Walk subdirectories. Default is shallow (top-level files only). |
| `--strategy {sliding,paragraph,markdown}` | Chunking strategy (default: `paragraph`). |
| `--chunk-tokens N` | Token cap per chunk (uses `--tokenizer`). Pair with `--overlap-tokens` for `sliding`. |
| `--chunk-size N` | Soft per-chunk character cap (library default 2048). Use **either** this OR `--chunk-tokens`, not both. |
| `--overlap N` | Sliding-strategy character-mode overlap (default 200 when `--strategy sliding`; must be 0 / unset for `paragraph` or `markdown` — they're non-overlapping by design). |
| `--overlap-tokens N` | Token-mode overlap for `--strategy sliding` paired with `--chunk-tokens`. |
| `--tokenizer MODEL_NAME` | HF tokenizer used by `--chunk-tokens` / `--overlap-tokens`. |
| `--pii-mask` | Mask emails, phone, IDs, IBAN before writing. See [PII Masking](#/data/pii-masking). |
| `--secrets-mask` | Redact AWS keys, GitHub PATs, JWTs, etc. See [Secrets Scrubbing](#/data/secrets). |
| `--all-mask` | Shorthand for `--secrets-mask --pii-mask` together. |

### Phase 15 (v0.6.0) additions

| Flag | Description |
|---|---|
| `--language-hint LANG` | Enable per-file Unicode-block sanity check (`tr` / `en` / `de` / `fr` / `es` / `it` / `pt`). Fires a WARNING when the out-of-script char ratio exceeds the calibrated 1.5 % threshold. No-op when unset. |
| `--script-sanity-threshold X` | Override the 1.5 % default sanity-threshold (range `[0.0, 1.0]`). |
| `--normalise-profile {turkish,none}` | Apply a language-specific glyph normalisation table to extracted text. Auto-derived to `turkish` when `--language-hint tr` is set; `none` otherwise. Explicit value wins. |
| `--no-normalise-unicode` | Shortcut for `--normalise-profile none`. |
| `--no-quality-presignal` | Skip the end-of-run quality pre-signal (alpha / weird-char / repeated-line cheap checks). Default ON. |
| `--epub-no-skip-frontmatter` | Keep EPUB nav / cover / copyright / colophon / titlepage / frontmatter items in the JSONL. Default skips them. |
| `--keep-md-frontmatter` | Retain `---\n…\n---\n` YAML frontmatter at the start of Markdown files. Default strips. |
| `--strip-pattern REGEX` | Operator-controlled regex stripping (repeatable). Patterns are ReDoS-validated up-front (nested unbounded quantifiers + `.*?` + DOTALL back-ref shapes rejected) and run under a 5-second per-pattern SIGALRM budget on POSIX. |
| `--strip-pattern-no-timeout` | Disable the per-pattern SIGALRM timeout. |
| `--page-range START-END` | Restrict PDF extraction to a contiguous 1-indexed page range. Validation failures abort with `EXIT_CONFIG_ERROR (1)`. |
| `--keep-frontmatter` | Opt out of the default-ON PDF front-matter / back-matter heuristic (alpha < 0.45 + underscore > 0.10 + ≥ 5 page-number matches → drop up to 12 leading + trailing pages). |
| `--strip-urls {keep,mask,strip}` | URL handling for inline URLs: `keep` (default), `mask` (`[URL]` placeholder), `strip` (delete). Independent of `--all-mask`. |

## Common pitfalls

:::warn
**Forgetting `--pii-mask`.** The default is *no* masking, on the principle of "don't silently modify your data". For real corpora, enable it explicitly. The audit step ([Dataset Audit](#/data/audit)) will flag PII regardless, but better to mask at ingest.
:::

:::warn
**Pure-image PDFs.** ForgeLM doesn't ship OCR. If your PDFs are scanned images, run them through Tesseract or a commercial OCR first.
:::

:::warn
**`--chunk-tokens` larger than the model's context.** Chunks longer than `model.max_length` will be truncated at training time, losing the tail. Match `--chunk-tokens` to the training context.
:::

:::tip
**Always audit after ingest.** A clean ingest doesn't mean a clean dataset. Run `forgelm audit data/output.jsonl` to check for cross-split leakage, near-duplicates, and PII the masking missed.
:::

## See also

- [Dataset Audit](#/data/audit) — the next step after ingest.
- [PII Masking](#/data/pii-masking) — how the masking works.
- [Dataset Formats](#/concepts/data-formats) — what each output format looks like.
