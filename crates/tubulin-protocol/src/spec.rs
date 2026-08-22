use std::collections::HashMap;

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::messages::Node;

/// The entire declarative application, loaded once at startup.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AppSpec {
    pub title: String,
    pub nodes: Vec<Node>,
    /// node_id -> action fired by that node's primary event.
    #[serde(default)]
    pub actions: HashMap<String, ActionSpec>,
    /// job name -> where its Python function lives.
    #[serde(default)]
    pub jobs: HashMap<String, JobDef>,
    /// node_id -> how that widget reflects a job slot.
    #[serde(default)]
    pub bindings: HashMap<String, Binding>,
}

/// What a node's event does; executed by the runtime itself, no Python in the request path.
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
    /// Set node `target`'s `text` prop to `value`.
    SetText { target: String, value: String },
}

/// One job argument: a literal, or a reference into session state.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum ArgSpec {
    /// `state["{node}.{field}"]`, resolved at submit time.
    Ref { node: String, field: String },
    /// A constant baked in at registration.
    Literal { value: Value },
}

/// Where a job's Python function lives.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JobDef {
    pub module_file: String,
    pub qualname: String,
    /// Allows the runtime to fan the job out as N parallel shards.
    #[serde(default)]
    pub shardable: bool,
}

/// Connects a widget to a job slot: "status", "result_preview", "plot:<name>", etc.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Binding {
    pub slot: String,
    pub view: String,
}
