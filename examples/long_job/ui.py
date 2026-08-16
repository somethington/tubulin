"""Milestones 5-7: submit a long-running job, watch it stream progress and logs,
then preview the small result it writes to disk.

    kinesin run examples/long_job/ui.py
"""

import time

from kinesin import App

app = App("Long Job Demo")

app.text("Submit a long-running job and watch it stream progress over the WebSocket.")
run = app.button("Start 5-second job")


@app.job
def long_task(job):
    for i in range(1, 6):
        time.sleep(1)
        job.progress(i * 20)
        job.log(f"step {i}/5 complete")

    import polars as pl

    df = pl.DataFrame(
        {"step": list(range(1, 6)), "value": [i * i for i in range(1, 6)]}
    )
    out = f"{job.result_dir}/{job.id}.parquet"
    df.write_parquet(out)
    return {"result_path": out}


job = long_task.submit()
run.on_click(job)
app.job_status(job)
app.table_preview(job.result_path)
