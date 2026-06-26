"""The Kinesin job, written on the parallel primitive.

`@app.job(parallel=True)` makes the runtime run this as N workers (it picks N). The
job never touches shard indices: `job.map(...)` fans the per-protein plotting out
across the workers and `job.primary(...)` runs the expensive single-copy analysis
once. The framework handles slicing, merging, progress, and the download bundle.
"""

from __future__ import annotations

from app_def import app

import tpp_pipeline as tpp


def _pq(path, df):
    df.write_parquet(path)
    return path


def _txt(path, content):
    with open(path, "w") as fh:
        fh.write(content)
    return path


@app.job(parallel=True)
def tpp_analyze(
    tsv_path, meta_path, normalize, ref_temp, r2_threshold, seed,
    show_volcano, show_violin, show_distribution, show_curves, show_normality, max_curves, job,
):
    sel = float(ref_temp) if ref_temp not in (None, "") else None

    # Cheap context every shard needs to plot (load + impute + reshape, no stats-fit).
    rd, params = tpp.preprocess_only(
        tsv_path, meta_path, normalize=bool(normalize),
        selected_temp=sel, r2_threshold=float(r2_threshold), seed=int(seed),
    )
    proteins = sorted({p for treatment in rd.values() for p in treatment})
    if int(max_curves) > 0:
        proteins = proteins[: int(max_curves)]

    # Fan the curves out across the runtime's workers — but NOT shard 0, which is busy
    # with the analysis below; this overlaps the two instead of piling on shard 0.
    if show_curves:
        job.map(proteins, lambda p: tpp.plot_one(p, rd, params, "averaged"),
                into="avg_curves", progress=(0, 50), skip_primary=True)
        job.map(proteins, lambda p: tpp.plot_one(p, rd, params, "replicate"),
                into="rep_curves", progress=(50, 100), skip_primary=True)

    # Expensive single-copy outputs (stats + summaries): once, on shard 0, in parallel
    # with the other shards rendering curves.
    @job.primary
    def _summary():
        job.log(f"Shard 0 runs the analysis; shards 1-{max(job.num_shards - 1, 1)} render curves")
        full = tpp.run(
            tsv_path, meta_path, mode="kinesin", normalize=bool(normalize),
            selected_temp=sel, r2_threshold=float(r2_threshold), seed=int(seed), log_cb=job.log,
        )
        job.progress(60)
        stats = full["stats_df"]
        treatments = [full["metrics"]["control"], full["metrics"]["treatment"]]
        base = f"{job.result_dir}/{job.id}"

        job.result(_pq(f"{base}_stats.parquet", stats))
        if show_volcano:
            job.output("volcano", _txt(f"{base}_volcano.json", tpp.plotly_volcano(stats)))
        if show_violin:
            job.output("violin", _txt(f"{base}_violin.json",
                                      tpp.plotly_violin(full["averaged_table"], treatments)))
        if show_distribution:
            job.output("distribution", _txt(f"{base}_dist.json",
                                            tpp.plotly_distribution(full["summary_table"])))
        avg_table = tpp.averaged_summary(rd, params)
        job.table("averaged", _pq(f"{base}_averaged.parquet", avg_table))
        norm = tpp.normality_table(full["averaged_table"]) if show_normality else None
        if norm is not None and not norm.is_empty():
            job.table("normality", _pq(f"{base}_normality.parquet", norm))

        # Download bundle: CSV summaries + overview SVGs + every shard's curves.
        files = {
            "data/statistics.csv": _txt(f"{base}_b_stats.csv", stats.write_csv()),
            "data/replicate_summary.csv": _txt(f"{base}_b_rep.csv", full["replicate_table"].write_csv()),
            "data/averaged_summary.csv": _txt(f"{base}_b_avg.csv", avg_table.write_csv()),
            "figures/volcano.svg": _txt(f"{base}_b_volcano.svg", tpp.plot_volcano(stats)),
            "figures/violin.svg": _txt(f"{base}_b_violin.svg", tpp.plot_violin(full["averaged_table"], treatments)),
            "figures/distribution.svg": _txt(f"{base}_b_dist.svg", tpp.plot_distribution(full["summary_table"])),
        }
        if norm is not None and not norm.is_empty():
            files["data/normality.csv"] = _txt(f"{base}_b_norm.csv", norm.write_csv())
        job.bundle(files=files, dirs={"figures/averaged": "avg_curves", "figures/replicates": "rep_curves"})

        m = full["metrics"]
        job.log(f"{m['proteins_fit']} proteins fit, {m['significant']} significant (FDR<0.05)")
        job.progress(100)
