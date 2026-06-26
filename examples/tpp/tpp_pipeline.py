"""Headless Thermal Proteome Profiling pipeline — **polars + numpy**, pandas-free.

Data wrangling (read / filter / impute / reshape / group / stats assembly) is done
in polars; the numerics (sigmoid curve fitting, Mann-Whitney, Benjamini-Hochberg,
the seeded imputation draws) are numpy/scipy and are byte-for-byte the same as the
original pandas/tpp_solver version (verified against saved ground-truth outputs).

To stay pandas-free this module **vendors** the few small pure functions it needs
from tpp_solver (sigmoid, the per-protein replicate plotter, the data slicer, the
Freedman-Diaconis bin width, Benjamini-Hochberg) rather than importing tpp_solver
(which would pull in pandas + streamlit + duckdb).
"""

from __future__ import annotations

import io
import itertools

import matplotlib

matplotlib.use("Agg")
# Emit text as real <text> elements instead of converting every glyph to a vector
# <path>. ~halves SVG size and shaves serialization time; renders identically in any
# browser that has a sans-serif font (i.e. all of them).
matplotlib.rcParams["svg.fonttype"] = "none"
import matplotlib.pyplot as plt
import numpy as np
import polars as pl
from scipy.optimize import curve_fit
from scipy.stats import boxcox, gaussian_kde, mannwhitneyu, shapiro, yeojohnson

DEFAULTS = {"seed": 42, "normalize": True, "r2_threshold": 0.8}
SUMMARY_COLS = ["protein", "treatment", "replicate", "melting_point", "R²", "residuals"]


# =========================================================================
# Vendored pure-numpy helpers (from tpp_solver, verbatim behaviour)
# =========================================================================
def sigmoid(T, a, b, plateau):
    return a / (1 + np.exp(-(T - b))) + plateau


def _slice_replicate_data(replicate_data, protein):
    return {
        treatment: {protein: proteins[protein]}
        for treatment, proteins in replicate_data.items()
        if protein in proteins
    }


def calculate_bin_width(data):
    data = np.asarray(data)
    data = data[~np.isnan(data)]
    q25, q75 = np.percentile(data, [25, 75])
    iqr = q75 - q25
    bin_width = max(2 * iqr * len(data) ** (-1 / 3), 1e-3)
    bins = int(np.ceil((data.max() - data.min()) / bin_width))
    return max(bins, 10)


def benjamini_hochberg(p_values):
    p_values = np.asarray(p_values, dtype=float)
    n = len(p_values)
    if n == 0:
        return np.array([])
    order = np.argsort(p_values)
    fdr = p_values[order] * n / (np.arange(n) + 1)
    for i in range(n - 2, -1, -1):
        fdr[i] = min(fdr[i], fdr[i + 1])
    out = np.empty(n)
    out[order] = fdr
    return out


