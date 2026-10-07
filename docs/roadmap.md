# Tubulin framework roadmap

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

**Shipped:** [`tubulin.testing`](../python/tubulin/testing.py). Runs an app's job
in-process through the same execution core the worker uses
(`tubulin.worker.execute_job`), so output-collection semantics are identical.

```python
from tubulin.testing import run_app

r = run_app("examples/tpp/ui.py", inputs={"Imputation seed": 7, "number_2": 8})
assert r.result is not None              # primary result table written
df = r.table("averaged")                 # named table output -> polars DataFrame
assert df.height > 0 and r.elapsed < 30
assert len(r.gallery("avg_curves")) > 0  # files a job.map(into=...) accumulated
```

Plus a CLI for quick benchmarks: `python -m tubulin.testing <ui.py> [key=value ...]`.
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
`svg.fonttype="none"`) and ship a `tubulin.reserve_legend(fig)` helper so outside
legends don't clip without the tight cost. Make "fast and correct" the default.
(Applied ad-hoc in TPP — generalize it.)

## 4. Fork-server workers — ✅ done (Linux-validated)

**Pain:** measured ~1–3s cold import of polars+scipy+matplotlib *per worker* — the
largest fixed slice of the job time, paid by every shard.

**Built:** [`tubulin.forkserver`](../python/tubulin/forkserver.py) — a resident parent
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

**Runtime wiring (done):** `crates/tubulin-runtime/src/workers/forkserver.rs` — a
`ForkServerManager` lazily starts one `python -m tubulin.forkserver` per module file,
waits for its readiness line, and hands out the socket path. `workers/mod.rs::run_worker`
routes each job/shard over that socket (`run_via_socket`) and falls back to spawning a
fresh worker process if the socket can't be reached. Gated on at runtime by OS
(`cfg!(target_os = "linux")`) / `TUBULIN_FORKSERVER`.

**Validated end-to-end on Linux (Docker, full runtime + WS):** the full TPP job —
including the `gaussian_kde`→LAPACK that segfaults on macOS — completes through the
fork path (all 3 tables, both galleries), and is **~33% faster: 6.7s (fork off) → 4.5s
(fork on)**. macOS falls back cleanly with no regression (6.3s).

**Gate:** `forkserver.fork_supported()` (Python) and the Rust manager both default fork
on only for Linux; `TUBULIN_FORKSERVER=1/0` overrides. Known limitation: a job that
times out under the fork transport can't be force-killed via the socket (closing it
sends the child EPIPE on its next emit); acceptable for now.

## 5. Auto-balanced parallelism + automatic progress — ✅ done

**Pain:** we hand-tuned `skip_primary=True` and `progress=(0,50)`/`(50,100)` ranges to
overlap shard-0 analysis with rendering — scheduler expertise leaking into the app.

**Shipped:** `job.map` / `job.primary` now only *register* work; the worker runs it
([`worker.py` `JobContext._run_deferred`](../python/tubulin/worker.py)) after the job
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

**Add:** generate the Python + TS binding/protocol types from the `tubulin-protocol`
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
([`workers/process.rs`](../crates/tubulin-runtime/src/workers/process.rs)), and
errors cross the wire as bare strings ([`protocol.py` `failed`](../python/tubulin/protocol.py)).
Building TPP, a failing shard meant reading stderr by hand to reconstruct what
broke. The failure path is as much a framework concern as the success path.

**Add:** catch the Python exception in the worker, surface a clean traceback to
the UI, and carry a *kind* (validation / timeout / crash) alongside the message
so the client can render and dispatch on it instead of pattern-matching strings.
This also subsumes the fork-server "exited without a result (possible crash)"
message, which today hides the real cause.

## 9. Content-addressed result cache — medium effort, high payoff

**Pain:** `cache_dir` is configured and the directory is created on startup
([`config/mod.rs`](../crates/tubulin-runtime/src/config/mod.rs)) — but nothing ever reads
it. Re-running a job with identical inputs re-does every shard's work.

