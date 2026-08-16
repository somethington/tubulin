# Runtime internals (Rust)

The `kinesin` binary, in [`crates/kinesin-runtime/src/`](../crates/kinesin-runtime/src).
Built on **axum** (HTTP/WS) + **tokio**. This is a map for someone reading or changing
the runtime.

## Module map

| Module | Responsibility |
| --- | --- |
| `main.rs` | entry; tracing init; parse CLI; load config; register; serve |
| `cli.rs` | hand-rolled arg parser for `run <ui.py> [--port] [--config]` |
| `config/` | `mod.rs` loads `kinesin.toml` + defaults (canonicalized project root, Python interpreter, `max_workers` 0 = auto-cores); `raw.rs` is the on-disk TOML shape |
| `registry.rs` | spawn `python -m kinesin.register`, parse the `AppSpec` |
| `state.rs` | `AppState { config, spec, jobs, sessions }` shared via `Arc` |
| `http/` | `mod.rs` axum router (`/health`, `/ws`, static); `upload.rs` streaming multipart; `files.rs` containment-checked `/preview`, `/file`, `/download`; `bundle.rs` lazy `/bundle` builds; `websocket.rs` per-session loop; `events.rs` event → state/action dispatch |
| `session/` | `mod.rs` `Session` (state map + slots + outbound channel) and `SessionManager`; `actions.rs` resolves `ArgSpec` args and builds the `JobSink` from bindings |
| `jobs/` | `mod.rs` `JobManager` (bounded queue + semaphore); `runner.rs` single-worker path + shared `finish`; `sharded.rs` parallel fan-out with averaged progress; `outputs.rs` drives bound widgets; `records.rs` bounded job-record store |
| `workers/` | `mod.rs` `run_worker` + line-protocol pump; `socket.rs` fork-server transport; `process.rs` disposable `python -m kinesin.worker`; `forkserver.rs` `ForkServerManager` (Linux-gated, evicts dead servers) |
| `artifacts/` | `preview.rs` preview entry point (Python fallback for csv/ipc); `parquet.rs` native arrow/parquet preview (first 100 rows); `bundle.rs` native zip builder |

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
   │     result_preview → send_table (native parquet read; Python fallback for csv/ipc)
   │     plot:<n>       → send_plot   (read outputs[n] SVG, inline)
   │     plotly:<n>     → send_plotly (read outputs[n] JSON, parse)
   │     table:<n>      → send_table  (native parquet read, on outputs[n])
   │     gallery:<n>    → send_gallery (list outputs[n] directory → items)
   │     download:<n>   → send_download (/bundle?job=<id> link)
   └─ emit ⏱ elapsed (Instant since the job started)
```

`run_worker` (in `workers/mod.rs`) builds the job descriptor, then takes one of two
transports — both funnel through the same `WorkerMessage` pump:

- **Fork-server (preferred on Linux).** `ForkServerManager::socket_for(module_file)`
  returns a Unix-socket path, lazily starting one `python -m kinesin.forkserver
  <module>` per module file (it imports the module once, waits for a readiness line).
  `workers/socket.rs` connects, sends the descriptor as one line, half-closes, and reads
  the protocol back. If the socket can't be reached the dead server is evicted (so the
  next job restarts it) and the call falls through.
- **Process (fallback / non-Linux).** `workers/process.rs` spawns `python -m
  kinesin.worker` with `PYTHONPATH` set and `cwd = project_root`, writes the descriptor
  to stdin and closes it, drains stderr concurrently, and reads stdout.

Either way the read loop is wrapped in a `max_runtime_seconds` timeout. If the worker
exits without a terminal message, the runtime awaits the drained stderr and the exit
status: only a clean exit with empty stderr synthesizes `Completed` (empty result);
anything else synthesizes `Failed` with the exit code and stderr.

## Fork-server (amortizing the import)

A plain worker pays the full cold import of the user's module (polars/scipy/matplotlib,
~1–3 s) on every job; for a parallel job, every shard pays it. The fork-server imports
the module **once** in a resident parent, then `fork()`s a fresh child per job. The
child inherits the warm import via copy-on-write, runs one job, and exits — so memory
is still reclaimed by process exit and **no worker is ever reused** (the parent never
runs job code; this is *not* a warm worker pool). Measured ~33% off the TPP job on
Linux.

Gated to Linux by default (`ForkServerManager::new` checks `cfg!(target_os = "linux")`;
`KINESIN_FORKSERVER=1/0` overrides). On macOS it stays off because scipy/numpy LAPACK
via Apple's Accelerate framework segfaults in a forked child regardless of thread
settings — the Python side forces native libs single-threaded
(`POLARS_MAX_THREADS=1`, …) to be fork-safe on Linux's OpenBLAS.

## Resource allocation

`decide_shards(config) = min(config.max_workers, available_parallelism()).max(1)`. The
semaphore (`max_workers` permits) bounds *total* concurrent workers, so a parallel job
that takes N permits also caps how many other jobs run alongside it. **This couples
per-job parallelism with global concurrency** — fine for one user, wrong for many (see
[parallelism.md](parallelism.md) and the limitations in
[architecture.md](architecture.md#limitations--what-this-is-not-yet)).

## Serving result files

`http/files.rs::serve_file(state, path, req, attachment)` backs both `/file` (inline) and
`/download` (attachment): canonicalize the path, reject anything not under
`project_root`, then delegate to tower-http's `ServeFile` — streaming, mime-guessed
content-type, and Range/conditional-request support (resumable downloads) — with
`Content-Disposition` appended. `/bundle?job=<id>` checks for
`<result_dir>/<job_id>_results.zip`; if absent it builds the zip **natively in Rust**
([`artifacts/bundle.rs`](../crates/kinesin-runtime/src/artifacts/bundle.rs), on a blocking thread,
serialized behind `AppState::bundle_lock` so concurrent requests can't corrupt one
`.partial`) from `<job_id>_bundle.json` — expanding the manifest's directory globs
(Python-glob semantics: `*` skips dotfiles, directories are skipped, relative entries
resolve against the project root) — then streams it.

Result previews (`/preview` and slot-bound table widgets) read parquet **natively in
Rust** ([`artifacts/parquet.rs`](../crates/kinesin-runtime/src/artifacts/parquet.rs)) — no subprocess. Only
non-parquet formats (csv/ipc) fall back to `python -m kinesin.preview`. Uploads are
streamed straight to disk (a guard removes the partial file if the client aborts), and
text-ish responses are gzip/br-compressed (already-compressed zips and binary result
files are exempt).
