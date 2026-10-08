"""The `tubulin new` template must stay a working app.

The template is compiled into the Rust binary (`crates/tubulin-runtime/src/scaffold.rs`);
this runs the same file through the headless harness so a framework change that
breaks it fails here rather than in a new user's first minute.
"""

import shutil
from pathlib import Path

import polars as pl

from tubulin.testing import run_app

TEMPLATE = (
    Path(__file__).resolve().parents[1] / "crates/tubulin-runtime/templates/ui.py"
)


def test_scaffold_template_runs(tmp_path):
    ui = tmp_path / "ui.py"
    shutil.copy(TEMPLATE, ui)
    ui.write_text(ui.read_text().replace("{{title}}", "Scaffold Test"))

    r = run_app(ui, inputs={"Rows": 25})

    df = pl.read_parquet(r.result)
    assert df.columns == ["x", "x_squared"]
    assert df.height == 25
    assert df["x_squared"][-1] == 24**2
