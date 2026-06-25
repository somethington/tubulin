"""TPP Solver, adapted to Kinesin — full options + plots, wired to bundled data.

    kinesin run examples/tpp/ui.py

Exposes the same knobs as the upstream Streamlit app (normalization + reference
temperature, R^2 threshold, imputation seed, and toggles for each plot/analysis)
and produces the same outputs: the Mann-Whitney/FDR stats table, a volcano plot, a
violin plot, the melting-point distribution, example fitted curves, and a
Shapiro-Wilk normality table. Click "Run TPP analysis" — it runs on the bundled
sample data, so there's nothing to upload.
"""

from pathlib import Path

import pandas as pd

from app_def import app

import compute  # noqa: F401  — registers the tpp_analyze job on `app`

DATA = Path(__file__).resolve().parent / "data"
TSV = str(DATA / "sample_data.tsv")
META = str(DATA / "sample_metadata.csv")

# Reference-temperature options come from the metadata (read at registration).
_temps = sorted(pd.read_csv(META)["Temperature"].dropna().astype(float).unique())

app.text(
    "Thermal Proteome Profiling, adapted from tpp_solver — same options and outputs "
    "as the Streamlit app, wired to the bundled sample data (1008 proteins, control "
    "vs ADEP4). Adjust the options, then click Run."
)

app.header("Analysis options")
normalize = app.checkbox("Normalize curves to a reference temperature", default=True)
ref_temp = app.select("Reference temperature (°C)", [str(t) for t in _temps], default=str(_temps[0]))
r2 = app.slider("R² threshold for accepting a fit", min=0.5, max=0.99, step=0.01, default=0.8)
seed = app.number("Imputation seed", default=42)

app.header("Outputs to generate")
show_volcano = app.checkbox("Volcano plot + statistics", default=True)
show_violin = app.checkbox("Violin plot (melting points by treatment)", default=True)
show_distribution = app.checkbox("Melting-point distribution", default=True)
show_curves = app.checkbox("Per-protein melting-curve gallery", default=True)
show_normality = app.checkbox("Shapiro-Wilk normality tests", default=True)
max_curves = app.number("Max curves to plot (0 = all fitted proteins)", default=0)

run = app.button("Run TPP analysis")

# Order must match the tpp_analyze signature.
job = compute.tpp_analyze.submit(
    TSV,
    META,
    normalize.value,
    ref_temp.value,
    r2.value,
    seed.value,
    show_volcano.value,
    show_violin.value,
    show_distribution.value,
    show_curves.value,
    show_normality.value,
    max_curves.value,
)
run.on_click(job)

app.job_status(job)
app.download(job, "bundle", "⬇  Download analysis results (.zip)")
app.header("Results")
app.table_preview(job.result_path)  # Mann-Whitney + FDR stats table (per-replicate fits)
app.plot(job, "volcano", caption="Volcano plot — ΔTm vs -log10(FDR)")
app.plot(job, "violin", caption="Violin plot — melting points by treatment")
app.plot(job, "distribution", caption="Distribution of melting points")
app.gallery(job, "gallery_avg", caption="Averaged melting curves — one sigmoid per treatment")
app.gallery(job, "gallery_rep", caption="Per-replicate melting curves — one sigmoid per replicate")
app.result_table(job, "averaged", caption="Averaged data — one fit to mean-of-replicate intensities")
app.result_table(job, "normality", caption="Shapiro-Wilk normality of ΔTm transformations")
