# Document Ingestion Guide

Convert raw enterprise corpora (PDF / DOCX / EPUB / TXT / Markdown) into the
SFT-ready JSONL ForgeLM trains on. Ships with `v0.5.0` (Phases 11 + 11.5
+ 12 + 12.5 consolidated): paragraph / sliding / markdown chunking,
token-aware sizing, PDF page header/footer dedup, code/credential
scrubbing (`--secrets-mask`), DOCX tables rendered as markdown, and the
`--all-mask` shorthand for combined PII + secrets masking. Earlier
phase boundaries kept inline as breadcrumbs for reviewers; the
behaviours all ship in the same release.

> **Phase trail (kept for reviewers):** Phase 11 introduced the
> ingestion pipeline; Phase 11.5 added token-aware chunking + PDF page
> header/footer dedup + structured ingestion notes; Phase 12 added
> markdown-aware splitting + secrets scrubbing + DOCX table preservation;
> Phase 12.5 added `--all-mask`. The four phases were consolidated into
> a single `v0.5.0` release.

> Pair with [`forgelm audit`](data_audit.md) afterwards to surface
> length-distribution / language / near-duplicate / PII metrics, and with
> [`forgelm --generate-data`](../reference/usage.md) if you want the chunks
> expanded into Q&A `messages` form via a teacher model.

---

## Install

```bash
pip install 'forgelm[ingestion]'
```

The extra brings in `pypdf`, `python-docx`, `ebooklib`, `beautifulsoup4`,
`langdetect`, and the optional non-cryptographic `xxhash`
backend used by the simhash digest. They are optional because plain text
+ Markdown work without any of them. Importing the module does not pull
these in unless the matching extractor is exercised; `xxhash`'s absence
silently falls back to `hashlib.blake2b`.

OCR is **out of scope.** Scanned PDFs without a text layer surface a warning
and produce zero chunks; pre-process them with Tesseract or AWS Textract
before ingest.

---

## Single command — file in, JSONL out

```bash
forgelm ingest ./book.epub --output data/sft.jsonl
forgelm ingest ./policies/ --recursive --output data/policies.jsonl
forgelm ingest ./scan.pdf --strategy sliding --chunk-size 1024 --overlap 128 \
  --output data/scan.jsonl
```

Output is one chunk per line:

```json
{"text": "Article 10 of the EU AI Act requires high-risk AI providers to ..."}
{"text": "Data quality criteria include relevance, representativeness ..."}
```

The trainer's data loader treats `{"text": "..."}` as a pre-formatted SFT
column (see [`forgelm/data.py`](../../forgelm/data.py)) — no further
preprocessing required.

---

## Chunking strategies

| Strategy | When | Behavior |
|---|---|---|
| `paragraph` (default) | Prose, policy docs, articles | Greedy paragraph packer; never splits a paragraph mid-sentence. |
| `sliding` | Long technical documents, code mixed with prose | Fixed-size character window with `--overlap` for context bleed. |
| `markdown` (Phase 12) | Markdown docs, technical wikis, READMEs | Heading-aware: chunks at `# H1` / `## H2` boundaries, keeps code-fenced blocks atomic, inlines a heading breadcrumb so SFT loss sees document context. |

> Semantic / embedding-based chunking is reserved for a follow-up phase — it
> raises `NotImplementedError` today and is intentionally hidden from the CLI
> `--strategy` choice list to avoid runtime crashes.

`--chunk-size` is measured in **characters, not tokens**. As a rough rule,
`--chunk-size 2048` corresponds to ≈500–700 tokens for typical English /
Turkish text — set the value with `model.max_length` in mind (e.g. a model
with `max_length: 2048` tokens benefits from `--chunk-size 6000-8000` so the
formatter has headroom for system prompt + chat template overhead).
Paragraphs longer than the soft cap are emitted on their own — better than
mid-sentence splits.

**Sliding overlap is bounded.** `--overlap` must be both `< --chunk-size`
*and* `≤ --chunk-size // 2` — values above that explode chunk count
(`--overlap 199 --chunk-size 200` would emit ~one chunk per character).
The CLI rejects pathological combinations up front.

**Files are processed in lexicographic order** (sorted glob result), so a
re-run with the same input + flags produces the same JSONL byte-for-byte.

---

## PII masking on the way in

