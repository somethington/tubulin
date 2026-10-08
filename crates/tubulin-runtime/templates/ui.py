"""{{title}} — a tubulin app, scaffolded by `tubulin new`.

    tubulin run ui.py      # then open http://127.0.0.1:8000

Edit this file and save: the app re-registers and the open browser picks up the new UI.
"""

from tubulin import App

app = App("{{title}}")

app.markdown("Pick a size and press **Run**. The job runs in its own Python worker.")
rows = app.slider("Rows", min=10, max=1000, step=10, default=100)
run = app.button("Run")


@app.job
def squares(rows: float, job):
    import polars as pl

    n = int(rows)
    job.log(f"computing {n} squares")
    df = pl.DataFrame({"x": range(n)}).with_columns(x_squared=pl.col("x") ** 2)
    job.progress(100)
    job.result(df)  # writes the parquet; the runtime previews the first 100 rows


job = squares.submit(rows.value)
run.on_click(job)  # clicking the button submits the job
app.job_status(job)  # live status, progress and streamed logs
app.table_preview(job.result_path)
