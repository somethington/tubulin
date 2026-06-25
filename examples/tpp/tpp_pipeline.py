"""Headless Thermal Proteome Profiling pipeline, adapted from `tpp_solver`.

This reuses `tpp_solver`'s *actual* scientific code — filtering, seeded imputation,
sigmoidal melting-curve fitting (`scipy.curve_fit` on the package's `sigmoid`
model), Mann-Whitney U + Benjamini-Hochberg statistics. The only things bypassed
are the Streamlit-coupled orchestrators (`fit_and_plot_replicates`,
`perform_protein_statistical_tests`), which welded computation to `st.*` calls and
returned nothing. We drive the same underlying functions and *return* the tables.

Two fitting modes make the comparison honest:
  * ``mode="streamlit"`` calls tpp_solver's real `process_protein_replicates`, which
    builds a matplotlib Figure per protein, and **retains** them in a dict — exactly
    what `fit_and_plot_replicates` does (the incumbent's in-session memory pattern).
  * ``mode="kinesin"`` runs the identical fitting math without plotting and keeps
    only the result tables. Same numbers, flat memory.
"""

from __future__ import annotations

import sys
import time
import types

# --- Make tpp_solver's compute modules importable without a Streamlit runtime ---
if "streamlit" not in sys.modules:
    _stub = types.ModuleType("streamlit")

    class _Noop:
        def __getattr__(self, _name):
            return _Noop()

        def __call__(self, *_a, **_k):
            return _Noop()

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    _stub.__getattr__ = lambda _name: _Noop()  # type: ignore[attr-defined]
    sys.modules["streamlit"] = _stub

import io

import numpy as np
import pandas as pd
from scipy.optimize import curve_fit
from scipy.stats import boxcox, gaussian_kde, mannwhitneyu, shapiro, yeojohnson

# tpp_solver.fitting sets matplotlib's Agg backend on import; safe in a worker.
from tpp_solver.fitting import process_protein_replicates
import matplotlib.pyplot as plt

from tpp_solver.models import sigmoid
from tpp_solver.preprocessing import (
    _slice_replicate_data,
    calculate_bin_width,
    filter_and_lowest_float,
    get_replicate_data,
    impute_filtered_data,
    process_summary_tables,
)
from tpp_solver.statistics import benjamini_hochberg

DEFAULTS = {"seed": 42, "normalize": True, "r2_threshold": 0.8}

SUMMARY_COLS = ["protein", "treatment", "replicate", "melting_point", "R²", "residuals"]


# --------------------------------------------------------------------------- IO
def load_inputs(tsv_path, meta_path):
    """Read the FragPipe intensity TSV and the metadata CSV (BOM-safe via pandas)."""
    tsv = pd.read_csv(tsv_path, sep="\t")
    raw_meta = pd.read_csv(meta_path)
    meta = raw_meta[["Temperature", "Treatment", "Samples"]].dropna().reset_index(drop=True)
    samples = meta["Samples"].tolist()
    return tsv, meta, samples


def reference_temp(meta):
    """Reference (normalization) temperature: the lowest, where protein is folded."""
    return float(min(meta["Temperature"].astype(float).unique()))


def preprocess_only(tsv_path, meta_path, normalize=True, selected_temp=None,
                    r2_threshold=DEFAULTS["r2_threshold"], seed=DEFAULTS["seed"]):
    """Just enough to *plot* curves: load, impute, reshape — but **no stats-fit**.

    The expensive `fit_all` in `run()` exists only for the statistics table, which a
    plotting-only worker doesn't need (the plotting re-fits each curve itself). Used
    by non-primary shards so they skip the redundant full fit. Returns
    ``(replicate_data, params)``.
    """
    tsv, meta, samples = load_inputs(tsv_path, meta_path)
    if normalize:
        ref_temp = reference_temp(meta) if selected_temp is None else float(selected_temp)
    else:
        ref_temp = None
    replicate_data = preprocess(tsv, meta, samples, seed)
    params = {"ref_temp": ref_temp, "normalize": normalize, "r2_threshold": r2_threshold}
    return replicate_data, params