Add `--pii-mask` to redact emails, phone numbers, credit cards (Luhn-validated),
IBAN, and national IDs (TR Kimlik No, DE Personalausweis, FR INSEE, US SSN)
before any chunk lands in the JSONL:

```bash
forgelm ingest ./customer_emails/ --output data/anon.jsonl --pii-mask
```

Detected spans are replaced with `[REDACTED]`. Detection is regex-based —
false positives are intentional. Audit your output afterwards with
`forgelm audit` to verify.

---

## Secrets masking on the way in (Phase 12)

Add `--secrets-mask` to scrub credentials and tokens before chunks land
in the JSONL — fine-tuning a model on a corpus that contains a real API
key memorises that key in the model. Detected categories: AWS access
keys (`AKIA…`), GitHub PATs (`ghp_…` and friends), Slack tokens, OpenAI
API keys (`sk-…` / `sk-proj-…`), Google API keys (`AIza…`), JWTs,
OpenSSH/RSA/DSA/EC/PGP private-key block headers, Azure storage
connection strings.

```bash
# Standalone
forgelm ingest ./engineering_wiki/ --output data/wiki.jsonl --secrets-mask

# Combined with PII masking — secrets run first, PII second so combined
# detectors can't double-count overlapping spans
forgelm ingest ./mixed_corpus/ --output data/clean.jsonl --secrets-mask --pii-mask
```

Detected spans are replaced with `[REDACTED-SECRET]`. The
`ingest_path()` masking path delegates to
`forgelm.data_audit.mask_secrets`, which scans with the regex set
described in [data_audit.md](data_audit.md) (9 prefix-anchored
patterns: AWS, GitHub, Slack, OpenAI, Google, JWT, full
OpenSSH/RSA/DSA/EC/PGP private-key blocks, Azure storage). The
`[ingestion-secrets]` extra is reserved for a follow-up release —
installing it today does **not** change ingest masking behaviour.

---

## All-in-one masking — `--all-mask` (Phase 12.5)

`--all-mask` is a one-flag shorthand for `--secrets-mask --pii-mask` —
the common "scrub everything detectable before training on a shared
corpus" workflow. The two underlying detectors run in the documented
order (secrets first so combined PII detectors don't double-count
overlapping spans); the resulting JSONL carries both `[REDACTED-SECRET]`
and `[REDACTED]` tokens where matches were found.

```bash
forgelm ingest ./mixed_corpus/ --recursive --all-mask --output data/clean.jsonl
```

Composes additively with explicit flags — `--all-mask --pii-mask` is
not an error; the boolean union of the two flags is what runs. The
shorthand exists purely as ergonomics; it does not introduce any new
detector.

---

## Recursive directory walk

```text
./policies/
├── 2024_q1.pdf
├── 2024_q2.pdf
└── archive/
    ├── 2023.docx
    └── 2022.epub
```

```bash
forgelm ingest ./policies/ --recursive --output data/all_policies.jsonl
```

Files with unsupported extensions (`.png`, `.zip`, etc.) are skipped silently.
Files with supported extensions but no extractable text (scanned PDFs,
empty DOCX) skip with a warning.

**Encrypted PDFs** are caught explicitly: an empty-password decrypt is
attempted automatically (covers owner-encrypted PDFs that are still
readable). If that fails, the per-file extractor raises `ValueError`
internally — the batch ingestion loop catches that as "extraction
failed", logs a warning that names the file, increments
`files_skipped` in the result, and moves on to the next file. The
warning text recommends decrypting externally first:

```bash
qpdf --decrypt --password=<pwd> input.pdf out.pdf
# or
pdftk input.pdf input_pw <pwd> output out.pdf
```

Wiring a CLI password flag is intentionally avoided — keeping passwords
out of shell history is safer.

**Binary content masquerading as text** (a `.txt` file that's actually a
zip / image renamed) surfaces a warning when more than 1% of the file
decodes as Unicode replacement characters. The chunks still write —
operators decide whether to keep them — but the warning is loud enough
to catch in CI logs.

---

## End-to-end example

```bash
# 1. Ingest
forgelm ingest ./policies/ --recursive --output data/policies.jsonl

# 2. Audit (catches near-duplicates, PII, length outliers)
forgelm audit data/policies.jsonl --output ./audit/

# 3. (optional) Expand to Q&A via a teacher model
forgelm --config configs/synth.yaml --generate-data

# 4. Train
forgelm quickstart domain-expert --dataset data/policies.jsonl
```

