# Regex Hygiene Standard

> **Scope:** Every `re.compile`, `re.match`, `re.search`, `re.sub`, `re.findall`, and `re.split` call in [`forgelm/`](../../forgelm/) and [`tests/`](../../tests/).
> **Why this exists:** Phase 11/11.5/12 review cycles produced ~10 distinct SonarCloud / reviewer findings on regex correctness, ReDoS exposure, and code-smell patterns. This standard codifies the lessons so the next regex we ship doesn't trip the same flags.
> **Enforced by:** Code review checklist (in [code-review.md](code-review.md)) + the ReDoS guard in [logging-observability.md](logging-observability.md) when audit / ingest extends.

## Hard rules

These come straight from real findings on the ForgeLM codebase. If a new regex breaks one, fix the regex (or fail review).

### 1. `\w` is Unicode by default — pick the right form for your input

In Python, `\w` is **not** the same as `[A-Za-z0-9_]`. By default `\w` matches the full Unicode `\p{Word}` class — every letter in every script (`ş`, `é`, `中`, `Ω`, …) plus all Unicode digits. `[A-Za-z0-9_]` is strictly ASCII.

```python
import re
re.findall(r"\w+",          "ünicode türkçe")  # ['ünicode', 'türkçe']
re.findall(r"\w+",          "ünicode türkçe", re.ASCII)  # ['nicode', 't', 'rk', 'e']
re.findall(r"[A-Za-z0-9_]+", "ünicode türkçe")  # ['nicode', 't', 'rk', 'e']
```

**Rule of thumb:**

| Input shape | Use |
|---|---|
| Tokens / credentials / IDs whose grammar is ASCII-only (AWS keys, GitHub PATs, JWTs, base64) | `\w` **with** `flags=re.ASCII`, or the explicit `[A-Za-z0-9_]` |
| Natural-language text where any script counts (PII regex on Turkish names, multilingual prose) | bare `\w` (Unicode-aware) |
| Mixed identifier + free text | be explicit — pick one and document why |

Sonar `python:S6353` only fires on bare `[A-Za-z0-9_]` — but if you adopt `\w`, **say what you mean about Unicode**. The two failure modes are (a) `[A-Za-z0-9_]` rejecting a legitimate Turkish customer name in PII detection, and (b) bare `\w` matching `é` inside a token regex that should reject malformed inputs.

```python
# RIGHT for an ASCII-only credential — explicit re.ASCII flag.
# The standard's recommendation is the shorthand, but only with the flag
# so Unicode word chars don't leak into the match universe.
"github_token": re.compile(r"\b(?:ghp|gho)_\w{20,}", flags=re.ASCII),

# RIGHT for prose where Turkish / French / etc. should match.
_WORD_PATTERN = re.compile(r"\b[\w']+\b", re.UNICODE)  # explicit, intentional

# ALSO RIGHT — explicit ASCII class without flag, no ambiguity.
"github_token_v2": re.compile(r"\b(?:ghp|gho)_[A-Za-z0-9_]{20,}"),
```

If you genuinely need to exclude underscore (rare), use `[A-Za-z0-9]` and document why.

### 2. No single-char character classes; use the character

`[ ]` is a one-character class wrapping a single space. It's identical to a literal space, just slower for the engine and noisier to read. SonarCloud `python:S6328`.

```python
# WRONG — flagged in forgelm/ingestion.py (Phase 12 review round 2)
re.compile(r"^[ ]{0,3}(#{1,6})\s+(.+?)$", re.MULTILINE)

# RIGHT
re.compile(r"^ {0,3}(#{1,6})\s+(.+?)$", re.MULTILINE)
```

Same applies to `[a]`, `[\.]`, `[\\]` — strip the character class.

### 3. Bound your quantifiers

If the practical max is small, write it: `{1,6}` instead of `+`. Bounded quantifiers eliminate a whole class of ReDoS shapes and document the actual contract.

```python
# WEAK — accepts arbitrary-depth nesting; ATX heading depth is ≤ 6 in CommonMark
re.compile(r"^(#+)[ \t]+(.+)$")

# STRONG — bounded to the spec
re.compile(r"^(#{1,6})[ \t]+(.+)$")
```

### 4. Don't let two unbounded greedy/lazy quantifiers compete for the same characters

This is the #1 ReDoS shape we keep hitting. When two `*` / `+` / `*?` / `+?` quantifiers can both consume the same characters (overlapping character classes, or `.` matching everything), the engine has to try every split — O(n²) on lines that don't ultimately match.

```python
# WRONG — ReDoS-prone. Phase 12 audit confirmed: 100ms at n=2000, 600ms at n=5000.
# `[ \t]+`, `(.+?)`, and `[ \t]*$` can all match a space — engine tries every split.
re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*$", re.MULTILINE)

# RIGHT — anchor the body's first/last char on non-whitespace.
# Now `[ \t]+` and `[ \t]*` can't compete with the body capture.
re.compile(r"^ {0,3}(#{1,6})[ \t]+(\S(?:[^\n]*\S)?)[ \t]*$", re.MULTILINE)
```

