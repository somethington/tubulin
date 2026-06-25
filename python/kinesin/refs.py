"""Reference tokens produced during the registration pass.

At registration time we are *describing* an app, not running it, so values that
only exist at runtime (an uploaded file's path, a job's result) are represented as
reference tokens. The Rust runtime resolves them later against session state.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Ref:
    """A reference into a session's state, e.g. ``upload_1.path``."""

    node: str
    field: str


@dataclass(frozen=True)
class SlotResultRef:
    """A reference to the result a job slot will eventually produce on disk."""

    slot: str
