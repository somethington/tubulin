"""Result preview: ``python -m kinesin.preview <path> [limit]`` -> JSON.

Reads the first ``limit`` rows (default 100) of a result file and prints a single
JSON document: ``{columns, rows, total_rows, truncated}``. This enforces Memory
Rule 4 — the browser only ever sees a preview, never the full dataset.
"""

from __future__ import annotations

import json
import sys
from typing import Any


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


def main() -> None:
    if len(sys.argv) < 2:
        sys.stderr.write("usage: python -m kinesin.preview <path> [limit]\n")
        sys.exit(2)

    path = sys.argv[1]
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 100

    import polars as pl

    lower = path.lower()
    if lower.endswith(".csv"):
        df = pl.read_csv(path)
    elif lower.endswith((".ipc", ".arrow", ".feather")):
        df = pl.read_ipc(path)
    else:
        df = pl.read_parquet(path)

    total = df.height
    head = df.head(limit)
    rows = [[_json_safe(v) for v in row] for row in head.iter_rows()]

    json.dump(
        {
            "columns": head.columns,
            "rows": rows,
            "total_rows": total,
            "truncated": total > limit,
        },
        sys.stdout,
    )
    sys.stdout.flush()


if __name__ == "__main__":
    main()
