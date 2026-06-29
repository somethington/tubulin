# Architecture

Kinesin is a **Rust runtime** with a thin **Python authoring layer** and a **React
frontend**. The guiding rule: *Rust is the source of truth and owns the lifecycle;
Python is used only to describe the app once and to run jobs.*

```
Browser (React)
   │  HTTP: index/assets, POST /upload, GET /preview /file /download /bundle
   │  WebSocket: /ws  (one connection per browser session)
   ▼
Rust runtime  — the `kinesin` binary, the source of truth
   ├─ HTTP + WebSocket server (axum / tokio)
   ├─ AppSpec        : UI tree + actions + job catalog + bindings (loaded once)
   ├─ Session manager: per-socket state (ids/paths/small params only) + slots
   ├─ Job manager    : bounded queue + worker-pool semaphore + sharding
   └─ Worker manager : spawns Python workers, streams their stdout
   │  spawn once :  python -m kinesin.register ui.py   → AppSpec JSON
   │  spawn /job :  python -m kinesin.worker           → progress/log/result lines
   │  spawn      :  python -m kinesin.preview <file>   → first 100 rows (csv/ipc only;
   │                                                     parquet is read natively in Rust)
   ▼
Python layer (a one-shot registration pass + disposable workers)
   ▼
storage/{uploads,results,cache}   (the only place large data lives)
```

## Components

### 1. The Python authoring layer (`python/kinesin/`)

A small library the app author imports. It has two jobs:

- **Describe the app.** `App(...)` plus widget factory methods (`app.button`,
  `app.file_upload`, `app.gallery`, …) and the `@app.job` decorator accumulate an
  in-memory description of the UI, the actions wired to it, and the job catalog.
- **Run jobs.** In a worker process, the decorated job function executes; a
  `JobContext` (`job`) gives it `log`/`progress` and the parallel primitives
  (`map`/`primary`/`output`/`result`/`bundle`).

The author never starts a server, opens a socket, or touches HTTP. See
[python-api.md](python-api.md).

### 2. The Rust runtime (`crates/kinesin-runtime/`)

The `kinesin` binary. It serves the app, owns sessions and the job queue, spawns and
supervises Python workers, and streams updates to browsers. See
[runtime-internals.md](runtime-internals.md).

### 3. The protocol crate (`crates/kinesin-protocol/`)

The serde types shared inside the runtime: the browser↔runtime messages, the
`AppSpec` loaded from registration, and the worker line protocol. Python and the
frontend mirror these by hand. See [protocol.md](protocol.md).

### 4. The frontend (`frontend/`, React + Vite)

Connects to `/ws`, renders the UI tree, dispatches user events back to the runtime,
and applies `widget_update` patches. One React component per widget kind. Plotly
charts render via `plotly.js`; tables/galleries fetch previews/images on demand.

## The registration pass — how Rust reads `ui.py`

Rust cannot execute Python in-process, so `kinesin run ui.py` spawns a one-shot
`python -m kinesin.register ui.py`. That process imports the UI file; the Python API
records everything into an `App`, then prints **one JSON document** (the `AppSpec`) to
stdout and exits. The runtime deserializes it and serves from it forever after. Any
stray stdout from the user's module is quarantined to stderr so the JSON stays clean.

```json
{
  "title": "CSV Analyzer",
  "nodes":   [ {"id":"button_1","kind":"button","props":{"label":"Run"}}, … ],
  "actions": { "button_1": {"type":"submit_job","job":"analyze",
                            "args":[{"kind":"ref","node":"file_upload_1","field":"path"}],
                            "result_slot":"slot_1"} },
  "jobs":    { "analyze": {"module_file":"/abs/compute.py","qualname":"analyze",
                           "shardable":true} },
  "bindings":{ "status_1":{"slot":"slot_1","view":"status"},
               "table_1": {"slot":"slot_1","view":"result_preview"} }
}
```

Because the **action is declarative data** (not a Python closure), the runtime
executes it itself — there is no Python in the request path. A reference like
`upload.path` becomes `{"kind":"ref","node":"file_upload_1","field":"path"}`, resolved
against session state at submit time.

The `ui.py` / `compute.py` split: a job's `module_file` is the file where the function
is *defined* (captured via `func.__code__.co_filename`). So heavy job code can live in
its own module; the worker imports *that*, not the lightweight UI file.

## Request lifecycle (a button click)

1. Browser sends `{"type":"event","node_id":"button_1","event":"click"}` over `/ws`.
2. The WebSocket handler looks up `actions["button_1"]` in the `AppSpec`.
3. For a `submit_job` action it resolves each arg from **session state**
   (`file_upload_1.path` was written when the user uploaded), mints a `job_id`,
   records `slot → job_id` in the session, and enqueues a `JobRequest`.
