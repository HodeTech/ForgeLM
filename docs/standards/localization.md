# Localization Standard

> **Scope:** Which parts of ForgeLM get translated, how translations are paired with originals, and what stays English only.
> **Enforced by:** Review + [`tools/check_bilingual_parity.py --strict`](../../tools/check_bilingual_parity.py) (Wave 3 / Faz 24): a CI guard that fails on any H2/H3/H4 spine mismatch between an EN file and its `-tr.md` mirror. Scope has grown release over release (9/9 at introduction, 23/23 through Wave 4's QMS bilingualisation, 49 hand-registered pairs as of this writing) — the pair registry (`_PAIRS` in the tool) plus every auto-discovered `docs/usermanuals/en/**` ↔ `tr/**` page pair (`_all_pairs()`) is the authoritative, self-updating count; don't hardcode a snapshot number here.

## The policy in one line

**EN + TR are mandatory for user-facing docs. DE / FR / ES / ZH are deferred to a future translation cycle (formalised post-Phase-12.6).** Code, CLI output, config keys, logs, and internal dev docs are English only.

## Supported languages

- **EN + TR (authored).** All user-facing docs and user-manual pages are written and reviewed in both English and Turkish. EN+TR mirror parity is mandatory for the doc tree — see "Structural mirror rule" below.
- **DE / FR / ES / ZH (site UI translated; user-manual content English-fallback).** The site language picker exposes all six languages, and `site/js/translations.js` carries the marketing/product copy in DE/FR/ES/ZH. The user-manual content (`docs/usermanuals/...`) is **only authored in EN + TR**; when a deferred-language user picks DE/FR/ES/ZH, the manual side falls back to English via the i18n chain (`tableForLang(...) → DEFAULT='en'`). `tools/build_usermanuals.py` therefore emits JS bags only for the authored set (`en`, `tr`); the deferred languages reuse the EN bag through the fallback path.

A language is "site-translated" when its strings live in `site/js/translations.js`. A language is "manual-authored" only when `docs/usermanuals/<lang>/` content is reviewed by a native or near-native speaker.

Rationale:

- ForgeLM's market includes a significant Turkish-speaking segment (banking + regulatory + research) served by Turkish-language docs.
- Code in multiple languages fragments the community beyond a ceiling that ForgeLM doesn't need to cross.
- Tooling (ruff, pytest, HF error messages) is English; forcing localized mixing creates confusion.

## What's translated

| Path | TR mirror? | Reason |
|---|---|---|
| `README.md` | Yes (`README.md` has TR section inline or separate file) | Entry point |
| `docs/product_strategy.md` | Yes | Stakeholder-facing vision |
| `docs/roadmap.md` | Yes | Public planning |
| `docs/reference/architecture.md` | Yes | End-user architecture reference |
| `docs/reference/configuration.md` | Yes | End-user config reference |
| `docs/reference/usage.md` | Yes | End-user how-to |
| `docs/reference/data_preparation.md` | Yes | End-user data prep |
| `docs/reference/distributed_training.md` | Yes | End-user distributed |
| `docs/reference/compliance_summary.md` | Yes | EN+TR mirror (`compliance_summary-tr.md`) registered and spine-gated |
| `docs/guides/*.md` | Yes, except one (Wave 1 + 2b + 3 + 4 progressively bilingualised) | Bilingualised today (complete, reviewed Turkish translations, registered in `tools/check_bilingual_parity.py::_PAIRS`): `air_gap_deployment`, `alignment`, `cicd_pipeline`, `data_audit`, `gdpr_erasure`, `getting-started`, `human_approval_gate`, `ingestion`, `iso_soc2_deployer_guide`, `library_api`, `performance`, `pipeline`, `quickstart`, `safety_compliance`, `troubleshooting`. Single-language (EN): `enterprise_deployment` — TR mirror is an open follow-up |
| `docs/usermanuals/{en,tr}/` | Yes | EN+TR manual content authored & reviewed; DE/FR/ES/ZH fall back to EN via the `tableForLang(...) → DEFAULT='en'` chain (deferred to a future translation cycle). **Link-isolated:** pages here may only link to other in-manual pages (via the SPA route `#/<section>/<page>`) or external HTTPS URLs — see [`documentation.md` "User-manual link discipline"](documentation.md#user-manual-link-discipline-docsusermanuals). |
| `docs/design/*.md` | No | Internal design history |
| `docs/standards/*.md` | No | Contributor-facing |
| `docs/qms/*.md` | Yes | QMS templates ship bilingual EN+TR — regulated orgs in Turkey adopt the TR mirror as the canonical operating template. Wave 4 (Faz 26) drove the EN+TR pair count from 0 to 14 with the new ISO 27001 / SOC 2 alignment QMS docs |
| `docs/marketing/**` | Mixed (strategy/ is TR, operational is EN) | Local-only; author's choice |
| `docs/roadmap/**` | Partial | `docs/roadmap.md` ↔ `docs/roadmap-tr.md` mandatory mirror; sub-files (`roadmap/*.md`) English only with the TR summary surfaced through the top-level mirror |

Anything not in the "Yes" set is **English only**. Don't translate preemptively.

## File naming

**Pattern:** `<name>.md` (English) paired with `<name>-tr.md` (Turkish) in the same directory.

- ✅ `configuration.md` + `configuration-tr.md`
- ❌ `configuration.en.md` + `configuration.tr.md`
- ❌ `configuration.md` + `yapilandirma.md`

Rationale: simpler to search, simpler to CI-check sync, consistent with what exists.

## Structural mirror rule

TR mirrors must have the **same sections, in the same order, with the same number of sections** as their EN counterpart. Two reasons:

1. A reader switching languages should find the same content in the same place.
2. CI (future) can check "does every EN heading have a TR heading?" as a cheap drift detector.

**What may differ:**

- Wording within a section.
- Idioms and examples adapted for Turkish readers.
- Small TR-specific notes (e.g., BDDK / KVKK references) in a dedicated subsection.

**What must match:**

- H1 title corresponds (`# ForgeLM Roadmap` ↔ `# ForgeLM Yol Haritası`).
- H2 count and order.
- Tables of contents, lists of files, and rule lists.
- Code blocks (content is language-neutral).
- Links: **same set of targets, wrapped in TR link text — but a TR page must
  point at the TR mirror of a target when one exists.** A `*-tr.md` page that
  links the un-suffixed English sibling (`audit_event_catalog.md`) when
  `audit_event_catalog-tr.md` exists routes a Turkish reader out of Turkish;
  rewrite it to the `-tr.md` mirror, preserving the translated link text. The
  one exempt case is the `**Ayna:**` (Mirror) backlink line, which
  intentionally points back at the EN original. Enforced by
  `tools/check_tr_links_prefer_mirror.py` (wired into `ci.yml`).

## What to do when the EN doc changes

| EN change | TR action |
|---|---|
| Typo fix or wording tweak | Optional: update TR for parity if obvious |
| Section added | **Required:** add matching section to TR in same PR |
| Section removed | **Required:** remove matching section from TR in same PR |
| Structural reorganization | **Required:** restructure TR in same PR |
| Link target changed (file moved) | **Required:** update TR link target in same PR |

If you can't do the TR update in the same PR (e.g., you don't write Turkish), open a tracking issue labeled `translation-debt` and reference it in the PR. This is a non-blocking exception that the reviewer decides on.

## CLI and code

**English only.** Specifically:

- All `logger.info/warning/error` messages
- All `argparse` help strings
- All user-facing exception messages
- All docstrings
- All YAML config keys (`trainer_type`, not `egitici_turu`)
- All JSON output field names

**Reason:** CLI is the UX that Ahmet and Sarah (our two primary personas from the internal personas doc) share. MLOps engineers worldwide read English errors. CLI strings are the single most viral part of the tool — screenshots, stackoverflow, LLM training data.

Exception: wizard prompts may be translated if ever we ship a localized wizard, but that's behind a `--lang=tr` flag and not the default. Currently not planned.

**Native-language template titles.** Quickstart / dataset templates whose intended audience is non-English speakers may carry the native-language name in parentheses alongside the English form, e.g. `Medical Q&A (Türkçe / Turkish)`. This applies to the title field only — code identifiers, function names, log messages, error strings, and CLI flags remain English-only. The canonical example is `forgelm/quickstart.py:106`.

## Terminology

For the parts we do translate, keep terminology consistent. A small glossary:

| English | Turkish | Notes |
|---|---|---|
| fine-tuning | ince ayar | Preferred over "ince-ayarlama" |
| training | eğitim | |
| model | model | Cognate |
| config / configuration | yapılandırma / config | Either; prefer "yapılandırma" in prose, `config` in code references |
| pipeline | boru hattı / pipeline | Either; prefer transliterated "pipeline" in dev-heavy docs |
| safety evaluation | güvenlik değerlendirmesi | |
| audit trail | denetim izi | |
| compliance | uyumluluk | |
| quantization | quantization | Not commonly translated |
| attention | dikkat | |
| layer (ML) | katman | |
| tokenizer | tokenleştirici / tokenizer | Either; technical audiences prefer "tokenizer" |
| LoRA / PEFT / etc. | unchanged | Acronyms |
| EU AI Act | AB Yapay Zekâ Yasası / EU AI Act | First mention spell it out, then abbreviate |

When in doubt, look at sibling TR docs and match the most frequent choice. Don't invent new terminology mid-series.

## Quality bar for Turkish

The audience is technical. Priorities:

1. **Correct information** (same facts as EN).
2. **Plain, technical Turkish.** Not literary. Not Osmanlı.
3. **Transliterated technical terms where Turkish equivalents are awkward.** "Pipeline çalıştır" reads better than "boru hattını çalıştırın" in a CLI doc.
4. **No machine-translation output.** Google Translate / DeepL output pasted without review is worse than English only. If you can't rewrite a machine-translated passage into natural Turkish, don't add it.

## When a Turkish reader encounters English content

Link from the TR doc back to the EN with "bu konunun detayı (İngilizce):" (details in English). Don't translate placeholder content just to avoid the English link.

## Site chrome translations (deferred tiers)

### Site chrome — deferred-tier translation lag (DE/FR/ES/ZH)

`site/js/translations.js` carries six-locale chrome translations
(EN, TR, DE, FR, ES, ZH) maintained as a single registry. EN and TR
are **active tiers**: every Wave 1-5 chrome key MUST be present in both,
and the EN ↔ TR pair is checked at parity inside the file (no missing
keys at v0.5.5 HEAD). DE, FR, ES, ZH are **deferred tiers**: chrome keys
may lag behind the active tiers between releases, and missing keys fall
back to EN at runtime via the i18n chain in `site/js/translations.js`
(the same `tableForLang(...) → DEFAULT='en'` chain used by the user-manual
content).

This codifies the cosmetic asymmetry: at v0.5.5 HEAD, deferred-tier blocks
are short ~42 keys relative to EN/TR (Wave-5 governance / enterprise /
ISO / GDPR / safety-eval feature copy that landed EN+TR-only). v0.6.x
will run a native-review pass on accumulated deferred-tier debt; until
then, machine-translated additions are NOT preferred — leave the key
missing so the fallback chain shows the EN value (which a non-native
reader can usually still parse) rather than ship a low-quality translation
that a native reader would have to retire later. This mirrors the
"no machine-translation output" rule in §"Quality bar for Turkish" above.

The bilingual-parity gate (`tools/check_bilingual_parity.py --strict`)
does NOT extend to site chrome — its scope is the EN ↔ TR `*.md` /
`*-tr.md` doc pairs only. Site chrome has its own gate:
`tools/check_site_chrome_parity.py` IS wired into CI (`.github/workflows/ci.yml`
step "Site chrome (EN/TR translation) parity") and runs on every PR — but
**without `--strict`**, so it enforces only the active-tier EN ↔ TR key
parity described above; drift there fails the build. `--strict` (which
additionally gates the DE/FR/ES/ZH deferred tiers) is intentionally
withheld from CI per this deferred-tier policy until v0.6.x activates the
native-review cycle; operators can still run `--strict` locally to audit
the deferred-tier gap. See `docs/roadmap/risks-and-decisions.md` for the
v0.6.x activation plan.

## Future (not today)

- **DE / FR / ES / ZH user-manual translation** — explicitly deferred at v0.5.5 (Phase 12.6 closure cycle): the user-manual side falls back to EN via the i18n chain. A follow-up cycle picks this up after v0.6.0 ships, when adoption signal justifies the translation cost.
- **Spanish translations beyond the site UI** — not planned for 2026. The marketing docs mention a Spanish-speaking contributor but Spanish market entry isn't on the roadmap.
- **Localized CLI output behind a language flag** — not planned.
- **Automatic translation in CI** — not trustworthy enough, doesn't save review time.

If a new language becomes strategic, update this document first, then start translations.