---

## CLI reference

```text
forgelm ingest INPUT_PATH \
  --output FILE \
  [--chunk-size N | --chunk-tokens N --tokenizer MODEL_NAME] \
  [--input-encoding CODEC] \
  [--overlap N] \
  [--overlap-tokens N] \
  [--strategy {sliding,paragraph,markdown}] \
  [--recursive] \
  [--pii-mask] \
  [--secrets-mask] \
  [--all-mask] \
  [--language-hint LANG] \
  [--script-sanity-threshold X] \
  [--normalise-profile {turkish,none} | --no-normalise-unicode] \
  [--no-quality-presignal] \
  [--epub-no-skip-frontmatter] \
  [--keep-md-frontmatter] \
  [--strip-pattern REGEX ...] \
  [--strip-pattern-no-timeout] \
  [--page-range START-END] \
  [--keep-frontmatter] \
  [--strip-urls {keep,mask,strip}] \
  [--output-format {text,json}] \
  [--quiet | --log-level {DEBUG,INFO,WARNING,ERROR}]
```

`--output-format json` emits a machine-readable summary to stdout. The
envelope carries Phase 11.5 keys (`notes`, `notes_structured`,
`pdf_header_footer_lines_stripped`), Phase 12 keys
(`secrets_redaction_counts`), and the **Phase 15** additive fields:
`pdf_paragraph_packed_lines_stripped` (survivor-header second-pass
count), `script_sanity_triggered` (files with out-of-script char ratio
above threshold), `strip_pattern_substitutions` (`--strip-pattern`
match count), `urls_handled` (URLs masked / stripped by
`--strip-urls`), and `frontmatter_pages_dropped` (front-matter
heuristic drop count). Useful for CI/CD pipelines.

### Token-aware chunking — `--chunk-tokens` (Phase 11.5)

Char-based chunking is convenient but can blow past your model's
`max_length` token budget on dense text. Pass `--chunk-tokens N` along
with `--tokenizer MODEL_NAME` to size chunks against the same tokenizer
your trainer will use:

```bash
forgelm ingest ./policies/ --recursive --output data/policies.jsonl \
  --chunk-tokens 1024 --tokenizer "Qwen/Qwen2.5-7B-Instruct"
```

`--chunk-size` is ignored when `--chunk-tokens` is set (a warning logs
the override). `--overlap-tokens N` is the sliding-window equivalent of
`--overlap` measured in tokens, with the same half-window cap.
`--tokenizer` is **required** with `--chunk-tokens` — we refuse to
pick a default vocab because the resulting chunk count would silently
differ per-model.

### PDF page-level header/footer dedup (Phase 11.5 + Phase 15 Task 1)

`forgelm ingest` strips lines that recur as the first or last few
non-empty lines on ≥ 70 % of a PDF's pages — typical company
watermarks, copyright lines, page numbers — before chunking.

**Phase 15 Task 1** widens the inspection window from the single
outermost row to the **top-3 / bottom-3 rows** per page so a corpus
with a variable outer line (per-chapter section title) plus a constant
deeper line (publication identifier) gets fully dedup'd. The pre-Phase-15
implementation exited the dedup loop on pass 1 when the outermost line
didn't recur, leaving the deeper constant line stranded in 74 / 82
chunks on the audit's pilot corpus.

A second pass runs after paragraph packing to mop up survivor headers
the chunker re-glued mid-block — surfaced as the structured-notes
field `pdf_paragraph_packed_lines_stripped`.

Both passes are automatic; no flag, no opt-out beyond "send a non-PDF".
Skipped on documents shorter than 3 pages. The structured-notes payload
reports `pdf_header_footer_lines_stripped` so operators can see
post-hoc that dedup actually did work.

### Script-sanity check + glyph normalisation (Phase 15 Tasks 2 + 3)

Pypdf occasionally produces font-fallback artefacts on PDFs whose
fonts declare custom glyph names (the audit measured `ø Õ ú ÷ ࡟` for
Turkish characters on a real-world pilot). Two mitigations ship in
v0.6.0:

