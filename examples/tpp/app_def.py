"""Shared Tubulin App instance for the TPP example.

Both ui.py (widgets) and compute.py (the job) import this single app, which is the
ui.py / compute.py split the runtime is designed for: lightweight UI in one file,
heavy scientific code in another.
"""

from tubulin import App

app = App("TPP Solver — Tubulin")