def preprocess(tsv, meta, samples, seed):
    filtered, lowest = filter_and_lowest_float(tsv, samples)
    imputed = impute_filtered_data(filtered.copy(), samples, lowest, seed=seed)
    return get_replicate_data(meta, imputed)


# ---------------------------------------------------------------------- fitting
def _fit_protein_lean(protein, data_dict, selected_temp, normalize, r2_threshold):
    """Plot-free port of `process_protein_replicates`' fitting math.

    Same p0, same normalization, same R² and melting point (popt[1]) — verified
    equal to the original in benchmark.py. Drops only the matplotlib side-effect.
    """
    out = []
    for treatment, proteins in data_dict.items():
        if protein not in proteins:
            continue
        protein_data = proteins[protein]
        first_temp = list(protein_data.keys())[0]
        num_replicates = len(protein_data[first_temp])

        for r in range(num_replicates):
            temps, vals = [], []
            for t in sorted(protein_data.keys()):
                if len(protein_data[t]) > r:
                    temps.append(t)
                    vals.append(protein_data[t][r])
            if len(temps) < 4:
                continue
            temps = np.array(temps)
            vals = np.array(vals)

            if normalize and selected_temp in temps:
                idx = np.where(temps == selected_temp)[0][0]
                if vals[idx] != 0:
                    vals = vals / vals[idx]

            try:
                p0 = [max(vals), np.median(temps), min(vals)]
                popt, _ = curve_fit(sigmoid, temps, vals, p0=p0)
                fitted = sigmoid(temps, *popt)
                ss_res = np.sum((vals - fitted) ** 2)
                ss_tot = np.sum((vals - np.mean(vals)) ** 2)
                r2 = 1 - (ss_res / ss_tot)
                if r2 >= r2_threshold:
                    out.append(
                        {
                            "protein": protein,
                            "treatment": treatment,
                            "replicate": r,
                            "melting_point": popt[1],
                            "R²": r2,
                            "residuals": ",".join(map(str, (vals - fitted))),
                        }
                    )
            except RuntimeError:
                continue
    return out


def fit_all(replicate_data, selected_temp, normalize, r2_threshold, mode, progress_cb=None):
    """Fit every protein. Returns (summary_table, figures_held)."""
    proteins = sorted({p for treatment in replicate_data.values() for p in treatment})
    total = len(proteins)
    rows = []
    figures = {}  # only populated in "streamlit" mode — the memory hog

    for i, protein in enumerate(proteins):
        if mode == "streamlit":
            args = (
                protein,
                _slice_replicate_data(replicate_data, protein),
                ["o", "s", "^", "v"],
                [50, 75, 100],
                [1.0, 0.8, 0.6],
                selected_temp,
                normalize,
                r2_threshold,
            )
            prot, fig, summary = process_protein_replicates(args)
            if prot is not None and summary:
                figures[prot] = fig  # retained, exactly like fit_and_plot_replicates
                rows.extend(summary)
        else:
            rows.extend(
                _fit_protein_lean(
                    protein,
                    _slice_replicate_data(replicate_data, protein),
                    selected_temp,
                    normalize,
                    r2_threshold,
                )
            )
        if progress_cb and (i % 50 == 0 or i == total - 1):
            progress_cb(i + 1, total)

    summary_table = pd.DataFrame(rows, columns=SUMMARY_COLS)
    if not summary_table.empty:
        summary_table["protein"] = summary_table["protein"].astype(str)
        summary_table["treatment"] = summary_table["treatment"].astype(str)
        summary_table["melting_point"] = summary_table["melting_point"].astype(float)
    return summary_table, figures


