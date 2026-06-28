# Parallel jobs

A job marked `@app.job(parallel=True)` can be run by the runtime as **several worker
processes at once**. The author writes two verbs — `job.map` and `job.primary` — and
never touches shard indices, slicing, or merge plumbing.

## The model

```
click ─► JobManager.submit ─► run_sharded_job
            │
            │  num_shards = min(max_workers, cpu_cores)          ← the runtime decides
            ├─► worker shard 0 ─┐
            ├─► worker shard 1 ─┤ each fits the cheap context, plots its slice,
            ├─► worker shard 2 ─┤ writes SVGs into ONE shared dir; shard 0 also writes
            └─► worker shard 3 ─┘ the single-copy outputs (stats, plots, tables)
            │
            ▼ (await all)
        list the shared dir → gallery;  zip on demand → download
```

### `job.map(items, fn, into)`

Runs `fn` over **only this worker's slice** of `items` (a strided slice, so load is
spread evenly). If `fn(item)` returns a matplotlib `Figure`, the framework saves it as
`<item>.svg` and closes it; otherwise `fn` writes its own files. Every shard writes into
the directory bound to output `into`, and streams progress.

**Balancing and progress are automatic — the app declares *what* to fan out, not
*how*.** Two things the author used to hand-tune are now inferred from the plan:

- **Skip the primary shard.** If the job also declares a `job.primary` (see below),
  shard 0 is kept out of the map and the items are split across the remaining shards
  (`items[(shard-1)::(num_shards-1)]`), so the rendering **overlaps** the single-copy
  analysis instead of piling onto the same worker. With no primary, all shards map
  (`items[shard::num_shards]`).
- **Aggregate progress.** Each shard's maps share the 0–100 bar in proportion to how
  many items each got, so several maps advance one smooth bar with no per-map ranges.

This works because `map`/`primary` only *register* the work; the runtime executes it
after the job function returns, when it can see the whole plan.

> **Advanced overrides.** `job.map(..., skip_primary=<bool>)` forces whether shard 0
> participates, and `job.map(..., progress=(lo, hi))` pins a map to a sub-range it owns.
> Leave both at their default (`None`) for the automatic behaviour above — most apps
> should.

### `job.primary(fn)` / `@job.primary`

Runs `fn` once, on shard 0 only. **Put the expensive single-copy work here** (the
stats fit, summary tables, overview plots, the download manifest) so it isn't
duplicated across workers.

### Merge

There is no inter-process result passing. After all shards finish, the runtime
**lists the shared directory** to build each gallery, and uses shard 0's registered
outputs for the single-copy artifacts. The download `.zip` is built lazily by
`/bundle` from a manifest shard 0 wrote (its dir-globs pick up every shard's files).

## Avoiding redundant work

The naïve mistake is to do the full analysis in every shard. The pattern that avoids
it: a **cheap** context that every shard needs to plot, and the **expensive** analysis
only on the primary.

```python
@app.job(parallel=True)
def tpp_analyze(tsv, meta, …, job):
    rd, params = tpp.preprocess_only(tsv, meta, …)     # cheap: load+impute+reshape
    proteins = sorted(rd_proteins)

    job.map(proteins, lambda p: tpp.plot_one(p, rd, params, "averaged"), into="avg")
    job.map(proteins, lambda p: tpp.plot_one(p, rd, params, "replicate"), into="rep")

    @job.primary
    def _summary():
        full = tpp.run(tsv, meta, …)        # the expensive stats fit — once, on shard 0
        job.result(write(full.stats))
        job.output("volcano", write(plotly_volcano(full.stats)))
        job.bundle(files={…}, dirs={"figures/averaged": "avg", "figures/replicates": "rep"})
```

On the TPP example (1242 curves, a 14-core machine), the progression was:

| | wall time |
| --- | --- |
| single worker | ~54s |
| 4 shards, full fit in each | ~20s |
| 14 shards (auto-cores), full fit in each | ~15s |
| **14 shards, fit only on the primary** | **~13s** |

Per-worker peak stays ~224 MB (one figure at a time); the analysis is no longer
duplicated 14×.

## Honest comparison to `multiprocessing.Pool`

For a **single job in isolation, `job.map` is essentially `mp.Pool(cpu_count()).map`**:
same fan-out, same `min(workers, cores)`, same per-process import tax. On macOS both
use `spawn`, so even the memory cost is identical; on Linux `mp` with `fork` shares
imports copy-on-write and would actually use *less* memory than our spawn-based shards.

The thing the runtime model adds is **where the pool lives**:

- `mp.Pool` spawns its workers *inside one job's Python process*. The runtime sees one
  worker and cannot see or budget the pool's children. Two users → `2 × cpu_count()`
  processes, and `max_workers` is silently bypassed.
- Kinesin's shards *are* the runtime's workers — counted by the semaphore, schedulable
  against other jobs.

So the value is **global resource arbitration**, which only matters when there is more
than one concurrent job. And once "many users" means more than one machine, `mp` can't
help at all — it's single-host; the shard descriptor (JSON work unit over stdio) is
the thing that could be dispatched to a remote worker fleet.

## What's missing for multi-tenant servers

The shard *mechanism* is sound; the *policy and isolation* a server needs are not
built:

- **Fairness.** `decide_shards = min(max_workers, cores)` lets one job take the whole
  pool; concurrent jobs serialize behind it. A server needs a per-job cap (decoupled
  from `max_workers`), fair-share or priority scheduling, and per-user concurrency
  limits. The current default (`max_workers=0` = all cores) is tuned for single-job
  speed and is wrong for sharing.
- **Tenant isolation.** Jobs aren't scoped to a user; the file routes serve any
  job's results to anyone who knows the (uuid) id. Needs auth + per-owner scoping.
- **Density.** Each worker re-pays the ~181 MB import baseline per job per shard. A
  **warm worker pool** (pre-imported processes, recycled after K jobs or above an RSS
  cap) is the single biggest lever for serving many users on finite RAM.
- **Durability / scale-out.** The queue and state are in-memory and single-process; no
  durable broker, no horizontal scaling.

## Build vs. ride

Building a robust multi-tenant distributed scheduler is a large, perennial effort. A
pragmatic path: keep Kinesin's defensible value — the Python-app-to-web-app DX, the
session/binding model, the disk-backed memory discipline — and **delegate execution to
a proven backend**: Ray or Dask (Python scientific compute, cluster scheduling, fair
allocation), Celery/Temporal (language-agnostic, durable queues), or K8s Jobs. The
`job.map` / `job.primary` API stays as the dev-facing surface; only its backend
changes from "the runtime spawns processes" to "submit to the scheduler." Having built
the abstraction means the implementation is swappable.