4. Bindings tell the runtime which widgets reflect this slot; their updates flow back
   as `widget_update` messages.

## Job lifecycle

```
submit ─► queued ─► running ─► (worker streams log/progress) ─► completed | failed
                                                                     │
                                              dispatch bound widgets (status, preview,
                                              plots, galleries, download)
                                                                     │
                                              report end-to-end ⏱ elapsed
```

- The runtime spawns `python -m kinesin.worker`, writes a JSON **descriptor** to its
  stdin, and reads **one JSON object per line** from its stdout
  (`log` / `progress` / `completed` / `failed`).
- `stderr` is captured for diagnostics; a `max_runtime_seconds` timeout kills runaway
  workers; if a worker dies without a terminal message, the runtime synthesizes a
  failure from stderr.
- On `completed`, the runtime drives each bound output widget by its **view**:
  `result_preview` (parquet → 100-row preview), `plot:<name>` (inline SVG),
  `plotly:<name>` (figure JSON), `table:<name>` (secondary parquet), `gallery:<name>`
  (list a directory of SVGs), `download:<name>` (a `/bundle` link).

See [runtime-internals.md](runtime-internals.md) for the exact functions.

## Parallel jobs (sharding)

A job marked `@app.job(parallel=True)` may be run as **several worker processes at
once**. The runtime — not Python — decides how many:

```
num_shards = min(max_workers, cpu_cores)
```

It spawns that many workers, each handed a distinct `shard` / `num_shards`. The job's
`job.map(items, fn, into=)` processes only `items[shard::num_shards]`; every shard
writes per-item artifacts (e.g. one SVG per protein) into a **shared directory**, and
`job.primary(fn)` runs single-copy work (the stats table, overview plots) on shard 0
only. After all shards finish, the runtime **merges by listing the directory** and
reports the result. No inter-process data passing; the merge is the filesystem.

This is covered in depth — including the honest comparison to `multiprocessing.Pool`
and the multi-tenant gaps — in [parallelism.md](parallelism.md).

## Memory model — why this exists

The narrow thing Kinesin does better than a naive Streamlit-style app is **keep large
data out of session memory**:

| Rule | Enforcement |
| --- | --- |
| Sessions store only small data | `Session::set_state` rejects values > 64 KiB |
| Uploaded files go to disk | `POST /upload` streams to `storage/uploads/` |
| Job results go to disk | workers write parquet/SVG to `storage/results/` |
| Browser gets previews only | `kinesin.preview` returns the first 100 rows; plots are files |
| Workers are disposable | one process per job (or per shard); exits → memory reclaimed |

Measured on the TPP example: a worker peaks at ~224 MB (of which ~181 MB is just the
`matplotlib`/`scipy`/`pandas` import baseline; the actual reshaped data is ~0.6 MB).
Plotting is done one figure at a time, so peak memory is flat regardless of how many
hundreds of curves a job produces — the opposite of holding them all in a dict.

## HTTP routes

| Route | Purpose |
| --- | --- |
| `GET /` + assets | the built React frontend (served from `frontend/dist`) |
| `GET /ws` | per-session WebSocket (ui_tree out, events in, widget_updates out) |
| `POST /upload` | multipart upload → `storage/uploads/<uuid>.<ext>` → returns the path |
| `GET /preview?path=` | first 100 rows of a parquet/csv/arrow file as JSON |
| `GET /file?path=` | serve a result file **inline** (e.g. an SVG in `<img>`) |
| `GET /download?path=` | serve a result file as an **attachment** |
| `GET /bundle?job=` | build (lazily, once) and serve a job's results `.zip` |
| `GET /health` | liveness + app title + session count |

All file routes resolve paths under the project root and reject anything outside it.

## Limitations — what this is *not* yet

Kinesin today is a **single-tenant prototype**. For a multi-user server these are
real, known gaps:

- **Fairness.** One `parallel` job can acquire the whole worker pool; concurrent jobs
  serialize behind it. `decide_shards` is `min(max_workers, cores)` with no per-job
  cap or fair-share. (The default `max_workers=0`/auto is tuned for single-job speed,
  not multi-tenant sharing.)
- **Tenant isolation.** The file routes serve any path/job under the project root —
  any connected client that knows a `job_id` (a uuid) can read another user's
  results. There is no auth and no per-user scoping.
- **Density.** Every worker re-pays the ~181 MB import baseline per job per shard.
  There is no warm/pooled worker that amortizes imports.
- **Durability & scale-out.** The job queue and state are in-memory and single-process
  on one machine; a restart loses everything and there is no horizontal scaling.

The shard descriptor (a JSON unit of work over stdio) is, however, a reasonable
foundation for delegating execution to a real scheduler (Ray/Dask/Celery) later. See
[parallelism.md](parallelism.md) for that discussion.