# ------------------------------------------------------------------- statistics
def run_stats(replicate_table, treatment_1, treatment_2):
    """Headless port of `perform_protein_statistical_tests` that *returns* the df.

    Same Mann-Whitney U, same effect size (r = Z/sqrt(N)), same BH FDR.
    """
    results, raw_p = [], []
    for protein in replicate_table["protein"].unique():
        pdata = replicate_table[replicate_table["protein"] == protein]
        control = pdata[pdata["treatment"] == treatment_1]["melting_point"]
        treated = pdata[pdata["treatment"] == treatment_2]["melting_point"]
        if len(control) < 2 or len(treated) < 2:
            continue
        try:
            stat, p_value = mannwhitneyu(treated, control, alternative="two-sided")
            n1, n2 = len(treated), len(control)
            z = (stat - n1 * n2 / 2) / np.sqrt(n1 * n2 * (n1 + n2 + 1) / 12)
            effect = abs(z) / np.sqrt(n1 + n2)
            median_diff = treated.median() - control.median()
            results.append(
                {
                    "Protein": protein,
                    "Control Tm": f"{control.median():.2f} ± {control.std():.2f}",
                    "Treatment Tm": f"{treated.median():.2f} ± {treated.std():.2f}",
                    "ΔTm": float(median_diff),
                    "P-value": float(p_value),
                    "Effect Size": float(effect),
                    "n_control": int(n2),
                    "n_treatment": int(n1),
                }
            )
            raw_p.append(p_value)
        except Exception:
            continue

    df = pd.DataFrame(results)
    if not df.empty:
        df["FDR"] = benjamini_hochberg(raw_p)
        df["Significant"] = df["FDR"] < 0.05
        df["Significance"] = df["FDR"].apply(
            lambda p: "***" if p < 0.001 else "**" if p < 0.01 else "*" if p < 0.05 else "ns"
        )
        df = df.sort_values(["Significant", "P-value"], ascending=[False, True]).reset_index(
            drop=True
        )
    return df


# --------------------------------------------------------------------- pipeline
def run(
    tsv_path,
    meta_path,
    mode="kinesin",
    seed=DEFAULTS["seed"],
    normalize=DEFAULTS["normalize"],
    r2_threshold=DEFAULTS["r2_threshold"],
    selected_temp=None,
    progress_cb=None,
    log_cb=None,
):
    """Run the full TPP pipeline. Returns tables + a metrics dict.

    ``selected_temp`` overrides the normalization reference temperature; when None
    (and normalizing) the lowest temperature is used.
    """

    def log(msg):
        if log_cb:
            log_cb(msg)

    t0 = time.time()
    tsv, meta, samples = load_inputs(tsv_path, meta_path)
    log(f"Loaded {tsv.shape[0]} proteins x {len(samples)} sample columns")

    if normalize:
        ref_temp = reference_temp(meta) if selected_temp is None else float(selected_temp)
    else:
        ref_temp = None
    replicate_data = preprocess(tsv, meta, samples, seed)

    treatments = list(dict.fromkeys(meta["Treatment"].tolist()))
    control = "control" if "control" in treatments else treatments[0]
    treated = next(t for t in treatments if t != control)
    t1 = time.time()

    log(f"Fitting melting curves ({mode} mode), normalize@{ref_temp}°C, R^2>={r2_threshold}")
    summary_table, figures = fit_all(
        replicate_data, ref_temp, normalize, r2_threshold, mode, progress_cb
    )
    t2 = time.time()

    replicate_table, averaged_table = process_summary_tables(summary_table)
    stats_df = run_stats(replicate_table, control, treated)
    t3 = time.time()

    metrics = {
        "mode": mode,
        "proteins_in": int(tsv.shape[0]),
        "samples": len(samples),
        "curves_fit": int(len(summary_table)),
        "proteins_fit": int(summary_table["protein"].nunique()) if not summary_table.empty else 0,
        "proteins_tested": int(len(stats_df)),
        "significant": int(stats_df["Significant"].sum()) if not stats_df.empty else 0,
        "figures_held": len(figures),
        "control": control,
        "treatment": treated,
        "t_load_s": round(t1 - t0, 3),
        "t_fit_s": round(t2 - t1, 3),
        "t_stats_s": round(t3 - t2, 3),
        "t_total_s": round(t3 - t0, 3),
    }
    log(f"Fit {metrics['proteins_fit']} proteins; tested {metrics['proteins_tested']}; "
        f"{metrics['significant']} significant (FDR<0.05)")

    return {
        "summary_table": summary_table,
        "replicate_table": replicate_table,
        "averaged_table": averaged_table,
        "stats_df": stats_df,
        "figures": figures,
        "replicate_data": replicate_data,
        "params": {
            "ref_temp": ref_temp,
            "normalize": normalize,
            "r2_threshold": r2_threshold,
            "control": control,
            "treatment": treated,
        },
        "metrics": metrics,
    }


