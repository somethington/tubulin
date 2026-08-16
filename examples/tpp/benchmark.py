"""Compare the incumbent Streamlit-style execution against the Kinesin-managed run.

Both modes run the *identical* tpp_solver science (same seeded imputation, same
`scipy.curve_fit` sigmoidal fits, same Mann-Whitney + Benjamini-Hochberg). The only
difference is the execution model:

  * "streamlit"  — fits and **retains a matplotlib Figure per protein in a dict**,
                   exactly like `fit_and_plot_replicates`; everything stays in
                   process memory (the Streamlit session).
  * "kinesin"    — fits without plotting, writes the result table to disk, keeps
                   only that; in the real app the worker then exits.

Each mode runs in its own subprocess so peak RSS is clean. We assert the scientific
metrics are identical, then report the performance/memory difference.

    python examples/tpp/benchmark.py
"""

from __future__ import annotations

import hashlib
import json
import platform
import resource
import subprocess
import sys
import time
import tracemalloc
from pathlib import Path

HERE = Path(__file__).resolve().parent
TSV = HERE / "data" / "sample_data.tsv"
META = HERE / "data" / "sample_metadata.csv"


def _peak_rss_mb() -> float:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss / (1024 * 1024) if platform.system() == "Darwin" else rss / 1024


def _melting_point_hash(summary_table) -> str:
    mps = sorted(round(float(x), 6) for x in summary_table["melting_point"])
    return hashlib.sha256(",".join(map(str, mps)).encode()).hexdigest()[:16]


def run_one(mode: str) -> dict:
    """Run a single mode in this process and emit a JSON result line on stdout."""
    import tpp_pipeline as tpp

    real_stdout = sys.stdout
    sys.stdout = sys.stderr  # keep tpp_solver's stray prints off our JSON channel

    tracemalloc.start()
    t0 = time.time()
    result = tpp.run(str(TSV), str(META), mode=mode)
    wall = time.time() - t0
    _, py_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    metrics = result["metrics"]
    payload = {
        "mode": mode,
        "wall_s": round(wall, 3),
        "py_peak_mb": round(py_peak / 1e6, 1),
        "rss_peak_mb": round(_peak_rss_mb(), 1),
        "mp_hash": _melting_point_hash(result["summary_table"]),
        "metrics": metrics,
    }
    sys.stdout = real_stdout
    print("RESULT " + json.dumps(payload))
    return payload


def _spawn(mode: str) -> dict:
    proc = subprocess.run(
        [sys.executable, str(HERE / "benchmark.py"), "_run", mode],
        capture_output=True,
        text=True,
        cwd=HERE,
    )
    for line in proc.stdout.splitlines():
        if line.startswith("RESULT "):
            return json.loads(line[len("RESULT ") :])
    raise RuntimeError(f"no result from {mode} run:\n{proc.stdout}\n{proc.stderr}")


def main() -> None:
    print("Running TPP analysis in both modes (identical science, different host)…\n")
    sl = _spawn("streamlit")
    kn = _spawn("kinesin")

    # --- Metrics must be identical (correctness of the adaptation) -------------
    keys = [
        "proteins_in",
        "curves_fit",
        "proteins_fit",
        "proteins_tested",
        "significant",
    ]
    sm, km = sl["metrics"], kn["metrics"]
    metrics_match = all(sm[k] == km[k] for k in keys) and sl["mp_hash"] == kn["mp_hash"]

    def row(label, a, b):
        print(f"  {label:<28} {a:>22} {b:>22}")

    print("=" * 76)
    print(f"  {'METRIC':<28} {'Streamlit-style':>22} {'Kinesin':>22}")
    print("-" * 76)
    row("proteins in", sm["proteins_in"], km["proteins_in"])
    row("curves fit (R^2>=0.8)", sm["curves_fit"], km["curves_fit"])
    row("proteins fit", sm["proteins_fit"], km["proteins_fit"])
    row("proteins tested", sm["proteins_tested"], km["proteins_tested"])
    row("significant (FDR<0.05)", sm["significant"], km["significant"])
    row("melting-point fingerprint", sl["mp_hash"], kn["mp_hash"])
    print("-" * 76)
    print("  PERFORMANCE / MEMORY")
    row("wall time (s)", sl["wall_s"], kn["wall_s"])
    row("figures held in RAM", sm["figures_held"], km["figures_held"])
    row("peak Python heap (MB)", sl["py_peak_mb"], kn["py_peak_mb"])
    row("peak process RSS (MB)", sl["rss_peak_mb"], kn["rss_peak_mb"])
    print("=" * 76)

    print()
    if metrics_match:
        print("✓ METRICS IDENTICAL — the adaptation preserves the science exactly.")
    else:
        print("✗ METRICS DIFFER — investigate before trusting the comparison.")

    heap_ratio = (
        sl["py_peak_mb"] / kn["py_peak_mb"] if kn["py_peak_mb"] else float("inf")
    )
    print(
        f"\nKinesin runs the same analysis holding {sm['figures_held']} fewer figures, "
        f"with ~{heap_ratio:.1f}x less peak Python heap, and persists the result to disk\n"
        f"(only a 100-row preview is sent to the browser). In the real app the worker\n"
        f"then exits, returning all of that memory to the OS."
    )

    if not metrics_match:
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "_run":
        run_one(sys.argv[2])
    else:
        main()
