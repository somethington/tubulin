"""Milestones 1-3: static UI, a browser event reaching the runtime, and an action
the runtime executes that updates a widget — no job, no Python in the request path.

    kinesin run examples/hello_world/ui.py
"""

from kinesin import App

app = App("Hello Kinesin")

greeting = app.text("Click the button below.")
button = app.button("Say hello")

# `set_text` is a declarative action: the runtime updates the text widget itself.
button.on_click(app.set_text(greeting, "Hello from the Rust runtime! 👋"))