def process_protein_replicates(args):
    """Per-replicate melting-curve figure (one sigmoid per replicate, overlaid).
    Vendored from tpp_solver.fitting; numpy + matplotlib only. Returns
    (protein, fig, summary_rows) or (None, None, None)."""
    (protein, data_dict, marker_opts, size_opts, alpha_opts,
     selected_temp, normalize_data, r2_threshold) = args
    markers, sizes, alphas = (itertools.cycle(o) for o in (marker_opts, size_opts, alpha_opts))
    summary, fig, ax = [], None, None
    try:
        for treatment, proteins in data_dict.items():
            if protein not in proteins:
                continue
            protein_data = proteins[protein]
            first = list(protein_data.keys())[0]
            n_rep = len(protein_data[first])
            fits = []
            for r in range(n_rep):
                temps, vals = [], []
                for t in sorted(protein_data.keys()):
                    if len(protein_data[t]) > r:
                        temps.append(t)
                        vals.append(protein_data[t][r])
                if len(temps) < 4:
                    continue
                temps, vals = np.array(temps), np.array(vals)
                if normalize_data and selected_temp in temps:
                    nv = vals[np.where(temps == selected_temp)[0][0]]
                    if nv != 0:
                        vals = vals / nv
                try:
                    popt, _ = curve_fit(sigmoid, temps, vals, p0=[max(vals), np.median(temps), min(vals)])
                    fitted = sigmoid(temps, *popt)
                    r2 = 1 - np.sum((vals - fitted) ** 2) / np.sum((vals - np.mean(vals)) ** 2)
                    if r2 >= r2_threshold:
                        fits.append({"temperatures": temps, "values": vals, "popt": popt,
                                     "r_squared": r2, "replicate_num": r})
                        summary.append({"protein": protein, "treatment": treatment, "replicate": r,
                                        "melting_point": float(popt[1]), "R²": float(r2),
                                        "residuals": ",".join(map(str, vals - fitted))})
                except RuntimeError:
                    continue
            if fits:
                if fig is None:
                    fig, ax = plt.subplots(figsize=(10, 6))
                marker, size, alpha = next(markers), next(sizes), next(alphas)
                for i, fd in enumerate(fits):
                    ax.scatter(fd["temperatures"], fd["values"], marker=marker, s=size, alpha=alpha,
                               label=f"{protein} {treatment} Rep{fd['replicate_num']} points")
                    tr = np.linspace(min(fd["temperatures"]), max(fd["temperatures"]), 100)
                    ax.plot(tr, sigmoid(tr, *fd["popt"]), "--", alpha=0.7,
                            label=f"{protein} {treatment} Rep{fd['replicate_num']} fitted (R²={fd['r_squared']:.2f})")
                    mt = fd["popt"][1]
                    mv = sigmoid(mt, *fd["popt"])
                    off = (i - (len(fits) - 1) / 2.0) * 0.2
                    ax.scatter(mt + off, mv, color="red", s=75, marker="^")
                    ax.text(mt + off, mv, f"{mt:.2f}", color="red", ha="center", va="bottom")
        if fig is not None:
            ax.set_xlabel("Temperature")
            ax.set_ylabel("Intensity")
            ax.set_title(f"Fitted Curves for {protein} (All Replicates)")
            ax.legend(bbox_to_anchor=(1.05, 1), loc="upper left")
            # Reserve room for the outside legend (longer per-replicate labels) so the
            # framework can savefig without bbox_inches="tight".
            fig.subplots_adjust(right=0.62)
            return protein, fig, summary
    except Exception as e:  # pragma: no cover
        print(f"error processing {protein}: {e}")
    return None, None, None


# =========================================================================
# IO + preprocessing (polars + numpy)
# =========================================================================
def load_inputs(tsv_path, meta_path):
    tsv = pl.read_csv(tsv_path, separator="\t", infer_schema_length=20000)
    # Metadata header has a BOM and blank ",," columns; find the named columns then
    # read positionally (avoids polars' duplicate-empty-header error).
    with open(meta_path, encoding="utf-8-sig") as fh:
        header = fh.readline().rstrip("\n").split(",")
    idx = {n: header.index(n) for n in ("Temperature", "Treatment", "Samples")}
    raw = pl.read_csv(meta_path, has_header=False, skip_rows=1, infer_schema_length=0,
                      truncate_ragged_lines=True)
    meta = pl.DataFrame({
        "Temperature": raw[:, idx["Temperature"]].cast(pl.Float64, strict=False),
        "Treatment": raw[:, idx["Treatment"]],
        "Samples": raw[:, idx["Samples"]],
    }).drop_nulls()
    samples = meta["Samples"].to_list()
    return tsv, meta, samples


def reference_temp(meta):
    return float(meta["Temperature"].min())


