"""The Kinesin job: run the TPP analysis in disposable worker(s) and persist results.

This job is **shardable**: the runtime may run it as several parallel worker
processes, each handed a ``job.shard`` / ``job.num_shards``. Every shard fits the
data (cheap) and plots its slice ``proteins[shard::num_shards]`` of the melting
curves; shard 0 (the "primary") additionally writes the single-artifact outputs
(stats table, overview plots, summary tables) and the download manifest. The
runtime decides how many shards to spawn and aggregates everything.
"""

from __future__ import annotations

import json
import platform
import resource
import time

from app_def import app

import tpp_pipeline as tpp


def _peak_rss_mb() -> float:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss / (1024 * 1024) if platform.system() == "Darwin" else rss / 1024


@app.job(shardable=True)
def tpp_analyze(
    tsv_path: str,
    meta_path: str,
    normalize,
    ref_temp,
    r2_threshold,
    seed,
    show_volcano,
    show_violin,
    show_distribution,
    show_curves,
    show_normality,
    max_curves,
    job,
):
    t_start = time.time()
    primary = job.is_primary

    if primary:
        job.log("Starting TPP analysis (Kinesin-managed, disk-backed)")
        if job.num_shards > 1:
            job.log(f"Runtime split this job across {job.num_shards} parallel workers")

    # Every shard fits (cheap); only the primary narrates.
    result = tpp.run(
        tsv_path,
        meta_path,
        mode="kinesin",
        normalize=bool(normalize),
        selected_temp=(float(ref_temp) if ref_temp not in (None, "") else None),
        r2_threshold=float(r2_threshold),
        seed=int(seed),
        log_cb=(job.log if primary else None),
    )

    metrics = result["metrics"]
    stats_df = result["stats_df"]
    rd, params = result["replicate_data"], result["params"]
    treatments = [metrics["control"], metrics["treatment"]]
    base = f"{job.result_dir}/{job.id}"
    avg_dir = f"{base}_curves_avg"
    rep_dir = f"{base}_curves_rep"

    outputs: dict[str, str] = {}
    result_path = None

    # --- single-artifact outputs + download bundle: primary shard only ----------
    if primary:
        result_path = f"{base}_stats.parquet"
        stats_df.to_parquet(result_path, index=False)
        if show_volcano:
            outputs["volcano"] = _write(f"{base}_volcano.json", tpp.plotly_volcano(stats_df))
        if show_violin:
            outputs["violin"] = _write(
                f"{base}_violin.json", tpp.plotly_violin(result["averaged_table"], treatments)
            )
        if show_distribution:
            outputs["distribution"] = _write(
                f"{base}_dist.json", tpp.plotly_distribution(result["summary_table"])
            )
        avg_table = tpp.averaged_summary(rd, params)
        outputs["averaged"] = f"{base}_averaged.parquet"
        avg_table.to_parquet(outputs["averaged"], index=False)
        norm = tpp.normality_table(result["averaged_table"]) if show_normality else None
        if norm is not None and not norm.empty:
            outputs["normality"] = f"{base}_normality.parquet"
            norm.to_parquet(outputs["normality"], index=False)

        # Galleries are directories the runtime lists after every shard finishes.
        if show_curves:
            outputs["gallery_avg"] = avg_dir
            outputs["gallery_rep"] = rep_dir

        # Download manifest: the runtime zips this on demand (dir globs pick up the
        # curves written by *all* shards).
        _write_bundle_manifest(base, result, stats_df, avg_table, norm, treatments, avg_dir, rep_dir)

    # --- map step: every shard plots its slice of the curves --------------------
    if show_curves:
        if primary:
            job.log(f"Plotting melting curves across {job.num_shards} worker(s)…")
        tpp.generate_curves(
            rd, params, avg_dir, kind="averaged",
            shard=job.shard, num_shards=job.num_shards, limit=int(max_curves),
            progress_cb=lambda i, n: job.progress(int(50 * i / n)),
        )
        tpp.generate_curves(
            rd, params, rep_dir, kind="replicate",
            shard=job.shard, num_shards=job.num_shards, limit=int(max_curves),
            progress_cb=lambda i, n: job.progress(50 + int(50 * i / n)),
        )

    if primary:
        job.log(
            f"{metrics['proteins_fit']} proteins fit, {metrics['significant']} significant "
            f"(FDR<0.05); analysis {metrics['t_total_s']}s"
        )
        job.log(f"Primary worker peak memory: {_peak_rss_mb():.0f} MB")

    return {"result_path": result_path, "outputs": outputs}


def _write(path: str, content: str) -> str:
    with open(path, "w") as fh:
        fh.write(content)
    return path


def _write_bundle_manifest(base, result, stats_df, avg_table, norm, treatments, avg_dir, rep_dir):
    """Describe the download .zip; kinesin.zipjob builds it on demand, expanding the
    curve directory globs so it picks up every shard's curves."""
    entries = [
        {"file": _write(f"{base}_b_statistics.csv", stats_df.to_csv(index=False)),
         "arc": "data/statistics.csv"},
        {"file": _write(f"{base}_b_replicate.csv", result["replicate_table"].to_csv(index=False)),
         "arc": "data/replicate_summary.csv"},
        {"file": _write(f"{base}_b_averaged.csv", avg_table.to_csv(index=False)),
         "arc": "data/averaged_summary.csv"},
        {"file": _write(f"{base}_b_volcano.svg", tpp.plot_volcano(stats_df)),
         "arc": "figures/volcano.svg"},
        {"file": _write(f"{base}_b_violin.svg", tpp.plot_violin(result["averaged_table"], treatments)),
         "arc": "figures/violin.svg"},
        {"file": _write(f"{base}_b_distribution.svg", tpp.plot_distribution(result["summary_table"])),
         "arc": "figures/distribution.svg"},
        {"glob": f"{avg_dir}/*.svg", "arc_dir": "figures/averaged", "suffix": "_averaged"},
        {"glob": f"{rep_dir}/*.svg", "arc_dir": "figures/replicates"},
    ]
    if norm is not None and not norm.empty:
        entries.insert(3, {"file": _write(f"{base}_b_normality.csv", norm.to_csv(index=False)),
                           "arc": "data/normality.csv"})
    _write(f"{base}_bundle.json", json.dumps({"entries": entries}))
