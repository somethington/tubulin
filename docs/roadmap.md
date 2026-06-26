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

## 4. Fork-server workers — medium effort, the big perf lever (constraint-sensitive)

**Pain:** measured ~1–3s cold import of polars+scipy+matplotlib *per worker* — now the
largest slice of the job time.

**Add:** a resident parent that imports heavy deps once, then `fork()`s a fresh child
per job (COW + `gc.freeze()`). **Distinct from a warm worker pool:** the child still
dies after its job and memory is still reclaimed by process exit — only the import is
amortized; no worker is ever reused. Honors the disposable-worker principle, but it
*does* keep a parent process resident, so it brushes against the "no warm workers"
stance — decide explicitly before building. Plausibly cuts seconds off every job.

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
