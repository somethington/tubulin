# Tubulin documentation

Tubulin is a Python-first framework for building **managed scientific web apps**: you
write a small Python file that declares a UI and one or more jobs, and a Rust runtime
owns everything else — the HTTP/WebSocket server, browser sessions, a job queue,
disposable (and optionally parallel) Python workers, and a disk-backed result
lifecycle. Large data never lives in session memory; only ids, paths, and small
previews cross the wire.

```python
# ui.py
from tubulin import App

app = App("Hello")
greeting = app.text("Click below.")
btn = app.button("Say hi")
btn.on_click(app.set_text(greeting, "Hello from the Rust runtime"))
```

```bash
tubulin run ui.py     # → http://127.0.0.1:8000
```

## Contents

| Doc | What's in it |
| --- | --- |
| [architecture.md](architecture.md) | The whole system: components, the registration pass, request/job lifecycles, sharding, memory model, route map, and honest limitations. **Start here.** |
| [python-api.md](python-api.md) | The Python developer reference: `App`, every widget (incl. collapsible `group` and conditional `enabled_when`), refs, the `@app.job` decorator, and the `JobContext` primitives (`map`/`primary`/dataframe-native `result`/`table`/`write_*`). |
| [parallelism.md](parallelism.md) | The parallel-job model in depth: `job.map`/`job.primary`, how the runtime allocates workers, the memory/CPU tradeoffs, and what's missing for multi-tenant servers. |
| [testing.md](testing.md) | The headless `tubulin.testing` harness: run a job in-process and assert on its outputs — no browser, no server. |
| [protocol.md](protocol.md) | The wire contract: browser↔runtime messages, the app spec, the worker line protocol, and the HTTP routes. |
| [runtime-internals.md](runtime-internals.md) | The Rust runtime: crate/module map, key types, how a job runs, and the optional fork-server. |
| [configuration.md](configuration.md) | `tubulin.toml` and the environment variables. |
| [roadmap.md](roadmap.md) | Framework-improvement plan: the internal items distilled from building TPP, plus the tiered checklist for closing the Streamlit adoption gap. |
| [mvp.md](mvp.md) | The original MVP scope and milestone map (historical). |

The frontend's visual system (dependency-free CSS tokens, light + dark, a Plotly theme)
is its own package: [`design/README.md`](../design/README.md).

## A worked, real-world example

[`examples/tpp`](../examples/tpp) adapts a real Streamlit app
([tpp_solver](https://github.com/somethington/tpp_solver), Thermal Proteome
Profiling) to Tubulin — interactive Plotly charts, a paginated curve gallery of
hundreds of plots, a downloadable results bundle, and a parallel job that fans
hundreds of figures across the machine's cores. See
[examples/tpp/README.md](../examples/tpp/README.md).

## Status

This is a working prototype. It is **single-tenant**: it runs one user's app well on
one machine, but it lacks the fairness, isolation, and durability a multi-user server
needs. Those gaps are documented honestly at the end of
[architecture.md](architecture.md#limitations--what-this-is-not-yet) and
[parallelism.md](parallelism.md).