def preprocess(tsv, meta, samples, seed):
    """Filter + seeded impute + reshape into {treatment: {protein: {temp: [vals]}}}.

    The imputation runs in numpy with the exact column/row order of the original, so
    the seeded draws — and therefore every downstream melting point — are identical.
    """
    missing = set(samples) - set(tsv.columns)
    if missing:
        raise ValueError("The TSV does not contain all 'Samples' from the metadata")

    # sample columns as a (rows x samples) float matrix, in `samples` order
    mat = np.column_stack([tsv[s].cast(pl.Float64, strict=False).to_numpy() for s in samples])
    nonzero = np.where(mat == 0, np.nan, mat)
    lowest = np.nanmin(nonzero)
    if np.isnan(lowest):
        raise ValueError("No valid number found")

    rng = np.random.default_rng(seed)
    imputed = mat.copy()
    for j in range(imputed.shape[1]):
        col = imputed[:, j]
        zmask = col == 0
        zc = int(zmask.sum())
        if zc > 0:
            col[zmask] = rng.uniform(0, lowest, size=zc)
        imputed[:, j] = col

    protein_ids = tsv["Protein ID"].to_list()
    sample_index = {s: i for i, s in enumerate(samples)}
    tt_map = {}  # treatment -> temp -> [column indices], in metadata order
    for t, temp, s in zip(meta["Treatment"].to_list(),
                          meta["Temperature"].to_list(),
                          meta["Samples"].to_list()):
        tt_map.setdefault(t, {}).setdefault(float(temp), []).append(sample_index[s])

    replicate_data = {}
    for ri, pid in enumerate(protein_ids):
        row = imputed[ri]
        for treatment, temp_map in tt_map.items():
            rd_p = replicate_data.setdefault(treatment, {}).setdefault(pid, {})
            for temp, idxs in temp_map.items():
                rd_p[temp] = [float(row[i]) for i in idxs]
    return replicate_data


def preprocess_only(tsv_path, meta_path, normalize=True, selected_temp=None,
                    r2_threshold=DEFAULTS["r2_threshold"], seed=DEFAULTS["seed"]):
    """Cheap context for plotting only (no stats-fit)."""
    tsv, meta, samples = load_inputs(tsv_path, meta_path)
    if normalize:
        ref = reference_temp(meta) if selected_temp is None else float(selected_temp)
    else:
        ref = None
    return preprocess(tsv, meta, samples, seed), {"ref_temp": ref, "normalize": normalize,
                                                  "r2_threshold": r2_threshold}


# =========================================================================
# Fitting (numpy on the nested dict)
# =========================================================================
def _fit_protein_lean(protein, data_dict, selected_temp, normalize, r2_threshold):
    out = []
    for treatment, proteins in data_dict.items():
        if protein not in proteins:
            continue
        protein_data = proteins[protein]
        n_rep = len(protein_data[list(protein_data.keys())[0]])
        for r in range(n_rep):
            temps, vals = [], []
            for t in sorted(protein_data.keys()):
                if len(protein_data[t]) > r:
                    temps.append(t)
                    vals.append(protein_data[t][r])
            if len(temps) < 4:
                continue
            temps, vals = np.array(temps), np.array(vals)
            if normalize and selected_temp in temps:
                nv = vals[np.where(temps == selected_temp)[0][0]]
                if nv != 0:
                    vals = vals / nv
            try:
                popt, _ = curve_fit(sigmoid, temps, vals, p0=[max(vals), np.median(temps), min(vals)])
                fitted = sigmoid(temps, *popt)
                r2 = 1 - np.sum((vals - fitted) ** 2) / np.sum((vals - np.mean(vals)) ** 2)
                if r2 >= r2_threshold:
                    out.append({"protein": protein, "treatment": treatment, "replicate": r,
                                "melting_point": float(popt[1]), "R²": float(r2),
                                "residuals": ",".join(map(str, vals - fitted))})
            except RuntimeError:
                continue
    return out


def fit_all(replicate_data, selected_temp, normalize, r2_threshold, mode, progress_cb=None):
    proteins = sorted({p for t in replicate_data.values() for p in t})
    rows, figures = [], {}
    for i, protein in enumerate(proteins):
        if mode == "streamlit":
            args = (protein, _slice_replicate_data(replicate_data, protein),
                    ["o", "s", "^", "v"], [50, 75, 100], [1.0, 0.8, 0.6],
                    selected_temp, normalize, r2_threshold)
            prot, fig, summary = process_protein_replicates(args)
            if prot is not None and summary:
                figures[prot] = fig
                rows.extend(summary)
        else:
            rows.extend(_fit_protein_lean(protein, _slice_replicate_data(replicate_data, protein),
                                          selected_temp, normalize, r2_threshold))
        if progress_cb and (i % 50 == 0 or i == len(proteins) - 1):
            progress_cb(i + 1, len(proteins))
    schema = {"protein": pl.Utf8, "treatment": pl.Utf8, "replicate": pl.Int64,
              "melting_point": pl.Float64, "R²": pl.Float64, "residuals": pl.Utf8}
    summary_table = pl.DataFrame(rows, schema=schema) if rows else pl.DataFrame(schema=schema)
    return summary_table, figures


