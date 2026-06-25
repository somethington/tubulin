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


class JobContext:
    """Passed to jobs that declare a ``job`` parameter."""

    def __init__(self, job_id: str, result_dir: str, shard: int = 0, num_shards: int = 1):
        self.id = job_id
        self.result_dir = result_dir
        # When the runtime parallelises a shardable job, each worker gets a distinct
        # shard index; a job processes items[shard::num_shards]. Defaults: 1 shard.
        self.shard = shard
        self.num_shards = num_shards

    @property
    def is_primary(self) -> bool:
        """True for exactly one shard (shard 0) — owns single-artifact outputs."""
        return self.shard == 0

    def log(self, message: str) -> None:
        protocol.log(message)

    def progress(self, pct: int) -> None:
        protocol.progress(pct)


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

        path = None
        outputs = None
        if isinstance(result, dict):
            path = result.get("result_path") or result.get("path")
            outputs = result.get("outputs")
        protocol.completed(path, outputs)
    except Exception:
        protocol.failed(traceback.format_exc())


if __name__ == "__main__":
    main()