**Add:** hash the worker descriptor (module + qualname + args) and key results
under `cache_dir`; a hit short-circuits in the Rust `JobManager` **before any
worker spawns** ([`jobs/`](../crates/tubulin-runtime/src/jobs)). This is
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
- **Native parquet preview** ([`artifacts/preview.rs`](../crates/tubulin-runtime/src/artifacts/preview.rs)) —
  reads head+schema via `arrow`/`parquet` (NaN/non-finite → null, matching the
  Python contract), on a blocking thread. csv/ipc and any read error fall back to
  `python -m tubulin.preview`. The worker no longer writes `.preview.json`
  sidecars. Verified against an arrow-written *and* a polars-written (zstd) file.
- **Streaming uploads** — `field.chunk()` straight to disk, bounded memory.
- **Streaming file serving** — `Body::from_stream` over a `ReaderStream`.
- **Native bundles** ([`artifacts/bundle.rs`](../crates/tubulin-runtime/src/artifacts/bundle.rs)) —
  build the results `.zip` from the manifest in Rust (`zip` + `glob`), no
  `tubulin.zipjob` subprocess; written to `<out>.partial` then renamed.
- **Cheap wins** — `tower-http` `CompressionLayer` (gzip/br); per-shard `Progress`
  coalesced to whole-percent changes (single-job path too).

**Deferred:** Plotly `RawValue` passthrough — the WS `WidgetUpdate.props` is typed
`Map<String, Value>`, so avoiding the parse-then-reserialize would mean changing
the protocol type for a marginal gain on a bounded payload. Not worth the churn.

Original analysis below.

- **Preview spawns Python + polars per request.** [`artifacts/preview.rs`](../crates/tubulin-runtime/src/artifacts/preview.rs)
  runs `python -m tubulin.preview` on every on-demand preview, and `preview.py`
  imports polars — hundreds of ms to read the first 100 rows of a parquet file.
  The sidecar in [`jobs/outputs.rs` `send_table`](../crates/tubulin-runtime/src/jobs/outputs.rs)
  only covers job-bound tables; the `/preview` route and any sidecar-less path
  still spawn Python. **Read parquet head+schema natively in Rust** (`arrow` +
  `parquet` crates — lighter than pulling in the `polars` crate just for a head).
  Bonus: the worker can then stop writing `.preview.json` sidecars entirely.
  *Highest-payoff piece; it re-pays the cold-import cost on a routine path.*
- **Uploads buffer the whole file in RAM.** `upload_handler` does
  `field.bytes().await` (up to 512 MiB) before writing
  ([`http/files.rs`](../crates/tubulin-runtime/src/http/files.rs)), against the spirit of
  memory Rule 2. Stream `field.chunk()` straight to disk.
- **File serving reads whole files into a `Vec`.** `serve_file` uses
  `tokio::fs::read`; use `tower-http`'s `ServeFile` / `Body::from_stream`
  (gets Range support for free).
- **Bundles shell out to `tubulin.zipjob`.** It's stdlib-only, so the win is
  modest (interpreter startup), but the `zip` crate lets the archive **stream**
  into the response instead of write-then-read-it-all-back.
- **Cheap wins:** a `tower-http` `CompressionLayer` (JS bundle, SVG, Plotly JSON
  ship uncompressed today); throttle/coalesce per-shard `Progress` WS messages
  (one `WidgetUpdate` per progress line × N shards today); pass Plotly figure
  JSON through with `RawValue` instead of parse-then-reserialize in `send_plotly`.

Composes with #9: the cache check is the same kind of Rust short-circuit before a
worker spawns.

---

## Tiers — closing the Streamlit adoption gap

A separate lens from #1–#17, which are all *internal* (overhead and correctness the
framework should absorb). Those are the hard parts, and they are largely built. The
tiers below are the things whose absence stopped anyone getting far enough to *see*
that work: a text box, two columns, bold text, `pip install`.