def process_summary_tables(summary_table):
    replicate_table = summary_table.select(["protein", "treatment", "melting_point", "R²", "residuals"])
    averaged = (
        summary_table.group_by(["protein", "treatment"])
        .agg([
            pl.col("melting_point").mean().alias("melting_point"),
            pl.col("R²").mean().alias("R²"),
            pl.col("residuals").first().alias("residuals"),
            pl.col("melting_point").std().alias("melting_point_std"),
            pl.len().alias("num_replicates"),
        ])
        .with_columns(pl.col("melting_point_std").fill_null(0.0))
        .sort(["protein", "treatment"])
        .select(["protein", "treatment", "melting_point", "melting_point_std",
                 "num_replicates", "R²", "residuals"])
    )
    return replicate_table, averaged


# =========================================================================
# Statistics (polars + scipy)
# =========================================================================
def run_stats(replicate_table, treatment_1, treatment_2):
    results, raw_p = [], []
    for protein in replicate_table["protein"].unique(maintain_order=True).to_list():
        sub = replicate_table.filter(pl.col("protein") == protein)
        control = sub.filter(pl.col("treatment") == treatment_1)["melting_point"].to_numpy()
        treated = sub.filter(pl.col("treatment") == treatment_2)["melting_point"].to_numpy()
        if len(control) < 2 or len(treated) < 2:
            continue
        stat, p = mannwhitneyu(treated, control, alternative="two-sided")
        n1, n2 = len(treated), len(control)
        z = (stat - n1 * n2 / 2) / np.sqrt(n1 * n2 * (n1 + n2 + 1) / 12)
        results.append({
            "Protein": protein,
            "Control Tm": f"{np.median(control):.2f} ± {np.std(control, ddof=1):.2f}",
            "Treatment Tm": f"{np.median(treated):.2f} ± {np.std(treated, ddof=1):.2f}",
            "ΔTm": float(np.median(treated) - np.median(control)),
            "P-value": float(p),
            "Effect Size": float(abs(z) / np.sqrt(n1 + n2)),
            "n_control": int(n2),
            "n_treatment": int(n1),
        })
        raw_p.append(p)

    if not results:
        return pl.DataFrame()
    df = pl.DataFrame(results)
    fdr = benjamini_hochberg(raw_p)
    df = df.with_columns([
        pl.Series("FDR", fdr),
        pl.Series("Significant", fdr < 0.05),
        pl.Series("Significance", ["***" if p < 0.001 else "**" if p < 0.01 else "*" if p < 0.05 else "ns"
                                   for p in fdr]),
    ]).sort(["Significant", "P-value"], descending=[True, False])
    return df


def averaged_summary(replicate_data, params):
    """Averaged-curve fit (one sigmoid to the mean intensity per temperature)."""
    ref, normalize, r2_threshold = params["ref_temp"], params["normalize"], params["r2_threshold"]
    rows = []
    for protein in sorted({p for t in replicate_data.values() for p in t}):
        for treatment, proteins in replicate_data.items():
            if protein not in proteins:
                continue
            temp_dict = proteins[protein]
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
                nv = values[np.where(temperatures == ref)[0][0]]
                if nv != 0:
                    values = values / nv
            try:
                popt, _ = curve_fit(sigmoid, temperatures, values,
                                    p0=[np.max(values), np.median(temperatures), np.min(values)])
                fitted = sigmoid(temperatures, *popt)
                r2 = 1 - np.sum((values - fitted) ** 2) / np.sum((values - np.mean(values)) ** 2)
                if r2 < r2_threshold:
                    continue
                rows.append({"protein": str(protein), "treatment": str(treatment),
                             "melting_point": float(popt[1]), "R²": float(r2),
                             "residuals": ",".join(map(str, values - fitted))})
            except RuntimeError:
                continue
    schema = {"protein": pl.Utf8, "treatment": pl.Utf8, "melting_point": pl.Float64,
              "R²": pl.Float64, "residuals": pl.Utf8}
    return pl.DataFrame(rows, schema=schema) if rows else pl.DataFrame(schema=schema)


