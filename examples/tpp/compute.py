"""The Kinesin job, written on the parallel primitive.

`@app.job(parallel=True)` makes the runtime run this as N workers (it picks N). The
job never touches shard indices: `job.map(...)` fans the per-protein plotting out
across the workers and `job.primary(...)` runs the expensive single-copy analysis
once. The framework handles slicing, merging, progress, and the download bundle.
"""

from __future__ import annotations

from app_def import app

import tpp_pipeline as tpp


@app.job(parallel=True)
def tpp_analyze(
    tsv_path,
    meta_path,
    normalize,
    ref_temp,
    r2_threshold,
    seed,
    show_volcano,
    show_violin,
    show_distribution,
    show_curves,
    perform_sw,
    sw_log,
    sw_sqrt,
    sw_boxcox,
    sw_yeojohnson,
    max_curves,
    job,
):
    sel = float(ref_temp) if ref_temp not in (None, "") else None

    # Cheap context every shard needs to plot (load + impute + reshape, no stats-fit).
    rd, params = tpp.preprocess_only(
        tsv_path,
        meta_path,
        normalize=bool(normalize),
        selected_temp=sel,
        r2_threshold=float(r2_threshold),
        seed=int(seed),
    )
    proteins = sorted({p for treatment in rd.values() for p in treatment})
    if int(max_curves) > 0:
        proteins = proteins[: int(max_curves)]

    # Fan the curves out across the runtime's workers. Because the job also declares a
    # primary (below), the framework automatically keeps shard 0 — busy with that
    # analysis — out of the maps and aggregates the two maps' progress, so this app
    # never tunes shard slicing or progress ranges.
    if show_curves:
        job.map(
            proteins,
            lambda p: tpp.plot_one(p, rd, params, "averaged"),
            into="avg_curves",
        )
        job.map(
            proteins,
            lambda p: tpp.plot_one(p, rd, params, "replicate"),
            into="rep_curves",
        )

    # Expensive single-copy outputs (stats + summaries): once, on shard 0, in parallel
    # with the other shards rendering curves.
    @job.primary
    def _summary():
        job.log(
            f"Shard 0 runs the analysis; shards 1-{max(job.num_shards - 1, 1)} render curves"
        )
        full = tpp.run(
            tsv_path,
            meta_path,
            mode="kinesin",
            normalize=bool(normalize),
            selected_temp=sel,
            r2_threshold=float(r2_threshold),
            seed=int(seed),
            log_cb=job.log,
        )
        job.progress(60)
        stats = full["stats_df"]
        treatments = [full["metrics"]["control"], full["metrics"]["treatment"]]

        # Tables: hand the dataframe straight to the framework — it writes the parquet
        # under result_dir; the runtime previews it natively.
        job.result(stats)
        if show_volcano:
            job.output(
                "volcano", job.write_text("volcano.json", tpp.plotly_volcano(stats))
            )
        if show_violin:
            job.output(
                "violin",
                job.write_text(
                    "violin.json", tpp.plotly_violin(full["averaged_table"], treatments)
                ),
            )
        if show_distribution:
            job.output(
                "distribution",
                job.write_text(
                    "dist.json", tpp.plotly_distribution(full["summary_table"])
                ),
            )
        avg_table = tpp.averaged_summary(rd, params)
        job.table("averaged", avg_table)
        # Shapiro-Wilk only if requested; the toggles pick which transformations.
        norm = None
        if perform_sw:
            selected = [
                name
                for name, on in (
                    ("Log(ΔTm + shift)", sw_log),
                    ("Square Root", sw_sqrt),
                    ("Box-Cox", sw_boxcox),
                    ("Yeo-Johnson", sw_yeojohnson),
                )
                if on
            ]
            norm = tpp.normality_table(full["averaged_table"], transforms=selected)
        if norm is not None and not norm.is_empty():
            job.table("normality", norm)

        # Download bundle: CSV summaries + overview SVGs + every shard's curves.
        files = {
            "data/statistics.csv": job.write_csv("b_stats", stats),
            "data/replicate_summary.csv": job.write_csv(
                "b_rep", full["replicate_table"]
            ),
            "data/averaged_summary.csv": job.write_csv("b_avg", avg_table),
            "figures/volcano.svg": job.write_text(
                "b_volcano.svg", tpp.plot_volcano(stats)
            ),
            "figures/violin.svg": job.write_text(
                "b_violin.svg", tpp.plot_violin(full["averaged_table"], treatments)
            ),
            "figures/distribution.svg": job.write_text(
                "b_dist.svg", tpp.plot_distribution(full["summary_table"])
            ),
        }
        if norm is not None and not norm.is_empty():
            files["data/normality.csv"] = job.write_csv("b_norm", norm)
        job.bundle(
            files=files,
            dirs={"figures/averaged": "avg_curves", "figures/replicates": "rep_curves"},
        )

        m = full["metrics"]
        job.log(
            f"{m['proteins_fit']} proteins fit, {m['significant']} significant (FDR<0.05)"
        )
        job.progress(100)
