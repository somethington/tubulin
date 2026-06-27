# Configuration

## `kinesin.toml`

Looked up at the project root (or `--config <path>`). Every value shown is the
built-in default; the file is optional.

```toml
[server]
host = "127.0.0.1"
port = 8000

[workers]
# Max concurrent Python worker processes. Also bounds how many shards a parallel job
# fans out into. 0 = auto (use all CPU cores, like mp.Pool(cpu_count())).
max_workers = 0

[jobs]
max_runtime_seconds = 3600   # a worker exceeding this is killed
max_queue_size      = 100    # reject new submissions past this many in-flight jobs

[storage]
upload_dir = "storage/uploads"
result_dir = "storage/results"
cache_dir  = "storage/cache"
```

Notes:

- **`max_workers = 0`** resolves to the machine's core count. This maximizes a single
  job's speed but, on a shared server, lets one parallel job monopolize the pool — set
  a fixed number to bound per-job parallelism (see [parallelism.md](parallelism.md)).
- Relative `storage` paths resolve against the **project root**; absolute paths are
  used as-is. The storage dirs are created on startup.

## Environment variables

| Variable | Effect | Default |
| --- | --- | --- |
| `PORT` | HTTP port (CLI `--port` wins over this, which wins over `kinesin.toml`) | toml / 8000 |
| `KINESIN_ROOT` | project root (where `kinesin.toml`, `python/`, `frontend/dist`, `storage/` are resolved) | current working dir |
| `KINESIN_PYTHON` | the Python interpreter used for register/worker/preview | `<root>/.venv/bin/python` if present, else `python3` |
| `KINESIN_FRONTEND` | directory of the built frontend to serve | `<root>/frontend/dist` |
| `KINESIN_FORKSERVER` | force the [fork-server](runtime-internals.md#fork-server-amortizing-the-import) on (`1`) or off (`0`) | on for Linux, else off |

The runtime sets `PYTHONPATH=<root>/python` and `KINESIN_WORKER` / `KINESIN_REGISTER`
on the Python processes it spawns, so `import kinesin` resolves to the in-repo package
and the user's module knows which pass it is running under.

## CLI

```
kinesin run <ui.py> [--port <PORT>] [--config <FILE>]
```

During development (no installed binary on PATH) run it through cargo:

```bash
cargo run -p kinesin-runtime -- run examples/tpp/ui.py --port 8137
```

## What an app needs at runtime

- The `kinesin` Python package importable (via `PYTHONPATH`, set automatically).
- The app's own scientific dependencies importable by the chosen interpreter. Today
  these are installed into `.venv` by hand (e.g. `uv pip install pandas scipy …`);
  declaring them per-app is a planned improvement (see the roadmap discussion).
- A built frontend at `frontend/dist` (the runtime serves it; it does not build it).
