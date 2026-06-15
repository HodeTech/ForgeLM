"""Regression guard for F-P1-FAB-24 (M3).

``training.max_steps``, ``training.early_stopping_patience`` and
``evaluation.benchmark.output_dir`` are real, default-active schema fields
(``max_steps`` caps steps; ``early_stopping_patience`` is on whenever a
validation split exists) that were entirely absent from the canonical config
reference — an operator could not discover them. These tests pin that the
three fields appear in both the EN reference and its TR mirror so the gap
cannot silently reopen.

Scope note: deliberately a *targeted* test for the three named fields, not a
blanket every-``model_fields``-key-must-appear scanner (that broader
schema↔reference parity guard is tracked alongside the TR table rebuild noted
in the finding).
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_EN_REF = _REPO_ROOT / "docs" / "reference" / "configuration.md"
_TR_REF = _REPO_ROOT / "docs" / "reference" / "configuration-tr.md"

# Fields the finding flagged as missing from the reference tables. The
# ``output_dir`` entry is namespaced as ``evaluation.benchmark.output_dir``:
# a bare ``output_dir`` token also matches the unrelated ``training`` /
# ``merging`` / ``pipeline`` rows, so it must be checked in its benchmark
# context, not as a free-floating token.
_REQUIRED_FIELDS = (
    "max_steps",
    "early_stopping_patience",
    "evaluation.benchmark.output_dir",
)


@pytest.mark.parametrize("doc", [_EN_REF, _TR_REF], ids=["en", "tr"])
@pytest.mark.parametrize("field", _REQUIRED_FIELDS)
def test_field_documented_in_reference(doc, field):
    text = doc.read_text(encoding="utf-8")
    if field == "evaluation.benchmark.output_dir":
        # The benchmark table is introduced by an ``evaluation.benchmark``
        # heading; require both that heading and an ``output_dir`` row so an
        # unrelated ``output_dir`` mention cannot satisfy the gate.
        assert "`evaluation.benchmark`" in text and "`output_dir`" in text, f"{field} missing from {doc.name}"
    else:
        assert f"`{field}`" in text, f"{field} missing from {doc.name}"


@pytest.mark.parametrize("doc", [_EN_REF, _TR_REF], ids=["en", "tr"])
def test_num_train_epochs_override_caveat_documented(doc):
    """The epochs row must surface the ``max_steps == -1`` override interplay."""
    text = doc.read_text(encoding="utf-8")
    assert "max_steps == -1" in text, f"epochs override caveat missing from {doc.name}"
