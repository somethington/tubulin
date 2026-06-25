# Kinesin

A Python-first scientific application framework powered by a Rust runtime.

You write a small Python file that declares a UI and a scientific job. The Rust
runtime owns everything else: the HTTP/WebSocket server, browser sessions, a job
queue, disposable Python workers, and a **disk-backed result lifecycle**. Large data
never lives in session memory — only ids, paths, and small previews cross the wire.

```python
# ui.py
from kinesin import App

app = App("CSV Analyzer")

upload = app.file_upload("Upload CSV")
run = app.button("Analyze")

@app.job
def analyze_csv(path: str, job):
    import polars as pl, time
    job.log("Reading CSV")
    df = pl.read_csv(path)
    job.progress(30)
    time.sleep(2)
    summary = df.describe()
    out = f"{job.result_dir}/{job.id}.parquet"
    summary.write_parquet(out)
    job.progress(100)
    return {"result_path": out}

job = analyze_csv.submit(upload.path)
run.on_click(job)            # clicking the button submits the job
app.job_status(job)          # live status / progress / logs
app.table_preview(job.result_path)   # first 100 rows of the result
```

Run it:

```bash
kinesin run ui.py
# open http://127.0.0.1:8000
```

## Architecture

```
Browser (React)
   ↕  HTTP + WebSocket
Rust runtime  (the `kinesin` binary — source of truth)
   ↕  spawn once:  python -m kinesin.register ui.py   → app spec
   ↕  spawn / job: python -m kinesin.worker  …        → progress + result
Python layer (registration pass + disposable workers)
   ↕
storage/{uploads,results,cache}
```

See [docs/architecture.md](docs/architecture.md) and [docs/mvp.md](docs/mvp.md).

## Developing

```bash
# Rust runtime
cargo build

# Frontend
cd frontend && npm install && npm run build

# Python environment
uv sync

# Run an example (dev: invoke the binary through cargo)
cargo run -p kinesin-runtime -- run examples/csv_analysis/ui.py
```

This is an MVP. It is intentionally small; it is meant to be architecturally correct,
not feature-complete. See [docs/mvp.md](docs/mvp.md) for what is and isn't in scope.
