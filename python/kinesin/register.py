"""Registration pass: ``python -m kinesin.register ui.py`` -> app spec JSON.

Imports the user's UI file, lets the ``kinesin`` API accumulate the app
description, and prints exactly one JSON document to stdout. Any output the user's
module produces at import time is redirected to stderr so stdout stays clean.
"""

from __future__ import annotations

import json
import os
import runpy
import sys

os.environ["KINESIN_REGISTER"] = "1"


def main() -> None:
    if len(sys.argv) < 2:
        sys.stderr.write("usage: python -m kinesin.register <ui.py>\n")
        sys.exit(2)

    path = os.path.abspath(sys.argv[1])
    if not os.path.exists(path):
        sys.stderr.write(f"error: no such file: {path}\n")
        sys.exit(1)

    # Let the UI file import sibling modules (e.g. compute.py).
    directory = os.path.dirname(path)
    if directory not in sys.path:
        sys.path.insert(0, directory)

    from kinesin.app import get_active_app

    real_stdout = sys.stdout
    sys.stdout = sys.stderr  # quarantine the user module's stray prints
    try:
        runpy.run_path(path, run_name="__kinesin_ui__")
    finally:
        sys.stdout = real_stdout

    app = get_active_app()
    if app is None:
        sys.stderr.write(f"error: no kinesin.App was constructed in {path}\n")
        sys.exit(1)

    json.dump(app.to_spec(), sys.stdout)
    sys.stdout.flush()


if __name__ == "__main__":
    main()