# ------------------------------------------------------------------------ plots
def _fig_to_svg(fig):
    buf = io.StringIO()
    fig.savefig(buf, format="svg", bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def plot_volcano(stats_df):
    """ΔTm vs -log10(FDR), significant points highlighted."""
    fig, ax = plt.subplots(figsize=(7, 5))
    if not stats_df.empty:
        x = stats_df["ΔTm"].astype(float)
        fdr = stats_df["FDR"].astype(float).clip(lower=1e-300)
        y = -np.log10(fdr)
        sig = stats_df["Significant"].astype(bool)
        ax.scatter(x[~sig], y[~sig], c="#9aa", s=22, alpha=0.6, label="ns")
        ax.scatter(x[sig], y[sig], c="#e4572e", s=28, alpha=0.85, label="significant")
        ax.axhline(-np.log10(0.05), ls="--", c="#e4572e", alpha=0.5)
    ax.set_xlabel("ΔTm (°C)")
    ax.set_ylabel("-log10(FDR)")
    ax.set_title("Volcano plot of melting-point changes")
    ax.legend(frameon=False)
    return _fig_to_svg(fig)


def plot_violin(averaged_table, treatments):
    """Melting-point distribution per treatment, with jittered points."""
    fig, ax = plt.subplots(figsize=(7, 5))
    data = [
        averaged_table[averaged_table["treatment"] == t]["melting_point"].dropna().values
        for t in treatments
    ]
    data = [d for d in data if len(d) > 0]
    if data:
        ax.violinplot(data, showmeans=True, showmedians=True)
        rng = np.random.default_rng(0)
        for i, d in enumerate(data):
            ax.scatter(rng.normal(i + 1, 0.04, size=len(d)), d, s=8, c="black", alpha=0.45)
    ax.set_xticks(range(1, len(treatments) + 1))
    ax.set_xticklabels(treatments)
    ax.set_ylabel("Melting point (°C)")
    ax.set_title("Melting points by treatment")
    return _fig_to_svg(fig)


def plot_distribution(summary_table):
    """Histogram + KDE of all fitted melting points (Freedman-Diaconis bins)."""
    data = summary_table["melting_point"].dropna()
    data = data[(data >= 0) & (data <= 100)]
    fig, ax = plt.subplots(figsize=(7, 5))
    if not data.empty:
        bins = calculate_bin_width(data)
        ax.hist(data, bins=bins, color="#9ec5fe", edgecolor="black", alpha=0.7, label="Histogram")
        kde = gaussian_kde(data)
        xs = np.linspace(data.min(), data.max(), 200)
        bw = (data.max() - data.min()) / bins
        ax.plot(xs, kde(xs) * len(data) * bw, c="#1d3f8a", lw=2, label="Density")
        ax.legend(frameon=False)
    ax.set_xlabel("Melting point (°C)")
    ax.set_ylabel("Frequency")
    ax.set_title("Distribution of melting points")
    return _fig_to_svg(fig)


def plot_example_curve(replicate_data, stats_df, params):
    """The fitted melting curves of the protein with the largest |ΔTm|.

    Reuses tpp_solver's own `process_protein_replicates`, which returns the figure.
    """
    if stats_df.empty:
        return None
    idx = stats_df["ΔTm"].astype(float).abs().idxmax()
    protein = stats_df.loc[idx, "Protein"]
    args = (
        protein,
        _slice_replicate_data(replicate_data, protein),
        ["o", "s", "^", "v"],
        [50, 75, 100],
        [1.0, 0.8, 0.6],
        params["ref_temp"],
        params["normalize"],
        params["r2_threshold"],
    )
    _, fig, _ = process_protein_replicates(args)
    if fig is None:
        return None
    return _fig_to_svg(fig)


# ------------------------------------------------------- interactive plotly plots
# These mirror tpp_solver's own Plotly figures (statistics.py volcano, plotting.py
# violin + distribution), returning Plotly JSON for the browser to render natively.
import plotly.express as _px  # noqa: E402
import plotly.graph_objects as _go  # noqa: E402


def plotly_volcano(stats_df):
    fig = _go.Figure()
    if not stats_df.empty:
        x = [float(v) for v in stats_df["ΔTm"]]
        y = (-np.log10(stats_df["FDR"].astype(float).clip(lower=1e-300))).tolist()
        colors = stats_df["Significant"].map({True: "red", False: "gray"}).tolist()
        fig.add_trace(
            _go.Scatter(
                x=x,
                y=y,
                mode="markers",
                marker=dict(color=colors, size=8, opacity=0.7),
                text=stats_df["Protein"].tolist(),
                hovertemplate="<b>%{text}</b><br>ΔTm: %{x:.2f}°C<br>-log10(FDR): %{y:.2f}<extra></extra>",
            )
        )
        fig.add_hline(y=-np.log10(0.05), line_dash="dash", line_color="red", opacity=0.5)
    fig.update_layout(
        title="Volcano Plot of Melting Point Changes",
        xaxis_title="ΔTm (°C)",
        yaxis_title="-log10(FDR)",
        template="plotly_white",
        showlegend=False,
    )
    return fig.to_json()


def plotly_violin(averaged_table, treatments):
    data = averaged_table[["melting_point", "treatment"]].dropna()
    data = data[(data["melting_point"] >= 0) & (data["melting_point"] <= 100)]
    colors = _px.colors.qualitative.Plotly
    fig = _go.Figure()
    rng = np.random.default_rng(0)
    for i, t in enumerate(treatments):
        td = data[data["treatment"] == t]["melting_point"]
        fig.add_trace(
            _go.Violin(
                y=td,
                x=[i] * len(td),
                name=f"{t} Violin",
                box_visible=True,
                meanline_visible=True,
                opacity=0.6,
                width=0.6,
                points=False,
                line_color="black",
                fillcolor=colors[i % len(colors)],
                showlegend=True,
            )
        )
        fig.add_trace(
            _go.Scatter(
                y=td,
                x=i + rng.uniform(-0.05, 0.05, size=len(td)),
                mode="markers",
                name=f"{t} Points",
                marker=dict(color="black", size=6, opacity=0.8),
                showlegend=True,
            )
        )
    fig.update_layout(
        title="Melting Point Comparison Across Treatments",
        xaxis_title="Treatment",
        yaxis_title="Melting Point (°C)",
        template="plotly_white",
        violinmode="overlay",
        xaxis=dict(tickmode="array", tickvals=list(range(len(treatments))), ticktext=treatments),
    )
    return fig.to_json()


def plotly_distribution(summary_table):
    data = summary_table["melting_point"].dropna()
    data = data[(data >= 0) & (data <= 100)]
    fig = _go.Figure()
    if not data.empty:
        bins = calculate_bin_width(data)
        fig.add_trace(
            _go.Histogram(
                x=data,
                nbinsx=bins,
                marker=dict(color="lightblue", line=dict(color="black", width=1)),
                opacity=0.7,
                name="Histogram",
            )
        )
        kde = gaussian_kde(data)
        xs = np.linspace(data.min(), data.max(), 200)
        bw = (data.max() - data.min()) / bins
        fig.add_trace(
            _go.Scatter(
                x=xs,
                y=kde(xs) * len(data) * bw,
                mode="lines",
                line=dict(color="darkblue", width=2),
                name="Density Curve",
            )
        )
    fig.update_layout(
        title="Overall Distribution of Melting Points",
        xaxis_title="Melting Point (°C)",
        yaxis_title="Frequency",
        template="plotly_white",
        bargap=0.1,
    )
    return fig.to_json()


def _safe_name(s):
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in str(s))