- **`--language-hint LANG`** runs a Unicode-block sanity check after
  each per-file extract. When the out-of-script char ratio exceeds the
  calibrated 1.5 % threshold (tunable via `--script-sanity-threshold`),
  a WARNING fires + a structured `script_sanity_summary` block lands
  in `notes_structured`. Supported languages: `tr`, `en`, `de`, `fr`,
  `es`, `it`, `pt` (CJK / Arabic deferred to Phase 16+).
- **`--normalise-profile {turkish,none}`** applies a language-specific
  glyph normalisation table to extracted text. The module-level
  default is **`none`** (Phase 15 round-2 / C-2: a hint-less corpus
  must not be silently rewritten). When the operator passes
  `--language-hint tr` the dispatcher (CLI and library API alike)
  auto-derives the `turkish` profile; every other hint (and the
  unset case) stays on `none`. An explicit `--normalise-profile turkish`
  always wins. `--no-normalise-unicode` or `--normalise-profile none`
  disables normalisation entirely. Verify the table loaded via
  `forgelm doctor` — it surfaces a `pypdf_normalise.turkish: pass`
  row when the profile is healthy.

### Ingest-time quality pre-signal (Phase 15 Task 4)

At the end of every run `forgelm ingest` applies three cheap row-level
checks (alpha-ratio, weird-char ratio, repeated-line ratio) to every
emitted chunk and surfaces a single nudge line when any chunk falls
below threshold:

```text
[WARN] 74/82 chunks below ingestion quality threshold. Run
       `forgelm audit ./out.jsonl` for detail.
```

Full diagnostics still live in `forgelm audit --quality-filter`
(default-on from v0.6.0); the pre-signal is just the smallest possible
"hey, you might want to look at this" prompt. Opt out with
`--no-quality-presignal`. The structured payload lands under
`notes_structured.quality_presignal` with `samples_evaluated`,
`samples_flagged`, and per-check counts.

### DOCX header / footer subtraction (Phase 15 Task 6)

Word documents declare repeating headers and footers explicitly under
each section's `<w:hdr>` / `<w:ftr>` parts. v0.6.0 reads those parts
up-front and subtracts their lines from the body extraction, so a
3-line repeating header across 10 pages emits **zero** header lines
into the JSONL.

### EPUB spine order + nav / cover / copyright skip (Phase 15 Task 7)

EPUB extraction iterates `book.spine` (reading order) instead of
`book.get_items()` (file order), so chapters land in the correct
sequence. The default skip-list filters items whose file name or
`epub:type` matches `nav`, `cover`, `copyright`, `colophon`,
`titlepage`, or `frontmatter` — useful for SFT training where TOC
boilerplate is pure noise. Opt out with `--epub-no-skip-frontmatter`.

### TXT BOM + Markdown YAML frontmatter (Phase 15 Task 8)

TXT files are read via `encoding="utf-8-sig"` so a UTF-8 BOM at file
start is stripped transparently before chunking. Markdown files
additionally detect `^---\n…\n---\n` YAML frontmatter and strip it
by default — opt-in retention via `--keep-md-frontmatter` when you
*want* to train on the metadata block.

### Source-codec override — `--input-encoding CODEC`

The auto-detected `utf-8-sig` default above covers modern corpora, but
legacy exports from older Windows tooling are frequently not UTF-8 at
all. Pass `--input-encoding CODEC` to pin the real source codec
instead of losing every non-ASCII character to the `errors="replace"`
fallback:

```bash
forgelm ingest ./legacy_docs/ --output data/legacy.jsonl \
  --input-encoding cp1254
```

