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

## 5. Auto-balanced parallelism + automatic progress — medium effort

**Pain:** we hand-tuned `skip_primary=True` and `progress=(0,50)`/`(50,100)` ranges to
overlap shard-0 analysis with rendering — scheduler expertise leaking into the app.

**Add:** declare "this is the single-copy task, these are the fan-out items" and let
the runtime balance shards and aggregate progress automatically — no `skip_primary`,
no manual ranges.

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
