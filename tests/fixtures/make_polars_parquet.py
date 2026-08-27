"""Regenerate `polars_written.parquet`, the committed fixture behind the Rust
parquet-compatibility test.

    PYTHONPATH=python .venv/bin/python tests/fixtures/make_polars_parquet.py

The native preview reader in `crates/tubulin-runtime/src/artifacts/parquet/` is
verified against an *arrow*-written file by its other tests; this one is written by
**polars** (zstd), which is what real job outputs go through. Keep the shape in sync
with `reads_polars_written_parquet`.
"""

import os

import polars as pl

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "polars_written.parquet")

df = pl.DataFrame(
    {
        "name": ["alpha", "beta", "gamma"],
        "score": [1.5, float("nan"), 3.25],  # NaN must read back as null
        "count": [10, 20, 30],
        "flag": [True, False, True],
    }
)
df.write_parquet(OUT, compression="zstd")
print(f"wrote {OUT} ({os.path.getsize(OUT)} bytes)")
