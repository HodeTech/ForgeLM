"""Entry point for ``python -m forgelm.cli``.

Load-bearing for the quickstart subprocess flow: ``_run_quickstart_train_subprocess``
and ``_run_quickstart_chat_subprocess`` spawn ``[sys.executable, "-m", "forgelm.cli", ...]``,
so this file MUST exist for the package form of the CLI to be invokable.

``sys.argv[0]`` is normalised to ``"forgelm"`` for the same reason as in
``forgelm/__main__.py``: under ``python -m`` the interpreter sets it to
this file's path, so argparse would derive ``prog="__main__.py"`` and
emit ``usage: __main__.py ...``.  That is not merely cosmetic here — the
quickstart spawns this form, so an argparse error in the child would
hand the operator a usage line naming a command that does not exist.
One CLI, three entry points, one ``prog``.
"""

import sys

from forgelm.cli import main

if __name__ == "__main__":
    sys.argv[0] = "forgelm"
    main()
