"""The headline demo: upload a CSV, run a summary analysis as a managed job, and
preview the disk-backed result.

    kinesin run examples/csv_analysis/ui.py

A sample file is provided alongside this one (sample.csv).
"""

import time

from kinesin import App

app = App("CSV Analyzer")

app.text("Upload a CSV, then run a summary analysis as a managed, disk-backed job.")
upload = app.file_upload("Upload CSV")
run = app.button("Analyze")


@app.job
def analyze_csv(path: str, job):
    import polars as pl

    job.log(f"Reading {path}")
    df = pl.read_csv(path)
    job.progress(30)
    job.log(f"{df.height} rows x {df.width} columns")

    time.sleep(1)  # stand in for real scientific work
    job.log("Computing summary statistics")
    summary = df.describe()
    job.progress(80)

    out = f"{job.result_dir}/{job.id}.parquet"
    summary.write_parquet(out)
    job.progress(100)
    job.log("Done")
    return {"result_path": out}


# Build the binding once and reuse it so the button, status and preview all bind to
# the same job slot.
job = analyze_csv.submit(upload.path)
run.on_click(job)
app.job_status(job)
app.table_preview(job.result_path)
