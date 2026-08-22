"""Job declarations and the submit-binding they produce.

``@app.job`` turns a plain function into a :class:`JobHandle`. Calling
``handle.submit(...)`` during registration does **not** run anything — it returns a
:class:`JobBinding`, a small serialisable description of "submit this job with these
args" that the Rust runtime executes when the bound widget fires.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

from .refs import Ref, SlotResultRef


def to_argspec(value: Any) -> Dict[str, Any]:
    """Convert a Python argument into a wire ArgSpec (ref or literal)."""
    if isinstance(value, Ref):
        return {"kind": "ref", "node": value.node, "field": value.field}
    return {"kind": "literal", "value": value}


class JobBinding:
    """The result of ``job.submit(...)``: an action plus a handle to its result.

    Pass it to ``button.on_click(...)`` (to trigger it), ``app.job_status(...)``
    (to watch it), and ``app.table_preview(job.result_path)`` (to preview its
    output). Create it once and reuse it so all three bind to the same job slot.
    """

    def __init__(self, job_name: str, args: List[Dict[str, Any]], result_slot: str):
        self.job_name = job_name
        self.args = args
        self.result_slot = result_slot

    @property
    def result_path(self) -> SlotResultRef:
        return SlotResultRef(self.result_slot)

    def to_action(self) -> Dict[str, Any]:
        return {
            "type": "submit_job",
            "job": self.job_name,
            "args": self.args,
            "result_slot": self.result_slot,
        }


class JobHandle:
    """A registered job function. Use ``.submit(*args)`` to bind it to a widget."""

    def __init__(self, app, func):
        self.app = app
        self.func = func
        self.name = func.__name__
        # The file where the function is *defined* — this is the "computational
        # python file" the worker will import, which may differ from ui.py.
        self.module_file = os.path.abspath(func.__code__.co_filename)

    def submit(self, *args: Any) -> JobBinding:
        slot = self.app._new_slot()
        argspecs = [to_argspec(a) for a in args]
        return JobBinding(self.name, argspecs, slot)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        # Allow calling the underlying function directly (used inside workers).
        return self.func(*args, **kwargs)
