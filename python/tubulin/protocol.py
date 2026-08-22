"""The worker line protocol: one JSON object per line on stdout.

The runtime reads these lines and turns them into widget updates for the browser.
We capture the *real* stdout at import time so that even if user job code prints,
those prints can be redirected to stderr without corrupting this channel.
"""

from __future__ import annotations

import json
import sys
from typing import Optional

# Captured before any redirection so emit() always reaches the runtime.
_OUT = sys.stdout


def _emit(obj: dict) -> None:
    _OUT.write(json.dumps(obj) + "\n")
    _OUT.flush()


def log(message: str) -> None:
    _emit({"event": "log", "message": str(message)})


def progress(pct: int) -> None:
    _emit({"event": "progress", "progress": int(pct)})


def completed(path: Optional[str], outputs: Optional[dict] = None) -> None:
    _emit({"event": "completed", "result": {"path": path, "outputs": outputs or {}}})


def failed(error: str) -> None:
    _emit({"event": "failed", "error": str(error)})
