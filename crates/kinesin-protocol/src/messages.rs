use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

/// A single UI widget in the tree; `props` is an open bag interpreted per `kind`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Node {
    pub id: String,
    pub kind: String,
    #[serde(default)]
    pub props: Map<String, Value>,
}

/// Messages the runtime pushes to a browser session.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ServerMessage {
    /// Full UI tree, sent once on connect.
    UiTree {
        session_id: String,
        title: String,
        nodes: Vec<Node>,
    },
    /// Shallow-merge `props` into node `node_id`; carries every live update.
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
    /// A user interaction on a node (click, value change, finished upload).
    Event {
        node_id: String,
        event: String,
        #[serde(default)]
        payload: Value,
    },
}