The ordering principle is how much each unblocks. Tier 0 is what makes adoption
possible at all; Tier 1 is what makes an app stop reading like a form; Tier 2 is what
lets people other than the maintainer extend and deploy it.

### Checklist

**Tier 0 — adoption is impossible without these**

- [x] **Installable wheels** — `pip install tubulin`, no Rust toolchain, no node (T0.4)
- [ ] &nbsp;&nbsp;&nbsp;&nbsp;↳ *publish to PyPI* — needs a PyPI project + the `pypi` repo environment.
      The name `kinesin` was squatted, hence the rename to `tubulin` (see T0.4)
- [ ] &nbsp;&nbsp;&nbsp;&nbsp;↳ *Windows* — a port, not a matrix row (see T0.4)
- [x] &nbsp;&nbsp;&nbsp;&nbsp;↳ *first-run papercuts* — `--version`, `tubulin new`, app-named zips (T0.5)
- [x] **Rich text** — `app.markdown` (T0.1)
- [x] **Free text input** — `app.text_input`, `app.text_area` (T0.1)
- [x] **Multiple choice** — `app.multiselect` (T0.1)
- [x] **Layout** — `app.columns`, `app.tabs`, `app.sidebar` (T0.2)
- [x] **Hot reload** — re-register on save, broken edits keep the last good app (T0.3)

**Tier 1 — the app stops being a form**

- [ ] **Multipage + query params** — `pages/`-style navigation and URL state, so a run is
      a shareable link. Pairs with #9: a cached result keyed by inputs is what such a
      URL points at.
- [ ] **Derived widget props** — options/visibility computed in Rust from another
      widget's value (`options_from(...)`, show/hide predicates). #7 is the narrow case;
      the general gap is that nothing can happen between widgets without submitting a
      job. This is the deliberate architectural difference from Streamlit's rerun model,
      so the goal is *cheap* reactivity, not a rerun loop.
- [ ] **Interactive dataframe** — sort, filter, column config; `table_preview` is a
      static first-100-rows dump today.
- [ ] **One-call charts** — `app.line_chart(df)` without hand-building Plotly JSON in a
      job. Composes with #3 (performance-safe plotting defaults).
- [x] **Alerts and metrics** — `info`/`warning`/`error`/`success` boxes and KPI tiles (T1.1)
- [ ] **Multi-file upload** — TPP-shaped apps take a directory of files, not one.

**Tier 2 — ecosystem**

- [ ] **Custom-component API** — adding a widget means hand-editing four places today
      (protocol crate, `protocol.py`, `types.ts`, a React component), so only a
      maintainer can extend tubulin. #6's codegen is the prerequisite.
