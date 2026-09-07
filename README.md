# Tubulin

**Framework for making scientific python apps**

The project aims to create a simple framework that will promote more efficient usage of resources without the need of messing with GIL or multi processing in python, which can be problematic when applied in tandem with Streamlit. This is achieved by using a Rust server that handles the creation of disposable python workers with minimal memory duplication (currently only truly supported on Linux). By using **Tubulin**, less experienced developers and scientists can create simple python apps without fiddling with the aforementioned efficiency entry barriers. 

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

# TODO


## Install

# TODO

```bash
git clone https://github.com/somethington/tubulin && cd tubulin
make wheel && pip install dist/tubulin-*.whl
```

That needs a Rust toolchain and node **once**, to build the wheel. The wheel itself
carries the compiled runtime and the built frontend, so installing it needs neither.

Python 3.10+. Windows is not supported: the worker socket transport is built on Unix
domain sockets and needs a port

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

# TODO

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

## License

MIT.
