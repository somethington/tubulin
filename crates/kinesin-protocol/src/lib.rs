//! Wire types shared across the Kinesin runtime.
//!
//! Three families of messages live here:
//!   * the browser <-> runtime protocol ([`ServerMessage`], [`ClientMessage`]);
//!   * the **app spec** the runtime loads once from the Python registration pass
//!     ([`AppSpec`] and friends);
//!   * the line protocol a Python worker speaks on stdout ([`WorkerMessage`]).
//!
//! The Python package (`python/kinesin/protocol.py`) and the frontend
//! (`frontend/src/types.ts`) mirror these shapes by hand. Keep all three in sync.

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

/// A single UI widget in the tree. `props` is an open bag interpreted per `kind`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Node {
    pub id: String,
    pub kind: String,
    #[serde(default)]
    pub props: Map<String, Value>,
}

// ---------------------------------------------------------------------------
// Browser <-> runtime protocol
// ---------------------------------------------------------------------------

/// Messages the runtime pushes to a browser session over the WebSocket.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ServerMessage {
    /// Full UI tree, sent once on connect.
    UiTree {
        session_id: String,
        title: String,
        nodes: Vec<Node>,
    },
    /// Merge `props` into the props of node `node_id`. This is how the runtime
    /// drives every live update (job status, log lines, result previews, text).
    WidgetUpdate {
        node_id: String,
        props: Map<String, Value>,
    },
    /// A non-fatal error to surface in the UI.
    Error { message: String },
}

/// Messages a browser session sends to the runtime.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ClientMessage {
    /// A user interaction on a node (e.g. a button click, or an upload finishing).
    Event {
        node_id: String,
        event: String,
        #[serde(default)]
        payload: Value,
    },
}

// ---------------------------------------------------------------------------
// App spec (produced by `python -m kinesin.register`)
// ---------------------------------------------------------------------------

/// The entire declarative application, loaded once at startup.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AppSpec {
    pub title: String,
    pub nodes: Vec<Node>,
    /// node_id -> action to run when that node fires its primary event.
    #[serde(default)]
    pub actions: std::collections::HashMap<String, ActionSpec>,
    /// job name -> where to find its Python function.
    #[serde(default)]
    pub jobs: std::collections::HashMap<String, JobDef>,
    /// node_id -> how that widget reflects a job slot.
    #[serde(default)]
    pub bindings: std::collections::HashMap<String, Binding>,
}

/// What a node's event does. The runtime executes these itself — no Python in
/// the request path.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ActionSpec {
    /// Enqueue `job`, binding the resulting runtime job id to `result_slot`.
    SubmitJob {
        job: String,
        #[serde(default)]
        args: Vec<ArgSpec>,
        result_slot: String,
    },
    /// Set node `target`'s `text` prop to `value`. Used for trivial wiring/demos.
    SetText { target: String, value: String },
}

/// One argument to a submitted job: either a literal or a reference into the
/// session's state (e.g. the path produced by a file-upload widget).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum ArgSpec {
    /// `state["{node}.{field}"]` resolved at submit time.
    Ref { node: String, field: String },
    /// A constant baked in at registration.
    Literal { value: Value },
}

/// Where a job's Python function lives.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JobDef {
    pub module_file: String,
    pub qualname: String,
    /// If true, the runtime may run the job as N parallel shards (it picks N) and
    /// hands each worker its `shard`/`num_shards`.
    #[serde(default)]
    pub shardable: bool,
}

/// Connects a widget to a job slot so the runtime knows what to update.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Binding {
    pub slot: String,
    /// "status" or "result_preview".
    pub view: String,
}

// ---------------------------------------------------------------------------
// Job lifecycle
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum JobStatus {
    Queued,
    Running,
    Completed,
    Failed,
    Cancelled,
}

impl JobStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            JobStatus::Queued => "queued",
            JobStatus::Running => "running",
            JobStatus::Completed => "completed",
            JobStatus::Failed => "failed",
            JobStatus::Cancelled => "cancelled",
        }
    }
}

// ---------------------------------------------------------------------------
// Worker line protocol (worker stdout -> runtime, one JSON object per line)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "event", rename_all = "snake_case")]
pub enum WorkerMessage {
    Log { message: String },
    Progress { progress: u8 },
    Completed { result: WorkerResult },
    Failed { error: String },
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WorkerResult {
    /// Path on disk where the job wrote its primary result, if any.
    #[serde(default)]
    pub path: Option<String>,
    /// Named secondary artifacts (e.g. plot SVGs, extra tables): name -> path.
    /// Consumed by widgets bound with a `plot:<name>` or `table:<name>` view.
    #[serde(default)]
    pub outputs: std::collections::HashMap<String, String>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn app_spec_matches_registration_shape() {
        // The exact JSON shape emitted by `python -m kinesin.register`.
        let json = r#"{
            "title": "CSV Analyzer",
            "nodes": [{"id": "button_1", "kind": "button", "props": {"label": "Run"}}],
            "actions": {"button_1": {"type": "submit_job", "job": "analyze",
                "args": [{"kind": "ref", "node": "file_upload_1", "field": "path"}],
                "result_slot": "slot_1"}},
            "jobs": {"analyze": {"module_file": "/abs/ui.py", "qualname": "analyze"}},
            "bindings": {"status_1": {"slot": "slot_1", "view": "status"}}
        }"#;
        let spec: AppSpec = serde_json::from_str(json).unwrap();
        assert_eq!(spec.title, "CSV Analyzer");
        match spec.actions.get("button_1").unwrap() {
            ActionSpec::SubmitJob {
                job,
                args,
                result_slot,
            } => {
                assert_eq!(job, "analyze");
                assert_eq!(result_slot, "slot_1");
                assert!(matches!(&args[0], ArgSpec::Ref { node, field }
                    if node == "file_upload_1" && field == "path"));
            }
            _ => panic!("expected submit_job"),
        }
    }

    #[test]
    fn worker_messages_parse() {
        let cases = [
            r#"{"event": "log", "message": "reading"}"#,
            r#"{"event": "progress", "progress": 50}"#,
            r#"{"event": "completed", "result": {"path": "storage/results/x.parquet"}}"#,
            r#"{"event": "failed", "error": "boom"}"#,
        ];
        for c in cases {
            serde_json::from_str::<WorkerMessage>(c)
                .unwrap_or_else(|e| panic!("failed to parse {c}: {e}"));
        }
    }

    #[test]
    fn server_message_tagging() {
        let msg = ServerMessage::WidgetUpdate {
            node_id: "n".into(),
            props: Map::new(),
        };
        let json = serde_json::to_string(&msg).unwrap();
        assert!(json.contains("\"type\":\"widget_update\""));
    }
}
