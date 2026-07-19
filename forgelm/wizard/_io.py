"""I/O primitives + navigation tokens for the CLI wizard.

Every interactive prompt funnels through one of the helpers here so
navigation tokens (``back`` / ``reset`` / ``cancel``) get a chance to
fire before validation runs.  ``back``/``reset`` raise
:class:`WizardBack`/:class:`WizardReset`; the cancel sentinels raise
:class:`WizardCancel`, which the orchestrator converts into a clean
``EXIT_WIZARD_CANCELLED`` outcome.  The BYOD prelude catches
``WizardCancel`` locally and interprets it as "fall back to the full
wizard" — the same operator-typed ``cancel`` it has always honoured —
so the welcome banner's "type `cancel` / `q` to exit cleanly" promise
holds at every prompt (F-P7-OPUS-05/08).

The ``_print`` indirection mirrors :mod:`forgelm.chat` so wizard
output can be captured via ``capsys`` in tests and re-routed through
``rich`` or a structured logger later without touching every call
site.
"""

from __future__ import annotations

import re
import sys
from typing import Any, Dict, List, Tuple

# Best-effort readline integration — when present, gives the operator
# arrow-key line editing + history recall (Up arrow to repeat the
# previous answer, e.g. when typing the same long HF Hub ID after a
# ``back``).  ``readline`` is part of CPython's stdlib on Linux and
# macOS but absent on Windows; the import is wrapped so the wizard
# stays portable either way.  The single import side-effects glob
# completion onto ``input()`` automatically — no further wiring needed
# at the prompt sites.
try:
    import readline as _readline  # noqa: F401  -- side-effect import
except ImportError:  # pragma: no cover — Windows / minimal environments
    _readline = None

# ---------------------------------------------------------------------------
# Output indirection
# ---------------------------------------------------------------------------


def _print(*args, **kwargs) -> None:
    """Indirection over the builtin :func:`print`."""
    # NOTE: bypass the wizard's own ``print(`` → ``_print(`` rewrite by
    # going through ``builtins.print`` — a bare ``print(...)`` here would
    # be picked up by future codemods and turned into infinite recursion.
    import builtins as _builtins

    _builtins.print(*args, **kwargs)


# ---------------------------------------------------------------------------
# Navigation tokens — operator-typed sentinels that the step machine
# turns into back / reset transitions.  Keep the discipline centralised
# so individual ``_collect_*`` helpers share one source of truth.
# ---------------------------------------------------------------------------


class WizardBack(Exception):
    """Operator typed ``back`` / ``b`` — caller catches and re-renders the previous step."""


class WizardReset(Exception):
    """Operator typed ``reset`` / ``r`` — caller catches and clears state."""


class WizardCancel(Exception):
    """Operator typed ``cancel`` / ``q`` / ``c`` / ``quit`` — caller exits cleanly.

    The step orchestrator converts this into a
    ``WizardOutcome(config_path=None)`` (→ ``EXIT_WIZARD_CANCELLED``); the
    BYOD prelude catches it locally and falls back to the full wizard.
    """


_BACK_TOKENS: Tuple[str, ...] = ("back", "b")
_RESET_TOKENS: Tuple[str, ...] = ("reset", "r")
# Cancel sentinels now auto-raise :class:`WizardCancel` from every
# primitive prompt so the welcome banner's "type `cancel` / `q` to exit
# cleanly" promise holds at every step (F-P7-OPUS-05).  The BYOD prelude
# catches ``WizardCancel`` to preserve its long-standing "fall back to
# the full wizard" semantics.  Kept as a tuple for the membership check.
_CANCEL_TOKENS: Tuple[str, ...] = ("cancel", "c", "q", "quit")


def _check_navigation_token(value: str) -> None:
    """Raise the matching navigation exception when *value* is a sentinel.

    ``back``/``reset`` raise :class:`WizardBack`/:class:`WizardReset`;
    the cancel sentinels (``cancel``/``c``/``q``/``quit``) raise
    :class:`WizardCancel`.  Token matching is case-insensitive and
    ignores surrounding whitespace.  The empty string is not a
    sentinel — the caller should treat that as "no answer" and apply
    its default.
    """
    if not value:
        return
    lowered = value.strip().lower()
    if lowered in _BACK_TOKENS:
        raise WizardBack
    if lowered in _RESET_TOKENS:
        raise WizardReset
    if lowered in _CANCEL_TOKENS:
        raise WizardCancel


# ---------------------------------------------------------------------------
# Primitive prompts — every interactive call funnels through one of
# these so navigation tokens get a chance to fire before validation
# runs.
# ---------------------------------------------------------------------------


def _prompt(question: str, default: str = "") -> str:
    """Prompt the user with a default value."""
    suffix = f" [{default}]" if default else ""
    answer = input(f"  {question}{suffix}: ").strip()  # pragma: no cover  -- stdin read
    _check_navigation_token(answer)
    return answer if answer else default


