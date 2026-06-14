---
name: sync-bilingual-docs
description: Use this skill when editing any docs/reference/* or other bilingual documentation pairs in ForgeLM. Keeps the .md and -tr.md mirrors structurally aligned per docs/standards/localization.md. Triggered by requests like "update the configuration docs", "add a section about X to the usage guide", "translate the new feature docs".
---

# Skill: Keep Bilingual Docs in Sync

ForgeLM ships user-facing docs in English + Turkish. The mirrors must stay in structural sync or readers switching languages get confused and CI (future) will complain.

## When to use

Any change to these paths or their siblings:
- `docs/README.md` (if bilingual)
- `docs/product_strategy.md` / `product_strategy-tr.md`
- `docs/roadmap.md` / `roadmap-tr.md`
- `docs/reference/*.md` (has matching `-tr.md`)
- `docs/usermanuals/{en,tr}/**/*.md` (the static-site SPA viewer source)

Do **not** use for:
- `docs/standards/*` (English only)
- `docs/marketing/*` (gitignored working-memory; mixed languages)
- `docs/qms/*` (English only)
- `docs/design/*` (English only)
- `docs/guides/*` (English only for now — Turkish is future work)

Do **not** reference any file under gitignored working-memory directories
(`docs/marketing/`, `docs/analysis/`) from the bilingual docs you edit —
those paths are local-only and won't resolve in fresh clones.

**`docs/usermanuals/` is link-isolated.** Pages there feed the static-site
SPA viewer (`site/usermanual.html`), which only renders SPA hash-router
routes (`#/<section>/<page>`) and external HTTPS URLs. Repo-relative
links like `../../../guides/foo.md` or even intra-manual paths like
`../concepts/choosing-trainer.md` 404 in the SPA. When you edit a
usermanual page, every link MUST be one of:

1. A SPA route `#/<section>/<page>` where the target file exists under
   `docs/usermanuals/<lang>/<section>/<page>.md`.
2. An absolute HTTPS URL (use `https://github.com/HodeTech/ForgeLM/blob/main/<path>`
   for project files that live outside the manual — guides, references,
   QMS templates, source files, the roadmap).
3. A pure same-file anchor (`#heading-slug`).

The guard `tools/check_usermanual_self_contained.py --strict` enforces
this; see `docs/standards/documentation.md` "User-manual link discipline"
for the full ruleset.

## Required reading

1. [docs/standards/localization.md](../../../docs/standards/localization.md) — the full policy
2. [docs/standards/documentation.md](../../../docs/standards/documentation.md) — markdown conventions

## Rules in one screen

- **Same H2 count, same order.** If the EN file has 5 H2 sections, the TR file has 5 — in the same order.
- **Links update together.** If you change a link target in EN, change it in TR.
- **Code blocks are copy-pasted.** Code is language-neutral; don't translate Python/YAML/JSON.
- **Wording may vary within a section.** Idiomatic Turkish trumps literal translation.
- **Don't machine-translate.** Google Translate output pasted without review is a bug; either write it properly or leave a tracking issue.

## Workflow

### If you're adding content in English

```
1. Edit file.md (English)
2. Same PR: edit file-tr.md — add matching section in same position
3. Verify H2 count matches: grep -c "^## " docs/reference/X.md equals grep -c "^## " docs/reference/X-tr.md
4. Verify links in TR point to TR mirrors (-tr.md) where those exist
5. Commit together
```

### If you're adding content in Turkish

Same workflow, mirrored. But: the EN version is canonical — if you're writing new content, write the EN first, then mirror.

### If you can't write Turkish

Acceptable fallback (for non-native-speaker contributors):

1. Edit `file.md` (English)
2. Add `<!-- TR translation pending, tracked in #ISSUE -->` at the top of `file-tr.md` for the new section
3. Open a tracking issue labeled `translation-debt`
4. Reference the issue in the PR description
5. Reviewer decides whether to accept or require translation in this PR

**Do not paste untranslated English into the TR file.** Either translate properly or mark pending explicitly.

## Link parity

If the EN file (say `docs/reference/foo.md`) says:

```markdown
See [Architecture](architecture.md) for the module layout.
```

The TR file (`docs/reference/foo-tr.md`) should say:

```markdown
Modül yapısı için [Mimari](architecture-tr.md) belgesine bakın.
```

Both link text and target translate. A TR reader clicking an EN link mid-document is a UX bug.

## Verification commands

```bash
# H2 count must match
EN_COUNT=$(grep -c "^## " docs/reference/configuration.md)
TR_COUNT=$(grep -c "^## " docs/reference/configuration-tr.md)
[ "$EN_COUNT" = "$TR_COUNT" ] && echo "OK" || echo "MISMATCH"

# Links to other bilingual files should use -tr.md from TR files
grep -E "\]\([^)]+\.md\)" docs/reference/configuration-tr.md | \
  grep -v "\-tr\.md" | \
  grep -v "^.*standards/"  # standards are EN only, allowed
# Should produce no output
```

(These aren't enforced by CI yet, but the rules are the same.)

## Terminology

Consistent glossary from [localization.md](../../../docs/standards/localization.md):

| English | Turkish |
|---|---|
| fine-tuning | ince ayar |
| training | eğitim |
| config / configuration | yapılandırma / config |
| pipeline | pipeline (tercih) / boru hattı |
| safety evaluation | güvenlik değerlendirmesi |
| audit trail | denetim izi |
| compliance | uyumluluk |
| quantization | quantization (çevirmeyi zorlama) |
| layer | katman |
| tokenizer | tokenizer |
| EU AI Act | EU AI Act (ilk kullanımda: AB Yapay Zekâ Yasası) |

Acronyms (LoRA, PEFT, DPO, GRPO) stay unchanged in both languages.

## Common mistakes

- **Adding a new section in EN only** — TR drifts. Update both in the same PR.
- **Reordering sections in EN only** — TR becomes misaligned. Move both.
- **Changing a link target in EN only** — TR link 404s. Change both.
- **Translating code examples** — Python keywords don't translate. Leave code alone.
- **Adding decorative emoji to one and not the other** — cosmetic but inconsistent. Pick one style.
- **Machine-translated Turkish** — reads like broken code. Don't.

## When the mirror doesn't exist yet

The file list in [localization.md](../../../docs/standards/localization.md#whats-translated) marks which paths have TR mirrors. If you're working on one that doesn't:

- Don't create a TR mirror preemptively — creates maintenance burden.
- If you want to add one, first update the table in localization.md with reasoning.
- Then create the TR mirror and use this skill going forward.

## Related skills

- `add-config-field` — always touches `configuration.md` + `configuration-tr.md`
- `add-trainer-feature` — typically touches multiple `docs/reference/` files
- `cut-release` — CHANGELOG updates don't need a TR mirror (English only)
