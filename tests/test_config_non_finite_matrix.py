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

import inspect
import math
import typing

import pytest
import yaml
from pydantic import BaseModel, ValidationError

import forgelm.config as config_module
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


# Fields in sections the base config already declares (``training``) or that take a
# mapping, so they cannot ride the append-a-fragment path above without a duplicate
# YAML key silently replacing the base section.  ``value`` is substituted verbatim.
OTHER_FIELD_MATRIX = [
    ("training.learning_rate", ".inf", "+inf passes `gt=0` and reaches the optimiser"),
    ("training.weight_decay", ".inf", "+inf passes `ge=0`"),
    ("training.dpo_beta", ".inf", "+inf passes `gt=0`"),
    ("training.galore_scale", ".inf", "+inf passes `gt=0`"),
    ("training.gpu_cost_per_hour", ".inf", "+inf passes `ge=0` and poisons the cost report"),
    ("training.neftune_noise_alpha", ".nan", "an unbounded field takes NaN straight into TrainingArguments"),
    ("training.neftune_noise_alpha", ".inf", "an unbounded field takes inf"),
    ("training.rope_scaling", "{type: linear, factor: .nan}", "`nan <= 0` is False, so it passed the sign check"),
    ("training.rope_scaling", "{type: linear, factor: .inf}", "`inf <= 0` is False, so it passed the sign check"),
    ("training.rope_scaling", "{type: longrope, short_factor: [1.0, .nan], long_factor: [1.0]}", "list entries"),
    ("evaluation.benchmark.limit", "-1", "a negative sample cap is not a cap"),
    ("evaluation.benchmark.num_fewshot", "-1", "a negative few-shot count"),
]

_RAW = "RAW-YAML-PLACEHOLDER"


def _write_raw(tmp_path, dotted: str, raw: str) -> str:
    """Write the base config with ``dotted`` set to the raw YAML text ``raw``.

    The value goes in as a placeholder and is replaced in the serialised text, so
    the operator's spelling (``.inf``, a flow mapping) is what the loader sees.
    """
    *parents, field = dotted.split(".")
    config = _base_config(tmp_path)
    node = config
    for key in parents:
        node = node.setdefault(key, {})
    node[field] = _RAW
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config).replace(_RAW, raw), encoding="utf-8")
    return str(path)


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

    @pytest.mark.parametrize(
        ("dotted", "value", "reason"),
        OTHER_FIELD_MATRIX,
        ids=[f"{d}={v}" for d, v, _ in OTHER_FIELD_MATRIX],
    )
    def test_numeric_fields_outside_the_original_matrix(self, dotted, value, reason, tmp_path, capsys):
        self._assert_exit_1(_write_raw(tmp_path, dotted, value), capsys)

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

    @pytest.mark.parametrize(
        ("dotted", "value"),
        [
            ("training.learning_rate", "0.0003"),
            ("training.neftune_noise_alpha", "5.0"),
            ("training.rope_scaling", "{type: linear, factor: 4.0}"),
            ("training.rope_scaling", "{type: longrope, short_factor: [1.0, 1.5], long_factor: [2.0]}"),
            ("evaluation.benchmark.limit", "10"),
            ("evaluation.benchmark.num_fewshot", "0"),
        ],
    )
    def test_ordinary_values_of_the_other_numeric_fields_load(self, dotted, value, tmp_path):
        """The negative control for ``OTHER_FIELD_MATRIX``: same keys, ordinary values."""
        _load_config_or_exit(_write_raw(tmp_path, dotted, value), False)

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


def _config_models():
    return [
        cls
        for _, cls in inspect.getmembers(config_module, inspect.isclass)
        if issubclass(cls, BaseModel) and cls.__module__ == config_module.__name__
    ]


def _unwrap_optional(annotation):
    """``Optional[Annotated[float, ...]]`` -> ``float``: pydantic only strips the outer ``Annotated``."""
    if typing.get_origin(annotation) is typing.Annotated:
        return _unwrap_optional(typing.get_args(annotation)[0])
    if typing.get_origin(annotation) is typing.Union:
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return _unwrap_optional(args[0])
    return annotation


def _mentions_float(annotation) -> bool:
    return annotation is float or any(_mentions_float(a) for a in typing.get_args(annotation))


# Float-bearing fields that are containers rather than scalars; each is closed by a
# dedicated validator and covered above (`mix_ratio`: `_validate_mix_ratio`;
# `severity_thresholds`: a bounded `Annotated` value type, `test_safety_severity_threshold`).
# A new container-of-float field must be added here deliberately, with its own test.
CONTAINER_FLOAT_FIELDS = {"DataConfig.mix_ratio", "SafetyConfig.severity_thresholds"}


class TestEveryFloatFieldIsDerivedFromTheSchema:
    """The matrix above lists fields by hand; this one cannot go stale.

    The hand-written rows covered exactly the fields a fix had already touched,
    which is how ``learning_rate``, ``neftune_noise_alpha`` and the ``*_beta``
    family kept accepting ``.inf`` behind a changelog line saying non-finite
    configs exit 1. A float field added tomorrow without the guard fails here.
    """

    @staticmethod
    def _scalar_float_fields():
        for cls in _config_models():
            for name, info in cls.model_fields.items():
                if _unwrap_optional(info.annotation) is float:
                    yield cls, name

    def test_the_walk_finds_the_fields_it_is_meant_to_police(self):
        """Guards against a vacuous pass if the introspection ever returns nothing."""
        found = {f"{cls.__name__}.{name}" for cls, name in self._scalar_float_fields()}
        assert {"TrainingConfig.learning_rate", "TrainingConfig.neftune_noise_alpha"} <= found
        assert len(found) >= 20

    @pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf], ids=["nan", "inf", "-inf"])
    def test_every_scalar_float_field_refuses_it(self, bad):
        accepted = []
        for cls, name in self._scalar_float_fields():
            try:
                cls.__pydantic_validator__.validate_assignment(cls.model_construct(), name, bad)
            except ValidationError as exc:
                # Rejected *for this field*, not by some unrelated model validator.
                if any(name in err["loc"] for err in exc.errors()):
                    continue
            accepted.append(f"{cls.__name__}.{name}")
        assert not accepted, f"{bad!r} is accepted by {accepted}; type them as forgelm.config.FiniteFloat"

    def test_container_float_fields_are_the_known_ones(self):
        found = {
            f"{cls.__name__}.{name}"
            for cls in _config_models()
            for name, info in cls.model_fields.items()
            if _mentions_float(info.annotation) and _unwrap_optional(info.annotation) is not float
        }
        assert found == CONTAINER_FLOAT_FIELDS