Applies to `.txt` / `.md` input only — PDF, DOCX, and EPUB extractors
read their own embedded encoding metadata and ignore this flag. The
codec *name* is validated up front, before any file is read: an
unrecognised name (anything Python's `codecs.lookup()` rejects) aborts
the whole run with `EXIT_CONFIG_ERROR` (`1`) and a message listing
example valid codecs (`utf-8`, `cp1254`, `cp1252`, `latin-1`), instead
of silently reaching the per-file decode step and dropping every
TXT/MD file from the output. A syntactically valid but factually wrong
codec (e.g. `cp1252` on a `cp1254` file) is not rejected — it decodes
with `errors="replace"` and surfaces as the usual binary-contamination
warning, not a config error.

### Operator strip-patterns — `--strip-pattern REGEX` (Phase 15 Wave 2 Task 11)

Escape hatch for known boilerplate the dedup heuristic misses (variable
running headers, DOI lines, watermarks). Pass `--strip-pattern REGEX`
once per pattern; matches are deleted from the extracted text before
chunking:

```bash
forgelm ingest ./corpus/ --output data/clean.jsonl \
  --strip-pattern '^Confidential — internal use only$' \
  --strip-pattern '^https://example\.com/qr\?KOD=\d+$'
```

Each pattern is **structurally validated up-front**: nested unbounded
quantifiers (`(a+)+b`) and `.*?` + back-reference under DOTALL (the
SonarCloud `python:S5852` polynomial-runtime shape) are rejected with
`EXIT_CONFIG_ERROR`. A 5-second per-pattern SIGALRM budget bounds the
worst-case match cost on POSIX (opt out with
`--strip-pattern-no-timeout` when you've independently verified your
patterns are linear).

### `--page-range START-END` (Phase 15 Wave 2 Task 12)

Restrict PDF extraction to a contiguous page slice (1-indexed,
inclusive). Useful when the heuristic misses front-matter or you only
want a specific chapter:

```bash
forgelm ingest ./book.pdf --output data/ch3.jsonl --page-range 50-90
```

Validation failures (`start < 1`, `start > end`, `start > page_count`)
abort the run with `EXIT_CONFIG_ERROR` (1) so CI/CD pipelines branch
the same way for any operator-supplied parameter mistake.

### PDF front-matter / back-matter heuristic (Phase 15 Wave 2 Task 13, default ON)

v0.6.0 enables a three-signal heuristic on the first 12 / last 12 PDF
pages: when a page's alpha ratio < 0.30 (tightened from 0.45 in
round-4) AND leader ratio > 0.10 (combined `_` + `.` runs of length
≥ 3) AND it carries ≥ 5 inline `\n<1-3 digits>\n` page-number matches,
it is dropped with a WARNING listing the page indices. Catches ToC /
masthead / index / glossary boilerplate.

Opt out with `--keep-frontmatter` to restore the pre-Phase-15 "keep
everything" behaviour. The structured-notes payload reports
`frontmatter_pages_dropped` so an audit downstream can spot-check the
operation. On a multi-file batch, the top-level `frontmatter_pages_dropped`
integer is a true sum across every file, while the nested
`notes_structured.frontmatter_pages_dropped` list is a distinct-index
sample (which page positions were dropped, not how many times) — it can
legitimately be shorter than the top-level total when multiple files drop
the same indices, and that is not a bug.

> **Calibration caveat (round-5 independent review).** The heuristic
> is calibrated for the audit's pilot Turkish-textbook ToC shape:
> single-word chapter titles + heavy dotted leaders + inline page
> numbers (alpha ≈ 0.15 on the pilot pages). Realistic
> English-language ToCs with full-sentence chapter titles
> (`Chapter 1: Introduction to the Subject ……… 14`) measure
> alpha ≈ 0.47, well above the 0.30 threshold, and **will pass
> through** unchanged. Operators on such corpora should either
> (a) accept that front-matter survives the heuristic and prune
> downstream via `--strip-pattern`, or (b) pass `--page-range
> 12-N` to skip the front-matter manually. The 3-signal AND filter
> (alpha + leader + inline-page-number) also protects realistic form
> templates / exercise pages, which lack the inline-page-number
> signal and so never trigger the drop.

### `--strip-urls {keep,mask,strip}` (Phase 15 Wave 2 Task 14)

URL handling for corpora that embed QR-code references, DOI footers,
or social-media links the model would memorise:

- `keep` (default) — pass URLs through unchanged.
- `mask` — replace each URL with the literal `[URL]` placeholder.
- `strip` — delete URLs outright.

URL handling is intentionally **independent of `--all-mask`** (the
Phase 12.5 PII + secrets shorthand). URL stripping is a content-shape
decision, not a GDPR redaction, and the two flag families stay
orthogonal.

### Multi-column PDF warning (Phase 15 Wave 2 Task 15)

Two-column academic papers, government regulatory publications, and
multi-column legal layouts confuse pypdf's text-extraction reading
order. v0.6.0 samples the first three pages' text positions via
pypdf's `visitor_text` callback and fires a WARNING when a > 30 %-of-
page-width two-cluster gap is detected:

```text
[WARN] Detected 2-column layout in 'paper.pdf' — reading order may be
       scrambled. Consider --strategy sliding with a larger --chunk-size,
       or pre-process the PDF with a layout-aware tool.
```

No automatic fix — this is a better-than-nothing signal that the
operator should change strategies. Camelot-py / pdfplumber integration
is on the Wave 3 backlog.

> **Known limitation (Phase 15 round-3 review).** The current detector
> compares the min / max x-positions across all extracted Tj glyphs on a
> page, not per-line start positions. Because pypdf's `visitor_text`
> callback fires per glyph, a single-column page's glyphs naturally span
> a wide x-range (≈ 64 % of page width on a typical body line), which
> means the algorithm has to use a relatively high 30 %-of-page-width
> gap threshold to avoid false-positiving on single-column corpora.
> Side-effect: a real-world two-column page with a typical 5–10 mm
> gutter (≈ 5–8 %-of-page-width) **does not** trip the warning today.
> The detector is reliable on extreme layouts (academic posters,
> wide-gutter regulatory publications) but misses publication-grade
> two-column papers. A histogram-based bimodal-mode refactor is tracked
> as a Wave 3 follow-up (see
> [Phase 15 archive](../roadmap/completed-phases.md#phase-15--ingestion-pipeline-reliability-v060)
> Wave 3 — multi-column layout extraction).

### Markdown-aware splitter — `--strategy markdown` (Phase 12)

When the input has real markdown structure (technical wikis, README
collections, knowledge-base exports), heading-aware chunking beats
paragraph-greedy chunking: each chunk is a coherent section starting
with a heading, and the parent heading path is **inlined as a
breadcrumb** at the top of each chunk so SFT loss sees the document
context.

```bash
forgelm ingest ./engineering_wiki/ --recursive --output data/wiki.jsonl \
  --strategy markdown --chunk-size 4000
```

Behaviour notes:

- Boundaries are markdown headings (`# H1` … `###### H6`); the chunker
  never breaks mid-section.
- Code-fenced blocks (` ``` `) are kept atomic — never split mid-block.
  This means a single section containing a long code listing may exceed
  the soft cap (mirrors the paragraph strategy's "long-paragraph on
  its own" rule).
- Heading-shaped lines **inside** a fenced block (`# whoami`,
  `# noqa: E402`) are not interpreted as section boundaries.
- Composes with token-aware mode: `--strategy markdown --chunk-tokens
  1024 --tokenizer "Qwen/Qwen2.5-7B-Instruct"`.

### DOCX table preservation (Phase 12)

DOCX tables now extract as **markdown table syntax** (header row +
`---` separator + body rows) instead of the previous `" | "`-joined
flat line. Combined with `--strategy markdown` this keeps tables
intact across chunks. Uneven rows are right-padded with empty cells;
all-blank rows are dropped; the first non-empty row becomes the
header. SFT use cases where this matters: tabular Q&A, financial
assistants, code-with-data prompts.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ImportError: PDF ingestion requires the 'ingestion' extra` | Extra not installed | `pip install 'forgelm[ingestion]'` |
| "No extractable text in '<x>.pdf'" warning + 0 chunks | Scanned PDF, no text layer | OCR first (Tesseract / AWS Textract) |
| `FileNotFoundError: No supported files found at '<dir>'` | Directory has only unsupported extensions | Verify file extensions match `.pdf / .docx / .epub / .txt / .md` |
| `ValueError: overlap must be in [0, chunk_size)` | `--overlap >= --chunk-size` | Reduce `--overlap` |

---

## Limitations

- **OCR:** out of scope. Use external tooling — see the worked example below.
- **Tables / figures:** Since Phase 12 (consolidated into `v0.5.0`), `_extract_docx()`
  converts DOCX tables to **Markdown table syntax** (header + `---`
  separator + body rows) at extraction time, **before any chunking
  strategy runs** — so all strategies see the rendered Markdown, not a
  row-major flat string. The chunking strategy then chooses where chunk
  boundaries fall: `_markdown_sections()` splits **only on heading
  lines** (it is heading-aware, not table-aware). Under
  `--strategy markdown`, `_chunk_markdown()` (and its token-aware twin
  `_chunk_markdown_tokens()`) preserves each section as an indivisible
  unit — a section whose surrounding heading scope exceeds the chunk
  budget is emitted **whole**, not split mid-section, so a table inside
  it travels with the section intact regardless of size. Splitting a
  large table requires a table-aware chunker or a separate
  table-splitting pass; neither is wired up today. Under `paragraph` /
  `sliding`, the chunker is unaware of table structure and may slice
  rows mid-cell because those strategies operate on paragraph / window
  boundaries that have no notion of table integrity. PDF tables remain
  flattened in all cases (no extraction-time table parser is wired up
  for PDFs).
- **Metadata:** title / author / page numbers are dropped — only body text reaches the JSONL.
- **Encoding:** TXT / MD input auto-detects as `utf-8-sig` by default; non-UTF-8 bytes fall back to `errors="replace"` (binary noise becomes Unicode replacement characters) unless `--input-encoding CODEC` pins the real source codec. PDF / DOCX / EPUB carry their own encoding metadata and are unaffected by this flag.
- **Semantic chunking:** raises `NotImplementedError` until embedding support lands in a follow-up phase.

---

## Working with scanned PDFs (OCR handoff)

`forgelm ingest` does not perform OCR. Scanned PDFs without a text layer
surface as a warning and emit zero chunks. The clean way is to OCR
externally first, then pipe the result through ingestion. Two recipes,
in increasing order of cost:

### Recipe A — Tesseract (local, free)

```bash
# Install once.
brew install tesseract            # macOS
# or: sudo apt install tesseract-ocr tesseract-ocr-tur tesseract-ocr-eng  # Debian/Ubuntu

# 1. Convert each scanned PDF page to a searchable PDF (text layer added).
ocrmypdf scan_input.pdf scan_with_text_layer.pdf --language eng+tur

# 2. Now ingest as a normal text-bearing PDF.
forgelm ingest scan_with_text_layer.pdf --output data/scan.jsonl
```

`ocrmypdf` is the recommended wrapper — it handles deskew, page rotation,
and writes a hidden text layer rather than burning OCR'd characters into
the visual layer. Pure `tesseract` works too if you only need plain text:

```bash
# Per-page extraction, concatenate to a TXT, ingest the TXT.
pdftoppm -r 300 scan_input.pdf scan_page -png
for img in scan_page-*.png; do tesseract "$img" - >> scan_pages.txt; done
forgelm ingest scan_pages.txt --output data/scan.jsonl
```

### Recipe B — AWS Textract (cloud, paid; better on tables / forms)

```bash
# 1. Upload scanned PDFs to S3.
aws s3 cp scan_input.pdf s3://my-ingest-bucket/scan_input.pdf

# 2. Start an async Textract job. (See AWS Textract docs for the polling
#    + SNS notification pattern; a complete shell loop is out of scope.)
aws textract start-document-text-detection \
    --document-location "{\"S3Object\":{\"Bucket\":\"my-ingest-bucket\",\"Name\":\"scan_input.pdf\"}}"

# 3. Once the job finishes, dump LINE blocks to text and ingest.
python -c "
import boto3, sys
client = boto3.client('textract')
job = sys.argv[1]
result = client.get_document_text_detection(JobId=job)
for block in result['Blocks']:
    if block['BlockType'] == 'LINE':
        print(block['Text'])
" $JOB_ID > scan_textract.txt
forgelm ingest scan_textract.txt --output data/scan.jsonl
```

Textract is materially better than Tesseract on:
- Multi-column layouts (academic papers, magazines)
- Mixed-language pages
- Forms / tables (separate `start-document-analysis` API)

The trade-off is per-page cost (~$0.0015 / page for text detection at
the time of writing) and a network dependency.

### What about forms with PII?

Pre-process with `--pii-mask` after OCR, before publishing the dataset:

```bash
ocrmypdf medical_scan.pdf medical_with_text.pdf --language tur+eng
forgelm ingest medical_with_text.pdf --output data/medical.jsonl --pii-mask
forgelm audit data/medical.jsonl --output ./audit/
```

The audit step verifies redaction worked: any remaining PII flag in
`data_audit_report.json` is a row that escaped masking.

For larger corpora or a domain with lots of jargon, consider running
`forgelm --generate-data` (synthetic data pipeline) on the ingested JSONL
to expand a small seed into many Q&A pairs before fine-tuning.
