"""End-to-end proof that a non-finite value in a YAML file exits 1.

The unit-level half of this — ``ValidationError`` from each sub-config — lives
beside the fields it covers, in ``test_config.py`` and ``test_trainer.py``.
This module covers the other half, and it is not redundant with them for three
reasons that each correspond to a real way the protection could be absent
while those tests stayed green:

1. **YAML 1.1 spells it ``.nan``.**  The threat is not a library caller passing
   ``float("nan")``; it is an operator (or a config generator) writing ``.nan``
   in a file.  That only matters if PyYAML's ``safe_load`` actually produces a
   float NaN for that token — asserted directly below rather than assumed.
2. **Nested validation must propagate.**  A constraint on
   ``MergeInput.weight`` is worth nothing if ``ForgeConfig`` builds
   ``merge.models`` through a path that skips it.  These tests go through the
   real top-level entry point, ``load_config``, not the sub-model constructor.
3. **The exit code is the contract.**  ``error-handling.md`` publishes 1 for a
   config defect, and CI/CD pipelines branch on it.  A ``ValidationError`` that
   reached the operator as exit 2 (a *training* verdict) or as an unhandled
   traceback would satisfy every unit test and still break the contract.  The
   assertion is therefore on ``SystemExit.code`` out of the CLI's real loader.

Two constraints do this work jointly and the split is not obvious, so it is
recorded here: on a ``ge=0.0``-bounded field the range check already rejects
``.nan`` and ``-.inf`` (both comparisons are False), and ``allow_inf_nan=False``
is what rejects ``+inf`` — which ``>= 0.0`` happily accepts.  Mutating either
one alone therefore turns only part of this matrix red.  Both are asserted
because dropping either leaves a live hole.
"""

from __future__ import annotations

import pytest
import yaml

from forgelm.cli._config_load import _load_config_or_exit
from forgelm.cli._exit_codes import EXIT_CONFIG_ERROR

# Each entry: (dotted path, value written into the YAML, why it is refused).
# `value` is written as a raw YAML scalar so the token an operator would
# actually type is the thing under test.
NON_FINITE_MATRIX = [
    ("evaluation.max_acceptable_loss", ".nan", "the loss gate cannot compare against it"),
    ("evaluation.max_acceptable_loss", ".inf", "a ceiling no loss can exceed disarms the gate"),
    ("evaluation.max_acceptable_loss", "-1.0", "a negative loss is not reachable"),
    ("evaluation.baseline_loss", ".nan", "a NaN baseline disarms the regression check"),
    ("evaluation.baseline_loss", "-.inf", "a -inf baseline makes every model a regression"),
]

MERGE_MATRIX = [
    ("weight", ".nan", "SLERP discards the operator's weights on a non-finite sum"),
    ("weight", ".inf", "propagates into every merged tensor"),
    ("weight", "0.0", "a zero weight-sum raised exit 2 at runtime"),
    ("weight", "-1.0", "reaches the TIES renormalisation guard negative"),
]

MERGE_SCALAR_MATRIX = [
    ("ties_trim_fraction", ".nan", "the k-th-smallest threshold is undefined"),
    ("ties_trim_fraction", "1.0", "int(1.0 * n) == n inverts the trim to keep-only-maxima"),
    ("ties_trim_fraction", "-0.1", "a negative k is not a trim"),
    ("dare_drop_rate", ".nan", "a non-finite drop probability"),
    ("dare_drop_rate", "1.5", "not a probability"),
]


def _base_config(tmp_path) -> dict:
    """The smallest config that loads cleanly, so the matrix isolates one field."""
    return {
        "model": {"name_or_path": "org/base"},
        "lora": {"r": 8},
        "training": {"trainer_type": "sft", "output_dir": str(tmp_path / "out")},
        "data": {"dataset_name_or_path": "org/data"},
    }


def _write(tmp_path, config: dict, injected: str = "") -> str:
    """Serialise `config`, appending a raw YAML fragment verbatim.

    ``yaml.safe_dump`` emits ``.nan``/``.inf`` for Python floats, but round-
    tripping through Python would mean the test never proves the *token*
    parses.  Raw text keeps the operator's spelling intact.
    """
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config) + injected, encoding="utf-8")
    return str(path)


def test_yaml_really_produces_non_finite_floats():
    """The premise the whole matrix rests on.

    If PyYAML ever stopped resolving these tokens to floats, every test below
    would pass for the wrong reason — the config would be refused as a *string*
    where a float was expected, which is a different defect with a different
    message, and the numeric guards would be untested while looking covered.
    """
    parsed = yaml.safe_load("a: .nan\nb: .inf\nc: -.inf\n")
    assert all(isinstance(v, float) for v in parsed.values())
    assert parsed["a"] != parsed["a"]  # NaN is the only value unequal to itself
    assert parsed["b"] == float("inf")
    assert parsed["c"] == float("-inf")


