"""A gallery of every Tubulin widget and layout container, in one runnable app.

    tubulin run examples/widgets/ui.py

Nothing here is scientifically interesting — it exists so you can see what the
framework offers, and so a change to the widget layer has one place that exercises
all of it. Each section names the call that produced it.
"""

import json
import math

from tubulin import App

app = App("Widget Gallery")

CONDITIONS = ["37C", "42C", "47C", "52C", "57C", "62C"]

# ---------------------------------------------------------------- sidebar
# Widgets declared inside `app.sidebar()` render in the left rail; their values
# behave exactly like main-column widgets.
with app.sidebar():
    app.markdown("### Run settings")
    dataset = app.select("Dataset", ["Synthetic", "Replicate A", "Replicate B"])
    points = app.slider("Points per curve", 20, 200, step=10, default=80)
    conditions = app.multiselect("Conditions", CONDITIONS, default=CONDITIONS[:3])

# ---------------------------------------------------------------- main
app.markdown(
    """
    ## Widget gallery

    Every input below feeds the same job. The runtime owns the queue, the worker
    process and the result files — this file only *describes* the app.

    - `app.markdown` renders **bold**, `code`, [links](https://example.com) and lists
    - `app.columns` / `app.tabs` / `app.sidebar` place widgets without touching CSS
    - `app.group` collapses a section

    ---
    """
)

with app.tabs("Inputs", "Advanced", "About") as (inputs_tab, advanced_tab, about_tab):
    with inputs_tab:
        with app.columns([1, 1]) as (left, right):
            with left:
                label = app.text_input(
                    "Run label", default="gallery-1", placeholder="e.g. HeLa-rep2"
                )
                amplitude = app.number("Amplitude", default=1.0)
            with right:
                noise = app.slider("Noise", 0.0, 1.0, step=0.05, default=0.15)
                normalize = app.checkbox("Normalize curves", default=True)

        notes = app.text_area(
            "Notes",
            placeholder="Anything worth recording with this run…",
            rows=3,
        )

    with advanced_tab:
        with app.group("Fitting", expanded=True):
            advanced = app.checkbox("Override defaults")
            # Every scalar input accepts `enabled_when`, not just checkboxes.
            app.number("Max iterations", default=500, enabled_when=advanced)
            app.select("Solver", ["trf", "lm", "dogbox"], enabled_when=advanced)
            app.slider("Tolerance", 0.0, 0.1, step=0.005, enabled_when=advanced)

    with about_tab:
        app.markdown(
            """
            ### About

            > Hidden tab panels stay mounted, so switching tabs never resets an input
            > or drops a result that already rendered.

            ```python
            with app.tabs("Inputs", "Advanced") as (a, b):
                with a:
                    app.text_input("Run label")
            ```
            """
        )

run = app.button("Generate")


# ---------------------------------------------------------------- the job
@app.job
def generate(
    label, dataset, conditions, points, amplitude, noise, normalize, notes, job
):
    import polars as pl

    job.log(f"{label!r} · {dataset} · {len(conditions)} condition(s)")
    if not conditions:
        raise ValueError("select at least one condition")

    rows = []
    for index, condition in enumerate(conditions):
        job.progress(int(100 * index / max(1, len(conditions))))
        shift = 1.0 + 0.25 * index
        for step in range(int(points)):
            x = 10 * step / max(1, int(points) - 1)
            # A deterministic wobble stands in for measurement noise.
            wobble = float(noise) * math.sin(7.3 * x + index)
            y = float(amplitude) * math.exp(-x / (2 * shift)) + wobble
            rows.append({"condition": condition, "x": round(x, 4), "y": round(y, 6)})

    df = pl.DataFrame(rows)
    if normalize:
        job.log("Normalizing each condition to its own maximum")
        df = df.with_columns(
            (pl.col("y") / pl.col("y").max().over("condition")).alias("y")
        )

    # Hand the framework a dataframe: it writes the parquet and the preview.
    job.result(df)
    job.table(
        "summary", df.group_by("condition").agg(pl.col("y").mean().alias("mean_y"))
    )

    figure = {
        "data": [
            {
                "type": "scatter",
                "mode": "lines",
                "name": condition,
                "x": part["x"].to_list(),
                "y": part["y"].to_list(),
            }
            # polars yields the group key as a tuple, even for a single column.
            for (condition,), part in df.group_by("condition", maintain_order=True)
        ],
        "layout": {"title": {"text": f"{label} — {dataset}"}},
    }
    job.output("curves", job.write_text("curves.json", json.dumps(figure)))

    if notes.strip():
        job.output("notes", job.write_text("notes.txt", notes))
    job.progress(100)


binding = generate.submit(
    label.value,
    dataset.value,
    conditions.value,
    points.value,
    amplitude.value,
    noise.value,
    normalize.value,
    notes.value,
)
run.on_click(binding)
app.job_status(binding)

with app.columns([3, 2]) as (chart_col, table_col):
    with chart_col:
        app.plot(binding, "curves", caption="Decay curves")
    with table_col:
        app.result_table(binding, "summary", caption="Mean per condition")

app.table_preview(binding.result_path)
