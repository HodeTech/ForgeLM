"""H10 doc-consistency regression tests for docs/usermanuals reference pages.

These lock the corrected user-manual claims to the code/contract so a
future edit re-introducing the drift fails CI:

- F-P7-OPUS-02 — cli.md exit-codes table must not promise exit 130 for
  Ctrl+C (the dispatcher clamps it to EXIT_TRAINING_ERROR == 2).
- F-P7-OPUS-03 — cli.md must disclose that argparse usage errors exit 2,
  while post-parse config/semantic validation exits 1.
- F-P7-OPUS-18 — cli.md must describe ``forgelm reject`` as PRESERVING
  the staging directory, never "discard staging".

No GPU / no network — pure file reads against the shipped docs tree.
"""

from __future__ import annotations

import re
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
    "config_template.yaml",
    "docs/design/gdpr_erasure.md",
    "docs/guides/gdpr_erasure.md",
    "docs/guides/gdpr_erasure-tr.md",
    "docs/reference/configuration.md",
    "docs/reference/configuration-tr.md",
    "docs/reference/purge_subcommand.md",
    "docs/reference/purge_subcommand-tr.md",
    "docs/usermanuals/en/compliance/gdpr-erasure.md",
    "docs/usermanuals/tr/compliance/gdpr-erasure.md",
    "docs/usermanuals/en/reference/configuration.md",
    "docs/usermanuals/tr/reference/configuration.md",
    "docs/usermanuals/en/compliance/human-oversight.md",
    "docs/usermanuals/tr/compliance/human-oversight.md",
    "docs/qms/sop_change_management.md",
    "docs/qms/sop_change_management-tr.md",
    "forgelm/data_audit/_types.py",
    "forgelm/data_audit/_pii_regex.py",
    "forgelm/data_audit/_optional.py",
    "pyproject.toml",
    "docs/guides/data_audit.md",
    "docs/guides/data_audit-tr.md",
    "docs/usermanuals/en/data/pii-ml.md",
    "docs/usermanuals/tr/data/pii-ml.md",
    "docs/usermanuals/en/compliance/gdpr.md",
    "docs/usermanuals/tr/compliance/gdpr.md",
)

_RETIRED_GDPR_CLAIMS = (
    r"17\(3\)\(b\)\s+preserves",
    r"17\(3\)\(b\)\s+(?:exemption|defence)",
    r"17\(3\)\(b\)\s+records",
    r"17\(3\)\(b\) savunması",
    r"17\(3\)\(b\)\s+audit\s*/\s*muhasebe",
    r"17\(3\)\(b\) kayıt",
    r"append-only (?:Article|Madde) 17\(3\)\(b\)",
    r"5\s+years\s+default\s+\(Article\s+5\(1\)\(e\)\)",
    r"varsayılan 5 yıl \(Madde 5\(1\)\(e\)\)",
    r"legal-obligation\s+record\s+under",
    r"compliant\s+out\s+of\s+the\s+box",
    r"years\s+\(Article\s+12\s+record-keeping\)|Article\s+12\s+record-keeping\s+baseline",
    r"Horizons\s+honou?r\s+GDPR",
    r"removed\s+by\s+default\s+after\s+a\s+defined\s+retention\s+horizon",
    r"after\s+a\s+`forgelm\s+reject`\s+decision\s+before\s+scheduled\s+cleanup",
    r"work-week\s+to\s+act\s+on\s+a\s+`forgelm\s+reject`",
    r"Madde\s+12\s+kayıt-tutma",
    r"run's\s+compliance\s+bundle",
    r"staging\s+directory\s+or\s+compliance\s+bundle",
    r"physically\s+prunes",
    r"fiziksel\s+olarak\s+prune",
    r"until\s+retention\s+expires",
    r"retention\s+süresi\s+dolana",
    r"compliance/\*\.json`\s+files\s+whose\s+name\s+embeds",
    r"staging\s+directory,?\s+or\s+(?:the\s+|a\s+)?compliance\s+bundle",
    r"compliance\s+bundle'ı(?:nı)?\s+(?:siler|atomik)",
    r"compliance/\*\.json`\s+dosyalar",
    r"retain\s+staging\s+model\s+after\s+forgelm\s+reject",
    r"forgelm\s+reject\s+sonrası\s+staging",
    r"TTL\s+after\s+`forgelm\s+reject`",
    r"planlı\s+temizlenmeden",
    r"bir\s+iş\s+haftası",
    r"onurlandırır",
    r"17\(3\)\(b\)\s+protects",
    r"17\(3\)\(b\)[^.\n]{0,40}\bkorur",
    # The PII tier table and detector set are not a GDPR-mandated list: Art. 9 covers special categories (not
    # national IDs, which Art. 87 addresses) and Art. 10 covers criminal-conviction data.
    r"GDPR\s+Art\.\s+9\s*\+\s*ENISA",
    r"GDPR\s+Md\.\s+9\s*\+\s*ENISA",
    r"Article\s+10\s+cares",
    r"Madde\s+10'un\s+önemsediği",
    r"GDPR-mandated",
    # The GDPR / KVKK page (CR-A11-001): masking is a fixed regex set, not anonymisation, and no audit report can
    # confirm that a model holds nothing about a person.
    r"identifiable\s+subjects\s+are\s+removed",
    r"no\s+specific\s+person's\s+data\s+is\s+recoverable",
    r"audit\s+report\s+confirms\s+this",
    r"tanımlanabilir\s+özneler\s+kaldırılmıştır",
    r"geri\s+çıkarılamaz",
    r"Audit\s+raporu\s+bunu\s+teyit",
    r"international_transfers",
    r"No\s+matches\s+found\s+in\s+masked",
    r"eşleşme\s+bulunamadı",
    r"auto-delete\s+originals",
    r"records\s+the\s+intended\s+TTL",
    r"SHA-256\s+manifest\s+for\s+tamper",
    r"Source\s+attribution\s+\(which\s+document",
    r"pii_mask:",
)

