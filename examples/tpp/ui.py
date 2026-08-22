"""TPP Solver, adapted to Tubulin — full options + plots, wired to bundled data.

    tubulin run examples/tpp/ui.py

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
normalize = app.checkbox("Normalize to a reference temperature", default=True)
ref_temp = app.select(
    "Select the reference temperature (°C)",
    [str(t) for t in _temps],
    default=str(_temps[0]),
)
r2 = app.slider(
    "R² threshold for accepting a fit", min=0.5, max=0.99, step=0.01, default=0.8
)
seed = app.number("Imputation seed", default=42)

# Two collapsible cards, mirroring the upstream Streamlit app.
with app.group("Data Visualization Options"):
    show_distribution = app.checkbox(
        "Show Distribution of Melting Points", default=True
    )
    show_violin = app.checkbox(
        "Compare Melting Points Between Treatments (Violin Plot)", default=True
    )
    show_volcano = app.checkbox("Mann-Whitney and Benjamini-Hochberg", default=True)
    show_curves = app.checkbox("Per-protein melting-curve gallery", default=True)
    max_curves = app.number("Max curves to plot (0 = all fitted proteins)", default=0)

with app.group("Shapiro-Wilk Normality Test"):
    perform_sw = app.checkbox("Perform Shapiro-Wilk Test", default=False)
    # The transformation choices are disabled until Shapiro-Wilk is enabled.
    app.header("Normality Test Selection")
    sw_log = app.checkbox("Log Transformation", default=True, enabled_when=perform_sw)
    sw_sqrt = app.checkbox(
        "Square Root Transformation", default=True, enabled_when=perform_sw
    )
    sw_boxcox = app.checkbox(
        "Box-Cox Transformation", default=True, enabled_when=perform_sw
    )
    sw_yeojohnson = app.checkbox(
        "Yeo-Johnson Transformation", default=True, enabled_when=perform_sw
    )
    # No "Apply" button here: the upstream Streamlit app needed one because it doesn't
    # cache these settings (they reset on the next rerun). Tubulin persists widget values
    # in session state, so the choices simply take effect when "Run TPP analysis" runs.

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
    perform_sw.value,
    sw_log.value,
    sw_sqrt.value,
    sw_boxcox.value,
    sw_yeojohnson.value,
    max_curves.value,
)
run.on_click(job)

app.job_status(job)
app.download(job, "bundle", "⬇  Download analysis results (.zip)")
app.header("Results")
app.table_preview(
    job.result_path
)  # Mann-Whitney + FDR stats table (per-replicate fits)
app.plot(job, "volcano", caption="Volcano plot — ΔTm vs -log10(FDR)")
app.plot(job, "violin", caption="Violin plot — melting points by treatment")
app.plot(job, "distribution", caption="Distribution of melting points")
# The per-protein curves are rendered to SVG for the download zip regardless, so the
# gallery (which just lists those files) is essentially free — no extra analysis time.
app.gallery(
    job, "avg_curves", caption="Averaged melting curves — one sigmoid per treatment"
)
app.gallery(
    job,
    "rep_curves",
    caption="Per-replicate melting curves — one sigmoid per replicate",
)
app.result_table(
    job, "averaged", caption="Averaged data — one fit to mean-of-replicate intensities"
)
app.result_table(
    job, "normality", caption="Shapiro-Wilk normality of ΔTm transformations"
)
