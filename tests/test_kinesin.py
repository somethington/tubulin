"""Smoke tests for the Kinesin Python layer.

Run with:  PYTHONPATH=python .venv/bin/python -m pytest tests/
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import polars as pl

REPO = Path(__file__).resolve().parents[1]
ENV = {**os.environ, "PYTHONPATH": str(REPO / "python")}


def test_registration_spec_shape():
    from kinesin import App

    app = App("T")
    app.text("hello")
    upload = app.file_upload("Upload")
    btn = app.button("Go")

    @app.job
    def crunch(path, job):
        return {"result_path": "x"}

    binding = crunch.submit(upload.path)
    btn.on_click(binding)
    app.job_status(binding)
    app.table_preview(binding.result_path)

    spec = app.to_spec()
    assert spec["title"] == "T"
    kinds = [n["kind"] for n in spec["nodes"]]
    assert kinds == ["text", "file_upload", "button", "job_status", "table_preview"]

    action = spec["actions"]["button_1"]
    assert action["type"] == "submit_job"
    assert action["job"] == "crunch"
    assert action["args"] == [{"kind": "ref", "node": "file_upload_1", "field": "path"}]

    # The job's module_file is *this* test file (where crunch is defined).
    assert spec["jobs"]["crunch"]["module_file"] == str(Path(__file__).resolve())
    assert spec["bindings"]["job_status_1"]["view"] == "status"
    assert spec["bindings"]["table_preview_1"]["view"] == "result_preview"


def test_worker_runs_job_and_writes_result(tmp_path):
    # A tiny job module the worker will import and run.
    module = tmp_path / "compute.py"
    module.write_text(
        "from kinesin import get_active_app\n"
        "import kinesin\n"
        "_app = kinesin.App('w')\n"
        "@_app.job\n"
        "def summarize(path, job):\n"
        "    import polars as pl\n"
        "    job.log('reading')\n"
        "    job.progress(50)\n"
        "    df = pl.read_csv(path)\n"
        "    out = f'{job.result_dir}/{job.id}.parquet'\n"
        "    df.describe().write_parquet(out)\n"
        "    return {'result_path': out}\n"
    )
    csv = tmp_path / "in.csv"
    csv.write_text("a,b\n1,2\n3,4\n5,6\n")
    result_dir = tmp_path / "results"
    result_dir.mkdir()

    descriptor = {
        "job_id": "job_test",
        "module_file": str(module),
        "qualname": "summarize",
        "args": [str(csv)],
        "result_dir": str(result_dir),
    }
    proc = subprocess.run(
        [sys.executable, "-m", "kinesin.worker"],
        input=json.dumps(descriptor),
        capture_output=True,
        text=True,
        env=ENV,
        cwd=REPO,
    )
    lines = [json.loads(l) for l in proc.stdout.splitlines() if l.strip()]
    events = [m["event"] for m in lines]
    assert "log" in events and "progress" in events
    assert events[-1] == "completed", proc.stderr
    out_path = lines[-1]["result"]["path"]
    assert Path(out_path).exists()
    # describe() of 2 numeric columns -> 9 stat rows, 3 columns (statistic, a, b)
    assert pl.read_parquet(out_path).shape == (9, 3)


def test_preview_returns_first_rows(tmp_path):
    parquet = tmp_path / "r.parquet"
    pl.DataFrame({"n": range(250)}).write_parquet(parquet)

    proc = subprocess.run(
        [sys.executable, "-m", "kinesin.preview", str(parquet), "100"],
        capture_output=True,
        text=True,
        env=ENV,
        cwd=REPO,
    )
    data = json.loads(proc.stdout)
    assert data["columns"] == ["n"]
    assert len(data["rows"]) == 100
    assert data["total_rows"] == 250
    assert data["truncated"] is True
