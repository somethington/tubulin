# Testing apps headlessly (`kinesin.testing`)

The runtime normally drives an app — it reads `ui.py`, resolves the job's arguments
from widget values, spawns workers, and routes outputs to bound widgets. For **tests,
golden-output regression, and benchmarks** you usually just want to run the job once
and inspect what it produced, without a server, a WebSocket, or a browser. That's
`kinesin.testing`.

```python
from kinesin.testing import run_app

r = run_app("examples/tpp/ui.py", inputs={"Imputation seed": 7, "number_2": 8})
assert r.result is not None              # primary result table written
df = r.table("averaged")                 # a named table output → polars DataFrame
assert df.height > 0 and r.elapsed < 30
assert len(r.gallery("avg_curves")) > 0  # files a job.map(into=...) accumulated
```

It runs the job **in-process** through the same execution core the worker uses
(`kinesin.worker.execute_job`), so output-collection semantics are identical to a real
run.

## `run_app(ui_path, inputs=None, *, job=None, shards=1, result_dir=None) -> RunResult`

- **`inputs`** overrides widget values, keyed by widget **id** *or* **label**. Unknown
  keys raise (so a typo — or a label containing `=` — fails loudly instead of being
  silently ignored). File-upload args have no default, so pass them here.
- **`job`** selects which job to run when an app declares more than one. (Several
  buttons bound to the *same* job are fine — any is used.)
- **`shards`** runs the job as N sequential shards to exercise the sharding and
  auto skip-primary slicing. This is for *correctness*, not the runtime's parallel
  wall-clock — shards run one after another here.
- **`result_dir`** is where outputs are written (a fresh temp dir by default).

### `RunResult`

| Member | What |
| --- | --- |
| `r.elapsed` | seconds the run took (in-process, single machine) |
| `r.result` / `r.result_path` | path of the primary result table |
| `r.outputs` | `{name: path}` for every named output |
| `r.table(name=None)` | read a table output as a **polars DataFrame** (primary if `name` is `None`) |
| `r.text(name)` | read a text/JSON output (e.g. a Plotly figure) as a string |
| `r.gallery(name)` | list the files a `job.map(into=name)` directory accumulated |
| `r.logs` / `r.progress` | the `job.log(...)` / `job.progress(...)` calls, captured |

`load_app(ui_path)` is also exported — it imports the app fresh each call (evicting the
app's own modules first, so a module-level `App` singleton doesn't accumulate widgets
across repeated runs).

## CLI

A browser-free way to run an app and eyeball/time it:

```bash
python -m kinesin.testing examples/tpp/ui.py "Imputation seed=7" number_2=8
# job=tpp_analyze  elapsed=3.2s  shards=1
#   output[avg_curves] -> dir … (6 files)
#   …
```

`key=value` tokens become input overrides (keyed by id or label); `--shards N` sets the
shard count.

## Example

A runnable example test lives at
[`examples/tpp/test_tpp.py`](../examples/tpp/test_tpp.py) — it runs the real TPP job
in-process and asserts on the result columns, the named outputs, the gallery, and the
streamed logs.
