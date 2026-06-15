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