_PURGE_ARTEFACT_WARNING_FILES = (
    "docs/guides/gdpr_erasure.md",
    "docs/guides/gdpr_erasure-tr.md",
    "docs/reference/purge_subcommand.md",
    "docs/reference/purge_subcommand-tr.md",
    "docs/usermanuals/en/compliance/gdpr-erasure.md",
    "docs/usermanuals/tr/compliance/gdpr-erasure.md",
)


@pytest.mark.parametrize("rel", _GDPR_CLAIM_FILES)
def test_retired_gdpr_claims_stay_out(rel: str) -> None:
    text = (_REPO_ROOT / rel).read_text(encoding="utf-8")
    found = [pattern for pattern in _RETIRED_GDPR_CLAIMS if re.search(pattern, text, re.IGNORECASE)]
    assert found == [], f"{rel} still carries retired GDPR wording: {found}"


# Positive invariant, so a reworded version of the retired claim cannot slip past the phrase list: every mention of
# Article 17(3)(b) must sit next to a qualifier that limits it ("to the extent", "still requires", Turkish "ölçüde").
_CONDITIONAL_17_3_B = re.compile(r"to\s+the\s+extent|only\s+to\s|still\s+requires|ölçüde", re.IGNORECASE)


@pytest.mark.parametrize("rel", _GDPR_CLAIM_FILES)
def test_every_article_17_3_b_mention_is_conditional(rel: str) -> None:
    text = (_REPO_ROOT / rel).read_text(encoding="utf-8")
    unqualified = [
        text[max(0, m.start() - 60) : m.end() + 60]
        for m in re.finditer(r"17\(3\)\(b\)", text)
        if not _CONDITIONAL_17_3_B.search(text[max(0, m.start() - 250) : m.end() + 250])
    ]
    assert unqualified == [], f"{rel}: Article 17(3)(b) mentioned without a limiting qualifier: {unqualified}"


@pytest.mark.parametrize("rel", _PURGE_ARTEFACT_WARNING_FILES)
def test_purge_docs_warn_that_artefacts_misses_the_standard_bundle(rel: str) -> None:
    # CR-A10-002 / DOC-D1B05-005: the exporter writes fixed basenames and the
    # resolver matches filenames that embed the run id, so the documented
    # ``--kind artefacts`` finds nothing on a real output directory.  The
    # behaviour is pinned in tests/test_gdpr_erasure.py; the docs must say so.
    text = (_REPO_ROOT / rel).read_text(encoding="utf-8")
    assert "NoMatchingArtefacts" in text, f"{rel} must warn that --kind artefacts finds nothing"
    assert "compliance_report.json" in text, f"{rel} must name the fixed-basename bundle"


@pytest.mark.parametrize("lang", ["en", "tr"])
class TestGdprPageStaysExecutable:
    """CR-A11-001: the GDPR / KVKK page shows commands an operator can run and says what they find."""

    @staticmethod
    def _page(lang: str) -> str:
        return (_REPO_ROOT / "docs" / "usermanuals" / lang / "compliance" / "gdpr.md").read_text(encoding="utf-8")

    def test_reverse_pii_example_pins_the_output_dir_and_shows_the_real_output_line(self, lang: str) -> None:
        # Without --output-dir the command writes the per-directory salt and an audit event next to the first corpus
        # file; the real zero-match line is "No matches for identifier hash <16 hex>… (literal, plaintext mode) in N file(s)."
        text = self._page(lang)
        assert re.search(
            r"forgelm reverse-pii --query \"ali@example\.com\" --output-dir \./outputs data/\*\.jsonl", text
        )
        assert re.search(
            r"No matches for identifier hash [0-9a-f]{16}… \(literal, plaintext mode\) in \d+ file\(s\)\.", text
        )

    def test_names_point_to_the_detection_only_pii_ml_layer(self, lang: str) -> None:
        text = self._page(lang)
        assert "forgelm audit --pii-ml" in text and "#/data/pii-ml" in text
        assert "ingestion-pii-ml" in text