def normality_table(averaged_table):
    pivot = averaged_table.pivot(values="melting_point", index="protein", on="treatment")
    cols = sorted(c for c in pivot.columns if c != "protein")  # alphabetical, matches pandas
    if len(cols) < 2:
        return pl.DataFrame()
    delta = pivot[cols[0]].to_numpy() - pivot[cols[1]].to_numpy()
    delta = delta[~np.isnan(delta)]
    if len(delta) < 3:
        return pl.DataFrame()

    transforms = {
        "Original": delta,
        "Log(ΔTm + shift)": np.log(delta - delta.min() + 1),
        "Square Root": np.sqrt(delta - delta.min()),
        "Box-Cox": boxcox(delta - delta.min() + 1)[0],
        "Yeo-Johnson": yeojohnson(delta)[0],
    }
    rows = []
    for name, vals in transforms.items():
        v = np.asarray(vals)
        v = v[np.isfinite(v)]
        if len(v) < 3:
            continue
        stat, p = shapiro(v)
        rows.append({"Transformation": name, "W statistic": round(float(stat), 4),
                     "p-value": round(float(p), 4), "Normal (p>0.05)": bool(p > 0.05)})
    return pl.DataFrame(rows)


# =========================================================================
# Pipeline
# =========================================================================
def run(tsv_path, meta_path, mode="kinesin", seed=DEFAULTS["seed"],
        normalize=DEFAULTS["normalize"], r2_threshold=DEFAULTS["r2_threshold"],
        selected_temp=None, progress_cb=None, log_cb=None):
    def log(m):
        if log_cb:
            log_cb(m)

    import time

    t0 = time.time()
    tsv, meta, samples = load_inputs(tsv_path, meta_path)
    log(f"Loaded {tsv.height} proteins x {len(samples)} sample columns")
    ref = (reference_temp(meta) if selected_temp is None else float(selected_temp)) if normalize else None
    replicate_data = preprocess(tsv, meta, samples, seed)

    treatments = list(dict.fromkeys(meta["Treatment"].to_list()))
    control = "control" if "control" in treatments else treatments[0]
    treated = next(t for t in treatments if t != control)
    t1 = time.time()

    summary_table, figures = fit_all(replicate_data, ref, normalize, r2_threshold, mode, progress_cb)
    t2 = time.time()
    replicate_table, averaged_table = process_summary_tables(summary_table)
    stats_df = run_stats(replicate_table, control, treated)
    t3 = time.time()

    metrics = {
        "mode": mode,
        "proteins_in": int(tsv.height),
        "samples": len(samples),
        "curves_fit": int(summary_table.height),
        "proteins_fit": int(summary_table["protein"].n_unique()) if summary_table.height else 0,
        "proteins_tested": int(stats_df.height),
        "significant": int(stats_df["Significant"].sum()) if stats_df.height else 0,
        "figures_held": len(figures),
        "control": control, "treatment": treated,
        "t_load_s": round(t1 - t0, 3), "t_fit_s": round(t2 - t1, 3),
        "t_stats_s": round(t3 - t2, 3), "t_total_s": round(t3 - t0, 3),
    }
    log(f"Fit {metrics['proteins_fit']} proteins; tested {metrics['proteins_tested']}; "
        f"{metrics['significant']} significant (FDR<0.05)")
    return {"summary_table": summary_table, "replicate_table": replicate_table,
            "averaged_table": averaged_table, "stats_df": stats_df, "figures": figures,
            "replicate_data": replicate_data, "params": {"ref_temp": ref, "normalize": normalize,
            "r2_threshold": r2_threshold, "control": control, "treatment": treated},
            "metrics": metrics}


