# Protocol & wire formats

Three message families connect the pieces. The canonical Rust types live in
[`crates/tubulin-protocol/src/`](../crates/tubulin-protocol/src) — `messages.rs`
(browser ↔ runtime), `spec.rs` (AppSpec), `worker.rs` (worker line protocol), all
re-exported from `lib.rs`; Python (`python/tubulin/protocol.py`) and the frontend
(`frontend/src/types.ts`) mirror them by hand.

---

## 1. Browser ↔ runtime (over `/ws`, JSON, tagged by `type`)

### Runtime → browser

```jsonc
// Sent once on connect.
{ "type": "ui_tree", "session_id": "…", "title": "My App", "nodes": [ Node, … ] }

// The universal live update: merge `props` into node `node_id`'s props.
{ "type": "widget_update", "node_id": "status_1", "props": { "status": "running" } }

// A non-fatal error to surface in the UI.
{ "type": "error", "message": "no value for file_upload_1.path …" }
```

`Node` = `{ "id": string, "kind": string, "props": object }`. Every live change —
status, progress, logs, table previews, plot SVGs, gallery items, download links — is
a `widget_update` that the frontend shallow-merges into the node's props.

### Browser → runtime

```jsonc
{ "type": "event", "node_id": "button_1", "event": "click",  "payload": {} }
{ "type": "event", "node_id": "file_upload_1", "event": "upload", "payload": {"path": "storage/uploads/x.csv"} }
{ "type": "event", "node_id": "checkbox_1", "event": "change", "payload": {"value": true} }
```

The handler reacts to three events: `upload` (store `state["<node>.path"]`), `change`
(store `state["<node>.value"]`), and anything with a mapped **action** (`click`).

---

## 2. The app spec (registration → runtime)

`python -m tubulin.register ui.py` prints one `AppSpec`:

```jsonc
{
  "title": "string",
  "nodes":   [ { "id", "kind", "props" }, … ],          // the UI tree
  "actions": { "<node_id>": ActionSpec },                // what a node's event does
  "jobs":    { "<name>": { "module_file", "qualname", "shardable" } },
  "bindings":{ "<node_id>": { "slot", "view" } }         // how a widget reflects a job
}
```

**ActionSpec** (tagged by `type`):

```jsonc
{ "type": "submit_job", "job": "analyze", "args": [ ArgSpec, … ], "result_slot": "slot_1" }
{ "type": "set_text",  "target": "text_1", "value": "Done" }
```

**ArgSpec** (tagged by `kind`) — resolved at submit time:

```jsonc
{ "kind": "ref",     "node": "file_upload_1", "field": "path" }   // ← state["file_upload_1.path"]
{ "kind": "literal", "value": 42 }
```

**Binding `view`s** — how an output widget consumes the job:

| view | filled with |
| --- | --- |
| `status` | lifecycle: status, progress, logs, elapsed |
| `result_preview` | the job's `result_path`, previewed (100 rows) |
| `plot:<name>` | `outputs[name]` read as an inline SVG |
| `plotly:<name>` | `outputs[name]` parsed as a Plotly figure JSON |
| `table:<name>` | `outputs[name]` previewed as a secondary table |
| `gallery:<name>` | `outputs[name]` is a **directory**, listed into gallery items |
| `download:<name>` | a `/bundle?job=<id>` link |

---

## 3. The worker line protocol (runtime ↔ Python worker)

### Descriptor (runtime → worker stdin, one JSON object)

```jsonc
{
  "job_id": "uuid",
  "module_file": "/abs/compute.py",   // file where the job fn is defined
  "qualname": "analyze",
  "args": [ "storage/uploads/x.csv", true, 0.8 ],   // resolved arg values
  "result_dir": "/abs/storage/results",
  "shard": 0, "num_shards": 1          // > 1 for a parallel shard
}
```

The worker imports `module_file` (with `TUBULIN_WORKER=1` so a UI module doesn't
launch anything), finds `qualname`, builds a `JobContext`, and calls the function.

### Updates (worker → runtime stdout, **one JSON object per line**, tagged by `event`)

```jsonc
{ "event": "log",      "message": "Reading CSV" }
{ "event": "progress", "progress": 42 }
{ "event": "completed","result": { "path": "…/stats.parquet", "outputs": {"volcano": "…/v.json"} } }
{ "event": "failed",   "error": "Traceback …" }
```

The worker captures the *real* stdout at import time, then redirects user `print`s to
stderr, so stray prints can't corrupt this channel. The runtime maps each line onto
widget updates and fans them to the owning session.

---

## 4. HTTP routes

| Method & path | Body / params | Returns |
| --- | --- | --- |
| `GET /` + assets | — | the built React frontend |
| `GET /ws` | WebSocket upgrade | the per-session protocol above |
| `POST /upload` | multipart file | `{ upload_id, path, filename }` |
| `GET /preview` | `?path=` | `{ columns, rows, total_rows, truncated }` (100 rows) |
| `GET /file` | `?path=` | the file **inline** (`image/svg+xml`, etc.) |
| `GET /download` | `?path=` | the file as an **attachment** |
| `GET /bundle` | `?job=<id>` | builds (once) and serves the job's results `.zip` |
| `GET /health` | — | `{ status, app, sessions }` |

`/preview`, `/file`, `/download` take a path; `/bundle` takes a job id and builds the
zip natively in Rust from `<result_dir>/<job_id>_bundle.json` (a manifest the job wrote
with `job.bundle(...)`). All reject paths outside the project root.
