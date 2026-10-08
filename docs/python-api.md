# Python API reference

Everything an app author writes lives in `from tubulin import App`. There are three
groups: the **`App`** (declare UI + register jobs), **widget handles & refs** (wire
inputs to jobs and jobs to outputs), and the **`JobContext`** (`job`, available inside
a running job).

```python
from tubulin import App
app = App("My App")
```

`App(title="Tubulin App")` — constructing it registers it as the active app so the
registration pass can find it. Run with `tubulin run ui.py`.

---

## Display widgets

Each returns a `Widget` handle (it has a stable `.id`).

| Method | Renders |
| --- | --- |
| `app.text(value)` | a paragraph of plain text |
| `app.markdown(value)` | rich text — headings, lists, links, `**bold**`, quotes, fenced code |
| `app.header(value)` | a section header |
| `app.button(label)` | a button; wire it with `.on_click(...)` |
| `app.info/success/warning/error(body)` | a coloured callout ([below](#callouts)) |
| `app.metric(label, value)` | a headline number ([below](#metrics)) |

```python
app.text("Upload a CSV, then run the analysis.")
app.header("Options")
run = app.button("Run")

app.markdown("""
    ## Melting curve fit

    Uses a **sigmoid** with a plateau term; see [the method](https://example.com).

    - `R²` filters poorly-fit curves
    - imputation is deterministic given the seed
""")           # indented triple-quoted strings are dedented for you
```

The markdown renderer is a small CommonMark subset built into the frontend (no
markdown dependency): ATX headings, fenced code, unordered/ordered lists,
blockquotes, horizontal rules, paragraphs, and inline `` `code` ``, `**bold**`,
`*italic*`, `~~strike~~` and `[links](url)`. Absolute links are restricted to
http/https/mailto/ftp; relative links and fragments pass through.

<a id="callouts"></a>
### Callouts (`app.info` / `success` / `warning` / `error`)

A bordered, tinted box with a level the reader sees before they read the words. The
body goes through the same markdown subset as `app.markdown`, so a caveat can carry
a link or a unit in `**bold**`.

```python
app.info("Upload a **tidy** CSV: one row per measurement.")
app.success("Fit converged for every curve.")
app.warning("Two replicates only — treat the CI as indicative.", title="Low n")
app.error("No temperature column found.")
```

These are *authored* text, fixed at registration — they say something about the app,
not about a run. A **job's** failure is surfaced by `app.job_status`, which shows the
error the worker reported; don't try to reproduce it with `app.error`.

`app.alert(level, body, title=...)` is the same thing with the level as a value, for
when it's computed.

<a id="metrics"></a>
### Metrics (`app.metric`)

One big number with an optional change indicator — the KPI tile at the top of a
report. It comes in two forms.

**Static**, when the value is known at registration:

```python
app.metric("Replicates", 3)
app.metric("Threshold", 0.05, help="adjusted p-value cutoff")
```

**Bound to a job**, when the value is a result. The UI declares the label, help text
and colouring once; the job supplies only the number, via
[`job.metric`](#jobcontext-the-job-parameter):

```python
app.metric("Proteins fitted", job=binding, name="n_fit")
app.metric("Median R²", job=binding, name="median_r2", delta_color="off")
```

```python
@app.job
def fit(job):
    ...
    job.metric("n_fit", 1284, delta=-12)     # value + change since... whatever you mean
    job.metric("median_r2", 0.94)
```

A bound tile shows `—` until its job completes. `job=` and `name=` must be given
together.

| Argument | Effect |
| --- | --- |
| `delta=` | a signed number or string (`-12`, `"+3.1%"`, `"2 fewer"`) shown under the value |
| `delta_color=` | `"normal"` (up is good), `"inverse"` (down is good — an error rate, a runtime), `"off"` (no colour, no arrow) |
| `help=` | hover text on the label |

Numbers are formatted with thousands separators and at most 6 fraction digits, so a
float64 artefact never becomes the headline. Pass a string when you want to control
the units yourself (`"1.2 GB"`, `"n/a"`).

### Collapsible groups (`app.group`)

A context manager that wraps the widgets created inside it into a collapsible card
(like Streamlit's expander). Children stay normal top-level widgets — their values
seed and flow exactly as ungrouped ones do — so grouping is purely visual.

```python
with app.group("Data Visualization Options"):     # collapsible=True, expanded=True
    show_volcano = app.checkbox("Volcano plot", default=True)
    show_violin  = app.checkbox("Violin plot", default=True)
```

### Layout containers (`app.columns` / `app.tabs` / `app.sidebar`)

Three more context managers, all built the same way as `app.group`: the AppSpec stays
a **flat** node list and layout is expressed as balanced `<kind>_start` / `<kind>_end`
markers around a span of it, so a widget's value seeds and flows identically however
deeply it is nested — only the rendering differs.

```python
with app.columns(2) as (left, right):          # or app.columns([2, 1]) for relative widths
    with left:
        app.slider("Min temperature", 30, 70)
    with right:
        app.slider("Max temperature", 30, 70)

with app.tabs("Data", "Parameters") as (data, params):
    with data:
        app.file_upload("Measurements")
    with params:
        app.number("Seed", 7)

with app.sidebar():                            # renders in the left rail
    app.markdown("### Filters")
    app.multiselect("Conditions", CONDITIONS)
```

- **Columns** take a count or a list of relative widths; they stack on a narrow
  window, and a region you never enter still reserves its cell so the grid keeps its
  shape.
- **Tabs** keep hidden panels *mounted*, so switching tabs never resets an input or
  drops a result that already rendered (the same reason a collapsed `app.group` keeps
  its children).
- **Sidebar** blocks concatenate, so a long parameter list can stay next to the code
  it belongs to.

## Input widgets

Each has a `.value` property — a **reference** you pass to a job. The runtime stores
the live value in session state and seeds the default on connect.

| Method | `.value` type |
| --- | --- |
| `app.checkbox(label, default=False)` | `bool` |
| `app.slider(label, min, max, step=0.1, default=None)` | `float` |
| `app.number(label, default=0)` | `int`/`float` |
| `app.select(label, options, default=None)` | `str` |
| `app.multiselect(label, options, default=None)` | `list[str]` |
| `app.text_input(label, default="", placeholder="")` | `str` |
| `app.text_area(label, default="", placeholder="", rows=4)` | `str` |
| `app.file_upload(label)` | use `.path` (set when a file is uploaded) |

`multiselect` hands the job the selected options in **declaration order**, not click
order, so a job can index into the list deterministically. The two text widgets
debounce in the browser: keystrokes stay local and one session write is sent after a
short idle (or immediately on blur / Enter).

```python
normalize = app.checkbox("Normalize", default=True)
r2 = app.slider("R² threshold", min=0.5, max=0.99, step=0.01, default=0.8)
upload = app.file_upload("Upload CSV")
# normalize.value, r2.value, upload.path are refs → pass them to a job
```

### Conditional enable/disable (`enabled_when`)

Every input widget and `app.button(...)` accepts `enabled_when=<widget>` — the widget
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
| `app.download(job, name, label="Download")` | a button that downloads the job's `.zip`, saved as `<App_Title>_results_<id>.zip` |

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
job.metric("n_fit", 1284, delta=-12)  # a headline number (→ metric)

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
