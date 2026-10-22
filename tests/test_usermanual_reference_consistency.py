"""H10 doc-consistency regression tests for docs/usermanuals reference pages.

These lock the corrected user-manual claims to the code/contract so a
future edit re-introducing the drift fails CI:

- F-P7-OPUS-02 — cli.md exit-codes table must not promise exit 130 for
  Ctrl+C (the dispatcher clamps it to EXIT_TRAINING_ERROR == 2).
- F-P7-OPUS-03 — cli.md must disclose that argparse usage errors exit 2,
  while post-parse config/semantic validation exits 1.
- F-P7-OPUS-18 — cli.md must describe ``forgelm reject`` as PRESERVING
  the staging directory, never "discard staging".
- #101 — GDPR / PII wording: retired phrases, the Article 17(3) qualifier
  invariant, the ``--kind artefacts`` warning, the digest-correlation rule, the
  CI-gate recipe, the GDPR page's runnable examples, and the gpu-cost / chat /
  pii-masking pages checked against the code they describe.

No GPU / no network — pure file reads against the shipped docs tree.
"""

from __future__ import annotations

import bisect
import re
import shlex
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _cli_md(lang: str) -> str:
    return (_REPO_ROOT / "docs" / "usermanuals" / lang / "reference" / "cli.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("lang", ["en", "tr"])
class TestExitCodeTableConsistency:
    def test_ctrl_c_not_documented_as_130(self, lang: str) -> None:
        # The dispatcher clamps KeyboardInterrupt to EXIT_TRAINING_ERROR (2);
        # the cli.md table must not advertise a 130 row.
        text = _cli_md(lang)
        assert "| 130 |" not in text, f"{lang} cli.md still lists a 130 exit-code row"

    def test_argparse_usage_error_disclosed_as_exit_2(self, lang: str) -> None:
        text = _cli_md(lang).lower()
        # The corrected table prose must mention that argparse usage errors
        # exit 2 (the clamp/usage-error disclosure F-P7-OPUS-03 added). Assert
        # the exit-code claim itself, not the bare ``argparse`` token, so a
        # regression that drops the "exit 2" coupling fails.
        assert re.search(r"argparse.{0,120}\b2\b|\b2\b.{0,120}argparse", text), (
            f"{lang} cli.md must couple argparse usage errors with exit code 2"
        )


@pytest.mark.parametrize("lang", ["en", "tr"])
class TestRejectStagingConsistency:
    def test_reject_does_not_claim_discard_staging(self, lang: str) -> None:
        # F-P7-OPUS-18: reject PRESERVES staging for forensics.
        text = _cli_md(lang).lower()
        assert "discard staging" not in text
        # TR mirror used "staging'i at" ("discard staging").
        assert "staging'i at" not in text


# ---------------------------------------------------------------------------
# GDPR citations and erasure behaviour (issue #101)
# ---------------------------------------------------------------------------
#
# Article 17(3)(b) disapplies erasure only to the extent a specific Union or
# Member State legal obligation requires the processing; it does not preserve
# an audit log as a class.  The retention defaults are ForgeLM choices, not
# legal periods.  These pages and code strings used to say otherwise.

_GDPR_CLAIM_FILES = (
    "forgelm/config.py",
    "forgelm/cli/_parser.py",
    "forgelm/cli/subcommands/_purge.py",
    "forgelm/cli/subcommands/_reverse_pii.py",
    "config_template.yaml",
    "docs/design/gdpr_erasure.md",
    "docs/guides/gdpr_erasure.md",
    "docs/guides/gdpr_erasure-tr.md",
    "docs/guides/iso_soc2_deployer_guide.md",
    "docs/guides/iso_soc2_deployer_guide-tr.md",
    "docs/reference/configuration.md",
    "docs/reference/configuration-tr.md",
    "docs/reference/purge_subcommand.md",
    "docs/reference/purge_subcommand-tr.md",
    "docs/reference/reverse_pii_subcommand.md",
    "docs/reference/reverse_pii_subcommand-tr.md",
    "docs/reference/compliance_summary.md",
    "docs/reference/compliance_summary-tr.md",
    "docs/reference/audit_event_catalog.md",
    "docs/reference/audit_event_catalog-tr.md",
    "docs/usermanuals/en/compliance/gdpr-erasure.md",
    "docs/usermanuals/tr/compliance/gdpr-erasure.md",
    "docs/usermanuals/en/reference/configuration.md",
    "docs/usermanuals/tr/reference/configuration.md",
    "docs/usermanuals/en/compliance/human-oversight.md",
    "docs/usermanuals/tr/compliance/human-oversight.md",
    "docs/qms/sop_change_management.md",
    "docs/qms/sop_change_management-tr.md",
    "docs/qms/sop_incident_response.md",
    "docs/qms/sop_incident_response-tr.md",
    "docs/qms/sop_data_management.md",
    "docs/qms/sop_data_management-tr.md",
    "docs/qms/encryption_at_rest.md",
    "docs/qms/encryption_at_rest-tr.md",
    "docs/usermanuals/en/compliance/gdpr.md",
    "docs/usermanuals/tr/compliance/gdpr.md",
)

_RETIRED_GDPR_CLAIMS = (
    r"17\(3\)\(b\)\s+preserves",
    r"17\(3\)\(b\)\s+(?:exemption|defence)",
    r"17\(3\)\(b\)\s+records",
    r"17\(3\)\(b\)\s+savunması",
    r"17\(3\)\(b\)\s+audit\s*/\s*muhasebe",
    r"17\(3\)\(b\)\s+kayıt",
    r"append-only\s+(?:Article|Madde)\s+17\(3\)\(b\)",
    r"5\s+years\s+default\s+\(Article\s+5\(1\)\(e\)\)",
    r"varsayılan\s+5\s+yıl\s+\(Madde\s+5\(1\)\(e\)\)",
    r"legal[\s-]+obligation\s+record",
    r"compliant[\s-]+out[\s-]+of[\s-]+the[\s-]+box",
    r"compliant\s+by\s+default",
    r"years\s+\(Article\s+12\s+record-keeping\)|Article\s+12\s+record-keeping\s+baseline",
    r"Horizons\s+honou?r\s+GDPR",
    r"removed\s+by\s+default\s+after\s+a\s+defined\s+retention\s+horizon",
    r"after\s+a\s+`forgelm\s+reject`\s+decision\s+before\s+scheduled\s+cleanup",
    r"work-week\s+to\s+act\s+on\s+a\s+`forgelm\s+reject`",
    r"Madde\s+12\s+kayıt-tutma",
    r"(?:deletes|removes|erases)\s+(?:a|the)\s+run['’]s\s+compliance\s+bundle",
    r"staging\s+directory\s+or\s+compliance\s+bundle",
    r"physically\s+prunes",
    r"fiziksel\s+olarak\s+prune",
    r"until\s+retention\s+expires",
    r"retention\s+süresi\s+dolana",
    r"compliance/\*\.json`\s+files\s+whose\s+name\s+embeds",
    r"staging\s+directory,?\s+or\s+(?:the\s+|a\s+)?compliance\s+bundle",
    r"compliance\s+bundle'ı(?:nı)?\s+(?:siler|atomik)",
    r"compliance\s+paketini\s+siler",
    r"compliance/\*\.json`\s+dosyalar",
    r"retain\s+staging\s+model\s+after\s+forgelm\s+reject",
    r"forgelm\s+reject\s+sonrası\s+staging",
    r"TTL\s+after\s+`forgelm\s+reject`",
    r"planlı\s+temizlenmeden",
    r"bir\s+iş\s+haftası",
    r"GDPR[^.\n]{0,80}onurlandırır",
    r"17\(3\)\(b\)\s+protects",
    r"17\(3\)\(b\)[^.\n]{0,40}\bkorur",
    # The GDPR / KVKK page (CR-A11-001): masking is a fixed regex set, not anonymisation, and no audit report can
    # confirm that a model holds nothing about a person.
    r"identifiable\s+subjects\s+are\s+removed",
    r"no\s+specific\s+person's\s+data\s+is\s+recoverable",
    r"audit\s+report\s+confirms\s+this",
    r"tanımlanabilir\s+özneler\s+kaldırılmıştır",
    r"verisi\s+geri\s+çıkarılamaz",
    r"Audit\s+raporu\s+bunu\s+teyit",
    r"international_transfers",
    r"No\s+matches\s+found\s+in\s+masked",
    r"eşleşme\s+bulunamadı\s+\(maskelenmiş",
    r"auto-delete\s+originals",
    r"orijinalleri\s+otomatik\s+sil",
    r"records\s+the\s+intended\s+TTL",
    r"amaçlanan\s+TTL'yi",
    r"SHA-256\s+manifest\s+for\s+tamper",
    r"Tamper-evidence\s+için\s+SHA-256",
    r"Source\s+attribution\s+\(which\s+document",
    r"Kaynak\s+atfı\s+\(her\s+satırın",
    r"pii_mask:",
    # DOC-D2B02-024 / -051: the memorisation warning keys on a final_model/ directly under --output-dir.
    r"for\s+any\s+run\s+that\s+consumed",
    r"tüketen\s+herhangi\s+bir\s+koşum",
    r"exists\s+in\s+the\s+operator's\s+output\s+dir\s+tree",
    # Notices to recipients are Art. 19; Art. 17(2) only where the controller made the data public.
    r"(?:responsibility|sorumluluğudur)\s+\((?:Article|Madde)\s+17\(2\)\)",
    r"17\(2\)\*{0,2}\s+requires\s+the\s+controller\s+to\s+inform\s+downstream",
    r"downstream\s+processors\s+\(Article\s+17\(2\)\)",
    # With FORGELM_AUDIT_SECRET set, purge XORs it into the identifier salt.
    r"stable\s+across\s+env-var\s+rotations?",
    r"rotasyonlarına\s+karşı\s+stabil",
    r"rotates\s+\*{0,2}only\s+the\s+chain\s+HMAC",
    r"yalnızca\s+chain\s+HMAC\s+anahtarını",
    r"salt\s+is\s+the\s+only\s+input\s+to\s+identifier\s+hashing",
    r"identifier\s+hash'lemenin\s+tek\s+girdisi",
    # External copies is row mode only; age_source is audit | mtime; run mode defaults --output-dir to ".".
    r"webhook\s+block\s+\(both\s+modes\)",
    r"audit_genesis",
    r"required\s+in\s+run\s+mode",
    # The 5-year default is not derived from a legal floor.
    r"satisfies\s+the\s+AI\s+Act\s+floor",
    r"covers\s+the\s+GDPR\s+controller-record",
    r"Article\s+12\(2\)\s+sets\s+the\s+floor",
    # --kind artefacts does not find the standard bundle.
    r"bundle\s+filenames\s+embed\s+the\s+run-id",
    r"In-place\s+deletion\s+is\s+supported\s+via",
    r"--kind\s+artefacts`\s+rewrites",
    # reverse-pii digests correlate with purge digests in the default plaintext mode; --salt-source is a different
    # feature (a hash-mask scan) and finds nothing in a corpus that holds plaintext.
    r"only\s+when\s+it\s+uses\s+the\s+same\s+salt\s+source",
    r"use\s+the\s+same\s+salt\s+source",
    r"query\s+with\s+`--salt-source\s+(?:env_var|per_dir)`",
    r"`--salt-source\s+env_var`\s+kullanarak\s+sorgulayın",
    r"not\s+a\s+way\s+to\s+match\s+purge\s+digests",
    r"eşleştirmenin\s+bir\s+yolu\s+değildir",
    # The recorded query_hash does match in the default mode; what --salt-source gets wrong is the corpus scan.
    # Zero matches do not answer an access request.
    r"zero\s+matches[^.\n]{0,20}is\s+a\s+valid\s+answer",
    r"explicitly\s+accepts\s+[\"'“]no\s+records",
    r"sıfır\s+eşleşme\s+geçerli\s+bir\s+cevap",
    # The report-not-gate recipes that go green on a config error or on a directory that was never checked.
    r"jq\s+(?:-e\s+)?'\.violations\s*\|\s*length",
    r"\.success\s+and\s+\(\.violations",
)

# The PII tier table and the regex detector set are not a GDPR-mandated list: Art. 9 covers special categories (not
# national IDs, which Art. 87 addresses), Art. 10 covers criminal-conviction data, and the identifiers are personal
# data because of Art. 4(1).  Nothing in these files has a legitimate reason to cite Art. 9 in that role or to say GDPR
# mandates the detector set, so the guard bans the citation broadly instead of one exact old sentence.  A correct
# caveat ("special categories of personal data are not detected") is phrased without the article number.
_PII_CITATION_FILES = (
    "forgelm/data_audit/_types.py",
    "forgelm/data_audit/_pii_regex.py",
    "forgelm/data_audit/_optional.py",
    "pyproject.toml",
    "docs/guides/data_audit.md",
    "docs/guides/data_audit-tr.md",
    "docs/usermanuals/en/data/pii-ml.md",
    "docs/usermanuals/tr/data/pii-ml.md",
    "docs/usermanuals/en/data/pii-masking.md",
    "docs/usermanuals/tr/data/pii-masking.md",
)

_RETIRED_PII_CITATIONS = (
    r"(?:GDPR|tier|kademe)[^.\n]{0,40}\b(?:Art(?:icle|\.)?|Madde|Md\.)\s*9\b(?![^.\n]{0,60}not\s+detected)",
    r"GDPR[\s‐-―-]*(?:mandated|required)\s+(?:structured\s+)?identifiers",
    r"(?:mandated|required)\s+(?:by|under)\s+(?:the\s+)?GDPR",
    r"regulation[\s‐-―-]+mandated",
    r"(?:Article|Madde)\s+10\b[^.\n]{0,60}(?:cares|concerned|önemsediği)",
    r"consensus\s+regulatory",
    r"konsensüs\s+regülatif",
)

_PURGE_ARTEFACT_WARNING_FILES = (
    "docs/guides/gdpr_erasure.md",
    "docs/guides/gdpr_erasure-tr.md",
    "docs/reference/purge_subcommand.md",
    "docs/reference/purge_subcommand-tr.md",
    "docs/usermanuals/en/compliance/gdpr-erasure.md",
    "docs/usermanuals/tr/compliance/gdpr-erasure.md",
)


def _retired_hits(text: str, patterns: tuple) -> list:
    """Return ``line N: 'matched text'`` for every retired pattern, so a failure says what to reword and where."""
    return [
        f"line {text.count(chr(10), 0, m.start()) + 1}: {m.group(0)!r}"
        for pattern in patterns
        for m in re.finditer(pattern, text, re.IGNORECASE)
    ]


@pytest.mark.parametrize("rel", _GDPR_CLAIM_FILES)
def test_retired_gdpr_claims_stay_out(rel: str) -> None:
    text = (_REPO_ROOT / rel).read_text(encoding="utf-8")
    hits = _retired_hits(text, _RETIRED_GDPR_CLAIMS)
    assert hits == [], f"{rel} still carries wording the code or the law does not support: {hits}"


@pytest.mark.parametrize("rel", _PII_CITATION_FILES)
def test_retired_pii_citations_stay_out(rel: str) -> None:
    text = (_REPO_ROOT / rel).read_text(encoding="utf-8")
    hits = _retired_hits(text, _RETIRED_PII_CITATIONS)
    assert hits == [], f"{rel} attributes the PII detector set or tiers to the wrong GDPR provision: {hits}"


# Positive invariant, so a reworded version of the retired claim cannot slip past the phrase list: every mention of the
# Article 17(3) exceptions the docs rely on, (b) legal obligation and (e) legal claims, in any case and in any of the
# spellings "17(3)(b)", "17(3)b", "17(3), point (b)", "17 paragraph 3 point (b)", "17 para. 3 lit. b" or a bare "(3)(b)"
# after "Article 17(3)" (but not "35(3)(b)" of another article), carries a limiting qualifier in its own sentence.  The
# qualifier list accepts the English and Turkish ways of saying "to the extent" and nothing broader ("only to" is not one:
# it matches any "only to").  One qualifier can still cover both (b) and (e) in the same sentence, as the real text does.
_ART_17_3_EXCEPTION = re.compile(
    r"17\s*\(3\)\s*\([be]\)"
    r"|17\s*\(3\)[be]\b"
    r"|17\s*\(3\)\s*,?\s*(?:point|lit\.?)\s*\(?[be]\)?(?![a-z])"
    r"|17\s*,?\s*(?:paragraph|para\.?)\s*3\s*,?\s*(?:point|lit\.?)\s*\(?[be]\)?(?![a-z])"
    r"|(?<![\d)])\(3\)\s*\([be]\)",
    re.IGNORECASE,
)
_LIMITING_QUALIFIER = re.compile(
    r"to\s+the\s+extent|in\s+so\s+far\s+as|insofar\s+as|(?:as|so)\s+far\s+as|only\s+where|only\s+if"
    r"|provided\s+that|limited\s+to|unless|still\s+requires"
    r"|ölçüde|ölçüsünde|kadarıyla|kadar\s+uygulanır|sürece|hallerde",
    re.IGNORECASE,
)
# A sentence ends at ". " (closing quotes or brackets may follow the full stop, and markdown or comment markers such as
# "**", ">" or "#" may open the next sentence) before a letter, but not after "Art." / "Md." / "e.g." / "i.e." / "cf." /
# "etc." / "resp." / "vs."; it also ends at a blank line, at a new list item or table row, and at a table-cell separator.
_SENTENCE_BREAK = re.compile(
    r"(?<!\bArt\.)(?<!\bMd\.)(?<!\be\.g\.)(?<!\bi\.e\.)(?<!\bcf\.)(?<!\betc\.)(?<!\bresp\.)(?<!\bvs\.)"
    r"(?<=[.!?])[\"'”»)*_]*[\s\u00a0]+(?=[*_>#\"'“”«(\[ \t]*[^\W\d_])"
    r"|\n[ \t]*\n|\n[ \t]*(?:[-*+]|\d{1,3}\.|\|)[ \t]|\|"
)


def unqualified_article_17_3_mentions(text: str) -> list:
    """Return ``line N: sentence`` for each Article 17(3)(b)/(e) mention with no limiting qualifier in its sentence."""
    starts, ends = [0], []
    for m in _SENTENCE_BREAK.finditer(text):
        ends.append(m.start())
        starts.append(m.end())
    ends.append(len(text))
    qualified: dict = {}
    bad = []
    for m in _ART_17_3_EXCEPTION.finditer(text):
        i = bisect.bisect_right(starts, m.start()) - 1
        if i not in qualified:
            qualified[i] = _LIMITING_QUALIFIER.search(text, starts[i], ends[i]) is not None
        if not qualified[i]:
            sentence = text[starts[i] : min(ends[i], starts[i] + 400)]
            bad.append(f"line {text.count(chr(10), 0, m.start()) + 1}: {sentence.strip()[:160]}")
    return bad


# The invariant does not depend on context, so it runs over every text file of the public subtrees (named one by one:
# ``docs/`` itself is never walked) rather than over the hand-picked claim list, and a new GDPR page is covered at once.
_PUBLIC_TEXT_ROOTS = (
    "docs/usermanuals",
    "docs/reference",
    "docs/guides",
    "docs/qms",
    "docs/design",
    "docs/standards",
    "forgelm",
    ".claude",
    ".agents",
)
_PUBLIC_TEXT_FILES = (
    "README.md",
    "CONTRIBUTING.md",
    "CLAUDE.md",
    "AGENTS.md",
    "config_template.yaml",
    "pyproject.toml",
)
_PUBLIC_TEXT_SUFFIXES = {".md", ".py", ".yaml", ".yml", ".toml"}


def _public_text_files() -> list:
    found = [_REPO_ROOT / name for name in _PUBLIC_TEXT_FILES]
    for root in _PUBLIC_TEXT_ROOTS:
        found += [
            p for p in sorted((_REPO_ROOT / root).rglob("*")) if p.is_file() and p.suffix in _PUBLIC_TEXT_SUFFIXES
        ]
    # ``.claude/worktrees/`` holds disposable git worktrees (gitignored), not the project's own text.  The test is on the
    # path relative to the repository root, because the repository may itself be checked out inside such a directory.
    return [
        p for p in found if p.is_file() and not p.relative_to(_REPO_ROOT).as_posix().startswith(".claude/worktrees/")
    ]


def test_every_article_17_3_exception_mention_is_qualified() -> None:
    files = _public_text_files()
    assert len(files) > 150, f"the scan found {len(files)} files: a path filter dropped the public tree"
    failures = {}
    for path in files:
        bad = unqualified_article_17_3_mentions(path.read_text(encoding="utf-8"))
        if bad:
            failures[str(path.relative_to(_REPO_ROOT))] = bad
    assert failures == {}, (
        "an Article 17(3)(b)/(e) mention has no limiting qualifier in its own sentence (EN: 'applies only to the extent "
        "a specific Union or Member State obligation requires the processing'; TR: '... gerektirdiği ölçüde uygulanır'): "
        f"{failures}"
    )


@pytest.mark.parametrize(
    ("text", "expected_bad"),
    [
        ("Article 17(3)(b) applies only to the extent a specific obligation requires it.", 0),
        ("Article 17(3)(b) applies insofar as a specific obligation requires it.", 0),
        ("Article 17(3)(b) can justify keeping entries.  It applies only to the extent an obligation requires it.", 1),
        ("Access is limited only to staff.  Article 17(3)(b) exempts every audit log.", 1),
        ("Article 17(3)(b) exempts every audit log.\n\nAccess is limited only to staff.", 1),
        ("- Art. 17(3)(b) preserves audit records.\n- Retention applies only to the extent the user sets.", 1),
        ("Article 17(3)(b) exempts the audit log from erasure; the warning applies only to row mode.", 1),
        ("Madde 17(3)(b) audit kayıtlarını korur.", 1),
        ("Madde 17(3)(b) yalnızca belirli bir yükümlülüğün gerektirdiği ölçüde uygulanır.", 0),
        ("Madde 17(3)(b) yalnızca belirli bir yükümlülüğün gerektirdiği ölçüsünde uygulanır.", 0),
        ("Madde 17(3)(b) yalnızca gerektirdiği kadarıyla uygulanır.", 0),
        ("Article 17 (3) (b) is a blanket defence.", 1),
        ("Article 17(3)(B) exempts the audit log.", 1),
        ("Article 17(3)(e) preserves the audit log.", 1),
        ("Article 17(3), point (b), preserves the audit log.", 1),
        ("Article 17 paragraph 3 point (b) preserves the audit log.", 1),
        ("Art. 17 para. 3 lit. b preserves the audit log.", 1),
        ("Article 17(3)b preserves the audit log.", 1),
        ("Article 35(3)(b) requires a DPIA for large-scale processing.", 0),
        ("Article 17(3)(b) preserves the audit log.  **It applies only to the extent a law requires it.**", 1),
        ('"Article 17(3)(b) preserves the audit log." It applies only to the extent a law requires it.', 1),
        ("# Article 17(3)(b) exempts every log.\n# It applies only to the extent a law requires it.", 1),
        ("> Article 17(3)(b) exempts every log.\n> It applies only to the extent a law requires it.", 1),
        ("Article 17(3)(b) exempts every log.\u00a0It applies only to the extent a law requires it.", 1),
        ("Article 17(3)(b) applies in so far as a specific law requires the processing.", 0),
        ("Art. 17(3)(b) (cf. recital 65) applies only to the extent a law requires it.", 0),
        ("Art. 17(3)(b), (e), etc. apply only to the extent a law requires it.", 0),
        ("Madde 17(3)(b) yalnızca belirli bir yasanın gerektirdiği kadar uygulanır.", 0),
        ("Including (3)(b) compliance with a legal obligation, which is why the audit log is preserved.", 1),
    ],
)
def test_the_article_17_3_invariant_ties_the_qualifier_to_its_own_sentence(text: str, expected_bad: int) -> None:
    assert len(unqualified_article_17_3_mentions(text)) == expected_bad


@pytest.mark.parametrize("rel", _PURGE_ARTEFACT_WARNING_FILES)
def test_purge_docs_warn_that_artefacts_misses_the_standard_bundle(rel: str) -> None:
    # CR-A10-002 / DOC-D1B05-005: the exporter writes fixed basenames and the
    # resolver matches filenames that embed the run id, so the documented
    # ``--kind artefacts`` finds nothing on a real output directory.  The
    # behaviour is pinned in tests/test_gdpr_erasure.py (which fails, on purpose,
    # once the resolver is fixed); the docs must say so.
    text = (_REPO_ROOT / rel).read_text(encoding="utf-8")
    assert "NoMatchingArtefacts" in text, f"{rel} must warn that --kind artefacts finds nothing"
    assert "compliance_report.json" in text, f"{rel} must name the fixed-basename bundle"
    deletes_nothing = "deletes nothing" if "-tr" not in rel and "/tr/" not in rel else "hiçbir şey silmez"
    assert deletes_nothing in text, f"{rel} must say the failed command deletes nothing ({deletes_nothing!r})"


# The jq recipe operators wire into CI.  The weaker forms went green on a config error (`{"success": false}` has no
# `violations`) and on a directory whose retention block was never loaded (`count` is absent then).
_CI_GATE = """jq -e '.success == true and has("count") and .count == 0'"""
_CI_GATE_FILES = (
    "docs/guides/gdpr_erasure.md",
    "docs/guides/gdpr_erasure-tr.md",
    "docs/reference/purge_subcommand.md",
    "docs/reference/purge_subcommand-tr.md",
    "docs/design/gdpr_erasure.md",
    "forgelm/cli/subcommands/_purge.py",
)


@pytest.mark.parametrize("rel", _CI_GATE_FILES)
def test_the_ci_gate_recipe_is_the_checked_one(rel: str) -> None:
    text = (_REPO_ROOT / rel).read_text(encoding="utf-8")
    assert _CI_GATE in text, f"{rel} must show the CI gate recipe {_CI_GATE!r}"
    assert "pipefail" in text, f"{rel} must say the recipe runs under `set -o pipefail`"


_CORRELATION_RULE_FILES = {
    "docs/reference/purge_subcommand.md": r"byte-identical\s+only\s+when",
    "docs/reference/purge_subcommand-tr.md": r"byte-byte\s+aynıdır",
    "docs/reference/reverse_pii_subcommand.md": r"byte-identical\s+only\s+when",
    "docs/reference/reverse_pii_subcommand-tr.md": r"byte-byte\s+aynıdır",
    "docs/guides/gdpr_erasure.md": r"is\s+not\s+needed\s+for\s+the\s+digests\s+to\s+match",
}


@pytest.mark.parametrize(("rel", "pattern"), sorted(_CORRELATION_RULE_FILES.items()))
def test_the_digest_correlation_rule_is_stated(rel: str, pattern: str) -> None:
    text = (_REPO_ROOT / rel).read_text(encoding="utf-8")
    assert re.search(pattern, re.sub(r"\s+", " ", text)), f"{rel} must state when purge and reverse-pii digests match"


@pytest.mark.parametrize("lang", ["en", "tr"])
class TestGdprPageStaysExecutable:
    """CR-A11-001: the GDPR / KVKK page shows commands an operator can run and says what they find."""

    @staticmethod
    def _page(lang: str) -> str:
        return (_REPO_ROOT / "docs" / "usermanuals" / lang / "compliance" / "gdpr.md").read_text(encoding="utf-8")

    def test_reverse_pii_example_pins_the_output_dir_and_shows_the_real_output_line(self, lang: str) -> None:
        # Without --output-dir the command writes the per-directory salt and an audit event next to the first corpus
        # file.  The documented zero-match line must be what the command prints: the same regex is matched against
        # the real output in tests/test_reverse_pii.py.
        text = self._page(lang)
        assert re.search(
            r"forgelm reverse-pii --query \"ali@example\.com\" --output-dir \./outputs data/\*\.jsonl", text
        ), f"gdpr.md ({lang}) must show the reverse-pii example with --output-dir ./outputs"
        assert re.search(ZERO_MATCH_LINE, text), f"gdpr.md ({lang}) must show the real zero-match output line"

    def test_names_point_to_the_detection_only_pii_ml_layer(self, lang: str) -> None:
        text = self._page(lang)
        assert "forgelm audit --pii-ml" in text and "#/data/pii-ml" in text
        assert "ingestion-pii-ml" in text
        detection_only = "masks nothing" if lang == "en" else "hiçbir şeyi maskelemez"
        assert detection_only in text, f"gdpr.md ({lang}) must say the ML layer detects and masks nothing"

    def test_a_zero_match_scan_is_not_presented_as_the_answer(self, lang: str) -> None:
        text = self._page(lang)
        assert "15(1)(a)-(h)" in text, f"gdpr.md ({lang}) must say an access response needs more than a search result"


# The line `forgelm reverse-pii` prints when nothing matched; kept as one regex so the page and the command share it.
ZERO_MATCH_LINE = r"No matches for identifier hash [0-9a-f]{16}… \(literal, plaintext mode\) in \d+ file\(s\)\."


# ---------------------------------------------------------------------------
# Pages checked against the code they describe (gpu-cost, chat, pii-masking)
# ---------------------------------------------------------------------------
#
# These pages used to describe things the code does not do (an invented `run_complete` event, GGUF loading, a Steuer-ID
# checksum).  The retired list catches the old sentences; the tests after it tie the page to the code, so a new flag,
# price-table entry or detector cannot be added without the page.

_PAGE_FICTIONS = {
    "operations/gpu-cost.md": (
        r"run_complete",
        r"--gpus",
        r"nvidia-smi\s+--query",
        r"model\s+card\s+cites",
        r"rate_per_hour",
        r"within\s+20\s*%",
        r"auto-merges",
        r"7\.15",
    ),
    "deployment/chat.md": (
        r"GGUF\s+file:",
        r"uses\s+llama\.cpp\s+under\s+the\s+hood",
        r"set\s+`seed:`\s+in\s+YAML",
        r"as\s+much\s+as\s+fits\s+in\s+`max_length`",
        r"^forgelm>\s",
    ),
    "data/pii-masking.md": (
        r"nine\s+categories",
        r"Steuer-ID\s*\|\s*Format\s*\+\s*checksum",
        r"row\s+indices\s+and\s+offsets",
        r"\[EMAIL_REDACTED\]`\s+ruins",
    ),
}
_TR_PAGE_FICTIONS = {
    "operations/gpu-cost.md": (
        r"run_complete",
        r"--gpus",
        r"nvidia-smi\s+--query",
        r"rate_per_hour",
        r"%20\s+içindedir",
    ),
    "deployment/chat.md": (r"GGUF\s+dosyası:", r"alta\s+llama\.cpp", r"YAML'da\s+`seed:`", r"^forgelm>\s"),
    "data/pii-masking.md": (r"dokuz\s+kategori", r"Steuer-ID\s*\|\s*Format\s*\+\s*checksum", r"offset'leri\s+listeler"),
}


@pytest.mark.parametrize("lang", ["en", "tr"])
@pytest.mark.parametrize("page", sorted(_PAGE_FICTIONS))
def test_pages_do_not_describe_what_the_code_does_not_do(lang: str, page: str) -> None:
    patterns = _PAGE_FICTIONS[page] if lang == "en" else _TR_PAGE_FICTIONS[page]
    text = (_REPO_ROOT / "docs" / "usermanuals" / lang / page).read_text(encoding="utf-8")
    flags = re.IGNORECASE | re.MULTILINE
    hits = [
        f"line {text.count(chr(10), 0, m.start()) + 1}: {m.group(0)!r}"
        for pattern in patterns
        for m in re.finditer(pattern, text, flags)
    ]
    assert hits == [], f"{lang}/{page} describes something the code does not do: {hits}"


def _chat_parser():
    import argparse

    from forgelm.cli._parser import _add_chat_subcommand

    parser = argparse.ArgumentParser()
    _add_chat_subcommand(parser.add_subparsers())
    return next(iter(parser._subparsers._group_actions[0].choices.values()))


def _chat_option_strings() -> list:
    skipped = {"--help", "--quiet", "--log-level"}
    return sorted(
        opt
        for action in _chat_parser()._actions
        for opt in action.option_strings
        if opt.startswith("--") and opt not in skipped
    )


# Flags the chat page names without them belonging to the chat parser: a planned flag it says does not exist, the
# benchmark command it points to, and the prompts file of the planned chat-compare preview.
_CHAT_PAGE_OTHER_FLAGS = {"--benchmark-only", "--prompts", "--safety"}


def _flag_mentioned(text: str, flag: str) -> bool:
    return re.search(r"(?<![\w-])" + re.escape(flag) + r"(?![\w-])", text) is not None


@pytest.mark.parametrize("lang", ["en", "tr"])
def test_chat_page_documents_every_chat_flag_and_no_other(lang: str) -> None:
    text = (_REPO_ROOT / "docs" / "usermanuals" / lang / "deployment" / "chat.md").read_text(encoding="utf-8")
    flags = _chat_option_strings()
    assert "--backend" in flags and "--adapter" in flags, "the chat parser lost the flags this test expects"
    missing = [flag for flag in flags if not _flag_mentioned(text, flag)]
    assert missing == [], f"{lang}/deployment/chat.md does not document: {missing}"
    invented = sorted(set(re.findall(r"(?<![\w-])--[a-z][a-z0-9-]*", text)) - set(flags) - _CHAT_PAGE_OTHER_FLAGS)
    assert invented == [], f"{lang}/deployment/chat.md names flags the chat parser does not have: {invented}"


@pytest.mark.parametrize("lang", ["en", "tr"])
def test_chat_page_shows_the_parser_defaults(lang: str) -> None:
    text = (_REPO_ROOT / "docs" / "usermanuals" / lang / "deployment" / "chat.md").read_text(encoding="utf-8")
    defaults = {action.dest: action.default for action in _chat_parser()._actions}
    for flag, dest in (("--temperature", "temperature"), ("--max-new-tokens", "max_new_tokens")):
        row = next((line for line in text.splitlines() if line.startswith("|") and f"`{flag}`" in line), "")
        assert f"`{defaults[dest]}`" in row, f"{lang}/deployment/chat.md: the row for {flag} must show {defaults[dest]}"


@pytest.mark.parametrize("lang", ["en", "tr"])
def test_gpu_cost_page_lists_every_priced_gpu_and_cost_source(lang: str) -> None:
    from forgelm.trainer import ForgeTrainer

    text = (_REPO_ROOT / "docs" / "usermanuals" / lang / "operations" / "gpu-cost.md").read_text(encoding="utf-8")
    # Whole names only: "NVIDIA L4" is a prefix of "NVIDIA L40S", so a substring test cannot see it go missing.
    missing = [
        name
        for name in ForgeTrainer._GPU_PRICING
        if re.search(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])", text) is None
    ]
    assert missing == [], f"{lang}/operations/gpu-cost.md omits priced GPUs: {missing}"
    table = text.split("| `cost_source` |", 1)[1].split("\n\n", 1)[0]
    documented = set(re.findall(r"^\| `([a-z_]+)` \|", table, re.MULTILINE))
    assert documented == {"user_config", "auto_detected", "fuzzy_match"}, f"{lang}/operations/gpu-cost.md: {documented}"


