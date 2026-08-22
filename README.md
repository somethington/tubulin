# Tubulin

A Python-first scientific application framework powered by a Rust runtime.

You write a small Python file that declares a UI and a scientific job. The Rust
runtime owns everything else: the HTTP/WebSocket server, browser sessions, a job
queue, disposable Python workers, and a **disk-backed result lifecycle**. Large data
never lives in session memory — only ids, paths, and small previews cross the wire.

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
    job.result(df.describe())    # writes the parquet; the runtime previews it natively
    job.progress(100)

job = analyze_csv.submit(upload.path)
run.on_click(job)            # clicking the button submits the job
app.job_status(job)          # live status / progress / logs
app.table_preview(job.result_path)   # first 100 rows of the result
```

Hand a job a **dataframe** (`job.result(df)`, `job.table(name, df)`) and the framework
writes the parquet and the preview for you — no manual paths.

Install and run it:

```bash
pip install tubulin
tubulin run ui.py
# open http://127.0.0.1:8000
```

The wheel carries the compiled Rust runtime and the built frontend, so there is no Rust
toolchain and no node install to do.

> **Not on PyPI yet.** The wheels build and install, but nothing has been published, so
> `pip install tubulin` will not resolve until the first release. Build one locally with
> `make wheel` and `pip install dist/tubulin-*.whl`.

Edit `ui.py` and save — the runtime re-registers the app and every open browser picks
up the new UI. A save that doesn't parse keeps the last good version on screen and
shows the traceback as a banner.

## Architecture

```
Browser (React)
   ↕  HTTP + WebSocket
Rust runtime  (the `tubulin` binary — source of truth)
   ↕  spawn once:  python -m tubulin.register ui.py   → app spec
   ↕  spawn / job: python -m tubulin.worker  …        → progress + result
Python layer (registration pass + disposable workers)
   ↕
storage/{uploads,results,cache}
```

## What's here

- **Declarative widgets in Python** — inputs (`checkbox`/`slider`/`number`/`select`/
  `multiselect`/`text_input`/`text_area`/`file_upload`), outputs
  (`table_preview`/`plot`/`gallery`/`download`/`job_status`), rich text via
  `app.markdown(...)`, and **conditional `enabled_when=`** (a widget greys out until
  its controller is on).
- **Layout without CSS** — `app.columns(...)`, `app.tabs(...)`, `app.sidebar()` and
  collapsible `app.group(...)`, all context managers. The spec stays a flat node list,
  so a widget's value behaves identically however deeply it is nested.
- **Dataframe-native job outputs** — `job.result(df)` / `job.table(name, df)` write the
  parquet; the Rust runtime reads the 100-row preview natively (no subprocess); plus
  `job.write_csv` / `job.write_text` / `job.path` for bundle files.
- **Parallel jobs** — `@app.job(parallel=True)` + `job.map(...)` / `job.primary(...)`
  fan work across the machine's cores; the runtime owns sharding.
- **Interactive outputs** — Plotly charts, a paginated/searchable image gallery, and an
  on-demand `.zip` download bundle.
- **Disposable workers** — one process per job, memory reclaimed on exit. On Linux an
  optional **fork-server** amortizes the heavy import (one import, fork per job).
- **Hot reload** — `tubulin run` watches the app's `.py` files and re-registers on
  save; a broken edit surfaces as a banner instead of blanking the page
  (`--no-reload` opts out).
- **Headless test harness** — `tubulin.testing.run_app(...)` runs a job in-process and
  returns its outputs; no browser, no server. See [docs/testing.md](docs/testing.md).
- **Design system** — `@tubulin/design`: dependency-free CSS tokens (light + dark) and a
  Plotly theme. See [design/README.md](design/README.md).

## Documentation

Full docs are in [`docs/`](docs/README.md):

- [architecture.md](docs/architecture.md) — the whole system, lifecycles, memory model, route map, limitations
- [python-api.md](docs/python-api.md) — `App`, widgets (incl. `group` / `enabled_when`), jobs, the `JobContext` primitives
- [parallelism.md](docs/parallelism.md) — parallel jobs, `job.map`/`job.primary`, and the multi-tenant gaps
- [testing.md](docs/testing.md) — the headless `tubulin.testing` harness
- [protocol.md](docs/protocol.md) — the wire formats and HTTP routes
- [runtime-internals.md](docs/runtime-internals.md) — the Rust runtime, module by module (incl. the fork-server)
- [configuration.md](docs/configuration.md) — `tubulin.toml` and env vars
- [roadmap.md](docs/roadmap.md) — framework-improvement plan, plus the tiered checklist for closing the Streamlit adoption gap

Every widget and container in one runnable page: [examples/widgets](examples/widgets/ui.py)
(`tubulin run examples/widgets/ui.py`). A real adapted app (Thermal Proteome Profiling,
with parallel plotting of hundreds of curves) lives in [examples/tpp](examples/tpp/README.md).

## Developing

```bash
# Rust runtime
cargo build

# Frontend
cd frontend && npm install && npm run build

# Python environment
uv sync

# Run an example (dev: invoke the binary through cargo)
cargo run -p tubulin-runtime -- run examples/csv_analysis/ui.py
```

This is a working prototype: it runs one user's app well on one machine. It is
**single-tenant** — the fairness, isolation, and durability a multi-user server needs
are documented as known gaps in
[architecture.md](docs/architecture.md#limitations--what-this-is-not-yet). See
[docs/mvp.md](docs/mvp.md) for the original scope.