# =========================================================================
# Plots — matplotlib SVG (download) and Plotly JSON (interactive)
# =========================================================================
def _fig_to_svg(fig):
    buf = io.StringIO()
    fig.savefig(buf, format="svg", bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def plot_volcano(stats_df):
    fig, ax = plt.subplots(figsize=(7, 5))
    if stats_df.height:
        x = stats_df["ΔTm"].to_numpy().astype(float)
        y = -np.log10(np.clip(stats_df["FDR"].to_numpy().astype(float), 1e-300, None))
        sig = stats_df["Significant"].to_numpy().astype(bool)
        ax.scatter(x[~sig], y[~sig], c="#9aa", s=22, alpha=0.6, label="ns")
        ax.scatter(x[sig], y[sig], c="#e4572e", s=28, alpha=0.85, label="significant")
        ax.axhline(-np.log10(0.05), ls="--", c="#e4572e", alpha=0.5)
    ax.set_xlabel("ΔTm (°C)"); ax.set_ylabel("-log10(FDR)")
    ax.set_title("Volcano plot of melting-point changes"); ax.legend(frameon=False)
    return _fig_to_svg(fig)


def plot_violin(averaged_table, treatments):
    fig, ax = plt.subplots(figsize=(7, 5))
    data = [averaged_table.filter(pl.col("treatment") == t)["melting_point"].drop_nulls().to_numpy()
            for t in treatments]
    data = [d for d in data if len(d)]
    if data:
        ax.violinplot(data, showmeans=True, showmedians=True)
        rng = np.random.default_rng(0)
        for i, d in enumerate(data):
            ax.scatter(rng.normal(i + 1, 0.04, size=len(d)), d, s=8, c="black", alpha=0.45)
    ax.set_xticks(range(1, len(treatments) + 1)); ax.set_xticklabels(treatments)
    ax.set_ylabel("Melting point (°C)"); ax.set_title("Melting points by treatment")
    return _fig_to_svg(fig)


def plot_distribution(summary_table):
    data = summary_table["melting_point"].drop_nulls().to_numpy()
    data = data[(data >= 0) & (data <= 100)]
    fig, ax = plt.subplots(figsize=(7, 5))
    if len(data):
        bins = calculate_bin_width(data)
        ax.hist(data, bins=bins, color="#9ec5fe", edgecolor="black", alpha=0.7, label="Histogram")
        kde = gaussian_kde(data); xs = np.linspace(data.min(), data.max(), 200)
        ax.plot(xs, kde(xs) * len(data) * (data.max() - data.min()) / bins, c="#1d3f8a", lw=2, label="Density")
        ax.legend(frameon=False)
    ax.set_xlabel("Melting point (°C)"); ax.set_ylabel("Frequency")
    ax.set_title("Distribution of melting points")
    return _fig_to_svg(fig)


import plotly.express as _px  # noqa: E402
import plotly.graph_objects as _go  # noqa: E402


def plotly_volcano(stats_df):
    fig = _go.Figure()
    if stats_df.height:
        x = stats_df["ΔTm"].to_list()
        y = (-np.log10(np.clip(stats_df["FDR"].to_numpy().astype(float), 1e-300, None))).tolist()
        colors = ["red" if s else "gray" for s in stats_df["Significant"].to_list()]
        fig.add_trace(_go.Scatter(x=x, y=y, mode="markers",
                      marker=dict(color=colors, size=8, opacity=0.7), text=stats_df["Protein"].to_list(),
                      hovertemplate="<b>%{text}</b><br>ΔTm: %{x:.2f}°C<br>-log10(FDR): %{y:.2f}<extra></extra>"))
        fig.add_hline(y=-np.log10(0.05), line_dash="dash", line_color="red", opacity=0.5)
    fig.update_layout(title="Volcano Plot of Melting Point Changes", xaxis_title="ΔTm (°C)",
                      yaxis_title="-log10(FDR)", template="plotly_white", showlegend=False)
    return fig.to_json()


def plotly_violin(averaged_table, treatments):
    colors = _px.colors.qualitative.Plotly
    fig = _go.Figure()
    rng = np.random.default_rng(0)
    for i, t in enumerate(treatments):
        td = averaged_table.filter(pl.col("treatment") == t)["melting_point"].drop_nulls().to_numpy()
        td = td[(td >= 0) & (td <= 100)]
        fig.add_trace(_go.Violin(y=td, x=[i] * len(td), name=f"{t} Violin", box_visible=True,
                      meanline_visible=True, opacity=0.6, width=0.6, points=False, line_color="black",
                      fillcolor=colors[i % len(colors)], showlegend=True))
        fig.add_trace(_go.Scatter(y=td, x=i + rng.uniform(-0.05, 0.05, size=len(td)), mode="markers",
                      name=f"{t} Points", marker=dict(color="black", size=6, opacity=0.8), showlegend=True))
    fig.update_layout(title="Melting Point Comparison Across Treatments", xaxis_title="Treatment",
                      yaxis_title="Melting Point (°C)", template="plotly_white", violinmode="overlay",
                      xaxis=dict(tickmode="array", tickvals=list(range(len(treatments))), ticktext=treatments))
    return fig.to_json()


def plotly_distribution(summary_table):
    data = summary_table["melting_point"].drop_nulls().to_numpy()
    data = data[(data >= 0) & (data <= 100)]
    fig = _go.Figure()
    if len(data):
        bins = calculate_bin_width(data)
        fig.add_trace(_go.Histogram(x=data, nbinsx=bins,
                      marker=dict(color="lightblue", line=dict(color="black", width=1)),
                      opacity=0.7, name="Histogram"))
        kde = gaussian_kde(data); xs = np.linspace(data.min(), data.max(), 200)
        fig.add_trace(_go.Scatter(x=xs, y=kde(xs) * len(data) * (data.max() - data.min()) / bins,
                      mode="lines", line=dict(color="darkblue", width=2), name="Density Curve"))
    fig.update_layout(title="Overall Distribution of Melting Points", xaxis_title="Melting Point (°C)",
                      yaxis_title="Frequency", template="plotly_white", bargap=0.1)
    return fig.to_json()


# =========================================================================
# Per-protein curves (for job.map)
# =========================================================================
def _safe_name(name):
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in str(name))