def _prompt_choice(question: str, options: List[str], default: int = 1) -> str:
    """Prompt the user to pick from numbered options.

    Returns the literal string of the selected option (caller may
    extract a token via ``.split(" ")[0]`` when the option is
    decorated with a description).  Out-of-range or non-numeric
    answers fall back to *default* — preserving the v0.5.5 behaviour
    ``test_wizard_byod`` and friends pin.
    """
    _print(f"\n  {question}")
    for i, opt in enumerate(options, 1):
        marker = " *" if i == default else ""
        _print(f"    {i}) {opt}{marker}")
    choice = input(f"  Choice [{default}]: ").strip()  # pragma: no cover  -- stdin read
    _check_navigation_token(choice)
    try:
        idx = int(choice) if choice else default
        if idx < 1 or idx > len(options):
            return options[default - 1]
        return options[idx - 1]
    except (ValueError, IndexError):
        return options[default - 1]


def _prompt_yes_no(question: str, default: bool = True) -> bool:
    """Prompt for a yes / no answer."""
    hint = "Y/n" if default else "y/N"
    answer = input(f"  {question} [{hint}]: ").strip().lower()  # pragma: no cover  -- stdin read
    _check_navigation_token(answer)
    if not answer:
        return default
    return answer in ("y", "yes")


def _prompt_int(question: str, default: int, min_val: int = 1, max_val: int = 65536) -> int:
    """Prompt for an integer, re-asking until valid."""
    while True:
        raw = _prompt(question, str(default))
        try:
            val = int(raw)
            if min_val <= val <= max_val:
                return val
            _print(f"    Value must be between {min_val} and {max_val}.")
        except ValueError:
            _print("    Please enter a valid integer.")


def _prompt_float(question: str, default: float, min_val: float = 0.0, max_val: float = 1.0e9) -> float:
    """Prompt for a float, re-asking until valid."""
    while True:
        raw = _prompt(question, repr(default))
        try:
            val = float(raw)
            if min_val <= val <= max_val:
                return val
            _print(f"    Value must be between {min_val} and {max_val}.")
        except ValueError:
            _print("    Please enter a valid number.")


def _prompt_required(question: str) -> str:
    """Prompt for a non-empty value, re-asking until provided."""
    while True:
        answer = _prompt(question, "")
        if answer.strip():
            return answer.strip()
        _print("    A value is required.")


def _prompt_optional_list(question: str, *, default_csv: str = "") -> List[str]:
    """Prompt for a comma-separated list; empty input → empty list."""
    raw = _prompt(question + " (comma-separated; leave empty to skip)", default_csv)
    if not raw.strip():
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


# ---------------------------------------------------------------------------
# Hardware detection — used by the welcome step's backend hint.  The
# helper survived the rewrite because no parity finding asked us to
# touch it; kept here so the welcome step doesn't pull in a separate
# module just for one detection call.
# ---------------------------------------------------------------------------


def _detect_hardware() -> Dict[str, Any]:
    """Detect GPU hardware if available."""
    info: Dict[str, Any] = {
        "gpu_available": False,
        "gpu_name": None,
        "vram_gb": None,
        "cuda_version": None,
    }
    try:
        import torch

        if torch.cuda.is_available():
            info["gpu_available"] = True
            info["gpu_name"] = torch.cuda.get_device_name(0)
            info["vram_gb"] = round(torch.cuda.get_device_properties(0).total_memory / (1024**3), 1)
            info["cuda_version"] = torch.version.cuda
    except ImportError:
        pass
    return info


# ---------------------------------------------------------------------------
# Module-level constants reused across wizard sub-modules
# ---------------------------------------------------------------------------


# HF Hub dataset IDs usually look like ``<org>/<name>`` (one slash), but
# the Hub also still serves legacy canonical datasets with no namespace
# at all (e.g. ``squad``, ``glue``, ``imdb``) — the optional ``<org>/``
# group accepts both shapes.  Bounded quantifiers ({1,96} on each
# segment) keep this ReDoS-safe regardless (see docs/standards/regex.md
# rule 3): no unbounded repetition, and the literal ``/`` separator
# leaves nothing for the two ``[\w.-]`` runs to backtrack against each
# other over.  We accept these BYOD inputs without touching the local
# filesystem; the trainer resolves them at runtime.  See
# ``forgelm/wizard/_byod.py`` for the consumer.
_HF_HUB_ID_RE = re.compile(r"^(?:[\w.-]{1,96}/)?[\w.-]{1,96}$", flags=re.ASCII)


# ``sys.platform`` is read by the welcome step's backend hint; expose
# here so submodules don't each re-import ``sys``.
_PLATFORM = sys.platform
