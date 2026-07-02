# Kinesin framework roadmap

Improvements distilled from building the TPP example app. The recurring lesson:
**performance and correctness kept turning out to be framework concerns masquerading
as app concerns.** Several times the app author had to become an expert (matplotlib
internals, preview subprocesses, process/import cost) to get a good result. A good
framework should absorb that. Items are ordered by leverage; effort and any tension
with existing constraints are noted.

## 1. Headless test/eval harness — ✅ done

**Pain:** every measurement while building TPP — elapsed, table/gallery counts,
numeric parity — required hand-writing a WebSocket client to click the run button
and parse `widget_update`s. There was no way to drive an app without a browser.

**Shipped:** [`kinesin.testing`](../python/kinesin/testing.py). Runs an app's job
in-process through the same execution core the worker uses
(`kinesin.worker.execute_job`), so output-collection semantics are identical.

```python
from kinesin.testing import run_app

r = run_app("examples/tpp/ui.py", inputs={"Imputation seed": 7, "number_2": 8})
assert r.result is not None              # primary result table written
df = r.table("averaged")                 # named table output -> polars DataFrame
assert df.height > 0 and r.elapsed < 30
assert len(r.gallery("avg_curves")) > 0  # files a job.map(into=...) accumulated
```

Plus a CLI for quick benchmarks: `python -m kinesin.testing <ui.py> [key=value ...]`.
Inputs override widget values by **id or label**; unknown keys raise (no silent
drops). `shards=N` runs the sharding logic sequentially for correctness — it is *not*
the runtime's parallel wall-clock (use the real runtime for that number).

> Building this immediately caught a real bug: apps keep their `App` as a module
> singleton (`app_def.py`), so re-importing `ui.py` duplicated every widget/action.
> `load_app` now evicts the app's own modules to construct a fresh app each run.

## 2. Dataframe-native job outputs — ✅ done

**Pain:** `compute.py` was full of `_pq(path, df)` / `_txt(path, s)` helpers and manual
`f"{base}_averaged.parquet"` pathing; the author managed `result_dir`, parquet
writing, and preview sidecars by hand.

**Shipped:** `job.result(df)` and `job.table(name, df)` accept a **dataframe** (polars
or pandas) — the framework writes the parquet under `result_dir` and the preview
sidecar in one call (a path still works too). Plus `job.write_csv(name, df)`,
`job.write_text(name, content)`, and `job.path(name)` own result-dir pathing for
bundle files. TPP's `compute.py` dropped both `_pq` and `_txt` and the manual `base`
pathing. Verified end-to-end via the #1 harness (`examples/tpp/test_tpp.py`).

## 3. Performance-safe plotting defaults — small effort, high payoff

**Pain:** the framework *forced* `bbox_inches="tight"` (a hidden 2× render trap) and
default font embedding (2× file size). We only found it by benchmarking; no app
author should have to.

**Add:** bake the fast defaults into the framework's figure-saving (no `tight`,
`svg.fonttype="none"`) and ship a `kinesin.reserve_legend(fig)` helper so outside
legends don't clip without the tight cost. Make "fast and correct" the default.
(Applied ad-hoc in TPP — generalize it.)

## 4. Fork-server workers — ✅ done (Linux-validated)

**Pain:** measured ~1–3s cold import of polars+scipy+matplotlib *per worker* — the
largest fixed slice of the job time, paid by every shard.

**Built:** [`kinesin.forkserver`](../python/kinesin/forkserver.py) — a resident parent
imports the user's module **once**, then `fork()`s a fresh child per job. The child
inherits the warm import via copy-on-write, runs one job, and exits — memory is still
reclaimed by process exit and **no worker is reused** (the parent never runs job code,
so this is *not* a warm worker pool). One Unix-socket connection == one job == one
child, streaming the same worker protocol lines.

**Measured (TPP, macOS, warm OS cache):** cold worker ≈ 3.65s/job vs fork ≈ 2.36s/job —
**~1.3s (35%) saved per job** plus a one-time server import; and it collapses an
N-shard job's N cold imports into one. A fork-safe job completes in ~47ms/fork.

**The hazard, confirmed:** `fork()` after native thread pools / fork-hostile
frameworks. Forcing single-threaded native libs (`POLARS_MAX_THREADS=1` etc., set
before import) covers polars/OpenBLAS, and polars `write_parquet` + matplotlib + scipy
`curve_fit` all ran fine in the forked child. But on **macOS**, scipy LAPACK calls
(`solve_triangular` via `gaussian_kde`, used by the violin + distribution plots) go
through Apple's **Accelerate** framework, which segfaults in a forked child regardless
of thread settings — a documented macOS limitation (why CPython defaults to `spawn`
there). So the full TPP job can't complete via fork on macOS.