def _plot_averaged_curve(protein, data_dict, ref, normalize, r2_threshold):
    fig, ax = plt.subplots(figsize=(10, 6))
    fits = []
    for treatment, proteins in data_dict.items():
        if protein not in proteins:
            continue
        temp_dict = proteins[protein]
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
            nv = values[np.where(temperatures == ref)[0][0]]
            if nv != 0:
                values = values / nv
        try:
            popt, _ = curve_fit(sigmoid, temperatures, values,
                                p0=[np.max(values), np.median(temperatures), np.min(values)])
            fitted = sigmoid(temperatures, *popt)
            r2 = 1 - np.sum((values - fitted) ** 2) / np.sum((values - np.mean(values)) ** 2)
            if r2 < r2_threshold:
                continue
            fits.append({"treatment": treatment, "temperatures": temperatures, "values": values,
                         "popt": popt, "r_squared": r2})
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
        ax.text(mt + off, mv, f"{mt:.2f}°C", color="red", ha="center", va="bottom", fontsize=10, zorder=6)
    ax.set_xlabel("Temperature (°C)")
    ax.set_ylabel("Normalized Intensity" if normalize else "Intensity")
    ax.set_title(f"Averaged melting curves for {protein}")
    ax.legend(bbox_to_anchor=(1.05, 1), loc="upper left")
    # Reserve room for the outside legend so we can savefig without the (expensive)
    # bbox_inches="tight" pass. Few, short labels here → a modest right margin fits.
    fig.subplots_adjust(right=0.74)
    return fig


def _plot_replicate_curve(protein, data_dict, params):
    _, fig, _ = process_protein_replicates(
        (protein, data_dict, ["o", "s", "^", "v"], [50, 75, 100], [1.0, 0.8, 0.6],
         params["ref_temp"], params["normalize"], params["r2_threshold"]))
    return fig


def plot_one(protein, replicate_data, params, kind="averaged"):
    sliced = _slice_replicate_data(replicate_data, protein)
    if kind == "replicate":
        return _plot_replicate_curve(protein, sliced, params)
    return _plot_averaged_curve(protein, sliced, params["ref_temp"], params["normalize"],
                                params["r2_threshold"])