def _plot_averaged_curve(protein, data_dict, ref, normalize, r2_threshold):
    """One **averaged** melting curve per treatment for a protein (mean intensity at
    each temperature, then a single sigmoid fit). Plot-and-marker logic ported from
    tpp_solver's ``fit_and_plot_averaged_curves``. Returns a figure or None.
    """
    fig, ax = plt.subplots(figsize=(10, 6))
    fits = []
    for treatment, proteins_dict in data_dict.items():
        if protein not in proteins_dict:
            continue
        temp_dict = proteins_dict[protein]
        temps = sorted(temp_dict.keys())
        if len(temps) < 4:
            continue
        temperatures = np.array(temps)
        values = np.array([np.mean(np.array(temp_dict[t])) for t in temps])
        mask = ~np.isnan(values)
        temperatures, values = temperatures[mask], values[mask]
        if len(temperatures) < 4:
            continue
        if normalize and ref in temperatures:
            idx = np.where(temperatures == ref)[0][0]
            if values[idx] != 0:
                values = values / values[idx]
        try:
            popt, _ = curve_fit(
                sigmoid, temperatures, values,
                p0=[np.max(values), np.median(temperatures), np.min(values)],
            )
            fitted = sigmoid(temperatures, *popt)
            ss_res = np.sum((values - fitted) ** 2)
            ss_tot = np.sum((values - np.mean(values)) ** 2)
            r2 = 1 - (ss_res / ss_tot)
            if r2 < r2_threshold:
                continue
            fits.append({"treatment": treatment, "temperatures": temperatures,
                         "values": values, "popt": popt, "r_squared": r2})
        except RuntimeError:
            continue

    if not fits:
        plt.close(fig)
        return None

    fits.sort(key=lambda f: f["popt"][1])
    for idx, fit in enumerate(fits):
        ax.scatter(fit["temperatures"], fit["values"], marker="o", s=50, alpha=1.0,
                   label=f"{fit['treatment']} averaged points")
        tr = np.linspace(fit["temperatures"].min(), fit["temperatures"].max(), 100)
        ax.plot(tr, sigmoid(tr, *fit["popt"]), "--", alpha=0.8,
                label=f"{fit['treatment']} fit (R²={fit['r_squared']:.2f})")
        mt = fit["popt"][1]
        mv = sigmoid(mt, *fit["popt"])
        off = 0.3 * (idx - (len(fits) - 1) / 2)
        ax.scatter(mt + off, mv, color="red", s=75, marker="^", zorder=5)
        ax.text(mt + off, mv, f"{mt:.2f}°C", color="red", ha="center", va="bottom",
                fontsize=10, zorder=6)

    ax.set_xlabel("Temperature (°C)")
    ax.set_ylabel("Normalized Intensity" if normalize else "Intensity")
    ax.set_title(f"Averaged melting curves for {protein}")
    ax.legend(bbox_to_anchor=(1.05, 1), loc="upper left")
    return fig