- [ ] **Result cache behind shareable URLs** — #9 plus Tier 1's query params.
- [ ] **Deployment story** — Docker/k8s guide, secrets, and basic auth. Note this
      crosses the single-tenant line drawn in [architecture.md](architecture.md#limitations);
      see "Out of scope by design" at the end of this file. It is a deliberate scope
      decision, not a gap to close by default.

### T0.1 Widget surface — ✅ done

Six inputs became nine, plus rich text. `app.markdown` (a small CommonMark subset
rendered straight to React nodes — no markdown dependency, no `dangerouslySetInnerHTML`),
`app.text_input` / `app.text_area` (debounced: keystrokes stay local, one session write
after idle or on blur), and `app.multiselect` (emits in *declaration* order, so a job
can index into it deterministically). `enabled_when=` now works on every input, not just
checkboxes.

### T0.2 Layout containers — ✅ done

`app.columns(...)`, `app.tabs(...)`, `app.sidebar()` — context managers alongside the
existing `app.group(...)`. The AppSpec stays a **flat** node list with balanced
`<kind>_start`/`<kind>_end` markers; [`frontend/src/layout.ts`](../frontend/src/layout.ts)
folds them into a tree at render time, and `CONTAINERS` there is the single place a new
container is registered. A widget's value therefore seeds and flows identically however
deeply it is nested. Tabs keep hidden panels mounted, for the same reason a collapsed
group does. Every widget and container in one runnable page:
[`examples/widgets`](../examples/widgets/ui.py).

### T0.3 Hot reload — ✅ done

[`reload.rs`](../crates/tubulin-runtime/src/reload.rs) watches the app's `.py` files and
re-runs the registration pass on save, pushing a fresh `ui_tree` to every open browser.
The registration pass being cheap and self-contained is what makes this the *whole* of
hot reload. A save that doesn't parse keeps the last good spec serving and surfaces the
traceback as a banner — a half-typed edit never blanks the page. `--no-reload` opts out.
See [architecture.md](architecture.md#hot-reload) for why it polls mtimes rather than
taking a filesystem-notify dependency.

### T0.4 Installable wheels — ✅ done (publishing not yet run)

There was no `pip install tubulin`: the build backend produced a pure-Python wheel with
no console entry point, so the binary only existed via `cargo run`. Now maturin
(`bindings = "bin"`) builds one wheel carrying the compiled runtime, the Python package,
and the built frontend. `make wheel` builds it locally;
[`.github/workflows/release.yml`](../.github/workflows/release.yml) builds Linux and
macOS wheels on x86_64 and arm64, **smoke-tests each one** in a clean venv before it can
be published, and publishes on a `v*` tag via trusted publishing.

The supporting change is in [`config/mod.rs`](../crates/tubulin-runtime/src/config/mod.rs):
a source checkout and an installed wheel resolve the interpreter, `PYTHONPATH` and the
frontend directory differently, and every Python spawn now goes through
`Config::python_command()` so the two can't drift. Verified end to end — wheel installed
into a fresh venv outside the repo, `tubulin run` with no `TUBULIN_*` env set: app
registered, frontend served from inside the package, job ran to completion with tables
and a Plotly figure.

**The rename.** This framework was called *kinesin* until the first publish attempt ran
into a wall: `kinesin` is taken on PyPI by an unrelated 1 KB placeholder package
(`"""Placeholder."""`, uploaded 2026-04-05), and taken on crates.io as well. There was no
PyPI project to create. Worse, the squatter owns the *import* name too: with the real
package installed, `pip install kinesin` silently overwrote `site-packages/kinesin/__init__.py`
with the stub — no pip warning — breaking `from kinesin import App`, and uninstalling the
stub left the package broken while `pip list` still reported it healthy. Renaming to
**tubulin** (free on both registries) removes the collision outright rather than working
around it with a suffixed distribution name. Tubulin is the subunit that polymerizes into
the microtubule — the track a kinesin motor walks along — so the metaphor still holds.

**Not done:** the package has never been published, so `pip install tubulin` does not
work yet — it needs a PyPI project and the `pypi` environment configured on the repo.

**Fixed in the same pass**, from the review that found the name collision:
- **The sdist shipped without a frontend** — `include` scoped `_frontend` to
  `format = "wheel"` and the `sdist` job never built it, so any source-fallback install
  compiled a runtime that served 404 for every page. The sdist now carries the built
  frontend (`make sdist`, and a node step in the workflow's sdist job), and CI asserts
  `_frontend/index.html` is inside the tarball before it can be published. Verified by
  building a wheel *from the sdist alone*, with no node and outside the repo.
- **The missing-frontend warning was wrong for installed users** — it said
  `run 'cd frontend && npm run build'`, which a pip user cannot act on. The message now
  branches on `Config::is_source_checkout()`, the named form of the `pythonpath`
  discriminator that already distinguished the two deployment shapes.
- **Bare PyPI metadata** — added classifiers, `[project.urls]`, and keywords, and moved
  `license = { text = "MIT" }` to the PEP 639 `license = "MIT"` + `license-files` form.
  That also fixed a real mismatch: `PKG-INFO` advertised `License-File: LICENSE` while
  the sdist contained no `LICENSE`. `twine check` passes on both distributions.
  Note the SPDX expression and a `License ::` classifier are mutually exclusive under
  PEP 639 — carrying both makes PyPI reject the upload.

**Windows is not a matrix row — it is a port.** The obvious reading of "no Windows
wheel" is that the release matrix is missing an entry. It isn't: `workers/socket.rs`
is compiled unconditionally and builds on `tokio::net::UnixStream`, which does not
exist on Windows (verified — a minimal probe crate fails to resolve it for
`x86_64-pc-windows-msvc`). The fork-server is already gated to Linux and
`config/mod.rs` already has a `#[cfg(windows)]` interpreter branch, so the remaining
work is the socket transport: gate `mod socket` behind `#[cfg(unix)]` and let Windows
fall back to the per-job process transport. Until someone does that and can test it on
a real Windows runner, adding the matrix row would only turn the release workflow red.
The classifiers name Linux and macOS only, so the exclusion is at least declared.

---

### T0.5 First-run papercuts — ✅ done

All three shipped for 0.1: the bundle is saved as `<App_Title>_results_<id>.zip`
(`bundle_filename` in `jobs/outputs.rs`), `tubulin --version` / `-V` prints the build,
and `tubulin new <dir>` writes the starter app from `crates/tubulin-runtime/templates/`
(kept working by `tests/test_scaffold.py`; `scripts/smoke-dist.sh` checks both commands
from an installed wheel). The original write-up follows.

T0.4's review closed the packaging gaps and settled the Windows question. Three small
things were left, all of them things a first-time author meets in their first ten
minutes and none of them touching the two hard problems above.

1. **The download bundle is named after the example app.** `send_download` in
   [`jobs/outputs.rs`](../crates/tubulin-runtime/src/jobs/outputs.rs) hardcodes
   `format!("TPP_results_{short}.zip")`, so *every* tubulin app hands its users a zip
   named after a protein-thermal-profiling example they have never heard of. Framework
   code carrying an example's name is exactly what a new author notices first. Derive
   it from the app title, or take a `filename=` on `app.download(...)`.

2. **No `tubulin --version`.** [`cli.rs`](../crates/tubulin-runtime/src/cli.rs) handles
   `run`, `-h` and `--help`; `tubulin --version` exits 2 with usage. Once wheels ship
   across four platform targets, "which build are you on" is the first question of
   every bug report, and there is no way to answer it.

3. **No scaffold.** `tubulin run <ui.py>` needs a `ui.py` that doesn't exist yet, so
   the first minute of the project is copy-pasting from the README. A
   `tubulin new <dir>` writing a runnable `ui.py` + `tubulin.toml` is the smallest
   thing that turns "installed" into "running app".

---

### T1.1 Alerts and metrics — ✅ done

**Pain:** every result an app produced was a table, a figure or a log line. There was
no way to say *"only two replicates — read the CI accordingly"* next to the control it
qualifies, and no way to put the one number a reader actually came for at the top. TPP
ended up with both: caveats buried in `app.markdown` prose with no visual weight, and
headline counts findable only by reading a table.

**Shipped:**
- **Callouts** — `app.info` / `success` / `warning` / `error` (and `app.alert(level, …)`
  when the level is computed), with an optional `title=`. The body goes through the
  *same* markdown renderer as `app.markdown` — `blocks()` is now exported from
  [`Markdown.tsx`](../frontend/src/widgets/Markdown.tsx) rather than reimplemented — so
  a caveat can carry a link or a bolded unit. Errors get `role="alert"` /
  `aria-live="assertive"`; the rest announce politely, because a warning that steals
  focus from a running job is worse than one the reader reaches in their own time.
- **Metric tiles** — `app.metric(label, value)` for a number known at registration, and
  `app.metric(label, job=binding, name="n_fit")` for one a job produces. The split
  matters: the **UI** owns the label, help text and `delta_color`; the **job** supplies
  only `job.metric(name, value, delta=…)`. The runtime's `metric:<name>` view sends
  just `value`/`delta` and the client shallow-merges, so a job never restates its own
  presentation.
- `delta_color="inverse"` for the metrics where lower is better (an error rate, a
  runtime) — the one piece of formatting that is genuinely the app author's call.

**The mechanism is deliberately boring:** `job.metric` writes a tiny JSON file under
`result_dir` and registers it as `outputs[name]`, exactly like every other named
output. So it shards without special-casing (write it from a `job.primary`), it works
in the headless harness (`RunResult.metric(name)`), and
[`jobs/outputs.rs`](../crates/tubulin-runtime/src/jobs/outputs.rs) grew one more
`strip_prefix` arm rather than a new transport. No protocol-crate change was needed —
`Node.props` is already free-form — which is a useful data point for Tier 2's
custom-component API: the *wire* is not what makes adding a widget expensive.

**Also fixed on the way:** the dark theme never overrode `--success`/`--warn`/`--error`/
`--info`, so the light-mode status colours sat at ~2.7:1 on the dark background —
acceptable as a 1px border, unreadable as the alert copy that now uses them. Brightened
in [`design/tokens.css`](../design/tokens.css); measured 13.3:1 for body text and 7.9:1
for a coloured title in the running app.

**Covered by:** 7 Python tests (level validation, dedent, static vs bound props, the
half-binding and bad-`delta_color` errors, a harness round-trip, scalar coercion) and
17 vitest tests (unknown-level fallback, markdown in the body, aria roles, number
formatting including `0.1+0.2`, delta sign parsing for strings, `inverse`/`off`,
not-ready state). Both alerts and metrics are in
[`examples/widgets`](../examples/widgets/ui.py) and were verified in the running app.

**Not done:** callouts are static — an app can't raise one *from* a job. The natural
shape is a `alert:<name>` view mirroring `metric:<name>`, and it should wait until
roadmap #8 (job failure surfacing) lands so the two don't invent different vocabularies
for "something went wrong".

---

Items #12–#17 came out of the #11 review pass and the dependency-footprint
analysis: gaps the code itself exposed once the native I/O layer landed.
Suggested order for what's next: **#12 (CI protects everything else, ✅ done) → #9 →
#8 → #13**, with #3/#7 as small-item filler.

## 12. CI + committed compat fixture — ✅ done

**Pain:** the codebase had real test coverage (Rust unit tests, the Python suite, the
TPP integration test) and **nothing ran it** — no `.github/workflows` existed. The one
test covering real polars-parquet compatibility was `#[ignore]`d behind a
`TUBULIN_TEST_PARQUET` env var pointing at an uncommitted fixture, so it could only
ever run on the machine where it was written.

**Shipped:**
- **[`.github/workflows/ci.yml`](../.github/workflows/ci.yml)** — four jobs that
  *invoke* the `make` targets rather than restating them: rust (`make lint-rust`,
  `make test-rust`), python (`make lint-python`, `make test-python`, across 3.10 and
  3.13), frontend (`make lint-frontend`, `make test-frontend`, `make build-frontend`),
  and packaging (below).

  "Mirroring" the targets by hand is what the first version did, and it drifted:
  CI gated on `cargo fmt --all -- --check` while `make lint-all` never ran rustfmt at
  all, so the tubulin rename reordered imports, passed `make lint-all` locally, and
  would have failed CI. Invoking the targets makes that class of divergence
  impossible — and `lint-rust` now runs `fmt --check` itself.

- **Packaging runs on every PR** — `make wheel`, `make sdist`, an assertion that both
  carry `_frontend/index.html`, `twine check`, and a clean-venv smoke test of the
  wheel. Packaging used to be exercised only on a `v*` tag, which is precisely how the
  frontend-less sdist survived: nothing built one until release day. The Python
  matrix exists for the same reason — `requires-python = ">=3.10"` was a claim no job
  tested.

- **[`scripts/smoke-dist.sh`](../scripts/smoke-dist.sh)** — one smoke test shared by CI
  and the release workflow. It installs a distribution into a throwaway venv *outside*
  the repo with no `TUBULIN_*` env set, starts a real app, and checks `/health`,
  `index.html`, **and that the referenced JS and CSS assets return 200**. That last
  part is the point: a frontend-less install answers `/health` perfectly well, so a
  health check alone would have passed the exact bug this is here to catch. Verified
  against a deliberately broken sdist — it fails, as it should. CI smoke-tests the
  wheel and checks the sdist structurally (building an sdist recompiles the runtime
  from scratch); release.yml runs the full smoke test on both before publishing.
- **Committed parquet fixture** — [`tests/fixtures/polars_written.parquet`](../tests/fixtures/polars_written.parquet)
  (1.4 KB, zstd) plus the script that regenerates it. `reads_polars_written_parquet`
  dropped its `#[ignore]` and env var; the compatibility this roadmap advertised as
  "verified" is now actually gated.
- **Frontend test suite** — the gap the review didn't name: the React layer had *zero*
  tests, and a hang-on-every-link bug in the markdown renderer shipped because of it
  (a module-level `/g` regex whose `lastIndex` was reset by a recursive call, spinning
  forever and freezing the tab). 59 tests under vitest + happy-dom now cover the
  markdown scanner (links, malformed fences, href policy, HTML escaping), the layout
  marker→tree fold (nesting, stray/unbalanced markers, sidebar hoisting), multiselect
  ordering and filtering, the text-input debounce (including flush-on-unmount), and
  container rendering (columns tracks, tab panels staying mounted, `enabled_when`
  swallowing events).
- **Declared the example-only deps** — `scipy`/`matplotlib`/`pandas`/`plotly` were
  installed ad-hoc in the dev venv and declared nowhere, so CI could never have run the
  TPP test. They are now an `examples` dependency group, deliberately outside the
  framework's runtime floor (#16).

> Both parsers now carry an explicit **progress budget**: a scan that fails to advance
> bails out with a truncated render instead of spinning. That matters beyond the bug —
> vitest cannot interrupt a synchronous infinite loop, so without it a regression
> crashes the worker after 10s instead of failing an assertion in 360ms.

## 13. Native csv/ipc preview — delete `preview.py` — small-medium effort

**Pain:** the preview contract lives in two implementations (Rust for parquet,
Python for csv/ipc), and they **already drifted**: `preview.py` emitted bare
`NaN` tokens that serde_json rejects — found and patched in the #11 review, but
the dual-contract structure that produced the bug is still standing.

**Add:** the `arrow` crate already reads CSV and IPC; extend
[`artifacts/preview.rs`](../crates/tubulin-runtime/src/artifacts/preview.rs) to cover them and delete
`python/tubulin/preview.py` entirely. Kills the last preview subprocess *and*
the drift class structurally, instead of patching divergences as they're found.
(Same shape as #11's zipjob removal.)

## 14. Fork-transport timeout kill — small effort

**Pain:** #4's documented open gap: a job that times out under the fork
transport can't be force-killed — closing the socket only sends the child EPIPE
on its *next* emit, so a hung child (stuck in native code, emitting nothing)
leaks a process and its memory indefinitely.

**Add:** the forked child reports its PID as its first protocol line; on timeout
the runtime (which already detects it in
[`workers/socket.rs`](../crates/tubulin-runtime/src/workers/socket.rs)) sends
SIGKILL. Process exit is already the memory-reclamation mechanism — this just
makes it enforceable.

## 15. `GET /jobs` endpoint — trivial

**Pain:** `JobRecord` already tracks id/status/result/logs per job behind an
`#[allow(dead_code)]` promising "a future /jobs endpoint"
([`jobs/`](../crates/tubulin-runtime/src/jobs)) — the data is collected and
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
only pandas interop needs it, make it an optional extra (`tubulin[pandas]`).
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

[`architecture.md`](architecture.md) declares tubulin single-tenant, in-memory,
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
