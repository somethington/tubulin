"""Disposable job worker: ``python -m kinesin.worker`` reads a job descriptor on
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

os.environ["KINESIN_WORKER"] = "1"

from kinesin import protocol  # noqa: E402  (after env flag is set)


def _safe(name) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in str(name))


class JobContext:
    """Passed to jobs that declare a ``job`` parameter.

    For parallel (`@app.job(parallel=True)`) jobs this exposes the high-level
    primitives — :meth:`map`, :meth:`primary`, :meth:`output` — so the job never
    has to touch ``shard`` / ``is_primary`` / slicing directly.
    """

    def __init__(self, job_id: str, result_dir: str, shard: int = 0, num_shards: int = 1):
        self.id = job_id
        self.result_dir = result_dir
        # When the runtime parallelises a job, each worker gets a distinct shard
        # index; a job processes items[shard::num_shards]. Defaults: 1 shard.
        self.shard = shard
        self.num_shards = num_shards
        # Accumulated by the helpers; collected by the worker on completion.
        self._outputs: dict[str, str] = {}
        self._result_path = None

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
        """Run ``fn`` once, on shard 0 only. Returns ``fn()`` there, else ``None``.

        Usable as a call (``job.primary(fn)``) or a decorator (``@job.primary``).
        Put expensive single-copy work (stats, summary tables) here so it runs once.
        """
        return fn() if self.is_primary else None

    def result(self, path: str) -> str:
        """Register the job's primary result file (bound to ``table_preview``)."""
        self._result_path = path
        return path

    def output(self, name: str, path: str) -> str:
        """Register a named output artifact for a bound widget (`image`/`table`/…)."""
        self._outputs[name] = path
        return path

    def map(self, items, fn, into: str, progress=(0, 100)) -> int:
        """Fan ``fn`` over ``items``, **sharded across the runtime's workers**.

        This worker processes only its slice ``items[shard::num_shards]`` — the
        runtime runs the other shards in parallel. ``fn(item)`` may return a
        matplotlib ``Figure`` (saved as ``<item>.svg`` and closed) or write its own
        files and return ``None``. All shards write into the directory bound to
        output ``into``; the runtime builds the gallery by listing it. Streams
        progress over the given range.
        """
        out_dir = os.path.join(self.result_dir, f"{self.id}_{into}")
        os.makedirs(out_dir, exist_ok=True)
        self._outputs[into] = out_dir
        mine = items[self.shard :: self.num_shards]
        total = len(mine)
        lo, hi = progress
        for i, item in enumerate(mine):
            artifact = fn(item)
            if artifact is not None and hasattr(artifact, "savefig"):
                artifact.savefig(
                    os.path.join(out_dir, f"{_safe(item)}.svg"),
                    format="svg",
                    bbox_inches="tight",
                )
                import matplotlib.pyplot as plt

                plt.close(artifact)
            if total and (i % 20 == 0 or i == total - 1):
                self.progress(lo + int((hi - lo) * (i + 1) / total))
        return total

    def bundle(self, files=None, dirs=None) -> str:
        """Register a downloadable .zip (built on demand by the runtime).

        ``files``: ``{arcname: path}``. ``dirs``: ``{arc_prefix: output_name}`` —
        expanded at download time so it picks up files written by *every* shard.
        """
        import json

        entries = [{"file": p, "arc": arc} for arc, p in (files or {}).items()]
        for arc_prefix, into in (dirs or {}).items():
            d = self._outputs.get(into, os.path.join(self.result_dir, f"{self.id}_{into}"))
            entries.append({"glob": f"{d}/*", "arc_dir": arc_prefix})
        manifest = os.path.join(self.result_dir, f"{self.id}_bundle.json")
        with open(manifest, "w") as fh:
            json.dump({"entries": entries}, fh)
        return manifest


def _load_function(module_file: str, qualname: str):
    module_file = os.path.abspath(module_file)
    directory = os.path.dirname(module_file)
    if directory not in sys.path:
        sys.path.insert(0, directory)

    spec = importlib.util.spec_from_file_location("kinesin_user_module", module_file)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {module_file}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    obj = getattr(module, qualname, None)
    if obj is None:
        # Fall back to the active app's registry (handles the ui.py/compute.py split
        # where the function lives on the app, not as a module attribute by that name).
        from kinesin.app import get_active_app

        app = get_active_app()
        if app is not None and qualname in app._job_funcs:
            return app._job_funcs[qualname]
        raise RuntimeError(f"job '{qualname}' not found in {module_file}")

    # ``obj`` is typically a JobHandle wrapping the real function.
    return getattr(obj, "func", obj)


def main() -> None:
    descriptor: dict[str, Any] = json.load(sys.stdin)
    job_id = descriptor["job_id"]
    result_dir = descriptor.get("result_dir", "storage/results")
    args = descriptor.get("args", [])
    shard = int(descriptor.get("shard", 0))
    num_shards = int(descriptor.get("num_shards", 1))

    # From here on, keep stdout for the protocol only; user prints go to stderr.
    sys.stdout = sys.stderr

    try:
        func = _load_function(descriptor["module_file"], descriptor["qualname"])
        ctx = JobContext(job_id, result_dir, shard, num_shards)
        sig = inspect.signature(func)
        if "job" in sig.parameters:
            result = func(*args, job=ctx)
        else:
            result = func(*args)

        # Outputs may be registered via the job.* helpers and/or returned as a dict.
        path = ctx._result_path
        outputs = dict(ctx._outputs)
        if isinstance(result, dict):
            path = result.get("result_path") or result.get("path") or path
            outputs.update(result.get("outputs") or {})
        protocol.completed(path, outputs)
    except Exception:
        protocol.failed(traceback.format_exc())


if __name__ == "__main__":
    main()