@pytest.mark.parametrize("lang", ["en", "tr"])
class TestIngestionPageQuickExampleStaysRunnable:
    """CR-A12-004: the Document Ingestion quick example must be a command that runs and its real output."""

    @staticmethod
    def _page(lang: str) -> str:
        return (_REPO_ROOT / "docs" / "usermanuals" / lang / "data" / "ingestion.md").read_text(encoding="utf-8")

    def _quick_example_tokens(self, lang: str) -> list[str]:
        text = self._page(lang)
        block = next(
            (block for block in re.findall(r"```shell[ \t]*\n(.*?)```", text, re.DOTALL) if "forgelm ingest" in block),
            None,
        )
        assert block is not None, f"ingestion.md ({lang}) must show a shell block with `forgelm ingest`"
        command = re.sub(r"\\\n\s*", " ", block)
        line = next(line for line in command.splitlines() if "forgelm ingest" in line)
        return shlex.split(line.lstrip("$ ").strip())

    def test_chunk_tokens_is_paired_with_a_tokenizer(self, lang: str) -> None:
        # `forgelm ingest --chunk-tokens` refuses to run without `--tokenizer` (exit 1), so the first command a reader
        # copies must carry both.
        tokens = self._quick_example_tokens(lang)
        assert "--chunk-tokens" in tokens, f"ingestion.md ({lang}) must keep the documented --chunk-tokens example"
        assert "--tokenizer" in tokens, f"ingestion.md ({lang}) must pass --tokenizer with --chunk-tokens"
        chunk_tokens_at = tokens.index("--chunk-tokens")
        chunk_value = tokens[chunk_tokens_at + 1] if chunk_tokens_at + 1 < len(tokens) else ""
        assert chunk_value.isdigit() and int(chunk_value) > 0, (
            f"ingestion.md ({lang}) must give --chunk-tokens a positive token count, not just the bare flag"
        )
        tokenizer_at = tokens.index("--tokenizer")
        assert tokenizer_at + 1 < len(tokens) and not tokens[tokenizer_at + 1].startswith("-"), (
            f"ingestion.md ({lang}) must give --tokenizer a model name, not just the bare flag"
        )

    def test_page_shows_the_real_summarize_result_labels(self, lang: str) -> None:
        # The old block showed tick-mark progress lines that `summarize_result` never prints.
        text = self._page(lang)
        assert "Ingestion summary" in text, f"ingestion.md ({lang}) must show the real summarize_result heading"
        assert "Files (in/out)" in text, f"ingestion.md ({lang}) must show the real Files (in/out) line"


@pytest.mark.parametrize("lang", ["en", "tr"])
def test_pii_masking_page_counts_and_validates_the_real_detectors(lang: str) -> None:
    from forgelm.data_audit import PII_TYPES
    from forgelm.data_audit._pii_regex import CHECKSUM_VALIDATED_PII_TYPES

    text = (_REPO_ROOT / "docs" / "usermanuals" / lang / "data" / "pii-masking.md").read_text(encoding="utf-8")
    assert len(PII_TYPES) == 8, "a detector was added or removed: update the pii-masking page (count, table, API list)"
    assert ("eight categories" if lang == "en" else "sekiz kategori") in text
    assert CHECKSUM_VALIDATED_PII_TYPES == {"credit_card", "iban", "tr_id"}, "update the pii-masking validation table"
    kinds_line = next(line for line in text.splitlines() if "`fr_ssn`" in line and "`us_ssn`" in line)
    assert set(re.findall(r"`([a-z_]+)`", kinds_line)) == set(PII_TYPES), (
        f"{lang}/data/pii-masking.md: the listed detector kinds differ from PII_TYPES"
    )
