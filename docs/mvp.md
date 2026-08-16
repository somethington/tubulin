# Kinesin MVP

## What it proves

> A user builds a simple scientific web app in Python, while Rust manages the
> server, sessions, WebSocket communication, job execution, and result lifecycle.

The narrow win over Streamlit: **long-running jobs with controlled memory and
persistent, disk-backed results.**

## Milestones (all implemented)

| # | Milestone | Where |
| --- | --- | --- |
| 1 | Static UI from Python | `register.py` → `AppSpec` → React renders `ui_tree` |
| 2 | WebSocket events | `Button` → `event` → `http/websocket.rs` |
| 3 | Runtime executes an action | `set_text` action (see `examples/hello_world`) |
| 4 | File upload to disk | `POST /upload` → `storage/uploads/`, path in session |
| 5 | Job queue | `submit_job` → `JobManager` (`jobs/mod.rs`) |
| 6 | Python worker | `worker.py` runs the job, writes parquet, streams JSON |
| 7 | Result preview | `preview.py` → first 100 rows → `TablePreview` |
| 8 | Multi-session isolation | one `Session` per socket; results keyed by `job_id` |

## Run it

```bash
cargo build
cd frontend && npm install && npm run build && cd ..
uv sync

# Dev: invoke the binary through cargo
cargo run -p kinesin-runtime -- run examples/csv_analysis/ui.py
# open http://127.0.0.1:8000
```

The runtime finds Python at `.venv/bin/python` (override with `KINESIN_PYTHON`) and
imports the package from `python/` via `PYTHONPATH`. The project root defaults to
the current directory (override with `KINESIN_ROOT`); the frontend is served from
`frontend/dist` (override with `KINESIN_FRONTEND`).

## Explicitly out of scope (MVP)

Auth, cloud deploy, hot-reload of `ui.py`, a reactive dependency graph, dataframe
virtualization, job cancellation UI, multi-machine scheduling, embedding the
frontend in the binary, and a published Python wheel. Actions are restricted to
`submit_job` and `set_text`; arbitrary runtime Python callbacks are intentionally
not supported.

## Example apps

- `examples/hello_world` — milestones 1-3, no job.
- `examples/long_job` — a 5-second job streaming progress, then a small preview.
- `examples/csv_analysis` — the headline demo: upload → analyze → preview.
- `examples/tpp` — a real Streamlit app ([tpp_solver](https://github.com/somethington/tpp_solver),
  Thermal Proteome Profiling) adapted to Kinesin, with a benchmark showing identical
  scientific metrics at ~45× less peak Python heap and ~4× less RSS. See
  [examples/tpp/README.md](../examples/tpp/README.md).