The fix pattern: replace `(.+?)` between two whitespace runs with `(\S(?:[^\n]*\S)?)` — non-whitespace anchor at both ends, negated character class `[^\n]` in the middle.

### 5. Don't use `\s` when you mean `[ \t]`

`\s` matches `[ \t\n\r\f\v]` — including newlines. Under `re.MULTILINE` this almost always introduces ambiguity that the engine has to backtrack through, especially when the regex is anchored with `^…$`.

```python
# WRONG — `\s` overlaps with newlines under MULTILINE
re.compile(r"^(\w+)\s+(.+)$", re.MULTILINE)

# RIGHT — explicit "horizontal whitespace only"
re.compile(r"^(\w+)[ \t]+(.+)$", re.MULTILINE)
```

`\s` is acceptable in single-line contexts where newlines genuinely *should* count (rare in our codebase — the audit / ingest pipelines all process pre-split text).

### 6. Avoid `.*?` with a back-reference under `re.DOTALL`

This combination is the SonarCloud `python:S5852` "polynomial runtime" trigger. Even when CPython's regex engine handles it well in practice, the static analyser flags every instance.

```python
# WRONG — Sonar flagged data_audit.py L896 (Phase 12 review round 2.5).
# The `(?P=fence)` back-reference + lazy `.*?` + DOTALL is a textbook ReDoS shape.
_CODE_FENCE_BLOCK = re.compile(
    r"^ {0,3}(?P<fence>`{3,}|~{3,})[^\n]*\n.*?^ {0,3}(?P=fence)[ \t]*$",
    re.MULTILINE | re.DOTALL,
)

# RIGHT — replace the regex with a per-line state machine.
# Same logic; provably O(n); identical behaviour pinned by tests.
def _strip_code_fences(text: str) -> str:
    out, open_fence = [], None
    for line in text.splitlines(keepends=True):
        ...
```

The principle: when a regex with a back-reference needs to span multiple lines, write it as a state machine. The line-walker pattern in [`forgelm/ingestion.py::_markdown_sections`](../../forgelm/ingestion.py) and [`forgelm/data_audit/_quality.py::_strip_code_fences`](../../forgelm/data_audit/_quality.py) is the model.

### 7. Anchor `^` / `$` and use `re.MULTILINE` deliberately

`^` and `$` mean different things with and without `re.MULTILINE`. Pick one and document the intent.

- **`re.MULTILINE`** when you're working line-by-line on a multi-line string. Use `[^\n]` instead of `.` for the body (avoids overlap with `^` / `$`).
- **No `re.MULTILINE`** when the input is one logical line at a time (passed through `for line in text.splitlines()` already). Then `^` / `$` are start / end of the whole string.

### 8. No `^.*` when you mean "from the start"

`^.*` invites the engine to backtrack `.*` for every failure. Pick the regex that matches your actual intent — and if you only need a fixed-prefix check, skip regex entirely:

```python
# WRONG — `^.*foo` against a line that doesn't contain "foo" walks the whole line.
re.compile(r"^.*ERROR:")

# RIGHT — match only lines that *start* with "ERROR:".
re.compile(r"^ERROR:")          # or, even better: text.startswith("ERROR:")

# RIGHT — match "ERROR:" anywhere in the string (no anchor, no `^.*`).
re.compile(r"ERROR:")           # equivalent to the unanchored search intent
```

Avoid clever-looking variants like `r"^[^E]*ERROR:"` — `[^E]*` matches *zero* characters when the line begins with `E`, so `"EEE ERROR: boom"` fails to match even though `ERROR:` is present. Spell out the intent instead.

For "starts with X", just use `text.startswith("X")`. Regex isn't the answer to every string question.

## ReDoS exposure budget

Some regexes consume operator-controlled input (e.g. `audit` / `ingest` walking user-provided JSONL or PDFs). Adversarial 100K-row corpora are a realistic threat for a CI/CD pipeline that runs ForgeLM on a webhook trigger.

For any regex that runs on operator-controlled input:

- **Verify linearity empirically — measure scaling, not absolute milliseconds.** Run the regex at *multiple* input sizes (e.g. 1K / 5K / 10K characters of pathological input) and take the **median of N runs** (we use 5 in [`tests/test_phase12_review_fixes.py`](../../tests/test_phase12_review_fixes.py)) to absorb shared-CI jitter. Acceptable: roughly linear growth — doubling the input doubles the time. Broken: super-linear (≥ ~3× on a 2× input) or any explosive jump. Don't pin a hard ms cutoff in the standard; what matters is the *shape* of the curve, not the wall-clock on whoever's laptop. Existing ReDoS-regression tests assert generous absolute bounds (≤ 100 ms / ≤ 1 s on 10K input) only as a safety floor — a real ReDoS blows past those by orders of magnitude.
- **Or replace with a non-regex parser.** State machines (line walkers) are O(n) by construction. We use them in [`forgelm/data_audit/_quality.py::_strip_code_fences`](../../forgelm/data_audit/_quality.py) and [`forgelm/ingestion.py::_markdown_sections`](../../forgelm/ingestion.py) precisely because the regex equivalents tripped SonarCloud.

The pathological-input benchmark for our regexes:

```python
import re, time
pat = re.compile(r"YOUR_PATTERN")

