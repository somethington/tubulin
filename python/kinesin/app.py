"""The user-facing ``App`` object.

During the registration pass this accumulates a declarative description of the
whole application — widgets, the actions wired to them, the job catalogue, and the
widget→job bindings — which :mod:`kinesin.register` serialises to JSON for the Rust
runtime to own.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional, Union

from .jobs import JobBinding, JobHandle
from .refs import SlotResultRef
from .widgets import Widget

# The most recently constructed App. The registration and worker passes read this
# after importing the user's module.
_ACTIVE_APP: Optional["App"] = None


def set_active_app(app: "App") -> None:
    global _ACTIVE_APP
    _ACTIVE_APP = app


def get_active_app() -> Optional["App"]:
    return _ACTIVE_APP


class App:
    def __init__(self, title: str = "Kinesin App"):
        self.title = title
        self._nodes: List[Widget] = []
        self._actions: Dict[str, Dict[str, Any]] = {}
        self._jobs: Dict[str, Dict[str, str]] = {}
        self._job_funcs: Dict[str, Any] = {}
        self._bindings: Dict[str, Dict[str, str]] = {}
        self._counters: Dict[str, int] = {}
        self._slot_counter = 0
        set_active_app(self)

    # -- id helpers --------------------------------------------------------
    def _next_id(self, kind: str) -> str:
        self._counters[kind] = self._counters.get(kind, 0) + 1
        return f"{kind}_{self._counters[kind]}"

    def _new_slot(self) -> str:
        self._slot_counter += 1
        return f"slot_{self._slot_counter}"

    def _add(self, kind: str, props: Dict[str, Any]) -> Widget:
        widget = Widget(self, self._next_id(kind), kind, props)
        self._nodes.append(widget)
        return widget

    # -- widgets -----------------------------------------------------------
    def text(self, value: str) -> Widget:
        return self._add("text", {"value": value})

    def header(self, value: str) -> Widget:
        return self._add("header", {"value": value})

    def button(self, label: str) -> Widget:
        return self._add("button", {"label": label})

    def file_upload(self, label: str) -> Widget:
        return self._add("file_upload", {"label": label})

    def job_status(self, binding: JobBinding) -> Widget:
        widget = self._add(
            "job_status",
            {"status": "idle", "progress": 0, "message": "", "logs": []},
        )
        self._bindings[widget.id] = {"slot": binding.result_slot, "view": "status"}
        return widget

    def table_preview(self, source: Union[SlotResultRef, str]) -> Widget:
        widget = self._add("table_preview", {"ready": False, "columns": [], "rows": []})
        if isinstance(source, SlotResultRef):
            self._bindings[widget.id] = {"slot": source.slot, "view": "result_preview"}
        elif isinstance(source, str):
            widget.props["path"] = source
        else:
            raise TypeError("table_preview expects job.result_path or a path string")
        return widget

    # -- input widgets (their .value can be passed to a job) ----------------
    def checkbox(self, label: str, default: bool = False) -> Widget:
        return self._add("checkbox", {"label": label, "value": bool(default)})

    def slider(
        self, label: str, min: float, max: float, step: float = 0.1, default: float = None
    ) -> Widget:
        value = min if default is None else default
        return self._add(
            "slider", {"label": label, "min": min, "max": max, "step": step, "value": value}
        )

    def number(self, label: str, default: float = 0) -> Widget:
        return self._add("number", {"label": label, "value": default})

    def select(self, label: str, options: List[str], default: str = None) -> Widget:
        opts = [str(o) for o in options]
        value = opts[0] if default is None else str(default)
        return self._add("select", {"label": label, "options": opts, "value": value})

    # -- named outputs produced by a job -----------------------------------
    def image(self, job: JobBinding, name: str, caption: str = "") -> Widget:
        """A plot the job writes as ``outputs[name]`` (an SVG path)."""
        widget = self._add("image", {"ready": False, "caption": caption})
        self._bindings[widget.id] = {"slot": job.result_slot, "view": f"plot:{name}"}
        return widget

    def plot(self, job: JobBinding, name: str, caption: str = "") -> Widget:
        """An interactive Plotly chart the job writes as ``outputs[name]`` (a JSON path)."""
        widget = self._add("plotly", {"ready": False, "caption": caption})
        self._bindings[widget.id] = {"slot": job.result_slot, "view": f"plotly:{name}"}
        return widget

    def result_table(self, job: JobBinding, name: str, caption: str = "") -> Widget:
        """A secondary table the job writes as ``outputs[name]`` (a parquet path)."""
        widget = self._add(
            "table_preview", {"ready": False, "columns": [], "rows": [], "caption": caption}
        )
        self._bindings[widget.id] = {"slot": job.result_slot, "view": f"table:{name}"}
        return widget

    def download(self, job: JobBinding, name: str, label: str = "Download") -> Widget:
        """A download button for a file the job writes as ``outputs[name]``."""
        widget = self._add("download", {"ready": False, "label": label})
        self._bindings[widget.id] = {"slot": job.result_slot, "view": f"download:{name}"}
        return widget

    def gallery(self, job: JobBinding, name: str, caption: str = "") -> Widget:
        """A paginated image gallery; the job writes ``outputs[name]`` as a JSON index
        of ``[{"protein", "path"}]`` items (each a file the gallery loads on demand)."""
        widget = self._add("gallery", {"ready": False, "caption": caption})
        self._bindings[widget.id] = {"slot": job.result_slot, "view": f"gallery:{name}"}
        return widget

    # -- actions -----------------------------------------------------------
    def set_text(self, target: Widget, value: str) -> Dict[str, Any]:
        """A lightweight action that sets ``target``'s text to ``value``.

        Pass to ``button.on_click(...)``. Runs entirely in the runtime — handy for
        wiring demos without a job.
        """
        return {"type": "set_text", "target": target.id, "value": value}

    # -- jobs --------------------------------------------------------------
    def job(self, func=None, *, parallel: bool = False, shardable: bool = False):
        """Decorator registering a job; returns a :class:`JobHandle`.

        Use ``@app.job`` or ``@app.job(parallel=True)``. A parallel job is run by the
        runtime as several worker processes; use ``job.map(...)`` / ``job.primary(...)``
        to fan work out and write single-copy outputs without touching shard indices.
        """

        def register(fn):
            handle = JobHandle(self, fn)
            self._jobs[handle.name] = {
                "module_file": handle.module_file,
                "qualname": handle.name,
                "shardable": bool(parallel or shardable),
            }
            self._job_funcs[handle.name] = fn
            return handle

        return register(func) if func is not None else register

    # -- serialisation -----------------------------------------------------
    def to_spec(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "nodes": [
                {"id": w.id, "kind": w.kind, "props": w.props} for w in self._nodes
            ],
            "actions": self._actions,
            "jobs": self._jobs,
            "bindings": self._bindings,
        }

    # -- dev convenience ---------------------------------------------------
    def run(self, host: Optional[str] = None, port: Optional[int] = None) -> None:
        """Optional shim so ``python ui.py`` works during development.

        Under the registration/worker passes this is a no-op (the runtime drives
        everything). Run directly, it hands off to the ``kinesin`` binary.
        """
        if os.environ.get("KINESIN_REGISTER") or os.environ.get("KINESIN_WORKER"):
            return
        import shutil

        binary = shutil.which("kinesin")
        entry = os.path.abspath(sys.argv[0])
        if binary:
            os.execv(binary, [binary, "run", entry])
        else:
            sys.stderr.write(f"Run this app with:  kinesin run {entry}\n")
