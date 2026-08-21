"""The user-facing ``App`` object.

During the registration pass this accumulates a declarative description of the
whole application — widgets, the actions wired to them, the job catalogue, and the
widget→job bindings — which :mod:`kinesin.register` serialises to JSON for the Rust
runtime to own.
"""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from textwrap import dedent
from typing import Any, Dict, List, Optional, Union

from .jobs import JobBinding, JobHandle
from .refs import SlotResultRef
from .widgets import Widget

# The most recently constructed App. The registration and worker passes read this
# after importing the user's module.
_ACTIVE_APP: Optional["App"] = None


class _Region:
    """One column or tab: a context manager bracketing its widgets with markers.

    Layout stays a *flat* node list with balanced ``<kind>_start`` / ``<kind>_end``
    markers (the same shape ``app.group`` uses), so a widget's value seeds and flows
    identically however deeply it is nested — only the frontend's rendering differs.
    """

    def __init__(self, app: "App", kind: str, props: Dict[str, Any]):
        self._app = app
        self._kind = kind
        self._props = props
        self._used = False

    def __enter__(self) -> "_Region":
        if self._used:
            raise RuntimeError(f"this {self._kind} has already been entered")
        self._used = True
        self._app._add(f"{self._kind}_start", self._props)
        return self

    def __exit__(self, *exc) -> None:
        self._app._add(f"{self._kind}_end", {})

    def _emit_if_unused(self) -> None:
        """Reserve an empty cell for a region the app never entered."""
        if not self._used:
            self._used = True
            self._app._add(f"{self._kind}_start", self._props)
            self._app._add(f"{self._kind}_end", {})


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

    def _gate(self, widget: Widget, controller: Optional[Widget]) -> Widget:
        """Disable ``widget`` in the UI whenever ``controller``'s value is falsy."""
        if controller is not None:
            widget.props["enabled_when"] = controller.id
        return widget

    # -- widgets -----------------------------------------------------------
    def text(self, value: str) -> Widget:
        return self._add("text", {"value": value})

    def markdown(self, value: str) -> Widget:
        """Rich text: headings, lists, links, ``**bold**``, ``` `code` ```, quotes, rules.

        The renderer is a small CommonMark subset built into the frontend, so prose,
        instructions and units render without an app author reaching for HTML::

            app.markdown(\"\"\"
            ## Melting curve fit

            Uses a **sigmoid** with a plateau term; see [the method](https://…).
            \"\"\")

        Use ``app.text`` for a single unformatted line.
        """
        return self._add("markdown", {"value": dedent(str(value)).strip()})

    def header(self, value: str) -> Widget:
        return self._add("header", {"value": value})

    @contextmanager
    def group(self, title: str, collapsible: bool = True, expanded: bool = True):
        """Group the widgets created inside the ``with`` block into a collapsible card
        (like Streamlit's expander)::

            with app.group("Data Visualization Options"):
                app.checkbox("Show distribution")
                app.checkbox("Violin plot")

        Implemented with ``group_start`` / ``group_end`` marker nodes: the children
        stay normal top-level widgets, so their values seed and flow exactly as
        ungrouped ones do — the frontend just renders the span between the markers as
        one bordered, collapsible card.
        """
        self._add(
            "group_start",
            {
                "title": title,
                "collapsible": bool(collapsible),
                "expanded": bool(expanded),
            },
        )
        try:
            yield
        finally:
            self._add("group_end", {})

    @contextmanager
    def columns(self, spec: Union[int, List[float]]):
        """Lay widgets out side by side::

            with app.columns(2) as (left, right):
                with left:
                    app.slider("Min temperature", 30, 70)
                with right:
                    app.slider("Max temperature", 30, 70)

        ``spec`` is either a column count or a list of **relative** widths, so
        ``app.columns([2, 1])`` is a two-thirds / one-third split. Columns stack on a
        narrow window. A region you never enter still reserves its cell.
        """
        widths = [1.0] * spec if isinstance(spec, int) else [float(w) for w in spec]
        if not widths:
            raise ValueError("app.columns needs at least one column")
        self._add("columns_start", {"widths": widths})
        regions = tuple(_Region(self, "column", {}) for _ in widths)
        try:
            yield regions
        finally:
            # An unused column still gets its marker pair, so the grid keeps its shape.
            for region in regions:
                region._emit_if_unused()
            self._add("columns_end", {})

    @contextmanager
    def tabs(self, *labels: str):
        """Put widgets on separate tabs::

            with app.tabs("Data", "Parameters") as (data, params):
                with data:
                    app.file_upload("Measurements")
                with params:
                    app.number("Seed", 7)

        Hidden panels stay mounted, so switching tabs never resets a widget or drops
        a rendered result.
        """
        if len(labels) == 1 and isinstance(labels[0], (list, tuple)):
            labels = tuple(labels[0])
        names = [str(label) for label in labels]
        if not names:
            raise ValueError("app.tabs needs at least one label")
        self._add("tabs_start", {"labels": names})
        regions = tuple(_Region(self, "tab", {"label": name}) for name in names)
        try:
            yield regions
        finally:
            for region in regions:
                region._emit_if_unused()
            self._add("tabs_end", {})

    @contextmanager
    def sidebar(self):
        """Render the widgets in the block into the app's left sidebar::

            with app.sidebar():
                app.markdown("### Filters")
                app.multiselect("Conditions", CONDITIONS)

        Several ``sidebar()`` blocks concatenate, so a long parameter list can stay
        next to the code it belongs to. Their values behave exactly like main-column
        widgets — only the placement differs.
        """
        self._add("sidebar_start", {})
        try:
            yield
        finally:
            self._add("sidebar_end", {})

    def button(self, label: str, enabled_when: Optional[Widget] = None) -> Widget:
        return self._gate(self._add("button", {"label": label}), enabled_when)

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
    def checkbox(
        self, label: str, default: bool = False, enabled_when: Optional[Widget] = None
    ) -> Widget:
        w = self._add("checkbox", {"label": label, "value": bool(default)})
        return self._gate(w, enabled_when)

    def slider(
        self,
        label: str,
        min: float,
        max: float,
        step: float = 0.1,
        default: float = None,
        enabled_when: Optional[Widget] = None,
    ) -> Widget:
        value = min if default is None else default
        w = self._add(
            "slider",
            {"label": label, "min": min, "max": max, "step": step, "value": value},
        )
        return self._gate(w, enabled_when)

    def number(
        self, label: str, default: float = 0, enabled_when: Optional[Widget] = None
    ) -> Widget:
        return self._gate(
            self._add("number", {"label": label, "value": default}), enabled_when
        )

    def select(
        self,
        label: str,
        options: List[str],
        default: str = None,
        enabled_when: Optional[Widget] = None,
    ) -> Widget:
        opts = [str(o) for o in options]
        value = opts[0] if default is None else str(default)
        w = self._add("select", {"label": label, "options": opts, "value": value})
        return self._gate(w, enabled_when)

    def multiselect(
        self,
        label: str,
        options: List[str],
        default: Optional[List[str]] = None,
        enabled_when: Optional[Widget] = None,
    ) -> Widget:
        """Choose any subset of ``options``; the job receives a ``list[str]``.

        The value always arrives in **declaration order**, not click order, so a job
        can index into it deterministically. ``default=None`` selects nothing;
        pass ``default=options`` to start fully selected.
        """
        opts = [str(o) for o in options]
        if default is None:
            value: List[str] = []
        else:
            chosen = {str(d) for d in default}
            unknown = chosen - set(opts)
            if unknown:
                raise ValueError(
                    f"multiselect({label!r}) default has options that don't exist: "
                    f"{sorted(unknown)}"
                )
            value = [o for o in opts if o in chosen]
        w = self._add("multiselect", {"label": label, "options": opts, "value": value})
        return self._gate(w, enabled_when)

    def text_input(
        self,
        label: str,
        default: str = "",
        placeholder: str = "",
        enabled_when: Optional[Widget] = None,
    ) -> Widget:
        """A single-line free-text field (sample name, prefix, accession, regex)."""
        w = self._add(
            "text_input",
            {"label": label, "value": str(default), "placeholder": placeholder},
        )
        return self._gate(w, enabled_when)

    def text_area(
        self,
        label: str,
        default: str = "",
        placeholder: str = "",
        rows: int = 4,
        enabled_when: Optional[Widget] = None,
    ) -> Widget:
        """A multi-line free-text field (a pasted id list, a note, a small config)."""
        w = self._add(
            "text_area",
            {
                "label": label,
                "value": str(default),
                "placeholder": placeholder,
                "rows": int(rows),
            },
        )
        return self._gate(w, enabled_when)

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
            "table_preview",
            {"ready": False, "columns": [], "rows": [], "caption": caption},
        )
        self._bindings[widget.id] = {"slot": job.result_slot, "view": f"table:{name}"}
        return widget

    def download(self, job: JobBinding, name: str, label: str = "Download") -> Widget:
        """A download button for a file the job writes as ``outputs[name]``."""
        widget = self._add("download", {"ready": False, "label": label})
        self._bindings[widget.id] = {
            "slot": job.result_slot,
            "view": f"download:{name}",
        }
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