**Runtime wiring (done):** `crates/kinesin-runtime/src/forkserver.rs` — a
`ForkServerManager` lazily starts one `python -m kinesin.forkserver` per module file,
waits for its readiness line, and hands out the socket path. `worker.rs::run_worker`
routes each job/shard over that socket (`run_via_socket`) and falls back to spawning a
fresh worker process if the socket can't be reached. Gated on at runtime by OS
(`cfg!(target_os = "linux")`) / `KINESIN_FORKSERVER`.

**Validated end-to-end on Linux (Docker, full runtime + WS):** the full TPP job —
including the `gaussian_kde`→LAPACK that segfaults on macOS — completes through the
fork path (all 3 tables, both galleries), and is **~33% faster: 6.7s (fork off) → 4.5s
(fork on)**. macOS falls back cleanly with no regression (6.3s).

**Gate:** `forkserver.fork_supported()` (Python) and the Rust manager both default fork
on only for Linux; `KINESIN_FORKSERVER=1/0` overrides. Known limitation: a job that
times out under the fork transport can't be force-killed via the socket (closing it
sends the child EPIPE on its next emit); acceptable for now.

## 5. Auto-balanced parallelism + automatic progress — ✅ done

**Pain:** we hand-tuned `skip_primary=True` and `progress=(0,50)`/`(50,100)` ranges to
overlap shard-0 analysis with rendering — scheduler expertise leaking into the app.

**Shipped:** `job.map` / `job.primary` now only *register* work; the worker runs it
([`worker.py` `JobContext._run_deferred`](../python/kinesin/worker.py)) after the job
function returns, when it can see the whole plan. That deferral is what lets the
framework balance and aggregate with no app input:

- **Auto skip-primary.** Declaring a `job.primary` keeps shard 0 out of every `job.map`
  (when there's more than one shard) and strides the items across the rest — the
  rendering overlaps the single-copy analysis automatically. (The blocker before was
  ordering: `map` is called before `primary`, so only a deferred plan knows a primary
  exists.)
- **Auto progress.** A shard's maps share the 0–100 bar in proportion to their item
  counts; several maps advance one smooth bar. The runtime already averages progress
  across shards, so no per-map ranges are needed.

`skip_primary=`/`progress=(lo,hi)` remain as advanced per-map overrides (default
`None` = automatic). TPP's `compute.py` dropped both knobs from its two `job.map`
calls; verified via the #1 harness across shard counts (curve count is invariant) plus
a unit test asserting shard 0 maps nothing and worker progress is monotonic to 100.
Pure Python change — the Rust runtime (shard-count decision, cross-shard averaging) was
already in place.

## 6. Typed widget↔output bindings via codegen — medium effort

**Pain:** `plot:` / `plotly:` / `table:` / `gallery:` / `download:` are stringly-typed
contracts duplicated across Python and Rust; a typo is a silent runtime miss.

**Add:** generate the Python + TS binding/protocol types from the `kinesin-protocol`
crate (single source of truth). The plan deferred this; revisit. Robustness, not speed.

## 7. Data-derived UI options without pandas-at-registration — small effort

**Pain:** `ui.py` imports pandas *just* to fill a dropdown from a CSV column
(`pd.read_csv(META)["Temperature"]...`), pulling pandas into the registration pass.

**Add:** `app.select_from(file, column)` (or a deferred-options mechanism) so
registration stays light.

---

The items below were distilled in a later review pass (mapping the framework
internals, not just the TPP build). #8–#11 keep the same lens — overhead and
correctness the framework should absorb. The closing section is a deliberate
scope question, not a task.

## 8. Job failure surfacing — medium effort

**Pain:** when a worker dies, its stderr is only examined on crash
([`worker.rs` `run_via_process`](../crates/kinesin-runtime/src/worker.rs)), and
errors cross the wire as bare strings ([`protocol.py` `failed`](../python/kinesin/protocol.py)).
Building TPP, a failing shard meant reading stderr by hand to reconstruct what
broke. The failure path is as much a framework concern as the success path.

**Add:** catch the Python exception in the worker, surface a clean traceback to
the UI, and carry a *kind* (validation / timeout / crash) alongside the message
so the client can render and dispatch on it instead of pattern-matching strings.
This also subsumes the fork-server "exited without a result (possible crash)"
message, which today hides the real cause.

## 9. Content-addressed result cache — medium effort, high payoff

**Pain:** `cache_dir` is configured and the directory is created on startup
([`config.rs`](../crates/kinesin-runtime/src/config.rs)) — but nothing ever reads
it. Re-running a job with identical inputs re-does every shard's work.

**Add:** hash the worker descriptor (module + qualname + args) and key results
under `cache_dir`; a hit short-circuits in the Rust `JobManager` **before any
worker spawns** ([`jobs.rs`](../crates/kinesin-runtime/src/jobs.rs)). This is
filesystem result reuse, *not* process reuse — it does **not** touch the
[[no-warm-workers]] constraint. Opt-in per job so nondeterministic jobs can stay
uncached. (Either wire it up or drop the dead config knob.)

## 10. Input validation as a framework concern — medium effort

**Pain:** a job receives raw widget values; there is no `required` / `min` /
`max` / `pattern`, and no feedback before submit. Every app re-implements its own
checks inside the job — the same masquerade as #2/#3.

**Add:** declarative constraints on input widgets, validated in two places: the
frontend (pre-submit feedback) and the worker boundary (authoritative). Pairs
naturally with #6 (typed bindings) — the constraint travels with the binding.

## 11. Native Rust I/O layer — ✅ done (RawValue passthrough deferred)

**Pain:** several request-path operations re-pay interpreter startup or copy
whole files through memory — the exact overhead the rest of this roadmap exists
to remove, just on the Rust side.

**Shipped:**
- **Native parquet preview** ([`preview.rs`](../crates/kinesin-runtime/src/preview.rs)) —
  reads head+schema via `arrow`/`parquet` (NaN/non-finite → null, matching the
  Python contract), on a blocking thread. csv/ipc and any read error fall back to
  `python -m kinesin.preview`. The worker no longer writes `.preview.json`
  sidecars. Verified against an arrow-written *and* a polars-written (zstd) file.
- **Streaming uploads** — `field.chunk()` straight to disk, bounded memory.
- **Streaming file serving** — `Body::from_stream` over a `ReaderStream`.
- **Native bundles** ([`bundle.rs`](../crates/kinesin-runtime/src/bundle.rs)) —
  build the results `.zip` from the manifest in Rust (`zip` + `glob`), no
  `kinesin.zipjob` subprocess; written to `<out>.partial` then renamed.
- **Cheap wins** — `tower-http` `CompressionLayer` (gzip/br); per-shard `Progress`
  coalesced to whole-percent changes (single-job path too).

**Deferred:** Plotly `RawValue` passthrough — the WS `WidgetUpdate.props` is typed
`Map<String, Value>`, so avoiding the parse-then-reserialize would mean changing
the protocol type for a marginal gain on a bounded payload. Not worth the churn.

Original analysis below.

- **Preview spawns Python + polars per request.** [`preview.rs`](../crates/kinesin-runtime/src/preview.rs)
  runs `python -m kinesin.preview` on every on-demand preview, and `preview.py`
  imports polars — hundreds of ms to read the first 100 rows of a parquet file.
  The sidecar in [`jobs.rs` `send_table`](../crates/kinesin-runtime/src/jobs.rs)
  only covers job-bound tables; the `/preview` route and any sidecar-less path
  still spawn Python. **Read parquet head+schema natively in Rust** (`arrow` +
  `parquet` crates — lighter than pulling in the `polars` crate just for a head).
  Bonus: the worker can then stop writing `.preview.json` sidecars entirely.
  *Highest-payoff piece; it re-pays the cold-import cost on a routine path.*
- **Uploads buffer the whole file in RAM.** `upload_handler` does
  `field.bytes().await` (up to 512 MiB) before writing
  ([`server.rs`](../crates/kinesin-runtime/src/server.rs)), against the spirit of
  memory Rule 2. Stream `field.chunk()` straight to disk.
- **File serving reads whole files into a `Vec`.** `serve_file` uses
  `tokio::fs::read`; use `tower-http`'s `ServeFile` / `Body::from_stream`
  (gets Range support for free).
- **Bundles shell out to `kinesin.zipjob`.** It's stdlib-only, so the win is
  modest (interpreter startup), but the `zip` crate lets the archive **stream**
  into the response instead of write-then-read-it-all-back.
- **Cheap wins:** a `tower-http` `CompressionLayer` (JS bundle, SVG, Plotly JSON
  ship uncompressed today); throttle/coalesce per-shard `Progress` WS messages
  (one `WidgetUpdate` per progress line × N shards today); pass Plotly figure
  JSON through with `RawValue` instead of parse-then-reserialize in `send_plotly`.

Composes with #9: the cache check is the same kind of Rust short-circuit before a
worker spawns.

---

Items #12–#17 came out of the #11 review pass and the dependency-footprint
analysis: gaps the code itself exposed once the native I/O layer landed.
Suggested order for what's next: **#12 (CI protects everything else) → #9 →
#8 → #13**, with #3/#7 as small-item filler.

## 12. CI + committed compat fixture — small effort, highest protection

**Pain:** the codebase now has real test coverage (Rust unit tests, the Python
suite, the TPP integration test) and **nothing runs it** — no `.github/workflows`
exists. Worse, the one test covering real polars-parquet compatibility
([`preview.rs` `reads_polars_written_parquet`](../crates/kinesin-runtime/src/preview.rs))
is `#[ignore]`d behind a `KINESIN_TEST_PARQUET` env var pointing at an
uncommitted, undocumented fixture — it can only ever run on the machine where it
was written, while the roadmap advertises the compatibility it checks as
"verified".

**Add:** commit the sub-KB polars-written zstd fixture under `tests/`, drop the
env var and `#[ignore]`, and add a workflow running `cargo test` + `cargo clippy`
+ `pytest` (including the TPP integration test). Everything this roadmap shipped
was verified by hand at the time; nothing gates regressions.

## 13. Native csv/ipc preview — delete `preview.py` — small-medium effort

**Pain:** the preview contract lives in two implementations (Rust for parquet,
Python for csv/ipc), and they **already drifted**: `preview.py` emitted bare
`NaN` tokens that serde_json rejects — found and patched in the #11 review, but
the dual-contract structure that produced the bug is still standing.

**Add:** the `arrow` crate already reads CSV and IPC; extend
[`preview.rs`](../crates/kinesin-runtime/src/preview.rs) to cover them and delete
`python/kinesin/preview.py` entirely. Kills the last preview subprocess *and*
the drift class structurally, instead of patching divergences as they're found.
(Same shape as #11's zipjob removal.)

## 14. Fork-transport timeout kill — small effort

**Pain:** #4's documented open gap: a job that times out under the fork
transport can't be force-killed — closing the socket only sends the child EPIPE
on its *next* emit, so a hung child (stuck in native code, emitting nothing)
leaks a process and its memory indefinitely.

**Add:** the forked child reports its PID as its first protocol line; on timeout
the runtime (which already detects it in
[`worker.rs` `run_via_socket`](../crates/kinesin-runtime/src/worker.rs)) sends
SIGKILL. Process exit is already the memory-reclamation mechanism — this just
makes it enforceable.

## 15. `GET /jobs` endpoint — trivial

**Pain:** `JobRecord` already tracks id/status/result/logs per job behind an
`#[allow(dead_code)]` promising "a future /jobs endpoint"
([`jobs.rs`](../crates/kinesin-runtime/src/jobs.rs)) — the data is collected and
unreachable.

**Add:** serialize the records map as `GET /jobs`. It's also the natural
debugging surface for #8's failure kinds. (Promoted from the "productionize"
list below: as a debugging aid it needs no auth story.)

## 16. Slim the framework's Python floor — investigative, big footprint win

**Pain:** the framework's declared deps resolve to ~350 MB, and **pyarrow alone
is 122 MB** of it — declared in `pyproject.toml` but polars reads/writes parquet
natively, so it may be dead weight (possibly only reachable via
`polars.from_pandas` for pandas-dataframe job outputs). The example venv also
carries a 43 MB duckdb that nothing imports.

**Add:** one investigative pass — remove `pyarrow`, run the full suite + TPP; if
only pandas interop needs it, make it an optional extra (`kinesin[pandas]`).
Evict duckdb from the resolved env. Roughly halves the runtime disk floor if it
holds.

## 17. Storage retention sweep — small effort

**Pain:** `upload_dir` and `result_dir` grow forever — every upload and every
job's artifacts persist until someone deletes them by hand. (The #11 review
fixed *orphaned partial* uploads; completed files still accumulate.)

**Add:** an age-based sweep (config: `storage.retention_days`, off by default)
at startup and on a timer. Pairs with #9: the cache needs the same eviction
mechanism, so build it once.

## Out of scope by design — productionize only on a deliberate decision

[`architecture.md`](architecture.md) declares kinesin single-tenant, in-memory,
no-auth **by design**. The following are real gaps *for a deployed service* but a
scope change, not framework-absorbs-expertise items — listed so the decision is
explicit, not silent:

- **Auth / multi-tenant isolation / fair-share scheduling** — one parallel job can
  monopolize the worker pool; any job UUID is world-readable.
- **Monitoring** — no `/metrics`. (The `/jobs` introspection endpoint was
  promoted to #15 above — as a debugging aid it needs no auth story; Prometheus
  metrics stay here.)
- **Graceful shutdown** — no SIGTERM drain; in-flight workers die abruptly.
- **Deployment guide** — no Docker/k8s/cloud story.

Dropped as out-of-spirit (real but trace back to no lesson the build taught):
date/time & wizard widgets, session persistence, automatic retry, bundle caching.
