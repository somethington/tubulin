"""Smoke tests for the Kinesin Python layer.

Run with:  PYTHONPATH=python .venv/bin/python -m pytest tests/
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import polars as pl
import pytest

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


def test_parallel_auto_skips_primary_and_aggregates_progress(tmp_path):
    """A parallel job declares maps + a primary without any scheduling knobs; the
    framework keeps shard 0 out of the maps and streams smooth 0-100 progress."""
    from kinesin import protocol
    from kinesin.worker import JobContext, execute_job

    proteins = [f"P{i}" for i in range(12)]

    def job_fn(job):
        # No skip_primary, no progress ranges — the framework infers both.
        job.map(
            proteins, lambda p: None, into="avg"
        )  # fn writes its own files (none here)
        job.map(proteins, lambda p: None, into="rep")

        @job.primary
        def _summary():
            job.output("summary", "ran-on-shard-0")

    shards = 4
    seen_per_shard = []
    progresses = []
    old = protocol.progress
    protocol.progress = lambda p: progresses.append(int(p))  # type: ignore[assignment]
    try:
        for shard in range(shards):
            progresses.clear()
            ctx = JobContext("jid", str(tmp_path), shard=shard, num_shards=shards)
            # Count items each shard actually maps over.
            counted = {"avg": 0, "rep": 0}
            ctx.map(
                proteins,
                lambda p, c=counted: c.__setitem__("avg", c["avg"] + 1),
                into="avg",
            )
            ctx.map(
                proteins,
                lambda p, c=counted: c.__setitem__("rep", c["rep"] + 1),
                into="rep",
            )

            @ctx.primary
            def _summary():
                ctx.output("summary", "ran-on-shard-0")

            _, outputs = execute_job(lambda job: None, [], ctx)
            seen_per_shard.append((counted, "summary" in outputs, list(progresses)))
    finally:
        protocol.progress = old  # type: ignore[assignment]

    # Shard 0 runs the primary and maps nothing; the others split the items.
    assert seen_per_shard[0][0] == {"avg": 0, "rep": 0}
    assert seen_per_shard[0][1] is True  # primary ran on shard 0 only
    assert all(not s[1] for s in seen_per_shard[1:])
    # Every protein is mapped exactly once across the non-primary shards (both maps).
    assert sum(s[0]["avg"] for s in seen_per_shard) == len(proteins)
    assert sum(s[0]["rep"] for s in seen_per_shard) == len(proteins)
    # A worker shard's progress is monotonic and tops out at 100 (two maps share 0-100).
    worker = seen_per_shard[1][2]
    assert worker == sorted(worker) and worker[-1] == 100


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


def test_text_and_choice_widgets_spec():
    """The Tier-0 input widgets: free text, multi-choice, and rich text."""
    from kinesin import App

    app = App("inputs")
    app.markdown(
        """
        ## Heading
        Some **bold** prose.
        """
    )
    name = app.text_input("Sample name", default="run-1", placeholder="e.g. HeLa")
    notes = app.text_area("Accessions", rows=6)
    gate = app.checkbox("Filter", default=True)
    conds = app.multiselect(
        "Conditions", ["37C", "42C", "47C"], default=["47C", "37C"], enabled_when=gate
    )

    nodes = {n["id"]: n for n in app.to_spec()["nodes"]}

    # markdown is dedented at registration, so an indented triple-quoted string works.
    md = nodes["markdown_1"]["props"]["value"]
    assert md.startswith("## Heading")
    assert "**bold**" in md and "\n        " not in md

    assert nodes[name.id]["props"] == {
        "label": "Sample name",
        "value": "run-1",
        "placeholder": "e.g. HeLa",
    }
    assert nodes[notes.id]["props"]["rows"] == 6
    assert nodes[notes.id]["props"]["value"] == ""

    # The default is normalised to declaration order, not the order it was passed in.
    assert nodes[conds.id]["props"]["value"] == ["37C", "47C"]
    assert nodes[conds.id]["props"]["enabled_when"] == gate.id


def test_multiselect_rejects_unknown_default():
    from kinesin import App

    app = App("inputs")
    with pytest.raises(ValueError, match="don't exist"):
        app.multiselect("Conditions", ["a", "b"], default=["a", "z"])


def test_enabled_when_covers_every_scalar_input():
    """A gate on one widget should be expressible on all of them, not just checkboxes."""
    from kinesin import App

    app = App("gates")
    gate = app.checkbox("Advanced")
    gated = [
        app.slider("S", 0, 10, enabled_when=gate),
        app.number("N", enabled_when=gate),
        app.select("Sel", ["a", "b"], enabled_when=gate),
        app.text_input("T", enabled_when=gate),
        app.text_area("A", enabled_when=gate),
    ]
    nodes = {n["id"]: n for n in app.to_spec()["nodes"]}
    assert all(nodes[w.id]["props"]["enabled_when"] == gate.id for w in gated)


def test_text_and_list_values_reach_a_job(tmp_path):
    """End-to-end through the headless harness: a job gets a str and a list[str]."""
    from kinesin.testing import run_app

    ui = tmp_path / "ui.py"
    ui.write_text(
        "import kinesin\n"
        "app = kinesin.App('t')\n"
        "name = app.text_input('Sample name', default='run-1')\n"
        "conds = app.multiselect('Conditions', ['37C', '42C', '47C'], default=['37C'])\n"
        "run = app.button('Go')\n"
        "@app.job\n"
        "def echo(name, conds, job):\n"
        "    job.output('seen', job.write_text('seen', repr((name, conds))))\n"
        "binding = echo.submit(name.value, conds.value)\n"
        "run.on_click(binding)\n"
    )

    # Widget defaults flow through untouched.
    r = run_app(str(ui), result_dir=str(tmp_path / "out1"))
    assert Path(r.outputs["seen"]).read_text() == repr(("run-1", ["37C"]))

    # ...and `inputs` overrides them by label, list value included.
    r = run_app(
        str(ui),
        inputs={"Sample name": "HeLa", "Conditions": ["42C", "47C"]},
        result_dir=str(tmp_path / "out2"),
    )
    assert Path(r.outputs["seen"]).read_text() == repr(("HeLa", ["42C", "47C"]))
