"""Regression tests for the lazy-torch import contract.

`import forgelm.trainer` and `import forgelm.model` must NOT eagerly load
``torch`` (or its transitive deps via ``peft`` / ``transformers``). Eager
torch import costs ~3-5s of CLI startup per invocation, which dominates
``forgelm --help``, ``forgelm wizard``, and unit-test collection time.

A subprocess is used (rather than ``importlib.reload`` + ``sys.modules``
manipulation) because mutating the parent process's module table is fragile
across pytest runs and CI workers.

See closure-plan F-performance-101.
"""

import subprocess
import sys


def test_trainer_does_not_eagerly_load_torch():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import forgelm.trainer; import sys; sys.exit(0 if 'torch' not in sys.modules else 1)",
        ],
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, f"forgelm.trainer eagerly loaded torch: {result.stderr.decode()}"


def test_model_does_not_eagerly_load_torch():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import forgelm.model; import sys; sys.exit(0 if 'torch' not in sys.modules else 1)",
        ],
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, f"forgelm.model eagerly loaded torch: {result.stderr.decode()}"


# ---------------------------------------------------------------------------
# Wave 2-8 corpus extension (PR #29 master review F-PR29-A1-01).
#
# The original two pins (trainer / model) left a gap that let
# ``forgelm.data`` regress to top-level ``from datasets import …`` and
# ``from transformers import PreTrainedTokenizer``. The block below pins the
# remaining "should be lazy" modules so any future regression — anywhere a
# refactor accidentally hoists a heavy import to module scope — fails CI
# loudly instead of quietly slowing every CLI invocation by 3-5 s.
#
# Heavy modules pinned: ``torch``, ``transformers``, ``trl``. ``datasets`` is
# included for the data-pipeline modules where it is the primary regression
# vector. ``peft`` is intentionally NOT pinned here — it is exercised by the
# ``forgelm.model`` test above and ships its own torch-eager surface that is
# not relevant to these lighter modules.
# ---------------------------------------------------------------------------


def _assert_no_eager_heavy_imports(module: str, heavy: tuple[str, ...]) -> None:
    """Run ``import <module>`` in a fresh interpreter and assert no heavy dep leaked.

    A subprocess is used (rather than ``importlib.reload``) so that pytest
    workers' already-warm ``sys.modules`` cannot mask a regression.
    """
    forbidden = ", ".join(repr(h) for h in heavy)
    code = (
        f"import {module}; import sys; "
        f"leaked = [m for m in ({forbidden},) if m in sys.modules]; "
        "sys.exit(0 if not leaked else 1)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, f"{module} eagerly loaded one of {heavy}: stderr={result.stderr.decode()!r}"


def test_data_does_not_eagerly_load_heavy_deps():
    """``forgelm.data`` must not pull torch / transformers / datasets at import.

    Regression guard for F-PR29-A1-01 — top-level ``from datasets import …``
    and ``from transformers import PreTrainedTokenizer`` were hoisting a
    full torch dependency tree into every CLI invocation.
    """
    _assert_no_eager_heavy_imports("forgelm.data", ("torch", "transformers", "trl", "datasets"))


def test_benchmark_does_not_eagerly_load_heavy_deps():
    """``forgelm.benchmark`` is a thin orchestrator around ``lm-eval``; the
    runtime libraries should only be imported when a benchmark is actually
    executed, not when the module is imported (e.g. for ``forgelm --help``).
    """
    _assert_no_eager_heavy_imports("forgelm.benchmark", ("torch", "transformers", "trl"))


def test_safety_does_not_eagerly_load_heavy_deps():
    """``forgelm.safety`` defers Llama-Guard model loading until evaluation
    runs — importing the module to read constants / dataclasses must not
    drag torch + transformers into a help-text invocation.
    """
    _assert_no_eager_heavy_imports("forgelm.safety", ("torch", "transformers", "trl"))


def test_inference_does_not_eagerly_load_heavy_deps():
    """``forgelm.inference`` exposes ``load_model`` / generation helpers; the
    heavy deps must only be touched inside those callables, never at import
    time. Probed in a fresh subprocess to confirm.
    """
    _assert_no_eager_heavy_imports("forgelm.inference", ("torch", "transformers", "trl"))


def test_synthetic_does_not_eagerly_load_heavy_deps():
    """``forgelm.synthetic`` should be import-cheap so synthetic-data CLI
    surfaces (``forgelm synthetic --help``) start instantly.
    """
    _assert_no_eager_heavy_imports("forgelm.synthetic", ("torch", "transformers", "trl"))


def test_judge_does_not_eagerly_load_heavy_deps():
    """``forgelm.judge`` orchestrates LLM-as-judge calls; like the other
    evaluation modules, the heavy deps must be imported lazily inside the
    judge functions, not at module load.
    """
    _assert_no_eager_heavy_imports("forgelm.judge", ("torch", "transformers", "trl"))


def test_cli_does_not_eagerly_load_ingestion():
    """``import forgelm.cli`` must NOT pull ``forgelm.ingestion`` into ``sys.modules``.

    Regression guard for F-PR29-A3-08 — ``cli/subcommands/_ingest.py`` used to
    do a top-level ``from ...ingestion import OptionalDependencyError`` so the
    exception class was bindable in the dispatcher's ``except`` clause. Because
    ``cli/__init__.py`` re-exports ``_run_ingest_cmd``, every ``forgelm`` CLI
    invocation (including ``forgelm --help``) was eagerly importing the
    ingestion package — inconsistent with the lazy-import contract observed by
    the sibling ``_chat`` / ``_export`` / ``_deploy`` subcommands. The fix
    moved the import inside ``_run_ingest_cmd``; this test pins it.
    """
    code = "import forgelm.cli; import sys; sys.exit(0 if 'forgelm.ingestion' not in sys.modules else 1)"
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, f"forgelm.cli eagerly loaded forgelm.ingestion: stderr={result.stderr.decode()!r}"


def test_stable_verify_symbols_resolve_without_importing_the_cli():
    """``forgelm.verify_integrity`` and friends are **stable-tier** library
    symbols, so resolving one must not drag the CLI layer into a library
    consumer's import graph.

    This is the concrete payoff of moving the three verifiers out of
    ``forgelm/cli/subcommands/_verify_*.py`` into ``forgelm/verify.py``:
    before the move, every one of these attribute accesses imported
    ``forgelm.cli`` (and therefore argparse wiring plus ~20 sibling
    subcommand modules) as a side effect.
    """
    code = (
        "import forgelm; "
        "forgelm.verify_integrity; forgelm.verify_gguf; forgelm.verify_annex_iv_artifact; "
        "forgelm.VerifyIntegrityResult; forgelm.VerifyGgufResult; forgelm.VerifyAnnexIVResult; "
        "import sys; "
        "leaked = [m for m in sys.modules if m == 'forgelm.cli' or m.startswith('forgelm.cli.')]; "
        "print(leaked); "
        "sys.exit(0 if not leaked else 1)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        "resolving a stable verify-* symbol pulled the CLI package in: "
        f"stdout={result.stdout.decode()!r} stderr={result.stderr.decode()!r}"
    )
