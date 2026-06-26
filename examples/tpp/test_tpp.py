"""Example: testing a Kinesin app headlessly with ``kinesin.testing``.

Run it directly (``python examples/tpp/test_tpp.py``) or under pytest. It runs the
real job in-process — no browser, no runtime — and asserts on what it produced. Pass
``number_2`` (the "Max curves" widget, whose label contains "=") a small value so the
test renders a handful of curves instead of all ~1200.
"""

from kinesin.testing import run_app

UI = "examples/tpp/ui.py"


def test_tpp_produces_expected_outputs():
    r = run_app(UI, inputs={"Imputation seed": 42, "number_2": 8})

    # Primary result + named tables exist and are non-empty.
    assert r.result_path and r.result_path.endswith("_stats.parquet")
    stats = r.table()
    assert {"Protein", "ΔTm", "P-value", "FDR"} <= set(stats.columns)
    assert stats.height > 0
    assert r.table("averaged").height > 0

    # Every bound output the UI expects was written.
    assert {"volcano", "violin", "distribution", "averaged", "normality"} <= set(r.outputs)

    # The curve fan-out wrote SVGs (limited by our max-curves override).
    assert 0 < len(r.gallery("avg_curves")) <= 8

    # Progress and logs streamed.
    assert r.logs and r.progress


if __name__ == "__main__":
    test_tpp_produces_expected_outputs()
    print("ok")
