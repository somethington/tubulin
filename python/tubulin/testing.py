"""Headless harness for running a Tubulin app's job without a browser or the runtime.

The Rust runtime normally drives an app: it reads ``ui.py``, resolves the job's
arguments from widget values, spawns worker processes, and routes outputs to bound
widgets. For *tests, golden-output regression, and benchmarks* you usually just want
to run the job once and inspect what it produced — without a server, a WebSocket, or
a browser. That's what this module is for::

    from tubulin.testing import run_app

    r = run_app("examples/tpp/ui.py")                 # run with widget defaults
    assert r.result is not None                       # primary result table written
    assert "averaged" in r.outputs                    # a named table output exists
    df = r.table("averaged")                          # -> polars DataFrame
    assert df.height > 0
    assert r.elapsed < 60

    # override inputs by widget id *or* by label, exactly like a user would set them
    r = run_app("examples/tpp/ui.py", inputs={"Imputation seed": 7, "checkbox_7": False})

This runs the job **in-process** through the same execution core the worker uses
(:func:`tubulin.worker.execute_job`), so output-collection semantics are identical.
What it does *not* reproduce is the runtime's real parallelism: by default the job
runs as a single shard. Pass ``shards=N`` to exercise the sharding / auto skip-primary
slicing — shards run **sequentially** here, so ``elapsed`` is a single-machine
sequential time, not the runtime's wall-clock. Use it for correctness, not for the
parallel speed number (spin up the real runtime for that).
"""

from __future__ import annotations

import importlib.util
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import protocol
from .app import App, get_active_app
from .worker import JobContext, execute_job


@dataclass
class RunResult:
    """Everything a headless run produced. Returned by :func:`run_app`."""

    job: str
    elapsed: float
    result_path: Optional[str]
    outputs: Dict[str, str]
    logs: List[str] = field(default_factory=list)
    progress: List[int] = field(default_factory=list)
    result_dir: str = ""

    # -- convenience accessors --------------------------------------------
    @property
    def result(self) -> Optional[str]:
        """Path of the primary result table (what ``job.result(...)`` registered)."""
        return self.result_path

    def table(self, name: Optional[str] = None):
        """Read a table output as a polars DataFrame.

        ``name=None`` reads the primary result; otherwise reads ``outputs[name]``
        (what ``job.table(name, ...)`` / ``job.result(...)`` wrote as parquet).
        """
        import polars as pl

        path = self.result_path if name is None else self.outputs.get(name)
        if not path:
            raise KeyError(f"no table output {name!r}; have {sorted(self.outputs)}")
        return pl.read_parquet(path)

    def text(self, name: str) -> str:
        """Read a text/JSON output (e.g. a Plotly figure) as a string."""
        with open(self.outputs[name]) as fh:
            return fh.read()

    def gallery(self, name: str) -> List[str]:
        """List the files a ``job.map(into=name)`` output directory accumulated."""
        d = self.outputs.get(name)
        if not d or not os.path.isdir(d):
            return []
        return sorted(os.path.join(d, f) for f in os.listdir(d))


