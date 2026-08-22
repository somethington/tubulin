# Configuration

## `tubulin.toml`

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
| `PORT` | HTTP port (CLI `--port` wins over this, which wins over `tubulin.toml`) | toml / 8000 |
| `TUBULIN_ROOT` | project root (where `tubulin.toml`, `python/`, `frontend/dist`, `storage/` are resolved) | current working dir |
| `TUBULIN_PYTHON` | the Python interpreter used for register/worker/preview | `<root>/.venv/bin/python` if present, else `python3` |
| `TUBULIN_FRONTEND` | directory of the built frontend to serve | `<root>/frontend/dist` |
| `TUBULIN_FORKSERVER` | force the [fork-server](runtime-internals.md#fork-server-amortizing-the-import) on (`1`) or off (`0`) | on for Linux, else off |

The runtime sets `PYTHONPATH=<root>/python` and `TUBULIN_WORKER` / `TUBULIN_REGISTER`
on the Python processes it spawns, so `import tubulin` resolves to the in-repo package
and the user's module knows which pass it is running under.

## CLI

```
tubulin run <ui.py> [--port <PORT>] [--config <FILE>] [--no-reload]
```

`tubulin run` **watches the app's `.py` files and re-registers on save** — edit `ui.py`
(or a sibling module like `compute.py`), and every open browser gets the new UI tree
without a restart. A save that doesn't parse leaves the last good app serving and
surfaces the traceback as an error banner in the browser, so a half-typed edit never
blanks the page. Pass `--no-reload` to turn the watcher off.

During development (no installed binary on PATH) run it through cargo:

```bash
cargo run -p tubulin-runtime -- run examples/tpp/ui.py --port 8137
```

### How an installed tubulin finds its parts

`pip install tubulin` puts the `tubulin` binary in the environment's `bin/`, next to
that environment's `python`. The runtime resolves, in order:

| | Source checkout | Installed wheel |
| --- | --- | --- |
| Interpreter | `.venv/bin/python`, else `python3` | the `python3` **next to the binary**, so a venv works unactivated |
| `PYTHONPATH` | `<root>/python` | unset — `tubulin` is already importable from site-packages |
| Frontend | `<root>/frontend/dist` | `tubulin/_frontend/` inside the installed package |

`TUBULIN_PYTHON` and `TUBULIN_FRONTEND` override the first and last. Build a wheel
locally with `make wheel` (builds the frontend, stages it into the package, runs
maturin).

## What an app needs at runtime

- The `tubulin` Python package importable (via `PYTHONPATH`, set automatically).
- The app's own scientific dependencies importable by the chosen interpreter. Today
  these are installed into `.venv` by hand (e.g. `uv pip install pandas scipy …`);
  declaring them per-app is a planned improvement (see the roadmap discussion).
- A built frontend at `frontend/dist` (the runtime serves it; it does not build it).