def _plot_replicate_curve(protein, data_dict, params):
    """Per-replicate curves (one sigmoid per replicate, overlaid). Reuses
    tpp_solver's own ``process_protein_replicates``; returns a figure or None."""
    args = (
        protein,
        data_dict,
        ["o", "s", "^", "v"],
        [50, 75, 100],
        [1.0, 0.8, 0.6],
        params["ref_temp"],
        params["normalize"],
        params["r2_threshold"],
    )
    prot, fig, _ = process_protein_replicates(args)
    if prot is None and fig is not None:
        plt.close(fig)
        return None
    return fig


def generate_curves(
    replicate_data, params, out_dir, kind="averaged", progress_cb=None, limit=0,
    shard=0, num_shards=1,
):
    """Plot fitted proteins' melting curves to ``out_dir`` as SVG.

    ``kind="averaged"`` → one sigmoid per treatment (mean of replicates);
    ``kind="replicate"`` → one sigmoid per replicate, overlaid.

    When ``num_shards > 1`` this worker plots only its slice
    ``proteins[shard::num_shards]`` — the runtime runs the other shards in parallel.

    Lean by design: fit, plot, save, and **close** one figure at a time, so peak
    memory is a single figure regardless of how many proteins there are. Returns
    ``[{"protein", "path"}]``.
    """
    import os

    os.makedirs(out_dir, exist_ok=True)
    proteins = sorted({p for treatment in replicate_data.values() for p in treatment})
    if limit and limit > 0:
        proteins = proteins[:limit]
    if num_shards > 1:
        proteins = proteins[shard::num_shards]

    items = []
    total = len(proteins)
    for i, protein in enumerate(proteins):
        sliced = _slice_replicate_data(replicate_data, protein)
        if kind == "replicate":
            fig = _plot_replicate_curve(protein, sliced, params)
        else:
            fig = _plot_averaged_curve(
                protein, sliced, params["ref_temp"], params["normalize"], params["r2_threshold"]
            )
        if fig is not None:
            path = os.path.join(out_dir, f"{_safe_name(protein)}.svg")
            fig.savefig(path, format="svg", bbox_inches="tight")
            plt.close(fig)
            items.append({"protein": str(protein), "path": path})
        if progress_cb and (i % 20 == 0 or i == total - 1):
            progress_cb(i + 1, total)
    return items


