# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Kinesin is a Python-first scientific app framework powered by a Rust runtime. App authors write one Python file (`ui.py`) declaring widgets and jobs; the Rust `kinesin` binary owns everything else: the HTTP/WebSocket server, sessions, the job queue, and disposable Python worker processes. The core design rule: **Rust is the source of truth; Python only describes the app once and runs jobs. Large data never lives in session memory** — only ids, paths, and small previews cross the wire (sessions reject values > 64 KiB; results live in `storage/`).

## Commands

```bash
# Rust runtime
cargo build
cargo test                        # unit tests in kinesin-protocol, session/actions.rs, artifacts/{bundle,parquet}
cargo test -p kinesin-runtime parquet   # one module's tests

# Python layer
uv sync                           # creates .venv with polars/pyarrow/pydantic + pytest
PYTHONPATH=python .venv/bin/python -m pytest tests/                       # all tests
PYTHONPATH=python .venv/bin/python -m pytest tests/test_kinesin.py::test_registration_spec_shape  # one test

# Frontend (runtime serves the built output from frontend/dist)
cd frontend && npm install && npm run build

# Run an example app end-to-end (dev)
cargo run -p kinesin-runtime -- run examples/csv_analysis/ui.py
# open http://127.0.0.1:8000
```

Configuration comes from `kinesin.toml` (host/port, `max_workers`, job timeout/queue caps, storage dirs); the checked-in file documents all defaults.

## Architecture

Three layers, connected by JSON protocols (full details: `docs/architecture.md`, `docs/runtime-internals.md`, `docs/protocol.md`):

- **Rust runtime** (`crates/kinesin-runtime/`) — axum/tokio server, organized by folder: `http/` (router in `mod.rs`, plus `upload.rs`, `files.rs`, `bundle.rs`, `websocket.rs`, `events.rs` — routes: `/upload`, `/preview`, `/file`, `/download`, `/bundle`, `/health`, `/ws`), `session/` (`mod.rs` session state, `actions.rs` resolves declarative actions), `jobs/` (`mod.rs` queue + semaphore, `runner.rs`/`sharded.rs` execution, `outputs.rs` widget dispatch, `records.rs` bounded record store), `workers/` (`mod.rs` + `process.rs`/`socket.rs` transports, `forkserver.rs` Linux-only warm-import fork server), `artifacts/` (`preview.rs`/`parquet.rs` native parquet preview with Python fallback for csv/ipc, `bundle.rs` native zip builder), `config/`.
- **Python layer** (`python/kinesin/`) — `app.py`/`widgets.py`/`jobs.py` build an in-memory app description; `register.py` prints it as one AppSpec JSON; `worker.py` executes a job and streams JSON lines (`log`/`progress`/`completed`/`failed`) over stdout; `testing.py` is the headless harness.
- **React frontend** (`frontend/`, Vite + TS) — connects to `/ws`, renders the node tree, shallow-merges `widget_update` patches. One component per widget kind in `frontend/src/widgets/`. Styling comes from `@kinesin/design` (`design/tokens.css`, CSS custom properties, light + dark).

### Key mechanisms

- **Registration pass**: Rust can't run Python in-process, so `kinesin run ui.py` spawns `python -m kinesin.register ui.py` once, which imports the UI file and prints the AppSpec (UI tree + actions + job catalog + bindings). Actions are declarative data, not closures — the runtime executes them itself; there is no Python in the request path. A job's `module_file` is the file where the function is *defined*, so heavy compute can live outside `ui.py`.
- **Job lifecycle**: submit → queued → running (worker streams stdout lines) → completed/failed. On completion the runtime drives each bound output widget by its view (`result_preview`, `plot:<name>`, `plotly:<name>`, `table:<name>`, `gallery:<name>`, `download:<name>`).
- **Parallel jobs**: `@app.job(parallel=True)` fans out as `min(max_workers, cores)` shard workers. `job.map(items, fn, into=)` processes `items[shard::num_shards]`; `job.primary(fn)` runs on shard 0 only; shards write artifacts into a shared directory and the runtime merges by listing it — the filesystem is the merge.
- **Dataframe-native outputs**: `job.result(df)` / `job.table(name, df)` write parquet; the runtime previews the first 100 rows natively in Rust.

### Protocol mirroring — cross-cutting invariant

The canonical wire types live in `crates/kinesin-protocol/src/` (`messages.rs`, `spec.rs`, `worker.rs`, re-exported from `lib.rs`). Python (`python/kinesin/protocol.py`) and the frontend (`frontend/src/types.ts`) mirror them **by hand**. Any change to a message shape, AppSpec field, or the worker line protocol must be applied in all three places, and `docs/protocol.md` updated.

## Testing apps headlessly

Prefer `kinesin.testing.run_app(ui_path, inputs={...}, shards=N)` for job/regression tests — it runs the job in-process through the same execution core as a real worker (no server/browser) and returns a `RunResult` (`r.result`, `r.table(name)`, `r.gallery(name)`, `r.logs`). `inputs` is keyed by widget id or label and raises on unknown keys. `shards=N` runs shards sequentially to test sharding correctness. See `docs/testing.md`. A realistic example app with tests is `examples/tpp/`.

## Scope notes

- This is a deliberate **single-tenant prototype**: no auth, no fair-share scheduling, in-memory queue, file routes scoped only to the project root. These gaps are documented in `docs/architecture.md#limitations` and `docs/roadmap.md` — don't "fix" them in passing, and don't build on assumptions of multi-tenancy.
- The `docs/` directory is maintained deliberately and per-module; when changing behavior, update the matching doc.
