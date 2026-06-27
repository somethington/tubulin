# TPP Solver, adapted to Kinesin

This adapts [`somethington/tpp_solver`](https://github.com/somethington/tpp_solver) —
a Streamlit app for **Thermal Proteome Profiling** (sigmoidal melting-curve fitting +
Mann-Whitney/Benjamini-Hochberg statistics) — to run as a managed Kinesin job, and
benchmarks the two execution models on the project's own example data.

The point: **same science, different host.** The pipeline is **polars + numpy**
(pandas-free); it vendors the few small pure-numpy functions it needs from tpp_solver
(sigmoid, the per-protein replicate plotter, Benjamini-Hochberg, the data slicer, the
bin-width rule) rather than importing tpp_solver — so it pulls in no pandas, streamlit,
or duckdb. The data wrangling is polars; the numerics are numpy/scipy and are verified
**byte-for-byte identical** to the original pandas/tpp_solver outputs (see below).

## Files

| File | Role |
| --- | --- |
| `tpp_pipeline.py` | Headless **polars + numpy** pipeline: filtering, seeded imputation, `curve_fit` sigmoid fits, Mann-Whitney + BH FDR. Self-contained (no pandas/tpp_solver). |
| `app_def.py` | The shared Kinesin `App`. |
| `compute.py` | The `@app.job` — runs the pipeline in a disposable worker, writes the stats table to disk. The "computational file" the runtime imports. |
| `ui.py` | The Kinesin app: options (in two collapsible cards), run button, live status, result preview, Plotly plots, curve gallery, and download — wired to the bundled data. |
| `benchmark.py` | Runs the analysis Streamlit-style vs Kinesin-style, asserts metrics are identical, reports the performance/memory delta. |
| `data/` | The upstream example data (`sample_data.tsv`, `sample_metadata.csv`). |

## Setup

The pipeline is self-contained — just its scientific deps in the Kinesin venv (no
pandas, no tpp_solver, no duckdb):

```bash
uv pip install polars numpy scipy matplotlib plotly
```

The numerics are verified byte-identical to the original pandas/tpp_solver version:
run on the sample data, the polars pipeline reproduces every output table at
`atol=1e-9` and the same melting-point fingerprint (`f9cb67a4b1798f61`). Switching
pandas → polars dropped the per-worker import baseline from ~209 MB to ~159 MB while
leaving analysis time ~2 s (the numpy/scipy fitting dominates and is unchanged).

## Run the app

```bash
cargo run -p kinesin-runtime -- run examples/tpp/ui.py
# open the URL, adjust options if you like, click "Run TPP analysis" (no upload)
```

`ui.py` is wired to the bundled `data/` files via literal paths, so there's nothing
to upload. The job streams progress/logs over the WebSocket, writes every artifact to
`storage/results/`, and sends previews/plots to the browser. (To analyze your own
files, swap the literal paths in `ui.py` for `app.file_upload(...)` widgets.)

## Feature parity with the Streamlit app

The same options and outputs as upstream tpp_solver are exposed:

**Options** (Kinesin input widgets): normalize curves (checkbox), reference
temperature (select), R² threshold (slider), imputation seed (number), and a per-output
toggle — grouped into two collapsible cards mirroring the upstream app ("Data
Visualization Options" and "Shapiro-Wilk Normality Test"). The Shapiro-Wilk
transformation choices use `enabled_when`: they grey out until "Perform Shapiro-Wilk
Test" is on. Settings take effect when you click **Run** — Kinesin keeps widget values
in session state, so (unlike Streamlit) there's no separate "apply" step.

**Outputs** (all produced by the job, rendered in the browser):

| Output | How |
| --- | --- |
| Mann-Whitney + BH-FDR stats table | parquet → 100-row preview |
| **Download results (.zip)** | worker bundles CSVs + SVGs → served via `/download` |
| Volcano plot (ΔTm vs -log10 FDR) | **interactive Plotly** (reuses tpp_solver's figure) |
| Violin plot (melting points by treatment) | **interactive Plotly** |
| Melting-point distribution (hist + KDE) | **interactive Plotly** |
| **Averaged melting-curve gallery (every fitted protein)** | one sigmoid per treatment (mean-of-replicate intensities); worker plots each to disk one-at-a-time (lean), browsable + paginated, served inline via `/file` |
| **Averaged data table** | plot-free port of `fit_and_plot_averaged_curves` (bit-identical) → parquet → table |
| Shapiro-Wilk normality of ΔTm transformations | parquet → table; the transform toggles (Log / √ / Box-Cox / Yeo-Johnson) filter which are tested |

### Fitting: 1:1 with tpp_solver

The original computes melting points two ways, and both are reproduced exactly:

- **Per-replicate fit** (`process_protein_replicates`): one sigmoid per replicate.
  This feeds the statistics, volcano, distribution, and (after averaging the
  per-replicate melting points) the violin/normality. Verified bit-identical by the
  benchmark's melting-point fingerprint.
- **Averaged-curve fit** (`fit_and_plot_averaged_curves`, reused verbatim): averages
  the replicate intensities at each temperature, then fits one sigmoid. Drives the
  "Averaged data" table and `averaged_summary.csv` in the download.

The download `.zip` mirrors the Streamlit app's bundle: `data/statistics.csv`,
`replicate_summary.csv`, `averaged_summary.csv`, `normality.csv`, overview SVGs, and
**every per-protein curve** under `figures/curves/`.

### The parallel primitive (`job.map` / `job.primary`)

A parallel job declares `@app.job(parallel=True)` and uses two verbs — it never
touches shard indices, slicing, or merge plumbing:

```python
@app.job(parallel=True)
def tpp_analyze(..., job):
    rd, params = tpp.preprocess_only(...)               # cheap, every shard
    proteins = sorted(rd_proteins)

    job.map(proteins, lambda p: tpp.plot_one(p, rd, params, "averaged"),
            into="avg_curves")        # fanned across workers, figures auto-saved + merged
    job.map(proteins, lambda p: tpp.plot_one(p, rd, params, "replicate"),
            into="rep_curves")

    @job.primary                      # runs ONCE, on shard 0
    def _summary():
        full = tpp.run(...)           # the expensive fit, not duplicated per shard
        job.result(stats_parquet)
        job.output("volcano", volcano_json)
        job.bundle(files={...}, dirs={"figures/averaged": "avg_curves"})
```

`job.map(items, fn, into=)` runs only this worker's slice `items[shard::num_shards]`
(the runtime runs the rest in parallel), auto-saves a returned matplotlib figure as
`<item>.svg`, registers `into` as a gallery, and streams progress. `job.primary(fn)`
runs once. The framework handles fan-out, the lean memory model, dir-merge, and the
on-demand bundle. The TPP job dropped from ~180 lines to ~90 with no loss of speed.

### Runtime-managed parallelism

The job is declared `@app.job(shardable=True)`. On submit, the **runtime decides how
many workers to use** — `num_shards = min(max_workers, cpu_cores)` — and fans the job
out across that many processes, each handed its `job.shard` / `job.num_shards`. Every
shard fits the data and plots its slice `proteins[shard::num_shards]` to shared
directories; shard 0 also writes the single-artifact outputs. The runtime aggregates
progress, builds each gallery by listing the shared dirs once all shards finish, and
zips the download on demand (`/bundle`, via `kinesin.zipjob`).

Crucially the parallelism lives in **Rust, not Python** — no `multiprocessing.Pool`
hidden inside one worker. The runtime owns resource allocation (it already manages the
worker pool), so it can balance this job against others.

On a 14-core machine with `max_workers=4`, plotting all 1242 curves:

| | Whole job, all plots |
| --- | --- |
| Streamlit (its own `mp.Pool`) | ~35s |
| Kinesin, single worker | ~54s |
| **Kinesin, 4 runtime shards** | **~20s** |

Raise `max_workers` in `kinesin.toml` to use more cores. Per-worker peak memory stays
~276 MB regardless of shard count (each plots one figure at a time).

> These numbers predate two later speedups: per-curve rendering was roughly **halved**
> (dropping the framework's forced `bbox_inches="tight"` and setting
> `svg.fonttype="none"`), and table previews are now served from worker-written
> sidecars instead of post-job preview subprocesses — so the full job now runs in
> ~10 s here (and faster on Linux with the fork-server).

### Plotting hundreds of curves, lean

The headline cost of the original is that it builds and **holds a matplotlib figure
per protein in the session** (~640 figures, hundreds of MB). The Kinesin worker
instead loops over all fitted proteins and writes each curve SVG to disk **one figure
at a time** (fit → save → close), streaming progress, then exits. On this dataset:
642 curves plotted, **peak worker memory ~276 MB**, all reclaimed when the worker
exits. The gallery and download read the on-disk SVGs; nothing holds them all in RAM.

The UI uses the Kinesin design system (`@kinesin/design`): dependency-free CSS tokens
with **light and dark** themes, a geometric type system (Space Grotesk / IBM Plex
Sans / IBM Plex Mono), a warm orange accent, the lab logo + sidebar, and a themed
Plotly layout. The charts are still built from tpp_solver's own trace/layout code →
`fig.to_json()` → rendered by plotly.js. (It originally shipped as a Streamlit look-
alike — coral `#FF4B4B`, Source Sans — later replaced by the design kit.)

This exercised new Kinesin framework widgets: `checkbox`/`slider`/`number`/`select`
inputs (their `.value` feeds the job), an `image` widget that inlines an SVG, and a
`plotly` widget that renders a figure JSON the job wrote to disk. The runtime stores
input values in session state and the worker emits named `outputs` (paths) that drive
the bound widgets.

> Note: GO-annotation visualizations are out of scope (they need the bundled DuckDB
> database). The example fitted-curve plot is matplotlib SVG, matching upstream (those
> curves are matplotlib there too).

## Run the benchmark

```bash
PYTHONPATH=examples/tpp .venv/bin/python examples/tpp/benchmark.py
```

## Results (example data: 1008 proteins, 40 samples, control vs ADEP4)

```
============================================================================
  METRIC                              Streamlit-style                Kinesin
----------------------------------------------------------------------------
  proteins in                                    1008                   1008
  curves fit (R^2>=0.8)                          1742                   1742
  proteins fit                                    642                    642
  proteins tested                                 195                    195
  significant (FDR<0.05)                            0                      0
  melting-point fingerprint          f9cb67a4b1798f61       f9cb67a4b1798f61
----------------------------------------------------------------------------
  PERFORMANCE / MEMORY
  wall time (s)                                18.364                  7.594
  figures held in RAM                             642                      0
  peak Python heap (MB)                         352.7                    7.8
  peak process RSS (MB)                         866.2                  221.0
============================================================================
```

### What this shows

- **Metrics are identical** — same fitted curves (bit-identical melting-point
  fingerprint), same statistics. The adaptation preserves the science exactly. The
  `0 significant` result is expected and matches tpp_solver's own published sample
  output: only 2 replicates per condition survive fitting, so Mann-Whitney cannot
  reach significance (min p-value 0.333, FDR 1.0).
- **Memory is the differentiator.** The Streamlit pattern (`fit_and_plot_replicates`)
  builds and **retains a matplotlib `Figure` per protein** in a dict that lives in the
  session — here, 642 figures and a **352.7 MB** Python heap. Kinesin runs the same
  fits without eager plotting, keeps only the result table (**7.8 MB heap, ~45× less**),
  writes it to disk, and sends a 100-row preview. Peak RSS drops ~4× (866 → 221 MB),
  and in the real app the worker process then **exits, returning all of it to the OS**.
- **Time:** lower here because Kinesin skips rendering ~642 server-side figures the
  user may never open; plots become an on-demand browser concern.

### Honest caveats

- Both modes are run **single-threaded** to isolate the *memory* difference. The
  original Streamlit app parallelizes fitting with `multiprocessing.Pool`, so its
  wall-clock for fitting would be lower — but it still must hold every figure in
  session memory, which is the cost Kinesin removes.
- "Streamlit-style" here reproduces the incumbent's *memory lifecycle* (retain all
  figures in-process) using the real `process_protein_replicates`; it is not the
  Streamlit server itself.
