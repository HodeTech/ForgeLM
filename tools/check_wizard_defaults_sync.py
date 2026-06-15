"""CI guard — wizard defaults JSON / JS shipped artefacts match the schema.

Regenerates the expected artefact content in memory (via
:mod:`tools.generate_wizard_defaults`'s ``render_*`` helpers) and
byte-compares it against the committed files.  Fails the run with a
clear message + a one-liner the operator can copy-paste to regenerate.

Why a guard instead of always-regenerate?

The shipped JSON / JS files are operator-facing artefacts (one is
package data ``forgelm/wizard/_defaults.json``, the other is web asset
``site/js/wizard_defaults.js``).  Always regenerating at install time
would either (a) require a build step the project doesn't have, or
(b) leave the operator's working tree dirty after every test run.
Treating the artefacts as committed source + verifying drift at PR
time is the same discipline the project uses for SBOMs and audit-event
catalogues.

Run via::

    python tools/check_wizard_defaults_sync.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from tools.generate_wizard_defaults import (  # noqa: E402  -- after path tweak
    JS_TARGET,
    PYTHON_TARGET,
    collect_defaults,
    render_js_literal,
    render_python_json,
)


def _display_path(target: Path) -> str:
    """Best-effort relative path for display; falls back to absolute.

    The guard normally runs against in-repo paths; tests that
    monkeypatch ``PYTHON_TARGET`` to a tmp dir would crash inside
    ``Path.relative_to`` without this guard.
    """
    try:
        return str(target.relative_to(_REPO_ROOT))
    except ValueError:
        return str(target)


def _check_one(target: Path, expected: str) -> bool:
    """Return ``True`` when *target* matches *expected* byte-for-byte."""
    if not target.is_file():
        print(f"  ✗ {_display_path(target)} is missing.")
        return False
    actual = target.read_text(encoding="utf-8")
    if actual == expected:
        return True
    print(f"  ✗ {_display_path(target)} drifted from schema-derived value.")
    # Print the first ~10 differing lines so operators see WHAT
    # changed without having to scroll the whole file.
    actual_lines = actual.splitlines()
    expected_lines = expected.splitlines()
    diffs_shown = 0
    for i, (a, e) in enumerate(zip(actual_lines, expected_lines)):
        if a != e:
            print(f"      line {i + 1}:")
            print(f"        on disk: {a}")
            print(f"        schema : {e}")
            diffs_shown += 1
            if diffs_shown >= 5:
                print("      ...(truncated; regenerate to see the full diff)")
                break
    return False


def main() -> int:
    defaults = collect_defaults()
    expected_python = render_python_json(defaults)
    expected_js = render_js_literal(defaults)

    ok_python = _check_one(PYTHON_TARGET, expected_python)
    ok_js = _check_one(JS_TARGET, expected_js)

    if ok_python and ok_js:
        section_count = len(defaults)
        field_count = sum(len(v) for v in defaults.values())
        print(f"OK: shipped wizard defaults ({section_count} section(s) / {field_count} field(s)) match the schema.")
        return 0

    print(
        "\nFix: a wizard-flagged Pydantic field's default changed without "
        "regenerating the shipped artefacts.  Run:\n"
        "    python tools/generate_wizard_defaults.py\n"
        "and commit the updated forgelm/wizard/_defaults.json + "
        "site/js/wizard_defaults.js."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
