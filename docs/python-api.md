# Python API reference

Everything an app author writes lives in `from kinesin import App`. There are three
groups: the **`App`** (declare UI + register jobs), **widget handles & refs** (wire
inputs to jobs and jobs to outputs), and the **`JobContext`** (`job`, available inside
a running job).

```python
from kinesin import App
app = App("My App")
```

`App(title="Kinesin App")` — constructing it registers it as the active app so the
registration pass can find it. Run with `kinesin run ui.py`.

---

## Display widgets

Each returns a `Widget` handle (it has a stable `.id`).

| Method | Renders |
| --- | --- |
| `app.text(value)` | a paragraph |
| `app.header(value)` | a section header |
| `app.button(label)` | a button; wire it with `.on_click(...)` |

```python
app.text("Upload a CSV, then run the analysis.")
app.header("Options")
run = app.button("Run")
```

### Collapsible groups (`app.group`)

A context manager that wraps the widgets created inside it into a collapsible card
(like Streamlit's expander). Children stay normal top-level widgets — their values
seed and flow exactly as ungrouped ones do — so grouping is purely visual.

```python
with app.group("Data Visualization Options"):     # collapsible=True, expanded=True
    show_volcano = app.checkbox("Volcano plot", default=True)
    show_violin  = app.checkbox("Violin plot", default=True)
```

## Input widgets

Each has a `.value` property — a **reference** you pass to a job. The runtime stores
the live value in session state and seeds the default on connect.

| Method | `.value` type |
| --- | --- |
| `app.checkbox(label, default=False)` | `bool` |
| `app.slider(label, min, max, step=0.1, default=None)` | `float` |
| `app.number(label, default=0)` | `int`/`float` |
| `app.select(label, options, default=None)` | `str` |
| `app.file_upload(label)` | use `.path` (set when a file is uploaded) |

```python
normalize = app.checkbox("Normalize", default=True)
r2 = app.slider("R² threshold", min=0.5, max=0.99, step=0.01, default=0.8)
upload = app.file_upload("Upload CSV")
# normalize.value, r2.value, upload.path are refs → pass them to a job
```

### Conditional enable/disable (`enabled_when`)

`app.checkbox(...)` and `app.button(...)` accept `enabled_when=<widget>` — the widget
greys out and goes non-interactive whenever the controller's value is falsy. The
frontend tracks input values live, so it reacts as soon as the controller changes.

```python
perform = app.checkbox("Perform Shapiro-Wilk test", default=False)
log_t   = app.checkbox("Log transform", default=True, enabled_when=perform)   # disabled until `perform` is on
```

## Jobs

### Declaring a job

```python
@app.job
def analyze(path, job):
    ...
    return {"result_path": out}          # or use job.result(out)

@app.job(parallel=True)                  # runtime may run this as N workers
def big_analysis(path, job):
    ...
```

The decorated name becomes a **`JobHandle`**. `@app.job(parallel=True)` opts into
[runtime sharding](parallelism.md). The optional last parameter named `job` receives
the `JobContext`.

### Submitting a job (wiring it to a button)

`handle.submit(*args)` does **not** run anything during registration — it returns a
`JobBinding` describing "submit this job with these args", and binds a result slot.
Arguments are either **refs** (`upload.path`, `slider.value`) resolved at click time,
or **literals**.

```python
job = analyze.submit(upload.path)        # ref arg
run.on_click(job)                        # clicking submits it
```

Create the binding **once** and reuse it for the button, the status, and the outputs
so they all bind to the same slot. `button.on_click(...)` also accepts a 0-arg lambda
(`lambda: analyze.submit(upload.path)`) or a raw action dict; the lambda is invoked
once during registration to capture the binding.

### Lightweight (jobless) actions

```python
button.on_click(app.set_text(target_widget, "Done"))   # runtime sets target's text
```

## Output widgets

Bind these to a `JobBinding` (or its `.result_path`); the runtime fills them when the
job completes. The `name` matches an output the job registers (`job.output(name, …)`
or `job.map(into=name)`).

| Method | Shows |
| --- | --- |
| `app.job_status(job)` | live status pill, progress bar, streamed logs, ⏱ elapsed |
| `app.table_preview(job.result_path)` | first 100 rows of the job's main result |
| `app.result_table(job, name)` | first 100 rows of a secondary table output |
| `app.image(job, name, caption="")` | an inline SVG the job wrote |
| `app.plot(job, name, caption="")` | an interactive **Plotly** chart (figure JSON) |
| `app.gallery(job, name, caption="")` | a paginated, searchable image gallery |
| `app.download(job, name, label="Download")` | a button that downloads the job's `.zip` |

```python
app.job_status(job)
app.table_preview(job.result_path)
app.plot(job, "volcano", caption="Volcano plot")
app.gallery(job, "curves", caption="Fitted curves")
app.download(job, "bundle", "⬇ Download results (.zip)")
```

---

## `JobContext` (the `job` parameter)

Available inside a running job. Attributes: `job.id`, `job.result_dir`, `job.shard`,
`job.num_shards`, `job.is_primary`.

### Basics

```python
job.log("Reading CSV")        # streamed to the status widget's log
job.progress(42)              # 0–100, drives the progress bar
```

### Parallel primitives

These let a `parallel=True` job fan out without ever touching shard indices. See
[parallelism.md](parallelism.md) for the full model.

```python
# Run a per-item function over `items`, sharded across the runtime's workers.
# This worker only handles its slice; a returned matplotlib Figure is auto-saved as
# "<item>.svg" and closed. `into` names a gallery output. Balancing is automatic:
# because the job also declares a job.primary (below), shard 0 is kept out of the map
# and the items split across the rest, and the maps' progress is aggregated to 0-100 —
# no `skip_primary`/`progress` ranges to tune. (Both remain as advanced overrides.)
job.map(items, fn, into="curves")

# Run `fn` once, on shard 0 only (or as a decorator). Put expensive single-copy
# work here so it isn't duplicated across workers; it runs in parallel with the maps.
@job.primary
def _summary():
    ...

# Register outputs (typically inside job.primary). The table helpers accept a
# DataFrame *or* a path; a DataFrame is written to parquet, and the runtime
# reads the 100-row preview natively in Rust (no subprocess):
job.result(df_or_path)              # main result table (→ table_preview)
job.table("averaged", df_or_path)   # a named table output (→ result_table)
job.output("volcano", path)         # a named artifact path (→ image / plot / gallery)

# Path helpers (own the result_dir naming so jobs don't build paths by hand):
csv_path = job.write_csv("stats", df)        # → <result_dir>/<id>_stats.csv
svg_path = job.write_text("volcano.svg", s)  # → <result_dir>/<id>_volcano.svg
p        = job.path("name")                  # → <result_dir>/<id>_name

job.bundle(                         # register the download .zip (built on demand)
    files={"data/stats.csv": csv_path, "figures/volcano.svg": svg_path},
    dirs={"figures/curves": "curves"},   # arc_prefix → output name (a directory)
)
```

A job may register outputs via these helpers **and/or** return
`{"result_path": ..., "outputs": {...}}` — the worker merges both.

### Minimal parallel job

```python
@app.job(parallel=True)
def render(path, job):
    items = load_items(path)                       # cheap, every shard
    job.map(items, lambda it: make_figure(it), into="plots")
    @job.primary
    def _summary():
        job.result(write_summary(path))
        job.bundle(dirs={"figures": "plots"})
```

---

## Refs and slots (the binding model)

You rarely construct these directly, but they explain how wiring works:

- `widget.path` → `Ref(node_id, "path")` — for a file upload.
- `widget.value` → `Ref(node_id, "value")` — for an input widget.
- `Ref` args are resolved at submit time from **session state**
  (`state["<node_id>.<field>"]`).
- `handle.submit(...)` allocates a **slot**; `job.result_path` is a `SlotResultRef`
  bound to that slot. Output widgets bind to the slot via a **view**
  (`status`, `result_preview`, `plot:<name>`, `gallery:<name>`, …).

This is what lets the runtime resolve actions and route updates with no Python in the
request path.