for n in [1_000, 5_000, 10_000]:
    payload = "ADVERSARIAL_BUILDER" * n
    t0 = time.perf_counter()
    pat.search(payload)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    print(f"n={n}: {elapsed_ms:.2f} ms")
```

Linear scaling on doubling input → safe. Quadratic / exponential scaling → broken; fix the regex or replace with a parser.

## Test fixture hygiene

Test files that exercise security-sensitive regexes (PII, credentials, JWTs) inevitably contain credential-shaped strings. Repo-wide secret scanners (gitleaks, trufflehog, GitGuardian) treat full literals as leaks even when they're test fixtures.

**Build secret-shaped fixtures from inert fragments at runtime.** The regex still has to match the canonical shape; no full literal credential lives in the source tree.

```python
# WRONG — gitleaks flags this as a real AWS key leak.
text = "config: aws_access_key_id=AKIAIOSFODNN7EXAMPLE end"

# RIGHT — module-level fragment-built constant.
FAKE_AWS_KEY: str = "AKIA" + "IOSFODNN7" + "EXAMPLE"
text = f"config: aws_access_key_id={FAKE_AWS_KEY} end"
```

The same applies to PEM block markers in regex source (we split `r"-----" + r"BEGIN " + r"..."` in [`forgelm/data_audit/_secrets.py::_SECRET_PATTERNS`](../../forgelm/data_audit/_secrets.py) for this reason).

## Pre-merge checklist

Before opening a PR that touches regex:

- [ ] No unintentional `[A-Za-z0-9_]` (use `\w`); explicit ASCII-only classes
      are permitted for deliberate ASCII-only grammars when paired with
      `re.ASCII` or a documented justification (see [Rule 1](#1-w-is-unicode-by-default--pick-the-right-form-for-your-input) — `\w` + `re.ASCII` and an explicit ASCII class are both listed as RIGHT).
- [ ] No single-char character classes (use the character).
- [ ] Quantifiers bounded where the spec allows (`{1,6}` not `+`).
- [ ] No two competing unbounded quantifiers over the same character class.
- [ ] No `.*?` + back-reference + `DOTALL` (replace with a state machine).
- [ ] `re.MULTILINE` matches the actual input shape — line-by-line vs. whole-string.
- [ ] If the regex runs on operator-controlled input: linearity verified empirically — pathological benchmark at **1K / 5K / 10K** characters, **median of N runs** (we use 5), and **approximately linear growth** across sizes (per the methodology in [ReDoS exposure budget](#redos-exposure-budget)).
- [ ] Test fixtures with credential-shaped strings built from inert fragments.

## Related standards

- [coding.md](coding.md) — overall Python style.
- [code-review.md](code-review.md) — the `regex` line item in the review checklist.
- [testing.md](testing.md) — how to add ReDoS regression tests.

## Real findings this standard absorbs

Each rule above traces back to a concrete review finding:

| Rule | Finding (file:line) | Phase |
|---|---|---|
| `[A-Za-z0-9_]` → `\w` | `forgelm/data_audit/_secrets.py:_SECRET_PATTERNS["github_token"]` | 12 round 2 |
| Single-char class | `ingestion.py:_MARKDOWN_HEADING_PATTERN` | 12 round 2 |
| Two competing quantifiers | `ingestion.py:_MARKDOWN_HEADING_PATTERN` | 12 round 2.5 (ReDoS confirmed) |
| `\s` overlap with `\n` | `ingestion.py:_MARKDOWN_HEADING_PATTERN` (early Phase 12) | 12 round 1 |
| `.*?` + back-ref + DOTALL | `forgelm/data_audit/_quality.py:_CODE_FENCE_BLOCK` | 12 round 2.5 |
| Test fixture fragmentation | `tests/test_data_audit_phase12.py`, `tests/test_ingestion_phase12.py` | 12 round 2 |
| PEM marker fragmentation | `forgelm/data_audit/_secrets.py:_SECRET_PATTERNS["openssh_private_key"]` | 12 round 2 |

Note: these findings predate the Phase 12.6 `data_audit.py` → `forgelm/data_audit/`
sub-package split (see [architecture.md](architecture.md) "Module topology");
citations above use the current post-split paths. `tests/test_data_audit_phase12.py`
retains its historical name but exercises the split modules.