class TestNonFiniteConfigExitsOne:
    """A whole-file load, through the CLI's real loader, must exit 1."""

    def _assert_exit_1(self, path, capsys):
        with pytest.raises(SystemExit) as exc_info:
            _load_config_or_exit(path, json_output=False)
        assert exc_info.value.code == EXIT_CONFIG_ERROR, (
            "error-handling.md publishes 1 for a config defect; a pipeline "
            "branching on the exit code must not see a training verdict here"
        )
        capsys.readouterr()  # drain the logged message

    @pytest.mark.parametrize(
        ("dotted", "value", "reason"),
        NON_FINITE_MATRIX,
        ids=[f"{d}={v}" for d, v, _ in NON_FINITE_MATRIX],
    )
    def test_evaluation_thresholds(self, dotted, value, reason, tmp_path, capsys):
        section, field = dotted.split(".")
        self._assert_exit_1(
            _write(tmp_path, _base_config(tmp_path), f"{section}:\n  {field}: {value}\n"),
            capsys,
        )

    @pytest.mark.parametrize(
        ("field", "value", "reason"),
        MERGE_MATRIX,
        ids=[f"models[1].{f}={v}" for f, v, _ in MERGE_MATRIX],
    )
    def test_merge_input_weight(self, field, value, reason, tmp_path, capsys):
        fragment = f"merge:\n  enabled: true\n  models:\n    - path: org/a\n    - path: org/b\n      {field}: {value}\n"
        self._assert_exit_1(_write(tmp_path, _base_config(tmp_path), fragment), capsys)

    @pytest.mark.parametrize(
        ("field", "value", "reason"),
        MERGE_SCALAR_MATRIX,
        ids=[f"merge.{f}={v}" for f, v, _ in MERGE_SCALAR_MATRIX],
    )
    def test_merge_scalars(self, field, value, reason, tmp_path, capsys):
        fragment = f"merge:\n  enabled: true\n  models:\n    - path: org/a\n    - path: org/b\n  {field}: {value}\n"
        self._assert_exit_1(_write(tmp_path, _base_config(tmp_path), fragment), capsys)

    def test_safety_severity_threshold(self, tmp_path, capsys):
        """A dict *value* constraint — the shape Pydantic does not check by default.

        ``Dict[str, float]`` accepts NaN for every value; the bound lives on
        the annotated value type, which is easy to write and easy to omit.
        """
        fragment = "evaluation:\n  safety:\n    enabled: true\n    severity_thresholds:\n      high: .nan\n"
        self._assert_exit_1(_write(tmp_path, _base_config(tmp_path), fragment), capsys)

    def test_merge_entry_with_an_unknown_key(self, tmp_path, capsys):
        """A key that is read, discarded and never mentioned is a silent lie."""
        fragment = "merge:\n  enabled: true\n  models:\n    - path: org/a\n    - path: org/b\n      density: 0.5\n"
        self._assert_exit_1(_write(tmp_path, _base_config(tmp_path), fragment), capsys)


class TestTheMatrixCanStillLoadAGoodConfig:
    """The negative control.

    Every test above asserts a *rejection*, so a schema that refused
    everything — or a loader that exited 1 unconditionally — would make the
    whole module green while breaking every real config.  These assert the
    same files load when the values are ordinary.
    """

    def test_finite_thresholds_load(self, tmp_path):
        fragment = "evaluation:\n  max_acceptable_loss: 2.0\n  baseline_loss: 1.5\n"
        config = _load_config_or_exit(_write(tmp_path, _base_config(tmp_path), fragment), False)
        assert config.evaluation.max_acceptable_loss == 2.0
        assert config.evaluation.baseline_loss == 1.5

    def test_ordinary_merge_block_loads(self, tmp_path):
        fragment = (
            "merge:\n"
            "  enabled: true\n"
            "  models:\n"
            "    - path: org/a\n"
            "      weight: 0.7\n"
            "    - path: org/b\n"
            "      weight: 0.3\n"
            "  ties_trim_fraction: 0.2\n"
            "  dare_drop_rate: 0.5\n"
        )
        config = _load_config_or_exit(_write(tmp_path, _base_config(tmp_path), fragment), False)
        assert [m.weight for m in config.merge.models] == [0.7, 0.3]
        assert config.merge.ties_trim_fraction == 0.2

    def test_ordinary_severity_thresholds_load(self, tmp_path):
        fragment = "evaluation:\n  safety:\n    enabled: true\n    severity_thresholds:\n      high: 0.8\n"
        config = _load_config_or_exit(_write(tmp_path, _base_config(tmp_path), fragment), False)
        assert config.evaluation.safety.severity_thresholds == {"high": 0.8}