def averaged_summary(replicate_data, params):
    """Averaged-curve fit: one sigmoid to the *mean* replicate intensity at each
    temperature (vs the per-replicate fits that drive the statistics).

    Plot-free port of tpp_solver's ``fit_and_plot_averaged_curves`` — identical
    averaging, p0, normalization and R² math, but it doesn't build a figure per
    protein, so peak memory stays flat. Returns the averaged summary table.
    """
    ref = params["ref_temp"]
    normalize = params["normalize"]
    r2_threshold = params["r2_threshold"]
    proteins = sorted({p for treatment in replicate_data.values() for p in treatment})

    rows = []
    for protein in proteins:
        for treatment, proteins_dict in replicate_data.items():
            if protein not in proteins_dict:
                continue
            temp_dict = proteins_dict[protein]
            temps = sorted(temp_dict.keys())
            if len(temps) < 4:
                continue
            temperatures = np.array(temps)
            values = np.array([np.mean(np.array(temp_dict[t])) for t in temps])
            mask = ~np.isnan(values)
            temperatures, values = temperatures[mask], values[mask]
            if len(temperatures) < 4:
                continue
            if normalize and ref in temperatures:
                idx = np.where(temperatures == ref)[0][0]
                if values[idx] != 0:
                    values = values / values[idx]
            try:
                p0 = [np.max(values), np.median(temperatures), np.min(values)]
                popt, _ = curve_fit(sigmoid, temperatures, values, p0=p0)
                fitted = sigmoid(temperatures, *popt)
                ss_res = np.sum((values - fitted) ** 2)
                ss_tot = np.sum((values - np.mean(values)) ** 2)
                r2 = 1 - (ss_res / ss_tot)
                if r2 < r2_threshold:
                    continue
                rows.append(
                    {
                        "protein": str(protein),
                        "treatment": str(treatment),
                        "melting_point": float(popt[1]),
                        "R²": float(r2),
                        "residuals": ",".join(map(str, values - fitted)),
                    }
                )
            except RuntimeError:
                continue
    return pd.DataFrame(rows, columns=["protein", "treatment", "melting_point", "R²", "residuals"])


def normality_table(averaged_table):
    """Shapiro-Wilk normality of ΔTm under several transformations."""
    pivot = averaged_table.pivot(index="protein", columns="treatment", values="melting_point")
    if pivot.shape[1] < 2:
        return pd.DataFrame()
    delta = (pivot.iloc[:, 0] - pivot.iloc[:, 1]).dropna()
    if len(delta) < 3:
        return pd.DataFrame()

    transforms = {"Original": delta}
    transforms["Log(ΔTm + shift)"] = np.log(delta - delta.min() + 1)
    transforms["Square Root"] = np.sqrt(delta - delta.min())
    transforms["Box-Cox"] = pd.Series(boxcox(delta - delta.min() + 1)[0], index=delta.index)
    transforms["Yeo-Johnson"] = pd.Series(yeojohnson(delta)[0], index=delta.index)

    rows = []
    for name, vals in transforms.items():
        v = pd.Series(vals).replace([np.inf, -np.inf], np.nan).dropna()
        if len(v) < 3:
            continue
        stat, p = shapiro(v)
        rows.append(
            {
                "Transformation": name,
                "W statistic": round(float(stat), 4),
                "p-value": round(float(p), 4),
                "Normal (p>0.05)": bool(p > 0.05),
            }
        )
    return pd.DataFrame(rows)
