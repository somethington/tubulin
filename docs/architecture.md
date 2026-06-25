# Kinesin architecture

Kinesin is a Rust runtime with a thin Python authoring layer and a React frontend.
The guiding rule: **Rust is the source of truth**; Python is used only to *describe*
the app once and to *run jobs* in disposable workers.

```
Browser (React)
   |  HTTP: index/assets, POST /upload, GET /preview
   |  WebSocket: /ws  (one per browser session)
   v
Rust runtime  — the `kinesin` binary
   - HTTP + WebSocket server (axum/tokio)
   - AppSpec: UI tree + actions + job catalog + bindings (loaded once)
   - Session manager: per-socket state (ids/paths/small params only)
   - Job manager: bounded queue + worker pool
   - Worker manager: spawns Python, streams its stdout
   |  spawn once:    python -m kinesin.register ui.py   -> AppSpec JSON
   |  spawn per job: python -m kinesin.worker           -> progress/log/result lines
   |  spawn/preview: python -m kinesin.preview <file>   -> first 100 rows
   v
Python layer (registration pass + disposable workers)
   v
storage/{uploads,results,cache}
```

## Lifecycle of `kinesin run ui.py`

1. **Start.** The Rust binary loads `kinesin.toml`, ensures storage dirs exist.
2. **Register.** It runs `python -m kinesin.register ui.py` once. The Python API
   records every widget, the action wired to each, the job catalog, and the
   widget→job bindings, then prints one JSON document ([`AppSpec`]). The process
   exits. Rust now owns the entire app description.
3. **Serve.** Rust starts the HTTP/WS server and serves the built React frontend.
4. **Session.** Each browser connection is a session with its own small state map
   and outbound channel. On connect it receives the `ui_tree`.
5. **Event.** A click or finished upload arrives over the WebSocket. Rust either
   records an uploaded path into session state or executes the node's declarative
   action — *no Python runs in the request path*.
6. **Job.** A `submit_job` action resolves its args from session state, creates a
   `job_id`, and enqueues. When a worker slot is free, Rust spawns
   `python -m kinesin.worker`, feeding it a descriptor on stdin.
7. **Stream.** The worker prints `log`/`progress` lines and a terminal
   `completed`/`failed`. Rust maps these onto the widgets bound to the job's slot
   and pushes `widget_update` messages to the browser. The worker exits, reclaiming
   its memory.
8. **Preview.** On completion Rust runs `python -m kinesin.preview` on the result
   file and pushes the first 100 rows to the bound table widget.

## Why declarative actions

Because Rust owns execution, UI actions must be data, not arbitrary Python
closures. `job.submit(upload.path)` returns a `JobBinding` — a serialisable
`{submit_job, args, result_slot}` — captured at registration. A reference like
`upload.path` becomes `Ref(file_upload_1, path)`, resolved against session state at
submit time. This keeps the request path entirely in Rust and makes future
distributed execution possible.

## The ui.py / compute.py split

A job's `module_file` is the file where the function is **defined**, captured via
`func.__code__.co_filename`. So heavy job code can live in its own `compute.py`,
imported by `ui.py`; the worker imports `compute.py` (not the UI file) to run it.
The UI file stays lightweight.

## Memory discipline

| Rule | Enforcement |
| --- | --- |
| Sessions store only small data | `Session::set_state` rejects values > 64 KiB |
| Uploaded files go to disk | `POST /upload` streams to `storage/uploads/` |
| Job results go to disk | workers write parquet to `storage/results/` |
| Browser gets previews only | `kinesin.preview` returns the first 100 rows |
| Workers are disposable | one process per job; exits when done |

See [mvp.md](mvp.md) for scope and the milestone map.
