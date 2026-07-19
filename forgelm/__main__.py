"""Entry point for ``python -m forgelm``.

Equivalent to the ``forgelm`` console script declared in
``pyproject.toml``'s ``[project.scripts]``, with one operationally
important difference: ``python -m`` resolves the package from
``sys.path``, where the current working directory precedes
``site-packages``.  Running it from a checkout therefore exercises the
**working tree**, whereas the console script's ``sys.path[0]`` is the
script's own ``bin``/``Scripts`` directory, so it exercises whatever
copy of ``forgelm`` is installed — a stale non-editable install
silently shadows the checkout and turns the contributor gauntlet's
``--dry-run`` step into a check that validates code nobody edited.
See ``CONTRIBUTING.md``'s validation-gauntlet block.

``sys.argv[0]`` is normalised to ``"forgelm"`` so argparse's ``prog``
matches the console script byte-for-byte in ``--help`` and every
``usage:`` line.  This reproduces the effect of the
``sys.argv[0].removesuffix('.exe')`` line in the generated console
script — that line exists solely to turn the Windows launcher's
``...\\Scripts\\forgelm.exe`` into a ``forgelm`` prog, and under
``python -m`` the interpreter sets ``sys.argv[0]`` to this file's path
instead, which would otherwise render as ``usage: __main__.py``.

Exit codes are the public 0/1/2/3/4/5/6 contract (see
``docs/standards/error-handling.md``); ``main()`` is the same callable
the console script invokes, so the two forms agree by construction.
"""

from __future__ import annotations

import sys

from forgelm.cli import main

if __name__ == "__main__":
    sys.argv[0] = "forgelm"
    sys.exit(main())
