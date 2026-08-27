# Tubulin

**Python-first scientific apps, on a Rust runtime.**

You write one Python file describing a UI and a job. The Rust `tubulin` binary owns
everything else — the HTTP/WebSocket server, browser sessions, the job queue, and the
disposable Python worker processes that run your compute.

```python
# ui.py
from tubulin import App

app = App("CSV Analyzer")

upload = app.file_upload("Upload CSV")
run = app.button("Analyze")

@app.job
def analyze_csv(path: str, job):
    import polars as pl
    job.log("Reading CSV")
    df = pl.read_csv(path)
    job.progress(50)
    job.result(df.describe())       # writes the parquet; the runtime previews it
    job.progress(100)

job = analyze_csv.submit(upload.path)
run.on_click(job)                    # clicking the button submits the job
app.job_status(job)                  # live status / progress / streamed logs
app.table_preview(job.result_path)   # first 100 rows of the result
```

```bash
tubulin run ui.py     # → http://127.0.0.1:8000
```

Edit `ui.py` and save: the app re-registers and every open browser picks up the new UI.
A save that doesn't parse keeps the last good version on screen and shows the traceback
as a banner.

## Why not just use Streamlit

Streamlit re-runs your whole script on every interaction. That model is excellent for
dashboards and awkward for the work this is built for: a curve fit over a gigabyte of
measurements that takes ten minutes and wants every core on the machine.

Tubulin makes a different trade. Your Python file is a **description**, evaluated once
to produce an app spec; after that there is no Python in the request path. Compute goes
to a **job** — a separate process the runtime schedules, shards across cores, streams
progress from, and lets exit so the OS reclaims its memory.

The consequences worth knowing before you pick it up:

- **Large data never lives in session memory.** Sessions reject values over 64 KiB;
  results live on disk and only ids, paths and small previews cross the wire. A job that
  loads 8 GB doesn't grow the server.
- **Workers are disposable** — one process per job, memory reclaimed on exit, never
  pooled or reused. On Linux an optional fork-server amortizes the heavy import
  (one `import scipy`, then a fork per job) without keeping any worker alive.
- **Nothing happens between widgets without a job.** Deriving one widget's options from
  another's value isn't supported yet — see [the roadmap](docs/roadmap.md). This is the
  cost of not having a rerun loop, and it is a real limitation, not an oversight.
- **It is single-tenant by design.** No auth, no fair-share scheduling, in-memory queue.
  One person's apps on one machine. See
  [limitations](docs/architecture.md#limitations--what-this-is-not-yet).

## Install

Not on PyPI yet — the name is secured and the release pipeline builds and smoke-tests
wheels for Linux and macOS (x86_64 + arm64), but nothing has been published. Until then,
build a wheel from a checkout:

```bash
git clone https://github.com/somethington/tubulin && cd tubulin
make wheel && pip install dist/tubulin-*.whl
```

That needs a Rust toolchain and node **once**, to build the wheel. The wheel itself
carries the compiled runtime and the built frontend, so installing it needs neither.

Python 3.10+. Windows is not supported: the worker socket transport is built on Unix
domain sockets and needs a port, not a matrix entry
(see [T0.4 in the roadmap](docs/roadmap.md)).

## How it works

```
Browser (React)
   ↕  HTTP + WebSocket
Rust runtime  (the `tubulin` binary — the source of truth)
   ↕  once at startup:  python -m tubulin.register ui.py   → app spec
   ↕  once per job:     python -m tubulin.worker  …        → progress + result
Python layer (registration pass + disposable workers)
   ↕
storage/{uploads,results,cache}
```

Rust can't run Python in-process, so `tubulin run ui.py` spawns the registration pass,
which imports your file and prints one JSON app spec: the widget tree, the job
catalogue, and the actions wired to each widget. Actions are **data**, not closures —
the runtime executes them itself. That is also the whole of hot reload: on save, run the
registration pass again and push the new tree.

## What's in it

- **Widgets in Python** — `checkbox` / `slider` / `number` / `select` / `multiselect` /
  `text_input` / `text_area` / `file_upload`, rich text with `app.markdown`, callouts
  (`app.info` / `warning` / `error` / `success`) and metric tiles, plus `enabled_when=`
  to grey a widget out until its controller is on.
- **Layout without CSS** — `app.columns(...)`, `app.tabs(...)`, `app.sidebar()` and
  collapsible `app.group(...)`, all context managers. The spec stays a flat node list, so
  a widget's value behaves identically however deeply it is nested.
- **Dataframe-native outputs** — hand `job.result(df)` or `job.table(name, df)` a polars
  or pandas frame and the framework writes the parquet; the runtime reads the 100-row
  preview natively in Rust, no subprocess.
- **Parallel jobs** — `@app.job(parallel=True)` with `job.map(...)` / `job.primary(...)`.
  The runtime decides the shard count, keeps the primary shard out of the fan-out, and
  aggregates progress. No scheduling knobs in your app.
- **Interactive outputs** — Plotly charts, a paginated searchable image gallery, and an
  on-demand `.zip` bundle built natively.
- **A headless test harness** — `tubulin.testing.run_app(...)` runs a job in-process
  through the same execution core a real worker uses, and hands back its outputs. No
  browser, no server. This is the intended way to test an app.

## Documentation

| | |
| --- | --- |
| [architecture.md](docs/architecture.md) | the whole system, lifecycles, memory model, routes, limitations |
| [python-api.md](docs/python-api.md) | `App`, every widget, jobs, and the `JobContext` primitives |
| [parallelism.md](docs/parallelism.md) | parallel jobs, `job.map` / `job.primary`, sharding |
| [testing.md](docs/testing.md) | the headless harness |
| [protocol.md](docs/protocol.md) | wire formats and HTTP routes |
| [runtime-internals.md](docs/runtime-internals.md) | the Rust runtime, module by module |
| [configuration.md](docs/configuration.md) | `tubulin.toml` and env vars |
| [roadmap.md](docs/roadmap.md) | what's built, what isn't, and why |

Every widget and container on one runnable page:

```bash
tubulin run examples/widgets/ui.py
```

A real adapted application — Thermal Proteome Profiling, fitting and plotting hundreds
of melting curves in parallel — lives in [examples/tpp](examples/tpp/README.md).

## Development

```bash
uv sync --group examples          # Python env
cd frontend && npm install        # frontend deps
cargo build                       # the runtime

cargo run -p tubulin-runtime -- run examples/csv_analysis/ui.py
```

`make test-all` and `make lint-all` are exactly what CI runs — Rust tests, the Python
suite including the TPP integration test on 3.10 and 3.13, vitest, clippy, ruff, `tsc`,
and rustfmt. CI invokes these same targets rather than restating them, so if they pass
locally they pass in CI.

## Status

A working prototype that runs one person's apps well on one machine. The framework
internals it set out to solve — disposable workers, sharded parallelism, native parquet
previews, hot reload, a headless harness — are built and tested. What's thinner is the
surface an app author touches: multipage apps, reactive widgets and interactive tables
are all still ahead. [docs/roadmap.md](docs/roadmap.md) is honest about both.

## License

MIT.
