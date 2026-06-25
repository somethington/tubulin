# Runtime internals (Rust)

The `kinesin` binary, in [`crates/kinesin-runtime/src/`](../crates/kinesin-runtime/src).
Built on **axum** (HTTP/WS) + **tokio**. This is a map for someone reading or changing
the runtime.

## Module map

| Module | Responsibility |
| --- | --- |
| `main.rs` | entry; tracing init; parse CLI; load config; register; serve |
| `cli.rs` | hand-rolled arg parser for `run <ui.py> [--port] [--config]` |
| `config.rs` | load `kinesin.toml` + defaults; resolve paths, Python interpreter, `max_workers` (0 = auto-cores) |
| `registry.rs` | spawn `python -m kinesin.register`, parse the `AppSpec` |
| `state.rs` | `AppState { config, spec, jobs, sessions }` shared via `Arc` |
| `server.rs` | axum router + handlers: `/upload`, `/preview`, `/file`, `/download`, `/bundle`, `/health`, `/ws`, static |
| `websocket.rs` | per-session loop: send `ui_tree`, receive events, run actions, push updates |
| `session.rs` | `Session` (state map + slots + outbound channel) and `SessionManager` |
| `actions.rs` | resolve `ArgSpec` args from session state; build the `JobSink` from bindings |
| `jobs.rs` | `JobManager`: queue, semaphore, sharding, worker streaming, output dispatch |
| `worker.rs` | spawn a Python worker, feed the descriptor, stream its stdout lines, enforce timeout |
| `preview.rs` | run `python -m kinesin.preview`, return the JSON |

## Key types

- **`Config`** — host/port, `max_workers`, `max_runtime_seconds`, `max_queue_size`,
  the storage dirs, the resolved Python interpreter + `PYTHONPATH`, the frontend dir.
- **`AppState`** — `config: Arc<Config>`, the immutable `AppSpec`, the `JobManager`,
  the `SessionManager`. Cloned (Arc) into every handler.
- **`Session`** — `state: Mutex<HashMap<String, Value>>` (refs; rejects > 64 KiB),
  `slots: Mutex<HashMap<String,String>>` (slot → job_id), `tx` (outbound channel to
  this browser).
- **`JobManager`** — `sem: Semaphore(max_workers)`, an `active` counter (queue cap),
  and a `records` map (`JobRecord` per job for introspection).
- **`JobSink`** — where a job's updates go: the session `tx`, plus `status_nodes` and
  `result_nodes: Vec<(node_id, view)>` derived from the bindings for its slot.
- **`JobRequest`** — `{ job_id, job_def, args, sink }`.

## How a job runs

```
JobManager::submit(req)
   ├─ queue-cap check; insert JobRecord(Queued)
   └─ tokio::spawn:
        if job_def.shardable → run_sharded_job(...)
        else                 → run_job(...)

run_job (single worker)
   ├─ status Queued → acquire permit → status Running
   ├─ run_worker(config, id, def, args, shard=0, num_shards=1, on_msg)
   │     on_msg: log → push+emit; progress → emit; completed → capture; failed → capture
   └─ finish_job(...)

run_sharded_job (parallel)
   ├─ shards = decide_shards(config) = min(max_workers, cores)
   ├─ status: "Runtime allocating N workers …"
   ├─ spawn N tasks, each: acquire permit → run_worker(shard=i, num_shards=N)
   │     progress: each shard reports 0–100 of its slice → runtime shows the average
   │     only shard 0's logs/outputs/result_path are kept (the others just write curves)
   ├─ await all; collect shard 0's outputs + any failure
   └─ finish_job(...)

finish_job(config, sink, records, id, started, result_path, outputs, failure)
   ├─ on failure: status Failed + message; return
   ├─ status Completed (progress 100)
   ├─ for each (node_id, view) in sink.result_nodes:
   │     result_preview → send_table (run_preview on result_path)
   │     plot:<n>       → send_plot   (read outputs[n] SVG, inline)
   │     plotly:<n>     → send_plotly (read outputs[n] JSON, parse)
   │     table:<n>      → send_table  (run_preview on outputs[n])
   │     gallery:<n>    → send_gallery (list outputs[n] directory → items)
   │     download:<n>   → send_download (/bundle?job=<id> link)
   └─ emit ⏱ elapsed (Instant since the job started)
```

`run_worker` (in `worker.rs`): spawns `python -m kinesin.worker` with `PYTHONPATH`
set and `cwd = project_root`, writes the descriptor to stdin and closes it, drains
stderr concurrently, reads stdout line-by-line into `WorkerMessage`s, wraps the read
loop in a `max_runtime_seconds` timeout (kills the child on elapse), and synthesizes a
`Failed`/`Completed` if the worker exits without a terminal message.

## Resource allocation

`decide_shards(config) = min(config.max_workers, available_parallelism()).max(1)`. The
semaphore (`max_workers` permits) bounds *total* concurrent workers, so a parallel job
that takes N permits also caps how many other jobs run alongside it. **This couples
per-job parallelism with global concurrency** — fine for one user, wrong for many (see
[parallelism.md](parallelism.md) and the limitations in
[architecture.md](architecture.md#limitations--what-this-is-not-yet)).

## Serving result files

`server.rs::serve_file(state, path, attachment)` backs both `/file` (inline) and
`/download` (attachment): canonicalize the path, reject anything not under
`project_root`, read it, set content-type by extension. `/bundle?job=<id>` checks for
`<result_dir>/<job_id>_results.zip`; if absent it runs `python -m kinesin.zipjob
<job_id>_bundle.json <…>.zip` (expanding the manifest's directory globs so it captures
every shard's curves), then serves the zip.
