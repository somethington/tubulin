"""Widget handles returned by the ``App`` factory methods.

A widget is just an id + kind + a mutable props bag. Behaviour that only some
widgets use (``.path`` on a file upload, ``.on_click`` on a button) lives on the
shared base for simplicity — this is an MVP, not a type-safe component tree.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Union

from .refs import Ref


class Widget:
    def __init__(self, app, node_id: str, kind: str, props: Dict[str, Any]):
        self.app = app
        self.id = node_id
        self.kind = kind
        self.props = props

    @property
    def path(self) -> Ref:
        """Reference to the path of the file uploaded into this widget."""
        return Ref(self.id, "path")

    @property
    def value(self) -> Ref:
        """Reference to this input widget's current value (checkbox/slider/...)."""
        return Ref(self.id, "value")

    def on_click(
        self, action: Union["JobBindingLike", Callable[[], Any], Dict[str, Any]]
    ) -> "Widget":
        """Bind an action to this widget's click.

        Accepts a :class:`~kinesin.jobs.JobBinding`, a 0-arg callable that returns
        one (e.g. ``lambda: job.submit(upload.path)``), or a raw action dict.
        Action callables are invoked **once** here during registration; they must
        be lightweight and only describe a submission.
        """
        if callable(action):
            action = action()
        if hasattr(action, "to_action"):
            self.app._actions[self.id] = action.to_action()
        elif isinstance(action, dict):
            self.app._actions[self.id] = action
        else:
            raise TypeError(
                f"on_click expects a JobBinding, a callable returning one, or an "
                f"action dict; got {type(action)!r}"
            )
        return self


# Forward-reference alias for typing only.
class JobBindingLike:  # pragma: no cover - typing helper
    def to_action(self) -> Dict[str, Any]: ...