def load_app(ui_path: str) -> App:
    """Import a ``ui.py`` in-process and return the :class:`App` it constructs.

    Sets ``TUBULIN_REGISTER`` so the optional ``app.run()`` dev-shim is a no-op, and
    puts the app's directory on ``sys.path`` (so its ``compute.py`` / ``app_def.py``
    imports resolve) exactly as the worker does.
    """
    ui_path = os.path.abspath(ui_path)
    directory = os.path.dirname(ui_path)
    if directory not in sys.path:
        sys.path.insert(0, directory)
    os.environ.setdefault("TUBULIN_REGISTER", "1")

    # Evict any previously-imported modules that live in the app's directory, so each
    # call constructs a *fresh* App. Apps keep their App as a module-level singleton
    # (e.g. app_def.py); without this, re-importing ui.py would re-run its body
    # against the cached App and duplicate every widget and action. Heavy libraries
    # (polars/scipy/matplotlib) live elsewhere and stay cached — re-import is cheap.
    prefix = directory + os.sep
    for name, mod in list(sys.modules.items()):
        modfile = getattr(mod, "__file__", None)
        if modfile and os.path.abspath(modfile).startswith(prefix):
            del sys.modules[name]

    spec = importlib.util.spec_from_file_location("tubulin_app_under_test", ui_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {ui_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    app = get_active_app()
    if app is None:
        raise RuntimeError(f"{ui_path} did not construct an App")
    return app


def _find_action(app: App, job: Optional[str]) -> Dict[str, Any]:
    submits = {
        nid: a for nid, a in app._actions.items() if a.get("type") == "submit_job"
    }
    if job is not None:
        for action in submits.values():
            if action["job"] == job:
                return action
        raise KeyError(
            f"no submit_job action for job {job!r} (have {[a['job'] for a in submits.values()]})"
        )
    if not submits:
        raise ValueError(
            "app declares no submit_job action (no button wired to a job?)"
        )
    distinct = {a["job"] for a in submits.values()}
    if len(distinct) > 1:
        raise ValueError(
            f"app has multiple jobs {sorted(distinct)}; pass job=<name> to pick one"
        )
    # Several buttons may trigger the same job (e.g. Run + Apply) — any is fine.
    return next(iter(submits.values()))


def _validate_inputs(app: App, inputs: Optional[Dict[str, Any]]) -> None:
    """Reject input keys that match no widget id or label, so a typo (or a label
    that happens to contain ``=``) fails loudly instead of being silently ignored."""
    if not inputs:
        return
    valid = {w.id for w in app._nodes}
    valid |= {w.props["label"] for w in app._nodes if "label" in w.props}
    unknown = [k for k in inputs if k not in valid]
    if unknown:
        labels = sorted(w.props["label"] for w in app._nodes if "label" in w.props)
        raise KeyError(
            f"unknown input(s) {unknown}; match a widget id or one of these labels: {labels}"
        )


def _resolve_args(
    app: App, action: Dict[str, Any], inputs: Optional[Dict[str, Any]]
) -> List[Any]:
    """Turn the action's recorded ArgSpecs into concrete values.

    Refs resolve to the referenced widget's default value; ``inputs`` overrides them
    by widget id *or* by label. File-upload args (``.path``) have no default, so they
    must be supplied via ``inputs``.
    """
    inputs = inputs or {}
    nodes = {w.id: w for w in app._nodes}
    args: List[Any] = []
    for spec in action["args"]:
        if spec["kind"] == "literal":
            args.append(spec["value"])
            continue
        node, fieldname = spec["node"], spec["field"]
        widget = nodes.get(node)
        label = widget.props.get("label") if widget else None
        if node in inputs:
            args.append(inputs[node])
        elif label is not None and label in inputs:
            args.append(inputs[label])
        elif widget is not None and fieldname in widget.props:
            args.append(widget.props[fieldname])
        elif fieldname == "path":
            raise ValueError(
                f"input {label or node!r} is a file with no default; "
                f"pass inputs={{{node!r}: '/path/to/file'}}"
            )
        else:
            raise ValueError(f"cannot resolve arg {node}.{fieldname}")
    return args


def run_app(
    ui_path: str,
    inputs: Optional[Dict[str, Any]] = None,
    *,
    job: Optional[str] = None,
    shards: int = 1,
    result_dir: Optional[str] = None,
) -> RunResult:
    """Run an app's job to completion in-process and return a :class:`RunResult`.

    ``inputs`` overrides widget values (by id or label). ``job`` selects which job to
    run when an app has more than one. ``shards`` runs the job as N sequential shards
    to exercise the sharding logic. ``result_dir`` is where outputs are written
    (a fresh temp dir by default).
    """
    app = load_app(ui_path)
    action = _find_action(app, job)
    func = app._job_funcs[action["job"]]
    _validate_inputs(app, inputs)
    args = _resolve_args(app, action, inputs)

    if result_dir is None:
        import tempfile

        result_dir = tempfile.mkdtemp(prefix="tubulin_test_")
    os.makedirs(result_dir, exist_ok=True)
    job_id = uuid.uuid4().hex

    # Capture the worker line-protocol calls instead of letting them print.
    logs: List[str] = []
    progress: List[int] = []
    old_log, old_progress = protocol.log, protocol.progress
    protocol.log = lambda m: logs.append(str(m))  # type: ignore[assignment]
    protocol.progress = lambda p: progress.append(int(p))  # type: ignore[assignment]

    result_path: Optional[str] = None
    outputs: Dict[str, str] = {}
    try:
        start = time.perf_counter()
        for shard in range(max(1, shards)):
            ctx = JobContext(job_id, result_dir, shard=shard, num_shards=max(1, shards))
            path, outs = execute_job(func, args, ctx)
            if path:
                result_path = path
            outputs.update(outs)
        elapsed = round(time.perf_counter() - start, 2)
    finally:
        protocol.log, protocol.progress = old_log, old_progress  # type: ignore[assignment]

    return RunResult(
        job=action["job"],
        elapsed=elapsed,
        result_path=result_path,
        outputs=outputs,
        logs=logs,
        progress=progress,
        result_dir=result_dir,
    )


def main(argv: Optional[List[str]] = None) -> None:
    """CLI: ``python -m tubulin.testing <ui.py> [--shards N] [key=value ...]``.

    Runs the app's job with widget defaults (plus any ``key=value`` input overrides)
    and prints a one-line summary — a browser-free replacement for hand-driving the
    app over a WebSocket just to time it or eyeball its outputs.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(
            "usage: python -m tubulin.testing <ui.py> [--shards N] [label_or_id=value ...]"
        )
        raise SystemExit(2)

    ui_path = argv[0]
    shards = 1
    inputs: Dict[str, Any] = {}
    i = 1
    while i < len(argv):
        tok = argv[i]
        if tok == "--shards":
            shards = int(argv[i + 1])
            i += 2
            continue
        if "=" in tok:
            key, _, raw = tok.partition("=")
            inputs[key] = _coerce(raw)
        i += 1

    r = run_app(ui_path, inputs=inputs, shards=shards)
    print(f"job={r.job}  elapsed={r.elapsed}s  shards={shards}")
    print(f"result={r.result_path}")
    for name, path in r.outputs.items():
        kind = "dir" if os.path.isdir(path) else "file"
        extra = f" ({len(r.gallery(name))} files)" if kind == "dir" else ""
        print(f"  output[{name}] -> {kind} {path}{extra}")
    if r.logs:
        print("logs:")
        for line in r.logs:
            print(f"  {line}")


def _coerce(raw: str) -> Any:
    low = raw.lower()
    if low in ("true", "false"):
        return low == "true"
    # A JSON array is the unambiguous way to pass a multiselect from the CLI —
    # splitting on commas would corrupt any free-text value containing one.
    if raw.startswith("["):
        import json

        try:
            return json.loads(raw)
        except ValueError:
            return raw
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        return raw


if __name__ == "__main__":
    main()
