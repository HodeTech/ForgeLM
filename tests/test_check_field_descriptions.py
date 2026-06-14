"""Phase 16 — AST scanner own test suite.

F-16-01: ``tools/check_field_descriptions.py`` is the load-bearing CI
gate that asserts every Pydantic field carries a ``description=``.  Its
only end-to-end signal today is exit-0 when run against
``forgelm/config.py``.  None of its four recognition branches
(``Field(...)`` RHS, literal-default RHS, bare annotation, ``Annotated[T,
Field(...)]`` annotation, ``model_config`` / private skip) was
independently regression-tested before Wave 2b's final review.

Each test below plants a synthetic mini-module on disk, points the
scanner at it, and asserts the expected pass / flag outcome — so a
future refactor that breaks ``_annotation_has_described_field`` (the
Round-4 addition for the Pydantic v2 ``Annotated`` form) or silently
adds a fifth recognition mode would be caught.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# Load the scanner from its file path without leaking ``tools/`` onto
# ``sys.path`` for the rest of the pytest invocation (which would
# risk shadowing other test fixtures or test-time importers).  The
# canonical no-side-effect dynamic-import pattern is
# ``spec_from_file_location`` → ``module_from_spec`` → register in
# ``sys.modules`` → ``exec_module``.  The sys.modules registration is
# load-bearing: ``@dataclass(frozen=True)`` (used inside the scanner)
# resolves its parent module via ``sys.modules[cls.__module__]`` at
# class-creation time, so a module that is never registered there
# trips an ``AttributeError`` during ``_process_class``.  We scope the
# registration to one specific module name so we can pop it cleanly
# without mass-affecting ``sys.path``.
_TOOLS_DIR = Path(__file__).parent.parent / "tools"
_SCANNER_PATH = _TOOLS_DIR / "check_field_descriptions.py"
_spec = importlib.util.spec_from_file_location("check_field_descriptions", _SCANNER_PATH)
assert _spec is not None and _spec.loader is not None, f"could not load scanner module spec from {_SCANNER_PATH!r}"
check_field_descriptions = importlib.util.module_from_spec(_spec)
sys.modules["check_field_descriptions"] = check_field_descriptions
_spec.loader.exec_module(check_field_descriptions)

scan_file = check_field_descriptions.scan_file
MissingDescription = check_field_descriptions.MissingDescription


def _write(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Recognition branches: pass cases (must NOT flag)
# ---------------------------------------------------------------------------


class TestRecognisedPassingForms:
    def test_canonical_field_call_passes(self, tmp_path: Path) -> None:
        """``r: int = Field(default=8, description="LoRA rank.")`` is the
        canonical form and must not be flagged."""
        src = _write(
            tmp_path / "m.py",
            "from pydantic import BaseModel, Field\n"
            "class M(BaseModel):\n"
            "    r: int = Field(default=8, description='LoRA rank.')\n",
        )
        assert scan_file(str(src)) == []

    def test_pydantic_attribute_field_call_passes(self, tmp_path: Path) -> None:
        """``r: int = pydantic.Field(...)`` (attribute access) is also
        the canonical form."""
        src = _write(
            tmp_path / "m.py",
            "import pydantic\n"
            "class M(pydantic.BaseModel):\n"
            "    r: int = pydantic.Field(default=8, description='LoRA rank.')\n",
        )
        assert scan_file(str(src)) == []

    def test_annotated_field_passes(self, tmp_path: Path) -> None:
        """Pydantic v2 ``Annotated[T, Field(..., description=...)]`` form;
        Round-4 absorption added the recognition branch."""
        src = _write(
            tmp_path / "m.py",
            "from typing import Annotated\n"
            "from pydantic import BaseModel, Field\n"
            "class M(BaseModel):\n"
            "    r: Annotated[int, Field(default=8, description='LoRA rank.')]\n",
        )
        assert scan_file(str(src)) == []

    def test_annotated_typing_attribute_form_passes(self, tmp_path: Path) -> None:
        """``typing.Annotated[T, Field(..., description=...)]`` (attribute
        access on the Annotated marker) — same recognition branch."""
        src = _write(
            tmp_path / "m.py",
            "import typing\n"
            "from pydantic import BaseModel, Field\n"
            "class M(BaseModel):\n"
            "    r: typing.Annotated[int, Field(default=8, description='LoRA rank.')]\n",
        )
        assert scan_file(str(src)) == []

    def test_model_config_skipped(self, tmp_path: Path) -> None:
        """``model_config = ConfigDict(...)`` is Pydantic machinery, not
        a config knob — must be skipped (not flagged)."""
        src = _write(
            tmp_path / "m.py",
            "from pydantic import BaseModel, ConfigDict, Field\n"
            "class M(BaseModel):\n"
            "    model_config = ConfigDict(extra='forbid')\n"
            "    r: int = Field(default=8, description='LoRA rank.')\n",
        )
        assert scan_file(str(src)) == []

    def test_private_attribute_skipped(self, tmp_path: Path) -> None:
        """``_internal: int`` is a private attr — must be skipped."""
        src = _write(
            tmp_path / "m.py",
            "from pydantic import BaseModel, Field\n"
            "class M(BaseModel):\n"
            "    _internal: int = Field(default=0)\n"  # No description: would flag if not skipped.
            "    r: int = Field(default=8, description='LoRA rank.')\n",
        )
        assert scan_file(str(src)) == []


# ---------------------------------------------------------------------------
# Recognition branches: fail cases (must flag)
# ---------------------------------------------------------------------------


class TestRecognisedFailingForms:
    def test_field_call_without_description_flagged(self, tmp_path: Path) -> None:
        src = _write(
            tmp_path / "m.py",
            "from pydantic import BaseModel, Field\nclass M(BaseModel):\n    r: int = Field(default=8)\n",
        )
        missing = scan_file(str(src))
        assert len(missing) == 1
        assert missing[0].class_name == "M"
        assert missing[0].field_name == "r"

    def test_literal_default_flagged(self, tmp_path: Path) -> None:
        """``r: int = 8`` — Pydantic accepts the bare default but the
        scanner must flag it (no description by construction)."""
        src = _write(
            tmp_path / "m.py",
            "from pydantic import BaseModel\nclass M(BaseModel):\n    r: int = 8\n",
        )
        missing = scan_file(str(src))
        assert len(missing) == 1
        assert missing[0].field_name == "r"

    def test_bare_annotation_flagged(self, tmp_path: Path) -> None:
        """``r: int`` (no default) — must be flagged so the operator
        reading the docs sees the type-only field."""
        src = _write(
            tmp_path / "m.py",
            "from pydantic import BaseModel\nclass M(BaseModel):\n    r: int\n",
        )
        missing = scan_file(str(src))
        assert len(missing) == 1
        assert missing[0].field_name == "r"

    def test_annotated_without_field_call_flagged(self, tmp_path: Path) -> None:
        """``Annotated[int, "metadata"]`` (no Field call inside) — the
        annotation has no description, so the field must be flagged."""
        src = _write(
            tmp_path / "m.py",
            "from typing import Annotated\n"
            "from pydantic import BaseModel\n"
            "class M(BaseModel):\n"
            "    r: Annotated[int, 'string-metadata-not-Field']\n",
        )
        missing = scan_file(str(src))
        assert len(missing) == 1
        assert missing[0].field_name == "r"

    def test_annotated_field_without_description_flagged(self, tmp_path: Path) -> None:
        """``Annotated[int, Field(default=8)]`` — Field is present in
        the annotation but no ``description=``; the scanner's
        Annotated-branch must distinguish."""
        src = _write(
            tmp_path / "m.py",
            "from typing import Annotated\n"
            "from pydantic import BaseModel, Field\n"
            "class M(BaseModel):\n"
            "    r: Annotated[int, Field(default=8)]\n",
        )
        missing = scan_file(str(src))
        # ``_annotation_has_described_field`` walks the Annotated args
        # AND inspects ``description=`` per element via
        # ``_has_description_kwarg`` (see
        # ``tools/check_field_descriptions.py:114``), so a bare
        # Field-in-Annotated without ``description=`` IS flagged — the
        # assertion below pins that contract.  If a future refactor
        # changes the recursion strategy (e.g. starts walking into
        # nested kwargs of a Field call within Annotated), tighten or
        # relax this assertion accordingly so it keeps reflecting the
        # shipped behaviour.
        assert len(missing) == 1, f"Annotated[T, Field(...)] without description must be flagged; got {missing}"


# ---------------------------------------------------------------------------
# Multi-class + non-Pydantic discrimination
# ---------------------------------------------------------------------------


class TestPydanticClassDetection:
    def test_non_basemodel_class_ignored(self, tmp_path: Path) -> None:
        """A class that does not inherit from ``BaseModel`` must be
        ignored even if it has type-annotated fields without
        descriptions."""
        src = _write(
            tmp_path / "m.py",
            "class Plain:\n"
            "    r: int\n"  # Plain class — must NOT be scanned.
            "    name: str = 'x'\n",
        )
        assert scan_file(str(src)) == []

    def test_multiple_classes_each_scanned(self, tmp_path: Path) -> None:
        """Each Pydantic class in the file is scanned independently."""
        src = _write(
            tmp_path / "m.py",
            "from pydantic import BaseModel, Field\n"
            "class A(BaseModel):\n"
            "    r: int = Field(default=8, description='ok')\n"
            "class B(BaseModel):\n"
            "    s: int = 8\n"  # missing
            "class C(BaseModel):\n"
            "    t: int = Field(default=8)\n",  # missing
        )
        missing = scan_file(str(src))
        flagged_classes = {m.class_name for m in missing}
        assert flagged_classes == {"B", "C"}, f"expected only B and C to be flagged, got {flagged_classes}"

    def test_indirect_inheritance_scanned(self, tmp_path: Path) -> None:
        """F-P1-FAB-29: a class inheriting BaseModel through an
        intermediate base must still be scanned.  Previously the
        per-class check matched only a *direct* BaseModel base, so a
        shared-base refactor would silently exempt every migrated model
        from the description guard while CI stayed green."""
        src = _write(
            tmp_path / "m.py",
            "from pydantic import BaseModel, Field\n"
            "class ForgeBase(BaseModel):\n"
            "    model_config = {'extra': 'forbid'}\n"
            "class LeafConfig(ForgeBase):\n"
            "    undocumented: int = Field(default=8)\n"  # missing — must be flagged
            "    documented: int = Field(default=1, description='ok')\n",
        )
        missing = scan_file(str(src))
        flagged = {(m.class_name, m.field_name) for m in missing}
        assert flagged == {("LeafConfig", "undocumented")}, f"indirect-inheritance field not flagged; got {flagged}"

    def test_two_level_indirect_inheritance_scanned(self, tmp_path: Path) -> None:
        """Transitivity holds across more than one intermediate base."""
        src = _write(
            tmp_path / "m.py",
            "from pydantic import BaseModel, Field\n"
            "class L0(BaseModel):\n"
            "    a: int = Field(default=1, description='ok')\n"
            "class L1(L0):\n"
            "    b: int = Field(default=2, description='ok')\n"
            "class L2(L1):\n"
            "    c: int = Field(default=3)\n",  # missing — must be flagged
        )
        missing = scan_file(str(src))
        flagged = {(m.class_name, m.field_name) for m in missing}
        assert flagged == {("L2", "c")}, f"two-level indirect field not flagged; got {flagged}"


# ---------------------------------------------------------------------------
# CLI surface (--strict mode)
# ---------------------------------------------------------------------------


class TestCliStrictMode:
    def test_strict_exit_code_one_when_missing(self, tmp_path: Path) -> None:
        src = _write(
            tmp_path / "m.py",
            "from pydantic import BaseModel\nclass M(BaseModel):\n    r: int = 8\n",
        )
        rc = check_field_descriptions.main(["--strict", str(src)])
        assert rc == 1

    def test_strict_exit_code_zero_when_clean(self, tmp_path: Path) -> None:
        src = _write(
            tmp_path / "m.py",
            "from pydantic import BaseModel, Field\n"
            "class M(BaseModel):\n"
            "    r: int = Field(default=8, description='ok')\n",
        )
        rc = check_field_descriptions.main(["--strict", str(src)])
        assert rc == 0

    def test_non_strict_exit_code_zero_even_when_missing(self, tmp_path: Path) -> None:
        """Without ``--strict``, the scanner reports but exits 0
        (advisory mode)."""
        src = _write(
            tmp_path / "m.py",
            "from pydantic import BaseModel\nclass M(BaseModel):\n    r: int = 8\n",
        )
        rc = check_field_descriptions.main([str(src)])
        assert rc == 0


# ---------------------------------------------------------------------------
# Real-file canonical: forgelm/config.py exits clean
# ---------------------------------------------------------------------------


class TestCanonicalFile:
    def test_forgelm_config_passes_strict_scanner(self) -> None:
        """The canonical file must always exit 0 under ``--strict`` —
        Phase 16 guarantee.  This is the test that fails first if any
        new field skips the description= migration."""
        config_path = Path(__file__).parent.parent / "forgelm" / "config.py"
        rc = check_field_descriptions.main(["--strict", str(config_path)])
        assert rc == 0, "forgelm/config.py must pass --strict; some field is missing description="
