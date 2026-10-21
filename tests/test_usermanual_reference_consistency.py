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
    r"Article\s+12\s+record-keeping\s+(?:baseline|\))",
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
    r"justified\s+only\s+to\s+the\s+extent",
    r"name\s+the\s+Union\s+or\s+Member\s+State\s+obligation",
    r"compliance/\*\.json`\s+files\s+whose\s+name\s+embeds",
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


@pytest.mark.parametrize("rel", _PURGE_ARTEFACT_WARNING_FILES)
def test_purge_docs_warn_that_artefacts_misses_the_standard_bundle(rel: str) -> None:
    # CR-A10-002 / DOC-D1B05-005: the exporter writes fixed basenames and the
    # resolver matches filenames that embed the run id, so the documented
    # ``--kind artefacts`` finds nothing on a real output directory.  The
    # behaviour is pinned in tests/test_gdpr_erasure.py; the docs must say so.
    text = (_REPO_ROOT / rel).read_text(encoding="utf-8")
    assert "NoMatchingArtefacts" in text, f"{rel} must warn that --kind artefacts finds nothing"
    assert "compliance_report.json" in text, f"{rel} must name the fixed-basename bundle"
