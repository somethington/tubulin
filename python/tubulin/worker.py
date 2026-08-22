"""Disposable job worker: ``python -m tubulin.worker`` reads a job descriptor on
stdin, runs the job, streams progress/log lines, and writes its result to disk.

Descriptor (one JSON object on stdin)::

    {"job_id": "...", "module_file": "/abs/compute.py", "qualname": "analyze_csv",
     "args": ["storage/uploads/x.csv"], "result_dir": "storage/results"}

The worker imports ``module_file`` (the file where the job is *defined*) and exits
when done, so the OS reclaims all memory the job used.
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import os
import sys
import traceback
from typing import Any

os.environ["TUBULIN_WORKER"] = "1"

from tubulin import protocol  # noqa: E402  (after env flag is set)


def _safe(name) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in str(name))


def _is_frame(value) -> bool:
    """True for a dataframe (polars or pandas) as opposed to a path string."""
    return not isinstance(value, str) and (
        hasattr(value, "write_parquet") or hasattr(value, "to_parquet")
    )


class JobContext:
    """Passed to jobs that declare a ``job`` parameter.

    For parallel (`@app.job(parallel=True)`) jobs this exposes the high-level
    primitives — :meth:`map`, :meth:`primary`, :meth:`output` — so the job never
    has to touch ``shard`` / ``is_primary`` / slicing directly.
    """

    def __init__(
        self, job_id: str, result_dir: str, shard: int = 0, num_shards: int = 1
    ):
        self.id = job_id
        self.result_dir = result_dir
        # When the runtime parallelises a job, each worker gets a distinct shard
        # index; a job processes items[shard::num_shards]. Defaults: 1 shard.
        self.shard = shard
        self.num_shards = num_shards
        # Accumulated by the helpers; collected by the worker on completion.
        self._outputs: dict[str, str] = {}
        self._result_path = None
        # Deferred map/primary work for parallel jobs. ``map``/``primary`` only
        # *register* tasks; the worker runs them (via :meth:`_run_deferred`) after
        # the job function returns, when the full plan is known — which is what lets
        # the framework balance the fan-out (skip the primary shard) and aggregate
        # progress automatically, with no scheduling knobs in the app.
        self._tasks: list[dict] = []

    @property
    def is_primary(self) -> bool:
        """True for exactly one shard (shard 0) — owns single-copy outputs."""
        return self.shard == 0

    def log(self, message: str) -> None:
        protocol.log(message)

    def progress(self, pct: int) -> None:
        protocol.progress(pct)

    # -- high-level parallel primitives ------------------------------------
    def primary(self, fn):
        """Register single-copy work to run **once, on shard 0**.

        Usable as a call (``job.primary(fn)``) or a decorator (``@job.primary``);
        returns ``fn`` unchanged. Put expensive single-copy work (stats, summary
        tables, the download bundle) here so it isn't duplicated across workers.

        The work is *deferred*: it runs after the job function returns, in parallel
        with the :meth:`map` fan-out on the other shards. Declaring a primary is also
        what makes the framework keep shard 0 out of the maps automatically (see
        :meth:`map`), so the analysis and the rendering overlap instead of piling on
        the same worker.
        """
        self._tasks.append({"kind": "primary", "fn": fn})
        return fn

    def result(self, table) -> str:
        """Register the job's primary result table (bound to ``table_preview``).

        Pass a **dataframe** (it's written to parquet under ``result_dir``) or a path
        to an already-written file. The runtime reads the parquet preview natively,
        so no sidecar is written here.
        """
        path = self._to_parquet("result", table) if _is_frame(table) else table
        self._result_path = path
        return path

    def output(self, name: str, path: str) -> str:
        """Register a named output artifact for a bound widget (`image`/`plot`/…).

        Takes a path (use :meth:`write_text` / :meth:`write_csv` to produce one).
        """
        self._outputs[name] = path
        return path

    def table(self, name: str, table) -> str:
        """Register a named **table** output (bound to ``result_table``).

        Pass a **dataframe** or a path; the runtime reads the parquet preview
        natively, so no sidecar is written here.
        """
        path = self._to_parquet(name, table) if _is_frame(table) else table
        self._outputs[name] = path
        return path

    # -- file helpers: own the result_dir pathing so jobs don't build paths --
    def path(self, name: str) -> str:
        """Absolute path for an output file named ``name`` under ``result_dir``."""
        return os.path.join(self.result_dir, f"{self.id}_{name}")

    def _to_parquet(self, name: str, df) -> str:
        path = self.path(f"{name}.parquet")
        if hasattr(df, "write_parquet"):
            df.write_parquet(path)  # polars
        else:
            df.to_parquet(path)  # pandas
        return path

    def write_csv(self, name: str, df) -> str:
        """Write a dataframe to ``<result_dir>/<id>_<name>.csv`` and return the path
        (handy for download bundles). Not registered as a widget output."""
        path = self.path(f"{name}.csv")
        if hasattr(df, "write_csv"):
            df.write_csv(path)  # polars
        else:
            df.to_csv(path, index=False)  # pandas
        return path

    def write_text(self, name: str, content: str) -> str:
        """Write text (e.g. a Plotly JSON or an SVG) to ``<result_dir>/<id>_<name>``
        and return the path."""
        path = self.path(name)
        with open(path, "w") as fh:
            fh.write(content)
        return path

    def map(self, items, fn, into: str, *, progress=None, skip_primary=None) -> None:
        """Fan ``fn`` over ``items``, **sharded across the runtime's workers**.

        Declares the fan-out; the worker runs only its slice once the full plan is
        known (after the job function returns). ``fn(item)`` may return a matplotlib
        ``Figure`` (saved as ``<item>.svg`` and closed) or write its own files and
        return ``None``. All shards write into the directory bound to output ``into``;
        the runtime builds the gallery by listing it.

        **Balancing and progress are automatic.** When the job also declares a
        :meth:`primary`, shard 0 is kept out of the map and the items are split across
        the remaining shards, so the rendering overlaps the single-copy analysis
        instead of piling onto it. Progress streams over 0–100 automatically; when a
        job has several maps they share that range in proportion to their item counts.
        No ``progress`` band or ``skip_primary`` flag to hand-tune.

        ``progress`` / ``skip_primary`` remain as **advanced overrides**: pass an
        explicit ``(lo, hi)`` to pin this map to a sub-range, or a ``bool`` to force
        whether shard 0 participates. Leave them ``None`` (the default) for the
        automatic behaviour.
        """
        out_dir = os.path.join(self.result_dir, f"{self.id}_{into}")
        os.makedirs(out_dir, exist_ok=True)
        self._outputs[into] = out_dir
        self._tasks.append(
            {
                "kind": "map",
                "items": list(items),
                "fn": fn,
                "out_dir": out_dir,
                "progress": progress,
                "skip_primary": skip_primary,
            }
        )

    def _run_deferred(self) -> None:
        """Run the registered :meth:`map`/:meth:`primary` tasks for this shard.

        Called by the worker after the job function returns. By then the whole plan
        is known, so the framework can balance and aggregate without app input:

        * **Skip-primary is automatic.** If any ``primary`` is declared (and there is
          more than one shard), each map excludes shard 0 and strides its items across
          the remaining shards — shard 0 runs the primary instead, overlapping it. An
          explicit ``skip_primary=`` on a map overrides this.
        * **Progress is automatic.** A shard's auto-progress maps share 0–100 in
          proportion to how many items each got, so the bar advances smoothly across
          all of them. A map given an explicit ``progress=(lo, hi)`` owns that band
          itself and is left out of the shared budget.

        Tasks run in declaration order, so a single shard (e.g. the test harness, or a
        one-core machine where skip-primary can't apply) still runs the maps and then
        the primary exactly as written.
        """
        if not self._tasks:
            return

        has_primary = any(t["kind"] == "primary" for t in self._tasks)
        maps = [t for t in self._tasks if t["kind"] == "map"]

        # Resolve each map's slice for this shard, applying skip-primary (auto unless
        # the app pinned it). Skip-primary only bites when there's more than one shard.
        for t in maps:
            skip = has_primary if t["skip_primary"] is None else t["skip_primary"]
            if skip and self.num_shards > 1:
                t["mine"] = (
                    []
                    if self.is_primary
                    else t["items"][(self.shard - 1) :: (self.num_shards - 1)]
                )
            else:
                t["mine"] = t["items"][self.shard :: self.num_shards]

        # Auto-progress budget: total items across maps that didn't pin their own band.
        auto_total = sum(len(t["mine"]) for t in maps if t["progress"] is None)
        done = 0

        for t in self._tasks:
            if t["kind"] == "primary":
                if self.is_primary:
                    t["fn"]()
                continue
            mine = t["mine"]
            n = len(mine)
            explicit = t["progress"]
            for i, item in enumerate(mine):
                artifact = t["fn"](item)
                if artifact is not None and hasattr(artifact, "savefig"):
                    # Save as laid out — no bbox_inches="tight", which forces a second
                    # render pass per figure (~13ms/curve here). Figures with artists
                    # outside the axes (e.g. an external legend) should reserve space
                    # themselves (subplots_adjust) before being returned.
                    artifact.savefig(
                        os.path.join(t["out_dir"], f"{_safe(item)}.svg"), format="svg"
                    )
                    import matplotlib.pyplot as plt

                    plt.close(artifact)
                if n and (i % 20 == 0 or i == n - 1):
                    if explicit is not None:
                        lo, hi = explicit
                        self.progress(lo + int((hi - lo) * (i + 1) / n))
                    elif auto_total:
                        self.progress(int(100 * (done + i + 1) / auto_total))
            if explicit is None:
                done += n

    def bundle(self, files=None, dirs=None) -> str:
        """Register a downloadable .zip (built on demand by the runtime).

        ``files``: ``{arcname: path}``. ``dirs``: ``{arc_prefix: output_name}`` —
        expanded at download time so it picks up files written by *every* shard.
        """
        import json

        entries = [{"file": p, "arc": arc} for arc, p in (files or {}).items()]
        for arc_prefix, into in (dirs or {}).items():
            d = self._outputs.get(
                into, os.path.join(self.result_dir, f"{self.id}_{into}")
            )
            entries.append({"glob": f"{d}/*", "arc_dir": arc_prefix})
        manifest = os.path.join(self.result_dir, f"{self.id}_bundle.json")
        with open(manifest, "w") as fh:
            json.dump({"entries": entries}, fh)
        return manifest


def execute_job(func, args, ctx: "JobContext"):
    """Call a job function with a :class:`JobContext` and collect its outputs.

    Returns ``(result_path, outputs)``. Shared by the worker subprocess (below) and
    the in-process test harness (:mod:`tubulin.testing`) so both exercise the exact
    same output-collection semantics.
    """
    sig = inspect.signature(func)
    if "job" in sig.parameters:
        result = func(*args, job=ctx)
    else:
        result = func(*args)
    # Run any map/primary work the job declared (no-op for non-parallel jobs).
    ctx._run_deferred()
    path = ctx._result_path
    outputs = dict(ctx._outputs)
    if isinstance(result, dict):
        path = result.get("result_path") or result.get("path") or path
        outputs.update(result.get("outputs") or {})
    return path, outputs


def import_user_module(module_file: str):
    """Import the file where a job is defined (the heavy import). Split out from
    resolution so the fork-server can do this **once** in the resident parent."""
    module_file = os.path.abspath(module_file)
    directory = os.path.dirname(module_file)
    if directory not in sys.path:
        sys.path.insert(0, directory)

    spec = importlib.util.spec_from_file_location("tubulin_user_module", module_file)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {module_file}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def resolve_function(module, qualname: str):
    """Find a job function on an already-imported module (or the active app registry)."""
    obj = getattr(module, qualname, None)
    if obj is None:
        # Fall back to the active app's registry (handles the ui.py/compute.py split
        # where the function lives on the app, not as a module attribute by that name).
        from tubulin.app import get_active_app

        app = get_active_app()
        if app is not None and qualname in app._job_funcs:
            return app._job_funcs[qualname]
        raise RuntimeError(f"job '{qualname}' not found")

    # ``obj`` is typically a JobHandle wrapping the real function.
    return getattr(obj, "func", obj)


def _load_function(module_file: str, qualname: str):
    return resolve_function(import_user_module(module_file), qualname)


def run_job_descriptor(descriptor: dict, module=None) -> None:
    """Run one job descriptor, emitting protocol lines (``completed``/``failed``).

    ``module``: if given, the user module is already imported (fork-server path);
    otherwise it's imported from ``descriptor["module_file"]`` (plain worker path).
    Outputs may be registered via the ``job.*`` helpers and/or returned as a dict.
    """
    job_id = descriptor["job_id"]
    result_dir = descriptor.get("result_dir", "storage/results")
    args = descriptor.get("args", [])
    shard = int(descriptor.get("shard", 0))
    num_shards = int(descriptor.get("num_shards", 1))
    try:
        if module is None:
            func = _load_function(descriptor["module_file"], descriptor["qualname"])
        else:
            func = resolve_function(module, descriptor["qualname"])
        ctx = JobContext(job_id, result_dir, shard, num_shards)
        path, outputs = execute_job(func, args, ctx)
        protocol.completed(path, outputs)
    except Exception:
        protocol.failed(traceback.format_exc())


def main() -> None:
    descriptor: dict[str, Any] = json.load(sys.stdin)
    # From here on, keep stdout for the protocol only; user prints go to stderr.
    sys.stdout = sys.stderr
    run_job_descriptor(descriptor)


if __name__ == "__main__":
    main()
